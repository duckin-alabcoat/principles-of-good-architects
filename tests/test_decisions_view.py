"""`session.py decisions` — the decisions-for-review batch (WI-0288 R3 (5) / WI-0330).

R3 removed a gate (a dispatched lane no longer asks; it decides and records) and
replaced it with a record. WI-0274 named the risk in its own words: *"without it this
change removes a gate and replaces it with nothing"* — the record is only a replacement
if something collects it. These tests pin the collecting.

Three properties carry the weight, and all three are about telling apart answers that
look alike:

  - a heading is a structural boundary, so entries under the DECISION heading are listed
    and entries under any other heading are not — the distractor case;
  - a section that is present and EMPTY (zero decisions recorded) and a section that is
    ABSENT (no such heading at all) are different answers and stay different, which is
    the contract `_ledger_section` has carried since WI-0159;
  - `--json` and the human rendering are the same rows, so the reviewer and any later
    tooling are reading one view rather than two that can drift.

The fixture journals are built here rather than pointed at the live store on purpose:
`tests/test_poga_fleet.py` records the ruling that a suite running inside the merge gate
must not assert on production data.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


def _journal(sid: str, started: str, body: str, work_items: str = "WI-0001") -> str:
    return (
        "---\n"
        f"session-id: {sid}\n"
        "ordinal: 42\n"
        "title: a fixture session\n"
        "machine: DevBox\n"
        "runtime: claude-code\n"
        "role-doc-version: v2.61.0\n"
        "base-commit: 0123456789ab\n"
        f"started: {started}\n"
        f"ended: {started}\n"
        "claude-session-id: 00000000-0000-0000-0000-000000000000\n"
        f"work-items: {work_items}\n"
        "---\n\n"
        + body
    )


# Three decisions, and TWO distractor sections whose bullets must not be listed: the
# escalation half of the same ledger, and the stub heading every journal carries.
THREE_AND_A_DISTRACTOR = """### What happened

- Session opened.

### Open questions for the user

- Should the fixture escalate? This bullet is an ESCALATION, not a decision.
- A second escalation.

### Decisions I made without you, for review

1. **The first call, about WI-0111.** Reasoning, wrapped the way a journal wraps it
   across more than one line. *Rejected:* the other thing.

2. **The second call.** No item is cited in this entry at all, deliberately.

3. **The third call, about WI-0222 and OPS-0003.** Two ids, one entry.

### Notes filed

- A note. Not a decision.
"""

EMPTY_SECTION = """### What happened

- Session opened.

### Decisions I made without you, for review

### Notes filed

- Nothing was decided; the section is here and empty.
"""

NO_SECTION = """### What happened

- Session opened.

### Notes filed

- This journal never wrote a decisions section at all.
"""

OUT_OF_WINDOW = """### Decisions I made without you, for review

