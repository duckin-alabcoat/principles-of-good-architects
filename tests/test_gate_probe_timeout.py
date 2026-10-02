"""OPS-0009's nightly derive died three nights running (2026-09-22..24) on a probe cap sized
for a shell it does not run in.

THE MECHANISM. `gate_inputs.derive()` runs the suite serially in a child process and used to
cap it at a fixed 1800s. The nightly runner is a LaunchDaemon with `ProcessType: Background`,
which macOS throttles on CPU and I/O. Under background policy a suite of several thousand
tests can need about an hour, so every night would end `subprocess.TimeoutExpired ... timed out after 1800 seconds`, as a bare traceback the
runner could only report as "the derive exited 1: Traceback".

WHAT THESE PIN.
  * The deriver's cap is a knob (`POGA_GATE_PROBE_TIMEOUT`) with the old default, so an
    interactive derive is unchanged and a malformed value cannot cost a night.
  * The runner turns that knob, to a value that covers the measured throttled probe and
    still sits under its own backstop — so the deriver's legible refusal, not the runner's
    kill, is what a too-slow night reports.
  * A probe that does hit the cap refuses in words that name the cap and the knob, rather
    than escaping as a traceback.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import shutil
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from curate import gate_inputs  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate_inputs_runner_for_probe_cap", ROOT / "curate" / "gate-inputs-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

#: The throttled probe's measured duration, rounded up (2026-09-24: 1,275 tests in 13m08s
#: under `taskpolicy -b` in the daemon's environment, extrapolated over ~6,060 tests).
MEASURED_THROTTLED_PROBE_SECONDS = 3700


class TheDeriverCapIsAKnobTest(unittest.TestCase):

    def test_unset_keeps_the_interactive_default(self):
        self.assertEqual(gate_inputs.probe_timeout({}), 1800)

    def test_a_positive_integer_overrides(self):
        self.assertEqual(
            gate_inputs.probe_timeout({gate_inputs.PROBE_TIMEOUT_ENV: "5400"}), 5400)

    def test_garbage_zero_and_negative_fall_back_rather_than_raise(self):
        for bad in ("", "soon", "0", "-5", "54.5"):
            with self.subTest(value=bad):
                self.assertEqual(
                    gate_inputs.probe_timeout({gate_inputs.PROBE_TIMEOUT_ENV: bad}), 1800)


class TheRunnerTurnsTheKnobTest(unittest.TestCase):

    def test_the_cap_covers_the_throttled_probe_and_sits_under_the_backstop(self):
        self.assertGreater(runner.PROBE_TIMEOUT_SECONDS, MEASURED_THROTTLED_PROBE_SECONDS)
        # Room for the eight gate-command measurements after the probe; otherwise the
        # runner's own kill pre-empts the deriver's legible refusal.
        self.assertLessEqual(runner.PROBE_TIMEOUT_SECONDS + 1800,
                             runner.DERIVE_TIMEOUT_SECONDS)

    def test_run_derive_hands_the_deriver_its_cap_under_the_deriver_s_own_name(self):
        lane = pathlib.Path(tempfile.mkdtemp(prefix="probe-cap-lane-"))
        self.addCleanup(shutil.rmtree, lane, ignore_errors=True)
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(runner.subprocess, "run", return_value=done) as run:
            runner.run_derive(lane)
        kwargs = run.call_args.kwargs
        # The runner's key is spelled literally; asserting it against the DERIVER's
        # constant is what catches the two drifting apart.
        self.assertEqual(kwargs["env"].get(gate_inputs.PROBE_TIMEOUT_ENV),
                         str(runner.PROBE_TIMEOUT_SECONDS))
        self.assertEqual(kwargs["timeout"], runner.DERIVE_TIMEOUT_SECONDS)


class AProbeThatHitsTheCapRefusesInWordsTest(unittest.TestCase):

    def test_timeout_is_a_named_refusal_not_a_traceback(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="probe-cap-root-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        leaked = tmp / ".gate-inputs.raw"

        def fake_run(argv, *a, **kw):
            if argv and argv[0] == "git":
                return subprocess.CompletedProcess(argv, 0, "", "")
            leaked.write_text("partial")  # a killed probe may leave this behind
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

        with mock.patch.object(gate_inputs, "ROOT", tmp), \
                mock.patch.object(gate_inputs.subprocess, "check_output",
                                  return_value="tree\n"), \
                mock.patch.object(gate_inputs.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(gate_inputs.session, "_gate_suite_tree",
                                  return_value="suite"), \
                mock.patch.dict(gate_inputs.os.environ,
                                {gate_inputs.PROBE_TIMEOUT_ENV: "77"}):
            with self.assertRaises(SystemExit) as cm:
                gate_inputs.derive()
        msg = str(cm.exception.code)
        self.assertIn("did not finish within 77s", msg)
        self.assertIn(gate_inputs.PROBE_TIMEOUT_ENV, msg)
        self.assertFalse(leaked.exists(), "a killed probe's partial output must not linger")


if __name__ == "__main__":
    unittest.main()
