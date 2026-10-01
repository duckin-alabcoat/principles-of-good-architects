"""The main checkout falls behind its own trunk, and the two repair verbs deadlock on it.

THE DEFECT, stated as the sequence that produces it.

A lane lands: `_land_worktree_lane` CAS-advances `refs/heads/main` with no checkout, and
`_sync_main_checkout` brings the main checkout's tree along. When that sync is declined or
interrupted, main's tree stays at the pre-land commit while its HEAD resolves through the
advanced ref. Every path the land touched now reports as dirt — and it is not dirt, it is a
STALE COPY OF A COMMITTED VERSION.

The harness then classifies that dirt and hands it to two verbs that each defer to the
other:

  * `main-restore` repairs only AUTHORED paths and leaves the harness-written ones
    (`session-handoff.md`, `STATUS.md`, `ROADMAP.md`, `sessions/journal/*.md`) alone by
    name — "they are `session.py main-sync`'s job (ADR-0091 D3)".
  * `main-sync` refuses before it looks, because `_index_is_empty` is false: a stale
    materialization IS a non-empty index. Its own words — "main's index is not empty —
    mid-flight or stale state; not committing anything there".

So nothing materializes the harness half, and the deadlock is not merely a stall: if
`main-sync` ever DID commit those paths it would commit the pre-land content of files the
trunk has already moved past — a revert of the landed work, dressed as routine
housekeeping. That is WI-0040 happening again, and it is why "commit it" can never be the
answer for a stale copy. The only correct answer is to MATERIALIZE.

Worse, a partial repair makes the next land wrong too. `main-restore` fixes the authored
paths to HEAD and leaves the harness paths at the old commit, so the tree now matches NO
commit at all. `_checkout_sync_base`'s exact-index search then falls through to its
`parent` fallback, and every subsequent land's `read-tree -m -u` reads the un-materialized
gap as content the checkout owns and carefully protects it. Permanent, and compounding —
the same shape WI-0133 fixed for the index, reappearing one layer out.

WHAT THESE TESTS PIN.

The fix is to stop SEARCHING for where the checkout sits and start RECORDING it. With the
materialization commit written down, "does this tree hold anything of its own?" is a
question with a definite answer instead of a walk that fails open. When the answer is no,
the tree is brought to the tip automatically — at the land and at the next session start,
with no prompt — and when the answer is yes, that authored delta is left strictly alone and
reported as one row.

The classes are ordered by how much they prove. The last one builds the configuration the
change actually creates — a real lane, a real land, a real checkout left behind — because a
suite written from the code's own premise can only confirm it
(`verify-in-the-created-configuration`).
"""

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from test_worktree_lane import GIT, WorktreeLaneBase, _git, _out  # noqa: E402


class MaterializationBase(WorktreeLaneBase):
    def setUp(self):
        super().setUp()
        self._install_main_compiler()
        self._point_session_at_lane()

    def _main_status(self):
        return _out(self.main, "status", "--porcelain")

    def _land(self, path="trunkfile.txt", body="new\n"):
        """A real land from the lane: the trunk ref advances and main is materialized."""
        (self.lane / path).write_text(body, encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"lane changes {path}")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))

    def _land_without_syncing_main(self, path="landed.txt", body="landed\n"):
        """The state a DECLINED or interrupted sync leaves: the ref moved, the tree did
        not. `_sync_main_checkout` is stubbed out rather than the CAS hand-rolled, so the
        fixture exercises the real land path and diverges only where the defect does."""
        (self.lane / path).write_text(body, encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"lane changes {path}")
        with mock.patch.object(session, "_sync_main_checkout", return_value=""):
            self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))


