"""The detector for `neutralize_dispatch_env` (WI-0249) — a guard nobody can forget.

The suite runs at the LAND GATE, and every land in this repo happens inside a lane — very
often a dispatched one. So while these tests execute, `POGA_DISPATCH`, `POGA_DISPATCH_ITEM`
and `POGA_LANE_CLOSE` are set, and the process ancestry above the runner is a real runtime
in a real tmux session. Any fixture that drives a code path keyed on those variables is
therefore testing the developer's machine rather than its own scenario.

That already happened once, benignly: `_lane_reserve` stamps `dispatch_id` from the
environment, so `test_lane_alloc`'s hand-launched lanes reserved as dispatched and three
cap tests failed for a reason unrelated to the code under test (lane poga-6, dispatch
D-788b4a, 2026-09-03). The fix was local to that module — which is precisely the shape
`test_coord_fixture_guard` was written to eliminate one layer down: a guard that must be
remembered per module is a guard that gets forgotten
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

WI-0249 raises the stakes from a confusing red to a destroyed session. `cmd_end`'s lane
path now calls `_dispatch_close_runtime`, which SIGTERMs the lane's runtime. A fixture that
reaches it with an inherited `POGA_DISPATCH` is one guard away from killing the process
running the suite. The production code carries its own independent proof (the closed
journal must be this runtime's own — see `_dispatch_close_runtime`); this detector removes
the input as well, because when the cost of being wrong is a live session, two independent
reasons to be safe is the right number.

The rule is mechanical, like the coord one: a module that CALLS a function reading the
dispatch environment reaches a dispatch-keyed path. Mocking one out, or naming it in
prose, does not.

WHICH functions those are is **derived from `session.py`, not typed here** (WI-0243). It
was typed once — six names — while eleven functions read the environment, and the gap was
not academic: `_grant_origin` was among the missing five, `test_grants` calls it, and that
module therefore cleared the variables with a partial hand-written list of its own while
this guard reported everything covered. Three other modules did the same. The lesson is
the one the substrate keeps relearning: a hand-kept list of what the code does is a copy
of the code, and it drifts.
"""

import ast
import pathlib
import re
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import harness_fixture  # noqa: E402
from ambient_fixture import AMBIENT_VARS  # noqa: E402
from coord_fixture import DISPATCH_ENV_VARS  # noqa: E402

#: An `os.environ` READ of one of the dispatch variables — `.get(…)`, `[…]`, or the
#: `(os.environ.get(…) or "")` form. Deliberately not `.pop`/`.setdefault`: clearing a
#: variable is what the guard DOES, and matching it would make the guard find itself.
ENV_READ = re.compile(
    r"os\.environ(?:\.get)?[\(\[]\s*[\"'](" + "|".join(DISPATCH_ENV_VARS) + r")[\"']")

#: Functions that reach a dispatch-keyed path WITHOUT reading the environment in their
#: own body — a caller whose callee does. Derivation cannot see these, so they are named
#: here, and each says which reader it reaches. This is the ONLY hand-maintained half.
TRANSITIVE_REACHERS = (
    "cmd_end",           # → _dispatch_close_runtime — SIGTERMs the lane's own runtime
    "cmd_merge",         # → _dispatch_close_after_land — the same, on the `--continue`
                         #   route, which does NOT go through cmd_end (WI-0354)
    "_lane_alloc",       # → _lane_reserve — stamps dispatch_id into the reservation
    "_coord_reap",       # → _attention_session_is_over → _holder_journal_dirs
    "_attention_write",  # → _attention_session_is_over → _holder_journal_dirs
)


def _env_reading_functions(src: str) -> set:
    """Every top-level function in `src` whose body reads a dispatch variable.

    DERIVED, NOT REMEMBERED (WI-0243). This list used to be six names typed into the
    regex below. `session.py` has eleven direct readers, and the five that were missing
    included `_grant_origin` — which `test_grants` calls, so that module was never
    required to carry the guard and cleared the variables with a hand-rolled list of its
    own instead. The guard read GREEN with that hole open, which is the same shape twice
    over: WI-0242's completeness check could not see the launcher's half of the
    environment, and this one could not see most of the functions that read it.

    A hand-written list of what code does is a copy of the code that drifts from it
    ([`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)). Read
    the source instead, and the next reader someone adds is covered the day it lands."""
    lines = src.splitlines()
    found = set()
    for node in ast.parse(src).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = "\n".join(lines[node.lineno - 1:node.end_lineno or node.lineno])
        if ENV_READ.search(body):
            found.add(node.name)
    return found


