"""WI-0316: the mail poller gives the deploy runner its scheduled turn.

ADR-0103 D8 wanted a scheduled sweep. D9 would not let it be installed until a rollback had
been drilled, the drill was an execution on the runner host, and no federation session ever
runs there — so without another trigger the sweep is never installed and a member can sit
deployed-but-not-running indefinitely. The poller is the way out because it is already there: plain code, already on a
600s schedule, already fetching and fast-forwarding the trunk that carries the runner and
the registry.

TWO CLAIMS ARE TESTED HERE AND THEY ARE DIFFERENT CLAIMS. That the sweep is triggered only
when the checkout it would deploy from is actually the trunk's; and that a sweep going
wrong cannot take mail delivery down with it. The second matters because they now share a
process, which is the cost of not adding a second launchd unit.

Nothing here runs a real deploy — `test_deploy_drill.py` does that. What is real is the git
in `refresh`, because the fast-forward's own report is the thing under test.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_CURATE = pathlib.Path(__file__).resolve().parents[1] / "curate"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _CURATE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


class PollerSweepCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.poller = _load("mail_poller_sweep", "mail-poller.py")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.state = self.root / "checkout" / ".session-state"
        self._patches = [
            mock.patch.object(self.poller, "ROOT", self.root / "checkout"),
            # MAIL_ROOT, NOT ONLY ROOT. Since ADR-0135 the poller's `_git` runs against
            # `MAIL_ROOT` — `channel.data_root(ROOT)`, resolved at import — so a fixture
            # that redirected only ROOT would leave every git call pointed at the real
            # checkout. They are the same directory here because this fixture is not a
            # sealed tree, which is exactly the pre-cutover arrangement `refresh` takes the
            # unsealed branch for.
            mock.patch.object(self.poller, "MAIL_ROOT", self.root / "checkout"),
            mock.patch.object(self.poller, "STATE_DIR", self.state),
            mock.patch.object(self.poller, "STATUS_FILE", self.state / "s.json"),
            mock.patch.object(self.poller, "LOCK_FILE", self.state / "s.lock"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._tmp.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)

    # -- a real upstream and a real clone, because the fast-forward is the subject ----

    def _clone(self):
        up = self.root / "up"
        up.mkdir()
        _git(["init", "-b", "main"], up)
        (up / "f.txt").write_text("one\n")
        _git(["add", "-A"], up)
        _git(["commit", "-m", "one"], up)
        subprocess.run(["git", "clone", "--quiet", str(up), str(self.root / "checkout")],
                       check=True, capture_output=True)
        return up

    def _advance(self, up, text):
        (up / "f.txt").write_text(text)
        _git(["add", "-A"], up)
        _git(["commit", "-m", text], up)


class TestRefreshSaysWhetherTheTrunkMoved(PollerSweepCase):

    def test_already_up_to_date_is_not_reported_as_advanced(self):
        """`git merge --ff-only @{u}` exits 0 BOTH when it fast-forwards and when the
        branch is already current, so the old bare `True` was written into the status file
        as `refreshed_trunk` on every quiet run and read like news. Two different facts."""
        self._clone()
        r = self.poller.refresh(lambda *_: None)
        self.assertTrue(r.ok)
        self.assertFalse(r.advanced, "nothing new upstream — the trunk did not move")
        self.assertEqual(r.before, r.after)

    def test_a_real_fast_forward_is_reported_as_advanced(self):
        up = self._clone()
        self._advance(up, "two\n")
        r = self.poller.refresh(lambda *_: None)
        self.assertTrue(r.ok)
        self.assertTrue(r.advanced)
        self.assertNotEqual(r.before, r.after)
        self.assertEqual((self.poller.ROOT / "f.txt").read_text(), "two\n")

    def test_a_dirty_tree_does_not_advance_and_says_so(self):
        """Conservative on purpose: this runs unattended in a checkout a human may be
        working in. Yanking it out from under them costs their session."""
        up = self._clone()
        self._advance(up, "two\n")
        (self.poller.ROOT / "f.txt").write_text("local edit\n")
        r = self.poller.refresh(lambda *_: None)
        self.assertFalse(r.ok)
        self.assertFalse(r.advanced)

    def test_the_status_file_records_both_facts_separately(self):
        self._clone()
        result = {"queued": 0, "delivered": [], "elsewhere": [], "already_held": [],
                  "failed": []}
        payload = self.poller.write_status(result, self.poller.Refresh(True, advanced=False))
        self.assertIs(payload["refreshed_trunk"], True)
        self.assertIs(payload["trunk_advanced"], False)


class TestTheSweepTrigger(PollerSweepCase):
    """`main()` with delivery stubbed out — the question is only whether, and when, the
    deploy runner is handed its turn."""

    EMPTY = {"queued": 0, "delivered": [], "elsewhere": [], "already_held": [],
             "couriered": [], "sender_files": [], "unroutable": [], "malformed": [],
             "failed": []}

    def _run(self, argv, refresh=None, sweep_rc=0):
        self.state.mkdir(parents=True, exist_ok=True)
        calls = []

        def _sweep(log, dry_run=False):
            calls.append(dry_run)
            return {"ran": True, "rc": sweep_rc, "detail": ""}

        buf = io.StringIO()
        with mock.patch.object(self.poller, "refresh",
                               return_value=refresh or self.poller.Refresh(True)), \
             mock.patch.object(self.poller, "run_once", return_value=dict(self.EMPTY)), \
             mock.patch.object(self.poller, "persist"), \
             mock.patch.object(self.poller, "sweep_deploys", side_effect=_sweep), \
             redirect_stdout(buf), redirect_stderr(buf):
            rc = self.poller.main(argv)
        return rc, buf.getvalue(), calls

    def test_a_completed_refresh_hands_the_runner_its_turn(self):
        rc, _out, calls = self._run([])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [False], "the sweep must run without being started by hand")

    def test_it_sweeps_even_when_the_trunk_did_not_move(self):
        """THE TEST THAT PINS THE TRIGGER TO THE RIGHT EVENT. The promotion is a TAG in the
        member's own remote (ADR-0103 D2), and that arrives whether or not the federation
        trunk moved. Sweeping only on `advanced` would couple every member's release to an
        unrelated commit here — a member would wait for a federation commit to go live."""
        rc, _out, calls = self._run([], refresh=self.poller.Refresh(True, advanced=False))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [False])

    def test_an_incomplete_refresh_skips_the_sweep(self):
        """What `ok` rules out is the case that actually matters: a stale, dirty or
        diverged checkout deploying from runner code and a registry that are not the
        trunk's."""
        rc, out, calls = self._run([], refresh=self.poller.Refresh(False))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("sweep: skipped", out)
        self.assertIn("may not be the trunk's", out)

    def test_no_refresh_skips_the_sweep_too(self):
        """`--no-refresh` never even asks, so the checkout's relationship to the trunk is
        unknown — which is a reason to withhold, not to proceed."""
        rc, _out, calls = self._run(["--no-refresh"])
        self.assertEqual(calls, [])
        self.assertEqual(rc, 0)

    def test_the_opt_out_is_an_opt_out_and_not_the_default(self):
        """A fix that ends in "and then someone passes a flag" has moved the work, not done
        it. The sweep is what happens when nobody says anything."""
        rc, _out, calls = self._run(["--no-deploy-sweep"])
        self.assertEqual(calls, [])
        self.assertEqual(rc, 0)

    def test_a_dry_run_poller_only_rehearses_the_sweep(self):
        rc, _out, calls = self._run(["--dry-run"])
        self.assertEqual(calls, [True], "a dry run must not hand the runner a real turn")
        self.assertEqual(rc, 0)

    def test_a_refusing_sweep_does_not_make_the_mail_run_fail(self):
        """They share a process now, which is the price of not adding a second launchd
        unit. The launchd alarm channel is about whether MAIL is moving; a deploy that
        refuses would otherwise leave the job red for days over something no re-run here
        can clear."""
        rc, out, calls = self._run([], sweep_rc=1)
        self.assertEqual(calls, [False])
        self.assertEqual(rc, 0, "a deploy refusal is not a mail failure")
        self.assertIn("DEPLOY SWEEP: exit 1", out)

    def test_a_refusing_sweep_is_still_said_out_loud(self):
        """Printed past --quiet, for the same reason `unroutable` and `malformed` are: it
        is not a routine outcome, and the one reader is a log nobody opens unless something
        in it says to."""
        _rc, out, _calls = self._run(["--quiet"], sweep_rc=2)
        self.assertIn("DEPLOY SWEEP: exit 2", out)


