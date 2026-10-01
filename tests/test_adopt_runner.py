"""Tests for curate/adopt-runner.py — the ADR-0050 headless adoption runner.

Covers the runner's PURE decision logic — the parts that decide *whether* a repo
and a brief get a headless session, with no I/O and no spawned process:

  * classify_brief — the read-side of the ADR-0050 total manual partition: only a
    `manual` brief with a `verify:` and a non-`attended` reason is agent-path
    eligible; `auto` is the code path, everything else surfaces to operator.
  * is_claude_member — a member declaring NO Claude runtime is skipped; one declaring
    Claude among co-equal runtimes is served, like the fleet push.
  * repo_is_live / beat_age_minutes — the ADR-0036 liveness guard: a fresh
    heartbeat means never race a session.

The spawn/verify/reset side effects are exercised by the live pilot, not here.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import poga_evidence as evidence  # noqa: E402  (WI-0371 — the shared cut rule these tests assert on)

_spec = importlib.util.spec_from_file_location("adopt_runner", ROOT / "curate" / "adopt-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _hdr(text):
    return runner.parse_frontmatter(text)


class ClassifyBriefTest(unittest.TestCase):

    def _classify(self, text):
        header, has_fm = _hdr(text)
        return runner.classify_brief(header, has_fm)

    def test_manual_with_verify_is_agent_eligible(self):
        ok, route, _ = self._classify(
            "---\napply: manual\nmanual-reason: multi-file migration\n"
            "verify: python3 -m pytest\n---\n\nbody\n")
        self.assertTrue(ok)
        self.assertEqual(route, "agent")

    def test_auto_is_code_path_not_runner(self):
        ok, route, _ = self._classify("---\napply: auto\n---\n\nbody\n")
        self.assertFalse(ok)
        self.assertEqual(route, "code")

    def test_manual_attended_surfaces_to_the_operator(self):
        ok, route, _ = self._classify(
            "---\napply: manual\nmanual-reason: attended\nverify: true\n---\n\nbody\n")
        self.assertFalse(ok)
        self.assertEqual(route, "operator")

    def test_manual_attended_case_insensitive(self):
        ok, route, _ = self._classify(
            "---\napply: manual\nmanual-reason: Attended\n---\n\nbody\n")
        self.assertFalse(ok)
        self.assertEqual(route, "operator")

    def test_manual_without_verify_surfaces_to_the_operator(self):
        ok, route, _ = self._classify(
            "---\napply: manual\nmanual-reason: substrate install\n---\n\nbody\n")
        self.assertFalse(ok)
        self.assertEqual(route, "operator")

    def test_no_frontmatter_surfaces(self):
        ok, route, _ = self._classify("# a prose brief\n\nbody\n")
        self.assertFalse(ok)
        self.assertEqual(route, "operator")


class RuntimeFilterTest(unittest.TestCase):

    def test_default_config_is_claude(self):
        self.assertTrue(runner.is_claude_member({}))

    def test_explicit_claude_runtime(self):
        self.assertTrue(runner.is_claude_member({"runtime": "claude-code"}))

    def test_non_claude_runtime_excluded(self):
        self.assertFalse(runner.is_claude_member({"runtime": "gemini-antigravity"}))

    def test_symmetric_multi_runtime_member_is_included(self):
        # A member declaring Claude among co-equal runtimes has
        # first-class Claude sessions, so the agent path serves it, exactly as the
        # fleet push does. This assertion was inverted until session 90, which is why
        # a brief addressed to such a member's Claude side could never be adopted.
        self.assertTrue(runner.is_claude_member(
            {"runtimes": ["claude-code", "gemini-antigravity"]}))

    def test_member_with_no_claude_runtime_excluded(self):
        self.assertFalse(runner.is_claude_member(
            {"runtimes": ["gemini-antigravity", "codex"]}))

    def test_claude_only_runtimes_array_included(self):
        self.assertTrue(runner.is_claude_member({"runtimes": ["claude-code"]}))


class AuthDetectTest(unittest.TestCase):

    def test_not_logged_in_is_auth_error(self):
        self.assertTrue(runner.is_auth_error("Not logged in · Please run /login"))

    def test_expired_token_is_auth_error(self):
        self.assertTrue(runner.is_auth_error(
            "API Error: 401 OAuth access token has expired. Re-authenticate to continue."))

    def test_ordinary_failure_is_not_auth_error(self):
        self.assertFalse(runner.is_auth_error("verify exit 1: assertion failed"))

    def test_empty_is_not_auth_error(self):
        self.assertFalse(runner.is_auth_error(""))
        self.assertFalse(runner.is_auth_error(None))


class LivenessTest(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 7, 15, 21, 0, 0, tzinfo=timezone.utc)

    def _iso(self, minutes_ago):
        return (self.now - timedelta(minutes=minutes_ago)).isoformat()

    def test_fresh_heartbeat_is_live(self):
        self.assertTrue(runner.repo_is_live([{"last_beat": self._iso(3)}], self.now))

    def test_stale_heartbeat_is_not_live(self):
        self.assertFalse(runner.repo_is_live([{"last_beat": self._iso(30)}], self.now))

    def test_any_fresh_among_many_is_live(self):
        jsons = [{"last_beat": self._iso(120)}, {"last_beat": self._iso(2)}]
        self.assertTrue(runner.repo_is_live(jsons, self.now))

    def test_no_live_files_is_not_live(self):
        self.assertFalse(runner.repo_is_live([], self.now))

    def test_unparseable_timestamp_is_ignored(self):
        self.assertFalse(runner.repo_is_live([{"last_beat": "not-a-date"}], self.now))

    def test_falls_back_to_started_when_no_last_beat(self):
        self.assertTrue(runner.repo_is_live([{"started": self._iso(1)}], self.now))

    def test_boundary_exactly_at_threshold_is_live(self):
        # age == stale_min counts as live (<=), matching session.py's concurrent test.
        self.assertTrue(runner.repo_is_live(
            [{"last_beat": self._iso(runner.HEARTBEAT_STALE_MIN)}], self.now))


class JanitorPassTest(unittest.TestCase):
    """The nightly retention pass. It runs each member's OWN `session.py janitor` in its
    own repo, so the federation never tidies a member's tree itself, and it must survive
    the fleet being mid-rollout — which is its normal state for as long as a push takes."""

    def setUp(self):
        self.now = datetime(2026, 7, 15, 21, 0, 0, tzinfo=timezone.utc)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.args = argparse.Namespace(dry_run=True, verbose=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _repo(self, name, script_body):
        r = self.root / name
        (r / ".session-state").mkdir(parents=True)
        (r / "session.py").write_text(script_body, encoding="utf-8")
        return r

    def test_a_member_whose_harness_predates_the_verb_reads_as_a_rollout_gap(self):
        """argparse's own failure dumps ~45 subcommand names. Left raw it buries the one
        fact that matters — this member needs a substrate push — under a wall of noise,
        and an unreadable report is a report nobody acts on."""
        r = self._repo("old", "import sys\n"
                             "sys.stderr.write(\"session.py: error: argument cmd: \"\n"
                             "  \"invalid choice: 'janitor' (choose from 'start', 'end')\\n\")\n"
                             "sys.exit(2)\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = runner.run_janitor_pass([r], self.now, self.args)
        self.assertEqual({"swept": 0, "skipped": 0, "failed": 1}, res)
        self.assertIn("needs a substrate push", buf.getvalue())
        self.assertNotIn("choose from", buf.getvalue())

    def test_a_live_repo_is_never_swept(self):
        """Same rule the adoption sweep follows: a fresh heartbeat means hands off."""
        r = self._repo("live", "print('janitor: pruned 0 sidecar file(s)')\n")
        (r / ".session-state" / "x.live").write_text(
            json.dumps({"last_beat": (self.now - timedelta(minutes=2)).isoformat()}),
            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            res = runner.run_janitor_pass([r], self.now, self.args)
        self.assertEqual({"swept": 0, "skipped": 1, "failed": 0}, res)

    def test_a_quiet_member_is_swept_and_its_closes_are_reported(self):
        """A close is news even on a routine pass — it is the signal that something is
        not closing itself, which is the defect the sweep must never hide."""
        r = self._repo("quiet", "print('janitor: closed j-1 at 2026-07-01 (aged out)')\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = runner.run_janitor_pass([r], self.now, self.args)
        self.assertEqual({"swept": 1, "skipped": 0, "failed": 0}, res)
        self.assertIn("closed j-1", buf.getvalue())

    def test_a_repo_with_no_harness_is_not_a_failure(self):
        """A located directory that is not a converged member is simply not our business —
        counting it as a failure would make the nightly report cry wolf every night."""
        r = self.root / "notamember"
        r.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            res = runner.run_janitor_pass([r], self.now, self.args)
        self.assertEqual({"swept": 0, "skipped": 0, "failed": 0}, res)

    def test_one_broken_member_does_not_stop_the_others(self):
        good = self._repo("good", "print('janitor: pruned 3 sidecar file(s)')\n")
        bad = self._repo("bad", "import sys; sys.exit(9)\n")
        with contextlib.redirect_stdout(io.StringIO()):
            res = runner.run_janitor_pass([bad, good], self.now, self.args)
        self.assertEqual({"swept": 1, "skipped": 0, "failed": 1}, res)

    def test_dry_run_is_passed_through_to_the_member(self):
        r = self._repo("echo", "import sys; print('janitor: ' + ' '.join(sys.argv[1:]))\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.run_janitor_pass([r], self.now,
                                    argparse.Namespace(dry_run=True, verbose=True))
        self.assertIn("--dry-run", buf.getvalue())


class FoldStatusTest(unittest.TestCase):
    """The pure stale-streak / proof-of-life fold — the state that decides whether the
    routine 'couldn't authenticate' lives on the status line or escalates to comms."""

    S1, S2, S3 = "2026-07-15T03:15:00Z", "2026-07-16T03:15:00Z", "2026-07-17T03:15:00Z"

    def test_first_unauthenticated_starts_streak(self):
        st = runner.fold_status({}, result="unauthenticated", stamp=self.S1, detail="expired")
        self.assertEqual(st["consecutive_stale"], 1)
        self.assertEqual(st["stale_since"], self.S1)
        self.assertIsNone(st["last_success"])

    def test_consecutive_unauth_preserves_stale_since(self):
        st1 = runner.fold_status({}, result="unauthenticated", stamp=self.S1)
        st2 = runner.fold_status(st1, result="unauthenticated", stamp=self.S2)
        self.assertEqual(st2["consecutive_stale"], 2)
        self.assertEqual(st2["stale_since"], self.S1)  # first-seen, not the latest sweep

    def test_authenticated_completion_resets_and_stamps_success(self):
        st1 = runner.fold_status({}, result="unauthenticated", stamp=self.S1)
        st2 = runner.fold_status(st1, result="no-eligible", stamp=self.S2)
        self.assertEqual(st2["consecutive_stale"], 0)
        self.assertIsNone(st2["stale_since"])
        self.assertEqual(st2["last_success"], self.S2)

    def test_failed_verify_counts_as_authenticated_proof_of_life(self):
        # A brief that failed VERIFY still means the runner authenticated + ran.
        st = runner.fold_status({}, result="failed", stamp=self.S1, adopted=0, failed=1)
        self.assertEqual(st["consecutive_stale"], 0)
        self.assertEqual(st["last_success"], self.S1)

    # ── WI-0084: two questions, two fields ────────────────────────────────────────

    def test_a_failed_sweep_does_not_stamp_a_clean_sweep(self):
        """THE LIVE SHAPE. A real status file read `result: failed, failed: 1,
        eligible: 1` while `last_success` carried the SAME SECOND — and that is the one
        field a human or a dashboard glances at. `last_success` was not wrong for what
        it exists to do (prove auth works); it was wrong for what its name promises, and
        there was no field at all for adoption health."""
        st = runner.fold_status({}, result="failed", stamp=self.S1, adopted=0, failed=1)
        self.assertEqual(st["last_success"], self.S1, "auth proof is unchanged")
        self.assertIsNone(st["last_clean_sweep"],
                          "a sweep in which a brief failed is not a clean sweep")

    def test_a_clean_sweep_stamps_both(self):
        st = runner.fold_status({}, result="no-eligible", stamp=self.S1)
        self.assertEqual(st["last_success"], self.S1)
        self.assertEqual(st["last_clean_sweep"], self.S1)

    def test_an_adoption_with_no_failures_is_clean(self):
        st = runner.fold_status({}, result="adopted", stamp=self.S1, adopted=2, failed=0)
        self.assertEqual(st["last_clean_sweep"], self.S1)

    def test_a_partial_adoption_is_not_clean(self):
        """Some adopted and some failed is the case most likely to read as fine."""
        st = runner.fold_status({}, result="adopted", stamp=self.S1, adopted=1, failed=1)
        self.assertEqual(st["last_success"], self.S1)
        self.assertIsNone(st["last_clean_sweep"])

    def test_the_clean_stamp_is_carried_not_cleared_by_a_later_bad_sweep(self):
        """"When did it last work?" must stay answerable through a bad run — clearing it
        would trade one silence for another."""
        st1 = runner.fold_status({}, result="no-eligible", stamp=self.S1)
        st2 = runner.fold_status(st1, result="failed", stamp=self.S2, failed=1)
        self.assertEqual(st2["last_clean_sweep"], self.S1)
        self.assertEqual(st2["last_success"], self.S2)

    def test_unauthenticated_and_error_never_stamp_clean(self):
        st1 = runner.fold_status({}, result="unauthenticated", stamp=self.S1)
        self.assertIsNone(st1["last_clean_sweep"])
        st2 = runner.fold_status({}, result="error", stamp=self.S1)
        self.assertIsNone(st2["last_clean_sweep"])

    def test_error_is_auth_agnostic_and_carries_prior(self):
        st1 = runner.fold_status({}, result="unauthenticated", stamp=self.S1)
        st2 = runner.fold_status(st1, result="error", stamp=self.S2, detail="boom")
        # An error is not an auth verdict: streak + last_success carry unchanged.
        self.assertEqual(st2["consecutive_stale"], 1)
        self.assertEqual(st2["stale_since"], self.S1)
        self.assertIsNone(st2["last_success"])

    def test_success_then_stale_starts_fresh_streak(self):
        st1 = runner.fold_status({}, result="adopted", stamp=self.S1, adopted=1)
        st2 = runner.fold_status(st1, result="unauthenticated", stamp=self.S2)
        self.assertEqual(st2["consecutive_stale"], 1)
        self.assertEqual(st2["stale_since"], self.S2)      # new streak, new first-seen
        self.assertEqual(st2["last_success"], self.S1)     # last good sweep remembered


