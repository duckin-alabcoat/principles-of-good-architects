"""Discarding a lane that provably holds nothing — and refusing every lane that doesn't.

WI-0111. `_lane_holds_only_phantom_debris` already recognised the lane that opened and
died with its stub journal UNCOMMITTED. It cannot see the same nothing once the `Stop`
hook's `wip(checkpoint)` commit has swept it onto the branch: the lane is then *unmerged*,
so the reaper leaves it alone (ADR-0089 D3, correctly — nothing stranded is swept
automatically), and it is reported at every session start forever. The federation carried
two of these; the substrate's own advice for them was `git branch -D`, which `check-bash`
denies as destructive, also correctly.

The way out of a guard that is right to refuse is a verb whose PREDICATE carries the
safety argument. So what has to be pinned here is not that the verb deletes — it is
everything it must REFUSE to delete:

  - a lane holding a journal with real narrative;
  - a lane whose commits touch any non-journal path, however small;
  - a lane whose journal was a stub at some intermediate commit but gained narrative by
    the branch tip;
  - a lane whose history cannot be read at all (unknown is never "safe to delete").

The last is the one that matters most and is easiest to get wrong: defaulting an
unreadable answer to benign is exactly how a real deletion gets waved through — the same
fail-closed rule session ~133 established for `_wi_id_ever_existed`.

stdlib unittest: python3 -m unittest tests.test_phantom_lane_discard
"""

import argparse
import io
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
import session  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class PhantomDiscardBase(unittest.TestCase):
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
        (self.main / "sessions" / "journal").mkdir(parents=True)
        (self.main / "sessions" / "journal" / ".keep").write_text("", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "CFG_RAW")}
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        session.CFG_RAW = {"trunk": "main"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lane(self, n=1):
        """A lane worktree on its own branch, as `poga` creates it."""
        path = self.main / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-poga-{n}",
             str(path), "main")
        return {"name": f"poga-{n}", "branch": f"worktree-poga-{n}",
                "path": str(path), "status": "unmerged", "is_self": False}

    def commit_journal(self, lane, body, name="20260810T0000Z-runner-aaaa", msg="wip"):
        p = pathlib.Path(lane["path"]) / "sessions" / "journal" / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nsession-id: {name}\n---\n\n{body}", encoding="utf-8")
        _git(lane["path"], "add", "-A")
        _git(lane["path"], "commit", "-qm", msg)


