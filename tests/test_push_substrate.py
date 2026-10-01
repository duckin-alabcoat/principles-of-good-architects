"""Tests for curate/push-substrate.py — the settings-clobber guard (ADR-0034).

The load-bearing property (2026-07-05 consultant Item 1): a converged repo that
carries the convergence markers (CANON.md + session.py) but has NO
session.config.json — the shape of a repo whose root another agent owns —
must NOT have its root .claude/settings.json rewritten with the bare federation
floor. Such a settings.json is that agent's own permission surface; overwriting it
with the Architect floor is an operational + SECURITY regression. Keep this fixture
whether or not any member has this shape today.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
# push-substrate.py imports `from common import ...` / `from gen_settings import ...`,
# both under curate/ — put it on the path before loading the hyphenated module.
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location(
    "push_substrate", ROOT / "curate" / "push-substrate.py"
)
push_substrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_substrate)

SETTINGS_PATH = push_substrate.SETTINGS_PATH  # ".claude/settings.json"

import common  # noqa: E402  (curate/ is on the path from above)

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
from coord_fixture import neutralize_live_store  # noqa: E402


def _settings_changes(repo):
    """The diff_files entries that would push a settings file (label == settings)."""
    return [c for c in push_substrate.diff_files(repo) if c[0] == SETTINGS_PATH]


class _RepoFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        # Convergence markers so this looks like a converged Architect repo.
        (self.repo / "CANON.md").write_text("# canon\n", encoding="utf-8")
        (self.repo / "session.py").write_text("# harness\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_settings(self, text, rel=SETTINGS_PATH):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _write_config(self, cfg):
        (self.repo / "session.config.json").write_text(
            json.dumps(cfg), encoding="utf-8"
        )


class ResidentPersonaShapeTest(_RepoFixture):
    """The exact hazard: markers present, NO config, differing root settings."""

    def test_unconfigured_repo_is_gated_out_of_settings(self):
        self._write_settings('{"permissions": {"allow": ["Bash(echo persona)"]}}')
        self.assertIsNone(push_substrate.settings_target(self.repo))

    def test_unconfigured_repo_settings_is_never_in_diff(self):
        # A settings.json that differs from the floor MUST NOT be flagged for push.
        self._write_settings('{"permissions": {"allow": ["Bash(echo persona)"]}}')
        self.assertEqual(_settings_changes(self.repo), [])

    def test_unreadable_config_is_gated_out(self):
        (self.repo / "session.config.json").write_text("{not json", encoding="utf-8")
        self._write_settings('{"different": true}')
        self.assertIsNone(push_substrate.settings_target(self.repo))
        self.assertEqual(_settings_changes(self.repo), [])


class ConfiguredRepoTest(_RepoFixture):
    """A normal converged Architect repo: config present, root settings."""

    def test_target_defaults_to_root(self):
        self._write_config({"settings_extras": {"allow": ["Bash(npm test:*)"]}})
        self.assertEqual(push_substrate.settings_target(self.repo), SETTINGS_PATH)

    def test_differing_root_settings_is_flagged(self):
        self._write_config({"settings_extras": {"allow": ["Bash(npm test:*)"]}})
        self._write_settings('{"stale": true}')
        changes = _settings_changes(self.repo)
        self.assertEqual(len(changes), 1)
        label, rel, want = changes[0]
        self.assertEqual(rel, SETTINGS_PATH)
        # `want` is the freshly generated floor+extras, not the stale content.
        self.assertIn(b"Bash(npm test:*)", want)

    def test_up_to_date_settings_not_flagged(self):
        cfg = {"settings_extras": {"allow": ["Bash(npm test:*)"]}}
        self._write_config(cfg)
        # Write exactly what the generator would produce.
        self._write_settings(push_substrate.render_settings(cfg), rel=SETTINGS_PATH)
        self.assertEqual(_settings_changes(self.repo), [])

    def test_absent_settings_is_refresh_only_not_introduced(self):
        # Config present but no settings file — refresh-only means don't create it.
        self._write_config({"settings_extras": {"allow": ["Bash(npm test:*)"]}})
        self.assertEqual(_settings_changes(self.repo), [])


class SettingsPathKeyTest(_RepoFixture):
    """The resident-runtime `settings_path` declaration + explicit opt-out."""

    def test_explicit_opt_out_values_skip_settings(self):
        for val in (False, None, "", "none", "off"):
            with self.subTest(val=val):
                self._write_config({"settings_path": val,
                                    "settings_extras": {"allow": ["Bash(x)"]}})
                self._write_settings('{"stale": true}')
                self.assertIsNone(push_substrate.settings_target(self.repo))
                self.assertEqual(_settings_changes(self.repo), [])

    def test_redirect_targets_declared_off_root_path(self):
        rel = "architect/home/.claude/settings.json"
        cfg = {"settings_path": rel,
               "settings_extras": {"allow": ["Bash(npm test:*)"]}}
        self._write_config(cfg)
        self.assertEqual(push_substrate.settings_target(self.repo), rel)
        # A differing file AT the declared path is flagged; the root is untouched.
        self._write_settings('{"stale": true}', rel=rel)
        self._write_settings('{"runtime persona": true}', rel=SETTINGS_PATH)
        changes = _settings_changes(self.repo)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][1], rel)


class RuntimeGateTest(_RepoFixture):
    """ADR-0041 §5: the fleet push services a repo that declares
    claude-code (alone, or among co-equal runtimes) and skips a pure-non-Claude one,
    even though both carry the convergence markers (governance substrate)."""

    def test_runtimes_default_to_claude(self):
        # No config, and config without the field, both read as [claude-code].
        self.assertEqual(push_substrate.repo_runtimes(self.repo), ["claude-code"])
        self._write_config({"settings_extras": {"allow": ["Bash(x)"]}})
        self.assertEqual(push_substrate.repo_runtimes(self.repo), ["claude-code"])

    def test_single_runtime_string_reads_as_one_element_list(self):
        # Legacy single "runtime" string back-compat.
        self._write_config({"runtime": "gemini-antigravity"})
        self.assertEqual(push_substrate.repo_runtimes(self.repo), ["gemini-antigravity"])

    def test_runtimes_array_is_read(self):
        self._write_config({"runtimes": ["claude-code", "gemini-antigravity"]})
        self.assertEqual(push_substrate.repo_runtimes(self.repo),
                         ["claude-code", "gemini-antigravity"])

    def test_pure_non_claude_single_is_skipped(self):
        self._write_config({"runtime": "gemini-antigravity"})
        lines = push_substrate.push_to_repo(self.repo, dry_run=True)
        self.assertEqual(len(lines), 1)
        self.assertIn("SKIPPED", lines[0])
        self.assertIn("no Claude-Code runtime", lines[0])

    def test_pure_non_claude_list_is_skipped(self):
        self._write_config({"runtimes": ["gemini-antigravity"]})
        lines = push_substrate.push_to_repo(self.repo, dry_run=True)
        self.assertIn("SKIPPED", lines[0])
        self.assertIn("no Claude-Code runtime", lines[0])

    def test_multi_runtime_including_claude_is_serviced(self):
        # Declaring claude-code AMONG co-equal runtimes => serviced, not skipped:
        # the push proceeds past the runtime gate.
        self._write_config({"runtimes": ["claude-code", "gemini-antigravity"]})
        lines = push_substrate.push_to_repo(self.repo, dry_run=True)
        self.assertFalse(any("no Claude-Code runtime" in l for l in lines))

    def test_non_claude_skip_writes_nothing_even_with_stale_substrate(self):
        # Stale governance substrate present; the skip must not touch it.
        self._write_config({"runtime": "gemini-antigravity"})
        (self.repo / "CANON.md").write_text("# stale canon\n", encoding="utf-8")
        lines = push_substrate.push_to_repo(self.repo, dry_run=False)
        self.assertTrue(lines[0].strip().endswith("(ADR-0041 §5)"))
        # Not overwritten with the federation copy.
        self.assertEqual((self.repo / "CANON.md").read_text(encoding="utf-8"), "# stale canon\n")


class SourceIntegrityGate(unittest.TestCase):
    """`source_committed` — refuse to ship substrate the trunk does not carry.

    Session 90, after the session-89 near-miss: a `poga` lane advanced
    refs/heads/main by CAS with no checkout, leaving the main tree 5 commits
    behind for hours while the scheduled adopt-runner fired from it. A push from
    that tree would have shipped the pre-C4/C5 session.py fleet-wide as
    byte-identical substrate — and passed the test gate, because the OLD harness
    is green against the OLD tests. Green tests prove the harness works; only
    this gate proves it is the harness the trunk blessed.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        # WI-0295: the real push path resolves the MAIN checkout through
        # `_shared_work_root()`, i.e. the operator's own repo. Measured: 161 reads.
        neutralize_live_store(self, self.root)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")
        for name in push_substrate.BYTE_IDENTICAL:
            # BYTE_IDENTICAL carries nested paths (`sessionlib/*.py`), not just
            # root-level files.
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(f"# {name} v1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "v1")
        self._orig_root = push_substrate.FED_ROOT
        push_substrate.FED_ROOT = self.root

    def tearDown(self):
        push_substrate.FED_ROOT = self._orig_root
        self._tmp.cleanup()

    def _git(self, *args):
        import subprocess
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_clean_tree_matching_trunk_passes(self):
        ok, detail = push_substrate.source_committed()
        self.assertTrue(ok, detail)
        self.assertIn("matches refs/heads/main", detail)

    def test_stale_checkout_after_lane_land_is_refused(self):
        # Exactly the ADR-0060 lane-land signature: refs/heads/main advances by
        # update-ref with no checkout, so the working tree keeps the old bytes.
        self._git("checkout", "-b", "lane")
        (self.root / "session.py").write_text("# session.py v2\n", encoding="utf-8")
        self._git("commit", "-am", "v2")
        tip = self._git("rev-parse", "HEAD")
        self._git("checkout", "main")
        self._git("update-ref", "refs/heads/main", tip)
        # The tree is now BEHIND the ref — the exact near-miss condition.
        self.assertEqual(
            (self.root / "session.py").read_text(encoding="utf-8"), "# session.py v1\n"
        )
        ok, detail = push_substrate.source_committed()
        self.assertFalse(ok, "a stale checkout must not ship substrate fleet-wide")
        self.assertIn("session.py", detail)
        # WI-0158 / ADR-0099 D4. This used to pin `reset --hard`, which is precisely the
        # defect: the refusal named a remedy check-bash denies (P9) and a lane reader's
        # isolation guard denies again (`git -C <other tree>`). The gate's job is
        # unchanged — refuse the push — but the remedy it names now has to be one its
        # reader can actually run, so the pin moves to the verb. Asserting the OLD string
        # would have held the escalation in place with a green test on top of it.
        self.assertIn("main-restore", detail)
        self.assertNotIn("reset --hard", detail,
                         "the guard-denied remedy is back — see WI-0158")

    def test_uncommitted_local_edit_is_refused(self):
        # The tree AHEAD of the ref — a half-finished harness change would
        # otherwise reach every member before it is committed or reviewed.
        (self.root / "session.py").write_text("# session.py WIP\n", encoding="utf-8")
        ok, detail = push_substrate.source_committed()
        self.assertFalse(ok)
        self.assertIn("session.py", detail)

    def test_edit_to_a_non_substrate_file_does_not_block(self):
        # The gate is scoped to what actually ships; unrelated work in flight
        # must not block a rollout (it would make the gate get disabled).
        (self.root / "ROADMAP.md").write_text("# in flight\n", encoding="utf-8")
        ok, detail = push_substrate.source_committed()
        self.assertTrue(ok, detail)

    def test_unresolvable_trunk_fails_closed(self):
        # A push is a fleet-wide irreversible write: an UNDETERMINABLE comparison
        # must block, not proceed. A guard that fails open on git breaking is a
        # guard that isn't there on the day it matters.
        self._git("branch", "-m", "main", "elsewhere")
        ok, detail = push_substrate.source_committed()
        self.assertFalse(ok)
        self.assertIn("undeterminable provenance", detail)


class StaleCheckoutGuardTest(unittest.TestCase):
    """The 2026-08-07 settings clobber in one member (brief 2026-08-13). A lane land advances
    the target's main REF without touching the main checkout's index/worktree,
    so the pre-land tree sits in the index as staged reverts/deletes of the
    landed session's work. The push must (1) SKIP such a target by name, and
    (2) even were the guard bypassed, commit only its own pathspec — a bare
    `git commit` here shipped a stale tree that reverted 1,072 landed lines.

    Real git repos and a real `update-ref` land, because the defect lives in
    git's index semantics, not in our python.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.repo = self.base / "member"
        self.repo.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        self._git("config", "commit.gpgsign", "false")
        # Converged substrate, all current except CANON.md (the refresh payload).
        for name in push_substrate.BYTE_IDENTICAL:
            dst = self.repo / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((push_substrate.FED_ROOT / name).read_bytes())
        for name in push_substrate.EXECUTABLE:
            (self.repo / name).chmod(0o755)
        (self.repo / "CANON.md").write_text("# old canon\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("v1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "A: base")
        self.sha_a = self._git("rev-parse", "HEAD")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, repo=None):
        proc = subprocess.run(
            ["git", "-C", str(repo or self.repo), *args],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0,
                         f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def _land_session(self):
        """Commit a session's work on a lane branch, then advance main's ref
        under the primary checkout — exactly what a CAS land does. Returns the
        landed sha: HEAD moves, index and worktree do not."""
        lane = self.base / "lane"
        self._git("worktree", "add", "-b", "lane", str(lane))
        (lane / "src" / "app.py").write_text("v2 landed\n", encoding="utf-8")
        (lane / "PUBLISH.md").write_text("landed doc\n", encoding="utf-8")
        self._git("add", "-A", repo=lane)
        self._git("commit", "-m", "B: landed session", repo=lane)
        sha_b = self._git("rev-parse", "HEAD", repo=lane)
        self._git("update-ref", "refs/heads/main", sha_b)
        return sha_b

    def test_stale_checkout_is_skipped_by_name(self):
        sha_b = self._land_session()
        lines = push_substrate.push_to_repo(self.repo, dry_run=False)
        joined = "\n".join(lines)
        self.assertIn("stale checkout", joined)
        # Nothing committed: HEAD still the landed session, its work intact.
        self.assertEqual(self._git("rev-parse", "HEAD"), sha_b)
        self.assertEqual(self._git("show", "main:src/app.py"), "v2 landed")
        self.assertEqual(self._git("show", "main:PUBLISH.md"), "landed doc")

    def test_dry_run_also_reports_stale_checkout(self):
        # A dry run exists to predict the real run — it must name the skip,
        # not claim the target would refresh.
        self._land_session()
        lines = push_substrate.push_to_repo(self.repo, dry_run=True)
        self.assertIn("stale checkout", "\n".join(lines))
        # And a dry run wrote nothing.
        self.assertEqual((self.repo / "CANON.md").read_text(encoding="utf-8"),
                         "# old canon\n")

    def test_pathspec_commit_cannot_sweep_a_divergent_index(self):
        # Second defense, tested with the guard blinded: even if the staleness
        # check ever misses, the pathspec'd commit must not carry the stale
        # index entries — the 879df82 sweep must be impossible twice over.
        sha_b = self._land_session()
        real_run_git = push_substrate.run_git

        def blind_to_staged(repo, args):
            if args[:3] == ["diff", "--cached", "--name-only"]:
                return True, ""
            return real_run_git(repo, args)

        push_substrate.run_git = blind_to_staged
        self.addCleanup(setattr, push_substrate, "run_git", real_run_git)

        lines = push_substrate.push_to_repo(self.repo, dry_run=False)
        self.assertIn("committed", "\n".join(lines))
        # The refresh commit sits on the landed session...
        self.assertEqual(self._git("rev-parse", "HEAD~1"), sha_b)
        committed = self._git("show", "--name-only", "--format=", "HEAD").splitlines()
        manifest = set(push_substrate.BYTE_IDENTICAL) | {push_substrate.SETTINGS_PATH}
        self.assertTrue(set(committed) <= manifest,
                        f"non-substrate paths in refresh commit: {committed}")
        # ...and the landed work survives it byte-for-byte.
        self.assertEqual(self._git("show", "HEAD:src/app.py"), "v2 landed")
        self.assertEqual(self._git("show", "HEAD:PUBLISH.md"), "landed doc")

    def test_clean_target_still_refreshes(self):
        # The guard must not false-positive an ordinary current checkout.
        lines = push_substrate.push_to_repo(self.repo, dry_run=False)
        joined = "\n".join(lines)
        self.assertNotIn("stale checkout", joined)
        self.assertIn("committed", joined)
        committed = self._git("show", "--name-only", "--format=", "HEAD").splitlines()
        manifest = set(push_substrate.BYTE_IDENTICAL) | {push_substrate.SETTINGS_PATH}
        self.assertTrue(set(committed) <= manifest,
                        f"non-substrate paths in refresh commit: {committed}")
        self.assertNotEqual(self._git("show", "HEAD:CANON.md"), "# old canon")


class UnusableRootsRefusalTest(unittest.TestCase):
    """WI-0132 — this tool WRITES to every member it finds, so finding NONE is the one
    outcome that reports success while doing nothing.

    With a foreign machine's roots config, every path is skipped, the walk finds zero
    converged repos, and the run would print its usual closing summary over an empty
    list. Refusing names the likely cause instead of leaving the operator to notice that
    a fleet push serviced nobody."""

    def test_declared_roots_that_all_missing_are_refused(self):
        rc = push_substrate.main.__globals__["roots_diagnosis"](
            ["/nope/a", "/nope/b"], "the roots given")
        self.assertIn("NO USABLE SEARCH ROOTS", rc)

    def test_a_real_root_is_not_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                push_substrate.main.__globals__["roots_diagnosis"]([d], "x"), "")


class PushMarkerTest(_RepoFixture):
    """WI-0131 — a serviced member learns its substrate moved, from a banner.

    A member's Architect asked for this after finding a settings-strip by noticing unexplained
    tree noise, and marked it low priority themselves: the shrinking-render guard already
    covers the harm, so what this adds is DISCOVERY. These pin the properties that make a
    courtesy notice safe rather than the wording, which is the banner's business."""

    def test_the_marker_names_what_changed(self):
        push_substrate.write_push_marker(self.repo, ["session.py", "poga"])
        rec = json.loads((self.repo / push_substrate.PUSH_MARKER_REL)
                         .read_text(encoding="utf-8"))
        self.assertEqual(rec["files"], ["poga", "session.py"])
        self.assertFalse(rec["announced"], "a fresh notice has not been shown to anyone")
        self.assertTrue(rec["at"], "an undated notice cannot be read as recent or stale")

    def test_it_lands_in_the_gitignored_sidecar_not_the_tree(self):
        """A marker that dirtied the target's tree would be a worse version of the noise
        it exists to replace — and push-substrate commits only substrate paths, so a
        tracked marker would linger as unexplained dirt in someone else's repo."""
        push_substrate.write_push_marker(self.repo, ["session.py"])
        self.assertEqual(push_substrate.PUSH_MARKER_REL.parts[0], ".session-state")
        self.assertTrue((self.repo / ".session-state" /
                         "substrate-push.json").is_file())

    def test_writing_it_can_never_fail_the_push(self):
        """The push has already committed by the time this runs; a courtesy notice must
        not be able to turn a completed refresh into a failure."""
        push_substrate.write_push_marker(self.repo / "does" / "not" / "exist" / "\0bad",
                                         ["session.py"])          # must not raise

    def test_a_second_push_replaces_the_notice_rather_than_appending(self):
        """Each push supersedes the last: the reader wants what just changed, not a log."""
        push_substrate.write_push_marker(self.repo, ["session.py"])
        push_substrate.write_push_marker(self.repo, ["CANON.md"])
        rec = json.loads((self.repo / push_substrate.PUSH_MARKER_REL)
                         .read_text(encoding="utf-8"))
        self.assertEqual(rec["files"], ["CANON.md"])
        self.assertFalse(rec["announced"], "a NEW push is un-announced again")


class FreshnessPreconditionTest(unittest.TestCase):
    """The precondition that was missing (WI-0213 part 1): is the TARGET current?

    This tool had two integrity gates and neither asked. `source_committed()` checks the
    FEDERATION's tree against its own trunk; the ADR-0060 per-target guard checks the
    target's tree against the TARGET'S OWN LOCAL trunk — which a stale clone satisfies
    perfectly, because a clone that has not fetched in weeks is entirely self-consistent.
    On 2026-08-30 that committed into several members and some were rejected at push time
    (each one ahead and behind its remote), by which point the commit existed and the
    divergence had been CREATED rather than avoided. `--repair` makes that recoverable;
    only this makes it not happen.

    Every case runs against a real clone of a real bare remote, because the defect is
    about what a checkout knows about its remote and a fake cannot be stale.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.remote = self.base / "remote.git"
        self.repo = self.base / "member"
        self._run("git", "init", "--bare", "-q", "-b", "main", str(self.remote))
        self._run("git", "clone", "-q", str(self.remote), str(self.repo))
        self._g("config", "user.email", "t@t.t")
        self._g("config", "user.name", "t")
        self._g("config", "commit.gpgsign", "false")
        # Converged substrate, all current except CANON.md — so `diff_files` yields
        # exactly one refresh payload and any write is unmistakable.
        for name in push_substrate.BYTE_IDENTICAL:
            dst = self.repo / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((push_substrate.FED_ROOT / name).read_bytes())
        for name in push_substrate.EXECUTABLE:
            (self.repo / name).chmod(0o755)
        (self.repo / "CANON.md").write_text("# old canon\n", encoding="utf-8")
        (self.repo / "product.py").write_text("# the member's own\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "base")
        self._g("push", "-q", "-u", "origin", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(args, cwd=str(self.base), capture_output=True, text=True,
                              check=True).stdout.strip()

    def _g(self, *args, repo=None):
        proc = subprocess.run(["git", "-C", str(repo or self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode,
                         f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def _push(self, dry_run=False, retire=False):
        return "\n".join(push_substrate.push_to_repo(self.repo, dry_run, retire=retire))

    def _advance_remote_unseen(self, n=1, filename="product.py"):
        """Move the remote WITHOUT letting this clone hear about it. No fetch here:
        that IS the condition — the stale member's own `origin/main` was as stale as its `main`,
        which is why every read-only check reported it `ok` that morning."""
        other = self.base / "other"
        self._run("git", "clone", "-q", str(self.remote), str(other))
        for k, v in (("user.email", "o@o.o"), ("user.name", "o"),
                     ("commit.gpgsign", "false")):
            self._g("config", k, v, repo=other)
        for i in range(n):
            (other / filename).write_text(f"# remote moved on {i}\n", encoding="utf-8")
            self._g("add", filename, repo=other)
            self._g("commit", "-qm", f"member's own work {i}", repo=other)
        self._g("push", "-q", repo=other)
        shutil.rmtree(other)

    # --- the refusal ------------------------------------------------------------

    def test_a_behind_target_is_skipped_by_name_and_nothing_is_written(self):
        """A target merely BEHIND is not diverged yet — committing onto it is what
        diverges it. So the bar is `behind > 0`, not `diverged`: refusing to write is
        strictly better than writing and failing to push."""
        self._advance_remote_unseen()
        before = self._g("rev-parse", "HEAD")
        out = self._push()
        self.assertIn("stale base", out)
        self.assertIn("BEHIND", out)
        self.assertIn("origin/main", out)
        self.assertEqual(before, self._g("rev-parse", "HEAD"), "it committed anyway")
        self.assertEqual("# old canon\n",
                         (self.repo / "CANON.md").read_text(encoding="utf-8"),
                         "it overwrote a substrate file on a stale base")

    def test_a_diverged_target_is_skipped_and_named_diverged(self):
        """The state the 2026-08-30 run left some members in."""
        self._advance_remote_unseen()
        self._g("commit", "-q", "--allow-empty", "-m", "local work")
        before = self._g("rev-parse", "HEAD")
        out = self._push()
        self.assertIn("DIVERGED", out)
        self.assertIn("1 ahead / 1 behind", out)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))

    def test_the_gate_fetches_rather_than_trusting_the_cached_ref(self):
        """THE LOAD-BEARING TEST. Everything above would pass against a read-only check
        too — but only because the fixture happens to ask right after the remote moved.
        This asserts the thing that actually distinguishes the fix: at the moment of the
        refusal the clone's OWN remote-tracking ref still said it was current, exactly
        as the stale member's did. Without the fetch there is nothing here to see."""
        self._advance_remote_unseen()
        self.assertEqual(0, common.git_divergence(self.repo)["behind"],
                         "the cached ref already knew — the premise is broken and this "
                         "test would pass without any fetch at all")
        out = self._push()
        self.assertIn("stale base", out)

    def test_an_unreachable_remote_is_skipped_rather_than_written_blind(self):
        """A base we cannot verify is not a base we may write onto — and a push to a
        remote we cannot reach would strand the commit locally, which is the very shape
        that created the divergence."""
        self._g("remote", "set-url", "origin", str(self.base / "gone.git"))
        before = self._g("rev-parse", "HEAD")
        out = self._push()
        self.assertIn("could not reach", out)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))

    def test_a_plan_refuses_the_same_targets_the_run_would(self):
        """A plan that predicts a write the real run will refuse is a preview of a
        different operation ([`declare-what-a-check-assumes`]), so the plan pays for the
        fetch too. A fetch mutates only remote-tracking refs — it is not a write to the
        member, and the plan's no-write promise is unbroken."""
        self._advance_remote_unseen()
        before = self._g("rev-parse", "HEAD")
        out = self._push(dry_run=True)
        self.assertIn("stale base", out)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))
        self.assertEqual("# old canon\n",
                         (self.repo / "CANON.md").read_text(encoding="utf-8"))

    def test_the_retirement_half_is_gated_too(self):
        """Both halves of `push_to_repo` COMMIT, so the gate sits above them rather than
        inside either one. A removal riding onto a stale base strands exactly the same
        way a refresh does — and is harder to undo."""
        orphan = self.repo / "sessionlib" / "retired_mod.py"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("# retired\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "carries a retired module")
        self._g("push", "-q")
        self._advance_remote_unseen()
        before = self._g("rev-parse", "HEAD")
        out = self._push(retire=True)
        self.assertIn("stale base", out)
        self.assertTrue(orphan.exists(), "a retirement removed a file on a stale base")
        self.assertEqual(before, self._g("rev-parse", "HEAD"))

    # --- and what it must NOT refuse --------------------------------------------

    def test_a_current_target_is_written_and_pushed(self):
        """The gate must not over-refuse: the normal case still delivers, end to end."""
        out = self._push()
        self.assertIn("committed", out)
        self.assertIn("pushed", out)
        self.assertNotIn("stale base", out)
        self.assertEqual((push_substrate.FED_ROOT / "CANON.md").read_bytes(),
                         (self.repo / "CANON.md").read_bytes())
        self._g("fetch", "-q", "origin")
        self.assertEqual("0\t0", self._g("rev-list", "--left-right", "--count",
                                         "HEAD...origin/main"))

    def test_ahead_but_not_behind_is_not_refused(self):
        """Unpushed local commits are not staleness — the push fast-forwards over them.
        Refusing here would strand the fleet on any member that had committed locally."""
        (self.repo / "product.py").write_text("# their own work\n", encoding="utf-8")
        self._g("commit", "-qam", "feat: their own change")
        out = self._push()
        self.assertNotIn("stale base", out)
        self.assertIn("pushed", out)

    def test_a_target_with_no_upstream_is_serviced_and_the_gap_is_named(self):
        """NOT a refusal, and not silence either. A checkout with no tracking ref has no
        remote to be behind and no push to be rejected, so nothing can strand — but
        "current" and "could not ask" are different answers and only one of them was
        checked ([`declare-what-a-check-assumes`])."""
        self._g("remote", "remove", "origin")
        out = self._push()
        self.assertNotIn("stale base", out)
        self.assertIn("committed", out)
        self.assertIn("freshness NOT CHECKED", out)


class UncommittedSubstrateEditsAreSkippedTest(unittest.TestCase):
    """The oldest guard in `_refresh_lines`, and until now the only untested one.

    Handed to this item by WI-0212's closing note: *"a regression test pinning that
    push-substrate SKIPS a dirty target rather than refreshing over it. grep for
    'uncommitted local changes' in tests/ returns zero hits, so that guard is genuinely
    untested."* Its two neighbours — the stale-checkout guard below it and the
    retirement half's twin — both have tests; this one protects a member's uncommitted
    work from being silently overwritten, which is the most expensive of the three to
    get wrong, and it was the one running unwatched.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.repo = self.base / "member"
        self.repo.mkdir()
        self._g("init", "-q", "-b", "main")
        self._g("config", "user.email", "t@t.t")
        self._g("config", "user.name", "t")
        self._g("config", "commit.gpgsign", "false")
        for name in push_substrate.BYTE_IDENTICAL:
            dst = self.repo / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((push_substrate.FED_ROOT / name).read_bytes())
        for name in push_substrate.EXECUTABLE:
            (self.repo / name).chmod(0o755)
        (self.repo / "CANON.md").write_text("# old canon\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "base")

    def tearDown(self):
        self._tmp.cleanup()

    def _g(self, *args):
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode,
                         f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def _push(self):
        return "\n".join(push_substrate.push_to_repo(self.repo, dry_run=False))

    def test_an_uncommitted_substrate_edit_is_skipped_not_clobbered(self):
        """The edit survives. Local substrate edits are already a policy violation — the
        federation is single-writer — but the violation is for the Architects to
        resolve, not for this script to erase before anyone can see it."""
        (self.repo / "CANON.md").write_text("# THEIR UNCOMMITTED EDIT\n", encoding="utf-8")
        before = self._g("rev-parse", "HEAD")
        out = self._push()
        self.assertIn("uncommitted local changes on substrate path(s)", out)
        self.assertIn("CANON.md", out)
        self.assertEqual("# THEIR UNCOMMITTED EDIT\n",
                         (self.repo / "CANON.md").read_text(encoding="utf-8"),
                         "the push overwrote a member's uncommitted work")
        self.assertEqual(before, self._g("rev-parse", "HEAD"))

    def test_a_dirty_non_substrate_file_does_not_block_the_refresh(self):
        """Scoped to the substrate manifest, deliberately. A member with work in
        progress on its OWN files is the normal state of a live repo — blocking on that
        would make the fleet push undeliverable most of the time."""
        (self.repo / "product.py").write_text("# work in progress\n", encoding="utf-8")
        out = self._push()
        self.assertNotIn("uncommitted local changes", out)
        self.assertIn("committed", out)
        self.assertEqual("# work in progress\n",
                         (self.repo / "product.py").read_text(encoding="utf-8"))


class RepairReplaysOnlyOurOwnCommitsTest(unittest.TestCase):
    """`--repair` (WI-0186) — finish a delivery this tool started, and nothing else.

    The 2026-08-30 push committed into some members whose local trunk was already
    behind their remote, because nothing compared a target's local trunk to its REMOTE
    before writing. Each was left diverged, which fails the `git pull --ff-only` its own
    session-start ritual runs. The repair replays our commit onto the fetched tip.

    The load-bearing property is the REFUSAL. Replaying a commit this tool made is the
    federation finishing its own work; replaying anything else is rewriting a member's
    history, which ADR-0088 D1 forbids and which a rebase would do silently. So the
    proof that a commit is ours is two-part — the exact subject AND only substrate paths
    — and either half failing stops the whole repo, untouched.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self.remote = base / "remote.git"
        self.repo = base / "member"
        self._run(base, "git", "init", "--bare", "-q", "-b", "main", str(self.remote))
        self._run(base, "git", "clone", "-q", str(self.remote), str(self.repo))
        self._g("config", "user.email", "t@t.t")
        self._g("config", "user.name", "t")
        for name in push_substrate.BYTE_IDENTICAL:
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(f"# {name} v1\n", encoding="utf-8")
        (self.repo / "product.py").write_text("# member's own\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "base")
        self._g("push", "-q", "-u", "origin", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, cwd, *args):
        return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                              check=True).stdout.strip()

    def _g(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def _advance_remote(self, filename="product.py", body="# moved on\n"):
        """A commit landing on the remote that this clone does not have — the stale-base
        condition that made the original push's commit unpushable."""
        other = pathlib.Path(self._tmp.name) / "other"
        self._run(pathlib.Path(self._tmp.name), "git", "clone", "-q",
                  str(self.remote), str(other))
        subprocess.run(["git", "-C", str(other), "config", "user.email", "o@o.o"], check=True)
        subprocess.run(["git", "-C", str(other), "config", "user.name", "o"], check=True)
        (other / filename).write_text(body, encoding="utf-8")
        # `add` before `commit`, not `commit -a`: -a stages only TRACKED modifications,
        # so a new file silently stages nothing and the commit fails.
        subprocess.run(["git", "-C", str(other), "add", filename],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "member's own work"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(other), "push", "-q"], check=True,
                       capture_output=True)
        shutil.rmtree(other)

    def _substrate_commit(self):
        (self.repo / "session.py").write_text("# session.py v2\n", encoding="utf-8")
        self._g("add", "session.py")
        self._g("commit", "-qm", push_substrate.SUBSTRATE_COMMIT_SUBJECT)

    def _diverged(self):
        counts = self._g("rev-list", "--left-right", "--count", "HEAD...origin/main")
        return tuple(int(x) for x in counts.split())

    def test_a_diverged_member_is_replayed_and_pushed(self):
        self._advance_remote()
        self._substrate_commit()
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertIn("    pushed", lines, lines)
        self._g("fetch", "-q", "origin")
        self.assertEqual((0, 0), self._diverged(),
                         "a repaired member is level with its remote — the replay "
                         "landed and the push delivered it")
        self.assertEqual("# session.py v2\n",
                         (self.repo / "session.py").read_text(encoding="utf-8"))
        self.assertEqual("# moved on\n",
                         (self.repo / "product.py").read_text(encoding="utf-8"),
                         "the member's own landed work was clobbered by the replay")

    def test_a_member_authored_commit_is_refused_and_nothing_moves(self):
        """The property the whole verb turns on. A rebase would happily rewrite this.

        The remote advances on a DIFFERENT file than the local commit touches, so the
        replay would apply cleanly. That matters: an earlier version of this test moved
        both on `product.py`, so it passed even with the ownership proof disabled —
        the conflict refused it, and the test was certifying a guard that was not
        running ([`verify-in-the-created-configuration`])."""
        self._advance_remote(filename="README.md", body="# remote moved on\n")
        (self.repo / "product.py").write_text("# their local work\n", encoding="utf-8")
        self._g("commit", "-qam", "feat: their own change")
        before = self._g("rev-parse", "HEAD")
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertTrue(any("REFUSED" in l for l in lines), lines)
        self.assertEqual(before, self._g("rev-parse", "HEAD"), "history was rewritten")

    def test_the_substrate_subject_alone_is_not_enough(self):
        """Subject is typeable; paths are not. A commit wearing our subject while
        touching a member's own file is not ours, and saying otherwise would let any
        member's commit be rewritten by copying a string. Non-conflicting for the same
        reason as above — only the ownership proof may be what stops this."""
        self._advance_remote(filename="README.md", body="# remote moved on\n")
        (self.repo / "product.py").write_text("# wearing our hat\n", encoding="utf-8")
        self._g("commit", "-qam", push_substrate.SUBSTRATE_COMMIT_SUBJECT)
        before = self._g("rev-parse", "HEAD")
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertTrue(any("non-substrate path" in l for l in lines), lines)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))

    def test_a_dirty_tree_is_skipped_rather_than_autostashed(self):
        """rebase.autoStash is a config away, and silently pocketing someone's
        uncommitted work is not a thing this may do."""
        self._advance_remote()
        self._substrate_commit()
        (self.repo / "product.py").write_text("# uncommitted\n", encoding="utf-8")
        before = self._g("rev-parse", "HEAD")
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertTrue(any("working tree is dirty" in l for l in lines), lines)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))
        self.assertEqual("# uncommitted\n",
                         (self.repo / "product.py").read_text(encoding="utf-8"))

    def test_a_conflicting_replay_aborts_and_leaves_the_repo_as_found(self):
        """The one case where a rebase can strand a repo mid-operation."""
        self._advance_remote(filename="session.py", body="# conflicting session.py\n")
        self._substrate_commit()
        before = self._g("rev-parse", "HEAD")
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertTrue(any("REFUSED" in l for l in lines), lines)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))
        self.assertFalse((self.repo / ".git" / "rebase-merge").exists(),
                         "left mid-rebase")
        self.assertFalse((self.repo / ".git" / "rebase-apply").exists(),
                         "left mid-rebase")

    def test_an_undiverged_member_is_a_no_op(self):
        lines = push_substrate.repair_target(self.repo, dry_run=False)
        self.assertTrue(any("nothing to replay" in l for l in lines), lines)

    def test_a_plan_writes_nothing(self):
        self._advance_remote()
        self._substrate_commit()
        before = self._g("rev-parse", "HEAD")
        lines = push_substrate.repair_target(self.repo, dry_run=True)
        self.assertTrue(any("plan — nothing written" in l for l in lines), lines)
        self.assertEqual(before, self._g("rev-parse", "HEAD"))
        self.assertEqual((1, 1), self._diverged(), "the plan changed the divergence")


