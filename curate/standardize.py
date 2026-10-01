#!/usr/bin/env python3
"""Standard-section generator — federation source -> STANDARD.md + STANDARD-REFERENCE.md.

Per ADR-0024. The **standard section** of every role doc (the session rituals,
the inherited-canon pointer, the standard substrate, the handoff format, the
mid-session/emergency commands) is federation-owned and generated, NOT
hand-authored prose inside each Architect's own role-doc file. It is read +
injected into every session by `session.py start` (the ADR-0022 code channel,
alongside CANON.md). Because no Architect ever holds the standard section as
editable text, no Architect can drop a step from it — the stamp bug one member hit
(re-author the prose, lose a step) becomes structurally impossible (ADR-0024 §3).

This generator is the sibling of `curate/distill.py`: same dual-audience,
two-target, --check shape, but its source is a prose file (`standard-source.md`)
rather than the registries, and the transform is a passthrough + audience header
(the standard body is identical for every Architect; only the framing differs).
The single source is the P16 win — the standard prose lives in exactly one file
and is delivered everywhere from it (`single-source-and-deliver`).

Deterministic, no LLM (P15). It does NOT author the standard section — a human
(the Federation Architect, sole author per ADR-0024 §1) writes `standard-source.md`;
this only frames + ships it to the two output locations and `--check`s staleness.

Federation-specific session steps (curator registry read, curate-review count,
registry sweep, self-promotion guard) are NOT standard — they live in the
federation's own custom section and run at the additive extension point the
standard protocols end with (ADR-0024 §4).

TWO TIERS (WI-0208 / ADR-0136). One source, two delivered files. A `##` section
whose next line is the marker `<!-- tier: reference -->` is emitted into
STANDARD-REFERENCE.md; every other section, and the preamble above the first heading,
is emitted into STANDARD.md. Both are delivered to every member by push-substrate;
only STANDARD.md is injected into a session by `session.py start`. The split exists
because the injected set is paid for at every session start by every Architect forever,
while lookup material only needs to be reachable. It is a delivery decision, not an
authority one — the reference tier is exactly as binding as the injected tier.

Usage:
  python3 curate/standardize.py            # regenerate both tiers (root + kit)
  python3 curate/standardize.py --check    # exit 1 if any copy is stale (no write)
  python3 curate/standardize.py --stdout   # print the injected federation copy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "standard-source.md"
OUT = ROOT / "STANDARD.md"
# The kit ships a snapshot to every fresh Architect (ADR-0024 / ADR-0022 code
# channel). Writing it here from the same source keeps the kit copy from drifting —
# the whole point of generating instead of hand-maintaining. Same body, Architect
# framing in the header.
KIT_OUT = ROOT / "bootstrap-kit" / "STANDARD.md"
REF_OUT = ROOT / "STANDARD-REFERENCE.md"
KIT_REF_OUT = ROOT / "bootstrap-kit" / "STANDARD-REFERENCE.md"

# The marker that moves a section out of the injected tier. It sits on the line
# directly under the section's `##` heading, and is stripped from the output.
TIER_MARKER = "<!-- tier: reference -->"

# Header preamble per audience. The body (the standard section) is identical; only
# the framing differs. The federation copy documents its own generation mechanics
# (standard-source.md / standardize.py / --check). The kit copy ships into Architect
# repos that have none of those, so its header frames the file as the inherited
# standard the harness injects, names the receipt ritual as the change channel, and
# notes that ADR references resolve in the federation repo.
_HEADER_FEDERATION = [
    "> **GENERATED — do not edit by hand.** Produced from `standard-source.md` by",
    "> `curate/standardize.py` per [ADR-0024](adr/0024-standard-role-doc-section-is-generated-and-injected.md);",
    "> `--check` fails if this file is stale. Edit the source, never this file.",
]
_HEADER_ARCHITECT = [
    "> **GENERATED in the federation — do not edit by hand.** The standard role-doc",
    "> section you inherit, identical for every Architect (ADR-0024) and injected into",
    "> your context by `session.py start`. To change a standard step, recommend it to",
    "> the Federation Architect through the receipt ritual (ADR-0013) — a hand-edit is",
    "> overwritten. ADR references resolve in the federation repo.",
]


def split_tiers(body: str) -> tuple:
    """Partition the source into (injected, reference) by the per-section marker.

    A `##` section runs to the next `##` (a `###` subsection belongs to its parent),
    so a marked section takes its subsections with it. Fenced blocks are skipped so a
    `##` inside an example is not read as a heading. The preamble above the first
    heading is always injected — it frames the file a member reads first.
    """
    lines = body.splitlines(keepends=True)
    starts, in_fence = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            starts.append(i)
    bounds = [(s, starts[n + 1] if n + 1 < len(starts) else len(lines))
              for n, s in enumerate(starts)]

    injected = list(lines[:starts[0]]) if starts else list(lines)
    reference = []
    for a, b in bounds:
        chunk = lines[a:b]
        marked = len(chunk) > 1 and chunk[1].strip() == TIER_MARKER
        if marked:
            rest = chunk[2:]
            # The marker occupied the blank line under the heading; put one back so
            # the rendered section is still well-formed markdown.
            if rest and rest[0].strip():
                rest = ["\n"] + rest
            reference.extend([chunk[0]] + rest)
        else:
            injected.extend(chunk)
    return "".join(injected), "".join(reference)


_HEADER_REFERENCE = [
    "> **GENERATED — do not edit by hand.** The reference tier of the standard section",
    "> (ADR-0136): material you consult when you deliberately use a named surface.",
    "> Delivered to every member and **exactly as binding as `STANDARD.md`**; it is",
    "> simply not injected into every session. Read it on demand. Edit the source.",
]


def render(body: str, audience: str = "federation", tier: str = "injected") -> str:
    if tier == "reference":
        title, header = "# Standard role-doc section — reference", _HEADER_REFERENCE
    else:
        title = "# Standard role-doc section"
        header = _HEADER_ARCHITECT if audience == "architect" else _HEADER_FEDERATION
    L = [title, "", *header, ""]
    return "\n".join(L) + "\n" + body.strip("\n") + "\n"


def build(audience: str = "federation", tier: str = "injected") -> str:
    injected, reference = split_tiers(SRC.read_text(encoding="utf-8"))
    return render(reference if tier == "reference" else injected, audience, tier)


# Each output target: (path, audience, tier). All four derive from the same source, so
# they stay in lockstep — regenerate writes all; --check verifies all.
TARGETS = [(OUT, "federation", "injected"),
           (KIT_OUT, "architect", "injected"),
           (REF_OUT, "federation", "reference"),
           (KIT_REF_OUT, "architect", "reference")]


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the injected standard role-doc section.")
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if any generated tier (root or kit) is stale vs the source; write nothing.")
    ap.add_argument("--stdout", action="store_true", help="Print the federation copy; write nothing.")
    args = ap.parse_args()

    if args.stdout:
        sys.stdout.write(build("federation"))
        return
    if args.check:
        body = SRC.read_text(encoding="utf-8")
        if "What's next" in body or "What’s next" in body:
            print("INVALID: obsolete journal section in standard-source.md; use State at close, Parked question, Notes filed.")
            sys.exit(1)
        stale = []
        for path, audience, tier in TARGETS:
            want = build(audience, tier)
            have = path.read_text(encoding="utf-8") if path.exists() else ""
            if have != want:
                stale.append(path.relative_to(ROOT))
        if stale:
            names = ", ".join(str(s) for s in stale)
            print(f"STALE: {names} out of sync with standard-source.md — run `python3 curate/standardize.py`.")
            sys.exit(1)
        print("STANDARD.md + STANDARD-REFERENCE.md (root + kit) in sync with standard-source.md.")
        return

    for path, audience, tier in TARGETS:
        out = build(audience, tier)
        path.write_text(out, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} — {out.count(chr(10)) + 1} lines")


if __name__ == "__main__":
    main()