class PredicateTest(PhantomDiscardBase):
    def test_a_committed_stub_journal_is_phantom_debris(self):
        """The case the uncommitted sibling cannot see, and the whole reason for this."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        self.assertTrue(session._lane_commits_are_only_phantom_debris(l))

    def test_a_journal_with_real_narrative_is_not(self):
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- Shipped the thing.\n")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_any_non_journal_path_disqualifies_the_whole_lane(self):
        """One byte of real work anywhere on the branch and the lane is not empty."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        (pathlib.Path(l["path"]) / "f.txt").write_text("edited\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "real work")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_a_stub_that_later_gained_narrative_is_judged_at_the_tip(self):
        """An intermediate stub revision must not read as an empty lane."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        self.commit_journal(l, "### What happened\n\n- Then I did the work.\n", msg="wip 2")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_a_commit_touching_only_liveness_sidecars_is_debris(self):
        """`.session-state/` is gitignored by design and never holds authored content.
        Where a member tracks it anyway, every session commits heartbeat bumps and a
        stop-checkpoint turns that into a branch that reads as stranded WORK. One member's
        entire red lane was one `.live` and one `.ended` file."""
        l = self.lane()
        d = pathlib.Path(l["path"]) / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "abc.live").write_text("beat\n", encoding="utf-8")
        (d / "abc.ended").write_text("done\n", encoding="utf-8")
        _git(l["path"], "add", "-f", ".session-state")
        _git(l["path"], "commit", "-qm", "wip(checkpoint)")
        self.assertTrue(session._lane_commits_are_only_phantom_debris(l))

    def test_a_sidecar_commit_alongside_real_work_is_still_refused(self):
        """The sidecar allowance must not become a door for anything travelling with it."""
        l = self.lane()
        d = pathlib.Path(l["path"]) / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "abc.live").write_text("beat\n", encoding="utf-8")
        (pathlib.Path(l["path"]) / "f.txt").write_text("real\n", encoding="utf-8")
        _git(l["path"], "add", "-f", ".session-state", "f.txt")
        _git(l["path"], "commit", "-qm", "wip(checkpoint)")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_a_lane_with_nothing_ahead_is_not_this_functions_case(self):
        self.assertFalse(session._lane_commits_are_only_phantom_debris(self.lane()))

    def test_an_unreadable_history_fails_CLOSED(self):
        """Unknown is never "safe to delete" — defaulting benign is how a real deletion
        gets waved through. Same fail-closed rule as `_wi_id_ever_existed`."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        self.assertTrue(session._lane_commits_are_only_phantom_debris(l))
        with mock.patch.object(session, "sh",
                               side_effect=OSError("git is unavailable")):
            self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_the_question_is_asked_AT_the_lane_not_at_the_process_cwd(self):
        """A cwd-relative git call answers about whatever repo the process is standing in.
        It gave the right answer in the federation for the wrong reason — cwd and lane
        share an object store there — and would be arbitrarily wrong anywhere else."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        calls = []
        real = session.sh

        def spy(cmd, **kw):
            if cmd and cmd[0] == "git":
                calls.append(cmd)
            return real(cmd, **kw)

        with mock.patch.object(session, "sh", side_effect=spy):
            session._lane_commits_are_only_phantom_debris(l)
        self.assertTrue(calls, "expected git calls")
        for c in calls:
            self.assertEqual(c[1:3], ["-C", l["path"]], f"not anchored at the lane: {c}")


class CommandTest(PhantomDiscardBase):
    def _run(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_discard_phantom_lanes(argparse.Namespace(**{"dry_run": False, **kw}))
        return buf.getvalue()

    def test_dry_run_deletes_nothing(self):
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            out = self._run(dry_run=True)
        self.assertIn("would discard 1 lane(s)", out)
        self.assertTrue(pathlib.Path(l["path"]).exists())

    def test_a_lane_holding_real_work_is_refused_BY_NAME(self):
        """Silence would be indistinguishable from having swept it."""
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- Real.\n")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            out = self._run()
        self.assertIn("refused: poga-1", out)
        self.assertIn("holds real work", out)
        self.assertTrue(pathlib.Path(l["path"]).exists())

    def test_a_live_lane_is_never_reachable_however_empty_it_looks(self):
        """This verb force-deletes a branch. The one lane it must never reach is one
        someone is working in — and a session holds its stub journal for its whole life,
        so a live lane looks exactly like a phantom."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        with mock.patch.object(session, "_tree_has_live_session", return_value=True):
            out = self._run()
        self.assertIn("nothing to discard", out)
        self.assertTrue(pathlib.Path(l["path"]).exists())


