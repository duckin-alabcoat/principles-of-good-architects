"""WI-0411 — an apply-strategy member can re-run apply once the tree holds the target.

THE DEFECT. `_deploy` short-circuits on `before == target`. For a member whose contract
declares an `apply` strategy, `cut` is `{}` by construction, both mid-branches are skipped
by falsiness, and control reaches "already running — nothing to do" having called
`run_apply` exactly never. The only route to apply is the forward path, which requires
`before != target`. So a member whose tree already holds the tag can never apply it: the
second release of the same tag is a no-op by construction, not by policy.

The shape to guard against: an apply refuses (say, because a verb its apply script calls
is not on the sweep unit's PATH), someone completes it by hand, and that leaves the tree
holding the tag. Every sweep after that returns 0 having executed nothing, and the member
reads as current with its apply never having run under the runner.

WHAT THE FIX TURNS ON. "The tree HOLDS the tag" and "the tag was APPLIED here" are
different facts, and only the first is on disk. The second needs a receipt, stamped with
the tag it belongs to for the same reason `cutover_event` is — `write_ledger` merges, so an
unstamped receipt would survive into the next release and report last month's apply as this
one's.

THE TEST THAT MATTERS MOST IS THE NEGATIVE CONTROL. Without it this trades a skip that
never runs for a loop that re-runs someone else's apply every five minutes, which is
strictly worse: the old defect was invisible, the new one restarts a service on a
schedule.
"""

import json
import unittest

from test_deploy_runner import CONTRACT, DeployRunnerCase

import runner


class ApplyCase(DeployRunnerCase):
    """An apply-strategy contract whose apply leaves a countable trace."""

    #: `>>` so a second run is visible as a second line rather than overwriting the first.
    #: A test that asserted only "the file exists" could not tell one apply from two, which
    #: is the whole question the negative control asks.
    APPLY = {**CONTRACT,
             "units": [],
             "apply": {"cmd": ["/bin/sh", "-c", "echo ran >> applied.log"]}}
    RERUNNABLE = {**APPLY,
                  "apply": {**APPLY["apply"], "rerunnable": True}}

    def _applies(self) -> int:
        """How many times the apply has run against this deploy tree."""
        log = self._tree() / "applied.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def _receipt(self) -> dict:
        return self._ledger().get("apply_receipt") or {}


class TheForwardPathEarnsAReceipt(ApplyCase):

    def test_a_deploy_that_applies_records_the_tag_it_applied(self):
        self._release("v2.0.0", contract=self.RERUNNABLE)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._applies(), 1)
        self.assertEqual(self._receipt()["tag"], "v2.0.0")

    def test_a_units_member_never_grows_a_receipt(self):
        """Absence has to keep meaning 'this member has no apply', or a later reader takes
        a missing receipt for a missed apply on every units-based system in the fleet."""
        # A marker, or this re-commits the contract setUp already wrote and git refuses
        # the empty commit.
        self._release("v2.0.0", contract=CONTRACT, marker="v2\n")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertNotIn("apply_receipt", self._ledger())


class TheShortCircuitRunsAnApplyThatNeverRan(ApplyCase):

    def setUp(self):
        super().setUp()
        self._release("v2.0.0", contract=self.RERUNNABLE)

    def _strand(self):
        """Reproduce the stranded state: the tag is on the tree and its apply never ran.

        Built by deploying and then REMOVING the receipt, rather than by hand-checking-out
        the tree — the point is a ledger that has no receipt for the tag on disk, and
        reaching it through the real deploy path keeps every other field honest."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        rec = self._ledger()
        rec.pop("apply_receipt")
        runner.replace_ledger(self.system, rec)
        (self._tree() / "applied.log").unlink()

    def test_the_sweep_runs_apply_and_records_the_receipt(self):
        """THE ACCEPTANCE: target already on the tree, no receipt for it, apply runs."""
        self._strand()
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._applies(), 1)
        self.assertEqual(self._receipt()["tag"], "v2.0.0")
        self.assertIn("its apply has not run here", out)
        self.assertNotIn("nothing to do", out)

    def test_a_receipt_for_that_same_tag_does_not_re_run_apply(self):
        """THE NEGATIVE CONTROL, REQUIRED AND NOT OPTIONAL. A sweep runs every five
        minutes. Re-running an apply on each one — for a member whose apply restarts a
        service on another host — trades an invisible skip for a visible loop."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._applies(), 1)

        for _ in range(3):
            rc, out = self._deploy()
            self.assertEqual(rc, 0)
            self.assertIn("nothing to do", out)
        self.assertEqual(self._applies(), 1, "the sweep re-ran an apply it had a receipt "
                                             "for — every five minutes, forever")

    def test_a_receipt_for_a_DIFFERENT_tag_does_not_count(self):
        """The stamp is the whole value of the receipt. An unstamped one would satisfy the
        test above while reporting an older release's apply as this one's."""
        self._strand()
        runner.write_ledger(self.system,
                            apply_receipt={"tag": "v1.0.0", "at": "2026-01-01T00:00:00"})
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("the receipt on file is for v1.0.0", out)
        self.assertEqual(self._applies(), 1)
        self.assertEqual(self._receipt()["tag"], "v2.0.0")

    def test_a_dry_run_applies_nothing_and_records_nothing(self):
        self._strand()
        before = json.dumps(self._ledger(), sort_keys=True)
        rc, _ = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._applies(), 0)
        self.assertEqual(json.dumps(self._ledger(), sort_keys=True), before)


