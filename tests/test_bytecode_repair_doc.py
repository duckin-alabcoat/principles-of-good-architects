"""WI-0461 leftover: the one-time repair for a project that already tracks bytecode.

The sweeps no longer stage `.pyc` files, so a project hit before that fix keeps its
tracked bytecode until someone removes it from the index. docs/first-task.md gives the
commands. This test runs THOSE commands, read from the doc, in a scratch repo, under
bash and under zsh. The doc and the test cannot drift: change the doc and this runs the
new text.
"""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "first-task.md"
HEADING = "### Python bytecode is tracked in git"


def documented_repair() -> str:
    """The first ```sh block under the troubleshooting heading, verbatim."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(HEADING) + len(HEADING)
    end = text.find("\n## ", start)
    section = text[start:end if end != -1 else len(text)]
    m = re.search(r"```sh\n(.*?)```", section, re.S)
    if not m:
        raise AssertionError("no ```sh block under %r in %s" % (HEADING, DOC))
    return m.group(1)


BYTECODE = [
    "__pycache__/interpreter.cpython-314.pyc",
    "sessionlib/__pycache__/config.cpython-39.pyc",
    "pkg dir/__pycache__/m.cpython-312.pyc",   # a space must not split the path
    "stray.pyc",
]
SOURCE = ["greet.py", "tests/test_greet.py", "pkg dir/m.py", "notes-about-pyc.md"]


class DocumentedBytecodeRepairTest(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        home = self.tmp / "home"
        home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "ZDOTDIR": str(home),
        }
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "gc.auto", "0")
        self.git("config", "maintenance.auto", "false")

    def git(self, *args) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def commit_files(self, paths):
        for rel in paths:
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")

    def run_doc(self, shell):
        exe = shutil.which(shell)
        if not exe:
            self.skipTest("%s not installed" % shell)
        return subprocess.run([exe, "-c", documented_repair()], cwd=self.repo,
                              env=self.env, capture_output=True, text=True)

    def tracked(self):
        return set(self.git("ls-files", "-z").split("\0")) - {""}

    def check_repairs(self, shell):
        self.commit_files(SOURCE + BYTECODE)
        before = self.git("rev-parse", "HEAD").strip()
        proc = self.run_doc(shell)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(self.tracked(), set(SOURCE), "only bytecode left the index")
        for rel in BYTECODE:
            self.assertTrue((self.repo / rel).is_file(), "%s stays on disk" % rel)
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), before, "committed")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "", "index clean")
        head_tree = set(self.git("ls-tree", "-r", "-z", "--name-only", "HEAD")
                        .split("\0")) - {""}
        self.assertEqual(head_tree, set(SOURCE))

    def check_noop(self, shell):
        self.commit_files(SOURCE)
        before = self.git("rev-parse", "HEAD").strip()
        proc = self.run_doc(shell)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Without --ignore-unmatch, `git rm` prints "fatal: pathspec ... did not match"
        # here, and the exit status still reads 0 (the last line's). Quiet is the check.
        self.assertEqual(proc.stderr, "", "nothing to remove is not an error")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before, "no empty commit")
        self.assertEqual(self.tracked(), set(SOURCE))

    def test_bash_removes_tracked_bytecode_and_keeps_the_files(self):
        self.check_repairs("bash")

    def test_zsh_removes_tracked_bytecode_and_keeps_the_files(self):
        self.check_repairs("zsh")

    def test_bash_is_a_clean_noop_when_nothing_matches(self):
        self.check_noop("bash")

    def test_zsh_is_a_clean_noop_when_nothing_matches(self):
        self.check_noop("zsh")

    def test_the_doc_block_is_found(self):
        block = documented_repair()
        self.assertIn("git rm", block)
        self.assertIn("--cached", block)


if __name__ == "__main__":
    unittest.main()
