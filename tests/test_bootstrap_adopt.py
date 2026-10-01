"""bootstrap.py --adopt — the third onboarding path (ADR-0067).

Fresh assumes an empty/absent repo; retrofit (ADR-0014) assumes an evolved role doc to
reconcile. A system that was BUILT and works but never had an Architect is neither —
a hypothetical `example-app`, already running with ADRs of its own and no role doc, is
the shape that needs this path.

The load-bearing property: adopt installs the operating substrate and touches nothing
else. An adopted repo's own files are its house (ADR-0023 — same pipes, different
houses), and one of them is usually protecting something irreplaceable: a built
system's `.gitignore` may be the only thing keeping `data/` — unrecoverable production
data — out of git. A bootstrap that replaced it would be a data-loss bug, not a style regression.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)

GIT = shutil.which("git")


class MergeGitignoreTest(unittest.TestCase):
    """The federation's data exclusions must arrive WITHOUT displacing the target's."""

    KIT = "inputs/\nusers/\nproposed-edits/\narchitect-learnings.md\n.session-state/\n"

    def test_existing_rules_survive(self):
        existing = "node_modules/\ndata/\nlogs/\n.DS_Store\n"
        merged = bootstrap.merge_gitignore(existing, self.KIT)
        for rule in ("node_modules/", "data/", "logs/", ".DS_Store"):
            self.assertIn(rule, merged, f"{rule} must survive the merge")

    def test_federation_exclusions_are_added(self):
        merged = bootstrap.merge_gitignore("data/\n", self.KIT)
        for rule in ("users/", "proposed-edits/", "architect-learnings.md", ".session-state/"):
            self.assertIn(rule, merged, f"{rule} must be added — P3 boundary")

    def test_data_dir_is_never_dropped(self):
        """The regression that would cost irreplaceable data."""
        merged = bootstrap.merge_gitignore("data/\n", self.KIT)
        self.assertIn("data/", merged)

    def test_merge_is_idempotent(self):
        once = bootstrap.merge_gitignore("data/\n", self.KIT)
        twice = bootstrap.merge_gitignore(once, self.KIT)
        self.assertEqual(once, twice, "re-adopting must not append the block again")

    def test_already_covered_target_is_unchanged(self):
        existing = self.KIT
        self.assertEqual(bootstrap.merge_gitignore(existing, self.KIT), existing)

    def test_comments_do_not_count_as_coverage(self):
        """A commented-out rule is not a rule."""
        merged = bootstrap.merge_gitignore("# users/\ndata/\n", self.KIT)
        self.assertIn("\nusers/", merged)


class AdoptPreflightTest(unittest.TestCase):
    """Adopt must refuse every shape that is not 'built system, no Architect'."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.target = pathlib.Path(self._tmp.name) / "sys"
        self.target.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")
        (self.target / "server.js").write_text("// app\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "the system, already built")

        # A throwaway user profile under the federation root. `users/` is gitignored
        # data and therefore absent from a poga worktree lane entirely, so the tests
        # must not depend on the real operator profile being present.
        # PER-PROCESS id (WI-0054). `users/` is gitignored data, so in a poga lane it is a
        # SYMLINK to the main checkout — every lane's suite writes into ONE shared
        # directory. With a fixed name, two lanes running the suite at once each create
        # and then rmtree the same path, and whichever loses has its profile deleted
        # between setUp and the subprocess call. bootstrap.py then dies at "user profile
        # not found" before printing anything, so the assertion fails on output that was
        # never produced — fails under `discover`, passes on an identical re-run, passes
        # in isolation. That is the whole flake, and it randomly blocked fleet pushes
        # because `push-substrate` refuses to run unless the suite is green.
        self.user_id = f"_test_adopt_user_{os.getpid()}"
        self.profile_dir = ROOT / "users" / self.user_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # addCleanup, not tearDown: it still runs if setUp raises after this point, so a
        # crashed run cannot leave a stray profile in shared state.
        self.addCleanup(shutil.rmtree, self.profile_dir, ignore_errors=True)
        (self.profile_dir / "profile.md").write_text("# test profile\n", encoding="utf-8")

        self.spec_path = pathlib.Path(self._tmp.name) / "spec.json"
        self.spec_path.write_text(json.dumps({
            "system_name": "Test Sys", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main",
            "mission_prose": "Keep the thing running.",
        }), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()          # the profile is handled by addCleanup in setUp

    def _git(self, *args):
        return subprocess.run([GIT, "-C", str(self.target), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--spec", str(self.spec_path),
             "--adopt", str(self.target), *extra],
            capture_output=True, text=True, cwd=str(ROOT),
        )

    def test_dry_run_writes_nothing(self):
        before = sorted(p.name for p in self.target.iterdir())
        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("adopt-in-place", proc.stdout)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), before)

    def test_refuses_a_repo_that_already_has_substrate(self):
        """A repo carrying CANON.md/session.py has an Architect — that is a RETROFIT.
        Adopting it would write a template role doc over evolved practice."""
        (self.target / "session.py").write_text("# harness\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "substrate")
        proc = self._run("--dry-run")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("RETROFIT", proc.stderr)

    def test_non_git_directory_is_auto_inited_not_refused(self):
        """ADR-0067 update note (2026-07-24, the operator's ruling: adopt ends in a working
        session with working git, and does not stop to hand that step to the operator) — a plain
        directory with real content and no git history is what adopt is FOR now, not
        a refusal case. `--dry-run` must still touch nothing, not even `git init`."""
        plain = pathlib.Path(self._tmp.name) / "plain"
        plain.mkdir()
        (plain / "app.py").write_text("# the system\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--spec", str(self.spec_path),
             "--adopt", str(plain), "--dry-run"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no git history", proc.stdout)
        self.assertFalse((plain / ".git").exists(),
                         "--dry-run must not run git init either")

    def test_refuses_a_dirty_tree(self):
        """In-flight work must not be swept into the adoption commit."""
        (self.target / "wip.txt").write_text("half done\n", encoding="utf-8")
        proc = self._run("--dry-run")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("uncommitted", proc.stderr)

    def test_refuses_a_missing_target(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--spec", str(self.spec_path),
             "--adopt", str(self.target / "nope"), "--dry-run"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)

    def test_adopt_spec_does_not_require_clone_location_keys(self):
        """friendly_folder / projects_dir are fresh-only — the adopt target answers them."""
        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("missing required key", proc.stderr)

    def test_no_remote_is_reported_and_left_alone_without_the_flag(self):
        """Creating a GitHub repo is outward-facing — never a side effect."""
        proc = self._run("--dry-run")
        self.assertIn("LEFT AS-IS", proc.stdout)

    def test_plan_alias_for_dry_run(self):
        """`--plan` (poga's spelling) must behave identically to `--dry-run` on this
        legacy surface too — the flag-name reconciliation half of the 2026-07-24
        consultant parity brief (`--plan` is `poga restore`'s spelling and the
        brief's named survivor)."""
        before = sorted(p.name for p in self.target.iterdir())
        proc = self._run("--plan")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("adopt-in-place", proc.stdout)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), before)


