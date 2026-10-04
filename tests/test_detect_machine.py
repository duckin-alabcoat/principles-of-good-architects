"""`detect_machine` falls back to `hostname` when there is no `scutil` (WI-0467).

The machine name came only from macOS's `scutil --get ComputerName`, so on any host
without it every session stamped UNKNOWN. These cases stub the two commands, so they run
the same on any machine, and pin that the macOS path is unchanged: when `scutil` answers,
`hostname` is never asked.

stdlib unittest: python3 -m unittest tests.test_detect_machine
"""

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

config = sys.modules[session.detect_machine.__module__]


def _stub_sh(answers):
    """A `sh` whose commands answer from `answers` ({argv[0]: stdout, or an exception}),
    recording every argv it is asked."""
    calls = []

    def sh(args, check=True, **_kw):
        calls.append(list(args))
        got = answers.get(args[0], FileNotFoundError(args[0]))
        if isinstance(got, BaseException):
            raise got
        return subprocess.CompletedProcess(args, 0, stdout=got + "\n", stderr="")

    return sh, calls


class DetectMachineTest(unittest.TestCase):
    def detect(self, answers, machine_map=None):
        sh, calls = _stub_sh(answers)
        cfg = dict(config.CFG or {}, machine_map=machine_map or {})
        with mock.patch.object(config, "sh", sh), mock.patch.object(config, "CFG", cfg):
            return config.detect_machine(), calls

    def test_macos_is_unchanged_and_never_asks_hostname(self):
        got, calls = self.detect({"scutil": "the operator's Machine", "hostname": "nope"},
                                 {"the operator's Machine": "devbox"})
        self.assertEqual(got, "devbox")
        self.assertEqual([c[0] for c in calls], ["scutil"])
        got, calls = self.detect({"scutil": "Some Laptop", "hostname": "nope"})
        self.assertEqual(got, "Laptop")
        self.assertEqual([c[0] for c in calls], ["scutil"])

    def test_no_scutil_falls_back_to_the_short_hostname(self):
        got, _ = self.detect({"hostname": "build-vm.example.internal"})
        self.assertEqual(got, "build-vm")

    def test_the_full_hostname_is_looked_up_in_the_map_first(self):
        """`poga init` keys machine_map by the full `hostname` output."""
        got, _ = self.detect({"hostname": "build-vm.example.internal"},
                             {"build-vm.example.internal": "cloud"})
        self.assertEqual(got, "cloud")

    def test_the_short_hostname_is_looked_up_too(self):
        got, _ = self.detect({"hostname": "build-vm.example.internal"}, {"build-vm": "cloud"})
        self.assertEqual(got, "cloud")

    def test_neither_command_answering_is_still_unknown(self):
        got, _ = self.detect({})
        self.assertEqual(got, "UNKNOWN")
        got, _ = self.detect({"hostname": subprocess.CalledProcessError(1, "hostname")})
        self.assertEqual(got, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
