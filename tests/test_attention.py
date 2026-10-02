"""ADR-0101 / WI-0162 — a lane may block on a question, never invisibly.

The failure this exists to prevent is not "operator missed a question". It is that a lane
waiting on him and a lane doing nothing produce byte-identical status on every surface we
have. On 2026-08-22 two abandoned lanes read `interactive · idle` in the session roster and
`live session — protected` in the reaper for over two hours, while a genuinely attended
session would have read exactly the same. Every liveness signal we keep answers "did the
process move"; none answers "is a human there".

So the properties pinned here are about the RECORD, not about delivery:

  1. THE HOOK WRITES A RECORD, and it survives being called with garbage — a notification
     path that can fail the session it reports on is worse than no notification path.
  2. THREE STATES, NEVER TWO. `unknown` (store unreadable) must never render as `clear`
     (nobody waiting). That collapse is a recurring failure.
  3. WORKING CLEARS IT. A tool call proves the session is not blocked, and so does an end
     — a record left behind ages into a permanent "waiting on you" for a lane that no
     longer exists, which is the ghost shape this design exists to avoid.
  4. AGE IS THE PAYLOAD. "Waiting" and "waiting for six hours" are different facts.
"""

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
import harness_fixture  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (neutralize_coord_journal,  # noqa: E402
                           neutralize_live_store)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class AttentionBase(unittest.TestCase):
    def setUp(self):
        # WI-0242: this module reaches `_holder_journal_dirs` (via `_coord_reap` /
        # `_attention_write`), which appends `POGA_INVOKED_FROM` to the journal
        # directories it scans — the operator's REAL checkout and every live sibling
        # lane. `neutralize_coord_journal` does not cover this route: it patches
        # `_coord_holder`, and nothing here goes through it.
        # WI-0275 folds that clear into the ambient neutraliser, which clears
        # `DISPATCH_ENV_VARS` plus the identity axis `_coord_identity` reads. It is a
        # strict superset of `neutralize_dispatch_env`, pinned by
        # `test_ambient_fixture_guard.TheListIsOneListTest`, so this one call does both.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        # ...and WI-0295: clearing POGA_INVOKED_FROM removes ONE of the directories
        # `_holder_journal_dirs` scans. `_shared_work_root()` supplies the rest — it
        # resolves through the git COMMON dir to the developer's real repo whatever
        # `ROOT` says, and every live lane under it gets its journals globbed and
        # parsed. Measured: this module made 2,576 reads of the live store.
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
        os.environ["CLAUDE_CODE_SESSION_ID"] = "testsession"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _notify(self, payload):
        buf = io.StringIO(json.dumps(payload))
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value=json.loads(buf.getvalue())):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_notify(argparse.Namespace())
        return cm.exception.code


class TheHookWritesARecordTest(AttentionBase):
    """Property 1."""

    def test_a_notification_becomes_a_readable_waiting_record(self):
        self._notify({"session_id": "s", "hook_event_name": "Notification",
                      "message": "Claude needs your permission to use Bash"})
        state, recs = session._attention_state()
        self.assertEqual(state, "waiting")
        self.assertEqual(len(recs), 1)
        rec = next(iter(recs.values()))
        self.assertIn("permission to use Bash", rec["message"])

    def test_the_hook_exits_clean_on_garbage_input(self):
        with mock.patch.object(session, "_read_hook_stdin", side_effect=ValueError("boom")):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_notify(argparse.Namespace())
        self.assertEqual(cm.exception.code, 0)

    def test_a_notification_with_no_message_still_records_the_waiting(self):
        """The fact that a lane is blocked matters even when the payload says nothing."""
        self._notify({"session_id": "s", "hook_event_name": "Notification"})
        state, _ = session._attention_state()
        self.assertEqual(state, "waiting")

    def test_an_enormous_message_is_bounded(self):
        self._notify({"session_id": "s", "message": "x" * 5000})
        rec = next(iter(session._attention_state()[1].values()))
        self.assertLessEqual(len(rec["message"]), 500)

    def test_a_write_failure_is_swallowed_not_raised(self):
        with mock.patch.object(session, "atomic_write", side_effect=OSError("read-only")):
            self.assertFalse(session._attention_write("hi"))


