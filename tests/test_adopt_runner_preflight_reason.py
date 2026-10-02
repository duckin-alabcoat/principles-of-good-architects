"""WI-0446: a failed auth preflight must carry claude's own reason, not the JSON preamble.

`preflight_auth` runs `claude -p ... --output-format json`. The JSON reply opens with
a usage/cost preamble well over 200 chars, and the failure detail was a 200-char HEAD
clip of the raw output, so the logged ABORT line held only `{"duration_api_ms":0,...`
and never said why. These tests fake `subprocess.run` and pin that the detail now
names `is_error`, `subtype` and the `result` text.

stdlib unittest: python3 -m unittest tests.test_adopt_runner_preflight_reason
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("adopt_runner", ROOT / "curate" / "adopt-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _reply(result, is_error=True, subtype="success"):
    """A claude JSON reply with the real field order: the preamble comes first and is
    long enough on its own to fill a 200-char head window."""
    return json.dumps({
        "duration_api_ms": 0,
        "stop_reason": "stop_sequence",
        "session_id": "0f3c9a2e-7b1d-4c55-9e8a-2d6b0c1f8e77",
        "total_cost_usd": 0,
        "usage": {"output_tokens_details": {"thinking_tokens": 0}, "input_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "output_tokens": 0, "server_tool_use": {"web_search_requests": 0}},
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result,
    })


def _run(returncode, stdout="", stderr=""):
    proc = subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)
    with mock.patch.object(runner.subprocess, "run", return_value=proc):
        return runner.preflight_auth("claude", timeout_s=5)


class PreflightReasonTest(unittest.TestCase):

    def test_json_failure_detail_carries_the_result_not_the_preamble(self):
        # A reason that is NOT an auth sign, so this exercises the plain-exit branch.
        stdout = _reply("Credit balance is too low", subtype="error_during_execution")
        self.assertGreater(stdout.index('"result"'), 200, "preamble must outrun the clip")
        self.assertFalse(runner.is_auth_error(stdout), "fixture must miss the auth branch")
        ok, detail = _run(1, stdout=stdout)
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("claude preflight exit 1: "), detail)
        self.assertIn("Credit balance is too low", detail)
        self.assertIn("is_error=True", detail)
        self.assertIn("subtype=error_during_execution", detail)
        self.assertNotIn("duration_api_ms", detail)

    def test_json_failure_includes_stderr_when_present(self):
        stdout = _reply("Credit balance is too low", subtype="error_during_execution")
        ok, detail = _run(1, stdout=stdout, stderr="warning: something on stderr")
        self.assertFalse(ok)
        self.assertIn("Credit balance is too low", detail)
        self.assertIn("warning: something on stderr", detail)

    def test_non_json_failure_reports_stderr(self):
        ok, detail = _run(2, stdout="", stderr="segfault in the renderer")
        self.assertFalse(ok)
        self.assertEqual(detail, "claude preflight exit 2: segfault in the renderer")

    def test_non_json_failure_puts_stderr_before_stdout(self):
        ok, detail = _run(2, stdout="partial stdout text", stderr="the stderr reason")
        self.assertFalse(ok)
        self.assertLess(detail.index("the stderr reason"), detail.index("partial stdout text"))

    def test_success_is_authenticated(self):
        ok, detail = _run(0, stdout=_reply("OK", is_error=False))
        self.assertEqual((ok, detail), (True, "authenticated"))

    def test_auth_error_json_names_the_reason(self):
        stdout = _reply("Invalid API key · Please run /login")
        ok, detail = _run(1, stdout=stdout)
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("not authenticated: "), detail)
        self.assertIn("Invalid API key · Please run /login", detail)
        self.assertIn("is_error=True", detail)
        self.assertIn("subtype=success", detail)
        self.assertNotIn("duration_api_ms", detail)

    def test_auth_error_json_with_exit_zero_still_fails(self):
        ok, detail = _run(0, stdout=_reply("Not logged in · Please run /login"))
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("not authenticated: "), detail)
        self.assertIn("Not logged in", detail)


if __name__ == "__main__":
    unittest.main()
