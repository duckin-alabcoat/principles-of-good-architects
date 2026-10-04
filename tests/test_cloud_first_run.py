"""WI-0494 — a Claude cloud container's first run: the gate skips it, and poga says so
instead of opening a lane that hangs at a login picker.

THE DEFECT. In a cloud container the walkthrough's one-time `claude` first run showed
Claude Code's login picker, although sign-in there is host-managed and `claude -p` works.
Esc did not skip it, so the walkthrough stopped at step 3. The launch gate would have
done the same: onboarding reads FAIL there, and the guided repair opens that same picker.

THE RULE. In a cloud container (WI-0484's two-signal test) with auth OK, the gate applies
the WI-0463 narrow skip by itself and says so; the full report marks the two rows SKIPPED,
not applicable. Auth not OK: unchanged. Outside a cloud container: unchanged, and nothing
extra is asked. Claude Code has no supported way to skip the interactive picker, so an
interactive Claude launch in a cloud container whose first run is unfinished prints one
line naming `poga -p` and stops; `-p` launches go through.
"""
from __future__ import annotations

import contextlib
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from test_cloud_container import TABLE, _bash_env  # noqa: E402

POGA = ROOT / "poga"
ENV = "POGA_SKIP_PREFLIGHT_CHECKS"
OK, FAIL, UNKNOWN = session.PREFLIGHT_OK, session.PREFLIGHT_FAIL, session.PREFLIGHT_UNKNOWN


def _check(name, verdict, calls, detail=None):
    def fn():
        calls.append(name)
        return session._PreflightResult(name, verdict, detail or f"{name} detail",
                                        f"{name} remedy")
    fn.__name__ = f"_fake_{name}"
    return fn


class CloudGateTest(unittest.TestCase):
    """The launch gate with fake checks, the cloud signal patched either way."""

    def gate(self, cloud, auth=OK, onboarding=FAIL, trust=UNKNOWN, env_value=None):
        calls = []
        term = _check("terminfo", OK, calls)
        tr = _check("workspace-trust", trust, calls)
        onb = _check("onboarding", onboarding, calls)
        mem = _check("memory-pressure", OK, calls)
        au = _check("auth", auth, calls, detail="logged in (host-managed)")
        env = {k: v for k, v in os.environ.items()
               if k not in (ENV, session.PREFLIGHT_BYPASS_ENV)}
        if env_value is not None:
            env[ENV] = env_value
        env[session.PREFLIGHT_NO_REPAIR_ENV] = "1"
        err = io.StringIO()
        code = 0
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(session, "_cloud_first_run_managed", return_value=cloud), \
             mock.patch.object(session, "PREFLIGHT_GATE_CHECKS", (term, tr, onb, mem)), \
             mock.patch.object(session, "_pf_onboarding", onb), \
             mock.patch.object(session, "_pf_workspace_trust", tr), \
             mock.patch.object(session, "_pf_auth", au), \
             mock.patch.object(session, "detect_machine", return_value="TestBox"), \
             contextlib.redirect_stderr(err):
            try:
                session._preflight_gate()
            except SystemExit as e:
                code = int(e.code)
        return code, calls, err.getvalue()

    def test_cloud_with_live_auth_skips_both_and_says_so(self):
        code, calls, err = self.gate(cloud=True)
        self.assertEqual(code, 0, err)
        self.assertNotIn("onboarding", calls)
        self.assertNotIn("workspace-trust", calls)
        self.assertIn("terminfo", calls)
        self.assertIn("memory-pressure", calls)
        self.assertIn("cloud container: onboarding, workspace-trust NOT checked", err)
        self.assertIn("logged in (host-managed)", err, "the banner names what stood in")

    def test_cloud_without_live_auth_is_unchanged(self):
        for verdict in (FAIL, UNKNOWN):
            with self.subTest(auth=verdict):
                code, calls, err = self.gate(cloud=True, auth=verdict)
                self.assertEqual(code, 1)
                self.assertIn("onboarding", calls)
                self.assertIn("workspace-trust", calls)
                self.assertIn("REFUSING", err)
                self.assertNotIn("NOT checked", err)

    def test_outside_a_cloud_container_nothing_changes(self):
        code, calls, err = self.gate(cloud=False)
        self.assertEqual(code, 1)
        self.assertIn("onboarding", calls)
        self.assertNotIn("auth", calls, "the ordinary launch must not pay for auth")
        self.assertNotIn("cloud container", err)

    def test_outside_a_cloud_container_a_finished_first_run_passes_silently(self):
        code, calls, err = self.gate(cloud=False, onboarding=OK, trust=OK)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("auth", calls)

    def test_auth_is_asked_once_and_the_note_printed_once(self):
        """The gate filtered its checks with the skip call inside a generator, so auth
        ran, and the NOTE printed, once per gate check. Found by the container proof."""
        for cloud, env_value in ((True, None), (False, "onboarding,workspace-trust")):
            with self.subTest(cloud=cloud, env=env_value):
                code, calls, err = self.gate(cloud=cloud, env_value=env_value)
                self.assertEqual(code, 0, err)
                self.assertEqual(calls.count("auth"), 1)
                self.assertEqual(err.count("NOT checked"), 1, err)

    def test_the_explicit_variable_still_rules_in_a_cloud_container(self):
        """WI-0463's own path is untouched: naming one check skips only that one."""
        code, calls, err = self.gate(cloud=True, env_value="onboarding", trust=OK)
        self.assertEqual(code, 0)
        self.assertNotIn("onboarding", calls)
        self.assertIn("workspace-trust", calls)
        self.assertIn(ENV, err)


