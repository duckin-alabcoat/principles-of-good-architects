"""WI-0462 — a serial `poga test` files a verdict when the serial suite IS the gate's.

THE DEFECT. ADR-0148 D2 lets a land skip the suite when `poga test` already filed a
green verdict for the lane's code tree. Only the SHARDED run filed one. A new project's
gate is the serial `unittest discover -s tests` (it has no `curate/run_suite.py`), so it
never filed a verdict, and every land printed "verdict: none — running the suite once".
The walkthrough (docs/first-task.md) shows the verdict line, so the docs and the code
disagreed.

THE RULE. A serial run files a verdict only when the gate's suite is exactly that one
serial command. Everything else answers a different question and files nothing: a `-p`
subset, a `--serial` run standing in for a sharded gate, a gate whose unittest command
carries other flags, a gate with two suite commands.
"""

import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from test_test_verb_runs_the_gates_suite import (  # noqa: E402
    SERIAL_DEFAULT, SHARDED, _args, _drive)

TREE = "a" * 40
REPO = pathlib.Path(__file__).resolve().parent.parent


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def drive(self, gate, args=None, **kw):
        """`cmd_test` with the tree key and the verdict store stubbed, so nothing is
        written to the real common dir. Returns (spy, record_verdict mock)."""
        rec = mock.MagicMock(return_value={})
        with mock.patch.object(session, "_code_tree", return_value=TREE), \
             mock.patch.object(session, "record_verdict", rec), \
             _drive(list(gate), self.tmp, args or _args(),
                    runner_present=False, **kw) as spy:
            return spy, rec


class ASerialGateFilesAVerdictTest(_Base):

    def test_a_full_serial_run_of_a_serial_gate_files_a_green(self):
        spy, rec = self.drive(SERIAL_DEFAULT)
        rec.assert_called_once()
        tree, green, source = rec.call_args[0][:3]
        self.assertEqual((tree, green, source), (TREE, True, "poga-test"))
        self.assertIn(f"verdict: filed PASSED for this tree ({TREE[:12]})", spy.out)
        self.assertIn("test: PASSED [serial]", spy.out)

    def test_a_string_spelled_serial_gate_files_too(self):
        """`python3` in the config resolves to the running interpreter, as the gate's
        own spawn does, so a string spelling is the same command."""
        spy, rec = self.drive(["python3 -m unittest discover -s tests"])
        rec.assert_called_once()

    def test_the_run_is_told_it_is_the_gate(self):
        """A verdict standing in for the gate's run must be that run (ADR-0119 D4)."""
        spy, _ = self.drive(SERIAL_DEFAULT)
        env = spy.kwargs.get("env") or {}
        self.assertEqual(env.get(session.GATE_ENV_VAR), "1")

    def test_a_red_serial_run_files_a_red(self):
        spy, rec = self.drive(SERIAL_DEFAULT,
                              verdict=("failed", "3 test(s), FAILED (failures=1)"), rc=1)
        rec.assert_called_once()
        self.assertFalse(rec.call_args[0][1])
        self.assertIn("verdict: filed FAILED", spy.out)

    def test_verbose_still_files(self):
        """`-v` changes the output, not which tests run."""
        _, rec = self.drive(SERIAL_DEFAULT, args=_args(verbose=True))
        rec.assert_called_once()

    def test_a_tree_that_moved_during_the_run_files_nothing(self):
        rec = mock.MagicMock(return_value={})
        trees = iter([TREE, "b" * 40])
        with mock.patch.object(session, "_code_tree", side_effect=lambda _c: next(trees)), \
             mock.patch.object(session, "record_verdict", rec), \
             _drive(SERIAL_DEFAULT, self.tmp, _args(), runner_present=False) as spy:
            pass
        rec.assert_not_called()
        self.assertIn("verdict: NOT filed", spy.out)


class OtherRunsFileNothingTest(_Base):

    def test_a_pattern_subset_files_nothing(self):
        spy, rec = self.drive(SERIAL_DEFAULT, args=_args(pattern="test_greet.py"))
        rec.assert_not_called()
        self.assertNotIn("verdict:", spy.out)

    def test_serial_standing_in_for_a_sharded_gate_files_nothing(self):
        _, rec = self.drive([SHARDED], args=_args(serial=True))
        rec.assert_not_called()

    def test_a_gate_whose_unittest_command_differs_files_nothing(self):
        _, rec = self.drive(["python3 -m unittest discover -s tests -t ."])
        rec.assert_not_called()

    def test_a_gate_with_two_suite_commands_files_nothing(self):
        _, rec = self.drive(SERIAL_DEFAULT + [["python3", "-m", "unittest", "other"]])
        rec.assert_not_called()

    def test_a_gate_with_no_suite_files_nothing(self):
        _, rec = self.drive(["python3 session.py wi-check"])
        rec.assert_not_called()


class TheWalkthroughMatchesTheOutputTest(_Base):
    """docs/first-task.md shows the two lines a new project's `poga test` prints."""

    def test_both_lines_in_the_doc_are_printed_in_that_order(self):
        doc = (REPO / "docs" / "first-task.md").read_text(encoding="utf-8")
        self.assertIn("verdict: filed PASSED for this tree (", doc)
        self.assertIn("test: PASSED [serial]", doc)
        spy, _ = self.drive(SERIAL_DEFAULT)
        v = spy.out.index("verdict: filed PASSED for this tree (")
        t = spy.out.index("test: PASSED [serial]")
        self.assertLess(v, t)
        self.assertIn("a land of this tree runs no suite (ADR-0148).", spy.out)


if __name__ == "__main__":
    unittest.main()
