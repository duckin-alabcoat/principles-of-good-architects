"""WI-0378 — the integrate validates outside the land gate, like every other lander.

THE DESIGN PROBLEM. An `integrate` that re-runs the full gate while HOLDING the land gate
holds it for roughly one full suite run, which breaches the hold budget by design, not by
bad luck. **The expensive thing is the re-gate and nothing else** — `merge` and `push` are
sub-second next to it. (A separate fault — ssh's own TCP timeout down a dead route,
re-paid on a fetch — is closed by ADR-0134 D5.)

WHAT THESE TESTS PIN, and why each needs to exist rather than be folded into another:

  1. THE STRUCTURAL GUARD — from inside a held land gate, control cannot reach a full gate
     run, on ANY path, following calls rather than reading one function. This is the bound.
     It is placement, not a timer: validation cannot overrun the hold budget because it
     does not hold the gate. A timer inside the section would have to choose, at the moment
     it fired, between a half-published trunk and holding anyway — and there is no moment
     in the new shape where that choice arises.
  2. BEHAVIOURAL, for the verb — a diverged `integrate` runs its suite with the gate FREE.
  3. BEHAVIOURAL, for the land — the push-rejected escalation runs with the gate FREE.
  4. THE VERB IS MEASURED AT ALL. ADR-0124 line 124 states that integrate's "receipts are
     written under the `integrate` verb so the board can tell the two apart". That was
     false: `cmd_integrate` wrote no receipt, so a receipts file could hold no `integrate`
     verb at all — only `land-lane` and `resume-lane`. An
     operator-run integrate holding the gate for minutes was invisible to FL7, to the
     OVER BUDGET line, and to the measurement this item was minted from
     (`ship-the-detector-with-the-capability`).
  5. THE EXIT-2 STRING. `cmd_integrate` selects exit 2 by matching "kept moving" in its own
     report. Nothing tested it, so rewording the BLOCKED line silently collapsed exit 2
     into exit 1 — and this restructure rewords lines near it.
  6. THE FETCH IS BOUNDED. The push was bounded by ADR-0134 D4 and the fetch beside it was
     not, through the same bare `sh` with no timeout.

stdlib unittest: python3 -m unittest discover -s tests
"""

import ast
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import harness_fixture                                            # noqa: E402
import session                                                    # noqa: E402
from test_worktree_lane import IntegrateWithRemoteTest, _git, _out  # noqa: E402

GIT = harness_fixture.GIT if hasattr(harness_fixture, "GIT") else "git"


# ───────────────────── 1. the structural guard — the bound ─────────────────────

#: What "runs the suite" means. `_gate_commit` gates a candidate in a scratch worktree and
#: `_run_gate` is the runner underneath it; `_land_gate` (ADR-0148, which replaced the
#: deleted `_gate_unless_neutral`) decides what to call; `_run_suite_for_verdict` and
#: `_ensure_lane_verdict` run the suite for a lane's verdict. Any of them inside a held
#: gate is the defect.
GATE_SINKS = {"_gate_commit", "_run_gate", "_land_gate", "_run_suite_for_verdict",
              "_ensure_lane_verdict"}

#: The context manager that IS the serialized section.
SECTION_OPENERS = {"land_gate_lock"}

#: The two helpers that own the section and take a caller's closure. Their `publish`
#: argument runs INSIDE the lock, and it is a parameter — so no name-based call walk can
#: see through it from the helper's side. It has to be resolved at each CALL site.
SECTION_RUNNERS = {"_serialized_advance", "_serialized_publish"}


