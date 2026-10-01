"""WI-0181 — an append-only note is a RECORD, so two machines appending cannot collide.

The bug this removes was not a disagreement. Two machines each APPENDED a paragraph to the
same item's notes field; neither touched what the other wrote. But both added at the end of
one growing string with an empty common base for the added region, so git had no way to
order two appends and produced a conflict with no mechanical answer — which stopped a trunk
reconcile dead and is what WI-0153 / ADR-0102 was chasing when it was found.

ADR-0102 made that class RESOLVABLE. This makes it IMPOSSIBLE, and the difference is the
whole point of the item, so the acceptance test below asserts an ABSENT conflict rather than
a resolved one: two real checkouts append to the same item, both land, and the merge is
clean because the two writes were never to the same file.

The properties pinned here:

  1. AN APPEND DOES NOT TOUCH THE ITEM FILE. This is the mechanism. Everything else is a
     consequence, and if this regresses the rest is decoration.
  2. THE READER STILL SEES ONE THING. Body then records, in time order, on every surface —
     the storage split must not be visible as a split.
  3. A RENDER WRITES THE BODY, NEVER THE COMPILED VIEW. The one way this design can eat
     itself: fold the records back into the file and the hotspot is rebuilt, silently,
     with the note text now duplicated.
  4. BOTH STORES. `ops-items/` had the identical shape, and `ops-ran`'s run receipt is the
     purest append of the three sites.
"""

import argparse
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _out(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


class NotesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir(parents=True, exist_ok=True)
        (self.tmp / "ops-items").mkdir(parents=True, exist_ok=True)
        self._root, self._cfg = session.ROOT, session.CFG
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT, session.CFG = self._root, self._cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _item(self, wid="WI-0001", title="An item", notes="the authored body"):
        session._wi_write_item({"id": wid, "title": title, "status": "open",
                                "section": "next", "blocked_by": [], "group": "",
                                "source": "", "impact": "fix", "version": "",
                                "notes": notes})
        return wid

    def _append(self, wid, text):
        with redirect_stdout(io.StringIO()):
            session.cmd_wi_edit(argparse.Namespace(
                id=wid, title=None, notes=None, append_notes=text, group=None))

    def _file_text(self, wid):
        return (session._wi_dir() / session._wi_filename_for(wid)).read_text(encoding="utf-8")

    def _read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)


class AnAppendDoesNotTouchTheItemFileTest(NotesBase):
    """Property 1 — the mechanism itself."""

    def test_appending_a_note_leaves_the_item_file_byte_identical(self):
        wid = self._item()
        before = self._file_text(wid)
        self._append(wid, "a finding from another machine")
        self.assertEqual(before, self._file_text(wid),
                         "an append that rewrites the item file can still collide")

    def test_the_note_lands_in_its_own_record(self):
        wid = self._item()
        self._append(wid, "a finding")
        recs = sorted(session._notes_dir(session.WI_DIRNAME, wid).glob("*.md"))
        self.assertEqual(len(recs), 1)
        self.assertIn("a finding", recs[0].read_text(encoding="utf-8"))

    def test_two_appends_are_two_records_not_one_longer_one(self):
        wid = self._item()
        self._append(wid, "first")
        self._append(wid, "second")
        self.assertEqual(len(list(session._notes_dir(session.WI_DIRNAME, wid).glob("*.md"))), 2)

    def test_an_empty_note_writes_nothing(self):
        wid = self._item()
        self.assertIsNone(session._note_append(session.WI_DIRNAME, wid, "   ", "v"))
        self.assertFalse(list(session._notes_dir(session.WI_DIRNAME, wid).glob("*.md")))

    def test_a_note_is_bounded(self):
        wid = self._item()
        session._note_append(session.WI_DIRNAME, wid, "x" * 50000, "v")
        rec = next(iter(session._notes_dir(session.WI_DIRNAME, wid).glob("*.md")))
        self.assertLessEqual(len(rec.read_text(encoding="utf-8")),
                             session.NOTE_MAX_CHARS + 64)


