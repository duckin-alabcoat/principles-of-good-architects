"""The federation's move onto external state, rehearsed against isolated installations (WI-0365).

Every fixture here builds a whole fake installation in a temporary directory: its own
release checkout at a real tag, its own state and config roots, its own LaunchAgents
directory, its own `poga` symlink, and its own registry. Nothing reaches the operator's
machine.

`launchctl` IS STUBBED, AND THAT IS A SAFETY PROPERTY RATHER THAN A CONVENIENCE. The
migration boots out and bootstraps jobs by label, and the labels it uses are the real
federation's. A suite that let those calls through would boot out the running federation
on whatever machine ran the tests — the sharper version of the hazard that made every
other path in this program redirectable. The stub records what was asked, so the tests can
assert the ORDER of bootout and bootstrap, which is what makes "never two writers" a
checkable claim instead of a hope.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
sys.path.insert(0, str(REPO / "curate"))
import runner                                                        # noqa: E402
import migrate                                                       # noqa: E402
import channel                                                       # noqa: E402
import production                                                    # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures
from macos_only import requires_macos  # noqa: E402  WI-0468

#: Cleared rather than redirected, for `CLEARED_ENV`'s reason in the deploy suite: its
#: ABSENCE is what keeps the rest of the process on the development path while these
#: tests drive production explicitly through an argument.
CLEARED = ("POGA_FEDERATION_CONFIG",)

UNITS = ["com.federation.deploy-sweep", "com.federation.mail-poller",
         "com.federation.adopt-runner"]


def _git(args, cwd, check=True):
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
         *args],
        cwd=str(cwd), capture_output=True, text=True, check=check)


def _no_background_git(repo):
    """No detached gc/maintenance in a fixture repo, written into ITS OWN config.

    `_git`'s `-c` flags do not reach the far side of a local push: git clears
    `GIT_CONFIG_*` before it starts `receive-pack` in the target, which then reads only
    the target's config — `receive.autogc` on, `gc.autoDetach` on. A test that refuses
    early tears down right after setUp's push, racing that detached gc
    ("Directory not empty: 'info'" / "'objects'" / "'remote.git'", 2026-09-29)."""
    for key, value in (("gc.auto", "0"), ("maintenance.auto", "false"),
                       ("gc.autoDetach", "false"), ("receive.autogc", "false")):
        _git(["config", key, value], repo)


class FakeLaunchctl:
    """A launchd that records instead of acting, and answers `print` from its own state."""

    def __init__(self, loaded=None):
        self.calls = []
        self.loaded = dict(loaded or {})

    def __call__(self, cmd, cwd=None, timeout=120, **kw):
        if not cmd or Path(str(cmd[0])).name != "launchctl":
            return FakeLaunchctl._real(cmd, cwd=cwd, timeout=timeout, **kw)
        self.calls.append(list(cmd))
        verb = cmd[1]
        # `bootout`/`print`/`enable` take `<domain>/<label>`; `bootstrap` takes
        # `<domain> <plist path>`. Keying both off the last argument stored the job under
        # "com.federation.x.plist", so `unit_target` found nothing afterwards and every
        # rebound unit read as NOT LOADED — which made two real regression tests fail for
        # a reason that was entirely this fixture's.
        label = cmd[-1].rsplit("/", 1)[-1]
        if label.endswith(".plist"):
            label = label[: -len(".plist")]
        if verb == "print":
            if label not in self.loaded:
                return subprocess.CompletedProcess(cmd, 1, "", "not found")
            return subprocess.CompletedProcess(
                cmd, 0, f"\tpath = {self.loaded[label]['plist']}\n"
                        f"\tworking directory = {self.loaded[label]['wd']}\n", "")
        if verb == "bootout":
            self.loaded.pop(label, None)
        if verb == "bootstrap":
            self.loaded[label] = {"plist": cmd[-1], "wd": self._wd_of(cmd[-1])}
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @staticmethod
    def _wd_of(path):
        try:
            return plistlib.loads(Path(path).read_bytes()).get("WorkingDirectory", "")
        except (OSError, ValueError, plistlib.InvalidFileException):
            return ""

    def verbs(self, label):
        return [c[1] for c in self.calls if c[-1].endswith(label) or label in str(c)]


FakeLaunchctl._real = staticmethod(runner.run)


class MigrationCase(unittest.TestCase):
    """One isolated installation: a detached release, in-tree state, and a fake scheduler."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="federation-migration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.remote = self.root / "remote.git"
        self.remote.mkdir()
        _git(["init", "--bare", "-q", "-b", "main"], self.remote)
        _no_background_git(self.remote)

        self.deploy_root = self.root / "deploy-trees"
        self.release = self.deploy_root / "federation"
        self._build_release()

        self.agents = self.root / "LaunchAgents"
        self.agents.mkdir()
        self.ledger = self.root / "ledger"
        self.service = self.root / "service.json"
        self.state = self.root / "state"
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({
            "contract_version": 1,
            "systems": {"federation": {"remote": str(self.remote), "subdir": None,
                                       "self": True, "cutover": "auto"}}}))
        self.cli = self.root / "bin" / "poga"
        self.cli.parent.mkdir(parents=True)
        self.old_cli_target = self.root / "old-checkout" / "poga"
        self.old_cli_target.parent.mkdir(parents=True)
        self.old_cli_target.write_text("#!/bin/sh\n")
        self.cli.symlink_to(self.old_cli_target)

        self._env = {
            # REDIRECTED, AND IT IS NOT DEFENCE IN DEPTH — it is the path this suite
            # already took. `author_config` derives its default `state_root` as
            # `$HOME/.local/state/poga/federation`, so a fixture that left HOME alone
            # provisioned the operator's real home and imported the real repository's
            # comms notes and outbox into it. Measured: 83 files, on the first run.
            # `production.resolve` reads `env["HOME"]` for the same default, and
            # `Path.home()` reads it too, so one redirect covers both.
            "HOME": str(self.root / "home"),
            "POGA_DEPLOY_REGISTRY": str(self.registry),
            "POGA_DEPLOY_ROOT": str(self.deploy_root),
            "POGA_DEPLOY_LEDGER_DIR": str(self.ledger),
            "POGA_DEPLOY_LAUNCH_AGENTS": str(self.agents),
            "POGA_DEPLOY_COMMS_DIR": str(self.root / "comms-out"),
            "POGA_DEPLOY_OUTBOX_DIR": str(self.root / "outbox-out"),
            "POGA_DEPLOY_MAILBOXES": str(self.release / "mailboxes.json"),
            "POGA_CHANNEL_ROOT": str(self.root / "federation-channel"),
            "POGA_CLI_LINK": str(self.cli),
            "POGA_FEDERATION_SERVICE_CONFIG": str(self.service),
        }
        (self.root / "home").mkdir()
        # CAPTURED ONCE PER TEST, NOT ONCE PER setUp CALL, and the difference is a red
        # trunk. `test_each_handoff_boundary_resumes_and_converges` calls `self.setUp()`
        # again inside a loop to get a fresh fixture per phase. Re-capturing here on the
        # second call saves an environment this fixture has ALREADY mutated -- and one
        # the migration under test has since pointed at a tempdir of its own -- so
        # `_restore` then faithfully re-installs that value at the end of the test. The
        # tempdir is gone by then, so every module imported afterwards that resolves the
        # production config at import time raises on a path that cannot be read.
        #
        # Measured, not reasoned about: it left `POGA_FEDERATION_CONFIG` pointing at a
        # deleted `federation-migration-*/service.json` and cost 80 errors in the SERIAL
        # suite, which is the only run that sees it -- each shard of the sharded runner is
        # a fresh interpreter, so the gate is structurally blind to it. That in turn
        # failed the nightly derive, and a derive that cannot complete disables the
        # neutral-skip, so every land on the fleet pays a full validation.
        #
        # The key set is identical on every call, so capturing once captures the true
        # pre-test environment; re-registering `_restore` as a cleanup is then harmless
        # because each registration restores the same correct baseline.
        if not hasattr(self, "_saved"):
            self._saved = {k: os.environ.get(k) for k in (*self._env, *CLEARED)}
        os.environ.update(self._env)
        for key in CLEARED:
            os.environ.pop(key, None)
        self.addCleanup(self._restore)
        # AUTHORED, so `author_config` takes its "an existing file is used as authored"
        # path and every root in this suite is one the fixture chose. The derivation is
        # exercised on its own, against a redirected HOME, in `ADerivedConfiguration`.
        #
        # ITS REFS ARE DELIBERATELY NOT THE CHANNEL BRANCH. They were `runner/mail` until
        # WI-0418 -- the ref `curate/channel.py` writes a full tree to and the one the
        # transport refuses. Under the upgrade this fixture would take the "upgraded" path
        # instead of the "authored" one it exists to exercise, and thirty tests would
        # silently run against refs the fixture did not choose.
        self.service.write_text(json.dumps({
            "schema_version": 1,
            "code_root": str(self.release),
            "state_root": str(self.state),
            "config_root": str(self.state / "config"),
            "transport_root": str(self.root / "transport.git"),
            "transport": {"remote": str(self.remote),
                          "owned_ref": "refs/heads/runner/messages",
                          "input_refs": ["refs/heads/dev/messages"]},
        }))

        self.launchctl = FakeLaunchctl(loaded={
            unit: {"plist": str(self.agents / f"{unit}.plist"), "wd": str(self.release)}
            for unit in UNITS})
        for unit in UNITS:
            self._install_unbound(unit)
        # AS THE RUNNER, because that is the machine this whole suite models: the
        # federation's transition onto external state happens there, and `UNITS` above is
        # the Runner's three. Since WI-0420 made rendering host-scoped, leaving the host to
        # whatever box runs the suite meant `deploy-sweep` and `adopt-runner` -- both
        # declaring `Runner` -- refused to render on devbox and `rebind` raised. A fixture
        # that does not say which machine it is asks the ambient environment a question.
        machine = patch.object(runner.common, "this_machine", return_value="Runner")
        machine.start()
        self.addCleanup(machine.stop)
        # AND WITH A `claude` OF ITS OWN, for the same reason. The adopt-runner template
        # carries `__CLAUDE_BIN__`, which `render_unit_plist` fills from
        # `shutil.which("claude")` and REFUSES to render without. Left to the ambient PATH,
        # this suite passed in an interactive shell (`claude` on PATH) and failed 39
        # tests under the nightly's daemon PATH, where `rebind` raised "no installable
        # plist" for the adopt-runner. The two tests that want `claude` absent still patch
        # `which` themselves, over this.
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_claude.chmod(0o755)
        real_which = shutil.which
        claude = patch.object(
            runner.shutil, "which",
            side_effect=lambda name, *a, **kw: (str(fake_claude) if name == "claude"
                                                else real_which(name, *a, **kw)))
        claude.start()
        self.addCleanup(claude.stop)
        patcher = patch.object(runner, "run", self.launchctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        # PINNED, AND THIS IS THE MOST IMPORTANT LINE IN THE FIXTURE. `runner.REPO_ROOT`
        # is frozen at import to the checkout the module was loaded from — this repo. Left
        # alone, `retiring_root`'s `self`-row fallback answers with the REAL federation
        # checkout (a lane is not a deploy tree), and the migration then seeds its
        # configuration and imports its mail out of the operator's own working clone. That
        # is not hypothetical: it is what the first run of this suite did, and it is why
        # three tests passed that should have refused and one failed for the right reason
        # by accident.
        root_patch = patch.object(runner, "REPO_ROOT", self.release)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # -- the installation ------------------------------------------------------

    def _build_release(self):
        """A real detached checkout at a real tag, carrying the modules under test."""
        self.release.mkdir(parents=True)
        _git(["init", "-q", "-b", "main"], self.release)
        _no_background_git(self.release)
        for relative in ("curate", "sessionlib"):
            source = REPO / relative
            if source.is_dir():
                shutil.copytree(source, self.release / relative,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.release / "deploy").mkdir(exist_ok=True)
        for name in ("runner.py", "migrate.py", "contract.schema.json"):
            shutil.copyfile(REPO / "deploy" / name, self.release / "deploy" / name)
        for template in (REPO / "deploy").glob("com.federation.*.plist.template"):
            shutil.copyfile(template, self.release / "deploy" / template.name)
        (self.release / "deploy" / "deploy.json").write_text(json.dumps({
            "contract_version": 1, "system": "federation", "units": UNITS,
            "restart": "none", "deps": {"kind": "none"},
            "smoke": {"cmd": ["/bin/sh", "-c", "exit 0"]},
            "verify": {"cmd": ["/bin/sh", "-c", "exit 0"], "settle_seconds": 0},
        }, indent=2))
        # The machine-local configuration a deploy that reached `deployed` must have
        # seeded into the tree, and the authored content the migration must carry across
        # byte for byte rather than regenerate.
        (self.release / "mailboxes.json").write_text(json.dumps(
            {"members": {"runner-test": {"architect_id": "runner-test-arch"}}}))
        (self.release / "repo-paths.local").write_text("runner-test = /somewhere/real\n")
        (self.release / "reconcile-roots.local").write_text("/somewhere/real\n")
        # In-tree state the units have been writing since the cutover.
        session_state = self.release / ".session-state"
        session_state.mkdir()
        (session_state / "adopt-runner.briefs.json").write_text(
            json.dumps({"quarantined": ["2026-09-01-a-brief"], "attempts": {"x": 3}}))
        (session_state / "adopt-runner.status").write_text("last run: clean\n")
        (session_state / "mail-poller.status.json").write_text(json.dumps({"runs": 41}))
        box = self.release / "outbox" / "to-runner-test-arch"
        box.mkdir(parents=True)
        self.queued = box / "2026-09-14-a-queued-brief.md"
        self.queued.write_text("---\nedit-id: 2026-09-14-a\napply: manual\n"
                               "manual-reason: attended\n---\n\nA queued fixture brief.\n")
        comms = self.release / "comms"
        comms.mkdir()
        (comms / "2026-09-15-an-unread-escalation.md").write_text(
            "# Something needed attention and nobody read it\n")
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "fixture release"], self.release)
        _git(["tag", "v1.0.0"], self.release)
        # A REAL DEPLOY TREE HAS AN ORIGIN, and the runner fetches from it before it
        # resolves a target. Without this the fixture's tree is a clone of nothing and
        # every deploy stops at the fetch — which is what the first run of the
        # installed-runner test actually reported.
        _git(["remote", "add", "origin", str(self.remote)], self.release)
        _git(["push", "-q", "origin", "main", "--tags"], self.release)
        _git(["checkout", "-q", "--detach", "v1.0.0"], self.release)

    def _install_unbound(self, unit):
        """The definition the cutover left: released code, production dormant."""
        document = {
            "Label": unit,
            "ProgramArguments": [sys.executable, str(self.release / "deploy/runner.py")],
            "WorkingDirectory": str(self.release),
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin"},
            "StandardOutPath": str(self.release / ".session-state" / f"{unit}.out.log"),
        }
        (self.agents / f"{unit}.plist").write_bytes(plistlib.dumps(document))

    # -- helpers ---------------------------------------------------------------

    def _prepare(self, **kw):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = migrate.prepare(self.service, log=lambda m: print(m), **kw)
        return result, buffer.getvalue()

    def _roots(self):
        return production.resolve(self.release, {production.CONFIG_ENV: str(self.service)})

    def _snapshot(self, directory):
        directory = Path(directory)
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in sorted(directory.rglob("*"))
                if path.is_file() and ".git" not in path.relative_to(directory).parts}

    def _queue_payloads(self):
        queue = self.state / "queue"
        if not queue.is_dir():
            return {}
        found = {}
        for entry in sorted(queue.iterdir()):
            envelope = json.loads((entry / "envelope.json").read_text())
            found[envelope["message_id"]] = (entry / envelope["filename"]).read_bytes()
        return found


