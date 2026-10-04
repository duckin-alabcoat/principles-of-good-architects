"""ADR-0051 C5 — single-writer leases (phase 3).

A lease excludes two lanes from running the same single-writer resource op (push-substrate
writes the shared member repos — worktree isolation does NOT cover that). These pin:

  1. the `lease()` context manager acquires, holds, and releases;
  2. a live OTHER lane is refused (LeaseHeld) and the block never runs;
  3. release-on-exception (the finally path);
  4. the CLI contract (lease exit 0/1, unlease holder-respect).
"""

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
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
class LeaseBase(unittest.TestCase):
    def setUp(self):
        # Fixture records carry no journal (WI-0123 / ADR-0093). Without it both fake
        # lanes inherit the journal of whatever real session runs the suite, `_coord_mine`
        # matches on it, and laneB RECLAIMS laneA's lease instead of being refused —
        # all three exclusion assertions inverted, green or red on whether the developer
        # happened to have an open journal. The guard's one definition is in
        # `coord_fixture`; the six modules that were missing it are WI-0126.
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
        _git(self.main, "branch", "-M", "main")
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(self.laneB), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ContextManagerTest(LeaseBase):
    def test_acquires_and_releases(self):
        session.ROOT = self.laneA
        with session.lease("push-substrate"):
            self.assertIn("push-substrate", session._coord_list("leases"))
        self.assertEqual(session._coord_list("leases"), {})   # released on exit

    def test_live_other_lane_is_refused(self):
        session.ROOT = self.laneA
        session._lease_try("push-substrate")                  # laneA holds it
        session.ROOT = self.laneB
        ran = []
        with self.assertRaises(session.LeaseHeld) as cm:
            with session.lease("push-substrate"):
                ran.append(1)                                 # must never run
        self.assertEqual(ran, [])
        self.assertEqual(cm.exception.holder, "worktree-poga-1")

    def test_releases_on_exception(self):
        session.ROOT = self.laneA
        with self.assertRaises(ValueError):
            with session.lease("push-substrate"):
                raise ValueError("boom")
        self.assertEqual(session._coord_list("leases"), {})   # finally released it


class CliTest(LeaseBase):
    def _run(self, func, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                func(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_lease_then_conflict_exits_1(self):
        session.ROOT = self.laneA
        code, _ = self._run(session.cmd_lease, name="push-substrate", ttl=0)
        self.assertEqual(code, 0)
        session.ROOT = self.laneB
        code, out = self._run(session.cmd_lease, name="push-substrate", ttl=0)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)

    def test_unlease_respects_holder(self):
        session.ROOT = self.laneA
        self._run(session.cmd_lease, name="push-substrate", ttl=0)
        session.ROOT = self.laneB
        code, _ = self._run(session.cmd_unlease, name="push-substrate", force=False)
        self.assertEqual(code, 1)                             # laneB isn't the holder
        code, _ = self._run(session.cmd_unlease, name="push-substrate", force=True)
        self.assertEqual(code, 0)                             # force overrides


if __name__ == "__main__":
    unittest.main()
