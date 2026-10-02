"""WI-0401 — a closed session can always stop.

TWO HALVES OF ONE FACT, and the tests split along the same seam:

  1. **`session.py end` prints a GOAL DISPOSITION block** — every clause of the active
     goal with what became of it, and the instruction QUOTED for any clause an order took
     away or consent blocked. The Stop hook's evaluator is a model reading the transcript
     for evidence; what it lacked was not judgment but an artifact stating the facts it
     cannot derive. A verdict with no evidence is a different unreadable answer, so the
     recorder REFUSES the two evidence-bearing states without it, and that refusal is
     tested as carefully as the block.

  2. **A conditional goal clause is refused where it is authored.** "Close WI-0324 only
     if the reading passes" has no readable answer once the reading is called off, and
     the session holding it cannot leave.

WHAT THESE TESTS ARE REALLY GUARDING is the second half's NEGATIVE CONTROL. A guard that
fires on correct goals gets switched off, and this one sits directly in the dispatch
path. So the permit cases below are synthetic operator goals written in the SHAPES the
recorded goals actually take, and the three refuse cases are the shapes of the WI-0324
stop trap — a clause whose truth is gated on something the operator may call off.

The same discipline applies to the splitter: the anchor is the item's own arithmetic. The
fixture goal has the incident's shape and must split into exactly the eight clauses the
incident counts when it says "six of eight clauses were met" — a fixture written to
agree with the code would prove nothing about either.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (neutralize_coord_journal, neutralize_dispatch_env,  # noqa: E402
                           neutralize_live_store)


#: A synthetic goal in the shape of the one the item was written from: eight clauses,
#: six met, one taken back by the operator mid-session and one blocked on the consent
#: `end` requires. It is the fixture for nearly everything here.
GOAL_FIXTURE = ("Read the federation inbox, fix the runner PATH, mint and dispatch the "
                "WI-9001 status emitter, cut v1.2.0 through the pipeline, land the parser "
                "fix alone, then dispatch wave 2; journal every decision in bullets; "
                "end the session.")

#: The operator's (synthetic) instruction taking the release clause away.
MOOTED_BY = ("Leave the release out of this session; it will be cut from a session "
             "opened on main after this one closes.")


class GoalClauseTest(unittest.TestCase):
    """The splitter, against the item's own count and against the shapes that break one."""

    def test_the_fixture_goal_splits_into_the_eight_clauses_the_item_counts(self):
        """THE ANCHOR. The item says "six of eight clauses were met" about a sentence of
        this shape. Any splitter that disagrees is answering a different question than the
        one the incident asked, however reasonable its own answer looks."""
        clauses = session.goal_clauses(GOAL_FIXTURE)
        self.assertEqual(len(clauses), 8, clauses)
        self.assertEqual(clauses[0], "Read the federation inbox")
        self.assertEqual(clauses[3], "cut v1.2.0 through the pipeline")
        self.assertEqual(clauses[7], "end the session.")

    def test_a_filename_or_version_mid_sentence_does_not_end_a_clause(self):
        """`session.py`, `v1.2.0` and `poga-19.` all carry a full stop, and every goal in
        the corpus names a file or a version. A sentence split on the character alone
        shreds them."""
        clauses = session.goal_clauses(
            "Run python3 session.py test under v1.2.0 and land it. Then close.")
        self.assertEqual(clauses,
                         ["Run python3 session.py test under v1.2.0 and land it.",
                          "Then close."])

    def test_a_short_list_item_rejoins_its_clause_instead_of_becoming_one(self):
        """A comma inside an enumeration is not a clause break. Measured on the first
        live use of this code: "…with met, not met, moot or blocked" produced a clause
        reading "not met", which is not an obligation anybody can dispose of.

        ASSERTED ON THE DEFECT, not on a tidy clause count. Separating a list from a
        clause is English, and this code deliberately does not attempt it beyond the
        length rule — so the promise is the narrow one it can keep: no clause is a bare
        enumeration item."""
        clauses = session.goal_clauses(
            "Print every clause with met, not met, moot or blocked, and land it.")
        self.assertNotIn("not met", clauses)
        self.assertIn("Print every clause with met, not met", clauses[0])
        self.assertEqual(clauses[-1], "and land it.")

    def test_a_short_sentence_is_still_a_clause(self):
        """The length rule applies ACROSS A COMMA ONLY. A full stop is not ambiguous
        between a clause break and a list, so "Then close." stands on its own however
        short it is — rejoining it would hide one obligation behind another."""
        clauses = session.goal_clauses("Land the fix and push it. Then close.")
        self.assertEqual(clauses, ["Land the fix and push it.", "Then close."])

    def test_punctuation_only_fragments_are_not_clauses(self):
        self.assertEqual(session.goal_clauses("Do the thing, (1), ;;, and stop."),
                         ["Do the thing", "and stop."])

    def test_an_empty_goal_has_no_clauses(self):
        self.assertEqual(session.goal_clauses(""), [])
        self.assertEqual(session.goal_clauses(None), [])


