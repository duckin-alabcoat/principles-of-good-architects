"""ADR-0091 — the harness commits what the harness writes.

Two committers share one classifier (`_harness_owned`): the janitor's, for the repo it
runs in (D1 sidecar migration + D2 pathspec'd commit), and `main-sync`, for a lane
acting on the main checkout (D3). What is pinned here, in both directions:

  * harness-written records (journals, the compiled handoff, STATUS.md, ROADMAP.md)
    are committed by machinery, pathspec'd, never `add -A`;
  * AUTHORED files are never committed by machinery, under any mix;
  * neither committer touches a tree with a provably-live session or a non-empty
    index (the c8edc02 class: a bare commit shipping someone else's staged state);
  * dry runs write nothing (the session-136 lesson: the worst defect available here
    is the one that looks like a preview);
  * D4: the lane-start warning names only authored dirt — harness dirt is one
    "pending sweep" line, not a user-facing problem.
"""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _init_repo(path, gitignore=".claude/\n.session-state/\n"):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("base\n", encoding="utf-8")
    if gitignore is not None:
        (path / ".gitignore").write_text(gitignore, encoding="utf-8")
    (path / "sessions" / "journal").mkdir(parents=True)
    (path / "sessions" / "journal" / ".gitkeep").write_text("", encoding="utf-8")
    (path / "STATUS.md").write_text("status\n", encoding="utf-8")
    (path / "session-handoff.md").write_text("handoff\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    _git(path, "branch", "-M", "main")


def _porcelain(repo):
    out = _git(repo, "status", "--porcelain").stdout
    return sorted(l for l in out.splitlines() if l.strip())


def _live_marker(repo, fresh=True):
    state = repo / ".session-state"
    state.mkdir(exist_ok=True)
    beat = datetime.now(timezone.utc)
    if not fresh:
        beat = beat.replace(year=beat.year - 1)
    (state / "abc123.live").write_text(
        json.dumps({"last_beat": beat.isoformat()}), encoding="utf-8")


@unittest.skipUnless(GIT, "git not available")
class HarnessBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        _init_repo(self.main)
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "SESSION_STATE_DIR", "CFG")}
        session.CFG = {
            "tz": ZoneInfo("UTC"), "machine_map": {},
            "architect_name": "Test", "architect_id": "test-arch", "trunk": "main",
            "handoff": self.main / "session-handoff.md",
            "status": self.main / "STATUS.md",
        }

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _as_lane(self):
        session.ROOT = self.lane
        session.SESSION_STATE_DIR = self.lane / ".session-state"

    def _as_main(self):
        session.ROOT = self.main
        session.SESSION_STATE_DIR = self.main / ".session-state"


class ClassifierTest(HarnessBase):
    def test_harness_records_are_owned_and_code_is_not(self):
        self._as_main()
        for rel in ("session-handoff.md", "STATUS.md", "ROADMAP.md",
                    "sessions/journal/20260813T0000Z-x-abcd.md"):
            self.assertTrue(session._harness_owned(rel), rel)
        for rel in ("session.py", "adr/0091-x.md", "habits/master.md",
                    "sessions/journal/notes.txt", "work-items/WI-0001-x.md"):
            self.assertFalse(session._harness_owned(rel), rel)

    def test_unnameable_porcelain_lines_are_authored(self):
        """A rename or a quoted path cannot be named with certainty — the safe answer
        is 'authored', which the machinery never commits."""
        harness, authored = session._classify_dirt(
            ['R  a.md -> b.md', ' M "we ird.md"', " M STATUS.md"])
        self.assertEqual(harness, ["STATUS.md"])
        self.assertEqual(len(authored), 2)


