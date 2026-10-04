"""Lane litter — the residue the lane machinery leaves when it does not exit cleanly.

Sibling of `test_worktree_lane.py` (which covers the lanes themselves) and
`test_janitor.py` (which covers the sweep's own rules). What is pinned here is the class
operator named in session ~125, with the ruling that time spent fixing stranded lanes in any
session, with any architect, should be zero. Stranded **work** was solved
by ADR-0089; this is the litter left behind it —

  WI-0063  scratch worktrees from a merge gate whose process died before its `finally`;
  WI-0078  coordination claims outliving the lane that held them, on every teardown route
           except the one the release was wired to;
  WI-0074  a released claim leaving no trace, so "released cleanly" and "never claimed"
           read identically afterwards;
  WI-0085  a lane's janitor sweeping the lane's own sidecar dir and never the main
           checkout's, which is where the pile actually accumulates.

Two of these tests exist specifically to fail if the WIRING is removed while the helpers
stay correct — the shape that left `_auto_reap_lanes` dead for a day with green tests
(session 90) and that ADR-0089 had to add a call-site assertion to catch.
"""

import argparse
import contextlib
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
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("base\n", encoding="utf-8")
    # Lane worktrees live under `.claude/worktrees/`, which every real member gitignores —
    # without this the fixture's main checkout reads as permanently dirty and the
    # main-visibility tests measure the fixture instead of the code.
    (path / ".gitignore").write_text(".claude/\n.session-state/\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    _git(path, "branch", "-M", "main")


def _fake_procs(self, *specs):
    """Patch `_lane_processes` with fabricated rows.

    Each spec is `(pid, lane, age_sec)` or `(pid, lane, age_sec, cwd)`. **`cwd` defaults to
    this repo's own `<lanes_root>/<lane>`**, which is what makes a fabricated process
    OURS — since WI-0135 a lane process is attributed to a repo by its working directory,
    not by its lane name, and an unattributable process is unprovable rather than stranded.
    Taken from `session._lanes_root()` at call time rather than rebuilt from `self.main`,
    so the fixture cannot disagree with the code under test about symlinked temp paths.

    Pass an explicit `cwd` (a foreign path, or None) to exercise the gate itself."""
    root = session._lanes_root()
    rows = []
    for spec in specs:
        pid, lane, age = spec[0], spec[1], spec[2]
        cwd = spec[3] if len(spec) > 3 else str(root / lane)
        rows.append({"pid": pid, "lane": lane, "age_sec": age, "cmd": "claude",
                     "cwd": cwd})
    return mock.patch.object(session, "_lane_processes", return_value=rows)


@unittest.skipUnless(GIT, "git not available")
class LitterBase(unittest.TestCase):
    def setUp(self):
        # Dead-lane reclaim and tombstone assertions turn on WHO holds a record; without
        # this every fictional lane inherits the runner's journal (WI-0126).
        # WI-0242: this module reaches `_holder_journal_dirs` (via `_coord_reap` /
        # `_attention_write`), which appends `POGA_INVOKED_FROM` to the journal
        # directories it scans — the operator's REAL checkout and every live sibling
        # lane. `neutralize_coord_journal` does not cover this route: it patches
        # `_coord_holder`, and nothing here goes through it.
        # WI-0275 folds that clear into the ambient neutraliser, a strict superset of
        # `neutralize_dispatch_env` that also clears the identity `_coord_identity`
        # falls back to. One call, both axes.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        _init_repo(self.main)
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        # JOURNAL_DIR is rebound with the rest (WI-0146). It was not, and this module
        # calls `janitor_sweep`, whose journal half then ran against the REAL checkout's
        # tracked journals while its sidecar half correctly used the fixture — the exact
        # leak that stamped `closed-by: janitor` into 20260814T1220Z-runner-5599.md and
        # made the second suite run in a main checkout fail six unrelated test_claims
        # cases. The runtime guard now refuses that write; this makes the fixture honest
        # so the guard has nothing to refuse.
        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "SESSION_STATE_DIR", "CFG")}
        session.ROOT = self.main
        session.JOURNAL_DIR = self.main / "sessions" / "journal"
        session.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        session.SESSION_STATE_DIR = self.main / ".session-state"
        session.CFG = {
            "tz": ZoneInfo("UTC"), "machine_map": {},
            "architect_name": "Test", "architect_id": "test-arch", "trunk": "main",
        }

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_gate_worktree(self, repo, host_dir, age_minutes):
        """Register a `<host>/fed-gate-XXXX/wt` scratch worktree against `repo`, exactly
        as `_gate_commit` does, and backdate it by `age_minutes`."""
        host_dir.mkdir(parents=True, exist_ok=True)
        gate = pathlib.Path(tempfile.mkdtemp(prefix=session.GATE_WORKTREE_PREFIX,
                                             dir=str(host_dir)))
        wt = gate / "wt"
        head = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        _git(repo, "worktree", "add", "--detach", "--quiet", str(wt), head)
        old = time.time() - age_minutes * 60
        import os
        os.utime(gate, (old, old))
        return wt


