"""The detector for fixture repos that borrow the machine's git configuration.

Two keys, one defect. `init.defaultBranch` is the one this module was written for;
`commit.gpgsign` is the same bug one key over, found by WI-0250's sweep and added here
rather than in a module of its own, because a second detector for "fixtures must not read
git config off the machine" would be the duplication these guards exist to prevent.


The suite was green on one machine and red on another for one reason: one git
distribution ships a system gitconfig carrying `init.defaultBranch = main`, and another
carries no such file. Twenty-two
fixtures called `git init` with no `-b`, so every one of them silently read that setting
off the machine. Most did not care what the branch was called; `LandWithRemoteTest` did —
its bare `origin` came up on `master`, the clone of it could not check out `main`, and the
push failed. A land on the second machine would have been blocked by a gate failure that says
nothing about that machine.

The rule is mechanical and needs no exemption list: a line that hands `"init"` to git
creates a repository, and a repository a test depends on must state its own branch name
rather than inherit one. This is
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) applied
to the fixture — the assumption was real, held everywhere, and written down nowhere.

`"init"` also appears as a commit message (`"commit", "-qm", "init"`), which creates
nothing; those lines are excluded by the presence of the `commit` verb on the same line.
"""

import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent

#: `"init"` handed to git on a line that is reaching for git at all.
CREATES_REPO = re.compile(r'"init"')
REACHES_GIT = re.compile(r"\b(_git|git|GIT)\b")
#: A commit whose message happens to be "init" — creates no repository.
IS_COMMIT = re.compile(r'"commit"')
#: An explicit initial branch, in either of git's spellings.
NAMES_BRANCH = re.compile(r'"-b",\s*"main"|--initial-branch[= ]main')
#: Signing turned off, in any of the three spellings a fixture legitimately needs:
#: `git config commit.gpgsign false` on a repo that already exists, `-c
#: commit.gpgsign=false` on a one-shot invocation, and `GIT_CONFIG_*` in the environment
#: for the one case that has no repo to configure yet (production code creating the repo).
DISABLES_SIGNING = re.compile(r"gpgsign")
#: A module that never commits cannot be broken by a signing default.
COMMITS = re.compile(r'"commit"|commit -')


def _modules():
    """(path, source) for every test module but this one."""
    for p in sorted(TESTS.glob("*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue
        yield p, p.read_text(encoding="utf-8")


def _repo_creating_lines():
    """(module name, 1-indexed line number, line) for every fixture repo creation."""
    for p in sorted(TESTS.glob("*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if (CREATES_REPO.search(line) and REACHES_GIT.search(line)
                    and not IS_COMMIT.search(line)):
                yield p.name, n, line


class FixtureReposNameTheirBranchTest(unittest.TestCase):
    def test_every_fixture_repo_states_its_own_initial_branch(self):
        borrowed = [f"{name}:{n}" for name, n, line in _repo_creating_lines()
                    if not NAMES_BRANCH.search(line)]
        self.assertEqual(borrowed, [], (
            "these fixtures create a repository without saying what its first branch is "
            "called, so they read `init.defaultBranch` off whatever machine runs them — "
            "`main` under a git whose system config sets it, `master` under one that does not. "
            'Pass `"-b", "main"` in the call.'))

    def test_the_scan_finds_the_fixtures_it_is_guarding(self):
        """A detector that matches nothing certifies a gap instead of catching it."""
        found = list(_repo_creating_lines())
        self.assertGreater(len(found), 15,
                           "the scan stopped matching the fixtures it exists to cover — "
                           "the pattern drifted, not the fixtures")

    def test_the_detector_would_actually_fire(self):
        """A check that cannot fail is not a check
        ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""
        borrowed = '        _git(self.main, "init", "-q")\n'
        explicit = '        _git(self.main, "init", "-q", "-b", "main")\n'
        commit_message = '        _git(self.main, "commit", "-qm", "init")\n'

        self.assertTrue(CREATES_REPO.search(borrowed) and REACHES_GIT.search(borrowed))
        self.assertFalse(NAMES_BRANCH.search(borrowed))
        self.assertTrue(NAMES_BRANCH.search(explicit))
        self.assertTrue(IS_COMMIT.search(commit_message),
                        "a commit whose message is 'init' must not read as a repo creation")


class FixtureReposDisableCommitSigningTest(unittest.TestCase):
    """The same defect as above, on `commit.gpgsign` instead of `init.defaultBranch`.

    A developer with `commit.gpgsign = true` in their global config has every fixture
    commit shell out to gpg. With no TTY that either hangs on a pinentry prompt or fails
    outright, and because the fixtures run git with `check=True` the failure lands in
    `setUp` — so the suite errors in bulk, before a single assertion, with a message about
    gpg rather than about anything under test. Repo-local config overrides global, so
    `git config commit.gpgsign false` on the fixture repo is the whole fix; the bug is
    only ever forgetting it.

    Measured before the fix (WI-0250), with `commit.gpgsign = true` supplied at global
    precedence through `GIT_CONFIG_GLOBAL` and `gpg.program = /usr/bin/false` so a signing
    attempt fails fast instead of hanging: `test_bootstrap_adopt` 4 failures,
    `test_poga_cli` 1, against 0 for every module that already carried the setting.
    """

    def _modules_that_commit_into_a_fixture_repo(self):
        creators = {name for name, _n, _line in _repo_creating_lines()}
        for p, src in _modules():
            if p.name in creators and COMMITS.search(src):
                yield p, src

    def test_every_module_that_commits_disables_signing(self):
        missing = sorted(p.name for p, src in self._modules_that_commit_into_a_fixture_repo()
                         if not DISABLES_SIGNING.search(src))
        self.assertEqual(missing, [], (
            "these modules commit into a fixture repo without disabling commit signing, "
            "so they inherit `commit.gpgsign` from whoever runs the suite and break on a "
            "machine configured to sign. Add `\"config\", \"commit.gpgsign\", \"false\"` "
            "beside the fixture's user.name config, or `-c commit.gpgsign=false` on a "
            "one-shot git invocation."))

    def test_the_scan_finds_the_fixtures_it_is_guarding(self):
        """A detector that matches nothing certifies a gap instead of catching it."""
        covered = list(self._modules_that_commit_into_a_fixture_repo())
        self.assertGreater(len(covered), 15,
                           "the scan stopped matching the fixtures it exists to cover — "
                           "the pattern drifted, not the fixtures")

    def test_the_detector_would_actually_fire(self):
        """A check that cannot fail is not a check
        ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""
        borrowed = '_git(d, "config", "user.name", "T")\n_git(d, "commit", "-qm", "x")\n'
        explicit = borrowed + '_git(d, "config", "commit.gpgsign", "false")\n'
        self.assertFalse(DISABLES_SIGNING.search(borrowed))
        self.assertTrue(DISABLES_SIGNING.search(explicit))
        self.assertTrue(COMMITS.search(borrowed))
        self.assertFalse(COMMITS.search('_git(d, "init", "-q", "-b", "main")\n'),
                         "a module that only creates repos cannot be broken by signing")


if __name__ == "__main__":
    unittest.main()
