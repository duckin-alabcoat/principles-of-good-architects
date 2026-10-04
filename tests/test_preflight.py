"""WI-0149 — `poga preflight`, the verb that checks what a fresh machine actually fails.

The four walls devbox hit in one hour (session ~161) each had the same shape: nothing
errored until operator hit it, and three of the four ended with a command in his terminal.
So the contract worth pinning is not "does each check work" — it is that **a check which
could not run never reads as a pass**. That is the property the walls exploited, and it
is the one a future edit is most likely to quietly break by adding a friendly fallback.

Pinned here:

  1. Each check's three verdicts are reachable and distinct — OK / FAIL / UNKNOWN.
  2. `loggedIn: false` is a FAIL. This is source-pinned rather than incidental: the
     first cut of `_pf_auth` substring-matched "loggedin" against the lowercased output,
     which is present in the logged-OUT document too, so a logged-out machine reported
     live. The regression shape, not just the fixed behaviour (ADR-0089).
  3. An unrecognised auth payload is UNKNOWN, never OK — the output shape belongs to
     another product and will change without asking us.
  4. `--quick` reports the gate SKIPPED and exits 2. A skipped check is not a passed one.
  5. Exit codes are the three-way split: 0 ready, 1 something failed, 2 cannot tell.
  6. The `poga` wrapper actually forwards the verb, and the verb is named in its help
     and in its unknown-verb roster — an unrouted verb is indistinguishable from a
     missing one at the only moment anybody types it.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402
import harness_fixture  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
POGA = ROOT / "poga"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


AUTH_LIVE = json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                        "email": "someone@example.com", "subscriptionType": "example-plan"})
AUTH_OUT = json.dumps({"loggedIn": False, "authMethod": "claude.ai"})


class RuntimeQuarantineTest(unittest.TestCase):
    """The macOS branch. Pinned to darwin so it runs — and means the same — on a Linux
    box, where the real check is a single not-applicable SKIPPED row (WI-0468,
    tests/test_linux_portability.py)."""

    def setUp(self):
        plat = mock.patch.object(sys, "platform", "darwin")
        plat.start()
        self.addCleanup(plat.stop)

    def test_registry_rows_quarantined_clean_and_unresolvable(self):
        paths = {"claude": "/opt/bin/claude", "codex": "/opt/bin/codex",
                 "xattr": "/usr/bin/xattr"}
        with mock.patch.object(session.shutil, "which", side_effect=paths.get), \
             mock.patch.object(session.subprocess, "run", side_effect=[
                 _completed("0081;quarantine"),
                 _completed(returncode=1, stderr="No such xattr: com.apple.quarantine")]) as probe:
            rows = session._pf_runtime_quarantine()
        self.assertEqual([r.as_dict() for r in rows], [
            {"check": "runtime-quarantine", "verdict": "FAIL",
             "detail": "/opt/bin/claude: com.apple.quarantine is set.",
             "remedy": "xattr -d com.apple.quarantine /opt/bin/claude"},
            {"check": "runtime-quarantine", "verdict": "OK",
             "detail": "/opt/bin/codex: com.apple.quarantine is not set.", "remedy": ""},
            {"check": "runtime-quarantine", "verdict": "UNCHECKED",
             "detail": "agy: not installed or not resolvable on PATH.", "remedy": ""}])
        self.assertEqual([c.args[0] for c in probe.call_args_list], [
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", paths[c]]
            for c in ("claude", "codex")])
        self.assertTrue(all(0 < c.kwargs["timeout"] <= 5 for c in probe.call_args_list))

    def test_removal_command_quotes_binary_paths(self):
        with mock.patch.object(session.shutil, "which", return_value="/Applications/Agent CLI/agy"), \
             mock.patch.object(session.subprocess, "run", return_value=_completed()):
            rows = session._pf_runtime_quarantine()
        self.assertEqual(rows[0].remedy,
                         "xattr -d com.apple.quarantine '/Applications/Agent CLI/agy'")

    def test_unavailable_xattr_and_probe_failures_are_unchecked(self):
        for response, reason in [
            (None, "xattr unavailable on PATH"),
            (_completed(returncode=1, stderr="Permission denied"), "Permission denied"),
            (OSError("cannot execute"), "cannot execute"),
            (subprocess.TimeoutExpired("xattr", 5), "timed out")]:
            with self.subTest(reason=reason), \
                 mock.patch.object(session.shutil, "which", side_effect=lambda c:
                                   None if c == "xattr" and response is None else "/bin/" + c), \
                 mock.patch.object(session.subprocess, "run") as probe:
                if isinstance(response, Exception):
                    probe.side_effect = response
                else:
                    probe.return_value = response
                rows = session._pf_runtime_quarantine()
                self.assertEqual(len(rows), len(session.RUNTIMES))
                for row in rows:
                    self.assertEqual(row.verdict, "UNCHECKED")
                    self.assertIn(reason, row.detail)
                if response is None:
                    probe.assert_not_called()

    def test_quarantine_is_report_only_even_with_guided_repair(self):
        row = session._PreflightResult("runtime-quarantine", "FAIL", "quarantined",
                                       "xattr -d com.apple.quarantine /bin/agy")
        with mock.patch.object(session.subprocess, "run") as run, \
             mock.patch.object(session, "_repair_claude_first_run") as guided:
            rows, narration = session._preflight_repair(
                [row], allow_guided=True, checks_by_name={})
        self.assertEqual(session.PREFLIGHT_REPAIRS[row.name].kind, session.REPAIR_NONE)
        self.assertEqual(rows, [row])
        self.assertEqual(narration, [])
        run.assert_not_called()
        guided.assert_not_called()


class AuthCheckTest(unittest.TestCase):
    """The check that shipped wrong once. Every branch pinned."""

    def setUp(self):
        # `_pf_auth` answers UNKNOWN under the land gate before it looks at anything
        # (ADR-0119 D4), and this suite runs under that gate. Every case here asserts the
        # outside-the-gate answer, so the ambient flag is cleared, as in the fixtures below.
        env = mock.patch.dict(os.environ, {})
        env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(env.stop)

    def test_logged_in_is_ok(self):
        with mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed(AUTH_LIVE)):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)
        self.assertIn("someone@example.com", r.detail)

    def test_logged_out_is_fail_not_ok(self):
        """THE REGRESSION. `"loggedIn": false` contains the substring "loggedin", so a
        text match reports a logged-out machine as live — a wrong answer shaped exactly
        like a right one, on the check whose entire job is telling those apart."""
        self.assertIn("loggedin", AUTH_OUT.lower())      # the trap is really present
        with mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed(AUTH_OUT)):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("claude auth login", r.remedy)

    def test_unparseable_payload_is_unknown_not_ok(self):
        with mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed("Logged in as someone")):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_json_without_boolean_loggedin_is_unknown(self):
        with mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed(json.dumps({"status": "ok"}))):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_missing_binary_is_fail(self):
        with mock.patch.object(session.shutil, "which", return_value=None):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_timeout_is_unknown_not_fail(self):
        """No answer is not a failed login. Calling it FAIL would send someone to
        re-authenticate a machine whose only problem is the network."""
        with mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("claude", 20)):
            r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class ClaudeJsonChecksTest(unittest.TestCase):
    """Walls 2 and 3 — both read `~/.claude.json`, so both must distinguish 'absent'
    from 'present and false' from 'unreadable'."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "repo"
        self.root.mkdir()

    def _with_home(self, payload):
        """Point Path.home() at a temp dir carrying (or not) a .claude.json."""
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        if payload is not None:
            (home / ".claude.json").write_text(
                payload if isinstance(payload, str) else json.dumps(payload),
                encoding="utf-8")
        return mock.patch.object(session.Path, "home", staticmethod(lambda: home))

    def test_trust_accepted_is_ok(self):
        payload = {"projects": {str(self.root): {"hasTrustDialogAccepted": True}}}
        with self._with_home(payload), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)

    def test_trust_not_accepted_is_fail(self):
        payload = {"projects": {str(self.root): {"hasTrustDialogAccepted": False}}}
        with self._with_home(payload), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_unknown_project_is_fail_and_names_the_launch_symptom(self):
        """The wall as operator met it: a failed `poga` launch, not a dialog. The remedy has
        to name the path to open, because that is the step he otherwise has to work out."""
        with self._with_home({"projects": {}}), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("Workspace trust not yet accepted", r.detail)
        self.assertIn(str(self.root), r.remedy)

    def test_missing_claude_json_is_unknown_for_trust(self):
        """Absent file: trust is UNDETERMINED, not refused. The distinction matters —
        one says 'go accept a dialog', the other says 'this check told you nothing'."""
        with self._with_home(None), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_trust_checks_the_main_checkout_not_the_lane(self):
        """poga cds to the repo root and asks the runtime to make the worktree there, so
        the trusted path must be the MAIN checkout. Trusting only the lane would pass on
        a machine where the launch still cannot happen."""
        lane = self.root / ".claude" / "worktrees" / "poga-1"
        lane.mkdir(parents=True)
        payload = {"projects": {str(lane): {"hasTrustDialogAccepted": True}}}
        with self._with_home(payload), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_onboarding_absent_flag_is_fail(self):
        with self._with_home({"projects": {}}):
            r = session._pf_onboarding()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("browser", r.detail)

    def test_onboarding_complete_is_ok(self):
        with self._with_home({"hasCompletedOnboarding": True, "projects": {}}):
            r = session._pf_onboarding()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)

    def test_onboarding_missing_file_is_fail_because_the_wizard_will_run(self):
        """Unlike trust, an absent file here is not undetermined — no file means the
        wizard runs, which is the failure itself."""
        with self._with_home(None):
            r = session._pf_onboarding()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_corrupt_claude_json_is_unknown_for_trust(self):
        with self._with_home("{not json"), \
             mock.patch.object(session, "_shared_work_root", return_value=self.root):
            r = session._pf_workspace_trust()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class DataRootCheckTest(unittest.TestCase):
    """The gitignored half. Its whole point is that absent reads as empty, and an empty
    inbox is indistinguishable from an unreachable one unless something says so."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_inbox_is_fail_and_says_it_is_not_the_same_as_empty(self):
        cfg = {"inbox": "inbox/pending", "user_profile": "users/operator/profile.md"}
        with mock.patch.object(session, "CFG", cfg), \
             mock.patch.object(session, "_shared_work_root", return_value=self.tmp):
            r = session._pf_data_root()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("not the same as none pending", r.detail)

    def test_present_paths_are_ok(self):
        (self.tmp / "inbox" / "pending").mkdir(parents=True)
        (self.tmp / "users" / "operator").mkdir(parents=True)
        (self.tmp / "users" / "operator" / "profile.md").write_text("x", encoding="utf-8")
        cfg = {"inbox": "inbox/pending", "user_profile": "users/operator/profile.md"}
        with mock.patch.object(session, "CFG", cfg), \
             mock.patch.object(session, "_shared_work_root", return_value=self.tmp):
            r = session._pf_data_root()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)

    def test_nothing_declared_is_unknown_not_ok(self):
        """Zero checks passing vacuously is the `ship-the-detector-with-the-capability`
        trap: a not-applicable branch that reads as a pass certifies a gap."""
        with mock.patch.object(session, "CFG", {}), \
             mock.patch.object(session, "_shared_work_root", return_value=self.tmp):
            r = session._pf_data_root()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class TerminfoCheckTest(unittest.TestCase):
    def test_unset_term_is_unknown_not_ok(self):
        with mock.patch.dict(session.os.environ, {"TERM": ""}, clear=False):
            r = session._pf_terminfo()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_unresolvable_term_is_fail_and_names_the_transfer(self):
        with mock.patch.dict(session.os.environ, {"TERM": "xterm-nonesuch"}, clear=False), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed("", returncode=1)):
            r = session._pf_terminfo()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("tic -x", r.remedy)

    def test_entry_missing_kbs_is_fail(self):
        """Resolving is not enough — the devbox entry that made backspace dead would
        have resolved. The capability is checked by name."""
        with mock.patch.dict(session.os.environ, {"TERM": "xterm-x"}, clear=False), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_completed("xterm-x|X,\n\tcols#80,\n")):
            r = session._pf_terminfo()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_missing_infocmp_is_unknown_not_fail(self):
        """Absence of the tool is not absence of the entry."""
        with mock.patch.dict(session.os.environ, {"TERM": "xterm-x"}, clear=False), \
             mock.patch.object(session.subprocess, "run", side_effect=FileNotFoundError):
            r = session._pf_terminfo()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class StrandedRuntimeCheckTest(unittest.TestCase):
    def test_auth_login_flow_is_flagged(self):
        ps = ("  4501 30:12 /opt/tools/bin/claude auth login\n"
              "  502 01:00 /usr/bin/vim notes.md\n")
        with mock.patch.object(session.subprocess, "run", return_value=_completed(ps)):
            r = session._pf_stranded_runtimes()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("4501", r.detail)

    def test_ordinary_runtime_sessions_are_not_flagged(self):
        """A check that fires on healthy state is one people learn to ignore. This very
        session is a live `claude` process and must not trip it."""
        ps = ("  4501 30:12 /opt/tools/bin/claude\n"
              "  502 02:00 /opt/tools/bin/claude --worktree poga-1\n")
        with mock.patch.object(session.subprocess, "run", return_value=_completed(ps)):
            r = session._pf_stranded_runtimes()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)

    def test_ok_declares_what_it_did_not_check(self):
        with mock.patch.object(session.subprocess, "run", return_value=_completed("")):
            r = session._pf_stranded_runtimes()
        self.assertIn("NOT checked", r.detail)

    def test_ps_failure_is_unknown(self):
        with mock.patch.object(session.subprocess, "run", side_effect=OSError("no ps")):
            r = session._pf_stranded_runtimes()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class GateCheckTest(unittest.TestCase):
    def test_red_gate_is_fail(self):
        with mock.patch.object(session, "_run_gate",
                               return_value=(False, "gate:    FAIL (exit 1) — unittest")):
            r = session._pf_gate()
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("cannot land", r.detail)

    def test_green_gate_is_ok(self):
        with mock.patch.object(session, "_run_gate",
                               return_value=(True, "gate:    ok — unittest")):
            r = session._pf_gate()
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)


class ExitCodeTest(unittest.TestCase):
    """The three-way split is the whole contract. Collapsing UNKNOWN into either
    neighbour is what turns 'we did not look' into 'it is fine'."""

    def _run(self, verdicts, quick=False, quarantine=()):
        results = [session._PreflightResult(f"c{i}", v, "d", "r")
                   for i, v in enumerate(verdicts)]
        checks = [(lambda r=r: r) for r in results]
        # The subject here is the three-way exit-code contract over a set of verdicts,
        # with every check replaced — nothing in this class touches the machine. But
        # `cmd_preflight` drops its two machine checks under the land gate (ADR-0119 D4),
        # and this suite RUNS under that gate, so an ambient `POGA_GATE` would silently
        # remove the very verdict a case is asserting on. Same shape as the dispatch and
        # coord fixtures: a test that does not clear an ambient variable is testing the
        # environment it happens to be run in.
        env = mock.patch.dict(os.environ, {})
        env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(env.stop)
        with mock.patch.object(session, "_pf_terminfo", checks[0]), \
             mock.patch.object(session, "_pf_workspace_trust", checks[1]), \
             mock.patch.object(session, "_pf_onboarding", checks[2]), \
             mock.patch.object(session, "_pf_auth", checks[3]), \
             mock.patch.object(session, "_pf_data_root", checks[4]), \
             mock.patch.object(session, "_pf_stranded_runtimes", checks[5]), \
             mock.patch.object(session, "_pf_memory_pressure", checks[6]), \
             mock.patch.object(session, "_pf_gate", checks[7]), \
             mock.patch.object(session, "_pf_runtime_quarantine", return_value=list(quarantine)), \
             mock.patch.object(session, "detect_machine", return_value="TestBox"):
            args = argparse.Namespace(quick=quick, json=False)
            try:
                session.cmd_preflight(args)
            except SystemExit as e:
                return int(e.code)
            return 0

    def test_quarantine_rows_are_printed_and_unchecked_prevents_ready(self):
        rows = [session._PreflightResult("runtime-quarantine", "UNCHECKED",
                                         "agy: not installed or not resolvable on PATH.")]
        with mock.patch("builtins.print") as output:
            code = self._run([session.PREFLIGHT_OK] * 8, quarantine=rows)
        self.assertEqual(code, 2)
        self.assertIn("  UNCHECKED runtime-quarantine  agy: not installed or not resolvable on PATH.",
                      [c.args[0] for c in output.call_args_list])

    def test_all_ok_exits_zero(self):
        self.assertEqual(self._run([session.PREFLIGHT_OK] * 8), 0)

    def test_a_not_applicable_skip_does_not_make_it_cannot_tell(self):
        """WI-0468. Off macOS the quarantine check is one SKIPPED row whose subject does
        not exist there. It prints, but it is not undetermined — otherwise every Linux
        `poga preflight` would exit 2 forever, and a permanent 2 teaches skipping it."""
        rows = [session._PreflightResult("runtime-quarantine", session.PREFLIGHT_SKIPPED,
                                         "NOT checked — macOS only.", applicable=False)]
        with mock.patch("builtins.print") as output:
            code = self._run([session.PREFLIGHT_OK] * 8, quarantine=rows)
        self.assertEqual(code, 0)
        self.assertIn("  SKIPPED  runtime-quarantine  NOT checked — macOS only.",
                      [c.args[0] for c in output.call_args_list])

    def test_an_applicable_skip_still_cannot_tell(self):
        """The negative control: the exemption is the flag, not the verdict."""
        rows = [session._PreflightResult("runtime-quarantine", session.PREFLIGHT_SKIPPED,
                                         "skipped")]
        with mock.patch("builtins.print"):
            self.assertEqual(self._run([session.PREFLIGHT_OK] * 8, quarantine=rows), 2)

    def test_any_fail_exits_one(self):
        v = [session.PREFLIGHT_OK] * 8
        v[3] = session.PREFLIGHT_FAIL
        self.assertEqual(self._run(v), 1)

    def test_unknown_without_failure_exits_two(self):
        v = [session.PREFLIGHT_OK] * 8
        v[0] = session.PREFLIGHT_UNKNOWN
        self.assertEqual(self._run(v), 2)

    def test_failure_outranks_unknown(self):
        v = [session.PREFLIGHT_OK] * 8
        v[0], v[1] = session.PREFLIGHT_UNKNOWN, session.PREFLIGHT_FAIL
        self.assertEqual(self._run(v), 1)

    def test_quick_skips_the_gate_and_still_cannot_tell(self):
        """`--quick` must not be a shortcut to a green verdict."""
        self.assertEqual(self._run([session.PREFLIGHT_OK] * 8, quick=True), 2)


class LaunchGateTest(unittest.TestCase):
    """ADR-0096 D3–D7 — the gate poga runs before it allocates a lane.

    Its contract differs from the report's on every axis that matters, and each
    difference is a decision someone could plausibly "tidy" back into the other one."""

    def _gate(self, verdicts):
        results = [session._PreflightResult(n, v, f"{n} detail", f"{n} remedy")
                   for n, v in zip(("terminfo", "workspace-trust", "onboarding"), verdicts)]
        with mock.patch.object(session, "PREFLIGHT_GATE_CHECKS",
                               tuple((lambda r=r: r) for r in results)), \
             mock.patch.object(session, "detect_machine", return_value="TestBox"):
            try:
                session._preflight_gate()
            except SystemExit as e:
                return int(e.code)
            return 0

    def test_subset_is_the_instant_local_checks(self):
        """D3, source-pinned. Auth and the land gate are OUT by decision, not by
        oversight — auth reaches the network and the gate is ~90s, and this runs in
        front of every launch. A future edit that adds either should have to change
        this test and read why.

        CHANGED 2026-08-30 (ADR-0106), and this is the edit the docstring above demanded
        should have to be made deliberately. `_pf_residency` joins the subset because it
        satisfies D3's membership test exactly as the other three do: LOCAL (a config
        read), INSTANT (no process, no network) and LAUNCH-FATAL (developing on a declared
        production host is the thing being prevented). Adding it here is also what gives
        `poga resume` the same refusal, which matters because entering an existing lane on
        a production host is the identical mistake as creating one.

        CHANGED AGAIN 2026-09-19 (WI-0398) — `_pf_memory_pressure` joins, and the same
        deliberateness is owed. LOCAL: `sysctl` and `ps` read this kernel's own counters.
        INSTANT: milliseconds for the pair, against auth's 20 s network
        timeout. LAUNCH-FATAL: a lane created on a thrashing machine cannot land, and one once
        sat for hours proving it. Leaving it out of the subset would have made
        it a dashboard, and a dashboard is exactly what that session did not open.

        The two exclusions are untouched and are the half worth keeping rigid: the reason
        auth and the land gate stay out is a COST argument about every launch, and nothing
        about residency or memory pressure weakens it."""
        names = [f.__name__ for f in session.PREFLIGHT_GATE_CHECKS]
        self.assertEqual(names, ["_pf_terminfo", "_pf_workspace_trust", "_pf_onboarding",
                                 "_pf_residency", "_pf_memory_pressure"])
        self.assertNotIn("_pf_auth", names)
        self.assertNotIn("_pf_gate", names)

    def test_all_ok_exits_zero(self):
        self.assertEqual(self._gate([session.PREFLIGHT_OK] * 3), 0)

    def test_fail_refuses(self):
        self.assertEqual(
            self._gate([session.PREFLIGHT_FAIL, session.PREFLIGHT_OK,
                        session.PREFLIGHT_OK]), 1)

    def test_unknown_warns_but_does_not_refuse(self):
        """D4. Blocking on 'could not determine' would refuse every launch with $TERM
        unset — an unanswered question turned into a locked door."""
        self.assertEqual(
            self._gate([session.PREFLIGHT_UNKNOWN, session.PREFLIGHT_OK,
                        session.PREFLIGHT_OK]), 0)

    def test_clean_pass_is_silent(self):
        """It runs in front of every launch. A gate that narrates its own success is
        noise that trains people to stop reading it."""
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self._gate([session.PREFLIGHT_OK] * 3)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_refusal_names_the_check_the_remedy_and_the_bypass(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._gate([session.PREFLIGHT_FAIL, session.PREFLIGHT_OK,
                        session.PREFLIGHT_OK])
        text = err.getvalue()
        self.assertIn("REFUSING to create a lane", text)
        self.assertIn("terminfo remedy", text)
        self.assertIn(session.PREFLIGHT_BYPASS_ENV, text)

    def test_unknown_is_named_on_stderr(self):
        """Warning-and-continuing is only honest if the warning is actually printed."""
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._gate([session.PREFLIGHT_UNKNOWN, session.PREFLIGHT_OK,
                        session.PREFLIGHT_OK])
        self.assertIn("could not verify terminfo", err.getvalue())

    def test_gate_flag_is_wired_to_the_gate_not_the_report(self):
        called = {}
        with mock.patch.object(session, "_preflight_gate",
                               lambda: called.setdefault("gate", True)):
            session.cmd_preflight(argparse.Namespace(gate=True, quick=False, json=False))
        self.assertTrue(called.get("gate"))


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PogaLaunchGateWiringTest(unittest.TestCase):
    """The gate's placement in `poga` is load-bearing in three separate ways, and none
    of them is visible from the shell function itself."""

    def setUp(self):
        self.src = POGA.read_text(encoding="utf-8")

    def test_gate_runs_before_alloc_lane(self):
        """D5 / ADR-0082 D4: a refusal after the worktree and branch exist is litter
        (WI-0105), not a refusal."""
        body = self.src.split("cmd_session() {", 1)[1].split("\n}", 1)[0]
        # Match the CALL, not the word: the comment above the call names `alloc_lane`
        # too, and searching for the bare token found the comment and read the order
        # backwards. A wiring test that can be satisfied by prose is not a wiring test.
        call = re.search(r"^\s*poga_preflight_gate\s*$", body, re.M)
        alloc = re.search(r"^\s*lane=\"\$\(alloc_lane\)\"", body, re.M)
        self.assertIsNotNone(call, "cmd_session does not call poga_preflight_gate")
        self.assertIsNotNone(alloc, "cmd_session does not call alloc_lane")
        self.assertLess(call.start(), alloc.start(),
                        "preflight gate must precede lane allocation")

    def test_resume_also_gates(self):
        body = self.src.split("cmd_resume() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("poga_preflight_gate", body)

    def test_bypass_env_is_honoured_and_announced(self):
        fn = _extract_function("poga_preflight_gate")
        self.assertIn("POGA_SKIP_PREFLIGHT", fn)
        self.assertIn("NOT checked", fn)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PogaLaunchGateBehaviourTest(unittest.TestCase):
    """The gate EXECUTED, not read.

    This class exists because reading the source was not enough. The fail-open branch
    (D6) was unreachable under `set -e` — a bare `python3 …` exiting nonzero terminated
    the shell before `rc=$?` ran, so the code written to keep un-migrated members
    launching would have bricked them instead. The source looked correct; only running
    it under poga's real `set -euo pipefail` showed otherwise. Every case below runs the
    real shell function against a stub `session.py` whose exit code we choose.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # BOTH functions: the gate delegates its two fail-open exits to the capture
        # helper (WI-0180), so extracting the gate alone would test a shell that cannot
        # run the branches this class exists to pin.
        self.fn = (_extract_function("poga_preflight_gate") + "\n"
                   + _extract_function("poga_preflight_capture"))

    def _stub(self, code: int, stderr: str = ""):
        (self.tmp / "session.py").write_text(
            "import sys\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n",
            encoding="utf-8")

    def _run(self, env_extra: dict | None = None):
        """Run the function under the SAME shell options poga sets. `set -euo pipefail`
        is the whole point — without it the regression this class pins is invisible."""
        script = (f'set -euo pipefail\nROOT="{self.tmp}"\n{self.fn}\n'
                  'poga_preflight_gate\necho "RETURNED"\n')
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        env.update(env_extra or {})
        return subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                              env=env)

    def test_pass_returns_and_execution_continues(self):
        self._stub(0)
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("RETURNED", r.stdout)

    def test_refusal_exits_one_and_stops_the_launch(self):
        self._stub(1, "poga: REFUSING to create a lane — 1 precondition(s) failed.\n")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("RETURNED", r.stdout)      # the launch must NOT proceed
        self.assertIn("REFUSING", r.stderr)         # and the reason must survive

    def test_harness_without_the_verb_fails_open(self):
        """THE REGRESSION. argparse answers exit 2 on a member whose session.py predates
        `preflight`. Under `set -e` a bare call made that kill poga outright."""
        self._stub(2, "session.py: error: argument cmd: invalid choice: 'preflight'\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, f"gate failed CLOSED: {r.stderr}")
        self.assertIn("RETURNED", r.stdout)
        self.assertIn("preflight did not run", r.stderr)

    def test_argparse_noise_is_not_dumped_on_every_launch(self):
        """The usage dump from an un-migrated member is not the operator's problem, and
        printing it in front of every launch is how a note becomes wallpaper."""
        self._stub(2, "usage: session.py [-h] {start,end,...}\nerror: invalid choice\n")
        r = self._run()
        self.assertNotIn("usage:", r.stderr)
        self.assertIn("preflight did not run", r.stderr)

    def test_unknown_warnings_are_passed_through_on_a_pass(self):
        """D4 — warning-and-continuing is only honest if the warning reaches the
        operator. Swallowing it would make UNKNOWN indistinguishable from OK, which is
        the exact collapse this whole verb refuses."""
        self._stub(0, "poga: preflight could not verify terminfo — $TERM is unset\n")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("could not verify terminfo", r.stderr)

    def test_bypass_skips_the_check_and_says_so(self):
        self._stub(1, "should never run\n")
        r = self._run({"POGA_SKIP_PREFLIGHT": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertIn("RETURNED", r.stdout)
        self.assertIn("NOT checked", r.stderr)
        self.assertNotIn("should never run", r.stderr)

    def test_absent_harness_is_a_silent_no_op(self):
        """A repo with no session.py at all is not a repo this gate has an opinion
        about."""
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("RETURNED", r.stdout)


def _extract_function(name: str) -> str:
    """The named shell function's source, lifted from poga (a script, not a sourceable
    library — executing it to reach one helper would run main)."""
    src = POGA.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", src, re.M | re.S)
    assert m, f"function {name} not found in poga"
    return m.group(0)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PogaWiringTest(unittest.TestCase):
    """A verb that exists in session.py and is unreachable from `poga` is missing at the
    only moment anyone types it."""

    def setUp(self):
        self.src = POGA.read_text(encoding="utf-8")

    def test_verb_is_routed(self):
        self.assertTrue(re.search(r"^    preflight\)", self.src, re.M),
                        "no `preflight)` case arm in poga")
        arm = self.src.split("    preflight)", 1)[1].split(";;", 1)[0]
        self.assertIn("session.py\" preflight", arm)

    def test_verb_is_in_help(self):
        self.assertIn("poga preflight", self.src)

    def test_verb_is_in_the_unknown_verb_roster(self):
        """The roster is what someone sees after a typo. A verb absent from it reads as
        one that does not exist — the WI-0112 failure, in the other direction.

        RUNS poga instead of reading its source. WI-0158 replaced the hand-transcribed
        roster — which had gone stale by eight live verbs, `preflight` surviving only
        because this test happened to pin it — with one DERIVED from the case table, so
        the only way to see what a reader is shown is to make the tool print it."""
        # RUN IT OUTSIDE ANY REPO, deliberately (WI-0151). The refusal now CONSULTS
        # ORIGIN before it fires — a verb this copy lacks may simply be one it has not
        # received yet — and from `ROOT` that consult would fetch the live federation's
        # real remote from inside the suite. The roster under test is read from the
        # script's own file and owes nothing to a cwd, so there is no repo to be in and
        # the test cannot reach live state at all
        # ([`evidence-is-separated-from-state-by-construction`]).
        with tempfile.TemporaryDirectory() as nowhere:
            r = subprocess.run(["bash", str(POGA), "definitely-not-a-verb"],
                               text=True, capture_output=True, cwd=nowhere)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("verbs:", r.stderr, r.stderr)
        roster = r.stderr.split("verbs:", 1)[1].split("(`poga --help`", 1)[0]
        self.assertIn("preflight", roster)

    def test_session_py_registers_the_subcommand(self):
        r = subprocess.run([sys.executable, str(ROOT / "session.py"), "preflight", "-h"],
                           text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--quick", r.stdout)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class PreflightFailureCaptureTest(unittest.TestCase):
    """WI-0180 — a preflight that could not deliver a verdict must leave the REASON behind.

    The defect this pins is not that the gate failed open; failing open is the design.
    It is that the fail-open branch ran `rm -f "$errfile"` one line before telling the
    operator it had happened, so the only record of WHY was destroyed at the moment it
    was produced. The justification in the comment — "rc>=2 means this member predates
    the verb, and the stderr is just an argparse dump" — is true for an un-migrated
    member and false everywhere the verb exists. Federation has the verb, saw the note
    intermittently (operator, session ~174), and could not diagnose it: re-running cannot
    recover deleted evidence, and the one session run deliberately to catch it passed.

    So the property is: **the terminal stays quiet and the evidence survives.** Both, not
    either. A future edit that restores the discard to keep the launch banner clean is
    the regression, and `test_the_reason_survives_the_note` is what catches it.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fn = (_extract_function("poga_preflight_gate") + "\n"
                   + _extract_function("poga_preflight_capture"))

    def _stub(self, code: int, stderr: str = ""):
        (self.tmp / "session.py").write_text(
            "import sys\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n",
            encoding="utf-8")

    def _run(self):
        script = (f'set -euo pipefail\nROOT="{self.tmp}"\n{self.fn}\n'
                  'poga_preflight_gate\necho "RETURNED"\n')
        return subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                              env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})

    def _records(self):
        d = self.tmp / ".git" / "poga-coord" / "preflight-failures"
        return sorted(d.glob("*.txt")) if d.is_dir() else []

    def test_the_reason_survives_the_note(self):
        """THE REGRESSION, stated as the thing that was lost rather than the line that
        lost it: after a launch that could not verify preconditions, the stderr that
        explains why is readable from disk."""
        self._stub(2, "session.py: error: argument cmd: invalid choice: 'preflight'\n")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("RETURNED", r.stdout)
        recs = self._records()
        self.assertEqual(len(recs), 1, f"the reason was discarded again: {r.stderr}")
        self.assertIn("invalid choice", recs[0].read_text())

    def test_the_note_names_where_the_reason_went(self):
        """A record nobody can find is the discard with extra steps."""
        self._stub(2, "boom\n")
        r = self._run()
        self.assertIn("reason captured at", r.stderr)
        self.assertIn(str(self._records()[0]), r.stderr)

    def test_the_terminal_still_does_not_get_the_dump(self):
        """The half of the original reasoning that was RIGHT. Keeping the evidence must
        not undo it — an argparse dump in front of every launch is how a note becomes
        wallpaper, which is how this one went unread for so long."""
        self._stub(2, "usage: session.py [-h] {start,end,...}\nerror: invalid choice\n")
        r = self._run()
        self.assertNotIn("usage:", r.stderr)
        self.assertIn("usage:", self._records()[0].read_text())

    def test_the_record_carries_the_environment_not_just_the_error(self):
        """An intermittent failure is usually about the environment, not the message.
        `python3` resolving differently under a stripped PATH is WI-0187's defect one
        step over, and it is invisible unless the record says which python ran."""
        self._stub(2, "boom\n")
        self._run()
        body = self._records()[0].read_text()
        for field in ("when:", "root:", "cwd:", "python:", "path:"):
            self.assertIn(field, body)

    def test_a_crash_is_not_narrated_as_a_verdict(self):
        """Exit 1 is two things. A check that PROVED something broken exits 1, and so
        does an unhandled traceback — and the gate used to read both as "refuse, it
        printed its remedy", telling the operator a bug in the gate was a considered
        judgement about their machine. A crash proves nothing, so it fails open."""
        self._stub(1, 'Traceback (most recent call last):\n  File "x"\nKeyError: 1\n')
        r = self._run()
        self.assertEqual(r.returncode, 0, "a crashing gate blocked the launch")
        self.assertIn("RETURNED", r.stdout)
        self.assertIn("preflight crashed", r.stderr)
        self.assertIn("KeyError", self._records()[0].read_text())

    def test_a_real_refusal_still_refuses_and_leaves_no_record(self):
        """The guard on the guard. Widening the crash escape hatch until a genuine
        precondition failure slips through it would turn this fix into the bug preflight
        exists to prevent."""
        self._stub(1, "poga: REFUSING to create a lane — 1 precondition(s) failed.\n")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("RETURNED", r.stdout)
        self.assertIn("REFUSING", r.stderr)
        self.assertEqual(self._records(), [])

    def test_a_pass_leaves_no_record(self):
        self._stub(0, "poga: preflight could not verify terminfo\n")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._records(), [])

    def test_an_uncapturable_reason_says_so_rather_than_implying_one_exists(self):
        """If the store cannot be written the note must not point at a file that is not
        there — reporting absence as presence is the fabrication this substrate refuses
        everywhere else."""
        self._stub(2, "boom\n")
        (self.tmp / ".git").write_text("not a directory\n")   # mkdir -p will fail
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("could NOT be captured", r.stderr)
        self.assertNotIn("reason captured at", r.stderr)


class PreflightFailureSurfaceTest(unittest.TestCase):
    """The other half of WI-0180, and the half that decides who does the diagnosing.

    Capturing alone moves the evidence from nowhere to a path nobody opens; the operator
    would still have to notice a NOTE scroll past under a launch banner, remember it, and
    go looking. That is a person used as an execution surface for a fact the machine
    already holds (ADR-0099) — and it is exactly the step that failed, because the
    message is intermittent. The session that follows the failed launch reads it instead.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Never the real repo: a fixture that resolves the true common dir writes into
        # the federation's own coordination store.
        patcher = mock.patch.object(session, "_git_common_dir", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dir = session._preflight_failure_dir(create=True)

    def _record(self, name: str, head: str, last: str):
        (self.dir / name).write_text(
            f"# preflight {head}\nwhen:   x\n--- stderr ---\n{last}\n")

    def test_silent_when_nothing_failed(self):
        self.assertEqual(session._preflight_failure_lines(), [])

    def test_the_reason_reaches_the_session_not_just_the_disk(self):
        self._record("20260830T010101Z-1.txt", "did not run (exit 2)",
                     "session.py: error: argument cmd: invalid choice: 'preflight'")
        blob = "\n".join(session._preflight_failure_lines())
        self.assertIn("could not verify preconditions", blob)
        self.assertIn("invalid choice", blob)          # the cause, without opening the file
        self.assertIn(str(self.dir), blob)             # and the path, for when that is not enough

    def test_surfaced_records_are_not_re_reported_forever(self):
        """A signal that repeats after it has been read is one that gets ignored — which
        is how the note it replaces went unread."""
        self._record("20260830T010101Z-1.txt", "did not run (exit 2)", "boom")
        self.assertTrue(session._preflight_failure_lines())
        self.assertEqual(session._preflight_failure_lines(), [])

    def test_surfaced_records_are_kept_not_deleted(self):
        """A second occurrence is the evidence that says whether the cause is the
        machine, the clock, or the checkout. Deleting the first throws that away."""
        self._record("20260830T010101Z-1.txt", "did not run (exit 2)", "boom")
        session._preflight_failure_lines()
        self.assertTrue((self.dir / "seen" / "20260830T010101Z-1.txt").is_file())

    def test_a_dry_run_does_not_consume_evidence_nobody_read(self):
        self._record("20260830T010101Z-1.txt", "did not run (exit 2)", "boom")
        self.assertTrue(session._preflight_failure_lines(consume=False))
        self.assertTrue(session._preflight_failure_lines(consume=False))

    def test_many_records_are_summarised_not_dumped(self):
        for i in range(6):
            self._record(f"2026083{i}T010101Z-{i}.txt", "did not run (exit 2)", f"boom{i}")
        lines = session._preflight_failure_lines()
        self.assertIn("6 launch(es)", lines[0])
        self.assertLessEqual(len(lines), 5)
        self.assertIn("older record(s)", lines[-1])

    def test_newest_first(self):
        self._record("20260801T010101Z-1.txt", "did not run (exit 2)", "older")
        self._record("20260830T010101Z-2.txt", "crashed (exit 1)", "newer")
        self.assertIn("newer", session._preflight_failure_lines()[1])

    def test_a_malformed_record_does_not_break_the_announce(self):
        """This runs on the session-start path. A record it cannot parse must cost a
        detail line, never the banner."""
        (self.dir / "20260830T010101Z-1.txt").write_bytes(b"\xff\xfe not text at all")
        self.assertTrue(session._preflight_failure_lines())

    def test_the_start_banner_calls_it(self):
        """Wiring. A surface nothing invokes is indistinguishable from one that does not
        exist — the WI-0112 shape, which this file already pins for the verb itself."""
        src = harness_fixture.harness_source()
        self.assertIn("_preflight_failure_lines(consume=", src)



GB = 1024 ** 3


def _proc(rss, uid, pid, comm):
    return {"rss_bytes": int(rss), "uid": uid, "pid": pid, "comm": comm}


#: THE THRASHING MACHINE, an invented sample in the shape the finding described: one
#: system daemon ballooned to many GB, swap nearly exhausted, one session running. It
#: is fabricated rather than reproduced because the only way to produce it live is to inflict
#: it — a runaway daemon on a box with sibling lanes working is the harm this check
#: exists to prevent, not a test fixture.
THRASHING = {
    "phys_bytes": 64 * GB,
    "swap_used_bytes": int(60 * GB),
    "procs": [_proc(12 * GB, 0, 261, "/usr/libexec/exampled-indexer"),
              _proc(1 * GB, 1000, 5626, "/Users/x/.local/bin/claude"),
              _proc(150 * 1024 ** 2, 0, 325, "/usr/local/bin/exampled")],
}

#: THE NEGATIVE CONTROL, an invented sample of a busy development machine: several
#: live sessions and lanes working, high load averages. A machine at high load is
#: the exact case a load-keyed guard would have refused, and one false refusal on an
#: ordinary parallel wave is how a guard in front of every launch gets deleted.
BUSY_AND_FINE = {
    "phys_bytes": 64 * GB,
    "swap_used_bytes": 0,
    "procs": [_proc(1500 * 1024 ** 2, 1000, 5626, "/Users/x/.local/bin/claude"),
              _proc(1000 * 1024 ** 2, 1000, 37905, "/Users/x/.local/bin/claude"),
              _proc(900 * 1024 ** 2, 1000, 37847, "/Users/x/.local/bin/claude"),
              _proc(400 * 1024 ** 2, 0, 354, "/System/.../XProtectBridgeService"),
              _proc(150 * 1024 ** 2, 0, 325, "/usr/local/bin/exampled")],
}

RUNTIME_NAMES = frozenset(("claude", "codex"))


class MemoryPressureVerdictTest(unittest.TestCase):
    """WI-0398. The judgement is a pure function over a sample, so both ends of the
    measurement are cases rather than anecdotes."""

    def _v(self, sample, uid=1000):
        return session._memory_pressure_verdict(sample, uid=uid, runtimes=RUNTIME_NAMES)

    def test_the_thrashing_machine_is_refused(self):
        r = self._v(THRASHING)
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_the_refusal_names_the_daemon_and_its_rss(self):
        """The acceptance condition, and the reason this is not a dashboard. 'High memory
        pressure' sends the reader to Activity Monitor to do the diagnosis again; a named
        12.0 GB system daemon is a thing to act on."""
        r = self._v(THRASHING)
        self.assertIn("exampled-indexer", r.detail)
        self.assertIn("12.0 GB", r.detail)
        self.assertIn("261", r.detail)
        self.assertIn("kill 261", r.remedy)

    def test_the_refusal_says_waiting_will_not_clear_it(self):
        """What the lost time actually cost: a lane waited hours because nothing told it
        that waiting was the wrong move."""
        self.assertIn("waited hours", self._v(THRASHING).remedy)

    def test_a_busy_machine_is_not_refused(self):
        """THE NEGATIVE CONTROL. Several sessions, high load, no runaway."""
        r = self._v(BUSY_AND_FINE)
        self.assertEqual(r.verdict, session.PREFLIGHT_OK, r.detail)

    def test_swap_alone_refuses(self):
        """Condition (1) on its own — no daemon over the bar, but the machine is paging."""
        sample = dict(BUSY_AND_FINE, swap_used_bytes=int(20 * GB))
        r = self._v(sample)
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("swap", r.detail)
        self.assertIn("refuses at 25%", r.detail)

    def test_a_runaway_daemon_alone_refuses(self):
        """Condition (2) on its own — swap still clean, one daemon eating the box. This is
        the state the thrashing machine passed THROUGH on its way to nearly all of its swap, which is
        why either condition refuses rather than both."""
        sample = dict(BUSY_AND_FINE,
                      procs=BUSY_AND_FINE["procs"]
                      + [_proc(12 * GB, 0, 261, "/usr/libexec/exampled-indexer")])
        r = self._v(sample)
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        self.assertIn("exampled-indexer", r.detail)

    def test_a_daemon_over_the_session_but_under_the_floor_is_not_refused(self):
        """The floor is the half that keeps the rule usable. 'More than the session would'
        is won by every daemon on an idle box, and refusing a modest indexer on a machine
        with nothing running is the false refusal that gets a guard deleted."""
        sample = {"phys_bytes": 64 * GB, "swap_used_bytes": 0,
                  "procs": [_proc(2 * GB, 0, 304, "/usr/libexec/mds_stores"),
                            _proc(600 * 1024 ** 2, 1000, 5626, "/Users/x/.local/bin/claude")]}
        r = self._v(sample)
        self.assertEqual(r.verdict, session.PREFLIGHT_OK, r.detail)

    def test_a_daemon_over_the_floor_but_under_a_bigger_session_is_not_refused(self):
        """And the other half of the AND. A daemon over the floor on a box whose sessions
        each hold more is not the thing that lost the time."""
        sample = {"phys_bytes": 64 * GB, "swap_used_bytes": 0,
                  "procs": [_proc(10 * GB, 0, 304, "/usr/libexec/mds_stores"),
                            _proc(12 * GB, 1000, 5626, "/Users/x/.local/bin/claude")]}
        r = self._v(sample)
        self.assertEqual(r.verdict, session.PREFLIGHT_OK, r.detail)

    def test_the_users_own_runaway_is_not_a_system_daemon(self):
        """Scope, deliberately. A runaway `claude` is this operator's own work and killing it
        is not this check's call; the finding is about a daemon nobody chose to run."""
        sample = {"phys_bytes": 64 * GB, "swap_used_bytes": 0,
                  "procs": [_proc(12 * GB, 1000, 5626, "/Users/x/.local/bin/claude"),
                            _proc(200 * 1024 ** 2, 0, 354, "/usr/libexec/syspolicyd")]}
        self.assertEqual(self._v(sample).verdict, session.PREFLIGHT_OK)

    def test_an_unreadable_machine_is_unknown_not_ok(self):
        """`declare-what-a-check-assumes`. 'Could not tell' must not wear the same face as
        'checked and fine' — a probe that folds them refuses nothing and certifies
        everything."""
        r = self._v({"phys_bytes": None, "swap_used_bytes": None, "procs": [],
                     "unreadable": ["sysctl: FileNotFoundError"]})
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)
        self.assertIn("sysctl: FileNotFoundError", r.detail)
        self.assertIn("invisible here", r.detail)

    def test_a_readable_machine_with_no_process_table_is_unknown(self):
        """Half a reading is not a reading: swap alone cannot name an offender."""
        r = self._v({"phys_bytes": 64 * GB, "swap_used_bytes": 0, "procs": [],
                     "unreadable": ["ps exited 1"]})
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_the_ok_row_says_what_it_did_not_look_at(self):
        """Load average is not a condition, on purpose — the thrashing machine read very high,
        and so does a healthy one running several suites."""
        self.assertIn("NOT checked: load average", self._v(BUSY_AND_FINE).detail)


class MemorySampleTest(unittest.TestCase):
    """The I/O half. Parsing is where a resource probe usually lies to you.

    These are the macOS (`sysctl`) shapes, so the platform is pinned to darwin: on Linux
    the sample reads /proc/meminfo instead (WI-0468, tests/test_linux_portability.py)."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {})
        env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(env.stop)
        plat = mock.patch.object(sys, "platform", "darwin")
        plat.start()
        self.addCleanup(plat.stop)

    def _run(self, sysctl_out, ps_out, sysctl_rc=0, ps_rc=0):
        def fake(argv, **kw):
            if argv[0] == "sysctl":
                return _completed(sysctl_out, sysctl_rc)
            return _completed(ps_out, ps_rc)
        with mock.patch.object(session.subprocess, "run", side_effect=fake):
            return session._memory_sample()

    def test_it_reads_bytes_from_sysctl_and_kilobytes_from_ps(self):
        """`ps` reports RSS in KB and `vm.swapusage` in suffixed MB/GB. Getting either
        unit wrong is a check that is off by a factor of 1024 and still looks plausible."""
        s = self._run("68719476736\ntotal = 64.00G  used = 60.00G  free = 4.00G\n",
                      "12582912   0   261 /usr/libexec/exampled-indexer\n")
        self.assertEqual(s["phys_bytes"], 68719476736)
        self.assertAlmostEqual(s["swap_used_bytes"] / GB, 60.0, places=2)
        self.assertEqual(s["procs"][0]["rss_bytes"], 12582912 * 1024)
        self.assertEqual(s["procs"][0]["uid"], 0)
        self.assertEqual(s["procs"][0]["comm"],
                         "/usr/libexec/exampled-indexer")

    def test_megabyte_swap_is_read_as_megabytes(self):
        """The live shape on a healthy machine, where the unit differs from the bad one."""
        s = self._run("68719476736\ntotal = 0.00M  used = 0.00M  free = 0.00M  (encrypted)\n",
                      "722784  1000  5626 /Users/x/.local/bin/claude\n")
        self.assertEqual(s["swap_used_bytes"], 0)

    def test_a_dead_sysctl_is_recorded_not_swallowed(self):
        s = self._run("", "722784  1000  5626 /Users/x/.local/bin/claude\n", sysctl_rc=1)
        self.assertIsNone(s["phys_bytes"])
        self.assertTrue(any("sysctl" in u for u in s["unreadable"]))

    def test_an_unparseable_swap_line_is_named(self):
        """A shape this parser does not know must read as unknown, not as zero swap."""
        s = self._run("68719476736\nswap is somewhere else now\n",
                      "722784  1000  5626 /Users/x/.local/bin/claude\n")
        self.assertIsNone(s["swap_used_bytes"])
        self.assertTrue(any("unparseable" in u for u in s["unreadable"]))

    def test_a_missing_ps_leaves_an_empty_table_and_says_so(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=OSError("no ps")):
            s = session._memory_sample()
        self.assertEqual(s["procs"], [])
        self.assertTrue(any("OSError" in u for u in s["unreadable"]))

    def test_under_the_gate_it_does_not_spawn_anything(self):
        """ADR-0119 D4. The guard is in the spawning function because that is where the
        detector reads for it — and because the launch gate, not `cmd_preflight`, is this
        check's other caller."""
        with mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"}), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=AssertionError("read the machine")):
            s = session._memory_sample()
        self.assertEqual(s["procs"], [])
        self.assertTrue(any("land gate" in u for u in s["unreadable"]))


class MemoryPressureCheckTest(unittest.TestCase):
    """The check itself — the wiring between the two halves above."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {})
        env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(env.stop)

    def test_under_the_gate_it_skips_and_names_the_reason(self):
        with mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"}), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=AssertionError("read the machine")):
            r = session._pf_memory_pressure()
        self.assertEqual(r.verdict, session.PREFLIGHT_SKIPPED)
        self.assertIn("NOT checked", r.detail)

    def test_it_asks_the_runtime_registry_which_processes_are_sessions(self):
        """`derive-a-checks-subjects-from-the-authority`: the list of session commands is
        RUNTIMES, not a literal typed here that goes stale the day a runtime is added."""
        seen = {}

        def capture(sample, *, uid, runtimes):
            seen["runtimes"] = runtimes
            return session._PreflightResult("memory-pressure", session.PREFLIGHT_OK, "d")

        with mock.patch.object(session, "_memory_sample", return_value={}), \
             mock.patch.object(session, "_memory_pressure_verdict", side_effect=capture):
            session._pf_memory_pressure()
        self.assertEqual(seen["runtimes"],
                         frozenset(r["command"] for r in session.RUNTIMES
                                   if r.get("command")))

    def test_it_reads_this_machine_and_returns_a_verdict(self):
        """The only test here that touches the real box: it asserts the check RUNS and
        lands on one of the three real verdicts, never what this machine's answer is."""
        r = session._pf_memory_pressure()
        self.assertEqual(r.name, "memory-pressure")
        self.assertIn(r.verdict, (session.PREFLIGHT_OK, session.PREFLIGHT_FAIL,
                                  session.PREFLIGHT_UNKNOWN))

if __name__ == "__main__":
    unittest.main()