class GateResidueTest(LitterBase):
    """WI-0063 — `_gate_commit` cleans up in a `finally`, so a killed process leaks a
    REGISTERED worktree. Eleven accumulated in one four-minute burst on 2026-07-28."""

    def test_an_aged_leaked_gate_worktree_is_found_and_freed(self):
        wt = self._add_gate_worktree(self.main, self.tmp / "t", age_minutes=120)
        self.assertEqual([p.resolve() for p in session._gate_worktree_residue()],
                         [wt.resolve()])
        lines = session._reap_gate_worktrees()
        self.assertTrue(any("freed leaked gate worktree" in l for l in lines), lines)
        self.assertFalse(wt.exists())
        listed = subprocess.run([GIT, "-C", str(self.main), "worktree", "list"],
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn(session.GATE_WORKTREE_PREFIX, listed)

    def test_a_young_gate_worktree_is_left_alone(self):
        """A concurrent land in THIS repo has a live gate worktree minutes old. Reaping it
        would fail someone else's land, so age is the guard."""
        wt = self._add_gate_worktree(self.main, self.tmp / "t", age_minutes=1)
        self.assertEqual(session._gate_worktree_residue(), [])
        self.assertEqual(session._reap_gate_worktrees(), [])
        self.assertTrue(wt.exists())

    def test_another_repos_gate_worktree_in_the_same_temp_dir_is_invisible(self):
        """THE load-bearing safety property (WI-0063). `session.py` is byte-identical
        fleet substrate, so every member stages `fed-gate-*` in the same temp
        directory — one was seen appearing there mid-cleanup on 2026-07-30. A sweeper that
        globbed the prefix would destroy another repo's in-flight land. Driving from
        `git worktree list` means a foreign gate is never even visible."""
        other = self.tmp / "other-repo"
        _init_repo(other)
        shared = self.tmp / "t"
        foreign = self._add_gate_worktree(other, shared, age_minutes=120)
        mine = self._add_gate_worktree(self.main, shared, age_minutes=120)

        # The naive implementation, spelled out so the hazard is pinned rather than
        # described: a filesystem glob over the shared prefix sees BOTH repos' scratch
        # trees and cannot tell them apart. This assertion is what makes the next one
        # meaningful — without it, "found only mine" could just mean "found nothing much".
        globbed = sorted(p.name for p in shared.glob(f"{session.GATE_WORKTREE_PREFIX}*"))
        self.assertEqual(len(globbed), 2,
                         "fixture no longer reproduces the shared-prefix collision")

        self.assertEqual([p.resolve() for p in session._gate_worktree_residue()],
                         [mine.resolve()])
        session._reap_gate_worktrees()
        self.assertFalse(mine.exists())
        self.assertTrue(foreign.exists(), "reaped ANOTHER repo's gate worktree")

    def test_a_real_lane_worktree_is_never_mistaken_for_gate_residue(self):
        self.assertEqual(session._gate_worktree_residue(), [])
        self.assertTrue(self.lane.exists())


class DeadLaneClaimTest(LitterBase):
    """WI-0078 — release was wired to ONE teardown route (`cmd_worktree_remove`, and only
    on the branch delete succeeding). The reaper and manual teardown both left the holds
    behind; one member's survived an orderly teardown by a week."""

    def _claim(self, name, identity):
        ok, _ = session._coord_try_acquire("claims", name, identity,
                                           session.CLAIM_TTL_SECONDS)
        self.assertTrue(ok)

    def test_a_claim_held_by_a_vanished_lane_is_released(self):
        self._claim("WI-0001", "worktree-poga-9")     # no such branch
        freed = session._coord_release_dead_lanes()
        self.assertEqual(freed, [("claims", "WI-0001")])
        self.assertEqual(session._coord_list("claims", include_expired=True), {})

    def test_a_claim_held_by_a_lane_that_still_exists_is_kept(self):
        self._claim("WI-0002", "worktree-poga-1")     # the live lane from setUp
        self.assertEqual(session._coord_release_dead_lanes(), [])
        self.assertIn("WI-0002", session._coord_list("claims"))

    def test_a_non_lane_identity_is_left_to_the_ttl(self):
        """An absent Claude session id is not evidence of anything — treating it as proof
        of death is the `declare-what-a-check-assumes` collapse."""
        self._claim("WI-0003", "some-claude-session-id")
        self.assertEqual(session._coord_release_dead_lanes(), [])
        self.assertIn("WI-0003", session._coord_list("claims"))

    def test_manual_teardown_is_covered_because_the_test_is_the_branch_not_the_route(self):
        """The documented route when a live sibling must be preserved: remove the worktree
        and delete the branch by hand, so no hook fires at all."""
        self._claim("WI-0004", "worktree-poga-1")
        _git(self.main, "worktree", "remove", str(self.lane))
        _git(self.main, "branch", "-D", "worktree-poga-1")
        self.assertEqual(session._coord_release_dead_lanes(), [("claims", "WI-0004")])

    def test_the_reaper_actually_calls_it(self):
        """Call-site assertion. Every test above passes just as happily with the wiring
        removed — the exact shape that left `_auto_reap_lanes` dead for a day (session
        90)."""
        with mock.patch.object(session, "_coord_release_dead_lanes") as m, \
             mock.patch.object(session, "_scan_lanes", return_value=[]):
            session._auto_reap_lanes()
        m.assert_called_once()

    def test_the_reaper_actually_sweeps_gate_worktrees(self):
        with mock.patch.object(session, "_reap_gate_worktrees") as m, \
             mock.patch.object(session, "_scan_lanes", return_value=[]):
            session._auto_reap_lanes()
        m.assert_called_once()


class ReleaseTombstoneTest(LitterBase):
    """WI-0074 — `_coord_release` ended in a bare `unlink()`, so afterwards a clean release
    and a session that never coordinated were byte-identical from outside. A status
    reader had to answer "released or never claimed — not distinguishable"."""

    def test_a_release_leaves_a_tombstone_naming_the_holder_and_the_reason(self):
        session._coord_try_acquire("claims", "WI-0007", "worktree-poga-1",
                                   session.CLAIM_TTL_SECONDS)
        self.assertTrue(session._coord_release("claims", "WI-0007", "worktree-poga-1"))
        stones = session._coord_tombstones("claims")
        self.assertIn("WI-0007", stones)
        rec = stones["WI-0007"]
        self.assertEqual(rec["session_id"], "worktree-poga-1")
        self.assertEqual(rec["reason"], "released")
        self.assertTrue(rec["claimed_at"])
        self.assertTrue(rec["released_at"])

    def test_the_reason_distinguishes_release_from_teardown(self):
        session._coord_try_acquire("claims", "WI-0008", "worktree-poga-9",
                                   session.CLAIM_TTL_SECONDS)
        session._coord_release_dead_lanes()
        self.assertEqual(session._coord_tombstones("claims")["WI-0008"]["reason"],
                         "teardown")

    def test_a_tombstone_is_not_a_live_record(self):
        """It must be invisible to every reader of the kind, or a released claim would
        read as still held."""
        session._coord_try_acquire("claims", "WI-0009", "worktree-poga-1",
                                   session.CLAIM_TTL_SECONDS)
        session._coord_release("claims", "WI-0009", "worktree-poga-1")
        self.assertEqual(session._coord_list("claims", include_expired=True), {})
        ok, _ = session._coord_try_acquire("claims", "WI-0009", "worktree-poga-2",
                                           session.CLAIM_TTL_SECONDS)
        self.assertTrue(ok, "a tombstone blocked a re-claim")

    def test_an_expired_tombstone_is_reaped_so_the_trace_dir_is_self_limiting(self):
        session._coord_try_acquire("claims", "WI-0010", "worktree-poga-1",
                                   session.CLAIM_TTL_SECONDS)
        session._coord_release("claims", "WI-0010", "worktree-poga-1")
        p = session._coord_tombstone_dir("claims") / "WI-0010.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["expires_at"] = time.time() - 1
        p.write_text(json.dumps(rec), encoding="utf-8")
        self.assertGreaterEqual(session._coord_reap(), 1)
        self.assertFalse(p.exists())
        self.assertEqual(session._coord_tombstones("claims"), {})


# WI-0244: every counter's reservation namespace, read off the registry rather than
# spelled out. The defect these tests now cover was a HAND-LISTED pair — `adr-alloc` and
# `wi-alloc` — that `ops-alloc` was never added to, so a test naming the same two kinds
# would have gone green beside the hole. Driving the loop off `_counters()` means a
# counter row added tomorrow is exercised here with no edit, which is the same
# by-construction property the reaper itself now has.
def _alloc_namespaces():
    return sorted(c.namespace for c in session._counters().values())


# The on-trunk filename each counter would land, keyed by counter. Hand-written on
# purpose — a filename derived from the row's own regex would only prove the regex
# matches itself. `test_every_counter_has_a_landed_filename` fails loudly when a new
# counter arrives without one, so this cannot rot into a silent skip.
def _landed_name(key, num):
    return {
        "adr": lambda: f"{session._adr_prefix()}{num}-landed.md",
        "wi": lambda: f"WI-{num}-landed.md",
        "ops": lambda: f"OPS-{num}-landed.md",
    }[key]()


class ImmortalReservationTest(LitterBase):
    """The R5b rule holds a TTL-expired allocator reservation while its branch still
    exists, so a lane-local file cannot have its number re-issued. The trunk never stops
    existing — so a reservation drawn from the MAIN checkout and then abandoned was held
    FOREVER. Three were found in the live store at session ~125, oldest expired 11 days;
    two of them (0083, 0086) are deliberately burned numbers that can never appear on the
    trunk by construction.

    WI-0244 widened these from the two kinds the rule was written for to EVERY counter in
    the registry. `ops-alloc` had been in `COORD_ALLOC_KINDS` and gated at the land since
    ADR-0076 D2, but the reaper's carve-out was two hardcoded `if kind ==` branches, so an
    OPS number held by a live lane was freed on its 8h TTL and could be re-issued to a
    sibling."""

    def _reserve(self, kind, num, branch, expired=True):
        d = session._coord_dir(kind, create=True)
        rec = {"session_id": "x", "branch": branch, "name": num, "kind": kind,
               "expires_at": time.time() - 1 if expired else time.time() + 3600}
        (d / f"{num}.json").write_text(json.dumps(rec), encoding="utf-8")
        return d / f"{num}.json"

    def test_an_abandoned_trunk_reservation_is_reaped(self):
        for kind in _alloc_namespaces():
            with self.subTest(kind=kind):
                p = self._reserve(kind, "0072", "main")
                session._coord_reap()
                self.assertFalse(p.exists(), f"{kind} reservation held immortally")

    def test_a_live_lane_reservation_is_still_protected(self):
        """The rule the fix must not break: the number's file is lane-local until land.

        This is the WI-0244 reproduction. Before the fix `ops-alloc` failed here and the
        other two passed — the number freed while the lane holding it was still running.
        """
        for kind in _alloc_namespaces():
            with self.subTest(kind=kind):
                p = self._reserve(kind, "0073", "worktree-poga-1")
                session._coord_reap()
                self.assertTrue(p.exists(), f"{kind} reservation freed while its lane lives")

    def test_a_vanished_lanes_reservation_is_reaped(self):
        for kind in _alloc_namespaces():
            with self.subTest(kind=kind):
                p = self._reserve(kind, "0074", "worktree-poga-99")
                session._coord_reap()
                self.assertFalse(p.exists())

    def test_an_unexpired_trunk_reservation_is_untouched(self):
        """A live main-checkout session keeps its own hold alive by heartbeat."""
        p = self._reserve("adr-alloc", "0075", "main", expired=False)
        session._coord_reap()
        self.assertTrue(p.exists())

    def test_every_counter_has_a_landed_filename(self):
        """The guard on `_landed_name`'s hand-written map. A new counter row must arrive
        with the name it lands under, or the trunk-visibility test below would quietly
        stop covering it — the exact shape of the defect WI-0244 fixed."""
        for c in session._counters().values():
            with self.subTest(counter=c.key):
                self.assertTrue(_landed_name(c.key, "0001"))

    @unittest.skipUnless(GIT, "git not available")
    def test_a_number_already_on_the_trunk_is_reaped_even_with_a_live_lane(self):
        """The other half of the carve-out, and the half that keeps it from leaking
        numbers: the hold is on TRUNK VISIBILITY, not on the branch. Once the file is
        landed the reservation has nothing left to protect, so it reaps even though the
        lane that drew it is still alive."""
        for c in session._counters().values():
            with self.subTest(counter=c.key):
                num = "0091"
                d = self.main / c.dirname()
                d.mkdir(parents=True, exist_ok=True)
                (d / _landed_name(c.key, num)).write_text("x\n", encoding="utf-8")
                _git(self.main, "add", "-A")
                _git(self.main, "commit", "-qm", f"land {c.key} {num}")
                p = self._reserve(c.namespace, num, "worktree-poga-1")
                session._coord_reap()
                self.assertFalse(p.exists(),
                                 f"{c.namespace} {num} held after its file landed")

    def test_lane_alloc_has_no_carve_out_and_reaps_on_its_ttl(self):
        """`lane-alloc` is an allocator kind with NO counter row, on purpose — it reserves
        a lane number, which has no file that could be invisible on the trunk. The
        registry lookup returning None is the right answer for it, not a miss, and the TTL
        is the whole of its rule."""
        self.assertIsNone(session._counter_by_namespace("lane-alloc"))
        p = self._reserve("lane-alloc", "0001", "worktree-poga-1")
        session._coord_reap()
        self.assertFalse(p.exists(), "lane-alloc grew a carve-out it should not have")


@unittest.skipUnless(GIT, "git not available")
class JanitorScanSetTest(LitterBase):
    """WI-0085 — `.session-state/` is per-tree and `SESSION_STATE_DIR` is anchored on
    `ROOT`, so a lane's janitor swept the lane's handful of files and never main's. With
    ADR-0059 steering every session into a lane, main's pile was the one nobody was
    positioned to sweep: 78 stale `.live` markers, oldest 2026-07-05, while the sweep
    reported itself as having run."""

    def _stale(self, d, name, days=30):
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text("x", encoding="utf-8")
        old = time.time() - days * 86400
        import os
        os.utime(p, (old, old))
        return p

    def _as_lane(self):
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.lane / "sessions" / "journal"
        session.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        session.SESSION_STATE_DIR = self.lane / ".session-state"

    def test_a_lane_sees_the_main_checkouts_sidecar_dir(self):
        (self.main / ".session-state").mkdir(exist_ok=True)
        (self.lane / ".session-state").mkdir(exist_ok=True)
        self._as_lane()
        dirs = {p.resolve() for p in session._janitor_sidecar_dirs()}
        self.assertIn((self.main / ".session-state").resolve(), dirs)
        self.assertIn((self.lane / ".session-state").resolve(), dirs)

    def test_a_sweep_run_from_a_lane_prunes_the_main_checkouts_stale_residue(self):
        stale = self._stale(self.main / ".session-state", "old-csid.live")
        self._as_lane()
        res = session.janitor_sweep(current_csid=None)
        self.assertGreaterEqual(res["pruned"], 1)
        self.assertFalse(stale.exists(), "main's pile is still the one nobody sweeps")

    def test_the_main_checkout_is_not_swept_twice(self):
        self._stale(self.main / ".session-state", "old-csid.live")
        dirs = session._janitor_sidecar_dirs()      # ROOT is main here
        self.assertEqual(len({p.resolve() for p in dirs}), len(dirs))

    def test_a_live_sessions_residue_is_never_pruned_from_either_tree(self):
        d = self.main / ".session-state"
        d.mkdir(exist_ok=True)
        fresh = d / "live-csid.live"
        fresh.write_text("x", encoding="utf-8")
        self._as_lane()
        session.janitor_sweep(current_csid="live-csid")
        self.assertTrue(fresh.exists())


@unittest.skipUnless(GIT, "git not available")
class LaneBirthTest(LitterBase):
    """The `predates` proof needs a creation time, and `st_birthtime` is a macOS/BSD field
    Python does not expose on Linux. Shipping only that would have made this detector
    quietly weaker on every Linux member — not broken, not warned about, just never firing
    for the harder half of the class."""

    @unittest.skipUnless(hasattr(__import__("os").stat_result, "st_birthtime"),
                         "this platform's Python has no st_birthtime (Linux): the "
                         "git-registration fallback below is the source there (WI-0468)")
    def test_the_filesystem_birth_time_is_preferred(self):
        birth, source = session._lane_birth(self.lane)
        self.assertEqual(source, "st_birthtime")
        self.assertIsNotNone(birth)

    def test_it_falls_back_to_gits_own_registration_record(self):
        """Simulates Linux, which is otherwise unreachable from the machine this is
        developed on."""
        with mock.patch.object(session, "_fs_birthtime", return_value=None):
            birth, source = session._lane_birth(self.lane)
        self.assertEqual(source, "git-registration")
        self.assertIsNotNone(birth)

    def test_both_sources_agree_on_the_live_pool(self):
        """The fallback is only worth having if it reports the same moment. Verified on the
        real pool when this was designed; pinned here so it stays true."""
        fs, _ = session._lane_birth(self.lane)
        with mock.patch.object(session, "_fs_birthtime", return_value=None):
            git_reg, _ = session._lane_birth(self.lane)
        self.assertLess(abs(fs - git_reg), 5.0)

    def test_no_source_is_unavailable_not_zero(self):
        with mock.patch.object(session, "_fs_birthtime", return_value=None), \
             mock.patch.object(session, "_git_common_dir", return_value=None):
            self.assertEqual(session._lane_birth(self.lane), (None, "unavailable"))

    def test_a_dragged_birth_time_fails_in_the_SAFE_direction(self):
        """`st_birthtime` is NOT immutable on macOS — setting an mtime older than the
        creation date drags the birth time down with it (found by this very test, which
        was originally written asserting the opposite). Anything that rewrites mtimes — a
        restore, `rsync -t`, a backup tool — can therefore move the proof.

        What matters is WHICH WAY it moves. The predicate is
        `process_start < birth - grace`, so a birth time dragged EARLIER makes the
        condition harder to satisfy: fewer processes qualify, never more. The failure mode
        is a stranded process we decline to kill, not a live one we do. Pinned here because
        the safe direction is a property of the comparison, and someone could flip it while
        refactoring without noticing what it cost.

        WI-0468: BOTH sources are dragged. On Linux there is no `st_birthtime` and the
        birth comes from git's registration record (the `gitdir` file's mtime), which a
        restore or `rsync -t` rewrites just the same. Dragging only the directory left
        the Linux birth at "now", so the fixture's 3-day-old process genuinely predated
        the lane — a true positive for that source, not the dragged case this pins."""
        import os as _os
        old = time.time() - 9 * 86400
        _os.utime(self.lane, (old, old))
        gitdir = session._git_common_dir() / "worktrees" / self.lane.name / "gitdir"
        self.assertTrue(gitdir.is_file(), gitdir)
        _os.utime(gitdir, (old, old))
        birth, source = session._lane_birth(self.lane)
        self.assertIn(source, ("st_birthtime", "git-registration"))
        self.assertLess(birth, time.time() - 8 * 86400, f"{source} was not dragged")
        with _fake_procs(self, (5199, self.lane.name, 3 * 86400)):
            proven, _ = session._classify_lane_processes()
        self.assertEqual(proven, [],
                         "a dragged birth time made a process look MORE stranded — the "
                         "comparison has been inverted and this can now kill live work")


@unittest.skipUnless(GIT, "git not available")
class UnprovableIsNotProvenTest(LitterBase):
    """The safety property that has to hold by construction: a question we could not ask
    must never arrive in the list the killer acts on."""

    _procs = _fake_procs

    def test_an_unprovable_process_never_reaches_the_proven_list(self):
        with self._procs((5150, "poga-1", 9 * 86400)), \
             mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")):
            proven, unprovable = session._classify_lane_processes()
        self.assertEqual(proven, [])
        self.assertEqual([p["pid"] for p in unprovable], [5150])
        with self._procs((5150, "poga-1", 9 * 86400)), \
             mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")), \
             mock.patch.object(session.os, "kill") as k:
            session._reap_lane_processes()
        k.assert_not_called()

    def test_the_banner_says_it_could_not_check_rather_than_going_quiet(self):
        with self._procs((5151, "poga-1", 9 * 86400)), \
             mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")):
            lines = session._stranded_process_lines()
        self.assertTrue(any("NOT CHECKED" in l for l in lines), lines)

    def test_lane_gone_still_works_where_birth_is_unavailable(self):
        """The weaker proof must survive — a Linux member keeps the `lane-gone` half."""
        with self._procs((5152, "poga-99", 9 * 86400)), \
             mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")):
            proven, _ = session._classify_lane_processes()
        self.assertEqual([(p["pid"], p["reason"]) for p in proven], [(5152, "lane-gone")])


@unittest.skipUnless(GIT, "git not available")
class MainCheckoutVisibilityTest(LitterBase):
    """WI-0089 one level up. A lane is structurally blind to the shared checkout it
    depends on — the harness refuses `git -C <main>` and refuses editing a shared-checkout
    path, both correctly — so main's dirt was found only by tripping over it: a land that
    quietly declines to sync, a refused substrate push, or a session-end from main
    committing it verbatim (which would have reverted landed ROADMAP prose)."""

    def _as_lane(self):
        session.ROOT = self.lane

    def test_a_clean_main_is_silent(self):
        self._as_lane()
        self.assertEqual(session._main_checkout_lines(), [])

    def test_a_dirty_main_is_named_in_one_row(self):
        """the stale-materialization deadlock collapsed this to ONE row. It was a header, up to
        `MAIN_DIRT_REPORT_CAP` dirt entries, an elision line, a three-clause consequences
        paragraph and a fix line — eleven lines, every session, for a condition that was
        usually not authored work at all but a stale materialization the substrate can now
        repair for itself before this ever renders.

        The consequences prose went with it, deliberately. Its lead clause ("a land here
        will NOT fast-forward main") described exactly the case that is now handled
        automatically, so keeping it would have left the loudest sentence on the row
        asserting something the substrate had already fixed. What survives is what a reader
        can act on: the count, the names, and the verb.
        """
        (self.main / "someone-elses-work.txt").write_text("x", encoding="utf-8")
        self._as_lane()
        lines = session._main_checkout_lines()
        rows = [l for l in lines if l.startswith("main:")]
        self.assertEqual(len(rows), 1, rows)
        # WI-0097 changed what this line may claim. It used to say the lane "cannot clean
        # them" and then tell the reader to open a session in the main checkout — an
        # instruction addressed to a session that by standing policy never exists. Now the
        # lane CAN clean what the trunk has provably superseded, so the banner names the
        # verb. Asserting the absence too: a nag whose only remedy is a place the user
        # refuses to go is what trained him to skim this whole block.
        self.assertIn("someone-elses-work.txt", rows[0])
        self.assertIn("not yours", rows[0])
        self.assertIn("main-restore", rows[0])
        self.assertNotIn("open one in the main checkout", rows[0])

    def test_the_names_on_the_row_are_capped(self):
        """One row must stay one row. The cap moved inline with the names it bounds — a
        row that grows without limit is the block this replaced, wearing one newline."""
        for i in range(session.MAIN_DIRT_REPORT_CAP + 5):
            (self.main / f"f{i}.txt").write_text("x", encoding="utf-8")
        self._as_lane()
        rows = [l for l in session._main_checkout_lines() if l.startswith("main:")]
        self.assertEqual(len(rows), 1, rows)
        named = [f"f{i}.txt" for i in range(session.MAIN_DIRT_REPORT_CAP + 5)
                 if f"f{i}.txt" in rows[0]]
        self.assertEqual(len(named), session.MAIN_DIRT_REPORT_CAP, rows[0])
        self.assertIn("+5 more", rows[0])

    def test_unreadable_is_reported_distinctly_from_clean(self):
        self._as_lane()
        real = session.sh

        def only_status_fails(args, *a, **kw):
            # Fail ONLY the status probe. Mocking `sh` wholesale also breaks
            # `_on_worktree_lane` and `_main_checkout`, so the function returns None and
            # the test passes for the wrong reason — it would be measuring the mock.
            if "status" in args:
                return subprocess.CompletedProcess(args, 1, "", "boom")
            return real(args, *a, **kw)

        with mock.patch.object(session, "sh", side_effect=only_status_fails):
            lines = session._main_checkout_lines()
        self.assertTrue(any("NOT CHECKED" in l for l in lines), lines)

    def test_it_says_nothing_from_the_main_checkout_itself(self):
        (self.main / "dirt.txt").write_text("x", encoding="utf-8")
        self.assertEqual(session._main_checkout_lines(), [])

    def test_it_never_writes(self):
        """The guards it reads around exist to stop a lane MUTATING another tree. This
        must stay a read, or it becomes the thing they were protecting against."""
        (self.main / "dirt.txt").write_text("x", encoding="utf-8")
        self._as_lane()
        before = sorted(p.name for p in self.main.iterdir())
        session._main_checkout_lines()
        self.assertEqual(sorted(p.name for p in self.main.iterdir()), before)


@unittest.skipUnless(GIT, "git not available")
class RenderCommitsWhatItWritesTest(LitterBase):
    """WI-0089's mechanism, closed. `poga work` anchors on the MAIN checkout by design, so
    a render driven from a lane writes main's ROADMAP.md and then had no route to commit
    it — the lane cannot reach that tree. The dirt sat until some later session-end swept
    it VERBATIM, which is how a render could revert landed prose.

    The precise question before committing is NOT "was the file clean" — this file is half
    rendered and half hand-authored, so a previous render leaves it dirty in a way that is
    entirely ours, while one edited prose line is not ours at all."""

    B, E = session.WI_GEN_BEGIN, session.WI_GEN_END

    def _write_roadmap(self, prose, generated, second=""):
        """Two marker pairs — `wi-render` renders Next and Backlog and refuses to fill a
        region it cannot name, rather than inventing structure to splice into.

        The `## Next` heading is load-bearing since ADR-0105: regions are filled by the
        heading above them rather than by document position, so a marker pair floating
        under bare prose is unnameable and correctly refused. This fixture used to omit
        it — modelling a shape no real ROADMAP has, since the format has required these
        headings since ADR-0030."""
        p = self.main / "ROADMAP.md"
        p.write_text(
            f"# Roadmap\n\n{prose}\n\n"
            f"## Next\n\n{self.B}\n\n{generated}\n{self.E}\n\n"
            f"## Backlog\n\n{self.B}\n\n{second}\n{self.E}\n",
            encoding="utf-8")
        return p

    def setUp(self):
        super().setUp()
        self._write_roadmap("hand-authored prose", "- old rendered row")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "roadmap")

    def test_strip_leaves_only_the_hand_authored_remainder(self):
        text = (self.main / "ROADMAP.md").read_text(encoding="utf-8")
        stripped = session._strip_generated_regions(text)
        self.assertIn("hand-authored prose", stripped)
        self.assertNotIn("old rendered row", stripped)

    def test_a_generated_only_change_is_safe_to_commit(self):
        new = (self.main / "ROADMAP.md").read_text(encoding="utf-8").replace(
            "- old rendered row", "- NEW rendered row")
        self.assertTrue(session._wi_only_generated_differs(new))

    def test_an_edited_prose_line_is_NOT_safe_to_commit(self):
        """The harm arriving from the other direction: folding someone's prose into a
        commit titled as a render."""
        new = (self.main / "ROADMAP.md").read_text(encoding="utf-8").replace(
            "hand-authored prose", "hand-authored prose, edited by a human")
        self.assertFalse(session._wi_only_generated_differs(new))

    def test_a_previous_render_left_dirty_is_still_safe(self):
        """The case that made 'was the file clean?' the wrong test — and the exact state
        this session produced live."""
        p = self._write_roadmap("hand-authored prose", "- render one")
        newer = p.read_text(encoding="utf-8").replace("- render one", "- render two")
        self.assertTrue(session._wi_only_generated_differs(newer))

    def test_an_earlier_renders_output_is_committed_even_when_this_run_changes_nothing(self):
        """The wrong-baseline mistake one level in, observed live the first time this
        shipped: `rendered == text` returned early, so an EARLIER render's uncommitted
        output sat there forever. The baseline that matters is HEAD, not the working file."""
        # NO `addCleanup` to restore ROOT here: cleanups run AFTER tearDown, so restoring
        # it would re-point ROOT at the temp dir tearDown has just deleted — poisoning
        # every later test in the suite. `LitterBase.tearDown` already restores it, and it
        # is the only thing that should. (Cost of learning this: 7 failures in
        # test_wi_commit that passed in isolation.)
        session.ROOT = self.main
        # Simulate the live state: the file on disk already holds a previous render.
        self._write_roadmap("hand-authored prose", "- render from an earlier run")
        with mock.patch.object(session, "_wi_render_next_section",
                               return_value="- render from an earlier run"), \
             mock.patch.object(session, "_wi_render_backlog_section", return_value=""), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            session.cmd_wi_render(argparse.Namespace())
        self.assertIn("already current", buf.getvalue())
        dirty = subprocess.run([GIT, "-C", str(self.main), "status", "--porcelain",
                                "--", "ROADMAP.md"], capture_output=True, text=True).stdout
        self.assertEqual(dirty.strip(), "",
                         "an earlier render's output was left uncommitted")

    def test_it_fails_CLOSED_when_git_cannot_answer(self):
        with mock.patch.object(session, "sh") as m:
            m.return_value = subprocess.CompletedProcess([], 1, "", "boom")
            self.assertFalse(session._wi_only_generated_differs("anything"))


