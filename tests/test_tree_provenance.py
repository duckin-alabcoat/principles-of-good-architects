"""ADR-0108 D1/D2 / WI-0236 — a read names the tree and commit it came from.

`shared_work_root()` answers *where* to read. It has always answered correctly and that
is exactly why the defect was invisible: `curate/reconcile.py` read `portfolio.md` from
the MAIN checkout, as designed, and reported a roster row applied *and verified* in a lane
as off-roster. The verdict was not wrong about the file it read — it was silent about
which file that was.

The direction is not fixable by anchoring. The same session also had a lane reading its
own frozen copy while main was newer, so "always read main" and "always read the lane" are
each wrong half the time. Naming the source is the only move that is right in both.

Properties:

  1. A READABLE TREE YIELDS ITS COMMIT, and the rendered clause carries both the path and
     the short sha.
  2. UNKNOWN IS NOT UNREADABLE IS NOT FINE. Three outcomes, distinctly rendered
     (`declare-what-a-check-assumes`).
  3. THE RECONCILE VERDICT CARRIES THE CLAUSE — the surface that produced instance 2,
     tested at the surface rather than only at the helper.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
import reconcile  # noqa: E402
from common import provenance_clause, tree_provenance  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True)


class TreeProvenance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main", ".")
        _git(self.repo, "config", "user.email", "test@example.invalid")
        _git(self.repo, "config", "user.name", "Test")
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", "seed.txt")
        _git(self.repo, "commit", "-m", "seed")

    def test_readable_tree_yields_its_commit(self):
        """Property 1."""
        p = tree_provenance(self.repo)
        self.assertTrue(p["readable"])
        head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(p["sha"], head)

        clause = provenance_clause(self.repo)
        self.assertIn(str(self.repo), clause)
        self.assertIn(head[:7], clause)
        self.assertTrue(clause.startswith("per "), clause)

    def test_a_present_directory_with_no_commit_is_UNKNOWN_not_unreadable(self):
        """Property 2, middle state — the tree is there, the commit is not.

        This is the distinction the ADR's three-state table turns on and the one most
        easily lost: a directory that exists and simply has no commit is honestly
        reportable as a source ("per <path>"), and only its commit is missing. Folding it
        into UNREADABLE would tell the reader we could not see the tree when we could.
        """
        plain = pathlib.Path(self.tmp.name) / "not-a-repo"
        plain.mkdir()
        p = tree_provenance(plain)
        self.assertTrue(p["readable"])
        self.assertIsNone(p["sha"])
        clause = provenance_clause(plain)
        self.assertIn("commit UNKNOWN", clause)
        self.assertIn(str(plain), clause)

    def test_a_missing_path_is_unreadable(self):
        """Property 2, third state — absence must not render as a clean read."""
        missing = pathlib.Path(self.tmp.name) / "gone"
        p = tree_provenance(missing)
        self.assertFalse(p["readable"])
        self.assertIn("UNREADABLE", provenance_clause(missing))
        self.assertNotIn("commit UNKNOWN", provenance_clause(missing))

    def test_the_three_outcomes_are_mutually_distinct(self):
        """Property 2, stated directly: no two outcomes may render alike.

        Without this, a future 'simplification' that collapses unreadable into unknown
        still passes both cases above.
        """
        plain = pathlib.Path(self.tmp.name) / "plain"
        plain.mkdir()
        known = provenance_clause(self.repo)
        unknown = provenance_clause(plain)
        unreadable = provenance_clause(pathlib.Path(self.tmp.name) / "absent")
        self.assertEqual(len({known, unknown, unreadable}), 3,
                         f"outcomes collapsed: {known!r} {unknown!r} {unreadable!r}")
        self.assertIn(" at ", known)
        self.assertIn("commit UNKNOWN", unknown)
        self.assertIn("UNREADABLE", unreadable)


class ReconcileNamesItsRoster(unittest.TestCase):
    """Property 3 — instance 2's own surface, not just the helper under it.

    THIS CLASS USED TO READ THE DEVELOPER'S MACHINE, and that is WI-0250's second
    measured instance of the ambient-state class. It shelled out to the real
    `curate/reconcile.py --status` in the real checkout and asserted on the answer — so
    what it actually pinned was the runner's own `reconcile-roots.local` and
    `repo-paths.local`, two GITIGNORED machine-local files. Where they exist the test
    passes; in any fresh clone, on a rebuilt devbox, or in CI they do not exist,
    `status_line()` takes its no-config branch, and the test fails with an assertion about
    a missing substring while nothing at all is wrong with the code.

    Measured, not argued: same commit, same environment, run from the lane -> green; run
    from a clone of that same commit -> `AssertionError: 'roster ' not found in
    'Reconcile: no roots or repo-path map ...'`.

    The rewrite drives `status_line()` in-process against a fixture root, which is what
    made the third state testable at all. The old test could not have covered the
    no-config branch: it had no way to produce one, because it could not choose what
    config the machine had.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)

        # A member repo for the walk to find, with the STATUS.md `find_status_files` reads.
        self.member = base / "member"
        self.member.mkdir()
        _git(self.member, "init", "-b", "main", ".")
        _git(self.member, "config", "user.email", "test@example.invalid")
        _git(self.member, "config", "user.name", "Test")
        _git(self.member, "config", "commit.gpgsign", "false")
        (self.member / "STATUS.md").write_text(
            "---\nsystem: testmember\nversion: 1.0.0\n---\n", encoding="utf-8")
        _git(self.member, "add", "STATUS.md")
        _git(self.member, "commit", "-m", "seed")

        # The federation tree the roster is read FROM — the tree whose provenance the
        # verdict must name (ADR-0108 D1 / WI-0246).
        self.fed = base / "fed"
        self.fed.mkdir()
        _git(self.fed, "init", "-b", "main", ".")
        _git(self.fed, "config", "user.email", "test@example.invalid")
        _git(self.fed, "config", "user.name", "Test")
        _git(self.fed, "config", "commit.gpgsign", "false")
        (self.fed / "portfolio.md").write_text(
            "# Portfolio\n\n- `testmember` — a member\n", encoding="utf-8")
        _git(self.fed, "add", "portfolio.md")
        _git(self.fed, "commit", "-m", "roster")

        self.roots_cfg = base / "reconcile-roots.local"
        self.map_cfg = base / "repo-paths.local"
        self.roots_cfg.write_text(f"{self.member.parent}\n", encoding="utf-8")
        self.map_cfg.write_text("", encoding="utf-8")

        for attr, value in (("FED_ROOT", self.fed),
                            ("ROOTS_CONFIG", self.roots_cfg),
                            ("REPO_PATHS_CONFIG", self.map_cfg)):
            p = unittest.mock.patch.object(reconcile, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # `self_entry` reads the MAIN checkout's STATUS.md by design (it is a trunk-only
        # generated view). That is correct in production and ambient here, so the fixture
        # states it has no self entry rather than inheriting the runner's.
        p = unittest.mock.patch.object(reconcile, "self_entry", return_value=({}, []))
        p.start()
        self.addCleanup(p.stop)

    def test_status_line_carries_the_roster_source(self):
        """Property 3, with the config the fixture chose rather than the machine's."""
        line = reconcile.status_line()
        self.assertTrue(line.startswith("Reconcile:"), line)
        self.assertIn("roster ", line)
        self.assertIn("per ", line)
        self.assertIn(str(self.fed), line)
        head = _git(self.fed, "rev-parse", "HEAD").stdout.strip()
        self.assertIn(head[:7], line)

    def test_the_no_config_branch_names_no_source_and_says_so(self):
        """The third state, which the ambient version of this test could not reach.

        With no roots and no map nothing has been measured, so there IS no roster source
        to name — and the honest answer is the one that says the signal is off, not a
        verdict with an empty clause. This is
        [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
        at the surface: 'couldn't tell' must not render like 'checked and fine'.
        """
        self.roots_cfg.write_text("", encoding="utf-8")
        self.map_cfg.write_text("", encoding="utf-8")
        line = reconcile.status_line()
        self.assertIn("no roots or repo-path map", line)
        self.assertNotIn("self-consistent", line)
        self.assertNotIn("per ", line)

    def test_the_two_answers_are_not_the_same_answer(self):
        """A check that cannot fail is not a check — the configured and unconfigured
        verdicts must be distinguishable, or the branch above proves nothing."""
        configured = reconcile.status_line()
        self.roots_cfg.write_text("", encoding="utf-8")
        self.map_cfg.write_text("", encoding="utf-8")
        unconfigured = reconcile.status_line()
        self.assertNotEqual(configured, unconfigured)


if __name__ == "__main__":
    unittest.main()
