"""WI-0032 — dispatch: fan a work-item range out to peer lanes.

The load-bearing properties, and why each is pinned here rather than trusted:

  1. RESOLUTION is against the session's PINNED snapshot, and an ordinal the snapshot
     doesn't carry is NAMED, never dropped — "do 1-10" must not quietly become "do the
     eight I could find" (`declare-what-a-check-assumes`).
  2. ORDERING holds a dependent whose blocker this dispatch will not itself spawn —
     including a blocker skipped because a live lane already claimed it, which is the
     case a naive in-range check gets wrong.
  3. THE SPAWN RIGHT IS DRAWN, not computed. Two lanes landing at the same instant must
     not both spawn the same item (`numbers-are-drawn-never-picked`).
  4. NO MASTER: the wave trigger lives in the LANDING session, keyed off the environment
     the spawning tab set — so a hand-opened lane never advances someone else's queue,
     and a dispatched one needs no supervisor.
  5. CAP and BUDGET both bind, and an unlisted spawn surface is REFUSED rather than
     tried (the phantom-session scar tissue).

Terminal spawning is mocked throughout — these tests assert on the command line
dispatch would run and on the state it records, which is what actually has to be right.
"""

import argparse
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (neutralize_coord_journal,  # noqa: E402
                           neutralize_dispatch_env)

GIT = shutil.which("git")


def _wi_text(wid, title, status="open", blocked="", section="next", acceptance=True):
    """A fixture work item. Carries ACCEPTANCE criteria by DEFAULT since WI-0288 R3 (4):
    dispatch now refuses an item that has none, so a fixture item without them is not a
    dispatchable item and every ordering/cap/CLI assertion built on one would be
    asserting against a wave that never runs. `acceptance=False` is for the readiness
    tests themselves, which are the only ones whose subject is the absence."""
    body = "ACCEPTANCE: the fixture's item is dispatchable.\n" if acceptance else ""
    return (f"# {wid}: {title}\n\n- status: {status}\n- section: {section}\n"
            f"- blocked-by: {blocked}\n- group: \n- source: \n\n{body}")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class DispatchBase(unittest.TestCase):
    """A real repo with a real lane, so the coordination store is the real shared one."""

    ITEMS = [
        ("WI-0001", "Alpha", "open", ""),
        ("WI-0002", "Beta", "open", ""),
        ("WI-0003", "Gamma needs Beta", "open", "WI-0002"),
        ("WI-0004", "Delta already shipped", "done", ""),
        ("WI-0005", "Epsilon needs an outsider", "open", "WI-0009"),
        ("WI-0009", "Outsider", "open", ""),
    ]

    def setUp(self):
        # Fictional lanes (worktree-poga-9 and friends) must not share the runner's
        # journal — the claimed/held assertions here are exactly what inverts (WI-0126).
        # ...nor the runner's environment. This module had no environment guard at all,
        # which is how `TerminalTitleTest` came to assert that the OSC-0 escape IS written
        # while `_set_terminal_title` suppresses it under an inherited `NO_COLOR` (WI-0250).
        # First, before the journal patch and before the fixture builds anything (WI-0275).
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        d = self.main / "work-items"
        d.mkdir()
        for wid, title, status, blocked in self.ITEMS:
            (d / f"{wid}-x.md").write_text(_wi_text(wid, title, status, blocked),
                                           encoding="utf-8")
        (self.main / "poga").write_text("#!/bin/sh\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "CFG", "DISPATCH_BRIEF_GRACE_SECONDS",
                       "DISPATCH_BRIEF_POLL_SECONDS", "DISPATCH_REBRIEF_CONFIRM_SECONDS")}
        self._env = dict(os.environ)
        # WI-0248 put a 45s brief-confirmation grace INSIDE `_dispatch_advance`, so every
        # test in this file that advances a wave without a receipt on disk would pay it in
        # full — the module went from seconds to tens of minutes, and a suite nobody waits
        # for is a suite nobody runs. Zeroed here rather than mocked per test, because
        # `_dispatch_confirm_briefs` still READS once at grace 0: the states asserted below
        # are the real measured ones, and only the waiting is gone.
        session.DISPATCH_BRIEF_GRACE_SECONDS = 0
        session.DISPATCH_BRIEF_POLL_SECONDS = 0.01
        session.DISPATCH_REBRIEF_CONFIRM_SECONDS = 0
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "testsession"
        # WI-0249: the land gate runs this suite from INSIDE a dispatched lane, so the
        # dispatch handles are live while the tests run. Use the shared helper rather than
        # popping two names by hand — it covers POGA_LANE_CLOSE too, and the hand-rolled
        # version silently stops covering whatever is added to it next.
        neutralize_dispatch_env(self)

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pin_snapshot(self, mapping, snapshot_id="S-test01"):
        sd = session.ROOT / ".session-state"
        sd.mkdir(exist_ok=True)
        import json
        (sd / "wi-snapshot-testsession.json").write_text(
            json.dumps({"snapshot_id": snapshot_id, "mapping": mapping}), encoding="utf-8")


class LaneHeldResolutionTest(DispatchBase):
    """WI-0239. `_dispatch_resolve` keyed off `_wi_parse()` — this checkout's
    `work-items/` and nothing else — so an id minted on a lane that has not landed
    reported as unresolvable, identical to a number nobody ever drew.

    Unresolvable is still the right DISPOSITION: this tree cannot read the body, so it
    cannot brief a lane on it. What was wrong is that the operator was told to fix a range
    that was correct, when the actual remedy is to land the lane."""

    def _mint_on_the_lane(self, wid="WI-0300"):
        (self.lane / "work-items" / f"{wid}-minted-in-the-lane.md").write_text(
            _wi_text(wid, "Minted in the lane"), encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"add {wid} in the lane")

    def test_a_lane_held_id_is_unresolvable_but_named_as_lane_held(self):
        self._mint_on_the_lane()
        _sid, mapping = session._dispatch_snapshot()
        ids, bad, why = session._dispatch_resolve(["WI-0300"], mapping or {})
        self.assertEqual(ids, [], "this tree cannot read the body — still unresolvable")
        self.assertEqual(bad, ["WI-0300"])
        self.assertIn("WI-0300", why)
        self.assertIn("worktree-poga-1", why["WI-0300"], "name the ref that holds it")
        self.assertIn("UNLANDED LANE", why["WI-0300"])
        self.assertIn("session.py merge", why["WI-0300"], "a verb, not a diagnosis")

    def test_the_plan_prints_the_reason_under_the_unresolved_line(self):
        """The plan is the ONE approval surface — a reason the operator never sees is
        not a fix."""
        self._mint_on_the_lane()
        rec = {"dispatch_id": "D-x", "snapshot_id": "S-1", "cap": 4, "budget": 4,
               "surface": "tmux"}
        _sid, mapping = session._dispatch_snapshot()
        _ids, bad, why = session._dispatch_resolve(["WI-0300"], mapping or {})
        out = "\n".join(session._dispatch_render_plan(
            rec, [], {}, {}, bad, [], why))
        self.assertIn("UNRESOLVED", out)
        self.assertIn("worktree-poga-1", out)

    def test_a_genuinely_unknown_id_still_reads_as_unknown(self):
        """The other direction. If every unresolvable token grew a lane story, the
        message would be useless — an id nobody ever drew gets no extra line."""
        _sid, mapping = session._dispatch_snapshot()
        rec = {"dispatch_id": "D-x", "snapshot_id": "S-1", "cap": 4, "budget": 4,
               "surface": "tmux"}
        _ids, bad, why = session._dispatch_resolve(["WI-4242"], mapping or {})
        out = "\n".join(session._dispatch_render_plan(rec, [], {}, {}, bad, [], why))
        self.assertIn("WI-4242", out)
        self.assertNotIn("UNLANDED LANE", out)

    def test_an_unprobeable_branch_list_says_unknown_rather_than_no_such_item(self):
        """`declare-what-a-check-assumes`: "couldn't tell" is its own answer here too."""
        self._mint_on_the_lane()
        _sid, mapping = session._dispatch_snapshot()
        with mock.patch.object(session, "_wi_branch_holders", return_value=None):
            _ids, bad, why = session._dispatch_resolve(["WI-0300"], mapping or {})
        self.assertEqual(bad, ["WI-0300"])
        self.assertIn("UNKNOWN", why["WI-0300"])


class RangeParsingTest(DispatchBase):
    def test_hyphen_range_expands(self):
        self.assertEqual(session._dispatch_parse_range("1-4"), ["1", "2", "3", "4"])

    def test_comma_and_mixed(self):
        self.assertEqual(session._dispatch_parse_range("1-3,7"), ["1", "2", "3", "7"])

    def test_reversed_range_is_read_not_rejected(self):
        self.assertEqual(session._dispatch_parse_range("4-2"), ["2", "3", "4"])

    def test_duplicates_collapse_preserving_first_position(self):
        self.assertEqual(session._dispatch_parse_range("3,1-3"), ["3", "1", "2"])

    def test_explicit_ids_pass_through_uppercased(self):
        self.assertEqual(session._dispatch_parse_range("wi-0007,2"), ["WI-0007", "2"])


class ResolutionTest(DispatchBase):
    def test_ordinals_resolve_through_the_pinned_snapshot(self):
        self._pin_snapshot({"1": "WI-0001", "2": "WI-0002"})
        _sid, mapping = session._dispatch_snapshot()
        ids, bad, why = session._dispatch_resolve(["1", "2"], mapping)
        self.assertEqual(ids, ["WI-0001", "WI-0002"])
        self.assertEqual(bad, [])
        self.assertEqual(why, {})

    def test_an_unmapped_ordinal_is_named_not_dropped(self):
        """The whole point: 'do 1-10' must never silently become 'do 8 of them'."""
        self._pin_snapshot({"1": "WI-0001"})
        _sid, mapping = session._dispatch_snapshot()
        ids, bad, why = session._dispatch_resolve(["1", "7"], mapping)
        self.assertEqual(ids, ["WI-0001"])
        self.assertEqual(bad, ["7"])
        self.assertEqual(why, {}, "an unmapped ORDINAL is a fact about the pinned "
                                  "snapshot, not about the store — nothing to probe")

    def test_an_id_not_in_the_store_is_unresolved(self):
        _sid, mapping = session._dispatch_snapshot()
        ids, bad, why = session._dispatch_resolve(["WI-4242"], mapping or {})
        self.assertEqual(ids, [])
        self.assertEqual(bad, ["WI-4242"])
        self.assertEqual(why, {}, "no ref carries it and nothing holds the number — the "
                                  "caller's own plain wording is the right answer")

    def test_no_pinned_snapshot_is_distinct_from_an_empty_one(self):
        sid, mapping = session._dispatch_snapshot()
        self.assertIsNone(sid)
        self.assertEqual(mapping, {})
        self._pin_snapshot({"1": "WI-0001"})
        sid2, mapping2 = session._dispatch_snapshot()
        self.assertEqual(sid2, "S-test01")
        self.assertTrue(mapping2)


class OrderingTest(DispatchBase):
    def test_an_in_range_blocker_orders_before_its_dependent(self):
        ordered, held, skipped = session._dispatch_order(["WI-0003", "WI-0002"])
        self.assertEqual(ordered.index("WI-0002"), 0)
        self.assertEqual(ordered.index("WI-0003"), 1)
        self.assertEqual(held, {})

    def test_an_out_of_range_live_blocker_holds_the_dependent(self):
        ordered, held, _skipped = session._dispatch_order(["WI-0005"])
        self.assertEqual(ordered, [])
        self.assertEqual(held, {"WI-0005": ["WI-0009"]})

    def test_a_terminal_blocker_does_not_block(self):
        d = session.ROOT / "work-items"
        (d / "WI-0006-x.md").write_text(
            _wi_text("WI-0006", "Needs the shipped one", blocked="WI-0004"),
            encoding="utf-8")
        ordered, held, _ = session._dispatch_order(["WI-0006"])
        self.assertEqual(ordered, ["WI-0006"])
        self.assertEqual(held, {})

    def test_a_terminal_item_is_skipped_by_name(self):
        ordered, _held, skipped = session._dispatch_order(["WI-0004"])
        self.assertEqual(ordered, [])
        self.assertIn("already done", skipped["WI-0004"])

    def test_an_item_claimed_by_a_live_lane_is_skipped(self):
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-9", 3600)
        ordered, _held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(ordered, [])
        self.assertIn("claimed by", skipped["WI-0001"])

    def test_a_dependent_of_a_claimed_blocker_is_HELD_not_released(self):
        """The bug a naive in-range check has: WI-0002 is in the range but skipped
        because another lane owns it, so WI-0003 must NOT be spawned ahead of it."""
        session._coord_try_acquire("claims", "WI-0002", "worktree-poga-9", 3600)
        ordered, held, skipped = session._dispatch_order(["WI-0002", "WI-0003"])
        self.assertNotIn("WI-0003", ordered)
        self.assertEqual(held.get("WI-0003"), ["WI-0002"])
        self.assertIn("WI-0002", skipped)

    def test_a_cycle_is_held_rather_than_broken_arbitrarily(self):
        d = session.ROOT / "work-items"
        (d / "WI-0007-x.md").write_text(_wi_text("WI-0007", "A", blocked="WI-0008"),
                                        encoding="utf-8")
        (d / "WI-0008-x.md").write_text(_wi_text("WI-0008", "B", blocked="WI-0007"),
                                        encoding="utf-8")
        ordered, held, _ = session._dispatch_order(["WI-0007", "WI-0008"])
        self.assertEqual(ordered, [])
        self.assertEqual(set(held), {"WI-0007", "WI-0008"})


class SpawnSlotTest(DispatchBase):
    """Property 3 — the spawn right is DRAWN. This is the concurrency guarantee."""

    def _rec(self, queue):
        return {"dispatch_id": "D-test01", "snapshot_id": "S-test01", "cap": 5,
                "budget": 5, "spawned": 0, "queue": list(queue), "log": [],
                "surface": "Terminal", "created_by": "worktree-poga-1"}

    def test_two_landers_racing_for_one_item_produce_exactly_one_spawn(self):
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            ok_a, _ = session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1")
            ok_b, detail_b = session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-2")
        self.assertTrue(ok_a)
        self.assertFalse(ok_b)
        self.assertIn("already drawn", detail_b)

    def test_the_slot_is_written_before_the_tab_opens(self):
        """Provenance-before-spawn: nothing dispatch-spawned can be an anonymous
        phantom, so the record must already exist when the tab is asked for."""
        seen = {}

        def _capture(cmd, surface, name=""):
            recs = session._coord_list(session.DISPATCH_SPAWN_KIND)
            seen["slots"] = dict(recs)
            return True, ""

        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab", side_effect=_capture):
            session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1")
        self.assertIn("D-test01__WI-0001", seen["slots"])
        slot = seen["slots"]["D-test01__WI-0001"]
        self.assertEqual(slot["item"], "WI-0001")
        self.assertEqual(slot["dispatch_id"], "D-test01")
        self.assertEqual(slot["snapshot_id"], "S-test01")

    def test_a_failed_tab_releases_the_slot_so_the_item_is_not_stranded(self):
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab",
                               return_value=(False, "no such app")):
            ok, detail = session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1")
        self.assertFalse(ok)
        self.assertIn("FAILED", detail)
        self.assertNotIn("D-test01__WI-0001",
                         session._coord_list(session.DISPATCH_SPAWN_KIND))

    def test_the_spawn_command_uses_an_absolute_poga_and_carries_the_dispatch_env(self):
        cmd = session._dispatch_spawn_command(self.main, "D-test01", "WI-0001")
        self.assertIn(str(self.main / "poga"), cmd)
        self.assertIn("POGA_DISPATCH=D-test01", cmd)
        self.assertIn("POGA_DISPATCH_ITEM=WI-0001", cmd)

    def test_the_spawn_command_is_a_parseable_shell_line(self):
        cmd = session._dispatch_spawn_command(self.main, "D-x", "WI-0001",
                                              runtime="codex")
        subprocess.run(["bash", "-n", "-c", cmd], check=True, capture_output=True)


