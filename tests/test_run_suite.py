"""The sharded suite runner is a partition and a verdict, and both are testable (WI-0296).

`curate/run_suite.py` exists to make the land gate's suite finish in a minute instead
of five. Its whole claim is that it changes only the SPEED — same tests, same verdict,
same exit code — and a claim like that is worth exactly as much as its detector
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

There are two ways it could be wrong, and each is worse than being slow:

1. **It could lose tests.** A shard plan that misses a file, double-counts one, or
   drops a class in the packing produces a green run over a subset — the gate would
   pass a change nothing checked. The tests here pin the partition property directly:
   the union of a file's parts is the file, and no test is in two parts.

2. **It could lose the verdict.** `session._test_verdict` reads `^Ran N tests in` and
   classifies the line after it; anything it does not recognise is "did not report".
   So the summary block is not cosmetic — it is the interface. The tests here render
   every verdict shape unittest can emit and require the exit code to match.

The fail-closed path is tested too, because it is the one that only ever runs on a bad
day: a worker that dies without writing a result must become an ERROR, never a silently
smaller run.

stdlib unittest: python3 -m unittest discover -s tests
"""

import io
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
import run_suite  # noqa: E402


def _sample():
    """Carriers for the verdict shapes — a TestCase whose methods deliberately fail,
    error, skip, and unexpectedly succeed. Every outcome unittest can report is here,
    including the two that are easy to forget: an expected failure (green) and an
    unexpected success (RED, and the one people get backwards).

    BUILT INSIDE A FUNCTION, deliberately. `loadTestsFromModule` collects every
    module-level `TestCase` subclass by type, not by name — so a class defined at
    module scope here, under any name, would be discovered and its deliberate
    failures counted as this file's own. These are instruments, not tests; keeping
    the class out of the module namespace is what makes that structural rather than
    a naming convention someone has to know about."""
    cls = getattr(_sample, "_cls", None)
    if cls is not None:
        return cls

    class Sample(unittest.TestCase):
        def test_pass(self):
            pass

        def test_fail(self):
            self.assertEqual(1, 2, "deliberate")

        def test_error(self):
            raise RuntimeError("deliberate")

        @unittest.skip("deliberate")
        def test_skip(self):
            pass

        @unittest.expectedFailure
        def test_xfail(self):
            self.fail("deliberate")

        @unittest.expectedFailure
        def test_xpass(self):
            pass

    _sample._cls = Sample
    return Sample


def _stdlib_names_unexpected_successes():
    """Does the RUNNING stdlib's `printErrors` name each unexpected success?

    A probe, not a version comparison ([`declare-what-a-check-assumes`]
    (../habits/master.md#declare-what-a-check-assumes)). `TextTestResult.printErrors`
    grew a block that prints `separator1` then one `UNEXPECTED SUCCESS: <desc>` line
    per test; older stdlibs print the ERROR and FAIL lists only and never say which
    test unexpectedly succeeded. Which of those the interpreter under us does is an
    observable fact — so observe it, by running one synthetic unexpected success
    through a live runner and reading what came out. `sys.version_info >= (3, 11)`
    would encode a guess about a stdlib we are not running and would go stale silently
    (a backport, a vendored runner, a future rewording) in the direction that matters:
    a check that stops checking.

    Computed once; the answer cannot change inside a process."""
    cached = getattr(_stdlib_names_unexpected_successes, "_answer", None)
    if cached is None:
        buf = io.StringIO()
        unittest.TextTestRunner(stream=buf, verbosity=0).run(
            unittest.TestSuite([_sample()("test_xpass")]))
        cached = "UNEXPECTED SUCCESS:" in buf.getvalue()
        _stdlib_names_unexpected_successes._answer = cached
    return cached


def _shard_record(methods):
    """One worker's payload for `methods`, built the way `_worker` builds it."""
    suite = unittest.TestSuite(_sample()(m) for m in methods)
    res = unittest.TextTestRunner(stream=run_suite._NullStream(), verbosity=0).run(suite)
    return {
        "module": "synthetic.py",
        "part": 0,
        "parts": 1,
        "testsRun": res.testsRun,
        "failures": [[run_suite._describe(t), tb] for t, tb in res.failures],
        "errors": [[run_suite._describe(t), tb] for t, tb in res.errors],
        "skipped": [[run_suite._describe(t), r] for t, r in res.skipped],
        "expectedFailures": [[run_suite._describe(t), tb] for t, tb in res.expectedFailures],
        "unexpectedSuccesses": [run_suite._describe(t) for t in res.unexpectedSuccesses],
    }