class MainSweepTest(HarnessBase):
    """D3 — a lane commits, in main, exactly the harness-written records."""

    def _dirty_main_harness(self):
        (self.main / "session-handoff.md").write_text("handoff + addendum\n",
                                                      encoding="utf-8")
        j = self.main / "sessions" / "journal" / "20260811T1200Z-t-c1cf.md"
        j.write_text("journal body\n", encoding="utf-8")
        return j

    def test_sweeps_harness_records_and_leaves_authored_work(self):
        self._dirty_main_harness()
        (self.main / "someones-code.py").write_text("x\n", encoding="utf-8")
        self._as_lane()
        res = session._main_harness_sweep()
        self.assertEqual(res["skipped"], "")
        self.assertEqual(sorted(res["committed"]),
                         ["session-handoff.md",
                          "sessions/journal/20260811T1200Z-t-c1cf.md"])
        self.assertEqual(_porcelain(self.main), ["?? someones-code.py"])
        msg = _git(self.main, "log", "-1", "--format=%s").stdout
        self.assertIn("ADR-0091", msg)
        self.assertTrue(any("someones-code.py" in l for l in res["authored"]))

    def test_dry_run_writes_nothing(self):
        self._dirty_main_harness()
        self._as_lane()
        before = _porcelain(self.main)
        head = _git(self.main, "rev-parse", "HEAD").stdout
        res = session._main_harness_sweep(dry_run=True)
        self.assertTrue(res["committed"])
        self.assertEqual(_porcelain(self.main), before)
        self.assertEqual(_git(self.main, "rev-parse", "HEAD").stdout, head)

    def test_refuses_when_a_session_is_live_in_main(self):
        self._dirty_main_harness()
        _live_marker(self.main)
        self._as_lane()
        res = session._main_harness_sweep()
        self.assertIn("live", res["skipped"])
        self.assertEqual(res["committed"], [])

    def test_a_stale_beat_does_not_block(self):
        self._dirty_main_harness()
        _live_marker(self.main, fresh=False)
        self._as_lane()
        res = session._main_harness_sweep()
        self.assertEqual(res["skipped"], "")
        self.assertTrue(res["committed"])

    def test_refuses_when_mains_index_is_not_empty(self):
        """The c8edc02 class — staged state in main means mid-flight or stale; the
        sweep must not commit anything there, pathspecs or no."""
        self._dirty_main_harness()
        (self.main / "staged.txt").write_text("x\n", encoding="utf-8")
        _git(self.main, "add", "staged.txt")
        self._as_lane()
        res = session._main_harness_sweep()
        self.assertIn("index", res["skipped"])
        self.assertEqual(res["committed"], [])

    def test_refuses_off_the_trunk(self):
        self._dirty_main_harness()
        _git(self.main, "checkout", "-q", "-b", "other")
        self._as_lane()
        res = session._main_harness_sweep()
        self.assertIn("not main", res["skipped"])
        self.assertEqual(res["committed"], [])

    def test_noop_from_the_main_checkout_itself(self):
        self._as_main()
        res = session._main_harness_sweep()
        self.assertIn("lane", res["skipped"])

    def test_auto_wrapper_never_raises(self):
        self._as_lane()
        session._main_harness_sweep_auto()   # must not raise, whatever the state


class MainWarningScopeTest(HarnessBase):
    """D4 — the lane-start warning names only authored dirt."""

    def test_harness_only_dirt_is_one_pending_line(self):
        (self.main / "session-handoff.md").write_text("dirty\n", encoding="utf-8")
        self._as_lane()
        lines = session._main_checkout_lines()
        self.assertTrue(any("pending" in l and "ADR-0091" in l for l in lines), lines)
        self.assertFalse(any("cannot clean them" in l for l in lines), lines)

    def test_authored_dirt_keeps_the_warning_without_harness_noise(self):
        (self.main / "session-handoff.md").write_text("dirty\n", encoding="utf-8")
        (self.main / "someones-code.py").write_text("x\n", encoding="utf-8")
        self._as_lane()
        lines = session._main_checkout_lines()
        self.assertTrue(any("AUTHORED" in l for l in lines), lines)
        self.assertTrue(any("someones-code.py" in l for l in lines), lines)
        self.assertFalse(any("session-handoff.md" in l for l in lines), lines)


