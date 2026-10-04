"""WI-0354 — a dispatched lane's close acts on the fact, and has a handle that cannot
come back empty.

TWO DEFECTS, ONE LIFECYCLE, both measured rather than reasoned about.

  1. **The close was bolted to the verb.** `cmd_end` runs the lane exit and the runtime
     close only when the land IT ran came back landed. `merge --continue` is the verb for
     the case where that land did NOT — a lane blocked at the gate closes its journal,
     fails to land, is told its session is being left up, and lands minutes later through
     a route with no close in it at all. `poga-D-2659c6-WI-0350`: journal closed,
     `exit: 1 — nothing landed (gate)`, landed minutes later through `--continue`, runtime
     alive long afterwards at a prompt.

  2. **The close had one handle and it was the fragile one.** `_dispatch_runtime_pid`
     reads process ancestry off the machine. When that read came back empty the close
     printed "could not identify this lane's runtime process" and returned, which is the
     one branch that leaves a finished lane running. A lane on the tmux surface is still
     nameable when its ancestry is not, because the name is computed from the environment
     the spawn line set.

The tests below are split along that seam: what the close is keyed on (the closed journal,
never the verb), and what it can act with (either handle, or both). The danger a fixture in
this file carries is named in `tests/test_dispatch_fixture_guard.py` — every case
neutralises the dispatch environment first, and every case that reaches the reaper mocks
`sh`, because the process this suite would otherwise signal is the one running it.
"""

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
from contextlib import redirect_stdout, redirect_stderr
import io
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402

GIT = shutil.which("git")

SID = "20260913T1200Z-devbox-2cfd"       # the lane that measured defect 1
CSID = f"csid-{SID}"


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class LaneCloseBase(unittest.TestCase):
    """One repo, one lane, one dispatched session — `test_lane_exit`'s shape, because the
    coordination store these assertions read has to be the real one for the read to mean
    anything."""

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
        self.identity = "worktree-poga-1"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── the session, its journal, and its records ────────────────────────────────
    def _journal(self, ended=""):
        (self.journals / f"{SID}.md").write_text(
            "---\n"
            f"session-id: {SID}\n"
            f"claude-session-id: {CSID}\n"
            f"started: {self._at(-3600)}\n"
            f"ended: {ended}\n"
            "machine: devbox\n"
            "---\n\n### What happened\n- work\n",
            encoding="utf-8")

    def _at(self, offset):
        return session._coord_iso(time.time() + offset)

    def _dispatched(self, did="D-2659c6", item="WI-0350", marker=""):
        """Run the rest of this test inside a lane that dispatch opened."""
        env = {"POGA_DISPATCH": did, "POGA_DISPATCH_ITEM": item,
               "CLAUDE_CODE_SESSION_ID": CSID}
        if marker:
            env["POGA_LANE_CLOSE"] = marker
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, kind, name, **extra):
        d = session._coord_dir(kind, create=True)
        rec = {"session_id": self.identity, "journal": SID, "branch": self.identity,
               "machine": "devbox", "created_at": self._at(-600),
               "expires_at": time.time() + 3600, "ttl_seconds": 3600}
        rec.update(extra)
        (d / f"{name}.json").write_text(json.dumps(rec) + "\n", encoding="utf-8")

    def _names(self, kind):
        return set(session._coord_list(kind, include_expired=True))

    def _off_the_gate(self):
        """Answer `_under_gate()` the way a LANE answers it, for the cases about what the
        close does on a real machine.

        THE GATE IS WHY THIS IS NEEDED AND WHY IT IS NARROW. `_under_gate` is inherited by
        every child of the suite the land gate runs, and under it `_dispatch_tmux_session`
        and `_lane_exit_kill_tmux` return their CANNOT-TELL answers by design (ADR-0119
        D4) — so a case asserting what the close DOES would be asserting the gate's
        refusal instead, and would pass in a lane and fail at the gate. It did: this
        module's first land was blocked by five of its own cases.

        Every case that calls this also mocks the reach — `sh`, `subprocess.run`, or
        `_lane_exit_kill_tmux` itself — so nothing here can touch the operator's machine
        with the guard answered off. The cases that are ABOUT the guard patch it back on
        locally."""
        p = mock.patch.object(session, "_under_gate", return_value=False)
        p.start()
        self.addCleanup(p.stop)


