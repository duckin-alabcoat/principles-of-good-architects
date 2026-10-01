"""`session.py test` runs the GATE's suite, and says which runner produced the verdict
(WI-0353).

THE DEFECT. The repo carried two definitions of "the suite". `cmd_test` ran a plain
serial `unittest discover` — roughly twelve minutes — while the land gate ran
`curate/run_suite.py` sharded across every core, roughly ninety seconds. So the verb an
Architect reaches for was both the slow one AND not the one that decides the land: a
green from it cost twelve minutes and still did not answer the question it was asked.

THE FIX IS A DERIVATION, NOT A SECOND NAME. `_gate_suite_command` reads the gate's own
configured command list and returns the suite entry from it, so there is one definition
and no second place to edit. `RunnerIsDerivedFromTheGateTest` is what keeps that true:
it would fail if someone reintroduced a hardcoded runner in `cmd_test`, because the
tests point the CONFIG somewhere else and demand the verb follow it.

`--serial` SURVIVES, and the reason is load-bearing rather than a courtesy. The serial
order is the ONLY detector of order-dependent test pollution. Session ~305 is the case:
`tests/test_notes_file.py` replaced two functions on the shared `session` module and
never restored them, the trunk went red, and the sharded gate never saw it because
sharding changes test order — three code lands succeeded on that red trunk. A sharded
PASS therefore has to SAY it is blind to that class, which `TheVerdictNamesItsRunnerTest`
pins; a green that does not declare its own blind spot is the WI-0139 defect wearing new
clothes.

THE MEMBER CASE IS NOT A FALLBACK. `sessionlib/hooks.py` ships byte-identical to every
member (`curate/push-substrate.py`'s `BYTE_IDENTICAL` globs `sessionlib/*.py`) while
`curate/` ships to nobody. On a member the gate's own default IS the serial `unittest
discover`, so `_gate_suite_command` returns None and the verb runs serially — not
degraded, but running exactly the suite that member's gate runs, which is the property
this whole item is about.
"""

import argparse
import contextlib
import io
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


SHARDED = "python3 " + session.GATE_SUITE_RUNNER
SERIAL_DEFAULT = [["python3", "-m", "unittest", "discover", "-s", "tests"]]


def _args(**over):
    """The verb's namespace with every flag at its parser default."""
    ns = dict(pattern=None, verbose=False, serial=False, jobs=None)
    ns.update(over)
    return argparse.Namespace(**ns)


class _Run:
    """A captured `subprocess.run` — records the argv, reports a green run."""

    def __init__(self, returncode=0):
        self.argv = None
        self.kwargs = None
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        return subprocess.CompletedProcess(argv, self.returncode)


@contextlib.contextmanager
def _drive(gate, tmp, args, verdict=("passed", "4340 test(s), OK (skipped=2)"), rc=0,
           runner_present=True):
    """Run `cmd_test` against a configured `gate` list without running a suite.

    The stderr log the verb reads back is stubbed through `_test_verdict`, which has its
    own eleven tests in `tests/test_test_verdict.py` — pinning it a second time here
    would be testing the classifier through a straw rather than testing the verb.
    """
    runner = tmp / session.GATE_SUITE_RUNNER
    if runner_present:
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text("# stand-in\n", encoding="utf-8")
    spy = _Run(rc)
    buf = io.StringIO()
    with mock.patch.object(session, "ROOT", tmp), \
         mock.patch.object(session, "CFG", {"gate": gate}), \
         mock.patch.object(session, "_git_dir", return_value=None), \
         mock.patch.object(session, "_test_verdict", return_value=verdict), \
         mock.patch.object(subprocess, "run", spy), \
         contextlib.redirect_stdout(buf):
        try:
            session.cmd_test(args)
        except SystemExit as e:
            spy.exit_code = e.code
        else:
            spy.exit_code = 0
    spy.out = buf.getvalue()
    yield spy


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def drive(self, gate=(SHARDED,), args=None, **kw):
        with _drive(list(gate), self.tmp, args or _args(), **kw) as spy:
            return spy


