"""WI-0283 — a remote-tracking ref is neither the remote nor your tree.

`tree_provenance` (ADR-0108 D2) made a read name the TREE and COMMIT it came from.
`origin/<branch>` is a THIRD kind of source that rule never reached, and it is the one
that fails silently:

  * it is NOT the remote — it is a local ref, as old as the last SUCCESSFUL fetch, and
    a failed fetch leaves it UNCHANGED rather than unreadable, so every probe keeps
    answering in the old shape with no error at all;
  * it is NOT your tree — the lanes share one git common dir (ADR-0060/0062), so a
    SIBLING's push moves it underneath you and it can be FRESHER than anything you did.

The classes are ordered by how much they prove. The first pins the contract, the second
pins the two implementations to each other, and the last two build the configurations
the defect actually arrives in — a ref moved by a peer's push into a shared object
store, and a lane that is level with a trunk nobody refreshed. A suite written only
from the code's own premise can only confirm it (`verify-in-the-created-configuration`).

stdlib unittest: python3 -m unittest discover -s tests
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
import session  # noqa: E402
from common import remote_clause, remote_provenance  # noqa: E402
import common  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _identify(repo):
    for k, v in (("user.email", "t@t.invalid"), ("user.name", "Test"),
                 ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)


def _commit(repo, name, body="x\n"):
    (pathlib.Path(repo) / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add " + name)


@unittest.skipUnless(GIT, "git not available")
class RemoteFixture(unittest.TestCase):
    """A real bare `origin` on disk, cloned. No network anywhere in this file — a
    filesystem path is a perfectly good remote, which is the house convention
    (`tests/test_checkout_staleness.py:243`)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="poga-remote-prov-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        self.main = self.tmp / "main"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(self.main)],
                       check=True, capture_output=True, text=True)
        _identify(self.main)
        _commit(self.main, "seed.txt")
        _git(self.main, "push", "-q", "-u", "origin", "main")

    def prov(self, **kw):
        return remote_provenance(self.main, "origin/main", **kw)


