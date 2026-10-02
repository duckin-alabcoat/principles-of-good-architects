"""WI-0349: a clock on ready-to-land through on-trunk.

WHAT WAS MISSING, measured rather than asserted. ADR-0124 D4 gave every land a receipt,
and the receipt carried the lock hold, the four inside-lock stages and the validation —
but every one of those clocks is scoped to a SINGLE ATTEMPT of the retry loop, and all
of them start after the lander has already reaped dead journals and written its close
commit. Read over the 24 landed receipts on one machine on 2026-09-12, the shape was:

    lock_seconds      median  1.24s   worst   3.27s   (FL7's number — green)
    validate_seconds  median 66.44s   worst 135.18s
    queue_seconds     median  2.37s   worst  53.68s
    the three summed  median 69.99s   worst 164.16s   — 24 of 24 over a minute

So FL7 was green on every one of them while every one of them took over a minute. That
is not a contradiction: FL7 budgets the LOCK, because the lock is what other lanes queue
behind. Nothing measured what the land cost the person who ran it.

THE ONE-MINUTE TARGET IS A GOAL, NEVER A GATE. Nothing in this file asserts that a land
is fast, and nothing in the code it tests refuses a slow one. A land that skips its
recompile to get under the number is not fast, it is incomplete, and the debt lands in
someone else's session. The tests that matter here are the ones proving the number is
COMPLETE — that no wait falls outside it — and that a receipt which cannot be measured
says so rather than reading as a pass.
"""
import contextlib
import io
import json
import pathlib
import time
import unittest
from unittest import mock

from test_land_is_a_merge import GateNeutralBase
import session


