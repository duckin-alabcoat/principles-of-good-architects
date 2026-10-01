"""WI-0028 — the poga board ensure hook (consultant B8).

The board's `no-server-outlives-its-session` exemption rests on this wrapper
lifecycle, so the hook's contract is worth pinning precisely:

  1. configured (a `poga.local` sets POGA_BOARD_CMD) → the command runs at lane
     launch, backgrounded;
  2. unconfigured (no file, no env) → silent no-op, exit 0;
  2b. WI-0154: a MACHINE-level `~/.config/poga/poga.local` is read before the
     repo-local one, so one file per box serves every member checkout on it; a
     repo-local file still wins, and an unset `$HOME` reads nothing at all;
  3. a FAILING board command is still a no-op for the lane — the board is an
     observer and must never be able to fail a launch;
  4. the call site: `cmd_session` calls the hook after lane allocation and before
     EVERY `exec` — `exec` replaces the shell, so a hook placed after any of the
     three launch paths never runs (the load-bearing placement the B8 brief named,
     and the regression shape source-pinned per the ADR-0089 lesson).
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
POGA = ROOT / "poga"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import session  # noqa: E402  (path set above, as every suite in this tree does)


def _extract_function(name: str) -> str:
    """The named shell function's source, lifted from poga (which is a script, not a
    sourceable library — executing it to test one helper would run main)."""
    src = POGA.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", src, re.M | re.S)
    assert m, f"function {name} not found in poga"
    return m.group(0)


@unittest.skipUnless(shutil.which("bash"), "bash required")
class BoardEnsureHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.fn = _extract_function("poga_board_ensure")
        # WI-0154. The hook now reads `$HOME/.config/poga/poga.local`, so an unpinned
        # HOME would let this suite source the DEVELOPER'S real machine config and
        # actually launch a board — a fixture addressing live state, which is the one
        # thing `ambient_fixture` exists to make structurally impossible. Every case
        # below runs against this empty home unless it sets its own.
        self.home = self.tmp / "home"
        self.home.mkdir()

    def _machine_local(self, content: str) -> pathlib.Path:
        d = self.home / ".config" / "poga"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "poga.local"
        path.write_text(content, encoding="utf-8")
        return path

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, local_content: str | None, env_cmd: str | None = None,
             home: str | None = "") -> int:
        """`home=""` means this fixture's own empty home (the default seal); a caller
        passes `None` to run with HOME genuinely unset."""
        if local_content is not None:
            (self.tmp / "poga.local").write_text(local_content, encoding="utf-8")
        script = f'ROOT="{self.tmp}"\n{self.fn}\npoga_board_ensure\nexit $?\n'
        env = {"PATH": "/usr/bin:/bin"}
        if home == "":
            env["HOME"] = str(self.home)
        elif home is not None:
            env["HOME"] = home
        if env_cmd is not None:
            env["POGA_BOARD_CMD"] = env_cmd
        return subprocess.run(["bash", "-c", script], env=env,
                              capture_output=True, text=True).returncode

    def _wait_marker(self, marker: pathlib.Path, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if marker.exists():
                return True
            time.sleep(0.05)
        return False

    def test_configured_local_runs_the_command(self):
        marker = self.tmp / "board-started"
        rc = self._run(f'export POGA_BOARD_CMD="touch {marker}"\n')
        self.assertEqual(rc, 0)
        self.assertTrue(self._wait_marker(marker),
                        "POGA_BOARD_CMD from poga.local never ran")

    def test_env_var_works_without_a_local_file(self):
        marker = self.tmp / "board-started"
        rc = self._run(None, env_cmd=f"touch {marker}")
        self.assertEqual(rc, 0)
        self.assertTrue(self._wait_marker(marker))

    def test_unconfigured_is_a_silent_noop(self):
        self.assertEqual(self._run(None), 0)

    def test_failing_board_command_never_fails_the_caller(self):
        rc = self._run('export POGA_BOARD_CMD="exit 97"\n')
        self.assertEqual(rc, 0)

    def test_unreadable_local_file_never_fails_the_caller(self):
        # A poga.local with a syntax error must not brick every lane launch on the
        # machine that authored it — the hook is best-effort end to end.
        rc = self._run('this is ( not valid shell\n')
        self.assertEqual(rc, 0)

    # ── WI-0154: the machine-level file ──────────────────────────────────────────
    def test_machine_level_file_configures_a_repo_that_has_none(self):
        """The defect this closes. The development machine carried a poga launcher in
        every member checkout and a `poga.local` in none of them, so `poga_board_ensure` was a no-op on the machine
        where every lane now runs and the board survived only because a human had
        started it by hand."""
        marker = self.tmp / "board-started"
        self._machine_local(f'export POGA_BOARD_CMD="touch {marker}"\n')
        rc = self._run(None)
        self.assertEqual(rc, 0)
        self.assertTrue(self._wait_marker(marker),
                        "a machine-level poga.local never reached the hook")

    def test_repo_local_overrides_the_machine_level_file(self):
        """Precedence is machine-then-repo, so a member that genuinely needs its own
        board keeps the override it had before this change."""
        machine_marker = self.tmp / "machine-board"
        repo_marker = self.tmp / "repo-board"
        self._machine_local(f'export POGA_BOARD_CMD="touch {machine_marker}"\n')
        rc = self._run(f'export POGA_BOARD_CMD="touch {repo_marker}"\n')
        self.assertEqual(rc, 0)
        self.assertTrue(self._wait_marker(repo_marker))
        self.assertFalse(machine_marker.exists(),
                         "the machine-level command ran even though the repo overrode it")

    def test_machine_level_file_beats_an_inherited_env_var(self):
        """Same direction as the repo-local file's existing precedence over the env:
        a declared file is a decision, an inherited variable is an accident."""
        file_marker = self.tmp / "from-file"
        env_marker = self.tmp / "from-env"
        self._machine_local(f'export POGA_BOARD_CMD="touch {file_marker}"\n')
        rc = self._run(None, env_cmd=f"touch {env_marker}")
        self.assertEqual(rc, 0)
        self.assertTrue(self._wait_marker(file_marker))
        self.assertFalse(env_marker.exists())

    def test_an_unreadable_machine_file_never_fails_the_caller(self):
        """One bad machine-level file would otherwise brick the launch of every member
        on the box — strictly worse than the per-repo blast radius it replaces."""
        self._machine_local("this is ( not valid shell\n")
        self.assertEqual(self._run(None), 0)

    def test_an_unset_home_reads_nothing_and_still_exits_clean(self):
        """`~` expands via getpwuid when HOME is unset, so an unguarded read would let
        any process with a scrubbed environment — this suite included — reach the real
        config and start a real server. The guard is `[ -n "${HOME:-}" ]`."""
        self.assertEqual(self._run(None, home=None), 0)

    def test_the_hook_never_expands_a_bare_tilde(self):
        """Source-pinned because the failure is silent: a bare `~/.config/...` passes
        every test above (HOME is set in all of them) and only reaches the developer's
        disk in the one configuration no test creates."""
        self.assertNotIn("~/.config", self.fn,
                         "use an explicit, guarded $HOME — a bare ~ expands via getpwuid")
        self.assertIn('[ -n "${HOME:-}" ]', self.fn,
                      "the machine-level read must be guarded on a non-empty $HOME")


class BoardEnsureLifecycleTest(unittest.TestCase):
    """WI-0154 — `session.board_ensure_config`, the detector WI-0028 shipped without.

    The capability and the report must agree about WHERE the config lives, so these
    cases and the bash cases above are deliberately in one file: two lists of paths
    maintained apart is how the hook and its own check come to disagree.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.base = self.tmp / "repo"
        self.home.mkdir()
        self.base.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _machine(self, content: str) -> None:
        d = self.home / ".config" / "poga"
        d.mkdir(parents=True, exist_ok=True)
        (d / "poga.local").write_text(content, encoding="utf-8")

    def _repo(self, content: str) -> None:
        (self.base / "poga.local").write_text(content, encoding="utf-8")

    def _state(self) -> str:
        return session.board_ensure_config(home=self.home, base=self.base)[0]

    def test_the_devbox_defect_reads_unconfigured(self):
        """THE REAL DEFECT, reproduced: neither file present. This was devbox's actual
        state — every checkout running the launcher, a `poga.local` in none of them and
        machine level — while the hook reported exactly what a correctly configured
        machine reports, which is nothing. A detector that cannot name this state is
        the one that let it stand."""
        self.assertEqual(self._state(), "unconfigured")

    def test_the_machine_file_alone_is_configured(self):
        self._machine('export POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertEqual(self._state(), "configured")

    def test_the_repo_file_alone_is_still_configured(self):
        """The pre-WI-0154 arrangement keeps working and keeps reporting honestly."""
        self._repo('export POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertEqual(self._state(), "configured")

    def test_an_assignment_without_export_counts(self):
        self._machine('POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertEqual(self._state(), "configured")

    def test_a_readonly_assignment_counts(self):
        self._machine('readonly POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertEqual(self._state(), "configured")

    def test_an_indented_assignment_counts(self):
        self._machine('    export POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertEqual(self._state(), "configured")

    def test_a_mention_inside_a_comment_is_not_configuration(self):
        """The anchor's real subject. `poga.local.example` documents the variable in
        prose lines that MENTION `POGA_BOARD_CMD=...`; an unanchored search would read
        the template itself as a configured machine and certify a lifecycle that does
        not exist."""
        self._machine('# set POGA_BOARD_CMD=... here; see the example file\n')
        self.assertNotEqual(self._state(), "configured")

    def test_the_shipped_example_file_never_reads_as_configured(self):
        """The end-to-end version of the case above, against the REAL tracked template
        rather than a hand-written imitation of it — a fixture that only resembles the
        artifact proves nothing about the artifact."""
        example = (ROOT / "poga.local.example").read_text(encoding="utf-8")
        self._machine(example)
        self.assertNotEqual(self._state(), "configured",
                            "copying the template unedited reads as a configured board")

    def test_a_commented_out_assignment_is_not_configuration(self):
        """`poga.local.example` is almost entirely commented assignments; a scan that
        counted them would certify every machine that merely copied the template."""
        self._machine('# export POGA_BOARD_CMD="example_board.py --ensure"\n')
        self.assertNotEqual(self._state(), "configured")

    def test_a_file_with_no_assignment_is_unknown_not_unconfigured(self):
        """Three outcomes, never two. The file may set the variable by indirection —
        a text scan cannot see that, and must not report it as nothing being set."""
        self._machine('export SOMETHING_ELSE=1\n')
        self.assertEqual(self._state(), "unknown")

    def test_an_unreadable_file_is_unknown(self):
        d = self.home / ".config" / "poga"
        d.mkdir(parents=True, exist_ok=True)
        (d / "poga.local").mkdir()          # a directory where a file belongs
        self.assertEqual(self._state(), "unknown")

    def test_the_reported_paths_are_the_ones_the_hook_sources(self):
        """The hook and the check must not drift apart. Source-pinned against `poga`
        itself rather than restated, because a check that looks in a different place
        than the capability reads is a check that certifies the wrong machine."""
        fn = _extract_function("poga_board_ensure")
        machine, repo = session.board_ensure_paths(home=self.home, base=self.base)
        self.assertIn(".config/poga/poga.local", fn,
                      "poga no longer sources the machine-level path this check reports")
        self.assertTrue(str(machine).endswith(".config/poga/poga.local"))
        self.assertIn('"$ROOT/poga.local"', fn)
        self.assertTrue(str(repo).endswith("poga.local"))

    def test_a_line_that_cannot_resolve_its_paths_says_so(self):
        """The third outcome, at the reporting layer too. `Path.home()` and
        `_shared_work_root()` can both fail, and a diagnostic verb whose job is to answer
        a question about a broken machine must not raise out of the answer."""
        boom = unittest.mock.Mock(side_effect=OSError("no home"))
        with unittest.mock.patch.object(session, "board_ensure_config", boom):
            line = session.board_ensure_line()
        self.assertIn("CANNOT TELL", line)
        self.assertIn("unknown", line)

    def test_the_line_never_claims_a_restart_it_cannot_promise(self):
        line = session.board_ensure_line(home=self.home, base=self.base)
        self.assertIn("WILL NOT restart", line)
        self._machine('export POGA_BOARD_CMD="x"\n')
        self.assertIn("will restart", session.board_ensure_line(
            home=self.home, base=self.base))


class CallSiteTest(unittest.TestCase):
    def test_ensure_precedes_every_exec_in_cmd_session(self):
        src = POGA.read_text(encoding="utf-8")
        m = re.search(r"^cmd_session\(\) \{\n.*?^\}", src, re.M | re.S)
        self.assertIsNotNone(m, "cmd_session not found")
        body = m.group(0)
        call = body.find("poga_board_ensure")
        self.assertNotEqual(call, -1, "cmd_session never calls poga_board_ensure")
        alloc = body.find("alloc_lane")
        self.assertLess(alloc, call, "hook must run at lane creation (after alloc)")
        # WI-0105 routed every launch through `exec_or_rollback`, so the literal
        # `exec "$rt_cmd"` moved out of cmd_session into that wrapper. The INVARIANT is
        # unchanged and is what this asserts: whatever hands off to the runtime, the
        # hook must run before it, because a successful exec never comes back.
        first_exec = min(i for i in
                         (body.find("exec \"$rt_cmd\""), body.find("exec claude"),
                          body.find("exec_or_rollback"))
                         if i != -1)
        self.assertLess(call, first_exec,
                        "exec replaces the shell — the hook must precede every exec")

    def test_no_launch_path_bypasses_the_rollback_wrapper(self):
        """WI-0105's other half, pinned here because this is the file that already
        reasons about cmd_session's exec sites. A launch that execs directly would
        strand its lane on a failed exec — the defect the wrapper exists to close —
        and would do it silently, since nothing about the happy path differs."""
        src = POGA.read_text(encoding="utf-8")
        m = re.search(r"^cmd_session\(\) \{\n.*?^\}", src, re.M | re.S)
        body = m.group(0)
        self.assertNotIn('exec "$rt_cmd"', body,
                         "a launch path execs directly instead of via exec_or_rollback")
        self.assertIn("exec_or_rollback", body)

    def test_poga_local_is_gitignored_and_templated(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("poga.local", gi,
                      "machine-local hook file must never be tracked (P3)")
        self.assertTrue((ROOT / "poga.local.example").is_file(),
                        "the tracked template is the discoverable half")


if __name__ == "__main__":
    unittest.main()
