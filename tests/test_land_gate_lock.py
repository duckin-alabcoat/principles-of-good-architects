"""WI-0267 — the land gate is serialized: one land at a time per repo, the rest queue.

Concurrent lands each re-ran the full suite in a scratch worktree, so contention did not
merely add latency, it MULTIPLIED it: N suites on one box make each slower, a slower gate
widens the window for a sibling to CAS the trunk first, and losing the CAS starts another
full suite. These pin the mechanism that removes the concurrency, and — as much — the
three ways it deliberately gives up rather than stranding a land.

  1. exclusion: a live other lane cannot hold the gate at the same time;
  2. THE REGRESSION: sibling lanes sharing one journal still cannot both hold it — the
     hole that would have made `lease()` useless for exactly the population (dispatched
     lanes) that causes the storm;
  3. crash-safety: an expired holder is reclaimed, and a dead WAITER stops holding the
     line;
  4. fail-open, all three paths — no store, the wait cap, and re-entrancy;
  5. the queue reports a position, in enqueue order, so a long wait is legible;
  6. release happens on the exception path too, and both kinds are wired into the
     COORD_KINDS surfaces that free a dead lane's holds.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class LandGateBase(unittest.TestCase):
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
        (self.main / "f.txt").write_text("x\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(self.laneB), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "_LAND_GATE_DEPTH")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        session._LAND_GATE_DEPTH = 0

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # A land that never sleeps and never waits: the tests drive the clock by asserting on
    # what the loop DID, not by living through it.
    def _lock(self, **kw):
        kw.setdefault("emit", self.out.append)
        kw.setdefault("poll", 0)
        kw.setdefault("sleep", lambda _s: None)
        return session.land_gate_lock(**kw)

    def _gate_records(self):
        return session._coord_list(session.LAND_LOCK_KIND)

    def _queue_records(self):
        return session._coord_list(session.LAND_QUEUE_KIND)

    def _kill_holder(self, kind, name):
        """Make the record's holder provably gone (WI-0298). A pid that cannot be running
        — drawn from above the kernel's legal range rather than picked, so this is not
        asserting against whatever process happens to own a guessed number."""
        p = session._coord_dir(kind) / f"{name}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["holder_pid"] = 2 ** 22
        rec["holder_host"] = session._coord_host()
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    def _expire(self, kind, name):
        """Age a record out by rewriting its expiry — the house pattern; there is no fake
        clock anywhere in this suite."""
        p = session._coord_dir(kind) / f"{name}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["expires_at"] = time.time() - 1
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")


class ExclusionTest(LandGateBase):
    def setUp(self):
        super().setUp()
        self.out = []

    def test_one_lane_holds_the_gate_and_the_other_does_not(self):
        session.ROOT = self.laneA
        ok_a, _, _ = session._land_gate_reserve(session._coord_identity())
        self.assertTrue(ok_a)
        session.ROOT = self.laneB
        ok_b, holder, _ = session._land_gate_reserve(session._coord_identity())
        self.assertFalse(ok_b)
        self.assertEqual(holder.get("session_id"), "worktree-poga-1")

    def test_the_block_holds_it_and_the_exit_releases_it(self):
        session.ROOT = self.laneA
        with self._lock() as gate:
            self.assertTrue(gate.serialized)
            self.assertEqual(gate.reason, "acquired")
            self.assertIn(session.LAND_LOCK_NAME, self._gate_records())
        self.assertEqual(self._gate_records(), {})
        self.assertEqual(self._queue_records(), {})   # the ticket goes too

    def test_releases_on_exception(self):
        session.ROOT = self.laneA
        with self.assertRaises(ValueError):
            with self._lock():
                raise ValueError("boom")
        self.assertEqual(self._gate_records(), {})
        self.assertEqual(self._queue_records(), {})
        self.assertEqual(session._LAND_GATE_DEPTH, 0)


class SiblingLanesShareAJournalTest(LandGateBase):
    """THE REGRESSION, and the reason this lock is not `lease()`.

    `_lease_try` delegates to `_coord_try_acquire`, which reclaims a record `_coord_mine`
    judges as already ours — and `_coord_mine` matches on the ambient JOURNAL when the
    session id differs. Dispatched lanes are exactly that case: `tmux new-session` hands
    each child the dispatcher's environment, so siblings inherit one journal. They are
    also exactly the population that storms the gate, so a lock they can both walk through
    would have been decorative.

    This test therefore does the opposite of the usual fixture: it RESTORES a shared
    journal instead of neutralizing it, because the shared journal IS the scenario.
    """

    def setUp(self):
        super().setUp()
        self.out = []
        # Both fake lanes report the SAME journal — the dispatched-sibling shape.
        patch = unittest.mock.patch.object(
            session, "_coord_holder", lambda: ("20260904T0000Z-devbox-shared", "claude-code", "journal"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_gate_still_excludes_them(self):
        session.ROOT = self.laneA
        self.assertTrue(session._land_gate_reserve(session._coord_identity())[0])
        session.ROOT = self.laneB
        ok, holder, _ = session._land_gate_reserve(session._coord_identity())
        self.assertFalse(ok, "a sibling lane sharing the dispatcher's journal took the "
                             "land gate — the whole lock is void for dispatched lanes")
        self.assertEqual(holder.get("session_id"), "worktree-poga-1")

    def test_the_lease_primitive_does_not_which_is_why_this_lock_exists(self):
        """Pins the hazard itself, so the reason for the extra code stays visible. If
        this ever starts failing, `_coord_try_acquire` was fixed and the comment on
        `_land_gate_reserve` should be revisited — not deleted, verified."""
        session.ROOT = self.laneA
        self.assertTrue(session._lease_try("some-shared-resource")[0])
        session.ROOT = self.laneB
        self.assertTrue(session._lease_try("some-shared-resource")[0],
                        "the journal-matching reclaim is gone; re-check _land_gate_reserve")

    def test_a_sibling_cannot_refresh_the_holders_grip(self):
        """A sibling extending the holder's TTL would hide a wedged lane from the one
        mechanism that frees it."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        session.ROOT = self.laneB
        self.assertFalse(session._land_gate_refresh(session._coord_identity()))


