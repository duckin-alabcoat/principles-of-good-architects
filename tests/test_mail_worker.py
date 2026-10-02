"""Bounded scheduler/worker contracts, without sessions, credentials, or live launchd."""
from __future__ import annotations

from datetime import timedelta
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
import mailqueue
import mailworker
import production
from production import Roots

spec = importlib.util.spec_from_file_location("mail_worker_installer", ROOT / "deploy/install-mail-worker.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def hold_cycle_lock(roots, connected):
    with mailworker.cycle_lock(roots) as acquired:
        connected.send(acquired)
        connected.recv()


class WorkerFixture:

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.code = base / "release"
        self.state = base / "state"
        self.config = base / "config"
        for path in (self.code / "curate", self.state, self.config):
            path.mkdir(parents=True)
        (self.code / "curate/mailworker.py").write_text("# fixture\n")
        self.roots = Roots(self.code, self.state, base / "transport.git", self.config, None, {})
        self.events = []

    def publish(self, roots):
        self.events.append("publish")
        for item in mailqueue.pending(roots):
            mailqueue.mark(roots, item.message_id, "published", evidence="fixture")
        return {"status": "ok", "last_publication": mailworker._now().isoformat()}

    def consume(self, roots):
        self.events.append("deliver")
        outcomes = []
        for item in mailqueue.pending(roots):
            if item.provenance.get("kind") != "delivery-ack":
                mailqueue.mark(roots, item.message_id, "delivered", evidence="fixture")
                outcomes.append({"message_id": "ack-envelope-id", "outcome": "acknowledged"})
                mailqueue.enqueue(roots, "transport-ack", "ack.json", "fixture", message_id="ack-id",
                                  provenance={"kind": "delivery-ack"})
        return {"outcomes": outcomes, "snapshots": [{"ref": "refs/heads/other"}], "errors": []}


class WorkerTests(WorkerFixture, unittest.TestCase):
    def test_unattended_failure_then_retry_without_new_mail_or_hook(self):
        mailqueue.enqueue(self.roots, "recipient", "brief.md", "work", message_id="original")
        failed = lambda _roots: {"status": "failed", "failure": "transport Git operation failed (exit 128)"}
        idle = lambda _roots: {"outcomes": [], "errors": [], "snapshots": []}
        first = mailworker.run_cycle(self.roots, publish=failed, consume=idle)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["pending_count"], 1)
        second = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        self.assertEqual(second["status"], "ok")
        self.assertEqual(self.events, ["publish", "deliver", "publish"])
        self.assertEqual(second["pending_count"], 0)
        self.assertEqual(second["pending_ack_publication_count"], 0)
        self.assertGreaterEqual(second["last_observed_ack_latency_seconds"], 0)
        self.assertTrue((self.state / "runtime" / mailworker.HEALTH_NAME).exists())
        self.assertFalse((self.code / ".session-state").exists())

    def test_corrupt_queue_entry_does_not_suppress_valid_work(self):
        mailqueue.enqueue(self.roots, "recipient", "good.md", "work", message_id="good")
        bad = self.state / "queue" / "broken"
        bad.mkdir()
        (bad / "envelope.json").write_text("broken JSON")
        events = []
        def publish(roots):
            events.append("publish")
            for item in mailqueue.pending(roots, on_error=lambda *_: None):
                mailqueue.mark(roots, item.message_id, "published", evidence="fixture")
            return {"status": "ok"}
        result = mailworker.run_cycle(self.roots, publish=publish,
                                      consume=lambda _: {"outcomes": [], "errors": []})
        self.assertEqual(events, ["publish", "publish"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["corrupt_queue_count"], 1)
        self.assertIsNone(result["pending_count"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "good")["state"], "published")
        self.assertEqual(mailworker.read_status(self.roots)["status"], "unknown")
        self.assertTrue(bad.exists())

    def test_process_overlap_leaves_owner_health_untouched(self):
        context = multiprocessing.get_context("fork")
        parent, child = context.Pipe()
        process = context.Process(target=hold_cycle_lock, args=(self.roots, child))
        process.start()
        try:
            self.assertTrue(parent.recv())
            result = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
            self.assertEqual(result["status"], "busy")
            self.assertEqual(self.events, [])
            self.assertFalse((self.state / "runtime" / mailworker.HEALTH_NAME).exists())
        finally:
            parent.send("release")
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def test_unknown_stopped_failed_and_aged_are_distinct(self):
        self.assertEqual(mailworker.read_status(self.roots)["status"], "unknown")
        result = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        self.assertEqual(mailworker.read_status(self.roots)["status"], "ok")
        later = mailworker._date(result["observed_at"]) + timedelta(hours=1)
        self.assertEqual(mailworker.read_status(self.roots, later)["status"], "stale")
        mailqueue.enqueue(self.roots, "recipient", "old.md", "aged", message_id="old")
        envelope = self.state / "queue" / mailqueue.identity_key("old") / "envelope.json"
        data = json.loads(envelope.read_text())
        data["created_at"] = (mailworker._now() - timedelta(hours=2)).isoformat()
        envelope.write_text(json.dumps(data))
        self.assertEqual(mailworker.read_status(self.roots)["status"], "aged")
        failure = lambda _roots: {"status": "failed", "failure": "transport Git operation timed out"}
        mailworker.run_cycle(self.roots, publish=failure, consume=lambda _: {"errors": []})
        self.assertEqual(mailworker.read_status(self.roots)["status"], "failed")

    def test_deadline_retains_queue_and_allows_next_cycle(self):
        self.roots.transport["max_cycle_seconds"] = 1
        mailqueue.enqueue(self.roots, "recipient", "brief.md", "work", message_id="slow")
        before = time.monotonic()
        result = mailworker.run_cycle(self.roots, publish=lambda _: time.sleep(10), consume=self.consume)
        self.assertLess(time.monotonic() - before, 3)
        self.assertIn("deadline exceeded", result["failure"])
        self.assertEqual(len(mailqueue.pending(self.roots)), 1)
        self.assertEqual(mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)["status"], "ok")

    def test_service_stop_unwinds_work_before_releasing_lock(self):
        previous = signal.getsignal(signal.SIGTERM)
        cleaned = []
        def terminated(_roots):
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            finally:
                cleaned.append(True)
        result = mailworker.run_cycle(self.roots, publish=terminated, consume=self.consume)
        self.assertIn("service termination", result["failure"])
        self.assertEqual(cleaned, [True])
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)["status"], "ok")

    def test_no_implicit_production_execution(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mailworker.main(["--quiet"]), 2)
        self.assertFalse((self.state / "runtime").exists())

    def test_cli_status_uses_real_schema_validation_without_network(self):
        for args in (("init", "--quiet"), ("add", "curate/mailworker.py"),
                     ("commit", "--quiet", "-m", "fixture"), ("checkout", "--quiet", "--detach")):
            subprocess.run(["git", "-C", str(self.code), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@localhost", "-c", "commit.gpgsign=false", *args],
                           check=True, capture_output=True)
        with mock.patch.object(mailworker, "ROOT", self.code):
            for name in ("mailboxes.json", "repo-paths.local", "reconcile-roots.local"):
                (self.config / name).write_text("{}" if name.endswith("json") else "")
            cfg = self.config / "production.json"
            value = {"schema_version": 1, "code_root": str(self.code), "state_root": str(self.state),
                     "config_root": str(self.config), "transport_root": str(self.roots.transport_root)}
            cfg.write_text(json.dumps(value))
            with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(mailworker.main(["--config", str(cfg), "--status", "--json"]), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(mailworker, "run_cycle") as run, \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                def observe(roots):
                    self.assertEqual(os.environ["POGA_FEDERATION_CONFIG"], str(cfg))
                    self.assertEqual(roots.state_root, self.state)
                    return {"status": "ok"}
                run.side_effect = observe
                self.assertEqual(mailworker.main(["--config", str(cfg), "--quiet"]), 0)
                self.assertNotIn("POGA_FEDERATION_CONFIG", os.environ)
            value["schema_version"] = 99
            cfg.write_text(json.dumps(value))
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(mailworker.main(["--config", str(cfg), "--status"]), 2)
            self.assertFalse(self.roots.transport_root.exists())

    def test_bounds_refuse_before_work(self):
        self.roots.transport["max_cycle_seconds"] = 0
        with self.assertRaises(ValueError):
            mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        self.assertEqual(self.events, [])


class FakeLaunchd:
    def __init__(self, domain="user/1000"):
        self.domain = domain
        self.services = {}
        self.calls = []
        self.fail_bootstrap = False

    def __call__(self, args):
        self.calls.append(args)
        verb, target = args[:2]
        if verb == "print":
            if target == self.domain:
                return 0, "domain fixture"
            if target in self.services:
                return 0, "path = " + self.services[target] + "\n"
            return 113, ""
        if verb == "bootout":
            self.services.pop(target, None)
        elif verb == "bootstrap":
            if self.fail_bootstrap:
                return 5, ""
            self.services[target + "/" + installer.LABEL] = args[2]
        return 0, ""


class InstallerTests(WorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.destination = Path(self.tmp.name) / "scheduler" / (installer.LABEL + ".plist")
        self.cfgfile = self.config / "production.json"
        self.cfgfile.write_text("{}")
        self.data = installer.render(self.roots, self.cfgfile, sys.executable)
        self.ctl = FakeLaunchd()

    def apply(self, **kwargs):
        return installer.change(self.destination, self.data, 1000, approved_roots=[self.code],
                                ctl=self.ctl, **kwargs)

    def test_render_explicit_external_roots_and_background(self):
        value = plistlib.loads(self.data)
        self.assertEqual(value["LimitLoadToSessionType"], "Background")
        self.assertEqual(value["Label"], installer.LABEL)
        self.assertEqual(value["StartInterval"], 600)
        self.assertIn(str(self.cfgfile), value["ProgramArguments"])
        self.assertTrue(value["StandardOutPath"].startswith(str(self.state)))
        self.assertEqual(self.ctl.calls, [])

    def test_scheduled_environment_does_not_write_code_bytecode(self):
        value = plistlib.loads(self.data)
        (self.code / "read_only_fixture.py").write_text("VALUE = 1\n")
        env = dict(os.environ)
        env.pop("PYTHONPYCACHEPREFIX", None)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        env.update(value["EnvironmentVariables"])
        subprocess.run([sys.executable, "-c", "import read_only_fixture"], cwd=self.code,
                       env=env, check=True, capture_output=True)
        self.assertFalse((self.code / "__pycache__").exists())

    def test_install_repeat_upgrade_uninstall(self):
        result = self.apply()
        self.assertEqual(result["status"], "registered")
        self.assertFalse(result["execution_verified"])
        self.apply()
        self.roots.transport["poll_interval_seconds"] = 900
        self.data = installer.render(self.roots, self.cfgfile, sys.executable)
        self.apply()
        self.assertEqual(plistlib.loads(self.destination.read_bytes())["StartInterval"], 900)
        self.apply(uninstall=True)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.ctl.services, {})
        self.assertEqual(self.apply(uninstall=True)["status"], "uninstalled")

    def test_explicit_old_gui_service_replaced_without_second_publisher(self):
        old = Path(self.tmp.name) / "old-agent.plist"
        old.write_bytes(self.data)
        self.ctl.services["gui/1000/" + installer.LABEL] = str(old)
        self.apply(legacy=[old])
        self.assertFalse(old.exists())
        self.assertTrue(old.with_suffix(".plist.pre-mail-worker").exists())
        self.assertEqual(list(self.ctl.services), ["user/1000/" + installer.LABEL])

    def test_unknown_service_or_missing_user_domain_is_refused_before_mutation(self):
        self.ctl.services["gui/1000/" + installer.LABEL] = "/unknown/other.plist"
        with self.assertRaises(installer.InstallError):
            self.apply()
        self.assertFalse(self.destination.exists())
        self.assertTrue(all(call[0] == "print" for call in self.ctl.calls))
        self.ctl = FakeLaunchd("user/502")
        with self.assertRaises(installer.InstallError):
            self.apply()
        self.assertFalse(self.destination.exists())

    def test_scheduler_failure_is_not_certified_success(self):
        self.ctl.fail_bootstrap = True
        with self.assertRaises(installer.InstallError):
            self.apply()
        self.assertFalse(self.ctl.services)



class ConfigurationBoundsTests(WorkerFixture, unittest.TestCase):
    """Every knob's RANGE, not just its default (WI-0364).

    `test_bounds_refuse_before_work` proved one value out of range refuses. It did not
    touch either END of any range, so eleven mutations that moved a bound by one second
    survived: every limit could be widened or narrowed and nothing in the repository
    failed. A range nobody has stood on the edge of is a comment with a syntax.

    The messages are matched rather than the type. Both refusals here raise `ValueError`
    and one is reachable only after the other passes, so a bare `assertRaises` stays green
    when the narrow rule is deleted.
    """

    # (key, low, high, other knobs needed to keep the cross-field rule satisfied)
    LIMITS = (("poll_interval_seconds", 30, 86400, {"stale_after_seconds": 604800}),
              ("stale_after_seconds", 60, 604800, {"poll_interval_seconds": 30}),
              ("aged_after_seconds", 60, 604800, {}),
              ("max_cycle_seconds", 1, 3600, {}))

    def _options(self, **knobs):
        self.roots.transport.clear()
        self.roots.transport.update(knobs)
        return mailworker.options(self.roots)

    def test_the_defaults_are_the_values_an_unconfigured_host_actually_runs(self):
        """Every range below is asserted with a knob set explicitly, which leaves the
        DEFAULTS — the numbers a host with no `transport` block runs on, which is every
        host today — asserted nowhere. A ten-minute poll and a thirty-minute staleness
        threshold are a scheduling decision, not an implementation detail."""
        self.assertEqual(self._options(), {"poll_interval_seconds": 600,
                                           "stale_after_seconds": 1800,
                                           "aged_after_seconds": 3600,
                                           "max_cycle_seconds": 240})

    def test_each_limit_accepts_its_own_endpoints(self):
        """The low and the high are IN the range. A mutation that moves either inward
        makes one of these refuse, which is the half a refusal-only test cannot see."""
        for key, low, high, extra in self.LIMITS:
            for value in (low, high):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self._options(**{key: value}, **extra)[key], value)

    def test_each_limit_refuses_one_step_outside_itself(self):
        for key, low, high, extra in self.LIMITS:
            for value in (low - 1, high + 1):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as caught:
                        self._options(**{key: value}, **extra)
                    self.assertIn(f"transport.{key} must be an integer between {low} and "
                                  f"{high}", str(caught.exception))

    def test_a_boolean_is_not_an_integer_here(self):
        """`True` is an `int` in Python and would otherwise pass as 1 second."""
        with self.assertRaises(ValueError) as caught:
            self._options(max_cycle_seconds=True)
        self.assertIn("must be an integer", str(caught.exception))

    def test_a_stale_threshold_at_or_below_the_poll_interval_is_refused(self):
        """The cross-field rule, which no test fed at all: a worker declared stale sooner
        than it is scheduled to run reports every healthy gap between cycles as a stopped
        worker. EQUAL is the case the boundary turns on, and it is the one that refuses —
        `<=`, not `<`, so a mutation to the stricter operator fails here."""
        with self.assertRaises(ValueError) as caught:
            self._options(poll_interval_seconds=100, stale_after_seconds=100)
        self.assertIn("stale_after_seconds must exceed poll_interval_seconds",
                      str(caught.exception))
        self.assertEqual(
            self._options(poll_interval_seconds=100, stale_after_seconds=101)
            ["stale_after_seconds"], 101,
            "one second clear of the interval is the first accepted value")

    def test_a_timestamp_without_a_timezone_is_refused_by_name(self):
        """Asserted on `_date` directly, and the reason is the interesting part: through
        every caller this rule is INDISTINGUISHABLE from its own absence. Drop the raise
        and the naive datetime reaches `now - stamp`, which raises TypeError, which lands
        in the very same `except (ValueError, TypeError)` handler and produces the very
        same 'invalid timestamp' entry. The guard is real, its message is what tells an
        operator which of the two happened, and only a direct call can see it."""
        with self.assertRaises(ValueError) as caught:
            mailworker._date("2026-01-01T00:00:00")
        self.assertIn("timestamp must include timezone", str(caught.exception))
        self.assertIsNotNone(mailworker._date("2026-01-01T00:00:00+00:00").tzinfo)


class HealthRecordDurabilityTests(WorkerFixture, unittest.TestCase):
    """The health file is the one thing that must still be readable when everything else
    is not, so its writer's failure behaviour is part of the contract (WI-0364)."""

    def _health(self):
        return self.state / "runtime" / mailworker.HEALTH_NAME

    def test_a_failed_write_leaves_no_temporary_file_behind(self):
        """The `finally` that unlinks the temp file was never exercised. A worker running
        every ten minutes turns a skipped unlink into thousands of `.mail-health-*` files
        in the state root, and the failure that produces them is invisible until the disk
        is — which is the opposite of what this file exists for."""
        with self.assertRaises(TypeError):
            mailworker._write(self.roots, {"unserializable": object()})
        # Every entry, not a glob for the prefix this writer happens to use: the claim is
        # that the directory is as it was, and a glob only rules out the one name it knows.
        self.assertEqual(list((self.state / "runtime").iterdir()), [],
                         "a half-written record must not survive its own write")
        self.assertFalse(self._health().exists(),
                         "and must never be promoted to the real name")

    def test_the_record_replaces_its_predecessor_rather_than_failing_on_it(self):
        """Two writes into one directory. `exist_ok` is the difference between a worker
        that runs every interval and one that succeeds exactly once."""
        mailworker._write(self.roots, {"schema_version": 1, "status": "first"})
        mailworker._write(self.roots, {"schema_version": 1, "status": "second"})
        self.assertEqual(json.loads(self._health().read_text())["status"], "second")

    def test_the_runtime_directory_is_created_whole_and_private(self):
        """The record names the host, the release and the queue's shape; it is written
        under a state root shared with nothing, and 0o700 is what keeps it that way."""
        deep = Path(self.tmp.name).resolve() / "absent" / "state"
        roots = Roots(self.code, deep, deep.parent / "t.git", self.config, None, {})
        mailworker._write(roots, {"schema_version": 1})
        self.assertTrue((deep / "runtime" / mailworker.HEALTH_NAME).is_file(),
                        "a missing state root is created, parents and all")
        self.assertEqual(oct(os.stat(deep / "runtime").st_mode)[-3:], "700")

    def test_a_record_from_another_schema_is_unreadable_rather_than_trusted(self):
        """`_record` refuses a version it does not know, and nothing fed it one. A record
        left by a future release would otherwise be read key-by-key by code written for
        version 1 — the one case where 'unknown' is the honest answer and the one case
        that returned a confident wrong one."""
        self._health().parent.mkdir(parents=True, exist_ok=True)
        self._health().write_text(json.dumps({"schema_version": 2, "status": "ok",
                                              "observed_at": mailworker._now().isoformat()}))
        self.assertIsNone(mailworker._record(self.roots))
        self.assertEqual(mailworker.read_status(self.roots)["status"], "unknown",
                         "a record we cannot read is not a healthy one")

    def test_the_lock_makes_the_directory_it_needs(self):
        """The lock sits beside the transport repository, and on a host that has never run
        a cycle that directory does not exist yet. Requiring it to be there in advance
        makes the first scheduled run — the one nobody is watching — the one that fails."""
        deep = Path(self.tmp.name).resolve() / "unmade" / "nested" / "transport.git"
        roots = Roots(self.code, self.state, deep, self.config, None, {})
        with mailworker.cycle_lock(roots) as acquired:
            self.assertTrue(acquired)
        self.assertTrue(deep.parent.is_dir())

    def test_the_cycle_lock_is_private_to_its_owner(self):
        """0o600. The lock fences a publisher that pushes this host's mail; a lock file
        another account can open is a lock another account can take."""
        with mailworker.cycle_lock(self.roots) as acquired:
            self.assertTrue(acquired)
            root = Path(self.roots.transport_root)
            lock = root.parent / ("." + root.name + ".mail-cycle.lock")
            self.assertEqual(oct(os.stat(lock).st_mode)[-3:], "600")


class RecordIdentityTests(WorkerFixture, unittest.TestCase):
    """The record says WHICH host and WHICH release produced it, and reports latency it
    measured rather than the interval it was configured with (WI-0364)."""

    def test_the_record_names_the_host_and_the_release_that_wrote_it(self):
        """`host` was written into every record and asserted by nothing. The state root is
        reachable from more than one machine by design, so a record that does not name its
        author is a reading of somebody else's worker presented as this one's."""
        record = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        self.assertEqual(record["host"], socket.gethostname())
        self.assertEqual(record["code_root"], str(self.code))
        self.assertEqual(record["release"], "unknown",
                         "a release with no readable commit is 'unknown', never blank")

    def test_the_release_is_read_from_the_code_root_and_falls_back_to_unknown(self):
        """`_release` shells out to git against the CONFIGURED release, not the process's
        working directory — a worker started by launchd has no useful cwd, and a release
        read from the wrong tree would name a commit that is not the one running. The
        fallback is the word 'unknown', never an empty string, because an empty field in
        the record reads as 'nothing was wrong' rather than 'nobody could tell'."""
        self.assertEqual(mailworker._release(self.roots), "unknown",
                         "a code root that is not a checkout answers, rather than raising")
        for args in (("init", "--quiet"), ("commit", "--quiet", "--allow-empty", "-m", "x")):
            subprocess.run(["git", "-C", str(self.code), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@localhost",
                            "-c", "commit.gpgsign=false", *args],
                           check=True, capture_output=True)
        head = subprocess.run(["git", "-C", str(self.code), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(mailworker._release(self.roots), head)

    def test_latency_is_the_age_it_measured_and_not_the_interval_it_was_given(self):
        """The item asks for ACTUAL transport latency rather than a promise of delivery
        within one interval. The existing assertion was `>= 0`, which a constant zero
        satisfies — so the number could have stopped being a measurement and nothing would
        have failed. This pins it against a message whose age is known."""
        mailqueue.enqueue(self.roots, "recipient", "old.md", "aged", message_id="old")
        envelope = self.state / "queue" / mailqueue.identity_key("old") / "envelope.json"
        data = json.loads(envelope.read_text())
        data["created_at"] = (mailworker._now() - timedelta(hours=2)).isoformat()
        envelope.write_text(json.dumps(data))
        record = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        self.assertGreaterEqual(record["last_observed_ack_latency_seconds"], 7200)
        self.assertNotEqual(record["last_observed_ack_latency_seconds"],
                            record["poll_interval_seconds"],
                            "the reported latency is observed, not the configured cadence")


class DeadlineDisciplineTests(WorkerFixture, unittest.TestCase):
    """`deadline` installs a process-wide alarm and two process-wide signal handlers, and
    both of its refusals about that were unreachable from any test (WI-0364)."""

    def test_it_refuses_to_arm_from_a_thread_that_cannot_receive_the_signal(self):
        """Python delivers every signal on the main thread. Armed from a worker thread the
        alarm fires somewhere the cycle cannot see, so the bound silently stops existing —
        which is worse than no bound, because the record still reports one."""
        failures = []

        def arm():
            try:
                with mailworker.deadline(5):
                    pass
            except RuntimeError as exc:
                failures.append(str(exc))

        thread = threading.Thread(target=arm)
        thread.start()
        thread.join(10)
        self.assertEqual(len(failures), 1)
        self.assertIn("must run in the main thread", failures[0])

    def test_it_refuses_to_replace_an_alarm_somebody_else_armed(self):
        """`setitimer` is global and returns no ownership. Entering over a live alarm
        cancels it and hands it back wrong on exit, so the OTHER timer's owner waits
        forever. The read is of the REMAINING TIME, element 0 — element 1 is the repeat
        interval, which is 0.0 for the one-shot timers this module sets, so a guard
        reading it never fires at all."""
        signal.setitimer(signal.ITIMER_REAL, 30)
        try:
            with self.assertRaises(RuntimeError) as caught:
                with mailworker.deadline(5):
                    pass
            self.assertIn("cannot replace an existing process alarm", str(caught.exception))
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0,
                               "the refusal must leave the other timer running")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)


class QueueStatusFieldsTests(WorkerFixture, unittest.TestCase):
    """Every field the health record publishes, read by name.

    Three of them — `known_pending_count`, `queue_errors`, `unpublished_count` — could be
    renamed to anything at all and the whole repository stayed green. A field that is
    computed, written into the record an operator reads, and asserted by nothing is the
    same defect as `unmatched-ack` one layer down (WI-0363): present, plausible, and
    load-bearing for nobody.
    """

    def setUp(self):
        super().setUp()
        mailqueue.enqueue(self.roots, "alpha-arch", "one.md", "first", message_id="one")
        mailqueue.enqueue(self.roots, "alpha-arch", "two.md", "second", message_id="two")
        mailqueue.mark(self.roots, "two", "published", evidence="fixture")
        mailqueue.enqueue(self.roots, "transport-ack", "ack.json", "{}", message_id="ack",
                          provenance={"kind": "delivery-ack"})
        corrupt = self.state / "queue" / "corrupt-entry"
        corrupt.mkdir(parents=True)
        (corrupt / "envelope.json").write_text("{ not json")

    def test_every_published_count_is_the_thing_its_name_says(self):
        status = mailworker.queue_status(self.roots)
        self.assertEqual(status["known_pending_count"], 2,
                         "the two data messages; an acknowledgment is not someone's mail")
        self.assertIsNone(status["pending_count"],
                          "a queue with an unreadable entry has no trustworthy total")
        self.assertEqual(status["corrupt_queue_count"], 1)
        self.assertEqual([e["entry"] for e in status["queue_errors"]], ["corrupt-entry"],
                         "the errors name the entry, so the operator can go and read it")
        self.assertEqual(status["unpublished_count"], 2,
                         "'one' and the ack are still queued; 'two' is published")
        self.assertEqual(status["pending_ack_publication_count"], 1)
        self.assertIsNotNone(status["oldest_pending_age_seconds"])

    def test_a_readable_queue_reports_a_total_rather_than_none(self):
        """The positive control for `pending_count` being None above — without it that
        assertion passes on an implementation that never computes a total at all."""
        shutil.rmtree(self.state / "queue" / "corrupt-entry")
        self.assertEqual(mailworker.queue_status(self.roots)["pending_count"], 2)

    def test_an_entry_with_an_unusable_timestamp_is_an_error_not_an_age(self):
        shutil.rmtree(self.state / "queue" / "corrupt-entry")
        envelope = self.state / "queue" / mailqueue.identity_key("one") / "envelope.json"
        data = json.loads(envelope.read_text())
        data["created_at"] = "not-a-date"
        envelope.write_text(json.dumps(data))
        status = mailworker.queue_status(self.roots)
        self.assertEqual([e["failure"] for e in status["queue_errors"]],
                         ["invalid timestamp"])
        self.assertIsNone(status["pending_count"])


class CycleFailureLabellingTests(WorkerFixture, unittest.TestCase):
    """A failure is reported with the stage it happened in, including the stage before
    the first one (WI-0364)."""

    def test_a_failure_before_any_stage_is_labelled_for_the_queue_read(self):
        """`stage` is seeded with "queue" precisely because the queue is read before the
        loop that reassigns it. Nothing exercised that seed, so a failure reading the
        queue could have been reported under any stage name — or none."""
        with mock.patch.object(mailworker, "queue_status",
                               side_effect=RuntimeError("boom")):
            record = mailworker.run_cycle(self.roots, publish=self.publish,
                                          consume=self.consume)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["failure"].startswith("queue: "), record["failure"])
        self.assertEqual(self.events, [], "no stage ran, so none may be reported")


