"""The work-item `waiting-on` field (WI-0408, brief 2026-09-10 from a member's Architect).

A schema-legal home for the marker that says A HUMAN OWES THIS ITEM AN ANSWER. The field
exists because the obvious place to put it does not work, and that impossibility is the
first thing these tests defend:

  `blocked-by` is the store's ONE machine-validated cross-reference — `_wi_validate`
  resolves every token in it against the store and `wi-check` exits 1 on a dangling one.
  So `blocked-by: operator` cannot be adopted by any member: the first land after adopting it
  fails. The requesting Architect found that by trying it on its own store and withdrew
  its own request. Widening the validator to admit actor-shaped tokens was the alternative, and
  it is the NEGATIVE CONTROL stated in the brief: it buys one field at the price of
  making every future reading of `blocked-by` ambiguous, because every reader would then
  have to classify each token before it could trust it.

So the two fields refuse each other's values, in both directions and on purpose, and
`SeparationTest` is where that is pinned. Delete it and the two fields can quietly
converge back into one ambiguous field, which is the entire defect this item is about.

The field follows `migration` rather than `scope` on the one question that matters for a
fleet of many members — IS THE LINE WRITTEN WHEN UNSET? — and the reason is that an
absence means different things for the two shapes. An unscoped item is a gap to close, so
its empty line must be visible. "Nobody is waiting on a human here" is already the right
answer and needs no line to say it. That choice is what makes this field cost the fleet
nothing: every existing item file is byte-identical before and after it arrives.

THE TEST THAT CAN PASS FOR THE WRONG REASON is
`test_a_legacy_item_re_renders_without_growing_a_waiting_on_line`. Everything else here
would stay green if the line were written always; only that one, and its sibling over a
whole legacy store, fail. They are the acceptance line's "the one that can silently
pass", written to be able to fail.

The feed-level half of WI-0408 — `writes_waiting_marker`, which is what lets a consumer
tell "nobody is waiting" from "this member does not write the marker at all" — lives in
`test_wi_feed.py`, beside the rest of the feed contract.
"""

import argparse
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def _item(wid, **kw):
    it = {"id": wid, "title": f"Item {wid}", "status": "open", "section": "next",
          "blocked_by": [], "group": "", "source": "", "impact": "fix", "migration": "",
          "scope": "harness", "version": "", "notes": ""}
    it.update(kw)
    return it


#: An item file as it was written BEFORE this field existed — no `waiting-on` line, and
#: no `scope` line either, which is what 400-odd files in the real store look like. Built
#: as raw text rather than through `_wi_render_item`, deliberately: rendering it with
#: today's writer would produce today's shape and the legacy tests would be testing the
#: code against itself.
def _legacy_text(wid, title="A legacy item"):
    return (f"# {wid}: {title}\n\n"
            f"- status: open\n- section: backlog\n- blocked-by: \n- group: \n"
            f"- source: \n- impact: fix\n- version: \n\n"
            f"Notes that predate the field.\n")


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir()
        (self.tmp / "ops-items").mkdir()
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, item):
        session._wi_write_item(item)

    def write_raw(self, wid, text):
        """Put a file in the store WITHOUT going through the writer. The whole point of
        the legacy cases is a file today's renderer did not produce."""
        (self.tmp / "work-items" / f"{wid}-legacy.md").write_text(text, encoding="utf-8")

    def read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)

    def text(self, wid):
        return (self.tmp / "work-items"
                / session._wi_filename_for(wid)).read_text(encoding="utf-8")


class VocabularyTest(unittest.TestCase):
    def test_unset_is_the_empty_string_and_not_a_token(self):
        """There is no token meaning "not waiting" for the same reason `scope` has no
        fourth enum value: a placeholder reads as an answer in every count that groups by
        the field. Absence is the answer."""
        self.assertEqual(session.WI_WAITING_ON_UNSET, "")

    def test_an_actor_token_is_a_shape_not_an_allowlist(self):
        """Deliberately not an enum. The bound user differs per member, so a fleet-wide
        allowlist of people is a list that is wrong the first time a member has a
        different operator."""
        for ok in ("alex", "m", "example-arch", "ops.oncall", "user_2"):
            self.assertTrue(session.WI_WAITING_ON_RE.match(ok), ok)
        for bad in ("Alex", "2fast", "", "alex doe", "alex!", "x" * 33):
            self.assertFalse(session.WI_WAITING_ON_RE.match(bad), bad)

    def test_every_item_id_shape_is_recognised_as_an_id(self):
        """The refusal is only as wide as this pattern, and a shape it misses is a
        silent, unvalidated dependency edge in the wrong field."""
        for wid in ("WI-0007", "OPS-0011", "DRILL-3", "wi-0007", "WI-12345"):
            self.assertTrue(session.WI_WAITING_ON_ID_RE.match(wid), wid)
        for actor in ("alex", "wi", "ops-oncall"):
            self.assertFalse(session.WI_WAITING_ON_ID_RE.match(actor), actor)


