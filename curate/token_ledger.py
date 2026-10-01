#!/usr/bin/env python3
"""Read-only token ledger for Claude Code and Codex transcripts.

Claude uses the last usage record per message.id. Codex uses the final cumulative
counter snapshot and distinct counter advances for response observations. Input
includes caches and output includes reasoning; neither component is added twice.

  python3 curate/token_ledger.py --status
  python3 curate/token_ledger.py --days 7 --project federation
  python3 curate/token_ledger.py --days 7 --json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_DIR = Path.home() / ".codex" / "sessions"
IDLE_GAP_S = 600          # a gap between turns longer than this is "waiting", not working
CTX_TARGET = 80_000       # finish-line target for the median context a turn carries
_PROJ_RE = re.compile(r"Projects-([A-Za-z0-9_.-]+?)(?:--claude|$)")
_FED_RE = re.compile(r"(principles-of-good-architects)")


def project_name(slug: str) -> str:
    """The member behind a transcript directory slug. A lane's slug carries the
    checkout it was cut from: `...-Projects-exampleapp--claude-worktrees-poga-1`."""
    m = _PROJ_RE.search(slug) or _FED_RE.search(slug)
    if m:
        return m.group(1)
    m = re.search(r"-([A-Za-z0-9_.-]+)--claude-worktrees", slug)
    return m.group(1) if m else slug


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _records(path: Path):
    """Skip malformed JSON lines, including a live transcript's unfinished tail."""
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                yield d


def _stamp(d: dict) -> float | None:
    try:
        return _ts(d["timestamp"])
    except (KeyError, ValueError, TypeError):
        return None


def _row(path, runtime, project, observations, totals, tools, users, warnings):
    ctxs = [o["input"] for o in observations]
    stamps = sorted(o["stamp"] for o in observations if o["stamp"] is not None)
    models = collections.Counter(o["model"] for o in observations)
    wall = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0
    return {
        "runtime": runtime, "project": project, "file": path.name,
        "responses": len(observations),
        "turns": len(observations),  # compatibility for existing scoreboard consumers
        "tool_calls": sum(tools.values()), "user_turns": len(users),
        "first_ctx": ctxs[0] if ctxs else 0,
        "ctx_median": int(statistics.median(ctxs)) if ctxs else 0,
        "ctx_p90": sorted(ctxs)[min(len(ctxs)-1, int(len(ctxs)*.9))] if ctxs else 0,
        "ctx_max": max(ctxs, default=0), "ctxs": ctxs,
        **totals, "tokens": totals["input"] + totals["out"],
        "wall_s": wall,
        "idle_s": sum(b-a for a,b in zip(stamps, stamps[1:]) if b-a > IDLE_GAP_S),
        "model": ",".join(models) or "?", "models": dict(models),
        "tools": dict(tools), "warnings": warnings,
        "started": datetime.fromtimestamp(stamps[0], tz=timezone.utc).isoformat() if stamps else "",
    }


def read_session(path: Path) -> dict | None:
    """Claude: last record per message.id supplies usage and response timestamp.

    Content blocks are separate records, so collect tool ids across ALL fragments.
    A user tool_result is transport, not a human turn. Legacy missing response ids
    cannot be deduplicated; count their records separately and disclose that limit.
    """
    responses, calls, users = {}, {}, set()
    missing_ids = 0
    for n, d in enumerate(_records(path)):
        m = d.get("message") or {}
        content = m.get("content")
        if d.get("type") == "user":
            if (isinstance(content, str) or isinstance(content, list) and
                    any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content)):
                users.add(d.get("uuid") or ("record", n))
        if d.get("type") != "assistant":
            continue
        mid = m.get("id")
        if not mid:
            missing_ids += 1
        responses[mid or ("record", n)] = d
        if isinstance(content, list):
            for i, b in enumerate(content):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    calls[b.get("id") or (mid or n, i)] = b.get("name", "?")
    observations = []
    totals = dict(input=0, fresh=0, cache_write=0, cache_read=0, out=0, reasoning=None)
    for d in responses.values():
        m = d.get("message") or {}
        u = m.get("usage")
        if not u:
            continue
        fresh = u.get("input_tokens", 0) or 0
        cw = u.get("cache_creation_input_tokens", 0) or 0
        cr = u.get("cache_read_input_tokens", 0) or 0
        inp = fresh + cw + cr
        for k, v in dict(input=inp, fresh=fresh, cache_write=cw, cache_read=cr,
                         out=u.get("output_tokens", 0) or 0).items():
            totals[k] += v
        observations.append(dict(input=inp, stamp=_stamp(d), model=m.get("model", "?")))
    if not observations:
        return None
    observations.sort(key=lambda o: o["stamp"] if o["stamp"] is not None else float('inf'))
    warnings = ([f"{missing_ids} assistant records lack message.id; unique response count is uncertain"]
                if missing_ids else [])
    return _row(path, "claude", project_name(path.parent.name), observations, totals,
                collections.Counter(calls.values()), users, warnings)


