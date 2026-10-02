"""WI-0407 — OPS-0009's nightly runner, and the four outcomes it must keep apart.

WHAT THESE GUARD, and why each one is a defect somebody would otherwise reintroduce.

THE LEDGER IS NOT A LOG. `poga ops ran` rolls `due` forward from the cadence on EVERY
result, including `fail`. So "record whatever happened" — the obvious simplification, and
the one a reader reaches for when they see `ops_result_for` return None — silently marks a
daily obligation met on a night it was not met, and drops it off the startup view until
tomorrow. Two of these tests exist only to make that simplification fail.

A RED DERIVE STILL WRITES A RECORD. `gate_inputs.py` writes `gate-inputs.json` before it
returns 1 on a red suite, and the land's reader folds `suite_ok: false` to None. Landing
one would replace a usable record with one that licenses no gate skips at all. So "did it
write a record?" is not the landing test, and `should_land` is asserted against all four
outcomes rather than the two that are obvious.

THE REFUSAL AND THE RED SUITE BOTH EXIT 1. Neither the exit code nor the presence of a
record discriminates them, because the previous run's record may still be on disk when
this run's derive refuses to write one. `classify_derive` is therefore asserted with a STALE record
present, which is the case that gets the ordering wrong.

THE LAND IS PART OF THE OBLIGATION. A green suite whose record never reached the trunk has
not met OPS-0009 — the item's own negative control says a runner that derives and stops
"does not close this item, because the ledger then stays exactly one run behind reality".
`outcome_after_land` is where that lives.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "gate_inputs_runner", ROOT / "curate" / "gate-inputs-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

GIT = shutil.which("git")

#: A record shaped like the one a RED derive leaves behind: well-formed, and false.
RED_RECORD = {"schema": 3, "tests": 5895, "suite_ok": False,
              "verdict": {"exit_code": 1, "failures": 2, "errors": 1}}
GREEN_RECORD = {"schema": 3, "tests": 5895, "suite_ok": True,
                "verdict": {"exit_code": 0, "failures": 0, "errors": 0}}


class ClassifyDeriveTest(unittest.TestCase):

    def test_a_clean_exit_is_green(self):
        outcome, why = runner.classify_derive(
            0, "derived: 42 read prefix(es) from 5895 tests (suite green)", "", GREEN_RECORD)
        self.assertEqual(outcome, runner.GREEN)
        self.assertIn("green", why)

    def test_a_red_suite_is_red_and_names_the_counts(self):
        outcome, why = runner.classify_derive(
            1, "derived: ... (suite RED)", "", RED_RECORD)
        self.assertEqual(outcome, runner.RED)
        self.assertIn("2 failure(s)", why)
        self.assertIn("1 error(s)", why)

    def test_the_tests_changed_refusal_is_refused_not_red(self):
        outcome, why = runner.classify_derive(
            1, "", "derive: tests changed during measurement; retry the derive", None)
        self.assertEqual(outcome, runner.REFUSED)
        self.assertIn("moving tree", why)

    def test_the_refusal_wins_over_a_record_left_from_last_night(self):
        """THE ORDERING TEST, and the reason `classify_derive` checks the message first.

        A refusal writes NO record, so whatever is on disk is the previous night's. If that
        record happened to be red — a red night followed by a refusing night, which is
        exactly the sequence a broken tree produces — reading the record first would report
        RED, record `fail`, roll the obligation forward, and bury the refusal.
        """
        outcome, _ = runner.classify_derive(
            1, "", "derive: tests changed during measurement; retry the derive", RED_RECORD)
        self.assertEqual(outcome, runner.REFUSED)

    def test_a_floor_refusal_is_an_error_and_quotes_it(self):
        outcome, why = runner.classify_derive(
            1, "", "derive: refusing to write a record — child audit incomplete.", None)
        self.assertEqual(outcome, runner.ERROR)
        self.assertIn("refusing to write a record", why)

    def test_a_timeout_is_an_error(self):
        outcome, why = runner.classify_derive(124, "", "the derive did not finish", None)
        self.assertEqual(outcome, runner.ERROR)
        self.assertIn("124", why)

    def test_a_signal_death_is_an_error_that_names_the_signal(self):
        outcome, why = runner.classify_derive(-9, "", "", None)
        self.assertEqual(outcome, runner.ERROR)
        self.assertIn("signal 9", why)

    def test_a_green_exit_is_green_even_with_no_record_to_read(self):
        """Exit 0 is the deriver's own verdict. If the record cannot be read back the land
        step fails on its own terms and says so; inventing an ERROR here would report the
        wrong cause for a green run."""
        outcome, _ = runner.classify_derive(0, "derived: ...", "", None)
        self.assertEqual(outcome, runner.GREEN)


class WhatReachesTheLedgerTest(unittest.TestCase):

    def test_only_a_run_with_a_verdict_is_recorded(self):
        self.assertEqual(runner.ops_result_for(runner.GREEN), "pass")
        self.assertEqual(runner.ops_result_for(runner.RED), "fail")

    def test_a_refusal_writes_NOTHING_to_the_ledger(self):
        """NOT an oversight, and the test that makes the simplification fail.

        `ops ran` rolls `due` forward on every result. A refusal recorded as `fail` would
        mark this daily obligation met on a night it did not run, and the startup view —
        which lists ops rows only when they are `overdue` or `never run` — would show
        nothing. Leaving the ledger alone is what makes the skipped night visible.
        """
        self.assertIsNone(runner.ops_result_for(runner.REFUSED))

    def test_an_error_writes_NOTHING_to_the_ledger(self):
        self.assertIsNone(runner.ops_result_for(runner.ERROR))

    def test_every_outcome_is_covered_so_a_fifth_cannot_be_forgotten(self):
        for outcome in (runner.GREEN, runner.RED, runner.REFUSED, runner.ERROR):
            with self.subTest(outcome=outcome):
                self.assertIn(runner.ops_result_for(outcome), ("pass", "fail", None))


class WhatIsLandableTest(unittest.TestCase):

    def test_only_green_lands(self):
        self.assertTrue(runner.should_land(runner.GREEN))

    def test_a_red_record_is_never_landed(self):
        """A red derive DOES write a record. Landing it replaces a usable one with a
        record the land's reader folds to None, which licenses no gate skip at all — every
        land on the fleet gets slower until the next green night."""
        self.assertFalse(runner.should_land(runner.RED))

    def test_neither_a_refusal_nor_an_error_lands(self):
        self.assertFalse(runner.should_land(runner.REFUSED))
        self.assertFalse(runner.should_land(runner.ERROR))


class AGreenDeriveThatDidNotLandTest(unittest.TestCase):

    def test_a_green_derive_whose_land_failed_is_not_a_met_obligation(self):
        """WI-0407's negative control, arriving by the other route.

        The suite ran and passed, so `pass` is tempting. But the record is in a lane about
        to be reaped, and the obligation is that the record MOVED. Recording `pass` would
        roll the obligation forward on work that was thrown away.
        """
        self.assertEqual(runner.outcome_after_land(runner.GREEN, landed=False), runner.ERROR)
        self.assertIsNone(
            runner.ops_result_for(runner.outcome_after_land(runner.GREEN, landed=False)))

    def test_a_green_derive_that_landed_is_a_met_obligation(self):
        self.assertEqual(runner.outcome_after_land(runner.GREEN, landed=True), runner.GREEN)
        self.assertEqual(
            runner.ops_result_for(runner.outcome_after_land(runner.GREEN, landed=True)), "pass")

    def test_a_red_outcome_is_untouched_by_the_land_flag(self):
        """RED never reaches the land, so `landed` is False for reasons that have nothing
        to do with a failure to land. It must not be rewritten to ERROR — a red suite is a
        run with a verdict and the ledger should say so."""
        self.assertEqual(runner.outcome_after_land(runner.RED, landed=False), runner.RED)
        self.assertEqual(runner.outcome_after_land(runner.REFUSED, landed=False), runner.REFUSED)


#: A green serial suite whose gate-command measurement ran `run_suite.py` red — the
#: 2026-09-29 02:30 night, trimmed. The in-process probe passed; the sharded runner under
#: `POGA_GATE=1` did not. That is a red gate, and it read GREEN.
GATE_RED_RECORD = dict(GREEN_RECORD, commands=[
    {"cmd": "python3 session.py wi-check", "exit_code": 0, "measured": True},
    {"cmd": "python3 curate/run_suite.py", "exit_code": 1, "measured": False,
     "unmeasured_reason": "exited 1 — a failing check stops early",
     "stderr": "AssertionError: False is not true\n\n-----\nRan 6676 tests in 1033.241s"
               "\n\nFAILED (failures=2, skipped=51)\n"},
])


class AGateCommandThatExitedNonzeroTest(unittest.TestCase):
    """Brief 2026-09-29, work A. The derive's exit code speaks only for the serial suite.
    Each gate command is then run for real to measure it, and a command that exits nonzero
    is a gate that would refuse the land. The deriver already records that
    (`exit_code`, `measured: False`); the runner read none of it and said GREEN."""

    def test_a_green_suite_with_a_red_gate_command_is_red(self):
        outcome, why = runner.classify_derive(0, "derived: ...", "", GATE_RED_RECORD)
        self.assertEqual(outcome, runner.RED)
        self.assertIn("python3 curate/run_suite.py", why)
        self.assertIn("exited 1", why)
        self.assertIn("FAILED (failures=2, skipped=51)", why)

    def test_it_is_not_landed_and_the_ledger_records_fail(self):
        outcome, _ = runner.classify_derive(0, "", "", GATE_RED_RECORD)
        self.assertFalse(runner.should_land(outcome))
        self.assertEqual(runner.ops_result_for(outcome), "fail")

    def test_an_unmeasured_command_that_exited_zero_is_still_green(self):
        """Unmeasured is not failed. A command whose shim never reported simply runs on
        every land; it says nothing about whether the gate passes."""
        record = dict(GREEN_RECORD, commands=[
            {"cmd": "python3 curate/distill.py --check", "exit_code": 0, "measured": False,
             "unmeasured_reason": "the shim never reported"}])
        self.assertEqual(runner.classify_derive(0, "", "", record)[0], runner.GREEN)

    def test_a_command_that_could_not_launch_is_red(self):
        record = dict(GREEN_RECORD, commands=[
            {"cmd": "python3 curate/gone.py", "measured": False,
             "unmeasured_reason": "could not launch: [Errno 2] No such file"}])
        outcome, why = runner.classify_derive(0, "", "", record)
        self.assertEqual(outcome, runner.RED)
        self.assertIn("could not launch", why)

    def test_the_note_names_the_failing_command_and_its_tail(self):
        block = runner.gate_command_block(runner.failing_gate_commands(GATE_RED_RECORD))
        self.assertIn("python3 curate/run_suite.py", block)
        self.assertIn("FAILED (failures=2, skipped=51)", block)
        self.assertEqual(runner.gate_command_block([]), "")

    def test_the_note_names_each_failing_test_the_deriver_kept(self):
        record = json.loads(json.dumps(GATE_RED_RECORD))
        record["commands"][1]["failed_tests"] = [
            "FAIL: test_one (tests.test_a.A.test_one)", "ERROR: test_two (tests.test_b.B)"]
        block = runner.gate_command_block(runner.failing_gate_commands(record))
        self.assertIn("FAIL: test_one (tests.test_a.A.test_one)", block)
        self.assertIn("ERROR: test_two (tests.test_b.B)", block)

    def test_the_deriver_keeps_the_failing_test_headers_the_tail_drops(self):
        """The 09-29 record's 400-char tail named one of two failures. The headers are
        printed far above the tail, so a long enough output drops them from it."""
        from curate import gate_inputs
        with tempfile.TemporaryDirectory() as tmp:
            script = pathlib.Path(tmp) / "red_check.py"
            script.write_text(
                "import sys\n"
                "sys.stderr.write('FAIL: test_early (tests.test_x.X)\\n')\n"
                "sys.stderr.write('x' * 2000 + '\\n')\n"
                "sys.stderr.write('ERROR: test_late (tests.test_y.Y)\\n')\n"
                "sys.stderr.write('FAILED (failures=1, errors=1)\\n')\n"
                "sys.exit(1)\n")
            entry = gate_inputs.measure_command(f"python3 {script}", set())
        self.assertEqual(entry["exit_code"], 1)
        self.assertNotIn("test_early", entry["stderr"])
        self.assertEqual(entry["failed_tests"], ["FAIL: test_early (tests.test_x.X)",
                                                 "ERROR: test_late (tests.test_y.Y)"])


class TheLandRunsFromTheLaneTest(unittest.TestCase):
    """Brief 2026-09-29, work A — the 09-29 night's actual failure. `land_record` ran the
    MAIN checkout's `session.py` with the lane as cwd. `session.py` resolves its repo from
    its own `__file__`, not from cwd, so it read the main checkout's branch and refused:
    `session.py merge: not on a session branch (on 'main')`. A green derive, thrown away.

    The fixture's `session.py` does what the real one does — asks git for the branch of
    the directory it lives in — so the test asserts where the land RAN, not which argv it
    was handed. `recover-lanes` gets this right by running the lane's own entry point."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.main = pathlib.Path(self._tmp.name) / "main"
        self.main.mkdir()
        self._git(self.main, "init", "-q", "-b", "main")
        self._git(self.main, "config", "user.email", "t@example.invalid")
        self._git(self.main, "config", "user.name", "t")
        self._git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "session.py").write_text(
            "import pathlib, subprocess, sys\n"
            "here = pathlib.Path(__file__).resolve().parent\n"
            "b = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=here,\n"
            "                   capture_output=True, text=True).stdout.strip()\n"
            "print('landing', b)\n"
            "sys.exit(0 if b.startswith('worktree-') else 1)\n")
        self._git(self.main, "add", "-A")
        self._git(self.main, "commit", "-qm", "base")
        self.lane = self.main / ".claude" / "worktrees" / "gate-lane"
        self._git(self.main, "worktree", "add", "-q", "-b", "worktree-gate-lane",
                  str(self.lane))

    def tearDown(self):
        self._tmp.cleanup() if pathlib.Path(self._tmp.name).exists() else None

    def _git(self, cwd, *args):
        return subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True,
                              check=False)

    def test_the_land_runs_against_the_lane_branch_not_main(self):
        with mock.patch.object(runner, "ROOT", self.main):
            code, out = runner.land_record(self.lane)
        self.assertIn("landing worktree-gate-lane", out)
        self.assertEqual(code, 0, out)


