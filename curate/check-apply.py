#!/usr/bin/env python3
"""Apply-mode delivery guard — enforce the ADR-0049 authoring default.

A redistribution brief is authored to the strict `Apply: auto` schema **by
default**; `Apply: manual` is the exception a brief must *earn*. This guard,
run before delivery (sibling of `curate/check-brief.py`), refuses a brief that:

1. **declares no recognized apply mode** — every brief must open with YAML
   frontmatter carrying `apply: auto` or `apply: manual`. A brief with no
   frontmatter, or an `apply:` value that is neither, fails. (Legacy prose briefs
   that predate ADR-0049 are not retrofitted — the guard binds new briefs, which
   are the only ones delivered forward.)
2. **is `apply: manual` with no reason** — a manual brief MUST carry a non-empty
   `manual-reason:` frontmatter field naming the genuine judgment/complexity the
   four strict ops can't express (e.g. `requires target-side judgment`,
   `multi-file migration`, `substrate install`, `attended`). Per ADR-0049 `manual`
   routes to the R3 headless-agent path, never to an attended session — the
   reason is the handoff to that runner. A parse gap ("the engine didn't read this
   member's format") is NOT a valid reason: fix the engine. A reason-less manual
   brief is indistinguishable from a brief that is manual out of authoring habit —
   exactly the default this policy removes.
3. **is `apply: manual` (agent path) with no `verify:`** — ADR-0050 makes the manual
   partition TOTAL: every manual brief declares its route in its own header. A brief
   whose `manual-reason` is exactly `attended` (case-insensitive) surfaces to operator
   (the reserved escape hatch). Put the explanation in `attended-because:`;
   prose after an initial `attended` token is refused to prevent accidental routing.
   Every other manual brief routes to the R3 headless runner, which refuses to adopt
   what it cannot verify — so it MUST carry a `verify:` command. A manual brief that
   is neither `attended` nor verifiable falls into an unowned gap; the guard refuses
   it at delivery.
4. **is `apply: manual` with a `verify:` that cannot report a verdict** (WI-0304) — a
   verify command owes its reader ONE LINE naming what was checked and what was
   absent, never a stack trace. A bare `assert` in a `python -c` one-liner can emit
   only `AssertionError`, naming nothing; that is provable from the text, so it is
   refused here rather than discovered on the fourteenth failed sweep. The runtime
   half of the same contract lives in `curate/adopt-runner.py`, which records an
   uncaught exception as a DEFECTIVE BRIEF rather than a failed adoption.

For `apply: auto` briefs the guard additionally checks the four required strict-
schema header fields are present (`edit-id`, `target-file`,
`expected-base-version`, `proposed-new-version`) so an auto brief that would
surface at the target for a missing header is caught here first. It does NOT
re-implement the engine: whether the ops actually apply is verified against the
target's live repo with `session.py apply-briefs --dry-run` (PROCESS.md), and the
target's own startup engine is the final arbiter.

This is the `add-structural-guard-on-recurrence` pattern (P15): habit-`manual`
recurred the moment the `apply-briefs` engine shipped (ADR-0039), so the reason
requirement becomes a tool you run, not a rule to remember.

Federation-only.

Usage:
    python3 curate/check-apply.py <brief.md> [<brief.md> ...]

Exits 0 ("OK") if all briefs declare a valid apply mode (auto with a complete
header, or manual with a reason); exits 1 and prints each offence otherwise.

Each `apply: manual` brief checked is also recorded, pass or refuse, as one line in the
machine-local manual-adoption ledger (`curate/adoption_metrics.py`): its reason
category, the check's elapsed time, how many earlier checks of the same brief the ledger
holds (`retries`), and its operation shape. This is the delivery half of that
measurement; `curate/adopt-runner.py` records the adoption half. The record is
best-effort and never changes the verdict or the exit code.
"""

import ast
import os
import pathlib
import shlex
import sys
import time
from datetime import datetime, timezone

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import adoption_metrics  # noqa: E402  (sibling in curate/)
except Exception:  # a ledger that cannot load must never stop the lint
    adoption_metrics = None

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sessionlib.brief import brief_header as _brief_header  # noqa: E402  (repo root)

AUTO_REQUIRED_HEADER = ("edit-id", "target-file", "expected-base-version",
                        "proposed-new-version")