_CODEX_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _codex_usage(u):
    return tuple(u.get(k, 0) or 0 for k in _CODEX_KEYS)


def _cwd_project(cwd):
    if not cwd:
        return "unknown"
    # Worktree suffixes do not create separate projects.
    base = str(cwd).split("/.claude/worktrees/")[0]
    base = base.split("/.codex/worktrees/")[0]
    return "principles-of-good-architects" if _FED_RE.search(base) else Path(base).name


def read_codex_session(path: Path, paths=None) -> dict | None:
    """Read last cumulative snapshot, never sum token_count events.

    Resumed files with the same session_meta.id are one conversation. Replayed
    snapshots are not new responses. Counter decreases are disclosed: totals then
    describe the last counter epoch, not a fabricated reconstructed lifetime total.
    Response distributions describe observed distinct counter advances, which may
    be incomplete in a truncated transcript. Allowance changes are metadata only.
    """
    paths = paths or [path]
    records = [d for p in paths for d in _records(p)]
    records.sort(key=lambda d: _stamp(d) if _stamp(d) is not None else float('-inf'))
    model, project, sid = "?", "unknown", path.stem
    observations, calls, users, event_users = [], {}, set(), set()
    seen, prev, total = set(), None, None
    allowance, allowance_events = {}, []
    discontinuities, warnings = 0, []
    for n, d in enumerate(records):
        p = d.get("payload") or {}
        kind = d.get("type")
        if kind == "session_meta":
            sid = p.get("id") or sid
            project = _cwd_project(p.get("cwd"))
        elif kind == "turn_context":
            model = p.get("model") or model
        elif kind == "response_item":
            if p.get("type") in ("function_call", "custom_tool_call"):
                calls[p.get("call_id") or p.get("id") or ("record", n)] = p.get("name", "?")
            elif p.get("type") == "message" and p.get("role") == "user":
                content = p.get("content") or []
                # Codex injects host orientation as a user-role message. It is not
                # a user's turn; do not count that transport record as one.
                environment = (isinstance(content, list) and len(content) == 1 and
                               isinstance(content[0], dict) and
                               content[0].get("text", "").strip().startswith("<environment_context>") and
                               content[0].get("text", "").strip().endswith("</environment_context>"))
                if not environment:
                    users.add(p.get("id") or (d.get("timestamp"), json.dumps(p, sort_keys=True)))
        elif kind == "event_msg" and p.get("type") == "user_message":
            event_users.add(p.get("turn_id") or (d.get("timestamp"), json.dumps(p, sort_keys=True)))
        if kind != "event_msg" or p.get("type") != "token_count":
            continue
        limits = p.get("rate_limits") or {}
        for window in ("primary", "secondary"):
            reset = (limits.get(window) or {}).get("resets_at")
            if reset is not None:
                key = (limits.get("limit_id", "codex"), window)
                if key in allowance and allowance[key] != reset:
                    allowance_events.append(dict(timestamp=d.get("timestamp"), window=window,
                                                 previous=allowance[key], resets_at=reset))
                allowance[key] = reset
        info = p.get("info") or {}
        cumulative = info.get("total_token_usage")
        if not cumulative:
            continue
        current = _codex_usage(cumulative)
        # Preserve the actual final snapshot even when it replays an earlier value.
        total = current
        if current == prev:
            continue
        if current in seen:
            continue
        if prev is not None and any(b < a for a, b in zip(prev, current)):
            discontinuities += 1
        last = info.get("last_token_usage")
        if last:
            observations.append(dict(input=last.get("input_tokens", 0) or 0,
                                     stamp=_stamp(d), model=model))
        else:
            warnings.append("counter advance lacks last_token_usage; response distribution incomplete")
        seen.add(current)
        prev = current
    if total is None:
        return None
    if total != prev:
        warnings.append("final cumulative snapshot replays an earlier counter; totals use that snapshot")
    inp, cached, out, reasoning = total
    if discontinuities:
        warnings.append(f"{discontinuities} cumulative counter discontinuities; totals are the last snapshot, not lifetime usage")
    if cached > inp or reasoning > out:
        warnings.append("invalid component counters: cached input or reasoning exceeds its inclusive total")
    totals = dict(input=inp, fresh=inp-cached, cache_write=0, cache_read=cached,
                  out=out, reasoning=reasoning)
    row = _row(path, "codex", project, observations, totals,
               collections.Counter(calls.values()), users or event_users, warnings)
    row.update(session_id=sid, files=[p.name for p in paths],
               allowance_events=allowance_events, counter_discontinuities=discontinuities,
               response_basis="distinct cumulative token_count advances",
               startup=startup_audit(records))
    return row


