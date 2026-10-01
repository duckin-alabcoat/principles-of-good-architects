"""Recovering the work stranded in a lane whose session is gone — WI-0122.

`reap-lanes` refuses an unmerged lane and tells the reader to `cd` into it and run
`session.py merge`. That instruction can only be executed by a session INSIDE the lane,
and a lane is stranded precisely because its session no longer exists — so the recovery
step was addressed to someone who was not there to read it, and five lanes across four
systems sat holding 14 commits nobody would ever land.

`recover-lanes` is the caller that closes it. What has to be pinned here is almost
entirely what it must REFUSE to touch, because the failure mode of an automatic land is
landing work out from under a session that is still working:

  - a lane whose heartbeat is inside its grace is LIVE, not dead;
  - a supervised lane whose watched pid is still alive is LIVE;
  - a lane with no sidecar, or a sidecar with no readable beat, is UNKNOWN — and unknown
    is never treated as dead ([`declare-what-a-check-assumes`]);
  - one live session among several dead ones vetoes the whole lane.

And the two shapes it must ACT on, which are the operator's two real cases: the session that was
walked away from (silence past the grace) and the one the runtime dropped (an `.ended`
exit marker with no stamped journal — a false close).

The land itself is deliberately NOT re-implemented, so what is pinned about it is that the
lane's OWN `session.py merge` is what gets invoked, with the lane as cwd.

stdlib unittest: python3 -m unittest tests.test_lane_recovery
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
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from coord_fixture import point_store_at  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@unittest.skipUnless(GIT, "git not available")
class RecoveryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / ".gitignore").write_text(".claude/\n.session-state/\n", encoding="utf-8")
        (self.main / "f.txt").write_text("base\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "CFG_RAW")}
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        session.CFG_RAW = {"trunk": "main"}
        # ADR-0148 D3 / WI-0427: `recover-lanes` resolves holder journals through
        # `_holder_journal_dirs` — `JOURNAL_DIR`, `_shared_work_root()`'s lanes and
        # `POGA_INVOKED_FROM` — none of which follow `ROOT`. Unpinned, `_run` globbed
        # the real checkout's `sessions/journal` (2,784 reads across four tests).
        point_store_at(self, self.main)

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lane(self, n=1, work=True):
        """A lane worktree on its own branch, holding a commit not on the trunk."""
        path = self.main / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-poga-{n}",
             str(path), "main")
        if work:
            (path / f"work-{n}.txt").write_text("real work\n", encoding="utf-8")
            _git(path, "add", "-A")
            _git(path, "commit", "-qm", f"feat: lane {n} work")
        return path

    def age_lane(self, path, age_sec=None):
        """Backdate a lane's CREATION time past the WI-0258 birth floor.

        Needed because a test lane is by construction a newborn — `git worktree add` ran
        milliseconds ago — and `lane-enterable` now refuses a lane too young for its
        liveness evidence to have appeared. Every test here that means "a lane somebody
        walked away from" has to actually say so rather than inherit it from how fast the
        fixture runs.

        BOTH sources `_lane_birth` can read are moved, which is the whole point of the
        helper. `st_birthtime` (macOS) is dragged down by setting an older mtime on the
        directory — the documented mutability the reaper's
        `test_a_dragged_birth_time_fails_in_the_SAFE_direction` pins — but that trick does
        nothing on Linux, where `_lane_birth` falls back to git's registration record. A
        helper that moved only the first would pass here and quietly stop aging anything
        on the members that use the fallback, which is the [`declare-what-a-check-assumes`]
        failure one level down: a fixture that works for a reason it does not state."""
        old = time.time() - (age_sec if age_sec is not None
                             else session.LANE_ENTER_MIN_AGE_SEC * 10)
        os.utime(path, (old, old))
        gitdir = self.main / ".git" / "worktrees" / pathlib.Path(path).name / "gitdir"
        if gitdir.is_file():
            os.utime(gitdir, (old, old))
        return path

    def beat(self, path, csid="abcd1234", age_min=1.0, source="", pid=None,
             machine="", ended=False, gone=False, live=True, sid=""):
        """Write a lane's liveness sidecar the way the heartbeat hook does.

        `sid` is the DURABLE session id the real hook always writes (`hooks.py` writes
        `session_id` on every one of its three paths). It defaults to empty to keep every
        existing caller on the tier-3 label it was written against; a test that is about
        WHICH session held the lane passes it and gets the real identity path."""
        d = path / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        if live:
            rec = {"last_beat": _ago(age_min)}
            if sid:
                rec["session_id"] = sid
            if source:
                rec["beat_source"] = source
            if pid is not None:
                rec["beat_pid"] = pid
            if machine:
                rec["beat_machine"] = machine
            (d / f"{csid}.live").write_text(json.dumps(rec), encoding="utf-8")
        if ended:
            (d / f"{csid}.ended").write_text(
                json.dumps({"ended": "2026-08-14T18:22:00+00:00"}), encoding="utf-8")
        if gone:
            (d / f"{csid}.gone").write_text(
                json.dumps({"gone": "2026-08-14T18:22:00+00:00"}), encoding="utf-8")
        return d


class DeathVerdictTest(RecoveryBase):
    """Three answers, and the third is the one that keeps this safe."""

    def test_an_exit_marker_with_no_stamped_journal_is_dead(self):
        """the operator's dropped-session case: SessionEnd fired, `end` never ran."""
        p = self.lane()
        self.beat(p, age_min=5, ended=True)
        verdict, reason = session._lane_death_verdict(p)
        self.assertEqual(verdict, "dead")
        self.assertIn("exited cleanly", reason)

    def test_an_observed_exit_is_dead(self):
        p = self.lane()
        self.beat(p, age_min=5, gone=True)
        self.assertEqual(session._lane_death_verdict(p)[0], "dead")

    def test_silence_past_the_grace_is_dead(self):
        """the operator's walked-away case: no marker, just silence."""
        p = self.lane()
        self.beat(p, age_min=session.REAP_CRASH_GRACE_MIN + 60)
        verdict, reason = session._lane_death_verdict(p)
        self.assertEqual(verdict, "dead")
        self.assertIn("silent", reason)

    def test_a_beat_inside_the_grace_is_live(self):
        """The refusal that matters most — a quiet session is not a dead one."""
        p = self.lane()
        self.beat(p, age_min=30)
        verdict, reason = session._lane_death_verdict(p)
        self.assertEqual(verdict, "live")
        self.assertIn("grace", reason)

    def test_a_supervised_session_whose_pid_is_alive_is_live(self):
        """A timer beat gets a 2h grace, so a stale one would otherwise read dead —
        but the watched process is still there, which outranks the clock."""
        p = self.lane()
        self.beat(p, age_min=session.REAP_SUPERVISED_GRACE_MIN + 60,
                  source="supervisor", pid=4242, machine="Runner")
        with mock.patch.object(session, "detect_machine", return_value="Runner"), \
             mock.patch.object(session, "_process_alive", return_value=True):
            self.assertEqual(session._lane_death_verdict(p)[0], "live")

    def test_no_sidecar_directory_is_unknown_not_dead(self):
        p = self.lane()
        verdict, reason = session._lane_death_verdict(p)
        self.assertEqual(verdict, "unknown")
        self.assertIn("nothing to judge", reason)

    def test_a_sidecar_with_no_live_file_is_unknown(self):
        p = self.lane()
        (p / ".session-state").mkdir(parents=True)
        self.assertEqual(session._lane_death_verdict(p)[0], "unknown")

    def test_an_unreadable_heartbeat_is_unknown_not_dead(self):
        """A `.live` whose beat cannot be parsed says nothing about death. Defaulting
        that to dead is how an unreadable file authorises a land."""
        p = self.lane()
        d = p / ".session-state"
        d.mkdir(parents=True)
        (d / "abcd1234.live").write_text(json.dumps({"last_beat": "not-a-date"}),
                                         encoding="utf-8")
        self.assertEqual(session._lane_death_verdict(p)[0], "unknown")

    def test_one_live_session_vetoes_a_lane_full_of_dead_ones(self):
        """A lane reused across sessions holds several sidecars; the lane is only
        recoverable if EVERY session in it is over."""
        p = self.lane()
        self.beat(p, csid="dead0001", age_min=5, ended=True)
        self.beat(p, csid="live0002", age_min=10)
        self.assertEqual(session._lane_death_verdict(p)[0], "live")