class JanitorCommitTest(HarnessBase):
    """D1 + D2 — the janitor's own repo: sidecar migration and the records commit."""

    def test_untracks_the_sidecar_and_ignores_it(self):
        repo = self.tmp / "member"
        _init_repo(repo, gitignore="")          # no ignore line — the pre-D1 fleet state
        state = repo / ".session-state"
        state.mkdir()
        (state / "old.live").write_text("{}", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "track sidecar (the WI-0113 state)")
        (state / "old.live").write_text('{"x": 1}', encoding="utf-8")  # dirties the repo
        session.ROOT = repo
        session.SESSION_STATE_DIR = state
        res = session._janitor_commit_harness_writes(dry_run=False)
        self.assertIn(".gitignore", res["committed"])
        self.assertIn(".session-state", res["committed"])
        self.assertEqual(
            _git(repo, "ls-files", "--", ".session-state").stdout.strip(), "")
        self.assertTrue((state / "old.live").exists())   # untracked, never deleted
        self.assertIn(".session-state/",
                      (repo / ".gitignore").read_text(encoding="utf-8"))
        self.assertEqual(_porcelain(repo), [])
        head = _git(repo, "show", "--stat", "--format=%s", "HEAD").stdout
        self.assertIn("chore(janitor)", head)

    def test_commits_journals_and_status_but_never_authored_files(self):
        self._as_main()
        j = self.main / "sessions" / "journal" / "20260803T1200Z-t-2b23.md"
        j.write_text("closed by janitor\n", encoding="utf-8")
        (self.main / "STATUS.md").write_text("recompiled\n", encoding="utf-8")
        (self.main / "authored.py").write_text("x\n", encoding="utf-8")
        res = session._janitor_commit_harness_writes(dry_run=False)
        self.assertEqual(sorted(res["committed"]),
                         ["STATUS.md", "sessions/journal/20260803T1200Z-t-2b23.md"])
        self.assertEqual(_porcelain(self.main), ["?? authored.py"])

    def test_refuses_when_a_session_is_live_here(self):
        self._as_main()
        (self.main / "STATUS.md").write_text("recompiled\n", encoding="utf-8")
        _live_marker(self.main)
        res = session._janitor_commit_harness_writes(dry_run=False)
        self.assertIn("live", res["note"])
        self.assertEqual(res["committed"], [])
        self.assertIn(" M STATUS.md", _porcelain(self.main))

    def test_refuses_on_a_non_empty_index(self):
        self._as_main()
        (self.main / "STATUS.md").write_text("recompiled\n", encoding="utf-8")
        (self.main / "staged.txt").write_text("x\n", encoding="utf-8")
        _git(self.main, "add", "staged.txt")
        res = session._janitor_commit_harness_writes(dry_run=False)
        self.assertIn("index", res["note"])
        self.assertEqual(res["committed"], [])

    def test_dry_run_reports_and_writes_nothing(self):
        repo = self.tmp / "member2"
        _init_repo(repo, gitignore="")
        state = repo / ".session-state"
        state.mkdir()
        (state / "old.live").write_text("{}", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "track sidecar")
        (repo / "STATUS.md").write_text("recompiled\n", encoding="utf-8")
        session.ROOT = repo
        session.SESSION_STATE_DIR = state
        head = _git(repo, "rev-parse", "HEAD").stdout
        before = _porcelain(repo)
        res = session._janitor_commit_harness_writes(dry_run=True)
        self.assertIn("STATUS.md", res["committed"])
        self.assertEqual(_git(repo, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(_porcelain(repo), before)
        self.assertEqual(_git(repo, "ls-files", "--", ".gitignore").stdout.strip(),
                         ".gitignore")   # the ignore line was not written either
        self.assertNotIn(".session-state",
                         (repo / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
