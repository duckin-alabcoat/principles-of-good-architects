"""Tests for the Runner-side deploy runner (ADR-0103).

These build a REAL git repo with REAL tags and let the runner clone, fetch, check out and
roll back against it. Nothing about git is mocked, because every interesting bug in this
program lives in the seam between what git actually does and what the code assumed it
does — and a mock can only ever assert the assumption back.

What is deliberately NOT exercised: `launchctl`. Every contract here declares no units or
`restart: none`, so the suite never touches this machine's running services. The restart
step is the one part that must be proved in a real cutover instead
([`verify-in-the-created-configuration`] — a test that could restart production would be
a worse bug than the one it was written to catch).
"""

from __future__ import annotations

import ast
import io
import json
import os
import plistlib
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import runner  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures
from macos_only import requires_macos  # noqa: E402  WI-0468


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


#: Env names a fixture must REMOVE rather than redirect, with the reason (WI-0361).
#: One owner, like `TestEveryProductionPathIsRedirected.READ_ONLY`: every fixture in the
#: deploy suites reads this, and both guards count a name here as neutralised. Copying it
#: per fixture is how the two would disagree on the first name added.
CLEARED_ENV = {
    "POGA_FEDERATION_CONFIG":
        "not a directory a fixture can substitute: it names the operator's whole service "
        "configuration — queue, config root and transport at once — and the runner reads "
        "it through `curate/production.py` on every `production.resolve(REPO_ROOT)`. Its "
        "ABSENCE is what keeps these tests on the development path, so the fixture deletes "
        "it for the duration of the test and puts it back afterwards.",
    "XPC_SERVICE_NAME":
        "not a path: launchd's job label, which `running_under_unit()` reads to answer "
        "'which unit am I?'. launchd sets it for every process it starts, so the nightly "
        "serial run (com.federation.gate-inputs) handed it to every deploy test, which then "
        "took the under-a-unit path — `refuse_unsealed_process_root` refused the lane as "
        "an unsealed tree. 16 F / 56 E on 2026-09-27, none reproducible from a shell, "
        "where it is unset (WI-0432). A test that wants a unit sets it with patch.dict.",
}


CONTRACT = {
    "contract_version": 1,
    "system": "widget",
    "units": [],
    "restart": "none",
    "deps": {"kind": "none"},
    "smoke": {"cmd": ["/bin/sh", "-c", "exit 0"]},
    "verify": {"cmd": ["/bin/sh", "-c", "exit 0"], "settle_seconds": 0},
}


class DeployRunnerCase(unittest.TestCase):
    """Base fixture: an upstream repo the runner can clone, plus redirected paths so the
    suite can never write into the federation's own repo or the real ledger."""

    system = "widget"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        _git(["init", "-b", "main"], self.upstream)
        (self.upstream / "deploy").mkdir()
        self._write_contract(CONTRACT)
        (self.upstream / "marker.txt").write_text("v1\n")
        _git(["add", "-A"], self.upstream)
        _git(["commit", "-m", "first"], self.upstream)
        _git(["tag", "v1.0.0"], self.upstream)

        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({
            "contract_version": 1,
            "systems": {self.system: {"remote": str(self.upstream), "subdir": None}},
        }))

        # The fixture's own federation repo. The runner posts its result into `outbox/`
        # and COMMITS it, so this has to be a real git repo and it has to be THIS one —
        # left unredirected, the suite queues fixture receipts in the federation's own
        # outbox and commits them there, which is not a hypothetical: it happened on the
        # first run of this feature, eleven commits deep, before anything asserted
        # anything. `TestEveryProductionPathIsRedirected` is what stops the next path
        # repeating it.
        self.fedrepo = self.root / "fed"
        (self.fedrepo / "outbox").mkdir(parents=True)
        _git(["init", "-b", "main"], self.fedrepo)
        (self.fedrepo / "outbox" / ".keep").write_text("")
        _git(["add", "-A"], self.fedrepo)
        _git(["commit", "-m", "fed"], self.fedrepo)
        self.mailboxes = self.root / "mailboxes.json"
        self.mailboxes.write_text(json.dumps({"members": {
            self.system: {"architect_id": f"{self.system}-arch"}}}))

        self._env = {
            "POGA_DEPLOY_REGISTRY": str(self.registry),
            "POGA_DEPLOY_ROOT": str(self.root / "deploy-trees"),
            "POGA_DEPLOY_LEDGER_DIR": str(self.root / "ledger"),
            "POGA_DEPLOY_COMMS_DIR": str(self.root / "comms"),
            "POGA_DEPLOY_OUTBOX_DIR": str(self.fedrepo / "outbox"),
            "POGA_DEPLOY_MAILBOXES": str(self.mailboxes),
            # A LIVE SYMLINK TO THE OPERATOR'S OWN COMMAND, which makes it the most
            # dangerous path this program has (WI-0360). The real value is
            # ~/.local/bin/poga; a cutover that reached it would repoint the `poga` every
            # session on this machine runs through, and the test would pass.
            "POGA_CLI_LINK": str(self.root / "bin" / "poga"),
            # The runner resolves `outbox_dir()` and `comms_dir()` through
            # `channel.data_root`, so the channel's own root is a path THIS program writes
            # even though it is spelled in another module.
            "POGA_CHANNEL_ROOT": str(self.root / "federation-channel"),
            # An auto-cutover INSTALLS a launchd job. Redirected for the reason the base
            # fixture redirects everything else, only more so: the real value is
            # ~/Library/LaunchAgents, and a suite that reached it would install jobs on
            # whatever machine ran the tests.
            "POGA_DEPLOY_LAUNCH_AGENTS": str(self.root / "LaunchAgents"),
            # WI-0410. Deliberately a path that does NOT exist: `_derive_reconcile_roots`
            # answers None for a missing root, so every pre-existing test keeps the
            # behaviour it was written against (no derivation, the required-state refusal
            # still fires). A test that wants the derivation creates this directory.
            "POGA_DEPLOY_MEMBER_ROOT": str(self.root / "member-repos"),
        }
        # Cleared, not redirected — see CLEARED_ENV for which names and why.
        self._cleared = tuple(CLEARED_ENV)
        self._saved = {k: os.environ.get(k) for k in (*self._env, *self._cleared)}
        os.environ.update(self._env)
        for k in self._cleared:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------------

    def _write_contract(self, contract, **overrides):
        merged = {**contract, **overrides}
        (self.upstream / "deploy" / "deploy.json").write_text(
            json.dumps(merged, indent=2))

    def _release(self, tag, contract=None, marker=None):
        """Cut a new upstream release: optionally a new contract and marker, then tag."""
        if contract is not None:
            self._write_contract(contract)
        if marker is not None:
            (self.upstream / "marker.txt").write_text(marker)
        _git(["add", "-A"], self.upstream)
        _git(["commit", "-m", tag], self.upstream)
        _git(["tag", tag], self.upstream)

    def _deploy(self, **kw):
        """Run a deploy NOISILY but capture it, so assertions can be made about what the
        operator would actually have been told. `quiet=True` would silence the very
        sentences several of these tests exist to pin."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.deploy(self.system, **kw)
        return rc, buf.getvalue()

    def _ledger(self):
        return runner.read_ledger(self.system)

    def _tree(self):
        return runner.deploy_tree(self.system)

    def _queued(self):
        """[(architect_id, Path)] posted into the fixture's outbox by this run."""
        return sorted(
            (d.name[len("to-"):], f)
            for d in (self.fedrepo / "outbox").glob("to-*") if d.is_dir()
            for f in d.iterdir() if f.is_file() and not f.name.startswith("."))

    def _fed_log(self):
        return subprocess.run(["git", "log", "--format=%s"], cwd=str(self.fedrepo),
                              capture_output=True, text=True, check=True).stdout.split("\n")


