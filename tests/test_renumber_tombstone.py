"""WI-0307 half three — the renumberer deleted the id it moved.

THE MEASUREMENT. Session ~241's land remapped four re-scoped items onto drawn numbers.
`_store_renumber_one` writes the new file and then unlinks the old one with
`missing_ok=True`, leaving NOTHING behind — so WI-0018, WI-0047 and WI-0269 went missing
from `work-items/` entirely. `wi-check` refused the trunk with three problems, and the
ids were restored by hand as superseded tombstones. The store's rule is that an id is
never deleted: other items' notes cite it, commits cite it, and a vanished id turns every
one of those into a dangling reference.

Part two stops the re-scope case from ever reaching the renumberer. This is the floor
under it: WHATEVER causes a renumber, the number it vacates still resolves.

WHEN A TOMBSTONE IS WRONG, and this is the half that is easy to get backwards. In a
GENUINE collision the old number is not vacated at all — the trunk's own item is still
sitting on it, which is why the land renumbered in the first place. Writing a tombstone
there would put a second file on that number and re-create the collision the remap had
just resolved. So the tombstone is written only when nothing else holds the number after
the move, which is exactly the condition "this id would otherwise be deleted".

  A. A VACATED ID STILL RESOLVES — a superseded tombstone naming its successor.
  B. `wi-check` IS SOUND AFTER A RENUMBER. The end-to-end property the incident
     violated, asserted through the validator rather than by reading fields.
  C. NO TOMBSTONE WHEN THE NUMBER IS STILL HELD. The negative control that keeps A from
     being implemented as "always write a file at the old id".
  D. THE TOMBSTONE SATISFIES THE STORE'S OWN RULES — status `superseded`, and notes that
     name the successor, which is what `_wi_validate` requires of a superseded item.
  E. THE OPS STORE GETS THE SAME FLOOR. `superseded` is a valid status in both
     namespaces and the renumberer is deliberately store-neutral (WI-0245).

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _item_text(wid, title):
    return (f"# {wid}: {title}\n\n"
            f"- status: open\n- section: next\n- blocked-by: \n"
            f"- group: \n- source: \n- impact: fix\n- version: \n\n"
            f"ACCEPTANCE: the body of {wid}.\n")


def _ops_text(oid, title):
    return (f"# {oid}: {title}\n\n"
            f"- status: open\n- cadence: \n- last-completed: \n"
            f"- last-result: \n- due: \n- group: \n- source: \n\n"
            f"the body of {oid}.\n")


@unittest.skipUnless(GIT, "git required")
class TombstoneBase(unittest.TestCase):
    """A real checkout with a small, GAP-FREE store — a gap would send
    `_wi_missing_numbers` to git, and the subject here is the tombstone."""

    def setUp(self):
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        (self.root / "work-items").mkdir(parents=True)
        (self.root / "sessions" / "journal").mkdir(parents=True)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@t")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "config", "commit.gpgsign", "false")

        for n, title in ((1, "first"), (2, "the item that moves")):
            (self.root / "work-items" / f"WI-{n:04d}-x.md").write_text(
                _item_text(f"WI-{n:04d}", title), encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "base")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.root
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "tombtest"
        self.addCleanup(self._restore)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)

    def _renumber(self, old="WI-0002", new="WI-0003", key="wi"):
        c = session._counter(key)
        return session._store_renumber_one(c, old, new)

    def _wi_check(self):
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                session.cmd_wi_check(argparse.Namespace(status=False))
            code = 0
        except SystemExit as e:
            code = e.code
        return code, buf.getvalue()

    def _text(self, store, wid):
        hits = sorted((self.root / store).glob(f"{wid}-*.md"))
        return hits[0].read_text(encoding="utf-8") if hits else None


class AVacatedIdStillResolvesTest(TombstoneBase):
    """Property A — an id is never deleted."""

    def test_the_old_id_still_has_a_file(self):
        self._renumber()
        self.assertIsNotNone(
            session._wi_filename_for("WI-0002"),
            "the renumberer must leave a tombstone — an id is never deleted")

    def test_the_tombstone_names_its_successor(self):
        self._renumber()
        text = self._text("work-items", "WI-0002")
        self.assertIn("WI-0003", text,
                      "the tombstone must point at the number the item moved to")
        self.assertIn("superseded", text.lower())

    def test_the_moved_item_is_really_there_too(self):
        # Control: a tombstone is only correct if the item genuinely moved.
        self._renumber()
        moved = self._text("work-items", "WI-0003")
        self.assertIsNotNone(moved, "the item must exist at its new number")
        self.assertIn("the item that moves", moved)


class WiCheckIsSoundAfterARenumberTest(TombstoneBase):
    """Property B — the end-to-end property the incident actually violated."""

    def test_the_store_validates_after_a_renumber(self):
        self._renumber()
        code, out = self._wi_check()
        self.assertEqual(code, 0, f"a renumber must leave the store sound:\n{out}")

    def test_without_the_tombstone_the_same_store_is_refused(self):
        # THE NEGATIVE CONTROL, and the one that proves B is not vacuous: delete the
        # tombstone and the validator must refuse, exactly as it refused the trunk in
        # session ~241.
        self._renumber()
        for p in (self.root / "work-items").glob("WI-0002-*.md"):
            p.unlink()
        code, out = self._wi_check()
        self.assertEqual(code, 1,
                         f"a deleted id must still be refused:\n{out}")


class NoTombstoneWhenTheNumberIsStillHeldTest(TombstoneBase):
    """Property C — the half that is easy to get backwards."""

    def test_a_second_file_on_the_old_number_gets_no_tombstone(self):
        # The post-rebase genuine-collision shape: two files on WI-0002. The remap moves
        # one of them; the other still holds the number, so a tombstone would put the
        # collision straight back.
        (self.root / "work-items" / "WI-0002-second-holder.md").write_text(
            _item_text("WI-0002", "a different item on the same number"),
            encoding="utf-8")
        names_before = sorted(p.name for p in
                              (self.root / "work-items").glob("WI-0002-*.md"))
        self.assertEqual(len(names_before), 2, "the fixture must really collide")

        self._renumber()
        left = sorted(p.name for p in (self.root / "work-items").glob("WI-0002-*.md"))
        self.assertEqual(
            len(left), 1,
            f"exactly one file may remain on WI-0002, not a tombstone beside it: {left}")
        self.assertNotIn("superseded",
                         (self.root / "work-items" / left[0]).read_text(encoding="utf-8"),
                         "the surviving file is a live item, not a tombstone")


class TheTombstoneSatisfiesTheStoresRulesTest(TombstoneBase):
    """Property D — valid by the store's own validator, not merely present."""

    def test_status_is_superseded_and_the_successor_is_named(self):
        self._renumber()
        items = {it["id"]: it for it in session._wi_parse()}
        tomb = items.get("WI-0002")
        self.assertIsNotNone(tomb)
        self.assertEqual(tomb["status"], "superseded")
        self.assertIn("WI-0003", tomb.get("notes") or "",
                      "`_wi_validate` requires a superseded item to name a successor")

    def test_the_tombstone_does_not_carry_the_old_blockers_or_section(self):
        self._renumber()
        items = {it["id"]: it for it in session._wi_parse()}
        tomb = items["WI-0002"]
        self.assertEqual(tomb.get("blocked_by") or [], [],
                         "a tombstone blocks nothing — the live item carries the edges")
        self.assertEqual(tomb.get("section"), "backlog",
                         "a tombstone is not queued work")