class SupersededJournalTest(PhantomDiscardBase):
    """A lane holding real narrative that the trunk has already CLOSED.

    Not a stub, so the phantom predicate rightly refuses it — and not unlanded work
    either, because the finished, stamped version of that same session is already on the
    trunk. From inside the lane the two are indistinguishable, which is how a rescue
    commit got written for a journal that was never lost (a member's poga-5, session ~134)."""

    def _trunk_journal(self, name, body, ended="2026-08-04T17:45:56"):
        p = self.main / "sessions" / "journal" / f"{name}.md"
        p.write_text(f"---\nsession-id: {name}\nended: {ended}\nduration: 6h 42m\n---\n\n{body}",
                     encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "close")

    def test_a_journal_already_closed_on_the_trunk_is_superseded(self):
        name = "20260804T1200Z-runner-d951"
        self._trunk_journal(name, "### What happened\n\n- The finished version.\n")
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- An earlier snapshot.\n", name=name)
        self.assertTrue(session._lane_journals_are_superseded_on_trunk(l))

    def test_a_journal_the_trunk_has_never_seen_is_REAL_WORK(self):
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- Only here.\n", name="20260101T0000Z-x-aaaa")
        self.assertFalse(session._lane_journals_are_superseded_on_trunk(l))

    def test_a_journal_on_the_trunk_but_still_OPEN_is_not_superseded(self):
        """Present is not the same as finished — an open trunk copy may still need this."""
        name = "20260804T1200Z-runner-d951"
        self._trunk_journal(name, "### What happened\n\n- Still going.\n", ended="")
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- Snapshot.\n", name=name)
        self.assertFalse(session._lane_journals_are_superseded_on_trunk(l))

    def test_any_non_journal_path_disqualifies_it(self):
        name = "20260804T1200Z-runner-d951"
        self._trunk_journal(name, "### What happened\n\n- Done.\n")
        l = self.lane()
        self.commit_journal(l, "### What happened\n\n- Snapshot.\n", name=name)
        (pathlib.Path(l["path"]) / "f.txt").write_text("real\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "real work")
        self.assertFalse(session._lane_journals_are_superseded_on_trunk(l))


class StaleLockTest(PhantomDiscardBase):
    """A lock Claude Code takes on a `--worktree` lane and never releases.

    The reason names its owner — `claude session poga-2 (pid 3774 start …)` — and when
    that session dies without unlocking, every sweep we own fails against the lane with
    `cannot remove a locked working tree`. One member carried one for two days behind a pid
    that had been gone the whole time. The pid is what makes releasing it safe rather
    than a guess, so what is pinned here is that a lock is released ONLY on proof the
    owner is gone."""

    def _lock(self, lane, reason):
        subprocess.run([GIT, "-C", str(self.main), "worktree", "lock", "--reason", reason,
                        lane["path"]], check=True, capture_output=True)

    def _is_locked(self, lane):
        r = subprocess.run([GIT, "-C", str(self.main), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True)
        target, cur = str(pathlib.Path(lane["path"]).resolve()), None
        for ln in r.stdout.splitlines():
            if ln.startswith("worktree "):
                cur = str(pathlib.Path(ln[9:].strip()).resolve())
            elif ln.startswith("locked") and cur == target:
                return True
        return False

    def test_a_lock_whose_pid_is_gone_is_released(self):
        l = self.lane()
        self._lock(l, "claude session poga-1 (pid 999999 start Thu Jan 1 12:00:00 2026)")
        with mock.patch.object(session, "_pid_alive", return_value=False):
            self.assertTrue(session._unlock_if_owner_is_dead(l["path"]))
        self.assertFalse(self._is_locked(l))

    def test_a_lock_whose_pid_is_ALIVE_is_left_alone(self):
        """The whole point of the lock. A live session must never be swept."""
        l = self.lane()
        self._lock(l, "claude session poga-1 (pid 4242 start Thu Jan 1 12:00:00 2026)")
        with mock.patch.object(session, "_pid_alive", return_value=True):
            self.assertFalse(session._unlock_if_owner_is_dead(l["path"]))
        self.assertTrue(self._is_locked(l))

    def test_a_lock_reason_naming_no_pid_is_left_alone(self):
        """No pid means no evidence, and no evidence is not permission."""
        l = self.lane()
        self._lock(l, "held by something that did not say what")
        self.assertFalse(session._unlock_if_owner_is_dead(l["path"]))
        self.assertTrue(self._is_locked(l))

    def test_an_unlocked_worktree_is_not_touched(self):
        self.assertFalse(session._unlock_if_owner_is_dead(self.lane()["path"]))

    def test_the_discard_clears_a_dead_lock_and_removes_the_lane(self):
        """End to end: the state that member was actually in."""
        l = self.lane()
        self.commit_journal(l, session._STUB_BODY)
        self._lock(l, "claude session poga-1 (pid 999999 start Thu Jan 1 12:00:00 2026)")
        buf = io.StringIO()
        with mock.patch.object(session, "_tree_has_live_session", return_value=False), \
             mock.patch.object(session, "_pid_alive", return_value=False), \
             redirect_stdout(buf):
            session.cmd_discard_phantom_lanes(argparse.Namespace(dry_run=False))
        self.assertIn("unlocked:", buf.getvalue())
        self.assertIn("discarded:", buf.getvalue())
        self.assertFalse(pathlib.Path(l["path"]).exists())


class ContentNotShaTest(PhantomDiscardBase):
    """WI-0147 — 'is this work on the trunk' is a question about CONTENT, and asking it
    about SHA reachability gets a different, wrong answer after a rebase, a squash, or a
    cherry-pick recovery.

    Hit live on 2026-08-22: poga-2's session died holding two work-item commits. Session
    ~166 recovered them by cherry-pick and landed them, so identical patches sat on main
    under different SHAs. Every verb still called the lane `unmerged`, refused to retire
    it, and printed `git branch -D` as the remedy — a command its own guard denies. The
    lane then kept consuming the dispatch cap, which is the mechanism that made D-4d41da
    spawn zero lanes in the first place.
    """

    def _land_by_cherry_pick(self, lane, sha_count=1):
        """Land the lane's work on main the way a recovery does — different SHAs, same
        patches.

        THE ADVANCE ON MAIN IS LOAD-BEARING, and leaving it out is how the first version of
        these tests passed against a deliberately broken build. Cherry-picking a commit onto
        a main whose tip is still that commit's own parent reproduces it byte for byte —
        same tree, same parent, same metadata, therefore the SAME SHA — so SHA-reachability
        and patch-equivalence agree and the test proves nothing. Advancing main first is
        what makes the two answers differ, which is the real recovery situation."""
        (self.main / "unrelated.txt").write_text("main moved on\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "unrelated work on main")
        shas = subprocess.run(["git", "-C", str(lane["path"]), "log", "--format=%H",
                               f"main..{lane['branch']}", "--reverse"],
                              capture_output=True, text=True, check=True).stdout.split()
        for sha in shas[:sha_count]:
            _git(self.main, "cherry-pick", sha)

    def test_a_lane_whose_work_was_recovered_elsewhere_counts_as_on_trunk(self):
        l = self.lane()
        (pathlib.Path(l["path"]) / "f.txt").write_text("real work\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "real work")
        self.assertFalse(session._lane_work_is_on_trunk(l["branch"], "main"),
                         "not landed anywhere yet")
        self._land_by_cherry_pick(l)
        self.assertTrue(session._lane_work_is_on_trunk(l["branch"], "main"),
                        "same patch, different SHA — the work IS on the trunk")

    def test_a_genuinely_unlanded_lane_is_still_held_back(self):
        """The safety half. Widening this must not start deleting real work."""
        l = self.lane()
        (pathlib.Path(l["path"]) / "f.txt").write_text("never landed\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "real work")
        self.assertFalse(session._lane_work_is_on_trunk(l["branch"], "main"))

    def test_an_unreadable_history_is_kept_not_swept(self):
        with mock.patch.object(session, "sh") as fake:
            fake.return_value = subprocess.CompletedProcess([], 128, "", "fatal")
            self.assertFalse(session._lane_work_is_on_trunk("worktree-poga-9", "main"))

    def test_the_predicate_ignores_commits_already_on_trunk_by_content(self):
        """poga-2's exact shape: two recovered work-item commits plus one stub-journal
        checkpoint. Walking `trunk..branch` sees the work-item commits and refuses; asking
        `git cherry` sees only the stub, which is what the lane actually still holds."""
        l = self.lane()
        (pathlib.Path(l["path"]) / "f.txt").write_text("recovered later\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "work that gets recovered")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l),
                         "while genuinely unlanded, this lane holds real work")
        self._land_by_cherry_pick(l)
        self.commit_journal(l, session._STUB_BODY, msg="wip(checkpoint)")
        self.assertTrue(session._lane_commits_are_only_phantom_debris(l),
                        "the only content still held is an empty session stub")

    def test_the_displayed_ahead_count_is_by_content_too(self):
        """The symptom WI-0147 is named for: '2 commit(s) not on main' over commits whose
        patches are already there. The operator either redoes landed work or learns to
        ignore the number, and both are worse than a right answer."""
        l = self.lane()
        (pathlib.Path(l["path"]) / "f.txt").write_text("recovered later\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "work that gets recovered")
        self.assertEqual(session._lane_commits_ahead_count(l["branch"]), "1")
        self._land_by_cherry_pick(l)
        self.assertEqual(session._lane_commits_ahead_count(l["branch"]), "0",
                         "the patch is on the trunk; only its SHA is not")

    def test_an_unreadable_ahead_count_says_so_rather_than_zero(self):
        with mock.patch.object(session, "sh") as fake:
            fake.return_value = subprocess.CompletedProcess([], 128, "", "fatal")
            self.assertEqual(session._lane_commits_ahead_count("worktree-poga-9"), "?")

    def test_real_unlanded_work_alongside_a_stub_still_refuses(self):
        l = self.lane()
        (pathlib.Path(l["path"]) / "f.txt").write_text("never landed\n", encoding="utf-8")
        _git(l["path"], "add", "-A")
        _git(l["path"], "commit", "-qm", "real work")
        self.commit_journal(l, session._STUB_BODY, msg="wip(checkpoint)")
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))


class AddsNothingTheTrunkLacksTest(PhantomDiscardBase):
    """WI-0141 / session ~174 — the phantom shape that is about CONTRIBUTION, not content.

    `poga-4` held a closed session's work, so neither existing predicate could call it
    empty. Its 24 substantive commits were then cherry-picked into a sibling lane and landed
    from there, leaving nine `recompile views and stamp STATUS` commits and two that wrote
    work-item files whose bytes the trunk already had. Nothing empty, nothing superseded,
    and nothing to keep — but no predicate could say so, so the only advice the substrate
    could give was `git branch -D`, which `check-bash` denies as destructive, correctly.

    The refusing direction is the one that matters. A predicate that sweeps a branch holding
    the only copy of something is worse than no predicate at all, so every test below that
    proves True has a near-twin that proves False on one changed byte.
    """

    def setUp(self):
        super().setUp()
        session.CFG = dict(session.CFG,
                           handoff=pathlib.Path("session-handoff.md"),
                           status=pathlib.Path("STATUS.md"))

    def _commit(self, where, files, msg="c"):
        for rel, body in files.items():
            p = pathlib.Path(where) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if body is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(body, encoding="utf-8")
        _git(where, "add", "-A")
        _git(where, "commit", "-qm", msg)

    def test_a_lane_holding_only_stale_generated_views_adds_nothing(self):
        """The nine `recompile views` commits, which is what a landed lane leaves behind.
        A generated view is a render, not content: the next land rewrites both from the
        journals, so a stale copy on a branch is a snapshot of an older render."""
        self._commit(self.main, {"STATUS.md": "trunk render\n",
                                 "session-handoff.md": "trunk handoff\n"}, "trunk views")
        lane = self.lane(1)
        _git(lane["path"], "merge", "-q", "main")
        self._commit(lane["path"], {"STATUS.md": "lane render\n"}, "recompile views")
        self.assertTrue(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_file_written_to_the_bytes_the_trunk_already_has_adds_nothing(self):
        """The two commits I wrote fighting the rebase: they set work-item files to exactly
        the trunk's content. Ahead by sha, contributing nothing."""
        lane = self.lane(1)
        self._commit(lane["path"], {"f.txt": "changed\n"}, "wander")
        self._commit(lane["path"], {"f.txt": "base\n"}, "back to the trunk's bytes")
        self.assertTrue(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_path_deleted_on_both_sides_adds_nothing(self):
        """A lane that deleted a file the trunk no longer has either — usually because the
        trunk RENAMED it — has nothing left to contribute through that path."""
        self._commit(self.main, {"g.txt": "shared\n"}, "add g")
        lane = self.lane(1)
        self._commit(self.main, {"g.txt": None}, "trunk drops g")
        self._commit(lane["path"], {"g.txt": None, "STATUS.md": "lane render\n"},
                     "lane drops g and recompiles")
        self.assertTrue(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_file_the_trunk_has_NEVER_SEEN_is_real_work(self):
        """The near-twin, and the whole safety argument. One file only this branch has and
        the answer must flip — this is the case where deleting the branch loses work."""
        lane = self.lane(1)
        self._commit(lane["path"], {"deploy/runner.py": "the only copy\n"}, "real work")
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_one_differing_byte_is_enough_to_refuse(self):
        lane = self.lane(1)
        self._commit(lane["path"], {"f.txt": "base \n"}, "one trailing space")
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_generated_view_does_not_launder_real_work_beside_it(self):
        """A commit that touches a generated view AND something else is not excused by the
        view — the sidecar test's argument, one predicate over."""
        self._commit(self.main, {"STATUS.md": "trunk render\n"}, "trunk view")
        lane = self.lane(1)
        _git(lane["path"], "merge", "-q", "main")
        self._commit(lane["path"], {"STATUS.md": "lane render\n",
                                    "adr/0103-real.md": "a decision only here\n"}, "both")
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_lane_with_nothing_ahead_is_not_this_functions_case(self):
        """Returns False rather than True on an empty set: 'nothing ahead' is the merged
        case, which the caller classifies for itself and reports differently."""
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(self.lane(1)))

    def test_an_unreadable_history_fails_CLOSED(self):
        lane = dict(self.lane(1), path=str(self.tmp / "does-not-exist"))
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(lane))

    def test_a_lane_with_no_branch_fails_CLOSED(self):
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks({"path": str(self.main)}))

    def test_the_command_discards_it_and_names_THIS_reason(self):
        """A receipt that describes the wrong shape is the defect this verb already fixed
        once. Four proofs now reach the same delete; the operator must be told which."""
        self._commit(self.main, {"STATUS.md": "trunk render\n"}, "trunk view")
        lane = self.lane(1)
        _git(lane["path"], "merge", "-q", "main")
        self._commit(lane["path"], {"STATUS.md": "lane render\n"}, "recompile views")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_discard_phantom_lanes(argparse.Namespace(dry_run=True))
        out = buf.getvalue()
        self.assertIn("poga-1", out)
        self.assertIn("reached the trunk by another route", out)

    def test_the_command_still_refuses_a_lane_holding_the_only_copy(self):
        lane = self.lane(1)
        self._commit(lane["path"], {"deploy/runner.py": "the only copy\n"}, "real work")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_discard_phantom_lanes(argparse.Namespace(dry_run=True))
        out = buf.getvalue()
        self.assertIn("refused: poga-1", out)
        self.assertNotIn("would discard", out)
        self.assertTrue((pathlib.Path(lane["path"]) / "deploy" / "runner.py").is_file())


# --- WI-0377 -------------------------------------------------------------------
# A11. `_is_stub_body` is byte-exact against the opening stub, and `_journal_note` files
# a line under `### Notes filed` during lazy start on every session a checkout with
# authored work opens. So every predicate built on the strict form answered "not a stub"
# for a journal nobody wrote, and the lane holding it was never debris, never phantom,
# never rollback-safe and never reaped -- it was reported as stranded work forever.
#
# The fixture is DERIVED from `_STUB_BODY`, never spelled again: a hand-copied stub that
# drifts from the constant would make every assertion below pass for the wrong reason.

def _machine_noted_stub():
    """The opening stub plus the one line the HARNESS itself files into it."""
    return (session._STUB_BODY
            + "\n- the shared checkout holds AUTHORED work and was NOT materialized\n")


class HarnessNoteDoesNotDefeatTheStubPredicateTest(PhantomDiscardBase):
    """Every call site, asserted on the SAME body, because the defect was the class.

    WI-0377 named three call sites; there were four. Fixing the three named ones would
    have left `_lane_commits_are_only_phantom_debris` -- the committed half, and the one
    whose lanes accumulate -- answering the old way, with the reported symptom gone
    ([`retire-the-class-not-the-instance`](habits/master.md#retire-the-class-not-the-instance)).
    """

    def test_the_machine_note_really_does_defeat_the_strict_predicate(self):
        """The premise, pinned first. If this ever goes False the tests below are passing
        for a reason that has nothing to do with what they claim to check."""
        self.assertFalse(session._is_stub_body(_machine_noted_stub()))
        self.assertTrue(session._journal_is_unauthored(_machine_noted_stub()))

    def test_a_committed_machine_noted_journal_is_phantom_debris(self):
        """Call site: `_lane_commits_are_only_phantom_debris` (the one WI-0377 missed)."""
        l = self.lane()
        self.commit_journal(l, _machine_noted_stub())
        self.assertTrue(session._lane_commits_are_only_phantom_debris(l))

    def test_an_uncommitted_machine_noted_journal_is_phantom_debris(self):
        """Call site: `_lane_holds_only_phantom_debris`."""
        l = self.lane()
        p = pathlib.Path(l["path"]) / "sessions" / "journal" / "20260810T0000Z-runner-bbbb.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nsession-id: x\n---\n\n" + _machine_noted_stub(),
                     encoding="utf-8")
        self.assertTrue(session._lane_holds_only_phantom_debris(l))

    def test_a_lane_whose_only_journal_is_machine_noted_can_be_rolled_back(self):
        """Call site: `_launch_left_nothing` in config.py -- the failed-launch rollback."""
        l = self.lane()
        p = pathlib.Path(l["path"]) / "sessions" / "journal" / "20260810T0000Z-runner-cccc.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\nsession-id: x\n---\n\n" + _machine_noted_stub(),
                     encoding="utf-8")
        safe, why = session._launch_left_nothing(pathlib.Path(l["path"]), l["branch"])
        self.assertTrue(safe, "refused a machine-noted stub: " + why)

    def test_the_reaper_phantom_rule_reads_a_machine_noted_body_as_a_stub(self):
        """Call site: the phantom rule in `_reap_dead_journals`, asserted on the predicate
        it gates on -- a machine-noted body under the phantom age is a non-event whose
        ordinal is reclaimed, exactly as a byte-exact stub is."""
        self.assertTrue(session._journal_is_unauthored(_machine_noted_stub()))
        self.assertTrue(session._journal_is_unauthored(session._STUB_BODY))

    def test_one_authored_line_still_disqualifies_every_call_site(self):
        """The direction that must NOT have moved. Widening the predicate widens what gets
        DELETED, so the negative control is the whole safety argument: a journal an
        Architect touched is still refused."""
        authored = _machine_noted_stub().replace("- Session opened.", "- Shipped it.")
        self.assertFalse(session._journal_is_unauthored(authored))
        l = self.lane()
        self.commit_journal(l, authored)
        self.assertFalse(session._lane_commits_are_only_phantom_debris(l))

    def test_narrative_filed_under_the_harness_heading_is_the_known_widening(self):
        """DECLARED, not discovered later. `### Notes filed` is the one section the harness
        writes, so the predicate cannot tell a machine line there from an Architect one.
        That is the price of the fix and it is bounded: every section above the heading
        must still be byte-exact, so a journal carrying any narrative of its own is safe.
        Asserted so the boundary is visible rather than implied
        ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes))."""
        human_only = session._STUB_BODY + "\n- A note I typed and nothing else.\n"
        self.assertTrue(session._journal_is_unauthored(human_only))


class ReapLaneNotMergedBranchTest(PhantomDiscardBase):
    """A12, and the item's diagnosis of it was wrong in both halves. MEASURED, not read.

    `_reap_lane` carried `ok = ok and d.returncode == 0` TWICE -- once correctly inside
    the `else:` that binds `d`, and once again just after it, at the outer level. WI-0377
    recorded that as "CONFIRMED LIVE ... by reading 3785-3806": a `NameError` the first
    time the `if not merged:` branch runs and a STALE `d` every time after. The line is
    gone now because a duplicate that reads as a live crash is worth removing on its own,
    but neither failure was ever reachable, and the record should say so:

      * NOT a stale `d`. `d` is a local of `_reap_lane`, which is called once per lane.
        Every call gets a fresh frame, so there is no earlier iteration to be stale from.

      * NOT a `NameError` either -- and this is the half that needed running rather than
        reading. `and` SHORT-CIRCUITS. The `if not merged:` branch sets `ok = False` on
        the line immediately above, so the duplicate evaluates `False and ...` and never
        touches `d` at all. Probed on the pre-fix code through this very fixture: the
        not-merged branch returns `(False, [... branch delete SKIPPED ...])` and raises
        nothing. The duplicate was provably dead -- idempotent in the `else:` arm, where
        the same term is already applied, and unevaluated in the other.

    Which is the whole reason these tests exist rather than a code reading
    ([`a-timing-claim-is-measured-never-read`](habits/master.md#a-timing-claim-is-measured-never-read)):
    reading the two lines shows an unbound name, and stops one operator short of the
    evaluation rule that decides whether it is ever read. What is pinned below is the
    BEHAVIOUR of the branch WI-0377 called unreachable -- it was always reachable -- so
    the removal is demonstrably a no-op and a future edit that makes `ok` True before the
    skip cannot quietly reintroduce the crash the item described."""

    def _reapable_lane(self, n=1):
        l = self.lane(n)
        # `_scan_lanes` hands `_reap_lane` a real `Path`; the shared fixture stores a str
        # because the predicates only ever `Path(...)` it back. Coerce here rather than in
        # the fixture, so the other suites keep the shape they assert on.
        l.update({"path": pathlib.Path(l["path"]), "registered": True, "locked": False,
                  "lock_pid": None, "status": "reap", "age_min": 999})
        return l

    def test_a_lane_whose_branch_is_not_on_the_trunk_reports_instead_of_raising(self):
        l = self._reapable_lane()
        self.commit_journal(l, "### What happened\n\n- Real, unlanded work.\n")
        ok, acts = session._reap_lane(l, self.main / ".claude" / "worktrees")
        self.assertFalse(ok, "unlanded work is not the reaper's to delete")
        self.assertTrue(any("branch delete SKIPPED" in a for a in acts), acts)
        self.assertTrue(any("work is NOT landed" in a for a in acts), acts)

    def test_the_unmerged_branch_still_exists_afterwards(self):
        """The outcome the crash was hiding: `ok=False` must mean the branch was KEPT."""
        l = self._reapable_lane()
        self.commit_journal(l, "### What happened\n\n- Real, unlanded work.\n")
        session._reap_lane(l, self.main / ".claude" / "worktrees")
        refs = subprocess.run([GIT, "-C", str(self.main), "branch",
                               "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout
        self.assertIn(l["branch"], refs, "the reaper deleted work it said it skipped")

    def test_a_skipped_branch_delete_always_reports_not_ok(self):
        """The invariant the removed duplicate depended on, pinned in its own right.

        `ok` is False whenever the branch delete is skipped -- which is what made
        `False and d.returncode == 0` short-circuit past an unbound `d`. It matters
        independently of the duplicate: `cmd_reap_lanes` counts successes off this flag,
        so a skipped delete that returned ok=True would report a lane as reaped while its
        branch, and the work on it, were still there."""
        l = self._reapable_lane(3)
        self.commit_journal(l, "### What happened\n\n- Real, unlanded work.\n")
        ok, acts = session._reap_lane(l, self.main / ".claude" / "worktrees")
        self.assertFalse(ok)
        self.assertFalse(any("deleted merged branch" in a for a in acts), acts)

    def test_the_merged_path_still_deletes_the_branch(self):
        """The half that always worked, pinned so removing the duplicate line cannot have
        quietly removed the `ok` term the `else:` branch genuinely needs."""
        l = self._reapable_lane(2)
        self.commit_journal(l, session._STUB_BODY)
        subprocess.run([GIT, "-C", str(self.main), "merge", "-q", "--no-ff",
                        "-m", "land", l["branch"]], capture_output=True, text=True)
        ok, acts = session._reap_lane(l, self.main / ".claude" / "worktrees")
        self.assertTrue(any("deleted merged branch" in a for a in acts), acts)
        refs = subprocess.run([GIT, "-C", str(self.main), "branch",
                               "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout
        self.assertNotIn(l["branch"], refs)


def _is_stub_body_call_line():
    """The line `_journal_is_unauthored` calls its core on -- looked up, never hardcoded,
    so the guard below does not go red every time the file above it grows a line."""
    import ast
    src = pathlib.Path(session.__file__).resolve().parent / "sessionlib" / "config.py"
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_journal_is_unauthored":
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_is_stub_body"):
                    return node.lineno
    raise AssertionError("_journal_is_unauthored no longer calls _is_stub_body at all")


class IsStubBodyHasExactlyOneCallerTest(unittest.TestCase):
    """The STRUCTURAL guard, not another patch.

    WI-0377 is the second session to record this defect, which is this repo's own
    threshold for building the enforcement into code rather than trusting the next author
    to remember
    ([`add-structural-guard-on-recurrence`](habits/master.md#add-structural-guard-on-recurrence)).
    The failure mode is not that four call sites were wrong once -- it is that
    `_is_stub_body` reads like the obvious predicate to reach for, so it re-acquires
    callers, and each new one is wrong in a way that surfaces as a lane nobody reaps
    rather than as a red test.

    An AST sweep over the whole harness namespace, never a grep: the subject is a CLASS of
    call sites, and a truncated grep over a class is how a sweep of this shape missed
    three of them before
    ([`a-truncated-grep-is-not-a-searched-negative`](habits/master.md#a-truncated-grep-is-not-a-searched-negative))."""

    def test_only_journal_is_unauthored_calls_the_byte_exact_predicate(self):
        import ast
        root = pathlib.Path(session.__file__).resolve().parent / "sessionlib"
        parts = sorted(root.glob("*.py"))
        self.assertTrue(parts, "no harness parts found under " + str(root))
        callers = []
        for part in parts:
            tree = ast.parse(part.read_text(encoding="utf-8"), filename=str(part))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "_is_stub_body"):
                        callers.append(part.name + ":" + str(node.lineno)
                                       + " in " + fn.name + "()")
        expected = ["config.py:" + str(_is_stub_body_call_line())
                    + " in _journal_is_unauthored()"]
        self.assertEqual(
            sorted(callers), expected,
            "`_is_stub_body` is byte-exact and the harness files a note into every running "
            "journal, so any caller but `_journal_is_unauthored` answers 'not a stub' for a "
            "journal nobody wrote. Call `_journal_is_unauthored` instead. Callers found: "
            + repr(sorted(callers)))


if __name__ == "__main__":
    unittest.main()
