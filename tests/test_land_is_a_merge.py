"""WI-0427 / ADR-0148 — a land is a merge.

THE CHANGE, in one line: the land verb checks, merges and pushes; it never runs the suite.
Validation belongs to the lane and is filed against the lane's CODE TREE (D2); a diff that
touches only `BOOKKEEPING_PATHS` needs none (D3); a lane that loses a race to the trunk
rebases and lands without re-validating (D4); the trunk verifies itself after the merge,
in the background (D5, sessionlib/trunkcheck.py). Measured before it: validate was ~98%
of every land's total, median 193 s even for a journal-only diff.

WHAT IS PINNED HERE, and each fails differently:

  A. The bookkeeping rule itself (`_is_bookkeeping_path`) — prefix for directories, exact
     for bare files, and nothing that merely LOOKS like one.
  B. The verdict key (`_code_tree`) — equal across a bookkeeping-only change, different
     across a code change, the same tree whether read from the working tree or from the
     commit, and None (never a key) when git cannot read it.
  C. The verdict store — a round trip, a fingerprint that refuses a green from another
     interpreter or another suite command, and a RED filed as red.
  D. The lander, end to end, with a SUITE-SHAPED gate: the fixture's own
     `curate/run_suite.py` is a fake that counts its runs in a file OUTSIDE the repo, so
     "the suite did not run" is a count, not an inference from timing.
  E. The trunk check's two hooks into the lander: the refusal (code lands only) and the
     spawn (once, after a landed attempt, never after `moved`).

`GateNeutralBase` lives here since the WI-0294 classifier tests it came with were retired
by ADR-0148 D6. It is imported by several sibling modules; its name is kept so they did
not all have to change at once, and it still pins `SESSION_STATE_DIR` in its own setUp,
which `test_live_store_fixture_guard` follows through the import.
"""
from __future__ import annotations

import contextlib
import io
import json
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
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402

GIT = shutil.which("git")

#: A gate that would pass if it ran — and contains no suite, so under ADR-0148 every
#: command in it is a cheap check that runs at every land.
PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]

#: A `gate-inputs.json` body for the modules that still read the record (the trunk
#: suite verdict, the nightly deriver). The land itself no longer reads it (ADR-0148 D6).
FIXTURE_RECORD = {
    "schema": session.GATE_INPUTS_SCHEMA,
    "derived_at": "2026-09-05T20:00:00+00:00",
    "derived_at_commit": "0" * 40,
    "suite": "python3 -m unittest discover -s tests",
    "tests": 2937,
    "suite_ok": True,
    "read_prefixes": ["curate/", "session.py", "tests/"],
    "read_paths_sampled": [],
    "child_audit": {"ok": True, "spawns": 0, "expected_reports": 0, "reports": 0},
}


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _out(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True,
                          text=True).stdout.strip()