class CrossDispatchItemGuardTest(DispatchBase):
    """WI-0259 — the spawn right is drawn MACHINE-WIDE, not just within one dispatch.

    The per-dispatch slot (`SpawnSlotTest`) is keyed `{dispatch_id}__{wid}`, so it gives
    no cross-dispatch protection at all. And the claim record `_dispatch_order` reads is
    written by the CHILD at its own startup, tens of seconds to minutes after the spawn —
    so two overlapping `poga dispatch` runs inside that window each saw no claim, each
    printed `will spawn`, and each opened a lane for the same item.

    These pin both halves of the fix and, just as importantly, the thing the fix must NOT
    do: block a legitimate re-dispatch once the item is free again.
    """

    def _rec(self, did, queue=("WI-0001",)):
        return {"dispatch_id": did, "snapshot_id": "S-test01", "cap": 5,
                "budget": 5, "spawned": 0, "queue": list(queue), "log": [],
                "surface": "Terminal", "created_by": "worktree-poga-1"}

    def test_two_overlapping_dispatches_open_exactly_one_lane_for_an_item(self):
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            ok_a, _ = session._dispatch_try_spawn(
                self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
            ok_b, detail_b = session._dispatch_try_spawn(
                self._rec("D-bbb"), "WI-0001", "worktree-poga-2")
        self.assertTrue(ok_a)
        self.assertFalse(ok_b, "the second dispatch opened a duplicate lane")
        self.assertIn("D-aaa", detail_b)
        self.assertIn("already being spawned", detail_b)

    def test_the_refused_dispatch_does_not_leave_its_own_slot_behind(self):
        """A per-dispatch slot held over a lane that never opened is a silent strand —
        the same reason the tab-failure path releases."""
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
            session._dispatch_try_spawn(self._rec("D-bbb"), "WI-0001", "worktree-poga-2")
        slots = session._coord_list(session.DISPATCH_SPAWN_KIND)
        self.assertIn("D-aaa__WI-0001", slots)
        self.assertNotIn("D-bbb__WI-0001", slots)

    def test_the_plan_surface_says_being_spawned_rather_than_will_spawn(self):
        """The defect's other half: the approval surface asserted something it could not
        honour. An item another dispatch is mid-spawn is skipped BY NAME."""
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
        ordered, _held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(ordered, [])
        self.assertIn("being spawned by dispatch D-aaa", skipped["WI-0001"])

    def test_the_guard_is_released_the_moment_the_child_self_claims(self):
        """The guard bridges spawn -> self-claim and nothing more. Once the claim exists
        it IS the exclusion, so holding on would only block a later re-dispatch."""
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
        self.assertIn("WI-0001", session._coord_list(session.DISPATCH_ITEM_KIND))
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-9", 3600)
        session._dispatch_item_sweep()
        self.assertNotIn("WI-0001", session._coord_list(session.DISPATCH_ITEM_KIND))

    def test_an_item_freed_after_teardown_can_be_re_dispatched(self):
        """The regression this fix had to avoid: a lane torn down mid-flight must be
        re-dispatchable, not blocked behind the guard.

        The sequence is the real one, INCLUDING the sweep. The guard is dropped while the
        child's claim is live — any dispatch path performs that — and by the time teardown
        releases the claim there is nothing left to block the next dispatch. Written this
        way rather than claim-then-immediately-release because that compressed version
        asserts a property the sweep does not have: it is driven by the dispatch paths, not
        by the claim, so a claim that comes and goes between two of them is simply never
        seen. That case is bounded by the TTL instead, and is pinned separately below."""
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
            session._coord_try_acquire("claims", "WI-0001", "worktree-poga-9", 3600)
            session._dispatch_order(["WI-0002"])          # any dispatch path sweeps
            session._coord_release("claims", "WI-0001", "worktree-poga-9", force=True)
            ordered, _held, skipped = session._dispatch_order(["WI-0001"])
            ok, detail = session._dispatch_try_spawn(
                self._rec("D-ccc"), "WI-0001", "worktree-poga-3")
        self.assertEqual(ordered, ["WI-0001"], skipped)
        self.assertTrue(ok, detail)

    def test_a_failed_tab_releases_the_item_guard_too(self):
        """Otherwise a spawn that never opened would block every other dispatch from the
        item for the full TTL — a strand moved rather than removed."""
        with mock.patch.object(session, "_dispatch_open_tab",
                               return_value=(False, "no such app")):
            ok, _ = session._dispatch_try_spawn(
                self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
        self.assertFalse(ok)
        self.assertNotIn("WI-0001", session._coord_list(session.DISPATCH_ITEM_KIND))
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            ok2, detail2 = session._dispatch_try_spawn(
                self._rec("D-bbb"), "WI-0001", "worktree-poga-2")
        self.assertTrue(ok2, detail2)

    def test_an_expired_guard_does_not_block_anything(self):
        """The TTL is the actual bound on the guard, so pin it as such. It covers every
        case the sweep misses — a spawn that died before the child ever claimed, and a
        claim that came and went between two dispatch paths — with no operator verb."""
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(self._rec("D-aaa"), "WI-0001", "worktree-poga-1")
        d = session._coord_dir(session.DISPATCH_ITEM_KIND)
        path = d / "WI-0001.json"
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["expires_at"] = time.time() - 1
        path.write_text(json.dumps(rec), encoding="utf-8")
        ordered, _held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(ordered, ["WI-0001"], skipped)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            ok, detail = session._dispatch_try_spawn(
                self._rec("D-bbb"), "WI-0001", "worktree-poga-2")
        self.assertTrue(ok, detail)

    def test_the_ttl_is_a_bridge_not_the_provenance_slots_day(self):
        """Pinned deliberately: giving the item guard the per-dispatch slot's 24h is
        exactly the re-dispatch block WI-0259 warned about."""
        self.assertLess(session.DISPATCH_ITEM_TTL_SECONDS,
                        session.DISPATCH_SPAWN_TTL_SECONDS)
        self.assertGreaterEqual(session.DISPATCH_ITEM_TTL_SECONDS, 5 * 60)


class SpawnFailsFastTest(DispatchBase):
    """Found live, session ~105: with macOS Automation permission ungranted, `osascript`
    does not error — it BLOCKS on the Apple Event until the ~120s system timeout. Every
    mocked test above was green while a real dispatch of five items would have sat
    silently for ten minutes. A hung launcher IS the silent strand."""

    def test_a_blocked_apple_event_is_bounded_and_names_the_real_fix(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("osascript", 20)):
            ok, detail = session._dispatch_open_tab("echo hi", "Terminal")
        self.assertFalse(ok)
        self.assertIn("blocked", detail)
        self.assertIn("Automation", detail)
        self.assertIn("poga", detail)          # names the by-hand fallback

    def test_the_call_is_actually_given_a_timeout(self):
        """Pins the bound itself — the defect was an unbounded call, so a test that only
        checked the TimeoutExpired branch would pass against the broken version."""
        with mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            session._dispatch_open_tab("echo hi", "Terminal")
        self.assertEqual(run.call_args.kwargs.get("timeout"),
                         session.DISPATCH_SPAWN_TIMEOUT_SECONDS)

    def test_the_minus_1712_error_text_gets_the_permission_hint_appended(self):
        err = "Terminal got an error: AppleEvent timed out. (-1712)"
        with mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", err)
            ok, detail = session._dispatch_open_tab("echo hi", "Terminal")
        self.assertFalse(ok)
        self.assertIn("-1712", detail)
        self.assertIn("Automation", detail)

    def test_a_missing_osascript_is_reported_not_raised(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=FileNotFoundError("osascript")):
            ok, detail = session._dispatch_open_tab("echo hi", "Terminal")
        self.assertFalse(ok)
        self.assertIn("osascript", detail)

    def test_a_wave_stops_on_the_first_failed_spawn_rather_than_grinding_through(self):
        rec = {"dispatch_id": "D-fail01", "snapshot_id": "S-test01", "cap": 5,
               "budget": 5, "spawned": 0, "queue": ["WI-0001", "WI-0002", "WI-0009"],
               "log": [], "surface": "Terminal", "created_by": "worktree-poga-1"}
        with mock.patch.object(session, "_dispatch_open_tab",
                               return_value=(False, "blocked")) as tab, \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=5)
        self.assertEqual(tab.call_count, 1, "a broken surface must not be retried 5x")
        self.assertEqual(rec["spawned"], 0)
        self.assertIn("WI-0001", rec["queue"])   # re-queued, not lost
        self.assertTrue(any("FAILED" in x for x in lines))


class TerminalTitleTest(DispatchBase):
    """WI-0031 Part A. With dispatch opening five tabs at once, 'which tab is which' stops
    being cosmetic. The label rule is load-bearing: lane name and topic, NEVER a session
    number — numbers are assigned at LAND (ADR-0069), so two live lanes would transiently
    show the same one."""

    def test_the_escape_is_written_only_to_a_real_terminal(self):
        buf = io.StringIO()
        buf.isatty = lambda: True
        with mock.patch.object(session.sys, "stderr", buf):
            session._set_terminal_title("poga-1 · something")
        self.assertIn("\033]0;poga-1 · something\007", buf.getvalue())

    def test_a_captured_stderr_gets_no_escape(self):
        """Piped or captured, the escape is literal garbage in someone's log."""
        buf = io.StringIO()
        buf.isatty = lambda: False
        with mock.patch.object(session.sys, "stderr", buf):
            session._set_terminal_title("poga-1")
        self.assertEqual(buf.getvalue(), "")

    def test_no_color_suppresses_it(self):
        buf = io.StringIO()
        buf.isatty = lambda: True
        os.environ["NO_COLOR"] = "1"
        with mock.patch.object(session.sys, "stderr", buf):
            session._set_terminal_title("poga-1")
        self.assertEqual(buf.getvalue(), "")

    def test_it_never_raises(self):
        with mock.patch.object(session.sys, "stderr", None):
            session._set_terminal_title("poga-1")     # must not raise

    def test_poga_titles_the_tab_with_the_project_label_not_a_number(self):
        # The real script, not the fixture's stub.
        src = (pathlib.Path(session.__file__).parent / "poga").read_text(encoding="utf-8")
        self.assertIn('set_terminal_title "$(lane_label "$lane")"', src)
        self.assertIn("lane_label() {", src)
        self.assertIn("[ -t 2 ] || return 0", src)


class LaneLabelTest(unittest.TestCase):
    """Session 127, operator ruled that a lane is labelled by its repo (`federation lane 1`,
    `example-app lane 3`), not by its allocator name (`poga-1`). Lane numbers are allocated PER REPO, so every project's first
    lane is 1 and a wall of tabs reads identically. Only the LABEL changes — the lane's
    own name stays an allocator identity, because the worktree dir, the branch, and the
    free-slot check all key on it and names are recycled on purpose."""

    def _with_arch(self, arch_id):
        return mock.patch.dict(session.CFG, {"architect_id": arch_id}, clear=False)

    def test_the_project_leads_and_the_number_survives(self):
        with self._with_arch("federation-arch"):
            self.assertEqual(session._lane_label("worktree-poga-1"), "federation lane 1")
            self.assertEqual(session._lane_label("poga-3"), "federation lane 3")
            self.assertEqual(session._lane_label("worktree-poga-12"), "federation lane 12")

    def test_two_projects_do_not_collide_on_lane_one(self):
        """The actual complaint: five tabs, all `poga-1`, nothing to tell them apart."""
        with self._with_arch("example-app-arch"):
            a = session._lane_label("worktree-poga-1")
        with self._with_arch("orbit-arch"):
            b = session._lane_label("worktree-poga-1")
        self.assertEqual(a, "example-app lane 1")
        self.assertEqual(b, "orbit lane 1")
        self.assertNotEqual(a, b)

    def test_the_label_is_derived_from_architect_id_not_the_directory(self):
        """ADR-0006: a member's repo directory may be named for its orchestrator agent's
        codename (say `example-agent`). Labelling from the folder would print
        `example-agent lane 1` and conflate the agent with the system — the exact collapse
        the naming convention forbids."""
        with self._with_arch("example-assistant-arch"):
            self.assertEqual(session._lane_label("worktree-poga-1"),
                             "example-assistant lane 1")

    def test_a_nonnumeric_lane_still_gets_its_project(self):
        with self._with_arch("federation-arch"):
            self.assertEqual(session._lane_label("poga-x"), "federation poga-x")

    def test_it_never_raises_and_always_says_something(self):
        with self._with_arch(""):
            out = session._lane_label("worktree-poga-1")
        self.assertTrue(out.strip())           # a bare number is never the answer
        self.assertIn("lane 1", out)


class PromptTest(DispatchBase):
    def test_the_dispatched_lane_is_told_to_self_claim_and_stop_on_a_lost_race(self):
        p = session._dispatch_prompt("WI-0001", "D-test01", "S-test01")
        self.assertIn("WI-0001", p)
        self.assertIn("Alpha", p)
        self.assertIn("poga work claim WI-0001", p)
        self.assertIn("stop", p.lower())
        self.assertIn("D-test01", p)

    def test_the_dispatched_lane_is_told_to_decide_and_to_park_rather_than_block(self):
        """R3 (consultant brief 2026-09-04, ruled session ~208) / WI-0274 item (1).

        A dispatched lane has nobody to ask: it was opened by a dispatch, not a person, so
        a question holds a slot nothing will release. The prompt must carry BOTH halves —
        decide by default, and when an escalation is genuinely warranted, park it on the
        item and end rather than waiting. This asserts on the prompt because the prompt is
        the only thing that reaches a lane BEFORE it stalls; delivering it by hand
        afterwards is what this replaced."""
        p = session._dispatch_prompt("WI-0001", "D-test01", "S-test01")
        low = p.lower()
        # Decide, and leave a reviewable record rather than an interruption.
        self.assertIn("decide", low)
        self.assertIn("decisions i made without you", low)
        # Park, don't block — with the verbs to do it, on THIS item.
        self.assertIn("never block", low)
        self.assertIn("poga work status WI-0001 --status held", p)
        self.assertIn("poga work edit WI-0001", p)
        self.assertIn("session.py end", p)
        # The instruction this replaced must not come back: it is what stalled the lanes.
        self.assertNotIn("exactly as you would in a session", p)

    def test_the_dispatched_lane_is_forbidden_to_render_a_dialog_and_told_which_ones(self):
        """WI-0241. A lane is a DETACHED pane: a UI that waits for a keystroke converts a
        working lane into a silently stalled one, and every surface still reports it live.
        operator ruled it out entirely in session ~185. The prompt is the only thing that reaches
        a lane BEFORE it stalls, so it must carry the prohibition AND name the surfaces —
        a lane that does not know `SendFeedback` is the trap cannot avoid it.

        The enumeration is asserted by NAME rather than by count on purpose: the item asked
        for the surfaces to be enumerated, and a test that only counted them would stay green
        while one was quietly dropped."""
        p = session._dispatch_prompt("WI-0001", "D-test01", "S-test01")
        low = p.lower()
        self.assertIn("never put a dialog in front of operator", low)
        self.assertIn("detached", low)
        # Each of the three model-reachable surfaces, by the name the lane would call it by.
        self.assertIn("SendFeedback", p)
        self.assertIn("ProposeGoal", p)
        self.assertIn("AskUserQuestion", p)
        # ...and the switch/guard that closes each, so the prohibition is checkable, not bare.
        self.assertIn("feedbackDrafts", p)
        self.assertIn("modelProposedGoals", p)
        self.assertIn("check-question", p)
        # A dialog that appears anyway is a defect to record, never a thing to wait on.
        self.assertIn("defect", low)


class TheSpawnLineCompetesForNoPromptSlotTest(DispatchBase):
    """WI-0390 layers 1 and 3 — what the spawn line may and may not contain.

    The defect had two independent halves and both were invisible from inside Python.
    Dispatch could not express a Codex lane at all (no runtime flag anywhere, so every
    lane fell through to the registry default), and the brief it passed positionally was
    classified by `poga` as the OPERATOR'S own prompt — which suppressed the splice that
    delivers canon, principles, habits and the standard rituals. Every dispatched lane in
    the fleet ran without them, and the only notice was a stderr line the model never sees.
    """

    #: The ARGV `poga` is actually launched with. The spawn line is
    #: `cd <root> && VAR=v VAR=v <poga> [flags]; rc=$?; if …` — the pane-holding wrapper
    #: is not argv, and neither are the leading environment assignments (those are the
    #: shell's, consumed before the command is ever formed).
    def _argv(self, cmd):
        import re
        import shlex
        toks = shlex.split(cmd.split("; rc=$?", 1)[0].split("&&", 1)[1])
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks.pop(0)
        return toks

    def test_the_spawn_line_passes_no_bare_positional_prompt(self):
        """THE LOAD-BEARING ASSERTION, and it is keyed on the argv that is actually BUILT
        rather than on a substring of it. A substring test ("the brief is not in the
        command") would pass for a line that had merely renamed the brief; what has to be
        true is stronger and structural — after the launcher's own path there is nothing a
        position-keyed classifier could read as somebody's prompt.

        `has_operator_prompt` (poga:379-395) decides by POSITION and never by authorship:
        any bare token that is not a listed value-flag's value is the operator's. Probed
        by lifting the function out of `poga` and running it on four argv shapes — `[]`
        and `[--model sonnet]` DELIVERED; a dispatch brief SUPPRESSED; and poga's OWN
        default prompt, passed positionally, SUPPRESSED too, which is the negative control
        proving the mechanism is position rather than content."""
        for runtime in (None, "codex"):
            with self.subTest(runtime=runtime):
                argv = self._argv(session._dispatch_spawn_command(
                    self.main, "D-test01", "WI-0001", runtime=runtime))
                self.assertEqual(argv[0], str(self.main / "poga"),
                                 "the launcher must be the first argv token")
                bare = [a for a in argv[1:] if not a.startswith("-")]
                self.assertEqual(bare, [], (
                    f"these tokens would be read as the operator's own prompt and would "
                    f"cost the lane its canon: {bare}"))

    def test_the_spawn_line_carries_the_requested_runtime(self):
        cmd = session._dispatch_spawn_command(self.main, "D-test01", "WI-0001",
                                              runtime="codex")
        self.assertIn("--runtime=codex", cmd)

    def test_the_runtime_is_the_JOINED_spelling_and_not_the_separated_one(self):
        """WI-0390 layer 3, found while building this and measured the same way as layer 2.

        `-r` and `--runtime` are absent from poga's `POGA_VALUE_FLAGS`, and `cmd_session`
        calls `has_operator_prompt` on the RAW argv (poga:719) — before `parse_runtime_flag`
        has produced anything. So the separated spelling leaves the runtime's NAME sitting
        as a bare token and re-breaks delivery in the very act of selecting the runtime.
        Probed: `[-r codex]` SUPPRESSED, `[--runtime codex]` SUPPRESSED, `[--runtime=codex]`
        DELIVERED. The real repair belongs to `poga` and to the sibling item's file set;
        this asserts the spelling that works against `poga` exactly as it stands today, so
        the fix cannot be "tidied" back into the shape that silently breaks it."""
        argv = self._argv(session._dispatch_spawn_command(
            self.main, "D-x", "WI-0001", runtime="codex"))
        self.assertIn("--runtime=codex", argv)
        self.assertNotIn("-r", argv)
        self.assertNotIn("--runtime", argv)

    def test_no_runtime_requested_emits_no_flag_at_all(self):
        """Absence is its own state. A default spawn line must be what it always was —
        adding `--runtime=claude-code` for everyone would make "nobody asked" and "someone
        asked for the default" indistinguishable on the record and in `ps`."""
        argv = self._argv(session._dispatch_spawn_command(self.main, "D-x", "WI-0001"))
        self.assertEqual(argv, [str(self.main / "poga")])

    def test_an_unknown_runtime_refuses_once_in_front_of_the_operator(self):
        """Not in each of N tabs nobody is attached to — that is WI-0105's litter shape."""
        self._pin_snapshot({"1": "WI-0001"})
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as e:
            session.cmd_dispatch(argparse.Namespace(
                items="WI-0001", go=False, cap=None, run=None, max_turns=None,
                subagent="", force=False, runtime="gemini"))
        self.assertEqual(e.exception.code, 2)
        out = buf.getvalue()
        self.assertIn("unknown runtime", out)
        self.assertIn("codex", out, "the refusal must print the roster it accepts")

    def test_an_alias_is_recorded_as_the_canonical_id(self):
        """`-r cx` and `-r codex` are the same request (ADR-0082 D2). The spawn line must
        never carry an operator's abbreviation — the lane's own resolution would work, but
        the RECORD would then disagree with itself about which runtime a wave ran on."""
        self._pin_snapshot({"1": "WI-0001"})
        with redirect_stdout(io.StringIO()), \
                mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session.cmd_dispatch(argparse.Namespace(
                items="WI-0001", go=True, cap=None, run=None, max_turns=None,
                subagent="", force=False, runtime="cx"))
        recs = [session._coord_read(p)
                for p in sorted((session._dispatch_dir() or self.tmp).glob("D-*.json"))]
        self.assertTrue(recs, "the dispatch record must have been written")
        self.assertEqual([r["runtime"] for r in recs if r], ["codex"])

    def test_the_wave_spawns_with_the_recorded_runtime_not_the_default(self):
        """The record and the spawn line must agree. A runtime recorded but not emitted is
        the original defect wearing a flag: the operator reads `runtime: codex` on the
        approval surface and every lane still opens on claude-code."""
        self._pin_snapshot({"1": "WI-0001"})
        with redirect_stdout(io.StringIO()), \
                mock.patch.object(session, "_dispatch_open_tab",
                                  return_value=(True, "")) as tab:
            session.cmd_dispatch(argparse.Namespace(
                items="WI-0001", go=True, cap=None, run=None, max_turns=None,
                subagent="", force=False, runtime="cx"))
        self.assertTrue(tab.called, "nothing was spawned, so nothing was asserted")
        self.assertIn("--runtime=codex", tab.call_args[0][0])

    def test_the_plan_names_the_runtime_and_its_guard_posture(self):
        """The ONE approval surface. "10 Claude lanes" and "10 Codex lanes" are materially
        different things to agree to, and only claude-code declares `native` guards
        (ADR-0082 D6) — an unguarded wave must not look identical to a guarded one."""
        self._pin_snapshot({"1": "WI-0001"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_dispatch(argparse.Namespace(
                items="WI-0001", go=False, cap=None, run=None, max_turns=None,
                subagent="", force=False, runtime="cx"))
        out = buf.getvalue()
        self.assertIn("runtime: codex", out)
        self.assertIn("undeclared", out)


class TheBriefRidesTheOrientationTest(DispatchBase):
    """WI-0390 layer 2 — the channel the brief moved to, and the lane that must not see one.

    An item that arrives unbriefed is WI-0248 all over again, so removing the brief from
    argv is only half a fix: the other half is that it still reaches the lane. It reaches
    it through the session-start orientation, which both delivery routes carry — the
    SessionStart hook's `additionalContext` on the native path, and the `--prep`
    session-context payload on every other runtime."""

    def _spawn(self, wid="WI-0001"):
        rec = {"dispatch_id": "D-test01", "snapshot_id": "S-test01", "cap": 5,
               "budget": 5, "spawned": 0, "queue": [wid], "log": [],
               "surface": "Terminal", "created_by": "worktree-poga-1"}
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(rec, wid, "worktree-poga-1")
        return rec

    def test_the_brief_reaches_the_lane_through_the_orientation(self):
        self._spawn()
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test01",
                                          "POGA_DISPATCH_ITEM": "WI-0001"}):
            block = session._dispatch_brief_block()
        # Everything the argv brief carried is still carried, by the new route.
        self.assertIn("WI-0001", block)
        self.assertIn("poga work claim WI-0001", block)
        self.assertIn("D-test01", block)
        self.assertIn("NEVER BLOCK", block)
        # ...and it says which of the two things the lane was handed outranks the other.
        self.assertIn("OUTRANKS", block)

    def test_the_title_comes_off_the_slot_not_the_lane_s_frozen_store(self):
        """A lane reads a work-item store frozen at its own base commit. The spawner knew
        the title; recording it on the slot the spawner already writes means the brief is
        not a second, differently-stale read of it."""
        self._spawn()
        slot = session._coord_read(session._dispatch_slot_path("D-test01", "WI-0001"))
        self.assertEqual(slot["title"], "Alpha")
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test01",
                                          "POGA_DISPATCH_ITEM": "WI-0001"}):
            with mock.patch.object(session, "_wi_parse",
                                   side_effect=AssertionError("must not read the store")):
                block = session._dispatch_brief_block()
        self.assertIn("Alpha", block)

    def test_an_item_with_no_slot_still_gets_told_what_to_work_on(self):
        """Degrade toward a briefed lane, never toward a silent one: a dispatch record
        that has been swept, or one predating the slot, must still produce a brief."""
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-gone",
                                          "POGA_DISPATCH_ITEM": "WI-0002"}):
            block = session._dispatch_brief_block()
        self.assertIn("WI-0002", block)
        self.assertIn("Beta", block, "it falls back to this tree's store for the title")

    # ---- THE NEGATIVE CONTROL: a hand launch must be untouched ----------------------

    def test_a_hand_launched_lane_gets_no_brief_block_at_all(self):
        """A guard that fires on correct code gets deleted. A lane operator opened himself has
        no dispatch in its environment and must get EXACTLY the orientation it got before
        this shipped — nothing appended, and nothing claiming it was dispatched."""
        for env in ({}, {"POGA_DISPATCH": "D-test01"}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=False):
                    os.environ.pop("POGA_DISPATCH_ITEM", None)
                    if not env:
                        os.environ.pop("POGA_DISPATCH", None)
                    self.assertEqual(session._dispatch_brief_block(), "")

    def test_a_hand_launched_lane_files_no_receipt_and_leaves_no_verdict(self):
        """The receipt half of the same control: nothing is written, so no dispatch record
        anywhere acquires an opinion about a lane that was never dispatched."""
        os.environ.pop("POGA_DISPATCH", None)
        os.environ.pop("POGA_DISPATCH_ITEM", None)
        with mock.patch.object(session, "atomic_write") as write:
            session._dispatch_write_receipt(session._dispatch_brief_block())
        write.assert_not_called()

    def test_the_receipt_is_written_about_the_block_the_caller_holds(self):
        """`session.py start` builds the block once and uses it twice — the receipt is
        written ABOUT it, and the same string is appended to the orientation. Passed in
        rather than re-derived so the receipt is a claim about THIS start's delivery."""
        self._spawn()
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test01",
                                          "POGA_DISPATCH_ITEM": "WI-0001"}):
            session._dispatch_write_receipt("=== YOUR DISPATCH BRIEF ===\nwork WI-0001")
            slot = session._coord_read(session._dispatch_slot_path("D-test01", "WI-0001"))
        self.assertEqual(slot["brief"], "briefed")
        self.assertIn("orientation", slot["brief_detail"])