class StageOutcomeRecordingTests(WorkerFixture, unittest.TestCase):
    """What each stage writes into the record, field by field (WI-0364).

    The health record is the only thing anyone reads about a worker nobody is watching,
    and most of what it says about the delivery stage was asserted nowhere: the fetch,
    publication and delivery timestamps, the `elsewhere` count, and which of two very
    different delivery faults occurred. A field that is written and never read is the
    `unmatched-ack` defect WI-0363 fixed, one layer up.
    """

    def _delivery(self, **value):
        value.setdefault("snapshots", [])
        value.setdefault("outcomes", [])
        value.setdefault("errors", [])
        return mailworker.run_cycle(
            self.roots, publish=lambda _roots: {"status": "ok"},
            consume=lambda _roots: value)

    def test_a_fetch_that_returned_nothing_does_not_count_as_a_successful_fetch(self):
        """`snapshots` is the evidence the remote was actually read. A cycle that reached
        the remote and found nothing, and a cycle that never reached it, are the same
        shape here — so the timestamp has to come from the evidence, not the attempt."""
        empty = self._delivery()
        self.assertIsNotNone(empty["last_successful_fetch"],
                             "the publication stage's own ok still counts as a fetch")
        before = empty["last_successful_fetch"]
        fetched = self._delivery(snapshots=[{"ref": "refs/heads/other", "commit": "a" * 40}])
        self.assertGreater(fetched["last_successful_fetch"], before)

    def test_delivered_and_already_delivered_both_prove_delivery_and_elsewhere_does_not(self):
        """`already-delivered` is what a retry after a crash reports, and it is evidence
        the message arrived. `elsewhere` is someone else's mail and proves nothing about
        ours; counting it would make a host that only ever sees other people's traffic
        report a healthy delivery path."""
        # `elsewhere` FIRST, against a record that has never seen a delivery. Run after a
        # successful one it would inherit the timestamp the record carries forward by
        # design, and the assertion would pass without testing anything.
        record = self._delivery(outcomes=[{"ref": "r", "outcome": "elsewhere"},
                                          {"ref": "r", "outcome": "elsewhere"}])
        self.assertIsNone(record["last_successful_delivery"],
                          "another host's mail is not evidence ours moved")
        self.assertEqual(record["elsewhere_count"], 2)
        self.assertEqual(record["attention"], [],
                         "and it is not something anyone needs to act on")
        for outcome in ("delivered", "already-delivered"):
            with self.subTest(outcome=outcome):
                previous = record["last_successful_delivery"]
                record = self._delivery(outcomes=[{"ref": "r", "outcome": outcome}])
                self.assertIsNotNone(record["last_successful_delivery"])
                self.assertNotEqual(record["last_successful_delivery"], previous,
                                    "each one advances the mark rather than inheriting it")

    def test_a_busy_delivery_and_a_failing_one_are_different_sentences(self):
        """Busy means another cycle holds the lock and this one should simply run again;
        errors mean something needs reading. Reported as one word they take the same
        remedy, and only one of them has one."""
        busy = self._delivery(busy=True)
        self.assertEqual(busy["failure"], "delivery: busy")
        failed = self._delivery(errors=[{"ref": "r", "error": "remote hung up"}])
        self.assertIn("remote hung up", failed["failure"])
        self.assertNotIn("busy", failed["failure"])

    def test_a_flood_of_delivery_errors_is_bounded_in_the_record(self):
        """The record is written to a state root and read by a startup line. An unbounded
        error list makes a bad cycle produce a health file nobody can read and a status
        line that fills the terminal — the failure mode turning a fault into an outage."""
        record = self._delivery(errors=[{"ref": "r", "error": "e%d" % n} for n in range(9)])
        self.assertIn("e4", record["failure"])
        self.assertNotIn("e5", record["failure"], "five errors is the stated bound")

    def test_the_publication_timestamp_is_the_publishers_own_not_our_clock(self):
        """The publisher confirms against the remote ref; the moment it names is when the
        mail was actually accepted. Stamping our own clock here would report a successful
        publication for a cycle in which the publisher had nothing to publish."""
        stamp = "2026-01-02T03:04:05+00:00"
        record = mailworker.run_cycle(
            self.roots, publish=lambda _roots: {"status": "ok", "last_publication": stamp},
            consume=lambda _roots: {"snapshots": [], "outcomes": [], "errors": []})
        self.assertEqual(record["last_successful_publication"], stamp)

    def test_a_failed_cycle_carries_the_previous_successes_forward(self):
        """The counterpart to `read_status`'s rule that old success is never current
        health: the timestamps themselves must SURVIVE a failure, or the record loses the
        only evidence of when the channel last worked — which is the first thing anyone
        diagnosing a stopped worker wants."""
        good = self._delivery(snapshots=[{"ref": "r"}],
                              outcomes=[{"ref": "r", "outcome": "delivered"}])
        bad = mailworker.run_cycle(
            self.roots, publish=lambda _roots: {"status": "failed", "failure": "no remote"},
            consume=lambda _roots: {"snapshots": [], "outcomes": [], "errors": []})
        self.assertEqual(bad["status"], "failed")
        self.assertEqual(bad["last_successful_delivery"], good["last_successful_delivery"])
        self.assertEqual(bad["last_successful_fetch"], good["last_successful_fetch"])


