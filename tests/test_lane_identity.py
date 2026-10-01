"""A lane is NAMED by its session and ADDRESSED by its slot — WI-0157 / ADR-0129.

`poga-N` is an address: drawn lowest-free, freed on teardown, reissued to the next lane,
and numbered per machine. Measured, it referred to three distinct sessions across two
machines in one day. Every lane-facing surface nonetheless printed it in the reading
position as though it were a name, and `poga lanes` printed it and nothing else — so a
person holding two of those surfaces an hour apart could not tell whether they were
looking at the same work.

What is pinned here is the rule and its acceptance drill, not the wording of any one row:

  - the three tiers of `lane_identity`, each the most durable fact actually on hand;
  - that the lane->item join survives the `branch: "main"` that EVERY front-door claim
    carries (ADR-0073), which is the trap a branch-keyed join falls into silently;
  - that a lane which ran two sessions is named for the one a reader could still reach;
  - and the acceptance itself: no bare `poga-N` stands as the PRIMARY identifier in the
    converted renders, while the slot is still present as the address.

The last one is the test that matters. The first three could all pass over a surface that
never called the helper.

stdlib unittest: python3 -m unittest tests.test_lane_identity
"""

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class LaneIdentityTierTest(unittest.TestCase):
    """The three tiers, against a lane dict and a claim index — no repo needed."""

    def setUp(self):
        self._cfg = session.CFG
        session.CFG = dict(session.CFG or {}, architect_id="federation-arch")

    def tearDown(self):
        session.CFG = self._cfg

    def _lane(self, tmp, name="poga-3", sid="20260912T1230Z-devbox-d5b9",
              beat_min=1.0, csid="c-1"):
        path = pathlib.Path(tmp) / name
        if sid is not None:
            d = path / ".session-state"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{csid}.live").write_text(
                json.dumps({"session_id": sid, "machine": "DevBox",
                            "last_beat": _ago(beat_min)}), encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
        # A `Path`, because that is what `_scan_lanes` actually puts in this field
        # (`lanes.py`). A str fixture here hid an AttributeError on the real caller.
        return {"name": name, "path": path}

    def test_tier_one_a_claimed_lane_is_named_machine_and_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            lane = self._lane(tmp)
            got = session.lane_identity(
                lane, {"20260912T1230Z-devbox-d5b9": "WI-0157"})
        self.assertEqual(got, "devbox·WI-0157")
        self.assertNotIn("poga-3", got,
                         "the slot is an address and must not stand as the name")

    def test_tier_two_an_unclaimed_lane_falls_back_to_the_id_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = session.lane_identity(self._lane(tmp), {})
        # The tail is the half of the durable id that two concurrent sessions never
        # share; the timestamp half is the half they do.
        self.assertEqual(got, "devbox·d5b9")

    def test_tier_three_a_lane_that_never_ran_gets_the_project_label(self):
        """Not a fallback to the old behaviour — a dormant slot has no session to name.

        What it must not do is present the raw slot AS a name, so the project-qualified
        label is the floor. This is also the first stdout use `_lane_label` has ever had:
        it was built for the operator's session ~127 complaint and wired only to a terminal title.
        """
        with tempfile.TemporaryDirectory() as tmp:
            got = session.lane_identity(self._lane(tmp, name="poga-9", sid=None), {})
        self.assertEqual(got, "federation lane 9")
        self.assertNotEqual(got, "poga-9")

    def test_a_malformed_session_id_does_not_masquerade_as_a_name(self):
        """Absence is not identity — an id we cannot parse falls to tier 3, not to half
        a label built from whatever split() happened to return."""
        with tempfile.TemporaryDirectory() as tmp:
            lane = self._lane(tmp, name="poga-4", sid="garbage")
            got = session.lane_identity(lane, {})
        self.assertEqual(got, "federation lane 4")

    def test_a_lane_dict_with_no_path_names_nothing_rather_than_this_session(self):
        """`Path("")` is `Path(".")`. Without the guard this reads the CWD's own
        `.session-state` and hands back the RUNNING session's id as though it were the
        lane's — two different lanes rendering the same confident, wrong name, in the one
        column this item exists to make trustworthy. Latent while every caller passes a
        path; ADR-0129 D5 invites ~30 more callers."""
        self.assertEqual(session.lane_durable_session({"name": "poga-9"}), "")
        self.assertEqual(session.lane_identity({"name": "poga-9"}, {}), "federation lane 9")

    def test_the_machine_is_read_from_the_front_of_the_id_not_the_back(self):
        """`durable_session_id`'s own docstring says the id is extensible to
        `<ts>-<machine>-<person>-<random>`. Counting the machine from the END renames it
        the person the day that lands. The timestamp carries no hyphen and the machine is
        slugged to `[a-z0-9]+`, so index 1 is the guaranteed position."""
        self.assertEqual(
            session._durable_id_parts("20260912T1230Z-devbox-operator-d5b9"),
            ("devbox", "d5b9"))

    def test_one_session_holding_two_claims_says_so_instead_of_picking_one(self):
        """Nothing in `cmd_claim` limits a session to one claim, and a label is one cell.
        Silently keeping whichever the store globbed last would answer "what is that lane
        doing" with half the answer and no marker."""
        idx = session.lane_claim_index({"WI-0157": {"journal": "J"},
                                        "WI-0100": {"journal": "J"}})
        self.assertEqual(idx, {"J": "WI-0100+1"}, "lowest id, and a count of the rest")

    def test_last_beat_wins_when_a_slot_has_been_reused(self):
        """THE RECYCLING CASE, and the reason this rule exists at all.

        A slot outlives its occupants, so a long-lived worktree accumulates one sidecar
        per session that ever ran in it. Naming the lane for a finished session — while a
        live one sits in it — would reproduce the exact confusion the item is about, one
        level further in."""
        with tempfile.TemporaryDirectory() as tmp:
            # THE FILENAMES ARE ADVERSARIAL ON PURPOSE. `lane_session_evidence`
            # iterates `sorted(glob(...))`, so naming the newer sidecar first in sort
            # order lets an implementation that ignores `last_beat` altogether pass.
            # `c-aaa` (old) sorts BEFORE `c-zzz` (new), so first-in-glob-order is the
            # WRONG answer and only a real recency comparison gets this right.
            lane = self._lane(tmp, sid="20260912T0100Z-devbox-old1",
                              beat_min=600, csid="c-aaa")
            d = pathlib.Path(lane["path"]) / ".session-state"
            (d / "c-zzz.live").write_text(
                json.dumps({"session_id": "20260912T1230Z-devbox-new2",
                            "last_beat": _ago(1)}), encoding="utf-8")
            got = session.lane_identity(lane, {})
        self.assertEqual(got, "devbox·new2")


