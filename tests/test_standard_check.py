"""Tests for standard_check.py — the SHIPPED member-side standard-version self-check
(ADR-0047). This module owns the manifest + detectors + evaluate; the federation
fleet tool (curate/standard_version.py) imports the same core, so these cover both.

Load-bearing properties:
  - detected version is computed from DETECTORS (ground truth), not the declared
    marker (a claim); a claim that outruns the evidence is drift.
  - a claude-hook capability is N/A for a member bound another way (resident /
    non-Claude), never falsely "missing" (ADR-0041).
  - a member below the v1.0.0 floor is named by its FLOOR gap, not a higher
    version's not-yet-rolled capability.
  - the v1.1.0 rollout capability is "carries standard_check.py" (self-checks
    locally), not merely a declared marker.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("standard_check", ROOT / "standard_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

# The synthetic-member fixture is shared with test_standard_version.py — one
# authoritative definition, so a new capability marker is a one-place edit (P16).
from harness_fixture import harness_files  # noqa: E402
from member_fixture import (  # noqa: E402
    CANON_MD, CANON_MD_TRUNCATED, FULL_FILES, HOOK_SETTINGS, HOOK_SETTINGS_V12,
    RESIDENT_SETTINGS, SESSION_PY, STANDARD_MD, STANDARD_REFERENCE_MD,
    cfg, make_repo,
)


class GeneratedSubstrateContentTest(unittest.TestCase):
    """WI-0375 — the three GENERATED-substrate detectors read CONTENT, not existence.

    `d_canon`, `d_standard_section` and `d_standard_reference` were each a bare
    `_exists`, so a member whose CANON.md was an empty file, a stub someone typed, or
    the tail of a half-written push reported the capability PRESENT and the member
    printed `ok` at every session start. Every test below FAILS against that code.

    WHAT THESE DO NOT CLAIM. None of them detects STALENESS, and no member-side
    detector can: it reads only the member's own files and there is no federation
    reference on the member's disk (see `_generated`). The currency half lives in
    `curate/standard_version.py` and is covered by `test_standard_version.py`. Read the
    two suites together or you will read this one as promising more than it does."""

    def _repo(self, d):
        return make_repo(d)

    def test_an_empty_canon_is_not_the_capability(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            (repo / "CANON.md").write_text("", encoding="utf-8")
            self.assertFalse(sc.d_canon(repo, cfg()))
            # And it must move the member's VERDICT, not just the boolean: canon is a
            # v1.0.0 floor capability, so a member without it is below floor, not `ok`.
            self.assertEqual(sc.evaluate(repo, cfg())["status"], "below-floor")

    def test_a_hand_written_stub_is_not_the_capability(self):
        """The literal shape the fixture used to ship: a one-character file. It exists,
        so the old detector said yes."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            for name in ("CANON.md", "STANDARD.md", "STANDARD-REFERENCE.md"):
                (repo / name).write_text("x", encoding="utf-8")
            self.assertFalse(sc.d_canon(repo, cfg()))
            self.assertFalse(sc.d_standard_section(repo, cfg()))
            self.assertFalse(sc.d_standard_reference(repo, cfg()))

    def test_a_truncated_canon_fails_its_own_tally(self):
        """The checksum that makes CANON.md stronger than its two siblings: the file
        declares how many principles and habits it contains, so a body that does not
        match its own header is caught without any external reference."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            (repo / "CANON.md").write_text(CANON_MD_TRUNCATED, encoding="utf-8")
            self.assertFalse(sc.d_canon(repo, cfg()))
            (repo / "CANON.md").write_text(CANON_MD, encoding="utf-8")
            self.assertTrue(sc.d_canon(repo, cfg()))

    def test_a_file_without_the_generated_banner_is_not_the_capability(self):
        """A member's copy is the federation's OUTPUT. Something carrying the right
        headings and no banner is a file someone wrote, which is the hand-edit every one
        of these headers exists to forbid."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            (repo / "STANDARD.md").write_text(
                STANDARD_MD.replace("do not edit by hand.", "edit freely."),
                encoding="utf-8")
            self.assertFalse(sc.d_standard_section(repo, cfg()))

    def test_a_banner_with_no_body_is_a_dangling_pointer_that_resolves(self):
        """`d_standard_reference`'s stated job is "is the pointer dangling?". A stub at
        the end of the pointer is a dangling pointer that happens to resolve — the case
        presence-only could not tell from the real reference tier."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            (repo / "STANDARD-REFERENCE.md").write_text(
                "# Standard role-doc section — reference\n\n"
                "> **GENERATED — do not edit by hand.** The reference tier.\n",
                encoding="utf-8")
            self.assertFalse(sc.d_standard_reference(repo, cfg()))
            (repo / "STANDARD-REFERENCE.md").write_text(STANDARD_REFERENCE_MD, encoding="utf-8")
            self.assertTrue(sc.d_standard_reference(repo, cfg()))

    def test_the_federations_own_files_pass_all_three(self):
        """THE CONTROL, and the one that would have caught this going in. These are
        FLOOR detectors — `canon` and `standard-section` are v1.0.0 — so a predicate
        the real generated artifacts fail would read the entire fleet, and this repo,
        below floor at the next session start. Asserted against the live files, not a
        fixture, because the fixture is the thing being trusted everywhere else."""
        self.assertTrue(sc.d_canon(ROOT, cfg()))
        self.assertTrue(sc.d_standard_section(ROOT, cfg()))
        self.assertTrue(sc.d_standard_reference(ROOT, cfg()))

    def test_an_older_but_correct_generated_file_still_passes(self):
        """A member one push behind carries a file that is CORRECT and not current. The
        detectors must pass it — they are capability checks, and staleness is the
        federation-side axis's question. A predicate pinned to today's headings or
        today's tally would turn every behind-but-working member below floor, which is a
        guard firing on correct code."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            (repo / "CANON.md").write_text(
                CANON_MD.replace("**2 principles · 1 universal habits**",
                                 "**1 principles · 1 universal habits**")
                        .replace("- **P2 `second-principle`** — Two.\n", ""),
                encoding="utf-8")
            (repo / "STANDARD.md").write_text(
                STANDARD_MD.replace("## Session-start protocol", "## Session start"),
                encoding="utf-8")
            self.assertTrue(sc.d_canon(repo, cfg()))
            self.assertTrue(sc.d_standard_section(repo, cfg()))
            self.assertEqual(sc.evaluate(repo, cfg())["status"], "clean")


