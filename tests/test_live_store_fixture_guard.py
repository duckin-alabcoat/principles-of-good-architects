"""The detector for `neutralize_live_store` (WI-0270) — the anchor nobody can forget.

`neutralize_live_store` shipped for WI-0295 as the only neutralizer in `coord_fixture`
with NO detector. The gap was not academic: it was missing the anchor that mattered most,
and nothing in the suite could say so.

`SESSION_STATE_DIR` is a module global computed at IMPORT from the real repo. A fixture
that rebinds `ROOT` afterwards does not move it — the directory was already resolved. So
`test_attention`'s `_beat()` drove the real `cmd_heartbeat` with a session id of
`"testsession"` and wrote `testsession.live` into the OPERATOR'S `.session-state/`.

WHAT THAT COSTS, which is what makes this a guard rather than tidiness. A `.live` file
bearing a fresh beat and no `.ended` is indistinguishable from a working session BY
DESIGN — that is the entire purpose of the file. So a fixture's heartbeat does not look
like litter, it looks like a colleague, and `recover-lanes` correctly refuses to land a
lane it cannot prove is over. Measured 2026-09-04 (session ~185), four lanes held,
verbatim `[LIVE] testsess beat 17.7h ago -- inside its 48h grace; not provably dead`.
Still true eight days later on `poga-7`, whose real session stamped `.ended` that morning.

Note the DIRECTION, against WI-0249's. That escape would have let a test KILL a live
session; this one lets a test PRESERVE a dead one. Both are the fixture reaching live
state, and the preserving direction is by far the quieter: nothing fails, no test goes
red, work simply stops being recoverable and no surface says why. The loud failure is the
one that gets fixed; this is the argument for detecting the silent one mechanically.

THE RULE IS STATED ON THE HAZARD, NOT ON THE HELPER — the one deliberate departure from
`test_dispatch_fixture_guard`, which demands `neutralize_dispatch_env` by name. It can,
because clearing an environment variable has exactly one correct implementation.
Redirecting a directory does not: thirteen modules already pin `SESSION_STATE_DIR` to a
tmpdir of their own, correctly, and a rule that flagged all thirteen would be a rule that
gets weakened rather than obeyed — which is how a guard becomes decoration. So what is
required here is that THE ANCHOR IS REDIRECTED, by the shared fixture or by the module's
own explicit pin. A module that does neither is the hole.
"""

import ast
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
import harness_fixture  # noqa: E402
import session  # noqa: E402

#: The global that names the live coordination store. ONE anchor, deliberately.
#: There were three: `SESSION_BANNER_FILE` and `GUARD_FIRINGS_FILE` were paths computed
#: FROM `SESSION_STATE_DIR` at import, so a fixture that redirected the directory — the
#: thing this rule asks for — did not move them. That is not a second defect, it is this
#: one at one remove, and it is why the rule could read green over a live escape: measured
#: 2026-09-13, 35 writes a run of the real `announce.txt` from a module whose `setUp` pins
#: the anchor correctly. Both are now NAMES joined to the directory at call time, so the
#: anchor list cannot drift from the file list again (WI-0286 / ADR-0133).
LIVE_STORE_ANCHORS = ("SESSION_STATE_DIR",)

#: A read of one of those globals inside a function body.
STORE_READ = re.compile(r"\b(" + "|".join(LIVE_STORE_ANCHORS) + r")\b")

#: Functions that reach the live store WITHOUT naming an anchor in their own body — a
#: caller whose callee does. Derivation cannot see these, so they are named here, each
#: with the reader it reaches. This is the ONLY hand-maintained half.
TRANSITIVE_REACHERS = (
    "cmd_heartbeat",   # → _sidecar_write — THE defect: plants a .live in the real store
    "cmd_start",       # → _sidecar_write + _write_prep_marker
    "cmd_record_end",  # → _sidecar_write (.ended)
    "cmd_check_bash",  # → _log_guard_firing — appends to the real guard-firings.jsonl
)