class EscalateTest(unittest.TestCase):

    def test_below_threshold_does_not_escalate(self):
        self.assertFalse(runner.should_escalate({"consecutive_stale": 4}, threshold=5))

    def test_at_threshold_escalates(self):
        self.assertTrue(runner.should_escalate({"consecutive_stale": 5}, threshold=5))

    def test_past_threshold_escalates(self):
        self.assertTrue(runner.should_escalate({"consecutive_stale": 9}, threshold=5))

    def test_zero_streak_never_escalates(self):
        self.assertFalse(runner.should_escalate({"consecutive_stale": 0}, threshold=5))
        self.assertFalse(runner.should_escalate({}, threshold=5))


class StaleNoteSyncTest(unittest.TestCase):
    """The standing stale-login note is a single CONDITION file: written only past the
    threshold, refreshed in place (never one-per-night), and cleared on recovery."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.note = pathlib.Path(self._tmp.name) / "comms" / "adopt-runner-stale.md"

    def tearDown(self):
        self._tmp.cleanup()

    def _status(self, stale, since="2026-07-10T03:15:00Z"):
        return {"consecutive_stale": stale, "stale_since": since,
                "last_run": "2026-07-17T03:15:00Z", "last_success": None}

    def test_below_threshold_writes_nothing(self):
        action, _ = runner.sync_stale_note(self._status(3), escalate_after=5, path=self.note)
        self.assertEqual(action, "noop")
        self.assertFalse(self.note.exists())

    def test_at_threshold_writes_single_note(self):
        action, p = runner.sync_stale_note(self._status(5), escalate_after=5, path=self.note)
        self.assertEqual(action, "written")
        self.assertTrue(self.note.exists())
        body = self.note.read_text()
        self.assertIn("5 consecutive", body)
        self.assertIn("type: blocked", body)

    def test_refresh_overwrites_same_file_not_accretes(self):
        runner.sync_stale_note(self._status(5), escalate_after=5, path=self.note)
        runner.sync_stale_note(self._status(6), escalate_after=5, path=self.note)
        # exactly ONE file, updated to the newer night count
        siblings = list(self.note.parent.glob("*.md"))
        self.assertEqual(len(siblings), 1)
        self.assertIn("6 consecutive", self.note.read_text())

    def test_recovery_clears_the_note(self):
        runner.sync_stale_note(self._status(5), escalate_after=5, path=self.note)
        self.assertTrue(self.note.exists())
        action, _ = runner.sync_stale_note(self._status(0, since=None), escalate_after=5, path=self.note)
        self.assertEqual(action, "cleared")
        self.assertFalse(self.note.exists())

    def test_recovery_with_no_prior_note_is_noop(self):
        action, _ = runner.sync_stale_note(self._status(0, since=None), escalate_after=5, path=self.note)
        self.assertEqual(action, "noop")


class StatusRoundTripTest(unittest.TestCase):

    def test_wi0315_default_escalates_on_first_stale_sweep(self):
        self.assertEqual(runner.DEFAULT_ESCALATE_AFTER_STALE, 1)
        self.assertTrue(runner.should_escalate({"consecutive_stale": 1}))

    def test_wi0315_first_stale_status_and_note_clear_on_recovery(self):
        from functools import partial
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            status_path = pathlib.Path(d) / "adopt-runner.status"
            note_path = pathlib.Path(d) / "adopt-runner-stale.md"
            args = argparse.Namespace(escalate_after=runner.DEFAULT_ESCALATE_AFTER_STALE)
            with patch.object(runner, "write_status", partial(runner.write_status, path=status_path)), \
                 patch.object(runner, "sync_stale_note", partial(runner.sync_stale_note, path=note_path)):
                runner.persist_status({}, result="unauthenticated",
                                      stamp="2026-09-04T03:15:00Z", args=args)
                stale = runner.read_status(status_path)
                with self.subTest("status on first sweep"):
                    self.assertEqual(stale.get("auth"), "unauthenticated since 2026-09-04T03:15:00Z")
                with self.subTest("note on first sweep"):
                    self.assertTrue(note_path.exists())
                    if note_path.exists():
                        self.assertIn("2026-09-04T03:15:00Z", note_path.read_text())
                runner.persist_status(stale, result="no-eligible",
                                      stamp="2026-09-07T03:00:00Z", args=args)
                recovered = runner.read_status(status_path)
                self.assertIsNone(recovered.get("auth"))
                self.assertIsNone(recovered["stale_since"])
                self.assertEqual(recovered["consecutive_stale"], 0)
                self.assertFalse(note_path.exists())


    def test_write_then_read_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / ".session-state" / "adopt-runner.status"
            st = runner.fold_status({}, result="no-eligible", stamp="2026-07-17T03:15:00Z",
                                    detail="0 adopted", repos_scanned=8)
            runner.write_status(st, path=p)
            self.assertEqual(runner.read_status(path=p), st)

    def test_read_absent_status_is_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "does-not-exist.status"
            self.assertEqual(runner.read_status(path=p), {})


class LaunchDirTest(unittest.TestCase):
    """`_launch_dir` decides where the headless `claude -p` adoption session
    actually launches. Getting this wrong for a resident-runtime member (one whose repo
    root is its runtime's own persona, not the Architect's) means the
    unattended, --permission-mode bypassPermissions sweep boots the WRONG persona
    with no gates at all — this is the regression pin for that class (session 99)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / "resident-member"
        self.repo.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_pad_dir_launches_at_repo_root(self):
        self.assertEqual(runner._launch_dir(self.repo, {}), self.repo)

    def test_declared_pad_dir_launches_at_the_pad(self):
        pad = self.repo.parent / "architect"
        pad.mkdir()
        got = runner._launch_dir(self.repo, {"pad_dir": "../architect"})
        self.assertEqual(got, pad.resolve())
        self.assertNotEqual(got, self.repo)

    def test_declared_pad_dir_that_does_not_exist_falls_back_to_repo(self):
        # Never invent a launch site the member hasn't actually built.
        got = runner._launch_dir(self.repo, {"pad_dir": "../architect"})
        self.assertEqual(got, self.repo)

    def test_run_claude_adopt_uses_the_pad_as_cwd(self):
        pad = self.repo.parent / "architect"
        pad.mkdir()
        (self.repo / "inbox").mkdir()
        brief = self.repo / "inbox" / "x.md"
        brief.write_text("body", encoding="utf-8")
        seen = {}

        # `**kw` rather than a fixed signature: this stub stands in for
        # `subprocess.run`, and pinning its keywords makes an unrelated change to the
        # spawn fail HERE, on a test about the pad, with a TypeError that names nothing
        # about the launch directory. WI-0331 adding `env=` did exactly that.
        def fake_run(cmd, cwd=None, **kw):
            seen["cwd"] = cwd
            seen["kw"] = kw
            class R:
                returncode = 0
                stdout = '{"is_error": false}'
                stderr = ""
            return R()

        orig = runner.subprocess.run
        runner.subprocess.run = fake_run
        try:
            runner.run_claude_adopt(self.repo, {"pad_dir": "../architect"}, brief,
                                    "claude", None, 60, 10, False)
        finally:
            runner.subprocess.run = orig
        self.assertEqual(pathlib.Path(seen["cwd"]), pad.resolve())
        # WI-0331: a resident-runtime member's session is marked like any other. The pad
        # is the subject here only because it is where the record has to land — `end`
        # resolves it from the harness's own root, which for a pad member is the pad.
        self.assertIn("POGA_UNATTENDED_RUN", seen["kw"]["env"])
        # ...and the record lands in the PAD's state dir, not the repo's: `end` resolves
        # it from the harness's own root, which for a resident-runtime member is the pad.
        # Asserted on the directory, because the record itself is retired in the spawn's
        # `finally` and is already gone by the time this line runs.
        self.assertTrue((pad / ".session-state").is_dir())
        self.assertFalse((self.repo / ".session-state").exists())
        self.assertEqual(list((pad / ".session-state").glob("unattended-run-*.json")), [])