class ConditionalGoalRefusalTest(unittest.TestCase):
    """Both directions. A guard that only ever passes is indistinguishable from one that
    is switched off, and a guard that fires on correct goals gets switched off."""

    def _refused(self, text):
        return session.goal_conditional_offences(text)

    # ---- the stop-trap shapes ----------------------------------------------------

    def test_the_trap_clause_is_refused(self):
        """The trap shape: its truth is gated on a reading the operator may then call off,
        and a session holding it cannot leave."""
        offences = self._refused(
            "Run the benchmark against the sample set and record the new numbers on "
            "WI-9002 beside the old ones; close it if the benchmark passes.")
        self.assertEqual(len(offences), 1, offences)
        _index, clause, marker = offences[0]
        self.assertEqual(marker, "if")
        self.assertIn("close it if the benchmark passes", clause)

    def test_only_if_is_refused(self):
        offences = self._refused("Read the pack. Close WI-0324 only if both readings pass.")
        self.assertEqual([m for _i, _c, m in offences], ["only if"], offences)

    def test_unless_is_refused(self):
        offences = self._refused("Land the fix and close the item unless the gate is red.")
        self.assertEqual([m for _i, _c, m in offences], ["unless"], offences)

    # ---- the negative control: goal shapes that must NOT be refused --------------

    def test_a_fronted_conditional_is_permitted(self):
        """The antecedent comes first, so the false branch is
        inside the sentence and the clause is decidable either way."""
        self.assertEqual(self._refused(
            "Resume lane poga-4 and merge its checkpoint; if the check refuses, leave it "
            "on the branch and write down why."), [])

    def test_a_postposed_conditional_with_an_alternative_is_permitted(self):
        """`otherwise` states what happens when it is false, and
        it lands in the NEXT clause because the comma before it is a clause break."""
        self.assertEqual(self._refused(
            "Fold WI-9003 into this lane if the parser lives in lanes.py, otherwise give "
            "WI-9003 its own lane and drop lane 2."), [])

    def test_rather_than_is_an_alternative_too(self):
        """The same shape with `rather than`: "trim down rather than over"."""
        self.assertEqual(self._refused(
            "Keep under the size ceiling; if the queue would exceed it, trim down "
            "rather than over."), [])

    def test_if_meaning_whether_is_permitted(self):
        """THE NEGATIVE CONTROL THE ITEM NAMES BY NAME: "a goal containing the word 'if'
        in a non-conditional sense must not be refused". Substituting "whether" is the
        test a person applies; a verb that takes a whether-complement is the mechanical
        stand-in for it."""
        for text in ("Read the sweep and check if the gate is red.",
                     "Run the suite and report if anything is still failing.",
                     "Determine if the record is stale and write down the answer."):
            with self.subTest(text=text):
                self.assertEqual(self._refused(text), [])

    def test_a_fronted_conditional_inside_a_quotation_is_permitted(self):
        """The guard's single false positive when it was measured across 1,474 markdown
        files, in the shape it actually occurred: the clause OPENS with the quote, and an
        opening `*"` was all that stood between a fronted conditional and its exemption.

        The limit this does not claim to clear: a conditional quoted MID-clause ("do X:
        'if Y, then Z'") still reads as postposed, because telling a quotation from a
        condition needs more than punctuation. That shape is not in the corpus, and the
        surface it would reach — a brief's `Prompted by:` line — is deliberately outside
        the guard's pattern for the same reason."""
        self.assertEqual(self._refused(
            '*"if the report works, make it list the finished items '
            'as well."*'), [])

    def test_if_as_a_substring_is_not_a_conditional(self):
        """Word boundaries, not a grep. This is the whole difference between matching a
        clause shape and matching text."""
        self.assertEqual(self._refused(
            "Notify the board, read the diff, and land the modified file."), [])

    def test_the_refusal_says_where_the_clause_belongs(self):
        """A refusal that names no fix is a diagnosis, and the author is left guessing."""
        message = session.goal_conditional_refusal(
            "Close WI-0324 only if the reading passes.", "dispatch")
        self.assertTrue(message.startswith("dispatch: refused"), message)
        self.assertIn("only if", message)
        self.assertIn("PROMPT BODY", message.upper())

    def test_a_clean_goal_produces_no_refusal_at_all(self):
        self.assertEqual(session.goal_conditional_refusal(GOAL_FIXTURE, "dispatch"), "")