class TestTheSweepSubprocess(PollerSweepCase):

    def test_it_invokes_the_runner_s_unattended_verb(self):
        """Not `--sweep`. `--unattended` is what carries the host gate and the D9 drill;
        calling the bare sweep from here would deploy production systems onto every machine
        in the fleet, because this program runs on all of them by design."""
        self.state.mkdir(parents=True, exist_ok=True)
        seen = {}

        def _run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

        with mock.patch.object(self.poller.subprocess, "run", side_effect=_run):
            rec = self.poller.sweep_deploys(lambda *_: None)
        self.assertEqual(rec["rc"], 0)
        self.assertIn("--unattended", seen["cmd"])
        self.assertNotIn("--sweep", seen["cmd"])
        self.assertTrue(seen["cmd"][1].endswith("deploy/runner.py"))
        self.assertEqual(seen["kw"]["timeout"], self.poller.SWEEP_TIMEOUT_SECONDS)

    def test_a_sweep_that_overruns_its_budget_is_abandoned_not_awaited(self):
        """It runs inside the poller's lock, and that lock goes stale at 900s. A sweep that
        outlived it would be overtaken by the next fire — two sweeps over one deploy tree,
        the one outcome worth a hard stop."""
        self.assertLess(self.poller.SWEEP_TIMEOUT_SECONDS, self.poller.LOCK_STALE_SECONDS)
        with mock.patch.object(self.poller.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)):
            rec = self.poller.sweep_deploys(lambda *_: None)
        self.assertEqual(rec["detail"], "timed out")
        self.assertIsNone(rec["rc"])

    def test_a_runner_that_will_not_start_is_recorded_not_raised(self):
        """`main` has no handler for this. An OSError escaping here would abort a mail run
        that had already succeeded."""
        with mock.patch.object(self.poller.subprocess, "run",
                               side_effect=OSError("no such file")):
            rec = self.poller.sweep_deploys(lambda *_: None)
        self.assertFalse(rec["ran"])
        self.assertIn("OSError", rec["detail"])


if __name__ == "__main__":
    unittest.main()
