"""WI-0266 — the CAS retry budget is derived from what a retry costs, not a constant.

The gate runs INSIDE the compare-and-swap window (ADR-0058 D4/D5), so the window's width
is the suite's runtime. Every lander carried `attempts: int = 3` beside that moving cost:
a bare default parameter, passed by no caller, reachable from no CLI flag. As the suite
grows each attempt gets wider, the odds of surviving one fall, and the 3 beside it does
not move — so the same literal quietly means something weaker every month. One work
item's worth of new tests can widen the same four-command gate by several percent
inside one day.

ADR-0114 (WI-0267) serialized the land and made a lost CAS rarer, but did NOT close this:

  * its three fail-open paths — no coordination store, the 45-minute record expiry, the
    90-minute wait cap — each drop the lane back into the unserialized loop, and the cap
    fires only during a storm, which is exactly when a budget of 3 is least adequate;
  * the lock serializes LANDS, not writers of the trunk ref. `poga work` / `poga ops`
    commit straight to the main checkout outside it by design (ADR-0073), so a
    gate-holding lane still loses swaps to store writes — observed while landing ADR-0114
    itself (WI-0272).

What these pin:

  1. the budget scales with the gate instead of standing beside it, with a floor that is
     the old fixed 3 (never worse) and a cap that stops a fast member running away (P19);
  2. THE HEADLINE: a land subjected to more than three lost swaps now lands, where the
     fixed budget gave up — WI-0266's own stated verification, "N concurrent lands with a
     deliberately slow gate, asserting every one eventually lands";
  3. giving up is still possible and still exits 2 — a budget must not convert a failure
     into a hang, which would undo WI-0263;
  4. the retry line no longer asserts "another lane landed first", which since ADR-0114 is
     usually false;
  5. the structural guard: no lander may regress to a hardcoded retry count.
"""

import ast
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import harness_fixture  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402

GIT = shutil.which("git")
PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


