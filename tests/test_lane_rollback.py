"""A failed runtime launch rolls back what it created (WI-0105).

Found live during the WI-0033 Phase 3 drill, session ~129: `poga -r cx` created lane
poga-5, its worktree and `.session-state/prep.json`, wrote the 81k session-context, then
`exec codex` failed with *"Error: stdin is not a terminal"* and exit 1. Everything prep
built stayed on disk with no agent behind it, and `poga` was already gone — after `exec`
succeeds there is no `poga` left to clean up. Each failed launch therefore added a lane
that only operator could remove, since the obvious cleanup (`git branch -D`) is denied by
the substrate's own guard.

These pin the PREDICATE, because the verb is destructive and the predicate is the entire
safety argument — same discipline as `discard-phantom-lanes`. Every test that matters
here is a REFUSAL: a lane with real commits, a live session, a written journal, or an
unaccounted file must survive, or the fix for litter becomes a way to lose work.

The `execfail` shell mechanics are verified separately and by measurement — see the note
in `poga`'s `exec_or_rollback`. Two forms that read correctly were written and observed
to kill the shell with the rollback never firing.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))          # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)


@unittest.skipIf(GIT is None, "git unavailable")
class LaneRollbackBase(unittest.TestCase):
    def setUp(self):
        neutralize_ambient_env(self)             # WI-0275 — first, before anything reads it
        neutralize_coord_journal(self)           # WI-0126, and WI-0312 made it load-bearing
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / ".gitignore").write_text(
            ".claude/\n.session-state/\n", encoding="utf-8")
        (self.main / "f.txt").write_text("base\n", encoding="utf-8")
        (self.main / "sessions" / "journal").mkdir(parents=True)
        (self.main / "sessions" / "journal" / ".keep").write_text("", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
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

    def make_lane(self, n=5):
        """A lane exactly as a launch that never started leaves it: worktree, branch at
        the base, prep marker, session-context payload, no journal, no commits."""
        path = self.main / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-poga-{n}",
             str(path), "main")
        state = path / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "prep.json").write_text(
            '{"session_id": "20260808T1210Z-runner-f958", "runtime": "codex"}',
            encoding="utf-8")
        (path / "session-context.md").write_text("x" * 2048, encoding="utf-8")
        return path, f"worktree-poga-{n}"

    def rollback(self, lane="poga-5", **kw):
        ns = argparse.Namespace(**{"lane": lane, "reason": "test",
                                   "dry_run": False, **kw})
        buf = io.StringIO()
        with mock.patch.object(session, "_lanes_root",
                               return_value=self.main / ".claude" / "worktrees"), \
             mock.patch.object(session, "_coord_release", return_value=True):
            with redirect_stdout(buf):
                session.cmd_lane_rollback(ns)
        return buf.getvalue()


class PredicateRefusalTest(LaneRollbackBase):
    """Every one of these must REFUSE. The verb removes a worktree and force-deletes a
    branch; a predicate that says yes when it should say no loses work."""

    def test_a_live_session_is_refused(self):
        path, branch = self.make_lane()
        with mock.patch.object(session, "_tree_has_live_session", return_value=True):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertFalse(safe)
        self.assertIn("LIVE", why)

    def test_real_unlanded_commits_are_refused(self):
        path, branch = self.make_lane()
        (path / "f.txt").write_text("real work\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", "real work")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertFalse(safe)
        self.assertIn("not on main", why)

    def test_a_journal_with_real_narrative_is_refused(self):
        """An agent ran and wrote, so this is not a failed launch — whatever else it is."""
        path, branch = self.make_lane()
        j = path / "sessions" / "journal" / "20260808T1210Z-runner-f958.md"
        j.write_text("---\nsession-id: x\n---\n\n### What happened\n\n- Real work.\n",
                     encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertFalse(safe)
        self.assertIn("real narrative", why)

    def test_an_unaccounted_uncommitted_file_is_refused(self):
        """Everything prep writes is enumerable. Anything else came from somewhere this
        verb cannot account for, so it must not assume it is disposable."""
        path, branch = self.make_lane()
        (path / "somebody-elses-work.md").write_text("mine\n", encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertFalse(safe)
        self.assertIn("cannot account for", why)

    def test_a_missing_lane_directory_is_refused(self):
        safe, why = session._launch_left_nothing(self.main / "nope", "worktree-poga-9")
        self.assertFalse(safe)
        self.assertIn("no such lane", why)

    def test_an_unreadable_lane_is_never_a_candidate(self):
        path, branch = self.make_lane()
        with mock.patch.object(session, "_tree_has_live_session",
                               side_effect=OSError("boom")):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertFalse(safe)
        self.assertIn("could not establish safety", why)


class PredicateAcceptTest(LaneRollbackBase):
    """The one shape it must accept — or the verb does nothing and the litter stays."""

    def test_the_exact_failed_launch_shape_is_safe(self):
        path, branch = self.make_lane()
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertTrue(safe, why)

    def test_preps_own_payload_does_not_block_the_rollback(self):
        """`session-context.md` and `.session-state/` are prep's own output. If they
        counted as unaccounted files, the predicate would refuse every real case."""
        path, branch = self.make_lane()
        self.assertTrue((path / "session-context.md").is_file())
        self.assertTrue((path / ".session-state" / "prep.json").is_file())
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, _ = session._launch_left_nothing(path, branch)
        self.assertTrue(safe)

    def test_a_stub_journal_does_not_block_the_rollback(self):
        path, branch = self.make_lane()
        j = path / "sessions" / "journal" / "20260808T1210Z-runner-f958.md"
        j.write_text(f"---\nsession-id: x\n---\n\n{session._STUB_BODY}",
                     encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            safe, why = session._launch_left_nothing(path, branch)
        self.assertTrue(safe, why)


class CommandTest(LaneRollbackBase):
    def test_a_rollback_removes_the_worktree_and_the_branch(self):
        path, branch = self.make_lane()
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            out = self.rollback()
        self.assertFalse(path.exists())
        branches = subprocess.run([GIT, "-C", str(self.main), "branch", "--list", branch],
                                  capture_output=True, text=True).stdout
        self.assertEqual(branches.strip(), "")
        self.assertIn("Nothing was lost", out)

    def test_a_refusal_removes_nothing_and_exits_nonzero(self):
        path, _branch = self.make_lane()
        (path / "f.txt").write_text("real work\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", "real work")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            with self.assertRaises(SystemExit):
                self.rollback()
        self.assertTrue(path.exists())

    def test_a_refusal_names_the_landing_route_rather_than_a_denied_command(self):
        """The reader of a refusal is the next one at risk of being handed a chore."""
        path, _b = self.make_lane()
        (path / "f.txt").write_text("real\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-qm", "real")
        buf = io.StringIO()
        with mock.patch.object(session, "_tree_has_live_session", return_value=False), \
             mock.patch.object(session, "_lanes_root",
                               return_value=self.main / ".claude" / "worktrees"):
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    session.cmd_lane_rollback(
                        argparse.Namespace(lane="poga-5", reason="t", dry_run=False))
        text = buf.getvalue()
        self.assertIn("session.py merge", text)
        self.assertNotIn("git branch -D", text)

    def test_dry_run_removes_nothing(self):
        path, _branch = self.make_lane()
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            out = self.rollback(dry_run=True)
        self.assertTrue(path.exists())
        self.assertIn("dry-run", out)

    def test_the_lane_reservation_is_released(self):
        """`alloc_lane` DRAWS the slot from the coord store, so removing only the
        worktree would leave the number reserved — a lane nobody can use and nothing
        will free until its TTL.

        Asserts on the STORE rather than on a mocked call (WI-0312). This used to patch
        `_coord_release` and check its arguments, which passed against a fixture that had
        never drawn a reservation at all: the old rollback released unconditionally, so
        the test could not tell a freed record from a call into an empty store. Rollback
        now goes through the one lane exit, which looks the record up first — and the
        receipt for a release is the record being gone
        ([`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence))."""
        self.make_lane()
        d = session._coord_dir(session.LANE_ALLOC_KIND, create=True)
        rec = d / "poga-5.json"
        rec.write_text(json.dumps(session._coord_record(
            "app", 3600, name="poga-5", kind=session.LANE_ALLOC_KIND)) + "\n",
            encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False), \
             mock.patch.object(session, "_lanes_root",
                               return_value=self.main / ".claude" / "worktrees"):
            with redirect_stdout(io.StringIO()):
                session.cmd_lane_rollback(
                    argparse.Namespace(lane="poga-5", reason="t", dry_run=False))
        self.assertFalse(rec.exists(),
                         "the rolled-back lane's reservation outlived it — nothing else "
                         "frees it before its TTL")


if __name__ == "__main__":
    unittest.main()
