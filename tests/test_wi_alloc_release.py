"""WI-0237 — a `wi-alloc` reservation tagged `branch: main` is held by nothing and freed
by nothing, so it blocks the trunk for its full 8-hour TTL.

THE SHAPE. `poga work new` runs against the MAIN checkout even when it is invoked from a
lane — by design (ADR-0073, WI-0193's fix), so an item filed in a lane is not stranded in
it. The number it draws is therefore reserved through the main-anchored front door, and
`_coord_record` stamps that record with `_current_branch()`, which in the main checkout is
`main`. Every release path is lane-shaped: `_coord_release_dead_lanes` and
`_coord_holder_is_dead` are scoped to identities that start with `worktree-`, and a lane
teardown frees holds belonging to the LANE branch. A record tagged `branch: main` is held
by a branch that always exists, drawn by a session that has since ended, and no path frees
it.

WHY IT COSTS SOMETHING RATHER THAN JUST BEING UNTIDY. `_counter_land_gate` reads
`_counter_alloc_holds`, so the residue is not inert: session ~185's `poga integrate` was
refused with *"work-items/WI-0223-… claims WI number 0223, which is reserved by main"* —
the collision check comparing that session's own file against that session's own
unreleased ticket, the very ticket the file was created with. Fourteen of them, WI-0223
through WI-0236, from one session's fourteen successful work items. Twelve are sitting in
the live store as this is written, every one for a number whose item file is already
committed on the trunk.

WHAT THESE TWO TESTS PIN, which is the item's fix candidate (a) — *release the reservation
at the moment the item file is committed; the number is used, the ticket has done its job,
and holding it past that point protects nothing*:

  1. the WRITE side — a number drawn through the front door and then written and committed
     as `work-items/WI-NNNN-<slug>.md` is no longer reserved;
  2. the REAP side — the backstop frees a `branch: main` reservation whose file the trunk
     already carries, instead of skipping it because its TTL has hours to run;
  3. the LIMIT on (1) — a file that was written but whose commit FAILED keeps its number.

They are complementary rather than redundant: (1) closes the hole going forward, (2) is
what drains the twelve records already in the store, and neither one implies the other.
(3) is the boundary that makes (1) safe rather than merely convenient — the release is
tied to the commit, not to the write, because an uncommitted file is invisible to every
scan a sibling lane's draw makes and its number is therefore still doing real work.

Holding the number past the commit protects nothing, and that is not an assumption made
here — it is the property `test_landed_file_number_never_reused` in `test_adr_alloc.py`
already pins from the other side: once the file exists, `_counter_existing_max`,
`_counter_lane_tips_max` and `_counter_trunk_max` keep the number out of circulation on
the strength of the FILE, with no reservation involved.

HOW THEY WERE VERIFIED TO BE ABLE TO FAIL, which is the only thing that makes a green
suite mean anything here ([`verify-in-the-created-configuration`]). (1) and (2) were run
against `git show HEAD:session.py` — the code as it stood before this fix — in a scratch
checkout, and both failed there on their FINAL assertion, with every precondition
(`branch == "main"`, the holder id, not-expired, the file present on the trunk) holding
first. They pass against the fix in the same commit. (3) fails against a release that is
hung on the write rather than on the commit.
"""

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
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")