class CrashSafetyTest(LandGateBase):
    def setUp(self):
        super().setUp()
        self.out = []

    def test_an_expired_holder_is_reclaimed(self):
        """A lane that DIES holding the gate must not wedge every other lane forever.

        WI-0298 sharpened what this asserts. Expiry alone used to be the whole verdict, so
        this test only had to age the record out. It is now the TRIGGER for a verdict —
        a lapsed record whose holder is still running keeps the gate, because a TTL long
        enough for the slowest honest land is also how long a crashed one wedges the repo,
        and no single number is both. So the scenario has to be what the docstring always
        said it was: the holder is gone. The dead pid is that fact, made explicit instead
        of implied by a clock (`test_a_live_holder_is_not_reclaimed` is now the other
        half, and `DeadHolderDetectorTest` pins the detector itself)."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        self._kill_holder(session.LAND_LOCK_KIND, session.LAND_LOCK_NAME)
        self._expire(session.LAND_LOCK_KIND, session.LAND_LOCK_NAME)
        session.ROOT = self.laneB
        ok, _, _ = session._land_gate_reserve(session._coord_identity())
        self.assertTrue(ok)
        held = session._coord_dir(session.LAND_LOCK_KIND) / f"{session.LAND_LOCK_NAME}.json"
        self.assertEqual(json.loads(held.read_text())["session_id"], "worktree-poga-2")

    def test_a_live_holder_is_not_reclaimed(self):
        """The other half of the same claim — reclaiming a LIVE holder would be worse
        than the contention this fixes."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        session.ROOT = self.laneB
        self.assertFalse(session._land_gate_reserve(session._coord_identity())[0])

    def test_a_dead_waiter_stops_holding_up_the_line(self):
        """A corpse at the HEAD of the queue is the same outage as a corpse holding the
        gate — the line never advances — so an expired ticket must not be counted."""
        session.ROOT = self.laneA
        now = time.time()
        session._coord_try_acquire(session.LAND_QUEUE_KIND, "worktree-poga-9",
                                   "worktree-poga-9", session.LAND_QUEUE_TTL_SECONDS,
                                   enqueued_at=now - 100)
        self._expire(session.LAND_QUEUE_KIND, "worktree-poga-9")
        pos, total = session._land_queue_position("worktree-poga-1", now)
        self.assertEqual((pos, total), (1, 1))

    def test_the_holds_are_wired_into_the_dead_lane_surfaces(self):
        """Both kinds must be in COORD_KINDS — that one tuple drives the reaper, the
        dead-lane sweep, lane teardown, `release --kind` and the coord dump. A kind
        missing from it writes records nothing can ever free (WI-0166, twice)."""
        self.assertIn(session.LAND_LOCK_KIND, session.COORD_KINDS)
        self.assertIn(session.LAND_QUEUE_KIND, session.COORD_KINDS)

    def test_teardown_frees_a_dead_lanes_gate(self):
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        session.ROOT = self.main
        freed = session._lane_exit_release("worktree-poga-1")
        self.assertGreaterEqual(len(freed), 1)
        self.assertEqual(self._gate_records(), {})


