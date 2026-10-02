"""WI-0299 D4 — the gate runs on a snapshot and nothing else.

[ADR-0058](adr/0058-land-per-session-with-an-isolated-gate.md) D4 claims the candidate is
checked out into a throwaway worktree so the gate *"sees exactly what the trunk would
become — no in-flight noise from anyone."* WI-0271 measured that claim with an audit hook
across 36 test modules inside a real scratch gate worktree, and it was not true: the suite
shelled out to the machine's `lsof`, `ps`, `tmux` and `claude`, and asserted against the
LIVE work-item store. A verdict that depends on the operator's process table is not a
function of the commit it gates, and this class has blocked a land twice already.

**THE GUARD IS IN THE PRODUCTION CODE, NOT IN A RULE TESTS MUST REMEMBER**, and that is a
deliberate departure from the item's own sketch. WI-0299 asked for the audit-hook probe
itself to become the permanent test. Measured against what that costs: the hook is
per-interpreter and cannot be uninstalled, so it can only run the suite in a child, which
means every land pays for the suite TWICE — 224–328s on one machine per
[ADR-0117](adr/0117-a-land-classifies-its-own-diff-before-it-gates.md), on top of the run
it is checking. ADR-0117 exists because operator ruled that a land should take seconds, not
tens of minutes; a guard that doubles every land to enforce ADR-0117's own premise is
the wrong trade in the same week.

So the reach is closed where it happens. `_run_gate` sets `POGA_GATE=1` in the suite's
environment, and the four functions that leave the machine's own boundary — `_process_cwd`
(lsof), `_lane_processes` (ps), `_pf_stranded_runtimes` (ps), `_pf_auth` (claude) — plus
`_tmux_bin`, which every tmux path already routes through, return their existing
CANNOT-TELL answer instead of spawning. A test cannot forget a guard it does not have to
apply, which is `add-structural-guard-on-recurrence` read correctly: this is the third
instance of the class, so the fix belongs below the tests rather than in them.

`CompletenessTest` is the other half — `ship-the-detector-with-the-capability`. A guard on
four named functions is a hand-list the moment a fifth one is written, so the detector
reads the harness source and fails when any function spawns one of the forbidden programs
without going through the guard.

**WHAT THIS CANNOT TELL YOU, stated because a check that hides its blind spot is worse
than no check.** It bounds what the SUITE's own process spawns. A test that shells out to
`session.py` or `git` gets a child interpreter which re-reads `POGA_GATE` and is therefore
also bound — but a child that clears its environment, or a binary that reads the process
table by some route other than these five functions, is invisible here. That is exactly
the per-interpreter limit ADR-0117 names for `sys.addaudithook`, and swapping mechanisms
did not remove it. `SpawnedProcessReadsAreCannotTellTest` pins the honest verdict so it
cannot quietly become a claim of completeness.
"""

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from harness_fixture import ROOT as HARNESS_ROOT, harness_files  # noqa: E402


def _ok(name):
    """A passing preflight result, for the checks a test is not the subject of."""
    return session._PreflightResult(name, session.PREFLIGHT_OK, "fine")


class GateEnvTest(unittest.TestCase):
    """The gate must SAY it is the gate. Everything below keys on that one variable, so a
    gate that does not set it is a guard that never fires — the silent-exemption shape."""

    def test_the_gate_marks_the_suites_environment(self):
        seen = {}

        def fake_sh(argv, check=True, cwd=None, env=None, stdin=None):
            seen["env"] = env
            return subprocess.CompletedProcess(argv, 0, "", "")

        cfg = dict(session.CFG or {})
        cfg["gate"] = [["python3", "-c", "pass"]]
        with mock.patch.object(session, "CFG", cfg), \
             mock.patch.object(session, "sh", fake_sh):
            ok, _report = session._run_gate(cwd=pathlib.Path("/tmp"))
        self.assertTrue(ok)
        self.assertIsNotNone(seen.get("env"),
                             "the gate inherited the ambient environment wholesale")
        self.assertEqual(seen["env"].get(session.GATE_ENV_VAR), "1")

    def test_under_gate_reads_that_variable(self):
        with mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"}):
            self.assertTrue(session._under_gate())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(session._under_gate())


