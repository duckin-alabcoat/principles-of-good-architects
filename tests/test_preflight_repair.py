"""WI-0150 / ADR-0109 — preflight repairs what it can, and escalates only what it tried.

ADR-0096 shipped the report and said in its own Consequences that nothing fixes what it
finds. the operator's ruling closing that gap is broader than the verb — preflight, git and lane
problems are to be fixed and carried on from, not reported to him — so the
contract worth pinning is not "does the terminfo repair work". It is the set of
properties that keep an automatic repair from becoming a new way to lie:

  1. **The re-run is the receipt.** A repair that reports success while the check still
     fails is a FAILED repair. `tic` exits 0 having installed a wrong entry, so the
     repair's own exit code can never be the evidence — only the check passing after it.
  2. **The destination is never a source.** `~/.terminfo` is where the repair writes, and
     the check only failed because what is there is absent or broken; reading the entry
     back out of it would reinstall the broken entry and then confirm success.
  3. **A source without the capability is not a source.** An entry that resolves but
     declares no `kbs` swaps one silently-broken terminal for another.
  4. **Every check declares a repair class.** A check missing from the table is an error,
     not a silent "unrepairable" — that is the same absent-reads-as-answered failure the
     OK/FAIL/UNKNOWN split exists to prevent.
  5. **Repair is the DEFAULT.** WI-0150 proposed `--fix`; an opt-in fix leaves the errand
     where it was. `--no-repair` is the cautious path, and the pinned direction is that
     the flag turns repair OFF.
  6. **The guided step never starts a second auth flow.** Wall 4 of ADR-0096 was exactly
     that, and a repair that reproduces it is the original defect wearing a fix's clothes.

The terminfo tests use the REAL `tic` and `infocmp` against temporary databases rather
than mocking them (`verify-in-the-created-configuration`): the failure this repair is
built to prevent lives in what those two binaries actually do with an entry, and a mock
of them would pin our belief about that instead of the behaviour.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402
import harness_fixture  # noqa: E402

HAVE_TERMINFO_TOOLS = bool(shutil.which("tic") and shutil.which("infocmp"))

# A $TERM that cannot exist in any system database, so "the check passes" can only ever
# mean "our repair installed it" and never "the developer's machine already had it".
FAKE_TERM = "poga-preflight-selftest"

# Minimal but REAL terminfo source. `kbs` is the capability whose absence makes backspace
# do nothing on a host whose terminfo lacks it — the one PREFLIGHT_TERM_CAPS names.
GOOD_SRC = (f"{FAKE_TERM}|poga preflight selftest terminal,\n"
            f"\tam, xenl,\n"
            f"\tcols#80, lines#24,\n"
            f"\tkbs=\\177, bel=^G, clear=\\E[H\\E[2J, cup=\\E[%i%p1%d;%p2%dH,\n")
# The same terminal, WITHOUT kbs — resolves fine and is still the broken machine.
BAD_SRC = (f"{FAKE_TERM}|poga preflight selftest terminal,\n"
           f"\tam, xenl,\n"
           f"\tcols#80, lines#24,\n"
           f"\tbel=^G, clear=\\E[H\\E[2J, cup=\\E[%i%p1%d;%p2%dH,\n")


def _compile_db(source: str, into: pathlib.Path) -> pathlib.Path:
    """Build a real terminfo database from `source` using the real `tic`."""
    into.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ti", delete=False) as fh:
        fh.write(source)
        path = fh.name
    try:
        r = subprocess.run(["tic", "-x", "-o", str(into), path],
                           capture_output=True, text=True)
        if r.returncode != 0:                      # pragma: no cover - setup failure
            raise AssertionError(f"tic failed building the fixture: {r.stderr}")
    finally:
        os.unlink(path)
    return into


def _result(name: str, verdict: str, detail: str = "d", remedy: str = "r"):
    return session._PreflightResult(name, verdict, detail, remedy)


class _EnvSandbox(unittest.TestCase):
    """Every test here reads $TERM / $TERMINFO / $TERMINFO_DIRS, all of which the
    developer's own shell sets. Restoring them is what keeps the suite from depending on
    the terminal it happens to be run from."""

    KEYS = ("TERM", "TERMINFO", "TERMINFO_DIRS", session.PREFLIGHT_NO_REPAIR_ENV)

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _point_at(self, dest: pathlib.Path):
        """Make the CHECK read only `dest`, so a pass can only come from what we wrote."""
        os.environ["TERM"] = FAKE_TERM
        os.environ["TERMINFO"] = str(dest)
        os.environ["TERMINFO_DIRS"] = str(dest)


@unittest.skipUnless(HAVE_TERMINFO_TOOLS, "needs the real tic/infocmp")
class TerminfoRepairTest(_EnvSandbox):
    """The one AUTO-class repair, against real binaries."""

    def test_missing_entry_is_installed_and_the_check_then_passes(self):
        """The whole point, end to end: FAIL before, OK after, and the OK is the
        re-run's, not the repair's own account of itself."""
        dest = self._tmp / "dest"
        src = _compile_db(GOOD_SRC, self._tmp / "src")
        self._point_at(dest)

        before = session._pf_terminfo()
        self.assertEqual(before.verdict, session.PREFLIGHT_FAIL, before.detail)

        with mock.patch.object(session, "_terminfo_source_dbs", return_value=[src]):
            results, narration = session._preflight_repair(
                [before], allow_guided=False,
                checks_by_name={"terminfo": session._pf_terminfo})

        self.assertEqual(results[0].verdict, session.PREFLIGHT_OK, narration)
        self.assertTrue(any("repaired" in n for n in narration), narration)
        # And it is really on disk, not merely reported.
        self.assertTrue(any(p.is_file() for p in dest.rglob("*")), list(dest.rglob("*")))

    def test_source_without_the_capability_is_refused(self):
        """PROPERTY 3. An entry that resolves but declares no `kbs` is not a repair — it
        is the same broken terminal with a fresh timestamp."""
        dest = self._tmp / "dest"
        src = _compile_db(BAD_SRC, self._tmp / "src")
        self._point_at(dest)

        with mock.patch.object(session, "_terminfo_source_dbs", return_value=[src]):
            ok, how = session._repair_terminfo(session._pf_terminfo())

        self.assertFalse(ok)
        self.assertIn("kbs", how)
        self.assertFalse(list(dest.rglob("*")), "refused source must install nothing")

    def test_the_destination_is_never_used_as_a_source(self):
        """PROPERTY 2, and the one that would silently 'succeed' if it regressed: the
        only database carrying the entry is the DESTINATION, and it carries the broken
        one. A repair that reads its own destination reports success here."""
        dest = _compile_db(BAD_SRC, self._tmp / "dest")
        self._point_at(dest)

        before = session._pf_terminfo()
        self.assertEqual(before.verdict, session.PREFLIGHT_FAIL, before.detail)

        # NOT patched: the real discovery function must exclude the destination itself.
        ok, how = session._repair_terminfo(before)
        self.assertFalse(ok, f"repaired from its own destination: {how}")

    def test_discovery_excludes_the_destination_even_via_terminfo_dirs(self):
        dest = self._tmp / "dest"
        dest.mkdir()
        os.environ["TERMINFO"] = str(dest)
        os.environ["TERMINFO_DIRS"] = str(dest)
        self.assertNotIn(dest.resolve(),
                         [p.resolve() for p in session._terminfo_source_dbs()])

    def test_no_source_anywhere_says_so_rather_than_claiming_a_repair(self):
        dest = self._tmp / "dest"
        self._point_at(dest)
        with mock.patch.object(session, "_terminfo_source_dbs", return_value=[]):
            ok, how = session._repair_terminfo(session._pf_terminfo())
        self.assertFalse(ok)
        self.assertIn("no local terminfo database", how)

    def test_unset_term_is_not_repairable(self):
        os.environ.pop("TERM", None)
        ok, how = session._repair_terminfo(_result("terminfo", session.PREFLIGHT_FAIL))
        self.assertFalse(ok)
        self.assertIn("$TERM", how)


class RepairReceiptTest(_EnvSandbox):
    """PROPERTY 1 — the re-run is the receipt. Pinned with a repair that LIES."""

    def test_successful_repair_whose_check_still_fails_is_reported_as_not_fixed(self):
        still_bad = _result("terminfo", session.PREFLIGHT_FAIL, "still broken")
        liar = session._PreflightRepair(
            session.REPAIR_AUTO, "why", lambda r: (True, "did something"))
        with mock.patch.dict(session.PREFLIGHT_REPAIRS, {"terminfo": liar}):
            results, narration = session._preflight_repair(
                [_result("terminfo", session.PREFLIGHT_FAIL)],
                allow_guided=False,
                checks_by_name={"terminfo": lambda: still_bad})
        self.assertEqual(results[0].verdict, session.PREFLIGHT_FAIL)
        self.assertTrue(any("still does not pass" in n for n in narration), narration)

    def test_a_repair_that_could_not_run_escalates_as_tried_and_could_not(self):
        gave_up = session._PreflightRepair(
            session.REPAIR_AUTO, "why", lambda r: (False, "no source anywhere"))
        with mock.patch.dict(session.PREFLIGHT_REPAIRS, {"terminfo": gave_up}):
            _, narration = session._preflight_repair(
                [_result("terminfo", session.PREFLIGHT_FAIL)],
                allow_guided=False, checks_by_name={"terminfo": lambda: None})
        self.assertTrue(any("tried to repair and could not" in n for n in narration),
                        narration)

    def test_only_failed_checks_are_repaired(self):
        called = []
        spec = session._PreflightRepair(
            session.REPAIR_AUTO, "why",
            lambda r: (called.append(r.name), (True, "x"))[1])
        with mock.patch.dict(session.PREFLIGHT_REPAIRS, {"terminfo": spec}):
            session._preflight_repair(
                [_result("terminfo", session.PREFLIGHT_OK)],
                allow_guided=False, checks_by_name={"terminfo": session._pf_terminfo})
        self.assertEqual(called, [], "a passing check must never be 'repaired'")

    def test_undeclared_check_is_named_as_a_defect_not_silently_skipped(self):
        """PROPERTY 4. A check added later with no declared class must be visible as OUR
        omission. It must not crash the verb — a diagnostic tool that dies on the machine
        whose diagnosis was requested is a worse failure than the one it guards — and it
        must not read as 'nothing can be done', which is the absent-reads-as-answered
        shape the whole verdict split exists to prevent. So: loud, and non-fatal."""
        _, narration = session._preflight_repair(
            [_result("brand-new-check", session.PREFLIGHT_FAIL)],
            allow_guided=False, checks_by_name={})
        self.assertTrue(any("brand-new-check" in n and "substrate defect" in n
                            for n in narration), narration)

    def test_an_undeclared_check_is_never_treated_as_guided(self):
        """The fallback class must be inert, not a licence to open a dialog."""
        with mock.patch.object(session, "_repair_claude_first_run") as launch:
            session._preflight_repair(
                [_result("brand-new-check", session.PREFLIGHT_FAIL)],
                allow_guided=True, checks_by_name={})
        launch.assert_not_called()


class RepairTableTest(unittest.TestCase):
    """The declared split, pinned as data so a silent reclassification is visible."""

    ALL_CHECKS = ("terminfo", "workspace-trust", "onboarding", "auth",
                  "data-root", "residency", "stranded-runtimes", "memory-pressure",
                  "runtime-quarantine", "gate")

    def test_every_check_the_verb_runs_declares_a_class(self):
        self.assertEqual(set(session.PREFLIGHT_REPAIRS), set(self.ALL_CHECKS))

    def test_the_split_is_one_auto_three_guided_six_none(self):
        """SIX none since WI-0398: `memory-pressure` names a root-owned daemon and stops.
        Killing it is destructive and it is not this harness's process (P9)."""
        kinds = [s.kind for s in session.PREFLIGHT_REPAIRS.values()]
        self.assertEqual(kinds.count(session.REPAIR_AUTO), 1)
        self.assertEqual(kinds.count(session.REPAIR_GUIDED), 3)
        self.assertEqual(kinds.count(session.REPAIR_NONE), 6)

    def test_every_non_auto_class_states_why_not(self):
        """An escalation without a reason is 'here is what I found', which is the thing
        the ruling forbids."""
        for name, spec in session.PREFLIGHT_REPAIRS.items():
            if spec.kind != session.REPAIR_AUTO:
                self.assertTrue(spec.why.strip(), f"{name} escalates with no reason")

    def test_auto_class_carries_a_function_and_none_class_does_not(self):
        for name, spec in session.PREFLIGHT_REPAIRS.items():
            if spec.kind == session.REPAIR_AUTO:
                self.assertTrue(callable(spec.fn), name)
            if spec.kind == session.REPAIR_NONE:
                self.assertIsNone(spec.fn, name)