@requires_macos("plutil")
class APreparationMovesTheInstallationOntoExternalState(MigrationCase):

    def test_state_and_mail_arrive_and_the_source_is_left_byte_identical(self):
        """The acceptance's first clause, both halves at once.

        The source snapshot is taken BEFORE and compared AFTER, over the whole release
        tree — not over the files the migration happened to touch. A migration that
        moved rather than copied, or that rewrote a payload on the way past, shows up
        here whatever it recorded about itself."""
        before = self._snapshot(self.release)
        journal, _ = self._prepare()
        self.assertIsNotNone(journal["completed_at"])
        self.assertEqual(self._snapshot(self.release), before,
                         "the migration changed the installation it was reading from")

        runtime = self.state / "runtime"
        self.assertEqual(
            json.loads((runtime / "adopt-runner.briefs.json").read_text())["quarantined"],
            ["2026-09-01-a-brief"],
            "the adoption quarantine record did not survive the move")
        self.assertEqual((runtime / "adopt-runner.status").read_text(), "last run: clean\n")

        payloads = self._queue_payloads()
        self.assertEqual(len(payloads), 1, payloads)
        self.assertEqual(next(iter(payloads.values())), self.queued.read_bytes(),
                         "the queued brief did not arrive byte-identical")
        self.assertTrue((self.state / "comms" / "2026-09-15-an-unread-escalation.md").is_file(),
                        "an unread escalation was left where nothing reads it")

    def test_the_configuration_is_seeded_from_the_tree_the_deploy_already_proved(self):
        """After cutover no unit points outside the deploy tree, so `retiring_root`
        answers None — correctly. The tree itself is then the honest source: the contract
        declares all three files `required: true`, so a deploy that reached `deployed`
        could not have got there without them being present."""
        self.assertIsNone(runner.retiring_root(
            "federation", runner.registry_entry("federation"),
            {"units": UNITS}), "this fixture is meant to be already cut over")
        self._prepare()
        config = self.state / "config"
        self.assertEqual((config / "repo-paths.local").read_text(),
                         "runner-test = /somewhere/real\n")
        self.assertEqual((config / "reconcile-roots.local").read_text(), "/somewhere/real\n")

    def test_running_it_twice_queues_nothing_twice(self):
        """Idempotence is what makes a resume a re-run rather than a repair."""
        self._prepare()
        first = self._queue_payloads()
        self._prepare()
        self.assertEqual(self._queue_payloads(), first)

    def test_every_unit_ends_bound_to_external_state(self):
        self._prepare()
        for unit in UNITS:
            document = plistlib.loads((self.agents / f"{unit}.plist").read_bytes())
            environment = document.get("EnvironmentVariables") or {}
            self.assertEqual(environment.get(production.CONFIG_ENV), str(self.service),
                             f"{unit} still runs with production dormant")
            self.assertTrue(environment.get("PYTHONPYCACHEPREFIX", "").startswith(str(self.state)),
                            f"{unit} would still cache bytecode inside the sealed tree")
            for key in ("StandardOutPath", "StandardErrorPath"):
                self.assertTrue(document[key].startswith(str(self.state)),
                                f"{unit}'s {key} still writes into the release tree")

    def test_poga_is_repointed_at_the_release_after_its_old_target_is_recorded(self):
        (self.release / "poga").write_text("#!/bin/sh\n")
        journal, _ = self._prepare()
        self.assertEqual(journal["phases"]["repoint"]["previous_target"],
                         str(self.old_cli_target))
        self.assertEqual(self.cli.resolve(), (self.release / "poga").resolve())


