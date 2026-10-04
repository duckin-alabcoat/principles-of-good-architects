"""Core first: a remote is opt-in on every path (WI-0452), and one person on one machine
can start from nothing with `poga init` (WI-0453).

Two claims, each driven through the real entry points:

1. **No step of first use creates a remote, or needs `gh`, unless asked.** An explicit
   `repo_url` is honoured before any GitHub owner is looked up; a spec that names no
   remote and asks for none builds a local-only repo and never runs `gh`; the remote is
   created only under `--create-remote` (or `"create_remote": true`).

2. **`poga init` makes a local federation, and a lesson recorded in one project reaches
   another through it** — with no GitHub, no second host, no scheduler, board or broker,
   and no pre-existing profile. The lesson travels by the existing mechanisms: the
   producer file (`architect-learnings.md`), the fleet's own entry parser, and the
   receipt-ritual inbox that `session.py start` reads.

`gh` is shadowed on PATH by a stub that RECORDS every call and fails, so "never called"
is an observation, not an inference. HOME and the federation location are temp dirs, so
nothing here reads or writes the operator's own machine config.

stdlib unittest: python3 -m unittest tests.test_local_init
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bootstrap  # noqa: E402
import poga_cli  # noqa: E402

lf = poga_cli   # the local-federation half of the CLI (WI-0453)

GIT = shutil.which("git")

LESSON = """
## 2026-09-28 · scope: architect-general · Check the exit code, not the tail

Piping a test run to `tail` reports tail's exit code, so a red run reads green.

## 2026-09-28 · scope: {sid}-specific · The ledger is append-only