class ManagedFollowsTheDetectorTest(unittest.TestCase):
    """`_cloud_first_run_managed` is WI-0484's detector, row for row, outside a test run:
    marker absent, macOS, or launchd present all read as an ordinary machine."""

    def test_the_table(self):
        for remote, kernel, launchctl, want in TABLE:
            with self.subTest(row=(remote, kernel, launchctl)):
                env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
                if remote is not None:
                    env["CLAUDE_CODE_REMOTE"] = remote
                platform = {"Linux": "linux", "Darwin": "darwin"}[kernel]
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(sys, "platform", platform), \
                        mock.patch.object(session, "_under_test", return_value=False), \
                        mock.patch("shutil.which",
                                   lambda name, *a, **k: "/bin/launchctl"
                                   if name == "launchctl" and launchctl else None):
                    self.assertEqual(session._cloud_first_run_managed(), want)

    def test_off_under_a_test_framework(self):
        """A suite run inside the container still tests the ordinary checks."""
        with mock.patch.object(session, "_cloud_container", return_value=True):
            self.assertFalse(session._cloud_first_run_managed())


class FullReportRowsTest(unittest.TestCase):

    def rows(self, cloud, auth):
        results = [session._PreflightResult("terminfo", OK, "t"),
                   session._PreflightResult("workspace-trust", UNKNOWN, "no file", "r"),
                   session._PreflightResult("onboarding", FAIL, "not done", "r"),
                   session._PreflightResult("auth", auth, "logged in")]
        with mock.patch.object(session, "_cloud_first_run_managed", return_value=cloud):
            return results, session._preflight_cloud_rows(results)

    def test_cloud_with_live_auth_reads_skipped_not_ok(self):
        _, out = self.rows(True, OK)
        by = {r.name: r for r in out}
        for name in ("onboarding", "workspace-trust"):
            self.assertEqual(by[name].verdict, session.PREFLIGHT_SKIPPED)
            self.assertFalse(by[name].applicable)
            self.assertIn("cloud container", by[name].detail)
        self.assertEqual(by["terminfo"].verdict, OK)

    def test_cloud_without_live_auth_and_non_cloud_are_unchanged(self):
        for cloud, auth in ((True, FAIL), (True, UNKNOWN), (False, OK)):
            with self.subTest(cloud=cloud, auth=auth):
                before, after = self.rows(cloud, auth)
                self.assertEqual(after, before)

    def test_the_report_uses_it(self):
        body = (ROOT / "sessionlib" / "hooks.py").read_text(encoding="utf-8")
        body = body.split("def cmd_preflight(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_preflight_cloud_rows(", body)


class CloudFirstRunStopTest(unittest.TestCase):
    """`preflight --cloud-first-run`: exit 3 and one line, or exit 0 and silence."""

    def stop(self, cloud, onboarding=FAIL, trust=OK):
        calls, err, code = [], io.StringIO(), None
        with mock.patch.object(session, "_cloud_first_run_managed", return_value=cloud), \
             mock.patch.object(session, "_pf_onboarding", _check("onboarding", onboarding, calls)), \
             mock.patch.object(session, "_pf_workspace_trust", _check("workspace-trust", trust, calls)), \
             contextlib.redirect_stderr(err):
            try:
                session._cloud_first_run_stop()
            except SystemExit as e:
                code = int(e.code)
        return code, calls, err.getvalue()

    def test_unfinished_first_run_in_a_cloud_container_prints_one_line(self):
        for onb, tr in ((FAIL, OK), (OK, FAIL), (OK, UNKNOWN)):
            with self.subTest(onboarding=onb, trust=tr):
                code, _, err = self.stop(True, onb, tr)
                self.assertEqual(code, session.PREFLIGHT_CLOUD_STOP_EXIT)
                self.assertEqual(len(err.strip().splitlines()), 1, err)
                self.assertIn("cloud container", err)
                self.assertIn("claude -p", err)
                self.assertIn("poga -p", err)

    def test_finished_first_run_launches_silently(self):
        code, _, err = self.stop(True, OK, OK)
        self.assertEqual((code, err), (0, ""))

    def test_outside_a_cloud_container_it_reads_nothing(self):
        code, calls, err = self.stop(False)
        self.assertEqual((code, calls, err), (0, [], ""))

    def test_the_stop_code_is_neither_a_traceback_nor_argparse(self):
        self.assertNotIn(session.PREFLIGHT_CLOUD_STOP_EXIT, (0, 1, 2))

    def test_the_flag_is_wired(self):
        args = session._cli_parser().parse_args(["preflight", "--cloud-first-run"])
        self.assertTrue(args.cloud_first_run)
        self.assertIs(args.func, session.cmd_preflight)