class RecoverLanesTest(RecoveryBase):
    """The verb: which lanes it drives, and what it does when the land refuses."""

    def _run(self, **kw):
        args = argparse.Namespace(dry_run=kw.pop("dry_run", False),
                                  lane=kw.pop("lane", None))
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_recover_lanes(args)
        return buf.getvalue()

    def test_it_drives_the_lanes_own_merge_in_the_lane(self):
        """The land is not re-implemented — the lane's own harness is invoked, with the
        lane as cwd. Anything else is a second land path built by accident."""
        p = self.lane()
        self.beat(p, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess(
                                   [], 0, "land:    ok\n", "")) as run:
            out = self._run()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(pathlib.Path(run.call_args[0][0]).resolve(), p.resolve())
        self.assertIn("RECOVERED", out)

    def test_a_live_lane_is_never_driven(self):
        """The whole safety argument in one assertion."""
        p = self.lane()
        self.beat(p, age_min=30)
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run()
        run.assert_not_called()
        # WI-0140: the tag used to be `[LIVE]`, which is `lane-enterable`'s word for a
        # different question. Positive evidence of life and no evidence at all are both
        # "not provably dead" here, and they are now told apart by name.
        self.assertIn("[SIGNS OF LIFE]", out)
        self.assertNotIn("[LIVE]", out, "must not reuse the occupancy verb's vocabulary")
        self.assertIn("left exactly as it was", out)

    def test_an_unprovable_lane_is_never_driven_and_says_so(self):
        p = self.lane()
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run()
        run.assert_not_called()
        self.assertIn("[NO EVIDENCE]", out)

    def test_a_blocked_land_is_reported_as_blocked_and_nothing_is_lost(self):
        """A rebase conflict or a red gate must not read as a recovery."""
        p = self.lane()
        self.beat(p, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess(
                                   [], 0, "land:    BLOCKED — rebase onto main conflicts.\n", "")):
            out = self._run()
        self.assertIn("BLOCKED", out)
        self.assertNotIn("RECOVERED", out)
        # the branch is still there, holding its commit
        heads = subprocess.run([GIT, "-C", str(self.main), "branch", "--list",
                                "worktree-poga-1"], capture_output=True, text=True).stdout
        self.assertIn("worktree-poga-1", heads)

    def test_a_nonzero_exit_is_not_a_recovery(self):
        p = self.lane()
        self.beat(p, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess([], 1, "", "boom")):
            out = self._run()
        self.assertIn("BLOCKED", out)

    def test_dry_run_lands_nothing(self):
        p = self.lane()
        self.beat(p, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run(dry_run=True)
        run.assert_not_called()
        self.assertIn("dry-run", out)

    def test_the_recover_row_names_the_lane_durably(self):
        """WI-0157 / ADR-0129 D2. Mutation-proved: reverting this one row to a bare slot
        left `test_lane_identity`, `test_lane_recovery` and `test_claims` all green.

        This row is printed about a lane whose session is GONE, which is precisely when a
        reader has least other context for working out which lane it is — and the slot
        has by then very likely been reissued."""
        p = self.lane(2)
        self.beat(p, age_min=5, ended=True, sid="20260912T1220Z-devbox-e743")
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run(dry_run=True)
        run.assert_not_called()
        row = next(l for l in out.splitlines() if l.startswith("recover:"))
        self.assertIn("devbox\u00b7e743", row,
                      f"the durable identity must lead the row: {row!r}")
        self.assertIn("[poga-2]", row, "and the slot survives as the address")
        self.assertFalse(row.split(":", 1)[1].lstrip().startswith("poga-2"))

    def test_lane_filter_restricts_the_sweep(self):
        p1 = self.lane(1)
        p2 = self.lane(2)
        self.beat(p1, age_min=5, ended=True)
        self.beat(p2, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self._run(lane="poga-2")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(pathlib.Path(run.call_args[0][0]).resolve(), p2.resolve())

    # ---- WI-0140: the verb states the question it asked --------------------------
    # Two verbs ask genuinely different liveness questions and both used to answer with
    # the bare word "live", so two minutes apart the substrate told one operator that
    # lane poga-2 was LIVE and that it was enterable. Unifying the predicates would make
    # one verb wrong; naming the questions is the repair, and these pin the naming.

    def test_a_held_lane_prints_the_question_this_verb_asked(self):
        """The bare verdict was the defect. Recovery LANDS work, so its bar is proof of
        death — and the line that holds a lane back has to say so, or `poga resume`
        answering differently about the same lane reads as a contradiction."""
        p = self.lane(1)
        self.beat(p, age_min=30)
        with mock.patch.object(session, "_drive_lane_merge"):
            out = self._run()
        self.assertIn("held — `recover-lanes` asks: is the session PROVABLY OVER?", out)

    def test_the_question_header_is_absent_when_nothing_is_held(self):
        """The header is a caption on the held list, not a banner. A sweep that held
        nothing back has no disagreement to explain."""
        p = self.lane(1)
        self.beat(p, age_min=5, ended=True)
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess([], 0, "", "")):
            out = self._run()
        self.assertNotIn("PROVABLY OVER", out)
        self.assertNotIn("held:", out)

    def test_signs_of_life_and_no_evidence_are_told_apart_and_neither_says_live(self):
        """`[LIVE]`/`[UNKNOWN]` borrowed `lane-enterable`'s word for occupancy. Positive
        evidence of a beat and no evidence at all are both "not provably dead" here, and
        they are now named for the evidence rather than for the other verb's verdict."""
        p1 = self.lane(1)
        self.beat(p1, age_min=30)          # a beat inside its grace: signs of life
        self.lane(2)                        # no sidecar at all: nothing to judge from
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run()
        run.assert_not_called()
        # WI-0157: the row now leads with the lane's DURABLE identity and carries the
        # slot in brackets as the address, so it is keyed on the address rather than on
        # "the second whitespace token" — which was only ever true while the slot sat in
        # the reading position.
        held = [l for l in out.splitlines() if l.startswith("held:")]
        lines = {s: l for l in held for s in ("poga-1", "poga-2") if f"[{s}]" in l}
        self.assertIn("[SIGNS OF LIFE]", lines["poga-1"])
        self.assertIn("[NO EVIDENCE]", lines["poga-2"])
        for slot, line in lines.items():
            body = line.split(":", 1)[1].lstrip()
            self.assertFalse(body.startswith(slot),
                             f"the slot is an address, not the name: {line!r}")
        self.assertNotIn("[LIVE]", out,
                         "must not reuse `lane-enterable`'s vocabulary for occupancy")
        self.assertNotIn("[UNKNOWN]", out)

    def test_every_held_lane_is_routed_to_resume_by_lane_number(self):
        """The routing half, and the reason the item was not cosmetic: in the observed
        incident the refusal read as a dead end while `resume` was open the whole time
        and was how the lane actually got landed. The number matters — `poga resume`
        takes a lane NUMBER, so printing the directory name would be advice that fails."""
        p = self.lane(2)
        self.beat(p, age_min=30)
        with mock.patch.object(session, "_drive_lane_merge"):
            out = self._run()
        self.assertIn("nothing is sitting in this lane, though", out)
        self.assertIn("`poga resume 2`", out)
        self.assertNotIn("poga resume poga-2", out)
        self.assertIn("session.py merge", out)

    def test_the_summary_names_the_set_it_counted_over(self):
        """`0 held` read as a claim about lanes in general while `poga resume` named nine
        lanes as held. Both were true; the pair was unreadable because the denominator
        was invisible. This verb only ever considers UNMERGED lanes, so it says so."""
        p1 = self.lane(1)
        self.beat(p1, age_min=30)           # held: signs of life
        self.lane(2)                        # held: no evidence
        p3 = self.lane(3)
        self.beat(p3, age_min=5, ended=True)  # provably over
        with mock.patch.object(session, "_drive_lane_merge",
                               return_value=subprocess.CompletedProcess([], 0, "", "")):
            out = self._run()
        self.assertIn("recover-lanes: of 3 unmerged lane(s)", out)
        self.assertIn("1 provably over", out)
        self.assertIn("2 held for lack of proof", out)

    def test_a_merged_lane_is_not_this_verbs_business(self):
        """`reap-lanes` owns merged lanes; this one only ever touches stranded work."""
        self.lane(1, work=False)     # branch is an ancestor of the trunk
        with mock.patch.object(session, "_drive_lane_merge") as run:
            out = self._run()
        run.assert_not_called()
        self.assertIn("nothing stranded", out)


class LaneEnterableTest(RecoveryBase):
    """The gate behind `poga resume`. Its one absolute refusal is a lane a live session
    already holds — two sessions sharing a worktree share an index, a branch and a
    journal directory, which is the one thing the lane model cannot survive."""

    def _run(self, lane=None):
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                session.cmd_lane_enterable(argparse.Namespace(lane=lane))
        except SystemExit as exc:
            code = exc.code
        return code, buf.getvalue()

    def test_a_stranded_lane_can_be_entered(self):
        """The whole point: a lane whose session walked away is re-enterable."""
        self.age_lane(self.lane(1))
        code, out = self._run("poga-1")
        self.assertEqual(code, 0)
        self.assertIn("yes", out)

    def test_a_lane_held_by_a_live_session_is_refused(self):
        p = self.lane(1)
        with mock.patch.object(session, "_scan_lanes", return_value=[
                {"name": "poga-1", "path": p, "branch": "worktree-poga-1",
                 "status": "live", "dirty": False, "is_self": False}]):
            code, out = self._run("poga-1")
        self.assertEqual(code, 1)
        self.assertIn("cannot share one worktree", out)

    def test_the_lane_you_are_standing_in_is_refused_by_name(self):
        """A different sentence from someone else's live lane — the fix is different."""
        p = self.lane(1)
        with mock.patch.object(session, "_scan_lanes", return_value=[
                {"name": "poga-1", "path": p, "branch": "worktree-poga-1",
                 "status": "live", "dirty": False, "is_self": True}]):
            code, out = self._run("poga-1")
        self.assertEqual(code, 1)
        self.assertIn("already in it", out)

    def test_an_unknown_lane_is_refused(self):
        self.lane(1)
        code, out = self._run("poga-9")
        self.assertEqual(code, 1)
        self.assertIn("no lane named", out)

    def test_an_unprovable_lane_is_still_enterable(self):
        """The deliberate asymmetry with recover-lanes: recovery LANDS, so it demands
        proof of death; resume only OPENS, so absence of a live holder is enough. A lane
        with no sidecar at all is precisely the walked-away case resume exists to rescue,
        and refusing it would leave that work unreachable forever."""
        p = self.age_lane(self.lane(1))
        self.assertEqual(session._lane_death_verdict(p)[0], "unknown")
        self.assertEqual(self._run("poga-1")[0], 0)

    def test_bare_lists_every_lane_with_its_answer(self):
        self.age_lane(self.lane(1))
        self.age_lane(self.lane(2))
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("poga-1", out)
        self.assertIn("poga-2", out)

    # ---- WI-0140 ------------------------------------------------------------------

    def test_the_bare_list_prints_the_question_this_verb_asked(self):
        """This verb only OPENS a lane, so absence of a holder is enough. Saying so is
        what keeps `recover-lanes` holding the same lane from reading as a contradiction."""
        self.age_lane(self.lane(1))
        code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("`lane-enterable` asks: is a session IN this lane right now?", out)

    def test_an_occupied_lane_is_refused_as_occupied_not_as_live(self):
        """`live` was this verb's word AND the other verb's word for a different
        question. Here the refusal is about occupancy, so it names occupancy."""
        p = self.lane(1)
        with mock.patch.object(session, "_scan_lanes", return_value=[
                {"name": "poga-1", "path": p, "branch": "worktree-poga-1",
                 "status": "live", "dirty": False, "is_self": False}]):
            code, out = self._run()
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.startswith("poga-1"))
        self.assertIn("OCCUPIED: a session is in this worktree now", line)
        self.assertNotIn("live", line)


