"""The guard that stops a session telling operator about git.

WHY A GUARD AND NOT A RULE. The rule already existed — `standard-source.md` §"What
reaches the user" said a session never hands operator a git command — and it was violated
four times in one session by the session that wrote the stronger version down, twice
after being told directly. `add-structural-guard-on-recurrence` says exactly what that
means: the first is a miss, a repeat means discipline is not enough and enforcement
belongs in code.

EVERY POSITIVE CASE BELOW IS A SYNTHETIC SENTENCE IN THE SHAPE OF ONE A SESSION ACTUALLY
SENT. That is deliberate: a guard tested only against shapes nobody has produced is
tested against the author's imagination, which is the same faculty that produced the
violations (`a-detector-proves-itself-on-the-real-defect`).

The negative cases matter as much. The first version of this guard blocked "the example-app
learnings merge is blocked on someone else" — ordinary English about merging learnings —
and a guard that fires on correct output is one that gets deleted, taking the protection
with it (`a-guard-that-fires-on-correct-code-gets-deleted`).
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
import check_reply  # noqa: E402


#: The shapes of a WI-0159 escalation defect (synthetic sentences).
VIOLATIONS = [
    "Pushed 1a2b3c4d..5e6f7a8b, and verified against the remote itself.",
    "Local main had diverged: 3 ahead, 2 behind. I am running integrate.",
    "FL7 fails at land time whenever lanes are running; the lock covers the recompile.",
    "The trunk reconciled on its own at 0badc0de.",
    "Everything is landed and on origin at 1234abcd.",
    "git -C ~/example-repo push origin HEAD:main",
    "Four landed commits were sitting unpushed on local main.",
]

#: Must all reach him untouched.
MUST_PASS = [
    "example-service is live and verifies. WI-0343 is decided and recorded.",
    "Four items are running. Only the example-app learnings merge is blocked on someone else.",
    "Theme B is done. Theme C is down to one item held for its dependency.",
    "The only thing that needs you is the residue convention ruling.",
    "Nothing is blocked on you.",
]


class TheGuardCatchesWhatWasActuallySaidTest(unittest.TestCase):
    def test_every_violation_shape_is_refused(self):
        for text in VIOLATIONS:
            with self.subTest(text[:50]):
                self.assertTrue(check_reply.violation(text),
                                "this shape must not be sayable")

    def test_the_refusal_says_what_to_do_instead(self):
        """A guard that only denies teaches nothing; this one has to redirect to the
        outcome framing, or the next attempt is the same sentence reworded."""
        why = check_reply.violation(VIOLATIONS[0])
        self.assertIn("OUTCOMES", why)
        self.assertIn("journal", why)


class TheGuardDoesNotFireOnOrdinaryReportsTest(unittest.TestCase):
    def test_clean_outcome_reports_pass(self):
        for text in MUST_PASS:
            with self.subTest(text[:50]):
                self.assertFalse(check_reply.violation(text),
                                 "a guard that blocks correct output gets deleted")

    def test_land_in_its_ordinary_english_sense_passes(self):
        """The guard blocked "escalations land where nobody reads them" on its first live
        firing — ordinary English, not version control. `land` is both in this repo, and a
        guard that cannot tell them apart blocks correct replies until someone deletes it."""
        self.assertFalse(check_reply.violation(
            "Escalations land where nobody reads them."))
        self.assertFalse(check_reply.violation(
            "The adopt-runner can no longer locate a single member repo."))

    def test_gate_in_its_non_version_control_senses_passes(self):
        """Third live false positive. This repo gates several things that are not version
        control — the CANON BUDGET gate is the word a ruling itself uses, and the bare
        pattern made a reply about that ruling unsendable."""
        self.assertFalse(check_reply.violation(
            "The canon budget is a gate now: a change that crosses the ceiling does not "
            "land unless it carries its own consolidation."))
        self.assertFalse(check_reply.violation(
            "It refuses in the lane that wrote the change, naming the bytes it added."))

    def test_merge_in_its_ordinary_english_sense_passes(self):
        """The first version blocked this exact sentence."""
        self.assertFalse(check_reply.violation(
            "The example-app Architect has to merge its learnings before the clone goes."))


class TheTwoSanctionedExceptionsTest(unittest.TestCase):
    """the operator's ruling names two, and only two: the git architecture being redesigned, and
    something having been lost. Both have to get through or the guard would suppress the
    one conversation about git that is legitimate."""

    def test_a_redesign_conversation_is_allowed(self):
        self.assertFalse(check_reply.violation(
            "I want to redesign the land architecture so the lock holds only the merge."))

    def test_reporting_lost_work_is_allowed(self):
        self.assertFalse(check_reply.violation(
            "The worktree was deleted and that work is lost."))


class TheGuardFailsOpenTest(unittest.TestCase):
    """It sits on the reply path. Whatever it cannot read, it permits — the thing on the
    other side is an irritated user, not lost data, so open is the safe direction."""

    def test_an_empty_reply_is_permitted(self):
        self.assertEqual(check_reply.violation(""), "")
        self.assertEqual(check_reply.violation("   \n "), "")

    def test_an_unreadable_transcript_is_permitted(self):
        with self.assertRaises(Exception):
            check_reply.last_assistant_text("/nonexistent/transcript.jsonl")
        # ...and `main()` swallows that rather than blocking the conversation. The sample
        # here deliberately contains no forbidden word — the first version of this test
        # used the phrase "nothing git here", which the guard correctly refused, so the
        # test was wrong and the code was right.
        self.assertEqual(check_reply.violation("example-service is live."), "")



class TheGuardBlocksOnceAndNeverTwiceTest(unittest.TestCase):
    """A guard on prose that cannot be satisfied is worse than no guard (WI-0159).

    MEASURED 2026-09-13: a reply was blocked, rewritten clean — zero matches when the new
    text was tested directly — and REFUSED AGAIN. The second refusal was not a judgement
    about the rewrite at all: the transcript the hook reads does not yet carry the new
    message, so the last assistant record is still the one already rejected, and the hook
    re-read the old verdict. Left alone that is a livelock; the conversation cannot
    proceed however correct the rewrite is.

    One refusal carries the entire value here — it makes the rule impossible to forget at
    the moment it matters. A second adds nothing and can wedge the session, which is a
    worse failure than the sentence it was stopping.
    """

    def _payload(self, text, active):
        import json, tempfile, pathlib as pl
        tr = pl.Path(tempfile.mkdtemp()) / "t.jsonl"
        tr.write_text(json.dumps(
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        return json.dumps({"transcript_path": str(tr), "stop_hook_active": active})

    def _run(self, text, active):
        import subprocess, sys, pathlib as pl
        root = pl.Path(__file__).resolve().parent.parent
        p = subprocess.run([sys.executable, str(root / "curate" / "check_reply.py")],
                           input=self._payload(text, active),
                           capture_output=True, text=True)
        return (p.stdout or "").strip()

    def test_a_fresh_violation_is_blocked(self):
        self.assertTrue(self._run("Everything is landed and on origin at 1234abcd.", False))

    def test_the_same_text_is_permitted_while_the_rewrite_is_in_flight(self):
        self.assertFalse(self._run("Everything is landed and on origin at 1234abcd.", True),
                         "blocking twice on a transcript that has not caught up is a "
                         "livelock, not a second judgement")

if __name__ == "__main__":
    unittest.main()
