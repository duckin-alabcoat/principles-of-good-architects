"""WI-0278 — a mid-session escalation outlives the answer that ends it.

The `attention` record (WI-0162) is CURRENT STATE: one live row per lane, overwritten by
the next Notification and unlinked by the clear. That is correct for what it was built to
do — answer *is a human being waited on right now* — and it means the event is destroyed
by the act of answering it, which is the one moment you would want it kept. So the only
durable source of "how often did this system interrupt operator" was what a session still
remembered to write down at close, and the bias was measured: on 2026-09-04 the
federation's nineteen journals recorded eleven escalations between them while WI-0274
records roughly fifty relayed answers reaching him across the same period.

The properties pinned here are about the RECORD, not about delivery:

  1. THE EVENT SURVIVES THE ANSWER. A raise followed by a clear is still readable after
     the clear — the failure this item exists to reverse.
  2. THE WAIT IS DERIVABLE. Both ends carry a timestamp, so "he took four hours" is a
     number rather than a recollection. Sub-minute waits must not render as `0m`.
  3. PAIRING IS ON THE RAISE, NEVER ON ORDER. `_attention_write` overwrites, so raises
     and clears do not alternate, and an unanswered question must not inherit the next
     question's answer.
  4. THE CLOSE MOVES IT INTO THE TRACKED RECORD. The sidecar is gitignored per-machine
     residue; the journal is what survives, is backed up, and is mined fleet-wide.
  5. ONE SOURCE FOR THE COUNT. `curate/metrics.py` reads the JOURNAL and must never read
     the ledger — the item says so in as many words ("do not make this a second source of
     truth for the count").
  6. IT NEVER BREAKS THE HOOK IT RIDES ON. A telemetry write that can fail the session it
     reports on is worse than no telemetry.

stdlib unittest: python3 -m unittest tests.test_escalation_ledger
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (exercise_real_coord_holder,  # noqa: E402
                           neutralize_coord_journal, neutralize_live_store)
from test_session_branch import BranchGateBase  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("metrics", _ROOT / "curate" / "metrics.py")
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


CSID = "esc-test-session"
SID = "20260917T1200Z-devbox-e5c0"


class LedgerBase(unittest.TestCase):
    """The `test_attention` fixture, plus a real open journal.

    The journal is not decoration: `_coord_holder` resolves the DURABLE session id from
    the holding session's own frontmatter, and that id is what keys the ledger file. A
    fixture with no journal would exercise the "nothing to fold into, write nothing" path
    and silently prove nothing about the one this item is about.
    """

    def setUp(self):
        # Registered FIRST so it runs LAST (cleanups are LIFO, and they run after
        # tearDown). `exercise_real_coord_holder` below parks the CWD elsewhere and
        # restores whatever it found — and what it finds must not be a directory this
        # tearDown is about to delete, or every test in the module errors in cleanup.
        self.addCleanup(os.chdir, os.getcwd())
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "x").write_text("x", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.main
        neutralize_live_store(self, self.main)
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = CSID

        self.jdir = self.main / "sessions" / "journal"
        self.jdir.mkdir(parents=True, exist_ok=True)
        self._pj = mock.patch.object(session, "JOURNAL_DIR", self.jdir)
        self._pj.start()
        self.addCleanup(self._pj.stop)
        self.jpath = self.jdir / f"{SID}.md"
        self.write_journal()
        # The ledger is keyed by the DURABLE session id, which `_coord_holder` resolves
        # from the holding session's own journal frontmatter. The default neutraliser
        # returns `("", "", "")` — correct for a fixture that must not inherit the
        # developer's open journal, and fatal here, because an empty id means "nothing to
        # fold into" and the whole subject of this module stops being written. So opt out
        # by name, which also parks the CWD somewhere with no journal of its own.
        os.chdir(_ROOT)          # stable, and outside the tree tearDown removes
        exercise_real_coord_holder(self)

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_journal(self, body="### What happened\n\n- Session opened.\n", ended=""):
        self.jpath.write_text(
            "---\n"
            f"session-id: {SID}\n"
            "ordinal: 1\n"
            "title: (in progress)\n"
            "machine: DevBox\n"
            "runtime: claude-code\n"
            f"claude-session-id: {CSID}\n"
            "started: 2026-09-17T12:00:10.785010+00:00\n"
            f"ended: {ended}\n"
            "---\n\n" + body,
            encoding="utf-8")

    # -- drivers ------------------------------------------------------------------
    def notify(self, message):
        """The real `Notification` hook path, not `_attention_write` directly."""
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"session_id": "s",
                                             "hook_event_name": "Notification",
                                             "message": message}):
            with self.assertRaises(SystemExit):
                session.cmd_notify(argparse.Namespace())

    def events(self):
        return session.escalation_events(SID)

    def kinds(self):
        return [e.get("event") for e in self.events()]


# ── Property 1 — the event survives the answer ──────────────────────────────────────


class TheEventSurvivesTheAnswerTest(LedgerBase):

    def test_an_escalation_written_and_then_cleared_is_still_in_the_record(self):
        """THE ITEM'S ACCEPTANCE, in one test. Before this, answering the question was
        what destroyed the evidence that it had been asked."""
        self.notify("Claude needs your permission to use Bash")
        self.assertEqual(session._attention_state()[0], "waiting")

        self.assertTrue(session._attention_clear())
        self.assertEqual(session._attention_state()[0], "clear")   # live record gone...

        evs = self.events()                                         # ...record is not
        self.assertEqual([e["event"] for e in evs], ["raised", "cleared"])
        self.assertIn("permission to use Bash", evs[0]["message"])

    def test_the_lane_and_the_item_ride_along_with_the_message(self):
        """A count with no subject sends the reader back to grep, which is the thing this
        whole metric exists because it cannot do."""
        d = session._coord_dir("claims", create=True)
        session.atomic_write(d / "WI-0278.json", json.dumps(session._coord_record(
            CSID, 3600, kind="claims", name="WI-0278")) + "\n")
        self.notify("which option do you want?")
        rec = self.events()[0]
        self.assertEqual(rec["item"], "WI-0278")
        self.assertTrue(rec["lane"])
        self.assertEqual(rec["claude_session_id"], CSID)

    def test_a_second_escalation_does_not_overwrite_the_first(self):
        """The live record IS overwritten — one row per lane — and that is exactly the
        property the ledger exists to compensate for."""
        self.notify("first question")
        self.notify("second question")
        raised = [e for e in self.events() if e["event"] == "raised"]
        self.assertEqual(len(raised), 2)
        self.assertEqual([e["message"] for e in raised], ["first question", "second question"])
        self.assertEqual(len(session._attention_state()[1]), 1)     # still one live row

    def test_nothing_is_written_for_a_session_with_no_journal_to_fold_into(self):
        """The ledger is keyed by the durable session id. With no journal there is no id,
        so there is nothing that could ever fold the record — and a file nothing reads is
        residue, not evidence."""
        self.jpath.unlink()
        self.notify("a question from a session with no journal")
        self.assertEqual(session.escalation_events(SID), [])
        self.assertIsNone(session._escalation_log_path(""))


# ── Property 2 — the wait is derivable ──────────────────────────────────────────────


class TheWaitIsDerivableTest(LedgerBase):

    def test_both_ends_carry_a_timestamp_and_they_join(self):
        self.notify("how long will this take")
        session._attention_clear()
        raised, cleared = self.events()
        self.assertTrue(raised["at"])
        self.assertTrue(cleared["at"])
        self.assertEqual(cleared["raised_at"], raised["at"],
                         "the clear must name the raise it answers, not merely follow it")

    def test_a_sub_minute_answer_is_not_rendered_as_zero(self):
        """`format_duration` rounds to whole minutes, so every fast answer would print
        `0m` and a fan-out stalling on sub-minute round-trips would be indistinguishable
        from one that never stalled."""
        self.assertEqual(session._escalation_wait("2026-09-17T11:00:00+00:00",
                                                  "2026-09-17T11:00:07+00:00"), "7s")

    def test_minutes_and_hours_both_render(self):
        self.assertEqual(session._escalation_wait("2026-09-17T11:00:00+00:00",
                                                  "2026-09-17T11:04:30+00:00"), "4m 30s")
        self.assertEqual(session._escalation_wait("2026-09-17T11:00:00+00:00",
                                                  "2026-09-17T15:07:00+00:00"), "4h 07m")

    def test_an_uncomputable_wait_is_empty_never_a_guess(self):
        self.assertEqual(session._escalation_wait("", "2026-09-17T11:00:00+00:00"), "")
        self.assertEqual(session._escalation_wait("not-a-date", "also-not"), "")
        # Clock skew must not manufacture a negative duration.
        self.assertEqual(session._escalation_wait("2026-09-17T11:05:00+00:00",
                                                  "2026-09-17T11:00:00+00:00"), "")


# ── Property 3 — pairing is on the raise, never on order ────────────────────────────


class PairingIsOnTheRaiseNeverOnOrderTest(LedgerBase):

    def test_an_unanswered_question_does_not_inherit_the_next_ones_answer(self):
        """Two raises and one clear. Order-based pairing would attribute the answer to the
        FIRST question and report the second as still open — the exact inversion."""
        self.notify("question one, never answered")
        self.notify("question two, answered")
        session._attention_clear()
        out = session.render_midsession_escalations(self.events())
        first, second = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertIn("question one", first)
        self.assertIn("NO CLEAR OBSERVED", first)
        self.assertIn("question two", second)
        self.assertIn("answered after", second)

    def test_a_record_freed_without_a_clear_reads_as_unanswered_not_as_fast(self):
        """The reaper's attention arm unlinks without routing through `_attention_clear`,
        so no clear line is ever written for it. Absent must not render as instant."""
        self.notify("reaped, never answered")
        out = session.render_midsession_escalations(self.events())
        self.assertIn("NO CLEAR OBSERVED", out)
        self.assertNotIn("answered after", out)

    def test_clearing_nothing_writes_no_line(self):
        """The heartbeat clears on EVERY tool call. A clear line per beat would bury the
        real events under thousands of no-ops."""
        self.assertFalse(session._attention_clear())
        self.assertEqual(self.events(), [])


# ── Property 4 — the close moves it into the tracked record ─────────────────────────


class TheCloseFoldsItIntoTheJournalTest(LedgerBase):

    def _fold(self):
        text = session.fold_midsession_escalations(
            self.jpath.read_text(encoding="utf-8"), SID)
        self.jpath.write_text(text, encoding="utf-8")
        return text

    def test_the_section_appears_and_the_ledger_reader_counts_it(self):
        self.notify("first")
        session._attention_clear()
        self.notify("second")
        session._attention_clear()
        text = self._fold()
        self.assertIn(session.MIDSESSION_HEADING, text)
        _fm, body = session.parse_journal(text)
        self.assertEqual(
            session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 2)

    def test_the_authored_sections_are_untouched(self):
        """`### Open questions for the user` means OPEN AT CLOSE. Folding answered
        questions into it would report a question that was answered as still open — and,
        since WI-0405 retired `probe_parked_questions` and put both sections into one
        intervention measure, would also collapse the at-close/mid-session split that
        WI-0274's five-to-one finding rests on."""
        self.write_journal("### What happened\n\n- Worked.\n\n"
                           "### Open questions for the user\n\n- Only this one.\n")
        self.notify("a mid-session interruption")
        session._attention_clear()
        _fm, body = session.parse_journal(self._fold())
        self.assertEqual(
            session.ledger_count(body, session.LEDGER_ESCALATION_HEADINGS), 1)
        self.assertEqual(
            session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)
        self.assertIn("- Only this one.", body)

    def test_folding_twice_does_not_double_the_entries(self):
        """`end` is not the only route to a journal — a refused close, a `merge
        --continue` then an `end`, a re-render. A second append would double the one
        number this item exists to make trustworthy."""
        self.notify("asked once")
        session._attention_clear()
        first = self._fold()
        second = self._fold()
        self.assertEqual(first, second)
        _fm, body = session.parse_journal(second)
        self.assertEqual(
            session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)

    def test_an_empty_ledger_leaves_the_journal_byte_identical(self):
        before = self.jpath.read_text(encoding="utf-8")
        self.assertEqual(session.fold_midsession_escalations(before, SID), before)

    def test_an_unreadable_ledger_never_deletes_a_written_section(self):
        """`escalation_events` answers `[]` both for 'escalated nothing' and for 'could
        not read'. Deleting a record on the strength of that would let an unreadable file
        erase evidence — the direction this item exists to reverse."""
        self.notify("asked once")
        session._attention_clear()
        folded = self._fold()
        session._escalation_log_path(SID).unlink()
        self.assertEqual(session.fold_midsession_escalations(folded, SID), folded)

    def test_a_payload_carrying_markdown_cannot_inflate_the_count(self):
        """The journal is parsed structurally. A raw notification with a newline and a
        bullet would split into extra ledger entries; one with a `###` at a line start
        would end the section early."""
        self.notify("line one\n- forged entry\n### Notes filed\n- another")
        session._attention_clear()
        _fm, body = session.parse_journal(self._fold())
        self.assertEqual(
            session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)
        self.assertIn("forged entry", body)          # preserved, just not as an entry

    def test_the_close_verb_folds_before_it_stamps(self):
        """Source-level, like `test_session_end_clears_it_so_no_ghost_outlives_the_lane`:
        the fold has to read a body that already carries the final clear, and it has to
        happen before `finalize_journal` or the close writes the journal without it."""
        src = (_ROOT / "sessionlib" / "hooks.py").read_text(encoding="utf-8")
        body = src.split("def cmd_end", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("fold_midsession_escalations(", body)
        self.assertLess(body.index("_attention_clear()"),
                        body.index("fold_midsession_escalations("))
        self.assertLess(body.index("fold_midsession_escalations("),
                        body.index("closed = finalize_journal("))


# ── Property 5 — one source for the count ───────────────────────────────────────────


class TheJournalIsTheSingleSourceForTheCountTest(LedgerBase):

    def test_the_count_survives_the_sidecar_being_deleted(self):
        """The sidecar is gitignored per-machine residue the janitor drops on its own
        schedule. If the number moved when it went away, the tracked journal would not be
        the record — which is the whole reason the close folds it in."""
        self.notify("asked")
        session._attention_clear()
        self.jpath.write_text(session.fold_midsession_escalations(
            self.jpath.read_text(encoding="utf-8"), SID), encoding="utf-8")
        session._escalation_log_path(SID).unlink()

        mined = metrics.mine_journal_sessions(self.main)
        self.assertEqual(len(mined), 1)
        self.assertEqual(mined[0]["midsession_escalations"], 1)

    def test_metrics_never_reaches_the_ledger_sidecar(self):
        """Structural, not incidental. metrics mines members whose harness version it does
        not control, over a `.session-state/` that is per-machine and unbackupable — a
        second reader there would make the fleet number depend on which machine ran it."""
        src = (_ROOT / "curate" / "metrics.py").read_text(encoding="utf-8")
        for forbidden in ("escalation_events", "_escalation_log_path",
                          "ESCALATION_LOG_SUFFIX", "escalations.jsonl"):
            self.assertNotIn(forbidden, src,
                             f"metrics.py must read the journal, never the ledger "
                             f"({forbidden})")

    def test_a_folded_section_does_not_make_an_empty_journal_look_authored(self):
        """`boilerplate` means the ARCHITECT wrote nothing beyond the turn-1 stub. Every
        line the fold contributes was written by the harness, so a session that recorded
        nothing and interrupted operator once must still read as boilerplate — otherwise the
        conformance surface asserts substance the journal does not have."""
        self.assertTrue(metrics.mine_journal_sessions(self.main)[0]["boilerplate"])
        self.notify("a question, and nothing else this session")
        session._attention_clear()
        self.jpath.write_text(session.fold_midsession_escalations(
            self.jpath.read_text(encoding="utf-8"), SID), encoding="utf-8")

        mined = metrics.mine_journal_sessions(self.main)[0]
        self.assertEqual(mined["midsession_escalations"], 1)   # the fold DID happen...
        self.assertTrue(mined["boilerplate"],                  # ...and changed nothing
                        "the machine-written section is not authored content")

    def test_stripping_the_section_leaves_authored_prose_alone(self):
        body = (f"### What happened\n\n- Real work.\n\n{session.MIDSESSION_HEADING}\n\n"
                "- **11:00** -- q -- answered after 5s\n\n### Notes filed\n\n- Kept.\n")
        out = session.strip_midsession_escalations(body)
        self.assertIn("- Real work.", out)
        self.assertIn("- Kept.", out)
        self.assertNotIn(session.MIDSESSION_HEADING, out)
        self.assertNotIn("answered after", out)
        self.assertEqual(session.strip_midsession_escalations("### Only\n\n- a\n"),
                         "### Only\n\n- a\n")

    def test_an_absent_section_is_unknown_never_zero(self):
        """Every journal written before the fold shipped lacks this section. Reading those
        as zero would report the blind spot as a clean month — the failure ADR-0099 D4
        refused to half-ship."""
        mined = metrics.mine_journal_sessions(self.main)
        self.assertIsNone(mined[0]["midsession_escalations"])

    def test_the_headline_counts_both_halves_but_still_reports_the_ratio(self):
        """WI-0274's finding IS the ratio — eleven written at close against roughly fifty
        answered in flight. The headline must include mid-session or a session that
        interrupted operator twenty times reads as `escalations at ZERO`; the split must
        survive or the comparison that found the problem is destroyed."""
        import datetime as _dt
        today = _dt.date(2026, 9, 17)
        started = _dt.datetime(2026, 9, 16)
        lg = metrics.summarize_ledger(
            [{"path": None, "started": started, "escalations": 2,
              "midsession_escalations": 5, "decisions": 0}], today)
        self.assertEqual(lg["escalations"], 7)
        self.assertEqual(lg["escalations_at_close"], 2)
        self.assertEqual(lg["escalations_midsession"], 5)

    def test_the_blind_spot_is_no_longer_declared_unmined(self):
        """A signal named in `NOT_YET_MINED` is one the report says it cannot see. Leaving
        it after the source exists understates the number as thoroughly as the silent
        omission it was added to prevent."""
        self.assertNotIn("mid-session-escalations", metrics.NOT_YET_MINED)
        self.assertIs(metrics.MIDSESSION_HEADINGS, session.LEDGER_MIDSESSION_HEADINGS)

    def test_the_three_headings_are_three_different_sections(self):
        body = ("### Open questions for the user\n\n- a\n\n"
                "### Decisions I made without you, for review\n\n- b\n- c\n\n"
                f"{session.MIDSESSION_HEADING}\n\n- d\n- e\n- f\n")
        self.assertEqual(session.ledger_count(body, session.LEDGER_ESCALATION_HEADINGS), 1)
        self.assertEqual(session.ledger_count(body, session.LEDGER_DECISION_HEADINGS), 2)
        self.assertEqual(session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 3)


# ── Property 6 — it never breaks the hook it rides on ───────────────────────────────


class ItNeverBreaksTheHookItRidesOnTest(LedgerBase):

    def test_a_ledger_write_failure_does_not_fail_the_attention_write(self):
        with mock.patch("builtins.open", side_effect=OSError("read-only")):
            self.assertFalse(session._escalation_append(SID, "raised", {"at": "x"}))
        self.assertTrue(session._attention_write("still recorded"))

    def test_a_ledger_write_failure_does_not_fail_the_notify_hook(self):
        with mock.patch.object(session, "_escalation_append",
                               side_effect=OSError("boom")):
            with self.assertRaises(SystemExit) as cm:
                with mock.patch.object(session, "_read_hook_stdin",
                                       return_value={"message": "q"}):
                    session.cmd_notify(argparse.Namespace())
        self.assertEqual(cm.exception.code, 0)

    def test_a_corrupt_line_is_skipped_rather_than_fatal(self):
        """A hook whose process died mid-write leaves a partial line. It must not be able
        to fail the close that reads the file."""
        self.notify("good one")
        p = session._escalation_log_path(SID)
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"event": "raised", "at"\n')      # truncated
            f.write("\n")                              # blank
            f.write('"not an object"\n')
        self.assertEqual([e["event"] for e in self.events()], ["raised"])

    def test_the_clear_still_frees_the_record_when_the_ledger_cannot_be_written(self):
        """Direction matters: the coordination record must be freed even if the telemetry
        about freeing it cannot be written. A lane left reading WAITING ON YOU because a
        log was read-only is the ghost this substrate exists to avoid."""
        self.notify("asked")
        with mock.patch.object(session, "_escalation_append",
                               side_effect=OSError("read-only")):
            with self.assertRaises(OSError):
                session._attention_clear()
        self.assertEqual(session._attention_state()[0], "clear")


# ── Retention — the ledger is per-session residue, not a shared log ─────────────────


class TheLedgerHasARetentionOwnerTest(LedgerBase):

    def test_the_janitor_knows_the_suffix(self):
        """`.jsonl` is deliberately absent from the janitor's list — the SHARED logs there
        are append-only records with their own lifecycle. This one is keyed by a single
        session and its durable copy is the tracked journal, so without a line here it
        would be the one per-session sidecar nothing ever drops."""
        self.assertIn(f".{session.ESCALATION_LOG_SUFFIX}",
                      session.JANITOR_SIDECAR_SUFFIXES)

    def test_the_suffix_is_not_liveness_evidence(self):
        """A stem naming no session does harm only where a verdict READS it. The ledger
        must not be globbable as a heartbeat, or a test fixture's leavings would hold a
        lane against `recover` for the full crash grace (WI-0270)."""
        self.assertNotIn(f".{session.ESCALATION_LOG_SUFFIX}", session.LIVENESS_SUFFIXES)
        self.assertTrue(session.ESCALATION_LOG_SUFFIX.endswith(".jsonl"))

    def test_an_aged_ledger_for_a_closed_session_is_pruned(self):
        state = session.SESSION_STATE_DIR
        state.mkdir(parents=True, exist_ok=True)
        p = state / f"{SID}.{session.ESCALATION_LOG_SUFFIX}"
        p.write_text('{"event": "raised"}\n', encoding="utf-8")
        old = p.stat().st_mtime - (session.JANITOR_SIDECAR_KEEP_DAYS + 1) * 86400
        os.utime(p, (old, old))
        session._janitor_prune_sidecars(state, set(), p.stat().st_mtime + 1, "", False)
        self.assertFalse(p.exists())

    def test_a_fresh_ledger_is_left_alone(self):
        self.notify("in flight right now")
        p = session._escalation_log_path(SID)
        self.assertTrue(p.is_file())
        cutoff = p.stat().st_mtime - 86400
        session._janitor_prune_sidecars(p.parent, set(), cutoff, "", False)
        self.assertTrue(p.is_file())


# ── The close itself, driven for real ───────────────────────────────────────────────


E2E_SID = "20260917T2000Z-runner-0009"


class TheRealCloseFoldsTheLedgerTest(BranchGateBase):
    """`cmd_end` end to end, because a source-level assertion is STRUCTURE, not BEHAVIOUR.

    The sibling check in `TheCloseFoldsItIntoTheJournalTest` greps `cmd_end` for the call
    and for its position relative to the clear and the stamp. That is worth having — it
    catches a reordering — and it would go on passing if the call were unreachable, if the
    ledger key did not match what the close resolves, or if `finalize_journal` were handed
    the file's bytes instead of the folded text. Every one of those is a way the feature
    ships dead while the guard stays green ([`verify-in-the-created-configuration`], and
    `exercise-delegated-work-end-to-end` for the same reason one layer out).

    So this drives the real verb against a real throwaway repo and reads the journal that
    lands on disk.
    """

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_CODE_SESSION_ID"] = "csid-" + E2E_SID
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        self._write_journal(E2E_SID)
        self.jpath = session.JOURNAL_DIR / f"{E2E_SID}.md"

    def _end(self, **over):
        base = dict(session_id=E2E_SID, title="close", commit=None, push=False,
                    focus=None, blocked=None, no_merge=True, dry_run=False,
                    confirm="yes, close it out")
        base.update(over)
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_end(argparse.Namespace(**base))
        return self.jpath.read_text(encoding="utf-8")

    def test_a_question_raised_and_answered_reaches_the_closed_journal(self):
        session._attention_write("Claude needs your permission to use Bash")
        session._attention_clear()
        text = self._end()

        fm, body = session.parse_journal(text)
        self.assertTrue(fm.get("ended"), "the close still closes")
        self.assertEqual(session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)
        self.assertIn("permission to use Bash", body)
        self.assertIn("answered after", body)

    def test_a_question_still_open_at_the_close_is_answered_BY_the_close(self):
        """`cmd_end` clears the attention record before it folds, so the last question's
        clear is already on the ledger by the time this reads. That is why the wait is a
        number rather than a blank — and it is the ordering the source-level check pins."""
        session._attention_write("still waiting when the session ended")
        text = self._end()
        _fm, body = session.parse_journal(text)
        self.assertEqual(session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)
        self.assertIn("still waiting when the session ended", body)
        self.assertNotIn("NO CLEAR OBSERVED", body)

    def test_a_session_that_escalated_nothing_gains_no_section(self):
        """An absent section is what `ledger_count` reads as None — unknown, not zero. A
        heading emitted for every close would turn every quiet session into a measured
        zero and quietly re-fabricate the number this item exists to make honest."""
        _fm, body = session.parse_journal(self._end())
        self.assertNotIn(session.MIDSESSION_HEADING, body)
        self.assertIsNone(
            session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS))

    def test_the_authored_body_survives_the_fold(self):
        text = self.jpath.read_text(encoding="utf-8")
        fm, body = session.parse_journal(text)
        self.jpath.write_text(
            session._rerender_journal(fm, body.rstrip("\n") + "\n\n"
                                      "### Open questions for the user\n\n- Authored.\n"),
            encoding="utf-8")
        session._attention_write("and a machine-written one")
        session._attention_clear()
        _fm, body = session.parse_journal(self._end())
        self.assertIn("- Authored.", body)
        self.assertEqual(session.ledger_count(body, session.LEDGER_ESCALATION_HEADINGS), 1)
        self.assertEqual(session.ledger_count(body, session.LEDGER_MIDSESSION_HEADINGS), 1)


if __name__ == "__main__":
    unittest.main()