class InterfaceRefusesAndPlansFirstTest(unittest.TestCase):
    """WI-0178 — the safe mode is the one you get without asking.

    There was no parser. `dry_run` was `"--dry-run" in args` and every token not starting
    with `-` became a search root, so an unrecognised flag was silently discarded and the
    run proceeded as a REAL push into every member repo found. Measured 2026-08-30:
    `--help` armed a fleet-wide write, and the only thing that stopped it was
    `tests_green()` running the whole suite first, which took long enough to notice. A
    typo'd `--dry-runn` was the same live push with no such reprieve.

    Every case here drives `main()` with the writing half mocked, and asserts on what it
    was ASKED to do — the point is which mode was chosen, and whether anything was
    reached at all.
    """

    def setUp(self):
        # WI-0295: `test_go_runs_both_gates_and_then_pushes_for_real` reaches the real
        # gate helpers, which resolve the MAIN checkout and its journals — the operator's
        # own. Measured: 161 reads from this one test. No root of its own to point at
        # (everything else here is mocked), so the empty default is right.
        neutralize_live_store(self)
        self.pushed, self.gated, self.retire_asked = [], [], []
        self.reconciled, self.reconcile_rc = [], 0
        self._patches = [
            unittest.mock.patch.object(push_substrate, "read_roots_config",
                                       lambda: ["/some/root"]),
            unittest.mock.patch.object(push_substrate, "roots_diagnosis",
                                       lambda *a, **k: None),
            unittest.mock.patch.object(push_substrate, "find_converged_repos",
                                       lambda roots: {"m": pathlib.Path("/some/root/m")}),
            unittest.mock.patch.object(push_substrate, "print_rollout_gate",
                                       lambda roots: None),
            # Stubbed for the same reason as the gate above: this class drives `main()`
            # with a FAKE repo, so the real reconciliation would correctly find every
            # roster member unserviced and return 1 — a true verdict about a fixture,
            # answering a question this class does not ask. It records the mode it was
            # given and returns a clean verdict; `DeliveryReconciliationTest` drives the
            # real arithmetic, and the two cases below pin that `main` still carries
            # this return value out rather than discarding it.
            unittest.mock.patch.object(
                push_substrate, "print_delivery_reconciliation",
                lambda repos, roots, dry_run=False: (
                    self.reconciled.append(bool(dry_run)), self.reconcile_rc)[1]),
            unittest.mock.patch.object(push_substrate, "push_to_repo", self._fake_push),
            unittest.mock.patch.object(
                push_substrate, "tests_green",
                lambda: (self.gated.append("tests"), (True, ""))[1]),
            unittest.mock.patch.object(
                push_substrate, "source_committed",
                lambda: (self.gated.append("source"), (True, "clean"))[1]),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _fake_push(self, repo, dry_run, retire=False):
        """The writing half, replaced. A METHOD rather than a lambda because it has to
        keep the real `push_to_repo` signature: the moment it drifts, `main()` raises
        TypeError and every case here fails on the double instead of on the behaviour.
        Records BOTH modes it was asked for — which mode was chosen is the whole
        subject of this class, and `retire` is a second one (WI-0341)."""
        self.pushed.append(bool(dry_run))
        self.retire_asked.append(bool(retire))
        return []

    def _run(self, argv):
        with unittest.mock.patch("sys.stdout", io.StringIO()) as out:
            rc = push_substrate.main(argv)
        return rc, out.getvalue()

    def test_help_writes_nothing_and_never_reaches_the_gate(self):
        """The measured near-miss. `--help` must answer from the parser and stop —
        crucially BEFORE tests_green(), whose slowness was the only accidental brake."""
        with self.assertRaises(SystemExit) as cm, \
                unittest.mock.patch("sys.stdout", io.StringIO()):
            push_substrate.main(["--help"])
        self.assertEqual(0, cm.exception.code)
        self.assertEqual([], self.pushed)
        self.assertEqual([], self.gated)

    def test_an_unknown_flag_is_refused_not_swallowed(self):
        """`--dry-runn` used to be a real push: it starts with `-`, so it was not taken
        as a root, and it is not `--dry-run`, so it did not set dry_run either."""
        with self.assertRaises(SystemExit) as cm, \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            push_substrate.main(["--dry-runn"])
        self.assertNotEqual(0, cm.exception.code)
        self.assertEqual([], self.pushed)
        self.assertEqual([], self.gated)

    def test_no_arguments_plans_and_writes_nothing(self):
        rc, out = self._run([])
        self.assertEqual(0, rc)
        self.assertEqual([True], self.pushed, "bare invocation must be a plan")
        self.assertIn("PLAN ONLY", out)

    def test_a_delivery_gap_is_carried_out_of_main_as_the_exit_code(self):
        """The reconciliation is only a guard if its verdict survives the trip out of
        `main()`. `_do_push` is a closure whose result used to be discarded — `main`
        returned a literal 0 on every path that ran — so this pins the wiring, not the
        arithmetic."""
        self.reconcile_rc = 1
        rc, _ = self._run([])
        self.assertEqual(1, rc)
        self.assertEqual([True], self.reconciled, "the plan must still reconcile")

    def test_both_a_plan_and_a_real_run_reconcile(self):
        """A plan that skipped the reconciliation would preview a different operation
        than `--go` performs — the same argument the gate-skipping case below makes."""
        self._run([])
        self._run(["--go"])
        self.assertEqual([True, False], self.reconciled)

    def test_a_plan_says_the_gates_did_not_run(self):
        """A plan that silently skipped a gate the real run enforces would be a preview
        of a different operation than --go performs."""
        rc, out = self._run([])
        self.assertEqual([], self.gated)
        self.assertIn("NOT RUN in plan mode", out)

    def test_go_runs_both_gates_and_then_pushes_for_real(self):
        rc, _ = self._run(["--go"])
        self.assertEqual(0, rc)
        self.assertEqual(["tests", "source"], self.gated)
        self.assertEqual([False], self.pushed)

    def test_go_refuses_on_a_red_suite_without_touching_a_repo(self):
        with unittest.mock.patch.object(push_substrate, "tests_green",
                                        lambda: (False, "3 failures")), \
                unittest.mock.patch("sys.stdout", io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(2, push_substrate.main(["--go"]))
        self.assertEqual([], self.pushed)

    def test_dry_run_still_means_what_it_said(self):
        """Kept as an accepted flag so existing invocations and docs do not change
        meaning — it is simply no longer the only thing standing between a typo and a
        fleet write."""
        rc, _ = self._run(["--dry-run"])
        self.assertEqual([True], self.pushed)

    def test_go_and_dry_run_together_are_refused_rather_than_ranked(self):
        """Asking for both is a mistake, and guessing which one was meant is how a
        preview becomes a push."""
        with self.assertRaises(SystemExit), unittest.mock.patch("sys.stderr", io.StringIO()):
            push_substrate.main(["--go", "--dry-run"])
        self.assertEqual([], self.pushed)

    def test_retire_orphans_is_off_unless_asked_and_reaches_the_worker_when_it_is(self):
        """WI-0341's flag has to actually arrive. A removal verb wired to a flag the
        worker never sees is the same silent-discard class this parser was rewritten to
        close — it would report as asked-for and do nothing, or worse, the reverse."""
        self._run(["--go"])
        self.assertEqual([False], self.retire_asked)
        self.retire_asked.clear()
        self._run(["--go", "--retire-orphans"])
        self.assertEqual([True], self.retire_asked)

    def test_roots_are_still_positional_and_override_the_config(self):
        seen = []
        with unittest.mock.patch.object(push_substrate, "find_converged_repos",
                                        lambda roots: seen.append(list(roots)) or {}), \
                unittest.mock.patch("sys.stdout", io.StringIO()):
            push_substrate.main(["/a", "/b"])
        self.assertEqual([["/a", "/b"]], seen)


class RetiredSubstrateIsRemovableTest(unittest.TestCase):
    """WI-0341 — the federation could add a file to every member and never remove one.

    `diff_files` iterates BYTE_IDENTICAL, so it enumerates what the federation HAS.
    Nothing enumerated what a TARGET has that the federation has LOST, and grepping
    unlink/remove/delete across the whole tool found no target-side deletion path at
    all — so a retired `sessionlib` module, or any root file dropped from the manifest,
    sat in every member permanently. Found on OPS-0007's first run, 2026-09-11.

    Severity was low BY A MECHANISM rather than by luck: `sessionlib/__init__.py`'s
    PARTS list ships too, so an orphaned module is dead weight rather than live wrong
    behaviour. That is precisely why the remedy must not be more dangerous than the
    disease — a deletion path running unattended across every member would be a worse
    defect than the inert file it removes. Hence: REPORT on every run, REMOVE only
    behind an explicit flag, and never delete anything git cannot give back.

    The fixture points FED_ROOT at a fake federation carrying ONLY a `sessionlib/`
    package, so `diff_files` finds no sources to refresh and every case here exercises
    the retirement half alone. `federation_sessionlib_files()` reads FED_ROOT at CALL
    time, which is what makes "what the federation still ships" controllable from here.
    """

    ORPHAN = "sessionlib/retired_mod.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)

        self.fed = self.base / "fed"
        (self.fed / "sessionlib").mkdir(parents=True)
        for mod in ("config.py", "land.py"):
            (self.fed / "sessionlib" / mod).write_text(f"# {mod}\n", encoding="utf-8")
        self._orig_fed = push_substrate.FED_ROOT
        push_substrate.FED_ROOT = self.fed
        self.addCleanup(setattr, push_substrate, "FED_ROOT", self._orig_fed)

        self.remote = self.base / "remote.git"
        self.repo = self.base / "member"
        self._run("git", "init", "--bare", "-q", "-b", "main", str(self.remote))
        self._run("git", "clone", "-q", str(self.remote), str(self.repo))
        self._g("config", "user.email", "t@t.t")
        self._g("config", "user.name", "t")
        self._g("config", "commit.gpgsign", "false")
        (self.repo / "CANON.md").write_text("# canon\n", encoding="utf-8")
        (self.repo / "session.py").write_text("# harness\n", encoding="utf-8")
        (self.repo / "sessionlib").mkdir()
        # Two modules the federation still carries, and one it does not.
        for mod in ("config.py", "land.py", "retired_mod.py"):
            (self.repo / "sessionlib" / mod).write_text(f"# {mod}\n", encoding="utf-8")
        (self.repo / "product.py").write_text("# the member's own\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "base")
        self._g("push", "-q", "-u", "origin", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(args, cwd=str(self.base), capture_output=True, text=True,
                              check=True).stdout.strip()

    def _g(self, *args):
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode,
                         f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def _push(self, dry_run=False, retire=False):
        return "\n".join(push_substrate.push_to_repo(self.repo, dry_run, retire=retire))

    # --- reporting: happens always, costs nothing, and is the acceptance -------------

    def test_an_orphan_is_reported_and_left_alone_without_the_flag(self):
        """The default run. Removal is opt-in, so the file stays — but the fleet can
        now SEE it, which it could not before at any flag setting."""
        head = self._g("rev-parse", "HEAD")
        out = self._push(retire=False)
        self.assertIn(self.ORPHAN, out)
        self.assertIn("no longer ships this file", out)
        self.assertIn("NOT removed", out)
        self.assertTrue((self.repo / self.ORPHAN).is_file())
        self.assertEqual(head, self._g("rev-parse", "HEAD"))

    def test_a_clean_member_still_reports_up_to_date(self):
        """No orphan, nothing to refresh — the pre-existing one-line report is
        unchanged, so a quiet member does not become noisy."""
        (self.repo / self.ORPHAN).unlink()
        self._g("commit", "-qam", "member drops it itself")
        self.assertIn("up to date — no change", self._push(retire=True))

    # --- removal: only on the flag, only what git can give back ----------------------

    def test_the_flag_removes_commits_and_pushes_exactly_the_orphan(self):
        out = self._push(retire=True)
        self.assertIn("removed and committed 1 retired file(s)", out)
        self.assertIn("pushed", out)
        self.assertFalse((self.repo / self.ORPHAN).exists())
        self.assertEqual(push_substrate.SUBSTRATE_RETIRE_SUBJECT,
                         self._g("log", "-1", "--format=%s"))
        self.assertEqual([self.ORPHAN],
                         self._g("show", "--name-only", "--format=", "HEAD").split())
        self.assertEqual(self._g("rev-parse", "HEAD"),
                         self._g("rev-parse", "origin/main"))

    def test_a_retirement_is_labelled_a_removal_and_not_a_refresh(self):
        """The two subjects have to stay DISTINCT, and only this case says so. Asserting
        the commit carries SUBSTRATE_RETIRE_SUBJECT is tautological if that constant is
        the refresh subject — measured by mutation: collapsing the two left every other
        case in this class green. A member reading its own `git log` must be able to
        tell a file that was deleted from a file that was refreshed, and that is the
        entire reason the removal is a second commit."""
        self.assertNotEqual(push_substrate.SUBSTRATE_COMMIT_SUBJECT,
                            push_substrate.SUBSTRATE_RETIRE_SUBJECT)
        self._push(retire=True)
        subject = self._g("log", "-1", "--format=%s")
        self.assertIn("retire", subject)
        self.assertNotIn("refresh", subject)

    def test_modules_the_federation_still_ships_are_never_touched(self):
        self._push(retire=True)
        for live in ("sessionlib/config.py", "sessionlib/land.py"):
            self.assertTrue((self.repo / live).is_file(), live)
        self.assertTrue((self.repo / "product.py").is_file())

    def test_a_plan_names_the_removal_and_performs_none(self):
        out = self._push(dry_run=True, retire=True)
        self.assertIn(self.ORPHAN, out)
        self.assertIn("(plan — nothing removed)", out)
        self.assertTrue((self.repo / self.ORPHAN).is_file())

    def test_an_untracked_orphan_is_reported_and_left(self):
        """Only a TRACKED deletion is recoverable from the member's own history. An
        untracked file has no such backstop, so it is named and left however confident
        the retirement verdict is."""
        (self.repo / "sessionlib" / "scratch.py").write_text("# x\n", encoding="utf-8")
        removable, notes = push_substrate.orphan_files(self.repo)
        self.assertNotIn("sessionlib/scratch.py", removable)
        self.assertIn("UNTRACKED", "\n".join(notes))
        self._push(retire=True)
        self.assertTrue((self.repo / "sessionlib" / "scratch.py").is_file())

    def test_local_edits_to_a_retired_path_skip_the_removal(self):
        """A deletion must not carry off someone's unsaved work on the way out."""
        (self.repo / self.ORPHAN).write_text("# edited by the member\n", encoding="utf-8")
        out = self._push(retire=True)
        self.assertIn("SKIPPED removal", out)
        self.assertTrue((self.repo / self.ORPHAN).is_file())
        self.assertIn("edited by the member",
                      (self.repo / self.ORPHAN).read_text(encoding="utf-8"))

    # --- the failure this check must not have ----------------------------------------

    def test_a_federation_with_no_readable_package_deletes_nothing(self):
        """With no federation package to compare against, EVERY module in the target
        reads as "not in the federation" — so the naive answer is to delete the whole
        package from every member, which is incomparably worse than the immortal file.
        It must refuse to answer, and say so rather than return an empty orphan list
        that reads as clean."""
        shutil.rmtree(self.fed / "sessionlib")
        removable, notes = push_substrate.orphan_files(self.repo)
        self.assertEqual([], removable)
        self.assertIn("NOT CHECKED", "\n".join(notes))
        out = self._push(retire=True)
        self.assertIn("NOT CHECKED", out)
        for mod in ("config.py", "land.py", "retired_mod.py"):
            self.assertTrue((self.repo / "sessionlib" / mod).is_file(), mod)

    # --- declared root retirements ---------------------------------------------------

    def test_a_declared_root_retirement_is_reported_and_removable(self):
        """A root file cannot be derived — the federation's absence of `old_check.py`
        is not evidence it ever shipped one — so it is DECLARED in RETIRED."""
        (self.repo / "old_check.py").write_text("# retired\n", encoding="utf-8")
        self._g("add", "old_check.py")
        self._g("commit", "-qm", "member carries the old root file")
        with unittest.mock.patch.object(push_substrate, "RETIRED", ["old_check.py"]):
            out = self._push(retire=True)
        self.assertIn("old_check.py", out)
        self.assertFalse((self.repo / "old_check.py").exists())

    def test_a_root_file_not_declared_retired_is_never_touched(self):
        """The bound on the whole mechanism: with an empty RETIRED, a member's own root
        files are invisible to it. Nothing here may generalise to "delete whatever the
        federation does not happen to have"."""
        self.assertEqual([], push_substrate.RETIRED)
        self._push(retire=True)
        self.assertTrue((self.repo / "product.py").is_file())
        self.assertTrue((self.repo / "CANON.md").is_file())

    def test_a_path_both_shipped_and_retired_is_refused_not_deleted(self):
        """A contradiction resolves toward NOT deleting — the half that leaves the
        target as it was — and is named, because a silent resolution is how it survives
        to the next reader."""
        with unittest.mock.patch.object(push_substrate, "RETIRED", ["session.py"]):
            removable, notes = push_substrate.orphan_files(self.repo)
            out = self._push(retire=True)
        self.assertNotIn("session.py", removable)
        self.assertIn("cannot be both", "\n".join(notes))
        self.assertIn("cannot be both", out)
        self.assertTrue((self.repo / "session.py").is_file())

    # --- the couplings a new write verb has to keep ----------------------------------

    def test_the_repair_still_recognises_a_retirement_commit_as_ours(self):
        """`--repair` proves a commit is the federation's by subject AND touched paths.
        A retirement carries a DIFFERENT subject and deletes a path in no BYTE_IDENTICAL
        list, so the manifest as it stood would have called this tool's own commit
        foreign — a repair that declines to fix what it wrote."""
        self._push(retire=True)
        ok, why = push_substrate._commit_is_ours(self.repo, self._g("rev-parse", "HEAD"))
        self.assertTrue(ok, why)

    def test_the_retire_subject_alone_still_does_not_make_a_commit_ours(self):
        """The ownership proof must not have been weakened by widening it: anyone can
        type the subject, so the touched paths still have to be substrate."""
        (self.repo / "product.py").write_text("# member edit\n", encoding="utf-8")
        self._g("commit", "-qam", push_substrate.SUBSTRATE_RETIRE_SUBJECT)
        ok, why = push_substrate._commit_is_ours(self.repo, self._g("rev-parse", "HEAD"))
        self.assertFalse(ok)
        self.assertIn("product.py", why)

    def test_the_marker_records_a_removal_apart_from_a_refresh(self):
        """The member-side banner renders `files` as "substrate: refreshed ... — <names>",
        so a DELETED file listed there would tell the member its substrate was refreshed
        with a file that is gone. Its own key instead — inert to that reader, and a
        durable local receipt that the deletion happened."""
        self._push(retire=True)
        rec = json.loads((self.repo / push_substrate.PUSH_MARKER_REL)
                         .read_text(encoding="utf-8"))
        self.assertEqual([self.ORPHAN], rec["retired"])
        self.assertEqual([], rec["files"])
        self.assertIs(False, rec["announced"])

    def test_repair_and_retire_together_are_refused_rather_than_ignored(self):
        """A repair replays commits already made and writes no new bytes, so the flag
        could not do anything — and a flag accepted-but-inert is the exact defect
        WI-0178 closed in this parser."""
        with self.assertRaises(SystemExit) as cm, \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            push_substrate.main(["--repair", "--retire-orphans", "--go"])
        self.assertNotEqual(0, cm.exception.code)


class DeliveryReconciliationTest(unittest.TestCase):
    """A member this run could not reach is NAMED, and an undeclared one FAILS.

    THE DEFECT. A member could go without substrate indefinitely, and no run of this
    tool would say so. The cause is not a skip: membership is `find_converged_repos`, a walk
    of this machine's disk, and repos that reside on another machine are deliberately
    absent from this machine's roots, so such a member is never a candidate to skip. A
    run could service most of the roster and print nothing about the members it could
    not see.

    So these tests drive the RECONCILIATION, not the walk: given what a run serviced and
    what the roster expects, is the remainder named, is it split by why, and does the
    undeclared half cost an exit code. `find_converged_repos` is mocked everywhere else
    in this module, which is exactly why the question "who did the walk miss" had no
    coverage at all until now.
    """

    ROSTER = {"alpha", "beta", "gone", "away"}

    def _reconcile(self, serviced, residency, machine="DevBox", roster=None):
        """Drive `delivery_reconciliation` with a stubbed roster and locator.

        `serviced` and the locator's answer are set to the SAME repo paths, so the test
        exercises the reconciliation arithmetic rather than re-testing path resolution.
        """
        roster = self.ROSTER if roster is None else roster
        located = {sid: (pathlib.Path(f"/fleet/{sid}"), {}) for sid in serviced}

        class _SV:
            LATEST = "1.0.0"

            @staticmethod
            def find_members(_roots):
                return dict(located)

            @staticmethod
            def resolve_mapped(_paths):
                return {}

            @staticmethod
            def coverage(located_sids):
                loc = set(located_sids)
                return {"roster": set(roster), "expected": set(roster),
                        "located": loc, "unlocated": sorted(set(roster) - loc),
                        "unknown": [], "stale_parked": [], "parked": []}

        class _Reconcile:
            @staticmethod
            def roster_residency():
                return dict(residency)

        with unittest.mock.patch.dict(
                sys.modules, {"standard_version": _SV, "reconcile": _Reconcile}), \
                unittest.mock.patch.object(push_substrate, "this_machine",
                                           lambda: machine), \
                unittest.mock.patch.object(push_substrate, "read_repo_paths_config",
                                           lambda _p: {}):
            return push_substrate.delivery_reconciliation(
                [pathlib.Path(f"/fleet/{sid}") for sid in serviced], ["/fleet"])

    def test_a_member_residing_elsewhere_is_named_with_its_machine(self):
        rec = self._reconcile(
            serviced={"alpha", "beta", "gone"}, residency={"away": "Runner"})
        self.assertEqual([("away", "Runner")], rec["elsewhere"])
        self.assertEqual([], rec["gap"])

    def test_an_undeclared_miss_is_a_gap_and_not_excused(self):
        """The half that must never be pooled with the one above. `gone` has no
        residency declaration, so its absence is a defect, not an arrangement."""
        rec = self._reconcile(
            serviced={"alpha", "beta"}, residency={"away": "Runner"})
        self.assertEqual([("away", "Runner")], rec["elsewhere"])
        self.assertEqual(["gone"], rec["gap"])

    def test_a_member_declaring_THIS_machine_is_a_gap_not_an_excuse(self):
        """`declared_elsewhere` returns "" when the declared machine IS this one — a
        member that claims to live here and was not serviced here is a real gap. Pinned
        because the excusing branch is the one that would swallow it."""
        rec = self._reconcile(
            serviced={"alpha", "beta", "away"}, residency={"gone": "DevBox"})
        self.assertEqual([], rec["elsewhere"])
        self.assertEqual(["gone"], rec["gap"])

    def test_servicing_every_expected_member_reconciles_clean(self):
        rec = self._reconcile(serviced=set(self.ROSTER), residency={})
        self.assertEqual([], rec["elsewhere"])
        self.assertEqual([], rec["gap"])
        self.assertEqual(sorted(self.ROSTER), rec["serviced"])

    def test_the_gap_costs_an_exit_code_and_an_elsewhere_does_not(self):
        """The whole point of the reconciliation is that one of these two outcomes is a
        failure. If both exit 0 the surface is a comment."""
        for residency, expected_rc in (({"gone": "Runner", "away": "Runner"}, 0),
                                       ({"away": "Runner"}, 1)):
            with self.subTest(residency=residency):
                with unittest.mock.patch.object(
                        push_substrate, "delivery_reconciliation",
                        return_value=self._reconcile(
                            serviced={"alpha", "beta"}, residency=residency)):
                    buf = io.StringIO()
                    with unittest.mock.patch("sys.stdout", buf):
                        rc = push_substrate.print_delivery_reconciliation([], ["/fleet"])
                self.assertEqual(expected_rc, rc)

    def test_an_unreadable_roster_refuses_rather_than_reporting_a_clean_run(self):
        """NOT fail-open, unlike `print_rollout_gate` one function down. A clean-looking
        delivery over a denominator the run could not read is the precise silence this
        was built to end, so the refusal is the feature."""
        with unittest.mock.patch.object(
                push_substrate, "delivery_reconciliation",
                side_effect=push_substrate.RosterUnreadableHere("roster table moved")):
            err = io.StringIO()
            with unittest.mock.patch("sys.stderr", err):
                rc = push_substrate.print_delivery_reconciliation([], ["/fleet"])
        self.assertEqual(2, rc)
        self.assertIn("REFUSED", err.getvalue())
        self.assertIn("roster table moved", err.getvalue())

    def test_the_not_serviced_members_are_named_in_the_output(self):
        """A count is not a report: "serviced 11 of 14" sends nobody anywhere. The three
        that did not get it have to appear by name, which is the half the 2026-09-10 run
        was missing."""
        rec = self._reconcile(serviced={"alpha"},
                              residency={"away": "Runner", "beta": "Runner"})
        with unittest.mock.patch.object(
                push_substrate, "delivery_reconciliation", return_value=rec):
            buf = io.StringIO()
            with unittest.mock.patch("sys.stdout", buf):
                push_substrate.print_delivery_reconciliation([], ["/fleet"])
        out = buf.getvalue()
        self.assertIn("away (Runner)", out)
        self.assertIn("beta (Runner)", out)
        self.assertIn("gone", out)

    def test_dry_run_changes_the_verb_but_not_the_verdict(self):
        """A plan must not claim a delivery it did not make — and must not soften the
        exit code either, or `--go` and a plan would disagree about the fleet."""
        rec = self._reconcile(serviced={"alpha", "beta"}, residency={"away": "Runner"})
        seen = {}
        for dry in (True, False):
            with unittest.mock.patch.object(
                    push_substrate, "delivery_reconciliation", return_value=rec):
                buf = io.StringIO()
                with unittest.mock.patch("sys.stdout", buf):
                    seen[dry] = (push_substrate.print_delivery_reconciliation(
                        [], ["/fleet"], dry_run=dry), buf.getvalue())
        self.assertIn("would service", seen[True][1])
        self.assertIn("serviced 2 of", seen[False][1])
        self.assertEqual(seen[True][0], seen[False][0])

    def test_finding_zero_repos_still_reconciles(self):
        """The loudest version of the defect: a walk that finds nothing used to print
        "nothing to do" and exit 0 over a whole roster."""
        rec = self._reconcile(serviced=set(), residency={})
        self.assertEqual(sorted(self.ROSTER), rec["gap"])
        self.assertEqual([], rec["serviced"])


class TheResidencyRuleExistsOnceTest(unittest.TestCase):
    """P16 — push-substrate joins reconcile and deliver on the ONE residency rule.

    `tests/test_reconcile_residency.py::OneImplementationTest` already pins that
    reconcile and deliver share the function object rather than a copy. This tool now
    prints a residency verdict into the same operator's view, so a third copy could
    drift into contradicting the other two — which is the defect WI-0253 was filed
    about, arriving by a third door."""

    def test_push_substrate_shares_the_function_objects(self):
        self.assertIs(push_substrate.declared_elsewhere, common.declared_elsewhere)
        self.assertIs(push_substrate.this_machine, common.this_machine)

    def test_it_defines_no_copy_of_its_own(self):
        text = (ROOT / "curate" / "push-substrate.py").read_text(encoding="utf-8")
        self.assertNotIn("def declared_elsewhere(", text)
        self.assertNotIn("def this_machine(", text)


if __name__ == "__main__":
    unittest.main()