class TheClockCoversTheWholeLandTest(GateNeutralBase):
    """The boundary is the operator's ruling: time spent waiting to land is part of the land, so
    a long wait before a short merge is a long land. Any wait — queue, freeze, retry — is inside the number."""

    def setUp(self):
        super().setUp()
        # The clock is process-scoped by design (see `mark_ready_to_land`). Each test
        # here is a different land, so each starts from nothing.
        session._clear_ready_to_land()
        self.addCleanup(session._clear_ready_to_land)

    def test_a_land_records_an_end_to_end_duration(self):
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[-1]
        self.assertEqual(rec["outcome"], "landed")
        self.assertIsInstance(rec["total_seconds"], (int, float))
        self.assertEqual(rec["total_from"], "ready",
                         "the lander must have marked the clock, not fallen back")

    def test_the_total_covers_more_than_the_per_attempt_clocks(self):
        """The load-bearing test in this file.

        The three per-attempt clocks were all that existed before, and the pre-loop work
        — the journal reap and the close commit — fell outside every one of them. If the
        total is merely their sum, this change measured nothing new."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[-1]
        # The final attempt is everything the old clocks could see. `queue_seconds`
        # already contains `lock_seconds`, so the attempt's real span is queue+validate.
        attempt = rec["queue_seconds"] + rec["validate_seconds"]
        self.assertGreater(rec["total_seconds"], attempt,
                           "the end-to-end total is no larger than the final attempt — "
                           "the pre-loop work (journal reap, close commit) is still in "
                           "no number at all")
        self.assertGreater(rec["phases"]["unattributed"], 0.0,
                           "the phase that was invisible before WI-0349 is still empty")

    def test_the_phases_are_an_exact_partition_of_the_total(self):
        """A breakdown whose parts do not sum to the whole sends its next reader to
        re-derive the difference by arithmetic and guess what it means.

        This is the test that caught the real defect while this was being built: the
        first draft listed queue/validate/lock as siblings, and `queue_seconds` already
        CONTAINS the hold — so the breakdown double-counted the lock and could exceed
        the total it was decomposing."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[-1]
        ph = rec["phases"]
        self.assertAlmostEqual(sum(ph.values()), rec["total_seconds"], places=2,
                               msg=f"phases do not partition the total: {rec}")
        self.assertLessEqual(ph["lock"], rec["lock_seconds"] + 0.001)

    def test_a_wait_before_the_lock_is_inside_the_number(self):
        """A real queue, injected where the real one happens.

        This is the property the whole item turns on. A land that waits on the land gate
        must carry that wait in its own number — parking the cost just outside the metric
        is the exact failure this measurement exists to correct."""
        self._journal_only_lane()
        real = session._serialized_advance

        def slow(*a, **kw):
            time.sleep(0.4)                 # the queue, made visible
            return real(*a, **kw)

        with mock.patch.object(session, "_serialized_advance", side_effect=slow):
            outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[-1]
        self.assertGreaterEqual(rec["total_seconds"], 0.4,
                                "a wait before the ref moved fell outside the land's "
                                "own number")

    def test_the_breakdown_carries_the_gate_rather_than_discarding_it(self):
        """Acceptance, in as many words: the gate's time is present in the breakdown.

        ADR-0148 split it in two: `validate` is SUITE time only (zero on the new path —
        the land runs no suite) and `checks` is everything else `_land_gate` ran. The
        fixture gate (`PASS_GATE`) has no suite in it, so every command is a cheap check
        and all of its time must land in `checks` — not vanish, and not be filed as
        validation it never was. Still an exact partition of the total."""
        self._code_carrying_lane()
        outcome, gate_calls, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertTrue(gate_calls, "control: the gate must actually have run")
        rec = session.land_receipts()[-1]
        ph = rec["phases"]
        self.assertIn("validate", ph)
        self.assertIn("checks", ph)
        self.assertEqual(ph["validate"], 0.0, "no suite ran, yet validate is non-zero")
        self.assertGreater(ph["checks"], 0.0,
                           "the gate ran and its time is still not in the breakdown")
        self.assertEqual(rec["checks_seconds"], ph["checks"])
        self.assertAlmostEqual(sum(ph.values()), rec["total_seconds"], places=2,
                               msg=f"phases do not partition the total: {rec}")

    def test_the_inside_lock_stages_keep_their_meaning(self):
        """`stages` is the INSIDE-lock breakdown and FL7 reads it to explain a long hold.

        Filing the gate in there would report validation under a lock ADR-0124
        deliberately runs it outside of — a number that is not wrong so much as
        differently-scoped, which is worse."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[-1]
        self.assertIn("merge", rec["stages"])
        self.assertNotIn("validate", rec["stages"])
        self.assertNotIn("queue", rec["stages"])


class TheLandSaysItsOwnNumberTest(GateNeutralBase):
    """`a-close-is-the-banner-not-the-sentence`. A number filed where nobody looks is a
    number the operator has to take on trust."""

    def setUp(self):
        super().setUp()
        session._clear_ready_to_land()
        self.addCleanup(session._clear_ready_to_land)

    def test_the_land_prints_the_end_to_end_duration(self):
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertIn("land took:", out)
        self.assertIn("end-to-end", out)
        self.assertIn("ready-to-land", out)

    def test_a_slow_land_says_the_goal_is_not_a_gate(self):
        """The wording is the deliverable. A bare "over the goal" on a land that was
        never at risk of being refused trains the reader to treat a report as a failure —
        and the next person to 'fix' that reads stage-shedding as the remedy, which is
        the proposal operator withdrew."""
        rec = {"outcome": "landed", "total_seconds": session.LAND_DURATION_GOAL_SECONDS + 5,
               "total_from": "ready",
               "phases": {"validate": 60.0, "queue": 4.0, "lock": 1.0,
                          "unattributed": 0.0}}
        line = session._land_end_to_end_line(rec)
        self.assertIn("over the 60s goal", line)
        self.assertIn("not a gate", line)
        self.assertIn("nothing", line.lower())

    def test_a_fast_land_does_not_mention_the_goal_at_all(self):
        rec = {"outcome": "landed", "total_seconds": 12.0, "total_from": "ready",
               "phases": {"validate": 10.0, "queue": 1.0, "lock": 1.0,
                          "unattributed": 0.0}}
        self.assertNotIn("goal", session._land_end_to_end_line(rec))

    def test_an_unmarked_clock_says_which_number_this_is(self):
        """`declare-what-a-check-assumes`: the fallback UNDERSTATES the land, so it must
        never be presentable as the end-to-end measurement."""
        rec = {"outcome": "landed", "total_seconds": 3.0, "total_from": "attempt",
               "phases": {"validate": 2.0, "queue": 1.0, "lock": 0.0,
                          "unattributed": 0.0}}
        line = session._land_end_to_end_line(rec)
        self.assertIn("final attempt only", line)

    def test_a_moved_attempt_prints_no_duration(self):
        """Its clock is still running; printing one would report a fraction of the land
        as the land."""
        rec = {"outcome": "moved", "total_seconds": 3.0, "total_from": "ready",
               "phases": {}}
        self.assertEqual(session._land_end_to_end_line(rec), "")

    def test_the_hold_line_still_says_what_it_always_said(self):
        """FL7's operator surface must not have been displaced by the new one."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertIn("stage lock: held", out)
        self.assertIn("validated", out)


