"""`poga` as FLEET SUBSTRATE — the session-98 promotion of the ADR-0060 lane
lifecycle from a federation privilege to a member capability.

Before this, every member could already *coordinate* between lanes (v1.4.0's
cross-lane-coordination store) while having no way to *launch* one: the `poga`
wrapper was federation-only and the WorktreeRemove teardown hook lived in the
federation's own settings_extras. Three things had to become true, and each is a
test here:

  1. poga resolves the repo it drives from the CALLER'S CWD, not from where the
     script physically lives. This is the whole reason one byte-identical file can
     be substrate: the same bytes must correctly drive whichever member you are
     standing in. (Script-relative was right when there was one repo; shipping it to
     N members makes script-relative actively WRONG — it would silently drive the
     federation checkout from inside a member.)
  2. The federation-only verbs degrade honestly on a member copy — a named refusal,
     not a python "no such file" traceback for a file the member was never sent.
  3. The floor carries the teardown hook, so a member gets it without opting in.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import harness_fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent
POGA = ROOT / "poga"
FLOOR = ROOT / "standard-settings.json"

_spec = importlib.util.spec_from_file_location("standard_check", ROOT / "standard_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _scratch_repo(path: pathlib.Path) -> pathlib.Path:
    """A minimal real git repo with one commit — enough for branch/worktree probes."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "seed.txt").write_text("x", encoding="utf-8")
    _git(path, "add", "seed.txt")
    _git(path, "commit", "-m", "seed")
    return path


def _store_repo(path: pathlib.Path) -> pathlib.Path:
    """A fixture checkout carrying the real harness and a one-row store of each kind.

    `poga work` / `poga ops` anchor on the main checkout of whatever repo they stand in,
    so running them from the REAL checkout read the operator's live backlog — thousands
    of files, and a test outcome that bookkeeping lands can change without touching code
    (ADR-0148 D3 store guard). What these classes pin is the ROUTING, which a fixture
    exercises identically."""
    harness_fixture.install_harness(path)
    shutil.copyfile(POGA, path / "poga")
    (path / "role.md").write_text("role\n", encoding="utf-8")
    (path / "session.config.json").write_text(json.dumps({
        "architect_name": "Architect", "architect_id": "fixture-arch",
        "user_name": "operator", "role_doc": "role.md", "handoff": "handoff.md",
        "timezone": "UTC", "machine_map": {"anything": "TestBox"},
    }, indent=2), encoding="utf-8")
    (path / "work-items").mkdir()
    (path / "work-items" / "WI-0001-a-fixture-item.md").write_text(
        "# WI-0001: A fixture item\n\n- status: open\n- section: next\n"
        "- blocked-by: \n- group: \n- source: \n- impact: fix\n- version: \n\n"
        "Body.\n", encoding="utf-8")
    (path / "ops-items").mkdir()
    (path / "ops-items" / "OPS-0001-a-fixture-obligation.md").write_text(
        "# OPS-0001: A fixture obligation\n\n- status: open\n- cadence: quarterly\n"
        "- last-completed: 2026-07-21\n- last-result: pass\n- due: 2026-10-21\n"
        "- group: \n- source: \n\nBody.\n", encoding="utf-8")
    (path / ".gitignore").write_text(".claude/worktrees/\n.session-state/\n",
                                     encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "seed")
    return path


def _store_lane(repo: pathlib.Path) -> pathlib.Path:
    """A linked worktree of the fixture, where the real test used whatever live lane
    happened to exist (and skipped when none did)."""
    lane = repo / ".claude" / "worktrees" / "poga-1"
    _git(repo, "worktree", "add", "-q", "-b", "worktree-poga-1", str(lane), "main")
    return lane


