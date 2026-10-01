"""WI-0346 — the land gate says what it actually holds, and frees only that.

WI-0277 shipped half of itself. `_land_gate_reserve` gained three named fail-open paths
and an `emit` that says which one it took, and all three still returned `True` — "the
gate is yours" — from a function that had just finished explaining it reserved nothing.
`_land_gate_verified`'s own docstring carried the excuse: the empty record, it said, "is
what `land_gate_lock`'s `finally` checks before it tries to release something it never
took." The `finally` checked `serialized`, not the record. So the lie was not merely
cosmetic — it ran, at `force=True`, against the ONE shared land-gate record, under a
comment asserting that `serialized` proved the record was ours.

Two ways that unlinks a live lane's gate, and both are pinned below:

  1. THE FAIL-OPEN PATHS. No coordination store, an unexpected filesystem error, or a
     record written and not read back: we hold nothing, we say `serialized`, and on the
     way out we delete whatever is at the path — which is whoever DID hold the gate.
  2. THE RECLAIM. `_land_gate_holder_dead` lets a second lane take a lapsed record whose
     holder is provably gone. A first lane that was merely slow to be declared dead is
     still inside its block, and its `finally` unlinks the record the second lane now
     legitimately holds. Nothing about `serialized` was ever evidence against this one;
     it is true and stale at the same time.

The fix is not a better comment. `_coord_release_exact` releases a record only when the
record ON DISK is the one we wrote — same `created_at`, same `session_id`, same
`journal` — so "we hold nothing" and "someone else holds it now" both resolve to a no-op
by construction rather than by proof. The reserve, separately, stops claiming a
reservation it does not have: fail-open now yields `serialized=False` WITH a reason that
means *stop waiting*, so the land still lands (ADR-0119 D3's fail-open shape is kept) and
the gate's own bookkeeping stops disagreeing with the disk.

`_trunk_lock_reserve` / `trunk_lock` carry the identical shape against the trunk-lock
record, including the same `force=True`-is-safe-because-`held` comment, so they are fixed
and pinned here too ([`retire-the-class-not-the-instance`]).
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
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


def _boom(*_a, **_k):
    """The filesystem failing under the one `os.open` every exclusive create goes
    through. Not a mocked *return* — the real exception shape the reserve claims to
    handle."""
    raise OSError("disk gone")


@unittest.skipUnless(GIT, "git not available")
class GateReleaseBase(unittest.TestCase):
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
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2",
             str(self.laneB), "main")
        self._save = {k: getattr(session, k)
                      for k in ("ROOT", "CFG", "_LAND_GATE_DEPTH", "_TRUNK_LOCK_DEPTH")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        session._LAND_GATE_DEPTH = 0
        session._TRUNK_LOCK_DEPTH = 0

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lock(self, **kw):
        kw.setdefault("emit", self.out.append)
        kw.setdefault("poll", 0)
        kw.setdefault("sleep", lambda _s: None)
        kw.setdefault("heartbeat", 0)
        return session.land_gate_lock(**kw)

    def _gate_path(self):
        return (session._coord_dir(session.LAND_LOCK_KIND, create=True)
                / f"{session.LAND_LOCK_NAME}.json")

    def _trunk_path(self):
        return (session._coord_dir(session.TRUNK_LOCK_KIND, create=True)
                / f"{session.TRUNK_LOCK_NAME}.json")

    def _plant(self, path, kind, name, ttl, session_id):
        """A record belonging to somebody else, written straight to disk — the state a
        second lane's reserve would have left, without needing that lane's process."""
        rec = session._coord_record(session_id, ttl, name=name, kind=kind)
        path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        return rec


class ReserveReportsWhatItHoldsTest(GateReleaseBase):
    """The first half: a reserve that took nothing must not answer `True`, and must say
    so in a way the caller can act on — there is no holder to queue behind."""

    def test_no_coordination_store_is_not_a_held_gate(self):
        session.ROOT = self.laneA
        with unittest.mock.patch.object(session, "_coord_dir", lambda *a, **k: None):
            held, rec, why = session._land_gate_reserve("worktree-poga-1",
                                                        emit=self.out.append)
        self.assertFalse(held, "reserve reported a gate it could not even open a store for")
        self.assertFalse(rec)
        self.assertTrue(why, "a fail-open with no reason cannot be distinguished from a wait")

    def test_a_filesystem_error_is_not_a_held_gate(self):
        session.ROOT = self.laneA
        with unittest.mock.patch.object(os, "open", _boom):
            held, rec, why = session._land_gate_reserve("worktree-poga-1",
                                                        emit=self.out.append)
        self.assertFalse(held, "reserve reported a gate whose record it could not write")
        self.assertFalse(rec)
        self.assertTrue(why)
        self.assertTrue(any("disk gone" in line for line in self.out),
                        f"the error path swallowed its reason: {self.out}")

    def test_a_record_that_did_not_persist_is_not_a_held_gate(self):
        """THE ACCEPTANCE SENTENCE: a reservation that did not persist yields False."""
        session.ROOT = self.laneA
        real = session.atomic_write
        with unittest.mock.patch.object(session, "_coord_read", lambda _p: None), \
                unittest.mock.patch.object(session, "atomic_write", real):
            held, rec, why = session._land_gate_reserve(session._coord_identity(),
                                                        emit=self.out.append)
        self.assertFalse(held, "reserve reported a gate it never read back off disk")
        self.assertFalse(rec)
        self.assertTrue(why)
        self.assertTrue(any("unverified" in line for line in self.out),
                        f"the unverified path said nothing: {self.out}")

    def test_a_live_holder_is_still_a_wait_not_a_fail_open(self):
        """The distinction the third value exists to carry. Losing to a live lane must
        NOT read as fail-open — that lane is coming back, and the queue is the answer."""
        session.ROOT = self.laneA
        self.assertTrue(session._land_gate_reserve(session._coord_identity())[0])
        session.ROOT = self.laneB
        held, holder, why = session._land_gate_reserve(session._coord_identity())
        self.assertFalse(held)
        self.assertEqual(holder.get("session_id"), "worktree-poga-1")
        self.assertEqual(why, "", "a contended gate was reported as a fail-open, so the "
                                  "loser would land unserialized instead of queueing")


class AFailOpenFreesNothingTest(GateReleaseBase):
    """The half that had no test at all: what the `finally` does when `serialized` is a
    claim rather than a fact."""

    def test_a_filesystem_error_does_not_unlink_the_holders_gate(self):
        session.ROOT = self.laneA
        self.assertTrue(session._land_gate_reserve(session._coord_identity())[0])
        before = self._gate_path().read_text(encoding="utf-8")

        session.ROOT = self.laneB
        with unittest.mock.patch.object(os, "open", _boom):
            with self._lock(max_wait=0) as gate:
                self.assertFalse(gate.serialized,
                                 "laneB reported a serialized land it never reserved")
        self.assertEqual(self._gate_path().read_text(encoding="utf-8"), before,
                         "laneB force-released laneA's live gate on its way out")

    def test_a_fail_open_lands_instead_of_burning_the_queue_cap(self):
        """ADR-0119 D3's fail-open shape is KEPT. `serialized=False` here must not mean
        "wait": there is no holder to wait for, and the 90-minute cap is not the answer
        to a broken disk."""
        session.ROOT = self.laneB
        sleeps = []
        with unittest.mock.patch.object(os, "open", _boom):
            with self._lock(max_wait=9999, sleep=sleeps.append) as gate:
                self.assertFalse(gate.serialized)
                self.assertNotEqual(gate.reason, "cap")
                self.assertTrue(gate.reason,
                                "the lock yielded an unserialized gate with no reason")
        self.assertEqual(sleeps, [], "a fail-open queued instead of landing")

    def test_the_holder_still_frees_its_own_gate(self):
        """The property the fix must not cost: an ordinary land leaves nothing behind."""
        session.ROOT = self.laneA
        with self._lock() as gate:
            self.assertTrue(gate.serialized)
            self.assertTrue(self._gate_path().exists())
        self.assertEqual(session._coord_list(session.LAND_LOCK_KIND), {})
        self.assertEqual(session._coord_list(session.LAND_QUEUE_KIND), {})


class AReclaimedRecordIsNotOursToFreeTest(GateReleaseBase):
    """The case `serialized` could never have covered. It is TRUE — we did take the gate
    — and the record at that path stopped being ours while we were still landing."""

    def test_the_former_holder_does_not_unlink_the_new_holders_record(self):
        session.ROOT = self.laneA
        path = self._gate_path()
        with self._lock() as gate:
            self.assertTrue(gate.serialized)
            # laneB's reclaim of a lapsed record whose holder read as gone
            # (`_land_gate_holder_dead`) — laneA is slow, not dead, and is still here.
            self._plant(path, session.LAND_LOCK_KIND, session.LAND_LOCK_NAME,
                        session.LAND_LOCK_TTL_SECONDS, "worktree-poga-2")
        rec = session._coord_read(path)
        self.assertIsNotNone(rec, "laneA unlinked the gate record laneB now holds")
        self.assertEqual(rec.get("session_id"), "worktree-poga-2")

    def test_a_ticket_rewritten_by_a_later_session_survives_our_exit(self):
        """Same shape, one name over: the queue ticket is keyed on the LANE, which every
        session that ever runs in that lane shares."""
        session.ROOT = self.laneA
        ident = session._coord_identity()
        qpath = (session._coord_dir(session.LAND_QUEUE_KIND, create=True)
                 / f"{ident}.json")
        with self._lock() as gate:
            self.assertTrue(gate.serialized)
            self._plant(qpath, session.LAND_QUEUE_KIND, ident,
                        session.LAND_QUEUE_TTL_SECONDS, ident)
        self.assertIsNotNone(session._coord_read(qpath),
                             "our exit freed a ticket a later session had taken")


class ReleaseExactTest(GateReleaseBase):
    """The primitive itself, because every caller above delegates its safety to it."""

    def test_it_releases_the_record_we_wrote(self):
        session.ROOT = self.laneA
        rec = self._plant(self._gate_path(), session.LAND_LOCK_KIND,
                          session.LAND_LOCK_NAME, session.LAND_LOCK_TTL_SECONDS,
                          "worktree-poga-1")
        self.assertTrue(session._coord_release_exact(
            session.LAND_LOCK_KIND, session.LAND_LOCK_NAME, rec, reason="test"))
        self.assertFalse(self._gate_path().exists())

    def test_it_refuses_a_record_it_did_not_write(self):
        session.ROOT = self.laneA
        ours = session._coord_record("worktree-poga-1", session.LAND_LOCK_TTL_SECONDS,
                                     name=session.LAND_LOCK_NAME,
                                     kind=session.LAND_LOCK_KIND)
        self._plant(self._gate_path(), session.LAND_LOCK_KIND, session.LAND_LOCK_NAME,
                    session.LAND_LOCK_TTL_SECONDS, "worktree-poga-2")
        self.assertFalse(session._coord_release_exact(
            session.LAND_LOCK_KIND, session.LAND_LOCK_NAME, ours, reason="test",
            emit=self.out.append))
        self.assertTrue(self._gate_path().exists())
        self.assertTrue(self.out, "a refused release said nothing; the record it found "
                                  "belonging to someone else is the whole signal")

    def test_an_empty_record_releases_nothing(self):
        """What every fail-open path hands it. `{}` is not a key that matches anything."""
        session.ROOT = self.laneA
        self._plant(self._gate_path(), session.LAND_LOCK_KIND, session.LAND_LOCK_NAME,
                    session.LAND_LOCK_TTL_SECONDS, "worktree-poga-2")
        self.assertFalse(session._coord_release_exact(
            session.LAND_LOCK_KIND, session.LAND_LOCK_NAME, {}, reason="test"))
        self.assertTrue(self._gate_path().exists())

    def test_a_refresh_does_not_break_the_match(self):
        """The heartbeat rewrites expiry on the record we hold. If identity were keyed on
        anything a renewal touches, a long land could not free its own gate."""
        session.ROOT = self.laneA
        held, rec, _ = session._land_gate_reserve(session._coord_identity())
        self.assertTrue(held)
        self.assertTrue(session._land_gate_refresh(session._coord_identity()))
        self.assertTrue(session._coord_release_exact(
            session.LAND_LOCK_KIND, session.LAND_LOCK_NAME, rec, reason="test"))
        self.assertFalse(self._gate_path().exists())

    def test_it_leaves_a_tombstone(self):
        """WI-0074: a release stays visible after the fact. The exact release is still a
        release, and must not quietly become the one unlink nobody can audit."""
        session.ROOT = self.laneA
        rec = self._plant(self._gate_path(), session.LAND_LOCK_KIND,
                          session.LAND_LOCK_NAME, session.LAND_LOCK_TTL_SECONDS,
                          "worktree-poga-1")
        session._coord_release_exact(session.LAND_LOCK_KIND, session.LAND_LOCK_NAME,
                                     rec, reason="land complete")
        tombs = session._coord_dir(session.LAND_LOCK_KIND, create=True).parent
        self.assertTrue(any("land complete" in p.read_text(encoding="utf-8")
                            for p in tombs.rglob("*") if p.is_file()),
                        "the exact release left no tombstone")


class TrunkLockFreesOnlyWhatItHoldsTest(GateReleaseBase):
    """The sibling of the same shape, one lock over, guarding the trunk ref itself."""

    def _trunk(self, **kw):
        kw.setdefault("emit", self.out.append)
        kw.setdefault("poll", 0)
        kw.setdefault("sleep", lambda _s: None)
        return session.trunk_lock(**kw)

    def test_a_filesystem_error_does_not_unlink_another_writers_lock(self):
        session.ROOT = self.laneA
        self._plant(self._trunk_path(), session.TRUNK_LOCK_KIND,
                    session.TRUNK_LOCK_NAME, session.TRUNK_LOCK_TTL_SECONDS,
                    "worktree-poga-1")
        before = self._trunk_path().read_text(encoding="utf-8")

        session.ROOT = self.laneB
        with unittest.mock.patch.object(os, "open", _boom):
            with self._trunk(max_wait=0) as held:
                self.assertFalse(held, "a writer reported a trunk lock it never took")
        self.assertEqual(self._trunk_path().read_text(encoding="utf-8"), before,
                         "the failed writer force-released the live writer's trunk lock")

    def test_the_holder_still_frees_its_own_lock(self):
        session.ROOT = self.laneA
        with self._trunk(max_wait=0) as held:
            self.assertTrue(held)
            self.assertTrue(self._trunk_path().exists())
        self.assertFalse(self._trunk_path().exists())


if __name__ == "__main__":
    unittest.main()
