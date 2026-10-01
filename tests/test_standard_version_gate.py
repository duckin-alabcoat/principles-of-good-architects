"""Rollout-complete gate (ADR-0047) — the fleet-completeness predicate that
push-substrate's gate and `standard_version.py --assert-complete` both read.

Unit-level: the located-member set and each member's verdict are injected, so the
gate's classification logic is tested without needing real member repos on disk.
stdlib unittest (the federation gate must not depend on pytest — same reason
push-substrate.tests_green uses unittest)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "curate"))
import standard_version as sv  # noqa: E402


def _verdict(status="clean", detected=None, harness_stale=False):
    detected = sv.LATEST if detected is None else detected
    return {"status": status, "detected": detected, "declared": detected,
            "runtime": "claude", "note": "", "harness_stale": harness_stale}


class RolloutGateTest(unittest.TestCase):
    def _run(self, verdicts, roster=None, parked=frozenset()):
        """fleet_complete over an injected member set; each member's verdict is
        keyed by system-id (the fake repo's name).

        The roster AND the parked set are both injected (WI-0098): coverage is now
        part of the predicate, so a test that left either implicit would be asserting
        against whatever portfolio.md and PARKED happen to say on the day it runs.
        `parked` defaults to empty rather than to the shipped set — these fixtures use
        synthetic system-ids, and the real exemptions would (correctly) surface as
        parked-but-unrostered against them."""
        members = {sid: (Path("/fake") / sid, {}) for sid in verdicts}
        roster = set(verdicts) if roster is None else set(roster)
        with mock.patch.object(sv, "locate", lambda roots, rp: members), \
             mock.patch.object(sv, "PARKED", set(parked)), \
             mock.patch.object(sv, "evaluate_member", lambda repo, cfg: verdicts[repo.name]):
            return sv.fleet_complete([], {}, roster=roster)

    def test_all_clean_is_complete(self):
        complete, blockers, n, _cov = self._run({"a": _verdict(), "b": _verdict()})
        self.assertTrue(complete)
        self.assertEqual(n, 2)
        self.assertEqual(sv._blocker_parts(blockers), [])

    def test_below_floor_blocks(self):
        complete, blockers, _n, _cov = self._run({"a": _verdict(), "b": _verdict(status="below-floor")})
        self.assertFalse(complete)
        self.assertIn("b", blockers["below"])

    def test_behind_blocks(self):
        complete, blockers, _n, _cov = self._run({"a": _verdict(status="behind", detected="1.0.0")})
        self.assertFalse(complete)
        self.assertIn("a", blockers["behind"])

    def test_detected_below_latest_without_behind_status_still_blocks(self):
        # A member whose status isn't literally "behind" but whose detected
        # version is < LATEST must still count as behind (the _cmp arm).
        complete, blockers, _n, _cov = self._run({"a": _verdict(status="clean", detected="1.0.0")})
        self.assertFalse(complete)
        self.assertIn("a", blockers["behind"])

    def test_harness_stale_blocks(self):
        complete, blockers, _n, _cov = self._run({"a": _verdict(harness_stale=True)})
        self.assertFalse(complete)
        self.assertIn("a", blockers["stale"])

    def test_no_members_is_not_complete(self):
        # Nothing located is never "complete" (n==0 must fail the gate closed).
        complete, _blockers, n, _cov = self._run({})
        self.assertFalse(complete)
        self.assertEqual(n, 0)


class CoverageDenominatorTest(unittest.TestCase):
    """WI-0098 — the gate must not get easier to pass as coverage gets worse.

    Every test here FAILS against the pre-WI-0098 code, which computed completeness
    over `locate()` alone: an unreachable member left the numerator and the
    denominator together, so the assertions below all returned complete=True."""

    _run = RolloutGateTest._run

    def test_unlocated_expected_member_blocks_complete(self):
        # THE defect. Two clean members, but the roster expects three: the third is
        # unreachable HERE, which is unverified — not passed.
        complete, blockers, n, cov = self._run(
            {"a": _verdict(), "b": _verdict()}, roster={"a", "b", "c"})
        self.assertFalse(complete, "an unlocated expected member must block COMPLETE")
        self.assertEqual(sv._blocker_parts(blockers), [],
                         "no member FAILED — this is a coverage gap, not a detector failure")
        self.assertEqual(cov["unlocated"], ["c"])
        self.assertEqual(n, 2)

    def test_losing_a_member_flips_the_gate_rather_than_shrinking_the_fleet(self):
        # The asymmetry stated as a test: same roster, one fewer located member.
        # Before WI-0098 both of these passed identically.
        roster = {"a", "b", "c"}
        all_three, _b, _n, _c = self._run(
            {"a": _verdict(), "b": _verdict(), "c": _verdict()}, roster=roster)
        lost_one, _b2, _n2, _c2 = self._run(
            {"a": _verdict(), "b": _verdict()}, roster=roster)
        self.assertTrue(all_three)
        self.assertFalse(lost_one)

    def test_parked_members_are_not_expected(self):
        # A declared convergence exemption leaves the denominator deliberately.
        complete, _b, _n, cov = self._run(
            {"a": _verdict(), "b": _verdict()}, roster={"a", "b", "c"}, parked={"c"})
        self.assertTrue(complete)
        self.assertEqual(cov["unlocated"], [])
        self.assertEqual(cov["parked"], ["c"])

    def test_located_but_unrostered_member_is_surfaced(self):
        # A repo running the substrate that the portfolio does not list: either an
        # unregistered system or a stale roster. Never silently absorbed.
        complete, _b, _n, cov = self._run(
            {"a": _verdict(), "z": _verdict()}, roster={"a"})
        self.assertFalse(complete)
        self.assertEqual(cov["unknown"], ["z"])

    def test_parked_id_absent_from_roster_is_an_error_not_an_exemption(self):
        # An exemption naming a system the roster does not carry is excusing nothing
        # and has outlived its subject — `ship-the-detector-with-the-capability`.
        complete, _b, _n, cov = self._run(
            {"a": _verdict()}, roster={"a"}, parked={"retired-system"})
        self.assertFalse(complete)
        self.assertEqual(cov["stale_parked"], ["retired-system"])

    @unittest.skipIf(
        (Path(__file__).resolve().parent.parent / "PUBLIC-CUT-RECEIPT.md").is_file(),
        "public cut: portfolio.md ships as the authored template, so the real PARKED set "
        "has no real roster to be checked against")
    def test_shipped_parked_set_is_a_subset_of_the_real_roster(self):
        # Not a fixture — asserts against the REAL portfolio.md and the REAL PARKED
        # constant, so the exemptions can never quietly outlive their systems. This is
        # the production instance of the case above.
        roster = sv.read_roster()
        self.assertEqual(sv.PARKED - roster, set(),
                         "PARKED names a system portfolio.md does not list")


class RosterReadTest(unittest.TestCase):
    """The denominator's source. An empty or unparseable roster must RAISE, never
    return an empty set — an empty denominator makes coverage trivially pass, which
    is the failure this whole item is about, reintroduced one layer down."""

    def _write(self, body):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(body)
        f.close()
        return f.name

    def test_parses_roster_rows_and_drops_the_federation(self):
        p = self._write(
            "| System | Agent | Architect | System ID | Architect ID | Status |\n"
            "|---|---|---|---|---|---|\n"
            "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` | Active |\n"
            "| federation | (none) | Federation Architect | `federation` | `federation-arch` | Active |\n"
            "| sample-svc | (none) | Sample-Svc Architect | `sample-svc` | `sample-svc-arch` | Active |\n")
        self.assertEqual(sv.read_roster(p), {"orbit", "sample-svc"})

    def test_ignores_tables_that_are_not_the_roster(self):
        # portfolio.md carries a second table (folded directories) whose rows must
        # not become phantom members. The architect-id anchor is what excludes them.
        p = self._write(
            "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` | Active |\n"
            "| Directory | Disposition | Owner | Decided | Basis |\n"
            "| `~/old-architect-dir/` | Folded | Example Architect | 2026-07-14 | ADR-0005 |\n")
        self.assertEqual(sv.read_roster(p), {"orbit"})

    def test_unparseable_roster_raises_rather_than_returning_empty(self):
        p = self._write("# portfolio\n\nno table here at all\n")
        with self.assertRaises(sv.RosterUnreadable):
            sv.read_roster(p)

    def test_missing_file_raises(self):
        with self.assertRaises(sv.RosterUnreadable):
            sv.read_roster("/nonexistent/portfolio.md")

    def test_gate_fails_closed_when_the_roster_cannot_be_read(self):
        # The gate must never pass on an unreadable denominator.
        members = {"a": (Path("/fake/a"), {})}
        with mock.patch.object(sv, "locate", lambda roots, rp: members), \
             mock.patch.object(sv, "evaluate_member", lambda repo, cfg: _verdict()), \
             mock.patch.object(sv, "read_roster",
                               mock.Mock(side_effect=sv.RosterUnreadable("boom"))):
            with self.assertRaises(sv.RosterUnreadable):
                sv.fleet_complete([], {})


if __name__ == "__main__":
    unittest.main()