class TheMaterializationPointIsRecordedTest(MaterializationBase):
    """The record is the whole fix: where main was last materialized becomes a FACT the
    substrate wrote down, not an answer re-derived by a bounded walk that fails open."""

    def test_a_land_records_the_commit_main_was_materialized_at(self):
        self._land()
        tip = _out(self.main, "rev-parse", "HEAD")
        self.assertEqual(session._main_materialized_at(self.main), tip,
                         "the sync that materialized main must say which commit it left "
                         "the tree at")

    def test_a_clean_checkout_with_no_record_seeds_itself_at_head(self):
        """Every checkout on the fleet predates this record. A clean tree IS materialized
        at HEAD by definition, so the record can be seeded for free rather than requiring
        one more land before the fix engages anywhere."""
        (self.main / ".session-state" / session.MAIN_MATERIALIZED_FILE).unlink(
            missing_ok=True)
        self.assertEqual(self._main_status(), "", "fixture precondition: main is clean")

        session._materialize_main_checkout(trunk="main", tip=_out(self.main, "rev-parse",
                                                                 "HEAD"))

        self.assertEqual(session._main_materialized_at(self.main),
                         _out(self.main, "rev-parse", "HEAD"))

    def test_an_absent_record_is_not_read_as_a_match(self):
        """Three outcomes, never two. "I have no record" must not render as "the tree
        equals the record" — that collapse would license a reset over real work."""
        (self.main / ".session-state" / session.MAIN_MATERIALIZED_FILE).unlink(
            missing_ok=True)
        (self.main / "operators-wip.txt").write_text("do not lose me\n", encoding="utf-8")

        ok, why = session._main_holds_nothing_of_its_own(self.main)

        self.assertFalse(ok, why)
        self.assertTrue((self.main / "operators-wip.txt").exists())


class TheDeadlockIsBrokenTest(MaterializationBase):
    """The two verbs that each defer to the other, and the third thing that ends it."""

    def test_main_sync_still_refuses_a_stale_index_and_that_is_correct(self):
        """Pinned deliberately: `main-sync` committing a stale handoff would REVERT the
        landed version. Its refusal is right; it is just not a repair, and nothing else
        was doing the repair."""
        self._land_without_syncing_main()
        res = session._main_harness_sweep(dry_run=True)
        self.assertEqual(res["committed"], [], "a stale copy is never committed")
        self.assertIn("index is not empty", res["skipped"])

    def test_the_stale_harness_records_are_materialized_not_left_to_main_sync(self):
        """The deadlock's sharp end. `session-handoff.md` and `STATUS.md` are harness-owned,
        so `main-restore` hands them to `main-sync`, which refuses. After the fix the
        materialization has already brought them forward and neither verb has anything
        left to argue about."""
        (self.lane / "session-handoff.md").write_text("v2 handoff\n", encoding="utf-8")
        (self.lane / "STATUS.md").write_text(
            "---\nid: test\nversion: v2\nlast_active: 2026-01-02\nfocus: y\n"
            "blocked: false\n---\n", encoding="utf-8")
        self._land_without_syncing_main(path="landed.txt")

        line = session._materialize_main_checkout(
            trunk="main", tip=_out(self.main, "rev-parse", "HEAD"))

        self.assertTrue(line, "a materialization that happened must say so")
        self.assertEqual((self.main / "session-handoff.md").read_text(encoding="utf-8"),
                         "v2 handoff\n")
        self.assertEqual(self._main_status(), "",
                         f"main is materialized, not merely argued about: {line}")

    def test_the_tree_matches_a_commit_again_so_the_next_land_is_not_poisoned(self):
        """A partial repair leaves a tree matching no commit, and `_checkout_sync_base`
        then falls back to `parent` forever. After a full materialization the index
        matches HEAD exactly, so the base search answers `parent` because it is TRUE."""
        self._land_without_syncing_main()
        session._materialize_main_checkout(trunk="main",
                                           tip=_out(self.main, "rev-parse", "HEAD"))
        head = _out(self.main, "rev-parse", "HEAD")
        self.assertEqual(
            subprocess.run([GIT, "-C", str(self.main), "diff", "--cached", "--quiet",
                            head], capture_output=True).returncode, 0,
            "the index sits at HEAD, provably")