#: A Claude-session-id-shaped holder, which is what `_coord_identity()` returns for the
#: front door: `poga work` anchors on the main checkout, so `_on_worktree_lane()` is false
#: there and the identity falls to `CLAUDE_CODE_SESSION_ID`. This is the literal id
#: WI-0237 recorded off the record that refused the integrate — a real front-door draw,
#: not a lane key, which is exactly why every lane-shaped release path passes it by.
CSID = "6b888145-2edf-4989-a549-30bd5a21a91e"


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class WiAllocReleaseBase(unittest.TestCase):
    """A real main checkout with a real linked lane, and `session` pointed at MAIN.

    Pointed at main deliberately, and it is the whole fixture: this defect does not
    reproduce from a lane, because from a lane the record would be tagged
    `worktree-poga-1` and the existing release paths would eventually free it. The
    front door's own anchoring is the thing under test, so the fixture stands where the
    front door stands. The lane exists so the shape is honest — a session working in a
    lane, filing items into main's store — and so a `worktree-*` ref is present for
    anything that scans for one.
    """

    def setUp(self):
        # Fictional holders here must not inherit the runner's journal (WI-0126): the
        # `branch: main` record's whole distinguishing feature is WHO holds it, and a
        # borrowed journal would make the fixture's holder and the runner's session read
        # as one.
        # WI-0249: this module drives `_coord_reap`, which reads the dispatch
        # environment. Without this it inherits the dispatch handles of the lane
        # running the suite, and `tests/test_dispatch_fixture_guard.py` says so.
        # WI-0275: the ambient neutraliser is where that clear lives now — it is
        # `neutralize_dispatch_env` plus the identity axis, so one call covers both.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        (self.main / "work-items").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        # The lane pool and the sidecar dir are ignored exactly as they are in the real
        # repo, so `_commit_everything` below can use `add -A` without sweeping either.
        (self.main / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\n", encoding="utf-8")
        (self.main / "work-items" / ".keep").write_text("", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")

        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.lane),
             "main")

        # No ambient Claude session id. `_record_store_write` attributes store writes to
        # `_claude_session_id()` and would otherwise drop a sidecar into the REAL repo's
        # `.session-state` the moment the under-test guard is lifted below (the WI-0068
        # class). With no id it returns before touching anything.
        env = mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""})
        env.start()
        self.addCleanup(env.stop)

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "SESSION_STATE_DIR", "CFG")}
        session.ROOT = self.main
        session.SESSION_STATE_DIR = self.main / ".session-state"
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        session._SHARED_WORK_ROOT = None      # memoized per ROOT; do not inherit a peer's

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        session._SHARED_WORK_ROOT = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────────────────

    def _alloc_path(self, num: str) -> pathlib.Path:
        return session._coord_dir("wi-alloc", create=True) / f"{num}.json"

    def _commit_everything(self, subject: str) -> None:
        """Commit whatever is dirty on the trunk, or do nothing if it is already clean.

        Tolerant of an empty tree on purpose: `_store_autocommit` may or may not have
        committed the file already (it early-returns under the suite), and which of the
        two happened is not what any assertion here is about. The file being ON THE
        TRUNK is the precondition; how it got there is not."""
        _git(self.main, "add", "-A")
        dirty = subprocess.run([GIT, "-C", str(self.main), "status", "--porcelain"],
                               check=True, capture_output=True, text=True).stdout.strip()
        if dirty:
            _git(self.main, "commit", "-qm", subject)

    def _trunk_wi_files(self) -> list[str]:
        r = subprocess.run([GIT, "-C", str(self.main), "ls-tree", "--name-only", "main",
                            f"{session.WI_DIRNAME}/"],
                           check=True, capture_output=True, text=True)
        return (r.stdout or "").split()


