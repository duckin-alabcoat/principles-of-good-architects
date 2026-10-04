"""WI-0246 — `curate/reconcile.py` reads the roster from the tree it is RUNNING IN.

The defect, measured session ~187 and reproduced below: `roster_ids()` resolved
`portfolio.md` through `shared_work_root()`, i.e. from the MAIN checkout, while the
file is authored content any lane may edit. A lane that had applied a roster row —
and verified it with the brief's own probe — read its own new member back as
`off-roster(not in portfolio.md)`, because the roster it was measured against was a
different file in a different tree.

That inversion is worse than a plain false negative: the tool reports a real problem
that has ALREADY been fixed, so the obedient response is to go fix it a second time.

The routing rule this pins is NOT "tracked wins, gitignored loses" — it is **who writes
the file**:

  1. THE ROSTER FOLLOWS THE RUNNING TREE. `portfolio.md` is authored, and any lane may
     be its writer, so the lane holding the edit is the truth.
  2. MACHINE-LOCAL CONFIG STILL FOLLOWS THE MAIN CHECKOUT. `reconcile-roots.local` /
     `repo-paths.local` are excluded from version control and exist in exactly one
     tree; a lane has no copy to read. Splitting the roots must not regress this —
     that anchoring is the session-99 false "no roots" fix.
  3. TRUNK-ONLY GENERATED VIEWS STILL FOLLOW THE MAIN CHECKOUT. `STATUS.md` is stamped
     on the trunk by the land (ADR-0056); a lane never writes it, so a lane's copy is a
     frozen duplicate rather than a newer truth.
  4. THE VERDICT NAMES THE TREE IT READ (ADR-0108 D1) — and now names the running one.

stdlib unittest: python3 -m unittest tests.test_reconcile_roster_root
"""

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GIT = shutil.which("git")

HEADER = (
    "| System | Orchestrator agent | Canonical Architect | System ID | Architect ID | Status |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(sid):
    return f"| {sid} | (none) | {sid.title()} Architect | `{sid}` | `{sid}-arch` | Active |\n"


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _load_reconcile(tree):
    """Import the copy of `reconcile.py` that lives in `tree` — the point of the test.

    `FED_ROOT` is derived from `__file__`, so importing the LANE's copy is what makes
    "the tree this script is running in" a real, distinguishable value rather than an
    assertion about a variable we set ourselves.
    """
    curate = tree / "curate"
    sys.path.insert(0, str(curate))
    try:
        spec = importlib.util.spec_from_file_location(
            f"reconcile_{abs(hash(str(tree)))}", curate / "reconcile.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(curate))


@unittest.skipUnless(GIT, "git not available")
class RosterFollowsTheRunningTree(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "main"
        (self.main / "curate").mkdir(parents=True)
        for name in ("reconcile.py", "common.py"):
            shutil.copy2(ROOT / "curate" / name, self.main / "curate" / name)
        (self.main / "portfolio.md").write_text(
            HEADER + _row("orbit") + _row("sample-svc"), encoding="utf-8")
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed")

        # The machine-local locator config: present ONLY in the main checkout, exactly
        # as on a real machine (it is gitignored there, so a lane never receives one).
        (self.main / "repo-paths.local").write_text("orbit = /nowhere\n", encoding="utf-8")

        self.lane = self.tmp / "lane"
        _git(self.main, "worktree", "add", "-q", str(self.lane), "-b", "worktree-lane")

    def _add_row_in_lane(self, sid):
        p = self.lane / "portfolio.md"
        p.write_text(p.read_text(encoding="utf-8") + _row(sid), encoding="utf-8")

    def test_a_roster_row_applied_in_a_lane_is_visible_to_that_lane(self):
        """Property 1 — the defect itself, in one assertion.

        `newcomer` exists in the lane and nowhere else, which is the state of any lane
        between applying a roster row and landing it. Reading main here is what made the
        lane's own verified fix report as off-roster.
        """
        self._add_row_in_lane("newcomer")
        lane_mod = _load_reconcile(self.lane)
        self.assertIn("newcomer", lane_mod.roster_ids())

    def test_the_main_checkout_is_unaffected_by_the_lanes_unlanded_edit(self):
        """The other direction: splitting the roots must not leak the lane into main.

        Run from the main checkout, an unlanded lane edit is correctly invisible — main
        genuinely does not have that member yet.
        """
        self._add_row_in_lane("newcomer")
        main_mod = _load_reconcile(self.main)
        roster = main_mod.roster_ids()
        self.assertNotIn("newcomer", roster)
        self.assertEqual({"orbit", "sample-svc"}, roster)

    def test_machine_local_config_still_resolves_to_the_main_checkout(self):
        """Property 2 — the regression this fix could plausibly cause, pinned.

        A lane has no copy of the gitignored locator config. If the split had moved
        these onto the running tree too, every lane run would silently locate nothing —
        the session-99 false 'no roots' finding, reintroduced.
        """
        lane_mod = _load_reconcile(self.lane)
        self.assertEqual(lane_mod.REPO_PATHS_CONFIG, self.main.resolve() / "repo-paths.local")
        self.assertEqual(lane_mod.ROOTS_CONFIG, self.main.resolve() / "reconcile-roots.local")
        self.assertTrue(lane_mod.REPO_PATHS_CONFIG.is_file(),
                        "the lane must still reach a config that exists only in main")

    def test_the_trunk_only_status_view_still_resolves_to_the_main_checkout(self):
        """Property 3 — `STATUS.md` is written by the trunk, so it is read from there.

        Stated as a live read rather than a path comparison: the lane's own copy is
        given DIFFERENT content, and `self_entry()` must return main's. Otherwise the
        federation's self-row would report whatever a lane happened to freeze.
        """
        for tree, sid in ((self.main, "federation"), (self.lane, "stale-lane-copy")):
            (tree / "STATUS.md").write_text(
                f"---\nid: {sid}\nversion: 1.0.0\n---\n", encoding="utf-8")
        lane_mod = _load_reconcile(self.lane)
        entry, idless = lane_mod.self_entry()
        self.assertEqual(["federation"], list(entry))
        self.assertEqual([], idless)

    def test_the_status_verdict_names_the_tree_it_actually_read(self):
        """Property 4 — ADR-0108 D1 held through the reroute.

        The clause must name the LANE now, and carry its commit. A verdict measured
        against one tree while naming another is the WI-0236 failure wearing a fix.
        """
        self._add_row_in_lane("newcomer")
        (self.lane / "reconcile-roots.local").write_text("", encoding="utf-8")
        lane_mod = _load_reconcile(self.lane)
        line = lane_mod.status_line()
        self.assertIn(str(self.lane.resolve()), line)
        head = subprocess.run([GIT, "-C", str(self.lane), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertIn(head[:7], line)
        self.assertNotIn(f"{self.main.resolve()} at", line)


if __name__ == "__main__":
    unittest.main()