class LaneBirthWindowTest(RecoveryBase):
    """WI-0258, half one: the verb had two answers where the world has three.

    `lane-enterable` decided purely on `_scan_lanes`' `live` field, and `live` requires
    positive evidence — this session, or a worktree lock held by a living pid. A lane that
    has not produced that evidence YET is not a lane nobody is in; it is a lane nobody has
    been able to look inside of. Folding those together is
    [`declare-what-a-check-assumes`]'s exact failure, and the direction it folded is the
    dangerous one: "yes, enter," into a worktree a session is starting up in.

    What is pinned here is that the third answer EXISTS, is distinguishable from the other
    two by exit code, and CLEARS ITSELF — a birth refusal that did not expire would trade a
    rare collision for a permanent one."""

    def _run(self, lane=None):
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                session.cmd_lane_enterable(argparse.Namespace(lane=lane))
        except SystemExit as exc:
            code = exc.code
        return code, buf.getvalue()

    def test_a_newborn_lane_is_refused(self):
        """The defect, reproduced. `git worktree add` ran milliseconds ago, nothing holds
        a lock yet — and the old gate answered `yes`."""
        self.lane(1)                      # deliberately NOT aged
        code, out = self._run("poga-1")
        self.assertEqual(code, 2)
        self.assertIn("WAIT", out)

    def test_the_birth_refusal_is_a_wait_not_a_ban(self):
        """Same lane, same everything, aged past the floor: enterable. If this ever fails
        while the test above passes, the fix has turned a race into a lockout."""
        p = self.lane(1)
        self.assertEqual(self._run("poga-1")[0], 2)
        self.age_lane(p)
        self.assertEqual(self._run("poga-1")[0], 0)

    def test_wait_and_occupied_do_not_share_an_exit_code(self):
        """A caller has to be able to tell `retry in a moment` from `someone is in there`.
        Collapsing them is how a transient refusal teaches an operator to ignore a
        permanent one."""
        p = self.lane(1)
        young, _ = self._run("poga-1")
        with mock.patch.object(session, "_scan_lanes", return_value=[
                {"name": "poga-1", "path": p, "branch": "worktree-poga-1",
                 "status": "live", "dirty": False, "is_self": False}]):
            occupied, _ = self._run("poga-1")
        self.assertEqual((young, occupied), (2, 1))

    def test_an_unreadable_birth_time_is_refused_not_waved_through(self):
        """Unknown is never `safe to enter`. `_lane_birth`'s own third answer
        (`unavailable`) has to reach the operator as a third answer, not as a `yes`."""
        self.age_lane(self.lane(1))
        with mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")):
            code, out = self._run("poga-1")
        self.assertEqual(code, 2)
        self.assertIn("unreadable", out)

    def test_the_bare_list_says_wait_for_a_newborn(self):
        """The listing is what `poga resume` prints with no argument, so the third state
        has to be visible there too — a lane that reads `yes` in the list and refuses on
        entry is a surface that disagrees with itself."""
        self.lane(1)
        code, out = self._run()
        self.assertEqual(code, 0)
        line = next(l for l in out.splitlines() if l.startswith("poga-1"))
        self.assertIn("WAIT", line)

    def test_the_wait_reason_tells_the_operator_how_long(self):
        """A refusal that does not say when to retry gets retried immediately, or not at
        all."""
        self.lane(1)
        _code, out = self._run("poga-1")
        self.assertIn("retry in", out)