@requires_macos("plutil")
class AnInterruptedPreparationResumesAtItsBoundary(MigrationCase):

    def _fail_at(self, phase):
        """Let the migration run until `phase`, then interrupt it exactly there."""
        original = getattr(migrate, phase)

        def boom(*a, **kw):
            raise KeyboardInterrupt(f"interrupted inside {phase}")
        return patch.object(migrate, phase, boom), original

    def test_each_handoff_boundary_resumes_and_converges(self):
        """The acceptance names resume AT EACH BOUNDARY, so each is interrupted in turn
        rather than one standing in for the rest — the boundaries differ in what they
        have already written, which is the whole reason a resume can go wrong."""
        for phase in ("inventory", "import_state", "import_mail", "fence", "rebind"):
            with self.subTest(phase=phase):
                self.setUp()
                self.addCleanup(lambda: None)
                patcher, _ = self._fail_at(phase)
                patcher.start()
                try:
                    with self.assertRaises(KeyboardInterrupt):
                        self._prepare()
                finally:
                    patcher.stop()
                journal, _ = self._prepare()
                self.assertIsNotNone(journal["completed_at"],
                                     f"a run interrupted in {phase} did not converge")
                self.assertEqual(len(self._queue_payloads()), 1,
                                 f"resuming after {phase} duplicated queued mail")
                for unit in UNITS:
                    document = plistlib.loads((self.agents / f"{unit}.plist").read_bytes())
                    self.assertIn(production.CONFIG_ENV,
                                  document.get("EnvironmentVariables") or {})

    def test_a_completed_phase_is_not_run_again_on_resume(self):
        """The checkpoint has to be READ, not merely written. A resume that re-ran an
        import would still converge here — the enqueue is idempotent — so this asserts
        the phase function was not entered at all."""
        journal, _ = self._prepare()
        self.assertIn("import-mail", journal["phases"])
        with patch.object(migrate, "import_mail") as never:
            self._prepare()
        never.assert_not_called()


@requires_macos("plutil")
class APartialRebindIsNotRecordedAsDone(MigrationCase):

    def test_a_unit_that_could_not_be_installed_leaves_the_phase_outstanding(self):
        """`record` is what makes a phase never run again. Checkpointing a rebind that
        only half happened would leave some units bound and some not, permanently, under a
        journal reading complete and a log line saying "runs on external state"."""
        original = runner._cutover_install

        def fail_the_last(unit, src, log):
            if unit == UNITS[-1]:
                return 1                      # bootout succeeded, bootstrap did not
            return original(unit, src, log)

        with patch.object(runner, "_cutover_install", fail_the_last):
            with self.assertRaises(migrate.MigrationError) as caught:
                self._prepare()
        self.assertIn(UNITS[-1], str(caught.exception))
        journal = migrate.read_journal(self.state)
        self.assertNotIn("rebind", journal["phases"],
                         "a partial rebind was checkpointed as done")
        self.assertIsNone(journal.get("completed_at"))

    def test_and_the_retry_binds_only_the_units_that_did_not_take(self):
        """The other half: leaving the phase outstanding is only useful if re-entering it
        is cheap and correct. The units that took are skipped as already bound."""
        original = runner._cutover_install
        with patch.object(runner, "_cutover_install",
                          lambda u, s, l: 1 if u == UNITS[-1] else original(u, s, l)):
            with self.assertRaises(migrate.MigrationError):
                self._prepare()
        journal, _ = self._prepare()
        self.assertIsNotNone(journal["completed_at"])
        rebound = journal["phases"]["rebind"]
        already = [row["unit"] for row in rebound["bound"] if row.get("already_bound")]
        self.assertEqual(sorted(already), sorted(UNITS[:-1]))

    def test_an_unloaded_unit_is_not_read_as_bound_from_its_plist_alone(self):
        """`_cutover_install` boots out before it bootstraps, so a failed bootstrap leaves
        the bound plist on disk and the job loaded by nothing. Asking the file alone
        reports it bound, the retry skips it, and the unit never comes back."""
        self._prepare()
        unit = UNITS[0]
        self.assertTrue(migrate._is_production_bound(
            self.agents / f"{unit}.plist", unit, self.service))
        self.launchctl.loaded.pop(unit)
        self.assertFalse(migrate._is_production_bound(
            self.agents / f"{unit}.plist", unit, self.service),
            "an unloaded unit read as bound because its plist mentions the selector")

    def test_a_unit_bound_to_a_different_configuration_is_not_read_as_bound(self):
        """Presence of the key is not the same question as this configuration. Re-running
        against another state root must rebind, not skip."""
        self._prepare()
        unit = UNITS[0]
        self.assertFalse(migrate._is_production_bound(
            self.agents / f"{unit}.plist", unit, self.root / "other-service.json"))


@requires_macos("plutil")
class TheRecordedPreMigrationReadingIsNotOverwritten(MigrationCase):

    def test_a_second_preparation_keeps_the_original_poga_target(self):
        """`discovery` holds the PRE-migration reading. Refreshing it every run overwrites
        the one thing `rollback` needs: after `repoint` has moved the link, the next run
        would record the deploy tree as the "previous" target and a rollback would restore
        `poga` to where it already points."""
        (self.release / "poga").write_text("#!/bin/sh\n")
        self._prepare()
        self._prepare()
        journal = migrate.read_journal(self.state)
        self.assertEqual(journal["discovery"]["cli_target"], str(self.old_cli_target))
        migrate.rollback(self.service, log=lambda m: None)
        self.assertEqual(self.cli.resolve(), self.old_cli_target.resolve())


