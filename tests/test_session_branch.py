"""Tests for the session branch → gated trunk flow (ADR-0056, concurrency Phase 2 / C3).

Drives the real git-side-effecting helpers — branch cut, the merge gate, and the
land flow — against throwaway git repos, plus one end-to-end `cmd_end` that closes a
journal on a session branch and lands it. Skipped where `git` is unavailable.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from coord_fixture import (neutralize_dispatch_env,  # noqa: E402
                           neutralize_live_store)

GIT = shutil.which("git")

PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]
FAIL_GATE = [["python3", "-c", "import sys; sys.exit(1)"]]
UNLAUNCHABLE_GATE = [["this-binary-does-not-exist-42"]]


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _head_files(repo):
    return _head_files_on(repo, "HEAD")


def _head_files_on(repo, ref):
    r = subprocess.run([GIT, "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
                       capture_output=True, text=True)
    return set(r.stdout.split())


@unittest.skipUnless(GIT, "git not available")
class BranchGateBase(unittest.TestCase):
    def setUp(self):
        # WI-0249 — see `neutralize_dispatch_env`: `cmd_end` can now end a lane's
        # runtime, and the land gate runs this suite from inside one.
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "role.md").write_text("**Version:** v1.0.0\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "branch", "-M", "main")

        (self.repo / "sessions" / "journal").mkdir(parents=True)
        (self.repo / "sessions" / "pre-journal-archive.md").write_text("", encoding="utf-8")
        (self.repo / "STATUS.md").write_text(
            "---\nid: test\nversion: v0\nlast_active: 2026-01-01\nfocus: x\nblocked: false\n---\n",
            encoding="utf-8")

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR", "CONFIG_PATH", "CFG")}
        session.ROOT = self.repo
        # WI-0295: JOURNAL_DIR below is not the only journal directory the land path
        # reads. `_holder_journal_dirs` also walks every lane under
        # `_shared_work_root()`, which resolves to the operator's real repo through the
        # git common dir. Measured: 1,127 reads of the live store from this module.
        neutralize_live_store(self, self.repo)
        session.JOURNAL_DIR = self.repo / "sessions" / "journal"
        session.ARCHIVE = self.repo / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = self.repo / ".session-state"
        session.CONFIG_PATH = self.repo / "session.config.json"
        session.CFG = {
            "handoff": self.repo / "session-handoff.md",
            "status": self.repo / "STATUS.md",
            "role_doc": self.repo / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect",
            "architect_id": "test-arch",
            "machine_map": {},
            "user_name": "operator",
            "inbox": None,
            "trunk": "main",
            "branch_sessions": True,
            "gate": PASS_GATE,
        }

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -------------------------------------------------------------
    def _current(self):
        return subprocess.run([GIT, "-C", str(self.repo), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def _write_journal(self, did, ended=""):
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": "(in progress)",
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": session._base_commit(),
            "started": "2026-07-19T20:00:00+00:00", "ended": ended,
            "claude-session-id": "csid-" + did,
        })

    def _stage_session_branch(self, did, workfile="work.txt", conflict_trunk=False):
        """Cut a branch, drop a journal + a work file, commit them on the branch."""
        session._cut_session_branch(did)
        self.assertEqual(self._current(), f"session/{did}")
        self._write_journal(did)
        (self.repo / workfile).write_text("branch work\n", encoding="utf-8")
        if conflict_trunk:
            (self.repo / "trunkfile.txt").write_text("branch edit\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "branch work")


class CutBranchTest(BranchGateBase):
    def test_cut_from_trunk(self):
        b = session._cut_session_branch("20260719T2000Z-runner-aaaa")
        self.assertEqual(b, "session/20260719T2000Z-runner-aaaa")
        self.assertEqual(self._current(), "session/20260719T2000Z-runner-aaaa")
        self.assertTrue(session._on_session_branch())

    def test_cut_idempotent(self):
        did = "20260719T2000Z-runner-bbbb"
        session._cut_session_branch(did)
        again = session._cut_session_branch(did)          # already on it
        self.assertEqual(again, f"session/{did}")
        self.assertEqual(self._current(), f"session/{did}")

    def test_disabled_stays_on_trunk(self):
        session.CFG["branch_sessions"] = False
        b = session._cut_session_branch("20260719T2000Z-runner-cccc")
        self.assertIsNone(b)
        self.assertEqual(self._current(), "main")
        self.assertFalse(session._on_session_branch())

    def test_non_trunk_non_session_branch_not_hijacked(self):
        _git(self.repo, "checkout", "-q", "-b", "feature")
        b = session._cut_session_branch("20260719T2000Z-runner-dddd")
        self.assertIsNone(b)            # a hand-cut feature branch is left alone
        self.assertEqual(self._current(), "feature")


class ConfigDefaultTest(BranchGateBase):
    """Pin the LOADER default for branch_sessions (ADR-0058: default OFF). This is
    the guard against the exact drift that shipped in v2.37.0 — the guard comment and
    the ADR said "default off" while `_load_config`'s default stayed True, so every
    session silently branched and took the legacy land path. A comment is not a
    default; this test is."""

    _MINIMAL_CFG = (
        '{"handoff": "session-handoff.md", "role_doc": "role.md", '
        '"timezone": "UTC", "machine_map": {}, '
        '"architect_name": "Test", "architect_id": "test-arch"'
    )

    def test_absent_key_defaults_off(self):
        session.CONFIG_PATH.write_text(self._MINIMAL_CFG + "}", encoding="utf-8")
        cfg = session._load_config()
        self.assertFalse(cfg["branch_sessions"])   # ADR-0058: immediate-land is the default

    def test_explicit_opt_in_honoured(self):
        session.CONFIG_PATH.write_text(
            self._MINIMAL_CFG + ', "branch_sessions": true}', encoding="utf-8")
        cfg = session._load_config()
        self.assertTrue(cfg["branch_sessions"])     # opt-in for the worktree-lane world still works


class CompileGuardTest(BranchGateBase):
    def test_compile_noops_on_session_branch(self):
        session._cut_session_branch("20260719T2000Z-runner-eeee")
        self._write_journal("20260719T2000Z-runner-eeee")
        session.run_compile(session.CFG["tz"])
        self.assertFalse(session.CFG["handoff"].exists(),
                         "handoff must NOT be written while on a session branch (D2)")

    def test_compile_writes_on_trunk(self):
        self._write_journal("20260719T2000Z-runner-ffff")
        session.run_compile(session.CFG["tz"])
        self.assertTrue(session.CFG["handoff"].exists())


class GateTest(BranchGateBase):
    def test_pass(self):
        session.CFG["gate"] = PASS_GATE
        ok, _ = session._run_gate()
        self.assertTrue(ok)

    def test_fail_closed(self):
        session.CFG["gate"] = FAIL_GATE
        ok, report = session._run_gate()
        self.assertFalse(ok)
        self.assertIn("FAIL", report)

    def test_unlaunchable_fails_closed(self):
        session.CFG["gate"] = UNLAUNCHABLE_GATE
        ok, report = session._run_gate()
        self.assertFalse(ok, "a gate command that cannot launch must block, not pass")

    def test_empty_gate_passes_vacuously(self):
        session.CFG["gate"] = []
        ok, _ = session._run_gate()
        self.assertTrue(ok)


class LandTest(BranchGateBase):
    def test_branch_land_gates_candidate_despite_late_live_edit(self):
        """WI-0311: the fifth land door must test its snapshot, not late dirt."""
        self._stage_session_branch("20260719T2000Z-runner-1112")
        observed = self.tmp / "gate-observed.json"
        session.CFG["gate"] = [[sys.executable, "-c",
            "import json, pathlib, sys; "
            "value = pathlib.Path('trunkfile.txt').read_text(); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps("
            "{'cwd': str(pathlib.Path.cwd()), 'value': value})); "
            "sys.exit(0 if value == 'base\\n' else 1)", str(observed)]]
        real_sh = session.sh
        real_gate = session._run_gate
        dirtied = []

        def late_writer(argv, **kwargs):
            result = real_sh(argv, **kwargs)
            if argv[:2] == ["git", "rebase"] and result.returncode == 0:
                # The committed candidate is now fixed. A concurrent writer changes
                # only the live file, after Git's clean-tree precondition has passed.
                (self.repo / "trunkfile.txt").write_text("late live edit\n")
                dirtied.append(True)
            return result

        def gate(cwd=None, skip=frozenset()):
            try:
                self.assertEqual((self.repo / "trunkfile.txt").read_text(),
                                 "late live edit\n")
                return real_gate(cwd=cwd, skip=skip)
            finally:
                # The writer removes its edit only AFTER the real gate finishes,
                # allowing the remainder of this real land to complete cleanly.
                (self.repo / "trunkfile.txt").write_text("base\n")

        receipt = io.StringIO()
        with mock.patch.object(session, "sh", side_effect=late_writer), \
                mock.patch.object(session, "_run_gate", side_effect=gate), \
                contextlib.redirect_stdout(receipt):
            landed = session._land_branch(None, None, "1.0.0", push=False)
        record = json.loads(observed.read_text())
        self.assertEqual(dirtied, [True])
        self.assertEqual(record["value"], "base\n", record)
        self.assertNotEqual(pathlib.Path(record["cwd"]).resolve(), self.repo.resolve())
        self.assertIn("gated in: isolated " + record["cwd"], receipt.getvalue())
        self.assertTrue(landed, receipt.getvalue())
        self.assertEqual(self._current(), "main")
        self.assertIn("work.txt", _head_files_on(self.repo, "main"))

    def test_happy_path_lands_and_deletes_branch(self):
        did = "20260719T2000Z-runner-1111"
        self._stage_session_branch(did)
        landed = session._land_branch(None, None, "1.0.0", push=False)
        self.assertTrue(landed)
        self.assertEqual(self._current(), "main")
        self.assertIn("work.txt", _head_files(self.repo))
        self.assertIn(f"sessions/journal/{did}.md", _head_files(self.repo))
        self.assertTrue(session.CFG["handoff"].exists(), "views compiled on trunk after land")
        branches = subprocess.run([GIT, "-C", str(self.repo), "branch"],
                                  capture_output=True, text=True).stdout
        self.assertNotIn(f"session/{did}", branches, "merged branch deleted")

    def test_gate_failure_blocks_and_preserves_branch(self):
        did = "20260719T2000Z-runner-2222"
        self._stage_session_branch(did)
        session.CFG["gate"] = FAIL_GATE
        landed = session._land_branch(None, None, "1.0.0", push=False)
        self.assertFalse(landed)
        self.assertEqual(self._current(), f"session/{did}", "still on the branch after a gate block")
        self.assertNotIn("work.txt", _head_files_on(self.repo, "main"),
                         "nothing reached the trunk")

    def test_rebase_conflict_blocks_and_aborts(self):
        did = "20260719T2000Z-runner-3333"
        self._stage_session_branch(did, conflict_trunk=True)
        # A concurrent session lands a conflicting change on main.
        _git(self.repo, "checkout", "-q", "main")
        (self.repo / "trunkfile.txt").write_text("main edit\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "concurrent main edit")
        _git(self.repo, "checkout", "-q", f"session/{did}")

        landed = session._land_branch(None, None, "1.0.0", push=False)
        self.assertFalse(landed)
        self.assertEqual(self._current(), f"session/{did}")
        # rebase was aborted cleanly — no rebase in progress, tree not conflicted.
        st = subprocess.run([GIT, "-C", str(self.repo), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
        self.assertNotIn("UU", st)

    def test_not_on_branch_is_noop(self):
        self.assertEqual(self._current(), "main")
        landed = session._land_branch(None, None, "1.0.0", push=False)
        self.assertFalse(landed)


class LandWithRemoteTest(BranchGateBase):
    """The production path: a real `origin` exists, so fetch succeeds, the trunk is
    fast-forwarded to origin/<trunk>, and the land pushes. The offline tests above
    cover the no-remote degradation; this covers what actually runs in a live repo."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)
        _git(self.repo, "remote", "add", "origin", str(self.origin))
        _git(self.repo, "push", "-q", "-u", "origin", "main")

    def _origin_files(self):
        return _head_files_on(self.origin, "main")

    def test_lands_and_pushes_to_origin(self):
        did = "20260719T2000Z-runner-6666"
        self._stage_session_branch(did)
        landed = session._land_branch(None, None, "1.0.0", push=True)
        self.assertTrue(landed)
        self.assertEqual(self._current(), "main")
        self.assertIn("work.txt", self._origin_files(), "work reached origin/main")
        self.assertIn(f"sessions/journal/{did}.md", self._origin_files())

    def test_rebases_onto_concurrent_origin_work_then_lands(self):
        """A concurrent session landed on origin/main first. Ours must rebase onto it
        (loser rebases) and still fast-forward — the real two-session case."""
        did = "20260719T2000Z-runner-7777"
        self._stage_session_branch(did)
        # Simulate the other session landing first, directly on origin.
        other = self.tmp / "other"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "t@t")
        _git(other, "config", "user.name", "t")
        (other / "other.txt").write_text("other session\n", encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "other session work")
        _git(other, "push", "-q", "origin", "main")

        landed = session._land_branch(None, None, "1.0.0", push=True)
        self.assertTrue(landed, "must rebase onto the concurrent work and land")
        files = self._origin_files()
        self.assertIn("work.txt", files, "our work landed")
        self.assertIn("other.txt", files, "the concurrent session's work survived")

    def test_gate_block_pushes_nothing(self):
        did = "20260719T2000Z-runner-8888"
        self._stage_session_branch(did)
        session.CFG["gate"] = FAIL_GATE
        landed = session._land_branch(None, None, "1.0.0", push=True)
        self.assertFalse(landed)
        self.assertNotIn("work.txt", self._origin_files(), "a blocked gate pushes nothing")


