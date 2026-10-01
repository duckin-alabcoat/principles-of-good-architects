"""`session.py wi-chart` — the user's approved work-item chart, as code (WI-0039).

The chart is a *format* the user signed off on, so these tests pin the properties that
made him sign off, not the incidental appearance. Each one guards a failure the
hand-rolled version actually produced before the renderer existed:

  1. COMPLETENESS is about rows — every item, every state, exactly once. A filtered
     chart reads as the whole list and is the failure this whole surface exists to
     prevent.
  2. READABILITY is about columns — a fixed grid that stays aligned. A single cell
     that overflows its width pushes the right border out and destroys the rectangle.
  3. Long titles WRAP, never truncate — truncation is completeness failing quietly.
  4. Colour carries state, in the user's assignment (green closed, red in-progress).
  5. When rows ARE dropped (`--open-only`), the chart says so out loud
     (`declare-what-a-check-assumes` — a partial list must never read as complete).
"""

import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

LONG = ("Resolve user_profile and the banner's inbox line against the main checkout, "
        "not the lane root")
ITEMS = [
    ("WI-0001", "Short open item", "open", "next"),
    ("WI-0002", LONG, "in-progress", "next"),
    ("WI-0003", "Finished thing", "done", "next"),
    ("WI-0004", "Folded into another item", "superseded", "backlog"),
    ("WI-0005", "A backlog item", "open", "backlog"),
]


def _seed(root, items=ITEMS):
    d = root / "work-items"
    d.mkdir(parents=True, exist_ok=True)
    for wid, title, status, section in items:
        (d / f"{wid}-slug.md").write_text(
            f"# {wid}: {title}\n\n- status: {status}\n- section: {section}\n"
            f"- blocked-by: \n- group: \n- source: \n\n", encoding="utf-8")
    return d


class ChartBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _seed(self.tmp)
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def chart(self, **kw):
        return session._wi_chart_lines(**kw)

    def body(self, lines):
        """Just the bordered rows — no fence, no trailing omission note."""
        return [ln for ln in lines if ln.lstrip(" +-").startswith(("│", "┌", "├", "└"))]


class CompletenessTest(ChartBase):
    def test_every_item_appears_exactly_once(self):
        text = "\n".join(self.chart())
        for wid, _, _, _ in ITEMS:
            self.assertEqual(text.count(wid), 1, f"{wid} should appear exactly once")

    def test_terminal_rows_are_included_by_default(self):
        text = "\n".join(self.chart())
        # done + superseded are the rows a "current work" view would drop. The chart
        # is the WHOLE list, so they are present and merely coloured differently.
        self.assertIn("WI-0003", text)
        self.assertIn("WI-0004", text)

    def test_empty_store_does_not_crash(self):
        shutil.rmtree(self.tmp / "work-items")
        lines = self.chart()
        self.assertEqual(len(lines), 1)
        self.assertIn("none", lines[0])


class GridTest(ChartBase):
    def test_all_rows_are_exactly_the_same_width(self):
        rows = self.body(self.chart())
        widths = {len(r) for r in rows}
        self.assertEqual(len(widths), 1, f"ragged grid: widths {sorted(widths)}")

    def test_a_long_unbreakable_token_cannot_widen_a_row(self):
        # No word boundary anywhere in the cell — textwrap must break INSIDE the token
        # rather than emit one over-long line, or the right border walks off the grid.
        _seed(self.tmp, [("WI-0009", "x" * 200, "open", "next")])
        rows = self.body(self.chart())
        self.assertEqual(len({len(r) for r in rows}), 1)

    def test_borders_open_and_close_the_table(self):
        rows = self.body(self.chart())
        self.assertTrue(rows[0].startswith(" ┌"))
        self.assertTrue(rows[-1].startswith(" └"))
        # ...and the bottom border replaces the last separator rather than following it.
        self.assertFalse(rows[-2].startswith(" ├"))


class WrapTest(ChartBase):
    def test_a_long_title_wraps_without_losing_a_word(self):
        text = "\n".join(self.chart())
        # Reassemble WI-0002's title out of the cells it was wrapped across. TITLE is the
        # LAST cell, addressed from the right — the earlier version indexed it at a fixed
        # position, so adding the ADR-0076 `KIND` column broke a test that is not about
        # column layout at all. Right-anchoring keeps it pinned to the property it names.
        cells = [ln.rsplit("│", 2)[-2].strip() for ln in text.splitlines()
                 if ln.count("│") >= 2 and not ln.lstrip().startswith(("┌", "├", "└"))]
        joined = " ".join(c for c in cells if c)
        for word in LONG.split():
            self.assertIn(word, joined, f"'{word}' was lost in wrapping")

    def test_no_ellipsis_truncation(self):
        self.assertNotIn("…", "\n".join(self.chart()))


