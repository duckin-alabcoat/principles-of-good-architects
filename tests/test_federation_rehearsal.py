"""WI-0366: the rehearsal artifact is produced by a run, and can say the run failed.

`curate/mailrehearsal.py` exists to stop the one acceptance input a lane on this machine
can supply from being hand-written. That only helps if the driver can report a gap — a
producer that cannot fail is a rubber stamp with a digest on it, and the digest makes it
look like evidence. So the tests that matter here are the failing ones.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
import mailacceptance as acceptance
import mailrehearsal as rehearsal

# The acceptance fixture builds a manifest `validate_manifest` accepts. Copying those
# thirty lines here would be a second copy of a schema that is already strict about its
# own shape; when the schema moves, one copy moves with it and the other starts asserting
# a manifest nothing accepts.
from tests.test_mailacceptance import EvidenceTests


def _completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TheMappingIsDerivedFromTheAcceptanceLedger(unittest.TestCase):
    """`mailacceptance.REHEARSAL_CHECKS` is the authority. A rehearsal that took its own
    key list as the subject would answer a question the ledger had stopped asking."""

    def test_the_shipped_mapping_covers_exactly_the_declared_checks(self):
        self.assertEqual(set(rehearsal.validate_mapping()), set(acceptance.REHEARSAL_CHECKS))

    def test_a_check_the_ledger_adds_is_refused_rather_than_ignored(self):
        short = {key: value for key, value in rehearsal.CHECK_TESTS.items()
                 if key != acceptance.REHEARSAL_CHECKS[0]}
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unmapped"):
            rehearsal.validate_mapping(short)

    def test_a_check_mapped_to_nothing_is_refused_rather_than_passing_vacuously(self):
        empty = dict(rehearsal.CHECK_TESTS, rollback=())
        with self.assertRaisesRegex(rehearsal.RehearsalError, "vacuously"):
            rehearsal.validate_mapping(empty)

    def test_a_mapping_naming_a_check_the_ledger_does_not_declare_is_refused(self):
        extra = dict(rehearsal.CHECK_TESTS, invented_check=("tests.test_outbox",))
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unknown"):
            rehearsal.validate_mapping(extra)


class ARunThatDidNotProveAnythingIsNotAPass(unittest.TestCase):
    """The three ways a check can fail to be evidence, each recorded as itself."""

    def test_a_test_that_fails_fails_its_check(self):
        record, _ = rehearsal.rehearse(
            runner=lambda *a, **k: _completed(1, "Ran 1 test in 0.1s\n\nFAILED (failures=1)\n"))
        self.assertTrue(all(value == "failed" for value in record["checks"].values()))
        self.assertTrue(all("exit 1" in gap for gap in record["gaps"]))

    def test_a_run_reporting_no_tests_is_not_a_pass_however_it_exited(self):
        """`unittest` exits 0 for `Ran 0 tests`. A verdict read off the exit code alone
        would report every check green the day a test id stopped resolving."""
        record, _ = rehearsal.rehearse(
            runner=lambda *a, **k: _completed(0, "\nRan 0 tests in 0.0s\n\nOK\n"))
        self.assertTrue(all(value == "failed" for value in record["checks"].values()))
        self.assertIn("not exactly one", record["gaps"][0])

    def test_a_run_that_could_not_start_is_a_failure_not_an_absence(self):
        def explode(*_args, **_kwargs):
            raise OSError("no interpreter")
        record, _ = rehearsal.rehearse(runner=explode)
        self.assertTrue(all(value == "failed" for value in record["checks"].values()))
        self.assertIn("could not be completed", record["gaps"][0])

    def test_a_test_id_that_no_longer_resolves_fails_for_real(self):
        """Not a stub. A real interpreter, a real id that names nothing, so the guard is
        proved against the thing it is guarding rather than against a fake of it."""
        result = rehearsal.run_one("tests.test_federation_rehearsal.NoSuchClass.no_such_test")
        self.assertEqual(result["outcome"], "failed")
        self.assertIsNotNone(result["why"])


class TheArtifactDescribesTheRunItCameFrom(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def runner(argv, **_kwargs):
            self.calls.append(argv)
            return _completed(0, "Ran 1 test in 0.01s\n\nOK\n")
        self.record, self.body = rehearsal.rehearse(runner=runner)

    def test_every_check_passed_and_the_digest_is_of_the_bytes_written(self):
        self.assertTrue(all(value == "passed" for value in self.record["checks"].values()))
        self.assertEqual(self.record["artifact_sha256"], hashlib.sha256(self.body).hexdigest())
        self.assertEqual(self.record["gaps"], [])

    def test_a_test_named_by_two_checks_is_run_once_and_reported_to_both(self):
        """The release walk proves the second release, both directions and the rollback
        in one scenario. Running it per check would let one subject return two verdicts."""
        ids = [argv[-2] for argv in self.calls]
        self.assertEqual(len(ids), len(set(ids)))
        shared = ("tests.test_federation_mail_release_e2e.FederationMailReleaseE2E"
                  ".test_two_releases_resume_bidirectional_mail_and_rollback_preserves_pending")
        named_by = [check for check, tests in rehearsal.CHECK_TESTS.items() if shared in tests]
        self.assertGreater(len(named_by), 1)
        for check in named_by:
            self.assertIn(shared, [row["id"] for row in self.record["tests"][check]])

    def test_the_transcript_names_every_test_and_its_outcome(self):
        text = self.body.decode()
        for tests in rehearsal.CHECK_TESTS.values():
            for test_id in tests:
                self.assertIn(test_id, text)

    def test_it_says_in_itself_that_it_cannot_satisfy_a_live_requirement(self):
        self.assertIn("cannot satisfy any live-host requirement", self.record["note"])
        self.assertNotIn("accepted", self.record)


class TheLedgerAcceptsWhatThisProduces(unittest.TestCase):
    """The two halves have to fit. An artifact the checker rejects on shape is worth
    nothing however green the run was, and neither program would notice on its own.

    The fixture is BORROWED, not inherited. Subclassing `EvidenceTests` re-runs its
    eleven tests under this module's name too — measured: 38 tests here instead of 27 —
    which doubles them in the suite and reports one subject's result twice."""

    setUp = EvidenceTests.setUp
    provenance = EvidenceTests.provenance
    observation = EvidenceTests.observation
    check = EvidenceTests.check

    def _artifact(self, runner):
        record, _ = rehearsal.rehearse(runner=runner)
        return record

    def test_a_passing_run_records_the_local_rehearsal(self):
        self.rehearsal = self._artifact(lambda *a, **k: _completed(0, "Ran 1 test\n\nOK\n"))
        result = self.check()
        self.assertEqual(result["local_rehearsal"], "recorded", result["local_gaps"])
        self.assertEqual(result["local_gaps"], [])

    def test_a_failing_run_leaves_the_rehearsal_incomplete(self):
        """The branch `tests/test_mailacceptance.py` never reaches: with every fixture
        supplying all checks `passed`, `local_gaps` was never non-empty, so the ten
        check names were inert strings as far as the suite was concerned."""
        self.rehearsal = self._artifact(lambda *a, **k: _completed(1, "Ran 1 test\n\nFAILED\n"))
        result = self.check()
        self.assertEqual(result["local_rehearsal"], "incomplete")
        self.assertEqual(sorted(result["local_gaps"]), sorted(acceptance.REHEARSAL_CHECKS))
        self.assertEqual(result["status"], "blocked")
        self.assertIs(result["accepted"], False)


class TheCommandItself(unittest.TestCase):
    def test_the_checks_verb_prints_the_mapping_without_running_a_test(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "curate" / "mailrehearsal.py"),
                              "checks"], cwd=str(ROOT), capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(set(json.loads(run.stdout)), set(acceptance.REHEARSAL_CHECKS))

    def test_the_command_the_artifact_names_is_the_one_that_produces_it(self):
        record, _ = rehearsal.rehearse(runner=lambda *a, **k: _completed(0, "Ran 1 test\n\nOK\n"))
        self.assertIn("curate/mailrehearsal.py", record["command"])
        self.assertTrue((ROOT / "curate" / "mailrehearsal.py").is_file())


if __name__ == "__main__":
    unittest.main()