class EndToEndTest(BranchGateBase):
    def test_cmd_end_closes_journal_on_branch_and_lands(self):
        did = "20260719T2000Z-runner-4444"
        session._cut_session_branch(did)
        self._write_journal(did)
        (self.repo / "work.txt").write_text("work\n", encoding="utf-8")

        args = argparse.Namespace(session_id=did, title="phase2", commit="", push=False,
                                  focus=None, blocked=None, no_merge=False, dry_run=False,
                                  confirm="test close confirmation")
        session.cmd_end(args)

        self.assertEqual(self._current(), "main", "landed onto the trunk")
        self.assertIn(f"sessions/journal/{did}.md", _head_files(self.repo))
        fm, _ = session.parse_journal((session.JOURNAL_DIR / f"{did}.md").read_text(encoding="utf-8"))
        self.assertTrue(fm.get("ended"), "journal was closed")

    def test_cmd_end_no_merge_leaves_branch_open(self):
        did = "20260719T2000Z-runner-5555"
        session._cut_session_branch(did)
        self._write_journal(did)
        args = argparse.Namespace(session_id=did, title="held", commit="", push=False,
                                  focus=None, blocked=None, no_merge=True, dry_run=False,
                                  confirm="test close confirmation")
        session.cmd_end(args)
        self.assertEqual(self._current(), f"session/{did}", "--no-merge stays on the branch")


