"""The suite may not read the live store (ADR-0148 D3, WI-0427).

A bookkeeping-only land runs no suite. That is safe only while no test's outcome can
depend on the real work-items/, sessions/, ops-items/ … as they sit in the checkout —
so `curate/store_guard.py` fails any test that opens, lists or scans them, in the
worker and in its Python children. These tests are the guard's own acceptance: each
builds a miniature harness checkout (the REAL runner and guard, a `sessionlib/config.py`
declaring `BOOKKEEPING_PATHS`, a tests/ directory of our own) and runs the real runner
over it, so the property is proven end to end rather than by calling the hook.

Acceptance 6 of WI-0427: "the guard test fails when a test is made to read the live
work-items/ directory" — `ALiveReadFailsTheTest` is that test.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "curate"))
import store_guard  # noqa: E402

sys.path.insert(0, str(REPO))
import session  # noqa: E402

CONFIG = 'BOOKKEEPING_PATHS = ("work-items/", "sessions/", "STATUS.md")\n'


class _MiniHarness(unittest.TestCase):
    """A checkout whose own work-items/ is, for the runner inside it, the LIVE store."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="storeguard-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        root = self.root = self.tmp / "repo"
        (root / "curate").mkdir(parents=True)
        for rel in ("curate/run_suite.py", "curate/store_guard.py", "poga_evidence.py"):
            shutil.copy(REPO / rel, root / rel)
        shutil.copytree(REPO / "curate" / "storeguard_child", root / "curate" / "storeguard_child")
        (root / "sessionlib").mkdir()
        (root / "sessionlib" / "config.py").write_text(CONFIG, encoding="utf-8")
        (root / "work-items").mkdir()
        (root / "work-items" / "WI-0001-x.md").write_text("# WI-0001\n", encoding="utf-8")
        (root / "tests").mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)

    def _suite(self, body: str, mode: str | None = None):
        (self.root / "tests" / "test_probe.py").write_text(textwrap.dedent(body),
                                                           encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("POGA_STORE_GUARD")}
        if mode:
            env[store_guard.ENV_MODE] = mode
        return subprocess.run([sys.executable, "curate/run_suite.py", "-j", "1"],
                              cwd=str(self.root), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env, timeout=120)


READS_LIVE = '''
    import pathlib, unittest
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    class T(unittest.TestCase):
        def test_reads_the_live_store(self):
            (ROOT / "work-items" / "WI-0001-x.md").read_text()
'''


class ALiveReadFailsTheTest(_MiniHarness):

    def test_a_test_that_reads_live_work_items_fails_by_name(self):
        r = self._suite(READS_LIVE)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("FAIL: test_reads_the_live_store", r.stderr)
        self.assertIn("store guard (ADR-0148 D3)", r.stderr)
        self.assertIn("work-items/WI-0001-x.md", r.stderr)

    def test_listing_the_directory_is_a_read(self):
        r = self._suite('''
            import os, pathlib, unittest
            ROOT = pathlib.Path(__file__).resolve().parent.parent
            class T(unittest.TestCase):
                def test_lists(self):
                    os.listdir(ROOT / "work-items")
        ''')
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("FAIL: test_lists", r.stderr)

    def test_a_child_process_read_is_charged_to_the_test(self):
        r = self._suite('''
            import pathlib, subprocess, sys, unittest
            ROOT = pathlib.Path(__file__).resolve().parent.parent
            class T(unittest.TestCase):
                def test_child_reads(self):
                    subprocess.run([sys.executable, "-c",
                                    "open(%r).read()" % str(ROOT / "work-items" / "WI-0001-x.md")],
                                   check=True)
        ''')
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("FAIL: test_child_reads", r.stderr)
        self.assertIn("(child:", r.stderr)

    def test_a_read_at_import_is_not_lost(self):
        r = self._suite('''
            import pathlib, unittest
            ROOT = pathlib.Path(__file__).resolve().parent.parent
            (ROOT / "work-items" / "WI-0001-x.md").read_text()
            class T(unittest.TestCase):
                def test_after_import(self):
                    pass
        ''')
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("before this test: import or setUpClass", r.stderr)


class AFixtureReadPasses(_MiniHarness):

    def test_a_copy_in_a_tmpdir_is_not_the_live_store(self):
        r = self._suite('''
            import pathlib, shutil, tempfile, unittest
            ROOT = pathlib.Path(__file__).resolve().parent.parent
            class T(unittest.TestCase):
                def test_reads_a_fixture(self):
                    d = pathlib.Path(tempfile.mkdtemp())
                    (d / "work-items").mkdir()
                    (d / "work-items" / "WI-0001-x.md").write_text("x")
                    (d / "work-items" / "WI-0001-x.md").read_text()
                    shutil.rmtree(d)
        ''')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stderr)

    def test_report_mode_records_without_failing(self):
        r = self._suite(READS_LIVE, mode="report")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_non_bookkeeping_path_is_not_guarded(self):
        (self.root / "notes.md").write_text("x", encoding="utf-8")
        r = self._suite('''
            import pathlib, unittest
            ROOT = pathlib.Path(__file__).resolve().parent.parent
            class T(unittest.TestCase):
                def test_reads_code(self):
                    (ROOT / "notes.md").read_text()
        ''')
        self.assertEqual(r.returncode, 0, r.stderr)


class TheRuleIsTheLandsRule(unittest.TestCase):
    """The guard and the land must police one list, or a path the land treats as
    bookkeeping is a path the guard lets tests read (P16)."""

    def test_the_guard_reads_the_same_list_the_land_uses(self):
        self.assertEqual(store_guard.bookkeeping_paths(REPO), tuple(session.BOOKKEEPING_PATHS))

    def test_directory_prefixes_match_by_prefix_and_files_exactly(self):
        pre = ["/r/work-items/", "/r/STATUS.md"]
        self.assertTrue(store_guard.matches("/r/work-items/WI-1.md", pre))
        self.assertTrue(store_guard.matches("/r/work-items", pre))
        self.assertTrue(store_guard.matches("/r/STATUS.md", pre))
        self.assertIsNone(store_guard.matches("/r/STATUS.md.bak", pre))
        self.assertIsNone(store_guard.matches("/r/work-itemsX/a", pre))
        self.assertIsNone(store_guard.matches(7, pre))

    def test_a_bare_relative_name_is_not_resolved_against_the_cwd(self):
        # `os.open(name, dir_fd=fd)` — shutil.rmtree's walk — reports a bare name and no
        # dir_fd. Resolving it against a cwd at the repo charged every tmpdir teardown to
        # the real store (31k false reads on the first measurement).
        cwd = os.getcwd()
        try:
            os.chdir("/")
            self.assertIsNone(store_guard.matches("r", ["/r/"]))
        finally:
            os.chdir(cwd)

    def test_the_main_checkout_is_a_root_too(self):
        main = store_guard.main_checkout(REPO)
        self.assertIsNotNone(main)
        prefixes = store_guard.forbidden_prefixes(REPO)
        self.assertIn(str(main).rstrip("/") + "/work-items/", prefixes)


if __name__ == "__main__":
    unittest.main()
