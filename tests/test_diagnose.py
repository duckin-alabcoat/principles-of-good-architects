"""Tests for the read-only diagnose collector (WI-0229, Exit C of the 09-03 brief).

These reuse `test_deploy_runner.DeployRunnerCase` rather than building a second fixture,
which matters for more than tidiness: that fixture is what redirects every production path
away from the federation's own repo and the real ledger, and a diagnose suite with its own
half-copy of it is exactly how the eleven fixture receipts got committed for real the first
time. One fixture, one redirection map, one `TestEveryProductionPathIsRedirected` watching
both.

THE TWO PROPERTIES WORTH MOST are the ones a green suite would not otherwise show:

  * **It does not write.** Asserted by hashing the whole redirected state directory before
    and after a collection, including the case that used to be the exception — an invalid
    contract, where `runner.read_contract` writes a `contract-invalid` ledger row. A
    collector whose act of looking rewrites the record it reports is not a collector, and
    reading the code cannot establish the absence of a write.
  * **Silence means unchanged, never unseen.** The posting rule is a state change, so the
    tests that matter are the pair: appending to a log must NOT post (or the sweep posts
    144 times a day and the record buries what it records), while a real state change MUST.

`launchctl` is injected, never called — the same rule the runner's own suite states. A test
that could read or restart this machine's services would be a worse bug than any it caught.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner                                                        # noqa: E402
import diagnose                                                      # noqa: E402
from test_deploy_runner import CONTRACT, DeployRunnerCase            # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _fake_launchctl(out, rc=0):
    """A stand-in for `runner.run` that answers only `launchctl print`.

    Everything else — the gate commands the contract declares — is passed through to the
    real implementation. A mock that swallowed those too would make `verify` untestable
    and would quietly turn "the command ran and passed" into "nothing ran", which is the
    distinction half of this module exists to preserve.
    """
    real = runner.run

    def _run(cmd, **kw):
        if cmd[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(cmd, rc, out, "")
        return real(cmd, **kw)
    return _run


class DiagnoseCase(DeployRunnerCase):

    def _snapshot(self, **kw):
        return diagnose.snapshot(self.system, **kw)

    def _state_fingerprint(self):
        """Every byte under the redirected state roots, so a stray write cannot hide.

        Deliberately the DIRECTORY, not a list of files this test thought to name: the
        write worth catching is the one nobody predicted.
        """
        acc = {}
        for base in (self.root / "ledger", self.root / "comms",
                     self.fedrepo / "outbox", self.root / "LaunchAgents"):
            for p in sorted(base.rglob("*")) if base.exists() else []:
                if p.is_file():
                    acc[str(p.relative_to(self.root))] = hashlib.sha256(
                        p.read_bytes()).hexdigest()
        return acc


class TheCollectorDoesNotWrite(DiagnoseCase):
    """ADR-0103 D4 made the runner plain code; this is the stricter claim underneath it."""

    def test_a_collection_changes_nothing_on_disk(self):
        self._deploy()
        before = self._state_fingerprint()
        self._snapshot()
        self.assertEqual(before, self._state_fingerprint(),
                         "collecting a diagnosis wrote something")

    def test_an_invalid_contract_is_reported_without_writing_the_ledger(self):
        """`runner.read_contract` writes `status=contract-invalid` when validation fails.

        That is correct for a deploy and fatal for an observer, and it is why `_contract`
        calls the validator directly instead of reusing the whole function. Without this
        test the reuse would look like the obvious simplification it is not.
        """
        self._deploy()
        self._release("v2.0.0", contract={**CONTRACT, "nonsense_key": "x"})
        _tree = runner.deploy_tree(self.system)
        runner.git(["fetch", "--tags", "origin"], _tree, timeout=60)
        runner.git(["checkout", "--quiet", "--detach", "v2.0.0"], _tree, timeout=60)

        before = self._state_fingerprint()
        bundle = self._snapshot()
        self.assertEqual(before, self._state_fingerprint(),
                         "reporting an invalid contract wrote a ledger row")
        self.assertIs(bundle["contract"]["valid"], False)
        self.assertIn("nonsense_key", bundle["contract"]["why"])


class SilenceMeansUnchanged(DiagnoseCase):
    """The posting rule, from both sides. Either half alone is the wrong program."""

    def setUp(self):
        super().setUp()
        self._deploy()

    def test_the_first_bundle_posts(self):
        r = diagnose.post_if_changed(self.system, log=lambda _m: None)
        self.assertTrue(r["posted"], r)
        self.assertEqual(len(self._queued()), 2,
                         "expected the deploy receipt and one diagnosis")

    def test_a_second_run_with_nothing_changed_posts_nothing(self):
        diagnose.post_if_changed(self.system, log=lambda _m: None)
        n = len(self._queued())
        r = diagnose.post_if_changed(self.system, log=lambda _m: None)
        self.assertFalse(r["posted"])
        self.assertEqual(r["why"], "no change in the stable state")
        self.assertEqual(len(self._queued()), n, "a second bundle was posted unchanged")

    @staticmethod
    def _bundle(logs="first\n", loaded=True, last_exit="0"):
        """A bundle in the shape `snapshot` produces, varying only what each test varies.

        Hand-built ON PURPOSE, and the exception to this file's own preference for real
        fixtures: `digest` is a pure function from bundle to digest, so its input domain
        IS a bundle, and the deployed tree that would produce one cannot be made to grow a
        log line on demand without units this suite must not create.
        """
        return {
            "registered": True,
            "tree": {"present": True, "deployed_tag": "v1.0.0"},
            "contract": {"valid": True},
            "ledger": {"readable": True, "present": True, "record": {"status": "deployed"}},
            "units": {"rows": [{"label": "com.example.unit", "loaded": loaded,
                                "points_at_deploy_tree": True,
                                "last_exit_code": last_exit}]},
            "verify": {"ok": True, "exit": 0},
            "logs": [{"label": "com.example.unit",
                      "stdout": {"read": True, "tail": logs}}],
            "gaps": [],
        }

    def test_the_digest_ignores_log_text(self):
        """THE LOAD-BEARING NEGATIVE. A log gains lines continuously and the sweep runs
        every 600s, so a digest that included log text would post on every single run —
        rebuilding, inside the fix, the exact problem the posting rule exists to prevent.
        """
        self.assertEqual(diagnose.digest(self._bundle(logs="first\n")),
                         diagnose.digest(self._bundle(logs="first\nsecond\nthird\n")),
                         "the digest moved because a log grew, so every sweep would post")

    def test_the_digest_still_notices_a_unit_going_down(self):
        """The control for the test above. A digest that ignored EVERYTHING would satisfy
        it perfectly and report a dead system as unchanged forever — a decoy that passes
        the mutation it claims to rule out. This is what makes the pair meaningful."""
        self.assertNotEqual(diagnose.digest(self._bundle(loaded=True)),
                            diagnose.digest(self._bundle(loaded=False)))
        self.assertNotEqual(diagnose.digest(self._bundle(last_exit="0")),
                            diagnose.digest(self._bundle(last_exit="78")))

    def test_a_real_state_change_does_post(self):
        """The companion positive: silence must be caused by nothing happening, not by a
        digest too coarse to notice anything."""
        diagnose.post_if_changed(self.system, log=lambda _m: None)
        n = len(self._queued())
        self._release("v2.0.0", contract=CONTRACT, marker="two")
        self._deploy()
        r = diagnose.post_if_changed(self.system, log=lambda _m: None)
        self.assertTrue(r["posted"], "a new deployed tag did not produce a bundle")
        self.assertGreater(len(self._queued()), n)

    def _flapping(self, last_exit):
        """The flapping shape: one unit whose last exit code moves between sweeps."""
        real = diagnose.snapshot

        def snapshot(system, run_verify=True):
            bundle = real(system, run_verify=run_verify)
            bundle["units"] = {"rows": [{"label": "com.example.unit", "loaded": True,
                                         "points_at_deploy_tree": True,
                                         "last_exit_code": last_exit}]}
            return bundle
        return patch.object(diagnose, "snapshot", snapshot)

    def _post(self, last_exit):
        with self._flapping(last_exit):
            return diagnose.post_if_changed(self.system, log=lambda _m: None)

    def test_a_flapping_unit_posts_each_state_once_per_tag(self):
        """OPS-0010 soak: hundreds of bundles from one member in four days, five distinct states. The
        old rule compared with the LAST digest only, so 0,1,0,1 was four changes."""
        n = len(self._queued())
        posted = [self._post(code)["posted"] for code in ("0", "1", "0", "1", "0", "1")]
        self.assertEqual(posted, [True, True, False, False, False, False])
        self.assertEqual(len(self._queued()), n + 2)

    def test_a_new_tag_reposts_a_state_already_reported(self):
        """The control: the list is per TAG. Without it, a state first seen under v1
        would be silenced forever, including under a release that changed everything."""
        self._post("0")
        self._release("v2.0.0", contract=CONTRACT, marker="two")
        self._deploy()
        self.assertTrue(self._post("0")["posted"],
                        "the same state under a new deployed tag was treated as old news")
        record = json.loads(diagnose._digest_path(self.system).read_text())
        self.assertEqual((record["deployed_tag"], len(record["posted"])), ("v2.0.0", 1),
                         "the old tag's states were carried into the new tag's list")

    def test_a_record_from_before_the_list_still_suppresses_an_unchanged_bundle(self):
        """The record on every host today is one bare digest. Reading it as nothing
        would re-post every system once on upgrade; reading it as a list of one does not."""
        with self._flapping("0"):
            bare = diagnose.digest(diagnose.snapshot(self.system))
        path = diagnose._digest_path(self.system)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bare))
        self.assertFalse(self._post("0")["posted"])
        self.assertTrue(self._post("1")["posted"])

    def test_a_failed_post_does_not_record_the_digest(self):
        """Otherwise a transient outbox failure silences the state change FOREVER: the
        next run compares against a digest that was written for a bundle nobody received.
        """
        with patch.object(diagnose.outbox, "post", side_effect=ValueError("nope")):
            r = diagnose.post_if_changed(self.system, log=lambda _m: None)
        self.assertFalse(r["posted"])
        self.assertFalse(diagnose._digest_path(self.system).exists(),
                         "the digest was recorded for a bundle that was never sent")
        r2 = diagnose.post_if_changed(self.system, log=lambda _m: None)
        self.assertTrue(r2["posted"], "the retry stayed silent about an unreported change")


class WhatItCouldNotSeeIsNamed(DiagnoseCase):

    def test_an_unregistered_system_is_not_a_healthy_one(self):
        b = diagnose.snapshot("not-a-system")
        self.assertIs(b["registered"], False)
        self.assertIn("NOT", b["why"])
        text = diagnose.render(b)
        self.assertIn("Not deployable here", text)
        self.assertNotIn("## Units", text,
                         "a system this machine cannot see must not get a units verdict")

    def test_never_deployed_does_not_render_empty_verdicts(self):
        """The defect the first live run of this collector actually produced: with no
        tree it printed 'Units: none' and 'Verify: not declared', which read as findings
        about the system and were findings about nothing having been asked."""
        b = self._snapshot()
        self.assertEqual(b["stopped_at"], "tree")
        text = diagnose.render(b)
        self.assertIn("was not reached", text)
        self.assertNotIn("not declared", text)

    def test_an_unreadable_log_is_a_gap_and_not_an_empty_one(self):
        self._deploy()
        gaps = []
        rec = diagnose._tail(str(self.root / "no-such-dir" / "x.log"), gaps, "probe")
        self.assertFalse(rec["read"])
        self.assertIn("absent on disk", rec["why"])
        self.assertNotIn("empty", rec)

    def test_an_empty_log_says_so_distinctly(self):
        p = self.root / "empty.log"
        p.write_text("")
        rec = diagnose._tail(str(p), [], "probe")
        self.assertTrue(rec["read"])
        self.assertTrue(rec["empty"])
        self.assertIn("not the same as never having run", rec["why"])


class LogPathsAreDerivedFromRealPlists(DiagnoseCase):
    """Nothing declares a log path — not the registry, and not the contract, whose schema
    is `additionalProperties: false` and which is read from an immutable tag. The plist is
    the only thing that carries the answer, so this parses REAL plists rather than a
    stand-in: `plistlib` on a file is the whole mechanism, and a mock of it would assert
    the assumption back.

    This is also the part of the collector devbox can never exercise for real — it holds
    no deploy trees and no installed units — so it is exercised here or nowhere.
    """

    LABEL = "com.example.widget"

    def _write_plist(self, path, out=None, err=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"Label": self.LABEL, "WorkingDirectory": "/tmp/x"}
        if out:
            data["StandardOutPath"] = str(out)
        if err:
            data["StandardErrorPath"] = str(err)
        path.write_bytes(plistlib.dumps(data))

    def test_the_installed_plist_is_preferred_over_the_tags(self):
        """They answer different questions. The installed one is what launchd LOADED; the
        one in the tree is what the current tag WOULD install. This is a question about the
        process that is running, so the installed one wins — and both are reported, because
        a disagreement between them is itself worth seeing."""
        installed_log = self.root / "installed.log"
        tag_log = self.root / "from-tag.log"
        self._write_plist(runner.launch_agents_dir() / f"{self.LABEL}.plist",
                          out=installed_log)
        entry = {"subdir": None}
        self._write_plist(runner.unit_plist(self.system, entry, self.LABEL), out=tag_log)

        where = diagnose._log_paths(self.system, entry, self.LABEL)
        self.assertEqual(where["installed"]["stdout"], str(installed_log))
        self.assertEqual(where["tag"]["stdout"], str(tag_log))

    def test_a_stream_the_plist_does_not_declare_is_discarded_by_launchd(self):
        """`StandardErrorPath` absent is not an empty log — launchd throws that stream
        away, and saying 'no output' about it would be a claim nobody can support."""
        self._write_plist(runner.launch_agents_dir() / f"{self.LABEL}.plist",
                          out=self.root / "o.log")
        where = diagnose._log_paths(self.system, {"subdir": None}, self.LABEL)
        self.assertIsNone(where["installed"]["stderr"])
        rec = diagnose._tail(where["installed"]["stderr"], [], "probe")
        self.assertFalse(rec["read"])
        self.assertIn("discards it", rec["why"])

    def test_an_unreadable_plist_says_so_instead_of_reporting_no_logs(self):
        bad = runner.launch_agents_dir() / f"{self.LABEL}.plist"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("this is not a plist")
        where = diagnose._log_paths(self.system, {"subdir": None}, self.LABEL)
        self.assertFalse(where["installed"]["read"])
        self.assertIn("plist", where["installed"])

    def test_a_real_log_is_tailed_with_the_cut_announced(self):
        """`evidence.clip` names the file the full text is still in, so a reader who needs
        the head knows it exists and where. A bare tail reads as the whole log."""
        log = self.root / "big.log"
        log.write_text("".join(f"line {i}\n" for i in range(200)))
        rec = diagnose._tail(str(log), [], "probe")
        self.assertTrue(rec["read"])
        self.assertIn("line 199", rec["tail"])
        self.assertNotIn("line 0\n", rec["tail"])
        self.assertIn(str(log), rec["tail"],
                      "the cut did not name the file holding the full text")


class MachineOutputIsRedacted(DiagnoseCase):
    """The outbox is tracked and pushed, so anything quoted here is permanent."""

    def test_a_log_tail_with_a_secret_is_neutralised_and_the_bundle_still_travels(self):
        self._deploy()
        log = self.root / "unit.log"
        log.write_text("serving on 10.20.30.40\napi_key = hunter2hunter2\nall good\n")
        gaps = []
        rec = diagnose._tail(str(log), gaps, "unit stdout")
        tail = rec["tail"]
        self.assertNotIn("10.20.30.40", tail)
        self.assertNotIn("hunter2hunter2", tail,
                         "masking the MARKER leaves the secret one character to the right")
        self.assertIn("all good", tail, "redaction ate the surrounding evidence")
        self.assertTrue(gaps, "a redaction happened and the bundle did not say so")

    def test_the_scrub_gate_is_an_independent_check_and_is_never_forced(self):
        """`outbox.post` refuses on findings. That door stays shut in front of `_clean`
        rather than being argued past, so a class the redactor misses still cannot travel.
        """
        src = (REPO / "deploy" / "diagnose.py").read_text()
        self.assertNotIn("force=True", src)
        self.assertNotIn("force = True", src)

    def test_raw_launchctl_output_is_never_stored(self):
        """It can carry the loaded job's whole environment — `curate/mailacceptance.py`
        refuses to keep it for exactly this reason."""
        raw = ("\tstate = running\n\tpid = 4242\n\tlast exit code = 0\n"
               "\tworking directory = /tmp/x\n\tSECRET_TOKEN => abcdef123456\n")
        with patch.object(runner, "run", _fake_launchctl(raw)):
            rec = diagnose._launchctl("com.example.unit", [])
        self.assertEqual(rec["pid"], "4242")
        self.assertNotIn("abcdef123456", json.dumps(rec))
        self.assertNotIn("SECRET_TOKEN", json.dumps(rec))


class UnitLiveness(DiagnoseCase):

    def test_a_unit_that_is_not_loaded_is_reported_as_such(self):
        with patch.object(runner, "run", _fake_launchctl("", rc=1)):
            rec = diagnose._launchctl("com.example.unit", [])
        self.assertFalse(rec["loaded"])

    def test_named_fields_are_parsed_and_the_unread_ones_are_declared(self):
        with patch.object(runner, "run", _fake_launchctl("\tstate = running\n")):
            gaps = []
            rec = diagnose._launchctl("com.example.unit", gaps)
        self.assertEqual(rec["state"], "running")
        self.assertNotIn("pid", rec)
        self.assertTrue(any("UNREAD, not zero" in g for g in gaps),
                        "a field that was not read must not read as absent")


class TheVerifyIsExecutedAndSaysSo(DiagnoseCase):

    def test_no_verify_reports_skipped_rather_than_passed(self):
        self._deploy()
        b = self._snapshot(run_verify=False)
        self.assertIn("skipped", b["verify"])
        self.assertNotIn("ok", b["verify"])
        self.assertIn("says nothing about whether it passes", b["verify"]["skipped"])

    def test_a_declared_verify_runs_and_is_marked_as_executed(self):
        self._deploy()
        b = self._snapshot()
        self.assertTrue(b["verify"]["executed"])
        self.assertTrue(b["verify"]["ok"])

    def test_an_undeclared_verify_is_unchecked_and_never_passed(self):
        line = diagnose._gate_line({"declared": False})
        self.assertIn("NOT DECLARED", line)
        self.assertIn("never 'passed'", line)

    def test_a_timeout_is_not_a_plain_failure(self):
        line = diagnose._gate_line({"declared": True, "ok": False, "exit": None,
                                    "detail": "timed out after 60s"})
        self.assertIn("TIMED OUT", line)


class TheBriefIsDeliverable(DiagnoseCase):
    """A header the poller refuses is bucketed `malformed` forever and never moves the
    exit code — so a wrong header here builds a return path whose failure is silent."""

    def test_the_header_passes_the_real_check_apply_guard(self):
        self._deploy()
        diagnose.post_if_changed(self.system, log=lambda _m: None)
        briefs = [f for _a, f in self._queued() if "diagnose" in f.name]
        self.assertEqual(len(briefs), 1, [f.name for _a, f in self._queued()])
        proc = subprocess.run(
            [sys.executable, str(REPO / "curate" / "check-apply.py"), str(briefs[0])],
            capture_output=True, text=True,
            env={**os.environ, "POGA_ADOPTION_LEDGER_OFF": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_it_routes_to_a_reader_and_not_to_the_headless_runner(self):
        self._deploy()
        diagnose.post_if_changed(self.system, log=lambda _m: None)
        text = [f for _a, f in self._queued() if "diagnose" in f.name][0].read_text()
        self.assertIn("apply: manual", text)
        self.assertIn("manual-reason: attended", text)

    def test_silence_is_explained_inside_the_bundle(self):
        """A reader holding one bundle needs to know that no later bundle means no later
        CHANGE, not that the collector stopped."""
        self._deploy()
        diagnose.post_if_changed(self.system, log=lambda _m: None)
        text = [f for _a, f in self._queued() if "diagnose" in f.name][0].read_text()
        self.assertIn("posts nothing", text)


class TheSweepIsNotEndangeredByIt(DiagnoseCase):

    def test_one_systems_failed_collection_is_reported_and_not_fatal(self):
        """The inner swallow, in `post_if_changed`: one system this machine cannot read
        must not stop the others being reported, or the first broken system silences the
        whole fleet."""
        with patch.object(diagnose, "snapshot", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = runner.sweep()
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("could not collect", buf.getvalue(),
                      "the failure was swallowed silently instead of being said")

    def test_a_collector_that_fails_outright_does_not_change_the_sweeps_exit_code(self):
        """The OUTER swallow, in `runner.sweep`. The two are not the same guard and the
        first does not imply the second: `diagnose.sweep` handles each system itself, so
        the runner's own handler only ever sees a failure of the collector as a whole —
        an import error, a broken registry read. A mutation making that path set the
        sweep's exit code went undetected until this test existed.

        The sweep's job is to deploy. A report that could not be produced must not turn a
        successful deploy run into a failed one, because a launchd job that exits non-zero
        for a condition no re-run clears is an alarm channel that gets muted."""
        with patch.object(diagnose, "sweep", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = runner.sweep()
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("what is lost is this run's report", buf.getvalue(),
                      "the sweep must say the report is missing, not imply it is clean")


class TheStaleLedgerFieldIsMarked(DiagnoseCase):
    """`write_ledger` merges, so a field from three releases ago sits beside today's
    status and reads exactly like a current fact."""

    def test_a_ledger_naming_another_tag_is_called_out(self):
        self._deploy()
        runner.write_ledger(self.system, current="v0.0.9")
        b = self._snapshot()
        self.assertIn("ledger_disagrees_with_tree", b["ledger"])
        self.assertIn("ground truth", b["ledger"]["ledger_disagrees_with_tree"])

    def test_a_cutover_event_for_an_older_tag_is_called_out(self):
        self._deploy()
        tag = runner.deployed_tag(self.system, runner.registry_entry(self.system))
        runner.write_ledger(self.system, cutover_event={"how": "performed",
                                                        "tag": "v0.0.1", "at": "x"})
        b = self._snapshot()
        self.assertIn("stale_cutover_event", b["ledger"])
        self.assertIn(tag, b["ledger"]["stale_cutover_event"])


class TheVerbIsWiredIntoPoga(unittest.TestCase):
    """Mirrors `test_preflight.py`'s wiring pattern. `poga` ships byte-identical to every
    member while `deploy/` is federation-only, so the refusal path is part of the wiring.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = (REPO / "poga").read_text()

    def test_the_case_arm_exists_at_the_indent_the_table_reader_requires(self):
        import re
        self.assertTrue(re.search(r"^    diagnose\)", self.src, re.M),
                        "no 4-space `diagnose)` arm — the awk table reader would not see "
                        "it, so the verb would work and be absent from the roster")

    def test_it_execs_the_collector_and_not_the_runner(self):
        arm = self.src.split("    diagnose)", 1)[1].split(";;", 1)[0]
        self.assertIn("deploy/diagnose.py", arm)
        self.assertNotIn("runner.py", arm,
                         "diagnose must not route through the program that mutates")

    def test_it_refuses_by_name_on_a_member_checkout(self):
        arm = self.src.split("    diagnose)", 1)[1].split(";;", 1)[0]
        self.assertIn("not installed from a federation checkout", arm)

    def test_it_is_in_the_help_block(self):
        self.assertIn("poga diagnose", self.src)