#: The real launcher, not the one-line stub `DispatchBase` writes into its fixture repo.
REAL_POGA = pathlib.Path(__file__).resolve().parent.parent / "poga"


def _lift_bash_function(name):
    """The named shell function's source, lifted out of `poga` — or None when it is not
    there under that name any more. `poga` is a script, not a sourceable library, so
    executing it to reach one helper would run `main`."""
    import re
    src = REAL_POGA.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", src, re.M | re.S)
    return m.group(0) if m else None


@unittest.skipUnless(shutil.which("bash"), "bash required")
class TheSpawnLineSurvivesPogasOwnClassifierTest(unittest.TestCase):
    """WI-0390 — the detector proving itself against the REAL classifier, by running it.

    Everything else in this module asserts on the command line dispatch builds. That is
    necessary and it is not the claim that matters: the claim is that `poga` classifies
    that command line as OURS, so `deliver_the_context` splices the session-start payload
    into it and the lane gets its canon. Only `poga` can answer that, and asserting on a
    substring of its source would be a textual coupling to a file this lane does not own.

    So the classifier is EXECUTED. `has_operator_prompt` is lifted out and run under bash
    on the argv actually built — the same method `tests/test_preflight.py` uses on the
    preflight gate, and the same method that MEASURED this defect in the first place.

    SKIPS RATHER THAN REDS if the function is renamed or reshaped by the lane that owns
    `poga`. A red here would report a defect in this lane's work for a change that has
    nothing to do with it; a named skip says exactly what went missing."""

    #: Lifted verbatim with the function, because the classification depends on it: a bare
    #: token is a prompt UNLESS it is the value of a flag listed here.
    FLAGS_ASSIGN = "POGA_VALUE_FLAGS="

    def setUp(self):
        fn = _lift_bash_function("has_operator_prompt")
        if fn is None:
            self.skipTest("`has_operator_prompt` is no longer in poga under that name — "
                          "the classifier this asserts against has been reshaped")
        src = REAL_POGA.read_text(encoding="utf-8")
        flags = [l for l in src.splitlines() if l.startswith(self.FLAGS_ASSIGN)]
        if not flags:
            self.skipTest("`POGA_VALUE_FLAGS` is no longer assigned at poga's top level")
        self.script = flags[0] + "\n" + fn + "\n"

    def _is_operator_prompt(self, argv):
        """True when `poga` would treat this argv as carrying the OPERATOR'S own prompt —
        which is the state in which it refuses to deliver the session-start context."""
        r = subprocess.run(["bash", "-c", self.script + 'has_operator_prompt "$@"',
                            "_"] + list(argv), capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode == 0

    def _argv(self, **kw):
        import re
        import shlex
        root = pathlib.Path("/repo")
        cmd = session._dispatch_spawn_command(root, "D-test01", "WI-0001", **kw)
        toks = shlex.split(cmd.split("; rc=$?", 1)[0].split("&&", 1)[1])
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks.pop(0)
        return toks[1:]          # everything poga is handed, minus poga itself

    def test_the_spawn_line_is_classified_as_OURS_so_canon_is_delivered(self):
        """THE WHOLE ITEM, in one assertion. Before this change the brief sat here as a
        bare positional and this came back True — the lane's canon, principles, habits and
        rituals were withheld and the only notice was a stderr line the model never sees."""
        for runtime in (None, "codex", "antigravity"):
            with self.subTest(runtime=runtime):
                self.assertFalse(self._is_operator_prompt(self._argv(runtime=runtime)))

    def test_a_hand_launch_with_the_operators_own_prompt_is_still_left_untouched(self):
        """THE NEGATIVE CONTROL. A guard that fires on correct code gets deleted — and one
        that quietly starts amending prompts operator typed would be worse than the defect.
        the operator's own prompt must still be classified as his."""
        self.assertTrue(self._is_operator_prompt(["fix the thing"]))
        self.assertTrue(self._is_operator_prompt(["--model", "sonnet", "fix the thing"]))
        # ...and a value-taking flag's value is still not a prompt, so a bare
        # `poga --model sonnet` still gets its context.
        self.assertFalse(self._is_operator_prompt(["--model", "sonnet"]))

    def test_the_separated_runtime_spelling_would_re_break_delivery(self):
        """WI-0390 layer 3, pinned as the REASON the joined form is used rather than as a
        style preference. `-r`/`--runtime` are not in `POGA_VALUE_FLAGS`, so the runtime's
        NAME is left as a bare token and classified as somebody's prompt — selecting a
        runtime would suppress the canon that selecting it was for.

        This test is expected to INVERT when the lane that owns `poga` fixes it (by adding
        the flag to `POGA_VALUE_FLAGS`, or by classifying the post-parse argv). That is the
        point: it is the tripwire that says the finding was acted on. Written so the
        failure names the fix rather than looking like a regression."""
        if not self._is_operator_prompt(["-r", "codex"]):
            self.skipTest("poga now classifies `-r <id>` correctly — WI-0390's layer-3 "
                          "finding has been fixed; the joined `--runtime=` form in "
                          "`_dispatch_spawn_command` is now belt-and-braces, not required")
        self.assertTrue(self._is_operator_prompt(["--runtime", "codex"]),
                        "the two separated spellings must behave alike")
        self.assertFalse(self._is_operator_prompt(["--runtime=codex"]),
                         "the joined form is the one dispatch emits and must survive")


class TheOrientationActuallyCarriesTheBriefTest(DispatchBase):
    """WI-0390 layer 2, END TO END — `session.py start` is the thing that has to deliver.

    `_dispatch_brief_block` returning a brief is necessary and not sufficient: the defect
    this item fixes was precisely a payload that existed and was never spliced into what
    the model reads. So this drives the real `cmd_start` and asserts on the orientation it
    produces, which is the same string both delivery routes carry — the SessionStart
    hook's `additionalContext` on the native path, and the `--prep` session-context file
    on every other runtime."""

    def setUp(self):
        super().setUp()
        t = self.tmp / "start"
        t.mkdir()
        role = t / "test-arch.md"
        role.write_text("# Role\n\n**Version:** 1.0.0\n", encoding="utf-8")
        session.CFG.update({"handoff": t / "session-handoff.md", "status": t / "STATUS.md",
                            "role_doc": role, "inbox": None, "user_name": "Tester"})
        self._patches = [mock.patch.object(session, "JOURNAL_DIR", t / "journal"),
                         mock.patch.object(session, "ARCHIVE", t / "archive.md"),
                         mock.patch.object(session, "SESSION_STATE_DIR", t / ".session-state")]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _orientation(self):
        buf = io.StringIO()
        with mock.patch.object(session, "sh",
                               lambda argv, check=True: types.SimpleNamespace(
                                   returncode=0, stdout="", stderr="")), \
             mock.patch.object(session, "git_sync",
                               lambda dry, push=True: ("up to date", [])), \
             mock.patch.object(session, "_read_hook_stdin", lambda: {}), \
             mock.patch.object(session, "detect_machine", lambda: "Runner"), \
             redirect_stdout(buf):
            session.cmd_start(argparse.Namespace(dry_run=True))
        return buf.getvalue()

    def _spawn(self, wid="WI-0001"):
        rec = {"dispatch_id": "D-test01", "snapshot_id": "S-test01", "cap": 5,
               "budget": 5, "spawned": 0, "queue": [wid], "log": [],
               "surface": "Terminal", "created_by": "worktree-poga-1"}
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(rec, wid, "worktree-poga-1")

    def test_a_dispatched_lane_is_handed_its_brief_by_session_start(self):
        self._spawn()
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test01",
                                          "POGA_DISPATCH_ITEM": "WI-0001"}):
            out = self._orientation()
        self.assertIn(session.DISPATCH_BRIEF_HEADING, out)
        self.assertIn("poga work claim WI-0001", out)
        self.assertIn("Alpha", out)
        # It must land INSIDE the orientation, after the announce line — the lane orients,
        # reads its assignment, then reads the canon it has to apply to it.
        self.assertLess(out.index("announce:"), out.index(session.DISPATCH_BRIEF_HEADING))

    def test_a_hand_launched_session_start_is_byte_identical_to_before(self):
        """THE NEGATIVE CONTROL. A guard that fires on correct code gets deleted. A session
        operator opened himself has no dispatch in its environment; its orientation must gain
        nothing at all — not a heading, not a blank line, not a claim that it was
        dispatched."""
        os.environ.pop("POGA_DISPATCH", None)
        os.environ.pop("POGA_DISPATCH_ITEM", None)
        out = self._orientation()
        self.assertNotIn(session.DISPATCH_BRIEF_HEADING, out)
        self.assertNotIn("You have been dispatched", out)
        self.assertIn("announce:", out, "the orientation itself must still be produced")
        # NOTHING AT ALL after the announce line — and asserted on the raw bytes rather
        # than a stripped tail, because an `if True` in place of the emptiness check
        # appends a blank section that a stripped comparison cannot see. (Measured: with
        # the guard mutated to always-append, an `rstrip()`-based version of this
        # assertion passed. A test written to kill a mutation may not kill it.)
        tail = out[out.rindex("announce:"):]
        self.assertEqual(tail.count("\n"), 1,
                         f"the orientation gained a trailing section: {tail!r}")


