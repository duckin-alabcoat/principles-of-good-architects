"""WI-0356 — a land behind origin must not proceed on a warning.

THE MEASURED DEFECT. `_report_checkout_staleness` printed *"IS N COMMIT(S) BEHIND
origin/main ... Pull there, then re-run. (Reporting only; nothing pulls under you.)"* and
the land ran anyway. On 2026-09-13 the shared trunk sat 1 behind origin — a deploy host's
own deploy-receipt push — and lands kept stacking on the stale trunk. Within the hour it was
19 AHEAD and 1 BEHIND, diverged, and the push was rejected non-fast-forward. Recovery cost
an `integrate` (merge plus a full re-gate) and reached operator.

A warning the tool itself then ignores is not a safeguard; it is a record that the tool
knew. One-behind is trivially recoverable and diverged is not, so the warning fired in
exactly the window where acting was free and declined to act.

WHAT THESE TESTS PIN, one branch each, so removing any branch turns one of them red:

  1. BEHIND ONLY — the land fast-forwards the shared trunk and proceeds, with no human
     action and nothing left warned-about. Watched going red against the warn-and-proceed
     code it replaces (see `MUTATION` below).
  2. DIVERGED — the land REFUSES, names `session.py integrate`, leaves the trunk exactly
     where it was, and does so BEFORE taking the land gate, so a refusal costs no queue
     slot and nobody waits behind it.
  3. CANNOT TELL — a refresh that failed refuses and says it could not verify, rather than
     rendering as "current". This is `declare-what-a-check-assumes` at its sharpest: the
     whole item is that proceeding on an unverified trunk is what cost. The refusal names
     its own escape, and `POGA_LAND_ON_UNVERIFIED_TRUNK=1` is that escape — off by
     default, armed only by exactly "1", and loud about what it traded, so a machine whose
     network is sick is not locked out of its own trunk by the safeguard.
  4. NOTHING TO BE BEHIND — no remote, or an origin with no trunk yet, is NOT one of the
     three and must land normally. Without this control the honest way to pass 1-3 would
     be to refuse everything, which would stop every land in every fixture.
  5. The check is in ALL THREE landers (ADR-0117 D4), asserted over the source, because a
     behavioural test only ever reaches the lander its fixture drives.

MUTATION, RUN RATHER THAN ASSERTED (2026-09-13), and it changed this file. With
`_require_trunk_current_for_land`'s call removed from `_land_worktree_lane` and the old
bare `git fetch` put back, the first draft of `ABehindOnlyTrunkIsFastForwardedTest` passed
3/3 against the defect. Its assertions were all about the END STATE — peer commit
reachable, trunk level with origin, peer's file in the main checkout — and warn-and-proceed
reaches that end state too: it lands on the stale tip, the push is rejected, and WI-0143's
self-integrate merges origin in and re-gates. Same destination, a second full suite run
later. Its `assertIn("fast-forwarded", report)` was worse still, matching
`_sync_main_checkout`'s own line on the recovery path — a green detector detecting nothing.

What WI-0356 changes is the COST, so the cost is what is asserted now: one gate run per
land, and no `integrating with origin`. Re-run against the same mutation after the rewrite:

    ABehindOnlyTrunkIsFastForwardedTest   FAILED (failures=3)   <- was OK, 3/3
    ADivergedTrunkIsRefusedTest           FAILED (failures=3)
    AnUnverifiableTrunkIsRefusedTest      FAILED (failures=3)
    NothingToBeBehindIsNotARefusalTest    OK                    <- the control holds

stdlib unittest: python3 -m unittest discover -s tests
"""

import ast
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import harness_fixture  # noqa: E402
from test_worktree_lane import GIT, WorktreeLaneBase, _git, _out  # noqa: E402