class TheReaderStillSeesOneThingTest(NotesBase):
    """Property 2 — the storage split must not be visible as a split."""

    def test_a_note_ids_timestamp_dominates_its_random_suffix(self):
        """The DETERMINISTIC half of the ordering property, and the one that actually
        detects a regression.

        The integration test below is probabilistic: if note ids ever went back to minute
        granularity, two notes appended in the same minute would differ only by their random
        suffix, so a two-note ordering assertion passes about half the time. It did exactly
        that against a deliberately minute-granular build — the break went UNDETECTED, which
        is how this test came to exist.

        So pin the property at the source: a LATER note with a lexically LARGER random must
        still sort after an earlier one with a smaller random. Only the timestamp can carry
        that, and only if it is finer than the interval being distinguished."""
        t0 = datetime(2026, 8, 23, 17, 30, 5, 100, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 23, 17, 30, 5, 200, tzinfo=timezone.utc)
        first = session._note_id("devbox", t0, "zzzz")
        second = session._note_id("devbox", t1, "aaaa")
        self.assertLess(first, second,
                        "the timestamp must dominate the random suffix, or filename order "
                        "is not time order")

    def test_notes_read_back_as_body_then_records_in_time_order(self):
        wid = self._item(notes="BODY")
        self._append(wid, "FIRST")
        self._append(wid, "SECOND")
        notes = self._read(wid)["notes"]
        self.assertLess(notes.index("BODY"), notes.index("FIRST"))
        self.assertLess(notes.index("FIRST"), notes.index("SECOND"))

    def test_an_item_with_no_records_reads_exactly_as_before(self):
        wid = self._item(notes="just the body")
        self.assertEqual(self._read(wid)["notes"], "just the body")

    def test_wi_show_prints_the_records(self):
        wid = self._item()
        self._append(wid, "a finding worth seeing")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_show(argparse.Namespace(id=wid))
        self.assertIn("a finding worth seeing", buf.getvalue())

    def test_replacing_the_notes_keeps_the_append_only_records_and_says_so(self):
        """`--notes` replaces the authored body. Records are append-only by construction,
        so they survive — and the verb states that rather than leaving the operator to
        discover that `wi-show` still prints text they thought they had replaced."""
        wid = self._item(notes="OLD BODY")
        self._append(wid, "A RECORD")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_edit(argparse.Namespace(
                id=wid, title=None, notes="NEW BODY", append_notes=None, group=None))
        out = buf.getvalue()
        self.assertIn("note record(s) kept", out)
        notes = self._read(wid)["notes"]
        self.assertIn("NEW BODY", notes)
        self.assertNotIn("OLD BODY", notes)
        self.assertIn("A RECORD", notes)


class ARenderWritesTheBodyNeverTheViewTest(NotesBase):
    """Property 3 — the one way this design can eat itself.

    Fold the compiled view back into the item file and the append hotspot is rebuilt, with
    every note now duplicated between the file and its record. It would look harmless: the
    reader still shows the right text, once, because nothing dedupes — right up until two
    machines append again.
    """

    def test_a_parse_render_round_trip_does_not_fold_records_into_the_file(self):
        wid = self._item(notes="BODY")
        self._append(wid, "A RECORD")
        item = self._read(wid)                       # notes are COMPILED here
        self.assertIn("A RECORD", item["notes"])
        session._wi_write_item(item)                 # ...and must not be written back
        self.assertNotIn("A RECORD", self._file_text(wid))
        self.assertIn("BODY", self._file_text(wid))

    def test_the_round_trip_does_not_duplicate_the_note_for_the_reader(self):
        wid = self._item(notes="BODY")
        self._append(wid, "A RECORD")
        session._wi_write_item(self._read(wid))
        self.assertEqual(self._read(wid)["notes"].count("A RECORD"), 1)

    def test_a_title_change_still_migrates_the_body(self):
        """The rename path rewrites the file under a new slug; the body must ride along."""
        wid = self._item(notes="BODY")
        self._append(wid, "A RECORD")
        with redirect_stdout(io.StringIO()):
            session.cmd_wi_edit(argparse.Namespace(
                id=wid, title="A different title", notes=None,
                append_notes=None, group=None))
        self.assertIn("BODY", self._file_text(wid))
        self.assertIn("A RECORD", self._read(wid)["notes"])