def _reaching_names() -> list:
    src = harness_fixture.harness_source()
    return sorted(_env_reading_functions(src) | set(TRANSITIVE_REACHERS))


#: A real call into a path that reads the dispatch environment.
REACHES_DISPATCH_ENV = re.compile(
    r"session\.(" + "|".join(_reaching_names()) + r")\s*\(")
#: The guard, however the module reaches it — EITHER helper satisfies this rule.
#: `ambient_fixture.AMBIENT_VARS` is `DISPATCH_ENV_VARS` plus an identity axis, DERIVED
#: from this module's tuple rather than restated beside it, and
#: `test_ambient_fixture_guard.TheListIsOneListTest` fails the suite the moment that
#: stops being true. So a fixture calling only `neutralize_ambient_env` clears strictly
#: more than this guard demands, and demanding the narrower call as well would leave
#: sixteen fixtures making two calls where one does the work — the redundancy that
#: teaches the next reader to wonder which list is in force (WI-0275).
APPLIES_GUARD = re.compile(r"neutralize_(?:dispatch_env|ambient_env)\s*\(")
#: ...and the modules those two helpers live in.
GUARD_MODULES = ("coord_fixture", "ambient_fixture")


def _modules():
    for p in sorted(TESTS.glob("test_*.py")):
        yield p, p.read_text(encoding="utf-8")


def _is_environ_pop(node) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "pop"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ")