class SiblingLivenessTest(BranchGateBase):
    """ADR-0057 D2 — who counts as still working in this tree, and therefore blocks
    a land. The asymmetry under test: a fresh beat proves life, but a stale one
    proves nothing, so silence must NOT be read as death inside the grace."""

    def _iso(self, minutes_ago):
        return (datetime.now(session.CFG["tz"]) - timedelta(minutes=minutes_ago)).isoformat()

    def _sibling(self, did, beat_min=1, ended_min=None, closed=False):
        """An open (or closed) sibling journal plus its liveness sidecar."""
        self._write_journal(did, ended=self._iso(0) if closed else "")
        csid = "csid-" + did
        session._sidecar_write(csid, "live", {"last_beat": self._iso(beat_min)})
        if ended_min is not None:
            session._sidecar_write(csid, "ended", {"ended": self._iso(ended_min)})
        return csid

    def test_open_journal_with_fresh_beat_blocks_landing(self):
        self._sibling("sib-1", beat_min=1)
        self.assertEqual(session._live_sibling_csids("mine"), ["csid-sib-1"])

    def test_self_is_excluded(self):
        csid = self._sibling("sib-1", beat_min=1)
        self.assertEqual(session._live_sibling_csids(csid), [])

    def test_idle_within_grace_still_blocks_landing(self):
        """The regression this ADR exists for: a session idle between turns goes
        quiet well past HEARTBEAT_STALE_MIN (15m) and is very much alive. Reading
        that silence as death is what deleted its branch."""
        self._sibling("sib-1", beat_min=session.HEARTBEAT_STALE_MIN + 20)
        self.assertEqual(session._live_sibling_csids("mine"), ["csid-sib-1"],
                         "an idle sibling must still defer the land")

    def test_clean_exit_does_not_block(self):
        self._sibling("sib-1", beat_min=5, ended_min=1)     # exit AFTER the last beat
        self.assertEqual(session._live_sibling_csids("mine"), [])

    def test_beat_after_exit_marker_is_a_resume_and_blocks(self):
        self._sibling("sib-1", beat_min=1, ended_min=5)     # resumed past the exit
        self.assertEqual(session._live_sibling_csids("mine"), ["csid-sib-1"])

    def test_silent_past_the_grace_does_not_block(self):
        self._sibling("sib-1", beat_min=session.LAND_DEFER_GRACE_MIN + 10)
        self.assertEqual(session._live_sibling_csids("mine"), [])

    def test_closed_journal_does_not_block(self):
        self._sibling("sib-1", beat_min=1, closed=True)
        self.assertEqual(session._live_sibling_csids("mine"), [])

    def test_grace_sits_between_the_liveness_proof_and_the_reap_grace(self):
        """The three constants encode three different decisions; collapsing any two
        reintroduces either the deleted-branch bug or a 48h landing freeze."""
        self.assertLess(session.HEARTBEAT_STALE_MIN, session.LAND_DEFER_GRACE_MIN)
        self.assertLess(session.LAND_DEFER_GRACE_MIN, session.REAP_CRASH_GRACE_MIN)