def _summary_of(records):
    buf = io.StringIO()
    ok = run_suite._summarize(records, 1.0, buf)
    text = buf.getvalue()
    verdict = text.splitlines()[-1]
    return ok, text, verdict


class TheVerdictIsUnittestsTest(unittest.TestCase):
    """The summary block is the gate's interface, not a log line."""

    def test_the_ran_line_is_the_shape_the_verdict_parser_anchors_on(self):
        _, text, _ = _summary_of([_shard_record(["test_pass", "test_pass"])])
        self.assertIn("Ran 2 tests in 1.000s", text)

    def test_a_single_test_is_not_pluralised(self):
        """`Ran 1 test`, not `Ran 1 tests` — unittest's own wording, and the regex
        that reads it (`Ran (\\d+) tests?`) tolerates both, so only a comparison
        against unittest can catch this drifting."""
        _, text, _ = _summary_of([_shard_record(["test_pass"])])
        self.assertIn("Ran 1 test in", text)
        self.assertNotIn("Ran 1 tests in", text)

    def test_green_says_ok(self):
        ok, _, verdict = _summary_of([_shard_record(["test_pass"])])
        self.assertTrue(ok)
        self.assertEqual(verdict, "OK")

    def test_a_skip_is_still_green_and_says_so(self):
        ok, _, verdict = _summary_of([_shard_record(["test_pass", "test_skip"])])
        self.assertTrue(ok)
        self.assertEqual(verdict, "OK (skipped=1)")

    def test_an_expected_failure_is_green(self):
        ok, _, verdict = _summary_of([_shard_record(["test_pass", "test_xfail"])])
        self.assertTrue(ok)
        self.assertEqual(verdict, "OK (expected failures=1)")

    def test_an_unexpected_success_is_red(self):
        """The one that reads like good news. unittest fails the run on it; so must
        this, or a test that started passing when it was declared broken lands."""
        ok, _, verdict = _summary_of([_shard_record(["test_pass", "test_xpass"])])
        self.assertFalse(ok)
        self.assertEqual(verdict, "FAILED (unexpected successes=1)")

    def test_failures_and_errors_are_counted_separately(self):
        ok, _, verdict = _summary_of([_shard_record(["test_fail", "test_error"])])
        self.assertFalse(ok)
        self.assertEqual(verdict, "FAILED (failures=1, errors=1)")

    def test_counts_are_summed_across_shards(self):
        """The point of the whole exercise: two shards, one verdict."""
        ok, text, verdict = _summary_of([_shard_record(["test_pass", "test_fail"]),
                                         _shard_record(["test_pass", "test_error"])])
        self.assertFalse(ok)
        self.assertIn("Ran 4 tests in", text)
        self.assertEqual(verdict, "FAILED (failures=1, errors=1)")

    def test_the_failure_report_keeps_unittests_headers(self):
        _, text, _ = _summary_of([_shard_record(["test_fail"])])
        self.assertIn(run_suite.SEP1, text)
        self.assertIn("FAIL: test_fail", text)
        self.assertIn("deliberate", text)