class TheDistributionTest(unittest.TestCase):
    """operator needs to know whether landing is USUALLY over a minute, not whether it was
    once. One slow land is noise. ADR-0148 D7 moved the verdict from the median to the
    p90 (`LAND_DURATION_RED_QUANTILE`): a median hid that one land in ten took eleven
    minutes, and the p90 is what a person landing several times a day actually meets."""

    def setUp(self):
        self.tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self.path = self.tmp / "receipts.jsonl"
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _write(self, totals, outcome="landed", key="total_seconds"):
        lines = []
        for t in totals:
            rec = {"at_epoch": time.time(), "outcome": outcome}
            if t is not None:
                rec[key] = t
            lines.append(json.dumps(rec))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_the_p90_is_the_verdict_not_the_worst_case(self):
        """The opposite call from the lock budget (FL8), deliberately. Nineteen quick
        lands and one very slow one is a good week with an outlier — the p90 is still
        quick, and red here would mean nothing."""
        self.assertEqual(session.LAND_DURATION_RED_QUANTILE, 0.9)
        self._write([10.0] * 19 + [600.0])
        stats = session.land_duration_stats(path=self.path)
        self.assertEqual(stats["n"], 20)
        self.assertFalse(stats["red"], "one slow land must not turn the window red")
        self.assertEqual(stats["worst"], 600.0, "the outlier is still reported")
        self.assertEqual(stats["over_goal"], 1)

    def test_a_median_over_the_goal_is_red(self):
        self._write([70.0] * 11 + [10.0] * 9)
        stats = session.land_duration_stats(path=self.path)
        self.assertTrue(stats["red"])

    def test_one_land_in_ten_slow_is_red_though_the_median_is_fine(self):
        """ADR-0148 D7's reason for the move, as a fixture: the median reads 10s and
        says nothing is wrong; the p90 reads 600s and is the land people remember."""
        self._write([10.0] * 17 + [600.0] * 3)
        stats = session.land_duration_stats(path=self.path)
        self.assertEqual(stats["median"], 10.0)
        self.assertEqual(stats["p90"], 600.0)
        self.assertTrue(stats["red"], "a slow tenth must turn the window red at p90")

    def test_receipts_from_before_the_clock_are_unmeasured_never_zero(self):
        """The flattering-measurement failure this item exists to correct. Old receipts
        carry no total; counting them as zero-second lands would report the worst window
        on record as a perfect score."""
        self._write([None, None, None])
        stats = session.land_duration_stats(path=self.path)
        self.assertEqual(stats["n"], 0)
        self.assertEqual(stats["unmeasured"], 3)
        self.assertNotIn("median", stats)

    def test_a_mixed_window_counts_only_what_was_measured(self):
        self._write([None, 10.0, None, 20.0])
        stats = session.land_duration_stats(path=self.path)
        self.assertEqual((stats["n"], stats["unmeasured"]), (2, 2))
        self.assertIn("2 older land(s) unmeasured",
                      session.land_duration_line(stats))

    def test_a_failed_land_is_not_in_the_distribution(self):
        """A `moved` attempt's receipt is an intermediate record, not a land."""
        self._write([5.0, 5.0], outcome="moved")
        self.assertEqual(session.land_duration_stats(path=self.path)["n"], 0)

    def test_an_empty_window_is_not_a_pass(self):
        self.path.write_text("", encoding="utf-8")
        stats = session.land_duration_stats(path=self.path)
        self.assertEqual(stats["n"], 0)
        self.assertIn("no land measured", session.land_duration_line(stats))

    def test_every_reported_quantile_is_a_land_that_happened(self):
        """Nearest-rank, so no number on the board is an interpolation between two lands
        rather than one of them."""
        self._write([1.0, 2.0, 3.0, 4.0])
        stats = session.land_duration_stats(path=self.path)
        for key in ("median", "p90", "worst", "best"):
            self.assertIn(stats[key], [1.0, 2.0, 3.0, 4.0], key)