class _Clock:
    """A fake monotonic clock. The budget is arithmetic over durations, so driving it with
    a real clock would mean a test that either sleeps for twenty minutes or asserts
    nothing — the two ways this kind of test is usually written and useless."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _spend(budget, gate_seconds, clock):
    """Run a budget to exhaustion where every attempt costs `gate_seconds`."""
    n = 0
    for _ in budget.attempts():
        n += 1
        clock.advance(gate_seconds)
    return n


class BudgetArithmeticTest(unittest.TestCase):
    """The derivation itself, with no git and no gate — just the sums."""

    def test_a_slower_gate_no_longer_silently_means_weaker_odds(self):
        """The defect in one assertion: the number of tries must respond to the cost of a
        try. Both gate durations below are example values."""
        c1 = _Clock()
        at_202 = _spend(session._CasBudget(now=c1), 202.6, c1)
        c2 = _Clock()
        at_20 = _spend(session._CasBudget(now=c2), 20.0, c2)
        self.assertGreater(at_202, 3,
                           "a 202.6s gate must now buy more than the "
                           "fixed 3 attempts it used to")
        self.assertGreater(at_20, at_202,
                           "a member with a fast gate can afford more tries in the same "
                           "wall-clock allowance — that is the whole point of deriving it")

    def test_the_floor_is_the_fixed_three_it_replaces(self):
        """A gate slower than the entire allowance must still get the old 3. Without the
        floor the estimate permits one attempt and stops, which would make this change a
        REGRESSION for exactly the members whose gates are slowest."""
        c = _Clock()
        b = session._CasBudget(now=c)
        self.assertEqual(_spend(b, session.LAND_CAS_BUDGET_SECONDS * 10, c), 3)
        self.assertEqual(session.LAND_CAS_MIN_ATTEMPTS, 3,
                         "the floor IS the constant this replaces; if it ever differs, "
                         "this change stopped being safe-by-construction")

    def test_the_cap_stops_a_fast_gate_running_away(self):
        """P19. A one-second gate would otherwise fit 1200 attempts into the allowance."""
        c = _Clock()
        b = session._CasBudget(now=c)
        self.assertEqual(_spend(b, 1.0, c), session.LAND_CAS_MAX_ATTEMPTS)
        self.assertEqual(b.stopped_by, "cap")

    def test_an_explicit_count_still_means_exactly_that(self):
        """~45 existing tests call the landers with `attempts=N` and mean 'exactly N'.
        A widening that quietly changed what they assert would be a rewrite wearing a
        widening's clothes."""
        for want in (1, 2, 3, 7):
            c = _Clock()
            self.assertEqual(_spend(session._cas_budget(want), 1.0, c), want)

    def test_none_means_derived_and_that_is_the_default(self):
        """The automatic path is the DEFAULT and the fixed count the opt-out, never the
        reverse — a fix that only works when someone remembers a flag has not shipped."""
        b = session._cas_budget(None)
        self.assertEqual(b.min_attempts, session.LAND_CAS_MIN_ATTEMPTS)
        self.assertEqual(b.max_attempts, session.LAND_CAS_MAX_ATTEMPTS)
        self.assertEqual(b.budget, float(session.LAND_CAS_BUDGET_SECONDS))

    def test_the_estimate_uses_the_slowest_attempt_not_the_mean(self):
        """A budget that averages away one slow run overruns the allowance it advertises —
        and the allowance is what the queue behind this lane is paying for.

        The numbers are chosen so the two rules DISAGREE — with 61s spent and a 100s
        allowance, the worst-case estimate (60s) does not fit and the mean (30.5s) does.
        A test where both rules stop proves only that some rule was applied."""
        c = _Clock()
        b = session._CasBudget(min_attempts=1, max_attempts=99, budget=100.0, now=c)
        it = b.attempts()
        next(it)
        c.advance(1.0)          # a fast first attempt
        next(it)
        c.advance(60.0)         # then a slow one: 61s spent, 39s left, 60s needed
        with self.assertRaises(StopIteration):
            next(it)
        self.assertEqual(b.stopped_by, "budget")

    def test_exhaustion_says_which_limit_stopped_it(self):
        """'the trunk outruns this gate' and 'we were unlucky' need different responses,
        and the old line — 'kept moving under us after 3 attempts' — could not tell them
        apart because 3 was the only number it ever printed."""
        c = _Clock()
        capped = session._CasBudget(now=c)
        _spend(capped, 1.0, c)
        self.assertIn("cap", capped.exhausted_line("main"))

        c2 = _Clock()
        spent = session._CasBudget(now=c2)
        _spend(spent, 202.6, c2)
        line = spent.exhausted_line("main")
        self.assertIn("retry budget", line)
        self.assertIn("session.py merge", line, "a refusal must still name its next verb")