class AdoptDefaultsTest(unittest.TestCase):
    """The 2026-07-24 UX request (operator): `poga bootstrap --adopt` with no PATH and no
    `--spec` should default to 'wherever the user is standing' and 'the spec sitting
    right there' — not require both to be spelled out by hand every time."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = pathlib.Path(self._tmp.name) / "sys"
        self.target.mkdir()
        (self.target / "app.py").write_text("# the system\n", encoding="utf-8")

        self.user_id = f"_test_adopt_defaults_user_{os.getpid()}"   # WI-0054, as above
        self.profile_dir = ROOT / "users" / self.user_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.profile_dir, ignore_errors=True)
        (self.profile_dir / "profile.md").write_text("# test profile\n", encoding="utf-8")

    def _write_spec_in_target(self):
        (self.target / "bootstrap-spec.json").write_text(json.dumps({
            "system_name": "Test Sys", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main",
            "mission_prose": "Keep the thing running.",
        }), encoding="utf-8")

    def test_bare_adopt_defaults_to_cwd(self):
        """No PATH after --adopt: direct invocation, so plain cwd already IS where
        the user is standing — no wrapper `cd` to correct for here.

        Must run with POGA_INVOKED_FROM cleared from the child's environment: that
        var is exactly what `resolve_from_invocation` prefers over cwd when set (by
        design, for the real `poga` wrapper flow) — and `subprocess.run` inherits
        the full parent environment by default, so running this suite from INSIDE a
        poga-launched lane (the var is legitimately set for the whole session) would
        leak it into the child and silently test the wrapped case instead of the
        bare one this test is named for."""
        self._write_spec_in_target()
        env = {k: v for k, v in os.environ.items() if k != "POGA_INVOKED_FROM"}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--adopt", "--dry-run"],
            capture_output=True, text=True, cwd=str(self.target), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(self.target.resolve()), proc.stdout)

    def test_spec_defaults_to_bootstrap_spec_json_in_target(self):
        self._write_spec_in_target()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--adopt", str(self.target),
             "--dry-run"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # What this test is actually about: the spec was FOUND inside the target with
        # no `--spec`. It used to assert the banner said "adopt-in-place", which stopped
        # being true when the banner started naming the job instead of the code path —
        # this fixture's target has loose files and no git history, so it is a fresh
        # build in place, not an adopt (session ~157).
        # `str(self.target)`, not `.resolve()` — an explicit PATH argument is echoed as
        # typed, and on macOS /var is a symlink to /private/var, so resolving here
        # compares against a path the run never printed.
        self.assertIn(str(self.target), proc.stdout)
        self.assertIn("bootstrap", proc.stdout)

    def test_missing_spec_notifies_clearly_instead_of_guessing(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--adopt", str(self.target),
             "--dry-run"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no --spec given and no bootstrap-spec.json found", proc.stderr)

    def test_explicit_spec_overrides_the_target_default(self):
        """An explicit --spec wins even when the target ALSO carries its own
        bootstrap-spec.json — no ambiguity about which one is used."""
        (self.target / "bootstrap-spec.json").write_text(json.dumps({
            "system_name": "Wrong One", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main", "mission_prose": "x",
        }), encoding="utf-8")
        explicit = pathlib.Path(self._tmp.name) / "explicit-spec.json"
        explicit.write_text(json.dumps({
            "system_name": "Right One", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main", "mission_prose": "x",
        }), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bootstrap.py"), "--adopt", str(self.target),
             "--spec", str(explicit), "--dry-run"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Right One", proc.stdout)
        self.assertNotIn("Wrong One", proc.stdout)


class InitAndCommitExistingContentTest(unittest.TestCase):
    """`init_and_commit_existing_content()` directly — the actual mutation behind
    the auto-init report line, exercised in isolation so this doesn't have to run
    the whole `adopt_in_place` pipeline (which would touch the real federation
    `portfolio.md` and call `gh`) just to prove `git init` + commit works."""

    # The committer identity AND the signing default. Signing has to be pinned through
    # the environment here, unlike every other fixture in this file: the repo does not
    # exist until `init_and_commit_existing_content` creates it, so there is nothing to
    # `git config` beforehand. `GIT_CONFIG_*` outranks the runner's global config, which
    # is the point — a developer with `commit.gpgsign = true` would otherwise have this
    # test shell out to gpg and hang or fail (WI-0250). Production is deliberately NOT
    # changed to suppress signing: a real adopt commit SHOULD be signed if the user
    # configured that. It is the fixture that must say it does not sign.
    GIT_IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "commit.gpgsign",
                    "GIT_CONFIG_VALUE_0": "false"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_content_is_committed_as_the_root_commit(self):
        target = pathlib.Path(self._tmp.name) / "sys"
        target.mkdir()
        (target / "app.py").write_text("# the system\n", encoding="utf-8")
        with mock.patch.dict(os.environ, self.GIT_IDENTITY):
            committed = bootstrap.init_and_commit_existing_content(target)
        self.assertTrue(committed)
        self.assertTrue((target / ".git").is_dir())
        status = subprocess.run([GIT, "-C", str(target), "status", "--porcelain"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "", "the new commit must leave the tree clean")
        log = subprocess.run([GIT, "-C", str(target), "log", "--oneline"],
                             capture_output=True, text=True, check=True)
        self.assertEqual(len(log.stdout.strip().splitlines()), 1,
                         "exactly one root commit, not the target's whole future history")

    def test_empty_directory_is_inited_but_nothing_to_commit(self):
        target = pathlib.Path(self._tmp.name) / "empty"
        target.mkdir()
        with mock.patch.dict(os.environ, self.GIT_IDENTITY):
            committed = bootstrap.init_and_commit_existing_content(target)
        self.assertFalse(committed)
        self.assertTrue((target / ".git").is_dir(), "still a real repo, just no history yet")


class AdoptPlanTest(unittest.TestCase):
    """The copy plan is substrate-only, by construction."""

    def test_plan_omits_the_systems_own_surfaces(self):
        dests = {d for _, d, _ in bootstrap.ADOPT_COPY_PLAN}
        for owned in ("README.md", "STATUS.md", ".gitignore",
                      "adr/README.md", "adr/template.md"):
            self.assertNotIn(owned, dests,
                             f"{owned} belongs to the adopted system, not the substrate")

    def test_plan_installs_the_substrate(self):
        dests = {d for _, d, _ in bootstrap.ADOPT_COPY_PLAN}
        for required in ("CLAUDE.md", "CANON.md", "STANDARD.md",
                         "session.config.json", ".claude/settings.json"):
            self.assertIn(required, dests)

    def test_plan_is_a_subset_of_the_fresh_plan(self):
        """Adopt must never install something fresh doesn't — one kit, one shape."""
        fresh = {(k, d) for k, d, _ in bootstrap.COPY_PLAN}
        adopt = {(k, d) for k, d, _ in bootstrap.ADOPT_COPY_PLAN}
        self.assertTrue(adopt <= fresh, adopt - fresh)


