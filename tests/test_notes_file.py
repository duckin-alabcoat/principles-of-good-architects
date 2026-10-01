"""WI-0289 — an item's prose reaches the store as FILE BYTES, never as a shell argument.

THE DEFECT, hit live in session ~208 while filing WI-0288. Notes were passed to
`poga work new` / `poga work edit` as a `--notes` argument. An agent composing that command
writes prose containing backticks, which is the natural way to quote a verb or a filename in
Markdown — and inside a double-quoted shell argument a backtick is COMMAND SUBSTITUTION. The
shell ran the contents, they produced nothing, and the empty result is what got stored. Three
fragments of that item's notes were lost before anyone read them.

WHY IT NEEDS A ROUTE RATHER THAN CARE. The failure is silent in the artifact — the item
commits successfully and reads fine unless you already know what the sentence was supposed to
say — and it is unbounded, because `$(...)` executes too and this is the front door of the
canonical record for the whole backlog. It corrupts the one thing the store exists to keep:
the reasoning. A lost line number can be looked up again; a lost *do not conflate these two
budgets* cannot, because nobody knows it was ever there.

The properties pinned here:

  1. THE PREMISE IS REAL, AND MEASURED RATHER THAN ASSUMED. `test_the_shell_really_eats_it`
     runs a double-quoted argument through `/bin/sh` and watches the characters vanish. Every
     other test in this file is a response to that one, so it is asserted, not stated.
  2. FILE BYTES SURVIVE. What a `--notes-file` holds is what the record holds, at all five
     verbs — backticks, `$(...)`, `${VAR}`, brackets, globs, tildes and quotes included.
  3. THE TWO SOURCES ARE EXCLUSIVE, at the parser and again in the resolver. Two sources for
     one field have no defensible winner, so neither is chosen.
  4. INLINE PROSE IS REFUSED, and the refusal says what it is. It is a LENGTH rule, not a
     corruption detector: whatever a shell already ate is gone before this process starts.
     Short values — the phrase a flag is actually good for — still work.
  5. A REFUSAL CHANGES NOTHING. An unreadable path or an oversized inline value is caught
     before the first write, so the item is not left half-edited.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

# Every shape the shell mangles, in one payload, written the way an Architect actually writes
# a note. The three that bit WI-0288 are the first three lines; the rest are the same class.
NASTY = (
    "Backticks quote a verb: `session.py end` ends the session by default, and\n"
    "`poga work claim <id>` is the one that must run first.\n"
    "\n"
    "Command substitution is the other form: $(poga work list) and ${HOME} and $PATH.\n"
    "A bracketed flag globs: [--no-commit], and so do [0-9] and *.md and ~operator.\n"
    "Quotes both survive: it's fine, \"quoted\" is fine, and a backslash \\n stays two chars.\n"
)


class NotesFileBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "work-items").mkdir(parents=True, exist_ok=True)
        (self.tmp / "ops-items").mkdir(parents=True, exist_ok=True)
        self._root = session.ROOT
        session.ROOT = self.tmp

    def tearDown(self):
        session.ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── fixtures ────────────────────────────────────────────────────────────────────
    def notes_file(self, text=NASTY, name="notes.md"):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def wi(self, wid="WI-0001", notes="the authored body"):
        session._wi_write_item({"id": wid, "title": "An item", "status": "open",
                                "section": "next", "blocked_by": [], "group": "",
                                "source": "", "impact": "fix", "version": "",
                                "notes": notes})
        return wid

    def ops(self, oid="OPS-0001", notes="the authored body", **fields):
        item = {"id": oid, "title": "An obligation", "status": "open", "cadence": "",
                "last_completed": "", "last_result": "", "due": "", "group": "",
                "source": "", "notes": notes}
        item.update(fields)
        session._ops_write_item(item)
        return oid

    def stub_draw(self, name, n=7):
        """Replace a number-drawing function FOR THIS TEST ONLY.

        The coord store is not this file's subject, so stubbing the draw is right. Doing
        it with a bare `session.<name> = ...` is not: these are module-level names on a
        module every other test in the process shares, so the stub outlives the test that
        set it and every later caller silently draws the same fixed number.

        That is not hypothetical. `session._wi_reserve_next` stayed pinned at 7 for the
        rest of the run and took four allocator tests down with it -- two reading the
        wrong next number, and two looking for a reservation record the stub never wrote.
        None of the four are in this file, all four pass in isolation, and the suite was
        red for two days. Restoring on cleanup is what keeps that local."""
        original = getattr(session, name)
        self.addCleanup(setattr, session, name, original)
        setattr(session, name, lambda *a, **k: n)

    def wi_read(self, wid):
        return next(it for it in session._wi_parse() if it["id"] == wid)

    def ops_read(self, oid):
        return next(it for it in session._ops_parse() if it["id"] == oid)

    def invoke(self, fn, args):
        """`(exit_code, stdout)`. 0 when the verb returned without calling sys.exit."""
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                fn(args)
        except SystemExit as e:
            return (int(e.code or 0), buf.getvalue())
        return (0, buf.getvalue())

    def wi_edit_args(self, wid, **over):
        base = dict(id=wid, title=None, notes=None, notes_file=None,
                    append_notes=None, append_notes_file=None, group=None)
        base.update(over)
        return argparse.Namespace(**base)

    def ops_edit_args(self, oid, **over):
        base = dict(id=oid, title=None, notes=None, notes_file=None,
                    append_notes=None, append_notes_file=None)
        base.update(over)
        return argparse.Namespace(**base)


class TheShellPremiseTest(unittest.TestCase):
    """The claim the whole item rests on, MEASURED. Nothing downstream is worth asserting if
    this turns out not to happen, and 'everybody knows shells do that' is not a probe."""

    def test_the_shell_really_eats_it(self):
        sh = shutil.which("sh") or "/bin/sh"
        prose = 'the verb `session.py end` ends it, see $(poga work list)'
        # The substitutions really RUN — so with the operator's PATH, `poga work list`
        # lists the LIVE backlog (ADR-0148 D3 store guard). An empty PATH keeps the
        # premise (the text is executed away) without reaching anything real; `printf`
        # is a shell builtin and needs no PATH.
        with tempfile.TemporaryDirectory() as empty:
            got = subprocess.run([sh, "-c", 'printf "%s" "' + prose + '"'],
                                 capture_output=True, text=True,
                                 env={"PATH": empty}).stdout
        self.assertNotEqual(got, prose, "the premise of WI-0289 did not reproduce")
        self.assertNotIn("session.py end", got,
                         "backticked text should have been executed away")
        self.assertNotIn("poga work list", got,
                         "dollar-parens should have been executed away")
        # The residue: the words are gone and the sentence still looks like a sentence.
        self.assertIn("the verb", got)
        self.assertIn("ends it", got)

    def test_single_quoting_is_the_workaround_that_is_not_a_fix(self):
        """Stated here so the alternative this item rejected is on the record rather than in
        a commit message: single quotes DO work, which is exactly why 'just remember to use
        them' was tempting — and why a route that needs no remembering is the fix."""
        sh = shutil.which("sh") or "/bin/sh"
        prose = 'the verb `session.py end` ends it'
        got = subprocess.run([sh, "-c", "printf '%s' '" + prose + "'"],
                             capture_output=True, text=True).stdout
        self.assertEqual(got, prose)


