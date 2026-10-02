"""Operational items — the `ops` kind (ADR-0076).

Each test pins one of the ADR's decisions, and each guards a failure the WI-only model
actually produced. The through-line: operational work RECURS, so its correct record is a
DATE and a RESULT, not a status. These are the properties that make that true:

  D3  A recurring item's `due` is DERIVED (last-completed + cadence) and never typed —
      typing it reintroduces the silently-lapsing chore the kind exists to catch.
  D4  A run records a RESULT. `findings` is not `fail`: restore drill #1 succeeded at
      telling us the data was unrecoverable, and a store that renders that identically
      to an uneventful drill records the wrong fact about its most valuable run.
  D5  A recurring obligation NEVER closes; a one-time one does. `done` on a recurring
      item is invalid, not merely discouraged.
  D6  Identity splits, visibility does NOT — one chart, both namespaces. An obligation
      kept on its own surface is how a quarterly drill quietly becomes an annual one.
"""

import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


class OpsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "ops-items").mkdir(parents=True, exist_ok=True)
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, oid, title="An obligation", **fields):
        item = {"id": oid, "title": title, "status": "open", "cadence": "",
                "last_completed": "", "last_result": "", "due": "", "group": "",
                "source": "", "notes": ""}
        item.update(fields)
        session._ops_write_item(item)
        return item

    def read(self, oid):
        """One item as a READER sees it — file fields plus its note records compiled in.

        WI-0181 moved appended notes (including `ops-ran`'s run receipts) out of the item
        file into per-note records, so parsing the file alone answers about STORAGE while
        every real consumer (`_ops_parse`, `ops-show`, the chart, the feed) asks about the
        item. A helper that keeps reading the file alone turns "the run history stays with
        the obligation" into "the run history is in this particular file", which is the
        assertion the change is deliberately breaking."""
        name = session._ops_filename_for(oid)
        item = session._ops_parse_text(
            oid, (session._ops_dir() / name).read_text(encoding="utf-8"))
        item["notes"] = session._notes_compiled(session.OPS_DIRNAME, item)
        return item


class WeeklySiblingsRenderTest(OpsBase):
    def test_weekly_curation_and_findings_render_beside_each_other_never_run(self):
        self.write("OPS-0007", "Weekly findings review", cadence="weekly",
                   group="Governance", last_completed="")
        self.write("OPS-0008", "Weekly canon curation", cadence="weekly",
                   group="Governance", last_completed="")
        rendered = session._ops_render_section()
        rows = rendered.strip().splitlines()
        self.assertEqual(len(rows), 2)
        for row, oid in zip(rows, ("OPS-0007", "OPS-0008")):
            self.assertIn("[" + oid + "]", row)
            self.assertIn("*never run* (weekly)", row)
            self.assertNotIn("Last run", row)


class DueDerivationTest(OpsBase):
    """D3 — the date model."""

    def test_a_recurring_due_is_last_completed_plus_the_cadence(self):
        self.assertEqual(session._ops_derive_due("quarterly", "2026-07-21"), "2026-10-21")
        self.assertEqual(session._ops_derive_due("monthly", "2026-07-21"), "2026-08-21")
        self.assertEqual(session._ops_derive_due("annual", "2026-07-21"), "2027-07-21")

    def test_a_never_run_recurring_item_has_no_derivable_due(self):
        # Owed NOW. An empty due is honest; a fabricated one would claim a schedule that
        # was never established.
        self.assertEqual(session._ops_derive_due("quarterly", ""), "")

    def test_a_one_time_items_due_is_whatever_was_authored(self):
        self.assertEqual(session._ops_derive_due("", "", "2026-09-01"), "2026-09-01")

    def test_month_end_clamps_instead_of_overflowing(self):
        # A monthly obligation completed on the 31st must not skip a 30-day month, and
        # must never produce an invalid date like 2026-02-31.
        self.assertEqual(session._ops_derive_due("monthly", "2026-01-31"), "2026-02-28")
        self.assertEqual(session._ops_derive_due("monthly", "2026-03-31"), "2026-04-30")
        self.assertEqual(session._ops_derive_due("annual", "2028-02-29"), "2029-02-28")

    def test_a_year_boundary_rolls_correctly(self):
        self.assertEqual(session._ops_derive_due("quarterly", "2026-11-15"), "2027-02-15")

    def test_weekly_rolls_a_fixed_seven_days_and_keeps_the_weekday(self):
        # A weekly review is anchored to one weekday, so the property that matters is not
        # just "+7" but that a run lands on the same weekday next week — over a month
        # boundary, and over a year boundary, where a month-based roll-forward would
        # move the weekday.
        self.assertEqual(session._ops_derive_due("weekly", "2026-09-11"), "2026-09-18")
        self.assertEqual(session._ops_derive_due("weekly", "2026-09-25"), "2026-10-02")
        self.assertEqual(session._ops_derive_due("weekly", "2026-12-25"), "2027-01-01")
        for run, nxt in (("2026-09-11", "2026-09-18"), ("2026-12-25", "2027-01-01")):
            self.assertEqual(
                datetime.strptime(run, "%Y-%m-%d").weekday(),
                datetime.strptime(nxt, "%Y-%m-%d").weekday(),
                "a weekly cadence must not drift off its weekday")

    def test_a_never_run_weekly_item_has_no_derivable_due(self):
        # Same rule as every other cadence: owed NOW, rendered as an empty due rather
        # than a fabricated first date.
        self.assertEqual(session._ops_derive_due("weekly", ""), "")
        self.assertEqual(session._ops_derive_due("weekly", "", "2026-09-11"), "")