@unittest.skipUnless(GIT, "git not available")
class ContendedLandTest(unittest.TestCase):
    """The behaviour, driven through the real lane lander against a real git repo.

    On the honest limit of this fixture: WI-0266 asks for "N concurrent lands with a
    deliberately slow gate, asserting every one eventually lands". These drive ONE land
    against a deterministic competitor rather than racing N real processes. The contention
    is real (the trunk genuinely advances between gate and swap, and the CAS genuinely
    fails), but the concurrency is simulated — `session.ROOT` and its siblings are module
    globals, so in-process threads would race on them, and a subprocess race is both slow
    and nondeterministic in a gate that must be reliable. The property that actually
    distinguishes the fix from the defect is the NUMBER OF LOST SWAPS SURVIVED, and that
    is deterministic here. A true N-process race remains manual (ADR-0114's author did one
    for the queue and recorded it in the ADR rather than the suite).
    """

    def setUp(self):
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\n", encoding="utf-8")
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        (self.main / "role.md").write_text("**Version:** v1.0.0\n", encoding="utf-8")
        (self.main / "sessions" / "journal").mkdir(parents=True)
        (self.main / "sessions" / "pre-journal-archive.md").write_text("", encoding="utf-8")
        (self.main / "STATUS.md").write_text(
            "---\nid: test\nversion: v0\nlast_active: 2026-01-01\nfocus: x\n"
            "blocked: false\n---\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR",
                       "CONFIG_PATH", "CFG", "_LAND_GATE_DEPTH")}
        session._LAND_GATE_DEPTH = 0
        self._point_at_lane()

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _point_at_lane(self):
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.lane / "sessions" / "journal"
        session.ARCHIVE = self.lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.CONFIG_PATH = self.lane / "session.config.json"
        session.CFG = {
            "handoff": self.lane / "session-handoff.md",
            "status": self.lane / "STATUS.md",
            "role_doc": self.lane / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect", "architect_id": "test-arch",
            "machine_map": {}, "user_name": "operator", "inbox": None,
            "trunk": "main", "branch_sessions": False, "gate": PASS_GATE,
        }

    def _lane_journal(self, did):
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": "contended lane",
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": "2026-07-22T16:00:00+00:00",
            "ended": "2026-07-22T17:00:00+00:00",
            "claude-session-id": "csid-" + did,
        })

    def _competitor(self, n, code=False):
        """Advance the trunk the way the real one advances under a held land gate: a
        `poga work`-shaped store write, which commits to the main checkout OUTSIDE the
        gate by design (ADR-0073) and is the majority of real arrivals."""
        name = "mod.py" if code else "work-items/WI-9999-note.md"
        p = self.main / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"competitor {n}\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", f"chore(work-items): note {n}")

    def _land_losing(self, losses, **kw):
        """Land, with a competitor that advances the trunk after each of the first
        `losses` gate runs and then stops. Returns (outcome, gate_runs, output)."""
        self._lane_journal("20260722T1600Z-runner-aaaa")
        (self.lane / "work.txt").write_text("lane work\n", encoding="utf-8")
        real_gate = session._gate_commit
        runs = {"n": 0}

        def racing_gate(sha, skip=frozenset()):
            # WI-0347 gave the gate a per-command skip plan. Passed straight
            # through rather than dropped: this spy races the CAS, it does not
            # change what the gate under it decides to run.
            ok, rep = real_gate(sha, skip)
            runs["n"] += 1
            if runs["n"] <= losses:
                self._competitor(runs["n"], code=kw.pop("code", False))
            return ok, rep

        out = []
        with mock.patch.object(session, "_gate_commit", side_effect=racing_gate), \
                mock.patch.object(session, "_refuse_git_on_fixture", create=True), \
                mock.patch.object(session, "role_doc_version", return_value="1.0.0"), \
                mock.patch.object(session, "_recompile_main_checkout", return_value=None), \
                mock.patch.object(session, "_sync_main_checkout", return_value=None), \
                mock.patch.object(session, "_dispatch_after_land", return_value=None), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(
                    " ".join(str(x) for x in a))):
            outcome = session._land_worktree_lane(
                None, None, "1.0.0", commit_msg="lane work", push=False, **kw)
        return outcome, runs["n"], "\n".join(out)

    def test_a_land_survives_more_than_three_lost_swaps(self):
        """THE WI-0266 CASE. Four lost swaps used to be one more than the budget allowed,
        so a lane whose work was perfectly good — every gate green — landed nothing and
        reported 'main kept moving under us after 3 attempts'. It must now land."""
        outcome, gate_runs, out = self._land_losing(4)
        self.assertTrue(outcome, f"a land that lost 4 swaps must still land:\n{out}")
        self.assertGreater(gate_runs, 3,
                           "control: the loop really did run past the old fixed budget, "
                           "so this test exercised the fix rather than an early win")

    def test_the_old_fixed_budget_still_gives_up_at_three(self):
        """The control for the test above, and the backwards-compatibility guarantee: an
        explicit `attempts=3` must behave exactly as it did before this change, or the
        ~45 existing call sites that pass a count have quietly changed meaning."""
        outcome, gate_runs, out = self._land_losing(4, attempts=3)
        self.assertFalse(outcome)
        self.assertEqual(outcome.reason, "contended")
        self.assertEqual(gate_runs, 3)

    def test_giving_up_is_still_possible_and_still_exits_2(self):
        """A budget must not turn a failure into a hang. WI-0263 made a failed land exit
        2; a retry loop that never terminates would undo that by never reaching it."""
        outcome, _, out = self._land_losing(999)
        self.assertFalse(outcome, "a land that never wins must still give up")
        self.assertEqual(outcome.reason, "contended")
        self.assertEqual(outcome.exit_code, 2)
        self.assertIn("BLOCKED", out)

    def test_the_retry_line_does_not_claim_another_lane_landed(self):
        """Since ADR-0114 serialized the land, 'another lane landed first' is usually
        FALSE — the gate makes lane lands mutually exclusive, and what a holder loses to
        is mostly an unserialized `poga work` store commit (WI-0272). A diagnostic that
        names the wrong culprit sends the reader to the wrong mechanism."""
        _, _, out = self._land_losing(1)
        self.assertIn("re-checking outside the queue", out)
        self.assertNotIn("another lane landed first", out)
        self.assertNotIn("another session landed first", out)

    def test_a_moved_trunk_is_not_reported_as_a_refused_swap(self):
        """ADR-0124 D1 split one event into two, and the words have to follow.

        Before, a trunk that moved while the lane gated was discovered by a compare-and-
        swap that git refused — so 'lost the swap' was literally true. Now the move is
        caught by a comparison BEFORE anything is written, and the swap is never
        attempted. Reporting that as a refused swap would describe the one condition
        (a CAS refused under the lock with the tip verified a line earlier) that nothing
        in the current design produces — and so would spend the reader's attention on an
        anomaly when what happened was routine."""
        _, _, out = self._land_losing(1)
        self.assertIn("nothing was written", out)
        self.assertNotIn("REFUSED", out)
        self.assertNotIn("lost the swap", out)

    def test_the_retry_line_says_whether_code_arrived(self):
        """The one distinction that changes what a reader should do: a docs/store commit
        arriving is routine, a code commit arriving means the tree you are about to land
        on is genuinely different from the one you gated."""
        _, _, docs_out = self._land_losing(1)
        self.assertIn("none of them code changes", docs_out)


