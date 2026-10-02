"""WI-0334 — a failing gate threw away the failing test's name.

THE MEASUREMENT, from OPS-0007's first run on 2026-09-11. `_run_gate` kept
`(stderr or stdout).strip().splitlines()[-4:]` as the receipt for a failed command.
`curate/run_suite.py` writes, as its LAST FOUR LINES: a separator, `Ran N tests in
Xs`, a blank line, and `FAILED (failures=1)`. The `FAIL:` and `ERROR:` blocks that
name which test broke are printed EARLIER, by `_print_error_list`. Four lines kept,
four lines of verdict — so the retained receipt was structurally guaranteed to carry
the verdict and never the identity. Not a near-miss a longer tail fixes; the identity
lives in the body, so the body is where it has to be found.

THE LOSS IS TOTAL, which is why it is worth a fix rather than a note. The gate runs in
a throwaway worktree that `_gate_commit` removes in a `finally`, so the output the
receipt dropped is not on disk anywhere. Nor is re-running it by hand the same
question: reproducing a gate run takes a detached worktree at the candidate commit,
`POGA_GATE=1` and a closed stdin. The operator was sent to do several minutes of work
to recover a fact the process had already read and discarded.

WHY THE EXISTING TEST DID NOT CATCH IT.
`tests/test_gate_runs_the_store_validator.py` asserts that a failing check's output
reaches the receipt — with a fixture that emits ONE line. A one-line output cannot
express a truncation, so the assertion passed for every four-line-tail implementation,
including the broken one. That test is kept (it owns a different property: the
stdout FALLBACK is load-bearing for `wi-check`) and this file is the widening the item
asked for.

  A. THE IDENTITY SURVIVES, END TO END. Real `curate/run_suite.py`, real failing
     tests, real multi-line output, through the real `_run_gate`. Asserted against the
     receipt, not against a helper.
  B. THE PREMISE IS ASSERTED, NOT ASSUMED. The same run's last four lines are checked
     to NOT contain the name. If run_suite's summary block ever changes shape so the
     tail does carry identity, A starts passing for a reason that has nothing to do
     with this fix, and B is what goes red instead of nothing
     ([`declare-what-a-check-assumes`]).
  C. WHAT IT LEFT OUT IS DECLARED. The two losses — tests it could not name, body
     lines it did not show — are counted separately and never double-counted, and "the
     command wrote nothing" renders as itself rather than as an empty receipt.
  D. A ONE-LINE CHECK IS UNTOUCHED. Every other gate command is a `--check` script
     whose diagnosis is its last line or two. Their receipts must be byte-identical to
     the pre-WI-0334 ones, or this fix bought identity at the cost of a regression
     everywhere else.
  E. THE PREFLIGHT BANNER SHOWS THE FAILURE. `_pf_gate` rendered `lines[:6]` of the
     SAME report — its preamble, since `_run_gate` returns at the first failure and
     everything interesting is therefore at the end. Two truncations of one string,
     from opposite ends, and only one of them is the one the item was filed against.
     Fixing `_run_gate` alone would have produced a receipt that names the failing test
     and a SessionStart banner that still never shows it
     ([`retire-the-class-not-the-instance`]) — so this property exists to keep the
     class closed rather than the instance.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import session  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The failing fixture the end-to-end property drives through the real runner. Two red
#: tests, not one: a receipt that names "a" failing test and a receipt that names THE
#: failing tests are different promises, and only the second is useful when the suite
#: comes back with more than one.
FIXTURE = '''\
import unittest


class TheRedOnesTest(unittest.TestCase):

    def test_a_deliberately_failing_assertion(self):
        self.assertEqual("left", "right")

    def test_a_deliberately_raised_error(self):
        raise RuntimeError("this one errors rather than fails")

    def test_a_passing_one_for_contrast(self):
        self.assertTrue(True)
'''


class TheReceiptNamesTheFailingTestTest(unittest.TestCase):
    """Properties A and B — the end-to-end one the item's acceptance names."""

    def setUp(self):
        # A miniature repo: the REAL runner, and a tests/ directory of our own. The
        # runner resolves its suite from its own `__file__`, so a copy placed here
        # discovers these three tests and nothing else — running the federation's own
        # suite from inside the federation's own suite is not a test, it is a fork bomb.
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="wi0334-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "curate").mkdir()
        shutil.copy(REPO / "curate" / "run_suite.py", self.tmp / "curate" / "run_suite.py")
        # `run_suite.py` imports it at module scope (WI-0371), so a miniature repo
        # without it does not fail this test's assertion — it fails to START, and the
        # receipt then names an ImportError instead of the test it was asked about.
        shutil.copy(REPO / "poga_evidence.py", self.tmp / "poga_evidence.py")
        # Same reason, ADR-0148 D3: the runner arms the live-store guard at import.
        shutil.copy(REPO / "curate" / "store_guard.py", self.tmp / "curate" / "store_guard.py")
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_red.py").write_text(FIXTURE, encoding="utf-8")

        saved = getattr(session, "CFG")
        self.addCleanup(setattr, session, "CFG", saved)
        # `-j 1`: one worker is enough for three tests and keeps the shard scheduling
        # out of a property that is about output, not about concurrency.
        session.CFG = {"gate": [["python3", "curate/run_suite.py", "-j", "1"]]}

    def _run(self):
        ok, report = session._run_gate(cwd=self.tmp)
        self.assertFalse(ok, f"a suite with two red tests must fail the gate:\n{report}")
        return report

    def test_the_receipt_names_both_failing_tests(self):
        report = self._run()
        for name in ("test_a_deliberately_failing_assertion",
                     "test_a_deliberately_raised_error"):
            self.assertIn(
                name, report,
                f"the gate's receipt must name {name} — it is the whole reason the "
                f"operator reads it:\n{report}")

    def test_the_receipt_still_carries_the_verdict(self):
        # The fix adds to the tail; it must not replace it. `FAILED (...)` and the
        # counts are what say how big the failure is.
        report = self._run()
        self.assertIn("FAILED (", report, f"the verdict must survive too:\n{report}")
        self.assertIn("Ran 3 tests", report, f"the run size must survive too:\n{report}")

    def test_the_last_four_lines_alone_would_not_have_named_them(self):
        # Property B. This is the defect, asserted as a live fact about the runner's
        # output rather than as a story about it. If this goes red, the premise under
        # the test above has moved and that test is no longer exercising anything.
        import subprocess
        r = subprocess.run([sys.executable, "curate/run_suite.py", "-j", "1"],
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, f"fixture must fail:\n{r.stderr}")
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-4:])
        self.assertNotIn(
            "test_a_deliberately_failing_assertion", tail,
            "the premise of WI-0334 no longer holds — the runner's last four lines now "
            f"carry the identity, so the receipt test above proves nothing:\n{tail}")


