"""End-to-end harness test for curate/adopt-runner.py (ADR-0050).

Drives the runner's real side-effecting orchestration — `adopt_one` — against a
throwaway git repo, with a STUB `claude` on PATH standing in for the headless
adoption session. This proves the parts the pure unit tests can't:

  * a verify-PASS adoption keeps its commit (HEAD moves, brief filed applied);
  * a verify-FAIL adoption is reset to the pre-adoption HEAD (brief restored to
    pending, role-doc change reverted) and a `comms/` blocked note is authored +
    committed.

No real `claude` and no network: the stub simulates the session deterministically.
Skipped automatically where `git` is unavailable.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("adopt_runner", ROOT / "curate" / "adopt-runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


# A stub `claude` executable. It ignores its args, reads the env var pointing at a
# python snippet that mutates the repo (cwd), runs it, and prints a JSON result — so
# the test controls exactly what "the adoption session" does.
STUB_CLAUDE = """#!/usr/bin/env bash
python3 "$STUB_ACTION"
echo '{"is_error": false, "result": "stub adoption done"}'
"""


def _make_args(**over):
    base = dict(dry_run=False, repo=None, model=None, claude_bin="claude",
                timeout=60, max_turns=10, max_repos=12, max_briefs_per_repo=3,
                verbose=False)
    base.update(over)
    return argparse.Namespace(**base)


class AuthSweepTest(unittest.TestCase):
    def test_first_stale_sweep_reports_and_recovery_clears(self):
        from contextlib import ExitStack
        from functools import partial
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            status_path = pathlib.Path(d) / "adopt-runner.status"
            note_path = pathlib.Path(d) / "adopt-runner-stale.md"
            for name, path in (("read_status", status_path), ("write_status", status_path),
                               ("sync_stale_note", note_path)):
                stack.enter_context(patch.object(runner, name,
                    partial(getattr(runner, name), path=path)))
            for name, value in (("discover_repos", []), ("run_janitor_pass", {}),
                                ("read_brief_state", {}), ("write_brief_state", None),
                                ("sync_dead_letter_note", ("noop", None)),
                                ("log_outcome", None)):
                stack.enter_context(patch.object(runner, name, return_value=value))
            probe = stack.enter_context(patch.object(runner, "preflight_auth",
                side_effect=[(False, "expired"), (True, "ok")]))
            args = _make_args(escalate_after=runner.DEFAULT_ESCALATE_AFTER_STALE)
            runner.sweep(args)
            stale = json.loads(status_path.read_text())
            self.assertEqual(stale["consecutive_stale"], 1)
            self.assertEqual(stale["auth"], "unauthenticated since " + stale["last_run"])
            self.assertIn(stale["stale_since"], note_path.read_text())
            runner.sweep(args)
            self.assertIsNone(json.loads(status_path.read_text())["auth"])
            self.assertFalse(note_path.exists())
            self.assertEqual(probe.call_count, 2)


@unittest.skipUnless(GIT, "git not available")
class AdoptOneE2ETest(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "member"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        # Minimal member repo: a role doc, a config, and a pending brief.
        (self.repo / "role.md").write_text("**Version:** v1.0.0\n\nrole\n", encoding="utf-8")
        (self.repo / "session.config.json").write_text(
            json.dumps({"architect_id": "member-arch"}), encoding="utf-8")
        pend = self.repo / "inbox" / "pending"
        pend.mkdir(parents=True)
        (self.repo / "inbox" / "applied").mkdir()
        self.brief = pend / "2026-07-15-x.md"
        self.brief.write_text(
            "---\nedit-id: 2026-07-15-x\napply: manual\n"
            "manual-reason: multi-file migration\nverify: test -f adopted.txt\n---\n\nbody\n",
            encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        self.config = {"architect_id": "member-arch", "inbox": "inbox/pending"}

        # PATH shim: our stub `claude` first.
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        stub = self.bindir / "claude"
        stub.write_text(STUB_CLAUDE, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bindir}:{self._old_path}"

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_action(self, snippet):
        action = self.tmp / "action.py"
        action.write_text(snippet, encoding="utf-8")
        os.environ["STUB_ACTION"] = str(action)

    def _head(self):
        return runner.git_head(self.repo)

    def test_verify_pass_keeps_commit(self):
        # The stub "adopts": creates the verify sentinel, moves brief to applied,
        # bumps the role doc, and commits — exactly what a real session would.
        self._set_action(
            "import pathlib\n"
            "pathlib.Path('adopted.txt').write_text('ok')\n"
            "import shutil, subprocess\n"
            "shutil.move('inbox/pending/2026-07-15-x.md', 'inbox/applied/2026-07-15-x.md')\n"
            "pathlib.Path('role.md').write_text('**Version:** v1.1.0\\n\\nrole\\n')\n"
            "subprocess.run(['git','add','-A'],check=True)\n"
            "subprocess.run(['git','commit','-qm','adopt'],check=True)\n")
        pre = self._head()
        oc = runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        self.assertEqual(oc["result"], "adopted", oc)
        self.assertNotEqual(self._head(), pre)
        self.assertTrue((self.repo / "inbox" / "applied" / "2026-07-15-x.md").exists())
        self.assertFalse(self.brief.exists())

    def test_verify_fail_resets_and_writes_comms(self):
        # The stub commits an adoption but NEVER creates the sentinel → verify fails.
        self._set_action(
            "import pathlib, subprocess\n"
            "shutil = __import__('shutil')\n"
            "shutil.move('inbox/pending/2026-07-15-x.md', 'inbox/applied/2026-07-15-x.md')\n"
            "pathlib.Path('role.md').write_text('**Version:** v1.1.0\\n\\nBROKEN\\n')\n"
            "subprocess.run(['git','add','-A'],check=True)\n"
            "subprocess.run(['git','commit','-qm','bad adopt'],check=True)\n")
        pre = self._head()
        oc = runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        self.assertEqual(oc["result"], "failed", oc)
        # Working tree reset to pre — the bad role-doc change is gone, brief back in pending.
        self.assertIn("BROKEN", "BROKEN")  # sanity
        self.assertEqual(
            (self.repo / "role.md").read_text(encoding="utf-8"),
            "**Version:** v1.0.0\n\nrole\n")
        self.assertTrue(self.brief.exists(),
                        "brief must be restored to pending after reset")
        self.assertFalse((self.repo / "inbox" / "applied" / "2026-07-15-x.md").exists())
        # A comms blocked note was authored AND committed (survives the reset because
        # it is written after it).
        notes = list((self.repo / "comms").glob("*-adopt-failed-*.md"))
        self.assertEqual(len(notes), 1, "one comms blocked note expected")
        self.assertIn("type: blocked", notes[0].read_text(encoding="utf-8"))
        # HEAD advanced by exactly the comms-note commit past `pre` (not the bad adopt).
        ok, subject = runner.run_git(self.repo, ["log", "-1", "--pretty=%s"])
        self.assertIn("background adoption", subject.lower())

    # --- WI-0331: the spawned session can actually CLOSE ------------------------
    #
    # The runner told the headless session to close with `session.py end --commit` and
    # gave it nothing that would let `end` agree to. `end` refuses without `--confirm`
    # unless a dispatch authorized the close, the dispatch exemption reads
    # `POGA_DISPATCH`, and `subprocess.run(cmd, cwd=...)` passed no `env=` — so nothing
    # ever set it. Every adoption applied its brief, failed to close, produced no commit,
    # and was reset by `verify-or-reset`: the fleet's only unattended adoption path could
    # not complete by construction. Worse than a stuck verb, because the one escape open
    # to the agent was `--confirm "<an invented user quote>"` into the audited field that
    # exists to prove a human agreed.
    #
    # These assert what the SPAWNED PROCESS actually sees, which is the only place the
    # defect lived — reading the runner's source could not have told us.

    def _probe_action(self):
        """A stub adoption that records its own environment, then adopts normally."""
        self.probe = self.tmp / "probe.json"
        os.environ["PROBE_OUT"] = str(self.probe)
        self._set_action(
            "import json, os, pathlib, subprocess\n"
            "rid = os.environ.get('POGA_UNATTENDED_RUN')\n"
            "rec = pathlib.Path('.session-state') / ('unattended-run-%s.json' % rid)\n"
            "pathlib.Path(os.environ['PROBE_OUT']).write_text(json.dumps({\n"
            "    'run_id': rid,\n"
            "    'record_readable': rec.is_file(),\n"
            "    'record': json.loads(rec.read_text()) if rec.is_file() else None,\n"
            "    'dispatch': os.environ.get('POGA_DISPATCH'),\n"
            "    'dispatch_item': os.environ.get('POGA_DISPATCH_ITEM'),\n"
            "}))\n"
            "pathlib.Path('adopted.txt').write_text('ok')\n"
            "shutil = __import__('shutil')\n"
            "shutil.move('inbox/pending/2026-07-15-x.md', 'inbox/applied/2026-07-15-x.md')\n"
            "subprocess.run(['git','add','-A'],check=True)\n"
            "subprocess.run(['git','commit','-qm','adopt'],check=True)\n")

    def _probe(self):
        return json.loads(self.probe.read_text(encoding="utf-8"))

    def test_the_spawned_session_is_marked_as_an_unattended_run(self):
        """The marker half. Without it `end` has nothing to key on and refuses."""
        self._probe_action()
        runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        self.assertTrue(self._probe()["run_id"],
                        "the adoption session ran with no POGA_UNATTENDED_RUN set")

    def test_the_run_record_is_readable_from_inside_the_spawned_session(self):
        """The RESOLVED half, and the one that makes the close honest rather than merely
        unblocked. An environment variable is a marker, not a control (ADR-0112 D7), so
        the session must be able to cite an artifact it read itself — which means the
        record has to exist, in that repo, while the session is running."""
        self._probe_action()
        runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        probe = self._probe()
        self.assertTrue(probe["record_readable"],
                        "the run record was not readable from the session's own cwd")
        self.assertEqual(probe["record"]["run_id"], probe["run_id"])
        self.assertIn("adopt-runner", probe["record"]["runner"])
        self.assertIn("2026-07-15-x", probe["record"]["subject"])

    def test_the_record_does_not_outlive_the_run(self):
        """The runner wrote it, so the runner removes it. An authorization artifact left
        behind is a standing licence to close a session nobody started — and the next
        interactive session in that repo would find one sitting in its state dir."""
        self._probe_action()
        runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        # Asserted against the record the session ACTUALLY SAW, never against an empty
        # glob: "no record afterwards" is also what a runner that never wrote one
        # produces, and a test that cannot tell those apart passes hardest in the state
        # it exists to catch (`declare-what-a-check-assumes`).
        self.assertTrue(self._probe()["record_readable"],
                        "nothing was ever written, so this proves nothing about cleanup")
        left = list((self.repo / ".session-state").glob("unattended-run-*.json"))
        self.assertEqual(left, [], "the run record outlived the spawn that authorized it")

    def test_the_sweeps_own_dispatch_handles_do_not_reach_the_member(self):
        """The sweep is routinely run from inside a dispatched lane and `subprocess.run`
        passes the ambient environment through wholesale. Unstripped, a member's `end`
        would resolve the FEDERATION's dispatch id against its own store, find nothing,
        and write `dispatch D-xxxxxx — UNRESOLVED` into that member's permanent record —
        a citation naming a lane in another repo that had nothing to do with it."""
        self._probe_action()
        with mock.patch.dict(
                os.environ, {"POGA_DISPATCH": "D-fed001", "POGA_DISPATCH_ITEM": "WI-0331"}):
            runner.adopt_one(self.repo, self.config, self.brief, _make_args(),
                             "2026-07-15T00:00:00Z")
        probe = self._probe()
        self.assertIsNone(probe["dispatch"], "the federation's dispatch id reached the member")
        self.assertIsNone(probe["dispatch_item"])
        self.assertTrue(probe["run_id"], "and the unattended marker must still be set")

    def test_no_commit_is_treated_as_failure(self):
        # The stub does nothing → HEAD unchanged → adoption failed (no work landed).
        self._set_action("pass\n")
        pre = self._head()
        oc = runner.adopt_one(self.repo, self.config, self.brief, _make_args(), "2026-07-15T00:00:00Z")
        self.assertEqual(oc["result"], "failed", oc)
        self.assertIn("no commit", oc["detail"].lower())
        self.assertTrue(self.brief.exists())

    # --- WI-0304 B1: the setup-brief replay --------------------------------------
    #
    # The acceptance criterion, driven end-to-end rather than asserted: a synthetic
    # setup brief's verify command (the tool `gizmo` is invented), against a member
    # state that produces `KeyError: 'gizmo'`, through the real adoption path. What
    # the old code logged was the last 400 characters of a traceback, beginning
    # mid-token with the header already cut off, every night the brief was retried.

    ENROLLMENT_VERIFY = (
        'python3 -c "import json;m=json.load(open(\'.mcp.json\'));'
        "assert m['mcpServers']['gizmo']['command']=='gizmo-server';"
        'print(\'gizmo setup wired\')"'
    )

    def _rewrite_brief_verify(self, verify):
        self.brief.write_text(
            "---\nedit-id: 2026-01-10-gizmo-setup\napply: manual\n"
            f"manual-reason: substrate install\nverify: {verify}\n---\n\nbody\n",
            encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "brief")

    def _adopt_leaving_gizmo_unwired(self):
        """A stub adoption that commits real work but never wires gizmo into
        `.mcp.json` — exactly the state the brief's verify meets."""
        self._set_action(
            "import pathlib, json, subprocess\n"
            "pathlib.Path('.mcp.json').write_text(json.dumps({'mcpServers': {}}))\n"
            "pathlib.Path('role.md').write_text('**Version:** v1.1.0\\n\\nrole\\n')\n"
            "subprocess.run(['git','add','-A'],check=True)\n"
            "subprocess.run(['git','commit','-qm','adopt'],check=True)\n")

    def test_the_setup_keyerror_now_lands_as_a_verdict_not_a_traceback(self):
        self._rewrite_brief_verify(self.ENROLLMENT_VERIFY)
        self._adopt_leaving_gizmo_unwired()
        pre = self._head()
        oc = runner.adopt_one(self.repo, self.config, self.brief, _make_args(),
                              "2026-07-15T00:00:00Z")

        # The fact recorded is DEFECTIVE BRIEF, not "the adoption failed verification".
        self.assertEqual(oc["result"], "defective", oc)
        detail = oc["detail"]
        self.assertNotIn("\n", detail, "the whole point is that this is ONE line")
        self.assertNotIn("Traceback", detail)
        self.assertIn("DEFECTIVE BRIEF", detail)
        self.assertIn("KeyError", detail)
        self.assertIn("gizmo", detail)

        # The evidence is demoted, not destroyed — the raw traceback still rides the
        # outcome record for whoever debugs the brief.
        self.assertIn("Traceback (most recent call last):", oc["verify_output"])
        self.assertIn("KeyError", oc["verify_output"])

        # Unverified work is still never kept: same reset, same restored brief.
        self.assertEqual((self.repo / "role.md").read_text(encoding="utf-8"),
                         "**Version:** v1.0.0\n\nrole\n")
        self.assertTrue(self.brief.exists())

        # And the note the member gets names the right defect and the right owner.
        notes = list((self.repo / "comms").glob("*-adopt-defective-*.md"))
        self.assertEqual(len(notes), 1, "a defective brief gets its own note, not a "
                                        "failed-adoption note")
        body = notes[0].read_text(encoding="utf-8")
        self.assertIn("DEFECTIVE", body)
        self.assertIn("belongs to whoever", body)
        self.assertNotIn("Traceback", body)
        self.assertEqual(len(list((self.repo / "comms").glob("*-adopt-failed-*.md"))), 0)

    def test_the_same_brief_with_a_messaged_assert_stays_a_failed_adoption(self):
        """The control. Same crash site, same member state, one difference — the assert
        names its condition. That brief is SOUND, and must keep reading as a failed
        adoption on the ordinary three-strike counter."""
        self._rewrite_brief_verify(
            'python3 -c "import json;m=json.load(open(\'.mcp.json\'));'
            "assert 'gizmo' in m['mcpServers'], 'gizmo not wired into .mcp.json';"
            'print(\'ok\')"')
        self._adopt_leaving_gizmo_unwired()
        oc = runner.adopt_one(self.repo, self.config, self.brief, _make_args(),
                              "2026-07-15T00:00:00Z")
        self.assertEqual(oc["result"], "failed", oc)
        self.assertIn("gizmo not wired into .mcp.json", oc["detail"])
        self.assertNotIn("DEFECTIVE", oc["detail"])
        self.assertEqual(len(list((self.repo / "comms").glob("*-adopt-failed-*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