class AMissingConfigurationRefusesBeforeAnythingIsDisabled(MigrationCase):

    def test_a_missing_member_location_file_refuses_with_the_scheduler_untouched(self):
        """The acceptance's "fail missing configuration before disabling the last working
        setup" — asserted as the ORDER, which is the only thing that makes it true. The
        refusal is not interesting on its own; the scheduler being untouched is."""
        (self.release / "repo-paths.local").unlink()
        before = {unit: (self.agents / f"{unit}.plist").read_bytes() for unit in UNITS}
        with self.assertRaises(migrate.MigrationError) as caught:
            self._prepare()
        self.assertIn("repo-paths.local", str(caught.exception))
        self.assertEqual([c for c in self.launchctl.calls if c[1] in ("bootout", "bootstrap")],
                         [], "a unit was touched before the configuration was validated")
        for unit in UNITS:
            self.assertEqual((self.agents / f"{unit}.plist").read_bytes(), before[unit])

    def test_a_transport_owned_by_something_else_refuses_before_any_unit_is_touched(self):
        """"Validate external root ownership before enabling dependent jobs." The
        member-location half is the test above; this is the ownership half. A transport
        repository whose marker names another remote belongs to another configuration, and
        adopting it would put this host's mail on somebody else's wire."""
        transport = self.root / "transport.git"
        transport.mkdir()
        (transport / "poga-mail-transport.json").write_text(json.dumps(
            {"schema_version": 1, "remote": "https://example.invalid/other.git",
             "owned_ref": "refs/heads/runner/mail"}))
        with self.assertRaises(migrate.MigrationError) as caught:
            self._prepare()
        self.assertIn("transport", str(caught.exception))
        self.assertEqual([c for c in self.launchctl.calls if c[1] in ("bootout", "bootstrap")],
                         [], "a unit was touched before the transport was validated")

    def test_a_configuration_naming_another_installation_refuses(self):
        self.service.write_text(json.dumps({
            "schema_version": 1, "code_root": str(self.root / "somewhere-else"),
            "state_root": str(self.state), "transport_root": str(self.root / "t.git")}))
        with self.assertRaises(migrate.MigrationError) as caught:
            self._prepare()
        self.assertIn("code_root", str(caught.exception))

    def test_a_refusal_holds_production_instead_of_failing_the_deploy(self):
        """`run_apply` turns a non-zero exit into a failed deploy, so a refusal that
        exited 1 would make every later federation release undeployable and need a human
        on the Runner. Held, escalated, and exit 0."""
        (self.release / "repo-paths.local").unlink()
        with patch.object(migrate.runner, "escalate") as escalated:
            code = migrate.main(["prepare", "--config", str(self.service),
                                 "--quiet", "--hold-on-refusal"])
        self.assertEqual(code, 0)
        escalated.assert_called_once()
        self.assertIn("HELD", escalated.call_args[0][1])

    def test_a_fault_that_is_not_one_of_our_own_refusals_still_holds(self):
        """The flag has to cover the failures this program does NOT write. A full disk
        mid-copy, a slow `git show`, a config removed between two phases — each of those
        was a traceback, a non-zero exit, a failed deploy and a rollback: verbatim the
        outcome the flag exists to prevent."""
        with patch.object(migrate, "import_state", side_effect=OSError("No space left")):
            with patch.object(migrate.runner, "escalate") as escalated:
                code = migrate.main(["prepare", "--config", str(self.service),
                                     "--quiet", "--hold-on-refusal"])
        self.assertEqual(code, 0)
        escalated.assert_called_once()
        self.assertIn("No space left", escalated.call_args[0][2])

    def test_an_operators_interrupt_is_not_swallowed_as_a_hold(self):
        """The control. `KeyboardInterrupt` is not an `Exception`, and must still stop."""
        with patch.object(migrate, "import_state", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                migrate.main(["prepare", "--config", str(self.service), "--quiet",
                              "--hold-on-refusal"])

    def test_without_that_flag_a_refusal_still_exits_non_zero(self):
        """The control. A held refusal is a deploy-path concession, not the default."""
        (self.release / "repo-paths.local").unlink()
        self.assertEqual(
            migrate.main(["prepare", "--config", str(self.service), "--quiet"]), 1)


@requires_macos("plutil")
class RollbackRestoresTheArrangementItReplaced(MigrationCase):

    def test_the_preserved_definitions_come_back_and_the_queue_survives(self):
        before = {unit: (self.agents / f"{unit}.plist").read_bytes() for unit in UNITS}
        self._prepare()
        queued = self._queue_payloads()
        self.assertTrue(queued)
        result = migrate.rollback(self.service, log=lambda m: None)
        self.assertEqual(sorted(result["restored"]), sorted(UNITS))
        for unit in UNITS:
            self.assertEqual((self.agents / f"{unit}.plist").read_bytes(), before[unit],
                             f"{unit} was not restored to the definition it had")
        self.assertEqual(self._queue_payloads(), queued,
                         "rolling back the scheduler discarded queued mail")

    def test_each_unit_is_booted_out_before_its_replacement_is_bootstrapped(self):
        """"Rollback cannot reactivate competing writers", made checkable. Two publishers
        holding one owned ref is a timing property, so the assertion is on the ORDER of
        the calls per label rather than on the end state, which looks identical either
        way."""
        self._prepare()
        self.launchctl.calls.clear()
        migrate.rollback(self.service, log=lambda m: None)
        for unit in UNITS:
            verbs = [c[1] for c in self.launchctl.calls if c[-1].endswith(unit)
                     or unit in c[-1]]
            self.assertIn("bootout", verbs, f"{unit} was never stopped")
            self.assertIn("bootstrap", verbs, f"{unit} was never restarted")
            self.assertLess(verbs.index("bootout"), verbs.index("bootstrap"),
                            f"{unit} was bootstrapped while the other writer still ran")

    def test_poga_goes_back_to_the_target_discovery_recorded(self):
        (self.release / "poga").write_text("#!/bin/sh\n")
        self._prepare()
        self.assertEqual(self.cli.resolve(), (self.release / "poga").resolve())
        migrate.rollback(self.service, log=lambda m: None)
        self.assertEqual(self.cli.resolve(), self.old_cli_target.resolve())

    def test_it_refuses_to_restore_a_program_that_cannot_read_the_queue(self):
        """"Rollback cannot downgrade into code unable to read the migrated state." Once
        the queue holds messages that exist only in production state, restoring a program
        without the modules that read it strands them where nothing looks."""
        self._prepare()
        self.assertTrue(self._queue_payloads())
        (self.release / "curate" / "mailqueue.py").unlink()
        self.launchctl.calls.clear()
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.rollback(self.service, log=lambda m: None)
        self.assertIn("mailqueue.py", str(caught.exception))
        self.assertEqual([c for c in self.launchctl.calls if c[1] == "bootout"], [],
                         "it booted a unit out before deciding it could not continue")

    def test_that_refusal_does_not_fire_when_the_queue_is_empty(self):
        """The control for the rule above. An empty queue has nothing to strand, so the
        rule must not fire — a guard that refuses every rollback is not this guard.

        The history recovery is suppressed rather than the file merely deleted: the
        queued brief is COMMITTED in this fixture, so `stranded_in_release_history` finds
        it in the object store and queues it anyway. That is the recovery working, and it
        is why removing the working-tree copy is not enough to make the queue empty."""
        self.queued.unlink()
        with patch.object(migrate, "stranded_in_release_history", return_value=[]):
            self._prepare()
        self.assertEqual(self._queue_payloads(), {})
        (self.release / "curate" / "mailqueue.py").unlink()
        migrate.rollback(self.service, log=lambda m: None)

    def test_a_tampered_backup_refuses_rather_than_bootstrapping_it(self):
        self._prepare()
        backup = self.agents / "com.federation.adopt-runner.plist.pre-migration"
        backup.write_bytes(plistlib.dumps({"Label": "something-else"}))
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.rollback(self.service, log=lambda m: None)
        self.assertIn("digest", str(caught.exception))

    def test_rollback_without_a_recorded_migration_refuses(self):
        self.service.write_text(json.dumps({
            "schema_version": 1, "code_root": str(self.release),
            "state_root": str(self.state), "transport_root": str(self.root / "t.git")}))
        self.state.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.rollback(self.service, log=lambda m: None)
        self.assertIn("nothing to restore", str(caught.exception))


@requires_macos("plutil")
class StrandedMailInTheReleaseTreeIsRecovered(MigrationCase):

    def test_a_message_committed_into_the_detached_tree_is_queued(self):
        """The arrangement that has been live since the cutover: `outbox.publish` commits
        under the repository root and pushes, and in a detached tree the push has no
        branch to go to — so the commit is made and stranded. The payload is still in the
        object store, and this is what gets it out."""
        box = self.release / "outbox" / "to-runner-test-arch"
        stranded = box / "2026-09-16-a-stranded-receipt.md"
        stranded.write_text("---\nedit-id: 2026-09-16-s\napply: manual\n"
                            "manual-reason: attended\n---\n\nA stranded fixture receipt.\n")
        payload = stranded.read_bytes()
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "stranded receipt"], self.release)
        stranded.unlink()
        _git(["checkout", "-q", "--detach", "v1.0.0"], self.release)

        self._prepare()
        recovered = [p for p in self._queue_payloads().values() if p == payload]
        self.assertEqual(len(recovered), 1,
                         "the stranded receipt was not recovered from the tree's history")

    def test_a_local_commit_the_upstream_does_not_have_is_recorded(self):
        """"Inventory ... all queued/unpublished mail (including local commits)." A commit
        is not something a mail queue can deliver, so it is RECORDED rather than replayed
        — which is what lets a reader afterwards see that nothing was quietly dropped."""
        _git(["checkout", "-q", "main"], self.release)
        _git(["branch", "--set-upstream-to=origin/main", "main"], self.release)
        (self.release / "comms" / "2026-09-17-a-local-only-note.md").write_text("# local\n")
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "a local-only commit"], self.release)
        found = migrate.unpublished_commits(self.release)
        self.assertEqual([row["subject"] for row in found], ["a local-only commit"])

    def test_a_checkout_with_no_upstream_reports_none_rather_than_blocking(self):
        """The control. A detached deploy tree has no tracking ref, and `@{u}` there fails
        or prompts — neither of which is an answer about mail."""
        self.assertEqual(migrate.unpublished_commits(self.release), [])

    def test_a_published_message_is_not_recovered_and_sent_again(self):
        """THE FALSE POSITIVE, and it is the expensive one.

        `git log -g --all` walks the reflog of EVERY ref, and a fetch writes an entry at
        each remote-tracking ref's tip — so every receipt the channel ever published comes
        back as a candidate. Measured on the real repository: 53 candidates, the first of
        them contained in `origin/runner/mail`. Enqueued, each would be DELIVERED AGAIN,
        because a `migrated:` identity is new and nothing dedupes against mail already
        sent.

        The fixture puts a message only in published history — added, pushed, then removed
        from the working tree and pushed again — so the sole way to queue it is the
        recovery scan. It must not."""
        box = self.release / "outbox" / "to-runner-test-arch"
        published = box / "2026-09-10-already-delivered.md"
        published.write_text("---\nedit-id: 2026-09-10-d\napply: manual\n"
                             "manual-reason: attended\n---\n\nAlready delivered.\n")
        payload = published.read_bytes()
        _git(["checkout", "-q", "main"], self.release)
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "publish a receipt"], self.release)
        published.unlink()
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "retire the delivered receipt"], self.release)
        _git(["push", "-q", "origin", "main"], self.release)
        _git(["checkout", "-q", "--detach", "v1.0.0"], self.release)

        self._prepare()
        self.assertNotIn(payload, list(self._queue_payloads().values()),
                         "a message the channel already published was queued for delivery "
                         "a second time")

    def test_the_control_a_stranded_message_is_still_recovered(self):
        """The control for the rule above: the filter must not be "recover nothing"."""
        box = self.release / "outbox" / "to-runner-test-arch"
        stranded = box / "2026-09-16-never-published.md"
        stranded.write_text("---\nedit-id: 2026-09-16-n\napply: manual\n"
                            "manual-reason: attended\n---\n\nNever published.\n")
        payload = stranded.read_bytes()
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "stranded on a detached head"], self.release)
        stranded.unlink()
        _git(["checkout", "-q", "--detach", "v1.0.0"], self.release)
        self._prepare()
        self.assertIn(payload, list(self._queue_payloads().values()))

    def test_an_unreadable_history_is_reported_rather_than_read_as_empty(self):
        """"Report unprovable/lost content honestly." An unreadable history and an empty
        one are different answers, and folding them together is how a recovery reports
        success over mail it never looked for."""
        with patch.object(migrate.runner, "git") as failing:
            failing.return_value = subprocess.CompletedProcess([], 1, "", "broken")
            found = migrate.stranded_in_release_history(self.release, lambda m: None)
        self.assertEqual(found, [{"unreadable": True}])