class TheLaneCanNameItsOwnTmuxSessionTest(LaneCloseBase):
    """`_dispatch_tmux_session` — the handle that cannot come back empty for a lane that
    has one, and is empty for every lane that must not be touched."""

    def setUp(self):
        super().setUp()
        self._off_the_gate()

    def test_it_names_the_session_dispatch_actually_created(self):
        """The same expression the spawn used, so the name is the one `tmux ls` shows —
        derived from the launcher rather than restated here, which is the only way the two
        cannot drift."""
        self._dispatched(did="D-2659c6", item="WI-0350")
        self.assertEqual(session._dispatch_tmux_session(), "poga-D-2659c6-WI-0350")
        self.assertEqual(session._dispatch_tmux_session(),
                         session._tmux_session_name("D-2659c6", "WI-0350"))

    def test_a_hand_opened_lane_has_no_name(self):
        """The discriminator for the whole mechanism. A lane a person opened is their
        workspace, and killing the terminal out from under them is a worse defect than the
        one being fixed — so with no dispatch in the environment there is no name to kill
        by, structurally rather than by a check someone must remember."""
        self.assertEqual(session._dispatch_tmux_session(), "")

    def test_half_an_environment_names_nothing(self):
        """A dispatch id with no item would build `poga-D-2659c6-`, which is a prefix of
        every lane in that dispatch and a name tmux would happily accept. Both or
        neither."""
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-2659c6"}):
            self.assertEqual(session._dispatch_tmux_session(), "")
        with mock.patch.dict(os.environ, {"POGA_DISPATCH_ITEM": "WI-0350"}):
            self.assertEqual(session._dispatch_tmux_session(), "")

    def test_the_gate_gets_no_name(self):
        """ADR-0119 D4: `tmux` is a forbidden program under the gate, and the suite the
        gate runs is running inside somebody's real lane. A name is a handle to kill by,
        so the gate must not be handed one."""
        self._dispatched()
        with mock.patch.object(session, "_under_gate", return_value=True):
            self.assertEqual(session._dispatch_tmux_session(), "")