class ForbiddenReachesRefuseUnderTheGateTest(unittest.TestCase):
    """Each reach returns the answer it already returns when it genuinely cannot tell —
    never a fabricated one, and never a spawn."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = mock.patch.object(subprocess, "run",
                                     side_effect=AssertionError("spawned under the gate"))
        self.run.start()
        self.addCleanup(self.run.stop)

    def test_process_cwd_does_not_reach_lsof(self):
        self.assertIsNone(session._process_cwd(os.getpid()),
                          "under the gate a process cwd is unknowable, not guessable")

    def test_lane_processes_reads_no_process_table(self):
        self.assertEqual(session._lane_processes(), [])

    def test_no_keystrokes_are_sent_to_a_live_session(self):
        """WI-0271's sharpest instance: `CapBindsOnTheFirstWaveTest` typed a real brief
        into the operator's live tmux server during a land."""
        ok, detail = session._dispatch_rebrief_tmux("poga-gate-probe", "a brief")
        self.assertFalse(ok)
        self.assertIn("land gate", detail)

    pass

    def test_the_lookup_itself_is_not_guarded(self):
        """The narrowing that cost a round trip, pinned so it is not casually undone.
        `_tmux_bin()` must keep answering, because `_dispatch_surface` asks it merely to
        decide whether tmux is a usable surface — and a None there made `poga dispatch`
        PLANNING exit 2 under the gate while passing outside it. A gate whose verdict
        differs from reality fails correct work, which is worse than the reach it was
        closing."""
        if shutil.which("tmux") is None:
            self.skipTest("tmux not installed on this machine")
        self.assertIsNotNone(session._tmux_bin())

    def test_preflight_auth_says_unknown_rather_than_asking_the_cli(self):
        r = session._pf_auth()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)

    def test_preflight_stranded_runtimes_says_unknown(self):
        r = session._pf_stranded_runtimes()
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)


class PreflightIsGuardedAtTheCommandTest(unittest.TestCase):
    """The guard is on `cmd_preflight`, not on `_pf_auth` / `_pf_stranded_runtimes`.

    Both probes have hermetic unit tests that mock `subprocess.run` and pin every branch
    (`AuthCheckTest`, `StrandedRuntimeCheckTest`), so a guard inside the probe sits ABOVE
    that mock and deletes six tests that never touched the machine. The reach WI-0271
    actually measured was `VerbDefaultsTest` calling this COMMAND for real, twice, during
    a land — so the refusal belongs here, where the reach is."""

    def test_the_two_machine_checks_are_not_run(self):
        import argparse
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {session.GATE_ENV_VAR: "1"}), \
             mock.patch.object(session, "_pf_auth",
                               side_effect=AssertionError("asked the CLI")), \
             mock.patch.object(session, "_pf_stranded_runtimes",
                               side_effect=AssertionError("read the process table")), \
             mock.patch.object(session, "_pf_terminfo", return_value=_ok("terminfo")), \
             mock.patch.object(session, "_pf_workspace_trust", return_value=_ok("trust")), \
             mock.patch.object(session, "_pf_onboarding", return_value=_ok("onboarding")), \
             mock.patch.object(session, "_pf_data_root", return_value=_ok("data-root")), \
             mock.patch.object(session, "_pf_residency", return_value=_ok("residency")), \
             contextlib.redirect_stdout(buf):
            with contextlib.suppress(SystemExit):
                session.cmd_preflight(argparse.Namespace(gate=False, quick=True,
                                                         repair=False, json=False))
        self.assertIn("NOT checked", buf.getvalue())


