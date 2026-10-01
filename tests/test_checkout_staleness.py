"""WI-0222 — the checkout that owns the store goes stale, and every verb answers from it.

WI-0188 gave `attach` a staleness line and stopped there; WI-0155 covers `dispatch`. `poga
work` was the THIRD surface of one fact, which is what `add-structural-guard-on-recurrence`
says to stop fixing one verb at a time. The failure: a main sitting hundreds of commits
behind `origin/main` lists a store with different numbering from the trunk, and —
the live hazard — can DRAW a number that already exists, re-issuing WI-0188, whose
own title is "The operator machine's checkout goes stale silently."

So: reading REPORTS and drawing REFUSES, from one probe.

The classes below are ordered by how much they prove. The mocked ones pin the contract; the
last one builds the configuration the change actually creates — a checkout genuinely behind
a real remote — because a suite written from the code's own premise can only confirm it
(`verify-in-the-created-configuration`).
"""

import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from coord_fixture import point_store_at  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=out, stderr=err)


class ReportsButNeverAllClearsTest(unittest.TestCase):
    """THREE OUTCOMES, NEVER TWO. "I could not look" must not render as "nothing is wrong".

    That collapse is the defect this whole neighbourhood keeps producing
    (`declare-what-a-check-assumes`), and the fix for it rebuilt it once: see
    `test_a_zero_count_with_no_successful_refresh_is_not_an_all_clear`, which is a real bug
    this change shipped and then caught by running the real path with the network down.
    """

    def _report(self, answer, verb="wi-list"):
        err = io.StringIO()
        with mock.patch.object(session, "_checkout_staleness", return_value=answer), \
             mock.patch.object(session, "_shared_work_root",
                               return_value=pathlib.Path("/repo")), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stderr", err):
            session._report_checkout_staleness(verb)
        return err.getvalue()

    def test_behind_is_reported_with_the_count_and_the_upstream(self):
        out = self._report((4, "origin/main", "", 0.0))
        self.assertIn("4 COMMIT(S) BEHIND origin/main", out)
        self.assertIn("/repo", out)

    def test_current_says_nothing_at_all(self):
        # Silence is load-bearing: a line on every healthy invocation is a line nobody
        # reads by the time it matters.
        self.assertEqual(self._report((0, "origin/main", "", 0.0)), "")

    def test_cannot_tell_is_its_own_answer(self):
        out = self._report((None, "", "no upstream is tracked for main", None))
        self.assertIn("CANNOT TELL", out)
        self.assertIn("no upstream is tracked", out)

    def test_a_zero_count_with_no_successful_refresh_is_not_an_all_clear(self):
        """The bug this change shipped, caught by running it for real (WI-0222).

        With the network down, the attempt-throttle correctly skips the retry, `rev-list`
        answers 0 against remote-tracking refs that were never updated, and a naive
        `behind <= 0: return` renders that byte-identical to a verified all-clear. `age is
        None` is the tell — there is no successful refresh to date the answer against."""
        out = self._report((0, "origin/main", "", None))
        self.assertIn("CANNOT TELL", out)

    def test_a_zero_count_on_refs_that_aged_out_is_not_an_all_clear(self):
        """The THIRD layer of the same collapse, found by running the real path after the
        land. While refreshes keep failing, `tried` advances and `at` does not, so the
        throttle skips the refresh and `rev-list` answers 0 against refs that may be hours
        old. Silence there claims a freshness nothing established. The TTL is this code's
        own definition of fresh enough, so an answer older than it cannot pass as one."""
        out = self._report((0, "origin/main", "", session.CHECKOUT_FETCH_TTL_SECONDS * 3))
        self.assertIn("CANNOT TELL", out)
        self.assertIn("no refresh has succeeded since", out)

    def test_a_zero_count_inside_the_ttl_is_still_silent(self):
        """The other half, and the reason the check above is bounded rather than absolute:
        a verified-current checkout must stay silent, or the line stops being read."""
        self.assertEqual(self._report((0, "origin/main", "", 30.0)), "")

    def test_a_stale_answer_says_how_old_it_is(self):
        out = self._report((3, "origin/main", "", 900.0))
        self.assertIn("15 min ago", out)

    def test_it_reports_on_stderr_so_a_captured_number_is_never_corrupted(self):
        """`N=$(session.py adr-next)` captures stdout. A warning that lands there would be
        worse than the staleness it warns about."""
        out = io.StringIO()
        with mock.patch.object(session, "_checkout_staleness",
                               return_value=(9, "origin/main", "", 0.0)), \
             mock.patch.object(session, "_shared_work_root",
                               return_value=pathlib.Path("/repo")), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stdout", out), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            session._report_checkout_staleness("wi-list")
        self.assertEqual(out.getvalue(), "")


