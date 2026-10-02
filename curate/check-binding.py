#!/usr/bin/env python3
"""Binding-manifest validator — enforce the ADR-0041 contract-vs-binding schema.

ADR-0041 re-frames the substrate floor as a runtime-agnostic CONTRACT of five
behavioural obligations (C1–C5); how a runtime satisfies each is its BINDING,
declared in a `binding.md` at the target repo root. A Claude-Code system needs no
binding.md (its manifest is implicit/all-satisfied); a non-Claude system authors
one from `bootstrap-kit/binding-template.md`.

This is the structural guard on that schema (`add-structural-guard-on-recurrence`
/ P15): "no-subtraction" (ADR-0041 §3) survives only if every obligation carries a
real disposition and every waiver names a compensating control — a silent hole is
a subtraction. This check makes that machine-enforced, not a thing to remember.

Conformance rules:
  - The `**Runtime:**` line is present and filled (not the `<...>` placeholder).
  - All five obligations are present, exactly once, keyed verbatim:
    canon-delivery, state-continuity, operational-guards, auditable-evolution,
    identity-provenance.
  - Each disposition is one of: satisfied | satisfied-differently |
    waived-with-compensation.
  - Every `waived-with-compensation` row has a non-empty Mechanism/Compensation
    cell (not blank, not a `<...>` placeholder, not a bare dash / TODO).

Federation-only.

Usage:
    python3 curate/check-binding.py <binding.md> [<binding.md> ...]

Exits 0 if all manifests conform; 1 and prints each problem if any do not;
2 on a usage error or unreadable file.
"""

import sys

# Canonical obligation keys (ADR-0041 §1), in contract order. The validator
# anchors on these substrings appearing in a table row's first cell.
OBLIGATIONS = [
    "canon-delivery",
    "state-continuity",
    "operational-guards",
    "auditable-evolution",
    "identity-provenance",
]

VALID_DISPOSITIONS = {
    "satisfied",
    "satisfied-differently",
    "waived-with-compensation",
}

# Cell contents that count as "not really filled in".
_EMPTY_CELL = {"", "-", "—", "--", "n/a", "na", "tbd", "todo"}


def _is_placeholder(text):
    """A `<...>` template placeholder left unfilled."""
    return "<" in text and ">" in text


def _cells(line):
    """Split a markdown table row into trimmed cells, dropping the empty
    leading/trailing fields the outer pipes produce. Returns [] for a non-row."""
    if "|" not in line:
        return []
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts


def _declared_runtimes(lines):
    """Parse the manifest's declared runtime(s).

    Returns (mode, runtimes, problem):
      - mode 'multi'  when a `**Runtimes:**` (plural, comma-separated) header is present
        — the symmetric multi-runtime matrix; runtimes is the declared list.
      - mode 'single' when only a `**Runtime:**` (singular) header is present — the
        legacy ADR-0041 one-row-per-obligation form; runtimes is a one-element list.
      - mode None when neither is present.
    problem is a diagnostic string when the header is missing / empty / a placeholder,
    else None. Runtime names are lower-cased for comparison."""
    for line in lines:
        low = line.lower().lstrip()
        if low.startswith("**runtimes:**") or low.startswith("runtimes:"):
            value = line.split(":", 1)[1].replace("*", "").strip()
            if not value or _is_placeholder(value):
                return "multi", [], "**Runtimes:** line is empty or still a <placeholder>"
            rts = [r.strip().lower() for r in value.split(",") if r.strip()]
            return "multi", rts, (None if rts else "**Runtimes:** line lists no runtimes")
    for line in lines:
        low = line.lower().lstrip()
        if low.startswith("**runtime:**") or low.startswith("runtime:"):
            value = line.split(":", 1)[1].replace("*", "").strip()
            if not value or _is_placeholder(value):
                return "single", [], "**Runtime:** line is empty or still a <placeholder>"
            return "single", [value.lower()], None
    return None, [], "missing **Runtime:** / **Runtimes:** line"