class CapAndBudgetTest(DispatchBase):
    def _rec(self, queue, cap=5, budget=5):
        return {"dispatch_id": "D-test02", "snapshot_id": "S-test01", "cap": cap,
                "budget": budget, "spawned": 0, "queue": list(queue), "log": [],
                "surface": "Terminal", "created_by": "worktree-poga-1"}

    def test_budget_stops_the_chain_and_says_so(self):
        rec = self._rec(["WI-0001", "WI-0002", "WI-0009"], budget=2)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=5)
        self.assertEqual(rec["spawned"], 2)
        self.assertEqual(rec["queue"], ["WI-0009"])
        self.assertTrue(any("budget reached" in x for x in lines))

    def test_cap_holds_the_rest_and_names_the_landing_session_as_the_trigger(self):
        # NOTE (WI-0238) on the `side_effect=[0, 1]` below: this mock SIMULATES the
        # spawned lane registering itself between iterations, which is the one thing the
        # real system cannot do — the lane-alloc record is written by the child, seconds
        # later. So for two years this test asserted a cap that never bound in production,
        # because the test and the code shared the same false premise. It is kept as-is:
        # it still pins the message and the queue split. The property it CANNOT see is
        # pinned by `test_the_cap_binds_when_no_spawned_lane_has_registered_yet`, which
        # mocks nothing.
        rec = self._rec(["WI-0001", "WI-0002"], cap=1, budget=5)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", side_effect=[0, 1]):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=5)
        self.assertEqual(rec["spawned"], 1)
        self.assertEqual(rec["queue"], ["WI-0002"])
        self.assertTrue(any("cap reached" in x for x in lines))
        self.assertTrue(any("LAND" in x for x in lines))

    def test_lanes_in_use_see_both_a_worktree_dir_and_a_bare_lane_branch(self):
        # AMENDED for WI-0156. This assertion used to be made of
        # `_dispatch_live_lane_count`, back when the cap counted every live lane. That
        # reading did not go away — it moved to `_lane_names_in_use`, which is what the
        # ALLOCATOR wants (a slot with a leftover branch must never be reissued). The
        # property is unchanged and still worth pinning: a bare branch with no worktree
        # directory counts as a lane in use.
        _git(self.main, "branch", "worktree-poga-7")
        self.assertGreaterEqual(len(session._lane_names_in_use()), 2)

    def test_the_cap_ignores_lanes_that_no_dispatch_launched(self):
        # The other half of the same amendment, asserted HERE rather than only in
        # test_lane_alloc because this is the file a reader comes to when asking what the
        # cap counts. the operator's ruling: a lane he opened by hand is his own work, not
        # dispatch load. Both lanes below are hand-launched, so the cap sees neither —
        # which is what makes a dispatch always spawn at least one lane, and the
        # six-hour `[draining]` deadlock unreachable.
        _git(self.main, "branch", "worktree-poga-7")
        self.assertGreaterEqual(len(session._lane_names_in_use()), 2)
        self.assertEqual(session._dispatch_live_lane_count(), 0)


class CapBindsOnTheFirstWaveTest(DispatchBase):
    """WI-0238 — THE REGRESSION, and it is deliberately mock-free where it counts.

    Measured 2026-09-03 by causing it: `poga dispatch <15 ids> --cap 5 --run all --go`
    printed "cap 5, budget 15", then spawned all fifteen in one wave. The cap is the only
    thing bounding a fan-out and `--run all` removes the other bound, so the two together
    are precisely where it has to hold.

    Nothing here mocks the lane count. That is the whole point: the count is REAL and, as
    in production, sees none of the lanes this wave just spawned — because their lane-alloc
    record is written by the child process, seconds later. A test that mocks the count into
    moving cannot fail on this bug; the one above it did exactly that and passed for two
    years (`verify-in-the-created-configuration`)."""

    def _rec(self, queue, cap=5, budget=None):
        return {"dispatch_id": "D-wave01", "snapshot_id": "S-test01", "cap": cap,
                "budget": len(queue) if budget is None else budget, "spawned": 0,
                "queue": list(queue), "log": [], "surface": "tmux",
                "created_by": "worktree-poga-1"}

    def _lane_alloc_record(self, lane, did, item):
        """A lane that HAS registered itself — what the child eventually writes."""
        d = session._coord_dir(session.LANE_ALLOC_KIND, create=True)
        (self.main / ".claude" / "worktrees" / lane).mkdir(parents=True, exist_ok=True)
        rec = session._coord_record("someone", session.LANE_ALLOC_TTL_SECONDS, name=lane,
                                    kind=session.LANE_ALLOC_KIND, root=str(self.main),
                                    dispatch_id=did, dispatch_item=item)
        (d / f"{lane}.json").write_text(json.dumps(rec) + "\n", encoding="utf-8")

    def test_the_cap_binds_when_no_spawned_lane_has_registered_yet(self):
        ids = [f"WI-{n:04d}" for n in range(1, 16)]
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            lines = session._dispatch_advance(rec := self._rec(ids, cap=5), "x", want=15)
        self.assertEqual(rec["spawned"], 5)          # not 15
        self.assertEqual(len(rec["queue"]), 10)
        self.assertTrue(any("cap reached" in x for x in lines))
        self.assertEqual(rec["stall"]["kind"], "cap")

    def test_a_lane_that_registers_mid_wave_is_counted_once_not_twice(self):
        """The union, not a bare counter. If the child for WI-0001 wins the race and
        registers while this same wave is still looping, it must not consume two slots —
        an increment-and-also-re-read would bind the cap a lane early, which is a
        different wrong answer, not a safe one."""
        self._lane_alloc_record("poga-9", "D-wave01", "WI-0001")
        self.assertEqual(session._dispatch_live_lane_count(), 1)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_advance(rec := self._rec(["WI-0001", "WI-0002", "WI-0003"],
                                                       cap=2), "x", want=3)
        self.assertEqual(rec["spawned"], 2)
        self.assertEqual(rec["queue"], ["WI-0003"])

    def test_lanes_held_by_another_dispatch_still_consume_the_cap(self):
        """The pending set ADDS to the shared view; it does not replace it. WI-0156's
        reading — only dispatched lanes count, hand-opened ones never do — is untouched."""
        self._lane_alloc_record("poga-8", "D-other", "WI-0099")
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_advance(rec := self._rec(["WI-0001", "WI-0002"], cap=2),
                                      "x", want=2)
        self.assertEqual(rec["spawned"], 1)

    def test_an_unreadable_coord_store_still_bounds_the_wave(self):
        """Fail-open used to mean fail-unbounded. It no longer does: with the shared view
        gone the count degrades to the caller's own pending set, so the cap still holds."""
        ids = [f"WI-{n:04d}" for n in range(1, 16)]
        with mock.patch.object(session, "_lane_names_in_use", side_effect=OSError("boom")), \
             mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_advance(rec := self._rec(ids, cap=5), "x", want=15)
        self.assertEqual(rec["spawned"], 5)

    def test_the_plan_does_not_promise_room_the_cap_does_not_have(self):
        """The second half of WI-0238: the line `lanes (5 now, rest as lanes land)` was
        printed by a path that ignored the cap entirely. It is an approval surface — what
        it says is what got approved — so it measures the room rather than assuming it."""
        rec = {"dispatch_id": "D-plan", "snapshot_id": "S-1", "cap": 5, "budget": 10,
               "surface": "tmux"}
        ids = [f"WI-{n:04d}" for n in range(1, 11)]
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            out = "\n".join(session._dispatch_render_plan(rec, ids, {}, {}, [], []))
        self.assertIn("lanes (3 now", out)
        self.assertIn("already held", out)

    def test_a_plan_that_will_spawn_nothing_says_so_on_the_approval_surface(self):
        rec = {"dispatch_id": "D-plan", "snapshot_id": "S-1", "cap": 2, "budget": 5,
               "surface": "tmux"}
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            out = "\n".join(session._dispatch_render_plan(rec, ["WI-0001"], {}, {}, [], []))
        self.assertIn("lanes (0 now", out)
        self.assertIn("NOTHING will spawn", out)


class AfterLandTest(DispatchBase):
    """Property 4 — no master. The wave trigger is the landing session."""

    def _write_dispatch(self, queue, budget=5):
        rec = {"dispatch_id": "D-land01", "snapshot_id": "S-test01", "cap": 5,
               "budget": budget, "spawned": 0, "queue": list(queue), "log": [],
               "surface": "Terminal", "created_by": "worktree-poga-1"}
        session._dispatch_write(rec)
        return rec

    def test_a_hand_opened_lane_advances_nothing(self):
        self._write_dispatch(["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab") as tab:
            session._dispatch_after_land()
        tab.assert_not_called()
        self.assertEqual(session._dispatch_read("D-land01")["queue"], ["WI-0001"])

    def test_a_dispatched_lane_spawns_exactly_one_successor_on_landing(self):
        self._write_dispatch(["WI-0001", "WI-0002"])
        os.environ["POGA_DISPATCH"] = "D-land01"
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0), \
             redirect_stdout(io.StringIO()):
            session._dispatch_after_land()
        rec = session._dispatch_read("D-land01")
        self.assertEqual(rec["spawned"], 1)
        self.assertEqual(rec["queue"], ["WI-0002"])
        self.assertEqual(rec["log"][0]["item"], "WI-0001")

    def test_an_exhausted_queue_is_a_no_op(self):
        self._write_dispatch([])
        os.environ["POGA_DISPATCH"] = "D-land01"
        with mock.patch.object(session, "_dispatch_open_tab") as tab:
            session._dispatch_after_land()
        tab.assert_not_called()

    def test_a_dispatch_failure_never_raises_into_the_land_path(self):
        """Fail-open is load-bearing: a dispatch problem must not turn a landed lane
        into a failed one."""
        self._write_dispatch(["WI-0001"])
        os.environ["POGA_DISPATCH"] = "D-land01"
        with mock.patch.object(session, "_dispatch_advance",
                               side_effect=RuntimeError("boom")), \
             redirect_stdout(io.StringIO()):
            session._dispatch_after_land()  # must not raise

    def test_an_unknown_dispatch_id_is_a_no_op(self):
        os.environ["POGA_DISPATCH"] = "D-nosuch"
        with mock.patch.object(session, "_dispatch_open_tab") as tab:
            session._dispatch_after_land()
        tab.assert_not_called()


class SurfaceTest(DispatchBase):
    """The surface is DETECTED by default (WI-0049). Every test here mocks the detection
    signal — otherwise the suite's answer would depend on whether the machine running it
    happens to be inside a GUI login, which is the exact ambiguity being removed."""

    def _gui(self, available, manager=None):
        mgr = manager if manager is not None else ("Aqua" if available else "Background")
        return mock.patch.object(session, "_dispatch_session_manager", return_value=mgr)

    def test_the_default_is_auto_and_picks_a_gui_tab_inside_the_gui_session(self):
        with self._gui(True):
            self.assertEqual(session._dispatch_surface(), "Terminal")

    def test_auto_falls_back_to_tmux_outside_the_gui_session(self):
        """The session-~107 finding: an SSH login is launchd-manager 'Background', and an
        Apple Event cannot leave it no matter what Automation permission is granted."""
        with self._gui(False), mock.patch.object(session, "_tmux_bin",
                                                 return_value="/opt/tools/bin/tmux"):
            self.assertEqual(session._dispatch_surface(), "tmux")

    def test_an_unrecognised_manager_name_resolves_to_tmux_not_a_gui_surface(self):
        """The allowlist direction is the safety property: guessing wrong must cost a
        detached session, never a 20s block on a lane that never opens."""
        with self._gui(False, manager="SomethingNew"), \
             mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"):
            self.assertEqual(session._dispatch_surface(), "tmux")

    def test_an_undeterminable_manager_resolves_to_tmux(self):
        with self._gui(False, manager=""), \
             mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"):
            self.assertEqual(session._dispatch_surface(), "tmux")

    def test_auto_with_neither_a_gui_nor_tmux_refuses_and_names_both_ways_out(self):
        with self._gui(False), mock.patch.object(session, "_tmux_bin", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                session._dispatch_surface()
        msg = str(ctx.exception)
        self.assertIn("brew install tmux", msg)
        self.assertIn("Aqua", msg)
        self.assertIn("GUI session", msg)

    def test_an_unlisted_surface_is_refused_by_name_not_tried(self):
        session.CFG = dict(session.CFG, spawn_surface="Warp")
        with self.assertRaises(ValueError) as ctx:
            session._dispatch_surface()
        self.assertIn("Warp", str(ctx.exception))
        self.assertIn("session.config.json", str(ctx.exception))

    def test_an_explicit_surface_is_honoured_and_not_overridden_by_the_detector(self):
        """A declaration outranks a detector — the mismatch is WARNED about, not rewritten,
        so the operator's configured intent is never silently replaced."""
        session.CFG = dict(session.CFG, spawn_surface="iTerm2")
        with self._gui(False):
            self.assertEqual(session._dispatch_surface(), "iTerm2")

    def test_explicit_tmux_without_tmux_installed_is_refused_with_the_install_command(self):
        session.CFG = dict(session.CFG, spawn_surface="tmux")
        with mock.patch.object(session, "_tmux_bin", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                session._dispatch_surface()
        self.assertIn("brew install tmux", str(ctx.exception))

    def test_a_gui_surface_outside_the_gui_session_warns_before_spawning_not_after(self):
        with self._gui(False):
            warn = session._dispatch_surface_mismatch("Terminal")
        self.assertIsNotNone(warn)
        self.assertIn("Background", warn)
        self.assertIn("tmux", warn)
        self.assertIn(str(session.DISPATCH_SPAWN_TIMEOUT_SECONDS), warn)

    def test_no_warning_when_the_gui_surface_can_actually_work(self):
        with self._gui(True):
            self.assertIsNone(session._dispatch_surface_mismatch("Terminal"))

    def test_tmux_never_warns_because_it_needs_no_gui_session(self):
        with self._gui(False):
            self.assertIsNone(session._dispatch_surface_mismatch("tmux"))


@unittest.skipIf(session._under_gate(),
                 "its subject IS the tmux spawn path, which a land gate must not reach "
                 "(ADR-0119 D4)")
class TmuxSpawnTest(DispatchBase):
    """The surface that works from any session — verified live from an SSH login in session
    ~107 (a real detached lane ran its command and was read back with capture-pane) before
    these were written, because a fully-mocked suite was green through the osascript defect."""

    def test_the_session_name_carries_both_the_dispatch_and_the_item(self):
        name = session._tmux_session_name("D-abc123", "WI-0049")
        self.assertIn("D-abc123", name)
        self.assertIn("WI-0049", name)

    def test_tmux_target_reserved_characters_are_mapped_out_of_the_name(self):
        name = session._tmux_session_name("D-a.b", "WI-0049:x")
        self.assertNotIn(".", name)
        self.assertNotIn(":", name)

    def test_the_tmux_surface_routes_to_tmux_and_not_to_osascript(self):
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            ok, detail = session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertTrue(ok)
        # WI-0185 added a `has-session` confirmation AFTER the launch — `spawned` now
        # means OBSERVED, not attempted — so the LAST tmux call is no longer the launching
        # one. Assert on the launch itself, and on the confirmation separately.
        calls = [c.args[0] for c in run.call_args_list]
        launch = next(c for c in calls if "new-session" in c)
        self.assertEqual(launch[0], "/usr/bin/tmux")
        self.assertIn("-d", launch)
        self.assertIn("poga-x", launch)
        self.assertTrue(any("has-session" in c for c in calls))
        for c in calls:
            self.assertNotIn("osascript", c)

    def test_the_tmux_call_is_also_bounded(self):
        """Same argument as the osascript bound: a launcher that can hang is the silent
        strand, whichever surface it hangs on."""
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertEqual(run.call_args.kwargs.get("timeout"),
                         session.DISPATCH_SPAWN_TIMEOUT_SECONDS)

    def test_success_carries_the_attach_command_because_no_window_appears(self):
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            ok, detail = session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertTrue(ok)
        self.assertIn("tmux attach -t poga-x", detail)

    def test_the_attach_line_reaches_the_operator_through_the_spawn_report(self):
        """A lane nobody is told how to reach is the strand this design prevents, so the
        attach line must survive the layer that normally discards success detail."""
        rec = {"dispatch_id": "D-test01", "snapshot_id": "S-test01", "cap": 5, "budget": 5,
               "spawned": 0, "queue": [], "log": [], "surface": "tmux",
               "created_by": "worktree-poga-1"}
        with mock.patch.object(session, "_dispatch_open_tab",
                               return_value=(True, "in tmux session 'poga-q' — attach with "
                                                   "`tmux attach -t poga-q`")):
            ok, detail = session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1")
        self.assertTrue(ok)
        self.assertIn("tmux attach -t poga-q", detail)

    def test_a_duplicate_session_names_the_running_lane_rather_than_looking_like_a_crash(self):
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], 1, "", "duplicate session: poga-x")
            ok, detail = session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertFalse(ok)
        self.assertIn("already running", detail)
        self.assertIn("tmux attach -t poga-x", detail)

    def test_a_missing_tmux_is_reported_not_raised(self):
        with mock.patch.object(session, "_tmux_bin", return_value=None):
            ok, detail = session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertFalse(ok)
        self.assertIn("brew install tmux", detail)

    def test_a_blocked_tmux_is_bounded_and_named(self):
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("tmux", 20)):
            ok, detail = session._dispatch_open_tab("echo hi", "tmux", "poga-x")
        self.assertFalse(ok)
        self.assertIn("blocked", detail)

    def test_the_plan_says_lanes_open_detached_so_that_is_what_gets_approved(self):
        rec = {"dispatch_id": "D-p", "snapshot_id": "S-1", "cap": 5, "budget": 5,
               "surface": "tmux"}
        out = "\n".join(session._dispatch_render_plan(rec, ["WI-0001"], {}, {}, [], []))
        self.assertIn("tmux", out)
        self.assertIn("attach", out)

    def test_the_plan_says_tabs_when_tabs_are_what_will_open(self):
        rec = {"dispatch_id": "D-p", "snapshot_id": "S-1", "cap": 5, "budget": 5,
               "surface": "Terminal"}
        out = "\n".join(session._dispatch_render_plan(rec, ["WI-0001"], {}, {}, [], []))
        self.assertIn("Terminal tabs", out)

    def test_the_dry_run_names_the_tmux_session_it_would_start(self):
        rec = {"dispatch_id": "D-dry", "snapshot_id": "S-1", "cap": 5, "budget": 5,
               "spawned": 0, "queue": [], "log": [], "surface": "tmux",
               "created_by": "worktree-poga-1"}
        ok, detail = session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1",
                                                 dry_run=True)
        self.assertTrue(ok)
        self.assertIn("tmux session", detail)


