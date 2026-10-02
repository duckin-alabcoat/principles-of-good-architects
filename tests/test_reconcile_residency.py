"""WI-0253 — reconcile must tell 'deliberately elsewhere' from 'unlocated'.

THE DEFECT, measured session ~190 and reproduced here. devbox deliberately carries
only a subset of the fleet: the Runner-side systems are absent on purpose. `curate/deliver.py` learned that in WI-0205
by reading a DECLARED `resides` label out of `mailboxes.json`. `curate/reconcile.py`
keys off a different roster (`portfolio.md`) and was deliberately left alone, so the two
tools disagreed inside a single startup banner — "Delivery audit: 1 recipient(s)
unreachable" (correct) sitting directly above "Reconcile: 8 unlocated", three of which
were exactly the members the audit had just stopped flagging.

WHAT THESE TESTS PIN, in the order the defect can come back:

  1. THE DECLARATION IS READ, AND ONLY FROM A COLUMN THAT SAYS SO. `Resides` is located
     by its header NAME, never a fixed index — the three other readers of this file all
     anchor on the adjacent sid/aid pair, so a future column insertion would silently
     shift an index-based read onto the Status cell. That failure direction is the bad
     one: an arbitrary Status string is "not this machine", so it would EXCUSE members
     rather than fail loudly.

  2. UNDECLARED IS STILL A GAP. An empty cell, an em-dash, or no column at all must
     leave the member reporting as UNLOCATED. An exemption that defaults to pass does
     not fail to detect a gap, it certifies one
     ([`ship-the-detector-with-the-capability`]).

  3. THE EXEMPTION IS WITHHELD WHEN WE CANNOT NAME THIS MACHINE. Not knowing where you
     are standing is a reason to withhold the verdict, never to grant it.

  4. THE TWO TOOLS SHARE ONE IMPLEMENTATION. A rule whose entire value is that it says
     the same thing in two surfaces of one banner cannot survive as two copies (P16).

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("reconcile", ROOT / "curate" / "reconcile.py")
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)

import common  # noqa: E402  (curate/ is on the path from above)


HEADER = ("| System | Agent | Architect | System ID | Architect ID | Resides | Status |\n"
          "|---|---|---|---|---|---|---|\n")


def row(sid, resides="—", status="Active"):
    return (f"| {sid} | (none) | {sid} Architect | `{sid}` | `{sid}-arch` "
            f"| {resides} | {status} |\n")


class RosterResidencyTest(unittest.TestCase):
    """Parsing the `Resides` column out of portfolio.md."""

    def _residency(self, text):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "portfolio.md").write_text(text, encoding="utf-8")
            with mock.patch.object(reconcile, "FED_ROOT", root):
                return reconcile.roster_residency()

    def test_a_declared_label_is_read(self):
        got = self._residency(HEADER + row("orbit", "Runner") + row("lumen", "—"))
        self.assertEqual(got, {"orbit": "Runner"})

    def test_placeholders_mean_undeclared_not_a_machine_named_dash(self):
        """`—`, `-`, `n/a` and `none` are how a human writes "nothing here" in a table.
        Read literally they are machine labels that differ from every real one, so each
        would hand its member a free exemption."""
        for placeholder in ("—", "-", "–", "n/a", "N/A", "none", "None", ""):
            with self.subTest(placeholder=placeholder):
                self.assertEqual(self._residency(HEADER + row("orbit", placeholder)), {})

    def test_a_portfolio_with_no_resides_column_declares_nothing(self):
        """The pre-WI-0253 file shape. It must yield an empty map — every member then
        reports exactly as it did before, which is the safe direction to degrade in.
        A parser that guessed a column here would excuse the whole roster at once."""
        text = ("| System | Agent | Architect | System ID | Architect ID | Status |\n"
                "|---|---|---|---|---|---|\n"
                "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` | Active |\n")
        self.assertEqual(self._residency(text), {})

    def test_the_column_is_found_by_name_not_by_index(self):
        """The load-bearing one. Every other reader of portfolio.md anchors on the
        sid/aid pair and so survives a new column; an index-based read here would not,
        and its failure mode is to read a Status cell as a machine label — which
        `declared_elsewhere` compares against DevBox, finds different, and grants."""
        text = ("| System | Agent | Architect | System ID | Architect ID | Status "
                "| Resides |\n"
                "|---|---|---|---|---|---|---|\n"
                "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` "
                "| Active | Runner |\n")
        self.assertEqual(self._residency(text), {"orbit": "Runner"})

    def test_a_status_cell_is_never_mistaken_for_a_machine_label(self):
        """The same defect stated as its symptom: with the column moved, the member
        must not come back declared as living on a machine called 'Active'."""
        text = ("| System | Agent | Architect | System ID | Architect ID | Status "
                "| Resides |\n"
                "|---|---|---|---|---|---|---|\n"
                "| orbit | (none) | Orbit Architect | `orbit` | `orbit-arch` "
                "| Active | — |\n")
        self.assertEqual(self._residency(text), {})

    def test_the_disposition_tables_cannot_leak_in(self):
        """portfolio.md carries a second table (folded directories) whose rows start
        with `|` too. Only rows with the ADR-0006 sid/aid pair are roster rows."""
        text = (HEADER + row("orbit", "Runner")
                + "\n## Off-roster dispositions\n\n"
                + "| Directory | Disposition | Owner | Decided | Basis |\n"
                + "|---|---|---|---|---|\n"
                + "| `example-dir/` | Folded | example-owner | 2020-01-01 | ADR-0005 |\n")
        self.assertEqual(self._residency(text), {"orbit": "Runner"})

    def test_an_unreadable_portfolio_declares_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(reconcile, "FED_ROOT", pathlib.Path(d)):
                self.assertEqual(reconcile.roster_residency(), {})


class ClassifyAbsentTest(unittest.TestCase):
    """The three-way split itself — the whole of the WI-0253 fix in one function."""

    ROSTER = {"orbit", "beacon", "sprocket", "lumen"}
    FOUND = {"lumen"}
    RESIDES = {"orbit": "Runner", "beacon": "Runner"}

    def _split(self, machine="DevBox", residency=None, roster=None, found=None):
        return reconcile.classify_absent(
            self.ROSTER if roster is None else roster,
            self.FOUND if found is None else found,
            residency=self.RESIDES if residency is None else residency,
            machine=machine)

    def test_declared_elsewhere_members_leave_the_unlocated_list(self):
        elsewhere, unlocated = self._split()
        self.assertEqual(elsewhere, {"orbit": "Runner", "beacon": "Runner"})
        self.assertEqual(unlocated, ["sprocket"])

    def test_an_undeclared_absent_member_is_still_a_gap(self):
        """The half-finished-rename shape: the repo IS on this disk, under an old name,
        so the roster id finds nothing. That is a real finding, and the residency
        column must not launder it into 'elsewhere by design'."""
        _, unlocated = self._split()
        self.assertIn("sprocket", unlocated)

    def test_a_located_member_is_never_exempted_even_when_declared_elsewhere(self):
        """The column is consulted only for members that were NOT located, so it can
        never hide a member that is present and broken."""
        elsewhere, unlocated = self._split(
            found={"lumen", "orbit"}, residency={"orbit": "Runner"})
        self.assertEqual(elsewhere, {})
        self.assertNotIn("orbit", unlocated)

    def test_a_member_declaring_THIS_machine_and_missing_is_a_gap(self):
        """Claiming to live here and not being here is a defect, not a design."""
        elsewhere, unlocated = self._split(residency={"orbit": "DevBox"})
        self.assertEqual(elsewhere, {})
        self.assertIn("orbit", unlocated)

    def test_an_unnamed_machine_withholds_every_exemption(self):
        """`this_machine()` degrades to "" when scutil cannot answer. An exemption that
        defaults to pass certifies a gap rather than detecting one — so the degraded
        run must report MORE, never less."""
        elsewhere, unlocated = self._split(machine="")
        self.assertEqual(elsewhere, {})
        self.assertEqual(unlocated, ["beacon", "orbit", "sprocket"])

    def test_an_empty_roster_yields_nothing_rather_than_everything(self):
        elsewhere, unlocated = self._split(roster=set())
        self.assertEqual((elsewhere, unlocated), ({}, []))


class StatusLineThreeWayTest(unittest.TestCase):
    """The SessionStart banner line — the surface the item was filed against.

    The two halves of this module must agree (WI-0175, where the hook printed 7
    unlocated and the command it told a human to run printed 15); both now route
    through `classify_absent`, and these pin what the banner says out of it.
    """

    def _line(self, machine="DevBox"):
        found = {"lumen": ({"version": "1.0"}, pathlib.Path("/x/lumen/STATUS.md"))}
        with mock.patch.object(reconcile, "read_roots_config", lambda warn=True: ["/r"]), \
                mock.patch.object(reconcile, "read_repo_paths", lambda: {}), \
                mock.patch.object(reconcile, "compute_drift",
                                  lambda roots, rp=None: (
                                      found,
                                      {"lumen", "orbit", "beacon", "sprocket"},
                                      {}, [])), \
                mock.patch.object(reconcile, "live_roledoc_version", lambda d: "1.0"), \
                mock.patch.object(reconcile, "roster_residency",
                                  lambda: {"orbit": "Runner", "beacon": "Runner"}), \
                mock.patch.object(reconcile, "this_machine", lambda: machine):
            return reconcile.status_line()

    def test_the_banner_counts_the_two_states_separately(self):
        line = self._line()
        self.assertIn("1 unlocated", line)
        self.assertIn("2 elsewhere by design", line)

    def test_the_by_design_count_is_stated_rather_than_silently_dropped(self):
        """Dropping the middle state entirely would make a partly-unsurveyed roster read
        as fully located — the opposite conflation, and just as wrong."""
        self.assertIn("elsewhere by design", self._line())

    def test_an_unnamed_machine_reports_them_all_as_unlocated(self):
        line = self._line(machine="")
        self.assertIn("3 unlocated", line)
        self.assertNotIn("elsewhere by design", line)


class FullReportThreeWayTest(unittest.TestCase):
    """The human report. Its old advice for an elsewhere member — 'Add the repo's path
    to repo-paths.local' — could only be followed by inventing a path to a repo that is
    not on this disk. Wrong advice is worse than none: it is actionable."""

    def _report(self, machine="DevBox"):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["reconcile.py", "/r"]), \
                mock.patch.object(reconcile, "read_repo_paths", lambda: {}), \
                mock.patch.object(reconcile, "self_entry", lambda: ({}, [])), \
                mock.patch.object(reconcile, "find_status_files", lambda roots: ({}, [])), \
                mock.patch.object(reconcile, "resolve_mapped", lambda rp: ({}, {})), \
                mock.patch.object(reconcile, "roster_ids",
                                  lambda: {"orbit", "sprocket"}), \
                mock.patch.object(reconcile, "roster_residency",
                                  lambda: {"orbit": "Runner"}), \
                mock.patch.object(reconcile, "this_machine", lambda: machine), \
                mock.patch.object(sys, "stdout", buf):
            reconcile.main()
        return buf.getvalue()

    def test_an_elsewhere_member_gets_its_own_section_and_names_the_machine(self):
        out = self._report()
        self.assertIn("ELSEWHERE BY DESIGN", out)
        self.assertIn("lives on Runner", out)

    def test_an_elsewhere_member_is_not_told_to_map_a_path_that_does_not_exist(self):
        out = self._report()
        elsewhere_block = out.split("ELSEWHERE BY DESIGN", 1)[1].split("UNLOCATED", 1)[0]
        self.assertIn("no path to add", elsewhere_block)

    def test_the_genuine_gap_still_gets_the_unlocated_section(self):
        out = self._report()
        self.assertIn("UNLOCATED", out)
        self.assertIn("- sprocket", out)

    def test_a_withheld_exemption_says_so_instead_of_a_silently_longer_list(self):
        """Otherwise the degraded run is indistinguishable from a run where nobody had
        declared residency yet — two very different facts printing identically."""
        out = self._report(machine="")
        self.assertNotIn("ELSEWHERE BY DESIGN", out)
        self.assertIn("could not name itself", out)


class OneImplementationTest(unittest.TestCase):
    """P16 — the residency rule exists once.

    Both tools print residency verdicts into the SAME startup banner, so a second copy
    of the rule could only ever drift into contradicting the first one line above it.
    That is the entire defect WI-0253 was filed about, so a duplicate implementation is
    the recurrence, not merely untidy.
    """

    def test_reconcile_and_deliver_share_the_same_function_object(self):
        _s = importlib.util.spec_from_file_location(
            "deliver_shared_check", ROOT / "curate" / "deliver.py")
        deliver = importlib.util.module_from_spec(_s)
        _s.loader.exec_module(deliver)
        self.assertIs(reconcile.declared_elsewhere, common.declared_elsewhere)
        self.assertIs(deliver.declared_elsewhere, common.declared_elsewhere)
        self.assertIs(reconcile.this_machine, common.this_machine)
        self.assertIs(deliver.this_machine, common.this_machine)

    def test_neither_module_defines_its_own_copy(self):
        for rel in ("curate/reconcile.py", "curate/deliver.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("def declared_elsewhere(", text, rel)
            self.assertNotIn("def this_machine(", text, rel)


class LiveRosterTest(unittest.TestCase):
    """Not a fixture — the REAL portfolio.md, so a residency label that stops parsing
    when someone reformats the table is caught here rather than by a banner quietly
    going back to over-reporting."""

    @unittest.skipIf((ROOT / "PUBLIC-CUT-RECEIPT.md").is_file(),
                     "public cut: portfolio.md ships as the authored template, whose "
                     "rows declare no residency; the count is a fact about the real roster")
    def test_the_real_roster_declares_residency_for_the_runner_side_members(self):
        # Counted, not named: the three Runner-side members are the ones whose rows must
        # keep parsing, and naming them here would publish them (WI-0447).
        residency = reconcile.roster_residency()
        runner = sorted(s for s, m in residency.items() if m == "Runner")
        self.assertGreaterEqual(len(runner), 3, runner)

    def test_every_declared_label_is_a_known_machine(self):
        """A typo'd label (`runner`, `Runner Host`) is indistinguishable from a real
        elsewhere declaration to `declared_elsewhere` — it differs from this machine, so
        it grants the exemption. Pin the vocabulary to `session.config.json`'s
        `machine_map`, which is where every session stamp's label comes from."""
        import json
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        known = set((cfg.get("machine_map") or {}).values())
        self.assertTrue(known, "session.config.json declares no machine_map")
        for sid, label in reconcile.roster_residency().items():
            self.assertIn(label, known, f"{sid} declares an unknown machine '{label}'")

    def test_the_federation_itself_stays_undeclared(self):
        """It is on every machine, so any single label would be a lie in some direction
        — and a wrong one would excuse the hub's own disappearance."""
        self.assertNotIn("federation", reconcile.roster_residency())


if __name__ == "__main__":
    unittest.main()