class GoalDispositionRecordTest(unittest.TestCase):
    """The states, and the evidence the two claim-shaped ones require."""

    def test_moot_without_evidence_is_refused(self):
        """THE HINGE OF THE WHOLE ITEM. "Moot" is how a clause stops counting against a
        session, so it is exactly the state a session has an interest in claiming — and a
        bare "moot" is no more readable than the "No." it replaces."""
        refusal = session.goal_disposition_refused("moot", "")
        self.assertIn("needs the words that did it", refusal)
        self.assertIn("--because", refusal)

    def test_blocked_without_evidence_is_refused(self):
        self.assertIn("needs the words that did it",
                      session.goal_disposition_refused("blocked", "   "))

    def test_moot_with_evidence_is_accepted(self):
        self.assertEqual(session.goal_disposition_refused("moot", MOOTED_BY), "")

    def test_met_and_not_met_need_no_evidence(self):
        """They are the evaluator's own vocabulary and neither excuses a clause: a
        session claiming "met" is claiming something the transcript shows."""
        self.assertEqual(session.goal_disposition_refused("met", ""), "")
        self.assertEqual(session.goal_disposition_refused("not-met", ""), "")

    def test_an_unknown_state_is_refused_by_name(self):
        refusal = session.goal_disposition_refused("probably", "")
        self.assertIn("probably", refusal)
        self.assertIn("met", refusal)