class RunnerIsDerivedFromTheGateTest(_Base):
    """The verb spawns the gate's command, read from the config at call time."""

    def test_the_default_run_is_the_gates_own_suite_command(self):
        spy = self.drive()
        self.assertIn(session.GATE_SUITE_RUNNER, spy.argv,
                      "the default run must be the gate's sharded runner, not a "
                      "second definition of the suite (WI-0353)")
        self.assertNotIn("unittest", spy.argv)

    def test_it_follows_the_config_rather_than_a_name_in_the_source(self):
        """The derivation, stated as a test that a hardcode cannot pass: the gate
        entry carries flags the verb never knew about, and they must arrive."""
        spy = self.drive(gate=[SHARDED + " --expect 4340", "python3 session.py wi-check"])
        self.assertEqual(spy.argv[1:], [session.GATE_SUITE_RUNNER, "--expect", "4340"],
                         "the verb restated the runner instead of reading the gate's "
                         "command — `--expect 4340` is in the gate and not in the run")

    def test_python3_resolves_to_the_running_interpreter(self):
        """WI-0328's rule, on the path it did not previously cover. `poga test` once
        re-resolved `python3` from PATH inside an already-running interpreter, so the
        verb could report a verdict from a different Python than the gate's. The serial
        branch's `sys.executable` literal is pinned by
        `tests/test_interpreter.py::test_the_test_verb_spells_sys_executable`; this is
        the sharded branch's half of the same claim."""
        spy = self.drive()
        self.assertEqual(spy.argv[0], sys.executable)
        self.assertNotIn("python3", spy.argv)

    def test_the_gates_command_list_is_read_at_call_time(self):
        """A gate re-pointed at a different runner takes the verb with it — which is
        what makes 'one definition' structural rather than asserted."""
        spy = self.drive(gate=["python3 tools/other_runner.py"])
        self.assertIn("-m", spy.argv)
        self.assertIn("unittest", spy.argv)