class AnAuthoredDeltaIsLeftAloneTest(MaterializationBase):
    """The safety envelope. The record makes the AUTOMATIC case safe; it must not make the
    dangerous case quiet."""

    def test_a_real_authored_delta_is_never_materialized_over(self):
        self._land()
        (self.main / "trunkfile.txt").write_text("operator was typing here\n", encoding="utf-8")
        self._land_without_syncing_main(path="landed.txt")

        line = session._materialize_main_checkout(
            trunk="main", tip=_out(self.main, "rev-parse", "HEAD"))

        self.assertEqual((self.main / "trunkfile.txt").read_text(encoding="utf-8"),
                         "operator was typing here\n", "real work is never overwritten")
        self.assertFalse((self.main / "landed.txt").exists(),
                         "and nothing is half-materialized around it")
        self.assertIn("authored", line.lower())

    def test_an_authored_delta_is_reported_as_one_row(self):
        """One row, not a block. The old report printed a header, up to eight dirt lines, a
        consequences line and a fix line — for a condition whose only useful content is
        'main holds something of its own'. A signal that costs eleven lines is a signal
        the reader learns to scroll past."""
        self._land()
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.main / name).write_text("mine\n", encoding="utf-8")

        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            rows = [l for l in session._main_checkout_lines() if l.startswith("main:")]

        self.assertEqual(len(rows), 1, f"one row, got {len(rows)}:\n" + "\n".join(rows))

    def test_a_live_session_in_main_is_never_materialized_over(self):
        """A tree someone is working in is not ours to rewrite, however provable the
        record is — the record says where the tree WAS, not that nobody is typing in it."""
        self._land_without_syncing_main()
        with mock.patch.object(session, "_tree_has_live_session", return_value=True):
            line = session._materialize_main_checkout(
                trunk="main", tip=_out(self.main, "rev-parse", "HEAD"))
        self.assertFalse((self.main / "landed.txt").exists(), line)


class MainRestoreProvesPerPathTest(MaterializationBase):
    """`main-restore`'s proof, corrected.

    It asked whether the working copy matched some commit in the trunk's last
    `CHECKOUT_BASE_SEARCH` commits — a window over the TREE's history, walked whether or not
    those commits ever touched the path. A file whose matching version is older than the
    window reads as "it holds something of its own" and is KEPT: the answer a human's edit
    gets, given to a stale copy, on a checkout that has been behind long enough to need the
    verb most.

    The honest question is per PATH: does this file's working copy match a committed version
    ON THIS PATH? That walk is over the versions the file has actually had, so it is
    complete over the file's own history and cheaper — a file that never changes has one
    version to check, not two hundred.
    """

    def test_a_stale_copy_older_than_the_window_is_still_proven(self):
        """The order is what makes this bite, and getting it wrong writes a test that
        passes against the defect. The stale version must be SUPERSEDED first and the
        churn piled on after: a path that simply goes untouched for a long time still
        matches at `behind == 0`, so a naive fixture proves nothing."""
        self._land(path="old.txt", body="v1 — the version main still holds\n")
        stale = (self.main / "old.txt").read_text(encoding="utf-8")
        self._land(path="old.txt", body="v2 — the version the trunk moved on to\n")
        # Now bury v1 past the window with commits that never touch the path.
        for i in range(session.CHECKOUT_BASE_SEARCH + 5):
            (self.lane / "churn.txt").write_text(f"{i}\n", encoding="utf-8")
            _git(self.lane, "add", "-A")
            _git(self.lane, "commit", "-qm", f"churn {i}")
        self._land(path="churn.txt", body="settled\n")
        (self.main / "old.txt").write_text(stale, encoding="utf-8")
        window = _out(self.main, "rev-list", "--first-parent",
                      f"-{session.CHECKOUT_BASE_SEARCH}", "HEAD").split()
        self.assertTrue(
            all(subprocess.run([GIT, "-C", str(self.main), "diff", "--quiet", rev, "--",
                                "old.txt"], capture_output=True).returncode != 0
                for rev in window),
            "fixture precondition: no commit inside the window holds the stale version")

        ok, why = session._main_restore_verdict(self.main, "old.txt", untracked=False)

        self.assertTrue(ok, why)
        self.assertNotIn("holds something of its own", why)

    def test_a_human_edit_is_still_kept(self):
        """The predicate must not get weaker in the process. Matching no committed version
        of this path is what tells a human's edit from a stale copy."""
        self._land(path="edited.txt", body="committed\n")
        (self.main / "edited.txt").write_text("a paragraph nobody committed\n",
                                              encoding="utf-8")

        ok, why = session._main_restore_verdict(self.main, "edited.txt", untracked=False)

        self.assertFalse(ok, why)
        self.assertIn("its own", why)

    def test_untracked_is_still_never_deletable(self):
        ok, why = session._main_restore_verdict(self.main, "nope.txt", untracked=True)
        self.assertFalse(ok)
        self.assertIn("untracked", why)