class StaleTrunkBase(WorktreeLaneBase):
    """A lane, a trunk, and a real `origin` — a filesystem path, never a network.

    Mirrors `IntegrateWithRemoteTest`'s setup deliberately rather than inventing a second
    one: the states this file is about are the states that file already knows how to
    build, and two fixtures for one topology is how two suites start disagreeing about
    what "behind" means."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)
        _git(self.main, "remote", "add", "origin", str(self.origin))
        self._install_main_compiler()
        _git(self.main, "push", "-q", "origin", "main")
        self._point_session_at_lane()

    # -- helpers ---------------------------------------------------------------
    def _peer_pushes(self, filename="peer.txt"):
        """Another machine lands and pushes. Through a real clone, so origin advances by
        a commit whose sha this side could never have produced."""
        other = self.tmp / f"other-{filename}"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True, text=True)
        for k, v in (("user.email", "peer@t"), ("user.name", "peer"),
                     ("commit.gpgsign", "false")):
            _git(other, "config", k, v)
        (other / filename).write_text("peer\n", encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", f"peer work {filename}")
        _git(other, "push", "-q", "origin", "main")
        return _out(other, "rev-parse", "HEAD")

    def _trunk_tip(self):
        return _out(self.main, "rev-parse", "refs/heads/main")

    def _origin_tip(self):
        return _out(self.origin, "rev-parse", "main")

    def _land(self, workfile="work.txt", push=True, watch_gate=False):
        """Drive the real lander. Returns `(outcome, report, gate_holds)`.

        The land gate is spied on rather than counted from the output because a refusal
        that takes the lock prints nothing about having taken it — the whole claim is
        about a lock nobody sees."""
        self._stage_lane_work(workfile)
        holds = []
        buf = io.StringIO()
        real_lock = session.land_gate_lock

        def spy(*a, **kw):
            holds.append(True)
            return real_lock(*a, **kw)

        with contextlib.ExitStack() as stack:
            if watch_gate:
                stack.enter_context(
                    mock.patch.object(session, "land_gate_lock", side_effect=spy))
            stack.enter_context(contextlib.redirect_stdout(buf))
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=push)
        return outcome, buf.getvalue(), holds

    def _land_counting_gates(self, workfile="work.txt"):
        """Drive the lander and count REAL gate runs. Returns `(outcome, report, runs)`.

        THE GATE COUNT IS THE MEASUREMENT THAT DISCRIMINATES, and it took a mutation run
        to find that out. Every end-state assertion in this file's first draft — the
        peer's commit reachable, the trunk level with origin, the peer's file in the main
        checkout — passed against warn-and-proceed code, because the old path REACHED the
        same end state: it landed on the stale tip, had its push rejected, and WI-0143's
        self-integrate merged origin in and re-gated. Same destination, a second full suite
        run and a merge commit later. The thing WI-0356 changes is the cost, so the cost is
        what has to be asserted. (An `assertIn("fast-forwarded", report)` was worse than
        useless here: `_sync_main_checkout` prints that word too, so it matched on the
        recovery path and reported a passing detector that detected nothing.)"""
        self._stage_lane_work(workfile)
        runs = []
        real_gate = session._gate_commit

        def counting(sha, skip=frozenset()):
            runs.append(sha)
            return real_gate(sha, skip)

        buf = io.StringIO()
        with mock.patch.object(session, "_gate_commit", side_effect=counting), \
                contextlib.redirect_stdout(buf):
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=True)
        return outcome, buf.getvalue(), runs

    def _make_diverged(self):
        """Ours on the trunk and unpushed, theirs on origin — the unrecoverable state.

        Built in the order it happens in life: a land that could not publish leaves the
        trunk AHEAD, and a peer pushing afterwards makes it behind as well. Returns the
        trunk tip, which every refusal in this file must leave untouched."""
        self._stage_lane_work("ours.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))
        self._peer_pushes()
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "diverged",
                         f"control: the fixture must actually be diverged, got {st!r}")
        return self._trunk_tip()


@unittest.skipUnless(GIT, "git not available")
class TheThreeStatesAreDistinguishedTest(StaleTrunkBase):
    """The classifier alone, because a lander test cannot say WHICH branch it took.

    `declare-what-a-check-assumes`: three answers, never two. A classifier that can only
    say "fine / not fine" is one that has already collapsed CANNOT TELL into one of the
    other two, and it is never obvious afterwards which one."""

    def test_level_with_origin_is_current(self):
        self.assertEqual(session._trunk_vs_origin("main").state, "current")

    def test_ahead_of_origin_alone_is_current_not_a_refusal(self):
        """Unpushed local commits are the ordinary state of a trunk between a land and its
        push, and the land about to run is what adds more of them. Only the COMBINATION
        with behind is unrecoverable, so ahead-only must not be swept in with it."""
        self._stage_lane_work("ours.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "current", repr(st))
        self.assertGreater(st.ahead, 0, "control: it really is ahead")
        self.assertTrue(st.may_land)

    def test_behind_only_is_behind_and_is_not_may_land(self):
        """`may_land` is False here ON PURPOSE. Behind is recoverable, not acceptable —
        the caller fast-forwards and only then proceeds, so nothing can read this property
        and conclude that landing on a stale trunk is fine."""
        self._peer_pushes()
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "behind", repr(st))
        self.assertEqual(st.ahead, 0)
        self.assertEqual(st.behind, 1)
        self.assertFalse(st.may_land)

    def test_both_at_once_is_diverged(self):
        self._make_diverged()
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "diverged", repr(st))
        self.assertGreater(st.ahead, 0)
        self.assertGreater(st.behind, 0)
        self.assertFalse(st.may_land)

    def test_a_failed_refresh_is_unknown_and_never_current(self):
        """THE COLLAPSE THIS EXISTS TO REFUSE. The refs on disk say level with origin, and
        that is exactly what makes it dangerous: without the fetch's verdict the honest
        "I could not look" renders byte-identical to "nothing is wrong"."""
        _git(self.main, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "unknown", repr(st))
        self.assertIn("refresh", st.why)
        self.assertFalse(st.may_land)

    def test_no_remote_is_not_unknown(self):
        """The control that keeps 'refuse when unsure' from meaning 'refuse always'."""
        _git(self.main, "remote", "remove", "origin")
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "unremoted", repr(st))
        self.assertTrue(st.may_land)

    def test_an_origin_without_the_trunk_yet_is_not_unknown(self):
        empty = self.tmp / "empty.git"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(empty)],
                       check=True, capture_output=True, text=True)
        _git(self.main, "remote", "set-url", "origin", str(empty))
        _git(self.main, "fetch", "-q", "--prune", "origin")
        st = session._trunk_vs_origin("main")
        self.assertEqual(st.state, "unremoted", repr(st))
        self.assertTrue(st.may_land)


@unittest.skipUnless(GIT, "git not available")
class ABehindOnlyTrunkIsFastForwardedTest(StaleTrunkBase):
    """CASE 1 — the state where acting is free, and the whole reason this is a fix rather
    than a louder print.

    Shipping the fast-forward as the DEFAULT is the point ([`a-verb-is-still-an-errand`]):
    a fix that ends in "and then someone runs a pull" leaves the defect in place with a
    manual step bolted on."""

    #: The sentence only the pre-land fast-forward prints. Matched in full, deliberately:
    #: the word "fast-forwarded" ALONE also appears in `_sync_main_checkout`'s line, which
    #: is printed on the recovery path too — so the short match passed against the very
    #: code this class exists to refuse.
    FF_LINE = "origin was ahead and we held nothing of our own"

    def test_the_land_fast_forwards_the_trunk_before_it_builds_on_it(self):
        peer = self._peer_pushes()
        outcome, report, _ = self._land()
        self.assertTrue(outcome, report)
        self.assertIn(self.FF_LINE, report)
        self.assertEqual(
            subprocess.run([GIT, "-C", str(self.main), "merge-base", "--is-ancestor",
                            peer, "refs/heads/main"]).returncode, 0,
            f"the peer's commit must be an ancestor of the landed trunk\n{report}")
        self.assertEqual(session._trunk_vs_origin("main").state, "current", report)

    def test_it_never_pays_for_the_rejected_push_repair(self):
        """THE COST CLAIM, and the only assertion here that the old code cannot satisfy.

        Warn-and-proceed reached the same end state by landing on the stale tip, having
        its push rejected, and letting WI-0143's self-integrate merge origin in and re-gate
        — a SECOND full suite run, inside the land gate, on a trunk that now carries a
        merge commit nobody needed. One gate run is the whole difference, so one gate run
        is what gets pinned."""
        self._peer_pushes()
        outcome, report, runs = self._land_counting_gates()
        self.assertTrue(outcome, report)
        self.assertNotIn("integrating with origin", report,
                         "the rejected-push repair ran; the trunk was stale when we built "
                         "on it")
        self.assertEqual(len(runs), 1,
                         f"the suite ran {len(runs)} time(s) for one land\n{report}")

    def test_the_main_checkout_is_brought_along_by_the_fast_forward(self):
        """A ref that moved while the working tree stayed put is the session-89 defect,
        and it is worse here than at an ordinary land: the tree would be stale with a
        peer's work it has never seen. `_sync_main_checkout` owns this, and the
        fast-forward has to actually call it — which is why the gate count is asserted
        alongside, so this cannot pass by having been repaired afterwards instead."""
        self._peer_pushes()
        outcome, report, runs = self._land_counting_gates()
        self.assertTrue(outcome, report)
        self.assertEqual(len(runs), 1, report)
        self.assertTrue((self.main / "peer.txt").is_file(),
                        f"the peer's file never reached the main checkout's tree\n{report}")


@unittest.skipUnless(GIT, "git not available")
class ADivergedTrunkIsRefusedTest(StaleTrunkBase):
    """CASE 2 — refuse, and name the verb.

    WHY REFUSE RATHER THAN AUTO-INTEGRATE: an auto-integrate inside a land would run the
    suite twice and could leave a half-merged trunk if the re-gate went red, with the lane
    holding the land gate throughout. Refusing costs one command; guessing costs the
    trunk."""

    def test_the_land_refuses_and_names_integrate(self):
        self._make_diverged()
        outcome, report, _ = self._land("more.txt")
        self.assertFalse(outcome, report)
        self.assertEqual(outcome.reason, "diverged", report)
        self.assertIn("DIVERGED", report)
        self.assertIn("session.py integrate", report)

    def test_it_refuses_before_taking_the_land_gate(self):
        """A refusal must cost nobody a queue slot. The land gate is a dated-ticket queue
        whose holds run to minutes, so taking it in order to say no would make every other
        lane wait for an answer that needed no lock at all."""
        self._make_diverged()
        outcome, report, holds = self._land("more.txt", watch_gate=True)
        self.assertFalse(outcome, report)
        self.assertEqual(holds, [], f"the land gate was taken to refuse\n{report}")

    def test_the_trunk_is_left_exactly_where_it_was(self):
        before = self._make_diverged()
        origin_before = self._origin_tip()
        outcome, report, _ = self._land("more.txt")
        self.assertFalse(outcome, report)
        self.assertEqual(self._trunk_tip(), before, report)
        self.assertEqual(self._origin_tip(), origin_before, report)
        self.assertEqual(outcome.exit_code, 1,
                         "refused with the trunk untouched is `integrate`'s exit 1")


@unittest.skipUnless(GIT, "git not available")
class AnUnverifiableTrunkIsRefusedTest(StaleTrunkBase):
    """CASE 3 — "I could not look" must never render as "fine".

    The trap is specific and this fixture builds it exactly: the remote-tracking refs on
    disk say level with origin. A check that answered from them alone would report CURRENT
    with complete confidence, having verified nothing."""

    def _break_the_remote(self):
        _git(self.main, "remote", "set-url", "origin", str(self.tmp / "gone.git"))

    def test_a_failed_refresh_refuses_rather_than_reporting_current(self):
        self._break_the_remote()
        outcome, report, _ = self._land()
        self.assertFalse(outcome, report)
        self.assertEqual(outcome.reason, "unverified", report)
        self.assertIn("CANNOT TELL", report)

    def test_it_says_it_could_not_verify_rather_than_that_it_is_current(self):
        self._break_the_remote()
        _, report, _ = self._land()
        self.assertNotIn("fast-forwarded", report)
        self.assertNotIn("landed:", report)

    def test_it_refuses_before_taking_the_land_gate(self):
        self._break_the_remote()
        before = self._trunk_tip()
        outcome, report, holds = self._land(watch_gate=True)
        self.assertFalse(outcome, report)
        self.assertEqual(holds, [], f"the land gate was taken to refuse\n{report}")
        self.assertEqual(self._trunk_tip(), before, report)

    def test_the_refusal_names_the_escape(self):
        """An escape nobody can find is not an escape. The refusal is the only place an
        operator is standing when they need it, so it is where the flag is named."""
        self._break_the_remote()
        _, report, _ = self._land()
        self.assertIn(session.LAND_UNVERIFIED_TRUNK_ENV, report)

    def test_the_opt_out_lands_and_says_what_it_traded(self):
        """The opt-out exists so a machine with a sick network is not locked out of its
        own trunk — `a-verb-is-still-an-errand` in the other direction: the automatic path
        is the default and the escape is a deliberate act. It must be LOUD, because what
        it buys is a land on a trunk nothing checked."""
        self._break_the_remote()
        with mock.patch.dict(os.environ,
                             {session.LAND_UNVERIFIED_TRUNK_ENV: "1"}):
            outcome, report, _ = self._land(push=False)
        self.assertTrue(outcome, report)
        self.assertIn("UNVERIFIED", report)
        self.assertIn(session.LAND_UNVERIFIED_TRUNK_ENV, report)

    def test_the_opt_out_is_off_unless_it_is_exactly_one(self):
        """A truthy-ish value must not arm it. `POGA_LAND_ON_UNVERIFIED_TRUNK=0` reads as
        "I thought about this and said no", and an `== "1"` test is the only reading of it
        that does not turn that into a yes."""
        self._break_the_remote()
        for value in ("0", "false", ""):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ,
                                     {session.LAND_UNVERIFIED_TRUNK_ENV: value}):
                    outcome, report, _ = self._land(f"work-{value or 'empty'}.txt")
                self.assertFalse(outcome, report)
                self.assertEqual(outcome.reason, "unverified", report)


@unittest.skipUnless(GIT, "git not available")
class NothingToBeBehindIsNotARefusalTest(WorktreeLaneBase):
    """CASE 4 — the control, and it is load-bearing.

    Every assertion above is satisfied by a check that refuses unconditionally. This is
    the half that says it does not: a checkout with no remote has nothing it could be
    behind, and every land in every fixture — and on any member whose trunk is local by
    design — runs in exactly that state."""

    def setUp(self):
        super().setUp()
        self._install_main_compiler()
        self._point_session_at_lane()

    def test_a_checkout_with_no_remote_lands_normally(self):
        self._stage_lane_work()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertTrue(outcome, report)
        self.assertNotIn("REFUSED", report)
        self.assertNotIn("CANNOT TELL", report)


class EveryLanderRunsThePreLandCheckTest(unittest.TestCase):
    """CASE 5 — the structural half (`add-structural-guard-on-recurrence`).

    ADR-0117 D4 is the precedent and the warning, and `_serialized_advance`'s own docstring
    quotes it: the last land-path change made in one lander and not the others shipped
    covering one of four ref-advancing paths and had to be written up as a known gap the
    same day. Asserted over the SOURCE for the reason its sibling guards give — a
    behavioural test only ever reaches the lander its fixture happens to drive, and
    `_land_candidate` and `_land_branch` are exactly the ones no fixture drives."""

    LANDERS = {
        "sessionlib/lanes.py": ["_land_worktree_lane"],
        "sessionlib/land.py": ["_land_candidate", "_land_branch"],
    }

    def _source_of(self, path, name):
        root = pathlib.Path(session.__file__).resolve().parent
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.dump(node)
        self.fail(f"{name} not found in {path}")

    def test_every_lander_checks_the_trunk_against_origin_first(self):
        for path, names in self.LANDERS.items():
            for name in names:
                with self.subTest(lander=name):
                    self.assertIn("_require_trunk_current_for_land",
                                  self._source_of(path, name),
                                  f"{name} lands without checking the trunk against "
                                  f"origin — WI-0356 in the lander it was not applied to")

    def test_no_lander_still_fetches_without_reading_the_answer(self):
        """The defect's actual shape, and the reason this guard is not redundant with the
        one above: every lander USED to open its attempt with a bare
        `sh(["git", "fetch", "--quiet"], check=False)` whose return code went nowhere. A
        lander could satisfy the check above and still carry that line, in which case a
        failed refresh would be invisible again on the path that re-added it."""
        src = harness_fixture.harness_source()
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name in {"_land_candidate", "_land_branch",
                                      "_land_worktree_lane"}):
                continue
            body = "\n".join(lines[node.lineno - 1:node.end_lineno or node.lineno])
            with self.subTest(lander=node.name):
                self.assertNotIn('sh(["git", "fetch", "--quiet"], check=False)', body,
                                 f"{node.name} refreshes without reading the answer — "
                                 f"the exact line WI-0356 replaced")


if __name__ == "__main__":
    unittest.main()