class TargetOwnContentTest(unittest.TestCase):
    """Plan selection reads the target, not the verb (session ~118, WI-0080).

    `poga new` on an empty folder finishes with `bootstrap --adopt`, so the adopt
    plan was being applied to a system owning nothing — every "it already has its
    own" omission firing for a file that did not exist. A member shipped with no
    STATUS.md that way, which is the ADR-0021 surface reconcile.py reads.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = pathlib.Path(self._tmp.name)

    @staticmethod
    def _make_repo(d: pathlib.Path) -> None:
        """A directory with real git history — what ADOPT_COPY_PLAN's every omission
        defers to. `select_copy_plan` keys on history, not on loose files, so a test
        for the adopt plan has to build an actual repo (session ~157)."""
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run([GIT, "-C", str(d), "init", "-q", "-b", "main"], check=True)
        subprocess.run([GIT, "-C", str(d), "config", "user.email", "t@example.com"], check=True)
        subprocess.run([GIT, "-C", str(d), "config", "user.name", "T"], check=True)
        subprocess.run([GIT, "-C", str(d), "config", "commit.gpgsign", "false"], check=True)
        (d / "app.py").write_text("# the system\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(d), "add", "-A"], check=True)
        subprocess.run([GIT, "-C", str(d), "commit", "-q", "-m", "root"], check=True)

    def test_bare_directory_owns_nothing(self):
        self.assertEqual(bootstrap.target_own_content(self.d), [])

    def test_git_dir_and_the_intake_spec_do_not_count_as_content(self):
        """The intake writes bootstrap-spec.json into the folder immediately before
        calling the installer, and `git init` may already have run. Counting either
        as the system's 'own' content would push a fresh folder back onto the adopt
        plan — reintroducing the exact bug, invisibly."""
        (self.d / ".git").mkdir()
        (self.d / "bootstrap-spec.json").write_text("{}", encoding="utf-8")
        (self.d / ".DS_Store").write_text("", encoding="utf-8")
        self.assertEqual(bootstrap.target_own_content(self.d), [])

    def test_real_content_is_reported(self):
        (self.d / "app.py").write_text("# the system\n", encoding="utf-8")
        (self.d / "STATUS.md").write_text("# status\n", encoding="utf-8")
        self.assertEqual(bootstrap.target_own_content(self.d), ["STATUS.md", "app.py"])

    def test_empty_target_selects_the_fresh_plan_including_status_and_readme(self):
        """The regression this exists to catch: an empty target must get STATUS.md
        and README.md, which ADOPT_COPY_PLAN deliberately omits. Calls the real
        selector — an earlier cut of this test re-derived the choice from the same
        inputs and would have passed with the bug still in place."""
        dests = {d for _, d, _ in bootstrap.select_copy_plan(self.d)}
        self.assertIn("STATUS.md", dests)
        self.assertIn("README.md", dests)
        self.assertIn("adr/README.md", dests)

    def test_existing_repo_keeps_the_adopt_plan(self):
        """The adopt case must not regress: a system that owns its surfaces keeps
        them, and never has our STATUS.md/README.md/adr layout imposed on it."""
        repo = self.d / "existing"
        self._make_repo(repo)
        dests = {d for _, d, _ in bootstrap.select_copy_plan(repo)}
        for owned in ("README.md", "STATUS.md", "adr/README.md", "adr/template.md"):
            self.assertNotIn(owned, dests)

    def test_loose_files_without_history_are_a_fresh_build_not_an_adopt(self):
        """A spec-only folder (session ~157: there was nothing to adopt). The folder
        held a build brief and a bootstrap spec — the INPUTS the system was specified
        from, written before any substrate existed. Keying on "does this directory
        contain anything" classified that as an adopted system and silently stripped
        STATUS.md, README.md and the whole adr/ home out of the install."""
        d = self.d / "spec-only-member"
        d.mkdir()
        (d / "2026-08-18-app-brief.md").write_text("# brief\n", encoding="utf-8")
        (d / "bootstrap-spec.json").write_text("{}", encoding="utf-8")
        self.assertFalse(bootstrap.has_git_history(d))
        dests = {dst for _, dst, _ in bootstrap.select_copy_plan(d)}
        for required in ("STATUS.md", "README.md", "adr/README.md", "adr/template.md"):
            self.assertIn(required, dests)

    def test_an_uncommitted_git_init_is_still_not_history(self):
        """`git init` with no commit yet leaves `.git` present and HEAD unborn. Testing
        for the directory rather than for a resolvable HEAD would read that as an
        established system and take the adopt branch — the check failing toward the
        reassuring answer, which is the direction that hides the bug."""
        d = self.d / "initialised"
        d.mkdir()
        subprocess.run([GIT, "-C", str(d), "init", "-q", "-b", "main"], check=True)
        (d / "brief.md").write_text("# brief\n", encoding="utf-8")
        self.assertFalse(bootstrap.has_git_history(d))
        self.assertIn("STATUS.md", {dst for _, dst, _ in bootstrap.select_copy_plan(d)})

    def test_substrate_lands_under_either_plan(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                d = self.d / ("full" if populated else "bare")
                if populated:
                    self._make_repo(d)
                else:
                    d.mkdir()
                dests = {dst for _, dst, _ in bootstrap.select_copy_plan(d)}
                for required in ("CLAUDE.md", "CANON.md", "STANDARD.md",
                                 "session.config.json", ".claude/settings.json"):
                    self.assertIn(required, dests)

    def test_gitignore_is_never_in_the_selected_plan(self):
        """.gitignore has one writer on the adopt path — the merge block — because
        the target's own rules are load-bearing (a built system's `data/`). Letting the
        fresh plan's wholesale copy through would be a data-loss bug."""
        for populated in (False, True):
            with self.subTest(populated=populated):
                d = self.d / ("gi_full" if populated else "gi_bare")
                if populated:
                    self._make_repo(d)
                else:
                    d.mkdir()
                self.assertNotIn(".gitignore",
                                 {dst for _, dst, _ in bootstrap.select_copy_plan(d)})