class ThreeOutcomesNeverTwoTest(RemoteFixture):
    """ADR-0108 D1's floor, for the third kind of source. "I could not tell" must never
    render as "nothing is wrong" — that collapse is the whole defect."""

    def test_a_ref_that_does_not_exist_is_unreadable_not_current(self):
        p = remote_provenance(self.main, "origin/no-such-branch")
        self.assertFalse(p["readable"])
        self.assertIsNone(p["sha"])
        self.assertFalse(p["fresh"], "an absent ref must never read as fresh")
        self.assertIn("UNREADABLE", remote_clause(self.main, "origin/no-such-branch"))

    def test_a_readable_recently_contacted_ref_is_fresh(self):
        """The negative control. Without it, a CANNOT TELL below could equally mean the
        helper answers CANNOT TELL for everything."""
        p = self.prov(ttl=10 ** 9)
        self.assertTrue(p["readable"])
        self.assertTrue(p["fresh"], p)
        self.assertIsNotNone(p["sha"])
        self.assertNotIn("CANNOT TELL", remote_clause(self.main, "origin/main",
                                                      ttl=10 ** 9))

    def test_contact_older_than_the_ttl_is_cannot_tell_not_a_number(self):
        p = self.prov(ttl=0)
        self.assertTrue(p["readable"], "the ref is perfectly readable; only its age is bad")
        self.assertFalse(p["fresh"])
        self.assertIn("CANNOT TELL", remote_clause(self.main, "origin/main", ttl=0))

    def test_a_ref_with_no_reflog_and_no_stamp_says_so_rather_than_guessing(self):
        """Absence of evidence is its own state. A ref whose reflog was never written
        (or has been expired or gc'd) can say nothing about when it was contacted, and
        must not borrow freshness from the fact that it resolves."""
        _git(self.main, "config", "core.logAllRefUpdates", "false")
        _git(self.main, "update-ref", "refs/remotes/origin/quiet", "refs/remotes/origin/main")
        p = remote_provenance(self.main, "origin/quiet", ttl=10 ** 9)
        self.assertTrue(p["readable"])
        self.assertIsNone(p["age"], "no witness to contact must not become an age")
        self.assertIsNone(p["moved_by"])
        self.assertFalse(p["fresh"], "unknown age can never be fresh, whatever the ttl")
        clause = remote_clause(self.main, "origin/quiet", ttl=10 ** 9)
        self.assertIn("CANNOT TELL", clause)
        self.assertIn("ever reached the remote", clause)
        self.assertIn("no reflog survives", clause,
                      "the two unknowns are separate facts and both belong in the clause")

    def test_a_dead_git_is_unreadable_and_never_raises(self):
        """A provenance annotation must NEVER cost its caller the verdict it was
        qualifying. Found the hard way: `_wi_missing_id_lines` reports a missing work
        item even when git cannot run at all, and `test_claims.OneListTest.test_an_
        unreadable_history_is_reported_as_a_hole_not_waved_through` mocks `sh` into
        raising to prove it. The new provenance call sat outside that guard and took
        the whole verdict down with it — a helper whose entire job is to say "I cannot
        tell" was stopping anyone from saying anything.

        An unrunnable git is the UNREADABLE outcome, which this helper already has a
        slot for, so both copies degrade to today's behaviour instead of raising."""
        with mock.patch.object(common.subprocess, "run", side_effect=OSError("no git")):
            p = remote_provenance(self.main, "origin/main")
            self.assertFalse(p["readable"])
            self.assertFalse(p["fresh"])
            self.assertIn("UNREADABLE", remote_clause(self.main, "origin/main"))
        with mock.patch.object(session, "sh", side_effect=OSError("no git")):
            q = session._remote_provenance("origin/main", root=self.main)
            self.assertFalse(q["readable"])
            self.assertFalse(q["fresh"])
            self.assertIn("UNREADABLE",
                          session._remote_clause("origin/main", root=self.main))

    def test_a_failing_root_resolution_is_unreadable_and_never_raises(self):
        """THE PATH THAT ACTUALLY BROKE, which the git-call guards did not cover.

        `root or _shared_work_root()` is a default-argument resolution: it runs before
        any guard placed around the git calls, and it shells out itself. Guarding the
        calls you thought of is the same mistake one level down, so the guarantee is
        stated once around the whole body — and this is the test that can tell those
        two designs apart."""
        with mock.patch.object(session, "_shared_work_root",
                               side_effect=OSError("no common dir")):
            q = session._remote_provenance("origin/main")
            self.assertFalse(q["readable"])
            self.assertFalse(q["fresh"])
            self.assertIn("UNREADABLE", session._remote_clause("origin/main"))

    def test_both_ref_spellings_name_the_same_ref(self):
        """`refs/remotes/origin/main` and `origin/main` are one ref, and the fetch stamp
        is keyed by the short one — so the long spelling must not silently lose its
        freshness."""
        short = remote_provenance(self.main, "origin/main", ttl=10 ** 9)
        long = remote_provenance(self.main, "refs/remotes/origin/main", ttl=10 ** 9)
        self.assertEqual(short["ref"], long["ref"])
        self.assertEqual(short["sha"], long["sha"])
        self.assertEqual(short["fresh"], long["fresh"])