class GoalDispositionBlockTest(unittest.TestCase):
    """The artifact the evaluator reads."""

    def _block(self, records=None):
        return "\n".join(session.goal_disposition_block(GOAL_FIXTURE, records or {},
                                                       source="transcript"))

    def _keyed(self, mapping):
        """`{clause-number: (state, because)}` -> the by-clause record the block takes."""
        clauses = session.goal_clauses(GOAL_FIXTURE)
        return {session._goal_key(clauses[n - 1]): {"state": s, "because": b}
                for n, (s, b) in mapping.items()}

    def test_the_fixture_case_renders_a_readable_verdict(self):
        """THE ITEM'S DEMONSTRATION: a goal whose clause was made moot by an instruction
        not to run the thing it tested produces a readable verdict rather than "No.". Six
        met, one moot with the instruction under it, one blocked on consent."""
        out = self._block(self._keyed({
            1: ("met", ""), 2: ("met", ""), 3: ("met", ""),
            4: ("moot", MOOTED_BY),
            5: ("met", ""), 6: ("met", ""), 7: ("met", ""),
            8: ("blocked", "end refuses without --confirm and no human is attached."),
        }))
        self.assertIn("GOAL DISPOSITION", out)
        self.assertIn("[MOOT BY INSTRUCTION] cut v1.2.0 through the pipeline", out)
        self.assertIn("[BLOCKED] end the session.", out)
        self.assertEqual(out.count("[MET]"), 6, out)
        self.assertIn("6 met", out)

    def test_a_moot_clause_carries_the_instruction_verbatim(self):
        """A verdict without its evidence is a different unreadable answer, not a fix —
        so the words that took the clause away are IN the block, quoted, not summarised
        and not merely referenced."""
        out = self._block(self._keyed({4: ("moot", MOOTED_BY)}))
        self.assertIn(MOOTED_BY, out)
        self.assertIn("because:", out)

    def test_an_undisposed_clause_reads_NOT_RECORDED_never_NOT_MET(self):
        """Silence is not failure. Reading it as failure is the same fabrication as
        reading it as success, and it tells the reader nothing about what to do."""
        out = self._block(self._keyed({1: ("met", "")}))
        self.assertIn("[NOT RECORDED]", out)
        self.assertNotIn("[NOT MET]", out)
        self.assertIn("7 not recorded", out)

    def test_a_session_with_no_goal_renders_nothing_at_all(self):
        """Silent, because nearly every session has no goal and a block announcing that
        nothing applies is noise on every close."""
        self.assertEqual(session.goal_disposition_block("", {}), [])
        self.assertEqual(session.goal_disposition_block("   ", {}), [])

    def test_a_disposition_does_not_migrate_to_a_different_clause(self):
        """Dispositions are keyed on the CLAUSE, not on its number. A session that
        recorded "clause 4 is moot" and then received a second goal would otherwise carry
        that verdict onto whatever now sits in position 4 — a wrong answer produced
        silently, which is worse than no answer."""
        records = self._keyed({4: ("moot", MOOTED_BY)})
        other = "Do something else entirely, then stop, and write it down."
        out = "\n".join(session.goal_disposition_block(other, records))
        self.assertNotIn("MOOT", out)
        self.assertNotIn(MOOTED_BY, out)