# --- WI-0324: the startup-read audit -------------------------------------------------
#
# R4's acceptance is checked here: *"a Codex federation session's first ten tool calls
# contain no truncated output and no overlapping ranges of the same unchanged file."*
# Before this the Codex parser kept tool-call NAMES only — `{name: count}`, with the ids
# and the order thrown away — so the acceptance could not be evaluated at all.
#
# The unit of "tool call" here is the MODEL-VISIBLE call (`custom_tool_call` /
# `function_call`), not the shell commands inside it. That is deliberate and it matters:
# Codex batches several `exec_command`s into one call, and truncation is applied to the
# call's output as a whole, so counting shell commands would both inflate "ten" and
# attribute one truncation to the wrong command.

# Reads are parsed out of the call's OWN argument text rather than matched against the
# `CommandExecution` items the runtime emits separately. Those items carry a cleaner
# `parsed_cmd`, but they carry no `call_id`, so attributing one to its call means
# matching on ordinal adjacency — a heuristic that fails exactly where it matters, on
# the batched calls. The argument text is self-contained and is what the model wrote.
# Paths stop at a quote as well as at whitespace. The commands live inside a JS snippet
# (`tools.exec_command({cmd:"sed -n '1,240p' FILE","workdir":...})`), so a greedy `\S+`
# swallows the closing quote and the rest of the JSON object into the "path" — which
# then matches nothing, and a read that matches nothing is a read the audit cannot see.
_PATH = r"[^\s'\"]+"
_SED_RE = re.compile(r"sed\s+-n\s+['\"]?(\d+),(\d+)p['\"]?\s+(" + _PATH + r")")
# `cat` takes PATH-LOOKING tokens only. An unrestricted match reads the word after any
# `cat` in the argument text, which produced findings against a file literally named
# "the" — a path with neither a dot nor a slash is prose, not a filename.
_CAT_RE = re.compile(r"\bcat\s+((?:(?:-\S+|[\w./~@+-]*[./][\w./~@+-]*)\s*)+)")
_HEADC_RE = re.compile(r"\bhead\s+-c\s*(\d+)\s+(" + _PATH + r")")
_SHOW_BYTES_RE = re.compile(r"session\.py\s+show\s+(" + _PATH + r")[^;|&]*?--bytes\s+(\d*)-(\d*)")
_SHOW_SECTION_RE = re.compile(r"session\.py\s+show\s+(" + _PATH + r")[^;|&]*?--section")
_TRUNC_RE = re.compile(r"truncated output \(original token count: (\d+)\)")


def _clean_path(tok: str) -> str:
    return tok.strip().strip("'\"").rstrip(";")


