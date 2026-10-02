"""WI-0360 — the sealed process root, and the seed map that stopped being a precondition.

THE FAILURE THIS FILE EXISTS TO PREVENT, in the order it actually happened.

The federation's three launchd units ran out of the operator's working checkout — the
live trunk clone that development advances all day. Their own writes (a deploy receipt, queued
mail, an escalation) committed INTO that clone and then could not push: measured
behind-counts on 2026-09-13 were 417, 32, 15, 16, 9, and three stranded commits in one
day were each cleared by hand. Sealing the process root ends that class. Two new hazards
arrive with the seal and both are silent:

  1. THE SEED GAP. A sealed tree starts empty of everything gitignored, and the three
     files that tell the adopt-runner and reconcile WHERE THE MEMBER REPOS ARE
     (`reconcile-roots.local`, `repo-paths.local`, `deploy/seed-paths.local.json`) are
     exactly that. Without a derived source a sweep reports all three "absent and this
     machine declares no seed source", because the seed map is an INPUT a human has to
     write. Cut over in that state, the nightly adopt-runner finds zero members
     and reports a clean night — a silent wrong answer, which is strictly worse than a
     refusal at deploy time while the previous version is still running. `retiring_root`
     derives the source from the checkout the units are being moved OFF, `vault_state`
     mirrors what the tree ends up holding into a machine-local STATE VAULT beside the
     ledger, `write_seed_map` turns the map into an OUTPUT pointing at that vault, and the
     contract's three entries flip to required:true so the gap refuses instead of
     degrading. The vault is what makes the map mean anything: the first cut pointed each
     entry at the deploy tree's own copy, which can only ever name a file that is missing
     for the same reason the seed was needed. The map has to point somewhere a rollback, a
     `checkout --force` or a deleted tree cannot reach — and the vault is asked BY NAME as
     the second of three source tiers (declared map, vault, derived retiring checkout), so
     that reaching it needs no surviving pointer at all. The map is a convenience; the
     vault is the mechanism. `TheVaultIsASourceTierNotJustAMapEntryTest` pins the order.

  2. THE GUARD THAT FIRES ON CORRECT USE. `refuse_unsealed_process_root` stops a future
     `install-*.sh` pointing a unit back at a working clone — every one of them resolves
     FED_ROOT to the checkout it was run from, so the regression is one script away. Taken
     literally the ruling would also refuse a person typing `python3 deploy/runner.py
     example-app` in a trunk clone, and a guard that fires on correct use is a guard somebody
     deletes ([`a-guard-that-fires-on-correct-code-gets-deleted`]). The three-condition
     narrowing is therefore tested from BOTH sides here: the one case that must raise, and
     the three that must not.

SAFETY. Every `POGA_DEPLOY_*` path plus `POGA_CHANNEL_ROOT` is redirected into a
`TemporaryDirectory` by the base fixture, copying `test_deploy_runner.DeployRunnerCase`.
A fixture in this area has already written into the federation's own repo and the real
ledger; a suite that CAN reach production paths eventually will. `launchctl` is never
shelled out to — `unit_target` and `running_under_unit` are patched — so the suite cannot
read, install or disturb a single job on the machine running it. Git is NOT mocked: the
sealed/unsealed distinction IS a detached HEAD, so the repos here are real ones, and
identity is configured per-repo with `git -C`, never `--global`.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
import runner  # noqa: E402
# The end-to-end case below subclasses the existing deploy fixture rather than copying it:
# the upstream repo, the fixture federation repo and the mailbox registry are exactly the
# machinery a real `runner.deploy()` needs, and a second hand-written copy of them is a
# second thing to keep in agreement.
import test_deploy_runner as base  # noqa: E402


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    """One git command against one repo. Identity is set with `git -C <repo> config` in
    `_make_repo` — per-repo and never `--global`, because a suite that writes the running
    machine's global git config has reached production by a side door."""
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def _make_repo(path: Path, *, detached: bool = False) -> Path:
    """A REAL git repo, because `channel.is_deploy_tree` asks a real git question.

    Its test is a detached HEAD, not a path convention — that is the property the hazard is
    made of, since a commit on a detached HEAD belongs to no branch, has no upstream, and
    is gone the moment the next deploy checks a different tag out over it. Faking that with
    a mock would assert the assumption back instead of the behaviour."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, ["init", "-b", "main"])
    _git(path, ["config", "user.email", "test@example.com"])
    _git(path, ["config", "user.name", "Test"])
    _git(path, ["config", "commit.gpgsign", "false"])
    # Do not fork git work that OUTLIVES THE TEST — the convention and its reasoning are
    # at tests/test_runner_channel.py's `_NO_BACKGROUND_GIT`. The first two say do not
    # start background maintenance; the third says that if something starts it anyway it
    # runs in the foreground, where `subprocess.run` waits for it. Without them the two
    # tests below that `shutil.rmtree(self.old)` race git's own maintenance process and
    # fail with `FileNotFoundError: 'maintenance.lock'` — the file is gone between
    # rmtree's scandir and its unlink. Seen twice in two consecutive sharded suite runs,
    # on a different test each time, and never in isolation.
    _git(path, ["config", "gc.auto", "0"])
    _git(path, ["config", "gc.autoDetach", "false"])
    _git(path, ["config", "maintenance.auto", "false"])
    (path / "marker.txt").write_text("v1\n")
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-m", "first"])
    if detached:
        _git(path, ["tag", "v1.0.0"])
        _git(path, ["checkout", "--force", "--detach", "v1.0.0"])
    return path


class SealedRootCase(unittest.TestCase):
    """Base fixture: every production path redirected, and launchd unreachable.

    The env list is copied verbatim from `test_deploy_runner.DeployRunnerCase` rather than
    invented, plus `POGA_CHANNEL_ROOT` — `comms_dir()` and `outbox_dir()` now resolve
    through `channel.data_root`, so an un-redirected channel would let a fixture reach
    `~/deploy/federation-channel`, which is a real clone with a real upstream.

    `POGA_DEPLOY_LEDGER_DIR` carries MORE weight than it used to, and the next author has
    to know it: `state_vault()` is `ledger_dir() / "<system>-state"`, so the ledger
    redirection is now what stands between a seeding test and real copies of this machine's
    live state appearing in `~/.local/state/poga/deploy/`. It went from a directory holding
    small JSON records to the durable home of everything a deploy seeds.
    `TheStateVaultOutlivesEveryTreeTest` asserts the redirection holds rather than trusting
    it."""

    system = "widget"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.deploy_root = self.root / "deploy-trees"
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(parents=True)
        # Deliberately ABSENT: `seed_paths` reads {} for a missing file, which is the
        # state of every machine this wave is about. Tests that want a declared map write
        # this file themselves.
        self.seeds = self.root / "seeds.json"

        self._env = {
            "POGA_DEPLOY_REGISTRY": str(self.root / "registry.json"),
            "POGA_DEPLOY_ROOT": str(self.deploy_root),
            "POGA_DEPLOY_LEDGER_DIR": str(self.ledger),
            "POGA_DEPLOY_LAUNCH_AGENTS": str(self.root / "LaunchAgents"),
            # WI-0410, and see DeployRunnerCase for why it points at a directory that does
            # not exist: a derivation that fired by default would silently satisfy the
            # refusal tests in this very file.
            "POGA_DEPLOY_MEMBER_ROOT": str(self.root / "member-repos"),
            "POGA_DEPLOY_SEEDS": str(self.seeds),
            "POGA_DEPLOY_OUTBOX_DIR": str(self.root / "outbox"),
            "POGA_DEPLOY_COMMS_DIR": str(self.root / "comms"),
            "POGA_DEPLOY_MAILBOXES": str(self.root / "mailboxes.json"),
            "POGA_CHANNEL_ROOT": str(self.root / "federation-channel"),
            # THE SHARPEST ONE IN THE LIST. `cli_link()` defaults to ~/.local/bin/poga,
            # which on this machine is a live symlink into the trunk clone and is how the
            # operator and every Architect resident on it start a session. A fixture that
            # forgot this line would not write a stray file somewhere harmless — it would
            # repoint the command they type, to a path inside a temp directory that is
            # deleted at tearDown.
            "POGA_CLI_LINK": str(self.root / "bin" / "poga"),
        }
        # Cleared, not redirected — `base.CLEARED_ENV` owns which names and why (WI-0361).
        self._cleared = tuple(base.CLEARED_ENV)
        self._saved = {k: os.environ.get(k) for k in (*self._env, *self._cleared)}
        os.environ.update(self._env)
        for k in self._cleared:
            os.environ.pop(k, None)
        self.addCleanup(self._restore_env)

        # launchctl is NEVER exercised, exactly as in test_deploy_runner.py. `unit_target`
        # answers from this dict, so a test declares the loaded units it wants and the
        # suite shells out to nothing; `running_under_unit` answers "" — a human at a
        # terminal — unless a test says otherwise.
        self.units: dict[str, str] = {}
        self.unit_target = patch.object(
            runner, "unit_target", side_effect=lambda label: self.units.get(label)).start()
        self.under_unit = patch.object(runner, "running_under_unit", return_value="").start()
        self.addCleanup(patch.stopall)

        self.said: list[str] = []

    def _restore_env(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- helpers ---------------------------------------------------------------

    def log(self, msg: str) -> None:
        self.said.append(msg)

    def _wd(self, entry=None) -> Path:
        return runner.work_dir(self.system, entry or {"subdir": None})

    def _tree(self, entry=None) -> Path:
        wd = self._wd(entry)
        wd.mkdir(parents=True, exist_ok=True)
        return wd

    def _write_seed_file(self, mapping: dict) -> None:
        self.seeds.write_text(json.dumps({"systems": {self.system: mapping}}))

    def _ledger_row(self, name: str, **fields) -> Path:
        p = self.ledger / f"{name}.json"
        p.write_text(json.dumps({"system": name, **fields}, indent=2))
        return p


# -- retiring_root ---------------------------------------------------------------

class TheRetiringRootIsDerivedFromTheRunningUnitsTest(SealedRootCase):
    """Where does a first deploy COPY state from, when nobody wrote a map?

    From the checkout the system is executing out of right now, read from the loaded
    units' WorkingDirectory. A loaded job keeps the directory it was bootstrapped with, so
    that is the one source that cannot be stale — every file on disk claiming otherwise is
    a claim, and this is the process.
    """

    def _checkout(self, name: str, *, is_git: bool = True) -> Path:
        p = self.root / name
        if is_git:
            _make_repo(p)
        else:
            p.mkdir(parents=True)
        return p

    def test_the_loaded_units_working_directory_is_the_retiring_root(self):
        old = self._checkout("devclone")
        self.units["com.widget.runner"] = str(old)
        got = runner.retiring_root(
            self.system, {"subdir": None}, {"units": ["com.widget.runner"]})
        self.assertEqual(got, old)

    def test_a_unit_already_inside_the_deploy_tree_is_not_a_source(self):
        """Where we are GOING is not where we came from. Treating the deploy tree as its
        own retiring root would 'seed' every file from itself, report success, and leave
        the tree exactly as empty as it was — the silent wrong answer this wave exists to
        stop, dressed as a completed step."""
        wd = self._tree()
        self.units["com.widget.runner"] = str(wd)
        self.assertIsNone(runner.retiring_root(
            self.system, {"subdir": None}, {"units": ["com.widget.runner"]}))

    def test_a_subdirectory_of_the_deploy_tree_is_not_a_source_either(self):
        """The prefix check, not just equality: a unit whose WorkingDirectory is a
        subdirectory of the tree is still pointing at the destination."""
        wd = self._tree()
        inner = wd / "sub"
        _make_repo(inner)
        self.units["com.widget.runner"] = str(inner)
        self.assertIsNone(runner.retiring_root(
            self.system, {"subdir": None}, {"units": ["com.widget.runner"]}))

    def test_a_unit_target_that_is_not_a_checkout_is_ignored(self):
        """`launchctl print` will happily report a WorkingDirectory that holds no repo at
        all. Copying declared state out of an arbitrary directory that happens to sit
        there would seed whatever was in it, so the candidate has to carry a `.git`."""
        self.units["com.widget.runner"] = str(self._checkout("not-a-repo", is_git=False))
        self.assertIsNone(runner.retiring_root(
            self.system, {"subdir": None}, {"units": ["com.widget.runner"]}))

    def test_the_first_usable_unit_wins_and_unloaded_units_are_skipped(self):
        """`unit_target` answers None for a unit that is not loaded. A system mid-cutover
        has some units loaded and some not, so 'not loaded' must skip rather than end the
        search."""
        old = self._checkout("devclone")
        self.units["com.widget.b"] = str(old)          # "a" is not loaded at all
        got = runner.retiring_root(
            self.system, {"subdir": None},
            {"units": ["com.widget.a", "com.widget.b"]})
        self.assertEqual(got, old)

    def test_the_self_row_falls_back_to_this_runners_own_checkout(self):
        """On a FIRST self-deploy the program doing the seeding IS the retiring process
        root — the most direct answer there is. Second rather than first because a live
        WorkingDirectory is evidence and an executable's location is an inference; they
        agree on the ordinary path, and when they disagree the running job is true."""
        here = _make_repo(self.root / "process-root")
        with patch.object(runner, "REPO_ROOT", here):
            got = runner.retiring_root(
                "federation", {"self": True}, {"units": ["com.federation.deploy-sweep"]})
        self.assertEqual(got, here)

    def test_a_non_self_row_with_no_usable_unit_target_answers_none(self):
        """The fallback is scoped to the self row on purpose. This runner's checkout is
        the federation's, and handing it to some other system as a seed source would copy
        the federation's own files into a stranger's deploy tree."""
        here = _make_repo(self.root / "process-root")
        with patch.object(runner, "REPO_ROOT", here):
            self.assertIsNone(runner.retiring_root(
                self.system, {"subdir": None}, {"units": ["com.widget.runner"]}))

    def test_a_sealed_process_root_is_not_a_fallback_even_for_the_self_row(self):
        """Once the runner is itself executing from a sealed tree there is no retiring
        checkout left to read — answering with it would be the same seed-from-yourself
        no-op as the unit case above, and would make the required-state verdict pass on a
        tree that holds nothing."""
        sealed = _make_repo(self.root / "sealed-root", detached=True)
        with patch.object(runner, "REPO_ROOT", sealed):
            self.assertIsNone(runner.retiring_root(
                "federation", {"self": True}, {"units": []}))


# -- seed_state ------------------------------------------------------------------

class SeedingDerivesItsSourceWhenNobodyWroteAMapTest(SealedRootCase):
    """THE DEFECT. The deploy host had no seed map at all, so every declared state path
    resolved to "this machine declares no seed source" and the deploy tree would have been
    cut over holding none of the three files that locate member repos. `seed_state` now
    derives the source rather than requiring one to have been written by hand.
    """

    CONTRACT = {"units": ["com.widget.runner"],
                "state": [{"path": "reconcile-roots.local", "required": True}]}

    def setUp(self):
        super().setUp()
        self.wd = self._tree()
        self.old = _make_repo(self.root / "devclone")
        (self.old / "reconcile-roots.local").write_text("MEMBER ROOTS FROM THE OLD CLONE\n")
        self.units["com.widget.runner"] = str(self.old)

    def test_state_is_seeded_from_the_derived_retiring_root(self):
        seeded = runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                                   self.log, dry_run=False)
        self.assertEqual(seeded, ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(),
                         "MEMBER ROOTS FROM THE OLD CLONE\n")
        # COPY, never move: the retiring checkout keeps its copy as a fallback, which is
        # what makes an aborted cutover recoverable.
        self.assertTrue((self.old / "reconcile-roots.local").exists())
        self.assertTrue(any("derived from the checkout" in s for s in self.said), self.said)

    def test_seeding_never_overwrites_state_already_in_the_deploy_tree(self):
        """A second deploy must leave accumulated state entirely alone. Overwriting it
        from the retiring checkout on every deploy would silently roll the live file back
        to whatever the abandoned clone last held — unrecoverable and unannounced."""
        (self.wd / "reconcile-roots.local").write_text("MUCH LATER\n")
        seeded = runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                                   self.log, dry_run=False)
        self.assertEqual(seeded, [])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "MUCH LATER\n")

    def test_a_declared_seed_path_still_wins_over_the_derived_one(self):
        """Derivation is the FALLBACK, not a replacement. A machine that has deliberately
        written a map has said where its state comes from, and a runner that overrode that
        with its own inference would make the declaration unusable."""
        declared = self.root / "declared-source.local"
        declared.write_text("DECLARED\n")
        self._write_seed_file({"reconcile-roots.local": str(declared)})
        runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                          self.log, dry_run=False)
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "DECLARED\n")
        # And nothing was derived at all: the `launchctl print` per unit is not paid when
        # the answer is already declared.
        self.assertEqual(self.unit_target.call_count, 0)

    def test_required_state_seedable_from_nowhere_refuses_before_anything_restarts(self):
        """The reversal WI-0360 makes. These three files are how the adopt-runner LOCATES
        MEMBER REPOS AT ALL, so their absence is not a degraded walk, it is a nightly run
        that finds zero members and reports a clean night. A refusal at deploy time, with
        the previous version still running and somebody's attention on it, is strictly
        better than that."""
        self.units.clear()                       # nothing loaded, nothing to derive from
        with self.assertRaises(runner.DeployError) as cm:
            runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                              self.log, dry_run=False)
        msg = str(cm.exception)
        self.assertIn("required state absent after seeding", msg)
        self.assertIn("reconcile-roots.local", msg)
        self.assertIn("nothing was restarted", msg)
        # ALL THREE TIERS NAMED, and the vault named BY PATH. A refusal that says only
        # "could not be derived" leaves the operator unable to tell whether the vault was
        # consulted and empty or never consulted at all — two very different things to do
        # next, rendered identically. The path is in the message because the next action is
        # to go and look in it, and it is machine-local state nobody has memorised.
        self.assertIn("seed map", msg)
        self.assertIn("state vault", msg)
        self.assertIn(str(runner.state_vault(self.system)), msg)
        self.assertIn("the checkout this system runs on today", msg)

    def test_a_dry_run_reports_the_gap_without_refusing(self):
        """A dry run is a question, not a deploy. Raising here would make `--dry-run`
        unusable on exactly the machine whose state is missing — the one someone runs it
        on to find out."""
        self.units.clear()
        seeded = runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                                   self.log, dry_run=True)
        self.assertEqual(seeded, [])
        self.assertFalse((self.wd / "reconcile-roots.local").exists())

    def test_a_dry_run_copies_nothing_even_when_it_could(self):
        seeded = runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                                   self.log, dry_run=True)
        self.assertEqual(seeded, ["reconcile-roots.local"])   # it says what it WOULD do
        self.assertFalse((self.wd / "reconcile-roots.local").exists())


# -- write_seed_map --------------------------------------------------------------

class TheSeedMapIsAnOutputNotAPreconditionTest(SealedRootCase):
    """W2's inversion. Read as an INPUT the map is a file a human must write on every
    machine before a first deploy works — and the machine it was most needed on had never
    had one. Written as an OUTPUT it is produced by the one deploy that could still see
    the retiring checkout, and nobody is needed.

    AND IT HAS TO POINT OUT OF THE TREE. The first cut of this function mapped each state
    path to the deploy tree's own copy, which reads sensibly and cannot work: `seed_state`
    skips any path already present, so the map is consulted ONLY when the file is absent
    from the tree — and an entry naming that same tree is then absent too. It satisfied the
    `required` check and could do nothing else. The vault beside the ledger is the fix, and
    the re-seed test below is the property the old shape only claimed.
    """

    CONTRACT = {"units": ["com.widget.runner"],
                "state": [{"path": "reconcile-roots.local", "required": True},
                          {"path": "deploy/seed-paths.local.json", "required": True}]}

    def setUp(self):
        super().setUp()
        self.wd = self._tree()
        self.vault = runner.state_vault(self.system)
        self.old = _make_repo(self.root / "devclone")
        (self.old / "reconcile-roots.local").write_text("ROOTS\n")
        self.units["com.widget.runner"] = str(self.old)
        self.map_path = self.wd / "deploy" / "seed-paths.local.json"

    def test_the_map_points_at_the_vault_and_never_at_the_deploy_tree(self):
        """The whole correction. An entry inside the tree is unreachable exactly when it
        is wanted, so a map that names one is indistinguishable from no map at all —
        except that it also silences the derivation (see the suppression test below)."""
        (self.wd / "reconcile-roots.local").write_text("ROOTS\n")
        runner.vault_state(self.system, {"subdir": None}, self.CONTRACT, self.log)
        wrote = runner.write_seed_map(self.system, {"subdir": None}, self.CONTRACT,
                                      self.log, dry_run=False)
        self.assertTrue(wrote)
        doc = json.loads(self.map_path.read_text())
        mapped = doc["systems"][self.system]
        self.assertIn("reconcile-roots.local", mapped)
        for rel, src in mapped.items():
            self.assertEqual(Path(src), self.vault / rel)
            self.assertTrue(Path(src).is_absolute(),
                            "a seed source is machine-local and must be absolute")
            self.assertFalse(
                Path(src).is_relative_to(self.wd),
                f"{rel} is mapped to {src}, inside the deploy tree — the one place a "
                f"rollback, a `checkout --force` or a re-clone can take it away")
        self.assertIn("GENERATED", doc["//"])

    def test_an_entry_the_vault_does_not_hold_is_omitted_rather_than_mapped(self):
        """`deploy/seed-paths.local.json` is the natural case: on a first deploy it does
        not exist in the tree when `vault_state` runs, so the vault cannot hold it and the
        map must leave it out. Mapping it anyway would name a path that is not there."""
        runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                          self.log, dry_run=False)
        mapped = json.loads(self.map_path.read_text())["systems"][self.system]
        self.assertEqual(set(mapped), {"reconcile-roots.local"})
        for src in mapped.values():
            self.assertTrue(Path(src).exists())

    def test_the_maps_own_entry_appears_once_the_vault_has_seen_it(self):
        """And it stabilises: the second deploy finds the map in the tree, vaults it, and
        can then name it. An omission that never healed would be a permanent gap."""
        entry = {"subdir": None}
        runner.seed_state(self.system, entry, self.CONTRACT, self.log, dry_run=False)
        runner.seed_state(self.system, entry, self.CONTRACT, self.log, dry_run=False)
        mapped = json.loads(self.map_path.read_text())["systems"][self.system]
        self.assertEqual(set(mapped),
                         {"reconcile-roots.local", "deploy/seed-paths.local.json"})
        for src in mapped.values():
            self.assertTrue(Path(src).exists())

    # ONE state path in both fall-through tests, so a call count or a log line is
    # unambiguously about THIS path and not about a second entry resolving its own source.
    ONE_PATH = {"units": ["com.widget.runner"],
                "state": [{"path": "reconcile-roots.local", "required": True}]}

    def test_a_map_entry_naming_a_missing_path_falls_through_to_the_vault(self):
        """A MAP IS A HINT ABOUT WHERE STATE MIGHT BE, NEVER A CLAIM THAT IT IS NOWHERE
        ELSE. This inverts what the first cut did and what this test used to pin.

        The old rule took a declared entry as THE answer and, finding the file absent,
        logged "seed source does not exist" and stopped — so a single stale line in a
        hand-written map, on a machine whose paths had moved, silenced both the vault and
        the derivation and refused a deploy that had two working sources sitting right
        there. The cost of falling through is one extra `exists()`; the cost of not falling
        through is a refused deploy whose reason is a line somebody typed months ago."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("RECOVERED FROM THE VAULT\n")
        gone = self.root / "gone.local"
        self._write_seed_file({"reconcile-roots.local": str(gone)})

        seeded = runner.seed_state(self.system, {"subdir": None}, self.ONE_PATH,
                                   self.log, dry_run=False)

        self.assertEqual(seeded, ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(),
                         "RECOVERED FROM THE VAULT\n")
        # And it SAYS the map was wrong, naming the path it looked at. A silent fall-through
        # would leave a stale map entry to be discovered years later by someone wondering
        # why it never mattered.
        self.assertTrue(any(str(gone) in s and "looking further" in s for s in self.said),
                        self.said)

    def test_a_map_entry_naming_a_missing_path_falls_all_the_way_to_the_derivation(self):
        """Two tiers missing is not different in kind from one. The declared path is gone,
        the vault is empty — a machine that has never completed a deploy — and the retiring
        checkout still has the file. Refusing here would be refusing with the answer in
        plain sight."""
        gone = self.root / "gone.local"
        self._write_seed_file({"reconcile-roots.local": str(gone)})
        self.assertFalse(self.vault.exists())

        seeded = runner.seed_state(self.system, {"subdir": None}, self.ONE_PATH,
                                   self.log, dry_run=False)

        self.assertEqual(seeded, ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "ROOTS\n")
        self.assertTrue(any("derived from the checkout" in s for s in self.said), self.said)

    def test_the_vault_re_seeds_a_tree_whose_own_copy_was_thrown_away(self):
        """THE PROPERTY THE OLD MAP ONLY CLAIMED, and the reason the vault exists.

        A rollback, a `checkout --force` or a deleted-and-re-cloned tree takes the tree's
        copy with it, and by then the retiring checkout is long gone — that is the whole
        point of having retired it. `retiring_root` is made to RAISE here, so a pass cannot
        come from the derivation quietly doing the work the vault is supposed to do."""
        entry = {"subdir": None}
        runner.seed_state(self.system, entry, self.CONTRACT, self.log, dry_run=False)
        self.assertTrue((self.vault / "reconcile-roots.local").exists())

        # The tree loses its copy; the checkout it came from no longer exists; no unit is
        # loaded anywhere. The vault is the only surviving source on the machine.
        (self.wd / "reconcile-roots.local").unlink()
        shutil.rmtree(self.old)
        self.units.clear()
        # Post-cutover, REPO_ROOT IS the deploy tree, so the generated map is this
        # machine's declared map.
        os.environ["POGA_DEPLOY_SEEDS"] = str(self.map_path)
        boom = patch.object(
            runner, "retiring_root",
            side_effect=AssertionError("the vault must re-seed without deriving")).start()

        seeded = runner.seed_state(self.system, entry, self.CONTRACT, self.log,
                                   dry_run=False)
        self.assertEqual(seeded, ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "ROOTS\n")
        self.assertEqual(boom.call_count, 0)

    def test_a_dry_run_writes_no_map(self):
        """A dry run that wrote the map would leave a real file behind claiming a deploy
        had happened, and the next real deploy would read it as settled fact."""
        wrote = runner.write_seed_map(self.system, {"subdir": None}, self.CONTRACT,
                                      self.log, dry_run=True)
        self.assertTrue(wrote)                   # it reports what it WOULD do
        self.assertFalse(self.map_path.exists())
        self.assertTrue(any("would record" in s for s in self.said), self.said)

    def test_a_contract_with_no_state_writes_nothing(self):
        self.assertFalse(runner.write_seed_map(self.system, {"subdir": None},
                                               {"units": []}, self.log, dry_run=False))
        self.assertFalse(self.map_path.exists())

    def test_the_map_is_written_before_the_required_state_verdict(self):
        """`deploy/seed-paths.local.json` is itself one of the three required entries, and
        nothing can seed it — it does not exist in the retiring checkout either. It passes
        the verdict because `seed_state` PRODUCES it, so the ordering is the only reason
        this deploy is not refused. Move the write after the verdict and the federation
        can never deploy itself again."""
        contract = {"units": ["com.widget.runner"],
                    "state": [{"path": "deploy/seed-paths.local.json", "required": True}]}
        runner.seed_state(self.system, {"subdir": None}, contract, self.log, dry_run=False)
        self.assertTrue(self.map_path.exists())

    def test_a_later_deploy_reads_the_generated_map_and_derives_nothing(self):
        """THE POINT OF THE MAP. After cutover the runner executes from the deploy tree,
        so `seed_paths` reads the map this deploy just wrote — and the retiring checkout it
        was derived from may be gone. `retiring_root` is made to explode here: reaching it
        at all would mean the map had not removed the dependency it exists to remove."""
        runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                          self.log, dry_run=False)
        self.assertTrue(self.map_path.exists())

        # The post-cutover world: REPO_ROOT is the deploy tree, so the generated map IS
        # this machine's declared map, and the old clone is unreachable.
        os.environ["POGA_DEPLOY_SEEDS"] = str(self.map_path)
        shutil.rmtree(self.old)
        self.units.clear()
        declared = runner.seed_paths(self.system)
        self.assertTrue(declared, "the generated map declares nothing at all")
        for rel, src in declared.items():
            self.assertTrue(Path(src).exists(),
                            f"the generated map points {rel} at {src}, which does not "
                            f"exist — a map whose sources are gone is worse than no map, "
                            f"because a named-but-missing source stops `seed_state` "
                            f"looking any further")

        boom = patch.object(
            runner, "retiring_root",
            side_effect=AssertionError(
                "a second deploy must not need the retiring checkout")).start()
        runner.seed_state(self.system, {"subdir": None}, self.CONTRACT,
                          self.log, dry_run=False)
        self.assertEqual(boom.call_count, 0)


