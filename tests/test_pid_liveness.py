"""One liveness helper, and an unsure answer never reads as death.

Refactor-cleanup package 1. `_pid_alive` was defined TWICE in sessionlib/config.py. The
parts are exec'd into one namespace (ADR-0118), so the later definition silently replaced
the earlier one — and the earlier one's docstring promised "a live lane can never be
reaped" while its catch-all returned False (dead) for an OverflowError or any OSError.
Neither copy honoured the promise for every input: the live one let OverflowError escape
into the lane sweep.

The contract pinned here: `_pid_alive` is False ONLY when signal 0 proves the pid is gone
(ESRCH). Permission-denied is alive. Oversized, non-positive, non-int and any other OSError
are UNSURE, and unsure reads as alive — the safe direction for every caller (the reaper
skips, the unlocker leaves the lock, the process sweep reports SURVIVED).

The second class is the static guard: no top-level name may be bound twice across the
assembler's PARTS, because a duplicate is not an error in one namespace — it is a silent
override. It stays as long as the exec assembler does.

stdlib unittest: python3 -m unittest tests.test_pid_liveness
"""

import ast
import collections
import errno
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import session  # noqa: E402
import sessionlib  # noqa: E402


def _dead_pid() -> int:
    """A pid that existed a moment ago and has been reaped — provably gone."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class PidLivenessContract(unittest.TestCase):
    def test_live_pid_is_alive(self):
        self.assertIs(session._pid_liveness(os.getpid()), True)
        self.assertTrue(session._pid_alive(os.getpid()))

    def test_dead_pid_is_dead(self):
        pid = _dead_pid()
        self.assertIs(session._pid_liveness(pid), False)
        self.assertFalse(session._pid_alive(pid))

    def test_permission_denied_is_alive(self):
        with mock.patch.object(session.os, "kill", side_effect=PermissionError(errno.EPERM, "nope")):
            self.assertIs(session._pid_liveness(4242), True)
            self.assertTrue(session._pid_alive(4242))

    def test_oversized_pid_is_unsure_not_dead(self):
        huge = 2 ** 80
        self.assertIsNone(session._pid_liveness(huge))
        self.assertTrue(session._pid_alive(huge))

    def test_invalid_pids_are_unsure_not_dead(self):
        # 0 and negatives address process GROUPS, not a process; a bool is not a pid.
        for bad in (0, -1, -4242, True, False, None, "1234", 12.0):
            with self.subTest(pid=bad):
                self.assertIsNone(session._pid_liveness(bad))
                self.assertTrue(session._pid_alive(bad))

    def test_unexpected_oserror_is_unsure_not_dead(self):
        for exc in (OSError(errno.EINVAL, "weird"), OverflowError("big"), ValueError("bad")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(session.os, "kill", side_effect=exc):
                self.assertIsNone(session._pid_liveness(4242))
                self.assertTrue(session._pid_alive(4242))

    def test_signal_zero_is_the_only_probe(self):
        with mock.patch.object(session.os, "kill") as kill:
            session._pid_alive(4242)
        kill.assert_called_once_with(4242, 0)


class CallersTakeTheSafeDirection(unittest.TestCase):
    """The callers are not re-tested end to end here (test_lane_litter, test_lane_recovery
    and test_phantom_lane_discard patch `_pid_alive` for that); what is pinned is that an
    unsure pid reaches them as ALIVE, through the one shared helper."""

    def test_unlocker_leaves_a_lock_whose_pid_is_oversized(self):
        porcelain = ("worktree /x/lane\nHEAD abc\nbranch refs/heads/l\n"
                     f"locked claude agent (pid {2 ** 80})\n")
        calls = []

        def fake_sh(cmd, check=False, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=porcelain, stderr="")

        with mock.patch.object(session, "sh", side_effect=fake_sh), \
                mock.patch.object(session.Path, "resolve", lambda self: self):
            unlocked = session._unlock_if_owner_is_dead("/x/lane")
        self.assertFalse(unlocked)
        self.assertFalse(any("unlock" in c for c in calls), calls)

    def test_process_sweep_reports_survived_when_liveness_is_unsure(self):
        proc = {"pid": 2 ** 80, "lane": "l", "reason": "gone", "age_sec": 7200}
        with mock.patch.object(session, "_stranded_lane_processes", return_value=[proc]), \
                mock.patch.object(session.os, "kill") as kill, \
                mock.patch.object(session, "PROC_SIGNAL_GRACE_SEC", 0.0):
            # SIGTERM/SIGKILL "succeed"; signal 0 then raises OverflowError (unsure).
            def k(pid, sig):
                if sig == 0:
                    raise OverflowError("too big")
            kill.side_effect = k
            lines = session._reap_lane_processes()
        self.assertEqual(len(lines), 1)
        self.assertIn("SURVIVED", lines[0])


def _top_level_bindings(path: pathlib.Path):
    """(name, line) for each def/class/plain assignment at module top level."""
    for node in ast.parse(path.read_text(encoding="utf-8"), str(path)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name, node.lineno
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, node.lineno
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id, node.lineno


def duplicate_part_bindings(parts_dir: pathlib.Path, parts) -> dict:
    """{name: [(part, line), ...]} for every top-level name bound more than once across
    the assembler's parts — in ONE namespace, each extra binding is a silent override."""
    seen = collections.defaultdict(list)
    for part in parts:
        for name, line in _top_level_bindings(parts_dir / (part + ".py")):
            seen[name].append((part, line))
    return {k: v for k, v in seen.items() if len(v) > 1}


class NoDuplicateTopLevelNamesAcrossParts(unittest.TestCase):
    def test_parts_bind_each_top_level_name_once(self):
        parts_dir = pathlib.Path(sessionlib.__file__).resolve().parent
        dups = duplicate_part_bindings(parts_dir, sessionlib.PARTS)
        self.assertEqual(dups, {}, "a top-level name is bound more than once across "
                         "sessionlib PARTS; the later one silently replaces the earlier "
                         f"in the shared namespace: {dups}")

    def test_the_check_catches_a_duplicate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "a.py").write_text("def f():\n    return 1\nX = 1\n", encoding="utf-8")
            (d / "b.py").write_text("def f():\n    return 2\n", encoding="utf-8")
            (d / "c.py").write_text("X: int = 2\nclass K:\n    f = 3\n", encoding="utf-8")
            dups = duplicate_part_bindings(d, ("a", "b", "c"))
        self.assertEqual(set(dups), {"f", "X"})
        self.assertEqual(dups["f"], [("a", 1), ("b", 1)])


if __name__ == "__main__":
    unittest.main()
