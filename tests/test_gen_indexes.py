"""Tests for curate/gen_indexes.py — regenerating receipt-ritual index.md tables from
the directory layout (ADR-0030 §3).

The load-bearing property: section membership is derived AUTHORITATIVELY from the
sibling `pending/` `applied/` `superseded/` … directories, so the drift class where a
row lingers under `## Pending` after its brief moved to `applied/` cannot survive a
pass — while every authored cell (Description, Applied date, version) and all non-table
prose (intro, blockquotes, `## Notes`) carry through untouched.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_indexes", ROOT / "curate" / "gen_indexes.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def brief(title, date="2026-07-14", drafted_line=None):
    body = f"# {title}\n\n"
    if drafted_line:
        body += drafted_line + "\n"
    body += f"- **Edit ID:** {date}-x\n\nSome body.\n"
    return body


class Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_index(self, text):
        (self.dir / "index.md").write_text(text, encoding="utf-8")

    def put_brief(self, section, stem, content="# A brief\n\nbody\n"):
        d = self.dir / section
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.md").write_text(content, encoding="utf-8")

    def regen(self, text):
        return gen.regenerate(text, self.dir)


class RowMovedToCorrectSection(Harness):
    def test_pending_row_moves_to_applied_when_file_moved(self):
        # X's brief physically lives in applied/, but the index still lists it Pending.
        self.put_brief("applied", "2026-07-01-x")
        self.put_brief("pending", "2026-07-02-y")
        text = (
            "# Inbox\n\nIntro.\n\n"
            "## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-01-x](pending/2026-07-01-x.md) | Authored X blurb. | 2026-07-01 |\n"
            "| [2026-07-02-y](pending/2026-07-02-y.md) | Authored Y blurb. | 2026-07-02 |\n\n"
            "## Applied\n\n"
            "| Edit ID | Description | Drafted | Applied | At version |\n"
            "|---|---|---|---|---|\n\n"
            "## Notes\n\n- keep me.\n"
        )
        out, _notes = self.regen(text)
        pending_block = out.split("## Pending")[1].split("## Applied")[0]
        applied_block = out.split("## Applied")[1].split("## Notes")[0]
        # X left Pending, Y stayed.
        self.assertNotIn("2026-07-01-x", pending_block)
        self.assertIn("2026-07-02-y", pending_block)
        # X landed in Applied, pointing at applied/, carrying its authored Description.
        self.assertIn("[2026-07-01-x](applied/2026-07-01-x.md)", applied_block)
        self.assertIn("Authored X blurb.", applied_block)
        # Applied date / version are never fabricated on a move.
        self.assertIn("*(verify)*", applied_block)


class NewFileGetsRow(Harness):
    def test_new_file_row_uses_derived_cells(self):
        self.put_brief("pending", "2026-07-14-fresh",
                       content=brief("FOR ARCHITECT — do the new thing", date="2026-07-14"))
        text = (
            "# Inbox\n\n"
            "## Pending\n\n"
            "| Edit ID | Description | Batch | Drafted | Expected base | Proposed new |\n"
            "|---|---|---|---|---|---|\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("[2026-07-14-fresh](pending/2026-07-14-fresh.md)", out)
        # Description = H1 with the FOR ARCHITECT — prefix stripped; Drafted from filename.
        self.assertIn("do the new thing", out)
        self.assertIn("| 2026-07-14 |", out)
        # Non-derivable columns get an em-dash, never invented content.
        self.assertIn(gen.DASH, out)

    def test_drafted_prefers_header_line_over_filename(self):
        self.put_brief("pending", "2026-07-14-dated",
                       content=brief("A title", date="2026-07-14",
                                     drafted_line="- **Drafted on:** 2026-06-01"))
        text = ("# I\n\n## Pending\n\n| Edit ID | Drafted |\n|---|---|\n\n## Notes\n\n- n.\n")
        out, _ = self.regen(text)
        self.assertIn("| 2026-06-01 |", out)             # header line wins in the Drafted cell
        self.assertNotIn("| 2026-07-14 |", out)          # filename date not used as Drafted


class DeletedFileRowRemoved(Harness):
    def test_orphan_row_dropped(self):
        self.put_brief("pending", "2026-07-01-keep")
        # 2026-07-02-gone has NO file on disk.
        text = (
            "# I\n\n## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-01-keep](pending/2026-07-01-keep.md) | Keep. | 2026-07-01 |\n"
            "| [2026-07-02-gone](pending/2026-07-02-gone.md) | Gone. | 2026-07-02 |\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("2026-07-01-keep", out)
        self.assertNotIn("2026-07-02-gone", out)


class ProsePreserved(Harness):
    def test_intro_blockquote_and_notes_untouched(self):
        self.put_brief("pending", "2026-07-02-y")
        text = (
            "# Inbox title\n\n"
            "Intro paragraph with a [link](../x.md) and detail.\n\n"
            "## Pending\n\n"
            "> **A HUMAN BLOCKQUOTE.** Preserve me verbatim, em-dash — and all.\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-02-y](pending/2026-07-02-y.md) | Y. | 2026-07-02 |\n\n"
            "## Notes\n\n"
            "- **Boundary.** Keep this note.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("Intro paragraph with a [link](../x.md) and detail.", out)
        self.assertIn("> **A HUMAN BLOCKQUOTE.** Preserve me verbatim, em-dash — and all.", out)
        self.assertIn("- **Boundary.** Keep this note.", out)


class Idempotency(Harness):
    def test_second_pass_is_noop(self):
        self.put_brief("applied", "2026-07-01-x")
        self.put_brief("pending", "2026-07-02-y")
        text = (
            "# Inbox\n\nIntro.\n\n"
            "## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-01-x](pending/2026-07-01-x.md) | X. | 2026-07-01 |\n\n"
            "## Applied\n\n"
            "| Edit ID | Description |\n"
            "|---|---|\n\n"
            "## Notes\n\n- n.\n"
        )
        once, _ = self.regen(text)
        twice, _ = self.regen(once)
        self.assertEqual(once, twice)

    def test_already_correct_index_unchanged(self):
        self.put_brief("pending", "2026-07-02-y")
        text = (
            "# Inbox\n\nIntro.\n\n"
            "## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-02-y](pending/2026-07-02-y.md) | Y. | 2026-07-02 |\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertEqual(out, text)


class CheckMode(Harness):
    def _stale_index(self):
        self.put_brief("applied", "2026-07-01-x")
        self.put_brief("pending", "2026-07-02-y")
        self.write_index(
            "# Inbox\n\n## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-07-01-x](pending/2026-07-01-x.md) | X. | 2026-07-01 |\n"
            "| [2026-07-02-y](pending/2026-07-02-y.md) | Y. | 2026-07-02 |\n\n"
            "## Applied\n\n| Edit ID | Description | Drafted | Applied | At version |\n"
            "|---|---|---|---|---|\n\n## Notes\n\n- n.\n"
        )
        return self.dir / "index.md"

    def test_check_detects_drift_without_writing(self):
        idx = self._stale_index()
        before = idx.read_text()
        changed, _notes, problems = gen.process(idx, check=True)
        self.assertTrue(changed)
        self.assertEqual(idx.read_text(), before)  # check writes nothing
        self.assertEqual(problems, [])             # drift is not a "problem"

    def test_write_then_check_is_clean(self):
        idx = self._stale_index()
        gen.process(idx, check=False)              # fix it
        changed, _notes, _problems = gen.process(idx, check=True)
        self.assertFalse(changed)                  # now clean

    def test_main_check_exit_code(self):
        self._stale_index()
        self.assertEqual(gen.main(["--check", str(self.dir)]), 1)
        gen.main([str(self.dir)])                  # rewrite
        self.assertEqual(gen.main(["--check", str(self.dir)]), 0)


class FleetStateVocabulary(Harness):
    """The map of known state directories is an assumption about the fleet, and a
    directory missing from it is INVISIBLE rather than flagged. Surveyed 2026-08-16, the
    original five missed three live states — `accepted/` and `declined/` (the
    federation's own triage vocabulary, holding five real briefs) and `delivered/` (used
    by several members). Until this landed, our own index could not see them at all."""

    def test_accepted_section_is_generated_from_the_directory(self):
        self.put_brief("accepted", "2026-08-01-a", brief("Accepted brief", "2026-08-01"))
        text = "# Inbox\n\nIntro.\n\n## Pending\n\n(none)\n\n## Notes\n\n- n.\n"
        new, _notes = self.regen(text)
        self.assertIn("## Accepted", new)
        self.assertIn("[2026-08-01-a](accepted/2026-08-01-a.md)", new)

    def test_pending_row_moves_to_accepted_when_file_moved(self):
        # The exact 9th-drift shape, in the state the generator used to be blind to.
        self.put_brief("accepted", "2026-08-01-a")
        text = (
            "# Inbox\n\n## Pending\n\n"
            "| Edit ID | Description | Drafted |\n"
            "|---|---|---|\n"
            "| [2026-08-01-a](pending/2026-08-01-a.md) | A. | 2026-08-01 |\n\n"
            "## Accepted\n\n| Edit ID | Description | Drafted |\n|---|---|---|\n\n"
        )
        new, _notes = self.regen(text)
        pending_half, accepted_half = new.split("## Accepted")
        self.assertNotIn("2026-08-01-a", pending_half)   # left Pending
        self.assertIn("2026-08-01-a", accepted_half)     # arrived in Accepted
        self.assertIn("| A. |", accepted_half)           # authored cell salvaged across

    def test_declined_and_delivered_are_known_states(self):
        self.put_brief("declined", "2026-08-02-d")
        self.put_brief("delivered", "2026-08-03-e")
        new, _notes = self.regen("# Inbox\n\n## Pending\n\n(none)\n")
        self.assertIn("[2026-08-02-d](declined/2026-08-02-d.md)", new)
        self.assertIn("[2026-08-03-e](delivered/2026-08-03-e.md)", new)


class ContradictionsAreFindingsNotRows(Harness):
    """A regenerated table makes the index agree with the directory — but if the
    DIRECTORY contradicts itself, rendering that as two tidy rows hides it. Live case
    2026-08-05: one brief in both `pending/` and `accepted/`, spotted only by a human
    reading the file."""

    def test_same_brief_in_two_states_is_a_problem(self):
        self.put_brief("pending", "2026-08-04-dup")
        self.put_brief("accepted", "2026-08-04-dup")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        idx = self.dir / "index.md"
        _changed, _notes, problems = gen.process(idx, check=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("2026-08-04-dup", problems[0])
        self.assertIn("accepted", problems[0])
        self.assertIn("pending", problems[0])

    def test_duplicate_alone_fails_check_even_when_tables_are_current(self):
        # The sharp case: nothing is stale, so a staleness-only check passes clean while
        # the mailbox is self-contradictory.
        self.put_brief("pending", "2026-08-04-dup")
        self.put_brief("accepted", "2026-08-04-dup")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        gen.main([str(self.dir)])                       # bring tables fully up to date
        idx = self.dir / "index.md"
        changed, _notes, problems = gen.process(idx, check=True)
        self.assertFalse(changed)                       # not stale …
        self.assertTrue(problems)                       # … but still a finding
        self.assertEqual(gen.main(["--check", str(self.dir)]), 1)

    def test_unknown_state_directory_is_named_not_ignored(self):
        self.put_brief("quarantined", "2026-08-05-q")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        _c, _n, problems = gen.process(self.dir / "index.md", check=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("quarantined", problems[0])

    def test_known_state_directory_is_not_reported_as_unknown(self):
        self.put_brief("delivered", "2026-08-05-ok")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        _c, _n, problems = gen.process(self.dir / "index.md", check=True)
        self.assertEqual(problems, [])


class NothingToCheckIsNotAPass(Harness):
    """`proposed-edits/` is gitignored data, so a merge gate — which runs against a
    scratch worktree of the candidate commit — sees no indexes at all. The old answer
    there was `OK — all 0 index(es) match`, exit 0: a check that cannot fail, certifying
    the gap it was meant to detect."""

    def test_zero_indexes_exits_distinctly_and_never_says_ok(self):
        empty = self.dir / "no-indexes-here"
        empty.mkdir()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gen.main(["--check", str(empty)])
        self.assertEqual(rc, 2)                         # not 0 (pass), not 1 (findings)
        self.assertNotIn("OK", buf.getvalue())

    def test_real_tree_with_indexes_still_passes_normally(self):
        self.put_brief("pending", "2026-08-06-p")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        gen.main([str(self.dir)])
        self.assertEqual(gen.main(["--check", str(self.dir)]), 0)


class StatusModeIsTheWiredForm(Harness):
    """`--status` is the mode actually wired into the startup hook, so it is the mode
    that must be exercised: this item exists because a capability that shipped unwired
    and unwatched drifted nine times."""

    def _run_status(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gen.main(["--status", str(self.dir)])
        return rc, buf.getvalue()

    def test_quiet_when_nothing_to_do(self):
        self.put_brief("pending", "2026-08-07-p")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        gen.main([str(self.dir)])                  # bring current first
        rc, out = self._run_status()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")          # silent on a clean pass

    def test_status_still_regenerates_rather_than_only_reporting(self):
        self.put_brief("accepted", "2026-08-07-a")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        rc, out = self._run_status()
        self.assertEqual(rc, 0)
        self.assertIn("regenerated", out.lower())
        # The load-bearing half: the file on disk is actually fixed, not just described.
        self.assertIn("accepted/2026-08-07-a.md", (self.dir / "index.md").read_text())

    def test_status_speaks_up_about_a_contradiction_but_exits_zero(self):
        self.put_brief("pending", "2026-08-07-dup")
        self.put_brief("accepted", "2026-08-07-dup")
        self.write_index("# Inbox\n\n## Pending\n\n(none)\n")
        rc, out = self._run_status()
        self.assertEqual(rc, 0)                    # never brick the session start
        self.assertIn("PROBLEM", out)


class PlaceholderAndAuthoredProse(Harness):
    def test_none_placeholder_becomes_table_when_dir_nonempty(self):
        self.put_brief("pending", "2026-07-14-new")
        text = "# I\n\n## Pending\n\n(none)\n\n## Notes\n\n- n.\n"
        out, notes = self.regen(text)
        self.assertIn("| Edit ID | Description | Drafted |", out)
        self.assertIn("[2026-07-14-new](pending/2026-07-14-new.md)", out)
        self.assertEqual(notes, [])

    def test_authored_prose_left_untouched_and_flagged(self):
        # A deliberately-empty Applied note, as one member authors it, over a non-empty applied/ dir.
        self.put_brief("applied", "2026-07-01-x")
        prose = "(none recorded federation-side — the system tracks its own `applied/`.)"
        text = f"# I\n\n## Applied\n\n{prose}\n\n## Notes\n\n- n.\n"
        out, notes = self.regen(text)
        self.assertIn(prose, out)                  # untouched
        self.assertNotIn("2026-07-01-x", out)      # not forced into a table
        self.assertEqual(len(notes), 1)            # but surfaced for a human
        self.assertIn("applied", notes[0])

    def test_empty_dir_keeps_placeholder(self):
        text = "# I\n\n## Applied\n\n(none yet)\n\n## Notes\n\n- n.\n"
        out, _ = self.regen(text)
        self.assertEqual(out, text)


class MissingSectionAndSalvageEdges(Harness):
    def test_missing_section_created_before_notes(self):
        self.put_brief("pending", "2026-07-02-y")
        self.put_brief("superseded", "2026-06-21-old")
        text = (
            "# I\n\n## Pending\n\n"
            "| Edit ID | Description | Drafted |\n|---|---|---|\n"
            "| [2026-07-02-y](pending/2026-07-02-y.md) | Y. | 2026-07-02 |\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("## Superseded", out)
        self.assertLess(out.index("## Superseded"), out.index("## Notes"))
        self.assertIn("[2026-06-21-old](superseded/2026-06-21-old.md)", out)

    def test_plain_text_first_cell_preserved(self):
        # One member's superseded rows are deliberately plain text (no link) though the file
        # exists; a matched plain row keeps its plain rendering.
        self.put_brief("superseded", "2026-06-14-code-channel")
        text = (
            "# I\n\n## Superseded\n\n"
            "| Edit ID | Why superseded |\n|---|---|\n"
            "| 2026-06-14-code-channel | Covered by CANON. |\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("| 2026-06-14-code-channel | Covered by CANON. |", out)
        self.assertNotIn("[2026-06-14-code-channel]", out)  # stayed plain

    def test_escaped_pipe_in_cell_survives(self):
        self.put_brief("applied", "2026-07-01-x")
        text = (
            "# I\n\n## Applied\n\n"
            "| Edit ID | Description | Drafted | Applied | At version |\n"
            "|---|---|---|---|---|\n"
            "| [2026-07-01-x](applied/2026-07-01-x.md) | cmd --keep\\|--bad here. | 2026-07-01 | 2026-07-02 | v1.0.0 |\n\n"
            "## Notes\n\n- n.\n"
        )
        out, _ = self.regen(text)
        self.assertIn("cmd --keep\\|--bad here.", out)
        self.assertIn("| 2026-07-02 | v1.0.0 |", out)  # authored date/version salvaged


if __name__ == "__main__":
    unittest.main()