class QueueTest(LandGateBase):
    def setUp(self):
        super().setUp()
        self.out = []

    def test_position_is_enqueue_order(self):
        now = time.time()
        session.ROOT = self.laneA
        for name, when in (("worktree-poga-7", now - 30), ("worktree-poga-8", now - 20)):
            session._coord_try_acquire(session.LAND_QUEUE_KIND, name, name,
                                       session.LAND_QUEUE_TTL_SECONDS, enqueued_at=when)
        pos, total = session._land_queue_position("worktree-poga-1", now)
        self.assertEqual(pos, 3)
        self.assertEqual(total, 3)
        # ...and the lane that got there first is first.
        self.assertEqual(session._land_queue_position("worktree-poga-7", now - 30)[0], 1)

    def test_a_ticket_with_no_readable_enqueue_time_waits_behind_us(self):
        """An unorderable ticket must not silently outrank a lane that has been waiting —
        'cannot tell' is not 'first'."""
        now = time.time()
        session.ROOT = self.laneA
        session._coord_try_acquire(session.LAND_QUEUE_KIND, "worktree-poga-9",
                                   "worktree-poga-9", session.LAND_QUEUE_TTL_SECONDS,
                                   enqueued_at="not-a-number")
        self.assertEqual(session._land_queue_position("worktree-poga-1", now)[0], 1)

    def test_a_waiting_lane_says_where_it_is_in_line(self):
        """An unexplained wait is indistinguishable from a stall on every surface we
        have, and this one can legitimately run to tens of minutes."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())      # laneA holds the gate
        session.ROOT = self.laneB
        with self._lock(max_wait=0.0, report_every=0.0) as gate:
            self.assertFalse(gate.serialized)
        said = "\n".join(self.out)
        self.assertIn("position", said)
        self.assertIn("queue, not a stall", said)

    def test_a_waiter_behind_the_head_still_names_the_gate_holder(self):
        """The lane whose wait most needs explaining is the one NOT at the head: it never
        attempts an acquire, so it never learns the holder from one. Caught by racing two
        real processes, not by the fixture — the single-process tests all happened to sit
        at position 1, where the failed acquire hands you the holder for free."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        session._coord_try_acquire(session.LAND_QUEUE_KIND, "worktree-poga-7",
                                   "worktree-poga-7", session.LAND_QUEUE_TTL_SECONDS,
                                   enqueued_at=time.time() - 600)
        session.ROOT = self.laneB
        with self._lock(max_wait=0.0, report_every=0.0):
            pass
        said = "\n".join(self.out)
        self.assertIn("position 2", said)
        self.assertIn("gate held by poga-1", said)   # `_coord_short_holder` shortens it

    def test_a_short_wait_is_not_reported_as_zero_minutes(self):
        """`0m` for a real 45-second wait reads as a broken reporter, and this line exists
        to be believed."""
        self.assertEqual(session._fmt_wait(45), "45s")
        self.assertEqual(session._fmt_wait(600), "10m")


