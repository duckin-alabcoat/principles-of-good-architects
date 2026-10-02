"""One guard, one place: a fixture's coordination records must not inherit the AMBIENT
session's journal (WI-0126).

`_coord_record` stamps every coordination record with `_coord_holder()`, which resolves
the journal of the session *executing the process* — not the fictional lane the fixture
is pretending to be. Before ADR-0093 that was cosmetic: the journal rode along as an
identifying field and nothing matched on it. ADR-0093 made it an ownership key in
`_coord_mine`, and the same field became load-bearing: two fictional holders built in one
fixture ("sess-A" and "sess-B", `worktree-poga-1` and `-2`) both inherit the ONE journal
of whatever real session runs the suite, so `_coord_mine` matches on it and they read as
the same holder.

The failure that produced this module: `test_leases.py` had laneB RECLAIM laneA's lease
instead of being refused, inverting all three exclusion assertions — and the suite then
passed or failed on whether the developer running it happened to have an open journal
(green from a lane whose journal was closed, red from one still open), with the merge gate
inheriting the coin-flip. `test_coord.py` and `test_leases.py` each carried their own copy
of the fix; six other modules that write coordination records carried none. A guard that
must be remembered per module is a guard that gets forgotten, so it lives here once
([`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)) and
`test_coord_fixture_guard.py` fails the suite when a coord-writing module skips it
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

Neutralising the journal leaves the BRANCH key (`worktree-poga-1` vs `-2`) as the only
discriminator, which is what a lease/claim/reclaim fixture is actually about. Journal
matching itself is pinned directly in `test_coord.py`, against records built to carry one.
"""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

#: `_coord_holder()`'s return shape: (durable-journal-id, runtime, runtime-source). Empty
#: journal is the honest value for a fictional holder — it has no session, so it has none.
NEUTRAL_HOLDER = ("", "", "")

_ATTR = "_coord_journal_patch"


def neutralize_coord_journal(case):
    """Fixture records carry no journal for the duration of `case`. Call FIRST in setUp.

    Registers its own cleanup, so a test that stops it early (see
    `exercise_real_coord_holder`) cannot double-stop the patch at teardown."""
    patch = unittest.mock.patch.object(session, "_coord_holder",
                                       return_value=NEUTRAL_HOLDER)
    patch.start()
    setattr(case, _ATTR, patch)

    def _stop():
        if getattr(case, _ATTR, None) is not None:
            patch.stop()
            setattr(case, _ATTR, None)

    case.addCleanup(_stop)


def exercise_real_coord_holder(case):
    """Opt OUT of the neutraliser, for a class whose subject IS holder resolution.

    The neutraliser is the default because inheriting the ambient journal is silent and
    wrong nearly everywhere. A test that asserts on `rec["journal"]` — `_coord_record`
    finding a lane's journal from the anchored checkout, the launcher/journal precedence
    tiers, the honest-empty cases — needs the real function, and says so by name rather
    than by not having heard of the guard
    ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
    Such a test owns its own inputs (`JOURNAL_DIR`, `CLAUDE_CODE_SESSION_ID`,
    `POGA_INVOKED_FROM`, and the CWD); it does not read the developer's live session.

    **The CWD is the fourth input, and it was missed.** WI-0106 gave
    `_find_holder_journal` a tier ABOVE `POGA_INVOKED_FROM` — the checkout the process is
    actually in (`Path.cwd()`) — because for a lane-created non-Claude session those
    differ. Correct in production, and invisible here: the suite's CWD is the developer's
    own checkout, so once that checkout had an open journal the resolver returned the
    RUNNER'S session and never consulted the fixture's `POGA_INVOKED_FROM` at all. Six
    tests in this file then failed with `claude-code` where they expected `codex-cli`.

    That is the same coin-flip this module was written to remove, resurfacing one tier
    out: green from a session whose journal was closed, red from one still open, with the
    merge gate inheriting the toss. So the opt-out also parks the CWD in an empty
    directory — no `sessions/journal/`, no `.session-state/prep.json`, therefore no
    holder — which makes the tier fall through to the input the test actually sets. An
    empty dir rather than the fixture repo, because it asserts nothing about the
    fixture's layout: it is neutral by construction, not by inspection."""
    patch = getattr(case, _ATTR, None)
    if patch is not None:
        patch.stop()
        setattr(case, _ATTR, None)

    neutral = tempfile.mkdtemp(prefix="coord-neutral-cwd-")
    prior = os.getcwd()
    os.chdir(neutral)

    def _restore():
        os.chdir(prior)
        shutil.rmtree(neutral, ignore_errors=True)

    case.addCleanup(_restore)