class GateNeutralBase(unittest.TestCase):
    """A main repo, one linked lane worktree, and the session globals pointed at the
    lane. Every tmp path; nothing reaches the real checkout."""

    def setUp(self):
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.argv_log = self.tmp / "main-session-argv.log"
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\n", encoding="utf-8")
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        (self.main / "role.md").write_text("**Version:** v1.0.0\n", encoding="utf-8")
        (self.main / "session.py").write_text("# the code the gate reads\n", encoding="utf-8")
        (self.main / "sessions" / "journal").mkdir(parents=True)
        (self.main / "sessions" / "pre-journal-archive.md").write_text("", encoding="utf-8")
        (self.main / "work-items").mkdir()
        (self.main / "STATUS.md").write_text(
            "---\nid: test\nversion: v0\nlast_active: 2026-01-01\nfocus: x\n"
            "blocked: false\n---\n", encoding="utf-8")
        self._write_record(FIXTURE_RECORD)
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR",
                       "CONFIG_PATH", "CFG")}
        self._point_session_at_lane()
        self._install_main_compiler()

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        session._LAND_BUILD.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_record(self, record, where=None):
        target = (where or self.main) / "gate-inputs.json"
        record = dict(record, suite_tree=session._gate_suite_tree(target.parent),
                      gate_skips=0, gate_skip_ids=[])
        target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def _point_session_at_lane(self):
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.lane / "sessions" / "journal"
        session.ARCHIVE = self.lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.CONFIG_PATH = self.lane / "session.config.json"
        session.CFG = {
            "handoff": self.lane / "session-handoff.md",
            "status": self.lane / "STATUS.md",
            "role_doc": self.lane / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect", "architect_id": "test-arch",
            "machine_map": {}, "user_name": "operator", "inbox": None,
            "trunk": "main", "branch_sessions": False, "gate": PASS_GATE,
        }

    def _install_main_compiler(self):
        (self.main / "session.py").write_text(
            "import sys, pathlib\n"
            f"pathlib.Path(r'{self.argv_log}').open('a').write("
            f"' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(0) if 'stamp-status' in sys.argv else None\n"
            f"pathlib.Path(__file__).parent.joinpath('session-handoff.md')"
            f".write_text('RECOMPILED\\n')\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "main compiler")
        # The lane must carry the trunk's compiler commit or its land is a rebase, not
        # the pure fast-forward these tests are about.
        _git(self.lane, "rebase", "main")

    # ---- lane work shapes -------------------------------------------------

    def _commit_in_lane(self, rel: str, body: str, msg: str) -> None:
        p = self.lane / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", msg)

    def _journal_only_lane(self) -> None:
        """The common case: the lane's whole diff is one session journal."""
        self._commit_in_lane(
            "sessions/journal/20260905T2300Z-devbox-aaaa.md",
            "---\nsession-id: 20260905T2300Z-devbox-aaaa\nended: 2026-09-05T23:30:00+00:00\n"
            "---\n\n### Outcome\n\nA journal-only close-out.\n",
            "docs(journal): close a session")

    def _code_carrying_lane(self) -> None:
        self._commit_in_lane("session.py", "# edited code\n", "fix(session): a real change")

    # ---- the land, with the gate spied on ---------------------------------

    def _land_watching_the_gate(self):
        """Land the lane; return (outcome, gate_call_count, printed output).

        `gate_call_count` counts `_run_gate` calls — each one is a gate run over the
        candidate, which under ADR-0148 means the CHEAP CHECKS (the suite command is
        skipped inside it). A suite run is counted separately by `SuiteGateBase`."""
        real = session._run_gate
        calls = []

        def spy(cwd=None, **kwargs):
            calls.append(cwd)
            return real(cwd=cwd, **kwargs)

        buf = io.StringIO()
        with mock.patch.object(session, "_run_gate", side_effect=spy):
            with contextlib.redirect_stdout(buf):
                outcome = session._land_worktree_lane(None, None, "1.0.0", push=False)
        return outcome, len(calls), buf.getvalue()

    def _trunk_tip(self) -> str:
        return _out(self.main, "rev-parse", "main")


class SuiteGateBase(GateNeutralBase):
    """GateNeutralBase with a SUITE-SHAPED gate: the real runner's name, a fake body.

    `curate/run_suite.py` in the fixture repo appends its cwd to a counter file in the
    tmpdir (outside every worktree, so no land carries it) and prints a unittest-shaped
    summary on stderr — red when a `SUITE_RED` file is in the tree it runs over. The
    second gate command is a cheap check that counts itself the same way."""

    def setUp(self):
        super().setUp()
        self.suite_log = self.tmp / "suite-runs.log"
        self.check_log = self.tmp / "check-runs.log"
        runner = (
            "import os, sys, pathlib\n"
            f"open(r'{self.suite_log}', 'a').write(os.getcwd() + '\\n')\n"
            "if pathlib.Path('SUITE_RED').exists():\n"
            "    sys.stderr.write('FAIL: test_x (test_fake.T)\\n'\n"
            "                     '----------------------------------------------------'\n"
            "                     '------------------\\nRan 1 test in 0.001s\\n\\n'\n"
            "                     'FAILED (failures=1)\\n')\n"
            "    sys.exit(1)\n"
            "sys.stderr.write('Ran 1 test in 0.001s\\n\\nOK\\n')\n")
        p = self.main / "curate" / "run_suite.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(runner, encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "fake suite runner")
        _git(self.lane, "rebase", "-q", "main")
        self.check_cmd = ["python3", "-c",
                          f"open(r'{self.check_log}', 'a').write('c\\n')"]
        session.CFG = dict(session.CFG, gate=[["python3", "curate/run_suite.py"],
                                              self.check_cmd])
        session._clear_ready_to_land()
        self.addCleanup(session._clear_ready_to_land)

    def suite_runs(self) -> list:
        try:
            return self.suite_log.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def check_runs(self) -> int:
        try:
            return len(self.check_log.read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def land(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=False)
        return outcome, buf.getvalue()

    def landed_receipt(self) -> dict:
        recs = [r for r in session.land_receipts() if r.get("outcome") == "landed"]
        self.assertTrue(recs, "no landed receipt was written")
        return recs[-1]

    def file_green_verdict(self) -> dict:
        """What `poga test` does after a green sharded run, from inside the lane."""
        rec = session.record_verdict(session._code_tree(None), True, "test", 1.0)
        self.assertIsNotNone(rec, "control: the verdict must have been filed")
        return rec

    def commit_on_trunk(self, rel: str, body: str, msg: str) -> None:
        p = self.main / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", msg)


# ── A. the bookkeeping rule ──────────────────────────────────────────────────────


class TheBookkeepingRuleTest(unittest.TestCase):

    def test_the_table(self):
        cases = {
            "sessions/journal/x.md": True,
            "work-items/WI-0001-x.md": True,
            "ops-items/OPS-0001.md": True,
            "proposed-edits/a/b.md": True,
            "comms/inbox/x.md": True,
            "outbox/x.md": True,
            "releases/v1.md": True,
            "ROADMAP.md": True,
            "STATUS.md": True,
            "session-handoff.md": True,
            "gate-inputs.json": True,
            "./work-items/x": True,            # normalised
            " STATUS.md ": True,               # stripped
            "comms": False,                    # the directory NAME without its slash
            "commsx/y.md": False,
            "curate/work-items/x": False,      # nested — not the store
            "tests/sessions/x.py": False,
            "STATUS.md.bak": False,            # a bare file matches exactly
            "docs/ROADMAP.md": False,
            "session.py": False,
            "tests/test_new.py": False,
            "": False,
            "./": False,
        }
        for rel, want in cases.items():
            with self.subTest(rel=rel):
                self.assertIs(session._is_bookkeeping_path(rel), want)

    def test_every_declared_entry_is_itself_bookkeeping(self):
        """Guard against a declared entry the predicate cannot match (an exemption that
        cannot match reads as a working one)."""
        for p in session.BOOKKEEPING_PATHS:
            probe = p + "x.md" if p.endswith("/") else p
            with self.subTest(entry=p):
                self.assertTrue(session._is_bookkeeping_path(probe))


# ── B. the verdict key ───────────────────────────────────────────────────────────


class TheCodeTreeTest(GateNeutralBase):

    def test_a_bookkeeping_only_change_leaves_it_equal(self):
        before = session._code_tree("HEAD")
        self._journal_only_lane()
        self._commit_in_lane("work-items/WI-0009-x.md", "x\n", "chore: item")
        self._commit_in_lane("STATUS.md", "changed\n", "chore: status")
        self.assertIsNotNone(before)
        self.assertEqual(session._code_tree("HEAD"), before)

    def test_editing_an_existing_bookkeeping_file_leaves_it_equal(self):
        """The common bookkeeping shape is an EDIT (a work item's status, STATUS.md),
        not only a new journal. Read from the lane, the trunk's copy of that file differs
        from both the lane's HEAD and its working file, and a plain `git rm --cached`
        refuses the WHOLE call ("staged content different from both the file and the
        HEAD") — leaving the trunk's tree unfiltered, so the diff reads as code."""
        self._commit_in_lane("STATUS.md", "edited status\n", "chore: status")
        self.assertEqual(session._code_tree("HEAD"), session._code_tree("main"),
                         "_code_tree(<trunk>) kept the bookkeeping paths: its `git rm "
                         "--cached` needs -f when the lane's copy differs")

    def test_a_code_change_alters_it(self):
        before = session._code_tree("HEAD")
        self._code_carrying_lane()
        self.assertNotEqual(session._code_tree("HEAD"), before)

    def test_a_nested_store_shaped_path_is_code(self):
        before = session._code_tree("HEAD")
        self._commit_in_lane("curate/work-items/x.md", "x\n", "code that looks like a store")
        self.assertNotEqual(session._code_tree("HEAD"), before)

    def test_the_working_tree_form_equals_the_commit_form_after_committing(self):
        (self.lane / "session.py").write_text("# uncommitted edit\n", encoding="utf-8")
        (self.lane / "sessions" / "journal").mkdir(parents=True, exist_ok=True)
        (self.lane / "sessions" / "journal" / "j.md").write_text("j\n", encoding="utf-8")
        working = session._code_tree(None)
        self.assertNotEqual(working, session._code_tree("HEAD"),
                            "control: the uncommitted edit must count")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "commit it")
        self.assertEqual(session._code_tree("HEAD"), working)
        self.assertEqual(session._code_tree(None), working)

    def test_untracked_non_ignored_files_count_and_ignored_do_not(self):
        base = session._code_tree(None)
        (self.lane / ".session-state").mkdir(exist_ok=True)
        (self.lane / ".session-state" / "x.live").write_text("x\n", encoding="utf-8")
        self.assertEqual(session._code_tree(None), base, "an ignored file moved the key")
        (self.lane / "new_module.py").write_text("x = 1\n", encoding="utf-8")
        self.assertNotEqual(session._code_tree(None), base,
                            "an untracked file the suite would import did not count")

    def test_the_real_index_is_not_disturbed(self):
        (self.lane / "new_module.py").write_text("x = 1\n", encoding="utf-8")
        session._code_tree(None)
        self.assertEqual(_out(self.lane, "diff", "--cached", "--name-only"), "",
                         "computing the key staged something in the lane's own index")

    def test_an_unreadable_commit_is_none_never_a_key(self):
        self.assertIsNone(session._code_tree("0" * 40))
        self.assertIsNone(session._code_tree("no-such-ref"))

    def test_an_unreadable_side_needs_a_verdict(self):
        """Fail closed: "I could not read it" is a code change, never bookkeeping.
        (Ported from the retired classifier's unreadable-diff test.)"""
        tree = session._code_tree("HEAD")
        self.assertTrue(session._lane_needs_verdict(None, tree))
        self.assertTrue(session._lane_needs_verdict("0" * 40, tree))
        self.assertTrue(session._lane_needs_verdict(_out(self.lane, "rev-parse", "HEAD"),
                                                    None))
        self.assertFalse(session._lane_needs_verdict(
            _out(self.lane, "rev-parse", "HEAD"), tree))

    def test_the_land_gate_never_calls_an_unreadable_parent_neutral(self):
        self._journal_only_lane()
        tip = _out(self.lane, "rev-parse", "HEAD")
        ok, report, info = session._land_gate("0" * 40, tip,
                                              lambda skip: (True, "ran"), {})
        self.assertEqual(info["diff_class"], "code")
        self.assertIsNone(info["gate_neutral"])


# ── C. the verdict store ─────────────────────────────────────────────────────────


class TheVerdictStoreTest(GateNeutralBase):

    def setUp(self):
        super().setUp()
        session.CFG = dict(session.CFG, gate=[["python3", "curate/run_suite.py"],
                                              ["python3", "-c", "pass"]])
        self.tree = session._code_tree(None)

    def test_a_round_trip(self):
        rec = session.record_verdict(self.tree, True, "test", 2.5, "3 test(s), OK")
        got = session.lookup_verdict(self.tree)
        self.assertEqual(got, rec)
        self.assertTrue(got["green"])
        self.assertEqual(got["source"], "test")
        self.assertEqual(got["suite"], session._suite_fingerprint())

    def test_it_lives_in_the_common_dir_not_the_tree(self):
        session.record_verdict(self.tree, True, "test", 1.0)
        common = pathlib.Path(_out(self.lane, "rev-parse", "--path-format=absolute",
                                   "--git-common-dir"))
        self.assertTrue((common / session.LAND_VERDICTS_DIRNAME / f"{self.tree}.json")
                        .is_file())
        self.assertEqual(_out(self.lane, "status", "--porcelain"), "")

    def test_another_interpreter_is_a_different_claim(self):
        session.record_verdict(self.tree, True, "test", 1.0)
        with mock.patch.object(sys, "executable", "/some/other/python3"):
            self.assertIsNone(session.lookup_verdict(self.tree))
        self.assertIsNotNone(session.lookup_verdict(self.tree), "control")

    def test_another_suite_command_is_a_different_claim(self):
        session.record_verdict(self.tree, True, "test", 1.0)
        session.CFG = dict(session.CFG, gate=[["python3", "curate/run_suite.py", "-j", "2"]])
        self.assertIsNone(session.lookup_verdict(self.tree))

    def test_a_cheap_check_change_does_not_orphan_the_verdict(self):
        """The fingerprint is the SUITE's; a cheap check added to the gate runs at every
        land anyway and must not throw away every verdict the session earned."""
        session.record_verdict(self.tree, True, "test", 1.0)
        session.CFG = dict(session.CFG, gate=list(session.CFG["gate"])
                           + [["python3", "session.py", "wi-check"]])
        self.assertIsNotNone(session.lookup_verdict(self.tree))

    def test_a_red_verdict_is_filed_as_red(self):
        session.record_verdict(self.tree, False, "built-at-land", 1.0, "FAILED")
        got = session.lookup_verdict(self.tree)
        self.assertIsNotNone(got)
        self.assertIs(got["green"], False)

    def test_no_tree_files_nothing(self):
        self.assertIsNone(session.record_verdict(None, True, "test", 1.0))
        self.assertIsNone(session.lookup_verdict(None))

    def test_an_unreadable_verdict_is_no_verdict(self):
        session.record_verdict(self.tree, True, "test", 1.0)
        common = pathlib.Path(_out(self.lane, "rev-parse", "--path-format=absolute",
                                   "--git-common-dir"))
        (common / session.LAND_VERDICTS_DIRNAME / f"{self.tree}.json").write_text(
            "{not json", encoding="utf-8")
        self.assertIsNone(session.lookup_verdict(self.tree))


class WhichCommandsAreTheSuiteTest(unittest.TestCase):
    """Ported from the retired per-command classifier: each command is still judged on
    its own, now by one declared rule — the suite commands skip at land, every other
    command runs."""

    def setUp(self):
        saved = session.CFG
        self.addCleanup(setattr, session, "CFG", saved)

    def _with_gate(self, gate):
        session.CFG = dict(session.CFG or {}, gate=gate)

    def test_the_sharded_runner_and_a_unittest_module_are_the_suite(self):
        self._with_gate(["python3 curate/run_suite.py", "python3 -m unittest discover -s tests",
                         "python3 session.py wi-check", ["python3", "curate/check_citations.py"]])
        self.assertEqual(session._land_suite_keys(), frozenset({
            "python3 curate/run_suite.py", "python3 -m unittest discover -s tests"}))

    def test_a_gate_with_no_suite_has_no_suite_keys(self):
        self._with_gate(["python3 session.py wi-check"])
        self.assertEqual(session._land_suite_keys(), frozenset())

    def test_a_dash_m_other_module_is_not_the_suite(self):
        self._with_gate(["python3 -m json.tool x.json"])
        self.assertEqual(session._land_suite_keys(), frozenset())


# ── D. the lander, end to end ────────────────────────────────────────────────────


class ABookkeepingLaneTest(SuiteGateBase):

    def test_it_lands_without_the_suite_and_says_why(self):
        self._journal_only_lane()
        before = self._trunk_tip()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertNotEqual(before, self._trunk_tip())
        self.assertEqual(self.suite_runs(), [], "the suite ran for a bookkeeping diff:\n" + out)
        self.assertEqual(self.check_runs(), 1, "the cheap checks must still run")
        self.assertIn("suite not run", out)
        self.assertIn("bookkeeping", out)
        rec = self.landed_receipt()
        self.assertEqual(rec["diff_class"], "bookkeeping")
        self.assertEqual(rec["validate_seconds"], 0)
        self.assertEqual(rec["phases"]["validate"], 0)
        self.assertGreater(rec["checks_seconds"], 0)
        self.assertTrue(rec["gate_neutral"])
        self.assertEqual(rec["verdict"]["source"], "neutral")
        self.assertNotIn("build_seconds", rec)

    def test_every_bookkeeping_path_together_is_still_bookkeeping(self):
        self._journal_only_lane()
        self._commit_in_lane("work-items/WI-0001-x.md", "x\n", "chore: item")
        self._commit_in_lane("comms/inbox/m.md", "m\n", "chore: mail")
        self._commit_in_lane("gate-inputs.json", "{not json", "chore: record")
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(self.suite_runs(), [])
        self.assertEqual(self.landed_receipt()["diff_class"], "bookkeeping")

    def test_the_gate_input_record_is_no_longer_an_input(self):
        """Ported from the retired "no measurement means gate" class. The classifier no
        longer reads `gate-inputs.json`, so an absent record changes nothing — and the
        land does not rewrite it either (the ADR-0124 D3 half that survives)."""
        (self.main / "gate-inputs.json").unlink()
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "drop the record")
        _git(self.lane, "rebase", "-q", "main")
        self._journal_only_lane()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(self.suite_runs(), [])
        self.assertFalse((self.main / "gate-inputs.json").exists())