# -- the vault as a source tier --------------------------------------------------

class TheVaultIsASourceTierNotJustAMapEntryTest(SealedRootCase):
    """The vault is asked BY NAME, not only through the map that points at it.

    TWO THINGS WERE WRONG WITH REACHING IT ONLY THROUGH THE MAP, and both are tested here
    rather than argued.

      * THE MAP IS WRITE-ONLY FOR EVERY SYSTEM BUT ONE. `write_seed_map` writes into the
        system's own deploy tree; `seed_paths` reads the RUNNER's `REPO_ROOT`. Those are
        the same file only for the `self` row after cutover, so every non-self
        member got a vault full of state and no way to ask for it.

      * THE POINTER LIVED IN THE THING IT POINTED OUT OF. A rollback keeps the in-tree
        map; `rm -rf` and re-clone does not. The vault survived exactly the failure whose
        pointer did not.

    Asking `state_vault(system)/rel` directly costs one `exists()` and needs nothing to
    have survived. This case builds a NON-self row deliberately, with `REPO_ROOT` pointed
    at a runner checkout that is not the deploy tree — the arrangement in which the map is
    unreachable — so these are direct tests of the broken case, not variants of a working
    one.
    """

    CONTRACT = {"units": ["com.widget.runner"],
                "state": [{"path": "reconcile-roots.local", "required": True}]}
    FROM_CHECKOUT = "FROM THE RETIRING CHECKOUT\n"

    def setUp(self):
        super().setUp()
        self.entry = {"subdir": None}          # a NON-self row: no `self` key at all
        self.wd = self._tree()
        self.vault = runner.state_vault(self.system)
        self.old = _make_repo(self.root / "devclone")
        (self.old / "reconcile-roots.local").write_text(self.FROM_CHECKOUT)
        self.units["com.widget.runner"] = str(self.old)

        # The runner's own checkout, which for a non-self row is NOT this system's deploy
        # tree. `POGA_DEPLOY_SEEDS` is unset so `seed_paths` resolves the map the way
        # production does — through REPO_ROOT — which is the whole point of the case.
        self.process_root = self.root / "process-root"
        (self.process_root / "deploy").mkdir(parents=True)
        patch.object(runner, "REPO_ROOT", self.process_root).start()
        os.environ.pop("POGA_DEPLOY_SEEDS", None)

    def _seed(self):
        return runner.seed_state(self.system, self.entry, self.CONTRACT,
                                 self.log, dry_run=False)

    def _forbid_derivation(self):
        """`retiring_root` made to raise, so nothing below can pass by deriving."""
        return patch.object(
            runner, "retiring_root",
            side_effect=AssertionError("the vault must answer without deriving")).start()

    def test_a_non_self_rows_unreachable_map_does_not_strand_its_vault(self):
        """THE PREVIOUSLY WRITE-ONLY CASE. The map this deploy writes is real, and nothing
        will ever read it; the vault it describes has to be reachable anyway."""
        self.assertEqual(self._seed(), ["reconcile-roots.local"])
        map_in_tree = self.wd / "deploy" / "seed-paths.local.json"
        self.assertTrue(map_in_tree.exists())
        # Asserted, not assumed: the map exists on disk and `seed_paths` cannot see it.
        self.assertEqual(runner.seed_paths(self.system), {},
                         "this row's map is supposed to be unreachable — if it is not, "
                         "this test is no longer exercising the case it was written for")

        (self.wd / "reconcile-roots.local").unlink()
        shutil.rmtree(self.old)
        self.units.clear()
        boom = self._forbid_derivation()

        self.assertEqual(self._seed(), ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), self.FROM_CHECKOUT)
        self.assertEqual(boom.call_count, 0)
        self.assertTrue(any("state vault" in s for s in self.said), self.said)

    def test_a_deleted_and_re_cloned_tree_is_re_seeded_from_the_vault(self):
        """`rm -rf` the tree and clone it again — the case the in-tree map could not
        survive, because the map went with the tree. P21 in one test: the state comes
        back, and it comes back without a pointer, without the retiring checkout, and
        without anybody having written anything by hand."""
        self._seed()
        self.assertTrue((self.vault / "reconcile-roots.local").exists())

        shutil.rmtree(self.wd)                 # the tree, the state and the map, all gone
        self.wd.mkdir(parents=True)            # ensure_tree re-clones an empty one
        shutil.rmtree(self.old)
        self.units.clear()
        self.assertEqual(runner.seed_paths(self.system), {},
                         "there must be no map anywhere for this to prove anything")
        boom = self._forbid_derivation()

        self.assertEqual(self._seed(), ["reconcile-roots.local"])
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), self.FROM_CHECKOUT)
        self.assertEqual(boom.call_count, 0)

    def test_a_declared_map_entry_wins_over_the_vault(self):
        """TIER 1 BEATS TIER 2. A machine that has written a map has said where its state
        comes from; a vault copy silently overriding that declaration would make the map
        unusable and the override invisible — the two together being how you get a system
        running on state nobody chose."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("VAULTED\n")
        declared = self.root / "declared-source.local"
        declared.write_text("DECLARED\n")
        os.environ["POGA_DEPLOY_SEEDS"] = str(self.seeds)
        self._write_seed_file({"reconcile-roots.local": str(declared)})

        self._seed()
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "DECLARED\n")

    def test_the_vault_wins_over_what_the_retiring_checkout_would_have_given(self):
        """TIER 2 BEATS TIER 3, and the order is not cosmetic. The vault is this machine's
        own last-known-good copy of the state the system was actually running; the retiring
        checkout is where it used to live and may have moved on, been partially cleaned, or
        be mid-edit. Reversing the two would prefer the less authoritative source and do it
        silently, which is why the test distinguishes them by CONTENT rather than by
        counting calls alone."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("VAULTED\n")
        third = _make_repo(self.root / "third-checkout")
        (third / "reconcile-roots.local").write_text("DERIVED\n")
        derive = patch.object(runner, "retiring_root", return_value=third).start()

        self._seed()
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "VAULTED\n")
        # Never consulted, and never touched.
        self.assertEqual(derive.call_count, 0)
        self.assertEqual((third / "reconcile-roots.local").read_text(), "DERIVED\n")

    def test_the_derivation_is_never_reached_when_the_map_answers(self):
        """TIER 3 IS LAZY, and this is the kind of thing that regresses silently.

        Deriving costs a `launchctl print` per unit — a subprocess each, in a program that
        runs unattended every ten minutes. Hoisting the derivation above the loop, or
        computing it eagerly "for clarity", would still pass every behavioural test in this
        file: the right file lands in the right place either way, and the only evidence is
        a cost nobody is watching. So it is asserted rather than left to review."""
        declared = self.root / "declared-source.local"
        declared.write_text("DECLARED\n")
        os.environ["POGA_DEPLOY_SEEDS"] = str(self.seeds)
        self._write_seed_file({"reconcile-roots.local": str(declared)})
        boom = self._forbid_derivation()

        self._seed()
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "DECLARED\n")
        self.assertEqual(boom.call_count, 0)
        # And no `launchctl` either, which is the cost the laziness is actually about.
        self.assertEqual(self.unit_target.call_count, 0)

    def test_the_derivation_is_never_reached_when_the_vault_answers(self):
        """The same for tier 2, which is the common case in steady state: after the first
        deploy the vault has everything, so a machine with several systems should pay zero
        `launchctl print` calls per sweep, not one per unit per system."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("VAULTED\n")
        boom = self._forbid_derivation()

        self._seed()
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "VAULTED\n")
        self.assertEqual(boom.call_count, 0)
        self.assertEqual(self.unit_target.call_count, 0)

    def test_the_derivation_still_answers_when_the_vault_is_empty(self):
        """The bottom of the ladder, kept here so the three tiers are pinned in one place.
        A vault tier that accidentally short-circuited the derivation would break the very
        first deploy on a machine that has no vault yet — which is every machine, once."""
        self.assertFalse(self.vault.exists())
        self._seed()
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), self.FROM_CHECKOUT)
        self.assertTrue(any("derived from the checkout" in s for s in self.said), self.said)


# -- the state vault -------------------------------------------------------------

class TheStateVaultOutlivesEveryTreeTest(SealedRootCase):
    """State that survives the tree being thrown away (P21).

    A deploy tree is disposable BY DESIGN — `checkout --force` over it, a rollback to an
    older tag, or delete it and re-clone. Everything gitignored in it goes with it, and
    the three files this wave is about are gitignored. The vault is the machine-local copy
    that is not inside anything a deploy touches, which is what lets the map name a source
    that is still there when the seed is actually needed.
    """

    CONTRACT = {"units": [], "state": [{"path": "reconcile-roots.local", "required": True}]}

    def setUp(self):
        super().setUp()
        self.wd = self._tree()
        self.vault = runner.state_vault(self.system)
        self.entry = {"subdir": None}

    def test_the_vault_is_machine_local_state_beside_the_ledger(self):
        """Named here because the fixture's safety now depends on it: the vault lives
        under `ledger_dir()`, so a test that forgot to redirect `POGA_DEPLOY_LEDGER_DIR`
        would copy this machine's live state into `~/.local/state/poga/deploy/`. Asserting
        the redirection holds is cheaper than discovering it from the files left behind."""
        self.assertEqual(self.vault, runner.ledger_dir() / f"{self.system}-state")
        self.assertTrue(self.vault.is_relative_to(Path(self._env["POGA_DEPLOY_LEDGER_DIR"])))
        self.assertFalse(self.vault.is_relative_to(Path.home() / ".local"))
        # And outside every tree a deploy can touch.
        self.assertFalse(self.vault.is_relative_to(runner.deploy_root()))

    def test_state_in_the_tree_is_mirrored_into_the_vault(self):
        (self.wd / "reconcile-roots.local").write_text("ROOTS\n")
        runner.vault_state(self.system, self.entry, self.CONTRACT, self.log)
        self.assertEqual((self.vault / "reconcile-roots.local").read_text(), "ROOTS\n")
        # COPY, never move: the tree keeps the live copy its units read.
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(), "ROOTS\n")

    def test_a_state_path_the_tree_does_not_hold_is_not_vaulted(self):
        """Absence is not something to mirror. A vault entry conjured for a file that was
        never there would then be mapped as a seed source and hand the system an empty
        file where it expected its own state."""
        runner.vault_state(self.system, self.entry, self.CONTRACT, self.log)
        self.assertFalse((self.vault / "reconcile-roots.local").exists())

    def test_a_newer_vault_copy_is_never_overwritten_by_an_older_tree_copy(self):
        """COPY-IF-NEWER, and this is the direction that matters. A deploy that clobbered
        the vault with whatever the tree happened to hold would destroy the recovery copy
        at exactly the moment a half-restored tree made it valuable — and it would do so
        silently, because the vault is the thing nobody looks at until they need it."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("NEWER, IN THE VAULT\n")
        (self.wd / "reconcile-roots.local").write_text("older, in the tree\n")
        # Pin both mtimes: a test over two files without this asserts the clock, not the
        # rule ([`a-relative-predicate-needs-both-sides-pinned`]).
        os.utime(self.wd / "reconcile-roots.local", (1_700_000_000, 1_700_000_000))
        os.utime(self.vault / "reconcile-roots.local", (1_700_000_500, 1_700_000_500))

        runner.vault_state(self.system, self.entry, self.CONTRACT, self.log)
        self.assertEqual((self.vault / "reconcile-roots.local").read_text(),
                         "NEWER, IN THE VAULT\n")
        # And never the other direction: the vault is a recovery source, not a second
        # writer on state the running system owns.
        self.assertEqual((self.wd / "reconcile-roots.local").read_text(),
                         "older, in the tree\n")

    def test_a_newer_tree_copy_does_refresh_the_vault(self):
        """The negative control for the rule above. A vault frozen at the first deploy
        would quietly become a stale answer, and 'never overwrites' would be satisfied by
        a function that copies nothing at all."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "reconcile-roots.local").write_text("stale\n")
        (self.wd / "reconcile-roots.local").write_text("FRESH\n")
        os.utime(self.vault / "reconcile-roots.local", (1_700_000_000, 1_700_000_000))
        os.utime(self.wd / "reconcile-roots.local", (1_700_000_500, 1_700_000_500))

        runner.vault_state(self.system, self.entry, self.CONTRACT, self.log)
        self.assertEqual((self.vault / "reconcile-roots.local").read_text(), "FRESH\n")

    def test_a_directory_state_path_is_re_vaulted_after_an_in_place_edit(self):
        """COPY-IF-NEWER IS FOR FILES ONLY, and this is the measurement that settles why.

        A directory's mtime moves when an entry is ADDED OR REMOVED and does not move when
        a file inside it is edited in place — measured on this filesystem, not assumed. An
        mtime comparison on a directory therefore stops re-vaulting it after the first
        in-place edit, forever, silently: the vault keeps answering, with state that is
        months stale. `seed-paths.local.example.json` documents `cache/` as a supported
        state shape, so this is a shape the contract invites, not a hypothetical.

        The vault's copy is pinned NEWER than the tree's so that an mtime rule would skip.
        Only a files-only rule can pass this ([`a-relative-predicate-needs-both-sides-pinned`])."""
        contract = {"units": [], "state": [{"path": "cache", "required": False}]}
        cache = self.wd / "cache"
        cache.mkdir()
        (cache / "entry.json").write_text("one")
        runner.vault_state(self.system, self.entry, contract, self.log)
        self.assertEqual((self.vault / "cache" / "entry.json").read_text(), "one")

        # Edited IN PLACE: no entry added, none removed, so the directory's own mtime is
        # untouched by the edit.
        (cache / "entry.json").write_text("TWO, EDITED IN PLACE")
        os.utime(cache, (1_700_000_000, 1_700_000_000))
        os.utime(self.vault / "cache", (1_700_000_500, 1_700_000_500))

        runner.vault_state(self.system, self.entry, contract, self.log)
        self.assertEqual((self.vault / "cache" / "entry.json").read_text(),
                         "TWO, EDITED IN PLACE",
                         "the vault kept a stale copy of a directory state path — an "
                         "mtime comparison on a directory cannot see an in-place edit")

    def test_a_directory_re_vault_drops_entries_the_tree_no_longer_has(self):
        """Wholesale re-copy, not a merge. A vault that accumulated files the live tree had
        deleted would re-seed them on recovery, resurrecting state the system had removed
        on purpose."""
        contract = {"units": [], "state": [{"path": "cache", "required": False}]}
        cache = self.wd / "cache"
        cache.mkdir()
        (cache / "keep.json").write_text("keep")
        (cache / "drop.json").write_text("drop")
        runner.vault_state(self.system, self.entry, contract, self.log)
        self.assertTrue((self.vault / "cache" / "drop.json").exists())

        (cache / "drop.json").unlink()
        runner.vault_state(self.system, self.entry, contract, self.log)
        self.assertTrue((self.vault / "cache" / "keep.json").exists())
        self.assertFalse((self.vault / "cache" / "drop.json").exists())

    def test_nothing_is_written_into_the_vault_on_a_dry_run(self):
        """A dry run must leave the machine exactly as it found it. Vaulting on a dry run
        would be the quietest possible violation — a real, durable, machine-local write
        performed by the verb whose entire contract is that it performs none."""
        (self.wd / "reconcile-roots.local").write_text("ROOTS\n")
        runner.seed_state(self.system, self.entry, self.CONTRACT, self.log, dry_run=True)
        self.assertFalse(self.vault.exists(),
                         f"a dry run created {self.vault}")


# -- anything_cut_over -----------------------------------------------------------

class HasThisMachineAdoptedTheSealedModelTest(SealedRootCase):
    """The guard's third condition, and the one that keeps it from bricking the first
    cutover. `awaiting-cutover` is its own recorded status, so `deployed` is exactly the
    ledger's word for "this system's units run out of its deploy tree".
    """

    def test_an_empty_ledger_directory_is_false(self):
        self.assertFalse(runner.anything_cut_over())

    def test_a_missing_ledger_directory_is_false_rather_than_an_error(self):
        """A machine that has never deployed anything has no ledger directory at all, and
        that must read as 'nothing cut over', never as a crash in the guard."""
        os.environ["POGA_DEPLOY_LEDGER_DIR"] = str(self.root / "never-created")
        self.assertFalse(runner.anything_cut_over())

    def test_awaiting_cutover_is_not_cut_over(self):
        """The distinction the whole guard rests on: a deployed TREE whose units still
        point at the developer clone is precisely the un-adopted state, and refusing there
        would refuse the sweep that performs the first cutover."""
        self._ledger_row("example-app", status="awaiting-cutover", version="v1.0.0")
        self.assertFalse(runner.anything_cut_over())

    def test_one_deployed_row_is_enough(self):
        """The question is whether this MACHINE has adopted the sealed model, not which
        system did it first."""
        self._ledger_row("example-app", status="awaiting-cutover")
        self._ledger_row("example-service", status="deployed", version="v2.0.0")
        self.assertTrue(runner.anything_cut_over())

    def test_a_malformed_ledger_is_skipped_rather_than_fatal(self):
        """A corrupt row must not take the guard — and therefore every sweep on the
        machine — down with it."""
        (self.ledger / "broken.json").write_text("{not json at all")
        self.assertFalse(runner.anything_cut_over())
        self._ledger_row("example-service", status="deployed")
        self.assertTrue(runner.anything_cut_over())


# -- refuse_unsealed_process_root ------------------------------------------------

class TheGuardRefusesOnlyTheUnattendedWriterTest(SealedRootCase):
    """All THREE conditions, from both sides.

    The hazard is a SCHEDULED job committing into a checkout somebody develops in: nobody
    sees the strand for hours. A person at a terminal is watching the output and is not a
    scheduled writer, so refusing them would be a guard firing on correct use — and a
    guard that fires on correct use is one somebody deletes. Each negative case below is a
    deletion pressure this file removes.
    """

    def setUp(self):
        super().setUp()
        self.working_clone = _make_repo(self.root / "trunk-clone")
        self.sealed_tree = _make_repo(self.root / "sealed-tree", detached=True)
        self._ledger_row("example-service", status="deployed", version="v2.0.0")

    def test_a_scheduled_job_in_a_working_clone_after_a_cutover_refuses(self):
        self.under_unit.return_value = "com.federation.deploy-sweep"
        with patch.object(runner, "REPO_ROOT", self.working_clone):
            with self.assertRaises(runner.DeployError) as cm:
                runner.refuse_unsealed_process_root()
        msg = str(cm.exception)
        self.assertIn("REFUSED to run under com.federation.deploy-sweep", msg)
        self.assertIn(str(self.working_clone), msg)
        # It must say what to DO, and that nothing was broken by refusing.
        self.assertIn("nothing was deployed", msg)
        self.assertIn("WorkingDirectory", msg)

    def test_a_human_at_a_terminal_is_never_refused(self):
        """THE FALSE POSITIVE THAT WOULD GET THIS DELETED. `python3 deploy/runner.py
        example-app`, typed in a trunk clone on a machine that has completed one deploy, is
        correct use — and taken literally the ruling refuses it. `running_under_unit()`
        returning "" is the whole narrowing, and this is its negative control."""
        self.under_unit.return_value = ""
        with patch.object(runner, "REPO_ROOT", self.working_clone):
            runner.refuse_unsealed_process_root()          # must not raise

    def test_a_job_already_running_from_a_sealed_tree_is_never_refused(self):
        """The arrangement the guard exists to reach. Refusing it would mean the sealed
        model, once adopted, could never run a sweep at all."""
        self.under_unit.return_value = "com.federation.deploy-sweep"
        with patch.object(runner, "REPO_ROOT", self.sealed_tree):
            runner.refuse_unsealed_process_root()          # must not raise

    def test_nothing_is_refused_before_the_first_cutover(self):
        """Running from a working clone IS the supported arrangement until something has
        cut over. Refusing here would brick the very sweep that performs the first
        cutover, and the machine could never leave the state the guard objects to."""
        for row in self.ledger.glob("*.json"):
            row.unlink()
        self._ledger_row("example-app", status="awaiting-cutover")
        self.under_unit.return_value = "com.federation.deploy-sweep"
        with patch.object(runner, "REPO_ROOT", self.working_clone):
            runner.refuse_unsealed_process_root()          # must not raise

    def test_the_sweep_refuses_before_it_reads_the_registry(self):
        """Called at the TOP of `sweep()`: the refusal has to land before anything is
        fetched, checked out or written, and before the registry is even read."""
        self.under_unit.return_value = "com.federation.deploy-sweep"
        with patch.object(runner, "REPO_ROOT", self.working_clone), \
             patch.object(runner, "load_registry",
                          side_effect=AssertionError("the sweep read the registry")):
            with self.assertRaises(runner.DeployError):
                runner.sweep()

    def test_the_single_system_verb_refuses_too(self):
        """A guard with a way around it is a guard about which ROUTE was taken rather than
        about the hazard. The sweep is not the only way a unit can invoke this program."""
        self.under_unit.return_value = "com.federation.mail-poller"
        with patch.object(runner, "REPO_ROOT", self.working_clone), \
             patch.object(runner, "registry_entry",
                          side_effect=AssertionError("_deploy got past the guard")):
            with self.assertRaises(runner.DeployError):
                runner._deploy(self.system, self.log, self.log)


# -- the contract ----------------------------------------------------------------

class TheContractRequiresTheThreeStateFilesTest(unittest.TestCase):
    """WI-0360's reversal, pinned in the file it was made in.

    required:false was defensible only while seeding needed a hand-written map no machine
    had. That precondition is gone, and with it the argument. Flipping any of these back
    to false restores the silent-clean-night failure, so the flip has to break a test.
    """

    EXPECTED = {"reconcile-roots.local", "repo-paths.local",
                "deploy/seed-paths.local.json"}

    def setUp(self):
        self.contract = json.loads(
            (REPO / "deploy" / "deploy.json").read_text(encoding="utf-8"))

    def test_the_contract_declares_exactly_the_three_state_paths(self):
        self.assertEqual({i["path"] for i in self.contract["state"]}, self.EXPECTED)

    def test_all_three_state_entries_are_required(self):
        for item in self.contract["state"]:
            self.assertIs(item.get("required"), True,
                          f"{item['path']} is how the adopt-runner and reconcile LOCATE "
                          f"MEMBER REPOS after cutover. required:false means the nightly run "
                          f"finds zero members and reports a clean night — a silent wrong "
                          f"answer, which is worse than a refusal at deploy time.")


# -- W5(a): no unit definition can point at a developer checkout -----------------

class NoUnitDefinitionCanNameADeveloperCheckoutTest(SealedRootCase):
    """THE STRUCTURAL GUARD, and the part that stops this regressing.

    Every `install-*.sh` in this repo resolves `FED_ROOT` to the checkout it was run from,
    which is exactly how the federation's three units came to point at the live trunk. The
    runtime guard above catches the consequence one sweep late; this catches the CAUSE at
    land time. It asserts both halves of the chain, because either alone is satisfiable by
    a broken system: that no template ever hard-codes a path, and that the renderer
    replaces the token with the DEPLOY TREE rather than with wherever the runner happens
    to be standing ([`a-detector-proves-itself-on-the-real-defect`]).
    """

    TOKEN = "__FED_ROOT__"
    TEMPLATES = sorted((REPO / "deploy").glob("*.plist.template"))

    def test_there_are_templates_to_check(self):
        """A guard whose subject has moved reads as a clean pass and is not one
        ([`a-truncated-grep-is-not-a-searched-negative`])."""
        self.assertTrue(self.TEMPLATES, "found no deploy/*.plist.template — the sweep "
                                        "stopped looking, which is not a result")

    def test_every_template_declares_the_token_as_its_working_directory(self):
        for t in self.TEMPLATES:
            with self.subTest(template=t.name):
                declared = plistlib.loads(t.read_bytes())
                self.assertEqual(
                    declared.get("WorkingDirectory"), self.TOKEN,
                    f"{t.name} names a literal WorkingDirectory. A committed absolute "
                    f"path is WI-0132's defect and would be wrong on every other machine "
                    f"that deployed the same tag; worse, the one path anybody would "
                    f"hard-code is the developer checkout the seal exists to leave.")

    def test_rendering_substitutes_the_token_with_a_path_inside_the_deploy_root(self):
        """The other half. A template that says `__FED_ROOT__` proves nothing if the
        renderer fills it with the runner's own checkout — which is precisely what
        `install-*.sh` does, and the regression this pair forbids."""
        entry = {"subdir": None}
        wd = runner.work_dir("federation", entry)
        (wd / "deploy").mkdir(parents=True, exist_ok=True)
        for t in self.TEMPLATES:
            unit = t.name[:-len(".plist.template")]
            with self.subTest(unit=unit):
                shutil.copy(t, wd / "deploy" / t.name)
                # AS THE MACHINE THE TEMPLATE NAMES (WI-0420). This loop's claim is about
                # every template's FED_ROOT substitution, one template at a time; since
                # rendering became host-scoped, running them all as one ambient machine
                # asserts the host rule instead, and silently on whichever box the suite
                # happens to be. `declared_unit_host` is exercised on its own elsewhere.
                # AND WITH A `claude` ON THE PATH, for the same reason: the adopt-runner
                # template carries `__CLAUDE_BIN__` and the render refuses without it, so
                # left ambient this passed in an operator's shell and failed under the
                # nightly's daemon PATH, which has no `~/.local/bin`.
                real_which = shutil.which
                fake_claude = str(wd / "fake-bin" / "claude")
                with patch.object(runner.common, "this_machine",
                                  return_value=runner.declared_unit_host(t)), \
                     patch.object(runner.shutil, "which",
                                  side_effect=lambda name, *a, **kw: (
                                      fake_claude if name == "claude"
                                      else real_which(name, *a, **kw))):
                    out = runner.render_unit_plist("federation", entry, unit, self.log)
                self.assertIsNotNone(out, f"{t.name} rendered nothing")
                text = out.read_text(encoding="utf-8")
                self.assertNotIn(self.TOKEN, text)
                declared = plistlib.loads(out.read_bytes())
                self.assertEqual(declared["Label"], unit)
                self.assertEqual(Path(declared["WorkingDirectory"]), wd)
                self.assertTrue(
                    Path(declared["WorkingDirectory"]).is_relative_to(runner.deploy_root()),
                    f"{unit} would run from {declared['WorkingDirectory']}, which is "
                    f"outside {runner.deploy_root()}")
                self.assertNotIn(str(REPO), text,
                                 f"{unit} carries this runner's own checkout path — the "
                                 f"exact thing cutover exists to end")

    def test_every_registry_row_resolves_its_work_dir_under_the_deploy_root(self):
        """Including the `self` row, and including rows with a `subdir`. `work_dir` is
        what `render_unit_plist` substitutes, so a row that resolved outside the deploy
        root would render a unit pointing there no matter how clean the template is."""
        reg = json.loads((REPO / "deploy" / "registry.json").read_text(encoding="utf-8"))
        rows = reg["systems"]
        self.assertTrue(rows, "the registry lists no systems")
        for name, entry in sorted(rows.items()):
            with self.subTest(system=name):
                wd = runner.work_dir(name, entry)
                self.assertTrue(
                    wd.is_relative_to(runner.deploy_root()),
                    f"{name} resolves to {wd}, outside {runner.deploy_root()} — a unit "
                    f"rendered for it would point at a checkout nobody sealed")


# -- the fall-through reaches a human ---------------------------------------------

class StateNotesReachThePostedReceiptTest(base.DeployRunnerCase):
    """A fall-through that only reaches launchd's log has not reached anybody.

    THE CASE THIS IS ABOUT. Tier 1 no longer short-circuits, which is right: one stale line
    in a hand-written map should not refuse a deploy with two working sources in plain
    sight. But it buys that with a new silence — if the operator's declared path is an
    unmounted volume holding the AUTHORITATIVE copy, the deploy quietly seeds from a vault
    copy that may be weeks old, and reaches a person as "DEPLOYED". A refusal turning into
    a silent wrong answer is the exact failure this whole arrangement exists to end, so the
    fall-through has to travel in the receipt, not only in a log tail nobody reads.

    END TO END ON A REAL DEPLOY, because the defect this guards against is a missed hand-off
    between four places — `seed_state` records, `_deploy` threads, `write_ledger` stores,
    `result_brief` renders — and every one of them passes its own unit test while the note
    still fails to arrive. The assertion is on the TEXT THAT GETS POSTED.
    """

    STATE = {**base.CONTRACT, "state": [{"path": "state.json", "required": True}]}

    def setUp(self):
        super().setUp()
        # Two paths the base fixture predates. Saved and restored here rather than edited
        # into `self._env`, so the base fixture's own teardown stays the only owner of it.
        extra = {"POGA_CHANNEL_ROOT": str(self.root / "federation-channel"),
                 "POGA_CLI_LINK": str(self.root / "bin" / "poga")}
        saved = {k: os.environ.get(k) for k in extra}
        os.environ.update(extra)

        def restore():
            for k, v in saved.items():
                os.environ[k] = v if v is not None else os.environ.pop(k, "")
        self.addCleanup(restore)
        self.vault = runner.state_vault(self.system)

    def _seeds(self, mapping):
        """This machine's declared seed map. Same shape as `TestStateSeeding._seeds` in
        the base file — it lives on that sibling class rather than on the shared fixture,
        so it is spelled again here rather than reached for across the hierarchy."""
        f = self.root / "seeds.json"
        f.write_text(json.dumps({"systems": {self.system: mapping}}))
        os.environ["POGA_DEPLOY_SEEDS"] = str(f)
        self.addCleanup(os.environ.pop, "POGA_DEPLOY_SEEDS", None)

    def _brief_text(self):
        queued = self._queued()
        self.assertEqual(len(queued), 1, f"expected one receipt, got {queued}")
        return queued[0][1].read_text()

    def test_a_fall_through_reaches_the_ledger_and_the_posted_brief(self):
        unmounted = self.root / "volumes" / "archive" / "state.json"
        self._seeds({"state.json": str(unmounted)})
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "state.json").write_text('{"from": "the vault"}')
        self._release("v2.0.0", contract=self.STATE)

        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual((self._tree() / "state.json").read_text(), '{"from": "the vault"}')

        notes = self._ledger().get("state_notes")
        self.assertTrue(notes, "the ledger recorded no state notes for a fall-through")
        self.assertTrue(any(str(unmounted) in n for n in notes), notes)

        # THE PART THAT MATTERS: it is in the text that leaves the machine.
        text = self._brief_text()
        self.assertIn("## State", text)
        self.assertIn(str(unmounted), text,
                      "the receipt does not name the declared path that was missing, so "
                      "the one person who could tell whether it mattered cannot")
        self.assertIn("NOT THERE", text)

    def test_an_ordinary_deploy_from_the_declared_map_says_nothing_about_state(self):
        """SILENCE IS THE NORMAL CASE and has to stay that way. A `## State` section on
        every receipt is a section nobody reads by the third one, and then the fall-through
        is invisible again — this time inside a heading that is always present."""
        src = self.root / "declared-state.json"
        src.write_text('{"from": "the declared source"}')
        self._seeds({"state.json": str(src)})
        self._release("v2.0.0", contract=self.STATE)

        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        self.assertEqual((self._tree() / "state.json").read_text(),
                         '{"from": "the declared source"}')
        self.assertFalse(self._ledger().get("state_notes"))
        self.assertNotIn("## State", self._brief_text())

    def test_seeding_from_the_vault_without_a_declared_map_is_still_reported(self):
        """No map at all is not "the operator got what they asked for" — they asked for
        nothing, and where the state came from is news."""
        self.vault.mkdir(parents=True, exist_ok=True)
        (self.vault / "state.json").write_text('{"from": "the vault"}')
        self._release("v2.0.0", contract=self.STATE)

        rc, _ = self._deploy()
        self.assertEqual(rc, 0)
        text = self._brief_text()
        self.assertIn("## State", text)
        self.assertIn("state vault", text)


# -- the CLI link ------------------------------------------------------------------

class TheCliLinkFollowsTheDeployTreeTest(SealedRootCase):
    """`~/.local/bin/poga` is not a unit, so nothing in the cutover was looking at it.

    Measured: it is a symlink into the process clone. Retire the process root
    with the link still pointing into it and every Architect session that starts
    through that command breaks, with nothing in this program having mentioned it.

    It is the one irreversible-shaped act in the cutover, so every case below is about it
    REFUSING rather than guessing.
    """

    def setUp(self):
        super().setUp()
        self.entry = {"self": True, "subdir": None}
        self.wd = self._tree(self.entry)
        self.link = runner.cli_link()
        self.link.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_link = self.link.with_name(self.link.name + ".repoint")
        # What the link points at before the cutover: the retiring process clone.
        self.old_clone = self.root / "process-clone"
        self.old_clone.mkdir()
        (self.old_clone / "poga").write_text("#!/bin/sh\n# the old clone's CLI\n")

    def _tree_has_poga(self):
        (self.wd / "poga").write_text("#!/bin/sh\n# the deployed CLI\n")
        return self.wd / "poga"

    def _repoint(self, dry_run=False):
        return runner.repoint_cli_link(self.system, self.entry, self.log, dry_run)

    def test_a_link_pointing_outside_the_tree_is_repointed(self):
        target = self._tree_has_poga()
        self.link.symlink_to(self.old_clone / "poga")

        self.assertTrue(self._repoint())
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(self.link.resolve(), target.resolve())
        self.assertFalse(self.tmp_link.exists() or self.tmp_link.is_symlink(),
                         "a .repoint temp survived a successful repoint")
        # The old clone is untouched: this moves a pointer, it does not delete a CLI.
        self.assertTrue((self.old_clone / "poga").exists())

    def test_a_real_file_is_refused_and_said_so(self):
        """Somebody else's install, and not ours to replace. The refusal has to be audible
        or the operator is left with a command that silently stops following the tag."""
        self._tree_has_poga()
        self.link.write_text("#!/bin/sh\n# a real install, not a symlink\n")

        self.assertFalse(self._repoint())
        self.assertFalse(self.link.is_symlink())
        self.assertIn("a real install", self.link.read_text())
        self.assertTrue(any("REFUSING to replace it" in s for s in self.said), self.said)
        self.assertFalse(self.tmp_link.exists() or self.tmp_link.is_symlink())

    def test_a_link_already_inside_the_tree_is_left_alone(self):
        """The steady state, and the reason this is safe to run on EVERY cutover instead of
        once: the second run has nothing to do and says nothing."""
        target = self._tree_has_poga()
        self.link.symlink_to(target)

        self.assertFalse(self._repoint())
        self.assertEqual(self.link.resolve(), target.resolve())
        self.assertEqual(self.said, [])

    def test_a_tree_with_no_poga_leaves_the_link_alone(self):
        """A tag that ships no CLI must not take the operator's command away. Repointing at
        a path that does not exist would leave a dangling link and a command that fails
        with 'No such file or directory' — worse than the stale one it replaced."""
        self.link.symlink_to(self.old_clone / "poga")

        self.assertFalse(self._repoint())
        self.assertEqual(self.link.resolve(), (self.old_clone / "poga").resolve())
        self.assertTrue(any("not present in the deploy tree" in s for s in self.said),
                        self.said)

    def test_a_dry_run_changes_nothing(self):
        self._tree_has_poga()
        self.link.symlink_to(self.old_clone / "poga")

        self.assertTrue(self._repoint(dry_run=True))     # it reports what it WOULD do
        self.assertEqual(self.link.resolve(), (self.old_clone / "poga").resolve())
        self.assertFalse(self.tmp_link.exists() or self.tmp_link.is_symlink())
        self.assertTrue(any("repointing" in s for s in self.said), self.said)

    def test_a_stale_repoint_temp_from_an_earlier_run_does_not_block_the_next(self):
        """Whatever leaves one behind, the next cutover has to get through it. A temp that
        blocked every subsequent repoint would turn a transient failure into a permanent
        one."""
        target = self._tree_has_poga()
        self.link.symlink_to(self.old_clone / "poga")
        self.tmp_link.symlink_to(self.root / "something-else")

        self.assertTrue(self._repoint())
        self.assertEqual(self.link.resolve(), target.resolve())
        self.assertFalse(self.tmp_link.exists() or self.tmp_link.is_symlink())

    def test_a_failed_replace_leaves_no_repoint_temp_behind(self):
        """THE FAILURE PATH. `os.replace` is what makes this atomic, and if it raises the
        temp symlink has already been created — so the cleanup has to be in the `except`,
        not only at the top of the next call. A leftover `poga.repoint` sits in the
        directory that is on everybody's PATH.

        Narrow, because both paths are in one directory so a cross-device failure is
        impossible; not nothing, because the next call is the only thing that clears it and
        the log line the operator sees says the repoint failed, not that it left a file."""
        self._tree_has_poga()
        self.link.symlink_to(self.old_clone / "poga")

        with patch.object(runner.os, "replace", side_effect=OSError("no space left")):
            self.assertFalse(self._repoint())

        self.assertEqual(self.link.resolve(), (self.old_clone / "poga").resolve(),
                         "a failed repoint must leave the working command in place")
        self.assertFalse(
            self.tmp_link.exists() or self.tmp_link.is_symlink(),
            f"{self.tmp_link.name} survived a failed repoint — the `except OSError` logs "
            f"and returns without unlinking the temp it created, so a stray symlink is "
            f"left in the directory on the operator's PATH")

    def test_the_cutover_repoints_for_the_self_row_and_only_for_it(self):
        """Repointing `poga` while deploying another member would be this program editing a tool
        it does not own, using a path from somebody else's tree. The flag is the whole
        authorisation, so both directions are asserted."""
        cut = {"pending": [], "unloaded": []}
        with patch.object(runner, "repoint_cli_link") as repoint:
            runner.perform_cutover("federation", {"self": True, "subdir": None}, {},
                                   cut, "v1.0.0", self.log, dry_run=True)
        self.assertEqual(repoint.call_count, 1)

        with patch.object(runner, "repoint_cli_link") as repoint:
            runner.perform_cutover("example-app", {"subdir": None}, {},
                                   cut, "v1.0.0", self.log, dry_run=True)
        self.assertEqual(repoint.call_count, 0)


# -- W5: the machine's own command --------------------------------------------------

class NoCommandOnThisMachinePointsAtAWorkingCloneTest(unittest.TestCase):
    """W5's symlink gate, SCOPED THE WAY `deploy/install-*.sh` SCOPES ITS OWN.

    An unconditional assertion would be wrong here and would be deleted within a day:
    `~/.local/bin/poga` legitimately points into the trunk clone on a
    development machine, which has no deploy tree and no reason for one. The condition that makes
    the rule apply is the same one the install scripts use — does a federation deploy tree
    EXIST on this machine — because that is what says the sealed model is in force here.

    Everything below is READ-ONLY on real paths. The skip is `skipTest` rather than an
    early `return` so that a machine where the gate did not run says so out loud: a silent
    pass and a real check render identically otherwise, and this file has already been
    burned once by a guard whose subject had moved.
    """

    def _real_deploy_root(self) -> Path:
        # Honoured the way install-*.sh honours it, so the check follows the runner's own
        # notion of where deploy trees live rather than a second copy of the convention.
        env = os.environ.get("POGA_DEPLOY_ROOT")
        return Path(env) if env else Path.home() / "deploy"

    def test_the_poga_command_resolves_inside_the_deploy_tree_once_one_exists(self):
        tree = self._real_deploy_root() / "federation"
        if not tree.is_dir():
            self.skipTest(
                f"no federation deploy tree at {tree}, so the sealed model is not in force "
                f"on this machine and `poga` pointing into a working clone is correct. "
                f"THIS CHECK DID NOT RUN.")
        link = Path.home() / ".local" / "bin" / "poga"
        if not link.exists() and not link.is_symlink():
            self.skipTest(f"no {link} installed on this machine. THIS CHECK DID NOT RUN.")

        resolved = link.resolve()
        self.assertTrue(
            resolved == tree.resolve() or str(resolved).startswith(str(tree.resolve()) + "/"),
            f"{link} resolves to {resolved}, outside the sealed tree {tree}. This machine "
            f"has adopted the sealed model, so the command every resident Architect "
            f"starts a session with is running code from a checkout somebody develops in — "
            f"and it stops working entirely the moment that checkout is retired.")


class SealedRootCaseRedirectsEveryProductionPathTest(unittest.TestCase):
    """THE SAME QUESTION, ABOUT A DIFFERENT FIXTURE — which is why this is not a duplicate.

    `test_deploy_runner.TestEveryProductionPathIsRedirected` proves `DeployRunnerCase` is
    safe. Almost every test in THIS file runs on `SealedRootCase`, which is a separate
    fixture with its own `_env` dict, and the base guard never instantiates it. Add a new
    production path, redirect it in one fixture, and the other guard still reports a clean
    pass while every test here reaches the real path — the precise shape of the failure
    both guards exist to prevent, one directory over.

    THE POLICY HAS ONE OWNER AND IT IS NOT THIS CLASS (P16). The source list and the
    read-only exemptions are READ FROM the base guard rather than restated, so there is
    exactly one place to argue that a name cannot write, and this class contributes only
    the subject: `SealedRootCase`. If the two lists were copied, they would disagree on the
    first name added and the disagreement would be invisible until it mattered.
    """

    BASE = base.TestEveryProductionPathIsRedirected

    def test_every_poga_env_name_the_runner_reads_is_redirected_by_sealed_root_case(self):
        source = "\n".join((REPO / f).read_text(encoding="utf-8") for f in self.BASE.SOURCES)
        names = set(re.findall(r"POGA_[A-Z_]+", source))
        # Report what was EXAMINED, not only the verdict: a guard that cannot tell "looked
        # and found nothing" from "never looked" is how the base guard's own prefix filter
        # passed for months over a set that excluded `POGA_CLI_LINK`.
        self.assertTrue(names, "found no POGA_* names — the extractor stopped looking, "
                               "which reads as a clean result and is not one")
        for dangerous in ("POGA_CLI_LINK", "POGA_CHANNEL_ROOT"):
            self.assertIn(dangerous, names,
                          f"{dangerous} is no longer among the names examined here")

        case = SealedRootCase("run")
        case.setUp()
        try:
            # A deliberate removal counts, the same as in the base guard: for a name that
            # selects a whole service rather than a directory, absence IS the redirect.
            fixture = set(case._env) | set(case._cleared)
            for name in case._cleared:
                self.assertNotIn(name, os.environ,
                                 f"{name} is counted as neutralised but survived setUp")
        finally:
            case.doCleanups()

        for name in sorted(names - set(self.BASE.READ_ONLY)):
            self.assertIn(
                name, fixture,
                f"{name} is a path the runner resolves at call time and SealedRootCase "
                f"does not redirect it. Left as is, every test in this file reaches the "
                f"real one through it — and the guard in test_deploy_runner.py will not "
                f"say so, because it examines a different fixture. Redirect it here, or "
                f"argue it into that file's READ_ONLY, which owns the exemptions.")


if __name__ == "__main__":
    unittest.main()