class GuidedRepairTest(_EnvSandbox):
    """The three checks we cannot repair but CAN stop printing at people."""

    def test_a_live_auth_flow_blocks_the_guided_step(self):
        """PROPERTY 6 — ADR-0096's wall 4, which a naive repair reproduces exactly."""
        why = session._preflight_guided_blocked([
            _result("stranded-runtimes", session.PREFLIGHT_FAIL)])
        self.assertIn("wall 4", why)

    def test_no_tty_blocks_the_guided_step_and_says_so(self):
        with mock.patch.object(session.sys, "stdin", mock.Mock(isatty=lambda: False)), \
             mock.patch.object(session.sys, "stdout", mock.Mock(isatty=lambda: True)):
            why = session._preflight_guided_blocked([])
        self.assertIn("terminal", why)

    def test_guided_is_not_launched_when_blocked(self):
        """The interlock has to stop the LAUNCH, not merely be computed next to it."""
        with mock.patch.object(session, "_repair_claude_first_run") as launch:
            _, narration = session._preflight_repair(
                [_result("workspace-trust", session.PREFLIGHT_FAIL),
                 _result("stranded-runtimes", session.PREFLIGHT_FAIL)],
                allow_guided=True, checks_by_name={})
        launch.assert_not_called()
        self.assertTrue(any("could not open the step here" in n for n in narration),
                        narration)

    def test_guided_is_not_launched_when_the_caller_forbids_it(self):
        with mock.patch.object(session, "_repair_claude_first_run") as launch:
            session._preflight_repair(
                [_result("workspace-trust", session.PREFLIGHT_FAIL)],
                allow_guided=False, checks_by_name={})
        launch.assert_not_called()

    def test_auth_alone_logs_in_rather_than_running_the_wizard(self):
        with mock.patch.object(session, "_shared_work_root",
                               return_value=pathlib.Path("/main")), \
             mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run") as run:
            ok, how = session._repair_claude_first_run(
                [_result("auth", session.PREFLIGHT_FAIL)])
        self.assertTrue(ok)
        self.assertEqual(run.call_args[0][0], ["claude", "auth", "login"])
        self.assertIn("auth login", how)

    def test_trust_and_onboarding_are_settled_by_one_invocation(self):
        """Two failures, ONE dialog — the whole reason the guided class is worth having
        rather than three printed remedies.

        `_shared_work_root` is patched so the count means what it says: it resolves by
        shelling out to git, so an unpatched run spends a `subprocess.run` of its own and
        the assertion measures plumbing instead of dialogs."""
        with mock.patch.object(session, "_shared_work_root",
                               return_value=pathlib.Path("/main")), \
             mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run") as run:
            ok, _ = session._repair_claude_first_run(
                [_result("workspace-trust", session.PREFLIGHT_FAIL),
                 _result("onboarding", session.PREFLIGHT_FAIL)])
        self.assertTrue(ok)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ["claude"])

    def test_it_runs_in_the_main_checkout_not_this_lane(self):
        """`claude --worktree` is invoked from the repo root, so the root's trust state
        is what decides a launch — checking or fixing the lane's own path is a right
        answer to the wrong question.

        `_shared_work_root` is patched rather than read, and that is not squeamishness:
        it resolves by shelling out to git, so a blanket `subprocess.run` mock silently
        degrades it to the lane's own path and the assertion then passes against the
        wrong root. Stating the premise is what keeps this test about the repair."""
        main = pathlib.Path("/somewhere/main-checkout")
        with mock.patch.object(session, "_shared_work_root", return_value=main), \
             mock.patch.object(session.shutil, "which", return_value="/bin/claude"), \
             mock.patch.object(session.subprocess, "run") as run:
            session._repair_claude_first_run(
                [_result("workspace-trust", session.PREFLIGHT_FAIL)])
        self.assertEqual(run.call_args.kwargs["cwd"], str(main))


