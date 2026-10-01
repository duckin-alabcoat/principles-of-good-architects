#!/usr/bin/env python3
"""Citation check — a `file.py:NNN` address in the record must still resolve.

WI-0342. On 2026-09-06 [ADR-0118](../adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md)
moved 25,620 lines of `session.py` into `sessionlib/` behind a 149-line entry point.
The quoted code all survived; only the addresses died. Five days later **119 of the
119** `session.py:NNNN` citations in the work-item store and the ADRs were wrong — 116
named a line past the end of the file, and the other three had drifted onto unrelated
comment lines, which is the worse half: a citation that is merely out of range announces
itself, and one that lands in range reads as verified.

THE COST WAS ALREADY BEING PAID, PER LANE. WI-0307's body carried the warning
"Re-verify these line numbers before building -- the ADR-0118 split moves them," so every
lane that opened one of these items re-derived by hand what one pass could settle once.
That is the shape this check exists to end: not the wrong number, but the standing
instruction to distrust every number.

WHY A CHECK AND NOT A GENERATOR. A citation is an author's claim about where something
is, made in prose, in a sentence that also says what it means. Nothing can generate that.
But the one part of it that is NOT a judgement — whether the address resolves — is exactly
the part that rots, and rots silently, on a refactor that touches no record at all. So
this asserts resolvability and leaves every word around it to the author.

WHY IT GATES RATHER THAN REPORTS. `wi-stale` already re-probes cited ADRs, cited item
ids, blockers and quoted counts, and deliberately only reports. That is right for a
*premise*, which can go stale for honest reasons an author must weigh. An address that
points past the end of a file is not a premise that aged — it is a fact that is now false,
with a mechanical answer and no judgement in it. It belongs where a false fact belongs,
which is in front of the commit that made it false
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).

Checks, each deriving its subjects from the corpus rather than a list kept here
([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)):

  D1  every citation whose file this repo has names a line that file actually has
      — GATES. Deterministic, and there is no judgement in it: the line is there or
      it is not.
  D2  where a citation names a symbol adjacent to it, and that symbol is unique in the
      cited file, the cited line falls inside that symbol's body
      — ADVISORY, behind `--symbols`. Does NOT gate.

D2 IS THE INTERESTING ONE AND IT DELIBERATELY DOES NOT GATE. It exists because the
three in-range-but-wrong citations above would all have passed D1 on the day they broke.
But it was measured before it was trusted, and it is wrong often enough to matter: on
this corpus its first cut fired 50 times, of which 29 were the check anchoring on the
SUBJECT of the sentence rather than on the cited code. In
"_auto_reap_lanes carries an age guard: lanes.py:2828 filters lane status", the address
belongs to the helper `_lane_is_dirty` that _auto_reap_lanes calls, and the citation is
correct. Tightening the anchor to immediate adjacency cut that to a handful, but a
handful of false failures in a fail-closed land gate is still a check that gets switched
off, and the residue is a mix of genuine old drift and prose this cannot parse. So D2
reports when asked and never blocks a land: a probe with judgement in it belongs beside
`wi-stale`, which reports, not beside `wi-check`, which refuses.

Four citation spellings are recognised, because the corpus uses all four and re-pointing
only some of them leaves one line naming two different files for the same code:

    sessionlib/land.py:1344        sessionlib/land.py:1344-1350
    sessionlib/land.py ~1608       :1610   (:1610)   (`:1610`)

The last is a continuation, inheriting the nearest preceding path on its line. It is also
where the false positives live: a bare `:349` after `tests/test_attention.py:301`
continues *that* file, and `csid[:8]` is a Python slice, so inheritance is resolved
against the nearest preceding path and slices are excluded outright.

Exit codes — three answers, never two
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)):

  0  checked, and every resolvable citation resolves
  1  checked, and found a citation that does not
  2  COULD NOT CHECK — a whole corpus directory is missing, so the sweep never ran over it

A citation naming a file this repo does not have (another repo, a deleted module, a
hypothetical) is NOT a finding and NOT an error: it is *unresolvable*, counted and listed
every run, so it can never quietly accumulate inside a number that reads as "checked."

Federation-only — `curate/` is not shipped to members.

CLI:
  python3 curate/check_citations.py            # report findings
  python3 curate/check_citations.py --check    # same; the spelling the land gate uses
  python3 curate/check_citations.py --status   # one-line signal for the SessionStart hook
  python3 curate/check_citations.py --list     # every citation and where it resolved
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The record directories this sweeps. Each must exist, or the answer is 2, not 0.
CORPUS_DIRS = ("work-items", "ops-items", "adr")

#: Bare module names appear in prose (`config.py:3332`). These are the directories a
#: bare name is tried against, in order, before the citation is called unresolvable.
BARE_NAME_DIRS = ("", "sessionlib", "curate", "tests", "deploy", "sessions/journal")

#: Extensions a citation may name. A citation to a non-source file is still an address.
CITED_SUFFIXES = ("py", "md", "json", "sh", "toml", "yaml", "yml")

_DASH = "[-–—]"

#: `path.py:12`, `path.py:12-20`, `path.py ~12` — an explicit address.
EXPLICIT_RE = re.compile(
    r"\b([A-Za-z0-9_./-]+\.(?:" + "|".join(CITED_SUFFIXES) + r"))"
    r"(?::(\d+)(?:" + _DASH + r"(\d+))?\b|\s*:?\s*~\s*(\d+)\b)")

#: `:12`, `(:12)`, `(`:12`)` — inherits the nearest preceding path on the same line.
#: The lookbehind is load-bearing: it keeps `csid[:8]` and `x.py:12` out.
BARE_RE = re.compile(r"(?<![A-Za-z0-9_./~\[-]):(\d+)(?:" + _DASH + r"(\d+))?\b")

#: A Python definition, for D2's symbol spans.
DEF_RE = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")

#: An anchoring symbol must sit IMMEDIATELY beside its citation — only backticks,
#: brackets and whitespace may intervene. Anything looser anchors on the subject of the
#: sentence instead of on the cited code, and those are routinely different: in
#: "_auto_reap_lanes carries an age guard: lanes.py:2828 filters lane status", the
#: address belongs to the helper `_lane_is_dirty` that _auto_reap_lanes calls. Measured
#: on this corpus, the loose rule was wrong 29 times out of 50.
ANCHOR_BEFORE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)`?\s*[(\[]?\s*`?$")
ANCHOR_AFTER_RE = re.compile(r"^`?[)\]]?[\s,]*`?([A-Za-z_][A-Za-z0-9_]*)")


class CannotCheck(Exception):
    """A corpus directory is absent, so nothing was swept. Never answer 0 on this path."""


# ---------------------------------------------------------------- the cited files

_lines_cache: dict[str, list[str] | None] = {}
_spans_cache: dict[str, dict[str, tuple[int, int]] | None] = {}


def reset_caches() -> None:
    """Drop the per-file caches.

    They are keyed by path RELATIVE to ROOT, so a second run against a different ROOT in
    the same process would otherwise answer from the first one's files — which is exactly
    what a test that builds two fixture trees does.
    """
    _lines_cache.clear()
    _spans_cache.clear()


def file_lines(rel: str) -> list[str] | None:
    """The cited file's lines, or None if this repo does not have it."""
    if rel not in _lines_cache:
        p = ROOT / rel
        try:
            _lines_cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            _lines_cache[rel] = None
    return _lines_cache[rel]