class TheOpsStoreHasTheSameShapeTest(NotesBase):
    """Property 4 — `ops-items/` had the identical bug, so it gets the identical fix."""

    def _ops(self, oid="OPS-0001"):
        session._ops_write_item({"id": oid, "title": "An obligation", "status": "open",
                                 "cadence": "quarterly", "last_completed": "",
                                 "last_result": "", "due": "", "group": "", "source": "",
                                 "notes": "the authored body"})
        return oid

    def _ops_file(self, oid):
        return (session._ops_dir() / session._ops_filename_for(oid)).read_text(encoding="utf-8")

    def test_an_ops_append_leaves_the_item_file_byte_identical(self):
        oid = self._ops()
        before = self._ops_file(oid)
        with redirect_stdout(io.StringIO()):
            session.cmd_ops_edit(argparse.Namespace(
                id=oid, title=None, notes=None, append_notes="a finding"))
        self.assertEqual(before, self._ops_file(oid))
        self.assertIn("a finding",
                      session._notes_compiled(session.OPS_DIRNAME, {"id": oid}))

    def test_a_run_receipt_is_a_record(self):
        """`ops-ran --notes` is the purest append of the three sites — a run log — and its
        receipt must not grow the item file. The FIELDS the run changes still do."""
        oid = self._ops()
        with redirect_stdout(io.StringIO()):
            session.cmd_ops_ran(argparse.Namespace(
                id=oid, result="findings", on="2026-07-21", notes="Data did not restore."))
        self.assertNotIn("Data did not restore.", self._ops_file(oid))
        item = next(it for it in session._ops_parse() if it["id"] == oid)
        self.assertIn("Data did not restore.", item["notes"])
        self.assertEqual(item["last_completed"], "2026-07-21")

    def test_an_ops_render_round_trip_does_not_fold_records_into_the_file(self):
        oid = self._ops()
        with redirect_stdout(io.StringIO()):
            session.cmd_ops_edit(argparse.Namespace(
                id=oid, title=None, notes=None, append_notes="A RECORD"))
        item = next(it for it in session._ops_parse() if it["id"] == oid)
        session._ops_write_item(item)
        self.assertNotIn("A RECORD", self._ops_file(oid))


class TwoMachinesAppendingDoNotCollideTest(unittest.TestCase):
    """WI-0181's stated acceptance, end to end and in real git.

    Two checkouts each append a note to the SAME item and both land. The assertion is that
    the merge is CLEAN — not that a conflict was resolved well, but that there was no
    conflict, because the two writes were never to the same file. Run against the old
    one-growing-string shape this fails: both sides add a paragraph at the end of the item
    file with an empty common base, and git cannot order two appends.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.a = self.tmp / "a"
        self.a.mkdir()
        _git(self.a, "init", "-q", "-b", "main")
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            _git(self.a, "config", k, v)
        (self.a / "work-items").mkdir()
        self._root = session.ROOT
        session.ROOT = self.a
        session._wi_write_item({"id": "WI-0001", "title": "Shared", "status": "open",
                                "section": "next", "blocked_by": [], "group": "",
                                "source": "", "impact": "fix", "version": "",
                                "notes": "the authored body"})
        _git(self.a, "add", "-A")
        _git(self.a, "commit", "-qm", "the item")
        # A second checkout of the same repo — the other machine.
        self.b = self.tmp / "b"
        subprocess.run([GIT, "clone", "-q", str(self.a), str(self.b)],
                       check=True, capture_output=True, text=True)
        for k, v in (("user.email", "p@p"), ("user.name", "p"), ("commit.gpgsign", "false")):
            _git(self.b, "config", k, v)

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append_in(self, repo, text):
        session.ROOT = repo
        session._note_append(session.WI_DIRNAME, "WI-0001", text, "v")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"note: {text}")

    def test_both_machines_append_and_the_merge_is_clean(self):
        self._append_in(self.a, "machine A found this")
        self._append_in(self.b, "machine B found this")
        # Pull B's commit into A and merge. No conflict is the assertion.
        _git(self.a, "remote", "add", "b", str(self.b))
        _git(self.a, "fetch", "-q", "b")
        m = subprocess.run([GIT, "-C", str(self.a), "merge", "--no-edit", "b/main"],
                           capture_output=True, text=True)
        self.assertEqual(m.returncode, 0,
                         f"two appends must not conflict:\n{m.stdout}\n{m.stderr}")
        self.assertEqual([], subprocess.run(
            [GIT, "-C", str(self.a), "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True).stdout.split())

        # And BOTH notes survive — a clean merge that dropped one would be worse.
        session.ROOT = self.a
        item = next(it for it in session._wi_parse() if it["id"] == "WI-0001")
        self.assertIn("machine A found this", item["notes"])
        self.assertIn("machine B found this", item["notes"])
        self.assertIn("the authored body", item["notes"])


if __name__ == "__main__":
    unittest.main()
