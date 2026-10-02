#!/usr/bin/env python3
"""Substrate-description check — the hand-maintained prose must describe the substrate that exists.

WI-0339. The federation already derives every list a MACHINE reads: `CANON.md` from the
registries, `STANDARD.md` from `standard-source.md`, `.claude/settings.json` from the
config, `port-blocks.md` from `dev-environments.json` — each with a generator and a
`--check`. Every one of those held. What drifted was the prose a READER orients from,
which had no generator, no check, and no way to be wrong out loud.

The measured drift, 2026-09-11: [ADR-0118](../adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md)
turned `session.py` into a ~150-line entry point over `sessionlib/` on 2026-09-06.
Five days later `POGA-OVERVIEW-AND-SCALABILITY.md` still opened "session.py is the
principal operational module", `federation-arch.md` §7 attributed 28,443 lines of
mechanics to a 149-line file, and the string `sessionlib` appeared in neither.

WHY A CHECK AND NOT A GENERATOR. WI-0339 says in its own body that whether these three
surfaces should be generated is "a separate and larger question that this item does not
settle", and it is right to hold that open: the Concern column of a module table and the
Lifecycle cell of an artifact row are judgements, and a generator that emitted them would
be emitting confident prose nobody wrote. So this asserts the one thing that is NOT a
judgement — that every subject is *named* — and leaves what is said about it to an author.
A missing name is the failure mode that actually occurred; a wrong sentence about a named
module is a different and much rarer defect.

THE ADR-INDEX HALF IS NOT THE SAME CHECK, THOUGH IT LOOKS LIKE IT. `adr/README.md` is a
row per ADR, so "every ADR has a row" is the obvious assertion and it is not the useful
one. The incident was this: on 2026-09-10 a session read the index, saw the sequence run
0119 -> 0122, and reported that "ADR-0120 and ADR-0121 have no index rows". 0120 was
genuinely missing a row. **ADR-0121 has never existed** — it was drawn from the allocator
on 2026-09-07 by a second call made to confirm the mechanism worked, never spent, and left
to expire. The reader could not tell a burned number from a missing row, because nothing in
the file distinguished them, and produced a confident wrong answer about a record that
does not exist. So the check asserts BOTH halves of that distinction: every ADR has a row
(D3), and every hole in the number run is declared (D5). Either one alone leaves the
ambiguity that caused the incident intact.

AND THERE IS A THIRD STATE, WHICH THE FIRST CUT OF D5 HAD NO NAME FOR (WI-0336). Burned
and missing are not the only ways a number can be absent from disk: it can be DRAWN AND
IN FLIGHT, held right now by another lane that has not landed yet. The federation runs
six lanes concurrently by design (ADR-0051) and `adr-next` hands each a distinct number,
so two lanes drawing 0125 and 0126 leave a hole at 0125 for exactly as long as the first
lane takes to land — during which D5 failed the SECOND lane's gate for a condition the
second lane neither caused nor can fix. Measured 2026-09-11: poga-7 drew 0126, poga-5
held 0125 with its ADR committed and unlanded, and poga-7's land was blocked.

Declaring 0125 under `## Unused numbers` would have been the available workaround and it
is a lie — the number is spent, not burned — written into the very table that exists to
tell those apart. So the allocator is asked instead, because it is the authority on the
question ([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)):
a hole with a LIVE, unexpired `adr-alloc` reservation is RESERVED, is not a problem, and
is NAMED in the output rather than passed over in silence — an exemption nobody can see is
how a check starts certifying instead of checking.

The exemption FAILS CLOSED. An unreadable coordination store yields no reservations and
therefore no exemptions, so the check behaves exactly as it did before; not being able to
read the allocator is a reason to withhold the excuse, never to grant it. It is also
self-expiring: when the holding lane lands, the file appears and the hole closes; when the
reservation expires, the hole is reported again as an undeclared one.

Checks, each deriving its subjects from the authority rather than from a list kept here
([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)):

  D1  every `sessionlib/*.py` is named in POGA-OVERVIEW-AND-SCALABILITY.md
  D2  every `sessionlib/*.py` is named in federation-arch.md section 7
  D3  every `adr/NNNN-*.md` has exactly one row in the adr/README.md index
  D4  every index row's number has a file on disk, and the row's link is that file
  D5  every hole in the ADR number run is declared under `## Unused numbers`,
      OR still held by a live reservation in the allocator
  D6  no blank line inside the index table
  D7  no index row contradicts its ADR's `Status`
  D8  no index row contradicts its ADR's `Date`

D7 and D8 abstain rather than guess. The index legitimately condenses a long status into
a short cell, so they compare only the one vocabulary word both sides must agree on and
say nothing at all about a cell they do not recognise. A check that fired on paraphrase
would be switched off, and a switched-off check guards nothing.

D6 earns its place: a blank line ends a Markdown table, so the two that were in the index
rendered it as three separate tables. The reader who invented ADR-0121 was reading across
one of those breaks. A gap that also *looks* like an ending is the condition the mistake
needed.

Exit codes — three answers, never two
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)):

  0  checked, and everything matches
  1  checked, and found something
  2  COULD NOT CHECK — a subject is missing entirely (no `sessionlib/`, no index, no
     section 7, no `## Unused numbers` heading). This is emphatically not a pass. The
     drift class this guards against is a description losing its subject, so a subject
     that has been renamed or moved away is exactly when a confident `0` would be worst.
     An absent subject disables only the checks that NEED it — the rest still run and
     still report — and `2` outranks `1`, because a partial answer must not read as a
     complete one.

CLI:
  python3 curate/check_substrate_docs.py            # report findings
  python3 curate/check_substrate_docs.py --check    # same; the spelling the land gate uses
  python3 curate/check_substrate_docs.py --status   # one-line signal for the SessionStart hook

Federation-only — `curate/` is not shipped to members.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SESSIONLIB = "sessionlib"
OVERVIEW = "POGA-OVERVIEW-AND-SCALABILITY.md"
ROLE_DOC = "federation-arch.md"
ADR_DIR = "adr"
ADR_INDEX = "adr/README.md"

SECTION_7_START = "## 7. Artifacts I maintain"
SECTION_7_END = "## 8. Inputs"
UNUSED_HEADING = "## Unused numbers"

def reserved_adr_numbers():
    """{int} ADR numbers a live `adr-alloc` reservation still holds.

    Read from the ALLOCATOR, which owns the fact, rather than inferred from anything on
    disk — the whole defect D5 exists for is a reader inferring a number's meaning from
    its absence. `_adr_alloc_holds` is the same helper the land gate consults.

    Returns an EMPTY set on any failure, which is the fail-closed direction: no
    reservations means no exemptions means the pre-existing behaviour. A store we cannot
    read must not be able to excuse a hole."""
    try:
        sys.path.insert(0, str(ROOT))
        # Through `session`, not `sessionlib.coord` directly: ADR-0118 made `session.py` a
        # thin entry point that ASSEMBLES the package, and the submodule does not import
        # standalone (`COORD_KINDS` is injected at assembly). Importing the submodule
        # raises, which the fail-closed `except` below would have swallowed into a silent
        # "no reservations" — the exemption would never fire and nothing would say why.
        import session
        out = set()
        for name in session._adr_alloc_holds():
            try:
                out.add(int(str(name).strip()))
            except (TypeError, ValueError):
                continue        # a malformed key excuses nothing; the hole stays reported
        return out
    except Exception:
        return set()


#: An index row: `| [0118](0118-....md) | ... |`
ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|")
# The unused-numbers table's own row pattern used to live here. It is now read by
# `session._declared_holes`, which the allocator reads it with too (WI-0379).
#: An ADR file: `0118-session-py-is-a-package-behind-a-thin-entry-point.md`
ADR_FILE_RE = re.compile(r"^(\d{4})-.+\.md$")
#: A whole index row, split into cells, for the Status and Date columns.
FULL_ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\([^)]+\)\s*\|(.*)\|\s*$")
#: An ADR header field, in either shape the corpus uses — `**Status:** x` and `- **Status:** x`.
FIELD_RE = "^-?\\s*\\*\\*%s:\\*\\*\\s*(.+)$"
#: The vocabulary `adr/README.md` itself declares under `## Conventions`.
STATUS_WORDS = ("Accepted", "Proposed", "Deprecated", "Superseded", "Rejected", "Withdrawn")
#: How far into a file a header field may be before we stop looking.
HEADER_LINES = 16


class CannotCheck(Exception):
    """A subject is absent, so nothing was verified. Never answer 0 on this path."""


def _read(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        raise CannotCheck(f"{rel} is not there — nothing to check it against")
    return p.read_text(encoding="utf-8")


def harness_parts() -> list[str]:
    """The authority is the directory, not a list in this file."""
    d = ROOT / SESSIONLIB
    if not d.is_dir():
        raise CannotCheck(f"{SESSIONLIB}/ is not there — the harness package has moved or gone")
    parts = sorted(p.name for p in d.glob("*.py"))
    if not parts:
        raise CannotCheck(f"{SESSIONLIB}/ holds no .py files")
    return parts


def section_7(role_doc: str) -> str:
    start = role_doc.find(SECTION_7_START)
    if start < 0:
        raise CannotCheck(f"{ROLE_DOC} has no {SECTION_7_START!r} — the section was renamed or removed")
    end = role_doc.find(SECTION_7_END, start)
    if end < 0:
        raise CannotCheck(f"{ROLE_DOC} section 7 has no {SECTION_7_END!r} after it — cannot bound the section")
    return role_doc[start:end]


def adr_files() -> dict[int, str]:
    d = ROOT / ADR_DIR
    if not d.is_dir():
        raise CannotCheck(f"{ADR_DIR}/ is not there")
    out: dict[int, str] = {}
    for p in sorted(d.glob("*.md")):
        m = ADR_FILE_RE.match(p.name)
        if m:
            out[int(m.group(1))] = p.name
    if not out:
        raise CannotCheck(f"{ADR_DIR}/ holds no NNNN-*.md files")
    return out


def adr_field(name: str, path: pathlib.Path) -> str | None:
    """One header field out of an ADR, tolerant of both header shapes in the corpus.

    ADR-0107 and ADR-0118 write their headers as a bullet list; every other ADR writes
    bare bold lines. A parser that knew only one shape would read `None` for those two
    and quietly check nothing — the exact way `standard_check.py`'s detectors went blind
    when session.py became a package (ADR-0118's own Consequences).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines()[:HEADER_LINES]:
        m = re.match(FIELD_RE % name, line.strip())
        if m:
            return m.group(1).strip()
    return None


def status_word(value: str | None) -> str | None:
    """The leading vocabulary word, or None if this is not a form we recognise.

    None means DO NOT JUDGE. The index legitimately condenses — "Accepted; partially
    superseded by ADR-0010" becomes "Accepted (partially superseded by [ADR-0010](...))"
    — so the check compares only the one word both sides must agree on, and abstains
    entirely on anything it does not recognise. A check that fired on paraphrase would
    be turned off within a week, and then it would be guarding nothing.
    """
    if not value:
        return None
    for w in STATUS_WORDS:
        if value.startswith(w):
            return w
    return None


def row_cells(index: str) -> dict[int, list[str]]:
    """Number -> the row's cells, for the columns that are derivable from the record."""
    out: dict[int, list[str]] = {}
    for line in index.splitlines():
        m = FULL_ROW_RE.match(line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            if len(cells) >= 4:
                out.setdefault(int(m.group(1)), cells)
    return out


def index_rows(index: str) -> dict[int, list[str]]:
    """Number -> the link target(s) of every row claiming it. A list, so duplicates show."""
    rows: dict[int, list[str]] = {}
    for line in index.splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.setdefault(int(m.group(1)), []).append(m.group(2))
    if not rows:
        raise CannotCheck(f"{ADR_INDEX} has no index rows — the table format changed")
    return rows


def declared_unused(index: str) -> set[int]:
    """The numbers `adr/README.md` declares permanently unused.

    DELEGATED, NOT REIMPLEMENTED (WI-0379). The allocator now reads this same table to
    refuse handing a burned number out again, and two parsers of one table is how the
    check and the allocator come to disagree about which numbers are spent — silently, and
    in the direction that reissues a number. `session._declared_holes` is the one reader;
    the POLICY on each of its answers stays here, because this repo must carry the table
    and a member of the fleet need not.

    Through `session`, not `sessionlib.config`, for the reason `reserved_adr_numbers`
    records above: the submodules do not import standalone."""
    try:
        sys.path.insert(0, str(ROOT))
        import session
    except Exception as e:                       # pragma: no cover - import-shape guard
        raise CannotCheck(f"could not load the shared hole-table reader ({e})")
    holes, problem, state = session._declared_holes(
        ROOT / ADR_INDEX, heading=UNUSED_HEADING.lstrip("# ").strip(), text=index)
    if state in (session.HOLES_NO_INDEX, session.HOLES_NO_SECTION):
        raise CannotCheck(
            f"{ADR_INDEX} has no {UNUSED_HEADING!r} section — without it a hole in the number "
            f"run cannot be told from a missing row, which is the defect WI-0339 was filed for")
    if state == session.HOLES_UNREADABLE:
        raise CannotCheck(
            f"{problem}. The heading is there and the table under it is not in the shape "
            f"either this check or the allocator can read, so neither is checking anything")
    return set(holes)


def blank_lines_inside_table(index: str) -> int:
    lines = index.splitlines()
    idx = [i for i, l in enumerate(lines) if ROW_RE.match(l)]
    if not idx:
        return 0
    return sum(1 for i in range(idx[0], idx[-1]) if not lines[i].strip())


def run() -> tuple[list[str], list[str]]:
    """Return (findings, unchecked).

    EVERY CHECK IS ATTEMPTED. The first cut of this raised on the first absent subject,
    so pointing it at the pre-fix tree answered with one line about a missing
    `## Unused numbers` heading and said nothing about the four harness parts the
    overview had never heard of. That is the same shape as the defect: a confident
    partial answer that reads like a complete one. A subject that is absent disables
    only the checks that need it, and says which.
    """
    problems: list[str] = []
    unchecked: list[str] = []

    try:
        parts = harness_parts()
    except CannotCheck as e:
        unchecked.append(f"D1+D2 (harness parts named in the prose): {e}")
        parts = None

    # D1 / D2 — the harness package is NAMED where the substrate is described.
    surfaces: list[tuple[str, str]] = []
    try:
        surfaces.append((OVERVIEW, _read(OVERVIEW)))
    except CannotCheck as e:
        unchecked.append(f"D1 ({OVERVIEW}): {e}")
    try:
        surfaces.append((f"{ROLE_DOC} section 7", section_7(_read(ROLE_DOC))))
    except CannotCheck as e:
        unchecked.append(f"D2 ({ROLE_DOC} section 7): {e}")

    if parts is not None:
        for label, text in surfaces:
            missing = [f"{SESSIONLIB}/{n}" for n in parts if n not in text]
            if missing:
                problems.append(
                    f"{label}: does not name {len(missing)} harness part(s) — {', '.join(missing)}. "
                    f"The package is the substrate; a description that omits a part describes "
                    f"something that is not there.")

    # D3 / D4 / D5 / D6 — the ADR index against its own directory.
    try:
        files = adr_files()
    except CannotCheck as e:
        unchecked.append(f"D3-D6 (the ADR index): {e}")
        return problems, unchecked
    try:
        index = _read(ADR_INDEX)
        rows = index_rows(index)
    except CannotCheck as e:
        unchecked.append(f"D3-D6 (the ADR index): {e}")
        return problems, unchecked

    for n in sorted(set(files) - set(rows)):
        problems.append(f"{ADR_INDEX}: ADR-{n:04d} ({files[n]}) is on disk with no index row.")
    for n in sorted(set(rows) - set(files)):
        problems.append(f"{ADR_INDEX}: a row claims ADR-{n:04d}, and no such file is in {ADR_DIR}/.")
    for n in sorted(n for n, links in rows.items() if len(links) > 1):
        problems.append(f"{ADR_INDEX}: ADR-{n:04d} has {len(rows[n])} rows; it must have exactly one.")
    for n in sorted(rows):
        if n in files and rows[n][0] != files[n]:
            problems.append(
                f"{ADR_INDEX}: ADR-{n:04d}'s row links {rows[n][0]!r}, and the file is {files[n]!r}.")

    holes = sorted(set(range(min(files), max(files) + 1)) - set(files))
    try:
        declared = declared_unused(index)
    except CannotCheck as e:
        unchecked.append(f"D5 (holes in the ADR number run): {e}")
    else:
        reserved = reserved_adr_numbers()
        for n in sorted(set(holes) - declared):
            if n in reserved:
                # DRAWN AND IN FLIGHT — a third state, neither burned nor missing. Named
                # rather than skipped silently: an exemption nobody can see is how a check
                # starts certifying instead of checking.
                # Printed where it is decided, the way `deliver.py` reports a CHANGELOG
                # cross-check it could not make: the signature stays a two-tuple, and the
                # exemption still reaches a human.
                print(f"note: {ADR_INDEX}: {n:04d} is a hole because another lane HOLDS "
                      f"it right now (live adr-alloc reservation). Not burned and not "
                      f"missing — it lands when that lane does. Nothing to declare.",
                      file=sys.stderr)
                continue
            problems.append(
                f"{ADR_INDEX}: {n:04d} is a hole in the number run and is not declared under "
                f"{UNUSED_HEADING!r}. A reader cannot tell a burned number from a missing record, "
                f"and on 2026-09-10 one did not — say which this is.")

    blanks = blank_lines_inside_table(index)
    if blanks:
        problems.append(
            f"{ADR_INDEX}: {blanks} blank line(s) inside the index table. A blank line ENDS a "
            f"Markdown table, so the index renders as {blanks + 1} separate tables and a gap "
            f"reads as an ending.")

    # D7 / D8 — the row must not CONTRADICT the record it indexes. A row reading
    # "Proposed" for an Accepted decision is worse than a missing row: a missing row is
    # visibly absent, and a wrong one is read and believed. Two were found on 2026-09-11
    # (one of them ADR-0112) alongside one date that had picked up an amendment's date
    # instead of the decision's own (ADR-0059).
    cells = row_cells(index)
    adr_dir = ROOT / ADR_DIR
    for n in sorted(set(cells) & set(files)):
        path = adr_dir / files[n]
        row_status, row_date = cells[n][-3], cells[n][-1]

        want, got = status_word(adr_field("Status", path)), status_word(row_status)
        if want and got and want != got:
            problems.append(
                f"{ADR_INDEX}: ADR-{n:04d}'s row says {got!r} and the record says {want!r}. "
                f"The record decides; the index describes it.")

        file_date = (adr_field("Date", path) or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", file_date) and row_date[:10] != file_date:
            problems.append(
                f"{ADR_INDEX}: ADR-{n:04d}'s row is dated {row_date[:10]!r} and the record is "
                f"dated {file_date!r}.")

    return problems, unchecked


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check that the hand-maintained descriptions of the substrate still describe it.")
    ap.add_argument("--check", action="store_true",
                    help="report findings and exit non-zero on any (the land-gate spelling; the default)")
    ap.add_argument("--status", action="store_true",
                    help="one-line signal for the SessionStart hook; always exits 0")
    args = ap.parse_args()

    problems, unchecked = run()

    if args.status:
        bits = []
        if problems:
            bits.append(f"{len(problems)} description(s) drifted from the substrate")
        if unchecked:
            bits.append(f"{len(unchecked)} check(s) COULD NOT RUN")
        if bits:
            print("Substrate docs: " + "; ".join(bits)
                  + " — run `python3 curate/check_substrate_docs.py`.")
        return 0

    if problems:
        print(f"{len(problems)} finding(s) — a hand-maintained description no longer describes "
              f"the substrate:\n")
        for p in problems:
            print(f"  - {p}")
        print()

    if unchecked:
        # NOT a pass, and it outranks a plain finding: part of the answer is missing, so
        # nothing here can be read as "the rest is fine".
        print(f"COULD NOT CHECK {len(unchecked)}:", file=sys.stderr)
        for u in unchecked:
            print(f"  - {u}", file=sys.stderr)
        print("\nThat part was not verified. This is not a pass.", file=sys.stderr)
        return 2

    if problems:
        print(f"Fix the description, not the check. If the substrate genuinely changed shape, "
              f"say so in {OVERVIEW} and {ROLE_DOC} section 7.")
        return 1

    print(f"Substrate descriptions match: {len(harness_parts())} harness part(s) named in "
          f"{OVERVIEW} and {ROLE_DOC} section 7; {len(adr_files())} ADR(s) indexed in "
          f"{ADR_INDEX}, every hole declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