- **An older call.** Before the window opens.
"""


class DecisionsViewTest(unittest.TestCase):
    SINCE = "2026-09-06"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.jd = self.tmp / "sessions" / "journal"
        self.jd.mkdir(parents=True)
        (self.jd / "README.md").write_text("not a journal — no session-id\n")
        (self.jd / "20260908T0101Z-devbox-aaaa.md").write_text(_journal(
            "20260908T0101Z-devbox-aaaa", "2026-09-08T01:01:00.000000+00:00",
            THREE_AND_A_DISTRACTOR, work_items="WI-0999"))
        (self.jd / "20260907T0101Z-devbox-bbbb.md").write_text(_journal(
            "20260907T0101Z-devbox-bbbb", "2026-09-07T01:01:00.000000+00:00",
            EMPTY_SECTION))
        (self.jd / "20260906T0101Z-devbox-cccc.md").write_text(_journal(
            "20260906T0101Z-devbox-cccc", "2026-09-06T01:01:00.000000+00:00",
            NO_SECTION))
        (self.jd / "20260901T0101Z-devbox-dddd.md").write_text(_journal(
            "20260901T0101Z-devbox-dddd", "2026-09-01T01:01:00.000000+00:00",
            OUT_OF_WINDOW))
        self._jdir = session.JOURNAL_DIR
        session.JOURNAL_DIR = self.jd

    def tearDown(self):
        session.JOURNAL_DIR = self._jdir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        out = io.StringIO()
        args = argparse.Namespace(since=kw.pop("since", self.SINCE),
                                  json=kw.pop("json", False))
        with contextlib.redirect_stdout(out):
            session.cmd_decisions(args)
        return out.getvalue()

    # ── (b) every decision under the heading, and nothing under any other ──────────

    def test_lists_every_decision_in_the_window(self):
        rows, _unparsed, absent = session.decision_rows(
            self.SINCE, journal_dir=self.jd)
        self.assertEqual(3, len(rows), [r["decision"] for r in rows])
        self.assertEqual(["The first call, about WI-0111.",
                          "The second call.",
                          "The third call, about WI-0222 and OPS-0003."],
                         [r["decision"] for r in rows])
        self.assertEqual([1, 2, 3], [r["n"] for r in rows])
        self.assertEqual({"20260908T0101Z-devbox-aaaa"}, {r["journal"] for r in rows})
        self.assertEqual("2026-09-08", rows[0]["date"])
        # The record says WHO decided, and reads it off the heading rather than the prose.
        self.assertEqual({"architect"}, {r["decided_by"] for r in rows})

    def test_a_distractor_heading_contributes_nothing(self):
        rows, _u, _a = session.decision_rows(self.SINCE, journal_dir=self.jd)
        blob = " ".join(r["text"] for r in rows)
        self.assertNotIn("ESCALATION", blob)
        self.assertNotIn("A note.", blob)
        # …and the escalation half of the same ledger still reads its own section.
        body = session.parse_journal(
            (self.jd / "20260908T0101Z-devbox-aaaa.md").read_text())[1]
        self.assertEqual(
            2, session.ledger_count(body, session.LEDGER_ESCALATION_HEADINGS))

    def test_item_ids_cited_by_the_entry_and_by_the_session(self):
        rows, _u, _a = session.decision_rows(self.SINCE, journal_dir=self.jd)
        self.assertEqual(["WI-0111"], rows[0]["items"])
        self.assertEqual([], rows[1]["items"])
        self.assertEqual(["OPS-0003", "WI-0222"], rows[2]["items"])
        # An uncited entry falls back to the session's items — in brackets, because
        # "the session held this" is a different claim from "the entry cited this".
        self.assertEqual(["WI-0999"], rows[1]["session_items"])
        self.assertEqual("(WI-0999)", session._decision_items(rows[1]))
        self.assertEqual("WI-0111", session._decision_items(rows[0]))

    # ── absent is not zero ────────────────────────────────────────────────────────

    def test_an_empty_section_is_zero_decisions_and_not_absent(self):
        _r, _u, absent = session.decision_rows(self.SINCE, journal_dir=self.jd)
        self.assertNotIn("20260907T0101Z-devbox-bbbb", [a["journal"] for a in absent])

    def test_a_missing_section_is_absent_and_not_zero(self):
        _r, _u, absent = session.decision_rows(self.SINCE, journal_dir=self.jd)
        self.assertEqual(["20260906T0101Z-devbox-cccc"], [a["journal"] for a in absent])
        self.assertIn("absent is not zero", self._run())

    def test_a_journal_before_the_window_is_excluded(self):
        rows, _u, absent = session.decision_rows(self.SINCE, journal_dir=self.jd)
        ids = [r["journal"] for r in rows] + [a["journal"] for a in absent]
        self.assertNotIn("20260901T0101Z-devbox-dddd", ids)
        # …and is listed again once the window opens far enough back.
        wider, _u, _a = session.decision_rows("2026-09-01", journal_dir=self.jd)
        self.assertIn("20260901T0101Z-devbox-dddd", [r["journal"] for r in wider])

    # ── (c) --json is the same rows ───────────────────────────────────────────────

    def test_json_emits_the_same_rows(self):
        payload = json.loads(self._run(json=True))
        rows, _u, absent = session.decision_rows(self.SINCE, journal_dir=self.jd)
        self.assertEqual(rows, payload["decisions"])
        self.assertEqual(absent, payload["absent"])
        self.assertEqual(self.SINCE, payload["since"])
        self.assertEqual("--since", payload["since_source"])
        human = self._run()
        for r in payload["decisions"]:
            self.assertIn(r["decision"], human)
            self.assertIn(r["journal"], human)

    def test_json_carries_the_full_entry_text_the_human_view_trims(self):
        payload = json.loads(self._run(json=True))
        first = payload["decisions"][0]
        self.assertIn("Rejected:", first["text"])
        self.assertNotIn("Rejected:", first["decision"])
        # Wrapped journal prose arrives as one line, not as the author's hard wraps.
        self.assertNotIn("\n", first["text"])

    # ── the window's own provenance ───────────────────────────────────────────────

    def test_since_defaults_to_the_last_ops_0007_run(self):
        root = self.tmp / "ops-run"
        (root / "ops-items").mkdir(parents=True)
        (root / "ops-items" / "OPS-0007-weekly.md").write_text(
            "# OPS-0007: Weekly findings review\n\n"
            "- status: open\n- cadence: weekly\n- last-completed: 2026-09-06\n"
            "- last-result: pass\n- due: \n- group: Governance\n- source: \n")
        prior = session.ROOT
        try:
            session.ROOT = root
            since, why = session._decisions_since_default()
        finally:
            session.ROOT = prior
        self.assertEqual("2026-09-06", since)
        self.assertIn("OPS-0007", why)

    def test_a_never_run_review_falls_back_to_the_window_and_says_so(self):
        root = self.tmp / "ops-never"
        (root / "ops-items").mkdir(parents=True)
        (root / "ops-items" / "OPS-0007-weekly.md").write_text(
            "# OPS-0007: Weekly findings review\n\n"
            "- status: open\n- cadence: weekly\n- last-completed: \n"
            "- last-result: \n- due: \n- group: Governance\n- source: \n")
        prior = session.ROOT
        try:
            session.ROOT = root
            since, why = session._decisions_since_default()
        finally:
            session.ROOT = prior
        expect = (session.datetime.strptime(session._today(), "%Y-%m-%d")
                  - session.timedelta(days=session.DECISIONS_WINDOW_DAYS)).date()
        self.assertEqual(expect.isoformat(), since)
        self.assertIn("never run", why)
        # The default must never silently widen to all of history.
        self.assertNotEqual("", since)

    def test_a_malformed_since_refuses_rather_than_guessing(self):
        with self.assertRaises(SystemExit) as e:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                session.cmd_decisions(argparse.Namespace(since="last tuesday", json=False))
        self.assertEqual(2, e.exception.code)

    # ── (a) read-only ─────────────────────────────────────────────────────────────

    def test_the_view_writes_nothing(self):
        def snapshot():
            return {p: p.stat().st_mtime_ns for p in sorted(self.tmp.rglob("*"))}
        before = snapshot()
        self._run()
        self._run(json=True)
        self.assertEqual(before, snapshot())


class LedgerRefactorTest(unittest.TestCase):
    """The split moved out of `curate/metrics.py`; the NUMBER it mines must not move
    with it. WI-0159's aggregate is a published metric, and a refactor that shifts a
    metric by one is indistinguishable afterwards from the behaviour changing."""

    def test_metrics_counts_exactly_what_the_splitter_splits(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
        import metrics  # noqa: E402

        for body in (THREE_AND_A_DISTRACTOR, EMPTY_SECTION, NO_SECTION, OUT_OF_WINDOW):
            entries = session.ledger_entries(body, session.LEDGER_DECISION_HEADINGS)
            count = metrics._ledger_section(body, metrics.DECISION_HEADINGS)
            self.assertEqual(None if entries is None else len(entries), count)

        self.assertEqual(3, metrics._ledger_section(THREE_AND_A_DISTRACTOR,
                                                   metrics.DECISION_HEADINGS))
        self.assertEqual(0, metrics._ledger_section(EMPTY_SECTION,
                                                   metrics.DECISION_HEADINGS))
        self.assertIsNone(metrics._ledger_section(NO_SECTION, metrics.DECISION_HEADINGS))

    def test_metrics_headings_are_the_harness_headings(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))
        import metrics  # noqa: E402
        self.assertIs(metrics.DECISION_HEADINGS, session.LEDGER_DECISION_HEADINGS)
        self.assertIs(metrics.ESCALATION_HEADINGS, session.LEDGER_ESCALATION_HEADINGS)
        # WI-0278's third heading, added here rather than only beside its own feature for
        # the reason this class exists: the copy that goes wrong quietly is the one where
        # metrics spells a heading itself instead of re-exporting the harness's.
        self.assertIs(metrics.MIDSESSION_HEADINGS, session.LEDGER_MIDSESSION_HEADINGS)


class DecisionLineTest(unittest.TestCase):
    """The decision LINE is the column a reviewer actually reads, and the one shape that
    broke it was found on the live journals rather than imagined: a session that recorded
    eighteen delegated calls opened each with the item id in the bold, so the lead was an
    identifier and the column said nothing the id column had not already said."""

    def test_a_bolded_lead_is_the_decision_line(self):
        self.assertEqual(
            "The publish stays inside the lock",
            session._ledger_lead("**The publish stays inside the lock** — number "
                                 "release, then push. **Rejected:** the other way."))

    def test_a_bold_that_is_only_an_item_id_falls_through_to_the_sentence(self):
        line = session._ledger_lead(
            "**WI-0004** — fold the resolver half into WI-0209; WI-0004 stays the "
            "fleet migration. WI-0209's own build order already draws that seam.")
        self.assertIn("fold the resolver half", line)
        self.assertNotIn("build order", line)     # the first sentence, not the entry
        self.assertNotIn("**", line)              # and without the markup

    def test_an_entry_with_no_bold_at_all_uses_its_first_sentence(self):
        self.assertEqual(
            "Decided the thing",
            session._ledger_lead("Decided the thing. And then explained it at length."))

    def test_a_bold_mentioning_an_id_alongside_prose_is_still_the_lead(self):
        # Only a bold that is NOTHING but ids falls through; one that says something
        # keeps its own words.
        self.assertEqual(
            "WI-0212 stays open on purpose",
            session._ledger_lead("**WI-0212 stays open on purpose** — because reasons."))


# ── WI-0330 residue (OPS-0007 agenda A1): the bold-ordinal entry form ─────────────
#
# NEGATIVE CONTROL, DRAWN FROM LIVE DATA (agenda B6: "every new detector ships one
# negative control drawn from live data, cited by path"). This is the first two entries
# of the decisions section of
#
#     sessions/journal/20260912T1200Z-devbox-8e11.md
#
# copied verbatim, em dashes and all. It is EMBEDDED rather than read from the live
# store because a suite running inside the merge gate must not assert on production data
# (tests/test_poga_fleet.py) — but it is not author-written, which is the whole point: an
# author writing a fixture writes the form they already had in mind, and the form that
# was being dropped is the one nobody had in mind. Before the fix this section parsed to
# ZERO entries and the renderer showed neither a count nor an absence.
BOLD_ORDINAL_FROM_A_REAL_JOURNAL = """### Decisions I made without you, for review

