"""WI-0329 / ADR-0124 D1: the serialized section holds the merge, never the validation.

WHAT WENT WRONG, measured rather than suspected. ADR-0114 D2 put the whole
rebase -> gate -> CAS window inside a one-at-a-time lock, which was right for the problem
it had: lanes were thrashing, each losing its compare-and-swap to the last and starting
another suite. But it made the queue drain at one SUITE per land. On 2026-09-10 the
WI-0325 land recorded 271 s of serial derive and 58 s of gate inside the lock, against a
merge of 0.060 s and a push of 1.7 s — so 99.5% of the hold was work that never touched
the trunk, and five lanes behind it took about an hour to land ten minutes of work.

WHAT MAKES THE NEW SHAPE SAFE is not the shortening. It is the comparison: inside the lock
the land refuses to advance unless the trunk is still exactly the commit its candidate was
validated against. So the tree that lands IS the tree that was gated — an identity, not an
argued equivalence. When the trunk has moved, nothing is written at all, and the rebase
and re-gate happen outside the queue. ADR-0114 D7 ("a lost CAS still re-runs the whole
gate") is kept in full; only its location changes.

These tests are about WHERE work happens, so most of them assert on the land gate's own
record rather than on elapsed time. A timing assertion would pass on a fast machine for a
build that still gated under the lock, which is the one thing here worth catching.
"""
import json
import pathlib
import sys
import time
import unittest
from unittest import mock

from test_land_is_a_merge import GateNeutralBase, _git
import session


class TheGateRunsOutsideTheQueueTest(GateNeutralBase):
    """D1, stated as the property a second lane can observe."""

    def test_the_land_gate_is_free_while_the_checks_run(self):
        """The load-bearing test in this file.

        `_land_gate_holder()` reads the record another lane would have to wait on. If it
        is empty while this land's gate is running, then a sibling arriving at that
        moment lands instead of queueing — which is the entire point of the change,
        expressed from the waiting lane's side rather than this one's.

        ADR-0148: the suite no longer runs at land at all (tests/test_land_is_a_merge.py
        pins that); what `_run_gate` runs here is the cheap checks, and they are still
        outside the lock."""
        self._code_carrying_lane()
        held_during_gate = []
        real = session._run_gate

        def spy(cwd=None, **kwargs):
            held_during_gate.append(session._land_gate_holder())
            return real(cwd=cwd, **kwargs)

        with mock.patch.object(session, "_run_gate", side_effect=spy):
            outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertTrue(held_during_gate, "control: the gate must actually have run")
        for holder in held_during_gate:
            self.assertEqual(holder, {},
                             "the land gate was HELD while the checks ran — that is "
                             "ADR-0114 D2, which ADR-0124 D1 supersedes")

    def test_the_publication_is_held(self):
        """The other half, and it has to be asserted or the first test is satisfied by a
        land that holds the lock for nothing at all.

        Everything after the ref moves — the number release, the main-checkout sync, the
        view recompile, the push — mutates state every lane shares, and two lands doing
        it at once collide on the main checkout's `index.lock` in a way no CAS refuses
        cleanly (WI-0293). the operator's ruling says the lock holds "merge and push"; this is
        what publishing that merge consists of."""
        self._journal_only_lane()
        held = []
        real = session._sync_main_checkout

        def spy(*a, **kw):
            held.append(session._land_gate_holder())
            return real(*a, **kw)

        with mock.patch.object(session, "_sync_main_checkout", side_effect=spy):
            outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertTrue(held, "control: the sync must actually have run")
        for holder in held:
            self.assertNotEqual(holder, {},
                                "the main checkout was mutated with the land gate free")


