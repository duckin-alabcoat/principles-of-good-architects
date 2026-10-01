#!/usr/bin/env python3
"""Federation metrics — the mining layer + four renderings (M2/M3 of the metrics brief).

The deterministic half of "how is the federation actually doing": it MINES numbers
from artifacts already on disk (session stamps, guard-firing logs, STATUS.md, the
proposed-edits inboxes) and from `git`, then renders them four ways. It computes;
the Architect keeps the judgment (P15 code-for-mechanism-not-judgment).

Also hosts the ritual-conformance miners (ADR-0053, self-governance R3): session-close
discipline, role-doc bump discipline, STATUS/ROADMAP refresh-at-close, adoption stamps,
and the spot-audit recency — ritual compliance mined from artifacts, never self-reported.

Two disciplines are load-bearing here and are enforced, not aspired to:

  - **P15 / no-fabricated-data.** Every number is mined from a real artifact or from
    git. When a signal's *source does not exist yet* (an apply.py surfacing ledger, a
    half-apply incident record, ring/comms/backup instrumentation), the number is
    emitted as `n/a (source pending: <what>)` — NEVER estimated. One invented number
    would poison the whole layer, so the miner would rather say "I can't know this."
  - **Read-only over the fleet (P18).** The miner never writes into another system's
    repo. It reads STATUS.md / handoffs / inboxes and returns a dict. Its only write
    is `EVIDENCE.md` into the federation repo root (generated, banner-stamped, same
    discipline as CANON.md).

Member location mirrors reconcile.py / standard_version.py: walk the machine-local
search roots (`reconcile-roots.local`, P3 keeps machine paths out of tracked files)
plus the `repo-paths.local` locator map, and read each system's repo-root STATUS.md.
The federation's own repo is included as a system (recursive participation, ADR-0003).

CLI:
  python3 curate/metrics.py --status [roots…]        # one-line SessionStart signal
  python3 curate/metrics.py --exceptions [roots…]    # ranked attention list (default)
  python3 curate/metrics.py --money-slide [roots…]   # the seven headline numbers
  python3 curate/metrics.py --evidence [roots…]      # write EVIDENCE.md (full dataset)

Roots default to `reconcile-roots.local` (like standard_version.py). Everything runs
off a single `mine(roots) -> dict` core; the four renderers are thin views over it.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta

from common import (
    atomic_write,
    read_repo_paths_config as _read_repo_paths_config,
    read_roots_config as _read_roots_config,
    member_config_root,
)

# reconcile.py holds the STATUS locator + freshness/stale-clone mechanics; reuse them
# verbatim (P16 avoid-duplication) rather than re-deriving "where does a member live".
import reconcile

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent

# WI-0029: layout resolution has ONE implementation, and it lives in the shipped
# harness rather than here — the member declares its layout, and the federation-side
# miner must read that declaration through the same door the member's own harness
# does. Same mechanism `standard_version.py` uses to import the shared capability
# manifest from `standard_check.py`: root on the path, import the real thing, never
# re-implement it. Importing `session` is cheap (script-relative paths, one config
# read, no git) — this module runs as a SessionStart probe.
sys.path.insert(0, str(FED_ROOT))
import session as _session  # noqa: E402  (federation root on the path; the shared resolver)
# WI-0361: external under an explicitly configured production service, and identical
# to `shared_work_root` otherwise. A release clone carries neither file, so reading
# them from the checkout would locate zero members and report a clean run.
ROOTS_CONFIG = member_config_root(FED_ROOT) / "reconcile-roots.local"
REPO_PATHS_CONFIG = member_config_root(FED_ROOT) / "repo-paths.local"
# CADENCE_PATH is tracked (system, not data — see .gitignore); EVIDENCE_PATH is
# gitignored but deliberately derived/ephemeral, correct to regenerate PER LANE
# (session.py's SHARED_LANE_PATHS comment excludes it by name). Neither shares.
CADENCE_PATH = FED_ROOT / "fleet-cadence.json"
EVIDENCE_PATH = FED_ROOT / "EVIDENCE.md"
# WI-0207 (c). The exception line used to end "detail in EVIDENCE.md", pointing a reader
# at a file that DOES NOT EXIST until someone runs the generator — it is written on the
# `--evidence` path only, never at startup, and is gitignored besides. A pointer to an
# absent file reads as a broken metric, and the one thing that would resolve it (the
# command) was the thing not printed. Name the command instead; it is the same length.
EVIDENCE_CMD = "run 'python3 curate/metrics.py --evidence'"

DEFAULT_CADENCE_DAYS = 7      # a system silent longer than this (and not parked) is stale
PENDING_DWELL_DAYS = 7        # a pending brief older than this is a stranded-brief signal
ORPHAN_LIVE_STALE_HOURS = 12  # a `.live` sidecar with no `.ended` older than this = residual
DEFAULT_RUNTIME = "claude-code"  # legacy stamps predate S1's runtime field; historically true
# The exceptions view retains its five-sweep attention threshold. WI-0315 makes
# the runner standing note escalate after one; its auth state is shown immediately
# in the evidence view below.
RUNNER_STALE_ATTENTION = 5

# ADR-0052: the injected doctrine set (CANON.md + STANDARD.md) is injected into every
# Architect's context every session start, forever — the one cost the whole fleet pays
# per session. It carries a size budget so its growth is SEEN, not felt. A SOFT budget:
# crossing it flags a consolidation pass owed (an exceptions line), it never blocks a
# change. Measured in bytes (`wc -c`) — deterministic and tokenizer-free (a token count
# needs a model-specific tokenizer, P15); the human intuition is ~bytes ÷ 4 ≈ tokens.
CANON_FILES = ("CANON.md", "STANDARD.md")  # the injected doctrine set
CANON_BUDGET_CHARS = 48000                 # ADR-0052 ceiling for the combined set

# STATUS `blocked:` values that mean "not blocked" (mirrors reconcile.py's own set).
_UNBLOCKED = {"false", "", "no", "none"}

EVIDENCE_BANNER = "> GENERATED — do not edit by hand. Produced by curate/metrics.py.\n"


# --------------------------------------------------------------------------- stamp parsing

# The datetime inside a stamp line; the TZ abbreviation varies machine to machine, so it
# is matched but not interpreted — naive datetimes are enough for ordering and counts.
_DT_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) [A-Z]{3,4}")
_DUR_RE = re.compile(r"^(?:\d+h\s+)?\d+m$")          # "2h 01m" / "46m"
_RUNTIME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")  # "claude-code", "gemini-antigravity"
_START_RE = re.compile(r"^\*\*Start:\*\*\s*(.*)$")
_END_RE = re.compile(r"^\*\*End:\*\*\s*(.*)$")


def _parse_dt(seg):
    m = _DT_RE.search(seg)
    if not m:
        return None
    y, mo, d, h, mi = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def _parse_stamp_body(body, kind):
    """Return (datetime|None, runtime|None) for a Start/End stamp body.

    Fields are ` · `-delimited. `runtime` is the LAST field on an S1 stamp, but legacy
    lines (pre-S1) simply LACK it: a Start ends at the datetime, an End ends at the
    duration. We locate the datetime segment structurally and read the field that would
    follow the runtime slot — Start: datetime+1; End: datetime+2 (past the duration).
    A candidate that looks like a duration or does not look like a runtime token is
    treated as "no runtime present" -> the caller defaults it (see resolve below). This
    keeps the claude-code default to lines that STRUCTURALLY lack the field, never to a
    line that carries a real one.
    """
    segs = [s.strip() for s in body.split("·")]
    dt = None
    dt_idx = None
    for i, s in enumerate(segs):
        d = _parse_dt(s)
        if d is not None:
            dt, dt_idx = d, i
            break
    runtime = None
    if dt_idx is not None:
        after = segs[dt_idx + 1:]
        cand = None
        if kind == "start":
            cand = after[0] if len(after) >= 1 else None
        else:  # end: [duration, runtime]
            cand = after[1] if len(after) >= 2 else None
        if cand and _RUNTIME_RE.match(cand) and not _DUR_RE.match(cand):
            runtime = cand
    return dt, runtime


def parse_sessions(text):
    """Split one handoff file into sessions on its Start/End stamp lines.

    Each session pairs a `**Start:**` with the next `**End:**` (an unclosed trailing
    Start — e.g. the in-progress session — is kept, end-less). The SESSION LOG table at
    the top is ignored: it has no stamp lines, so its rows never match here.
    """
    sessions = []
    cur = None
    for line in text.splitlines():
        ms = _START_RE.match(line)
        if ms:
            if cur is not None:
                sessions.append(cur)
            dt, rt = _parse_stamp_body(ms.group(1), "start")
            cur = {"start_dt": dt, "start_runtime": rt, "end_dt": None,
                   "end_runtime": None, "start_line": line.strip()}
            continue
        me = _END_RE.match(line)
        if me and cur is not None:
            dt, rt = _parse_stamp_body(me.group(1), "end")
            cur["end_dt"] = dt
            cur["end_runtime"] = rt
            sessions.append(cur)
            cur = None
    if cur is not None:
        sessions.append(cur)
    for s in sessions:
        # One session = one runtime: the End field wins (it is the authoritative close),
        # else the Start field, else the definitional legacy default.
        s["runtime"] = s["end_runtime"] or s["start_runtime"] or DEFAULT_RUNTIME
        s["dt"] = s["start_dt"] or s["end_dt"]
    return sessions


def _handoff_name(repo):
    """The repo's declared handoff filename from its own session.config.json
    (mirrors `standard_check.py::d_handoff`), or the standard default. A member that
    declares a non-default `handoff` value would otherwise mine zero sessions here
    while `standard_check.py` reports it present — a declare-what-a-check-assumes gap."""
    return _session.member_layout(repo, "handoff")


def _handoff_files(repo):
    """Every handoff file for a repo: the live handoff doc (declared name, or the
    standard `session-handoff.md`) plus rotated archives — sibling `*archive*.md`
    files AND `*.md` under an `*archive*` directory (the federation keeps
    `session-handoff-archive/sessions-NN-MM.md`)."""
    files = []
    main = repo / _handoff_name(repo)
    if main.is_file():
        files.append(main)
    for p in sorted(repo.glob("*archive*.md")):
        if p.is_file():
            files.append(p)
    for d in sorted(repo.glob("*archive*")):
        if d.is_dir():
            files.extend(sorted(p for p in d.glob("*.md") if p.is_file()))
    # de-dup, preserve order
    seen = set()
    out = []
    for p in files:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def mine_sessions(repo):
    """All sessions across a repo's handoff + archives, de-duplicated by stamp line
    (a rotation that left a session in both files must not double-count it)."""
    seen = set()
    out = []
    for f in _handoff_files(repo):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for s in parse_sessions(text):
            key = s["start_line"] or id(s)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


# --------------------------------------------------------------------------- guard firings


def mine_guard_firings(repo):
    """Counts per guard from `<repo>/.session-state/guard-firings.jsonl`.

    Absent file = zero firings (machine-local, gitignored, MAY be absent — not an
    error). Malformed lines are counted and skipped; a bad line never crashes the miner.
    """
    p = repo / ".session-state" / "guard-firings.jsonl"
    counts, total, malformed = {}, 0, 0
    if not p.is_file():
        return {"counts": counts, "total": total, "malformed": malformed}
    try:
        raw = p.read_text(errors="replace")
    except OSError:
        return {"counts": counts, "total": total, "malformed": malformed}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            malformed += 1
            continue
        if not isinstance(obj, dict):
            malformed += 1
            continue
        guard = obj.get("guard")
        if not isinstance(guard, str) or not guard:
            malformed += 1
            continue
        counts[guard] = counts.get(guard, 0) + 1
        total += 1
    return {"counts": counts, "total": total, "malformed": malformed}


# --------------------------------------------------------------------------- briefs (adoption)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _brief_date(value):
    if not value:
        return None
    m = _DATE_RE.search(value)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _brief_field(text, name):
    m = re.search(rf"(?im)^\s*-?\s*\*\*{re.escape(name)}:\*\*\s*(.+?)\s*$", text)
    if m:
        return m.group(1).strip()
    # apply.py stamps a non-bold `applied: <stamp>` frontmatter line on auto-adopt.
    m = re.search(rf"(?im)^\s*{re.escape(name.lower())}:\s*(.+?)\s*$", text)
    return m.group(1).strip() if m else None


_APPLY_MODE_RE = re.compile(r"(?im)^\s*apply:\s*([A-Za-z][A-Za-z-]*)\s*$")


def _parse_brief(path):
    try:
        text = path.read_text(errors="replace")[:4000]
    except OSError:
        text = ""
    drafted = _brief_date(_brief_field(text, "Drafted on"))
    applied = _brief_date(_brief_field(text, "Applied on")) or _brief_date(_brief_field(text, "applied"))
    return {
        "path": path,
        "edit_id": _brief_field(text, "Edit ID"),
        "state": _brief_field(text, "State"),
        "drafted_on": drafted,
        "applied_on": applied,
        # The apply engine stamps `applied:` iff the brief opens with a YAML
        # frontmatter fence (session.py file_applied_brief: text.startswith("---")).
        # A brief with no fence is a pre-schema legacy brief the engine cannot stamp.
        "has_frontmatter": text.startswith("---"),
        # The declared apply mode (`auto` / `manual`, ADR-0049), lowercased, or None
        # for a pre-schema brief that declares none. WHICH WRITER COULD HAVE STAMPED
        # THIS is what separates an audit gap from a structural impossibility — see
        # `_brief_was_stampable` (WI-0207).
        "apply_mode": _apply_mode(text),
    }


def _apply_mode(text):
    m = _APPLY_MODE_RE.search(text)
    return m.group(1).lower() if m else None


def _briefs_root(repo):
    """The repo's brief root, DERIVED from its own declared `inbox` (WI-0029).

    `inbox` is a full path to the pending dir (`proposed-edits/<arch-id>/pending`), so
    its grandparent is the root holding one directory per target inbox. Mirrors
    `_handoff_name` above, for the same reason: the member declares this, and reading the
    federation's own literal instead measures the federation's layout on someone else's
    repo. Returns (root, declared) — `declared` is False when we fell back to the
    standard default, so the caller can tell "checked" from "assumed".
    """
    v = _session.member_layout(repo, "inbox")
    if isinstance(v, str) and v.strip():
        p = pathlib.Path(v.strip())
        # <root>/<arch-id>/pending -> <root>, i.e. the GRANDPARENT of the pending dir.
        # (Not `parts[0]`: that only coincides with the root when the path is exactly
        # three segments deep, and silently mis-resolves any nested layout — which is
        # the whole class of defect this item exists to remove.) Anything shallower than
        # three parts is not that shape, so fall back rather than inventing a parent
        # above the repo.
        if len(p.parts) >= 3:
            return repo / p.parent.parent, True
    return repo / "proposed-edits", False


def mine_briefs(repo):
    """Applied + pending briefs under `<brief-root>/<arch-id>/{applied,pending}/`.

    Each entry carries its arch-id (the target inbox), file mtime (the applied/pending
    time PROXY), and any mined drafted/applied dates. mtime is a proxy and is labelled
    as such wherever it drives a number — we do not pretend to a precision we lack.

    `checked` is False when the root is absent — which is NOT the same as "this member
    has no briefs", and used to be reported identically (empty lists, no signal). A
    member that declares a non-default inbox, or whose gitignored inbox simply is not
    materialized in the checkout being mined, would read as a clean zero
    ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes) —
    the WI-0029 pattern, where the assurance surface reports success exactly where it is
    blind).
    """
    root, declared = _briefs_root(repo)
    applied, pending = [], []
    if not root.is_dir():
        return {"applied": applied, "pending": pending, "checked": False,
                "declared": declared, "root": str(root)}
    for arch_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for state, bucket in (("applied", applied), ("pending", pending)):
            d = arch_dir / state
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                entry = {"arch": arch_dir.name, "mtime": mtime, **_parse_brief(f)}
                bucket.append(entry)
    return {"applied": applied, "pending": pending, "checked": True,
            "declared": declared, "root": str(root)}


# --------------------------------------------------------------------------- orphan sidecars


def mine_orphan_live(repo, now_ts):
    """`.session-state/*.live` sidecars with no matching `.ended`, older than the stale
    threshold — best-effort residual detector (ADR-0035/0036 session model).

    A fresh `.live` with no `.ended` is simply the CURRENTLY-ACTIVE session, not an
    orphan; only a `.live` whose mtime is older than ORPHAN_LIVE_STALE_HOURS (with no
    `.ended`) is reported, so a live run never flags itself. Returns a list of
    (session-id, age_hours). The model is genuinely ambiguous, so this stays best-effort.
    """
    d = repo / ".session-state"
    if not d.is_dir():
        return []
    ended = {p.stem for p in d.glob("*.ended")}
    out = []
    for p in d.glob("*.live"):
        if p.stem in ended:
            continue
        try:
            age_h = (now_ts - p.stat().st_mtime) / 3600.0
        except OSError:
            continue
        if age_h >= ORPHAN_LIVE_STALE_HOURS:
            out.append((p.stem, age_h))
    return sorted(out, key=lambda t: -t[1])


def mine_adopt_runner(fed_root):
    """The R3 adoption runner's last-sweep status (ADR-0050), or None if it has never
    reported on THIS machine. The runner (`curate/adopt-runner.py`) overwrites
    `.session-state/adopt-runner.status` on every real sweep; this is a read-only mine
    of that single file — the dashboard's proof-of-life + stale-streak source.

    Machine-local by nature (`.session-state/` is gitignored, ADR-0036): the runner
    writes it where it executes (the Runner's nightly sweep), so an absent file here
    means "the runner does not run on this machine", NOT "the runner is broken" — it is
    reported as `n/a (source pending)`, never fabricated (P15)."""
    if fed_root is None:
        return None
    import production
    p = production.state_path(fed_root, "adopt-runner.status",
                              pathlib.Path(fed_root) / ".session-state" / "adopt-runner.status")
    try:
        data = json.loads(p.read_text(errors="replace"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- git backfill


def _git_earliest_commit(repo):
    """Date of the repo's root commit — the honest, cheap "months in operation" floor
    when session stamps don't reach back far enough. Read-only; any git error -> None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--max-parents=0", "--format=%cs"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    earliest = None
    for line in out.stdout.split():
        try:
            d = date.fromisoformat(line.strip())
        except ValueError:
            continue
        if earliest is None or d < earliest:
            earliest = d
    return earliest


def git_ahead_unpushed(repo):
    """Commits ahead of upstream with nothing behind = un-pushed local work ("unbacked-up").

    The backup-of-record is origin; a checkout ahead-only has work that exists on no
    other machine. Read-only (compares to the already-known upstream ref, never fetches),
    fail-soft: any git error / no upstream -> None. Complements reconcile.git_staleness,
    which flags the behind/diverged (stale-clone) direction.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--left-right", "--count", "HEAD...@{u}"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.split()
    if len(parts) != 2:
        return None
    try:
        ahead, behind = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if ahead > 0 and behind == 0:
        return ahead
    return None


# --------------------------------------------------------------------------- canon budget (ADR-0052)


def mine_canon_size(fed_root):
    """Size of the FEDERATION'S OWN injected doctrine set (CANON.md + STANDARD.md) vs. the
    ADR-0052 budget. Cheap (two file reads) — safe on the startup path.

    SCOPE — read this before quoting the number. This is the federation source: the
    per-session cost of an Architect that carries the CURRENT copy, and the subject a
    consolidation pass (ADR-0052 §3) actually edits. It is NOT the fleet's per-session
    cost, and this docstring used to say it was, on the assumption that the substrate push
    keeps every member byte-identical. Nothing enforces that assumption — the push is an
    act someone runs, and between runs a member carries whatever generation it was last
    given. The error is neither small nor stably signed: it has been measured both well UNDER
    what this function implied and, after a consolidation landed here and was never
    pushed, well OVER, with most repos above a ceiling this function reported as
    comfortably met (WI-0374). What the
    fleet carries is measured, per repo, by `mine_fleet_canon_size` — never inferred here.

    Measured in BYTES (`len(read_bytes())` == `wc -c`), the unit ADR-0052 calibrated the
    budget in. Read-only, fail-soft: a missing file contributes 0 and is recorded as
    `None`, never an error — which is right for a report and wrong for a gate, so
    `curate/check_canon_budget.py` (ADR-0137) deliberately does not reuse this. Returns
    {scope, files, total, budget, over_by}, or None when `fed_root` is None (tests) so the
    caller simply omits the signal."""
    if fed_root is None:
        return None
    fed = pathlib.Path(fed_root)
    files, total = {}, 0
    for name in CANON_FILES:
        try:
            n = len((fed / name).read_bytes())
        except OSError:
            n = None
        files[name] = n
        if n:
            total += n
    return {"scope": "federation-source", "files": files, "total": total,
            "budget": CANON_BUDGET_CHARS,
            "over_by": max(0, total - CANON_BUDGET_CHARS)}


def mine_fleet_canon_size(systems, fed_root=None, fed_total=None):
    """What every LOCATED repo actually carries of the injected doctrine set — measured
    per repo, never inferred from the federation's copy (WI-0374).

    `mine_canon_size` above measures one tree. Multiplying that by the repo count is the
    assumption this function retires: it reads each repo's own CANON.md + STANDARD.md, so
    a member that never received the last push is counted at the size it really pays.

    COST — two `read_bytes()` per located repo, inside the loop that already walks each
    repo's sessions/, journal/ and briefs. Same cost class as what is already there, so it
    stays on the standard path. ADR-0052 §4's "cheap, every startup" constraint is about
    the git-history walks (`mine_canon_trend`, the deep role-doc bumps), not about opening
    two files.

    FAIL-SOFT, WITHOUT LETTING AN UNREADABLE MEMBER READ AS A CHEAP ONE. Three states per
    repo, and only `complete` ones are summed:
      complete  both files read — contributes to `fleet_total`
      partial   one file read — its total is a FLOOR, so it is reported and NOT summed
      absent    neither file — not a doctrine carrier; reported, not summed
    A partial repo is the known push asymmetry (CANON.md is introduced where absent,
    STANDARD.md is refresh-only), surfaced here as a measurement, not diagnosed here.

    `fed_total` is the source size from `mine_canon_size` — passed IN rather than picked
    out of `systems`, because the two are not the same tree and assuming they were is how
    the first cut of this silently reported no source at all: `_locate_systems` resolves
    the roster's `federation` row through `repo-paths.local`, which names the main
    checkout, while `fed_root` here is whichever worktree is running. Same repo, different
    trees, different bytes while a lane is in flight.

    Returns {budget, fed_total, members, measured, partial, absent, fleet_total,
    if_uniform, gap, stale, over}, or None when nothing is located.

      fleet_total  measured bytes the fleet pays per session, summed over complete repos
      if_uniform   fed_total * measured — what the retired assumption reported
      gap          fleet_total - if_uniform; POSITIVE means the fleet costs MORE than the
                   federation-only number says, which is the direction a consolidation
                   landed-but-not-pushed produces
      stale        complete non-federation repos whose total differs from the federation's
      over         complete repos above the ceiling
    """
    if not systems:
        return None
    fed = None
    if fed_root is not None:
        try:
            fed = pathlib.Path(fed_root).resolve()
        except OSError:
            fed = None
    members, fleet_total = [], 0
    for sid in sorted(systems):
        repo = pathlib.Path(systems[sid][1])
        files, total, present = {}, 0, 0
        for name in CANON_FILES:
            try:
                n = len((repo / name).read_bytes())
            except OSError:
                n = None
            files[name] = n
            if n is not None:
                present += 1
                total += n
        state = ("complete" if present == len(CANON_FILES)
                 else "absent" if present == 0 else "partial")
        try:
            is_fed = fed is not None and repo.resolve() == fed
        except OSError:
            is_fed = False
        row = {"system": sid, "files": files, "state": state, "federation": is_fed,
               "total": total if state != "absent" else None,
               "over_by": max(0, total - CANON_BUDGET_CHARS) if state == "complete" else None}
        if state == "complete":
            fleet_total += total
        members.append(row)

    complete = [m for m in members if m["state"] == "complete"]
    measured = len(complete)
    stale = [m["system"] for m in complete
             if fed_total is not None and m["total"] != fed_total]
    over = [m["system"] for m in complete if m["over_by"]]
    if_uniform = fed_total * measured if fed_total is not None else None
    return {"budget": CANON_BUDGET_CHARS, "fed_total": fed_total, "members": members,
            "measured": measured,
            "partial": [m["system"] for m in members if m["state"] == "partial"],
            "absent": [m["system"] for m in members if m["state"] == "absent"],
            "fleet_total": fleet_total, "if_uniform": if_uniform,
            "gap": (fleet_total - if_uniform) if if_uniform is not None else None,
            "stale": stale, "over": over}


def mine_canon_trend(fed_root, files=CANON_FILES):
    """Injected-set size over time, mined from the git history of CANON.md + STANDARD.md.

    Deterministic, read-only, and ON-DEMAND (the `--evidence` path only — NEVER the
    startup path). One data point per date the set changed: (date, total_bytes), oldest
    first, one row per date (the last commit of a date wins). A file absent at a revision
    contributes 0 (it did not exist yet — CANON predates STANDARD). Any git error -> []
    (fail-soft).

    Two git calls total, regardless of history depth, and NO pathspec history walk: a
    plain `log` for every commit (pathspec filtering — `-- CANON.md STANDARD.md` — forces
    per-commit tree diffs that are pathologically slow on a network mount; a pathspec-free
    log is far cheaper), then a single `cat-file --batch-check`
    resolving every `<rev>:<file>` blob size at once. We keep only the commits where the
    combined size actually CHANGED, so flat runs across hundreds of commits collapse."""
    if fed_root is None:
        return []
    fed = pathlib.Path(fed_root)
    try:
        log = subprocess.run(
            ["git", "-C", str(fed), "log", "--format=%H %cs"],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if log.returncode != 0:
        return []
    revs = []  # git log is newest-first
    for line in log.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            revs.append((parts[0], parts[1]))
    if not revs:
        return []
    revs.reverse()  # walk oldest -> newest so "changed" means "changed from the past"

    # One batch-check resolves every <rev>:<file> object size. Present -> the size digits;
    # absent -> "<input> missing" (still exactly one output line, so alignment holds).
    queries = [f"{rev}:{name}" for rev, _ in revs for name in files]
    try:
        bc = subprocess.run(
            ["git", "-C", str(fed), "cat-file", "--batch-check=%(objectsize)"],
            input="\n".join(queries) + "\n", capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    sizes = bc.stdout.splitlines()
    if bc.returncode != 0 or len(sizes) != len(queries):
        return []

    by_date, prev_total = {}, None
    tokens = iter(sizes)
    for _rev, cs in revs:
        total, present = 0, False
        for _name in files:
            tok = next(tokens).strip()
            if tok.isdigit():          # a bare size == object present at this rev
                total += int(tok)
                present = True
            # else "<sha>:<file> missing" -> the file did not exist yet, adds 0
        if not present:
            continue                   # neither file existed at this rev — skip
        if total != prev_total:        # record only the points where the set changed
            by_date[cs] = total        # last change on a given date wins
            prev_total = total
    return sorted(by_date.items())


# --------------------------------------------------------------------------- ritual conformance (ADR-0053)

# Self-governance R3: ritual compliance is MINED from the artifacts the rituals already
# produce, never self-reported. Exceptions are bounded to a recent window so the report
# stays actionable; historical debt is an EVIDENCE.md count, not an attention line.
CONFORMANCE_RECENT_DAYS = 30   # exception window; older violations are evidence-only
UNCLOSED_GRACE_HOURS = 48      # a session younger than this is "live", never "unclosed"
SANE_SESSION_HOURS = 24        # a closed session longer than this is a stamp/clock bug
ROLEDOC_WINDOW_DAYS = 90       # bounded git window for the role-doc bump check
ROLEDOC_MAX_CONTENT_READS = 50 # cap on per-changed-commit content reads (P19)
STATUS_CLOSE_LAG_DAYS = 1      # close ritual must stamp STATUS within a day of close
ROADMAP_STALE_DAYS = 7         # ROADMAP as-of may trail the last close by this much
APPLIED_STAMP_RECENT_DAYS = 30 # unstamped applied briefs newer than this are exceptions
SPOT_AUDIT_CADENCE_DAYS = 30   # monthly Auditor-class spot audit (ADR-0053 mechanism 2)
SPOT_AUDIT_GRACE_DAYS = 5

# ── WI-0087: curation stops resting on someone remembering ──────────────────────
# Days since canon last absorbed anything before the debt is worth the operator's attention.
# the operator's call, session ~174. The argument for instrumenting this at all is the
# federation's own: this system has systematically retired "remembering" everywhere
# else — adoption became the ADR-0050 runner, liveness became the heartbeat sidecar —
# and curation was the last load-bearing chore still resting on human memory. It is
# also the one that produces canon, which makes it the worst candidate for a chore.
# `retention-enforced-by-code` pointed at our own core loop.
CURATE_CADENCE_DAYS = 14
_CURATE_STAMP_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-\d{6}Z$")
JOURNAL_BOILERPLATE = {"- Session opened."}  # the turn-1 stub body (ADR-0051)

# ── WI-0207: a missing stamp is a gap only where a writer could have written it ──
# `apply: manual` briefs were scored as audit gaps from the day this check shipped, and
# every one of them was structurally incapable of satisfying it. `session.py
# file_applied_brief()` — the only `applied:` writer that existed then — runs on the
# auto-apply path ONLY, so a brief the Architect applied by hand could never carry the
# stamp the check demanded. Every brief firing `applied-unstamped` was, by
# construction, an `apply: manual` one.
#
# THE FIX IS NOT AN EXEMPTION. `ship-the-detector-with-the-capability` warns that an
# exemption defaulting to pass does not merely fail to detect a gap, it certifies one —
# and exempting manual briefs outright would leave a manual apply with no receipt at
# all, permanently. What changed is the SUBSTRATE: WI-0223 gave `curate/adopt-runner.py`
# a `file_and_stamp_brief()` that files pending/ → applied/ and stamps the receipt
# itself, MINTING a frontmatter fence where the brief has none. From the day that writer
# started filing, a manual brief CAN be stamped and a missing stamp is a real gap again.
#
# The cutover is the operator's, ruled on WI-0256 (session ~241) for the sibling guard in
# `session.py`, and is hardcoded here for the reason he gave there: the legacy briefs are
# then structurally out of scope rather than exempted by a flag someone can later flip.
# Both surfaces must agree, or one brief is a violation on one and clean on the other.
MANUAL_STAMP_CUTOVER = date(2026, 9, 4)  # adopt-runner began filing-and-stamping

# ── WI-0207: the reaper working is not a lapse ──────────────────────────────────
# A journal naming one of these as its closer was closed by MACHINERY after the session
# that owned it was already gone — the janitor aging out an evidence-less record, `reap`
# closing a dropped lane at its exit marker, `resolve-orphan`, a removed worktree. A
# session that died cannot narrate itself, and the span between its last live stamp and a
# close written hours later is the evidence being read CORRECTLY, not a clock bug.
# Scoring either as a ritual violation scores the reaper's own correct behaviour.
#
# In practice most narrative-less closes are machine closes (`reap`, `janitor`,
# `resolve-orphan`), and scoring them buries the few human closes worth reading — the exact failure a conformance metric exists to prevent.
#
# `session.py end` is deliberately ABSENT. It is code, but it is the LIVE session's own
# close, and a session that reached it could have written a narrative. So is
# `reconstruction`: an Architect rebuilding a close by hand is a person, and leaving it
# scored keeps the honest direction — a record we are unsure about stays visible.
MACHINE_CLOSERS = ("janitor", "reap", "resolve-orphan", "worktree-remove")


def _machine_closed(closed_by):
    """True when `closed-by` names machinery that closed an already-dead session.

    Matched on the LEADING TOKEN, because these closers append their reason in
    parentheses (`reap (dropped — closed at its exit marker)`). An absent or unparseable
    `closed-by` reads as False — a journal that does not say who closed it is scored,
    which is the direction that keeps an unknown visible instead of quietly excusing it
    (`declare-what-a-check-assumes`)."""
    return (closed_by or "").strip().split("(", 1)[0].strip().lower() in MACHINE_CLOSERS


def _brief_was_stampable(brief, cutover=MANUAL_STAMP_CUTOVER):
    """Could ANY writer have stamped this applied brief? A missing stamp is an audit gap
    only where the answer is yes; where it is no, the check scores the substrate's own
    shape as a lapse and the count stops meaning anything (WI-0207).

    `mtime` is the filing-time PROXY the miner already trusts for its 30-day recency
    window (see `mine_applied_briefs`), so keying the cutover on it adds no assumption the
    check was not already making. A brief with no readable mtime dates to the epoch and
    reads as pre-cutover — an undateable brief stays OUT of the count rather than having a
    gap invented for it.

      * Filed on or after the cutover → stampable, WHATEVER its shape. `adopt-runner`'s
        `_inject_stamp` mints a fence for a fenceless brief, so `has_frontmatter` no
        longer bounds what can carry a receipt. (MEASURED 2026-09-13: zero post-cutover
        fenceless briefs exist today, so this widening costs nothing now and keeps the
        check's teeth for the class that can next occur.)
      * Filed before it, WITH a fence, and NOT `apply: manual` → stampable. The zero-touch
        engine has stamped fenced auto-applies since it shipped. This is the class the
        check was always right about, and its behaviour is unchanged.
      * Anything else → NOT stampable: a pre-schema legacy brief with no fence (the engine
        silently skips the stamp), or a pre-cutover `apply: manual` brief applied by hand
        when no writer existed that could have stamped it.

    A brief declaring NO apply mode is not read as manual. `apply:` became mandatory at
    ADR-0049, so silence means the brief predates the partition; reading it as `manual`
    would retroactively drop a class the check has been scoring correctly."""
    try:
        filed = date.fromtimestamp(brief.get("mtime") or 0)
    except (OverflowError, OSError, ValueError):
        return False
    if filed >= cutover:
        return True
    if not brief.get("has_frontmatter"):
        return False
    return (brief.get("apply_mode") or "").lower() != "manual"


# ── WI-0159: escalations to the user, paired with the fallback-queue depth ──────
# R5 of the 2026-08-19 consultant brief, deferred by ADR-0099 D4. TWO numbers that mean
# something only together, and either one read alone gets the answer backwards.
#
# THE SOURCE IS THE JOURNAL, AND STRUCTURALLY RATHER THAN BY PHRASE. The item proposed
# mining prose for "handed operator" / "asked operator to run" / "operator ran the one line".
# Measured against the federation's own 137 journals before building on it, those three
# phrases appear in 1, 1 and 7 files — so a counter built on them reads near-zero across
# the night that produced WI-0274's fifty relayed answers, reporting the failure as a
# win. That is precisely what D4 refused to half-ship ("a metric that reports zero
# because nothing feeds it ... certifies the problem as solved").
#
# What the journals DO carry is two authored headings — `### Open questions for the
# user` (87 of 137 journals) and `### Decisions I made without you, for review` (8). A
# heading is a structural boundary, which is the same property that already lets
# `mine_journal_sessions` judge boilerplate without judging prose: mechanism, not
# judgment (P15). The item's own framing — "belongs with the ADR-0053 miner rather than
# in metrics.py directly" — rests on a distinction that does not exist, because the
# ADR-0053 miner IS this module (adr/0053:110 rejected a separate script). The real
# split is the one taken here: this counts, the Auditor classifies.
ESCALATION_WINDOW_DAYS = 30          # window both numbers are read over
ESCALATION_QUEUE_STALE_DAYS = 30     # a fallback-queue item older than this is ageing
# The group ADR-0099 D2 named as its own falsifier ("If the `lane-last-mile` queue grows
# or ages, the group is wrong and moves to `next` on that evidence rather than on this
# argument"). There is no `parked` status and no first-class queue — the ladder's step
# (c) is "file or update the work item for the missing verb", and the free-text `group:`
# field is what identifies those items today. Measuring the group the ADR named is the
# measurement it asked for, not a proxy invented here.
FALLBACK_QUEUE_GROUP = "lane-last-mile"
# Prefix-matched, lower-cased: `### Open questions` and `### Open questions for the user`
# are one section, and journals use both spellings.
#
# RE-EXPORTS, not copies (WI-0330). The heading rule and the entry-split now have one
# home, `sessionlib/journal.py`, because a second reader of the same ledger arrived —
# the `decisions` verb, which needs the entry TEXTS where this module needs their count.
# The direction is forced: metrics already imports the harness for the layout resolver,
# so the shared read goes down into the harness and comes back up here. These names stay
# spelled as they were; every caller and every test that reaches for `metrics.
# DECISION_HEADINGS` or `metrics._ledger_section` is untouched by the move.
ESCALATION_HEADINGS = _session.LEDGER_ESCALATION_HEADINGS
DECISION_HEADINGS = _session.LEDGER_DECISION_HEADINGS
# WI-0278 closes D4's remaining half. The two headings above are AUTHORED at close, and
# that is the bias the item measured: on 2026-09-04 the federation's nineteen journals
# recorded eleven escalations between them while roughly fifty relayed answers actually
# reached operator. The third heading is MACHINE-written by `session.py end` from the session's
# own escalation ledger, so what it counts is what the Notification hook observed rather
# than what a session still remembered at the end of it.
#
# STILL ONE SOURCE, WHICH IS THE ITEM'S OWN CONSTRAINT ("do not make this a second source
# of truth for the count"). This module does not read the ledger sidecar and must not: the
# sidecar is gitignored per-machine residue the janitor drops, the JOURNAL is the tracked
# record, and metrics mines journals fleet-wide over members whose harness version it does
# not control. The sidecar feeds the journal; the journal feeds this.
MIDSESSION_HEADINGS = _session.LEDGER_MIDSESSION_HEADINGS
WI_OPEN_STATUSES = ("open", "in-progress", "held")   # everything but done/superseded
# HORIZONTAL whitespace only, both sides. `\s*` after the colon matches the NEWLINE
# too, so an EMPTY field (`- blocked-by: ` with nothing after it, which is what
# `_wi_render_item` writes for most items) swallows the line break and captures the NEXT
# field's line as its value — silently mis-reading `group` and `status` on exactly the
# items that leave a field blank. Caught by reading the live store rather than a fixture.
_WI_FIELD_RE = re.compile(r"^-[ \t]*([a-z-]+):[ \t]*(.*)$", re.M)
_WI_FILE_RE = re.compile(r"^WI-\d+.*\.md$")

_FM_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
# Both fleet spellings: `**Version:** 4.5.6` (colon inside the bold — the common
# form) and `**Version**: `v1.2.3`` (colon outside — a variant some role docs use; the session-67 variant
# that already taught the apply engine this lesson). Fix the engine, not the member.
_VERSION_LINE_RE = re.compile(r"(?m)^\*\*Version:?\*\*:?\s*(.+?)\s*$")
_ASOF_RE = re.compile(r"\*\*As of:\*\*\s*(\d{4}-\d{2}-\d{2})")
_AUDIT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9-]+\.md$")
_OID_RE = re.compile(r"^[0-9a-f]{40}$")


def _journal_fm_body(text):
    """(frontmatter dict, body) for a `---`-fenced journal. Local mirror of
    session.py's parse_journal — metrics stays standalone over the fleet (it must parse
    a member's journals regardless of that member's harness version, same reason the
    stamp parser above is local). Malformed -> ({}, whole-text), never raises."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fm, "\n".join(lines[i + 1:])
        m = _FM_LINE_RE.match(line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return {}, text


def _iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ledger_section(body, prefixes):
    """Entry count under the first `### ` heading whose text starts with one of
    `prefixes`, or **None when the journal has no such section at all** (WI-0159).

    None is not zero, and a caller that renders it as zero has built the instrument this
    metric exists to avoid (`declare-what-a-check-assumes`). The two headings are
    AUTHORED, not templated — the turn-1 stub carries only What happened / State at close /
    Parked question / Notes filed — so an absent section usually means "nothing to record"
    and sometimes means "recorded somewhere else". The aggregate reports how many journals
    were silent so the difference stays visible instead of being averaged away.

    Counting entries rather than judging prose is the whole point: `###` is a structural
    boundary, so this is mechanism (P15). What KIND of escalation each entry is —
    a command handed over, a machine-bound handoff, a design question that should have
    been decided in-lane — is judgment, and stays with the Auditor pass (ADR-0053
    mechanism 2 / OPS-0006), which is the split WI-0159 asked to have settled first.

    The SPLIT now lives in `sessionlib/journal.py` (WI-0330); this is the counting view
    of it, kept under its own name so nothing that mines through it had to move. The
    number is unchanged by construction — `ledger_entries` splits at exactly the
    positions this function used to count."""
    return _session.ledger_count(body, prefixes)


#: A journal FILENAME: `durable_session_id`'s shape (sessionlib/config.py
#: `durable_session_id` — `<utc-compact>-<machine>-<short-random>`), which is the only
#: thing left to recognise a journal by once its frontmatter is gone. This is a local
#: mirror for the same reason `_journal_fm_body` is: metrics parses a member's journals
#: regardless of that member's harness version, so it may not import the harness's copy.
_JOURNAL_NAME_RE = re.compile(r"^\d{8}T\d{4}Z-[a-z0-9]+-[0-9a-f]+\.md$")


def mine_unreadable_journals(repo):
    """[(filename, named reason)] for files that ARE journals by name but that
    `mine_journal_sessions` cannot read (WI-0332).

    WHY THIS EXISTS AS A SEPARATE MINER. `mine_journal_sessions` skipped anything without
    a `session-id` under the comment *"README or stray file, not a journal"* — an
    assumption, stated as a fact, that is false for the one case that matters. A journal
    rewritten with a WHOLE-FILE write loses its `---` block and lands in exactly that
    branch, so the session left the escalation ledger, the conformance counts and the
    session totals with no line anywhere saying a file had been skipped
    ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes):
    "couldn't tell" rendered identically to "checked and it's fine").

    The filename is what separates the two: a README genuinely is a stray, and a file
    named like a session-id with no frontmatter is a destroyed record. Nothing else can
    tell them apart once the block is gone, which is why the discriminator is the name.

    This is the fleet-side half of WI-0332 — the harness-side guard in
    `sessionlib/journal.py` cannot reach here, because metrics mines members whose
    harness may predate it. Never raises."""
    d = repo / (_session.member_layout(repo, "journal_dir") or "sessions/journal")
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if not _JOURNAL_NAME_RE.match(p.name):
            continue                    # genuinely a stray — the name was never a session id
        try:
            fm, _body = _journal_fm_body(p.read_text(errors="replace"))
        except OSError as exc:
            out.append((p.name, f"could not be read — {exc}"))
            continue
        if "session-id" not in fm:
            out.append((p.name, "named like a session but carries no `session-id` "
                                "frontmatter — a whole-file write destroys the `---` "
                                "block; this session is in NO compiled view"))
    return out


def mine_journal_sessions(repo):
    """Session records from the journal model (ADR-0051): one dict per
    journal file with a `session-id` frontmatter field. `boilerplate` is True
    when the body carries nothing beyond the turn-1 stub (section headers + 'Session
    opened.') — deterministic because the journal boundary is structural, unlike
    handoff-era prose (named unminable for that era, never judged). `escalations` and
    `decisions` (WI-0159) are entry counts under two authored headings, or None where
    the journal has no such section — see `_ledger_section`.

    The directory is the member's DECLARED `journal_dir`, not a literal: a member that
    moves its journals would otherwise mine zero sessions here and read as a fleet with
    no escalations and perfect conformance — the WI-0029 gap, in the one place where
    reading zero is indistinguishable from reading clean."""
    d = repo / (_session.member_layout(repo, "journal_dir") or "sessions/journal")
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            fm, body = _journal_fm_body(p.read_text(errors="replace"))
        except OSError:
            continue
        if "session-id" not in fm:
            # A stray file OR a journal whose frontmatter was destroyed — this branch
            # cannot tell, and saying which was the WI-0332 defect. It still skips (the
            # record genuinely cannot be mined), but `mine_unreadable_journals` now
            # answers the question this `continue` used to assert an answer to.
            continue
        # WI-0278: strip the MACHINE-written escalation section first. `boilerplate`
        # means the ARCHITECT wrote nothing beyond the turn-1 stub, and every line that
        # section contributes was folded in by `session.py end`. Counting it as content
        # would make a session that recorded nothing — but interrupted operator once — read
        # as an authored journal on the conformance surface, which is a metric asserting
        # substance where there is none.
        # WI-0401 adds the SECOND machine-written section on the same argument. `end`
        # folds in the goal disposition; not stripping it here would make every session
        # that had a goal read as an authored journal, which is the same metric asserting
        # substance where there is none, arriving through a new door.
        content = [ln.strip() for ln in
                   _session.strip_goal_disposition(
                       _session.strip_midsession_escalations(body)).splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
        out.append({
            "path": p,
            "started": _iso_dt(fm.get("started")),
            "ended": _iso_dt(fm.get("ended")),
            "boilerplate": all(ln in JOURNAL_BOILERPLATE for ln in content),
            # WHO closed this, verbatim (`reap (...)`, `janitor (...)`, `session.py
            # end`, or None where the frontmatter does not say). The conformance mine
            # needs it to tell a lapse from the reaper doing its job — see
            # `_machine_closed` (WI-0207).
            "closed_by": fm.get("closed-by"),
            "escalations": _ledger_section(body, ESCALATION_HEADINGS),
            "decisions": _ledger_section(body, DECISION_HEADINGS),
            # WI-0278. Absent on every journal written before the fold shipped, and that
            # absence reads as None — unknown, never zero — for the same reason the two
            # above do. A member still on an older harness mines the same way.
            "midsession_escalations": _ledger_section(body, MIDSESSION_HEADINGS),
            # WI-0405. The same two sections' entry TEXTS, for the intervention split.
            # Carried beside the counts rather than replacing them: `len(entries)` IS the
            # count by `ledger_entries`' own contract, but the counts are what every
            # existing caller reads and a refactor must not be able to move a metric.
            # None here means the same thing it means above — no section, so unknown.
            "escalation_entries": _session.ledger_entries(body, ESCALATION_HEADINGS),
            "midsession_entries": _session.ledger_entries(body, MIDSESSION_HEADINGS),
            # ADR-0093 D1's frontmatter, as the per-entry attribution's fallback: an
            # entry naming no item is charged to whatever the session held.
            "work_items": [s.strip() for s in (fm.get("work-items") or "").split(",")
                           if s.strip()],
        })
    return out


def summarize_ledger(journals, today, window_days=ESCALATION_WINDOW_DAYS):
    """Number (1) of WI-0159: escalations to the user, and decisions recorded in-lane,
    across the sessions that STARTED inside the window.

    Both totals sum only over journals that actually carry the section; `silent` counts
    the rest, and the renderer says so. Folding silent journals in as zeros would make
    the headline number fall every time a session forgot to write the section — the
    metric would improve fastest exactly when the record got worse.

    A journal with no `started` stamp is dated `unknown` and excluded from the window
    rather than assumed recent: a session whose date cannot be read is not evidence
    about this month."""
    cutoff = today - timedelta(days=window_days)
    considered = [j for j in journals
                  if j.get("started") is not None and j["started"].date() > cutoff]
    esc = [j["escalations"] for j in considered if j.get("escalations") is not None]
    dec = [j["decisions"] for j in considered if j.get("decisions") is not None]
    # WI-0278. The headline `escalations` below is now close-time PLUS mid-session, and it
    # has to be: the exception row branches on that one number, so a session that
    # interrupted operator twenty times and wrote nothing at close used to render as
    # "escalations at ZERO" — the metric reporting its own blind spot as a clean month.
    #
    # The two halves stay separately reported anyway, because WI-0274's finding IS the
    # ratio between them (eleven written against roughly fifty answered). A single summed
    # number would close the blind spot and destroy the comparison that found it.
    mid = [j["midsession_escalations"] for j in considered
           if j.get("midsession_escalations") is not None]
    # WHICH sessions, not just how many. The item's acceptance condition is that a
    # non-zero month "names the session that found it" — a bare count sends the reader
    # back to grep, and the whole argument for this metric is that grep is what fails.
    def _total(j):
        return (j.get("escalations") or 0) + (j.get("midsession_escalations") or 0)
    named = sorted(((getattr(j.get("path"), "name", "?"), _total(j))
                    for j in considered if _total(j)),
                   key=lambda kv: kv[0], reverse=True)
    return {
        "window_days": window_days,
        "sessions": len(considered),
        "escalations": sum(esc) + sum(mid),
        "escalations_at_close": sum(esc),
        "escalations_midsession": sum(mid),
        "escalation_sessions": len([j for j in considered if _total(j)]),
        "decisions": sum(dec),
        "decision_sessions": len([n for n in dec if n]),
        # journals in the window carrying NO escalation section — not zero, unknown
        "silent": len(considered) - len(esc),
        # ...and the same fact about the machine-written half. Separate because the two
        # are silent for different reasons: a missing authored section means a session
        # wrote nothing, a missing folded one means the session predates the fold (or its
        # ledger could not be read). Averaging them would report those as one thing.
        "midsession_silent": len(considered) - len(mid),
        "undated": len([j for j in journals if j.get("started") is None]),
        "named": named,
    }


# ---- the intervention split (WI-0405, consultant brief 2026-09-07 R3) -----------------
#
# The brief asks for *avoidable interventions per completed item*, where an avoidable
# intervention is "operator had to correct or unblock execution because the system mishandled
# an already-established requirement, authority, state, or procedure", and a legitimately
# **required decision** is counted separately with no target of zero. Both populations are
# already in the journals; what was missing was a way to tell them apart.
#
# A MARKER, NEVER THE PROSE. Reading an entry's text to decide whether operator NEEDED to be
# involved is judgment, and this module's standing split is `this counts, the Auditor
# classifies` — the same rule that keeps `decision_rows` taking `decided_by` off the
# heading rather than sniffing for "operator approved" (WI-0274's false-provenance finding).
# So the classification is AUTHORED, as a bracketed tag on the ledger entry, and this
# reads the tag. That also gives the brief the provenance it asked for: the tag sits on
# the journal line the event came from, and the weekly review (OPS-0007) is where a
# disputed one is corrected.
#
# UNTAGGED COUNTS AS AVOIDABLE, WHICH IS THE WHOLE DESIGN. The brief's binding constraint
# is that the measure must not be improvable by behaving worse, and the obvious hole in a
# tag-based scheme is that tagging nothing scores zero — a metric that reads perfect for
# everyone who has not adopted it, which is the `derive-a-checks-subjects-from-the-
# authority` failure in its output-based form. Defaulting to `avoidable` inverts that: the
# only way to lower the number is to author a `[required]` claim that a human can read and
# overturn. Silence costs you, and an unaudited exemption is not available.
#
# The untagged count is ALSO reported on its own, because a headline made mostly of
# untagged entries is a different statement from one made of audited ones, and a reader
# who cannot tell them apart has a number without its mechanism.
INTERVENTION_REQUIRED_TAGS = ("[required]", "[required decision]")
INTERVENTION_AVOIDABLE_TAGS = ("[avoidable]",)
#: The three buckets, in report order. `untagged` is not a third KIND of intervention —
#: it is the coverage of the classification, and it is summed INTO `avoidable` for the
#: headline while staying visible as itself.
INTERVENTION_BUCKETS = ("avoidable", "required", "untagged")


def classify_intervention(entry):
    """`'required'` | `'avoidable'` | `'untagged'` for one ledger entry's text.

    Tag-matching only, case-insensitively, anywhere in the entry — never a read of what
    the entry says. An entry carrying BOTH tags resolves to `avoidable`: a contested
    classification is the one a review should see, and resolving a contradiction in the
    direction that lowers the number would hide it."""
    text = (entry or "").lower()
    if any(t in text for t in INTERVENTION_AVOIDABLE_TAGS):
        return "avoidable"
    if any(t in text for t in INTERVENTION_REQUIRED_TAGS):
        return "required"
    return "untagged"


def intervention_events(journals, today, window_days=ESCALATION_WINDOW_DAYS):
    """Every intervention in the window as `{kind, items, journal, section}`, one per
    ledger entry across BOTH escalation sections.

    Both sections, because the brief's subject is "operator had to step in", and a question
    raised mid-flight and answered interrupts him exactly as much as one a session ends
    holding — WI-0278 exists because the authored half alone under-counted by roughly
    five to one. The `section` field keeps them distinguishable for anyone who wants the
    WI-0274 ratio back.

    `items` is the entry's OWN cited work-item ids where it names any, and the journal's
    `work-items:` frontmatter otherwise — an entry that names nothing is charged to what
    the session held, and one that names nothing in a session that held nothing is
    charged to no item at all rather than to a guess."""
    cutoff = today - timedelta(days=window_days)
    out = []
    for j in journals:
        started = j.get("started")
        if started is None or started.date() <= cutoff:
            continue
        held = list(j.get("work_items") or [])
        for section in ("escalation_entries", "midsession_entries"):
            for entry in (j.get(section) or []):
                cited = sorted(set(_session._LEDGER_ITEM_ID_RE.findall(entry)))
                out.append({
                    "kind": classify_intervention(entry),
                    "items": cited or held,
                    "journal": getattr(j.get("path"), "name", "?"),
                    "section": "midsession" if section.startswith("mid") else "at-close",
                })
    return out


def summarize_interventions(journals, today, window_days=ESCALATION_WINDOW_DAYS):
    """The intervention split over the window. Counts only — the RATE per completed item
    needs the work-item store's history, which belongs to whoever owns that read.

    Keys: the three `INTERVENTION_BUCKETS` counts, `total`, `avoidable_headline`
    (avoidable + untagged, the number the brief's measure is about), `sessions` in the
    window, `silent` (journals with no escalation section at all — unknown, never zero,
    the same contract `summarize_ledger` keeps), and `events` for a caller that needs the
    per-item attribution."""
    events = intervention_events(journals, today, window_days)
    cutoff = today - timedelta(days=window_days)
    considered = [j for j in journals if j.get("started") is not None
                  and j["started"].date() > cutoff]
    counts = {b: len([e for e in events if e["kind"] == b]) for b in INTERVENTION_BUCKETS}
    return {
        "window_days": window_days,
        "sessions": len(considered),
        "silent": len([j for j in considered if j.get("escalations") is None
                       and j.get("midsession_escalations") is None]),
        "total": len(events),
        "avoidable_headline": counts["avoidable"] + counts["untagged"],
        "events": events,
        **counts,
    }


def _wi_git_dates(repo, store_rel):
    """`{path: (first-add ISO, last-touch ISO)}` for the work-item store, from ONE walk
    of git history — or None when git cannot answer.

    Both ends matter and they answer different questions: the first add is when an item
    was FILED (its age), the last touch is the best available proxy for when it was
    CLOSED, since a done item's final commit is the one that set `status: done`.

    A work item carries no creation timestamp: `_wi_render_item` writes status, section,
    blocked-by, group, source, impact, scope, waiting-on and version, and no date. The
    list is the authority for the claim that follows it, so it is kept current as fields
    arrive — WI-0408 added the last of them. So the age half of
    WI-0159's second number has to come from history. ONE walk of the store's history
    costs ~40ms measured against the federation's 2.7k path-touch records; a per-item
    `git log` would be 250-odd subprocesses on a SessionStart hook, which is the runaway
    `cap-what-can-run-away` exists to refuse.

    None, never a partial map: a truncated map would silently make the oldest item look
    younger than it is, which is the direction that reports the queue as healthy."""
    cmd = ["git", "-C", str(repo), "log", "--reverse",
           "--format=%x00%aI", "--name-only", "--", store_rel]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    dates, cur = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("\x00"):
            cur = line[1:].strip() or None
            continue
        path = line.strip()
        if not path or not cur:
            continue
        first, _ = dates.get(path, (cur, cur))
        dates[path] = (first, cur)          # --reverse, so last write wins the tail
    return dates


def mine_fallback_queue(repo, today, group=FALLBACK_QUEUE_GROUP,
                        window_days=ESCALATION_WINDOW_DAYS,
                        stale_days=ESCALATION_QUEUE_STALE_DAYS):
    """Number (2) of WI-0159: the depth and age of the fallback queue that
    `never-route-your-own-work-through-the-user` routes blocked work into.

    Returns None when the member has no work-item store — a member with no store has no
    queue to measure, which is a different fact from an empty queue and must not render
    as a healthy zero. `oldest_days` / `added_in_window` are None when git could not
    date the store, for the same reason.

    `ids` is carried so drift is visible: the group is a free-text field, so an item
    that wandered in inflates the depth, and the reader can see which items are being
    counted rather than trusting the number."""
    store_rel = _session.member_layout(repo, "work_items") or "work-items"
    d = repo / store_rel
    if not d.is_dir():
        return None
    open_items, closed_items, group_ever_used = [], [], False
    for f in sorted(d.glob("*.md")):
        if not _WI_FILE_RE.match(f.name):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        fields = dict(_WI_FIELD_RE.findall(text))
        if fields.get("group", "").strip() != group:
            continue
        # Seen at ANY status, including done/superseded: whether this member MARKS its
        # fallback queue is a different question from whether the queue is empty today,
        # and a store that has never used the group must not report a healthy zero.
        group_ever_used = True
        if fields.get("status", "open").strip() not in WI_OPEN_STATUSES:
            closed_items.append(f)
            continue
        open_items.append(f)
    dates = _wi_git_dates(repo, store_rel)

    def _span(f):
        pair = None if dates is None else dates.get(f"{store_rel}/{f.name}")
        if not pair:
            return None, None
        return _iso_dt(pair[0]), _iso_dt(pair[1])

    ages, added = [], 0
    for f in open_items:
        first, _last = _span(f)
        if first is None:
            continue
        age = (today - first.date()).days
        ages.append(age)
        if age <= window_days:
            added += 1
    closed = 0
    for f in closed_items:
        _first, last = _span(f)
        if last is not None and (today - last.date()).days <= window_days:
            closed += 1
    return {
        "group": group,
        "depth": len(open_items),
        "ids": [f.name.split("-")[0] + "-" + f.name.split("-")[1] for f in open_items],
        "oldest_days": max(ages) if ages else (None if dates is None else 0),
        "added_in_window": None if dates is None else added,
        # Closed in the same window, so "growing" is NET flow. Without this the queue
        # reads as growing whenever anything was filed — including a fortnight in which
        # twice as much was drained — and a loud row that is always on is a loud row
        # nobody reads, which costs more than the signal is worth.
        "closed_in_window": None if dates is None else closed,
        # the queue is UNDATED when git could not answer; the caller must not read that
        # as a young queue (`declare-what-a-check-assumes`)
        "dated": dates is not None and len(ages) == len(open_items),
        "stale_days": stale_days,
        "group_used": group_ever_used,
    }


def _roledoc_name(repo):
    """The repo's role-doc filename from its own session.config.json, or None."""
    try:
        cfg = json.loads((repo / "session.config.json").read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    v = cfg.get("role_doc") if isinstance(cfg, dict) else None
    return v if isinstance(v, str) and v else None


def mine_roledoc_bumps(repo, role_doc, window_days=ROLEDOC_WINDOW_DAYS):
    """Commits in the window that changed the role doc WITHOUT changing its
    `**Version:**` line — the versioned-role-doc-and-changelog habit (C4), mined from
    git instead of self-reported. Returns {"checked": n-changed, "violations": [{short,
    date}]}, or None on any git failure (fail-soft; the axis is simply not checked).

    Cheap by construction (the ADR-0052 batch technique): a pathspec-free first-parent
    log for the window's revs, ONE `cat-file --batch-check` resolving the role-doc blob
    id at every rev (a pathspec log would force per-commit tree diffs — pathological on
    the network volume), then content reads only for the few revs where the blob id
    actually changed (capped, memoized). First-parent keeps the walk linear so
    consecutive-rev comparison means "changed by this commit"."""
    try:
        log = subprocess.run(
            ["git", "-C", str(repo), "log", f"--since={window_days}.days",
             "--first-parent", "--format=%H %h %cs"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if log.returncode != 0:
        return None
    revs = []
    for line in log.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            revs.append(parts)
    if not revs:
        return {"checked": 0, "violations": []}
    revs.reverse()  # oldest first
    # Boundary: the window's oldest rev compares against its parent (outside the
    # window); an unresolvable parent (root commit) yields "missing" -> treated as
    # file-absent, which suppresses the comparison at the boundary.
    queries = [f"{revs[0][0]}^:{role_doc}"] + [f"{full}:{role_doc}" for full, _, _ in revs]
    try:
        bc = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname)"],
            input="\n".join(queries) + "\n", capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = bc.stdout.splitlines()
    if len(lines) != len(queries):
        return None
    oids = []
    for ln in lines:
        tok = ln.split()[0] if ln.split() else ""
        oids.append(tok if _OID_RE.fullmatch(tok) else None)

    changed = []  # (short, commit-date, prev-oid, new-oid)
    prev = oids[0]
    for (_full, short, cs), oid in zip(revs, oids[1:]):
        if oid != prev:
            changed.append((short, cs, prev, oid))
        prev = oid

    # ONE `cat-file --batch` resolves every needed blob's content (per-oid `cat-file
    # blob` subprocesses cost ~1s each over the network volume — 50 of them made the
    # startup path pathological; one batched pipe is fast).
    needed = []
    for short, cs, a, b in changed[:ROLEDOC_MAX_CONTENT_READS]:
        for oid in (a, b):
            if oid is not None and oid not in needed:
                needed.append(oid)
    versions = _versions_by_oid(repo, needed)

    violations = []
    for short, cs, a, b in changed[:ROLEDOC_MAX_CONTENT_READS]:
        if a is None or b is None:
            continue  # creation or deletion — not a bump question
        try:
            cd = date.fromisoformat(cs)
        except ValueError:
            continue
        if versions.get(a) == versions.get(b):
            violations.append({"short": short, "date": cd})
    return {"checked": len(changed), "violations": violations}


def _versions_by_oid(repo, oids):
    """{oid: version-string|None} via one `cat-file --batch` call. Byte-precise parse
    of the batch stream (`<oid> blob <size>\\n<content>\\n`, or `<oid> missing\\n`);
    any error -> {} (fail-soft, the comparison then reads None == None -> no flag)."""
    if not oids:
        return {}
    try:
        bc = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            input="\n".join(oids).encode() + b"\n", capture_output=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if bc.returncode != 0:
        return {}
    out, buf, pos = {}, bc.stdout, 0
    for oid in oids:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        header = buf[pos:nl].decode(errors="replace").split()
        pos = nl + 1
        if len(header) >= 3 and header[1] == "blob":
            try:
                size = int(header[2])
            except ValueError:
                break
            m = _VERSION_LINE_RE.search(buf[pos:pos + size].decode(errors="replace"))
            out[oid] = m.group(1) if m else None
            pos += size + 1  # skip content + its trailing newline
        else:
            out[oid] = None  # "missing" (or non-blob) — treated as no version line
    return out


def mine_conformance(repo, sessions, journals, status_fm, applied_briefs,
                     roledoc_bumps, today, now_ts):
    """One system's ritual-conformance mine (ADR-0053) — pure read over already-mined
    inputs. Returns {"recent": {kind: [detail…]}, "historic": {kind: count},
    "informational": {kind: [detail…]}}: recent violations (the CONFORMANCE_RECENT_DAYS
    window) become the exception line; historic debt is counted for EVIDENCE.md only.

    `informational` is the third answer, and it is not a softer kind of violation
    (WI-0207). It holds facts the mine can see and a reader should have — a journal the
    janitor closed, and the impossible duration that correct close produces — which are
    NOT lapses by anyone and must not be scored as such. Folding them into `recent` is
    what made this metric unreadable: 11 machine-closed journals were burying the 4
    human closes actually worth looking at. Dropping them instead would hide the reaper's
    behaviour entirely, so they are counted and shown, just never scored."""
    recent_cut = now_ts - CONFORMANCE_RECENT_DAYS * 86400
    grace_cut = now_ts - UNCLOSED_GRACE_HOURS * 3600
    recent, historic, informational = {}, {}, {}

    def _add(kind, detail, is_recent, scored=True):
        # `scored=False` routes to `informational` — and only inside the window, because
        # an unscored fact outside it is not news, not debt, and not a violation; there
        # is nothing for a historic counter of it to mean.
        if not scored:
            if is_recent:
                informational.setdefault(kind, []).append(detail)
            return
        if is_recent:
            recent.setdefault(kind, []).append(detail)
        else:
            historic[kind] = historic.get(kind, 0) + 1

    # Session-close discipline: journals are authoritative where they exist (the
    # handoff is COMPILED from them — reading both would double-count); the handoff
    # stamp pairs cover the pre-journal / handoff-model members.
    latest_close = None
    if journals:
        for j in journals:
            st, en = j["started"], j["ended"]
            if st is not None and en is None and st.timestamp() < grace_cut:
                _add("unclosed", f"journal {j['path'].name} open since {st.date()}",
                     st.timestamp() >= recent_cut)
            if st is not None and en is not None:
                if latest_close is None or en.timestamp() > latest_close.timestamp():
                    latest_close = en
                # A machine close is the reaper reading death evidence, not a session
                # behaving badly: it could not have narrated itself, and its span is the
                # gap between its last live stamp and the sweep that found it. Both
                # facts stay visible; neither is scored (WI-0207).
                machine = _machine_closed(j.get("closed_by"))
                by = f" [{(j.get('closed_by') or '').strip()}]" if machine else ""
                span_h = (en.timestamp() - st.timestamp()) / 3600.0
                if span_h < 0 or span_h > SANE_SESSION_HOURS:
                    _add("machine-closed-duration" if machine else "insane-duration",
                         f"journal {j['path'].name}: {span_h:.1f}h{by}",
                         en.timestamp() >= recent_cut, scored=not machine)
                if j["boilerplate"]:
                    _add("machine-closed-no-narrative" if machine
                         else "missing-narrative",
                         f"journal {j['path'].name} closed with no narrative{by}",
                         en.timestamp() >= recent_cut, scored=not machine)
    else:
        for s in sessions:
            st, en = s.get("start_dt"), s.get("end_dt")
            if st is not None and en is None and st.timestamp() < grace_cut:
                _add("unclosed", f"handoff session started {st.date()} never closed",
                     st.timestamp() >= recent_cut)
            if st is not None and en is not None:
                if latest_close is None or en > latest_close:
                    latest_close = en
                span_h = (en - st).total_seconds() / 3600.0
                if span_h < 0 or span_h > SANE_SESSION_HOURS:
                    _add("insane-duration", f"session {st.date()}: {span_h:.1f}h",
                         en.timestamp() >= recent_cut)

    # Close ritual refreshed STATUS? (Distinct from cadence staleness: this compares
    # close-to-STATUS, not STATUS-to-today.)
    la_date = None
    la_raw = (status_fm or {}).get("last_active")
    if la_raw:
        try:
            la_date = date.fromisoformat(str(la_raw).strip()[:10])
        except ValueError:
            la_date = None
    if latest_close is not None and la_date is not None:
        lag = (latest_close.date() - la_date).days
        if lag > STATUS_CLOSE_LAG_DAYS:
            _add("status-stale-at-close",
                 f"last close {latest_close.date()} vs STATUS last_active {la_date} "
                 f"({lag}d lag)", True)

    # Close ritual refreshed ROADMAP? Only judged for repos with session history.
    if latest_close is not None:
        rp = repo / "ROADMAP.md"
        if not rp.is_file():
            _add("roadmap-missing", "no ROADMAP.md (standard deliverable, ADR-0030)", True)
        else:
            try:
                head = rp.read_text(errors="replace")[:2000]
            except OSError:
                head = ""
            m = _ASOF_RE.search(head)
            if not m:
                _add("roadmap-stale", "ROADMAP.md has no parseable **As of:** date", True)
            else:
                asof = date.fromisoformat(m.group(1))
                lag = (latest_close.date() - asof).days
                if lag > ROADMAP_STALE_DAYS:
                    _add("roadmap-stale",
                         f"ROADMAP as-of {asof} is {lag}d behind last close "
                         f"{latest_close.date()}", True)

    # Adoption left a record? A schema brief (YAML frontmatter) that the apply engine
    # applied should carry an `applied:` stamp; a missing one is an adoption with no
    # auditable trail. But a pre-schema legacy brief (no frontmatter fence) applied by
    # hand before the zero-touch engine existed is structurally un-stampable — the
    # engine only stamps briefs that open with `---`. Its applied date is not
    # authoritatively recoverable (proposed-edits/ is gitignored data), so a missing
    # stamp there is expected historic residue, not an audit gap. Gate on frontmatter
    # so only genuinely-stampable briefs surface (ADR-0053).
    stamp_cut = now_ts - APPLIED_STAMP_RECENT_DAYS * 86400
    for b in applied_briefs:
        if b.get("applied_on") is None and _brief_was_stampable(b):
            _add("applied-unstamped",
                 f"applied brief {b['path'].name} has no applied-stamp",
                 b.get("mtime", 0) >= stamp_cut)

    # Role-doc changes carried a version bump? (mined in mine_roledoc_bumps)
    # `roledoc_bumps is None` means the check DID NOT RUN — the shallow --status path
    # skips it for cost (see mine()'s `deep`). That is a different fact from "ran and
    # found nothing", and folding the two together is what let the startup line report
    # a smaller number than --exceptions with nothing on either surface saying why
    # (`declare-what-a-check-assumes`). Report it as unchecked instead of as clean.
    unchecked = []
    if roledoc_bumps is None:
        unchecked.append("roledoc-no-bump")
    else:
        for v in roledoc_bumps["violations"]:
            _add("roledoc-no-bump",
                 f"commit {v['short']} ({v['date']}) changed the role doc without a "
                 f"**Version:** change",
                 (today - v["date"]).days <= CONFORMANCE_RECENT_DAYS)

    return {"recent": recent, "historic": historic, "informational": informational,
            "unchecked": unchecked}


def mine_spot_audit(fed_root):
    """Newest spot-audit record date from `audits/YYYY-MM-DD-<system>.md` (ADR-0053
    mechanism 2). The audit itself is judgment; only its RECENCY is mined. Absent dir or
    no conforming records -> {"latest": None} ("never run" — an honest state, not an
    error). fed_root None (tests) -> None, the signal is omitted entirely."""
    if fed_root is None:
        return None
    latest = None
    d = pathlib.Path(fed_root) / "audits"
    if d.is_dir():
        for p in d.glob("*.md"):
            m = _AUDIT_NAME_RE.match(p.name)
            if not m:
                continue
            try:
                dt = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if latest is None or dt > latest:
                latest = dt
    return {"latest": latest}


def mine_curate(fed_root):
    """When canon last absorbed anything, and how much is waiting (WI-0087).

    SOURCE CHOICE, which is the whole design here. The obvious source is
    `curate-runs/REVIEW-*` — and it is the wrong one: that directory is GITIGNORED
    ephemeral staging, so it exists only on the machine that ran the gather. Mining it
    would report "never run" on every other machine and put a false overdue signal on
    every session start there. `curate/seen.json` is TRACKED, carries a `reviewed_on`
    stamp per accepted entry, and moves only when a curate pass actually accepted
    something — so it answers the question the threshold is really asking (*when did
    canon last take anything in?*) and it answers it identically on every machine.

    The pending count comes from `curate/gather.py`, imported lazily and behind a full
    guard: it is the same function the SessionStart hook already runs, but a metrics
    report must never fail because a producer file somewhere is unreadable.

    THREE states for recency, never two: a date, `None` for "never" (an honest state —
    the cursor exists and has never been advanced), and the whole signal omitted when
    `fed_root` is None (tests), rather than fabricated. `pending` is separately
    `None` when it could not be counted, which is NOT the same as zero."""
    if fed_root is None:
        return None
    latest = None
    p = pathlib.Path(fed_root) / "curate" / "seen.json"
    try:
        data = json.loads(p.read_text(errors="replace"))
        for meta in (data.get("reviewed") or {}).values():
            m = _CURATE_STAMP_RE.match(str((meta or {}).get("reviewed_on") or ""))
            if not m:
                continue
            try:
                dt = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if latest is None or dt > latest:
                latest = dt
    except (OSError, ValueError, AttributeError):
        pass
    pending = None
    try:                                   # best-effort; never fail the report
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_curate_gather", pathlib.Path(fed_root) / "curate" / "gather.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # `pending_count()`, not `gather()`: gather returns [] both when the queue is
        # genuinely clear and when no producer file could be read at all, and a 0 on
        # THIS report is the reassuring number on the one surface built to make canon
        # debt visible (WI-0211). `pending_count` returns None for the second case,
        # which the renderer below already distinguishes from zero.
        pending = mod.pending_count()
    except Exception:
        pending = None
    return {"latest": latest, "pending": pending}


# --------------------------------------------------------------------------- cadence config


def load_cadence(path=CADENCE_PATH):
    """The federation-side cadence/parked map. Missing or malformed -> defaults only
    (never an error): a missing config just means "everything on the default cadence"."""
    default = {"defaults": {"cadence_days": DEFAULT_CADENCE_DAYS}, "systems": {}}
    path = pathlib.Path(path)
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return default
    if not isinstance(data, dict):
        return default
    data.setdefault("defaults", {}).setdefault("cadence_days", DEFAULT_CADENCE_DAYS)
    data.setdefault("systems", {})
    return data


def cadence_for(cadence, sid):
    """(cadence_days, parked) for a system id, inheriting defaults."""
    default_days = cadence.get("defaults", {}).get("cadence_days", DEFAULT_CADENCE_DAYS)
    entry = cadence.get("systems", {}).get(sid, {})
    return entry.get("cadence_days", default_days), bool(entry.get("parked", False))


# --------------------------------------------------------------------------- the mine core


def _locate_systems(roots, repo_paths, fed_root):
    """{sid: (status_fm, repo_path)} for every located system, plus the federation
    itself. Location reuses reconcile.py's STATUS walk + repo-paths map (mapped wins)."""
    # The id-less STATUS.md files (WI-0373) are deliberately not mined here: every
    # metric below is keyed by system id, and a file with no id has no row to appear
    # in. `curate/reconcile.py` is the surface that reports them, at their paths.
    found, _idless = reconcile.find_status_files(roots) if roots else ({}, [])
    if repo_paths:
        mapped, _broken = reconcile.resolve_mapped(repo_paths)
        found.update(mapped)  # mapped repo (direct read) wins over a walked one
    systems = {sid: (fm, path.parent) for sid, (fm, path) in found.items()}
    # Recursive participation (ADR-0003): the federation is a governed system too.
    if fed_root is not None:
        fed = pathlib.Path(fed_root)
        status = fed / "STATUS.md"
        fm = {}
        if status.is_file():
            try:
                fm = reconcile.parse_frontmatter(status.read_text(errors="replace"))
            except OSError:
                fm = {}
        systems.setdefault(fm.get("id") or "federation", (fm, fed))
    return systems


def mine(roots=None, repo_paths=None, fed_root=FED_ROOT, today=None, now_ts=None,
         cadence=None, deep=True):
    """Gather every metric from disk + git ONCE. Pure read; returns a plain dict.

    `today`/`now_ts` are injectable for deterministic tests. `fed_root=None` omits the
    federation-self system (also for tests). The renderers below are thin views over
    this dict and never touch the filesystem themselves.

    `deep=False` (the --status startup path) skips the git-history role-doc bump check —
    a 90-day retrospective whose rev→blob resolution costs tens of seconds on a cold
    network-volume repo (the same pathology that keeps mine_canon_trend off the startup
    path). The startup count may therefore slightly undercount what --exceptions shows;
    the on-demand paths mine it fully.
    """
    roots = roots or []
    today = today or date.today()
    if now_ts is None:
        now_ts = datetime.now().timestamp()
    if cadence is None:
        cadence = load_cadence()

    systems = _locate_systems(roots, repo_paths, fed_root)

    per_system = {}
    all_sessions = []
    runtime_matrix = {}        # sid -> {runtime: count}
    guard_totals = {}          # guard -> count
    applied_briefs = []
    pending_briefs = []
    orphans = []               # (sid, session-id, age_hours)

    for sid, (fm, repo) in systems.items():
        sessions = mine_sessions(repo)
        guards = mine_guard_firings(repo)
        briefs = mine_briefs(repo)
        live_orphans = mine_orphan_live(repo, now_ts)
        journals = mine_journal_sessions(repo)
        unreadable_journals = mine_unreadable_journals(repo)   # WI-0332
        role_doc = _roledoc_name(repo) if deep else None
        roledoc_bumps = mine_roledoc_bumps(repo, role_doc) if role_doc else None
        conformance = mine_conformance(repo, sessions, journals, fm, briefs["applied"],
                                       roledoc_bumps, today, now_ts)
        ledger = summarize_ledger(journals, today)
        fallback_queue = mine_fallback_queue(repo, today)

        rt = {}
        for s in sessions:
            rt[s["runtime"]] = rt.get(s["runtime"], 0) + 1
        runtime_matrix[sid] = rt
        all_sessions.extend(sessions)
        for g, c in guards["counts"].items():
            guard_totals[g] = guard_totals.get(g, 0) + c
        for b in briefs["applied"]:
            applied_briefs.append({**b, "system": sid})
        for b in briefs["pending"]:
            pending_briefs.append({**b, "system": sid})
        for oid, age_h in live_orphans:
            orphans.append((sid, oid, age_h))

        per_system[sid] = {
            "repo": str(repo),
            "status": fm,
            "session_count": len(sessions),
            # WI-0332: the journals this system has that NOTHING can read. Carried beside
            # the count rather than folded into it — a session_count that silently shrinks
            # is the defect, not the report of it.
            "unreadable_journals": unreadable_journals,
            "runtimes": rt,
            "guards": guards,
            "applied_count": len(briefs["applied"]),
            "pending_count": len(briefs["pending"]),
            # WI-0029: carry whether the inbox was actually READABLE. Without it,
            # `pending_count: 0` means both "nothing is waiting" and "I could not see
            # the inbox", and a reader has no way to tell which.
            "briefs_checked": briefs.get("checked", False),
            "briefs_root": briefs.get("root", ""),
            "conformance": conformance,
            # WI-0159: the paired reading. Both halves live here so a renderer can never
            # show one without the other — showing (1) alone is the dashboard that
            # reports this brief's own failure mode as a win.
            "ledger": ledger,
            "fallback_queue": fallback_queue,
        }

    # runtime totals across the fleet
    runtime_totals = {}
    for rt in runtime_matrix.values():
        for r, c in rt.items():
            runtime_totals[r] = runtime_totals.get(r, 0) + c

    # earliest activity (session stamps; git root-commit as the honest floor)
    earliest_session = None
    for s in all_sessions:
        d = s.get("dt")
        if d is not None and (earliest_session is None or d < earliest_session):
            earliest_session = d
    earliest_session_date = earliest_session.date() if earliest_session else None
    git_earliest = _git_earliest_commit(fed_root) if fed_root is not None else None
    canon_size = mine_canon_size(fed_root)

    # propagation: drafted -> applied span (a PROXY; no distinct accept timestamp exists).
    prop_samples = []
    for b in applied_briefs:
        d0, d1 = b.get("drafted_on"), b.get("applied_on")
        if d0 and d1 and d1 >= d0:
            prop_samples.append((d1 - d0).days)

    return {
        "today": today,
        "now_ts": now_ts,
        "deep": deep,          # which mine produced this dict — the renderers say so
        "cadence": cadence,
        "systems": per_system,
        "system_ids": sorted(systems),
        "all_sessions": all_sessions,
        "runtime_matrix": runtime_matrix,
        "runtime_totals": runtime_totals,
        "guard_totals": guard_totals,
        "applied_briefs": applied_briefs,
        "pending_briefs": pending_briefs,
        "orphans": orphans,
        "earliest_session_date": earliest_session_date,
        "git_earliest_commit": git_earliest,
        "propagation_days": sorted(prop_samples),
        "adopt_runner": mine_adopt_runner(fed_root),
        "canon_size": canon_size,
        "fleet_canon": mine_fleet_canon_size(systems, fed_root,
                                             (canon_size or {}).get("total")),
        "spot_audit": mine_spot_audit(fed_root),
        "curate": mine_curate(fed_root),
    }


# --------------------------------------------------------------------------- exceptions


def compute_exceptions(data):
    """The ranked attention list — a list of {system, kind, detail, age_days} dicts,
    most-urgent (largest age) first. Only signals whose source exists TODAY are here;
    the not-yet-mined set is named by the renderer, never silently dropped."""
    cadence = data["cadence"]
    today = data["today"]
    items = []

    for sid in data["system_ids"]:
        info = data["systems"][sid]
        fm = info["status"] or {}
        cadence_days, parked = cadence_for(cadence, sid)

        # Staleness — active (non-parked) system silent past its cadence.
        if not parked:
            age = reconcile.age_days(fm.get("last_active"), today)
            if age is not None and age > cadence_days:
                items.append({"system": sid, "kind": "stale", "age_days": age,
                              "detail": f"{age}d since last_active (cadence {cadence_days}d)"})

        # Blocked — STATUS blocked: <reason>.
        blocked = (fm.get("blocked") or "false").strip()
        if blocked.lower() not in _UNBLOCKED:
            age = reconcile.age_days(fm.get("last_active"), today)
            reason = "" if blocked.lower() == "true" else f": {blocked}"
            items.append({"system": sid, "kind": "blocked", "age_days": age,
                          "detail": f"BLOCKED{reason}"})

        # Destroyed journal records (WI-0332). age_days None: this is not a staleness
        # signal, it is a corruption one — it does not get better by being old, and
        # ranking it by age would bury a record destroyed this morning.
        for name, why in (info.get("unreadable_journals") or []):
            items.append({"system": sid, "kind": "journal", "age_days": None,
                          "detail": f"UNREADABLE JOURNAL {name} — {why}"})

        # Git sync — behind/diverged (stale-clone) and ahead-unpushed (unbacked-up).
        repo = pathlib.Path(info["repo"])
        stale = reconcile.git_staleness(repo)
        if stale:
            items.append({"system": sid, "kind": "git", "age_days": None,
                          "detail": stale})
        ahead = git_ahead_unpushed(repo)
        if ahead:
            items.append({"system": sid, "kind": "git", "age_days": None,
                          "detail": f"unbacked-up ({ahead} commit(s) ahead of origin, unpushed)"})

        # Ritual conformance (ADR-0053) — ONE aggregated line per system (per-item
        # detail lives in EVIDENCE.md; per-violation lines would flood the list).
        conf = info.get("conformance") or {}
        conf_recent = conf.get("recent") or {}
        n_conf = sum(len(v) for v in conf_recent.values())
        # A skipped check is named ON the row, so a shallow row cannot be read as a
        # complete verdict on that system. It does NOT recover the row for a system whose
        # only violations are deep-only — that row is still absent from a shallow mine,
        # and the count still comes out lower. That residue is declared by render_status's
        # scope note rather than papered over here: emitting a zero-violation row per
        # system to carry the caveat would put 11 no-news lines on every session start.
        skipped = conf.get("unchecked") or []
        skip_s = f" [not checked: {', '.join(sorted(skipped))}]" if skipped else ""
        # Unscored facts ride along on the row rather than owning one (WI-0207): the
        # reaper's correct behaviour is worth seeing, but a system whose ONLY finding is
        # "the janitor closed four journals" has nothing to attend to, and an attention
        # list that prints it has learned nothing from what this item fixed.
        conf_info = conf.get("informational") or {}
        n_info = sum(len(v) for v in conf_info.values())
        info_s = ""
        if n_info:
            ip = ", ".join(f"{len(v)} {k}" for k, v in sorted(conf_info.items()))
            info_s = f" (+{n_info} not scored: {ip})"
        if n_conf:
            parts = ", ".join(f"{len(v)} {k}" for k, v in sorted(conf_recent.items()))
            items.append({"system": sid, "kind": "conformance", "age_days": None,
                          "detail": f"{n_conf} ritual violation(s) in "
                                    f"{CONFORMANCE_RECENT_DAYS}d: {parts}{info_s}{skip_s} "
                                    f"— detail: {EVIDENCE_CMD} (ADR-0053)"})

        # Escalations to the user, paired with the fallback queue (WI-0159 / ADR-0099
        # D4). ONE row carrying BOTH numbers, deliberately — the failure this metric
        # exists to catch is escalations at zero WHILE the queue grows, and a reader who
        # can see the first number without the second reads that as success. Same shape
        # as the curate row below, and the same reason.
        lg = info.get("ledger") or {}
        fq = info.get("fallback_queue")
        if lg.get("sessions") or isinstance(fq, dict):
            w = lg.get("window_days", ESCALATION_WINDOW_DAYS)
            n = lg.get("escalations", 0)
            # The queue half, and what it means when it cannot be read.
            if not isinstance(fq, dict):
                q_s = "fallback queue not measurable here (no work-item store)"
                q_bad = False
            elif not fq.get("group_used"):
                # A store that has never carried the group is UNMARKED, not empty. The
                # group is federation-shaped; a member that files its missing-verb work
                # some other way has a real queue this cannot see, and reporting depth 0
                # for it would be the healthy-looking zero this metric exists to refuse.
                q_s = (f"fallback queue UNMARKED here — no item in this store has ever "
                       f"used group '{fq['group']}', so its depth is unknown, not zero")
                q_bad = False
            else:
                oldest, added = fq.get("oldest_days"), fq.get("added_in_window")
                if not fq.get("dated"):
                    q_s = (f"fallback queue depth {fq['depth']} ({fq['group']}), "
                           f"age UNKNOWN — git could not date the store")
                    q_bad = bool(fq["depth"])   # undated is never read as young
                else:
                    closed = fq.get("closed_in_window") or 0
                    ageing = oldest is not None and oldest > fq["stale_days"]
                    growing = added > closed
                    q_s = (f"fallback queue depth {fq['depth']} ({fq['group']}), "
                           f"oldest {oldest}d, {added} filed / {closed} closed in {w}d")
                    q_bad = ageing or growing
            # How much of the escalation number is actually evidence.
            silent = lg.get("silent", 0)
            seen_s = (f"{lg.get('sessions', 0)} session(s) in {w}d"
                      + (f", {silent} carrying no such section and NOT counted as zero"
                         if silent else ""))
            # WI-0278: the split, on the row itself. "9 escalations" reads very
            # differently when one was written down at close and eight interrupted a live
            # session, and that difference is the whole finding WI-0274 recorded.
            mid_s = (f" ({lg.get('escalations_at_close', n)} at close, "
                     f"{lg['escalations_midsession']} mid-session)"
                     if lg.get("escalations_midsession") else "")
            if n:
                items.append({"system": sid, "kind": "escalation", "age_days": None,
                              "detail": f"{n} escalation(s) to the user{mid_s} across "
                                        f"{lg['escalation_sessions']} of {seen_s} "
                                        f"(target zero); {lg['decisions']} decision(s) "
                                        f"recorded in-lane; {q_s} (WI-0159)"})
            elif q_bad and lg.get("sessions"):
                # THE READING THE BRIEF CARES ABOUT, and it must be as loud as the
                # other: nobody is being interrupted any more, and the work they would
                # have been interrupted about is piling up instead.
                items.append({"system": sid, "kind": "escalation", "age_days": None,
                              "detail": f"escalations at ZERO across {seen_s} but the "
                                        f"{q_s} — doctrine adopted without the verbs "
                                        f"built, which the brief counts as its own "
                                        f"failure mode, not a success (WI-0159)"})
            elif q_bad:
                items.append({"system": sid, "kind": "escalation", "age_days": None,
                              "detail": f"{q_s}; escalations NOT measurable (no journal "
                                        f"started in {w}d) — the queue is the only half "
                                        f"being read (WI-0159)"})

    # Pending dwell — stranded briefs older than the dwell threshold.
    for b in data["pending_briefs"]:
        age = int((data["now_ts"] - b["mtime"]) / 86400.0)
        if age > PENDING_DWELL_DAYS:
            items.append({"system": b["arch"], "kind": "pending-dwell", "age_days": age,
                          "detail": f"pending brief stranded {age}d ({b['path'].name})"})

    # Orphan residual — stale .live sidecar with no .ended.
    for sid, oid, age_h in data["orphans"]:
        age_d = age_h / 24.0
        items.append({"system": sid, "kind": "orphan", "age_days": age_d,
                      "detail": f"orphan session sidecar {oid[:8]} live {age_h:.0f}h, no .ended"})

    # Adoption runner (ADR-0050) — surface only GENUINE trouble, not a single stale
    # night (that lives on the dashboard, not the attention list). Absent status = the
    # runner does not report here (n/a), never an exception. A stale STREAK past the
    # escalation threshold, or a failed/errored last sweep, is worth the operator's attention.
    ar = data.get("adopt_runner")
    if isinstance(ar, dict):
        stale = int(ar.get("consecutive_stale", 0) or 0)
        if stale >= RUNNER_STALE_ATTENTION:
            items.append({"system": "federation", "kind": "adopt-runner", "age_days": None,
                          "detail": f"adoption runner unauthenticated {stale} consecutive "
                                    f"sweep(s) (stale since {ar.get('stale_since', '?')}) — "
                                    f"refresh the Runner login"})
        elif ar.get("result") in ("failed", "error"):
            items.append({"system": "federation", "kind": "adopt-runner", "age_days": None,
                          "detail": f"adoption runner last sweep {ar.get('result')}: "
                                    f"{str(ar.get('detail', ''))[:80]}"})

    # Canon budget (ADR-0052) — the injected doctrine set over its size budget means a
    # consolidation pass is owed. Federation-scoped (it owns the doctrine source). Soft:
    # this is a flagged debt, not a block. Age-less, so it sorts with the other "—" items.
    cs = data.get("canon_size")
    if isinstance(cs, dict) and cs.get("over_by", 0) > 0:
        items.append({"system": "federation", "kind": "canon-budget", "age_days": None,
                      "detail": f"federation doctrine source {cs['total']} chars, over budget "
                                f"by {cs['over_by']} (ceiling {cs['budget']}) — consolidation "
                                f"pass owed (ADR-0052)"})

    # Fleet doctrine drift (WI-0374) — the line above measures ONE tree, the federation's.
    # What each member actually carries is a separate measured fact, and the two diverge
    # the moment a canon change lands and the substrate push does not run. The state that
    # made this necessary is the silent one: the federation sits comfortably under the
    # ceiling while most members sit over it, and the budget line above says nothing,
    # because it was never looking at them. Age-less, soft, federation-scoped (the push is
    # the federation's act, not a member's obligation).
    fc = data.get("fleet_canon")
    if isinstance(fc, dict) and fc.get("measured"):
        over, stale = fc.get("over") or [], fc.get("stale") or []
        if over or stale:
            bits = []
            if over:
                bits.append(f"{len(over)} of {fc['measured']} located repo(s) carry a set "
                            f"over the {fc['budget']}-byte ceiling")
            if stale:
                bits.append(f"{len(stale)} do not carry the current source")
            detail = f"fleet doctrine set: {'; '.join(bits)}"
            if fc.get("if_uniform") is not None:
                detail += (f" — measured fleet per-session cost {fc['fleet_total']} bytes "
                           f"vs {fc['if_uniform']} if all carried the federation's "
                           f"{fc['fed_total']} ({fc['gap']:+d})")
            detail += " — substrate push owed (ADR-0052 / WI-0010)"
            items.append({"system": "federation", "kind": "canon-drift", "age_days": None,
                          "detail": detail})

    # Spot-audit recency (ADR-0053 mechanism 2) — the judgment layer's sampled check.
    sa = data.get("spot_audit")
    if isinstance(sa, dict):
        if sa["latest"] is None:
            items.append({"system": "federation", "kind": "spot-audit", "age_days": None,
                          "detail": "spot-audit never run — monthly Auditor pass owed; "
                                    "record in audits/ (ADR-0053)"})
        else:
            age = (data["today"] - sa["latest"]).days
            if age > SPOT_AUDIT_CADENCE_DAYS + SPOT_AUDIT_GRACE_DAYS:
                items.append({"system": "federation", "kind": "spot-audit", "age_days": age,
                              "detail": f"spot-audit overdue: {age}d since last "
                                        f"({sa['latest']}), cadence "
                                        f"{SPOT_AUDIT_CADENCE_DAYS}d (ADR-0053)"})

    # Curation recency (WI-0087) — the last load-bearing chore resting on human memory,
    # and the one that produces canon. Two signals, deliberately separate: how long since
    # canon absorbed anything, and how much is queued behind that. Neither number means
    # much alone — a long gap with nothing waiting is a quiet fleet, not a debt; entries
    # waiting behind a recent pass are just in-flight.
    cu = data.get("curate")
    if isinstance(cu, dict):
        pend = cu.get("pending")
        # Three states, never two (WI-0211). `not pend` was true for BOTH zero and
        # None, so a queue that could not be counted rendered exactly like a queue
        # that is empty — on the signal whose entire job is making this debt visible.
        if pend is None:
            pend_s = ", producer queue NOT COUNTED (no producer file was readable)"
        elif not pend:
            pend_s = ""                      # counted, and genuinely clear
        else:
            pend_s = f", {pend} producer entr{'y' if pend == 1 else 'ies'} waiting"
        if cu.get("latest") is None:
            items.append({"system": "federation", "kind": "curate", "age_days": None,
                          "detail": "curate pass never recorded — no entry has ever been "
                                    f"accepted into canon{pend_s} (WI-0087)"})
        else:
            age = (data["today"] - cu["latest"]).days
            if age > CURATE_CADENCE_DAYS:
                items.append({"system": "federation", "kind": "curate", "age_days": age,
                              "detail": f"curation overdue: {age}d since canon last "
                                        f"absorbed anything ({cu['latest']}), cadence "
                                        f"{CURATE_CADENCE_DAYS}d{pend_s} (WI-0087)"})

    items.sort(key=lambda it: (it["age_days"] is None, -(it["age_days"] or 0)))
    return items


# signals whose SOURCE does not exist yet — omitted from the report, named so the
# omission is visible (never silent). Update as instrumentation lands.
# `mid-session-escalations` CAME OFF THIS LIST (WI-0278), and the removal is the point
# rather than a tidy-up: a signal named here is one the report declares it cannot see, and
# leaving it after the source exists would understate the number exactly as thoroughly as
# omitting it silently did. The `attention` record is still current-state-only — one live
# row per lane, destroyed by the clear that answers it — but it is no longer the only
# record: `_attention_write` and `_attention_clear` now append to a per-session ledger that
# `session.py end` folds into the journal under `### Escalations raised mid-session`, which
# `MIDSESSION_HEADINGS` above counts. Journals written before that shipped carry no such
# section and read as UNKNOWN, which the renderer says out loud.
NOT_YET_MINED = ["ring-stall", "comms-dwell", "suite/standard-failures",
                 "backup-verification-age"]


def render_exceptions(data):
    items = compute_exceptions(data)
    lines = []
    if not items:
        lines.append("All clear — no attention items.")
    else:
        for it in items:
            age = it["age_days"]
            age_s = "—" if age is None else (f"{age:.0f}d" if age >= 1 else f"{age*24:.0f}h")
            lines.append(f"[{age_s:>5}] {it['system']:<26} {it['kind']:<13} {it['detail']}")
    lines.append(f"# not yet mined: {', '.join(NOT_YET_MINED)} (sources pending)")
    return "\n".join(lines)


def render_status(data):
    """One-line SessionStart signal (like gather/reconcile --status).

    States its own scope. The startup path mines shallow (`deep=False`) for cost, so its
    count can be LOWER than `--exceptions` — and until this said so, the two surfaces
    disagreed with nothing on either explaining the gap, which reads as one of them being
    broken (`declare-what-a-check-assumes`).
    """
    scope = "" if data.get("deep", True) else " (startup scope: role-doc bump checks not run)"
    n = len(compute_exceptions(data))
    if n == 0:
        return f"Metrics: all clear{scope}."
    return (f"Metrics: {n} attention item(s){scope} — "
            f"run 'python3 curate/metrics.py --exceptions'")


# --------------------------------------------------------------------------- money slide


def _months_between(d0, d1):
    return (d1 - d0).days / 30.44


def render_money_slide(data):
    cadence = data["cadence"]
    today = data["today"]

    # (1) systems governed — active (located) vs parked (config, staleness-exempt).
    parked_ids = sorted(s for s in cadence.get("systems", {})
                        if cadence["systems"][s].get("parked"))
    located = data["system_ids"]
    active = [s for s in located if s not in parked_ids]
    lines = [
        "Federation metrics — money slide (each number mined or explicitly n/a)",
        "",
        f"(1) systems governed         : {len(active)} active (located)"
        f" / {len(parked_ids)} parked (config, staleness-exempt)"
        + (f" — parked: {', '.join(parked_ids)}" if parked_ids else ""),
    ]

    # (2) months in operation — earliest session stamp or git root commit -> today.
    floor = None
    src = None
    if data["earliest_session_date"]:
        floor, src = data["earliest_session_date"], "earliest session stamp"
    if data["git_earliest_commit"] and (floor is None or data["git_earliest_commit"] < floor):
        floor, src = data["git_earliest_commit"], "earliest git commit"
    if floor is None:
        lines.append("(2) months in operation      : n/a (source pending: no session stamps or git history)")
    else:
        lines.append(f"(2) months in operation      : {_months_between(floor, today):.1f} "
                     f"(since {floor.isoformat()}, {src})")

    # (3) behavior changes shipped — applied briefs fleet-wide.
    lines.append(f"(3) behavior changes shipped : {len(data['applied_briefs'])} applied brief(s) fleet-wide")

    # (4) propagation — drafted->applied span PROXY (no distinct accept timestamp exists).
    prop = data["propagation_days"]
    if prop:
        med = statistics.median(prop)
        lines.append(f"(4) median propagation time  : {med:.1f}d "
                     f"(n={len(prop)}; PROXY: brief 'Drafted on'->'Applied on', no accept stamp recorded)")
    else:
        lines.append("(4) median propagation time  : n/a (source pending: brief accept/apply timestamps)")

    # (5) human interventions per change — apply.py auto-adopt vs surface ratio.
    lines.append("(5) human interventions/change: n/a (source pending: apply.py surfacing ledger — "
                 "apply.py surfaces to stdout, no persisted auto-vs-surfaced record)")

    # (6) half-apply incidents — interesting only at zero.
    lines.append("(6) half-apply incidents     : n/a (source pending: half-apply incident ledger) "
                 "[interesting only at zero]")

    # (7) runtimes operated — per-runtime session counts (from stamp runtime fields).
    rt = data["runtime_totals"]
    if rt:
        parts = ", ".join(f"{r}={c}" for r, c in sorted(rt.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append(f"(7) runtimes operated        : {len(rt)} ({parts})")
    else:
        lines.append("(7) runtimes operated        : n/a (source pending: no session stamps found)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- evidence


def render_evidence(data):
    today = data["today"]
    out = [EVIDENCE_BANNER.rstrip("\n"), "",
           "# Federation metrics — evidence", "",
           f"Mined {today.isoformat()} by `curate/metrics.py --evidence`. "
           "Every figure below is read from an on-disk artifact or from git; figures "
           "whose source does not exist yet are shown as `n/a (source pending: …)`.", ""]

    # Per-system session-count + adoption table.
    out.append("## Per-system: sessions & adoption")
    out.append("")
    out.append("| system | sessions | applied briefs | pending briefs | last_active | blocked |")
    out.append("|---|---:|---:|---:|---|---|")
    for sid in data["system_ids"]:
        info = data["systems"][sid]
        fm = info["status"] or {}
        out.append(f"| {sid} | {info['session_count']} | {info['applied_count']} | "
                   f"{info['pending_count']} | {fm.get('last_active', '—')} | "
                   f"{fm.get('blocked', '—')} |")
    out.append("")

    # Runtime matrix (system x runtime x session count).
    out.append("## Runtime matrix (system × runtime × session count)")
    out.append("")
    runtimes = sorted(data["runtime_totals"])
    if runtimes:
        out.append("| system | " + " | ".join(runtimes) + " |")
        out.append("|---|" + "|".join("---:" for _ in runtimes) + "|")
        for sid in data["system_ids"]:
            row = data["runtime_matrix"].get(sid, {})
            out.append(f"| {sid} | " + " | ".join(str(row.get(r, 0)) for r in runtimes) + " |")
        out.append(f"| **total** | " + " | ".join(str(data["runtime_totals"][r]) for r in runtimes) + " |")
    else:
        out.append("n/a (source pending: no session stamps found)")
    out.append("")

    # Guard-firing counts by guard.
    out.append("## Guard firings by guard")
    out.append("")
    if data["guard_totals"]:
        out.append("| guard | firings |")
        out.append("|---|---:|")
        for g, c in sorted(data["guard_totals"].items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"| {g} | {c} |")
    else:
        out.append("No guard firings recorded (`.session-state/guard-firings.jsonl` absent or empty across the fleet).")
    out.append("")

    # Propagation-lag distribution.
    out.append("## Propagation lag (drafted → applied)")
    out.append("")
    prop = data["propagation_days"]
    if prop:
        out.append(f"- samples: {len(prop)}")
        out.append(f"- days: {', '.join(str(d) for d in prop)}")
        out.append(f"- median: {statistics.median(prop):.1f}d · min: {min(prop)}d · max: {max(prop)}d")
        out.append("- PROXY: brief `Drafted on` → `Applied on` metadata dates. There is no distinct "
                   "federation-side *accept* timestamp on disk, so this is drafted→applied, not "
                   "accept→adopted. Treat as an upper-bound proxy.")
    else:
        out.append("n/a (source pending: brief accept/apply timestamps)")
    out.append("")

    # Background adoption runner (R3, ADR-0050) — proof-of-life + stale-streak dashboard.
    out.append("## Background adoption runner (R3, ADR-0050)")
    out.append("")
    ar = data.get("adopt_runner")
    if not isinstance(ar, dict):
        out.append("n/a (source pending: the runner has not reported on this machine — "
                   "`.session-state/adopt-runner.status` absent). The runner writes this file "
                   "only where it executes, e.g. the Runner's nightly sweep; it is machine-local "
                   "(`.session-state/` is gitignored), so this section is populated where the "
                   "runner runs.")
    else:
        out.append(f"- last run: {ar.get('last_run', '?')} · result: **{ar.get('result', '?')}**")
        if ar.get("auth"):
            out.append(f"- auth: {ar['auth']}")
        out.append(f"- last successful (authenticated) sweep: {ar.get('last_success') or 'never'}")
        stale = int(ar.get("consecutive_stale", 0) or 0)
        if stale:
            out.append(f"- unauthenticated streak: {stale} consecutive sweep(s) "
                       f"(stale since {ar.get('stale_since', '?')}) — the runner is fail-safe "
                       f"idle, not broken; refresh the Runner login to re-arm")
        out.append(f"- last sweep: {ar.get('adopted', 0)} adopted · {ar.get('failed', 0)} "
                   f"failed/errored · {ar.get('eligible', 0)} eligible of "
                   f"{ar.get('repos_scanned', 0)} repo(s) scanned")
        if ar.get("detail"):
            out.append(f"- detail: {ar['detail']}")
    out.append("")

    # Injected doctrine set — canon budget (ADR-0052).
    out.append("## Injected doctrine set — canon budget (ADR-0052)")
    out.append("")
    cs = data.get("canon_size")
    if not isinstance(cs, dict):
        out.append("n/a (source pending: CANON.md / STANDARD.md not located)")
    else:
        budget, total = cs["budget"], cs["total"]
        pct = (total / budget * 100) if budget else 0.0
        state = (f"**OVER by {cs['over_by']}** — consolidation pass owed"
                 if cs["over_by"] > 0 else f"{budget - total} under budget")
        out.append(f"- budget: {budget} · current: {total} · {pct:.0f}% of budget · {state}")
        for name in CANON_FILES:
            v = cs["files"].get(name)
            out.append(f"  - {name}: {v if v is not None else 'absent'}")
        out.append("- measured in bytes (`wc -c`), the unit ADR-0052 calibrated the budget in "
                   "(~bytes ÷ 4 ≈ tokens). Soft budget: over it flags a consolidation pass owed, "
                   "it never blocks a change.")
        out.append("- SCOPE: the federation's own copy — the per-session cost of an Architect "
                   "carrying the current source, and the subject a consolidation pass edits. "
                   "What the fleet actually carries is the table below, measured per repo.")
    fc = data.get("fleet_canon")
    if isinstance(fc, dict):
        out.append("")
        out.append("### What the fleet actually carries (WI-0374)")
        out.append("")
        out.append("Each located repo's own `CANON.md` + `STANDARD.md`, read per repo. The "
                   "federation's copy is not multiplied out — a member that missed the last "
                   "substrate push pays the size it really carries, not the size of the source.")
        out.append("")
        out.append("| repo | CANON.md | STANDARD.md | total | vs source | vs ceiling | state |")
        out.append("|---|---:|---:|---:|---:|---:|---|")
        for m in fc["members"]:
            f = m["files"]
            t = m["total"]
            drift = ("—" if t is None or fc["fed_total"] is None
                     else "source" if m["federation"] else f"{t - fc['fed_total']:+d}")
            ceil = "—" if m["over_by"] is None else (f"+{m['over_by']}" if m["over_by"] else "under")
            out.append(f"| {m['system']}{' (source)' if m['federation'] else ''} "
                       f"| {f['CANON.md'] if f['CANON.md'] is not None else 'absent'} "
                       f"| {f['STANDARD.md'] if f['STANDARD.md'] is not None else 'absent'} "
                       f"| {t if t is not None else '—'} | {drift} | {ceil} | {m['state']} |")
        out.append("")
        out.append(f"- measured fleet per-session cost: **{fc['fleet_total']} bytes** over "
                   f"{fc['measured']} complete repo(s)")
        if fc["if_uniform"] is not None:
            out.append(f"- what a federation-only figure implies: {fc['if_uniform']} "
                       f"({fc['measured']} × {fc['fed_total']}) — **error {fc['gap']:+d} bytes**")
        out.append(f"- if every measured repo sat at the ceiling: "
                   f"{fc['measured'] * fc['budget']}")
        if fc["over"]:
            out.append(f"- over the ceiling: {', '.join(fc['over'])}")
        if fc["stale"]:
            out.append(f"- not carrying the current source: {', '.join(fc['stale'])}")
        if fc["partial"]:
            out.append(f"- PARTIAL (one file only — total is a floor, not summed): "
                       f"{', '.join(fc['partial'])}")
        if fc["absent"]:
            out.append(f"- no doctrine set located: {', '.join(fc['absent'])}")
    trend = data.get("canon_trend")
    if trend:
        out.append("")
        out.append("Trend — injected-set bytes over time (one point per date the set changed, mined from git):")
        out.append("")
        out.append("| date | bytes | vs budget |")
        out.append("|---|---:|---|")
        for d, t in trend:
            delta = t - CANON_BUDGET_CHARS
            out.append(f"| {d} | {t} | {('+' + str(delta)) if delta > 0 else str(delta)} |")
    out.append("")

    # Ritual conformance (ADR-0053) — full detail behind the aggregated exception line.
    out.append("## Ritual conformance (ADR-0053)")
    out.append("")
    out.append(f"Mined, never self-reported. Recent = last {CONFORMANCE_RECENT_DAYS} days "
               "(these also surface as exception lines); historic = older debt, counted "
               "here only. A violation is evidence, not an accusation — it is as likely "
               "a harness bug as a lapse, and both are worth knowing.")
    out.append("")
    out.append("**Not scored** (WI-0207) is a third answer, not a lenient violation: a "
               "journal closed by the janitor, `reap` or `resolve-orphan` was closed "
               "after its session was already dead, so it could not have narrated itself "
               "and the span to its close is death evidence read correctly. Counted and "
               "shown so the reaper's behaviour stays visible; never scored, so eleven "
               "of them stop burying the four human closes worth reading.")
    out.append("")
    clean = []
    for sid in data["system_ids"]:
        conf = data["systems"][sid].get("conformance") or {}
        recent = {k: v for k, v in (conf.get("recent") or {}).items() if v}
        historic = {k: c for k, c in (conf.get("historic") or {}).items() if c}
        info = {k: v for k, v in (conf.get("informational") or {}).items() if v}
        if not recent and not historic and not info:
            clean.append(sid)
            continue
        out.append(f"### {sid}")
        out.append("")
        for k in sorted(recent):
            out.append(f"- **{k}** (recent): {len(recent[k])}")
            for detail in recent[k][:10]:
                out.append(f"  - {detail}")
            if len(recent[k]) > 10:
                out.append(f"  - … and {len(recent[k]) - 10} more")
        for k in sorted(historic):
            out.append(f"- {k} (historic, pre-window): {historic[k]}")
        for k in sorted(info):
            out.append(f"- {k} (not scored): {len(info[k])}")
            for detail in info[k][:10]:
                out.append(f"  - {detail}")
            if len(info[k]) > 10:
                out.append(f"  - … and {len(info[k]) - 10} more")
        out.append("")
    if clean:
        out.append(f"Clean: {', '.join(clean)}.")
        out.append("")
    out.append("Handoff-era narrative completeness is unminable (prose has no structural "
               "boundary) and is deliberately not judged; the missing-narrative check "
               "covers journal-model repos only.")
    out.append("")
    sa = data.get("spot_audit")
    if isinstance(sa, dict):
        if sa["latest"] is None:
            out.append("Spot-audit (mechanism 2): **never run** — monthly Auditor pass "
                       "owed; records land in `audits/`.")
        else:
            out.append(f"Spot-audit (mechanism 2): last run **{sa['latest']}** "
                       f"(cadence {SPOT_AUDIT_CADENCE_DAYS}d).")
        out.append("")

    # WI-0159: the paired reading, with the detail a one-line exception row cannot hold
    # — which sessions escalated, and which items are actually sitting in the queue.
    out.append("## Escalations to the user, and the fallback queue (WI-0159)")
    out.append("")
    out.append("Counted structurally from journal headings — `### Open questions for the "
               "user` and `### Decisions I made without you, for review`, both AUTHORED "
               "at close, plus `### Escalations raised mid-session`, which `session.py "
               "end` folds in from the session's own escalation ledger (WI-0278). An "
               "absent section is UNKNOWN, never zero — and every journal written before "
               "the fold shipped is absent that third section, so a month spanning the "
               "cutover is still a floor on its mid-session half.")
    out.append("")
    for sid in data["system_ids"]:
        info = data["systems"][sid]
        lg = info.get("ledger") or {}
        if not lg.get("sessions"):
            continue
        fq = info.get("fallback_queue")
        out.append(f"### {sid}")
        out.append("")
        out.append(f"- {lg['escalations']} escalation(s) across "
                   f"{lg['escalation_sessions']} of {lg['sessions']} session(s) in "
                   f"{lg['window_days']}d; {lg['decisions']} decision(s) recorded in-lane")
        out.append(f"  - {lg.get('escalations_at_close', 0)} written down at close, "
                   f"{lg.get('escalations_midsession', 0)} observed mid-session — the "
                   f"ratio WI-0274 measured, not a total to be read alone")
        if lg.get("silent"):
            out.append(f"- {lg['silent']} journal(s) carry no escalation section — "
                       f"UNKNOWN, not counted as zero")
        if lg.get("midsession_silent"):
            out.append(f"- {lg['midsession_silent']} journal(s) carry no mid-session "
                       f"section — UNKNOWN (most will predate the fold), not zero")
        for name, n in lg.get("named", [])[:15]:
            out.append(f"  - {name}: {n}")
        if len(lg.get("named", [])) > 15:
            out.append(f"  - … and {len(lg['named']) - 15} more session(s)")
        if not isinstance(fq, dict):
            out.append("- fallback queue: no work-item store here — NOT measurable")
        elif not fq.get("group_used"):
            out.append(f"- fallback queue: group `{fq['group']}` has never been used in "
                       f"this store — depth UNKNOWN, not zero")
        else:
            aged = "UNKNOWN" if not fq.get("dated") else f"{fq['oldest_days']}d"
            out.append(f"- fallback queue `{fq['group']}`: depth {fq['depth']}, oldest "
                       f"{aged}, {fq.get('added_in_window')} filed / "
                       f"{fq.get('closed_in_window')} closed in {lg['window_days']}d"
                       + (f" — {', '.join(fq['ids'])}" if fq["ids"] else ""))
        out.append("")

    out.append("## Sources not yet mined")
    out.append("")
    out.append("These carry no on-disk source yet, so no number is emitted (P15 — never fabricated):")
    for s in NOT_YET_MINED:
        out.append(f"- {s}")
    out.append("- human-interventions-per-change (apply.py surfacing ledger)")
    out.append("- half-apply incidents (half-apply incident ledger)")
    out.append("")
    return "\n".join(out)


def write_evidence(data, path=EVIDENCE_PATH):
    atomic_write(path, render_evidence(data) + "\n")
    return path


# --------------------------------------------------------------------------- CLI


def _resolve_roots(args_roots):
    roots = args_roots or _read_roots_config(ROOTS_CONFIG)
    repo_paths = _read_repo_paths_config(REPO_PATHS_CONFIG)
    return roots, repo_paths


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Federation metrics miner + renderings.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="one-line SessionStart signal")
    mode.add_argument("--exceptions", action="store_true", help="ranked attention list (default)")
    mode.add_argument("--money-slide", action="store_true", help="the seven headline numbers")
    mode.add_argument("--evidence", action="store_true", help="write EVIDENCE.md (full dataset)")
    ap.add_argument("roots", nargs="*", help="search roots (default: reconcile-roots.local)")
    args = ap.parse_args(argv)

    # --status is a startup hook: it must FAIL OPEN and never throw.
    if args.status:
        try:
            roots, repo_paths = _resolve_roots(args.roots)
            data = mine(roots, repo_paths, deep=False)
            print(render_status(data))
        except Exception:  # never brick a session start
            pass
        return 0

    try:
        roots, repo_paths = _resolve_roots(args.roots)
        data = mine(roots, repo_paths)
    except Exception:
        import traceback
        traceback.print_exc()
        print("Metrics: mine errored — emitted nothing.", file=sys.stderr)
        return 1

    if args.money_slide:
        print(render_money_slide(data))
    elif args.evidence:
        # The size-over-time trend makes O(commits) git calls — on-demand only, never
        # on the startup path. Mined here and injected so render_evidence stays a pure
        # view over data (the module's renderer discipline).
        data["canon_trend"] = mine_canon_trend(FED_ROOT)
        path = write_evidence(data)
        print(f"Wrote {path} ({len(data['system_ids'])} system(s), "
              f"{len(data['applied_briefs'])} applied brief(s), "
              f"{sum(data['guard_totals'].values())} guard firing(s)).")
    else:  # --exceptions or bare default
        print(render_exceptions(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