class OutsideTheGateNothingChangesTest(unittest.TestCase):
    """The control. A guard that fires everywhere is not a guard, it is an amputation —
    these functions exist to probe the real machine and must still do it."""

    def setUp(self):
        # Only the gate marker is removed. Clearing the whole environment would strip
        # PATH, so `shutil.which` would answer None and these controls would pass for the
        # wrong reason — a green that proves the opposite of what it claims.
        self.env = mock.patch.dict(os.environ, {})
        self.env.start()
        os.environ.pop(session.GATE_ENV_VAR, None)
        self.addCleanup(self.env.stop)

    def test_tmux_is_found_when_installed(self):
        if shutil.which("tmux") is None:
            self.skipTest("tmux not installed on this machine")
        self.assertIsNotNone(session._tmux_bin())

    def test_process_cwd_still_reads_a_live_process(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             cwd=str(pathlib.Path(__file__).resolve().parent))
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.assertIsNotNone(session._process_cwd(p.pid),
                             "the guard disabled the probe outside the gate too")


class CompletenessTest(unittest.TestCase):
    """`ship-the-detector-with-the-capability`. Four guarded functions is a hand-list the
    moment somebody writes a fifth, and an unlisted reach does not read as absent — it
    reads as FINE, because the gate goes green and nobody is told the verdict came partly
    from the operator's machine.

    Derived from the harness source, never typed: every function that spawns one of the
    forbidden programs must consult the guard."""

    FORBIDDEN = frozenset(("lsof", "ps", "tmux", "claude"))

    #: Spawn sites whose guard is on the CALLER instead, each with the argument for why.
    #: An exemption list is the thing this repo is usually right to refuse — but the
    #: objection is to entries that default to *safe* with no argument
    #: (`ship-the-detector-with-the-capability`). These carry one, the test below asserts
    #: every entry HAS one, and adding a row is a visible edit in a file about guarding.
    #:
    #: The shared reason: each of these has its own hermetic unit test that mocks
    #: `subprocess.run` and pins every branch, so a guard inside the function sits ABOVE
    #: that mock and deletes coverage without removing a reach. The reach in each case is a
    #: caller invoking it for real, and that is where the refusal lives.
    GUARDED_AT_THE_CALLER = {
        "_pf_auth": "guarded in `cmd_preflight` — `AuthCheckTest` pins all four branches "
                    "against a mocked CLI; the reach was `VerbDefaultsTest` calling the "
                    "command for real (WI-0271 channel c).",
        "_pf_stranded_runtimes": "guarded in `cmd_preflight` — `StrandedRuntimeCheckTest` "
                                 "pins its parsing against a mocked `ps`; same reach.",
        "_dispatch_open_tmux": "`SpawnedMeansObservedTest` mocks `_tmux_bin` and "
                               "`subprocess.run` and pins all three outcomes. The unmocked "
                               "reach WI-0271 measured was `_dispatch_rebrief_tmux`, which "
                               "IS guarded.",
        "_attach_live_sessions": "`ReadsWhatIsRunningFromTmuxTest` is its hermetic unit "
                                 "test, ten cases over a mocked `subprocess.run`. No "
                                 "unmocked reach to it was ever measured.",
        "cmd_drill": "an operator verb; `DrillTouchesNothingRealTest` mocks the spawn. No "
                     "unmocked reach measured, and the drill's whole point is that it "
                     "touches nothing real.",
    }

    def _functions_spawning_forbidden(self):
        """{function name: {programs}} for every harness function that reaches the machine.

        TWO SHAPES, because one of them nearly cost the whole guard. The obvious shape is a
        literal argv — `["ps", "-axo", ...]` — and that is what the first version matched.
        It immediately found two `ps` sites the hand-written list had missed
        (`_process_chain`, `_dispatch_runtime_pid`), which was the detector paying for
        itself on its first run.

        It also matched NOTHING for tmux, and read as clean. Every tmux argv is built from
        a RESOLVED PATH (`tmux = _tmux_bin()`, then `[tmux, "send-keys", ...]`), so the
        first element is a Name and no literal ever appears — the exact `send-keys` WI-0271
        measured against the operator's live server was invisible to the check written to
        catch it. A detector that reports clean because it cannot see is
        `declare-what-a-check-assumes` turned on the detector itself. So the second shape:
        a function that spawns with the RESOLVED BINARY AS ARGV[0] is a spawn site,
        however that element is spelled.

        Argv[0] and not merely "resolves tmux somewhere in a function that also spawns",
        which is what the second shape said first. That over-matched `_attach_open`, which
        resolves the path only to INTERPOLATE it into a launch template and never runs tmux
        — and guarding it sat above the point where `test_attach`'s hermetic tests mock
        `subprocess.run`, breaking fourteen tests that had never touched the machine. The
        guard is for reaches, and a dispatcher whose spawn is mocked is not one."""
        found: dict[str, set] = {}
        for rel in harness_files():
            tree = ast.parse((HARNESS_ROOT / rel).read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                # Names bound in this function to something that makes it a spawn site:
                # `tmux = _tmux_bin()` (including the `or "tmux"` fallback), and
                # `argv = ["tmux", "ls", ...]` — the third blind spot, found when an
                # exemption for `_attach_live_sessions` went stale because the detector
                # could no longer see the function it was exempting. An argv built one line
                # above its `subprocess.run` is the ordinary way to write this, so a
                # detector that only reads inline literals misses whichever call sites
                # happen to be tidy.
                resolved = set()
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                        continue
                    if not isinstance(node.targets[0], ast.Name):
                        continue
                    if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                           and c.func.id == "_tmux_bin" for c in ast.walk(node.value)):
                        resolved.add(node.targets[0].id)
                    val = node.value
                    if isinstance(val, (ast.List, ast.Tuple)) and val.elts \
                            and isinstance(val.elts[0], ast.Constant) \
                            and val.elts[0].value in self.FORBIDDEN:
                        found.setdefault(fn.name, set()).add(val.elts[0].value)
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call) or not node.args:
                        continue
                    if not isinstance(node.args[0], (ast.List, ast.Tuple)):
                        continue
                    elts = node.args[0].elts
                    if not elts:
                        continue
                    head = elts[0]
                    if isinstance(head, ast.Constant) and isinstance(head.value, str) \
                            and head.value in self.FORBIDDEN:
                        found.setdefault(fn.name, set()).add(head.value)
                    elif isinstance(head, ast.Name) and head.id in resolved:
                        found.setdefault(fn.name, set()).add("tmux")
        return found

    def _consults_the_guard(self, name):
        for rel in harness_files():
            tree = ast.parse((HARNESS_ROOT / rel).read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == name]:
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                            and node.func.id == "_under_gate":
                        return True
        return False

    def test_the_detector_can_actually_see_something(self):
        """A detector that finds nothing proves nothing. If this ever empties, the AST
        shape changed and the guard below became vacuous — which is the failure mode this
        whole module is about."""
        self.assertTrue(self._functions_spawning_forbidden(),
                        "the detector matched no spawn sites at all — it has stopped "
                        "measuring rather than started passing")

    def test_every_exemption_carries_an_argument(self):
        """An exemption with no reason is the silent hand-list this detector exists to
        replace. Each row must say where the guard actually is."""
        for name, why in self.GUARDED_AT_THE_CALLER.items():
            self.assertTrue(len(why) > 40, f"{name}'s exemption is not an argument")

    def test_no_exemption_outlives_its_spawn_site(self):
        """A row for a function that no longer spawns is a stale claim about the code, and
        it would quietly cover a future function that took the same name."""
        sites = set(self._functions_spawning_forbidden())
        stale = sorted(set(self.GUARDED_AT_THE_CALLER) - sites)
        self.assertEqual(stale, [], "these exemptions name functions that no longer reach "
                                    "the machine — delete the rows")

    def test_every_forbidden_spawn_site_consults_the_guard(self):
        unguarded = {n: sorted(p) for n, p in self._functions_spawning_forbidden().items()
                     if not self._consults_the_guard(n)
                     and n not in self.GUARDED_AT_THE_CALLER}
        self.assertEqual(unguarded, {},
                         "these harness functions spawn a machine-scoped program with no "
                         "gate guard, so a test reaching them makes the land's verdict a "
                         "function of the operator's machine (WI-0271)")


