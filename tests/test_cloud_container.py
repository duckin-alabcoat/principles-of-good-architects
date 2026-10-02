"""WI-0484 — a Claude cloud container: root is allowed there and nowhere else, and
`start` writes nothing tracked there.

A cloud container always runs as root and is thrown away after the session, so WI-0468's
refusal made POGA unusable in one. The detector is two signals, not one, so a stray
variable cannot unlock root on a real machine: Claude Code's own remote marker
(`CLAUDE_CODE_REMOTE=true`), and Linux with no launchd. It is written twice, once in
bash (`poga_cloud_container`) and once in Python (`session._cloud_container`), and
`DetectorTableTest` holds both to the same table so they cannot drift.

The proof as real root is in a container, not here: this suite refuses to run as root.
"""
from __future__ import annotations

import json
import os
import pathlib
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

POGA = ROOT / "poga"
REFUSAL = "refusing to run as root"

# (CLAUDE_CODE_REMOTE, kernel, launchctl on PATH) -> is this a cloud container?
TABLE = [
    ("true", "Linux", False, True),
    ("true", "Linux", True, False),    # Linux with launchd: not the cloud
    ("true", "Darwin", True, False),   # a Mac with a stray marker stays refused
    ("true", "Darwin", False, False),
    ("", "Linux", False, False),       # no marker: an ordinary Linux box
    ("1", "Linux", False, False),      # only the literal "true" the binary tests counts
    ("TRUE", "Linux", False, False),
    (None, "Linux", False, False),
]


def _stub(dirpath: pathlib.Path, name: str, body: str) -> None:
    p = dirpath / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def _bash_env(bindir: pathlib.Path, remote, kernel: str, launchctl: bool, uid: int) -> dict:
    """A PATH holding ONLY stubs, so `launchctl` is present exactly when the row says so
    (a Mac carries /bin/launchctl, which a PATH reaching /bin would always find)."""
    for f in bindir.iterdir():
        f.unlink()
    _stub(bindir, "id", f'[ "$1" = "-u" ] && echo {uid} || exec /usr/bin/id "$@"')
    _stub(bindir, "uname", f"echo {kernel}")
    if launchctl:
        _stub(bindir, "launchctl", "exit 0")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
    env["PATH"] = str(bindir)
    if remote is not None:
        env["CLAUDE_CODE_REMOTE"] = remote
    return env


