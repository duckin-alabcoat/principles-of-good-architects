"""WI-0468: the public CI carries a Linux job that runs the whole suite.

POGA claims its core on Linux (operator, 2026-10-02). That claim is only as good as the job
that checks it, so the job is pinned here: an Ubuntu runner, the install smoke run, and
the same `unittest discover` the macOS suite job runs. Read as text — the stdlib has no
YAML parser, and the checks are about presence, not structure.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The trunk keeps the file under public-overlay/; the public cut ships it at its real
# path and carries no overlay. Exactly one of the two exists in any tree.
_SOURCE = ROOT / "public-overlay" / ".github" / "workflows" / "ci.yml"
_SHIPPED = ROOT / ".github" / "workflows" / "ci.yml"
CI = _SOURCE if _SOURCE.is_file() else _SHIPPED


def _job(text, name):
    """The body of top-level job `name` under `jobs:` (two-space indent)."""
    m = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name),
                  text, re.M | re.S)
    return m.group(1) if m else ""


class CiLinuxJobTest(unittest.TestCase):

    def setUp(self):
        self.text = CI.read_text(encoding="utf-8")
        self.linux = _job(self.text, "linux")

    def test_there_is_a_linux_job_on_an_ubuntu_runner(self):
        self.assertTrue(self.linux, "no `linux` job in %s" % CI)
        self.assertRegex(self.linux, r"runs-on: ubuntu-")

    def test_it_runs_the_same_suite_as_the_macos_job(self):
        cmd = "python3 -B -m unittest discover -s tests"
        self.assertIn(cmd, _job(self.text, "suite"))
        self.assertIn(cmd, self.linux)

    def test_it_runs_the_install_smoke_on_bash(self):
        self.assertIn("./poga install", self.linux)
        self.assertIn("poga init --yes", self.linux)
        self.assertIn("SHELL=/bin/bash", self.linux)

    def test_the_macos_jobs_are_still_there(self):
        for job in ("checks", "suite"):
            self.assertIn("runs-on: macos-15", _job(self.text, job), job)


if __name__ == "__main__":
    unittest.main()
