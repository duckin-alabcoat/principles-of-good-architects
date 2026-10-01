"""Tests for curate/public_cut.py — the gate refuses each class it claims to catch.

WI-0381. The public cut is a GENERIC POGA SYSTEM: no trace of any member project, no
operator, no machine. The tool's whole value is the refusal, so the tests that matter are
the NEGATIVE CONTROLS — one per class the brief's acceptance names: a member name, an
operator home path, a overlay address, an email.

Each control is paired against the SAME fixture tree that passes clean, so a refusal
proves the planted defect and not merely that the tool refuses everything. A negative
control with no clean baseline beside it cannot tell those two apart, and it is the shape
that lets a gate pass its own tests while catching nothing
([`a-detector-proves-itself-on-the-real-defect`]).

Three of the four classes are NOT reachable through curate/scrub.py: it has no email class
at all and knows nothing about member names or home paths. Only the overlay address is
scrub's, and the control for it is what proves scrub is genuinely wired in rather than
reimplemented here.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))

import public_cut  # noqa: E402


MANIFEST = {
    "manifest_version": 1,
    "include": [{"path": "adr", "why": "doctrine"},
                {"path": "NOTICE", "why": "authored later — must not fail the check"}],
    "exclude": [{"path": "sessions", "ships": None, "why": "the working record"},
                {"path": "work-items", "ships": None, "why": "the live backlog"}],
    "exclude_suffixes": [{"suffix": ".log", "why": "runtime noise"}],
    "exclude_glob": [],
    "sample": [],
    "replace": [],
    "generalize": {"roster_source": "portfolio.md",
                   "roster_exempt": ["federation"],
                   "substitutions": [],
                   "review": []},
    "identity_gate": {
        "classes": [],
        "email_pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "email_allow": [".invalid", "@example.com"],
        "home_path_patterns": ["/Users/[A-Za-z0-9_-][A-Za-z0-9._-]*"],
        "home_path_allow": ["/Users/<operator>"],
        "home_path_placeholder_segments": ["you", "someone"],
    },
    "binary_suffixes": [".png"],
    "gate_exempt": [],
}

#: A roster the fixture's portfolio.md publishes. `federation` is exempt — it is the
#: subject of the cut, not a foreign project.
PORTFOLIO = """# Portfolio registry

| System | Orchestrator agent | Canonical Architect | System ID | Architect ID | Resides | Status |
|---|---|---|---|---|---|---|
| federation | (none) | Federation Architect | `federation` | `federation-arch` | — | Active |
| barometer | (none) | Barometer Architect | `barometer` | `barometer-arch` | — | Active |

## Notes

- Nothing here identifies anybody.
"""

#: A real-domain address, assembled at run time: the file itself ships in the public cut,
#: and the cut carries no address but the public identity's own (WI-0020, 2026-09-27).
PERSON = "person" + "@" + "company.com"

#: A home path and an overlay-range address, assembled at run time for the same reason:
#: this file ships, and a literal one would need a file-wide exemption to pass the gate
#: it tests (WI-0449 retired that row).
HOME = "/Users" + "/realname"
TS_IP = "100.64" + ".9.9"


def _alias(kind, letter, sep=" "):
    """A letter alias (`Member 1`, `member-3`), built at run time. The cut dealiases every
    literal alias in a code file, so a literal input here was rewritten in the exported
    tree and the tests that feed it went red there (round-2 review, item 4)."""
    return kind + sep + letter

CLEAN = """# ADR-0001: Something the doctrine decided

