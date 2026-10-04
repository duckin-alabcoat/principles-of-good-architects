"""The work-item `scope` field (WI-0323, R4 of the 2026-09-07 consultant brief).

Every item says what KIND of work it is — `harness | product | mixed` — defined by the
CAPABILITY being changed, never by the paths the diff happens to touch. A shared-substrate
defect reported from a product repository is still harness work.

The field follows `impact` end to end and DIVERGES FROM IT AT EXACTLY ONE POINT, which is
most of what these tests defend. `impact` reports every unlabelled item as debt; `scope`
does not, because `_wi_validate` is what the LAND GATE runs and `scope` arrived to a store
of 367 items. A missing-scope problem per legacy item is a permanently red trunk until a
367-file backfill lands — landing a field by breaking every land is not shipping the field.

So the pressure sits in three other places, and each has a test below:

  * `wi-new` REFUSES an item minted without a scope — the authored-at-creation half, at
    the one moment the author knows the answer;
  * `wi-status --scope` scopes a legacy item as it is touched, which is how the unset
    count falls;
  * `curate/finish_line.py` PRINTS the unset count as its own bucket, so unscoped items
    stay visible and are never silently counted as product — the one thing R4 asks for by
    name.

The negative that matters most is `test_an_unscoped_legacy_item_is_not_a_validator_problem`.
Delete it and the obvious "make it symmetric with impact" edit reads as an improvement
right up until the trunk goes red.
"""

import argparse
import datetime as _dt
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
import session  # noqa: E402


def _item(wid, **kw):
    it = {"id": wid, "title": f"Item {wid}", "status": "open", "section": "next",
          "blocked_by": [], "group": "", "source": "", "impact": "fix", "migration": "",
          "scope": "", "version": "", "notes": ""}
    it.update(kw)
    return it


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

    def read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)

    def text(self, wid):
        return (self.tmp / "work-items"
                / session._wi_filename_for(wid)).read_text(encoding="utf-8")


class VocabularyTest(unittest.TestCase):
    def test_the_three_values_and_nothing_else(self):
        self.assertEqual(session.WI_SCOPES, ("harness", "product", "mixed"))

    def test_there_is_no_fourth_value_meaning_not_classified(self):
        """`WI_SCOPE_UNSET` is the EMPTY STRING, not an enum member, and that is the
        whole design. A fourth value meaning "not classified" would read as a
        classification in every count that groups by scope — the
        `ship-the-detector-with-the-capability` shape, where a not-applicable branch that
        reads as a pass does not merely miss a gap, it certifies one."""
        self.assertEqual(session.WI_SCOPE_UNSET, "")
        self.assertNotIn(session.WI_SCOPE_UNSET, session.WI_SCOPES)


class RoundTripTest(StoreBase):
    def test_scope_survives_a_write_read_cycle(self):
        self.write(_item("WI-0001", scope="harness"))
        self.assertEqual(self.read("WI-0001")["scope"], "harness")

    def test_each_value_round_trips(self):
        for n, value in enumerate(session.WI_SCOPES, start=1):
            self.write(_item(f"WI-{n:04d}", scope=value))
            self.assertEqual(self.read(f"WI-{n:04d}")["scope"], value)

    def test_the_line_is_written_even_when_unset(self):
        """UNLIKE `migration`, which is absent-means-no and writes no line when empty.

        The two shapes answer different questions. A missing `migration` line is a real
        answer; a missing `scope` line is not an answer at all, and an item that predates
        the field has to be visibly UNSCOPED rather than indistinguishable from one whose
        line nobody has written yet. The empty line is what makes the legacy backlog
        countable instead of inferred from an absence."""
        self.write(_item("WI-0001"))
        text = self.text("WI-0001")
        self.assertIn("- scope: \n", text)
        self.assertNotIn("migration:", text)      # the contrasting shape, in one place

    def test_an_older_file_without_the_field_parses_as_unset(self):
        """The store is append-only history and predates every field it will ever gain.
        367 files looked exactly like this when the field landed."""
        (self.tmp / "work-items" / "WI-0009-legacy.md").write_text(
            "# WI-0009: An item from before scope existed\n\n"
            "- status: open\n- section: next\n- blocked-by: \n- group: \n"
            "- source: \n- impact: fix\n- version: \n\n",
            encoding="utf-8")
        got = self.read("WI-0009")
        self.assertEqual(got["scope"], "")
        self.assertEqual(got["impact"], "fix")     # the neighbouring field still parses

    def test_an_unknown_value_round_trips_rather_than_being_silently_dropped(self):
        """The parser stores what it read; `_wi_validate` is what judges it. A parser
        that dropped a bad value would make the validator structurally unable to report
        it — the file would read clean while the bytes on disk said otherwise."""
        (self.tmp / "work-items" / "WI-0009-odd.md").write_text(
            "# WI-0009: t\n\n- status: open\n- section: next\n- impact: fix\n"
            "- scope: infrastructure\n", encoding="utf-8")
        self.assertEqual(self.read("WI-0009")["scope"], "infrastructure")


