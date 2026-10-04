#!/usr/bin/env python3
"""Headless background adoption runner — the R3 agent path (ADR-0050).

The residue zero-touch can't put on the code path (ADR-0039/0049 `apply-briefs`)
is the genuinely-manual brief: a substrate install, a multi-file migration, an edit
that needs target-side judgment. ADR-0049 routed those to an *agent path* so they
never need an attended session; this script IS that path.

It sweeps the fleet, and for each Claude-Code member holding a runner-eligible
`Apply: manual` brief in an idle repo, it opens a headless `claude -p` session *in
that repo, as that Architect*, works the brief, runs the brief's declared `Verify:`
command, and — commit-only — leaves the adoption in place or resets it.

Design (ADR-0050):
  * Eligibility — a `manual` brief with a non-empty `verify:` and a `manual-reason`
    that is NOT `attended`. `attended` / no-verify briefs are skipped (they surface
    to operator at the member's next interactive startup, unchanged).
  * Claude-only — a member with no Claude runtime is skipped; the
    runner adopts by spawning `claude -p`, which can only be that member's Claude
    binding.
  * Liveness guard (ADR-0036) — a repo with a fresh `.session-state/*.live`
    heartbeat is skipped this sweep; the runner never races a session.
  * Commit-only — the headless session closes through `session.py end --commit
    --no-push`. The one irreversible/outward act (the GitHub push) stays off the
    unattended path; the target's next interactive session auto-pushes (ADR-0035).
    Every runner action is a local, resettable commit. WI-0355 flipped `end`'s
    default to publishing, so this rule is now DECLARED rather than inherited from a
    flag nobody passed — which is the only form of it that survives the next default
    change.
  * Verify-or-reset — a non-zero `Verify:` exit, a `claude -p` error/timeout, or a
    session that produced no commit fails the adoption: `git reset --hard` to the
    pre-adoption HEAD, then a `comms/` `blocked` note (ADR-0046) is authored + the
    brief left pending. Never half-applied (P18).
  * Report-after (P7) — outcomes are printed and land in each target's
    comms/handoff/next-startup, with the git trail to revert. Nothing gates on operator.
  * Status line, not per-night mail — every real sweep overwrites ONE status file
    (`.session-state/adopt-runner.status`, the dashboard source metrics.py mines):
    last run, result, proof-of-life `last_success` (auth, not adoption health),
    `last_clean_sweep` (authenticated with nothing failed), and the unauthenticated
    streak.
    The routine "could not authenticate on this run" state lives there, NOT in a fresh
    comms note per stale night. A standing stale-login comms note (a single stable
    file, not one per night) is raised only once the streak crosses `--escalate-after`
    (default 5) and self-clears the moment auth recovers — so the mailbox never
    accretes and a real multi-night stall still gets pushed. An adoption that FAILS
    VERIFY in a target still writes that target's own dated blocked note (unchanged).
  * Fail-safe — any per-repo error is caught and logged; the sweep continues. Caps
    bound how much one sweep can do (P19 cap-what-can-run-away).

Trigger: a scheduled launchd sweep on the always-on Runner is the default; the
runner is idempotent (no eligible brief → no-op, live repo → skip) so re-running is
always safe. Runnable by hand for the pilot and on-demand sweeps.

Federation-only, like everything in curate/.

Usage:
    python3 curate/adopt-runner.py --dry-run          # plan only: discover, classify, liveness — spawn nothing
    python3 curate/adopt-runner.py --repo <abs-path>  # limit the sweep to one target (the pilot)
    python3 curate/adopt-runner.py                     # live sweep of every reachable Claude member
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (  # noqa: E402
    read_roots_config,
    read_repo_paths_config,
    shared_work_root,
    member_config_root,
    walk_tree,
    atomic_write,
)

import adoption_metrics
import production
import mailqueue
import mailnames

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import poga_evidence as evidence  # noqa: E402  (repo root, just added)
from sessionlib.brief import brief_header as _brief_header  # noqa: E402  (ditto)

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOTS_CONFIG = member_config_root(FED_ROOT) / "reconcile-roots.local"
REPO_PATHS_CONFIG = member_config_root(FED_ROOT) / "repo-paths.local"
MAX_DEPTH = 4
CONVERGENCE_MARKERS = ["CANON.md", "session.py"]

# WI-0233 / ADR-0103. A DEPLOY TREE is a checkout of a converged member's own repo, so it
# carries both convergence markers and is otherwise indistinguishable from that member's
# working repo. If a machine's walk roots ever contain the deploy root -- devbox's do not,
# but each machine's roots file is machine-local and unreadable from the others, so that is
# an assumption rather than a fact -- adoption would run INSIDE a tree the deploy runner
# owns, mutating a tag checkout that is supposed to be exactly its tag. Excluded here
# rather than by asking every machine's config to be right, because the roots file is the
# thing most likely to change without anyone thinking about this.
#
# The deploy root's definition is IMPORTED, never restated: it honours POGA_DEPLOY_ROOT and
# a copy here would be a hand-maintained duplicate that silently disagrees the day that
# variable is set (P16).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "deploy"))
try:
    from runner import deploy_root as _deploy_root  # noqa: E402
except Exception:  # noqa: BLE001 - adoption must not depend on the deploy subsystem
    _deploy_root = None
CLAUDE_RUNTIME = "claude-code"
SESSION_STATE_DIRNAME = ".session-state"
HEARTBEAT_STALE_MIN = 15   # matches session.py — a heartbeat older than this is not live

# P19 caps: an upper bound on how much a single sweep can spend, so a
# misconfiguration or a flood of eligible briefs can never run the fleet away.
DEFAULT_MAX_REPOS = 12
DEFAULT_MAX_BRIEFS_PER_REPO = 3
DEFAULT_TIMEOUT_S = 1800   # 30 min per headless adoption session
DEFAULT_MAX_TURNS = 60

# WI-0315: the runner once sat unauthenticated, unseen, across several sweeps.
# Several consecutive stale sweeps
# left the watcher quieter than the person; escalate after ONE sweep using the
# existing standing-note mechanism (ADR-0046). The CLI override remains available.
DEFAULT_ESCALATE_AFTER_STALE = 1

RUNNER_LOG = production.state_path(
    FED_ROOT, "adopt-runner.jsonl", FED_ROOT / SESSION_STATE_DIRNAME / "adopt-runner.jsonl")
# The runner's single, overwritten last-sweep status — the dashboard source (mined by
# curate/metrics.py). Machine-local like everything under .session-state/ (ADR-0036):
# the runner writes it where it executes (the Runner's nightly sweep), and the metrics
# layer mines it there. This REPLACES the per-sweep comms note as the routine channel.
STATUS_PATH = production.state_path(
    FED_ROOT, "adopt-runner.status", FED_ROOT / SESSION_STATE_DIRNAME / "adopt-runner.status")
# The standing stale-login comms note — a single stable-named CONDITION note (not one
# per night), authored past the escalation threshold and cleared on recovery.
# Production queues condition/recovery notices; development retains tracked comms.
_PRODUCTION = production.resolve(FED_ROOT)
_COMMS = _PRODUCTION.comms_root if _PRODUCTION else FED_ROOT / "comms"
STALE_NOTE = _COMMS / "adopt-runner-stale.md"

# ── WI-0084 A: the dead-letter path ─────────────────────────────────────────────
# Per-brief attempt state across sweeps. Without it the runner's failure mode is
# SILENT INFINITE RETRY: a brief that fails verify fails again every night, forever,
# and nothing anywhere counts. Seen more than once — one member's brief for many nights, and other
# members' copies of it for several more — each of which
# stopped only because some unrelated change made the brief ineligible. Nobody was
# ever told. A pipeline that is currently quiet but has no dead-letter path is not a
# fixed pipeline; it is one between instances.
#
# Machine-local under .session-state/ (ADR-0036), like the status file: this is the
# runner's own bookkeeping about its own sweeps, written where it executes.
BRIEF_STATE_PATH = production.state_path(
    FED_ROOT, "adopt-runner.briefs.json", FED_ROOT / SESSION_STATE_DIRNAME / "adopt-runner.briefs.json")

# Consecutive verify/adoption failures before a brief is quarantined out of the nightly
# sweep. Three, per the item's own recommendation: one failure is noise, two is a
# coincidence, three is a brief that is not going to start working on its own.
DEFAULT_DEAD_LETTER_AFTER = 3

# How much of a verify's raw output rides the JSONL outcome record (WI-0304). The old
# 400 was a *headline* budget and it showed: one member's note detail began
# mid-token, having already lost the traceback header that would have explained it. Now
# that the headline is a composed one-line verdict, this number's only job is to preserve
# evidence for whoever debugs the brief, so it can afford to be generous.
VERIFY_OUTPUT_KEPT = 2000

# The standing dead-letter comms note — same discipline as STALE_NOTE: one stable-named
# note for a CONDITION, refreshed while it holds, removed the moment the condition
# clears. Never one file per night; that accretes and trains its reader to ignore it.
DEAD_LETTER_NOTE = _COMMS / "adopt-runner-dead-letter.md"


# --------------------------------------------------------------------------- #
# Pure logic — no I/O, unit-tested in tests/test_adopt_runner.py.
# --------------------------------------------------------------------------- #

def parse_frontmatter(text):
    """(header_dict, has_frontmatter). The brief dialect session.py and check-apply.py
    read, from the one shared reader (`sessionlib/brief.py`): a leading `---` block of
    `key: value` lines terminated by a line starting `---`. Keys lower-cased;
    `#`-commented lines skipped."""
    return _brief_header(text)


def classify_brief(header, has_fm):
    """(eligible: bool, route: str, reason: str) for one brief header.

    `route` is one of: 'agent' (runner adopts), 'code' (an auto brief — the code
    path owns it, not the runner), 'operator' (surfaces to operator). Mirrors the ADR-0050
    total partition and the check-apply.py delivery lint, read-side."""
    if not has_fm:
        return False, "operator", "no frontmatter — not an appliable brief"
    mode = header.get("apply", "").strip().lower()
    if mode == "auto":
        return False, "code", "apply: auto — the code path (apply-briefs) owns this, not the runner"
    if mode != "manual":
        return False, "operator", f"apply mode not manual ({mode or 'unset'!r})"
    reason = header.get("manual-reason", "").strip()
    if reason.lower() == "attended":
        return False, "operator", "manual-reason: attended — reserved for the operator's interactive review"
    if not header.get("verify", "").strip():
        return False, "operator", "manual brief with no verify: — not runner-eligible (surfaces to operator)"
    return True, "agent", f"manual + verify → agent path ({reason or 'no reason'})"


def repo_runtimes(config):
    """The declared runtime(s) for a repo's parsed session.config.json, as a list.
    The `runtimes` array wins; legacy `runtime` string reads as one element;
    defaults to [claude-code] (never guess non-Claude)."""
    if not isinstance(config, dict):
        return [CLAUDE_RUNTIME]
    rts = config.get("runtimes")
    if isinstance(rts, list) and rts:
        return [str(r) for r in rts]
    return [str(config.get("runtime") or CLAUDE_RUNTIME)]


def is_claude_member(config):
    """True iff the member declares the Claude binding AT ALL — the same predicate the
    fleet push uses (`push-substrate.py`: `CLAUDE_RUNTIME not in rts` -> skip).

    ADR-0050 §2 scopes the runner to Claude members and says it filters "the same guard
    the fleet push uses"; it originally required EVERY declared runtime to be Claude,
    which is a stricter test and excluded a SYMMETRIC multi-runtime member
    — one whose Claude
    sessions are first-class co-equals, not a foreign runtime's residue. A hypothetical
    member (claude-code + another runtime) was skipped as "non-Claude" while the fleet push
    serviced it, so a brief addressed to its Claude side could never be adopted by the
    agent path (session 90). Only a member with NO Claude runtime is left to its own
    runtime's path."""
    return CLAUDE_RUNTIME in repo_runtimes(config)