class GateRepairTest(_EnvSandbox):
    """ADR-0109 D5/D6/D7 — the gate repairs and carries on, announces it, and can still
    be told to refuse instead."""

    def _gate(self, results, **env):
        for k, v in env.items():
            os.environ[k] = v
        with mock.patch.object(session, "PREFLIGHT_GATE_CHECKS",
                               tuple(lambda r=r: r for r in results)):
            with self.assertRaises(SystemExit) as e:
                session._preflight_gate()
        return e.exception.code

    def test_a_repaired_failure_lets_the_launch_through(self):
        bad = _result("terminfo", session.PREFLIGHT_FAIL)
        good = _result("terminfo", session.PREFLIGHT_OK)
        state = {"n": 0}

        def check():
            state["n"] += 1
            return bad if state["n"] == 1 else good

        Reviewer = session._PreflightRepair(session.REPAIR_AUTO, "w", lambda r: (True, "did"))
        with mock.patch.dict(session.PREFLIGHT_REPAIRS, {"terminfo": Reviewer}), \
             mock.patch.object(session, "PREFLIGHT_GATE_CHECKS", (check,)):
            with self.assertRaises(SystemExit) as e:
                session._preflight_gate()
        self.assertEqual(e.exception.code, 0, "a repaired machine must not be refused")

    def test_an_unrepairable_failure_still_refuses(self):
        code = self._gate([_result("residency", session.PREFLIGHT_FAIL)])
        self.assertEqual(code, 1)

    def test_the_no_repair_flag_restores_the_refusal_without_repairing(self):
        """D7 — the escape is an opt-OUT. Pinned by the repair never being called."""
        called = []
        Reviewer = session._PreflightRepair(
            session.REPAIR_AUTO, "w",
            lambda r: (called.append(1), (True, "did"))[1])
        with mock.patch.dict(session.PREFLIGHT_REPAIRS, {"terminfo": Reviewer}):
            code = self._gate([_result("terminfo", session.PREFLIGHT_FAIL)],
                              **{session.PREFLIGHT_NO_REPAIR_ENV: "1"})
        self.assertEqual(code, 1)
        self.assertEqual(called, [], "the opt-out must skip the repair, not just its report")

    def test_a_clean_machine_is_still_silent_and_passes(self):
        code = self._gate([_result("terminfo", session.PREFLIGHT_OK)])
        self.assertEqual(code, 0)


