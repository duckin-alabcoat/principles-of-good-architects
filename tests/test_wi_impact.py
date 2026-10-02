"""Work-item impact labels and the derived release bump (WI-0043, ADR-0081).

Each item carries `impact: fix | feature` — a judgment about SIZE — and a release's
small-number bump is the max label it contains. Separately it may carry
`migration: yes|no`, which says whether shipping forces other members to edit their own
files; that feeds no version number at all.

**The bump can never be "major".** `breaking` used to be a third impact value that
derived one, and operator retired it (2026-07-30), ruling that a major is a change that fundamentally
alters the system, whether by breaking it or by adding new capabilities on top.
Significance is not a property of any item and `max()` cannot compute it, so a major is
declared by naming a milestone. Two failures the old rule produced are pinned below:
cutting items from a named major could silently demote it, and a lone breaking item cut
a major nobody named.

The other property worth defending is the REFUSAL. The original brief floats defaulting
an unlabelled item to `feature`; these tests pin the opposite, because a default here is
invisible when it is wrong — an unclassified item would be counted into a minor nobody
classified it into, and the release would look computed when it was guessed. So: unset
is its own state, the bump returns None rather than a guess, and the unlabelled ids come
back so a human can label them.
"""

import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def _item(wid, **kw):
    it = {"id": wid, "title": f"Item {wid}", "status": "open", "section": "next",
          "blocked_by": [], "group": "", "source": "", "impact": "", "migration": "",
          "version": "", "notes": ""}
    it.update(kw)
    return it


class BumpTest(unittest.TestCase):
    def test_the_bump_is_never_major_however_big_the_set(self):
        """ADR-0081. A major is declared by naming a milestone, never computed here."""
        items = [_item(f"WI-{i:04d}", impact="feature") for i in range(1, 20)]
        self.assertEqual(session._wi_derive_bump(items), ("minor", []))

    def test_cutting_items_cannot_silently_demote_a_named_major(self):
        """One of the two failures that retired `breaking`.

        Under the old rule a named major kept its number only while some item in it
        happened to carry `breaking`. Cut those as the ship date approached — which is
        exactly what a date is *for* — and the derivation quietly returned "minor" for a
        release already promised outward. Now the derivation has no opinion about majors
        at all, so cutting cannot reach the promise.
        """
        before = [_item("WI-0001", impact="feature"), _item("WI-0002", impact="feature")]
        after_cutting = [_item("WI-0001", impact="feature")]
        self.assertEqual(session._wi_derive_bump(before)[0], "minor")
        self.assertEqual(session._wi_derive_bump(after_cutting)[0], "minor")

    def test_feature_wins_over_fix(self):
        items = [_item("WI-0001", impact="fix"), _item("WI-0002", impact="feature")]
        self.assertEqual(session._wi_derive_bump(items), ("minor", []))

    def test_only_fixes_is_a_patch(self):
        items = [_item("WI-0001", impact="fix"), _item("WI-0002", impact="fix")]
        self.assertEqual(session._wi_derive_bump(items), ("patch", []))

    def test_an_unlabelled_item_refuses_the_bump_and_names_itself(self):
        # THE test. A default would have quietly returned "minor" here and shipped a
        # release number nobody computed.
        items = [_item("WI-0001", impact="feature"), _item("WI-0002")]
        bump, unlabelled = session._wi_derive_bump(items)
        self.assertIsNone(bump)
        self.assertEqual(unlabelled, ["WI-0002"])

    def test_refusal_reports_every_unlabelled_id_not_just_the_first(self):
        items = [_item("WI-0001"), _item("WI-0002", impact="fix"), _item("WI-0003")]
        bump, unlabelled = session._wi_derive_bump(items)
        self.assertIsNone(bump)
        self.assertEqual(unlabelled, ["WI-0001", "WI-0003"])

    def test_an_unlabelled_item_cannot_be_masked_by_a_labelled_sibling(self):
        # Even when the answer looks obvious — a feature is present, so surely it's a
        # minor — the unlabelled item still blocks. "Obvious" is how a guess gets in.
        items = [_item("WI-0001", impact="feature"), _item("WI-0002")]
        self.assertEqual(session._wi_derive_bump(items)[0], None)

    def test_migration_does_not_touch_the_bump(self):
        """The half of `breaking` that survived is a warning, not a version input.

        A migration-forcing repair is still a patch: members editing their files is a
        deployment fact, not a statement about how big the change was.
        """
        items = [_item("WI-0001", impact="fix", migration="yes")]
        self.assertEqual(session._wi_derive_bump(items), ("patch", []))

    def test_empty_release_is_a_patch_not_a_crash(self):
        self.assertEqual(session._wi_derive_bump([]), ("patch", []))


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir()
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, item):
        session._wi_write_item(item)

    def read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)


