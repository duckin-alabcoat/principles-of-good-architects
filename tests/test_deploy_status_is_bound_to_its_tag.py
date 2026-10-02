"""WI-0412 — a status names the tag it was decided for, or it is not written.

THE DEFECT, AND IT HAD ALREADY FIRED. `write_ledger` is a read-modify-write merge, so a key
not named in a call keeps whatever an earlier call left there. The no-op path
("already running X — nothing to do") named `checked_at`, `current` and `cutover`, and NOT
`status`. So a member whose last real deploy ended at `verify-failed` or `awaiting-cutover`
got a fresh `checked_at` and a fresh `current` on every sweep, carrying a verdict decided
for an earlier release that now read as today's.

The runner host's live federation ledger, read 2026-09-20:

    status:       deployed
    current:      v7.4.0
    deployed_at:  2026-09-13T22:07:56
    apply:        {declared: false}

v7.4.0's own commit is dated 2026-09-19 — six days AFTER the `deployed_at` sitting beside
it. A green verdict for a release that never deployed.

WHY THE EXISTING CHECKS WERE BLIND, which is the part worth keeping. `ledger_disagrees_with_tree`
fires when `current != tag`; the no-op write sets `current` FROM the tree, so it was silent
by construction. `stale_cutover_event` needs a `cutover_event`, which an apply-strategy
member never has. Both compare a ledger field against the tree; neither examined `status`.

SO THE FIX IS NOT A THIRD COMPARISON OF THE SAME SHAPE. It binds `status` to its subject at
the moment it is decided, and makes an unbound one impossible to write — because the defect
is invisible at the call site that causes it, and a rule kept by discipline is the thing
that failed here the first time.
"""

import unittest

from test_deploy_runner import CONTRACT, DeployRunnerCase

import diagnose
import runner


class TheGuardMakesAnUnboundStatusImpossible(DeployRunnerCase):
    """`add-structural-guard-on-recurrence`: the rule is enforced, not remembered."""

    def test_a_status_written_without_its_tag_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            runner.write_ledger(self.system, status="deployed", current="v1.0.0")
        msg = str(cm.exception)
        self.assertIn("does not say which tag", msg)
        self.assertIn("_decided", msg)
        self.assertIn("WI-0412", msg)

    def test_nothing_is_written_when_the_guard_refuses(self):
        """A guard that refuses AFTER writing has not refused anything."""
        self.assertEqual(runner.read_ledger(self.system), {})
        with self.assertRaises(ValueError):
            runner.write_ledger(self.system, status="deployed", current="v1.0.0")
        self.assertEqual(runner.read_ledger(self.system), {})

    def test_a_write_that_names_no_status_at_all_is_untouched(self):
        """THE GUARD MUST NOT FIRE ON CORRECT CODE. The no-op path legitimately advances
        `checked_at` and `current` while deciding nothing — that call is not the defect,
        and a guard that refused it would be reverted within a day."""
        runner.write_ledger(self.system, checked_at="now", current="v1.0.0")
        self.assertEqual(runner.read_ledger(self.system)["current"], "v1.0.0")

    def test_a_null_tag_is_accepted_because_some_verdicts_precede_resolution(self):
        runner.write_ledger(self.system, **runner._decided("contract-invalid", None))
        self.assertIsNone(runner.read_ledger(self.system)["status_tag"])