def _parse_reads(text: str):
    """Read spans a tool call's argument text asks for, as `(path, start, end)`.

    Spans are half-open and expressed in the unit the command itself used — LINES for
    `sed -n`, BYTES for `head -c` and `session.py show --bytes`. They are never mixed:
    an overlap is only ever computed between two spans of the same unit, because a line
    range and a byte range over the same file cannot be compared without reading it, and
    guessing would manufacture overlaps that did not happen.

    A whole-file read (`cat`, or `show --section`) is recorded as the unbounded span
    `(0, None)`, which overlaps everything — which is correct, and is the point.
    """
    reads = []
    for m in _SED_RE.finditer(text):
        a, b, pth = int(m.group(1)), int(m.group(2)), _clean_path(m.group(3))
        reads.append((pth, "line", a, b + 1))
    for m in _HEADC_RE.finditer(text):
        reads.append((_clean_path(m.group(2)), "byte", 0, int(m.group(1))))
    for m in _SHOW_BYTES_RE.finditer(text):
        pth = _clean_path(m.group(1))
        start = int(m.group(2)) if m.group(2) else 0
        end = int(m.group(3)) if m.group(3) else None
        reads.append((pth, "byte", start, end))
    for m in _SHOW_SECTION_RE.finditer(text):
        reads.append((_clean_path(m.group(1)), "whole", 0, None))
    for m in _CAT_RE.finditer(text):
        for tok in m.group(1).split():
            pth = _clean_path(tok)
            if pth and not pth.startswith("-") and ("/" in pth or "." in pth):
                reads.append((pth, "whole", 0, None))
    # Two commands in one call reading the same span is one fact, not two.
    return list(dict.fromkeys(reads))


def _overlaps(a, b) -> bool:
    """Do two spans of the same file cover any of the same content?"""
    if a[1] != b[1] and "whole" not in (a[1], b[1]):
        return False                      # line-vs-byte: not comparable, not asserted
    a_end = a[3] if a[3] is not None else float("inf")
    b_end = b[3] if b[3] is not None else float("inf")
    return a[2] < b_end and b[2] < a_end


# The two sentinels `poga`'s `deliver_the_context` emits, one per delivery path
# (ADR-0144 D1). Matched as literal text because literal text is what reaches the model:
# the transcript records the opening prompt verbatim, so the bytes the launcher wrote are
# the bytes read back here.
#
# TIED TO THE PRODUCER BY A TEST, not by hope. A detector keyed on a string the other side
# is free to reword fails OPEN - every session would classify `absent`, which is a real
# class, so nothing would look broken while the audit quietly went blind.
# `test_the_sentinels_this_audit_matches_still_exist_in_poga` reads the launcher and fails
# if either line is gone.
CANON_EMBED_SENTINEL = "--- end of session-start context ---"
CANON_POINTER_SENTINEL = "it carries your session-start orientation"

# A Codex transcript opens with harness-authored `user`-role blocks (`<environment_context>`
# and siblings) BEFORE the operator's prompt - measured at record 5, with the real prompt at
# record 8. Taking the first user message blindly would read the harness block as the prompt
# and report `absent` for every session, including a correctly embedded one.
_HARNESS_BLOCK_RE = re.compile(r"<[a-z_][a-z0-9_]*>")


def _ordered(records):
    """Transcript order: by the `ordinal` every real record carries, falling back to
    timestamp then file order. Ordinal is used in preference because two records inside
    one turn share a timestamp to the millisecond, and a timestamp sort then reorders
    them arbitrarily."""
    def key(item):
        n, d = item
        o = d.get("ordinal")
        return (0, o, n) if isinstance(o, int) else (1, _stamp(d) or 0.0, n)

    return [d for _, d in sorted(enumerate(records), key=key)]


def codex_delivery(records) -> dict:
    """Which canon delivery this session's opening prompt actually carried.

    A fact about what the agent was HANDED, reported beside the reading verdict and never
    folded into it. The two answer different questions: the verdict says how the session
    read, the delivery says whether it was given anything to read in the first place.

    Keeping them apart is not tidiness - it is the one error this item has already made.
    On 2026-09-18 a session credited a clean overlap record to the byte-bounded reading
    rule and withdrew it the same hour: the rule travels INSIDE the payload, the payload
    never arrived, and a session handed nothing cannot re-read it. That reasoning was done
    by hand, off stderr and a launcher read, twice in one day. It is a property of the
    transcript, so it belongs in the instrument.

    Four classes, and `absent` is a measurement, not an error:

    * `embedded` - the payload itself is in the opening prompt (ADR-0144 D1).
    * `pointed`  - the degraded path: a pointer at `.session-state/session-context.md`.
    * `absent`   - neither. The session received no canon by either route.
    * `unknown`  - no operator prompt in the transcript at all, so the question was not
      answered rather than answered `absent`.
    """
    for d in _ordered(records):
        p = d.get("payload") or {}
        if p.get("type") != "message" or p.get("role") != "user":
            continue
        text = "".join(c.get("text", "") for c in p.get("content") or []
                       if isinstance(c, dict))
        if not text.strip() or _HARNESS_BLOCK_RE.match(text.strip()):
            continue
        if CANON_EMBED_SENTINEL in text:
            cls = "embedded"
        elif CANON_POINTER_SENTINEL in text:
            cls = "pointed"
        else:
            cls = "absent"
        return {"class": cls, "prompt_chars": len(text)}
    return {"class": "unknown", "prompt_chars": 0}