@unittest.skipUnless(GIT, "git not available")
class CommittedItemReleasesItsNumberTest(WiAllocReleaseBase):

    def test_a_committed_item_file_releases_its_number(self):
        """The write side. Draw through the front door, write and commit the item file,
        and the ticket the number was drawn on must be gone.

        WHAT IT CAUGHT BEFORE THE FIX: nothing on the write path released a `wi-alloc`
        record, and the one thing that could (`_coord_reap`) was not reached because the
        record is minutes old against an 8-hour TTL. The reservation survived the very
        event that makes it pointless."""
        n = session._wi_reserve_next(CSID)
        self.assertIsNotNone(n, "the coord store must be reachable in the fixture")
        num = f"{n:04d}"
        path = self._alloc_path(num)
        rec = json.loads(path.read_text(encoding="utf-8"))

        # The record's SHAPE is pinned before anything else, so this test can never pass
        # by having quietly stopped reproducing the defect. `branch: main` is the whole
        # bug — it is what every lane-shaped release path fails to match — and a
        # front-door draw produces it with no help from the fixture.
        self.assertEqual(rec["branch"], "main",
                         "the front door anchors on the main checkout, so the record it "
                         "writes is tagged with the one branch that never disappears")
        self.assertEqual(rec["session_id"], CSID)
        self.assertFalse(session._coord_expired(rec),
                         "drawn seconds ago — the full 8h TTL is ahead of it, which is "
                         "why the TTL is not a remedy")

        item = {"id": f"WI-{num}", "title": "an item filed from a lane", "status": "open",
                "section": "next", "blocked_by": [], "group": "", "source": "",
                "impact": "fix", "migration": "", "version": "", "notes": ""}
        # `_wi_write_item` is the store's SOLE writer and the one hook point a new verb
        # cannot forget — it is where WI-0056 hung the auto-commit for exactly that
        # reason, and it is where a release-on-save belongs for the same one. The
        # under-test guard is lifted for this one call so `_store_autocommit` actually
        # runs its git commit rather than returning at the door: ROOT is a temp repo and
        # the session id is cleared, so nothing here can reach the real checkout.
        # Its stdout receipt ("committed <sha> in <tmpdir> — the store is SAVED") is
        # captured rather than left in the suite's output: a passing test that prints a
        # commit sha from a temp repo reads like the suite touched a real one.
        with mock.patch.object(session, "_under_test", return_value=False):
            with contextlib.redirect_stdout(io.StringIO()):
                session._wi_write_item(item)
        self._commit_everything(f"chore(work-items): add WI-{num}")

        on_trunk = [f for f in self._trunk_wi_files()
                    if f.rsplit("/", 1)[-1].startswith(f"WI-{num}-")]
        self.assertTrue(on_trunk,
                        "precondition, not the subject: the item file is committed on "
                        "the trunk before the reservation is judged")

        self.assertFalse(
            path.exists(),
            f"WI-{num} is written and committed on the trunk, so its number is spent and "
            f"the file scan alone keeps it out of circulation — yet the reservation it "
            f"was drawn on is still in the store, tagged branch=main, with hours of TTL "
            f"left. Nothing will free it, and the next `poga integrate` will refuse this "
            f"session's own file as 'reserved by main'. (WI-0237)")
        self.assertNotIn(num, session._coord_list("wi-alloc", include_expired=True),
                         "and it must be gone from the store, not merely expired")
        self.assertNotIn(num, session._wi_alloc_holds(),
                         "the land gate reads _counter_alloc_holds — that is the surface "
                         "on which this residue costs a trunk integrate")


