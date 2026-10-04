"""`session.py test` — the verdict verb (WI-0139).

Three sessions (~129, ~134, ~148) reported this very suite green off a reading that
had never contained a verdict. Every one of them produced a CONFIDENT WRONG ANSWER
rather than an error, which is why this is a guard and not a note to self.

These tests pin the three states, not two. `did-not-report` is the whole point: a run
that produced no summary must be reportable as neither passed nor failed
(`declare-what-a-check-assumes` turned on our own test harness). Each case below is a
shape that actually bit, or the near-miss that the classifier has to survive:

  1. A green run is `passed` — including the `OK (skipped=N)` form, which a bare
     `^OK$` grep MISSED in session ~148, sending the author to read the line by hand.
  2. A red run is `failed`.
  3. No `Ran N tests` line at all is `did-not-report` — the ~129 shape, where the
     summary went to a stream nobody captured.
  4. Fixture output that merely CONTAINS `OK` is not a verdict — the ~134 shape,
     where a grep matched the substrate's own banner `OK — all 1 index(es)...`
     printed from a temp repo.
  5. A nested run's summary does not win over the suite's own.
  6. When the exit code and the summary disagree, neither is reportable — two
     witnesses, and a disagreement is information, not something to collapse.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def _err(body):
    """A stderr log shaped like unittest's own."""
    return "....\n" + "-" * 70 + "\n" + body + "\n"


class TestVerdictTest(unittest.TestCase):

    def test_green_run_is_passed(self):
        state, detail = session._test_verdict(_err("Ran 1791 tests in 92.4s\n\nOK"), 0)
        self.assertEqual(state, "passed")
        self.assertIn("1791", detail)

    def test_green_with_skips_is_passed(self):
        # Session ~148: `grep -E "^OK$"` did not match this, so a genuinely green run
        # read as unverifiable and had to be checked by hand.
        state, _ = session._test_verdict(_err("Ran 1791 tests in 92.4s\n\nOK (skipped=2)"), 0)
        self.assertEqual(state, "passed")

    def test_green_with_expected_failures_is_passed(self):
        state, _ = session._test_verdict(
            _err("Ran 12 tests in 0.1s\n\nOK (expected failures=1)"), 0)
        self.assertEqual(state, "passed")

    def test_red_run_is_failed(self):
        state, detail = session._test_verdict(
            _err("Ran 1791 tests in 92.4s\n\nFAILED (failures=1)"), 1)
        self.assertEqual(state, "failed")
        self.assertIn("FAILED", detail)

    def test_no_summary_is_did_not_report(self):
        # ~129: unittest writes its summary to stderr; a stdout-only capture produced
        # a log that looked like the suite had never run — and was reported as green.
        state, detail = session._test_verdict("some output, no summary at all\n", 0)
        self.assertEqual(state, "did-not-report")
        self.assertIn("never reported", detail)

    def test_fixture_banner_is_not_a_verdict(self):
        # ~134: the fixture prints the substrate's OWN banners from a temp repo, so a
        # bare-OK match reads a fixture line as the suite's verdict. Even sitting in
        # the exact position a verdict occupies, this must not classify.
        state, _ = session._test_verdict(
            _err("Ran 3 tests in 0.1s\n\nOK — all 1 index(es) match their directory layout"), 0)
        self.assertEqual(state, "did-not-report")

    def test_fixture_ok_without_any_run_line_is_not_passed(self):
        state, _ = session._test_verdict("OK — all 1 index(es) match\nlanded: lane -> main\n", 0)
        self.assertEqual(state, "did-not-report")

    def test_last_summary_wins_over_a_nested_one(self):
        nested = _err("Ran 2 tests in 0.0s\n\nOK")
        real = _err("Ran 1791 tests in 92.4s\n\nFAILED (errors=3)")
        state, detail = session._test_verdict(nested + real, 1)
        self.assertEqual(state, "failed")
        self.assertIn("1791", detail)

    def test_exit_code_disagreeing_with_a_pass_is_not_reportable(self):
        # ~148's shape, generalized: the exit code read was `tail`'s, not the suite's.
        # Here the summary says OK and the process says 1 — the honest answer is that
        # we cannot tell, not a coin flip between two confident verdicts.
        state, detail = session._test_verdict(_err("Ran 10 tests in 0.1s\n\nOK"), 1)
        self.assertEqual(state, "did-not-report")
        self.assertIn("disagree", detail)

    def test_exit_code_disagreeing_with_a_failure_is_not_reportable(self):
        state, _ = session._test_verdict(_err("Ran 10 tests in 0.1s\n\nFAILED (failures=1)"), 0)
        self.assertEqual(state, "did-not-report")

    def test_unrecognized_summary_line_is_did_not_report(self):
        state, detail = session._test_verdict(_err("Ran 5 tests in 0.1s\n\nWAT"), 0)
        self.assertEqual(state, "did-not-report")
        self.assertIn("neither", detail)


if __name__ == "__main__":
    unittest.main()
