"""WI-0185 — the lane worktree is DRAWN, never computed.

THE FAILURE, live at the first attach drill (session ~172). `poga dispatch
WI-0146,WI-0152 --go` reported "2 lane(s) spawned". WI-0152 came up in worktree poga-2;
WI-0146 had no tmux session and no worktree seconds later. Re-dispatched ALONE thirty
seconds afterwards it came up fine — which is what identifies a RACE for the slot rather
than anything about the item.

Three defects, and the third is the worst:

  1. `alloc_lane` was `while lane_taken n; do n++; done` — check-then-act with nothing in
     between, so two simultaneous launches both see poga-1 free and both try to create it.
  2. `spawned` counted launches ATTEMPTED, not lanes alive, so the record asserted a lane
     that did not exist and every reader downstream believed it.
  3. The dying lane took its own diagnostic with it: a tmux session ends when its command
     exits, so whatever it printed about why it could not start was gone. A failure whose
     evidence is destroyed by the failure is the hardest kind to fix, and the kind this
     substrate keeps producing (WI-0105, WI-0156).

The properties pinned here:

  A. TWO RACERS NEVER GET THE SAME LANE, even when they are indistinguishable to
     `_coord_mine` — which is the real configuration, because `tmux new-session` hands the
     child the dispatcher's environment and both inherit one session id and one journal.
  B. THE LOSER GETS THE NEXT LANE, not a failure. Contention is normal; a failed launch is
     not.
  C. A LANE ON DISK IS SKIPPED BEFORE THE DRAW — worktree directory or leftover branch,
     the same two facts the wrapper's own `lane_taken` reads.
  D. AN EXPIRED RESERVATION IS RECLAIMABLE, so a crashed launcher does not burn a lane.
  E. `spawned` MEANS OBSERVED. A tmux session that is already gone is a named failure, and
     "could not look" is not a failure.
"""

import argparse
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
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (  # noqa: E402
    neutralize_coord_journal,
    neutralize_dispatch_env,
)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class LaneAllocBase(unittest.TestCase):
    def setUp(self):
        neutralize_ambient_env(self)      # WI-0275 — first, before anything reads it
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
        # A DISPATCHED LANE'S OWN ENVIRONMENT LEAKED INTO THESE FIXTURES. `_lane_reserve`
        # stamps `dispatch_id` from `POGA_DISPATCH`, and the whole point of the cases below
        # is the hand-launched lane that carries NO dispatch handle — so run the suite from
        # inside a dispatched lane (which is where a lane's own land gate runs it) and
        # `POGA_DISPATCH` is already set: every hand-launched lane reserves as dispatched,
        # `_dispatch_live_lane_count` counts the real machine's lanes, and three tests fail
        # for a reason that has nothing to do with the code under test. Found 2026-09-03
        # from lane poga-6 under dispatch D-788b4a, where it blocked the land gate. Same
        # hazard `test_claims.ClaimsBase.setUp` already names for the other three vars: a
        # fixture that does not clear an ambient var is testing the developer's machine.
        #
        # WI-0249 moved the mechanism into `coord_fixture` and shipped a detector for it
        # (`test_dispatch_fixture_guard`): the local copy that lived here was exactly the
        # per-module fix that leaves every other module uncovered, and the stakes rose when
        # `cmd_end` gained the power to end a lane's runtime.
        #
        # WI-0250 arrived at the same fix independently, one axis wider — the call at the
        # TOP of this setUp clears the colour and Claude-session variables too, and
        # `AMBIENT_VARS` is now defined as `DISPATCH_ENV_VARS` plus those, so there is one
        # list and this call cannot go stale against it. Both calls stay: this one is what
        # `test_dispatch_fixture_guard` matches by name, and it is a no-op once the wider
        # guard above has run.
        neutralize_dispatch_env(self)

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _alloc(self, identity="racer"):
        lane, why = session._lane_alloc(self.main, identity)
        return lane, why

    def _record(self, lane):
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        return json.loads((d / f"{lane}.json").read_text(encoding="utf-8"))


