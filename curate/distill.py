#!/usr/bin/env python3
"""Distillation generator — registry -> Architect-facing canon digest.

Per ADR-0022. Reads the federation registries (the fat canon) and emits the lean,
one-line-per-item manifest that Architects ingest. The manifest is GENERATED, never
hand-authored, so the Architect-facing tier cannot drift from canon — which is the
whole failure this fixes (the hand-maintained role-doc §4 gloss drifted from the
registry; see ADR-0022 Context).

Deterministic, no LLM (P15). It does NOT judge — it transcribes the one-line
**Statement.** of every Accepted entry. Clustering, classification, and the
statements themselves are produced by the human-gated curation that writes the
registry; this only projects that canon into the lean view.

Scope of the manifest (ADR-0022): the universal set — all Accepted principles +
all Accepted universal habits. Proposed/Deprecated entries are excluded. Situation-
specific habits never live in the registry, so they are excluded by construction.
Class-bound habits (ADR-0042) DO live in the registry but declare `Binds-to: <class>`
(e.g. cloud-deployed); they are excluded here because the digest is the *universal*
set. An absent `Binds-to` (or `all`) is the universal default and is included.

Usage:
  python3 curate/distill.py            # regenerate CANON.md
  python3 curate/distill.py --check    # exit 1 if CANON.md is stale vs canon (no write)
  python3 curate/distill.py --stdout   # print the manifest, write nothing
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRINCIPLES = ROOT / "principles" / "master.md"
HABITS = ROOT / "habits" / "master.md"
OUT = ROOT / "CANON.md"
# The kit ships a snapshot of the same digest to every fresh Architect (ADR-0022 code
# channel). Writing it here, from the same canon, keeps the kit copy from drifting — the
# whole point of generating instead of hand-maintaining. Same body, Architect-facing header.
KIT_OUT = ROOT / "bootstrap-kit" / "CANON.md"

# Principle entry header: '## P15 — code-for-mechanism-not-judgment'
P_HEADER = re.compile(r"^## (P(\d+)) — (.+)$")
# Habit entry header: '## conventional-commits' (a bare kebab slug, not a doc section)
H_HEADER = re.compile(r"^## ([a-z0-9][a-z0-9-]+)$")
# Field lines inside an entry.
STATUS_RE = re.compile(r"^- \*\*Status:\*\* *(.+?) *$")
PARENT_RE = re.compile(r"^- \*\*Parent:\*\* *\[(P\d+)")
BINDS_TO_RE = re.compile(r"^- \*\*Binds-to:\*\* *(.+?) *$")
STATEMENT_RE = re.compile(r"^\*\*Statement\.\*\* *(.+?) *$")

# '## ' headers in the registries that are sections, not entries.
NON_ENTRY = {"Status legend"}


def _blocks(text: str):
    """Yield (header_line, [body_lines]) for each '## ...' section in order."""
    lines = text.splitlines()
    idxs = [i for i, l in enumerate(lines) if l.startswith("## ")]
    for k, i in enumerate(idxs):
        end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
        yield lines[i], lines[i + 1:end]


def _field(body: list[str], rx: re.Pattern) -> str | None:
    for l in body:
        m = rx.match(l)
        if m:
            return m.group(1)
    return None


def parse_principles(text: str) -> list[dict]:
    out = []
    for header, body in _blocks(text):
        m = P_HEADER.match(header)
        if not m:
            continue
        if _field(body, STATUS_RE) != "Accepted":
            continue
        statement = _field(body, STATEMENT_RE)
        if not statement:
            continue
        out.append({"id": m.group(1), "num": int(m.group(2)),
                    "slug": m.group(3), "statement": statement})
    out.sort(key=lambda p: p["num"])
    return out


def parse_habits(text: str) -> list[dict]:
    out = []
    for header, body in _blocks(text):
        m = H_HEADER.match(header)
        if not m or m.group(1) in {s.lower().replace(" ", "-") for s in NON_ENTRY}:
            continue
        # A real habit entry has a Status field; section headers like 'Status legend' don't.
        status = _field(body, STATUS_RE)
        if status != "Accepted":
            continue
        # Class-bound habits (ADR-0042) declare `Binds-to: <class>` and are NOT part of
        # the universal set — the digest is universal-only, so skip any non-`all` binding.
        # Absent binding (or `all`) is the universal default and is kept.
        binds_to = _field(body, BINDS_TO_RE)
        if binds_to and binds_to.strip().lower() != "all":
            continue
        statement = _field(body, STATEMENT_RE)
        if not statement:
            continue
        out.append({"slug": m.group(1),
                    "parent": _field(body, PARENT_RE) or "—",
                    "statement": statement})
    return out


# A Statement is lifted out of its registry (`principles/master.md`, `habits/master.md`)
# into a file that lives somewhere else, so a relative link in it was written against
# the REGISTRY's directory and means something different at the output. `../adr/X` in
# habits/ is `adr/X` at the root, and a bare `#slug` or `master.md#slug` points at the
# registry, not at the digest (whose lines are list items, not headings). The federation
# copy therefore re-bases every relative link onto the repo root. The kit copy ships into
# a member repo that has no registries and no `adr/`, so there is nothing a relative link
# could resolve to: it keeps the link text and drops the target.
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def relink(text: str, src_dir: str, audience: str = "federation") -> str:
    """Rewrite each relative markdown link in `text`, written from `src_dir` (a
    repo-root-relative directory), so it resolves from the digest's own location."""
    def fix(m: re.Match) -> str:
        label, target = m.group(1), m.group(2)
        if _SCHEME_RE.match(target):
            return m.group(0)
        if audience == "architect":
            return label
        path, sep, frag = target.partition("#")
        path = path or "master.md"          # a bare `#slug` is the registry's own anchor
        rebased = posixpath.normpath(posixpath.join(src_dir, path))
        return f"[{label}]({rebased}{sep}{frag})"
    return LINK_RE.sub(fix, text)