class TheTrunkSuppressesTheTombstoneTest(TombstoneBase):
    """Property C, second half — the suppressor that keeps a real collision resolved.

    A lane that branched before the trunk landed its own item does not carry that file,
    so the working tree alone reads the number as vacated when it is not. Passing the
    trunk ref is what makes the answer the same on both sides of the rebase."""

    def _trunk_also_holds_wi_0002(self):
        """Advance the trunk so it holds a DIFFERENT item on WI-0002, and hand back the
        ref — the pre-rebase collision shape the existing renumber suites exercise."""
        other = self.tmp / "other"
        _git(self.root, "worktree", "add", "-q", "-b", "side", str(other), "HEAD")
        (other / "work-items" / "WI-0002-trunk-side.md").write_text(
            _item_text("WI-0002", "the trunk's own second item"), encoding="utf-8")
        for p in (other / "work-items").glob("WI-0002-x.md"):
            p.unlink()
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "trunk lands its own WI-0002")
        return subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    def test_no_tombstone_when_the_trunk_still_holds_the_number(self):
        parent = self._trunk_also_holds_wi_0002()
        c = session._counter("wi")
        session._store_renumber_one(c, "WI-0002", "WI-0003", parent)
        self.assertIsNone(
            session._wi_filename_for("WI-0002"),
            "the trunk's own item keeps WI-0002 resolving; a tombstone here would put "
            "a second file on the number and re-create the collision")

    def test_an_unreadable_trunk_ref_also_suppresses(self):
        # Fail-closed: "I could not read the trunk" must not borrow the answer "the
        # trunk does not have it", which is the direction that writes a colliding file.
        c = session._counter("wi")
        session._store_renumber_one(c, "WI-0002", "WI-0003", "no-such-ref-at-all")
        self.assertIsNone(session._wi_filename_for("WI-0002"),
                          "an unreadable trunk ref suppresses the tombstone")

    def test_and_with_no_trunk_ref_the_tombstone_is_written(self):
        # The control for the two above: same call, no parent, tombstone appears.
        c = session._counter("wi")
        session._store_renumber_one(c, "WI-0002", "WI-0003")
        self.assertIsNotNone(session._wi_filename_for("WI-0002"))


