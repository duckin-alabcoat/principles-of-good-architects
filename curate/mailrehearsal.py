#!/usr/bin/env python3
"""Run WI-0366's local full-path rehearsal and emit the artifact `mailacceptance` requires.

`curate/mailacceptance.py` will not report `ready-for-review` without a rehearsal record
carrying a `command`, an `artifact_sha256` naming retained output, and every name in
`mailacceptance.REHEARSAL_CHECKS` marked `passed`. Nothing produced that record, so the
one input a lane on this machine can actually supply had to be hand-written — which is a
claim about a test run rather than a result of one.

WHAT THIS IS NOT. It is not a second implementation of the rehearsal. Each check names the
tests that already exercise it, in the modules that own the mechanism, and the verdict is
their exit status. Writing fresh scenarios beside the real ones would produce a rehearsal
that proves its own fixtures ([P16](../principles/master.md#p16--avoid-duplication)).

WHY ONE SUBPROCESS PER TEST rather than one run of the union. Two reasons, both measured
in this repository. These modules mutate `os.environ` and patch module globals in `setUp`,
so a shared interpreter makes one test's isolation another's starting state — the fault
that cost `tests/test_federation_migration.py` eighty errors in a serial run. And a per-test
exit code is a verdict that cannot be misread, where parsing a combined `-v` transcript
means deciding what a line means; the honest verb this repo already ships exists because a
run whose verdict is read off the wrong thing reports green while failing.

A CHECK IS NEVER PASSED BY DEFAULT. A test id that no longer resolves, a run that reports a
number of tests other than one, and a non-zero exit are three different failures and each
is recorded as itself. "Could not tell" is never folded into "fine": an absent subject is
how a rehearsal certifies a gap instead of finding it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailacceptance

ROOT = Path(__file__).resolve().parent.parent

# Each check names the tests that exercise it. Several checks share a test on purpose:
# `tests/test_federation_mail_release_e2e.py`'s release walk is one scenario that proves
# the second release, both directions and the rollback together, and splitting it into
# three would be three fixtures asserting one mechanism. Where a property is genuinely
# module-local — a signal arriving mid-cycle, a receive ledger — the test that owns it is
# named instead, because that is where a regression would actually land.
CHECK_TESTS = {
    # The old installation reaching the migration release, and the migration itself.
    "migration": (
        "tests.test_federation_migration.TheInstalledRunnerReachesThePreparation"
        ".test_v7_3_0s_runner_runs_the_incoming_tags_apply_command",
        "tests.test_federation_migration.APreparationMovesTheInstallationOntoExternalState"
        ".test_state_and_mail_arrive_and_the_source_is_left_byte_identical",
        "tests.test_federation_migration.AnInterruptedPreparationResumesAtItsBoundary"
        ".test_each_handoff_boundary_resumes_and_converges",
        "tests.test_federation_migration.TheRetiringCheckoutIsReadAndNeverWritten"
        ".test_the_retiring_checkout_is_derived_from_the_loaded_units",
    ),
    "second_release": (
        "tests.test_federation_mail_release_e2e.FederationMailReleaseE2E"
        ".test_two_releases_resume_bidirectional_mail_and_rollback_preserves_pending",
        "tests.test_production_mail_state.ExternalState"
        ".test_ack_state_separate_and_payload_survives_upgrade_rollback",
    ),
    "both_directions": (
        "tests.test_federation_mail_release_e2e.FederationMailReleaseE2E"
        ".test_two_releases_resume_bidirectional_mail_and_rollback_preserves_pending",
        # The control for the line above: without it, an entrypoint that returned
        # without running the worker would leave every delivery assertion unreached.
        "tests.test_federation_mail_release_e2e.FederationMailReleaseE2E"
        ".test_negative_control_detects_poller_that_returns_without_worker",
    ),
    "concurrent_producers": (
        "tests.test_production_mail_state.ExternalState"
        ".test_concurrent_producers_cannot_overwrite",
        "tests.test_mailtransport.MailTransportTest"
        ".test_overlapping_producers_and_workers_publish_every_identity_once",
    ),
    "lost_publication_confirmation": (
        "tests.test_mailtransport.MailTransportTest"
        ".test_process_death_after_remote_success_reconciles_confirmation",
        "tests.test_mailtransport.MailTransportTest"
        ".test_retry_cycle_confirms_the_remote_ref_and_not_its_own_push_status",
        "tests.test_maildelivery.AcknowledgmentAuthorizationTests"
        ".test_a_lost_acknowledgment_leaves_the_sender_pending_until_one_arrives",
    ),
    "worker_restart": (
        "tests.test_mail_worker.WorkerTests"
        ".test_service_stop_unwinds_work_before_releasing_lock",
        "tests.test_mail_worker.WorkerTests"
        ".test_deadline_retains_queue_and_allows_next_cycle",
        "tests.test_mailtransport.MailTransportTest"
        ".test_worker_restart_without_its_transport_repository_republishes_nothing",
    ),
    "duplicate_delivery": (
        "tests.test_maildelivery.MailDeliveryTests"
        ".test_delivery_queues_ack_and_restart_does_not_copy_twice",
        "tests.test_mailtransport.MailTransportTest"
        ".test_delivered_mail_is_retained_as_provenance_and_never_republished",
        "tests.test_federation_migration.StrandedMailInTheReleaseTreeIsRecovered"
        ".test_a_published_message_is_not_recovered_and_sent_again",
    ),
    "rollback": (
        "tests.test_federation_migration.RollbackRestoresTheArrangementItReplaced"
        ".test_the_preserved_definitions_come_back_and_the_queue_survives",
        "tests.test_federation_migration.RollbackRestoresTheArrangementItReplaced"
        ".test_it_refuses_to_restore_a_program_that_cannot_read_the_queue",
        # The runner's AUTOMATIC rollback, which is a different mechanism from the
        # migration's and from the `--rollback` verb: a verify failure has to reach it
        # with nobody invoking anything.
        "tests.test_deploy_runner.TestFailurePaths"
        ".test_verify_failure_rolls_back_and_confirms_the_old_version",
        "tests.test_deploy_drill.TestTheDrillRunsTheRealFailurePath"
        ".test_the_forced_failure_actually_reaches_the_rollback",
    ),
    "code_unchanged": (
        "tests.test_production_mail_state.ExternalState"
        ".test_actual_service_call_sites_leave_detached_release_unchanged",
    ),
    "old_checkout_unchanged": (
        "tests.test_federation_migration.TheRetiringCheckoutIsReadAndNeverWritten"
        ".test_the_retiring_checkout_is_left_byte_identical",
        # Without this the line above passes for free: a migration that never reads the
        # retiring tree also leaves it unchanged.
        "tests.test_federation_migration.TheRetiringCheckoutIsReadAndNeverWritten"
        ".test_that_reading_can_actually_fail",
    ),
}


class RehearsalError(RuntimeError):
    """The rehearsal could not be run or described honestly."""


def _now():
    return datetime.now(timezone.utc)


def validate_mapping(mapping=None):
    """Every declared check is mapped, and nothing is mapped that is not declared.

    `mailacceptance.REHEARSAL_CHECKS` is the authority. Deriving the subject list from
    this file instead would let a check added there acquire a rehearsal that never asks
    about it — the shape where a widened requirement reads as satisfied by an older
    answer."""
    mapping = CHECK_TESTS if mapping is None else mapping
    declared = set(mailacceptance.REHEARSAL_CHECKS)
    mapped = set(mapping)
    if mapped != declared:
        missing = sorted(declared - mapped)
        extra = sorted(mapped - declared)
        raise RehearsalError(
            "the rehearsal mapping does not match mailacceptance.REHEARSAL_CHECKS: "
            f"unmapped {missing or 'none'}, unknown {extra or 'none'}")
    for check, ids in mapping.items():
        if not ids:
            raise RehearsalError(f"check {check!r} names no test, so it could only pass vacuously")
    return mapping


def run_one(test_id, *, python=None, root=None, runner=subprocess.run):
    """One test, one interpreter, one verdict. Returns a record; never raises."""
    root = Path(root or ROOT)
    argv = [python or sys.executable, "-B", "-m", "unittest", test_id, "-v"]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("POGA_FEDERATION_CONFIG", None)
    try:
        completed = runner(argv, cwd=str(root), capture_output=True, text=True,
                           env=environment, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": test_id, "outcome": "failed", "ran": None,
                "why": f"the run could not be completed: {type(exc).__name__}"}
    output = (completed.stdout or "") + (completed.stderr or "")
    # `Ran 0 tests` exits 0. A verdict read off the exit code alone would report a check
    # green for a test id that has stopped resolving to anything.
    ran = None
    for line in output.splitlines():
        if line.startswith("Ran ") and " test" in line:
            try:
                ran = int(line.split()[1])
            except (IndexError, ValueError):
                ran = None
    if ran != 1:
        why = f"the run reported {ran if ran is not None else 'no'} test(s), not exactly one"
    elif completed.returncode != 0:
        why = f"the test failed (exit {completed.returncode})"
    else:
        why = None
    return {"id": test_id, "outcome": "failed" if why else "passed", "ran": ran,
            "why": why, "output": output}


def rehearse(mapping=None, *, python=None, root=None, runner=subprocess.run, log=None):
    """Run every mapped test once and return (record, transcript-bytes)."""
    mapping = validate_mapping(mapping)
    log = log or (lambda _message: None)
    results, transcript = {}, []
    seen = {}
    for check in mailacceptance.REHEARSAL_CHECKS:
        rows = []
        for test_id in mapping[check]:
            # A test named by two checks is RUN once and reported to both. Running it
            # twice would let the same subject produce two disagreeing verdicts.
            if test_id not in seen:
                seen[test_id] = run_one(test_id, python=python, root=root, runner=runner)
                record = seen[test_id]
                transcript.append(f"=== {test_id} — {record['outcome']} ===\n"
                                  + record.get("output", "") + "\n")
                log(f"rehearsal: {record['outcome']:<6} {test_id}")
            rows.append({key: value for key, value in seen[test_id].items() if key != "output"})
        results[check] = rows
    checks = {check: ("passed" if all(row["outcome"] == "passed" for row in rows) else "failed")
              for check, rows in results.items()}
    gaps = [f"{check}: {row['id']} — {row['why']}"
            for check, rows in results.items() for row in rows if row["why"]]
    body = "".join(transcript).encode("utf-8")
    record = {
        "schema_version": 1,
        "kind": "local-rehearsal",
        "observed_at": _now().isoformat(),
        "command": "python3 curate/mailrehearsal.py run",
        "artifact_sha256": hashlib.sha256(body).hexdigest(),
        "checks": checks,
        "tests": results,
        "gaps": gaps,
        # Said in the artifact itself so a reader of the file cannot mistake it for more
        # than it is. `mailacceptance.check` says the same thing and both should.
        "note": ("Local test results only. These cannot satisfy any live-host requirement "
                 "and do not start OPS-0010."),
    }
    return record, body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run the rehearsal and write its artifact")
    run.add_argument("--out", help="where to retain the transcript the digest names")
    run.add_argument("--quiet", action="store_true")
    sub.add_parser("checks", help="print the mapping without running anything")
    args = parser.parse_args(argv)
    try:
        if args.action == "checks":
            validate_mapping()
            print(json.dumps({check: list(ids) for check, ids in CHECK_TESTS.items()},
                             indent=2, sort_keys=True))
            return 0
        out = Path(args.out) if args.out else ROOT / ".session-state" / "wi-0366-rehearsal.log"
        record, body = rehearse(log=None if args.quiet else
                                (lambda message: print(message, file=sys.stderr)))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
        record["artifact"] = str(out)
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0 if all(value == "passed" for value in record["checks"].values()) else 1
    except (RehearsalError, OSError, ValueError) as exc:
        print(f"rehearsal unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