class TestTagResolution(DeployRunnerCase):

    def test_newest_tag_is_numeric_not_lexical(self):
        """v0.10.0 must beat v0.9.0. A string sort gets this backwards and the bug does
        not appear until a project's tenth release, by which point the runner is trusted.

        Asserted against `newest_tag` directly on a repo carrying ONLY the ambiguous tags:
        going through a deploy would let the fixture's own `v1.0.0` win on merit and the
        test would pass whether or not the comparison is numeric."""
        repo = self.root / "tenth"
        repo.mkdir()
        _git(["init", "-b", "main"], repo)
        (repo / "f").write_text("x")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)
        for tag in ("v0.9.0", "v0.10.0", "v0.2.0"):
            _git(["tag", tag], repo)
        self.assertEqual(runner.newest_tag(repo), "v0.10.0")

    def test_a_fresh_clone_is_not_reported_as_already_deployed(self):
        """The bug this suite caught before it shipped: a new clone's HEAD sits on the
        default branch, which may already carry the newest tag. Reading that as the
        deployed version made a first deploy a silent no-op over an empty directory."""
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertNotIn("already running", out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertTrue((self._tree() / "marker.txt").exists(),
                        "a deploy must actually materialize the tree")

    def test_untagged_repo_is_refused(self):
        """The whole gate: if it isn't tagged, it isn't deployable. A branch tip must be
        refused with a reason, never quietly deployed."""
        bare = self.root / "untagged"
        bare.mkdir()
        _git(["init", "-b", "main"], bare)
        (bare / "f").write_text("x")
        _git(["add", "-A"], bare)
        _git(["commit", "-m", "no tags"], bare)
        self.registry.write_text(json.dumps({
            "contract_version": 1,
            "systems": {"untagged": {"remote": str(bare), "subdir": None}}}))
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy("untagged", quiet=True)
        self.assertIn("no semver tag", str(cm.exception))

    def test_unregistered_system_reads_as_absent_not_current(self):
        """'I have no contract for this' and 'this is up to date' must never be the same
        output ([`declare-what-a-check-assumes`])."""
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy("nobody", quiet=True)
        msg = str(cm.exception)
        self.assertIn("not a deployable system", msg)
        self.assertIn("NOT the same as", msg)


class TestContractSource(DeployRunnerCase):

    def test_contract_is_read_from_the_tag_not_the_working_tree(self):
        """The contract and the code it describes must be the same object. Reading it off
        disk would mean a deploy is configured by whatever a previous, failed deploy left
        behind."""
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        # Poison the checked-out working tree with a contract that would fail hard.
        tree = self._tree()
        (tree / "deploy" / "deploy.json").write_text(json.dumps(
            {"contract_version": 99, "system": "somebody-else"}))
        got = runner.read_contract(self.system, {"subdir": None}, "v1.0.0")
        self.assertEqual(got["contract_version"], 1)
        self.assertEqual(got["system"], self.system)

    def test_unknown_contract_version_is_refused_not_guessed(self):
        self._release("v2.0.0", contract={**CONTRACT, "contract_version": 99})
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy(self.system, quiet=True)
        self.assertIn("Refusing rather than guessing", str(cm.exception))

    def test_contract_naming_another_system_is_refused(self):
        self._release("v2.0.0", contract={**CONTRACT, "system": "not-widget"})
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy(self.system, quiet=True)
        self.assertIn("names system", str(cm.exception))

    def test_tag_without_a_contract_is_refused(self):
        bare = self.root / "nocontract"
        bare.mkdir()
        _git(["init", "-b", "main"], bare)
        (bare / "f").write_text("x")
        _git(["add", "-A"], bare)
        _git(["commit", "-m", "c"], bare)
        _git(["tag", "v1.0.0"], bare)
        self.registry.write_text(json.dumps({
            "contract_version": 1,
            "systems": {"nocontract": {"remote": str(bare), "subdir": None}}}))
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy("nocontract", quiet=True)
        self.assertIn("carries no deploy contract", str(cm.exception))


class TestContractValidation(DeployRunnerCase):

    def test_press_box_strings_are_refused_before_seeding_or_tree_change(self):
        self.system = "example-app"
        self.registry.write_text(json.dumps({"systems": {
            self.system: {"remote": str(self.upstream), "subdir": None}}}))
        valid = {**CONTRACT, "system": self.system}
        self._release("v2.0.0", contract=valid)
        self.assertEqual(self._deploy()[0], 0)
        before = _git(["rev-parse", "HEAD"], self._tree()).stdout
        # `state: [42]`, not `["cache"]`: the bare string is now a VALID shape, kept
        # because a member's already-cut tag seals it (WI-0358). This test's subject is
        # that an invalid contract is refused BEFORE seeding or any tree change, so it
        # needs a contract that is still invalid — the shape is incidental to it.
        self._release("v3.0.0", contract={**valid, "state": [42]},
                      marker="must not deploy\n")
        with patch.object(runner, "seed_state", wraps=runner.seed_state) as seed:
            with self.assertRaises(runner.DeployError) as cm:
                self._deploy()
            seed.assert_not_called()
        message = ('example-app: contract state[0]: expected string or object with "path", '
                   'found integer 42')
        self.assertEqual(str(cm.exception), message)
        self.assertEqual(self._ledger()["status"], "contract-invalid")
        self.assertEqual(self._ledger()["message"], message)
        self.assertEqual(self._ledger()["current"], "v2.0.0")
        self.assertEqual(_git(["rev-parse", "HEAD"], self._tree()).stdout, before)
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")

    def test_valid_contract_passes_unchanged(self):
        valid = {**CONTRACT, "state": [{"path": "cache", "required": False}]}
        self._release("v2.0.0", contract=valid)
        entry = runner.registry_entry(self.system)
        runner.ensure_tree(self.system, entry, lambda _: None)
        self.assertEqual(runner.read_contract(self.system, entry, "v2.0.0"), valid)

    def test_unknown_top_level_key_is_refused(self):
        self._release("v2.0.0", contract={**CONTRACT, "surprise": True})
        with self.assertRaisesRegex(runner.DeployError,
                                    r"widget: contract surprise:.*additional properties"):
            self._deploy()

    def test_comment_key_is_permitted_at_the_top_level(self):
        """A member's v1.0.0 was REFUSED by the runner for exactly this.

        Its tagged contract carries a `//` documentation key, the house convention
        used across session.config.json, dev-environments.json and registry.json. The
        contract is read FROM THE TAG, so the key cannot be edited out after release --
        a schema forbidding it strands the release rather than correcting it.
        """
        documented = {**CONTRACT, "//": "THE DEPLOY CONTRACT for widget."}
        self._release("v2.0.0", contract=documented)
        entry = runner.registry_entry(self.system)
        runner.ensure_tree(self.system, entry, lambda _: None)
        self.assertEqual(runner.read_contract(self.system, entry, "v2.0.0"), documented)

    def test_comment_key_is_permitted_at_a_nested_object(self):
        """The fix is applied at every strict object, not only where it was observed.

        That member failed at the top level; a `//` inside `deps` or a `state` entry would
        have failed the same way, in a contract equally sealed inside a tag.
        """
        nested = {**CONTRACT,
                  "deps": {"kind": "none", "//": "stdlib only"},
                  "state": [{"path": "cache", "required": False, "//": "regenerable"}]}
        self._release("v2.0.0", contract=nested)
        entry = runner.registry_entry(self.system)
        runner.ensure_tree(self.system, entry, lambda _: None)
        self.assertEqual(runner.read_contract(self.system, entry, "v2.0.0"), nested)

    def test_a_non_comment_unknown_key_is_still_refused_after_the_comment_exemption(self):
        """The negative control for the two tests above.

        Permitting `//` must not degrade into permitting anything: a key that merely
        CONTAINS a slash, or sits beside a comment key, is still additional and still
        refused. Without this, the exemption would read as a fix while having removed
        the guard it was scoped inside.
        """
        self._release("v2.0.0", contract={**CONTRACT,
                                          "//": "documented, and allowed",
                                          "not//a//comment": True})
        with self.assertRaisesRegex(
                runner.DeployError,
                r"widget: contract not//a//comment:.*additional properties"):
            self._deploy()

    def test_sweep_continues_after_invalid_contract_and_returns_one(self):
        self._release("v2.0.0", contract={**CONTRACT, "system": "invalid",
                                          "state": [42]})
        self.registry.write_text(json.dumps({"systems": {
            "invalid": {"remote": str(self.upstream), "subdir": None},
            "widget": {"remote": str(self.upstream), "subdir": None}}}))
        # The later system selects the valid tag; both execute the real deploy path.
        deploy = runner.deploy
        def selected(system, **kwargs):
            return deploy(system, version="v1.0.0" if system == "widget" else None,
                          **kwargs)
        out = io.StringIO()
        with patch.object(runner, "deploy", side_effect=selected), redirect_stdout(out):
            self.assertEqual(runner.sweep(), 1)
        self.assertIn("invalid: REFUSED", out.getvalue())
        self.assertIn("contract state[0]", out.getvalue())
        self.assertEqual(runner.read_ledger("invalid")["status"], "contract-invalid")
        self.assertEqual(self._ledger()["status"], "deployed")

    def test_missing_required_field_is_refused(self):
        invalid = {k: v for k, v in CONTRACT.items() if k != "system"}
        self._release("v2.0.0", contract=invalid)
        with self.assertRaisesRegex(runner.DeployError,
                                    r"widget: contract system:.*required.*found missing"):
            self._deploy()


class TestStateSeeding(DeployRunnerCase):

    def _seeds(self, mapping):
        """Machine-local seed sources — the only place an absolute path is allowed to
        live. Never the system's tracked contract, which has to stay true everywhere."""
        f = self.root / "seeds.json"
        f.write_text(json.dumps({"systems": {self.system: mapping}}))
        os.environ["POGA_DEPLOY_SEEDS"] = str(f)
        self.addCleanup(os.environ.pop, "POGA_DEPLOY_SEEDS", None)

    def _with_state(self):
        src = self.root / "devclone-state.json"
        src.write_text('{"accumulated": true}')
        self._seeds({"state.json": str(src)})
        contract = {**CONTRACT, "state": [{"path": "state.json", "required": True}]}
        self._release("v2.0.0", contract=contract)
        return src

    def test_state_is_seeded_by_copy_on_first_deploy(self):
        src = self._with_state()
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual((self._tree() / "state.json").read_text(),
                         '{"accumulated": true}')
        # COPY, never move: the developer clone keeps its fallback.
        self.assertTrue(src.exists())

    def test_second_deploy_does_not_overwrite_accumulated_state(self):
        """State survives deploys because a tag checkout does not touch ignored files —
        and because seeding refuses to overwrite. Losing a watcher's accumulated state on
        every deploy would be silent and unrecoverable."""
        self._with_state()
        self._deploy()
        (self._tree() / "state.json").write_text('{"accumulated": "MUCH LATER"}')
        self._release("v3.0.0", marker="v3")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual((self._tree() / "state.json").read_text(),
                         '{"accumulated": "MUCH LATER"}')

    def test_required_state_that_cannot_be_seeded_fails_before_any_restart(self):
        self._seeds({"state.json": str(self.root / "does-not-exist")})
        contract = {**CONTRACT, "state": [{"path": "state.json", "required": True}]}
        self._release("v2.0.0", contract=contract)
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy(self.system, quiet=True)
        self.assertIn("required state absent", str(cm.exception))


class TestFailurePaths(DeployRunnerCase):

    def test_smoke_failure_restores_the_tree_and_restarts_nothing(self):
        """The cheap failure. Nothing was restarted, so the previously running version was
        never interrupted — and the tree goes back so the next deploy starts from a known
        place."""
        self._deploy()                     # v1.0.0 is live
        self._release("v2.0.0", contract={**CONTRACT,
                                          "smoke": {"cmd": ["/bin/sh", "-c",
                                                            "echo broken >&2; exit 3"]}},
                      marker="v2")
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        led = self._ledger()
        self.assertEqual(led["status"], "smoke-failed")
        self.assertEqual(led["attempted"], "v2.0.0")
        self.assertEqual(led["current"], "v1.0.0")

    def test_smoke_failure_escalates_to_a_comms_note(self):
        self._deploy()
        self._release("v2.0.0", contract={**CONTRACT,
                                          "smoke": {"cmd": ["/bin/sh", "-c", "exit 1"]}})
        self._deploy()
        notes = list((self.root / "comms").glob("*-deploy-widget-failed.md"))
        self.assertEqual(len(notes), 1, "a failed deploy must be loud on its own")
        self.assertIn("no unit was", notes[0].read_text().lower())

    def test_verify_failure_rolls_back_and_confirms_the_old_version(self):
        """The expensive failure: production was already switched. Rolling back is not
        enough — the runner must prove the version it rolled back TO is healthy before it
        reports anything reassuring."""
        self._deploy()
        self._release("v2.0.0", contract={**CONTRACT,
                                          "verify": {"cmd": ["/bin/sh", "-c", "exit 1"],
                                                     "settle_seconds": 0}},
                      marker="v2")
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        self.assertIn("ROLLED BACK", out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        led = self._ledger()
        self.assertEqual(led["status"], "rolled-back")
        self.assertEqual(led["current"], "v1.0.0")
        self.assertTrue(led["rollback_verify"]["ok"],
                        "the rollback's own verification must be recorded, not assumed")

    def test_verify_failure_with_no_previous_version_says_so_plainly(self):
        """A first deploy has no 'back'. The runner must not claim a rollback it cannot
        perform, and must not pretend the unverified version is fine."""
        self._release("v2.0.0", contract={**CONTRACT,
                                          "verify": {"cmd": ["/bin/sh", "-c", "exit 1"],
                                                     "settle_seconds": 0}})
        rc, out = self._deploy(version="v2.0.0")
        self.assertEqual(rc, 1)
        self.assertIn("no previous version", out)
        self.assertEqual(self._ledger()["status"], "verify-failed-no-rollback")


class TestGatesAndRollback(DeployRunnerCase):

    def test_undeclared_gate_is_recorded_as_unchecked_not_as_passed(self):
        """A system with no smoke check has not passed its smoke check."""
        contract = {k: v for k, v in CONTRACT.items() if k != "smoke"}
        self._release("v2.0.0", contract=contract)
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["smoke"], {"declared": False})

    def test_rollback_verb_returns_to_the_ledger_previous(self):
        self._deploy()                     # v1.0.0
        self._release("v2.0.0", marker="v2")
        self._deploy()                     # v2.0.0
        self.assertEqual(self._ledger()["current"], "v2.0.0")
        rc, out = self._deploy(rollback=True)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")

    def test_rollback_with_no_history_refuses(self):
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy(self.system, rollback=True, quiet=True)
        self.assertIn("nothing to roll back to", str(cm.exception))

    def test_redeploying_the_same_tag_is_a_no_op(self):
        self._deploy()
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("already running", out)

    def test_dry_run_changes_nothing(self):
        self._deploy()
        self._release("v2.0.0", marker="v2")
        rc, out = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual(self._ledger().get("current"), "v1.0.0")


class TestRollbackRestoresProduction(DeployRunnerCase):
    """A rollback has to put PRODUCTION back, not only the files.

    Two contract shapes reach production by different verbs. A units-based system gets
    there by restarting launchd units; an apply-based system (ADR-0103 D8 shape 2) has no
    units at all and gets there by running an `apply` command that installs the tag
    somewhere else entirely — another host, a cloud project. The forward path already
    picks between them at the `run_apply` / `restart_units` fork. The rollback path has to
    pick the same way: a rollback that only restarts units puts an apply-based system's
    TREE back while the machine that actually serves it keeps running the bad tag, the
    previous tag's verify then fails against that host, and the run ends `RollbackFailed`
    — a correct ledger and a correct escalation sitting on top of a restoration that never
    happened.

    Both halves below run the same failure (verify fails on the new tag) against the two
    shapes and demand the same thing of each: the previous version is genuinely back.

    No unit is ever declared here, for the reason the module docstring gives — this suite
    does not touch `launchctl`. That costs nothing, because the two branches are told
    apart by which verb the rollback CALLS, and an empty unit list still proves the runner
    chose `restart_units` and ran no apply.
    """

    def setUp(self):
        super().setUp()
        # Absolute, and OUTSIDE the deploy tree: a checkout must not be able to sweep away
        # the evidence of what actually ran.
        self.applied = self.root / "applied.log"

    def _apply_contract(self, label, verify_rc=0):
        """An apply-strategy contract whose apply leaves a trace naming its own tag, so
        the log reads back as the ordered history of what was really installed."""
        return {**CONTRACT,
                "units": [],
                "restart": "none",
                "apply": {"cmd": ["/bin/sh", "-c",
                                  "echo {} >> '{}'".format(label, self.applied)],
                          "timeout_seconds": 60},
                "verify": {"cmd": ["/bin/sh", "-c", "exit {}".format(verify_rc)],
                           "settle_seconds": 0}}

    def _applied(self):
        if not self.applied.exists():
            return []
        return self.applied.read_text().split()

    def test_apply_based_rollback_re_applies_the_previous_contract(self):
        """The defect this class exists for. `v3` installs, fails verification, and the
        rollback must RE-INSTALL `v2` — not merely check its files out."""
        self._release("v2.0.0", contract=self._apply_contract("v2"), marker="v2")
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual(self._applied(), ["v2"])

        self._release("v3.0.0", contract=self._apply_contract("v3", verify_rc=1),
                      marker="v3")
        rc, out = self._deploy()

        self.assertEqual(rc, 1)
        self.assertIn("ROLLED BACK", out)
        self.assertEqual(
            self._applied(), ["v2", "v3", "v2"],
            "the rollback must re-invoke apply with the PREVIOUS tag's contract; without "
            "that trailing 'v2' the deploy tree says v2 while the host it installs to is "
            "still running v3")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v2")
        led = self._ledger()
        self.assertEqual(led["status"], "rolled-back")
        self.assertEqual(led["current"], "v2.0.0")
        self.assertTrue(led["rollback_apply"]["ok"],
                        "the restoring apply is a step that can fail, so it is recorded "
                        "rather than assumed")
        self.assertTrue(led["rollback_verify"]["ok"])

    def test_units_based_rollback_restarts_units_and_applies_nothing(self):
        """The other shape, unchanged. No apply is declared, so the rollback goes through
        the units verb and must not invent one."""
        self._deploy()                     # v1.0.0, units-based
        self._release("v2.0.0", contract={**CONTRACT,
                                          "verify": {"cmd": ["/bin/sh", "-c", "exit 1"],
                                                     "settle_seconds": 0}},
                      marker="v2")
        rc, out = self._deploy()

        self.assertEqual(rc, 1)
        self.assertIn("ROLLED BACK", out)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        led = self._ledger()
        self.assertEqual(led["status"], "rolled-back")
        self.assertEqual(led["current"], "v1.0.0")
        self.assertTrue(led["rollback_verify"]["ok"])
        self.assertFalse(led["rollback_apply"]["declared"],
                         "a units-based contract declares no apply, and the rollback must "
                         "not invent one")
        self.assertEqual(self._applied(), [],
                         "nothing may be applied for a contract that declares no apply")
        self.assertGreaterEqual(
            out.count("restart: none"), 2,
            "the units verb runs on the forward path AND again on the rollback path")


class TestCutover(DeployRunnerCase):
    """A tree can be fully deployed and honestly not yet live. `unit_target` is stubbed
    here rather than driven through launchctl: a test that could restart this machine's
    real services would be a worse bug than any it could catch."""

    def setUp(self):
        super().setUp()
        self._real_unit_target = runner.unit_target

    def tearDown(self):
        runner.unit_target = self._real_unit_target
        super().tearDown()

    def _contract_with_unit(self):
        return {**CONTRACT, "units": ["com.example.thing"], "restart": "kickstart"}

    def test_a_redeploy_after_the_operator_cuts_over_confirms_it(self):
        """The runner must be able to observe its own last step.

        A deploy that ends at `awaiting-cutover` leaves the unit repoint to the OPERATOR,
        and that happens outside this program. The idempotence check tested only whether
        the TREE holds the target, so once a system reached awaiting-cutover it stayed
        there forever: the ledger kept its frozen snapshot, `--status` replayed it, and
        `verify` — skipped at deploy time because nothing was running the new code yet —
        could never run at all. The system is live and every surface says it is not.

        This also unblocks ADR-0103 D9, which refuses to install the sweep until the
        ledger holds evidence of successful deploys: no deploy of a unit-bearing system
        could reach a success status, so the nightly path was unreachable by construction.
        """
        runner.unit_target = lambda label: "/Users/someone/Projects/Thing"
        self._release("v2.0.0", contract=self._contract_with_unit())
        self._deploy()
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")

        # Still not cut over: a redeploy must NOT say "nothing to do". It names the unit
        # and where it actually runs, and fails.
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        self.assertIn("NOT cut over", out)
        self.assertIn("/Users/someone/Projects/Thing", out)
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")

        # The operator repoints the unit at the deploy tree. The next redeploy is the
        # first moment the runner can see it.
        runner.unit_target = lambda label: str(self._tree())
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("CUTOVER CONFIRMED", out)
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertTrue(self._ledger()["cutover"]["cut_over"])

    def test_a_unit_pointing_elsewhere_is_deployed_but_not_restarted(self):
        """The pre-cutover state, and the one this whole check exists for: restarting a
        unit that runs from the developer's clone would restart code this deploy never
        placed, and then call the result live."""
        runner.unit_target = lambda label: "/Users/someone/Projects/Thing"
        self._release("v2.0.0", contract=self._contract_with_unit())
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("AWAITING CUTOVER", out)
        led = self._ledger()
        self.assertEqual(led["status"], "awaiting-cutover")
        self.assertEqual(led["current"], "v2.0.0")
        self.assertEqual(led["cutover"]["pending"][0]["runs_in"],
                         "/Users/someone/Projects/Thing")
        self.assertIn("skipped", led["verify"],
                      "verification of a unit that was never restarted means nothing")

    def test_a_dry_run_in_the_pre_cutover_state_writes_no_ledger(self):
        """The awaiting-cutover branch returns early, so it does not reach the shared
        dry-run exit and has to guard itself. It shipped without that guard and wrote a
        real ledger entry on the very first rehearsal against a member."""
        runner.unit_target = lambda label: "/Users/someone/Projects/Thing"
        self._release("v2.0.0", contract=self._contract_with_unit())
        rc, out = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("dry-run complete", out)
        self.assertNotIn("DEPLOYED", out)
        self.assertEqual(self._ledger(), {}, "a dry run must leave no ledger behind")

    def test_an_unloaded_unit_is_not_treated_as_cut_over(self):
        """Not loaded is UNKNOWN, not 'fine' — the third state that must never collapse
        into one of the other two."""
        runner.unit_target = lambda label: None
        self._release("v2.0.0", contract=self._contract_with_unit())
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        led = self._ledger()
        self.assertEqual(led["status"], "awaiting-cutover")
        self.assertEqual(led["cutover"]["unloaded"], ["com.example.thing"])

    def test_a_cut_over_unit_proceeds_to_restart_and_verify(self):
        runner.unit_target = lambda label: str(runner.work_dir(self.system,
                                                               {"subdir": None}))
        calls = []
        real_run = runner.run

        def fake_run(cmd, **kw):
            if cmd and cmd[0] == "launchctl":
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kw)

        runner.run = fake_run
        try:
            self._release("v2.0.0", contract=self._contract_with_unit())
            rc, out = self._deploy()
        finally:
            runner.run = real_run
        self.assertEqual(rc, 0)
        self.assertIn("DEPLOYED v2.0.0", out)
        self.assertTrue(any("kickstart" in " ".join(c) for c in calls),
                        "a cut-over unit must actually be restarted")
        self.assertEqual(self._ledger()["status"], "deployed")