class ALocalOnlyProjectIsNotWarnedAboutTest(unittest.TestCase):
    """WI-0455: `poga init` makes a project with no remote (WI-0452), and the basic
    acceptance drill printed "CANNOT TELL whether ... is current" ahead of every `poga
    work` verb there. With no remote there is no other copy to be behind. Real repos, not
    mocks: the question is what `git remote` answers."""

    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        _git(self.repo, "init", "-q", "-b", "main")

    def _report(self):
        err = io.StringIO()
        with mock.patch.object(session, "_shared_work_root", return_value=self.repo), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stderr", err):
            session._report_checkout_staleness("wi-list")
        return err.getvalue()

    def test_no_remote_says_nothing(self):
        self.assertEqual(self._report(), "")

    def test_a_remote_without_an_upstream_still_cannot_tell(self):
        # The negative control: silence must come from "no remote", not from any repo.
        _git(self.repo, "remote", "add", "origin", str(self.repo / "nowhere.git"))
        out = self._report()
        self.assertIn("CANNOT TELL", out)
        self.assertIn("no upstream is tracked", out)

    def test_a_failed_remote_probe_is_not_read_as_no_remote(self):
        self.assertFalse(session._has_no_remote(self.repo / "does-not-exist"))


class TheQuietListIsADenylistTest(unittest.TestCase):
    """Default-ON, opt out explicitly.

    An allowlist would reproduce exactly what `ship-the-detector-with-the-capability`
    warns about: the next store verb someone adds would silently have no check and nothing
    would say so. A denylist fails the other way — a new hook verb prints one line — which
    is visible and cheap.
    """

    def _called_for(self, verb):
        probe = mock.Mock(return_value=(0, "origin/main", "", 0.0))
        with mock.patch.object(session, "_checkout_staleness", probe), \
             mock.patch.object(session, "_shared_work_root",
                               return_value=pathlib.Path("/repo")), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            session._report_checkout_staleness(verb)
        return probe.called

    def test_a_hook_verb_is_not_probed_at_all(self):
        # Not merely silent — not probed. A hook must not pay for a check whose output it
        # would discard, and `start` already diagnoses its own sync state.
        self.assertFalse(self._called_for("start"))
        self.assertFalse(self._called_for("heartbeat"))

    def test_an_unknown_future_verb_is_checked_by_default(self):
        self.assertTrue(self._called_for("wi-something-nobody-has-written-yet"))

    def test_the_store_verbs_are_checked(self):
        for verb in ("wi-list", "wi-new", "wi-next", "ops-new", "claims"):
            self.assertTrue(self._called_for(verb), verb)