class GoalSourceTest(unittest.TestCase):
    """Where `end` gets the goal from when nobody recorded one."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_dispatch_env(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.csid = "csid-goal-test"
        self.proj = self.home / ".claude" / "projects" / "-a-project"
        self.proj.mkdir(parents=True)
        self._home = mock.patch.object(session.Path, "home", staticmethod(lambda: self.home))
        self._home.start()
        self._csid = mock.patch.object(session, "_claude_session_id",
                                       return_value=self.csid)
        self._csid.start()

    def tearDown(self):
        self._csid.stop()
        self._home.stop()
        self.tmp.cleanup()

    def _transcript(self, rows):
        path = self.proj / f"{self.csid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def _typed(self, text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def _stated(self, condition):
        return {"type": "attachment",
                "attachment": {"type": "goal_status", "met": False,
                               "condition": condition}}

    def test_a_typed_goal_is_read_out_of_the_transcript(self):
        """On this runtime a goal is a `/goal` prompt and lives nowhere else — there is
        no store to read, and asking the operator to re-type it into one would make the
        automatic case an errand."""
        self._transcript([self._typed("/goal Land the item and close.")])
        self.assertEqual(session._goal_from_transcript(),
                         ("Land the item and close.", "transcript"))

    def test_the_runtimes_own_record_is_read_too(self):
        self._transcript([self._stated("Land the item and close.")])
        self.assertEqual(session._goal_from_transcript()[0], "Land the item and close.")

    def test_the_last_record_wins(self):
        """A session that sets a second goal has replaced the first, and the Stop hook is
        evaluating the new one."""
        self._transcript([self._typed("/goal First goal."),
                          self._stated("First goal."),
                          self._typed("/goal Second goal."),
                          self._stated("Second goal.")])
        self.assertEqual(session._goal_from_transcript()[0], "Second goal.")

    def test_a_cleared_goal_is_no_goal(self):
        """`/goal clear` is the runtime's own word for it. Reporting the last goal after
        it would be a block about an obligation nobody is held to."""
        self._transcript([self._typed("/goal Land the item and close."),
                          self._typed("/goal clear")])
        self.assertEqual(session._goal_from_transcript(), ("", ""))

    def test_a_prompt_that_merely_mentions_goal_is_not_one(self):
        self._transcript([self._typed("what does /goal do, and is my goal set?")])
        self.assertEqual(session._goal_from_transcript(), ("", ""))

    def test_no_transcript_is_a_clean_empty_answer(self):
        """Every other runtime reaches this path. It answers "no goal", never raises, and
        such a session uses `session.py goal-set` instead."""
        self.assertEqual(session._goal_from_transcript(), ("", ""))


class GoalDispatchRefusalTest(unittest.TestCase):
    """The dispatcher refuses a conditional goal BEFORE it draws anything."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)

    def _args(self, goal):
        return argparse.Namespace(items="1-3", go=False, cap=None, run=None,
                                  max_turns=None, subagent="", runtime="", force=False,
                                  goal=goal)

    def test_a_conditional_goal_refuses_before_a_snapshot_is_even_read(self):
        """The position is the point: refused above the snapshot read, the range
        resolution and the dispatch id, so a refusal costs the author one rewrite and
        nothing else. `_dispatch_snapshot` is asserted UNCALLED — that is what proves the
        refusal is hoisted rather than merely present."""
        buf = io.StringIO()
        with mock.patch.object(session, "_dispatch_snapshot") as snap:
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as exit_:
                session.cmd_dispatch(self._args(
                    "Read the pack and close WI-0324 only if the reading passes."))
        self.assertEqual(exit_.exception.code, 2)
        snap.assert_not_called()
        out = buf.getvalue()
        self.assertIn("dispatch: refused", out)
        self.assertIn("only if", out)
        self.assertIn("PROMPT BODY", out.upper())

    def test_a_goal_with_if_meaning_whether_is_not_refused_here(self):
        """The negative control, on the surface that matters most: this guard sits
        directly in the dispatch path, and one that fires on correct goals gets turned
        off. It must get PAST the goal check — where it stops afterwards is not this
        test's business."""
        with mock.patch.object(session, "_dispatch_snapshot",
                               return_value=(None, None)) as snap:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.suppress(SystemExit):
                    session.cmd_dispatch(self._args(
                        "Land the fix and check if the gate is red."))
        snap.assert_called_once()

    def test_a_dispatch_with_no_goal_is_unchanged(self):
        with mock.patch.object(session, "_dispatch_snapshot",
                               return_value=(None, None)) as snap:
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.suppress(SystemExit):
                    session.cmd_dispatch(self._args(""))
        snap.assert_called_once()