class AutoCutoverCase(DeployRunnerCase):
    """Fixture for the automatic first cutover (WI-0350).

    `launchctl` is still never invoked for real — the module docstring's rule stands —
    but the fake here is a MODEL of launchd rather than a stub of the answer. It keeps a
    loaded-units table that `bootout` and `bootstrap` actually mutate, reads the plist
    that `bootstrap` is pointed at to decide what the unit now runs in, and makes
    `kickstart` FAIL on a unit that is not loaded, exactly as the real one does. Two
    things follow that a stub could not give:

      * `unit_target` is answered from that table, so every assertion that a system
        became live is a re-measurement through `cutover_state` — the same question the
        runner asks — rather than a canned reply;
      * the reason this feature cannot be built out of `restart_units` is reproduced
        rather than asserted: `kickstart` on an unloaded unit fails here too.
    """

    def setUp(self):
        super().setUp()
        self._real_unit_target = runner.unit_target
        self._real_run = runner.run
        self.loaded: dict = {}
        self.launchctl: list = []

        def fake_run(cmd, **kw):
            if not cmd or cmd[0] != "launchctl":
                return self._real_run(cmd, **kw)
            self.launchctl.append(list(cmd))
            verb = cmd[1]
            if verb == "bootout":
                label = cmd[2].rsplit("/", 1)[-1]
                rc = 0 if self.loaded.pop(label, None) is not None else 3
                return subprocess.CompletedProcess(cmd, rc, "", "")
            if verb == "bootstrap":
                if self.bootstrap_loads_nothing:
                    # launchd accepts the job and does not run it — the real shape when
                    # the label is disabled in the domain's override database.
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if self.bootstrap_rc:
                    return subprocess.CompletedProcess(cmd, self.bootstrap_rc, "",
                                                       "5: Input/output error")
                data = plistlib.loads(Path(cmd[3]).read_bytes())
                self.loaded[data["Label"]] = data.get("WorkingDirectory")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if verb == "kickstart":
                label = cmd[-1].rsplit("/", 1)[-1]
                # The real verb cannot start what is not loaded, which is the whole
                # reason an auto-cutover needs bootstrap and not this.
                ok = label in self.loaded
                return subprocess.CompletedProcess(cmd, 0 if ok else 3, "",
                                                   "" if ok else "no such service")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        self.bootstrap_rc = 0
        self.bootstrap_loads_nothing = False
        runner.run = fake_run
        runner.unit_target = lambda label: self.loaded.get(label)

    def tearDown(self):
        runner.unit_target = self._real_unit_target
        runner.run = self._real_run
        super().tearDown()

    # -- helpers ---------------------------------------------------------------

    UNIT = "com.example.board"

    def _contract(self, **over):
        return {**CONTRACT, "units": [self.UNIT], "restart": "kickstart", **over}

    def _set_cutover(self, mode, system=None):
        reg = json.loads(self.registry.read_text())
        row = reg["systems"][system or self.system]
        if mode is None:
            row.pop("cutover", None)
        else:
            row["cutover"] = mode
        self.registry.write_text(json.dumps(reg))

    def _ship_plist(self, *, label=None, working_dir=None, name=None):
        """Put a plist in the UPSTREAM repo's deploy/ dir, so the deployed tag carries
        it the way a member's tag can carry its own rendered plist."""
        label = label or self.UNIT
        wd = working_dir if working_dir is not None else str(
            runner.work_dir(self.system, {"subdir": None}))
        path = self.upstream / "deploy" / f"{name or label}.plist"
        path.write_bytes(plistlib.dumps({
            "Label": label,
            "ProgramArguments": ["/bin/sh", "-c", "exit 0"],
            "WorkingDirectory": wd,
            "RunAtLoad": True,
        }))
        return path

    def _agents(self):
        d = Path(os.environ["POGA_DEPLOY_LAUNCH_AGENTS"])
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def _verbs(self):
        return [c[1] for c in self.launchctl]


@requires_macos("plutil")
class TestAutoCutover(AutoCutoverCase):
    """WI-0350: a green smoke must not still need a person to boot the unit."""

    def test_an_unloaded_unit_is_cut_over_without_a_human(self):
        """THE ITEM. The measured shape: the unit is not loaded at all, so there is
        nothing to kickstart, and before this the run stopped at `awaiting-cutover` and
        waited for someone to run three launchctl commands by hand."""
        self._set_cutover("auto")
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())

        rc, out = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["status"], "deployed",
                         "a green smoke on an auto row must reach the terminal healthy "
                         "status in ONE run, with no human step in between")
        self.assertIn("bootstrap", self._verbs())
        self.assertEqual(self.loaded[self.UNIT],
                         str(runner.work_dir(self.system, {"subdir": None})),
                         "the unit must end up running IN the deploy tree — the fact "
                         "cutover_state measures, not the fact the commands exited 0")
        self.assertEqual(self._agents(), [f"{self.UNIT}.plist"])
        self.assertTrue(self._ledger()["cutover"]["cut_over"])
        self.assertEqual(
            [a["unit"] for a in self._ledger()["cutover_action"]["acted"]], [self.UNIT],
            "the ledger has to say the runner did this, not that it found it done")
        self.assertNotIn("AWAITING CUTOVER", out)

    def test_a_manual_row_in_the_same_sweep_still_stops(self):
        """The other half of the acceptance, and the half that makes the first half safe.
        An auto path that promoted every system would be a different defect, not this
        fix — so this runs BOTH rows through one sweep and demands they diverge."""
        other = "widget2"
        up = self.root / other
        up.mkdir()
        _git(["init", "-b", "main"], up)
        (up / "deploy").mkdir()
        (up / "deploy" / "deploy.json").write_text(json.dumps(
            {**CONTRACT, "system": other, "units": [self.UNIT], "restart": "kickstart"}))
        plistlib
        (up / "deploy" / f"{self.UNIT}.plist").write_bytes(plistlib.dumps({
            "Label": self.UNIT, "ProgramArguments": ["/bin/sh", "-c", "exit 0"],
            "WorkingDirectory": str(runner.deploy_root() / other), "RunAtLoad": True}))
        _git(["add", "-A"], up)
        _git(["commit", "-m", "c"], up)
        _git(["tag", "v1.0.0"], up)

        reg = json.loads(self.registry.read_text())
        reg["systems"][self.system]["cutover"] = "auto"
        reg["systems"][other] = {"remote": str(up), "subdir": None, "cutover": "manual"}
        self.registry.write_text(json.dumps(reg))
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())

        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.sweep()

        self.assertEqual(runner.read_ledger(self.system)["status"], "deployed")
        self.assertEqual(runner.read_ledger(other)["status"], "awaiting-cutover",
                         "a system left on the default must be untouched by this "
                         "feature — being upgraded past it is not opting in")
        self.assertNotIn("cutover_action", runner.read_ledger(other),
                         "nothing may be recorded as done to a manual row")

    def test_the_default_is_manual_when_the_key_is_absent(self):
        """Stated as its own test because it is the compatibility promise: every row in
        the shipped registry except the one that opted in omits this key."""
        self._set_cutover(None)
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())

        rc, out = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")
        self.assertIn("AWAITING CUTOVER", out)
        self.assertEqual(self._verbs(), [],
                         "a default row must not reach launchctl at all")
        self.assertEqual(self._agents(), [])

    def test_a_stranded_system_is_cut_over_on_a_re_probe_with_no_new_tag(self):
        """A member that has held its tag on disk for days has `before == target`, so the
        sweep arrives on the RE-PROBE path — there is no new release coming to carry it
        down the forward path. An auto-cutover wired only into the forward branch would
        leave the one system it was built for exactly where it was."""
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        rc, _ = self._deploy()                       # lands at awaiting-cutover
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")

        self._set_cutover("auto")                    # the only change: the registry
        rc, out = self._deploy()                     # same tag, no new release

        self.assertEqual(rc, 0)
        self.assertIn("CUTOVER PERFORMED", out)
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertEqual(self.loaded[self.UNIT],
                         str(runner.work_dir(self.system, {"subdir": None})))

    def test_a_bootstrap_that_loads_nothing_is_not_reported_as_live(self):
        """Every launchctl command exits 0 and the unit still is not running in the
        deploy tree — launchd accepts a bootstrap for a label disabled in its override
        database without starting it.

        This is the one failure the whole `awaiting-cutover` state exists to prevent, so
        the runner must decide it is live by RE-ASKING `cutover_state` — the same
        question, re-measured — and never by concluding it from its own exit codes. A
        cutover that reported success here would name a version in the ledger that
        nothing on the machine is executing."""
        self.bootstrap_loads_nothing = True
        self._set_cutover("auto")
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())

        rc, out = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self.loaded, {}, "fixture check: nothing actually came up")
        self.assertIn("bootstrap", self._verbs(), "and the runner did try")
        self.assertEqual(
            self._ledger()["status"], "awaiting-cutover",
            "exit code 0 from launchctl is not evidence that the unit runs in the "
            "deploy tree; only cutover_state's re-measurement is")
        self.assertFalse(self._ledger()["cutover"]["cut_over"])
        self.assertIn("AWAITING CUTOVER", out)

    def test_the_pre_cutover_plist_is_kept_beside_the_tree(self):
        """The 09-11 ruling asks for the replaced plist to be saved. Beside the tree, not
        in it: a deploy is `checkout --force --detach`, which would sweep away a backup
        stored inside."""
        agents = Path(os.environ["POGA_DEPLOY_LAUNCH_AGENTS"])
        agents.mkdir(parents=True, exist_ok=True)
        old = agents / f"{self.UNIT}.plist"
        old.write_bytes(plistlib.dumps({
            "Label": self.UNIT, "ProgramArguments": ["/bin/sh", "-c", "exit 0"],
            "WorkingDirectory": "/Users/someone/Projects/board"}))
        self.loaded[self.UNIT] = "/Users/someone/Projects/board"

        self._set_cutover("auto")
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        rc, _ = self._deploy()

        self.assertEqual(rc, 0)
        kept = runner.pre_cutover_dir(self.system) / f"{self.UNIT}.plist"
        self.assertTrue(kept.exists(), "the replaced plist must survive the cutover")
        self.assertFalse(
            kept.resolve().is_relative_to(runner.deploy_tree(self.system).resolve()),
            "and must not live inside the tree a deploy force-checks-out")
        self.assertEqual(
            plistlib.loads(kept.read_bytes())["WorkingDirectory"],
            "/Users/someone/Projects/board",
            "the backup is the plist production RAN, not a copy of what we installed")
        self.assertIn("bootout", self._verbs(),
                      "a loaded unit pointing elsewhere has to be booted out first")

    def test_a_dry_run_cuts_nothing_over(self):
        """A dry run that installed a launchd job would not be a dry run."""
        self._set_cutover("auto")
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        rc, out = self._deploy(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._agents(), [])
        self.assertEqual(self.loaded, {})
        self.assertEqual(self._ledger(), {}, "a dry run writes no ledger")
        self.assertIn("would bootout", out)
        self.assertIn("a dry run does not write", out,
                      "and must say which checks it could not make")


@requires_macos("plutil")
class TheCutoverFactTravelsInTheReceiptTest(AutoCutoverCase):
    """WI-0350's acceptance is a fact about the LEDGER, and the ledger never leaves the
    machine that wrote it.

    WHY THIS IS NEEDED. An acceptance of the form "the member's ledger shows live v1.0.0
    reached with no human step" cannot be checked from the lane that owns it. A member
    deploys and cuts over on the Runner; the only artifact that travels is its
    `outbox/to-<member>-arch/<date>-deploy-<member>-deployed-v1.0.0.md` receipt, and it
    reports `deployed`, verify-passed, and NOTHING WHATEVER about the cutover. A runner
    that bootstrapped the unit itself and a person who bootstrapped it by hand before the
    sweep next looked produce receipts that are identical in every field. The difference
    lives only in the ledger, which ADR-0103 D7 keeps machine-local on purpose, on a
    machine no Architect can reach — so "go read the ledger" is not an available answer and
    the receipt is the whole of what anybody else ever gets.

    So the feature built to remove the human from the loop could not report whether it had.

    ASSERTED ON THE TEXT THAT GETS POSTED, end to end on real deploys, for
    `StateNotesReachThePostedReceiptTest`'s reason one file over: the hand-off runs
    `maybe_auto_cutover` records -> `write_ledger` stores -> `result_brief` renders, and
    every one of those three passes its own unit test while the fact still fails to arrive.
    """

    def _receipt(self, status):
        """The posted receipt for one outcome, by the status in its filename.

        BY STATUS, not by position or mtime: these cases deploy more than once, so the
        outbox holds an `awaiting-cutover` receipt and a `deployed` one written the same
        second, and both sorting rules pick between them by accident."""
        hits = [f for _, f in self._queued() if f"-{status}-" in f.name]
        self.assertEqual(len(hits), 1,
                         f"expected one {status} receipt, got {[f.name for _, f in self._queued()]}")
        return hits[0].read_text()

    def _live_unit(self):
        """What a unit already pointing at the deploy tree looks like to the fixture's
        model of launchd — i.e. a system somebody else has already cut over."""
        self.loaded[self.UNIT] = str(runner.work_dir(self.system, {"subdir": None}))

    def test_an_automatic_cutover_says_so_in_the_receipt_that_travels(self):
        """The measured shape: the tag is already on disk, the registry flips to
        `auto`, and the sweep arrives on the re-probe path. The receipt this produces is
        the one artifact anybody off the deploying machine will ever see, so it is where
        "no human step" has to be legible."""
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        self._deploy()                               # lands at awaiting-cutover
        self._set_cutover("auto")
        rc, _ = self._deploy()                       # same tag; the cutover happens here

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["status"], "deployed")

        text = self._receipt("deployed")
        self.assertIn("## Cutover", text)
        self.assertIn("Cut over by the runner", text)
        self.assertIn("No human step", text)
        self.assertIn(self.UNIT, text,
                      "the receipt must name the unit that was bootstrapped, not just "
                      "assert that one was")
        self.assertIn("v2.0.0", text)

    def test_a_cutover_somebody_else_performed_is_not_reported_as_automatic(self):
        """THE CASE THE WHOLE SECTION EXISTS FOR, and the one that was indistinguishable
        before it.

        A person boots the unit by hand; the next sweep re-probes, finds the system live,
        and moves the ledger to `deployed`. That is a correct outcome and an honest status
        — and it is NOT the acceptance criterion, which asks whether the loop closed with
        nobody in it. A receipt that renders this the same as the automatic case makes the
        item unclosable on evidence, because every `deployed` receipt would read as proof."""
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        self._deploy()
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")

        self._set_cutover("manual")
        self._live_unit()                            # a human, outside this program
        rc, _ = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertNotIn("cutover_action", self._ledger(),
                         "fixture check: the runner must not have acted here")

        text = self._receipt("deployed")
        self.assertIn("## Cutover", text)
        self.assertIn("Already cut over before this run measured it", text)
        self.assertNotIn("No human step", text,
                         "a cutover the runner did not perform must never read as one "
                         "that needed nobody — that is the exact claim WI-0350 is closed "
                         "on, and it would be false")

    def test_a_refused_automatic_cutover_carries_its_reason_off_the_machine(self):
        """A refusal is the runner working correctly, and it is still news: the tag is on
        disk and nothing executes it. Before this the receipt said `awaiting-cutover` and
        left the reason in a launchd log on the machine nobody can reach."""
        self._set_cutover("auto")
        self._release("v2.0.0", contract=self._contract())   # deliberately NO plist shipped

        rc, _ = self._deploy()

        self.assertEqual(self._ledger()["status"], "awaiting-cutover")
        text = self._receipt("awaiting-cutover")
        self.assertIn("## Cutover", text)
        self.assertIn("REFUSED", text)
        self.assertIn("carries no deploy/", text,
                      "the receipt must carry the refusal's own reason, not the fact that "
                      "there was one")
        self.assertIn("Something needs you", text,
                      "a system that OPTED IN to automatic cutover and was refused is not "
                      "the same as one parked on `manual` by choice — the first is the "
                      "runner unable to finish, and only a person can ship the plist it "
                      "asked for")

    def test_an_ordinary_deploy_of_a_live_system_says_nothing_about_cutover(self):
        """SILENCE IS THE NORMAL CASE and has to stay that way. A `## Cutover` heading on
        every receipt is a heading nobody reads by the third one, and then the line that
        matters — a person was needed for this — is invisible again, this time inside a
        section that is always present."""
        self._live_unit()
        self._release("v2.0.0", contract=self._contract())

        rc, _ = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["status"], "deployed")
        self.assertNotIn("## Cutover", self._receipt("deployed"))

    def test_a_how_this_version_does_not_recognise_renders_nothing_rather_than_crashing(self):
        """THE ONE CASE THAT CANNOT BE REACHED BY DEPLOYING, and it is not hypothetical.

        `write_ledger` merges and the ledger outlives the binary, so the runner that READS
        a `cutover_event` is not necessarily the one that wrote it: the Runner can be a
        release behind devbox, and a `how` added later lands in a record an older
        `result_brief` then renders. Looked up directly in `_CUTOVER_PROSE` that is a
        KeyError inside `post_result`, i.e. a deploy that succeeded and then died writing
        its own receipt.

        Constructed rather than deployed, deliberately and for once: the value under test
        is one THIS version cannot produce, which is exactly what makes it worth pinning —
        a fixture built from today's code could only ever test today's vocabulary. The
        mutation run for this change found this branch unproven (the tag guard was
        catching the case the silence test aimed at), so it is asserted on its own terms.
        """
        rec = {"status": "deployed", "current": "v2.0.0",
               "cutover_event": {"how": "teleported", "tag": "v2.0.0", "at": "2026-09-17"},
               "cutover": {"cut_over": True, "pending": [], "unloaded": []}}
        _, text = runner.result_brief(self.system, rec, "resolved from mailboxes.json")
        self.assertNotIn("## Cutover", text,
                         "an unrecognised verdict must be SILENT, not guessed at: a "
                         "receipt that picks the nearest known wording would put a "
                         "confident sentence about human involvement under a state this "
                         "version does not understand")
        self.assertIn("`deployed`", text, "and the rest of the receipt still renders")

    def test_a_later_release_does_not_inherit_the_previous_cutovers_claim(self):
        """`write_ledger` MERGES, so a cutover recorded for v2.0.0 is still sitting in the
        record when v3.0.0 deploys. Rendering it unguarded would stamp "no human step" onto
        a release that never asked the question — a stale field read as current, in the one
        place where being wrong means asserting nobody was involved.

        Proved by deploying a SECOND real release after a real cutover, not by handing the
        renderer a fabricated ledger: the staleness is produced by the merge, so a test
        that constructs the record by hand is testing its own fixture."""
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        self._deploy()
        self._set_cutover("auto")
        self._deploy()
        self.assertIn("No human step", self._receipt("deployed"),
                      "fixture check: v2.0.0 really did cut over automatically")

        self._release("v3.0.0", contract=self._contract(), marker="three")
        rc, _ = self._deploy()

        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger()["current"], "v3.0.0")
        self.assertEqual(self._ledger()["cutover_event"]["tag"], "v2.0.0",
                         "the ledger keeps the older event, which is the point — it is "
                         "how this machine became live and deleting it to avoid the bug "
                         "would lose the record")
        later = [f for _, f in self._queued() if "v3.0.0" in f.name]
        self.assertEqual(len(later), 1, [f.name for _, f in self._queued()])
        text = later[0].read_text()
        self.assertNotIn("## Cutover", text)
        self.assertNotIn("No human step", text)