class NoLanderCarriesAHardcodedBudgetTest(unittest.TestCase):
    """The structural half (`add-structural-guard-on-recurrence`).

    The defect was one bare `attempts: int = 3` per lander, in three places, none of them
    reachable from a caller — precisely the shape that gets re-added by someone copying an
    adjacent signature. WI-0263 fixed a three-places-one-default defect in these same
    functions and shipped an AST tripwire for it; this is the same guard for the same
    reason, on the parameter next door."""

    LANDERS = ("_land_candidate", "_land_worktree_lane", "_integrate_trunk_with_remote")

    def test_no_lander_defaults_attempts_to_a_number(self):
        tree = ast.parse(harness_fixture.harness_source())
        checked = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in self.LANDERS:
                continue
            checked.append(node.name)
            args = node.args.args + node.args.kwonlyargs
            defaults = ([None] * (len(node.args.args) - len(node.args.defaults))
                        + list(node.args.defaults))
            for arg, default in zip(node.args.args, defaults):
                if arg.arg != "attempts":
                    continue
                self.assertIsNotNone(default, f"{node.name}: `attempts` lost its default")
                self.assertIsInstance(
                    default, ast.Constant,
                    f"{node.name}: unexpected default form for `attempts`")
                self.assertIsNone(
                    default.value,
                    f"{node.name} hardcodes a retry budget again "
                    f"(`attempts={default.value!r}`). The budget is derived from the "
                    f"gate's measured cost — pass None and let `_cas_budget` size it "
                    f"(WI-0266).")
        self.assertCountEqual(checked, self.LANDERS,
                              "a lander was renamed or removed — this guard now watches "
                              "a function that no longer exists, which is worse than no "
                              "guard because it still passes")


if __name__ == "__main__":
    unittest.main()
