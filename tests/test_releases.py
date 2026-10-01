"""Release records and major declaration manifests (WI-0044).

Implements [ADR-0078] (sets are planned, releases are harvested), [ADR-0080] (the project
version lives here), [ADR-0081] (a major's number and theme are declared, never computed
from labels) and [ADR-0084] (a major SHIPS when its promise is complete).

The properties worth defending, in rough order of how expensive they are to get wrong:

  - **No label and no override can invent a major.** `release-declare` is the only door
    into a major's existence; `_wi_derive_bump` returns minor or patch and nothing else,
    and `--override-bump major` is refused in the function, not just in argparse.
  - **A major arrives by COUNTING, not by asking.** It ships exactly when every ruled-in
    item has shipped (ADR-0084 D1) — there is no `--ship-major` flag, because a milestone
    shippable on demand could be declared complete while incomplete.
  - **Nothing waits for its milestone, and nothing is forgotten either.** A ruled-in item
    that finishes early ships at its own size (D2), and the major's record still accounts
    for it under `## Previously shipped in this promise` (D4). Both halves or the
    milestone silently shrinks.
  - **A release is harvested, and a plan never gates it.** Whatever is closed-and-unstamped
    ships; a declared item that did not land is reported as SLIPPED rather than blocking.
  - **The record cannot quietly rewrite the plan.** If ten were promised and eight landed,
    the two are named. A record listing only what shipped is literally true and quietly
    false.
  - **Refusals over guesses.** An unlabelled item refuses the whole computation; an
    override with no reason is refused; a manifest is frozen once declared; a record is
    immutable.

stdlib unittest: python3 -m unittest tests.test_releases
"""

import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def _args(**kw):
    kw.setdefault("no_tag", True)  # This suite tests record-only harvesting.
    kw.setdefault("dry_run", False)
    kw.setdefault("override_bump", None)
    kw.setdefault("reason", "")
    kw.setdefault("status", False)
    kw.setdefault("ship_major", False)
    return type("A", (), kw)()


class ReleaseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir()
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, wid, **kw):
        it = {"id": wid, "title": f"Item {wid}", "status": "open", "section": "next",
              "blocked_by": [], "group": "", "source": "", "impact": "", "migration": "",
              "version": "", "notes": ""}
        it.update(kw)
        session._wi_write_item(it)
        return it

    def seed(self, version="6.0.0"):
        """A prior release, so a cut has something to bump from."""
        d = session._rel_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{version}.md").write_text(f"# seed\n\n- version: {version}\n", encoding="utf-8")

    def run_cmd(self, fn, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(args)
        return buf.getvalue()

    def read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)

    def record(self, version):
        return (session._rel_dir() / f"{version}.md").read_text(encoding="utf-8")


class CurrentVersionTest(ReleaseBase):
    def test_no_records_is_none_not_zero(self):
        """"Never released" and "released 0.0.0" are different facts."""
        self.assertIsNone(session._rel_current())

    def test_highest_record_wins_not_alphabetical(self):
        self.seed("6.9.0")
        self.seed("6.10.0")
        self.assertEqual(session._rel_current(), (6, 10, 0))

    def test_a_manifest_is_not_a_release(self):
        """A declared major that has not shipped must never read as a shipped version."""
        self.seed("6.0.0")
        d = session._rel_dir()
        (d / "7.0.0-manifest.md").write_text("- version: 7.0.0\n", encoding="utf-8")
        self.assertEqual(session._rel_current(), (6, 0, 0))


class HarvestTest(ReleaseBase):
    def test_only_closed_and_unstamped_items_are_harvested(self):
        self.item("WI-0001", status="done", impact="fix")
        self.item("WI-0002", status="open", impact="fix")
        self.item("WI-0003", status="done", impact="fix", version="5.0.0")
        self.assertEqual([it["id"] for it in session._rel_harvest()], ["WI-0001"])

    def test_superseded_work_never_ships(self):
        """Cancelled work must not inflate a release, nor drag its label into the bump."""
        self.item("WI-0001", status="superseded", impact="feature",
                  notes="Folded into WI-0002.")
        self.assertEqual(session._rel_harvest(), [])


