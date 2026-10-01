"""Tests for curate/reconcile.py — the repo-path locator map + stale-clone tell.

The load-bearing property (federation sessions 35/37/47): a live, converged system
whose repo is not under a walk root (on another volume, say) must reconcile via the
`repo-paths.local` map and NEVER be reported as "not onboarded." UNLOCATED means
"not reachable from this machine"; a bad map entry is BROKEN MAP (fix the map); and
a checkout far behind its own origin is flagged as a possible stale clone — the tell
that would have caught a retired abandoned copy of a member (5 ahead / 125 behind).

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("reconcile", ROOT / "curate" / "reconcile.py")
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)

import common  # noqa: E402  (curate/ is on the path from above)


class RepoPathsConfigTest(unittest.TestCase):
    """Parsing repo-paths.local: split on first `=`, tolerate spaces/`=` in paths."""

    def _parse(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "repo-paths.local"
            p.write_text(text, encoding="utf-8")
            return common.read_repo_paths_config(p)

    def test_missing_file_is_empty(self):
        self.assertEqual(common.read_repo_paths_config(ROOT / "does-not-exist.local"), {})

    def test_basic_mapping(self):
        self.assertEqual(self._parse("orbit = /Users/x/Orbit\n"),
                         {"orbit": "/Users/x/Orbit"})

    def test_path_with_spaces_and_equals(self):
        # Paths can have spaces; split on the FIRST `=` only.
        line = "orbit = /Users/x/Shared Volume/Projects/Orbit Project\n"
        self.assertEqual(self._parse(line),
                         {"orbit": "/Users/x/Shared Volume/Projects/Orbit Project"})

    def test_comments_blanks_and_no_equals_ignored(self):
        text = "# comment\n\n/Users/x/no-equals-line\norbit = /h\n"
        self.assertEqual(self._parse(text), {"orbit": "/h"})

    def test_later_line_wins(self):
        self.assertEqual(self._parse("h = /a\nh = /b\n"), {"h": "/b"})


class ResolveMappedTest(unittest.TestCase):
    """Direct STATUS.md read at mapped paths: found vs the reasons it's BROKEN MAP."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _repo(self, name, status_body=None):
        r = self.base / name
        r.mkdir(parents=True, exist_ok=True)
        if status_body is not None:
            (r / "STATUS.md").write_text(status_body, encoding="utf-8")
        return r

    def test_valid_mapped_repo_resolves(self):
        r = self._repo("Orbit Project", "---\nid: orbit\nversion: 3.5.1\n---\n")
        found, broken = reconcile.resolve_mapped({"orbit": str(r)})
        self.assertIn("orbit", found)
        self.assertEqual(broken, {})
        fm, path = found["orbit"]
        self.assertEqual(fm["version"], "3.5.1")
        self.assertEqual(path.name, "STATUS.md")

    def test_missing_directory_is_broken(self):
        found, broken = reconcile.resolve_mapped({"orbit": str(self.base / "nope")})
        self.assertEqual(found, {})
        self.assertIn("orbit", broken)

    def test_no_status_file_is_broken(self):
        r = self._repo("clone")  # dir exists, no STATUS.md — the dead-clone shape
        found, broken = reconcile.resolve_mapped({"orbit": str(r)})
        self.assertEqual(found, {})
        self.assertIn("orbit", broken)

    def test_id_mismatch_is_broken_not_silently_accepted(self):
        r = self._repo("wrong", "---\nid: something-else\nversion: 1.0.0\n---\n")
        found, broken = reconcile.resolve_mapped({"orbit": str(r)})
        self.assertEqual(found, {})
        self.assertIn("orbit", broken)
        self.assertIn("something-else", broken["orbit"][1])

    def test_status_without_id_is_broken_not_accepted_under_mapped_key(self):
        """WI-0373, the quiet half — and this test used to assert the defect.

        It read `test_status_without_id_is_accepted_under_mapped_key` and pinned the
        old `if live_id and live_id != sid` guard as intended behaviour: an id-less
        STATUS.md was "trusted to the key it's mapped under". That trust is the bug.
        The map key says which repo we opened; it cannot say what the repo IS, and a
        STATUS.md with no `id:` has made no self-report to agree or disagree with. The
        result was a broken member certified as converged — the failure direction the
        `id != key` case right above has always been caught in.
        """
        r = self._repo("h", "---\nversion: 2.0.0\n---\n")
        found, broken = reconcile.resolve_mapped({"orbit": str(r)})
        self.assertEqual(found, {})
        self.assertIn("orbit", broken)
        self.assertIn("no id", broken["orbit"][1])

    def test_a_status_with_no_frontmatter_at_all_is_broken_and_says_so(self):
        """The two id-less shapes are different facts and must not share a message —
        "frontmatter carries no `id:`" about a file with no header points the reader
        at a line that does not exist."""
        r = self._repo("h", "# STATUS\n\nnothing here\n")
        found, broken = reconcile.resolve_mapped({"orbit": str(r)})
        self.assertEqual(found, {})
        self.assertIn("no frontmatter block", broken["orbit"][1])