def _bash_detector(bindir: pathlib.Path, remote, kernel: str, launchctl: bool) -> bool:
    """Run the wrapper's own `poga_cloud_container`, lifted out of `poga` by name."""
    src = POGA.read_text(encoding="utf-8")
    start = src.index("poga_cloud_container() {")
    body = src[start:src.index("\n}\n", start) + 3]
    env = _bash_env(bindir, remote, kernel, launchctl, uid=1000)
    r = subprocess.run([shutil.which("bash") or "/bin/bash", "-c",
                        body + "\npoga_cloud_container"],
                       env=env, capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def _py_detector(remote, kernel: str, launchctl: bool) -> bool:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
    if remote is not None:
        env["CLAUDE_CODE_REMOTE"] = remote
    platform = {"Linux": "linux", "Darwin": "darwin"}[kernel]
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(sys, "platform", platform), \
            mock.patch("shutil.which",
                       lambda name, *a, **k: "/bin/launchctl"
                       if name == "launchctl" and launchctl else None):
        return session._cloud_container()


@unittest.skipUnless(shutil.which("bash"), "bash required")
class DetectorTableTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = pathlib.Path(tmp.name)

    def test_bash_and_python_agree_with_the_table(self):
        for remote, kernel, launchctl, want in TABLE:
            row = (remote, kernel, launchctl)
            with self.subTest(row=row):
                self.assertEqual(_bash_detector(self.bin, *row), want, f"poga: {row}")
                self.assertEqual(_py_detector(*row), want, f"session.py: {row}")


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PogaAsRootTest(unittest.TestCase):
    """The wrapper as uid 0 (a stubbed `id`), across the table."""

    def setUp(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("the suite must not run as root")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = pathlib.Path(tmp.name)

    def _poga(self, remote, kernel, launchctl):
        env = _bash_env(self.bin, remote, kernel, launchctl, uid=0)
        return subprocess.run([shutil.which("bash"), str(POGA), "lanes"], env=env,
                              cwd=str(self.bin), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_root_passes_the_refusal_only_in_a_cloud_container(self):
        for remote, kernel, launchctl, cloud in TABLE:
            with self.subTest(row=(remote, kernel, launchctl)):
                r = self._poga(remote, kernel, launchctl)
                if cloud:
                    self.assertNotIn(REFUSAL, r.stderr)
                else:
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn(REFUSAL, r.stderr)


class SessionPyAsRootTest(unittest.TestCase):
    """session.py is every PreToolUse hook, so this is what decides whether a cloud
    session can run a single Bash command."""

    def _run(self, remote, platform, launchctl):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
        if remote is not None:
            env["CLAUDE_CODE_REMOTE"] = remote
        which = "'/bin/launchctl'" if launchctl else "None"
        code = ("import os, runpy, shutil, sys; os.geteuid = lambda: 0; "
                f"sys.platform = {platform!r}; "
                f"shutil.which = lambda n, *a, **k: {which} if n == 'launchctl' else None; "
                f"sys.argv = [{str(ROOT / 'session.py')!r}, 'interpreter']; "
                f"runpy.run_path({str(ROOT / 'session.py')!r}, run_name='__main__')")
        return subprocess.run([sys.executable, "-B", "-c", code], env=env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def test_root_in_a_cloud_container_passes_the_refusal(self):
        """Only the refusal is asserted. A faked `sys.platform` on a Mac breaks imports
        further on, so the run's own exit code says nothing here; the real root run is
        the container proof (comms/2026-10-02-cloud-lanes-unblocked.md). Exit 2 is the
        refusal's code, so it must not be that."""
        r = self._run("true", "linux", False)
        self.assertNotIn(REFUSAL, r.stderr)
        self.assertNotEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_root_without_the_marker_is_still_refused(self):
        r = self._run(None, "linux", False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(REFUSAL, r.stderr)

    def test_root_on_a_mac_with_the_marker_is_still_refused(self):
        r = self._run("true", "darwin", True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(REFUSAL, r.stderr)


class CloudStartWritesNothingTrackedTest(unittest.TestCase):
    """The first heartbeat is where a lazy start writes its journal, handoff, STATUS and
    ROADMAP, pushes, and sweeps the main checkout. In a cloud container none of it runs,
    and the marker is still consumed so later beats do not keep re-entering."""

    STEPS = ["_migrate_freeze", "_reap_dead_journals", "_auto_reap_lanes",
             "_link_shared_data", "_coord_reap", "write_start_journal", "_push_if_ahead",
             "_materialize_main_auto", "_main_harness_sweep_auto", "_cut_session_branch",
             "run_compile", "_mark_substrate_push_announced"]

    def _beat(self, cloud: bool):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        marker = pathlib.Path(tmp.name) / "sid.pending-start"
        marker.write_text(json.dumps({"session_id": "20261002T0000Z-test-0000",
                                      "ordinal": 1, "machine": "T"}), encoding="utf-8")
        calls = []
        patches = [mock.patch.object(session, name,
                                     lambda *a, _n=name, **k: calls.append(_n))
                   for name in self.STEPS]
        patches += [
            mock.patch.object(session, "_cloud_start_quiet", lambda: cloud),
            mock.patch.object(session, "_pending_marker", lambda sid: marker),
            mock.patch.object(session, "_sweep_stale_pending", lambda **k: None),
            mock.patch.object(session, "_startup_timing_log", lambda *a, **k: None),
            mock.patch.object(session, "journal_path",
                              lambda did: pathlib.Path(tmp.name) / "no-such-journal.md"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        session._complete_lazy_start("sid")
        return calls, marker

    def test_in_a_cloud_container_no_step_runs_and_the_marker_is_consumed(self):
        calls, marker = self._beat(cloud=True)
        self.assertEqual(calls, [])
        self.assertFalse(marker.exists())

    def test_elsewhere_every_step_still_runs(self):
        """The control: without it, a stub that never fired would pass the cloud test."""
        calls, marker = self._beat(cloud=False)
        self.assertEqual(sorted(calls), sorted(self.STEPS))
        self.assertFalse(marker.exists())


class CloudStartQuietTest(unittest.TestCase):
    """The quiet start is the cloud detector, except while a test framework drives the
    process: a suite run inside the container is still testing what start writes."""

    def test_a_real_cloud_process_is_quiet(self):
        code = ("import sys; sys.path.insert(0, %r); import session; "
                "session._cloud_container = lambda: True; "
                "assert 'unittest' not in sys.modules; "
                "print(session._cloud_start_quiet())" % str(ROOT))
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.stdout.strip(), "True", r.stderr)

    def test_under_a_test_framework_it_is_not(self):
        with mock.patch.object(session, "_cloud_container", lambda: True):
            self.assertFalse(session._cloud_start_quiet())

    def test_off_the_cloud_it_is_not(self):
        code = ("import sys; sys.path.insert(0, %r); import session; "
                "session._cloud_container = lambda: False; "
                "print(session._cloud_start_quiet())" % str(ROOT))
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.stdout.strip(), "False", r.stderr)


class CloudApplyBriefsTest(unittest.TestCase):
    """`apply-briefs` is a SessionStart hook that writes tracked target files."""

    def _mode(self, cloud: bool, **flags) -> str:
        seen = []
        args = mock.Mock(status=flags.get("status", False),
                         dry_run=flags.get("dry_run", False))
        with mock.patch.object(session, "_cloud_start_quiet", lambda: cloud), \
                mock.patch.object(session, "_apply_run", seen.append):
            session.cmd_apply_briefs(args)
        return seen[0]

    def test_a_cloud_container_applies_nothing(self):
        self.assertEqual(self._mode(True), "dry-run")
        self.assertEqual(self._mode(True, status=True), "status")

    def test_elsewhere_apply_is_unchanged(self):
        self.assertEqual(self._mode(False), "apply")


if __name__ == "__main__":
    unittest.main()
