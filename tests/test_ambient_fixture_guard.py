"""The ambient-environment fixture has a detector, so the capability cannot read as
present while being absent ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

WHY THIS FILE EXISTS AT ALL, recorded because the omission is the interesting part.
`ambient_fixture.py` shipped (WI-0250) with two comments asserting that
`test_ambient_fixture_guard.py` "fails the suite when a module that reasons about these
variables skips it". No such file was ever written. The module whose central argument is
*ship the detector with the capability* shipped a citation to a detector instead of a
detector — and nothing caught that, because a guard that does not exist cannot report its
own absence. Found resuming the lane, before it landed.

The sibling detector for the dispatch half is
[`test_dispatch_fixture_guard`](test_dispatch_fixture_guard.py) (WI-0243/0249), and its AST
helpers are IMPORTED here rather than re-derived: two copies of "what does this setUp clear"
is the duplication both guards exist to prevent ([P16](../principles/master.md#p16--avoid-duplication))."""

import ast
import io
import os
import pathlib
import sys
import tokenize
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import ambient_fixture  # noqa: E402
from ambient_fixture import AMBIENT_ONLY_VARS, AMBIENT_VARS  # noqa: E402
from coord_fixture import DISPATCH_ENV_VARS  # noqa: E402
# The predicate for "this module builds fictional coordination holders" is the coord
# guard's, IMPORTED rather than restated — two copies of it would be the duplication
# both guards exist to prevent ([P16](../principles/master.md#p16--avoid-duplication)).
from test_coord_fixture_guard import CALLS_COORD  # noqa: E402
# The AST machinery is the sibling guard's; see the module docstring.
from test_dispatch_fixture_guard import (_cleared_by_hand,  # noqa: E402
                                         _fixture_setups, _modules)

#: A module that pulls the GUARD in. Deliberately the function's name and not the
#: module's: `test_dispatch_fixture_guard` imports `AMBIENT_VARS` to check that tuple is
#: still a superset of its own, which is reading a fact, not installing a fixture — and
#: under the module-name marker it read as a fixture that had forgotten to call itself.
IMPORTS_GUARD = "neutralize_ambient_env"
#: ...and a real call to it.
APPLIES_GUARD = "neutralize_ambient_env"


def code_only(src: str) -> str:
    """`src` with every string literal and comment blanked IN PLACE — same length, same
    line count, same columns — so a pattern quoted in a docstring or in an assertion's
    expected text does not read as a call.

    This is what lets the rule below need no exemption list. Without it the two sibling
    detectors match themselves: `test_coord_fixture_guard` quotes
    `session._coord_try_acquire(...)` as the synthetic source its own
    `test_the_detector_would_actually_fire` runs the rule against, and would be required
    to neutralise an environment it never touches. An exemption list would have hidden
    that — and an exemption list is a place for a real offender to be parked later, which
    is how a guard stops guarding. The coord guard's docstring makes the same boast about
    needing none; this keeps it true one axis over."""
    lines = src.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):     # fail open: judge nothing
        return src
    for tok in toks:
        if tok.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for r in range(r1, r2 + 1):
            line = lines[r - 1]
            a = c1 if r == r1 else 0
            b = c2 if r == r2 else len(line.rstrip("\n"))
            lines[r - 1] = line[:a] + " " * (b - a) + line[b:]
    return "".join(lines)