class CutTest(ReleaseBase):
    def test_cut_writes_a_record_and_stamps_every_shipped_item(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.item("WI-0002", status="done", impact="feature")
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 6.1.0", out)
        self.assertEqual(self.read("WI-0001")["version"], "6.1.0")
        self.assertEqual(self.read("WI-0002")["version"], "6.1.0")
        body = self.record("6.1.0")
        self.assertIn("- WI-0001", body)
        self.assertIn("- WI-0002", body)

    def test_only_fixes_is_a_patch(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.run_cmd(session.cmd_release_cut, _args())
        self.assertEqual(self.read("WI-0001")["version"], "6.0.1")

    def test_an_unlabelled_item_refuses_the_whole_cut(self):
        self.seed()
        self.item("WI-0001", status="done", impact="feature")
        self.item("WI-0002", status="done")           # unlabelled
        with self.assertRaises(SystemExit):
            self.run_cmd(session.cmd_release_cut, _args())
        # Nothing was stamped and no record was written — a refusal is total.
        self.assertEqual(self.read("WI-0001")["version"], "")
        self.assertFalse((session._rel_dir() / "6.1.0.md").exists())

    def test_dry_run_writes_nothing_and_stamps_nothing(self):
        self.seed()
        self.item("WI-0001", status="done", impact="feature")
        out = self.run_cmd(session.cmd_release_cut, _args(dry_run=True))
        self.assertIn("would cut 6.1.0", out)
        self.assertEqual(self.read("WI-0001")["version"], "")
        self.assertFalse((session._rel_dir() / "6.1.0.md").exists())

    def test_nothing_to_release_exits_rather_than_cutting_an_empty_version(self):
        self.seed()
        with self.assertRaises(SystemExit):
            self.run_cmd(session.cmd_release_cut, _args())

    def test_a_first_cut_with_no_prior_record_refuses(self):
        """Bumping from a version nobody declared would invent the sequence's origin."""
        self.item("WI-0001", status="done", impact="fix")
        with self.assertRaises(SystemExit):
            self.run_cmd(session.cmd_release_cut, _args())

    def test_a_second_cut_targets_the_next_version_never_the_same_one(self):
        """The path is self-avoiding, which is why records are safe to call immutable.

        The target version is derived from the highest record on disk, so a second cut
        cannot land on the first one's number. The `already exists` guard in `cmd_release_cut`
        is therefore a BACKSTOP for the concurrent case (two lanes between the read and the
        write), not something a single session can reach — recorded here rather than
        dressed up as a reachable scenario with a contrived fixture.
        """
        self.seed()
        self.item("WI-0001", status="done", impact="feature")
        self.run_cmd(session.cmd_release_cut, _args())
        self.item("WI-0002", status="done", impact="feature")
        self.run_cmd(session.cmd_release_cut, _args())
        self.assertTrue((session._rel_dir() / "6.1.0.md").exists())
        self.assertTrue((session._rel_dir() / "6.2.0.md").exists())
        self.assertEqual(self.read("WI-0001")["version"], "6.1.0")   # not re-stamped
        self.assertEqual(self.read("WI-0002")["version"], "6.2.0")


class NeverAMajorTest(ReleaseBase):
    def test_derivation_cannot_reach_major_however_many_features(self):
        self.seed()
        for i in range(1, 12):
            self.item(f"WI-{i:04d}", status="done", impact="feature")
        self.run_cmd(session.cmd_release_cut, _args())
        self.assertTrue((session._rel_dir() / "6.1.0.md").exists())
        self.assertFalse((session._rel_dir() / "7.0.0.md").exists())

    def test_override_cannot_be_used_to_manufacture_a_major(self):
        """Enforced in the function, not only in argparse.

        If an override could produce a major, "declared, not derived" would hold only by
        politeness — and the CLI is not the only caller. Called directly here, exactly as
        a wrapper script or a future release harness would.
        """
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        with self.assertRaises(SystemExit):
            self.run_cmd(session.cmd_release_cut,
                         _args(override_bump="major", reason="I really want one"))
        self.assertFalse((session._rel_dir() / "7.0.0.md").exists())
        self.assertEqual(self.read("WI-0001")["version"], "")


class OverrideTest(ReleaseBase):
    def test_override_without_a_reason_is_refused(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        with self.assertRaises(SystemExit):
            self.run_cmd(session.cmd_release_cut, _args(override_bump="minor"))

    def test_override_with_a_reason_is_recorded_in_the_record(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.run_cmd(session.cmd_release_cut,
                     _args(override_bump="minor", reason="ships a capability the labels miss"))
        body = self.record("6.1.0")
        self.assertIn("override reason: ships a capability the labels miss", body)
        self.assertIn("(overridden)", body)


class DeclareTest(ReleaseBase):
    def declare(self, version="7.0.0", theme="Members stop inheriting our shape",
                items="", target="", done=""):
        return self.run_cmd(
            session.cmd_release_declare,
            _args(version=version, theme=theme, items=items, target=target, done=done))

    def test_declaring_writes_a_manifest(self):
        self.seed()
        self.item("WI-0001")
        out = self.declare(items="WI-0001", target="2026-09-30")
        self.assertIn("declared 7.0.0", out)
        body = (session._rel_dir() / "7.0.0-manifest.md").read_text(encoding="utf-8")
        self.assertIn("- items: WI-0001", body)
        self.assertIn("- target: 2026-09-30", body)

    def test_a_manifest_is_frozen_once_declared(self):
        self.seed()
        self.item("WI-0001")
        self.declare(items="WI-0001")
        with self.assertRaises(SystemExit):
            self.declare(items="WI-0001")

    def test_an_unknown_id_is_refused(self):
        self.seed()
        with self.assertRaises(SystemExit):
            self.declare(items="WI-9999")

    def test_an_empty_theme_is_refused(self):
        """A major with no promise in it is the failure the theme exists to prevent."""
        self.seed()
        with self.assertRaises(SystemExit):
            self.declare(theme="   ")

    def test_a_version_behind_the_current_one_is_refused(self):
        self.seed("6.0.0")
        with self.assertRaises(SystemExit):
            self.declare(version="5.0.0")


class DeclaredMajorCutTest(ReleaseBase):
    """ADR-0084 D1 — the major fires when the promise is COMPLETE, counted at the cut.

    Every ruled-in item is done here, so this harvest *is* 7.0.0. There is no flag; the
    condition is arithmetic."""

    def setUp(self):
        super().setUp()
        self.seed()
        self.item("WI-0001", status="done", impact="feature", migration="yes")
        self.item("WI-0002", status="done", impact="fix")
        self.item("WI-0003", status="done", impact="feature")
        self.run_cmd(session.cmd_release_declare,
                     _args(version="7.0.0", theme="Members stop inheriting our shape",
                           items="WI-0001,WI-0002,WI-0003", target="2026-09-30", done=""))

    def test_a_complete_promise_ships_the_major(self):
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 7.0.0", out)
        self.assertIn("(promise complete)", self.record("7.0.0"))
        self.assertEqual(self.read("WI-0001")["version"], "7.0.0")

    def test_the_major_says_the_promise_was_kept(self):
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("PROMISE COMPLETE", out)
        self.assertIn("all 3 ruled-in item(s) accounted for", out)

    def test_nothing_slipped_because_nothing_could(self):
        """D6 — a major cannot ship with an item outstanding, so the section is empty by
        construction. Dropping an item is a manifest amendment, not a silent line."""
        self.run_cmd(session.cmd_release_cut, _args())
        # Scope to the section itself — splitting on the heading alone runs straight into
        # `## Migration required`, whose ids would read as slipped ones.
        section = self.record("7.0.0").split("## Slipped", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("- WI-", section)
        self.assertIn("Nothing declared for this release was left out", section)

    def test_the_original_target_is_kept_beside_the_actual_ship_date(self):
        """A target that can be moved as it approaches creates no pressure at all."""
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("7.0.0")
        self.assertIn("- target (original): 2026-09-30", body)
        self.assertIn("- shipped: ", body)

    def test_migration_items_are_called_out(self):
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("7.0.0")
        section = body.split("## Migration required", 1)[1]
        self.assertIn("WI-0001", section)
        self.assertNotIn("WI-0002", section)

    def test_a_superseded_ruled_in_item_stalls_the_major_by_name(self):
        """Cancelled work never shipped, so it must not complete a promise by
        abandonment — and the stall has to say so, with the way out, or the milestone
        just never arrives and nobody knows why."""
        self.item("WI-0003", status="superseded", impact="feature")
        out = self.run_cmd(session.cmd_release_cut, _args(dry_run=True))
        self.assertNotIn("would cut 7.0.0", out)
        self.assertIn("WI-0003", out)
        self.assertIn("superseded", out)
        self.assertIn("amend the manifest", out)


class NothingWaitsForItsMilestoneTest(ReleaseBase):
    """ADR-0084 D2 + D4 — the case that forced the whole decision.

    WI-0027 closed eight items ahead of the rest of 7.0.0. Holding it back protects a
    document at the cost of finished work; letting it ship and forgetting it shrinks the
    milestone. D2 ships it, D4 remembers it."""

    def setUp(self):
        super().setUp()
        self.seed("6.1.0")
        self.item("WI-0027", status="done", impact="fix")     # ruled in, done early
        self.item("WI-0033", status="open", impact="feature")  # ruled in, still in flight
        self.run_cmd(session.cmd_release_declare,
                     _args(version="7.0.0", theme="poga runs independent of LLM model",
                           items="WI-0027,WI-0033", target="2026-08-10", done=""))

    def test_a_ruled_in_item_ships_early_at_its_own_size(self):
        """A fix is a fix. Being promised to 7.0.0 confers no hold and no special size."""
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 6.1.1", out)
        self.assertEqual(self.read("WI-0027")["version"], "6.1.1")
        self.assertFalse((session._rel_dir() / "7.0.0.md").exists())

    def test_the_note_counts_what_is_outstanding(self):
        out = self.run_cmd(session.cmd_release_cut, _args(dry_run=True))
        self.assertIn("1 of 2 ruled-in item(s) still outstanding", out)

    def test_the_major_accounts_for_the_item_that_shipped_earlier(self):
        """The load-bearing half. Without it 7.0.0's record lists ONE item having
        promised two, and nothing anywhere says where the other went."""
        self.run_cmd(session.cmd_release_cut, _args())            # 6.1.1 takes WI-0027
        self.item("WI-0033", status="done", impact="feature")     # the last one lands
        out = self.run_cmd(session.cmd_release_cut, _args())      # ⇒ promise complete

        self.assertIn("cut 7.0.0", out)
        body = self.record("7.0.0")
        section = body.split("## Previously shipped in this promise", 1)[1]
        self.assertIn("- WI-0027 — 6.1.1", section)
        self.assertIn("(1 here, 1 earlier)", out)

    def test_the_last_item_rolls_the_major_even_when_it_is_only_a_fix(self):
        """Six of the real 7.0.0's nine items are fixes, so this is the likely path, not
        an edge case. The promise completing is what rolls the number — not the size of
        whatever happened to land last."""
        self.run_cmd(session.cmd_release_cut, _args())
        self.item("WI-0033", status="done", impact="fix")
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 7.0.0", out)


class DeclaredMajorDoesNotCaptureOrdinaryWorkTest(ReleaseBase):
    """The defect operator caught by asking the obvious question: wouldn't finished work bump
    the minor?

    `cmd_release_cut` used to read `if manifest:` and unconditionally become the declared
    major. "Declared, not derived" is about how a major's number is CHOSEN — a human names
    a milestone — not about which harvest ships it, and collapsing the two froze the whole
    version sequence for the length of a declaration.
    """

    def setUp(self):
        super().setUp()
        self.seed()
        self.item("WI-0100", status="done", impact="feature")   # unrelated finished work
        self.item("WI-0200", status="open", impact="feature")   # the milestone's own work,
        self.item("WI-0201", status="open", impact="feature")   # still in flight
        self.run_cmd(session.cmd_release_declare,
                     _args(version="7.0.0", theme="poga runs independent of the model",
                           items="WI-0200,WI-0201", target="2026-08-10", done=""))

    def test_ordinary_work_still_bumps_the_minor_while_a_major_is_outstanding(self):
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 6.1.0", out)
        self.assertEqual(self.read("WI-0100")["version"], "6.1.0")

    def test_the_major_number_is_not_consumed_by_unrelated_work(self):
        self.run_cmd(session.cmd_release_cut, _args())
        self.assertFalse((session._rel_dir() / "7.0.0.md").exists(),
                         "the milestone must still be unshipped")
        self.assertTrue((session._rel_dir() / "7.0.0-manifest.md").exists())

    def test_an_ordinary_cut_does_not_report_the_majors_items_as_slipped(self):
        """They were never promised by THIS release."""
        self.run_cmd(session.cmd_release_cut, _args())
        section = self.record("6.1.0").split("## Slipped", 1)[1]
        self.assertNotIn("WI-0200", section)

    def test_the_outstanding_major_is_announced_rather_than_silently_ignored(self):
        out = self.run_cmd(session.cmd_release_cut, _args(dry_run=True))
        self.assertIn("7.0.0", out)
        self.assertIn("2 of 2 ruled-in item(s) still outstanding", out)

    def test_the_milestone_ships_only_once_its_own_items_land(self):
        """Unrelated finished work can never trigger it — the condition is the manifest's
        own set, not "is anything done?"."""
        self.run_cmd(session.cmd_release_cut, _args())            # WI-0100 → 6.1.0
        self.assertFalse((session._rel_dir() / "7.0.0.md").exists())
        self.item("WI-0200", status="done", impact="feature")
        self.item("WI-0201", status="done", impact="feature")
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 7.0.0", out)
        self.assertEqual(self.read("WI-0100")["version"], "6.1.0",
                         "the unrelated item keeps the number it already shipped under")

    def test_an_ordinary_record_does_not_claim_the_majors_theme_or_target(self):
        """Caught on the FIRST REAL RUN, not by the dry-run — which returns before the
        record is written and therefore could not have seen it. The first 6.1.0 record
        carried `theme: poga runs independent of LLM model` and `target (original):
        2026-08-10`, both of them 7.0.0's, because the record header still keyed on
        "is a major declared?" rather than "is this record that major?"."""
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("6.1.0")
        self.assertNotIn("theme:", body)
        self.assertNotIn("target (original)", body)
        self.assertIn("no declared set", body.split("## Slipped", 1)[1].lower())

    def test_the_shipped_major_still_records_its_own_theme_and_target(self):
        """The other direction of the same fix — the record that IS the milestone must
        still carry it, or D7's permanent-original-target rule silently stops working."""
        self.item("WI-0200", status="done", impact="feature")
        self.item("WI-0201", status="done", impact="feature")
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("7.0.0")
        self.assertIn("- theme: poga runs independent of the model", body)
        self.assertIn("- target (original): 2026-08-10", body)

    def test_the_override_is_reachable_while_a_major_is_declared(self):
        """It used to sit in an `elif` behind the manifest check, so the documented escape
        hatch was unreachable exactly when one existed — and was discarded SILENTLY."""
        out = self.run_cmd(session.cmd_release_cut,
                           _args(override_bump="patch", reason="deliberate"))
        self.assertIn("cut 6.0.1", out)


class MinorHasNothingToSlipTest(ReleaseBase):
    def test_a_harvested_minor_says_so_rather_than_leaving_the_section_blank(self):
        self.seed()
        self.item("WI-0001", status="done", impact="feature")
        self.run_cmd(session.cmd_release_cut, _args())
        section = self.record("6.1.0").split("## Slipped", 1)[1]
        self.assertIn("no declared set", section.lower())


class RecordIsCommittedBeforeStampsTest(ReleaseBase):
    """The 7.0.0 cut auto-committed all 26 item stamps and left its OWN record untracked.

    `_rel_current` reads the highest record ON DISK, so the version read as cut to the
    process that cut it while existing nowhere in git — and had that lane been discarded,
    26 items would have carried `version: 7.0.0` with no record defining it. Neither side
    of that half-state announces itself.
    """

    def _calls(self, args):
        """Run a cut, recording the order of (commit-record, stamp-item) calls."""
        order = []
        real_commit, real_write = session._rel_commit_record, session._wi_write_item
        session._rel_commit_record = lambda p, v: order.append(("commit-record", v))
        session._wi_write_item = lambda it: (order.append(("stamp", it["id"])),
                                             real_write(it))[1]
        try:
            self.run_cmd(session.cmd_release_cut, args)
        finally:
            session._rel_commit_record, session._wi_write_item = real_commit, real_write
        return order

    def test_the_record_is_committed_at_all(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.assertIn("commit-record", [c[0] for c in self._calls(_args())])

    def test_the_record_is_committed_before_any_item_is_stamped(self):
        """Ordering is the whole fix. A record with unstamped items is self-announcing
        and re-runnable; stamps with no record are neither, so the survivable half must
        be the one that lands first."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.item("WI-0002", status="done", impact="feature")
        kinds = [c[0] for c in self._calls(_args())]
        self.assertEqual(kinds[0], "commit-record")
        self.assertEqual(kinds.count("commit-record"), 1)
        self.assertEqual(kinds.count("stamp"), 2)

    def test_a_dry_run_commits_nothing(self):
        """`--dry-run` promises it writes nothing and stamps nothing; a commit is both."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.assertEqual(self._calls(_args(dry_run=True)), [])


class StoreReceiptNamesWhereItActuallyCommittedTest(unittest.TestCase):
    """The receipt asserted "it lives in the main checkout" as a constant. True on the
    `poga work` path, false whenever `session.py` drives the store from inside a lane —
    where it printed the lane's own path in the same breath as the claim the commit was
    somewhere else, 26 times during the 7.0.0 cut. A receipt that misreports its location
    tells the reader not to look in the only place the commit is."""

    MAIN = pathlib.Path("/repo")
    LANE = pathlib.Path("/repo/.claude/worktrees/poga-9")

    def test_committed_in_the_main_checkout_says_so(self):
        s = session._wi_receipt_where(self.MAIN, self.MAIN)
        self.assertIn("main checkout", s)
        self.assertIn("nothing for the user to run", s.lower())

    def test_committed_in_a_lane_does_not_claim_the_main_checkout(self):
        s = session._wi_receipt_where(self.LANE, self.MAIN)
        self.assertIn("THIS lane", s)
        self.assertNotIn("It lives in the main checkout", s)
        self.assertIn("unchanged until then", s)

    def test_an_undeterminable_checkout_borrows_neither_confident_sentence(self):
        """"I could not tell" is its own answer, not a synonym for either."""
        s = session._wi_receipt_where(self.LANE, None)
        self.assertIn("unverified", s)
        self.assertNotIn("It lives in the main checkout", s)
        self.assertNotIn("THIS lane", s)


class LaneHeldIdsTest(ReleaseBase):
    """WI-0239 — two of the four lane-blind lookups live in this file's subject.

    `release-declare` keyed off `_wi_parse()` and refused a manifest whose ruled-in items
    were minted on open lanes with `not in the store` — the refusal is the safe direction
    (a manifest is FROZEN once declared, so ruling in an id this tree cannot read is not
    something to wave through), but the diagnosis sent the operator looking for work that
    exists.

    `_rel_promise_state` was the SILENT one: an id absent from the trunk store yielded
    `it = {}`, no version, and landed in `outstanding` — indistinguishable from work
    nobody has started, so the promise counter read a stranded lane as incomplete and
    said nothing about which kind of incomplete it was.

    The branch probe itself is pinned end-to-end against real lanes in
    `tests/test_claims.py::AbsentCauseTest`; these mock at that seam and pin what the two
    release surfaces DO with the answer."""

    HELD = {"WI-0300": "But ref worktree-poga-7 carries it — an UNLANDED LANE holds the "
                       "id. It is real, not a fiction: `poga resume 7` then "
                       "`python3 session.py merge` from the lane."}

    def declare(self, items, version="7.0.0"):
        return self.run_cmd(
            session.cmd_release_declare,
            _args(version=version, theme="A milestone", items=items, target="", done=""))

    def manifest(self, version="7.0.0", items=("WI-0001",)):
        d = session._rel_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{version}-manifest.md").write_text(
            f"- version: {version}\n- theme: A milestone\n- target: \n"
            f"- items: {', '.join(items)}\n", encoding="utf-8")

    # ── release-declare ─────────────────────────────────────────────────────────
    def test_a_lane_minted_id_is_still_refused_but_named_correctly(self):
        self.seed()
        with mock.patch.object(session, "_wi_absent_cause", return_value=self.HELD):
            out = self.run_cmd_expecting_exit(items="WI-0300")
        self.assertIn("worktree-poga-7", out)
        self.assertIn("UNLANDED LANE", out)
        self.assertIn("session.py merge", out, "name the remedy, not just the fault")

    def test_an_id_nobody_ever_drew_still_reads_as_absent(self):
        self.seed()
        with mock.patch.object(session, "_wi_absent_cause", return_value={}):
            out = self.run_cmd_expecting_exit(items="WI-9999")
        self.assertIn("WI-9999", out)
        self.assertIn("no ref carries it", out)
        self.assertNotIn("UNLANDED", out)

    def run_cmd_expecting_exit(self, items):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as e:
                session.cmd_release_declare(
                    _args(version="7.0.0", theme="A milestone", items=items,
                          target="", done=""))
        self.assertEqual(e.exception.code, 2, "the refusal is the safe direction")
        return buf.getvalue()

    # ── the promise counter ─────────────────────────────────────────────────────
    def test_the_promise_state_separates_absent_from_merely_unshipped(self):
        self.item("WI-0001", status="open", impact="fix")
        with mock.patch.object(session, "_wi_absent_cause", return_value=self.HELD):
            st = session._rel_promise_state(
                {"items": ["WI-0001", "WI-0300"]}, set())
        self.assertEqual(sorted(st["outstanding"]), ["WI-0001", "WI-0300"])
        self.assertEqual(list(st["absent"]), ["WI-0300"],
                         "WI-0001 is in the store and merely unfinished")
        self.assertFalse(st["complete"], "lane-held work has not shipped")

    def test_only_ids_missing_from_the_store_are_probed(self):
        """The cost guard, and the meaning guard: an outstanding id that IS here is
        ordinary unfinished work and must not acquire a branch story."""
        self.item("WI-0001", status="open", impact="fix")
        with mock.patch.object(session, "_wi_absent_cause", return_value={}) as probe:
            session._rel_promise_state({"items": ["WI-0001"]}, set())
        probe.assert_called_once_with([])

    def test_the_cut_note_names_the_lane_held_item(self):
        """`release-cut` is the surface an operator actually reads. Without this they see
        "1 of 2 ruled-in item(s) still outstanding" and go looking for work that is
        already done on a lane nobody landed."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.manifest(items=("WI-0001", "WI-0300"))
        with mock.patch.object(session, "_wi_absent_cause", return_value=self.HELD):
            out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("still outstanding", out)
        self.assertIn("WI-0300 is not in this tree's store", out)
        self.assertIn("worktree-poga-7", out)
        self.assertIn("cut 6.0.1", out, "and the ordinary harvest still ships normally")

    def test_an_ordinary_outstanding_item_adds_no_line(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix")
        self.item("WI-0002", status="open", impact="fix")
        self.manifest(items=("WI-0001", "WI-0002"))
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("still outstanding", out)
        self.assertNotIn("not in this tree's store", out)


class RecordSaysWhatIsNewTest(ReleaseBase):
    """WI-0065 — a record must answer "what's new?" without opening every item.

    the operator's ruling, session ~111, on the first harvested record: a minor needs a one-line
    summary readable at a glance and a slightly longer record readable in seconds. Two
    deliverables. 6.1.0's record was thirteen bare ids and a
    date — literally complete and practically unreadable, because the theme that answers
    the question is written only for a declared major, and a major is the version that
    ships the LEAST of the work.

    The properties defended here:

      - **The one-liner exists on a minor.** Derived, never typed: `release-cut` runs
        unattended on the landed trunk, so a summary that needs an operator at the
        keyboard is a summary that ends up empty.
      - **Every id the record names carries its title**, in all four sections, so the
        five-second read is the record itself rather than thirteen file opens.
      - **A tie is reported as a tie.** Naming the alphabetically-first of two equal
        groups would read as a finding while being an artefact of the alphabet.
      - **Nothing gains a stored copy of a title.** ADR-0073 D3 keeps the item file the
        single writer of a title; a record renders one at write time and is immutable, so
        it is a receipt, not a second copy anybody has to keep in sync.
    """

    def test_a_minor_carries_a_summary_and_every_title(self):
        """The acceptance case, whole: seed a store, cut a MINOR, read the record."""
        self.seed()
        self.item("WI-0001", status="done", impact="feature",
                  title="Harvest a release from closed items", group="Versioning")
        self.item("WI-0002", status="done", impact="fix",
                  title="A stranded lane no longer reads as unfinished work",
                  group="Versioning")
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("cut 6.1.0", out)
        body = self.record("6.1.0")

        summary = next((ln for ln in body.splitlines()
                        if ln.startswith("- summary:")), "")
        self.assertTrue(summary, "a minor's record must carry a summary line")
        self.assertTrue(summary[len("- summary:"):].strip(),
                        "an empty summary is worse than none — it reads as answered")
        self.assertIn("2 items", summary)
        self.assertIn("1 feature", summary)
        self.assertIn("1 fix", summary)
        self.assertIn("all in Versioning", summary)

        self.assertIn("- WI-0001 — Harvest a release from closed items", body)
        self.assertIn("- WI-0002 — A stranded lane no longer reads as unfinished work",
                      body)

    def test_the_operator_sees_the_one_liner_without_opening_the_record(self):
        """The at-a-glance half has to reach the person who ran the verb, not only the
        file — the record lands on the trunk, the terminal is where they are."""
        self.seed()
        self.item("WI-0001", status="done", impact="feature", title="A title",
                  group="Versioning")
        out = self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("1 item — 1 feature; all in Versioning", out)

    def test_a_dry_run_shows_what_it_would_say(self):
        """A dry run that hides the summary cannot be used to check the summary."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title="A title", group="Lanes")
        out = self.run_cmd(session.cmd_release_cut, _args(dry_run=True))
        self.assertIn("would cut 6.0.1", out)
        self.assertIn("1 item — 1 fix; all in Lanes", out)

    def test_the_largest_group_wins_not_the_first_one_alphabetically(self):
        """The decoy is deliberate: `Alpha` sorts first and `Zulu` is the actual answer,
        so a summary that reached for `sorted(groups)[0]` fails here instead of passing
        by coincidence."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title="a", group="Alpha")
        for n, wid in enumerate(("WI-0002", "WI-0003", "WI-0004")):
            self.item(wid, status="done", impact="fix", title="t%d" % n, group="Zulu")
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("6.0.1")
        self.assertIn("mostly Zulu (3 of 4)", body)
        self.assertNotIn("mostly Alpha", body)

    def test_a_tie_is_reported_as_a_tie(self):
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title="a", group="Alpha")
        self.item("WI-0002", status="done", impact="fix", title="b", group="Zulu")
        self.run_cmd(session.cmd_release_cut, _args())
        self.assertIn("led by Alpha, Zulu (1 each of 2)", self.record("6.0.1"))

    def test_an_ungrouped_harvest_says_so_rather_than_going_quiet(self):
        """A clause that silently disappears is indistinguishable from one that ran and
        found nothing ([`declare-what-a-check-assumes`])."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title="a")
        self.item("WI-0002", status="done", impact="feature", title="b")
        self.run_cmd(session.cmd_release_cut, _args())
        summary = next(ln for ln in self.record("6.1.0").splitlines()
                       if ln.startswith("- summary:"))
        self.assertIn("no group recorded on any of them", summary)
        self.assertIn("2 items — 1 feature, 1 fix", summary)

    def test_an_item_with_no_title_renders_as_a_bare_id_not_a_doubled_one(self):
        """`_wi_parse_text` defaults a missing title to the id itself, so a naive render
        produces `WI-0001 — WI-0001`."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title="WI-0001")
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("6.0.1")
        self.assertIn("- WI-0001\n", body)
        self.assertNotIn("WI-0001 — WI-0001", body)

    MARKER = "Zygomorphic quicksilver parapet"      # cannot occur by accident

    def carriers(self, marker):
        """Every file under the fixture root whose bytes contain `marker`."""
        found = set()
        for path in self.tmp.rglob("*"):
            if not path.is_file():
                continue
            try:
                if marker in path.read_text(encoding="utf-8"):
                    found.add(path.relative_to(self.tmp).as_posix())
            except (OSError, UnicodeDecodeError):
                continue
        return found

    def test_the_cut_adds_exactly_one_carrier_of_a_title_and_it_is_the_record(self):
        """The ADR-0073 D3 tension, settled by measurement rather than by assertion.

        A title may be RENDERED into the immutable release record — that is the
        build-assemble delivery `single-source-and-deliver` sanctions, and the record can
        never drift because `release-cut` refuses to rewrite one. What must not happen is
        a second STORE: an index, a sidecar, a cached titles map, a copy written back
        into a sibling item.

        Measured as a DELTA across the cut, not as an absolute set, and the difference
        matters. Writing the fixture items already puts the title in a second file —
        `.session-state/wi-feed.json`, the gitignored, machine-local, `wi-feed`-rebuildable
        surface a read-only dashboard renders. That is a render of the store, owned by
        a different mechanism and out of this item's scope; an absolute assertion here
        would fail on it and say nothing about `release-cut`. The delta asks the question
        the acceptance actually asks — does cutting a release create a new place a title
        is kept? — and the answer has to be "one, the record".
        """
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title=self.MARKER)
        self.item("WI-0002", status="done", impact="fix", title="An unrelated item")
        before = self.carriers(self.MARKER)
        item = next((x for x in before if x.startswith("work-items/WI-0001")), None)
        self.assertIsNotNone(item, "the item file must own its title to begin with")

        self.run_cmd(session.cmd_release_cut, _args())

        record = "releases/%s/6.0.1.md" % session._rel_project()
        after = self.carriers(self.MARKER)
        self.assertEqual(after - before, {record},
                         "cutting a release may render a title into the immutable record "
                         "and nowhere else")
        self.assertIn(item, after, "and the item file still owns it")

    def test_no_sibling_item_gains_another_items_title(self):
        """The narrower half, stated on its own so it cannot be lost in the delta: within
        the store itself — the one place ADR-0073 D3 governs — a title lives in exactly
        one file, before the cut and after it."""
        self.seed()
        self.item("WI-0001", status="done", impact="fix", title=self.MARKER)
        self.item("WI-0002", status="done", impact="fix", title="An unrelated item")
        self.run_cmd(session.cmd_release_cut, _args())
        in_store = {x for x in self.carriers(self.MARKER)
                    if x.startswith("work-items/")}
        self.assertEqual(len(in_store), 1, in_store)
        self.assertTrue(next(iter(in_store)).startswith("work-items/WI-0001"))


class MajorRecordAlsoSaysWhatIsNewTest(ReleaseBase):
    """The summary is not a consolation prize for minors — a major carries it too.

    A theme and a summary are different claims: the theme is the promise a human wrote
    before the work, the summary is what the harvest turned out to hold. A milestone that
    shipped something other than what it promised is precisely the case worth being able
    to see, and it is invisible if only one of the two is on the record."""

    def setUp(self):
        super().setUp()
        self.seed()
        self.item("WI-0001", status="done", impact="feature",
                  title="Members stop inheriting our shape", group="Fleet",
                  migration="yes")
        self.item("WI-0002", status="done", impact="fix",
                  title="The push confirms at origin", group="Fleet")
        self.run_cmd(session.cmd_release_declare,
                     _args(version="7.0.0", theme="Members stop inheriting our shape",
                           items="WI-0001,WI-0002", target="2026-09-30", done=""))

    def test_a_major_carries_both_its_theme_and_its_summary(self):
        self.run_cmd(session.cmd_release_cut, _args())
        body = self.record("7.0.0")
        self.assertIn("- theme: Members stop inheriting our shape", body)
        self.assertIn("- summary: 2 items — 1 feature, 1 fix; all in Fleet", body)

    def test_the_migration_section_names_the_item_not_only_its_id(self):
        """`## Migration required` is the section a member READS to find out whether it
        has to edit its own files — a bare id there is the least useful place for one."""
        self.run_cmd(session.cmd_release_cut, _args())
        section = self.record("7.0.0").split("## Migration required", 1)[1]
        self.assertIn("- WI-0001 — Members stop inheriting our shape", section)


class PriorlyShippedItemKeepsItsVersionTest(ReleaseBase):
    """The `## Previously shipped in this promise` bullet leads with id and version,
    because that pair is what accounts for the promise; the title follows it.

    Order is load-bearing and not cosmetic: the id and the number it went out under are
    the accounting, and a title inserted between them would push the version behind a
    free-text field of unbounded length."""

    def test_an_item_that_shipped_earlier_keeps_its_version_and_gains_its_title(self):
        self.seed()
        self.item("WI-0001", status="done", impact="feature",
                  title="Members stop inheriting our shape", group="Fleet")
        self.item("WI-0002", status="open", impact="fix",
                  title="The push confirms at origin", group="Fleet")
        self.run_cmd(session.cmd_release_declare,
                     _args(version="7.0.0", theme="A theme",
                           items="WI-0001,WI-0002", target="2026-09-30", done=""))
        self.run_cmd(session.cmd_release_cut, _args())        # 6.1.0 takes WI-0001
        self.item("WI-0002", status="done", impact="fix",
                  title="The push confirms at origin", group="Fleet")
        self.run_cmd(session.cmd_release_cut, _args())        # => promise complete
        section = self.record("7.0.0").split(
            "## Previously shipped in this promise", 1)[1].split("\n## ", 1)[0]
        self.assertIn("- WI-0001 — 6.1.0 — Members stop inheriting our shape", section)


class SlippedItemsAreNamedNotOnlyNumberedTest(ReleaseBase):
    """`## Slipped` is the honesty section — it exists so a record cannot rewrite the plan
    to match the outcome. A reader who has to open a file to find out what was dropped is
    a reader who does not look.

    A slipped bullet can no longer be produced by a real cut: ADR-0084 D6 made `slipped`
    structurally empty, and the section is kept because the record is a published
    contract. The renderer its bullets go through is still exercised directly."""

    def test_the_renderer_appends_a_title_and_drops_an_absent_one(self):
        titles = {"WI-0009": "The gate does not name the test that failed"}
        self.assertEqual(
            session._rel_titled("WI-0009", titles),
            "WI-0009 — The gate does not name the test that failed")
        self.assertEqual(session._rel_titled("WI-0010", titles), "WI-0010")


class SummaryShapeTest(unittest.TestCase):
    """`_rel_summary` on its own, where the interesting inputs are cheap to build."""

    @staticmethod
    def items(*specs):
        return [{"id": "WI-%04d" % i, "impact": imp, "group": grp}
                for i, (imp, grp) in enumerate(specs, start=1)]

    def test_one_item_is_singular(self):
        self.assertEqual(session._rel_summary(self.items(("fix", "Lanes"))),
                         "1 item — 1 fix; all in Lanes")

    def test_the_impact_mix_leads_with_the_larger_count(self):
        s = session._rel_summary(self.items(("fix", ""), ("feature", ""), ("fix", "")))
        self.assertIn("— 2 fix, 1 feature;", s)

    def test_a_partly_grouped_harvest_reports_the_share_not_a_bare_name(self):
        """`mostly Lanes` with no denominator invites reading it as "all of them"."""
        s = session._rel_summary(self.items(("fix", "Lanes"), ("fix", "Lanes"),
                                            ("fix", "")))
        self.assertIn("mostly Lanes (2 of 3)", s)
        self.assertNotIn("all in Lanes", s)

    def test_an_unlabelled_item_is_named_rather_than_dropped(self):
        """The cut refuses before reaching here, so this is defence in depth for any other
        caller — an item silently missing from the mix understates the release."""
        s = session._rel_summary(self.items(("", "Lanes")))
        self.assertIn("1 unlabelled", s)

    def test_three_tied_groups_are_all_named(self):
        s = session._rel_summary(self.items(("fix", "A"), ("fix", "B"), ("fix", "C")))
        self.assertIn("led by A, B, C (1 each of 3)", s)

    def test_a_leader_holding_a_twelfth_of_the_release_is_reported_as_a_spread(self):
        """The case the real store produced: 25 items over many groups, largest 2. A
        named leader there is a sentence that reads as a finding and carries none."""
        specs = [("fix", "G%d" % (i // 2)) for i in range(24)]
        s = session._rel_summary(self.items(*specs))
        self.assertIn("no dominant group (24 of 24 grouped, across 12)", s)
        self.assertNotIn("led by", s)
        self.assertNotIn("mostly", s)

    def test_a_clear_leader_is_still_named_when_it_clears_the_floor(self):
        """The floor must not swallow the useful case — a quarter of the harvest in one
        group is exactly the "what's new" answer the summary exists to give."""
        specs = [("fix", "Lanes")] * 3 + [("fix", "A"), ("fix", "B"), ("fix", "C")]
        s = session._rel_summary(self.items(*specs))
        self.assertIn("mostly Lanes (3 of 6)", s)

    def test_the_spread_clause_counts_only_the_items_it_is_about(self):
        """The real store's shape: a big harvest where most items carry no group at all.
        "across 4 groups" with no denominator reads as all of them being in those four."""
        specs = ([("fix", "A")] * 2 + [("fix", "B")] * 2 + [("fix", "C")]
                 + [("fix", "D")] + [("fix", "")] * 19)
        s = session._rel_summary(self.items(*specs))
        self.assertIn("no dominant group (6 of 25 grouped, across 4)", s)

    def test_more_than_three_tied_leaders_is_a_spread_not_a_list(self):
        """Four names and a count is not a one-liner."""
        s = session._rel_summary(self.items(("fix", "A"), ("fix", "B"),
                                            ("fix", "C"), ("fix", "D")))
        self.assertIn("no dominant group (4 of 4 grouped, across 4)", s)


# ── WI-0303 (b): a declared set rots by slippage ─────────────────────────────────
# ADR-0084 D1 closed every way a milestone can shrink EXCEPT one — an item nobody drops
# and nobody finishes — and these defend the reporter that names it.
#
# The property that needs defending hardest is not "it flags an idle item". It is that the
# three non-findings stay tellable apart from each other and from the finding: idle,
# recently moved, and NO DATED EVIDENCE AT ALL are three different sentences, and a
# reporter that folds the third into either of the others either invents a de-scope
# proposal or hides one. The threshold is measured to be quiet (see `REL_STALE_DAYS`), so
# a suite that only ever asserted the flag would pass against a reporter that had gone
# silent.


class ManifestStalenessBase(ReleaseBase):
    def manifest(self, version="8.0.0", items=(), declared="2026-09-01",
                 theme="a promise", target="2026-10-01"):
        d = session._rel_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{version}-manifest.md"
        p.write_text(
            f"# {version} — declaration manifest\n\n"
            f"- version: {version}\n- theme: {theme}\n- target: {target}\n"
            f"- declared: {declared}\n- items: {', '.join(items)}\n",
            encoding="utf-8")
        man = session._rel_parse_manifest(p)
        man["version"] = version
        return man

    def note(self, wid, day, suffix="0001"):
        """One note record, dated by its filename the way `_note_id` writes them."""
        d = session._notes_dir(session.WI_DIRNAME, wid, create=True)
        stamp = day.replace("-", "")
        (d / f"{stamp}T120000000000Z-devbox-{suffix}.md").write_text(
            "a note\n", encoding="utf-8")

    def report(self, man, today="2026-09-20", **kw):
        return session._rel_stale_report(man, today=today, **kw)

    def lines(self, man, today="2026-09-20", **kw):
        return "\n".join(session._rel_stale_lines(self.report(man, today, **kw)))


class ManifestStalenessTest(ManifestStalenessBase):
    def test_an_idle_ruled_in_item_is_flagged_with_a_de_scope_proposal(self):
        self.item("WI-0001")
        self.note("WI-0001", "2026-09-01")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("IDLE 14d OR MORE (1)", out)
        self.assertIn("idle 19d", out)
        self.assertIn("amend", out.lower())

    def test_a_recently_moved_item_is_not_flagged_but_its_age_is_still_printed(self):
        """The half a 'flag only on findings' reporter would have dropped. The age is the
        evidence that the check ran at all."""
        self.item("WI-0001")
        self.note("WI-0001", "2026-09-18")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertNotIn("IDLE 14d OR MORE", out)
        self.assertIn("moved within 14d (1)", out)
        self.assertIn("WI-0001 (2d)", out)

    def test_no_dated_evidence_is_its_own_answer_and_not_folded_into_either(self):
        """The item that would otherwise become a fabricated finding. An item with no note
        and no `probed:` stamp is neither idle nor fresh — it is unmeasured, and saying so
        is the whole of `declare-what-a-check-assumes` applied here."""
        self.item("WI-0001")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("NO MOVEMENT EVIDENCE (1)", out)
        self.assertIn("not a de-scope proposal", out)
        self.assertNotIn("IDLE 14d OR MORE", out)
        self.assertNotIn("moved within 14d", out)
        rows = self.report(self.manifest(items=["WI-0001"]))["rows"]
        self.assertEqual(["unknown"], [r["state"] for r in rows])

    def test_a_superseded_ruled_in_item_says_the_milestone_cannot_complete(self):
        """Not merely 'stale'. Under ADR-0084 D1 cancelled work never ships, so the
        promise is STUCK rather than slipping, and the two want different remedies."""
        self.item("WI-0001", status="superseded")
        self.note("WI-0001", "2026-09-19")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("SUPERSEDED WHILE RULED IN (1)", out)
        self.assertIn("can NEVER complete", out)
        self.assertNotIn("moved within 14d", out)

    def test_a_shipped_ruled_in_item_is_not_a_subject(self):
        """It cannot rot. `_rel_promise_state` is the authority on the split and this
        defers to it rather than re-deriving it alongside."""
        self.item("WI-0001", status="done", version="7.9.0")
        self.item("WI-0002")
        rep = self.report(self.manifest(items=["WI-0001", "WI-0002"]))
        self.assertEqual(["WI-0002"], [r["id"] for r in rep["rows"]])

    def test_a_complete_promise_says_so_rather_than_printing_an_empty_report(self):
        self.item("WI-0001", status="done", version="7.9.0")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("the promise is complete", out)

    def test_the_oldest_age_prints_even_when_nothing_trips(self):
        """THE DESIGN'S LOAD-BEARING TEST. `REL_STALE_DAYS` is measured to be quiet, so a
        report whose only output is a finding would be indistinguishable from one that had
        stopped working. An empty finding list has to read as a negative RESULT."""
        self.item("WI-0001")
        self.note("WI-0001", "2026-09-19")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("oldest idle 1d", out)
        self.assertIn("threshold 14d", out)

    def test_the_threshold_is_a_flag_and_moving_it_moves_the_verdict(self):
        self.item("WI-0001")
        self.note("WI-0001", "2026-09-15")
        self.assertNotIn("IDLE", self.lines(self.manifest(items=["WI-0001"])))
        self.assertIn("IDLE 3d OR MORE",
                      self.lines(self.manifest(items=["WI-0001"]), stale_days=3))

    def test_a_probed_stamp_counts_as_movement_when_it_is_the_newer_evidence(self):
        """Both clocks are read and the newest wins. Asserted on the report rather than on
        the text because the FRESH bucket is a compact list by design — a non-finding does
        not earn a line of its own — so the source only reaches the page on a finding."""
        self.item("WI-0001", probed="2026-09-19 abc1234")
        self.note("WI-0001", "2026-09-01")
        man = self.manifest(items=["WI-0001"])
        row = self.report(man)["rows"][0]
        self.assertEqual(("2026-09-19", "probe"), (row["moved"], row["source"]))
        self.assertNotIn("IDLE", self.lines(man))

    def test_a_finding_names_which_clock_it_read(self):
        """`note` and `probe` are different strengths of evidence: a note is somebody
        writing about the work, a `probed:` stamp records only the mechanical citation
        check `wi-stale` ran — whose own writer says it does not mean anyone re-read
        anything. A de-scope proposal resting on the weaker one must say so."""
        self.item("WI-0001", probed="2026-09-01 abc1234")
        out = self.lines(self.manifest(items=["WI-0001"]))
        self.assertIn("idle 19d (last probe 2026-09-01)", out)

    def test_an_id_that_is_not_in_the_store_is_named_as_unmeasurable(self):
        out = self.lines(self.manifest(items=["WI-0404"]))
        self.assertIn("NOT IN THIS STORE", out)

    def test_nothing_is_ever_written(self):
        """The remedy is an amendment with a reason in it (ADR-0084 D5). There is no verb
        for this reporter to have called even if it were allowed to."""
        self.item("WI-0001")
        self.note("WI-0001", "2026-09-01")
        man = self.manifest(items=["WI-0001"])
        before_item = (session._wi_dir() / "WI-0001-item-wi-0001.md").read_text()
        before_man = man["path"].read_text()
        self.lines(man)
        self.assertEqual(before_item,
                         (session._wi_dir() / "WI-0001-item-wi-0001.md").read_text())
        self.assertEqual(before_man, man["path"].read_text())


class StalenessOnTheReleaseListTest(ManifestStalenessBase):
    """It rides `release-list` because that verb's `--status` is already a startup hook.
    An on-demand verb nags nobody, and the item's acceptance is that the nagging must not
    come from anyone's memory."""

    def test_the_full_verb_carries_the_report_under_the_declared_major(self):
        self.seed("7.9.0")
        self.item("WI-0001")
        self.note("WI-0001", "2026-08-01")
        self.manifest(items=["WI-0001"])
        out = self.run_cmd(session.cmd_release_list, _args(status=False, stale_days=14))
        self.assertIn("DECLARED, not shipped", out)
        self.assertIn("IDLE 14d OR MORE", out)

    def test_the_status_line_names_a_number_even_when_nothing_trips(self):
        """Same argument as the oldest-age test, on the surface operator actually reads. A
        clause that appears only on a finding cannot be told apart from a broken one."""
        self.seed("7.9.0")
        self.item("WI-0001")
        # Dated TODAY rather than pinned, so the row is 0d idle whenever the suite runs.
        # `cmd_release_list` takes no `today`, and patching `_rel_stale_report` with a
        # lambda that calls it back is a self-recursion the first draft of this test
        # actually hit — it surfaced as the fail-open branch reporting "slippage
        # unreadable", which is the reporter working correctly on a broken test.
        self.note("WI-0001", datetime.now(timezone.utc).date().isoformat())
        self.manifest(items=["WI-0001"])
        out = self.run_cmd(session.cmd_release_list, _args(status=True, stale_days=14))
        self.assertIn("8.0.0 declared", out)
        self.assertIn("1 outstanding", out)
        self.assertIn("oldest 0d", out)

    def test_a_superseded_item_reaches_the_status_line_as_cannot_complete(self):
        self.seed("7.9.0")
        self.item("WI-0001", status="superseded")
        self.manifest(items=["WI-0001"])
        out = self.run_cmd(session.cmd_release_list, _args(status=True, stale_days=14))
        self.assertIn("cannot complete", out)

    def test_with_no_declared_major_the_verb_is_unchanged(self):
        """The common case, and the one a new section most easily breaks."""
        self.seed("7.9.0")
        out = self.run_cmd(session.cmd_release_list, _args(status=False, stale_days=14))
        self.assertIn("7.9.0", out)
        self.assertNotIn("outstanding", out)

    def test_an_unreadable_store_does_not_take_the_startup_line_down(self):
        """Fail-open like every other startup line: a banner that can raise is a banner
        that can take the whole session start with it."""
        self.seed("7.9.0")
        self.manifest(items=["WI-0001"])
        with mock.patch.object(session, "_rel_stale_report",
                               mock.Mock(side_effect=RuntimeError("boom"))):
            out = self.run_cmd(session.cmd_release_list, _args(status=True, stale_days=14))
        self.assertIn("slippage unreadable", out)
        self.assertIn("version 7.9.0", out)


# ── WI-0303 (a): the delegable harvest ───────────────────────────────────────────
# Agents propose and track; operator names and ratifies. The tests that matter most are the
# NEGATIVE ones — that the slate writes no theme and rules nothing in or out — because
# those are the two halves the item calls permanently non-delegable, and a later change
# that helpfully filled them in would look like an improvement.


class CandidateSlateTest(ManifestStalenessBase):
    def slate(self, **kw):
        kw.setdefault("today", "2026-09-20")
        kw.setdefault("sections", 0)
        return "\n".join(session._rel_candidate_lines(**kw))

    def test_no_theme_sentence_is_ever_written(self):
        """The item's own reasoning: an agent-written theme produces majors called
        'various improvements', and a proposed sentence cannot be un-read once it is on
        the page. The slate leaves the line blank and says why.

        EVERY `theme:` line is checked, not merely that a blank one exists. Asserting only
        the presence of the template would survive the change this test is here to
        prevent — one that helpfully prints a suggested sentence BESIDE the blank."""
        self.item("WI-0001", group="Lanes")
        self.item("WI-0002", group="Rituals")
        out = self.slate()
        themes = [ln for ln in out.splitlines() if ln.lstrip().startswith("theme:")]
        self.assertEqual(2, len(themes))
        for ln in themes:
            self.assertEqual("theme: ______________________________________________  "
                             "(yours)", ln.strip())
        self.assertIn("No theme sentence and no in/out ruling", out)

    def test_nothing_is_ruled_in_or_out(self):
        """The other permanently non-delegable half. The slate lays candidates out; it
        never marks one in, out, or on the cut line — that judgment is the sitting."""
        self.item("WI-0001", group="Lanes")
        out = self.slate().lower()
        for verdict in (" ruled in:", "recommend", "in:", "out:", "cut line:"):
            self.assertNotIn(f"\n{verdict}", out)
        self.assertIn("each cluster above is in, out, or on the cut line", out)

    def test_the_cluster_heading_is_the_authored_group_not_a_generated_label(self):
        self.item("WI-0001", group="Lanes")
        self.assertIn("### Lanes — 1 item(s)", self.slate())

    def test_cost_is_not_estimated_and_the_report_says_so(self):
        """There is no effort field in the store, so any number would be invented."""
        self.item("WI-0001", group="Lanes")
        out = self.slate()
        self.assertIn("Cost is not estimated", out)
        self.assertIn("named as proxies", out)

    def test_the_cluster_counters_are_a_partition(self):
        """Three numbers on one line read as a breakdown whether or not they were built as
        one. The first cut printed 'ready 1, blocked 1, held 2' for three items."""
        self.item("WI-0001", group="G")
        self.item("WI-0002", group="G", status="held")
        self.item("WI-0003", group="G", blocked_by=["WI-0002"])
        self.assertIn("size: 3 = ready 1 + blocked 1 + held 1", self.slate())

    def test_a_blocker_that_has_closed_does_not_count_as_blocking(self):
        """Resolved against the store at read time, the way `wi-list` does it. A closed
        blocker left in the field would inflate every cluster's cost with finished work."""
        self.item("WI-0009", status="done", version="7.0.0")
        self.item("WI-0001", group="G", blocked_by=["WI-0009"])
        out = self.slate()
        self.assertIn("size: 1 = ready 1 + blocked 0 + held 0", out)
        self.assertIn("none — it can be ruled in whole", out)

    def test_terminal_items_are_not_candidates(self):
        self.item("WI-0001", group="G", status="done", version="7.0.0")
        self.item("WI-0002", group="G", status="superseded")
        self.item("WI-0003", group="G")
        self.assertIn("### G — 1 item(s)", self.slate())

    def test_a_declared_majors_items_are_excluded_and_the_exclusion_is_announced(self):
        """The slate is for the version AFTER the one already promised; silently mixing
        the two would propose re-ruling work that is already ruled in."""
        self.item("WI-0001", group="G")
        self.item("WI-0002", group="G")
        self.manifest(items=["WI-0001"])
        out = self.slate()
        self.assertIn("IS ALREADY DECLARED", out)
        self.assertIn("### G — 1 item(s)", out)

    def test_migration_forcing_items_are_surfaced_but_never_as_a_trigger(self):
        """ADR-0081 D5 retired `impact: breaking` into `impact` + `migration`. The label
        feeds no version number and gates nothing, so it is a candidate SIGNAL."""
        self.item("WI-0001", group="G", migration="yes")
        out = self.slate()
        self.assertIn("Migration-forcing, not yet shipped", out)
        self.assertIn("never read as a trigger", out)
        self.assertIn("WI-0001", out)

    def test_no_migration_forcing_item_reads_as_a_measured_zero(self):
        """The dropped half of this item — escalate when N breaking items queue — was
        dropped BECAUSE the set is empty. The slate re-measures that on the day it is read
        rather than restating the ruling as a fact."""
        self.item("WI-0001", group="G")
        out = self.slate()
        self.assertIn("None. No live item carries `migration: yes`", out)
        self.assertIn("measured zero", out)

    def test_an_unlabelled_item_is_named_because_it_refuses_the_cut(self):
        """`_wi_derive_bump` refuses the whole computation on an unlabelled item, so a
        slate that did not name them would propose a set that cannot be released."""
        self.item("WI-0001", group="G", impact="")
        self.assertIn("UNLABELLED", self.slate())

    def test_the_section_cap_announces_itself_when_it_truncates(self):
        """P19 bounds the layout, and a bound silently reached is indistinguishable from a
        short store — which is how a third of the open set once went unprobed."""
        self.item("WI-0001", group="A")
        self.item("WI-0002", group="B")
        out = self.slate(sections=1)
        self.assertIn("NOT SHOWN (1)", out)
        self.assertIn("Nothing above is a statement about it", out)

    def test_the_cap_announcement_does_not_silently_cap_its_own_name_list(self):
        """Caught against the live store: the header counted ten and then listed eight.
        That is the same defect the header exists to prevent, one level down."""
        for n in range(1, 12):
            self.item(f"WI-{n:04d}", group=f"G{n:02d}")
        out = self.slate(sections=1)
        self.assertIn("NOT SHOWN (10)", out)
        self.assertIn("and 2 more not named here", out)

    def test_ungrouped_items_are_listed_rather_than_dropped(self):
        self.item("WI-0001", group="")
        out = self.slate()
        self.assertIn("## Ungrouped — 1 item(s)", out)
        self.assertIn("WI-0001", out)

    def test_out_writes_exactly_what_it_would_have_printed(self):
        self.item("WI-0001", group="G")
        dest = self.tmp / "slate.md"
        out = self.run_cmd(session.cmd_release_candidates,
                           _args(out=str(dest), sections=0, today="2026-09-20"))
        self.assertIn("Wrote the candidate slate", out)
        self.assertEqual(self.slate() + "\n", dest.read_text(encoding="utf-8"))

    def test_the_default_writes_nothing(self):
        """A slate is a view over the store and is stale the moment an item moves;
        persisting a dated copy on every run mints the second backlog the manifest's own
        preamble exists to prevent."""
        self.item("WI-0001", group="G")
        before = sorted(p.name for p in self.tmp.rglob("*"))
        self.run_cmd(session.cmd_release_candidates,
                     _args(out="", sections=0, today="2026-09-20"))
        self.assertEqual(before, sorted(p.name for p in self.tmp.rglob("*")))


if __name__ == "__main__":
    unittest.main()
