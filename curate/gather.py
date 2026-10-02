#!/usr/bin/env python3
"""Curate-pass gather step — deterministic, no LLM.

Collects new/changed learnings entries from the producer files and writes a
REVIEW.md listing them verbatim for operator + the Federation Architect to review
together. This is the mechanical half of the curate pass (see
adr/0019-curate-gather-and-staging-boundary.md). It makes NO judgment: it does
not classify, cluster by meaning, score universality, or write canon. It finds
what is new since the last review and lays it out.

Producer files (the "suggestions"):
  - architect-learnings.md          (Federation Architect's own producer file)
  - inputs/*-learnings.md           (mirrored copies of other systems' producer files)

Entry delimiter: a line matching `## <YYYY-MM-DD>` — handles both on-disk
formats ('## 2026-05-30 — title' and '## 2026-05-26 · scope: tag · title').
Literal template lines ('## YYYY-MM-DD — ...') and section headers ('## Format')
do not match the date pattern and are skipped.

State: curate/seen.json records the id of every entry we've already reviewed, so
nothing is re-surfaced. An entry's id is a content hash — editing an entry
changes its id, so a materially revised lesson correctly re-surfaces.

Accept marks exactly what was reviewed: each gather writes an ids sidecar
(REVIEW-<stamp>.ids.json) next to the REVIEW, and `--accept` marks THAT set —
never "whatever is new at accept time". An entry landing between gather and
accept therefore stays unreviewed and surfaces on the next gather, instead of
being silently marked reviewed without ever appearing in a REVIEW file.

Usage:
  python3 curate/gather.py            # write a REVIEW (+ ids sidecar) for everything new; no state change
  python3 curate/gather.py --status   # print the count only; write nothing (used by the hook)
                                      # three outcomes: N awaiting, nothing new, or
                                      # no producer file readable at all (WI-0211)
  python3 curate/gather.py --accept   # mark the newest gathered REVIEW's entries as reviewed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import atomic_write, shared_work_root

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "curate" / "seen.json"
RUNS_DIR = ROOT / "curate-runs"

# Entry header: '## ' followed by an ISO date. Captures both producer formats.
ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\b(.*)$")


def producer_root() -> Path:
    """Where the producer files PHYSICALLY live — the main checkout's working tree.

    `architect-learnings.md` and `inputs/` are gitignored shared data (P3): they exist
    once, in the main checkout, and a `poga` lane sees them only through the symlinks
    `session.py`'s `_link_shared_data()` materialises at the lane's FIRST heartbeat
    (ADR-0055 lazy start). This script runs as a `SessionStart` hook, which fires
    before that — so `Path(__file__).parent.parent` named a lane whose producer files
    did not exist yet, `producer_files()` returned nothing, and the hook announced
    "nothing new to review" while dozens of entries were waiting (WI-0211). Resolving
    through the git COMMON dir removes the race rather than outrunning it — the same
    fix WI-0027 applied to `reconcile.py`, via the same helper.

    Deliberately NOT used for `seen.json` (tracked, so the lane's own copy is the one
    to read) or `curate-runs/` (ephemeral staging, correctly regenerated per lane).
    """
    return Path(shared_work_root(ROOT))


def _source_label(path: Path) -> str:
    """A producer file named relative to the tree it was read from, never absolute."""
    for base in (producer_root(), ROOT):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def producer_sources() -> tuple[dict[Path, str], list[str]]:
    """The producer files' text, and a reason for every expected source we could not read.

    THREE states, never two (`declare-what-a-check-assumes`). "N awaiting review" and
    "nothing new to review" are both answers about producer files we actually read; an
    EMPTY mapping means we read none of them, which is a different fact and the one a
    caller must never fold into the reassuring line — it is wrong in the direction that
    stops anyone looking.

    An absent `inputs/` is not a failure: members carry different gitignored sets and a
    federation with no upstream mirrors is the ordinary case. An `inputs/` that exists
    but cannot be listed is.
    """
    root = producer_root()
    texts: dict[Path, str] = {}
    unreadable: list[str] = []

    candidates: list[Path] = [root / "architect-learnings.md"]
    inputs = root / "inputs"
    if inputs.is_dir():
        try:
            candidates.extend(sorted(inputs.glob("*-learnings.md")))
        except OSError as e:
            unreadable.append(f"inputs/: {e.strerror or e}")
    elif inputs.exists() or inputs.is_symlink():
        unreadable.append("inputs/: present but not a readable directory")

    for c in candidates:
        try:
            texts[c] = c.read_text(encoding="utf-8")
        except OSError as e:
            unreadable.append(f"{c.name}: {e.strerror or e}")
    return texts, unreadable


def unreadable_diagnosis(unreadable: list[str]) -> str:
    """The third state, said in a sentence that cannot be mistaken for the second.

    Deliberately does NOT quote the reassuring line to contrast itself against it: a
    reader — or a grep — scanning this output for that phrase would find it here, and
    the whole defect is that phrase appearing where the queue was never counted."""
    detail = "; ".join(unreadable) if unreadable else "no producer file found"
    return (
        f"NO PRODUCER FILE WAS READABLE under {producer_root()} ({detail}) — so the "
        f"queue was never counted, and this line says NOTHING about how much is "
        f"waiting. Check that `architect-learnings.md` exists in the main checkout."
    )


def parse_entries(path: Path, text: str | None = None) -> list[dict]:
    """Split one producer file into entries on the date-header delimiter.

    `text` is the already-read content when the caller read it, so the readable/
    unreadable decision and the parse share one read and cannot disagree."""
    if text is None:
        text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    entries: list[dict] = []
    current: dict | None = None
    for lineno, line in enumerate(lines, start=1):
        m = ENTRY_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            title = m.group(2).strip(" —·-").strip()
            current = {
                "date": m.group(1),
                "title": title,
                "line": lineno,
                "body_lines": [line],
            }
        elif current is not None:
            current["body_lines"].append(line)
    if current is not None:
        entries.append(current)

    for e in entries:
        body = "\n".join(e.pop("body_lines")).strip()
        e["text"] = body
        e["id"] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        e["source"] = _source_label(path)
    return entries


def load_seen() -> dict:
    if not SEEN_PATH.exists():
        return {"reviewed": {}}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # Fail loud, not silent: treating a corrupt cursor as empty would
        # re-surface every reviewed entry; overwriting it would lose the review
        # history. It is tracked in git — restore it.
        sys.exit(
            f"curate/gather.py: {SEEN_PATH.relative_to(ROOT)} is unreadable "
            f"({e}). It is tracked in git — restore it "
            f"(`git restore curate/seen.json` or `git checkout -- curate/seen.json`) "
            f"before running the curate pass."
        )


def save_seen(seen: dict) -> None:
    atomic_write(
        SEEN_PATH, json.dumps(seen, indent=2, ensure_ascii=False) + "\n"
    )


def gather(texts: dict[Path, str] | None = None) -> list[dict]:
    """All entries across producer files not yet marked reviewed.

    Counts only what was actually read: an empty result means "nothing new" ONLY once
    the caller has checked that `producer_sources()` returned something to read. Use
    `pending_count()` to get the two apart in a single value."""
    if texts is None:
        texts, _ = producer_sources()
    seen = load_seen()["reviewed"]
    new: list[dict] = []
    for path, text in texts.items():
        for e in parse_entries(path, text):
            if e["id"] not in seen:
                new.append(e)
    return new


def pending_count() -> int | None:
    """Entries awaiting review, or None when NO producer file could be read.

    The counting half of the three states, for callers that report a number rather
    than a line (`curate/metrics.py`'s WI-0087 canon-debt signal, which already
    renders None differently from zero). Returning 0 there would print the reassuring
    number on the one surface built to make this debt visible."""
    texts, _ = producer_sources()
    if not texts:
        return None
    return len(gather(texts))


def write_review(new: list[dict], stamp: str) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"REVIEW-{stamp}.md"

    by_source: dict[str, list[dict]] = {}
    for e in new:
        by_source.setdefault(e["source"], []).append(e)

    lines: list[str] = []
    lines.append(f"# Curate review — {stamp}")
    lines.append("")
    lines.append(
        f"**{len(new)} new/changed entr{'y' if len(new) == 1 else 'ies'}** "
        f"across {len(by_source)} producer file(s), gathered by `curate/gather.py`."
    )
    lines.append("")
    lines.append(
        "This is a mechanical gather — no classification, no clustering, no scoring. "
        "operator and the Federation Architect review these together and decide which, "
        "if any, become principles or habits. Expected yield is low and that is "
        "correct: most entries are single-source and stay surfaced, not promoted. "
        "Run `python3 curate/gather.py --accept` once reviewed to advance the cursor."
    )
    lines.append("")
    for source in sorted(by_source):
        entries = by_source[source]
        lines.append(f"## {source} ({len(entries)})")
        lines.append("")
        for e in entries:
            lines.append(f"### {e['date']} — {e['title']}")
            lines.append(f"`{e['source']}:{e['line']}` · id `{e['id']}`")
            lines.append("")
            lines.append(e["text"])
            lines.append("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The ids sidecar is what --accept consumes: the exact set laid out for
    # review, so acceptance can never swallow an entry that arrived after this
    # gather. Ephemeral staging like the REVIEW itself (gitignored).
    sidecar = out.with_suffix(".ids.json")
    atomic_write(sidecar, json.dumps({
        "review": out.name,
        "ids": {
            e["id"]: {"source": e["source"], "date": e["date"], "title": e["title"]}
            for e in new
        },
    }, indent=2, ensure_ascii=False) + "\n")
    return out


def latest_sidecar() -> Path | None:
    """The newest gathered ids sidecar (stamps sort lexicographically)."""
    if not RUNS_DIR.is_dir():
        return None
    candidates = sorted(RUNS_DIR.glob("REVIEW-*.ids.json"))
    return candidates[-1] if candidates else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Gather new learnings for curate review.")
    ap.add_argument(
        "--status",
        action="store_true",
        help="Print the count of entries awaiting review; write nothing.",
    )
    ap.add_argument(
        "--accept",
        action="store_true",
        help="Mark the current new entries as reviewed (advance the cursor).",
    )
    args = ap.parse_args()

    texts, unreadable = producer_sources()
    new = gather(texts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")

    if args.status:
        if not texts:
            # Loud, but exit 0: this rides a SessionStart hook and must never break
            # the session it reports on (`add-structural-guard-on-recurrence` — nothing
            # on the other side of this guard is irreversible, so open is the safe
            # direction; the alarm is in the text, not the exit code).
            print(f"Curate: {unreadable_diagnosis(unreadable)}")
        elif new:
            print(
                f"Curate: {len(new)} producer entr{'y' if len(new) == 1 else 'ies'} "
                f"awaiting review (run `python3 curate/gather.py` to lay them out)."
            )
        else:
            print("Curate: nothing new to review.")
        return

    if args.accept:
        sidecar = latest_sidecar()
        if sidecar is None:
            sys.exit(
                "curate/gather.py --accept: no gathered REVIEW ids sidecar found in "
                "curate-runs/ — run `python3 curate/gather.py` first and review its "
                "output. Accept marks exactly the gathered set, never whatever "
                "happens to be new at accept time."
            )
        gathered = json.loads(sidecar.read_text(encoding="utf-8"))
        seen = load_seen()
        marked = 0
        for eid, meta in gathered["ids"].items():
            if eid in seen["reviewed"]:
                continue
            seen["reviewed"][eid] = {**meta, "reviewed_on": stamp}
            marked += 1
        save_seen(seen)
        print(f"Marked {marked} entr{'y' if marked == 1 else 'ies'} reviewed "
              f"(from {gathered['review']}).")
        if not texts:
            print(f"NOTE: {unreadable_diagnosis(unreadable)}")
            return
        remaining = len(gather(texts))
        if remaining:
            print(f"{remaining} entr{'y' if remaining == 1 else 'ies'} arrived after "
                  f"that gather and remain{'s' if remaining == 1 else ''} un-reviewed — "
                  f"run `python3 curate/gather.py` again.")
        return

    if not texts:
        # A hand-run curate pass against a root it cannot read must FAIL, not report
        # emptiness — the operator is about to conclude the queue is clear.
        sys.exit(f"curate/gather.py: {unreadable_diagnosis(unreadable)}")

    if not new:
        print("Nothing new to curate.")
        return

    out = write_review(new, stamp)
    print(f"{len(new)} new entr{'y' if len(new) == 1 else 'ies'} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