class AttributionTest(BranchGateBase):
    """ADR-0057 D1 — per-session file attribution and what it stages."""

    def test_records_editing_tool_paths(self):
        session._record_touch("csid-a", "Write", {"file_path": str(self.repo / "a.txt")})
        session._record_touch("csid-a", "Edit", {"file_path": str(self.repo / "b.txt")})
        self.assertEqual(session._touched_paths("csid-a"), ["a.txt", "b.txt"])

    def test_ignores_non_editing_tools(self):
        session._record_touch("csid-a", "Bash", {"command": "rm -rf /"})
        session._record_touch("csid-a", "Read", {"file_path": str(self.repo / "a.txt")})
        self.assertEqual(session._touched_paths("csid-a"), [],
                         "a read is not a write; Bash writes are invisible here by design")

    def test_ignores_paths_outside_the_repo(self):
        session._record_touch("csid-a", "Write", {"file_path": "/etc/passwd"})
        self.assertEqual(session._touched_paths("csid-a"), [])

    def test_records_each_path_once(self):
        for _ in range(3):
            session._record_touch("csid-a", "Write", {"file_path": str(self.repo / "a.txt")})
        self.assertEqual(session._touched_paths("csid-a"), ["a.txt"])

    def test_unknown_session_has_no_paths(self):
        self.assertEqual(session._touched_paths(None), [])
        self.assertEqual(session._touched_paths("never-seen"), [])

    def test_claims_mine_and_unclaimed_but_not_a_live_siblings_work(self):
        """The core of D1: a close must not carry the other session's in-flight edits."""
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        (self.repo / "theirs.txt").write_text("theirs\n", encoding="utf-8")
        (self.repo / "generated.txt").write_text("by a script\n", encoding="utf-8")
        session._record_touch("csid-me", "Write", {"file_path": str(self.repo / "mine.txt")})
        session._record_touch("csid-them", "Write", {"file_path": str(self.repo / "theirs.txt")})

        paths, exact, swept = session._attributed_paths("csid-me", ["csid-them"])
        self.assertEqual(exact, 1)                     # mine.txt, attributed exactly
        self.assertGreaterEqual(swept, 1)              # generated.txt + other unclaimed
        self.assertIn("mine.txt", paths)
        self.assertIn("generated.txt", paths, "unclaimed Bash residue is swept up")
        self.assertNotIn("theirs.txt", paths, "a live sibling's work is left alone")

    def test_nothing_is_staged_in_the_shared_index(self):
        """ADR-0058 D3: attribution never touches the shared index — the candidate is
        built through a private one, so concurrent sessions cannot corrupt each other."""
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-me", "Write", {"file_path": str(self.repo / "mine.txt")})
        session._attributed_paths("csid-me", [])
        staged = subprocess.run([GIT, "-C", str(self.repo), "diff", "--cached",
                                 "--name-only"], capture_output=True, text=True).stdout.split()
        self.assertEqual(staged, [], "the shared index must be left alone")