def _stats(n, p90, median=None):
    """One class's `land_duration_stats` result, shaped as the real one."""
    if not n:
        return {"n": 0, "unmeasured": 0}
    median = p90 if median is None else median
    return {"n": n, "unmeasured": 0, "median": median, "p90": p90, "worst": p90,
            "best": median, "over_goal": 0, "goal": 60.0, "red": False}


def _split(code, bookkeeping):
    """A `land_duration_stats` stand-in answering per `diff_class` (ADR-0148 D7): the
    surfaces read code and bookkeeping separately, so a single `return_value` cannot
    tell the two apart and would let a surface that mixed them pass."""
    def fake(days=0.0, path=None, diff_class=None):
        return {"code": code, "bookkeeping": bookkeeping}.get(diff_class, code)
    return fake


class TheSurfacesTest(unittest.TestCase):
    """The acceptance asks for a distribution readable "without running a command nobody
    runs" — which `curate/finish_line.py` is today, in as many words (WI-0285)."""

    def test_the_start_banner_carries_a_landing_line(self):
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(3, 90.0, 70.0), _stats(4, 12.0))):
            line = session.landing_line()
        self.assertTrue(line.startswith("landing:"), line)
        self.assertIn("OVER GOAL", line)
        self.assertIn("code p90 90.0s over 3 (goal 60s, OVER)", line)
        self.assertIn("bookkeeping p90 12.0s over 4 (goal 30s)", line)
        self.assertIn("· 7d", line)
        self.assertNotIn("median", line, "FL7 reads the p90 now (ADR-0148 D7)")

    def test_a_slow_bookkeeping_class_alone_turns_the_line_over_goal(self):
        """Each class against its OWN target: 40s is fine for code and over for a land
        that runs no suite at all."""
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(5, 40.0), _stats(5, 40.0))):
            line = session.landing_line()
        self.assertIn("OVER GOAL", line)
        self.assertIn("code p90 40.0s over 5 (goal 60s)", line)
        self.assertIn("bookkeeping p90 40.0s over 5 (goal 30s, OVER)", line)

    def test_both_within_goal_says_so(self):
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(5, 50.0), _stats(5, 20.0))):
            line = session.landing_line()
        self.assertIn("within goal", line)
        self.assertNotIn("OVER", line)

    def test_the_banner_line_renders_in_every_state(self):
        """Unconditional, all states — a line that appeared only on a slow window is a
        line nobody can tell from a line that failed to run."""
        for stats in ({"n": 0, "unmeasured": 0},
                      {"n": 0, "unmeasured": 9},
                      {"n": 2, "unmeasured": 0, "median": 5.0, "p90": 6.0, "worst": 6.0,
                       "best": 5.0, "over_goal": 0, "goal": 60.0, "red": False}):
            with mock.patch.object(session, "land_duration_stats",
                                   side_effect=_split(stats, stats)):
                line = session.landing_line()
            self.assertTrue(line.startswith("landing:"), line)
            self.assertEqual(len(line.splitlines()), 1,
                             f"the orientation block is one line per fact: {line}")
            # Every state has to say something a reader can act on — "landing:" with
            # nothing after it is the line that cannot be told from one that failed.
            self.assertGreater(len(line), len("landing: "), line)

    def test_cmd_start_actually_emits_it(self):
        """The wiring, not just the formatter — the WI-0333 precedent's own test shape."""
        import inspect
        self.assertIn("landing_line()", inspect.getsource(session.cmd_start))