# Interpreter tokens whose `-c` argument is Python source we can parse.
_PYTHON_TOKENS = ("python", "python3", "python3.9", "python3.10", "python3.11",
                  "python3.12", "python3.13", "python3.14")


def python_c_bodies(command):
    """Every `python -c <body>` source string in a shell command line.

    Returns `(bodies, parse_error)`. `parse_error` is set when the command line does not
    tokenize as shell at all (an unbalanced quote), which is itself worth refusing —
    and, per [`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes),
    must never be folded into the same answer as *tokenized fine, found no python*."""
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return [], str(e)
    bodies = []
    for i, tok in enumerate(tokens):
        if tok.rsplit("/", 1)[-1] not in _PYTHON_TOKENS:
            continue
        for j in range(i + 1, len(tokens)):
            if tokens[j] == "-c":
                if j + 1 < len(tokens):
                    bodies.append(tokens[j + 1])
                break
            if not tokens[j].startswith("-"):
                break                       # a script path, not a `-c` one-liner
    return bodies, None


def unmessaged_asserts(source):
    """Count of `assert` statements carrying no message, or None if `source` won't parse.

    None and 0 are different answers and the caller must keep them apart: a one-liner
    this cannot parse has NOT been cleared, it has been unexamined."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Assert) and n.msg is None)


def check_verify_contract(command):
    """Offences against the WI-0304 verify contract for one `verify:` command.

    **The contract**: a verify command that fails owes its reader a one-line human
    verdict naming what was checked and what was absent — never a stack trace. The R3
    runner (`curate/adopt-runner.py`) enforces the runtime half: an uncaught exception
    is recorded as a DEFECTIVE BRIEF rather than a failed adoption, and quarantined on
    sight. This is the half that can be enforced *before delivery*, which is the half
    that costs nobody a wasted sweep.

    What it refuses is narrow and exact — **a bare `assert` in a `python -c` one-liner**.
    A bare assert can only ever say `AssertionError` and name no condition, so it is a
    verify that cannot produce a verdict *by construction*, provable from the text
    without running anything. That was half of the failure this rule comes from: one
    member's enrollment brief's verify was a chain of four
    message-less asserts, and for fourteen consecutive runs the only thing anyone
    learned from it was a truncated traceback ending in a bare `KeyError`.

    What it deliberately does NOT attempt: proving a lookup can't raise. `m['mcpServers']
    ['widget']` — the shape of subscript that actually threw — cannot be shown safe statically,
    and a rule that guessed would either be noise or a false clearance. That case is the
    runner's to catch at runtime, where the answer is real."""
    offences = []
    bodies, parse_error = python_c_bodies(command)
    if parse_error:
        offences.append(
            f"`verify:` does not tokenize as a shell command line ({parse_error}) — the "
            f"runner passes it to `sh -c`, so it would fail on quoting before checking "
            f"anything. Fix the quoting.")
        return offences
    for body in bodies:
        n = unmessaged_asserts(body)
        if n is None:
            offences.append(
                "`verify:` contains a `python -c` one-liner whose body is not valid "
                "Python — it would die on a SyntaxError having checked nothing, which "
                "the R3 runner records as a DEFECTIVE BRIEF (WI-0304).")
        elif n:
            offences.append(
                f"`verify:` contains {n} bare `assert` statement(s) in a `python -c` "
                f"one-liner. A message-less assert can only report `AssertionError`, "
                f"naming neither what was checked nor what was absent — so this verify "
                f"cannot produce a verdict on failure, by construction (WI-0304). Give "
                f"every assert a message: `assert cond, \"widget MCP server not wired "
                f"into .mcp.json\"`.")
    return offences


def parse_frontmatter(text):
    """(header_dict, has_frontmatter). session.py's brief dialect, from the one shared
    reader (`sessionlib/brief.py`): a leading `---` block of `key: value` lines,
    terminated by a line starting `---`. Keys are lower-cased; a `#`-commented line is
    skipped."""
    return _brief_header(text)