class ALaneLandsAndTheNextStartMaterializesItTest(MaterializationBase):
    """THE ITEM, end to end, in the configuration the change creates.

    A lane lands. Main is left behind — the ref moved, the tree did not. The next session
    start brings it forward with no prompt, no verb typed by anyone, and one line in the
    journal saying it happened.
    """

    def test_a_lane_lands_main_is_behind_and_the_next_start_materializes_it(self):
        self._land_without_syncing_main(path="landed.txt")
        tip = _out(self.main, "rev-parse", "HEAD")
        self.assertFalse((self.main / "landed.txt").exists(),
                         "fixture precondition: main is behind its own trunk")
        self.assertNotEqual(self._main_status(), "",
                            "fixture precondition: the phantom is present")

        session._materialize_main_auto()

        self.assertTrue((self.main / "landed.txt").exists(),
                        "the next start materializes the landed work")
        self.assertEqual(self._main_status(), "", "and leaves the checkout clean")
        self.assertEqual(session._main_materialized_at(self.main), tip)

    def test_it_says_so_in_one_journal_line(self):
        session.write_start_journal("20260906T0000Z-devbox-test", {
            "session-id": "20260906T0000Z-devbox-test", "ordinal": 1,
            "title": "(in progress)", "machine": "DevBox",
            "runtime": session.CLAUDE_RUNTIME, "role-doc-version": "v1.0.0",
            "base-commit": "0" * 12, "started": "2026-09-06T08:00:00+00:00", "ended": "",
            "claude-session-id": "csid-mat",
        })
        journal = session.JOURNAL_DIR / "20260906T0000Z-devbox-test.md"
        before = journal.read_text(encoding="utf-8").splitlines()
        self._land_without_syncing_main(path="landed.txt")

        with mock.patch.dict(session.os.environ, {"CLAUDE_CODE_SESSION_ID": "csid-mat"}):
            session._materialize_main_auto()

        after = journal.read_text(encoding="utf-8").splitlines()
        added = [l for l in after if l not in before and l.strip()]
        self.assertEqual(len(added), 1, f"one line, got {len(added)}:\n" + "\n".join(added))
        self.assertIn("main", added[0].lower())

    def test_a_start_with_main_already_current_writes_nothing_at_all(self):
        """Silence is the common case and must stay free. A start that materializes nothing
        must not touch the journal, the record, or the tree."""
        self._land()
        journal_before = sorted(p.read_text(encoding="utf-8")
                                for p in session.JOURNAL_DIR.glob("*.md"))

        line = session._materialize_main_auto()

        self.assertFalse(line, f"nothing to say: {line!r}")
        self.assertEqual(sorted(p.read_text(encoding="utf-8")
                                for p in session.JOURNAL_DIR.glob("*.md")),
                         journal_before)

    def test_it_never_raises_and_never_fails_the_start_it_rides_on(self):
        """Fail-open, like every other lazy-start step. A materialization hiccup must not
        be able to break the session it is helping."""
        with mock.patch.object(session, "_main_checkout",
                               side_effect=RuntimeError("git is having a day")):
            self.assertEqual(session._materialize_main_auto(), "")