class FailOpenTest(LandGateBase):
    def setUp(self):
        super().setUp()
        self.out = []

    def test_no_coordination_store_lands_unserialized_and_says_so(self):
        session.ROOT = self.tmp / "not-a-repo"
        (self.tmp / "not-a-repo").mkdir()
        with unittest.mock.patch.object(session, "_git_common_dir", lambda: None):
            with self._lock() as gate:
                self.assertFalse(gate.serialized)
                self.assertEqual(gate.reason, "no-store")
        self.assertIn("UNSERIALIZED", "\n".join(self.out))

    def test_the_cap_lets_a_starved_lane_land_anyway(self):
        """A gate nobody can enter is worse than a gate two lanes enter. This is the
        backstop for a holder that is alive but wedged, which nothing else can detect."""
        session.ROOT = self.laneA
        session._land_gate_reserve(session._coord_identity())
        session.ROOT = self.laneB
        with self._lock(max_wait=0.0) as gate:
            self.assertFalse(gate.serialized)
            self.assertEqual(gate.reason, "cap")
        self.assertIn("UNSERIALIZED", "\n".join(self.out))
        # laneA still holds it — the starved lane proceeded, it did not steal the gate.
        held = session._coord_dir(session.LAND_LOCK_KIND) / f"{session.LAND_LOCK_NAME}.json"
        self.assertEqual(json.loads(held.read_text())["session_id"], "worktree-poga-1")

    def test_nesting_does_not_queue_behind_itself(self):
        """`_land_worktree_lane`'s push-rejected path re-gates through
        `_integrate_trunk_with_remote` from INSIDE the lock. Without re-entrancy that
        lane waits out the full cap on itself, turning a successful land into a hang."""
        session.ROOT = self.laneA
        with self._lock() as outer:
            self.assertTrue(outer.serialized)
            with self._lock(max_wait=0.0) as inner:
                self.assertEqual(inner.reason, "reentrant")
            # the inner block's exit must NOT have dropped the outer lane's hold
            self.assertIn(session.LAND_LOCK_NAME, self._gate_records())
        self.assertEqual(self._gate_records(), {})
        self.assertEqual(session._LAND_GATE_DEPTH, 0)


class TtlWiringTest(LandGateBase):
    def setUp(self):
        super().setUp()
        self.out = []

    def test_the_heartbeat_does_not_restamp_them_with_someone_elses_lifetime(self):
        """`_coord_refresh_identity`'s map defaults to the 8-hour CLAIM ttl, so a kind
        missing a row is silently given a stranger's lifetime — which for a 10-minute
        queue ticket means a dead waiter holds the head of the line until tomorrow
        (WI-0166, the same omission twice)."""
        session.ROOT = self.laneA
        ident = session._coord_identity()
        session._coord_try_acquire(session.LAND_QUEUE_KIND, ident, ident,
                                   session.LAND_QUEUE_TTL_SECONDS, enqueued_at=time.time())
        session._coord_refresh_identity(ident)
        p = session._coord_dir(session.LAND_QUEUE_KIND) / f"{ident}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        self.assertLess(rec["expires_at"] - time.time(),
                        session.LAND_QUEUE_TTL_SECONDS + 60)


if __name__ == "__main__":
    unittest.main()