class TheHostEmitsTheAcceptanceEvidence(DiagnoseCase):
    """WI-0396: `curate/mailacceptance.py` could always read WI-0366's inputs and nothing
    outside its own tests ever called it, so the evidence existed only when a person ran a
    command on the Runner. These cover the call, and the thing that makes the call worth
    having — that it reports a GAP instead of a green line.

    `launchctl` is injected here exactly as everywhere else in this file, and with a sharper
    reason: these tests name one of the three real federation job labels, so a probe that
    escaped would be reading the machine the federation actually runs on.
    """

    UNIT = "com.federation.mail-poller"
    #: All three, because the acceptance is about the three together — a contract naming
    #: one is the partial case and gets its own test below, not the default fixture.
    UNITS = sorted(diagnose.acceptance.LABELS)

    def setUp(self):
        super().setUp()
        self._release("v2.0.0", contract={**CONTRACT, "units": list(self.UNITS)})

    @staticmethod
    def _loaded(wd, config="/fixture/prod/config/production.json", args=None):
        """`launchctl print` output in the shape `parse_loaded_job` reads."""
        argv = args if args is not None else ["/usr/bin/python3", f"{wd}/curate/mailworker.py"]
        block = "".join(f"\t\t{a}\n" for a in argv)
        return ("\tstate = running\n\tpid = 4242\n\tlast exit code = 0\n"
                f"\tworking directory = {wd}\n"
                f"\targuments = {{\n{block}\t}}\n"
                + (f"\tPOGA_FEDERATION_CONFIG => {config}\n" if config else ""))

    def _collect(self, out, rc=0, host=False):
        """Deploy and collect with every outward probe injected.

        The deploy runs under the same `launchctl` stand-in as the collection: a contract
        that declares units makes `cutover_state` ask launchd about them, and that question
        must not reach this machine either.
        """
        with patch.object(runner, "run", _fake_launchctl(out, rc)):
            self._deploy()
            if not host:
                return self._snapshot(run_verify=False)
            with self._host():
                return self._snapshot(run_verify=False)

    def _host(self):
        """The two probes that would otherwise leave the fixture, injected together.

        `production.resolve` because the fixture deliberately CLEARS `POGA_FEDERATION_CONFIG`
        rather than substituting one, and `deliver.locate_repos` because it walks the
        operator's real member map — the same injection `test_mailacceptance` makes, for the
        same reason. `mailacceptance.observe_host` itself runs for real.
        """
        state = self.root / "prod-state"
        (state / "runtime").mkdir(parents=True, exist_ok=True)
        (state / "config").mkdir(parents=True, exist_ok=True)
        roots = diagnose.production.Roots(
            runner.deploy_tree(self.system), state, self.root / "prod-transport",
            state / "config", None, {"owned_ref": "refs/heads/fixture/mail"})
        link = runner.cli_link()
        link.parent.mkdir(parents=True, exist_ok=True)
        target = self.root / "prod-poga"
        target.write_text("fixture only\n")
        if not link.is_symlink():
            link.symlink_to(target)
        real = diagnose.acceptance.observe_host

        def observe(_roots, poga, **_kw):
            return real(_roots, poga,
                        lookup=lambda _r: {"status": "observed", "members": ["m"], "count": 1})

        stack = ExitStack()
        stack.enter_context(patch.object(diagnose.production, "resolve", return_value=roots))
        stack.enter_context(patch.object(diagnose.acceptance, "observe_host", observe))
        stack.enter_context(patch("mailworker.read_status", return_value={"status": "ok"}))
        return stack

    # -- scoping ---------------------------------------------------------------

    def test_a_system_declaring_none_of_the_jobs_gets_no_section_at_all(self):
        """Absent because it was never asked, not empty because the answer was nothing.
        An empty acceptance block on an unrelated member would read as a finding about a system
        WI-0366's acceptance has nothing to say about."""
        self._release("v3.0.0", contract=CONTRACT)
        with patch.object(runner, "run", _fake_launchctl("", rc=1)):
            self._deploy()
            bundle = self._snapshot(run_verify=False)
        self.assertEqual(bundle["contract"]["units"], [])
        self.assertNotIn("acceptance", bundle)
        self.assertNotIn("Host evidence", diagnose.render(bundle))

    # -- the positive control --------------------------------------------------

    def test_a_loaded_job_on_a_resolved_host_reports_the_program_and_is_complete(self):
        """Without this the `complete` flag could be hard-wired false and every negative
        below would still pass — a decoy that survives the mutation it claims to rule out.
        """
        wd = str(runner.deploy_tree(self.system))
        bundle = self._collect(self._loaded(wd), host=True)
        acc = bundle["acceptance"]
        self.assertEqual(acc["gaps"], [], acc["gaps"])
        self.assertTrue(acc["complete"])
        job = acc["jobs"][self.UNIT]
        self.assertTrue(job["loaded"])
        self.assertEqual(job["working_directory"], wd)
        self.assertEqual(job["arguments"],
                         ["/usr/bin/python3", f"{wd}/curate/mailworker.py"])
        self.assertEqual(job["config_environment"],
                         "/fixture/prod/config/production.json")
        self.assertEqual(acc["release"]["deployed_tag"], "v2.0.0")
        self.assertTrue(acc["host"]["read"])
        self.assertIn("Host evidence", diagnose.render(bundle))

    # -- the negative controls -------------------------------------------------

    def test_a_job_that_is_not_loaded_is_a_named_gap_and_never_a_pass(self):
        bundle = self._collect("", rc=1, host=True)
        acc = bundle["acceptance"]
        self.assertFalse(acc["complete"])
        self.assertFalse(acc["jobs"][self.UNIT]["loaded"])
        named = [g for g in acc["gaps"] if self.UNIT in g and "NOT LOADED" in g]
        self.assertTrue(named, acc["gaps"])
        self.assertIn(named[0], bundle["gaps"],
                      "the acceptance gap did not reach the bundle's own gap list")
        self.assertIn(named[0], diagnose.render(bundle),
                      "a reader of the prose would not see the unit was not loaded")

    def test_launchctl_answering_in_an_unreadable_shape_is_a_named_gap(self):
        """The distinction WI-0366's OWNERSHIP clause turns on: a line saying the unit is
        there is not a loaded-unit probe. The parser fails closed, and this is what keeps
        that refusal from being swallowed into a silent default."""
        bundle = self._collect("Installed com.federation.mail-poller successfully\n",
                               host=True)
        acc = bundle["acceptance"]
        self.assertFalse(acc["complete"])
        self.assertTrue(acc["jobs"][self.UNIT]["loaded"])
        self.assertNotIn("arguments", acc["jobs"][self.UNIT])
        self.assertTrue([g for g in acc["gaps"] if "does not answer" in g
                         or "shape the parser recognises" in g], acc["gaps"])

    def test_an_unset_production_config_is_three_missing_inputs_and_not_a_healthy_host(self):
        """The fixture clears `POGA_FEDERATION_CONFIG`, so this is the un-injected path —
        and it is also what a real host looks like before its config is placed. `poga`,
        the worker and the member lookup are then ABSENT, which is not the same as fine."""
        wd = str(runner.deploy_tree(self.system))
        bundle = self._collect(self._loaded(wd))
        acc = bundle["acceptance"]
        self.assertFalse(acc["complete"])
        self.assertFalse(acc["host"]["read"])
        self.assertTrue([g for g in acc["gaps"] if "NOT COLLECTED" in g], acc["gaps"])
        # The renderer's own branch for this case. A bundle that raised while being written
        # up would take the whole diagnosis down on exactly the host that has no config.
        text = diagnose.render(bundle)
        self.assertIn("Absent, not healthy", text)
        self.assertNotIn("Worker:", text)

    def test_a_job_loaded_without_the_config_in_its_environment_is_a_gap(self):
        wd = str(runner.deploy_tree(self.system))
        bundle = self._collect(self._loaded(wd, config=None), host=True)
        acc = bundle["acceptance"]
        self.assertFalse(acc["complete"])
        self.assertIsNone(acc["jobs"][self.UNIT]["config_environment"])
        self.assertTrue([g for g in acc["gaps"] if "POGA_FEDERATION_CONFIG" in g],
                        acc["gaps"])

    def test_the_two_jobs_this_contract_does_not_declare_are_named_not_omitted(self):
        """The acceptance is about all three together. A bundle reporting on one and saying
        nothing about the others would read as evidence for three."""
        self._release("v3.0.0", contract={**CONTRACT, "units": [self.UNIT]})
        wd = str(runner.deploy_tree(self.system))
        acc = self._collect(self._loaded(wd), host=True)["acceptance"]
        self.assertEqual(sorted(acc["jobs"]), [self.UNIT])
        missing = [g for g in acc["gaps"] if "never asked about here" in g]
        self.assertTrue(missing, acc["gaps"])
        for label in ("com.federation.deploy-sweep", "com.federation.adopt-runner"):
            self.assertIn(label, missing[0])