class FileBytesSurviveTest(NotesFileBase):
    """Property 2 — at every verb that takes prose. Each assertion is byte equality against
    the file's own text, not a substring probe, because a substring probe passes on a record
    that lost a *different* sentence."""

    def test_wi_edit_notes_file_replaces_the_body_verbatim(self):
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit,
                           self.wi_edit_args(wid, notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertEqual(self.wi_read(wid)["notes"], NASTY.strip())

    def test_wi_edit_append_notes_file_records_the_paragraph_verbatim(self):
        """The append path is the one that carries findings, so it is the one prose actually
        goes through — and the one that lost WI-0288's three fragments."""
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit,
                           self.wi_edit_args(wid, append_notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertIn(NASTY.strip(),
                      session._notes_compiled(session.WI_DIRNAME, {"id": wid}))

    def test_ops_edit_notes_file_replaces_the_body_verbatim(self):
        oid = self.ops()
        code, _ = self.invoke(session.cmd_ops_edit,
                           self.ops_edit_args(oid, notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertEqual(self.ops_read(oid)["notes"], NASTY.strip())

    def test_ops_edit_append_notes_file_records_the_paragraph_verbatim(self):
        oid = self.ops()
        code, _ = self.invoke(session.cmd_ops_edit,
                           self.ops_edit_args(oid, append_notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertIn(NASTY.strip(),
                      session._notes_compiled(session.OPS_DIRNAME, {"id": oid}))

    def test_ops_ran_notes_file_keeps_the_run_writeup_verbatim(self):
        oid = self.ops()
        code, _ = self.invoke(session.cmd_ops_ran, argparse.Namespace(
            id=oid, result="findings", on="2026-07-21",
            notes="", notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        item = self.ops_read(oid)
        self.assertIn(NASTY.strip(), item["notes"])
        self.assertEqual(item["last_completed"], "2026-07-21")

    def test_wi_new_writes_the_file_body_verbatim(self):
        self.stub_draw("_wi_reserve_next")   # the coord store is not the subject
        code, _ = self.invoke(session.cmd_wi_new, argparse.Namespace(
            title="A new item", section="next", blocked_by="", group="", source="",
            impact="fix", migration="", scope="harness", notes="",
            notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertEqual(self.wi_read("WI-0007")["notes"], NASTY.strip())

    def test_ops_new_writes_the_file_body_verbatim(self):
        self.stub_draw("_ops_reserve_next")
        code, _ = self.invoke(session.cmd_ops_new, argparse.Namespace(
            title="A new obligation", cadence="", due="", group="", source="",
            notes="", notes_file=self.notes_file()))
        self.assertEqual(code, 0)
        self.assertEqual(self.ops_read("OPS-0007")["notes"], NASTY.strip())

    def test_interior_shape_is_preserved_not_just_the_characters(self):
        """Blank lines and leading indentation are part of the prose. A resolver that
        normalised whitespace would pass every substring check above and still ruin a note."""
        text = "first line\n\n    indented continuation\n\n\nthree blank lines above\n"
        wid = self.wi()
        self.invoke(session.cmd_wi_edit,
                 self.wi_edit_args(wid, notes_file=self.notes_file(text, "shape.md")))
        self.assertEqual(self.wi_read(wid)["notes"], text.strip())


class TheTwoSourcesAreExclusiveTest(NotesFileBase):
    """Property 3. The parser arm is the one real callers hit; the resolver arm covers the
    in-process callers the parser never sees."""

    def _parser_refusal(self, argv):
        err = io.StringIO()
        old = sys.argv
        sys.argv = ["session.py"] + argv
        try:
            with redirect_stdout(io.StringIO()):
                import contextlib
                with contextlib.redirect_stderr(err):
                    session.main()
        except SystemExit as e:
            return (int(e.code or 0), err.getvalue())
        finally:
            sys.argv = old
        return (0, err.getvalue())

    def test_every_subparser_refuses_the_pair(self):
        cases = [
            (["wi-new", "t", "--notes", "a", "--notes-file", "b"], "--notes-file"),
            (["wi-edit", "WI-0001", "--notes", "a", "--notes-file", "b"], "--notes-file"),
            (["wi-edit", "WI-0001", "--append-notes", "a",
              "--append-notes-file", "b"], "--append-notes-file"),
            (["ops-new", "t", "--notes", "a", "--notes-file", "b"], "--notes-file"),
            (["ops-ran", "OPS-0001", "--result", "pass",
              "--notes", "a", "--notes-file", "b"], "--notes-file"),
            (["ops-edit", "OPS-0001", "--notes", "a", "--notes-file", "b"], "--notes-file"),
            (["ops-edit", "OPS-0001", "--append-notes", "a",
              "--append-notes-file", "b"], "--append-notes-file"),
        ]
        for argv, flag in cases:
            with self.subTest(argv=" ".join(argv)):
                code, err = self._parser_refusal(argv)
                self.assertEqual(code, 2)
                self.assertIn("not allowed with", err)
                self.assertIn(flag, err)

    def test_the_resolver_refuses_the_pair_too(self):
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit, self.wi_edit_args(
            wid, notes="inline", notes_file=self.notes_file()))
        self.assertEqual(code, 2)
        self.assertIn("not both", out)
        self.assertEqual(self.wi_read(wid)["notes"], "the authored body",
                         "a refused edit must leave the item untouched")


class InlineProseIsRefusedTest(NotesFileBase):
    """Property 4. The cap exists because there is nothing left to detect by the time this
    process runs — so the guard asks about the shape of the payload, and says so."""

    LONG = "x" * (session.NOTES_INLINE_MAX + 1)

    def test_a_long_inline_notes_is_refused_and_names_the_route(self):
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit, self.wi_edit_args(wid, notes=self.LONG))
        self.assertEqual(code, 2)
        self.assertIn("--notes-file", out)
        self.assertIn("WI-0289", out)
        self.assertEqual(self.wi_read(wid)["notes"], "the authored body")

    def test_the_refusal_does_not_claim_to_have_detected_corruption(self):
        """`declare-what-a-check-assumes`. A length rule that reads as a corruption detector
        would teach every reader that a passing command was checked for damage. It was not."""
        _code, out = self.invoke(session.cmd_wi_edit,
                              self.wi_edit_args(self.wi(), notes=self.LONG))
        self.assertIn("NOT A CORRUPTION DETECTOR", out)

    def test_the_remedy_is_runnable_by_the_reader_it_is_printed_to(self):
        """WI-0158: a refusal's remedy must be a command the blocked reader can actually run.
        A heredoc whose delimiter is unquoted would re-expand the prose it is rescuing, so the
        quoting of the remedy is load-bearing, not cosmetic."""
        _code, out = self.invoke(session.cmd_wi_edit,
                              self.wi_edit_args(self.wi(), notes=self.LONG))
        self.assertIn("<<'NOTES'", out)
        self.assertIn("poga work edit <id> --notes-file", out)

    def test_the_long_append_path_is_capped_as_well(self):
        """The append flag is where the prose goes, so capping only `--notes` would leave the
        defect standing on the path that caused it."""
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit,
                             self.wi_edit_args(wid, append_notes=self.LONG))
        self.assertEqual(code, 2)
        self.assertIn("--append-notes-file", out)
        self.assertEqual(session._notes_records(session.WI_DIRNAME, wid), [])

    def test_a_phrase_still_goes_through_inline(self):
        """The cap must not become a refusal to use the flag at all — `ops ran --notes clean`
        is what a flag is good for, and breaking it would push prose nowhere better."""
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit,
                           self.wi_edit_args(wid, notes="ran clean; nothing to report"))
        self.assertEqual(code, 0)
        self.assertEqual(self.wi_read(wid)["notes"], "ran clean; nothing to report")

    def test_a_value_exactly_at_the_cap_is_allowed(self):
        """The boundary is stated once, here, so a later refactor cannot quietly move it."""
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit,
                           self.wi_edit_args(wid, notes="y" * session.NOTES_INLINE_MAX))
        self.assertEqual(code, 0)

    def test_the_cap_does_not_apply_to_the_file_route(self):
        """The whole point: length is a proxy for shell exposure, and a file has none."""
        wid = self.wi()
        long_prose = ("a genuinely long paragraph. " * 40) + "`with backticks`"
        code, _ = self.invoke(session.cmd_wi_edit, self.wi_edit_args(
            wid, notes_file=self.notes_file(long_prose, "long.md")))
        self.assertEqual(code, 0)
        self.assertEqual(self.wi_read(wid)["notes"], long_prose.strip())


class ARefusalChangesNothingTest(NotesFileBase):
    """Property 5 — the resolver runs before the first write at every verb, so a bad path
    cannot leave an item with a new title and its old notes."""

    def test_a_missing_file_is_refused_and_the_title_is_not_applied(self):
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit, self.wi_edit_args(
            wid, title="A NEW TITLE", notes_file=str(self.tmp / "nope.md")))
        self.assertEqual(code, 2)
        self.assertIn("cannot read", out)
        item = self.wi_read(wid)
        self.assertEqual(item["title"], "An item")
        self.assertEqual(item["notes"], "the authored body")

    def test_an_empty_file_is_refused_rather_than_silently_clearing_the_body(self):
        """A heredoc that never ran leaves a zero-byte file, and 'delete everything this item
        knows' is not a plausible reading of that. Clearing has its own spelling."""
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit,
                             self.wi_edit_args(wid, notes_file=self.notes_file("   \n", "e.md")))
        self.assertEqual(code, 2)
        self.assertIn("is empty", out)
        self.assertIn("--notes ''", out)
        self.assertEqual(self.wi_read(wid)["notes"], "the authored body")

    def test_clearing_the_body_still_has_a_spelling(self):
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit, self.wi_edit_args(wid, notes=""))
        self.assertEqual(code, 0)
        self.assertEqual(self.wi_read(wid)["notes"], "")

    def test_a_directory_is_refused_like_any_other_unreadable_path(self):
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit,
                             self.wi_edit_args(wid, notes_file=str(self.tmp / "work-items")))
        self.assertEqual(code, 2)
        self.assertIn("cannot read", out)

    def test_non_utf8_bytes_are_named_rather_than_mangled(self):
        p = self.tmp / "latin.md"
        p.write_bytes(b"caf\xe9 is latin-1, not utf-8")
        wid = self.wi()
        code, out = self.invoke(session.cmd_wi_edit,
                             self.wi_edit_args(wid, notes_file=str(p)))
        self.assertEqual(code, 2)
        self.assertIn("not UTF-8", out)

    def test_ops_edit_refuses_before_applying_a_title(self):
        oid = self.ops()
        code, _ = self.invoke(session.cmd_ops_edit, self.ops_edit_args(
            oid, title="A NEW TITLE", append_notes_file=str(self.tmp / "nope.md")))
        self.assertEqual(code, 2)
        self.assertEqual(self.ops_read(oid)["title"], "An obligation")

    def test_ops_ran_refuses_before_stamping_the_run(self):
        """The worst half-write in this store: a run recorded with its write-up missing rolls
        `due` forward, so the obligation looks met and the findings are nowhere."""
        oid = self.ops(cadence="monthly")
        code, _ = self.invoke(session.cmd_ops_ran, argparse.Namespace(
            id=oid, result="findings", on="2026-07-21",
            notes="", notes_file=str(self.tmp / "nope.md")))
        self.assertEqual(code, 2)
        item = self.ops_read(oid)
        self.assertEqual(item["last_completed"], "")
        self.assertEqual(item["due"], "")


class TheSentinelsAreNotFlattenedTest(NotesFileBase):
    """The resolver is shared by five verbs whose `--notes` defaults deliberately differ —
    `None` where 'not passed' and 'set to empty' mean different things, `""` where they do
    not. Collapsing them would make every `wi-edit` that touches only the title re-author the
    body as empty, which is the quiet catastrophe this file is otherwise about."""

    def test_not_passing_notes_leaves_the_body_alone(self):
        wid = self.wi()
        code, _ = self.invoke(session.cmd_wi_edit, self.wi_edit_args(wid, title="Retitled"))
        self.assertEqual(code, 0)
        item = self.wi_read(wid)
        self.assertEqual(item["title"], "Retitled")
        self.assertEqual(item["notes"], "the authored body")

    def test_the_resolver_returns_each_defaults_own_sentinel(self):
        none_args = argparse.Namespace(notes=None, notes_file=None)
        empty_args = argparse.Namespace(notes="", notes_file="")
        self.assertIsNone(session._notes_from_args(none_args, "wi-edit"))
        self.assertEqual(session._notes_from_args(empty_args, "wi-new"), "")

    def test_a_namespace_with_no_file_attribute_at_all_still_resolves(self):
        """Older in-process callers build a Namespace without the new field. They must keep
        working, or this change breaks the store from inside while the CLI looks fine."""
        legacy = argparse.Namespace(notes="a phrase")
        self.assertEqual(session._notes_from_args(legacy, "wi-edit"), "a phrase")


if __name__ == "__main__":
    unittest.main()