class TheTwoImplementationsAgreeTest(RemoteFixture):
    """There are two copies and they cannot share code: `sessionlib/*` ships
    byte-identical to every member (`curate/push-substrate.py` BYTE_IDENTICAL) and
    `curate/` does not ship at all, so `land.py` can never import `common.py`. That is
    the same split `atomic_write` already lives with (`sessionlib/config.py:597`), and
    the precedent for keeping a forced duplicate honest is a test asserting the same
    property of both (`tests/test_gate_inputs_child_audit.py:472`).

    If these drift, every verdict the two halves render disagrees — silently, and about
    whether someone's work is published."""

    KEYS = ("ref", "sha", "readable", "fresh", "confirmed_by", "moved_by")

    def _both(self, **kw):
        return (remote_provenance(self.main, "origin/main", **kw),
                session._remote_provenance("origin/main", root=self.main, **kw))

    def test_the_ttl_constants_are_equal(self):
        """Divergence here is silent and in the UNSAFE direction: a larger value in
        `common` would call refs fresh that `session.py` had already given up on."""
        self.assertEqual(common.REMOTE_FRESH_TTL_SECONDS,
                         session.CHECKOUT_FETCH_TTL_SECONDS)

    def test_they_agree_on_a_fresh_ref(self):
        a, b = self._both(ttl=10 ** 9)
        self.assertEqual([a[k] for k in self.KEYS], [b[k] for k in self.KEYS])
        self.assertTrue(a["fresh"])

    def test_they_agree_on_a_stale_ref(self):
        a, b = self._both(ttl=0)
        self.assertEqual([a[k] for k in self.KEYS], [b[k] for k in self.KEYS])
        self.assertFalse(a["fresh"])

    def test_they_agree_on_an_absent_ref(self):
        a = remote_provenance(self.main, "origin/nope")
        b = session._remote_provenance("origin/nope", root=self.main)
        self.assertEqual([a[k] for k in self.KEYS], [b[k] for k in self.KEYS])
        self.assertFalse(a["readable"])

    def test_rendering_a_held_record_never_touches_git(self):
        """A caller that DECIDED something from a record must quote THAT record.

        `_lane_trunk_distance` probes the ref to decide whether the answer is stale, and
        its line function then renders a clause. If rendering re-probed, a sibling's
        push landing between the two calls could produce a line that asserts CANNOT TELL
        while quoting "fetched just now" — the verdict contradicting its own evidence
        inside one sentence, which is ADR-0108's named failure mode ("provenance that is
        itself stale") in its sharpest form. So rendering takes a record, not a ref.

        Proven by rendering with git mocked into raising: a renderer that still worked
        cannot have called git."""
        held = remote_provenance(self.main, "origin/main", ttl=10 ** 9)
        expected = common.remote_clause_of(held)
        self.assertIn(held["sha"][:7], expected, "a clause must name what it read")
        # Rendered again with git mocked into raising. A renderer that still produces
        # the same sentence cannot have shelled out to produce it.
        with mock.patch.object(common.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(common.remote_clause_of(held), expected)
        with mock.patch.object(session, "sh", side_effect=OSError("no git")):
            self.assertEqual(session._remote_clause_of(held), expected)

    def test_the_probing_clause_agrees_with_the_rendering_one(self):
        """The convenience wrapper must stay defined in terms of the renderer, or the
        two spellings drift and only one of them is the tested one."""
        for ttl in (0, 10 ** 9):
            p = remote_provenance(self.main, "origin/main", ttl=ttl)
            self.assertEqual(common.remote_clause_of(p),
                             remote_clause(self.main, "origin/main", ttl=ttl))
            q = session._remote_provenance("origin/main", root=self.main, ttl=ttl)
            self.assertEqual(session._remote_clause_of(q),
                             session._remote_clause("origin/main", root=self.main,
                                                    ttl=ttl))

    def test_they_render_the_same_clause(self):
        self.assertEqual(remote_clause(self.main, "origin/main", ttl=0),
                         session._remote_clause("origin/main", root=self.main, ttl=0))


class APeersPushMovesTheRefWithoutUsLookingTest(RemoteFixture):
    """THE CONFIGURATION THE ITEM WAS FILED FROM, built rather than described.

    `poga` lanes are worktrees sharing ONE git common dir, so a sibling lane's push
    updates `refs/remotes/origin/main` in the store this checkout reads. The measured
    incident: a lane confirmed its work was published against an `origin/main` its own
    `git fetch` had never reached (rc 128, ssh timeout); the ref was current only
    because a peer had pushed into the shared store. Every probe returned the ordinary
    shape, and the conclusion happened to be true."""

    def setUp(self):
        super().setUp()
        self.lane = self.tmp / "lane"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.lane))
        _identify(self.lane)

    def _peer_lane_pushes(self):
        _commit(self.lane, "peer.txt")
        _git(self.lane, "push", "-q", "origin", "worktree-poga-1:main")

    def test_the_ref_advances_although_this_checkout_never_fetched(self):
        before = self.prov(ttl=10 ** 9)["sha"]
        self._peer_lane_pushes()
        after = self.prov(ttl=10 ** 9)
        self.assertNotEqual(before, after["sha"],
                            "the sibling's push must move the ref in the shared store")
        self.assertEqual(after["moved_by"], "push",
                         "and the helper must be able to say that a push, not a fetch, "
                         "is what put it there")

    def test_a_push_is_contact_and_is_named_as_a_push_not_a_fetch(self):
        """A push that succeeded DID reach the remote — it dates the ref exactly as a
        fetch does. What differs is whose act it was and what it proves, so the clause
        names the act instead of ranking it. Reporting a push as "fetched" would be the
        same collapse in the other direction."""
        self._peer_lane_pushes()
        p = self.prov(ttl=10 ** 9)
        self.assertTrue(p["fresh"])
        self.assertEqual(p["confirmed_by"], "push")
        clause = remote_clause(self.main, "origin/main", ttl=10 ** 9)
        self.assertIn("local push", clause)
        self.assertNotIn("fetched", clause)

    def test_the_lane_and_the_checkout_read_one_shared_ref(self):
        """The premise the whole item rests on. If these ever disagreed, a lane's view
        of `origin` would be its own and none of this would apply."""
        self._peer_lane_pushes()
        self.assertEqual(remote_provenance(self.main, "origin/main")["sha"],
                         remote_provenance(self.lane, "origin/main")["sha"])