def _cleared_by_hand(fn) -> set:
    """Every variable name `fn` clears from the environment itself.

    Both real shapes: a literal `os.environ.pop("POGA_DISPATCH", None)`, and the loop
    `for var in (…): os.environ.pop(var, None)` — whose tuple is read for its literals.
    A name merely SAVED for restore (`{k: os.environ.get(k) for k in (…)}`) is not a
    clear and must not be flagged; three `test_coord` classes do exactly that."""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Delete):
            for t in node.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Attribute)
                        and t.value.attr == "environ"
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)):
                    names.add(t.slice.value)
        if not (_is_environ_pop(node) and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.add(arg.value)
        elif isinstance(arg, ast.Name):
            for loop in ast.walk(fn):
                if (isinstance(loop, ast.For)
                        and isinstance(loop.target, ast.Name)
                        and loop.target.id == arg.id
                        and isinstance(loop.iter, (ast.Tuple, ast.List))):
                    names |= {e.value for e in loop.iter.elts
                              if isinstance(e, ast.Constant)
                              and isinstance(e.value, str)}
    return names


def _fixture_setups(src: str):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name in ("setUp", "setUpClass"):
            yield node


class DispatchKeyedFixturesCarryTheGuardTest(unittest.TestCase):
    def test_every_module_that_reaches_a_dispatch_keyed_path_clears_the_environment(self):
        missing = [p.name for p, src in _modules()
                   if REACHES_DISPATCH_ENV.search(src) and not APPLIES_GUARD.search(src)]
        self.assertEqual(missing, [], (
            "these modules drive code that reads POGA_DISPATCH* but never call "
            "`neutralize_dispatch_env(self)`, so they inherit the dispatch handles of the "
            "lane running the suite (WI-0249). Import it from `coord_fixture` and call it "
            "in the fixture's setUp."))

    def test_the_guard_is_reached_through_the_shared_module(self):
        """One implementation, not N copies — the copies are how modules drift apart."""
        for p, src in _modules():
            if APPLIES_GUARD.search(src):
                self.assertTrue(any(m in src for m in GUARD_MODULES),
                                f"{p.name} calls the guard without importing the one "
                                "that is under test; a local re-definition is the "
                                "duplication this module exists to remove")

    def test_the_wider_helper_really_is_wider(self):
        """The whole licence for accepting `neutralize_ambient_env` here is that it
        clears everything this guard names. Pinned at the point of use, not only in the
        sibling guard — a rule that accepts a substitute on someone else's say-so is a
        rule that goes wrong quietly when the say-so changes
        ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes))."""
        self.assertEqual(sorted(set(DISPATCH_ENV_VARS) - set(AMBIENT_VARS)), [], (
            "`neutralize_ambient_env` no longer clears every dispatch variable, so it "
            "must stop satisfying this guard — narrow `APPLIES_GUARD` back to "
            "`neutralize_dispatch_env`."))

    def test_the_detector_would_actually_fire(self):
        """A check that cannot fail is not a check
        ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""
        unguarded = "session.cmd_end(argparse.Namespace(title='t'))\n"
        self.assertTrue(REACHES_DISPATCH_ENV.search(unguarded))
        self.assertFalse(APPLIES_GUARD.search(unguarded))
        mocked_only = 'p = mock.patch.object(session, "cmd_end", side_effect=OSError)\n'
        self.assertIsNone(REACHES_DISPATCH_ENV.search(mocked_only),
                          "mocking a verb out is not driving the path")

    def test_at_least_one_module_is_actually_covered(self):
        """Guards against the rule silently matching nothing (an empty sweep reads green)."""
        covered = [p.name for p, src in _modules() if REACHES_DISPATCH_ENV.search(src)]
        self.assertGreaterEqual(len(covered), 3, covered)

    def test_the_variable_list_covers_what_the_spawn_command_sets(self):
        """The list is only right while it matches what a dispatch actually exports —
        a variable added to the spawn line and not here is a hole that reads as covered."""
        src = harness_fixture.harness_source()
        spawn = src.split("def _dispatch_spawn_command", 1)[1].split("\ndef ", 1)[0]
        exported = set(re.findall(r"\b(POGA_[A-Z_]+)=", spawn))
        self.assertTrue(exported, "could not read the spawn command's environment")
        self.assertEqual(exported - set(DISPATCH_ENV_VARS), set(), (
            "the dispatch spawn command exports a variable that `DISPATCH_ENV_VARS` does "
            "not clear, so a fixture would inherit it"))

    def test_the_variable_list_covers_what_the_LAUNCHER_exports(self):
        """The spawn line is only the OUTER half of a dispatched lane's environment.

        WI-0242. `_dispatch_spawn_command` sets three variables and then execs `poga`,
        and `poga` exports two more of its own — `POGA_INVOKED_FROM` (poga:52,
        unconditional, before any `cd`) and `POGA_RUNTIME_ID`. Those are just as present
        in a lane's environment as the three the spawn line names, and just as inherited
        by a fixture that does not clear them.

        The test above reads only `_dispatch_spawn_command`'s body, so it could never see
        them: a completeness check that cannot observe the thing it is incomplete about
        reads green with the hole open. That is the WI-0242 shape reproduced one layer out
        from where WI-0249 closed it — the guard was made structural, its completeness
        check was not ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability))."""
        launcher = (TESTS.parent / "poga").read_text(encoding="utf-8")
        exported = set(re.findall(r"^\s*export\s+(POGA_[A-Z_]+)=", launcher, re.M))
        self.assertTrue(exported, "could not read the launcher's environment")
        self.assertEqual(exported - set(DISPATCH_ENV_VARS), set(), (
            "`poga` exports a variable that `DISPATCH_ENV_VARS` does not clear, so every "
            "fixture in a dispatched lane inherits it from the launcher rather than from "
            "the spawn line — the half the spawn-line check cannot see (WI-0242)"))


class TheReachingSetIsDerivedFromTheSourceTest(unittest.TestCase):
    """WI-0243. The rule above is only as complete as the list of functions it matches,
    and that list used to be typed. A completeness check that cannot observe the thing it
    is incomplete about reads green with the hole open — proven here before it was fixed:
    dropping `POGA_DISPATCH` from `test_grants`' own hand-rolled clear turned two grant
    tests RED inside a dispatched lane while this module stayed entirely GREEN."""

    def test_the_derivation_finds_every_reader_in_session_py(self):
        src = harness_fixture.harness_source()
        found = _env_reading_functions(src)
        self.assertGreaterEqual(len(found), 8, sorted(found))
        for expected in ("_lane_reserve", "_dispatch_close_runtime", "_grant_origin",
                         "_holder_journal_dirs", "_dispatched_close_authorization"):
            self.assertIn(expected, found)

    def test_the_regex_covers_every_derived_reader(self):
        """The two halves must actually meet — a name derived but not matched would be a
        reader the rule still cannot see."""
        for name in _reaching_names():
            self.assertRegex(f"session.{name}(", REACHES_DISPATCH_ENV, name)

    def test_the_derivation_would_notice_a_reader_added_tomorrow(self):
        """A check that cannot fail is not a check. Run against synthetic source, because
        the real question is what happens to code that does not exist yet."""
        self.assertIn("_brand_new_verb", _env_reading_functions(
            "def _brand_new_verb():\n"
            "    return os.environ.get('POGA_DISPATCH', '').strip()\n"))
        self.assertIn("_subscript_form", _env_reading_functions(
            "def _subscript_form():\n"
            "    return os.environ['POGA_LANE_CLOSE']\n"))

    def test_naming_a_variable_is_not_reading_it(self):
        """`_dispatch_spawn_command` WRITES these variables into a shell command and the
        guard itself POPS them; neither is an ambient read, and folding them in would
        make the rule demand a guard of the guard."""
        self.assertNotIn("_only_mentions_it", _env_reading_functions(
            "def _only_mentions_it():\n"
            "    return 'POGA_DISPATCH=' + did\n"))
        self.assertNotIn("_clears_it", _env_reading_functions(
            "def _clears_it():\n"
            "    os.environ.pop('POGA_DISPATCH', None)\n"))

    def test_the_transitive_list_names_only_non_readers(self):
        """A name that IS derived does not belong in the hand-maintained half — leaving
        it there is how the typed list starts growing back."""
        derived = _env_reading_functions(harness_fixture.harness_source())
        self.assertEqual(sorted(set(TRANSITIVE_REACHERS) & derived), [],
                         "these are found by derivation and need no hand entry")


class NoFixtureKeepsItsOwnCopyOfTheVariableListTest(unittest.TestCase):
    """WI-0243. `test_the_guard_is_reached_through_the_shared_module` forbids a local
    re-definition of the guard — but only for a module that CALLS it. Four modules
    cleared these variables in `setUp` with a hand-written tuple instead and were
    therefore invisible to it: `test_attach`, `test_claims`, `test_grants` and
    `test_wi_feed`. Every one of the four was a PARTIAL copy, and each was missing a
    different subset — which is what a maintained duplicate always becomes
    ([`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)).

    The cost is not theoretical: `test_grants`' copy was the only reason two grant tests
    passed inside a dispatched lane, and it omitted two of the five variables."""

    def test_no_setup_clears_a_dispatch_variable_by_hand(self):
        offenders = []
        for p, src in _modules():
            for fn in _fixture_setups(src):
                hit = sorted(_cleared_by_hand(fn) & set(DISPATCH_ENV_VARS))
                if hit:
                    offenders.append(f"{p.name}:{fn.lineno} clears {hit}")
        self.assertEqual(offenders, [], (
            "these fixtures clear the dispatch environment with their own list instead "
            "of `neutralize_dispatch_env(self)` from `coord_fixture`. A second copy of "
            "the list is a copy that goes stale — all four that existed were missing "
            "variables the shared one clears. Call the helper; keep only the vars it "
            "does not own (e.g. CLAUDE_CODE_SESSION_ID)."))

    def test_the_rule_tells_a_clear_apart_from_a_save(self):
        """Saving a name for restore is not clearing it — three `test_coord` classes do
        exactly that, and flagging them would train the reader to wave the rule off."""
        clears = ast.parse(
            "def setUp(self):\n"
            "    os.environ.pop('POGA_DISPATCH', None)\n").body[0]
        self.assertEqual(_cleared_by_hand(clears), {"POGA_DISPATCH"})
        saves = ast.parse(
            "def setUp(self):\n"
            "    self._env = {k: os.environ.get(k) for k in ('POGA_INVOKED_FROM',)}\n"
            "    os.environ.pop('CLAUDE_CODE_SESSION_ID', None)\n").body[0]
        self.assertEqual(_cleared_by_hand(saves) & set(DISPATCH_ENV_VARS), set())

    def test_the_rule_reads_the_loop_form_too(self):
        """The shape all four offenders actually used."""
        looped = ast.parse(
            "def setUp(self):\n"
            "    for var in ('CLAUDE_CODE_SESSION_ID', 'POGA_RUNTIME_ID'):\n"
            "        os.environ.pop(var, None)\n").body[0]
        self.assertEqual(_cleared_by_hand(looped),
                         {"CLAUDE_CODE_SESSION_ID", "POGA_RUNTIME_ID"})


if __name__ == "__main__":
    unittest.main()