#: The variables a DISPATCHED lane carries in its environment. A fixture that inherits
#: them is not testing its own scenario — it is testing the developer's machine.
#: The first four come from the spawn line
#: (`_dispatch_spawn_command`); the last two are exported by `poga` itself (poga:52,
#: poga:587/1132/1360) and are therefore present in EVERY lane, dispatched or not.
#: WI-0242 — the spawn line is only the outer half, and the completeness check that
#: read only the spawn line could not see the inner half it was missing.
#: WI-0288 R2 adds `POGA_TURN_BUDGET`. Its own completeness guard caught the omission
#: on the first full-suite run, which is `ship-the-detector-with-the-capability` paying
#: out: a fixture inheriting a real budget would count the runner's turns against it.
#: WI-0331 adds `POGA_UNATTENDED_RUN`. `cmd_end` acts on it exactly as it acts on
#: `POGA_DISPATCH` — it is the second close authorization — so a fixture that inherits
#: it from an adoption sweep would close sessions it never marked, and the
#: close-guard tests would pass for a reason that has nothing to do with their subject.
DISPATCH_ENV_VARS = ("POGA_DISPATCH", "POGA_DISPATCH_ITEM", "POGA_LANE_CLOSE",
                     "POGA_TURN_BUDGET", "POGA_INVOKED_FROM", "POGA_RUNTIME_ID",
                     "POGA_UNATTENDED_RUN")