def _launch_env(home: pathlib.Path, **extra) -> dict:
    """The caller's environment with no route to the operator's board.

    A bare `poga` launch evals `POGA_BOARD_CMD` — inherited, or sourced from
    `$HOME/.config/poga/poga.local` — which starts the REAL board against the live
    checkouts (it reads their STATUS.md and store: ADR-0148 D3 store guard). So the
    variable is dropped AND `HOME` is the fixture's own tmpdir."""
    env = {k: v for k, v in os.environ.items() if k != "POGA_BOARD_CMD"}
    env["HOME"] = str(home)
    env.update(extra)
    return env


class _StoreRepoCase(unittest.TestCase):
    """Builds one fixture store per class; every verb runs with cwd inside it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = _store_repo(pathlib.Path(cls._tmp.name) / "repo")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class PogaResolvesRepoFromCwdTest(unittest.TestCase):
    """Property 1 — the load-bearing change that makes poga shippable."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.member = _scratch_repo(pathlib.Path(self._tmp.name) / "member")

    def tearDown(self):
        self._tmp.cleanup()

    def _lanes(self, cwd):
        return subprocess.run(["bash", str(POGA), "lanes"],
                              capture_output=True, text=True, cwd=str(cwd))

    def test_lanes_reports_the_cwd_repo_not_the_federation(self):
        # A lane branch that exists ONLY in the scratch member. If poga were still
        # script-relative it would report the federation's lanes and never see this.
        _git(self.member, "branch", "worktree-poga-9")
        proc = self._lanes(self.member)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # WI-0157 moved this view's columns: the branch ref is no longer printed, since
        # the SLOT is what a reader types at `poga resume`. The marker moves with it —
        # what is under test is still that poga read the CWD repo's refs at all.
        self.assertIn("poga-9", proc.stdout)

    def test_federation_lanes_do_not_leak_into_a_member_view(self):
        """The converse, and the one that would actually bite: standing in a member,
        poga must NOT report branches that exist only in the federation checkout."""
        fed_branches = _git(ROOT, "for-each-ref", "--format=%(refname:short)",
                            "refs/heads/worktree-*").stdout.split()
        # The subject of this test is branches that DO exist leaking across; with none in
        # the real checkout the loop below runs zero times and the test passes having
        # checked nothing. State the precondition rather than let an empty machine certify
        # the property (`declare-what-a-check-assumes`, WI-0250) — an ambient read whose
        # failure mode is a silent pass, not a red test.
        if not fed_branches:
            self.skipTest("no worktree-* branches in the federation checkout — nothing "
                          "could leak, so this asserts nothing right now")
        proc = self._lanes(self.member)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # BOTH SPELLINGS, and the second one is the whole point of this edit. WI-0157
        # stopped this view printing the branch ref, which would have left the loop below
        # asserting the absence of a string the surface can no longer emit under any
        # circumstances — a test that cannot fail, certifying the property it was written
        # to defend ([`declare-what-a-check-assumes`], and the same silent-pass shape the
        # skip above already guards against one level up).
        for b in fed_branches:
            self.assertNotIn(b, proc.stdout,
                             f"federation lane {b} leaked into the member's lane view")
            slot = b.replace("worktree-", "")
            self.assertNotIn(slot, proc.stdout,
                             f"federation lane {b} leaked in as slot {slot}")

    def test_outside_a_repo_it_refuses_by_name_rather_than_guessing(self):
        outside = pathlib.Path(self._tmp.name) / "not-a-repo"
        outside.mkdir()
        proc = self._lanes(outside)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a git repository", proc.stderr)
        # Names the fix, not just the failure.
        self.assertIn("standing in", proc.stderr)


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class PogaWorkVerbTest(_StoreRepoCase):
    """`poga work` — the front door onto the work-item store (session ~103).

    The reason it routes through poga rather than being invoked as `session.py wi-*`:
    every worktree carries its OWN `session.py`, so running the store's CLI from a lane
    can drive today's data with whatever tool that lane was cut with. `require_repo`
    anchors on the MAIN checkout, which makes "which copy" answerable once. These pin
    that routing and the dispatch surface.
    """

    def _work(self, *args, cwd=None):
        return subprocess.run(["bash", str(POGA), "work", *args],
                              capture_output=True, text=True, cwd=str(cwd or self.repo))

    def test_bare_work_lists(self):
        proc = self._work()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Work items", proc.stdout)

    def test_show_reaches_the_stores_own_command(self):
        proc = self._work("show", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("WI-0001", proc.stdout)
        self.assertIn("status:", proc.stdout)

    def test_check_runs_the_validator(self):
        """Pins the ROUTING — that `check` reaches the store's validator — and
        deliberately NOT that the live store happens to be sound.

        Asserting `sound` here made a CODE gate a function of mutable production DATA,
        and it wedged the entire fleet (session ~129). This test runs inside the merge
        gate, and `poga work` anchors on the MAIN checkout — so the moment one number in
        main's store went bad, every lane's land failed on it, in a checkout none of them
        could repair from the inside. WI-0092 was drawn in ~125, never used, and when its
        allocator hold expired it took the land path down for the whole machine: three
        lanes holding real, finished work sat unmerged, and each new session reported them
        as stranded (session ~129).

        A gate must be a function of the thing it gates. Store HEALTH still has two
        surfaces that are about data and should be: the `wi-check --status` startup line
        and `poga work check` run directly. What the gate needs to know is that the wiring
        works, which is what this asserts — the validator was reached and rendered its
        verdict, whichever verdict the data earns today.
        """
        proc = self._work("check")
        self.assertIn(proc.returncode, (0, 1),
                      f"the validator ran and returned a verdict: {proc.stderr}")
        self.assertIn("wi-check:", proc.stdout,
                      "routed to the store's validator, not swallowed")

    def test_an_unknown_verb_is_named_not_guessed(self):
        proc = self._work("frobnicate")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown verb", proc.stderr)

    def test_help_lists_the_surface(self):
        proc = self._work("--help")
        self.assertEqual(proc.returncode, 0)
        for verb in ("show", "edit", "move", "check"):
            self.assertIn(verb, proc.stderr + proc.stdout)

    def test_from_a_lane_it_resolves_to_the_main_checkout(self):
        """The whole point. Invoked inside a lane, `poga work` must drive the MAIN
        checkout's store and tool, not the lane's own possibly-stale copy."""
        lane = _store_lane(self.repo)
        proc = self._work("check", cwd=lane)
        # ROUTING, never the store's health — the completion of the removal made one
        # function above (WI-0271 item 2). `require_repo` resolves ROOT from the
        # git-common-dir, so this `check` reads MAIN's work-item store from inside a lane.
        # This used to glob the operator's REAL lanes and so read the live store (and
        # skipped wherever no lane existed — an accident of gitignore standing in for a
        # decision); it now runs from a lane of the fixture, every time.
        self.assertIn(proc.returncode, (0, 1),
                      f"the validator ran and returned a verdict: {proc.stderr}")
        self.assertIn("wi-check:", proc.stdout,
                      "routed to the MAIN checkout's validator from inside a lane")


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class PogaOpsVerbTest(_StoreRepoCase):
    """`poga ops` — the ops namespace's front door (WI-0125).

    `poga work` has anchored the wi-* verbs on the MAIN checkout since ADR-0073. The ops
    verbs (ADR-0076) never got the same treatment, so the only way to reach them was
    `python3 session.py ops-new` — and from a lane that writes the LANE's frozen store,
    which no other checkout ever reads. It cost OPS-0004: drawn in a lane, invisible to
    `poga work list`, deleted, and the number permanently burned because main's allocator
    had already moved past it. These pin the routing and the anchor, not the store's data.
    """

    def _ops(self, *args, cwd=None):
        return subprocess.run(["bash", str(POGA), "ops", *args],
                              capture_output=True, text=True, cwd=str(cwd or self.repo))

    def test_bare_ops_renders_the_shared_list(self):
        """Obligations render WITH the items — one board, one chart, one startup view.
        A second list would be the duplication ADR-0076 deliberately avoided."""
        proc = self._ops()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Work items", proc.stdout)

    def test_show_reaches_the_stores_own_command(self):
        proc = self._ops("show", "OPS-0001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OPS-0001", proc.stdout)

    def test_an_unknown_verb_is_named_not_guessed(self):
        proc = self._ops("frobnicate")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown verb", proc.stderr)

    def test_help_lists_the_surface(self):
        proc = self._ops("--help")
        self.assertEqual(proc.returncode, 0)
        text = proc.stderr + proc.stdout
        for verb in ("new", "ran", "status", "show", "commit"):
            self.assertIn(verb, text)

    def test_the_verb_is_reachable_at_all(self):
        """The wiring, separately from any one verb: an unrouted `ops` would fall through
        to the launcher and open a LANE with "ops" as its prompt (WI-0112's shape), which
        exits 0 and looks like success."""
        proc = self._ops("--help")
        self.assertIn("obligations", proc.stderr + proc.stdout,
                      "`ops` reached its own help, not the launcher fallthrough")

    def test_from_a_lane_it_resolves_to_the_main_checkout(self):
        """The whole point of the front door. `show` is read-only, so this asserts the
        anchoring without writing to the operator's real store."""
        lane = _store_lane(self.repo)
        proc = self._ops("show", "OPS-0001", cwd=lane)
        # Same narrowing as its `poga work` sibling above: assert the verb ROUTED to the
        # main checkout's store. Runs from a lane of the fixture, never a live lane.
        self.assertIn(proc.returncode, (0, 1),
                      f"the verb ran and returned a verdict: {proc.stderr}")
        self.assertNotIn("not a git repository", proc.stderr,
                         "resolved to the main checkout from inside a lane")


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class PogaFederationOnlyVerbsTest(unittest.TestCase):
    """Property 2 — bootstrap/restore need poga_cli.py, which members are not sent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # A member-shaped copy: poga present, poga_cli.py absent — exactly what
        # push-substrate delivers.
        self.member = _scratch_repo(pathlib.Path(self._tmp.name) / "member")
        shutil.copyfile(POGA, self.member / "poga")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bootstrap_on_a_member_copy_refuses_by_name(self):
        proc = subprocess.run(["bash", str(self.member / "poga"), "bootstrap", "--plan"],
                              capture_output=True, text=True, cwd=str(self.member))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("federation-only verb", proc.stderr)
        # The refusal must say what DOES work here, and where to go for the rest.
        self.assertIn("session", proc.stderr)
        self.assertIn("principles-of-good-architects", proc.stderr)
        # Never a raw interpreter error for a file the member never received.
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("No such file or directory", proc.stderr)

    def test_the_universal_verbs_still_work_from_that_same_member_copy(self):
        """The refusal must be scoped to the lifecycle verbs — not a member copy that
        is broadly crippled."""
        proc = subprocess.run(["bash", str(self.member / "poga"), "lanes"],
                              capture_output=True, text=True, cwd=str(self.member))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("poga lanes", proc.stdout)


class SettingsFloorCarriesTeardownTest(unittest.TestCase):
    """Property 3 — the hook is FLOOR, so a member inherits it without opting in."""

    def test_floor_wires_worktree_remove(self):
        floor = json.loads(FLOOR.read_text(encoding="utf-8"))
        entries = floor.get("hooks", {}).get("WorktreeRemove") or []
        cmds = [h.get("command", "") for e in entries for h in (e.get("hooks") or [])]
        self.assertTrue(any("worktree-remove" in c for c in cmds),
                        "the lane teardown hook must live in the shared floor, not in "
                        "any one system's settings_extras")

    def test_federation_no_longer_declares_it_as_a_local_extra(self):
        """Floor-promoted means REMOVED from the federation's extras — a hook declared
        in both would be emitted twice into the generated settings."""
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        extras_hooks = (cfg.get("settings_extras") or {}).get("hooks") or {}
        self.assertNotIn("WorktreeRemove", extras_hooks)

    def test_generated_settings_carry_it_exactly_once(self):
        data = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        entries = data.get("hooks", {}).get("WorktreeRemove") or []
        cmds = [h.get("command", "") for e in entries for h in (e.get("hooks") or [])]
        self.assertEqual(len([c for c in cmds if "worktree-remove" in c]), 1)


class WorktreeLanesCapabilityTest(unittest.TestCase):
    """The v1.5.0 detector. Both halves required — a member with one and not the other
    is precisely the half-state this capability exists to make visible."""

    HOOKED = ('{"hooks": {"WorktreeRemove": '
              '[{"hooks": [{"command": "session.py worktree-remove"}]}]}}')

    def _repo(self, tmp, poga=True, settings=HOOKED):
        repo = pathlib.Path(tmp)
        if poga:
            (repo / "poga").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "settings.json").write_text(settings, encoding="utf-8")
        return repo

    def test_both_halves_present(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(sc.d_worktree_lanes(self._repo(d), {}))

    def test_launcher_without_teardown_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, settings='{"hooks": {}}')
            self.assertFalse(sc.d_worktree_lanes(repo, {}))

    def test_teardown_without_launcher_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(sc.d_worktree_lanes(self._repo(d, poga=False), {}))

    def test_it_parses_rather_than_greps(self):
        """`worktree-remove` appearing anywhere in the file must not count — only a
        real WorktreeRemove hook entry does. Same discipline as the heartbeat
        detector, where the Stop hook runs an identical command string."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, settings='{"note": "session.py worktree-remove", '
                                          '"hooks": {"Stop": [{"hooks": '
                                          '[{"command": "session.py worktree-remove"}]}]}}')
            self.assertFalse(sc.d_worktree_lanes(repo, {}))

    def test_it_is_in_the_manifest_at_v150_and_scoped_to_the_claude_hook_binding(self):
        self.assertIn("worktree-lanes", sc.CAPABILITIES)
        self.assertEqual(sc.CAPABILITIES["worktree-lanes"][2], "claude-hook")
        self.assertIn("worktree-lanes", sc.required_set("1.5.0"))
        self.assertNotIn("worktree-lanes", sc.required_set("1.4.0"))