def _store_reading_functions(src: str) -> set:
    """Every top-level function in `src` whose body names a live-store anchor.

    DERIVED, NOT REMEMBERED, for the reason WI-0243 established one guard over: a
    hand-written list of what the code does is a copy of the code, and it drifts. Read the
    source instead and the next reader someone adds is covered the day it lands."""
    lines = src.splitlines()
    found = set()
    for node in ast.parse(src).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = "\n".join(lines[node.lineno - 1:node.end_lineno or node.lineno])
        if STORE_READ.search(body):
            found.add(node.name)
    return found


def _reaching_names() -> list:
    return sorted(_store_reading_functions(harness_fixture.harness_source())
                  | set(TRANSITIVE_REACHERS))


#: A real call into a path that resolves the live store.
REACHES_LIVE_STORE = re.compile(r"session\.(" + "|".join(_reaching_names()) + r")\s*\(")
#: The shared fixture — by either name. `point_store_at` (ADR-0148 D3, WI-0427) is
#: `neutralize_live_store` plus the dispatch-env clear, not a second implementation.
APPLIES_FIXTURE = re.compile(r"(?:neutralize_live_store|point_store_at)\s*\(")
#: ...or the module's own explicit pin of the directory, which is equally safe.
PINS_ANCHOR_BY_HAND = re.compile(r"session\.SESSION_STATE_DIR\s*=")


def _modules():
    for p in sorted(TESTS.glob("test_*.py")):
        yield p, p.read_text(encoding="utf-8")


def _imported_test_modules(src: str) -> set:
    """Sibling test modules this one imports from — `from test_x import Base`, `import
    test_x`. The suite puts `tests/` on `sys.path`, so these are flat module names."""
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("test_"):
            out.add(node.module)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("test_"):
                    out.add(a.name)
    return out


def _redirects_the_anchor(src: str, seen=None) -> bool:
    """Does this module redirect the live store — itself, or through a base class it
    inherits from a sibling module?

    THE SECOND HALF IS NOT A LOOPHOLE, it is how the protection is actually expressed.
    `test_land_duration_clock` subclasses `GateNeutralBase`, which lives in
    `test_gate_neutral_land` and pins `SESSION_STATE_DIR` in its own `setUp`. That module
    is completely safe, and the first version of this rule — plain per-file text matching,
    copied from the dispatch guard — failed it at the land gate. A guard that fires on
    correct code does not get obeyed, it gets weakened, and then it is worth nothing on
    the day it is right. So the rule follows imports.

    Transitive, with a `seen` set: base classes chain, and a rule that handled one hop
    would simply move the same false positive one level out."""
    seen = seen if seen is not None else set()
    if APPLIES_FIXTURE.search(src) or PINS_ANCHOR_BY_HAND.search(src):
        return True
    for name in _imported_test_modules(src) - seen:
        seen.add(name)
        p = TESTS / f"{name}.py"
        if not p.is_file():
            continue
        if _redirects_the_anchor(p.read_text(encoding="utf-8"), seen):
            return True
    return False


