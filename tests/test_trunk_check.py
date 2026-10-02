"""WI-0427 / ADR-0148 D5: the trunk verifies itself after the merge.

The land verb no longer runs the suite, so the one defect only the MERGED tree can show —
two lanes each green alone, red together — is caught here instead: a background runner
checks the newest trunk tip, names the failing tests, bisects a coalesced range by LAND
boundaries to find the land that turned it red, and the next code land refuses.

Every fixture is a temp git repo with a FAKE suite (`curate/run_suite.py` inside the
fixture) whose verdict is a fact of the checked-out tree: a test module whose text says
BROKEN fails with a real identity line. So the verdicts below are produced by actually
staging scratch worktrees of the commits in question, not by stubbing the runner — the
worktree staging, `-p` narrowing and module-file mapping are all on the tested path.
"""
import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import session
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

FAKE_SUITE = r'''
import argparse, fnmatch, pathlib, sys
ap = argparse.ArgumentParser()
ap.add_argument("-p", "--pattern", default="test*.py")
args, _ = ap.parse_known_args()
bad = 0
for f in sorted(pathlib.Path("tests").glob("*.py")):
    if not fnmatch.fnmatch(f.name, args.pattern):
        continue
    if "BROKEN" in f.read_text():
        print(f"FAIL: test_x ({f.stem}.C)", file=sys.stderr)
        bad += 1
print("Ran N tests", file=sys.stderr)
sys.exit(1 if bad else 0)
'''


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=repo, text=True, capture_output=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


