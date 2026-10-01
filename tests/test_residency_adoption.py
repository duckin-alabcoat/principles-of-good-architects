"""WI-0209 — the federation can SEE whether the fleet answered the residency ask.

THE GAP THESE TESTS CLOSE. ADR-0106 D1 asks every member to declare a `residency`
block; D5 forbids the federation from declaring it for them. `standard_check.d_residency`
therefore checks only that a member's SUBSTRATE can carry a declaration and says so in
its own docstring. So after the brief went to every member on 2026-09-13, the only
way to learn how many had answered was to open every config by hand — and WI-0209's
acceptance turns on exactly that number.

WHAT THESE PIN, in the order the defect can come back:

  1. UNDECLARED IS NOT "NO PRODUCTION". `{"process": null}` is an answer; a missing block
     is silence. Folding them is the whole reason D1 spends a paragraph on it, and a
     survey that reports "all fine" because nobody objected is worse than no survey.

  2. LABEL-DRIFT IS ITS OWN STATE. A member whose map yields `devbox` looks adopted and
     is not: `residency_state` matches labels by exact string, so declaring `"DevBox"`
     would land that member in `declared-but-unresolvable`. Some members were in this
     shape when the view was built. A survey that reports them as merely `missing` loses
     the fact that explains the later failure.

  3. THE DENOMINATOR IS THE ROSTER. An expected member that cannot be reached is counted
     UNLOCATED, never dropped — otherwise the fleet reads MORE compliant exactly as it
     gets harder to reach (WI-0098's lesson, inherited here rather than re-learned).

  4. THE REFERENCE COMES FROM THE FEDERATION'S CONFIG, NOT A CONSTANT. A `"DevBox"`
     literal in the curate script would be a second declaration of a fact
     `session.config.json` already holds, and it would not learn about a fourth machine.

  5. NOT-CHECKED IS NOT CLEAN. With no reference labels, the status line must say it
     verified nothing — an exemption that defaults to pass certifies the gap it was
     built to detect.

  6. CONTRADICTION IS UNREACHABLE, BY CONSTRUCTION. A survey on one machine cannot see
     another's disk, so it passes `production_root_present=None` and must never emit
     `declared-and-contradicted` from an unobserved root.

  7. RESIDENCY DOES NOT GATE. `fleet_complete` is the standard-VERSION predicate. An
     undeclared member has the capability; making it fail the rollout gate would report
     a member as BEHIND on something it demonstrably carries.

  8. THE TABLE DOES NOT REPEAT ITSELF. The shared UNDECLARED prose is printed once, not
     once per row — `residency_state`'s wording is a single member's whole message and a
     fourteen-row wall of it is unreadable, which is a documented user-visible failure
     shape, not a style preference.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
# `tests/` explicitly, so `member_fixture` resolves under `-m unittest tests.<mod>` too.
# `unittest discover -s tests` adds it for free and the sibling suites lean on that; a
# file that only imports the shared fixture under ONE of the two entry points is a test
# that passes where it is run and errors where the next person runs it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
_spec = importlib.util.spec_from_file_location(
    "standard_version", ROOT / "curate" / "standard_version.py")
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)

REFERENCE = {"DevBox", "Laptop", "Runner"}
# Every row is driven from an injected machine, never the real one. A test that read the
# machine under it would pass on the box that wrote it and prove nothing about the other
# — the discipline `residency_state`'s own docstring holds, inherited here.
HERE = "DevBox"


def member(residency=None, labels=("Laptop", "Runner"), architect_id="x-arch"):
    """A synthetic member config: a machine_map yielding `labels`, plus an optional
    residency block. Keys are synthetic ComputerNames — this axis reads the map's
    VALUES, and a test that pinned real key spellings would be testing the fixture."""
    cfg = {"architect_id": architect_id,
           "machine_map": {"host-%d" % i: l for i, l in enumerate(labels)}}
    if residency is not None:
        cfg["residency"] = residency
    return cfg


def write_member(base, sid, cfg):
    repo = pathlib.Path(base) / sid
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "session.config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo


class UndeclaredIsNotNoProductionTest(unittest.TestCase):
    """(1) Silence and "I have no production mode" are different answers."""

    def test_missing_block_reads_undeclared(self):
        state, _why, _l, _m, _d = sv.residency_row(member(), REFERENCE, HERE)
        self.assertEqual(state, "undeclared")

    def test_explicit_nulls_read_declared(self):
        state, _why, _l, _m, _d = sv.residency_row(
            member({"process": None, "data": None}), REFERENCE, HERE)
        self.assertEqual(state, "declared")

    def test_the_two_do_not_share_a_count(self):
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "silent", member(architect_id="silent-arch"))
            write_member(d, "answered",
                         member({"process": None, "data": None},
                                architect_id="answered-arch"))
            a = sv.residency_adoption([d], {}, roster={"silent", "answered"})
            self.assertEqual(a["counts"]["declared"], ["answered"])
            self.assertEqual(a["counts"]["undeclared"], ["silent"])


class LabelDriftTest(unittest.TestCase):
    """(2) The row that looks adopted and is not."""

    def test_near_miss_spelling_is_its_own_state(self):
        state, _why, lstate, missing, drift = sv.residency_row(
            member(labels=("Laptop", "Runner", "devbox")), REFERENCE, HERE)
        self.assertEqual(state, "undeclared")
        self.assertEqual(lstate, sv.LABELS_DRIFT)
        self.assertEqual(missing, ["DevBox"])
        self.assertEqual(drift, ["devbox"])

    def test_plain_absence_is_not_reported_as_drift(self):
        _s, _w, lstate, missing, drift = sv.residency_row(
            member(labels=("Laptop", "Runner")), REFERENCE, HERE)
        self.assertEqual(lstate, sv.LABELS_MISSING)
        self.assertEqual(missing, ["DevBox"])
        self.assertEqual(drift, [])

    def test_the_drift_is_what_makes_a_declaration_unresolvable(self):
        # The failure the state exists to predict: this member CAN be told to declare
        # DevBox, and the moment it does, its own resolver rejects it.
        state, _why, _l, _m, _d = sv.residency_row(
            member({"process": "DevBox"}, labels=("Laptop", "Runner", "devbox")),
            REFERENCE, HERE)
        self.assertEqual(state, "declared-but-unresolvable")

    def test_a_full_map_reads_ok(self):
        _s, _w, lstate, missing, drift = sv.residency_row(
            member(labels=("Laptop", "Runner", "DevBox")), REFERENCE, HERE)
        self.assertEqual(lstate, sv.LABELS_OK)
        self.assertEqual((missing, drift), ([], []))


class DenominatorTest(unittest.TestCase):
    """(3) Unreachable is unverified, never passed and never subtracted."""

    def test_an_unreachable_expected_member_is_counted_unlocated(self):
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "here", member(architect_id="here-arch"))
            a = sv.residency_adoption([d], {}, roster={"here", "elsewhere"})
            self.assertEqual(a["counts"]["unlocated"], ["elsewhere"])
            self.assertEqual(sorted(a["counts"]["expected"]), ["elsewhere", "here"])

    def test_a_located_member_outside_the_roster_is_not_counted(self):
        """Found by mutation, not by inspection: swapping the counts' iteration from
        `expected` to `located` survived every other test here. It is the live defect —
        the federation IS located, IS the one config that has declared, and is NOT a
        member of its own fleet, so counting it turns the reference implementation into
        reported fleet adoption. `0 of 14` would print as `1 of 14` with nobody having
        answered."""
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "member", member(architect_id="member-arch"))
            write_member(d, "offroster",
                         member({"process": "Runner"}, architect_id="offroster-arch"))
            a = sv.residency_adoption([d], {}, roster={"member"})
            self.assertEqual(a["counts"]["expected"], ["member"])
            self.assertEqual(a["counts"]["declared"], [])
            self.assertEqual(a["counts"]["undeclared"], ["member"])
            # It is still SHOWN — the row exists so the reference is visible — but it
            # is not counted. Shown and counted are different things.
            self.assertIn("offroster", a["rows"])

    def test_losing_a_member_does_not_raise_the_declared_fraction(self):
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "answered",
                         member({"process": None}, architect_id="answered-arch"))
            reachable = sv.residency_adoption([d], {}, roster={"answered", "gone"})
            self.assertEqual(len(reachable["counts"]["expected"]), 2)
            self.assertEqual(reachable["counts"]["declared"], ["answered"])
            self.assertEqual(reachable["counts"]["unlocated"], ["gone"])


class ReferenceIsReadNotHardcodedTest(unittest.TestCase):
    """(4) The spelling lives in the federation's own config."""

    def test_reference_labels_come_from_the_passed_config(self):
        self.assertEqual(sv.reference_labels({"machine_map": {"a": "Alpha", "b": "Beta"}}),
                         {"Alpha", "Beta"})

    def test_the_federations_own_config_is_the_default_source(self):
        fed = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(sv.reference_labels(), set(fed["machine_map"].values()))

    def test_a_fourth_machine_needs_no_edit_here(self):
        extended = sv.reference_labels(
            {"machine_map": {"a": "Laptop", "b": "Runner", "c": "DevBox", "d": "Rack"}})
        _s, _w, lstate, missing, _d = sv.residency_row(
            member(labels=("Laptop", "Runner", "DevBox")), extended, HERE)
        self.assertEqual(lstate, sv.LABELS_MISSING)
        self.assertEqual(missing, ["Rack"])