class ValidateTest(StoreBase):
    def test_a_bogus_value_is_rejected(self):
        self.write(_item("WI-0001", scope="infrastructure"))
        problems = session._wi_validate()
        self.assertTrue(any("scope 'infrastructure' is not one of" in p for p in problems),
                        problems)

    def test_every_valid_value_passes(self):
        for n, value in enumerate(session.WI_SCOPES, start=1):
            self.write(_item(f"WI-{n:04d}", scope=value))
        self.assertEqual([p for p in session._wi_validate() if "scope" in p], [])

    def test_an_unscoped_legacy_item_is_not_a_validator_problem(self):
        """THE LOAD-BEARING NEGATIVE, and the one asymmetry with `impact`.

        `_wi_validate` is what the land gate runs. One problem per unscoped item, over the
        367 that predate the field, is a permanently red trunk until a 367-file backfill
        lands — and that backfill collides with every item in flight. The convergence
        pressure lives in `cmd_wi_new` (refuses a new one) and `cmd_wi_status` (scopes an
        old one as it is touched), never here."""
        self.write(_item("WI-0001"))
        self.assertEqual([p for p in session._wi_validate() if "scope" in p], [])

    def test_a_store_of_unscoped_items_validates_clean_at_scale(self):
        """The count is the point, not the shape: a per-item problem is only a red trunk
        because there are hundreds of them. Asserting one item is clean would pass even
        if the rule fired on, say, only `next`-section items."""
        for n in range(1, 41):
            self.write(_item(f"WI-{n:04d}"))
        self.assertEqual([p for p in session._wi_validate() if "scope" in p], [])

    def test_the_impact_debt_report_still_fires(self):
        """A guard against fixing the asymmetry in the wrong direction: `impact` must
        keep nagging. If a future edit makes the two fields symmetric, exactly one of
        this test and the one above it goes red, whichever way the edit went."""
        self.write(_item("WI-0001", impact="", scope="harness"))
        self.assertTrue(any("no impact label" in p for p in session._wi_validate()))