class LaneLivenessEvidenceTest(RecoveryBase):
    """WI-0258, half two — and the larger half, though the item did not see it.

    The item reads the hole as a race: the lock arrives `seconds after poga creates the
    worktree`. In practice the lock lands a fraction of a second after
    registration, because Claude creates AND locks the worktree itself. The real hole is
    not that window. It is that `poga` has THREE lane-creation paths and only ONE of them
    produces a lock at all: the resident-runtime pad and the ADR-0082 D4 no-worktree-flag
    path (codex, antigravity) both `create_lane_worktree` and exec a runtime that never
    locks anything. For those lanes the old gate said `enterable` for the entire life of
    the session sitting in them.

    The ADR-0054 heartbeat is the runtime-agnostic evidence, and it exists for exactly
    those lanes because `prep_and_supervise` starts `session.py supervise --detach` before
    it execs. These tests pin that the gate now reads it, and that reading it did not cost
    the walked-away case the verb exists to serve."""

    def _run(self, lane=None):
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                session.cmd_lane_enterable(argparse.Namespace(lane=lane))
        except SystemExit as exc:
            code = exc.code
        return code, buf.getvalue()

    def test_a_beating_lane_with_no_lock_is_refused(self):
        """THE regression test. No worktree lock — this lane`s runtime does not take one —
        but a session is demonstrably in there, beating. Before the fix this returned 0."""
        p = self.age_lane(self.lane(1))
        self.beat(p, age_min=1)
        code, out = self._run("poga-1")
        self.assertEqual(code, 1)
        self.assertIn("cannot share one worktree", out)

    def test_the_refusal_names_the_heartbeat_as_its_probe(self):
        """[`capture-the-probe`]. A refusal on a heartbeat and a refusal on a lock cover
        different runtimes; an operator who cannot tell which answered cannot judge it."""
        p = self.age_lane(self.lane(1))
        self.beat(p, age_min=1)
        self.assertIn("heartbeat", self._run("poga-1")[1])

    def test_scan_lanes_records_which_evidence_said_live(self):
        """The field, at its source — `live` is now three different claims wearing one
        word, and the consumer has to be able to take them apart."""
        p = self.age_lane(self.lane(1))
        self.beat(p, age_min=1)
        lane = next(l for l in session._scan_lanes() if l["name"] == "poga-1")
        self.assertEqual((lane["status"], lane["live_by"]), ("live", "heartbeat"))

    def test_a_stale_heartbeat_does_not_hold_the_lane(self):
        """The cost check. `HEARTBEAT_STALE_MIN` is what keeps the walked-away lane
        re-enterable — if a dead session`s last beat held its lane forever, this fix would
        have recreated WI-0122, the very thing `resume` was built to solve."""
        p = self.age_lane(self.lane(1))
        self.beat(p, age_min=session.HEARTBEAT_STALE_MIN + 5)
        code, _out = self._run("poga-1")
        self.assertEqual(code, 0)

    def test_an_ended_session_does_not_hold_the_lane(self):
        """A closed session`s sidecar is not an occupant, however fresh its last beat."""
        p = self.age_lane(self.lane(1))
        self.beat(p, age_min=1, ended=True)
        self.assertEqual(self._run("poga-1")[0], 0)

    def test_widening_live_only_ever_holds_a_lane_back(self):
        """The blast-radius argument, asserted rather than reasoned: `_scan_lanes` feeds
        the reaper and `recover-lanes` too, so the safety claim is that the new evidence
        can only move a lane INTO `live` (never touched) and never out of it."""
        p = self.age_lane(self.lane(1))
        before = next(l for l in session._scan_lanes() if l["name"] == "poga-1")
        self.assertEqual(before["status"], "unmerged")
        self.beat(p, age_min=1)
        after = next(l for l in session._scan_lanes() if l["name"] == "poga-1")
        self.assertEqual(after["status"], "live")


