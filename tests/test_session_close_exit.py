"""WI-0399 — a closed session exits its own runtime, and its summary is read off the board.

TWO HALVES OF ONE FACT, folded into one item on the operator's direction (2026-09-19), and the
tests are split along the same seam.

  1. **A closed session used to sit at its runtime's exit prompt** until operator closed the
     terminal by hand. `lane-exit` (WI-0249) already knew how to END a runtime; what it
     could not do was NAME one outside a dispatch, because its only handle was
     `_dispatch_runtime_pid`, which matches `POGA_DISPATCH=<id>` in an ancestor's command
     line. A session a person opened carries no such variable. The wrapper has the handle
     and always did — `exec` keeps the pid — so it writes it down, and the close reads it
     back.

  2. **The close summary is read off the board, not the terminal.** A summary printed
     to a window that then closes itself has been shown to nobody. The close publishes one
     coordination record, exactly as `attention` already publishes "this lane needs you",
     and the board renders it.

WHAT THESE TESTS ARE REALLY GUARDING is the authorization, not the plumbing. A launch
receipt is a number in a file, and the failure mode that matters is signalling a process
that number no longer refers to — a stale receipt, a relaunched slot, a recycled pid. The
answer is `_pid_is_ancestor`: not "does this file say so" but "am I running inside it".
Every refusal case below is one way of getting that wrong.

THE DANGER A FIXTURE HERE CARRIES is named in `tests/test_dispatch_fixture_guard.py`: the
process this suite would otherwise signal is the one running it. Every case that reaches
the reaper mocks `sh` or `os.kill`, and every case neutralises the dispatch environment
first.
"""

import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402

GIT = shutil.which("git")

SID = "20260919T1200Z-devbox-ca15"
CSID = f"csid-{SID}"

JOURNAL = """---
session-id: {sid}
claude-session-id: {csid}
title: a title
machine: devbox
started: 2026-09-19T12:00:00+00:00
ended: {ended}
---

### What happened

- built the thing

### State at close

- WI-0399 built, tested and landed.
- The panel itself ships separately.

#### Caveat

- the notification is a record, not a push.

### Parked question

- nothing parked
"""


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True,
                   text=True)


# ══════════════════════════════════════════════════════════════════════════════════
# A. Reading the summary out of the journal
# ══════════════════════════════════════════════════════════════════════════════════

class CloseSummaryReadTest(unittest.TestCase):
    """`journal_section` / `close_summary` / `close_summary_line` — pure, no fixture."""

    def setUp(self):
        self.text = JOURNAL.format(sid=SID, csid=CSID, ended="2026-09-19T12:00:00+00:00")

    def test_the_section_is_returned(self):
        self.assertIn("WI-0399 built, tested and landed.",
                      session.close_summary(self.text))

    def test_a_deeper_subsection_is_kept(self):
        """THE REGRESSION THIS FUNCTION EXISTS TO NOT HAVE. A section scanner that stops
        at the next heading of ANY level silently drops its own subsections and reports
        the truncation as a complete read — there is no error and no short-read signal,
        so the summary on the board would simply be missing its last paragraph forever.
        Same-or-shallower is the rule; `#### Caveat` is deeper, so it belongs inside."""
        self.assertIn("the notification is a record", session.close_summary(self.text))

    def test_it_stops_at_the_next_section_of_the_same_level(self):
        self.assertNotIn("nothing parked", session.close_summary(self.text))
        self.assertNotIn("built the thing", session.close_summary(self.text))

    def test_an_absent_section_is_empty_and_not_an_error(self):
        """Empty is a real answer: a session that wrote nothing there leaves nothing to
        show, and the honest failure is a missing line rather than an invented one."""
        bare = "---\nsession-id: x\n---\n\n### What happened\n\n- x\n"
        self.assertEqual(session.close_summary(bare), "")
        self.assertEqual(session.close_summary_line(bare), "")

    def test_unparseable_input_is_empty_and_not_an_error(self):
        self.assertEqual(session.close_summary("not a journal at all"), "")
        self.assertEqual(session.close_summary_line(""), "")

    def test_the_one_line_form_drops_the_bullet(self):
        """The section is authored as bullets; a `- ` in front of a one-line alert is
        noise from a format the alert does not share."""
        self.assertEqual(session.close_summary_line(self.text),
                         "WI-0399 built, tested and landed.")

    def test_the_one_line_form_is_capped(self):
        long = JOURNAL.format(sid=SID, csid=CSID, ended="x").replace(
            "- WI-0399 built, tested and landed.", "- " + ("y" * 500))
        self.assertEqual(len(session.close_summary_line(long)),
                         session.CLOSE_SUMMARY_LINE_CAP)

    def test_the_heading_match_is_level_tolerant(self):
        """A member whose journal template uses `##` would otherwise contribute nothing
        forever, with nothing anywhere erroring."""
        two = "---\na: b\n---\n\n## State at close\n\n- shipped\n\n## Next\n\n- no\n"
        self.assertEqual(session.close_summary(two), "- shipped")

    def test_the_outcome_reader_is_unchanged_by_the_refactor(self):
        """`_journal_outcome` now delegates to `journal_section`. Its behaviour is what
        `ROADMAP.md`'s compiled region depends on, so the delegation is pinned here
        rather than assumed."""
        body = "## Outcome\n\nshipped the thing.\n\n## Next\n\nnope\n"
        self.assertEqual(session._journal_outcome(body), "shipped the thing.")
        self.assertEqual(session._journal_outcome("### What happened\n\n- x\n"), "")