def codex_tool_calls(records):
    """Every model-visible tool call, in transcript order, with reads and truncation."""
    ordered = _ordered(records)
    outputs, changes, calls = {}, [], []
    # `issued` counts the calls ALREADY MADE when a file change lands, so a write can
    # only excuse a re-read that comes after it. Counting `len(calls)` here instead
    # records 0 for every change, because `calls` is not filled until the second pass -
    # and a write late in a session would then retroactively bless an overlap early in
    # it. Found by mutation sweep, not by a failing test: the fixture that should have
    # caught it passed for the wrong reason.
    issued = 0
    for d in ordered:
        p = d.get("payload") or {}
        if p.get("type") in ("custom_tool_call", "function_call"):
            issued += 1
        elif p.get("type") in ("custom_tool_call_output", "function_call_output"):
            out = p.get("output")
            outputs[p.get("call_id")] = out if isinstance(out, str) else json.dumps(out)
        elif p.get("type") == "item_completed":
            item = p.get("item") or {}
            if item.get("type") == "FileChange":
                changes.append((issued, set((item.get("changes") or {}).keys())))
    for d in ordered:
        p = d.get("payload") or {}
        if p.get("type") not in ("custom_tool_call", "function_call"):
            continue
        text = p.get("input") or p.get("arguments") or ""
        if not isinstance(text, str):
            text = json.dumps(text)
        cid = p.get("call_id") or p.get("id")
        body = outputs.get(cid, "")
        m = _TRUNC_RE.search(body)
        calls.append({
            "n": len(calls) + 1, "call_id": cid, "name": p.get("name", "?"),
            "reads": _parse_reads(text),
            "truncated": m is not None,
            "truncated_tokens": int(m.group(1)) if m else 0,
            "output_seen": bool(body),
            "command": text[:400],
        })
    return calls, changes


def startup_audit(records, limit: int = 10) -> dict:
    """The R4 verdict over a session's first `limit` model-visible tool calls.

    Reports three separate things and never folds them together
    ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes)):

    * `truncated` — calls whose output the runtime cut. Counted only where the output
      record was actually present; a call with no output record in the transcript is
      `unverifiable`, not `clean`.
    * `overlaps` — a later read covering content an earlier read already fetched, with
      no write to that path in between.
    * `rereads_after_change` — the same shape, but with a `FileChange` touching that path
      between the two reads. R4 asks for this distinction by name: re-reading a file that
      just changed is correct behaviour, and scoring it as waste would make the check
      fire on the code it exists to bless.
    """
    calls, changes = codex_tool_calls(records)
    delivery = codex_delivery(records)
    window = calls[:limit]
    seen, overlaps, rereads, reported = {}, [], [], set()
    for c in window:
        changed = {pth for idx, paths in changes if idx < c["n"] for pth in paths}
        for r in c["reads"]:
            pth = r[0]
            for prev_n, prev in seen.get(pth, []):
                # A call is never scored against itself. Two spans inside ONE call are
                # issued together, so neither is a re-read prompted by the other's
                # truncation - which is the waste this audit exists to find.
                if prev_n == c["n"] or not _overlaps(prev, r):
                    continue
                touched = any(pth == ch or ch.endswith("/" + pth) for ch in changed)
                key = (pth, prev_n, c["n"], touched)
                if key in reported:
                    break
                reported.add(key)
                rec = {"path": pth, "first_call": prev_n, "second_call": c["n"],
                       "first": prev[1:], "second": r[1:]}
                (rereads if touched else overlaps).append(rec)
                break
            seen.setdefault(pth, []).append((c["n"], r))
    truncated = [c["n"] for c in window if c["truncated"]]
    unverifiable = [c["n"] for c in window if not c["output_seen"]]
    return {
        "calls_examined": len(window), "limit": limit,
        # BESIDE the verdict, never inside it. What the session was handed and how it read
        # are two claims; folding them would turn "received nothing" into a reading fault
        # and let a verdict be quoted as evidence about a delivery that never happened.
        "delivery": delivery["class"],
        "prompt_chars": delivery["prompt_chars"],
        # A session that made 1 tool call literally satisfies "the first ten contain no
        # truncated output", and reading that as the same result as ten clean calls is
        # the trap: measured-small is not structurally-zero. The flag travels with the
        # verdict so no consumer can lose it.
        "full_window": len(window) >= limit,
        "truncated_calls": truncated,
        "unverifiable_calls": unverifiable,
        "overlaps": overlaps,
        "rereads_after_change": rereads,
        # PASS requires positive evidence, not the absence of a complaint: a transcript
        # with no tool calls at all, or one whose outputs are missing, cannot show the
        # first ten calls were clean and must not read as if it did.
        "verdict": ("PASS" if window and not truncated and not overlaps
                    and not unverifiable else
                    "UNVERIFIABLE" if not window or unverifiable else "FAIL"),
    }


