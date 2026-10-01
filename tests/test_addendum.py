"""Post-close work has a verb, and the close stays irreversible (WI-0100).

Sessions 126 and 127 both ran the full close — `ended` stamped, banner printed — and in
both the conversation continued afterwards with substantial work; in 127 that was three
fleet-wide fixes shipped after the session was formally over. Twice in
consecutive sessions is the `add-structural-guard-on-recurrence` threshold: the first is
a miss, the second means discipline is not the fix.

The hand-written addendum was already the honest FORM. It was not a MECHANISM, and the
difference is exactly what these tests pin: the compiled handoff, the session index and
`STATUS.md` are all downstream of the journal, and each was verified stale in session 127
while the work sat in the body underneath them.

What is deliberately NOT built is the tempting answer — a reopen. Nothing here clears
`ended`. A close you can quietly take back is the record-integrity harm
`a-close-is-the-banner-not-the-sentence` exists to prevent, and the item was explicit
that the refusal must not weaken. So the strongest tests here are the negative ones: that
every close field survives, and that an OPEN journal is refused outright.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import pathlib
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402

CLOSED_FM = {
    "session-id": "20260810T0000Z-runner-aaaa",
    "claude-session-id": "csid-me",
    "started": "2026-08-10T00:00:00Z",
    "ended": "2026-08-10T08:00:00Z",
    "duration": "8h 0m",
    "closed-by": "session.py end",
    "close-confirm": "yes close it out",
    "machine": "Runner",
    "role-doc-version": "2.55.0",
    "title": "a closed session",
}


class AddendumBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.jdir = self.tmp / "sessions" / "journal"
        self.jdir.mkdir(parents=True)
        self._save = {k: getattr(session, k) for k in ("JOURNAL_DIR", "CFG", "ROOT")}
        session.JOURNAL_DIR = self.jdir
        session.ROOT = self.tmp
        session.CFG = {**(self._save["CFG"] or {}), "tz": ZoneInfo("UTC")}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_journal(self, body="### What happened\n\n- Real work.\n", **over):
        fm = {**CLOSED_FM, **over}
        p = self.jdir / f"{fm['session-id']}.md"
        p.write_text(session._rerender_journal(fm, body), encoding="utf-8")
        return p

    def run_addendum(self, **kw):
        ns = argparse.Namespace(**{"note": "post-close work", "session_id": None,
                                   "dry_run": False, **kw})
        buf = io.StringIO()
        with mock.patch.object(session, "_on_worktree_lane", return_value=False), \
             mock.patch.object(session, "run_compile") as rc:
            with redirect_stdout(buf):
                session.cmd_addendum(ns)
        return buf.getvalue(), rc


class ClosePreservationTest(AddendumBase):
    """The load-bearing negative. Nothing here may weaken the close."""

    def test_every_close_field_survives_untouched(self):
        p = self.write_journal()
        self.run_addendum(session_id=CLOSED_FM["session-id"])
        fm, _ = session.parse_journal(p.read_text(encoding="utf-8"))
        for field in ("ended", "duration", "closed-by", "close-confirm"):
            self.assertEqual(fm[field], CLOSED_FM[field],
                             f"{field} changed — the close was weakened")

    def test_the_original_narrative_is_not_rewritten(self):
        p = self.write_journal(body="### What happened\n\n- The original.\n")
        self.run_addendum(session_id=CLOSED_FM["session-id"])
        self.assertIn("- The original.", p.read_text(encoding="utf-8"))

    def test_an_open_journal_is_refused(self):
        """An open session already has somewhere for its narrative. Routing live work
        through an addendum would manufacture a post-close record for a session still
        running — the mirror of the falsehood `journal-title` refuses."""
        self.write_journal(ended="", duration="", closed_by="")
        with self.assertRaises(SystemExit) as cm:
            self.run_addendum(session_id=CLOSED_FM["session-id"])
        self.assertIn("still OPEN", str(cm.exception))

    def test_an_open_journal_is_not_modified_by_the_refusal(self):
        p = self.write_journal(ended="")
        before = p.read_text(encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_addendum(session_id=CLOSED_FM["session-id"])
        self.assertEqual(p.read_text(encoding="utf-8"), before)


class AppendTest(AddendumBase):
    def test_the_note_lands_in_the_body_under_a_stamped_heading(self):
        p = self.write_journal()
        self.run_addendum(session_id=CLOSED_FM["session-id"],
                          note="shipped three fleet fixes after the close")
        text = p.read_text(encoding="utf-8")
        self.assertIn(session.ADDENDUM_HEADING, text)
        self.assertIn("shipped three fleet fixes after the close", text)

    def test_frontmatter_records_the_count_and_the_stamp(self):
        p = self.write_journal()
        self.run_addendum(session_id=CLOSED_FM["session-id"])
        fm, _ = session.parse_journal(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["addenda"], "1")
        self.assertTrue(fm["last-addendum"])

    def test_a_second_addendum_appends_rather_than_replacing(self):
        p = self.write_journal()
        self.run_addendum(session_id=CLOSED_FM["session-id"], note="first thing")
        self.run_addendum(session_id=CLOSED_FM["session-id"], note="second thing")
        text = p.read_text(encoding="utf-8")
        fm, _ = session.parse_journal(text)
        self.assertEqual(fm["addenda"], "2")
        self.assertIn("first thing", text)
        self.assertIn("second thing", text)
        self.assertEqual(text.count(session.ADDENDUM_HEADING), 2)

    def test_an_empty_note_is_refused(self):
        """An empty addendum records that something happened and nothing about what."""
        self.write_journal()
        for bad in ("", "   ", "\n"):
            with self.assertRaises(SystemExit) as cm:
                self.run_addendum(session_id=CLOSED_FM["session-id"], note=bad)
            self.assertIn("--note", str(cm.exception))

    def test_a_corrupt_count_does_not_lose_the_addendum(self):
        p = self.write_journal(addenda="not-a-number")
        self.run_addendum(session_id=CLOSED_FM["session-id"])
        fm, _ = session.parse_journal(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["addenda"], "1")

    def test_dry_run_writes_nothing(self):
        p = self.write_journal()
        before = p.read_text(encoding="utf-8")
        out, rc = self.run_addendum(session_id=CLOSED_FM["session-id"], dry_run=True)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        self.assertIn("dry-run", out)
        rc.assert_not_called()

    def test_a_missing_journal_says_ids_are_not_ordinals(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_addendum(session_id="127")
        self.assertIn("ordinals are not ids", str(cm.exception))


class DerivedViewsTest(AddendumBase):
    """The half a hand-written addendum never did, and the item's actual complaint:
    post-close work was invisible to every view compiled FROM the journal."""

    def test_the_views_are_recompiled(self):
        self.write_journal()
        _out, rc = self.run_addendum(session_id=CLOSED_FM["session-id"])
        rc.assert_called_once()

    def test_a_lane_says_why_it_skipped_the_recompile(self):
        """The handoff is trunk-only (ADR-0056), so a lane must not write it — but a
        silent skip is indistinguishable from a compile that failed."""
        self.write_journal()
        ns = argparse.Namespace(note="x", session_id=CLOSED_FM["session-id"],
                                dry_run=False)
        buf = io.StringIO()
        with mock.patch.object(session, "_on_worktree_lane", return_value=True), \
             mock.patch.object(session, "run_compile") as rc:
            with redirect_stdout(buf):
                session.cmd_addendum(ns)
        rc.assert_not_called()
        self.assertIn("NOT recompiled", buf.getvalue())
        self.assertIn("trunk-only", buf.getvalue())

    def test_a_failed_recompile_still_reports_the_addendum_as_written(self):
        """The append already happened. Reporting the whole verb as failed would send a
        reader looking for work that is on disk."""
        p = self.write_journal()
        ns = argparse.Namespace(note="x", session_id=CLOSED_FM["session-id"],
                                dry_run=False)
        buf = io.StringIO()
        with mock.patch.object(session, "_on_worktree_lane", return_value=False), \
             mock.patch.object(session, "run_compile", side_effect=OSError("boom")):
            with redirect_stdout(buf):
                session.cmd_addendum(ns)
        self.assertIn("addendum IS written", buf.getvalue())
        self.assertIn(session.ADDENDUM_HEADING, p.read_text(encoding="utf-8"))

    def test_the_session_index_row_flags_the_addendum(self):
        """The index is what a cold session scans to orient. Prose eight screens down is
        not where "this session kept working" belongs."""
        fm = {**CLOSED_FM, "addenda": "1"}
        row = session._journal_row(fm, 127, session.CFG["tz"])
        self.assertIn("(+1 addendum)", row)

    def test_two_addenda_pluralise(self):
        fm = {**CLOSED_FM, "addenda": "2"}
        self.assertIn("(+2 addenda)", session._journal_row(fm, 127, session.CFG["tz"]))

    def test_a_session_with_no_addenda_gets_no_marker(self):
        """The marker must stay rare enough to mean something."""
        row = session._journal_row(CLOSED_FM, 127, session.CFG["tz"])
        self.assertNotIn("addend", row)

    def test_a_corrupt_addenda_field_does_not_break_the_index(self):
        fm = {**CLOSED_FM, "addenda": "banana"}
        row = session._journal_row(fm, 127, session.CFG["tz"])
        self.assertNotIn("addend", row)


class ResolutionTest(AddendumBase):
    """The default target is THIS session's journal, resolved across open AND closed —
    `find_open_journal` is the wrong tool by construction, since the journal an addendum
    targets is always closed."""

    def test_a_closed_journal_is_found_by_its_claude_session_id(self):
        p = self.write_journal()
        self.assertEqual(session._journal_for_claude_session("csid-me"), p)

    def test_an_unknown_claude_session_id_resolves_to_nothing(self):
        self.write_journal()
        self.assertIsNone(session._journal_for_claude_session("csid-someone-else"))

    def test_no_claude_session_id_resolves_to_nothing(self):
        self.write_journal()
        self.assertIsNone(session._journal_for_claude_session(""))


if __name__ == "__main__":
    unittest.main()