class TheCloseActsWithEitherHandleTest(LaneCloseBase):
    """`_dispatch_close_runtime` — defect 2. Every case mocks `sh`: the reaper it would
    otherwise spawn signals a real process."""

    def setUp(self):
        super().setUp()
        self._off_the_gate()
        self.marker = str(self.tmp / "close.marker")
        self._dispatched(marker=self.marker)

    def _close(self, pid):
        out = io.StringIO()
        with mock.patch.object(session, "_dispatch_runtime_pid", return_value=pid), \
                mock.patch.object(session, "sh") as ran, \
                redirect_stdout(out):
            session._dispatch_close_runtime(True, CSID)
        argv = ran.call_args[0][0] if ran.call_args else None
        return argv, out.getvalue()

    def test_an_unreadable_ancestry_no_longer_ends_the_close(self):
        """THE MEASURED DEFECT. This returned here, printed "could not identify this
        lane's runtime process", and left the lane running — the one branch that produces
        exactly the state the item was filed over."""
        argv, said = self._close(None)
        self.assertIsNotNone(argv, "the close gave up with a usable handle in hand")
        self.assertIn("--tmux-session", argv)
        self.assertIn("poga-D-2659c6-WI-0350", argv)
        self.assertNotIn("--pid", argv)
        self.assertIn("item complete and landed", said)

    def test_both_handles_are_passed_when_both_resolve(self):
        """The pid is the gentler instrument and is not given up just because a second one
        exists — the reaper signals first and destroys the pane afterwards."""
        argv, _said = self._close(4242)
        self.assertIn("--pid", argv)
        self.assertEqual(argv[argv.index("--pid") + 1], "4242")
        self.assertIn("--tmux-session", argv)
        self.assertEqual(argv[argv.index("--tmux-session") + 1], "poga-D-2659c6-WI-0350")

    def test_with_no_handle_at_all_it_says_so_and_acts_on_nothing(self):
        """Honest cannot-tell, not a guess. With no item in the environment there is no
        name, and with no ancestry there is no pid; a close that invented either would be
        signalling something it never identified."""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"POGA_DISPATCH_ITEM": ""}), \
                mock.patch.object(session, "_dispatch_runtime_pid", return_value=None), \
                mock.patch.object(session, "sh") as ran, \
                redirect_stdout(out):
            session._dispatch_close_runtime(True, CSID)
        ran.assert_not_called()
        self.assertIn("could not identify", out.getvalue())

    def test_the_marker_records_the_handle_that_was_used(self):
        """The wrapper shell reads this file to tell a sanctioned close from a crash, and
        a receipt that cannot say what it did is the thing
        `a-close-is-the-banner-not-the-sentence` is about."""
        self._close(None)
        rec = json.loads(pathlib.Path(self.marker).read_text(encoding="utf-8"))
        self.assertEqual(rec["tmux_session"], "poga-D-2659c6-WI-0350")
        self.assertEqual(rec["item"], "WI-0350")
        self.assertIsNone(rec["pid"])

    def test_an_unlanded_lane_is_still_left_up(self):
        """Unchanged and load-bearing: a lane whose gate failed has its work committed on
        its own branch and needs a session alive to run `merge` again. Killing it converts
        a recoverable failure into stranded work."""
        out = io.StringIO()
        with mock.patch.object(session, "_dispatch_runtime_pid", return_value=4242), \
                mock.patch.object(session, "sh") as ran, redirect_stdout(out):
            session._dispatch_close_runtime(False, CSID)
        ran.assert_not_called()
        self.assertIn("did NOT land", out.getvalue())

    def test_a_journal_that_is_not_this_runtimes_closes_nothing(self):
        """Unchanged and load-bearing: `end --session-id <other>` closes a sibling's
        journal. That is not grounds for ending THIS runtime, and the cost of getting it
        wrong is a live session destroyed mid-work."""
        with mock.patch.object(session, "_dispatch_runtime_pid", return_value=4242), \
                mock.patch.object(session, "sh") as ran, redirect_stdout(io.StringIO()):
            session._dispatch_close_runtime(True, "csid-somebody-else")
        ran.assert_not_called()