class CrossVerbQuestionTest(RecoveryBase):
    """WI-0140, the whole item in one test. ONE lane, asked by both verbs in the same
    breath, getting OPPOSITE answers — which is correct, because they were asked
    different questions: `recover-lanes` holds it back (it lands work, so it needs proof
    the session is over, and a beat inside the grace is not that proof) while
    `lane-enterable` waves it through (it only opens a lane, and nothing is sitting in
    it). Observed on lane poga-2, two minutes apart, when both surfaces printed the bare
    word "live" and the substrate appeared to contradict itself.

    What is pinned is not agreement — forcing that would make one verb wrong — but that
    each output NAMES ITS OWN QUESTION, so a reader holding both can see two answers to
    two questions rather than two answers to one."""

    def _recover(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch.object(session, "_drive_lane_merge") as run:
                session.cmd_recover_lanes(argparse.Namespace(dry_run=False, lane=None))
        run.assert_not_called()
        return buf.getvalue()

    def _enterable(self):
        buf = io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf):
                session.cmd_lane_enterable(argparse.Namespace(lane=None))
        except SystemExit as exc:
            code = exc.code
        return code, buf.getvalue()

    def test_the_two_verbs_disagree_about_one_lane_and_each_names_its_question(self):
        p = self.age_lane(self.lane(2))
        self.beat(p, age_min=30)        # a live beat inside its grace, work unlanded
        # The premise: the predicates really do disagree about this lane.
        self.assertEqual(session._lane_death_verdict(p)[0], "live")
        self.assertEqual([l["status"] for l in session._scan_lanes()
                          if l["name"] == "poga-2"], ["unmerged"])

        rec = self._recover()
        code, ent = self._enterable()

        # recover-lanes: NO, and it says which question it answered.
        self.assertIn("held — `recover-lanes` asks: is the session PROVABLY OVER?", rec)
        held = next(l for l in rec.splitlines() if l.startswith("held:"))
        self.assertIn("poga-2", held)
        self.assertIn("[SIGNS OF LIFE]", held)

        # lane-enterable: YES, and it says which question it answered.
        self.assertEqual(code, 0)
        self.assertIn("`lane-enterable` asks: is a session IN this lane right now?", ent)
        answer = next(l for l in ent.splitlines() if l.startswith("poga-2"))
        self.assertIn("yes", answer)

        # Neither borrows the other's vocabulary, which is what made the pair read as a
        # contradiction rather than as two answers.
        self.assertNotIn("[LIVE]", rec)
        self.assertNotIn("live", answer)

        # And the disagreement resolves to a route instead of a dead end.
        self.assertIn("`poga resume 2`", rec)


