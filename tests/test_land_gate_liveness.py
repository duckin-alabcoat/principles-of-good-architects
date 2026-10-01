"""WI-0298 — the land gate is held by a LIVE HOLDER, not by a clock.

The incident these pin, measured 2026-09-06 07:27 and recorded in section 11 of
`2026-09-05-consultant-one-writer-for-the-trunk.md`: a land waited 28 minutes with no
suite running. The gate record was held by identity `worktree-poga-4` under journal
`a1ef` — the session that had closed on that lane at 06:37, leaving its record behind. A
NEW session (journal `83dd`) started in the same lane, queued behind it as a foreign
holder (ownership matches on the journal, WI-0123/ADR-0093) — and then its own session
heartbeat matched that record on `session_id`, which inside a lane is the LANE BRANCH,
and kept renewing the dead holder's expiry. The record could never expire. The only exit
was the 90-minute fail-open cap, and operator released it by hand — the second time in twelve
hours.

Two rules, and each is a class below:

  * **A waiter never refreshes a record it does not own** — refresh and ownership use the
    SAME key, and that key is the journal. The lane name is a location, not an identity.
  * **A close releases every coordination record its session holds** before the lane is
    torn down, and the teardown verifies the store is empty of that session's records.

With the first rule in force the TTL stops being a cap on a running land and becomes what
it should always have been: a DEAD-HOLDER DETECTOR. A land that is genuinely running
refreshes from inside its own process and never expires however long it takes; a record
whose holder is gone is reclaimable the moment it lapses. `DeadHolderDetectorTest` pins
both directions, and `ReserveVerifiesOnDiskTest` pins that a success is a record read back
off disk — never an assumption — and that every fail-open path says why out loud.
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
import unittest.mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")

# A pid that is certainly not running. Drawn rather than picked: the kernel hands the
# highest legal pid back only under wrap-around, and a test that guessed "999999" on a box
# configured for a larger pid_max would be asserting against a live process.
DEAD_PID = 2 ** 22


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class LivenessBase(unittest.TestCase):
    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.out = []
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
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.laneA), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "_LAND_GATE_DEPTH")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        session._LAND_GATE_DEPTH = 0
        session.ROOT = self.laneA

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _as_journal(self, journal):
        """Run the rest of this test as a session whose durable journal id is `journal`,
        in whatever lane `session.ROOT` currently names. This is the whole scenario: two
        sessions, one lane, therefore ONE `_coord_identity()` and two journals."""
        p = unittest.mock.patch.object(
            session, "_coord_holder", lambda: (journal, "claude-code", "journal"))
        p.start()
        self.addCleanup(p.stop)

    def _gate_path(self):
        return session._coord_dir(session.LAND_LOCK_KIND) / f"{session.LAND_LOCK_NAME}.json"

    def _gate_rec(self):
        return json.loads(self._gate_path().read_text(encoding="utf-8"))

    def _rewrite_gate(self, **fields):
        rec = self._gate_rec()
        rec.update(fields)
        # ATOMIC, like every production writer. An in-place write_text truncates first,
        # and a heartbeat that read the file in that window got None, took it for "no
        # longer ours" and stopped for good — the intermittent failure of
        # test_a_live_land_refreshes_from_inside_its_own_process (2026-09-30 trunk check).
        # Its OWN temp name: `atomic_write` uses `<name>.tmp`, and sharing it with the
        # heartbeat let one writer's replace take the other's file away.
        path = self._gate_path()
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rewrite-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, path)
        return rec

    def _expire_gate(self):
        return self._rewrite_gate(expires_at=time.time() - 1)


class WaiterCannotKeepADeadHoldersRecordAliveTest(LivenessBase):
    """The 07:27 incident, in the smallest form that reproduces it."""

    def test_the_session_heartbeat_does_not_refresh_a_foreign_journals_record(self):
        """THE INCIDENT. Session `a1ef` takes the gate and closes. Session `83dd` starts
        in the same lane — same `_coord_identity()`, different journal — and its heartbeat
        fires. It must not touch a record it does not own; if it does, the dead holder's
        expiry is pushed out forever and the gate can only be freed by hand."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        self.assertTrue(session._land_gate_reserve(ident)[0])
        before = self._expire_gate()["expires_at"]

        self._as_journal("20260906T0700Z-devbox-83dd")
        self.assertEqual(session._coord_identity(), ident,
                         "precondition: both sessions share the lane branch as identity")
        session._coord_refresh_identity(ident)

        self.assertEqual(self._gate_rec()["expires_at"], before,
                         "a waiter refreshed a record held by a different session — this "
                         "is the 28-minute wedge of 2026-09-06 07:27")

    def test_land_gate_refresh_keys_on_the_journal_not_the_lane_name(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        session._land_gate_reserve(ident)
        self._as_journal("20260906T0700Z-devbox-83dd")
        self.assertFalse(session._land_gate_refresh(ident),
                         "refresh matched on the lane name, which every session in that "
                         "lane shares — the lane is a location, not an identity")

    def test_the_owner_can_still_refresh_its_own_record(self):
        """The other half, so the fix is a narrowing and not an amputation: the session
        that actually holds the gate must still be able to keep it alive."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        session._land_gate_reserve(ident)
        self._expire_gate()
        self.assertTrue(session._land_gate_refresh(ident))
        self.assertGreater(self._gate_rec()["expires_at"], time.time())

    def test_a_record_with_no_journal_still_refreshes_on_the_session_id(self):
        """Absence is not identity. A record written before this change carries no
        journal; matching must fall back to the old key rather than orphaning it, or the
        fix strands every record already in the store."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        session._land_gate_reserve(ident)
        self._rewrite_gate(journal="", expires_at=time.time() - 1)
        self.assertTrue(session._land_gate_refresh(ident))


class DeadHolderDetectorTest(LivenessBase):
    """D3 — the TTL detects a dead holder; it never caps a running land."""

    def test_an_expired_record_whose_holder_is_alive_is_not_reclaimed(self):
        """The behaviour change this item buys. A land that outruns the TTL is still
        landing; taking the gate out from under it is how two suites end up racing for
        one trunk."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        session._land_gate_reserve(session._coord_identity())
        self._rewrite_gate(holder_pid=os.getpid(), holder_host=session._coord_host(),
                           expires_at=time.time() - 1)
        self._as_journal("20260906T0700Z-devbox-83dd")
        ok, holder, _ = session._land_gate_reserve("worktree-poga-2")
        self.assertFalse(ok, "an expired but LIVE holder was evicted mid-land")
        self.assertEqual(holder.get("journal"), "20260906T0600Z-devbox-a1ef")

    def test_an_expired_record_whose_holder_is_gone_is_reclaimed(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        session._land_gate_reserve(session._coord_identity())
        self._rewrite_gate(holder_pid=DEAD_PID, holder_host=session._coord_host(),
                           expires_at=time.time() - 1)
        self._as_journal("20260906T0700Z-devbox-83dd")
        ok, _, _ = session._land_gate_reserve("worktree-poga-2")
        self.assertTrue(ok, "a lapsed record whose holder is provably gone must be "
                            "reclaimable, or a crashed land wedges the repo")

    def test_a_record_from_another_machine_falls_back_to_the_heartbeat(self):
        """A pid is only evidence on the box that owns it. Off-host, the honest test is
        the heartbeat, and the answer must be stated rather than guessed."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        session._land_gate_reserve(session._coord_identity())
        self._rewrite_gate(holder_pid=os.getpid(), holder_host="some-other-box",
                           heartbeat_at=session._coord_iso(time.time()),
                           expires_at=time.time() - 1)
        dead, why = session._land_gate_holder_dead(self._gate_rec())
        self.assertFalse(dead)
        self.assertIn("heartbeat", why)

    def test_a_stale_heartbeat_off_host_reads_as_dead(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        session._land_gate_reserve(session._coord_identity())
        stale = time.time() - session.LAND_LOCK_DEAD_AFTER_SECONDS - 60
        self._rewrite_gate(holder_pid=os.getpid(), holder_host="some-other-box",
                           heartbeat_at=session._coord_iso(stale),
                           expires_at=time.time() - 1)
        dead, _why = session._land_gate_holder_dead(self._gate_rec())
        self.assertTrue(dead)

    def test_a_live_land_refreshes_from_inside_its_own_process(self):
        """The holder does not depend on a session heartbeat: `session.py merge` blocks
        the shell, so nothing else is running. The lock keeps itself alive."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        with session.land_gate_lock(emit=self.out.append, poll=0, sleep=lambda _s: None,
                                    heartbeat=0.01) as gate:
            self.assertTrue(gate.serialized)
            self._rewrite_gate(expires_at=time.time() - 1)
            deadline = time.time() + 5
            while time.time() < deadline and self._gate_rec()["expires_at"] < time.time():
                time.sleep(0.02)
            self.assertGreater(self._gate_rec()["expires_at"], time.time(),
                               "the holder's own heartbeat never fired — a long land "
                               "would lapse and be evicted by a waiter")

    def test_the_heartbeat_thread_does_not_outlive_the_block(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        with session.land_gate_lock(emit=self.out.append, poll=0, sleep=lambda _s: None,
                                    heartbeat=0.01) as gate:
            beat = gate.heartbeat_thread
            self.assertIsNotNone(beat)
        beat.join(timeout=5)
        self.assertFalse(beat.is_alive(), "the refresh thread outlived its lock, so a "
                                          "released gate is still being renewed")


class ReserveVerifiesOnDiskTest(LivenessBase):
    """D3 — success is a record READ BACK, and every fail-open path prints why."""

    def test_success_requires_the_record_on_disk(self):
        """`declare-what-a-check-assumes`: an exclusive create that returns without the
        file existing must not report a held gate. It reports the fail-open it actually
        took."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        real = session.atomic_write
        with unittest.mock.patch.object(session, "_coord_read", lambda _p: None), \
                unittest.mock.patch.object(session, "atomic_write", real):
            ok, rec, why = session._land_gate_reserve(session._coord_identity(),
                                                      emit=self.out.append)
        self.assertFalse(rec, "reserve returned a record it never read back off disk")
        self.assertFalse(ok, "reserve reported a HELD gate it never read back off disk")
        self.assertTrue(why)
        self.assertTrue(any("land-gate" in line and "unverified" in line
                            for line in self.out),
                        f"the unverified path said nothing: {self.out}")

    # WI-0346 — THESE TWO PINNED THE DEFECT, and are corrected rather than deleted.
    # Each asserted `assertTrue(ok)` one line after the reserve had printed that it
    # reserved nothing, so the half of WI-0277 that never landed was held in place by
    # WI-0277's own suite: any attempt to tell the truth here failed the build. What D3
    # actually promised is the fail-open POLICY (land rather than strand) and a reason
    # said out loud — neither of which needs the return value to be a lie. The new third
    # value carries the "stop waiting" that `True` was standing in for.

    def test_the_no_store_path_says_why_and_holds_nothing(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        with unittest.mock.patch.object(session, "_coord_dir", lambda *a, **k: None):
            ok, _, why = session._land_gate_reserve("worktree-poga-1",
                                                    emit=self.out.append)
        self.assertFalse(ok, "a reserve with no store to write to reported a held gate")
        self.assertEqual(why, "no-store")
        self.assertTrue(any("no coordination store" in line for line in self.out),
                        f"a silent fail-open: {self.out}")

    def test_an_unexpected_filesystem_error_says_why_and_holds_nothing(self):
        self._as_journal("20260906T0600Z-devbox-a1ef")
        def boom(*_a, **_k):
            raise OSError("disk gone")
        with unittest.mock.patch.object(os, "open", boom):
            ok, _, why = session._land_gate_reserve("worktree-poga-1",
                                                    emit=self.out.append)
        self.assertFalse(ok, "a reserve whose write raised reported a held gate")
        self.assertEqual(why, "fs-error")
        self.assertTrue(any("disk gone" in line for line in self.out),
                        f"the error path swallowed its reason: {self.out}")


class ClosedSessionLeavesNoRecordTest(LivenessBase):
    """D6, and the first of the two rules: a close releases what it holds, and the
    teardown verifies rather than assumes."""

    def _hold_one_of_everything(self, ident):
        session._land_gate_reserve(ident)
        session._coord_try_acquire(session.LAND_QUEUE_KIND, ident, ident,
                                   session.LAND_QUEUE_TTL_SECONDS, enqueued_at=time.time())
        session._coord_try_acquire("wi-alloc", "0299", ident,
                                   session.WI_ALLOC_TTL_SECONDS)
        session._coord_try_acquire(session.DISPATCH_SPAWN_KIND, "d1__WI-0298", ident,
                                   session.DISPATCH_SPAWN_TTL_SECONDS)

    def test_a_closed_session_leaves_no_record_behind(self):
        """THE SECOND RULE. Whatever the session held — the gate, its place in the queue,
        a drawn number, a dispatch slot — is gone when the session is."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        self._hold_one_of_everything(ident)
        self.assertTrue(session._session_records(ident, ("20260906T0600Z-devbox-a1ef",)))

        session._lane_exit_release(ident, journals=("20260906T0600Z-devbox-a1ef",))

        self.assertEqual(
            session._session_records(ident, ("20260906T0600Z-devbox-a1ef",)), [],
            "a closed session left coordination records behind — the next session in "
            "that lane inherits them as a foreign holder it cannot free")

    def test_the_exit_frees_gate_reservation_and_dispatch_slot_in_one_step(self):
        """D6: land, teardown, reap and recovery all go through ONE exit. Wiring routes
        one at a time is what left three of them leaking (WI-0078)."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        self._hold_one_of_everything(ident)
        freed = session._lane_exit_release(ident, journals=("20260906T0600Z-devbox-a1ef",))
        kinds = {k for k, _n in freed}
        for kind in (session.LAND_LOCK_KIND, "wi-alloc", session.DISPATCH_SPAWN_KIND):
            self.assertIn(kind, kinds, f"{kind} survived the lane exit")

    def test_the_dispatch_slot_kind_is_on_the_exit_surface(self):
        """The slot was invisible to every sweep because its kind was not on the tuple
        they iterate — the WI-0166 shape, a third time."""
        self.assertIn(session.DISPATCH_SPAWN_KIND, session.LANE_EXIT_KINDS)
        for kind in session.COORD_KINDS:
            self.assertIn(kind, session.LANE_EXIT_KINDS)

    def test_teardown_verifies_the_store_is_empty_of_that_sessions_records(self):
        """A release that reports success without checking is the `a-close-is-the-banner-
        not-the-sentence` failure: the receipt has to be the store, not the sentence."""
        self._as_journal("20260906T0600Z-devbox-a1ef")
        ident = session._coord_identity()
        self._hold_one_of_everything(ident)
        with unittest.mock.patch.object(session, "_coord_release",
                                        lambda *a, **k: False):
            ok, left = session._lane_exit_verify(ident,
                                                 journals=("20260906T0600Z-devbox-a1ef",))
        self.assertFalse(ok)
        self.assertTrue(left, "teardown reported a clean store while records remained")


if __name__ == "__main__":
    unittest.main()