class TheLineReachesTheBannerTest(GateNeutralBase):
    """THE WIRING, end to end, and it gets its own class for the reason
    `test_the_lines_actually_reach_the_start_banner` gives in test_worktree_lane.py: the
    tests above call the composer directly, so every one of them would pass just as
    happily if the call had never been added to `cmd_start`. A capability nobody calls
    reads as absent — which is precisely the acceptance clause here, since the whole
    point is a number that does NOT require running a command."""

    def test_the_landing_line_is_in_the_start_banner(self):
        import argparse
        self._point_session_at_lane()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(4, 88.0, 70.0), _stats(0, 0))):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                session.cmd_start(argparse.Namespace(dry_run=True))
        banner = out.getvalue()
        self.assertIn("landing:", banner,
                      "the start banner does not carry the landing line — the number is "
                      "back to being a command nobody runs")
        self.assertIn("code p90 88.0s", banner)

    def test_a_broken_receipt_log_never_breaks_a_session_start(self):
        """Fail-open, like every other orientation line. A start that dies because a
        metrics file is unreadable has traded a report for the session itself."""
        import argparse
        self._point_session_at_lane()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=RuntimeError("boom")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                session.cmd_start(argparse.Namespace(dry_run=True))   # must not raise
        self.assertNotIn("landing:", out.getvalue())

    def _finish_line(self):
        import sys
        sys.path.insert(0, str(pathlib.Path(session.__file__).resolve().parent))
        from curate import finish_line
        return finish_line

    def test_fl7_reads_the_distribution_and_fl8_the_lock(self):
        """Both rows exist because they answer different questions, and the day they
        were first measured together they disagreed: median hold 1.2s, median land
        70.0s. ADR-0148 D7 swapped the numbering — FL7 is now the land duration (the
        number the person landing waits for), FL8 the lock hold."""
        finish_line = self._finish_line()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(5, 80.0, 70.0), _stats(0, 0))):
            verdict, evidence = finish_line.probe_land_duration()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("code p90 80.0s", evidence)
        self.assertIn("OVER", evidence)
        self.assertIn("FL7", finish_line.probe_land_duration.__doc__)
        self.assertIn("FL8", finish_line.probe_land_lock_hold.__doc__)

    def test_fl7_is_unknown_when_no_receipt_is_measured(self):
        """An empty window — nobody landed, or every receipt predates the clock — is
        UNKNOWN, never a PASS (`declare-what-a-check-assumes`)."""
        finish_line = self._finish_line()
        with mock.patch.object(session, "land_duration_stats",
                               return_value={"n": 0, "unmeasured": 7}):
            verdict, evidence = finish_line.probe_land_duration()
        self.assertEqual(verdict, "UNKNOWN")
        self.assertIn("no measured land in 7d", evidence)

    def test_fl7_fails_on_a_slow_bookkeeping_class_alone(self):
        """The split, on the board: code p90 under its 60s, bookkeeping p90 over its
        30s — red, and the evidence says which class."""
        finish_line = self._finish_line()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(6, 45.0), _stats(9, 35.0))):
            verdict, evidence = finish_line.probe_land_duration()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("bookkeeping p90 35.0s", evidence)
        self.assertIn("goal 30s — OVER", evidence)
        self.assertNotIn("goal 60s — OVER", evidence)

    def test_fl7_passes_when_both_classes_are_within_goal(self):
        finish_line = self._finish_line()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(6, 45.0), _stats(9, 20.0))):
            verdict, evidence = finish_line.probe_land_duration()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertNotIn("OVER", evidence)

    def test_fl7_reports_an_empty_class_rather_than_hiding_it(self):
        finish_line = self._finish_line()
        with mock.patch.object(session, "land_duration_stats",
                               side_effect=_split(_stats(0, 0), _stats(9, 20.0))):
            verdict, evidence = finish_line.probe_land_duration()
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn("code: none", evidence)

    def test_fl8_is_on_the_board(self):
        import sys
        sys.path.insert(0, str(pathlib.Path(session.__file__).resolve().parent))
        from curate import finish_line
        # The subject is the WIRING — that `board()` renders an FL8 row — not what any
        # probe measures. Running the real probes read the live `sessions/journal/`,
        # `work-items/` and `session-handoff.md` (1,500+ files), which ADR-0148 D3's
        # store guard fails: a bookkeeping-only land skips the suite, so no test may
        # depend on the store as it sits in the checkout. Every probe is stubbed by NAME
        # PATTERN rather than one by one, so a probe added or renamed later (FL7 is being
        # redefined as this is written) is stubbed too instead of reaching the store.
        stubs = {n: (lambda *a, **k: ("UNKNOWN", "stubbed"))
                 for n in dir(finish_line) if n.startswith("probe_")
                 and callable(getattr(finish_line, n))}
        if "probe_tokens" in stubs:          # the one probe that returns a triple
            stubs["probe_tokens"] = lambda *a, **k: ("UNKNOWN", "stubbed", None)
        with contextlib.ExitStack() as stack:
            for n, f in stubs.items():
                stack.enter_context(mock.patch.object(finish_line, n, f))
            caps, _ = finish_line.board(run_tests=False)
        self.assertTrue(any("FL8" in name for name, _, _ in caps),
                        "the probe exists but nothing renders it")


