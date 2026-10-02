"""WI-0316: the rollback drill, and the unattended trigger that gates itself on it.

ADR-0103 D9 says the scheduled sweep may not be installed until a rollback has been
drilled. Until now the only way to produce that evidence was for a person to break a smoke
check by hand on the deploy host — a machine no federation session runs on — so the gate
stayed shut and the sweep was never installed. `--drill` is that act written as code, and
these tests are about whether it is the real failure path or a re-enactment of it.

WHAT IS REAL HERE. Real git repos, real tags, a real checkout, the smoke gate really run,
the ledger really written. Exactly ONE thing is injected — the verify gate's verdict — and
the tests below pin where that injection is allowed to reach: everything downstream of it
is production code, and everything upstream of it ran for real.

`launchctl` is never exercised, for the reason `test_deploy_runner.py` gives: every
contract here declares no units or `restart: none`, so the suite cannot restart anything on
the machine that runs it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "curate"))

import channel  # noqa: E402
import common  # noqa: E402
import runner  # noqa: E402
from test_deploy_runner import CONTRACT, DeployRunnerCase, _git  # noqa: E402


class DrillCase(DeployRunnerCase):
    """A widget already deployed and healthy — the only state a drill is for."""

    host = "Fixture"

    def setUp(self):
        super().setUp()
        # The resident machine is declared through the seam `curate/channel.py` ALREADY
        # carries — `POGA_CHANNEL_WRITER` — because the deploy gate reads that same
        # declaration rather than a second one of its own. The machine is faked to match,
        # so the suite never depends on which box it runs on.
        self._set_host(self.host)
        self._machine = patch.object(common, "this_machine", lambda: "Fixture")
        self._machine.start()
        self.addCleanup(self._machine.stop)

    def _set_host(self, host):
        saved = os.environ.get("POGA_CHANNEL_WRITER")
        if host is None:
            os.environ.pop("POGA_CHANNEL_WRITER", None)
        else:
            os.environ["POGA_CHANNEL_WRITER"] = host
        self.addCleanup(self._restore_host, saved)

    @staticmethod
    def _restore_host(saved):
        if saved is None:
            os.environ.pop("POGA_CHANNEL_WRITER", None)
        else:
            os.environ["POGA_CHANNEL_WRITER"] = saved

    def _deployed(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger().get("status"), "deployed")
        return rc

    def _drill(self, system=None, **kw):
        # BOTH STREAMS. Every refusal and every inconclusive verdict in this feature is
        # written to stderr, so a helper that captured only stdout would assert against a
        # blank string and pass for the wrong reason — which is how the first cut of these
        # tests read three real messages as absent.
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = runner.drill(system or self.system, **kw)
        return rc, buf.getvalue()


class TestTheDrillRunsTheRealFailurePath(DrillCase):

    def test_a_drill_leaves_production_on_the_tag_it_started_on(self):
        """The property that makes a drill safe to run unattended on a live box.

        Rolling back to the ledger's `previous` would be the more faithful rehearsal, and
        it was rejected precisely here: it parks production on an old release until a
        restore step succeeds, and that restore is a second thing that can fail with nobody
        watching. Same-tag cannot end anywhere but where it began."""
        self._deployed()
        rc, out = self._drill()
        self.assertEqual(rc, 0, out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0",
                         "a drill must not move the version production is running")
        self.assertTrue((self._tree() / "marker.txt").exists())

    def test_the_forced_failure_actually_reaches_the_rollback(self):
        """A drill that reported success without the rollback running would be the exact
        unproven claim D9 exists to retire, so the evidence is read out of the run."""
        self._deployed()
        rc, out = self._drill()
        self.assertEqual(rc, 0, out)
        self.assertIn("verify: FORCED TO FAIL", out)
        self.assertIn("verify FAILED after restart — rolling back", out)
        self.assertIn("verify(rollback)", out,
                      "the restored version must be re-verified, not assumed")
        self.assertIn("DRILL PASSED", out)

    def test_the_evidence_key_is_the_one_the_d9_gate_actually_greps_for(self):
        """`install-deploy-sweep.sh` tests the ledger TEXT for the literal string
        "rollback_verify". A drill that recorded its evidence under any other name would
        run, report success, and leave the gate it was written to open still shut — which
        is what the brief's own recipe would have done by injecting at the smoke gate,
        since the smoke path returns at `smoke-failed` and never writes that key."""
        self._deployed()
        self._drill()
        text = runner.ledger_path(self.system).read_text()
        self.assertIn('"rollback_verify"', text)
        drill = self._ledger()["drill"]
        self.assertIs(drill["ok"], True)
        self.assertIs(drill["rollback_verify"]["ok"], True)
        self.assertEqual(drill["tag"], "v1.0.0")

    def test_the_ledger_is_restored_not_merged_over(self):
        """The drill drives the record through `rolled-back` on the way. A merge-style
        restore cannot REMOVE the `failed_at` and `attempted` keys that transit leaves
        behind, so the ledger would describe a withdrawn release on a system that is
        running fine — and `--status`, the dashboard and the next reader would all repeat
        it."""
        self._deployed()
        before = self._ledger()
        self._drill()
        after = self._ledger()
        self.assertEqual(after.get("status"), "deployed")
        self.assertEqual(after.get("current"), "v1.0.0")
        self.assertNotIn("failed_at", after)
        self.assertNotIn("attempted", after)
        self.assertEqual({k: v for k, v in after.items() if k != "drill"}, before,
                         "a drill must leave the record exactly as it found it, plus "
                         "its own evidence")

    def test_a_drill_does_not_tell_the_member_its_release_failed(self):
        """THE FALSE-ALARM GUARD. Every other outcome of `deploy()` posts a brief to the
        system's Architect (WI-0230). A drill ends in `rolled-back`, so the unexempted rule
        would queue, commit and push a brief telling the member's Architect that its release
        failed verification and was withdrawn — a sentence that is false, durable, and read
        by someone who cannot see the flag that caused it."""
        self._deployed()
        posted_before = self._queued()
        self.assertTrue(posted_before, "the ordinary deploy should have posted a receipt")
        self._drill()
        self.assertEqual(self._queued(), posted_before,
                         "a drill must post no deploy result to the member's Architect")

    def test_the_escalation_says_it_is_a_rehearsal(self):
        """The rollback path writes a comms note, and the drill must not suppress it — the
        escalation is part of what is being proven. It must be impossible to read as an
        outage, because the person who finds it later cannot see this flag either."""
        self._deployed()
        self._drill()
        notes = sorted((self.root / "comms").glob("*.md"))
        self.assertTrue(notes, "the rollback path must still write its comms note")
        body = notes[-1].read_text()
        self.assertIn("DRILL", body)
        self.assertIn("rehearsal, not an outage", body)

    def test_the_smoke_gate_is_really_run_and_is_not_the_injection_point(self):
        """The injection is ONE verdict at ONE gate. If the smoke gate were faked too, the
        drill would stop proving that the tree it rolled back from was ever viable."""
        self._write_contract(CONTRACT, smoke={"cmd": ["/bin/sh", "-c",
                                                      "echo SMOKE-REALLY-RAN"]})
        _git(["add", "-A"], self.upstream)
        _git(["commit", "-m", "smoke marker"], self.upstream)
        _git(["tag", "v1.1.0"], self.upstream)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        rc, out = self._drill()
        self.assertEqual(rc, 0, out)
        self.assertIn("SMOKE-REALLY-RAN", out)


class TestTheDrillRefusesBeforeItDisturbsAnything(DrillCase):

    def test_a_system_that_has_never_deployed_is_refused(self):
        with self.assertRaises(runner.DeployError) as e:
            self._drill()
        self.assertIn("no tag checked out", str(e.exception))

    def test_a_system_that_is_not_healthy_is_refused(self):
        """A drill disturbs a live system twice. It is for proving the failure path on a
        healthy one, never for poking one that is already in a state someone needs to
        look at."""
        self._deployed()
        # `_decided` rather than a bare `status=`: since WI-0412 a status must name the tag
        # it was decided for, and `write_ledger` refuses one that does not.
        runner.write_ledger(self.system, **runner._decided("awaiting-cutover", "v1.0.0"))
        with self.assertRaises(runner.DeployError) as e:
            self._drill()
        self.assertIn("awaiting-cutover", str(e.exception))

    def test_a_contract_with_no_verify_gate_is_refused_before_being_touched(self):
        """There is no gate to force, so the failure would be invented — and the rollback
        would then be accepted with nothing confirming the restored version is healthy,
        which is the same class of claim as an unverified backup. Refused up front, so a
        live system is not disturbed twice to produce evidence that could not be evidence."""
        contract = {k: v for k, v in CONTRACT.items() if k != "verify"}
        self._write_contract(contract)
        _git(["add", "-A"], self.upstream)
        _git(["commit", "-m", "no verify"], self.upstream)
        _git(["tag", "v1.1.0"], self.upstream)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        before = self._ledger()
        with self.assertRaises(runner.DeployError) as e:
            self._drill()
        self.assertIn("no verify gate", str(e.exception))
        self.assertEqual(self._ledger(), before, "a refusal must touch nothing")

    def test_the_self_row_is_refused(self):
        """Its units are the runner's own, and a drill restarts them twice mid-run — the
        hazard `refuse_self_restart` exists to refuse, arriving through a different verb."""
        self._deployed()
        reg = json.loads(self.registry.read_text())
        reg["systems"][self.system]["self"] = True
        self.registry.write_text(json.dumps(reg))
        with self.assertRaises(runner.DeployError) as e:
            self._drill()
        self.assertIn("self", str(e.exception))

    def test_a_smoke_failure_during_a_drill_is_inconclusive_and_stops_the_sweep(self):
        """The drill forces the VERIFY gate. If the SMOKE gate fails first, the run never
        reaches the failure path the drill exists to exercise — it returns at
        `smoke-failed`, having proven nothing about the rollback.

        Reporting that as a passed drill would be the worst outcome this feature can
        produce: it would open the D9 gate on evidence that was never gathered. It reports
        INCONCLUSIVE, leaves the genuine `smoke-failed` record standing rather than
        restoring over it, and `unattended` then refuses to sweep."""
        self._deployed()
        real = runner.run_gate

        def _gate(system, entry, spec, label, log, dry_run):
            if label == "smoke":
                return {"declared": True, "ok": False, "exit": 1, "detail": "boom"}
            return real(system, entry, spec, label, log, dry_run)

        with patch.object(runner, "run_gate", side_effect=_gate):
            rc, out = self._drill()
        self.assertEqual(rc, 1)
        self.assertIn("DRILL INCONCLUSIVE", out)
        self.assertEqual(self._ledger().get("status"), "smoke-failed",
                         "a real smoke failure is a finding and must stay on the record")
        self.assertNotIn("drill", self._ledger(),
                         "a run that never reached the rollback is not drill evidence")
        self.assertEqual(runner.drill_evidence(), [])

    def test_an_unconfirmed_rollback_is_not_recorded_as_evidence(self):
        """The rollback ran, but nothing verified what it restored — so it is not D9
        evidence and must not be filed as any.

        Reached by making the ROLLBACK's verify gate report undeclared, which the up-front
        guard cannot cover: that guard asks about the contract being deployed, this asks
        about the contract being rolled back TO. They are the same file only because this
        drill rolls back to the same tag, and that is a property of today's drill rather
        than of the check."""
        self._deployed()
        real = runner.run_gate

        def _gate(system, entry, spec, label, log, dry_run):
            if label == "verify(rollback)":
                return {"declared": False}
            return real(system, entry, spec, label, log, dry_run)

        before = self._ledger()
        with patch.object(runner, "run_gate", side_effect=_gate):
            rc, out = self._drill()
        self.assertEqual(rc, 1)
        self.assertIn("DRILL INCONCLUSIVE", out)
        self.assertIn("never confirmed healthy", out)
        self.assertIs(self._ledger()["drill"]["ok"], False)
        self.assertEqual(runner.drill_evidence(), [],
                         "an unconfirmed rollback must not open the D9 gate")
        self.assertEqual({k: v for k, v in self._ledger().items() if k != "drill"}, before,
                         "the record is still restored, inconclusive or not")

    def test_a_dry_run_drill_writes_nothing(self):
        self._deployed()
        before = self._ledger()
        rc, out = self._drill(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger(), before)
        self.assertNotIn("DRILL PASSED", out)


class TestDrillSelection(DrillCase):

    def test_it_picks_a_deployed_system_and_skips_the_rest(self):
        self.assertIsNone(runner.drillable_system(json.loads(self.registry.read_text())),
                          "nothing deployed yet — there is nothing to drill")
        self._deployed()
        self.assertEqual(
            runner.drillable_system(json.loads(self.registry.read_text())), self.system)

    def test_the_self_row_is_never_selected(self):
        self._deployed()
        reg = json.loads(self.registry.read_text())
        reg["systems"][self.system]["self"] = True
        self.registry.write_text(json.dumps(reg))
        self.assertIsNone(runner.drillable_system(reg))

    def test_evidence_is_only_counted_when_the_drill_passed(self):
        self._deployed()
        self.assertEqual(runner.drill_evidence(), [])
        runner.write_ledger(self.system, drill={"ok": False, "detail": "broke"})
        self.assertEqual(runner.drill_evidence(), [],
                         "a FAILED drill is not evidence that the failure path works")
        self._drill()
        self.assertEqual([s for s, _ in runner.drill_evidence()], [self.system])


class TestTheUnattendedTriggerGatesItself(DrillCase):

    def _unattended(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = runner.unattended(**kw)
        return rc, buf.getvalue()

    def test_a_machine_that_is_not_the_declared_host_deploys_nothing(self):
        """The expected answer on most of the fleet, every ten minutes, forever. The poller
        that calls this runs on EVERY machine by design, so this is the gate that replaced
        the one deleted when the trigger moved out of a single-host launchd job."""
        self._set_host("SomewhereElse")
        rc, out = self._unattended()
        self.assertEqual(rc, 0, "not being the deploy host is routine, never an error")
        self.assertIn("resident machine is", out)
        self.assertFalse(self._tree().exists(), "nothing may be deployed here")

    def test_a_machine_that_cannot_name_itself_declines(self):
        """Not knowing where you are standing is a reason to withhold an unattended deploy,
        never to grant one."""
        with patch.object(common, "this_machine", lambda: ""):
            rc, out = self._unattended()
        self.assertEqual(rc, 1)
        self.assertIn("cannot name itself", out)
        self.assertFalse(self._tree().exists())

    def test_the_resident_machine_is_the_one_the_channel_declares(self):
        """NOT A SECOND KEY. The gate reads `channel.writer_label()` — the same declaration
        that decides who may write the one-writer mail branch — so a deploy host and a
        channel writer cannot drift into two answers about which machine is resident. With
        the override cleared it falls back to the channel's own default rather than to a
        permission."""
        self._set_host(None)
        self.assertEqual(channel.writer_label(), channel.DEFAULT_WRITER)
        rc, out = self._unattended()
        self.assertEqual(rc, 0)
        self.assertIn(channel.DEFAULT_WRITER, out)
        self.assertFalse(self._tree().exists(), "nothing may be deployed here")

    def test_it_drills_before_sweeping_when_there_is_no_evidence(self):
        self._deployed()
        self.assertEqual(runner.drill_evidence(), [])
        rc, out = self._unattended()
        self.assertEqual(rc, 0, out)
        self.assertIn("no drilled rollback on record", out)
        self.assertIn("DRILL PASSED", out)
        self.assertTrue(runner.drill_evidence())

    def test_it_does_not_re_drill_once_the_evidence_exists(self):
        """A drill restarts a live system twice. Doing it every ten minutes forever would
        make the safety check the largest source of disturbance on the box."""
        self._deployed()
        self._drill()
        rc, out = self._unattended()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DRILL PASSED", out)

    def test_nothing_deployed_yet_sweeps_rather_than_deadlocking(self):
        """THE AMENDED D9, AND THE PLACE IT IS WEAKER THAN THE ORIGINAL. A machine where
        nothing has ever deployed has nothing to drill, so requiring the drill first would
        deadlock permanently — which is the state this whole change exists to end. The
        sweep is allowed to establish a first deployment and the drill becomes due on the
        next pass. The window is one pass long and it is SAID OUT LOUD, not hidden."""
        rc, out = self._unattended()
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing deployed here yet", out)
        self.assertIn("due on the next pass", out)
        self.assertEqual(self._ledger().get("status"), "deployed")

    def test_a_failed_drill_stops_the_sweep(self):
        """NOT SWEEPING IS THE POINT. An unattended deployer whose recovery path has just
        been demonstrated broken is the one thing worse than one that was never tested."""
        self._deployed()
        with patch.object(runner, "drill", side_effect=runner.DeployError("boom")):
            rc, out = self._unattended()
        self.assertEqual(rc, 2)
        self.assertIn("DRILL FAILED", out)
        self.assertIn("refusing to sweep", out)

    def test_a_dry_run_does_not_drill(self):
        self._deployed()
        rc, out = self._unattended(dry_run=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DRILL", out)
        self.assertEqual(runner.drill_evidence(), [])


class TestTheCli(DrillCase):

    def _main(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = runner.main(argv)
        return rc, buf.getvalue()

    def test_drill_picks_its_own_system_when_none_is_named(self):
        self._deployed()
        rc, out = self._main(["--drill"])
        self.assertEqual(rc, 0, out)
        self.assertIn("DRILL PASSED", out)

    def test_drill_accepts_a_named_system(self):
        self._deployed()
        rc, out = self._main(["--drill", self.system])
        self.assertEqual(rc, 0, out)
        self.assertIn("DRILL PASSED", out)

    def test_unattended_is_reachable_from_the_cli(self):
        self._set_host("SomewhereElse")
        rc, out = self._main(["--unattended"])
        self.assertEqual(rc, 0)
        self.assertIn("resident machine is", out)


if __name__ == "__main__":
    unittest.main()