class DependencyColumnTest(ChartBase):
    """operator, session ~109: the chart did not show dependencies. It could not: a blocked item
    rendered as plain `open` here while the startup list showed it as `blocked` — two
    surfaces over one store disagreeing about whether work is actionable."""

    def _seed_blocked(self, blocked_by):
        d = self.tmp / "work-items"
        for wid, dep in (("WI-0001", ""), ("WI-0009", blocked_by)):
            (d / f"{wid}-slug.md").write_text(
                f"# {wid}: An item\n\n- status: open\n- section: next\n"
                f"- blocked-by: {dep}\n- group: \n- source: \n\n", encoding="utf-8")

    def test_a_blocked_item_says_blocked_and_names_its_blocker(self):
        self._seed_blocked("WI-0001")
        row = [r for r in self.chart() if "WI-0009" in r][0]
        self.assertIn("blocked", row)
        self.assertIn("0001", row, "the blocking id must be visible, not just the state")

    def test_a_dependency_on_CLOSED_work_is_not_a_dependency(self):
        # WI-0003 is `done` in the base fixture. An item waiting on finished work is
        # actionable, and rendering it blocked would manufacture a bottleneck.
        self._seed_blocked("WI-0003")
        row = [r for r in self.chart() if "WI-0009" in r][0]
        self.assertNotIn("blocked", row, row)

    def test_two_blockers_both_appear_without_splitting_an_id(self):
        self._seed_blocked("WI-0001,WI-0002")
        text = "\n".join(r for r in self.chart() if "WI-0009" in r)
        self.assertIn("0001", text)
        self.assertIn("0002", text)


class LeverageTest(ChartBase):
    """the operator's ruling, session ~109: the chart focuses on blocking items, showing how many
    other items each one would unblock. A blocked-by list says what is stopping you; this
    says where the leverage is, which a backlog cannot answer by being read."""

    def _rows(self, spec):
        d = self.tmp / "work-items"
        for f in d.glob("*.md"):
            f.unlink()
        for wid, dep in spec:
            (d / f"{wid}-slug.md").write_text(
                f"# {wid}: An item\n\n- status: open\n- section: next\n"
                f"- blocked-by: {dep}\n- group: \n- source: \n\n", encoding="utf-8")
        return session._chart_rows()

    def test_it_counts_the_items_a_close_would_make_actionable(self):
        rows = self._rows([("WI-0001", ""), ("WI-0002", "WI-0001"),
                           ("WI-0003", "WI-0001")])
        self.assertEqual(session._wi_frees(rows)["WI-0001"], 2)

    def test_being_unblocked_is_not_being_done(self):
        # The correctness case. Closing 0001 frees 0002 and 0003; it does NOT free 0004,
        # which waits on 0001 AND 0003 — 0003 is merely actionable, not finished.
        # Cascading through freed items would score 0001 at 3 and overstate exactly the
        # item a reader would most trust.
        rows = self._rows([("WI-0001", ""), ("WI-0002", "WI-0001"),
                           ("WI-0003", "WI-0001"), ("WI-0004", "WI-0001,WI-0003")])
        self.assertEqual(session._wi_frees(rows)["WI-0001"], 2)

    def test_an_item_nothing_waits_on_has_no_leverage(self):
        rows = self._rows([("WI-0001", ""), ("WI-0002", "")])
        self.assertEqual(session._wi_frees(rows)["WI-0002"], 0)

    def test_leverage_is_shown_where_the_blockers_would_be(self):
        self._rows([("WI-0001", ""), ("WI-0002", "WI-0001"), ("WI-0003", "WI-0001")])
        row = [r for r in self.chart() if "WI-0001" in r][0]
        self.assertIn("frees 2", row, row)

    def test_a_blocked_item_shows_its_blockers_not_its_leverage(self):
        # Both directions share one column; what is stopping you wins, because you cannot
        # act on the leverage until it clears.
        self._rows([("WI-0001", ""), ("WI-0002", "WI-0001"), ("WI-0003", "WI-0002")])
        row = [r for r in self.chart() if "WI-0002" in r][0]
        self.assertIn("0001", row)
        self.assertNotIn("frees", row, row)