class TheTmuxSessionIsDestroyedLastTest(LaneCloseBase):
    """`cmd_lane_exit` and `_lane_exit_kill_tmux` — the reaper end of the two handles."""

    def setUp(self):
        super().setUp()
        self._off_the_gate()

    def _run_exit(self, **kw):
        args = type("A", (), {"pid": 0, "tmux_session": "", "grace": 0.05,
                              "detach": False})()
        for k, v in kw.items():
            setattr(args, k, v)
        with self.assertRaises(SystemExit) as ctx:
            session.cmd_lane_exit(args)
        return ctx.exception.code

    def test_a_tmux_name_alone_is_enough_to_run(self):
        """The reaper used to exit immediately on a pid it could not signal. That is the
        same dead end one layer down, and it is where a handle-less close ends up."""
        with mock.patch.object(session, "_lane_exit_kill_tmux") as killed, \
                mock.patch.object(session.os, "kill") as signalled:
            self._run_exit(tmux_session="poga-D-2659c6-WI-0350")
        killed.assert_called_once_with("poga-D-2659c6-WI-0350")
        signalled.assert_not_called()

    def test_the_pane_is_destroyed_after_the_signal_not_before(self):
        """A runtime that handles SIGTERM writes what it writes on the way out; taking its
        terminal first would take the writing with it. Asserted as an ORDER, because that
        is the whole claim — a call count would pass with the two reversed."""
        order = []
        alive = iter([True, False])                # signalable, then gone on the first look
        with mock.patch.object(session, "_process_alive",
                               side_effect=lambda _p: next(alive, False)), \
                mock.patch.object(session.os, "kill",
                                  side_effect=lambda _p, s: order.append(s)), \
                mock.patch.object(session, "_lane_exit_kill_tmux",
                                  side_effect=lambda n: order.append("kill-session")):
            self._run_exit(pid=4242, tmux_session="poga-D-2659c6-WI-0350")
        self.assertEqual(order, [session.signal.SIGTERM, "kill-session"])

    def test_the_escalation_still_runs_before_the_pane_goes(self):
        """SIGTERM, then SIGKILL after the grace, and only then the pane. The escalation is
        what makes the mechanism independent of how a runtime treats SIGTERM — a fact this
        substrate could not measure — so a second handle must not quietly replace it."""
        order = []
        with mock.patch.object(session, "_process_alive", return_value=True), \
                mock.patch.object(session.os, "kill",
                                  side_effect=lambda _p, s: order.append(s)), \
                mock.patch.object(session, "_lane_exit_kill_tmux",
                                  side_effect=lambda n: order.append("kill-session")):
            self._run_exit(pid=4242, tmux_session="poga-D-2659c6-WI-0350")
        self.assertEqual(order, [session.signal.SIGTERM, session.signal.SIGKILL,
                                 "kill-session"])

    def test_with_neither_handle_it_does_nothing(self):
        with mock.patch.object(session, "_lane_exit_kill_tmux") as killed, \
                mock.patch.object(session.os, "kill") as signalled:
            self._run_exit()
        killed.assert_not_called()
        signalled.assert_not_called()

    def test_kill_session_names_the_lanes_own_session(self):
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
                mock.patch.object(session.subprocess, "run") as ran:
            session._lane_exit_kill_tmux("poga-D-2659c6-WI-0350")
        self.assertEqual(ran.call_args[0][0],
                         ["/usr/bin/tmux", "kill-session", "-t", "poga-D-2659c6-WI-0350"])

    def test_it_reaches_no_tmux_without_a_name_under_a_gate_or_without_the_binary(self):
        """Three separate reasons not to act, each of which used to be one `if` away from
        a suite that ends a real pane on the operator's machine."""
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
                mock.patch.object(session.subprocess, "run") as ran:
            session._lane_exit_kill_tmux("")
            ran.assert_not_called()
            with mock.patch.object(session, "_under_gate", return_value=True):
                session._lane_exit_kill_tmux("poga-x")
            ran.assert_not_called()
        with mock.patch.object(session, "_tmux_bin", return_value=None), \
                mock.patch.object(session.subprocess, "run") as ran:
            session._lane_exit_kill_tmux("poga-x")
            ran.assert_not_called()