class TheSummaryMatchesUnittestByteForByteTest(unittest.TestCase):
    """Not "looks like unittest's" — IS unittest's, compared against a live runner.

    Asserting the strings by hand (above) says what we believe the shapes are; this
    says we believed correctly. Both are wanted: the hand-written ones fail with a
    readable message, this one fails when the stdlib moves under us."""

    SCENARIOS = (
        ("all green", ["test_pass"]),
        ("one failure", ["test_pass", "test_fail"]),
        ("failure and error", ["test_fail", "test_error"]),
        ("green with skip", ["test_pass", "test_skip"]),
        ("expected failure", ["test_pass", "test_xfail"]),
        ("unexpected success", ["test_pass", "test_xpass"]),
        ("every outcome at once", ["test_pass", "test_fail", "test_error",
                                   "test_skip", "test_xfail", "test_xpass"]),
    )

    @staticmethod
    def _normalise(text):
        """Only the elapsed seconds may differ — nothing else is allowed to."""
        import re
        return re.sub(r"in \d+\.\d+s", "in T", text)

    @staticmethod
    def _with_unexpected_success_block(text, res):
        """unittest's `text`, plus the UNEXPECTED SUCCESS block `res` earned.

        Only used on a stdlib whose `printErrors` does not print one. The block is
        built from THIS scenario's actual unexpected successes, described by
        unittest's own `getDescription` and ruled by unittest's own `separator1` —
        nothing about the expectation is borrowed from `run_suite`, so a wrong
        description, a wrong separator, a missing line, an extra line, or a block in
        the wrong place all still fail. Everything outside the spliced block stays a
        byte-for-byte comparison.

        Documented position, copied from the `printErrors` that does print it: after
        the ERROR and FAIL lists, and immediately before the final `separator2` that
        precedes the `Ran N tests in ...` line.
        """
        lines = ["UNEXPECTED SUCCESS: %s\n" % res.getDescription(t)
                 for t in res.unexpectedSuccesses]
        if not lines:
            return text
        block = res.separator1 + "\n" + "".join(lines)
        marker = res.separator2 + "\nRan "
        head, sep, tail = text.rpartition(marker)
        # Not a convenience: if the anchor is missing or ambiguous, unittest's summary
        # has moved somewhere this splice cannot describe, and the honest outcome is a
        # loud failure rather than a comparison against a guess.
        assert sep and text.count(marker) == 1, (
            "cannot locate the single final separator2 before the Ran line in "
            "unittest's summary: %r" % (text,))
        return head + block + sep + tail

    def test_every_verdict_shape_renders_identically(self):
        # DELIBERATE, DOCUMENTED SUPERSET. `run_suite._summarize` always names each
        # unexpected success (commit e208db7a); stdlibs whose `printErrors` omits that
        # block therefore print strictly less than we do, for those scenarios only.
        # We keep our block and widen the EXPECTATION rather than the other way round:
        # the block is what the gate receipt needs to say WHICH test unexpectedly
        # succeeded, and dropping or version-gating it in `run_suite` would re-open the
        # "the receipt lost the identity" defect on the interpreter the gate actually
        # runs under. A test that is a superset on some stdlibs is a cost we can see
        # here; a receipt that silently forgets a name is a cost nobody sees at all.
        names_xpass = _stdlib_names_unexpected_successes()
        for name, methods in self.SCENARIOS:
            with self.subTest(name):
                buf = io.StringIO()
                res = unittest.TextTestRunner(stream=buf, verbosity=0).run(
                    unittest.TestSuite(_sample()(m) for m in methods))
                ours_ok, ours_text, _ = _summary_of([_shard_record(methods)])
                theirs = buf.getvalue()
                if not names_xpass:
                    theirs = self._with_unexpected_success_block(theirs, res)
                self.assertEqual(self._normalise(theirs),
                                 self._normalise(ours_text),
                                 f"the {name} summary has drifted from unittest's")
                self.assertEqual(ours_ok, res.wasSuccessful(),
                                 f"the {name} exit code has drifted from unittest's")