if __name__ == "__main__":
    unittest.main()


class TheCheckoutYouAreStandingInIsMaterialisedTooTest(MaterializationBase):
    """`integrate` run INSIDE the main checkout left its own tree stale (WI-0359).

    The trunk is advanced by a CAS `git update-ref`, which is what makes a land cheap and
    lockless — and `update-ref` on the branch you are standing on moves HEAD while leaving
    the index and working tree exactly where they were. `_sync_main_checkout` used to
    return early whenever the checkout being synced was the tree the process was running
    in, on the assumption that such a caller had already materialised it. It had not.

    THE FAILURE: integrate run in the main checkout merges origin, pushes, and reports
    success with the ref level with origin — while tracked files stay stale on disk. The
    federation's scheduled jobs run from that tree, so the runner goes on executing
    pre-merge code after a successful integrate, and a fix that has already landed does
    not take effect.
    """

    def test_the_tree_is_updated_when_the_sync_target_is_our_own_checkout(self):
        head_before = _out(self.main, "rev-parse", "HEAD").strip()
        (self.main / "runner-ish.txt").write_text("old\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "main: the code the jobs run")
        parent = _out(self.main, "rev-parse", "HEAD").strip()

        # A commit reachable from the trunk that the tree has not been moved onto — the
        # state a CAS advance leaves behind.
        (self.lane / "runner-ish.txt").write_text("new\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "lane: the fix that must reach the jobs")
        tip = _out(self.lane, "rev-parse", "HEAD").strip()

        # RESOLVED ON BOTH SIDES, or the test is a decoy. The code asks
        # `main == ROOT.resolve()`; handing it an unresolved fixture path on one side makes
        # that comparison False, the own-tree branch never runs, and the test passes
        # whether or not the fix is present — which is exactly what the first version of
        # this test did, caught by mutating the fix and seeing nothing go red.
        resolved = self.main.resolve()
        with mock.patch.object(session, "ROOT", resolved), \
             mock.patch.object(session, "_main_checkout", return_value=resolved):
            self.assertEqual(resolved, session.ROOT.resolve(),
                             "precondition: this test must exercise the own-tree branch")
            report = session._sync_main_checkout(session._trunk(), parent, tip)

        self.assertNotEqual(
            (self.main / "runner-ish.txt").read_text(encoding="utf-8"), "old\n",
            "the ref advanced and the tree it is standing in was left stale — this is the "
            f"Runner defect verbatim. report: {report!r}")
        self.assertEqual((self.main / "runner-ish.txt").read_text(encoding="utf-8"), "new\n")
        self.assertNotEqual(head_before, tip)

    def test_someone_elses_live_tree_is_still_refused(self):
        """The guard that is NOT relaxed. Skipping the live-session check is licensed only
        because the live session found in our OWN tree is this process; another checkout
        with a live session must still be left alone, or the fix trades a stale runner for
        moving a working session's tree under it."""
        (self.lane / "x.txt").write_text("v\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "lane work")
        tip = _out(self.lane, "rev-parse", "HEAD").strip()
        parent = _out(self.main, "rev-parse", "HEAD").strip()

        with mock.patch.object(session, "_main_checkout", return_value=self.main), \
             mock.patch.object(session, "_tree_has_live_session", return_value=True):
            report = session._sync_main_checkout(session._trunk(), parent, tip)

        self.assertIn("NOT synced", report)
        self.assertIn("live", report)