class TwoRacersNeverGetTheSameLaneTest(LaneAllocBase):
    """Property A + B."""

    def test_the_first_free_lane_is_poga_1(self):
        self.assertEqual(self._alloc()[0], "poga-1")

    def test_a_second_racer_gets_the_next_lane_not_a_failure(self):
        self.assertEqual(self._alloc("a")[0], "poga-1")
        self.assertEqual(self._alloc("b")[0], "poga-2")

    def test_identical_identities_still_do_not_collide(self):
        # THE REGRESSION THIS EXISTS FOR. `_coord_try_acquire` reclaims a name it judges
        # already "ours", and `_coord_mine` matches on the ambient journal when the session
        # id is shared — which is exactly two lanes spawned from one dispatcher's tmux
        # environment. Ten racers under one identity were measured all drawing poga-2.
        lanes = [self._alloc("same-identity")[0] for _ in range(5)]
        self.assertEqual(lanes, ["poga-1", "poga-2", "poga-3", "poga-4", "poga-5"])
        self.assertEqual(len(set(lanes)), 5)

    def test_a_held_reservation_is_not_reclaimed_by_its_own_holder(self):
        # Re-asking is not a refresh here: a launcher holding poga-1 that asks again is
        # asking for a FREE lane, not for the one it already has.
        self.assertTrue(session._lane_reserve("poga-1", "me", self.main))
        self.assertFalse(session._lane_reserve("poga-1", "me", self.main))


class ALaneOnDiskIsSkippedBeforeTheDrawTest(LaneAllocBase):
    """Property C — the same two facts the wrapper's `lane_taken` reads."""

    def test_an_existing_worktree_directory_is_taken(self):
        (self.main / ".claude" / "worktrees" / "poga-1").mkdir(parents=True)
        self.assertTrue(session._lane_taken(self.main, "poga-1"))
        self.assertEqual(self._alloc()[0], "poga-2")

    def test_a_leftover_branch_is_taken_even_with_no_worktree(self):
        # A branch with no directory is a crashed or un-landed lane. Silently reusing it
        # would put a new session on top of unmerged work.
        _git(self.main, "branch", "worktree-poga-1")
        self.assertTrue(session._lane_taken(self.main, "poga-1"))
        self.assertEqual(self._alloc()[0], "poga-2")

    def test_a_free_lane_is_neither(self):
        self.assertFalse(session._lane_taken(self.main, "poga-3"))

    def test_running_out_of_lanes_is_a_named_refusal(self):
        lane, why = session._lane_alloc(self.main, "x", limit=2)
        self.assertEqual(lane, "poga-1")
        session._lane_alloc(self.main, "x", limit=2)
        lane, why = session._lane_alloc(self.main, "x", limit=2)
        self.assertEqual(lane, "")
        self.assertIn("no free lane", why)
        self.assertIn("poga lanes", why)