def beat_age_minutes(live_json, now):
    """Minutes since a `.live` file's last heartbeat, or None if unparseable.
    `now` is injected for testability."""
    ts = (live_json or {}).get("last_beat") or (live_json or {}).get("started")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    ref = now if dt.tzinfo else now.replace(tzinfo=None)
    return (ref - dt).total_seconds() / 60.0


def repo_is_live(live_jsons, now, stale_min=HEARTBEAT_STALE_MIN):
    """True iff ANY parsed `.live` payload has a heartbeat fresher than stale_min —
    i.e. a session is (or may be) running. Unparseable/old markers don't count."""
    for lj in live_jsons:
        age = beat_age_minutes(lj, now)
        if age is not None and age <= stale_min:
            return True
    return False


def fold_status(prior, *, result, stamp, detail="", adopted=0, failed=0,
                repos_scanned=0, eligible=0):
    """Pure: fold this sweep's outcome into the prior persisted status → new status dict.

    The runner is stateless across sweeps except for what this file carries, so the
    stale-streak counter and its first-seen timestamp live here. `result` is one of:

      * `adopted`        — authenticated, at least one brief adopted
      * `no-eligible`    — authenticated, swept, nothing runner-eligible (healthy steady state)
      * `failed`         — authenticated, attempted adoption(s), none survived verify
      * `unauthenticated`— auth preflight/mid-sweep abort (the stale-token case)
      * `error`          — the sweep itself errored (not an auth verdict either way)

    Three-way on auth: `unauthenticated` extends the stale streak (preserving the
    streak's first-seen `stale_since`); any AUTHENTICATED completion (adopted /
    no-eligible / failed — all prove the runner is alive and auth works) resets the
    streak and stamps `last_success`; an `error` is agnostic and carries prior state.

    WI-0084 finding A. `last_success` is NOT what its name says. It is stamped by any
    authenticated completion **including a sweep in which every brief failed** — which is
    correct for what it exists to do (prove the runner is alive and auth works, the signal
    the stale-login escalation keys on) and wrong for what its name promises. Live proof:
    a status file reading `result: failed, failed: 1, eligible: 1` carried
    `last_success` stamped to the SAME SECOND, and that is the one field a human or a
    board glances at.

    The fix is NOT to redefine it — the auth-staleness escalation is load-bearing and
    would break, and every existing reader would silently change meaning. It is to add the
    field that was missing: `last_clean_sweep`, stamped only when the runner authenticated
    AND nothing failed. Two questions, two fields, so neither has to lie for the other
    ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)):

      * `last_success`      — "was the runner alive and authenticated?"  (unchanged)
      * `last_clean_sweep`  — "did it actually get through a sweep with nothing broken?"

    A pipeline whose only health field is the first one reads healthy while a brief fails
    every night for a fortnight, which is exactly what happened more than once (one
    member's brief, and other members' copies of it).
    """
    prior = prior or {}
    prev_stale = int(prior.get("consecutive_stale", 0) or 0)
    if result == "unauthenticated":
        consecutive_stale = prev_stale + 1
        stale_since = prior.get("stale_since") or stamp
        last_success = prior.get("last_success")
    elif result == "error":
        consecutive_stale = prev_stale
        stale_since = prior.get("stale_since")
        last_success = prior.get("last_success")
    else:  # adopted | no-eligible | failed — authenticated and completed
        consecutive_stale = 0
        stale_since = None
        last_success = stamp
    # Carried forward on every path that did not prove it: an unauthenticated or errored
    # sweep says nothing about adoption health, and a FAILED one says the opposite.
    prev_clean = prior.get("last_clean_sweep")
    clean = (result in ("adopted", "no-eligible")
             and not int(failed or 0))
    last_clean_sweep = stamp if clean else prev_clean
    return {
        "last_run": stamp,
        "result": result,
        "detail": detail,
        "adopted": adopted,
        "failed": failed,
        "eligible": eligible,
        "repos_scanned": repos_scanned,
        "consecutive_stale": consecutive_stale,
        "stale_since": stale_since,
        "auth": f"unauthenticated since {stale_since}" if consecutive_stale else None,
        "last_success": last_success,
        "last_clean_sweep": last_clean_sweep,
    }


def should_escalate(status, threshold=DEFAULT_ESCALATE_AFTER_STALE):
    """True iff the runner has been unauthenticated for >= threshold consecutive sweeps
    — the point at which the standing stale-login condition warrants a comms note."""
    return int((status or {}).get("consecutive_stale", 0) or 0) >= threshold


def brief_key(repo, brief_id):
    """The stable identity of one brief in one repo, for the attempt ledger."""
    return f"{pathlib.Path(repo).name}::{brief_id}"


