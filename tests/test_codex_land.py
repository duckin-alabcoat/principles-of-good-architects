"""WI-0320: sandbox gates skip GUI probes and preserve actionable receipts.

ADR-0124 D3 removed the in-land serial derive, and with it the two tests here that
drove `_gate_lane_candidate`'s refusal receipts. What they were protecting — that a
failed measurement names its exit and its real stderr rather than a generic refusal —
now belongs to whoever runs the deriver, which is the nightly obligation OPS-0009 and
no longer any land. The land-side invariant that replaces them is the last test in
this file: the land must not spawn a derive at all."""
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session


class SandboxGateTest(unittest.TestCase):
    def test_dictionary_probe_skips_under_gate_with_reason(self):
        code = """
import unittest
import test_attach
case = test_attach.GhosttyTabsTest('test_the_script_compiles_against_ghosttys_real_dictionary')
result = unittest.TestResult()
case.run(result)
assert len(result.skipped) == 1, (result.failures, result.errors)
assert 'GUI service' in result.skipped[0][1], result.skipped
"""
        result = subprocess.run([sys.executable, '-c', code],
                                cwd=pathlib.Path(__file__).resolve().parent,
                                env={**os.environ, 'POGA_GATE': '1'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_commands_and_receipt_use_session_interpreter(self):
        import json
        cfg = json.loads((pathlib.Path(session.__file__).parent / 'session.config.json').read_text())
        for config in (cfg, {}):
            with self.subTest(config=config), mock.patch.object(session, 'CFG', config), \
                    mock.patch.object(session, 'sh', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                ok, receipt = session._run_gate()
            self.assertTrue(ok)
            self.assertTrue(run.call_args_list)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][0], sys.executable)
            version = '.'.join(map(str, sys.version_info[:3]))
            self.assertIn(f'gated with: python {version} ({sys.executable})', receipt)

    def test_measurement_suite_uses_current_interpreter(self):
        from curate import gate_inputs
        with mock.patch.object(sys, 'executable', '/test/current-python'):
            self.assertEqual(gate_inputs.GATE_SUITE()[0], sys.executable)


class TheLandNeverDerivesTest(unittest.TestCase):
    """ADR-0124 D3, as a structural guard rather than a promise.

    The serial derive was 271 seconds inside the one-at-a-time lock, and it got there by
    being a reasonable thing to do at the point where the record was known to be stale.
    Nothing stops it being re-added at that same point by the same reasoning, so the
    prohibition is a test: no land path may pass `--derive` to anything. The check is over
    the SOURCE of the landers, because a behavioural test can only catch the paths a
    fixture happens to drive, and this is a claim about all of them.

    Prose is exempt, deliberately and by parsing rather than by pattern. Comments,
    docstrings and the operator-facing WARN line SHOULD name the deriver — that line is
    the one place a reader is told the command and who owns it now. What is forbidden is
    the flag appearing in an ARGV: a list or tuple literal, which is the only shape
    anything here ever spawns a process with. A grep would have to forbid the explanation
    along with the call, which is how a guard teaches people to delete the comment."""

    def test_no_lander_spawns_the_deriver(self):
        import ast
        root = pathlib.Path(session.__file__).resolve().parent
        for name in ("sessionlib/land.py", "sessionlib/lanes.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            argv = []
            for node in ast.walk(tree):
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                for element in node.elts:
                    if (isinstance(element, ast.Constant)
                            and isinstance(element.value, str)
                            and "--derive" in element.value):
                        argv.append(element.lineno)
            self.assertEqual(argv, [],
                             f"{name} builds an argv containing --derive (line "
                             f"{argv[0] if argv else '?'}); ADR-0124 D3 took the derive "
                             f"out of the land path and OPS-0009 owns it now")

    def test_the_gate_runner_takes_no_reuse_evidence(self):
        """`serial_derive_exit` was how the derive's verdict reached the gate. Its
        absence is what makes the removal complete rather than merely unused: a parameter
        left behind is an invitation to supply it again.

        WI-0347 added `skip` — the set of gate commands whose own measured input set the
        lane's diff does not touch. It is NOT reuse evidence returning under a new name,
        and the distinction is the one this test exists to keep: `serial_derive_exit`
        carried a VERDICT from an earlier run of the suite, so the gate could decline to
        re-decide; `skip` carries a statement about the DIFF, and every command it names
        is one whose verdict this diff cannot change. So the assertion stays an exact
        parameter list — a third parameter still has to come and argue its case here —
        and the banned name is checked by name."""
        import inspect
        params = list(inspect.signature(session._run_gate).parameters)
        self.assertEqual(params, ["cwd", "skip"])
        self.assertNotIn("serial_derive_exit", params)
