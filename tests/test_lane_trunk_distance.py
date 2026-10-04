"""ADR-0108 D3 / WI-0236 — a lane names its distance from the trunk, or says it could not tell.

The defect this pins is a *silence*, which is why the negative control matters more than
the positive case. `git_sync` derives every behind/ahead number in the start banner from
`@{u}` (`_git_diagnose`). A lane branch (`worktree-<name>`) has no upstream, so that
function returns `no upstream tracking — pull/push skipped` before reaching any distance
computation — and EVERY lane takes that path, ALWAYS. So the banner was not reporting a
number the reader ignored; there was no number. A test that only asserted "the line
appears when behind" would pass just as happily against a hardcoded string, so all four
outcomes are pinned here, including the two silences and the difference between them.

Properties:

  1. BEHIND IS COUNTED AND SOURCED. The line carries the count, the lane's own commit, and
     the trunk commit it was measured against — the ADR-0108 D1 floor: an answer about
     shared state names the tree and the commit it read.
  2. CURRENT IS SILENT. The common case adds no banner line. This is the negative control:
     without it, property 1 is satisfied by a constant.
  3. UNMEASURABLE IS NOT CURRENT. When the trunk tip cannot be resolved, the result is a
     distinct NOT CHECKED line — never the silence of property 2
     (`declare-what-a-check-assumes`).
  4. NOT A LANE IS NOT AN ANSWER. Off a lane the helper returns None and contributes
     nothing, so the main checkout's own `git status` stays the authority there.

The distance is measured against `_trunk_tip()` rather than the local trunk ref, so a lane
is told how far it sits from the newest trunk the machine knows about — the same reference
a land builds its candidate on.
"""

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True)


def _commit(cwd, name, body):
    (pathlib.Path(cwd) / name).write_text(body, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", f"add {name}")


class LaneTrunkDistanceBase(unittest.TestCase):
    """A real repo with a real linked worktree — the configuration the code runs in.

    Mocking `_on_worktree_lane` would let the whole thing pass against a repo shape that
    cannot occur ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.main = pathlib.Path(self.tmp.name) / "main"
        self.main.mkdir()
        _git(self.main, "init", "-b", "main", ".")
        _git(self.main, "config", "user.email", "test@example.invalid")
        _git(self.main, "config", "user.name", "Test")
        _git(self.main, "config", "commit.gpgsign", "false")
        _commit(self.main, "seed.txt", "seed\n")

        self.lane = pathlib.Path(self.tmp.name) / "lane"
        _git(self.main, "worktree", "add", "-b", "worktree-test", str(self.lane))

        self._root = session.ROOT
        session.ROOT = self.lane
        self._cwd = os.getcwd()
        os.chdir(self.lane)
        self.addCleanup(self._restore)

    def _restore(self):
        os.chdir(self._cwd)
        session.ROOT = self._root
        # Release the worktree before the temp dir goes away, so git does not leave a
        # registration pointing at a path that no longer exists.
        subprocess.run(["git", "-C", str(self.main), "worktree", "remove", "--force",
                        str(self.lane)], capture_output=True, text=True)
        self.tmp.cleanup()

    def _advance_main(self, n):
        for i in range(n):
            _commit(self.main, f"trunk-{i}.txt", f"trunk {i}\n")


class LaneTrunkDistance(LaneTrunkDistanceBase):

    def test_behind_counts_and_names_both_commits(self):
        """Property 1 — the count, the lane's commit, and the trunk commit measured against."""
        self._advance_main(3)
        d = session._lane_trunk_distance()
        self.assertIsNotNone(d)
        self.assertTrue(d["readable"])
        self.assertEqual(d["behind"], 3)

        head = _git(self.lane, "rev-parse", "HEAD").stdout.strip()
        tip = _git(self.main, "rev-parse", "main").stdout.strip()
        self.assertEqual(d["head"], head)
        self.assertEqual(d["tip"], tip)

        lines = session._lane_trunk_distance_lines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn("3 commit(s) behind", line)
        self.assertIn(head[:7], line, "the lane's own commit must appear")
        self.assertIn(tip[:7], line, "the commit measured against must appear")
        self.assertIn("ADR-0108", line)

    def test_current_lane_is_silent(self):
        """Property 2, the negative control — without this, property 1 passes on a constant."""
        d = session._lane_trunk_distance()
        self.assertTrue(d["readable"])
        self.assertEqual(d["behind"], 0)
        self.assertEqual(session._lane_trunk_distance_lines(), [])

    def test_unmeasurable_is_distinct_from_current(self):
        """Property 3 — 'could not tell' must never render as 'you are current'."""
        with mock.patch.object(session, "_trunk_tip", return_value=None):
            d = session._lane_trunk_distance()
            self.assertFalse(d["readable"])
            lines = session._lane_trunk_distance_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("NOT CHECKED", lines[0])
        self.assertNotEqual(lines, [], "unmeasurable must not collapse into the silent case")

    def test_off_a_lane_contributes_nothing(self):
        """Property 4 — the main checkout's own status stays the authority there."""
        os.chdir(self.main)
        session.ROOT = self.main
        self.assertIsNone(session._lane_trunk_distance())
        self.assertEqual(session._lane_trunk_distance_lines(), [])

    def test_the_line_never_pulls(self):
        """The WI-0188 precedent, restated as a test: this reports, it does not repair.

        ADR-0108 D4 rejects auto-integrating a behind lane. A line that quietly ff-pulled
        would satisfy every assertion above while changing the operator's tree under them,
        so the argv is inspected directly — the same shape WI-0188's fix is pinned by.
        """
        self._advance_main(2)
        seen = []
        real = session.sh

        def recording(args, **kw):
            seen.append(list(args))
            return real(args, **kw)

        with mock.patch.object(session, "sh", side_effect=recording):
            session._lane_trunk_distance_lines()

        self.assertTrue(seen, "expected the helper to shell out at all")
        for argv in seen:
            self.assertNotIn("pull", argv)
            self.assertNotIn("merge", argv)
            self.assertNotIn("fetch", argv)
            self.assertNotIn("checkout", argv)

        after = _git(self.lane, "rev-parse", "HEAD").stdout.strip()
        seeded = _git(self.lane, "rev-parse", "worktree-test").stdout.strip()
        self.assertEqual(after, seeded, "the lane's HEAD must not have moved")


if __name__ == "__main__":
    unittest.main()