class ThePlanIsAPartitionTest(unittest.TestCase):
    """Every test in exactly one shard — the property a speedup must not trade away."""

    def test_every_test_file_appears_in_the_plan(self):
        plan = run_suite._shards(run_suite.DEFAULT_PATTERN)
        planned = {name for name, _, _ in plan}
        import fnmatch
        on_disk = {p.name for p in run_suite.TESTS.glob("*.py")
                   if fnmatch.fnmatch(p.name, run_suite.DEFAULT_PATTERN)}
        self.assertEqual(planned, on_disk,
                         "the shard plan and the tests/ directory disagree about which "
                         "files exist — a file in neither set runs nowhere.")

    def test_each_file_contributes_exactly_its_parts_once(self):
        plan = run_suite._shards(run_suite.DEFAULT_PATTERN)
        seen = {}
        for name, part, parts in plan:
            seen.setdefault(name, []).append(part)
            self.assertEqual(parts, len(
                [p for p in plan if p[0] == name]),
                f"{name} declares {parts} parts but the plan carries a different number")
        for name, parts in seen.items():
            self.assertEqual(sorted(parts), list(range(len(parts))),
                             f"{name}'s parts are not 0..n-1 exactly once: {sorted(parts)}")

    def test_the_plan_is_ordered_longest_pole_first(self):
        """A big file drawn last sets the floor on wall time, so size descends."""
        plan = run_suite._shards(run_suite.DEFAULT_PATTERN)
        sizes = [(run_suite.TESTS / name).stat().st_size for name, part, _ in plan
                 if part == 0]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_a_files_parts_cover_every_class_exactly_once(self):
        """The packing itself, over a synthetic class list. Union == whole, and no
        overlap — checked for every part count from 1 to more parts than classes,
        because the degenerate 'more bins than work' case is where an off-by-one in
        the greedy loop would hide."""
        groups = [["t%d.%d" % (g, i) for i in range(n)]
                  for g, n in enumerate([7, 1, 5, 3, 3, 9, 2])]
        whole = [t for g in groups for t in g]
        for parts in range(1, 11):
            with self.subTest(parts=parts):
                got = []
                for part in range(parts):
                    for group in run_suite._bin_for_part(groups, part, parts):
                        got.extend(group)
                self.assertEqual(sorted(got), sorted(whole),
                                 "the parts are not a partition of the file")
                self.assertEqual(len(got), len(set(got)), "a test landed in two parts")

    def test_a_class_is_never_split_across_parts(self):
        """`setUpClass` state is shared by a class's methods; two processes running
        half a class each would each build their own fixture under it."""
        groups = [["t%d.%d" % (g, i) for i in range(n)]
                  for g, n in enumerate([7, 1, 5, 3, 3, 9, 2])]
        for parts in (2, 3, 4, 8):
            with self.subTest(parts=parts):
                for part in range(parts):
                    for group in run_suite._bin_for_part(groups, part, parts):
                        self.assertIn(group, groups,
                                      "a part received a fragment of a class, not a class")

    def test_the_packing_is_deterministic(self):
        """Each worker derives the partition itself rather than being told it, so two
        callers must compute the same answer or a class runs twice — or never."""
        groups = [["t%d.%d" % (g, i) for i in range(n)]
                  for g, n in enumerate([4, 4, 9, 1, 6])]
        for part in range(3):
            self.assertEqual(run_suite._bin_for_part(groups, part, 3),
                             run_suite._bin_for_part(groups, part, 3))


class ADeadWorkerFailsClosedTest(unittest.TestCase):
    """The bad-day path. A shard that produced no result is not a shard that passed."""

    def test_a_lost_shard_is_an_error_carrying_its_output(self):
        rec = run_suite._lost_shard("test_x.py[2/4]", -9, RuntimeError("no file"),
                                    "Killed: 9")
        self.assertEqual(rec["testsRun"], 0)
        self.assertEqual(len(rec["errors"]), 1)
        self.assertIn("test_x.py[2/4]", rec["errors"][0][0])
        self.assertIn("exited -9", rec["errors"][0][1])
        self.assertIn("Killed: 9", rec["errors"][0][1],
                      "the worker's output tail is the only evidence of why it died")

    def test_a_lost_shard_turns_the_whole_run_red(self):
        ok, _, verdict = _summary_of([
            _shard_record(["test_pass"]),
            run_suite._lost_shard("test_x.py", -9, RuntimeError("no file"), ""),
        ])
        self.assertFalse(ok)
        self.assertEqual(verdict, "FAILED (errors=1)")


class TheGateCommandStillPointsHereTest(unittest.TestCase):
    """The runner is only a speedup if the gate actually calls it.

    A file like this one can be perfect and irrelevant: nothing else in the suite
    would notice if `session.config.json`'s gate list quietly went back to a bare
    `unittest discover`, and the symptom — lands taking five minutes again — is one
    nobody files a bug about. So the wiring is asserted where the wiring lives."""

    def test_the_configured_gate_runs_the_sharded_runner(self):
        import json
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        gate = cfg.get("gate") or []
        self.assertTrue(
            any("curate/run_suite.py" in cmd for cmd in gate),
            "session.config.json's gate no longer runs curate/run_suite.py, so the "
            "land gate is back on the single-core suite:\n  " + "\n  ".join(gate))

    def test_the_gate_never_runs_the_suite_twice(self):
        """Both commands would be green, and the land would take six minutes instead
        of five — a regression that hides behind a passing gate."""
        import json
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        gate = cfg.get("gate") or []
        bare = [c for c in gate if "unittest" in c and "run_suite" not in c]
        self.assertEqual(bare, [], "the serial suite is still in the gate list alongside "
                                   "the sharded runner; the gate would run both")