class RunRecordingTest(OpsBase):
    """D4/D5 — a run stamps a date AND a result, and recurring never closes."""

    def _ran(self, oid, result, on):
        args = type("A", (), {"id": oid, "result": result, "on": on, "notes": ""})()
        with redirect_stdout(io.StringIO()):
            session.cmd_ops_ran(args)
        return self.read(oid)

    def test_running_a_recurring_item_rolls_due_and_leaves_it_open(self):
        self.write("OPS-0001", cadence="quarterly")
        item = self._ran("OPS-0001", "pass", "2026-07-21")
        self.assertEqual(item["last_completed"], "2026-07-21")
        self.assertEqual(item["last_result"], "pass")
        self.assertEqual(item["due"], "2026-10-21")
        self.assertEqual(item["status"], "open",
                         "a standing obligation is never finished by running it")

    def test_running_a_one_time_item_closes_it(self):
        self.write("OPS-0002", due="2026-07-25")
        item = self._ran("OPS-0002", "pass", "2026-07-26")
        self.assertEqual(item["status"], "done",
                         "a one-time check IS finished when it has run")

    def test_findings_is_a_distinct_result_from_fail(self):
        # The drill-#1 case: it did not fail, it succeeded at telling us something bad.
        self.write("OPS-0003", cadence="quarterly")
        item = self._ran("OPS-0003", "findings", "2026-07-21")
        self.assertEqual(item["last_result"], "findings")
        self.assertIn("findings", session.OPS_RESULTS)
        self.assertNotEqual(item["last_result"], "fail")

    def test_the_run_history_stays_with_the_obligation(self):
        self.write("OPS-0004", cadence="quarterly")
        args = type("A", (), {"id": "OPS-0004", "result": "findings", "on": "2026-07-21",
                              "notes": "Data did not restore."})()
        with redirect_stdout(io.StringIO()):
            session.cmd_ops_ran(args)
        notes = self.read("OPS-0004")["notes"]
        self.assertIn("2026-07-21", notes)
        self.assertIn("findings", notes)
        self.assertIn("Data did not restore.", notes)


class ValidationTest(OpsBase):
    """D5 — `done` on a recurring obligation is INVALID, and the store can say so."""

    def test_done_on_a_recurring_obligation_is_rejected(self):
        item = self.write("OPS-0001", cadence="quarterly", status="done")
        problems = session._ops_validate(item)
        self.assertTrue(any("never finished" in p for p in problems), problems)

    def test_done_on_a_one_time_obligation_is_fine(self):
        item = self.write("OPS-0002", status="done", due="2026-07-25")
        self.assertEqual(session._ops_validate(item), [])

    def test_a_hand_typed_due_that_disagrees_with_the_cadence_is_caught(self):
        # The whole point of deriving `due` is that nobody has to remember to push it
        # forward. A stale one must therefore be detectable, not silently trusted.
        item = self.write("OPS-0003", cadence="quarterly",
                          last_completed="2026-07-21", due="2099-01-01")
        problems = session._ops_validate(item)
        self.assertTrue(any("computed, never" in p for p in problems), problems)

    def test_an_unknown_cadence_or_result_is_caught(self):
        item = self.write("OPS-0004", cadence="fortnightly")
        self.assertTrue(session._ops_validate(item))
        item2 = self.write("OPS-0005", last_result="mostly-ok")
        self.assertTrue(session._ops_validate(item2))

    def test_ops_problems_surface_through_the_ONE_validate_command(self):
        # A second validate verb is a second thing to remember to run — and the
        # obligation this kind exists to catch is exactly the one nobody remembers.
        self.write("OPS-0006", cadence="quarterly", status="done")
        problems = session._wi_validate()
        self.assertTrue(any("OPS-0006" in p for p in problems), problems)