@requires_macos("plutil")
class RenderingBindsAUnitToExternalState(MigrationCase):

    def test_an_unsubstituted_placeholder_refuses_rather_than_installing(self):
        """`plutil -lint` accepts `__CLAUDE_BIN__` as an ordinary string, so an
        unsubstituted token passes every check the cutover makes and installs a job
        launchd cannot spawn. Proved on the real shape: the adopt-runner template carries
        `__CLAUDE_BIN__`, and with no `claude` on the PATH the render must refuse."""
        entry = runner.registry_entry("federation")
        with patch.object(runner.shutil, "which",
                          side_effect=lambda n: None if n == "claude" else "/usr/bin/python3"):
            out = runner.render_unit_plist("federation", entry,
                                           "com.federation.adopt-runner", lambda m: None)
        self.assertIsNone(out, "a plist with an unsubstituted placeholder was rendered")

    def test_the_guard_does_not_fire_on_the_word_in_the_templates_own_comment(self):
        """The control, and it is the failure this guard actually had first: all three
        templates DOCUMENT themselves as carrying "__PLACEHOLDER__ tokens" in an XML
        comment, so a regex over the source refuses every correct render."""
        entry = runner.registry_entry("federation")
        for unit in UNITS:
            with self.subTest(unit=unit):
                self.assertIn("__PLACEHOLDER__",
                              (self.release / "deploy" / f"{unit}.plist.template").read_text(),
                              "this control is vacuous unless the template says it")
                self.assertIsNotNone(
                    runner.render_unit_plist("federation", entry, unit, lambda m: None))

    def test_a_refused_render_does_not_fall_back_to_the_stale_in_tree_plist(self):
        """The refusal must not be routed around by the thing it refuses.

        v7.3.0's renderer wrote `deploy/<label>.plist` INTO the deploy tree and never
        substituted `__CLAUDE_BIN__`, and `checkout --force --detach` leaves untracked
        files alone — so that unbound artifact is still there on a real installation. With
        `claude` off the PATH (which is the launchd case: the adopt-runner template injects
        `__CLAUDE_DIR__` into PATH precisely because it is not on the default one) the
        render refuses, and an `or src` fallback would install exactly that file: no
        production selector, logs into the sealed tree, an unspawnable argv — recorded as
        a success."""
        # PROVISIONED FIRST, and this is the whole reason the test is shaped this way.
        # Without it `production.resolve` refuses (no state root yet), `unit_plist_source`
        # returns None down the unusable-configuration path, and the assertion below
        # passes whether or not the fallback exists — a test that cannot fail on its own
        # subject. Measured: with the fallback deliberately restored, the first cut of
        # this test still passed.
        self._prepare()
        stale = self.release / "deploy" / "com.federation.adopt-runner.plist"
        stale.write_bytes(plistlib.dumps({
            "Label": "com.federation.adopt-runner",
            "ProgramArguments": ["__CLAUDE_BIN__"],
            "WorkingDirectory": str(self.release)}))
        entry = runner.registry_entry("federation")
        with patch.object(runner.shutil, "which",
                          side_effect=lambda n: None if n == "claude" else "/usr/bin/python3"):
            os.environ[production.CONFIG_ENV] = str(self.service)
            try:
                source = runner.unit_plist_source(
                    "federation", entry, "com.federation.adopt-runner", lambda m: None)
            finally:
                os.environ.pop(production.CONFIG_ENV, None)
        self.assertIsNone(source, f"the refused render fell back to {source}")

    def test_an_unusable_production_config_refuses_instead_of_raising(self):
        """`finish_self_cutover` promises its caller it never raises, and
        `production.resolve` RAISES on a selector that is set but unusable — it does not
        return None. A sweep whose config file has moved must refuse to install, not die."""
        entry = runner.registry_entry("federation")
        os.environ[production.CONFIG_ENV] = str(self.root / "not-a-config.json")
        try:
            source = runner.unit_plist_source(
                "federation", entry, "com.federation.mail-poller", lambda m: None)
        finally:
            os.environ.pop(production.CONFIG_ENV, None)
        self.assertIsNone(source)

    def test_off_production_nothing_about_rendering_changes(self):
        """"New features remain inactive until preparation is complete." With no service
        configuration resolved, the render is the one it always was: in the tree, with no
        production selector."""
        entry = runner.registry_entry("federation")
        out = runner.render_unit_plist("federation", entry, "com.federation.mail-poller",
                                       lambda m: None)
        self.assertEqual(out.parent, self.release / "deploy")
        document = plistlib.loads(out.read_bytes())
        self.assertNotIn(production.CONFIG_ENV,
                         document.get("EnvironmentVariables") or {})