# ══════════════════════════════════════════════════════════════════════════════════
# The lane fixture the rest of the module shares
# ══════════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(GIT, "git not available")
class LaneBase(unittest.TestCase):
    """One repo, one lane, one session — the shape `test_lane_close_after_land` uses,
    because the coordination store these assertions read has to be the real one for the
    read to mean anything."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "f.txt").write_text("x\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        self.journals = self.lane / "sessions" / "journal"
        self.journals.mkdir(parents=True)
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "JOURNAL_DIR")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.journals

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _journal(self, ended="2026-09-19T12:00:00+00:00", sid=SID, csid=CSID):
        p = self.journals / f"{sid}.md"
        p.write_text(JOURNAL.format(sid=sid, csid=csid, ended=ended), encoding="utf-8")
        return p

    def _as_this_session(self, csid=CSID):
        p = mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": csid})
        p.start()
        self.addCleanup(p.stop)

    def _off_the_gate(self):
        """`_under_gate()` is inherited by every child of the suite the land gate runs,
        and under it the close returns its cannot-tell answer by design (ADR-0119 D4) —
        so a case asserting what the close DOES would assert the gate's refusal instead,
        pass in a lane and fail at the gate. Every case that calls this also mocks the
        reach, so nothing here can touch the operator's machine with the guard off."""
        p = mock.patch.object(session, "_under_gate", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def _launch(self, pid, lane="poga-1", attended=True, runtime="claude-code"):
        args = type("A", (), {"lane": lane, "pid": pid, "runtime": runtime,
                              "attended": attended})()
        with self.assertRaises(SystemExit):
            session.cmd_lane_launch(args)


# ══════════════════════════════════════════════════════════════════════════════════
# B. The wrapper's launch receipt
# ══════════════════════════════════════════════════════════════════════════════════

class LaunchRecordTest(LaneBase):
    def test_it_round_trips(self):
        self._launch(pid=4321, runtime="codex")
        rec = session._lane_launch_record()
        self.assertEqual(rec["pid"], 4321)
        self.assertEqual(rec["lane"], "poga-1")
        self.assertEqual(rec["runtime"], "codex")
        self.assertTrue(rec["attended"])
        self.assertTrue(rec["launched"])

    def test_the_runtime_is_recorded_and_nothing_branches_on_it(self):
        """RUNTIME-AGNOSTIC BY CONSTRUCTION (ADR-0041). The field exists so a close can
        SAY what it ended; a codex receipt behaves identically to a claude one, which is
        the property that makes this not a Claude-only exit path."""
        self._launch(pid=4321, runtime="antigravity")
        self.assertEqual(session._lane_launch_record()["runtime"], "antigravity")

    def test_one_record_per_slot_and_a_relaunch_overwrites(self):
        """The question is 'what is running in poga-1 RIGHT NOW'. A fresh name per launch
        would leave the close hunting through predecessors with no way to tell which is
        current — and `poga resume` into the same slot is the ordinary case."""
        self._launch(pid=1111)
        self._launch(pid=2222)
        self.assertEqual(session._lane_launch_record()["pid"], 2222)

    def test_a_nameless_or_impossible_launch_writes_nothing(self):
        self._launch(pid=0)
        self.assertEqual(session._lane_launch_record(), {})
        self._launch(pid=1)          # pid 1 is never a runtime we launched
        self.assertEqual(session._lane_launch_record(), {})
        self._launch(pid=99, lane="")
        self.assertEqual(session._lane_launch_record(), {})

    def test_no_receipt_reads_as_empty_not_as_an_error(self):
        """Every session `poga` did not launch — an app session, a lane entered by hand,
        this suite — and every caller treats {} as 'no handle, leave it alone'."""
        self.assertEqual(session._lane_launch_record(), {})


# ══════════════════════════════════════════════════════════════════════════════════
# C. The authorization
# ══════════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(session._under_gate(),
                 "reads the real process table; under the gate `_pid_is_ancestor` "
                 "refuses by design (ADR-0119 D4) and there is nothing to measure")