class ManifestTest(unittest.TestCase):
    def test_required_set_is_cumulative(self):
        floor = sc.required_set("1.0.0")
        v11 = sc.required_set("1.1.0")
        v12 = sc.required_set("1.2.0")
        v13 = sc.required_set("1.3.0")
        self.assertIn("announce-stamp", floor)
        self.assertNotIn("rollout-selfcheck", floor)
        self.assertEqual(set(v11) - set(floor), {"rollout-selfcheck"})
        self.assertEqual(set(v12) - set(v11), {"session-journals"})
        self.assertEqual(set(v13) - set(v12), {"heartbeat-on-tooluse", "lazy-start"})
        v14 = sc.required_set("1.4.0")
        self.assertEqual(set(v14) - set(v13), {"cross-lane-coordination"})

    def test_v17_adds_the_work_item_store(self):
        v16 = sc.required_set("1.6.0")
        v17 = sc.required_set("1.7.0")
        self.assertEqual(set(v17) - set(v16), {"work-item-store"})

    def test_v18_adds_the_janitor(self):
        v17 = sc.required_set("1.7.0")
        v18 = sc.required_set("1.8.0")
        self.assertEqual(set(v18) - set(v17), {"janitor"})

    def test_v19_adds_session_work_items(self):
        v18 = sc.required_set("1.8.0")
        v19 = sc.required_set("1.9.0")
        self.assertEqual(set(v19) - set(v18), {"session-work-items"})

    def test_session_work_items_needs_the_harvest_and_the_call(self):
        """ADR-0093's marker, written in the same pass as its capability — unlike the
        three releases before it. The partial that matters is a member holding the
        harvest function whose `end` never passes `work_items=`: it would have the code
        and silently never write the field, which is the exact silent-no-op this surface
        exists to catch."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            self.assertFalse(sc.d_session_work_items(repo, {}), "empty repo")
            (repo / "session.py").write_text("def _harvest_session_work_items(j): pass",
                                             encoding="utf-8")
            self.assertFalse(sc.d_session_work_items(repo, {}),
                             "harvest present but the close never calls it")
            (repo / "session.py").write_text("finalize_journal(work_items=[])",
                                             encoding="utf-8")
            self.assertFalse(sc.d_session_work_items(repo, {}),
                             "call present but nothing to harvest with")
            (repo / "session.py").write_text(
                "def _harvest_session_work_items(j): pass\n"
                "finalize_journal(work_items=_harvest_session_work_items(x))\n",
                encoding="utf-8")
            self.assertTrue(sc.d_session_work_items(repo, {}))

    def test_v110_adds_the_ops_front_door(self):
        v19 = sc.required_set("1.9.0")
        v110 = sc.required_set("1.10.0")
        self.assertEqual(set(v110) - set(v19), {"ops-front-door"})

    def test_ops_front_door_needs_the_verb_the_save_and_the_door(self):
        """WI-0125. Three markers across two files, and none of them is decorative.

        The failure this marks is not an absent feature — it is a capability built for
        one of two namespaces that render identically to every reader, so a member can
        hold `ops-new` and still write to a store nobody reads and save nothing. Pin each
        half failing on its own: a detector that cannot return False would report the
        fleet fine on a question nobody taught it to ask, which is how the three late
        markers before 1.9.0 happened."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            self.assertFalse(sc.d_ops_front_door(repo, {}), "empty repo")
            (repo / "session.py").write_text('sub.add_parser("ops-new")',
                                             encoding="utf-8")
            self.assertFalse(sc.d_ops_front_door(repo, {}),
                             "verb registered but the write still saves nothing")
            (repo / "session.py").write_text(
                'sub.add_parser("ops-new")\n_store_autocommit(OPS_DIRNAME, c, s, v)\n',
                encoding="utf-8")
            self.assertFalse(sc.d_ops_front_door(repo, {}),
                             "harness half complete, but no front door — a lane still "
                             "writes its own frozen store, which is the burned-id defect")
            (repo / "poga").write_text("cmd_work() { :; }\n", encoding="utf-8")
            self.assertFalse(sc.d_ops_front_door(repo, {}),
                             "the WORK front door is not the ops one — the two namespaces "
                             "reading alike is the whole defect")
            (repo / "poga").write_text("cmd_work() { :; }\ncmd_ops() { :; }\n",
                                       encoding="utf-8")
            self.assertTrue(sc.d_ops_front_door(repo, {}))

    def test_janitor_needs_the_verb_reachable_and_implemented(self):
        """WI-0116, and the third late detector in a row — so pin it FAILING first.

        The janitor's only prior detection path was `curate/adopt-runner.py` printing
        `JANITOR N/A` in a nightly log: a true reading in a place nobody consults to ask
        which members can clean up after themselves. A detector that cannot return False
        would leave the surface exactly as wrong while looking fixed, so each half is
        pinned failing on its own. The halves are not decorative: a parser entry without
        `cmd_janitor` is a harness that accepts the verb and dies running it — a partial
        copy is the realistic failure when there is no second file to cross-check."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            self.assertFalse(sc.d_janitor(repo, {}), "empty repo")
            (repo / "session.py").write_text("def cmd_janitor(args): pass",
                                             encoding="utf-8")
            self.assertFalse(sc.d_janitor(repo, {}),
                             "implemented but never registered — unreachable verb")
            (repo / "session.py").write_text('sub.add_parser("janitor")',
                                             encoding="utf-8")
            self.assertFalse(sc.d_janitor(repo, {}),
                             "registered but not implemented — accepts the verb, then dies")
            (repo / "session.py").write_text('sub.add_parser("janitor")\n'
                                             "def cmd_janitor(args): pass\n",
                                             encoding="utf-8")
            self.assertTrue(sc.d_janitor(repo, {}))

    def test_work_item_store_needs_both_halves(self):
        """The detector arrived a release LATE — the store's code shipped inside
        byte-identical substrate while CANON/STANDARD said nothing and this manifest
        carried no marker, so one member held 25 wi-* verbs and reported clean. A
        detector that cannot return False would repeat that exactly, so pin each half
        failing on its own rather than only the happy path."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            self.assertFalse(sc.d_work_item_store(repo, {}), "empty repo")
            (repo / "session.py").write_text('"wi-new" "wi-check"', encoding="utf-8")
            self.assertFalse(sc.d_work_item_store(repo, {}),
                             "harness verbs without the `poga work` front door")
            (repo / "poga").write_text("cmd_lanes() {}", encoding="utf-8")
            self.assertFalse(sc.d_work_item_store(repo, {}),
                             "a poga lacking the work verb is not the front door")
            (repo / "poga").write_text("cmd_work() {}", encoding="utf-8")
            self.assertTrue(sc.d_work_item_store(repo, {}))

    def test_work_item_store_does_not_require_an_existing_store(self):
        """A member that has drawn no items is CONFORMANT — the capability is the
        ability to run a store, not evidence of use. Requiring `work-items/` would
        report an empty backlog as a defect."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / "session.py").write_text('"wi-new" "wi-check"', encoding="utf-8")
            (repo / "poga").write_text("cmd_work() {}", encoding="utf-8")
            self.assertFalse((repo / "work-items").exists())
            self.assertTrue(sc.d_work_item_store(repo, {}))

    def test_v115_adds_the_dispatched_close_authorization(self):
        """ADR-0113 / WI-0249 (a). A separate release rather than a fold into 1.14.0,
        because 1.14.0 has ROLLED OUT — the fleet view reads the federation on it and the
        residency brief naming it is already out to the members. The 1.3.0 / 1.13.0 folding
        precedent only licenses a fold when the prior release reached nobody; re-cutting a
        satisfied version leaves that member GREEN on a capability it does not have."""
        v114 = sc.required_set("1.14.0")
        v115 = sc.required_set("1.15.0")
        self.assertIn("dispatched-close-authorization", sc.CAPABILITIES)
        self.assertEqual(set(v115) - set(v114), {"dispatched-close-authorization"})
        self.assertNotIn("dispatched-close-authorization", v114)

    def test_v116_adds_the_counter_renumber(self):
        """WI-0169 / WI-0245. The capability shipped in 2026-08 and its marker did not, on
        the reading that a new row would make already-stale members newly
        non-conformant. The design answer: a member already behind the latest version
        shows amber either way, so the row moves the target inside an amber line it was
        showing anyway and takes nobody from ok to behind.

        A separate release rather than a fold into 1.15.0, on the precedent 1.15.0 itself
        cites: folding is free only when the prior release reached NOBODY, and 1.15.0 is
        already satisfied by the federation."""
        v115 = sc.required_set("1.15.0")
        v116 = sc.required_set("1.16.0")
        self.assertIn("counter-renumber", sc.CAPABILITIES)
        self.assertEqual(set(v116) - set(v115), {"counter-renumber"})
        self.assertNotIn("counter-renumber", v115)

    def test_v117_adds_the_interpreter_pin(self):
        """WI-0328. The land and test verbs ran on whatever `python3` PATH resolved, so a
        newer interpreter refused a tree that is green under an older one and a whole wave
        of lanes parked at the gate (measured 2026-09-10, dispatch D-069e3d).

        Cut as its own row rather than deferred, on the precedent 1.16.0 sets directly
        above: the "it marks members newly behind" objection is answered by the rollout
        having already answered it, and a real capability left out of this manifest is
        invisible to the one surface built to say who has it.

        This capability is the hardest on the list to see from outside, which is the
        argument FOR the row rather than against it: `session.py` guards the import so a
        member without `interpreter.py` still starts, correctly, and therefore looks
        exactly like a member with a working pin — right up until two machines disagree
        about a verdict."""
        v116 = sc.required_set("1.16.0")
        v117 = sc.required_set("1.17.0")
        self.assertIn("interpreter-pin", sc.CAPABILITIES)
        self.assertEqual(set(v117) - set(v116), {"interpreter-pin"})
        self.assertNotIn("interpreter-pin", v116)

    def test_this_repo_carries_the_interpreter_pin(self):
        """The federation runs the harness it ships."""
        self.assertTrue(sc.d_interpreter_pin(ROOT, None))

    def test_the_pin_reads_absent_on_each_half_alone(self):
        """The marker must be able to say BEHIND, and it has TWO ways to be half-true.

        A member that received `interpreter.py` from a push whose `session.py` did not
        land carries the module and never calls it; a member whose `session.py` calls
        `ensure` without the module falls through its own try/except every time. Both
        run, both look fine, and neither has a pin. A detector that answered `True` to
        either would certify exactly the state this item exists to end."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            (repo / "sessionlib").mkdir()

            # Module present, entry point never calls it.
            (repo / "interpreter.py").write_text("def ensure(root):\n    pass\n",
                                                 encoding="utf-8")
            (repo / "session.py").write_text("import sessionlib\n", encoding="utf-8")
            self.assertFalse(sc.d_interpreter_pin(repo, None),
                             "a pin that nothing invokes read as present")

            # Entry point calls it, module never arrived.
            (repo / "interpreter.py").unlink()
            (repo / "session.py").write_text("_interpreter.ensure(_ROOT)\n",
                                             encoding="utf-8")
            self.assertFalse(sc.d_interpreter_pin(repo, None),
                             "a call into a module the member does not have read as "
                             "present")

    def test_this_repo_carries_the_counter_renumber(self):
        """The federation runs the harness it ships — `ship-the-detector-with-the-capability`
        cutting the other way: a detector that cannot see the capability in the one repo
        known to carry it is measuring its own spelling."""
        self.assertTrue(sc.d_counter_renumber(ROOT, None))

    def test_the_pre_wi0169_harness_reads_absent(self):
        """The marker must be able to say BEHIND. Two subjects, because they fail
        differently — and the second is the one this release exists to catch.

        First: a harness with the counter registry but no renumberer at all, which is what
        every member below v1.16.0 actually holds.

        Second: the REAL `session.py` with the row BINDING stripped and the renumberer left
        in place. That member has working remap code the land can never reach, because
        `_auto_renumber_collisions` skips every row whose `renumberer` is None. A marker
        satisfied by the function alone would certify it as carrying a capability that
        remaps nothing — the silent-no-op shape this manifest exists to catch."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            pre_wi0169 = ("class _Counter:\n"
                          "def _counters():\n"
                          "def _counter_land_gate(c, tip, parent):\n")
            (repo / "session.py").write_text(pre_wi0169, encoding="utf-8")
            self.assertFalse(sc.d_counter_renumber(repo, {}),
                             "a counter registry with no renumberer is not this capability")
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            real = (ROOT / "session.py").read_text(encoding="utf-8")
            unbound = real.replace("renumberer=_store_renumber_batch", "renumberer=None")
            (repo / "session.py").write_text(unbound, encoding="utf-8")
            self.assertFalse(sc.d_counter_renumber(repo, {}),
                             "the renumberer present but bound to no row remaps nothing — "
                             "it must read behind, not conformant")

    def test_this_repo_carries_the_dispatched_close_authorization(self):
        """The federation runs the harness it ships. `ship-the-detector-with-the-capability`
        cuts both ways: a detector that cannot see the capability in the one repo known to
        carry it is measuring its own spelling."""
        self.assertTrue(sc.d_dispatched_close_authorization(ROOT, None))

    def test_the_pre_adr0113_harness_reads_absent(self):
        """The marker must be able to say BEHIND. Members hold the v1.6.0 close guard — the
        flag, the receipt field, the LANE exemption — and that population is what this
        release is measured against, so a detector matching it would certify the fleet as
        already carrying a capability nobody has pulled.

        Two subjects, because they fail differently. The first is the old guard as a member
        holds it. The second is the REAL `session.py` with ADR-0113's names stripped: 22k
        lines of dispatch machinery that predate this capability, which is what catches a
        marker generic enough to be satisfied by ADR-0104-era code."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            old_guard = ('"--confirm"\n"close-confirm"\n'
                         "on_lane = _on_worktree_lane()\n"
                         'if not on_lane and not (getattr(args, "confirm", None) or "")\n'
                         'did = os.environ.get("POGA_DISPATCH", "").strip()\n'
                         "rec = _dispatch_read(did)\n")
            (repo / "session.py").write_text(old_guard, encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "the v1.6.0 lane-keyed close guard is not this capability")
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            real = (ROOT / "session.py").read_text(encoding="utf-8")
            pre = (real.replace("_dispatched_close_authorization", "_pre_adr0113_close")
                       .replace("confirm=confirm", "confirm=getattr(args, 'confirm', None)"))
            (repo / "session.py").write_text(pre, encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "the harness WITHOUT ADR-0113 must read behind — a marker the "
                             "old file already satisfies measures nothing")

    def test_dispatched_close_authorization_needs_the_gate_wired_and_the_citation_resolved(self):
        """Each half pinned failing on its own, on the `d_trunk_integrate` lesson: a
        function present and never called refuses nothing, and an exemption widened without
        a receipt closes every dispatched session with an empty `close-confirm` — a wider
        hole than the one v1.6.0 opened on lanes, because a dispatch runs unattended."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            sp = repo / "session.py"
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}), "empty repo")

            sp.write_text("def _dispatched_close_authorization():\n"
                          "    return None\n", encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "computed but never consulted — `end` still keys on the lane")

            sp.write_text("rec = _dispatch_read(did)\n"
                          "def _dispatched_close_authorization():\n"
                          '    return os.environ.get("POGA_DISPATCH")\n'
                          "dispatch_confirm = _dispatched_close_authorization()\n"
                          "confirm=confirm or None,\n", encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "the citation is ASSERTED from the environment, not resolved "
                             "against the record — and the file's own pre-ADR-0104 "
                             "`_dispatch_read` call must not stand in for it (ADR-0112 D7)")

            sp.write_text("def _dispatched_close_authorization():\n"
                          "    rec = _dispatch_read(did)\n"
                          "dispatch_confirm = _dispatched_close_authorization()\n",
                          encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "exemption wired, citation never written — a dispatched close "
                             "leaves `close-confirm` empty, which reads in the record like "
                             "the unprompted self-close the guard exists to prevent")

            sp.write_text("def _dispatched_close_authorization():\n"
                          "    rec = _dispatch_read(did)\n"
                          "confirm=confirm or None,\n", encoding="utf-8")
            self.assertFalse(sc.d_dispatched_close_authorization(repo, {}),
                             "citation written, authorization never consulted — the other "
                             "way round, and the half that leaves `end` keyed on the lane "
                             "while the journal looks compliant")

            sp.write_text("def _dispatched_close_authorization():\n"
                          "    rec = _dispatch_read(did)\n"
                          "dispatch_confirm = _dispatched_close_authorization()\n"
                          "confirm=confirm or None,\n", encoding="utf-8")
            self.assertTrue(sc.d_dispatched_close_authorization(repo, {}))

    # --- 1.18.0: the unattended close (WI-0331) ---------------------------------

    def test_v118_adds_the_unattended_close_authorization(self):
        """A separate release rather than a fold into 1.15.0, on the precedent 1.15.0
        itself cites and 1.16.0 re-applies: folding is free only when the prior release
        reached NOBODY, and 1.15.0 is already satisfied by the federation. Re-cutting it
        would leave that member reading GREEN on a capability it does not yet have."""
        v117 = sc.required_set("1.17.0")
        v118 = sc.required_set("1.18.0")
        self.assertIn("unattended-close-authorization", sc.CAPABILITIES)
        self.assertEqual(set(v118) - set(v117), {"unattended-close-authorization"})
        self.assertNotIn("unattended-close-authorization", v117)

    def test_v119_adds_the_false_green_guard(self):
        """WI-0344. Separate again, on the precedent every row since 1.13.0 cites:
        folding is free only when the prior release reached NOBODY, and 1.18.0 is
        already satisfied by the federation.

        `claude-hook`, not `all`, and the scope is the substance of the row. The
        guard only ever fires through the PreToolUse binding, so a resident member
        carrying the function has the file and not the capability — which is why
        the detector requires the wired hook too, and why a detector satisfied by
        the source alone would certify the gap it exists to show."""
        v118 = sc.required_set("1.18.0")
        v119 = sc.required_set("1.19.0")
        self.assertIn("false-green-guard", sc.CAPABILITIES)
        self.assertEqual(set(v119) - set(v118), {"false-green-guard"})
        self.assertNotIn("false-green-guard", v118)
        self.assertEqual(sc.CAPABILITIES["false-green-guard"][2], "claude-hook")

    def test_v120_adds_the_standard_reference_tier(self):
        """WI-0208 / ADR-0136. Separate again, and with more force than usual: 1.19.0 is
        itself mid-rollout, so folding would let a member that has neither report on one.

        `all`, not `claude-hook` — the reference tier is a delivered FILE, and every
        member is expected to hold it whatever runtime it runs. Presence on disk is the
        whole capability here, because the split is a delivery decision: there is no
        second half to wire, and a detector asking for one would never go green.

        NOT IN THE FLOOR. `required_set` walks RELEASES cumulatively and the 1.0.0 floor
        is what makes a member BELOW-FLOOR rather than merely behind. In the floor this
        key would flip every existing member to below-floor the moment it landed, for a
        file none of them can have until the push runs — and that push is sequenced AFTER
        the consolidation on purpose. `behind` is the honest reading in between."""
        v119 = sc.required_set("1.19.0")
        v120 = sc.required_set("1.20.0")
        self.assertIn("standard-reference", sc.CAPABILITIES)
        self.assertEqual(set(v120) - set(v119), {"standard-reference"})
        self.assertNotIn("standard-reference", v119)
        self.assertEqual(sc.CAPABILITIES["standard-reference"][2], "all")
        self.assertNotIn("standard-reference", dict(sc.RELEASES)["1.0.0"]["add"])
        # The LATEST pin lives on the NEWEST row and moves with each release — a member
        # is measured against `sc.LATEST`, so a manifest that grew a row the version
        # never followed would report the whole fleet current while a real capability
        # goes unmeasured. (It was stranded on the 1.18.0 row until WI-0208 moved it.)
        self.assertEqual(sc.LATEST, "1.20.0")

    def test_the_false_green_detector_needs_the_hook_as_well_as_the_code(self):
        """WI-0344, and pinned FAILING on each half first, like every detector since
        1.9.0. A detector satisfied by the shipped function alone would read GREEN
        for a member whose Bash calls nothing guards, which is the shape
        `ship-the-detector-with-the-capability` names exactly: an exemption that
        defaults to pass does not merely fail to detect a gap, it certifies it."""
        code = "def false_green_violation(cmd):\n    return None\n"
        wired = '{"hooks": {"PreToolUse": [{"command": "session.py check-bash"}]}}'
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / ".claude").mkdir()
            settings = repo / ".claude" / "settings.json"

            self.assertFalse(sc.d_false_green_guard(repo, {}), "empty repo")

            # The hook, with no guard behind it: check-bash is wired and denies
            # nothing of this class. This is every member TODAY.
            (repo / "session.py").write_text("def other(): pass", encoding="utf-8")
            settings.write_text(wired, encoding="utf-8")
            self.assertFalse(sc.d_false_green_guard(repo, {}),
                             "hook wired but the guard is not in the harness")

            # The code, with nothing wired to run it: a resident/non-Claude member
            # that took the harness has the file and not the capability, which is
            # why this row is scoped claude-hook.
            (repo / "session.py").write_text(code, encoding="utf-8")
            settings.write_text("{}", encoding="utf-8")
            self.assertFalse(sc.d_false_green_guard(repo, {}),
                             "guard shipped but nothing invokes it")

            settings.write_text(wired, encoding="utf-8")
            self.assertTrue(sc.d_false_green_guard(repo, {}))

    def test_the_full_synthetic_member_carries_the_false_green_guard(self):
        """The fixture is the definition of a conformant member, so a capability it
        does not carry cannot be clean at LATEST. Named here rather than left
        implicit in `test_full_member_is_clean_at_latest`: when that test goes red
        the reader should be able to tell a missing fixture marker from a broken
        detector without bisecting the manifest."""
        self.assertIn("def false_green_violation", SESSION_PY)

    def test_this_repo_carries_the_unattended_close_authorization(self):
        """The federation runs the harness it ships — and a detector that cannot see the
        capability in the one repo known to carry it is measuring its own spelling."""
        self.assertTrue(sc.d_unattended_close_authorization(ROOT, None))

    def test_the_pre_wi0331_harness_reads_absent(self):
        """The marker must be able to say BEHIND, and the subject that catches a lazy one
        is not an empty repo — it is the REAL harness with this capability's names
        stripped: 20k+ lines carrying ADR-0113's dispatch exemption, the `--confirm` flag
        and the `close-confirm` field, none of which is this row. A detector satisfied by
        that file would certify the entire fleet for a capability nobody has pulled."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            # The WHOLE harness, not the thin entry point: `session.py` has been a
            # forwarder since the package split, so a subject built from it alone reads
            # absent for every capability and would prove nothing about this one.
            for rel in harness_files():
                src = (ROOT / rel).read_text(encoding="utf-8")
                dst = repo / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(
                    src.replace("_unattended_close_authorization", "_pre_wi0331_close"),
                    encoding="utf-8")
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}),
                             "the harness WITHOUT WI-0331 must read behind")
            # ...and the sibling row must be unaffected: the two capabilities are
            # independent, so stripping one must not take the other down with it. This
            # also proves the subject is a real harness rather than an empty directory,
            # which is what makes the assertion above mean anything.
            self.assertTrue(sc.d_dispatched_close_authorization(repo, {}))

    def test_the_dispatch_exemption_alone_does_not_satisfy_this_row(self):
        """The exact state every member is in today: ADR-0113 wired, and the unattended
        adoption path still unable to close. If a member holding only the dispatch half
        read present here, the one surface built to say who can run an unattended
        adoption would answer 'everyone' about a fleet where the answer is 'nobody'."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / "session.py").write_text(
                "def _dispatched_close_authorization():\n"
                "    rec = _dispatch_read(did)\n"
                "dispatch_confirm = _dispatched_close_authorization()\n"
                "confirm=confirm or None,\n", encoding="utf-8")
            self.assertTrue(sc.d_dispatched_close_authorization(repo, {}))
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}))

    def test_unattended_close_authorization_needs_computing_consulting_and_labelling(self):
        """Each half pinned failing on its own, on the `d_trunk_integrate` lesson."""
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            sp = repo / "session.py"
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}), "empty repo")

            sp.write_text("def _unattended_close_authorization():\n"
                          "    rec = _unattended_run_read(rid)\n"
                          '    return "NO HUMAN CONFIRMED THIS CLOSE"\n',
                          encoding="utf-8")
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}),
                             "computed but never consulted — `end` still refuses the "
                             "unattended close, and the function shipped beside it "
                             "changes nothing")

            sp.write_text("rec = _unattended_run_read(rid)\n"
                          "def _unattended_close_authorization():\n"
                          '    return os.environ.get("POGA_UNATTENDED_RUN")\n'
                          "unattended_confirm = _unattended_close_authorization()\n",
                          encoding="utf-8")
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}),
                             "the citation is ASSERTED from the environment, not resolved "
                             "against the run record — and a read elsewhere in the file "
                             "must not stand in for one inside the function (ADR-0112 D7)")

            sp.write_text("def _unattended_close_authorization():\n"
                          "    rec = _unattended_run_read(rid)\n"
                          '    return f"unattended run {rid}"\n'
                          "unattended_confirm = _unattended_close_authorization()\n",
                          encoding="utf-8")
            self.assertFalse(sc.d_unattended_close_authorization(repo, {}),
                             "resolved and wired, but the receipt does not say a machine "
                             "closed it — an unattended close that reads like any other "
                             "is the audit corruption this row exists to prevent, arrived "
                             "at by omission instead of by a fabricated quote")

            sp.write_text("def _unattended_close_authorization():\n"
                          "    rec = _unattended_run_read(rid)\n"
                          '    return "NO HUMAN CONFIRMED THIS CLOSE"\n'
                          "unattended_confirm = _unattended_close_authorization()\n",
                          encoding="utf-8")
            self.assertTrue(sc.d_unattended_close_authorization(repo, {}))

    def test_a_member_carrying_the_1_18_0_markers_reads_present(self):
        """The shared fixture is the subject on purpose: it is the one place a capability
        marker is taught (P16), so this pins that the fixture and the manifest moved
        together rather than leaving every `LATEST` test one release behind."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self.assertTrue(sc.d_unattended_close_authorization(repo, {}))
            self.assertEqual(sc.evaluate(repo, cfg())["caps"]
                             ["unattended-close-authorization"], "present")

    def test_a_member_carrying_the_1_15_0_markers_reads_present(self):
        """The shared fixture is the subject on purpose: it is the one place a capability
        marker is taught (P16), so this pins that the fixture and the manifest moved
        together rather than leaving every `LATEST` test one release behind."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self.assertTrue(sc.d_dispatched_close_authorization(repo, {}))
            self.assertEqual(sc.evaluate(repo, cfg())["caps"]
                             ["dispatched-close-authorization"], "present")

    def test_retired_set_empty_without_removals(self):
        self.assertEqual(sc.retired_set("1.1.0"), [])

    def test_version_norm_and_cmp(self):
        self.assertEqual(sc._norm("v1.2.3"), "1.2.3")
        self.assertIsNone(sc._norm("nonsense"))
        self.assertEqual(sc._cmp("1.1.0", "1.0.0"), 1)
        self.assertEqual(sc._cmp("1.0.0", "1.1.0"), -1)
        self.assertEqual(sc._cmp("1.0.0", "1.0.0"), 0)


class EvaluateTest(unittest.TestCase):
    def test_full_member_is_clean_at_latest(self):
        with tempfile.TemporaryDirectory() as d:
            r = sc.evaluate(make_repo(d), cfg())
            # sc.LATEST, not a literal: this test asserts "a fully-equipped member is
            # current", which is a property of the manifest, not of one release number.
            self.assertEqual(r["detected"], sc.LATEST)
            self.assertEqual(r["caps"]["session-journals"], "present")
            self.assertEqual(r["caps"]["heartbeat-on-tooluse"], "present")
            self.assertEqual(r["caps"]["cross-lane-coordination"], "present")
            self.assertEqual(r["caps"]["worktree-lanes"], "present")
            self.assertEqual(r["status"], "clean")

    def test_hook_member_without_tooluse_heartbeat_is_behind(self):
        # A v1.2.0-era binding (Stop heartbeat only): the PreToolUse heartbeat
        # detector must NOT be fooled by the Stop hook's identical command.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, settings=HOOK_SETTINGS_V12)
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["caps"]["heartbeat-on-tooluse"], "absent")
            self.assertEqual(r["detected"], "1.2.0")
            self.assertEqual(r["status"], "behind")

    def test_without_selfcheck_module_detects_floor_only(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, files=[f for f in FULL_FILES if f != "standard_check.py"])
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["detected"], "1.0.0")     # rollout-selfcheck absent
            self.assertEqual(r["status"], "behind")
            self.assertIn("1.0.0", r["note"])

    def test_missing_floor_file_is_below_floor_named_by_floor_gap(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, files=[f for f in FULL_FILES if f != "ROADMAP.md"])
            r = sc.evaluate(repo, cfg())
            self.assertIsNone(r["detected"])
            self.assertEqual(r["status"], "below-floor")
            self.assertIn("roadmap", r["note"])
            self.assertNotIn("rollout-selfcheck", r["note"])

    def test_a_declared_standard_version_is_ignored_entirely(self):
        """ADR-0068 retired the declared axis. A leftover `standard_version` in a
        member's config (one member set one by hand; others never did) must have NO
        effect on the verdict — not believed, not compared, not reported. Asserted
        with a wildly wrong claim so a re-introduced comparison could not pass."""
        with tempfile.TemporaryDirectory() as d:
            stale = sc.evaluate(make_repo(d), cfg(standard_version="v2.0.0"))
            absent = sc.evaluate(make_repo(d), cfg())
            self.assertEqual(stale["detected"], sc.LATEST)
            self.assertEqual(stale["status"], "clean")
            self.assertNotIn("2.0.0", stale["note"])
            self.assertNotIn("declared", stale)
            self.assertEqual(stale["status"], absent["status"])
            self.assertEqual(stale["note"], absent["note"])

    def test_resident_binding_makes_hook_caps_na_not_missing(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, settings=RESIDENT_SETTINGS)
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["caps"]["settings-harness-wired"], "n/a")
            self.assertEqual(r["caps"]["announce-stamp"], "n/a")
            self.assertEqual(r["caps"]["heartbeat-on-tooluse"], "n/a")
            # worktree-lanes is claude-hook scope too — a resident binding launches
            # lanes (if at all) its own way, so this reads n/a, never "missing".
            self.assertEqual(r["caps"]["worktree-lanes"], "n/a")
            self.assertEqual(r["detected"], sc.LATEST)
            self.assertEqual(r["status"], "clean")

    def test_hook_member_missing_announce_is_a_real_finding(self):
        with tempfile.TemporaryDirectory() as d:
            settings = '{"hooks": "session.py start", "pre": "session.py check-bash"}'
            repo = make_repo(d, settings=settings)
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["caps"]["announce-stamp"], "absent")
            self.assertIsNone(r["detected"])
            self.assertEqual(r["status"], "below-floor")
            self.assertIn("announce-stamp", r["note"])


class ArchitectSettingsPathTest(unittest.TestCase):
    """The read-side declaration (session 98). A resident-runtime member's repo-root
    settings can belong to someone ELSE — the resident runtime's own lockdown — so
    reading the default measures the wrong file, finds no harness reference, and
    exempts the member from every hook-scoped capability. It then reports "current"
    on the strength of checks that never ran. The fix is a declaration, not an
    inference, because an exemption that defaults to PASS ages badly.
    """

    def _member(self, tmp):
        """The resident-runtime shape: repo-root settings belong to the runtime persona; the
        Architect's own settings live OUTSIDE the repo, one level up."""
        base = pathlib.Path(tmp)
        repo = base / "repo"
        repo.mkdir()
        make_repo(str(repo), settings=RESIDENT_SETTINGS)   # persona's file at the root
        arch = base / "architect" / ".claude"
        arch.mkdir(parents=True)
        (arch / "settings.json").write_text(HOOK_SETTINGS, encoding="utf-8")
        return repo

    def test_without_the_declaration_the_member_is_exempted_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            r = sc.evaluate(repo, cfg())
            self.assertFalse(sc._hook_bound(repo, cfg()))
            self.assertEqual(r["caps"]["announce-stamp"], "n/a")
            self.assertEqual(r["caps"]["worktree-lanes"], "n/a")
            self.assertEqual(r["status"], "clean")          # the false pass

    def test_with_the_declaration_the_real_binding_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            c = cfg(architect_settings_path="../architect/.claude/settings.json")
            self.assertTrue(sc._hook_bound(repo, c))
            r = sc.evaluate(repo, c)
            # `worktree-lanes` is deliberately NOT in this list: an off-repo settings
            # path is the resident-runtime shape, and that capability
            # also requires a reachable pad launch, which this fixture's stub files do
            # not carry. Covered by tests/test_resident_runtime_lane.py::LanesDetectorTest.
            for k in ("settings-harness-wired", "announce-stamp", "heartbeat-on-tooluse"):
                self.assertEqual(r["caps"][k], "present", k)

    def test_the_declaration_can_turn_a_false_pass_into_a_real_finding(self):
        """The whole point: measuring can only ever be worse than exempting, and
        that is the desired direction. The live case — an Architect settings file
        predating the announce hook — must surface as below-floor, not 'current'."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            arch = pathlib.Path(d) / "architect" / ".claude" / "settings.json"
            arch.write_text('{"hooks": {"SessionStart": "session.py start"}, '
                            '"pre": "session.py check-bash"}', encoding="utf-8")
            c = cfg(architect_settings_path="../architect/.claude/settings.json")
            r = sc.evaluate(repo, c)
            self.assertEqual(r["caps"]["announce-stamp"], "absent")
            self.assertEqual(r["status"], "below-floor")

    def test_a_declared_but_unreadable_path_does_not_fall_back(self):
        """Falling back to the repo-root default would restore the exact false-pass
        this field exists to kill — a typo'd declaration must read as unbound, which
        surfaces, rather than as 'bound some other way', which exempts."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            c = cfg(architect_settings_path="../nope/.claude/settings.json")
            self.assertEqual(sc._settings(repo, c), "")
            self.assertFalse(sc._hook_bound(repo, c))

    def test_an_absent_declaration_preserves_todays_behaviour_for_every_member(self):
        """Back-compat: the kit member (settings at the repo root, no declaration)
        must be unaffected — this changed reading semantics fleet-wide."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)                    # ordinary hook-bound kit member
            self.assertTrue(sc._hook_bound(repo, cfg()))
            self.assertEqual(sc.evaluate(repo, cfg())["detected"], sc.LATEST)


class SettingsPathPrecedenceTest(unittest.TestCase):
    """D5 — one declared location, read through a documented precedence.

    Once a member joins the settings push channel, `settings_path` names the file
    the federation GENERATES; `architect_settings_path` was the read-side-only
    stopgap for a member not on that channel. Two keys describing one file is a
    drift seam (P16), so the reader has a stated order instead of a second opinion.
    """

    def _member(self, tmp):
        """A resident-runtime member mid-migration: the runtime's lockdown at the
        repo root, the Architect's real settings tracked IN-repo at architect/."""
        base = pathlib.Path(tmp)
        repo = base / "repo"
        repo.mkdir()
        make_repo(str(repo), settings=RESIDENT_SETTINGS)
        inrepo = repo / "architect" / ".claude"
        inrepo.mkdir(parents=True)
        (inrepo / "settings.json").write_text(HOOK_SETTINGS, encoding="utf-8")
        return repo

    def test_settings_path_wins_when_it_names_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            c = cfg(settings_path="architect/.claude/settings.json")
            self.assertTrue(sc._hook_bound(repo, c))
            self.assertEqual(sc.evaluate(repo, c)["detected"], sc.LATEST)

    def test_false_settings_path_falls_through_to_the_read_side_declaration(self):
        """A resident-runtime member not yet migrated: opted out of the write channel, declaring where to read.
        Flipping the reader must not un-measure the member before it migrates."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            c = cfg(settings_path=False,
                    architect_settings_path="architect/.claude/settings.json")
            self.assertTrue(sc._hook_bound(repo, c))

    def test_settings_path_beats_a_stale_read_side_declaration(self):
        """The seam this collapses: after migrating, a left-behind
        `architect_settings_path` must not keep steering the reader at the old copy
        the channel no longer maintains."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            stale = pathlib.Path(d) / "architect" / ".claude"
            stale.mkdir(parents=True)
            stale.joinpath("settings.json").write_text(
                '{"hooks": {"SessionStart": "session.py start"}}', encoding="utf-8")
            c = cfg(settings_path="architect/.claude/settings.json",
                    architect_settings_path="../architect/.claude/settings.json")
            r = sc.evaluate(repo, c)
            # announce-stamp is the witness: it is present ONLY in the in-repo file,
            # absent from the stale off-repo one, so this asserts which file was read.
            # (Not `detected == LATEST`: the off-repo declaration also marks the
            # resident-runtime shape, whose lanes capability needs a pad launch this
            # fixture has no stub for.)
            self.assertEqual(r["caps"]["announce-stamp"], "present")

    def test_absent_both_is_the_kit_default(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            self.assertTrue(sc._hook_bound(repo, cfg()))
            self.assertEqual(sc.evaluate(repo, cfg())["detected"], sc.LATEST)

    def test_a_declared_but_unreadable_settings_path_does_not_fall_back(self):
        """Same no-silent-downgrade rule the read-side key already has: a typo must
        surface as unbound, never as 'bound some other way'."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member(d)
            c = cfg(settings_path="architect/nope/settings.json")
            self.assertEqual(sc._settings(repo, c), "")
            self.assertFalse(sc._hook_bound(repo, c))


if __name__ == "__main__":
    unittest.main()