class ThreeStatesNeverTwoTest(AttentionBase):
    """Property 2 — the collapse that keeps recurring."""

    def test_nothing_waiting_is_clear_and_renders_silent(self):
        state, _ = session._attention_state()
        self.assertEqual(state, "clear")
        self.assertEqual(session._attention_lines(), [])

    def test_an_unreadable_store_is_unknown_not_clear(self):
        with mock.patch.object(session, "_coord_dir", return_value=None):
            state, _ = session._attention_state()
        self.assertEqual(state, "unknown")

    def test_a_listing_error_is_unknown_not_clear(self):
        session._attention_write("waiting")
        with mock.patch.object(session, "_coord_list", side_effect=OSError("gone")):
            state, _ = session._attention_state()
        self.assertEqual(state, "unknown")

    def test_unknown_is_never_silent_because_silence_reads_as_nobody_waiting(self):
        with mock.patch.object(session, "_coord_dir", return_value=None):
            lines = session._attention_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("CANNOT TELL", lines[0])
        self.assertIn("not 'none'", lines[0])


class WorkingClearsItTest(AttentionBase):
    """Property 3."""

    def test_clear_removes_the_record(self):
        session._attention_write("waiting")
        self.assertTrue(session._attention_clear())
        self.assertEqual(session._attention_state()[0], "clear")

    def test_clearing_nothing_is_not_an_error(self):
        self.assertFalse(session._attention_clear())

    def _beat(self):
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"session_id": "testsession"}), \
             mock.patch.object(session, "_complete_lazy_start"):
            with contextlib.suppress(BaseException):
                session.cmd_heartbeat(argparse.Namespace())

    def test_a_tool_call_clears_the_waiting_record(self):
        session._attention_write("waiting")
        self._beat()
        self.assertEqual(session._attention_state()[0], "clear")

    def test_the_clear_survives_the_heartbeat_throttle(self):
        """The heartbeat throttles its WRITE to one per interval. If the clear sat below
        that throttle, the first beat after an answer — the one that must clear it — would
        be skipped, and the lane would read 'waiting on you' while visibly working."""
        self._beat()                      # first beat: primes the throttle
        session._attention_write("asked again immediately after")
        self._beat()                      # second beat: inside the throttle window
        self.assertEqual(session._attention_state()[0], "clear")

    def test_session_end_clears_it_so_no_ghost_outlives_the_lane(self):
        src = harness_fixture.harness_source()
        body = src.split("def cmd_end", 1)[1].split("def ", 1)[0]
        self.assertIn("_attention_clear()", body)


class AgeIsThePayloadTest(AttentionBase):
    """Property 4."""

    def test_a_waiting_line_carries_the_age_and_the_message(self):
        session._attention_write("should I use X or Y?")
        lines = session._attention_lines(exclude_self=False)
        self.assertEqual(len(lines), 1)
        self.assertIn("WAITING ON YOU", lines[0])
        self.assertIn("0m", lines[0])
        self.assertIn("should I use X or Y?", lines[0])

    def test_hours_are_rendered_as_hours_not_a_large_minute_count(self):
        session._attention_write("old question")
        d = session._coord_dir(session.ATTENTION_KIND)
        p = next(d.glob("*.json"))
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["created_at"] = "2026-08-23T12:00:00+00:00"
        p.write_text(json.dumps(rec), encoding="utf-8")
        with mock.patch.object(session, "_iso_age_min", return_value=440.0):
            line = session._attention_lines(exclude_self=False)[0]
        self.assertIn("7h 20m", line)

    def test_a_lane_does_not_report_itself_to_itself(self):
        session._attention_write("waiting")
        self.assertEqual(session._attention_lines(exclude_self=True), [])
        self.assertEqual(len(session._attention_lines(exclude_self=False)), 1)

    def test_the_claimed_item_is_carried_so_waiting_joins_to_work(self):
        """WI-0241 — and the FIXTURE is the point, so do not simplify it back.

        This test used to hand-build `{"WI-0042": {"branch": _coord_identity()}}` — a claim
        whose branch is the lane address. `poga work claim` cannot produce that: it writes
        through the ADR-0073 main-checkout front door, so a real claim always carries
        `branch: "main"` while the identity is `worktree-poga-N`. The old join compared those
        two and was false for every claim that can exist, so `item` was empty on every record
        ever written and `_attach_raise_pass` — which skips a record with a blank `item` —
        never ran. The test stayed green the whole time because its fixture was the one shape
        the writer never emits. So build the claim with `_coord_record`, the real writer, and
        let it stamp its own branch."""
        claim = session._coord_record("testsession", 3600, kind="claims", name="WI-0042")
        self.assertEqual(claim["branch"], "main")          # the shape the front door emits
        self.assertNotEqual(claim["branch"], session._coord_identity())   # ...never the lane
        with mock.patch.object(session, "_coord_list", return_value={"WI-0042": claim}):
            session._attention_write("blocked on a design call")
        rec = next(iter(session._attention_state()[1].values()))
        self.assertEqual(rec["item"], "WI-0042")

    def test_a_claim_on_a_named_branch_still_joins_when_the_session_id_is_gone(self):
        """The branch arm is a real fallback, not decoration: a claim taken by a session whose
        id we cannot read still joins by branch. Pinned so the session-first fix above cannot
        be mistaken for a licence to delete it."""
        claim = {"session_id": "", "branch": session._coord_identity()}
        with mock.patch.object(session, "_coord_list", return_value={"WI-0077": claim}):
            self.assertEqual(session._attention_claimed_item(csid="", identity=""), "WI-0077")

    def test_a_claim_held_by_another_session_is_not_reported_as_this_lanes_work(self):
        """The join must not pick up a sibling lane's claim. Both records here carry
        `branch: "main"` — which is what every real claim carries — so a predicate that leans
        on the branch would match the wrong one."""
        mine = session._coord_record("testsession", 3600, kind="claims", name="WI-0042")
        theirs = session._coord_record("othersession", 3600, kind="claims", name="WI-0099")
        with mock.patch.object(session, "_coord_list",
                               return_value={"WI-0099": theirs, "WI-0042": mine}):
            self.assertEqual(session._attention_claimed_item(csid="testsession"), "WI-0042")