class LaneClaimIndexTest(unittest.TestCase):
    """The join key is the JOURNAL, never the branch."""

    def test_the_join_survives_the_main_branch_every_front_door_claim_carries(self):
        """`poga work claim` cd's to the main checkout by design (ADR-0073), so
        `rec["branch"]` reads `main` for EVERY front-door claim — verified on the live
        store while building this, where all five held claims said `main` and carried
        five distinct journal ids. A branch-keyed join does not error on that; it returns
        nothing, which is the failure mode that survives a code read."""
        claims = {
            "WI-0157": {"branch": "main", "journal": "20260912T1230Z-devbox-d5b9"},
            "WI-0190": {"branch": "main", "journal": "20260912T1220Z-devbox-e743"},
        }
        idx = session.lane_claim_index(claims)
        self.assertEqual(idx, {"20260912T1230Z-devbox-d5b9": "WI-0157",
                               "20260912T1220Z-devbox-e743": "WI-0190"})
        self.assertNotIn("main", idx, "the branch must never become a join key")

    def test_a_claim_with_no_journal_is_skipped_rather_than_keyed_on_empty(self):
        idx = session.lane_claim_index({"WI-0001": {"branch": "main", "journal": ""}})
        self.assertEqual(idx, {})


class DurableIdPartsTest(unittest.TestCase):
    """One parse of the durable id, shared by the narrow cell and the wide column."""

    def setUp(self):
        # WI-0275's guard, and WI-0126's. Same argument for both, and it is this class's
        # own: it reaches the coordination layer only to render a literal record, so it
        # resolves no holder and reads no identity of its own — but both rules are
        # deliberately mechanical and neither has an exemption list, and honouring them
        # costs nothing. NOT `exercise_real_coord_holder`: nothing here is about
        # resolving a holder, only about how one is spelled once resolved.
        #
        # The ambient one goes FIRST — it snapshots the environment and restores that
        # snapshot wholesale at cleanup, so anything set before it outlives the test.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)

    def test_it_splits_the_machine_and_the_tail(self):
        self.assertEqual(session._durable_id_parts("20260912T1230Z-devbox-d5b9"),
                         ("devbox", "d5b9"))

    def test_anything_that_is_not_the_three_part_id_returns_empties(self):
        for bad in ("", "nope", "a-b", None):
            self.assertEqual(session._durable_id_parts(bad), ("", ""))

    def test_coord_who_leads_with_the_session_and_trails_with_the_ref(self):
        """The swap this change made to `claims` and `coord`, pinned. Mutation-proved:
        reverting `_coord_who`'s holder to the raw branch left every suite green, because
        the only existing assertion on it checked for substrings true before AND after.

        Both facts were always in the phrase; the recycled half was in the reading
        position and the durable half was parenthetical."""
        rec = {"branch": "worktree-poga-3", "journal": "20260912T1210Z-devbox-87e5"}
        narrow = session._coord_who(rec, runtime=False)
        self.assertTrue(narrow.startswith("devbox-87e5"),
                        f"the session leads: {narrow!r}")
        self.assertIn("[worktree-poga-3]", narrow, "the ref trails, as the address")

    def test_coord_who_gives_a_journal_less_record_a_label_not_a_slot(self):
        """A record with no journal has no session to name, so it gets the same answer
        `lane_identity` tier 3 gives — never the bare slot, and never the literal string
        "no journal", which named nothing and printed twice on the runtime line."""
        narrow = session._coord_who({"branch": "worktree-poga-3"}, runtime=False)
        self.assertTrue(narrow.startswith("federation lane 3"), narrow)
        self.assertNotIn("no journal", narrow.split("[")[0])

    def test_the_narrow_cell_still_renders_the_hyphen_form(self):
        """`_coord_short_holder` was refactored onto the shared parse but keeps its own
        spelling — the STATE cell is narrow and the identity column is not. What must not
        be duplicated is knowledge of how the id is BUILT, not the separator."""
        self.assertEqual(
            session._coord_short_holder({"journal": "20260912T1230Z-devbox-d5b9"}),
            "devbox-d5b9")