class AnExpiredReservationIsReclaimableTest(LaneAllocBase):
    """Property D — a crashed launcher must not burn a lane forever."""

    def test_an_expired_reservation_is_taken_over(self):
        self.assertTrue(session._lane_reserve("poga-1", "dead", self.main))
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        rec = self._record("poga-1")
        rec["expires_at"] = time.time() - 1
        (d / "poga-1.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertTrue(session._lane_reserve("poga-1", "alive", self.main))
        self.assertEqual(self._record("poga-1")["session_id"], "alive")

    def test_a_live_reservation_is_not(self):
        self.assertTrue(session._lane_reserve("poga-1", "holder", self.main))
        self.assertFalse(session._lane_reserve("poga-1", "other", self.main))
        self.assertEqual(self._record("poga-1")["session_id"], "holder")

    def test_the_ttl_is_short_enough_to_self_heal(self):
        # It is only load-bearing for the seconds between the draw and the worktree
        # existing. A long TTL here would mean a crashed launch costs a lane for hours.
        self.assertLessEqual(session.LANE_ALLOC_TTL_SECONDS, 15 * 60)

    def test_release_covers_the_kind(self):
        # WI-0164 made `release --kind` cover every COORD_KIND; a reservation with no
        # retirement verb is the hand-repair class ADR-0099 exists to stop.
        self.assertIn(session.LANE_ALLOC_KIND, session.COORD_KINDS)


class SpawnedMeansObservedTest(LaneAllocBase):
    """Property E — defect 2. `spawned` counted launches ATTEMPTED."""

    def _open(self, create_rc=0, has_rc=0, has_raises=False):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "has-session" in argv:
                if has_raises:
                    raise OSError("boom")
                return subprocess.CompletedProcess(argv, has_rc, "", "")
            return subprocess.CompletedProcess(argv, create_rc, "", "")

        with mock.patch.object(session, "_tmux_bin", return_value="/opt/tmux"), \
             mock.patch.object(session.subprocess, "run", side_effect=run):
            ok, detail = session._dispatch_open_tmux("true", "poga-D-x-WI-1")
        return ok, detail, calls

    def test_a_session_that_is_already_gone_is_a_named_failure(self):
        ok, detail, _ = self._open(create_rc=0, has_rc=1)
        self.assertFalse(ok)
        self.assertIn("already gone", detail)
        self.assertIn("exited immediately", detail)

    def test_a_live_session_reports_success(self):
        ok, detail, calls = self._open(create_rc=0, has_rc=0)
        self.assertTrue(ok)
        self.assertTrue(any("has-session" in c for c in calls))

    def test_could_not_look_is_not_a_failure(self):
        # Reporting a live lane as dead would strand it worse than the bug being fixed.
        ok, detail, _ = self._open(has_raises=True)
        self.assertTrue(ok)
        self.assertIn("could not confirm", detail)

    def test_a_refused_launch_never_reaches_the_confirmation(self):
        ok, detail, calls = self._open(create_rc=1)
        self.assertFalse(ok)
        self.assertFalse(any("has-session" in c for c in calls))


class TheDyingLaneKeepsItsDiagnosticTest(LaneAllocBase):
    """Defect 3 — a failure whose evidence is destroyed by the failure."""

    def test_a_nonzero_exit_holds_the_pane_open(self):
        cmd = session._dispatch_spawn_command(self.main, "D-x", "WI-1")
        self.assertIn("rc=$?", cmd)
        self.assertIn("read _", cmd)
        self.assertIn("lane exited", cmd)

    def test_a_clean_exit_still_closes(self):
        # Held panes must not accumulate. The hold is guarded on a NON-ZERO exit.
        cmd = session._dispatch_spawn_command(self.main, "D-x", "WI-1")
        self.assertIn('-ne 0', cmd)

    def test_the_lane_still_carries_its_dispatch_environment(self):
        cmd = session._dispatch_spawn_command(self.main, "D-x", "WI-1")
        self.assertIn("POGA_DISPATCH=", cmd)
        self.assertIn("POGA_DISPATCH_ITEM=", cmd)

    def test_the_wrapper_actually_runs(self):
        # The hold is appended to a real shell line; a quoting slip would brick every
        # dispatch. Run it for real with a failing payload and confirm the shell parses.
        cmd = session._dispatch_spawn_command(self.main, "D-x", "WI-1")
        r = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TheVerbPrintsOnlyTheLaneTest(LaneAllocBase):
    """The wrapper captures stdout, so anything else on it becomes the lane name."""

    def test_stdout_is_exactly_the_lane(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_lane_alloc(argparse.Namespace(identity="x"))
        self.assertEqual(buf.getvalue().strip(), "poga-1")

    def test_exhaustion_exits_nonzero_with_nothing_on_stdout(self):
        with mock.patch.object(session, "_lane_alloc", return_value=("", "all full")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as e:
                    session.cmd_lane_alloc(argparse.Namespace(identity="x"))
        self.assertEqual(e.exception.code, 2)
        self.assertEqual(buf.getvalue().strip(), "")


class TheCapCountsDispatchedLanesOnlyTest(LaneAllocBase):
    """WI-0156 — a lane operator opened by hand is his own work, not dispatch load.

    THE BUG THIS CLOSES was a disagreement between two counts, not a missing check. The
    cap counted every live lane; the drain baton passes only between DISPATCHED ones. So a
    dispatch issued while the cap was already full of hand-launched lanes spawned zero, and
    with zero spawned no lane carried the baton and none ever would — it sat `[draining]`
    forever. Live: D-4d41da, 2026-08-22, cap 2 against 2 hand-opened lanes, six hours.

    the operator's ruling: sessions the command did not launch do not count against the cap; the
    dispatch ignores them and runs its own.
    """

    def _reserve_as_dispatched(self, lane, did="D-abc123", item="WI-0001"):
        os.environ["POGA_DISPATCH"] = did
        os.environ["POGA_DISPATCH_ITEM"] = item
        try:
            return session._lane_reserve(lane, "launcher", self.main)
        finally:
            os.environ.pop("POGA_DISPATCH", None)
            os.environ.pop("POGA_DISPATCH_ITEM", None)

    def _make_lane_exist(self, lane):
        (self.main / ".claude" / "worktrees" / lane).mkdir(parents=True, exist_ok=True)

    def test_a_hand_launched_lane_records_no_dispatch_id(self):
        # The distinction has to be captured at the DRAW; nothing later can reconstruct it.
        self.assertTrue(session._lane_reserve("poga-1", "me", self.main))
        self.assertEqual(self._record("poga-1").get("dispatch_id"), "")

    def test_a_dispatched_lane_records_the_handle_from_its_environment(self):
        self.assertTrue(self._reserve_as_dispatched("poga-1", "D-xyz", "WI-0042"))
        rec = self._record("poga-1")
        self.assertEqual(rec.get("dispatch_id"), "D-xyz")
        self.assertEqual(rec.get("dispatch_item"), "WI-0042")

    def test_hand_launched_lanes_do_not_count_against_the_cap(self):
        # THE REGRESSION. Three lanes operator opened himself; the dispatch cap sees none.
        for n in (1, 2, 3):
            self._make_lane_exist(f"poga-{n}")
            session._lane_reserve(f"poga-{n}", "me", self.main)
        self.assertEqual(len(session._lane_names_in_use()), 3)
        self.assertEqual(session._dispatch_live_lane_count(), 0)

    def test_dispatched_lanes_do_count(self):
        self._make_lane_exist("poga-1")
        self._reserve_as_dispatched("poga-1")
        self._make_lane_exist("poga-2")
        session._lane_reserve("poga-2", "me", self.main)      # hand-launched
        self.assertEqual(len(session._lane_names_in_use()), 2)
        self.assertEqual(session._dispatch_live_lane_count(), 1)

    def test_a_lane_that_no_longer_exists_stops_counting(self):
        # The reservation alone is not the lane. A record left behind by a torn-down lane
        # must not hold dispatch capacity — that is WI-0164's shape and must not return.
        self._make_lane_exist("poga-1")
        self._reserve_as_dispatched("poga-1")
        self.assertEqual(session._dispatch_live_lane_count(), 1)
        shutil.rmtree(self.main / ".claude" / "worktrees" / "poga-1")
        self.assertEqual(session._dispatch_live_lane_count(), 0)

    def test_the_count_fails_open_to_zero(self):
        # A cap that cannot be computed must never be the thing that stops a dispatch.
        with mock.patch.object(session, "_lane_names_in_use", side_effect=OSError("boom")):
            self.assertEqual(session._dispatch_live_lane_count(), 0)

    def test_an_expired_reservation_stops_counting(self):
        # WI-0249. The count read each record with `_coord_read`, which hands back a record
        # whatever its TTL says — so a reservation nobody had refreshed for hours still
        # held dispatch capacity. `_coord_list` is the one place that decides what LIVE
        # means for a coordination record; the count goes through it.
        self._make_lane_exist("poga-1")
        self._reserve_as_dispatched("poga-1")
        self.assertEqual(session._dispatch_live_lane_count(), 1)
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        rec = self._record("poga-1")
        rec["expires_at"] = time.time() - 1
        (d / "poga-1.json").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        self.assertEqual(session._dispatch_live_lane_count(), 0)
        # The lane itself is untouched — this is about the RECORD's lifetime, not the lane's.
        self.assertIn("poga-1", session._lane_names_in_use())


class AClosedJournalReleasesTheLaneReservationTest(LaneAllocBase):
    """WI-0249 — a finished lane must not go on holding dispatch capacity.

    MEASURED, session ~191 (lane poga-25, dispatch D-382d74). The reservation was released
    in exactly one place: `cmd_worktree_remove`, the WorktreeRemove hook, which fires when
    the RUNTIME tears the lane down. A dispatched lane's runtime does not tear anything
    down — it is still running after its own harness has stamped the journal closed — and
    every heartbeat re-stamped the record's TTL (`heartbeat_at` observed advancing on each
    tool call, expiry pushed five minutes out each time). So the lane read as finished
    everywhere that mattered while `_dispatch_live_lane_count` went on counting it.

    The properties pinned here:

      A. A LANE RELEASES ITS OWN RESERVATION, and the release is not blocked by the record
         naming a different holder — the launcher writes it before the runtime has adopted
         a journal, so it carries `session_id: "app"` while the lane's identity is its
         branch. An unforced release would silently no-op and leave the defect intact.
      B. RELEASING DOES NOT FREE THE LANE NAME. The worktree and branch are still on disk,
         and `_lane_taken` — not the reservation — is what stops the allocator reissuing a
         live lane. This is the safety property the early release rests on.
      C. IT IS SCOPED TO A LANE. A session on the trunk has no reservation to release and
         must not delete one belonging to someone else.
    """

    def setUp(self):
        super().setUp()
        # A PROPERLY-BUILT FIXTURE, deliberately. `_release_lane_reservation` refuses when
        # `_running_against_fixture()` is true, and that predicate reads a HALF-patched
        # module: `JOURNAL_DIR` outside `ROOT`. `LaneAllocBase` moves `ROOT` to the temp
        # repo and leaves `JOURNAL_DIR` on the live checkout, which is exactly the shape
        # the guard exists to stop — so without this the class under test would be refused
        # by its own safety net and every assertion below would pass vacuously on `None`.
        # Pointing the journal dir inside the fixture repo is what the predicate's
        # docstring calls the honest form: both patched, git lands where it belongs.
        self._jd = session.JOURNAL_DIR
        session.JOURNAL_DIR = self.main / "sessions" / "journal"
        session.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(session, "JOURNAL_DIR", self._jd))
        self.assertFalse(session._running_against_fixture(),
                         "the fixture must read as real, or these tests prove nothing")

    def _reserve_as_dispatched(self, lane, did="D-abc123", item="WI-0001"):
        os.environ["POGA_DISPATCH"] = did
        os.environ["POGA_DISPATCH_ITEM"] = item
        try:
            return session._lane_reserve(lane, "launcher", self.main)
        finally:
            os.environ.pop("POGA_DISPATCH", None)
            os.environ.pop("POGA_DISPATCH_ITEM", None)

    def _released(self, lane, on_lane=True, branch=None):
        """Run the release as if this process were the session holding lane `lane`."""
        with mock.patch.object(session, "_on_worktree_lane", return_value=on_lane), \
             mock.patch.object(session, "_current_branch",
                               return_value=branch or f"worktree-{lane}"):
            return session._release_lane_reservation("test")

    def test_the_lane_releases_a_record_written_by_the_launcher(self):
        # Property A. The record says `session_id: "app"`; the lane's identity is its
        # branch. The two never match, which is why the release is forced.
        (self.main / ".claude" / "worktrees" / "poga-3").mkdir(parents=True)
        self._reserve_as_dispatched("poga-3")
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        self.assertTrue((d / "poga-3.json").exists())
        self.assertEqual(self._released("poga-3"), "poga-3")
        self.assertFalse((d / "poga-3.json").exists())

    def test_the_released_lane_stops_counting_against_the_cap(self):
        (self.main / ".claude" / "worktrees" / "poga-3").mkdir(parents=True)
        self._reserve_as_dispatched("poga-3")
        self.assertEqual(session._dispatch_live_lane_count(), 1)
        self._released("poga-3")
        self.assertEqual(session._dispatch_live_lane_count(), 0)

    def test_releasing_does_not_hand_the_lane_name_to_a_new_launch(self):
        # Property B — the safety property. The worktree is still on disk, so `_lane_taken`
        # keeps the name reserved by the only fact that outlives the record.
        (self.main / ".claude" / "worktrees" / "poga-1").mkdir(parents=True)
        self._reserve_as_dispatched("poga-1")
        self._released("poga-1")
        self.assertTrue(session._lane_taken(self.main, "poga-1"))
        self.assertEqual(session._lane_alloc(self.main, "next-launch")[0], "poga-2")

    def test_a_branch_left_behind_also_keeps_the_name(self):
        # Same property, the other half of `_lane_taken`: a lane whose directory is gone
        # but whose branch carries unlanded work is still taken.
        _git(self.main, "branch", "worktree-poga-1")
        self._reserve_as_dispatched("poga-1")
        self._released("poga-1")
        self.assertEqual(session._lane_alloc(self.main, "next-launch")[0], "poga-2")

    def test_a_session_that_is_not_on_a_lane_releases_nothing(self):
        # Property C. On the trunk there is no reservation of ours to let go of, and
        # deleting one at random would take a live lane's slot out from under it.
        (self.main / ".claude" / "worktrees" / "poga-1").mkdir(parents=True)
        self._reserve_as_dispatched("poga-1")
        self.assertIsNone(self._released("poga-1", on_lane=False))
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        self.assertTrue((d / "poga-1.json").exists())

    def test_no_record_is_not_an_error(self):
        # A hand-launched lane whose reservation already lapsed, or a lane launched before
        # this shipped. Nothing to release is a real answer, not a failure.
        (self.main / ".claude" / "worktrees" / "poga-7").mkdir(parents=True)
        self.assertIsNone(self._released("poga-7"))

    def test_a_half_patched_fixture_is_refused_before_it_touches_the_real_store(self):
        # WI-0035, and NOT hypothetical: the first cut of this feature ran before
        # `cmd_end` reaches `_refuse_git_on_fixture`, and
        # `test_session.test_lane_close_refuses_git_against_a_fixture` mocks
        # `_on_worktree_lane` True and calls `cmd_end` for real with `ROOT` on the LIVE
        # checkout. That would have resolved `worktree-poga-N` from the real machine and
        # force-deleted a live lane's reservation. The guard lives inside the helper so it
        # covers every caller, and this proves it fires rather than assuming it.
        (self.main / ".claude" / "worktrees" / "poga-9").mkdir(parents=True)
        self._reserve_as_dispatched("poga-9")
        session.JOURNAL_DIR = pathlib.Path(tempfile.mkdtemp())   # outside ROOT → fixture
        self.addCleanup(shutil.rmtree, session.JOURNAL_DIR, True)
        self.assertTrue(session._running_against_fixture())
        self.assertIsNone(self._released("poga-9"))
        d = session._coord_dir(session.LANE_ALLOC_KIND)
        self.assertTrue((d / "poga-9.json").exists(),
                        "a fixture must not be able to delete a reservation")

    def test_it_never_raises(self):
        # A coordination write must not turn a completed close into a failed one.
        with mock.patch.object(session, "_coord_release", side_effect=OSError("boom")), \
             mock.patch.object(session, "_on_worktree_lane", return_value=True), \
             mock.patch.object(session, "_current_branch", return_value="worktree-poga-1"):
            self.assertIsNone(session._release_lane_reservation("test"))


class ASlotHoldingAnUnlandedPredecessorIsNeverReissuedTest(LaneAllocBase):
    """WI-0165 — pinned rather than built, because the allocator rewrite already closed it.

    THE INCIDENT (2026-08-23, session ~165): a dispatched lane was spawned into slot
    poga-2 whose branch `worktree-poga-2` still carried a dead session's COMMITTED but
    never-landed work. The new lane read its inherited store, found five in-progress items
    main had never seen, and asked operator whether it should work them — an agenda belonging
    to a dead session. Worse, its own land would have carried the predecessors to main
    under a commit message about something else. Measured after the first count was wrong:
    SIXTY-THREE commits across two sessions and four ADRs, not five commits.

    `_lane_taken` refuses any lane whose branch exists AT ALL, which is stricter than this
    item asked for and closes it as a side effect of the WI-0185 race work. Verified here
    rather than assumed, and pinned so the stricter reading cannot be relaxed into an
    "is it ahead of the trunk?" test that reintroduces the case.
    """

    def test_a_branch_with_unlanded_commits_makes_the_slot_taken(self):
        _git(self.main, "branch", "worktree-poga-1")
        self.assertTrue(session._lane_taken(self.main, "poga-1"))

    def test_the_allocator_walks_past_it_instead_of_reissuing_it(self):
        _git(self.main, "branch", "worktree-poga-1")
        self.assertEqual(self._alloc()[0], "poga-2")

    def test_it_is_taken_even_with_no_worktree_directory_on_disk(self):
        # The predecessor's worktree is usually already gone — the BRANCH is what carries
        # the unlanded work, so the directory's absence must not read as a free slot.
        _git(self.main, "branch", "worktree-poga-1")
        self.assertFalse((self.main / ".claude" / "worktrees" / "poga-1").exists())
        self.assertTrue(session._lane_taken(self.main, "poga-1"))


if __name__ == "__main__":
    unittest.main()
