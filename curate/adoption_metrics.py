"""The manual-adoption ledger — one JSONL line per MANUAL brief the federation handles.

`apply: auto` briefs are adopted by the engine (`session.py apply-briefs`), which already
leaves its own receipt. What had no record at all was the other path: an `apply: manual`
brief, checked at delivery by `curate/check-apply.py` and adopted by the R3 headless
runner `curate/adopt-runner.py`. Nobody could say how many manual briefs there are, why
they were manual, how long one takes, or how often it is retried, so whether a manual
category should become an engine op was a guess. This is the measurement.

Each line records:

  * `reason_category` — the `manual-reason:` folded to a small fixed vocabulary
    (`reason_category` below), with the raw text kept beside it;
  * `elapsed_s` — wall time of the step that wrote the line (the whole adoption, verify
    and filing or reset included, for the runner; the lint, for check-apply);
  * `retries` — earlier attempts at the SAME brief: the runner's consecutive-failure
    count from its dead-letter ledger; for check-apply, earlier check-apply lines for the
    same brief in this file;
  * `op_shape` — what kind of operations the brief contained (`op_shape` below).

Machine-local and append-only, beside the runner's own `adopt-runner.jsonl`: under
`.session-state/` in a checkout (gitignored, ADR-0036), or the production state root
when one is declared (WI-0361). Never a tracked file. Writing is best-effort — a ledger
that cannot be written must never fail a lint or an adoption.

Federation-only, like everything in curate/.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import production

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FED_ROOT))
from sessionlib.brief import OP_HEADING, frontmatter_block  # noqa: E402  (repo root)
LOG_NAME = "manual-adoptions.jsonl"
# Set by a caller driving FIXTURE briefs (`curate/deliver.py` under a test suite, which
# runs check-apply as a subprocess the suite cannot patch). A fixture's line in the real
# ledger would be fabricated data, so `record` writes nothing while it is set.
OFF_ENV = "POGA_ADOPTION_LEDGER_OFF"

# The engine's own op-heading grammar (`OP_HEADING`, sessionlib/brief.py), so a manual
# brief's shape is read in the same terms an auto brief is applied in.
_SECTION = re.compile(r"^##\s+\S", re.M)

# Ordered: the first matching rule wins. The vocabulary is the set PROCESS.md names
# (`attended`, `requires target-side judgment`, `multi-file migration` /
# `substrate install`) plus the two shapes the archive actually holds beyond those —
# briefs that inform rather than edit, and briefs whose reason fits none of these.
_CATEGORIES = (
    ("attended", re.compile(r"^attended\b")),
    ("substrate-install", re.compile(r"substrate|install")),
    ("migration", re.compile(r"migrat|multi-file|ingest")),
    ("judgment", re.compile(r"judg(e)?ment|decide|decision|yours")),
    ("informational", re.compile(r"informational|no role-doc edit|nothing here edits"
                                 r"|routing answer|finding")),
)


def log_path():
    """Where the ledger lives on this machine. Resolved per call, like the runner's
    other state, so a test can repoint it and production can relocate it."""
    return production.state_path(FED_ROOT, LOG_NAME,
                                  FED_ROOT / ".session-state" / LOG_NAME)


def reason_category(manual_reason):
    """Fold a free-text `manual-reason:` into one category. `unset` when empty."""
    text = (manual_reason or "").strip().lower()
    if not text:
        return "unset"
    for name, rule in _CATEGORIES:
        if rule.search(text):
            return name
    return "other"


def _body(text):
    """The brief body after its frontmatter (the whole text when there is none) — the
    shared brief reader's split (sessionlib/brief.py)."""
    return frontmatter_block(text)[1]


def op_shape(text):
    """The operations a brief contains, as data.

    `ops` counts each strict-schema op (`## op: replace` …); `prose_sections` counts the
    other `##` sections. `kind` is `ops` (only strict ops — a manual brief the engine
    could have taken), `prose` (none), or `mixed`."""
    body = _body(text or "")
    ops: dict[str, int] = {}
    for line in body.split("\n"):
        m = OP_HEADING.match(line)
        if m:
            name = m.group(1).lower()
            ops[name] = ops.get(name, 0) + 1
    op_count = sum(ops.values())
    prose_sections = len(_SECTION.findall(body)) - op_count
    kind = "prose" if not op_count else ("mixed" if prose_sections else "ops")
    return {"kind": kind, "ops": dict(sorted(ops.items())), "op_count": op_count,
            "prose_sections": prose_sections, "body_bytes": len(body.encode("utf-8"))}


def prior_attempts(source, brief, path=None):
    """Earlier lines from `source` for `brief` in the ledger. 0 when it is absent."""
    try:
        p = path or log_path()
        if not p.is_file():
            return 0
        n = 0
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("source") == source and rec.get("brief") == brief:
                    n += 1
        return n
    except Exception:
        return 0


def entry(*, source, stamp, brief, header, text, result, elapsed_s, retries, **extra):
    """One ledger line. Pure."""
    reason = (header or {}).get("manual-reason", "").strip()
    rec = {"stamp": stamp, "source": source, "brief": brief, "result": result,
           "reason_category": reason_category(reason), "manual_reason": reason,
           "elapsed_s": round(float(elapsed_s), 3), "retries": int(retries or 0),
           "op_shape": op_shape(text),
           "has_verify": bool((header or {}).get("verify", "").strip())}
    rec.update(extra)
    return rec


def record(rec, path=None):
    """Append one line. Best-effort: returns False rather than raising, and False
    without writing while `OFF_ENV` is set."""
    if os.environ.get(OFF_ENV):
        return False
    try:
        p = path or log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return True
    except Exception:
        return False