class StructuralNameTest(unittest.TestCase):
    """WI-0157's third finding, which is WI-0152's acceptance half.

    The session title claimed uniqueness in its own docstring and got it from the ONE
    field that collides. Nothing here changes how the ordinal is derived — that is
    ADR-0069 D1's compiled label and the rest of WI-0152 — only that the name stops
    resting its uniqueness on it.
    """

    def setUp(self):
        self._cfg = session.CFG
        session.CFG = dict(session.CFG or {}, architect_id="federation-arch")

    def tearDown(self):
        session.CFG = self._cfg

    def test_two_sessions_that_derived_the_same_ordinal_get_different_names(self):
        """The measured case: two devbox sessions an hour apart, both stamped 162.

        Every lane cut from one base derives the same ordinal, so this is the common
        case under concurrency and not an edge one."""
        a = session.structural_name(162, "DevBox", "2026-08-22 12:00",
                                    "20260822T1200Z-devbox-ab25")
        b = session.structural_name(162, "DevBox", "2026-08-22 12:55",
                                    "20260822T1255Z-devbox-0403")
        self.assertNotEqual(a, b)
        self.assertIn("S162-ab25", a)
        self.assertIn("S162-0403", b)

    def test_the_docstring_no_longer_asserts_what_the_measurement_refutes(self):
        """The false claim was load-bearing: it is why nobody looked. Pinned as text
        because the defect WAS text — the code did exactly what it said."""
        doc = session.structural_name.__doc__ or ""
        self.assertIn("DOES NOT MAKE THIS UNIQUE", doc)
        # The old sentence is still quoted — deliberately, so the next reader sees what
        # was believed and why it was wrong. What must not survive is it being ASSERTED,
        # so the quote has to sit inside the refutation.
        self.assertIn('used to say it did', doc)
        before = doc.split("S<n> alone guarantees it")[0]
        self.assertIn("DOES NOT MAKE THIS UNIQUE", before,
                      "the refutation must precede the claim, not follow it")

    def test_a_caller_with_no_id_still_gets_a_name(self):
        """Fail-soft: a cosmetic label must not raise. It is then not unique, which the
        docstring now admits instead of asserting the opposite."""
        self.assertEqual(session.structural_name(7, "Runner", "2026-06-05 09:00"),
                         "federation-arch\u00b7S7\u00b7Runner\u00b7FriJun5")