class RoundTripTest(StoreBase):
    def test_the_marker_survives_a_write_read_cycle(self):
        self.write(_item("WI-0001", waiting_on="alex"))
        self.assertEqual(self.read("WI-0001")["waiting_on"], "alex")

    def test_the_line_is_written_when_set(self):
        self.write(_item("WI-0001", waiting_on="alex"))
        self.assertIn("- waiting-on: alex", self.text("WI-0001"))

    def test_no_line_is_written_when_unset(self):
        """The `migration` shape, not the `scope` shape. See the module docstring."""
        self.write(_item("WI-0001"))
        self.assertNotIn("waiting-on", self.text("WI-0001"))

    def test_an_item_with_no_line_parses_as_waiting_on_nobody(self):
        self.write(_item("WI-0001"))
        self.assertEqual(self.read("WI-0001")["waiting_on"], "")

    def test_the_hyphenated_file_key_reaches_the_snake_case_dict_key(self):
        """The one spelling split in this schema besides `blocked-by`/`blocked_by`. Get
        it wrong and the line parses as NOTES — silently, with no error anywhere, which
        is how a marker would reach the store and never reach the feed."""
        self.write(_item("WI-0001", waiting_on="alex"))
        item = self.read("WI-0001")
        self.assertEqual(item["waiting_on"], "alex")
        self.assertNotIn("waiting-on", item["notes"])

    def test_clearing_the_marker_removes_the_line(self):
        self.write(_item("WI-0001", waiting_on="alex"))
        item = self.read("WI-0001")
        item["waiting_on"] = ""
        self.write(item)
        self.assertNotIn("waiting-on", self.text("WI-0001"))


class LegacyToleranceTest(StoreBase):
    """THE HALF THAT CAN PASS FOR THE WRONG REASON — the acceptance line says so by name.

    A new field ships byte-identical to every member, each with a store full of
    items that predate it. Every test above would stay green if the line were written
    always; these are the ones that fail.
    """

    def test_a_legacy_item_parses_with_the_field_unset(self):
        self.write_raw("WI-0001", _legacy_text("WI-0001"))
        self.assertEqual(self.read("WI-0001")["waiting_on"], "")

    def test_a_legacy_item_re_renders_without_growing_a_waiting_on_line(self):
        """Read an untouched pre-field item and write it back: the file must not gain a
        line. This is what makes the field cost the fleet nothing — no member edits any
        file, and no member's next store write produces a diff on every item it touches.

        It is also the test that would go green for free if the assertion were dropped,
        so it asserts on the RENDERED TEXT rather than on the parsed value: a value can
        be empty while its line is written, and only one of those two is the property."""
        self.write_raw("WI-0001", _legacy_text("WI-0001"))
        self.write(self.read("WI-0001"))
        self.assertNotIn("waiting-on", self.text("WI-0001"))

    def test_a_whole_legacy_store_validates_with_no_waiting_on_problem(self):
        """The land gate runs `_wi_validate`. One problem per legacy item is a
        permanently red trunk until a store-wide backfill lands, and that backfill
        collides with every item in flight — so the VALUE is validated and the ABSENCE
        never is (the `scope` lesson, WI-0323, applied a second time)."""
        for n in range(1, 8):
            self.write_raw(f"WI-{n:04d}", _legacy_text(f"WI-{n:04d}"))
        problems = session._wi_validate()
        self.assertEqual([p for p in problems if "waiting" in p], [])

    def test_an_unset_marker_is_not_treated_as_waiting(self):
        """"Not participating" and "not waiting" are the same state for an ITEM; the
        distinction the brief needs is a MEMBER-level one and is answered once, at feed
        level. What must never happen here is an empty marker reading as a live one."""
        self.write_raw("WI-0001", _legacy_text("WI-0001"))
        self.assertFalse(self.read("WI-0001")["waiting_on"])