class ADerivedConfigurationStaysInsideThisMachinesOwnState(MigrationCase):

    def test_the_derived_default_is_written_under_the_home_it_is_given(self):
        """The derivation every other test opts out of, exercised where it cannot escape.

        It is asserted against a redirected HOME because that is the fault it had: the
        default `state_root` is `$HOME/.local/state/poga/federation`, and a caller that
        did not redirect HOME provisioned the real one."""
        self.service.unlink()
        migrate.author_config(self.service, code_root=self.release,
                              remote=str(self.remote), log=lambda m: None)
        document = json.loads(self.service.read_text())
        self.assertTrue(document["state_root"].startswith(str(self.root / "home")),
                        document["state_root"])
        self.assertEqual(document["code_root"], str(self.release))

    def test_the_derived_default_never_points_the_transport_at_the_channel_branch(self):
        """The defect at its source. A derived config is what a first run gets, and until
        WI-0418 what it got was `refs/heads/runner/mail` -- the branch `curate/channel.py`
        writes a full tree to, which `mailtransport` then refuses as its own owned ref."""
        self.service.unlink()
        self.assertEqual("written", migrate.author_config(
            self.service, code_root=self.release, remote=str(self.remote),
            log=lambda m: None))
        document = json.loads(self.service.read_text())
        self.assertEqual(document["schema_version"], migrate.SERVICE_CONFIG_SCHEMA)
        self.assertEqual(document["transport"]["owned_ref"], migrate.TRANSPORT_OWNED_REF)
        self.assertEqual(document["transport"]["input_refs"],
                         list(migrate.TRANSPORT_INPUT_REFS))
        self.assertFalse(migrate.names_channel_ref(document["transport"]))

    def test_an_authored_configuration_is_never_rewritten(self):
        before = self.service.read_bytes()
        self.assertEqual("authored", migrate.author_config(
            self.service, code_root=self.release, remote=str(self.remote),
            log=lambda m: None))
        self.assertEqual(self.service.read_bytes(), before)


class AConfigurationNamingTheChannelBranchIsCorrected(MigrationCase):
    """WI-0418. One ref name, two writers, two shapes.

    `curate/channel.py` publishes a full working tree to `refs/heads/runner/mail`;
    `mailtransport._validate_tree` refuses an owned ref holding anything but
    `messages/<sha256>.json` at 100644. The Runner's configuration was written naming it by
    the v7.5.2 apply at 11:24:53 on 2026-09-21, and its worker failed every cycle from
    09:51 with 56 pending and 0 published. The correction has to happen in the migration,
    because the only other place it can happen is a person typing on the host."""

    def author(self, transport):
        document = json.loads(self.service.read_text())
        document["transport"] = transport
        self.service.write_text(json.dumps(document))
        return migrate.author_config(self.service, code_root=self.release,
                                     remote=str(self.remote), log=lambda m: None)

    def test_an_owned_ref_on_the_channel_branch_moves_and_takes_its_input_ref_with_it(self):
        """BOTH refs move, and that is not tidiness. Correcting the owned ref alone leaves
        the input ref pointed at a branch the peer never publishes to, which buys a worker
        that publishes into silence instead of one that refuses out loud."""
        outcome = self.author({"remote": str(self.remote),
                               "owned_ref": "refs/heads/runner/mail",
                               "input_refs": ["refs/heads/dev/mail"]})
        self.assertEqual("upgraded", outcome)
        document = json.loads(self.service.read_text())
        self.assertEqual(document["transport"]["owned_ref"], migrate.TRANSPORT_OWNED_REF)
        self.assertEqual(document["transport"]["input_refs"],
                         list(migrate.TRANSPORT_INPUT_REFS))

    def test_the_bare_spelling_of_the_channel_branch_is_the_same_collision(self):
        """`channel.BRANCH` is bare and the config key is qualified. A comparison that does
        not normalise matches neither of the two ways the same ref is written."""
        self.assertEqual("upgraded", self.author({"remote": str(self.remote),
                                                  "owned_ref": "runner/mail",
                                                  "input_refs": []}))

    def test_the_channel_branch_hiding_in_an_input_ref_is_caught_too(self):
        outcome = self.author({"remote": str(self.remote),
                               "owned_ref": "refs/heads/runner/messages",
                               "input_refs": ["refs/heads/runner/mail"]})
        self.assertEqual("upgraded", outcome)
        document = json.loads(self.service.read_text())
        self.assertEqual(document["transport"]["input_refs"],
                         list(migrate.TRANSPORT_INPUT_REFS))

    def test_the_upgrade_preserves_every_other_key_the_operator_authored(self):
        """The trigger is one unserviceable ref, so the correction is one unserviceable ref.
        A migration that used it as licence to re-derive the roots would be the rewrite the
        docstring promises never happens."""
        document = json.loads(self.service.read_text())
        document["inbox_root"] = str(self.root / "authored-inbox")
        document["transport"] = {"remote": str(self.remote),
                                 "owned_ref": "refs/heads/runner/mail",
                                 "input_refs": ["refs/heads/dev/mail"],
                                 "poll_interval_seconds": 45}
        self.service.write_text(json.dumps(document))
        migrate.author_config(self.service, code_root=self.release,
                              remote=str(self.remote), log=lambda m: None)
        after = json.loads(self.service.read_text())
        self.assertEqual(after["inbox_root"], str(self.root / "authored-inbox"))
        self.assertEqual(after["state_root"], document["state_root"])
        self.assertEqual(after["transport_root"], document["transport_root"])
        self.assertEqual(after["transport"]["poll_interval_seconds"], 45)
        self.assertEqual(after["transport"]["remote"], str(self.remote))

    def test_an_upgraded_document_carries_the_new_schema_version(self):
        self.author({"remote": str(self.remote), "owned_ref": "refs/heads/runner/mail",
                     "input_refs": []})
        self.assertEqual(json.loads(self.service.read_text())["schema_version"],
                         migrate.SERVICE_CONFIG_SCHEMA)
        self.assertEqual(migrate.SERVICE_CONFIG_SCHEMA, 2)

    def test_a_host_with_its_own_ref_names_is_not_dragged_onto_the_defaults(self):
        """The trigger is the channel branch, NOT "differs from the default". A host is
        entitled to its own ref names, and an upgrade that collected them all would be a
        second defect wearing the first one's fix as a disguise."""
        transport = {"remote": str(self.remote), "owned_ref": "refs/heads/host/outbound",
                     "input_refs": ["refs/heads/host/inbound"]}
        self.assertEqual("authored", self.author(transport))
        self.assertEqual(json.loads(self.service.read_text())["transport"], transport)

    def test_an_unreadable_configuration_is_left_for_raw_config_to_refuse(self):
        """Repairing it here would destroy the evidence for the refusal three lines later
        in `prepare`, which is the one that names the actual fault."""
        self.service.write_text("{not json at all")
        self.assertEqual("authored", migrate.author_config(
            self.service, code_root=self.release, remote=str(self.remote),
            log=lambda m: None))
        self.assertEqual(self.service.read_text(), "{not json at all")
        with self.assertRaises(migrate.MigrationError):
            migrate.raw_config(self.service)

    def test_the_channel_ref_is_read_off_the_channel_rather_than_spelled_again(self):
        """A second literal is a second thing to forget. Renaming the channel branch has to
        carry the collision check with it."""
        self.assertEqual(migrate.CHANNEL_REF, "refs/heads/" + channel.BRANCH)
        self.assertEqual(migrate.CHANNEL_REF, "refs/heads/runner/mail")