class LiveStoreReachingFixturesRedirectTheAnchorTest(unittest.TestCase):
    def test_every_module_that_reaches_the_live_store_redirects_it(self):
        missing = [p.name for p, src in _modules()
                   if REACHES_LIVE_STORE.search(src) and not _redirects_the_anchor(src)]
        self.assertEqual(missing, [], (
            "these modules drive code that resolves SESSION_STATE_DIR but never redirect "
            "it, so their writes land in the REAL checkout's .session-state/ — where a "
            "test-shaped .live file is indistinguishable from a working session and holds "
            "the lane against `recover` for 48h (WI-0270). Call "
            "`neutralize_live_store(self)` from `coord_fixture` in setUp, or pin "
            "`session.SESSION_STATE_DIR` to a tmpdir of your own. Inheriting a base class "
            "that does either already counts."))

    def test_the_fixture_is_reached_through_the_shared_module(self):
        """One implementation, not N copies — the copies are how modules drift apart."""
        for p, src in _modules():
            if APPLIES_FIXTURE.search(src):
                self.assertIn("coord_fixture", src,
                              f"{p.name} calls the fixture without importing the one that "
                              "is under test; a local re-definition is the duplication "
                              "this module exists to remove")

    def test_the_detector_would_actually_fire(self):
        """A check that cannot fail is not a check
        ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""
        unguarded = "session.cmd_heartbeat(argparse.Namespace())\n"
        self.assertTrue(REACHES_LIVE_STORE.search(unguarded))
        self.assertFalse(APPLIES_FIXTURE.search(unguarded))
        self.assertFalse(PINS_ANCHOR_BY_HAND.search(unguarded))
        mocked_only = 'p = mock.patch.object(session, "cmd_heartbeat", side_effect=OSError)\n'
        self.assertIsNone(REACHES_LIVE_STORE.search(mocked_only),
                          "mocking a verb out is not driving the path")

    def test_at_least_one_module_is_actually_covered(self):
        """Guards against the rule silently matching nothing (an empty sweep reads green)."""
        covered = [p.name for p, src in _modules() if REACHES_LIVE_STORE.search(src)]
        self.assertGreaterEqual(len(covered), 3, covered)

    def test_protection_inherited_from_a_sibling_module_counts(self):
        """The false positive this rule shipped with, caught by the land gate.

        `test_land_duration_clock` drives `session.cmd_start` and contains neither the
        fixture call nor a pin — it subclasses `GateNeutralBase`, which lives in
        `test_gate_neutral_land` and pins `SESSION_STATE_DIR` itself. Per-file matching
        called that module a hole and blocked the land of the very change it belongs to.

        Asserted on synthetic sources rather than on those two files, so it keeps testing
        the RULE after either of them is rewritten."""
        importer = "from test_somebase import Base\nsession.cmd_start(ns)\n"
        self.assertTrue(REACHES_LIVE_STORE.search(importer))
        self.assertFalse(APPLIES_FIXTURE.search(importer),
                         "the importer must not redirect on its own, or this proves nothing")
        base = TESTS / "test_somebase.py"
        self.addCleanup(lambda: base.unlink(missing_ok=True))
        base.write_text("session.SESSION_STATE_DIR = tmp\n", encoding="utf-8")
        self.assertTrue(_redirects_the_anchor(importer),
                        "a base class in a sibling module that pins the anchor protects "
                        "its subclasses, and the rule has to see that")
        base.write_text("nothing here redirects anything\n", encoding="utf-8")
        self.assertFalse(_redirects_the_anchor(importer),
                         "and it must still FAIL when the imported module does not "
                         "redirect — otherwise following imports is just a way to pass")

    def test_the_import_walk_terminates_on_a_cycle(self):
        """Two test modules importing each other must not hang the sweep."""
        a = TESTS / "test_cycle_a.py"
        b = TESTS / "test_cycle_b.py"
        self.addCleanup(lambda: (a.unlink(missing_ok=True), b.unlink(missing_ok=True)))
        a.write_text("from test_cycle_b import X\n", encoding="utf-8")
        b.write_text("from test_cycle_a import Y\n", encoding="utf-8")
        self.assertFalse(_redirects_the_anchor(a.read_text(encoding="utf-8")))

    def test_the_module_that_caused_this_item_is_covered(self):
        """`test_attention` is the module whose heartbeat held four lanes. If the sweep
        above ever stops seeing it, the rule has drifted off the case it was written for."""
        src = (TESTS / "test_attention.py").read_text(encoding="utf-8")
        self.assertTrue(REACHES_LIVE_STORE.search(src))
        self.assertTrue(APPLIES_FIXTURE.search(src))


class TheAnchorSetIsWhatTheFixtureActuallyRedirectsTest(unittest.TestCase):
    """The acceptance criterion WI-0270 names in as many words: a guard test that fails if
    `SESSION_STATE_DIR` drops out of the neutralized anchor set.

    Asserted against the RUNNING fixture rather than its source text, because what matters
    is where a write lands, not whether the file contains a line that looks right."""

    def test_the_fixture_redirects_every_anchor(self):
        probe = _Probe()
        probe.setUp()
        try:
            from coord_fixture import neutralize_live_store
            base = neutralize_live_store(probe)
            for name in LIVE_STORE_ANCHORS:
                value = pathlib.Path(getattr(session, name))
                self.assertTrue(
                    str(value).startswith(str(base)),
                    f"{name} still points at {value} after the fixture ran — a write "
                    "through it lands in the operator's real store (WI-0270)")
        finally:
            probe.doCleanups()

    def test_a_heartbeat_under_the_fixture_writes_nowhere_real(self):
        """End to end, through the exact call that escaped: the real `cmd_heartbeat`, the
        real sidecar writer, a test-shaped id — and nothing in the real checkout.

        `exercise-delegated-work-end-to-end`: asserting the anchor moved proves the
        fixture assigns a variable. This proves the byte does not land.

        THE "REAL" STORE HERE IS A STAND-IN THIS TEST OWNS, and that is a correctness
        requirement, not tidiness. The first version watched the actual
        `_LAUNCH_SESSION_STATE_DIR` and diffed it before/after — a directory shared with
        every other test in the process AND, at the land gate, with every sibling shard,
        because `curate/run_suite.py` shards across cores into ONE gate worktree. Anything
        else writing there inside the window failed this test. Reported by poga-20
        (WI-0251): the gate failed here on one run and passed on the identical tree on the
        next, costing that lane a diagnosis for a red that named a real test and read like
        a genuine regression.

        That is the same defect this item spent its session describing — a test asserting
        on shared mutable state while siblings mutate it — reproduced inside the guard
        written to fix it. Pinning BOTH globals to a private directory makes "the real
        store" mean something nothing else can touch, and the property under test is
        unchanged: redirect the anchor and the beat must miss this directory; fail to
        redirect it and the beat lands here, which is still exactly what goes red."""
        import argparse
        import contextlib
        import json
        import io
        from unittest import mock
        from coord_fixture import point_store_at

        real = pathlib.Path(tempfile.mkdtemp(prefix="wi0270-standin-")) / ".session-state"
        real.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, real.parent, ignore_errors=True)
        for name in ("SESSION_STATE_DIR", "_LAUNCH_SESSION_STATE_DIR"):
            self.addCleanup(setattr, session, name, getattr(session, name))
            setattr(session, name, real)
        before = sorted(p.name for p in real.iterdir()) if real.is_dir() else []
        probe = _Probe()
        probe.setUp()
        try:
            # `point_store_at`, not bare `neutralize_live_store`: the heartbeat resolves
            # the holder journal, and `POGA_INVOKED_FROM` (the main checkout, in every
            # `poga` session) would otherwise hand it the operator's real journals.
            base = point_store_at(probe)
            prior_throttle = session.HEARTBEAT_MIN_INTERVAL_SEC
            session.HEARTBEAT_MIN_INTERVAL_SEC = 0
            try:
                with mock.patch.object(session, "_read_hook_stdin",
                                       return_value={"session_id": "testsession"}), \
                        mock.patch.object(session, "_complete_lazy_start"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with contextlib.suppress(BaseException):
                        session.cmd_heartbeat(argparse.Namespace())
            finally:
                session.HEARTBEAT_MIN_INTERVAL_SEC = prior_throttle
            after = sorted(p.name for p in real.iterdir()) if real.is_dir() else []
            # The harm first, and named as the harm — this is the assertion that has to
            # carry the diagnosis when it goes red, so it must not be reachable only
            # after some other line has already thrown.
            self.assertEqual(
                after, before,
                "a heartbeat under the fixture wrote into the unredirected store: "
                f"{sorted(set(after) - set(before))} appeared. The anchor is not "
                "redirected. If what appeared is a `.live` file, that IS the WI-0270 "
                "escape — a session id belonging to no session, planted where `recover` "
                "reads liveness. If it is `guard-firings.jsonl`, the write-side refusal "
                "caught the forgery and this is its audit record: the damage was "
                "prevented, the fixture gap is still real, and it is still this test's "
                "job to say so.")
            state = base / ".session-state"
            landed = sorted(p.name for p in state.iterdir()) if state.is_dir() else []
            self.assertIn("testsession.live", landed,
                          "the beat has to go SOMEWHERE, or this test proves nothing "
                          f"about where it goes (fixture store held {landed})")
        finally:
            probe.doCleanups()


class TheWriterRefusesAnIdentityThatNamesNoSessionTest(unittest.TestCase):
    """WI-0270 ask (c). The fixture is the fix; this is the floor under it, for the next
    fixture that forgets. The detector above catches a module that never redirects; this
    catches the write itself, at the one line the damage goes through.

    `setUp` PINS BOTH GLOBALS, and the reason is worth keeping because this class learned
    it the hard way. The refusal is a function of `SESSION_STATE_DIR` *relative to*
    `_LAUNCH_SESSION_STATE_DIR`, so a test that asserts on it while reading whatever the
    previous test left behind is not testing the predicate, it is testing the run order.
    Every one of these passed in isolation and
    `test_a_test_shaped_identity_aimed_at_the_real_store_is_refused_by_name` failed at
    3,844 tests, because some earlier module had left `SESSION_STATE_DIR` pointing at its
    own tmpdir — which is exactly the ambient-state dependence this whole item is about,
    reproduced in the tests written to fix it. Isolation runs cannot see this class of
    bug; only the full suite can."""

    def setUp(self):
        self._prior_dir = session.SESSION_STATE_DIR
        self._prior_launch = session._LAUNCH_SESSION_STATE_DIR
        self.addCleanup(setattr, session, "SESSION_STATE_DIR", self._prior_dir)
        self.addCleanup(setattr, session, "_LAUNCH_SESSION_STATE_DIR", self._prior_launch)
        # "The real store" is whatever this class says it is — a directory of its own, so
        # nothing here depends on, or writes to, the actual checkout.
        self.real = pathlib.Path(tempfile.mkdtemp(prefix="wi0270-launch-")) / ".session-state"
        self.real.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.real.parent, ignore_errors=True)
        session.SESSION_STATE_DIR = self.real
        session._LAUNCH_SESSION_STATE_DIR = self.real

    def test_both_legitimate_shapes_are_accepted(self):
        """The trap WI-0270's own triage flagged: refusing everything but a UUID would
        break `_supervise_key`, which keys on the DURABLE id until adoption stamps a
        claude-session-id. Both shapes, or the fix breaks the supervisor."""
        self.assertEqual(
            session._sidecar_stem_refused("4147fa3b-a04e-4eb7-a57e-419aa001d010"), "")
        durable = session.durable_session_id(
            "DevBox", session.datetime.now(session.timezone.utc))
        self.assertEqual(session._sidecar_stem_refused(durable), "",
                         f"a freshly minted durable id was refused: {durable}")

    def test_the_shape_rule_matches_what_the_generator_mints(self):
        """Derived, not remembered: the regex is only right while it accepts what
        `durable_session_id` actually produces, whatever that becomes."""
        for machine in ("DevBox", "Laptop", "Runner", "weird name 9"):
            minted = session.durable_session_id(
                machine, session.datetime.now(session.timezone.utc))
            self.assertRegex(minted, session.SIDECAR_STEM_RE)

    def test_a_test_shaped_identity_aimed_at_the_real_store_is_refused_by_name(self):
        reason = session._sidecar_stem_refused("testsession")
        self.assertTrue(reason)
        self.assertIn("testsession", reason,
                      "a refusal that does not name the stem cannot be acted on")

    def test_the_same_identity_is_allowed_once_the_store_is_redirected(self):
        """The scope that makes this rule shippable. MEASURED over the full suite: 171 of
        242 sidecar writes use a non-conforming stem, every one of them into the fixture's
        own tmpdir where a short readable id is correct. Refusing on shape alone would
        break 171 right writes to catch the one wrong one."""
        session.SESSION_STATE_DIR = pathlib.Path(
            tempfile.mkdtemp(prefix="wi0270-redirected-")) / ".session-state"
        session.SESSION_STATE_DIR.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, session.SESSION_STATE_DIR.parent, ignore_errors=True)
        for stem in ("testsession", "csid-sib-1", "eph-real", "sess-main"):
            self.assertEqual(session._sidecar_stem_refused(stem), "", stem)

    def test_the_refusal_writes_nothing(self):
        """Refusing means no file, not a file with a caveat in it."""
        session._sidecar_write("testsession", "live", {"last_beat": "now"})
        self.assertEqual([p.name for p in self.real.iterdir() if p.suffix == ".live"], [])

    def test_a_real_identity_still_writes(self):
        """The guard must not brick the thing it guards — the whole orphan model rests on
        this write happening."""
        sid = "4147fa3b-a04e-4eb7-a57e-419aa001d010"
        session._sidecar_write(sid, "live", {"last_beat": "now"})
        self.assertTrue((self.real / f"{sid}.live").is_file())


class TheJanitorClearsResidueTheRefusalCannotTest(unittest.TestCase):
    """WI-0270 ask (d). The write-side refusal stops NEW forgeries; it does nothing about
    the three already on disk. Nor did the janitor: its age cutoff spares anything recent,
    and a `testsession.live` was refreshed by every suite run, so it was always recent.
    A stem that names no session is not evidence and no grace applies to it."""

    def _store(self):
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp()) / ".session-state"
        d.mkdir(parents=True)
        return d

    def test_a_malformed_stem_is_pruned_however_fresh(self):
        d = self._store()
        (d / "testsession.live").write_text('{"last_beat": "now"}', encoding="utf-8")
        pruned = session._janitor_prune_sidecars(
            d, set(), cutoff=0.0, current_csid=None, dry_run=False)
        self.assertEqual(pruned, 1)
        self.assertFalse((d / "testsession.live").exists())

    def test_a_real_identity_inside_the_cutoff_is_still_spared(self):
        """The age rule is correct for real residue and stays untouched."""
        d = self._store()
        sid = "4147fa3b-a04e-4eb7-a57e-419aa001d010"
        (d / f"{sid}.live").write_text('{"last_beat": "now"}', encoding="utf-8")
        pruned = session._janitor_prune_sidecars(
            d, set(), cutoff=0.0, current_csid=None, dry_run=False)
        self.assertEqual(pruned, 0)
        self.assertTrue((d / f"{sid}.live").exists())

    def test_a_dry_run_reports_without_deleting(self):
        d = self._store()
        (d / "testsession.live").write_text('{"last_beat": "now"}', encoding="utf-8")
        pruned = session._janitor_prune_sidecars(
            d, set(), cutoff=0.0, current_csid=None, dry_run=True)
        self.assertEqual(pruned, 1)
        self.assertTrue((d / "testsession.live").exists())


class _Probe:
    """A throwaway stand-in to hang `addCleanup` on, so the fixture can be exercised the
    way a real `setUp` exercises it. Deliberately NOT a `TestCase` subclass — one of those
    gets collected and run as a test of its own, which is a stray green line reporting on
    nothing."""

    def __init__(self):
        self._cleanups = []

    def setUp(self):
        self._cleanups = []

    def addCleanup(self, fn, *a, **kw):
        self._cleanups.append((fn, a, kw))

    def doCleanups(self):
        while self._cleanups:
            fn, a, kw = self._cleanups.pop()
            fn(*a, **kw)


if __name__ == "__main__":
    unittest.main()