@requires_macos("plutil")
class TestAutoCutoverRefuses(AutoCutoverCase):
    """Every refusal below leaves the system exactly where it already was —
    `awaiting-cutover`, which is not a failure — and NAMES why. A cutover that guessed
    would report a system live while something else is running, which is the single lie
    `cutover_state` exists to make impossible."""

    def setUp(self):
        super().setUp()
        self._set_cutover("auto")

    def _stopped(self, out):
        self.assertEqual(self._ledger()["status"], "awaiting-cutover")
        self.assertEqual(self._agents(), [],
                         "nothing may be installed by a refused cutover")
        return self._ledger()["cutover_action"]["refused"]

    def test_a_tag_that_ships_no_plist_is_refused(self):
        self._release("v2.0.0", contract=self._contract())
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        refused = self._stopped(out)
        self.assertEqual(refused[0]["unit"], self.UNIT)
        self.assertIn("carries no", refused[0]["reason"])

    def test_a_plist_declaring_another_label_is_refused(self):
        """Label and filename differ across this fleet — one member's com.<member>.plist
        holds label com.<member>.<job> — so a file found at the right PATH is not evidence that
        it defines the right JOB."""
        self._ship_plist(label="com.example.somethingelse", name=self.UNIT)
        self._release("v2.0.0", contract=self._contract())
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        refused = self._stopped(_)
        self.assertIn("com.example.somethingelse", refused[0]["reason"])
        self.assertEqual(self.loaded, {}, "no job may be loaded by a refused cutover")

    def test_a_plist_pointing_outside_the_deploy_tree_is_refused(self):
        """The comfortable lie. This plist is valid, its Label is right, and launchd
        would load it happily — onto the developer's clone. Bootstrapping it would make
        `cutover_state` report the system live while production runs unreleased code."""
        self._ship_plist(working_dir="/Users/someone/Projects/board")
        self._release("v2.0.0", contract=self._contract())
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        refused = self._stopped(_)
        self.assertIn("outside the deploy tree", refused[0]["reason"])
        self.assertEqual(self.loaded, {})

    def test_an_unreadable_plist_is_refused(self):
        (self.upstream / "deploy" / f"{self.UNIT}.plist").write_text("not a plist")
        self._release("v2.0.0", contract=self._contract())
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertTrue(self._stopped(_))

    def test_a_failed_bootstrap_is_loud_and_names_the_backup(self):
        """The one path that cannot fall back quietly: the plist has already been
        replaced, so the unit is neither on the old code nor running the new."""
        self.bootstrap_rc = 5
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        with self.assertRaises(runner.DeployError) as cm:
            self._deploy()
        self.assertIn("bootstrap failed", str(cm.exception))
        self.assertIn(str(runner.pre_cutover_dir(self.system)), str(cm.exception),
                      "the operator has to be told where the plist it replaced went")

    def test_a_misspelled_key_refuses_rather_than_reading_as_the_default(self):
        """`manual` is the safe direction, which is exactly why a near-miss must not
        silently land there: an opt-in that does nothing looks identical to one that was
        never made."""
        reg = json.loads(self.registry.read_text())
        reg["systems"][self.system].pop("cutover")
        reg["systems"][self.system]["Cutover"] = "auto"
        self.registry.write_text(json.dumps(reg))
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        with self.assertRaises(runner.DeployError) as cm:
            self._deploy()
        self.assertIn("'Cutover'", str(cm.exception))

    def test_an_unknown_value_refuses(self):
        self._set_cutover("automatic")
        self._ship_plist()
        self._release("v2.0.0", contract=self._contract())
        with self.assertRaises(runner.DeployError) as cm:
            self._deploy()
        self.assertIn("must be one of", str(cm.exception))