The reasoning is generic. It names no project, no person and no machine.
"""


class PublicCutGateTest(unittest.TestCase):
    """Four planted defects, four refusals, one shared clean baseline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        (self.repo / "adr").mkdir(parents=True)
        (self.repo / "sessions").mkdir()
        (self.repo / "portfolio.md").write_text(PORTFOLIO, encoding="utf-8")
        (self.repo / "adr" / "0001-clean.md").write_text(CLEAN, encoding="utf-8")
        # Excluded, and carrying every shape at once: if the walk ever reaches it, every
        # control below would pass for the wrong reason.
        (self.repo / "sessions" / "journal.md").write_text(
            "barometer at %s and %s, mail to %s\n" % (HOME, TS_IP, PERSON),
            encoding="utf-8")

        self.manifest = pathlib.Path(self.tmp.name) / "manifest.json"
        self.manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")

        self._saved = (public_cut.ROOT, public_cut.MANIFEST_PATH)
        self.addCleanup(self.restore)
        public_cut.ROOT = self.repo
        public_cut.MANIFEST_PATH = self.manifest

    def restore(self):
        public_cut.ROOT, public_cut.MANIFEST_PATH = self._saved

    # --- helpers -----------------------------------------------------------------

    def run_check(self):
        """--check over the fixture. Returns (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = public_cut.main(["--check", "--root", str(self.repo),
                                    "--manifest", str(self.manifest)])
        return code, out.getvalue(), err.getvalue()

    def run_out(self, out_dir):
        """`--out` with stdout captured — the suite's output is not this tool's scratchpad."""
        with contextlib.redirect_stdout(io.StringIO()):
            return public_cut.main(["--out", str(out_dir), "--root", str(self.repo),
                                    "--manifest", str(self.manifest)])

    def plant(self, body, name="0002-planted.md"):
        (self.repo / "adr" / name).write_text(body, encoding="utf-8")
        return "adr/%s" % name

    # --- the baseline the controls are read against ------------------------------

    def test_the_clean_fixture_passes(self):
        """The positive control. Without it, every refusal below could be a tool that
        refuses unconditionally, and the four controls would prove nothing."""
        code, out, err = self.run_check()
        self.assertEqual(code, 0, err)
        self.assertIn("clean", out)

    # --- the four negative controls ----------------------------------------------

    def test_a_member_name_is_REFUSED_with_file_and_line(self):
        """Control 1 of 4. Not reachable through scrub.py — this class is the tool's own,
        and the roster it checks against is read from portfolio.md at run time."""
        rel = self.plant("# ADR-0002\n\nline two\nThe barometer port collision.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("member-name", err)
        self.assertIn("%s:4" % rel, err)

    def test_an_operator_home_path_is_REFUSED_with_file_and_line(self):
        """Control 2 of 4. Also the tool's own class; scrub.py has no notion of a home."""
        rel = self.plant("# ADR-0002\n\nSee %s/notes for the rest.\n" % HOME)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("home-path", err)
        self.assertIn("%s:3" % rel, err)

    def test_a_tailscale_address_is_REFUSED_with_file_and_line(self):
        """Control 3 of 4, and the one that proves curate/scrub.py is genuinely wired in:
        `overlay-ip` is scrub's class name, not a string this module defines."""
        rel = self.plant("# ADR-0002\n\nThe host answers on %s today.\n" % TS_IP)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("overlay-ip", err)
        self.assertIn("%s:3" % rel, err)

    def test_an_email_is_REFUSED_with_file_and_line(self):
        """Control 4 of 4. scrub.py carries NO email class, which is the whole reason the
        tool has an identity gate of its own rather than deferring to scrub for all four."""
        rel = self.plant("# ADR-0002\n\nWrite to %s about it.\n" % PERSON)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("email", err)
        self.assertIn("%s:3" % rel, err)

    # --- the controls must not fire on the things they are allowed to see ---------

    def test_the_exempt_member_name_does_not_fire(self):
        """`federation` is the subject of the cut. Substituting or refusing it would erase
        the thing being published."""
        self.plant("# ADR-0002\n\nThe federation decided this.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_reserved_tld_and_a_placeholder_home_do_not_fire(self):
        """A fixture identity belongs to nobody. The class exists to catch a person."""
        self.plant("# ADR-0002\n\nt@e.invalid works from /Users/someone/x.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)


class PublicCutOutputTest(PublicCutGateTest):
    """What the cut writes, and what the receipt is obliged to say about it."""

    def test_an_excluded_tree_is_absent_and_the_receipt_says_why(self):
        out_dir = pathlib.Path(self.tmp.name) / "out"
        code = self.run_out(out_dir)
        self.assertEqual(code, 0)
        self.assertFalse((out_dir / "sessions").exists(),
                         "an excluded tree reached the cut")
        self.assertTrue((out_dir / "adr" / "0001-clean.md").is_file())

        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("## Published", receipt)
        self.assertIn("## Withheld by the boundary", receipt)
        self.assertIn("the working record", receipt)

    def test_the_receipt_says_only_that_ruled_out_records_are_withheld(self):
        """WI-0449: a ruled-out record under an included tree prints no characterisation
        and no count, and the receipt prints no substitution count (the header and the
        section once disagreed, and a count describes the source)."""
        self.plant(CLEAN, "0003-a-kind-of-thing.md")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["exclude"].append({"path": "adr/0003-a-kind-of-thing.md",
                               "why": "withheld from this copy (the operator's ruling, 2026-09-28)"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn(f"- some records — {public_cut.WITHHELD_WHY}\n", receipt)
        self.assertNotIn("the operator's ruling", receipt)
        self.assertNotIn("substitution(s)", receipt)
        self.assertNotIn("- substitutions:", receipt)

    def test_the_receipt_NAMES_a_withheld_tree_the_walk_never_visits(self):
        """`sessions/` sits under no `include` path, so the copy walk never reaches it. If
        the receipt were built from the walk, it would report nothing withheld while a
        whole tree went unmentioned — a clean-looking receipt that had not looked.

        WI-0448: named, NOT counted. How many journals or items the record holds is the
        operator's working volume, which a cold auditor read straight off the counts."""
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.run_out(out_dir)
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("`sessions` — withheld", receipt)
        self.assertNotIn("file(s) present and withheld", receipt)
        self.assertNotIn("withheld in total", receipt)

    def test_an_unselected_sample_SAYS_SO_rather_than_printing_nothing(self):
        """An empty section reads like a clean result. The sample is the third item's to
        select, and the receipt has to state that it is unselected."""
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.run_out(out_dir)
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("UNSELECTED", receipt)

    def test_an_include_that_does_not_exist_yet_is_PENDING_and_not_a_failure(self):
        """NOTICE is authored by the third item in the brief's sequence. Failing here would
        make the manifest un-landable until that item ships, inverting the sequence."""
        code, out, err = self.run_check()
        self.assertEqual(code, 0, err)
        self.assertIn("NOTICE", out)
        self.assertIn("not yet authored", out)

    def test_a_manifest_missing_a_required_list_is_REFUSED_not_defaulted(self):
        """An absent `exclude` cannot be told apart from a boundary that excludes nothing,
        and only one of those is safe to publish."""
        bad = dict(MANIFEST)
        del bad["exclude"]
        p = pathlib.Path(self.tmp.name) / "bad.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = public_cut.main(["--check", "--root", str(self.repo),
                                    "--manifest", str(p)])
        self.assertEqual(code, 2, "a distinct exit code, not the findings code")
        self.assertIn("exclude", err.getvalue())


class ExecutableBitSurvivesTest(PublicCutGateTest):
    """The launcher is started as `./poga`. A cut written with write_text alone ships it
    0644 -- a launcher that cannot launch (WI-0383's first cut)."""

    def test_an_executable_source_is_executable_in_the_cut(self):
        tool = self.repo / "adr" / "run.sh"
        tool.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        tool.chmod(0o755)
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        self.assertTrue(os.access(out_dir / "adr" / "run.sh", os.X_OK))
        self.assertFalse(os.access(out_dir / "adr" / "0001-clean.md", os.X_OK))


class WaivedFindingsAreStillPrintedTest(PublicCutGateTest):
    """An exemption that does not show its work is how a gate rots into decoration."""

    def test_a_waived_finding_appears_in_the_receipt_with_file_and_line(self):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["gate_exempt"] = [{"path": "adr/0002-planted.md",
                               "classes": ["overlay-ip"],
                               "spans": [r"100\.64\.9\.9"],
                               "why": "the fixture exists to carry one"}]
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        self.plant("# ADR-0002\n\nThe host answers on %s.\n" % TS_IP)

        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.run_out(out_dir)
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("## Waived by a named exemption", receipt)
        self.assertIn("adr/0002-planted.md", receipt)
        self.assertIn("overlay-ip", receipt)
        self.assertIn("the fixture exists to carry one", receipt)


class RosterIsReadAtRunTimeTest(PublicCutGateTest):
    """A member added to portfolio.md tomorrow cannot slip into the cut unnamed."""

    def test_a_member_added_to_the_roster_is_caught_without_touching_the_tool(self):
        self.plant("# ADR-0002\n\nThe sundial incident.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, "precondition: `sundial` is not yet a member\n%s" % err)

        (self.repo / "portfolio.md").write_text(
            PORTFOLIO.replace(
                "## Notes",
                "| sundial | (none) | Sundial Architect | `sundial` | `sundial-arch` "
                "| — | Active |\n\n## Notes"),
            encoding="utf-8")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1, "the new member was not caught")
        self.assertIn("member-name", err)


class SampleIsChosenNotEditedTest(PublicCutGateTest):
    """WI-0383. The curated sample passes the gates like any other file, and may be touched
    only by the machine/operator-path rows. A member substitution means the candidate
    carried a member trace -- the table would hide it, and hiding it is not choosing."""

    def set_sample(self, rows, subs=()):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["sample"] = rows
        man["sample_rules"] = {"allowed_kinds": ["host", "operator-path"]}
        man["generalize"]["substitutions"] = list(subs)
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    def journal(self, body, name="20260101T0000Z-boxhost-abcd.md"):
        (self.repo / "sessions" / name).write_text(body, encoding="utf-8")
        return "sessions/%s" % name

    HOST = {"find": "boxhost", "replace": "<host>", "kind": "host", "match_case": True}
    MEMBER = {"find": "barometer", "replace": _alias("Member", "Z"), "kind": "identifier"}

    def test_a_sample_file_is_copied_to_its_TO_path_and_its_why_is_printed(self):
        src = self.journal("A clean journal from boxhost.\n")
        self.set_sample([{"from": src, "to": "sessions/20260101T0000Z-host-abcd.md",
                          "why": "the model journal"}], [self.HOST])
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.run_out(out_dir)
        got = (out_dir / "sessions" / "20260101T0000Z-host-abcd.md").read_text(encoding="utf-8")
        self.assertIn("<host>", got, "the host row applies to a sample like any file")
        self.assertFalse((out_dir / src).exists(), "the sample landed at `from`, not `to`")
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("the model journal", receipt)
        self.assertNotIn("UNSELECTED", receipt)

    def test_a_sample_that_needs_a_MEMBER_substitution_is_REFUSED(self):
        """The whole rule. Paired with the test above: same file shape, one member name."""
        src = self.journal("line one\nThe barometer deploy went red.\n")
        self.set_sample([{"from": src, "to": src, "why": "looked clean"}],
                        [self.HOST, self.MEMBER])
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("sample-edited", err)
        self.assertIn("%s:2" % src, err)

    def test_a_sample_whose_source_is_gone_is_REFUSED_not_skipped(self):
        self.set_sample([{"from": "sessions/nope.md", "to": "sessions/nope.md", "why": "x"}])
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("sample-missing", err)

    def test_a_sample_without_a_why_is_REFUSED(self):
        src = self.journal("clean\n")
        self.set_sample([{"from": src, "to": src, "why": "  "}])
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("sample-malformed", err)


class MatchCaseTest(unittest.TestCase):
    """WI-0382 KEPT the `DevBox` label; the `devbox` host row rewrote it anyway because
    every row was case-blind. `match_case` makes the row mean what its ruling says."""

    def man(self, match_case):
        row = {"find": "boxhost", "replace": "<host>"}
        if match_case:
            row["match_case"] = True
        return {"generalize": {"substitutions": [row], "protect": []}}

    def test_the_label_survives_and_the_host_name_does_not(self):
        text, _h = public_cut.generalize("machine: BoxHost, host boxhost\n", self.man(True))
        self.assertEqual(text, "machine: BoxHost, host <host>\n")

    def test_without_the_flag_the_row_is_case_blind_as_before(self):
        text, _h = public_cut.generalize("machine: BoxHost\n", self.man(False))
        self.assertEqual(text, "machine: <host>\n")


class BlankProfileTest(PublicCutGateTest):
    """`users/` ships ONE blank profile built from the kit template -- the real profile is
    never read, so nothing of it can leak through a reducer."""

    def test_the_profile_ships_from_the_template_and_the_real_one_does_not(self):
        (self.repo / "bootstrap-kit").mkdir()
        (self.repo / "bootstrap-kit" / "user-profile-template.md").write_text(
            "# User profile -- `operator`\n\n_No entries yet._\n", encoding="utf-8")
        (self.repo / "users" / "realname").mkdir(parents=True)
        (self.repo / "users" / "realname" / "profile.md").write_text(
            "# realname likes grams\n", encoding="utf-8")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["exclude"].append({"path": "users", "ships": "blank-operator-profile",
                               "ships_from": "bootstrap-kit/user-profile-template.md",
                               "ships_to": "users/operator/profile.md", "why": "personal"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        got = (out_dir / "users" / "operator" / "profile.md").read_text(encoding="utf-8")
        self.assertIn("_No entries yet._", got)
        self.assertFalse((out_dir / "users" / "realname").exists())


class TheFirstAuditsLeaksTest(PublicCutGateTest):
    """WI-0383's cold audit of the first cut. Each test pins one leak it found, on the
    fixture the gate controls above are paired against."""

    def set_man(self, **kw):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        for k, v in kw.items():
            if k == "subs":
                man["generalize"]["substitutions"] = v
            else:
                man["identity_gate"][k] = v
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    SUB = [{"find": "barometer", "replace": _alias("Member", "Z"), "kind": "identifier"}]

    def test_the_public_receipt_never_quotes_an_original(self):
        """The first receipt printed `original -> placeholder` for every hit: a decoder
        ring for the whole cut. The detail now goes BESIDE the cut, never into it."""
        self.set_man(subs=self.SUB)
        self.plant("# ADR-0002\n\nThe barometer decision.\n")
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        receipt = (out_dir / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertNotIn("barometer", receipt.lower())
        private = public_cut.private_receipt_path(out_dir)
        self.assertNotEqual(private.parent, out_dir)
        self.assertIn("barometer", private.read_text(encoding="utf-8"))

    def test_a_name_inside_a_hyphenated_compound_is_REFUSED(self):
        """`barometer-arch` walked through the first cut: the boundary counted `-` as
        part of the word. Paired with the bare-name control above, which always fired."""
        rel = self.plant("# ADR-0002\n\nSee the barometer-arch brief.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:3" % rel, err)

    def test_a_substring_name_is_caught_inside_camel_case(self):
        self.plant("# ADR-0002\n\nclass BarometerShapeTest: pass\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, "precondition: a bounded match cannot see it\n%s" % err)
        self.set_man(substring_names=["barometer"])
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("member-name", err)

    def test_a_path_carrying_a_member_name_is_renamed_and_its_links_follow(self):
        self.set_man(subs=self.SUB)
        self.plant("# ADR-0003\n\nnotes\n", name="0003-barometer-notes.md")
        self.plant("# ADR-0002\n\nSee [the notes](0003-barometer-notes.md).\n")
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        self.assertTrue((out_dir / "adr" / "0003-a-member-notes.md").is_file())
        self.assertFalse((out_dir / "adr" / "0003-barometer-notes.md").exists())
        self.assertIn("(0003-a-member-notes.md)",
                      (out_dir / "adr" / "0002-planted.md").read_text(encoding="utf-8"))

    def test_a_kit_file_is_not_taken_by_a_root_only_glob(self):
        man = {"exclude": [], "exclude_glob": [
            {"glob": "session-handoff*.md", "root_only": True, "why": "root"}]}
        self.assertIsNone(public_cut._exclude_reason(
            "bootstrap-kit/session-handoff-template.md", man))
        self.assertIsNotNone(public_cut._exclude_reason("session-handoff.md", man))


class TheSecondAuditsLeaksTest(PublicCutGateTest):
    """Round 2 of WI-0383's cold audit: the table broke code, decoded itself through an
    English word, and missed case on literal rows."""

    set_man = TheFirstAuditsLeaksTest.set_man

    def test_an_identifier_is_left_whole_and_a_hyphen_compound_is_not(self):
        text, _h = public_cut.generalize(
            "import barometer_mail\nsee barometer-arch\n",
            {"generalize": {"substitutions": TheFirstAuditsLeaksTest.SUB, "protect": []}})
        self.assertIn("import barometer_mail", text)
        self.assertIn("see a member-arch", text)

    def test_a_host_row_uses_its_code_spelling_inside_python(self):
        """A test on main named a variable for the host, and `<host> = {...}` does not
        compile -- caught by the pre-land re-run against main (WI-0383)."""
        man = {"generalize": {"substitutions": [
            {"find": "boxhost", "replace": "<host>", "code_replace": "somehost"}], "protect": []}}
        self.assertEqual(public_cut.generalize("boxhost = 1\n", man, code=True)[0],
                         "somehost = 1\n")
        self.assertEqual(public_cut.generalize("boxhost = 1\n", man)[0], "<host> = 1\n")

    def test_published_python_that_no_longer_compiles_is_REFUSED(self):
        (self.repo / "adr" / "tool.py").write_text("def ok(:\n", encoding="utf-8")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("code-broken", err)

    def test_a_literal_row_matches_any_case(self):
        text, _h = public_cut.generalize(
            "barometer's row and Barometer's row\n",
            {"generalize": {"substitutions": [
                {"find": "Barometer's", "replace": _alias("Member", "Z") + "'s"}],
             "protect": []}})
        self.assertEqual(text, "A member's row and a member's row\n")

    def test_an_english_roster_id_is_refused_only_in_its_member_forms(self):
        self.plant("# ADR-0002\n\nfleet barometer readings are fine.\n")
        code, _out, _err = self.run_check()
        self.assertEqual(code, 1, "precondition: the bare roster id is refused")
        self.set_man(english_ids=["barometer"])
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)


class TheStrictRulingTest(PublicCutGateTest):
    """WI-0447. operator ruled the cut STRICT: no reader may DESCRIBE a member, not only name
    one. A describing sentence carries no name for the member-name class to see."""

    set_man = TheFirstAuditsLeaksTest.set_man

    MARKERS = [{"re": r"barometric (?:archive|feed)s?", "why": "the fixture member's data",
                "example": "its barometric archive"}]

    def test_a_describing_sentence_passes_the_name_gate_alone(self):
        """The precondition: without the class, the leak is invisible."""
        self.plant("# ADR-0002\n\nOne member's barometric archive filled the disk.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_describing_sentence_is_REFUSED_with_file_and_line(self):
        rel = self.plant("# ADR-0002\n\nline\nOne member's barometric archive filled it.\n")
        self.set_man(descriptive_markers=self.MARKERS)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("descriptive-marker", err)
        self.assertIn("%s:4" % rel, err)
        self.assertNotIn("barometric", err, "a finding that quotes the marker republishes it")

    def test_the_clean_fixture_stays_clean_with_markers_on(self):
        self.set_man(descriptive_markers=self.MARKERS)
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_marker_is_bounded_like_a_name(self):
        self.plant("# ADR-0002\n\nThe nonbarometric archives are fine.\n")
        self.set_man(descriptive_markers=self.MARKERS)
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_protected_phrase_shields_a_marker(self):
        self.plant("# ADR-0002\n\nSee barometric archive rules.\n")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["identity_gate"]["descriptive_markers"] = self.MARKERS
        man["generalize"]["protect"] = [{"phrase": "barometric archive rules"}]
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)


class TheUnlinkedIdentityTest(PublicCutGateTest):
    """The public version is published as one configured identity and must name no account
    the manifest lists as private. A host blocks a push that exposes the publishing
    account's own email; it cannot know which other names to keep out, so the gate must.
    Both fixture identities are invented, so this file ships carrying neither real one;
    the real names live only in the manifest."""

    set_man = TheFirstAuditsLeaksTest.set_man
    PRIV = ["otter-lantern"]
    PUB = "Quill-Harbor/principles-of-good-architects"
    EMAIL = "1+Quill-Harbor" + "@" + "users.noreply.github.com"
    IDENT = {"repo": PUB, "email": EMAIL, "shield": [PUB, EMAIL]}

    def test_a_private_account_name_is_REFUSED_with_file_and_line(self):
        rel = self.plant("# ADR-0002\n\nline\nThe repo lives on otter-lantern.\n")
        self.set_man(private_accounts=self.PRIV)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("private-account", err)
        self.assertIn("%s:4" % rel, err)
        self.assertNotIn("otter", err, "a finding that quotes the handle republishes it")

    def test_no_separator_or_case_hides_it(self):
        self.set_man(private_accounts=self.PRIV)
        for spelling in ("OtterLantern", "otter_lantern", "OTTER.LANTERN", "x/otter-lantern.git"):
            with self.subTest(spelling=spelling):
                self.plant("# ADR-0002\n\nSee %s.\n" % spelling)
                code, _out, err = self.run_check()
                self.assertEqual(code, 1)
                self.assertIn("private-account", err)

    def test_a_protected_phrase_does_not_shield_it(self):
        self.plant("# ADR-0002\n\nSee otter-lantern rules.\n")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["identity_gate"]["private_accounts"] = self.PRIV
        man["generalize"]["protect"] = [{"phrase": "otter-lantern rules"}]
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("private-account", err)

    def test_the_clean_fixture_stays_clean_with_the_class_on(self):
        self.set_man(private_accounts=self.PRIV, public_identity=self.IDENT)
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_the_public_repo_passes_and_a_bare_operator_name_does_not(self):
        """The public owner's name is also the operator's, which the name class refuses.
        Only the shielded literals pass; the same name anywhere else still fires."""
        self.set_man(extra_names=["Quill", "Harbor"], public_identity=self.IDENT)
        self.plant("# ADR-0002\n\nSee https://github.com/%s/blob/main.\n" % self.PUB)
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)
        rel = self.plant("# ADR-0002\n\nAsk Quill-Harbor.\n", name="0003-bare.md")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:3" % rel, err)
        self.assertIn("member-name", err)

    def test_only_the_public_noreply_address_passes(self):
        self.set_man(public_identity=self.IDENT)
        self.plant("# ADR-0002\n\ncommit as %s\n" % self.EMAIL)
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)
        # Another account's noreply address names that account.
        other = "12345+someone" + "@" + "users.noreply.github.com"
        rel = self.plant("# ADR-0002\n\ncommit as %s\n" % other, name="0003-other.md")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:3" % rel, err)
        self.assertIn("email", err)

    def test_a_whole_address_entry_is_exact_not_a_suffix(self):
        self.set_man(email_allow=[".invalid", "noreply" + "@" + "anthropic.com"])
        self.plant("# ADR-0002\n\nCo-Authored-By: <%s>\n" % ("noreply" + "@" + "anthropic.com"))
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)
        self.plant("# ADR-0002\n\nmail %s\n" % ("xnoreply" + "@" + "anthropic.com"),
                   name="0003-x.md")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("email", err)

    def test_a_row_that_writes_the_public_repo_is_not_rewritten_by_a_shorter_row(self):
        man = {"generalize": {"substitutions": [
                   {"find": "otter-lantern/internal", "replace": self.PUB},
                   {"find": "Quill-Harbor", "replace": "operator-account"}]},
               "identity_gate": {"public_identity": self.IDENT}}
        out, _h = public_cut.generalize(
            "https://github.com/otter-lantern/internal.git and Quill-Harbor", man)
        self.assertEqual(out, "https://github.com/%s.git and operator-account" % self.PUB)


class VerifyPublishTest(PublicCutGateTest):
    """WI-0020's pre-push check, over a real scratch clone. The one commit carries only the
    public address, the repo-local config names it, and every tracked file passes the gate.
    Git runs with an empty global and system config, so the operator's own never leaks in."""

    set_man = TheFirstAuditsLeaksTest.set_man
    EMAIL = TheUnlinkedIdentityTest.EMAIL
    IDENT = TheUnlinkedIdentityTest.IDENT
    NAME = "Quill-Harbor"

    def setUp(self):
        super().setUp()
        self.set_man(private_accounts=["otter-lantern"], public_identity=self.IDENT)
        self.clone = pathlib.Path(self.tmp.name) / "clone"
        self.clone.mkdir()
        empty = pathlib.Path(self.tmp.name) / "empty.gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1",
                        HOME=self.tmp.name)
        self.git("init", "-q", "-b", "main")

    def git(self, *args, env=None):
        import subprocess
        subprocess.run(["git", "-C", str(self.clone)] + list(args), check=True,
                       capture_output=True, env=env or self.env)

    def commit(self, files, email=None, msg="POGA public cut", date="2031-01-02T03:04:05+0000"):
        self.git("config", "--local", "user.name", self.NAME)
        self.git("config", "--local", "user.email", self.EMAIL)
        self.git("config", "commit.gpgsign", "false")
        for rel, text in files.items():
            (self.clone / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.clone / rel).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        env = dict(self.env)
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        if email:
            env.update(GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
        self.git("commit", "-q", "-m", msg, env=env)

    def verify(self):
        man = public_cut.load_manifest(self.manifest)
        return public_cut.verify_publish(self.clone, man, self.repo)

    def test_a_commit_date_with_a_zone_offset_is_REFUSED(self):
        """Round-2 audit (WI-0449): the offset on a commit date is the committer's zone."""
        self.commit({"README.md": CLEAN}, date="2031-01-02T03:04:05-0700")
        self.assertIn("non-UTC offset", "\n".join(self.verify()))

    def test_one_clean_commit_under_the_public_address_passes(self):
        self.commit({"README.md": CLEAN})
        self.assertEqual([], self.verify())

    def test_a_second_address_is_REFUSED_and_not_quoted(self):
        other = "someone" + "@" + "company.com"
        self.commit({"README.md": CLEAN}, email=other)
        got = self.verify()
        self.assertTrue(any("not the public noreply address" in p for p in got), got)
        self.assertNotIn(other, "\n".join(got))

    def test_another_author_name_is_REFUSED(self):
        self.commit({"README.md": CLEAN})
        self.git("config", "--local", "user.name", "Somebody Else")
        self.git("commit", "-q", "--amend", "--reset-author", "--no-edit")
        self.assertTrue(any("name is not the public" in p for p in self.verify()))

    def test_a_missing_repo_local_email_is_REFUSED(self):
        self.commit({"README.md": CLEAN})
        self.git("config", "--local", "--unset", "user.email")
        self.assertTrue(any("repo-local" in p for p in self.verify()))

    def test_more_than_one_commit_is_REFUSED(self):
        self.commit({"README.md": CLEAN})
        self.commit({"NOTICE": "x\n"})
        self.assertTrue(any("2 commit(s)" in p for p in self.verify()))

    def test_a_private_name_in_a_file_or_the_message_is_REFUSED(self):
        self.commit({"README.md": "Mirror of OtterLantern's repo.\n"},
                    msg="cut from otter-lantern")
        got = "\n".join(self.verify())
        self.assertIn("README.md:1 [private-account]", got)
        self.assertIn("(commit message)", got)
        self.assertNotIn("otter", got.lower())

    def test_the_private_receipt_is_REFUSED(self):
        self.commit({"README.md": CLEAN, "cut.PRIVATE-RECEIPT.md": CLEAN})
        self.assertTrue(any("private receipt" in p for p in self.verify()))

    def test_the_public_copy_of_the_manifest_cannot_vouch_for_a_push(self):
        """Its private_accounts list is withheld, so a clean read from it proves nothing."""
        self.commit({"README.md": CLEAN})
        self.set_man(private_accounts=[])
        got = self.verify()
        self.assertEqual(1, len(got))
        self.assertIn("internal lane", got[0])


class ReducerTest(unittest.TestCase):

    def test_the_registry_keeps_its_own_row_under_systems(self):
        src = json.dumps({"_comment": ["x"], "systems": {
            "federation": {"repo": "self"}, "barometer": {"repo": "b"}}})
        text, n = public_cut.reduce_registry(src)
        self.assertEqual(list(json.loads(text)["systems"]), ["federation"])
        self.assertEqual(n, 1)

    NOTE_KEYS = ("notes", "_note", "_notes_extra", "_comment", "_comment_2", "//", "//apply")

    def _assert_no_note_key(self, v, where):
        if isinstance(v, dict):
            for k, x in v.items():
                self.assertNotIn(k, self.NOTE_KEYS, f"{where}: a `{k}` note shipped")
                self.assertFalse(k.startswith(("_note", "_comment", "//")) or k == "notes",
                                 f"{where}: a `{k}` note shipped")
                self._assert_no_note_key(x, where)
        elif isinstance(v, list):
            for x in v:
                self._assert_no_note_key(x, where)

    def _planted(self):
        """Every note shape, at the top, nested in a dict and nested inside a list."""
        notes = {k: "measured on the build host" for k in self.NOTE_KEYS}
        return {**notes, "units": ["com.example.sweep"],
                "restart": {"kind": "none", **notes},
                "state": [{"path": "cache", "required": True, **notes}]}

    def test_the_notes_stripped_reducer_drops_every_note_shape_at_every_depth(self):
        """WI-0449, reconstruction audit: deploy.json shipped a long `notes` field of
        measured host facts, because the reducer dropped only `//` keys while the receipt
        said the notes were stripped. A `notes` key planted here must not survive."""
        text, n = public_cut.REDUCERS["json-notes-stripped"](json.dumps(self._planted()))
        data = json.loads(text)
        self._assert_no_note_key(data, "json-notes-stripped")
        self.assertNotIn("notes", data)
        self.assertNotIn("measured on the build host", text)
        # The configuration itself survives.
        self.assertEqual(data["units"], ["com.example.sweep"])
        self.assertEqual(data["restart"], {"kind": "none"})
        self.assertEqual(data["state"], [{"path": "cache", "required": True}])
        self.assertEqual(n, 3 * len(self.NOTE_KEYS))

    def test_every_reducer_that_ships_without_notes_drops_every_shape(self):
        src = self._planted()
        cases = {
            "registry-self-row-only": {**src, "systems": {"federation": {"self": True, **src}}},
            "fleet-cadence-public": {**src, "systems": {"federation": {"days": 1, **src}}},
            "session-config-public": src,
            "empty-mailboxes": src,
        }
        for name, doc in cases.items():
            with self.subTest(reducer=name):
                text, _n = public_cut.REDUCERS[name](json.dumps(doc))
                self.assertNotIn("measured on the build host", text)
                data = json.loads(text)
                # The public session config and cadence carry ONE authored note of their
                # own, which says nothing about the source; nothing else may survive.
                authored = {"session-config-public": "//", "fleet-cadence-public": "_note"}
                data.pop(authored.get(name, ""), None)
                self._assert_no_note_key(data, name)

    def test_a_ruled_out_record_ships_masked_with_the_bare_reason(self):
        """WI-0449: the public manifest and receipt say only that a record is withheld
        from this copy -- never what kind of record it is or whose."""
        src = json.dumps({"generalize": {}, "identity_gate": {}, "include": [],
                          "exclude": [{"path": "adr/0999-some-kind-of-thing.md",
                                       "why": "withheld from this copy (the operator's ruling, 2026-09-28)"}]})
        data = json.loads(public_cut.manifest_public(src)[0])
        row = data["exclude"][0]
        self.assertEqual(row["path"], public_cut.MASKED_PATH)
        self.assertEqual(row["why"], public_cut.WITHHELD_WHY)
        self.assertNotIn("member", public_cut.MASKED_PATH + public_cut.WITHHELD_WHY)

    def test_the_manifest_ships_without_its_name_lists(self):
        src = json.dumps({"generalize": {"substitutions": [{"find": "x", "replace": "y"}],
                                         "protect": [{"phrase": "x ray"}]},
                          "identity_gate": {"extra_names": ["x"],
                                            "descriptive_markers": [{"re": "x"}],
                                            "private_accounts": ["x"],
                                            "_private_accounts_note": ["x"]},
                          "include": []})
        text, n = public_cut.manifest_public(src)
        data = json.loads(text)
        self.assertEqual(data["generalize"]["substitutions"], [])
        self.assertEqual(data["generalize"]["protect"], [])
        self.assertEqual(data["identity_gate"]["extra_names"], [])
        # A list of domains to refuse is a description of the members it hides.
        self.assertEqual(data["identity_gate"]["descriptive_markers"], [])
        # The private account's names ARE the link the class refuses.
        self.assertEqual(data["identity_gate"]["private_accounts"], [])
        self.assertNotIn("_private_accounts_note", data["identity_gate"])
        self.assertIn("_withheld", data["generalize"])
        self.assertEqual(n, 5)

    def test_gitignore_re_includes_only_what_the_cut_publishes(self):
        text, _n = public_cut.gitignore_public(
            "users\n/other/\n# users\n",
            {"published": ["users/operator/profile.md", "adr/0001.md"]})
        self.assertIn("\n!users\n", text)
        self.assertNotIn("!/other/", text)


def _machine_masked(rel):
    """A session id's machine segment (`...Z-<machine>-a1b2.md`) replaced by one token."""
    import re
    return re.sub(r"Z-[^/]+-([0-9a-f]{4}\.md)$", r"Z-M-\1", rel)


class TheRealManifestTest(unittest.TestCase):
    """The shipped boundary, read against the tree it sits in. These run in the internal
    trunk AND in the exported cut, which is why a sample is resolved at `to` when `from` is
    absent, and why the member roster may legitimately be empty (the cut's portfolio.md
    ships as a shell)."""

    @classmethod
    def setUpClass(cls):
        cls.man = public_cut.load_manifest(ROOT / "curate" / "public_cut_manifest.json")
        cls.roster = public_cut.member_roster(
            ROOT, cls.man["generalize"].get("roster_exempt", []))
        # The internal trunk, not the exported cut. The roster cannot tell them apart: the
        # cut's portfolio overlay ships example rows. The withheld name lists can.
        ig = cls.man["identity_gate"]
        cls.internal = bool(ig.get("descriptive_markers") or ig.get("private_accounts"))

    def test_every_sample_entry_is_well_formed(self):
        """The sample's SHAPE, read from the manifest alone.

        Its CONTENT -- both gates, and no member substitution -- is checked by
        `public_cut.py --check` at cut time (`sample-edited`, `sample-missing`), and the
        refusals are proven on fixtures above. It is deliberately not re-read here: the
        sample lives in the work-item, ops and journal stores, bookkeeping lands skip the
        suite (ADR-0148 D3), so a test over those files could go red with no code land
        anywhere near it."""
        rows = self.man["sample"]
        self.assertTrue(rows, "the sample is empty")
        tos = [r.get("to") for r in rows]
        self.assertEqual(len(tos), len(set(tos)), "two sample entries share a `to`")
        includes = [r["path"] for r in self.man["include"]]
        for row in rows:
            with self.subTest(sample=row.get("from")):
                self.assertTrue((row.get("why") or "").strip(), "no `why`")
                # `to` differs from `from` only in the session id's machine segment,
                # which follows the content table's host row.
                # The cut's host row rewrites the machine name in BOTH this line and the
                # manifest, differently (code vs prose spelling), so the segment is masked
                # rather than named: the test holds in the trunk and in the exported cut.
                self.assertEqual(_machine_masked(row["from"]), _machine_masked(row["to"]))
                self.assertFalse(
                    any(row["to"] == i or row["to"].startswith(i + "/") for i in includes),
                    "a sample `to` inside an include path would collide with the walk")

    def test_every_top_level_tracked_path_is_a_decision(self):
        """A path in neither `include` nor `exclude` is invisible to the receipt: it is
        neither published nor listed as withheld. WI-0383 found seventeen of them, the
        launcher among them."""
        import subprocess
        try:
            ls = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        named = {r["path"].split("/")[0] for r in self.man["include"] + self.man["exclude"]}
        # An `add` row's path exists only in the exported cut (`.github/`), where this
        # test also runs; there it is a decision too.
        named |= {r["path"].split("/")[0] for r in self.man.get("add", [])}
        named.add(public_cut.RECEIPT_NAME)
        loose = sorted({p.split("/")[0] for p in ls} - named)
        self.assertEqual([], loose)

    def test_the_internal_tree_carries_no_added_path(self):
        """An `add` row's file exists only in the public cuts. The internal origin is a
        private repo that must run no Actions, so it must not carry a root `.github/`."""
        if not (ROOT / public_cut.OVERLAY_DIR).is_dir():
            self.skipTest("the exported cut: the added files belong here")
        for row in self.man.get("add", []):
            with self.subTest(add=row.get("path")):
                self.assertTrue(row.get("from", "").startswith(public_cut.OVERLAY_DIR + "/"))
                self.assertFalse((ROOT / row["path"]).exists())
        self.assertFalse((ROOT / ".github").exists(),
                         "the internal repo must not carry a root .github/")

    def test_every_descriptive_marker_fires_on_its_own_example(self):
        """A marker that cannot match reads exactly like one that works. Each row carries
        the sentence shape it exists to catch, and must catch it. The cut ships the list
        withheld, so there it is empty and only the internal trunk is held to non-empty."""
        rows = self.man["identity_gate"].get("descriptive_markers", [])
        if self.internal:
            self.assertTrue(rows, "the strict cut has no descriptive markers")
        for row in rows:
            with self.subTest(marker=row.get("re")):
                self.assertTrue((row.get("why") or "").strip(), "no `why`")
                self.assertTrue(public_cut.marker_pattern(row["re"]).search(row["example"]))

    def test_the_private_account_list_is_not_empty(self):
        """The cut ships the list withheld, so only the internal trunk is held to it. No
        digest of a name is pinned here: this file ships, and an unsalted hash of a short
        handle is a VERIFIER -- anyone who guesses the name can confirm it (cold audit,
        2026-09-27). The spelling test below proves each listed name is caught."""
        if not self.internal:
            self.skipTest("the public cut withholds the list")
        self.assertTrue(self.man["identity_gate"].get("private_accounts"))

    def test_every_spelling_of_the_private_owner_is_generalized_away(self):
        """The table must leave the gate nothing to refuse: each form the trunk uses."""
        names = self.man["identity_gate"].get("private_accounts", [])
        if not self.internal:
            self.skipTest("the public cut withholds the list")
        owner = names[0]
        for line in ("https://github.com/%s/internal-repo.git" % owner,
                     "$id https://github.com/%s/principles-of-good-architects/x.json" % owner,
                     '"repo_owner": "%s",' % owner, "on `%s` account, private" % owner,
                     "| `%s` |" % owner, 'repo_owner "%s"' % owner,
                     "gh auth switch --user %s" % owner, "the `%s` org" % owner,
                     "https://github.com/%s/Member 2" % owner):
            with self.subTest(line=line):
                out, _h = public_cut.generalize(line, self.man)
                found = public_cut.identity_findings(out, "x.md", self.man, [])
                self.assertEqual([], [f for f in found if f.cls == "private-account"], out)

    def test_the_public_identity_is_the_noreply_address(self):
        ident = self.man["identity_gate"]["public_identity"]
        self.assertEqual(ident["repo"], "duckin-alabcoat/principles-of-good-architects")
        self.assertEqual(ident["email"], "49006243+duckin-alabcoat@users.noreply.github.com")
        self.assertIn(ident["repo"], ident["shield"])

    def test_the_front_matter_exists_and_the_readme_leads_with_the_problem(self):
        first = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertNotIn("Principles of Good Architects", first)
        for name in ("AGENTS.md", "LICENSE", "NOTICE"):
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertIn("Apache License", (ROOT / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("Version 2.0", (ROOT / "LICENSE").read_text(encoding="utf-8"))


class VerifyPublishScansCommittedBytesTest(VerifyPublishTest):
    """Consultant brief 2026-09-27, item 5. The check read the WORKING TREE after listing
    tracked files, so a committed leak under an uncommitted cleanup passed. It now gates
    the blobs of the one commit and refuses a working tree that is not that commit."""

    LEAK = "Mirror of OtterLantern's repo.\n"

    def test_a_committed_leak_under_a_clean_working_edit_is_REFUSED(self):
        self.commit({"README.md": self.LEAK})
        (self.clone / "README.md").write_text(CLEAN, encoding="utf-8")
        got = "\n".join(self.verify())
        self.assertIn("README.md:1 [private-account]", got)
        self.assertIn("working tree differs", got)
        self.assertNotIn("otter", got.lower())

    def test_a_leak_hidden_from_status_is_still_caught_in_the_blob(self):
        """skip-worktree makes the clean edit invisible to `git status`, so only a read of
        the committed blob can see the leak -- the dirty check alone would pass it."""
        self.commit({"README.md": self.LEAK})
        self.git("update-index", "--skip-worktree", "README.md")
        (self.clone / "README.md").write_text(CLEAN, encoding="utf-8")
        got = self.verify()
        self.assertFalse(any("working tree" in p for p in got), got)
        self.assertIn("README.md:1 [private-account]", "\n".join(got))

    def test_an_untracked_file_is_REFUSED(self):
        self.commit({"README.md": CLEAN})
        (self.clone / "stray.md").write_text(CLEAN, encoding="utf-8")
        self.assertTrue(any("untracked" in p for p in self.verify()))

    def test_a_tag_name_carrying_the_private_name_is_REFUSED_and_not_quoted(self):
        self.commit({"README.md": CLEAN})
        self.git("tag", "otter-lantern-v1")
        got = "\n".join(self.verify())
        self.assertIn("(ref names)", got)
        self.assertNotIn("otter", got.lower())

    def test_an_annotated_tag_message_is_gated(self):
        self.commit({"README.md": CLEAN})
        self.git("tag", "-a", "v1", "-m", "cut from otter-lantern")
        got = "\n".join(self.verify())
        self.assertIn("(annotated tag)", got)

    def test_a_binary_blob_is_REFUSED_not_skipped(self):
        self.commit({"README.md": CLEAN, "logo.png": "not really a png\n"})
        self.assertTrue(any("logo.png: a binary blob" in p for p in self.verify()))

    def run_verify_cli(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = public_cut.main(["--verify-publish", str(self.clone), "--root",
                                    str(self.repo), "--manifest", str(self.manifest)])
        return code, out.getvalue(), err.getvalue()

    def hashes(self):
        import subprocess
        run = lambda *a: subprocess.run(["git", "-C", str(self.clone)] + list(a),
                                        capture_output=True, text=True, env=self.env,
                                        check=True).stdout.strip()
        return run("rev-parse", "HEAD"), run("rev-parse", "HEAD^{tree}")

    def test_the_output_names_the_commit_and_tree_it_checked_clean(self):
        self.commit({"README.md": CLEAN})
        code, out, err = self.run_verify_cli()
        self.assertEqual(code, 0, err)
        commit, tree = self.hashes()
        self.assertIn("commit %s tree %s" % (commit, tree), out)

    def test_the_output_names_the_commit_and_tree_it_checked_refused(self):
        self.commit({"README.md": self.LEAK})
        code, _out, err = self.run_verify_cli()
        self.assertEqual(code, 1)
        commit, tree = self.hashes()
        self.assertIn("commit %s tree %s" % (commit, tree), err)
        self.assertIn("DO NOT PUSH", err)


class FailClosedOutTest(PublicCutGateTest):
    """Consultant brief 2026-09-27, item 6. `--out` wrote the export BEFORE it refused. A
    refused cut now lands only at `<out>.REJECTED`; `<out>` is never touched by one."""

    def setUp(self):
        super().setUp()
        self.out = pathlib.Path(self.tmp.name) / "out"
        self.rej = public_cut.rejected_path(self.out)

    def previous_clean_cut(self):
        self.assertEqual(self.run_out(self.out), 0)
        (self.out / "SENTINEL").write_text("the last clean cut\n", encoding="utf-8")

    def test_a_refused_cut_goes_to_REJECTED_and_out_is_untouched(self):
        self.previous_clean_cut()
        self.rej.mkdir()
        (self.rej / "stale.md").write_text("old\n", encoding="utf-8")
        self.plant("# ADR-0002\n\nThe barometer port collision.\n")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = public_cut.main(["--out", str(self.out), "--root", str(self.repo),
                                    "--manifest", str(self.manifest)])
        self.assertEqual(code, 1)
        self.assertEqual((self.out / "SENTINEL").read_text(encoding="utf-8"),
                         "the last clean cut\n")
        self.assertFalse((self.out / "adr" / "0002-planted.md").exists(),
                         "the refused file reached the publishable path")
        note = (self.rej / public_cut.REJECTED_NOTE).read_text(encoding="utf-8")
        self.assertIn("1 finding(s)", note)
        self.assertIn("adr/0002-planted.md:3", note)
        self.assertNotIn("barometer", note, "the note must not quote what it caught")
        self.assertFalse((self.rej / "stale.md").exists(), "the old REJECTED was not replaced")
        self.assertIn(str(self.rej), err.getvalue())
        private = public_cut.private_receipt_path(self.rej)
        self.assertTrue(private.is_file())
        self.assertNotEqual(private.parent, self.rej)

    def test_a_clean_cut_replaces_out_and_removes_a_stale_REJECTED(self):
        self.previous_clean_cut()
        self.rej.mkdir()
        (self.rej / public_cut.REJECTED_NOTE).write_text("old\n", encoding="utf-8")
        public_cut.private_receipt_path(self.rej).write_text("old\n", encoding="utf-8")
        self.assertEqual(self.run_out(self.out), 0)
        self.assertFalse((self.out / "SENTINEL").exists(), "the old cut was not replaced")
        self.assertTrue((self.out / "adr" / "0001-clean.md").is_file())
        self.assertFalse(self.rej.exists())
        self.assertFalse(public_cut.private_receipt_path(self.rej).exists())
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".out.")]
        self.assertEqual([], leftovers, "a staging directory was left behind")

    def test_a_write_that_dies_halfway_leaves_out_as_it_was(self):
        self.previous_clean_cut()
        real = public_cut._write_tree

        def dies(cut, dest):
            real(cut, dest)
            raise OSError("disk full")

        with mock.patch.object(public_cut, "_write_tree", dies):
            with self.assertRaises(OSError):
                self.run_out(self.out)
        self.assertTrue((self.out / "SENTINEL").is_file())
        leftovers = [p.name for p in self.out.parent.iterdir() if p.name.startswith(".out.")]
        self.assertEqual([], leftovers)


class OverlayTest(PublicCutGateTest):
    """Consultant brief 2026-09-27, item 2 and the `replace` list. An authored generic
    version at public-overlay/<path> ships instead of the source, gated like any file,
    and every way it can fail to happen is a refusal."""

    OVERLAY_PORTFOLIO = "# Portfolio registry\n\nA template. No rows.\n"

    def set_replace(self, rows, portfolio_slot=True):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["replace"] = rows
        if portfolio_slot:
            man["exclude"].append({"path": "portfolio.md", "ships": "public-overlay",
                                   "why": "the roster"})
        man["exclude"].append({"path": "public-overlay", "ships": None, "why": "overlays"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    def overlay(self, rel, text):
        p = self.repo / "public-overlay" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return "public-overlay/" + rel

    def build(self):
        return public_cut.build(self.repo, public_cut.load_manifest(self.manifest))[0]

    def test_portfolio_ships_the_overlay_and_the_roster_is_still_read_from_the_root(self):
        src = self.overlay("portfolio.md", self.OVERLAY_PORTFOLIO)
        self.set_replace([{"path": "portfolio.md", "from": src, "why": "authored template"}])
        self.assertEqual(self.run_out(pathlib.Path(self.tmp.name) / "out"), 0)
        out = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual((out / "portfolio.md").read_text(encoding="utf-8"),
                         self.OVERLAY_PORTFOLIO)
        self.assertFalse((out / "public-overlay").exists(), "the overlay tree was copied")
        receipt = (out / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("\n- `portfolio.md`\n", receipt)
        self.assertNotIn(src, receipt, "the receipt names an overlay's source path")
        # The member-name class reads the REAL roster, not the overlay.
        self.plant("# ADR-0002\n\nThe barometer port collision.\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("member-name", err)

    def test_without_its_replace_row_the_overlay_slot_ships_nothing(self):
        self.set_replace([])
        cut = self.build()
        self.assertNotIn("portfolio.md", [r for r, _t in cut.files])
        self.assertIn("portfolio.md", [r for r, _s, _w in cut.owed])

    def test_an_included_file_ships_its_overlay_instead_of_the_source(self):
        src = self.overlay("adr/0001-clean.md", "# ADR-0001\n\nThe generic version.\n")
        self.set_replace([{"path": "adr/0001-clean.md", "from": src, "why": "generic"}],
                         portfolio_slot=False)
        files = dict(self.build().files)
        self.assertEqual(files["adr/0001-clean.md"], "# ADR-0001\n\nThe generic version.\n")

    def test_the_overlay_is_gated_like_any_file(self):
        src = self.overlay("adr/0001-clean.md", "# ADR-0001\n\nline\nThe barometer rule.\n")
        self.set_replace([{"path": "adr/0001-clean.md", "from": src, "why": "generic"}],
                         portfolio_slot=False)
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("adr/0001-clean.md:4", err)

    def test_a_missing_overlay_is_REFUSED_and_the_source_does_not_ship(self):
        self.set_replace([{"path": "adr/0001-clean.md", "from": "public-overlay/adr/nope.md",
                           "why": "generic"}], portfolio_slot=False)
        cut = self.build()
        self.assertIn("overlay-missing", [f.cls for f in cut.findings])
        self.assertNotIn("adr/0001-clean.md", [r for r, _t in cut.files])

    def test_an_overlay_for_a_path_not_otherwise_published_is_REFUSED(self):
        src = self.overlay("adr/9999-new.md", "# new\n")
        self.set_replace([{"path": "adr/9999-new.md", "from": src, "why": "x"}],
                         portfolio_slot=False)
        cut = self.build()
        self.assertIn("overlay-orphan", [f.cls for f in cut.findings])
        self.assertNotIn("adr/9999-new.md", [r for r, _t in cut.files])

    def test_an_overlay_of_an_excluded_path_is_REFUSED(self):
        (self.repo / "sessions" / "x.md").write_text("private\n", encoding="utf-8")
        src = self.overlay("sessions/x.md", "generic\n")
        self.set_replace([{"path": "sessions/x.md", "from": src, "why": "x"}],
                         portfolio_slot=False)
        self.assertIn("overlay-orphan", [f.cls for f in self.build().findings])

    def test_a_from_outside_the_overlay_tree_is_REFUSED(self):
        self.set_replace([{"path": "adr/0001-clean.md", "from": "sessions/journal.md",
                           "why": "x"}], portfolio_slot=False)
        self.assertIn("overlay-malformed", [f.cls for f in self.build().findings])

    def test_a_manifest_without_replace_is_REFUSED(self):
        bad = dict(MANIFEST)
        del bad["replace"]
        p = pathlib.Path(self.tmp.name) / "bad.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(public_cut.ManifestError):
            public_cut.load_manifest(p)


class DisclosureClassTest(PublicCutGateTest):
    """Consultant brief 2026-09-27, items 1 and 4: prose that lets a reader reconstruct the
    operator's network, whereabouts or habits. The vocabulary here is INVENTED -- this file
    ships, and the real vocabulary is held to its own examples in TheRealManifestTest."""

    CLS = {"cls": "zz-topology", "why": "describes the invented network",
           "markers": [{"re": r"zorblat\s+segments?", "why": "x", "example": "the zorblat segment"}]}

    def set_disclosure(self, exempt=()):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["identity_gate"]["disclosure_classes"] = [self.CLS]
        man["identity_gate"]["disclosure_exempt"] = list(exempt)
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    def test_the_clean_fixture_stays_clean_with_the_class_on(self):
        self.set_disclosure()
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_describing_sentence_is_REFUSED_with_file_and_line_and_not_quoted(self):
        self.set_disclosure()
        rel = self.plant("# ADR-0002\n\nline\nThe Zorblat Segment is untrusted.\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("[zz-topology]", err)
        self.assertIn("%s:4" % rel, err)
        self.assertNotIn("zorblat", err.lower())

    def test_it_is_bounded_like_a_name(self):
        self.set_disclosure()
        self.plant("# ADR-0002\n\nxzorblat segmentsy\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)

    BODY = "# ADR-0002\n\nline\nthe zorblat segment in the lab example\nline\na zorblat segment\n"

    def test_a_phrase_exemption_shields_that_occurrence_only(self):
        rel = self.plant(self.BODY)
        self.set_disclosure([{"path": rel, "phrase": "zorblat segment in the lab",
                              "why": "the worked example"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:6" % rel, err)
        self.assertNotIn("%s:4" % rel, err)
        self.assertNotIn("disclosure-exempt", err)

    def test_a_line_re_exemption_works_and_the_waiver_is_printed(self):
        rel = self.plant("# ADR-0002\n\nthe zorblat segment, by design\n")
        self.set_disclosure([{"path": rel, "line_re": r"zorblat segment, by design$",
                              "why": "the worked example"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)
        out = pathlib.Path(self.tmp.name) / "out"
        self.run_out(out)
        receipt = (out / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("`%s`:3 **[zz-topology]** — the worked example" % rel, receipt)

    def test_a_line_re_that_matches_BESIDE_the_hit_does_not_waive_it(self):
        """Round-2 review, item 5: `by design$` matched the line, so every disclosure on it
        was waived. The match span must CONTAIN the hit."""
        rel = self.plant("# ADR-0002\n\nline\nthe zorblat segment is open, by design\n")
        self.set_disclosure([{"path": rel, "line_re": r"by design$", "why": "x"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("[zz-topology]", err)
        self.assertIn("%s:4" % rel, err)
        # ...and the row that no longer shields anything says so.
        self.assertIn("disclosure-exempt-stale", err)

    def test_an_exemption_that_matches_many_lines_is_REFUSED(self):
        """One that matches every line has quietly become a file exemption."""
        rel = self.plant(self.BODY)
        self.set_disclosure([{"path": rel, "phrase": "zorblat segment", "why": "x"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("disclosure-exempt-stale", err)

    def test_an_exemption_that_shields_nothing_is_REFUSED(self):
        rel = self.plant("# ADR-0002\n\nnothing here\n")
        self.set_disclosure([{"path": rel, "phrase": "nothing here", "why": "x"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("disclosure-exempt-stale", err)

    def test_an_exemption_for_a_file_the_cut_does_not_publish_is_REFUSED(self):
        self.set_disclosure([{"path": "adr/gone.md", "phrase": "x", "why": "x"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("disclosure-exempt-stale", err)

    def test_an_exemption_without_a_why_is_REFUSED(self):
        rel = self.plant("# ADR-0002\n\nthe zorblat segment\n")
        self.set_disclosure([{"path": rel, "phrase": "zorblat segment"}])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("disclosure-exempt-malformed", err)

    def test_the_public_manifest_withholds_the_vocabulary_and_the_path_rows(self):
        src = json.dumps({"generalize": {}, "include": [], "exclude": [],
                          "replace": [{"path": "adr/0001-barometer.md",
                                       "from": "public-overlay/adr/0001-barometer.md"}],
                          "rename": [{"from": "adr/0004-zorblat-segment.md",
                                      "to": "adr/0004-a-rule.md"}],
                          "identity_gate": {"disclosure_classes": [self.CLS],
                                            "disclosure_exempt": [
                                                {"path": "adr/0002-barometer.md", "phrase": "p"},
                                                {"path": "adr/0003.md", "phrase": "p"}]}})
        text, n = public_cut.manifest_public(src, {"terms": ["barometer"]})
        data = json.loads(text)
        self.assertEqual(data["identity_gate"]["disclosure_classes"], [])
        self.assertEqual(data["identity_gate"]["disclosure_exempt"], [])
        self.assertEqual(data["replace"], [])
        self.assertEqual(data["rename"], [])
        self.assertEqual(n, 5)
        for leak in ("barometer", "zorblat"):
            self.assertNotIn(leak, text)


class TheRealManifestDisclosureTest(unittest.TestCase):
    """The disclosure vocabulary and overlay list, read from the real manifest. The public
    copy withholds all of it, so in the exported cut this class has nothing to hold."""

    @classmethod
    def setUpClass(cls):
        cls.man = public_cut.load_manifest(ROOT / "curate" / "public_cut_manifest.json")

    def setUp(self):
        if not self.man["identity_gate"].get("private_accounts"):
            self.skipTest("the public copy of the manifest withholds these lists")

    def test_the_three_classes_exist(self):
        got = {r["cls"] for r in self.man["identity_gate"].get("disclosure_classes", [])}
        self.assertLessEqual({"network-topology", "operator-availability",
                              "operator-preference", "identity-arrangement"}, got)

    def test_identity_arrangement_is_quiet_on_the_generic_facility(self):
        """The class refuses prose about how publication identities relate (its examples
        hold the positive side). It must stay quiet on the words the generic facility
        and unrelated code use, or it fires on correct prose and gets switched off."""
        for line in ("The brief is not a second account of the deploy.",
                     "ADR-0007 settled on a private account, private, for the repo.",
                     "a name of the private account survived",
                     "Which publication identity the cut is for"):
            with self.subTest(line=line):
                found, _w = public_cut.disclosure_findings(line + "\n", "x.md", self.man)
                self.assertNotIn("identity-arrangement", [f.cls for f in found])

    def test_every_disclosure_marker_fires_on_its_own_example(self):
        for row in self.man["identity_gate"]["disclosure_classes"]:
            self.assertTrue((row.get("why") or "").strip(), row["cls"])
            self.assertTrue(row.get("markers"), row["cls"])
            for m in row["markers"]:
                with self.subTest(cls=row["cls"], marker=m.get("re")):
                    self.assertTrue((m.get("why") or "").strip(), "no `why`")
                    self.assertTrue(public_cut.marker_pattern(m["re"]).search(m["example"]))
                    found, _w = public_cut.disclosure_findings(
                        m["example"] + "\n", "x.md", self.man)
                    self.assertIn(row["cls"], [f.cls for f in found])

    def test_every_exemption_is_value_level(self):
        for row in self.man["identity_gate"].get("disclosure_exempt", []):
            with self.subTest(row=row):
                self.assertTrue(row.get("path"))
                self.assertTrue((row.get("why") or "").strip())
                self.assertEqual(1, sum(bool(row.get(k)) for k in ("phrase", "line_re")))

    def test_the_overlay_tree_is_excluded_and_every_replace_row_is_under_it(self):
        self.assertIn(public_cut.OVERLAY_DIR, [r["path"] for r in self.man["exclude"]])
        for row in self.man["replace"]:
            if row["path"] == public_cut.MASKED_PATH:
                continue  # the public copy masks a member-named row
            with self.subTest(path=row["path"]):
                self.assertTrue(row["from"].startswith(public_cut.OVERLAY_DIR + "/"))
                self.assertTrue((row.get("why") or "").strip())

    def test_portfolio_ships_only_through_its_overlay(self):
        row = [r for r in self.man["exclude"] if r["path"] == "portfolio.md"][0]
        self.assertEqual(row["ships"], public_cut.OVERLAY_SHIPS)
        self.assertIn("portfolio.md", [r["path"] for r in self.man["replace"]])


class SessionConfigPublicTest(unittest.TestCase):
    """WI-0448: the session config ships generated from the live file, with the operator's
    time zone, machine names and profile path replaced and every `//` note dropped."""

    SRC = json.dumps({
        "//": "top note", "timezone": "Pacific/Zorblat", "user_profile": "users/zed/profile.md",
        "machine_map": {"Zed's Zorblat Pro": "Laptop"}, "gate": ["x"],
        "settings_extras": {"//inner": "a note about zed's terminal", "allow": ["y"]},
        "//gate": "measured on zed's box"})

    def test_values_replaced_and_notes_dropped(self):
        text, n = public_cut.session_config_public(self.SRC)
        data = json.loads(text)
        self.assertEqual(data["timezone"], "UTC")
        self.assertEqual(data["user_profile"], "users/operator/profile.md")
        self.assertEqual(data["machine_map"],
                         public_cut.PUBLIC_SESSION_CONFIG["machine_map"])
        self.assertEqual(data["gate"], ["x"])
        self.assertEqual(data["settings_extras"], {"allow": ["y"]})
        self.assertEqual([k for k in data if k.startswith("//")], ["//"])
        self.assertNotIn("zed", text.lower())
        self.assertNotIn("Zorblat", text)
        self.assertEqual(n, 6)  # three notes dropped, three values replaced

    def test_the_real_manifest_ships_the_config_through_it(self):
        man = public_cut.load_manifest(ROOT / "curate" / "public_cut_manifest.json")
        rows = [r for r in man["exclude"] if r["path"] == "session.config.json"]
        self.assertEqual([r.get("ships") for r in rows], ["session-config-public"])


class RelabelAndDealiasTest(unittest.TestCase):
    """WI-0448 round 1: role labels are relabelled by substring, case-preserved, and no
    letter alias survives to let a reader join facts across files."""

    MAN = {"generalize": {"substitutions": [], "protect": [],
                          "relabel": [{"find": "Zorblat", "replace": "Runner"}]}}

    def test_relabel_is_substring_and_case_preserving(self):
        text, n = public_cut.relabel("Zorblat, zorblat_mail, ZORBLAT_ONLY, the Zorblat's\n",
                                     self.MAN)
        self.assertEqual(text, "Runner, runner_mail, RUNNER_ONLY, the Runner's\n")
        self.assertEqual(n, 4)

    def test_generalize_runs_both_passes(self):
        text, _hits = public_cut.generalize(
            "%s runs on the Zorblat.\n" % _alias("Member", "Q"), self.MAN)
        self.assertEqual(text, "A member runs on the Runner.\n")

    def test_no_letter_alias_survives(self):
        m = lambda x: _alias("Member", x)
        text, n = public_cut.dealias(
            "The %s broker; %s's cli; %s; see %s-arch.\n- %s runs\n"
            % (m("E"), m("E"), _alias("Orchestrator", "B"), m("K"), m("M")))
        self.assertNotRegex(text, r"(Member|Orchestrator|Agent) [A-Z]\b")
        self.assertEqual(n, 5)
        self.assertIn("A member broker", text)
        self.assertIn("\n- A member runs", text)

    def test_prose_loses_session_clock_times_and_kebab_aliases_but_code_keeps_them(self):
        src = "see 20260717T2036Z-laptop-a3f9 and `%s-arch`\n" % _alias("member", "m", "-")
        prose, _h = public_cut.generalize(src, self.MAN)
        self.assertEqual(prose, "see 20260717-a3f9 and `example-member-arch`\n")
        code, _h = public_cut.generalize(src, self.MAN, code=True)
        self.assertEqual(code, "see 20260717T2036Z-laptop-a3f9 and `member-1-arch`\n")
        path, _h = public_cut.generalize("20260101T0000Z-host-abcd.md", self.MAN, prose=False)
        self.assertEqual(path, "20260101T0000Z-host-abcd.md")

    def test_prose_loses_iso_clock_times_but_code_keeps_them(self):
        """Round-2 audit (WI-0449): two land timestamps beside each other said when the
        operator works. Prose keeps the date; the time and the offset go."""
        src = ("no event since 2031-01-02T03:04; | 2031-01-02 05:06:07+09:00 lane |"
               " at 2031-01-03T01:02:03Z, version 2031-01-03\n")
        prose, _h = public_cut.generalize(src, self.MAN)
        self.assertEqual(prose, "no event since 2031-01-02; | 2031-01-02 lane |"
                                " at 2031-01-03, version 2031-01-03\n")
        code, _h = public_cut.generalize(src, self.MAN, code=True)
        self.assertEqual(code, src)

    def test_code_keeps_distinct_aliases_distinct_per_file(self):
        """Round 3: prose collapsing turned a set of three parked ids into one string."""
        m = lambda x: _alias("Member", x)
        src = 'PARKED = {"%s", "%s", "%s"}  # %s first\n' % (
            m("P"), m("Q"), m("N"), _alias("member", "p", "-"))
        text, _h = public_cut.generalize(src, self.MAN, code=True)
        self.assertEqual(text,
                         'PARKED = {"Member 1", "Member 2", "Member 3"}  # member-1 first\n')

    def test_the_public_manifest_withholds_the_relabel_table(self):
        src = json.dumps({"generalize": {"relabel": [{"find": "Zorblat", "replace": "Runner"}],
                                         "review": [{"term": "Zorblat"}]},
                          "include": [], "exclude": [], "identity_gate": {}})
        text, _n = public_cut.manifest_public(src, {"terms": []})
        self.assertNotIn("Zorblat", text)


class VerifyPublishGatesPathNamesTest(VerifyPublishTest):
    """Round-2 review, item 3: the check gated each blob's BODY and never its path, so a
    commit adding `<private-account>-private-project.md` with clean content passed. Every
    committed path and directory name now goes through every class, with no exemption."""

    ACCT = "otter" + "-" + "lantern"  # assembled: this file ships

    def test_a_clean_nested_path_passes(self):
        """The positive control the two refusals below are read against."""
        self.commit({"README.md": CLEAN, "docs/guide/notes.md": CLEAN})
        self.assertEqual([], self.verify())

    def test_a_private_account_in_a_FILENAME_is_REFUSED_with_the_path_named(self):
        self.commit({"README.md": CLEAN, "docs/%s-private-project.md" % self.ACCT: CLEAN})
        got = "\n".join(self.verify())
        self.assertIn("docs/<withheld> (blob ", got)
        self.assertIn("[private-account] the committed PATH", got)
        self.assertNotIn("otter", got.lower(), "a problem that quotes the name republishes it")

    def test_a_member_name_in_a_DIRECTORY_is_REFUSED_with_the_path_named(self):
        """`barometer` is the fixture roster's member (a temp-dir portfolio.md)."""
        self.commit({"README.md": CLEAN, "barometer-notes/readme.md": CLEAN})
        got = "\n".join(self.verify())
        self.assertIn("<withheld>/readme.md (blob ", got)
        self.assertIn("[member-name]", got)
        self.assertNotIn("barometer", got.lower())

    def test_an_exemption_does_not_reach_a_name(self):
        """A gate_exempt or protect row shields a body, never a path."""
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["generalize"]["protect"] = [{"phrase": "barometer-notes"}]
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        self.commit({"README.md": CLEAN, "barometer-notes/readme.md": CLEAN})
        self.assertIn("[member-name]", "\n".join(self.verify()))


class ExportGatesPathNamesTest(PublicCutGateTest):
    """The same name gate runs over every OUTPUT path of the cut, after the renames."""

    set_man = TheFirstAuditsLeaksTest.set_man

    def test_a_private_account_in_a_published_filename_is_REFUSED(self):
        self.set_man(private_accounts=["otter" + "-" + "lantern"])
        self.plant(CLEAN, name="0002-%s.md" % ("otter" + "-" + "lantern"))
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("adr/<withheld>:0", err)
        self.assertIn("[private-account]", err)
        self.assertNotIn("otter", err.lower())

    def test_a_member_name_in_a_published_directory_is_REFUSED(self):
        (self.repo / "adr" / "barometer").mkdir()
        (self.repo / "adr" / "barometer" / "0003-x.md").write_text(CLEAN, encoding="utf-8")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("adr/<withheld>/0003-x.md:0", err)
        self.assertNotIn("barometer", err.lower())


class GateExemptIsSpanScopedTest(PublicCutGateTest):
    """Round-2 review, item 5: a gate_exempt row was a file-wide class waiver. It now waives
    only a hit its `spans` contain; a second value of the class still fires."""

    OTHER = "100.100" + ".1.2"  # a second overlay-range address, assembled at run time

    def set_exempt(self, rows):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["gate_exempt"] = rows
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    ROW = {"path": "adr/0002-planted.md", "classes": ["overlay-ip"],
           "spans": [r"100\.64\.9\.9"], "why": "the fixture value"}

    def test_the_span_waives_its_own_value(self):
        self.plant("# ADR-0002\n\nfixture %s here\n" % TS_IP)
        self.set_exempt([self.ROW])
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_second_value_on_the_SAME_line_still_fires(self):
        rel = self.plant("# ADR-0002\n\nfixture %s and %s\n" % (TS_IP, self.OTHER))
        self.set_exempt([self.ROW])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:3" % rel, err)
        self.assertIn("overlay-ip", err)

    def test_a_second_value_ELSEWHERE_in_the_file_still_fires(self):
        rel = self.plant("# ADR-0002\n\nfixture %s\nleak %s\n" % (TS_IP, self.OTHER))
        self.set_exempt([self.ROW])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:4" % rel, err)
        self.assertNotIn("%s:3" % rel, err)

    def test_a_row_without_spans_is_REFUSED_not_read_as_a_file_waiver(self):
        self.plant("# ADR-0002\n\nfixture %s\n" % TS_IP)
        row = dict(self.ROW)
        del row["spans"]
        self.set_exempt([row])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("gate-exempt-malformed", err)
        self.assertIn("overlay-ip", err, "a malformed row waives nothing")

    def test_a_span_that_shields_nothing_is_REFUSED(self):
        self.plant("# ADR-0002\n\nfixture %s\n" % TS_IP)
        row = dict(self.ROW, spans=[r"100\.64\.9\.9", r"no-such-value"])
        self.set_exempt([row])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("gate-exempt-stale", err)

    def test_a_row_for_a_file_the_cut_does_not_publish_is_REFUSED(self):
        self.set_exempt([dict(self.ROW, path="adr/gone.md")])
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("gate-exempt-stale", err)

    def test_the_real_manifest_has_no_file_wide_row(self):
        man = public_cut.load_manifest(ROOT / "curate" / "public_cut_manifest.json")
        for row in man.get("gate_exempt", []):
            with self.subTest(path=row.get("path")):
                self.assertTrue(row.get("spans"), "a row without spans is a file-wide waiver")


class WithheldLinksTest(PublicCutGateTest):
    """A link into a file the cut withholds ships as its text plus ` (withheld)`, unlinked;
    any relative link left pointing outside the cut is a `dangling-link` finding."""

    def out_text(self, name):
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        return (out_dir / "adr" / name).read_text(encoding="utf-8")

    def test_a_link_into_an_excluded_tree_is_unlinked(self):
        (self.repo / "sessions" / "j.md").write_text("x\n", encoding="utf-8")
        self.plant("# ADR-0002\n\nSee [the journal](../sessions/j.md#top) and "
                   "[ADR-0001](0001-clean.md).\n")
        got = self.out_text("0002-planted.md")
        self.assertIn("See the journal (withheld) and [ADR-0001](0001-clean.md).", got)

    def test_an_excluded_file_inside_an_included_tree_is_unlinked(self):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["exclude"].append({"path": "adr/0040-gone.md", "ships": None, "why": "withheld"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        (self.repo / "adr" / "0040-gone.md").write_text(CLEAN, encoding="utf-8")
        self.plant("# ADR-0002\n\nSupersedes [ADR-0040](./0040-gone.md \"t\").\n")
        got = self.out_text("0002-planted.md")
        self.assertIn("Supersedes ADR-0040 (withheld).", got)
        self.assertNotIn("0040-gone", got)

    def test_code_and_urls_and_directories_are_left_alone(self):
        body = ("# ADR-0002\n\n`[x](nope.md)` and [site](https://example.com/a.md) and "
                "[dir](../adr/) and [top](#h)\n\n```\n[y](nope.md)\n```\n")
        self.plant(body)
        self.assertEqual(self.out_text("0002-planted.md"), body)

    def test_a_link_out_of_the_tree_is_a_dangling_link_finding(self):
        rel = self.plant("# ADR-0002\n\nline\nSee [x](../../outside.md).\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("[dangling-link]", err)
        self.assertIn("%s:4" % rel, err)

    def test_a_reference_definition_to_a_withheld_path_is_a_dangling_link_finding(self):
        rel = self.plant("# ADR-0002\n\nSee [x][1].\n\n[1]: ../sessions/journal.md\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:5" % rel, err)
        self.assertIn("dangling-link", err)

    def test_a_kit_template_resolves_its_links_from_the_slot_it_fills(self):
        """The profile template is written to be read at users/operator/profile.md."""
        (self.repo / "bootstrap-kit").mkdir()
        (self.repo / "bootstrap-kit" / "user-profile-template.md").write_text(
            "# Profile\n\nPer [ADR-0001](../../adr/0001-clean.md).\n", encoding="utf-8")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["include"].append({"path": "bootstrap-kit", "why": "kit"})
        man["exclude"].append({"path": "users", "ships": "blank-operator-profile",
                               "ships_from": "bootstrap-kit/user-profile-template.md",
                               "ships_to": "users/operator/profile.md", "why": "personal"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_the_clean_fixture_has_no_dangling_link(self):
        self.plant("# ADR-0002\n\nSee [ADR-0001](0001-clean.md).\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 0, err)


class CommunityEditionTest(VerifyPublishTest):
    """WI-0450: a second publication identity, built as an EDITION of the same cut.

    Every name here is invented: the default identity is Quill-Harbor and refuses
    otter-lantern; the community edition is Otter-Lantern and must refuse Quill-Harbor. Neither copy may name or link the other, and they never share a
    commit address. The same scratch clone passes one edition and is refused by the other,
    so each refusal proves the planted link and not a gate that refuses everything."""

    PUB = TheUnlinkedIdentityTest.PUB
    COMM_PUB = "Otter-Lantern/principles-of-good-architects"
    COMM_EMAIL = "2+Otter-Lantern" + "@" + "users.noreply.github.com"
    COMM = {"repo": COMM_PUB, "email": COMM_EMAIL, "shield": [COMM_PUB, COMM_EMAIL]}

    def setUp(self):
        super().setUp()
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["generalize"]["substitutions"] = [
            {"find": "otter-lantern/internal", "replace": self.PUB, "kind": "operator-path"}]
        man["editions"] = {"community": {
            "public_identity": self.COMM,
            "private_accounts": ["quillharbor"],
            "extra_names": ["Quill"],
            "substring_names": ["quill"],
        }}
        self.manifest.write_text(json.dumps(man), encoding="utf-8")

    def edition(self, name="community"):
        return public_cut.apply_edition(public_cut.load_manifest(self.manifest), name)

    def verify_as(self, name):
        return public_cut.verify_publish(self.clone, self.edition(name), self.repo)

    def test_the_showcase_is_the_manifest_as_written(self):
        man = public_cut.load_manifest(self.manifest)
        self.assertIs(man, public_cut.apply_edition(man, None))
        self.assertIs(man, public_cut.apply_edition(man, "showcase"))

    def test_an_unknown_edition_is_REFUSED(self):
        with self.assertRaisesRegex(public_cut.ManifestError, "no edition"):
            self.edition("nightly")

    def test_an_edition_without_private_accounts_is_REFUSED(self):
        man = public_cut.load_manifest(self.manifest)
        man["editions"]["community"]["private_accounts"] = []
        with self.assertRaisesRegex(public_cut.ManifestError, "private_accounts"):
            public_cut.apply_edition(man, "community")

    def test_the_edition_swaps_the_identity_and_the_refused_accounts(self):
        man = self.edition()
        ig = man["identity_gate"]
        self.assertEqual(self.COMM, ig["public_identity"])
        self.assertEqual(["quillharbor"], ig["private_accounts"])
        self.assertIn("Quill", ig["extra_names"])

    def test_a_row_writing_the_showcase_repo_writes_the_editions_repo(self):
        """No table row can put a refused identity into this copy."""
        out, _hits = public_cut.generalize("see otter-lantern/internal\n", self.edition())
        self.assertIn(self.COMM_PUB, out)
        self.assertNotIn("Quill", out)

    def test_the_shield_never_covers_the_prefix_of_a_longer_repo_name(self):
        """The first community export kept the internal repo's URL: the edition's own
        address is a prefix of it, and the shield held that prefix away from the row."""
        man = public_cut.load_manifest(self.manifest)
        man["generalize"]["substitutions"] = [
            {"find": self.COMM_PUB + "-internal", "replace": self.PUB, "kind": "operator-path"}]
        man = public_cut.apply_edition(man, "community")
        out, _hits = public_cut.generalize("x %s-internal.git\n" % self.COMM_PUB, man)
        self.assertEqual("x %s.git\n" % self.COMM_PUB, out)

    def test_the_name_inside_an_identifier_is_REFUSED(self):
        """Audit round 1 of the community cut: `surfaces_to_<name>` has no word boundary, so
        only the edition's substring list sees the name inside it."""
        found = public_cut.gate_text("def test_surfaces_to_quill(self):\n", "t.py",
                                     self.edition(), [])[0]
        self.assertIn("member-name", {f.cls for f in found})

    def test_the_showcase_repo_named_in_the_body_is_REFUSED(self):
        man = self.edition()
        found = public_cut.gate_text("see %s\n" % self.PUB, "a.md", man, [])[0]
        self.assertIn("private-account", {f.cls for f in found})

    def test_one_commit_under_the_community_identity_passes_it_and_not_the_showcase(self):
        self.NAME, self.EMAIL = "Otter-Lantern", self.COMM_EMAIL
        self.commit({"README.md": CLEAN})
        self.assertEqual([], self.verify_as("community"))
        self.assertTrue(self.verify_as("showcase"), "the showcase must refuse this clone")

    def test_a_showcase_commit_is_REFUSED_by_the_community_edition(self):
        """The two copies never share a commit address or name."""
        self.commit({"README.md": CLEAN})
        got = "\n".join(self.verify_as("community"))
        self.assertIn("not the public noreply address", got)
        self.assertIn("not the public account's name", got)

    def test_the_shipped_manifest_carries_no_editions_and_only_its_own_identity(self):
        text, _n = public_cut.manifest_public(self.manifest.read_text(encoding="utf-8"),
                                              {"public_identity": self.COMM})
        data = json.loads(text)
        self.assertNotIn("editions", data)
        self.assertEqual(self.COMM, data["identity_gate"]["public_identity"])
        self.assertNotIn("Quill", text)


class TheRealEditionsAreCrossLockedTest(unittest.TestCase):
    """Over the real manifest: each edition refuses the other's account on letters and
    digits. The public copy ships no editions, so there this is skipped."""

    def test_each_account_refuses_the_other(self):
        man = public_cut.load_manifest()
        if not man.get("editions"):
            self.skipTest("the public cut ships no editions block")
        show = man["identity_gate"]
        for name in man["editions"]:
            ed = public_cut.apply_edition(man, name)["identity_gate"]
            with self.subTest(edition=name):
                mine = public_cut.normalized(ed["public_identity"]["repo"].split("/")[0])
                theirs = public_cut.normalized(show["public_identity"]["repo"].split("/")[0])
                self.assertNotEqual(ed["public_identity"]["email"],
                                    show["public_identity"]["email"])
                self.assertTrue(any(public_cut.normalized(a) in theirs
                                    for a in ed["private_accounts"]),
                                "the edition must refuse the default identity")
                self.assertTrue(any(public_cut.normalized(a) in mine
                                    for a in show["private_accounts"]),
                                "the showcase must refuse the edition's account")


class DocScriptMustBeExecutableTest(PublicCutGateTest):
    """External review 2026-09-30: the drill doc told a reader to run
    `drills/basic-acceptance.sh`, the script shipped 0644, and the command exited 126.
    `doc-script-not-executable` refuses a cut where a published doc tells a reader to run a
    published `*.sh` directly and that script is not executable in the cut."""

    CLS = public_cut.DOC_SCRIPT_CLASS

    def script(self, mode, rel="adr/run.sh"):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        p.chmod(mode)
        return rel

    def test_a_doc_naming_a_644_script_is_REFUSED_with_file_and_line(self):
        self.script(0o644)
        doc = self.plant("# ADR-0002\n\nFrom the root, run `adr/run.sh`.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn(self.CLS, err)
        self.assertIn("%s:3" % doc, err)

    def test_a_fenced_dot_slash_invocation_is_REFUSED(self):
        self.script(0o644)
        doc = self.plant("# ADR-0002\n\n```sh\n$ ./adr/run.sh --fast\n```\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("%s:4" % doc, err)

    def test_a_link_relative_to_the_doc_is_resolved_there(self):
        self.script(0o644)
        self.plant("# ADR-0002\n\nThe driver: [`run.sh`](run.sh).\n")
        cut = public_cut.build(self.repo, public_cut.load_manifest(self.manifest))[0]
        self.assertIn(self.CLS, [f.cls for f in cut.findings])

    def test_the_same_doc_naming_a_755_script_passes(self):
        self.script(0o755)
        self.plant("# ADR-0002\n\nFrom the root, run `adr/run.sh`.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_bash_in_front_of_a_644_script_passes(self):
        self.script(0o644)
        self.plant("# ADR-0002\n\nRun `bash adr/run.sh`, or `sh adr/run.sh`.\n\n"
                   "```\nbash adr/run.sh\n```\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_script_the_cut_does_not_publish_is_not_its_to_check(self):
        """A command for the reader's own project names no file in this cut."""
        self.plant("# ADR-0002\n\nIn your project, run `./setup.sh`.\n")
        code, _out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_a_sampled_script_keeps_its_bit_in_the_written_cut(self):
        """drills/ ships only through sample rows, so the bit must follow a sample too --
        the gap that let the drill driver ship 0644 even with its source executable."""
        src = self.script(0o755, "sessions/drive.sh")
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["sample"] = [{"from": src, "to": "drills/drive.sh", "why": "the driver"},
                         {"from": "sessions/drive.md", "to": "drills/drive.md",
                          "why": "its record"}]
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        (self.repo / "sessions" / "drive.md").write_text(
            "# Drive\n\nRun `drills/drive.sh`.\n", encoding="utf-8")
        out_dir = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out_dir), 0)
        self.assertTrue(os.access(out_dir / "drills" / "drive.sh", os.X_OK))
        # And the check reads the same set: the source at 0644 is refused.
        (self.repo / src).chmod(0o644)
        code, _out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("drills/drive.md:3", err)


class VerifyPublishChecksScriptModesTest(VerifyPublishTest):
    """The same rule over the COMMITTED tree: the mode git stores is what a clone gets."""

    def commit_with_script(self, mode):
        script = self.clone / "drills" / "drive.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        script.chmod(mode)
        self.git("config", "core.fileMode", "true")
        self.commit({"README.md": "# Readme\n\nRun `drills/drive.sh`.\n"})

    def test_a_committed_644_script_the_docs_run_is_REFUSED(self):
        self.commit_with_script(0o644)
        got = "\n".join(self.verify())
        self.assertIn("README.md:3 [%s]" % public_cut.DOC_SCRIPT_CLASS, got)

    def test_a_committed_755_script_passes(self):
        self.commit_with_script(0o755)
        self.assertEqual([], self.verify())


class AddTableTest(PublicCutGateTest):
    """`add`: a file that exists ONLY in the public cuts (`.github/workflows/ci.yml`; the
    internal origin is private and must run no Actions). Its source sits under
    public-overlay/, and it is gated like any file."""

    CI = "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
    PATH = ".github/workflows/ci.yml"
    FROM = "public-overlay/.github/workflows/ci.yml"

    def set_add(self, rows, write=True, text=None):
        man = json.loads(self.manifest.read_text(encoding="utf-8"))
        man["add"] = rows
        man["exclude"].append({"path": "public-overlay", "ships": None, "why": "overlays"})
        self.manifest.write_text(json.dumps(man), encoding="utf-8")
        if write:
            p = self.repo / self.FROM
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text if text is not None else self.CI, encoding="utf-8")

    def row(self, **kw):
        r = {"path": self.PATH, "from": self.FROM, "why": "the public copies' CI"}
        r.update(kw)
        return r

    def build(self):
        return public_cut.build(self.repo, public_cut.load_manifest(self.manifest))[0]

    def test_the_added_file_ships_at_its_dotted_path_and_the_receipt_lists_it(self):
        self.set_add([self.row()])
        out = pathlib.Path(self.tmp.name) / "out"
        self.assertEqual(self.run_out(out), 0)
        self.assertEqual((out / self.PATH).read_text(encoding="utf-8"), self.CI)
        self.assertFalse((out / "public-overlay").exists(), "the overlay tree was copied")
        receipt = (out / public_cut.RECEIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("\n- `%s`\n" % self.PATH, receipt)
        self.assertIn("- `%s` — the public copies' CI" % self.PATH, receipt)

    def test_the_added_file_is_gated_like_any_file(self):
        self.set_add([self.row()], text=self.CI + "# the barometer job\n")
        code, _o, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("member-name", err)
        self.assertIn("%s:6" % self.PATH, err)

    def test_a_missing_from_is_REFUSED(self):
        self.set_add([self.row()], write=False)
        (self.repo / "public-overlay").mkdir()  # the internal shape: the tree, not the file
        cut = self.build()
        self.assertIn("add-missing", [f.cls for f in cut.findings])
        self.assertNotIn(self.PATH, [r for r, _t in cut.files])

    def test_a_path_the_cut_already_publishes_is_REFUSED(self):
        self.set_add([self.row(path="adr/0001-clean.md")])
        cut = self.build()
        self.assertIn("add-collides", [f.cls for f in cut.findings])
        self.assertEqual(CLEAN, dict(cut.files)["adr/0001-clean.md"],
                         "the add must not have replaced the walked file")

    def test_an_internal_copy_at_the_path_is_REFUSED(self):
        self.set_add([self.row()])
        p = self.repo / self.PATH
        p.parent.mkdir(parents=True)
        p.write_text(self.CI, encoding="utf-8")
        self.assertIn("add-shadowed", [f.cls for f in self.build().findings])

    def test_a_from_outside_the_overlay_tree_is_REFUSED(self):
        self.set_add([self.row(**{"from": "sessions/journal.md"})])
        self.assertIn("add-malformed", [f.cls for f in self.build().findings])

    def test_a_row_without_a_why_is_REFUSED(self):
        self.set_add([self.row(why=" ")])
        self.assertIn("add-malformed", [f.cls for f in self.build().findings])

    def test_in_the_exported_cut_the_file_is_read_at_its_path(self):
        """The cut has no public-overlay/; its own check reads the file where it ships."""
        self.set_add([self.row()], write=False)
        p = self.repo / self.PATH
        p.parent.mkdir(parents=True)
        p.write_text(self.CI, encoding="utf-8")
        cut = self.build()
        self.assertEqual([], [f.cls for f in cut.findings])
        self.assertEqual(self.CI, dict(cut.files)[self.PATH])


if __name__ == "__main__":
    unittest.main()