class ValidateTest(StoreBase):
    def test_a_valid_actor_token_is_not_a_problem(self):
        self.write(_item("WI-0001", waiting_on="alex"))
        self.assertEqual([p for p in session._wi_validate() if "waiting" in p], [])

    def test_an_item_id_in_the_marker_is_refused_and_points_at_blocked_by(self):
        """The mirror of the mistake this field exists to prevent. An item id here would
        create a second dependency edge that nothing resolves — `_wi_validate` checks
        `blocked-by` against the store and would never look at this field — so the
        refusal must name where it belongs, or the two fields converge by accident."""
        self.write(_item("WI-0001", waiting_on="WI-0002"))
        hits = [p for p in session._wi_validate() if "waiting-on" in p]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("item id", hits[0])
        self.assertIn("blocked-by", hits[0])

    def test_a_malformed_token_is_refused_as_a_shape(self):
        self.write(_item("WI-0001", waiting_on="Alex Doe"))
        hits = [p for p in session._wi_validate() if "waiting-on" in p]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("actor token", hits[0])

    def test_the_two_refusals_are_different_sentences(self):
        """An untyped "invalid value" would collapse the two mistakes into one message,
        and they need different actions: one moves the token to another field, the other
        fixes its spelling."""
        self.write(_item("WI-0001", waiting_on="OPS-0011"))
        self.write(_item("WI-0002", waiting_on="Alex"))
        problems = {p.split(":")[0]: p for p in session._wi_validate()
                    if "waiting-on" in p}
        self.assertIn("item id", problems["WI-0001"])
        self.assertNotIn("item id", problems["WI-0002"])


class SeparationTest(StoreBase):
    """The negative control the source brief states, in both directions.

    Widening `blocked-by` to accept actor-shaped tokens does NOT satisfy WI-0408. These
    two tests are what would go red if someone did it anyway.
    """

    def test_an_actor_token_in_blocked_by_is_still_a_validator_problem(self):
        self.write(_item("WI-0001", blocked_by=["alex"]))
        hits = [p for p in session._wi_validate() if "blocked-by names alex" in p]
        self.assertEqual(len(hits), 1, session._wi_validate())

    def test_the_marker_does_not_create_a_cross_reference(self):
        """`waiting-on` names an actor, so it must NOT be resolved against the store —
        an actor is not expected to be an item, and validating it as one would recreate
        the exact failure that made `blocked-by: alex` impossible."""
        self.write(_item("WI-0001", waiting_on="alex"))
        self.assertEqual([p for p in session._wi_validate()
                          if "not in this tree's store" in p], [])