class StatusClockTests(WorkerFixture, unittest.TestCase):
    """An observation from the future is its own answer (WI-0364)."""

    def test_an_observation_ahead_of_the_clock_is_unknown_not_fresh(self):
        """Reported by no test. A record stamped ahead of this host's clock has a NEGATIVE
        age, which is younger than every staleness threshold — so a worker whose clock
        jumped forward, or a state root shared with a host whose clock did, reads as
        permanently, perfectly fresh. 'Unknown' is the honest answer and it was
        unreachable."""
        record = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        observed = mailworker._date(record["observed_at"])
        status = mailworker.read_status(self.roots, observed - timedelta(minutes=5))
        self.assertEqual(status["status"], "unknown")
        self.assertIn("in the future", status["failure"])

    def test_a_minute_of_skew_is_tolerated_and_the_boundary_is_the_minute(self):
        """The tolerance exists because two processes on one host do not share a clock to
        the microsecond. Sixty seconds exactly is INSIDE it — `age < -60`, not `<=` — and
        the endpoint is pinned here because a threshold whose edge nobody has stood on is
        a number, not a rule."""
        record = mailworker.run_cycle(self.roots, publish=self.publish, consume=self.consume)
        observed = mailworker._date(record["observed_at"])
        edge = mailworker.read_status(self.roots, observed - timedelta(seconds=60))
        self.assertNotEqual(edge["status"], "unknown",
                            "exactly one minute of skew is tolerated")
        beyond = mailworker.read_status(self.roots, observed - timedelta(seconds=61))
        self.assertEqual(beyond["status"], "unknown")