**1. Scan the body for identity lines, rather than enlarging the tail.**
Rejected: a bigger tail (say 40 lines). It fixes the one-failure case by luck and fails
the moment the last failure's traceback is long or there are several failures \u2014 the
identity is in the body, so the body is where it has to be found. Also rejected:
changing `run_suite` to re-print a roll-call after `FAILED (...)` so any tail-keeper
downstream inherits the fix. It is the more structural answer and I may still want it,
but it perturbs the output block that `_test_verdict` anchors on, and it fixes only the
suite \u2014 the receipt has to work for every gate command.

**2. Cap named tests at 10 in the receipt, 20 in `poga test`, and count the overflow.**
Different numbers because the losses differ: the gate's receipt is all the operator
gets (the worktree is deleted in a `finally`), while `poga test` prints a path to the
full output on disk. Rejected an uncapped receipt: a 300-failure run would paste 300
lines into a terminal and a lane note ([`cap-what-can-run-away`]).
"""

# A heading whose section carries prose that no marker recognises AND no bold lead. This
# is the state the renderer now has to name instead of swallowing.
UNRECOGNISED_SECTION = """### Decisions I made without you, for review

nothing here opens the way an entry opens, so the splitter cannot find one.
"""


class BoldOrdinalEntryTest(unittest.TestCase):
    """The form 39 of 67 sections in one week were written in, and which parsed to
    nothing.

    Journals wrap the ordinal inside the bold lead. `_LEDGER_ENTRY_RE` recognised a
    `-`/`*` bullet and an `N.`/`N)` ordinal, so `**1. ...**` matched neither, the section
    split into `[]`, and `[]` was rendered as neither present nor absent — the count read
    28 sessions where 67 had written one.
    """

    def test_the_real_journal_form_parses_into_its_entries(self):
        entries = session.ledger_entries(BOLD_ORDINAL_FROM_A_REAL_JOURNAL,
                                         session.LEDGER_DECISION_HEADINGS)
        self.assertEqual(2, len(entries), entries)

    def test_this_exact_fixture_used_to_parse_to_nothing(self):
        """The property, stated against the old pattern rather than remembered.

        A detector proves itself on the defect: if this assertion ever fails, the old
        pattern has started matching the bold-ordinal form and the fixture has stopped
        being a control.
        """
        import re
        old = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S", re.M)
        section = BOLD_ORDINAL_FROM_A_REAL_JOURNAL.split("for review", 1)[1]
        self.assertEqual([], old.findall(section))

    def test_the_ordinal_is_the_marker_and_the_bold_is_the_lead(self):
        """`**1. Scan ...**` flattens to `**Scan ...**`, so the lead is the decision.

        Stripping `**1. ` whole would tear the lead's opening delimiter off, and
        `_ledger_lead` would silently fall through to a first-sentence split — the row
        would still render, with the wrong text in it.
        """
        entries = session.ledger_entries(BOLD_ORDINAL_FROM_A_REAL_JOURNAL,
                                         session.LEDGER_DECISION_HEADINGS)
        lead = session._ledger_lead(session._ledger_flatten(entries[0]))
        self.assertEqual(
            "Scan the body for identity lines, rather than enlarging the tail.", lead)
        self.assertEqual(
            "Cap named tests at 10 in the receipt, 20 in `poga test`, and count the "
            "overflow.",
            session._ledger_lead(session._ledger_flatten(entries[1])))


class ThirdStateTest(unittest.TestCase):
    """Heading present / nothing parsed is its own answer, and it is COUNTED.

    `decision_rows` has always told absent (None) from empty (`[]`); only the first
    reached the footer. A section that parsed to nothing contributed no row and appeared
    in no absence, so it left the view as though the session had never happened.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.jd = self.tmp / "sessions" / "journal"
        self.jd.mkdir(parents=True)
        (self.jd / "20260912T0101Z-devbox-eeee.md").write_text(_journal(
            "20260912T0101Z-devbox-eeee", "2026-09-12T01:01:00.000000+00:00",
            BOLD_ORDINAL_FROM_A_REAL_JOURNAL))
        (self.jd / "20260912T0202Z-devbox-ffff.md").write_text(_journal(
            "20260912T0202Z-devbox-ffff", "2026-09-12T02:02:00.000000+00:00",
            UNRECOGNISED_SECTION))
        (self.jd / "20260912T0303Z-devbox-9999.md").write_text(_journal(
            "20260912T0303Z-devbox-9999", "2026-09-12T03:03:00.000000+00:00",
            NO_SECTION))
        self._jdir = session.JOURNAL_DIR
        session.JOURNAL_DIR = self.jd

    def tearDown(self):
        session.JOURNAL_DIR = self._jdir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        out = io.StringIO()
        args = argparse.Namespace(since=kw.pop("since", "2026-09-11"),
                                  json=kw.pop("json", False))
        with contextlib.redirect_stdout(out):
            session.cmd_decisions(args)
        return out.getvalue()

    def test_the_three_states_are_three_different_buckets(self):
        rows, unparsed, absent = session.decision_rows("2026-09-11", journal_dir=self.jd)
        self.assertEqual({"20260912T0101Z-devbox-eeee"}, {r["journal"] for r in rows})
        self.assertEqual(["20260912T0202Z-devbox-ffff"],
                         [u["journal"] for u in unparsed])
        self.assertEqual(["20260912T0303Z-devbox-9999"],
                         [a["journal"] for a in absent])

    def test_the_footer_prints_all_three_counts(self):
        out = self._run()
        # TWO sessions wrote a section: one that parsed and one that did not. The
        # unparsed one counting toward "with a section" is the whole correction — it is
        # the session that used to appear in no count at all.
        self.assertIn("2 session(s) with a section", out)
        self.assertIn("1 unparsed", out)
        self.assertIn("1 absent", out)

    def test_an_unparsed_session_is_named_so_it_can_be_read_by_hand(self):
        out = self._run()
        self.assertIn("20260912T0202Z-devbox-ffff", out)

    def test_json_carries_the_unparsed_bucket_too(self):
        payload = json.loads(self._run(json=True))
        self.assertEqual(["20260912T0202Z-devbox-ffff"],
                         [u["journal"] for u in payload["unparsed"]])

    def test_a_recorded_zero_is_still_a_zero_and_never_a_decision(self):
        """`None this session.` is prose under the heading, and it means zero.

        The widening treats unrecognised prose WITH a bold lead as one entry, because
        that is a decision written without a marker. Without the bold-lead condition it
        would also promote this line into a decision row that nobody decided — the
        `no-fabricated-data` failure, arriving through the fix for the opposite one.
        """
        body = "### Decisions I made without you, for review\n\nNone this session.\n"
        self.assertEqual(
            [], session.ledger_entries(body, session.LEDGER_DECISION_HEADINGS))
        self.assertEqual(
            0, session.ledger_count(body, session.LEDGER_DECISION_HEADINGS))


if __name__ == "__main__":
    unittest.main()