def neutralize_live_store(case, root=None):
    """No route from this test reaches the developer's live journals or backlog.

    WI-0295, extended by WI-0270. Every anchor below was found the same way: the previous
    fix was applied, the measurement did not move, and the reason was one more route
    nobody had enumerated. Read that as the standing warning it is — the count has been
    "all of them" three times now.

    The first two, and fixing only the far one is why the first attempt changed nothing
    measurable:

      * `session.JOURNAL_DIR` — a module global computed at IMPORT from the real repo.
        `_holder_journal_dirs()` (session.py:2842) starts with `add(JOURNAL_DIR)`, so a
        module that patches `ROOT` but not this one hands the live journal directory
        straight to `_open_journals_in`, which globs and parses every file in it. This
        is the one that survived the first pass: `test_attention` patches `ROOT` and
        `CFG` and never touches `JOURNAL_DIR`, and kept making 161 live reads with the
        far anchor already patched.
      * `session._shared_work_root()` (session.py:2491) — resolves the MAIN checkout
        through the git COMMON dir, so it points at the operator's own repo whatever
        `ROOT` says. It anchors the work-item store (:8199, :8320), the wi feed (:8120,
        :8129), session-state (:11580) and the lane worktree list (:2845, :4290) — and
        through that last one it feeds `_holder_journal_dirs` every LIVE lane's journal
        directory too.

    Together with `neutralize_dispatch_env` (which clears `POGA_INVOKED_FROM`, a third
    directory in the same list) that is every route into `_holder_journal_dirs` — which
    is a narrower claim than it reads as, and WI-0270 is what the gap between them cost.
    `_holder_journal_dirs` is about READING the operator's journals. The live store is
    also WRITTEN, through `SESSION_STATE_DIR`, and that route goes through none of the
    anchors above. Hence the fourth below, and its detector.

    MEASURED, twice, because the first measurement was incomplete and said so only when
    the number failed to move. Per-test attribution with an `sys.addaudithook`: 98 tests
    across 6 modules, 28,094 reads of the live store — 162 files under
    `sessions/journal/`, 109 under `work-items/`. Those tests' results depend on the
    developer's open journal and current backlog: they pass or fail by who ran them,
    which is the coin-flip this module's header describes, one anchor further out.
    Session ~206 found the mechanism, called it "non-hermetic by construction", and did
    not file it.

    `root` is the fixture's own repo root where it has one; otherwise an empty tmpdir,
    which is the honest default — a test with no main checkout of its own should see one
    with nothing in it. A caller that sets `JOURNAL_DIR` itself should call this FIRST
    and then assign; its own value wins and is restored at cleanup.

    A test whose SUBJECT is main-checkout resolution passes its own `root` rather than
    opting out, so it still reads nothing of the operator's — see
    `HeartbeatAnnouncesArrivalsTest`, whose inbox genuinely resolves through the anchor.
    If some test must truly read the live store, its prefix stays a real gate input
    (ADR-0117) and the land keeps gating on it — reported, not hidden."""
    base = pathlib.Path(root) if root is not None else pathlib.Path(
        tempfile.mkdtemp(prefix="poga-live-store-"))
    if root is None:
        case.addCleanup(shutil.rmtree, base, ignore_errors=True)

    patch = unittest.mock.patch.object(session, "_shared_work_root", return_value=base)
    patch.start()
    case.addCleanup(patch.stop)

    # The near anchor. Restored rather than patched, so a caller that assigns its own
    # `JOURNAL_DIR` after this call keeps it and still gets cleaned up.
    prior_journal_dir = session.JOURNAL_DIR
    case.addCleanup(setattr, session, "JOURNAL_DIR", prior_journal_dir)
    session.JOURNAL_DIR = base / "sessions" / "journal"
    session.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    # `ARCHIVE` rides with it. `_next_ordinal` reads it DIRECTLY, not through
    # `JOURNAL_DIR`, so a class that carefully redirects the journal directory and
    # forgets this one still reads the operator's `sessions/pre-journal-archive.md` —
    # which is how `StampTest` kept 12 live reads after everything else was closed.
    prior_archive = session.ARCHIVE
    case.addCleanup(setattr, session, "ARCHIVE", prior_archive)
    session.ARCHIVE = base / "sessions" / "pre-journal-archive.md"

    # ...and the nearest anchor of all, but ONLY for a caller that supplied no root of
    # its own. `ROOT` is where `_wi_dir()` and `_roadmap_path()` resolve from, so a class
    # that never patches it runs `cmd_wi_render` against the operator's real store and
    # real `ROADMAP.md` — reading 109 work items, and reaching an `atomic_write` of that
    # file. It is a no-op today only because the render happens to match what is on disk;
    # nothing guards it, and `_under_test` does not cover this path. A caller that DID
    # pass a root owns `ROOT` itself and is left alone.
    if root is None:
        prior_root = session.ROOT
        case.addCleanup(setattr, session, "ROOT", prior_root)
        session.ROOT = base
        # ...and the CFG paths that live in the store. `CFG` is absolutised ONCE at
        # import, so moving `ROOT` does not move `CFG["inbox"]` (proposed-edits/),
        # `CFG["status"]` or `CFG["handoff"]`. `inbox_dirs()` re-bases the inbox only
        # when `_shared_work_root()` differs from `ROOT` — and here they are the same
        # tmpdir, so it handed back the REAL `proposed-edits/<id>/pending` and
        # `_inbox_names` listed it (ADR-0148 D3's guard, WI-0427). Which keys move is
        # decided by `_is_bookkeeping_path`, the same list the land and the guard read
        # (P16), not by a key list kept here.
        moved = {}
        for key, value in (session.CFG or {}).items():
            if not isinstance(value, pathlib.Path):
                continue
            try:
                rel = value.relative_to(prior_root).as_posix()
            except ValueError:
                continue
            if session._is_bookkeeping_path(rel):
                moved[key] = base / rel
        if moved:
            cfg_patch = unittest.mock.patch.dict(session.CFG, moved)
            cfg_patch.start()
            case.addCleanup(cfg_patch.stop)

    # THE SESSION-STATE DIRECTORY, which is where WI-0270 found this fixture one anchor
    # short. `SESSION_STATE_DIR` is a module global computed at IMPORT from the real repo,
    # exactly like `JOURNAL_DIR` above — so a caller that rebinds `ROOT` afterwards does
    # NOT move it, because the directory was already resolved. `test_attention` did
    # precisely that, and its `_beat()` drove the real `cmd_heartbeat` with a session id
    # of `"testsession"`, writing `testsession.live` into the OPERATOR'S `.session-state/`.
    #
    # A `.live` file with a fresh beat and no `.ended` is indistinguishable from a working
    # session BY DESIGN — that is what the file is FOR. So the escape did not look like
    # litter, it looked like a colleague: `recover-lanes` refused to land four lanes'
    # work for the full 48h crash grace, verbatim `[LIVE] testsess beat 17.7h ago --
    # inside its 48h grace; not provably dead` (session ~185). Still true eight days
    # later, on `poga-7`, whose real session had stamped `.ended` that morning.
    #
    # Note the direction against `neutralize_dispatch_env`'s. That guard exists because a
    # fixture could KILL a live session; this one because a fixture can PRESERVE a dead
    # one. Both are the fixture reaching live state, and the preserving direction is the
    # quieter of the two — nothing fails, work simply stops being recoverable.
    prior_state_dir = session.SESSION_STATE_DIR
    case.addCleanup(setattr, session, "SESSION_STATE_DIR", prior_state_dir)
    session.SESSION_STATE_DIR = base / ".session-state"
    session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)

    # ...and the two globals DERIVED from it at import, which do not follow it. This is
    # the same import-binding trap one level down, and missing them would leave the fix
    # half-applied in exactly the way that produced the bug. `_log_guard_firing` already
    # resolves `SESSION_STATE_DIR` at CALL time — its docstring says, in as many words,
    # that it was changed to do so "to let a test point `SESSION_STATE_DIR` somewhere
    # disposable" after 432 of 532 records in the real denial log turned out to be test
    # fixtures (2026-08-07). The mechanism it was written for is the line above; it never
    # shipped, so the log kept filling for five more weeks — `test_session`'s
    # `PeerInjectionViolationTest` appended its own fixture string to the operator's real
    # log on every run until WI-0270 gave that class this fixture.
    # NOTHING TO PATCH HERE ANY MORE, and that is the fix rather than an omission.
    # `SESSION_BANNER_FILE` and `GUARD_FIRINGS_FILE` were paths computed from
    # `SESSION_STATE_DIR` at import, so each had to be rebound separately and a THIRD
    # such file would have had to be remembered here too. Both are now names joined to
    # the directory at call time (`BANNER_NAME`, `GUARD_FIRINGS_NAME`), so redirecting
    # the one anchor above moves everything under it, including whatever gets added next
    # (WI-0286 / ADR-0133). The banner was the half that never got converted in 2026-08:
    # most of the real-store writes measured came through it, from modules
    # that had pinned the directory correctly and read as covered.

    # THE CWD IS THE FIFTH INPUT, and `exercise_real_coord_holder` above already says so
    # in as many words — it parks the CWD in an empty directory for precisely this
    # reason. `_find_holder_journal` (session.py:2904) asks
    # `_journal_in_checkout(Path.cwd().resolve())` BEFORE it consults
    # `POGA_INVOKED_FROM`, so the suite's own working directory — the operator's checkout
    # — gets its `sessions/journal` globbed and parsed no matter what `ROOT`,
    # `JOURNAL_DIR` and `_shared_work_root()` have been pointed at.
    #
    # This is why the first three anchors removed `work-items/`, `ops-items/` and
    # `ROADMAP.md` from the measured gate-input set and left `sessions/` exactly where it
    # was: three of the four routes were closed and the fourth is the one that does not
    # go through any of them.
    #
    # Parking the CWD at `base` rather than an arbitrary empty dir keeps the test inside
    # its own fixture: a module that legitimately has journals of its own still sees
    # them, and sees only them.
    prior_cwd = os.getcwd()
    case.addCleanup(os.chdir, prior_cwd)
    os.chdir(base)
    return base


