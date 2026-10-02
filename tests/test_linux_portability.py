"""WI-0468 — the POGA core runs on Linux.

the operator's ruling: POGA claims Linux for the core (install, init, lanes, work items, the land
gate, handoffs, checks); launchd / plutil / sandbox-exec features stay macOS-only. Each
place the core used to assume macOS is pinned here, and each test takes the Linux branch
on ANY machine (by patching `sys.platform`, or a PATH of stubs) so a macOS run still
proves it. The few tests that need the real kernel are `skipUnless(linux)` and run in the
Linux container; on macOS they report skipped, which is the honest reading.

  1. memory-pressure reads /proc/meminfo into the same sample the pure verdict judges.
  2. runtime-quarantine is one not-applicable SKIPPED row off macOS, never a FAIL.
  3. no st_birthtime means None — never a silent st_mtime (the reaper's unsafe side).
  4. machine naming falls back to `hostname` where `scutil` is absent.
  5. `launchctl` absent degrades to the tmux surface.
  6. `poga init` on Linux pins the first python3 >= 3.9 on PATH, or refuses.
  7. `poga` and `session.py` refuse to run as root.

stdlib unittest: python3 -m unittest tests.test_linux_portability
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import bootstrap  # noqa: E402
import interpreter  # noqa: E402
import session  # noqa: E402
import harness_fixture  # noqa: E402,F401  (the suite's shared fixture contract)

POGA = ROOT / "poga"
GB = 1024 ** 3
ON_LINUX = sys.platform.startswith("linux")

MEMINFO = """MemTotal:       16384000 kB
MemFree:         1024000 kB
MemAvailable:    8192000 kB
SwapCached:        12000 kB
SwapTotal:       4096000 kB
SwapFree:        1024000 kB
HugePages_Total:       0
Hugepagesize:       2048 kB
"""


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _stub(dirpath: pathlib.Path, name: str, body: str) -> pathlib.Path:
    p = dirpath / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)
    return p


class _NoGate(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {})
        env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(env.stop)


# ---------------------------------------------------------------------------
# 1. memory pressure
# ---------------------------------------------------------------------------


class MeminfoParseTest(unittest.TestCase):
    def _parse(self, text):
        sample = {"phys_bytes": None, "swap_used_bytes": None, "procs": [],
                  "unreadable": []}
        session._meminfo_into(sample, text)
        return sample

    def test_kilobytes_are_kibibytes_and_swap_is_total_minus_free(self):
        s = self._parse(MEMINFO)
        self.assertEqual(s["phys_bytes"], 16384000 * 1024)
        self.assertEqual(s["swap_used_bytes"], (4096000 - 1024000) * 1024)
        self.assertEqual(s["unreadable"], [])

    def test_a_box_with_no_swap_reads_zero_in_use_not_unknown(self):
        s = self._parse("MemTotal: 2048000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
        self.assertEqual(s["swap_used_bytes"], 0)
        self.assertEqual(s["unreadable"], [])

    def test_missing_fields_are_named_never_defaulted(self):
        s = self._parse("MemFree: 10 kB\n")
        self.assertIsNone(s["phys_bytes"])
        self.assertIsNone(s["swap_used_bytes"])
        self.assertEqual(len(s["unreadable"]), 2)


class LinuxMemorySampleTest(_NoGate):
    PS = " 12582912     0   261 exampled\n   722784  1000  5626 claude\n"

    def _sample(self, platform, meminfo_path):
        def fake(argv, **kw):
            if argv[0] == "sysctl":
                raise AssertionError("asked sysctl off macOS")
            return _completed(self.PS)
        with mock.patch.object(sys, "platform", platform), \
             mock.patch.object(session, "_PROC_MEMINFO", str(meminfo_path)), \
             mock.patch.object(session.subprocess, "run", side_effect=fake):
            return session._memory_sample()

    def test_linux_reads_proc_meminfo_into_the_same_shape(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "meminfo"
            p.write_text(MEMINFO, encoding="utf-8")
            s = self._sample("linux", p)
        self.assertEqual(s["phys_bytes"], 16384000 * 1024)
        self.assertEqual(s["swap_used_bytes"], 3072000 * 1024)
        self.assertEqual(s["counters"], "/proc/meminfo")
        self.assertEqual([x["comm"] for x in s["procs"]], ["exampled", "claude"])
        # ...and the unchanged pure verdict judges it.
        r = session._memory_pressure_verdict(s, uid=1000, runtimes=frozenset({"claude"}))
        self.assertIn(r.verdict, (session.PREFLIGHT_OK, session.PREFLIGHT_FAIL))

    def test_an_unreadable_meminfo_is_unknown_with_a_linux_hand_read(self):
        s = self._sample("linux", "/nonexistent/meminfo")
        self.assertTrue(any("/proc/meminfo" in u for u in s["unreadable"]))
        r = session._memory_pressure_verdict(s, uid=1000)
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)
        self.assertIn("/proc/meminfo", r.remedy)
        self.assertNotIn("sysctl", r.remedy)

    def test_an_unknown_kernel_says_so_and_reads_unknown(self):
        s = self._sample("freebsd14", "/nonexistent/meminfo")
        self.assertTrue(any("freebsd14" in u for u in s["unreadable"]))
        self.assertEqual(session._memory_pressure_verdict(s, uid=1000).verdict,
                         session.PREFLIGHT_UNKNOWN)

    def test_a_linux_thrash_names_systemd_not_launchd(self):
        sample = {"phys_bytes": 16 * GB, "swap_used_bytes": 15 * GB, "counters": "/proc/meminfo",
                  "procs": [{"rss_bytes": 12 * GB, "uid": 0, "pid": 261, "comm": "exampled"}],
                  "unreadable": []}
        r = session._memory_pressure_verdict(sample, uid=1000)
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("systemd", r.remedy)
        self.assertNotIn("launchd", r.remedy)

    @unittest.skipUnless(ON_LINUX, "needs a real Linux /proc")
    def test_the_real_linux_box_is_read(self):
        s = session._memory_sample()
        self.assertGreater(s["phys_bytes"] or 0, 0, s["unreadable"])
        self.assertIsNotNone(s["swap_used_bytes"], s["unreadable"])
        self.assertTrue(s["procs"], s["unreadable"])
        self.assertIn(session._pf_memory_pressure().verdict,
                      (session.PREFLIGHT_OK, session.PREFLIGHT_FAIL))


# ---------------------------------------------------------------------------
# 2. quarantine
# ---------------------------------------------------------------------------


class QuarantineOffMacTest(unittest.TestCase):
    def test_off_macos_one_not_applicable_skipped_row_and_no_xattr(self):
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch.object(session.shutil, "which",
                               side_effect=AssertionError("looked for xattr")), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=AssertionError("ran xattr")):
            rows = session._pf_runtime_quarantine()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "runtime-quarantine")
        self.assertEqual(rows[0].verdict, session.PREFLIGHT_SKIPPED)
        self.assertFalse(rows[0].applicable)
        self.assertIn("macOS", rows[0].detail)
        self.assertNotIn("unavailable", rows[0].detail)

    def test_on_macos_rows_stay_applicable(self):
        with mock.patch.object(sys, "platform", "darwin"), \
             mock.patch.object(session.shutil, "which", return_value=None):
            rows = session._pf_runtime_quarantine()
        self.assertTrue(rows and all(r.applicable for r in rows))


# ---------------------------------------------------------------------------
# 3. birth time
# ---------------------------------------------------------------------------


class BirthTimeTest(unittest.TestCase):
    def test_no_birthtime_is_none_never_mtime(self):
        """The reaper's predicate is `process_start < birth - grace`; a fresh mtime as
        "birth" makes it EASIER to satisfy, which kills a live session. None instead."""
        fake = types.SimpleNamespace(st_mtime=1_700_000_000.0, st_ctime=1_700_000_000.0)
        with mock.patch.object(pathlib.Path, "stat", return_value=fake):
            self.assertIsNone(session._fs_birthtime(pathlib.Path("/anything")))

    @unittest.skipIf(hasattr(os.stat_result, "st_birthtime"),
                     "this platform's Python exposes st_birthtime")
    def test_on_a_platform_without_birthtime_the_real_stat_gives_none(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertIsNone(session._fs_birthtime(pathlib.Path(f.name)))


# ---------------------------------------------------------------------------
# 4 + 5. machine name, launchctl
# ---------------------------------------------------------------------------


def _load_standard_version():
    sys.path.insert(0, str(ROOT / "curate"))
    spec = importlib.util.spec_from_file_location(
        "standard_version_wi0468", ROOT / "curate" / "standard_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MachineNameTest(unittest.TestCase):
    def _run(self, responses, machine_map):
        sv = _load_standard_version()

        def fake(argv, **kw):
            r = responses.get(argv[0])
            if r is None:
                raise FileNotFoundError(argv[0])
            return _completed(r)
        with mock.patch.object(sv, "_load_cfg", return_value={"machine_map": machine_map}), \
             mock.patch("subprocess.run", side_effect=fake):
            return sv._this_machine()

    def test_no_scutil_falls_back_to_the_hostname(self):
        self.assertEqual(self._run({"hostname": "devbox.lan\n"}, {"devbox.lan": "DevBox"}),
                         "DevBox")

    def test_the_short_hostname_also_resolves(self):
        self.assertEqual(self._run({"hostname": "devbox.lan\n"}, {"devbox": "DevBox"}),
                         "DevBox")

    def test_macos_computername_still_wins(self):
        self.assertEqual(self._run({"scutil": "Runner\n", "hostname": "x\n"},
                                   {"Runner": "Runner", "x": "Other"}), "Runner")

    def test_an_unmapped_host_is_still_unknown_not_guessed(self):
        self.assertEqual(self._run({"hostname": "devbox\n"}, {}), "UNKNOWN-MACHINE")


class LaunchctlAbsentTest(unittest.TestCase):
    def test_no_launchctl_means_no_apple_events_so_tmux(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=FileNotFoundError("launchctl")):
            self.assertEqual(session._dispatch_session_manager(), "")
            self.assertFalse(session._dispatch_apple_events_available())


# ---------------------------------------------------------------------------
# 6. interpreter pin
# ---------------------------------------------------------------------------


class FirstOnPathTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.old, self.new, self.newer = (self.root / n for n in ("old", "new", "newer"))
        for d, ver in ((self.old, "3 8"), (self.new, "3 9"), (self.newer, "3 12")):
            d.mkdir()
            _stub(d, "python3", f"echo '{ver}'")

    def _path(self, *dirs):
        return os.pathsep.join(str(d) for d in dirs)

    def test_path_order_wins_over_newest_and_old_ones_are_passed_over(self):
        got, rejected = interpreter.first_on_path(self._path(self.old, self.new, self.newer))
        self.assertEqual(got, str(self.new / "python3"))
        self.assertEqual(rejected, [f"{self.old / 'python3'} (3.8)"])

    def test_the_answer_is_absolute_even_from_a_relative_path_entry(self):
        rel = os.path.relpath(self.newer, os.getcwd())
        got, _ = interpreter.first_on_path(rel)
        self.assertTrue(os.path.isabs(got), got)
        self.assertEqual(got, str(self.newer / "python3"))

    def test_none_new_enough_returns_empty_and_what_it_saw(self):
        got, rejected = interpreter.first_on_path(self._path(self.old))
        self.assertEqual(got, "")
        self.assertEqual(len(rejected), 1)

    def test_a_python_that_will_not_say_its_version_is_skipped(self):
        broken = self.root / "broken"
        broken.mkdir()
        _stub(broken, "python3", "exit 1")
        got, rejected = interpreter.first_on_path(self._path(broken, self.newer))
        self.assertEqual(got, str(self.newer / "python3"))
        self.assertIn("would not report", rejected[0])


class InstallInterpreterTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.old = self.root / "old"
        self.old.mkdir()
        _stub(self.old, "python3", "echo '3 8'")
        self.good = self.root / "good"
        self.good.mkdir()
        _stub(self.good, "python3", "echo '3 11'")

    def _find(self, linux, path):
        with mock.patch.object(bootstrap, "pins_interpreter", return_value=linux), \
             mock.patch.dict(os.environ, {"PATH": path}):
            return bootstrap.find_install_interpreter()

    def test_macos_pins_nothing(self):
        self.assertEqual(self._find(False, str(self.good)), ("", ""))

    def test_linux_pins_the_first_new_enough_python3(self):
        path = os.pathsep.join([str(self.old), str(self.good)])
        self.assertEqual(self._find(True, path), (str(self.good / "python3"), ""))

    def test_linux_with_none_new_enough_refuses_and_names_what_it_found(self):
        got, refusal = self._find(True, str(self.old))
        self.assertEqual(got, "")
        self.assertIn("3.9 or newer", refusal)
        self.assertIn(str(self.old / "python3") + " (3.8)", refusal)
        with mock.patch.object(bootstrap, "pins_interpreter", return_value=True), \
             mock.patch.dict(os.environ, {"PATH": str(self.old)}), \
             self.assertRaises(SystemExit):
            bootstrap.resolve_install_interpreter()

    def _cfg(self, body):
        (self.root / "session.config.json").write_text(json.dumps(body), encoding="utf-8")

    def test_pin_writes_layout_interpreter_and_says_so(self):
        self._cfg({"architect_id": "x-arch", "machine_map": {}})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertTrue(bootstrap.pin_interpreter(self.root, "/usr/bin/python3"))
        cfg = json.loads((self.root / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["layout"]["interpreter"], "/usr/bin/python3")
        self.assertEqual(cfg["architect_id"], "x-arch")
        self.assertIn("pinned /usr/bin/python3", out.getvalue())
        # ...and it is what the resolver then reads.
        self.assertEqual(interpreter.declared(cfg), "/usr/bin/python3")

    def test_pin_never_overrides_a_declared_interpreter(self):
        for body in ({"interpreter": "/opt/py"}, {"layout": {"interpreter": "/opt/py"}}):
            with self.subTest(body=body):
                self._cfg(body)
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(bootstrap.pin_interpreter(self.root, "/usr/bin/python3"))
                cfg = json.loads((self.root / "session.config.json").read_text(encoding="utf-8"))
                self.assertEqual(interpreter.declared(cfg), "/opt/py")

    def test_no_path_writes_nothing(self):
        self._cfg({"a": 1})
        self.assertFalse(bootstrap.pin_interpreter(self.root, ""))
        self.assertNotIn("layout", json.loads(
            (self.root / "session.config.json").read_text(encoding="utf-8")))


@unittest.skipUnless(shutil.which("git"), "git required")
class InitPinsOnLinuxTest(unittest.TestCase):
    """The real `poga init`, end to end, through `poga_cli.py`."""

    def setUp(self):
        from test_local_init import Sandbox  # noqa: PLC0415  (the shared init sandbox)
        self.sb = Sandbox(self)

    @unittest.skipUnless(ON_LINUX, "the pin is Linux-only; macOS keeps its default")
    def test_init_records_the_first_python3_at_or_above_3_9(self):
        old = self.sb.root / "oldpy"
        old.mkdir()
        _stub(old, "python3", "echo '3 8'")
        self.sb.env["PATH"] = f"{old}{os.pathsep}{self.sb.env['PATH']}"
        want, _ = interpreter.first_on_path(self.sb.env["PATH"])
        self.assertTrue(want)
        a = self.sb.project("alpha")
        proc = self.sb.cli("init", "--yes", cwd=a)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        cfg = json.loads((a / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["layout"]["interpreter"], want)
        self.assertNotEqual(want, str(old / "python3"))
        self.assertIn(f"pinned {want}", proc.stdout)
        # Committed with the install, not left as dirt.
        st = subprocess.run(["git", "-C", str(a), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
        self.assertEqual(st, "")

    @unittest.skipUnless(ON_LINUX, "the refusal is Linux-only")
    def test_init_refuses_when_no_python3_is_new_enough_and_writes_nothing(self):
        only = self.sb.root / "onlyold"
        only.mkdir()
        _stub(only, "python3", "echo '3 6'")
        for tool in ("git", "hostname"):
            real = shutil.which(tool)
            if real:
                os.symlink(real, only / tool)
        self.sb.env["PATH"] = f"{self.sb.bin}{os.pathsep}{only}"
        a = self.sb.project("alpha")
        proc = self.sb.cli("init", "--yes", cwd=a)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("3.9 or newer", proc.stdout + proc.stderr)
        self.assertEqual(list(a.iterdir()), [])
        self.assertFalse(self.sb.fed.exists())

    @unittest.skipIf(ON_LINUX, "macOS behaviour")
    def test_macos_init_writes_no_pin(self):
        a = self.sb.project("alpha")
        proc = self.sb.cli("init", "--yes", cwd=a)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        cfg = json.loads((a / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(interpreter.declared(cfg), "")


# ---------------------------------------------------------------------------
# 7. root
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("bash"), "bash required")
class RootRefusalTest(unittest.TestCase):
    MSG = "refusing to run as root"

    def setUp(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("the suite must not run as root")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = pathlib.Path(tmp.name)

    def _poga(self, uid, *args):
        _stub(self.bin, "id", f'[ "$1" = "-u" ] && echo {uid} || exec /usr/bin/id "$@"')
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        return subprocess.run(["bash", str(POGA), *args], env=env, cwd=str(self.bin),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=60)

    def test_poga_as_root_refuses_in_one_line_before_doing_anything(self):
        r = self._poga(0, "lanes")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        lines = r.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn(self.MSG, lines[0])
        self.assertIn("normal user", lines[0])

    def test_poga_help_still_answers_as_root(self):
        r = self._poga(0, "--help")
        self.assertNotIn(self.MSG, r.stderr)
        self.assertIn("usage: poga", r.stdout)

    def test_a_normal_uid_is_not_refused(self):
        r = self._poga(1000, "lanes")
        self.assertNotIn(self.MSG, r.stderr)

    def test_the_refusal_runs_before_the_first_verb_dispatch(self):
        src = POGA.read_text(encoding="utf-8")
        call = re.search(r'^poga_refuse_root "\$\{1:-\}"$', src, re.M)
        self.assertIsNotNone(call)
        self.assertLess(call.start(), src.index("require_repo() {"))

    def test_session_py_as_root_refuses_with_a_blocking_exit(self):
        """Exit 2, because session.py is also every PreToolUse hook: there 2 BLOCKS,
        and any other non-zero would let the tool call through unguarded."""
        code = ("import os, runpy, sys; os.geteuid = lambda: 0; "
                f"sys.argv = [{str(ROOT / 'session.py')!r}, 'interpreter']; "
                f"runpy.run_path({str(ROOT / 'session.py')!r}, run_name='__main__')")
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(self.MSG, r.stderr)

    def test_session_py_as_a_user_runs(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "session.py"), "interpreter"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(self.MSG, r.stderr)


if __name__ == "__main__":
    unittest.main()