class EveryStatusTheRunnerWritesCarriesItsTag(DeployRunnerCase):
    """The class grep, executed rather than asserted in prose. Whatever path a deploy takes
    out, the status it leaves behind names the tag it was decided for."""

    def test_a_successful_deploy_binds_deployed_to_the_tag_it_deployed(self):
        self._release("v2.0.0", marker="v2\n")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        rec = self._ledger()
        self.assertEqual(rec["status"], "deployed")
        self.assertEqual(rec["status_tag"], "v2.0.0")

    def test_a_smoke_failure_binds_the_verdict_to_the_REFUSED_tag_not_the_running_one(self):
        """The sharpest of these. `current` goes BACK to the healthy version while the
        verdict is about the one that was turned away — bind the status to `current` and a
        smoke failure reads as a verdict on the version it just protected."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self._release("v2.0.0", contract={**CONTRACT,
                                          "smoke": {"cmd": ["/bin/sh", "-c", "exit 1"]}})
        rc, _ = self._deploy()
        self.assertEqual(rc, 1)

        rec = self._ledger()
        self.assertEqual(rec["status"], "smoke-failed")
        self.assertEqual(rec["current"], "v1.0.0", "the healthy version still runs")
        self.assertEqual(rec["status_tag"], "v2.0.0", "the verdict is about the refusal")

    def test_an_invalid_contract_binds_its_verdict_to_the_tag_it_could_not_read(self):
        self._release("v2.0.0", contract={**CONTRACT, "restart": "nonsense"})
        with self.assertRaises(runner.DeployError):
            self._deploy()
        rec = self._ledger()
        self.assertEqual(rec["status"], "contract-invalid")
        self.assertEqual(rec["status_tag"], "v2.0.0")


class TheStaleVerdictIsReportedAsStale(DeployRunnerCase):

    def _reproduce_the_runner(self):
        """The live defect, rebuilt: a verdict decided for an older tag, left beside a
        `current` the no-op write dragged forward onto the tag now on the tree."""
        rc, _ = self._deploy()                       # v1.0.0, honestly deployed
        self.assertEqual(rc, 0)
        self._release("v2.0.0", marker="v2\n")
        rc, _ = self._deploy()                       # v2.0.0, honestly deployed
        self.assertEqual(rc, 0)
        # Now forge the state the merge produced: status still says v1.0.0's verdict.
        runner.write_ledger(self.system, **runner._decided("deployed", "v1.0.0"))

    def test_a_status_decided_for_an_earlier_tag_is_called_out(self):
        self._reproduce_the_runner()
        bundle = diagnose.snapshot(self.system)
        said = bundle["ledger"]["stale_status"]
        self.assertIn("decided for v1.0.0", said)
        self.assertIn("v2.0.0", said)
        self.assertIn("says nothing about this one", said)
        self.assertIn("**STALE:**", diagnose.render(bundle))

    def test_the_two_existing_checks_are_still_silent_on_it(self):
        """THE POINT OF THE ITEM, PINNED. If either older check fired here, this would be a
        duplicate rather than a gap — and a future simplification that folded the new check
        into one of them would pass its own tests while re-opening the hole."""
        self._reproduce_the_runner()
        led = diagnose.snapshot(self.system)["ledger"]
        self.assertNotIn("ledger_disagrees_with_tree", led,
                         "current matches the tree, which is why this case was invisible")
        self.assertNotIn("stale_cutover_event", led)
        self.assertIn("stale_status", led)

    def test_a_status_decided_for_the_tag_on_the_tree_raises_nothing(self):
        """NEGATIVE CONTROL. A check that flags every healthy member is one somebody
        switches off."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        led = diagnose.snapshot(self.system)["ledger"]
        self.assertNotIn("stale_status", led)
        self.assertNotIn("unbound_status", led)
        self.assertNotIn("**STALE:**", diagnose.render(diagnose.snapshot(self.system)))

    def test_a_record_with_no_status_tag_reads_as_CANNOT_TELL_not_as_fine(self):
        """A ledger written by an older runner genuinely cannot say what its status was
        about. Folding that into the clean case would certify exactly the records most
        likely to be carrying the defect."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        rec = self._ledger()
        rec.pop("status_tag")
        runner.replace_ledger(self.system, rec)

        led = diagnose.snapshot(self.system)["ledger"]
        self.assertIn("unbound_status", led)
        self.assertIn("cannot be determined", led["unbound_status"])
        self.assertNotIn("stale_status", led, "unknown is not the same answer as stale")

    def test_an_unvouched_status_sends_the_sweep_down_the_full_path(self):
        """SUPERSEDED AND REWRITTEN THE SAME DAY, and the history is the point.

        This test was originally `test_the_no_op_sweep_does_not_launder_a_stale_status`. It
        drove the same fixture, asserted the sweep said "nothing to do", and checked that
        the no-op write had NOT re-stamped `status_tag` — i.e. it pinned that a stale
        verdict stayed visibly stale.

        It was a true assertion about a defect that WI-0412 only made VISIBLE. Hours later
        the runner host showed what visible-but-tolerated costs: a sweep on 2026-09-21
        took exactly this path, reported "already running v7.5.0 — nothing to do", and left
        a member that had REFUSED ten minutes earlier unreachable by every future sweep.
        WI-0416's answer is that the sweep must not take the no-op path at all when the
        ledger does not vouch for the tag — which makes the laundering scenario this test
        was written about UNREACHABLE.

        So the old assertion is not merely obsolete, it would now pin the wrong behaviour.
        Rewritten to assert the stronger property rather than deleted, because the fixture
        — a verdict decided for an earlier tag, sitting beside a tree at the current one —
        is still the exact shape that bit production."""
        self._reproduce_the_runner()
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertNotIn("nothing to do", out)
        self.assertIn("the ledger does not vouch for it", out)
        self.assertIn("was decided for v1.0.0", out)
        # It really redeployed: the verdict is now about the tag on the tree.
        self.assertEqual(self._ledger()["status_tag"], "v2.0.0")
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertNotIn("stale_status", diagnose.snapshot(self.system)["ledger"])


class TheDigestCarriesTheBinding(DeployRunnerCase):

    def test_a_status_that_stops_being_about_the_tree_moves_the_digest(self):
        """`ledger_status` alone cannot move here — the status IS the thing that did not
        change, which is the defect. Without the tag in the digest, `post_if_changed`
        compares equal across exactly the transition that matters and mails nothing."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        before = diagnose.digest(diagnose.snapshot(self.system))

        runner.write_ledger(self.system, **runner._decided("deployed", "v0.0.1"))
        after = diagnose.digest(diagnose.snapshot(self.system))

        self.assertEqual(before["ledger_status"], after["ledger_status"],
                         "the status text is identical — that is the whole problem")
        self.assertNotEqual(before, after)
        self.assertEqual(after["ledger_status_tag"], "v0.0.1")


if __name__ == "__main__":
    unittest.main()