def read_sessions(projects_dir: Path = PROJECTS_DIR, days: int = 14,
                  codex_dir: Path | None = None, project: str | None = None) -> list[dict]:
    # Existing callers that supply a fixture directory remain isolated from home.
    if codex_dir is None and projects_dir == PROJECTS_DIR:
        codex_dir = CODEX_DIR
    cutoff = time.time() - days * 86400
    rows = []
    def recent(p):
        try:
            return p.stat().st_mtime >= cutoff
        except OSError:
            return False
    for p in projects_dir.glob("*/*.jsonl"):
        if recent(p):
            row = read_session(p)
            if row:
                rows.append(row)
    groups = collections.defaultdict(list)
    if codex_dir is not None:
        for p in codex_dir.rglob("*.jsonl"):
            sid = str(p)
            for d in _records(p):
                if d.get("type") == "session_meta":
                    sid = (d.get("payload") or {}).get("id") or sid
                    break
            groups[sid].append(p)
    for paths in groups.values():
        # Include older shards of a recently resumed conversation.
        if any(recent(p) for p in paths):
            row = read_codex_session(paths[0], sorted(paths))
            if row:
                rows.append(row)
    if project:
        wanted = "principles-of-good-architects" if project == "federation" else project
        rows = [r for r in rows if r["project"] == wanted]
    rows.sort(key=lambda r: -r["tokens"])
    return rows


def summarize(rows: list[dict]) -> dict:
    ctx_all = sorted(c for r in rows for c in r["ctxs"])
    wall = sum(r["wall_s"] for r in rows)
    idle = sum(r["idle_s"] for r in rows)
    byp = collections.defaultdict(lambda: dict(sessions=0, responses=0, tool_calls=0,
                                              user_turns=0, tokens=0))
    tools = collections.Counter()
    for r in rows:
        b = byp[r["project"]]
        b["sessions"] += 1
        for k in ("responses", "tool_calls", "user_turns", "tokens"):
            b[k] += r[k]
        tools.update(r["tools"])
    total = sum(r["tokens"] for r in rows)
    for b in byp.values():
        b["share"] = b["tokens"] / total if total else 0.0
        b["turns"] = b["responses"]  # legacy JSON alias, no longer a report label
    result = {k: sum(r[k] for r in rows) for k in
              ("responses", "tool_calls", "user_turns", "input", "fresh", "cache_write", "cache_read", "out")}
    result.update(
        sessions=len(rows), turns=result["responses"], tokens=total,
        reasoning=sum(r["reasoning"] or 0 for r in rows),
        reasoning_sessions=sum(r["reasoning"] is not None for r in rows),
        ctx_median=int(statistics.median(ctx_all)) if ctx_all else 0,
        ctx_p90=ctx_all[min(len(ctx_all)-1, int(len(ctx_all)*.9))] if ctx_all else 0,
        over_200k_share=sum(c > 200_000 for c in ctx_all)/len(ctx_all) if ctx_all else 0.0,
        idle_share=idle/wall if wall else 0.0,
        by_project=dict(sorted(byp.items(), key=lambda kv: -kv[1]["tokens"])),
        tool_calls_by_name=dict(tools.most_common(8)),
        warnings=sum(len(r["warnings"]) for r in rows),
    )
    return result