class NewTest(StoreBase):
    """`_wi_reserve_next` is stubbed with a COUNTER, not a coord store.

    The allocator reaches a coordination store this fixture has not got, so the real one
    returns None here and `wi-new` exits 3 before any of this file's subject is reached.
    The counter is not a convenience: the property
    `test_the_refusal_does_not_burn_an_allocator_number` defends is exactly "how many
    times did a refusing call reach the draw", and counting the calls measures that at
    the seam rather than inferring it from a number printed afterwards."""

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
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_new(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_minting_without_a_scope_is_refused(self):
        code, out = self._new()
        self.assertEqual(code, 2)
        self.assertIn("--scope is required", out)

    def test_the_refusal_names_all_three_values_and_how_to_choose(self):
        """A refusal that only says "required" makes the operator go read the source.
        The three values and the capability-not-path rule are the whole judgment."""
        _, out = self._new()
        for value in session.WI_SCOPES:
            self.assertIn(f"--scope {value}", out)
        self.assertIn("CAPABILITY", out)

    def test_no_refusal_reaches_the_allocator_draw(self):
        """`_wi_reserve_next` consumes an id from a shared allocator, so a refusal placed
        after it burns that id for good — the store already carries 20 drawn-but-never-
        created numbers and OPS-0004's is gone permanently. Making `--scope` required
        turns the commonest mistake at this verb into an id leak unless every refusal
        happens first, so the ordering is pinned here rather than trusted.

        EVERY refusal, not just the new one: a bad impact, a bad migration, a bad scope,
        a missing scope and an over-long inline notes value all had to move above the
        draw together, and a test that only covered `--scope` would let the next one be
        added below the line."""
        self._new()                                    # no scope at all
        self._new(scope="nonsense")                    # bad scope
        self._new(scope="harness", impact="urgent")    # bad impact
        self._new(scope="harness", migration="maybe")  # bad migration
        self._new(scope="harness", notes="x" * (session.NOTES_INLINE_MAX + 1))
        self.assertEqual(self.draws, [])
        self._new(scope="harness")                     # the one that should draw
        self.assertEqual(len(self.draws), 1)

    def test_a_bogus_value_is_refused_before_anything_is_written(self):
        code, out = self._new(scope="infrastructure")
        self.assertEqual(code, 2)
        self.assertIn("is not one of", out)
        self.assertEqual(list((self.tmp / "work-items").glob("WI-*.md")), [])

    def test_a_scoped_item_is_created_and_says_so(self):
        code, out = self._new(scope="harness")
        self.assertEqual(code, 0)
        self.assertIn("[scope: harness]", out)
        items = session._wi_parse()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["scope"], "harness")

    def test_mixed_is_a_real_answer_not_an_escape_hatch(self):
        """The refusal must not push an honest "materially both" into a coin flip."""
        code, _ = self._new(scope="mixed")
        self.assertEqual(code, 0)
        self.assertEqual(session._wi_parse()[0]["scope"], "mixed")


