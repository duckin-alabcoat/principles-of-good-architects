"""One guard, one place: a fixture must not inherit the RUNNER'S environment (WI-0250).

The sibling of [`coord_fixture`](coord_fixture.py), one layer out. That module stops a
fixture inheriting the runner's *journal*; this one stops it inheriting the runner's
*environment*. Both exist for the same reason and were found the same way — a test that
reads ambient process state passes or fails on who ran it, and the merge gate inherits the
coin-flip.

WHAT THE DEFECT LOOKS LIKE. `session._lane_reserve` stamps `dispatch_id` from
`POGA_DISPATCH` by design — that is how a dispatched lane records which dispatch spawned
it. `test_lane_alloc`'s whole subject is the hand-launched lane that carries NO dispatch
handle. Run that suite from inside a dispatched lane, which is exactly where a lane's own
land gate runs it, and `POGA_DISPATCH` is already set: every "hand-launched" fixture
silently reserves as dispatched. The suite had therefore NEVER been green inside a
dispatched lane, and every dispatched session that ran it saw two red tests it did not
cause. The obedient reading of a red test is "my change broke something", so the cost was
paid in misattributed debugging by whoever was holding the lane, every time.

WHY A SHARED MODULE RATHER THAN A POP IN EACH `setUp`. Because that is what was already
tried, and it drifted — the same way `coord_fixture` records six modules drifting from
two. At the time this module was written, three fixtures cleared ambient variables and
each cleared a DIFFERENT set:

    test_claims.ClaimsBase      CLAUDE_CODE_SESSION_ID, POGA_INVOKED_FROM, POGA_RUNTIME_ID
    test_lane_alloc.LaneAllocBase              POGA_DISPATCH, POGA_DISPATCH_ITEM
    test_dispatch.DispatchBase                 (none)

Three lists, no two alike, and nothing anywhere that could tell you so. The gap in the
third is not hypothetical: `TerminalTitleTest.test_the_escape_is_written_only_to_a_real_terminal`
asserts the OSC-0 escape IS written, and `_set_terminal_title` suppresses it under
`NO_COLOR` — so that test fails for anyone whose terminal sets `NO_COLOR`, on a machine
where nothing is wrong. A list maintained per module is a list that diverges; one list
delivered to every fixture is [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver),
and `test_ambient_fixture_guard.py` fails the suite when a module imports this fixture
without calling it, when `AMBIENT_VARS` stops covering `coord_fixture.DISPATCH_ENV_VARS`,
or when a `setUp` goes back to popping one of these names by hand
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

WHAT IS AND IS NOT IN THE LIST. `AMBIENT_VARS` holds the variables that tell a process
*which session, lane, dispatch and machine it is* — the ones that point a fixture at the
developer's real world. It deliberately does NOT hold terminal-capability variables
(`TERM`, `TERM_PROGRAM`): those describe the device, not the identity, and a test that
shells out to `poga` needs a working terminfo. `NO_COLOR` and `POGA_FORCE_COLOR` ARE here
despite looking cosmetic, because the substrate branches on them and one test already
turned on that branch.

This is the ENVIRONMENT axis only. Two other ambient inputs have their own guards, because
neutralising an environment variable does nothing for either: the runner's journal
(`coord_fixture.neutralize_coord_journal`) and the real coordination store, which
`session._coord_dir` resolves from `git rev-parse --git-common-dir` run in **`session.ROOT`,
not the process cwd** — `sh()` defaults to `cwd=cwd or ROOT`. The distinction is the whole
safety argument and this sentence had it backwards until 2026-09-13 (WI-0286): a fixture
that chdirs into a tmpdir believing that isolates it still writes the OPERATOR's real
`poga-coord/`, and the module documenting the hazard was the one pointing the wrong way.
VERIFIED by running it — `os.chdir` to a fresh `git init` tmpdir, then `_coord_dir("claims")`,
which returned the real checkout's store. Rebind `session.ROOT` to move it. A
third — gitignored machine-local config read out of the real checkout — is why
`test_tree_provenance.ReconcileNamesItsRoster` was rewritten against a fixture root rather
than given a variable to pop.
"""

import os

from coord_fixture import DISPATCH_ENV_VARS

#: The variables this module adds ON TOP of the dispatch handles: which Claude session,
#: the two preflight/production escape hatches, and the two colour switches the substrate
#: branches on. Every one is set in a real `poga` session and read by `session.py`, so any
#: left in place points a fixture at the developer's live checkout instead of its own.
AMBIENT_ONLY_VARS = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "POGA_SKIP_PREFLIGHT",
    "POGA_SKIP_PREFLIGHT_CHECKS",
    "POGA_ALLOW_DEV_ON_PROD",
    "NO_COLOR",
    "POGA_FORCE_COLOR",
)

#: The full ambient identity of the process. DERIVED from `coord_fixture`'s list rather
#: than restating it, because this module and that one were written independently against
#: the SAME incident (WI-0243/0249 and WI-0250, both from lane poga-6 under D-788b4a) and
#: landed with different lists: `DISPATCH_ENV_VARS` carried `POGA_LANE_CLOSE`, which this
#: tuple had never heard of, and this tuple carried the colour switches, which that one
#: had never heard of. Two hand-kept lists for one hazard is precisely the drift both
#: modules were extracted to end, so there is now one list of dispatch handles and this is
#: that list plus an axis
#: ([`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)).
#: `test_ambient_fixture_guard` fails if this stops being a superset.
AMBIENT_VARS = tuple(DISPATCH_ENV_VARS) + AMBIENT_ONLY_VARS

_ATTR = "_ambient_env_snapshot"


def neutralize_ambient_env(case):
    """Run `case` from a KNOWN environment. Call FIRST in setUp, before anything reads it.

    Restores the whole environment at cleanup, so a fixture that goes on to set its own
    variables (the normal case — most of these tests need `CLAUDE_CODE_SESSION_ID` set to
    a fictional value) does not have to unwind them itself."""
    if getattr(case, _ATTR, None) is not None:      # idempotent: a subclass may call twice
        return
    snapshot = dict(os.environ)
    setattr(case, _ATTR, snapshot)

    for var in AMBIENT_VARS:
        os.environ.pop(var, None)

    def _restore():
        if getattr(case, _ATTR, None) is None:
            return
        os.environ.clear()
        os.environ.update(snapshot)
        setattr(case, _ATTR, None)

    case.addCleanup(_restore)


#: THERE IS DELIBERATELY NO OPT-OUT HERE, unlike `coord_fixture.exercise_real_coord_holder`.
#: That module needs one because holder resolution is genuinely the subject of some of its
#: tests. Nothing in this suite has a legitimate reason to assert on the environment of
#: whatever machine happens to be running it — a test about how the substrate reads
#: `POGA_DISPATCH` wants a KNOWN value, which it sets itself after calling the neutraliser.
#: An escape hatch with no caller is an invitation, so it is not shipped until something
#: needs it.
