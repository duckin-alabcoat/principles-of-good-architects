"""`poga` in a folder with no repo — the WI-0069 new-system intake.

The arm this covers only exists on the path the change CREATED: before it, a non-git
folder was a dead end (`require_repo` exit 2), so every pre-existing test of `poga`
runs in a configuration that can never reach this code. That is exactly the
`verify-in-the-created-configuration` trap, so these cases drive the real script in the
real configuration — an actual non-git directory, on an actual pty, with a stub runtime
on PATH — rather than asserting on the parts that were reachable anyway.

The invariant every case checks: **an intake that does not complete leaves the folder
exactly as it found it.** Nothing may be written before the operator says yes, because
`ADOPTED_MARKERS` (bootstrap.py) is `session.py` + `CANON.md` + `STANDARD.md` — install
any of them early and an abandoned intake becomes a folder the re-run refuses as a
retrofit.
"""

import os
import pathlib
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
POGA = ROOT / "poga"
TEMPLATE = ROOT / "bootstrap-kit" / "intake-prompt.md"


def _drive_on_a_pty(argv, cwd, keystrokes, env=None, timeout=30.0):
    """Run `argv` with a real terminal on stdin/stdout/stderr and type `keystrokes`.

    The intake refuses outright without a tty (it asks two questions), so the only way
    to exercise the interview is to give it one. Returns (exit_status, transcript).
    """
    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, cwd=str(cwd), stdin=slave, stdout=slave, stderr=slave,
                            env=env or os.environ.copy(), close_fds=True)
    os.close(slave)
    os.write(master, keystrokes.encode())
    chunks = []
    try:
        while True:
            ready, _, _ = select.select([master], [], [], timeout)
            if not ready:
                proc.kill()
                raise AssertionError("intake did not finish — it is probably blocked on "
                                     "a read the test did not answer:\n"
                                     + b"".join(chunks).decode(errors="replace"))
            try:
                data = os.read(master, 4096)
            except OSError:      # the child closed the pty — normal exit
                break
            if not data:
                break
            chunks.append(data)
    finally:
        os.close(master)
    proc.wait(timeout=timeout)
    return proc.returncode, b"".join(chunks).decode(errors="replace")


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class PogaIntakeTest(unittest.TestCase):
    # The agent-run interview is what this class drives. A bare `poga` picks between it
    # and `poga init` from the machine's state (WI-0453, `poga_cli.intake_route`),
    # so every case here names the agent path explicitly and pins the federation location
    # to an empty temp dir — the result must not depend on whose machine runs the suite.
    AGENT = "--agent-intake"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.target = pathlib.Path(self._tmp.name) / "brand-new-thing"
        self.target.mkdir()
        self.env = os.environ.copy()
        self.env["POGA_FEDERATION_HOME"] = str(pathlib.Path(self._tmp.name) / "no-federation")

    def tearDown(self):
        self._tmp.cleanup()

    def _contents(self):
        return sorted(p.name for p in self.target.iterdir())

    def _stub_runtime_env(self):
        """A PATH carrying a stub `claude` that records the argv it was launched with.

        Same shape as the stub-`claude` harness in test_adopt_runner_e2e: the point is to
        capture what the LAUNCH actually hands the runtime — the rendered prompt — without
        starting an agent or letting anything run the installer.
        """
        binroot = pathlib.Path(self._tmp.name) / "bin"
        binroot.mkdir(exist_ok=True)
        self.argv_log = pathlib.Path(self._tmp.name) / "argv.log"
        stub = binroot / "claude"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s" "$*" > {self.argv_log}\n'
            "exit 0\n",
            encoding="utf-8")
        stub.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{binroot}:/usr/bin:/bin"
        return env

    # --- the folder is left alone unless the operator says yes -------------------------

    def test_no_tty_refuses_by_name_and_writes_nothing(self):
        """Non-interactive is a REFUSAL, not a default-yes and not a hang. The message
        has to say which half is missing — folding "couldn't ask" into a generic error is
        the declare-what-a-check-assumes failure."""
        proc = subprocess.run(["bash", str(POGA), self.AGENT], cwd=str(self.target),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env=self.env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("interactive terminal", proc.stderr)
        self.assertEqual(self._contents(), [], "refusal must not write into the folder")

    def test_declining_the_gate_changes_nothing(self):
        """The y/N gate is the outward-facing one: past it, a private GitHub repo gets
        created. Declining must exit clean and leave an untouched folder."""
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\n\nn\n",
                                  env=self._stub_runtime_env())
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing changed", out)
        self.assertEqual(self._contents(), [])

    def test_bare_enter_at_the_gate_is_a_no(self):
        """The gate is `[y/N]` — anything that is not an explicit yes must not proceed."""
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\n\n\n",
                                  env=self._stub_runtime_env())
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing changed", out)
        self.assertEqual(self._contents(), [])

    # --- what the operator is told BEFORE deciding -------------------------------------

    def test_the_explanation_names_the_repo_creation_before_the_gate(self):
        """the operator's step 2: it explains what it is about to do. The outward-facing part —
        creating and pushing a private GitHub repo — must appear ahead of the gate, not
        be discovered afterwards, and must say it happens only on a yes (WI-0452)."""
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\n\nn\n",
                                  env=self._stub_runtime_env())
        self.assertEqual(rc, 0, out)
        self.assertIn("PRIVATE GITHUB REPO", out)
        self.assertLess(out.index("PRIVATE GITHUB REPO"), out.index("Start the intake"),
                        "the operator must read what happens BEFORE being asked to agree")
        self.assertIn("ONLY if you say yes", out)
        self.assertLess(out.index("Create a private GitHub repo"), out.index("Start the intake"),
                        "the remote question is asked before the gate, not assumed")

    def test_the_runtime_question_is_skipped_when_dash_r_answered_it(self):
        """the operator's step 3 is conditional: `-r` already answers it. Asking anyway would be
        a question with no trade-off."""
        rc, out = _drive_on_a_pty(["bash", str(POGA), "-r", "c"], self.target, "\nn\n",
                                  env=self._stub_runtime_env())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Which agent should run the intake?", out)

    # --- the launch itself --------------------------------------------------------------

    def test_yes_launches_the_runtime_with_a_fully_rendered_self_contained_prompt(self):
        """The end of the flow. The prompt must reach the runtime with every placeholder
        resolved: the folder is empty, so an unresolved `{{TARGET_DIR}}` cannot be looked
        up from context — the agent would interview the operator about it or invent one."""
        env = self._stub_runtime_env()
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\n\ny\n",
                                  env=env)
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.argv_log.exists(), f"runtime was never launched:\n{out}")
        prompt = self.argv_log.read_text(encoding="utf-8")
        self.assertNotIn("{{", prompt, "unresolved placeholder reached the runtime")
        # The facts the agent cannot recover on its own from an empty folder.
        self.assertIn(str(self.target), prompt)
        # WI-0452: the default answer is local only, so the install creates no remote.
        self.assertIn("bootstrap --adopt", prompt)
        self.assertNotIn("bootstrap --adopt --create-remote", prompt)
        self.assertIn("no: local only", prompt)
        # And it still wrote nothing itself — the agent owns every write from here.
        self.assertEqual(self._contents(), [])

    def test_a_yes_to_the_remote_question_is_the_only_way_to_create_one(self):
        env = self._stub_runtime_env()
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\ny\ny\n",
                                  env=env)
        self.assertEqual(rc, 0, out)
        prompt = self.argv_log.read_text(encoding="utf-8")
        self.assertIn("bootstrap --adopt --create-remote", prompt)
        self.assertIn("yes: create a private GitHub repo", prompt)
        self.assertEqual(self._contents(), [])

    # --- refusals that must land BEFORE the interview, not after ------------------------

    def test_a_member_copy_refuses_up_front(self):
        """`bootstrap` resolves against SCRIPT_DIR, so a poga symlinked out of a member
        checkout cannot run the install. Refusing after a ten-minute interview would be
        the worst possible time to find out."""
        fake_member = pathlib.Path(self._tmp.name) / "member"
        fake_member.mkdir()
        shutil.copy2(POGA, fake_member / "poga")
        proc = subprocess.run(["bash", str(fake_member / "poga")], cwd=str(self.target),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env=self.env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("principles-of-good-architects", proc.stderr)
        self.assertEqual(self._contents(), [])

    def test_an_already_adopted_folder_is_named_as_a_retrofit(self):
        """ADOPTED_MARKERS present means this folder has been through an install. Saying
        so up front is the difference between a clear refusal and a confusing one from
        deep inside bootstrap.py."""
        for marker in ("session.py", "CANON.md", "STANDARD.md"):
            (self.target / marker).write_text("x", encoding="utf-8")
        proc = subprocess.run(["bash", str(POGA)], cwd=str(self.target),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("RETROFIT", proc.stderr)

    def test_an_existing_spec_offers_the_install_instead_of_re_interviewing(self):
        """Resumability: a spec in the folder is the judgment half already done. Throwing
        it away and re-interviewing would punish the operator for a crashed session."""
        (self.target / "bootstrap-spec.json").write_text("{}", encoding="utf-8")
        rc, out = _drive_on_a_pty(["bash", str(POGA), self.AGENT], self.target, "c\n\nn\n",
                                  env=self._stub_runtime_env())
        self.assertEqual(rc, 0, out)
        self.assertIn("already has a bootstrap-spec.json", out)
        self.assertNotIn("interviews you", out)

    # --- the other verbs keep the old refusal -------------------------------------------

    def test_other_verbs_still_refuse_outside_a_repo(self):
        """Only `session` routes to the intake. `lanes` has nothing to stand up, so its
        plain "cd into a member checkout" refusal must survive unchanged."""
        proc = subprocess.run(["bash", str(POGA), "lanes"], cwd=str(self.target),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("not a git repository", proc.stderr)


class IntakePromptTemplateTest(unittest.TestCase):
    """The prompt is the whole judgment half of this feature — if it drifts, the intake
    still 'works' and produces a thin Architect, which is the failure this arm exists to
    prevent. These pin the parts that cannot be dropped silently."""

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_it_carries_every_required_spec_key(self):
        for key in ("system_name", "user_id", "user_name",
                    "federation_repo_url", "mission_prose"):
            self.assertIn(key, self.text, f"the intake cannot produce a spec without {key}")

    def test_it_states_the_ask_in_prose_rule_inline(self):
        """The runtime may be Codex or Gemini and inherits no canon, so P17 has to be IN
        the prompt. A prompt that merely cites a principle the agent cannot read is the
        single-source habit misapplied — there is nothing here to point at."""
        self.assertIn("Never a multiple-choice picker", self.text)

    def test_it_tells_the_agent_to_run_the_install_itself(self):
        """The command is rendered by the wrapper from the operator's answer to the
        remote question (WI-0452): `--create-remote` appears in it only on a yes."""
        self.assertIn("{{INSTALL_COMMAND}}", self.text)
        self.assertIn("{{REMOTE_CHOICE}}", self.text)
        self.assertNotIn("bootstrap --adopt --create-remote", self.text)

    def test_it_tells_the_agent_to_write_nothing_on_an_abandoned_interview(self):
        self.assertIn("write\nnothing", self.text.replace("**", ""))


if __name__ == "__main__":
    unittest.main()