def _extract_function(name: str) -> str:
    src = POGA.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", src, re.M | re.S)
    assert m, f"function {name} not found in poga"
    return m.group(0)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PogaCloudStopBehaviourTest(unittest.TestCase):
    """The wrapper's function EXECUTED under poga's own `set -euo pipefail`, against a stub
    session.py that records its argv and answers the exit code we choose."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name) / "repo"
        self.bin = pathlib.Path(tmp.name) / "bin"
        self.root.mkdir()
        self.bin.mkdir()
        self.log = pathlib.Path(tmp.name) / "called"
        self.fn = (_extract_function("poga_cloud_container") + "\n"
                   + _extract_function("poga_cloud_first_run_stop"))

    def run_fn(self, code, remote="true", kernel="Linux", launchctl=False,
               args=(), runtime="claude-code"):
        (self.root / "session.py").write_text(
            "import sys\n"
            f"open({str(self.log)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
            "sys.stderr.write('STUB LINE\\n')\n"
            f"sys.exit({code})\n", encoding="utf-8")
        if self.log.exists():
            self.log.unlink()
        env = _bash_env(self.bin, remote, kernel, launchctl, uid=1000)
        env["PY"] = sys.executable
        quoted = " ".join(f"'{a}'" for a in args)
        script = (f'set -euo pipefail\nROOT="{self.root}"\nPOGA_RT_ID="{runtime}"\n'
                  f'{self.fn}\npoga_cloud_first_run_stop {quoted}\necho RETURNED\n')
        r = subprocess.run([shutil.which("bash"), "-c", script], env=env,
                           capture_output=True, text=True, timeout=60)
        called = self.log.read_text() if self.log.exists() else None
        return r, called

    def test_stop_code_stops_the_launch_with_the_line(self):
        r, called = self.run_fn(3)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("RETURNED", r.stdout)
        self.assertIn("STUB LINE", r.stderr)
        self.assertEqual(called, "preflight --cloud-first-run")

    def test_a_finished_first_run_launches(self):
        r, called = self.run_fn(0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RETURNED", r.stdout)
        self.assertIsNotNone(called)

    def test_other_codes_fail_open(self):
        """1 may be a traceback and 2 is argparse on a member without the flag."""
        for code in (1, 2):
            with self.subTest(code=code):
                r, _ = self.run_fn(code)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("RETURNED", r.stdout)

    def test_a_headless_launch_goes_through_unasked(self):
        for flag in ("-p", "--print"):
            with self.subTest(flag=flag):
                r, called = self.run_fn(3, args=("--model", "x", flag, "do it"))
                self.assertIn("RETURNED", r.stdout)
                self.assertIsNone(called)

    def test_another_runtime_is_not_asked(self):
        r, called = self.run_fn(3, runtime="codex")
        self.assertIn("RETURNED", r.stdout)
        self.assertIsNone(called)

    def test_outside_a_cloud_container_nothing_runs(self):
        for remote, kernel, launchctl, cloud in TABLE:
            if cloud:
                continue
            with self.subTest(row=(remote, kernel, launchctl)):
                r, called = self.run_fn(3, remote, kernel, launchctl)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("RETURNED", r.stdout)
                self.assertIsNone(called, "an ordinary machine must not even ask")


class PogaWiringTest(unittest.TestCase):

    def setUp(self):
        self.src = POGA.read_text(encoding="utf-8")

    def test_launch_stops_after_the_gate_and_before_a_lane_exists(self):
        body = self.src.split("cmd_session() {", 1)[1].split("\n}\n", 1)[0]
        gate = body.index("poga_preflight_gate")
        stop = body.index("poga_cloud_first_run_stop")
        alloc = body.index("lane=\"$(alloc_lane)\"")
        self.assertLess(gate, stop)
        self.assertLess(stop, alloc)

    def test_resume_stops_too(self):
        body = self.src.split("cmd_resume() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("poga_cloud_first_run_stop", body)


if __name__ == "__main__":
    unittest.main()