class TrunkCheckBase(unittest.TestCase):
    def setUp(self):
        # The lock and the running-state carry this host's name (`session._coord_host`),
        # so the module reads as a coordination writer to both fixture guards.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="trunkcheck-")).resolve()
        self.repo = self.tmp / "repo"
        (self.repo / "curate").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        _git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "curate" / "run_suite.py").write_text(FAKE_SUITE)
        (self.repo / "session.py").write_text("# fixture entry point\n")
        (self.repo / "tests" / "test_a.py").write_text("ok\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "base")
        for p in [mock.patch.object(session, "ROOT", self.repo),
                  mock.patch.object(session, "CFG",
                                    {"trunk": "main",
                                     "gate": ["python3 curate/run_suite.py"]}),
                  mock.patch.object(session, "_SHARED_WORK_ROOT", None),
                  mock.patch.dict(os.environ, {}, clear=False)]:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(session.GATE_ENV_VAR, None)
        os.environ.pop(session.TRUNK_CHECK_OFF_ENV, None)
        self.common = pathlib.Path(_git(self.repo, "rev-parse", "--path-format=absolute",
                                        "--git-common-dir")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def commit(self, name, text, msg=None):
        (self.repo / "tests" / name).write_text(text)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg or f"change {name}")
        return _git(self.repo, "rev-parse", "HEAD")

    def land(self, lane, name, text):
        """One land: a commit on main plus its receipt, as `_land_receipt` writes it."""
        parent = _git(self.repo, "rev-parse", "HEAD")
        tip = self.commit(name, text, f"land from {lane}")
        with open(self.common / session.LAND_RECEIPTS_NAME, "a") as fh:
            fh.write(json.dumps({"verb": "land-lane", "outcome": "landed", "trunk": "main",
                                 "parent": parent[:12], "tip": tip[:12], "lane": lane,
                                 "at_epoch": time.time()}) + "\n")
        return parent, tip

    def state(self):
        return session._trunk_check_state()

    def write_state(self, **kw):
        session._trunk_check_write(kw)


class TheRunnerRecordsAVerdictTest(TrunkCheckBase):
    def test_a_green_tip_records_green_and_the_baseline(self):
        tip = _git(self.repo, "rev-parse", "HEAD")
        st = session._trunk_check_run("main")
        self.assertEqual(st["state"], "green", st)
        self.assertEqual(st["tip"], tip)
        self.assertEqual(st["last_green_tip"], tip)
        self.assertIsNone(st["culprit"])
        self.assertIn("GREEN at " + tip[:12], session.trunk_check_line())
        # the scratch worktree is gone
        self.assertNotIn(session.GATE_WORKTREE_PREFIX, _git(self.repo, "worktree", "list"))

    def test_a_red_tip_names_the_failing_test(self):
        self.land("poga-1", "test_b.py", "BROKEN\n")
        st = session._trunk_check_run("main")
        self.assertEqual(st["state"], "red", st)
        self.assertEqual(st["failing"], ["FAIL: test_x (test_b.C)"])
        self.assertEqual(st["culprit"]["lane"], "poga-1")
        self.assertIn("unbisected", st["culprit"]["found_by"])
        self.assertIn("git revert --no-edit", st["proposal"])
        self.assertIn("RED at", session.trunk_check_line())
        self.assertIn("culprit lane poga-1", session.trunk_check_line())

    def test_an_already_checked_tip_is_not_run_again(self):
        session._trunk_check_run("main")
        with mock.patch.object(session, "_trunk_check_suite") as suite:
            session._trunk_check_run("main")
        suite.assert_not_called()


class TheCulpritIsTheLandThatTurnedItRedTest(TrunkCheckBase):
    def test_bisect_names_the_middle_of_three_coalesced_lands(self):
        """The planted semantic conflict: three lands arrive while one check runs; the
        second breaks a test. Only the second may be blamed, and the bisection runs the
        failing module alone, never the full suite, at each candidate."""
        session._trunk_check_run("main")                      # green baseline
        self.land("poga-1", "test_c.py", "ok\n")
        p2, t2 = self.land("poga-2", "test_b.py", "BROKEN\n")
        self.land("poga-3", "test_d.py", "ok\n")
        real = session._trunk_check_suite
        calls = []

        def spy(sha, files=None):
            calls.append((sha, files))
            return real(sha, files)

        with mock.patch.object(session, "_trunk_check_suite", side_effect=spy):
            st = session._trunk_check_run("main")
        self.assertEqual(st["state"], "red", st)
        c = st["culprit"]
        self.assertEqual((c["lane"], c["parent"], c["tip"]), ("poga-2", p2[:12], t2[:12]))
        self.assertEqual(c["found_by"], "bisected")
        self.assertIn(f"{p2[:12]}..{t2[:12]}", st["proposal"])
        self.assertIsNone(calls[0][1], "the check itself runs the full suite")
        self.assertTrue(len(calls) > 1, "control: the bisection must actually have run")
        for _sha, files in calls[1:]:
            self.assertEqual(files, ["tests/test_b.py"])

    def test_a_single_land_in_range_is_named_without_bisecting(self):
        session._trunk_check_run("main")
        _p, t = self.land("poga-9", "test_b.py", "BROKEN\n")
        real = session._trunk_check_suite
        with mock.patch.object(session, "_trunk_check_suite", side_effect=real) as suite:
            st = session._trunk_check_run("main")
        self.assertEqual(st["culprit"]["tip"], t[:12])
        self.assertEqual(st["culprit"]["found_by"], "only land in range")
        self.assertEqual(suite.call_count, 1)

    def test_module_files_come_from_both_identity_spellings(self):
        self.assertEqual(session._trunk_check_module_files(
            ["FAIL: test_x (test_m.C)", "ERROR: test_y (test_m.C.test_y)",
             "FAIL: test_z (test_n.D)"]), ["tests/test_m.py", "tests/test_n.py"])


class TheRunnerCoalescesTest(TrunkCheckBase):
    def test_a_second_runner_returns_at_once_while_the_lock_is_held(self):
        lock = self.common / session.TRUNK_CHECK_LOCK_NAME
        lock.write_text(json.dumps({"pid": os.getpid(), "host": session._coord_host(),
                                    "at": time.time()}))
        with mock.patch.object(session, "_trunk_check_suite") as suite:
            session._trunk_check_run("main")
        suite.assert_not_called()
        self.assertTrue(lock.exists(), "a loser must not release the holder's lock")

    def test_a_dead_holders_lock_is_reclaimed(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        lock = self.common / session.TRUNK_CHECK_LOCK_NAME
        lock.write_text(json.dumps({"pid": p.pid, "host": session._coord_host(),
                                    "at": time.time()}))
        st = session._trunk_check_run("main")
        self.assertEqual(st["state"], "green")
        self.assertFalse(lock.exists(), "the runner releases what it took")

    def test_the_runner_rechecks_when_the_tip_moved_during_its_run(self):
        real = session._trunk_check_suite
        moved = {}

        def racing(sha, files=None):
            out = real(sha, files)
            if not moved:
                moved["tip"] = self.commit("test_e.py", "ok\n", "arrived mid-check")
            return out

        with mock.patch.object(session, "_trunk_check_suite", side_effect=racing) as s:
            st = session._trunk_check_run("main")
        self.assertEqual(s.call_count, 2)
        self.assertEqual(st["tip"], moved["tip"])
        self.assertEqual(st["state"], "green")


class WhatTheFirstRealRunGotWrongTest(TrunkCheckBase):
    """Three defects a first live trunk check can show: the red
    was overwritten by "running" the moment the runner moved to a newer tip (so the
    next code land was NOT refused), the unbisected culprit was a land that arrived
    AFTER the red tip, and the failing test's name was never written to the log."""

    def _red_then_a_land_arrives_mid_check(self):
        """Red at the first tip; a bookkeeping land arrives while that check runs."""
        _p, red_tip = self.land("poga-bad", "test_b.py", "BROKEN\n")
        real = session._trunk_check_suite
        seen = {}

        def racing(sha, files=None):
            out = real(sha, files)
            if not seen:
                seen["later"] = self.land("poga-later", "notes.txt", "x\n")[1]
            return out

        return red_tip, seen, racing

    def test_the_culprit_is_never_a_land_after_the_red_tip(self):
        red_tip, seen, racing = self._red_then_a_land_arrives_mid_check()
        with mock.patch.object(session, "_trunk_check_suite", side_effect=racing):
            session._trunk_check_run("main")
        st = self.state()
        self.assertEqual(st["state"], "red", st)
        self.assertEqual(st["culprit"]["lane"], "poga-bad", st["culprit"])
        self.assertEqual(st["culprit"]["tip"], red_tip[:12])

    def test_the_red_survives_the_next_check_starting(self):
        red_tip, seen, racing = self._red_then_a_land_arrives_mid_check()
        started = {}

        def spy_write(state, _real=session._trunk_check_write):
            if state.get("state") == "running" and state.get("tip") == seen.get("later"):
                started["refusal"] = session._trunk_check_refusal(True, None) or "<none>"
                _real(state)
                started["refusal"] = session._trunk_check_refusal(True, None) or "<none>"
                started["line"] = session.trunk_check_line()
                return
            _real(state)

        with mock.patch.object(session, "_trunk_check_suite", side_effect=racing), \
                mock.patch.object(session, "_trunk_check_write", side_effect=spy_write):
            session._trunk_check_run("main")
        self.assertIn("refused", started["refusal"])
        self.assertIn("still RED", started["line"])

    def test_a_green_check_clears_the_carried_red(self):
        self.land("poga-bad", "test_b.py", "BROKEN\n")
        session._trunk_check_run("main")
        self.commit("test_b.py", "ok\n", "the fix")
        st = session._trunk_check_run("main")
        self.assertEqual(st["state"], "green")
        self.assertIsNone(st.get("red"))
        self.assertEqual(session._trunk_check_refusal(True, None), "")

    def test_the_failing_test_is_named_in_the_log(self):
        self.land("poga-bad", "test_b.py", "BROKEN\n")
        session._trunk_check_run("main")
        log = (self.common / session.TRUNK_CHECK_LOG_NAME).read_text()
        self.assertIn("FAIL: test_x (test_b.C)", log)


class TheRefusalTest(TrunkCheckBase):
    def _red(self):
        _p, tip = self.land("poga-2", "test_b.py", "BROKEN\n")
        session._trunk_check_run("main")
        self.assertEqual(self.state()["state"], "red")
        return tip

    def test_a_bookkeeping_land_passes_a_red_trunk(self):
        self._red()
        self.assertEqual(session._trunk_check_refusal(False, None), "")

    def test_a_code_land_is_refused_on_a_red_trunk(self):
        red = self._red()
        msg = session._trunk_check_refusal(True, _git(self.repo, "rev-parse", "HEAD~1"))
        self.assertIn("RED at " + red[:12], msg)
        self.assertIn("FAIL: test_x (test_b.C)", msg)
        self.assertIn("lane poga-2", msg)
        self.assertIn("git revert --no-edit", msg)
        self.assertIn("Bookkeeping lands still go through", msg)

    def test_a_refusal_holds_after_the_trunk_moves_past_the_red_tip(self):
        self._red()
        self.commit("test_f.py", "ok\n", "a store write")
        self.assertNotEqual(session._trunk_check_refusal(True, None), "")

    def test_a_lane_built_on_the_red_tip_may_land(self):
        red = self._red()
        _git(self.repo, "checkout", "-qb", "fix", red)
        head = self.commit("test_b.py", "ok\n", "the fix")
        _git(self.repo, "checkout", "-q", "main")
        self.assertEqual(session._trunk_check_refusal(True, head), "")

    def test_a_dead_runner_reads_unknown_and_refuses_nothing(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.write_state(state="running", tip=_git(self.repo, "rev-parse", "HEAD"),
                         trunk="main", pid=p.pid, host=session._coord_host(),
                         checked_at=time.time())
        self.assertEqual(session._trunk_check_effective_state(), "unknown")
        self.assertEqual(session._trunk_check_refusal(True, None), "")
        self.assertIn("UNKNOWN", session.trunk_check_line())

    def test_a_live_runner_reads_running(self):
        self.write_state(state="running", tip=_git(self.repo, "rev-parse", "HEAD"),
                         trunk="main", pid=os.getpid(), host=session._coord_host(),
                         checked_at=time.time())
        self.assertIn("RUNNING on", session.trunk_check_line())
        self.assertEqual(session._trunk_check_refusal(True, None), "")

    def test_no_state_is_unknown_and_refuses_nothing(self):
        self.assertEqual(session.trunk_check_line(), "trunk check: UNKNOWN — no check has run")
        self.assertEqual(session._trunk_check_refusal(True, None), "")


class TheSpawnNeverBlocksTest(TrunkCheckBase):
    def test_it_is_a_no_op_under_test(self):
        popen = mock.Mock()
        with mock.patch.object(session, "_TRUNK_CHECK_POPEN", popen):
            out = session._trunk_check_spawn("main", "abc")
        self.assertEqual(out, {"spawned": False, "reason": "under test"})
        popen.assert_not_called()

    def test_an_opted_in_spawn_is_detached_and_never_waited_on(self):
        proc = mock.Mock(pid=4242)
        popen = mock.Mock(return_value=proc)
        with mock.patch.object(session, "_TRUNK_CHECK_POPEN", popen), \
                mock.patch.object(session, "_TRUNK_CHECK_ALLOW_UNDER_TEST", True):
            out = session._trunk_check_spawn("main", "abc")
        self.assertEqual(out, {"spawned": True, "pid": 4242})
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:], [str(self.repo / "session.py"), "trunk-check", "--run"])
        kw = popen.call_args.kwargs
        self.assertTrue(kw["start_new_session"])
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(proc.method_calls, [], "the caller must never wait on the runner")

    def test_no_spawn_inside_the_gate_or_when_switched_off(self):
        popen = mock.Mock()
        with mock.patch.object(session, "_TRUNK_CHECK_POPEN", popen), \
                mock.patch.object(session, "_TRUNK_CHECK_ALLOW_UNDER_TEST", True):
            with mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"}):
                self.assertFalse(session._trunk_check_spawn("main")["spawned"])
            with mock.patch.dict(os.environ, {session.TRUNK_CHECK_OFF_ENV: "off"}):
                self.assertFalse(session._trunk_check_spawn("main")["spawned"])
        popen.assert_not_called()

    def test_a_failing_popen_is_reported_not_raised(self):
        with mock.patch.object(session, "_TRUNK_CHECK_POPEN", side_effect=OSError("nope")), \
                mock.patch.object(session, "_TRUNK_CHECK_ALLOW_UNDER_TEST", True):
            out = session._trunk_check_spawn("main")
        self.assertFalse(out["spawned"])
        self.assertIn("nope", out["reason"])


class TheVerbTest(TrunkCheckBase):
    def test_clear_deletes_the_state(self):
        session._trunk_check_run("main")
        self.assertTrue(self.state())
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_trunk_check(argparse.Namespace(run=False, clear=True))
        self.assertEqual(self.state(), {})

    def test_run_checks_and_prints_the_line(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_trunk_check(argparse.Namespace(run=True, clear=False))
        self.assertIn("trunk check: GREEN at", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