class GoalBriefTest(unittest.TestCase):
    """The goal rides the brief, and its absence leaves the brief exactly as it was."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_dispatch_env(self)

    def test_a_brief_with_no_goal_is_byte_identical_to_the_one_before_this_shipped(self):
        """The property `_dispatch_rebrief_tmux` and the brief-state check both rest on:
        two routes render the same string, so they cannot drift."""
        with mock.patch.object(session, "_dispatch_item_title", return_value="T"):
            plain = session._dispatch_prompt("WI-1", "D-1", "S-1")
            with_empty = session._dispatch_prompt("WI-1", "D-1", "S-1", goal="")
            with_none = session._dispatch_prompt("WI-1", "D-1", "S-1", goal=None)
        self.assertEqual(plain, with_empty)
        self.assertEqual(plain, with_none)
        self.assertNotIn("YOUR GOAL", plain)

    def test_a_goal_reaches_the_lane_with_the_verb_that_disposes_of_it(self):
        """A brief that states a goal and not how to answer for it would hand the lane
        the same unanswerable sentence by a different road."""
        with mock.patch.object(session, "_dispatch_item_title", return_value="T"):
            brief = session._dispatch_prompt("WI-1", "D-1", "S-1",
                                             goal="Land the fix and close.")
        self.assertIn("YOUR GOAL", brief)
        self.assertIn("Land the fix and close.", brief)
        self.assertIn("goal-dispose", brief)


class CmdEndGoalBlockTest(unittest.TestCase):
    """`session.py end` prints the block — the item's first acceptance clause.

    Built on the same shape as `CmdEndTest` in `tests/test_session.py` rather than
    imported from it: this suite pins the goal source, which that fixture knows nothing
    about, and a base class reaching into a sibling's internals to do so would couple two
    files that are about different things."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        # `cmd_end` ends in `run_compile`, which renders ROADMAP.md at `ROOT` and COMMITS
        # it there. Without this, `ROOT` is the checkout the suite runs in: the render read
        # this class's empty journal dir, wiped the real roadmap's Recently-shipped region,
        # and committed it — onto the nightly derive's lane branch, three nights running
        # (2026-09-22..24, reproduced 2026-09-24 on this test). The fixture repoints `ROOT`
        # at a tmpdir; the seeded roadmap below gives the render somewhere real to write,
        # so the close path is exercised rather than skipped on a missing file.
        neutralize_live_store(self)
        self.roadmap = session._roadmap_path()
        self.roadmap.write_text(
            "# Roadmap\n\n## Recently shipped\n\n" + session.WI_GEN_BEGIN
            + "\n\nstale\n\n" + session.WI_GEN_END + "\n", encoding="utf-8")
        self._cfg = session.CFG
        session.CFG = dict(session.CFG or {})
        self.tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self.tmp.name)
        self.handoff = tmp / "session-handoff.md"
        self.status = tmp / "STATUS.md"
        self.status.write_text("---\nversion: 0.0.0\nlast_active: 2000-01-01\n---\n",
                               encoding="utf-8")
        role_doc = tmp / "test-arch.md"
        role_doc.write_text("# Role\n\n**Version:** 1.0.0\n", encoding="utf-8")
        session.CFG["handoff"] = self.handoff
        session.CFG["status"] = self.status
        session.CFG["role_doc"] = role_doc
        self.jdir = tmp / "journal"
        self._patches = [
            mock.patch.object(session, "JOURNAL_DIR", self.jdir),
            mock.patch.object(session, "ARCHIVE", tmp / "pre-journal-archive.md"),
            mock.patch.object(session, "_on_session_branch", return_value=False),
            mock.patch.object(session, "_on_worktree_lane", return_value=False),
        ]
        for p in self._patches:
            p.start()
        self.sid = "20260919T1200Z-devbox-aaba"
        session.write_start_journal(self.sid, {
            "session-id": self.sid, "ordinal": 1, "title": "(in progress)",
            "machine": "devbox", "runtime": "claude-code", "role-doc-version": "v1.0.0",
            "base-commit": "abc", "started": "2026-09-19T12:00:00+00:00", "ended": "",
            "claude-session-id": "csid-goal"})

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()
        session.CFG = self._cfg

    def _args(self, **over):
        d = dict(title="did things", session_id=self.sid, commit=None, push=False,
                 dry_run=False, focus=None, blocked=None, no_merge=False,
                 resolve="generated", no_renumber=True,
                 confirm="test close confirmation")
        d.update(over)
        return argparse.Namespace(**d)

    def _with_goal(self, text, records):
        """Pin the goal source and the disposition record for one close."""
        return (mock.patch.object(session, "active_goal",
                                  return_value=(text, "transcript")),
                mock.patch.object(session, "goal_record_read",
                                  return_value={"goal": text, "source": "transcript",
                                                "dispositions": records}))

    def _close(self, **over):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_end(self._args(**over))
        return buf.getvalue()

    def test_the_close_renders_the_roadmap_into_the_fixture_never_the_real_checkout(self):
        """The pin for the fixture line above. Both halves, because either alone passes
        for the wrong reason: a render that never ran leaves the real checkout untouched
        too, and a render into the tmpdir says nothing if the real one ALSO moved."""
        real = pathlib.Path(__file__).resolve().parent.parent
        self.assertNotEqual(session.ROOT.resolve(), real)
        head = lambda: subprocess.run(["git", "rev-parse", "HEAD"], cwd=real,  # noqa: E731
                                      capture_output=True, text=True).stdout.strip()
        before = head()
        self._close()
        self.assertNotIn("\nstale\n", self.roadmap.read_text(encoding="utf-8"),
                         "the render did not run into the fixture's roadmap")
        self.assertEqual(head(), before, "the close committed into the real checkout")

    def test_end_prints_the_block_with_the_instruction_that_mooted_a_clause(self):
        """THE ITEM'S FIRST ACCEPTANCE CLAUSE, on the fixture case. The
        evaluator's own system prompt asks it to quote specific text from the transcript
        and to answer "insufficient evidence in transcript" when it cannot — so the words
        that took the clause away have to BE in the transcript, which is what this print
        puts there."""
        clauses = session.goal_clauses(GOAL_FIXTURE)
        records = [{"clause": clauses[3], "state": "moot", "because": MOOTED_BY},
                   {"clause": clauses[7], "state": "blocked",
                    "because": "end refuses without --confirm; nobody is attached."}]
        goal, record = self._with_goal(GOAL_FIXTURE, records)
        with goal, record:
            out = self._close()
        self.assertIn("GOAL DISPOSITION", out)
        self.assertIn("[MOOT BY INSTRUCTION] cut v1.2.0 through the pipeline", out)
        self.assertIn(MOOTED_BY, out)
        self.assertIn("[BLOCKED] end the session.", out)

    def test_the_block_is_printed_above_the_end_banner(self):
        """The banner is the close receipt; the disposition is why the close is
        legitimate. A reader scanning up from the receipt finds it immediately."""
        goal, record = self._with_goal(GOAL_FIXTURE, [])
        with goal, record:
            out = self._close()
        # ON THE BANNER ITSELF, not on the word "end" — which appears inside the block,
        # in the goal's own last clause. An ordering assertion whose landmark sits in
        # the thing being ordered passes whatever the order is.
        banner = f"{session.BANNER_RULE} Session"
        self.assertIn(banner, out)
        self.assertLess(out.index("GOAL DISPOSITION"), out.index(banner),
                        "the block must precede the close banner")

    def test_a_session_with_no_goal_prints_no_block(self):
        """Silent, because nearly every session has no goal."""
        with mock.patch.object(session, "active_goal", return_value=("", "")):
            out = self._close()
        self.assertNotIn("GOAL DISPOSITION", out)

    def test_a_dry_run_previews_the_block(self):
        """A session checking whether it can close is exactly the one that needs to see
        which clause it has not accounted for, and seeing it costs nothing."""
        goal, record = self._with_goal(GOAL_FIXTURE, [])
        with goal, record:
            out = self._close(dry_run=True)
        self.assertIn("GOAL DISPOSITION", out)
        self.assertIn("dry-run", out)

    def test_the_block_is_folded_into_the_journal_as_well_as_printed(self):
        """THE PRINTED BLOCK IS NOT THE RECORD. It goes to a terminal, and a dispatched
        lane's terminal is a detached pane nobody is attached to; "why did this session
        close with that clause unmet?" is asked days later, from the trunk."""
        clauses = session.goal_clauses(GOAL_FIXTURE)
        records = [{"clause": clauses[3], "state": "moot", "because": MOOTED_BY}]
        goal, record = self._with_goal(GOAL_FIXTURE, records)
        with goal, record:
            self._close()
        body = session.journal_path(self.sid).read_text(encoding="utf-8")
        self.assertIn(session.GOAL_DISPOSITION_HEADING, body)
        self.assertIn(MOOTED_BY, body)

    def test_the_folded_section_does_not_count_as_authored_content(self):
        """`boilerplate` on the conformance surface means the ARCHITECT wrote nothing,
        and every line this section contributes was written by the harness. Counted, a
        session that recorded nothing but happened to have a goal would read as an
        authored journal — the metric asserting substance where there is none."""
        clauses = session.goal_clauses(GOAL_FIXTURE)
        goal, record = self._with_goal(
            GOAL_FIXTURE, [{"clause": clauses[0], "state": "met", "because": ""}])
        with goal, record:
            self._close()
        body = session.journal_path(self.sid).read_text(encoding="utf-8")
        self.assertNotIn(session.GOAL_DISPOSITION_HEADING,
                         session.strip_goal_disposition(body))
        self.assertIn("### What happened", session.strip_goal_disposition(body))

    def test_folding_twice_does_not_double_the_section(self):
        """`end` is not the only thing that reaches a journal twice — a close that
        refuses after the fold, a `merge --continue` and then an `end`, a re-render."""
        text = session.journal_path(self.sid).read_text(encoding="utf-8")
        goal, record = self._with_goal(GOAL_FIXTURE, [])
        with goal, record:
            once = session.fold_goal_disposition(text, self.sid)
            twice = session.fold_goal_disposition(once, self.sid)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(session.GOAL_DISPOSITION_HEADING), 1)

    def test_an_unreadable_goal_record_never_stops_a_close(self):
        """A block that could refuse a close would be the defect wearing the fix's
        clothes. This is the one function in the item that must not have an opinion."""
        with mock.patch.object(session, "active_goal", side_effect=OSError("boom")):
            out = self._close()
        self.assertNotIn("GOAL DISPOSITION", out)
        fm, _ = session.parse_journal(
            session.journal_path(self.sid).read_text(encoding="utf-8"))
        self.assertTrue(fm["ended"], "the session still closed")