class RoundTripTest(StoreBase):
    def test_impact_and_version_survive_a_write_read_cycle(self):
        self.write(_item("WI-0001", impact="feature", version="2.0.0", status="done"))
        got = self.read("WI-0001")
        self.assertEqual(got["impact"], "feature")
        self.assertEqual(got["version"], "2.0.0")

    def test_migration_survives_a_write_read_cycle(self):
        self.write(_item("WI-0001", impact="feature", migration="yes"))
        self.assertEqual(self.read("WI-0001")["migration"], "yes")

    def test_no_migration_line_is_written_when_unset(self):
        # The common case is ~50 items with nothing to say; a "migration: no" line on
        # every one of them is noise in the file and in every diff that touches it.
        self.write(_item("WI-0001", impact="fix"))
        name = session._wi_filename_for("WI-0001")
        text = (self.tmp / "work-items" / name).read_text(encoding="utf-8")
        self.assertNotIn("migration:", text)
        self.assertEqual(self.read("WI-0001")["migration"], "")

    def test_unset_stays_unset_and_does_not_become_a_default(self):
        self.write(_item("WI-0001"))
        self.assertEqual(self.read("WI-0001")["impact"], "")
        self.assertEqual(self.read("WI-0001")["version"], "")

    def test_an_older_file_without_the_fields_still_parses(self):
        # Items written before this schema existed have no impact/version lines at all.
        # They must read back as UNSET rather than crashing the parser — the store is
        # append-only history and predates every field it will ever gain.
        (self.tmp / "work-items" / "WI-0009-legacy.md").write_text(
            "# WI-0009: An item from before impact existed\n\n"
            "- status: open\n- section: next\n- blocked-by: \n- group: \n- source: \n\n",
            encoding="utf-8")
        got = self.read("WI-0009")
        self.assertEqual(got["impact"], "")
        self.assertEqual(got["status"], "open")


class ValidateTest(StoreBase):
    def test_unlabelled_item_is_reported_as_debt(self):
        self.write(_item("WI-0001"))
        problems = session._wi_validate()
        self.assertTrue(any("no impact label" in p for p in problems))

    def test_a_bogus_label_is_rejected(self):
        self.write(_item("WI-0001", impact="urgent"))
        self.assertTrue(any("is not one of" in p for p in session._wi_validate()))

    def test_the_retired_breaking_label_is_rejected_with_where_it_went(self):
        # Every item in the store carried a valid label until ADR-0081 retired one of
        # them. An operator hitting this is holding a label that USED to be right, so a
        # bare "not one of" would be a riddle — name both halves of the split.
        self.write(_item("WI-0001", impact="breaking"))
        problems = session._wi_validate()
        self.assertTrue(any("ADR-0081" in p and "--migration yes" in p for p in problems))

    def test_a_bogus_migration_value_is_rejected(self):
        self.write(_item("WI-0001", impact="fix", migration="maybe"))
        self.assertTrue(any("migration 'maybe'" in p for p in session._wi_validate()))

    def test_a_superseded_item_needs_no_label(self):
        # Superseded work never ships, so it can never appear in a release and has
        # nothing to classify. Demanding a label there would be permanent nagging debt.
        self.write(_item("WI-0001", status="superseded",
                         notes="Folded into WI-0002."))
        self.assertFalse(any("no impact label" in p for p in session._wi_validate()))

    def test_a_version_on_an_open_item_is_a_problem(self):
        # A version is stamped BY a release onto shipped work. On an open item it is
        # either a hand-edit or a bug, and both mean the release record is not the
        # single writer of that fact.
        self.write(_item("WI-0001", status="open", impact="fix", version="1.2.0"))
        self.assertTrue(any("is not closed" in p for p in session._wi_validate()))

    def test_a_version_on_a_closed_item_is_fine(self):
        self.write(_item("WI-0001", status="done", impact="fix", version="1.2.0"))
        self.assertFalse(any("is not closed" in p for p in session._wi_validate()))


class CliTest(StoreBase):
    def _status(self, wid, **kw):
        kw.setdefault("status", None)
        kw.setdefault("section", None)
        kw.setdefault("blocked_by", None)
        kw.setdefault("source", None)
        kw.setdefault("impact", None)
        kw.setdefault("migration", None)
        kw["id"] = wid
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_status(type("A", (), kw)())
        return buf.getvalue()

    def test_status_sets_a_label(self):
        self.write(_item("WI-0001"))
        out = self._status("WI-0001", impact="feature")
        self.assertIn("impact=feature", out)
        self.assertEqual(self.read("WI-0001")["impact"], "feature")

    def test_status_sets_migration_independently_of_impact(self):
        self.write(_item("WI-0001", impact="fix"))
        out = self._status("WI-0001", migration="yes")
        self.assertIn("migration=yes", out)
        got = self.read("WI-0001")
        self.assertEqual(got["migration"], "yes")
        self.assertEqual(got["impact"], "fix")      # untouched by the other axis

    def test_a_bogus_migration_value_exits_nonzero_without_writing(self):
        self.write(_item("WI-0001", impact="fix", migration="yes"))
        with self.assertRaises(SystemExit) as cm:
            self._status("WI-0001", migration="sometimes")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.read("WI-0001")["migration"], "yes")   # unchanged

    def test_empty_string_clears_a_label_back_to_unset(self):
        # Withdrawing a label must be possible: an operator who realises a call was a
        # guess needs to un-say it, not overwrite it with a second guess.
        self.write(_item("WI-0001", impact="feature"))
        out = self._status("WI-0001", impact="")
        self.assertIn("impact=(unset)", out)
        self.assertEqual(self.read("WI-0001")["impact"], "")

    def test_a_bogus_label_exits_nonzero_without_writing(self):
        self.write(_item("WI-0001", impact="fix"))
        with self.assertRaises(SystemExit) as cm:
            self._status("WI-0001", impact="nonsense")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.read("WI-0001")["impact"], "fix")   # unchanged


if __name__ == "__main__":
    unittest.main()