class StateTest(OpsBase):
    """The STATE cell answers 'is this owed?', not 'is anyone on it?'."""

    def test_a_never_run_recurring_item_reads_never_run(self):
        item = self.write("OPS-0001", cadence="quarterly")
        self.assertEqual(session._ops_state(item, "2026-07-29"), "never run")

    def test_a_past_due_item_reads_overdue(self):
        item = self.write("OPS-0002", due="2026-07-25")
        self.assertEqual(session._ops_state(item, "2026-07-29"), "overdue")

    def test_a_future_due_item_reads_its_date(self):
        item = self.write("OPS-0003", cadence="quarterly",
                          last_completed="2026-07-21", due="2026-10-21")
        self.assertEqual(session._ops_state(item, "2026-07-29"), "due 2026-10-21")

    def test_an_ops_item_is_not_measured_by_dev_staleness(self):
        # Open for three months is stalled for a dev item and ON SCHEDULE for this one.
        item = self.write("OPS-0004", cadence="quarterly",
                          last_completed="2026-07-21", due="2026-10-21")
        self.assertNotIn(session._ops_state(item, "2026-09-29"), ("overdue", "never run"))


class OneChartTest(OpsBase):
    """D6 — identity splits, visibility does not."""

    def _seed_dev(self):
        d = self.tmp / "work-items"
        d.mkdir(parents=True, exist_ok=True)
        (d / "WI-0001-x.md").write_text(
            "# WI-0001: A dev item\n\n- status: open\n- section: next\n"
            "- blocked-by: \n- group: \n- source: \n- impact: fix\n- version: \n\n",
            encoding="utf-8")

    def test_both_namespaces_render_in_one_chart(self):
        self._seed_dev()
        self.write("OPS-0001", title="An obligation", cadence="quarterly")
        text = "\n".join(session._wi_chart_lines())
        self.assertIn("WI-0001", text)
        self.assertIn("OPS-0001", text)

    def test_the_kind_is_visible_from_the_id_itself(self):
        # D6 wants both kinds distinguishable in one view. The `WI-`/`OPS-` prefix does
        # that, which is why the KIND column was removed rather than kept alongside it.
        self._seed_dev()
        self.write("OPS-0001", title="An obligation", cadence="quarterly")
        rows = [r for r in session._wi_chart_lines() if "WI-0001" in r or "OPS-0001" in r]
        self.assertTrue(any("WI-" in r for r in rows), rows)
        self.assertTrue(any("OPS-" in r for r in rows), rows)

    def test_an_ops_row_carries_a_date_shaped_state_not_a_dev_status(self):
        # The other half of telling them apart: `open` is not an answer to "is this
        # owed?", so an ops row never renders one.
        self.write("OPS-0001", title="An obligation", cadence="quarterly",
                   last_completed="2026-07-21", due="2026-10-21")
        row = [r for r in session._wi_chart_lines() if "OPS-0001" in r][0]
        self.assertIn("2026-10-21", row, row)

    def test_an_overdue_obligation_is_coloured_as_needing_attention(self):
        # `-` is the red prefix in the diff fence. Overdue is not "in progress", but it
        # is the other state that means "this needs you now".
        self.write("OPS-0001", title="An obligation", due="2020-01-01")
        row = [r for r in session._wi_chart_lines() if "OPS-0001" in r][0]
        self.assertTrue(row.startswith("-"), row)

    def test_there_is_no_kind_column(self):
        # Removed on sight (session ~109): the id already carries the kind, so a column
        # restating it read `dev` on forty-one consecutive rows. Pinned because D6 asks
        # for one and a future reader may "restore" it.
        self._seed_dev()
        self.write("OPS-0001", title="An obligation", cadence="quarterly")
        header = [r for r in session._wi_chart_lines() if "TITLE" in r][0]
        self.assertNotIn("KIND", header, header)

    def test_ops_rows_reach_the_startup_list_too(self):
        # The startup banner is the surface the operator actually reads; an obligation
        # absent from it is an obligation nobody sees.
        self._seed_dev()
        self.write("OPS-0001", title="An obligation", cadence="quarterly")
        text = "\n".join(session._wi_render_snapshot_lines(colour=False))
        self.assertIn("OPS-0001", text)


class SupersedeTest(OpsBase):
    """D7 — the three migrated items keep their WI numbers as signposts."""

    def test_a_wi_item_may_name_an_OPS_successor(self):
        # ADR-0073 requires a superseded item to name what replaced it. Before ADR-0076
        # that pattern was WI-only, which would have rejected the very migration D7
        # prescribes.
        d = self.tmp / "work-items"
        d.mkdir(parents=True, exist_ok=True)
        (d / "WI-0001-x.md").write_text(
            "# WI-0001: Old operational item\n\n- status: superseded\n- section: next\n"
            "- blocked-by: \n- group: \n- source: \n- impact: fix\n- version: \n\n"
            "SUPERSEDED by OPS-0002.\n", encoding="utf-8")
        problems = [p for p in session._wi_validate() if "names no successor" in p]
        self.assertEqual(problems, [], "an OPS id is a valid successor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