class PortfolioRegistrationRequestTest(unittest.TestCase):
    """The installer asks to be registered; it never writes the roster (P13).

    Until session ~118 it edited `portfolio.md` in the federation checkout and left
    the edit staged. Staging is not single-writer: the write had already happened
    from inside another system's onboarding session, it left the federation tree
    dirty, and a worktree lane could not commit it by any route (WI-0056).
    """

    SPEC = {"system_name": "Widget Press", "user_id": "operator",
            "orchestrator_note": "(none — CLI runtime)"}
    IDS = {"system_id": "widget-press", "architect_id": "widget-press-arch",
           "architect_name": "Widget Press Architect"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_root = pathlib.Path(self._tmp.name)
        # A portfolio.md that must come back byte-identical.
        self.portfolio = self.fake_root / "portfolio.md"
        self.portfolio.write_text(
            "Last updated: 2020-01-01\n\n| a | b | c | d | e | f |\n\n## Notes\n",
            encoding="utf-8")
        shutil.copyfile(ROOT / "session.config.json", self.fake_root / "session.config.json")

    def _run(self):
        with mock.patch.object(bootstrap, "ROOT", self.fake_root), \
             mock.patch.object(bootstrap, "FED_CONFIG", self.fake_root / "session.config.json"):
            return bootstrap.request_portfolio_registration(self.SPEC, self.IDS, None)

    def test_portfolio_is_not_touched(self):
        before = self.portfolio.read_text(encoding="utf-8")
        self._run()
        self.assertEqual(self.portfolio.read_text(encoding="utf-8"), before,
                         "the roster has ONE writer and this is not it")

    def test_brief_lands_in_the_federation_inbox(self):
        brief = self._run()
        self.assertTrue(brief.is_file())
        self.assertEqual(brief.parent,
                         self.fake_root / "proposed-edits" / "federation-arch" / "pending")

    def test_brief_carries_the_exact_row_to_apply(self):
        """Applying must be transcription, not re-derivation — otherwise the row that
        lands can differ from the one the installer computed."""
        text = self._run().read_text(encoding="utf-8")
        self.assertIn(bootstrap.portfolio_row(self.SPEC, self.IDS, None), text)
        self.assertIn("`widget-press-arch`", text)

    def test_brief_is_manual_and_declares_a_reason(self):
        """curate/check-apply.py refuses a manual brief with no reason, and the
        auto engine cannot take this one: apply-briefs gates on the target's
        `**Version:**` line and portfolio.md is a table with none."""
        text = self._run().read_text(encoding="utf-8")
        self.assertIn("apply: manual", text)
        self.assertIn("manual-reason: attended", text)
        self.assertIn("target-file: portfolio.md", text)

    def test_rerun_is_idempotent_on_the_same_day(self):
        first = self._run()
        second = self._run()
        self.assertEqual(first, second)
        self.assertEqual(len(list(first.parent.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