class StatusTest(StoreBase):
    def _status(self, wid, **kw):
        for k in ("status", "section", "blocked_by", "source", "impact", "migration",
                  "scope"):
            kw.setdefault(k, None)
        kw["id"] = wid
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_status(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_status_scopes_a_legacy_item_as_it_is_touched(self):
        """This verb IS the backfill — one item at a time, by whoever is already holding
        it, instead of a 367-file diff that collides with every lane in flight."""
        self.write(_item("WI-0001"))
        code, out = self._status("WI-0001", scope="harness")
        self.assertEqual(code, 0)
        self.assertIn("scope=harness", out)
        self.assertEqual(self.read("WI-0001")["scope"], "harness")

    def test_an_unscoped_item_reports_itself_as_unset(self):
        self.write(_item("WI-0001"))
        _, out = self._status("WI-0001", status="in-progress")
        self.assertIn("scope=(unset)", out)

    def test_empty_string_withdraws_a_value(self):
        """Same withdrawal argument as `impact`: an operator who realises a call was a
        guess must be able to un-say it, not overwrite it with a second guess."""
        self.write(_item("WI-0001", scope="product"))
        _, out = self._status("WI-0001", scope="")
        self.assertIn("scope=(unset)", out)
        self.assertEqual(self.read("WI-0001")["scope"], "")

    def test_a_bogus_value_exits_nonzero_without_writing(self):
        self.write(_item("WI-0001", scope="harness"))
        code, _ = self._status("WI-0001", scope="nonsense")
        self.assertEqual(code, 2)
        self.assertEqual(self.read("WI-0001")["scope"], "harness")   # unchanged

    def test_scope_is_independent_of_the_release_axis(self):
        self.write(_item("WI-0001", impact="fix", scope="harness"))
        self._status("WI-0001", impact="feature")
        got = self.read("WI-0001")
        self.assertEqual(got["impact"], "feature")
        self.assertEqual(got["scope"], "harness")     # untouched by the other axis


class ShowTest(StoreBase):
    def _show(self, wid):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_show(argparse.Namespace(id=wid))
        return buf.getvalue()

    def test_show_prints_the_value(self):
        self.write(_item("WI-0001", scope="product"))
        self.assertIn("scope:      product", self._show("WI-0001"))

    def test_show_prints_the_row_even_when_unset(self):
        """Same argument as `impact`: an unscoped item is a thing to DO, and hiding the
        row makes the one surface built to show an item in full the one surface that
        cannot see the gap."""
        self.write(_item("WI-0001"))
        out = self._show("WI-0001")
        self.assertIn("scope:", out)
        self.assertIn("unset", out)


class OpsTest(StoreBase):
    """ADR-0076 D1's argument, re-derived for `scope` rather than inherited.

    `scope` exists to split the created/closed trend and to carry a preference for
    FINISHING existing harness work. An obligation recurs by construction — the whole
    property it was given its own kind for — so it is never created-then-closed and can
    never be finished. It cannot appear in the trend the field feeds, and the preference
    the field serves has no purchase on it."""

    def _ops(self, oid, **kw):
        it = {"id": oid, "title": f"Obligation {oid}", "status": "open",
              "cadence": "monthly", "last_completed": "", "last_result": "",
              "due": "", "group": "", "source": "", "notes": ""}
        it.update(kw)
        return it

    def test_an_ops_item_renders_no_scope_line(self):
        text = session._ops_render_item(self._ops("OPS-0001"))
        self.assertNotIn("scope", text)

    def test_an_ops_item_validates_without_one(self):
        """The failure this pins is a silent one: if `scope` were demanded of ops items,
        `wi-check` would go red for a reason nobody wrote down."""
        problems = session._ops_validate(session._ops_parse_text(
            "OPS-0001", session._ops_render_item(self._ops("OPS-0001"))))
        self.assertEqual([p for p in problems if "scope" in p], [])

    def test_a_scope_line_in_an_ops_file_is_not_parsed_into_the_item(self):
        """`OPS_FIELD_RE` does not whitelist it, so it stays prose. Asserted rather than
        assumed, because "the field simply does not exist there" is the claim."""
        got = session._ops_parse_text(
            "OPS-0001",
            "# OPS-0001: t\n\n- status: open\n- cadence: monthly\n- scope: harness\n")
        self.assertNotIn("scope", got)

    def test_there_is_no_fourth_scope_value_for_operational_work(self):
        """The cheap move D1 refused, refused again: a value meaning "this field does not
        apply" reads as a classification in every count that groups by scope."""
        for word in ("ops", "operational", "n/a", "none"):
            self.assertNotIn(word, session.WI_SCOPES)


class FeedTest(StoreBase):
    def test_the_feed_row_carries_scope(self):
        self.write(_item("WI-0001", scope="harness"))
        feed = session._wi_feed_build(self.tmp)
        row = next(r for r in feed["items"] if r["id"] == "WI-0001")
        self.assertEqual(row["scope"], "harness")

    def test_an_unscoped_row_carries_an_empty_string_not_a_missing_key(self):
        """The board reads the feed by key. A missing key and an empty one are
        different bugs on the other side of that contract, and only one of them is ours."""
        self.write(_item("WI-0001"))
        row = next(r for r in session._wi_feed_build(self.tmp)["items"]
                   if r["id"] == "WI-0001")
        self.assertIn("scope", row)
        self.assertEqual(row["scope"], "")

    def test_the_feed_still_serialises(self):
        self.write(_item("WI-0001", scope="mixed"))
        json.dumps(session._wi_feed_build(self.tmp))


class TrendTest(unittest.TestCase):
    """`curate/finish_line.py` — exit criterion 1, now split by scope (R4).

    Built on a real throwaway git repository rather than stubbed counts, because the whole
    subject here is what `git log` output means. The defect these tests exist to hold
    closed was invisible to every stubbed test: `created` counted `"WI-"` occurrences in
    `--name-only` output, and `work-items/notes/WI-0287/<stamp>.md` contains `WI-`, so
    every NOTE RECORD ever added counted as a work item created. Measured on the real
    store on 2026-09-18: 73/149/310/251 by the old count against 55/73/70/54 by the
    corrected one, over a window carrying 531 note-record additions."""

    def setUp(self):
        import finish_line
        self.fl = finish_line
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items" / "notes" / "WI-0001").mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@e.invalid")
        self._git("config", "user.name", "T")
        # Never inherit the runner's signing config: a machine with `commit.gpgsign`
        # on would fail every commit below (`test_fixture_git_defaults` enforces this
        # across every module that commits into a fixture repo).
        self._git("config", "commit.gpgsign", "false")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.tmp), *args], check=True,
                       capture_output=True, text=True)

    def _item(self, wid, scope, status="open"):
        (self.tmp / "work-items" / f"{wid}-a-thing.md").write_text(
            f"# {wid}: a thing\n\n- status: {status}\n- section: next\n"
            f"- impact: fix\n- scope: {scope}\n- version: \n", encoding="utf-8")

    def _commit(self, message, days_ago=3):
        when = (_dt.datetime.now() - _dt.timedelta(days=days_ago)).isoformat()
        self._git("add", "-A")
        subprocess.run(["git", "-C", str(self.tmp), "commit", "-q", "-m", message],
                       check=True, capture_output=True, text=True,
                       env={**__import__("os").environ,
                            "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})

    def test_a_note_record_is_not_a_created_item(self):
        """THE REGRESSION TEST FOR THE REAL DEFECT. One item and one note record land in
        the same commit; the trend must say one item was created, not two."""
        self._item("WI-0001", "harness")
        (self.tmp / "work-items" / "notes" / "WI-0001" / "20260918T0000Z-x.md").write_text(
            "a note record\n", encoding="utf-8")
        self._commit("add one item and one note")
        self.assertEqual(self.fl.wi_weeks(self.tmp)[-1][0], 1)

    def test_created_and_closed_are_attributed_to_the_item(self):
        self._item("WI-0001", "harness")
        self._commit("create")
        self._item("WI-0001", "harness", status="done")
        self._commit("close", days_ago=2)
        by = self.fl.wi_window_by_scope(self.tmp)
        self.assertEqual(by["harness"], (1, 1))

    def test_the_buckets_sum_to_the_headline(self):
        """The split and the total come from one pass over one id list, so they cannot
        tell different stories about the same window. Asserted because a split computed
        by a second mechanism is exactly how a board grows two contradictory numbers."""
        self._item("WI-0001", "harness")
        self._item("WI-0002", "product")
        self._item("WI-0003", "mixed")
        self._commit("three items")
        weeks = self.fl.wi_weeks(self.tmp)
        by = self.fl.wi_window_by_scope(self.tmp)
        self.assertEqual(sum(c for c, _ in weeks),
                         sum(c for c, _ in by.values()))
        self.assertEqual(sum(d for _, d in weeks),
                         sum(d for _, d in by.values()))

    def test_an_unscoped_item_lands_in_its_own_bucket_never_in_product(self):
        """R4's one explicit requirement about the legacy backlog: unset items must stay
        visible and must never be silently counted as product."""
        (self.tmp / "work-items" / "WI-0007-legacy.md").write_text(
            "# WI-0007: legacy\n\n- status: open\n- section: next\n- impact: fix\n",
            encoding="utf-8")
        self._commit("a pre-field item")
        by = self.fl.wi_window_by_scope(self.tmp)
        self.assertEqual(by[self.fl.SCOPE_UNSET_BUCKET], (1, 0))
        self.assertEqual(by["product"], (0, 0))

    def test_every_bucket_is_reported_even_at_zero(self):
        """A bucket that disappears when empty is one a reader cannot tell from a bucket
        nobody counted."""
        self._item("WI-0001", "harness")
        self._commit("one item")
        by = self.fl.wi_window_by_scope(self.tmp)
        self.assertEqual(sorted(by), sorted(self.fl.SCOPE_BUCKETS))

    def test_the_scope_map_reads_the_store_not_the_history(self):
        """The field is authored and carries no history of its own, so an item created
        three weeks ago is attributed to whatever it says NOW."""
        self._item("WI-0001", "product")
        self._commit("create as product")
        self._item("WI-0001", "harness")
        self._commit("reclassify", days_ago=1)
        self.assertEqual(self.fl.wi_scope_map(self.tmp)["WI-0001"], "harness")
        self.assertEqual(self.fl.wi_window_by_scope(self.tmp)["harness"][0], 1)

    def test_the_verdict_still_reads_the_total(self):
        """The split is evidence, not a second verdict. Exit criterion 1 is about the
        whole store: a week that closed more harness work than it opened while opening a
        pile of product work has not met it."""
        self.assertEqual(self.fl.verdict_ratio([(1, 5), (1, 5), (1, 5)]), "PASS")
        self.assertEqual(self.fl.verdict_ratio([(9, 1), (1, 5), (1, 5)]), "FAIL")


if __name__ == "__main__":
    unittest.main()
