"""Tests for the ADR-0082 D1 prep/adoption handshake.

The shape under test: `poga` runs the session-start ritual BEFORE launching an agent,
so at prep time the runtime's own session id does not exist yet. Without a rendezvous
the runtime's own start looks itself up by that id, misses, and opens a SECOND journal
for one session — the defect this handshake exists to prevent.

What must hold, and why each one bit:
  - prep writes a journal carrying the RESOLVED runtime, not a hardcoded `claude-code`
    (otherwise a codex lane's journal claims to be a Claude session — WI-0060's defect
    inverted);
  - the runtime's start ADOPTS that journal rather than allocating: exactly one journal
    exists afterwards, and it carries the runtime's session id so `end` can still find it;
  - the marker is CONSUMED, so a second runtime launched into the same lane cannot claim
    the same journal;
  - adoption DECLINES rather than guessing whenever the match is not unambiguous — no
    marker, a journal that vanished, one already closed, or one already held by a
    different live session. Declining degrades to today's behaviour (an extra journal);
    guessing would give two sessions one record, which is worse than the bug;
  - a start with no prep marker is byte-for-byte the pre-ADR-0082 behaviour, including
    the default runtime — Phase 2 promises no behaviour change for anyone not using it.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402

GIT = shutil.which("git")

PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class PrepAdoptionBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "role.md").write_text("**Version:** v1.0.0\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "branch", "-M", "main")

        (self.repo / "sessions" / "journal").mkdir(parents=True)
        (self.repo / "sessions" / "pre-journal-archive.md").write_text("", encoding="utf-8")
        (self.repo / "STATUS.md").write_text(
            "---\nid: test\nversion: v0\nlast_active: 2026-01-01\nfocus: x\nblocked: false\n---\n",
            encoding="utf-8")

        # CANON_PATH / STANDARD_PATH are module constants derived from ROOT at IMPORT,
        # so overriding ROOT alone leaves them pointed at the real repo — the fixture
        # would then read the federation's own canon and a delivery test would pass
        # without the fixture proving anything. Point them at known fixture content.
        (self.repo / "CANON.md").write_text(
            "# Canon digest\n\n- **P1 `fixture-principle`** — a known marker.\n", encoding="utf-8")
        (self.repo / "STANDARD.md").write_text(
            "# Standard role-doc section\n\nfixture-standard-marker\n", encoding="utf-8")

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR", "CONFIG_PATH",
                       "CFG", "CANON_PATH", "STANDARD_PATH")}
        session.CANON_PATH = self.repo / "CANON.md"
        session.STANDARD_PATH = self.repo / "STANDARD.md"
        session.ROOT = self.repo
        session.JOURNAL_DIR = self.repo / "sessions" / "journal"
        session.ARCHIVE = self.repo / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = self.repo / ".session-state"
        session.CONFIG_PATH = self.repo / "session.config.json"
        session.CFG = {
            "handoff": self.repo / "session-handoff.md",
            "status": self.repo / "STATUS.md",
            "role_doc": self.repo / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect",
            "architect_id": "test-arch",
            "machine_map": {},
            "user_name": "operator",
            "inbox": None,
            "trunk": "main",
            "branch_sessions": False,
            "gate": PASS_GATE,
        }

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -------------------------------------------------------------
    def _start(self, session_id=None, prep=False, runtime=None):
        """Run cmd_start the way a caller would: a hook supplies a session_id on stdin,
        the wrapper (prep) does not."""
        args = argparse.Namespace(dry_run=False, prep=prep, runtime=runtime)
        hook = {"session_id": session_id} if session_id else {}
        with mock.patch.object(session, "_read_hook_stdin", return_value=hook):
            session.cmd_start(args)

    def _journals(self):
        return sorted(session.JOURNAL_DIR.glob("*.md"))

    def _fm(self, path):
        fm, _ = session.parse_journal(path.read_text(encoding="utf-8"))
        return fm


class PrepMarkerTest(PrepAdoptionBase):
    def test_prep_records_the_resolved_runtime_not_a_constant(self):
        """A codex lane's journal must say codex. Hardcoding claude-code here would
        re-create WI-0060 (the record cannot say what ran) from the other direction."""
        self._start(prep=True, runtime="codex")
        js = self._journals()
        self.assertEqual(len(js), 1)
        self.assertEqual(self._fm(js[0]).get("runtime"), "codex")

    def test_prep_leaves_a_marker_naming_the_journal_it_created(self):
        self._start(prep=True, runtime="codex")
        marker = session._prep_marker()
        self.assertTrue(marker.is_file(), "prep must leave the rendezvous marker")
        rec = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(rec["session_id"], self._fm(self._journals()[0])["session-id"])
        self.assertEqual(rec["runtime"], "codex")

    def test_a_start_that_is_not_prep_leaves_no_marker(self):
        """The marker is what opts a lane into adoption; an ordinary start must not
        arm it, or the NEXT session in this worktree would adopt a stale journal."""
        self._start(prep=False)
        self.assertFalse(session._prep_marker().exists())

    def test_default_runtime_is_unchanged_when_none_is_passed(self):
        """Phase 2 promises no behaviour change for anyone not using it."""
        self._start(prep=False)
        self.assertEqual(self._fm(self._journals()[0]).get("runtime"), session.CLAUDE_RUNTIME)


class AdoptionTest(PrepAdoptionBase):
    def test_runtime_adopts_the_prepped_journal_instead_of_opening_a_second(self):
        """The whole point: one session, one record."""
        self._start(prep=True, runtime="codex")
        prepped = self._fm(self._journals()[0])["session-id"]

        self._start(session_id="csid-abc")

        js = self._journals()
        self.assertEqual(len(js), 1, "adoption must not allocate a second journal")
        fm = self._fm(js[0])
        self.assertEqual(fm["session-id"], prepped, "the prepped journal is the one kept")
        self.assertEqual(fm["claude-session-id"], "csid-abc",
                         "the runtime's id must be stamped in, or `end` cannot find it")
        self.assertEqual(fm["runtime"], "codex", "adoption must not overwrite the runtime")

    def test_adoption_consumes_the_marker(self):
        """A second runtime launched into the same lane must not claim the same journal."""
        self._start(prep=True, runtime="codex")
        self._start(session_id="csid-abc")
        self.assertFalse(session._prep_marker().exists())

    def test_adopted_session_is_findable_by_its_runtime_id(self):
        """`end` matches on claude-session-id — adoption is worthless if that lookup
        does not resolve afterwards."""
        self._start(prep=True, runtime="codex")
        self._start(session_id="csid-abc")
        found = session.find_open_journal(claude_session_id="csid-abc")
        self.assertIsNotNone(found)

    def test_second_runtime_does_not_steal_an_already_adopted_journal(self):
        """The dangerous case. A marker re-pointed at a journal another session already
        holds must be DECLINED — two sessions sharing one record is worse than the extra
        journal that declining produces."""
        self._start(prep=True, runtime="codex")
        did = self._fm(self._journals()[0])["session-id"]
        self._start(session_id="csid-abc")

        session._write_prep_marker(did, "codex")
        self.assertIsNone(session._claim_prepped_journal("csid-other"))
        self.assertEqual(self._fm(session.journal_path(did))["claude-session-id"], "csid-abc")

    def test_reclaim_by_the_same_session_is_allowed(self):
        """Idempotency: a re-run of the same runtime session is a no-op, not a refusal."""
        self._start(prep=True, runtime="codex")
        did = self._fm(self._journals()[0])["session-id"]
        self._start(session_id="csid-abc")

        session._write_prep_marker(did, "codex")
        self.assertIsNotNone(session._claim_prepped_journal("csid-abc"))


class AdoptionDeclinesTest(PrepAdoptionBase):
    def test_no_marker_declines(self):
        self.assertIsNone(session._claim_prepped_journal("csid-abc"))

    def test_marker_naming_a_missing_journal_declines(self):
        session._write_prep_marker("20260101T0000Z-runner-dead", "codex")
        self.assertIsNone(session._claim_prepped_journal("csid-abc"))

    def test_marker_naming_a_closed_journal_declines(self):
        """A closed journal is a finished session; adopting it would reopen history."""
        self._start(prep=True, runtime="codex")
        did = self._fm(self._journals()[0])["session-id"]
        p = session.journal_path(did)
        p.write_text(
            session.finalize_journal(p.read_text(encoding="utf-8"), "2026-01-01T00:00:00", "1h"),
            encoding="utf-8")
        session._write_prep_marker(did, "codex")
        self.assertIsNone(session._claim_prepped_journal("csid-abc"))

    def test_corrupt_marker_declines_without_raising(self):
        """Fail-open: a bad marker must degrade to an extra journal, never to a failed
        start — a start that dies is a session that never runs."""
        session.SESSION_STATE_DIR.mkdir(exist_ok=True)
        session._prep_marker().write_text("{not json", encoding="utf-8")
        self.assertIsNone(session._claim_prepped_journal("csid-abc"))

    def test_a_start_with_no_marker_falls_through_to_the_lazy_path(self):
        """Declining must leave the ordinary path intact, not suppress it.

        Note what "ordinary" is here: a hook-path start with nothing to adopt is LAZY
        (ADR-0055) — it writes a `.pending-start` marker and defers the journal to the
        first heartbeat, so that a phantom SessionStart allocates nothing. The assertion
        is therefore that the marker appears and NO journal does yet; an earlier draft of
        this test asserted a journal and failed, which is the test catching the author
        rather than the code."""
        self._start(session_id="csid-abc")
        self.assertEqual(self._journals(), [], "the hook path must still defer, not allocate")
        self.assertTrue(session._pending_marker("csid-abc").is_file(),
                        "lazy start must still arm its own marker when adoption declines")


class ProcessAliveTest(unittest.TestCase):
    def test_own_pid_is_alive(self):
        import os
        self.assertTrue(session._process_alive(os.getpid()))

    def test_reaped_child_is_not_alive(self):
        proc = subprocess.Popen(["python3", "-c", "pass"])
        proc.wait()
        self.assertFalse(session._process_alive(proc.pid))

    def test_permission_error_counts_as_alive(self):
        """EPERM means the process EXISTS but is not ours. A blanket
        `except OSError: return False` gets this backwards and reports a living session
        dead — the one error here that costs real work, since it hands a live session to
        the reaper."""
        with mock.patch.object(session.os, "kill", side_effect=PermissionError()):
            self.assertTrue(session._process_alive(4242))

    def test_lookup_error_counts_as_dead(self):
        with mock.patch.object(session.os, "kill", side_effect=ProcessLookupError()):
            self.assertFalse(session._process_alive(4242))


class SuperviseTest(PrepAdoptionBase):
    def _prepped(self):
        self._start(prep=True, runtime="codex")
        return self._fm(self._journals()[0])["session-id"]

    def test_beat_keys_the_sidecar_by_the_durable_id_before_adoption(self):
        did = self._prepped()
        self.assertTrue(session._supervise_beat(did))
        self.assertTrue((session.SESSION_STATE_DIR / f"{did}.live").is_file())

    def test_beat_follows_the_runtime_id_once_adoption_stamps_it(self):
        """The key does not exist when the watch starts and appears part-way through.
        Re-reading each beat is what lets the evidence land where the reaper actually
        looks (`_sidecar_evidence_by_csid`); capturing it once would beat forever into a
        namespace nothing reads."""
        did = self._prepped()
        session._supervise_beat(did)
        self._start(session_id="csid-abc")
        self.assertTrue(session._supervise_beat(did))

        live = session.SESSION_STATE_DIR / "csid-abc.live"
        self.assertTrue(live.is_file(), "the beat must follow the adopted session id")
        ev = session._sidecar_evidence_by_csid("csid-abc")
        self.assertTrue(ev["has_live"], "the reaper's own reader must see the beat")

    def test_beat_marks_its_provenance(self):
        """A supervised beat is process-alive, not tool-active. Anything reasoning about
        staleness has to be able to tell which kind it is reading."""
        did = self._prepped()
        session._supervise_beat(did)
        data = json.loads((session.SESSION_STATE_DIR / f"{did}.live").read_text(encoding="utf-8"))
        self.assertEqual(data["beat_source"], "supervisor")

    def test_beat_records_the_process_it_is_watching(self):
        """Provenance alone was not actionable — it said the beat was coarse without
        saying what it was coarse ABOUT. Carrying the pid (and the machine that pid means
        anything on) is what lets a later reader resolve silence instead of waiting it
        out."""
        import os
        did = self._prepped()
        session._supervise_beat(did, os.getpid())
        data = json.loads((session.SESSION_STATE_DIR / f"{did}.live").read_text(encoding="utf-8"))
        self.assertEqual(data["beat_pid"], os.getpid())
        self.assertTrue(data["beat_machine"], "a pid with no namespace is unprobeable")

    def test_a_beat_with_no_pid_records_none_rather_than_a_placeholder(self):
        """A caller that does not know the pid must leave the field ABSENT. A zero or a
        guess would be probed as if it were real."""
        did = self._prepped()
        session._supervise_beat(did)
        data = json.loads((session.SESSION_STATE_DIR / f"{did}.live").read_text(encoding="utf-8"))
        self.assertNotIn("beat_pid", data)
        ev = session._sidecar_evidence_by_csid(did)
        self.assertEqual("unknown", session._supervised_pid_state(ev))

    def test_a_real_dead_subject_reads_as_gone_end_to_end(self):
        """The whole chain against a real process rather than a mocked answer: the loop
        beats, the process dies, and the evidence the reaper reads says so."""
        import os
        did = self._prepped()
        proc = subprocess.Popen(["python3", "-c", "pass"])
        proc.wait()
        session._supervise_beat(did, proc.pid)
        ev = session._sidecar_evidence_by_csid(did)
        self.assertEqual("supervisor", ev["beat_source"])
        self.assertEqual(proc.pid, ev["beat_pid"])
        self.assertEqual("gone", session._supervised_pid_state(ev))

        session._supervise_beat(did, os.getpid())      # …and a live one reads as alive
        self.assertEqual("alive",
                         session._supervised_pid_state(session._sidecar_evidence_by_csid(did)))

    def test_beat_stops_when_the_journal_closes(self):
        """A landed lane must not keep beating."""
        did = self._prepped()
        p = session.journal_path(did)
        p.write_text(
            session.finalize_journal(p.read_text(encoding="utf-8"), "2026-01-01T00:00:00", "1h"),
            encoding="utf-8")
        self.assertFalse(session._supervise_beat(did))

    def test_beat_stops_when_the_journal_is_gone(self):
        did = self._prepped()
        session.journal_path(did).unlink()
        self.assertFalse(session._supervise_beat(did))

    def test_transient_read_error_does_not_stop_the_watch(self):
        """Failing to READ a journal is not evidence a session ended."""
        did = self._prepped()
        with mock.patch.object(session, "parse_journal", side_effect=OSError("boom")):
            self.assertTrue(session._supervise_beat(did))

    def test_loop_exits_when_the_watched_process_dies(self):
        did = self._prepped()
        proc = subprocess.Popen(["python3", "-c", "pass"])
        proc.wait()
        self.assertEqual(session._supervise_loop(did, proc.pid, interval=0), "exited")

    def test_loop_stops_on_a_closed_journal_even_while_the_process_lives(self):
        import os
        did = self._prepped()
        p = session.journal_path(did)
        p.write_text(
            session.finalize_journal(p.read_text(encoding="utf-8"), "2026-01-01T00:00:00", "1h"),
            encoding="utf-8")
        self.assertEqual(session._supervise_loop(did, os.getpid(), interval=0), "closed")

    def test_loop_is_bounded_by_max_beats(self):
        import os
        did = self._prepped()
        self.assertEqual(
            session._supervise_loop(did, os.getpid(), interval=0, max_beats=3), "max-beats")

    def test_supervise_resolves_the_session_from_the_prep_marker(self):
        did = self._prepped()
        self.assertEqual(session._prep_session_id(), did)

    def test_supervise_declines_a_dead_pid_without_watching(self):
        """Fail-open: nothing to watch must never stop a session from starting."""
        self._prepped()
        proc = subprocess.Popen(["python3", "-c", "pass"])
        proc.wait()
        args = argparse.Namespace(pid=proc.pid, session_id=None, detach=False,
                                  interval=0, max_beats=1)
        with self.assertRaises(SystemExit) as cm:
            session.cmd_supervise(args)
        self.assertEqual(cm.exception.code, 0)

    def test_supervise_declines_when_there_is_nothing_to_watch(self):
        import os
        args = argparse.Namespace(pid=os.getpid(), session_id=None, detach=False,
                                  interval=0, max_beats=1)
        with self.assertRaises(SystemExit) as cm:
            session.cmd_supervise(args)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(self._journals(), [])


class ObservedExitTest(PrepAdoptionBase):
    """ADR-0082 D7: a watcher that SAW the process go is definitive death evidence, so
    it does not need the crash grace. The grace exists for SILENCE, which is ambiguous."""

    def _prepped_and_adopted(self):
        self._start(prep=True, runtime="codex")
        did = self._fm(self._journals()[0])["session-id"]
        self._start(session_id="csid-abc")
        return did

    def test_watcher_records_an_observed_exit(self):
        did = self._prepped_and_adopted()
        proc = subprocess.Popen(["python3", "-c", "pass"])
        proc.wait()
        args = argparse.Namespace(pid=proc.pid, session_id=did, detach=False,
                                  interval=0, max_beats=None)
        with self.assertRaises(SystemExit):
            session.cmd_supervise(args)
        # dead pid -> declines to watch, so nothing observed and nothing claimed
        self.assertFalse((session.SESSION_STATE_DIR / "csid-abc.gone").exists())

    def test_gone_marker_is_written_under_the_adopted_key(self):
        did = self._prepped_and_adopted()
        session._write_gone_marker(session._supervise_key(did), 999)
        self.assertTrue((session.SESSION_STATE_DIR / "csid-abc.gone").is_file(),
                        "the marker must land where the reaper reads")
        ev = session._sidecar_evidence_by_csid("csid-abc")
        self.assertTrue(ev["has_gone"])

    def _give_it_real_work(self, did):
        """Put a non-stub body on the journal. Without this the reaper classifies a
        seconds-old session as a PHANTOM and deletes it — correct behaviour, and not the
        path under test here."""
        p = session.journal_path(did)
        fm, _ = session.parse_journal(p.read_text(encoding="utf-8"))
        p.write_text(session._rerender_journal(fm, "### What happened\n\n- real work\n"),
                     encoding="utf-8")

    def test_an_observed_exit_is_reaped_without_waiting_out_the_grace(self):
        """The whole point of the change: minutes, not two days."""
        did = self._prepped_and_adopted()
        self._give_it_real_work(did)
        session._supervise_beat(did)                      # a FRESH beat…
        session._write_gone_marker("csid-abc", 999)       # …then the process is seen to go
        reaped = session._reap_dead_journals(current_csid=None)
        self.assertEqual([(did, "closed")], [(s, d) for s, d in reaped if s == did])
        self.assertTrue(self._fm(session.journal_path(did))["ended"])

    def test_an_observed_exit_on_a_session_that_did_nothing_is_deleted_as_a_phantom(self):
        """The existing phantom rule still applies — an observed exit changes WHEN the
        reaper acts, never WHAT it decides the session was."""
        did = self._prepped_and_adopted()
        session._write_gone_marker("csid-abc", 999)
        reaped = session._reap_dead_journals(current_csid=None)
        self.assertEqual([(did, "phantom-deleted")], [(s, d) for s, d in reaped if s == did])
        self.assertFalse(session.journal_path(did).exists())

    def test_a_fresh_beat_after_an_observed_exit_still_reads_as_live(self):
        """A resumed session must not be reaped by a stale marker — the LATEST signal
        decides, which is the rule the clean-exit marker already followed."""
        did = self._prepped_and_adopted()
        session._write_gone_marker("csid-abc", 999)
        session._supervise_beat(did)                      # beat lands AFTER the marker
        reaped = session._reap_dead_journals(current_csid=None)
        self.assertNotIn(did, [sid for sid, _ in reaped])

    def test_silence_alone_still_waits_out_the_grace(self):
        """Unchanged behaviour for the ambiguous case — no watcher, just a quiet
        session, which may simply be idle-open."""
        did = self._prepped_and_adopted()
        session._supervise_beat(did)
        reaped = session._reap_dead_journals(current_csid=None)
        self.assertNotIn(did, [sid for sid, _ in reaped],
                         "a fresh beat with no death marker is a live session")


class CanonDeliveryTest(PrepAdoptionBase):
    """ADR-0082 D5, amended by WI-0324: delivery degrades embed -> point, never -> nothing."""

    def _ctx(self):
        return session.SESSION_STATE_DIR / session.SESSION_CONTEXT_NAME

    def test_prep_writes_the_payload_into_the_lane(self):
        self._start(prep=True, runtime="codex")
        self.assertTrue(self._ctx().is_file(),
                        "a hookless runtime has no injection channel — the file IS the delivery")

    def test_the_payload_carries_the_orientation(self):
        self._start(prep=True, runtime="codex")
        text = self._ctx().read_text(encoding="utf-8")
        self.assertIn("SESSION START", text)
        self.assertIn("Read this first", text)

    def test_the_payload_actually_carries_canon_and_the_standard_section(self):
        """The point of the whole mechanism. Asserted against FIXTURE markers, because
        the module's CANON_PATH is derived from ROOT at import — a fixture that did not
        override it would read the real repo's canon and this test would pass while
        proving nothing about delivery."""
        self._start(prep=True, runtime="codex")
        text = self._ctx().read_text(encoding="utf-8")
        self.assertIn("fixture-principle", text, "canon must reach a hookless runtime")
        self.assertIn("fixture-standard-marker", text,
                      "the standard session rituals must reach it too")

    def test_prep_prints_the_path_and_nothing_else_on_stdout(self):
        """stdout is a single machine-readable line so the wrapper can name the file
        without bash having to know where it lives (P16)."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._start(prep=True, runtime="codex")
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, f"expected exactly one stdout line, got {lines!r}")
        self.assertTrue(lines[0].endswith(session.SESSION_CONTEXT_NAME))
        self.assertTrue((self.repo / lines[0]).is_file(),
                        "the printed path must resolve from the lane root (the runtime's cwd)")

    def test_prep_does_not_emit_hook_json(self):
        """The old behaviour emitted JSON for a hook that does not exist on this path;
        a runtime told to read that file would find a serialized envelope, not a doc."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._start(prep=True, runtime="codex")
        self.assertNotIn("hookSpecificOutput", buf.getvalue())

    def test_an_ordinary_start_writes_no_context_file(self):
        """Where injection works, pointing is redundant — and a stale file left in a
        Claude lane would be a second, unmaintained copy of canon (P16)."""
        self._start(session_id="csid-abc")
        self.assertFalse(self._ctx().exists())

    def test_the_payload_carries_the_bounded_reading_rule(self):
        """WI-0324 / R4.2. The rule ships in the payload, not in STANDARD.md, because it
        is a property of runtimes whose shell output is capped — Claude's file tools
        bound themselves and would be told a rule that does not apply to them."""
        self._start(prep=True, runtime="codex")
        text = self._ctx().read_text(encoding="utf-8")
        self.assertIn("How to read large files here", text)
        self.assertIn("session.py show", text,
                      "a rule with no cheap way to obey it is a rule that gets ignored")
        self.assertIn("--bytes", text, "the rule must be bounded by BYTES, not lines")
        self.assertNotIn("332 KB", text,
                         "the brief's pre-WI-0284 size must not be restated as current")

    def test_the_payload_does_not_claim_it_could_not_be_delivered(self):
        """The old header told the agent the payload 'could not be injected into your
        context automatically'. Once the launcher embeds it, that sentence is false at
        exactly the moment the agent reads it — and a startup doc that misdescribes its
        own delivery teaches the agent to go looking for what it already has."""
        self._start(prep=True, runtime="codex")
        text = self._ctx().read_text(encoding="utf-8")
        self.assertNotIn("could not be injected", text)
        self.assertIn("do not need to read it twice", text)


class BoundedShowTest(unittest.TestCase):
    """WI-0324 / R4.2: `session.py show` — the harness half of the reading rule.

    Byte offsets throughout, because the files this exists for are the ones whose lines
    are long; a line range is not a bound on a 6,763-character line.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.doc = self.tmp / "doc.md"
        self.doc.write_text(
            "# Top\n\nintro\n\n"
            "## Alpha\n\naaaa\n\n"
            "### Alpha sub\n\nnested\n\n"
            "## Beta\n\nbbbb\n\n"
            "```\n## Not A Heading\n```\n\n"
            "## Gamma\n\ngggg\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spans(self):
        return session._md_section_spans(self.doc.read_bytes())

    def test_a_section_closes_at_the_same_or_shallower_heading(self):
        """The defect this guards: stopping at the NEXT heading of any level drops every
        subsection, so a parent reports the size of its own preamble. `## Alpha` must
        carry `### Alpha sub` and stop at `## Beta`."""
        spans = {s["heading"]: s for s in self._spans()}
        body = self.doc.read_bytes()[spans["Alpha"]["start"]:spans["Alpha"]["end"]]
        self.assertIn(b"### Alpha sub", body)
        self.assertIn(b"nested", body)
        self.assertNotIn(b"## Beta", body)

    def test_a_hash_inside_a_fence_is_not_a_heading(self):
        """A `#` at column zero inside a code fence is a comment. Treating it as a
        heading invents a section AND cuts the real one short at its start."""
        headings = [s["heading"] for s in self._spans()]
        self.assertNotIn("Not A Heading", headings)
        self.assertIn("Gamma", headings)

    def test_the_top_section_spans_the_whole_document(self):
        spans = {s["heading"]: s for s in self._spans()}
        self.assertEqual(spans["Top"]["start"], 0)
        self.assertEqual(spans["Top"]["end"], len(self.doc.read_bytes()))

    def test_an_ambiguous_section_is_refused_with_the_candidates_named(self):
        """Resolving to the first hit hands back a different section than was asked for,
        and the caller never learns it happened."""
        with self.assertRaises(ValueError) as cm:
            session.render_show(self.doc, section="alpha")
        self.assertIn("Alpha", str(cm.exception))
        self.assertIn("Alpha sub", str(cm.exception))

    def test_an_exact_heading_wins_over_a_containment_match(self):
        out = session.render_show(self.doc, section="Alpha")
        self.assertIn("nested", out)

    def test_a_missing_section_is_refused_not_approximated(self):
        with self.assertRaises(ValueError):
            session.render_show(self.doc, section="Delta")

    def test_a_backwards_byte_range_is_refused(self):
        with self.assertRaises(ValueError):
            session.render_show(self.doc, byte_range="99-3")

    def test_the_budget_names_the_offset_the_next_chunk_starts_at(self):
        """The whole point of the verb. An agent handed the next offset does not guess
        one, and two chunks that do not overlap cost what one truncated read plus its
        recovery reads cost today."""
        out = session.render_show(self.doc, byte_range="0-40", budget=10)
        self.assertIn("HELD BACK", out)
        self.assertIn("NEXT CHUNK STARTS AT 10", out)
        self.assertIn("--bytes 10-40", out)
        self.assertIn("do not re-read from 0", out)

    def test_chunks_taken_at_the_named_offset_do_not_overlap_and_lose_nothing(self):
        """Exercised in the configuration it creates: walk the file the way the rule
        tells an agent to, and the concatenation must equal the file exactly."""
        raw = self.doc.read_bytes()
        got, start, budget = b"", 0, 7
        while start < len(raw):
            out = session.render_show(self.doc, byte_range=f"{start}-{len(raw)}",
                                      budget=budget)
            body, _, foot = out.rpartition("\n\n--- ")
            got += raw[start:start + budget]
            if "HELD BACK" not in foot:
                break
            start += budget
        self.assertEqual(got, raw, "byte-bounded chunking must be lossless")

    def test_a_complete_view_says_so_rather_than_going_quiet(self):
        """'Couldn't tell' must never render the same as 'checked and it's fine'."""
        out = session.render_show(self.doc, budget=10_000)
        self.assertIn("This is the end of the file.", out)
        self.assertNotIn("HELD BACK", out)

    def test_the_section_index_reports_every_heading_with_its_range(self):
        out = session.render_show(self.doc, sections=True)
        for h in ("Top", "Alpha", "Alpha sub", "Beta", "Gamma"):
            self.assertIn(h, out)
        self.assertIn("sections", out)

    def test_a_directory_or_missing_path_is_refused(self):
        with self.assertRaises(ValueError):
            session.render_show(self.tmp)
        with self.assertRaises(ValueError):
            session.render_show(self.tmp / "nope.md")


class EmbedDeliveryWiringTest(unittest.TestCase):
    """WI-0324: the launcher embeds the payload rather than naming it.

    `poga` is bash and the suite cannot execute it, so the wiring is asserted textually —
    the same approach `PogaWiringTest` above takes.
    """

    def setUp(self):
        self.text = (ROOT / "poga").read_text(encoding="utf-8")
        i = self.text.index("deliver_the_context() {")
        self.fn = self.text[i:self.text.index("\n}\n", i)]

    def test_the_payload_itself_reaches_the_prompt(self):
        """Pointing costs a tool call whose output the runtime truncates with no marker
        the model can see; embedding costs no call and cannot be truncated."""
        self.assertIn("$(cat \"$POGA_SESSION_CONTEXT_ABS\")", self.fn)

    def test_the_absolute_path_is_resolved_where_the_lane_root_is_known(self):
        """`deliver_the_context` runs BEFORE the cd into the lane, so a lane-relative
        path would resolve against poga's cwd and read nothing."""
        self.assertIn('POGA_SESSION_CONTEXT_ABS="$lane_dir/$POGA_SESSION_CONTEXT"',
                      self.text)

    def test_an_oversized_payload_degrades_to_pointing_and_says_so(self):
        """A cap that fails silently just re-breaks on the next entry — the lesson
        PROFILE_INJECT_CAP was raised to record."""
        self.assertIn("POGA_CONTEXT_EMBED_CAP", self.fn)
        self.assertIn("over the ${POGA_CONTEXT_EMBED_CAP}B embed cap", self.fn)
        self.assertIn("pointing at it instead", self.fn)

    def test_an_unreadable_payload_degrades_rather_than_delivering_nothing(self):
        """Canon delivery degrades embed -> point, NEVER -> nothing: an Architect that
        never reads its principles is not bound by them."""
        self.assertIn("could not be read at", self.fn)

    def test_the_bash_cap_matches_the_python_one(self):
        """Two copies of a number is exactly the duplication P16 forbids, and this one
        is load-bearing: a bash cap above the python one would embed a payload the
        harness considers oversized."""
        m = re.search(r"^POGA_CONTEXT_EMBED_CAP=(\d+)$", self.text, re.M)
        self.assertIsNotNone(m, "the launcher must declare the cap it enforces")
        self.assertEqual(int(m.group(1)), session.SESSION_CONTEXT_EMBED_CAP)

    def test_the_pointer_fallback_names_the_bounded_reader(self):
        """When it must point, it must not point at `cat` — that is the read that
        started this."""
        self.assertIn("session.py show", self.fn)
        self.assertIn("--bytes 0-40000", self.fn)


# WI-0389's harness. `tests/test_preflight.py` owns the lift — one extractor, not two
# (P16) — and it exists because reading bash source was measurably not enough: the
# fail-open branch it pins read correctly and was unreachable under `set -e`.
from test_preflight import _extract_function  # noqa: E402


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PrepPayloadBoundaryTest(PrepAdoptionBase):
    """WI-0389 — prep's stdout is a CONTRACT, and the launcher now checks it.

    The defect, observed rather than reasoned about: a lane handed a worktree whose
    journal is still open takes `start --prep`'s ADR-0051 already-open early return,
    which prints a multi-line advisory block instead of the payload path. Nothing
    validated it, so `$lane_dir/<banner>` was composed and `deliver_the_context`
    interpolated the banner into the opening prompt — a Codex lane in poga-11 was told
    on 2026-09-18 to `Read === SESSION START (already open) ===` and then to run
    `session.py show === SESSION START (already open) === --bytes 0-40000`.

    RUN, not read. The functions are lifted out of `poga` and executed under the same
    `set -euo pipefail` poga sets, against a stub standing in for `$PY` whose stdout is
    the case under test. Textual assertions could not have caught this one: the code
    that composed the path read correctly and was handed the wrong shape.

    Every fixture value here is production-emitted. The banner comes from
    `session._emit_already_open`, which is the function that prints it; the good payload
    comes from a real `start --prep`. A guard proved against a value that cannot occur
    is a guard that has not been proved.
    """

    LANE_REL = ".session-state/session-context.md"

    def setUp(self):
        super().setUp()
        self.lane = self.tmp / "lane"
        (self.lane / ".session-state").mkdir(parents=True)
        # All three: the boundary sets the globals, prep_and_supervise calls it, and
        # delivery is where the harm landed. Extracting fewer would leave the failure
        # this class exists to pin outside the shell under test.
        self.fns = "\n".join(_extract_function(n) for n in
                             ("accept_context_path", "prep_and_supervise",
                              "deliver_the_context"))
        self.prep_stdout = self.tmp / "prep-stdout"
        self.prep_rc = self.tmp / "prep-rc"
        self.prep_rc.write_text("0\n", encoding="utf-8")
        # Stands in for `$PY`. argv is `<session.py> <verb> ...`, so the verb is $2.
        self.stub = self.tmp / "py-stub"
        self.stub.write_text(
            "#!/bin/sh\n"
            'case "$2" in\n'
            "  start)\n"
            "    cat " + shlex.quote(str(self.prep_stdout)) + "\n"
            '    exit "$(cat ' + shlex.quote(str(self.prep_rc)) + ')"\n'
            "    ;;\n"
            "  supervise) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8")
        self.stub.chmod(0o755)

    # --- fixtures, all production-emitted ------------------------------------

    def _real_banner(self):
        """The already-open block, from the function that prints it in production."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            session._emit_already_open({"session-id": "20260918T1200Z-devbox-4c11",
                                        "ordinal": "371"})
        text = buf.getvalue()
        self.assertIn("\n", text.strip(),
                      "the fixture must be the multi-line block this guard exists for")
        return text

    def _real_payload(self):
        """A genuine session-context payload, written by a real `start --prep`."""
        self._start(prep=True, runtime="codex")
        src = session.SESSION_STATE_DIR / session.SESSION_CONTEXT_NAME
        self.assertTrue(src.is_file(), "prep must have written the payload it prints")
        return src.read_text(encoding="utf-8")

    # --- the harness ---------------------------------------------------------

    def _feed(self, stdout, rc=0):
        """Run the real `prep_and_supervise` + `deliver_the_context` over `stdout`."""
        self.prep_stdout.write_text(stdout, encoding="utf-8")
        self.prep_rc.write_text(f"{rc}\n", encoding="utf-8")
        script = "\n".join([
            "set -euo pipefail",
            "PY=" + shlex.quote(str(self.stub)),
            f"POGA_CONTEXT_EMBED_CAP={session.SESSION_CONTEXT_EMBED_CAP}",
            "POGA_PROMPT_IS_OURS=1",
            'args=("OPERATOR-PROMPT-SENTINEL")',
            self.fns,
            "prep_and_supervise " + shlex.quote(str(self.lane)) + " codex",
            "deliver_the_context",
            r'printf "%s\n" "--CONTEXT-BEGIN--" "$POGA_SESSION_CONTEXT" "--CONTEXT-END--"',
            r'printf "%s\n" "--ABS-BEGIN--" "$POGA_SESSION_CONTEXT_ABS" "--ABS-END--"',
            r'printf "%s\n" "--INSTRUCTION-BEGIN--" "${args[0]}" "--INSTRUCTION-END--"',
        ])
        r = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        self.assertEqual(r.returncode, 0,
                         f"the boundary must degrade, never kill the launch: {r.stderr}")
        return (self._block(r.stdout, "CONTEXT"), self._block(r.stdout, "ABS"),
                self._block(r.stdout, "INSTRUCTION"), r.stderr)

    def _block(self, text, name):
        """Delimited, never `^NAME<(.*)>$`. The values under test are exactly the ones
        that span lines — that is the defect — so a line-anchored reader returns None
        precisely when the guard is missing, and the class then reports an
        `AttributeError` on a regex instead of the banner it was handed."""
        m = re.search(rf"^--{name}-BEGIN--\n(.*?)\n--{name}-END--$", text, re.M | re.S)
        self.assertIsNotNone(m, f"the harness printed no {name} block; stdout was {text!r}")
        return m.group(1)

    # --- the banner: refused, by the rule that actually applies ---------------

    def test_a_multi_line_block_is_refused_as_a_block(self):
        """Asserted on the SPECIFIC refusal, not merely that one fired. Two rules here
        reach the same outcome and one is a superset — a banner also names no readable
        file — so a bare "something was refused" stays green after the shape rule is
        deleted, which is exactly the rule with a live producer."""
        _, _, _, err = self._feed(self._real_banner())
        self.assertIn("prep printed a BLOCK where one path was expected", err)
        self.assertNotIn("is not readable", err,
                         "the block must be refused for its SHAPE; falling through to "
                         "the readability rule would report the wrong predicate and "
                         "make the shape rule untestable")

    def test_the_banner_never_reaches_the_instruction(self):
        """The harm itself. `deliver_the_context` interpolates its argument twice — once
        as `Read <path>` and once inside `session.py show <path> --bytes 0-40000` — so an
        unvalidated banner arrives in the model's first message as both."""
        _, _, instruction, _ = self._feed(self._real_banner())
        self.assertNotIn("SESSION START (already open)", instruction)
        self.assertNotIn("session.py show", instruction)
        self.assertEqual(instruction, "OPERATOR-PROMPT-SENTINEL",
                         "a refused payload leaves the prompt exactly as it was")

    def test_a_refused_block_composes_no_path_at_all(self):
        """Both globals stay empty. `deliver_the_context` returns on the first, so an
        ABS built from a banner is what every downstream reader would have to guard."""
        ctx, abs_, _, _ = self._feed(self._real_banner())
        self.assertEqual(ctx, "")
        self.assertEqual(abs_, "")

    # --- a single line naming nothing ----------------------------------------

    def test_a_path_naming_no_file_is_refused_and_named(self):
        """The second predicate. A pointer at a file that does not exist costs the agent
        a tool call and returns it nothing, which is worse than being told plainly."""
        ctx, abs_, instruction, err = self._feed(self.LANE_REL + "\n")
        self.assertIn(
            f"prep named a session context that is not readable: {self.LANE_REL}", err)
        self.assertNotIn("printed a BLOCK", err,
                         "a single line is not the block case; the two refusals must "
                         "stay distinguishable or neither can be asserted on")
        self.assertEqual(ctx, "")
        self.assertEqual(abs_, "")
        self.assertEqual(instruction, "OPERATOR-PROMPT-SENTINEL")

    # --- correct input: the guard must not fire ------------------------------

    def test_a_good_path_renders_the_instruction_unchanged(self):
        """A guard that fires on correct code gets deleted. This is the whole normal
        path, end to end: the payload embeds, the closing note names the file, and the
        operator's own prompt is still at the bottom."""
        payload = self._real_payload()
        (self.lane / self.LANE_REL).write_text(payload, encoding="utf-8")
        ctx, abs_, instruction, err = self._feed(self.LANE_REL + "\n")

        self.assertEqual(ctx, self.LANE_REL)
        self.assertEqual(abs_, f"{self.lane}/{self.LANE_REL}",
                         "the lane-relative path must resolve against the LANE root — "
                         "`deliver_the_context` runs before the cd, so resolving it "
                         "against poga's own cwd would read nothing")
        self.assertIn("fixture-principle", instruction,
                      "canon must reach a hookless runtime in the opening prompt")
        self.assertIn("--- end of session-start context ---", instruction)
        self.assertIn(f"The same bytes are on disk at {self.LANE_REL}", instruction)
        self.assertTrue(instruction.endswith("OPERATOR-PROMPT-SENTINEL"))
        self.assertNotIn("poga: NOTE", err, f"the guard fired on correct input: {err}")

    # --- prep failing outright is untouched ----------------------------------

    def test_a_failed_prep_still_degrades_quietly(self):
        """Pre-existing behaviour, pinned because the new boundary sits on the same
        path: prep exiting nonzero already reports itself, and a second note from the
        boundary about an empty value would be noise on a failure that is not this
        function's to explain."""
        ctx, _, instruction, err = self._feed("", rc=1)
        self.assertIn("prep failed", err)
        self.assertNotIn("poga: NOTE", err)
        self.assertEqual(ctx, "")
        self.assertEqual(instruction, "OPERATOR-PROMPT-SENTINEL")

    # --- the producer, end to end --------------------------------------------

    def test_the_real_already_open_prep_cannot_reach_the_instruction(self):
        """The observed case, with NOTHING synthesised: run `start --prep` twice against
        one fixture lane — the second takes the already-open return — and feed its actual
        stdout through the boundary.

        Asserted on the OUTCOME rather than on prep still misbehaving. The producing end
        (`cmd_start` ignoring `--prep` on that early return) belongs to a concurrent item
        on `sessionlib/hooks.py`; when it lands, prep prints a path here and this test
        keeps passing, because what it pins is that no banner reaches the prompt either
        way — not that the banner is still being printed.
        """
        import io
        from contextlib import redirect_stdout
        self._start(prep=True, runtime="codex")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._start(prep=True, runtime="codex")
        _, _, instruction, _ = self._feed(buf.getvalue())
        self.assertNotIn("SESSION START (already open)", instruction)
        self.assertNotIn("do NOT re-run", instruction)


class PogaWiringTest(unittest.TestCase):
    """`poga` is bash and the suite cannot execute it, so assert the wiring textually —
    the same approach test_resident_runtime_lane takes for the pad path."""

    def setUp(self):
        self.text = (ROOT / "poga").read_text(encoding="utf-8")

    def test_prep_and_supervise_is_called_on_the_paths_poga_owns(self):
        self.assertEqual(self.text.count("prep_and_supervise "), 2,
                         "both poga-created-worktree paths must prep and supervise")

    def test_the_native_worktree_path_is_left_alone(self):
        """Inverting Claude moves hook wiring out of every member's settings.json, which
        ADR-0082's Breaking note says ships as ONE standard bump with WI-0055/WI-0017."""
        native = self.text.split('if [ -n "$rt_wt" ]; then', 1)[1].split("fi", 1)[0]
        self.assertNotIn("prep_and_supervise", native)

    def test_the_context_is_named_on_both_hookless_paths(self):
        """D5's delivery half must fire wherever prep fires, or the payload is written
        and never read."""
        self.assertEqual(self.text.count("prep_and_supervise "),
                         self.text.count("\n    deliver_the_context") +
                         self.text.count("\n  deliver_the_context"),
                         "every prep site must also deliver the context")

    def test_the_context_instruction_is_only_applied_to_our_own_prompt(self):
        """Rewriting an operator's prompt means guessing which argv element it is;
        guessing wrong corrupts a real command line."""
        self.assertIn('POGA_PROMPT_IS_OURS" = "1"', self.text)

    def test_the_watcher_watches_pogas_own_pid(self):
        """exec keeps the pid, so $$ IS the agent after launch. Watching anything else
        would need bookkeeping that this design exists to avoid."""
        self.assertIn("supervise --pid $$ --detach", self.text)

    def test_the_bounded_reader_is_reachable_under_the_name_the_brief_uses(self):
        """R4 asks for the extractor as `poga show <file> --section <heading>`.

        `session.py show` shipped and the launcher verb did not, and the gap is not
        cosmetic: the reading rule written into every session context sends the agent
        here. Typing it got `'show' is not a poga verb` AFTER the stale-substrate
        recovery had fired, because `show` really does exist at origin in `session.py`,
        so a missing verb was diagnosed as a behind checkout and cost a refresh of the
        shared tree before refusing anyway.

        Asserted on the dispatch table rather than by running bash, like every test in
        this class. The verb list users see is DERIVED from this table, so routing it
        here is also what makes it appear in `--help` and in the unknown-verb refusal.
        """
        self.assertIn("    show)", self.text, "`show` must be routed in poga's verb table")
        self.assertIn("cmd_show()", self.text)

    def test_the_bounded_reader_reads_the_tree_the_caller_is_standing_in(self):
        """The one thing that could be wired wrong here and still look right.

        `work`, `ops` and `decisions` deliberately anchor on the MAIN checkout, because
        they read SHARED state a lane's copy is stale about. `show` reads a file the
        caller named, and `render_show` resolves a relative path against the root of the
        session.py it runs. Anchored the same way, `poga show federation-arch.md` from a
        lane would print the trunk's copy of a file the caller is editing -- a stale read
        delivered by the machinery for avoiding stale reads, and silent, because the
        output looks exactly like the answer.
        """
        body = self.text.split("cmd_show() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('harness_exec "$(git rev-parse --show-toplevel)/session.py" show',
                      body)
        self.assertNotIn('"$ROOT/session.py" show', body,
                         "anchoring show on the main checkout would hide the caller's "
                         "own edits behind the trunk's copy")


class RerenderTest(unittest.TestCase):
    def test_rerender_preserves_body_and_extra_keys(self):
        """Adoption and close share this renderer precisely so their field order cannot
        drift apart (P16); both paths must keep keys the journal grew."""
        meta = {"session-id": "x", "ordinal": "1", "title": "t", "machine": "Runner",
                "runtime": "codex", "started": "s", "ended": ""}
        text = session.render_journal(meta, body="### What happened\n\n- a thing\n")
        fm, body = session.parse_journal(text)
        fm["close-confirm"] = "yes"
        out = session._rerender_journal(fm, body)
        fm2, body2 = session.parse_journal(out)
        self.assertEqual(fm2["close-confirm"], "yes")
        self.assertEqual(fm2["runtime"], "codex")
        self.assertIn("- a thing", body2)


class LaneHandoverTest(PrepAdoptionBase):
    """WI-0391 — a `--prep` start launched into a lane holding ANOTHER session's open
    journal.

    `_my_open_journal` keys on the claude-session-id; a `--prep` run has none, so it takes
    its documented fallback ("an open journal on this machine started today") and returns a
    journal that belongs to somebody else. The harness then adopted it AND took the
    idempotent early return, which fires before the session-context payload is composed. So
    the launched lane wrote its narrative into a stranger's journal and received no canon,
    exit 0, silently. Both were observed live on 2026-09-18 across every `--prep` launch on
    a machine carrying any open journal from that day — five of them on main.

    the operator's ruling: close the foreign journal as dropped, with a pointer, and open our own.
    """

    def _foreign_open_journal(self, sid="20260101T0000Z-other-9999", title="(in progress)"):
        """An open journal belonging to a DIFFERENT session — same machine, today, which
        is the whole of what the fallback matches on."""
        session.write_start_journal(sid, {
            "session-id": sid, "ordinal": "1", "title": title,
            "machine": session.detect_machine(), "runtime": "claude-code",
            "role-doc-version": "v1.0.0", "base-commit": "deadbeef",
            "started": session._now_iso(), "ended": "",
            "claude-session-id": "csid-someone-else",
        })
        p = session.journal_path(sid)
        p.write_text(p.read_text(encoding="utf-8").rstrip("\n")
                     + "\n\n### What happened\n\n- the other session's own narrative\n",
                     encoding="utf-8")
        return p

    def _prep_stdout(self, runtime="codex"):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._start(prep=True, runtime=runtime)
        return buf.getvalue()

    # --- the headline regression --------------------------------------------
    def test_prep_writes_the_payload_even_with_a_foreign_open_journal(self):
        """THE defect. Against the pre-fix file this fails: the early return fires before
        the payload is composed, `session-context.md` is never written, and stdout carries
        a multi-line advisory banner instead of a path — which is what `prep_and_supervise`
        then used as a FILENAME."""
        self._foreign_open_journal()
        out = self._prep_stdout()
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1,
                         f"prep must print exactly one line (its payload path); got {out!r}")
        payload = session.ROOT / lines[0]
        self.assertTrue(payload.is_file(), f"no payload written at {payload}")
        self.assertIn("fixture-principle", payload.read_text(encoding="utf-8"),
                      "the payload must actually carry canon, not merely exist")

    def test_the_lane_gets_its_own_journal_not_the_strangers(self):
        foreign = self._foreign_open_journal()
        self._prep_stdout()
        js = self._journals()
        self.assertEqual(len(js), 2, "the foreign journal is kept, ours is added")
        ours = [p for p in js if p != foreign]
        self.assertEqual(len(ours), 1)
        self.assertEqual(self._fm(ours[0]).get("runtime"), "codex")
        self.assertEqual(self._fm(ours[0]).get("ended", ""), "",
                         "our own journal must be open for us to write in")

    def test_the_foreign_journal_is_closed_as_dropped_with_a_pointer(self):
        foreign = self._foreign_open_journal()
        self._prep_stdout()
        fm = self._fm(foreign)
        self.assertTrue(fm.get("ended"), "the foreign journal must be closed")
        self.assertIn("lane-reassigned", session.MACHINE_CLOSE_REASONS)
        self.assertIn(session.MACHINE_CLOSE_REASONS["lane-reassigned"],
                      fm.get("closed-by", ""),
                      "a machine close must say a machine did it, and on what evidence")
        ours = [p for p in self._journals() if p != foreign]
        self.assertEqual(fm.get("superseded-by"), self._fm(ours[0])["session-id"],
                         "the pointer is what makes the dropped record readable")

    def test_the_foreign_journals_narrative_is_left_verbatim(self):
        """The close touches frontmatter only. The behaviour it replaces APPENDED a second
        session's narrative to this body, so 'is closing it too destructive' is measured
        against that, not against leaving the file alone."""
        foreign = self._foreign_open_journal()
        self._prep_stdout()
        self.assertIn("- the other session's own narrative",
                      foreign.read_text(encoding="utf-8"))

    def test_an_authored_title_is_not_clobbered_by_the_close(self):
        """Same rule the reaper follows (WI-0137): a session that titled its own journal
        said something no machine can improve on."""
        foreign = self._foreign_open_journal(title="A real title the session wrote")
        self._prep_stdout()
        self.assertEqual(self._fm(foreign)["title"], "A real title the session wrote")

    def test_a_stub_title_becomes_the_named_death_class(self):
        foreign = self._foreign_open_journal()
        self._prep_stdout()
        self.assertEqual(self._fm(foreign)["title"],
                         session.machine_close_title("lane-reassigned"))

    def test_an_already_closed_journal_is_not_re_closed(self):
        """`_supersede_journal` re-reads and re-checks `ended` because the reap and the
        janitor run between the scan and the close. Re-closing would move the `ended` stamp
        forward onto this clock — a fabricated end time for someone else's session."""
        foreign = self._foreign_open_journal()
        text = foreign.read_text(encoding="utf-8")
        foreign.write_text(session.finalize_journal(
            text, "2026-01-01T00:00:00+00:00", "5m", closed_by="reap (test)", path=foreign),
            encoding="utf-8")
        self._prep_stdout()
        self.assertEqual(self._fm(foreign)["ended"], "2026-01-01T00:00:00+00:00")
        self.assertNotIn("superseded-by", self._fm(foreign))

    # --- what must NOT change ------------------------------------------------
    def test_a_hand_rerun_without_prep_still_takes_the_idempotent_early_return(self):
        """The fallback's REAL job (ADR-0051 C1/C2): the same session run twice by hand,
        which also has no session id. Gating on `--prep` and not on `session_id is None` is
        the whole reason that case survives."""
        self._start(prep=False)
        before = self._journals()
        self.assertEqual(len(before), 1)
        self._start(prep=False)
        self.assertEqual(self._journals(), before,
                         "a hand re-run must not open — or close — anything")
        self.assertEqual(self._fm(before[0]).get("ended", ""), "")

    def test_a_hook_start_with_its_own_open_journal_is_untouched(self):
        """The hook path identifies the journal by SESSION ID, so there the early return is
        correct idempotency and must stay. Reached through the prep→adopt handshake because
        that is the only route to a materialized journal bound to a runtime session — a
        bare hook start is lazy (ADR-0055) and has no journal yet."""
        self._start(prep=True, runtime="codex")
        self._start(session_id="csid-abc")            # adopts the prepped journal
        js = self._journals()
        self.assertEqual(len(js), 1)
        self.assertEqual(self._fm(js[0])["claude-session-id"], "csid-abc")
        self._start(session_id="csid-abc")            # the idempotent re-run: early return
        self.assertEqual(self._journals(), js)
        self.assertEqual(self._fm(js[0]).get("ended", ""), "",
                         "our own open journal must never be superseded")


