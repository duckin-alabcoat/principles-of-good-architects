"""WI-0307 half one — one note record, two owners, and nothing that could say so.

THE MEASUREMENT. Three note records under `work-items/notes/` existed twice, once under
a pre-renumber id and once under the post-renumber id: `0167`=`0183`, `0172`=`0188`,
`0173`=`0189`. `wi-check` read the store as sound throughout, because nothing it ran
looked at the notes directory at all.

WHAT ACTUALLY MADE THE COPY, established from git rather than assumed. The renumber
commit `e9a45e74` moved every one of those records — git recorded them `R100`, a pure
rename. The duplicate came from CONCURRENCY, not from the renumberer: two lanes each
wrote the same record under `WI-0167` (`8edb6127` and `0c00b082`), the renumber renamed
one lineage to `WI-0183`, and the merge kept the other lineage's path. So the fix that
matters is not "make the renumberer move" — it already moves, and property C pins that
so it stays true — it is that the STORE'S OWN VALIDATOR can see a record with two
owners. Part four then puts that validator inside the land gate, which is what makes
this class unable to pass green again.

WHY THE FILENAME IS PROOF. A note record is named `<utc-stamp>-<machine>-<rand>.md` by
`_note_write`, so its name is unique by construction. The same name under two ids is
therefore never a coincidence and never a naming collision — it is one record that
exists in two places, and at most one of those places can be right.

  A. A record filed under two ids FAILS the check, and the message names both owners.
  B. NEGATIVE CONTROL — the same store with one owner per record is sound. Without this,
     A could equally mean "the check refuses everything".
  C. (The renumberer MOVES a notes directory and never leaves a copy behind — the
     property the acceptance names. It is pinned in
     `test_renumber_tombstone.TheNotesDirectoryMovesAndIsNeverCopiedTest`, where the
     renumber fixture already lives, rather than rebuilt here.)
  D. The OPS store is checked by the SAME pass. Both stores keep notes under
     `<store>/notes/<ID>/` (WI-0165), and a validator that covered one of them would be
     the `ship-the-detector-with-the-capability` gap one directory over.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_live_store  # noqa: E402

#: A real record name, in the shape `_note_write` produces.
REC_A = "20260825T135423462062Z-devbox-009c.md"
REC_B = "20260825T141429380459Z-devbox-e3c6.md"


def _item_text(wid, title):
    return (f"# {wid}: {title}\n\n"
            f"- status: open\n- section: next\n- blocked-by: \n"
            f"- group: \n- source: \n- impact: fix\n- version: \n\n"
            f"ACCEPTANCE: the body of {wid}.\n")


def _ops_text(oid, title):
    return (f"# {oid}: {title}\n\n"
            f"- status: open\n- cadence: \n- due: \n- last-completed: \n"
            f"- last-result: \n- source: \n\n"
            f"the body of {oid}.\n")


class StoreBase(unittest.TestCase):
    """A store on disk, with `session.ROOT` pointed at it and nothing reaching the
    developer's own backlog (`neutralize_live_store`, WI-0295)."""

    def setUp(self):
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        (self.root / "work-items" / "notes").mkdir(parents=True)
        neutralize_live_store(self, root=self.root)

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        session.ROOT = self.root
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        self.addCleanup(self._restore)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # Contiguous ids on purpose: a gap would send `_wi_missing_numbers` to git, and
        # this fixture's subject is the notes directory, not the allocator.
        for n, title in ((1, "first"), (2, "second"), (3, "third")):
            (self.root / "work-items" / f"WI-{n:04d}-x.md").write_text(
                _item_text(f"WI-{n:04d}", title), encoding="utf-8")

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    # -- helpers ---------------------------------------------------------------
    def _note(self, store, wid, name, text="a note record.\n"):
        d = self.root / store / "notes" / wid
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def _wi_check(self):
        """(exit code, printed output) — the CLI driver, as `test_claims` does it."""
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                session.cmd_wi_check(argparse.Namespace(status=False))
            code = 0
        except SystemExit as e:
            code = e.code
        return code, buf.getvalue()


class ARecordWithTwoOwnersFailsTest(StoreBase):
    """Property A — the check the store did not have."""

    def test_the_same_record_under_two_ids_fails_the_check(self):
        self._note("work-items", "WI-0001", REC_A)
        self._note("work-items", "WI-0002", REC_A)          # the duplicate
        code, out = self._wi_check()
        self.assertEqual(code, 1, f"wi-check must FAIL on a duplicated record:\n{out}")
        self.assertIn(REC_A, out, "the message must name the record")
        self.assertIn("WI-0001", out, "the message must name both owners")
        self.assertIn("WI-0002", out, "the message must name both owners")

    def test_it_names_every_duplicated_record_not_only_the_first(self):
        self._note("work-items", "WI-0001", REC_A)
        self._note("work-items", "WI-0002", REC_A)
        self._note("work-items", "WI-0002", REC_B)
        self._note("work-items", "WI-0003", REC_B)
        code, out = self._wi_check()
        self.assertEqual(code, 1)
        self.assertIn(REC_A, out)
        self.assertIn(REC_B, out)


class OneOwnerPerRecordIsSoundTest(StoreBase):
    """Property B — the negative control."""

    def test_distinct_records_under_distinct_ids_are_sound(self):
        self._note("work-items", "WI-0001", REC_A)
        self._note("work-items", "WI-0002", REC_B)
        code, out = self._wi_check()
        self.assertEqual(code, 0, f"a store with one owner per record is sound:\n{out}")
        self.assertIn("sound", out)

    def test_an_empty_notes_tree_is_sound(self):
        code, out = self._wi_check()
        self.assertEqual(code, 0, out)


class TheOpsStoreIsCheckedByTheSamePassTest(StoreBase):
    """Property D — one validator, both namespaces."""

    def test_a_duplicated_ops_record_fails_the_same_check(self):
        (self.root / "ops-items").mkdir(parents=True, exist_ok=True)
        for n in (1, 2):
            (self.root / "ops-items" / f"OPS-{n:04d}-x.md").write_text(
                _ops_text(f"OPS-{n:04d}", f"obligation {n}"), encoding="utf-8")
        self._note("ops-items", "OPS-0001", REC_A)
        self._note("ops-items", "OPS-0002", REC_A)
        code, out = self._wi_check()
        self.assertEqual(code, 1, f"the ops store gets the same check:\n{out}")
        self.assertIn("OPS-0001", out)
        self.assertIn("OPS-0002", out)

    def test_a_wi_record_and_an_ops_record_may_share_nothing_but_are_not_confused(self):
        # Same record name in two DIFFERENT stores is still two owners of one record —
        # the stamp is globally unique, so this is the cross-store instance of A.
        (self.root / "ops-items").mkdir(parents=True, exist_ok=True)
        (self.root / "ops-items" / "OPS-0001-x.md").write_text(
            _ops_text("OPS-0001", "obligation"), encoding="utf-8")
        self._note("work-items", "WI-0001", REC_A)
        self._note("ops-items", "OPS-0001", REC_A)
        code, out = self._wi_check()
        self.assertEqual(code, 1, f"a record cannot belong to both stores:\n{out}")


if __name__ == "__main__":
    unittest.main()