class TheVerbTest(AttentionBase):
    def test_the_verb_reports_nothing_waiting_in_plain_words(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_attention(argparse.Namespace(clear=False))
        self.assertIn("no lane is waiting", buf.getvalue())

    def test_the_verb_lists_a_waiting_lane_including_this_one(self):
        session._attention_write("a question")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_attention(argparse.Namespace(clear=False))
        self.assertIn("WAITING ON YOU", buf.getvalue())

    def test_the_verb_can_clear_this_lanes_record(self):
        session._attention_write("a question")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_attention(argparse.Namespace(clear=True))
        self.assertIn("cleared", buf.getvalue())
        self.assertEqual(session._attention_state()[0], "clear")


class ARecordNeverOutlivesItsSessionTest(AttentionBase):
    """WI-0182 — Property 5, learned the hard way on the first real firing.

    The record has exactly two clear paths, the tool-use heartbeat and the session close,
    and BOTH belong to the session that wrote it. So a record created after that session's
    close can never be cleared by anything and sits for its full 24h TTL asserting that a
    human is being waited on.

    That is not a crash path. On 2026-08-23 the `Notification` hook — still bound while
    Claude Code shuts down — fired 3m40s AFTER `session.py end` had stamped the journal,
    and every surface reported `worktree-poga-4 has been WAITING ON YOU` for the rest of
    the day, at a lane whose worktree and branch had both been removed. It is the ORDINARY
    close, so untreated it recurs on every one.

    The proof of death is the JOURNAL's `ended:` stamp, not a `.session-state` marker: the
    journal is tracked and survives its lane's teardown, and the markers are per-worktree
    and did not. Both halves are pinned here — the write-side refusal, and the reaper that
    catches records written before the refusal shipped.
    """

    def setUp(self):
        super().setUp()
        self.jdir = self.tmp / "journals"
        self.jdir.mkdir()
        self._pj = mock.patch.object(session, "JOURNAL_DIR", self.jdir)
        self._pj.start()
        self.addCleanup(self._pj.stop)

    def _journal(self, csid, ended=""):
        (self.jdir / f"20260823T1200Z-devbox-{csid[:4]}.md").write_text(
            "---\n"
            f"session-id: 20260823T1200Z-devbox-{csid[:4]}\n"
            "ordinal: 1\n"
            "machine: DevBox\n"
            f"claude-session-id: {csid}\n"
            "started: 2026-08-23T12:00:55+00:00\n"
            f"ended: {ended}\n"
            "---\n\n### What happened\n",
            encoding="utf-8")

    # -- the write-side refusal ------------------------------------------------
    def test_a_notification_after_the_session_ended_writes_nothing(self):
        """The reported incident, reproduced: the journal is already stamped `ended` when
        the hook fires. No record may be created, because nothing could ever clear it."""
        self._journal("testsession", ended="2026-08-23T14:14:45+00:00")
        self._notify({"session_id": "s", "message": "Claude is waiting for your input"})
        self.assertEqual(session._attention_state()[0], "clear")

    def test_a_notification_while_the_session_is_open_still_records(self):
        """The guard must not swallow the case the whole design exists for."""
        self._journal("testsession", ended="")
        self._notify({"session_id": "s", "message": "which option do you want?"})
        self.assertEqual(session._attention_state()[0], "waiting")

    # -- fail-open: never claim `over` without proof ----------------------------
    def test_an_unresolvable_session_is_not_proven_over_and_still_records(self):
        """No journal matches, so death is UNPROVEN. The costs are asymmetric — a wrong
        `over` silently swallows a real question a human is owed, while a wrong `not over`
        leaves one line the reaper and the TTL both still clear."""
        self.assertFalse(session._attention_session_is_over("nobody-has-this-id"))
        self._notify({"session_id": "s", "message": "a real question"})
        self.assertEqual(session._attention_state()[0], "waiting")

    def test_a_runtime_with_no_session_id_is_not_proven_over(self):
        """A non-Claude runtime exports no `CLAUDE_CODE_SESSION_ID`, so there is no key to
        match on — which is 'cannot tell', never 'ended'."""
        self.assertFalse(session._attention_session_is_over(""))

    # -- the reaper backstop ---------------------------------------------------
    def test_the_reaper_clears_a_record_whose_session_is_over_before_its_ttl(self):
        """The write-side guard only helps records made after it ships. The one that
        exposed the defect was already on disk with 20 hours left to run, and the 24h TTL
        is deliberately long — a question left unanswered for most of a day is still real — which
        makes it a fine backstop and a terrible mechanism."""
        self._journal("testsession", ended="")
        session._attention_write("a question")
        self.assertEqual(session._attention_state()[0], "waiting")
        rec = next(iter(session._attention_state()[1].values()))
        self.assertFalse(session._coord_expired(rec), "must still be well inside its TTL")

        self._journal("testsession", ended="2026-08-23T14:14:45+00:00")
        self.assertGreaterEqual(session._coord_reap(), 1)
        self.assertEqual(session._attention_state()[0], "clear")

    def test_the_reaper_leaves_a_live_sessions_record_alone(self):
        """The backstop must not become a second way to lose a real question."""
        self._journal("testsession", ended="")
        session._attention_write("a question")
        session._coord_reap()
        self.assertEqual(session._attention_state()[0], "waiting")


class AStaleRecordHasAReleasePathTest(AttentionBase):
    """WI-0182 third defect — WI-0164's shape, one coordination kind further on.

    `release --kind` covered four kinds; the store writes six holds. `attention` had no
    release path at all, so the only exits from a stale record were waiting out 24h or
    hand-editing json under `.git/` — the denied-hand-run-with-no-verb that ADR-0099 exists
    to stop. `ops-alloc` had a TTL constant and no reaper to honour it, the same gap
    silently.
    """

    def test_attention_and_ops_alloc_are_releasable_kinds(self):
        self.assertIn("attention", session.COORD_KINDS)
        self.assertIn("ops-alloc", session.COORD_KINDS)

    def test_a_stale_attention_record_can_be_released_by_name(self):
        session._attention_write("waiting")
        key = session._attention_key()
        self.assertTrue(session._coord_release("attention", key,
                                               session._coord_identity(), force=True))
        self.assertEqual(session._attention_state()[0], "clear")

    def test_every_hold_kind_has_its_own_refresh_ttl(self):
        """A kind missing from the refresh map is silently re-stamped with the CLAIM
        lifetime — which would shorten a 24h attention record to 8h on the first heartbeat
        that touched it. Asserted against the tuple so a new kind cannot be added without
        one ([`declare-what-a-check-assumes`])."""
        import inspect
        src = inspect.getsource(session._coord_refresh_identity)
        for kind in session.COORD_KINDS:
            self.assertIn(f'"{kind}":', src, f"{kind} has no explicit refresh TTL")

    def test_the_coord_dump_shows_every_hold_kind(self):
        """`coord` documents itself as dumping ALL kinds and iterates COORD_KINDS to do it,
        so a kind absent from the tuple was invisible on the one surface an operator reads
        to diagnose exactly this."""
        session._attention_write("waiting")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_coord(argparse.Namespace(all=True))
        self.assertIn("attention", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