class DeadLetterLedgerTest(unittest.TestCase):
    """WI-0084 A. The runner's failure mode was SILENT INFINITE RETRY.

    Observed more than once, each time self-clearing and never escalated: a brief
    failed verify night after night, and stopped only because some unrelated change
    made it ineligible. Nothing counted the failures and nothing told anyone.

    The counting is the easy half. What these pin is the parts that are easy to get
    subtly wrong: that an infrastructure `error` is not evidence against a brief, that a
    success clears the record rather than leaving a decaying count, and above all that an
    EDITED brief starts over — without that, quarantine is a trap with no exit and the fix
    for a broken brief looks exactly like the bug."""

    def test_a_single_failure_does_not_quarantine(self):
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="S1", detail="verify failed")
        self.assertEqual(st["r::b"]["failures"], 1)
        self.assertNotIn("dead_since", st["r::b"])
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "f1"))

    def test_the_third_consecutive_failure_quarantines(self):
        st = {}
        for i in range(3):
            st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="failed",
                                         stamp=f"S{i}", detail="boom")
        self.assertEqual(st["r::b"]["failures"], 3)
        self.assertTrue(runner.is_dead_lettered(st, "r::b", "f1"))

    def test_the_boundary_is_driven_from_both_sides(self):
        """Two failures must NOT quarantine; three must. A `>` slipped to `>=` (or the
        reverse) changes how long the runner keeps hammering a broken brief."""
        st = {}
        for i in range(2):
            st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="failed",
                                         stamp=f"S{i}")
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "f1"))
        st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="failed",
                                     stamp="S2")
        self.assertTrue(runner.is_dead_lettered(st, "r::b", "f1"))

    def test_an_edited_brief_starts_over(self):
        """The anti-roach-motel property. A brief is quarantined for failing three times;
        the obvious next move is to fix it and re-deliver. Keyed on id alone, the fixed
        brief would inherit its predecessor's failures and stay quarantined forever."""
        st = {}
        for i in range(3):
            st = runner.fold_brief_state(st, "r::b", fingerprint="old", result="failed",
                                         stamp=f"S{i}")
        self.assertTrue(runner.is_dead_lettered(st, "r::b", "old"))
        # same brief id, new content
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "new"))
        st = runner.fold_brief_state(st, "r::b", fingerprint="new", result="failed",
                                     stamp="S9")
        self.assertEqual(st["r::b"]["failures"], 1)
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "new"))

    def test_a_successful_adoption_clears_the_record_entirely(self):
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="S1")
        st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="adopted",
                                     stamp="S2")
        self.assertNotIn("r::b", st)

    def test_an_infrastructure_error_does_not_count_against_the_brief(self):
        """The runner breaking is not evidence about the brief. Counting it would
        quarantine innocent briefs on a bad night — the same conflation `last_success`
        had between "the runner is alive" and "adoption is healthy", one layer down."""
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="S1")
        for i in range(5):
            st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="error",
                                         stamp=f"E{i}")
        self.assertEqual(st["r::b"]["failures"], 1)
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "f1"))

    def test_the_threshold_is_configurable(self):
        st = {}
        for i in range(2):
            st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="failed",
                                         stamp=f"S{i}", dead_letter_after=2)
        self.assertTrue(runner.is_dead_lettered(st, "r::b", "f1"))

    def test_the_record_carries_the_verdict_and_both_timestamps(self):
        """A quarantine that does not say WHY is another silence."""
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="FIRST", detail="verify: missing gizmo key")
        st = runner.fold_brief_state(st, "r::b", fingerprint="f1", result="failed",
                                     stamp="LAST", detail="verify: still missing")
        rec = st["r::b"]
        self.assertEqual(rec["first_failed"], "FIRST")
        self.assertEqual(rec["last_failed"], "LAST")
        self.assertIn("still missing", rec["last_detail"])

    def test_briefs_are_counted_independently(self):
        st = {}
        for i in range(3):
            st = runner.fold_brief_state(st, "r::a", fingerprint="fa", result="failed",
                                         stamp=f"S{i}")
        st = runner.fold_brief_state(st, "r::b", fingerprint="fb", result="failed",
                                     stamp="S0")
        self.assertTrue(runner.is_dead_lettered(st, "r::a", "fa"))
        self.assertFalse(runner.is_dead_lettered(st, "r::b", "fb"))

    def test_the_same_brief_id_in_two_repos_is_two_records(self):
        self.assertNotEqual(runner.brief_key(pathlib.Path("/x/alpha"), "b1"),
                            runner.brief_key(pathlib.Path("/x/beta"), "b1"))

    def test_fingerprint_changes_with_content_and_is_stable_otherwise(self):
        self.assertEqual(runner.brief_fingerprint("abc"), runner.brief_fingerprint("abc"))
        self.assertNotEqual(runner.brief_fingerprint("abc"), runner.brief_fingerprint("abd"))


class DeadLetterStateIOTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def test_a_missing_ledger_is_a_fresh_start_not_an_error(self):
        self.assertEqual(runner.read_brief_state(self.tmp / "nope.json"), {})

    def test_a_corrupt_ledger_is_a_fresh_start_not_an_error(self):
        """Erring toward forgetting is the right direction: forgetting costs one extra
        sweep, remembering wrongly quarantines a brief that works."""
        p = self.tmp / "briefs.json"
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(runner.read_brief_state(p), {})

    def test_a_ledger_round_trips(self):
        p = self.tmp / "briefs.json"
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="S1")
        runner.write_brief_state(st, p)
        self.assertEqual(runner.read_brief_state(p), st)


class DeadLetterNoteTest(unittest.TestCase):
    """Same discipline as the stale-login note: one stable-named CONDITION note,
    refreshed while it holds, removed the moment it clears. One file per failure would
    accrete nightly into the thing nobody reads — indistinguishable from the silence."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.note = self.tmp / "comms" / "dead-letter.md"

    def _quarantined(self, key="alpha::2026-01-10-gizmo-setup"):
        st = {}
        for i in range(3):
            st = runner.fold_brief_state(st, key, fingerprint="f1", result="failed",
                                         stamp=f"2026-08-0{i+1}T03:15:00Z",
                                         detail="verify: KeyError gizmo")
        return st

    def test_no_quarantine_writes_no_note(self):
        action, _ = runner.sync_dead_letter_note({}, self.note)
        self.assertEqual(action, "noop")
        self.assertFalse(self.note.exists())

    def test_a_quarantine_writes_a_note_naming_the_brief_and_the_verdict(self):
        action, p = runner.sync_dead_letter_note(self._quarantined(), self.note)
        self.assertEqual(action, "written")
        body = p.read_text(encoding="utf-8")
        self.assertIn("alpha::2026-01-10-gizmo-setup", body)
        self.assertIn("KeyError gizmo", body)

    def test_the_note_says_nothing_was_left_half_applied(self):
        """The first question a reader has. Every failed adoption was reset to its
        pre-adoption HEAD and the brief is still pending — quarantine stops the runner
        attempting it, it does not discard anything."""
        _, p = runner.sync_dead_letter_note(self._quarantined(), self.note)
        self.assertIn("Nothing was left half-applied", p.read_text(encoding="utf-8"))

    def test_the_note_says_how_to_release_a_brief(self):
        """A note describing a trap with no exit is worse than no note."""
        _, p = runner.sync_dead_letter_note(self._quarantined(), self.note)
        self.assertIn("Editing the brief releases it", p.read_text(encoding="utf-8"))

    def test_the_note_clears_itself_when_the_last_brief_clears(self):
        runner.sync_dead_letter_note(self._quarantined(), self.note)
        self.assertTrue(self.note.exists())
        action, _ = runner.sync_dead_letter_note({}, self.note)
        self.assertEqual(action, "cleared")
        self.assertFalse(self.note.exists())

    def test_the_note_is_rewritten_not_accreted(self):
        st = self._quarantined()
        runner.sync_dead_letter_note(st, self.note)
        first = self.note.read_text(encoding="utf-8")
        runner.sync_dead_letter_note(st, self.note)
        self.assertEqual(self.note.read_text(encoding="utf-8"), first)
        self.assertEqual(len(list(self.note.parent.glob("*.md"))), 1)

    def test_every_quarantined_brief_is_listed(self):
        st = self._quarantined("a::one")
        st.update(self._quarantined("b::two"))
        _, p = runner.sync_dead_letter_note(st, self.note)
        body = p.read_text(encoding="utf-8")
        self.assertIn("a::one", body)
        self.assertIn("b::two", body)

    def test_dead_letter_entries_ignores_briefs_still_being_retried(self):
        st = runner.fold_brief_state({}, "r::b", fingerprint="f1", result="failed",
                                     stamp="S1")
        self.assertEqual(runner.dead_letter_entries(st), [])


class EvidenceSurvivesEveryHopTest(unittest.TestCase):
    """WI-0371. This runner cut the same captured text at THREE separate hops, so the
    record at the far end — the dead-letter comms note operator actually reads — was a tail
    of a tail of a tail and nothing anywhere said so.

    The shape of the defect is a dead-letter note holding a 400-char window that began
    mid-token (``nd']=='gizmo-server'``), having already lost the traceback header that
    would have explained it. A reader could not tell that from a verify that genuinely
    printed one cryptic line.

    These tests drive the REAL hops, not a re-implementation of them:

      hop 1  `preflight_auth`      — a spawned binary's output, cut to 200
      hop 2  `fold_brief_state`    — the ledger's `last_detail`, cut to 400
      hop 3  `sync_dead_letter_note` — the note's `Last verdict:` row, cut to 200

    What the far end must state is the loss against the ORIGINAL size. Reporting it
    against whatever the previous hop handed over is the compounding failure itself,
    wearing the costume of an honest marker.
    """

    KEY = "alpha::2026-01-10-gizmo-setup"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.note = self.tmp / "comms" / "dead-letter.md"
        # No digits anywhere: `is_auth_error` matches "401", and a stray one in the
        # filler would route this down the auth branch and test nothing it claims to.
        self.blob = "the adoption transcript said something here and it mattered\n" * 90
        self.bin = self.tmp / "fake-claude"
        self.bin.write_text(
            "#!/bin/sh\ncat <<'EOF' >&2\n" + self.blob + "EOF\nexit 1\n",
            encoding="utf-8")
        self.bin.chmod(0o755)

    def _hop1(self):
        ok, detail = runner.preflight_auth(str(self.bin), timeout_s=60)
        self.assertFalse(ok)
        self.assertIn("claude preflight exit", detail,
                      "the filler tripped is_auth_error — this test would prove nothing")
        return detail

    def test_hop_one_marks_its_own_cut(self):
        detail = self._hop1()
        note = evidence.describe_cut(detail)
        self.assertIsNotNone(note, "the spawned binary's output was cut silently")
        self.assertGreaterEqual(note["total"], len(self.blob) - 2)
        self.assertNotIn("\n", detail, "a verdict is one line")

    def test_the_middle_hop_does_not_launder_an_earlier_cut(self):
        """`last_detail = detail[:400]` took no further bytes off a 225-char sentence
        and so looked like a no-op — while dropping the note that 5000 chars had
        already gone. A hop that cuts nothing must still carry the loss."""
        detail = self._hop1()
        st = runner.fold_brief_state({}, self.KEY, fingerprint="f1", result="failed",
                                     stamp="2026-08-01T03:15:00Z", detail=detail)
        stored = st[self.KEY]["last_detail"]
        self.assertTrue(evidence.was_cut(stored))
        self.assertGreaterEqual(evidence.describe_cut(stored)["total"],
                                len(self.blob) - 2)

    def test_the_note_at_the_far_end_says_it_was_cut_and_by_how_much(self):
        """The acceptance clause, end to end: three real hops, and the artifact a human
        opens still reports the loss against the ORIGINAL size."""
        detail = self._hop1()
        st = {}
        for i in range(3):
            st = runner.fold_brief_state(st, self.KEY, fingerprint="f1",
                                         result="failed",
                                         stamp="2026-08-0%dT03:15:00Z" % (i + 1),
                                         detail=detail)
        action, path = runner.sync_dead_letter_note(st, self.note)
        self.assertEqual(action, "written")
        body = path.read_text(encoding="utf-8")

        row = [l for l in body.splitlines() if "Last verdict:" in l]
        self.assertEqual(len(row), 1, body)
        note = evidence.describe_cut(row[0].split("`")[1])
        self.assertIsNotNone(note, "the far-end record cut the verdict silently:\n" + row[0])
        self.assertGreaterEqual(note["total"], len(self.blob) - 2)
        # The number a hop that re-based on its input would have printed instead.
        self.assertNotEqual(note["total"], 400)
        self.assertNotEqual(note["total"], 200)

    def test_a_verify_that_fits_is_not_marked(self):
        """The other half of the contract. If a complete verdict could come back
        marked, the marker stops meaning anything and this whole class is decoration."""
        kind, verdict, raw = runner.run_verify(self.tmp, "echo one small line; exit 1")
        self.assertEqual(kind, "failed")
        self.assertFalse(evidence.was_cut(raw), raw)

    def test_a_long_verify_output_is_marked_where_it_rides_the_record(self):
        """`run_verify` returns the raw tail that rides the JSONL outcome record. It is
        demoted, never destroyed — but a demotion nobody is told about is a deletion."""
        cmd = ("python3 -c \"print('x' * %d)\"; exit 1"
               % (runner.VERIFY_OUTPUT_KEPT + 5000))
        kind, verdict, raw = runner.run_verify(self.tmp, cmd)
        self.assertEqual(kind, "failed")
        self.assertTrue(evidence.was_cut(raw), raw[:200])
        self.assertGreater(evidence.describe_cut(raw)["total"],
                           runner.VERIFY_OUTPUT_KEPT)



class RunnerReceiptTest(unittest.TestCase):
    """WI-0223. The R3 agent path used to file briefs by telling the headless model to
    move the file — `ADOPT_PROMPT` step 1 said so in words. A model moving a file is a
    self-report, and WI-0041 is the finding that the filing is exactly what must not be
    self-reported: a member's enrollment brief reached `applied/` with none of its work
    done, which removed it from this runner's own queue and turned a failed enrollment
    into a silent one. The guard WI-0041 then built had to go blind on every `manual`
    brief precisely because this path never stamped one.

    These pin the receipt: that it exists, that it is the ENGINE's shape so one detector
    reads both paths, that it never invents the version it records, and that it survives
    a session which ignored the prompt."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.repo = self.tmp / "member"
        self.pending = self.repo / "proposed-edits" / "member-arch" / "pending"
        self.pending.mkdir(parents=True)
        self.applied = self.pending.parent / "applied"
        self.config = {"inbox": "proposed-edits/member-arch/pending",
                       "role_doc": "member-arch.md"}
        (self.repo / "member-arch.md").write_text(
            "# Member Architect\n\n**Version:** 4.2.0\n**Status:** Active\n",
            encoding="utf-8")

    def _brief(self, text, name="b.md"):
        path = self.pending / name
        path.write_text(text, encoding="utf-8")
        return path

    FENCED = "---\nedit-id: 2026-09-04-x\napply: manual\nverify: true\n---\n\n# Body\n"

    def _file(self, brief, version="4.2.0"):
        return runner.file_and_stamp_brief(
            self.repo, self.config, brief, "2026-09-04T05:00:00Z", version)

    def test_a_fenced_brief_is_filed_with_all_four_receipt_lines(self):
        dest, detail = self._file(self._brief(self.FENCED))
        self.assertEqual(dest, self.applied / "b.md")
        self.assertFalse((self.pending / "b.md").exists(), "it must leave pending/")
        text = dest.read_text(encoding="utf-8")
        for line in ("applied: 2026-09-04T05:00:00Z", "applied-at-version: 4.2.0",
                     "applied-by: runner", "state: applied"):
            self.assertIn(line, text)
        self.assertIn("edit-id: 2026-09-04-x", text, "the brief's own header survives")
        self.assertIn("# Body", text)

    def test_the_receipt_satisfies_the_engine_side_detector(self):
        """`session.py _applied_without_a_receipt()` keys on `^state:\\s*applied\\s*$`.
        Writing a different shape here would give the R3 path a receipt no existing
        detector could read — the same gap in a new spelling."""
        dest, _ = self._file(self._brief(self.FENCED))
        self.assertTrue(
            re.search(r"^state:\s*applied\s*$", dest.read_text(encoding="utf-8"), re.M))

    def test_applied_by_distinguishes_the_runner_from_the_engine(self):
        """Engine and runner are different actors reaching the same terminal state. A
        receipt that cannot say which one filed the brief cannot answer WI-0041."""
        dest, _ = self._file(self._brief(self.FENCED))
        self.assertIn("applied-by: runner", dest.read_text(encoding="utf-8"))

    def test_a_fenceless_brief_has_a_fence_minted_for_it(self):
        """`session.py file_applied_brief()` silently SKIPS the stamp when there is no
        `---` block and moves the file anyway — which is how 28 of the 42 briefs in
        federation-arch's `applied/` became permanently unauditable with nothing ever
        reporting a problem."""
        dest, _ = self._file(self._brief("# A pre-schema brief\n\nProse only.\n"))
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("state: applied", text)
        self.assertIn("# A pre-schema brief", text, "the brief's own content is intact")

    def test_the_version_comes_from_the_members_real_role_doc(self):
        self.assertEqual(runner.member_role_doc_version(self.repo, self.config), "4.2.0")

    def test_an_unreadable_role_doc_yields_unknown_and_never_a_guess(self):
        """A receipt is only worth reading because nothing in it was inferred. A guessed
        version would make every stamp exactly as trustworthy as the self-report it
        replaces ([`no-fabricated-data`])."""
        (self.repo / "member-arch.md").unlink()
        self.assertIsNone(runner.member_role_doc_version(self.repo, self.config))
        dest, _ = self._file(self._brief(self.FENCED),
                             version=runner.member_role_doc_version(self.repo, self.config))
        self.assertIn("applied-at-version: unknown", dest.read_text(encoding="utf-8"))

    def test_a_brief_the_session_already_moved_is_stamped_in_place(self):
        """The prompt is an instruction to a MODEL. A receipt mechanism that only works
        when the model complies fails on exactly the sessions worth auditing."""
        self.applied.mkdir(parents=True)
        (self.applied / "b.md").write_text(self.FENCED, encoding="utf-8")
        dest, detail = self._file(self.pending / "b.md")
        self.assertEqual(dest, self.applied / "b.md")
        self.assertIn("state: applied", dest.read_text(encoding="utf-8"))
        self.assertIn("in place", detail)

    def test_a_brief_in_neither_directory_returns_a_reason_and_invents_nothing(self):
        dest, detail = self._file(self.pending / "ghost.md")
        self.assertIsNone(dest)
        self.assertIn("neither", detail)
        self.assertFalse(self.applied.exists() and any(self.applied.iterdir()))

    def test_an_already_stamped_brief_is_left_exactly_as_it_was(self):
        self.applied.mkdir(parents=True)
        stamped = "---\napplied: 2026-01-01T00:00:00Z\nstate: applied\n---\n\n# B\n"
        (self.applied / "b.md").write_text(stamped, encoding="utf-8")
        dest, detail = self._file(self.pending / "b.md")
        self.assertEqual(dest.read_text(encoding="utf-8"), stamped,
                         "re-stamping would overwrite the receipt it is meant to preserve")
        self.assertIn("already carried a receipt", detail)

    def test_a_member_with_no_inbox_is_a_named_refusal_not_a_crash(self):
        dest, detail = runner.file_and_stamp_brief(
            self.repo, {}, self.pending / "b.md", "2026-09-04T05:00:00Z", None)
        self.assertIsNone(dest)
        self.assertIn("no inbox", detail)

    def test_the_prompt_no_longer_tells_the_model_to_file_the_brief(self):
        """The behaviour under test lives in a prompt, so the prompt is what gets pinned —
        an instruction is still the mechanism here, and a silent revert of this line puts
        the self-report back with nothing failing."""
        self.assertNotIn("Move the brief from your inbox", runner.ADOPT_PROMPT)
        self.assertIn("LEAVE THE BRIEF WHERE IT IS", runner.ADOPT_PROMPT)
        self.assertIn("do NOT move it out of `pending/`", runner.ADOPT_PROMPT)


# A synthetic setup brief's verify command, shaped like the one that produced this
# defect (the tool `gizmo` and its verbs are invented). Every assert in it is
# message-less, and `m['mcpServers']['gizmo']` is the subscript that raises.
ENROLLMENT_VERIFY = (
    "python3 -c \"import json;c=json.load(open('session.config.json'));"
    "m=json.load(open('.mcp.json'));h=c['settings_extras']['hooks'];"
    "assert m['mcpServers']['gizmo']['command']=='gizmo-server';"
    "assert any('gizmo release' in x['command'] for e in h['SessionEnd'] for x in e['hooks']);"
    "print('gizmo setup wired')\""
)

# The tail the OLD code logged for such a crash in its dead-letter comms note.
# It begins mid-token because the 400-char tail had already cut the traceback header off.
ENROLLMENT_LOGGED_TAIL = (
    "nd']=='gizmo-server';assert any('gizmo release' in x['command'] for e in h['SessionEnd'] "
    "for x in e['hooks']);print('gizmo setup wired')\n"
    "                              ~~~~~~~~~~~~~~~^^^^^^^^^^\n"
    "KeyError: 'gizmo'"
)

# The same crash as the runner now sees it — complete output, header intact.
ENROLLMENT_FULL_OUTPUT = (
    "Traceback (most recent call last):\n"
    "  File \"<string>\", line 1, in <module>\n"
    "    assert m['mcpServers']['gizmo']['command']=='gizmo-server'\n"
    "           ~~~~~~~~~~~~~~~^^^^^^^^^^\n"
    "KeyError: 'gizmo'"
)


class VerifyVerdictTest(unittest.TestCase):
    """WI-0304 B1 — a failed verify owes a one-line verdict, never a stack trace.

    The asymmetry these tests protect: over-classifying is as bad as under-classifying.
    A legitimate silent verify wrongly called `defective` would be quarantined on its
    first honest failure, which is worse than the defect being fixed here."""

    def test_the_setup_keyerror_becomes_a_verdict_not_a_traceback(self):
        kind, verdict = runner.verify_verdict(ENROLLMENT_VERIFY, 1, ENROLLMENT_FULL_OUTPUT)
        self.assertEqual(kind, "defective")
        self.assertIn("DEFECTIVE BRIEF", verdict)
        self.assertIn("KeyError", verdict)
        self.assertIn("gizmo", verdict)
        self.assertNotIn("Traceback", verdict)
        self.assertNotIn("~~~", verdict, "the caret/tilde frame art is not a verdict")

    def test_a_verdict_is_always_exactly_one_line(self):
        """The contract is 'one line'. Multi-line output is the case that breaks a
        convention-only promise, so it is the case that gets pinned."""
        for rc, out in ((1, ENROLLMENT_FULL_OUTPUT),
                        (1, "line one\nline two\nline three"),
                        (1, ""),
                        (127, "sh: nope: command not found"),
                        (0, "fine\nand dandy")):
            _, verdict = runner.verify_verdict("cmd\nwith a newline in it", rc, out)
            self.assertNotIn("\n", verdict, f"rc={rc} leaked a newline into the verdict")
            self.assertTrue(verdict.strip())

    def test_every_verdict_names_the_command_that_was_checked(self):
        for rc, out in ((1, ENROLLMENT_FULL_OUTPUT), (1, ""), (1, "nope"), (127, ""), (0, "")):
            _, verdict = runner.verify_verdict("test -f adopted.txt", rc, out)
            self.assertIn("test -f adopted.txt", verdict,
                          "the command is the only account of intent that always exists")

    def test_an_assert_with_a_message_is_a_failed_adoption_not_a_defective_brief(self):
        """The distinction the whole item rests on. This verify ANSWERED — it named the
        condition — so the brief is sound and the work is wrong."""
        out = ("Traceback (most recent call last):\n"
               "  File \"<string>\", line 1, in <module>\n"
               "AssertionError: widget MCP server not wired into .mcp.json")
        kind, verdict = runner.verify_verdict("python3 -c '...'", 1, out)
        self.assertEqual(kind, "failed")
        self.assertIn("widget MCP server not wired into .mcp.json", verdict)
        self.assertNotIn("DEFECTIVE", verdict)

    def test_a_bare_assertionerror_is_defective_because_it_names_nothing(self):
        out = ("Traceback (most recent call last):\n"
               "  File \"<string>\", line 1, in <module>\n"
               "AssertionError")
        kind, verdict = runner.verify_verdict("python3 -c 'assert x'", 1, out)
        self.assertEqual(kind, "defective")
        self.assertIn("bare AssertionError", verdict)

    def test_a_silent_nonzero_check_stays_failed_and_is_not_reclassified(self):
        """`test -f` / `grep -q` print nothing BY DESIGN and are the good, self-describing
        verifies. Calling their silence defective would quarantine the best commands in
        the fleet — and most real failure notes are exactly this shape."""
        kind, verdict = runner.verify_verdict("test -f adopted.txt", 1, "")
        self.assertEqual(kind, "failed")
        self.assertIn("printed nothing", verdict)
        self.assertIn("test -f adopted.txt", verdict)

    def test_a_test_runners_caught_traceback_is_not_a_crash_of_the_verify(self):
        """A suite prints tracebacks for failing tests and then a summary. That run
        answered the question. Anchoring only on the header would call every failing
        test suite in the fleet a defective brief."""
        out = ("Traceback (most recent call last):\n"
               "  File \"t.py\", line 3, in test_x\n"
               "AssertionError: inner assertion\n"
               "\n"
               "FAILED (failures=1)")
        kind, verdict = runner.verify_verdict("python3 -m unittest discover", 1, out)
        self.assertEqual(kind, "failed")
        self.assertIn("FAILED (failures=1)", verdict)

    def test_a_command_the_shell_cannot_run_is_defective(self):
        kind, verdict = runner.verify_verdict(
            "widget-check --strict", 127, "sh: widget-check: command not found")
        self.assertEqual(kind, "defective")
        self.assertIn("could not run", verdict)

    def test_a_syntaxerror_in_the_one_liner_is_defective(self):
        out = ("  File \"<string>\", line 1\n"
               "    assert (\n"
               "SyntaxError: '(' was never closed")
        # No traceback header — a SyntaxError at compile time prints none. Exit 1 with
        # output, so it lands on `failed`; the DELIVERY lint is what catches this shape,
        # and this test pins that the runtime side at least emits a readable verdict.
        kind, verdict = runner.verify_verdict("python3 -c 'assert ('", 1, out)
        self.assertNotIn("\n", verdict)
        self.assertIn("SyntaxError", verdict)

    def test_a_passing_verify_says_so(self):
        kind, verdict = runner.verify_verdict("test -f x", 0, "")
        self.assertEqual(kind, "pass")
        self.assertIn("passed", verdict)

    def test_prose_that_merely_looks_like_an_exception_is_not_one(self):
        """Without the traceback-header requirement, a check that PRINTS the words
        `KeyError: 'gizmo'` as its own diagnostic — the good behaviour we are asking
        authors for — would be classified as having crashed."""
        kind, verdict = runner.verify_verdict(
            "python3 check.py", 1, "checked .mcp.json for gizmo\nKeyError: 'gizmo'")
        self.assertEqual(kind, "failed")

    def test_a_truncated_traceback_cannot_be_read_and_says_so_by_not_claiming(self):
        """The exact text the OLD runner logged, with its header already truncated away.
        `last_exception` must return None rather than guess — which is WHY `run_verify`
        classifies on the full output and truncates only for storage."""
        self.assertIsNone(runner.last_exception(ENROLLMENT_LOGGED_TAIL))
        self.assertIsNotNone(runner.last_exception(ENROLLMENT_FULL_OUTPUT))

    def test_a_chained_exception_reports_the_one_that_escaped(self):
        out = ("Traceback (most recent call last):\n"
               "  File \"<string>\", line 1\n"
               "KeyError: 'gizmo'\n"
               "\n"
               "During handling of the above exception, another exception occurred:\n"
               "\n"
               "Traceback (most recent call last):\n"
               "  File \"<string>\", line 1\n"
               "RuntimeError: could not read the registry")
        self.assertEqual(runner.last_exception(out)[0], "RuntimeError")


class DefectiveBriefLedgerTest(unittest.TestCase):
    """WI-0304 B1 — a defective brief quarantines on sight, and releases on a fix."""

    def _fold(self, prior, result, fingerprint="fp1", stamp="S1"):
        return runner.fold_brief_state(prior, "alpha::b", fingerprint=fingerprint,
                                       result=result, stamp=stamp, detail="d")

    def test_one_defective_sweep_quarantines_where_three_failures_are_needed(self):
        """The counter buys evidence for 'this will not start working on its own'. A
        crashed check supplies that evidence in one night; nights two and three cost a
        full claude -p session each and teach nobody anything."""
        after_fail = self._fold({}, "failed")
        self.assertIsNone(after_fail["alpha::b"].get("dead_since"),
                          "one ordinary failure must NOT quarantine")
        after_defective = self._fold({}, "defective")
        self.assertTrue(after_defective["alpha::b"].get("dead_since"))
        self.assertTrue(after_defective["alpha::b"].get("defective"))
        self.assertTrue(runner.is_dead_lettered(after_defective, "alpha::b", "fp1"))

    def test_a_corrected_brief_is_released_with_no_hand_clearing(self):
        """The escape hatch needs no new machinery: the ledger is content-keyed, so the
        re-authored brief is a NEW brief to the counter."""
        dead = self._fold({}, "defective")
        self.assertFalse(runner.is_dead_lettered(dead, "alpha::b", "fp2-corrected"))

    def test_an_adoption_that_later_succeeds_clears_the_defective_record(self):
        dead = self._fold({}, "defective")
        self.assertEqual(self._fold(dead, "adopted"), {})

    def test_a_runner_error_still_says_nothing_about_the_brief(self):
        self.assertEqual(self._fold({}, "error"), {})

    def test_the_dead_letter_note_does_not_invent_a_failure_count(self):
        """A defective brief has ONE failure. Printing '3 consecutive failures' next to it
        would be fabricated data, and would point the reader at the adoption when the fix
        is in the brief."""
        row = self._row_for(self._fold({}, "defective"))
        self.assertIn("DEFECTIVE BRIEF", row)
        self.assertIn("quarantined on first sight", row)
        self.assertNotIn("consecutive failures", row,
                         "the row is where a reader reads the count — the note's standing "
                         "prose about the threshold is not a claim about this brief")

    def test_the_note_still_reads_correctly_for_an_ordinary_quarantine(self):
        state = {}
        for i in range(3):
            state = self._fold(state, "failed", stamp=f"S{i}")
        row = self._row_for(state)
        self.assertIn("3 consecutive failures", row)
        self.assertNotIn("DEFECTIVE BRIEF", row)

    def _row_for(self, state):
        """The note's row for `alpha::b` — the line a reader reads about THIS brief,
        as distinct from the note's standing prose about the mechanism."""
        with tempfile.TemporaryDirectory() as d:
            action, path = runner.sync_dead_letter_note(
                state, path=pathlib.Path(d) / "dl.md")
            self.assertEqual(action, "written")
            body = path.read_text(encoding="utf-8")
        rows = [ln for ln in body.splitlines() if ln.startswith("- **alpha::b**")]
        self.assertEqual(len(rows), 1, f"expected exactly one row for the brief in:\n{body}")
        return rows[0]


if __name__ == "__main__":
    unittest.main()
