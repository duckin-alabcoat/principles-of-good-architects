"""WI-0332 — a journal whose frontmatter is gone must not close, and must not vanish
quietly from the compiled views.

A whole-file write over a staged journal (an agent "rewriting" its narrative instead of
appending to it) destroys the frontmatter block, and with it `session-id` and the
`base-commit` ADR-0055 records as causality-as-fact. Every layer then failed open at
once: `parse_journal` returns `({}, text)` rather than raising, the compilers filter on
`fm.get("session-id")` and DROP, `finalize_journal` returned the text unchanged, and
`cmd_end` wrote it back byte-identical, printed its end banner and exited 0. The session
was gone from every view with nothing anywhere reporting a problem.

The tell that this was an oversight rather than a decision: the BY-HAND surgery verbs
(`journal-title`, `addendum`, `resolve-orphan`) all refuse on unparseable frontmatter.
Only the AUTOMATIC close-and-compile path — the one nobody is watching — passed.

THESE TESTS WERE WRITTEN AND OBSERVED PASSING SILENTLY BEFORE THE GUARD EXISTED: the
silence is the defect, so it had to be witnessed rather than assumed. Run 1 with the
guard reverted reports a clean `end`, an unchanged file and a handoff that never
mentions the journal it dropped.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import io
import pathlib
import sys
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from coord_fixture import neutralize_dispatch_env  # noqa: E402
from test_session_branch import BranchGateBase  # noqa: E402


GOOD_SID = "20260719T2000Z-runner-0001"
BAD_SID = "20260719T2100Z-runner-0002"

#: What a whole-file write leaves behind: the narrative, and nothing else.
CLOBBERED = "# Session notes\n\nRewrote the whole file. The frontmatter is gone.\n"

#: The subtler half of the same defect — a `---` block that parses but carries no
#: `session-id`. Every compiled view filters on exactly that key, so this disappears by
#: the identical route while *looking* like a well-formed journal.
NO_SID = "---\ntitle: something\nstarted: 2026-07-19T21:00:00+00:00\nended:\n---\n\nbody\n"


class JournalFrontmatterGuardTest(BranchGateBase):
    def setUp(self):
        # WI-0249, and stated in THIS module rather than inherited silently from the base:
        # these tests drive `cmd_end`, whose lane path can end a lane's runtime, and the
        # land gate runs this suite from inside a dispatched lane. The base fixture clears
        # the same variables; `test_dispatch_fixture_guard` reads each module on its own
        # terms, which is the right call — inheritance is exactly how a fixture loses a
        # guard without anyone noticing. Idempotent.
        neutralize_dispatch_env(self)
        super().setUp()

    def _end_args(self, sid, **over):
        base = dict(session_id=sid, title=None, commit=None, push=False, focus=None,
                    blocked=None, no_merge=True, dry_run=False,
                    confirm="test close confirmation")
        base.update(over)
        return argparse.Namespace(**base)

    def _write_raw(self, sid, text):
        p = session.JOURNAL_DIR / f"{sid}.md"
        p.write_text(text, encoding="utf-8")
        return p

    # --- the close refuses, with a named reason ------------------------------

    def test_close_refuses_a_journal_whose_frontmatter_was_destroyed(self):
        p = self._write_raw(BAD_SID, CLOBBERED)
        before = p.read_bytes()
        with self.assertRaises(SystemExit) as cm:
            session.cmd_end(self._end_args(BAD_SID))
        msg = str(cm.exception)
        self.assertIn(p.name, msg, "the refusal names the file")
        self.assertIn("frontmatter", msg, "the refusal names the reason")
        self.assertEqual(before, p.read_bytes(),
                         "a refused close writes nothing — the narrative survives")

    def test_close_refuses_frontmatter_that_parses_but_has_no_session_id(self):
        """`session-id` is the key every compiled view filters on, so its absence is the
        same disappearance wearing a well-formed `---` block."""
        p = self._write_raw(BAD_SID, NO_SID)
        with self.assertRaises(SystemExit) as cm:
            session.cmd_end(self._end_args(BAD_SID))
        self.assertIn("session-id", str(cm.exception))

    def test_finalize_journal_refuses_instead_of_returning_the_text_unchanged(self):
        """The choke point, not the instance: every automatic close reaches bytes through
        here, so the refusal lives here and covers the close verbs it has not met yet
        ([`retire-the-class-not-the-instance`])."""
        with self.assertRaises(SystemExit) as cm:
            session.finalize_journal(CLOBBERED, "2026-07-19T21:00:00+00:00", "1h")
        self.assertIn("frontmatter", str(cm.exception))

    def test_a_well_formed_journal_still_closes(self):
        """The guard must refuse the defect and nothing else."""
        self._write_journal(GOOD_SID)
        session.cmd_end(self._end_args(GOOD_SID, commit=None, no_merge=True))
        fm, _ = session.parse_journal(
            (session.JOURNAL_DIR / f"{GOOD_SID}.md").read_text(encoding="utf-8"))
        self.assertTrue(fm.get("ended"), "a good journal closes as before")

    # --- the compile stays fail-open, but says what it dropped ---------------

    def test_the_compile_names_every_journal_it_dropped(self):
        """Fail-OPEN and loud, deliberately: one unreadable journal must not brick the
        views for every other session on the machine, but "dropped" must stop rendering
        identically to "there was nothing there"
        ([`declare-what-a-check-assumes`])."""
        self._write_journal(GOOD_SID)
        self._write_raw(BAD_SID, CLOBBERED)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            session.run_compile(session.CFG["tz"], force=True)
        handoff = session.CFG["handoff"].read_text(encoding="utf-8")
        self.assertEqual(session.compiled_ordinal(GOOD_SID), 1,
                         "the readable journal still compiles")
        self.assertIn("Session 1", handoff, "and still renders into the handoff")
        self.assertIn(f"{BAD_SID}.md", err.getvalue(),
                      "the compile names the journal it could not read")
        self.assertIn("NO compiled view", err.getvalue(),
                      "and says what being dropped means")

    def test_the_compile_keeps_the_roadmap_report_off_stdout(self):
        """WI-0455: `start` runs the compile inside the SessionStart hook, whose stdout
        must be one JSON object. The basic acceptance drill caught a fresh project's first
        start printing the ROADMAP render's report ahead of that JSON."""
        self._write_journal(GOOD_SID)
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(
                session, "cmd_wi_render",
                lambda _a: print("wi-render: 0 section(s) already current in ROADMAP.md.")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            session.run_compile(session.CFG["tz"], force=True)
        self.assertEqual(out.getvalue(), "", "nothing may reach the hook's stdout")
        self.assertIn("wi-render:", err.getvalue(), "the report is kept, on stderr")

    def test_unreadable_journals_reports_the_reason_per_file(self):
        self._write_journal(GOOD_SID)
        self._write_raw(BAD_SID, CLOBBERED)
        found = {p.name: why for p, why in session.unreadable_journals()}
        self.assertEqual(list(found), [f"{BAD_SID}.md"],
                         "exactly the unreadable one, and no false positive")
        self.assertIn("frontmatter", found[f"{BAD_SID}.md"])


if __name__ == "__main__":
    unittest.main()
