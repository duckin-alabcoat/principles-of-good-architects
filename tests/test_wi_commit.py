"""Tests for the work-item store's auto-commit and its catch-up verb (WI-0056).

The defect: `poga work` wrote the item file, reported success, and left committing it
to whoever noticed. From a lane there was ultimately no route at all — the
worktree-isolation guard denies the `git -C <main>` redirect that used to stand in for
mechanism — so the tool's success message and git's view of the world disagreed, and in
session ~118 that blocked a land outright.

These drive the real git-side-effecting helpers against throwaway repos rather than
mocking git, because the properties that matter are all properties of what git actually
did: that a concurrent session's staged work survives untouched, that a rename commits
both halves, and that a failure leaves the file for the catch-up verb instead of losing
it. Skipped where `git` is unavailable.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import io
import pathlib
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from coord_fixture import point_store_at  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)


def _log_subjects(repo):
    r = _git(repo, "log", "--format=%s")
    return r.stdout.splitlines()


def _tracked(repo):
    r = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    return set(r.stdout.split())


@unittest.skipUnless(GIT, "git not available")
class WiCommitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / session.WI_DIRNAME).mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        # Point the store at the temp repo, and drop the under-test suppression that
        # exists so the suite cannot commit the operator's real repository.
        #
        # ROOT is patched as well as `_wi_dir` since WI-0125: the auto-commit is now
        # shared by both namespaces, so it resolves the checkout from ROOT rather than
        # from one store's own dir helper. Patching ROOT is also the honest fixture — it
        # is the single fact a real run has, and it moves `_ops_dir` with it.
        self._root = session.ROOT
        session.ROOT = self.repo
        # WI-0295: ROOT and `_wi_dir` are not the whole anchor. `_shared_work_root()`
        # resolves through the git COMMON dir to the operator's real repo regardless of
        # both, and the auto-commit path reaches `_holder_journal_dirs` through it —
        # which globs and parses every journal in every live lane. Measured: 2,415 reads
        # of the live store from this module.
        point_store_at(self, self.repo)
        self._pd = mock.patch.object(session, "_wi_dir",
                                     lambda base=None: self.repo / session.WI_DIRNAME)
        self._pd.start()
        self._pt = mock.patch.object(session, "_under_test", return_value=False)
        self._pt.start()

    def tearDown(self):
        self._pt.stop()
        self._pd.stop()
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _item(self, wid="WI-0001", title="a first item"):
        return {"id": wid, "title": title, "status": "open", "section": "next",
                "blocked_by": [], "group": "", "source": "", "impact": "fix",
                "migration": "", "version": session.WI_VERSION_UNSET, "notes": ""}


class AutoCommitTest(WiCommitBase):
    def test_a_new_item_is_committed_by_the_write(self):
        """The whole point: write and save stop being separate operations."""
        session._wi_write_item(self._item())
        self.assertIn(f"{session.WI_DIRNAME}/WI-0001-a-first-item.md", _tracked(self.repo))
        self.assertIn("chore(work-items): add WI-0001 — a first item",
                      _log_subjects(self.repo))

    def test_an_update_commits_as_an_update(self):
        session._wi_write_item(self._item())
        it = self._item()
        it["status"] = "done"
        session._wi_write_item(it)
        self.assertEqual(_log_subjects(self.repo)[0],
                         "chore(work-items): update WI-0001 — a first item")

    def test_a_retitle_commits_both_halves_of_the_rename(self):
        """A changed title regenerates the slug and unlinks the old file. Committing only
        the new name would leave the stale file tracked — an id with two files, which is
        the corruption `wi-check` exists to catch."""
        session._wi_write_item(self._item())
        it = self._item(title="a renamed item")
        session._wi_write_item(it)
        tracked = _tracked(self.repo)
        self.assertIn(f"{session.WI_DIRNAME}/WI-0001-a-renamed-item.md", tracked)
        self.assertNotIn(f"{session.WI_DIRNAME}/WI-0001-a-first-item.md", tracked)

    def test_a_concurrent_sessions_staged_work_is_not_swept_in(self):
        """The property that forced a pathspec-limited commit. Another session in this
        checkout may have staged its own work; `git commit -- <paths>` must take our file
        and leave theirs staged and uncommitted. A bare commit would attribute their work
        to us and commit it without their knowledge."""
        (self.repo / "theirs.txt").write_text("in flight\n", encoding="utf-8")
        _git(self.repo, "add", "theirs.txt")
        session._wi_write_item(self._item())
        self.assertNotIn("theirs.txt", _tracked(self.repo))
        still_staged = _git(self.repo, "diff", "--cached", "--name-only").stdout.split()
        self.assertIn("theirs.txt", still_staged)

    def test_an_unchanged_rewrite_makes_no_empty_commit(self):
        session._wi_write_item(self._item())
        before = len(_log_subjects(self.repo))
        session._wi_write_item(self._item())
        self.assertEqual(len(_log_subjects(self.repo)), before)

    def test_the_write_survives_a_git_failure(self):
        """Fail-open. The file is already on disk when the commit runs; a git failure
        must report that the save did not happen, never turn a good write into an error
        or take the file back down with it."""
        with mock.patch.object(session, "sh", side_effect=OSError("git exploded")):
            session._wi_write_item(self._item())      # must not raise
        self.assertTrue((self.repo / session.WI_DIRNAME /
                         "WI-0001-a-first-item.md").is_file())

    def test_no_commit_when_the_directory_is_not_a_git_repo(self):
        """A store outside git is a supported state (a member that has not init'd yet);
        it must degrade to 'written, not committed', not to an error."""
        plain = self.tmp / "plain" / session.WI_DIRNAME
        plain.mkdir(parents=True)
        with mock.patch.object(session, "_wi_dir", lambda base=None: plain):
            session._wi_write_item(self._item())
        self.assertTrue((plain / "WI-0001-a-first-item.md").is_file())

    def test_suppressed_under_the_test_suite(self):
        """The WI-0068 lesson applied here: `wi-new`/`wi-status` run in dozens of tests,
        and committing the operator's real repo from a test is that same class."""
        self._pt.stop()
        try:
            session._wi_write_item(self._item())
            self.assertEqual(_log_subjects(self.repo), ["init"])
        finally:
            self._pt.start()


class AutoCommitReceiptTest(WiCommitBase):
    """Session 127: the auto-commit used to succeed in SILENCE and speak only on failure.

    That asymmetry is the whole bug. The commit lands in the MAIN checkout, so a lane
    that goes looking for it in its own `git status` finds nothing, concludes the edit
    was stranded, and hands the operator a git command to run by hand — for work that is
    already committed, in a checkout that is already clean. Observed on a member and
    hit first-hand in the federation the same day. A receipt that exists but is invisible
    is indistinguishable from one that was never written, and the reader's pessimistic
    guess is the one that manufactures a chore for a human."""

    def _write_capturing(self, item=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session._wi_write_item(item or self._item())
        return buf.getvalue()

    def test_a_successful_commit_announces_itself(self):
        out = self._write_capturing()
        self.assertIn("committed", out)
        head = _git(self.repo, "rev-parse", "--short", "HEAD").stdout.strip()
        self.assertIn(head, out)                      # the actual sha, not a claim
        self.assertIn(str(self.repo), out)            # ...and WHERE, since it is elsewhere

    def test_the_receipt_says_a_lane_will_not_see_it_and_nothing_is_owed(self):
        """The two inferences that produced the hand-off message, both pre-empted."""
        out = self._write_capturing()
        self.assertIn("main checkout", out)
        self.assertIn("nothing for the user to run", out)

    def test_a_failed_commit_prints_no_success_receipt(self):
        """Fail-open still holds — but a failure must never read as a save."""
        buf_out, buf_err = io.StringIO(), io.StringIO()
        real_sh = session.sh

        def flaky(argv, check=True):
            if "commit" in argv:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="nope")
            return real_sh(argv, check=check)

        with mock.patch.object(session, "sh", flaky), \
             contextlib.redirect_stdout(buf_out), \
             contextlib.redirect_stderr(buf_err):
            session._wi_write_item(self._item())
        self.assertNotIn("committed", buf_out.getvalue())
        self.assertIn("NOT committed", buf_err.getvalue())
        # the write itself still stands — that is the fail-open contract
        self.assertTrue((self.repo / session.WI_DIRNAME /
                         "WI-0001-a-first-item.md").is_file())

    def test_an_empty_rewrite_stays_silent(self):
        """No commit happened, so there is nothing to announce. A receipt here would be
        the same lie in the other direction."""
        self._write_capturing()
        out = self._write_capturing()                 # identical rewrite -> no commit
        self.assertNotIn("committed", out)


class WiCommitVerbTest(WiCommitBase):
    def _run(self):
        return session.cmd_wi_commit(argparse.Namespace())

    def test_sweeps_files_left_uncommitted(self):
        """The residue case: files written before auto-commit shipped, or whose own
        commit failed. Without this the only exit was a hand-run cross-checkout git
        command, which is the route the isolation guard closed."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-left-behind.md").write_text("# stray\n", encoding="utf-8")
        self._run()
        self.assertIn(f"{session.WI_DIRNAME}/WI-0009-left-behind.md", _tracked(self.repo))

    def test_names_each_file_rather_than_the_directory(self):
        """Found by reading this verb's own output. Plain `--porcelain` collapses an
        untracked directory to one `work-items/` entry, so the sweep staged the whole
        directory and reported "1 file" while committing several — the report was false
        and the pathspec was wide enough to catch a concurrent lane's new item."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-one.md").write_text("# a\n", encoding="utf-8")
        (d / "WI-0010-two.md").write_text("# b\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._run()
        text = out.getvalue()
        self.assertIn("WI-0009-one.md", text)
        self.assertIn("WI-0010-two.md", text)
        self.assertNotIn(f"  committed  {session.WI_DIRNAME}/\n", text)
        self.assertIn("2 file(s) committed", text)

    def test_clean_store_is_a_no_op(self):
        session._wi_write_item(self._item())
        before = len(_log_subjects(self.repo))
        self._run()
        self.assertEqual(len(_log_subjects(self.repo)), before)

    def test_does_not_touch_files_outside_the_store(self):
        (self.repo / "elsewhere.txt").write_text("not mine\n", encoding="utf-8")
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-left-behind.md").write_text("# stray\n", encoding="utf-8")
        self._run()
        self.assertNotIn("elsewhere.txt", _tracked(self.repo))


class OpsAutoCommitTest(WiCommitBase):
    """WI-0125 — the ops namespace had the write and not the save.

    Both namespaces render in the same chart, the same startup view and the same feed, so
    `ops-items/` reads as a peer of `work-items/` everywhere a user looks. It diverged at
    the one point nobody can see from the outside: a wi write committed itself and an ops
    write did not. OPS-0005 was left uncommitted in the main checkout by that gap, where
    the next session would have met it as authored dirt with no verb that could save it.
    """

    def _ops(self, oid="OPS-0001", title="check the backups"):
        return {"id": oid, "title": title, "status": "open", "cadence": "",
                "last_completed": "", "last_result": "", "due": "2026-01-01",
                "group": "", "source": "", "notes": ""}

    def test_a_new_obligation_is_committed_by_the_write(self):
        session._ops_write_item(self._ops())
        self.assertIn(f"{session.OPS_DIRNAME}/OPS-0001-check-the-backups.md",
                      _tracked(self.repo))
        self.assertIn("chore(ops-items): add OPS-0001 — check the backups",
                      _log_subjects(self.repo))

    def test_an_update_commits_as_an_update(self):
        session._ops_write_item(self._ops())
        it = self._ops()
        it["status"] = "done"
        session._ops_write_item(it)
        self.assertEqual(_log_subjects(self.repo)[0],
                         "chore(ops-items): update OPS-0001 — check the backups")

    def test_a_retitle_commits_both_halves_of_the_rename(self):
        session._ops_write_item(self._ops())
        session._ops_write_item(self._ops(title="check the restore log"))
        tracked = _tracked(self.repo)
        self.assertIn(f"{session.OPS_DIRNAME}/OPS-0001-check-the-restore-log.md", tracked)
        self.assertNotIn(f"{session.OPS_DIRNAME}/OPS-0001-check-the-backups.md", tracked)

    def test_the_write_survives_a_git_failure(self):
        """Fail-open, same contract as the wi side: the file is on disk before the commit
        runs, so a git failure reports that the save did not happen and never takes the
        write down with it."""
        with mock.patch.object(session, "sh", side_effect=OSError("git exploded")):
            session._ops_write_item(self._ops())          # must not raise
        self.assertTrue((self.repo / session.OPS_DIRNAME /
                         "OPS-0001-check-the-backups.md").is_file())

    def test_the_failure_note_names_the_ops_retry_verb(self):
        """A retry hint naming the wrong verb is worse than none — `poga work commit` is
        the wi side's verb and the message is shared code."""
        err = io.StringIO()
        with mock.patch.object(session, "sh", side_effect=OSError("git exploded")), \
                contextlib.redirect_stderr(err):
            session._ops_write_item(self._ops())
        self.assertIn("poga ops commit", err.getvalue())
        self.assertIn(session.OPS_DIRNAME, err.getvalue())

    def test_a_concurrent_sessions_staged_work_is_not_swept_in(self):
        (self.repo / "theirs.txt").write_text("in flight\n", encoding="utf-8")
        _git(self.repo, "add", "theirs.txt")
        session._ops_write_item(self._ops())
        self.assertNotIn("theirs.txt", _tracked(self.repo))


class SweepCoversBothNamespacesTest(WiCommitBase):
    """WI-0125's other half. `wi-commit` read `work-items/` alone, so against a dirty
    `ops-items/` it answered *"work-items/ is clean — nothing to commit"*: true, useless,
    and indistinguishable from "your store is saved"
    ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes))."""

    def _run(self):
        return session.cmd_wi_commit(argparse.Namespace())

    def setUp(self):
        super().setUp()
        (self.repo / session.OPS_DIRNAME).mkdir(exist_ok=True)

    def test_an_uncommitted_obligation_is_swept(self):
        d = self.repo / session.OPS_DIRNAME
        (d / "OPS-0005-left-behind.md").write_text("# stray\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._run()
        self.assertIn(f"{session.OPS_DIRNAME}/OPS-0005-left-behind.md",
                      _tracked(self.repo))
        self.assertIn("OPS-0005-left-behind.md", out.getvalue())

    def test_the_subject_names_only_the_namespace_actually_swept(self):
        """An ops-only sweep committed under `chore(work-items)` is a small lie in the
        audit trail, and the audit trail is the point (P2).

        WI-0081 changed the VERB half of this subject and not the scope half: a stray
        file this session never wrote is adopted residue, and the subject now says so."""
        (self.repo / session.OPS_DIRNAME / "OPS-0005-x.md").write_text(
            "# stray\n", encoding="utf-8")
        self._run()
        self.assertEqual(_log_subjects(self.repo)[0],
                         "chore(ops-items): adopt 1 unattributed store file left by "
                         "an earlier session")

    def test_both_namespaces_sweep_together_and_both_are_named(self):
        (self.repo / session.WI_DIRNAME / "WI-0009-a.md").write_text(
            "# a\n", encoding="utf-8")
        (self.repo / session.OPS_DIRNAME / "OPS-0005-b.md").write_text(
            "# b\n", encoding="utf-8")
        self._run()
        tracked = _tracked(self.repo)
        self.assertIn(f"{session.WI_DIRNAME}/WI-0009-a.md", tracked)
        self.assertIn(f"{session.OPS_DIRNAME}/OPS-0005-b.md", tracked)
        self.assertEqual(
            _log_subjects(self.repo)[0],
            f"chore({session.WI_DIRNAME},{session.OPS_DIRNAME}): "
            "adopt 2 unattributed store files left by an earlier session")

    def test_a_clean_pair_says_so_naming_both(self):
        """The old sentence named one directory while silently ignoring the other; the
        replacement has to say what it actually looked at."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._run()
        text = out.getvalue()
        self.assertIn(session.WI_DIRNAME, text)
        self.assertIn(session.OPS_DIRNAME, text)
        self.assertIn("nothing to commit", text)


class AttributedSweepTest(WiCommitBase):
    """WI-0081. The verb committed what was DIRTY, not what this session WROTE.

    Observed session ~118, running it from lane worktree-poga-2: it committed WI-0033 and
    WI-0068 — two files that session had never opened, left in the main checkout by other
    sessions — under a subject claiming them as its own work. WI-0056 had specified the
    opposite in as many words: *"stages ONLY the item files it changed against the main
    checkout ... refuses on anything it did not write."*

    The machinery to do this already existed (ADR-0057's attribution set, used by the
    session close since ADR-0058); the verb simply never asked it. The other half of the
    gap was that store writes went through neither the `PreToolUse` recorder nor anything
    else, so the store was invisible to attribution even for the session that wrote it.

    Three buckets, and the distinctions are the point: MINE commits, a LIVE SIBLING's is
    refused, and unattributed residue is adopted in a SEPARATE commit that says so. That
    last split is half the fix — the original harm was not only which files were taken but
    that they landed in a commit asserting authorship of them."""

    def _run(self):
        return session.cmd_wi_commit(argparse.Namespace())

    def _patched(self, csid="csid-me", mine=(), siblings=(), theirs=()):
        touched = {csid: list(mine)}
        for s in siblings:
            touched[s] = list(theirs)
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(session, "_claude_session_id",
                                              return_value=csid))
        stack.enter_context(mock.patch.object(session, "_touched_paths",
                                              side_effect=lambda c: touched.get(c, [])))
        stack.enter_context(mock.patch.object(session, "_live_sibling_csids",
                                              return_value=list(siblings)))
        return stack

    def test_this_sessions_own_file_commits_as_its_own_work(self):
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-mine.md").write_text("# mine\n", encoding="utf-8")
        rel = f"{session.WI_DIRNAME}/WI-0009-mine.md"
        with self._patched(mine=[rel]):
            self._run()
        self.assertIn(rel, _tracked(self.repo))
        self.assertEqual(_log_subjects(self.repo)[0],
                         "chore(work-items): commit 1 uncommitted store file")

    def test_a_live_siblings_file_is_refused_and_left_on_disk(self):
        """The dangerous case. A lane may be mid-write; taking the file both attributes
        their in-flight work to us and pulls it out from under them."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0033-theirs.md").write_text("# theirs\n", encoding="utf-8")
        rel = f"{session.WI_DIRNAME}/WI-0033-theirs.md"
        out = io.StringIO()
        with self._patched(siblings=["csid-them"], theirs=[rel]):
            with contextlib.redirect_stdout(out):
                self._run()
        self.assertNotIn(rel, _tracked(self.repo))
        self.assertIn("LEFT", out.getvalue())
        self.assertIn("live session is holding it", out.getvalue())

    def test_the_exact_session_118_shape_takes_mine_and_leaves_theirs(self):
        """WI-0033 and WI-0068 belong to someone else; this session wrote WI-0081."""
        d = self.repo / session.WI_DIRNAME
        for name in ("WI-0033-a.md", "WI-0068-b.md", "WI-0081-c.md"):
            (d / name).write_text(f"# {name}\n", encoding="utf-8")
        mine = [f"{session.WI_DIRNAME}/WI-0081-c.md"]
        theirs = [f"{session.WI_DIRNAME}/WI-0033-a.md",
                  f"{session.WI_DIRNAME}/WI-0068-b.md"]
        with self._patched(mine=mine, siblings=["csid-them"], theirs=theirs):
            self._run()
        tracked = _tracked(self.repo)
        self.assertIn(mine[0], tracked)
        for t in theirs:
            self.assertNotIn(t, tracked)

    def test_unattributed_residue_is_adopted_not_claimed(self):
        """Refusing it would strand the store permanently, which is the no-route-out
        condition this verb exists to end. So it commits — under its own honest
        subject, because `git log` in six months is only the subject."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-orphan.md").write_text("# orphan\n", encoding="utf-8")
        with self._patched():
            self._run()
        self.assertIn(f"{session.WI_DIRNAME}/WI-0009-orphan.md", _tracked(self.repo))
        self.assertEqual(_log_subjects(self.repo)[0],
                         "chore(work-items): adopt 1 unattributed store file left by "
                         "an earlier session")

    def test_mine_and_residue_land_in_separate_commits(self):
        """Blending them is the original defect in miniature: one commit cannot honestly
        describe both work this session did and work it merely found."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-mine.md").write_text("# mine\n", encoding="utf-8")
        (d / "WI-0010-found.md").write_text("# found\n", encoding="utf-8")
        with self._patched(mine=[f"{session.WI_DIRNAME}/WI-0009-mine.md"]):
            self._run()
        subjects = _log_subjects(self.repo)[:2]
        self.assertIn("adopt 1 unattributed store file", subjects[0])
        self.assertIn("commit 1 uncommitted store file", subjects[1])

    def test_all_files_held_commits_nothing_and_says_nothing_is_owed(self):
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0033-theirs.md").write_text("# theirs\n", encoding="utf-8")
        before = len(_log_subjects(self.repo))
        out = io.StringIO()
        with self._patched(siblings=["csid-them"],
                           theirs=[f"{session.WI_DIRNAME}/WI-0033-theirs.md"]):
            with contextlib.redirect_stdout(out):
                self._run()
        self.assertEqual(len(_log_subjects(self.repo)), before)
        self.assertIn("Nothing is owed", out.getvalue())

    def test_the_tail_reports_what_was_left_behind(self):
        """A sweep that silently declines a file is indistinguishable from one that did
        not see it — the count of what was left is part of the receipt."""
        d = self.repo / session.WI_DIRNAME
        (d / "WI-0009-mine.md").write_text("# mine\n", encoding="utf-8")
        (d / "WI-0033-theirs.md").write_text("# theirs\n", encoding="utf-8")
        out = io.StringIO()
        with self._patched(mine=[f"{session.WI_DIRNAME}/WI-0009-mine.md"],
                           siblings=["csid-them"],
                           theirs=[f"{session.WI_DIRNAME}/WI-0033-theirs.md"]):
            with contextlib.redirect_stdout(out):
                self._run()
        self.assertIn("1 left with their live owner", out.getvalue())


class StoreWriteAttributionTest(WiCommitBase):
    """The other half of WI-0081: a store write must ENTER the attribution set.

    `_record_touch` is a `PreToolUse` hook and sees only Write/Edit/MultiEdit/
    NotebookEdit. A work-item write is `session.py` writing a file directly, so before
    this the store was invisible to attribution — and `wi-commit` could not have sorted
    by author even if it had asked, because every file looked unattributed."""

    def test_a_store_write_records_itself_against_the_current_session(self):
        recorded = {}

        def _capture(sid, suffix, payload):
            recorded[suffix] = payload

        with mock.patch.object(session, "_claude_session_id", return_value="csid-me"), \
             mock.patch.object(session, "_touched_paths", return_value=[]), \
             mock.patch.object(session, "_sidecar_write", _capture):
            session._wi_write_item(self._item())
        self.assertIn("touched", recorded)
        self.assertIn(f"{session.WI_DIRNAME}/WI-0001-a-first-item.md",
                      recorded["touched"]["paths"])

    def test_recording_appends_rather_than_replacing(self):
        """A session writes many files; the second must not erase the first."""
        recorded = {}
        with mock.patch.object(session, "_claude_session_id", return_value="csid-me"), \
             mock.patch.object(session, "_touched_paths", return_value=["already/there.md"]), \
             mock.patch.object(session, "_sidecar_write",
                               lambda s, suf, p: recorded.update({suf: p})):
            session._record_store_write(["work-items/WI-0002-x.md"])
        self.assertEqual(recorded["touched"]["paths"],
                         ["already/there.md", "work-items/WI-0002-x.md"])

    def test_an_already_recorded_path_is_not_written_twice(self):
        calls = []
        with mock.patch.object(session, "_claude_session_id", return_value="csid-me"), \
             mock.patch.object(session, "_touched_paths", return_value=["work-items/x.md"]), \
             mock.patch.object(session, "_sidecar_write",
                               lambda *a, **k: calls.append(a)):
            session._record_store_write(["work-items/x.md"])
        self.assertEqual(calls, [])

    def test_no_session_identity_records_nothing_and_does_not_raise(self):
        """Attribution is bookkeeping on a write that already happened. It must never be
        the thing that turns a successful store write into an error."""
        calls = []
        with mock.patch.object(session, "_claude_session_id", return_value=""), \
             mock.patch.object(session, "_sidecar_write",
                               lambda *a, **k: calls.append(a)):
            session._record_store_write(["work-items/x.md"])
        self.assertEqual(calls, [])

    def test_a_failing_sidecar_write_is_swallowed(self):
        def _boom(*a, **k):
            raise OSError("disk full")

        with mock.patch.object(session, "_claude_session_id", return_value="csid-me"), \
             mock.patch.object(session, "_touched_paths", return_value=[]), \
             mock.patch.object(session, "_sidecar_write", _boom):
            session._record_store_write(["work-items/x.md"])   # must not raise


if __name__ == "__main__":
    unittest.main()