class TestSweepAndStatus(DeployRunnerCase):
    """The sweep is the surface that runs with nobody watching, which makes it the one
    whose failure modes matter most and the one least likely to be exercised by hand."""

    def _second_system(self, name="widget2", tags=("v1.0.0",)):
        repo = self.root / name
        repo.mkdir()
        _git(["init", "-b", "main"], repo)
        (repo / "deploy").mkdir()
        (repo / "deploy" / "deploy.json").write_text(
            json.dumps({**CONTRACT, "system": name}))
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)
        for t in tags:
            _git(["tag", t], repo)
        reg = json.loads(self.registry.read_text())
        reg["systems"][name] = {"remote": str(repo), "subdir": None}
        self.registry.write_text(json.dumps(reg))
        return repo

    def test_sweep_deploys_every_registered_system(self):
        self._second_system()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(runner.read_ledger("widget")["current"], "v1.0.0")
        self.assertEqual(runner.read_ledger("widget2")["current"], "v1.0.0")

    def test_one_broken_system_does_not_stop_the_sweep(self):
        """A sweep that aborts on its first refusal leaves every later system unvisited,
        and nothing says which ones were skipped — the failure would look like 'they were
        all fine'."""
        broken = self.root / "broken"
        broken.mkdir()
        _git(["init", "-b", "main"], broken)
        (broken / "f").write_text("x")
        _git(["add", "-A"], broken)
        _git(["commit", "-m", "c"], broken)          # no tags at all
        reg = json.loads(self.registry.read_text())
        reg["systems"]["broken"] = {"remote": str(broken), "subdir": None}
        self.registry.write_text(json.dumps(reg))

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.sweep()
        out = buf.getvalue()
        self.assertEqual(rc, 1, "a refusal must surface in the sweep's exit code")
        self.assertIn("broken: REFUSED", out)
        self.assertEqual(runner.read_ledger("widget")["current"], "v1.0.0",
                         "the healthy system must still have been deployed")

    def test_sweep_dry_run_deploys_nothing(self):
        self._second_system()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.sweep(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.read_ledger("widget"), {})
        self.assertEqual(runner.read_ledger("widget2"), {})

    def test_status_distinguishes_never_deployed_from_up_to_date(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.status()
        self.assertIn("never deployed on this machine", buf.getvalue())
        self._deploy()
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.status()
        self.assertIn("v1.0.0", buf.getvalue())
        self.assertNotIn("never deployed", buf.getvalue())

    def test_status_reports_an_unregistered_system_as_unregistered(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.status("nobody")
        self.assertEqual(rc, 1)
        self.assertIn("NOT REGISTERED", buf.getvalue())
        self.assertIn("not the same as", buf.getvalue())

    def test_the_cli_wires_every_flag_it_advertises(self):
        """Goes through `main()`, not through the functions directly. The rest of this
        suite calls `status()`/`deploy()` and would stay green with the argument parser
        completely broken — which is exactly what happened: `--offline` was documented in
        help text the parser had never been given, and only running the CLI found it."""
        for argv in (["--status"], ["--status", "--offline"], ["--sweep", "--dry-run"],
                     [self.system, "--canary", "v1.0.0", "--dry-run"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = runner.main(argv)
            self.assertIn(rc, (0, 1), f"{argv} exited {rc}")

    def test_status_flags_a_newer_tag_as_available(self):
        self._deploy()
        self._release("v2.0.0", marker="v2")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.status()
        self.assertIn("v2.0.0 is available", buf.getvalue())


class TestLedgerHonesty(DeployRunnerCase):

    def test_ground_truth_is_the_tree_not_the_ledger(self):
        """The ledger is a convenience; the checked-out tag is the fact. A ledger that
        disagrees with the tree must lose ([`a-close-is-the-banner-not-the-sentence`])."""
        self._deploy()
        runner.write_ledger(self.system, current="v9.9.9")
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")

    def test_corrupt_ledger_reads_as_unknown_not_as_never_deployed(self):
        self._deploy()
        runner.ledger_path(self.system).write_text("{not json")
        self.assertTrue(runner.read_ledger(self.system).get("_unreadable"))


class TestEveryProductionPathIsRedirected(unittest.TestCase):
    """Every `POGA_DEPLOY_*` path the runner WRITES through must be redirected by the base
    fixture — derived from the source, never transcribed.

    THIS EXISTS BECAUSE THE OMISSION IS SILENT AND THE DAMAGE IS TRACKED. The runner's own
    header already says every path it writes is overridable, and that it is a test-safety
    property rather than a convenience. Overridable is not the same as overridden: adding
    `outbox_dir()` left the four existing redirections correct and complete, and the suite
    quietly queued fixture receipts into the federation's real outbox and committed eleven
    of them. Nothing failed. Prose asking the next author to remember is what did not work
    the first time, so this is the tool instead (`add-structural-guard-on-recurrence`).
    """

    # Read-only defaults are exempt, and each says why. Absence from this list is not a
    # pass — a new name must be redirected or argued for here.
    READ_ONLY = {
        "POGA_DEPLOY_SEEDS": "read-only: a gitignored local file that reads as {} when "
                             "absent, so an un-redirected fixture writes nothing",
        "POGA_CHANNEL_WRITER": "not a path at all: a machine LABEL the channel compares "
                               "against, so there is nothing for a fixture to write through",
    }

    #: Scanned for names. `curate/channel.py` is in the list because the runner resolves
    #: `outbox_dir()` and `comms_dir()` THROUGH `channel.data_root` — a path this program
    #: writes is still this program's hazard when it is spelled in another module.
    #: `curate/production.py` joined it for exactly that reason (WI-0361): the runner
    #: calls `production.resolve(REPO_ROOT)` on every deploy, and the env name that
    #: selects the service — and with it the queue, the config root and the transport —
    #: is spelled only there. Until it was scanned, the single most consequential env
    #: read the runner performs was invisible to the guard that exists to find them.
    SOURCES = ("deploy/runner.py", "curate/channel.py", "curate/production.py",
               # WI-0229. It resolves every path through the runner's own late-bound
               # accessors and so declares no name of its own TODAY. It is listed anyway:
               # the guard's failure mode is a file nobody added, and "it has none yet" is
               # the state every one of these files was in before it had one.
               "deploy/diagnose.py",
               # WI-0366, on diagnose's own argument and with a sharper reason. Its
               # DEFAULTS are the real machine — `~/Library/LaunchAgents`, `launchctl`,
               # `crontab`, `~/.local/bin/poga` — so it is the deploy-side file whose
               # un-redirected reach is widest, and the day it reads a path from the
               # environment instead of a flag is the day nobody remembers this list.
               "deploy/legacydeps.py")

    def test_every_env_path_the_runner_reads_is_redirected_by_the_fixture(self):
        # `POGA_[A-Z_]+`, NOT `POGA_DEPLOY_[A-Z_]+`, and the narrower spelling is exactly the
        # failure this class's own docstring warns about, committed by this class (WI-0360).
        # The prefix filter silently shrank the SUBJECT SET: `POGA_CLI_LINK` — a live symlink
        # to the operator's `poga` command — and `POGA_CHANNEL_ROOT` were both invisible to
        # it, so the guard would have reported a clean pass over a set that excluded the most
        # dangerous path the runner has. A check that reports a verdict without reporting
        # what it examined cannot tell "looked and found nothing" from "never looked", and
        # the assertion below on `names` being non-empty caught only the total-zero case.
        root = Path(__file__).resolve().parents[1]
        source = "\n".join((root / f).read_text() for f in self.SOURCES)
        names = set(re.findall(r"POGA_[A-Z_]+", source))
        self.assertTrue(names, "found no POGA_* names — the extractor stopped "
                               "looking, which reads as a clean result and is not one")
        # Name the two the old prefix filter could not see, so a future narrowing fails here
        # rather than passing quietly over a smaller set.
        for dangerous in ("POGA_CLI_LINK", "POGA_CHANNEL_ROOT"):
            self.assertIn(dangerous, names,
                          f"{dangerous} is no longer among the names this guard examines. "
                          f"Either it left the source, or the extractor narrowed again — "
                          f"and a guard that stops looking at a path reports a pass.")
        fixture = _fixture_env_names()
        for name in sorted(names - set(self.READ_ONLY)):
            self.assertIn(
                name, fixture,
                f"{name} is a path the runner resolves at call time, and "
                f"DeployRunnerCase.setUp does not redirect it. Left as is, every test in "
                f"this file reaches the federation's own repo through it. Redirect it in "
                f"the fixture, or add it to READ_ONLY with the reason it cannot write.")

    def test_an_escalation_from_a_sealed_tree_publishes_when_production_is_unset(self):
        """WI-0361 must not retire the channel before its replacement can run.

        `POGA_FEDERATION_CONFIG` is set by nothing yet — provisioning is WI-0365's — so
        every escalation on a cut-over Runner takes this branch. Returning False here
        writes the note into a release clone and tells no one, which is the exact defect
        the channel path was built to end. The three routes are pinned together because
        the bug was a route silently collapsing into `return False`."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-09-17-deploy-widget-failed.md"
            path.write_text("# widget failed\n")
            published = []

            def _publish(files, subject, body, origin=None, log=None):
                published.append(subject)
                return True

            with patch.object(runner.production, "resolve", return_value=None), \
                 patch.object(runner.channel, "publish", _publish), \
                 patch.object(runner.channel, "data_root", return_value=Path(tmp)), \
                 patch.object(runner.channel, "origin_of", return_value="origin"):
                with patch.object(runner.channel, "is_deploy_tree", return_value=True):
                    self.assertTrue(runner.publish_note(path, "widget", lambda _: None))
                self.assertEqual(len(published), 1, "a sealed tree must still publish")
                # A lane or the trunk must NOT publish: the session owning that checkout
                # commits the note itself, and a second writer is what P13 forbids.
                with patch.object(runner.channel, "is_deploy_tree", return_value=False):
                    self.assertFalse(runner.publish_note(path, "widget", lambda _: None))
                self.assertEqual(len(published), 1, "an attached checkout must not publish")

    def test_the_fixture_really_removes_the_names_it_claims_to_clear(self):
        """`_cleared` is counted as a defence above, so it has to be one.

        A list of names that the fixture reads and does nothing with would satisfy the
        guard while leaving an ambient service config live for every test in this file —
        the guard would then be reporting on its own bookkeeping instead of on the
        environment the runner actually sees."""
        sentinel = str(Path(__file__).resolve().parent / "not-a-real-service.json")
        case = DeployRunnerCase("setUp")
        prior = os.environ.get("POGA_FEDERATION_CONFIG")
        os.environ["POGA_FEDERATION_CONFIG"] = sentinel
        try:
            case.setUp()
            try:
                self.assertTrue(case._cleared, "nothing is cleared, so the claim is empty")
                for name in case._cleared:
                    self.assertNotIn(name, os.environ,
                                     f"{name} survived setUp, so the runner still reads "
                                     f"the ambient value during every test in this file")
            finally:
                case.tearDown()
            self.assertEqual(os.environ.get("POGA_FEDERATION_CONFIG"), sentinel,
                             "tearDown must put the operator's own environment back")
        finally:
            if prior is None:
                os.environ.pop("POGA_FEDERATION_CONFIG", None)
            else:
                os.environ["POGA_FEDERATION_CONFIG"] = prior


def _fixture_env_names():
    """The env names `DeployRunnerCase.setUp` actually neutralises, observed by running it.

    Reading `self._env` after a real `setUp` rather than parsing the source: the question
    is what the fixture DOES, and a regex over its body would answer a question about how
    it is spelled. `_cleared` counts too — removing a name is as good a defence as
    redirecting it, and for a whole-service selector it is the only correct one."""
    case = DeployRunnerCase("setUp")
    case.setUp()
    try:
        return set(case._env) | set(case._cleared)
    finally:
        case.tearDown()


class TestResultReturnsToItsArchitect(DeployRunnerCase):
    """WI-0230 — a deploy's outcome leaves the machine that produced it.

    The ledger stays machine-local (ADR-0103 D7) and a COPY travels, so there is still one
    writer of what runs here and the development machine still gets told."""

    def test_a_deploy_posts_a_receipt_carrying_the_tags_gates_and_timestamps(self):
        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        queued = self._queued()
        self.assertEqual(len(queued), 1, f"expected one receipt, got {queued}")
        aid, path = queued[0]
        self.assertEqual(aid, "widget-arch")
        text = path.read_text()
        for field in ("v1.0.0", "ledger `previous`", "**Smoke:**", "**Verify:**",
                      "**Recorded at:**", "`deployed`"):
            self.assertIn(field, text)

    def test_the_receipt_declares_an_apply_mode_the_delivery_guard_accepts(self):
        """A header the guard refuses is bucketed `malformed` by the poller forever, and
        that bucket never moves its exit code — so the wrong header builds a return path
        whose failure is silent. Run the real guard, not a copy of its rules."""
        self._deploy()
        _, path = self._queued()[0]
        check = Path(__file__).resolve().parents[1] / "curate" / "check-apply.py"
        proc = subprocess.run([sys.executable, str(check), str(path)],
                              capture_output=True, text=True,
                              env={**os.environ, "POGA_ADOPTION_LEDGER_OFF": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_receipt_is_committed_not_merely_written(self):
        """A brief nobody commits has not been sent; it has been typed. Worse, it leaves
        the tree dirty, and `mail-poller.refresh` then declines to fast-forward — so one
        uncommitted receipt stops the checkout advancing at all."""
        self._deploy()
        self.assertTrue(any("deploy receipt" in s for s in self._fed_log()),
                        f"no receipt commit in {self._fed_log()}")
        dirt = subprocess.run(["git", "status", "--porcelain", "--", "outbox"],
                              cwd=str(self.fedrepo), capture_output=True, text=True,
                              check=True).stdout.strip()
        self.assertEqual(dirt, "", f"outbox left dirty after the post: {dirt!r}")

    def test_a_second_look_at_an_unchanged_deploy_posts_nothing(self):
        """The sweep re-runs every ten minutes. A receipt per invocation would be a tracked
        file per system per ten minutes forever — the record burying what it records."""
        self._deploy()
        self.assertEqual(len(self._queued()), 1)
        rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)
        self.assertEqual(len(self._queued()), 1, "a no-op posted a second receipt")

    def test_a_new_version_posts_a_second_receipt_naming_the_previous_tag(self):
        self._deploy()
        self._release("v1.1.0", marker="v2")
        self._deploy()
        queued = self._queued()
        self.assertEqual(len(queued), 2)
        newest = max((p for _, p in queued), key=lambda p: p.name)
        text = newest.read_text()
        self.assertIn("v1.1.0", text)
        self.assertIn("`v1.0.0`", text)

    def test_a_dry_run_posts_nothing(self):
        """A rehearsal that writes a tracked file is not a rehearsal — this program has
        already shipped that bug once against the ledger."""
        self._deploy(dry_run=True)
        self.assertEqual(self._queued(), [])

    def test_the_dry_run_guard_holds_even_when_the_ledger_DID_change(self):
        """The test above passes for a second reason — a dry run writes no ledger, so the
        state-change comparison alone would already refuse. That makes it blind to the
        guard it is named for: deleting `if dry_run: return` leaves it green.

        A branch that writes the ledger on a dry run is not hypothetical here; the
        awaiting-cutover path shipped doing exactly that and carries its own guard because
        of it. So pin the rule itself — a changed ledger, `dry_run=True`, nothing posted."""
        self._deploy()
        before = self._ledger()
        # `_decided` rather than a bare `status=`: since WI-0412 a status must name the tag
        # it was decided for, and `write_ledger` refuses one that does not.
        runner.write_ledger(self.system, current="v9.9.9", attempted="v9.9.9",
                            **runner._decided("deployed", "v9.9.9"))
        posted = runner.post_result(self.system, before, True, lambda *_: None)
        self.assertFalse(posted["posted"])
        self.assertEqual(posted["why"], "dry run")
        self.assertEqual(len(self._queued()), 1, "a dry run posted a second receipt")

    def test_a_smoke_refusal_posts_its_own_receipt(self):
        self._deploy()
        self._release("v1.1.0", {**CONTRACT, "smoke": {"cmd": ["/bin/sh", "-c", "exit 3"]}},
                      marker="bad")
        rc, _ = self._deploy()
        self.assertEqual(rc, 1)
        texts = [p.read_text() for _, p in self._queued()]
        self.assertTrue(any("smoke-failed" in t and "FAILED (exit 3)" in t for t in texts),
                        "the refusal produced no receipt naming the failed gate")

    def test_an_unrostered_system_is_told_apart_from_a_resolved_one(self):
        """A system can be registered for deploy and absent from `mailboxes.json`. The
        receipt still goes to the conventional address — a tracked directory reaches the
        development machine whether or not a mailbox resolves — and it says which of the
        two answers it is ([`declare-what-a-check-assumes`])."""
        self.mailboxes.write_text(json.dumps({"members": {}}))
        self._deploy()
        aid, path = self._queued()[0]
        self.assertEqual(aid, "widget-arch")
        self.assertIn("derived from the <system>-arch convention", path.read_text())

    def test_mail_trouble_never_fails_a_deploy_that_worked(self):
        """A mail problem is not a deploy problem, and reporting one as the other is how a
        green release starts reading as red."""
        with patch.object(runner.outbox, "post", side_effect=OSError("disk full")):
            rc, out = self._deploy()
        self.assertEqual(rc, 0)
        self.assertIn("could not queue the result", out)

    def test_two_receipts_on_one_day_keep_both(self):
        """Deploy, roll back, redeploy the same tag: three honest records wanting one
        name. `stage` refuses a collision and is right to; a generated receipt is the
        opposite case."""
        self._deploy()
        self._release("v1.1.0", marker="v2")
        self._deploy()
        self._deploy(rollback=True)
        names = [p.name for _, p in self._queued()]
        self.assertEqual(len(names), len(set(names)), f"a receipt was overwritten: {names}")
        self.assertEqual(len(names), 3, f"expected three receipts, got {names}")



class TheSelfSweepCannotBootOutItsOwnUnitTest(DeployRunnerCase):
    """The last manual link for the federation itself (WI-0359).

    `refuse_self_restart` already names the hazard for `kickstart`: every unit of the self
    system runs code this process is made of, so restarting them from inside a sweep
    restarts the sweep. `bootout` is the same hazard by a different verb — and the cutover
    NEEDS bootout, because a loaded job keeps the WorkingDirectory it was bootstrapped
    with. So deferring without a finisher would mean NEVER cutting over, with the ledger
    reporting the cutover handled while production stayed on the old tree.
    """

    def test_the_unit_we_run_under_is_deferred_not_booted_out(self):
        entry = {"remote": str(self.upstream), "subdir": None, "self": True}
        cut = {"pending": [{"unit": "com.federation.deploy-sweep"}], "unloaded": []}
        spawned = []
        with patch.dict(os.environ,
                        {"XPC_SERVICE_NAME": "com.federation.deploy-sweep"}), \
             patch.object(runner, "_spawn_self_cutover_finisher",
                          side_effect=lambda *a: (spawned.append(a), True)[1]), \
             patch.object(runner, "run") as ran:
            rec = runner.perform_cutover("federation", entry, {}, cut, "v1.0.0",
                                         lambda *_: None, dry_run=False)
        self.assertEqual([d["unit"] for d in rec["deferred"]],
                         ["com.federation.deploy-sweep"])
        self.assertEqual(rec["acted"], [])
        self.assertTrue(spawned, "a deferral with no finisher is a silent omission")
        for call in ran.call_args_list:
            self.assertNotIn("bootout", " ".join(map(str, call.args[0])),
                             "the sweep booted out the job it is running under")

    def test_a_sibling_unit_of_the_self_system_is_still_cut_over(self):
        """Only the ONE unit we run under is special. Deferring the rest would leave the
        federation half cut over for no reason."""
        entry = {"remote": str(self.upstream), "subdir": None, "self": True}
        cut = {"pending": [], "unloaded": ["com.federation.mail-poller"]}
        with patch.dict(os.environ,
                        {"XPC_SERVICE_NAME": "com.federation.deploy-sweep"}), \
             patch.object(runner, "_spawn_self_cutover_finisher", return_value=True), \
             patch.object(runner, "unit_plist",
                          return_value=Path("/nonexistent/x.plist")):
            rec = runner.perform_cutover("federation", entry, {}, cut, "v1.0.0",
                                         lambda *_: None, dry_run=False)
        self.assertEqual(rec["deferred"], [])
        self.assertTrue(rec["refused"], "a missing plist must refuse, not silently pass")

    def test_a_non_self_system_is_never_deferred(self):
        """`example-app` runs no code this process is made of; deferring its units would
        reintroduce the manual step `cutover: auto` exists to remove."""
        entry = {"remote": str(self.upstream), "subdir": None}
        cut = {"pending": [], "unloaded": ["com.example.app"]}
        with patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.example.app"}), \
             patch.object(runner, "unit_plist",
                          return_value=Path("/nonexistent/x.plist")):
            rec = runner.perform_cutover("example-app", entry, {}, cut, "v1.0.0",
                                         lambda *_: None, dry_run=False)
        self.assertEqual(rec["deferred"], [])

    def test_the_finisher_refuses_while_the_sweep_is_still_alive(self):
        """It waits on the PID, not on a sleep: what must be true is that the sweep has
        EXITED, and a sleep asserts that by hope. Still-alive must refuse rather than
        boot out the process that is waiting for it."""
        with patch.object(runner, "SELF_CUTOVER_WAIT_SECONDS", 2), \
             patch.object(runner, "_cutover_install") as install:
            rc = runner.finish_self_cutover("federation", "com.federation.deploy-sweep",
                                            os.getpid(), log=lambda *_: None)
        self.assertEqual(rc, 1)
        install.assert_not_called()


def _git_out(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True).stdout


class TheSelfRowRefusesABackwardsCutoverTest(DeployRunnerCase):
    """The guard compares the candidate tag against the DEPLOYED tag (WI-0359).

    ITS FIRST VERSION COMPARED AGAINST THE TRUNK CHECKOUT AND WAS UNPASSABLE. A trunk
    checkout is by construction ahead of the newest tag — the tag is cut from a commit and
    the trunk keeps moving — so the FIRST cutover could never pass. By design the mail-poller
    refresh can fast-forward the process checkout between the sync and the sweep, and then
    the just-cut tag is refused as "an ancestor" for doing nothing wrong.
    A guard that fires on correct code is one somebody deletes, and
    the real protection goes with it.

    What it is actually for: a system already LIVE on a tag being moved to an older one.
    """

    def _ledger(self, current):
        return patch.object(runner, "read_ledger", return_value=
                            ({"current": current} if current else {}))

    def _ancestry(self, cand, live, is_ancestor):
        """Stub the three git calls the guard makes, so the test pins the DECISION rather
        than re-deriving real ancestry (which would make it a test of git)."""
        def fake(args, cwd=None, timeout=None):
            if args[:2] == ["rev-list", "-n"]:
                out = cand if args[3] != "v-live" else live
            elif args[:1] == ["merge-base"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0 if is_ancestor else 1, stdout="", stderr="")
            else:
                out = ""
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout=out + "\n", stderr="")
        return patch.object(runner, "git", side_effect=fake)

    def test_the_first_cutover_proceeds_when_nothing_is_live_yet(self):
        """THE CASE THE FIRST VERSION BROKE. No deployed tag means there is nothing to
        move backwards from; the units are still on a developer checkout, which is the
        state a cutover exists to end."""
        with self._ledger(None):
            self.assertEqual(
                runner.self_cutover_would_regress("federation", {"self": True}, "v7.2.0"),
                "")

    def test_an_older_tag_than_the_live_one_is_refused(self):
        with self._ledger("v-live"), self._ancestry("aaaa1111", "bbbb2222", True):
            why = runner.self_cutover_would_regress(
                "federation", {"self": True}, "v7.1.0")
        self.assertIn("BACKWARDS", why)
        self.assertIn("ANCESTOR", why)

    def test_a_newer_tag_than_the_live_one_is_allowed(self):
        with self._ledger("v-live"), self._ancestry("cccc3333", "bbbb2222", False):
            self.assertEqual(
                runner.self_cutover_would_regress("federation", {"self": True}, "v7.3.0"),
                "")

    def test_redeploying_the_live_tag_is_allowed(self):
        with self._ledger("v7.2.0"):
            self.assertEqual(
                runner.self_cutover_would_regress("federation", {"self": True}, "v7.2.0"),
                "")

    def test_a_non_self_system_is_never_subject_to_this(self):
        """A non-self member deploying an old tag is an ordinary rollback and the operator's
        business; its tree is not made of the code this process runs."""
        self.assertEqual(runner.self_cutover_would_regress("example-app", {}, "v0.0.1"), "")

    def test_unreadable_ancestry_refuses_rather_than_guessing(self):
        """Not knowing whether this moves production backwards is not permission to try:
        a deferred cutover is retried by the next sweep, a wrong guess regresses the
        runner itself."""
        def broken(args, cwd=None, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="",
                                               stderr="")
        with self._ledger("v-live"), patch.object(runner, "git", side_effect=broken):
            self.assertIn("refusing", runner.self_cutover_would_regress(
                "federation", {"self": True}, "v7.1.0"))

    def test_the_refusal_reaches_the_cutover_decision(self):
        """The guard is worthless if `maybe_auto_cutover` never consults it."""
        cut = {"pending": [{"unit": "com.federation.deploy-sweep"}], "unloaded": []}
        with patch.object(runner, "self_cutover_would_regress",
                          return_value="v7.1.0 is an ANCESTOR"), \
             patch.object(runner, "perform_cutover") as performed:
            state, record = runner.maybe_auto_cutover(
                "federation", {"self": True, "cutover": "auto"}, {}, cut, "v7.1.0",
                lambda *_: None, dry_run=False)
        performed.assert_not_called()
        self.assertEqual(record["acted"], [])
        self.assertTrue(record["refused"])


def declared_host_of(unit):
    """The machine a shipped template says it belongs to (WI-0420).

    A RENDER TEST HAS TO SAY WHICH MACHINE IT IS. Rendering is host-scoped now, so a
    fixture that leaves the host to the ambient environment passes on one machine and fails
    on another -- the same fault as session 404's `test_an_attached_checkout_...`, which
    stopped a patch to get a default and then read whatever checkout the suite happened to
    run in. Read from the template rather than spelled here, so a template that changes
    hosts moves its own tests with it."""
    template = Path(runner.REPO_ROOT) / "deploy" / f"{unit}.plist.template"
    return runner.declared_unit_host(template)


@requires_macos("plutil")
class TheTagShipsTemplatesAndCutoverRendersThemTest(DeployRunnerCase):
    """mail-poller and adopt-runner were REFUSED on the deploy host because a release
    carried only `deploy/*.plist.template` and the cutover wants the plist it bootstraps.

    THE TAG MUST NOT START SHIPPING RENDERED PLISTS. The template says why itself: the
    concrete file "carries machine-specific absolute paths, P3" and is never committed.
    `__FED_ROOT__` is wherever THIS machine keeps the deploy tree and `__PYTHON__` is
    whichever interpreter it resolves — committing either would be wrong on every other
    machine that deployed the same tag, which is WI-0132's defect wearing a new hat. So
    the rendering belongs at the cutover, the moment those values are known.
    """

    def setUp(self):
        super().setUp()
        # Every test here renders com.federation.deploy-sweep, which declares `Runner`.
        host = declared_host_of("com.federation.deploy-sweep")
        patcher = patch.object(runner.common, "this_machine", return_value=host)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tree_with_template(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "deploy").mkdir()
        src = Path(runner.REPO_ROOT) / "deploy" / "com.federation.deploy-sweep.plist.template"
        shutil.copy(src, tmp / "deploy")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def test_a_template_is_rendered_with_this_machines_values(self):
        tmp = self._tree_with_template()
        with patch.object(runner, "work_dir", return_value=tmp):
            out = runner.render_unit_plist(
                "federation", {}, "com.federation.deploy-sweep", lambda *_: None)
        self.assertIsNotNone(out)
        text = out.read_text(encoding="utf-8")
        self.assertNotIn("__FED_ROOT__", text)
        self.assertNotIn("__PYTHON__", text)

    def test_the_rendered_plist_points_at_the_deploy_tree_not_the_checkout(self):
        """The whole point of a cutover. A rendered plist naming the developer clone would
        satisfy launchd and leave production running unreleased code."""
        tmp = self._tree_with_template()
        with patch.object(runner, "work_dir", return_value=tmp):
            out = runner.render_unit_plist(
                "federation", {}, "com.federation.deploy-sweep", lambda *_: None)
        declared = plistlib.loads(out.read_bytes())
        self.assertEqual(declared["Label"], "com.federation.deploy-sweep")
        self.assertTrue(str(declared["WorkingDirectory"]).startswith(str(tmp)))
        self.assertNotIn(str(runner.REPO_ROOT), str(declared["WorkingDirectory"]))

    def test_the_rendered_plist_passes_the_install_checks(self):
        """Rendering is not enough if the result would be refused anyway — the four checks
        are the gate this has to get through."""
        tmp = self._tree_with_template()
        with patch.object(runner, "work_dir", return_value=tmp):
            out = runner.render_unit_plist(
                "federation", {}, "com.federation.deploy-sweep", lambda *_: None)
            ok, why = runner._cutover_plist_is_usable(
                out, "com.federation.deploy-sweep", str(tmp))
        self.assertTrue(ok, why)

    def test_no_template_is_not_an_error(self):
        """A member that ships a real plist needs no rendering; absence must not refuse."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "deploy").mkdir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with patch.object(runner, "work_dir", return_value=tmp):
            self.assertIsNone(runner.render_unit_plist(
                "example-app", {}, "com.example.app", lambda *_: None))


class TheDeferredFinisherLeavesAReceiptTest(DeployRunnerCase):
    """The finisher was spawned with stdout and stderr to DEVNULL, so it could not report.

    With nowhere to write, a sweep could exit with the units still on the old checkout
    and no log line, no file and no process to say whether the finisher had run,
    refused, or never spawned. Three outcomes rendering identically as
    silence is the false-green shape, in the tail nobody watches.
    """

    def test_the_log_path_is_machine_local_state(self):
        p = runner.self_cutover_log("federation")
        self.assertEqual(p.name, "self-cutover-federation.log")
        self.assertEqual(p.parent.name, ".session-state")

    def test_an_empty_log_is_reported_as_did_not_run_not_as_nothing_to_say(self):
        """The distinction that matters: a spawned finisher that wrote nothing is a
        failure, and must not read as an absence."""
        said = []
        p = runner.self_cutover_log("federation")
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        original = p.read_bytes() if existed else None
        self.addCleanup(lambda: p.write_bytes(original) if existed else
                        (p.unlink() if p.exists() else None))
        p.write_text("", encoding="utf-8")
        runner.report_self_cutover_log("federation", said.append)
        self.assertTrue(any("did not run" in s for s in said), said)

    def test_a_missing_log_says_nothing_at_all(self):
        """Never deferred on this machine is not a condition to warn about."""
        said = []
        runner.report_self_cutover_log("no-such-system-here", said.append)
        self.assertEqual(said, [])


class TheRegistryDeclaresWhyItIsHandMaintainedTest(unittest.TestCase):
    """WI-0217. `registry.json` restates a fact ADR-0106 D1 also declares from the member
    side (`residency.process`), so "just derive it" is the obvious next move and will be
    proposed again. It is decided against, and the reason is a MEASUREMENT with an expiry
    -- no member has declared -- not a preference. A decision whose reason lives only in a
    closed work item gets re-litigated from scratch by whoever opens the file next, so the
    reason ships IN the file ([`decisions-auditable`]). These pin the two parts of that
    declaration that can rot without anyone touching the paragraph."""

    REPO = Path(__file__).resolve().parents[1]

    def setUp(self):
        reg = json.loads((self.REPO / "deploy" / "registry.json").read_text(encoding="utf-8"))
        if "_comment" not in reg and (self.REPO / "PUBLIC-CUT-RECEIPT.md").is_file():
            # The public cut ships the registry reduced to its `self` row with the notes
            # dropped (they count the fleet and name its rows). Only the internal trunk,
            # which has no receipt, is held to the declaration.
            self.skipTest("public cut: deploy/registry.json ships without its _comment")
        self.comment = "\n".join(reg["_comment"])

    def test_the_registry_says_it_is_hand_maintained_and_names_the_other_declaration(self):
        for anchor in ("HAND-MAINTAINED", "residency.process", "ADR-0106"):
            with self.subTest(anchor=anchor):
                self.assertIn(
                    anchor, self.comment,
                    f"the registry no longer says {anchor!r} — the hand-maintained "
                    f"declaration WI-0217 shipped has been edited away, and a reader is "
                    f"back to guessing whether this file is authored or a stale render")

    def test_the_reopen_check_it_names_is_still_a_real_flag(self):
        """The half that can go wrong on its own, and the reason this is a test rather
        than a proofread. The declaration tells its reader to run
        `standard_version.py --residency-status` to learn whether the condition has been
        met. Rename or drop that flag and the paragraph still READS fine while sending
        every future reader at a command that no longer exists — a pointer rots silently
        where a claim does not. Resolved against the argparse calls rather than a grep of
        the source, so the flag merely being mentioned in a docstring does not pass."""
        self.assertIn("--residency-status", self.comment,
                      "the declaration no longer names a way to check its own premise")
        src = (self.REPO / "curate" / "standard_version.py").read_text(encoding="utf-8")
        flags = {
            node.args[0].value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        self.assertIn(
            "--residency-status", flags,
            f"deploy/registry.json tells its reader to run "
            f"`curate/standard_version.py --residency-status` to find out whether the "
            f"derivation question has reopened, and that flag no longer exists. Point the "
            f"declaration at whatever replaced it. Flags found: {sorted(flags)}")


# -- WI-0228: release candidates and the canary path -----------------------------------


class TheTagPatternAdmitsACandidateButTheResolverIgnoresItTest(DeployRunnerCase):
    """WI-0228, half one. `SEMVER_TAG_RE` had no way to spell a release candidate, so an
    `-rc.N` tag was invisible to this program — not refused, not selected, not
    mentionable. Admitting it is what makes `--canary` addressable; the thing that keeps
    it out of production is `newest_tag`'s default, NOT the pattern.

    Both halves are pinned here because they are one change and each is worthless alone:
    a pattern that matches without a resolver that excludes is an unattended deployer that
    promotes candidates, and a resolver that excludes without a pattern that matches is
    what we already had."""

    def test_a_release_candidate_matches_the_tag_pattern(self):
        m = runner.SEMVER_TAG_RE.match("v2.0.0-rc.1")
        self.assertIsNotNone(m, "an -rc.N tag must be spellable at all")
        self.assertEqual(m.groups(), ("2", "0", "0", "1"))
        self.assertTrue(runner.is_prerelease("v2.0.0-rc.1"))
        self.assertFalse(runner.is_prerelease("v2.0.0"))

    def test_the_pattern_still_refuses_a_suffix_it_does_not_speak(self):
        """The negative control on the widening. `-alpha` and a bare `-rc` are the two
        shapes a reader would assume work, and neither is in this vocabulary — so they
        must read as NOT A SEMVER TAG rather than matching with an empty group."""
        for bad in ("v2.0.0-alpha.1", "v2.0.0-rc", "v2.0.0-rc.x", "v2.0.0rc1", "v2.0"):
            self.assertIsNone(runner.SEMVER_TAG_RE.match(bad), bad)
            self.assertFalse(runner.is_prerelease(bad), bad)

    def _repo_with(self, *tags):
        repo = self.root / f"tags-{'-'.join(t.replace('.', '_') for t in tags)}"
        repo.mkdir()
        _git(["init", "-b", "main"], repo)
        (repo / "f").write_text("x")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)
        for t in tags:
            _git(["tag", t], repo)
        return repo

    def test_a_candidate_is_not_selected_by_the_default_resolver(self):
        """THE ACCEPTANCE CLAUSE. Asserted against `newest_tag` directly, on a repo whose
        candidate is numerically the HIGHEST tag present — if the release were also the
        highest, the assertion would pass whether or not candidates are excluded."""
        repo = self._repo_with("v1.0.0", "v2.0.0-rc.1")
        self.assertEqual(runner.newest_tag(repo), "v1.0.0")
        self.assertEqual(runner.newest_tag(repo, prereleases=True), "v2.0.0-rc.1",
                         "the caller that IS asking about candidates must still get one")

    def test_a_repo_with_only_candidates_has_nothing_deployable(self):
        """Not "up to date", not the candidate — nothing. A system whose only tags are
        candidates has never cut a release, and `resolve_target` must say so in the words
        the untagged case already uses."""
        repo = self._repo_with("v0.1.0-rc.1", "v0.1.0-rc.2")
        self.assertIsNone(runner.newest_tag(repo))
        self.assertEqual(runner.newest_tag(repo, prereleases=True), "v0.1.0-rc.2",
                         "rc.2 must beat rc.1 — the candidate ordinal is a number too")

    def test_a_candidate_ranks_below_its_own_release(self):
        repo = self._repo_with("v2.0.0", "v2.0.0-rc.9")
        self.assertEqual(runner.newest_tag(repo, prereleases=True), "v2.0.0",
                         "v2.0.0 is the release v2.0.0-rc.9 was a candidate FOR")

    def test_candidate_ordering_is_numeric_not_lexical(self):
        repo = self._repo_with("v0.9.0-rc.9", "v0.10.0-rc.2", "v0.10.0-rc.10")
        self.assertEqual(runner.newest_tag(repo, prereleases=True), "v0.10.0-rc.10")

    def test_deploying_a_named_candidate_is_refused(self):
        """`newest_tag` will never hand a candidate to production — but `--version` walks
        straight past it, and that is the route a person takes."""
        self._release("v2.0.0-rc.1", marker="v2")
        with self.assertRaises(runner.DeployError) as cm:
            runner.deploy(self.system, version="v2.0.0-rc.1", quiet=True)
        msg = str(cm.exception)
        self.assertIn("release CANDIDATE", msg)
        self.assertIn("--canary", msg, "a refusal must name the path that does work")
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), None)

    def test_a_sweep_does_not_deploy_a_candidate(self):
        """THE HAZARD THE WHOLE DEFAULT EXISTS FOR, end to end: the unattended path with
        a candidate sitting on origin as the newest tag. Run through `sweep()` rather than
        `newest_tag`, because the sweep is what runs unattended with nobody in the room."""
        self._deploy()
        self._release("v2.0.0-rc.1", marker="v2")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.sweep()
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        self.assertIn("already running v1.0.0", buf.getvalue())


class TheCanaryTreeCannotBeAProductionTreeTest(DeployRunnerCase):
    """WI-0228: the separation `--canary` sells is arithmetic, not care.

    This asserts the property that makes every other canary test's "production untouched"
    claim generalise beyond the one configuration it was measured in. A `canary=True`
    boolean threaded through the deploy path would make that claim true only of the code
    as currently written; a root derived with an unconditional suffix makes it true of
    every configuration there is."""

    def test_no_deploy_root_produces_a_canary_tree_inside_it(self):
        saved = os.environ.get("POGA_DEPLOY_ROOT")
        self.addCleanup(lambda: os.environ.__setitem__("POGA_DEPLOY_ROOT", saved)
                        if saved is not None else os.environ.pop("POGA_DEPLOY_ROOT", None))
        roots = [str(self.root / "deploy"), str(self.root / "a" / "b" / "deploy"),
                 "/tmp/deploy-trees", str(self.root / "deploy-canary")]
        for r in roots:
            os.environ["POGA_DEPLOY_ROOT"] = r
            prod, can = runner.deploy_tree(self.system), runner.canary_tree(self.system)
            self.assertNotEqual(prod, can, r)
            self.assertFalse(can.is_relative_to(runner.deploy_root()), f"{r}: {can}")
            self.assertFalse(prod.is_relative_to(runner.canary_root()), f"{r}: {prod}")
        # The DEFAULT, with nothing redirected, is the configuration that actually runs on
        # the Runner and the one no fixture would otherwise exercise.
        os.environ.pop("POGA_DEPLOY_ROOT", None)
        self.assertNotEqual(runner.deploy_tree("x"), runner.canary_tree("x"))
        self.assertFalse(runner.canary_tree("x").is_relative_to(runner.deploy_root()))


class TheCanaryLeavesProductionExactlyWhereItWasTest(DeployRunnerCase):
    """WI-0228, half two — the ACCEPTANCE test for `--canary`.

    Every assertion here is about something the canary must NOT have done, which is the
    kind of test that passes for free if the verb never ran. So each one is paired: the
    canary is proved to have actually checked the tag out and actually run the smoke
    command in its own tree, and only then is production proved unchanged."""

    def _canary(self, tag, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.canary(self.system, tag, **kw)
        return rc, buf.getvalue()

    def _canary_tree(self):
        return runner.canary_tree(self.system)

    def test_a_canary_runs_the_smoke_in_its_own_tree_and_changes_no_production_state(self):
        self._deploy()
        before_ledger = runner.ledger_path(self.system).read_text()
        # The smoke command writes its own working directory out, so "the gate ran" and
        # "the gate ran THERE" are two separate facts and the second is checkable. A smoke
        # of `exit 0` would satisfy this test with the workdir argument deleted.
        cwd_probe = self.root / "smoke-cwd.txt"
        self._release("v2.0.0-rc.1", marker="v2", contract={
            **CONTRACT, "smoke": {"cmd": ["/bin/sh", "-c", f"pwd > {cwd_probe}"]}})

        rc, out = self._canary("v2.0.0-rc.1")

        self.assertEqual(rc, 0, out)
        self.assertIn("CANARY PASSED", out)
        # It really ran, and it ran in the canary tree.
        self.assertEqual(cwd_probe.read_text().strip(),
                         str(self._canary_tree().resolve()))
        self.assertEqual((self._canary_tree() / "marker.txt").read_text(), "v2")
        # Production is exactly where it was — tree, tag and ledger.
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")
        self.assertEqual(runner.ledger_path(self.system).read_text(), before_ledger,
                         "a canary may not write one byte of the production ledger")
        # And it left a record of its own, marked as what it is.
        rec = runner.read_canary_ledger(self.system)
        self.assertEqual(rec["kind"], "canary")
        self.assertEqual(rec["outcome"], "passed")
        self.assertEqual(rec["tag"], "v2.0.0-rc.1")
        self.assertEqual(rec["production_tag_at_run"], "v1.0.0")

    def test_the_canary_record_is_not_in_the_production_ledgers_glob(self):
        """`anything_cut_over()` reads `status` out of every `*.json` in the ledger dir to
        decide whether this machine has adopted the sealed model. A canary verdict must be
        unreachable from there — not merely shaped so it happens not to match."""
        self._deploy()
        self._release("v2.0.0-rc.1", marker="v2")
        self._canary("v2.0.0-rc.1")
        top = {p.name for p in runner.ledger_dir().glob("*.json")}
        self.assertEqual(top, {f"{self.system}.json"}, top)
        self.assertTrue(runner.canary_ledger_path(self.system).exists())

    def test_a_failing_canary_says_failed_and_still_touches_nothing(self):
        self._deploy()
        before_ledger = runner.ledger_path(self.system).read_text()
        self._release("v2.0.0-rc.1", marker="v2", contract={
            **CONTRACT, "smoke": {"cmd": ["/bin/sh", "-c", "echo candidate is broken; exit 3"]}})

        rc, out = self._canary("v2.0.0-rc.1")

        self.assertEqual(rc, 1)
        self.assertIn("CANARY FAILED", out)
        self.assertIn("candidate is broken", out, "the verdict must carry the evidence")
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        self.assertEqual(runner.ledger_path(self.system).read_text(), before_ledger)
        self.assertEqual(runner.read_canary_ledger(self.system)["outcome"], "failed")

    def test_a_tag_with_no_smoke_check_is_inconclusive_not_passed(self):
        """`run_gate` records an undeclared gate as `declared: False`, and the deploy path
        treats that as non-failing — correctly, because a deploy does other things. A
        canary does not: the smoke IS the canary, so "no smoke declared" means nothing was
        measured, and a caller reading only the exit code must not see a pass."""
        self._release("v2.0.0-rc.1", marker="v2",
                      contract={k: v for k, v in CONTRACT.items() if k != "smoke"})
        rc, out = self._canary("v2.0.0-rc.1")
        self.assertEqual(rc, 1)
        self.assertIn("INCONCLUSIVE", out)
        self.assertIn("not a pass", out)
        self.assertEqual(runner.read_canary_ledger(self.system)["outcome"], "inconclusive")

    def test_a_canary_of_a_released_tag_is_allowed(self):
        """The verb is about candidates but is not restricted to them: rehearsing a real
        release before promoting it is the same question, and refusing it would push
        people back onto deploying to find out."""
        rc, out = self._canary("v1.0.0")
        self.assertEqual(rc, 0, out)
        self.assertIn("CANARY PASSED", out)
        self.assertNotIn("Promoting it is still a separate act", out,
                         "that sentence is for a candidate; a release IS the promotion")

    def test_a_tag_outside_the_vocabulary_is_refused(self):
        _git(["tag", "nightly-2026-09-17"], self.upstream)
        with self.assertRaises(runner.DeployError) as cm:
            runner.canary(self.system, "nightly-2026-09-17", quiet=True)
        self.assertIn("not a release or a release candidate", str(cm.exception))

    def test_a_tag_that_does_not_exist_is_refused(self):
        with self.assertRaises(runner.DeployError) as cm:
            runner.canary(self.system, "v9.9.9-rc.1", quiet=True)
        self.assertIn("does not exist on origin", str(cm.exception))

    def test_a_dry_run_records_nothing(self):
        self._release("v2.0.0-rc.1", marker="v2")
        rc, out = self._canary("v2.0.0-rc.1", dry_run=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(runner.read_canary_ledger(self.system), {})
        self.assertFalse((self._canary_tree() / "marker.txt").exists(),
                         "a dry run clones so it can read the contract, and stops there")

    def test_the_dependency_sync_also_runs_in_the_canary_tree(self):
        """THE OTHER HALF OF THE WORKDIR CHANGE, and it needed its own test: every contract
        in this suite declares `deps: {"kind": "none"}`, so `sync_deps` returns before it
        runs anything and a mutation deleting its `workdir` argument survived the whole
        suite. Measured, not assumed — that is why this exists.

        `npm` is borrowed as the kind because it is in the schema's enum and its command is
        replaced here; nothing npm-shaped happens. What is under test is the working
        directory the deps command is launched in, and a canary that synced dependencies
        into the production tree would be writing production on behalf of a probe."""
        probe = self.root / "deps-cwd.txt"
        self._deploy()
        with patch.dict(runner.DEPS_COMMANDS,
                        {"npm": ["/bin/sh", "-c", f"pwd > {probe}"]}):
            self._release("v2.0.0-rc.1", marker="v2", contract={
                **CONTRACT, "deps": {"kind": "npm"}})
            rc, out = self._canary("v2.0.0-rc.1")
            self.assertEqual(rc, 0, out)
            self.assertEqual(probe.read_text().strip(),
                             str(self._canary_tree().resolve()))
            # The production path must still resolve its own tree when nobody names one —
            # this change made `workdir` a parameter, and a parameter with a broken default
            # breaks the caller that never passes it.
            probe.unlink()
            self._release("v3.0.0", marker="v3", contract={
                **CONTRACT, "deps": {"kind": "npm"}})
            self._deploy()
            self.assertEqual(probe.read_text().strip(), str(self._tree().resolve()))

    def test_status_prints_the_canary_verdict(self):
        """A verdict written to a file nobody prints is not a verdict."""
        self._deploy()
        self._release("v2.0.0-rc.1", marker="v2")
        self._canary("v2.0.0-rc.1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.status(self.system)
        out = buf.getvalue()
        self.assertIn("canary", out)
        self.assertIn("v2.0.0-rc.1", out)
        self.assertIn("PASSED", out)


@requires_macos("plutil")
class TheCanaryRestartsNoUnitTest(AutoCutoverCase):
    """WI-0228's remaining acceptance clause, and the one the rest of this suite
    structurally cannot make: every other contract here declares no units, so "restarted
    nothing" is true of them for free. This fixture models launchd and records every
    `launchctl` command issued, so the claim is measured against a system that IS cut over
    and WOULD be restarted by a deploy."""

    def test_a_canary_issues_no_launchctl_command_at_all(self):
        self._ship_plist()
        self._set_cutover("auto")
        self._release("v1.1.0", contract=self._contract(), marker="v1.1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.deploy(self.system)
        self.assertTrue(self.loaded, "the fixture must have a live unit to protect")
        loaded_before = dict(self.loaded)
        tag_before = runner.deployed_tag(self.system, {"subdir": None})

        self._release("v2.0.0-rc.1", contract=self._contract(), marker="v2")
        self.launchctl.clear()
        with redirect_stdout(io.StringIO()):
            rc = runner.canary(self.system, "v2.0.0-rc.1")

        self.assertEqual(rc, 0)
        self.assertEqual(self.launchctl, [],
                         "a canary that talks to launchctl at all has already lost")
        self.assertEqual(self.loaded, loaded_before)
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), tag_before)
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1.1")


class TheRenderedUnitCarriesTheOperatorsBinTest(DeployRunnerCase):
    """WI-0395. A member's release can pass its smoke gate on the runner host and then refuse
    at apply: `example-verb is not on PATH on this machine`, even with the verb installed
    at `~/.local/bin/example-verb`. The sweep runs under `com.federation.deploy-sweep`,
    whose PATH would otherwise be launchd's system default with no per-user bin directory,
    and a member's contract resolves such a verb on PATH by design (ADR-0103 D8 shape 2)
    because a machine path in a tracked contract is WI-0132's defect. Contract right,
    verb right, unit wrong — and the only way through is a human running apply by hand.

    Both halves are asserted here, because either alone is satisfiable by a broken system:
    that the operator's directory arrives from the RENDERER (so a regen reproduces it and
    no tracked file names a home directory), and that everything the template declared
    survives it.
    """

    UNIT = "com.federation.deploy-sweep"

    def _render(self, unit=None, home=None):
        """Render a shipped template into a throwaway deploy tree under a chosen HOME."""
        unit = unit or self.UNIT
        tree = self.root / "render" / unit
        (tree / "deploy").mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(runner.REPO_ROOT) / "deploy" / f"{unit}.plist.template",
                    tree / "deploy")
        home = home or (self.root / "operator-home")
        with patch.object(runner, "work_dir", return_value=tree), \
             patch.object(runner.common, "this_machine",
                          return_value=declared_host_of(unit)), \
             patch.dict(os.environ, {"HOME": str(home)}):
            out = runner.render_unit_plist("federation", {}, unit, lambda *_: None)
        self.assertIsNotNone(out, f"{unit} rendered nothing")
        return plistlib.loads(out.read_bytes()), home, out

    def _template_path(self, unit=None):
        """The PATH the TRACKED template declares, before any rendering."""
        unit = unit or self.UNIT
        template = Path(runner.REPO_ROOT) / "deploy" / f"{unit}.plist.template"
        declared = plistlib.loads(template.read_bytes())
        return declared["EnvironmentVariables"]["PATH"].split(":")

    def test_the_rendered_path_leads_with_the_operators_own_bin(self):
        document, home, _ = self._render()
        entries = document["EnvironmentVariables"]["PATH"].split(":")
        self.assertEqual(entries[0], str(home / ".local/bin"))

    def test_every_directory_the_template_named_survives_in_order(self):
        """A PATH fix that drops `/usr/bin` trades one broken unit for a worse one."""
        document, _, _ = self._render()
        entries = document["EnvironmentVariables"]["PATH"].split(":")
        self.assertEqual(entries[1:], self._template_path())

    def test_the_directory_comes_from_the_renderer_not_from_the_tracked_file(self):
        """The WI-0132 half. Rendering under two different HOMEs must give two different
        answers — which a committed absolute path could not do, and which is the whole
        reason this is not a literal in the template."""
        first, home_a, _ = self._render(home=self.root / "home-a")
        second, home_b, _ = self._render(home=self.root / "home-b")
        self.assertNotEqual(first["EnvironmentVariables"]["PATH"],
                            second["EnvironmentVariables"]["PATH"])
        self.assertTrue(
            first["EnvironmentVariables"]["PATH"].startswith(str(home_a)))
        self.assertTrue(
            second["EnvironmentVariables"]["PATH"].startswith(str(home_b)))

    def test_the_mail_poller_gets_it_too_and_not_only_the_sweep(self):
        """The sweep is where it was observed; the poller is what actually calls the
        runner's `unattended` verb since WI-0316, so fixing only the unit named in the
        brief would fix the unit that no longer runs the deploy
        ([`retire-the-class-not-the-instance`])."""
        document, home, _ = self._render(unit="com.federation.mail-poller")
        self.assertEqual(
            document["EnvironmentVariables"]["PATH"].split(":")[0],
            str(home / ".local/bin"))

    def test_a_unit_declaring_no_path_is_left_without_one(self):
        """Prepending to a unit that declares no PATH would REPLACE launchd's own minimal
        PATH with a single directory — taking away `/usr/bin` to add `~/.local/bin`."""
        text = plistlib.dumps({"Label": "com.example.quiet",
                               "ProgramArguments": ["/bin/true"]}).decode("utf-8")
        out = runner._prepend_operator_bin(text)
        self.assertNotIn("PATH", plistlib.loads(out.encode("utf-8"))
                         .get("EnvironmentVariables", {}))

    def test_rendering_the_same_unit_twice_does_not_stack_the_directory(self):
        """Every cutover re-renders. A prepend that is not idempotent grows a PATH by one
        entry per deploy, and nothing would ever report it.

        Both passes run under the SAME home, which is the claim: idempotent for one
        machine. Under a different HOME it correctly writes a different directory — the
        first cut of this test failed exactly there, having left the patched environment
        before the second pass."""
        home = self.root / "operator-home"
        document, _, _ = self._render(home=home)
        once = document["EnvironmentVariables"]["PATH"]
        with patch.dict(os.environ, {"HOME": str(home)}):
            twice = runner._prepend_operator_bin(
                plistlib.dumps(document).decode("utf-8"))
        self.assertEqual(plistlib.loads(twice.encode("utf-8"))
                         ["EnvironmentVariables"]["PATH"], once)
        self.assertEqual(once.count(str(home / ".local/bin")), 1)

    def test_an_unparseable_plist_is_left_for_plutil_to_refuse(self):
        """`_unsubstituted_tokens` already leaves that verdict to `plutil`, which answers
        it with a line number. Raising here would convert a linted refusal into an
        unhelpful "could not render"."""
        broken = "<plist><dict><key>Label</key>"
        self.assertEqual(runner._prepend_operator_bin(broken), broken)

    def test_the_render_receipt_says_what_path_it_wrote(self):
        """A rendered PATH nobody printed is the state this defect lived in for five
        days ([`capture-the-probe`])."""
        tree = self.root / "receipt"
        (tree / "deploy").mkdir(parents=True)
        shutil.copy(Path(runner.REPO_ROOT) / "deploy" / f"{self.UNIT}.plist.template",
                    tree / "deploy")
        said = []
        with patch.object(runner, "work_dir", return_value=tree), \
             patch.object(runner.common, "this_machine",
                          return_value=declared_host_of(self.UNIT)), \
             patch.dict(os.environ, {"HOME": str(self.root / "receipt-home")}):
            runner.render_unit_plist("federation", {}, self.UNIT, said.append)
        self.assertTrue(any("PATH=" in line for line in said), said)
        self.assertTrue(any(str(self.root / "receipt-home" / ".local/bin") in line
                            for line in said), said)


class TheContractVerbsAreResolvedBeforeTheCheckoutTest(DeployRunnerCase):
    """WI-0395 half two. A verb the machine cannot resolve fails the step that needs it
    whatever anyone does; the only question is whether it fails with the tree still on the
    running version, or after the fetch, the checkout, the state seed, the dependency sync
    and the smoke gate. For `verify` it is worse than late — a verify that cannot RUN
    reads exactly like a verify that FAILED, and the answer to a failed verify is to roll
    a healthy deploy back."""

    MISSING = "wi0395-no-such-verb"

    def _release_with(self, key, cmd, tag="v1.1.0"):
        self._release(tag, contract={**CONTRACT, key: {"cmd": cmd}}, marker="v1.1")

    def test_an_unresolvable_apply_verb_refuses_before_anything_is_checked_out(self):
        self._deploy()
        self._release_with("apply", [self.MISSING, "go"])
        with self.assertRaises(runner.DeployError) as caught:
            self._deploy()
        self.assertIn(self.MISSING, str(caught.exception))
        self.assertIn("apply", str(caught.exception))
        # Ground truth, not the ledger: the tree never moved and v1.0.0 still runs.
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        self.assertEqual(runner.deployed_tag(self.system, {"subdir": None}), "v1.0.0")

    def test_an_unresolvable_verify_verb_costs_no_rollback_of_a_healthy_deploy(self):
        self._deploy()
        before = self._ledger()
        self._release_with("verify", [self.MISSING])
        with self.assertRaises(runner.DeployError) as caught:
            self._deploy()
        self.assertIn("verify", str(caught.exception))
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n")
        self.assertEqual(self._ledger().get("current"), before.get("current"))
        self.assertNotIn(self._ledger().get("status"), ("rolled-back", "verify-failed"))

    def test_the_refusal_names_the_verb_the_key_and_the_path_it_searched(self):
        """Three facts, because a refusal missing any one of them sends the reader back
        to the machine: WHAT could not be found, WHICH step wanted it, and WHAT was
        searched. The third is the one whose absence cost the 09-18 incident hours."""
        self._deploy()
        self._release_with("apply", [self.MISSING])
        with self.assertRaises(runner.DeployError) as caught:
            self._deploy()
        message = str(caught.exception)
        self.assertIn(self.MISSING, message)
        self.assertIn("apply", message)
        self.assertIn(os.environ.get("PATH", ""), message)

    def test_a_relative_script_the_new_tag_adds_is_not_refused(self):
        """The false-positive control, and the reason an argv[0] holding a separator is
        deferred rather than resolved: `deploy/apply.sh` is not in the tree yet — the
        checkout is what puts it there — so resolving it here would refuse every tag that
        ships its own apply script. A guard that fires on correct code gets deleted."""
        self._deploy()
        script = self.upstream / "deploy" / "apply.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        self._release_with("apply", ["/bin/sh", "deploy/apply.sh"])
        rc, out = self._deploy()
        self.assertEqual(rc, 0, out)
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1.1")

    def test_what_it_could_not_check_is_its_own_answer_in_the_log(self):
        """[`declare-what-a-check-assumes`] — "couldn't tell" must not print as
        "checked and fine". The fixture contract's own gates are `/bin/sh ...`, which this
        check defers, and the log has to say so rather than report a clean preflight."""
        self._deploy()
        self._release_with("apply", ["/bin/sh", "-c", "exit 0"])
        _, out = self._deploy()
        self.assertIn("preflight:", out)
        self.assertIn("not a PATH question", out)

    def test_the_keys_it_checks_are_every_contract_key_that_carries_a_cmd(self):
        """[`derive-a-checks-subjects-from-the-authority`]. The subject list comes from
        the schema, so a fourth command key added there fails this test instead of being
        silently skipped by a preflight that still remembers three."""
        schema = json.loads(
            (Path(runner.REPO_ROOT) / "deploy" / "contract.schema.json")
            .read_text(encoding="utf-8"))
        carry_cmd = {name for name, spec in schema["properties"].items()
                     if isinstance(spec, dict) and "cmd" in (spec.get("properties") or {})}
        self.assertEqual(carry_cmd, set(runner.COMMAND_KEYS))

    def test_a_dry_run_refuses_too_rather_than_reporting_a_clean_plan(self):
        """A dry run exists to answer "what would happen?". Answering "it would deploy"
        when the apply verb is absent is the false-green this whole item is about."""
        self._deploy()
        self._release_with("apply", [self.MISSING])
        with self.assertRaises(runner.DeployError):
            self._deploy(dry_run=True)


class AnUnexecutableContractCommandIsARefusalNotADeadSweepTest(DeployRunnerCase):
    """The consequence WI-0395 turned out to be hiding, found by deleting the preflight
    and reading what took its place: a `subprocess` whose argv[0] does not exist RAISES
    rather than returning a code, and `sweep()` catches `DeployError` and `RollbackFailed`
    only. So one member declaring a verb this machine cannot start did not refuse that
    member — it threw a bare `FileNotFoundError` out of the per-system loop and took
    every LATER system with it, the self row last of all.

    `refuse_unresolvable_verbs` catches the PATH-resolved shape before the checkout. This
    is the shape it deliberately defers: an argv[0] naming a path, which only the checkout
    can settle. It must still be one system's refusal."""

    def _release_unexecutable(self, key="apply", tag="v1.1.0"):
        """A tag whose `key` command names a script inside the tree that it does not
        actually ship — path-shaped, so the preflight defers it by design."""
        self._release(tag, marker="v1.1",
                      contract={**CONTRACT, key: {"cmd": ["deploy/not-shipped.sh"]}})

    def test_an_apply_that_cannot_start_refuses_this_system_only(self):
        self._deploy()
        self._release_unexecutable()
        with self.assertRaises(runner.DeployError) as caught:
            self._deploy()
        message = str(caught.exception)
        self.assertIn("not-shipped.sh", message)
        self.assertIn("could not start", message)

    def test_the_sweep_survives_it_and_still_deploys_the_others(self):
        """The property that actually matters. A second system is registered AFTER the
        broken one alphabetically, and the sweep has to reach it."""
        self._deploy()
        later = self.root / "zeta"
        later.mkdir()
        _git(["init", "-b", "main"], later)
        (later / "deploy").mkdir()
        (later / "deploy" / "deploy.json").write_text(
            json.dumps({**CONTRACT, "system": "zeta"}, indent=2))
        _git(["add", "-A"], later)
        _git(["commit", "-m", "c"], later)
        _git(["tag", "v1.0.0"], later)
        reg = json.loads(self.registry.read_text())
        reg["systems"]["zeta"] = {"remote": str(later), "subdir": None}
        self.registry.write_text(json.dumps(reg))
        self.mailboxes.write_text(json.dumps({"members": {
            self.system: {"architect_id": f"{self.system}-arch"},
            "zeta": {"architect_id": "zeta-arch"}}}))
        self._release_unexecutable()

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.sweep()
        out = buf.getvalue()
        self.assertEqual(rc, 1, out)
        self.assertIn("widget: REFUSED", out)
        self.assertEqual(runner.read_ledger("zeta").get("current"), "v1.0.0",
                         "the system after the broken one was never reached")

    def test_a_smoke_gate_that_cannot_start_is_recorded_as_unrunnable_not_as_a_verdict(self):
        """"Could not run" and "ran and said no" must not print the same
        ([`declare-what-a-check-assumes`]) — and neither may crash."""
        self._deploy()
        self._release_unexecutable(key="smoke")
        rc, out = self._deploy()
        self.assertEqual(rc, 1)
        smoke = self._ledger().get("smoke") or {}
        self.assertIs(smoke.get("ok"), False)
        self.assertIn("could not execute", smoke.get("detail", ""))
        self.assertEqual((self._tree() / "marker.txt").read_text(), "v1\n",
                         "a gate that could not run must still put the tree back")


class TheApplyFailureCarriesThePathItSearchedTest(DeployRunnerCase):
    """The shape `refuse_unresolvable_verbs` structurally CANNOT preflight, and the reason
    it must not be described as if it could: a member declared `/bin/sh deploy/apply.sh`,
    and the name that could not be found — `example-verb` — was inside that script.
    The script refused correctly and by name; what nobody could see was the PATH it was
    refused against."""

    def test_an_apply_that_refuses_on_path_reports_the_path(self):
        self._deploy()
        script = self.upstream / "deploy" / "apply.sh"
        script.write_text(
            "#!/bin/sh\n"
            "echo 'apply: example-verb is not on PATH on this machine "
            "- refusing' >&2\nexit 1\n")
        script.chmod(0o755)
        self._release("v1.1.0", marker="v1.1",
                      contract={**CONTRACT,
                                "apply": {"cmd": ["/bin/sh", "deploy/apply.sh"]}})
        with self.assertRaises(runner.DeployError) as caught:
            self._deploy()
        message = str(caught.exception)
        self.assertIn("is not on PATH on this machine", message)
        self.assertIn(os.environ.get("PATH", ""), message)


if __name__ == "__main__":
    unittest.main()


class AUnitRendersOnlyOnTheMachineItsTemplateNamesTest(DeployRunnerCase):
    """WI-0420. A cutover on the runner host could install `com.federation.gate-inputs` — a
    unit meant to run as a LaunchDaemon on the development host — as a LaunchAgent at `~/Library/LaunchAgents`
    instead, where it could never fire against the sealed deploy tree.

    THE FACT WAS ALREADY IN THE FILE. The template header has read "THIS UNIT IS
    DEVBOX'S, AND THAT IS THE POINT" since it was written. It was a comment, so nothing
    read it, and the renderer rendered whatever it found. That is the shape of the defect:
    a true statement in prose and a renderer with no way to act on it.

    Sharpened by a fact from the same reading: `launchctl_domain()` returns `gui/<uid>`
    unconditionally and `launch_agents_dir()` is the only install destination this runner
    has, so it could not have installed that daemon CORRECTLY even had it tried. There was
    never a right answer for it here -- only "not here".
    """

    ALL = ["com.federation.deploy-sweep", "com.federation.mail-poller",
           "com.federation.adopt-runner", "com.federation.gate-inputs"]
    DEVBOX_ONLY = "com.federation.gate-inputs"
    RUNNER_ONLY = "com.federation.deploy-sweep"
    EVERYWHERE = "com.federation.mail-poller"

    def setUp(self):
        super().setUp()
        self.tree = self.root / "host-scoped"
        (self.tree / "deploy").mkdir(parents=True)
        for unit in self.ALL:
            shutil.copy(Path(runner.REPO_ROOT) / "deploy" / f"{unit}.plist.template",
                        self.tree / "deploy")
        self.said = []
        patcher = patch.object(runner, "work_dir", return_value=self.tree)
        patcher.start()
        self.addCleanup(patcher.stop)

    def as_machine(self, label):
        return patch.object(runner.common, "this_machine", return_value=label)

    def units_on(self, machine, units=None):
        with self.as_machine(machine):
            return runner.units_for_this_host("federation", {},
                                              {"units": list(self.ALL if units is None
                                                             else units)},
                                              self.said.append)

    def template(self, unit):
        return self.tree / "deploy" / f"{unit}.plist.template"

    def strip_declaration(self, unit):
        path = self.template(unit)
        kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                if not runner.UNIT_HOST_DIRECTIVE.fullmatch(line.rstrip("\n"))]
        path.write_text("".join(kept), encoding="utf-8")
        self.assertEqual(runner.UNIT_HOST_DIRECTIVE.findall(path.read_text()), [])
        return path

    # ── the acceptance, stated as the item states it ─────────────────────────────

    def test_the_runners_cutover_does_not_touch_the_devbox_unit(self):
        """The item's first acceptance line, and the one the Runner can check on its next run."""
        self.assertNotIn(self.DEVBOX_ONLY, self.units_on("Runner"))

    def test_a_devbox_only_template_is_skipped_when_the_cutover_runs_as_the_runner(self):
        mine = self.units_on("Runner")
        self.assertEqual(mine, ["com.federation.deploy-sweep", "com.federation.mail-poller",
                                "com.federation.adopt-runner"])
        self.assertTrue(any("belong to another machine" in line for line in self.said),
                        self.said)
        self.assertTrue(any(self.DEVBOX_ONLY in line for line in self.said), self.said)

    def test_the_same_contract_on_devbox_keeps_the_devbox_unit_and_drops_the_runners(self):
        """The mirror, because a filter that returned the empty list would pass the test
        above for the wrong reason."""
        mine = self.units_on("DevBox")
        self.assertIn(self.DEVBOX_ONLY, mine)
        self.assertNotIn(self.RUNNER_ONLY, mine)
        self.assertIn(self.EVERYWHERE, mine)

    def test_a_template_with_no_host_declaration_is_refused_rather_than_rendered(self):
        """The item's second acceptance line. A refusal, not a skip: silently dropping an
        undeclared unit would make "nobody said" behave exactly like "not here", and the
        deploy would report itself finished having installed nothing."""
        self.strip_declaration(self.RUNNER_ONLY)
        with self.assertRaisesRegex(runner.DeployError,
                                    "does not declare which machine it belongs to"):
            self.units_on("Runner")

    def test_the_renderer_refuses_an_undeclared_template_without_raising(self):
        """`unit_plist_source` promises `finish_self_cutover` it never raises, and the
        detached finisher reaches the renderer without passing through the filter. So the
        same rule has to exist at both doors, in each door's own idiom."""
        self.strip_declaration(self.RUNNER_ONLY)
        with self.as_machine("Runner"):
            out = runner.render_unit_plist("federation", {}, self.RUNNER_ONLY,
                                           self.said.append)
        self.assertIsNone(out)
        self.assertTrue(any("REFUSING to render" in line for line in self.said), self.said)

    def test_the_renderer_refuses_another_machines_template(self):
        with self.as_machine("Runner"):
            out = runner.render_unit_plist("federation", {}, self.DEVBOX_ONLY,
                                           self.said.append)
        self.assertIsNone(out)
        self.assertTrue(any("declares host 'DevBox'" in line for line in self.said),
                        self.said)

    def test_the_renderer_renders_its_own_machines_template(self):
        """The control for the two refusals above: they must be refusing the host, not
        failing to render at all."""
        with self.as_machine("DevBox"):
            out = runner.render_unit_plist("federation", {}, self.DEVBOX_ONLY,
                                           self.said.append)
        self.assertIsNotNone(out)
        self.assertEqual(plistlib.loads(out.read_bytes())["Label"], self.DEVBOX_ONLY)

    # ── the declaration itself ───────────────────────────────────────────────────

    def test_every_shipped_template_declares_a_host(self):
        """The structural half. A template added later without a declaration fails HERE,
        at the point somebody is looking, rather than on the runner host in an unattended sweep."""
        templates = sorted((Path(runner.REPO_ROOT) / "deploy").glob("*.plist.template"))
        self.assertTrue(templates, "no unit templates found")
        for path in templates:
            with self.subTest(template=path.name):
                self.assertTrue(runner.declared_unit_host(path))

    def test_the_shipped_templates_declare_the_hosts_their_prose_already_claimed(self):
        expected = {"com.federation.gate-inputs": "DevBox",
                    "com.federation.deploy-sweep": "Runner",
                    "com.federation.adopt-runner": "Runner",
                    "com.federation.mail-poller": runner.UNIT_HOST_ANY}
        for unit, host in expected.items():
            with self.subTest(unit=unit):
                self.assertEqual(
                    runner.declared_unit_host(
                        Path(runner.REPO_ROOT) / "deploy" / f"{unit}.plist.template"),
                    host)

    def test_a_unit_declaring_any_belongs_on_every_machine(self):
        for machine in ("Runner", "DevBox", "Laptop"):
            with self.subTest(machine=machine):
                self.assertIn(self.EVERYWHERE, self.units_on(machine))

    def test_two_declarations_are_refused_as_hard_as_none(self):
        """Picking the first would make the file's meaning depend on its line order."""
        path = self.template(self.RUNNER_ONLY)
        path.write_text(path.read_text().replace("  poga-host: Runner\n",
                                                 "  poga-host: Runner\n  poga-host: DevBox\n"))
        with self.assertRaisesRegex(runner.DeployError, "declares 2 hosts"):
            runner.declared_unit_host(path)

    def test_a_misspelled_machine_is_a_typo_and_not_a_machine_that_is_elsewhere(self):
        """The failure this check exists for. Left to match nothing, `devhost` skips the
        unit on EVERY host, silently and forever -- a total outage that looks like a
        working filter."""
        path = self.template(self.DEVBOX_ONLY)
        path.write_text(path.read_text().replace("poga-host: DevBox", "poga-host: devhost"))
        with self.assertRaisesRegex(runner.DeployError, "not a machine this config knows"):
            runner.declared_unit_host(path)

    def test_the_declaration_is_case_insensitive_and_reports_the_canonical_label(self):
        path = self.template(self.DEVBOX_ONLY)
        path.write_text(path.read_text().replace("poga-host: DevBox", "poga-host: devbox"))
        self.assertEqual(runner.declared_unit_host(path), "DevBox")

    def test_prose_mentioning_the_directive_is_not_a_declaration(self):
        """gate-inputs' own header now explains the rule in a sentence containing the
        token. A regex that matched it would read two declarations and refuse the file
        that documents the feature."""
        text = self.template(self.DEVBOX_ONLY).read_text()
        self.assertIn("`poga-host:` line", text)
        self.assertEqual(len(runner.UNIT_HOST_DIRECTIVE.findall(text)), 1)

    # ── what it must not do ──────────────────────────────────────────────────────

    def test_a_unit_with_no_template_passes_through_untouched(self):
        """A member may ship a rendered `deploy/<label>.plist` and no template, or
        declare a unit with neither. Nothing renders them, so this rule has nothing to say
        about them, and a version that failed them would be a guard firing on correct code
        in other members."""
        self.assertEqual(self.units_on("Runner", ["com.example.app"]),
                         ["com.example.app"])

    def test_a_machine_that_cannot_name_itself_withholds_rather_than_installs(self):
        """`unattended_refusal` already rules that not knowing where you are standing is a
        reason to withhold an unattended deploy, never to grant one. Installing is the
        granting half."""
        with self.assertRaisesRegex(runner.DeployError, "cannot name itself"):
            self.units_on("")

    def test_an_empty_unit_list_stays_empty_and_asks_nothing(self):
        self.assertEqual(self.units_on("Runner", []), [])

    # ── the two enumeration sites ────────────────────────────────────────────────

    def test_cutover_state_does_not_report_another_machines_unit_as_unloaded(self):
        """Where the install decision is actually made. Before WI-0420 the devbox unit
        arrived here as `unloaded` -- truthfully, nothing on the Runner had it loaded --
        and the cutover dutifully installed it."""
        with self.as_machine("Runner"), \
             patch.object(runner, "unit_target", return_value=None):
            cut = runner.cutover_state("federation", {}, {"units": self.ALL},
                                       self.said.append)
        self.assertNotIn(self.DEVBOX_ONLY, cut["unloaded"])
        self.assertNotIn(self.DEVBOX_ONLY, [p["unit"] for p in cut["pending"]])
        self.assertEqual(cut["units"], self.units_on("Runner"))

    def test_restart_units_does_not_kick_another_machines_label(self):
        """The second site, and the one that had no host awareness at all: it walked
        `contract["units"]` straight through. A kickstart against a label this host should
        never have had is the same defect with a different verb."""
        kicked = []

        def fake_run(cmd, **kwargs):
            kicked.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with self.as_machine("Runner"), patch.object(runner, "run", fake_run):
            runner.restart_units("federation", {},
                                 {"units": self.ALL, "restart": "kickstart"},
                                 self.said.append, False)
        labels = [c[-1] for c in kicked]
        self.assertTrue(labels, "nothing was kicked at all")
        self.assertFalse([l for l in labels if self.DEVBOX_ONLY in l], labels)
        self.assertTrue([l for l in labels if self.RUNNER_ONLY in l], labels)


class TheSuiteIsBlindToTheLaunchdLabelItRunsUnderTest(unittest.TestCase):
    """WI-0432. The nightly serial run is a launchd job, so every process it starts carries
    `XPC_SERVICE_NAME=com.federation.gate-inputs`. The deploy fixtures inherited it, the
    runner's witness answered "I am a unit", and 71 deploy tests plus one in test_diagnose
    went red on 2026-09-27 — on the only run that has that variable, and on no shell run.

    So this runs REAL sweep tests from both fixture roots under a foreign label, and — the
    negative control — runs them again with the clear taken out of `CLEARED_ENV`, which
    must turn them red. Without the second half this would pass on a machine where the
    label no longer changes anything, and prove nothing about the clear."""

    FOREIGN = "com.federation.gate-inputs"

    def setUp(self):
        # PINNED to "a working clone". `channel.is_deploy_tree` reads the HOST checkout's
        # HEAD, and the post-land trunk check runs the suite in a DETACHED scratch
        # worktree — which reads as a sealed deploy tree, sends the sample sweeps down
        # another path, and let the negative control pass without the clear. Red on every
        # trunk check from 2026-09-28 while green in every lane.
        pin = patch.object(runner.channel, "is_deploy_tree", lambda repo: False)
        pin.start()
        self.addCleanup(pin.stop)

    def _samples(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_deploy_self                                      # noqa: E402
        return test_deploy_self, [
            TestSweepAndStatus("test_sweep_deploys_every_registered_system"),
            test_deploy_self.TestTheSelfSystemIsSweptLast(
                "test_a_real_sweep_runs_the_self_system_last"),
        ]

    @staticmethod
    def _run(case):
        result = unittest.TestResult()
        with redirect_stdout(io.StringIO()):
            case.run(result)
        return result

    def test_a_real_sweep_passes_under_a_foreign_launchd_label(self):
        _, samples = self._samples()
        with patch.dict(os.environ, {"XPC_SERVICE_NAME": self.FOREIGN}):
            for case in samples:
                result = self._run(case)
                self.assertTrue(result.wasSuccessful(), (
                    f"{case.id()} fails under XPC_SERVICE_NAME={self.FOREIGN}: "
                    f"{[t for _, t in result.failures + result.errors]}"))

    def test_the_same_sweep_fails_when_the_fixture_stops_clearing_the_label(self):
        self_mod, samples = self._samples()
        with patch.dict(os.environ, {"XPC_SERVICE_NAME": self.FOREIGN}), \
             patch.dict(CLEARED_ENV), patch.dict(self_mod.CLEARED_ENV):
            CLEARED_ENV.pop("XPC_SERVICE_NAME", None)
            self_mod.CLEARED_ENV.pop("XPC_SERVICE_NAME", None)
            for case in samples:
                self.assertFalse(self._run(case).wasSuccessful(), (
                    f"{case.id()} passed with the ambient label left in place, so the "
                    f"guard above no longer shows the clear is what makes it pass"))