# The fixture the store guard's failure message names (curate/store_guard.py).
def point_store_at(case, root=None):
    """Point EVERY route into the live store at `root` (or an empty tmpdir) for `case`.

    The entry point ADR-0148 D3 / WI-0427 names. Bookkeeping-only lands skip the suite,
    which is safe only if no test reads the real checkout's work-items/, sessions/,
    ops-items/, proposed-edits/, ... — and `curate/store_guard.py` now FAILS any test
    that does. This is the fixture its failure message points at.

    Not a second implementation (P16): it is `neutralize_live_store` (the path anchors
    and the CWD) plus `neutralize_dispatch_env`, because the store has one route neither
    covers alone. `_holder_journal_dirs` appends `POGA_INVOKED_FROM`/sessions/journal,
    and `poga` exports that as the MAIN checkout in every session — so a module that
    redirected every path anchor still globbed and parsed the operator's real journals
    through the environment (measured: 15 tests in `test_wi_commit`, 135 journal reads).
    Same arguments and return value as `neutralize_live_store`."""
    base = neutralize_live_store(case, root)
    neutralize_dispatch_env(case)
    return base


def neutralize_dispatch_env(case):
    """No dispatch handles for the duration of `case`. Call in setUp, alongside
    `neutralize_coord_journal`.

    WHY THIS IS NOT COSMETIC (WI-0249). The land gate runs this suite from INSIDE a
    dispatched lane, so `POGA_DISPATCH`, `POGA_DISPATCH_ITEM` and `POGA_LANE_CLOSE` are
    all set while the tests run, and the process ancestry above the suite is a real
    runtime in a real tmux session. Two live code paths key on exactly those variables:

      * `_lane_reserve` stamps `dispatch_id` into every reservation it writes, so a
        fixture's HAND-LAUNCHED lane reserves as dispatched and the cap tests invert.
        Found 2026-09-03 from lane poga-6 under dispatch D-788b4a, where it blocked a land.
      * `_holder_journal_dirs` (session.py:2820) appends `POGA_INVOKED_FROM`/<rel> to the
        journal directories it searches, so a fixture that believes it owns `JOURNAL_DIR`
        is in fact also reading the operator's REAL checkout and every live sibling lane.
        Reached from `_coord_reap` and `_attention_write` via `_attention_session_is_over`
        — a second entry point that `neutralize_coord_journal` does not cover, because
        that guard patches `_coord_holder` and this route never goes through it (WI-0242).
      * `_dispatch_close_runtime` ends the lane's runtime process. A fixture reaching
        `cmd_end`'s lane path with an inherited `POGA_DISPATCH` is one guard away from
        killing the session running the suite — a failure that destroys the evidence of
        itself, which is the shape this substrate keeps producing (WI-0105, WI-0156,
        WI-0169). The production code carries its own independent guard (the closed
        journal must be this runtime's own); this removes the input rather than relying
        on that guard alone, because two independent reasons to be safe is the right
        number when the cost of being wrong is a live session.

    Same hazard `ClaimsBase.setUp` names for the other three variables: a fixture that
    does not clear an ambient variable is testing the developer's machine."""
    prior = {v: os.environ.get(v) for v in DISPATCH_ENV_VARS}
    for v in DISPATCH_ENV_VARS:
        os.environ.pop(v, None)

    def _restore():
        for v, was in prior.items():
            if was is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = was

    case.addCleanup(_restore)