class EtimeParseTest(unittest.TestCase):
    """`ps -o etime` is `[[dd-]hh:]mm:ss`. Chosen over `lstart` because `lstart` renders
    day/month NAMES and would stop parsing under a non-English locale — reporting "no
    stranded processes" rather than failing."""

    def test_the_four_shapes(self):
        self.assertEqual(session._parse_etime("30:14"), 30 * 60 + 14)
        self.assertEqual(session._parse_etime("09:47:11"), 9 * 3600 + 47 * 60 + 11)
        self.assertEqual(session._parse_etime("08-13:02:24"),
                         8 * 86400 + 13 * 3600 + 2 * 60 + 24)
        self.assertEqual(session._parse_etime("  44:47 "), 44 * 60 + 47)

    def test_garbage_is_none_not_zero(self):
        """Zero would read as 'just started' and protect a stranded process forever."""
        for bad in ("", "nonsense", "1:2:3:4", "Thu Jan  1", "-", "aa:bb"):
            self.assertIsNone(session._parse_etime(bad), bad)


@unittest.skipUnless(GIT, "git not available")
class StrandedProcessTest(LitterBase):
    """Session ~124: four Claude sessions alive in lanes that no longer existed, oldest up
    7d17h, dispatched on an item that shipped in 6.1.0 — and the harness classifier refuses
    a bare `kill` from inside a session, so clearing them fell to operator."""

    _procs = _fake_procs

    def test_a_process_naming_a_lane_that_does_not_exist_is_stranded(self):
        with self._procs((4242, "poga-9", 3600)):
            got = session._stranded_lane_processes()
        self.assertEqual([(p["pid"], p["reason"]) for p in got], [(4242, "lane-gone")])

    def test_a_process_older_than_the_lane_it_names_is_stranded(self):
        """Lane names are RECYCLED, so "the lane exists" was never the question. The
        9-day-old process in session ~124 named `poga-1` — and a live `poga-1` existed."""
        with self._procs((4243, "poga-1", 9 * 86400)):
            got = session._stranded_lane_processes()
        self.assertEqual([(p["pid"], p["reason"]) for p in got], [(4243, "predates")])

    def test_the_process_that_created_the_lane_is_never_flagged(self):
        """THE near-miss, found by probing rather than reasoning (session ~125): a lane's
        own process necessarily starts BEFORE the directory exists — measured live, this
        session's pid started one second before its lane dir. Without the grace margin the
        detector reports the session running it as stranded, and `--processes` would then
        terminate the session that asked."""
        with self._procs((4244, "poga-1", 1)):
            self.assertEqual(session._stranded_lane_processes(), [])
        # …and the margin is generous enough to cover a slow lane start, not just 1s.
        with self._procs((4245, "poga-1",
                          session.LANE_PROCESS_BIRTH_GRACE_MIN * 60 - 30)):
            self.assertEqual(session._stranded_lane_processes(), [])

    def test_two_live_processes_on_one_lane_are_ambiguous_and_left_alone(self):
        """A resumed session is an ordinary state. `ambiguous` must not be folded in with
        `proven` — this list exists to be safe to act on."""
        with self._procs((4246, "poga-1", 10), (4247, "poga-1", 20)):
            self.assertEqual(session._stranded_lane_processes(), [])

    def test_the_startup_line_names_the_verb_and_never_kills(self):
        with self._procs((4248, "poga-9", 7 * 86400)):
            lines = session._stranded_process_lines()
        self.assertTrue(any("pid 4248" in l for l in lines), lines)
        self.assertTrue(any("reap-lanes --processes" in l for l in lines), lines)

    def test_silent_when_there_is_nothing_to_say(self):
        with self._procs():
            self.assertEqual(session._stranded_process_lines(), [])

    def test_sigterm_is_tried_first_and_a_dead_pid_reports_terminated(self):
        with self._procs((4249, "poga-9", 7 * 86400)), \
             mock.patch.object(session.os, "kill") as k, \
             mock.patch.object(session, "_pid_alive", return_value=False):
            lines = session._reap_lane_processes()
        k.assert_called_once_with(4249, session.signal.SIGTERM)
        self.assertTrue(any("terminated pid 4249" in l for l in lines), lines)

    def test_a_vanished_pid_reports_already_gone(self):
        with self._procs((4250, "poga-9", 7 * 86400)), \
             mock.patch.object(session.os, "kill", side_effect=ProcessLookupError):
            lines = session._reap_lane_processes()
        self.assertTrue(any("already gone" in l for l in lines), lines)

    def test_a_permission_error_reports_rather_than_raising(self):
        with self._procs((4251, "poga-9", 7 * 86400)), \
             mock.patch.object(session.os, "kill", side_effect=PermissionError):
            lines = session._reap_lane_processes()
        self.assertTrue(any("permission denied" in l for l in lines), lines)

    def test_a_process_that_ignores_sigterm_is_escalated_and_the_report_says_so(self):
        """The defect this function shipped with for twenty minutes: it printed
        `terminated` because `os.kill` did not raise — which proves the signal was
        DELIVERED, not that the process died. Caught by exercising it against a real
        four-day-old stranded process, which was still running afterwards."""
        # Driven by what was SENT, not by a fixed call sequence — the poll loop runs a
        # timing-dependent number of times, so a scripted iterator tests the clock.
        state = {"sigkilled": False}

        def fake_kill(_pid, sig):
            if sig == session.signal.SIGKILL:
                state["sigkilled"] = True

        with self._procs((4252, "poga-9", 7 * 86400)), \
             mock.patch.object(session.os, "kill", side_effect=fake_kill) as k, \
             mock.patch.object(session, "_pid_alive",
                               side_effect=lambda _p: not state["sigkilled"]), \
             mock.patch.object(session, "PROC_SIGNAL_GRACE_SEC", 0.01):
            lines = session._reap_lane_processes()
        self.assertEqual([c.args[1] for c in k.call_args_list],
                         [session.signal.SIGTERM, session.signal.SIGKILL])
        self.assertTrue(any("killed pid 4252" in l for l in lines), lines)
        self.assertFalse(any("terminated pid 4252" in l for l in lines), lines)

    def test_a_process_that_survives_everything_is_never_reported_as_terminated(self):
        with self._procs((4253, "poga-9", 7 * 86400)), \
             mock.patch.object(session.os, "kill"), \
             mock.patch.object(session, "_pid_alive", return_value=True), \
             mock.patch.object(session, "PROC_SIGNAL_GRACE_SEC", 0.01):
            lines = session._reap_lane_processes()
        self.assertTrue(any("SURVIVED" in l for l in lines), lines)
        self.assertFalse(any("terminated" in l or "killed" in l for l in lines), lines)