class SerialIsReachableAndSaysWhyTest(_Base):
    """`--serial` is the only detector of order-dependent pollution (session ~305)."""

    def test_serial_runs_the_unittest_discovery_order(self):
        spy = self.drive(args=_args(serial=True))
        self.assertEqual(spy.argv[1:], ["-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(spy.argv[0], sys.executable)

    def test_verbose_implies_serial_because_only_it_has_the_order(self):
        """`-v` names each test in discovery order. The sharded runner has no such
        order — its workers each discover their own shard — so honouring `-v` means
        running serially, and the output says so rather than swapping the runner out
        from under the reader."""
        spy = self.drive(args=_args(verbose=True))
        self.assertIn("unittest", spy.argv)
        self.assertIn("-v", spy.argv)
        self.assertIn("serial because", spy.out)

    def test_a_member_with_no_sharded_runner_in_its_gate_runs_serially(self):
        """`curate/` ships to no member while this file ships to every one of them, so
        a member's gate names no sharded runner. Serial there is not a degradation: it
        is that member's gate's own suite."""
        spy = self.drive(gate=SERIAL_DEFAULT, runner_present=False)
        self.assertIn("unittest", spy.argv)
        self.assertIn("serial because", spy.out)

    def test_a_gate_naming_a_runner_this_checkout_lacks_falls_back_and_says_so(self):
        spy = self.drive(runner_present=False)
        self.assertIn("unittest", spy.argv)
        self.assertIn(session.GATE_SUITE_RUNNER, spy.out,
                      "a fallback that does not name the file it could not find "
                      "leaves the reader to guess why the run was slow")

    def test_jobs_under_serial_is_declared_inert_rather_than_silently_dropped(self):
        spy = self.drive(args=_args(serial=True, jobs=4))
        self.assertNotIn("-j", spy.argv)
        self.assertIn("-j does nothing here", spy.out)

    def test_jobs_reaches_the_sharded_runner(self):
        spy = self.drive(args=_args(jobs=4))
        self.assertEqual(spy.argv[-2:], ["-j", "4"])


class TheVerdictNamesItsRunnerTest(_Base):
    """A green from each runner is a different claim about the tree. A reader who
    cannot tell which one they are holding has the problem this item exists to fix."""

    def test_a_sharded_pass_is_tagged_sharded(self):
        spy = self.drive()
        self.assertIn("test: PASSED [sharded]", spy.out)

    def test_a_serial_pass_is_tagged_serial(self):
        spy = self.drive(args=_args(serial=True))
        self.assertIn("test: PASSED [serial]", spy.out)

    def test_a_sharded_pass_declares_that_it_cannot_see_order_dependence(self):
        """The blind spot is printed with the green, not left in a doc. Session ~305's
        red trunk was invisible to the sharded gate for exactly this reason, and three
        code lands succeeded on it."""
        spy = self.drive()
        self.assertIn("order-dependent", spy.out)
        self.assertIn("--serial", spy.out)

    def test_a_serial_pass_does_not_carry_the_sharded_caveat(self):
        spy = self.drive(args=_args(serial=True))
        self.assertNotIn("order-dependent", spy.out,
                         "the serial run IS the detector; telling its reader to go "
                         "run the detector is noise")

    def test_a_failure_names_its_runner_too(self):
        spy = self.drive(verdict=("failed", "4340 test(s), FAILED (failures=1)"), rc=1)
        self.assertIn("test: FAILED [sharded]", spy.out)
        self.assertEqual(spy.exit_code, 1)

    def test_a_did_not_report_names_its_runner_too(self):
        spy = self.drive(verdict=("did-not-report", "no `Ran N tests` line"), rc=0)
        self.assertIn("test: DID NOT REPORT [sharded]", spy.out)
        self.assertEqual(spy.exit_code, 2)


class TheRunIsInsulatedLikeTheGatesTest(_Base):
    """The verb now runs the gate's suite, so it inherits the gate's own hazards."""

    def test_stdin_is_closed(self):
        """WI-0168: inheriting the session's stdin hung a land for 33 minutes with no
        output and no timeout — a test reached `_read_hook_stdin`, whose
        `sys.stdin.read()` waited on an EOF a harness socket never sends. `_run_gate`
        has passed DEVNULL since; this verb runs the same suite and had not. In a
        detached lane pane a wait for input is indistinguishable from work."""
        spy = self.drive()
        self.assertIs(spy.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_the_two_streams_go_to_separate_files_not_a_pipe(self):
        """WI-0139's shape, unchanged by this item and re-pinned because the runner
        swap rewrote the lines around it: a pipe would make the exit status the
        downstream command's, which is the whole false-green class."""
        spy = self.drive()
        self.assertIsNot(spy.kwargs.get("stdout"), subprocess.PIPE)
        self.assertIsNot(spy.kwargs.get("stderr"), subprocess.PIPE)
        self.assertIsNotNone(spy.kwargs.get("stdout"))
        self.assertIsNotNone(spy.kwargs.get("stderr"))


class GateArgvHasOneDefinitionTest(unittest.TestCase):
    """`_gate_argv` is shared by `_run_gate` and `cmd_test` (P16). Two copies of the
    `python3` rewrite is the drift that puts the verb and the gate back on different
    interpreters, which is the defect WI-0328 already paid for once."""

    def test_it_rewrites_only_the_leading_python3(self):
        self.assertEqual(session._gate_argv("python3 curate/run_suite.py"),
                         [sys.executable, "curate/run_suite.py"])

    def test_a_command_that_is_not_python3_is_untouched(self):
        self.assertEqual(session._gate_argv("make check"), ["make", "check"])

    def test_a_list_command_is_accepted_as_configured(self):
        self.assertEqual(session._gate_argv(["python3", "-m", "unittest"]),
                         [sys.executable, "-m", "unittest"])

    def test_the_rewrite_is_not_transcribed_a_second_time(self):
        """Reads the harness source. The rule lived inline in `_run_gate`; a future
        edit that pastes it back somewhere is what this catches."""
        src = (pathlib.Path(session.__file__).resolve().parent
               / "sessionlib" / "land.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('argv[0] = sys.executable'), 1,
                         "the `python3` -> interpreter rewrite has more than one copy "
                         "in land.py; route the new one through `_gate_argv`")

    def test_the_suite_command_is_found_in_the_configured_list(self):
        with mock.patch.object(session, "CFG",
                               {"gate": ["python3 session.py wi-check", SHARDED]}):
            self.assertEqual(session._gate_suite_command(), SHARDED)

    def test_a_gate_with_no_suite_command_answers_none_rather_than_guessing(self):
        with mock.patch.object(session, "CFG", {"gate": ["python3 session.py wi-check"]}):
            self.assertIsNone(session._gate_suite_command())


class TheRealGateStillNamesTheShardedRunnerTest(unittest.TestCase):
    """Against the repo's own config, not a fixture — the derivation above is only
    worth anything if this federation's gate actually configures the sharded runner.
    Sibling of `tests/test_run_suite.py::test_the_gate_still_runs_it`, from the other
    end: that one asserts the gate names it, this one asserts the VERB reaches it."""

    def test_the_verb_would_run_the_sharded_runner_here(self):
        cmd = session._gate_suite_command()
        self.assertIsNotNone(cmd, "this federation's gate no longer configures "
                                  "curate/run_suite.py, so `session.py test` has "
                                  "silently become a serial run again")
        self.assertIn(session.GATE_SUITE_RUNNER, session._gate_argv(cmd))

    def test_the_runner_the_gate_names_is_present_in_this_checkout(self):
        self.assertTrue((session.ROOT / session.GATE_SUITE_RUNNER).exists())


if __name__ == "__main__":
    unittest.main()
