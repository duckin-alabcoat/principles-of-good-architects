"""Tests for curate/gather.py — the curate cursor and the --accept set.

Two load-bearing properties:

  1. --accept marks exactly the entries laid out by the gather it accepts, so an
     entry landing between gather and accept can never be silently marked reviewed
     without ever surfacing (the consultant-review R3 race).
  2. THREE states, never two: "N awaiting review", "nothing new to review", and
     "no producer file was readable" are three distinct outputs. Folding the third
     into the second is WI-0211 — a SessionStart hook that announced nothing to
     review, from a lane whose producer symlinks did not exist yet, while the same
     tool run by hand minutes later reported 38 waiting.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))

import gather  # noqa: E402

ENTRY_A = "## 2026-07-01 — lesson a\n\nBody of lesson a.\n"
ENTRY_B = "## 2026-07-02 — lesson b\n\nBody of lesson b.\n"


class GatherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self.tmp.name)
        (tmp / "curate").mkdir()
        (tmp / "inputs").mkdir()
        self.producer = tmp / "architect-learnings.md"
        self.producer.write_text(ENTRY_A, encoding="utf-8")
        self._saved = (gather.ROOT, gather.SEEN_PATH, gather.RUNS_DIR)
        gather.ROOT = tmp
        gather.SEEN_PATH = tmp / "curate" / "seen.json"
        gather.RUNS_DIR = tmp / "curate-runs"

    def tearDown(self):
        gather.ROOT, gather.SEEN_PATH, gather.RUNS_DIR = self._saved
        self.tmp.cleanup()

    def run_main(self, *argv):
        with mock.patch.object(sys, "argv", ["gather.py", *argv]):
            gather.main()

    def capture_main(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["gather.py", *argv]), \
                contextlib.redirect_stdout(buf):
            gather.main()
        return buf.getvalue()

    def test_accept_marks_only_the_gathered_set(self):
        self.run_main()                       # gather: REVIEW + sidecar for A
        # B lands AFTER the gather, before the accept — the race window.
        self.producer.write_text(ENTRY_A + "\n" + ENTRY_B, encoding="utf-8")
        self.run_main("--accept")
        reviewed = json.loads(gather.SEEN_PATH.read_text(encoding="utf-8"))["reviewed"]
        titles = {m["title"] for m in reviewed.values()}
        self.assertEqual(titles, {"lesson a"})
        # B is still awaiting review and surfaces on the next gather.
        pending = gather.gather()
        self.assertEqual([e["title"] for e in pending], ["lesson b"])

    def test_accept_without_a_gather_refuses(self):
        with self.assertRaises(SystemExit):
            self.run_main("--accept")

    def test_edited_entry_resurfaces(self):
        self.run_main()
        self.run_main("--accept")
        self.assertEqual(gather.gather(), [])
        self.producer.write_text(ENTRY_A.replace("Body", "Revised body"), encoding="utf-8")
        self.assertEqual([e["title"] for e in gather.gather()], ["lesson a"])

    def test_corrupt_seen_fails_loud(self):
        gather.SEEN_PATH.write_text("{ truncated", encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            gather.load_seen()
        self.assertIn("seen.json", str(ctx.exception))

    def test_sidecar_written_next_to_review(self):
        self.run_main()
        sidecars = list(gather.RUNS_DIR.glob("REVIEW-*.ids.json"))
        self.assertEqual(len(sidecars), 1)
        data = json.loads(sidecars[0].read_text(encoding="utf-8"))
        self.assertEqual(len(data["ids"]), 1)
        (meta,) = data["ids"].values()
        self.assertEqual(meta["title"], "lesson a")

    # ---------------------------------------------------------------- WI-0211

    def strip_producers(self):
        """The lane-before-_link_shared_data shape: no producer file of any kind."""
        self.producer.unlink()
        (pathlib.Path(self.tmp.name) / "inputs").rmdir()

    def test_status_says_awaiting_when_entries_are_waiting(self):
        out = self.capture_main("--status")
        self.assertIn("1 producer entry awaiting review", out)

    def test_status_says_nothing_new_when_the_queue_is_actually_clear(self):
        self.run_main()
        self.run_main("--accept")
        out = self.capture_main("--status")
        self.assertEqual(out.strip(), "Curate: nothing new to review.")

    def test_status_does_not_say_nothing_new_when_no_producer_file_is_readable(self):
        """THE acceptance bar. The pre-fix code printed the reassuring line here."""
        self.strip_producers()
        out = self.capture_main("--status")
        self.assertNotIn("nothing new to review", out)
        self.assertIn("NO PRODUCER FILE WAS READABLE", out)
        self.assertIn("architect-learnings.md", out)

    def test_the_three_status_lines_are_all_different(self):
        awaiting = self.capture_main("--status")
        self.run_main()
        self.run_main("--accept")
        clear = self.capture_main("--status")
        self.strip_producers()
        blind = self.capture_main("--status")
        self.assertEqual(len({awaiting.strip(), clear.strip(), blind.strip()}), 3)

    def test_status_exits_zero_even_when_blind(self):
        """It rides a SessionStart hook: it may shout, it may not break the session."""
        self.strip_producers()
        self.capture_main("--status")        # main() returns; no SystemExit

    def test_a_hand_run_gather_refuses_rather_than_reporting_emptiness(self):
        self.strip_producers()
        with self.assertRaises(SystemExit) as ctx:
            self.run_main()
        self.assertIn("NO PRODUCER FILE WAS READABLE", str(ctx.exception))

    def test_pending_count_is_none_when_blind_and_zero_when_clear(self):
        """The counting half — metrics.py renders None and 0 differently (WI-0087)."""
        self.run_main()
        self.run_main("--accept")
        self.assertEqual(gather.pending_count(), 0)
        self.strip_producers()
        self.assertIsNone(gather.pending_count())

    def test_an_unreadable_producer_file_is_not_an_empty_one(self):
        """Present but unreadable is the third state too — not silently skipped."""
        self.producer.chmod(0o000)
        try:
            if os.access(self.producer, os.R_OK):
                self.skipTest("running as root — chmod does not deny a read")
            (pathlib.Path(self.tmp.name) / "inputs").rmdir()
            out = self.capture_main("--status")
        finally:
            # Restored here, not via addCleanup: cleanups run AFTER tearDown, which
            # has already deleted the tmpdir this file lives in.
            self.producer.chmod(0o644)
        self.assertNotIn("nothing new to review", out)
        self.assertIn("architect-learnings.md", out)


class ProducerRootTest(unittest.TestCase):
    """The race itself (WI-0211): producer files resolve to the MAIN checkout, so a
    lane reads them BEFORE `_link_shared_data()` has symlinked anything into it.

    Built as a real worktree, not a mock: the whole defect was that the path looked
    right from inside a lane, and a stub of `shared_work_root` would reproduce the
    belief rather than the filesystem."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.main_co = pathlib.Path(self.tmp.name) / "main"
        self.main_co.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        (self.main_co / "seed").write_text("x", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        # The gitignored shared data — present ONLY in the main checkout, exactly as
        # `architect-learnings.md` and `inputs/` are on disk.
        (self.main_co / "architect-learnings.md").write_text(ENTRY_A, encoding="utf-8")
        self.lane = pathlib.Path(self.tmp.name) / "lane"
        self.git("worktree", "add", "-q", "-b", "lane", str(self.lane))

        self._saved = (gather.ROOT, gather.SEEN_PATH, gather.RUNS_DIR)
        self.addCleanup(self.restore)
        gather.ROOT = self.lane
        gather.SEEN_PATH = self.lane / "curate" / "seen.json"
        gather.RUNS_DIR = self.lane / "curate-runs"

    def restore(self):
        gather.ROOT, gather.SEEN_PATH, gather.RUNS_DIR = self._saved

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.main_co), *args], check=True,
                       capture_output=True)

    def test_a_lane_reads_the_main_checkouts_producer_file(self):
        self.assertFalse((self.lane / "architect-learnings.md").exists(),
                         "precondition: the lane has not been symlinked yet")
        self.assertEqual([e["title"] for e in gather.gather()], ["lesson a"])

    def test_the_entry_is_labelled_by_its_relative_path_not_an_absolute_one(self):
        (entry,) = gather.gather()
        self.assertEqual(entry["source"], "architect-learnings.md")

    def test_status_from_an_unlinked_lane_reports_the_real_count(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["gather.py", "--status"]), \
                contextlib.redirect_stdout(buf):
            gather.main()
        self.assertIn("1 producer entry awaiting review", buf.getvalue())