class ReceiptDiffClassTest(unittest.TestCase):
    """ADR-0148 D7: FL7 splits code from bookkeeping, and the split has to be honest for
    receipts written before the field existed — or the seven-day window is empty (or
    all "code") for its first week."""

    def setUp(self):
        import shutil
        import subprocess
        import tempfile
        self.git = shutil.which("git")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self._subprocess = subprocess
        self._g("init", "-q", "-b", "main")
        for k, v in (("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            self._g("config", k, v)
        self._write("session.py", "x = 1\n")
        self.base = self._commit("base")
        saved = session.ROOT
        session.ROOT = self.repo
        self.addCleanup(setattr, session, "ROOT", saved)
        session._RECEIPT_CLASS_CACHE.clear()
        self.addCleanup(session._RECEIPT_CLASS_CACHE.clear)

    def _g(self, *args):
        return self._subprocess.run([self.git, "-C", str(self.repo), *args], check=True,
                                    capture_output=True, text=True).stdout.strip()

    def _write(self, rel, body):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def _commit(self, msg):
        self._g("add", "-A")
        self._g("commit", "-qm", msg)
        return self._g("rev-parse", "HEAD")

    def test_an_explicit_field_wins(self):
        # parent..tip here is a CODE diff; the field must still decide.
        self._write("session.py", "x = 2\n")
        tip = self._commit("code")
        rec = {"diff_class": "bookkeeping", "parent": self.base[:12], "tip": tip[:12]}
        self.assertEqual(session.receipt_diff_class(rec), "bookkeeping")
        self.assertEqual(session.receipt_diff_class(dict(rec, diff_class="code")), "code")

    def test_an_old_bookkeeping_receipt_is_classified_from_its_own_diff(self):
        self._write("sessions/journal/j.md", "j\n")
        self._write("work-items/WI-0001-x.md", "x\n")
        tip = self._commit("journal + item")
        rec = {"parent": self.base[:12], "tip": tip[:12], "outcome": "landed"}
        self.assertEqual(session.receipt_diff_class(rec), "bookkeeping")

    def test_an_old_code_receipt_is_classified_from_its_own_diff(self):
        self._write("sessions/journal/j.md", "j\n")
        self._write("session.py", "x = 3\n")
        tip = self._commit("mixed")
        rec = {"parent": self.base[:12], "tip": tip[:12], "outcome": "landed"}
        self.assertEqual(session.receipt_diff_class(rec), "code")

    def test_unreadable_or_missing_is_code(self):
        """The conservative side: code lands carry the looser 60s target, so an
        unclassifiable receipt can never make the bookkeeping number look better."""
        for rec in ({}, {"parent": self.base[:12]}, {"tip": self.base[:12]},
                    {"parent": "0" * 12, "tip": "1" * 12},
                    {"diff_class": "neither", "parent": "", "tip": ""},
                    {"parent": self.base[:12], "tip": self.base[:12]}):   # empty diff
            with self.subTest(rec=rec):
                session._RECEIPT_CLASS_CACHE.clear()
                self.assertEqual(session.receipt_diff_class(rec), "code")

    def test_the_stats_filter_by_class(self):
        self._write("sessions/journal/j.md", "j\n")
        tip = self._commit("journal")
        path = self.tmp / "receipts.jsonl"
        now = time.time()
        rows = [{"at_epoch": now, "outcome": "landed", "total_seconds": 5.0,
                 "diff_class": "bookkeeping"},
                {"at_epoch": now, "outcome": "landed", "total_seconds": 7.0,
                 "parent": self.base[:12], "tip": tip[:12]},          # old, bookkeeping
                {"at_epoch": now, "outcome": "landed", "total_seconds": 90.0,
                 "diff_class": "code"}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        bk = session.land_duration_stats(path=path, diff_class="bookkeeping")
        code = session.land_duration_stats(path=path, diff_class="code")
        self.assertEqual((bk["n"], bk["worst"]), (2, 7.0))
        self.assertEqual((code["n"], code["worst"]), (1, 90.0))
        self.assertEqual(session.land_duration_stats(path=path)["n"], 3)


class NothingIsSkippedToGoFastTest(unittest.TestCase):
    """the operator's correction, held as a standing refusal: option C would have made the land
    incomplete. A land that sheds its recompile is not fast, it is incomplete.

    This asserts over the SOURCE because the thing being forbidden is a future edit, not
    a current behaviour — there is no run in which stage-shedding happens to catch."""

    def test_the_goal_is_never_read_by_the_land_path(self):
        """The structural guard. The goal constant may be read by the REPORTING surfaces
        — the receipt line, the banner, the probe — and by nothing that decides what a
        land does."""
        import inspect
        root = pathlib.Path(session.__file__).resolve().parent
        allowed = {"_land_end_to_end_line", "land_duration_stats", "landing_line"}
        for part in ("land", "lanes", "hooks"):
            src = (root / "sessionlib" / f"{part}.py").read_text(encoding="utf-8")
            tree = __import__("ast").parse(src)
            for node in __import__("ast").walk(tree):
                if not isinstance(node, __import__("ast").FunctionDef):
                    continue
                if node.name in allowed:
                    continue
                body = __import__("ast").dump(node)
                # ADR-0148 D7 added the bookkeeping target beside the minute; it is the
                # same kind of number and gets the same refusal.
                for goal in ("LAND_DURATION_GOAL_SECONDS", "LAND_BOOKKEEPING_GOAL_SECONDS"):
                    self.assertNotIn(goal, body,
                                     f"{part}.{node.name} reads {goal} — it is a goal, "
                                     f"never a gate, and nothing that decides what a "
                                     f"land does may consult it")

    def test_the_guard_sees_the_bookkeeping_goal_where_it_is_read(self):
        """Control: the guard above would be vacuous if the name were read nowhere."""
        root = pathlib.Path(session.__file__).resolve().parent
        src = (root / "sessionlib" / "land.py").read_text(encoding="utf-8")
        self.assertIn("LAND_BOOKKEEPING_GOAL_SECONDS", src)


if __name__ == "__main__":
    unittest.main()