class VerbDefaultsTest(unittest.TestCase):
    """PROPERTY 5 — repair is the default; `--no-repair` is the opt-out."""

    def _parse(self, argv):
        p = session.build_parser() if hasattr(session, "build_parser") else None
        if p is None:
            self.skipTest("no module-level parser factory to interrogate")
        return p.parse_args(argv)

    def test_no_repair_flag_exists_and_defaults_off(self):
        """The direction of the flag IS the decision — a `--fix` here would mean the
        errand survives for everyone who does not know to type it."""
        src = harness_fixture.harness_source()
        self.assertIn('"--no-repair"', src)
        self.assertNotIn('pf.add_argument("--fix"', src)

    def test_cmd_preflight_repairs_unless_told_not_to(self):
        args = argparse.Namespace(gate=False, quick=True, json=True, no_repair=False)
        with mock.patch.object(session, "_pf_runtime_quarantine", return_value=[]), \
             mock.patch.object(session, "_preflight_repair",
                               side_effect=lambda r, **k: (r, [])) as rep:
            with self.assertRaises(SystemExit):
                session.cmd_preflight(args)
        rep.assert_called_once()

    def test_cmd_preflight_honours_no_repair(self):
        args = argparse.Namespace(gate=False, quick=True, json=True, no_repair=True)
        with mock.patch.object(session, "_pf_runtime_quarantine", return_value=[]), \
             mock.patch.object(session, "_preflight_repair") as rep:
            with self.assertRaises(SystemExit):
                session.cmd_preflight(args)
        rep.assert_not_called()


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
