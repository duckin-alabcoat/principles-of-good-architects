"""Tests for curate/check_substrate_docs.py — the WI-0339 structural guard.

The load-bearing property is not "the check passes today". It passed the first time it
was ever run, on a tree that had just been corrected by hand, and a check that has never
been shown to fail is indistinguishable from a check that cannot
([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)).
So every assertion below corrupts exactly one thing in a synthetic tree and demands the
single matching finding.

Two of these are asserted against the LIVE repo rather than a fixture, because they are
claims about this repo and a fixture cannot make them true:

  * `TheLiveTreeIsCleanTest` — the surfaces WI-0339 named actually describe the substrate.
  * `TheCheckIsInTheGateTest` — the guard is wired where it will be run. `gen_indexes.py`
    shipped 2026-07-14 wired NOWHERE and the drift it kills was found by hand nine times
    over six weeks afterwards. A guard nothing invokes is a guard that does not exist.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import tempfile
import sys
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_substrate_docs", REPO / "curate" / "check_substrate_docs.py")
csd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csd)


PARTS = ["__init__.py", "config.py", "land.py"]
#: Deliberately non-contiguous: 0002 is a hole the fixture declares, so the
#: "declared holes do not fire" case is the DEFAULT and every other test inherits it.
ADRS = {1: "0001-first.md", 3: "0003-third.md", 4: "0004-fourth.md"}


def _adr(n, status="Accepted", date="2026-01-01", bullets=False):
    """An ADR header in either of the two shapes the real corpus uses."""
    lead = "- " if bullets else ""
    return (f"# ADR-{n:04d}: Title\n\n"
            f"{lead}**Status:** {status}\n"
            f"{lead}**Date:** {date}\n"
            f"{lead}**Deciders:** somebody\n")


def _overview(parts=PARTS):
    return "# Overview\n\n## Main codebase components\n\n" + "".join(
        f"- `sessionlib/{n}` — what it holds.\n" for n in parts)


def _role_doc(parts=PARTS):
    rows = "".join(f"| Part | `sessionlib/{n}` | What it holds. |\n" for n in parts)
    return ("# Role doc\n\n## 6. Voice\n\nprose\n\n"
            "## 7. Artifacts I maintain\n\n| Artifact | Location | Lifecycle |\n|---|---|---|\n"
            + rows + "\n## 8. Inputs\n\n| Part | `sessionlib/never.py` | outside the section |\n")


def _index(rows=None, unused=(2,), blank_after=None, heading=True):
    rows = ADRS if rows is None else rows
    out = ["# Architecture Decision Records", "", "## Index", "",
           "| # | Title | Status | Reality | Date |", "|---|---|---|---|---|"]
    for n in sorted(rows):
        out.append(f"| [{n:04d}]({rows[n]}) | Title | Accepted | Built | 2026-01-01 |")
        if blank_after is not None and n == blank_after:
            out.append("")
    out += [""]
    if heading:
        out += ["## Unused numbers", "", "| # | Drawn | Why |", "|---|---|---|"]
        out += [f"| {n:04d} | 2026-01-01 | burned |" for n in unused]
        out += [""]
    out += ["## Pending ADRs", "", "- something"]
    return "\n".join(out) + "\n"


class Fixture:
    """A synthetic repo the check can be pointed at. One knob corrupted per test."""

    def __init__(self, case, *, parts=PARTS, adrs=None, overview=None,
                 role_doc=None, index=None, make_sessionlib=True, adr_bodies=None):
        adrs = ADRS if adrs is None else adrs
        self.root = pathlib.Path(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        if make_sessionlib:
            (self.root / "sessionlib").mkdir()
            for n in parts:
                (self.root / "sessionlib" / n).write_text("# part\n", encoding="utf-8")
        (self.root / "adr").mkdir()
        for n, name in adrs.items():
            body = adr_bodies.get(n) if adr_bodies else None
            (self.root / "adr" / name).write_text(
                body if body is not None else _adr(n), encoding="utf-8")
        (self.root / csd.OVERVIEW).write_text(
            _overview(parts) if overview is None else overview, encoding="utf-8")
        (self.root / csd.ROLE_DOC).write_text(
            _role_doc(parts) if role_doc is None else role_doc, encoding="utf-8")
        (self.root / "adr" / "README.md").write_text(
            _index(adrs) if index is None else index, encoding="utf-8")
        case.addCleanup(setattr, csd, "ROOT", csd.ROOT)
        csd.ROOT = self.root

    def run(self):
        """The findings only — what most tests assert on."""
        return csd.run()[0]

    def unchecked(self):
        return csd.run()[1]


class CleanTreeTest(unittest.TestCase):
    def test_a_correct_tree_has_no_findings(self):
        # The control. Every corruption test below is only meaningful against this.
        self.assertEqual(Fixture(self).run(), [])

    def test_a_declared_hole_is_not_a_finding(self):
        # 0002 is absent from ADRS and declared under Unused numbers.
        self.assertEqual(Fixture(self).run(), [])


class HarnessPartsTest(unittest.TestCase):
    def test_a_part_missing_from_the_overview_fails(self):
        f = Fixture(self, overview=_overview(["__init__.py", "config.py"]))
        problems = f.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(csd.OVERVIEW, problems[0])
        self.assertIn("sessionlib/land.py", problems[0])

    def test_a_part_missing_from_section_7_fails(self):
        f = Fixture(self, role_doc=_role_doc(["__init__.py", "land.py"]))
        problems = f.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("section 7", problems[0])
        self.assertIn("sessionlib/config.py", problems[0])

    def test_a_NEW_part_nobody_documented_fails_both_surfaces(self):
        # The actual WI-0339 shape: the directory grows and the prose does not.
        f = Fixture(self)
        (f.root / "sessionlib" / "brandnew.py").write_text("# new\n", encoding="utf-8")
        problems = f.run()
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("sessionlib/brandnew.py" in p for p in problems), problems)

    def test_a_mention_outside_section_7_does_not_count(self):
        # never.py exists, the overview names it, and the role doc names it ONLY under
        # section 8. A check that searched the whole role doc would pass here.
        f = Fixture(self, parts=PARTS + ["never.py"], role_doc=_role_doc(PARTS))
        problems = f.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("section 7", problems[0])


class AdrIndexTest(unittest.TestCase):
    def test_an_adr_with_no_row_fails(self):
        f = Fixture(self, index=_index({1: "0001-first.md", 3: "0003-third.md"}))
        problems = f.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ADR-0004", problems[0])
        self.assertIn("no index row", problems[0])

    def test_a_row_with_no_file_fails(self):
        rows = {**ADRS, 9: "0009-ghost.md"}
        f = Fixture(self, index=_index(rows, unused=(2,)))
        problems = f.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ADR-0009", problems[0])
        self.assertIn("no such file", problems[0])

    def test_a_duplicate_row_fails(self):
        idx = _index().replace(
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-01-01 |",
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-01-01 |\n"
            "| [0003](0003-third.md) | Again | Accepted | Built | 2026-01-01 |")
        problems = Fixture(self, index=idx).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("has 2 rows", problems[0])

    def test_a_row_linking_the_wrong_file_fails(self):
        idx = _index().replace("(0003-third.md)", "(0003-renamed-in-the-index-only.md)")
        problems = Fixture(self, index=idx).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("0003-renamed-in-the-index-only.md", problems[0])

    def test_an_UNDECLARED_hole_fails(self):
        # The incident itself: 0002 exists as a hole and nothing says why.
        problems = Fixture(self, index=_index(unused=())).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("0002", problems[0])
        self.assertIn("hole in the number run", problems[0])

    # ── WI-0336: a hole can also be DRAWN AND IN FLIGHT, a third state ──────────────

    def _reserving(self, *numbers):
        """Point the check's allocator read at a fixed set of live reservations."""
        return mock.patch.object(csd, "reserved_adr_numbers",
                                 return_value=set(numbers))

    def test_a_hole_HELD_BY_A_LIVE_RESERVATION_is_not_a_problem(self):
        """Another lane drew 0002 and has not landed. It is neither burned nor missing, and
        failing the second lane's gate for it punishes a condition that lane neither caused
        nor can fix."""
        with self._reserving(2):
            problems = Fixture(self, index=_index(unused=())).run()
        self.assertEqual(problems, [])

    def test_the_exemption_is_NAMED_not_silent(self):
        """An exemption nobody can see is how a check starts certifying instead of
        checking."""
        buf = io.StringIO()
        with self._reserving(2), contextlib.redirect_stderr(buf):
            Fixture(self, index=_index(unused=())).run()
        self.assertIn("0002", buf.getvalue())
        self.assertIn("HOLDS it right now", buf.getvalue())

    def test_an_UNRESERVED_hole_still_fails(self):
        """The control. The exemption must be narrow enough that the original incident
        still reports."""
        with self._reserving(999):
            problems = Fixture(self, index=_index(unused=())).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("hole in the number run", problems[0])

    def test_an_UNREADABLE_allocator_grants_NO_exemption(self):
        """Fail-closed, and the direction matters: not being able to read the allocator is
        a reason to withhold the excuse, never to grant it. This is also the bug that hid
        the feature during development — the import raised, the blanket `except` returned
        an empty set, and the exemption silently never fired."""
        with mock.patch.object(csd, "reserved_adr_numbers", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                Fixture(self, index=_index(unused=())).run()

    def test_reserved_adr_numbers_returns_a_SET_OF_INTS_from_the_allocator(self):
        """The real reader, against the real entry point. `sessionlib.coord` does not import
        standalone (ADR-0118 made `session.py` the assembling entry point), and importing it
        directly returns an empty set through the fail-closed path — green, and useless."""
        got = csd.reserved_adr_numbers()
        self.assertIsInstance(got, set)
        self.assertTrue(all(isinstance(n, int) for n in got), got)

    def test_a_MALFORMED_reservation_key_excuses_nothing(self):
        fake = types.SimpleNamespace(_adr_alloc_holds=lambda: {"not-a-number": {}})
        with mock.patch.dict(sys.modules, {"session": fake}):
            self.assertEqual(csd.reserved_adr_numbers(), set())

    def test_a_hole_that_is_also_a_missing_row_is_reported_as_the_missing_row(self):
        # 0003 has a file and no row. That must NOT be excused by declaring 0003 unused —
        # the two conditions are different answers and the check must give the right one.
        idx = _index({1: "0001-first.md", 4: "0004-fourth.md"}, unused=(2, 3))
        problems = Fixture(self, index=idx).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ADR-0003", problems[0])
        self.assertIn("no index row", problems[0])

    def test_a_row_contradicting_the_records_status_fails(self):
        # The real one, 2026-09-11: two records (one was ADR-0112) each read "Proposed" in the
        # index while the record itself said Accepted.
        idx = _index().replace(
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-01-01 |",
            "| [0003](0003-third.md) | Title | Proposed | Built | 2026-01-01 |")
        problems = Fixture(self, index=idx).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("'Proposed'", problems[0])
        self.assertIn("'Accepted'", problems[0])

    def test_a_row_contradicting_the_records_date_fails(self):
        # ADR-0059's shape: the index had picked up an amendment's date.
        idx = _index().replace(
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-01-01 |",
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-02-09 |")
        problems = Fixture(self, index=idx).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("2026-02-09", problems[0])
        self.assertIn("2026-01-01", problems[0])

    def test_the_bullet_header_shape_is_read_too(self):
        """ADR-0107 and ADR-0118 write bullet headers. A parser that knew one shape
        would read None for those two and check nothing, confidently."""
        bodies = {3: _adr(3, status="Proposed", bullets=True)}
        problems = Fixture(self, adr_bodies=bodies).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ADR-0003", problems[0])

    def test_a_condensed_status_is_not_a_contradiction(self):
        # The index legitimately shortens. D7 compares the vocabulary word only.
        idx = _index().replace(
            "| [0003](0003-third.md) | Title | Accepted | Built | 2026-01-01 |",
            "| [0003](0003-third.md) | Title | Accepted (partially superseded by [ADR-0004]"
            "(0004-fourth.md)) | Built | 2026-01-01 |")
        bodies = {3: _adr(3, status="Accepted; partially superseded by ADR-0004")}
        self.assertEqual(Fixture(self, index=idx, adr_bodies=bodies).run(), [])

    def test_an_unrecognised_status_is_abstained_on_not_guessed(self):
        bodies = {3: _adr(3, status="something nobody has seen before")}
        self.assertEqual(Fixture(self, adr_bodies=bodies).run(), [])

    def test_a_blank_line_inside_the_table_fails(self):
        problems = Fixture(self, index=_index(blank_after=3)).run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("blank line", problems[0])
        self.assertIn("2 separate tables", problems[0])


class CannotCheckTest(unittest.TestCase):
    """Exit 2, never 0. A subject that has gone missing is the worst time to pass."""

    def test_no_sessionlib_directory_cannot_check(self):
        u = Fixture(self, make_sessionlib=False).unchecked()
        self.assertEqual(len(u), 1, u)
        self.assertIn("sessionlib/", u[0])

    def test_no_unused_numbers_heading_cannot_check(self):
        u = Fixture(self, index=_index(heading=False)).unchecked()
        self.assertEqual(len(u), 1, u)
        self.assertIn("D5", u[0])

    def test_a_renamed_section_7_cannot_check(self):
        rd = _role_doc().replace("## 7. Artifacts I maintain", "## 7. Things I look after")
        u = Fixture(self, role_doc=rd).unchecked()
        self.assertEqual(len(u), 1, u)
        self.assertIn("D2", u[0])

    def test_a_missing_overview_cannot_check(self):
        f = Fixture(self)
        (f.root / csd.OVERVIEW).unlink()
        u = f.unchecked()
        self.assertEqual(len(u), 1, u)
        self.assertIn("D1", u[0])

    def test_an_absent_subject_does_not_suppress_the_other_findings(self):
        """The regression this check was almost shipped with.

        Pointed at the real pre-fix tree, the first cut raised on the missing
        `## Unused numbers` heading and reported NOTHING about the nine harness parts
        the overview had never heard of, the ADR with no row, or the split table. One
        line, delivered with the same confidence as a complete answer — which is the
        defect this whole item is about, reproduced inside its own fix.
        """
        f = Fixture(self,
                    index=_index({1: "0001-first.md", 3: "0003-third.md"}, heading=False),
                    overview=_overview(["__init__.py"]))
        problems, unchecked = csd.run()
        self.assertEqual(len(unchecked), 1, unchecked)   # D5 could not run
        # ...and everything that COULD run, did: two parts missing, 0004 unrowed.
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("sessionlib/config.py", problems[0])
        self.assertIn("ADR-0004", problems[1])

    def test_could_not_check_outranks_a_finding(self):
        # Both present: the answer is 2, because part of it is missing.
        Fixture(self, index=_index(heading=False), overview=_overview(["__init__.py"]))
        self.assertEqual(self._main([]), 2)

    def test_cannot_check_exits_2_not_0(self):
        Fixture(self, make_sessionlib=False)
        self.assertEqual(self._main([]), 2)

    def test_status_never_exits_nonzero_even_when_it_cannot_check(self):
        # A SessionStart hook that fails is a bricked session start.
        Fixture(self, make_sessionlib=False)
        self.assertEqual(self._main(["--status"]), 0)

    def test_status_never_exits_nonzero_on_a_finding(self):
        Fixture(self, index=_index(unused=()))
        self.assertEqual(self._main(["--status"]), 0)

    @staticmethod
    def _main(argv):
        import contextlib
        import io
        import sys
        old = sys.argv
        sys.argv = ["check_substrate_docs.py"] + argv
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return csd.main()
        finally:
            sys.argv = old


class ExitCodeTest(unittest.TestCase):
    def test_a_clean_tree_exits_0_and_a_finding_exits_1(self):
        Fixture(self)
        self.assertEqual(CannotCheckTest._main([]), 0)

    def test_check_flag_is_the_same_answer_as_the_default(self):
        Fixture(self, index=_index(unused=()))
        self.assertEqual(CannotCheckTest._main([]), 1)
        self.assertEqual(CannotCheckTest._main(["--check"]), 1)


class TheLiveTreeIsCleanTest(unittest.TestCase):
    """Asserted against this repo, not a fixture — it is a claim about this repo."""

    def test_the_real_surfaces_describe_the_real_substrate(self):
        problems, unchecked = csd.run()
        self.assertEqual(problems, [])
        self.assertEqual(unchecked, [])

    def test_every_harness_part_on_disk_is_a_subject(self):
        # The subject list is derived from the directory, so a new part is covered
        # the moment it exists — no edit to the check.
        on_disk = sorted(p.name for p in (REPO / "sessionlib").glob("*.py"))
        self.assertEqual(csd.harness_parts(), on_disk)
        self.assertIn("land.py", on_disk)


class TheCheckIsInTheGateTest(unittest.TestCase):
    """Wired, or it does not exist. gen_indexes.py is the precedent that says so."""

    def test_the_configured_gate_runs_the_substrate_check(self):
        cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
        cmds = [" ".join(c) if isinstance(c, list) else c for c in (cfg.get("gate") or [])]
        self.assertTrue(any("check_substrate_docs.py" in c for c in cmds),
                        f"the land gate must run the substrate check; it runs: {cmds}")

    def test_it_adds_a_check_and_replaces_none(self):
        cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
        cmds = " | ".join(" ".join(c) if isinstance(c, list) else c for c in (cfg.get("gate") or []))
        for expected in ("wi-check", "run_suite.py", "distill.py --check",
                         "standardize.py --check", "gen_settings.py --check"):
            self.assertIn(expected, cmds)

    def test_it_is_on_a_session_start_hook_too(self):
        # The gate only runs at a land. Drift should be visible at the start of the
        # session that is about to reason from the stale prose, not after it.
        cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
        blob = json.dumps(cfg.get("settings_extras") or {})
        self.assertIn("check_substrate_docs.py", blob)


if __name__ == "__main__":
    unittest.main()