class TheNotesDirectoryMovesAndIsNeverCopiedTest(TombstoneBase):
    """WI-0307 property C from `test_note_record_duplicates` — pinned where the
    renumber fixture lives.

    The incident was originally blamed on the renumberer copying note folders. Git says
    otherwise: the renumber commits recorded every record as `R100`, a pure rename, and
    the duplicate came from two lanes writing the same record and a merge keeping both.
    So this pins the property the acceptance names — the renumberer MOVES — so that a
    future edit cannot quietly introduce the copy it was accused of."""

    def _note(self, wid, name, text="a note record.\n"):
        d = self.root / "work-items" / "notes" / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def test_the_old_notes_directory_is_left_with_nothing(self):
        self._note("WI-0002", "20260825T135423462062Z-devbox-009c.md")
        self._note("WI-0002", "20260825T141429380459Z-devbox-e3c6.md")
        self._renumber()
        old = self.root / "work-items" / "notes" / "WI-0002"
        left = sorted(p.name for p in old.glob("*.md")) if old.is_dir() else []
        self.assertEqual(left, [],
                         f"the records must MOVE, never be copied: {left}")

    def test_the_records_are_all_at_the_new_id(self):
        self._note("WI-0002", "20260825T135423462062Z-devbox-009c.md")
        self._note("WI-0002", "20260825T141429380459Z-devbox-e3c6.md")
        self._renumber()
        new = self.root / "work-items" / "notes" / "WI-0003"
        moved = sorted(p.name for p in new.glob("*.md"))
        self.assertIn("20260825T135423462062Z-devbox-009c.md", moved)
        self.assertIn("20260825T141429380459Z-devbox-e3c6.md", moved)

    def test_and_the_store_has_no_duplicated_record_afterwards(self):
        # The two above, stated as the property the validator actually enforces.
        self._note("WI-0002", "20260825T135423462062Z-devbox-009c.md")
        self._renumber()
        self.assertEqual(session._duplicate_note_records(), [],
                         "a renumber must not leave a record with two owners")


@unittest.skipUnless(GIT, "git required")
class TheOpsStoreGetsTheSameFloorTest(TombstoneBase):
    """Property E — the renumberer is store-neutral, so the floor must be too."""

    def setUp(self):
        super().setUp()
        (self.root / "ops-items").mkdir(parents=True, exist_ok=True)
        for n, title in ((1, "an obligation"),):
            (self.root / "ops-items" / f"OPS-{n:04d}-x.md").write_text(
                _ops_text(f"OPS-{n:04d}", title), encoding="utf-8")

    def test_an_ops_renumber_also_leaves_a_tombstone(self):
        self._renumber(old="OPS-0001", new="OPS-0002", key="ops")
        text = self._text("ops-items", "OPS-0001")
        self.assertIsNotNone(text, "an OPS id is never deleted either")
        self.assertIn("OPS-0002", text)
        self.assertIn("superseded", text.lower())

    def test_the_ops_store_still_validates(self):
        self._renumber(old="OPS-0001", new="OPS-0002", key="ops")
        code, out = self._wi_check()
        self.assertEqual(code, 0, f"the ops tombstone must be valid:\n{out}")


if __name__ == "__main__":
    unittest.main()