class TheContinueRouteClosesTheLaneTest(LaneCloseBase):
    """`_dispatch_close_after_land` — defect 1. The close is keyed on the journal being
    closed, never on which verb ran the land."""

    def test_a_landed_lane_whose_journal_is_closed_is_closed(self):
        """The measured sequence, end to end: journal stamped closed, the first land
        refused by the gate, the work landed later through `--continue`. Nothing in that
        story reaches `cmd_end` a second time, so this is the only thing that can free the
        lane."""
        self._journal(ended=self._at(-500))
        self._dispatched()
        with mock.patch.object(session, "_dispatch_close_runtime") as closed, \
                redirect_stdout(io.StringIO()):
            session._dispatch_close_after_land()
        closed.assert_called_once_with(True, CSID)

    def test_it_frees_the_items_claim(self):
        """The acceptance's first half, against the real coordination store rather than a
        call assertion — `poga lanes` shows no claim for the item."""
        self._journal(ended=self._at(-500))
        self._dispatched()
        self._write("claims", "WI-0350")
        self.assertEqual(self._names("claims"), {"WI-0350"})

        with mock.patch.object(session, "_dispatch_close_runtime"), \
                redirect_stdout(io.StringIO()):
            session._dispatch_close_after_land()

        self.assertEqual(self._names("claims"), set())

    def test_an_open_journal_means_the_lane_is_mid_work(self):
        """`--continue` is also the ordinary mid-session land — the deferred land after
        `end --no-merge`, a genuine second package. Closing there would destroy a live
        session to save a sweep, so the stamp is read rather than the verb assumed."""
        self._journal()                                   # no `ended`
        self._dispatched()
        self._write("claims", "WI-0350")
        with mock.patch.object(session, "_dispatch_close_runtime") as closed, \
                redirect_stdout(io.StringIO()):
            session._dispatch_close_after_land()
        closed.assert_not_called()
        self.assertEqual(self._names("claims"), {"WI-0350"},
                         "a mid-session land released the claim it is still working under")

    def test_a_hand_opened_lane_is_never_closed_by_its_own_land(self):
        """the operator's lane is his workspace. The land is the same land; the authorization is
        the dispatch, and it is absent."""
        self._journal(ended=self._at(-500))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": CSID}), \
                mock.patch.object(session, "_dispatch_close_runtime") as closed, \
                redirect_stdout(io.StringIO()):
            session._dispatch_close_after_land()
        closed.assert_not_called()

    def test_no_journal_of_ours_closes_nothing(self):
        """`_find_holder_journal` answers None when it cannot say honestly which journal is
        this session's. None is not 'closed'."""
        self._dispatched()
        with mock.patch.object(session, "_dispatch_close_runtime") as closed, \
                redirect_stdout(io.StringIO()):
            session._dispatch_close_after_land()
        closed.assert_not_called()

    def test_it_never_acts_against_a_half_patched_fixture(self):
        """WI-0035, for the reason `_release_lane_reservation` spells out — and the stakes
        here are a signal, not a record."""
        self._journal(ended=self._at(-500))
        self._dispatched()
        with mock.patch.object(session, "_running_against_fixture", return_value=True), \
                mock.patch.object(session, "_dispatch_close_runtime") as closed:
            session._dispatch_close_after_land()
        closed.assert_not_called()

    def test_a_cleanup_failure_never_unlands_a_landed_lane(self):
        """Fail-open, stated as a test because the alternative is a successful land
        reported as a failure by its own housekeeping."""
        self._journal(ended=self._at(-500))
        self._dispatched()
        err = io.StringIO()
        with mock.patch.object(session, "_lane_exit_report",
                               side_effect=RuntimeError("store gone")), \
                redirect_stderr(err):
            session._dispatch_close_after_land()          # must not raise
        self.assertIn("the land stands", err.getvalue())


class TheContinueRouteIsWiredTest(unittest.TestCase):
    """The route exists in the source that runs it. `cmd_merge`'s `--continue` branch is
    the one place a lane lands without closing, so a refactor that drops this call
    re-opens the defect with every behavioural test above still green."""

    def test_merge_continue_calls_the_post_land_close(self):
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "sessionlib" / "land.py").read_text(encoding="utf-8")
        self.assertIn("_dispatch_close_after_land()", src,
                      "`merge --continue` no longer closes a dispatched lane that landed "
                      "through it — the WI-0354 defect, restored")

    def test_the_post_land_close_reaches_the_one_lane_exit(self):
        """It must not grow a release of its own — see
        `test_lane_exit.TheDocstringIsTrueTest`."""
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "sessionlib" / "lanes.py").read_text(encoding="utf-8")
        body = src.split("def _dispatch_close_after_land()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_lane_exit_report(", body)
        self.assertNotIn("_coord_release(", body)


if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