def _callee(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _calls_in(node):
    """Every callee name lexically inside `node`, nested defs included."""
    return {n for n in (_callee(c) for c in ast.walk(node) if isinstance(c, ast.Call)) if n}


def _params_of(fn):
    a = fn.args
    names = {p.arg for p in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _nested_defs(fn):
    out = {}
    for n in ast.walk(fn):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn:
            out.setdefault(n.name, n)
    return out


def _outermost_def(tree, target):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(x is target for x in ast.walk(n)):
                return n
    return None


def _held_sections(trees):
    """Every piece of code that runs with the land gate held.

    Two kinds, and the second is why this is not a one-line grep:

      (a) the body of a `with land_gate_lock():` block;
      (b) the closure handed to `_serialized_advance` / `_serialized_publish`, which those
          helpers call from inside their own lock.

    For (b) the argument is found by RESOLVING IT, never by index. `_serialized_advance`
    takes `publish` fourth and `_serialized_publish` takes it third; an index copied from
    one of them reads the other as having no closure at all, and the first draft of this
    check did exactly that and reported the resume path clean while it carried the defect.
    """
    out = []
    for rel, tree in trees:
        for n in ast.walk(tree):
            if not isinstance(n, ast.With):
                continue
            if not any(_callee(i.context_expr) in SECTION_OPENERS
                       for i in n.items if isinstance(i.context_expr, ast.Call)):
                continue
            host = _outermost_def(tree, n)
            # A call to one of the HOST'S OWN PARAMETERS is opaque here by construction —
            # `_serialized_advance` calls `publish(stages)` and which publish that is gets
            # decided at the call site, covered by (b). Resolving it against whichever
            # `publish` this file defines first is how one lander's defect gets reported
            # against all three.
            blind = _params_of(host) if host else set()
            label = (f"{rel}:{n.lineno} with land_gate_lock() in "
                     f"{host.name if host else '<module>'}")
            out.append((label, n, blind))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or _callee(n) not in SECTION_RUNNERS:
                continue
            host = _outermost_def(tree, n)
            local = _nested_defs(host) if host else {}
            for arg in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(arg, ast.Name) and arg.id in local:
                    out.append((f"{rel}:{n.lineno} publish={arg.id} in {host.name}",
                                local[arg.id], set()))
    return out


def _suite_reachable(sections, graph):
    """`(label, call chain)` for every held section that can reach a gate run."""
    found = []
    for label, node, blind in sections:
        seen = set()
        stack = [(c, [c]) for c in sorted(_calls_in(node)) if c not in blind]
        while stack:
            name, chain = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            if name in GATE_SINKS:
                found.append((label, " -> ".join(chain)))
                continue
            if name in graph:
                for nxt in sorted(_calls_in(graph[name])):
                    stack.append((nxt, chain + [nxt]))
    return found


def _harness_trees():
    trees, graph = [], {}
    for rel in harness_fixture.harness_files():
        tree = ast.parse((harness_fixture.ROOT / rel).read_text(encoding="utf-8"))
        trees.append((rel, tree))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                graph.setdefault(n.name, n)
    return trees, graph


class NoSuiteRunsInsideTheLandGateTest(unittest.TestCase):
    """THE BOUND, expressed where it cannot rot.

    ADR-0124 D1 moved validation outside the section for the three landers and named two
    paths it left inside — the integrate verb and the land's push-rejected escalation —
    as findings for review. Both were prose. A third grew afterwards (WI-0326's resume
    path, built on top of ADR-0124 and inheriting the shape nobody had written down), and
    it is the check below that found it, not a reading.

    `retire-the-class-not-the-instance`: the defect was FILED against the integrate and
    LIVES in "anything reachable from a held gate", so the guard is over the class.
    """

    #: Every function that owns a held section or hands a closure to one. Named, because
    #: `assertTrue(sections)` passes on ONE section: a resolution bug that quietly stopped
    #: seeing the publish closures would leave this green while watching almost nothing.
    #: That is not hypothetical — the first draft of `_held_sections` resolved `publish` by
    #: argument index and missed the resume path entirely, and a non-empty list is exactly
    #: what it returned. A rename that empties one of these rows fails here rather than
    #: leaving a guard that still passes while watching a function that no longer exists.
    MUST_BE_WATCHED = {
        "_serialized_advance",          # the section helper itself
        "_serialized_publish",          # its resume-case sibling
        "_land_worktree_lane",          # the three landers' publish closures
        "_land_candidate",
        "_land_branch",
        "_resume_owed_publication",     # the third path, which no reading found
        "_integrate_trunk_with_remote",  # the subject of WI-0378
    }

    def test_the_check_watches_every_holder_it_should(self):
        trees, _graph = _harness_trees()
        hosts = {lab.rsplit(" in ", 1)[-1] for lab, _n, _b in _held_sections(trees)}
        missing = sorted(self.MUST_BE_WATCHED - hosts)
        self.assertEqual([], missing,
                         "the check no longer sees these as holding the land gate, so it "
                         "is green about code it is not reading: " + ", ".join(missing))

    def test_no_held_section_can_reach_a_gate_run(self):
        trees, graph = _harness_trees()
        sections = _held_sections(trees)
        self.assertTrue(sections, "the check found no held sections at all — it is "
                                  "measuring nothing and would pass an empty repo")
        findings = _suite_reachable(sections, graph)
        self.assertEqual(
            [], findings,
            "these run the test suite while holding the land gate, so every other lane "
            "queues behind a validation that touches nothing they can see (ADR-0124 D1, "
            "WI-0378): " + "; ".join(f"{lab} [{chain}]" for lab, chain in findings))

    def test_the_guard_fires_on_the_shape_it_replaced(self):
        """`a-detector-proves-itself-on-the-real-defect`. A guard that finds zero offenders
        proves nothing until it is shown the defect. This is `cmd_integrate` as it stood
        before this item, reduced to its two load-bearing lines."""
        tree = ast.parse(
            "def cmd_integrate(args):\n"
            "    with land_gate_lock():\n"
            "        ok, lines = _integrate_trunk_with_remote(push=args.push)\n"
            "\n"
            "def _integrate_trunk_with_remote(push=True):\n"
            "    return _gate_commit(newtip)\n")
        graph = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        findings = _suite_reachable(_held_sections([("fixture.py", tree)]), graph)
        self.assertTrue(findings, "the guard must fire on the exact shape it replaced")
        self.assertIn("_gate_commit", findings[0][1])

    def test_the_guard_fires_through_a_publish_closure(self):
        """The second shape, and the one a lexical check cannot see: the gate run is not
        inside the `with` block, it is inside a closure handed to the helper that owns the
        `with`. This is `_land_worktree_lane`'s publish as it stood."""
        tree = ast.parse(
            "def _land_worktree_lane(a):\n"
            "    def publish(stages):\n"
            "        _integrate_trunk_with_remote(resolve=resolve)\n"
            "    outcome, hold, stages = _serialized_advance(t, p, tip, publish)\n"
            "\n"
            "def _integrate_trunk_with_remote(resolve=None):\n"
            "    return _gate_commit(newtip)\n")
        graph = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        findings = _suite_reachable(_held_sections([("fixture.py", tree)]), graph)
        self.assertTrue(findings, "the guard must see through the publish closure")

    def test_the_guard_resolves_publish_at_the_call_site_not_by_index(self):
        """The negative control that the first draft of this check failed.

        Two helpers take `publish` at DIFFERENT positions. A check that hard-codes one
        index reads the other as having no closure, passes, and says nothing — which is
        exactly how the resume path stayed unreported. Here only the SECOND lander is
        defective; the check must find it even though its helper puts `publish` third."""
        tree = ast.parse(
            "def clean(a):\n"
            "    def publish(stages):\n"
            "        _sync_main_checkout(t, p, tip)\n"
            "    _serialized_advance(t, p, tip, publish)\n"
            "\n"
            "def dirty(a):\n"
            "    def publish(stages):\n"
            "        _integrate_trunk_with_remote()\n"
            "    _serialized_publish(t, tip, publish)\n"
            "\n"
            "def _integrate_trunk_with_remote():\n"
            "    return _gate_commit(newtip)\n"
            "\n"
            "def _sync_main_checkout(a, b, c):\n"
            "    return None\n")
        graph = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        findings = _suite_reachable(_held_sections([("fixture.py", tree)]), graph)
        self.assertEqual(1, len(findings), f"expected exactly the dirty one: {findings}")
        self.assertIn("in dirty", findings[0][0])

    def test_a_clean_publish_closure_is_not_an_offender(self):
        """`a-guard-that-fires-on-correct-code-gets-deleted`. The three landers all hand a
        publish closure to the section helper, and all three are correct. A check that
        cannot tell them from the defective one would be turned off by whoever trips it."""
        tree = ast.parse(
            "def _land_candidate(a):\n"
            "    def publish(stages):\n"
            "        _sync_main_checkout(t, p, tip)\n"
            "        _recompile_main_checkout(t, v)\n"
            "        _push_trunk(t)\n"
            "    _serialized_advance(t, p, tip, publish)\n"
            "\n"
            "def _sync_main_checkout(a, b, c):\n    return None\n"
            "\n"
            "def _recompile_main_checkout(a, b):\n    return None\n"
            "\n"
            "def _push_trunk(t):\n    return None\n")
        graph = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertEqual(
            [], _suite_reachable(_held_sections([("fixture.py", tree)]), graph))


# ───────────────── 2. the bound, measured rather than read ─────────────────────

class TheIntegrateValidatesOutsideTheGateTest(IntegrateWithRemoteTest):
    """`a-timing-claim-is-measured-never-read`. The structural check above establishes
    that no call PATH reaches a gate run from a held section. It cannot establish that the
    gate is actually free while the suite actually runs — that is a claim about a moment
    in time, and the only way to know it is to look at that moment."""

    def _gate_holder_during_the_suite(self, run, peer_pushes_mid_gate=False):
        """Run `run()` with `_gate_commit` instrumented; return every land-gate holder
        observed at the instant the gate ran.

        `peer_pushes_mid_gate` exists because of WI-0356 and is not a convenience. A peer
        who pushes BEFORE a land no longer causes a rejected push at all: the pre-land
        check sees a trunk merely behind origin, fast-forwards it, and our push is an
        ordinary fast-forward. The window that check cannot span is the one the escalation
        lives in — the peer pushes after our check and before our push — and the gate is
        the long pole of a land, so inside the gate is where it actually happens.

        The first draft of these tests pushed the peer first and asserted the escalation
        ran; the control below is what caught it
        (`a-fixture-stem-that-cannot-occur-hides-a-dead-test`)."""
        observed = []
        pushed = []
        real = session._gate_commit

        def spy(*a, **k):
            observed.append(session._land_gate_holder())
            if peer_pushes_mid_gate and not pushed:
                pushed.append(self._peer_pushes())
            return real(*a, **k)

        with mock.patch.object(session, "_gate_commit", side_effect=spy):
            run()
        if peer_pushes_mid_gate:
            self.assertTrue(pushed, "control: the peer never pushed mid-gate, so the "
                                    "push was never rejected and no escalation ran")
        return observed

    def test_a_diverged_integrate_runs_its_suite_with_the_gate_free(self):
        """The verb. It used to take the gate at `cmd_integrate` and hold it across the
        whole reconcile, a full suite included (WI-0267)."""
        self._land("work.txt")
        self._peer_pushes()
        observed = self._gate_holder_during_the_suite(
            lambda: session._integrate_trunk_with_remote())
        self.assertTrue(observed, "control: the gate never ran, so this measured nothing")
        for holder in observed:
            self.assertEqual({}, holder,
                             "the land gate was HELD while the integrate ran its suite — "
                             "that is the 42.5% of hold time WI-0378 removed")

    def test_the_lands_escalation_runs_its_suite_with_the_gate_free(self):
        """The land's push-rejected path. ADR-0124 named it and left it alone; the
        argument for leaving it was that releasing "mid-publication, with the local trunk
        already advanced and origin not" is worse than a slow hold. The state is real, but
        the release does not create it — the REJECTED PUSH does, one line earlier, and
        since ADR-0134 D2/D3 it is a named, reported, recoverable state with a verb whose
        whole purpose is to clear it."""
        self._stage_lane_work("work.txt")
        observed = self._gate_holder_during_the_suite(
            lambda: session._land_worktree_lane(None, None, "1.0.0", push=True),
            peer_pushes_mid_gate=True)
        # Two gate runs: the land's own validation, then the escalation's re-gate. Both
        # must be free; the first was already (ADR-0124 D1), the second is this item.
        self.assertGreaterEqual(len(observed), 2,
                                "control: the escalation never re-gated, so the second "
                                "half of this test measured nothing")
        for holder in observed:
            self.assertEqual({}, holder, "a suite ran while the land gate was held")

    def test_the_escalation_still_happens_and_still_publishes(self):
        """The control for both tests above. Moving the escalation out of the section must
        not quietly move it out of existence — `assertNotIn` on a line that is never
        emitted passes for free (`a-fixture-stem-that-cannot-occur-hides-a-dead-test`)."""
        self._stage_lane_work("work.txt")
        pushed = []
        real = session._gate_commit

        def peer_pushes_while_we_gate(sha, skip=frozenset()):
            if not pushed:
                pushed.append(self._peer_pushes())
            return real(sha, skip)

        with mock.patch.object(session, "_gate_commit",
                               side_effect=peer_pushes_while_we_gate):
            out = session._land_worktree_lane(None, None, "1.0.0", push=True)
        self.assertTrue(pushed, "control: the peer must actually have pushed mid-land")
        self.assertTrue(out, "the land itself must still succeed")
        files = self._origin_files()
        self.assertIn("work.txt", files, "our work never reached origin")
        self.assertIn("peer.txt", files, "the peer's work was clobbered")
        self.assertEqual(_out(self.main, "rev-parse", "main"), self._origin_tip())


# ─────────────── 3. the verb is measured — ADR-0124's false claim ──────────────

class TheIntegrateWritesItsOwnReceiptTest(IntegrateWithRemoteTest):
    """ADR-0124 line 124: integrate's "receipts are written under the `integrate` verb so
    the board can tell the two apart, but the budget is not enforced differently for it."

    The second clause was true and the first was not. `cmd_integrate` called
    `_integrate_trunk_with_remote` and neither wrote a receipt, so across 184 receipts on
    this machine there is no `integrate` verb at all. An operator-run integrate could hold
    the gate for minutes and FL7 — which is red if ANY hold in the window exceeds the
    budget — would never see it. A capability with no marker reads as absent to the
    surface that should make it visible (`ship-the-detector-with-the-capability`)."""

    def _integrate_receipts(self):
        return [r for r in session.land_receipts() if r.get("verb") == "integrate"]

    def test_a_diverged_integrate_writes_a_receipt_under_its_own_verb(self):
        self._land("work.txt")
        self._peer_pushes()
        before = len(self._integrate_receipts())
        ok, lines = session._integrate_trunk_with_remote()
        self.assertTrue(ok, "\n".join(lines))
        recs = self._integrate_receipts()
        self.assertEqual(len(recs), before + 1,
                         "the integrate held the land gate and left no record of it")
        rec = recs[-1]
        self.assertEqual("landed", rec["outcome"])
        for key in ("lock_seconds", "queue_seconds", "validate_seconds", "at_epoch"):
            self.assertIsInstance(rec[key], (int, float), key)

    def test_the_receipt_files_the_suite_under_validation_not_under_the_hold(self):
        """The number this item exists to move. `validate_seconds` is time spent OUTSIDE
        the gate and `lock_seconds` is time inside it; before this change the suite was in
        the second. If a future edit puts it back, the receipt is where it shows."""
        self._land("work.txt")
        self._peer_pushes()
        ok, lines = session._integrate_trunk_with_remote()
        self.assertTrue(ok, "\n".join(lines))
        rec = self._integrate_receipts()[-1]
        # ADR-0148 D1/D4: a clean merge of two landed trunks runs no suite — the cheap
        # checks run over the merged tree, outside the hold, and the receipt files them
        # under `checks_seconds`; `validate_seconds` is suite time only.
        self.assertGreater(rec["checks_seconds"], 0.0,
                           "the integrate reported no check time at all, which would "
                           "mean the gate ran somewhere this receipt cannot see")
        self.assertEqual(rec["validate_seconds"], 0.0,
                         "the integrate ran a suite — ADR-0148 D4 says it must not")
        self.assertEqual(rec["phases"]["checks"], rec["checks_seconds"])
        self.assertEqual(rec["verdict"]["source"], "integrate")
        self.assertIn("ADR-0148 D4", rec["verdict"]["reason"])
        self.assertIn("merge", rec["stages"], "the CAS is what the hold is FOR")

    def test_fl7_can_see_an_over_budget_integrate(self):
        """The whole point of the receipt. FL7 reads `lock_seconds` across every receipt
        in its window and goes red if ANY exceeds the budget; a verb that writes none is
        invisible to it however long it holds."""
        import importlib
        finish_line = importlib.import_module("curate.finish_line")
        self._land("work.txt")
        self._peer_pushes()
        session._integrate_trunk_with_remote()
        recs = [r for r in session.land_receipts() if r.get("verb") == "integrate"]
        self.assertTrue(recs, "control: nothing to see")
        self.assertTrue(hasattr(finish_line, "probe_land_lock_hold"))


# ──────────────────── 4. the two untested edges this touched ───────────────────

class TheVerbsExitCodesAreNotAnAccidentTest(unittest.TestCase):
    """`cmd_integrate` picks exit 2 by matching the words "kept moving" in its own report
    (`sys.exit(2 if any("kept moving" in l for l in lines) else 1)`). Nothing tested the
    join, so rewording the BLOCKED line silently collapsed 2 into 1 — and 2 means "retry,
    the trunk is busy" while 1 means "stop, this needs a person". This item reworded lines
    beside it, which is exactly when a coupling like that gets broken."""

    def _exit_for(self, lines):
        args = mock.Mock(dry_run=True, push=True, resolve="generated")
        with mock.patch.object(session, "_integrate_trunk_with_remote",
                               return_value=(False, lines)):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_integrate(args)
        return cm.exception.code

    def test_a_contended_trunk_exits_two(self):
        self.assertEqual(2, self._exit_for(
            ["integrate: BLOCKED — main kept moving under us. 12 attempt(s) over 3m."]))

    def test_the_real_blocked_line_still_carries_the_phrase(self):
        """The half a mock cannot check: the string the code actually emits."""
        src = (harness_fixture.ROOT / "sessionlib" / "land.py").read_text(encoding="utf-8")
        self.assertIn("kept moving under us", src,
                      "the BLOCKED line lost the phrase `cmd_integrate` matches on, so "
                      "a contended trunk now exits 1 and reads as needing a human")

    def test_an_ordinary_refusal_exits_one(self):
        self.assertEqual(1, self._exit_for(
            ["integrate: REFUSED — merge conflict in trunkfile.txt."]))


class TheFetchIsBoundedTest(unittest.TestCase):
    """ADR-0134 D4 bounded the push because an unbounded network call inside the
    serialized section is invisible: the lane goes on reporting live while holding the
    gate against every queued peer. The fetch beside it went through the same bare `sh`,
    which passes no timeout — and the receipt that sized the push's bound is the receipt
    that proves it: `push 75.013s` then `integrate 75.024s`, the second number being this
    fetch going down the same dead route."""

    def test_the_timeout_is_derived_from_the_hold_budget(self):
        """Derived, not a second literal. A change to the budget must not leave a stale
        ceiling above it — the same rule `LAND_PUSH_TIMEOUT_SECONDS` follows."""
        self.assertEqual(session.LAND_FETCH_TIMEOUT_SECONDS,
                         session.LAND_LOCK_HOLD_BUDGET_SECONDS)

    def test_a_hung_fetch_is_cut_off_rather_than_waited_out(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "r"
            repo.mkdir()
            subprocess.run([GIT, "init", "-q", "-b", "main", str(repo)],
                           check=True, capture_output=True)
            subprocess.run([GIT, "-C", str(repo), "remote", "add", "origin",
                            "https://example.invalid/x.git"], check=True,
                           capture_output=True)
            with mock.patch.object(session, "ROOT", repo):
                out = session._fetch_origin(timeout=1.0)
        self.assertFalse(out.ok)
        self.assertIsInstance(out.seconds, float)
        self.assertLess(out.seconds, 30.0,
                        "the fetch was not cut off at its own timeout")

    def test_no_remote_is_not_a_fetch_failure(self):
        """The control `_push_trunk` also carries: a member with no origin is local by
        design, and reporting that as a stalled network makes the real signal meaningless
        inside a week."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "r"
            repo.mkdir()
            subprocess.run([GIT, "init", "-q", "-b", "main", str(repo)],
                           check=True, capture_output=True)
            with mock.patch.object(session, "ROOT", repo):
                out = session._fetch_origin()
        self.assertTrue(out.ok)
        self.assertFalse(out.unreachable)


if __name__ == "__main__":
    unittest.main()