class ACodeLaneTest(SuiteGateBase):

    def test_a_green_verdict_for_its_tree_lands_it_without_the_suite(self):
        self._code_carrying_lane()
        self.file_green_verdict()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(self.suite_runs(), [], out)
        self.assertEqual(self.check_runs(), 1)
        rec = self.landed_receipt()
        self.assertEqual(rec["diff_class"], "code")
        self.assertEqual(rec["verdict"]["source"], "lane-verdict")
        self.assertEqual(rec["validate_seconds"], 0)
        self.assertIn("suite runs at land: 0", out)

    def test_an_uncommitted_edit_is_covered_by_the_verdict_it_was_tested_with(self):
        """`poga test` runs over the WORKING tree; the land then commits it. The key
        must be the same tree on both sides, or every verdict is orphaned."""
        (self.lane / "session.py").write_text("# edited, not committed\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "wip")
        (self.lane / "sessions" / "journal").mkdir(parents=True, exist_ok=True)
        (self.lane / "sessions" / "journal" / "close.md").write_text(
            "closing\n", encoding="utf-8")         # the land's close commit adds this
        self.file_green_verdict()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(self.suite_runs(), [], out)

    def test_no_verdict_runs_the_suite_once_before_the_clock(self):
        self._code_carrying_lane()
        at_mark = []
        real_mark = session.mark_ready_to_land

        def mark(*a, **k):
            at_mark.append(len(self.suite_runs()))
            return real_mark(*a, **k)

        with mock.patch.object(session, "mark_ready_to_land", side_effect=mark):
            outcome, out = self.land()
        self.assertTrue(outcome, out)
        runs = self.suite_runs()
        self.assertEqual(len(runs), 1, out)
        self.assertEqual(pathlib.Path(runs[0]).resolve(), self.lane.resolve(),
                         "the build must run in the lane's own tree")
        self.assertEqual(at_mark, [1], "the suite must have finished BEFORE the clock")
        rec = self.landed_receipt()
        self.assertGreater(rec["build_seconds"], 0)
        self.assertEqual(rec["phases"]["validate"], 0)
        self.assertEqual(rec["validate_seconds"], 0)
        self.assertEqual(rec["verdict"]["source"], "built-at-land")
        # And it was filed, so the next land of this tree needs none.
        filed = session.lookup_verdict(session._code_tree("HEAD"))
        self.assertTrue(filed and filed["green"])
        self.assertEqual(filed["source"], "built-at-land")

    def test_a_file_inside_tests_is_code(self):
        """Ported: the grain is the directory. A new test file needs a verdict, and the
        land derives nothing (ADR-0124 D3)."""
        self._commit_in_lane("tests/test_new.py", "# a new test\n", "test: add one")
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(len(self.suite_runs()), 1, out)
        self.assertEqual(self.landed_receipt()["diff_class"], "code")
        self.assertNotIn("derive exit", out)

    def test_a_mixed_diff_is_code(self):
        """Ported: EVERY path must be bookkeeping, not any."""
        self._journal_only_lane()
        self._code_carrying_lane()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(len(self.suite_runs()), 1, out)
        self.assertEqual(self.landed_receipt()["diff_class"], "code")

    def test_a_red_suite_refuses_and_lands_nothing(self):
        self._commit_in_lane("SUITE_RED", "x\n", "break the suite")
        before = self._trunk_tip()
        outcome, out = self.land()
        self.assertFalse(outcome, out)
        self.assertEqual(outcome.reason, "gate")
        self.assertEqual(self._trunk_tip(), before, "a red lane moved the trunk")
        self.assertEqual(len(self.suite_runs()), 1)
        self.assertEqual(self.check_runs(), 0, "nothing past the verdict should have run")
        self.assertIn("test_x", out, "the failing test must be named")
        filed = session.lookup_verdict(session._code_tree(None))
        self.assertIsNotNone(filed)
        self.assertIs(filed["green"], False)
        self.assertEqual([r for r in session.land_receipts()
                          if r.get("outcome") == "landed"], [])

    def test_a_filed_red_verdict_is_not_a_licence(self):
        self._code_carrying_lane()
        session.record_verdict(session._code_tree(None), False, "test", 1.0)
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(len(self.suite_runs()), 1, "a red verdict was trusted as green")


class AMovedTrunkLandsWithoutRevalidatingTest(SuiteGateBase):
    """Acceptance 3 / ADR-0148 D4. The trunk advances under the first attempt with a
    CODE change, so the rebased tree is a combination no suite has run — and it still
    lands on the clean rebase alone. What could break only in combination is the trunk
    check's to catch."""

    def _land_racing(self):
        self._code_carrying_lane()
        self.file_green_verdict()
        real = session._run_gate
        runs = {"n": 0}

        def racing_gate(cwd=None, **kwargs):
            ok, rep = real(cwd=cwd, **kwargs)
            runs["n"] += 1
            if runs["n"] == 1:
                self.commit_on_trunk("other_module.py", "y = 2\n", "feat: a sibling lands")
            return ok, rep

        spawned = []
        with mock.patch.object(session, "_run_gate", side_effect=racing_gate), \
                mock.patch.object(session, "_trunk_check_spawn",
                                  side_effect=lambda trunk, tip: (
                                      spawned.append(tip) or {"spawned": True, "pid": 4242})):
            outcome, out = self.land()
        return outcome, out, spawned

    def test_it_lands_on_attempt_two_with_no_suite_run(self):
        outcome, out, _ = self._land_racing()
        self.assertTrue(outcome, out)
        self.assertEqual(self.suite_runs(), [], "a lost race bought a suite run:\n" + out)
        self.assertEqual(self.check_runs(), 2, "the cheap checks re-run per attempt")
        receipts = session.land_receipts()
        moved = [r for r in receipts if r["outcome"] == "moved"]
        landed = [r for r in receipts if r["outcome"] == "landed"]
        self.assertEqual(len(moved), 1, receipts)
        self.assertEqual(len(landed), 1, receipts)
        rec = landed[0]
        self.assertEqual(rec["attempts"], 2)
        self.assertEqual(rec["validate_seconds"], 0)
        self.assertTrue(rec["verdict"]["rebased"])
        self.assertIn("moved", rec["verdict"]["reason"])
        self.assertIn("ADR-0148 D4", rec["verdict"]["reason"])
        self.assertTrue((self.main / "other_module.py").exists())

    def test_the_trunk_check_is_spawned_once_after_the_landed_attempt(self):
        outcome, out, spawned = self._land_racing()
        self.assertTrue(outcome, out)
        self.assertEqual(spawned, [self._trunk_tip()],
                         "spawned after `moved`, or not after the land")
        rec = [r for r in session.land_receipts() if r["outcome"] == "landed"][-1]
        self.assertEqual(rec["trunk_check"], {"spawned": True, "pid": 4242})
        moved = [r for r in session.land_receipts() if r["outcome"] == "moved"][-1]
        self.assertIsNone(moved.get("trunk_check"))


class ARedTrunkRefusesCodeLandsOnlyTest(SuiteGateBase):

    REFUSAL = "trunk check: main is RED at abc — this code land is refused (ADR-0148 D5)"

    def _refusing(self):
        calls = []

        def refusal(code_change, lane_head=None):
            calls.append(code_change)
            return self.REFUSAL if code_change else ""
        return calls, mock.patch.object(session, "_trunk_check_refusal", side_effect=refusal)

    def test_a_code_lane_is_refused_as_trunk_red(self):
        self._code_carrying_lane()
        self.file_green_verdict()
        before = self._trunk_tip()
        calls, patch = self._refusing()
        with patch:
            outcome, out = self.land()
        self.assertFalse(outcome, out)
        self.assertEqual(outcome.reason, "trunk-red")
        self.assertEqual(self._trunk_tip(), before)
        self.assertEqual(calls, [True])
        self.assertIn(self.REFUSAL, out)
        refused = [r for r in session.land_receipts() if r["outcome"] == "refused"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["refused"], self.REFUSAL)
        self.assertEqual(self.check_runs(), 0, "a refused land ran its checks anyway")

    def test_a_bookkeeping_lane_still_lands(self):
        self._journal_only_lane()
        calls, patch = self._refusing()
        with patch:
            outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(calls, [False])

    def test_the_lane_head_is_passed_so_a_fix_built_on_the_red_tip_can_land(self):
        self._code_carrying_lane()
        self.file_green_verdict()
        head = _out(self.lane, "rev-parse", "HEAD")
        seen = []
        with mock.patch.object(session, "_trunk_check_refusal",
                               side_effect=lambda c, h=None: seen.append(h) or ""):
            outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(seen, [head])

    def test_the_trunk_check_line_precedes_the_gate_report(self):
        self._journal_only_lane()
        outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertIn("trunk check:", out)
        self.assertLess(out.index("trunk check:"), out.index("gate:"))


class AGateWithNoSuiteTest(SuiteGateBase):
    """A member whose gate is only cheap checks keeps the old contract: every command
    runs at every attempt, because there is nothing to skip."""

    def setUp(self):
        super().setUp()
        session.CFG = dict(session.CFG, gate=[self.check_cmd])

    def test_the_source_is_no_suite_and_checks_run_every_attempt(self):
        self._code_carrying_lane()
        real = session._run_gate
        runs = {"n": 0}

        def racing_gate(cwd=None, **kwargs):
            ok, rep = real(cwd=cwd, **kwargs)
            runs["n"] += 1
            if runs["n"] == 1:
                self.commit_on_trunk("work-items/WI-9991-note.md", "c\n", "chore: note")
            return ok, rep

        with mock.patch.object(session, "_run_gate", side_effect=racing_gate):
            outcome, out = self.land()
        self.assertTrue(outcome, out)
        self.assertEqual(self.check_runs(), 2)
        self.assertEqual(self.suite_runs(), [])
        rec = self.landed_receipt()
        self.assertEqual(rec["verdict"]["source"], "no-suite")
        self.assertIn("no suite configured", out)


class TheRunnerSkipsOnlyWhatItIsToldTest(unittest.TestCase):
    """`_run_gate(skip=...)` — kept from the retired per-command file, because the land
    still drives the runner through it (with the suite keys)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        saved = session.CFG
        self.addCleanup(setattr, session, "CFG", saved)
        self.a, self.b = self.tmp / "ran-a", self.tmp / "ran-b"
        self.cmd_a = f"python3 -c open(r'{self.a}','w').close()"
        self.cmd_b = f"python3 -c open(r'{self.b}','w').close()"
        session.CFG = dict(saved or {}, gate=[self.cmd_a, self.cmd_b])

    def test_a_skipped_command_is_not_executed_and_is_named(self):
        ok, report = session._run_gate(cwd=self.tmp, skip=frozenset({self.cmd_b}))
        self.assertTrue(ok, report)
        self.assertTrue(self.a.exists())
        self.assertFalse(self.b.exists(), report)
        self.assertIn("skipped", report)
        self.assertIn(self.cmd_b, report)

    def test_the_default_skips_nothing(self):
        ok, report = session._run_gate(cwd=self.tmp)
        self.assertTrue(ok, report)
        self.assertTrue(self.a.exists() and self.b.exists(), report)

    def test_a_skip_cannot_turn_a_red_gate_green(self):
        red = "python3 -c import sys; sys.exit(3)"
        session.CFG = dict(session.CFG, gate=[red, "python3 -c pass"])
        ok, report = session._run_gate(cwd=self.tmp, skip=frozenset({"python3 -c pass"}))
        self.assertFalse(ok, report)

    def test_a_skipped_suite_says_it_never_runs_at_land(self):
        suite = "python3 curate/run_suite.py"
        session.CFG = dict(session.CFG, gate=[suite, self.cmd_a])
        ok, report = session._run_gate(cwd=self.tmp, skip=session._land_suite_keys())
        self.assertTrue(ok, report)
        self.assertIn("ADR-0148 D1", report)
        self.assertTrue(self.a.exists())


class TheOldClassifierStaysDeletedTest(unittest.TestCase):
    """ADR-0148 D6: deleted, not left unreachable. A resurrected classifier would be a
    second rule for "may this land skip the suite", and two rules disagree silently."""

    def test_none_of_them_is_back(self):
        for name in ("_gate_unless_neutral", "_classify_gate_neutral", "_gate_skip_plan",
                     "_gate_record_freshness", "_gate_command_prefixes",
                     "_gate_skip_receipt"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(session, name))


if __name__ == "__main__":
    unittest.main()