class NotCheckedIsNotCleanTest(unittest.TestCase):
    """(5) An exemption that defaults to pass certifies the gap."""

    def test_no_reference_labels_reports_not_checked(self):
        _s, _w, lstate, missing, drift = sv.residency_row(member(), set(), HERE)
        self.assertEqual(lstate, sv.LABELS_NOT_CHECKED)
        self.assertEqual((missing, drift), ([], []))

    def test_the_status_line_says_it_verified_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "here", member(architect_id="here-arch"))
            real = sv.reference_labels
            sv.reference_labels = lambda fed_cfg=None: set()
            try:
                line = sv.residency_status_line([d], {})
            finally:
                sv.reference_labels = real
            self.assertIn("NOT CHECKED", line)
            self.assertNotIn("have declared", line)


class ContradictionIsUnreachableTest(unittest.TestCase):
    """(6) A survey on one machine must not rule on another machine's disk."""

    def test_a_declared_production_data_host_is_not_reported_contradicted(self):
        # Standing on DevBox, reading a member that declares its production data on
        # DevBox, with no production root anywhere near this tmpdir. The single-member
        # resolver CAN reach `declared-and-contradicted` here; the survey must not,
        # because it never looked.
        state, _why, _l, _m, _d = sv.residency_row(
            member({"process": None, "data": "DevBox"},
                   labels=("Laptop", "Runner", "DevBox")),
            REFERENCE, HERE)
        self.assertEqual(state, "declared")

    def test_the_report_declares_the_assumption(self):
        joined = "\n".join(sv.RESIDENCY_CAVEATS)
        self.assertIn("declared-and-contradicted", joined)
        self.assertIn("production_root_present", joined)
        self.assertIn("LABELS, not machine_map KEYS", joined)