def _check_disposition(label, disp_cell, comp, problems):
    """Validate one (obligation[, runtime]) row's disposition + compensation cell,
    appending any problem under `label` (which carries the runtime tag in matrix mode)."""
    disp = disp_cell.strip().lower()
    if _is_placeholder(disp_cell) or disp in _EMPTY_CELL:
        problems.append(f"{label}: disposition not filled in")
    elif disp not in VALID_DISPOSITIONS:
        problems.append(
            f"{label}: invalid disposition '{disp_cell.strip()}' "
            f"(must be one of {', '.join(sorted(VALID_DISPOSITIONS))})")
    elif disp == "waived-with-compensation":
        if _is_placeholder(comp) or comp.lower() in _EMPTY_CELL:
            problems.append(
                f"{label}: waived-with-compensation but no compensating "
                f"control named (a waiver with no compensation is a "
                f"subtraction — ADR-0041 §3)")


def check_file(path):
    """Return a list of problem strings for one manifest ([] = conformant).

    Single-runtime form (ADR-0041): `**Runtime:**` header + a 3-column table
    (| obligation | disposition | compensation |), each obligation once.
    Multi-runtime matrix form: `**Runtimes:**` header (comma list) + a
    4-column table (| obligation | runtime | disposition | compensation |), each
    obligation once PER declared runtime."""
    problems = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return [f"could not read: {e}"]

    mode, runtimes, rt_problem = _declared_runtimes(lines)
    if rt_problem:
        problems.append(rt_problem)

    # Collect obligation rows: first cell contains a canonical key.
    seen = {}  # single: key -> count ; multi: (key, runtime) -> count
    for line in lines:
        cells = _cells(line)
        if len(cells) < 3:
            continue
        first = cells[0].lower()
        matched = None
        for key in OBLIGATIONS:
            if key in first:
                matched = key
                break
        if matched is None:
            continue

        if mode == "multi":
            if len(cells) < 4:
                problems.append(
                    f"{matched}: matrix row needs 4 columns "
                    f"(| obligation | runtime | disposition | compensation |)")
                continue
            row_rt = cells[1].strip().lower()
            if row_rt not in runtimes:
                problems.append(
                    f"{matched}: runtime '{cells[1].strip()}' is not a declared "
                    f"runtime ({', '.join(runtimes) or 'none'})")
            else:
                seen[(matched, row_rt)] = seen.get((matched, row_rt), 0) + 1
            _check_disposition(f"{matched} [{row_rt}]", cells[2], cells[3].strip(), problems)
        else:  # single (or undetermined runtime — validate as single)
            seen[matched] = seen.get(matched, 0) + 1
            _check_disposition(matched, cells[1], cells[2].strip(), problems)

    if mode == "multi":
        for key in OBLIGATIONS:
            for rt in runtimes:
                n = seen.get((key, rt), 0)
                if n == 0:
                    problems.append(f"{key} [{rt}]: obligation row missing for this runtime")
                elif n > 1:
                    problems.append(f"{key} [{rt}]: obligation appears {n} times (expected once)")
    else:
        for key in OBLIGATIONS:
            n = seen.get(key, 0)
            if n == 0:
                problems.append(f"{key}: obligation row missing")
            elif n > 1:
                problems.append(f"{key}: obligation appears {n} times (expected once)")

    return problems


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        print("ERROR: pass at least one binding.md path.", file=sys.stderr)
        return 2

    any_bad = False
    any_unreadable = False
    for path in paths:
        problems = check_file(path)
        if problems:
            if any(p.startswith("could not read") for p in problems):
                any_unreadable = True
            any_bad = True
            print(f"FAIL: {path}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"OK: {path}")

    if any_unreadable:
        return 2
    if any_bad:
        print("\nNon-conformant binding manifest(s). Every obligation needs a real "
              "disposition and every waiver a compensating control — see "
              "bootstrap-kit/binding-template.md and ADR-0041.")
        return 1
    print("\nOK — all binding manifests conform to ADR-0041.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