class CliTest(DispatchBase):
    def _run(self, **kw):
        args = argparse.Namespace(items="1-3", go=False, cap=None, run=None,
                                  subagent="", force=False)
        for k, v in kw.items():
            setattr(args, k, v)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_dispatch(args)
            except SystemExit as e:
                return buf.getvalue(), e.code
        return buf.getvalue(), 0

    def test_plan_is_the_default_and_spawns_nothing(self):
        self._pin_snapshot({"1": "WI-0001", "2": "WI-0002", "3": "WI-0003"})
        with mock.patch.object(session, "_dispatch_open_tab") as tab:
            out, code = self._run()
        tab.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("plan only", out)
        self.assertIn("WI-0001", out)

    def test_without_a_snapshot_an_ordinal_range_refuses_and_names_the_fix(self):
        out, code = self._run(items="1-3")
        self.assertEqual(code, 2)
        self.assertIn("poga work list", out)

    def test_explicit_ids_work_with_no_snapshot_pinned(self):
        out, code = self._run(items="WI-0001")
        self.assertEqual(code, 0)
        self.assertIn("WI-0001", out)

    def test_go_refuses_when_a_token_did_not_resolve(self):
        self._pin_snapshot({"1": "WI-0001"})
        with mock.patch.object(session, "_dispatch_open_tab") as tab:
            out, code = self._run(items="1,7", go=True)
        tab.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("unresolved", out.lower())

    def test_force_dispatches_the_resolved_ones_only(self):
        self._pin_snapshot({"1": "WI-0001"})
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            out, code = self._run(items="1,7", go=True, force=True)
        self.assertEqual(code, 0)
        self.assertIn("1 lane(s) spawned", out)

    def test_default_budget_is_one_wave(self):
        self._pin_snapshot({str(i): w for i, w in
                            enumerate(["WI-0001", "WI-0002", "WI-0009"], start=1)})
        out, _ = self._run(items="1-3", cap=2)
        self.assertIn("cap 2, budget 2", out)

    def test_run_all_sets_the_budget_to_the_whole_range(self):
        self._pin_snapshot({str(i): w for i, w in
                            enumerate(["WI-0001", "WI-0002", "WI-0009"], start=1)})
        out, _ = self._run(items="1-3", run="all")
        self.assertIn("budget 3", out)

    def test_subagent_items_are_kept_here_and_not_queued_as_lanes(self):
        self._pin_snapshot({"1": "WI-0001", "2": "WI-0002"})
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            out, _ = self._run(items="1-2", subagent="1", go=True)
        self.assertIn("subagents (run in THIS session)", out)
        self.assertIn("Subagent items stay with you: WI-0001", out)
        self.assertIn("1 lane(s) spawned", out)

    def test_held_items_are_shown_with_their_blockers(self):
        self._pin_snapshot({"1": "WI-0005"})
        out, _ = self._run(items="1")
        self.assertIn("held (blocked by work outside this range)", out)
        self.assertIn("WI-0009", out)


