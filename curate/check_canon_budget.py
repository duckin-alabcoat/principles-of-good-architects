#!/usr/bin/env python3
"""Canon-budget gate — a land that grows the injected set past its ceiling is refused.

WI-0208 R2 (operator, 2026-09-13), amending ADR-0052 §2, which made the budget SOFT on
purpose and rejected a hard block by name on the reasoning that a reported ceiling would
"create real back-pressure within a few sessions". Measured over the four weeks to
2026-09-13: sixteen first-parent size steps, fourteen UP and two DOWN, the two down-steps
totalling 264 bytes against 21,748 bytes of growth; the set went from 1,034 over to 19,928
over; and the budget was re-crossed three days after the consolidation pass that cleared
it. A number nobody is stopped by is a number that only ever goes one way.

WHAT IT REFUSES, and the two halves are both load-bearing:

    the set AFTER this change is OVER the ceiling   AND   this change GREW it

Over-and-grew is the only refusal. Everything else passes, and passes SILENTLY:

  * Under the ceiling — silent, whatever the change did. A guard that fires on a change
    made under the ceiling is the thing lanes route around and then delete, and operator
    named that failure explicitly. The ceiling is the subject; the diff is not.
  * Over, but this change SHRANK or did not touch the set — silent. This is the operator's
    "unless it carries its own consolidation" clause made mechanical: a land that
    reduces the set IS carrying its consolidation, and a land that never touched
    CANON.md or STANDARD.md did not add the bytes it would otherwise be blamed for.
    Without this half the guard freezes the whole repo the moment the set is in debt —
    including the consolidation pass that would clear it, which is the one land that
    must always be able to get through.

THE THIRD ANSWER. "I could not measure the set" is not "it fits" (exit 2, and the land
blocks on it like any other gate failure). The before-side is read only when the after-side
is already over, so an unresolvable trunk cannot block a land that fits.

Exit codes, matching `curate/check_citations.py` and `curate/check_substrate_docs.py`:
  0  the set fits, or this change did not grow it
  1  REFUSED — over the ceiling and grown by this change
  2  COULD NOT CHECK — not a pass

Usage:
  python3 curate/check_canon_budget.py --check     # the land gate's form
  python3 curate/check_canon_budget.py --status    # report only; always exit 0
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))

from metrics import CANON_BUDGET_CHARS, CANON_FILES  # noqa: E402  single source of the ceiling


class CannotCheck(Exception):
    """The measurement did not happen. Never reported as a pass."""


def _tree_size(root: pathlib.Path) -> int:
    """Bytes of the injected set in a working tree. Raises rather than guessing.

    `metrics.mine_canon_size` is fail-soft by design — an unreadable file contributes
    0 and records None — which is right for a report and wrong for a gate: a missing
    CANON.md would read as a very comfortable pass.
    """
    total = 0
    for name in CANON_FILES:
        p = root / name
        try:
            total += len(p.read_bytes())
        except OSError as exc:
            raise CannotCheck(f"{name} in {root} — {exc}")
    return total


def _trunk_size(root: pathlib.Path, ref: str) -> int:
    """Bytes of the injected set at a git ref. Never fetches — a fetch with no upstream
    blocks on DNS or a credential prompt rather than failing, and a hang inside a gate
    reads as a gate still running."""
    total = 0
    for name in CANON_FILES:
        r = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=str(root),
                           capture_output=True)
        if r.returncode != 0:
            raise CannotCheck(
                f"{name} at {ref} — {(r.stderr or b'').decode('utf-8', 'replace').strip()}")
        total += len(r.stdout)
    return total


def _trunk_ref(root: pathlib.Path) -> str:
    """The ref this change is landing onto. The gate runs in a detached worktree at the
    candidate tip, which already carries main, so main is exactly 'before this land'."""
    for ref in ("main", "master"):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref],
                           cwd=str(root), capture_output=True)
        if r.returncode == 0 and r.stdout.strip():
            return ref
    raise CannotCheck("no main or master to measure the trunk's set against")


def evaluate(root: pathlib.Path) -> dict:
    after = _tree_size(root)
    if after <= CANON_BUDGET_CHARS:
        return {"verdict": "fits", "after": after, "before": None,
                "budget": CANON_BUDGET_CHARS}
    ref = _trunk_ref(root)
    before = _trunk_size(root, ref)
    verdict = "grew" if after > before else "in-debt-but-not-grown"
    return {"verdict": verdict, "after": after, "before": before,
            "budget": CANON_BUDGET_CHARS, "ref": ref}


def _report(v: dict) -> str:
    files = " + ".join(CANON_FILES)
    if v["verdict"] == "fits":
        return (f"canon budget: {v['after']} of {v['budget']} bytes "
                f"({files}) — {v['budget'] - v['after']} under.")
    delta = v["after"] - v["before"]
    head = (f"canon budget: {v['after']} of {v['budget']} bytes ({files}) — "
            f"OVER by {v['after'] - v['budget']}.")
    if v["verdict"] == "grew":
        return (head + f"\nThis change ADDS {delta} bytes to a set already over its "
                f"ceiling ({v['before']} at {v['ref']}).\n"
                "A change that crosses the ceiling does not land unless it carries its "
                "own consolidation:\nname an offsetting cut in this same land, or move "
                "lookup material to the reference tier\n(mark the section "
                "`<!-- tier: reference -->` in standard-source.md). ADR-0052 / ADR-0137.")
    return (head + f"\nThis change does not grow it ({v['before']} at {v['ref']}), so it "
            "is not refused — but the set is\nin debt and the next land that adds to it "
            "will be.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse a land that grows the injected set past its ceiling.")
    ap.add_argument("--check", action="store_true", help="Gate form: exit 1 on a refusal, 2 if unmeasurable.")
    ap.add_argument("--status", action="store_true", help="Report only; always exit 0.")
    args = ap.parse_args()

    try:
        v = evaluate(ROOT)
    except CannotCheck as exc:
        if args.status:
            print(f"canon budget: COULD NOT CHECK — {exc}")
            return 0
        print(f"COULD NOT CHECK: {exc}", file=sys.stderr)
        print("\nThe injected set was not measured. This is not a pass.", file=sys.stderr)
        return 2

    text = _report(v)
    if v["verdict"] == "grew" and not args.status:
        print(text, file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