class TheListIsOneListTest(unittest.TestCase):
    """`AMBIENT_VARS` is `DISPATCH_ENV_VARS` plus an axis. If that stops being true, the
    two modules have drifted back apart — which is the state this fold was made to end."""

    def test_the_ambient_list_covers_every_dispatch_variable(self):
        missing = sorted(set(DISPATCH_ENV_VARS) - set(AMBIENT_VARS))
        self.assertEqual(missing, [], (
            "`AMBIENT_VARS` no longer covers these `DISPATCH_ENV_VARS` entries, so a "
            "fixture calling only `neutralize_ambient_env` inherits them. It is supposed "
            "to be derived from that tuple, not maintained beside it."))

    def test_the_two_halves_do_not_overlap(self):
        """A name in both tuples means someone re-typed a dispatch variable locally."""
        both = sorted(set(AMBIENT_ONLY_VARS) & set(DISPATCH_ENV_VARS))
        self.assertEqual(both, [], (
            "these names are in `AMBIENT_ONLY_VARS` AND `DISPATCH_ENV_VARS` — the local "
            "copy is the drift this fold removed; delete it from `AMBIENT_ONLY_VARS`."))

    def test_the_derivation_is_live_not_a_snapshot(self):
        """Adding to `DISPATCH_ENV_VARS` tomorrow must widen `AMBIENT_VARS` for free."""
        src = (TESTS / "ambient_fixture.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        assigned = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                assigned[node.targets[0].id] = node.value
        self.assertIn("AMBIENT_VARS", assigned)
        names = {n.id for n in ast.walk(assigned["AMBIENT_VARS"]) if isinstance(n, ast.Name)}
        self.assertIn("DISPATCH_ENV_VARS", names, (
            "`AMBIENT_VARS` is built from literals rather than from "
            "`DISPATCH_ENV_VARS` — that is a snapshot, and it goes stale silently"))


class EveryImporterActuallyCallsItTest(unittest.TestCase):
    def test_a_module_that_imports_the_fixture_calls_it(self):
        missing = [p.name for p, src in _modules()
                   if IMPORTS_GUARD in code_only(src)
                   and APPLIES_GUARD + "(" not in code_only(src)]
        self.assertEqual(missing, [], (
            "these modules import `neutralize_ambient_env` but never call it — an "
            "import is not a guard."))

    def test_at_least_one_module_is_actually_covered(self):
        """A sweep that matches nothing reads green. That vacuous-pass shape is itself
        one of the four defects WI-0250 found, so this guard does not get to repeat it."""
        covered = [p.name for p, src in _modules() if APPLIES_GUARD + "(" in src]
        self.assertGreaterEqual(len(covered), 3, covered)


class CoordinationFixturesCarryTheIdentityGuardTest(unittest.TestCase):
    """WI-0275. A module that builds a fictional coordination holder must not let that
    holder's IDENTITY come from the operator's environment.

    `_coord_identity` (sessionlib/coord.py) answers in three tiers: the lane branch, then
    `CLAUDE_CODE_SESSION_ID`, then the invoking lane's key. Four modules — `test_leases`,
    `test_adr_alloc`, `test_lane_litter`, `test_land_gate_lock` — reached it with the
    variable set and were saved by the FIRST tier: each pins `session.ROOT` at a
    `worktree-poga-N` fixture inside the test body, so `_on_worktree_lane()` is true and
    the env read is never reached. That isolation is real but accidental: it lives in an
    assignment inside each test rather than in the fixture, so it is one refactor away
    from gone, and nothing anywhere would say so.

    MEASURED, because the static reading of it was wrong in the interesting direction.
    Wrapping `os.environ` and running those four modules with a session id exported: the
    env read IS reached, 7 times, in two classes that pin `ROOT` at a plain repo rather
    than a lane worktree — `test_adr_alloc.UndrawnNumberIsRefusedTest` (6) and
    `test_land_gate_lock.FailOpenTest` (1). Both are harmless TODAY only because the
    store is unreachable in those tests and the identity is never used. So the hole was
    not "one refactor away"; it was already open, in modules whose own short-circuit
    argument said it could not be.

    WHY THE RULE IS KEYED ON COORDINATION WRITES rather than on reaching
    `_coord_identity`. Reachability was measured over the harness call graph and does not
    admit a proportionate cut: direct readers of the identity variables catch 8 modules
    and only one of the four; adding callers one hop out gives 31; two hops 35; the depth
    that first catches all four is FOUR HOPS, at 47 modules — a suite-wide conversion
    justified by nothing the item found. `session._coord_` is the coord guard's own
    predicate, it picks out exactly the modules that build fictional holders (that guard's
    docstring argues the case), and it catches all four with 16 conversions. A fictional
    holder's journal and a fictional holder's identity are the same hazard one field
    apart, so the two guards should cover the same set of modules."""

    def test_every_module_that_writes_coord_records_neutralizes_the_environment(self):
        missing = [p.name for p, src in _modules()
                   if CALLS_COORD.search(code_only(src)) and APPLIES_GUARD + "(" not in src]
        self.assertEqual(missing, [], (
            "these modules write coordination records but never call "
            "`neutralize_ambient_env(self)`, so the identity their fictional holders are "
            "keyed by can fall through to the operator's own `CLAUDE_CODE_SESSION_ID` "
            "(WI-0275). Import it from `ambient_fixture` and call it FIRST in the "
            "fixture's setUp — before `neutralize_coord_journal`, and before anything "
            "else reads the environment."))

    def test_the_two_guards_cover_the_same_modules(self):
        """The journal guard and the identity guard answer to one predicate. If these
        sets ever diverge, one of the two rules was narrowed and the other was not."""
        writers = {p.name for p, src in _modules() if CALLS_COORD.search(code_only(src))}
        journal = {p.name for p, src in _modules()
                   if "neutralize_coord_journal(" in src}
        self.assertEqual(sorted(writers - journal), [], (
            "these write coordination records without neutralising the journal — "
            "`test_coord_fixture_guard` should already be failing"))

    def test_at_least_one_module_is_actually_covered(self):
        """A sweep that matches nothing reads green — the vacuous-pass shape WI-0250
        found four times. The floor is the sixteen this rule converted."""
        covered = [p.name for p, src in _modules() if CALLS_COORD.search(code_only(src))]
        self.assertGreaterEqual(len(covered), 16, covered)

    def test_the_rule_fires_on_a_coord_writer_with_no_guard(self):
        """A check that cannot fail is not a check."""
        src = ("class T(unittest.TestCase):\n"
               "    def setUp(self):\n"
               "        pass\n"
               "    def test_x(self):\n"
               "        session._coord_try_acquire('claims', 'WI-0001', 'lane', 60)\n")
        self.assertTrue(CALLS_COORD.search(code_only(src)))
        self.assertNotIn(APPLIES_GUARD + "(", src)

    def test_a_quoted_call_is_not_a_call(self):
        """The de-stringing is what removes the need for an exemption list, so it gets a
        test of its own rather than being trusted because the sweep came out green."""
        quoted = ('SAMPLE = "session._coord_try_acquire(...)"\n'
                  "# session._coord_release(...) in a comment\n")
        self.assertTrue(CALLS_COORD.search(quoted), "the raw text does match")
        self.assertIsNone(CALLS_COORD.search(code_only(quoted)),
                          "...and blanking the literals is what stops it")

    def test_the_blanking_preserves_positions(self):
        """A rewrite that shifted lines would move every other rule's line numbers."""
        src = 'x = 1\ny = "abc"  # note\nz = 2\n'
        out = code_only(src)
        self.assertEqual(len(out), len(src))
        self.assertEqual(out.splitlines()[0], "x = 1")
        self.assertEqual(out.splitlines()[2], "z = 2")

    def test_the_guard_runs_before_the_journal_one(self):
        """`neutralize_ambient_env` snapshots the whole environment and restores it at
        cleanup, and its docstring says call it FIRST. Ordering is not cosmetic here:
        cleanups run last-registered-first, so registering this one first makes its
        restore the LAST thing to run, which is what lets a fixture set its own variables
        in between and not have to unwind them."""
        out_of_order = []
        for p, src in _modules():
            if APPLIES_GUARD + "(" not in src or "neutralize_coord_journal(" not in src:
                continue
            for fn in _fixture_setups(src):
                calls = [n.func.id for n in ast.walk(fn)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id in (APPLIES_GUARD, "neutralize_coord_journal")]
                if "neutralize_coord_journal" in calls and APPLIES_GUARD in calls:
                    if calls.index(APPLIES_GUARD) > calls.index("neutralize_coord_journal"):
                        out_of_order.append(f"{p.name}:{fn.lineno}")
        self.assertEqual(out_of_order, [], (
            "these setUps call `neutralize_coord_journal` before "
            "`neutralize_ambient_env`; the ambient one goes first."))


class NoFixtureKeepsItsOwnCopyTest(unittest.TestCase):
    def test_no_setup_clears_an_ambient_variable_by_hand(self):
        offenders = []
        for p, src in _modules():
            if p.name == pathlib.Path(__file__).name:
                continue
            for fn in _fixture_setups(src):
                hit = sorted(_cleared_by_hand(fn) & set(AMBIENT_ONLY_VARS))
                if hit:
                    offenders.append(f"{p.name}:{fn.lineno} clears {hit}")
        self.assertEqual(offenders, [], (
            "a setUp is popping an ambient variable by hand instead of calling "
            "`neutralize_ambient_env(self)`. That is how three modules ended up with "
            "three different lists (WI-0250) — add the name to `AMBIENT_ONLY_VARS`."))


class TheDetectorWouldActuallyFireTest(unittest.TestCase):
    """A check that cannot fail is not a check
    ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""

    def test_the_hand_clear_rule_fires_on_a_real_hand_clear(self):
        fn = next(_fixture_setups(
            "class T:\n"
            "    def setUp(self):\n"
            "        os.environ.pop('NO_COLOR', None)\n"))
        self.assertEqual(_cleared_by_hand(fn) & set(AMBIENT_ONLY_VARS), {"NO_COLOR"})

    def test_saving_a_variable_is_not_clearing_it(self):
        fn = next(_fixture_setups(
            "class T:\n"
            "    def setUp(self):\n"
            "        self._e = {k: os.environ.get(k) for k in ('NO_COLOR',)}\n"))
        self.assertEqual(_cleared_by_hand(fn) & set(AMBIENT_ONLY_VARS), set())

    def test_the_importer_rule_fires_on_an_import_without_a_call(self):
        src = "from ambient_fixture import neutralize_ambient_env\n"
        self.assertIn(IMPORTS_GUARD, code_only(src))
        self.assertNotIn(APPLIES_GUARD + "(", code_only(src))

    def test_reading_the_tuple_is_not_importing_the_guard(self):
        """The distinction that made `test_dispatch_fixture_guard` a false positive."""
        src = "from ambient_fixture import AMBIENT_VARS\n"
        self.assertNotIn(IMPORTS_GUARD, code_only(src))


class TheFixtureDoesWhatItSaysTest(unittest.TestCase):
    """Exercise the helper itself, not only the modules that call it."""

    def test_it_clears_every_listed_variable_and_restores_the_environment(self):
        before = dict(os.environ)
        os.environ["NO_COLOR"] = "1"
        os.environ["POGA_DISPATCH"] = "D-ffffff"

        class Probe(unittest.TestCase):
            def runTest(self):
                pass

        case = Probe()
        ambient_fixture.neutralize_ambient_env(case)
        try:
            for var in AMBIENT_VARS:
                self.assertNotIn(var, os.environ, f"{var} survived the neutraliser")
        finally:
            case.doCleanups()
        self.assertEqual(os.environ.get("NO_COLOR"), "1")
        self.assertEqual(os.environ.get("POGA_DISPATCH"), "D-ffffff")
        os.environ.clear()
        os.environ.update(before)

    def test_a_second_call_is_a_no_op_rather_than_a_nested_snapshot(self):
        """Subclassed fixtures call setUp twice; the second must not snapshot the
        already-cleared environment and restore THAT."""
        before = dict(os.environ)
        os.environ["NO_COLOR"] = "1"

        class Probe(unittest.TestCase):
            def runTest(self):
                pass

        case = Probe()
        ambient_fixture.neutralize_ambient_env(case)
        ambient_fixture.neutralize_ambient_env(case)
        case.doCleanups()
        self.assertEqual(os.environ.get("NO_COLOR"), "1")
        os.environ.clear()
        os.environ.update(before)


if __name__ == "__main__":
    unittest.main()
