"""WI-0410 — a deploy refusal reaches the ledger, and required state can be derived.

TWO HALVES OF ONE FAILURE. The federation's production deploy refused at the state gate on
2026-09-13 and stayed dormant until 2026-09-20, when a human read a launchd stdout log on
the host. Nothing else could have told anyone: `--status` replays the last successful
record, and `deploy/diagnose.py` builds its bundle from the ledger, which a refusal never
touched. So a machine that refused to deploy and a machine that had nothing to do produced
the identical bundle.

The first half makes the refusal visible. The second half stops it happening again for the
reason it happened the first time — the two required state files no source on that machine
held, and which a seed-from-source design would refuse for on every rebuild forever.

WHAT THESE TESTS ARE CAREFUL ABOUT, because the obvious versions pass for free:

  * The refusal record must NOT overwrite what is still running. A test that asserts only
    "the refusal is recorded" passes just as happily against a `status="refused"` that has
    told `anything_cut_over` this machine never cut over.
  * The IDLE case has to be asserted too. "The bundle shows a refusal" and "the bundle
    shows a refusal only when there was one" are different claims, and only the pair rules
    out a key that is always set.
  * The digest is asserted separately from the bundle. A refusal that reaches the bundle
    and not the digest is mailed to nobody, which is the original defect one step later.
"""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_deploy_runner import CONTRACT, DeployRunnerCase, _git

import diagnose
import outbox
import runner


class RefusalCase(DeployRunnerCase):
    """A deploy that refuses at the state gate, which is where the real one refused."""

    #: A contract whose required state no source holds. `reconcile-roots.local` is the
    #: real file the deploy host refused on; using the real name keeps the derivation tests
    #: below honest rather than exercising a stand-in that nothing else knows about.
    STATEFUL = {**CONTRACT, "state": [{"path": "reconcile-roots.local", "required": True}]}

    def _refuse(self, **kw):
        """Drive a deploy that refuses, returning the refusal message."""
        with self.assertRaises(runner.DeployError) as cm:
            self._deploy(**kw)
        return str(cm.exception)

    def _snapshot(self, **kw):
        return diagnose.snapshot(self.system, **kw)

    def _register(self, name, tags=("v1.0.0",)):
        """A second registered system, so a refusal can be driven on a row that is not
        `widget`. Lifted from TestSweepAndStatus rather than imported: that helper is a
        method on a class this file does not otherwise want to inherit."""
        repo = self.root / name
        repo.mkdir()
        _git(["init", "-b", "main"], repo)
        # `_git` already passes this per-invocation, so this line changes no behaviour —
        # but `test_fixture_git_defaults` reads each module's OWN source, and a module that
        # creates a repo and commits into it must show the setting where a reader can see
        # it. A developer with `commit.gpgsign = true` globally otherwise gets a pinentry
        # prompt with no TTY and a setUp that fails talking about gpg (WI-0250).
        _git(["config", "commit.gpgsign", "false"], repo)
        (repo / "deploy").mkdir()
        (repo / "deploy" / "deploy.json").write_text(json.dumps({**CONTRACT, "system": name}))
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)
        for t in tags:
            _git(["tag", t], repo)
        reg = json.loads(self.registry.read_text())
        reg["systems"][name] = {"remote": str(repo), "subdir": None}
        self.registry.write_text(json.dumps(reg))
        return repo