def resolve_path(cited: str) -> str | None:
    """Where a cited path actually lives, or None when this repo has no such file."""
    candidates = [cited]
    if "/" not in cited:
        candidates += [f"{d}/{cited}" for d in BARE_NAME_DIRS if d]
    for c in candidates:
        p = ROOT / c
        if p.is_file():
            return c
    return None


def symbol_spans(rel: str) -> dict[str, tuple[int, int]]:
    """Every def/class in a Python file mapped to its 1-based (first, last) line.

    A symbol defined more than once maps to nothing — D2 abstains rather than pick.
    """
    if rel in _spans_cache:
        return _spans_cache[rel] or {}
    lines = file_lines(rel)
    if lines is None or not rel.endswith(".py"):
        _spans_cache[rel] = None
        return {}
    opened: list[tuple[str, int, int]] = []          # name, start, indent
    spans: dict[str, list[tuple[int, int]]] = {}
    for i, line in enumerate(lines, 1):
        m = DEF_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        while opened and opened[-1][2] >= indent:
            name, start, _ind = opened.pop()
            spans.setdefault(name, []).append((start, i - 1))
        opened.append((m.group(3), i, indent))
    for name, start, _ind in opened:
        spans.setdefault(name, []).append((start, len(lines)))
    out = {n: v[0] for n, v in spans.items() if len(v) == 1}
    _spans_cache[rel] = out
    return out


# ---------------------------------------------------------------- the citations

def corpus_files() -> list[pathlib.Path]:
    """Every record file to sweep, derived from the directories rather than a list."""
    files: list[pathlib.Path] = []
    for d in CORPUS_DIRS:
        root = ROOT / d
        if not root.is_dir():
            raise CannotCheck(f"{d}/ is missing, so no citation in it was checked")
        files.extend(sorted(root.rglob("*.md")))
    return files


def citations_in(line: str) -> list[tuple[re.Match, str, int, int]]:
    """Every citation on one line, as (match, cited path, first line, last line)."""
    events = [(m.start(), m.end(), "explicit", m) for m in EXPLICIT_RE.finditer(line)]
    spans = [(s, e) for s, e, _k, _m in events]
    for m in BARE_RE.finditer(line):
        if not any(s <= m.start() < e for s, e in spans):
            events.append((m.start(), m.end(), "bare", m))
    events.sort()

    out: list[tuple[re.Match, str, int, int]] = []
    last_path: str | None = None
    for _s, _e, kind, m in events:
        if kind == "explicit":
            last_path = m.group(1)
            if m.group(4):                       # the `~approximate` spelling
                lo = hi = int(m.group(4))
            else:
                lo = int(m.group(2))
                hi = int(m.group(3)) if m.group(3) else lo
            out.append((m, last_path, lo, hi))
        else:
            if last_path is None:                # a bare ref with nothing to inherit
                continue
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else lo
            out.append((m, last_path, lo, hi))
    return out