def _m(n: float) -> str:
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def status_line(s: dict, days: int) -> str:
    if not s["sessions"]:
        return f"tokens: no matching transcripts in the last {days}d."
    return (f"tokens {days}d: {_m(s['tokens'])} over {s['sessions']} sessions / "
            f"{s['responses']} responses / {s['tool_calls']} tool calls / {s['user_turns']} user turns; "
            f"median ctx/response {s['ctx_median'] // 1000}k (target ≤{CTX_TARGET // 1000}k), "
            f"{s['over_200k_share']:.0%} of responses >200k; "
            f"idle wall-clock between responses {s['idle_share']:.0%}; {s['warnings']} accounting notes.")


def report(rows: list[dict], s: dict, days: int) -> str:
    out = [status_line(s, days), ""]
    if not rows:
        return "\n".join(out)
    out.extend([
        "Window: transcript file mtime; resumed Codex files grouped by session_meta.id.",
        "Input includes caches; reasoning is part of output (— means unavailable).",
        "Responses: Claude message.id (last record); Codex distinct cumulative counter advances.",
        "Idle: gaps >600s of wall-clock between responses; elapsed idle time itself consumes no tokens.",
        "", "by project | sessions | responses | tool calls | user turns | tokens",
    ])
    for p, b in s["by_project"].items():
        out.append(f"{p} | {b['sessions']} | {b['responses']} | {b['tool_calls']} | "
                   f"{b['user_turns']} | {b['tokens']:,}")
    out.extend(["", "runtime | model | project | transcript | responses | tool calls | user turns | "
                "ctx first/median/p90/max | input | uncached | cache write | cache read | output | reasoning | idle"])
    # Every row: a small session must remain visible alongside the expensive ones.
    for r in rows:
        reasoning = f"{r['reasoning']:,}" if r['reasoning'] is not None else "—"
        out.append(f"{r['runtime']} | {r['model']} | {r['project']} | {r['file']} | "
                   f"{r['responses']} | {r['tool_calls']} | {r['user_turns']} | "
                   f"{r['first_ctx']:,}/{r['ctx_median']:,}/{r['ctx_p90']:,}/{r['ctx_max']:,} | "
                   f"{r['input']:,} | {r['fresh']:,} | {r['cache_write']:,} | {r['cache_read']:,} | "
                   f"{r['out']:,} | {reasoning} | "
                   f"{(r['idle_s']/r['wall_s'] if r['wall_s'] else 0):.0%}")
    for r in rows:
        for warning in r["warnings"]:
            out.append(f"NOTE {r['file']}: {warning}")
        for event in r.get("allowance_events", []):
            out.append(f"ALLOWANCE {r['file']}: {event['timestamp']} {event['window']} "
                       f"reset {event['previous']} -> {event['resets_at']}; same conversation")
    out.append("\ntool calls: " + ", ".join(f"{k}:{v}" for k,v in s['tool_calls_by_name'].items()))
    return "\n".join(out)


def startup_audit_row(row, limit: int) -> dict:
    """Re-run the audit at a different `limit` by re-reading the row's own files.

    The row caches the audit at the default limit; asking for a different one must
    re-read rather than reslice, because the cached record is a verdict, not the calls
    it was computed from.
    """
    paths = []
    for name in row.get("files", []):
        paths.extend(CODEX_DIR.rglob(name))
    records = [d for q in paths for d in _records(q)]
    if not records:
        return dict(calls_examined=0, limit=limit, truncated_calls=[],
                    unverifiable_calls=[], overlaps=[], rereads_after_change=[],
                    delivery="unknown", prompt_chars=0, verdict="UNVERIFIABLE")
    return startup_audit(records, limit)