class PushSubstrateDiffTest(unittest.TestCase):
    """diff_files: STANDARD.md is refresh-only — refreshed where present,
    never introduced into a CANON-floor repo (the two-floors rule)."""

    def setUp(self):
        import importlib
        self.ps = importlib.import_module("push-substrate")
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.fed = base / "fed"
        self.repo = base / "repo"
        self.fed.mkdir()
        self.repo.mkdir()
        for name in self.ps.SUBSTRATE_FILES:
            dst = self.fed / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(f"fed {name}", encoding="utf-8")
        self._fed_root = self.ps.FED_ROOT
        self.ps.FED_ROOT = self.fed

    def tearDown(self):
        self.ps.FED_ROOT = self._fed_root
        self.tmp.cleanup()

    def test_standard_not_introduced_on_canon_floor(self):
        # CANON-floor repo: has CANON.md + session.py, no STANDARD.md.
        # diff_files now returns (label, rel_path, want_bytes) tuples.
        (self.repo / "CANON.md").write_text("stale", encoding="utf-8")
        (self.repo / "session.py").write_text("stale", encoding="utf-8")
        labels = [c[0] for c in self.ps.diff_files(self.repo)]
        self.assertNotIn("STANDARD.md", labels)
        self.assertIn("CANON.md", labels)
        self.assertIn("session.py", labels)

    def test_standard_refreshed_where_present(self):
        (self.repo / "STANDARD.md").write_text("stale", encoding="utf-8")
        labels = [c[0] for c in self.ps.diff_files(self.repo)]
        self.assertIn("STANDARD.md", labels)

    def _mirror_substrate(self):
        """Give the fake target byte-identical substrate — including the executable
        bit on anything in EXECUTABLE, which is part of "correct" for a script the
        member has to be able to RUN."""
        for name in self.ps.BYTE_IDENTICAL:
            dst = self.repo / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((self.fed / name).read_bytes())
            if name in self.ps.EXECUTABLE:
                dst.chmod(dst.stat().st_mode | 0o111)

    def test_current_repo_is_noop(self):
        # Byte-identical substrate matches; with no session.config.json, settings
        # is gated out (settings_target -> None), so nothing is pending. This
        # fake repo is the markers-but-no-config shape for settings.
        self._mirror_substrate()
        self.assertEqual(self.ps.diff_files(self.repo), [])

    def test_right_bytes_wrong_mode_is_still_pending(self):
        """A pushed `poga` that landed non-executable is present-but-unrunnable: the
        capability detector tests presence and would call it fine while `./poga`
        returned "permission denied". Bytes-only comparison would call the repo up to
        date forever, so mode is part of the diff."""
        self._mirror_substrate()
        poga = self.repo / "poga"
        poga.chmod(poga.stat().st_mode & ~0o111)
        labels = [c[0] for c in self.ps.diff_files(self.repo)]
        self.assertEqual(labels, ["poga (mode)"])