@requires_macos("plutil")
class AHostThatAlreadyProvisionedStillRepoints(MigrationCase):
    """WI-0425. The Runner's exact state after an upgrade: `provision` had recorded an
    apply whose configuration named the channel branch, so the ownership marker names
    `refs/heads/runner/mail`; then a later release's `author_config` moved the
    configuration to `refs/heads/runner/messages`. The repoint lived inside `provision`,
    the phase gate skipped it, and the worker refused on every cycle.

    Built through the real code rather than a hand-written marker: the first preparation
    runs as v7.5.2 did, with the channel-branch upgrade not yet in existence."""

    CHANNEL = "refs/heads/runner/mail"

    def setUp(self):
        super().setUp()
        document = json.loads(self.service.read_text())
        document["transport"] = {"remote": str(self.remote), "owned_ref": self.CHANNEL,
                                 "input_refs": ["refs/heads/dev/mail"]}
        self.service.write_text(json.dumps(document))
        with patch.object(migrate, "names_channel_ref", lambda transport: False):
            journal, _ = self._prepare()        # v7.5.2: no upgrade, marker on the channel
        self.assertIn("provision", journal["phases"])
        self.assertEqual(self._marker()["owned_ref"], self.CHANNEL,
                         "the fixture did not reproduce the Runner's marker")

    def _marker(self):
        return json.loads((self.root / "transport.git" / "poga-mail-transport.json").read_text())

    def test_the_negative_control_the_gated_path_alone_leaves_the_marker(self):
        """The defect, reproduced: without the ungated step, the second preparation
        upgrades the configuration and never touches the marker, and the repository then
        refuses exactly as the Runner's worker does."""
        with patch.object(migrate, "reconcile_transport_ownership", lambda roots, log: False):
            self._prepare()
        self.assertEqual(json.loads(self.service.read_text())["transport"]["owned_ref"],
                         migrate.TRANSPORT_OWNED_REF)
        self.assertEqual(self._marker()["owned_ref"], self.CHANNEL)
        with self.assertRaisesRegex(migrate.mailtransport.TransportError,
                                    "ownership differs from configuration"):
            migrate.mailtransport.ensure_repository(self._roots())

    def test_the_next_preparation_repoints_without_rerunning_provision(self):
        with patch.object(migrate, "claim_transport") as provision_step:
            _, said = self._prepare()
        provision_step.assert_not_called()      # the phase really was skipped
        self.assertEqual(self._marker()["owned_ref"], migrate.TRANSPORT_OWNED_REF)
        self.assertIn("ownership marker moved off", said)
        migrate.mailtransport.ensure_repository(self._roots())      # no longer refuses

    def test_a_marker_on_some_other_ref_is_left_to_refuse(self):
        """The negative control the item names: only the channel branch is ever left. A
        marker naming anything else means another owner, and it still refuses."""
        marker = self.root / "transport.git" / "poga-mail-transport.json"
        declared = self._marker()
        declared["owned_ref"] = "refs/heads/host/outbound"
        marker.write_text(json.dumps(declared, sort_keys=True, separators=(",", ":")) + "\n")
        self._prepare()
        self.assertEqual(self._marker()["owned_ref"], "refs/heads/host/outbound")
        with self.assertRaisesRegex(migrate.mailtransport.TransportError,
                                    "ownership differs from configuration"):
            migrate.mailtransport.claim_repository(self._roots())

    def test_a_running_worker_does_not_fail_the_preparation(self):
        """The worker holds the same lock and applies the same repoint itself, so a busy
        lock is a race with no loser, never a refusal that holds a deploy."""
        with migrate.mailtransport.transport_lock(self._roots()):
            journal, said = self._prepare()
        self.assertIsNotNone(journal["completed_at"])
        self.assertIn("repoints the ownership marker itself", said)
        self.assertEqual(self._marker()["owned_ref"], self.CHANNEL)


class TheServiceConfigurationVersionIsReadStrictly(MigrationCase):

    def version(self, value):
        document = json.loads(self.service.read_text())
        document["schema_version"] = value
        self.service.write_text(json.dumps(document))

    def test_both_published_versions_are_readable(self):
        """Version 2 changed which refs were WRITTEN, not what a reader parses. Refusing
        version 1 would make production dormant on every host between this release landing
        and that host's own migration running."""
        for value in migrate.SUPPORTED_SCHEMA_VERSIONS:
            with self.subTest(value=value):
                self.version(value)
                self.assertEqual(migrate.raw_config(self.service)["schema_version"], value)

    def test_a_version_nobody_published_refuses(self):
        for value in (0, 3, 99, -1):
            with self.subTest(value=value):
                self.version(value)
                with self.assertRaises(migrate.MigrationError):
                    migrate.raw_config(self.service)

    def test_a_boolean_is_not_a_version_number(self):
        """`True == 1`, so `schema_version != 1` accepted `true` as version 1 for as long as
        that check existed. Matching the message rather than the class: `raw_config` refuses
        several different ways and a bare assertRaises cannot tell them apart."""
        self.version(True)
        with self.assertRaisesRegex(migrate.MigrationError, "schema_version"):
            migrate.raw_config(self.service)

    def test_a_string_is_not_a_version_number(self):
        self.version("2")
        with self.assertRaisesRegex(migrate.MigrationError, "schema_version"):
            migrate.raw_config(self.service)