class GitStalenessTest(unittest.TestCase):
    """The two verdicts over one predicate, on REAL repos with a bare remote.

    WI-0213. These were mock-`subprocess` tests, and a mock that answers every git call
    with one canned string cannot tell a refusal apart from an answer — it certainly
    cannot show that a 1-ahead/1-behind member reads clean. The defect this class now
    pins was measured on real repositories, so the fixture is a real repository.
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
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
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

    def _ahead(self, n=1):
        """`n` local commits the remote does not have."""
        for i in range(n):
            self._g("commit", "-q", "--allow-empty", "-m", f"local {i}")

    def _behind(self, n=1):
        """`n` commits on the remote this clone does not have — then FETCH, so the
        remote-tracking ref actually knows about them. `git_staleness` is read-only by
        contract, so without the fetch it would be asking about a ref that never moved
        and every case below would answer the same way."""
        other = self.base / "other"
        self._run("git", "clone", "-q", str(self.remote), str(other))
        self._g("config", "user.email", "o@o.o", repo=other)
        self._g("config", "user.name", "o", repo=other)
        self._g("config", "commit.gpgsign", "false", repo=other)
        for i in range(n):
            self._g("commit", "-q", "--allow-empty", "-m", f"remote {i}", repo=other)
        self._g("push", "-q", repo=other)
        shutil.rmtree(other)
        self._g("fetch", "-q", "origin")

    # --- DIVERGED: reported at ANY depth, with no threshold ---------------------

    def test_one_ahead_one_behind_is_flagged_diverged(self):
        """The 1/1 case, which read `ok` on the morning of the incident. It is 39
        commits short of the stale-clone threshold and still cannot `pull --ff-only`."""
        self._ahead(1)
        self._behind(1)
        flag = reconcile.git_staleness(self.repo)
        self.assertIsNotNone(flag, "a genuinely diverged member reported clean")
        self.assertIn("DIVERGED", flag)
        self.assertIn("1 ahead", flag)
        self.assertIn("1 behind", flag)

    def test_shallow_divergence_is_flagged_diverged(self):
        """A shallow divergence: 1 ahead / 2 behind."""
        self._ahead(1)
        self._behind(2)
        flag = reconcile.git_staleness(self.repo)
        self.assertIn("DIVERGED", flag)
        self.assertIn("2 behind", flag)

    def test_a_diverged_member_fails_pull_ff_only(self):
        """WHY any depth is the right bar, asserted against git rather than restated.

        The flag's whole claim is that this member can no longer run the `git pull
        --ff-only` in its own session-start ritual. That claim is checkable, and a
        threshold chosen without checking it is how 1/1 came to read clean."""
        self._ahead(1)
        self._behind(1)
        proc = subprocess.run(["git", "-C", str(self.repo), "pull", "--ff-only"],
                              capture_output=True, text=True)
        self.assertNotEqual(0, proc.returncode,
                            "a 1/1 divergence fast-forwarded — the premise is wrong")
        self.assertIsNotNone(reconcile.git_staleness(self.repo))

    # --- STALE-CLONE: keeps its threshold, and keeps answering its own question --

    def test_deep_divergence_still_reads_as_stale_clone(self):
        """The abandoned-copy shape. Past the threshold the abandoned-copy verdict
        wins the line — it already names the divergence in its own text, so nothing is
        lost, and the two questions stay distinguishable to a reader."""
        self._ahead(1)
        self._behind(reconcile.BEHIND_COMMITS)
        flag = reconcile.git_staleness(self.repo)
        self.assertIn("STALE-CLONE?", flag)
        self.assertIn("diverged", flag)
        self.assertIn(f"{reconcile.BEHIND_COMMITS} behind", flag)

    def test_far_behind_but_not_ahead_is_stale_clone_not_diverged(self):
        """Behind-only is not divergence: it still fast-forwards. The threshold is the
        whole verdict here, and it is unchanged."""
        self._behind(reconcile.BEHIND_COMMITS)
        flag = reconcile.git_staleness(self.repo)
        self.assertIn("STALE-CLONE?", flag)
        self.assertNotIn("DIVERGED", flag)

    def test_slightly_behind_only_is_not_flagged(self):
        """Under the threshold and not ahead: a normal checkout that has not pulled
        yet. Flagging this would fire on every healthy member in the fleet."""
        self._behind(3)
        self.assertIsNone(reconcile.git_staleness(self.repo))

    def test_ahead_only_is_not_flagged(self):
        """Unpushed local commits are a backup question, not a staleness one — and
        `metrics.py` already reports them separately as `unbacked-up`."""
        self._ahead(2)
        self.assertIsNone(reconcile.git_staleness(self.repo))

    def test_current_checkout_is_not_flagged(self):
        self.assertIsNone(reconcile.git_staleness(self.repo))

    # --- fail-soft: every unanswerable case is None, never a raise ---------------

    def test_no_upstream_returns_none(self):
        plain = self.base / "plain"
        plain.mkdir()
        self._g("init", "-q", "-b", "main", repo=plain)
        self.assertIsNone(reconcile.git_staleness(plain))

    def test_not_a_repo_returns_none(self):
        d = self.base / "not-a-repo"
        d.mkdir()
        self.assertIsNone(reconcile.git_staleness(d))

    def test_git_absent_returns_none(self):
        with mock.patch.object(common.subprocess, "run", side_effect=OSError):
            self.assertIsNone(reconcile.git_staleness(self.repo))

    # --- the read-only contract, which this refactor must not have widened -------

    def test_the_reconcile_read_never_fetches(self):
        """`git_staleness` is called from the session-start hook and from metrics; its
        docstring promises no network and no ref mutation. The fetching mode is opt-in
        and belongs to callers about to WRITE, so this pins that the default did not
        quietly acquire it ([`declare-what-a-check-assumes`])."""
        self._behind(1)
        self._g("update-ref", "-d", "refs/remotes/origin/main")
        before = self._g("rev-parse", "HEAD")
        reconcile.git_staleness(self.repo)
        self.assertNotEqual(
            0, subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify",
                               "--quiet", "refs/remotes/origin/main"],
                              capture_output=True, text=True).returncode,
            "git_staleness fetched — the read-only reconcile contract is broken")
        self.assertEqual(before, self._g("rev-parse", "HEAD"))


class GitDivergenceFetchModeTest(unittest.TestCase):
    """The opt-in fetching mode (WI-0213 part 3) — the half a cached ref cannot do.

    reconcile reported a member `ok` on the morning of the incident while it was 67
    commits behind, because that member's own `origin/main` ref was as stale as its
    `main`. A tell that reads a cached ref cannot detect that the cache is old. That
    is not a bug in the read-only tell — it is why a caller about to WRITE needs a
    different mode, and this is the test that separates them.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.remote = self.base / "remote.git"
        self.repo = self.base / "member"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(self.remote)],
                       check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.repo)],
                       check=True, capture_output=True)
        for k, v in (("user.email", "t@t.t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            self._g("config", k, v)
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
        self._g("add", "-A")
        self._g("commit", "-qm", "base")
        self._g("push", "-q", "-u", "origin", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def _g(self, *args, repo=None):
        proc = subprocess.run(["git", "-C", str(repo or self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode,
                         f"git {' '.join(args)}: {proc.stdout}{proc.stderr}")
        return proc.stdout.strip()

    def _advance_remote_unseen(self):
        """Move the remote WITHOUT letting this clone hear about it — the stale-cache
        condition. No fetch here, deliberately."""
        other = self.base / "other"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(other)],
                       check=True, capture_output=True)
        for k, v in (("user.email", "o@o.o"), ("user.name", "o"),
                     ("commit.gpgsign", "false")):
            self._g("config", k, v, repo=other)
        self._g("commit", "-q", "--allow-empty", "-m", "remote moved on", repo=other)
        self._g("push", "-q", repo=other)
        shutil.rmtree(other)

    def test_the_read_only_answer_is_blind_to_an_unseen_remote(self):
        """The premise, measured rather than assumed. If this ever stops being true the
        fetching mode is unnecessary, and the test below would be pinning nothing."""
        self._advance_remote_unseen()
        d = common.git_divergence(self.repo)
        self.assertTrue(d["readable"])
        self.assertEqual(0, d["behind"],
                         "the cached ref saw the remote move — premise broken")

    def test_the_fetching_mode_sees_it(self):
        self._advance_remote_unseen()
        d = common.git_divergence(self.repo, fetch=True)
        self.assertEqual("ok", d["fetch_state"])
        self.assertTrue(d["readable"])
        self.assertEqual(1, d["behind"], "fetch=True still read a stale cached ref")

    def test_divergence_is_true_at_depth_one(self):
        self._advance_remote_unseen()
        self._g("commit", "-q", "--allow-empty", "-m", "local")
        d = common.git_divergence(self.repo, fetch=True)
        self.assertTrue(d["diverged"])
        self.assertEqual((1, 1), (d["ahead"], d["behind"]))

    def test_no_upstream_is_unreadable_not_current(self):
        """THREE OUTCOMES, NEVER TWO. `readable=False` must not be reachable by a
        caller that only checked `diverged`, which is False here and means nothing."""
        plain = self.base / "plain"
        plain.mkdir()
        subprocess.run(["git", "-C", str(plain), "init", "-q", "-b", "main"],
                       check=True, capture_output=True)
        d = common.git_divergence(plain, fetch=True)
        self.assertFalse(d["readable"])
        self.assertIsNone(d["upstream"])
        self.assertEqual("no-upstream", d["fetch_state"])
        self.assertIsNotNone(d["reason"])

    def test_an_unreachable_remote_reports_failed_rather_than_hanging(self):
        """`git fetch` against a dead remote BLOCKS on DNS or a credential prompt
        rather than failing, and a block reads as "still working". One unreachable
        member must not stall a fleet-wide run."""
        self._g("remote", "set-url", "origin", str(self.base / "gone.git"))
        d = common.git_divergence(self.repo, fetch=True)
        self.assertEqual("failed", d["fetch_state"])

    def test_a_recent_fetch_on_record_skips_the_network(self):
        """The `or requires a recent fetch` half of the precondition: a fleet-wide run
        asking the same member twice pays one round trip, not two."""
        stamp = self.repo / common.FETCH_STAMP_REL
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"origin/main": {"at": 1_000_000, "tried": 1_000_000}}),
                         encoding="utf-8")
        self._advance_remote_unseen()
        d = common.git_divergence(self.repo, fetch=True, now=1_000_060)
        self.assertEqual("fresh", d["fetch_state"])
        self.assertEqual(0, d["behind"], "it fetched despite a fresh stamp")


class ComputeDriftPrecedenceTest(unittest.TestCase):
    """A mapped repo wins over the same id discovered by walking the roots."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        # `compute_drift` starts from `self_entry()`, which reads the MAIN checkout's
        # STATUS.md. An empty fixture main checkout keeps this test off the live store
        # (ADR-0148 D3) and leaves the precedence rule the only thing it measures.
        main = self.base / "main-checkout"
        main.mkdir()
        patcher = mock.patch.object(reconcile, "_MAIN_CHECKOUT", main)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mapped_overrides_walked(self):
        mapped_repo = self.base / "live"
        mapped_repo.mkdir()
        (mapped_repo / "STATUS.md").write_text(
            "---\nid: orbit\nversion: 3.5.1\n---\n", encoding="utf-8")
        walked = {"orbit": ({"id": "orbit", "version": "0.0.1"},
                              pathlib.Path("/stale/STATUS.md"))}
        with mock.patch.object(reconcile, "find_status_files",
                               return_value=(dict(walked), [])):
            found, _roster, broken, idless = reconcile.compute_drift(
                ["/whatever"], {"orbit": str(mapped_repo)})
        self.assertEqual(idless, [])
        self.assertEqual(found["orbit"][0]["version"], "3.5.1")  # mapped, not 0.0.1
        self.assertEqual(broken, {})


class LiveRoleDocVersionTest(unittest.TestCase):
    """WI-0090: the version now comes from the MEMBER's own role doc, located through
    the member's own `session.config.json` — never a federation-held mirror, which
    could only ever report the staleness of our own filing and printed that with the
    same label as the member's file being behind.

    The spelling tolerance is kept verbatim from the retired mirror reader: one member's
    backtick/`v`-wrapped form defeated the bare `v?` regex and left it
    'version-unreadable' forever."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = pathlib.Path(self._tmp.name)

    def _v(self, body, doc_name="arch.md", declare=True):
        (self.repo / doc_name).write_text(body, encoding="utf-8")
        if declare:
            (self.repo / "session.config.json").write_text(
                json.dumps({"role_doc": doc_name}), encoding="utf-8")
        return reconcile.live_roledoc_version(self.repo)

    def test_backtick_v_wrapped_parses(self):
        self.assertEqual(self._v("**Version**: `v0.1.9`\n"), "0.1.9")

    def test_bold_v_prefixed_parses(self):
        self.assertEqual(self._v("**Version:** v2.34.0\n"), "2.34.0")

    def test_plain_frontmatter_style_parses(self):
        self.assertEqual(self._v("version: 3.5.4\n"), "3.5.4")

    def test_no_version_line_is_none(self):
        self.assertIsNone(self._v("# role doc\nno version here\n"))

    def test_a_repo_with_no_session_config_says_so(self):
        """`declare-what-a-check-assumes`: 'this repo declares no config' is a
        different fact from 'its version disagrees', and must not print as drift."""
        self.assertEqual(self._v("**Version:** 1.0.0\n", declare=False), "<no-config>")

    def test_a_declared_role_doc_that_is_missing_says_so(self):
        (self.repo / "session.config.json").write_text(
            json.dumps({"role_doc": "nope.md"}), encoding="utf-8")
        self.assertEqual(reconcile.live_roledoc_version(self.repo), "<no-roledoc>")


class RosterTest(unittest.TestCase):
    """The roster replaced the mirror set as the answer to 'who should be here?'
    (WI-0090). Losing it would have made an unreachable member silently absent rather
    than UNLOCATED — a worse bug than the one being fixed.

    Patches `FED_ROOT`, not the main-checkout root: since WI-0246 the roster is read
    from the tree the script is running in, because `portfolio.md` is authored content
    any lane may write. `tests/test_reconcile_roster_root.py` pins that routing itself;
    these cover the parsing on top of it."""

    def _roster(self, text):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "portfolio.md").write_text(text, encoding="utf-8")
            with mock.patch.object(reconcile, "FED_ROOT", root):
                return reconcile.roster_ids()

    def test_system_ids_are_read_and_architect_ids_are_not(self):
        got = self._roster(
            "| System | Agent | Architect | System ID | Architect ID | Status |\n"
            "|---|---|---|---|---|---|\n"
            "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` | Active |\n"
            "| sample-svc | (none) | Sample-Svc Architect | `sample-svc` | `sample-svc-arch` | Active |\n")
        self.assertEqual(got, {"orbit", "sample-svc"})

    def test_the_federation_is_on_its_own_roster(self):
        """ADR-0003 — a participant on equal footing. Excluding ourselves is what let
        our own STATUS.md sit a minor behind the role doc unremarked for a week."""
        got = self._roster(
            "| System | Agent | Architect | System ID | Architect ID | Status |\n"
            "|---|---|---|---|---|---|\n"
            "| federation | (none) | Federation Architect | `federation` | "
            "`federation-arch` | Active |\n")
        self.assertEqual(got, {"federation"})

    def test_an_unreadable_portfolio_yields_an_empty_roster(self):
        """Fail-open, and the report says ROSTER NOT READ rather than implying that
        every member was located."""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(reconcile, "FED_ROOT", pathlib.Path(d)):
                self.assertEqual(reconcile.roster_ids(), set())