class WalkTreeTest(unittest.TestCase):
    def setUp(self):
        import common
        self.common = common
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        (base / "a" / "b" / "c" / "d").mkdir(parents=True)
        (base / "repo" / ".git").mkdir(parents=True)
        (base / "repo" / "node_modules" / "dep" / "deep").mkdir(parents=True)
        (base / "repo" / "STATUS.md").write_text("x", encoding="utf-8")
        self.base = base

    def tearDown(self):
        self.tmp.cleanup()

    def test_depth_bound(self):
        dirs = [d for d, _, _ in self.common.walk_tree(self.base, 1)]
        rels = {str(pathlib.Path(d).relative_to(self.base)) for d in dirs}
        self.assertIn(".", rels)
        self.assertIn("a", rels)
        self.assertNotIn(str(pathlib.Path("a") / "b"), rels)

    def test_prunes_heavy_and_dot_dirs(self):
        walked = [d for d, _, _ in self.common.walk_tree(self.base, 10)]
        joined = " ".join(walked)
        self.assertNotIn("node_modules", joined)
        self.assertNotIn(".git", joined)

    def test_git_still_visible_in_dirnames(self):
        for d, dirnames, filenames in self.common.walk_tree(self.base, 10):
            if pathlib.Path(d) == self.base / "repo":
                self.assertIn(".git", dirnames)
                self.assertIn("STATUS.md", filenames)
                break
        else:
            self.fail("repo dir was not walked")


if __name__ == "__main__":
    unittest.main()