class SuiteNeverRunsOverAnInProgressMergeTest(unittest.TestCase):
    """WI-0215, and it is a data-loss defect rather than a hygiene one.

    2026-08-30, session ~176, reconciling two trunks: a `git merge --no-commit` was in
    progress with 17 conflicted files resolved and staged, and `python3 -m unittest
    discover -s tests` was run in that same worktree. On return `MERGE_HEAD` was GONE —
    HEAD unmoved, the resolved content still correctly staged. So the merge PARENTAGE was
    destroyed while the work survived, which is the worst-shaped version: a plain `git
    commit` at that point produces a single-parent commit that silently loses the second
    trunk from the ancestry, and `integrate` then reports the trunks as diverged forever.
    Recovered by hand with `write-tree` / `commit-tree -p -p` / `update-ref`.

    The mechanism was never identified — the reflog carried no commit, reset or abort, so
    something deleted the file directly rather than issuing a git command. That is why this
    is a REFUSAL and not a fix: the honest move against an unidentified destroyer is to
    keep it away from the state it destroys. `cmd_test` is the one entry point both
    `session.py test` and `poga test` go through, so the guard is one place."""

    def setUp(self):
        self.tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._root = session.ROOT
        self.addCleanup(lambda: setattr(session, "ROOT", self._root))
        session.ROOT = self.tmp

    def test_a_merge_in_progress_refuses_rather_than_running(self):
        git_dir = self.tmp / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("deadbeef\n", encoding="utf-8")
        with mock.patch.object(session, "_git_dir", return_value=git_dir), \
             mock.patch.object(subprocess, "run",
                               side_effect=AssertionError("ran the suite mid-merge")):
            with self.assertRaises(SystemExit) as e:
                session.cmd_test(__import__("argparse").Namespace(pattern=None,
                                                                  verbose=False))
        self.assertEqual(e.exception.code, 2,
                         "a refusal must not read as a pass, and must not read as a "
                         "failing suite either")

    def test_the_refusal_names_the_merge(self):
        git_dir = self.tmp / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("deadbeef\n", encoding="utf-8")
        buf = __import__("io").StringIO()
        with mock.patch.object(session, "_git_dir", return_value=git_dir), \
             __import__("contextlib").redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                session.cmd_test(__import__("argparse").Namespace(pattern=None,
                                                                  verbose=False))
        self.assertIn("MERGE_HEAD", buf.getvalue())