class StalledDispatchTest(DispatchBase):
    """WI-0171 / ADR-0100 — a dispatch that spawns ZERO is a distinct state, says why,
    and can be retried or retired without hand-editing json under `.git/`.

    The live failure: D-4d41da (2026-08-22) hit a full cap on its first iteration, so the
    launcher was never reached; the record read `spawned 0, log [], held {}` and
    `dispatch-status` rendered it `[draining]` — the same word a healthy wave gets — for
    six hours. Two things were wrong and both are pinned here: the state had no name, and
    the queue had no way to move.
    """

    def _rec(self, queue, spawned=0, cap=2, budget=2):
        return {"dispatch_id": "D-stall1", "snapshot_id": "S-test01", "cap": cap,
                "budget": budget, "spawned": spawned, "queue": list(queue), "log": [],
                "surface": "tmux", "created_by": "worktree-poga-1"}

    def test_a_capped_wave_that_spawns_nothing_records_why(self):
        rec = self._rec(["WI-0001", "WI-0002"])
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            session._dispatch_advance(rec, "worktree-poga-1", want=2)
        self.assertEqual(rec["spawned"], 0)
        self.assertEqual(rec["stall"]["kind"], "cap")
        self.assertIn("cap reached (2/2", rec["stall"]["detail"])

    def test_spawned_zero_is_stalled_not_draining(self):
        self.assertEqual(session._dispatch_state(self._rec(["WI-0001"])), "stalled")
        self.assertEqual(
            session._dispatch_state(self._rec(["WI-0001"], spawned=1)), "draining")
        self.assertEqual(session._dispatch_state(self._rec([], spawned=1)), "complete")

    def test_the_self_drain_promise_is_withheld_when_no_lane_is_running(self):
        """The original text promised 'the next lane to LAND spawns the next one' over a
        dispatch with no lane to land. That sentence is the deadlock, written as reassurance."""
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=1)
        blob = " ".join(lines)
        self.assertIn("NOTHING will advance this queue", blob)
        self.assertNotIn("the next lane to LAND spawns the next one", blob)

    def test_the_self_drain_promise_is_kept_when_a_lane_IS_running(self):
        rec = self._rec(["WI-0001"], spawned=1, budget=5)
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=1)
        self.assertIn("the next lane to LAND spawns the next one", " ".join(lines))

    def test_a_successful_spawn_retires_the_stall_note(self):
        rec = self._rec(["WI-0001", "WI-0002"])
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        self.assertIn("stall", rec)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        self.assertNotIn("stall", rec)

    def test_status_prints_the_stall_reason_without_a_verbose_flag(self):
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_dispatch_status(argparse.Namespace(verbose=False))
        out = buf.getvalue()
        self.assertIn("[stalled]", out)
        self.assertIn("STALLED —", out)
        # WI-0158: pin the SHAPE of the remedy, not just the id. `dispatch-retry
        # D-stall1` matched the old `poga dispatch-retry …` text just as happily, so the
        # assertion meant to protect this line could not tell a runnable command from one
        # poga answers with "is not a poga verb". Naming the wrong form too is what makes a
        # regression fail here instead of only in front of a reader.
        self.assertIn("python3 session.py dispatch-retry D-stall1", out)
        self.assertNotIn("poga dispatch-retry", out)

    def test_a_record_with_no_stall_note_says_so_rather_than_inventing_one(self):
        """`declare-what-a-check-assumes`: a pre-fix record genuinely has no reason, and
        the surface must say that instead of printing a plausible guess."""
        session._dispatch_write(self._rec(["WI-0001"]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_dispatch_status(argparse.Namespace(verbose=False))
        self.assertIn("no reason recorded", buf.getvalue())

    def test_retry_spawns_once_the_cap_frees(self):
        session._dispatch_write(self._rec(["WI-0001", "WI-0002"]))
        args = argparse.Namespace(dispatch_id="D-stall1", cap=None, run=None, dry_run=False)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            buf = io.StringIO()
            with redirect_stdout(buf):
                session.cmd_dispatch_retry(args)
        rec = session._dispatch_read("D-stall1")
        self.assertEqual(rec["spawned"], 2)
        self.assertEqual(rec["queue"], [])
        self.assertEqual(session._dispatch_state(rec), "complete")

    def test_retry_can_raise_the_cap_that_blocked_it(self):
        session._dispatch_write(self._rec(["WI-0001"]))
        args = argparse.Namespace(dispatch_id="D-stall1", cap=9, run=None, dry_run=False)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=2):
            with redirect_stdout(io.StringIO()):
                session.cmd_dispatch_retry(args)
        self.assertEqual(session._dispatch_read("D-stall1")["spawned"], 1)

    def test_retry_on_an_unknown_id_exits_2_rather_than_inventing_a_dispatch(self):
        args = argparse.Namespace(dispatch_id="D-nope99", cap=None, run=None, dry_run=False)
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()):
                session.cmd_dispatch_retry(args)
        self.assertEqual(cm.exception.code, 2)

    def test_cancel_releases_the_queue_keeps_the_record_and_names_what_it_dropped(self):
        session._dispatch_write(self._rec(["WI-0001", "WI-0002"]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_dispatch_cancel(
                argparse.Namespace(dispatch_id="D-stall1", reason="superseded"))
        out = buf.getvalue()
        rec = session._dispatch_read("D-stall1")
        self.assertIsNotNone(rec, "the record must survive — it is the spawned lanes' provenance")
        self.assertEqual(rec["queue"], [])
        self.assertEqual(rec["cancelled"]["dropped"], ["WI-0001", "WI-0002"])
        self.assertEqual(rec["cancelled"]["reason"], "superseded")
        self.assertIn("released: WI-0001, WI-0002", out)

    def test_a_cancelled_dispatch_cannot_be_retried(self):
        session._dispatch_write(self._rec(["WI-0001"]))
        with redirect_stdout(io.StringIO()):
            session.cmd_dispatch_cancel(
                argparse.Namespace(dispatch_id="D-stall1", reason=""))
        args = argparse.Namespace(dispatch_id="D-stall1", cap=None, run=None, dry_run=False)
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()):
                session.cmd_dispatch_retry(args)
        self.assertEqual(cm.exception.code, 2)


@unittest.skipIf(session._under_gate(),
                 "asserts on real send-keys calls, which a land gate must not make "
                 "(ADR-0119 D4)")
class BriefedNotJustSpawnedTest(DispatchBase):
    """Property 6 — a lane counts as SPAWNED only once it has been told what to do.

    WI-0248. `tmux new-session` exiting 0 says tmux accepted the request; `has-session`
    says a process is alive. NEITHER says the lane received its brief. Measured live
    2026-09-03 (D-d9202c): three of five lanes came up at a virgin prompt with no
    instruction and every surface still called them spawned — the dispatch record counted
    them, the session existed, attach showed a healthy prompt. A lane that was never told
    anything is indistinguishable from one thinking hard, and it sits there forever.

    The load-bearing case is the argv probe's TARGET (below): the brief appears on more
    than one process in the chain, so a whole-chain scan would report every lane briefed
    and certify the exact failure this detector exists to find.

    TWO MORE FAILURES ARE PINNED HERE BECAUSE BOTH BROKE THE DETECTOR SILENTLY, IN THE
    DIRECTION OF SAYING NOTHING — which is indistinguishable from a healthy dispatch:

      * the lane-alloc reservation carries a 5-minute TTL refreshed by heartbeats, and
        heartbeats ride on TOOL CALLS, so an unbriefed lane never refreshes it. A live-only
        lookup therefore loses the lane exactly when something wants to repair it;
      * the confirm pass polled with `while … < deadline`, which at grace 0 never read at
        all and called every receipt on disk `no-receipt`.
    """

    DID = "D-brief1"

    # ---- fixtures --------------------------------------------------------------------

    def _rec(self, items, surface="tmux", spawned=None, **extra):
        rec = {"dispatch_id": self.DID, "snapshot_id": "S-test01", "cap": 5, "budget": 5,
               "spawned": len(items) if spawned is None else spawned, "queue": [],
               "log": [{"item": w, "at": "", "by": "worktree-poga-1"} for w in items],
               "surface": surface, "created_by": "worktree-poga-1"}
        rec.update(extra)
        return rec

    def _write_slot(self, wid, **fields):
        import json
        session._coord_dir(session.DISPATCH_SPAWN_KIND, create=True)
        path = session._dispatch_slot_path(self.DID, wid)
        payload = {"item": wid, "dispatch_id": self.DID, "snapshot_id": "S-test01"}
        payload.update(fields)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def _dispatched_lane(self, lane, wid):
        """A lane-alloc record shaped the way `_lane_reserve` shapes one for a DISPATCHED
        lane — the same route `test_lane_alloc` uses, because that record is the only place
        the dispatched/hand-launched distinction is captured."""
        (self.main / ".claude" / "worktrees" / lane).mkdir(parents=True, exist_ok=True)
        os.environ["POGA_DISPATCH"] = self.DID
        os.environ["POGA_DISPATCH_ITEM"] = wid
        try:
            session._lane_reserve(lane, "launcher", self.main)
        finally:
            os.environ.pop("POGA_DISPATCH", None)
            os.environ.pop("POGA_DISPATCH_ITEM", None)

    def _expired_lane_alloc(self, lane, wid, age=3600):
        """A lane-alloc reservation shaped like `_lane_reserve`'s, but whose TTL ran out
        `age` seconds ago — the state EVERY unbriefed lane reaches, because the reservation
        is refreshed by heartbeats and heartbeats ride on tool calls."""
        import json
        d = session._coord_dir(session.LANE_ALLOC_KIND, create=True)
        now = time.time()
        (d / f"{lane}.json").write_text(json.dumps({
            "session_id": "gone", "name": lane, "kind": session.LANE_ALLOC_KIND,
            "root": str(self.main), "branch": f"worktree-{lane}",
            "created_at": "", "expires_at": now - age, "expires_iso": "",
            "ttl_seconds": session.LANE_ALLOC_TTL_SECONDS,
            "dispatch_id": self.DID, "dispatch_item": wid,
        }) + "\n", encoding="utf-8")
        return d / f"{lane}.json"

    def _idle_lane_dir(self, lane):
        """A worktree that reached SessionStart and never made a tool call — ADR-0055's
        marker still sitting there unconsumed."""
        sd = self.main / ".claude" / "worktrees" / lane / ".session-state"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "sess-abc.pending-start").write_text("", encoding="utf-8")
        return sd

    def _brief_on_spawn(self, state="briefed"):
        """A tab that opens AND whose lane files its receipt — the healthy path end to end.
        The slot already exists when the tab is asked for (provenance-before-spawn), so the
        receipt merges onto it exactly as the real lane's hook does."""
        import json

        def _tab(cmd, surface, name=""):
            d = session._coord_dir(session.DISPATCH_SPAWN_KIND)
            for p in sorted(d.glob("*.json")):
                rec = session._coord_read(p) or {}
                if "brief" not in rec:
                    rec["brief"] = state
                    session.atomic_write(p, json.dumps(rec) + "\n")
            return True, ""
        return _tab

    # ---- the orientation probe: three states, and WHICH artifact it asks about -------
    #
    # WI-0390 MOVED THIS QUESTION because it moved the channel. These tests used to drive
    # an argv scan of the runtime process, which was the right witness while the brief WAS
    # the runtime's trailing argument. It is not any more — that argument is exactly what
    # made `poga` classify the brief as the operator's prompt and withhold canon — so a
    # detector still asking the old question would answer `unbriefed` for every correctly
    # briefed lane in the fleet AND send the re-brief path to type a duplicate into each
    # one. The witness is now the orientation block the lane is actually handed.

    def test_an_orientation_carrying_the_brief_reads_as_briefed(self):
        state, detail = session._dispatch_brief_state(
            "WI-0001", "=== YOUR DISPATCH BRIEF ===\nYou have been dispatched on WI-0001")
        self.assertEqual(state, "briefed")
        self.assertIn("orientation", detail)

    def test_a_brief_that_resolves_to_nothing_reads_as_unbriefed(self):
        """A MEASUREMENT, not a read failure: the environment named a dispatched item and
        the brief for it came back empty. That is the state a re-brief exists for."""
        state, detail = session._dispatch_brief_state("WI-0001", "")
        self.assertEqual(state, "unbriefed")
        self.assertIn("WI-0001", detail)

    def test_whitespace_is_not_a_brief(self):
        """`"\\n\\n"` is falsy in no useful sense — an empty block must not vouch for a
        lane just because the renderer left a newline behind."""
        state, _detail = session._dispatch_brief_state("WI-0001", "\n   \n")
        self.assertEqual(state, "unbriefed")

    def test_no_dispatch_item_is_unknown_rather_than_a_verdict(self):
        """And it must not even go looking: an empty item is not a lane to have an opinion
        about, and resolving a brief for `""` would be a question with no subject."""
        with mock.patch.object(session, "_dispatch_brief_block") as block:
            state, _detail = session._dispatch_brief_state("", None)
        block.assert_not_called()
        self.assertEqual(state, "unknown")

    def test_a_brief_that_cannot_be_RESOLVED_is_unknown_not_unbriefed(self):
        """Reporting a healthy lane as unbriefed strands it worse than the bug does — the
        re-brief path types into it. 'Could not tell' has to keep its own word, and a
        coord store that cannot be read is precisely could-not-tell."""
        with mock.patch.object(session, "_dispatch_brief_block",
                               side_effect=OSError("coord store unreadable")):
            state, detail = session._dispatch_brief_state("WI-0001", None)
        self.assertEqual(state, "unknown")
        self.assertIn("unreadable", detail)

    def test_the_detector_derives_the_block_when_none_is_handed_to_it(self):
        """The caller that holds the block passes it, so the receipt is about THIS start's
        delivery. Every other caller gets the same pure function over the same records —
        the two agree by construction rather than by timing."""
        with mock.patch.object(session, "_dispatch_brief_block",
                               return_value="a brief") as block:
            state, _detail = session._dispatch_brief_state("WI-0001")
        block.assert_called_once_with(wid="WI-0001")
        self.assertEqual(state, "briefed")

    # ---- the receipt half ------------------------------------------------------------

    def test_a_lane_that_was_not_dispatched_writes_no_receipt_at_all(self):
        os.environ.pop("POGA_DISPATCH", None)
        os.environ["POGA_DISPATCH_ITEM"] = "WI-0001"
        with mock.patch.object(session, "atomic_write") as write:
            session._dispatch_write_receipt()          # must not raise
        write.assert_not_called()

    def test_the_receipt_merges_onto_the_spawn_slot_without_destroying_it(self):
        """The two halves meet on one record with no coordinator: the spawner writes the
        provenance, the lane writes the verdict. A receipt that REPLACED the slot would
        erase the provenance that makes a dispatch-spawned lane non-anonymous."""
        import json
        rec = self._rec([], surface="Terminal", spawned=0)
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")):
            session._dispatch_try_spawn(rec, "WI-0001", "worktree-poga-1")
        os.environ["POGA_DISPATCH"] = self.DID
        os.environ["POGA_DISPATCH_ITEM"] = "WI-0001"
        with mock.patch.object(session, "_dispatch_brief_state",
                               return_value=("unbriefed", "no brief could be built")):
            session._dispatch_write_receipt()
        slot = json.loads(session._dispatch_slot_path(self.DID, "WI-0001")
                          .read_text(encoding="utf-8"))
        self.assertEqual(slot["brief"], "unbriefed")
        self.assertEqual(slot["brief_detail"], "no brief could be built")
        self.assertTrue(slot["brief_at"])
        self.assertIn("brief_lane", slot)
        # Everything `_dispatch_try_spawn` put there is still there.
        self.assertEqual(slot["item"], "WI-0001")
        self.assertEqual(slot["dispatch_id"], self.DID)
        self.assertEqual(slot["snapshot_id"], "S-test01")

    def test_a_corrupt_or_missing_slot_never_raises_into_the_session_start(self):
        """Fail-open in every direction: a receipt problem must never be the thing that
        breaks a session start, and a missing receipt is a state the reader handles."""
        os.environ["POGA_DISPATCH"] = self.DID
        os.environ["POGA_DISPATCH_ITEM"] = "WI-0001"
        session._dispatch_write_receipt()                     # slot absent entirely
        self._write_slot("WI-0001").write_text("{not json at all", encoding="utf-8")
        session._dispatch_write_receipt()                     # slot unparseable
        self._write_slot("WI-0001")
        with mock.patch.object(session, "_dispatch_brief_state",
                               side_effect=RuntimeError("ps exploded")):
            session._dispatch_write_receipt()                 # the probe itself blew up

    # ---- an unbriefed lane must not hold dispatch capacity ---------------------------

    def test_an_unbriefed_lane_does_not_consume_dispatch_capacity(self):
        """One lost prompt must not block the whole queue: the lane is alive and holds a
        worktree, but it was never told what to do and never will be by itself."""
        self._dispatched_lane("poga-2", "WI-0002")
        self._write_slot("WI-0002", brief="unbriefed")
        self.assertIn("poga-2", session._lane_names_in_use())
        self.assertEqual(session._dispatch_live_lane_count(), 0)

    def test_pending_unknown_and_a_missing_receipt_all_still_count(self):
        """Only a MEASURED `unbriefed` is excluded, which is the conservative direction:
        under-spawning costs a wave that drains slower, over-spawning costs real money and
        a machine full of sessions nobody asked for."""
        self._dispatched_lane("poga-2", "WI-0002")
        self._write_slot("WI-0002", brief="unbriefed")
        self._dispatched_lane("poga-3", "WI-0003")
        self._write_slot("WI-0003", brief="pending")
        self._dispatched_lane("poga-4", "WI-0004")            # no slot written at all
        self._dispatched_lane("poga-5", "WI-0005")
        self._write_slot("WI-0005", brief="unknown")
        self.assertEqual(session._dispatch_live_lane_count(), 3)

    # ---- ground truth: did the lane ever start WORKING? ------------------------------

    def test_an_unconsumed_pending_start_marker_means_the_lane_never_worked(self):
        """ADR-0055's marker is written by SessionStart and CONSUMED BY THE FIRST
        heartbeat, and heartbeats ride on tool calls — so a lane nobody briefed keeps its
        marker forever. *A phantom preload never beats.*"""
        sd = self.main / ".claude" / "worktrees" / "poga-3" / ".session-state"
        sd.mkdir(parents=True)
        (sd / "sess-abc.pending-start").write_text("", encoding="utf-8")
        self.assertIs(session._dispatch_lane_working("poga-3"), False)
        (sd / "sess-abc.pending-start").unlink()
        self.assertIs(session._dispatch_lane_working("poga-3"), True)

    def test_a_lane_with_no_session_state_is_unknown_and_never_reads_as_idle(self):
        """Absence of evidence must not read as evidence. `None` and `False` route to
        opposite branches here, and only one of them types into a live session."""
        self.assertIsNone(session._dispatch_lane_working("poga-9"))
        self.assertIsNone(session._dispatch_lane_working(""))
        self.assertIsNot(session._dispatch_lane_working("poga-9"), False)

    # ---- re-briefing ------------------------------------------------------------------

    def test_the_brief_and_the_enter_are_two_separate_send_keys_calls(self):
        """Combined into one, the TUI swallows the Enter and the text sits at the prompt
        unsent (measured in a peer lane, session ~185). `-l` sends the text literally, so a
        brief containing `;` or a key name cannot be re-read as tmux key syntax."""
        with mock.patch.object(session, "_tmux_bin", return_value="/usr/bin/tmux"), \
             mock.patch.object(session.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            ok, _detail = session._dispatch_rebrief_tmux("poga-x", "go do WI-0001")
        self.assertTrue(ok)
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(len(calls), 2, calls)
        self.assertEqual(calls[0], ["/usr/bin/tmux", "send-keys", "-t", "poga-x", "-l",
                                    "go do WI-0001"])
        self.assertEqual(calls[1], ["/usr/bin/tmux", "send-keys", "-t", "poga-x", "Enter"])

    def test_a_lane_that_is_not_briefed_but_IS_working_is_never_typed_into(self):
        """The one thing this must never do. Not-briefed alone is not enough: the argv
        probe answers `unbriefed` for a lane someone briefed by hand, and injecting text
        into a session that is thinking is worse than the strand being fixed."""
        self._write_slot("WI-0001", brief="briefed")
        self._write_slot("WI-0002", brief="unbriefed")
        rec = self._rec(["WI-0001", "WI-0002"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as rebrief:
            lines = session._dispatch_confirm_briefs(rec, grace=5)
        rebrief.assert_not_called()
        self.assertEqual(rec["briefed"], 1)
        self.assertEqual(rec["brief_states"],
                         {"WI-0001": "briefed", "WI-0002": "unbriefed"})
        self.assertTrue(any("left alone" in x for x in lines))

    def test_a_non_tmux_surface_says_it_cannot_re_brief_rather_than_doing_nothing(self):
        """A surface with no way to type into it is a fact the operator has to be handed;
        silence here is the same silent strand moved one layer out."""
        self._write_slot("WI-0001", brief="unbriefed")
        rec = self._rec(["WI-0001"], surface="Terminal")
        with mock.patch.object(session, "_dispatch_lane_working", return_value=False), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as rebrief:
            lines = session._dispatch_confirm_briefs(rec, grace=5)
        rebrief.assert_not_called()
        blob = " ".join(lines)
        self.assertIn("NOT BRIEFED", blob)
        self.assertIn("cannot be re-briefed automatically", blob)
        self.assertIn("Terminal", blob)
        self.assertEqual(rec["briefed"], 0)

    def test_a_slot_that_never_reports_is_recorded_as_no_receipt_not_as_briefed(self):
        """A silent slot is the case where the hook never ran at all — exactly how the
        D-d9202c lanes looked. Counting it as briefed is how the bug hid."""
        rec = self._rec(["WI-0007"], surface="Terminal")
        with mock.patch.object(session, "_dispatch_lane_working", return_value=None):
            session._dispatch_confirm_briefs(rec, grace=0)
        self.assertEqual(rec["brief_states"], {"WI-0007": "no-receipt"})
        self.assertEqual(rec["briefed"], 0)

    # ---- the grace bounds how long we WAIT, never whether we LOOK --------------------

    def test_a_receipt_already_on_disk_is_seen_even_with_no_grace_at_all(self):
        """A do-while, not a while. `while pending and time.time() < deadline` never enters
        its body at grace 0, so every slot came back `no-receipt` — including receipts
        already sitting on disk — and the count read 0 briefed over a wave that was fine.
        The failure is silent in both directions: it under-reports a healthy dispatch, and
        it is indistinguishable from the real strand this feature exists to name."""
        self._write_slot("WI-0001", brief="briefed")
        self._write_slot("WI-0002", brief="unbriefed")
        rec = self._rec(["WI-0001", "WI-0002"], surface="Terminal")
        with mock.patch.object(session, "_dispatch_lane_working", return_value=None):
            session._dispatch_confirm_briefs(rec, grace=0)
        self.assertEqual(rec["brief_states"],
                         {"WI-0001": "briefed", "WI-0002": "unbriefed"})
        self.assertEqual(rec["briefed"], 1)

    def test_the_zero_grace_read_does_not_sleep_on_its_way_out(self):
        """The other half of the same shape. A poll interval spent AFTER the last read buys
        nothing — nobody looks again — and it is paid once per wave on the healthy path,
        which is every wave. Asserted on `time.sleep` rather than on elapsed wall-clock so
        it pins the code and not the machine's mood."""
        rec = self._rec(["WI-0007"], surface="Terminal")
        with mock.patch.object(session.time, "sleep") as slept, \
             mock.patch.object(session, "_dispatch_lane_working", return_value=None):
            session._dispatch_confirm_briefs(rec, grace=0)
        slept.assert_not_called()
        self.assertEqual(rec["brief_states"], {"WI-0007": "no-receipt"})

    # ---- what the report is allowed to SAY about a silent slot -----------------------

    def test_a_measured_unbriefed_is_announced_on_its_own_evidence(self):
        """`unbriefed` came off a receipt the lane itself wrote about its own runtime, so
        it needs no second signal to be worth printing."""
        self._write_slot("WI-0007", brief="unbriefed")
        rec = self._rec(["WI-0007"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            session._dispatch_print_unbriefed(rec)
        out = buf.getvalue()
        self.assertIn("UNBRIEFED — WI-0007 (unbriefed)", out)
        self.assertIn("never received its brief", out)
        self.assertIn("tmux attach -t", out)

    def test_a_no_receipt_item_whose_lane_is_gone_is_never_announced_as_stranded(self):
        """`no-receipt` is ABSENCE OF A MECHANISM, not evidence of a stranded lane. Run
        against real records without this rule, the report printed 'this lane never
        received its brief' over every lane of every dispatch that predates the receipt —
        finished lanes included. A warning that fires on all of history is read as noise,
        which costs the ones that are real (`no-fabricated-data`)."""
        rec = self._rec(["WI-0007"], surface="tmux")
        rec["brief_states"] = {"WI-0007": "no-receipt"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            session._dispatch_print_unbriefed(rec)
        self.assertEqual(buf.getvalue(), "",
                         "a dispatch older than the receipt must not be reported stranded")

    def test_a_no_receipt_item_whose_lane_is_demonstrably_idle_IS_announced(self):
        """The other side of the same rule — and the reason `_dispatch_lane_for` has to
        keep resolving an EXPIRED reservation. Both signals are present here: the slot is
        silent AND the worktree still holds an unconsumed `.pending-start`."""
        self._expired_lane_alloc("poga-2", "WI-0007")
        self._idle_lane_dir("poga-2")
        rec = self._rec(["WI-0007"], surface="tmux")
        rec["brief_states"] = {"WI-0007": "no-receipt"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            session._dispatch_print_unbriefed(rec)
        out = buf.getvalue()
        self.assertIn("UNBRIEFED — WI-0007 (no-receipt)", out)
        self.assertIn("and it is idle", out)

    # ---- naming the lane AFTER its reservation has lapsed ----------------------------

    def test_an_expired_lane_reservation_still_names_its_lane(self):
        """The TTL that hid the bug. The reservation is refreshed by heartbeats and
        heartbeats ride on TOOL CALLS — so an unbriefed lane, which makes none, stops
        refreshing it. Five minutes later a live-only listing drops the record and the lane
        becomes unfindable EXACTLY WHEN something wants to name or repair it: once the TTL
        lapses without a heartbeat, `_dispatch_lane_for` goes from returning the lane's
        name to returning ''.

        The TTL governs whether the SLOT may be reissued, never whether the lane exists."""
        self._expired_lane_alloc("poga-2", "WI-0002")
        self.assertNotIn("poga-2", session._coord_list(session.LANE_ALLOC_KIND),
                         "the record must be genuinely expired or this test pins nothing")
        self.assertIn("poga-2", session._coord_list(session.LANE_ALLOC_KIND,
                                                    include_expired=True))
        self.assertEqual(session._dispatch_lane_for(self.DID, "WI-0002"), "poga-2")

    def test_an_expired_reservation_still_reports_its_lane_as_idle_not_unknown(self):
        """The whole reason the lookup matters. `False` (idle) and `None` (cannot tell)
        route to opposite branches in both the report and the re-brief, so a lapsed TTL
        must not be able to downgrade a measured idle lane into a shrug."""
        self._expired_lane_alloc("poga-2", "WI-0002")
        self._idle_lane_dir("poga-2")
        lane = session._dispatch_lane_for(self.DID, "WI-0002")
        self.assertIs(session._dispatch_lane_working(lane), False)
        self.assertIsNot(session._dispatch_lane_working(lane), None)

    # ---- where the confirm pass sits in the wave -------------------------------------

    def test_confirm_off_does_not_run_the_confirm_pass_at_all(self):
        """Dry runs and callers that must not block get the launch-only reading, and get
        it by asking for it rather than by accident."""
        rec = self._rec([], spawned=0, queue=["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0), \
             mock.patch.object(session, "_dispatch_confirm_briefs") as confirm:
            session._dispatch_advance(rec, "worktree-poga-1", want=1, confirm=False)
        confirm.assert_not_called()
        self.assertEqual(rec["spawned"], 1)

    def test_the_confirm_pass_sees_only_the_items_THIS_wave_spawned(self):
        """The log accumulates across waves. Re-confirming a settled lane would re-type a
        brief into a session that has been working for an hour."""
        rec = self._rec(["WI-0009"], spawned=1, queue=["WI-0001"])
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0), \
             mock.patch.object(session, "_dispatch_confirm_briefs",
                               return_value=[]) as confirm:
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        self.assertEqual(confirm.call_args.kwargs["items"], ["WI-0001"])
        self.assertEqual([e["item"] for e in rec["log"]], ["WI-0009", "WI-0001"])

    def test_the_briefed_count_accumulates_across_waves_rather_than_resetting(self):
        """Merged, not replaced: a later wave that overwrote `brief_states` would leave
        `briefed` counting only the most recent lanes, so the total would go DOWN as the
        dispatch made progress."""
        rec = self._rec([], spawned=0, queue=["WI-0001", "WI-0002"])
        with mock.patch.object(session, "_dispatch_open_tab",
                               side_effect=self._brief_on_spawn()), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
            self.assertEqual(rec["briefed"], 1)
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        self.assertEqual(rec["briefed"], 2)
        self.assertEqual(rec["brief_states"],
                         {"WI-0001": "briefed", "WI-0002": "briefed"})

    def test_the_wave_brief_check_line_compares_this_wave_against_itself(self):
        """Both halves of `N/M confirmed briefed this wave` must describe THE SAME WAVE.

        `rec["briefed"]` accumulates across every wave; pairing it with a this-wave
        denominator prints a numerator LARGER than its denominator (`2/1`) on any mixed
        wave after the first — and it does so at exactly the moment an operator is staring
        at a real strand and deciding whether the counter can be trusted. A broken counter
        beside a genuine failure is worse than no counter: it discredits the report that
        was right.

        Wave 1 briefs WI-0001. Wave 2 leaves WI-0002 unbriefed, which is what makes the
        line print at all (it is emitted only when something did not come back briefed).
        """
        rec = self._rec([], spawned=0, queue=["WI-0001", "WI-0002"])
        with mock.patch.object(session, "_dispatch_open_tab",
                               side_effect=self._brief_on_spawn()), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0):
            session._dispatch_advance(rec, "worktree-poga-1", want=1)
        # Wave 2: spawn WI-0002 but write NO receipt for it, so it stays unconfirmed.
        with mock.patch.object(session, "_dispatch_open_tab", return_value=(True, "")), \
             mock.patch.object(session, "_dispatch_live_lane_count", return_value=0), \
             mock.patch.object(session, "_dispatch_lane_working", return_value=None):
            lines = session._dispatch_advance(rec, "worktree-poga-1", want=1)

        check = [x for x in lines if "brief check:" in x]
        self.assertEqual(len(check), 1, f"expected one brief-check line, got {lines}")
        # The accumulated total is 1 (WI-0001 from wave 1); this wave briefed none of its
        # one item. The bug printed the accumulated numerator: `1/1`, claiming a lane that
        # is in fact unbriefed came back fine.
        self.assertIn("0/1", check[0])
        self.assertNotIn("1/1", check[0])
        # And the accumulated counter itself is untouched by the display fix.
        self.assertEqual(rec["briefed"], 1)


class TurnBudgetTest(DispatchBase):
    """WI-0288 R2 (2) — a dispatched lane launches with a TURN BUDGET.

    Nothing bounds a dispatched lane's LENGTH today. The cap bounds how many lanes run
    at once and the spawn budget bounds how many are ever opened; neither says anything
    about a single lane that keeps going, which is the cost R2 was ruled on — 4.89B
    tokens over fourteen days, the longest sessions paying the most.

    THE NAME COLLISION IS DELIBERATE SCAR TISSUE. `rec["budget"]` already means the
    SPAWN COUNT and `CapAndBudgetTest` above asserts on it. A turns budget must never
    reuse that key, so it is `turn_budget` everywhere and this class pins that the two
    do not touch.

    IT IS NOT A `claude` FLAG. Verified against the installed CLI rather than assumed:
    `claude --help` carries no `--max-turns` at all (only `--max-budget-usd`, and that
    one is `--print`-only), while these lanes launch as interactive TUI sessions through
    `poga`. So the budget is enforced by our own substrate at the turn boundary — the
    `Stop` hook, which is the only event that fires once per turn — or it is not
    enforced at all.
    """

    def _rec(self, queue, cap=5, budget=5, turn_budget=None):
        rec = {"dispatch_id": "D-test09", "snapshot_id": "S-test01", "cap": cap,
               "budget": budget, "spawned": 0, "queue": list(queue), "log": [],
               "surface": "Terminal", "created_by": "worktree-poga-1"}
        if turn_budget is not None:
            rec["turn_budget"] = turn_budget
        return rec

    def test_the_spawn_command_carries_the_turn_budget_into_the_lane(self):
        """The launch half. The lane cannot enforce a budget it was never told."""
        cmd = session._dispatch_spawn_command(self.main, "D-test09", "WI-0001",
                                              turn_budget=25)
        self.assertIn("POGA_TURN_BUDGET=25", cmd)
        self.assertIn("POGA_DISPATCH=D-test09", cmd,
                      "the existing handles must survive the addition")

    def test_a_lane_with_no_budget_carries_no_handle(self):
        """Absence is its own state, never a silent zero (`declare-what-a-check-assumes`)
        — a lane with an empty budget handle must not read as a lane budgeted at 0."""
        cmd = session._dispatch_spawn_command(self.main, "D-test09", "WI-0001")
        self.assertNotIn("POGA_TURN_BUDGET", cmd)

    def test_the_turn_budget_is_recorded_on_the_dispatch_and_is_not_the_spawn_budget(self):
        rec = self._rec(["WI-0001"], budget=2, turn_budget=25)
        self.assertEqual(rec["budget"], 2)
        self.assertEqual(rec["turn_budget"], 25)

    def test_a_turn_is_counted_at_the_stop_boundary_not_per_tool_call(self):
        """A turn is one Stop event. The same `heartbeat` entry point also binds
        PreToolUse — which fires many times inside ONE turn — so counting beats instead
        of turns would burn a 25-turn budget inside a single working turn."""
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test09"}):
            for _ in range(3):
                session._turn_budget_count("sid-1", "PreToolUse")
            self.assertEqual(session._turn_budget_used("sid-1"), 0,
                             "tool calls are not turns")
            session._turn_budget_count("sid-1", "Stop")
            session._turn_budget_count("sid-1", "Stop")
            self.assertEqual(session._turn_budget_used("sid-1"), 2)

    def test_an_exhausted_budget_tells_the_lane_to_land_and_close(self):
        """What the budget DOES on exhaustion. The lane is told at the turn boundary to
        park per R3 and close — the already-shipped park-don't-block posture, reached by
        running out of turns rather than by running out of answers."""
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test09",
                                          "POGA_TURN_BUDGET": "2"}):
            self.assertEqual(session._turn_budget_notice("sid-2"), "")
            session._turn_budget_count("sid-2", "Stop")
            self.assertEqual(session._turn_budget_notice("sid-2"), "",
                             "under budget, the lane is not interrupted")
            session._turn_budget_count("sid-2", "Stop")
            notice = session._turn_budget_notice("sid-2")
        self.assertIn("turn budget", notice.lower())
        self.assertIn("merge", notice,
                      "the notice must name the verb that lands AND closes, or it is "
                      "an instruction with no way to carry it out")

    def _heartbeat(self, sid, payload):
        """Drive the real hook entry point, reading its stdin the way the runtime does."""
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        with mock.patch.object(session, "_read_hook_stdin",
                               return_value=dict(payload, session_id=sid)), \
                mock.patch.object(session, "_attention_clear"), \
                mock.patch.object(session, "_complete_lazy_start"), \
                mock.patch.object(session, "_dissolve_lane_checkpoint"), \
                mock.patch.object(session, "_record_touch"), \
                redirect_stdout(buf):
            try:
                session.cmd_heartbeat(argparse.Namespace())
            except SystemExit:
                pass
        return buf.getvalue()

    def test_the_notice_reaches_the_model_through_the_stop_decision(self):
        """THE DELIVERY CHANNEL, pinned because getting it wrong is silent. A `Stop` hook
        that merely prints feeds the transcript, not the model — the budget would be
        counted, the notice composed, and nobody would ever read it. `decision: block`
        is the channel whose `reason` actually arrives."""
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test09",
                                          "POGA_TURN_BUDGET": "1"}):
            out = self._heartbeat("sid-4", {"hook_event_name": "Stop"})
        self.assertTrue(out.strip(), "an exhausted budget must emit something")
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("turn budget", payload["reason"].lower())

    def test_an_already_blocked_stop_is_not_blocked_again(self):
        """THE LOOP GUARD, and it is the one way this feature could be worse than not
        having it: re-blocking every stop holds the lane open forever, burning exactly
        the tokens R2 exists to stop. Told once, then allowed to end."""
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-test09",
                                          "POGA_TURN_BUDGET": "1"}):
            self._heartbeat("sid-5", {"hook_event_name": "Stop"})
            out = self._heartbeat("sid-5", {"hook_event_name": "Stop",
                                            "stop_hook_active": True})
        self.assertNotIn("block", out)

    def test_a_hand_opened_lane_has_no_turn_budget(self):
        """The budget binds a lane nobody is watching. A session operator opened is his own,
        and is bounded by him."""
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with mock.patch.dict(os.environ, {"POGA_TURN_BUDGET": "1"}, clear=False):
            os.environ.pop("POGA_DISPATCH", None)
            session._turn_budget_count("sid-3", "Stop")
            session._turn_budget_count("sid-3", "Stop")
            self.assertEqual(session._turn_budget_notice("sid-3"), "")


class DispatchReadinessTest(DispatchBase):
    """WI-0288 R3 (4) — dispatch REFUSES an item with no acceptance criteria or with an
    open decision on it.

    This is the half that makes park-don't-block honest. R3 told a dispatched lane to
    decide rather than ask, and to park the item where it genuinely cannot; without a
    readiness gate that converts blocked lanes into parked items and the queue depth
    just moves. If a lane parks because the item was never answerable, dispatch should
    not have spawned it.

    THE REFUSAL IS A SKIP, NOT A CRASH. It joins `_dispatch_order`'s existing `skipped`
    bucket beside 'terminal' and 'claimed by', so it prints on the one approval surface
    the plan already has, and one unready item never costs the other nine their wave.
    """

    def _write(self, wid, notes, status="open", blocked=""):
        # `acceptance=False`: this class supplies the whole body itself, because the
        # presence or absence of criteria IS its subject.
        (self.main / "work-items" / f"{wid}-x.md").write_text(
            _wi_text(wid, "T", status, blocked, acceptance=False) + notes + "\n",
            encoding="utf-8")

    def test_an_item_with_no_acceptance_criteria_is_skipped_by_name(self):
        self._write("WI-0001", "Some prose about the problem and nothing testable.")
        ordered, held, skipped = session._dispatch_order(["WI-0001"])
        self.assertNotIn("WI-0001", ordered)
        self.assertIn("WI-0001", skipped)
        self.assertIn("acceptance", skipped["WI-0001"].lower(),
                      "the skip must say WHICH readiness test failed — a bare 'not "
                      "ready' sends the reader back to the item to guess")

    def test_an_item_with_acceptance_criteria_is_dispatchable(self):
        self._write("WI-0001", "ACCEPTANCE: the gate refuses a hand-picked number.")
        ordered, held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(ordered, ["WI-0001"])
        self.assertNotIn("WI-0001", skipped)

    def test_marks_are_declarations_not_mentions_in_prose(self):
        bodies = (
            ("ACCEPTANCE: it works.", ""),
            ("The report says the ACCEPTANCE: line was missing.", "no acceptance"),
            ("ACCEPTANCE: it works.\nThe report mentions OPEN DECISION in prose.", ""),
        )
        for body, reason in bodies:
            with self.subTest(body=body):
                actual = session._wi_readiness({"notes": body})
                if reason:
                    self.assertIn(reason, actual)
                else:
                    self.assertEqual(actual, "")

    def test_an_open_decision_is_skipped_even_with_acceptance_criteria(self):
        """Both halves bind independently. An item can be perfectly testable and still
        be waiting on a call nobody has made."""
        self._write("WI-0001", "ACCEPTANCE: it works.\n\nOPEN DECISION FOR operator: "
                               "session-start or session-end.")
        ordered, held, skipped = session._dispatch_order(["WI-0001"])
        self.assertNotIn("WI-0001", ordered)
        self.assertIn("decision", skipped["WI-0001"].lower())

    def test_all_marks_accept_line_prefixes_but_not_prose(self):
        for prefix in ("", "  ", "\t", "- ", "  * "):
            criteria = prefix + "acceptance: it works."
            with self.subTest(prefix=prefix):
                self.assertEqual(session._wi_readiness({"notes": criteria}), "")
            for mark in session.WI_OPEN_DECISION_MARKS:
                with self.subTest(prefix=prefix, mark=mark):
                    self.assertIn("open decision", session._wi_readiness(
                        {"notes": criteria + "\n" + prefix + mark + ": choose."}))
                    self.assertEqual(session._wi_readiness(
                        {"notes": criteria + "\n" + prefix + "The report mentions " + mark}), "")
            with self.subTest(prefix=prefix, receipt=True):
                self.assertIn("completion receipt", session._wi_readiness(
                    {"notes": prefix + "ACCEPTANCE MET: done.\nProse mentions ACCEPTANCE."}))
                self.assertEqual(session._wi_readiness(
                    {"notes": prefix + "ACCEPTANCE MET: done.\n" + criteria}), "")

    def test_a_completion_receipt_is_not_acceptance_criteria(self):
        """The trap a substring match walks straight into: 'ACCEPTANCE MET AND PINNED'
        is a record that the work is DONE, not a criterion for doing it. An item whose
        only acceptance text is a receipt is not ready — it is finished."""
        self._write("WI-0001", "ACCEPTANCE MET AND PINNED: closed by session ~93.")
        ordered, held, skipped = session._dispatch_order(["WI-0001"])
        self.assertIn("WI-0001", skipped)

    def test_readiness_reads_the_compiled_notes_not_just_the_file_body(self):
        """A lot of ACCEPTANCE text lives in appended note records rather than in the
        item file, and an item is no less ready for having been answered later."""
        self._write("WI-0001", "Prose with no criteria in the file body.")
        nd = self.main / "work-items" / "notes" / "WI-0001"
        nd.mkdir(parents=True)
        (nd / "20260906T1300Z-x.md").write_text(
            "ACCEPTANCE: the lane refuses an unready item.\n", encoding="utf-8")
        ordered, held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(ordered, ["WI-0001"])

    def test_an_unready_item_does_not_cost_its_siblings_the_wave(self):
        self._write("WI-0001", "no criteria here")
        self._write("WI-0002", "ACCEPTANCE: it works.")
        ordered, held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertEqual(ordered, ["WI-0002"])
        self.assertIn("WI-0001", skipped)



# --- WI-0376 -------------------------------------------------------------------
# Three clauses, one item. Clause 1's premise needed correcting before it could be built:
# the overlap check did not "infer overlap from titles", it did not EXIST. Nothing in the
# dispatch path compared titles, paths or anything else -- titles were read only for
# display -- so four `deploy/runner.py` items in a single wave of five was the expected
# output of the code, and the OPS-0007 waves were composed by hand from the files each
# item names. What is built here is the check, not a fix to one.

def _wi_files_text(wid, title, files_block, status="open", blocked=""):
    """A fixture item carrying a `FILES:` declaration, written verbatim so the wrapping
    and annotation cases are the REAL shapes from the store rather than tidied ones."""
    return (f"# {wid}: {title}\n\n- status: {status}\n- section: next\n"
            f"- blocked-by: {blocked}\n- group: \n- source: \n\n"
            f"ACCEPTANCE: the fixture's item is dispatchable.\n\n{files_block}\n")


class DeclaredPathsTest(DispatchBase):
    """Clause 1, the parse. Literal by design, for `_wi_readiness`'s reason: which files an
    item "really" touches is a judgment call, and a judgment call in code is unpredictable
    and different every time the model behind it changes. A line the author wrote is a
    declaration."""

    def _item(self, block):
        return {"id": "WI-0001", "title": "x", "notes": block}

    def test_a_single_line_declaration_is_read(self):
        p, prose = session._wi_declared_paths(
            self._item("FILES: deploy/runner.py, tests/test_deploy_runner.py."))
        self.assertEqual(p, {"deploy/runner.py", "tests/test_deploy_runner.py"})
        self.assertEqual(prose, [])

    def test_a_declaration_that_WRAPS_is_read_to_the_end(self):
        """Two of the four real declarations in the store run onto a second line. Reading
        only the marker line would silently drop half of each."""
        p, _ = session._wi_declared_paths(self._item(
            "FILES: curate/public_cut.py, curate/public_cut_manifest.json, sessionlib/lanes.py,\n"
            "tests/test_public_cut.py."))
        self.assertEqual(p, {"curate/public_cut.py", "curate/public_cut_manifest.json",
                             "sessionlib/lanes.py", "tests/test_public_cut.py"})

    def test_the_declaration_stops_at_the_next_uppercase_marker(self):
        """WI-0350's real file has `FILES:` and then `SEQUENCING:` two lines later. Reading
        to the next blank line alone would be enough there; reading to the next MARKER is
        what keeps a declaration with no blank line after it from swallowing its
        neighbour's prose as filenames."""
        p, prose = session._wi_declared_paths(self._item(
            "FILES: deploy/runner.py\n"
            "SEQUENCING: first in the serialized Theme D wave D2, after WI-0230."))
        self.assertEqual(p, {"deploy/runner.py"})
        self.assertEqual(prose, [])

    def test_a_prose_fragment_is_returned_as_prose_never_as_a_path(self):
        """WI-0350 really does declare `FILES: deploy/runner.py, the registry schema.` The
        fragment names something real that is not a path. Counting it would invent a file;
        dropping it silently would report the item as fully assessed with a third of its
        declaration discarded unread."""
        p, prose = session._wi_declared_paths(
            self._item("FILES: deploy/runner.py, the registry schema."))
        self.assertEqual(p, {"deploy/runner.py"})
        self.assertEqual(prose, ["the registry schema"])

    def test_an_annotated_path_keeps_the_path_and_drops_the_annotation(self):
        """Both of these are real entries in WI-0350's compiled notes. On a bare word test
        they read as unparseable prose, and a fully-declared item reports as partly
        assessed -- a warning printed about the one item that did the work."""
        p, prose = session._wi_declared_paths(self._item(
            "FILES: deploy/registry.json (example-app row), "
            "tests/test_deploy_runner.py (+15 tests)."))
        self.assertEqual(p, {"deploy/registry.json", "tests/test_deploy_runner.py"})
        self.assertEqual(prose, [])

    def test_an_extensionless_path_is_still_a_path(self):
        """WI-0383 declares LICENSE and NOTICE. A rule requiring a slash or a dot would be
        tighter and would throw both away."""
        p, _ = session._wi_declared_paths(
            self._item("FILES: LICENSE, NOTICE, bootstrap-kit/."))
        self.assertEqual(p, {"LICENSE", "NOTICE", "bootstrap-kit/"})

    def test_lowercase_files_declares_nothing(self):
        """MEASURED before choosing case-sensitivity: 4 items carry a line-initial `FILES:`
        and zero carry a lowercase one, so IGNORECASE costs nothing today. It is refused
        for what it would match later -- "files" is an everyday noun, and a declaration
        that can be made by accident is not a declaration."""
        p, prose = session._wi_declared_paths(
            self._item("files: deploy/runner.py, and that is the lot."))
        self.assertEqual((p, prose), (set(), []))

    def test_a_longer_word_starting_with_FILES_declares_nothing(self):
        p, prose = session._wi_declared_paths(
            self._item("FILESYSTEM: the layout is described in adr/0095."))
        self.assertEqual((p, prose), (set(), []))

    def test_several_declarations_across_the_compiled_notes_are_unioned(self):
        """An item's `notes` is the file body PLUS every appended note record, and a great
        deal of scope is added to an item after it is filed. WI-0350 is the real instance:
        its file body declares two entries and its compiled notes carry more."""
        p, _ = session._wi_declared_paths(self._item(
            "FILES: a/one.py.\n\nSome later note.\n\nFILES: b/two.py."))
        self.assertEqual(p, {"a/one.py", "b/two.py"})


class PathCollisionTest(unittest.TestCase):
    """Clause 1, the rule. Symmetric, so the answer cannot depend on which item the caller
    happened to look at first."""

    def test_the_same_file_collides(self):
        self.assertEqual(session._wi_path_collisions({"a.py"}, {"a.py"}), {"a.py"})

    def test_different_files_do_not(self):
        self.assertEqual(session._wi_path_collisions({"a.py"}, {"b.py"}), set())

    def test_a_directory_collides_with_anything_beneath_it(self):
        """WI-0382 declares `adr/` precisely because it will touch files inside it that it
        cannot enumerate in advance. A directory that only matched itself would let every
        one of those through."""
        self.assertEqual(session._wi_path_collisions({"adr/"}, {"adr/0001-x.md"}), {"adr/"})
        self.assertEqual(session._wi_path_collisions({"adr/0001-x.md"}, {"adr/"}), {"adr/"})

    def test_a_directory_does_not_collide_with_a_sibling_directory(self):
        self.assertEqual(session._wi_path_collisions({"adr/"}, {"habits/m.md"}), set())

    def test_an_item_declaring_nothing_collides_with_nothing(self):
        """The unassessed case must not read as "collides with everything" -- that would
        take dispatch out of service for the 52 of 55 live items that declare nothing."""
        self.assertEqual(session._wi_path_collisions(set(), {"a.py"}), set())


class OverlapSkipsTheLaterItemTest(DispatchBase):
    """Clause 1, wired. THE REPRODUCTION is `test_four_items_on_one_file...`: the shape the
    item names, driven through the real ordering pass."""

    def _write(self, wid, title, block, status="open"):
        (self.main / "work-items" / f"{wid}-x.md").write_text(
            _wi_files_text(wid, title, block, status), encoding="utf-8")

    def test_two_items_on_one_file_do_not_both_run(self):
        self._write("WI-0001", "Alpha", "FILES: deploy/runner.py.")
        self._write("WI-0002", "Beta", "FILES: deploy/runner.py, docs/b.md.")
        ordered, _held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertEqual(ordered, ["WI-0001"])
        self.assertIn("WI-0002", skipped)
        self.assertIn("collides with WI-0001", skipped["WI-0002"])
        self.assertIn("deploy/runner.py", skipped["WI-0002"])

    def test_four_items_on_one_file_yield_a_wave_of_one(self):
        """The measured defect: `dispatch` planned FOUR `deploy/runner.py` items into a
        single wave of five, because the cap and `blocked-by` were the only bounds there
        were. Pre-fix this returns all four in `ordered`."""
        for n, wid in enumerate(["WI-0001", "WI-0002", "WI-0003", "WI-0005"]):
            self._write(wid, f"runner {n}", f"FILES: deploy/runner.py, docs/x{n}.md.")
        ordered, _held, skipped = session._dispatch_order(
            ["WI-0001", "WI-0002", "WI-0003", "WI-0005"])
        self.assertEqual(ordered, ["WI-0001"])
        self.assertEqual(len(skipped), 3)
        for wid in ("WI-0002", "WI-0003", "WI-0005"):
            self.assertIn("collides with WI-0001", skipped[wid])

    def test_disjoint_items_both_run(self):
        """The negative control. A check that skips everything is not a check."""
        self._write("WI-0001", "Alpha", "FILES: a/one.py.")
        self._write("WI-0002", "Beta", "FILES: b/two.py.")
        ordered, _held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertEqual(ordered, ["WI-0001", "WI-0002"])
        self.assertEqual(skipped, {})

    def test_items_declaring_nothing_are_never_held_apart(self):
        """52 of the 55 live items declare nothing. If "unknown" collided, dispatch would
        plan a wave of one forever."""
        ordered, _held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertEqual(ordered, ["WI-0001", "WI-0002"])
        self.assertEqual(skipped, {})

    def test_first_declared_wins_in_the_OPERATORS_order_not_by_id(self):
        """It has to be some rule and it has to be stable. Picking by id would silently
        reorder a range the operator wrote deliberately."""
        self._write("WI-0001", "Alpha", "FILES: deploy/runner.py.")
        self._write("WI-0002", "Beta", "FILES: deploy/runner.py.")
        ordered, _held, skipped = session._dispatch_order(["WI-0002", "WI-0001"])
        self.assertEqual(ordered, ["WI-0002"])
        self.assertIn("collides with WI-0002", skipped["WI-0001"])

    def test_a_directory_declaration_holds_back_a_file_inside_it(self):
        self._write("WI-0001", "Alpha", "FILES: adr/.")
        self._write("WI-0002", "Beta", "FILES: adr/0099-thing.md.")
        ordered, _held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertEqual(ordered, ["WI-0001"])
        self.assertIn("adr/", skipped["WI-0002"])

    def test_a_dependent_of_a_collided_item_is_HELD_not_released(self):
        """Pass 2 reads `candidates`, and a collided item is not one. Releasing its
        dependent here would start the dependent ahead of the dependency it names -- the
        same argument the claimed-blocker case already makes."""
        self._write("WI-0001", "Alpha", "FILES: deploy/runner.py.")
        self._write("WI-0002", "Beta", "FILES: deploy/runner.py.")
        self._write("WI-0003", "Gamma needs Beta", "FILES: docs/g.md.", status="open")
        (self.main / "work-items" / "WI-0003-x.md").write_text(
            _wi_files_text("WI-0003", "Gamma needs Beta", "FILES: docs/g.md.",
                           blocked="WI-0002"), encoding="utf-8")
        ordered, held, skipped = session._dispatch_order(
            ["WI-0001", "WI-0002", "WI-0003"])
        self.assertIn("WI-0002", skipped)
        self.assertIn("WI-0003", held)
        self.assertEqual(held["WI-0003"], ["WI-0002"])


class UnassessedSetIsNamedTest(DispatchBase):
    """Clause 2. Everything above the line was planned as disjoint, and for 52 of the 55
    live items that disjointness rests on no evidence at all. Silence about them is not a
    clean bill."""

    def _plan(self, ids, **kw):
        ordered, held, skipped = session._dispatch_order(ids)
        rec = {"dispatch_id": "D-test", "snapshot_id": "S-test01",
               "cap": 5, "budget": 5, "surface": "Terminal"}
        rec.update(kw)
        return "\n".join(session._dispatch_render_plan(
            rec, ordered, held, skipped, [], []))

    def _write(self, wid, title, block, status="open"):
        (self.main / "work-items" / f"{wid}-x.md").write_text(
            _wi_files_text(wid, title, block, status), encoding="utf-8")

    def test_an_item_with_no_declaration_is_planned_AND_named(self):
        """Both halves. Skipping it instead would take dispatch out of service; saying
        nothing is the failure this clause exists to end."""
        out = self._plan(["WI-0001"])
        self.assertIn("WI-0001", out)
        self.assertIn("NOT assessed for file overlap", out)
        self.assertIn("no FILES: declaration", out)

    def test_the_line_counts_the_unassessed_against_the_planned(self):
        out = self._plan(["WI-0001", "WI-0002"])
        self.assertIn("(2 of 2", out)

    def test_a_fully_declared_item_is_not_named(self):
        """The negative control: a warning printed over every item is a warning nobody
        reads."""
        self._write("WI-0001", "Alpha", "FILES: a/one.py.")
        out = self._plan(["WI-0001"])
        self.assertNotIn("NOT assessed for file overlap", out)

    def test_a_PARTLY_assessed_item_is_named_with_the_token_that_was_dropped(self):
        """WI-0350's real declaration. Reporting it as fully assessed would discard the
        prose fragment unread; reporting it as wholly unassessed would throw away the
        path it did declare."""
        self._write("WI-0001", "Alpha", "FILES: deploy/runner.py, the registry schema.")
        out = self._plan(["WI-0001"])
        self.assertIn("partly assessed", out)
        self.assertIn("the registry schema", out)
        self.assertIn("1 path(s) read", out)

    def test_a_skipped_item_is_not_warned_about(self):
        """The warning is about items being PLANNED as disjoint. One that will not run is
        not being planned as anything."""
        self._write("WI-0001", "Alpha", "FILES: a/one.py.")
        self._write("WI-0002", "Beta", "FILES: a/one.py.")
        out = self._plan(["WI-0001", "WI-0002"])
        self.assertIn("collides with WI-0001", out)
        self.assertNotIn("NOT assessed for file overlap", out)


class BuiltButNotLandedTest(DispatchBase):
    """Clause 3. Built-but-held and unstarted are different states, and a plan that
    conflates them re-dispatches finished work: a second lane opens, redoes what is already
    written on a branch nobody landed, and the two then collide."""

    def _commit_on_lane(self, message, fname="x.py"):
        (self.lane / fname).write_text("work\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", message)

    def test_an_item_with_unlanded_commits_is_skipped_and_the_branch_is_named(self):
        self._commit_on_lane("fix(x): do the thing (WI-0001)")
        ordered, _held, skipped = session._dispatch_order(["WI-0001", "WI-0002"])
        self.assertIn("WI-0001", skipped)
        self.assertIn("BUILT BUT NOT LANDED", skipped["WI-0001"])
        self.assertIn("worktree-poga-1", skipped["WI-0001"])
        self.assertEqual(ordered, ["WI-0002"])

    def test_an_unstarted_item_is_not_skipped(self):
        """The distinction itself. Without this the test above would pass for a check that
        skipped everything."""
        self._commit_on_lane("fix(x): do the thing (WI-0001)")
        _ordered, _held, skipped = session._dispatch_order(["WI-0002"])
        self.assertEqual(skipped, {})

    def test_work_that_REACHED_the_trunk_by_another_route_is_not_held_back(self):
        """WI-0147. A lane whose work was recovered into a sibling and landed there carries
        DIFFERENT SHAs for identical patches, so a reachability test reports it unlanded
        forever and the item could never be dispatched again. `git cherry` asks by content."""
        self._commit_on_lane("fix(x): do the thing (WI-0001)")
        _git(self.main, "merge", "-q", "--no-ff", "-m", "land", "worktree-poga-1")
        _ordered, _held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(skipped, {}, "content on the trunk must not read as unlanded")

    def test_a_commit_that_does_not_name_the_item_reads_as_unstarted(self):
        """THE DECLARED ASSUMPTION, asserted rather than left in a docstring. The probe
        reads the item id out of the commit message, which is convention here and not an
        invariant. It fails in the direction that plans a lane which may duplicate work --
        today's behaviour -- rather than refusing to dispatch an item whose evidence was
        never written down."""
        self._commit_on_lane("fix(x): do the thing with no item id")
        _ordered, _held, skipped = session._dispatch_order(["WI-0001"])
        self.assertEqual(skipped, {})

    def test_the_probe_survives_a_repo_it_cannot_read(self):
        """Fail-open: a probe that cannot run must not take the dispatch down with it."""
        with mock.patch.object(session, "sh", side_effect=OSError("git is unavailable")):
            self.assertEqual(session._wi_unlanded_builders(), {})


class TheBriefedButIdleLaneIsNudgedTest(DispatchBase):
    """WI-0409 — `briefed` was treated as `working`, and for two whole lanes it was not.

    MEASURED, 2026-09-19, dispatch D-4d6df1. Both lanes spawned at 10:10:38 and reported
    `2 confirmed briefed`. Both ran their session-start, CLAIMED their item and announced
    the assignment in their own words. Then neither executed a single step of its work for
    nineteen minutes, while every signal the dispatcher reads said healthy.

    WHY NOTHING CAUGHT IT, which is the part worth pinning rather than the symptom. The
    confirm pass skipped any lane whose brief state was `briefed` — so the one combination
    it never examined was *briefed AND idle*, which is exactly what both lanes were. And
    the fallback probe could not have rescued it either: `_dispatch_lane_working` reads the
    `.pending-start` marker, the FIRST HEARTBEAT consumes that marker, and a heartbeat rides
    on any tool call — so claiming the item consumed it. From that instant the probe answers
    `True` for a lane that has done nothing.

    Two signals, both consumed by the startup turn, both reading healthy. The attention
    record is the one that does not: it is written when the session goes to the user for
    input and it ages instead of clearing.
    """

    DID = "D-idle1"
    _rec = BriefedNotJustSpawnedTest._rec
    _write_slot = BriefedNotJustSpawnedTest._write_slot

    def _waiting_on(self, wid, key="worktree-poga-1"):
        """An attention record shaped the way a lane waiting for input writes one.

        `expires_at` IS NOT OPTIONAL HERE and its absence cost the first run of these tests.
        `_coord_expired` treats a record with no readable TTL as expired — deliberately, so
        an unreadable record can never hold a resource forever — and `_coord_list` drops
        expired records, so a fixture without it writes a file the reader correctly refuses
        to see. The guard then reported "nobody is waiting" and the tests failed against a
        fix that was working."""
        import json
        d = session._coord_dir(session.ATTENTION_KIND, create=True)
        (d / f"{key}.json").write_text(json.dumps({
            "item": wid, "created_at": session._coord_iso(time.time()),
            "expires_at": time.time() + 3600,
            "message": "Claude is waiting for your input"}) + "\n", encoding="utf-8")

    def _expired_waiting_on(self, wid, key="worktree-poga-1"):
        """The same record, with its TTL already run out."""
        import json
        d = session._coord_dir(session.ATTENTION_KIND, create=True)
        (d / f"{key}.json").write_text(json.dumps({
            "item": wid, "created_at": session._coord_iso(time.time() - 7200),
            "expires_at": time.time() - 3600,
            "message": "Claude is waiting for your input"}) + "\n", encoding="utf-8")

    # ---- the defect ------------------------------------------------------------------

    def test_a_briefed_lane_that_is_waiting_for_input_is_nudged(self):
        self._write_slot("WI-0001", brief="briefed")
        self._waiting_on("WI-0001")
        rec = self._rec(["WI-0001"])
        # `_dispatch_lane_working` returns True here exactly as it did on the day: the
        # startup turn consumed the marker. The old code called that "left alone".
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux",
                               return_value=(True, "")) as nudge:
            lines = session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_called_once()
        blob = " ".join(lines)
        self.assertIn("BRIEFED BUT IDLE", blob)
        self.assertNotIn("left alone", blob)

    def test_the_nudge_is_not_the_brief(self):
        """A lane that already read its brief, claimed and announced must not be handed the
        brief again — that invites a second orientation pass and a second claim on work it
        already holds. What was missing is the beat, not the instruction."""
        self._write_slot("WI-0001", brief="briefed")
        self._waiting_on("WI-0001")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux",
                               return_value=(True, "")) as nudge:
            session._dispatch_confirm_briefs(rec, grace=0)
        sent = nudge.call_args.args[1]
        self.assertEqual(sent, session.DISPATCH_RESUME_PROMPT)
        self.assertIn("Do not re-read the brief", sent)
        # The real brief names the snapshot and the full assignment; the nudge must not.
        self.assertNotIn("S-test01", sent)

    # ---- the negative control, which matters more than the fix ------------------------

    def test_a_briefed_lane_that_is_NOT_waiting_is_never_typed_into(self):
        """The guard must stay silent on every healthy lane, which is almost all of them.

        This is the assertion that stops the fix becoming a dispatcher that talks over
        sessions it just started -- the failure mode of over-correcting here."""
        self._write_slot("WI-0001", brief="briefed")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as nudge:
            lines = session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_not_called()
        self.assertEqual(lines, [])
        self.assertEqual(rec["briefed"], 1)

    def test_an_attention_record_for_a_DIFFERENT_item_does_not_nudge_this_lane(self):
        """The join is on the item. A record belonging to some other lane entirely must not
        make this one look wedged, or a busy machine nudges everything."""
        self._write_slot("WI-0001", brief="briefed")
        self._waiting_on("WI-0002", key="worktree-poga-9")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as nudge:
            session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_not_called()

    def test_a_record_with_no_item_is_not_evidence_about_any_lane(self):
        import json
        self._write_slot("WI-0001", brief="briefed")
        d = session._coord_dir(session.ATTENTION_KIND, create=True)
        (d / "worktree-poga-3.json").write_text(
            json.dumps({"created_at": session._coord_iso(time.time())}) + "\n",
            encoding="utf-8")
        self.assertIs(session._dispatch_lane_awaiting_input("WI-0001"), False)

    # ---- three outcomes, never two ----------------------------------------------------

    def test_an_unreadable_attention_store_answers_cannot_tell_not_false(self):
        """`unknown` is a different fact from "nobody is waiting", and collapsing the two is
        a recurring failure."""
        with mock.patch.object(session, "_attention_state",
                               return_value=("unknown", {})):
            self.assertIsNone(session._dispatch_lane_awaiting_input("WI-0001"))

    def test_a_raising_attention_store_answers_cannot_tell(self):
        with mock.patch.object(session, "_attention_state",
                               side_effect=RuntimeError("store is gone")):
            self.assertIsNone(session._dispatch_lane_awaiting_input("WI-0001"))

    def test_cannot_tell_does_not_nudge(self):
        """A lane we cannot read is not a lane we may type into."""
        self._write_slot("WI-0001", brief="briefed")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_awaiting_input", return_value=None), \
             mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as nudge:
            session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_not_called()

    # ---- reporting and accounting -----------------------------------------------------

    def test_a_non_tmux_surface_says_it_cannot_nudge_rather_than_skipping(self):
        self._write_slot("WI-0001", brief="briefed")
        self._waiting_on("WI-0001")
        rec = self._rec(["WI-0001"], surface="Terminal")
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as nudge:
            lines = session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_not_called()
        blob = " ".join(lines)
        self.assertIn("BRIEFED BUT IDLE", blob)
        self.assertIn("SendMessage", blob)

    def test_a_nudged_lane_still_counts_as_briefed(self):
        """It WAS briefed — that is what distinguishes it from a lane that got no prompt.
        Dropping it out of the count would make the number fall for a lane that has just
        been repaired, at the moment an operator is reading it."""
        self._write_slot("WI-0001", brief="briefed")
        self._waiting_on("WI-0001")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux",
                               return_value=(True, "")), \
             mock.patch.object(session.time, "sleep"):
            session._dispatch_confirm_briefs(rec, grace=0)
        self.assertIn(rec["brief_states"]["WI-0001"], ("nudged", "nudge-unconfirmed"))
        self.assertEqual(rec["briefed"], 1)

    def test_an_expired_attention_record_does_not_nudge(self):
        """A record whose TTL has run out is not a lane that is waiting now — and the reader
        already drops it. Pinned because the first version of these fixtures omitted the TTL
        entirely, which made every record look expired and every lane look healthy: the same
        silent direction the defect itself failed in."""
        self._write_slot("WI-0001", brief="briefed")
        self._expired_waiting_on("WI-0001")
        rec = self._rec(["WI-0001"])
        with mock.patch.object(session, "_dispatch_lane_working", return_value=True), \
             mock.patch.object(session, "_dispatch_rebrief_tmux") as nudge:
            session._dispatch_confirm_briefs(rec, grace=0)
        nudge.assert_not_called()

    def test_every_nudge_state_is_in_the_one_briefed_tuple(self):
        """The count is derived in two places. A state added to one and not the other
        prints a numerator that disagrees with itself."""
        for state in ("nudged", "nudge-unconfirmed"):
            with self.subTest(state=state):
                self.assertIn(state, session.DISPATCH_BRIEFED_STATES)


if __name__ == "__main__":
    unittest.main()
