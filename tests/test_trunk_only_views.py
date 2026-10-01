"""WI-0299 D5 — generated views are NEVER merged; the trunk regenerates them.

`ROADMAP.md`, the compiled `session-handoff.md` and `STATUS.md` are trunk-only compiled
views ([ADR-0056](adr/0056-session-branch-gated-trunk.md),
[ADR-0105](adr/0105-roadmap-is-a-compiled-trunk-only-view.md) D4: *"Lanes never write
ROADMAP.md"*). A lane may hold a copy of one — the harness itself writes them — but that
copy is a render of the lane's own tree, and a lane is by construction behind the trunk.

TWO INCIDENTS, ONE PROPERTY.

  * **WI-0260** — `poga work claim WI-0246` in lane `worktree-poga-27` re-rendered
    `ROADMAP.md` and auto-committed it (`1da47e4`, 1 insertion / 63 deletions), replacing
    the compiled `## Recently shipped — outcomes` region — FIVE real outcome entries — with
    the empty placeholder. What saved the trunk was a REFUSAL, not a guard: main had
    independently touched the same file, so the rebase conflicted. *"Had the trunk not
    touched ROADMAP.md, the rebase would have been clean and the gutted section would have
    landed."* `TrunkRenderWinsTest` is that unprotected case.
  * **WI-0262** — the same lane copy, one land later, blocking the lane's own land on a
    file the trunk regenerates seconds afterwards.

The fix is not a better conflict policy — it is that the lane never carries the divergence
into the rebase at all. Before the rebase the land RESTORES each trunk-only view to the
trunk's own content, so the replayed history contains no change to a file the lane may not
author, and the trunk's compile regenerates them on the landed tip afterwards.

**Restore, not delete.** Removing the files would land a trunk with no `ROADMAP.md` for the
window before the recompile, and the renderer splices into an existing file — it cannot
render into one that is gone. `RestoreNotDeleteTest` pins that direction.

The other half is the writer: `cmd_wi_render` had no lane guard at all, which is how
WI-0260 happened in the first place. `RenderIsTrunkOnlyTest` pins the refusal, and
`OneListTest` pins that the drop step, the resolve policy and the harness-owned classifier
all read ONE list rather than three copies that can drift apart.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from test_worktree_lane import WorktreeLaneBase, _git  # noqa: E402

GIT = shutil.which("git")

#: A roadmap whose Recently-shipped region carries real outcomes — the five bullets
#: WI-0260 watched a lane delete, in the smallest form that still reproduces.
GOOD_ROADMAP = f"""# Roadmap

## Now

Hand-authored prose that only a human writes.

## Recently shipped — outcomes

{session.WI_GEN_BEGIN}
- 2026-09-03 — the land gate serialized
- 2026-09-02 — lanes stopped colliding on views
{session.WI_GEN_END}
"""

#: What a behind-the-trunk lane renders over it: the placeholder, because the lane's own
#: `sessions/journal/` does not carry the outcomes the trunk's does.
GUTTED_ROADMAP = f"""# Roadmap

## Now

Hand-authored prose that only a human writes.

## Recently shipped — outcomes

