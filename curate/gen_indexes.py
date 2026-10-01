#!/usr/bin/env python3
"""Regenerate the receipt-ritual `index.md` courtesy tables from the directory layout.

Comms-brief F4 / ADR-0030 §3. PURE MECHANISM (P15 code-for-mechanism-not-judgment):
the model never decides section membership — the filesystem does. Each
`proposed-edits/<architect-id>/index.md` carries Markdown tables under one heading per
lifecycle state — `## Pending`, `## Accepted`, `## Applied`, `## Delivered`,
`## Declined`, `## Rejected`, `## Withdrawn`, `## Superseded` — listing the brief files
that physically live in the sibling directory of the same name. Agents hand-maintain
those tables and they DRIFT — a row stays under `## Pending` long after the file moved
to `applied/`. This script kills that drift class by deriving section membership
AUTHORITATIVELY from the directory while salvaging everything a human authored.

What is authoritative (regenerated): which edit-ids appear under which section, and
the first (Edit-ID) cell's link path.

What is salvaged (carried forward verbatim, matched by edit-id): the Description cell,
the Applied-date / At-version / Confirmed cells, and every other authored column. A
file with no existing row anywhere gets a new row with only the derivable cells filled
— Edit ID from the filename, Drafted from the brief's header (`Drafted on:` / `Date:`
line, else the leading date in the filename), Description from the brief's H1 as a
short placeholder. Non-derivable cells are NEVER fabricated: date/version/confirmed
columns get `*(verify)*`, everything else `—`.

What passes through untouched: all non-table prose — intro paragraph(s), blockquote
annotations, `## Notes`, and any authored section body that is not a table and not a
bare `(none)` placeholder (e.g. one member's deliberately-empty Applied note). Only table
bodies and bare `(none)` placeholders (when their directory is non-empty) are rewritten.

Idempotent: a correct index regenerates to itself, byte for byte.

What it will NOT fix, and reports instead (`PROBLEM` lines): a brief physically present
in two states at once — the directory contradicting itself, where which copy is
authoritative is a judgement, not a derivation — and a state directory holding briefs
whose name this script does not know, which would otherwise be silently omitted from a
confident-looking index.

CLI:
  python3 curate/gen_indexes.py [paths…]           # rewrite stale indexes in place
  python3 curate/gen_indexes.py --check [paths…]    # write nothing; exit non-zero if stale
`paths` may be index.md files, architect dirs, or the proposed-edits root; default is
the federation's own `proposed-edits/`. Only files named `index.md` are ever touched,
and only ones that already exist (no index is created for an un-indexed architect).

Exit codes — three answers, never two ([`declare-what-a-check-assumes`]):
  0  checked, and everything matches
  1  checked, and found something: a stale table, or a PROBLEM
  2  COULD NOT CHECK — no index.md found at all, so nothing was verified. This is
     emphatically not a pass. `proposed-edits/` is gitignored data, so anything that
     runs this against a scratch/clean checkout (a merge gate, CI) sees zero indexes;
     answering "OK" there would certify the gap rather than detect it.

Fail-open: an unexpected error prints a diagnostic and exits 0 so a session-end hook
is never bricked, and a PROBLEM in write mode is reported without being made fatal for
the same reason. A genuine finding under `--check` is not an error — it exits non-zero
by design.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Directory a `## <Header>` maps to, keyed by the header's first word (lowercased).
#
# This map is the generator's ASSUMPTION ABOUT THE FLEET'S VOCABULARY, and a directory
# missing from it is invisible rather than flagged — so it is kept honest against the
# real layout, not against the convention as documented. Surveyed across every
# architect dir: the original five missed three live states. `accepted/` and
# `declined/` are the federation's own triage vocabulary (some of our briefs live in
# `accepted/`, and none of them appeared in our index because of this gap); `delivered/`
# is the outbound marker used by several members. Adding a state here is what makes it
# visible — see `unknown_state_dirs()`, which reports any directory this map does not
# know rather than silently walking past it.
SECTION_DIRS = {
    "pending": "pending",
    "accepted": "accepted",
    "applied": "applied",
    "delivered": "delivered",
    "declined": "declined",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
    "superseded": "superseded",
}
# Canonical order for placing a section that has to be created from scratch — the
# lifecycle order a reader expects, inbound first: awaiting triage, triaged, acted on,
# sent, then the terminal dispositions.
CANON_ORDER = ["pending", "accepted", "applied", "delivered",
               "declined", "rejected", "withdrawn", "superseded"]

# Column schema used ONLY when a section needs a table built from nothing (a bare
# `(none)` placeholder over a non-empty dir, or a wholly missing section). An existing
# table always keeps its own header — this never reshapes a table a human already made.
# The new states carry only derivable columns on purpose: a date column we cannot read
# off the brief would be born full of `*(verify)*`, which is noise wearing the costume
# of a record (no-fabricated-data).
DEFAULT_HEADERS = {
    "pending": ["Edit ID", "Description", "Drafted"],
    "accepted": ["Edit ID", "Description", "Drafted"],
    "applied": ["Edit ID", "Description", "Drafted", "Applied", "At version"],
    "delivered": ["Edit ID", "Description", "Drafted"],
    "declined": ["Edit ID", "Description", "Drafted"],
    "rejected": ["Edit ID", "Description", "Drafted"],
    "withdrawn": ["Edit ID", "Description", "Drafted"],
    "superseded": ["Edit ID", "Why superseded"],
}

# Non-derivable cells in these columns get `*(verify)*` (never a fabricated value);
# every other non-derivable cell gets an em-dash.
VERIFY_COLUMNS = {"applied", "at version", "confirmed"}
DASH = "—"  # em dash

_LINK_RE = re.compile(r"^\[([^\]]*)\]\(([^)]*)\)\s*$")
_PLACEHOLDER_RE = re.compile(r"^\(none[\w ]*\)$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^##\s+([A-Za-z]+)")
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_DRAFTED_LINE_RE = re.compile(r"(?:drafted on|date)\s*[:*]*\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_H1_PREFIX_RE = re.compile(r"^FOR ARCHITECT\s*[—–-]\s*")


# --------------------------------------------------------------------------- cell helpers


def norm(header_cell):
    return header_cell.strip().lower()


def split_row(line):
    """Split a Markdown table row into stripped cells, honouring escaped `\\|`.

    A cell may legitimately contain a literal pipe written `\\|` (e.g. the federation
    Applied row's `resolve-orphan --keep\\|--bad`); splitting on a bare `|` there would
    shear the cell. Split only on unescaped pipes, then drop the empty fields the
    leading and trailing table pipes produce.
    """
    parts = re.split(r"(?<!\\)\|", line.strip())
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def build_row(cells):
    return "| " + " | ".join(cells) + " |"


def build_separator(ncols):
    return "|" + "---|" * ncols


def is_table_header(line):
    return line.strip().startswith("|")


def is_separator(line):
    s = line.strip()
    return bool(s) and set(s) <= set("|-: ") and "-" in s


def edit_id_of(cell):
    """(edit_id, is_link) for a first-column cell — link target's filename stem if a
    Markdown link, else the plain text. Both the link `[id](dir/id.md)` and a bare `id`
    forms appear in the wild (one member's superseded rows are plain text), and either is a
    valid handle onto the same brief."""
    m = _LINK_RE.match(cell.strip())
    if m:
        stem = pathlib.PurePosixPath(m.group(2)).name
        if stem.endswith(".md"):
            stem = stem[:-3]
        return stem, True
    return cell.strip(), False


# --------------------------------------------------------------------------- derivations


def _read_brief(path):
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def derive_description(brief_path):
    """The brief's H1 (minus a `FOR ARCHITECT —` prefix) as a short placeholder."""
    for line in _read_brief(brief_path).splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return _H1_PREFIX_RE.sub("", title).strip() or DASH
    return DASH


def derive_drafted(brief_path, edit_id):
    """A `Drafted on:` / `Date:` header line if present, else the leading date in the
    filename. Never guessed beyond those."""
    for line in _read_brief(brief_path).splitlines():
        m = _DRAFTED_LINE_RE.search(line)
        if m:
            return m.group(1)
    m = _FILENAME_DATE_RE.match(edit_id)
    return m.group(1) if m else DASH


# --------------------------------------------------------------------------- reconcile
#
# The index answers "what state is this brief in?", and the directory is the authority.
# Regenerating the tables makes the ANSWER match the directory — but it cannot make the
# QUESTION well-formed if the directory itself gives two answers for one brief. That is
# not hypothetical: 2026-08-05 the same brief sat in both `pending/` and `accepted/`,
# and the generated view would have listed it twice, once under each heading, with no
# indication that the two rows were the same file disagreeing with itself. A courtesy
# view that renders a contradiction as two tidy rows is worse than one that refuses.


def state_dirs_present(index_dir):
    """{dir_key: [edit-id, …]} for every KNOWN state directory that exists."""
    found = {}
    for dir_key in CANON_ORDER:
        d = index_dir / dir_key
        if d.is_dir():
            found[dir_key] = sorted(p.stem for p in d.glob("*.md"))
    return found


def find_cross_state_duplicates(index_dir):
    """{edit_id: [dir_key, …]} for any brief physically present in more than one state.

    Deliberately a HARD FINDING rather than something the generator resolves: which copy
    is authoritative is a judgement about what happened to the brief, and guessing it is
    how the 2026-08-05 duplicate would have been silently normalised away instead of
    surfaced. Reported and, under `--check`, non-zero."""
    where = {}
    for dir_key, stems in state_dirs_present(index_dir).items():
        for stem in stems:
            where.setdefault(stem, []).append(dir_key)
    return {eid: dirs for eid, dirs in sorted(where.items()) if len(dirs) > 1}


def unknown_state_dirs(index_dir):
    """Directory names beside the index that hold briefs but that `SECTION_DIRS` does
    not know — the shape this generator assumes, contradicted.

    Without this the failure is silent and reads as success: a member using a state name
    we never taught the map gets a clean, confident, and incomplete index
    (declare-what-a-check-assumes). Names it instead of walking past it."""
    out = []
    if not index_dir.is_dir():
        return out
    for d in sorted(index_dir.iterdir()):
        if not d.is_dir() or d.name in SECTION_DIRS:
            continue
        if any(d.glob("*.md")):
            out.append(d.name)
    return out


# --------------------------------------------------------------------------- salvage


def build_salvage(text):
    """Scan every table in the file and return:
      salvage[edit_id][normalized-header] -> raw authored cell (for columns after the
        first), so an authored cell carries forward by edit-id even across a move; and
      is_link[edit_id] -> whether that row's first cell was rendered as a link, so the
        generator preserves a deliberately plain-text handle (one member's superseded rows).
    """
    salvage = {}
    is_link = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if is_table_header(lines[i]) and i + 1 < len(lines) and is_separator(lines[i + 1]):
            headers = split_row(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = split_row(lines[j])
                if cells:
                    eid, linked = edit_id_of(cells[0])
                    if eid:
                        is_link[eid] = linked
                        col = salvage.setdefault(eid, {})
                        for k in range(1, min(len(cells), len(headers))):
                            col[norm(headers[k])] = cells[k]
                j += 1
            i = j
        else:
            i += 1
    return salvage, is_link


# --------------------------------------------------------------------------- row building


def build_cells(edit_id, dir_key, headers, section_dir, salvage, is_link):
    """One regenerated row (list of cells) for `edit_id` under section `dir_key`."""
    filename = edit_id + ".md"
    linked = is_link.get(edit_id, True)  # a brand-new file defaults to a link
    if linked:
        c0 = f"[{edit_id}]({dir_key}/{filename})"
    else:
        c0 = edit_id
    out = [c0]
    saved = salvage.get(edit_id, {})
    for h in headers[1:]:
        nh = norm(h)
        if nh in saved:
            out.append(saved[nh])
        elif nh == "description":
            out.append(derive_description(section_dir / filename))
        elif nh == "drafted":
            out.append(derive_drafted(section_dir / filename, edit_id))
        elif nh in VERIFY_COLUMNS:
            out.append("*(verify)*")
        else:
            out.append(DASH)
    return out


def order_edit_ids(existing_ids, target_stems):
    """Existing rows first, in their current order, filtered to files still present;
    then any remaining present files, sorted. Keeping existing order is what makes a
    stable index regenerate to itself; new/moved-in files append deterministically."""
    ordered, used = [], set()
    for eid in existing_ids:
        if eid in target_stems and eid not in used:
            ordered.append(eid)
            used.add(eid)
    for stem in sorted(target_stems):
        if stem not in used:
            ordered.append(stem)
            used.add(stem)
    return ordered


def regenerate_table_body(dir_key, headers, existing_rows, target_stems,
                          section_dir, salvage, is_link):
    existing_ids = [eid for eid, _linked, _cells in existing_rows]
    ordered = order_edit_ids(existing_ids, target_stems)
    return [build_row(build_cells(eid, dir_key, headers, section_dir, salvage, is_link))
            for eid in ordered]


# --------------------------------------------------------------------------- section rewrite


def find_table(body):
    """Index i of a table header inside a section body, or None. A table is a `|`-row
    immediately followed by a separator row."""
    for i in range(len(body) - 1):
        if is_table_header(body[i]) and is_separator(body[i + 1]):
            return i
    return None


def rewrite_section(dir_key, body, index_dir, salvage, is_link, notes):
    """Return the section's regenerated body lines. `notes` accumulates human-facing
    strings for ambiguous cases the generator declines to auto-fix."""
    section_dir = index_dir / dir_key
    if section_dir.is_dir():
        target_stems = {p.stem for p in section_dir.glob("*.md")}
    else:
        target_stems = set()

    ti = find_table(body)
    if ti is not None:
        headers = split_row(body[ti])
        j = ti + 2
        while j < len(body) and body[j].strip().startswith("|"):
            j += 1
        existing_rows = []
        for k in range(ti + 2, j):
            cells = split_row(body[k])
            if cells:
                eid, linked = edit_id_of(cells[0])
                existing_rows.append((eid, linked, cells))
        if not target_stems:
            # Table over an empty directory: the courtesy view collapses to `(none)`.
            replacement = ["(none)"]
        else:
            rows = regenerate_table_body(dir_key, headers, existing_rows, target_stems,
                                         section_dir, salvage, is_link)
            replacement = [body[ti], body[ti + 1]] + rows
        return body[:ti] + replacement + body[j:]

    # No table in the section.
    if not target_stems:
        return body  # empty dir, no table: authored `(none)`/prose stays put.

    # Non-empty dir, no table: only a bare `(none)` placeholder is safe to replace.
    for idx, line in enumerate(body):
        if line.strip() and not line.strip().startswith(">"):
            if _PLACEHOLDER_RE.match(line.strip()):
                headers = DEFAULT_HEADERS[dir_key]
                rows = regenerate_table_body(dir_key, headers, [], target_stems,
                                             section_dir, salvage, is_link)
                table = [build_row(headers), build_separator(len(headers))] + rows
                return body[:idx] + table + body[idx + 1:]
            break
    # Authored prose (not a bare placeholder) with files present: a human decision the
    # generator will not overwrite. Surface it, don't guess.
    notes.append(
        f"{dir_key}: {len(target_stems)} file(s) present but the section is authored "
        f"prose, not a table or `(none)` placeholder — left untouched (needs a human).")
    return body


# --------------------------------------------------------------------------- file rewrite


def split_sections(text):
    """(preamble_lines, [ (dir_key|None, header_line, body_lines) ]). Body lines are
    everything between one `## ` header and the next, blanks and all, so an unchanged
    file reassembles byte-for-byte."""
    lines = text.split("\n")
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not heads:
        return lines, []
    preamble = lines[: heads[0]]
    sections = []
    for n, h in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        header_line = lines[h]
        body = lines[h + 1: end]
        m = _SECTION_RE.match(header_line)
        key = SECTION_DIRS.get(m.group(1).lower()) if m else None
        sections.append([key, header_line, body])
    return preamble, sections


def regenerate(text, index_dir):
    """Return (new_text, notes) for one index file."""
    salvage, is_link = build_salvage(text)
    preamble, sections = split_sections(text)
    notes = []

    present_keys = {s[0] for s in sections if s[0]}
    for s in sections:
        if s[0]:
            s[2] = rewrite_section(s[0], s[2], index_dir, salvage, is_link, notes)

    # Create a section for any non-empty directory that no header maps to.
    for dir_key in CANON_ORDER:
        if dir_key in present_keys:
            continue
        d = index_dir / dir_key
        if not (d.is_dir() and any(d.glob("*.md"))):
            continue
        headers = DEFAULT_HEADERS[dir_key]
        rows = regenerate_table_body(dir_key, headers, [], {p.stem for p in d.glob("*.md")},
                                     d, salvage, is_link)
        body = ["", build_row(headers), build_separator(len(headers))] + rows + [""]
        new_section = [dir_key, f"## {dir_key.capitalize()}", body]
        pos = _insertion_point(sections, dir_key)
        sections.insert(pos, new_section)

    out = list(preamble)
    for _key, header_line, body in sections:
        out.append(header_line)
        out.extend(body)
    return "\n".join(out), notes


def _insertion_point(sections, dir_key):
    """Insert before the first section that sorts after `dir_key` canonically, or before
    the first unmapped (e.g. `## Notes`) section, else at the end."""
    rank = CANON_ORDER.index(dir_key)
    for i, (key, _h, _b) in enumerate(sections):
        if key is None:
            return i
        if CANON_ORDER.index(key) > rank:
            return i
    return len(sections)


# --------------------------------------------------------------------------- discovery / CLI


def discover_indexes(paths):
    """Resolve CLI args to a sorted list of existing index.md files."""
    found = set()
    for raw in paths:
        p = pathlib.Path(raw)
        if p.is_file() and p.name == "index.md":
            found.add(p.resolve())
        elif p.is_dir():
            for idx in p.glob("*/index.md"):
                found.add(idx.resolve())
            direct = p / "index.md"
            if direct.is_file():
                found.add(direct.resolve())
    return sorted(found)


def process(index_path, check):
    """(changed, notes, problems). In write mode a changed file is rewritten; in check
    mode it is only reported.

    `problems` are conditions the generator will NOT paper over — a brief in two states,
    a state directory whose name it does not know. They are properties of the directory
    rather than of the rewrite, so they are found here and not inside `regenerate()`,
    which stays a pure text transform."""
    index_dir = index_path.parent
    original = index_path.read_text()
    new_text, notes = regenerate(original, index_dir)
    changed = new_text != original
    if changed and not check:
        index_path.write_text(new_text)

    problems = []
    for eid, dirs in find_cross_state_duplicates(index_dir).items():
        problems.append(f"{eid} exists in {len(dirs)} states at once ({', '.join(dirs)}) "
                        f"— the directory contradicts itself; resolve by hand")
    for name in unknown_state_dirs(index_dir):
        problems.append(f"`{name}/` holds briefs but is not a known state — it is absent "
                        f"from this index, not merely unsorted; teach SECTION_DIRS or rename it")
    return changed, notes, problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Regenerate receipt-ritual index.md tables (ADR-0030 §3).")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any index is stale (for a hook / CI)")
    ap.add_argument("--status", action="store_true",
                    help="regenerate, but print ONLY when something happened (a rewrite "
                         "or a PROBLEM) and always exit 0 — the startup-hook form")
    ap.add_argument("paths", nargs="*", help="index.md files, architect dirs, or the "
                    "proposed-edits root (default: the federation's proposed-edits/)")
    args = ap.parse_args(argv)

    default_root = pathlib.Path(__file__).resolve().parent.parent / "proposed-edits"
    targets = discover_indexes(args.paths or [str(default_root)])

    # Zero targets is NOT a pass. The natural place to wire this check is a merge gate,
    # and a gate runs against a scratch worktree of the candidate commit — where
    # `proposed-edits/` does not exist at all, because it is gitignored data rather than
    # tracked system files. Reporting "OK — all 0 index(es) match" there is a green
    # certificate over nothing: the check cannot fail, so it certifies the gap instead of
    # detecting it (ship-the-detector-with-the-capability). "I could not check" gets its
    # own answer and its own exit code, distinct from both pass and fail.
    if not targets:
        where = ", ".join(args.paths) if args.paths else str(default_root)
        print(f"Indexes: UNKNOWN — no index.md found under {where}; NOTHING WAS CHECKED. "
              f"This is not a pass: point it at a tree where the (gitignored) "
              f"proposed-edits/ directory actually exists.", file=sys.stderr)
        return 0 if args.status else 2

    try:
        stale, all_notes, all_problems = [], [], []
        for idx in targets:
            changed, notes, problems = process(idx, args.check)
            rel = idx.parent.name + "/index.md"
            if changed:
                stale.append(rel)
            for n in notes:
                all_notes.append(f"{rel}: {n}")
            for p in problems:
                all_problems.append(f"{rel}: {p}")

        if args.check:
            if stale:
                print("STALE — these indexes do not match their directory layout:")
                for rel in stale:
                    print(f"  {rel}")
            elif not all_problems:
                print(f"OK — all {len(targets)} index(es) match their directory layout.")
            for p in all_problems:
                print(f"PROBLEM {p}")
            for n in all_notes:
                print(f"NOTE {n}", file=sys.stderr)
            return 1 if (stale or all_problems) else 0

        # `--status` is the startup-hook form: it still REGENERATES (the whole point is
        # that nobody has to remember), but says nothing on a quiet pass, matching the
        # sibling hooks. Silence therefore means "ran, nothing to say" — and because the
        # regeneration already happened, it is not the silence of a check that was never
        # able to run.
        if stale:
            if args.status:
                print(f"Indexes: regenerated {len(stale)} stale index(es) — "
                      f"{', '.join(stale)}")
            else:
                print(f"Regenerated {len(stale)} of {len(targets)} index(es):")
                for rel in stale:
                    print(f"  {rel}")
        elif not args.status:
            print(f"No change — all {len(targets)} index(es) already current.")
        # A problem is reported in write mode but never made fatal: this runs on the
        # startup path, and a contradiction in the mailbox must surface without taking
        # the session down with it (fail-open, as the module docstring promises).
        for p in all_problems:
            print(f"PROBLEM {p}")
        for n in all_notes:
            print(f"NOTE {n}", file=sys.stderr)
        return 0
    except Exception:  # fail open — never brick a session-end hook
        import traceback
        traceback.print_exc()
        print("gen_indexes: errored; regenerated nothing.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