class AncestryProofTest(unittest.TestCase):
    """`_pid_is_ancestor` — the fact that replaces `POGA_DISPATCH` for a hand-opened lane.

    Run against the REAL process tree, because that is the only thing that establishes it
    reads ancestry rather than reading a file back to itself. That is also exactly why the
    whole class stands down under the land gate: reading the machine there would make the
    land's verdict depend on what else the operator happens to be running, which is the
    property `test_gate_snapshot.CompletenessTest` exists to hold. The gate's own coverage
    of this function is the refusal itself, pinned below."""

    def test_our_own_parent_is_an_ancestor(self):
        self.assertTrue(session._pid_is_ancestor(os.getppid()))

    def test_we_are_not_our_own_ancestor(self):
        """Killing the process doing the killing loses the close it is in the middle of."""
        self.assertFalse(session._pid_is_ancestor(os.getpid()))

    def test_init_is_refused(self):
        self.assertFalse(session._pid_is_ancestor(1))
        self.assertFalse(session._pid_is_ancestor(0))
        self.assertFalse(session._pid_is_ancestor(-5))

    def test_a_pid_we_do_not_descend_from_is_refused(self):
        """THE RECYCLED-PID CASE, which is the whole reason the file is not enough. A
        real, live, signalable process that this session is simply not running inside."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(session._process_alive(proc.pid))
            self.assertFalse(session._pid_is_ancestor(proc.pid))
        finally:
            proc.kill()
            proc.wait()

    def test_unreadable_ancestry_refuses(self):
        with mock.patch.object(session, "sh",
                               return_value=type("R", (), {"stdout": ""})()):
            self.assertFalse(session._pid_is_ancestor(os.getppid()))


class AncestryUnderTheGateTest(unittest.TestCase):
    """The one ancestry case that must run EVERYWHERE, including at the gate.

    `_pid_is_ancestor` spawns `ps`. Under the gate the suite is running inside somebody's
    real runtime, so a True here would identify a live session as killable and the land's
    verdict would become a function of the operator's machine (ADR-0119 D4). This is the
    guard the completeness detector demanded; it is pinned here so it cannot be quietly
    removed to make a different test pass."""

    def test_it_refuses_under_the_gate_without_reading_the_machine(self):
        with mock.patch.object(session, "_under_gate", return_value=True), \
                mock.patch.object(session, "sh") as spawned:
            self.assertFalse(session._pid_is_ancestor(os.getppid()))
        spawned.assert_not_called()


class AttendedCloseTest(LaneBase):
    """`_attended_close_runtime` — three facts must hold, and each refusal is one way of
    killing the wrong thing."""

    def setUp(self):
        super().setUp()
        self._off_the_gate()
        self._journal()
        self._as_this_session()
        self.spawned = mock.patch.object(session, "_lane_exit_spawn").start()
        # ANCESTRY IS STUBBED HERE ON PURPOSE, and it is not a weakening of these cases.
        # What they are about is the close's DECISION — which facts it requires and which
        # refusals it makes — and `AncestryProofTest` owns whether the ancestry read is
        # itself correct, against the real process tree. Stubbing it keeps every case
        # below deterministic and stops this module reading the operator's machine at the
        # land gate, where `ps` is forbidden (ADR-0119 D4).
        self.ancestor = os.getppid()
        mock.patch.object(session, "_pid_is_ancestor",
                          side_effect=lambda pid: pid == self.ancestor).start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, landed=True, did=SID):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session._attended_close_runtime(landed, did)
        return buf.getvalue()

    # ── it acts ──────────────────────────────────────────────────────────────────
    def test_it_ends_the_runtime_it_is_running_inside(self):
        self._launch(pid=self.ancestor, attended=True)
        out = self._run()
        self.spawned.assert_called_once()
        pid, tmux, pause = self.spawned.call_args[0]
        self.assertEqual(pid, self.ancestor)
        self.assertEqual(pause, session.CLOSE_READ_PAUSE_SECONDS)
        self.assertIn("closes itself", out)

    def test_a_detached_pane_gets_no_pause(self):
        """Holding a pane open for a reader who does not exist is what WI-0249 removed."""
        self._launch(pid=self.ancestor, attended=False)
        self._run()
        self.assertEqual(self.spawned.call_args[0][2], 0.0)

    # ── it refuses ───────────────────────────────────────────────────────────────
    def test_no_receipt_no_close(self):
        self._run()
        self.spawned.assert_not_called()

    def test_a_receipt_naming_a_process_we_do_not_descend_from_is_refused(self):
        """THE CASE THIS AUTHORIZATION EXISTS FOR. A stale receipt, a relaunched slot, or
        a pid the OS has recycled: alive, signalable, and NOT ours. The file says go; the
        ancestry says no; no is the answer.

        The decoy is a real live process, so `_process_alive` passes on its own merits and
        the refusal can only be coming from the ancestry arm."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(session._process_alive(proc.pid))
            self._launch(pid=proc.pid)
            self._run()
            self.spawned.assert_not_called()
        finally:
            proc.kill()
            proc.wait()

    def test_a_dead_pid_is_refused(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self._launch(pid=proc.pid)
        self._run()
        self.spawned.assert_not_called()

    def test_a_dispatched_lane_is_left_to_the_other_arm(self):
        """Two arms must never both fire: `_dispatch_close_runtime` has already run by the
        time this is called, and a second reaper on the same pid is a double signal."""
        self._launch(pid=self.ancestor)
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-abc123"}):
            self._run()
        self.spawned.assert_not_called()

    def test_closing_a_siblings_journal_does_not_kill_this_runtime(self):
        """`end --session-id <other>` closes a NEIGHBOUR's record. Without this check,
        doing so from here would destroy a live session mid-work."""
        self._launch(pid=self.ancestor)
        self._run(did="20260918T0000Z-devbox-ffff")
        self.spawned.assert_not_called()

    def test_a_lane_that_did_not_land_stays_up_and_says_why(self):
        """Its work is committed on the lane and needs a session alive to finish it.
        Killing it converts a recoverable failure into stranded work with no owner."""
        self._launch(pid=self.ancestor)
        out = self._run(landed=False)
        self.spawned.assert_not_called()
        self.assertIn("did NOT land", out)
        self.assertIn("merge", out)

    def test_it_refuses_under_the_gate(self):
        """ADR-0119 D4 — the suite runs inside somebody's real runtime."""
        self._launch(pid=self.ancestor)
        with mock.patch.object(session, "_under_gate", return_value=True):
            self._run()
        self.spawned.assert_not_called()

    def test_it_refuses_against_a_fixture(self):
        self._launch(pid=self.ancestor)
        with mock.patch.object(session, "_running_against_fixture", return_value=True):
            self._run()
        self.spawned.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════════
# D. The reaper's readable pause
# ══════════════════════════════════════════════════════════════════════════════════

class ReadPauseTest(LaneBase):
    def setUp(self):
        super().setUp()
        self._off_the_gate()

    def _run_exit(self, **kw):
        args = type("A", (), {"pid": 0, "tmux_session": "", "grace": 0.05,
                              "detach": False, "read_pause": 0.0})()
        for k, v in kw.items():
            setattr(args, k, v)
        with self.assertRaises(SystemExit):
            session.cmd_lane_exit(args)

    def test_the_pause_happens_before_the_signal(self):
        """A pause AFTER the signal is a pause on a dead window. The order is what makes
        the summary readable at all."""
        order = []
        with mock.patch.object(session.time, "sleep",
                               side_effect=lambda s: order.append(("slept", s))), \
                mock.patch.object(session.os, "kill",
                                  side_effect=lambda *a: order.append(("killed",))), \
                mock.patch.object(session, "_process_alive", return_value=True), \
                mock.patch.object(session, "_lane_exit_kill_tmux"):
            self._run_exit(pid=os.getppid(), read_pause=4.0)
        self.assertEqual(order[0], ("slept", 4.0))
        self.assertIn(("killed",), order)
        self.assertLess(order.index(("slept", 4.0)), order.index(("killed",)))

    def test_a_process_that_left_during_the_pause_is_not_signalled(self):
        """A runtime can exit on its own while the summary is being read. Signalling then
        would be signalling whatever inherited the number."""
        alive = iter([True, False])
        with mock.patch.object(session.time, "sleep"), \
                mock.patch.object(session.os, "kill") as killed, \
                mock.patch.object(session, "_process_alive",
                                  side_effect=lambda p: next(alive, False)), \
                mock.patch.object(session, "_lane_exit_kill_tmux"):
            self._run_exit(pid=os.getppid(), read_pause=4.0)
        killed.assert_not_called()

    def test_no_pause_is_the_default(self):
        """Every pre-WI-0399 caller builds its args without the field, so the default is
        the behaviour the dispatched close has always had."""
        with mock.patch.object(session.time, "sleep") as slept, \
                mock.patch.object(session.os, "kill"), \
                mock.patch.object(session, "_process_alive", return_value=False), \
                mock.patch.object(session, "_lane_exit_kill_tmux"):
            self._run_exit(pid=os.getppid())
        for call in slept.call_args_list:
            self.assertNotEqual(call.args[0], session.CLOSE_READ_PAUSE_SECONDS)


# ══════════════════════════════════════════════════════════════════════════════════
# E. The published close record — what the board reads
# ══════════════════════════════════════════════════════════════════════════════════

class PublishedCloseTest(LaneBase):
    def setUp(self):
        super().setUp()
        self._as_this_session()
        self.text = JOURNAL.format(sid=SID, csid=CSID,
                                   ended="2026-09-19T12:00:00+00:00")
        self.fm, _ = session.parse_journal(self.text)

    def test_it_publishes_the_summary_and_its_first_line(self):
        self.assertTrue(session._session_close_publish(self.text, self.fm, landed=True))
        rows = session._session_close_records()
        self.assertEqual(len(rows), 1)
        self.assertIn("WI-0399 built, tested and landed.", rows[0]["summary"])
        self.assertEqual(rows[0]["summary_line"], "WI-0399 built, tested and landed.")
        self.assertEqual(rows[0]["journal_id"], SID)
        self.assertEqual(rows[0]["claude_session_id"], CSID)
        self.assertTrue(rows[0]["landed"])
        self.assertEqual(rows[0]["kind"], session.SESSION_CLOSE_KIND)

    def test_the_board_gets_the_whole_summary_and_the_alert_gets_one_line(self):
        """BOARD = WHERE IT IS READ, NOTIFICATION = THAT THERE IS SOMETHING TO READ. A
        notification carrying the whole summary makes the board redundant; the two fields
        are what keeps that split real instead of aspirational."""
        session._session_close_publish(self.text, self.fm, landed=True)
        row = session._session_close_records()[0]
        self.assertGreater(len(row["summary"]), len(row["summary_line"]))
        self.assertIn("the notification is a record", row["summary"])
        self.assertNotIn("\n", row["summary_line"])

    def test_landed_is_three_valued(self):
        """None is 'not tried yet', which is what a close published before its land can
        honestly say. A boolean would have to guess, and guessing False renders every
        pre-land close as a failure."""
        session._session_close_publish(self.text, self.fm, landed=None)
        self.assertIsNone(session._session_close_records()[0]["landed"])
        session._session_close_publish(self.text, self.fm, landed=False)
        self.assertIs(session._session_close_records()[0]["landed"], False)

    def test_a_republish_replaces_rather_than_accumulates(self):
        """One row per lane, like `attention`. The close publishes once when the journal
        is stamped and again when the land answers; two rows would show one session
        closing twice."""
        session._session_close_publish(self.text, self.fm, landed=None)
        session._session_close_publish(self.text, self.fm, landed=True)
        self.assertEqual(len(session._session_close_records()), 1)

    def test_records_come_back_in_finish_order(self):
        """Newest finish first, read off `ended` rather than off mtime — mtime says when
        a file was last WRITTEN, which stops being the same thing the first time anything
        rewrites one."""
        for n, (ident, ended) in enumerate([
                ("worktree-poga-1", "2026-09-19T10:00:00+00:00"),
                ("worktree-poga-2", "2026-09-19T14:00:00+00:00"),
                ("worktree-poga-3", "2026-09-19T12:00:00+00:00")]):
            fm = dict(self.fm, **{"ended": ended, "session-id": f"{SID}-{n}"})
            session._session_close_publish(self.text, fm, landed=True, identity=ident)
        got = [r["ended"] for r in session._session_close_records()]
        self.assertEqual(got, ["2026-09-19T14:00:00+00:00",
                               "2026-09-19T12:00:00+00:00",
                               "2026-09-19T10:00:00+00:00"])

    def test_an_expired_record_is_dropped_on_read_with_no_sweep(self):
        """A board polling a store nobody has written to for a day shows an empty panel,
        not yesterday's. Derived from the clock — no write happens anywhere."""
        session._session_close_publish(self.text, self.fm, landed=True)
        d = session._coord_dir(session.SESSION_CLOSE_KIND)
        f = next(d.glob("*.json"))
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec["expires_at"] = time.time() - 1
        f.write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(session._session_close_records(), [])
        self.assertEqual(len(session._session_close_records(include_expired=True)), 1)

    def test_a_close_that_cannot_be_published_is_still_a_close(self):
        """A close that FAILS because it could not reach the board is a lost session."""
        with mock.patch.object(session, "_coord_dir", return_value=None):
            self.assertFalse(session._session_close_publish(self.text, self.fm))
        with mock.patch.object(session, "atomic_write", side_effect=OSError("full")):
            self.assertFalse(session._session_close_publish(self.text, self.fm))

    def test_a_session_with_no_state_at_close_publishes_an_empty_summary(self):
        """Absent is not an error and must not be filled in."""
        bare = "---\nsession-id: x\nended: 2026-09-19T12:00:00+00:00\n---\n\n### Notes\n"
        fm, _ = session.parse_journal(bare)
        session._session_close_publish(bare, fm, landed=True)
        row = session._session_close_records()[0]
        self.assertEqual(row["summary"], "")
        self.assertEqual(row["summary_line"], "")


# ══════════════════════════════════════════════════════════════════════════════════
# F. The wrapper half — `poga_launch_record`
# ══════════════════════════════════════════════════════════════════════════════════

POGA = pathlib.Path(__file__).resolve().parent.parent / "poga"


def _extract_function(name: str) -> str:
    """The named shell function's source, lifted from `poga` (a script, not a sourceable
    library — executing it to test one helper would run `main`). Same lift
    `test_board_ensure` uses."""
    import re
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", POGA.read_text(encoding="utf-8"),
                  re.M | re.S)
    assert m, f"function {name} not found in poga"
    return m.group(0)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class LaunchRecordWrapperTest(unittest.TestCase):
    """The receipt is written by the WRAPPER, and that is the runtime-agnostic
    requirement (ADR-0041) rather than an implementation detail — so it is tested as
    shell, against a stub `session.py`, not as a Python call."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fn = _extract_function("poga_launch_record")
        self.seen = self.tmp / "argv.txt"
        (self.tmp / "session.py").write_text(
            "import sys, pathlib\n"
            f"pathlib.Path({str(self.seen)!r}).write_text(' '.join(sys.argv[1:]))\n",
            encoding="utf-8")

    def _run(self, lane="poga-7", rt="claude-code", stub_exit=0, extra=""):
        if stub_exit:
            (self.tmp / "session.py").write_text(
                "import sys, pathlib\n"
                f"pathlib.Path({str(self.seen)!r}).write_text(' '.join(sys.argv[1:]))\n"
                f"sys.exit({stub_exit})\n", encoding="utf-8")
        script = (f'set -euo pipefail\nROOT={str(self.tmp)!r}\nPY=python3\n'
                  f'POGA_RT_ID={rt!r}\n{extra}\n{self.fn}\n'
                  f'poga_launch_record {lane!r}\necho "rc=$?"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              cwd=self.tmp)

    def test_it_passes_the_lane_the_pid_and_the_runtime(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = self.seen.read_text(encoding="utf-8").split()
        self.assertEqual(argv[0], "lane-launch")
        self.assertIn("--lane", argv)
        self.assertEqual(argv[argv.index("--lane") + 1], "poga-7")
        self.assertEqual(argv[argv.index("--runtime") + 1], "claude-code")
        pid = int(argv[argv.index("--pid") + 1])
        self.assertGreater(pid, 1)

    def test_a_pipe_is_not_a_person_so_there_is_no_attended_flag(self):
        """`[ -t 1 ]` is the honest read. Captured output is not a tty, which is the
        detached-pane case: no pause, because nobody is there to read one."""
        self.assertNotIn("--attended", self._run().stdout + self.seen.read_text())

    def test_a_nameless_lane_records_nothing(self):
        self._run(lane="")
        self.assertFalse(self.seen.exists())

    def test_a_failing_recorder_never_fails_the_launch(self):
        """Degrades to exactly today's behaviour — a session that sits at its prompt
        until someone closes the window. That is not worth refusing to start over, and
        `set -e` would otherwise make it fatal."""
        r = self._run(stub_exit=3)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rc=0", r.stdout)

    def test_it_is_silent_on_success(self):
        r = self._run()
        self.assertEqual(r.stdout.strip(), "rc=0")
        self.assertEqual(r.stderr.strip(), "")


class LaunchRecordCallSiteTest(unittest.TestCase):
    """WHERE the call sits is load-bearing and source-pinned, the same argument
    `test_board_ensure` makes for the board hook: `exec` REPLACES the shell, so a call
    placed after any launch path never runs at all — and a receipt that is not written
    means a session that cannot close itself, silently."""

    def setUp(self):
        self.src = POGA.read_text(encoding="utf-8").splitlines()

    def _line_of(self, needle, start=0):
        for i in range(start, len(self.src)):
            if needle in self.src[i]:
                return i
        return -1

    def test_it_precedes_every_exec_in_cmd_session(self):
        start = self._line_of("cmd_session() {")
        self.assertGreater(start, 0)
        end = self._line_of("set_terminal_title() {", start)
        call = self._line_of("poga_launch_record", start)
        self.assertGreater(call, start, "cmd_session never records a launch")
        execs = [i for i in range(start, end)
                 if "exec_or_rollback " in self.src[i] and "()" not in self.src[i]]
        self.assertGreaterEqual(len(execs), 3, "the three launch paths moved")
        for i in execs:
            self.assertLess(call, i,
                            f"poga:{i + 1} execs before the launch is recorded — a "
                            f"successful exec never returns, so that receipt is never "
                            f"written and the session cannot close itself")

    def test_resume_records_too(self):
        """A resumed lane closes itself as well, and its predecessor's receipt names a
        pid that is long gone."""
        start = self._line_of("cmd_resume() {")
        self.assertGreater(start, 0)
        call = self._line_of("poga_launch_record", start)
        ex = self._line_of('exec "$POGA_RT_CMD"', start)
        self.assertGreater(call, start)
        self.assertLess(call, ex)


class CloseSummaryPrintTest(unittest.TestCase):
    """`_close_summary_lines` — the terminal's rendering of the same one section."""

    def test_it_prints_the_section_the_board_publishes(self):
        text = JOURNAL.format(sid=SID, csid=CSID, ended="x")
        lines = session._close_summary_lines(text)
        self.assertIn("state at close:", lines)
        self.assertTrue(any("WI-0399 built" in ln for ln in lines))
        self.assertEqual(session.close_summary(text).splitlines()[0].strip(),
                         next(ln.strip() for ln in lines if "WI-0399" in ln))

    def test_it_is_silent_when_the_section_is_empty(self):
        self.assertEqual(session._close_summary_lines("---\na: b\n---\n\n### X\n"), [])


if __name__ == "__main__":
    unittest.main()