class PrepContractTest(PrepAdoptionBase):
    """WI-0391's second half: `cmd_start`'s early returns CONSULT `--prep` either way, and
    a prep run never exits 0 having written nothing."""

    def test_the_wrapper_refuses_a_prep_run_that_wrote_no_payload(self):
        """The structural guard, tested as a guard: it must catch an early return that does
        not exist yet. Stubbing the body out is exactly that case — a `return` reached
        before the payload write, which is the defect's whole shape."""
        with mock.patch.object(session, "_cmd_start", lambda a: None):
            with self.assertRaises(SystemExit) as cm:
                self._start(prep=True, runtime="codex")
        self.assertIn("WI-0391", str(cm.exception))

    def test_the_wrapper_is_silent_on_a_prep_run_that_did_write(self):
        self._prep_ok = self._start(prep=True, runtime="codex")   # must not raise

    def test_a_non_prep_start_is_not_subject_to_the_contract(self):
        with mock.patch.object(session, "_cmd_start", lambda a: None):
            self._start(prep=False)          # must not raise

    def test_early_return_one_refuses_under_prep_instead_of_returning_zero(self):
        """Reachable only if a `--prep` run ever carries a session id. Built anyway: the
        ruling closes the door, not the one hole found behind it."""
        self._start(session_id="csid-abc")
        args = argparse.Namespace(dry_run=False, prep=True, runtime="codex")
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"session_id": "csid-abc"}):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_start(args)
        self.assertIn("WI-0391", str(cm.exception))

    def test_early_return_two_refuses_under_prep_instead_of_returning_zero(self):
        """The ADR-0055 lazy-start branch — the second early return, same hole."""
        session._sidecar_write("csid-lazy", "pending-start", {
            "session_id": "20260101T0000Z-x-0001", "ordinal": 1,
            "machine": session.detect_machine(), "started": session._now_iso(),
        })
        self.assertTrue(session._pending_marker("csid-lazy").is_file())
        args = argparse.Namespace(dry_run=False, prep=True, runtime="codex")
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"session_id": "csid-lazy"}):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_start(args)
        self.assertIn("WI-0391", str(cm.exception))

    def test_the_wrapper_stays_transparent_to_source_reading_guards(self):
        """Three guards elsewhere assert the start's wiring with
        `inspect.getsource(cmd_start)`. Wrapping the function without `__wrapped__` makes
        all three read a wrapper that mentions none of that wiring and pass anyway — a
        guard that still runs while no longer looking. Pinned here so the next person to
        wrap this function finds out from a failure rather than from the three guards
        quietly agreeing with everything."""
        import inspect
        src = inspect.getsource(session.cmd_start)
        for wiring in ("landing_line()", "trunk_suite_line()", "_board_live()"):
            self.assertIn(wiring, src,
                          "getsource(cmd_start) must reach the body that does the work")

    def test_early_return_two_still_returns_quietly_without_prep(self):
        session._sidecar_write("csid-lazy", "pending-start", {
            "session_id": "20260101T0000Z-x-0001", "ordinal": 1,
            "machine": session.detect_machine(), "started": session._now_iso(),
        })
        args = argparse.Namespace(dry_run=False, prep=False, runtime=None)
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"session_id": "csid-lazy"}):
            session.cmd_start(args)          # must not raise
        self.assertEqual(self._journals(), [], "the lazy branch allocates nothing")


if __name__ == "__main__":
    unittest.main()
