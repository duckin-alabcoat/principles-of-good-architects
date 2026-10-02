"""WI-0307 half two — re-scoping an item cost it its number, and then its file.

THE MEASUREMENT. Session ~241 re-scoped four items under the operator's ruling. Re-scoping
changes an item's TITLE, which regenerates its FILENAME, so at the land the lane's
newly-named file read as ADDED while the number was still present in the trunk — and the
gate classified that as a trunk collision. The renumberer dutifully moved four items onto
freshly drawn numbers (WI-0018->0302, 0047->0303, 0210->0304, 0269->0305) and, because it
deletes the id it moves, left three of those numbers with no file at all. `wi-check`
refused the trunk afterwards; the ids were restored by hand as tombstones.

WHY `--no-renames` STAYS. Rename detection is ON by default and the collision case is
exactly the shape that trips it: two work items with the same field skeleton pair as one
rename, `--diff-filter=A` then matches nothing, and a real collision gets a clean bill.
That hole was closed deliberately and is not reopened here. The fix is a SECOND question
asked of the numbers the first one flags.

THE DISCRIMINATOR, and why it needs two halves. The obvious test — "the landing tree
holds only one file for this number, so it is a rename" — is true after the rebase and
FALSE before it, because a lane that branched before the trunk landed its own item also
holds exactly one file. The gate is reached from both states (`_land_candidate` and
`_land_branch` call it around the rebase, `_auto_renumber_collisions` after it), so a
rule that is only true in one of them is a rule that is wrong in the other. What is true
in all four states is the conjunction:

    a retitle is a number that ALREADY EXISTED at the merge-base
    AND has exactly ONE file in the landing tree.

  - retitle, pre-rebase:   merge-base has it, tip has 1  -> keep the number
  - retitle, post-rebase:  merge-base has it, tip has 1  -> keep the number
  - collision, pre-rebase: merge-base has NONE, tip has 1 -> renumber
  - collision, post-rebase: merge-base has it, tip has 2  -> renumber

The first half says "this lane did not invent this number"; the second says "nothing else
is competing for it". Either alone is wrong in one of the four.

  A. A LANE THAT RETITLES KEEPS ITS NUMBER. The red test — the WI-0018 incident in
     miniature.
  B. NEGATIVE CONTROL — a genuine collision (two different items, one number) still
     renumbers. Green before this change and it must stay green; a "fix" that stopped
     renumbering real collisions would reopen the hole `--no-renames` was added to close.
  C. THE POST-REBASE COLLISION, where the landing tree carries BOTH files, still
     renumbers. This is the state the real land is actually in, and the one the
     single-half rule would have got wrong.
  D. FAIL-CLOSED. When the merge-base cannot be read, the number is treated as a
     collision exactly as it is today. "I could not tell" must not borrow the answer
     "it is only a retitle" ([`declare-what-a-check-assumes`]).
  E. THE GATE AND THE REMAP AGREE. Both read `_counter_new_and_trunk`, so a retitle the
     remap skips is not then refused by the gate — the two cannot drift into disagreeing
     about the same land (P16).

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _rev(cwd, ref="HEAD"):
    return subprocess.run(["git", "-C", str(cwd), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def _item_text(wid, title):
    return (f"# {wid}: {title}\n\n"
            f"- status: open\n- section: next\n- blocked-by: \n"
            f"- group: \n- source: \n- impact: fix\n- version: \n\n"
            f"the body of {wid}.\n")


@unittest.skipUnless(GIT, "git required")
class RetitleBase(unittest.TestCase):
    """A real checkout and a real linked lane. Git is not mocked — every property here
    is a property of what git actually reports about renames and merge-bases, and a fake
    would assert my model of git rather than git."""

    def setUp(self):
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        (self.main / "work-items").mkdir(parents=True)
        (self.main / "sessions" / "journal").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")

        # BASE: WI-0001 and WI-0002 both already exist. WI-0002 is the item the lane
        # will RE-SCOPE — it predates the lane, which is the whole point.
        (self.main / "work-items" / "WI-0001-first.md").write_text(
            _item_text("WI-0001", "first"), encoding="utf-8")
        (self.main / "work-items" / "WI-0002-the-original-title.md").write_text(
            _item_text("WI-0002", "the original title"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "base")

        self.lane = self.main / ".claude" / "worktrees" / "poga-9"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-9",
             str(self.lane), "HEAD")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.lane
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "retitletest"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers -------------------------------------------------------
    def _lane_commit(self, msg="lane work"):
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", msg)
        return _rev(self.lane)

    def _retitle_in_lane(self):
        """The lane re-scopes WI-0002: new title, and so a new filename."""
        old = self.lane / "work-items" / "WI-0002-the-original-title.md"
        new = self.lane / "work-items" / "WI-0002-a-completely-new-scope.md"
        old.unlink()
        new.write_text(_item_text("WI-0002", "a completely new scope"),
                       encoding="utf-8")
        return self._lane_commit("lane re-scopes WI-0002")

    def _collisions(self, tip, parent):
        c = session._counter("wi")
        return session._counter_trunk_collisions(c, tip, parent)


class ARetitleKeepsItsNumberTest(RetitleBase):
    """Property A — the WI-0018 incident, in miniature. RED before the fix."""

    def test_a_retitled_item_is_not_a_trunk_collision(self):
        tip = self._retitle_in_lane()
        parent = _rev(self.main, "refs/heads/main")
        self.assertEqual(
            self._collisions(tip, parent), [],
            "a re-scoped item keeps its number — the file was renamed, not duplicated")

    def test_the_fixture_really_does_set_the_trap(self):
        # Control on the FIXTURE, asserted against RAW GIT rather than against the
        # function under test — otherwise this "control" would just be re-asserting the
        # fix, and would go green for a fixture that never renamed anything at all.
        tip = self._retitle_in_lane()
        parent = _rev(self.main, "refs/heads/main")
        added = subprocess.run(
            ["git", "-C", str(self.lane), "diff", "--name-only", "--no-renames",
             "--diff-filter=A", parent, tip, "--", "work-items/"],
            capture_output=True, text=True, check=True).stdout.split()
        self.assertIn("work-items/WI-0002-a-completely-new-scope.md", added,
                      "the renamed file must read as ADDED — that is the trap")
        on_trunk = subprocess.run(
            ["git", "-C", str(self.lane), "ls-tree", "--name-only", parent,
             "work-items/"],
            capture_output=True, text=True, check=True).stdout.split()
        self.assertIn("work-items/WI-0002-the-original-title.md", on_trunk,
                      "and the trunk must still hold WI-0002 under its old name")


class AGenuineCollisionStillRenumbersTest(RetitleBase):
    """Property B — the negative control. Two DIFFERENT items on one number."""

    def _genuine_collision(self):
        """The trunk lands WI-0003 after the lane branched; the lane files its own."""
        (self.main / "work-items" / "WI-0003-trunk-side.md").write_text(
            _item_text("WI-0003", "the trunk's own third item"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands WI-0003")
        parent = _rev(self.main, "refs/heads/main")
        (self.lane / "work-items" / "WI-0003-lane-side.md").write_text(
            _item_text("WI-0003", "the lane's own third item"), encoding="utf-8")
        return self._lane_commit("lane files WI-0003"), parent

    def test_two_items_on_one_number_still_collide(self):
        tip, parent = self._genuine_collision()
        nums = [n for n, _ in self._collisions(tip, parent)]
        self.assertIn(3, nums,
                      "a number this lane INVENTED, already used on the trunk, is a "
                      "real collision and must still be remapped")

    def test_a_retitle_and_a_collision_in_one_land_are_told_apart(self):
        # Both in the same lane: WI-0002 retitled, WI-0003 genuinely collided. The
        # rule is per-number, and mixing them is how a coarse rule gets caught.
        self._retitle_in_lane()
        tip, parent = self._genuine_collision()
        nums = [n for n, _ in self._collisions(tip, parent)]
        self.assertIn(3, nums, "the invented number still collides")
        self.assertNotIn(2, nums, "the retitled number is not a collision")


class ThePostRebaseCollisionStillRenumbersTest(RetitleBase):
    """Property C — the state the real land is actually in when the remap runs."""

    def test_two_files_for_one_number_in_the_landing_tree_collide(self):
        # Rebase the lane onto a trunk that landed its own WI-0003, so the landing tree
        # carries BOTH files for 0003 — which is what makes it a collision and not a
        # rename, and what the one-file half of the rule alone would have missed.
        (self.main / "work-items" / "WI-0003-trunk-side.md").write_text(
            _item_text("WI-0003", "the trunk's own third item"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands WI-0003")
        (self.lane / "work-items" / "WI-0003-lane-side.md").write_text(
            _item_text("WI-0003", "the lane's own third item"), encoding="utf-8")
        self._lane_commit("lane files WI-0003")
        _git(self.lane, "rebase", "-q", "main")
        tip = _rev(self.lane)
        parent = _rev(self.main, "refs/heads/main")

        names = sorted(p.name for p in (self.lane / "work-items").glob("WI-0003-*.md"))
        self.assertEqual(len(names), 2,
                         f"the fixture must really carry both files: {names}")
        nums = [n for n, _ in self._collisions(tip, parent)]
        self.assertIn(3, nums, "a post-rebase collision must still renumber")

    def test_a_retitle_survives_the_rebase_too(self):
        self._retitle_in_lane()
        _git(self.lane, "rebase", "-q", "main")
        tip = _rev(self.lane)
        parent = _rev(self.main, "refs/heads/main")
        self.assertEqual(self._collisions(tip, parent), [],
                         "a retitle keeps its number after the rebase as well")


class FailClosedWhenTheBaseCannotBeReadTest(RetitleBase):
    """Property D — 'I could not tell' keeps today's answer, which is 'collision'."""

    def test_an_unreadable_merge_base_leaves_the_collision_standing(self):
        tip = self._retitle_in_lane()
        parent = _rev(self.main, "refs/heads/main")
        # With the merge-base unavailable the retitle cannot be PROVEN, so it is
        # treated as a collision — the safe direction, since a wrong renumber leaves a
        # tombstone (part three) while a wrong skip lands a duplicate number.
        with mock.patch.object(session, "_counter_merge_base", return_value=None):
            nums = [n for n, _ in self._collisions(tip, parent)]
        self.assertIn(2, nums,
                      "an unprovable retitle must fall back to today's behaviour")


class TheGateAndTheRemapAgreeTest(RetitleBase):
    """Property E — one scan feeds both, so they cannot drift (P16)."""

    def test_the_land_gate_does_not_refuse_a_retitle(self):
        tip = self._retitle_in_lane()
        parent = _rev(self.main, "refs/heads/main")
        ok, msg = session._all_counter_land_gates(tip, parent)
        self.assertTrue(ok, f"the gate must not refuse a re-scoped item:\n{msg}")

    def test_the_land_gate_still_refuses_a_real_duplicate(self):
        (self.main / "work-items" / "WI-0003-trunk-side.md").write_text(
            _item_text("WI-0003", "the trunk's own third item"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands WI-0003")
        parent = _rev(self.main, "refs/heads/main")
        (self.lane / "work-items" / "WI-0003-lane-side.md").write_text(
            _item_text("WI-0003", "the lane's own third item"), encoding="utf-8")
        tip = self._lane_commit("lane files WI-0003")
        ok, msg = session._all_counter_land_gates(tip, parent)
        self.assertFalse(ok, "a genuine duplicate must still be refused")
        self.assertIn("WI-0003-lane-side.md", msg)


if __name__ == "__main__":
    unittest.main()
