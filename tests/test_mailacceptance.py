"""WI-0366 evidence checks: fixtures exercise rules but never certify running hosts."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
import mailacceptance as acceptance
from production import Roots


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
        self.manifest = {"schema_version": 1,
                         "hosts": {}, "releases": [], "probes": []}
        for name in ("development", "production"):
            root = "/fixture/" + name
            labels = list(acceptance.LABELS) if name == "production" else ["com.federation.mail-poller"]
            self.manifest["hosts"][name] = {"hostname": name, "code_root": root + "/release",
                "old_code_root": root + "/old", "poga_path": root + "/bin/poga",
                "config_path": root + "/state/config/production.json",
                "owned_ref": "refs/heads/" + name + "/mail",
                "services": ["user/1000/" + label for label in labels]}
        for index in (1, 2):
            commit = str(index) * 40
            self.manifest["releases"].append({"commit": commit, "tag": "release-" + str(index),
                "approved_at": (self.now - timedelta(hours=3 - index)).isoformat(),
                "approval_evidence": "fixture approval reference " + str(index)})
            for sender in self.manifest["hosts"]:
                recipient = "production" if sender == "development" else "development"
                self.manifest["probes"].append({"message_id": sender + "-" + str(index),
                    "sender": sender, "recipient": recipient, "destination": "fixture-recipient",
                    "release": commit})
        self.rehearsal = {"checks": {key: "passed" for key in acceptance.REHEARSAL_CHECKS},
                          "command": "fixture-test-command", "artifact_sha256": "a" * 64}
        values = {"two_unattended_releases": [item["commit"] for item in self.manifest["releases"]],
                  "no_runner_hand_operation": 0, "no_production_main_writes": 0,
                  "old_checkout_preserved": True, "no_pending_work_lost": True,
                  "rollback_verified": True, "legacy_schedules_retired": True,
                  "legacy_dependencies_inspected": []}
        self.operational = {key: {"kind": "live-operations", "status": "observed", "value": value,
                                 "evidence": "synthetic test input only", "artifact_sha256": "b" * 64}
                            for key, value in values.items()}
        self.observations = [self.observation(host, release, index)
                             for index, release in enumerate(self.manifest["releases"])
                             for host in self.manifest["hosts"]]

    def provenance(self, observation):
        observation.pop("return_channel", None)
        observation["return_channel"] = {"source_ref": observation["owned_ref"],
            "source_commit": "a" * 40, "payload_sha256": acceptance._hash(acceptance.serialized(observation)),
            "receipt_evidence": "synthetic receipt for rule test only"}

    def observation(self, host, release, index):
        spec = self.manifest["hosts"][host]
        stamp = (self.now - timedelta(minutes=60 if index == 0 else 1)).isoformat()
        result = {"kind": "live-local", "host_id": host, "hostname": spec["hostname"],
            "observed_at": stamp, "code_root": spec["code_root"], "owned_ref": spec["owned_ref"],
            "config_path": spec["config_path"], "state_root": "/fixture/" + host + "/state",
            "transport_root": "/fixture/" + host + "/channel", "jobs": {}, "probe_messages": [],
            "code": {"head": release["commit"], "tags": [release["tag"]],
                     "detached": True, "tracked_clean": True},
            "poga": {"is_symlink": True, "target_exists": True, "target": spec["code_root"] + "/poga"},
            "worker": {"status": "ok", "release": release["commit"], "observed_at": stamp,
                       "stale_after_seconds": 1800},
            "member_lookup": {"status": "observed", "members": ["fixture-member"]}}
        for target in spec["services"]:
            label = target.rsplit("/", 1)[1]
            result["jobs"][target] = {"probe": "launchctl print", "returncode": 0,
                "raw_output_sha256": "b" * 64, "working_directory": spec["code_root"],
                "arguments": ["/usr/bin/python3", spec["code_root"] + "/" + acceptance.LABELS[label]],
                "config_environment": spec["config_path"]}
        for probe in self.manifest["probes"]:
            if probe["sender"] != host or probe["release"] != release["commit"]:
                continue
            result["probe_messages"].append({"message_id": probe["message_id"],
                "destination": probe["destination"], "sha256": "c" * 64,
                "lifecycle": {"state": "delivered", "acknowledgment_ref": self.manifest["hosts"][probe["recipient"]]["owned_ref"],
                    "acknowledgment": {"message_id": probe["message_id"], "sha256": "c" * 64,
                        "destination": probe["destination"], "source_ref": spec["owned_ref"], "outcome": "delivered"}}})
        self.provenance(result)
        return result

    def check(self, **kwargs):
        return acceptance.check(self.manifest, self.observations, rehearsal=self.rehearsal,
                                operational=self.operational, now=self.now, **kwargs)

    def test_complete_inputs_only_request_review_never_acceptance(self):
        result = self.check()
        self.assertEqual(result["status"], "ready-for-review")
        self.assertFalse(result["accepted"])
        self.assertIsNone(result["ops_observation_started_at"])
        self.assertTrue(result["review_required"])

    def test_local_rehearsal_cannot_substitute_for_any_live_observation(self):
        result = acceptance.check(self.manifest, [], rehearsal=self.rehearsal, now=self.now)
        self.assertEqual(result["local_rehearsal"], "recorded")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("host observation" in gap for gap in result["live_gaps"]))
        for observation in self.observations:
            observation["kind"] = "fixture"
            self.provenance(observation)
        self.assertTrue(any("fixture" in gap for gap in self.check()["live_gaps"]))

    def test_installer_log_and_wrong_loaded_program_do_not_prove_release(self):
        observed = self.observations[-1]
        target = next(iter(observed["jobs"]))
        observed["jobs"][target] = {"probe": "installer log", "returncode": 0}
        self.provenance(observed)
        self.assertTrue(any("loaded-unit probe" in gap for gap in self.check()["live_gaps"]))
        observed["jobs"][target] = {"probe": "launchctl print", "returncode": 0,
            "raw_output_sha256": "d" * 64, "working_directory": observed["code_root"],
            "arguments": ["/usr/bin/python3", "/fixture/production/old/deploy/runner.py"],
            "config_environment": observed["config_path"]}
        self.provenance(observed)
        gaps = self.check()["live_gaps"]
        self.assertTrue(any("old checkout" in gap for gap in gaps))
        self.assertTrue(any("loaded code paths" in gap for gap in gaps))

    def test_missing_or_wrong_ack_remains_a_directional_delivery_gap(self):
        self.observations[-1]["probe_messages"][0]["lifecycle"]["acknowledgment"]["sha256"] = "e" * 64
        self.provenance(self.observations[-1])
        self.assertTrue(any("matching delivery acknowledgment" in gap for gap in self.check()["live_gaps"]))

    def test_stale_second_snapshot_and_missing_operational_coverage_are_unknown(self):
        self.now += timedelta(hours=1)
        self.operational.pop("no_runner_hand_operation")
        result = self.check()
        self.assertTrue(any("second release observation is stale" in gap for gap in result["live_gaps"]))
        self.assertTrue(any("no_runner_hand_operation" in gap for gap in result["live_gaps"]))

    def test_changed_snapshot_cannot_keep_its_previous_return_digest(self):
        self.observations[-1]["poga"]["target"] = "/wrong/poga"
        self.assertTrue(any("return-channel receipt" in gap for gap in self.check()["live_gaps"]))

    def test_nonzero_interventions_and_remaining_dependencies_do_not_pass(self):
        self.operational["no_runner_hand_operation"]["value"] = 1
        self.operational["legacy_dependencies_inspected"]["value"] = ["old refresh job"]
        result = self.check()
        self.assertTrue(any("no_runner_hand_operation" in gap for gap in result["live_gaps"]))
        self.assertTrue(any("legacy_dependencies_inspected" in gap for gap in result["live_gaps"]))

    def test_manifest_requires_distinct_approved_releases(self):
        self.manifest["releases"][1]["commit"] = self.manifest["releases"][0]["commit"]
        with self.assertRaisesRegex(ValueError, "different commit"):
            self.check()

    def test_invalid_freshness_limit_cannot_disable_stale_detection(self):
        self.manifest["max_observation_age_seconds"] = "never"
        with self.assertRaisesRegex(ValueError, "max_observation_age_seconds"):
            self.check()

    def test_unknown_launchctl_format_fails_closed(self):
        with self.assertRaises(ValueError):
            acceptance.parse_loaded_job("Installed com.federation.mail-poller successfully")

    def test_snapshot_collects_its_host_evidence_through_the_shared_seam(self):
        """One collector, two callers (WI-0396).

        `deploy/diagnose.py` emits these three probes on the host's own schedule and has no
        manifest to pass, so they were LIFTED out of `snapshot` into `observe_host` rather
        than copied. This is what holds that: if `snapshot` grew a private copy the two
        would answer differently the first time either was fixed, and the acceptance would
        rest on whichever one nobody had looked at.
        """
        spec = self.manifest["hosts"]["development"]
        roots = Roots(Path(spec["code_root"]), Path("/fixture/development/state"),
                      Path("/fixture/development/channel"), Path("/fixture/development/config"),
                      None, {"owned_ref": spec["owned_ref"]})
        seam = {"poga": {"target": "sentinel"}, "worker": {"status": "sentinel"},
                "member_lookup": {"status": "sentinel"}, "gaps": ["sentinel gap"]}
        with mock.patch.object(acceptance, "observe_host", return_value=seam) as seen:
            result = acceptance.snapshot(roots, spec["config_path"], self.manifest,
                                         "development",
                                         run=lambda _argv: {"returncode": 1, "stdout": ""})
        seen.assert_called_once()
        self.assertEqual(seen.call_args.args[1], spec["poga_path"],
                         "snapshot passed the seam something other than the declared poga path")
        self.assertEqual(result["poga"], {"target": "sentinel"})
        self.assertEqual(result["worker"], {"status": "sentinel"})
        self.assertIn("sentinel gap", result["gaps"],
                      "a probe the seam could not answer did not reach the snapshot's gaps")


    def test_collector_reads_real_git_and_preserves_all_fixture_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            code = base / "release"
            state = base / "state"
            config = base / "config"
            code.mkdir()
            state.mkdir()
            config.mkdir()
            (code / "poga").write_text("fixture only\n")
            (base / "poga").symlink_to(code / "poga")
            def git(*args):
                return subprocess.run(["git", "-C", str(code), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@localhost", "-c", "commit.gpgsign=false", *args],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "--quiet", "-b", "main")
            git("add", "poga")
            git("commit", "--quiet", "-m", "fixture")
            git("tag", "release-1")
            git("checkout", "--quiet", "--detach")
            commit = git("rev-parse", "HEAD").stdout.decode().strip()
            manifest = copy.deepcopy(self.manifest)
            spec = manifest["hosts"]["development"]
            spec.update(code_root=str(code), old_code_root=str(base / "old"), poga_path=str(base / "poga"),
                        config_path=str(config / "production.json"), hostname=socket.gethostname())
            roots = Roots(code, state, base / "transport", config, None, {"owned_ref": spec["owned_ref"]})
            # This fixture only collects identity/health; it has no live send side effects.
            manifest["releases"][0]["commit"] = commit
            manifest["probes"] = []
            def fixture_probe(argv):
                if argv[0] == "git":
                    return acceptance._run(argv)
                text = ("working directory = " + str(code) + "\narguments = {\n/usr/bin/python3\n" +
                        str(code / "curate/mailworker.py") + "\n}\n" +
                        "POGA_FEDERATION_CONFIG => " + spec["config_path"] + "\n" +
                        "UNRELATED_SECRET => must-not-appear-in-artifact\n")
                return {"returncode": 0, "stdout": text}
            def hashes():
                return {str(path.relative_to(base)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in base.rglob("*") if path.is_file()}
            before = hashes()
            result = acceptance.snapshot(roots, spec["config_path"], manifest, "development",
                                         run=fixture_probe, lookup=lambda _: {"status": "observed", "members": ["fixture"]})
            self.assertEqual(result["kind"], "fixture")
            self.assertEqual(result["code"]["head"], commit)
            self.assertTrue(result["code"]["detached"])
            self.assertEqual(result["worker"]["status"], "unknown")
            self.assertNotIn("must-not-appear-in-artifact", json.dumps(result))
            self.assertEqual(before, hashes())
            self.assertFalse((state / "runtime").exists())
            self.assertFalse(roots.transport_root.exists())


class HostEvidenceIsCollectedWithoutAManifest(unittest.TestCase):
    """`observe_host` — the manifest-free seam WI-0366's evidence is emitted through.

    A machine reporting what it can see about itself every ten minutes holds no acceptance
    manifest: that document names two hosts, two approved releases and the directional mail
    probes between them, none of which a host needs in order to say where its own `poga`
    points. So the three probes that need no manifest answer without one.

    THE PAIR BELOW IS THE WHOLE POINT. An emitter that cannot report a gap is a rubber
    stamp, and a `gaps` list that can never be empty is the same failure wearing the
    opposite sign — it would report a healthy host as broken forever and nobody would read
    it twice. Both directions are held here.

    `deliver.locate_repos` is INJECTED, never called: it walks the operator's real member
    map, and a probe that left the fixture would be reading the machine running the tests.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.code = self.base / "release"
        self.state = self.base / "state"
        self.code.mkdir()
        (self.state / "runtime").mkdir(parents=True)
        (self.state / "config").mkdir(parents=True)
        (self.code / "poga").write_text("fixture only\n")
        self.poga = self.base / "poga"
        self.poga.symlink_to(self.code / "poga")
        self.roots = Roots(self.code, self.state, self.base / "transport",
                           self.state / "config", None,
                           {"owned_ref": "refs/heads/fixture/mail"})

    def _observe(self, worker, **kwargs):
        kwargs.setdefault("lookup", lambda _roots: {"status": "observed",
                                                    "members": ["fixture-member"], "count": 1})
        with mock.patch("mailworker.read_status", return_value=worker):
            return acceptance.observe_host(self.roots, self.poga, **kwargs)

    def test_a_resolved_host_reports_no_gaps_at_all(self):
        """The control. Without it a `gaps` list that always had something in it would pass
        every negative below and never once be evidence of anything."""
        result = self._observe({"status": "ok", "release": "a" * 40})
        self.assertEqual(result["gaps"], [])
        self.assertTrue(result["poga"]["is_symlink"])
        self.assertTrue(result["poga"]["target_exists"])
        self.assertEqual(result["poga"]["target"], str(self.code / "poga"))
        self.assertEqual(result["member_lookup"]["members"], ["fixture-member"])

    def test_every_probe_that_could_not_answer_is_named_and_none_reads_as_healthy(self):
        self.poga.unlink()
        self.poga.write_text("a real file where the link should be\n")

        def explode(_roots):
            raise RuntimeError("no member map on this host")

        result = self._observe({"status": "unknown",
                                "failure": "no readable local worker observation"},
                               lookup=explode)
        self.assertEqual(len(result["gaps"]), 3, result["gaps"])
        self.assertTrue(any("poga" in gap for gap in result["gaps"]))
        self.assertTrue(any("worker liveness" in gap for gap in result["gaps"]))
        self.assertTrue(any("member lookup" in gap for gap in result["gaps"]))
        self.assertFalse(result["poga"]["is_symlink"])
        self.assertEqual(result["member_lookup"]["failure"], "RuntimeError")
        self.assertEqual(result["member_lookup"]["status"], "unknown")

    def test_a_dangling_link_is_its_own_gap_and_not_the_missing_link_one(self):
        """They are different findings. A `poga` that is not a link was never installed; a
        link whose target is gone was installed and the release it points into is not there
        any more, which is what a half-finished cutover looks like."""
        (self.code / "poga").unlink()
        result = self._observe({"status": "ok", "release": "a" * 40})
        self.assertEqual(len(result["gaps"]), 1, result["gaps"])
        self.assertIn("which is not a file", result["gaps"][0])
        self.assertTrue(result["poga"]["is_symlink"])
        self.assertFalse(result["poga"]["target_exists"])

    def test_a_lookup_that_finds_nobody_is_a_failure_to_look(self):
        """Zero members is never a fleet with no members: after cutover the member map is
        how anything is located at all, so an empty answer means the map is missing."""
        result = self._observe({"status": "ok", "release": "a" * 40},
                               lookup=lambda _roots: {"status": "observed", "members": [],
                                                      "count": 0})
        self.assertEqual(len(result["gaps"]), 1, result["gaps"])
        self.assertIn("failure to look", result["gaps"][0])

    def test_a_stale_worker_is_not_a_healthy_one(self):
        result = self._observe({"status": "stale", "release": "a" * 40})
        self.assertEqual(len(result["gaps"]), 1, result["gaps"])
        self.assertIn("'stale'", result["gaps"][0])
        self.assertIn("never an inference", result["gaps"][0])

if __name__ == "__main__":
    unittest.main()
