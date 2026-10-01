"""Tests for curate/standard_version.py — the FEDERATION-side fleet parity tool
(ADR-0047). It imports the shared manifest from standard_check (covered by
test_standard_check.py) and adds the federation-only harness-currency axis and the
parked-member skip.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("standard_version", ROOT / "curate" / "standard_version.py")
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)

# The synthetic-member fixture is shared with test_standard_check.py — one
# authoritative definition, so a new capability marker is a one-place edit (P16).
# A default fixture member is capability-clean at LATEST and harness-stale (its
# session.py carries the markers but not the federation's real bytes), which is
# exactly the two-independent-axes case HarnessCurrencyTest below exercises.
from member_fixture import cfg, make_repo  # noqa: E402


class HarnessCurrencyTest(unittest.TestCase):
    def test_harness_stale_when_session_py_differs_from_federation(self):
        with tempfile.TemporaryDirectory() as d:
            # session.py="x" differs from the federation's real one => harness_stale,
            # even though capability version is clean (a separate axis).
            r = sv.evaluate_member(make_repo(d), cfg())
            self.assertTrue(r["harness_stale"])
            self.assertEqual(r["status"], "clean")

    def test_harness_current_when_session_py_matches_federation(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            (repo / "session.py").write_bytes((ROOT / "session.py").read_bytes())
            r = sv.evaluate_member(repo, cfg())
            self.assertFalse(r["harness_stale"])


class LocateTest(unittest.TestCase):
    def test_parked_members_are_skipped(self):
        # The set is patched, not read: the public copy ships PARKED empty (WI-0449).
        parked_ids = {"parked-a", "parked-b"}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sv, "PARKED", parked_ids):
            base = pathlib.Path(d)
            for sid in sorted(parked_ids) + ["sample-svc"]:
                repo = base / sid
                repo.mkdir()
                (repo / "session.config.json").write_text(
                    '{"architect_id": "%s-arch"}' % sid, encoding="utf-8")
            members = sv.locate([str(base)], {})
            self.assertIn("sample-svc", members)
            for parked in parked_ids:
                self.assertNotIn(parked, members)


if __name__ == "__main__":
    unittest.main()


class StandardBytesTest(unittest.TestCase):
    def test_standard_identity_requires_matching_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self.assertEqual(sv.evaluate_member(repo, cfg())["standard_identity"], "DIFFERS")
            (repo / "STANDARD.md").write_bytes((ROOT / "STANDARD.md").read_bytes())
            self.assertEqual(sv.evaluate_member(repo, cfg())["standard_identity"], "IDENTICAL")
            (repo / "STANDARD.md").unlink()
            self.assertEqual(sv.evaluate_member(repo, cfg())["standard_identity"], "NOT CHECKED")


def _older_canon():
    """The federation's CANON.md as it would have looked one habit ago.

    A REALISTIC stale copy, not a corrupted one, and the distinction is the whole
    point: it must be internally consistent — its tally matches its body, so the
    member-side `d_canon` checksum passes it and the member reads capability-clean —
    while differing from the federation's current bytes. That is precisely the member
    this item is about: a substrate three weeks behind that reads `ok`. Appending junk
    instead would break the tally and prove the wrong thing (the member-side detector
    catching a corrupt file, which `test_standard_check.py` already covers)."""
    text = (ROOT / "CANON.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) principles · (\d+) universal habits\*\*", text)
    assert m, "fixture assumption: CANON.md declares its own tally"
    lines = text.splitlines(keepends=True)
    last_habit = max(i for i, ln in enumerate(lines) if ln.startswith("- **`"))
    del lines[last_habit]
    return "".join(lines).replace(
        m.group(0), f"**{m.group(1)} principles · {int(m.group(2)) - 1} universal habits**")


class SubstrateCurrencyTest(unittest.TestCase):
    """WI-0375 — the currency axis covers the WHOLE byte-identical manifest.

    THE DEFECT, restated so the tests read as answers to it. `CANON.md` was compared by
    nobody on either side: member-side `d_canon` was a bare `.is_file()`, and this file
    compared exactly two things by bytes, `session.py` and `STANDARD.md`. So a member
    whose canon was three weeks behind printed the same word as one that was
    byte-current. The reference only exists here, so the comparison belongs here."""

    # The fixture's `session.py` is deliberately left synthetic in most tests here.
    # The real one is a thin entry point whose capability markers live in `sessionlib/`
    # (which the fixture has none of), so copying it in drops the member from v1.20.0 to
    # v1.1.0 — the status axis would then move for a reason that has nothing to do with
    # the currency axis under test, and the test would be reading its own setup.

    def _current(self, repo, *rels):
        """Give the fixture member the federation's REAL bytes for `rels`."""
        for rel in rels:
            (repo / rel).write_bytes((ROOT / rel).read_bytes())

    def test_a_stale_canon_is_named_and_is_not_ok(self):
        """The item's acceptance, directly: a member whose CANON.md is PRESENT and stale
        must not render the same as a byte-current one. Fails against the old code, which
        reported `harness_stale` and a STANDARD.md verdict and nothing about canon."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self._current(repo, "CANON.md")
            r = sv.evaluate_member(repo, cfg())
            self.assertEqual(r["substrate"]["CANON.md"], "IDENTICAL",
                             "control: a byte-current canon reads current")
            self.assertNotIn("CANON.md", r["substrate_stale"])
            self.assertEqual(r["status"], "clean")

            (repo / "CANON.md").write_text(_older_canon(), encoding="utf-8")
            r = sv.evaluate_member(repo, cfg())
            self.assertIn("CANON.md", r["substrate_stale"])
            self.assertEqual(r["substrate"]["CANON.md"], "DIFFERS")
            # The member's own capability word is deliberately unchanged — staleness is
            # not a capability absence. See `fleet_complete`'s docstring for why.
            self.assertEqual(r["status"], "clean")

    def test_a_stale_canon_blocks_the_rollout_gate(self):
        """...and the threshold: it does not turn the member red, it keeps the ROLLOUT
        from reading complete. A fleet with stale canon in it is not a finished rollout."""
        members = {"m": (pathlib.Path("/fake/m"), {})}
        verdict = {"status": "clean", "detected": sv.LATEST, "runtime": "claude",
                   "note": "", "harness_stale": False,
                   "substrate_stale": ["CANON.md"], "substrate": {"CANON.md": "DIFFERS"}}
        with mock.patch.object(sv, "locate", lambda roots, rp: members), \
             mock.patch.object(sv, "PARKED", set()), \
             mock.patch.object(sv, "evaluate_member", lambda repo, c: verdict):
            complete, blockers, _n, _cov = sv.fleet_complete([], {}, roster={"m"})
        self.assertFalse(complete)
        self.assertIn("m", blockers["substrate"])
        self.assertIn("substrate-stale", "; ".join(sv._blocker_parts(blockers)))

    def test_an_absent_file_is_not_reported_as_stale(self):
        """A member that legitimately carries no STANDARD.md (a CANON-floor repo —
        `push-substrate.REFRESH_ONLY` never introduces one there) must not read stale
        forever on a file the federation has chosen not to ship it. Absence is the
        capability detectors' question, and they answer it."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self._current(repo, "STANDARD.md")
            (repo / "STANDARD.md").unlink()
            r = sv.evaluate_member(repo, cfg())
            self.assertNotIn("STANDARD.md", r["substrate_stale"])
            self.assertEqual(r["substrate"]["STANDARD.md"], "NOT CHECKED")

    def test_harness_stale_is_a_view_on_the_one_computation(self):
        """P16: `harness_stale` is not a second comparison. It is `session.py`'s entry in
        the map — which is what keeps the startup banner's long-standing wording honest
        while the axis underneath it widened."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            r = sv.evaluate_member(repo, cfg())
            self.assertTrue(r["harness_stale"])
            self.assertEqual(r["substrate"]["session.py"], "DIFFERS")
            self._current(repo, "session.py")
            r = sv.evaluate_member(repo, cfg())
            self.assertFalse(r["harness_stale"])
            self.assertEqual(r["substrate"]["session.py"], "IDENTICAL")

    def test_the_federation_is_current_with_itself(self):
        r = sv.evaluate_member(ROOT, cfg())
        self.assertEqual(r["substrate_stale"], [])
        self.assertFalse(r["harness_stale"])


class SubstrateManifestTest(unittest.TestCase):
    """The pin that makes restating the manifest safe.

    `SUBSTRATE_ROOT_FILES` is a copy of `push-substrate.BYTE_IDENTICAL`'s root half,
    restated rather than imported because that module's body imports `gen_settings` and
    the whole `session` harness, and `fleet_status_line` runs inside a SessionStart hook.
    A copy with no pin is how a file added to the push manifest quietly stops being
    checked here — the exact silence WI-0375 is about. This fails the suite instead."""

    def _push_substrate(self):
        sys.path.insert(0, str(ROOT / "curate"))
        spec = importlib.util.spec_from_file_location("ps", ROOT / "curate" / "push-substrate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_subjects_match_the_push_manifest_exactly(self):
        ps = self._push_substrate()
        self.assertEqual(sorted(sv.substrate_subjects()), sorted(ps.BYTE_IDENTICAL),
                         "curate/standard_version.py compares a DIFFERENT set of files "
                         "than curate/push-substrate.py ships. Add the new path to "
                         "SUBSTRATE_ROOT_FILES (or drop it), so shipped and checked stay "
                         "the same list.")

    def test_sessionlib_is_derived_not_listed(self):
        """The package's membership is a fact of the federation's tree, on the same
        argument that keeps it out of BYTE_IDENTICAL: a module shipped tomorrow is
        compared here without anyone editing this file."""
        subjects = sv.substrate_subjects()
        on_disk = sorted(f"sessionlib/{m.name}"
                         for m in (ROOT / "sessionlib").iterdir()
                         if m.suffix == ".py" and m.is_file())
        self.assertTrue(on_disk, "fixture assumption: the federation has a sessionlib/")
        for rel in on_disk:
            self.assertIn(rel, subjects)
            self.assertNotIn(rel, sv.SUBSTRATE_ROOT_FILES)