def brief_fingerprint(text):
    """A short content hash of the brief, so an EDITED brief starts over.

    This is what keeps the dead-letter path from becoming a roach motel. A brief is
    quarantined for failing three times, and the obvious next move — for a person or for
    the authoring Architect — is to fix it and re-deliver. If the ledger keyed on the
    brief's id alone, the corrected brief would inherit its predecessor's failures and
    stay quarantined forever, and the fix would look exactly like the bug from outside.
    Content-keyed, a changed brief is a NEW brief to the counter, which is the honest
    reading: nothing has three times refused to work yet."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _one_line(text, limit=240):
    """`text` collapsed to a single line, truncated to `limit`.

    The load-bearing half is the collapse, not the truncation: a verdict is a promise
    that a reader gets ONE line, and a multi-line string keeps that promise only until
    the day some verify prints a newline. Making it structural here means every caller
    inherits it without remembering to."""
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ── WI-0304 B1: the verify contract ─────────────────────────────────────────────
# A `verify:` command answers exactly one question — did the adoption land correctly? —
# and it owes a person a SENTENCE, not a stack trace. One member's enrollment
# brief instead logged a raw traceback fragment ending in
# `KeyError: example-member`, thrown out of an inline one-liner of chained asserts, for fourteen
# consecutive nights. Nobody reading that log could learn WHAT CONDITION had failed.
#
# Two different facts were arriving through one exit code, and the runner recorded both
# the same way:
#
#   * **the verify RAN and said no** — the adoption failed. The brief is sound; the work
#     is wrong. Retrying is meaningful, and three retries is real evidence (the WI-0084
#     dead-letter counter is built on exactly that claim).
#   * **the verify BROKE** — nothing was checked at all. The brief is DEFECTIVE. The
#     adoption's fate is *unknown*, not bad, and retrying is pure waste: an uncaught
#     `KeyError` on one run raises the identical `KeyError` every night until a human
#     re-authors the command.
#
# Collapsing those two is what made fourteen nights of a broken check look like one
# stubborn adoption. Separating them is this section's whole job.

# The header line of a Python traceback. Requiring it is what keeps a line of ordinary
# prose that happens to read `ValueError: ...` from being mistaken for a crash.
_TRACEBACK_HEAD = "Traceback (most recent call last):"
# A traceback's terminal line: an exception type, optionally dotted, optionally with a
# message. Anchored at column 0 — every frame line above it is indented.
_EXC_LINE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?"
    r"([A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt|Warning))"
    r"(?::\s*(.*))?$")


def last_exception(output):
    """`(type, message)` of an uncaught Python exception that KILLED the run, else None.

    Two conditions, and the second is the one that earns its keep:

      1. a `Traceback (most recent call last):` header appears somewhere, and
      2. the **last non-blank line of the whole output** is the exception line.

    Condition 2 is what separates *the interpreter died here* from *a test runner
    printed somebody else's traceback*. A verify that runs a test suite prints
    tracebacks for failing tests and then a summary line (`FAILED (failures=1)`,
    `=== 1 failed in 0.4s ===`) — that run answered the question and is a plain
    `failed`, not a defective brief. An uncaught exception has nothing after it,
    because there was no longer a process to print anything.

    **What this assumes** ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes)):
    that `output` is the COMPLETE captured output, not a tail. Truncation is exactly
    what defeated the old detail line — one member's note 400-char tail
    began mid-token and had already cut the traceback header
    off. `run_verify` therefore classifies on the full text and truncates only for
    storage, never before this call."""
    text = output or ""
    if _TRACEBACK_HEAD not in text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    m = _EXC_LINE.match(lines[-1].rstrip())
    if not m:
        return None
    return m.group(1), (m.group(2) or "").strip()


def verify_verdict(command, returncode, output):
    """`(kind, verdict)` for one verify run. `kind` is `pass` | `failed` | `defective`.

    **The verdict is always exactly one line, never empty, and never a stack trace** —
    that is the contract, and `_one_line` makes it structural rather than a convention
    each branch below has to keep. It names what was CHECKED (the command — the only
    account of intent that always exists) and what the run reported ABSENT.

    The `defective` classification is reserved for a verify that did not answer the
    question it was asked:

      * it raised an uncaught exception — `KeyError`, `FileNotFoundError`, a
        `SyntaxError` in the one-liner itself;
      * it raised a **bare** `AssertionError` naming no condition, which is the same
        failure wearing the costume of an answer: the reader learns that Python objected
        and nothing else;
      * the shell could not run it at all (exit 126/127, `command not found`).

    Three deliberate NON-defective cases, because each is a legitimate verify that this
    check must not reclassify:

      * `AssertionError: <message>` — the verify's own designed failure path, and it
        named the condition. That IS a verdict. `failed`.
      * a silent non-zero exit — `test -f adopted.txt`, `grep -q`, a `session.py` check.
        These print nothing by design and the command text names both what was checked
        and, by failing, what was absent. Calling silence defective would condemn the
        entire class of self-describing one-word verifies, which are the good ones.
      * an exception *type name* appearing in output with no traceback header — a test
        runner reporting someone else's caught error, not a crash of the verify.
    """
    cmd = _one_line(command, 200) or "(empty command)"
    if returncode == 0:
        return "pass", f"verify passed — {cmd}"

    exc = last_exception(output)
    if exc:
        etype, emsg = exc
        if etype == "AssertionError" and emsg:
            return "failed", _one_line(
                f"verify failed: {emsg} — checked by: {cmd}")
        if etype == "AssertionError":
            return "defective", _one_line(
                f"DEFECTIVE BRIEF — the verify command raised a bare AssertionError "
                f"naming no condition, so nothing is recorded about what was absent; "
                f"give every assert a message. Command: {cmd}")
        return "defective", _one_line(
            f"DEFECTIVE BRIEF — the verify command crashed instead of reporting a "
            f"verdict: uncaught {etype}" + (f": {emsg}" if emsg else "") +
            f". Nothing was checked. Command: {cmd}")

    lowered = (output or "").lower()
    if returncode in (126, 127) or "command not found" in lowered:
        return "defective", _one_line(
            f"DEFECTIVE BRIEF — the shell could not run the verify command "
            f"(exit {returncode}), so nothing was checked. Command: {cmd}")

    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    if not lines:
        return "failed", _one_line(
            f"verify failed (exit {returncode}) and printed nothing — the command is the "
            f"whole account of what was checked: {cmd}")
    return "failed", _one_line(
        f"verify failed (exit {returncode}): {lines[-1]} — checked by: {cmd}")


def fold_brief_state(prior, key, *, fingerprint, result, stamp, detail="",
                     dead_letter_after=DEFAULT_DEAD_LETTER_AFTER):
    """The attempt ledger after one adoption outcome. Pure; returns a NEW dict.

    Four transitions, and each is a decision rather than bookkeeping:

      * **success clears the record entirely.** Self-clearing, like the stale note — a
        brief that adopted is not a brief with a history, and leaving a decaying count
        behind would eventually quarantine something that works.
      * **a changed brief resets the count.** See `brief_fingerprint`: the corrected
        brief must not inherit the broken one's failures, or the dead-letter state is a
        trap with no exit.
      * **failure increments, and crosses into `dead` at the threshold.** The count is
        of CONSECUTIVE failures of the SAME content, which is the only count that
        supports the claim "this will not start working on its own."
      * **`error` (the runner broke, not the brief) does NOT increment.** An
        infrastructure failure is not evidence against the brief, and counting it would
        quarantine innocent briefs on a bad night — the same conflation the status
        field's `last_success` had, one layer down.
      * **`defective` (WI-0304 B1) quarantines on the FIRST occurrence.** The threshold
        of three buys evidence for a claim — *this is not going to start working on its
        own* — and a defective verify supplies that evidence in one night rather than
        three: a command that raised an uncaught `KeyError` on one run raises the identical
        `KeyError` every night until a human re-authors it. Nothing about the second and
        third attempts is informative, and each one costs a full `claude -p` adoption
        session plus another comms note. The escape hatch is unchanged and needs no new
        machinery: the ledger is keyed on brief CONTENT, so the corrected brief is a new
        brief to the counter and is eligible again on the very next sweep.
    """
    prior = dict(prior or {})
    rec = dict(prior.get(key) or {})
    if result == "adopted":
        prior.pop(key, None)
        return prior
    if result not in ("failed", "defective"):
        return prior                        # error / skipped: says nothing about the brief
    if rec.get("fingerprint") != fingerprint:
        rec = {"fingerprint": fingerprint, "failures": 0, "first_failed": stamp}
    rec["failures"] = int(rec.get("failures", 0) or 0) + 1
    rec["last_failed"] = stamp
    rec["last_detail"] = evidence.clip(detail or "", 400, keep="head", one_line=True)
    rec.setdefault("first_failed", stamp)
    if result == "defective":
        rec["defective"] = True
        rec.setdefault("dead_since", stamp)
    elif rec["failures"] >= dead_letter_after and not rec.get("dead_since"):
        rec["dead_since"] = stamp
    prior[key] = rec
    return prior


def is_dead_lettered(state, key, fingerprint):
    """True iff this exact brief content is quarantined out of the nightly sweep.

    The fingerprint is checked here too, not just on write: a brief edited AFTER being
    quarantined must become eligible again on the very next sweep, without anyone having
    to clear the ledger by hand — a quarantine that needs a human to release it is
    another chore resting on someone remembering."""
    rec = (state or {}).get(key) or {}
    return bool(rec.get("dead_since")) and rec.get("fingerprint") == fingerprint


def dead_letter_entries(state):
    """Every quarantined brief, newest-quarantined last. Pure; for the note and report."""
    out = [(k, v) for k, v in (state or {}).items()
           if isinstance(v, dict) and v.get("dead_since")]
    return sorted(out, key=lambda kv: (kv[1].get("dead_since") or "", kv[0]))


# --------------------------------------------------------------------------- #
# I/O + discovery.
# --------------------------------------------------------------------------- #

def load_config(repo):
    """Parsed session.config.json for a repo, or {} if absent/unreadable."""
    p = repo / "session.config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_repos():
    """Every converged Architect repo reachable on this machine: the union of the
    depth-bounded walk of the search roots and the machine-local locator map
    (repo-paths.local — catches repos off the walk roots, e.g. one on a synced cloud drive).
    A repo qualifies if it is a git repo carrying the convergence markers and is not
    the federation itself. Runtime filtering happens later (per-repo)."""
    found = {}

    def consider(path):
        repo = pathlib.Path(path).resolve()
        if repo == FED_ROOT or not (repo / ".git").is_dir():
            return
        if _deploy_root is not None:
            try:
                droot = _deploy_root().resolve()
                if repo == droot or droot in repo.parents:
                    return
            except OSError:
                pass
        if not all((repo / m).is_file() for m in CONVERGENCE_MARKERS):
            return
        found.setdefault(repo, repo)

    for root in read_roots_config(ROOTS_CONFIG):
        base = pathlib.Path(root).resolve()
        if not base.is_dir():
            continue
        for dirpath, dirnames, _ in walk_tree(base, MAX_DEPTH - 1):
            if ".git" in dirnames:
                consider(dirpath)
    for path in read_repo_paths_config(REPO_PATHS_CONFIG).values():
        consider(path)
    return sorted(found.values(), key=lambda p: str(p))


def inbox_pending_dir(repo, config):
    """The repo's pending-inbox dir from its config `inbox` field, or None."""
    inbox = config.get("inbox")
    if not inbox:
        return None
    return repo / inbox


def pending_briefs(pending):
    if pending is None or not pending.is_dir():
        return []
    return sorted(p for p in pending.glob("*.md") if p.name != "index.md")