def startup_report(rows, limit: int = 10) -> str:
    """Render the R4 startup verdict per Codex session, worst first.

    Claude rows are counted as `n/a`, never silently dropped: the audit is a claim about
    runtimes that read through a capped exec channel, and a reader who sees only Codex
    rows cannot tell whether the Claude ones passed or were never asked.
    """
    codex = [r for r in rows if r.get("runtime") == "codex"]
    claude = [r for r in rows if r.get("runtime") != "codex"]
    if not codex:
        return ("startup audit: no Codex sessions in window "
                f"({len(claude)} Claude session(s), n/a - different read channel).")
    rank = {"FAIL": 0, "UNVERIFIABLE": 1, "PASS": 2}
    codex.sort(key=lambda r: (rank.get((r.get("startup") or {}).get("verdict"), 3),
                              r.get("file", "")))
    out = [f"startup audit - first {limit} tool calls, {len(codex)} Codex session(s)"
           f"  ({len(claude)} Claude session(s) n/a: different read channel)", ""]
    tally, delivered = collections.Counter(), collections.Counter()
    for r in codex:
        a = r.get("startup") or {}
        if a.get("limit") != limit:
            a = startup_audit_row(r, limit)
        v = a.get("verdict", "UNVERIFIABLE")
        tally[v if a.get("full_window") else v + " (short)"] += 1
        delivered[a.get("delivery", "unknown")] += 1
        short = "" if a.get("full_window") else \
            f"  [only {a.get('calls_examined', 0)} of {limit} calls - weak evidence]"
        out.append(f"{a.get('verdict', '?'):<13} "
                   f"{a.get('delivery', 'unknown'):<8} {r.get('project', '?')}  "
                   f"{r.get('file', '?')}{short}")
        if a.get("truncated_calls"):
            out.append("    truncated output on call(s): "
                       + ", ".join(map(str, a["truncated_calls"])))
        for o in a.get("overlaps", []):
            out.append(f"    call {o['second_call']} re-reads {o['path']} already "
                       f"fetched by call {o['first_call']} (no change in between)")
        for o in a.get("rereads_after_change", []):
            out.append(f"    call {o['second_call']} re-reads {o['path']} AFTER a write "
                       f"- deliberate, not counted against it")
        if a.get("unverifiable_calls"):
            out.append("    no output record for call(s): "
                       + ", ".join(map(str, a["unverifiable_calls"])))
    out += ["", "  ".join(f"{k}={v}" for k, v in sorted(tally.items())),
            "delivery: " + "  ".join(f"{k}={v}" for k, v in sorted(delivered.items()))]
    # THE SENTENCE THAT STOPS THE MISREADING, printed only when it is true. A reader who
    # sees PASS and FAIL counts will reason about them; if not one of those sessions was
    # handed the payload, every such reading is about the degraded path and about nothing
    # else. Saying so here costs one line and has already cost a withdrawn claim once.
    if not delivered["embedded"]:
        out.append("          no session here received the embedded payload (ADR-0144 D1),"
                   " so no verdict above is evidence about it.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--projects-dir", default=str(PROJECTS_DIR))
    ap.add_argument("--codex-dir", default=str(CODEX_DIR))
    ap.add_argument("--project", help="exact project name; federation aliases principles-of-good-architects")
    ap.add_argument("--status", action="store_true", help="one line, fail-open")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--startup-audit", action="store_true",
                    help="WI-0324: for each Codex session, the R4 verdict over its first "
                         "--startup-calls tool calls - truncated output, and overlapping "
                         "reads of a file that did not change in between - beside the "
                         "canon delivery that session actually received.")
    ap.add_argument("--startup-calls", type=int, default=10,
                    help="How many tool calls the startup audit examines (default 10, "
                         "the number R4's acceptance names).")
    a = ap.parse_args(argv)
    try:
        rows = read_sessions(Path(a.projects_dir), a.days, Path(a.codex_dir), a.project)
        s = summarize(rows)
    except Exception as e:  # a status probe must never break a session start
        print(f"tokens: could not read transcripts ({e}).")
        return 0
    if a.startup_audit:
        print(startup_report(rows, a.startup_calls))
    elif a.json:
        # `ctxs` is per-turn detail summarize() needs and a JSON consumer does not — tens
        # of thousands of integers. The order statistics stay on the row.
        slim = [{k: v for k, v in r.items() if k != "ctxs"} for r in rows]
        print(json.dumps({"summary": s, "sessions": slim}, indent=1))
    elif a.status:
        print(status_line(s, a.days))
    else:
        print(report(rows, s, a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