class CommandSurfaceTests(WorkerFixture, unittest.TestCase):
    """What the scheduler sees: the exit code, the quiet contract, and the environment
    the process is left in (WI-0364).

    launchd runs this every interval with `--quiet` and nothing reads its stdout unless
    it says something. Which invocations speak is therefore the whole operator-facing
    contract of an unattended worker, and three of its four branches were unasserted.
    """

    def _main(self, argv, status):
        out = io.StringIO()
        with mock.patch.object(mailworker, "read_status", return_value=status), \
                mock.patch.object(mailworker, "production") as production_module, \
                mock.patch("sys.stdout", out):
            production_module.CONFIG_ENV = "POGA_FEDERATION_CONFIG"
            production_module.resolve.return_value = self.roots
            code = mailworker.main(["--config", "/absolute/config.json", "--status", *argv])
        return code, out.getvalue()

    def test_quiet_says_nothing_about_an_ordinary_cycle(self):
        code, text = self._main(["--quiet"], {"status": "ok", "pending_count": 0})
        self.assertEqual((code, text), (0, ""))

    def test_quiet_still_speaks_when_the_cycle_failed(self):
        """The half that matters. A scheduled worker nobody is watching is silent by
        design; the failure is the one thing that must break that silence."""
        code, text = self._main(["--quiet"], {"status": "failed", "failure": "remote refused"})
        self.assertEqual(code, 1)
        self.assertIn("FAILED", text)
        self.assertIn("remote refused", text)

    def test_quiet_still_speaks_when_an_outcome_needs_a_person(self):
        """`ok` with an attention list is the case both other branches miss: the cycle
        worked, the exit code is 0, and something in it still needs reading."""
        code, text = self._main(["--quiet"], {
            "status": "ok", "pending_count": 0,
            "attention": [{"outcome": "unroutable", "path": "p", "detail": "no recipient"}]})
        self.assertEqual(code, 0, "an unroutable message is not a failed cycle")
        self.assertIn("unroutable", text)
        self.assertIn("no recipient", text)

    def test_the_human_line_is_one_line_and_says_all_four_things(self):
        """Pinned whole rather than by substring. This line IS the operator-facing
        contract of a service that otherwise speaks only to a log file: an assertion on
        'FAILED' alone leaves every label, separator and fallback in it free to drift,
        and a status line nobody can parse at a glance is one nobody reads twice."""
        _, text = self._main([], {"status": "aged", "pending_count": 3,
                                  "oldest_pending_age_seconds": 4200})
        self.assertEqual(
            text, "mail worker AGED: pending=3; oldest=4200s; attention=0; failure=none\n")

    def test_the_line_says_unknown_where_it_has_no_number_rather_than_zero(self):
        """A missing count and a count of zero are different facts, and the second is the
        one that means 'the queue is empty'. `pending_count` is None whenever the queue
        holds an unreadable entry, which is exactly when a zero would be a lie."""
        _, text = self._main([], {"status": "unknown", "failure": "queue unreadable"})
        self.assertIn("pending=unknown; oldest=unknown", text)
        self.assertIn("failure=queue unreadable", text)

    def test_json_is_printed_only_when_it_is_asked_for(self):
        _, text = self._main(["--json"], {"status": "ok", "pending_count": 0})
        self.assertEqual(json.loads(text)["status"], "ok")
        _, human = self._main([], {"status": "ok", "pending_count": 0})
        with self.assertRaises(ValueError,
                              msg="the default output is a line for a person, not JSON"):
            json.loads(human)
        self.assertIn("mail worker OK", human)

    def test_the_configuration_environment_is_restored_to_what_it_found(self):
        """`_configuration_environment` pops the variable on exit when it was unset and
        RESTORES it when it was set. Only the pop was covered, so a worker invoked from a
        process that had its own selection would have silently lost it — in-process, which
        is exactly where the session hook calls this from."""
        with mock.patch.dict(os.environ, {production.CONFIG_ENV: "/prior.json"}):
            with mailworker._configuration_environment("/mine.json"):
                self.assertEqual(os.environ[production.CONFIG_ENV], "/mine.json")
            self.assertEqual(os.environ[production.CONFIG_ENV], "/prior.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mailworker._configuration_environment("/mine.json"):
                pass
            self.assertNotIn(production.CONFIG_ENV, os.environ)

    def test_an_explicit_config_is_optional_when_the_environment_already_selects_one(self):
        """`--config` OVERRIDES the environment; it does not replace it as the only way
        in. The SessionEnd hook runs the poller, which delegates here with no `--config`
        at all, so a worker that required the flag would drain nothing on the host that
        has no launchd."""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {production.CONFIG_ENV: "/from-env.json"}), \
                mock.patch.object(mailworker.production, "resolve") as resolve, \
                mock.patch.object(mailworker, "read_status",
                                  return_value={"status": "ok", "pending_count": 0}), \
                mock.patch("sys.stdout", out):
            resolve.return_value = self.roots
            self.assertEqual(mailworker.main(["--status"]), 0)
            self.assertEqual(resolve.call_args[0][1][production.CONFIG_ENV],
                             "/from-env.json")