class AMovedTrunkIsCaughtBeforeAnythingIsWrittenTest(GateNeutralBase):
    """D1's refusal branch. ADR-0114 D7, relocated rather than repealed."""

    def _land_with_a_competitor(self, moves):
        """Land, with the trunk advancing after each of the first `moves` gate runs.

        The competitor is shaped like the real one: a `poga work` store write committed
        straight into the main checkout, which is outside the land gate by design
        (ADR-0073) and is the majority of what a land actually loses to (WI-0272).

        The lane carries CODE. The fixture gate (`PASS_GATE`) has no suite command, so
        under ADR-0148 every command in it is a cheap check that runs at every attempt;
        the no-re-validation half of D4 (a suite-shaped gate lands a moved lane with ZERO
        suite runs) is pinned in tests/test_land_is_a_merge.py."""
        self._code_carrying_lane()
        real = session._run_gate
        runs = {"n": 0}

        def racing_gate(cwd=None, **kwargs):
            ok, rep = real(cwd=cwd, **kwargs)
            runs["n"] += 1
            if runs["n"] <= moves:
                p = self.main / "work-items" / f"WI-999{runs['n']}-note.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"competitor {runs['n']}\n", encoding="utf-8")
                _git(self.main, "add", "-A")
                _git(self.main, "commit", "-qm", f"chore(work-items): note {runs['n']}")
            return ok, rep

        with mock.patch.object(session, "_run_gate", side_effect=racing_gate):
            outcome, _, out = self._land_watching_the_gate()
        return outcome, runs["n"], out

    def test_it_lands_after_re_checking_outside_the_queue(self):
        """ADR-0148 D4 changed what a lost race costs, not where it is paid: the cheap
        checks re-run over the rebased candidate (they read the very bookkeeping a
        competitor just wrote), the suite does not, and both happen outside the lock."""
        outcome, gate_runs, out = self._land_with_a_competitor(1)
        self.assertTrue(outcome, out)
        self.assertEqual(gate_runs, 2,
                         "the cheap checks must re-run over the rebased candidate")
        self.assertIn("re-checking outside the queue", out)

    def test_the_refused_attempt_wrote_nothing(self):
        """A receipt for an attempt that merged would carry a `merge` stage. The absence
        of one is what says the trunk check happened BEFORE the write, not after it —
        which is the difference between declining to land and undoing a land."""
        self._land_with_a_competitor(1)
        receipts = session.land_receipts()
        moved = [r for r in receipts if r["outcome"] == "moved"]
        self.assertTrue(moved, "the contended attempt must have left a receipt")
        for rec in moved:
            self.assertNotIn("merge", rec["stages"])
            self.assertIn("moved_to", rec["stages"])

    def test_a_moved_trunk_never_publishes(self):
        """Fail-closed on the publish side: an attempt that does not advance the ref must
        not sync, recompile or push either. Catching this by inspection would mean
        reading the whole publish closure; asserting it costs one counter."""
        published = []
        real = session._sync_main_checkout
        with mock.patch.object(session, "_sync_main_checkout",
                               side_effect=lambda *a, **k: (published.append(1),
                                                            real(*a, **k))[1]):
            outcome, gate_runs, out = self._land_with_a_competitor(1)
        self.assertTrue(outcome, out)
        self.assertEqual(len(published), 1,
                         "two check runs, one land — the refused attempt must not publish")