class NewTest(StoreBase):
    """`_wi_reserve_next` is stubbed with a COUNTER — the same seam `test_wi_scope` uses,
    and for the same reason: the property under test is "how many times did a refusing
    call reach the draw", and counting calls measures that directly."""

    def setUp(self):
        super().setUp()
        self.draws = []
        self._reserve = session._wi_reserve_next
        session._wi_reserve_next = self._draw

    def tearDown(self):
        session._wi_reserve_next = self._reserve
        super().tearDown()

    def _draw(self, *a, **kw):
        self.draws.append(1)
        return len(self.draws)

    def _new(self, **kw):
        kw.setdefault("title", "A thing")
        kw.setdefault("section", "next")
        kw.setdefault("blocked_by", "")
        kw.setdefault("group", "")
        kw.setdefault("notes", "")
        kw.setdefault("scope", "harness")
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_new(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_an_item_can_be_born_waiting(self):
        """Triage files the question it cannot answer itself, so the marker has to be
        settable at creation and not only afterwards."""
        code, out = self._new(waiting_on="alex")
        self.assertEqual(code, 0)
        self.assertIn("waiting on alex", out)
        self.assertEqual(session._wi_parse()[0]["waiting_on"], "alex")

    def test_the_normal_case_says_nothing_about_waiting(self):
        code, out = self._new()
        self.assertEqual(code, 0)
        self.assertNotIn("waiting", out)

    def test_an_item_id_is_refused_at_creation(self):
        code, out = self._new(waiting_on="WI-0002")
        self.assertEqual(code, 2)
        self.assertIn("--blocked-by", out)

    def test_neither_refusal_reaches_the_allocator_draw(self):
        """A refusal below `_wi_reserve_next` burns an allocator id for good. Every
        refusal in this verb had to be hoisted above the draw together, and a new one
        added below the line is exactly the regression this counts."""
        self._new(waiting_on="WI-0002")
        self._new(waiting_on="Alex Doe")
        self.assertEqual(self.draws, [])
        self._new(waiting_on="alex")
        self.assertEqual(len(self.draws), 1)


class StatusTest(StoreBase):
    def _status(self, wid, **kw):
        ns = {"id": wid, "status": None, "section": None, "blocked_by": None,
              "source": None, "impact": None, "migration": None, "scope": None,
              "waiting_on": None}
        ns.update(kw)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_status(argparse.Namespace(**ns))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_the_marker_is_set_on_an_existing_item(self):
        self.write(_item("WI-0001"))
        code, out = self._status("WI-0001", waiting_on="alex")
        self.assertEqual(code, 0)
        self.assertIn("waiting-on=alex", out)
        self.assertEqual(self.read("WI-0001")["waiting_on"], "alex")

    def test_an_empty_value_clears_it(self):
        """Clearing is the COMMON path, not the withdrawal-of-a-guess path it is for
        `impact` and `scope`: an answered question means the marker comes off, and a
        marker nobody removes ages into a permanent "waiting on you" for work that is
        moving again (ADR-0101's ghost shape, reader-side)."""
        self.write(_item("WI-0001", waiting_on="alex"))
        code, _ = self._status("WI-0001", waiting_on="")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("WI-0001")["waiting_on"], "")
        self.assertNotIn("waiting-on", self.text("WI-0001"))

    def test_an_untouched_marker_survives_an_unrelated_edit(self):
        """`None` means "not named on this call" and must not clear the field — the
        distinction between an absent flag and an empty one, which every other setter in
        this verb also depends on."""
        self.write(_item("WI-0001", waiting_on="alex"))
        self._status("WI-0001", section="backlog")
        self.assertEqual(self.read("WI-0001")["waiting_on"], "alex")

    def test_an_item_id_is_refused_and_the_item_is_untouched(self):
        """The refusal runs at the TOP of the verb, above every assignment — so a call
        that also carries a perfectly good edit makes neither change.

        This test is why the refusal is where it is: it was originally written beside the
        other per-field setters, where it still refused correctly and still wrote nothing
        (the exit precedes `_wi_write_item`), but seven fields had already been assigned
        on the in-memory item by then. That is late-but-harmless, one refactor from
        late-and-harmful, in a function that says of its own status guard that a refusal
        must leave the item untouched rather than half-edited."""
        self.write(_item("WI-0001", waiting_on="alex"))
        code, out = self._status("WI-0001", section="backlog", waiting_on="WI-0002")
        self.assertEqual(code, 2)
        self.assertIn("--blocked-by", out)
        item = self.read("WI-0001")
        self.assertEqual(item["waiting_on"], "alex")
        self.assertEqual(item["section"], "next")

    def test_a_malformed_token_is_refused_before_it_reaches_the_store(self):
        """The validator is a REPORT run at the gate; by the time it speaks, a bad value
        is already committed. This refusal is what stops it being written at all."""
        self.write(_item("WI-0001"))
        code, out = self._status("WI-0001", waiting_on="Alex Doe")
        self.assertEqual(code, 2)
        self.assertIn("actor token", out)
        self.assertEqual(self.read("WI-0001")["waiting_on"], "")


class ShowTest(StoreBase):
    def _show(self, wid):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_show(argparse.Namespace(id=wid))
        return buf.getvalue()

    def test_the_row_appears_when_a_human_is_waiting(self):
        self.write(_item("WI-0001", waiting_on="alex"))
        out = self._show("WI-0001")
        self.assertIn("waiting-on: alex", out)

    def test_no_row_when_nobody_is_waiting(self):
        """The OPPOSITE call to `impact` and `scope`, which are printed even when empty
        because an unset one is a thing to DO. An item waiting on nobody is the normal
        case, so a permanent `(nobody)` row would be noise on every item in the store
        while making the rare real marker harder to spot."""
        self.write(_item("WI-0001"))
        self.assertNotIn("waiting-on", self._show("WI-0001"))


class OpsTest(StoreBase):
    def test_an_obligation_carries_no_marker(self):
        """A standing obligation is owed on a DATE, never waiting on a person's answer,
        so `_ops_render_item` gains no field — the same call it makes for `scope`,
        `impact` and `version`."""
        session._ops_write_item({"id": "OPS-0001", "title": "A standing obligation",
                                 "cadence": "weekly", "notes": ""})
        text = (self.tmp / "ops-items" / session._ops_filename_for("OPS-0001")
                ).read_text(encoding="utf-8")
        self.assertNotIn("waiting-on", text)


if __name__ == "__main__":
    unittest.main()