class ShippedAsSubstrateTest(unittest.TestCase):
    """poga must actually be in the push set and the bootstrap seed set — the
    capability is unreachable otherwise."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "push_substrate", ROOT / "curate" / "push-substrate.py")
        self.ps = importlib.util.module_from_spec(spec)
        import sys
        sys.path.insert(0, str(ROOT / "curate"))
        spec.loader.exec_module(self.ps)

    def test_poga_is_pushed_byte_identical_and_marked_executable(self):
        self.assertIn("poga", self.ps.BYTE_IDENTICAL)
        self.assertIn("poga", self.ps.EXECUTABLE)

    def test_the_federation_source_copy_is_itself_executable(self):
        """The push copies bytes and re-applies the bit, but a non-executable source
        would still be the wrong thing to ship and to run in place."""
        self.assertTrue(os.access(POGA, os.X_OK))

    def test_bootstrap_seeds_it_executable(self):
        spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "bootstrap.py")
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        self.assertIn("poga", bs.SHARED_SUBSTRATE)
        with tempfile.TemporaryDirectory() as d:
            dest = pathlib.Path(d)
            bs.seed_shared_substrate(dest)
            seeded = dest / "poga"
            self.assertTrue(seeded.is_file())
            self.assertTrue(os.access(seeded, os.X_OK),
                            "a seeded member would carry a present-but-unrunnable poga")
            self.assertEqual(seeded.read_bytes(), POGA.read_bytes())


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class SessionPromptInjectionTest(unittest.TestCase):
    """`poga` opens every lane on the standard session prompt, so a lane starts by
    running its startup and identifying itself — rather than on an empty box the
    operator has to prime by hand. It deliberately does NOT ask for the work-item
    list: a board (`POGA_BOARD_CMD`) renders it, and the session-start protocol already obliges
    a recommendation.

    Exercised through the REAL script against a stub `claude` that prints its argv:
    the thing that matters is the command line poga actually exec's, and a test that
    re-implemented the arg logic would only confirm its own premise
    (`verify-in-the-created-configuration`)."""

    PROMPT = "run your startup and tell me who you are"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self.repo = _scratch_repo(base / "repo")
        shutil.copyfile(POGA, self.repo / "poga")
        # Stub claude: echo argv, one per line, so assertions read positionally.
        self.bin = base / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text('#!/usr/bin/env bash\nfor a in "$@"; do echo "$a"; done\n',
                        encoding="utf-8")
        stub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def _argv(self, *args):
        env = _launch_env(self.bin.parent, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        proc = subprocess.run(["bash", str(self.repo / "poga"), *args],
                              capture_output=True, text=True,
                              cwd=str(self.repo), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.splitlines()

    def test_a_bare_poga_opens_on_the_session_prompt(self):
        argv = self._argv()
        self.assertEqual(argv[-1], self.PROMPT)
        # It rides as the trailing positional, after the lane selection.
        self.assertEqual(argv[:2], ["--worktree", "poga-1"])

    def test_an_operator_prompt_wins_and_is_not_doubled(self):
        argv = self._argv("fix the flaky test")
        self.assertEqual(argv[-1], "fix the flaky test")
        self.assertNotIn(self.PROMPT, argv,
                         "two positionals would reach claude as a mangled command line")

    def test_a_value_taking_flag_is_not_mistaken_for_a_prompt(self):
        """`poga --model sonnet` must still inject — 'sonnet' is a flag's value, not
        the operator's prompt. Getting this wrong silently drops the prompt for the
        one shape most likely to be typed alongside it."""
        argv = self._argv("--model", "sonnet")
        self.assertEqual(argv[-1], self.PROMPT)
        self.assertEqual(argv[-3:-1], ["--model", "sonnet"])

    def test_a_value_taking_flag_plus_an_operator_prompt_still_defers(self):
        argv = self._argv("--model", "sonnet", "fix the thing")
        self.assertEqual(argv[-1], "fix the thing")
        self.assertNotIn(self.PROMPT, argv)

    def test_a_boolean_flag_alone_still_injects(self):
        argv = self._argv("-c")
        self.assertEqual(argv[-1], self.PROMPT)

    def test_the_prompt_is_the_one_in_the_script(self):
        """Pins the shipped text to this test's copy, both directions — a prose-only
        default is not a default (`verify-in-the-created-configuration`)."""
        line = [ln for ln in POGA.read_text(encoding="utf-8").splitlines()
                if ln.startswith("POGA_SESSION_PROMPT=")]
        self.assertEqual(len(line), 1, "expected exactly one session-prompt definition")
        self.assertEqual(line[0], f'POGA_SESSION_PROMPT="{self.PROMPT}"')


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "bash+git required")
class PogaExportsTheResolvedRuntimeTest(unittest.TestCase):
    """WI-0061 cause 1 — the runtime the launcher resolved never reached the session.

    `runtime-resolve` PRINTS `POGA_RUNTIME_ID=` for poga to parse, and poga parsed it into
    a plain local. Nothing downstream could see it, so `session.py` had no way to learn
    which agent it was running as: `_coord_holder` fell back to the `claude-code` default
    and every codex record asserted the one fact it could not know. The item's own fix
    sketch said poga "already exports POGA_RUNTIME_ID at launch" — it did not, and this
    test is why that gets checked rather than believed ([`verify-everything`]).

    Exercised through the REAL script against a stub runtime that prints its environment,
    because the claim under test is about the process poga exec's — a test that read the
    script's text would confirm the line exists, not that it reaches the agent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self.repo = _scratch_repo(base / "repo")
        shutil.copyfile(POGA, self.repo / "poga")
        self.bin = base / "bin"
        self.bin.mkdir()
        stub = self.bin / "claude"
        stub.write_text('#!/usr/bin/env bash\n'
                        'echo "POGA_RUNTIME_ID=${POGA_RUNTIME_ID-<unset>}"\n',
                        encoding="utf-8")
        stub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_launched_session_can_see_which_runtime_it_is(self):
        env = _launch_env(self.bin.parent, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        env.pop("POGA_RUNTIME_ID", None)      # inheriting it would prove nothing
        proc = subprocess.run(["bash", str(self.repo / "poga")],
                              capture_output=True, text=True,
                              cwd=str(self.repo), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("POGA_RUNTIME_ID=claude-code", proc.stdout,
                      "the resolved runtime must be EXPORTED, not merely parsed into a "
                      "shell local the exec'd process cannot read")

    def test_every_runtime_exec_is_downstream_of_an_export(self):
        """The structural half: a fifth launch path added above the export would
        reintroduce the defect for that path only, and the behavioural test above covers
        just one of them."""
        lines = POGA.read_text(encoding="utf-8").splitlines()
        exports = [i for i, ln in enumerate(lines)
                   if ln.strip().startswith("export POGA_RUNTIME_ID=")]
        execs = [i for i, ln in enumerate(lines) if 'exec "$rt_cmd"' in ln]
        self.assertTrue(exports, "no export at all")
        self.assertTrue(execs, "no runtime exec found — did the launch path move?")
        for e in execs:
            self.assertTrue(any(x < e for x in exports),
                            f"the runtime exec on line {e + 1} runs with no "
                            f"POGA_RUNTIME_ID exported before it")


class PogaAntigravityLaunchArgvTest(unittest.TestCase):
    """WI-0096: pin the argv that poga builds for an antigravity launch.

    agy REFUSES bare trailing positional prompt arguments at parse time (prompts are
    read only from -p, -i, or stdin). The runtime registry declares argv_shape: '-i'
    for antigravity, and poga uses that declared shape when appending the prompt."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self.repo = _scratch_repo(base / "repo")
        shutil.copyfile(POGA, self.repo / "poga")
        (self.repo / "poga").chmod(0o755)
        shutil.copyfile(ROOT / "session.py", self.repo / "session.py")
        (self.repo / "sessionlib").symlink_to(ROOT / "sessionlib")
        self.bin = base / "bin"
        self.bin.mkdir()
        stub = self.bin / "agy"
        stub.write_text('#!/usr/bin/env bash\n'
                        'for a in "$@"; do\n'
                        '  echo "ARG: $a"\n'
                        'done\n',
                        encoding="utf-8")
        stub.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def test_antigravity_launch_uses_interactive_flag_shape(self):
        path_env = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        env = _launch_env(self.bin.parent, PATH=path_env, POGA_SKIP_PREFLIGHT="1")
        env.pop("POGA_RUNTIME_ID", None)
        proc = subprocess.run(["bash", str(self.repo / "poga"), "-r", "ag"],
                              capture_output=True, text=True,
                              cwd=str(self.repo), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.startswith("ARG: ")]
        args = [ln[len("ARG: "):] for ln in lines]
        self.assertIn("-i", args,
                      f"expected '-i' flag in argv passed to agy; got bare/unshaped argv: {args}")
        i_idx = args.index("-i")
        self.assertLess(i_idx, len(args) - 1, "-i must precede the prompt argument")
        self.assertIn("run your startup and tell me who you are", args[i_idx + 1])


if __name__ == "__main__":
    unittest.main()