class ARefusalReachesTheLedger(RefusalCase):
    """No release in setUp: two of these tests need a HEALTHY deploy on the books first,
    and a fixture that pre-stages the refusing contract would make the first deploy in
    those tests refuse instead of the second."""

    def test_a_refused_deploy_leaves_a_record_naming_the_refusal_and_the_attempted_tag(self):
        self._release("v2.0.0", contract=self.STATEFUL)
        self._refuse(trigger="sweep")
        ref = self._ledger().get("refusal")
        self.assertIsNotNone(ref, "the refusal reached stdout and nothing else — which is "
                                  "the whole defect this item is about")
        self.assertEqual(ref["attempted"], "v2.0.0")
        self.assertEqual(ref["trigger"], "sweep")
        self.assertIn("required state absent after seeding", ref["message"])
        self.assertTrue(ref["at"], "a refusal with no timestamp cannot be aged")

    def test_the_fields_describing_the_running_version_survive_a_refusal(self):
        """THE NEGATIVE CONTROL THE ITEM REQUIRES, and the one that rules out the obvious
        wrong fix. Writing `status="refused"` would satisfy every other test in this class
        while telling `anything_cut_over` (which gates the whole sealed-model question on
        `status == "deployed"`) and `drillable_system` that this machine is not deployed."""
        # Get a real, healthy deploy on the books first.
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        live = self._ledger()
        self.assertEqual(live["status"], "deployed")

        self._release("v3.0.0", contract=self.STATEFUL)
        self._refuse()

        after = self._ledger()
        for field in ("status", "current", "deployed_at", "previous", "smoke", "verify",
                      "cutover", "trigger"):
            self.assertEqual(after.get(field), live.get(field),
                             f"the refusal rewrote {field!r}, which describes the version "
                             f"that is STILL RUNNING and was not touched by the attempt")
        self.assertEqual(after["refusal"]["attempted"], "v3.0.0")

    def test_a_refusal_that_precedes_tag_resolution_says_so_rather_than_nothing(self):
        """`attempted: null` is a third answer, not a missing one. A refusal raised before
        `resolve_target` ran — no tags, unreadable tree — genuinely does not know what it
        was reaching for, and folding that into the same shape as a known tag is the
        collapse the item is about, rebuilt one level down."""
        repo = self._register("tagless", tags=())
        self.assertTrue(repo.exists())
        with self.assertRaises(runner.DeployError):
            runner.deploy("tagless", trigger="sweep", quiet=True)
        ref = runner.read_ledger("tagless").get("refusal")
        self.assertIsNotNone(ref)
        self.assertIn("attempted", ref, "the key must be present and null, not absent — "
                                        "absent reads as 'nobody looked'")
        self.assertIsNone(ref["attempted"])

    def test_a_later_deploy_that_runs_clears_the_refusal(self):
        """A refusal that outlives its own resolution reads as current forever."""
        self._release("v2.0.0", contract=self.STATEFUL)
        self._refuse()
        self.assertIsNotNone(self._ledger().get("refusal"))

        # Same tag, this time with state it can actually seed.
        self._release("v3.0.0", contract=CONTRACT)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIsNone(self._ledger().get("refusal"),
                          "the deploy ran and did not refuse, so the outstanding refusal "
                          "is history — leaving it makes every later bundle report a "
                          "blockage that has been fixed")

    def test_a_dry_run_that_refuses_writes_nothing(self):
        """A dry run is a question. A question that leaves a refusal record on a healthy
        machine is a question that changed the answer."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        before = json.dumps(self._ledger(), sort_keys=True)

        self._release("v3.0.0", contract=self.STATEFUL)
        # The state gate does not raise on a dry run; drive the refusal that does.
        with patch.object(runner, "seed_state",
                          side_effect=runner.DeployError("widget: refused")):
            with self.assertRaises(runner.DeployError):
                self._deploy(dry_run=True)
        self.assertEqual(json.dumps(self._ledger(), sort_keys=True), before)


class TheBundleShowsTheRefusalAsItsOwnState(RefusalCase):

    def setUp(self):
        super().setUp()
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)

    def test_an_idle_machine_produces_a_bundle_with_no_refusal_in_it(self):
        """THE THIRD CLAUSE OF THE ACCEPTANCE, and the half that makes the other two mean
        something. Asserted FIRST and against the same fixture, so a `deploy_refused` key
        that is always populated fails here rather than passing everywhere."""
        bundle = self._snapshot()
        self.assertNotIn("deploy_refused", bundle["ledger"])
        self.assertIsNone(diagnose.digest(bundle)["deploy_refused"])
        self.assertNotIn("**REFUSED:**", diagnose.render(bundle))

    def test_a_refused_deploy_shows_up_as_a_distinct_state(self):
        self._release("v3.0.0", contract=self.STATEFUL)
        with self.assertRaises(runner.DeployError):
            self._deploy()

        bundle = self._snapshot()
        said = bundle["ledger"]["deploy_refused"]
        self.assertIn("REFUSED", said)
        self.assertIn("v3.0.0", said)
        self.assertIn("required state absent after seeding", said)
        self.assertIn("**REFUSED:**", diagnose.render(bundle))
        # The live fields are still rendered as live, not as part of the failure.
        self.assertEqual(bundle["ledger"]["record"]["status"], "deployed")

    def test_the_digest_moves_when_a_deploy_refuses(self):
        """WITHOUT THIS THE ITEM IS HALF-BUILT. `post_if_changed` compares digests to
        decide whether to mail anything, and a refusal deliberately changes no other key
        in one: status, deployed_tag, units and verify all keep describing the running
        version. So a digest without this field compares EQUAL across a refusal and the
        bundle is correct and reaches nobody."""
        before = diagnose.digest(self._snapshot())

        self._release("v3.0.0", contract=self.STATEFUL)
        with self.assertRaises(runner.DeployError):
            self._deploy()
        after = diagnose.digest(self._snapshot())

        self.assertNotEqual(before, after)
        self.assertEqual(after["deploy_refused"], "v3.0.0")

        # EVERY LEDGER-SOURCED KEY IS UNMOVED, which is exactly why the new key had to
        # exist: the refusal wrote none of them, so a digest without `deploy_refused`
        # compares equal and `post_if_changed` mails nothing.
        self.assertEqual(before["ledger_status"], after["ledger_status"])
        self.assertEqual(before["verify_ok"], after["verify_ok"])
        self.assertEqual(before["units"], after["units"])

        # `deployed_tag` DOES move, and it is not this change that moves it: `checkout`
        # runs before `seed_state` on the forward path, so a deploy that refuses at the
        # state gate leaves the new tag on the tree with nothing restarted. Asserted here
        # rather than left as a surprise, because it is the hinge between this item and
        # WI-0411 — once the tree holds the target, every later sweep takes the
        # short-circuit and reports "already running, nothing to do" about a release that
        # never deployed.
        self.assertEqual(before["deployed_tag"], "v1.0.0")
        self.assertEqual(after["deployed_tag"], "v3.0.0")

    def test_an_unresolved_refusal_does_not_digest_as_no_refusal_at_all(self):
        """The bug this assertion was written against was live in the first cut: passing
        `attempted`'s own None through meant a refusal raised before tag resolution
        digested IDENTICALLY to a clean machine. Three states, three values."""
        runner.write_ledger(self.system, refusal={
            "at": "2026-09-21T00:00:00", "attempted": None,
            "trigger": "sweep", "message": "could not resolve a target"})
        self.assertEqual(diagnose.digest(self._snapshot())["deploy_refused"], "unresolved")


class RequiredStateIsDerivedWhenNoSourceHasIt(RefusalCase):
    """WI-0410's second half, and the acceptance recorded on the item 2026-09-20."""

    APPLIED = {**CONTRACT,
               "state": [{"path": "reconcile-roots.local", "required": True},
                         {"path": "repo-paths.local", "required": True}],
               "apply": {"cmd": ["/bin/sh", "-c", "echo applied > applied.txt"]}}

    def setUp(self):
        super().setUp()
        # TIER 3 MUST BE SHUT OFF HERE, AND NOT SHUTTING IT OFF WAS A FALSE GREEN.
        #
        # `retiring_root`'s last resort is: if this row is `self` and REPO_ROOT is not a
        # deploy tree, return REPO_ROOT. In the suite REPO_ROOT is the developer's live
        # checkout — which HAS a real `repo-paths.local` and a real `reconcile-roots.local`
        # (symlinked into every lane). So the first cut of these tests seeded the
        # federation's actual files into the fixture tree, the derivation never ran at all,
        # and two tests passed green over code they never executed. The assertion that
        # caught it was the one naming the expected CONTENT; every assertion that checked
        # only "the file exists" passed.
        #
        # None is also the honest state of the machine this item is about: the deploy
        # host's `migrate.py plan` reported `retiring_root: null`, because the units
        # had already left the dev checkout. The fixture matches the box.
        p = patch.object(runner, "retiring_root", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _member_root(self) -> Path:
        root = Path(os.environ["POGA_DEPLOY_MEMBER_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _self_registry(self):
        """Mark the row `self`, which is what `repo-paths.local` derivation keys on."""
        reg = json.loads(self.registry.read_text())
        reg["systems"][self.system]["self"] = True
        self.registry.write_text(json.dumps(reg))

    def test_an_empty_state_vault_reaches_apply_on_the_first_sweep(self):
        """THE STATED ACCEPTANCE, verbatim from the item's 2026-09-20 note: 'a fresh state
        vault with none of the three files present reaches apply on the first sweep.'

        Nothing is seeded from anywhere — no map, no vault, no retiring checkout — which
        is precisely the state the deploy host measured (deploy tree 1 of 3,
        vault 0 of 3, retiring_root null)."""
        self._member_root()
        self._self_registry()
        self._release("v2.0.0", contract=self.APPLIED)
        self.assertFalse(runner.state_vault(self.system).exists())

        rc = runner.sweep()

        self.assertEqual(rc, 0, "the sweep refused where the real one refused")
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertIsNone(self._ledger().get("refusal"))
        self.assertTrue((self._tree() / "applied.txt").exists(),
                        "the deploy reached apply — which is the whole acceptance; a "
                        "deploy that seeds state and then refuses later proves nothing")

    def test_the_derived_roots_file_records_that_it_was_derived_and_what_it_assumed(self):
        """A derived file that reads like a hand-authored one is a fact with no provenance.
        The next person to debug this host must be able to tell what a program guessed from
        what an operator declared."""
        self._member_root()
        self._release("v2.0.0", contract=self.STATEFUL)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)

        body = (self._tree() / "reconcile-roots.local").read_text()
        self.assertIn("DERIVED by deploy/runner.py", body)
        self.assertIn("WI-0410", body)
        self.assertIn("THE ASSUMPTION", body)
        self.assertIn(str(self._member_root()), body)

    def test_the_derivation_is_reported_to_a_human_not_only_to_the_log(self):
        """`notes` is what reaches the posted receipt. A program deciding something on an
        operator's behalf, on a machine nobody was watching, says so where somebody reads."""
        self._member_root()
        self._release("v2.0.0", contract=self.STATEFUL)
        notes: list = []
        runner.seed_state(self.system, {"subdir": None}, self.STATEFUL,
                          lambda _m: None, dry_run=False, notes=notes)
        self.assertTrue(any("DERIVED from this machine" in n for n in notes))

    def test_a_real_source_still_wins_over_the_derivation(self):
        """TIER ORDER, asserted rather than assumed. A copy from any real source is
        EVIDENCE about what this system was actually using; the derivation is a reasoned
        default. A derivation that outranked the vault would silently replace a member's
        real walk roots with a guess on every rebuild."""
        self._member_root()
        vault = runner.state_vault(self.system)
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "reconcile-roots.local").write_text("/the/real/roots\n")

        self._release("v2.0.0", contract=self.STATEFUL)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual((self._tree() / "reconcile-roots.local").read_text(),
                         "/the/real/roots\n")

    def test_a_machine_with_no_member_root_still_refuses(self):
        """THE DERIVATION MUST BE ABLE TO FAIL. An empty roots file is syntactically valid
        and semantically a lie — it parses to [], every member reads as UNLOCATED, and the
        deploy that wrote it reports success. Refusing is the honest answer, and since the
        first half of this item that refusal now reaches the ledger."""
        # POGA_DEPLOY_MEMBER_ROOT points at a directory the fixture never creates.
        self.assertFalse(Path(os.environ["POGA_DEPLOY_MEMBER_ROOT"]).exists())
        self._release("v2.0.0", contract=self.STATEFUL)
        with self.assertRaises(runner.DeployError) as cm:
            self._deploy()
        self.assertIn("required state absent after seeding", str(cm.exception))
        self.assertEqual(self._ledger()["refusal"]["attempted"], "v2.0.0")

    def test_repo_paths_is_derived_only_for_the_row_that_is_this_system(self):
        """Guessing another member's location from a machine that may never have cloned it
        writes a confident wrong locator, and `reconcile` reports a mapped path whose
        STATUS.md does not resolve as BROKEN MAP — but only after it has stopped walking
        for that id. An absent row is recoverable; a wrong one is not."""
        self._member_root()
        contract = {**CONTRACT,
                    "state": [{"path": "repo-paths.local", "required": False}]}
        self._release("v2.0.0", contract=contract)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertFalse((self._tree() / "repo-paths.local").exists(),
                         "a non-self row derived a locator for itself")

        self._self_registry()
        # A marker, or the second commit is empty and git refuses it.
        self._release("v3.0.0", contract=contract, marker="v3\n")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        body = (self._tree() / "repo-paths.local").read_text()
        self.assertIn(f"{self.system} = {runner.work_dir(self.system, {'subdir': None})}",
                      body)

    def test_a_dry_run_derives_nothing(self):
        self._member_root()
        self._release("v2.0.0", contract=self.STATEFUL)
        rc, _ = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertFalse((self._tree() / "reconcile-roots.local").exists())

    def test_a_derived_file_is_never_overwritten_by_a_later_deploy(self):
        """The derivation's own header promises this in prose; the promise is the test."""
        self._member_root()
        self._release("v2.0.0", contract=self.STATEFUL)
        self.assertEqual(self._deploy()[0], 0)
        (self._tree() / "reconcile-roots.local").write_text("/edited/by/an/operator\n")

        self._release("v3.0.0", contract=self.STATEFUL, marker="v3\n")
        self.assertEqual(self._deploy()[0], 0)
        self.assertEqual((self._tree() / "reconcile-roots.local").read_text(),
                         "/edited/by/an/operator\n")


if __name__ == "__main__":
    unittest.main()


class TheEvidenceOfARefusalLeavesASealedTree(RefusalCase):
    """WI-0417 — a deploy that refuses on a sealed tree with no production configuration
    still gets its evidence to devbox.

    THE LOOP THIS CLOSES. `production.resolve` answers None until a service configuration
    exists, that configuration is written by the migration, and the migration is the step
    every deploy so far refused before reaching. So `post_result` and
    `diagnose.post_if_changed` fell past the mail-worker branch into `outbox.publish`'s
    detached-checkout refusal, and three deploy failures on the deploy host
    produced no outbound word at all. The evidence channel was gated on the migration whose
    failure it exists to report.

    WHAT IS NOT CHANGED, and it is the negative control the ruling sharpened. `outbox`
    still refuses to commit into a detached checkout — WI-0361's principle that production
    use is explicitly selected and never inferred from a detached HEAD, and its pinning test
    in `tests/test_runner_channel.py`, both stand. The refusal keeps firing where it is
    right; what is new is that the runner hands the files to the channel instead of dropping
    them, on the same route `runner.publish_note` has used for escalations since WI-0360.

    THE FIXTURE IS SEALED FOR REAL rather than only asserted to be. `self.fedrepo` — the
    repo the outbox lives in — is put on a detached HEAD, which is the state
    `is_deploy_tree` actually tests, so `outbox.publish` reaches its real refusal against a
    real detached checkout.

    WHAT IS PATCHED, AND WHY EACH ONE HAS TO BE. Production has ONE sealed tree: the outbox
    is `REPO_ROOT/outbox` and `TheTwoSealedTreeChecksHaveOneSubject` below pins that. The
    fixture cannot have one, because `REPO_ROOT` is how the runner finds
    `deploy/contract.schema.json` and pointing it at the fixture repo breaks every deploy
    before it can refuse. So the fixture has two directories standing in for the one, and
    `is_deploy_tree` is patched to answer for both — the same patch, for the same reason,
    that `test_an_escalation_from_a_sealed_tree_publishes_when_production_is_unset` uses.
    `channel.data_root` points at the repo the outbox is really in, so the published paths
    are the `outbox/...` ones the channel would carry. `channel.publish` is stubbed because
    the real one clones and pushes a branch. Everything between the refusal and that call is
    the shipping code.

    NOTE FOR WHOEVER READS THIS AGAINST PRODUCTION (WI-0415): this is a deploy-path change,
    so it is inert for the release that carries it. The runner deploying tag N is loaded
    from tag N-1, so this first takes effect on the tag AFTER the one it ships in.
    """

    def setUp(self):
        super().setUp()
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        # SEALED AFTER the clean deploy, so the baseline receipt travels the ordinary way
        # and the detachment is the only thing that differs below.
        _git(["checkout", "--detach", "--quiet", "HEAD"], self.fedrepo)
        self.assertTrue(runner.channel.is_deploy_tree(self.fedrepo),
                        "the fixture is not actually sealed, so nothing below is about "
                        "the configuration it claims to be about")
        self.published = []

        def _publish(files, subject, body, origin="", log=None):
            self.published.append((subject, dict(files)))
            return True

        # THE MOCK, NOT THE PATCHER. A test that wanted the attached case first STOPPED
        # this patch, which left `is_deploy_tree(REPO_ROOT)` answering about whatever
        # checkout the suite happens to be running in — attached in a lane and DETACHED in
        # the land gate's isolated worktree, so the test passed here and failed at the gate.
        # A case about an attached tree has to say "attached", not inherit it.
        self.sealed = patch.object(runner.channel, "is_deploy_tree", return_value=True)
        self.is_sealed = self.sealed.start()
        self.addCleanup(self.sealed.stop)
        for p in (patch.object(runner.channel, "data_root", return_value=self.fedrepo),
                  patch.object(runner.channel, "publish", _publish)):
            p.start()
            self.addCleanup(p.stop)

    def _carried(self) -> dict:
        """Every file that reached the channel, path → text."""
        out = {}
        for _, files in self.published:
            out.update(files)
        return out

    def _refuse_v3(self):
        self._release("v3.0.0", contract=self.STATEFUL)
        with self.assertRaises(runner.DeployError):
            self._deploy()

    def test_the_bundle_for_a_refused_deploy_reaches_the_channel(self):
        """THE ACCEPTANCE. A refusal, a sealed tree, no production configuration — and the
        evidence is on a branch that travels, with no human reading the host."""
        self._refuse_v3()
        diagnose.post_if_changed(self.system, log=lambda _: None)

        self.assertTrue(self.published,
                        "the sealed tree published nothing, which is the defect")
        carried = self._carried()
        self.assertTrue([p for p in carried if p.startswith("outbox/")],
                        f"nothing landed under outbox/ — carried {sorted(carried)}")
        blob = "\n".join(carried.values())
        self.assertIn("REFUSED", blob)
        self.assertIn("v3.0.0", blob)

    def test_a_receipt_takes_the_same_route_as_the_bundle(self):
        """Both evidence writers, not just the one the acceptance happens to run through.
        They were put behind one function precisely so neither can acquire the escape
        without the other — that asymmetry is how the outbox came to differ from
        `escalate` in the first place. Driven with a deploy that MOVES the ledger, because
        that is what makes `post_result` post at all."""
        self._release("v2.0.0", marker="v2\n")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertTrue(self.published, "the receipt published nothing")
        self.assertTrue([p for p in self._carried() if p.startswith("outbox/")])

    def test_a_state_gate_refusal_posts_no_receipt_at_all_so_the_bundle_is_the_evidence(self):
        """SAID OUT LOUD BECAUSE THE ACCEPTANCE RESTS ON IT. `post_result` posts on a
        change in `(status, current, attempted)`, and WI-0410 made the refusal record
        deliberately ADDITIVE — it writes a `refusal` key beside those fields and moves
        none of them, so the verdict on the version that is LIVE stays true. A refused
        deploy therefore produces no receipt to carry, and the diagnosis bundle is the
        whole of what travels.

        Written as its own assertion rather than left implicit: the first cut of this class
        asserted a receipt for a refusal and failed, which is the test finding the fact.
        Anyone who later reads "the receipt and the bundle" in the ruling and goes looking
        for a refusal receipt should find this instead of rediscovering it."""
        self._refuse_v3()
        self.assertEqual(self.published, [],
                         "a state-gate refusal posted a receipt, so the comment above is "
                         "stale and the acceptance below is testing the wrong writer")
        diagnose.post_if_changed(self.system, log=lambda _: None)
        self.assertTrue(self.published, "and then nothing carried the refusal either")

    def test_silence_still_means_unchanged(self):
        """A route that publishes on every pass buries the record in itself. The digest
        comparison is upstream of the new code and must stay the thing that decides."""
        diagnose.post_if_changed(self.system, log=lambda _: None)
        self.published.clear()
        diagnose.post_if_changed(self.system, log=lambda _: None)
        self.assertEqual(self.published, [],
                         "an unchanged stable state published anyway")

    def test_a_sealed_tree_with_a_working_mail_worker_still_routes_through_the_worker(self):
        """THE NEGATIVE CONTROL THE RULING NAMED. Where a service configuration exists the
        mail worker is the only writer, and the detached HEAD selects nothing — which is
        WI-0361's principle stated as an assertion rather than as a promise."""
        import mailqueue
        queued = []
        state = self.root / "prod-state"
        config = self.root / "prod-config"
        for d in (state, config):
            d.mkdir(exist_ok=True)
        roots = SimpleNamespace(queue_root=self.fedrepo / "outbox",
                                state_root=state, config_root=config,
                                mailboxes_path=self.mailboxes,
                                state_path=lambda *parts: state.joinpath(*parts))

        def _enqueue(_roots, aid, filename, text, **kw):
            queued.append(filename)
            return Path(filename)

        with patch.object(runner.production, "resolve", return_value=roots), \
             patch.object(mailqueue, "enqueue", _enqueue):
            self._release("v3.0.0", contract=self.STATEFUL)
            with self.assertRaises(runner.DeployError):
                self._deploy()
            diagnose.post_if_changed(self.system, log=lambda _: None)

        self.assertTrue(queued, "the worker was handed nothing")
        self.assertEqual(self.published, [],
                         "a configured machine published around its own mail worker")

    def test_the_outbox_itself_still_refuses_to_commit_into_a_detached_checkout(self):
        """WI-0361's principle, asserted where this change could have broken it. The
        pinning test in tests/test_runner_channel.py is the canonical one; this is the same
        claim standing next to the code that now runs after the refusal."""
        head = _git(["rev-parse", "HEAD"], self.fedrepo).stdout
        self._plant("brief.md", "# brief\n")
        self.assertFalse(outbox.publish("s", "b", root=runner.outbox_dir(),
                                        log=lambda _: None))
        self.assertEqual(head, _git(["rev-parse", "HEAD"], self.fedrepo).stdout,
                         "the outbox committed into a detached checkout")

    def test_an_attached_checkout_publishes_nothing_on_the_channel(self):
        """A lane and the trunk are owned by a session that commits their own files; a
        second writer is what P13 forbids. Asserted against the helper directly, because on
        an attached tree `outbox.publish` succeeds and the helper is never reached."""
        self.is_sealed.return_value = False
        _git(["checkout", "--quiet", "main"], self.fedrepo)
        self.assertFalse(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        self.assertEqual(self.published, [])

    def test_an_unreadable_production_config_publishes_nothing(self):
        """`production.resolve` RAISES on a selector that is set and wrong — it answers
        None only when nothing selected production at all. A publish that treated the raise
        as "not configured" would publish around a mail worker it failed to look at."""
        with patch.object(runner.production, "resolve",
                          side_effect=runner.production.ConfigurationError("bad config")):
            self.assertFalse(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        self.assertEqual(self.published, [])

    def _plant(self, name, data):
        box = runner.outbox_dir() / "to-widget-arch"
        box.mkdir(parents=True, exist_ok=True)
        p = box / name
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data)
        return p

    def test_a_file_that_cannot_be_decoded_is_skipped_and_the_rest_travels(self):
        """UnicodeDecodeError IS a ValueError. Two separate faults live in this handler and
        a fixture that drives only one of them passes against a handler naming only one."""
        self._plant("bad.md", b"\xff\xfe not utf-8")
        self._plant("good.md", "# the bundle\n")
        self.assertTrue(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        carried = self._carried()
        self.assertIn("outbox/to-widget-arch/good.md", carried)
        self.assertNotIn("outbox/to-widget-arch/bad.md", carried)

    def test_a_file_that_cannot_be_read_is_skipped_and_the_rest_travels(self):
        """The OSError half, which the bytes fixture above does NOT cover: a file deleted
        between the walk and the read, or one this process cannot open. Without it a real
        I/O failure escapes a function whose whole contract is to swallow."""
        if os.geteuid() == 0:
            self.skipTest("root can read a 0o000 file, so this drives nothing")
        locked = self._plant("locked.md", "# locked\n")
        locked.chmod(0o000)
        self._plant("good.md", "# the bundle\n")
        self.assertTrue(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        carried = self._carried()
        self.assertIn("outbox/to-widget-arch/good.md", carried)
        self.assertNotIn("outbox/to-widget-arch/locked.md", carried)

    def test_the_walk_carries_files_only_and_says_nothing_about_the_rest(self):
        """A LOG LINE THAT CRIES WOLF EVERY TEN MINUTES IS HOW A GUARD GETS SWITCHED OFF.

        `rglob` yields the mailbox directories too, and `read_text` on a directory raises
        IsADirectoryError — an OSError, so the handler catches it and the PUBLISHED SET is
        identical either way. What differs is that every sweep would report each mailbox as
        a file it could not prepare, which is a failure message about correct behaviour.
        The mutation that drops `is_file()` survives an assertion about what was carried and
        dies only against one about what was said."""
        self._plant("good.md", "# the bundle\n")
        said = []
        self.assertTrue(runner._publish_outbox_on_channel("s", "b", said.append))
        self.assertTrue([d for d in runner.outbox_dir().iterdir() if d.is_dir()],
                        "no directory in the box, so this asserts nothing")
        self.assertEqual([line for line in said if "could not prepare" in line], [],
                         "the walk reported a failure about something that is not a file")

    def test_a_dotfile_is_not_carried(self):
        """The box's `.keep` exists to make an empty directory tracked; it is not mail, and
        the channel is a permanent record of what was sent."""
        self._plant("good.md", "# the bundle\n")
        self.assertTrue((runner.outbox_dir() / ".keep").is_file(),
                        "no dotfile in the box, so this asserts nothing")
        self.assertTrue(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        self.assertNotIn("outbox/.keep", self._carried())

    def test_an_attached_checkout_still_commits_the_receipt_into_its_own_repo(self):
        """THE PATH EVERY DEVELOPMENT MACHINE TAKES, and the one a fallback is most likely
        to quietly replace. `publish_outbox` must try the outbox FIRST: skipping straight to
        the channel would leave the ordinary case with no local commit at all, and on an
        attached tree the channel declines — so the receipt would stop travelling by the
        route it has always travelled by, on every machine that is not the deploy host."""
        self.is_sealed.return_value = False
        _git(["checkout", "--quiet", "main"], self.fedrepo)
        before = _git(["rev-parse", "HEAD"], self.fedrepo).stdout
        self._plant("brief.md", "# brief\n")

        self.assertTrue(runner.publish_outbox("s", "b", lambda _: None))
        self.assertNotEqual(before, _git(["rev-parse", "HEAD"], self.fedrepo).stdout,
                            "the outbox was bypassed, so nothing was committed locally")
        self.assertEqual(self.published, [],
                         "an attached checkout wrote the one-writer channel")

    def test_an_empty_outbox_publishes_nothing(self):
        """A publish of nothing is a commit of nothing, and `channel.publish` would report
        success for it — which is how a route that carries no files starts reading as one
        that works."""
        for p in sorted(runner.outbox_dir().rglob("*")):
            if p.is_file():
                p.unlink()
        self.assertFalse(runner._publish_outbox_on_channel("s", "b", lambda _: None))
        self.assertEqual(self.published, [])


class TheTwoSealedTreeChecksHaveOneSubject(unittest.TestCase):
    """`outbox.publish` tests the outbox's own repo and `_publish_outbox_on_channel` tests
    `REPO_ROOT`. Those are the same directory wherever the fallback can run — the fallback
    is reachable only when production is unset, and with production unset `outbox_dir()` is
    `REPO_ROOT/outbox`. Pinned here because if they ever diverge the runner would answer a
    question about one tree with a fact about another, and nothing else would notice."""

    def test_the_outbox_lives_in_the_repo_root_when_production_is_unset(self):
        saved = {k: os.environ.get(k)
                 for k in ("POGA_DEPLOY_OUTBOX_DIR", "POGA_FEDERATION_CONFIG")}
        for k in saved:
            os.environ.pop(k, None)
        try:
            self.assertEqual(runner.outbox_dir().parent, runner.REPO_ROOT)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