class ColourTest(ChartBase):
    def _prefix_for(self, lines, wid):
        return next(ln[0] for ln in lines if wid in ln and "│" in ln)

    def test_green_marks_closed_red_marks_in_progress(self):
        lines = self.chart()
        self.assertEqual(self._prefix_for(lines, "WI-0003"), "+")   # done
        self.assertEqual(self._prefix_for(lines, "WI-0004"), "+")   # superseded
        self.assertEqual(self._prefix_for(lines, "WI-0002"), "-")   # in-progress
        self.assertEqual(self._prefix_for(lines, "WI-0001"), " ")   # open

    def test_a_wrapped_row_carries_its_colour_on_every_line(self):
        # A two-line in-progress row whose continuation line lost the prefix would
        # render half-red — the state would read as ambiguous.
        lines = [ln for ln in self.chart() if "│" in ln]
        i = next(n for n, ln in enumerate(lines) if "WI-0002" in ln)
        self.assertEqual(lines[i][0], "-")
        self.assertEqual(lines[i + 1][0], "-")

    def test_diff_fence_is_emitted(self):
        lines = self.chart()
        # Without the fence the prefixes are just stray punctuation — the fence IS the
        # colour mechanism, so the renderer emits it rather than leaving it to judgment.
        self.assertEqual(lines[0], "```diff")
        self.assertEqual(lines[-1], "```")


class OmissionTest(ChartBase):
    def test_open_only_drops_terminal_rows_and_says_so(self):
        lines = self.chart(all_states=False)
        text = "\n".join(lines)
        self.assertNotIn("WI-0003", text)
        self.assertNotIn("WI-0004", text)
        self.assertIn("2 done/superseded row(s) omitted", lines[-1])

    def test_full_chart_claims_no_omission(self):
        self.assertNotIn("omitted", "\n".join(self.chart()))


class CliTest(ChartBase):
    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_chart(type("A", (), kw)())
        return buf.getvalue()

    def test_cli_default_prints_the_whole_list(self):
        out = self._run(open_only=False)
        for wid, _, _, _ in ITEMS:
            self.assertIn(wid, out)

    def test_cli_open_only_flag_reaches_the_renderer(self):
        self.assertIn("omitted", self._run(open_only=True))


class VersionInTheChartTest(unittest.TestCase):
    """A version is a NAMED OUTCOME, not a number. The manifest already carries the
    sentence; until now it lived only in `releases/`, where nobody working the list saw
    it."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir()
        self._root = session.ROOT
        session.ROOT = self.tmp
        for wid in ("WI-0001", "WI-0002"):
            session._wi_write_item(
                {"id": wid, "title": f"Item {wid}", "status": "open", "section": "next",
                 "blocked_by": [], "group": "", "source": "", "impact": "feature",
                 "migration": "", "version": "", "notes": ""})

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _declare(self):
        d = session._rel_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "6.0.0.md").write_text("- version: 6.0.0\n", encoding="utf-8")
        (d / "7.0.0-manifest.md").write_text(
            "- version: 7.0.0\n"
            "- theme: poga runs independent of the model\n"
            "- target: 2026-08-10\n"
            "- items: WI-0001\n", encoding="utf-8")

    def test_the_chart_names_the_version_by_its_outcome(self):
        self._declare()
        text = "\n".join(session._wi_chart_lines())
        self.assertIn("Version 7 — poga runs independent of the model", text)

    def test_the_banner_says_it_is_not_yet_shipped_and_names_the_target(self):
        """A declared major that read like a shipped one is the confusion ADR-0078 D1
        draws the manifest/record line to prevent."""
        self._declare()
        text = "\n".join(session._wi_chart_lines())
        self.assertIn("declared, not yet shipped", text)
        self.assertIn("2026-08-10", text)

    def test_only_the_ruled_in_rows_carry_the_version(self):
        self._declare()
        rows = [l for l in session._wi_chart_lines() if "WI-000" in l]
        self.assertTrue(any("WI-0001" in l and "7.0.0" in l for l in rows))
        self.assertFalse(any("WI-0002" in l and "7.0.0" in l for l in rows),
                         "an unpromised item must not display a version it was never in")

    def test_a_shipped_stamp_wins_over_the_declared_set(self):
        """Once the release step has stamped an item, that is the fact; the manifest is
        only a plan."""
        self._declare()
        it = next(i for i in session._wi_parse() if i["id"] == "WI-0001")
        it["version"] = "6.1.0"
        session._wi_write_item(it)
        rows = [l for l in session._wi_chart_lines() if "WI-0001" in l]
        self.assertTrue(any("6.1.0" in l for l in rows))

    def test_no_declared_major_means_no_banner(self):
        """The chart must not grow a header for a promise nobody made."""
        text = "\n".join(session._wi_chart_lines())
        self.assertNotIn("declared, not yet shipped", text)
        self.assertTrue(text.lstrip().startswith("```diff"))


if __name__ == "__main__":
    unittest.main()
