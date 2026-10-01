"""WI-0164 — a dead lane releases its own reservations, and WI-0162 — the Notification
payload records its own shape.

Both exist because the substrate was leaving a human holding something the machine could
have held itself.

WI-0164. Recovering poga-2's own two commits into another lane was refused by the land
gate: *"WI-0155 claims WI number 0155, which is reserved by worktree-poga-2"*. (That quoted
WI-0155 is the claimant in poga-2's lane numbering, today's **WI-0170**; the quote is left
verbatim because it is a transcript. Today's WI-0155 is a different item.) The gate was
RIGHT — numbers are drawn, never picked, and it cannot tell a recovered original from a
hand-picked collision. But the two ways out were both bad: renumber items that had been
correctly drawn (breaking the ADR-0100/0101 references already written against them), or
hand-edit json under `.git/` — the hand-repair class ADR-0099 exists to end. `release
--kind` made it a verb, and a verb is still an errand. operator ruled in session ~173
that this is not the operator's work: the system must release from dead lanes on its own.

THE CIRCULARITY the old sweep could not break: `_coord_release_dead_lanes` keyed on the
holder's BRANCH being gone, and a lane that dies with unmerged work keeps its branch by
design. So the one sweep that could have freed the reservation was the one that provably
never would.

WI-0162. The key the attention message is read from was a guess that survives being wrong —
the record is still written, just without text — and every test fed it synthetically. The
only way to confirm it was for a person to be watching at the exact moment a real session
blocked, which is a person as an execution surface (ADR-0099) for a fact the machine can
capture on its own.

Properties pinned here:

  A. DEATH IS PROVEN TWO WAYS, and an unprovable answer is NOT death.
  B. THE LAND GATE FREES A DEAD HOLDER AND SAYS SO — but still refuses a live one.
  C. THE SWEEP SEES A PARKED BRANCH, which is the case it used to be blind to.
  D. THE PAYLOAD CONFIRMS ITS OWN SHAPE, in three states, never two.
"""

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
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class DeadHolderBase(unittest.TestCase):
    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "poga").write_text("#!/bin/sh\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "testsession"
        self._root = mock.patch.object(session, "_shared_work_root",
                                       return_value=self.main)
        self._root.start()
        self.addCleanup(self._root.stop)

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lane(self, name="poga-2", branch=True, worktree=True, live=False):
        """Build a lane in one of the states death has to be judged from."""
        if branch:
            _git(self.main, "branch", f"worktree-{name}")
        tree = self.main / ".claude" / "worktrees" / name
        if worktree:
            state = tree / ".session-state"
            state.mkdir(parents=True)
            if live:
                (state / "csid.live").write_text(
                    json.dumps({"last_beat": session._coord_iso(session.time.time())}),
                    encoding="utf-8")
        return {"session_id": f"worktree-{name}", "branch": f"worktree-{name}"}


class DeathIsProvenNeverGuessedTest(DeadHolderBase):
    """Property A."""

    def test_a_gone_branch_is_dead(self):
        rec = {"session_id": "worktree-poga-9"}
        self.assertTrue(session._coord_holder_is_dead(rec))

    def test_a_parked_branch_with_no_live_session_is_dead(self):
        # THE WI-0164 CASE. The branch survives by design because the work is unmerged;
        # the old sweep read that as "still alive" and never freed anything.
        rec = self._lane("poga-2", branch=True, worktree=True, live=False)
        self.assertTrue(session._coord_holder_is_dead(rec, liveness_proof=True))

    def test_the_liveness_proof_is_opt_in_and_off_by_default(self):
        # THE SAFETY PROPERTY. Heartbeat evidence is strong for a lane that has been
        # running and weak for one that has just started, so it is spent only where a
        # false positive is cheap — number reservations, never claims. A claim freed under
        # a lane between beats hands its item to a sibling while it is still working.
        rec = self._lane("poga-2b", branch=True, worktree=True, live=False)
        self.assertFalse(session._coord_holder_is_dead(rec))

    def test_claims_do_not_get_the_liveness_proof(self):
        self.assertNotIn("claims", session.COORD_ALLOC_KINDS)
        for k in ("adr-alloc", "wi-alloc", "ops-alloc", "lane-alloc"):
            self.assertIn(k, session.COORD_ALLOC_KINDS)

    def test_a_branch_whose_worktree_was_already_reaped_is_dead(self):
        rec = self._lane("poga-3", branch=True, worktree=False)
        self.assertTrue(session._coord_holder_is_dead(rec, liveness_proof=True))

    def test_a_lane_with_a_fresh_heartbeat_is_alive(self):
        rec = self._lane("poga-4", branch=True, worktree=True, live=True)
        self.assertFalse(session._coord_holder_is_dead(rec, liveness_proof=True))

    def test_a_non_lane_identity_is_never_judged_dead(self):
        # A Claude session id is not a branch, and an absent session id is not evidence
        # of anything — that is the `declare-what-a-check-assumes` collapse.
        self.assertFalse(session._coord_holder_is_dead({"session_id": "abc-123"}))
        self.assertFalse(session._coord_holder_is_dead({}))

    def test_cannot_tell_is_not_dead(self):
        rec = self._lane("poga-5", branch=True, worktree=True, live=False)
        with mock.patch.object(session, "_ref_exists", side_effect=OSError("boom")):
            self.assertFalse(session._coord_holder_is_dead(rec, liveness_proof=True))

    def test_an_unreadable_worktree_errs_toward_alive(self):
        # `_tree_has_live_session` errs toward True on a read failure, and that direction
        # is right here too: a record we cannot prove dead stays held.
        rec = self._lane("poga-6", branch=True, worktree=True, live=False)
        with mock.patch.object(session, "_tree_has_live_session", return_value=True):
            self.assertFalse(session._coord_holder_is_dead(rec, liveness_proof=True))


class TheSweepSeesAParkedBranchTest(DeadHolderBase):
    """Property C."""

    def test_a_parked_lanes_reservation_is_swept(self):
        self._lane("poga-2", branch=True, worktree=True, live=False)
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-2", 3600)
        freed = session._coord_release_dead_lanes(kinds=("wi-alloc",))
        self.assertIn(("wi-alloc", "0155"), freed)

    def test_a_live_lanes_reservation_is_left_alone(self):
        self._lane("poga-2", branch=True, worktree=True, live=True)
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-2", 3600)
        self.assertEqual(session._coord_release_dead_lanes(kinds=("wi-alloc",)), [])

    def test_it_still_sweeps_a_torn_down_lane(self):
        # The behaviour that already worked must not regress.
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-8", 3600)
        freed = session._coord_release_dead_lanes(kinds=("claims",))
        self.assertIn(("claims", "WI-0001"), freed)


class TheLandGateFreesADeadHolderTest(DeadHolderBase):
    """Property B."""

    def _gate(self, holder, ident="worktree-poga-7"):
        """Drive the real gate: one NEW file WI-0155 landing, nothing on the trunk.

        `sh` is dispatched on argv rather than sequenced, because `_coord_holder_is_dead`
        shells out too (`_ref_exists`) and a positional side_effect would hand the liveness
        check the gate's git output.
        """
        real_sh = session.sh

        def fake_sh(argv, **kw):
            if "diff" in argv:
                return subprocess.CompletedProcess(argv, 0, "work-items/WI-0155-x.md", "")
            if "ls-tree" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real_sh(argv, **kw)

        c = mock.Mock(label="WI", namespace="wi-alloc", draw_cmd="poga work next",
                      renumber_hint="", redraw_hint="")
        c.dirname.return_value = "work-items"
        buf = io.StringIO()
        with mock.patch.object(session, "sh", side_effect=fake_sh), \
             mock.patch.object(session, "_counter_num_in_path", return_value=155), \
             mock.patch.object(session, "_counter_alloc_holds",
                               return_value={"0155": holder}), \
             mock.patch.object(session, "_coord_my_journal", return_value="mine"), \
             mock.patch.object(session, "_coord_identity", return_value=ident), \
             mock.patch.object(session, "_current_branch", return_value=ident):
            with redirect_stdout(buf):
                ok, msg = session._counter_land_gate(c, "tip", "parent")
        return ok, msg, buf.getvalue()

    def test_a_dead_holder_is_released_and_the_land_proceeds(self):
        holder = self._lane("poga-2", branch=True, worktree=True, live=False)
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-2", 3600)
        ok, msg, out = self._gate(holder)
        self.assertTrue(ok, msg)
        self.assertIn("released WI 0155", out)
        self.assertIn("no longer has a live session", out)
        self.assertNotIn("0155", session._coord_list("wi-alloc"))

    def test_a_live_holder_still_blocks(self):
        # The gate's whole reason to exist. Freeing a live lane's number would let two
        # lanes carry the same one.
        holder = self._lane("poga-2", branch=True, worktree=True, live=True)
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-2", 3600)
        ok, msg, _ = self._gate(holder)
        self.assertFalse(ok)
        self.assertIn("BLOCKED", msg)

    def test_the_release_is_never_silent(self):
        # A release that turns out wrong must be visible in the land output rather than
        # inferred later from a collision.
        holder = self._lane("poga-2", branch=True, worktree=True, live=False)
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-2", 3600)
        _, _, out = self._gate(holder)
        self.assertIn("worktree-poga-2", out)


class ThePayloadConfirmsItsOwnShapeTest(DeadHolderBase):
    """Property D (WI-0162)."""

    def _fire(self, payload):
        with mock.patch.object(session, "_read_hook_stdin", return_value=payload), \
             mock.patch.object(session, "_attention_write", return_value=True):
            with self.assertRaises(SystemExit):
                session.cmd_notify(None)

    def test_nothing_captured_is_still_a_guess_not_a_pass(self):
        v = session._notify_key_verdict()
        self.assertIn("GUESS", v)
        self.assertNotIn("CONFIRMED", v)

    def test_a_matching_key_is_confirmed_and_named(self):
        self._fire({"message": "needs you", "session_id": "s"})
        v = session._notify_key_verdict()
        self.assertIn("CONFIRMED", v)
        self.assertIn("'message'", v)

    def test_a_payload_with_no_known_key_reports_WRONG_and_lists_what_it_saw(self):
        # The outcome the guess was always vulnerable to, and the one a synthetic test
        # could never have surfaced.
        self._fire({"body": "needs you", "session_id": "s"})
        v = session._notify_key_verdict()
        self.assertIn("WRONG", v)
        self.assertIn("body", v)

    def test_the_record_is_still_written_when_the_key_is_wrong(self):
        # The whole reason a wrong guess was survivable: the record stands, it just has
        # no text. That must not change.
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value={"body": "x"}), \
             mock.patch.object(session, "_attention_write") as w:
            with self.assertRaises(SystemExit):
                session.cmd_notify(None)
        w.assert_called_once_with("")

    def test_the_capture_is_capped(self):
        for i in range(session.NOTIFY_PAYLOAD_KEEP + 5):
            self._fire({"message": f"m{i}"})
        self.assertEqual(len(session._notify_payloads()), session.NOTIFY_PAYLOAD_KEEP)

    def test_the_capture_is_untracked(self):
        # It is diagnostic residue, not history. `.session-state/` is gitignored.
        self.assertIn(".session-state", str(session._notify_payload_path()))

    def test_a_hook_that_cannot_be_read_never_breaks_the_session(self):
        with mock.patch.object(session, "_read_hook_stdin", side_effect=OSError("boom")):
            with self.assertRaises(SystemExit) as e:
                session.cmd_notify(None)
        self.assertEqual(e.exception.code, 0)

    def test_a_capture_failure_never_breaks_the_hook(self):
        with mock.patch.object(session, "atomic_write", side_effect=OSError("full")), \
             mock.patch.object(session, "_attention_write") as w:
            session._notify_record_payload({"message": "x"}, "message")
        # No raise. The attention record is the thing that matters; the capture is not.


if __name__ == "__main__":
    unittest.main()