# Header preamble per audience. The body (principle/habit lines) is identical; only
# the framing differs. The federation copy documents its own generation mechanics
# (distill.py / --check / registries). The kit copy ships into Architect repos that
# have none of those, so its header drops the federation-relative links and mechanics
# and instead frames the digest as the inherited set the harness injects.
_HEADER_FEDERATION = [
    "> **GENERATED — do not edit by hand.** Produced from the federation registries",
    "> (`principles/master.md` + `habits/master.md`) by `curate/distill.py`, per",
    "> [ADR-0022](adr/0022-architect-ingest-distillation-and-code-channel.md). Regenerate",
    "> after any canon change; `--check` fails if this file is stale.",
    ">",
    "> This is the Architect-facing distillation: the **universal set** Architects",
    "> inherit (not negotiate), one line each. Full statements, reasoning, and",
    "> provenance live in the registries — the source of truth.",
]
_HEADER_ARCHITECT = [
    "> **GENERATED in the federation — do not edit by hand.** This is the **universal",
    "> set** you inherit (not negotiate): every Accepted principle and universal habit,",
    "> one line each (ADR-0008 / ADR-0022). The session harness (`session.py start`)",
    "> injects this file into your context at session start — you do not need to read it",
    "> manually. Updates ship from the federation; do not hand-edit (edits are overwritten",
    "> and drift from canon). Full statements, reasoning, and provenance live in the",
    "> federation registries — the source of truth.",
]


def render(principles: list[dict], habits: list[dict], audience: str = "federation") -> str:
    header = _HEADER_ARCHITECT if audience == "architect" else _HEADER_FEDERATION
    L = [
        "# Canon digest — principles & universal habits",
        "",
        *header,
        "",
        f"**{len(principles)} principles · {len(habits)} universal habits** (Accepted only).",
        "",
        "## Principles",
        "",
    ]
    L += [f"- **{p['id']} `{p['slug']}`** — {relink(p['statement'], 'principles', audience)}"
          for p in principles]
    L += ["", "## Universal habits", ""]
    L += [f"- **`{h['slug']}`** ({h['parent']}) — {relink(h['statement'], 'habits', audience)}"
          for h in habits]
    return "\n".join(L) + "\n"


def build(audience: str = "federation") -> str:
    return render(parse_principles(PRINCIPLES.read_text(encoding="utf-8")),
                  parse_habits(HABITS.read_text(encoding="utf-8")), audience)


# Each output target: (path, audience). Both derive from the same canon, so they
# stay in lockstep — regenerate writes both; --check verifies both.
TARGETS = [(OUT, "federation"), (KIT_OUT, "architect")]


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Architect-facing canon digest.")
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if CANON.md (root or kit) is stale vs the registries; write nothing.")
    ap.add_argument("--stdout", action="store_true", help="Print the manifest; write nothing.")
    args = ap.parse_args()

    if args.stdout:
        sys.stdout.write(build("federation"))
        return
    if args.check:
        stale = []
        for path, audience in TARGETS:
            want = build(audience)
            have = path.read_text(encoding="utf-8") if path.exists() else ""
            if have != want:
                stale.append(path.relative_to(ROOT))
        if stale:
            names = ", ".join(str(s) for s in stale)
            print(f"STALE: {names} out of sync with canon — run `python3 curate/distill.py`.")
            sys.exit(1)
        print("CANON.md (root + kit) in sync with canon.")
        return

    for path, audience in TARGETS:
        manifest = build(audience)
        path.write_text(manifest, encoding="utf-8")
        p = manifest.count("\n- **P")
        print(f"wrote {path.relative_to(ROOT)} — {p} principles + "
              f"{manifest.count('- **`')} habits")


if __name__ == "__main__":
    main()
