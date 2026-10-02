"""WI-0095 — flagging items whose cited code has moved since their notes were written.

The defect this answers: an item's notes are written when it is FILED, and nothing
updates them when adjacent work incidentally satisfies them. Of six items picked up in
session ~125, three were already partly or wholly shipped and still read as open work.
The cost is not the wasted hour — it is a SECOND implementation built beside the first.

These drive real git rather than mocking it, because every property that matters is a
property of what git actually reports: that a rewritten symbol is seen, that an
unchanged one is not, and that an English word in backticks is never mistaken for
deleted code. The last is what separates a metric from noise.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from coord_fixture import neutralize_live_store  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class WiStaleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / session.WI_DIRNAME).mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        self.mod = self.repo / "mod.py"
        self.mod.write_text(
            "def target():\n    return 1\n\n\n"
            "def cmd_wi_render():\n    return 'render'\n\n\n"
            "class Holder:\n    def indented_method(self):\n        return 2\n",
            encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        # The helpers shell out to git in the PROCESS cwd, so the test stands in the
        # throwaway repo rather than the operator's own.
        self._cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self._cwd)
        self._pd = mock.patch.object(session, "_wi_dir",
                                     lambda base=None: self.repo / session.WI_DIRNAME)
        self._pd.start()
        self.addCleanup(self._pd.stop)
        # The item store is not the only store the probe reads. Note records
        # (`_notes_records`) and the ops store (`_ops_parse`) resolve from `ROOT` and
        # `_shared_work_root()`, so without these the report read the operator's real
        # `work-items/notes/` and `ops-items/` (ADR-0148 D3 store guard, WI-0427).
        neutralize_live_store(self, root=self.repo)
        self._root = mock.patch.object(session, "ROOT", self.repo)
        self._root.start()
        self.addCleanup(self._root.stop)

    def _file_item(self, notes, wid="WI-0001", status="open", commit=True,
                   blocked_by="", probed=""):
        name = f"{wid}-an-item.md"
        (self.repo / session.WI_DIRNAME / name).write_text(
            f"# {wid}: an item\n\n- status: {status}\n- section: next\n"
            f"- blocked-by: {blocked_by}\n- group: \n- source: \n- impact: fix\n"
            f"- version: \n" + (f"- probed: {probed}\n" if probed else "") +
            f"\n{notes}\n", encoding="utf-8")
        if commit:
            _git(self.repo, "add", "-A")
            _git(self.repo, "commit", "-qm", f"file {wid}")
        return name

    def _rewrite_target(self):
        self.mod.write_text(
            self.mod.read_text(encoding="utf-8").replace(
                "def target():\n    return 1", "def target():\n    return 99"),
            encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "rewrite target")

    def _move_target(self, dest="pkg/moved.py", edit=False, msg="move target"):
        """Move `target`'s definition to another file, VERBATIM unless `edit`.

        The ADR-0118 split in miniature: a symbol arrives at a path that did not exist
        before, and `git log -L` on the new path can only report the move itself.
        """
        src = self.mod.read_text(encoding="utf-8")
        block = "def target():\n    return 1\n"
        assert block in src, "fixture drifted"
        moved = block.replace("return 1", "return 99") if edit else block
        self.mod.write_text(src.replace(block, ""), encoding="utf-8")
        out = self.repo / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(moved, encoding="utf-8")
        self._commit(msg)

    def _report(self):
        """Only the rows carrying a FINDING. Since WI-0308 the report also returns clean
        rows — the stamp pass needs them, and a report that dropped them could not tell
        'probed and fine' from 'never looked at'. Every assertion here is about findings,
        so the filter belongs in the helper rather than in each test."""
        return [r for r in session._wi_stale_report() if not r.get("clean")]

    def _all(self):
        return session._wi_stale_report()

    def _row(self, wid="WI-0001"):
        return next(r for r in self._all() if r["id"] == wid)

    def _commit(self, msg="change"):
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg)

    def _file_adr(self, num="0042", status="Accepted", slug="a-decision", commit=True):
        d = self.repo / session._adr_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{num}-{slug}.md"
        p.write_text(f"# ADR-{num}: a decision\n\n**Status:** {status}\n\nBody.\n",
                     encoding="utf-8")
        if commit:
            self._commit(f"adr {num} {status}")
        return p

    def _defs(self, symbol, rev=None):
        """Always against the throwaway repo. Asking without a repo would ask ROOT —
        the operator's own checkout — which is the defect the `repo` parameter exists
        to prevent, so the tests must not model the wrong call."""
        return session._wi_symbol_defs(symbol, rev, repo=self.repo)

    def _file_ops(self, oid="OPS-0001", status="open", commit=True):
        d = self.repo / session.OPS_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{oid}-an-obligation.md").write_text(
            f"# {oid}: an obligation\n\n- status: {status}\n- cadence: \n"
            f"- last-completed: \n- last-result: \n- due: \n- group: \n- source: \n\n"
            f"Notes.\n", encoding="utf-8")
        if commit:
            self._commit(f"file {oid}")


class CitedAdrTest(WiStaleBase):
    """The premise class the symbol probe is structurally blind to. An ADR is immutable
    once Accepted except to flip status, so it changes UNDER a citing item that never
    moves — and the item goes on standing on a decision that has been superseded."""

    def test_an_adr_that_flipped_status_since_the_notes_is_flagged(self):
        self._file_adr(status="Accepted")
        self._file_item("Per ADR-0042 this is settled.")
        self._file_adr(status="Superseded by ADR-0099")
        row = self._row()
        self.assertEqual([a[0] for a in row["adr_changed"]], ["ADR-0042"])
        self.assertEqual(row["adr_changed"][0][1], "Superseded by ADR-0099",
                         "the current status is the actionable half of the finding")

    def test_an_unchanged_adr_is_not_flagged(self):
        self._file_adr()
        self._file_item("Per ADR-0042 this is settled.")
        (self.repo / "unrelated.txt").write_text("noise\n", encoding="utf-8")
        self._commit("unrelated")
        self.assertEqual(self._row()["adr_changed"], [])

    def test_an_adr_cited_before_it_existed_is_not_reported_gone(self):
        """The forward reference — a plan naming the ADR it intends to write. Calling
        that a deletion is the same noise as an English word in backticks."""
        self._file_item("This will be recorded as ADR-0077.")
        self._rewrite_target()
        self.assertEqual(self._row()["adr_gone"], [])

    def test_an_adr_that_resolved_and_no_longer_does_is_flagged(self):
        p = self._file_adr(num="0042")
        self._file_item("Per ADR-0042.")
        p.unlink()
        self._commit("remove the adr")
        self.assertEqual(self._row()["adr_gone"], ["ADR-0042"])

    def test_a_status_the_record_does_not_carry_is_not_invented(self):
        """`declare-what-a-check-assumes`: an ADR with no Status line must report as
        unknown, never default to Accepted — defaulting would certify exactly the
        supersession this probe exists to find."""
        d = self.repo / session._adr_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / "0042-a-decision.md"
        p.write_text("# ADR-0042\n\nNo status line here.\n", encoding="utf-8")
        self._commit("adr without a status")
        self._file_item("Per ADR-0042.")
        p.write_text("# ADR-0042\n\nStill no status line.\n", encoding="utf-8")
        self._commit("edit it")
        self.assertEqual(self._row()["adr_changed"][0][1], "")


class CitedIdTest(WiStaleBase):
    """The highest-yield class: an id is the one citation this store can resolve
    exactly. Session 241 found WI-0296 closed under its citers and WI-0217 naming a
    blocker that had been renumbered away."""

    def test_a_cited_item_that_closed_since_is_flagged_with_both_statuses(self):
        self._file_item("Depends on the fix in WI-0002.", wid="WI-0002")
        self._file_item("Depends on the fix in WI-0002.")
        self._file_item("Depends on the fix in WI-0002.", wid="WI-0002", status="done")
        self.assertEqual(self._row()["id_changed"], [("WI-0002", "open", "done")])

    def test_a_cited_item_whose_status_held_is_not_flagged(self):
        self._file_item("noise", wid="WI-0002")
        self._file_item("Depends on WI-0002.")
        (self.repo / "unrelated.txt").write_text("x\n", encoding="utf-8")
        self._commit("unrelated")
        self.assertEqual(self._row()["id_changed"], [])

    def test_a_cited_item_already_closed_when_written_is_not_news(self):
        self._file_item("noise", wid="WI-0002", status="done")
        self._file_item("Absorbed into WI-0002, which is already done.")
        self._rewrite_target()
        self.assertEqual(self._row()["id_changed"], [])

    def test_an_id_renumbered_out_from_under_the_citation_is_flagged(self):
        """WI-0217's measured case: the blocker it named was renumbered away, and
        nothing said so."""
        self._file_item("noise", wid="WI-0002")
        self._file_item("Blocked behind WI-0002.")
        (self.repo / session.WI_DIRNAME / "WI-0002-an-item.md").unlink()
        self._commit("renumber WI-0002 away")
        self.assertEqual(self._row()["id_gone"], [("WI-0002", "open")])

    def test_an_id_that_never_existed_is_left_to_wi_check(self):
        """A number this tree never held is `wi-check`'s subject, diagnosed there with
        the lane/draw/dangling distinction. Guessing at it here would be a second
        half-diagnosis of the same fact (P16)."""
        self._file_item("Blocked behind WI-0999.")
        self._rewrite_target()
        self.assertEqual(self._row()["id_gone"], [])

    def test_an_item_never_cites_itself(self):
        self._file_item("WI-0001 is this very item.")
        self._rewrite_target()
        row = self._row()
        self.assertEqual((row["id_changed"], row["id_gone"]), ([], []))

    def test_an_ops_citation_resolves_in_its_own_namespace(self):
        """ADR-0076 D7 supersedes work items INTO the ops namespace, so a WI-only map
        would report every OPS citation as a dangling id — a fabricated finding in the
        verb built to stop fabricated premises."""
        self._file_ops("OPS-0001")
        self._file_item("Runs under OPS-0001.")
        self._rewrite_target()
        row = self._row()
        self.assertEqual((row["id_changed"], row["id_gone"]), ([], []))


class BlockerTest(WiStaleBase):
    def test_a_blocker_that_has_closed_is_reported(self):
        self._file_item("noise", wid="WI-0002")
        self._file_item("Waits on the other one.", blocked_by="WI-0002")
        self._file_item("noise", wid="WI-0002", status="done")
        self.assertEqual(self._row()["blockers"], [("WI-0002", "done")])

    def test_a_blocker_still_open_is_not_reported(self):
        self._file_item("noise", wid="WI-0002")
        self._file_item("Waits on the other one.", blocked_by="WI-0002")
        self.assertEqual(self._row()["blockers"], [])

    def test_a_blocker_that_does_not_resolve_is_left_to_wi_check(self):
        self._file_item("Waits on a ghost.", blocked_by="WI-0999")
        self.assertEqual(self._row()["blockers"], [])

    def test_the_blocker_probe_does_not_depend_on_the_watermark(self):
        """Unlike every other probe here. "I am blocked" is a claim about NOW, so a
        blocker that closed BEFORE these notes were written is still a live finding —
        the item is sitting in the wrong state either way."""
        self._file_item("noise", wid="WI-0002", status="done")
        self._file_item("Waits on something already finished.", blocked_by="WI-0002")
        self.assertEqual(self._row()["blockers"], [("WI-0002", "done")])


class QuotedCountTest(WiStaleBase):
    def test_a_quoted_ratio_is_listed(self):
        self._file_item("MEASURED: 84 of 104 premises were stale.")
        self.assertEqual(len(self._row()["counts"]), 1)
        self.assertIn("84 of 104", self._row()["counts"][0])

    def test_the_word_form_is_listed_too(self):
        self._file_item("Four of eighteen is not noise.")
        self.assertEqual(len(self._row()["counts"]), 1)

    def test_a_bare_number_is_not_a_quoted_count(self):
        """Measured before building: the generic "<number> <word>" shape is 2322
        occurrences across the live store, overwhelmingly dates, id fragments and design
        parameters. Reporting those makes the section unreadable and therefore unread."""
        self._file_item("Retries 3 attempts over 14 days, filed 2026-09-04.")
        self.assertEqual(self._row()["counts"], [])

    def test_a_quoted_count_never_makes_an_item_unclean(self):
        """The whole point of the separation. The item has done nothing wrong by quoting
        a measurement and there is no edit that resolves the line, so it is a declared
        limit of the tool, not a verdict on the item — and it must not hold back the
        stamp, or one probe run would silence a question it never answered."""
        self._file_item("MEASURED: 84 of 104 premises were stale.")
        self.assertTrue(self._row()["clean"])

    def test_the_clause_around_the_match_is_shown_not_the_head_of_the_line(self):
        """Item bodies are long single-line paragraphs. Clipping from the line start put
        the same opening sentence beside three different counts."""
        self._file_item("OPENING SENTENCE that runs on and on and on for a while yet, "
                        "and only much later says 4 of 6 rather than 3.")
        self.assertNotIn("OPENING SENTENCE", self._row()["counts"][0])
        self.assertIn("4 of 6", self._row()["counts"][0])


class ProbeStampTest(WiStaleBase):
    def _stale(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_wi_stale(mock.Mock(**kw))
        return buf.getvalue()

    def _text(self, wid="WI-0001"):
        return (self.repo / session.WI_DIRNAME /
                f"{wid}-an-item.md").read_text(encoding="utf-8")

    def test_a_clean_item_is_stamped(self):
        self._file_item("Nothing cited here at all.")
        self._stale(stamp=True)
        self.assertIn("- probed: ", self._text())

    def test_a_flagged_item_is_never_stamped(self):
        """The receipt must certify only what was actually checked. Stamping an item
        that came back with a finding would record a pass that never happened."""
        self._file_item("The fix lives in `target`.")
        self._rewrite_target()
        self._stale(stamp=True)
        self.assertNotIn("- probed: ", self._text())

    def test_a_second_run_the_same_day_writes_nothing(self):
        """The stamp lands in the item FILE, so writing it moves that file's last commit
        — which is where the watermark is read from. Comparing the full stamp meant every
        run saw a HEAD its own previous run had advanced, and re-stamped all 53 clean
        items forever: a commit per run whose only content was the receipt for that
        commit. Skipping on the DATE is what makes the pass idempotent."""
        self._file_item("Nothing cited here at all.")
        self._stale(stamp=True)
        first = self._text()
        self._stale(stamp=True)
        self.assertEqual(first, self._text())

    def test_no_stamp_writes_nothing(self):
        before = self._file_item("Nothing cited here at all.") and self._text()
        self._stale(stamp=False)
        self.assertEqual(before, self._text())

    def test_the_stamp_round_trips_through_parse_and_render(self):
        self._file_item("Nothing cited.", probed="2026-09-10 abc1234")
        item = next(it for it in session._wi_parse() if it["id"] == "WI-0001")
        self.assertEqual(item["probed"], "2026-09-10 abc1234")
        self.assertIn("- probed: 2026-09-10 abc1234",
                      session._wi_render_item(item))

    def test_an_uncommitted_stamp_still_moves_the_as_of_commit(self):
        """The case where the stamp is doing real work as a watermark, and the only one:
        `_store_autocommit` FAILS OPEN, so a stamp can be on disk with the item file
        still uncommitted. The notes watermark is then the old commit and the stamp names
        a newer one — and measuring from the older would re-report staleness that was
        already probed away. (In the ordinary path the stamp's own commit moves the notes
        watermark to the same place, so the two agree and this never arises.)"""
        self._file_item("The fix lives in `target`.")
        old = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._rewrite_target()
        head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._file_item("The fix lives in `target`.", commit=False,
                        probed=f"2026-09-10 {head[:7]}")
        self.assertEqual(self._row()["watermark"], head[:7])
        self.assertNotEqual(self._row()["watermark"], old)
        self.assertEqual(self._row()["changed"], [],
                         "the rewrite happened before the stamp, so it is not new news")

    def test_a_note_written_after_the_stamp_beats_the_stamp(self):
        """The stamp cannot simply win. Material added after a probe is material the
        probe never saw, so the honest 'as of' falls back to the note — otherwise one
        run would silence an item permanently, which is the false-green this widening
        exists to remove."""
        self._file_item("Nothing cited.")
        stale_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._file_item("Now it cites `target`.", probed=f"2026-09-10 {stale_head[:7]}")
        notes_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(self._row()["watermark"], notes_head,
                         "the later of the two commits is the honest 'as of'")

    def test_a_stamp_naming_an_unknown_commit_falls_back(self):
        self._file_item("Nothing cited.", probed="2026-09-10 deadbee")
        self.assertIsNotNone(self._row()["watermark"])
        self.assertNotEqual(self._row()["watermark"], "deadbee")


class ProbeScopeTest(WiStaleBase):
    def test_an_in_progress_item_is_probed(self):
        """The old status list named `open` and `blocked` — and `blocked` is not one of
        WI_STATUSES, so it matched nothing, while `in-progress` and `held` (the items
        whose premises are about to be leaned on) were never named at all."""
        self._file_item("The fix lives in `target`.", status="in-progress")
        self._rewrite_target()
        self.assertEqual([r["id"] for r in self._report()], ["WI-0001"])

    def test_a_held_item_is_probed(self):
        self._file_item("The fix lives in `target`.", status="held")
        self._rewrite_target()
        self.assertEqual([r["id"] for r in self._report()], ["WI-0001"])

    def test_a_superseded_item_is_not_probed(self):
        self._file_item("The fix lives in `target`.", status="superseded")
        self._rewrite_target()
        self.assertEqual(self._all(), [])

    def test_the_cap_is_applied_after_the_status_filter(self):
        """The measured false-green: the cap used to slice the whole store before the
        status filter ran, so with 310 files and a cap of 200 everything above WI-0200
        was reported as 'no staleness' having never been read — 27 items probed out of
        78 open."""
        for n in range(2, 8):
            self._file_item("closed", wid=f"WI-{n:04d}", status="done", commit=False)
        self._file_item("The fix lives in `target`.", wid="WI-0009")
        self._rewrite_target()
        with mock.patch.object(session, "WI_STALE_ITEM_CAP", 3):
            rows = session._wi_stale_report()
        self.assertEqual([r["id"] for r in rows], ["WI-0009"],
                         "the live item must be probed even though six done items sit "
                         "ahead of it and the cap is only three")


class DefinitionSearchTest(WiStaleBase):
    def test_a_module_level_def_is_found(self):
        self.assertEqual(self._defs("target"), ["mod.py"])

    def test_an_indented_method_is_found(self):
        """The regression that hid the whole feature: the first pattern used Python's
        `\\s` and `\\b`, which POSIX ERE does not support — `git grep -E` did not error,
        it silently matched NOTHING. Every item came back clean and the checker read as
        'no staleness anywhere' while being completely inert."""
        self.assertEqual(self._defs("indented_method"), ["mod.py"])

    def test_a_prefix_is_not_a_match(self):
        self.mod.write_text(self.mod.read_text(encoding="utf-8") +
                            "\n\ndef target_extra():\n    return 3\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "add lookalike")
        # `target` must not match `target_extra`, or every short name flags forever.
        self.assertEqual(self._defs("target_extra"), ["mod.py"])

    def test_an_english_word_resolves_to_nothing(self):
        self.assertEqual(self._defs("predates"), [])


class RenamedSymbolTest(WiStaleBase):
    """WI-0335 — a symbol that only CHANGED FILE is not a rewritten symbol.

    Measured on the live repo before this was built: `git log -L:sym:path` cannot follow
    a rename (`-M` and `--find-renames` make no difference to its answer), so the
    ADR-0118 `session.py` -> `sessionlib/` split read as a rewrite of every symbol it
    moved — 668 of the 675 `sessionlib/` symbols that predate it, against 64 that had
    really changed. A report that fires on 99% of what it looks at is one a reader learns
    to skip, which costs more than the staleness it was built to surface.

    The false-negative tests are the ones that matter most. The obvious fix — drop the
    split's sha from `-L`'s answer — would have made genuinely rewritten symbols read
    CLEAN, because `-L`'s walk stops at the file's creation and never sees a rewrite that
    happened before the move. Fourteen live instances were counted, `cmd_wi_edit` and
    `_ops_parse` among them: really rewritten at `b03aef05`, reported by `-L` on the
    current path as nothing but the split.
    """

    def test_a_symbol_that_only_moved_file_is_not_flagged(self):
        self._file_item("The fix lives in `target`.")
        self._move_target()
        self.assertEqual(self._report(), [],
                         "a verbatim move is not a rewrite of the definition")

    def test_code_inserted_next_to_a_symbol_does_not_flag_it(self):
        """The third `-L` imprecision, found while measuring the first two.

        `_wi_reserve_next`'s body is byte-identical at `ab7b101c^` and `ab7b101c`, and
        `-L` reported that commit anyway: the insertion landed immediately after the
        function, so git's `@@` hunk header carried its name. Line-range tracking is not
        definition tracking.
        """
        self._file_item("The fix lives in `target`.")
        self.mod.write_text(
            self.mod.read_text(encoding="utf-8").replace(
                "def target():\n    return 1\n",
                "def target():\n    return 1\n\n\ndef neighbour():\n    return 7\n"),
            encoding="utf-8")
        self._commit("insert a neighbour")
        self.assertEqual(self._report(), [],
                         "a symbol's neighbours are not its definition")

    def test_a_rewrite_before_a_move_is_still_flagged(self):
        """The false negative the cheap fix would have introduced."""
        self._file_item("The fix lives in `target`.")
        self._rewrite_target()          # the real rewrite, on the OLD path
        self.mod.write_text(            # ...then carried to a new file untouched
            self.mod.read_text(encoding="utf-8").replace(
                "def target():\n    return 99\n", ""), encoding="utf-8")
        (self.repo / "moved.py").write_text("def target():\n    return 99\n",
                                            encoding="utf-8")
        self._commit("move after the rewrite")
        rows = self._report()
        self.assertEqual([c[0] for c in rows[0]["changed"]], ["target"],
                         "the rewrite predates the move; it must survive it")

    def test_the_pre_move_rewrite_is_named_in_the_evidence(self):
        """A finding whose only cited commit is the move sends the reader to the wrong
        diff, which is the same distrust in a quieter costume."""
        self._file_item("The fix lives in `target`.")
        self._rewrite_target()
        real = _git(self.repo, "rev-parse", "--short", "HEAD").stdout.strip()
        self.mod.write_text(
            self.mod.read_text(encoding="utf-8").replace(
                "def target():\n    return 99\n", ""), encoding="utf-8")
        (self.repo / "moved.py").write_text("def target():\n    return 99\n",
                                            encoding="utf-8")
        self._commit("move after the rewrite")
        shas = self._row()["changed"][0][2]
        self.assertIn(real, shas, f"pre-move rewrite {real} missing from {shas}")

    def test_a_missing_sha_never_softens_the_verdict(self):
        """Evidence is best-effort; the verdict is not.

        `-L` is stubbed to find nothing — the state a second move, or any history it
        cannot walk, puts it in. The definition still differs across the window, so the
        finding stands with no commit attached. Folding "git could not name a commit"
        into "nothing changed" is the false-green this whole verb exists to remove.
        """
        self._file_item("The fix lives in `target`.")
        self._rewrite_target()
        real = session.sh

        def no_line_log(argv, *a, **kw):
            if any(str(x).startswith("-L:") for x in argv):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real(argv, *a, **kw)

        with mock.patch.object(session, "sh", no_line_log):
            row = self._row()
        self.assertEqual([c[0] for c in row["changed"]], ["target"])
        self.assertEqual(row["changed"][0][2], [])

    def test_the_printer_says_so_when_no_commit_can_be_named(self):
        """"0 commit(s)" would read as a contradiction of the finding it sits under."""
        rows = [{"id": "WI-0001", "title": "t", "watermark": "abcdef123", "unknown": False,
                 "clean": False, "changed": [("target", "mod.py", [])], "gone": [],
                 "adr_changed": [], "adr_gone": [], "id_changed": [], "id_gone": [],
                 "blockers": [], "counts": [], "age_days": None}]
        buf = io.StringIO()
        with mock.patch.object(session, "_wi_stale_report", lambda *a, **k: rows), \
                contextlib.redirect_stdout(buf):
            session.cmd_wi_stale(mock.Mock(stamp=False))
        self.assertIn("could not name which commit", buf.getvalue())
        self.assertNotIn("0 commit(s)", buf.getvalue())

    def test_a_move_that_also_rewrites_is_flagged(self):
        self._file_item("The fix lives in `target`.")
        self._move_target(edit=True, msg="move and rewrite")
        self.assertEqual([c[0] for c in self._row()["changed"]], ["target"])

    def test_a_symbol_is_reported_once_even_when_two_files_define_it(self):
        """The verdict is about the definition, not about a path, so it is asked once."""
        self._file_item("The fix lives in `target`.")
        (self.repo / "second.py").write_text("def target():\n    return 1\n",
                                             encoding="utf-8")
        self._commit("a second definition")
        self._rewrite_target()
        self.assertEqual([c[0] for c in self._row()["changed"]], ["target"])


class DefBlockTest(WiStaleBase):
    """The extractor the verdict stands on. It reads files at arbitrary historical revs,
    so every failure here reads as a silent 'nothing changed'."""

    SRC = ("def before():\n    return 0\n\n\n"
           "@decorated\n"
           "def target(a):\n"
           "    if a:\n"
           "        return 1\n\n"
           "    return 2\n\n\n"
           "def after():\n    return 3\n")

    def test_the_block_stops_at_the_next_definition(self):
        self.assertEqual(session._py_def_block(self.SRC, "target"),
                         "def target(a):\n    if a:\n        return 1\n\n    return 2")

    def test_an_interior_blank_line_stays_in_the_body(self):
        self.assertIn("\n\n    return 2", session._py_def_block(self.SRC, "target"))

    def test_a_decorator_is_not_part_of_the_definition(self):
        self.assertNotIn("@decorated", session._py_def_block(self.SRC, "target"))

    def test_a_prefix_is_not_a_match(self):
        self.assertIsNone(session._py_def_block("def target_extra():\n    pass\n",
                                                "target"))

    def test_an_absent_symbol_is_none_not_empty(self):
        """None and "" must not collapse: "" would compare equal to another miss and
        certify two unreadable revs as an unchanged definition."""
        self.assertIsNone(session._py_def_block(self.SRC, "nowhere"))

    def test_an_indented_method_keeps_its_own_indentation(self):
        src = "class Holder:\n    def m(self):\n        return 2\n\n    def n(self):\n        return 3\n"
        self.assertEqual(session._py_def_block(src, "m"),
                         "    def m(self):\n        return 2")

    def test_a_rev_that_will_not_parse_still_answers(self):
        """`ast` would raise here; indentation cannot. A probe that raises on a
        historical rev is a probe that reports every item as unknown."""
        self.assertEqual(
            session._py_def_block("def target():\n    return 1\n\nthis is not python(\n",
                                  "target"),
            "def target():\n    return 1")


class StaleReportTest(WiStaleBase):
    def test_a_rewritten_cited_symbol_is_flagged(self):
        self._file_item("The fix lives in `target`, which needs re-checking.")
        self._rewrite_target()
        rows = self._report()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "WI-0001")
        self.assertEqual([c[0] for c in rows[0]["changed"]], ["target"])

    def test_an_unchanged_symbol_is_not_flagged(self):
        self._file_item("The fix lives in `target`.")
        (self.repo / "unrelated.txt").write_text("noise\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "unrelated churn")
        self.assertEqual(self._report(), [],
                         "commits that do not touch the cited symbol must not flag it")

    def test_an_english_word_in_backticks_is_never_reported_gone(self):
        """The noise trap. Notes are prose; `predates`, `handoff`, and `inbox` all
        appear in backticks in the live store. A word that never resolved is not a
        deleted symbol, and reporting it as one makes the metric worthless."""
        self._file_item("This `predates` the rewrite and the `handoff` describes it.")
        self._rewrite_target()
        self.assertEqual(self._report(), [])

    def test_a_symbol_that_existed_and_is_now_gone_is_flagged(self):
        self._file_item("Depends on `target` still being there.")
        self.mod.write_text(
            self.mod.read_text(encoding="utf-8").replace(
                "def target():\n    return 1\n", ""), encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "remove target")
        rows = self._report()
        self.assertEqual(rows[0]["gone"], ["target"])

    def test_a_hyphenated_verb_resolves_to_its_command_function(self):
        """Measured before building: the store cites the CLI (`wi-render`), not python
        names. A symbol-only reader was nearly inert on the store it was built for."""
        self._file_item("`wi-render` leaks superseded items.")
        self.mod.write_text(
            self.mod.read_text(encoding="utf-8").replace(
                "return 'render'", "return 'rendered'"), encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "change the verb")
        rows = self._report()
        self.assertEqual([c[0] for c in rows[0]["changed"]], ["cmd_wi_render"])

    def test_a_habit_name_in_backticks_never_resolves(self):
        """Habit names share the hyphenated shape exactly. The `cmd_` prefix is what
        separates them, with no hand-kept exclusion list to fall out of date."""
        self._file_item("Per `declare-what-a-check-assumes` and `verify-everything`.")
        self._rewrite_target()
        self.assertEqual(self._report(), [])

    def test_a_done_item_is_not_examined(self):
        self._file_item("The fix lives in `target`.", status="done")
        self._rewrite_target()
        self.assertEqual(self._report(), [])

    def test_an_uncommitted_item_is_unknown_not_fresh(self):
        """`declare-what-a-check-assumes`: with no commit there is no 'as of' to measure
        against, and that is a different answer from 'checked and current'."""
        self._file_item("The fix lives in `target`.", commit=False)
        rows = self._report()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["unknown"])
        self.assertIsNone(rows[0]["watermark"])

    def test_nothing_is_ever_closed_or_edited(self):
        """The safety property. A wrong auto-close drops work silently, which is worse
        than the staleness being reported, so the tool has no such capability."""
        name = self._file_item("The fix lives in `target`.")
        before = (self.repo / session.WI_DIRNAME / name).read_text(encoding="utf-8")
        self._rewrite_target()
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_wi_stale(mock.Mock())
        self.assertEqual(before,
                         (self.repo / session.WI_DIRNAME / name).read_text(encoding="utf-8"))

    def test_the_verb_reports_the_unknowns_distinctly(self):
        self._file_item("The fix lives in `target`.", commit=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_wi_stale(mock.Mock())
        self.assertIn("NOT CHECKED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