def anchor_symbol(line: str, m: re.Match, rel: str) -> str | None:
    """The symbol a citation names, when it unambiguously names one.

    Nearest first, before the citation then after it; only a name this file defines
    exactly once counts, so prose words and repeated helpers never anchor anything.
    """
    spans = symbol_spans(rel)
    if not spans:
        return None
    names = []
    b = ANCHOR_BEFORE_RE.search(line[:m.start()])
    if b:
        names.append(b.group(1))
    a = ANCHOR_AFTER_RE.match(line[m.end():])
    if a:
        names.append(a.group(1))
    for n in names:
        if n in spans:
            return n
    return None


# ---------------------------------------------------------------- the sweep

def run(symbols: bool = False) -> tuple[list[str], list[str], list[str], int]:
    """Returns (problems, advisories, unresolvable, citations actually checked).

    `problems` are D1 — a line that is not there. `advisories` are D2, and are empty
    unless asked for, because they never gate.
    """
    reset_caches()
    problems: list[str] = []
    advisories: list[str] = []
    unresolvable: list[str] = []
    checked = 0

    for path in corpus_files():
        rel_record = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:                                   # pragma: no cover
            raise CannotCheck(f"{rel_record} could not be read ({exc})")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m, cited, lo, hi in citations_in(line):
                target = resolve_path(cited)
                if target is None:
                    unresolvable.append(
                        f"{rel_record}:{lineno} cites {m.group(0).strip()!r} — "
                        f"this repo has no {cited}")
                    continue
                lines = file_lines(target)
                if lines is None:                                # pragma: no cover
                    unresolvable.append(
                        f"{rel_record}:{lineno} cites {cited}, which could not be read")
                    continue
                checked += 1
                if hi > len(lines):
                    problems.append(
                        f"{rel_record}:{lineno} cites {m.group(0).strip()!r}, but "
                        f"{target} is {len(lines)} lines long.")
                    continue
                if not symbols:
                    continue
                sym = anchor_symbol(line, m, target)
                if sym:
                    first, last = symbol_spans(target)[sym]
                    if not (first <= lo <= last):
                        advisories.append(
                            f"{rel_record}:{lineno} cites {m.group(0).strip()!r} next to "
                            f"`{sym}`, but {sym} is {target}:{first}-{last}.")
    return problems, advisories, unresolvable, checked


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check that every file:line citation in the record still resolves.")
    ap.add_argument("--check", action="store_true",
                    help="report findings and exit non-zero on any (the land-gate spelling; the default)")
    ap.add_argument("--status", action="store_true",
                    help="one-line signal for the SessionStart hook; always exits 0")
    ap.add_argument("--list", action="store_true",
                    help="also print every unresolvable citation, not just the count")
    ap.add_argument("--symbols", action="store_true",
                    help="also run the advisory symbol-containment probe (D2); "
                         "reports only, never changes the exit code")
    args = ap.parse_args()

    try:
        problems, advisories, unresolvable, checked = run(symbols=args.symbols)
    except CannotCheck as exc:
        if args.status:
            print(f"Citations: COULD NOT CHECK — {exc}")
            return 0
        print(f"COULD NOT CHECK: {exc}", file=sys.stderr)
        print("\nNothing was verified. This is not a pass.", file=sys.stderr)
        return 2

    if args.status:
        if problems:
            print(f"Citations: {len(problems)} dead file:line citation(s) in the record "
                  f"— run `python3 curate/check_citations.py`.")
        return 0

    if unresolvable and (args.list or problems):
        # Never folded into the checked count: these were NOT verified.
        print(f"{len(unresolvable)} citation(s) name a file this repo does not have "
              f"— not checked:")
        for u in unresolvable:
            print(f"  - {u}")
        print()

    if advisories:
        # Advisory by construction: judgement-laden, and never the reason a land fails.
        print(f"{len(advisories)} advisory finding(s) — a citation may name the wrong "
              f"line inside the right file. Each needs reading; some are this probe "
              f"anchoring on the sentence's subject rather than on the cited code:\n")
        for a in advisories:
            print(f"  - {a}")
        print()

    if problems:
        print(f"{len(problems)} finding(s) — a citation no longer resolves:\n")
        for p in problems:
            print(f"  - {p}")
        print()
        print("Re-point by SYMBOL, not by arithmetic: find where the named code moved to "
              "and cite that, rather than adjusting the number by an offset.")
        return 1

    print(f"Citations resolve: {checked} file:line citation(s) checked across "
          f"{len(CORPUS_DIRS)} record director{'y' if len(CORPUS_DIRS) == 1 else 'ies'}, "
          f"every one in range; {len(unresolvable)} name a file this repo does not have "
          f"and were not checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
