"""WI-0326 / ADR-0122 D2: a stopped delivery resumes where it stopped, and never revalidates.

WHAT WENT WRONG, measured rather than suspected. Inject a failure at the
sync stage of the land's `publish` closure — after the CAS has already moved the trunk —
and then retry the land the way an operator or `poga recover` would. Before this change
the probe recorded: the trunk advanced (161a8cfb -> 211d2569), the exception escaped
`_serialized_advance` before any receipt was written, **zero** receipts on disk, and the
retry returning `LandOutcome(landed=False, reason='nothing')` — *"no commits ahead of
main, nothing to land"* — after zero gate runs, with the main checkout never synced and
the push never made. `LandExitStatusTest` classifies `"nothing"` as NOT a failure, so the
half-delivered trunk read as a clean no-op and nothing anywhere said otherwise.

THE SHAPE OF THE FIX, and why these tests assert what they do. Recovery here asks GIT,
never a record. ADR-0122 D2 requires reconciliation "against actual repository and remote
state — receipts cannot merely assert that an external effect happened", and names the
window it cares about: a push that succeeded before its receipt was saved. So the tests
below that matter most are the ones which delete or prevent the receipt and still demand
the right answer — because a design that read the receipt would pass every other test in
this file and fail exactly those.

ADR-0114 D7 is untouched. Nothing here reuses a validation across trees: a resume only
ever publishes a tip that is ALREADY on the trunk, gated when it landed, and any candidate
that is not on the trunk goes through the full gate as before. `AChangedCandidate...`
pins that, because a file about skipping work is the natural place for someone to
eventually skip the wrong thing.
"""
import contextlib
import io
import subprocess
import unittest
from unittest import mock

from test_land_is_a_merge import GateNeutralBase, _git, GIT
import session