{session.WI_GEN_BEGIN}
_No session has recorded an `## Outcome` yet._
{session.WI_GEN_END}
"""


@unittest.skipUnless(GIT, "git not available")
class TrunkOnlyViewBase(WorktreeLaneBase):
    def setUp(self):
        super().setUp()
        # Give both trees a roadmap. The fixture repo has none by default, and the
        # defect is about a file that exists on both sides.
        (self.main / "ROADMAP.md").write_text(GOOD_ROADMAP, encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "roadmap")
        _git(self.lane, "merge", "--ff-only", "-q", "main")

    def _main_roadmap(self):
        return (self.main / "ROADMAP.md").read_text(encoding="utf-8")

    def _gut_the_lane_roadmap(self):
        """Exactly WI-0260's commit: the harness's own re-render, on the lane branch."""
        (self.lane / "ROADMAP.md").write_text(GUTTED_ROADMAP, encoding="utf-8")
        _git(self.lane, "add", "--", "ROADMAP.md")
        _git(self.lane, "commit", "-qm",
             "docs(roadmap): re-render generated sections from the work-item store")

    def _land(self):
        return session._land_worktree_lane(None, None, "9.9.9",
                                           commit_msg="close lane", push=False)


class TrunkRenderWinsTest(TrunkOnlyViewBase):
    """WI-0260's unprotected case: the trunk has NOT touched the roadmap, so nothing
    conflicts and today the lane's gutted render lands clean."""

    def test_a_lane_carrying_a_gutted_roadmap_does_not_land_it(self):
        self._install_main_compiler()
        self._point_session_at_lane()
        self._gut_the_lane_roadmap()
        self._write_lane_journal("20260906T1500Z-devbox-aaaa", "lane work")
        self.assertTrue(self._land(), "the land must succeed — this is not a refusal")
        self.assertIn("the land gate serialized", self._main_roadmap(),
                      "a lane's stale render replaced the trunk's compiled "
                      "Recently-shipped section — WI-0260, with the refusal that "
                      "accidentally saved the trunk removed")
        self.assertNotIn("_No session has recorded", self._main_roadmap(),
                         "the placeholder reached the trunk")

    def test_authored_prose_in_a_lane_is_never_silently_discarded(self):
        """THE CONTROL THAT MAKES THE RESTORE HONEST, and it caught a real over-reach.

        A first cut of `_restore_trunk_views` took the trunk side of the WHOLE roadmap.
        That is the interim [ADR-0105](adr/0105-roadmap-is-a-compiled-trunk-only-view.md)
        rejected on measurement — *"it silently deletes authored prose on every land, with
        no second copy to recover it from"* — and the existing
        `RoadmapResolveTest.test_a_lane_that_edited_the_hand_authored_now_still_refuses_by_name`
        went red on it immediately.

        A lane editing `## Now` is out of contract (ADR-0105 D4 says lanes never write this
        file). Out of contract is an argument for someone being TOLD, never for the bytes
        being dropped. So the restore is confined to the generated regions, and prose
        survives — it lands, or it conflicts and is refused by name."""
        self._install_main_compiler()
        self._point_session_at_lane()
        (self.lane / "ROADMAP.md").write_text(
            GOOD_ROADMAP.replace("Hand-authored prose that only a human writes.",
                                 "a lane rewrote Now"), encoding="utf-8")
        _git(self.lane, "add", "--", "ROADMAP.md")
        _git(self.lane, "commit", "-qm", "lane touches Now")
        self._write_lane_journal("20260906T1500Z-devbox-bbbb", "lane work")
        self.assertTrue(self._land())
        self.assertIn("a lane rewrote Now", self._main_roadmap(),
                      "the land silently deleted prose that existed in exactly one place")

    def test_the_restore_says_when_it_held_back(self):
        """A view left alone must be visible in the receipt. A silent non-restore and a
        silent restore look identical from outside, and only one of them is right."""
        self._point_session_at_lane()
        (self.lane / "ROADMAP.md").write_text(
            GOOD_ROADMAP.replace("Hand-authored prose that only a human writes.",
                                 "a lane rewrote Now"), encoding="utf-8")
        _git(self.lane, "add", "--", "ROADMAP.md")
        _git(self.lane, "commit", "-qm", "lane touches Now")
        lines = session._restore_trunk_views("main")
        self.assertTrue(any("ROADMAP.md" in ln and "authored prose" in ln for ln in lines),
                        f"no receipt naming the held-back view: {lines}")

    def test_the_lanes_history_nets_to_no_change_in_a_trunk_only_view(self):
        """`AcceptLagTest` pins the easy half — a view the lane never committed is not
        folded into the close commit. This is the case it cannot cover: the lane already
        HAS a commit rewriting the view, made mid-session by the harness itself, and that
        commit cannot be un-made without rewriting history (which the destructive-ops
        guard refuses — WI-0262 says so in as many words).

        So the invariant is stated over the RANGE, not over one commit: whatever the lane's
        history says on the way through, the trunk's trunk-only views are unchanged by the
        land. That is the property both incidents are about, and it is the one a per-commit
        assertion would have measured only by accident."""
        self._install_main_compiler()
        self._point_session_at_lane()
        before = subprocess.run([GIT, "-C", str(self.main), "rev-parse", "main"],
                                capture_output=True, text=True).stdout.strip()
        self._gut_the_lane_roadmap()
        did = "20260906T1500Z-devbox-cccc"
        self._write_lane_journal(did, "lane work")
        self.assertTrue(self._land())
        net = subprocess.run(
            [GIT, "-C", str(self.main), "diff", "--name-only", before, "main",
             "--", "ROADMAP.md"], capture_output=True, text=True).stdout.split()
        self.assertEqual(net, [],
                         "the land changed a trunk-only view — the lane authored "
                         "ROADMAP.md and it reached the trunk (WI-0260)")
        landed = subprocess.run(
            [GIT, "-C", str(self.main), "diff", "--name-only", before, "main"],
            capture_output=True, text=True).stdout.split()
        self.assertIn(f"sessions/journal/{did}.md", landed,
                      "the lane's real work must still land — this is a restore, not a "
                      "refusal")