class RenderRefusalTests(InstallerTests):
    """`render()` builds the unit a scheduler will run unattended, and every one of its
    three refusals was unreachable from any test (WI-0364).

    The value is not hypothetical: a plist is written once and then run every ten minutes
    for months by something nobody is watching. A ProgramArguments naming an interpreter
    that is not there, a config path that resolves differently under launchd's working
    directory, or a release with no worker in it all produce the same symptom — a service
    that loads, exits non-zero forever, and delivers nothing.
    """

    def test_the_interpreter_must_be_an_absolute_executable_file(self):
        """Three separate conditions, fed one at a time. The non-executable case is the
        one that matters most: it fails ONLY the last of the three, so a rule that
        required all three to fail at once would still refuse the other two and read as
        working."""
        relative = Path(sys.executable).name
        missing = Path(self.tmp.name) / "no-such-python"
        not_executable = Path(self.tmp.name) / "python-not-executable"
        not_executable.write_text("#!/bin/sh\n")
        os.chmod(not_executable, 0o644)
        for candidate in (relative, missing, not_executable):
            with self.subTest(python=str(candidate)):
                with self.assertRaises(installer.InstallError) as caught:
                    installer.render(self.roots, self.cfgfile, candidate)
                self.assertIn("python must name an absolute executable",
                              str(caught.exception))

    def test_the_config_must_be_an_absolute_file(self):
        """A relative config is a live failure mode, not a hypothetical one: launchd sets
        the working directory from the plist, so a path that resolves at install time
        resolves somewhere else — or nowhere — at run time."""
        for candidate in (Path("production.json"), self.config / "absent.json"):
            with self.subTest(config=str(candidate)):
                with self.assertRaises(installer.InstallError) as caught:
                    installer.render(self.roots, candidate, sys.executable)
                self.assertIn("config must name an absolute external file",
                              str(caught.exception))

    def test_a_release_without_a_worker_in_it_is_refused(self):
        """The plist would name a script that is not there, and launchd would retry it on
        the interval forever with the failure only in a log nobody opens."""
        (self.code / "curate/mailworker.py").unlink()
        with self.assertRaises(installer.InstallError) as caught:
            installer.render(self.roots, self.cfgfile, sys.executable)
        self.assertIn("does not contain mailworker.py", str(caught.exception))

    def test_the_rendered_unit_carries_the_configuration_it_was_built_for(self):
        """The positive control for the three refusals above: without it they pass for
        free on an implementation that refuses everything."""
        value = plistlib.loads(installer.render(self.roots, self.cfgfile, sys.executable))
        self.assertEqual(value["EnvironmentVariables"][production.CONFIG_ENV],
                         str(self.cfgfile))
        self.assertEqual(value["WorkingDirectory"], str(self.code))