class TheBlockedNoteTest(unittest.TestCase):

    def _body(self, outcome):
        return runner.blocked_note_body(outcome, "something went wrong",
                                        "2026-09-20T02:48Z", "devbox", "the log")

    def test_a_red_night_says_the_ledger_recorded_fail(self):
        self.assertIn("recorded `fail`", self._body(runner.RED))

    def test_a_refused_night_says_the_ledger_was_left_alone_and_why(self):
        body = self._body(runner.REFUSED)
        self.assertIn("NOT recorded", body)
        self.assertIn("hide it until tomorrow", body)

    def test_the_note_names_the_obligation_and_declares_no_human_was_involved(self):
        body = self._body(runner.ERROR)
        self.assertIn(runner.OPS_ID, body)
        self.assertIn("no human was in the loop", body)
        self.assertIn("**Kind:** blocked", body)

    def test_the_note_says_its_own_filename_is_stable(self):
        """The note is overwritten while the condition holds and deleted on the next night
        that lands, so a reader has to know its presence is CURRENT rather than historical.
        Without that sentence a note from weeks ago reads like the latest run's."""
        self.assertIn("stable filename", self._body(runner.ERROR))


class RefuseWrongTreeTest(unittest.TestCase):

    def test_running_as_root_is_refused_and_names_the_fix(self):
        """A daemon runs as root unless its plist says otherwise, and `UserName` cannot
        live in the tracked template (the renderer accepts four tokens and refuses any
        other), so it is injected by the installer. A plist made some other way runs as
        root and would fill the checkout with root-owned files."""
        with mock.patch.object(runner.os, "geteuid", return_value=0):
            why = runner.refuse_wrong_tree()
        self.assertIsNotNone(why)
        self.assertIn("root", why)
        self.assertIn("install-gate-inputs.sh", why)

    def test_a_linked_worktree_is_refused_because_the_ledger_write_would_fail(self):
        with mock.patch.object(runner.os, "geteuid", return_value=1000), \
             mock.patch.object(runner, "git") as fake_git:
            fake_git.side_effect = lambda args, cwd: subprocess.CompletedProcess(
                args, 0, stdout=("/repo/.git\n" if "--git-common-dir" in args
                                 else "/repo/.git/worktrees/poga-3\n"), stderr="")
            why = runner.refuse_wrong_tree()
        self.assertIsNotNone(why)
        self.assertIn("linked worktree", why)

    def test_the_main_checkout_is_accepted(self):
        with mock.patch.object(runner.os, "geteuid", return_value=1000), \
             mock.patch.object(runner, "git") as fake_git:
            fake_git.side_effect = lambda args, cwd: subprocess.CompletedProcess(
                args, 0, stdout="/repo/.git\n", stderr="")
            self.assertIsNone(runner.refuse_wrong_tree())

    def test_a_sealed_deploy_tree_is_refused(self):
        """The inverse drift. The three Runner units belong IN the sealed tree; this one
        must never be there — a deploy tree is a detached clone at a release tag, so
        deriving there measures a tree nobody develops in."""
        with tempfile.TemporaryDirectory() as tmp:
            sealed = pathlib.Path(tmp) / "deploy" / "federation"
            sealed.mkdir(parents=True)
            with mock.patch.object(runner.os, "geteuid", return_value=1000), \
                 mock.patch.dict(runner.os.environ, {"POGA_DEPLOY_ROOT": str(sealed.parent)}), \
                 mock.patch.object(runner, "ROOT", sealed), \
                 mock.patch.object(runner, "git") as fake_git:
                fake_git.side_effect = lambda args, cwd: subprocess.CompletedProcess(
                    args, 0, stdout=f"{sealed}/.git\n", stderr="")
                why = runner.refuse_wrong_tree()
        self.assertIsNotNone(why)
        self.assertIn("sealed", why)