class ResumeBase(GateNeutralBase):
    """A lane, a trunk, and a real `origin` — the push stages need somewhere to push."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True)
        _git(self.main, "remote", "add", "origin", str(self.origin))
        _git(self.main, "push", "-q", "origin", "main")
        _git(self.lane, "fetch", "-q", "origin")

    def _origin_tip(self) -> str:
        r = subprocess.run([GIT, "-C", str(self.origin), "rev-parse", "main"],
                           capture_output=True, text=True)
        return r.stdout.strip()

    def _land(self, **patches):
        """Land with push enabled. Returns `(outcome, gate_runs, output, raised)`.

        `patches` are `mock.patch.object(session, name, **kw)` — the failure injection
        ADR-0122's consequences call for by name: *"failure-injection tests, not
        success-path tests alone."*
        """
        runs = []
        real = session._run_gate

        def spy(cwd=None, **kw):
            runs.append(cwd)
            return real(cwd=cwd, **kw)

        buf, raised, out = io.StringIO(), None, None
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(session, "_run_gate", side_effect=spy))
            for name, kw in patches.items():
                stack.enter_context(mock.patch.object(session, name, **kw))
            try:
                with contextlib.redirect_stdout(buf):
                    out = session._land_worktree_lane(None, None, "1.0.0", push=True)
            except BaseException as exc:                        # noqa: BLE001
                raised = exc
        return out, len(runs), buf.getvalue(), raised

    def _stop_at_sync(self):
        """The canonical stopped delivery: the CAS lands, the publication dies at sync."""
        return self._land(_sync_main_checkout={
            "side_effect": RuntimeError("power loss mid-publish")})


class ACrashedPublicationIsNotSilentTest(ResumeBase):
    """The first half of the defect: the evidence used to vanish with the exception."""

    def _land_receipt(self) -> dict:
        """The `land-lane` receipt specifically.

        The injected failure stays patched for the whole call, so the in-process resume
        that fires afterwards hits the same broken stage and writes a second, `stalled`
        `resume-lane` receipt of its own. That is correct — both events happened — so
        these assertions name the one they mean rather than trusting an index."""
        land = [r for r in session.land_receipts() if r["verb"] == "land-lane"]
        self.assertEqual(len(land), 1, f"expected exactly one land receipt, got {land}")
        return land[0]

    def test_a_publication_that_raises_still_writes_its_receipt(self):
        """The load-bearing one. The CAS is a fact — the trunk moved — so the receipt is
        owed whatever the publication did. Before the fix this was ZERO receipts of any
        kind: the exception carried the only evidence out with it."""
        self._code_carrying_lane()
        self._stop_at_sync()
        self.assertTrue(session.land_receipts(),
                        "a publication that raised took the whole receipt down with it")
        self._land_receipt()

    def test_the_receipt_names_the_stage_that_raised(self):
        """A receipt that records the crash but not its cause sends the next reader to
        re-measure what this one already knew."""
        self._code_carrying_lane()
        self._stop_at_sync()
        rec = self._land_receipt()
        self.assertEqual(rec["outcome"], "landed",
                         "the trunk DID advance; calling it anything else is a lie "
                         "about the ref")
        self.assertIn("publish_error", rec["stages"])
        self.assertIn("power loss mid-publish", rec["stages"]["publish_error"])

    def test_the_resumes_own_failure_is_recorded_as_stalled(self):
        """The recovery path gets the same treatment it gives the land: a stage that
        raises is named in a receipt, not swallowed and not raised through."""
        self._code_carrying_lane()
        self._stop_at_sync()
        resumes = [r for r in session.land_receipts() if r["verb"] == "resume-lane"]
        self.assertEqual(len(resumes), 1, f"expected one resume receipt, got {resumes}")
        self.assertEqual(resumes[0]["outcome"], "stalled")

    def test_the_land_does_not_raise_through_the_caller(self):
        """ADR-0122 D2: a failed stage leaves the session OPEN with a named blocker. An
        exception escaping to the command is not that."""
        self._code_carrying_lane()
        out, _runs, log, raised = self._stop_at_sync()
        self.assertIsNone(raised, f"the land raised through its caller: {raised!r}")
        self.assertIsNotNone(out)

    def test_a_stopped_delivery_is_not_reported_as_a_finished_land(self):
        self._code_carrying_lane()
        out, _runs, log, _ = self._stop_at_sync()
        self.assertFalse(out.landed)
        self.assertEqual(out.reason, "delivery")
        self.assertIn("BLOCKED", log)
        self.assertIn("stays OPEN", log)

    def test_a_stopped_delivery_does_not_exit_zero(self):
        """The whole defect in one assertion.

        Before this change the retry answered `"nothing"`, which is BENIGN and exits 0 —
        so a trunk with an owed push and a stale checkout reported success to every caller
        reading `$?`. A stopped delivery must not be benign."""
        self._code_carrying_lane()
        out, _runs, _log, _ = self._stop_at_sync()
        self.assertNotIn(out.reason, session.LandOutcome.BENIGN)
        self.assertEqual(out.exit_code, 1)


class ARetryResumesAtTheFailedStageTest(ResumeBase):
    """The second half: the retry used to say there was nothing to do."""

    def _stopped_then_retry(self):
        self._code_carrying_lane()
        self._stop_at_sync()
        return self._land()

    def test_the_retry_is_not_reported_as_nothing_to_land(self):
        """The exact false negative this item exists to close."""
        out, _runs, log, _ = self._stopped_then_retry()
        self.assertTrue(out.landed, log)
        self.assertNotIn("nothing to land", log)

    def test_the_retry_runs_no_gate(self):
        """Acceptance: never re-run validation for a candidate whose tip is unchanged.
        The tip is already ON the trunk, gated when it landed — re-gating it would be
        paying for a verdict we hold."""
        _out, runs, log, _ = self._stopped_then_retry()
        self.assertEqual(runs, 0, f"the resume ran the suite again:\n{log}")

    def test_the_retry_finishes_the_stages_that_were_owed(self):
        out, _runs, log, _ = self._stopped_then_retry()
        self.assertEqual(self._origin_tip(), self._trunk_tip(),
                         f"the owed push was not made:\n{log}")
        self.assertIn("delivery complete", log)

    def test_the_resume_says_which_stages_it_is_finishing(self):
        """`a-close-is-the-banner-not-the-sentence`: a recovery that repairs the trunk
        and says nothing is a change the next operator has to take on trust."""
        _out, _runs, log, _ = self._stopped_then_retry()
        self.assertIn("resume:", log)
        self.assertIn("No validation", log)

    def test_the_resume_writes_its_own_receipt_under_its_own_verb(self):
        """FL7 reasons about receipts as a population of LANDS. A resume is a different
        event with a different cost profile, so it is labelled rather than disguised."""
        self._stopped_then_retry()
        verbs = [r["verb"] for r in session.land_receipts()]
        self.assertIn("resume-lane", verbs)
        self.assertEqual(verbs.count("land-lane"), 1)


class TheCrashWindowIsReconciledAgainstOriginTest(ResumeBase):
    """ADR-0122 D2's named window, and the test a receipt-reading design cannot pass.

    The push SUCCEEDS and the process dies before the receipt is written. Every durable
    record therefore says the push never happened; origin says it did. Origin is right."""

    def _push_then_lose_the_receipt(self):
        self._code_carrying_lane()
        return self._land(_land_receipt={
            "side_effect": RuntimeError("crash before the receipt lands")})

    def test_the_effect_took_hold_and_no_receipt_records_it(self):
        """The control. If this ever stops being true the rest of the class proves
        nothing, because the window it is about would not have been opened."""
        self._push_then_lose_the_receipt()
        self.assertEqual(self._origin_tip(), self._trunk_tip(),
                         "control: the push must actually have succeeded")
        self.assertEqual(session.land_receipts(), [],
                         "control: no receipt must have survived")

    def test_nothing_is_owed_when_origin_already_carries_the_tip(self):
        self._push_then_lose_the_receipt()
        self.assertEqual(session._publication_owed("main"), [],
                         "the reconciliation trusted the missing receipt over origin")

    def test_the_retry_does_not_push_a_second_time(self):
        self._push_then_lose_the_receipt()
        before = self._origin_tip()
        out, runs, log, _ = self._land()
        self.assertEqual(self._origin_tip(), before, log)
        self.assertEqual(runs, 0, f"the retry re-gated an already-delivered tip:\n{log}")
        self.assertFalse(out.landed)
        self.assertEqual(out.reason, "nothing")

    def test_an_origin_ahead_of_us_is_delivered_not_owed(self):
        """Containment, not equality. A peer pushing after our tip landed there leaves
        origin AHEAD — which is a published delivery, not an owed one."""
        self._push_then_lose_the_receipt()
        peer = self.tmp / "peer"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(peer)], check=True)
        _git(peer, "config", "user.email", "p@p")
        _git(peer, "config", "user.name", "p")
        # WI-0250: repo-local config beats global, and without this the fixture inherits
        # `commit.gpgsign` from whoever runs the suite — on a signing machine the commit
        # below shells out to gpg with no TTY and the failure lands in the fixture rather
        # than in anything under test. `test_fixture_git_defaults` enforces this.
        _git(peer, "config", "commit.gpgsign", "false")
        (peer / "peer.txt").write_text("a peer landed after us\n", encoding="utf-8")
        _git(peer, "add", "-A")
        _git(peer, "commit", "-qm", "peer work")
        _git(peer, "push", "-q", "origin", "main")
        _git(self.lane, "fetch", "-q", "origin")
        self.assertEqual(session._publication_owed("main"), [],
                         "an origin that is ahead of us was read as an owed push")


class ARepeatCompletionDoesNoWorkTest(ResumeBase):
    """ADR-0122 D2 (d): repetition returns the existing receipt — no validation, no
    second close stamp, no work."""

    def _completed(self):
        self._code_carrying_lane()
        self._stop_at_sync()
        self._land()                                  # the resume that completes it
        return len(session.land_receipts())

    def test_a_repeat_runs_no_gate_and_writes_no_receipt(self):
        before = self._completed()
        _out, runs, log, _ = self._land()
        self.assertEqual(runs, 0, log)
        self.assertEqual(len(session.land_receipts()), before,
                         "a repeat completion wrote a new receipt for work it did not do")

    def test_a_repeat_surfaces_the_existing_receipt(self):
        self._completed()
        _out, _runs, log, _ = self._land()
        self.assertIn("already complete", log)
        self.assertIn("No validation run", log)

    def test_a_repeat_stays_benign_so_a_cleanup_only_retry_is_not_a_failure(self):
        """The risk THIS change introduces, pinned.

        Acceptance covers a cleanup-only retry: the delivery is done and only the lane
        teardown remains. That teardown is WI-0312's shared exit, which is already
        idempotent (`tests/test_lane_exit.py::TheExitIsIdempotentAndRetryableTest`) and is
        not re-implemented here. What this class must guarantee is that the new routing
        does not turn such a retry into a FAILURE on the way past — a non-benign reason
        would exit 1 and make a cleanup retry look like a broken land."""
        self._completed()
        out, _runs, _log, _ = self._land()
        self.assertEqual(out.reason, "nothing")
        self.assertIn(out.reason, session.LandOutcome.BENIGN)
        self.assertEqual(out.exit_code, 0,
                         "a repeat completion exits non-zero, so a cleanup-only retry "
                         "reads as a failed land")


class AnEmptyLaneIsStillNothingToLandTest(ResumeBase):
    """The regression guard. A lane that genuinely carries nothing must keep getting the
    answer it always got — the resume check runs on the same branch and a false positive
    here would put every empty land through a publication it was never owed."""

    def test_an_empty_lane_reports_nothing_to_land(self):
        out, runs, log, _ = self._land()
        self.assertFalse(out.landed)
        self.assertEqual(out.reason, "nothing")
        self.assertIn("nothing to land", log)
        self.assertEqual(runs, 0)

    def test_an_empty_lane_does_not_announce_a_resume(self):
        _out, _runs, log, _ = self._land()
        self.assertNotIn("resume:", log)


class AStaleCheckoutAloneDoesNotStartAResumeTest(ResumeBase):
    """`_publication_is_stalled`, stated as the rule it encodes.

    An owed push means a delivery stopped. An owed sync does not: a main checkout sits
    behind the trunk for ordinary reasons, `_sync_main_checkout` already owns that case
    (it writes `stale-checkout.txt`, names `main-restore`, and retries at the next land),
    and giving it a second owner would make every empty lane print a blocker about a
    checkout nobody asked it to repair."""

    def test_an_owed_push_is_a_stalled_delivery(self):
        self.assertTrue(session._publication_is_stalled(["push"]))
        self.assertTrue(session._publication_is_stalled(["sync", "push"]))

    def test_an_owed_sync_alone_is_not(self):
        self.assertFalse(session._publication_is_stalled(["sync"]))
        self.assertFalse(session._publication_is_stalled([]))


class ARewoundTrunkPublishesNothingTest(ResumeBase):
    """`_serialized_publish`'s precondition. Publishing a tree the trunk no longer carries
    would publish a lie, so it is checked rather than assumed."""

    def test_a_tip_no_longer_on_the_trunk_is_reported_gone(self):
        self._code_carrying_lane()
        tip = subprocess.run([GIT, "-C", str(self.lane), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        base = subprocess.run([GIT, "-C", str(self.main), "rev-parse", "main~1"],
                              capture_output=True, text=True).stdout.strip()
        calls = []
        outcome, _hold, _stages = session._serialized_publish(
            "main", tip, lambda stages: calls.append(stages), verb="resume-lane")
        self.assertEqual(outcome, "gone")
        self.assertEqual(calls, [], "a rewound trunk was published over anyway")


class AChangedCandidateRevalidatesInFullTest(ResumeBase):
    """ADR-0114 D7 stands, and this file is where someone would eventually break it.

    A resume skips validation only for a tip already ON the trunk. New work in the lane is
    a candidate no gate has seen, and it gates in full like any other."""

    def test_new_work_after_a_resumed_delivery_gates_again(self):
        self._code_carrying_lane()
        self._stop_at_sync()
        _out, resume_runs, _log, _ = self._land()
        self.assertEqual(resume_runs, 0, "control: the resume must not have gated")
        self._commit_in_lane("session.py", "# newer code\n", "fix(session): more work")
        out, runs, log, _ = self._land()
        self.assertTrue(out.landed, log)
        self.assertEqual(runs, 1, f"a changed candidate skipped its gate:\n{log}")


if __name__ == "__main__":
    unittest.main()