class TheReceiptDeclaresWhatItLeftOutTest(unittest.TestCase):
    """Properties C and D — the unit half, on `_gate_failure_receipt` directly."""

    def test_no_output_at_all_says_so(self):
        # It used to render as a `FAIL (exit 1)` line with nothing under it, which
        # reads as a broken display rather than as the command's real behaviour.
        self.assertEqual(session._gate_failure_receipt(""),
                         ["(the command failed without writing to stdout or stderr)"])
        self.assertEqual(session._gate_failure_receipt("   \n  \n"),
                         ["(the command failed without writing to stdout or stderr)"])

    def test_a_one_line_check_is_passed_through_unchanged(self):
        # Property D: `wi-check`, `distill --check`, `standardize --check` — every gate
        # command that is not the suite. Nothing matches, nothing is elided, and the
        # receipt is what it was before WI-0334.
        self.assertEqual(session._gate_failure_receipt("WI-0002 is missing"),
                         ["WI-0002 is missing"])
        four = "a\nb\nc\nd"
        self.assertEqual(session._gate_failure_receipt(four), ["a", "b", "c", "d"])

    def test_elided_body_lines_are_counted(self):
        out = session._gate_failure_receipt(
            "\n".join(["FAIL: test_x (m.C)", "trace 1", "trace 2", "trace 3",
                       "----", "Ran 9 tests in 1.000s", "", "FAILED (failures=1)"]))
        self.assertEqual(out[0], "FAIL: test_x (m.C)")
        self.assertIn("3 earlier line(s) not shown", out[1])
        self.assertEqual(out[-1], "FAILED (failures=1)")

    def test_the_named_cap_holds_and_counts_the_overflow(self):
        body = [f"FAIL: test_{i} (m.C)" for i in range(15)]
        out = session._gate_failure_receipt(
            "\n".join(body + ["----", "Ran 15 tests in 1.000s", "",
                              "FAILED (failures=15)"]))
        named = [ln for ln in out if ln.startswith("FAIL: ")]
        self.assertEqual(len(named), session.GATE_RECEIPT_MAX_NAMED,
                         "the cap is what keeps a 300-failure run out of the receipt")
        self.assertIn("and 5 more failing test(s) not named here", "\n".join(out))

    def test_the_two_notes_never_double_count_the_same_line(self):
        # A capped name is reported once, as a name it could not print — never a second
        # time as an anonymous elided line, or a receipt says "5 more tests" and "5
        # lines not shown" about the same five lines and reads as ten losses.
        body = [f"FAIL: test_{i} (m.C)" for i in range(15)]
        out = session._gate_failure_receipt(
            "\n".join(body + ["----", "Ran 15 tests in 1.000s", "",
                              "FAILED (failures=15)"]))
        self.assertNotIn("earlier line(s) not shown", "\n".join(out))

    def test_an_unexpected_success_is_named_too(self):
        # `run_suite._summarize` prints these under their own rule, and the comment
        # there says why: the verdict alone never said WHICH test. Same defect, same
        # receipt — so the same prefix list has to cover it.
        out = session._gate_failure_receipt(
            "\n".join(["UNEXPECTED SUCCESS: test_y (m.C)", "noise", "noise", "----",
                       "Ran 2 tests in 1.000s", "", "FAILED (unexpected successes=1)"]))
        self.assertIn("UNEXPECTED SUCCESS: test_y (m.C)", out)