@unittest.skipIf(GIT is None, "git not on PATH")
class StageRecordOnlyTest(unittest.TestCase):
    """The land's first move is `git add -A`, so whatever the derive left behind rides into
    the trunk under the runner's own commit message. The deriver leaks `.gate-inputs.raw`
    when a probe dies — it is not in `.gitignore` — and the eight gate commands it measures
    run for real against the lane. This is the guard that keeps the landed diff to one file.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        # Otherwise the fixture inherits `commit.gpgsign` from whoever runs the suite and
        # breaks on a machine configured to sign.
        self._git("config", "commit.gpgsign", "false")
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(GREEN_RECORD) + "\n")
        (self.repo / "other.txt").write_text("committed\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")

    def tearDown(self):
        # Background `git maintenance` can be mid-write in a fixture repo at teardown, so a
        # strict rmtree raises FileNotFoundError on a lock file that is already gone.
        self._tmp.cleanup() if pathlib.Path(self._tmp.name).exists() else None

    def _git(self, *args):
        return subprocess.run([GIT, *args], cwd=str(self.repo),
                              capture_output=True, text=True, check=False)

    def _staged(self):
        return self._git("diff", "--cached", "--name-only").stdout.split()

    def test_the_record_is_staged_and_nothing_else_is(self):
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(RED_RECORD) + "\n")
        (self.repo / ".gate-inputs.raw").write_text("leaked probe handoff\n")
        (self.repo / "other.txt").write_text("touched by a gate command\n")
        runner.stage_record_only(self.repo)
        self.assertEqual(self._staged(), [runner.RECORD_NAME])

    def test_the_leaked_raw_file_is_gone_rather_than_landed(self):
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(RED_RECORD) + "\n")
        (self.repo / ".gate-inputs.raw").write_text("leaked probe handoff\n")
        runner.stage_record_only(self.repo)
        self.assertFalse((self.repo / ".gate-inputs.raw").exists())

    def test_an_unrelated_tracked_edit_is_restored_not_committed(self):
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(RED_RECORD) + "\n")
        (self.repo / "other.txt").write_text("touched by a gate command\n")
        runner.stage_record_only(self.repo)
        self.assertEqual((self.repo / "other.txt").read_text(), "committed\n")

    def test_what_was_discarded_is_reported_and_excludes_the_record(self):
        """The runner logs this line. A night that silently threw away a file somebody
        cared about should at least say which one."""
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(RED_RECORD) + "\n")
        (self.repo / ".gate-inputs.raw").write_text("leaked\n")
        discarded = runner.stage_record_only(self.repo)
        self.assertIn(".gate-inputs.raw", discarded)
        self.assertNotIn(runner.RECORD_NAME, discarded)

    def test_a_clean_derive_leaves_only_the_record_and_reports_nothing_discarded(self):
        (self.repo / runner.RECORD_NAME).write_text(json.dumps(RED_RECORD) + "\n")
        self.assertEqual(runner.stage_record_only(self.repo), "")
        self.assertEqual(self._staged(), [runner.RECORD_NAME])


class ARedNightNamesItsTestsTest(unittest.TestCase):
    """WI-0431. The 2026-09-25 red night could not be diagnosed from its own record: the
    status file, the comms note and the log all said "20 failures, 94 errors" and named
    none of them."""

    def test_the_probe_records_failing_ids_from_a_real_result(self):
        from curate import gate_inputs

        class T(unittest.TestCase):
            def test_fails(self):
                self.fail("x")

            def test_errors(self):
                raise RuntimeError("x")

            def test_passes(self):
                pass

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(T).run(result)
        ids = gate_inputs.failing_ids(result)
        self.assertEqual([i.rsplit(".", 1)[1] for i in ids["failure_ids"]], ["test_fails"])
        self.assertEqual([i.rsplit(".", 1)[1] for i in ids["error_ids"]], ["test_errors"])

    def test_names_come_from_the_record_and_the_total_from_its_counts(self):
        record = dict(RED_RECORD, verdict=dict(RED_RECORD["verdict"],
                                               failure_ids=["m.A.test_a", "m.A.test_b"],
                                               error_ids=["n.B.test_c"]))
        total, names = runner.failing_tests(record)
        self.assertEqual(total, 3)
        self.assertEqual(names, ["FAIL m.A.test_a", "FAIL m.A.test_b", "ERROR n.B.test_c"])

    def test_a_record_without_names_still_carries_the_count(self):
        """A pre-WI-0431 record has counts and no ids. That is "3 failed, names not
        recorded", never "nothing failed"."""
        total, names = runner.failing_tests(RED_RECORD)
        self.assertEqual((total, names), (3, []))
        self.assertIn("no names", runner.failing_block(total, names, 25))

    def test_no_record_says_nothing(self):
        self.assertEqual(runner.failing_tests(None), (None, []))
        self.assertEqual(runner.failing_block(None, [], 25), "")

    def test_the_note_is_capped_and_says_how_many_it_left_out(self):
        names = [f"FAIL m.A.test_{i}" for i in range(30)]
        block = runner.failing_block(114, names, runner.NOTE_FAILING_CAP)
        self.assertIn("Failing tests (114)", block)
        self.assertIn("`FAIL m.A.test_24`", block)
        self.assertNotIn("test_25`", block)
        self.assertIn("89 more", block)

    def test_the_comms_note_carries_the_names(self):
        block = runner.failing_block(1, ["ERROR m.A.test_x"], runner.NOTE_FAILING_CAP)
        body = runner.blocked_note_body(runner.RED, "suite RED", "2026-09-26T03:12Z",
                                        "devbox", "the log", block)
        self.assertIn("ERROR m.A.test_x", body)

    def test_the_probe_records_why_each_one_failed(self):
        """WI-0432. The 2026-09-27 night named all 72 and still needed a reproduction to
        learn they shared one sentence. The reason is the exception's first line."""
        from curate import gate_inputs

        class T(unittest.TestCase):
            def test_fails(self):
                self.assertEqual(1, 2)

            def test_errors(self):
                raise RuntimeError("REFUSED to run under com.x\nsecond line")

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(T).run(result)
        why = {i.rsplit(".", 1)[1]: r
               for i, r in gate_inputs.failing_ids(result)["failing_reasons"].items()}
        self.assertEqual(why["test_fails"], "AssertionError: 1 != 2")
        self.assertEqual(why["test_errors"], "RuntimeError: REFUSED to run under com.x")

    def test_the_reason_is_the_exception_line_not_the_last_line_and_is_capped(self):
        from curate import gate_inputs
        tb = ("Traceback (most recent call last):\n"
              '  File "deploy/runner.py", line 3610, in refuse_unsealed_process_root\n'
              "    raise DeployError(\n"
              "runner.DeployError: REFUSED to run under com.federation.gate-inputs: "
              + "x" * 400 + "\n\ntail of a multi-line message\n")
        why = gate_inputs.failure_reason(tb)
        self.assertTrue(why.startswith("runner.DeployError: REFUSED to run under"), why)
        self.assertEqual(len(why), gate_inputs.FAILING_REASON_CAP)
        self.assertEqual(gate_inputs.failure_reason("no frames here\n"), "no frames here")

    def test_the_note_and_the_status_carry_the_reasons_beside_the_names(self):
        record = dict(RED_RECORD, verdict=dict(
            RED_RECORD["verdict"], failure_ids=["m.A.test_a"], error_ids=["n.B.test_c"],
            failing_reasons={"m.A.test_a": "AssertionError: 1 != 2",
                             "n.B.test_c": "DeployError: REFUSED"}))
        total, names = runner.failing_tests(record)
        why = runner.failing_reasons(record)
        self.assertEqual(why, {"FAIL m.A.test_a": "AssertionError: 1 != 2",
                               "ERROR n.B.test_c": "DeployError: REFUSED"})
        block = runner.failing_block(total, names, runner.NOTE_FAILING_CAP, why)
        self.assertIn("`ERROR n.B.test_c` — DeployError: REFUSED", block)
        import inspect
        self.assertIn('"failing_reasons":', inspect.getsource(runner.main))

    def test_a_record_without_reasons_still_names_every_test(self):
        self.assertEqual(runner.failing_reasons(RED_RECORD), {})
        block = runner.failing_block(1, ["ERROR m.A.test_x"], 25, {})
        self.assertIn("- `ERROR m.A.test_x`\n", block)

    def test_the_status_file_carries_the_names_and_the_count(self):
        """`main()` is the only writer; its payload must carry both fields the start
        line reads (sessionlib/land.py `_red_night_detail`)."""
        import inspect
        source = inspect.getsource(runner.main)
        self.assertIn('"failing_total": failing_total', source)
        self.assertIn('"failing": failing_names[:STATUS_FAILING_CAP]', source)