@unittest.skipUnless(GIT, "git not available")
class ReapFreesTrunkVisibleReservationTest(WiAllocReleaseBase):

    def test_reap_frees_a_reservation_whose_file_is_on_the_trunk(self):
        """The backstop side, and the half that drains the twelve records already in the
        live store. A fix that only closes the write path leaves them there until their
        TTLs run out.

        WHAT IT CAUGHT BEFORE THE FIX, precisely: `_coord_reap` read
        `if rec is not None and not _coord_expired(rec, now): continue` BEFORE it ever
        reached the R5b wi-alloc carve-out, so an unexpired record was skipped without the
        trunk ever being consulted. The carve-out below it already computed exactly the
        fact that settles this — `trunk_has` — and already documented why the rule it
        implements is meaningless on the trunk ('committed but not yet landed' cannot
        describe a trunk branch). It just never ran early enough to use it."""
        num = "0005"
        (self.main / session.WI_DIRNAME / f"WI-{num}-already-landed.md").write_text(
            f"# WI-{num}: already landed\n\n- status: open\n", encoding="utf-8")
        self._commit_everything(f"chore(work-items): add WI-{num}")
        self.assertTrue(
            any(f.rsplit("/", 1)[-1].startswith(f"WI-{num}-")
                for f in self._trunk_wi_files()),
            "precondition: the trunk carries the file this reservation was drawn for")

        d = session._coord_dir("wi-alloc", create=True)
        rec = session._coord_record(CSID, session.WI_ALLOC_TTL_SECONDS, name=num,
                                    kind="wi-alloc", branch="main")
        (d / f"{num}.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertFalse(session._coord_expired(rec),
                         "UNEXPIRED is the case that matters: an expired one already "
                         "reaps, which is why the store fills up during a working day "
                         "and empties overnight")

        session._coord_reap(("wi-alloc",))

        self.assertFalse(
            (d / f"{num}.json").exists(),
            f"the trunk carries WI-{num}, so the number is spent and the reservation "
            f"protects nothing — the R5b rule it is being spared by ('a lane may still "
            f"land this file') cannot describe branch=main at all. The reap skipped it "
            f"purely because its TTL has not run out. (WI-0237)")
        self.assertNotIn(num, session._wi_alloc_holds())


@unittest.skipUnless(GIT, "git not available")
class AnUncommittedFileKeepsItsNumberTest(WiAllocReleaseBase):

    def test_a_file_git_never_took_keeps_its_number(self):
        """The boundary that makes the write-side release safe: the number is freed by the
        COMMIT, never by the write.

        The trap this pins is that the two are hard to tell apart from inside
        `_store_autocommit`. It decides "nothing changed, no empty commit" from an empty
        `git status --porcelain -- <paths>` — and an empty status is ALSO what an
        unmatched pathspec produces. Here the store file is ignored, so `git add` takes
        nothing, `status` is empty with exit 0, and the file is not committed anywhere:
        not on the trunk, not on a lane tip, not in a sibling's working tree. Its number is
        still the only thing standing between two lanes drawing it, and a release keyed on
        that empty status would hand it away on the strength of a question git declined to
        answer ([`declare-what-a-check-assumes`]).

        Ignoring the file is a construction, not the expected failure — the real shapes
        are a locked index or a mid-rebase checkout, which `cmd_wi_commit` exists to
        repair. What matters is that they all reach this branch, and that the number
        survives all of them until the file is genuinely saved."""
        (self.main / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\nwork-items/WI-*.md\n", encoding="utf-8")
        _git(self.main, "add", "--", ".gitignore")
        _git(self.main, "commit", "-qm", "chore: ignore new work items")

        n = session._wi_reserve_next(CSID)
        self.assertIsNotNone(n, "the coord store must be reachable in the fixture")
        num = f"{n:04d}"
        path = self._alloc_path(num)

        item = {"id": f"WI-{num}", "title": "a write git never took", "status": "open",
                "section": "next", "blocked_by": [], "group": "", "source": "",
                "impact": "fix", "migration": "", "version": "", "notes": ""}
        with mock.patch.object(session, "_under_test", return_value=False):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                session._wi_write_item(item)

        self.assertFalse(
            [f for f in self._trunk_wi_files()
             if f.rsplit("/", 1)[-1].startswith(f"WI-{num}-")],
            "precondition, not the subject: git took nothing, so the file is not on the "
            "trunk and the file scans cannot account for its number")
        self.assertTrue(
            path.exists(),
            f"WI-{num} was written but never committed, so its number is invisible to "
            f"every scan a sibling lane's draw makes — the reservation is the only thing "
            f"holding it, and freeing it here would let two lanes draw the same number. "
            f"The release must key on the COMMIT, not on the write. (WI-0237)")
        self.assertIn(num, session._wi_alloc_holds(),
                      "and it must still be a hold, not merely a file on disk")


if __name__ == "__main__":
    unittest.main()