class ThePreflightBannerShowsTheFailureTest(unittest.TestCase):
    """Property E — the other end of the same report.

    A FIXTURE GATE, not the real suite: `_pf_gate` calls `_run_gate()` with no `cwd`,
    so it runs whatever `CFG["gate"]` holds in THIS checkout. The property under test is
    where the banner slices the report, which a synthetic report exercises exactly as
    well as a real one and in milliseconds rather than minutes. What the report's own
    shape must be is asserted end-to-end above, against the real runner."""

    #: Five green checks before the red one — so the report's first six lines (the
    #: `gated with:` line plus five `gate:    ok` lines) are entirely preamble, which is
    #: precisely what the old slice kept. Fewer than five and the bug hides.
    OK = ["python3", "-c", "pass"]
    RED = ["python3", "-c",
           "import sys; sys.stderr.write("
           "'FAIL: test_the_one_that_broke (m.C)\\n'"
           "'  File x, line 1\\n'"
           "'-' * 70 + '\\nRan 4 tests in 0.010s\\n\\nFAILED (failures=1)\\n'); "
           "sys.exit(1)"]

    def setUp(self):
        saved = getattr(session, "CFG")
        self.addCleanup(setattr, session, "CFG", saved)
        session.CFG = {"gate": [self.OK] * 5 + [self.RED]}

    def test_the_banner_names_the_failing_test(self):
        res = session._pf_gate()
        self.assertEqual(res.verdict, session.PREFLIGHT_FAIL)
        self.assertIn(
            "test_the_one_that_broke", res.detail,
            "the banner a session start prints must name what broke — it is the only "
            f"gate output an operator sees before deciding to work here:\n{res.detail}")

    def test_the_banner_starts_at_the_failure_not_at_the_preamble(self):
        res = session._pf_gate()
        body = [l.strip() for l in res.detail.splitlines()[1:] if l.strip()]
        self.assertTrue(
            body and body[0].startswith(session.GATE_FAIL_MARKER),
            f"the banner must open on the failed command, not on the checks that "
            f"passed before it:\n{res.detail}")
        self.assertNotIn("gate:    ok", res.detail,
                         f"a green check is not news on a RED banner:\n{res.detail}")

    def test_the_first_six_lines_alone_would_not_have_named_it(self):
        # The premise, asserted rather than described — same discipline as property B.
        _ok, report = session._run_gate()
        lines = [l for l in report.splitlines() if l.strip()]
        self.assertNotIn(
            "test_the_one_that_broke", "\n".join(lines[:6]),
            "the old head-slice now reaches the failure, so the property above is no "
            "longer exercising anything — re-check the fixture's preamble length.")

    def test_an_unrecognised_report_is_printed_whole_and_says_so(self):
        # `declare-what-a-check-assumes`: the anchor is a string this module also
        # writes, and a shape with no anchor must not silently fall back to showing the
        # preamble again — which is the bug — but say that it could not find it.
        with mock.patch.object(session, "_run_gate",
                               return_value=(False, "something\nelse entirely")):
            res = session._pf_gate()
        self.assertIn("no recognised failure line", res.detail)
        self.assertIn("else entirely", res.detail)


if __name__ == "__main__":
    unittest.main()