@unittest.skipUnless(GIT, "git not available")
class TheBannerBreaksItsSilenceTest(unittest.TestCase):
    """ADR-0108 D3's reference implementation, which had regressed into the very
    collapse its own docstring forbids: *"I could not ask must never render as you are
    current."*

    `_lane_trunk_distance` measures against `_trunk_tip()`, which PREFERS
    `origin/<trunk>` when it is ahead. Nothing on that path fetches. When the remote ref
    is stale it reads as not-ahead, the tip falls back to the local trunk, `behind`
    computes to 0, and the line function returns [] — silence, which this banner's
    contract means "you are current". The third outcome was wired only to UNREADABLE."""

    def _lines(self, *, stale, behind):
        """Drive the real line function with the two facts that decide its output,
        holding everything else at its ordinary value."""
        prov = {"readable": True, "fresh": not stale, "ref": "origin/main",
                "sha": "a" * 40, "age": None if stale else 5.0,
                "confirmed_by": None if stale else "fetch",
                "fetch_age": None, "moved_by": "push", "moved_age": 900.0}
        distance = {"readable": True, "trunk": "main", "tip": "b" * 40,
                    "head": "c" * 40, "behind": behind, "remote": prov}
        real_dist = session._lane_trunk_distance
        real_clause = session._remote_clause
        session._lane_trunk_distance = lambda: distance
        session._remote_clause = lambda *a, **k: "per origin/main at bbbbbbb — freshness CANNOT TELL (probe)"
        try:
            return session._lane_trunk_distance_lines()
        finally:
            session._lane_trunk_distance = real_dist
            session._remote_clause = real_clause

    def test_level_with_a_fresh_trunk_stays_silent(self):
        """The common case must still cost nothing to read, or the line stops being
        read at all — the reason this banner is silent by design."""
        self.assertEqual(self._lines(stale=False, behind=0), [])

    def test_level_with_an_unrefreshed_trunk_is_not_silent(self):
        """The false all-clear, and the regression this fixes."""
        lines = self._lines(stale=True, behind=0)
        self.assertEqual(len(lines), 1)
        self.assertIn("CANNOT TELL", lines[0])
        self.assertIn("not 'current'", lines[0])

    def test_a_behind_count_from_an_unrefreshed_ref_is_dated(self):
        """The number is not wrong, it is UNDERSTATED — it cannot see what the remote
        has that this machine never fetched. So it carries its own date."""
        lines = self._lines(stale=True, behind=7)
        self.assertIn("7 commit(s) behind", lines[0])
        self.assertIn("CANNOT TELL", lines[0])

    def test_a_behind_count_from_a_fresh_ref_is_not_cluttered(self):
        lines = self._lines(stale=False, behind=7)
        self.assertIn("7 commit(s) behind", lines[0])
        self.assertNotIn("CANNOT TELL", lines[0])


if __name__ == "__main__":
    unittest.main()