class TheRefreshIsThrottledAndNeverPullsTest(unittest.TestCase):
    """The cache operator chose (session ~187: the caching option), and the one thing it must not do."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".session-state").mkdir()

    def _run(self, ttl=session.CHECKOUT_FETCH_TTL_SECONDS, fetch_rc=0, fetch_err=""):
        runner = mock.Mock(return_value=_proc(rc=fetch_rc, err=fetch_err))
        with mock.patch.object(session, "sh", side_effect=[
                _proc(rc=0, out="origin/main\n"), _proc(rc=0, out="2\n")]), \
             mock.patch.object(session.subprocess, "run", runner):
            answer = session._checkout_staleness(ttl=ttl, ref="main", root=self.tmp)
        return answer, runner

    def test_it_never_pulls(self):
        """The one thing this must not do. Pulling under an operator mid-fan-out is the
        surprise WI-0188 rejected; a refresh is read-only, a pull is not."""
        _answer, runner = self._run()
        for call in runner.call_args_list:
            self.assertNotIn("pull", call[0][0])

    def test_a_second_call_inside_the_ttl_does_not_refresh(self):
        (answer, first) = self._run()
        self.assertEqual(answer[0], 2)
        self.assertEqual(first.call_count, 1)
        (_again, second) = self._run()
        self.assertEqual(second.call_count, 0, "the TTL did not throttle the refresh")

    def test_a_failed_refresh_is_recorded_so_it_is_not_retried_every_time(self):
        """The defect as measured: with the remote unreachable a refresh took
        75 s to fail, and stamping only on SUCCESS meant every single invocation paid the
        full timeout. The throttle keys on the last ATTEMPT, not the last success."""
        (answer, first) = self._run(fetch_rc=1, fetch_err="could not resolve host")
        self.assertIsNone(answer[0], "a failed refresh must be CANNOT TELL")
        self.assertEqual(first.call_count, 1)
        (_again, second) = self._run(fetch_rc=1, fetch_err="could not resolve host")
        self.assertEqual(second.call_count, 0, "a failed refresh was retried immediately")

    def test_a_failed_refresh_never_dates_the_answer_to_itself(self):
        answer, _ = self._run(fetch_rc=1, fetch_err="boom")
        self.assertIsNone(answer[0])
        self.assertNotEqual(answer[3], 0.0,
                            "a failed refresh must not report itself as fresh")

    def test_the_stamp_separates_the_last_success_from_the_last_attempt(self):
        self._run()                                   # succeeds: sets at + tried
        self._run(ttl=0, fetch_rc=1, fetch_err="x")   # ttl=0 writes nothing at all
        rec = json.loads((self.tmp / ".session-state" / "fetch-stamp.json")
                         .read_text(encoding="utf-8"))
        self.assertIn("at", rec["origin/main"])
        self.assertIn("tried", rec["origin/main"])

    def test_ttl_zero_always_refreshes_and_leaves_no_stamp(self):
        """`attach`'s mode: it runs rarely and deliberately and would rather pay the second
        than be wrong, so it must not read or write the shared cache."""
        (_a, first) = self._run(ttl=0)
        self.assertEqual(first.call_count, 1)
        self.assertFalse((self.tmp / ".session-state" / "fetch-stamp.json").exists())
        (_b, second) = self._run(ttl=0)
        self.assertEqual(second.call_count, 1, "ttl=0 must never be throttled")

    def test_a_timeout_is_cannot_tell_not_up_to_date(self):
        with mock.patch.object(session, "sh", return_value=_proc(rc=0, out="origin/main")), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 5)):
            behind, _up, why, _age = session._checkout_staleness(ref="main", root=self.tmp)
        self.assertIsNone(behind)
        self.assertIn("did not answer", why)


@unittest.skipUnless(GIT, "git not available")
class ARealCheckoutBehindARealRemoteTest(unittest.TestCase):
    """The configuration the change CREATES, not the one the tests were written in.

    Every class above mocks the probe or the refresh, so every one of them shares a premise
    with the code and can only confirm it. This one builds a checkout that is genuinely
    behind a genuinely-advanced remote and asks the two questions that matter: does the
    probe see it, and does the allocator refuse to draw from it.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        self.main = self.tmp / "main"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(self.main)],
                       check=True, capture_output=True, text=True)
        # WI-0295: `_checkout_staleness` reaches `_shared_work_root()`, which resolves
        # through the git COMMON dir to the operator's real repo and takes every live
        # lane's journals with it. Measured: 322 reads.
        #
        # AFTER the clone, deliberately: the fixture materialises `<root>/sessions/journal`,
        # and `git clone` refuses a destination that already exists and is non-empty.
        point_store_at(self, self.main)
        for k, v in (("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            _git(self.main, "config", k, v)
        (self.main / "work-items").mkdir()
        (self.main / "work-items" / "WI-0001-seed.md").write_text("seed\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed")
        _git(self.main, "push", "-q", "-u", "origin", "main")

    def _peer_pushes(self, name="WI-0002-peer.md"):
        """Another machine lands and pushes. Our checkout is now provably behind, and the
        item it cannot see is exactly the id our allocator would otherwise re-issue."""
        other = self.tmp / "other"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True, text=True)
        for k, v in (("user.email", "p@p"), ("user.name", "p"),
                     ("commit.gpgsign", "false")):
            _git(other, "config", k, v)
        (other / "work-items" / name).write_text("peer\n", encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "peer work")
        _git(other, "push", "-q", "origin", "main")

    def test_a_current_checkout_reads_as_current(self):
        """The negative control. Without it, a green refusal below could equally mean the
        probe answers "behind" for everything."""
        behind, upstream, why, age = session._checkout_staleness(
            ttl=0, ref="main", root=self.main)
        self.assertEqual(behind, 0, why)
        self.assertEqual(upstream, "origin/main")
        self.assertEqual(age, 0.0)

    def test_a_behind_checkout_is_seen_and_counted(self):
        self._peer_pushes()
        behind, upstream, why, _age = session._checkout_staleness(
            ttl=0, ref="main", root=self.main)
        self.assertEqual(behind, 1, why)
        self.assertEqual(upstream, "origin/main")

    def test_the_allocator_refuses_to_draw_and_prints_nothing_on_stdout(self):
        """The live hazard, end to end. The store on disk tops out at WI-0001, so an
        unguarded draw hands back WI-0002 — which already landed on the trunk. Nothing
        would surface that until the land."""
        self._peer_pushes()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(session, "ROOT", self.main), \
             mock.patch.object(session, "_SHARED_WORK_ROOT", None), \
             mock.patch.object(session, "_shared_work_root", return_value=self.main), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stdout", out), \
             mock.patch.object(sys, "stderr", err):
            with self.assertRaises(SystemExit) as raised:
                session._wi_reserve_next()
        self.assertEqual(raised.exception.code, 3,
                         "a refused draw must exit 3, distinct from argparse's 2")
        self.assertIn("REFUSING", err.getvalue())
        self.assertIn("1 commit(s) behind origin/main", err.getvalue())
        self.assertEqual(out.getvalue(), "",
                         "a capture must come back EMPTY, never holding a bad number")

    def test_a_current_checkout_still_draws(self):
        """The guard must not be a brake on the normal path — the failure mode of a
        correctness guard is that it stops correct work too."""
        with mock.patch.object(session, "ROOT", self.main), \
             mock.patch.object(session, "_SHARED_WORK_ROOT", None), \
             mock.patch.object(session, "_shared_work_root", return_value=self.main), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            n = session._wi_reserve_next()
        self.assertEqual(n, 2, "a current checkout must draw the next number normally")

    def test_cannot_tell_never_refuses_a_draw(self):
        """Refusing to draw because the network is down would be a worse bug than the one
        this prevents, and it would arrive on exactly the day someone is working offline.
        Only PROVABLY behind refuses."""
        self._peer_pushes()
        with mock.patch.object(session, "ROOT", self.main), \
             mock.patch.object(session, "_SHARED_WORK_ROOT", None), \
             mock.patch.object(session, "_shared_work_root", return_value=self.main), \
             mock.patch.object(session, "_trunk", return_value="main"), \
             mock.patch.object(session, "_checkout_staleness",
                               return_value=(None, "origin/main", "offline", None)), \
             mock.patch.object(sys, "stderr", io.StringIO()):
            n = session._wi_reserve_next()
        self.assertEqual(n, 2, "CANNOT TELL must not block a draw")


if __name__ == "__main__":
    unittest.main()
