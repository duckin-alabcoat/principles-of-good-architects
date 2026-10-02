"""WI-0416 — the tree holding a tag is not the ledger vouching for it.

THE PRODUCTION SEQUENCE, observed on the deploy host 2026-09-21 and reported by operator:

  sweep 1  the sweep took v7.5.0, checked it out, and REFUSED at the state gate.
           `checkout` precedes `seed_state`, so the refusal left v7.5.0 ON THE TREE.
  sweep 2  the next sweep asked `before == target` — a question about a DIRECTORY — found it
           true, and reported "already running v7.5.0 — nothing to do". It wrote
           `current=v7.5.0` beside a `status=deployed` that had been decided for v7.3.0 on
           2026-09-13, with no `status_tag` at all.

A green verdict for a release that had refused ten minutes earlier, and a member that every
subsequent sweep would skip for the same reason, forever. Neither WI-0411's apply receipt nor
WI-0412's binding could prevent it: the runner that wrote that record was v7.4.0's, which has
neither, and both fixes only take effect one tag later (WI-0415).

WHAT THIS PINS. `status_tag` is the only field that says which tag a verdict was decided
FOR, so it is the only thing that can turn "the tag is on disk" into "the tag is running".
An ABSENT `status_tag` counts as not-vouched, because every ledger written before WI-0412
lacks it and this is the one place where "cannot tell" and "it's fine" lead to opposite
actions.

THE NEGATIVE CONTROLS MATTER MORE THAN THE POSITIVE HERE. Trading a sweep that never
deploys for one that FULLY redeploys every five minutes is a worse defect than the one being
fixed, so the vouched cases are asserted alongside every unvouched one.
"""

import unittest

from test_deploy_runner import CONTRACT, DeployRunnerCase

import runner


class VouchCase(DeployRunnerCase):

    def _unbind(self):
        """Strip `status_tag`, reproducing every ledger written before WI-0412."""
        rec = self._ledger()
        rec.pop("status_tag", None)
        runner.replace_ledger(self.system, rec)


class AnUnvouchedStatusIsNotAlreadyRunning(VouchCase):

    def test_a_status_decided_for_an_earlier_tag_triggers_a_full_deploy(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self._release("v2.0.0", marker="v2\n")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        runner.write_ledger(self.system, **runner._decided("deployed", "v1.0.0"))

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("does not vouch", out)
        self.assertNotIn("nothing to do", out)
        self.assertEqual(self._ledger()["status_tag"], "v2.0.0")

    def test_a_record_with_no_status_tag_at_all_triggers_a_full_deploy(self):
        """THE LIVE CASE. Every member's ledger is in this state today."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self._unbind()

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("does not vouch", out)
        self.assertIn("predates status binding", out)
        self.assertEqual(self._ledger()["status_tag"], "v1.0.0")

    def test_the_full_path_really_runs_including_seed_state(self):
        """THE ACCEPTANCE, and it is about `seed_state` specifically. A tag that got onto
        the tree by a refusal may have missed seeding, and the refusal told us only where
        it stopped — so re-running apply alone would leave the gap that caused it."""
        calls = []
        real = runner.seed_state

        def spy(*a, **kw):
            calls.append(a[0])
            return real(*a, **kw)

        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self._unbind()
        calls.clear()

        runner.seed_state = spy
        self.addCleanup(lambda: setattr(runner, "seed_state", real))
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [self.system],
                         "the redeploy skipped seeding, so a refusal at the state gate "
                         "would never be retried")

    def test_an_unreadable_ledger_does_not_vouch(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        runner.ledger_path(self.system).write_text("{ not json")
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("could not be parsed", out)

    def test_a_dry_run_says_what_it_would_do_and_writes_nothing(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self._unbind()
        before = runner.read_ledger(self.system)

        rc, out = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("does not vouch", out)
        self.assertEqual(runner.read_ledger(self.system), before)


class AVouchedStatusStillShortCircuits(VouchCase):
    """THE NEGATIVE CONTROLS. A check that fires on healthy members gets switched off."""

    def test_a_status_decided_for_the_tag_on_the_tree_reports_nothing_to_do(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        for _ in range(3):
            rc, out = self._deploy()
            self.assertEqual(rc, 0)
            self.assertIn("nothing to do", out)
            self.assertNotIn("does not vouch", out)

    def test_a_vouched_member_is_not_redeployed_every_sweep(self):
        """The failure mode this trades against, asserted directly: a sweep that fully
        redeploys a healthy member every five minutes is worse than one that skips it."""
        calls = []
        real = runner.seed_state

        def spy(*a, **kw):
            calls.append(a[0])
            return real(*a, **kw)

        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        calls.clear()
        runner.seed_state = spy
        self.addCleanup(lambda: setattr(runner, "seed_state", real))
        for _ in range(3):
            self._deploy()
        self.assertEqual(calls, [], "a healthy member was re-seeded on every sweep")

    def test_a_vouched_apply_member_still_re_runs_only_its_apply(self):
        """WI-0411's branch must stay REACHABLE. Its question ('has this tag's apply run?')
        is narrower than this item's ('is this verdict about this tag?'), and folding them
        would turn a missing apply receipt into a full redeploy."""
        contract = {**CONTRACT, "units": [],
                    "apply": {"cmd": ["/bin/sh", "-c", "echo ran >> applied.log"],
                              "rerunnable": True}}
        self._release("v2.0.0", contract=contract)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)

        rec = self._ledger()
        rec.pop("apply_receipt")              # receipt gone, but status_tag still vouches
        runner.replace_ledger(self.system, rec)

        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("its apply has not run here", out)
        self.assertNotIn("does not vouch", out)


class TheCutoverReProbeIsUntouched(VouchCase):
    """The branches ABOVE the vouch check are a different mechanism (WI-0350) and must keep
    working — a units member that is not cut over is reported, not redeployed."""

    def setUp(self):
        super().setUp()
        self._real = runner.unit_target
        self.addCleanup(lambda: setattr(runner, "unit_target", self._real))

    def test_a_units_member_awaiting_cutover_still_says_so(self):
        contract = {**CONTRACT, "units": ["com.example.thing"], "restart": "none"}
        self._release("v2.0.0", contract=contract)
        runner.unit_target = lambda label: "/Users/someone/Projects/Thing"
        self._deploy()
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        self.assertIn("NOT cut over", out)
        self.assertNotIn("does not vouch", out)


if __name__ == "__main__":
    unittest.main()