class ChangeRefusalTests(InstallerTests):
    """The refusals that run BEFORE the scheduler is touched, fed the inputs they exist
    to refuse (WI-0364)."""

    def test_the_destination_must_carry_the_label_it_will_be_loaded_under(self):
        """launchd matches the label inside the file, not the filename — so a mismatch
        installs a unit that loads under a name `cutover_state` then reads as absent."""
        with self.assertRaises(installer.InstallError) as caught:
            installer.change(self.destination.with_name("other.plist"), self.data, 1000,
                             approved_roots=[self.code], ctl=self.ctl)
        self.assertIn("must use the existing mail-poller label filename",
                      str(caught.exception))
        self.assertEqual(self.ctl.calls, [], "refused before any scheduler read")

    def test_the_uid_must_be_a_real_nonnegative_user(self):
        """Two conditions fed separately, plus the endpoint. `uid` is interpolated
        straight into the `user/<uid>` domain, so a wrong type produces a domain string
        launchctl answers about some other session."""
        for value in ("1000", -1, None):
            with self.subTest(uid=value):
                with self.assertRaises(installer.InstallError) as caught:
                    installer.change(self.destination, self.data, value,
                                     approved_roots=[self.code], ctl=self.ctl)
                self.assertIn("uid must be a nonnegative integer", str(caught.exception))
        self.assertEqual(
            installer.change(self.destination, self.data, 0, approved_roots=[self.code],
                             ctl=FakeLaunchd("user/0"))["domain"], "user/0",
            "zero is a real uid; the boundary is nonnegative, not positive")

    def test_a_symlinked_service_plist_is_refused_rather_than_followed(self):
        """Following it writes the backup, and later the unit, wherever the link points —
        which is why the rule is separate from the readability check below it."""
        target = Path(self.tmp.name) / "elsewhere.plist"
        target.write_bytes(self.data)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.symlink_to(target)
        with self.assertRaises(installer.InstallError) as caught:
            self.apply()
        self.assertIn("refusing symlinked service plist", str(caught.exception))

    def test_an_existing_plist_is_validated_on_each_of_its_three_rules(self):
        """Label, argument count, and approved path. Each is fed on its own because the
        three share one refusal sentence: a rule that required all three to fail would
        still refuse the obvious cases and read as working."""
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        good = plistlib.loads(self.data)
        cases = {
            "wrong label": {**good, "Label": "com.example.other"},
            "too few arguments": {**good, "ProgramArguments": [sys.executable]},
            "unapproved path": {**good, "ProgramArguments": [
                sys.executable, "/somewhere/else/curate/mailworker.py"]},
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                self.destination.write_bytes(plistlib.dumps(value))
                with self.assertRaises(installer.InstallError) as caught:
                    self.apply()
                self.assertIn("refusing service outside approved code roots",
                              str(caught.exception))
        self.destination.write_bytes(plistlib.dumps({
            **good, "ProgramArguments": [sys.executable,
                                         str(self.code / "curate/mailworker.py")]}))
        self.assertEqual(self.apply()["status"], "registered",
                         "exactly two arguments is the minimum, not one short of it")

    def test_a_surviving_gui_service_after_installation_is_a_refusal(self):
        """Two publishers under one label is the failure the worker exists to prevent,
        and the check for it could never fire: nothing left a GUI service standing after
        a bootstrap."""
        class Resurrecting(FakeLaunchd):
            def __call__(self, args):
                code, out = super().__call__(args)
                if args[0] == "bootstrap":
                    self.services["gui/1000/" + installer.LABEL] = args[2]
                return code, out

        self.ctl = Resurrecting()
        with self.assertRaises(installer.InstallError) as caught:
            self.apply()
        self.assertIn("unexpected duplicate GUI service", str(caught.exception))

    def test_the_displaced_definition_is_kept_and_not_refreshed_by_the_next_upgrade(self):
        """The rollback copy is of what was there BEFORE this tool touched the host. An
        upgrade that refreshes it replaces the operator's own plist with our previous
        one, which is the thing they would be rolling back to escape."""
        self.apply()
        first = self.destination.read_bytes()
        self.roots.transport["poll_interval_seconds"] = 900
        self.data = installer.render(self.roots, self.cfgfile, sys.executable)
        self.apply()
        backup = self.destination.with_suffix(".plist.pre-mail-worker")
        self.assertEqual(backup.read_bytes(), first,
                         "the backup is the original, not the most recent")


class InstallerCommandTests(InstallerTests):
    """`main()`'s argument rules — the surface an operator types, and the one place a
    mistake reaches a live scheduler (WI-0364)."""

    def _main(self, argv):
        err = io.StringIO()
        with mock.patch.object(installer.production, "resolve", return_value=self.roots), \
                mock.patch("sys.stderr", err), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = installer.main(["--config", str(self.cfgfile), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_relative_destination_is_refused(self):
        code, _, err = self._main(["--destination", "relative.plist"])
        self.assertEqual(code, 1)
        self.assertIn("destination must be absolute", err)

    def test_rendering_refuses_to_write_into_a_live_scheduler_directory(self):
        """The guard that keeps a dry run from becoming an installation. Without `--apply`
        nothing is bootstrapped, so a file dropped into LaunchAgents is an installation
        that happens at the next login instead of now — the least visible kind."""
        code, _, err = self._main(
            ["--destination", str(Path.home() / "Library/LaunchAgents"
                                  / (installer.LABEL + ".plist"))])
        self.assertEqual(code, 1)
        self.assertIn("outside live scheduler directories", err)

    def test_uninstall_is_not_something_a_dry_run_can_do(self):
        code, _, err = self._main(["--destination", str(self.destination), "--uninstall"])
        self.assertEqual(code, 1)
        self.assertIn("uninstall requires explicit --apply", err)

    def test_applying_without_a_named_user_domain_is_refused(self):
        """`--uid` has no default on purpose: the Background domain belongs to a specific
        user, and inferring it from the invoking process is how a worker gets installed
        for whoever happened to run the installer."""
        code, _, err = self._main(["--destination", str(self.destination), "--apply"])
        self.assertEqual(code, 1)
        self.assertIn("apply requires an explicit --uid", err)

    def test_a_dry_run_writes_the_unit_and_says_it_changed_nothing(self):
        """The positive control for the four refusals above, and the contract a rehearsal
        depends on: a render is a file and nothing else."""
        target = Path(self.tmp.name) / "rendered" / (installer.LABEL + ".plist")
        code, out, _ = self._main(["--destination", str(target)])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertFalse(report["scheduler_changed"])
        self.assertEqual(plistlib.loads(target.read_bytes())["Label"], installer.LABEL)

    def test_an_apply_prepares_the_state_root_and_passes_the_roots_it_approved(self):
        """The successful `--apply` path was reached by nothing: every existing test drove
        `change()` directly, so main's own work — creating the private runtime directory
        the worker will write its health file into, and assembling `approved_roots` from
        the configured release plus any explicitly replaced ones — had no test at all. The
        runtime directory matters here rather than in the worker: launchd's first run
        happens with no session to create it, and `StandardOutPath` is opened by launchd
        BEFORE the worker starts, so a missing directory is a service that cannot log why
        it failed."""
        recorded = {}

        def change(destination, data, uid, **kwargs):
            recorded.update(destination=destination, uid=uid, **kwargs)
            return {"status": "registered"}

        shutil.rmtree(self.state / "runtime", ignore_errors=True)
        with mock.patch.object(installer.production, "resolve", return_value=self.roots), \
                mock.patch.object(installer, "change", side_effect=change), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            code = installer.main(["--config", str(self.cfgfile), "--destination",
                                   str(self.destination), "--apply", "--uid", "1000",
                                   "--replace-code-root", "/an/older/release"])
        self.assertEqual(code, 0)
        self.assertEqual(recorded["uid"], 1000)
        self.assertEqual([str(p) for p in recorded["approved_roots"]],
                         [str(self.code), "/an/older/release"],
                         "the configured release first, then what was explicitly replaced")
        self.assertEqual(oct(os.stat(self.state / "runtime").st_mode)[-3:], "700")


if __name__ == "__main__":
    unittest.main()