class MainRootsFallbackTest(unittest.TestCase):
    """WI-0175 — the full report and `--status` must read the same roots.

    `status_line()` has always read `reconcile-roots.local`; `main()` took argv only.
    So the hook printed a true "7 unlocated" and the command it told a human to run
    printed "15 unlocated" from a walk of nothing, with no line saying it had no roots.
    Two surfaces of one module disagreeing is worse than either being wrong alone.
    """

    def _roots_main_walked(self, argv):
        seen = []

        def fake_find(roots):
            seen.append(list(roots))
            return {}, []

        with mock.patch.object(sys, "argv", ["reconcile.py", *argv]), \
                mock.patch.object(reconcile, "read_roots_config",
                                  lambda warn=True: ["/configured/root"]), \
                mock.patch.object(reconcile, "read_repo_paths", lambda: {}), \
                mock.patch.object(reconcile, "find_status_files", fake_find), \
                mock.patch.object(reconcile, "self_entry", lambda: ({}, [])), \
                mock.patch.object(reconcile, "roster_ids", lambda: set()), \
                mock.patch("builtins.print", lambda *a, **k: None):
            reconcile.main()
        return seen[0]

    def test_bare_invocation_falls_back_to_the_machine_local_config(self):
        self.assertEqual(self._roots_main_walked([]), ["/configured/root"])

    def test_argv_still_wins_over_the_config(self):
        self.assertEqual(self._roots_main_walked(["/explicit"]), ["/explicit"])


if __name__ == "__main__":
    unittest.main()