class TheReceiptTest(GateNeutralBase):
    """D4. The measurement that keeps D1 honest after everyone has moved on."""

    def test_a_land_writes_one_receipt_with_its_hold(self):
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        receipts = session.land_receipts()
        self.assertEqual(len(receipts), 1, receipts)
        rec = receipts[0]
        self.assertEqual(rec["outcome"], "landed")
        self.assertEqual(rec["verb"], "land-lane")
        for key in ("lock_seconds", "queue_seconds", "validate_seconds", "at_epoch"):
            self.assertIsInstance(rec[key], (int, float), key)
        self.assertIn("merge", rec["stages"])
        self.assertIn("recompile", rec["stages"])

    def test_the_hold_on_an_unmoved_trunk_is_inside_the_budget(self):
        """Acceptance (e), in fixture form. The real measurement is in the journal; this
        is the version that runs on every land forever after."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        rec = session.land_receipts()[0]
        self.assertLessEqual(rec["lock_seconds"], session.LAND_LOCK_HOLD_BUDGET_SECONDS,
                             f"the serialized section is over budget: {rec}")

    def test_the_land_says_how_long_it_held(self):
        """`a-close-is-the-banner-not-the-sentence`: a section that shrank by two orders
        of magnitude and says nothing in the land's own output is a change the next
        operator has to take on trust."""
        self._journal_only_lane()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, out)
        self.assertIn("stage lock: held", out)
        self.assertIn("validated", out)

    def test_an_over_budget_hold_says_so_in_the_land_itself(self):
        """The warning has to be reachable, not merely written. FL7 is the surface that
        aggregates; this is the one the operator watching the land sees first."""
        rec = {"lock_seconds": session.LAND_LOCK_HOLD_BUDGET_SECONDS + 1,
               "queue_seconds": 0.1, "validate_seconds": 0.2, "stages": {"merge": 0.05}}
        line = session._land_hold_line(rec)
        self.assertIn("OVER BUDGET", line)
        self.assertIn("FL7", line)
        self.assertNotIn("OVER BUDGET", session._land_hold_line(dict(rec, lock_seconds=1.0)))

    def test_receipts_older_than_the_window_are_trimmed(self):
        """`retention-enforced-by-code`. A log that only grows is a chore that lapses,
        and this one is written on a path nobody watches."""
        path = self.tmp / "receipts.jsonl"
        old = time.time() - (session.LAND_RECEIPT_RETENTION_DAYS + 1) * 86400
        path.write_text(
            json.dumps({"at_epoch": old, "outcome": "landed"}) + "\n"
            + json.dumps({"at_epoch": time.time(), "outcome": "landed"}) + "\n",
            encoding="utf-8")
        session._land_receipts_trim(path)
        self.assertEqual(len(session.land_receipts(path=path)), 1)

    def test_an_unreadable_line_is_skipped_not_counted_as_a_good_land(self):
        """`declare-what-a-check-assumes`: corrupt evidence must not read as a pass."""
        path = self.tmp / "receipts.jsonl"
        path.write_text("{not json\n"
                        + json.dumps({"at_epoch": time.time(), "outcome": "landed",
                                      "lock_seconds": 1.0}) + "\n", encoding="utf-8")
        self.assertEqual(len(session.land_receipts(path=path)), 1)

    def test_no_common_dir_never_fails_a_land(self):
        """ADR-0114 D5's first fail-open path, extended to the receipt. A land must never
        fail for want of somewhere to write a note about itself."""
        with mock.patch.object(session, "_git_common_dir", return_value=None):
            self.assertIsNone(session._land_receipts_path())
            session._land_receipt_write({"at_epoch": time.time()})     # must not raise
            self.assertEqual(session.land_receipts(), [])


class EveryLanderUsesTheOneSerializedSectionTest(unittest.TestCase):
    """The structural guard (`add-structural-guard-on-recurrence`).

    ADR-0117 D4 is the precedent and the warning: the last land-path change made in one
    lander and not the others shipped covering one of four ref-advancing paths and had to
    be written up as a known gap the same day. This asserts over the SOURCE, because a
    behavioural test can only reach the lander its fixture happens to drive — and the two
    legacy landers are exactly the ones no fixture drives often."""

    LANDERS = {
        "sessionlib/lanes.py": ["_land_worktree_lane"],
        "sessionlib/land.py": ["_land_candidate", "_land_branch"],
    }

    def _source_of(self, path, name):
        import ast
        root = pathlib.Path(session.__file__).resolve().parent
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.dump(node)
        self.fail(f"{name} not found in {path}")

    def test_no_lander_opens_the_land_gate_itself(self):
        """One helper owns the critical section. A lander that takes the lock directly is
        a lander that can put something new inside it without anyone deciding to."""
        for path, names in self.LANDERS.items():
            for name in names:
                with self.subTest(lander=name):
                    self.assertNotIn("land_gate_lock", self._source_of(path, name))

    def test_every_lander_advances_through_the_shared_helper(self):
        for path, names in self.LANDERS.items():
            for name in names:
                with self.subTest(lander=name):
                    src = self._source_of(path, name)
                    self.assertIn("_serialized_advance", src)
                    self.assertIn("_land_receipt", src)

    def test_every_lander_starts_the_end_to_end_clock(self):
        """WI-0349, and it belongs in THIS class for the reason the class exists.

        The mark has to be taken inside the lander, because there are five call sites
        (two in `cmd_merge`, three in `cmd_end`) and a forgotten mark is a land that
        reports a per-attempt sum as its end-to-end cost — understating it silently,
        which is worse than not measuring at all. Asserted over the source for the same
        reason as its siblings: a behavioural test only reaches the lander its fixture
        drives, and the two legacy landers are exactly the ones no fixture drives."""
        for path, names in self.LANDERS.items():
            for name in names:
                with self.subTest(lander=name):
                    self.assertIn("mark_ready_to_land", self._source_of(path, name))