@unittest.skipUnless(GIT, "git not available")
class ForeignRepoLaneProcessTest(LitterBase):
    """WI-0135. `ps` is machine-wide and `--worktree poga-2` names a lane, not a repo — so
    joining the global scan against THIS repo's pool classified every other member's live
    session as `lane-gone` here, and `reap-lanes --processes` SIGKILLed it.

    This is not a hypothetical that a test invents. On 2026-08-15 the federation's own
    session 145 ran the verb in another member's repo and killed itself; because the kill
    landed mid-tool-call, its transcript kept a `tool_use` with no `tool_result` and that
    Claude session became permanently unresumable. The same probe then reported the NEXT
    federation session — live, reading the report — as stranded.

    `poga-1` and `poga-2` are the most common lane names on the machine, so this is the
    normal case rather than an edge."""

    _procs = _fake_procs

    def _foreign(self, lane="poga-1"):
        """A path shaped exactly like a lane, in a pool that is not ours."""
        return f"/Users/someone/OtherMember/.claude/worktrees/{lane}"

    def test_another_repos_lane_process_is_not_stranded_here(self):
        # `poga-1` DOES exist in our pool, and this process is 9 days older than it — so
        # the `predates` proof would fire on the name alone. It must not: the process is
        # working in a different repo's tree and is none of our business.
        with self._procs((7001, "poga-1", 9 * 86400, self._foreign())):
            proven, unprovable = session._classify_lane_processes()
        self.assertEqual(proven, [],
                         "another repo's live session was classified stranded — "
                         "`reap-lanes --processes` would SIGKILL it")
        self.assertEqual(unprovable, [],
                         "a foreign repo's lane is not our subject; reporting it puts "
                         "every member's lanes in every other member's banner")

    def test_another_repos_lane_process_is_never_signalled(self):
        """The end-to-end property, at the one place it actually costs work."""
        with self._procs((7002, "poga-9", 7 * 86400, self._foreign("poga-9"))), \
             mock.patch.object(session.os, "kill") as k:
            lines = session._reap_lane_processes()
        k.assert_not_called()
        self.assertEqual(lines, [])

    def test_it_stays_out_of_the_startup_banner(self):
        with self._procs((7003, "poga-9", 7 * 86400, self._foreign("poga-9"))):
            self.assertEqual(session._stranded_process_lines(), [])

    def test_our_own_lane_gone_process_is_still_proven(self):
        """The gate must not cost the proof it sits in front of. A process whose lane was
        removed out from under it keeps reporting the old cwd, so it is still attributable
        to us — pinned because 'add a repo check' is exactly the change that would
        accidentally require the directory to exist."""
        with self._procs((7004, "poga-9", 7 * 86400)):
            proven, _ = session._classify_lane_processes()
        self.assertEqual([(p["pid"], p["reason"]) for p in proven], [(7004, "lane-gone")])

    def test_an_unreadable_cwd_is_unprovable_and_outranks_lane_gone(self):
        """`poga-9` does not exist here, so the old code would call this `lane-gone` and
        kill it. Not knowing whose process it is has to beat every downstream proof."""
        with self._procs((7005, "poga-9", 7 * 86400, None)):
            proven, unprovable = session._classify_lane_processes()
        self.assertEqual(proven, [])
        self.assertEqual([(p["pid"], p.get("unprovable_cause")) for p in unprovable],
                         [(7005, "repo-unknown")])
        with self._procs((7005, "poga-9", 7 * 86400, None)), \
             mock.patch.object(session.os, "kill") as k:
            session._reap_lane_processes()
        k.assert_not_called()

    def test_the_two_unprovable_causes_get_different_sentences(self):
        """`declare-what-a-check-assumes`: "no birth time for a lane I own" and "I cannot
        tell whose process this is" are different ignorances, and the second is the more
        total one. Collapsing them would report a wholly unchecked process as if only the
        recycled-name half had been skipped."""
        with self._procs((7006, "poga-9", 7 * 86400, None)):
            no_repo = session._stranded_process_lines()
        self.assertTrue(any("NOT CHECKED at all" in l for l in no_repo), no_repo)
        self.assertTrue(any("belong to this repo" in l for l in no_repo), no_repo)
        with self._procs((7007, "poga-1", 9 * 86400)), \
             mock.patch.object(session, "_lane_birth", return_value=(None, "unavailable")):
            no_birth = session._stranded_process_lines()
        self.assertTrue(any("recycled-name case" in l for l in no_birth), no_birth)
        self.assertFalse(any("NOT CHECKED at all" in l for l in no_birth), no_birth)

    def test_a_cwd_deeper_inside_our_lane_still_counts_as_ours(self):
        root = session._lanes_root()
        with self._procs((7008, "poga-9", 7 * 86400, str(root / "poga-9" / "tests"))):
            proven, _ = session._classify_lane_processes()
        self.assertEqual([(p["pid"], p["reason"]) for p in proven], [(7008, "lane-gone")])

    def test_our_pool_but_the_wrong_lane_is_ambiguous_not_proven(self):
        """argv says one lane, cwd says another of ours. Real enough (a recycled name, a
        hand-moved cwd) and not something to guess at with a kill."""
        root = session._lanes_root()
        with self._procs((7009, "poga-9", 7 * 86400, str(root / "poga-1"))):
            proven, unprovable = session._classify_lane_processes()
        self.assertEqual(proven, [])
        self.assertEqual([p["pid"] for p in unprovable], [7009])

    def test_a_lane_named_as_a_prefix_of_ours_is_not_ours(self):
        """String-prefix containment done wrong makes `/…/worktrees/poga-1-old` look like
        it is inside `/…/worktrees/poga-1`. `_path_within` compares path COMPONENTS."""
        root = session._lanes_root()
        self.assertFalse(session._path_within(str(root) + "-elsewhere/poga-1", root))
        self.assertFalse(session._path_within(root / "poga-1-old", root / "poga-1"))
        self.assertTrue(session._path_within(root / "poga-1", root / "poga-1"))


