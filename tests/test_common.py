"""Tests for curate/common.py — shared mechanics for the curate scripts.

stdlib unittest: python3 -m unittest tests.test_common
"""

import contextlib
import importlib.util
import io
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("common", ROOT / "curate" / "common.py")
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class SharedWorkRootTest(unittest.TestCase):
    """`shared_work_root` is the curate-side mirror of session.py's own
    `_shared_work_root()` — resolving `reconcile-roots.local` / `repo-paths.local`
    against the git common dir instead of `__file__`'s own location, so a `poga`
    lane's copy of e.g. reconcile.py reads the MAIN checkout's config (which
    exists) instead of its own lane checkout's copy (which, being gitignored,
    never does) — regression pin for session 99's false 'no roots' finding."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "main"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "x.txt").write_text("x", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_checkout_resolves_to_itself(self):
        self.assertEqual(common.shared_work_root(self.main), self.main.resolve())

    def test_worktree_lane_resolves_to_the_main_checkout(self):
        lane = self.tmp / "lane"
        _git(self.main, "worktree", "add", str(lane), "-b", "lane-branch")
        self.assertEqual(common.shared_work_root(lane), self.main.resolve())

    def test_non_git_directory_falls_back_to_itself(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(common.shared_work_root(plain), plain)


class RootsDiagnosisTest(unittest.TestCase):
    """WI-0132 — declared-but-none-exist must not read as declared-nothing.

    Found by running the new reachability sweep from a second machine: the roots config
    is machine-local by design (its own first line says so) but lives at one path inside a
    repo two machines can both see, so the second machine read the first one's paths, none
    of which exist there. Every root was skipped, the reader returned nothing, and
    `deliver --audit` went on to report eleven of eleven recipients unreachable — an
    answer built entirely out of a config that could not be read from where it stood.

    The machine-scoped-filename half is deferred (nothing fleet-wide runs on the second
    machine). This half is not, because it is machine-independent: a renamed
    root, a mid-migration checkout, or a new machine's first day all produce it.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = self.tmp / "reconcile-roots.local"

    def _write(self, *lines):
        self.cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_declaring_nothing_is_silent(self):
        """A member with no roots configured is an honest, different state — warning
        about it would train the reader to skim past the case that matters."""
        self.assertEqual(common.roots_diagnosis([], self.cfg), "")

    def test_one_surviving_root_is_enough(self):
        """Partial misses are ordinary (a retired path, a machine with a subset). The
        alarm is for a TOTAL evaporation, which is what a foreign config looks like."""
        self.assertEqual(
            common.roots_diagnosis([str(self.tmp), "/nope/not/here"], self.cfg), "")

    def test_all_roots_missing_is_named_with_the_likely_cause(self):
        note = common.roots_diagnosis(["/nope/a", "/nope/b"], self.cfg)
        self.assertIn("NO USABLE SEARCH ROOTS", note)
        self.assertIn("DIFFERENT machine", note, "name the hypothesis, not just the fact")
        self.assertIn(str(self.cfg), note, "name the file to fix")

    def test_the_reader_warns_at_the_choke_point(self):
        """The warning lives in the reader every roots-driven tool passes through — a
        check each caller must remember is one the next caller added will not make."""
        self._write("/nope/a")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            roots = common.read_roots_config(self.cfg)
        self.assertEqual(roots, ["/nope/a"], "the return value is unchanged")
        self.assertIn("NO USABLE SEARCH ROOTS", err.getvalue())

    def test_a_usable_config_says_nothing(self):
        self._write(str(self.tmp))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            common.read_roots_config(self.cfg)
        self.assertEqual(err.getvalue(), "")

    def test_warn_false_returns_the_roots_without_printing(self):
        self._write("/nope/a")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(common.read_roots_config(self.cfg, warn=False), ["/nope/a"])
        self.assertEqual(err.getvalue(), "")

    def test_an_absent_config_is_not_a_missing_roots_alarm(self):
        """No file at all is 'nothing declared', which the first test already covers —
        it must not come out as the foreign-machine warning."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(common.read_roots_config(self.tmp / "absent.local"), [])
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
