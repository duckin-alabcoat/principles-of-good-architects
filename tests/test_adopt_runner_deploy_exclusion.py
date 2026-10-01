"""A deploy tree must never be discovered as an adoptable repo (WI-0233).

A deploy tree is a checkout of a converged member's OWN repo, so it carries both
convergence markers and is structurally indistinguishable from that member's working
repo. Adoption running inside one would mutate a tree whose entire contract is that it
is exactly its tag.

devbox's walk roots do not contain the deploy root, but every machine's roots file is
machine-local and unreadable from the others, so "the roots are fine" is an assumption
about a file nobody can check remotely — and the roots file is precisely the thing that
gets edited without anyone thinking about this. Hence a structural exclusion, and hence
this test.

The negative control is the point of the test. Without it, a green result could equally
mean discovery found nothing at all, which is the wrong-answer-shaped-like-a-right-one
this suite exists to catch.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "adopt_runner_excl", ROOT / "curate" / "adopt-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class DeployTreeExclusionTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name).resolve()
        # The deploy root sits UNDER the walk root on purpose: that is the
        # misconfiguration the guard exists to survive, not one it may assume away.
        self.deploy_root = self.base / "deploy"
        self.normal = self._repo(self.base / "Projects" / "widget")
        self.deployed = self._repo(self.deploy_root / "widget")

        self._prev = os.environ.get("POGA_DEPLOY_ROOT")
        os.environ["POGA_DEPLOY_ROOT"] = str(self.deploy_root)

        self._roots = runner.read_roots_config
        self._paths = runner.read_repo_paths_config
        runner.read_roots_config = lambda *a, **k: [str(self.base)]
        runner.read_repo_paths_config = lambda *a, **k: {}

    def tearDown(self):
        runner.read_roots_config = self._roots
        runner.read_repo_paths_config = self._paths
        if self._prev is None:
            os.environ.pop("POGA_DEPLOY_ROOT", None)
        else:
            os.environ["POGA_DEPLOY_ROOT"] = self._prev
        self._tmp.cleanup()

    @staticmethod
    def _repo(path):
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").mkdir(exist_ok=True)
        for marker in runner.CONVERGENCE_MARKERS:
            (path / marker).write_text("x", encoding="utf-8")
        return path.resolve()

    def test_deploy_root_import_is_live(self):
        """The guard is only real if the shared definition actually imported."""
        self.assertIsNotNone(
            runner._deploy_root,
            "deploy_root did not import — the exclusion is inert and this suite would "
            "pass while the guard does nothing")

    def test_deploy_tree_is_excluded_and_ordinary_repo_is_not(self):
        found = runner.discover_repos()
        self.assertIn(self.normal, found,
                      "the ordinary member repo was dropped — the guard is over-broad")
        self.assertNotIn(self.deployed, found,
                         "a deploy tree was offered for adoption")

    def test_negative_control_reproduces_without_the_guard(self):
        """With the guard off the deploy tree MUST reappear, or this file proves nothing."""
        prev = runner._deploy_root
        try:
            runner._deploy_root = None
            self.assertIn(self.deployed, runner.discover_repos())
        finally:
            runner._deploy_root = prev

    def test_honours_the_env_var_rather_than_a_hardcoded_path(self):
        """The exclusion must follow POGA_DEPLOY_ROOT, not a copy of its default."""
        os.environ["POGA_DEPLOY_ROOT"] = str(self.base / "somewhere-else")
        self.assertIn(self.deployed, runner.discover_repos())


if __name__ == "__main__":
    unittest.main()
