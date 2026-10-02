#!/usr/bin/env python3
"""Brief delivery-integrity guard — scan a proposed-edit brief for federation-
internal path references a target cannot resolve.

A brief is applied from the *target's* point of view, in the target's own repo.
Two defect classes, both the same root cause — the brief assuming the
*federation's* own filesystem or document structure:

1. **Federation-internal paths.** "copy from `bootstrap-kit/CANON.md`" / "from the
   federation repo root" — the target goes hunting for files that exist only in the
   federation's filesystem and never finds them (the session-43 onboarding failure).
2. **Federation role-doc section numbers.** "point role-doc `§4` at CANON, `§11` at
   STANDARD" — those are *federation-arch's* section numbers; the target's role doc
   is numbered differently (the session-44 member blocker, concern 3). Reference sections
   by *role/name*, not by a federation section number. ADR section citations
   (`ADR-0024 §4`) are fine — they cite a shared federation doc, not the target's
   structure — and are excluded.

Self-contained briefs reference only target-resolvable paths (the target's own repo,
its own inbox / this brief's `payload/` folder) and name sections by role, embedding
literal content where needed.

This is the `add-structural-guard-on-recurrence` pattern (P15): each defect class
recurred across multiple delivered briefs, so the check becomes a tool you run
before delivering any brief, not a thing to remember.

Federation-only.

Usage:
    python3 curate/check-brief.py <brief.md> [<brief.md> ...]

Exits 0 ("OK") if all clean; exits 1 and prints each offending file+line if any
forbidden reference is found.
"""

import pathlib
import re
import sys

# WI-0401: the conditional-goal rule has ONE implementation and it is the shipped
# harness's, imported here through the same door `curate/metrics.py` uses. A rule
# re-stated in this file would drift from the one `poga dispatch` and `session.py
# goal-check` apply by the second edit, and the whole argument for refusing a goal at
# authoring time is that every authoring surface refuses the same shape.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session as _session  # noqa: E402

# A line that DECLARES a goal for the session the brief opens, as opposed to prose that
# happens to say "goal". Line-anchored and uppercase-insensitive, the same declaration-
# not-mention discipline `WI_ACCEPTANCE_RE` applies: the word "goal" appears in ordinary
# brief prose, and matching that would refuse nearly everything for saying it.
#
# `Prompted by:` WAS IN THIS PATTERN AND WAS TAKEN OUT, on the evidence. It is the field
# consultant briefs use to say what prompted them, and across 1,474 markdown files every
# one of them fills it with the operator's own sentences, quoted. That is provenance, not a goal:
# a guard that refuses it is refusing the record of what he said, and would have fired on
# a 2026-07-24 commission brief for quoting the request that prompted it.
# The rule applies to a goal a brief AUTHORS, which is what the author can still rewrite.
_GOAL_LINE = re.compile(
    r"^[^\S\r\n]*(?:[-*][^\S\r\n]+)?(?:\*\*)?(goal)(?:\*\*)?[^\S\r\n]*:",
    re.IGNORECASE)

# Substrings that name a federation-internal path a target cannot resolve.
# Case-insensitive match.
FORBIDDEN = [
    "bootstrap-kit/",
    "federation repo root",
    "from the federation repo",
    "the federation repo root",
]

# A markdown inline link [text](url) — collapsed to its text so a §-citation that
# sits *after* a linked ADR (e.g. "[ADR-0024](…/0024-….md) §4") is recognized as
# ADR-attached and not mis-flagged as a target role-doc reference.
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# An ADR section citation — legitimate (cites a shared federation doc). Stripped
# before the bare-§ scan so only target-role-doc section numbers survive.
_ADR_SECTION = re.compile(r"adr-\d{3,4}\s*§\s*\d+", re.IGNORECASE)
# A citation into a NAMED SPEC — e.g. "the example-app spec §5.1", "your spec §11". Also
# legitimate, and for the same reason as an ADR citation: the § is qualified by a
# document the target can resolve (usually the target's OWN spec, quoted back at
# it). Without this the rule fired on a member brief quoting that member's own spec
# sections, which the target can resolve better than we can — the check was
# assuming every "§N" meant a federation role-doc section
# ([`declare-what-a-check-assumes`]). Sub-sections allowed: §5.1.
_SPEC_SECTION = re.compile(r"spec\s*§\s*\d+(?:\.\d+)*", re.IGNORECASE)
# A bare role-doc section reference (§ + number) — the forbidden form once the
# qualified citations above are removed.
_BARE_SECTION = re.compile(r"§\s*\d+")