class ResidencyDoesNotGateTest(unittest.TestCase):
    """(7) Declaring is the member's act; the rollout gate is about capability."""

    def test_fleet_complete_ignores_the_residency_block(self):
        """DIFFERENTIAL, not absolute. Asserting the fixture is blocker-free would pin
        this test to `standard_check.LATEST` and break on the next capability bump for
        a reason that has nothing to do with residency. The claim is narrower and does
        not decay: adding or removing a `residency` block must not move the gate."""
        import member_fixture

        def blockers_for(residency):
            with tempfile.TemporaryDirectory() as d:
                base = pathlib.Path(d)
                (base / "m").mkdir()
                repo = member_fixture.make_repo(str(base / "m"))
                cfg = member_fixture.cfg(architect_id="sample-svc-arch")
                if residency is not None:
                    cfg["residency"] = residency
                (repo / "session.config.json").write_text(
                    json.dumps(cfg), encoding="utf-8")
                _c, blockers, n, _cov = sv.fleet_complete(
                    [str(base)], {}, roster={"sample-svc"})
                self.assertEqual(n, 1)
                return blockers

        silent = blockers_for(None)
        answered = blockers_for({"process": "Runner", "data": None})
        self.assertEqual(silent, answered)
        # And residency never names itself as a blocker under either shape.
        for b in (silent, answered):
            self.assertNotIn("residency", json.dumps(b))


class ReportShapeTest(unittest.TestCase):
    """(8) The shared explanation is printed once, not once per row."""

    def test_the_undeclared_prose_appears_once(self):
        with tempfile.TemporaryDirectory() as d:
            for sid in ("alpha", "bravo", "charlie"):
                write_member(d, sid, member(architect_id="%s-arch" % sid))
            buf = io.StringIO()
            with redirect_stdout(buf):
                sv.residency_report([d], {})
            out = buf.getvalue()
            self.assertEqual(out.count("is NOT the same as declaring no"), 1)
            for sid in ("alpha", "bravo", "charlie"):
                self.assertIn(sid, out)

    def test_an_unlocated_member_prints_a_distinct_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            write_member(d, "alpha", member(architect_id="alpha-arch"))
            real = sv.coverage
            sv.coverage = lambda located, roster=None: real(
                located, roster={"alpha", "absent"})
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    sv.residency_report([d], {})
            finally:
                sv.coverage = real
            out = buf.getvalue()
            self.assertIn("UNLOCATED", out)
            self.assertIn("unverified, not passed", out)


if __name__ == "__main__":
    unittest.main()