class GoalBriefGuardTest(unittest.TestCase):
    """`curate/check-brief.py` — the Consultant-side authoring gate that lives here."""

    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "check_brief_goal", root / "curate" / "check-brief.py")
        cls.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.guard)

    def _offences(self, body):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "brief.md"
            p.write_text(body, encoding="utf-8")
            return [o for o in self.guard.check_file(str(p))
                    if o[2].startswith("conditional goal clause")]

    def test_a_conditional_goal_line_is_refused(self):
        offences = self._offences(
            "# Brief\n\n- **Goal:** Read the pack and close WI-0324 only if the reading "
            "passes.\n")
        self.assertEqual(len(offences), 1, offences)
        self.assertEqual(offences[0][0], 3, "the offending LINE is named")

    def test_a_whether_sense_goal_line_is_permitted(self):
        self.assertEqual(self._offences(
            "# Brief\n\n- **Goal:** Read the pack and check if the gate is red.\n"), [])

    def test_prose_that_merely_says_goal_is_not_a_goal_line(self):
        """Declaration, not mention — the discipline `WI_ACCEPTANCE_RE` applies. The word
        appears in ordinary brief prose and matching that would refuse nearly everything
        for saying it."""
        self.assertEqual(self._offences(
            "# Brief\n\nThe goal here is to land it only if the gate is green.\n"), [])

    def test_a_prompted_by_line_quoting_the_operator_is_not_refused(self):
        """Provenance, not a goal. Across 1,474 markdown files every `Prompted by:` line
        is filled with the operator's own sentences; a guard that refuses them is refusing the
        record of what he said."""
        self.assertEqual(self._offences(
            '# Brief\n\n- **Prompted by:** operator: *"if the report works, make it '
            'list the finished items as well."*\n'), [])


if __name__ == "__main__":
    unittest.main()