class RestoreNotDeleteTest(TrunkOnlyViewBase):
    """The drop is a RESTORE to the trunk's content, never a removal.

    A land that deleted the views would leave the trunk without a `ROADMAP.md` between the
    CAS and the recompile — and `cmd_wi_render` splices into an existing file, so the
    regeneration that is supposed to heal it would find nothing to write into. The failure
    would be a trunk that permanently loses its human-facing view, which is strictly worse
    than the defect being fixed."""

    def test_the_trunk_still_has_every_view_after_the_land(self):
        self._install_main_compiler()
        self._point_session_at_lane()
        self._gut_the_lane_roadmap()
        self._write_lane_journal("20260906T1500Z-devbox-dddd", "lane work")
        self.assertTrue(self._land())
        for rel in ("ROADMAP.md", "STATUS.md"):
            self.assertTrue((self.main / rel).is_file(),
                            f"the land removed {rel} from the trunk instead of "
                            f"restoring it")

    def test_a_lane_that_never_touched_a_view_still_lands(self):
        """The restore must be a no-op when there is nothing to restore — it cannot
        manufacture an empty commit or fail a land that was always clean."""
        self._install_main_compiler()
        self._point_session_at_lane()
        self._stage_lane_work()
        self._write_lane_journal("20260906T1500Z-devbox-eeee", "lane work")
        self.assertTrue(self._land())
        self.assertIn("the land gate serialized", self._main_roadmap())


class RenderIsTrunkOnlyTest(TrunkOnlyViewBase):
    """WI-0260's other half: the WRITER. `cmd_wi_render` wrote and committed
    `ROADMAP.md` in whatever tree it was standing in, with no lane check — `run_compile`
    no-ops in a lane, but `session.py wi-render` / `poga work render` reach the renderer
    directly and had no guard of their own."""

    def test_wi_render_writes_nothing_in_a_lane(self):
        import argparse
        self._point_session_at_lane()
        before = (self.lane / "ROADMAP.md").read_text(encoding="utf-8")
        session.cmd_wi_render(argparse.Namespace())
        self.assertEqual((self.lane / "ROADMAP.md").read_text(encoding="utf-8"), before,
                         "wi-render rewrote a trunk-only view from inside a lane")

    def test_wi_render_commits_nothing_in_a_lane(self):
        import argparse
        self._point_session_at_lane()
        head = subprocess.run([GIT, "-C", str(self.lane), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        session.cmd_wi_render(argparse.Namespace())
        self.assertEqual(
            subprocess.run([GIT, "-C", str(self.lane), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip(), head,
            "wi-render auto-committed in a lane — WI-0260's commit 1da47e4 exactly")


class OneListTest(unittest.TestCase):
    """ONE list, three readers. WI-0299 asks for *'the generated policy names every view
    from one list that compile and merge --resolve share'* — before this there were four
    independent enumerations (`_generated_view_names`, `_harness_owned`, the recompile's
    own `names`, and the layout defaults), and the roadmap was in some and not others."""

    def setUp(self):
        self._cfg_raw = session.CFG_RAW
        session.CFG_RAW = {}          # the federation shape, from LAYOUT_DEFAULTS
        self.addCleanup(lambda: setattr(session, "CFG_RAW", self._cfg_raw))

    def test_the_list_names_all_three_trunk_only_views(self):
        views = set(session._trunk_only_views())
        self.assertEqual(views, {"session-handoff.md", "STATUS.md", "ROADMAP.md"})

    def test_harness_owned_reads_the_same_list(self):
        for rel in session._trunk_only_views():
            self.assertTrue(session._harness_owned(rel),
                            f"{rel} is a trunk-only view the harness writes, but the "
                            f"dirt classifier does not own it")

    def test_the_generated_view_set_is_the_list_minus_the_roadmap(self):
        """The narrowing stays argued rather than becoming an accident of a second list.
        `_lane_adds_nothing_the_trunk_lacks` DELETES a lane branch whose only content is a
        member of `_generated_view_names()`, and the roadmap is generated only in regions —
        so a lane holding a real `## Now` edit must never read as empty. The roadmap earns
        its place in the resolve policy per-conflict instead."""
        names = {pathlib.Path(r).name for r in session._trunk_only_views()}
        self.assertEqual(session._generated_view_names(), names - {"ROADMAP.md"})

    def test_the_resolve_policy_covers_every_view_on_the_list(self):
        for rel in session._trunk_only_views():
            if pathlib.Path(rel).name == "ROADMAP.md":
                continue          # conditional by design — see _roadmap_side_is_render_only
            self.assertTrue(session._conflict_is_resolvable(rel, "generated"),
                            f"{rel} is on the trunk-only list but the generated resolve "
                            f"policy would refuse on it (WI-0262's shape)")


if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