class SpawnedProcessReadsAreCannotTellTest(unittest.TestCase):
    """`declare-what-a-check-assumes`, turned on this guard.

    ADR-0117 records the same limit for the `gate-inputs` deriver: an audit hook is
    per-interpreter, so reads by processes the suite SPAWNS are invisible to it — *"an
    under-measurement, not a neutral simplification"*. Replacing the hook with a
    production-side guard does not remove that limit, it relocates it: a child process
    inherits `POGA_GATE` and is bound, but a child that clears its environment, or one
    that reaches the machine by a route these functions do not own, is not.

    Pinned as a test so the honest verdict — CANNOT TELL — survives contact with a future
    reader who wants this module to mean 'the gate is hermetic'. It does not mean that."""

    def test_a_child_that_keeps_the_environment_is_bound(self):
        env = {**os.environ, session.GATE_ENV_VAR: "1"}
        r = subprocess.run(
            [sys.executable, "-c",
             "import os; print(os.environ.get('POGA_GATE'))"],
            env=env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "1")

    def test_a_child_that_clears_the_environment_is_not_and_we_say_so(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import os; print(os.environ.get('POGA_GATE'))"],
            env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "None",
                         "if this ever prints 1, the guard reaches further than its "
                         "docstring claims and the claim should be widened deliberately")

    def test_the_module_documents_the_limit(self):
        self.assertIn("CANNOT TELL", __doc__ or "",
                      "the blind spot must stay stated in the guard's own words")


if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