class TheInstalledRunnerReachesThePreparation(MigrationCase):
    """The clause that cannot be argued, only executed: *the unmodified OLD invocation
    reaches the new preparation automatically*.

    The old runner here is not a stand-in — it is `v7.3.0:deploy/runner.py`, the exact
    bytes host evidence shows are installed, read out of this repository's own object
    store and never edited. What it is asked to do is what
    `com.federation.deploy-sweep` asks it every 600 seconds."""

    def test_v7_3_0s_runner_runs_the_incoming_tags_apply_command(self):
        has_tag = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "v7.3.0^{commit}"], cwd=str(REPO),
            capture_output=True, text=True).returncode == 0
        if not has_tag and (REPO / "PUBLIC-CUT-RECEIPT.md").is_file():
            # The public cut is a fresh history with no release tags, so the installed
            # runner's exact bytes are not in its object store. The trunk has the tag
            # and still runs this; there a missing tag fails below, loudly.
            self.skipTest("public cut: tag v7.3.0 is not in this tree's history")
        old = self.root / "old-release"
        old.mkdir()
        (old / "deploy").mkdir()
        blob = subprocess.run(["git", "show", "v7.3.0:deploy/runner.py"], cwd=str(REPO),
                              capture_output=True, text=True, check=True).stdout
        (old / "deploy" / "runner.py").write_text(blob)
        self.assertNotIn("--unattended", blob,
                         "this fixture is only meaningful against the installed surface")
        self.assertIn("def run_apply", blob,
                      "the whole design rests on the installed runner knowing `apply`")
        for relative in ("curate", "sessionlib"):
            if (REPO / relative).is_dir():
                shutil.copytree(REPO / relative, old / relative,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(REPO / "deploy" / "contract.schema.json",
                        old / "deploy" / "contract.schema.json")

        # The incoming tag: it carries the apply command, and nothing else has to change.
        witness = self.root / "preparation-ran.txt"
        contract = json.loads((self.release / "deploy" / "deploy.json").read_text())
        contract["apply"] = {"cmd": ["/bin/sh", "-c", f"echo reached > {witness}"],
                             "timeout_seconds": 60}
        _git(["checkout", "-q", "main"], self.release)
        (self.release / "deploy" / "deploy.json").write_text(json.dumps(contract, indent=2))
        _git(["add", "-A"], self.release)
        _git(["commit", "-qm", "the migration release"], self.release)
        _git(["tag", "v1.1.0"], self.release)
        _git(["push", "-q", "--tags", str(self.remote), "main"], self.release)
        _git(["checkout", "-q", "--detach", "v1.0.0"], self.release)

        result = subprocess.run(
            [sys.executable, "-B", str(old / "deploy" / "runner.py"), "federation",
             "--trigger", "sweep"],
            cwd=str(self.root), capture_output=True, text=True,
            env={**os.environ, **self._env, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertTrue(witness.exists(),
                        "the installed runner never reached the incoming tag's apply "
                        f"command.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


@requires_macos("plutil")
class TheRetiringCheckoutIsReadAndNeverWritten(MigrationCase):
    """The installation WI-0366 has to move, which no other test in this file builds.

    Every fixture above is already cut over: `_install_unbound` points each unit at the
    release, so `retiring_root` answers None and the whole retiring-checkout branch of
    `config_sources`, `state_sources`, `inventory`, `import_state` and `import_mail` is
    never entered. That branch is the "old installation" leg of this item's rehearsal —
    the arrangement where the units still run from the process checkout the wave exists
    to retire — and it had no coverage at all.

    The retirement clause says the old directory stays intact as a recovery archive. That
    is a property of the MIGRATION, not of the later deletion: a migration that moved a
    queued brief instead of copying it would satisfy every delivery assertion in this file
    and still have emptied the archive. So the reading taken here is of the retiring tree
    itself, before and after, byte for byte."""

    OPERATOR_PATHS = "runner-test = /authored/on/the/retiring/checkout\n"

    def setUp(self):
        super().setUp()
        self.old = self.root / "old-process-root"
        self.old.mkdir()
        _git(["init", "-q", "-b", "main"], self.old)
        _no_background_git(self.old)
        # Distinct from the release's copies on purpose. "Seeded from the retiring
        # checkout" is only provable when the two sources disagree; with identical bytes
        # the assertion passes whichever source won, which is no assertion at all.
        (self.old / "repo-paths.local").write_text(self.OPERATOR_PATHS)
        (self.old / "reconcile-roots.local").write_text("/authored/on/the/retiring/checkout\n")
        (self.old / "mailboxes.json").write_text(json.dumps(
            {"members": {"runner-test": {"architect_id": "runner-test-arch"}}}))
        session_state = self.old / ".session-state"
        session_state.mkdir()
        (session_state / "adopt-runner.status").write_text("last run: on the old root\n")
        (session_state / "adopt-runner.jsonl").write_text('{"run": "old"}\n')
        box = self.old / "outbox" / "to-runner-test-arch"
        box.mkdir(parents=True)
        self.old_queued = box / "2026-09-12-waiting-on-the-old-root.md"
        self.old_queued.write_text("---\nedit-id: 2026-09-12-old\napply: manual\n"
                                   "manual-reason: attended\n---\n\nQueued before the move.\n")
        comms = self.old / "comms"
        comms.mkdir()
        (comms / "2026-09-11-nobody-read-this.md").write_text("# Still unread\n")
        _git(["add", "-A"], self.old)
        _git(["commit", "-qm", "the retiring process root"], self.old)
        for unit in UNITS:
            document = plistlib.loads((self.agents / f"{unit}.plist").read_bytes())
            document["WorkingDirectory"] = str(self.old)
            document["ProgramArguments"] = [sys.executable, str(self.old / "deploy/runner.py")]
            (self.agents / f"{unit}.plist").write_bytes(plistlib.dumps(document))
            self.launchctl.loaded[unit] = {"plist": str(self.agents / f"{unit}.plist"),
                                           "wd": str(self.old)}

    def test_the_retiring_checkout_is_derived_from_the_loaded_units(self):
        self._prepare()
        journal = migrate.read_journal(self.state)
        self.assertEqual(journal["discovery"]["retiring_root"], str(self.old))
        self.assertFalse(journal["discovery"]["already_in_release"])
        self.assertEqual(journal["config_sources"][0]["root"], str(self.old))

    def test_the_operators_own_configuration_wins_over_the_deploy_trees_copy(self):
        self._prepare()
        self.assertEqual((self.state / "config" / "repo-paths.local").read_text(),
                         self.OPERATOR_PATHS)
        self.assertNotEqual((self.release / "repo-paths.local").read_text(),
                            self.OPERATOR_PATHS)

    def test_state_and_mail_waiting_on_the_old_root_are_carried_across(self):
        self._prepare()
        self.assertEqual((self.state / "runtime" / "adopt-runner.status").read_text(),
                         "last run: on the old root\n")
        self.assertIn(self.old_queued.read_bytes(), self._queue_payloads().values())
        self.assertEqual((self.state / "comms" / "2026-09-11-nobody-read-this.md").read_text(),
                         "# Still unread\n")

    def test_the_retiring_checkout_is_left_byte_identical(self):
        before = self._snapshot(self.old)
        self.assertIn("outbox/to-runner-test-arch/2026-09-12-waiting-on-the-old-root.md", before)
        self._prepare()
        # ASSERTED FIRST, AND NOT DECORATION. A migration that never looked at this tree
        # also leaves it byte-identical, so the comparison below is only evidence once
        # something proves the tree was read. Measured: with `retiring_root` mutated to
        # answer None the equality still held and this test stayed green on its own.
        self.assertIn(self.old_queued.read_bytes(), self._queue_payloads().values(),
                      "the old root's queued mail never reached the queue, so this "
                      "tree was never read and its being unchanged proves nothing")
        self.assertEqual(self._snapshot(self.old), before,
                         "the migration wrote into, moved from, or removed from the "
                         "checkout the retirement keeps as a recovery archive")

    def test_that_reading_can_actually_fail(self):
        """The control. `assertEqual` on two snapshots passes for free if the snapshot
        cannot see the tree — an empty dict equals an empty dict. This asserts the
        comparison above has a subject and would report a write into it."""
        before = self._snapshot(self.old)
        self.assertTrue(before)
        self._prepare()
        (self.old / "outbox" / "to-runner-test-arch" / "a-later-write.md").write_text("x\n")
        self.assertNotEqual(self._snapshot(self.old), before)

    def test_a_second_preparation_queues_the_old_roots_mail_once(self):
        self._prepare()
        first = self._queue_payloads()
        self._prepare()
        self.assertEqual(self._queue_payloads(), first)


class TheShippedContractIsTheOneThatReachesThisProgram(unittest.TestCase):
    """Every test above proves the MECHANISM against a fixture contract. This one asks
    whether the contract this repository actually ships uses it — a capability whose own
    subject never declares it reads as absent to the surface that would make it real."""

    def setUp(self):
        self.contract = json.loads((REPO / "deploy" / "deploy.json").read_text())

    def test_it_declares_apply_and_apply_runs_this_program(self):
        command = (self.contract.get("apply") or {}).get("cmd")
        self.assertIsNotNone(command, "the shipped contract opens no door to preparation")
        self.assertIn("deploy/migrate.py", command)
        self.assertIn("prepare", command)
        self.assertTrue((REPO / "deploy" / "migrate.py").is_file())

    def test_a_refusal_is_held_rather_than_failing_every_later_release(self):
        """`run_apply` raises `DeployError` on a non-zero exit, and that failure is the
        whole deploy. Without this flag a preparation that refused would leave the
        federation undeployable until somebody worked on the Runner by hand, which is the
        one outcome this wave may not produce."""
        self.assertIn("--hold-on-refusal", self.contract["apply"]["cmd"])

    def test_it_still_validates_against_the_shipped_schema(self):
        schema = json.loads((REPO / "deploy" / "contract.schema.json").read_text())
        runner._validate_contract(self.contract, schema, "federation")

    def test_every_declared_phase_is_one_prepare_actually_checkpoints(self):
        """`PHASES` is documentation unless it agrees with the code. A phase that runs
        without recording a checkpoint cannot be resumed past, and a name here that
        nothing records reads as a boundary the resume protects when it does not."""
        source = (REPO / "deploy" / "migrate.py").read_text()
        for phase in migrate.PHASES:
            with self.subTest(phase=phase):
                self.assertIn(f'done(journal, "{phase}")', source)
                self.assertIn(f'journal, "{phase}"', source)


if __name__ == "__main__":
    unittest.main()


@requires_macos("plutil")
class TheMigrationBindsOnlyThisMachinesUnits(MigrationCase):
    """WI-0420's third caller, and the one the rule was nearly shipped without.

    `rebind` INSTALLS, so the host rule binds it exactly as it binds the cutover. Found by
    running the suite rather than by reading: with the filter in `cutover_state` and
    `restart_units` only, `rebind` asked `unit_plist_source` for a unit belonging to
    another machine, got the refusal the renderer now issues, recorded "no installable
    plist" and RAISED -- so the Runner's migration would have failed forever on
    `com.federation.gate-inputs`, a unit it should never have been binding at all. That is
    the deploy-time symptom of a fix applied in two places out of three.

    Discovery and fencing deliberately still walk the whole contract. Reporting what is
    loaded on this machine, and preserving what is already installed on it, are true of
    every declared unit whoever owns it -- and on the Runner `com.federation.gate-inputs`
    IS installed, which is the state this item exists to stop recurring.
    """

    DEVBOX_ONLY = "com.federation.gate-inputs"
    RUNNERS_OWN = list(UNITS)

    def setUp(self):
        # DECLARED BEFORE THE TAG IS CUT, because the contract is read FROM THE TAG
        # (`read_contract`) and not from the working tree. Editing `deploy.json` on disk
        # afterwards changes nothing the runner ever sees -- the first cut of this class
        # did exactly that and its assertions passed against a contract of three units
        # that had never heard of the fourth.
        patcher = patch.object(sys.modules[__name__], "UNITS",
                               [*self.RUNNERS_OWN, self.DEVBOX_ONLY])
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_the_contract_really_does_declare_the_other_machines_unit(self):
        """The fixture check this class needed and did not have. Everything below is
        vacuous if the fourth unit never reached the tag."""
        contract = json.loads(
            (self.release / "deploy" / "deploy.json").read_text())
        self.assertIn(self.DEVBOX_ONLY, contract["units"])

    def test_a_contract_naming_another_machines_unit_does_not_fail_the_migration(self):
        """The regression. Before the filter reached `rebind` this raised
        `rebind did not complete: com.federation.gate-inputs - no installable plist`."""
        journal, _ = self._prepare()
        rebound = journal["phases"]["rebind"]
        self.assertEqual(rebound["refused"], [])
        bound = [row["unit"] for row in rebound["bound"]]
        self.assertNotIn(self.DEVBOX_ONLY, bound)

    def test_the_control_this_machines_own_units_are_still_bound(self):
        """Without this, a filter that dropped everything would pass the test above."""
        journal, _ = self._prepare()
        bound = [row["unit"] for row in journal["phases"]["rebind"]["bound"]]
        self.assertEqual(sorted(bound), sorted(self.RUNNERS_OWN))

    def test_the_skip_is_said_out_loud_rather_than_being_silent(self):
        """A unit that vanishes from a transition with no line in the log is
        indistinguishable from one nobody declared."""
        _, said = self._prepare()
        self.assertIn(self.DEVBOX_ONLY, said)
        self.assertIn("belong to another machine", said)