def check_file(path):
    """Return a list of offence strings for one brief (empty = clean)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return [f"could not read: {e}"]

    header, has_fm = parse_frontmatter(text)
    if not has_fm:
        return ["no YAML frontmatter — every brief must declare `apply: auto` or "
                "`apply: manual` in a frontmatter header (ADR-0049)"]

    mode = header.get("apply", "").strip().lower()
    if mode == "auto":
        missing = [k for k in AUTO_REQUIRED_HEADER if not header.get(k)]
        if missing:
            return [f"apply: auto but header missing required field(s): {', '.join(missing)}"]
        return []
    if mode == "manual":
        reason = header.get("manual-reason", "").strip()
        if not reason:
            return ["apply: manual with no `manual-reason:` — a manual brief must state "
                    "the genuine judgment/complexity the strict ops can't express "
                    "(ADR-0049), e.g. `requires target-side judgment`, "
                    "`multi-file migration`, `attended`; it routes to the R3 agent path, "
                    "not to operator. A parse gap is not a valid reason — fix the engine."]
        if reason.lower().split()[0] == "attended" and reason.lower() != "attended":
            return ["manual-reason starts with `attended` but is not exactly `attended` — "
                    "this non-exact value would route the brief to the headless adopt-runner. "
                    "Set `manual-reason: attended` to route it to the operator's interactive review."]
        # ADR-0050: the manual partition must be TOTAL — every manual brief declares
        # its route in its own header. `attended` is the reserved escape hatch (surfaces
        # to operator). Every other manual brief takes the R3 agent path, and the headless
        # runner refuses to adopt a brief it cannot verify — so it MUST carry a `verify:`
        # command. A manual brief with neither falls into an unowned gap where no path
        # picks it up; that is a delivery error, caught here.
        if reason.lower() != "attended" and not header.get("verify", "").strip():
            return ["apply: manual (agent path) with no `verify:` — a manual brief that "
                    "routes to the R3 headless runner (ADR-0050) MUST declare a `verify:` "
                    "command the runner runs after adoption; a brief it cannot verify is "
                    "not runner-eligible and would land in an unowned gap. Either add "
                    "`verify: <command>`, or set `manual-reason: attended` to route it to "
                    "the operator's interactive review instead."]
        # WI-0304: declaring a `verify:` is not the same as declaring a verify that can
        # ANSWER. A command which, when it fails, can only emit a stack trace satisfied
        # every rule above and still left fourteen nights of failure undiagnosable.
        return check_verify_contract(header.get("verify", "").strip())
    if mode == "":
        return ["frontmatter present but no `apply:` field — declare `apply: auto` or "
                "`apply: manual` (ADR-0049)"]
    return [f"unrecognized apply mode: {mode!r} — must be `auto` or `manual` (ADR-0049)"]


def record_manual_check(path, offences, elapsed_s, *, log=None):
    """Append one manual-adoption ledger line for `path` if it is an `apply: manual`
    brief. Returns the record written, or None (not manual, unreadable, or no ledger).
    Never raises: the lint's verdict must not depend on its own bookkeeping."""
    if adoption_metrics is None:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        header, has_fm = parse_frontmatter(text)
        if not has_fm or header.get("apply", "").strip().lower() != "manual":
            return None
        brief = header.get("edit-id") or os.path.splitext(os.path.basename(path))[0]
        rec = adoption_metrics.entry(
            source="check-apply",
            stamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            brief=brief, header=header, text=text,
            result="refused" if offences else "ok", elapsed_s=elapsed_s,
            retries=adoption_metrics.prior_attempts("check-apply", brief, path=log),
            offences=len(offences))
        return rec if adoption_metrics.record(rec, path=log) else None
    except Exception:
        return None


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        print("ERROR: pass at least one brief path.", file=sys.stderr)
        return 2

    any_bad = False
    for path in paths:
        started = time.monotonic()
        offences = check_file(path)
        record_manual_check(path, offences, time.monotonic() - started)
        if offences:
            any_bad = True
            print(f"FAIL: {path}")
            for off in offences:
                print(f"  {off}")
        else:
            print(f"OK: {path}")

    if any_bad:
        print("\nApply-mode policy (ADR-0049): briefs default to `apply: auto` (strict "
              "schema, literal per-target anchors); `apply: manual` must carry a "
              "`manual-reason:` and, on the agent path, a `verify:` that can report a "
              "one-line verdict on failure (ADR-0050 §5a). Fix the header(s) above "
              "before delivering.")
        return 1
    print("\nOK — all briefs declare a valid apply mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