def read_live_jsons(repo):
    """Every parseable `.session-state/*.live` payload in a repo (for the liveness
    guard). Missing dir / unreadable files are simply absent from the list."""
    state = repo / SESSION_STATE_DIRNAME
    out = []
    if not state.is_dir():
        return out
    for p in state.glob("*.live"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def run_git(repo, args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def git_head(repo):
    ok, out = run_git(repo, ["rev-parse", "HEAD"])
    return out if ok else None


# --------------------------------------------------------------------------- #
# Adoption.
# --------------------------------------------------------------------------- #

class AuthError(Exception):
    """Headless Claude could not authenticate — a stale/absent OAuth token or a
    missing API key. This is an ENVIRONMENT condition, not a brief condition: it
    aborts the whole sweep (every adoption would fail identically) and surfaces a
    runner-level `blocked` note. It must never be mistaken for a brief that failed
    verification (which would wrongly blame the brief and reset it)."""


# Signatures of an auth failure in `claude -p` output. The subscription-login path
# (ADR-0050 auth note) reads the login's token from the keychain, which EXPIRES and only
# refreshes in an interactive session — so an unattended sweep can find a stale
# token ("access token has expired") or, over a context that can't read the keychain
# at all, "not logged in". Either way the fix is human (re-auth interactively) or an
# API key, never a brief edit.
AUTH_ERROR_SIGNS = ("not logged in", "please run /login", "oauth", "authenticate",
                    "401", "access token has expired", "expired")


def is_auth_error(text):
    low = (text or "").lower()
    return any(sign in low for sign in AUTH_ERROR_SIGNS)


def preflight_auth(claude_bin, timeout_s=90):
    """Cheap check that headless Claude is authenticated BEFORE the sweep spawns any
    adoption. Returns (ok, detail). A trivial one-token prompt; an auth failure here
    aborts the sweep with one clear blocked note instead of N doomed adoptions.

    Runs in a NEUTRAL cwd (a temp dir), never a governed repo: `claude -p` fires the
    SessionStart hook of whatever repo it starts in, so a preflight run inside the
    federation (the runner's own WorkingDirectory under launchd) would spawn a phantom
    federation session every sweep. The adoption call deliberately runs in the target
    repo; the preflight must not run in any."""
    cmd = [claude_bin, "-p", "reply with exactly: OK", "--output-format", "json"]
    try:
        neutral = tempfile.gettempdir()
        proc = subprocess.run(cmd, cwd=neutral, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        return False, f"claude binary not found: {claude_bin!r}"
    except subprocess.TimeoutExpired:
        return False, f"auth preflight timed out after {timeout_s}s"
    blob = (proc.stdout or "") + (proc.stderr or "")
    try:
        result = json.loads(proc.stdout)
    except (ValueError, TypeError):
        result = None
    if not isinstance(result, dict):
        result = None
    reason = _preflight_reason(result, proc.stdout, proc.stderr)
    if proc.returncode != 0 and is_auth_error(blob):
        return False, f"not authenticated: {reason}"
    if result is not None and result.get("is_error") and is_auth_error(str(result.get("result"))):
        return False, f"not authenticated: {reason}"
    if proc.returncode != 0:
        return False, f"claude preflight exit {proc.returncode}: {reason}"
    return True, "authenticated"


def _preflight_reason(result, stdout, stderr):
    """One line saying why the preflight failed, in claude's own words.

    With `--output-format json` the reply opens with the usage/cost preamble, so a
    200-char head clip of the raw output held only that and cut the reason off
    (WI-0446). A parsed reply reports its own `is_error` / `subtype` / `result`; any
    stderr rides along. Unparsed output falls back to stderr first, then stdout."""
    stderr = (stderr or "").strip()
    if result is None:
        raw = "\n".join(p for p in (stderr, (stdout or "").strip()) if p)
        return evidence.clip(raw, 200, keep="head", one_line=True)
    parts = [f"is_error={result.get('is_error')}", f"subtype={result.get('subtype')}",
             "result=" + evidence.clip(str(result.get("result")), 200,
                                       keep="head", one_line=True)]
    if stderr:
        parts.append("stderr=" + evidence.clip(stderr, 200, keep="head", one_line=True))
    return " ".join(parts)


ADOPT_PROMPT = """\
A federation redistribution brief is pending in your inbox and needs adoption.

Brief: {brief_rel}

This is a headless background adoption session (federation R3 agent path, ADR-0050),
run unattended, overnight. Work as your own Architect, per your role doc and
your standard session protocol. Steps:

1. Read the brief. Apply exactly the change it specifies to your role doc / files —
   nothing more. Bump your role-doc version and add the CHANGELOG entry as the brief
   directs. LEAVE THE BRIEF WHERE IT IS — do NOT move it out of `pending/`. The runner
   files it into `applied/` itself, with a receipt stamp, once your verify has passed.
   Moving it by hand destroys the only receipt this adoption produces (WI-0223), and a
   brief sitting in `applied/` that nothing stamped is indistinguishable from one filed
   with the work never done (WI-0041).
2. Run the brief's `Verify:` command yourself and confirm it passes before closing.
3. Close through your session-end harness COMMIT-ONLY: run
   `python3 session.py end --title "<short title>" --commit --no-push`
   The `--no-push` is REQUIRED and is not a formality — since WI-0355 `end` publishes by
   default, so omitting it pushes. Do not push. The commit stays local; it is pushed by
   your next interactive session.

   This close is ALREADY AUTHORIZED and needs no `--confirm`: the runner marked this
   session as an unattended run and wrote the record `end` resolves that against
   (WI-0331), so the close writes an honest citation saying a machine ran it and no
   human agreed. **NEVER pass `--confirm` here.** That field records what a HUMAN said,
   there is no human in this session, and inventing a sentence for it corrupts the only
   evidence anyone has that a close was agreed to. If `end` refuses anyway, the runner
   failed to mark this session: leave the session OPEN, say exactly that, and stop —
   the runner resets and reports, which is the correct outcome. A fabricated `--confirm`
   is never the correct outcome.

Do not take on any other work. If you cannot apply the brief cleanly, do NOT force it —
leave it pending, explain why, and close.
"""


_ROLE_DOC_VERSION = re.compile(r"^\*\*Version:\*\*\s*(\S+)\s*$", re.M)
_ALREADY_STAMPED = re.compile(r"^state:\s*applied\s*$", re.M)


def member_role_doc_version(repo, config):
    """The member's role-doc version as it stands AFTER the adoption, or None.

    None is a real answer here, not a gap to paper over — the caller writes it into the
    stamp as the literal word `unknown`. The stamp is a RECEIPT, and the only reason a
    receipt is worth reading is that nothing in it was inferred; a guessed version would
    make every stamp exactly as trustworthy as the self-report it replaces
    ([`no-fabricated-data`]).

    Read after the adoption commit on purpose: the brief directs a version bump, so the
    value that belongs in `applied-at-version` is the one the adoption produced, never
    the one the brief predicted it would."""
    name = (config or {}).get("role_doc")
    doc = (repo / name) if name else None
    if doc is None or not doc.is_file():
        return None
    try:
        m = _ROLE_DOC_VERSION.search(doc.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m.group(1).strip() if m else None


def _stamp_block(stamp, version):
    """The four receipt lines, in the engine's own plain-YAML form.

    MATCHING THE ENGINE IS THE POINT. `session.py file_applied_brief()` writes `applied:`
    / `applied-at-version:` / `state: applied` when the code path files an `apply: auto`
    brief, and `_applied_without_a_receipt()` keys on exactly that. Writing a different
    shape here would give the R3 path a receipt no existing detector could read, which is
    the same gap in a new spelling. `applied-by` is the one addition: engine and runner
    are different actors reaching the same terminal state, and a receipt that cannot say
    which one filed it cannot answer the question WI-0041 was opened to ask."""
    return (f"applied: {stamp}\n"
            f"applied-at-version: {version or 'unknown'}\n"
            f"applied-by: runner\n"
            f"state: applied\n")


def _inject_stamp(text, stamp, version):
    """The brief with its receipt in the frontmatter, minting a fence if it has none.

    A FENCELESS BRIEF GETS ONE. `session.py file_applied_brief()` silently SKIPS the
    stamp when there is no `---` block and moves the file anyway — which is how 28 of the
    42 briefs in `proposed-edits/federation-arch/applied/` came to be structurally
    unstampable, and therefore permanently unauditable, without anything ever reporting a
    problem. A receipt that quietly declines to exist for a whole class of input is the
    failure this item is about ([`declare-what-a-check-assumes`]), so the fence is minted
    rather than the stamp dropped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[: end + 1] + _stamp_block(stamp, version) + text[end + 1:]
    return "---\n" + _stamp_block(stamp, version) + "---\n\n" + text


def file_and_stamp_brief(repo, config, brief_path, stamp, version=None):
    """File an adopted brief `pending/` → `applied/`, stamping the runner's receipt.
    Returns `(dest_or_None, detail)`. Never raises.

    WHY THIS EXISTS (WI-0223). The R3 agent path used to file briefs by telling the
    headless model to move the file — `ADOPT_PROMPT` step 1 said so in words. A model
    moving a file is a self-report, and the whole finding of WI-0041 is that the filing
    is exactly the thing that must not be self-reported: a hypothetical member's enrollment brief was
    moved to `applied/` with none of its work done, which removed it from this runner's
    own queue, so the enrollment failed silently instead of loudly. The guard WI-0041
    built then had to go blind on every `manual` brief precisely because the R3 path never
    stamped one — 24 false lines on its first run. This closes that half. ADR-0013
    §"When an edit is applied" step 4 has mandated a manual-path stamp since the receipt
    ritual was written; this mechanizes it rather than deciding it ([P15]).

    IT DOES NOT ASSUME THE MODEL OBEYED THE PROMPT. If the session moved the brief to
    `applied/` anyway, it is stamped in place instead of being reported missing. The
    prompt is an instruction to a model, and a receipt mechanism that only works when the
    model complies is a receipt mechanism that fails on exactly the sessions worth
    auditing. A brief in neither directory returns a reason and invents nothing.

    Idempotent: an already-stamped brief is left untouched rather than double-stamped."""
    pending = inbox_pending_dir(repo, config)
    if pending is None:
        return None, "member declares no inbox — nowhere to file"
    applied = pending.parent / "applied"
    src, in_place = brief_path, False
    if not src.is_file():
        candidate = applied / brief_path.name
        if not candidate.is_file():
            return None, (f"{brief_path.name} is in neither pending/ nor applied/ — "
                          f"nothing to file, and nothing was invented")
        src, in_place = candidate, True
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"could not read the brief to stamp it ({type(e).__name__})"
    if _ALREADY_STAMPED.search(text):
        return src, "already carried a receipt — left exactly as it was"
    try:
        applied.mkdir(parents=True, exist_ok=True)
        atomic_write(applied / src.name, _inject_stamp(text, stamp, version))
        if not in_place:
            src.unlink()
    except OSError as e:
        return None, f"could not write the receipt ({type(e).__name__}: {e})"
    return applied / src.name, (
        "stamped in place — the session had already moved it to applied/" if in_place
        else "filed pending/ → applied/ with a runner receipt")


def _launch_dir(repo, config):
    """Where the Architect's OWN persona actually loads `CLAUDE.md` from.

    `repo` itself, unless the member declares `pad_dir`: a resident-
    runtime member's repo root is owned by another agent (with its own
    `CLAUDE.md` and its own permission lockdown), and the Architect's real launch
    home is a sibling pad outside that agent's load path. Spawning `claude -p` at `repo` for such a member boots the
    WRONG persona — with `--permission-mode bypassPermissions`, under no gates at
    all. Falls back to `repo` if the declared pad doesn't actually exist on this
    machine (never invents a launch site the member hasn't built)."""
    pad = str((config or {}).get("pad_dir") or "").strip()
    if not pad:
        return repo
    candidate = (repo / pad).resolve()
    return candidate if candidate.is_dir() else repo


def unattended_run_id(brief_path, now=None):
    """A short, per-run id for the adoption session about to be spawned.

    Drawn from the brief and the clock rather than a counter on purpose: this id names
    ONE spawn, it is never allocated from a shared namespace, and nothing else has to
    agree about it — so `numbers-are-drawn-never-picked`'s allocator requirement does not
    bind here (that rule governs identifiers shared across concurrent writers: WI/ADR/OPS
    numbers, session ordinals). It only has to be unique enough that two sweeps in the
    same second cannot collide on one record, which the brief path supplies."""
    seed = f"{brief_path}|{(now or datetime.now(timezone.utc)).isoformat()}"
    return "A-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]


def write_unattended_run_record(launch_dir, brief_path, now=None):
    """Write the run record the spawned session's `end` resolves its close against, and
    return (run_id, path). Returns (run_id, None) if it could not be written.

    THIS IS THE HALF THAT MAKES THE CLOSE HONEST RATHER THAN MERELY UNBLOCKED (WI-0331).
    Setting the environment variable alone would let the session assert its own
    authorization from a marker — exactly what ADR-0112 D7 says an environment variable
    can never be — so the runner leaves an artifact behind and the session cites what it
    READ. Same substitution ADR-0113 D3 made for a dispatch.

    Machine-local and gitignored under `.session-state/` (ADR-0036), like every other
    ephemeral session sidecar, and removed by the writer as soon as the spawn returns:
    the authorization is scoped to the run, so a record outliving it would authorize a
    close nobody asked for. Best-effort — a repo whose state dir cannot be written still
    gets its adoption attempted, and the close then records UNRESOLVED rather than
    failing outright, which is ADR-0113's fail-open direction for ADR-0113's reason.

    The federation writing one ephemeral file into a member's gitignored state dir is
    within ADR-0050's existing writer relationship (the runner already authors that
    member's comms notes, files its brief, and resets its tree); no tracked state and no
    single-writer boundary ([P13](principles/master.md#p13--single-writer-per-state)) is
    touched by it."""
    run_id = unattended_run_id(brief_path, now)
    try:
        state = pathlib.Path(launch_dir) / SESSION_STATE_DIRNAME
        state.mkdir(parents=True, exist_ok=True)
        path = state / f"unattended-run-{run_id}.json"
        atomic_write(path, json.dumps({
            "run_id": run_id,
            "kind": "adoption",
            "runner": "curate/adopt-runner.py",
            "subject": f"brief {brief_path.stem}",
            "brief": brief_path.name,
            "started": (now or datetime.now(timezone.utc)).isoformat(),
        }, ensure_ascii=False, indent=2) + "\n")
        return run_id, path
    except OSError:
        return run_id, None


def clear_unattended_run_record(path):
    """Retire the record the moment the spawn returns. The runner wrote it, so the runner
    removes it — a retention rule enforced by the code that owns the file rather than left
    to the janitor's sweep ([`retention-enforced-by-code`](habits/master.md#retention-enforced-by-code)),
    because an authorization artifact that outlives its run is a standing licence to close
    a session nobody started."""
    if path is None:
        return
    try:
        pathlib.Path(path).unlink()
    except OSError:
        pass


def unattended_env(run_id):
    """The environment the adoption session runs in.

    TWO JOBS, and the second one is a defect this fix would otherwise have shipped. It
    SETS `POGA_UNATTENDED_RUN` so the spawned session can resolve its own close, and it
    STRIPS the dispatch handles, because the sweep is routinely run from inside a
    dispatched lane and `subprocess.run` passes the ambient environment through wholesale.
    Without the strip, a member's `end` would read the FEDERATION's `POGA_DISPATCH`,
    resolve nothing against its own store, and write `dispatch D-xxxxxx — UNRESOLVED`
    into that member's permanent record — a citation naming a lane in another repo that
    had nothing to do with it. Same ambient-inheritance shape the gate's suite hit
    (WI-0242/0250); removed at the spawn rather than trusted not to be set.

    THE LIST IS A THIRD COPY AND THAT IS A KNOWN GAP, said here rather than left to be
    discovered. `tests/coord_fixture.DISPATCH_ENV_VARS` holds the same names, and the
    handoff already records that the production side has no such list and that it "would
    have to move into the harness first" — the gate's own suite inherits these variables
    for exactly this reason. Not solved here: `curate/` cannot import `sessionlib`, whose
    modules are executed into a session namespace rather than imported, so there is no
    shared home to point at yet. A wrong copy fails OPEN (a variable that stops being
    stripped leaks, it does not break the spawn), which is why naming it beats blocking
    this item on the refactor."""
    env = dict(os.environ)
    for var in ("POGA_DISPATCH", "POGA_DISPATCH_ITEM", "POGA_LANE_CLOSE",
                "POGA_TURN_BUDGET", "POGA_INVOKED_FROM", "POGA_RUNTIME_ID"):
        env.pop(var, None)
    env["POGA_UNATTENDED_RUN"] = run_id
    return env


def run_claude_adopt(repo, config, brief_path, claude_bin, model, timeout_s, max_turns, verbose):
    """Spawn a headless `claude -p` adoption session at the Architect's OWN launch
    site (`_launch_dir` — `repo`, or its declared pad for a resident-runtime member).
    Returns (ok, detail). ok is False on non-zero exit, a JSON `is_error`, or a
    timeout. Raises AuthError if the failure is an authentication problem (aborts the
    sweep — every adoption would fail identically; never blames the brief)."""
    launch_dir = _launch_dir(repo, config)
    try:
        brief_rel = str(brief_path.relative_to(launch_dir))
    except ValueError:
        # The brief lives under `repo`, not under a sibling pad — give an absolute
        # path so it still resolves regardless of where the session launches.
        brief_rel = str(brief_path.resolve())
    prompt = ADOPT_PROMPT.format(brief_rel=brief_rel)
    cmd = [claude_bin, "-p", prompt,
           "--permission-mode", "bypassPermissions",
           "--output-format", "json",
           "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    run_id, record = write_unattended_run_record(launch_dir, brief_path)
    try:
        proc = subprocess.run(cmd, cwd=str(launch_dir), capture_output=True, text=True,
                              timeout=timeout_s, env=unattended_env(run_id))
    except subprocess.TimeoutExpired:
        return False, f"claude -p timed out after {timeout_s}s"
    finally:
        clear_unattended_run_record(record)
    if verbose and proc.stdout:
        print(evidence.clip(proc.stdout, 2000, keep="tail"))
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        if is_auth_error(blob):
            raise AuthError("claude -p not authenticated: "
                            + evidence.clip(blob, 200, keep="head", one_line=True))
        return False, (f"claude -p exit {proc.returncode}: "
                       + evidence.clip(proc.stderr or "", 300, keep="head", one_line=True))
    # --output-format json emits a single result object; a well-formed run with
    # is_error true is still a failure. Tolerate non-JSON (treat exit 0 as ok).
    try:
        result = json.loads(proc.stdout)
        if isinstance(result, dict) and result.get("is_error"):
            rdetail = str(result.get("result"))
            if is_auth_error(rdetail):
                raise AuthError("claude -p not authenticated: "
                                + evidence.clip(rdetail, 200, keep="head", one_line=True))
            return False, ("claude -p reported is_error: "
                           + evidence.clip(rdetail, 300, keep="head", one_line=True))
    except (ValueError, TypeError):
        pass
    return True, "adoption session completed"


def run_verify(repo, command, timeout_s=600):
    """Run the brief's Verify command in the repo. Returns `(kind, verdict, raw)`.

    `kind` is `pass` | `failed` | `defective` (see `verify_verdict`); `verdict` is the
    one-line human sentence that goes into the log, the comms note and the sweep
    report; `raw` is the untouched output tail.

    The raw output is **demoted, never destroyed** — it rides the JSONL outcome record
    so the evidence survives for whoever debugs the brief, while the headline everyone
    actually reads is a sentence. Classification runs on the FULL text: truncating
    first is what turned one member's traceback into an unreadable fragment
    with its own header cut off.

    A timeout stays `failed` rather than `defective`, deliberately. A verify that never
    returns has indeed reported no verdict, but unlike a crash it is not *provably* the
    brief's fault — a wedged or merely slow environment produces the same silence, and
    calling it defective would quarantine an innocent brief on one bad night. Left on
    the counter, three timeouts still quarantine it, which is the right outcome for a
    check that genuinely never returns."""
    try:
        proc = subprocess.run(command, cwd=str(repo), shell=True,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        partial = "".join(
            (s.decode("utf-8", "replace") if isinstance(s, bytes) else (s or ""))
            for s in (e.stdout, e.stderr))
        return ("failed",
                _one_line(f"verify TIMED OUT after {timeout_s}s without reporting a "
                          f"verdict — command: {_one_line(command, 200)}"),
                evidence.clip(partial.strip(), VERIFY_OUTPUT_KEPT, keep="tail"))
    out = (proc.stdout + proc.stderr).strip()
    kind, verdict = verify_verdict(command, proc.returncode, out)
    return kind, verdict, evidence.clip(out, VERIFY_OUTPUT_KEPT, keep="tail")


def write_comms_blocked(repo, config, brief_id, detail, stamp, defective=False):
    """Author a `comms/` `blocked` note (ADR-0046) in the target and commit ONLY that
    file, so a failed adoption surfaces to operator on the ADR-0046 rail and rides the
    target's next auto-push (ADR-0035). Best-effort: never raises.

    `defective` (WI-0304 B1) switches the note from *this adoption did not verify* to
    *this brief's check is broken*, because they are addressed to different people. A
    failed adoption is the member's problem and might pass tomorrow. A defective brief
    is the AUTHOR's problem, will fail identically tomorrow, and the note should say so
    rather than leave a reader re-reading a traceback for a fourteenth night."""
    try:
        roots = production.resolve(FED_ROOT)
        comms = roots.comms_root if roots else repo / "comms"
        comms.mkdir(parents=True, exist_ok=True)
        system = config.get("architect_id") or config.get("architect_name") or repo.name
        date = stamp[:10]
        slug = brief_id.replace(" ", "-")[:60]
        kind = "defective" if defective else "failed"
        note = comms / f"{date}-adopt-{kind}-{slug}.md"
        if defective:
            body = (
                f"---\n"
                f"date: {date}\n"
                f"system: {system}\n"
                f"type: blocked\n"
                f"summary: Brief `{brief_id}` is DEFECTIVE — its `verify:` command "
                f"crashed instead of reporting a verdict, so the adoption could not be "
                f"judged. Quarantined; the brief is still pending.\n"
                f"---\n\n"
                f"The federation's headless adoption runner attempted to adopt "
                f"`{brief_id}` in this repo. The adoption itself may have been fine — "
                f"**nobody knows**, because the brief's `verify:` command did not run to "
                f"a verdict. The working tree was reset to the pre-adoption commit "
                f"(an unverified edit is never kept) and the brief left pending. No "
                f"change landed.\n\n"
                f"**Verdict:** {detail}\n\n"
                f"This is a defect in the BRIEF, not in this repo's adoption of it, and "
                f"it will fail identically on every future sweep — so the runner has "
                f"quarantined it rather than retry. **The fix belongs to whoever "
                f"authored the brief:** re-author the `verify:` command so that it "
                f"reports a one-line human verdict on failure, naming what it checked "
                f"and what was absent (give every `assert` a message; guard the lookups "
                f"that can raise). Re-delivering the corrected brief clears the "
                f"quarantine automatically — the ledger is keyed on brief content. Full "
                f"audit trail, including the raw output, in the federation's "
                f"`.session-state/adopt-runner.jsonl`.\n"
            )
            subject = (f"docs(comms): brief {brief_id} is defective — verify reported no "
                       f"verdict, quarantined (ADR-0050)")
        else:
            body = (
                f"---\n"
                f"date: {date}\n"
                f"system: {system}\n"
                f"type: blocked\n"
                f"summary: Background adoption (R3, ADR-0050) of brief `{brief_id}` failed "
                f"verification and was reset; the brief is still pending.\n"
                f"---\n\n"
                f"The federation's headless adoption runner attempted to adopt "
                f"`{brief_id}` in this repo and it did not pass its `Verify:` gate, so the "
                f"working tree was reset to the pre-adoption commit and the brief left "
                f"pending. No change landed.\n\n"
                f"**Verdict:** {detail}\n\n"
                f"This brief needs a look: either the brief's ops don't apply cleanly here, "
                f"or its `Verify:` command is wrong. Adopt it in an interactive session, or "
                f"re-author/mark it `manual-reason: attended`. Full audit trail in the "
                f"federation's `.session-state/adopt-runner.jsonl`.\n"
            )
            subject = (f"docs(comms): background adoption of {brief_id} failed verify — "
                       f"reset, brief pending (ADR-0050)")
        if roots:
            # One message per failed attempt: the local note keeps its day name, the mail
            # gets a unique name and an edit-id (a repeat failure the same day otherwise
            # collides at the receiver with no edit-id to tell the two apart).
            message_id, mail_name, text = mailnames.identity(
                note.name,
                "---\napply: manual\nmanual-reason: attended\n"
                "attended-because: adoption failure requires review\n---\n\n" + body)
            mailqueue.enqueue(roots, config.get("architect_id") or "federation-arch", mail_name,
                              text, message_id=message_id,
                              provenance={"producer": "adopt.runner", "brief_id": brief_id})
            atomic_write(note, body)
            return note
        atomic_write(note, body)
        run_git(repo, ["add", str(note.relative_to(repo))])
        run_git(repo, ["commit", "-m", subject])
        return note
    except Exception as e:
        print(f"    (could not author comms blocked note: {e})", file=sys.stderr)
        return None


def read_brief_state(path=BRIEF_STATE_PATH):
    """The per-brief attempt ledger, or {} on a fresh/corrupt file (WI-0084).

    A missing ledger is a fresh start, never an error. Erring toward forgetting is the
    right direction here: forgetting re-attempts a brief that would have been skipped,
    which costs one sweep; remembering wrongly quarantines a brief that works."""
    try:
        p = pathlib.Path(path)
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_brief_state(state, path=BRIEF_STATE_PATH):
    """Persist the attempt ledger. Best-effort; a failure must never fail a sweep."""
    try:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(state, indent=2, sort_keys=True) + "\n")
    except Exception as e:
        print(f"  (could not persist brief attempt ledger: {e})", file=sys.stderr)


def _queue_condition(path, body, cleared=False):
    """Each changed condition is durable mail; clearing sends a recovery notice too."""
    roots = production.resolve(FED_ROOT)
    if roots is None:
        return
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if not cleared and previous == body:
        return
    content = (f"# Resolved: {path.stem}\n\nThe previous condition has cleared.\n\n"
               + previous) if cleared else body
    content = ("---\napply: manual\nmanual-reason: attended\n"
               "attended-because: adoption runner condition requires review\n---\n\n" + content)
    # The LOCAL note is the standing condition — one stable name, one current copy. Each
    # change is a separate MESSAGE, so the mail carries a unique name and an edit-id; a
    # fixed mail name with no edit-id collided with the previous refresh at the receiver.
    message_id, mail_name, content = mailnames.identity(path.name, content)
    mailqueue.enqueue(roots, "federation-arch", mail_name, content, message_id=message_id,
                      provenance={"producer": "adopt.runner", "condition": path.stem,
                                  "cleared": cleared})


def sync_dead_letter_note(state, path=DEAD_LETTER_NOTE):
    """Author or clear the STANDING dead-letter comms note (ADR-0046, WI-0084).

    Same discipline as `sync_stale_note`, for the same reason: this is a CONDITION, not
    an event. One stable-named note that lists every quarantined brief and is refreshed
    while any remain; removed the moment the last one clears. One file per failure would
    accrete nightly and become the thing nobody reads — which is indistinguishable from
    the silence this whole item exists to end.

    Best-effort filesystem op only (no git): `comms/` is tracked, so the federation's
    next interactive session commits it and rides the auto-push.
    """
    p = pathlib.Path(path)
    try:
        entries = dead_letter_entries(state)
        if not entries:
            if p.exists():
                _queue_condition(p, "", cleared=True)
                p.unlink()
                return "cleared", p
            return "noop", None
        p.parent.mkdir(parents=True, exist_ok=True)
        newest = entries[-1][1].get("dead_since") or ""
        # WI-0304 B1: two reasons reach this list and they ask the reader for different
        # things, so the row says which. "3 consecutive failures" on a brief quarantined
        # for a broken check would be a fabricated count and, worse, would point the
        # reader at the adoption when the fix is in the brief.
        rows = "\n".join(
            (f"- **{k}** — DEFECTIVE BRIEF (its `verify:` reported no verdict), "
             f"quarantined on first sight {v.get('dead_since', '?')}. "
             if v.get("defective") else
             f"- **{k}** — {v.get('failures', '?')} consecutive failures, quarantined "
             f"{v.get('dead_since', '?')} (first failed {v.get('first_failed', '?')}). ")
            + "Last verdict: `%s`" % evidence.clip(
                (v.get("last_detail") or "(none recorded)").strip(), 200,
                keep="head", one_line=True)
            for k, v in entries)
        n_defective = sum(1 for _, v in entries if v.get("defective"))
        body = (
            f"---\n"
            f"date: {newest[:10]}\n"
            f"system: federation\n"
            f"type: blocked\n"
            f"summary: {len(entries)} brief(s) quarantined out of the nightly sweep "
            f"({n_defective} defective, {len(entries) - n_defective} failed "
            f"{DEFAULT_DEAD_LETTER_AFTER} times running) — each needs a decision, not "
            f"another retry.\n"
            f"---\n\n"
            f"The background adoption runner (R3, [ADR-0050](../adr/0050-headless-adoption-runner.md)) "
            f"stops retrying a brief after **{DEFAULT_DEAD_LETTER_AFTER} consecutive "
            f"failures**. Before this existed the runner retried forever and told nobody: "
            f"one brief failed every night for fourteen nights and stopped only because an "
            f"unrelated change made it ineligible.\n\n"
            f"A brief whose `verify:` command **crashed instead of reporting a verdict** "
            f"is quarantined on the FIRST sweep rather than the third: it is broken, not "
            f"unlucky, and the second and third attempts would tell nobody anything "
            f"(WI-0304).\n\n"
            f"Quarantined now:\n\n{rows}\n\n"
            f"**Nothing was left half-applied.** Every failed adoption was reset to its "
            f"pre-adoption HEAD and the brief is still pending in its own mailbox — "
            f"quarantine means the runner stops *attempting* it, not that anything was "
            f"discarded.\n\n"
            f"Each one needs a decision rather than another sweep: fix the brief, fix the "
            f"thing it verifies against, or withdraw it. **Editing the brief releases it "
            f"automatically** — the ledger is keyed on the brief's content, so a changed "
            f"brief is a new brief to the counter and the next sweep will try it. "
            f"**This note clears itself when the last quarantined brief clears.**\n\n"
            f"Ledger: `.session-state/adopt-runner.briefs.json` · audit trail: "
            f"`.session-state/adopt-runner.jsonl`.\n"
        )
        _queue_condition(p, body)
        atomic_write(p, body)
        return "written", p
    except Exception as e:
        print(f"  (could not sync dead-letter note: {e})", file=sys.stderr)
        return "noop", None


def read_status(path=STATUS_PATH):
    """The prior persisted sweep status, or {} if the runner has never reported here.
    Best-effort — a missing/corrupt file is simply a fresh start, never an error."""
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_status(status, path=STATUS_PATH):
    """Overwrite the runner's single last-sweep status file (the dashboard source).
    One file, always current — never accretes. Best-effort; never raises."""
    try:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(status, indent=2) + "\n")
        return p
    except Exception as e:
        print(f"  (could not write runner status: {e})", file=sys.stderr)
        return None


def sync_stale_note(status, escalate_after=DEFAULT_ESCALATE_AFTER_STALE, path=STALE_NOTE):
    """Author or clear the STANDING stale-login comms note (ADR-0046) to match the
    current runner status. This REPLACES the former per-sweep unauthenticated note:

      * a single stable-named note (not one dated file per stale night) — a persistent
        CONDITION, not an event, so it never accretes;
      * written only once the stale streak reaches `escalate_after` (refreshed, with the
        current night count, on each subsequent stale sweep);
      * removed as soon as auth recovers — the mailbox self-heals.

    Best-effort filesystem op only (no git): comms/ is tracked, so the federation's next
    interactive session commits the write/removal and rides ADR-0035 auto-push — matching
    the note's former commit contract, without the runner touching its own repo's git.
    Returns ('written' | 'cleared' | 'noop', path | None).
    """
    p = pathlib.Path(path)
    try:
        if should_escalate(status, escalate_after):
            p.parent.mkdir(parents=True, exist_ok=True)
            nights = int(status.get("consecutive_stale", 0) or 0)
            since = status.get("stale_since") or status.get("last_run") or "?"
            last_ok = status.get("last_success") or "never"
            last_clean = status.get("last_clean_sweep") or "never"
            date = (status.get("last_run") or "")[:10]
            body = (
                f"---\n"
                f"date: {date}\n"
                f"system: federation\n"
                f"type: blocked\n"
                f"summary: The headless adoption runner (R3, ADR-0050) has been unable to "
                f"authenticate for {nights} consecutive nightly sweeps — refresh the Runner "
                f"login to re-arm. No brief was touched.\n"
                f"---\n\n"
                f"The background adoption runner has now run **{nights} consecutive** nightly "
                f"sweeps without a valid Claude login (stale since {since}), so it has adopted "
                f"nothing. This is fail-safe — no brief was touched or reset on any of those "
                f"sweeps.\n\n"
                f"On the subscription-login path the runner reads the login's token, which "
                f"expires and only refreshes in an interactive Claude session. Open Claude "
                f"interactively on the Runner (or run `claude` once to refresh `/login`), and "
                f"the next scheduled sweep re-arms automatically — **this note clears itself "
                f"when auth recovers.** Alternatively set `ANTHROPIC_API_KEY` in the runner's "
                f"environment (metered billing) for a path that doesn't depend on token "
                f"freshness.\n\n"
                f"Last authenticated sweep: {last_ok} (this is proof of life and auth, "
                f"NOT proof that adoption is healthy). Last CLEAN sweep — authenticated "
                f"with nothing failed: {last_clean}. "
                f"Full state: `.session-state/adopt-runner.status` · audit trail: "
                f"`.session-state/adopt-runner.jsonl`.\n"
            )
            _queue_condition(p, body)
            atomic_write(p, body)
            return "written", p
        if p.exists():
            _queue_condition(p, "", cleared=True)
            p.unlink()
            return "cleared", p
        return "noop", None
    except Exception as e:
        print(f"  (could not sync stale-login note: {e})", file=sys.stderr)
        return "noop", None


def log_outcome(record):
    """Append one JSONL audit line to the federation's runner log. Best-effort."""
    try:
        RUNNER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUNNER_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Sweep orchestration.
# --------------------------------------------------------------------------- #

def persist_status(prior, *, result, stamp, args, detail="", adopted=0, failed=0,
                   repos_scanned=0, eligible=0):
    """Fold + write the runner's status file and reconcile the standing stale-login
    note — the single routine reporting channel (ADR-0050 R3'). Every real sweep
    overwrites one status line (the dashboard source metrics.py mines); a comms note
    fires only past the escalation threshold and self-clears on recovery. Returns the
    new status dict."""
    status = fold_status(prior, result=result, stamp=stamp, detail=detail,
                         adopted=adopted, failed=failed,
                         repos_scanned=repos_scanned, eligible=eligible)
    write_status(status)
    action, note = sync_stale_note(status, args.escalate_after)
    if action == "written":
        print(f"  stale-login note refreshed — {status['consecutive_stale']} consecutive "
              f"unauthenticated sweep(s): {note}")
    elif action == "cleared":
        print(f"  auth recovered — cleared standing stale-login note")
    return status


def record_manual_adoption(outcome, header, text, started, retries):
    """One line in the manual-adoption ledger (`curate/adoption_metrics.py`) for this
    adoption attempt: reason category, elapsed wall time, retries, operation shape.
    Best-effort — bookkeeping never changes an adoption's outcome."""
    try:
        adoption_metrics.record(adoption_metrics.entry(
            source="adopt-runner", stamp=outcome.get("stamp"), brief=outcome.get("brief"),
            header=header, text=text, result=outcome.get("result"),
            elapsed_s=time.monotonic() - started, retries=retries,
            repo=outcome.get("repo")))
    except Exception:
        pass


def adopt_one(repo, config, brief_path, args, stamp, prior_failures=None):
    """Adopt a single eligible brief in an idle repo. Returns an outcome dict.
    Commit-only; verify-or-reset. Never raises (caught by the caller too).

    `prior_failures` is this brief's consecutive-failure count from the dead-letter
    ledger — the `retries` the manual-adoption ledger records. None reads it from that
    ledger, which within a sweep still holds the sweep-start value for this brief (each
    brief is attempted at most once per sweep and the ledger is written at the end)."""
    started = time.monotonic()
    text = brief_path.read_text(encoding="utf-8")
    header, _ = parse_frontmatter(text)
    brief_id = header.get("edit-id", brief_path.stem)
    verify_cmd = header.get("verify", "").strip()
    if prior_failures is None:
        # Bookkeeping for the manual-adoption ledger only: a malformed dead-letter
        # record must not turn into an adoption error, so it reads as zero retries.
        try:
            rec = read_brief_state().get(brief_key(repo, brief_id)) or {}
            prior_failures = (int(rec.get("failures", 0) or 0)
                              if rec.get("fingerprint") == brief_fingerprint(text) else 0)
        except (AttributeError, TypeError, ValueError):
            prior_failures = 0
    pre = git_head(repo)
    outcome = {"repo": str(repo), "brief": brief_id, "stamp": stamp, "pre": pre}

    if pre is None:
        outcome.update(result="error", detail="could not read pre-adoption HEAD")
        record_manual_adoption(outcome, header, text, started, prior_failures)
        return outcome

    ok, detail = run_claude_adopt(repo, config, brief_path, args.claude_bin, args.model,
                                  args.timeout, args.max_turns, args.verbose)
    if ok:
        post = git_head(repo)
        if post == pre:
            ok, detail = False, "adoption produced no commit (HEAD unchanged)"
    # WI-0304 B1: `defective` is reachable ONLY from the verify step. An adoption-side
    # failure (a `claude -p` error, no commit) is a failure of the WORK, whatever the
    # brief's verify command looks like — the two must not be folded together just
    # because they both end the attempt.
    defective = False
    if ok:
        vkind, detail, vraw = run_verify(repo, verify_cmd)
        outcome.update(verify_command=verify_cmd, verify_output=vraw)
        ok = vkind == "pass"
        defective = vkind == "defective"

    if ok:
        # The receipt, written only now — after the verify passed. Filing on anything
        # weaker would re-create WI-0041's failure with the runner as its author.
        dest, receipt = file_and_stamp_brief(
            repo, config, brief_path, stamp, member_role_doc_version(repo, config))
        outcome.update(result="adopted", detail=detail, post=git_head(repo),
                       filed=str(dest) if dest else None, receipt=receipt)
        if dest is None:
            # A failed FILING must never fail the ADOPTION. The work is applied,
            # committed and verified by this point; resetting real work because
            # bookkeeping failed is the worse error, and `deliver.py _retire_source`
            # states the same rule for the delivery side. But unlike that function this
            # does NOT swallow it: `_retire_source` loses nothing when it gives up, while
            # a lost receipt is the very evidence the audit reads. So the outcome carries
            # the reason and the sweep says so out loud.
            print(f"    RECEIPT MISSING {repo.name}/{brief_path.name}: {receipt}")
        log_outcome(outcome)
        record_manual_adoption(outcome, header, text, started, prior_failures)
        return outcome

    # Failure — reset to the pre-adoption HEAD (restores the brief's pending
    # location and role doc, since the whole adoption rode commits after `pre`),
    # then surface via a comms blocked note.
    # A DEFECTIVE verify resets too, and that is not an oversight. The brief's check
    # broke, so the adoption is UNVERIFIED — not known-good — and keeping an unverified
    # unattended edit is the blind trust P18 and ADR-0050 §5 exist to refuse. Only the
    # fact being RECORDED changes here, never the safety behaviour.
    reset_ok, reset_out = run_git(repo, ["reset", "--hard", pre])
    note = write_comms_blocked(repo, config, brief_id, detail, stamp, defective=defective)
    outcome.update(result="defective" if defective else "failed", detail=detail,
                   reset=reset_ok,
                   reset_detail=None if reset_ok else reset_out,
                   comms_note=str(note) if note else None)
    log_outcome(outcome)
    record_manual_adoption(outcome, header, text, started, prior_failures)
    return outcome


def run_janitor_pass(targets, now, args):
    """Run each member's OWN `session.py janitor` in its OWN repo, nightly.

    Retention is not adoption, and it deliberately does not ride the adoption path: litter
    accumulates precisely in the repos nobody is working in, which are the repos with no
    brief to adopt and therefore the ones the sweep below skips first. Running it as its
    own pass is what makes coverage independent of whether there was anything to adopt.

    The federation does NOT reach in and tidy a member's tree itself. It invokes the
    member's own harness — byte-identical substrate, running in that repo, as that member —
    so the single-writer story is unchanged ([P13](principles/master.md#p13--single-writer-per-state))
    and a member whose harness predates the verb simply reports nothing rather than being
    operated on by a newer federation.

    Never races a live session: a fresh heartbeat means hands off, same rule the adoption
    sweep follows. Best-effort per repo — one member's broken checkout must not stop the
    others being swept."""
    swept, skipped, failed = 0, 0, 0
    for repo in targets:
        script = repo / "session.py"
        if not script.is_file():
            continue
        if repo_is_live(read_live_jsons(repo), now):
            skipped += 1
            if args.verbose:
                print(f"  JANITOR SKIP {repo.name}: live session — never race a session")
            continue
        cmd = [sys.executable, str(script), "janitor"]
        if args.dry_run:
            cmd.append("--dry-run")
        try:
            out = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                                 timeout=120)
        except Exception as exc:
            failed += 1
            print(f"  JANITOR FAIL {repo.name}: {exc}")
            continue
        if out.returncode != 0:
            # An older harness has no `janitor` subcommand. That is a ROLLOUT gap, not a
            # member fault, and it must read as one — argparse's own message dumps all ~45
            # subcommands, which buries the one fact that matters under a wall of noise.
            failed += 1
            err = (out.stderr or "").strip()
            if "invalid choice: 'janitor'" in err:
                hint = "harness predates the janitor verb — needs a substrate push"
            else:
                # The LAST line, deliberately — argparse's wall of subcommands is the
                # noise the comment above is about, and the message that matters is
                # underneath it. The line budget still reports how much was skipped,
                # so "one line" stays a choice a reader can see rather than a silent
                # one; the char budget then caps a single runaway line.
                hint = evidence.clip(
                    evidence.clip(err, 1, keep="tail", unit="lines", one_line=True),
                    160, keep="head", one_line=True) if err else f"exit {out.returncode}"
            print(f"  JANITOR N/A {repo.name}: {hint}")
            continue
        swept += 1
        for line in (out.stdout or "").strip().splitlines():
            if "closed" in line or args.verbose:
                print(f"  JANITOR {repo.name}: {line.replace('janitor: ', '')}")
    print(f"adopt-runner: janitor pass — {swept} repo(s) swept, {skipped} live-skipped, "
          f"{failed} unavailable")
    return {"swept": swept, "skipped": skipped, "failed": failed}


def sweep(args):
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    targets = [pathlib.Path(args.repo).resolve()] if args.repo else discover_repos()
    print(f"adopt-runner: {'DRY-RUN — ' if args.dry_run else ''}"
          f"{len(targets)} candidate repo(s) [{stamp}]")

    # Retention runs FIRST and unconditionally — above the auth preflight, deliberately.
    # It needs no token and spawns no agent, so a stale OAuth token (the failure that
    # aborts the whole adoption sweep) must not also stop the fleet being tidied. The
    # original placement below the preflight said "needs no auth" in its own comment while
    # sitting behind an early return that made that false.
    run_janitor_pass(targets, now, args)

    # Auth preflight (skipped on dry-run — dry-run spawns no session). One clear
    # blocked note beats N doomed adoptions when the token is stale (ADR-0050 auth).
    if not args.dry_run:
        auth_ok, auth_detail = preflight_auth(args.claude_bin)
        if not auth_ok:
            status = persist_status(read_status(), result="unauthenticated", stamp=stamp,
                                    args=args, detail=auth_detail, repos_scanned=len(targets))
            escalated = should_escalate(status, args.escalate_after)
            print(f"adopt-runner: ABORT — headless Claude not authenticated ({auth_detail}). "
                  f"No brief touched. Stale streak {status['consecutive_stale']}; "
                  f"comms {'raised' if escalated else 'held (below threshold)'}.")
            log_outcome({"stamp": stamp, "result": "unauthenticated", "detail": auth_detail})
            return [{"result": "unauthenticated", "detail": auth_detail}]

    outcomes = []
    repos_worked = 0
    total_eligible = 0
    # WI-0084: the attempt ledger, read once and carried through the sweep so a repeated
    # failure of the same brief content can actually be counted across nights.
    brief_state = read_brief_state()
    quarantined = 0
    for repo in targets:
        if repos_worked >= args.max_repos:
            print(f"  (repo cap {args.max_repos} reached — stopping sweep)")
            break
        config = load_config(repo)
        if not is_claude_member(config):
            print(f"  SKIP {repo.name}: non-Claude member — its runtime owns its residue")
            continue
        pending = inbox_pending_dir(repo, config)
        briefs = pending_briefs(pending)
        eligible = []
        for b in briefs:
            btext = b.read_text(encoding="utf-8")
            header, has_fm = parse_frontmatter(btext)
            ok, route, reason = classify_brief(header, has_fm)
            if not ok:
                if args.verbose:
                    print(f"    · {repo.name}/{b.name}: skip ({route}) — {reason}")
                continue
            # WI-0084: a quarantined brief is skipped BEFORE it can be attempted. This
            # is the line that ends the silent infinite retry — everything else in this
            # item is counting and reporting, and this is the part that stops the loop.
            bid = header.get("edit-id", b.stem)
            if is_dead_lettered(brief_state, brief_key(repo, bid),
                                brief_fingerprint(btext)):
                quarantined += 1
                print(f"    · {repo.name}/{b.name}: QUARANTINED after "
                      f"{args.dead_letter_after} failures — not retried. "
                      f"Edit the brief to release it.")
                continue
            eligible.append(b)
        total_eligible += len(eligible)
        if not eligible:
            if args.verbose:
                print(f"  SKIP {repo.name}: no runner-eligible brief")
            continue
        if repo_is_live(read_live_jsons(repo), now):
            print(f"  SKIP {repo.name}: live session (heartbeat fresh) — never race a session")
            continue

        repos_worked += 1
        for b in eligible[:args.max_briefs_per_repo]:
            if args.dry_run:
                header, _ = parse_frontmatter(b.read_text(encoding="utf-8"))
                print(f"  PLAN {repo.name}/{b.name}: would adopt "
                      f"(verify: {header.get('verify', '').strip()!r})")
                outcomes.append({"repo": str(repo), "brief": b.stem, "result": "plan"})
                continue
            print(f"  ADOPT {repo.name}/{b.name} …")
            try:
                oc = adopt_one(repo, config, b, args, stamp)
            except AuthError as e:  # token went stale mid-sweep — abort, don't blame briefs
                status = persist_status(read_status(), result="unauthenticated", stamp=stamp,
                                        args=args, detail=str(e), repos_scanned=len(targets),
                                        eligible=total_eligible)
                print(f"  ABORT: headless Claude lost auth mid-sweep ({e}). No brief touched. "
                      f"Stale streak {status['consecutive_stale']}.")
                log_outcome({"stamp": stamp, "result": "unauthenticated", "detail": str(e)})
                outcomes.append({"result": "unauthenticated", "detail": str(e)})
                return outcomes
            except Exception as e:  # fail-safe — one bad repo never bricks the sweep
                oc = {"repo": str(repo), "brief": b.stem, "result": "error", "detail": str(e)}
                log_outcome(oc)
            outcomes.append(oc)
            # WI-0084: fold this outcome into the attempt ledger. `adopted` clears the
            # record, `failed` increments and may quarantine, `error` is deliberately
            # inert — the runner breaking is not evidence against the brief.
            bid = oc.get("brief") or b.stem
            key = brief_key(repo, bid)
            was_dead = bool((brief_state.get(key) or {}).get("dead_since"))
            brief_state = fold_brief_state(
                brief_state, key,
                fingerprint=brief_fingerprint(b.read_text(encoding="utf-8"))
                if b.is_file() else "",
                result=oc.get("result", "error"), stamp=stamp,
                detail=oc.get("detail", ""),
                dead_letter_after=args.dead_letter_after)
            print(f"    → {oc['result']}: {oc.get('detail', '')}")
            if not was_dead and (brief_state.get(key) or {}).get("dead_since"):
                quarantined += 1
                why = ("its verify reported no verdict — a defective brief is quarantined "
                       "on first sight, since retrying a broken check learns nothing"
                       if oc.get("result") == "defective" else
                       f"{args.dead_letter_after} consecutive failures")
                print(f"    → QUARANTINED: {why} — the runner will stop retrying this "
                      f"brief. Nothing was left applied; it is still pending in its "
                      f"mailbox.")

    adopted = sum(1 for o in outcomes if o.get("result") == "adopted")
    # `failed` stays the count of briefs that DID NOT ADOPT — true of a defective one too,
    # and the dashboard's question. The distinct fact lives where a reader can act on it:
    # the per-brief outcome, the ledger, the comms note, and the detail line below.
    defective = sum(1 for o in outcomes if o.get("result") == "defective")
    failed = sum(1 for o in outcomes if o.get("result") in ("failed", "error", "defective"))
    planned = sum(1 for o in outcomes if o.get("result") == "plan")
    if args.dry_run:
        # Dry-run is a plan: it spawns nothing and, by contract, writes nothing —
        # so it must NOT touch the status file or the standing note.
        print(f"adopt-runner: dry-run complete — {planned} brief(s) would be adopted.")
        return outcomes

    # A real sweep RAN TO COMPLETION — it authenticated (preflight passed) and swept,
    # so this is proof-of-life regardless of whether anything was eligible. Record it:
    # resets any stale streak and clears the standing note (sync_stale_note).
    result = "adopted" if adopted else ("failed" if failed else "no-eligible")
    detail = (f"{adopted} adopted, {failed} failed/errored"
              + (f" ({defective} defective brief(s))" if defective else "")
              + f", {total_eligible} eligible across {len(targets)} repo(s) scanned")
    persist_status(read_status(), result=result, stamp=stamp, args=args, detail=detail,
                   adopted=adopted, failed=failed, repos_scanned=len(targets),
                   eligible=total_eligible)
    # WI-0084: persist the ledger and bring the standing dead-letter note into line with
    # it. Both AFTER the status write, so a failure here can never cost the status record.
    write_brief_state(brief_state)
    action, _p = sync_dead_letter_note(brief_state)
    dead_now = len(dead_letter_entries(brief_state))
    tail = (f" {dead_now} brief(s) quarantined — see comms/{DEAD_LETTER_NOTE.name}."
            if dead_now else
            (" Dead-letter note cleared — no brief is quarantined."
             if action == "cleared" else ""))
    print(f"adopt-runner: sweep complete — {adopted} adopted, {failed} failed/errored"
          + (f" ({defective} defective brief(s) — the check reported no verdict)"
             if defective else "")
          + f".{tail}")
    return outcomes


def main():
    ap = argparse.ArgumentParser(description="Headless background adoption runner (ADR-0050).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Discover, classify, liveness-check — spawn no session, write nothing.")
    ap.add_argument("--repo", default=None,
                    help="Limit the sweep to one target repo (absolute path) — the pilot.")
    ap.add_argument("--model", default=None,
                    help="Model alias for the headless session (default: inherit).")
    ap.add_argument("--claude-bin", default="claude",
                    help="Path to the claude binary (default: 'claude' on PATH). The "
                         "launchd job passes an absolute path since its PATH is minimal.")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help=f"Per-session timeout in seconds (default {DEFAULT_TIMEOUT_S}).")
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                    help=f"Per-session max agent turns (default {DEFAULT_MAX_TURNS}).")
    ap.add_argument("--max-repos", type=int, default=DEFAULT_MAX_REPOS,
                    help=f"Cap repos worked per sweep (default {DEFAULT_MAX_REPOS}).")
    ap.add_argument("--max-briefs-per-repo", type=int, default=DEFAULT_MAX_BRIEFS_PER_REPO,
                    help=f"Cap briefs adopted per repo per sweep (default {DEFAULT_MAX_BRIEFS_PER_REPO}).")
    ap.add_argument("--escalate-after", type=int, default=DEFAULT_ESCALATE_AFTER_STALE,
                    help=f"Consecutive unauthenticated sweeps before a standing stale-login "
                         f"comms note is raised (default {DEFAULT_ESCALATE_AFTER_STALE}). Below "
                         f"this the state lives only on the status line / dashboard.")
    ap.add_argument("--dead-letter-after", type=int, default=DEFAULT_DEAD_LETTER_AFTER,
                    help=f"Consecutive failures of the SAME brief content before it is "
                         f"quarantined out of the nightly sweep (default "
                         f"{DEFAULT_DEAD_LETTER_AFTER}). Editing the brief releases it: "
                         f"the ledger is keyed on content, so a changed brief starts over.")
    ap.add_argument("--verbose", action="store_true", help="Print skip reasons + session tails.")
    args = ap.parse_args()
    try:
        sweep(args)
    except Exception as e:  # fail-open — the sweep is best-effort infrastructure
        print(f"adopt-runner: sweep errored — {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