class TheUnitAndItsRunnerAgreeTest(unittest.TestCase):
    """Cheap structural guards. Each one is a rename that would otherwise be found at
    02:30 by launchd failing to spawn, with nothing but an empty log to say so."""

    LABEL = "com.federation.gate-inputs"
    TEMPLATE = ROOT / "deploy" / f"{LABEL}.plist.template"

    def test_the_template_points_at_a_runner_that_exists(self):
        import plistlib
        declared = plistlib.loads(self.TEMPLATE.read_bytes())
        program = declared["ProgramArguments"]
        target = next(a for a in program if a.endswith(".py"))
        self.assertTrue((ROOT / target.replace("__FED_ROOT__/", "")).exists(),
                        f"{target} is named by the unit and is not in the repo")

    def test_the_label_is_declared_in_the_deploy_contract(self):
        contract = json.loads((ROOT / "deploy" / "deploy.json").read_text())
        self.assertIn(self.LABEL, contract["units"])

    def test_the_installer_installs_the_label_it_is_named_for(self):
        text = (ROOT / "deploy" / "install-gate-inputs.sh").read_text()
        self.assertIn(f'LABEL="{self.LABEL}"', text)

    def test_the_unit_is_a_daemon_because_its_machine_has_no_graphical_login(self):
        """Not style. An agent cannot load on a host with no graphical login, which is
        the host this unit targets — measured: `launchctl managername` reports Background,
        the `gui/<uid>` domain is unreachable and bootstrapping into `user/<uid>` fails
        outright. If somebody converts this to a LaunchAgent for symmetry
        with its three siblings, it installs cleanly and never runs again."""
        installer = (ROOT / "deploy" / "install-gate-inputs.sh").read_text()
        self.assertIn("/Library/LaunchDaemons", installer)
        self.assertIn("UserName", installer,
                      "a daemon with no UserName runs as root in the operator's checkout")

    def test_the_runner_refuses_to_run_as_root(self):
        """The other half of the UserName injection: if it is ever missed, this is what
        turns a silent root derive into a refusal."""
        source = (ROOT / "curate" / "gate-inputs-runner.py").read_text()
        self.assertIn("os.geteuid() == 0", source)


if __name__ == "__main__":
    unittest.main()