class IdleLiveLaneAdvisoryTest(RecoveryBase):
    """WI-0172 — the lane that is alive, quiet, and holding work nobody can reach.

    OBSERVED, poga-2, 2026-09-05. Two commits authored at 20:08 and 20:10 sat unlanded.
    `recover-lanes --lane poga-2` answered *"nothing stranded — no unmerged orphan lanes"*
    and `reap-lanes` answered *"live: poga-2 (live session) — protected."* Both were
    CORRECT: the lock records pid 84498, a `claude` still running, so `_scan_lanes`
    classified the lane `live` and every consumer honoured that. But the heartbeat had
    stopped at 20:09:33 — 2h18m of silence with the process alive — and ADR-0054 beats on
    tool USE, so an idle window at a prompt emits nothing.

    The conservatism is right and must stay right: folding this into `dead` would land
    work out from under a quiet session, which is the session-74 false reap. So what is
    pinned here is a REPORT and the absence of an action:

      - both verbs print a distinct advisory row naming the lane and its commit count;
      - the startup banner — the list a session actually reads, and the surface where this
        lane appeared on NO line at all — names it too;
      - the lane's status stays `live`, nothing is reaped, and no land is driven;
      - a lane that is merely beating, or that holds nothing unlanded, is not advised
        (the cost check: a row that fires on every working lane is noise within a day).
    """

    def idle_live(self, n=2, silent_min=None, beat=True, work=True, pid=84498):
        """The exact observed state: a lane whose lock pid is alive and whose heartbeat
        stopped hours ago, holding a commit the trunk does not have."""
        path = self.age_lane(self.lane(n, work=work))
        if beat:
            age = (silent_min if silent_min is not None
                   else session.LANE_IDLE_ADVISORY_MIN + 18)
            self.beat(path, age_min=age)
        _git(self.main, "worktree", "lock", "--reason",
             f"claude session poga-{n} (pid {pid})", str(path))
        return path

    def _scan(self):
        with mock.patch.object(session, "_pid_alive", return_value=True):
            return {l["name"]: l for l in session._scan_lanes()}

    def _reap(self, dry_run=False):
        buf = io.StringIO()
        with redirect_stdout(buf), \
             mock.patch.object(session, "_pid_alive", return_value=True):
            session.cmd_reap_lanes(argparse.Namespace(dry_run=dry_run))
        return buf.getvalue()

    def _recover(self, lane=None):
        buf = io.StringIO()
        with redirect_stdout(buf), \
             mock.patch.object(session, "_pid_alive", return_value=True), \
             mock.patch.object(session, "_drive_lane_merge") as run:
            session.cmd_recover_lanes(argparse.Namespace(dry_run=False, lane=lane))
        return buf.getvalue(), run

    def _banner(self):
        with mock.patch.object(session, "_pid_alive", return_value=True):
            return "\n".join(session._stranded_lane_lines())

    # ── the premise ────────────────────────────────────────────────────────────────────

    def test_the_state_really_is_the_one_the_item_describes(self):
        """Asserted rather than assumed: `live` by the worktree LOCK, not by a heartbeat,
        with unlanded commits. If this drifts, every test below is testing something else."""
        self.idle_live()
        lane = self._scan()["poga-2"]
        self.assertEqual((lane["status"], lane["live_by"]), ("live", "worktree-lock"))
        self.assertFalse(session._tree_has_live_session(pathlib.Path(lane["path"])),
                         "the heartbeat is stale — only the lock says live")
        self.assertFalse(session._lane_work_is_on_trunk(lane["branch"], "main"))

    # ── the row, on each of the three surfaces ─────────────────────────────────────────

    def test_reap_lanes_prints_the_advisory_row(self):
        """`protected.` was the whole report. It is still protected; it is no longer the
        whole report."""
        self.idle_live()
        out = self._reap()
        self.assertIn("LIVE LANES HOLDING UNLANDED WORK", out)
        # WI-0157: the row is found by the SLOT IN BRACKETS, its address, because the
        # reading position now holds the lane's durable name.
        row = next(l for l in out.splitlines() if "[poga-2]" in l and l.startswith("  !"))
        self.assertIn("1 commit(s) not on main", row)
        self.assertIn("silent 2.3h", row)
        self.assertIn("worktree-poga-2", row)
        self.assertFalse(row.lstrip("! ").startswith("poga-2"),
                         f"a recycled slot is standing as the lane's name: {row!r}")

    def test_recover_lanes_prints_the_advisory_rather_than_a_clean_bill(self):
        """THE regression test. The verb still refuses to act — that is the safety
        property — but it may not answer "nothing stranded" over work it can see."""
        self.idle_live()
        out, _run = self._recover(lane="poga-2")
        advisory = next(l for l in out.splitlines() if l.startswith("advisory: "))
        # WI-0157: the lane is still named on this line; it is no longer named by the
        # slot, which is recycled and so cannot say WHICH session is holding the work.
        self.assertIn("[poga-2]", advisory)
        self.assertFalse(advisory.split(":", 1)[1].lstrip().startswith("poga-2"))
        self.assertIn("1 commit(s) not on main", out)
        # The old sentence survives, qualified. A bare "nothing stranded" next to the row
        # above would still be read as "there is nothing here".
        self.assertIn("1 live lane(s) hold unlanded commits", out)

    def test_the_startup_banner_names_it(self):
        """The surface where it was invisible ENTIRELY: not `unmerged`, not `dirty`, and
        `_tree_has_live_session` false — so it was not even on the `active:` line."""
        self.idle_live()
        banner = self._banner()
        self.assertIn("idle:", banner)
        self.assertIn("poga-2", banner)
        self.assertIn("1 commit(s) not on main", banner)

    def test_all_three_surfaces_describe_the_lane_in_the_same_words(self):
        """P16 / WI-0140: an operator reading all three in the same minute is holding
        three views of one lane, and they must not have three vocabularies for it."""
        self.idle_live()
        # The identity is tier 3 here (`beat()` writes no session-id, so this fixture's
        # lane has no session to name) — which is exactly the case that must still be a
        # NAME and not a bare slot. The three surfaces share the sentence either way.
        note = ("test lane 2 [poga-2] — LIVE (worktree-lock) but silent 2.3h: "
                "1 commit(s) not on main")
        for surface in (self._reap(), self._recover()[0], self._banner()):
            self.assertIn(note, surface)

    # ── and nothing is touched ─────────────────────────────────────────────────────────

    def test_the_status_stays_live_and_no_verb_touches_the_lane(self):
        """The whole safety argument in one test: reporting gained a row, no verb gained
        a power. Landing under a live session is the harm this design refuses."""
        path = self.idle_live()
        branch_before = _out_branches(self.main)
        out, run = self._recover()
        run.assert_not_called()
        self._reap()
        self.assertEqual(self._scan()["poga-2"]["status"], "live")
        self.assertTrue(path.is_dir(), "the lane worktree is still there")
        self.assertEqual(_out_branches(self.main), branch_before)
        self.assertFalse([l for l in out.splitlines()
                          if l.startswith("recover:") and "poga-2" in l],
                         "no recover row may name this lane at all")

    def test_the_advice_routes_through_the_owner_never_around_it(self):
        """ADR-0099 D3 / RC2: every line is executable by the reader. There is
        deliberately no command here that lands the lane from outside — that is the land
        the design refuses, and WI-0141 / WI-0099 own the consent-to-land question."""
        self.idle_live()
        out = self._reap()
        self.assertIn("cd <lane> && python3 session.py merge", out)
        self.assertIn("recover-lanes --lane <name>", out)
        self.assertNotIn("git branch -D", out)
        self.assertNotIn("git -C ", out)

    # ── the cost check: what must NOT be advised ───────────────────────────────────────

    def test_a_beating_lane_is_not_advised(self):
        """The row must not fire on a session that is simply working. `heartbeat` is a
        distinct liveness evidence and excludes itself."""
        self.idle_live(silent_min=1)
        for surface in (self._reap(), self._recover()[0], self._banner()):
            self.assertNotIn("poga-2 — LIVE", surface)

    def test_silence_inside_the_threshold_is_not_advised(self):
        """The boundary, at the named constant rather than at a literal — a session idle
        between turns is very much alive, and 15m of quiet is not a report."""
        self.idle_live(silent_min=session.LANE_IDLE_ADVISORY_MIN - 5)
        self.assertNotIn("poga-2 — LIVE", self._reap())

    def test_a_live_lane_holding_nothing_unlanded_is_not_advised(self):
        """Silence alone is not the harm. A quiet lane with its work on the trunk is a
        lane with nothing to lose, and advising on it would make the row meaningless."""
        self.idle_live(work=False)
        self.assertNotIn("poga-2 — LIVE", self._reap())

    def test_this_sessions_own_lane_is_never_advised(self):
        """A session's own unlanded work is work in progress, not a stranding — the same
        exclusion `_stranded_lane_lines` has made since session ~129."""
        path = self.idle_live()
        with mock.patch.object(session, "_pid_alive", return_value=True):
            lanes = [dict(l, is_self=(l["name"] == "poga-2"))
                     for l in session._scan_lanes()]
            self.assertEqual(session._idle_live_lanes(lanes), [])
        self.assertTrue(path.is_dir())

    # ── the measurement is never invented ──────────────────────────────────────────────

    def test_a_lane_with_no_heartbeat_says_so_rather_than_naming_a_duration(self):
        """A lock-live lane whose sidecar was never written is the same harm, but there is
        no silence to measure. "silent 0.0h" would be a fabricated probe."""
        self.idle_live(beat=False)
        out = self._reap()
        self.assertIn("no heartbeat ever recorded", out)
        self.assertNotIn("silent 0.0h", out)

    def test_an_unparseable_beat_reads_as_no_measurement_not_as_silence(self):
        """`_lane_last_beat_age_min` returns None on anything it cannot read, and the row
        degrades to the honest form rather than asserting a duration it cannot see."""
        path = self.idle_live(beat=False)
        d = path / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "abcd1234.live").write_text(json.dumps({"last_beat": "not-a-date"}),
                                         encoding="utf-8")
        self.assertIsNone(session._lane_last_beat_age_min(path))
        self.assertIn("no heartbeat ever recorded", self._reap())

    def test_the_freshest_beat_in_a_reused_lane_is_what_answers(self):
        """A lane holds one sidecar per session that ever ran in it. An ancient sidecar
        from a finished session says nothing about the session sitting there now, so
        taking the oldest would advise on every long-lived lane in the pool."""
        path = self.idle_live(beat=False)
        self.beat(path, csid="old00001", age_min=60 * 24 * 9)
        self.beat(path, csid="new00002", age_min=3)
        self.assertAlmostEqual(session._lane_last_beat_age_min(path), 3, delta=1)
        self.assertNotIn("poga-2 — LIVE", self._reap())


def _out_branches(repo):
    return sorted(subprocess.run(
        [GIT, "-C", str(repo), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True).stdout.split())


if __name__ == "__main__":
    unittest.main()