class AMemberMustDeclareThatItsApplyCanBeReRun(ApplyCase):
    """The opt-in, and why the default is the conservative direction.

    The receipt is new, so on the first sweep after it ships NO member has one. An opt-OUT
    default would therefore re-run every apply-strategy member's apply at once, unattended,
    on the strength of an assumption about scripts this program did not write. An
    `apply.sh` may delegate to a verb another member owns, on a host this Architect
    cannot reach — convergence is established from reading the script, the
    idempotency of the thing it actually invokes is not.
    """

    def test_a_member_that_has_not_declared_keeps_todays_behaviour(self):
        self._release("v2.0.0", contract=self.APPLY)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._applies(), 1)

        rec = self._ledger()
        rec.pop("apply_receipt", None)
        runner.replace_ledger(self.system, rec)

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)
        self.assertEqual(self._applies(), 1, "an undeclared member had its apply re-run — "
                                             "the blast radius this default exists to hold")

    def test_the_declaration_is_what_changes_the_answer(self):
        """Same stranded state, same sweep, one key different. Asserted as a PAIR against
        the test above so the opt-in is shown to be load-bearing rather than merely
        present — a flag nothing reads passes every test written about the flag."""
        self._release("v2.0.0", contract=self.RERUNNABLE)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        rec = self._ledger()
        rec.pop("apply_receipt")
        runner.replace_ledger(self.system, rec)

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("its apply has not run here", out)
        self.assertEqual(self._applies(), 2)

    def test_the_schema_accepts_the_key_so_a_declaring_contract_is_not_refused(self):
        """`contract.schema.json` sets `additionalProperties: false` on the apply block, so
        an undeclared key would refuse every contract that used it — the opt-in would be
        unusable and the refusal would arrive at deploy time on the member's machine."""
        self._release("v2.0.0", contract=self.RERUNNABLE)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertNotEqual(self._ledger().get("status"), "contract-invalid")


class AUnitBasedMemberIsUnchangedAtTheShortCircuit(ApplyCase):
    """Asserted DIRECTLY, as the item requires. The cutover re-probe above this branch is
    the fix that landed for the shape that HAS units; this change must not perturb it."""

    def setUp(self):
        super().setUp()
        self._real = runner.unit_target
        self.addCleanup(lambda: setattr(runner, "unit_target", self._real))

    def test_a_cut_over_units_member_still_reports_nothing_to_do(self):
        # `restart: none` because the short-circuit is what is under test and a real
        # `launchctl kickstart` is not available to the suite. The branch this test
        # exercises reads `contract["units"]`, never the restart strategy.
        contract = {**CONTRACT, "units": ["com.example.thing"], "restart": "none"}
        self._release("v2.0.0", contract=contract)
        runner.unit_target = lambda label: str(self._tree())
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)
        self.assertNotIn("its apply has not run here", out)
        self.assertNotIn("apply_receipt", self._ledger())

    def test_a_units_member_awaiting_cutover_still_says_so(self):
        # `restart: none` because the short-circuit is what is under test and a real
        # `launchctl kickstart` is not available to the suite. The branch this test
        # exercises reads `contract["units"]`, never the restart strategy.
        contract = {**CONTRACT, "units": ["com.example.thing"], "restart": "none"}
        self._release("v2.0.0", contract=contract)
        runner.unit_target = lambda label: "/Users/someone/Projects/Thing"
        self._deploy()
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        self.assertIn("NOT cut over", out)
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")


if __name__ == "__main__":
    unittest.main()