def check_file(path):
    """Return a list of (lineno, line, hit) offences for one brief."""
    offences = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                low = line.lower()
                for needle in FORBIDDEN:
                    if needle.lower() in low:
                        offences.append((i, line.rstrip("\n"), needle))
                # Bare §-section references to the *target's* role doc. Collapse
                # markdown links to their text, drop ADR section citations, and any
                # surviving "§N" is a federation-structure reference the target
                # cannot map — name the section by role instead.
                collapsed = _MD_LINK.sub(r"\1", line)
                qualified = _SPEC_SECTION.sub("", _ADR_SECTION.sub("", collapsed))
                if _BARE_SECTION.search(qualified):
                    offences.append((i, line.rstrip("\n"), "role-doc §-section ref"))
                # WI-0401. A goal line whose clause is gated on a condition the session
                # may never be able to settle. Refused HERE — before the brief is
                # delivered — because the cost otherwise lands on a session that has
                # already finished its work and cannot leave; on 2026-09-18 that was 34
                # turns after its journal was closed, with nobody attached to the pane.
                # Checked on the clause SHAPE, so "check if the gate is red" and "if X,
                # do Y" pass: a guard that fires on correct briefs gets turned off.
                goal_line = _GOAL_LINE.match(line)
                if goal_line:
                    text = line.split(":", 1)[1] if ":" in line else ""
                    for _idx, clause, marker in _session.goal_conditional_offences(text):
                        offences.append((i, clause, f"conditional goal clause: {marker}"))
    except OSError as e:
        offences.append((0, f"<could not read: {e}>", ""))
    return offences


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        print("ERROR: pass at least one brief path.", file=sys.stderr)
        return 2

    any_bad = False
    any_goal = False
    any_other = False
    for path in paths:
        offences = check_file(path)
        if offences:
            any_bad = True
            any_goal = any_goal or any(n.startswith("conditional goal clause")
                                       for _l, _t, n in offences)
            any_other = any_other or any(not n.startswith("conditional goal clause")
                                         for _l, _t, n in offences)
            print(f"FAIL: {path}")
            for lineno, line, needle in offences:
                tag = f" [{needle}]" if needle else ""
                print(f"  line {lineno}{tag}: {line.strip()}")
        else:
            print(f"OK: {path}")

    if any_bad:
        if any_goal:
            print("\nA GOAL LINE IS GATED ON A CONDITION THE SESSION MAY NEVER SETTLE. "
                  "A goal is the sentence a Stop hook re-reads every time the session "
                  "tries to stop, and a clause like this has no readable answer once the "
                  "thing it waits on is called off — not met, not unmet — so the session "
                  "cannot leave (WI-0401). Put the condition in the brief BODY, where it "
                  "informs the work without becoming a stop condition, and leave the goal "
                  "stating what DONE looks like.")
        if any_other:
            print("\nForbidden federation-internal reference(s) found — a path the target "
              "cannot resolve, or a federation role-doc §-section number the target's "
              "role doc does not share. Rewrite the brief to be self-contained: "
              "reference only the target's own repo / inbox (or this brief's payload/), "
              "and name sections by role, not by a federation section number "
              "(ADR-NNNN §N citations are fine).")
        return 1
    print("\nOK — all briefs are self-contained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