class TheDigestCarriesTheAcceptanceEvidence(unittest.TestCase):
    """Hand-built bundles, for the reason `SilenceMeansUnchanged._bundle` already gives:
    `digest` is a pure function whose input domain IS a bundle, and the states these need
    cannot be produced from a deployed tree without units this suite must not create."""

    @staticmethod
    def _bundle(complete=True, loaded=True, args=("/usr/bin/python3", "a.py"),
                worker="ok", members=("m",)):
        return {
            "registered": True,
            "tree": {"present": True, "deployed_tag": "v1.0.0"},
            "contract": {"valid": True},
            "ledger": {"readable": True, "present": True, "record": {"status": "deployed"}},
            "units": {"rows": [{"label": "com.federation.mail-poller", "loaded": loaded,
                                "points_at_deploy_tree": True, "last_exit_code": "0"}]},
            "verify": {"ok": True, "exit": 0},
            "logs": [],
            "gaps": [],
            "acceptance": {
                "complete": complete,
                "jobs": {"com.federation.mail-poller": {
                    "loaded": loaded, "working_directory": "/tmp/x",
                    "arguments": list(args), "config_environment": "/tmp/c.json"}},
                "host": {"read": True, "poga": {"target": "/tmp/x/poga"},
                         "worker": {"status": worker},
                         "member_lookup": {"status": "observed", "members": list(members)}},
                "gaps": [], "release": {"deployed_tag": "v1.0.0"},
            },
        }

    def test_the_digest_survives_the_round_trip_it_is_compared_across(self):
        """THE CONTROL FOR THE WHOLE POSTING RULE. `post_if_changed` compares this dict
        against one read back with `json.loads`, which returns lists — so a tuple anywhere
        inside can never equal its own recorded form, and the sweep posts an identical
        bundle every 600 seconds forever. That is the failure the posting rule exists to
        prevent, arriving through the mechanism meant to prevent it.

        It went unnoticed because `units` was the only list in the digest and every
        contract in this suite declares none, so the value compared was `[]` both ways
        (WI-0396). Declaring a unit is what makes the round trip mean anything.
        """
        d = diagnose.digest(self._bundle())
        self.assertEqual(json.loads(json.dumps(d, default=str)), d,
                         "the digest cannot compare equal to its own recorded form")

    def test_it_moves_when_the_host_evidence_stops_being_complete(self):
        self.assertNotEqual(diagnose.digest(self._bundle(complete=True)),
                            diagnose.digest(self._bundle(complete=False)))

    def test_it_moves_when_the_loaded_program_changes(self):
        """The one the acceptance is actually about: a unit still loaded, still pointing at
        the deploy tree, still exit 0 — and running a different program. Every field the
        digest carried before WI-0396 is identical across this pair."""
        before = self._bundle(args=("/usr/bin/python3", "a.py"))
        after = self._bundle(args=("/usr/bin/python3", "/old/checkout/a.py"))
        self.assertEqual(before["units"], after["units"])
        self.assertNotEqual(diagnose.digest(before), diagnose.digest(after))

    def test_it_moves_when_the_worker_or_the_member_set_changes(self):
        self.assertNotEqual(diagnose.digest(self._bundle(worker="ok")),
                            diagnose.digest(self._bundle(worker="stale")))
        self.assertNotEqual(diagnose.digest(self._bundle(members=("m",))),
                            diagnose.digest(self._bundle(members=("m", "n"))))

    def test_a_bundle_with_no_acceptance_section_still_digests(self):
        b = self._bundle()
        del b["acceptance"]
        self.assertIsNone(diagnose.digest(b)["acceptance"])


class TheModuleIsRegisteredWithTheGuards(unittest.TestCase):
    """A capability whose detector nobody wired is a capability the conformance surface
    reports as absent ([`ship-the-detector-with-the-capability`])."""

    def test_the_advice_lint_reads_this_module(self):
        import substrate_voice as sv
        self.assertIn("deploy/diagnose.py", sv.PY_SUBJECTS)

    def test_the_redirection_guard_scans_this_module(self):
        import test_deploy_runner as tdr
        self.assertIn("deploy/diagnose.py",
                      tdr.TestEveryProductionPathIsRedirected.SOURCES)


if __name__ == "__main__":
    unittest.main()