Only this project has a ledger; this one stays home.
"""


class Sandbox:
    """A temp HOME, a temp federation location, and a `gh` that records and fails."""

    def __init__(self, case: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.fed = self.root / "federation"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.gh_log = self.root / "gh-calls.log"
        gh = self.bin / "gh"
        gh.write_text(f'#!/bin/sh\necho "gh $*" >> "{self.gh_log}"\nexit 1\n',
                      encoding="utf-8")
        gh.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "POGA_"))}
        self.env.update(HOME=str(self.home), POGA_FEDERATION_HOME=str(self.fed),
                        GIT_CONFIG_NOSYSTEM="1",
                        # No background git (tests/test_runner_channel.py
                        # _NO_BACKGROUND_GIT): an install now commits twice, and a
                        # detached gc racing the temp dir's rmtree failed the teardown.
                        GIT_CONFIG_COUNT="3",
                        GIT_CONFIG_KEY_0="gc.auto", GIT_CONFIG_VALUE_0="0",
                        GIT_CONFIG_KEY_1="maintenance.auto", GIT_CONFIG_VALUE_1="false",
                        GIT_CONFIG_KEY_2="gc.autoDetach", GIT_CONFIG_VALUE_2="false",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")

    def project(self, name: str) -> pathlib.Path:
        p = self.root / "projects" / name
        p.mkdir(parents=True)
        return p

    def gh_calls(self) -> str:
        return self.gh_log.read_text(encoding="utf-8") if self.gh_log.exists() else ""

    def cli(self, *args, cwd=None, stdin=subprocess.DEVNULL):
        env = dict(self.env)
        if cwd is not None:
            env["POGA_INVOKED_FROM"] = str(cwd)
        return subprocess.run([sys.executable, str(ROOT / "poga_cli.py"), *args],
                              cwd=str(ROOT), env=env, stdin=stdin,
                              capture_output=True, text=True)


def _porcelain(repo: pathlib.Path, *extra) -> list:
    out = subprocess.run([GIT, "-C", str(repo), "status", "--porcelain", *extra],
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def _remotes(repo: pathlib.Path) -> str:
    return subprocess.run([GIT, "-C", str(repo), "remote"], capture_output=True,
                          text=True).stdout.strip()


# ---------------------------------------------------------------------------
# WI-0452 — remote creation is opt-in, and a local-only system never needs gh
# ---------------------------------------------------------------------------


class DeriveRepoUrlTest(unittest.TestCase):
    IDS = {"system_id": "widget", "architect_id": "widget-arch",
           "architect_name": "Widget Architect"}

    def _no_gh(self):
        return mock.patch.object(bootstrap, "detect_gh_account",
                                 side_effect=AssertionError("gh must not be asked"))

    def test_an_explicit_url_is_honoured_before_any_owner_is_resolved(self):
        with self._no_gh():
            url = poga_cli.derive_repo_url({"repo_url": "https://example.invalid/x/y.git"},
                                           self.IDS)
        self.assertEqual(url, "https://example.invalid/x/y.git")

    def test_an_explicit_owner_needs_no_gh(self):
        with self._no_gh():
            url = poga_cli.derive_repo_url({"repo_owner": "someone"}, self.IDS)
        self.assertEqual(url, "https://github.com/someone/widget.git")

    def test_nothing_named_and_no_remote_wanted_is_local_only(self):
        with self._no_gh():
            self.assertEqual(poga_cli.derive_repo_url({}, self.IDS, need=False), "")

    def test_gh_is_asked_only_when_a_remote_is_wanted_and_nothing_names_one(self):
        with mock.patch.object(bootstrap, "detect_gh_account", return_value="acct") as gh:
            url = poga_cli.derive_repo_url({}, self.IDS, need=True)
        gh.assert_called_once()
        self.assertEqual(url, "https://github.com/acct/widget.git")

    def test_remote_is_wanted_only_when_asked(self):
        self.assertFalse(poga_cli.wants_remote({}))
        self.assertFalse(poga_cli.wants_remote({"create_remote": "yes"}))
        self.assertTrue(poga_cli.wants_remote({}, flag=True))
        self.assertTrue(poga_cli.wants_remote({"create_remote": True}))


class LocalOnlyBootstrapTest(unittest.TestCase):
    """`poga bootstrap` in a brand-new folder, with a spec that names no remote. This
    exact case used to create a private GitHub repo unasked (`brand_new or ...`)."""

    def setUp(self):
        self.sb = Sandbox(self)
        self.sid = f"local-only-{os.getpid()}"
        self.target = self.sb.project(self.sid)
        # adopt files a roster request into THIS checkout's federation inbox; remove ours.
        brief = (ROOT / "proposed-edits" / "federation-arch" / "pending" /
                 f"{date.today():%Y-%m-%d}-register-{self.sid}.md")
        self.addCleanup(lambda: brief.unlink() if brief.exists() else None)
        self.spec = {"system_name": self.sid, "mission_prose": "Keep a list.",
                     "user_name": "Test Operator"}
        (self.target / "bootstrap-spec.json").write_text(json.dumps(self.spec),
                                                         encoding="utf-8")
        self.sb.env.update(GIT_AUTHOR_NAME="T", GIT_COMMITTER_NAME="T",
                           GIT_AUTHOR_EMAIL="t@example.invalid",
                           GIT_COMMITTER_EMAIL="t@example.invalid")

    def test_no_url_and_no_flag_builds_locally_and_never_runs_gh(self):
        proc = self.sb.cli("bootstrap", cwd=self.target)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sb.gh_calls(), "", "a local-only bootstrap ran gh")
        self.assertEqual(_remotes(self.target), "", "a remote appeared unasked")
        self.assertTrue((self.target / "session.py").is_file())
        # No operator profile existed anywhere: a blank one is seeded, not a refusal.
        self.assertTrue((self.target / "users" / "test-operator" / "profile.md").is_file())
        role = (self.target / f"{self.sid}-arch.md").read_text(encoding="utf-8")
        self.assertIn("local only — no remote yet", role)
        self.assertIn("LEFT AS-IS, local only", proc.stdout)

    def test_an_absent_target_is_made_and_built_locally(self):
        shutil.rmtree(self.target)
        spec_path = self.sb.root / "spec.json"
        spec_path.write_text(json.dumps({**self.spec,
                                         "install_dir": str(self.target)}), encoding="utf-8")
        proc = self.sb.cli("bootstrap", "--spec", str(spec_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sb.gh_calls(), "")
        self.assertEqual(_remotes(self.target), "")
        self.assertTrue((self.target / "session.py").is_file())

    def test_the_remote_is_created_only_with_the_flag(self):
        without = self.sb.cli("bootstrap", "--plan", cwd=self.target)
        self.assertIn("LEFT AS-IS", without.stdout)
        spec = dict(self.spec, repo_owner="someone")
        (self.target / "bootstrap-spec.json").write_text(json.dumps(spec), encoding="utf-8")
        with_flag = self.sb.cli("bootstrap", "--plan", "--create-remote", cwd=self.target)
        self.assertEqual(with_flag.returncode, 0, with_flag.stdout + with_flag.stderr)
        self.assertIn(f"will create someone/{self.sid}", with_flag.stdout)
        self.assertEqual(self.sb.gh_calls(), "", "the owner was named; gh had nothing to say")

    def test_a_named_remote_that_does_not_exist_is_not_created_unasked(self):
        """The pipeline path: the spec names a remote, the target is absent. Before
        WI-0452 `phase_acquire` ran `gh repo create` here without asking."""
        shutil.rmtree(self.target)
        spec_path = self.sb.root / "spec.json"
        spec_path.write_text(json.dumps({
            **self.spec, "install_dir": str(self.target),
            "repo_url": (self.sb.root / "no-such-remote.git").as_uri()}), encoding="utf-8")
        proc = self.sb.cli("bootstrap", "--spec", str(spec_path))
        self.assertEqual(proc.returncode, poga_cli.EXIT_USAGE, proc.stdout + proc.stderr)
        self.assertIn("nothing creates a remote unless asked", proc.stdout)
        self.assertIn("--create-remote", proc.stdout)
        self.assertEqual(self.sb.gh_calls(), "")
        self.assertFalse(self.target.exists())


class RenderWithoutAFederationTest(unittest.TestCase):
    """The time zone and machine map no longer have to come from a federation config."""

    def test_render_needs_no_federation_config(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bootstrap, "FED_CONFIG", pathlib.Path(d) / "absent.json"), \
                 mock.patch.object(bootstrap, "detect_gh_account",
                                   side_effect=AssertionError("gh must not be asked")):
                spec = bootstrap.with_defaults({"system_name": "Widget", "mission_prose": "x",
                                                "user_name": "Test Operator"})
                scalars, _, _, _ = bootstrap.render_context(
                    spec, bootstrap.derive_ids("Widget"), pathlib.Path(d))
        self.assertEqual(scalars["TIMEZONE_LONG"], bootstrap.detect_timezone())
        self.assertEqual(spec["user_id"], "test-operator")
        self.assertTrue(spec["federation_repo_url"])
        name = bootstrap.machine_name()
        if name:
            self.assertIn(name, json.loads(scalars["MACHINE_MAP"]))

    def test_spec_values_win_over_the_federation_config(self):
        spec = {"timezone": "UTC", "machine_map": {"box": "Box"}}
        self.assertEqual(bootstrap.resolve_timezone(spec), "UTC")
        self.assertEqual(bootstrap.resolve_machine_map(spec)["box"], "Box")

    def test_the_detected_time_zone_is_a_real_zone(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(bootstrap.detect_timezone())   # raises if it is not

    def test_a_bad_TZ_falls_through_rather_than_being_trusted(self):
        with mock.patch.dict(os.environ, {"TZ": "Not/AZone"}):
            self.assertNotEqual(bootstrap.detect_timezone(), "Not/AZone")


# ---------------------------------------------------------------------------
# WI-0453 — poga init and the lesson path
# ---------------------------------------------------------------------------


class InitTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(self)

    def _init(self, project, *extra):
        return self.sb.cli("init", "--yes", *extra, cwd=project)

    def test_init_from_nothing_makes_a_local_federation_and_a_local_project(self):
        a = self.sb.project("alpha")
        proc = self._init(a, "--purpose", "Keeps a small list.")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sb.gh_calls(), "", "init ran gh")
        self.assertEqual(_remotes(a), "", "init created a remote unasked")
        data = json.loads((self.sb.fed / "federation.json").read_text(encoding="utf-8"))
        self.assertIn("alpha", data["members"])
        self.assertEqual(data["members"]["alpha"]["path"], str(a))
        from zoneinfo import ZoneInfo
        ZoneInfo(data["timezone"])
        self.assertTrue((self.sb.fed / "users" / data["user_id"] / "profile.md").is_file())
        # A working, committed project whose harness actually imports.
        for f in ("session.py", "CANON.md", "STANDARD.md", "alpha-arch.md",
                  "sessionlib/__init__.py"):
            self.assertTrue((a / f).is_file(), f)
        self.assertEqual(subprocess.run([GIT, "-C", str(a), "status", "--porcelain"],
                                        capture_output=True, text=True).stdout, "")
        self.assertIn("Keeps a small list.", (a / "alpha-arch.md").read_text(encoding="utf-8"))
        cfg = json.loads((a / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["timezone"], data["timezone"])

    def test_the_first_session_start_writes_only_what_the_harness_commits(self):
        """WI-0464: the first `session.py start` froze the seed handoff into
        `sessions/pre-journal-archive.md` and rewrote `session-handoff.md`, neither in the
        install commit — and the archive is not a harness-owned path, so nothing ever
        committed it. The install now makes that one-time cutover itself, so the first
        start leaves only what every start leaves: its own journal and the compiled
        views, which the harness commits (ADR-0091)."""
        a = self.sb.project("alpha")
        self.assertEqual(self._init(a).returncode, 0)
        self.assertEqual(_porcelain(a), [])
        tracked = subprocess.run([GIT, "-C", str(a), "ls-files", "sessions/pre-journal-archive.md"],
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(tracked, "sessions/pre-journal-archive.md")
        self.assertIn("GENERATED — do not edit",
                      (a / "session-handoff.md").read_text(encoding="utf-8"))

        # WI-0484: this is about an ordinary start, so the cloud marker is dropped (in a
        # cloud container, start writes nothing tracked; test_cloud_container covers that).
        # Without the marker a root run is refused, so as root there is nothing to test.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("an ordinary start refuses root; the suite is running as root")
        env = {k: v for k, v in self.sb.env.items()
               if k not in ("POGA_INVOKED_FROM", "CLAUDE_CODE_REMOTE")}
        proc = subprocess.run([sys.executable, "session.py", "start"], cwd=str(a), env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dirty = [line[3:] for line in _porcelain(a, "-uall")]
        self.assertTrue(any(p.startswith("sessions/journal/") for p in dirty), dirty)
        stray = [p for p in dirty
                 if p not in ("STATUS.md", "session-handoff.md", "ROADMAP.md")
                 and not (p.startswith("sessions/journal/") and p.endswith(".md"))]
        self.assertEqual(stray, [], "the first start wrote a path the harness never commits")

    def test_a_new_project_is_at_the_current_standard(self):
        """WI-0465: the kit's settings copy lacked the floor's WorktreeRemove hook, so a
        brand-new project announced "v1.4.0 — open rollout to" the latest. The install
        renders settings from the floor, and the project's own self-check reads current."""
        import standard_check
        a = self.sb.project("alpha")
        self.assertEqual(self._init(a).returncode, 0)
        proc = subprocess.run([sys.executable, "standard_check.py", "--status"], cwd=str(a),
                              env=self.sb.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"v{standard_check.LATEST} — self-check clean", proc.stdout)
        self.assertNotIn("rollout", proc.stdout)

    def test_a_lesson_recorded_in_one_project_reaches_the_other(self):
        a, b = self.sb.project("alpha"), self.sb.project("beta")
        self.assertEqual(self._init(a).returncode, 0)
        with open(a / "architect-learnings.md", "a", encoding="utf-8") as fh:
            fh.write(LESSON.format(sid="alpha"))
        proc = self._init(b)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        pending = b / "proposed-edits" / "beta-arch" / "pending"
        got = sorted(p.name for p in pending.glob("*.md"))
        self.assertEqual(len(got), 1, got)
        text = (pending / got[0]).read_text(encoding="utf-8")
        self.assertIn("Check the exit code, not the tail", text)
        self.assertIn("apply: manual", text)
        self.assertIn("manual-reason: attended", text)
        self.assertNotIn("append-only", text, "a -specific entry must stay home")
        # Mirrored where the fleet's gather step would look for it.
        self.assertTrue((self.sb.fed / "inputs" / "alpha-arch-learnings.md").is_file())
        # Nothing flows back to where it came from.
        self.assertEqual(list((a / "proposed-edits" / "alpha-arch" / "pending").glob("*")), [])
        self.assertEqual(self.sb.gh_calls(), "")

        # Idempotent: a second share sends nothing, and neither does one after the
        # receiving project has triaged the brief out of pending/.
        again = self.sb.cli("share")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("0 new deliveries", again.stdout)
        applied = pending.parent / "applied"
        applied.mkdir(exist_ok=True)
        (pending / got[0]).rename(applied / got[0])
        self.sb.cli("share")
        self.assertEqual(list(pending.glob("*.md")), [])

    def test_the_receiving_session_start_names_the_lesson_in_its_inbox(self):
        """The lesson is not delivered until the receiving Architect can SEE it: the
        start banner's `inbox:` line is where a session reads its mail."""
        a, b = self.sb.project("alpha"), self.sb.project("beta")
        self.assertEqual(self._init(a).returncode, 0)
        with open(a / "architect-learnings.md", "a", encoding="utf-8") as fh:
            fh.write(LESSON.format(sid="alpha"))
        self.assertEqual(self._init(b).returncode, 0)
        env = {k: v for k, v in self.sb.env.items() if k != "POGA_INVOKED_FROM"}
        proc = subprocess.run([sys.executable, "session.py", "start"], cwd=str(b), env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        banner = proc.stdout + proc.stderr
        self.assertIn("lesson-from-alpha-", banner)

    def test_the_first_session_payload_follows_the_picker_setting(self):
        """Consultant brief 2026-09-28: the setting governs the instructions, not only
        the hook. A fresh project's first `session.py start` payload (the runtime's
        `additionalContext`) and its check-question hook agree, in both states."""
        env = {k: v for k, v in self.sb.env.items() if k != "POGA_INVOKED_FROM"}
        ask = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                          "tool_input": {"questions": []}})

        def first_session(name, pickers=None):
            # A second `start` in one session only says "already open", so each state
            # gets its own fresh project and reads that project's FIRST payload.
            project = self.sb.project(name)
            self.assertEqual(self._init(project).returncode, 0)
            if pickers is not None:
                cfg_path = project / "session.config.json"
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg["interaction"] = {"pickers": pickers}
                cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            proc = subprocess.run([sys.executable, "session.py", "start"], cwd=str(project),
                                  env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
            hook = subprocess.run([sys.executable, "session.py", "check-question"],
                                  cwd=str(project), env=env, input=ask,
                                  capture_output=True, text=True)
            return ctx, hook.stdout

        # Default: the ban is on, in the words and in the hook.
        ctx, hook = first_session("alpha")
        self.assertIn("pickers: denied", ctx)
        self.assertIn("Ask the user in prose, never a picker", ctx)
        self.assertIn("No multiple-choice pickers", ctx)
        self.assertNotIn("a picker is permitted", ctx)
        self.assertEqual(json.loads(hook)["hookSpecificOutput"]["permissionDecision"], "deny")

        # Opted in: the words permit the picker the hook now allows, and keep P17's rest.
        ctx, hook = first_session("beta", pickers="allow")
        self.assertIn("pickers: allowed", ctx)
        self.assertIn("a picker is permitted", ctx)
        self.assertIn("rejecting the question's premise", ctx)
        self.assertNotIn("never a picker", ctx)
        self.assertNotIn("No multiple-choice pickers", ctx)
        self.assertEqual(hook.strip(), "")

    def test_without_a_terminal_or_yes_it_refuses_and_writes_nothing(self):
        a = self.sb.project("alpha")
        proc = self.sb.cli("init", cwd=a)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("interactive terminal", proc.stdout)
        self.assertIn("--yes", proc.stdout)
        self.assertEqual(list(a.iterdir()), [])
        self.assertFalse(self.sb.fed.exists())

    def test_the_interview_answers_come_from_stdin_with_defaults(self):
        """Every prompt has a default. Driven through a pipe with --yes absent would
        refuse (no terminal), so the Interview is exercised directly."""
        import contextlib
        import io
        iv = lf.Interview(yes=False)
        with mock.patch.object(lf.sys, "stdin") as stdin, \
                contextlib.redirect_stdout(io.StringIO()):
            stdin.isatty.return_value = True
            stdin.readline.side_effect = ["\n", "Custom\n", "y\n", "\n"]
            self.assertEqual(iv.ask("Q", "dflt"), "dflt")
            self.assertEqual(iv.ask("Q", "dflt"), "Custom")
            self.assertTrue(iv.yes_no("R?", False))
            self.assertFalse(iv.yes_no("R?", False))

    def test_a_name_already_taken_by_another_folder_is_refused(self):
        a, other = self.sb.project("alpha"), self.sb.project("elsewhere")
        self.assertEqual(self._init(a).returncode, 0)
        proc = self._init(other, "--name", "Alpha")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("already has a project called 'alpha'", proc.stdout)
        self.assertEqual(list(other.iterdir()), [])

    def test_init_on_an_installed_project_only_registers_it(self):
        a = self.sb.project("alpha")
        self.assertEqual(self._init(a).returncode, 0)
        (self.sb.fed / "federation.json").write_text(json.dumps(
            {**json.loads((self.sb.fed / "federation.json").read_text()), "members": {}}))
        head = subprocess.run([GIT, "-C", str(a), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout
        proc = self._init(a)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("registering it only", proc.stdout)
        data = json.loads((self.sb.fed / "federation.json").read_text(encoding="utf-8"))
        self.assertIn("alpha", data["members"])
        self.assertEqual(subprocess.run([GIT, "-C", str(a), "rev-parse", "HEAD"],
                                        capture_output=True, text=True).stdout, head)

    def test_plan_writes_nothing(self):
        a = self.sb.project("alpha")
        proc = self.sb.cli("init", "--yes", "--plan", cwd=a)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(list(a.iterdir()), [])
        self.assertFalse(self.sb.fed.exists())


class IntakeRouteTest(unittest.TestCase):
    """Which interview a folder with no repo gets: `init` unless this checkout is an
    operating fleet federation (an operator profile with real entries)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = pathlib.Path(tmp.name)
        (self.d / "users" / "op").mkdir(parents=True)
        self.profile = self.d / "users" / "op" / "profile.md"
        cfg = self.d / "session.config.json"
        cfg.write_text(json.dumps({"user_profile": "users/op/profile.md"}), encoding="utf-8")
        for target, value in ((lf, "ROOT"), (bootstrap, "FED_CONFIG")):
            p = mock.patch.object(target, value, self.d if value == "ROOT" else cfg)
            p.start()
            self.addCleanup(p.stop)
        self.nofed = self.d / "no-federation"

    def test_a_blank_profile_is_not_a_fleet(self):
        self.profile.write_text(bootstrap.blank_profile_text("op"), encoding="utf-8")
        self.assertEqual(lf.intake_route(self.nofed), "init")

    def test_no_profile_at_all_routes_to_init(self):
        self.assertEqual(lf.intake_route(self.nofed), "init")

    def test_a_profile_with_entries_keeps_the_agent_intake(self):
        self.profile.write_text("# profile\n\n### a-real-entry\n\nterse.\n", encoding="utf-8")
        self.assertEqual(lf.intake_route(self.nofed), "agent")

    def test_an_existing_local_federation_routes_to_init(self):
        self.profile.write_text("### a-real-entry\n", encoding="utf-8")
        fed = self.d / "fed"
        lf.create_federation(fed, "op", "Op", "UTC", {})
        self.assertEqual(lf.intake_route(fed), "init")


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class WrapperFallsBackToInitTest(unittest.TestCase):
    """`poga` in a folder with no repo, on a machine with a local federation, runs init
    through the real wrapper — no runtime, no agent, no GitHub."""

    def test_bare_poga_in_an_empty_folder_runs_init(self):
        sb = Sandbox(self)
        lf.create_federation(sb.fed, "op", "Op", "UTC", {})
        target = sb.project("gamma")
        proc = subprocess.run(["bash", str(ROOT / "poga"), "--yes"], cwd=str(target),
                              env=sb.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("poga init", proc.stderr)
        self.assertTrue((target / "session.py").is_file())
        self.assertEqual(_remotes(target), "")
        self.assertEqual(sb.gh_calls(), "")


if __name__ == "__main__":
    unittest.main()