if __name__ == "__main__":
    unittest.main()


class ConcurrentLanesShareTheMachineTest(unittest.TestCase):
    """`-j` defaulted to every core, per lane, and lanes run in parallel (WI-0353 made
    the suite fast enough that everything reaches for it).

    OBSERVED: a few dispatched lanes each took the whole box to a very high load. The
    damage was not only slowness — FL7 asserts a 0.6s land-lock hold, went red under the
    load, and an `integrate` refused reporting a conflict between two machines' work that
    did not exist. A default that is right alone and ruinous in parallel is a defect of
    the default.
    """

    def setUp(self):
        self._env = os.environ.get(run_suite.JOBS_ENV)
        os.environ.pop(run_suite.JOBS_ENV, None)

    def tearDown(self):
        os.environ.pop(run_suite.JOBS_ENV, None)
        if self._env is not None:
            os.environ[run_suite.JOBS_ENV] = self._env

    def test_the_width_is_divided_by_the_suites_already_running(self):
        with mock.patch.object(os, "cpu_count", return_value=12), \
             mock.patch.object(run_suite, "_concurrent_suite_parents", return_value=3):
            self.assertEqual(run_suite.default_jobs(), 4)

    def test_one_suite_alone_still_gets_the_whole_machine(self):
        """The fix must not tax the common case. A lone suite counts only itself."""
        with mock.patch.object(os, "cpu_count", return_value=12), \
             mock.patch.object(run_suite, "_concurrent_suite_parents", return_value=1):
            self.assertEqual(run_suite.default_jobs(), 12)

    def test_more_suites_than_cores_still_leaves_one_worker(self):
        with mock.patch.object(os, "cpu_count", return_value=4), \
             mock.patch.object(run_suite, "_concurrent_suite_parents", return_value=9):
            self.assertEqual(run_suite.default_jobs(), 1)

    def test_an_unreadable_process_table_does_not_throttle_the_suite(self):
        """THE DIRECTION THAT MATTERS. If the probe cannot answer, the old full-width
        default stands. Failing the other way would turn an unreadable `ps` into a 5x
        slower suite with no error to explain it — a performance cliff with no message,
        which is the shape nobody diagnoses."""
        with mock.patch.object(run_suite.subprocess, "run",
                               side_effect=OSError("no ps here")):
            self.assertEqual(run_suite._concurrent_suite_parents(), 1)
        with mock.patch.object(os, "cpu_count", return_value=8), \
             mock.patch.object(run_suite.subprocess, "run",
                               side_effect=OSError("no ps here")):
            self.assertEqual(run_suite.default_jobs(), 8)

    def test_the_pools_own_workers_are_not_counted_as_peers(self):
        """`--worker` lines are this pool's children. Counting them would divide the
        width by the width, collapsing every run to a single worker."""
        table = ("python3 curate/run_suite.py\n"
                 "python3 curate/run_suite.py --worker /tmp/a.json tests/test_x.py 0 1\n"
                 "python3 curate/run_suite.py --worker /tmp/b.json tests/test_y.py 0 1\n")
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout=table, stderr="")
        with mock.patch.object(run_suite.subprocess, "run", return_value=done):
            self.assertEqual(run_suite._concurrent_suite_parents(), 1)

    def test_an_explicit_override_wins_and_a_bad_one_is_ignored(self):
        with mock.patch.object(os, "cpu_count", return_value=12), \
             mock.patch.object(run_suite, "_concurrent_suite_parents", return_value=4):
            os.environ[run_suite.JOBS_ENV] = "10"
            self.assertEqual(run_suite.default_jobs(), 10)
            os.environ[run_suite.JOBS_ENV] = "not-a-number"
            self.assertEqual(run_suite.default_jobs(), 3,
                             "an unparseable override falls back to the shared default, "
                             "never to full width")