@unittest.skipIf(session._under_gate(),
                 "reads the real process table — not under the land gate (ADR-0119 D4)")
class ProcessCwdProbeTest(unittest.TestCase):
    """`_process_cwd` against real processes. The classifier above is pure logic over
    fabricated rows, which is what makes it exercisable — but the rows are only as good as
    this, and the load-bearing claim (a deleted directory still answers) is a property of
    the OS, not of our code. Reasoning about it would be exactly the mistake.

    SKIPPED UNDER THE GATE, and skipped rather than adapted. This class is the one place
    that is SUPPOSED to shell out to `lsof` — its whole subject is what the OS does — so
    the guard would make every assertion here false. A land has no business asking, and a
    test whose subject is the operator's machine is a test a land should not run
    (WI-0271 item 3: *"probably skipped under the gate rather than run"*). It still runs
    everywhere else, which is where its evidence was ever worth anything."""

    def test_it_reads_a_live_process_cwd(self):
        d = pathlib.Path(tempfile.mkdtemp()).resolve()
        p = subprocess.Popen(["sleep", "30"], cwd=str(d))
        try:
            time.sleep(0.4)
            got = session._process_cwd(p.pid)
            self.assertIsNotNone(got, "no cwd for a live process — the repo gate would "
                                      "read every lane process as unprovable")
            self.assertEqual(pathlib.Path(got).resolve(), d)
        finally:
            p.kill(); p.wait()
            shutil.rmtree(d, ignore_errors=True)

    def test_a_deleted_working_directory_still_answers(self):
        """THE property `lane-gone` rests on: the stranded case is precisely the one where
        the directory is gone, so if cwd stopped answering there, adding the repo gate
        would have silently retired the proof it was meant to protect."""
        d = pathlib.Path(tempfile.mkdtemp()).resolve() / "lane"
        d.mkdir()
        p = subprocess.Popen(["sleep", "30"], cwd=str(d))
        try:
            time.sleep(0.4)
            shutil.rmtree(d)
            time.sleep(0.2)
            got = session._process_cwd(p.pid)
            self.assertIsNotNone(got, "a deleted cwd stopped answering — `lane-gone` can "
                                      "no longer be proven and stranded processes will "
                                      "accumulate unreapable")
            self.assertEqual(pathlib.Path(got).resolve(), d)
        finally:
            p.kill(); p.wait()

    def test_a_dead_pid_is_none_rather_than_a_raise(self):
        p = subprocess.Popen(["sleep", "0.01"]); p.wait()
        time.sleep(0.2)
        self.assertIsNone(session._process_cwd(p.pid))


if __name__ == "__main__":
    unittest.main()
