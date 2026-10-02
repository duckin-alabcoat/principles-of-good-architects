"""`_wi_render_next_section` / `_wi_render_backlog_section` — the ROADMAP.md sections
that render FROM the work-item store (WI-0071).

The defect these pin: both render functions hand-rolled `status != "done"` where every
other consumer in the file uses `WI_TERMINAL = ("done", "superseded")`. Net effect was
the worst way round — a SUPERSEDED item vanished from `wi-list`, the startup snapshot
and blocked-by liveness, while still rendering into `ROADMAP.md`. Invisible on every
machine-facing surface; visible in the one human-facing deliverable. An Architect
supersedes an item, watches it disappear from the list, and never learns it is still in
the roadmap.

It is more than stale rows: it preferentially surfaces exactly the items an Architect
has decided are WRONG, in the file whose entire job is to be the honest status view.
A member Architect found it by superseding an item whose title asserted a claim they had just
disproved, and watching it render anyway; their workaround was to retitle the item to
state its own supersession, which does not scale.

The code fix landed 2026-08-04 (`3d97887`) and shipped to the fleet — but nothing
pinned it, so it could regress silently, which is the same failure mode one level up.
That is what these tests are for.
"""

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

ITEMS = [
    ("WI-0001", "An open next item", "open", "next"),
    ("WI-0002", "A superseded next item", "superseded", "next"),
    ("WI-0003", "A finished next item", "done", "next"),
    ("WI-0004", "An open backlog item", "open", "backlog"),
    ("WI-0005", "A superseded backlog item", "superseded", "backlog"),
    ("WI-0006", "A finished backlog item", "done", "backlog"),
    ("WI-0007", "A blocked item still in flight", "blocked", "next"),
]


class RenderBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        d = self.tmp / "work-items"
        d.mkdir(parents=True, exist_ok=True)
        for wid, title, status, section in ITEMS:
            (d / f"{wid}-slug.md").write_text(
                f"# {wid}: {title}\n\n- status: {status}\n- section: {section}\n"
                f"- blocked-by: \n- group: \n- source: \n\n", encoding="utf-8")
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)


class TerminalItemsDoNotRenderTest(RenderBase):

    def test_superseded_item_is_absent_from_next(self):
        # THE defect. Before the fix this id rendered as a bold bullet still asserting
        # whatever claim its author had superseded it for.
        self.assertNotIn("WI-0002", session._wi_render_next_section())

    def test_superseded_item_is_absent_from_backlog(self):
        self.assertNotIn("WI-0005", session._wi_render_backlog_section())

    def test_done_item_is_absent_from_next(self):
        self.assertNotIn("WI-0003", session._wi_render_next_section())

    def test_done_item_is_absent_from_backlog(self):
        self.assertNotIn("WI-0006", session._wi_render_backlog_section())


class LiveItemsStillRenderTest(RenderBase):
    """The other direction — a filter that drops everything would pass the tests above
    while destroying the deliverable."""

    def test_open_next_item_renders(self):
        self.assertIn("WI-0001", session._wi_render_next_section())

    def test_open_backlog_item_renders(self):
        self.assertIn("WI-0004", session._wi_render_backlog_section())

    def test_a_non_terminal_status_is_not_treated_as_terminal(self):
        # `blocked` is in flight, not finished. Only WI_TERMINAL is terminal.
        self.assertIn("WI-0007", session._wi_render_next_section())

    def test_sections_do_not_leak_into_each_other(self):
        self.assertNotIn("WI-0004", session._wi_render_next_section())
        self.assertNotIn("WI-0001", session._wi_render_backlog_section())


class AgreesWithEveryOtherConsumerTest(RenderBase):
    """The root cause was a SECOND, narrower definition of 'terminal' — so pin the
    property that there is only one, rather than only the two call sites it bit."""

    def test_renderers_hide_exactly_what_wi_terminal_names(self):
        rendered = (session._wi_render_next_section()
                    + session._wi_render_backlog_section())
        for it in session._wi_parse():
            terminal = it.get("status") in session.WI_TERMINAL
            self.assertEqual(
                it["id"] not in rendered, terminal,
                f"{it['id']} (status={it.get('status')}) renders inconsistently with "
                f"WI_TERMINAL — the renderers have drifted from every other consumer "
                f"again (WI-0071)")

    def test_superseded_is_actually_in_the_terminal_set(self):
        # If this ever stops holding, the tests above pass vacuously.
        self.assertIn("superseded", session.WI_TERMINAL)
        self.assertIn("done", session.WI_TERMINAL)


if __name__ == "__main__":
    unittest.main()