class LandPerSessionTest(BranchGateBase):
    """ADR-0058 D2/D3/D5 end-to-end through `cmd_end`: every session lands its own work
    immediately, gated in isolation, without waiting on anyone."""

    def _args(self, sid, **over):
        d = dict(title="did things", session_id=sid, commit="", push=False,
                 dry_run=False, focus=None, blocked=None, no_merge=False,
                 confirm="test close confirmation")
        d.update(over)
        return argparse.Namespace(**d)

    def _iso_now(self):
        return datetime.now(session.CFG["tz"]).isoformat()

    def _live_sibling(self, did="20260719T2000Z-runner-bbbb"):
        self._write_journal(did)
        session._sidecar_write("csid-" + did, "live", {"last_beat": self._iso_now()})
        return "csid-" + did

    def _close(self, did, **over):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "csid-" + did}):
            session.cmd_end(self._args(did, **over))

    def test_lands_immediately_even_with_a_live_sibling(self):
        """The ADR-0057 regression: a live sibling used to freeze the trunk forever."""
        did = "20260719T2000Z-runner-aaaa"
        self._write_journal(did)
        sib = self._live_sibling()
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        (self.repo / "theirs.txt").write_text("half-written\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})
        session._record_touch(sib, "Write", {"file_path": str(self.repo / "theirs.txt")})

        receipt = io.StringIO()
        with contextlib.redirect_stdout(receipt):
            self._close(did)
        self.assertIn("gated in: isolated ", receipt.getvalue())

        landed = _head_files_on(self.repo, "main")
        self.assertIn("mine.txt", landed, "a session lands without waiting on anyone")
        self.assertNotIn("theirs.txt", landed,
                         "the live sibling's in-flight file must NOT be committed")

    def test_the_siblings_file_survives_on_disk_uncommitted(self):
        did = "20260719T2000Z-runner-aaaa"
        self._write_journal(did)
        sib = self._live_sibling()
        (self.repo / "theirs.txt").write_text("half-written\n", encoding="utf-8")
        session._record_touch(sib, "Write", {"file_path": str(self.repo / "theirs.txt")})
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})

        self._close(did)

        self.assertEqual((self.repo / "theirs.txt").read_text(), "half-written\n",
                         "their work is left exactly as it was, still theirs to commit")

    def test_gate_failure_blocks_and_leaves_work_on_disk(self):
        did = "20260719T2000Z-runner-cccc"
        self._write_journal(did)
        session.CFG["gate"] = FAIL_GATE
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})

        # WI-0263: the close now carries its verdict into the exit status, so a blocked
        # land raises. Asserting the code here rather than merely tolerating the
        # exception keeps this test pinning BOTH halves of the promise — the work
        # survives on disk, and the caller is told the land did not happen. Exit 1 is
        # "refused, trunk untouched"; contention would be 2.
        with self.assertRaises(SystemExit) as caught:
            self._close(did)
        self.assertEqual(caught.exception.code, 1)

        self.assertNotIn("mine.txt", _head_files_on(self.repo, "main"))
        self.assertTrue((self.repo / "mine.txt").exists(),
                        "a blocked gate must never cost the work")

    def test_gate_runs_against_the_candidate_not_the_dirty_tree(self):
        """ADR-0058 D4: a sibling's broken in-flight file is not in the candidate, so
        it cannot fail this session's gate. The gate asserts the file is absent."""
        did = "20260719T2000Z-runner-dddd"
        self._write_journal(did)
        sib = self._live_sibling()
        (self.repo / "broken.txt").write_text("their mess\n", encoding="utf-8")
        session._record_touch(sib, "Write", {"file_path": str(self.repo / "broken.txt")})
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})
        session.CFG["gate"] = [["python3", "-c",
                                "import os,sys; sys.exit(1 if os.path.exists('broken.txt') else 0)"]]

        self._close(did)

        self.assertIn("mine.txt", _head_files_on(self.repo, "main"),
                      "the gate saw a clean candidate, not the shared tree")

    def test_shared_index_is_never_touched(self):
        did = "20260719T2000Z-runner-eeee"
        self._write_journal(did)
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})

        self._close(did)

        staged = subprocess.run([GIT, "-C", str(self.repo), "diff", "--cached",
                                 "--name-only"], capture_output=True, text=True).stdout.split()
        self.assertEqual(staged, [], "landing leaves the shared index clean")

    def test_loser_of_the_race_rebuilds_on_the_new_tip(self):
        """ADR-0058 D5: if the trunk moves between build and swap, the loser rebuilds
        rather than clobbering. Simulated by advancing the trunk during the gate."""
        did = "20260719T2000Z-runner-ffff"
        self._write_journal(did)
        (self.repo / "mine.txt").write_text("mine\n", encoding="utf-8")
        session._record_touch("csid-" + did, "Write",
                              {"file_path": str(self.repo / "mine.txt")})

        real_gate, state = session._gate_commit, {"bumped": False}

        def racing_gate(sha, skip=frozenset()):
            if not state["bumped"]:      # another session lands while we are gating
                state["bumped"] = True
                (self.repo / "other.txt").write_text("other session\n", encoding="utf-8")
                _git(self.repo, "add", "other.txt")
                _git(self.repo, "commit", "-qm", "other session landed")
            return real_gate(sha, skip)

        with mock.patch.object(session, "_gate_commit", racing_gate):
            self._close(did)

        landed = _head_files_on(self.repo, "main")
        self.assertIn("mine.txt", landed, "the loser still lands, on the new tip")
        self.assertIn("other.txt", landed, "and never clobbers the winner")


if __name__ == "__main__":
    unittest.main()