class ProvisionalOrdinalTest(unittest.TestCase):
    """WI-0152's remaining half: a START-TIME ordinal is an estimate EVERYWHERE.

    `_display_ordinal` used to render `~N` when `_on_worktree_lane()` and a bare `N`
    otherwise, and ADR-0069 D1 rule (a) said exactly that — *"only a trunk session shows
    a bare N"*. The reasoning under the tilde is that the number belongs to the TRUNK
    COMPILE (`frozen_max` + rank by `started` over the journals actually landed), so a
    value derived before that compile has seen the final set is a guess. None of that
    reasoning mentions worktrees.

    WHY THE TRUNK IS NOT EXEMPT. `render_handoff` assigns `frozen_max + 1 + rank` over
    `_load_journals`' (`started`, session-id) order. An in-flight lane's journal lives on
    the lane's own branch, so a trunk session cannot see it; when that lane lands, a
    journal with an EARLIER `started` is inserted AHEAD and every number behind it
    shifts up. `_on_worktree_lane()` was therefore standing in for "am I
    a concurrent writer?" and answering false for one.

    The measurement is the item's: two devbox sessions an hour apart both stamped 162,
    and the later sweep found six journals sharing 191, six sharing 174, six sharing 129.
    A bare `N` at start was asserting those away.
    """

    def setUp(self):
        self._cfg = session.CFG
        session.CFG = dict(session.CFG or {}, architect_id="federation-arch")

    def tearDown(self):
        session.CFG = self._cfg

    def test_the_default_render_is_provisional(self):
        """The default is the honest render on purpose. What sits on the other side of
        this guard is a false claim of uniqueness on the field a human navigates by, so a
        caller who forgets the flag must land on `~N`, never on the asserting form."""
        self.assertEqual(session._display_ordinal(162), "~162")

    def test_only_a_caller_that_can_show_the_number_is_settled_gets_a_bare_one(self):
        self.assertEqual(session._display_ordinal(162, settled=True), "162")

    def test_the_render_no_longer_consults_lane_ness(self):
        """The structural pin, and the one that would catch the regression. The old
        surface proxy is mocked BOTH ways; the render must not move either time. A test
        that only checked the off-lane value would still pass if someone reinstated
        `_on_worktree_lane()` as the condition with the branches swapped."""
        for on_lane in (True, False):
            with mock.patch.object(session, "_on_worktree_lane", return_value=on_lane):
                self.assertEqual(
                    session._display_ordinal(191), "~191",
                    f"a start-time ordinal is provisional with on_lane={on_lane}")

    def _derive_in_its_own_journal_set(self, did, started):
        """The ordinal a session derives when its journal set holds only itself — the
        shape of a lane cut from a shared base, and of a trunk session that cannot see
        the lanes in flight beside it. Returns the derived ordinal."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        jdir = root / "journal"
        jdir.mkdir()
        jdir.joinpath(f"{did}.md").write_text(
            "---\n"
            f"session-id: {did}\n"
            "ordinal: 162\n"
            "title: (in progress)\n"
            "machine: DevBox\n"
            f"started: {started}\n"
            "ended: \n"
            "---\n\n### What happened\n\n- ...\n", encoding="utf-8")
        with mock.patch.object(session, "JOURNAL_DIR", jdir), \
             mock.patch.object(session, "ARCHIVE", root / "pre-journal-archive.md"):
            return session._next_ordinal()

    def test_two_journals_that_derived_one_ordinal_differ_and_neither_shows_a_bare_n(self):
        """The item's acceptance, in its own words, over its own measured case: the two
        devbox journals an hour apart that both carry `ordinal: 162`.

        Each derives from a journal set holding only itself, which is why they collide —
        no mock decides the collision, the topology does."""
        did_a, did_b = "20260822T1200Z-devbox-ab25", "20260822T1255Z-devbox-0403"
        n_a = self._derive_in_its_own_journal_set(did_a, "2026-08-22T12:00:00+00:00")
        n_b = self._derive_in_its_own_journal_set(did_b, "2026-08-22T12:55:00+00:00")
        self.assertEqual(n_a, n_b, "the collision is the premise; if it stopped happening "
                                   "the rest of this test proves nothing")

        # Their settled labels differ (WI-0157's half, which this must not regress) ...
        name_a = session.structural_name(n_a, "DevBox", "2026-08-22 12:00", did_a)
        name_b = session.structural_name(n_b, "DevBox", "2026-08-22 12:55", did_b)
        self.assertNotEqual(name_a, name_b)

        # ... and neither session DISPLAYS the number as settled — on a lane or off one.
        for on_lane in (True, False):
            with mock.patch.object(session, "_on_worktree_lane", return_value=on_lane):
                for n in (n_a, n_b):
                    shown = session._display_ordinal(n)
                    self.assertNotEqual(shown, str(n),
                                        "a start-time ordinal must never render bare")
                    self.assertTrue(shown.startswith("~"), shown)

    def test_an_unknown_ordinal_is_not_decorated(self):
        """`compiled_ordinal` returns None when no journal ranks, and two callers turn
        that into the literal `"?"`. `~?` would decorate a non-answer as an estimate of
        something; `?` already says the only true thing."""
        self.assertEqual(session._display_ordinal("?"), "?")
        self.assertEqual(session._display_ordinal("?", settled=True), "?")

    def test_both_notes_disclose_the_same_thing_about_the_same_number(self):
        """Off a lane the wait is on the compile, on a lane it is on the land — different
        events, one fact. Pinned so a future edit cannot quietly drop the off-lane note
        and leave `~N` standing with nothing saying what it is waiting for."""
        self.assertIn("final number assigned", session.LANE_ORDINAL_NOTE)
        self.assertIn("final number assigned", session.START_ORDINAL_NOTE)
        self.assertIn("provisional", session.START_ORDINAL_NOTE)


class AcceptanceDrillTest(unittest.TestCase):
    """The acceptance: the slot is present as an ADDRESS, absent as a NAME.

    Written against the rendered lines rather than the helper, because every test above
    would pass over a surface that never called it — which is precisely the state the
    item found the codebase in, with `_lane_label` built and wired to one caller.
    """

    def setUp(self):
        self._cfg = session.CFG
        session.CFG = dict(session.CFG or {}, architect_id="federation-arch")

    def tearDown(self):
        session.CFG = self._cfg

    def _lane(self, tmp, name, sid, ahead=2):
        path = pathlib.Path(tmp) / name
        d = path / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "c.live").write_text(
            json.dumps({"session_id": sid, "last_beat": _ago(1)}), encoding="utf-8")
        return {"name": name, "path": path, "branch": f"worktree-{name}",
                "ahead": ahead, "silent_min": 90.0, "live_by": "heartbeat",
                "status": "live", "dirty": False, "is_self": False}

    def test_the_shared_idle_sentence_leads_with_the_session_not_the_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            lane = self._lane(tmp, "poga-2", "20260912T1220Z-devbox-e743")
            with mock.patch.object(session, "_trunk", return_value="main"):
                line = session._idle_live_lane_note(
                    lane, {"20260912T1220Z-devbox-e743": "WI-0190"})

        # The NAME leads.
        self.assertTrue(line.startswith("devbox·WI-0190"),
                        f"the durable identity must lead the line: {line!r}")
        # The SLOT is still there — it is what `recover-lanes --lane` is typed with.
        self.assertIn("[poga-2]", line, "the slot survives as the address")
        # And it is never again the thing in the reading position.
        self.assertFalse(line.startswith("poga-2"))

    def test_the_startup_lane_lines_name_every_lane_durably(self):
        """One read of the claim store, and every line — active, idle, stranded, empty —
        carries the identity. The stranded line keeps its `cd <path> && merge` routing,
        which is the address half, untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            live = self._lane(tmp, "poga-5", "20260912T1200Z-devbox-de88")
            unmerged = dict(self._lane(tmp, "poga-7", "20260912T1130Z-devbox-fca9"),
                            status="unmerged")
            with mock.patch.object(session, "_scan_lanes",
                                   return_value=[live, unmerged]), \
                 mock.patch.object(session, "_idle_live_lanes", return_value=[]), \
                 mock.patch.object(session, "_lane_holds_only_phantom_debris",
                                   return_value=False), \
                 mock.patch.object(session, "lane_claim_index",
                                   return_value={"20260912T1200Z-devbox-de88": "WI-0012"}), \
                 mock.patch.object(session, "_trunk", return_value="main"), \
                 mock.patch.object(session, "_lanes_root", return_value="/x"), \
                 mock.patch.object(session, "_tree_has_live_session",
                                   side_effect=lambda p: "poga-5" in str(p)):
                lines = session._stranded_lane_lines()

        blob = "\n".join(lines)
        self.assertIn("devbox·WI-0012", blob, "the claimed lane is named by its item")
        self.assertIn("devbox·fca9", blob,
                      "the unclaimed lane falls back to its id tail, not to its slot")
        self.assertIn("[poga-7]", blob, "the slot stays as the address")
        for line in lines:
            body = line.split(":", 1)[1].lstrip() if ":" in line else line
            self.assertFalse(
                body.startswith("poga-"),
                f"a bare slot is standing as the primary identifier: {line!r}")

    def test_the_active_line_names_lanes_when_nothing_is_stranded(self):
        """The COMMON case on a healthy machine, and the drill missed it: the early
        return at the top of `_stranded_lane_lines` builds its own `active:` line, so a
        fixture that always contains something stranded never reaches it."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            live = self._lane(tmp, "poga-5", "20260912T1200Z-devbox-de88")
            with mock.patch.object(session, "_scan_lanes", return_value=[live]), \
                 mock.patch.object(session, "_idle_live_lanes", return_value=[]), \
                 mock.patch.object(session, "_lane_holds_only_phantom_debris",
                                   return_value=False), \
                 mock.patch.object(session, "lane_claim_index",
                                   return_value={"20260912T1200Z-devbox-de88": "WI-0012"}), \
                 mock.patch.object(session, "_lanes_root", return_value="/x"), \
                 mock.patch.object(session, "_trunk", return_value="main"), \
                 mock.patch.object(session, "_tree_has_live_session", return_value=True):
                lines = session._stranded_lane_lines()
        blob = "\n".join(lines)
        self.assertIn("active:", blob)
        self.assertIn("devbox\u00b7WI-0012", blob)
        self.assertIn("[poga-5]", blob)

    def test_the_empty_and_recover_rows_name_the_lane_too(self):
        """Two converted lines the acceptance drill did not reach. Both render a lane in
        a state nobody is in — an abandoned stub, and a dead lane being landed — which is
        exactly when a reader has least other context to identify it by."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            phantom = dict(self._lane(tmp, "poga-6", "20260912T1210Z-devbox-87e5"),
                           status="dirty")
            with mock.patch.object(session, "_scan_lanes", return_value=[phantom]), \
                 mock.patch.object(session, "_idle_live_lanes", return_value=[]), \
                 mock.patch.object(session, "_lane_holds_only_phantom_debris",
                                   return_value=True), \
                 mock.patch.object(session, "lane_claim_index", return_value={}), \
                 mock.patch.object(session, "_lanes_root", return_value="/x"), \
                 mock.patch.object(session, "_trunk", return_value="main"), \
                 mock.patch.object(session, "_tree_has_live_session", return_value=False):
                lines = session._stranded_lane_lines()
        empty = next(l for l in lines if l.startswith("empty:"))
        self.assertIn("devbox\u00b787e5", empty)
        self.assertIn("[poga-6]", empty)
        self.assertFalse(empty.split(":", 1)[1].lstrip().startswith("poga-6"))

    def test_poga_lanes_renders_the_identity_by_actually_running_it(self):
        """RUNS `poga lanes`; does not grep its source.

        The first cut of this test was four `assertIn`s over `poga`'s text, and an
        adversarial pass proved what that buys: replacing the awk lookup with `ident=""`,
        changing the field separator, and deleting the empty-map check were ALL green.
        It pinned the strings and nothing about the behaviour — a test shaped like a
        guard over a conversion that could be entirely removed under it.

        So this builds a real repo with a real lane worktree and a real liveness sidecar,
        runs the real script, and reads the real table.
        """
        import subprocess
        import tempfile
        git = shutil.which("git")
        if not (git and shutil.which("bash")):
            self.skipTest("bash+git required")
        root = pathlib.Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "member"
            repo.mkdir()
            for a in (("init", "-b", "main"), ("config", "user.email", "t@t"),
                      ("config", "user.name", "t"),
                      ("config", "commit.gpgsign", "false")):
                subprocess.run([git, "-C", str(repo), *a], capture_output=True)
            (repo / "seed.txt").write_text("x", encoding="utf-8")
            subprocess.run([git, "-C", str(repo), "add", "-A"], capture_output=True)
            subprocess.run([git, "-C", str(repo), "commit", "-m", "seed"],
                           capture_output=True)
            # The harness the shell-out reaches. `require_repo` anchors on the repo it is
            # standing in, so the member needs its own copy of the entry point.
            for name in ("session.py", "sessionlib", "session.config.json"):
                src = root / name
                if src.is_dir():
                    shutil.copytree(src, repo / name,
                                    ignore=shutil.ignore_patterns("__pycache__"))
                elif src.exists():
                    shutil.copy2(src, repo / name)

            lane = repo / ".claude" / "worktrees" / "poga-4"
            subprocess.run([git, "-C", str(repo), "worktree", "add", "-q",
                            "-b", "worktree-poga-4", str(lane), "main"],
                           capture_output=True)
            state = lane / ".session-state"
            state.mkdir(parents=True, exist_ok=True)
            (state / "c.live").write_text(
                json.dumps({"session_id": "20260912T1230Z-devbox-d5b9",
                            "last_beat": _ago(1)}), encoding="utf-8")

            proc = subprocess.run(["bash", str(root / "poga"), "lanes"],
                                  capture_output=True, text=True, cwd=str(repo))

        self.assertEqual(proc.returncode, 0, proc.stderr)
        row = next((l for l in proc.stdout.splitlines() if "poga-4" in l
                    and "LANE" not in l), None)
        self.assertIsNotNone(row, f"no row for the lane: {proc.stdout!r}")
        # The NAME, read off a real sidecar through the real shell-out.
        self.assertIn("devbox\u00b7d5b9", row,
                      f"the LANE column must carry the durable identity: {row!r}")
        # The SLOT survives as the address a reader types.
        self.assertIn("poga-4", row)
        # And it is not what the row LEADS with.
        self.assertFalse(row.strip().startswith("poga-4"),
                         f"a recycled slot is standing as the name: {row!r}")
        # The header names both columns, so the distinction is on the surface itself.
        self.assertIn("LANE", proc.stdout)
        self.assertIn("SLOT", proc.stdout)
        # Nothing degraded, so nothing complained.
        self.assertNotIn("NOTE:", proc.stderr)

    def test_poga_lanes_says_so_when_it_cannot_name_a_lane(self):
        """D4, exercised rather than asserted: a repo with a lane branch but NO harness
        to ask. The column must fall back to a project label and SAY it fell back —
        a column of bare slots is indistinguishable from a column of names."""
        import subprocess
        import tempfile
        git = shutil.which("git")
        if not (git and shutil.which("bash")):
            self.skipTest("bash+git required")
        root = pathlib.Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "member"
            repo.mkdir()
            for a in (("init", "-b", "main"), ("config", "user.email", "t@t"),
                      ("config", "user.name", "t"),
                      ("config", "commit.gpgsign", "false")):
                subprocess.run([git, "-C", str(repo), *a], capture_output=True)
            (repo / "seed.txt").write_text("x", encoding="utf-8")
            subprocess.run([git, "-C", str(repo), "add", "-A"], capture_output=True)
            subprocess.run([git, "-C", str(repo), "commit", "-m", "seed"],
                           capture_output=True)
            # NO session.py copied — the shell-out cannot succeed.
            subprocess.run([git, "-C", str(repo), "branch", "worktree-poga-4"],
                           capture_output=True)
            proc = subprocess.run(["bash", str(root / "poga"), "lanes"],
                                  capture_output=True, text=True, cwd=str(repo))

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NOTE:", proc.stderr, "a degrade must announce itself")
        self.assertIn("SLOTS are recycled addresses", proc.stderr)
        row = next(l for l in proc.stdout.splitlines()
                   if "poga-4" in l and "LANE" not in l)
        # Tier 3, from poga's own `lane_label` — never the raw slot.
        self.assertIn("lane 4", row, f"the fallback is a project label: {row!r}")
        self.assertFalse(row.strip().startswith("poga-4"))

if __name__ == "__main__":
    unittest.main()
