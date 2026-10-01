#!/usr/bin/env python3
"""The finish-line scoreboard — is the harness mature yet, by measurement.

Six capabilities (`tests/test_finish_line.py`, one class each) say what the harness must
DO. This file says whether the fleet, as it is running right now, is inside the line:
the live numbers a unit test cannot see, and the three exit criteria that separate
"mature" from "still sprawling". It REPORTS. It never blocks a land, a push, or a start —
a finish line that failed the suite would stop the very work that reaches it.

  python3 curate/finish_line.py            # the board
  python3 curate/finish_line.py --status   # one line, for a SessionStart hook
  python3 curate/finish_line.py --no-tests # the board without running the suite

Exit criteria (all three at once, then the harness is mature):
  1. work items created <= closed, three consecutive weeks
  2. harness line count flat (+-2%) over 30 days -- session.py PLUS sessionlib/, which
     is the body ADR-0118 moved it into; the entry point alone cannot grow
  3. product members filed zero substrate-internal items in the window

Two OUTCOME measures (consultant brief 2026-09-07 R3) sit among the capabilities, and
they are REPORT rows: a measured number with no bar, deliberately. Their definitions:

  time to verified done   From the earliest durable evidence the item was filed or
                          worked, to the commit that set `status: done` -- counted ONLY
                          when the record carries an acceptance receipt (ACCEPTANCE MET
                          / RECORDED / VERIFIED). A `done` without one is UNVERIFIED,
                          shown as such, and does not end the interval. Waits on the
                          user are inside the elapsed time, never netted out. The ages
                          of items still open print beside it, so abandoning hard work
                          cannot flatter the completed-item number.
  avoidable interventions Ledger entries, per item the system actually finished, where
                          the user had to correct or unblock execution that an already
                          established requirement, authority, state or procedure should
                          have covered. A `[required]` tag on the entry moves it to the
                          separately-reported REQUIRED count, which has NO target; an
                          untagged entry counts as avoidable, so the number cannot be
                          improved by declining to classify. Both are diagnostic, and
                          neither is a quota or a gate.

Everything is fail-open: a probe that cannot answer prints UNKNOWN and why, never a
guess, never a crash (`declare-what-a-check-assumes`).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
ROOT = SCRIPT_ROOT                      # the checkout being measured; `--root` repoints it


def _set_root(path: str | None) -> None:
    """Point every probe at a checkout. The suite still runs from where this file lives;
    the live probes (session.py, journals, store, fleet config) read from ROOT."""
    global ROOT
    ROOT = Path(path).resolve() if path else SCRIPT_ROOT
    for p in (str(ROOT / "curate"), str(ROOT)):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


_set_root(None)

# ---- targets -------------------------------------------------------------------------
# Expressed in BYTES because bytes are what probe_start_budget can count. 160KB is the
# original 40k-token intent at a nominal 4 B/token, so the bar is unchanged — but the
# comparison is now made in the unit that is measured.
START_BUDGET_BYTES = 160_000
ROLE_DOC_BYTES = 10_000
# Single source of truth: token_ledger owns this threshold and prints it in its own
# status line. Two copies would let the board and the ledger disagree about the same bar.
def _ctx_target() -> int:
    import token_ledger
    return token_ledger.CTX_TARGET
OPEN_ITEMS_TARGET = 30
WEEKS_FOR_TREND = 3
FLAT_TOLERANCE = 0.02
WINDOW_DAYS = 14
# The window the two OUTCOME measures (R3) are read over, numerator and denominator alike.
# 30 days rather than the board's 14 because both are ratios over work items the system
# actually finished, and at this store's cadence a fortnight is a handful of closures —
# a denominator small enough that one item moves the rate by more than the behaviour does.
# It is `curate/metrics.py`'s own `ESCALATION_WINDOW_DAYS` by value, and passed explicitly
# to it rather than left to default, so the two halves cannot drift apart silently.
OUTCOME_WINDOW_DAYS = 30

_SUBSTRATE_WORDS = re.compile(
    r"\b(session\.py|poga|lane|lanes|land gate|land-gate|landing|dispatch|worktree|"
    r"reap|cas|trunk|rebase|heartbeat|journal|handoff|roadmap compile|substrate|"
    r"standard\.md|canon\.md|apply-briefs|mail-poller|coord)\b", re.I)


# ---- pure verdicts (unit-tested) -------------------------------------------------------

def verdict_ctx(median_ctx: int) -> str:
    return "PASS" if median_ctx <= _ctx_target() else "FAIL"


def verdict_ratio(weeks: list[tuple[int, int]]) -> str:
    """weeks = [(created, done), ...] oldest..newest. PASS when the last three weeks each
    closed at least as many as they opened."""
    if len(weeks) < WEEKS_FOR_TREND:
        return "UNKNOWN"
    tail = weeks[-WEEKS_FOR_TREND:]
    return "PASS" if all(c <= d for c, d in tail) else "FAIL"


def verdict_flat(then_lines: int, now_lines: int) -> str:
    if not then_lines:
        return "UNKNOWN"
    return "PASS" if abs(now_lines - then_lines) / then_lines <= FLAT_TOLERANCE else "FAIL"


def is_substrate_internal(title: str) -> bool:
    return bool(_SUBSTRATE_WORDS.search(title.replace("-", " ")))


# ---- probes (each returns (verdict, evidence)) ------------------------------------------

GIT_TIMEOUT_S = 30
TESTS_TIMEOUT_S = 900


class ProbeUnknown(Exception):
    """A probe could not measure its subject. Never conflated with a measured verdict."""


def _git(repo: Path, *args) -> str:
    """Read-only git, bounded. A wedged repo (network mount, stuck index lock) must
    surface as UNKNOWN, never as a hung SessionStart hook
    ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes))."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ProbeUnknown(f"git {args[0]} exceeded {GIT_TIMEOUT_S}s in {repo}")
    return r.stdout if r.returncode == 0 else ""


def probe_tests() -> tuple[str, str]:
    """NOT READ-ONLY. Runs `tests.test_finish_line`, which drives the real
    `bootstrap.py --adopt` and therefore writes (and cleans up) a throwaway profile under
    `users/` and a roster brief under `proposed-edits/`. From a lane both are symlinks to
    the shared main checkout. Opt in with `--tests`; the board and the `--status` hook
    leave it alone."""
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_finish_line"],
                           cwd=str(SCRIPT_ROOT), capture_output=True, text=True,
                           timeout=TESTS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "UNKNOWN", f"capability suite exceeded {TESTS_TIMEOUT_S}s"
    tail = r.stderr.strip().splitlines()
    ran = next((l for l in tail if l.startswith("Ran ")), "no verdict")
    ok = any(l.startswith("OK") for l in tail)
    fails = [l for l in tail if l.startswith(("FAIL:", "ERROR:"))]
    return ("PASS" if ok else "FAIL"), ran + ("" if ok else "; " + "; ".join(fails[:4]))


def probe_start_budget() -> tuple[str, str]:
    try:
        import session
        canon = session._canon_block()
        standard = session._standard_block()
        handoff = Path(session.CFG["handoff"]) if isinstance(session.CFG, dict) else None
        prior = ""
        if handoff and handoff.is_file():
            entries = re.split(r"(?m)^## ", handoff.read_text(encoding="utf-8"))
            prior = ("## " + entries[1]) if len(entries) > 1 else ""
        injected = len(canon) + len(standard) + len(prior)
        role = Path(session.CFG["role_doc"])
        role_bytes = role.stat().st_size if role.is_file() else 0
        total_bytes = injected + role_bytes
        # BYTES are what this probe measures, so bytes are what the verdict rides on.
        # Nothing here tokenizes; the previous form divided by 4 and compared the result
        # to a TOKEN budget, printing an estimate in the shape of a measurement
        # ([`no-fabricated-data`](habits/master.md#no-fabricated-data)). The token figure
        # is kept as orientation and marked "est.".
        v = "PASS" if total_bytes <= START_BUDGET_BYTES and role_bytes <= ROLE_DOC_BYTES else "FAIL"
        return v, (f"{total_bytes // 1000}KB to orient — injected {injected // 1000}KB + "
                   f"role doc {role_bytes // 1000}KB "
                   f"{'≤' if role_bytes <= ROLE_DOC_BYTES else '>'} {ROLE_DOC_BYTES // 1000}KB; "
                   f"target ≤{START_BUDGET_BYTES // 1000}KB (est. ~{total_bytes // 4 // 1000}k "
                   f"tokens at 4 B/token — not measured)")
    except Exception as e:
        return "UNKNOWN", f"could not assemble the start payload ({e})"


def _self_checkouts() -> set[Path]:
    """Every path that IS this Architect: the checkout being measured, and — when that is
    a linked worktree — the trunk checkout it belongs to. `git rev-parse --git-common-dir`
    resolves to the shared `.git` for both a lane and the main tree, so its parent is the
    trunk in either case."""
    paths = {ROOT.resolve()}
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (ROOT / common)
            paths.add(common.resolve().parent)
    except (subprocess.TimeoutExpired, OSError):
        pass                      # ROOT alone; the probe degrades, it does not guess
    return paths


def _fleet():
    """(members dict sid->Path, note). Uses the parity tool's own locators."""
    spec = importlib.util.spec_from_file_location("standard_version", ROOT / "curate" / "standard_version.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    # The parity tool's OWN readers for its two machine-local files — never a second parser.
    read_roots = getattr(sv, "_read_roots_config", None)
    read_paths = getattr(sv, "_read_repo_paths_config", None)
    if read_roots is None or read_paths is None:
        import common  # noqa: F401  (curate/common.py, on sys.path via _set_root)
        read_roots = getattr(common, "_read_roots_config", None) or getattr(common, "read_roots_config")
        read_paths = getattr(common, "_read_repo_paths_config", None) or getattr(common, "read_repo_paths_config")
    roots = read_roots(sv.ROOTS_CONFIG, warn=False)
    mapped = read_paths(sv.REPO_PATHS_CONFIG)
    members = sv.locate(roots, mapped)          # {sid: (repo, cfg)}, federation excluded
    # Exclude the federation by its TRUNK checkout, not just by ROOT. Every poga session
    # runs from a linked worktree under .claude/worktrees/, where ROOT is the lane and the
    # main checkout does not compare equal to it — so the federation located itself as a
    # twelfth "member", read as harness_stale against its own lane, and had its own work
    # items counted as substrate items filed by a product member. The board was wrong in
    # exactly the configuration it always runs in
    # ([`verify-in-the-created-configuration`](habits/master.md#verify-in-the-created-configuration)).
    return sv, {sid: (Path(repo), cfg) for sid, (repo, cfg) in members.items()
                if Path(repo).resolve() not in _self_checkouts()}


def probe_fleet_identity() -> tuple[str, str]:
    try:
        sv, members = _fleet()
        if not members:
            return "UNKNOWN", "no members located on this machine (reconcile-roots.local / repo-paths.local)"
        stale = []
        for sid, (repo, cfg) in members.items():
            r = sv.evaluate_member(repo, cfg)
            if r.get("harness_stale"):
                stale.append(sid)
        n = len(members)
        if stale:
            return "FAIL", f"{len(stale)}/{n} members carry a different session.py: {', '.join(stale)}"
        return "PASS", f"{n}/{n} members byte-identical to the federation's session.py"
    except Exception as e:
        return "UNKNOWN", f"parity tool could not answer ({e})"


#: An ITEM file under the store, as `git log --name-only` prints it. Anchored and
#: `fullmatch`-shaped on purpose: `work-items/notes/WI-0287/<stamp>.md` is a NOTE RECORD,
#: not an item, and it must not match. See `wi_weeks` for what that distinction cost.
_WI_ITEM_PATH = re.compile(r"^work-items/(WI-\d{4})-[^/]*\.md$")
#: The bucket a work item created or closed before the `scope` field existed falls into,
#: and the bucket an id that no longer resolves in the store falls into. It is NOT a
#: fourth value of the field and it is never folded into `product` — R4 asks by name that
#: unscoped items stay visible rather than being silently counted as something.
SCOPE_UNSET_BUCKET = "unscoped"
SCOPE_BUCKETS = ("harness", "product", "mixed", SCOPE_UNSET_BUCKET)


def wi_scope_map(repo: Path | None = None) -> dict[str, str]:
    """{item id: scope} read from the store as it stands TODAY.

    The field is authored and carries no history of its own, so an item created three
    weeks ago is attributed to whatever it says now — which is the right answer for a
    trend ("how much harness work did we open?") and the wrong one for an audit. An id
    the store cannot resolve maps to `SCOPE_UNSET_BUCKET` rather than being dropped, so
    the buckets always sum to the total.

    Parsed here rather than through `session._wi_parse` deliberately: that reader resolves
    its directory from `session.ROOT`, which would ignore this script's `--root` and
    silently measure the wrong checkout. `probe_open_items` reads the store the same way
    and for the same reason."""
    repo = repo if repo is not None else ROOT
    out: dict[str, str] = {}
    try:
        paths = sorted((repo / "work-items").glob("WI-*.md"))
    except Exception:
        return out
    for path in paths:
        m = _WI_ITEM_PATH.match(f"work-items/{path.name}")
        if not m:
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:600]
        except Exception:
            continue
        got = re.search(r"(?m)^- scope: ([a-z]+)\s*$", head)
        out[m.group(1)] = got.group(1) if got else ""
    return out


def _wi_window(repo: Path, a, b) -> tuple[list[str], list[str]]:
    """([ids created], [ids closed]) in [a, b), from the store's git history.

    BOTH SIDES ARE ATTRIBUTED TO AN ITEM ID, which is what lets the trend split by scope
    at all — and fixing `created` to do that corrected a real defect rather than merely
    enabling the split. The count this replaces was `--name-only` output `.count("WI-")`,
    and `work-items/notes/WI-0287/<stamp>.md` contains `WI-`: every NOTE RECORD ever added
    was being counted as a work item created. Measured on this store on 2026-09-18 over
    the four weeks the board reads: 73/149/310/251 by the old count against 55/73/70/54
    by this one, with 531 note records added in the window and every one of them counted
    as a creation. Exit criterion 1 was being read off a number roughly four times the
    thing it named. The verdict does not move today — it was FAIL under both, checked
    before the change went in — so this corrects the evidence without manufacturing a pass.

    `done` is UNCHANGED in total and only gains its attribution: the same
    `+- status: done` patch lines, now charged to the file whose hunk they appear in.
    Measured identical on all four weeks (32/43/89/84 either way).

    WHAT IS STILL APPROXIMATE, because a corrected number invites being quoted as a count:
    a rebase, revert or cherry-pick of a `status: done` line re-counts that closure, and an
    item renumbered off a lane is created under one id and closed under another. Good
    enough to read a trend, not good enough to quote as a count
    ([`no-fabricated-data`](habits/master.md#no-fabricated-data))."""
    names = _git(repo, "log", f"--since={a}", f"--until={b}", "--diff-filter=A",
                 "--name-only", "--format=", "--", "work-items")
    created = [m.group(1) for m in
               (_WI_ITEM_PATH.match(n.strip()) for n in names.splitlines()) if m]
    closed: list[str] = []
    current = ""
    for line in _git(repo, "log", f"--since={a}", f"--until={b}", "-p", "--format=",
                     "--", "work-items").splitlines():
        if line.startswith("+++ b/"):
            m = _WI_ITEM_PATH.match(line[len("+++ b/"):].strip())
            current = m.group(1) if m else ""
        elif line.startswith("+- status: done") and current:
            closed.append(current)
    return created, closed


#: A note record's filename stamp, which `_note_id` writes UTC and microsecond-precise
#: precisely so filename order is time order. It is the only per-item timestamp the
#: TRACKED store carries — the item file itself has no date field of any kind, by a
#: decision recorded on `_wi_note_watermark` ("a field someone must remember to update is
#: the very mechanism whose absence this item documents").
_NOTE_STAMP_RE = re.compile(r"^(\d{8}T\d{6}\d{6})Z-")


def _note_stamp(name: str):
    m = _NOTE_STAMP_RE.match(name)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S%f").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def _wi_first_touch(repo: Path) -> dict[str, _dt.datetime]:
    """{item id: earliest durable evidence that this item existed and was being worked}.

    THE START OF THE INTERVAL, AND IT IS A SUBSTITUTE — say so wherever it is used. The
    brief specifies "the item's FIRST claim stamp, kept across retries, re-claims and
    handoffs". No such stamp survives: a claim is a coordination record under the git
    common dir with an 8-hour TTL, `_coord_try_acquire` rewrites `created_at` wholesale on
    every re-claim, and the release tombstone that would preserve the original expires on
    the same clock. For an item closed three weeks ago there is nothing left to read.

    What IS durable and git-tracked: the item's own first-add commit, and its note records,
    whose filenames are UTC stamps to the microsecond. The earlier of the two is used —
    normally the add — and which one each item used is reported as coverage rather than
    averaged away, because "filed" and "worked" are different events and an interval built
    on a mixture of them should say so."""
    out: dict[str, _dt.datetime] = {}
    stamp = ""
    for line in _git(repo, "log", "--reverse", "--diff-filter=A", "--format=%x00%aI",
                     "--name-only", "--", "work-items").splitlines():
        line = line.strip()
        if line.startswith("\x00"):
            stamp = line[1:]
            continue
        m = _WI_ITEM_PATH.match(line)
        if not m or not stamp:
            continue
        try:
            when = _dt.datetime.fromisoformat(stamp)
        except ValueError:
            continue
        # `--reverse` walks oldest first, so the first sighting is the add. A second one
        # is a renumber re-adding the file under a new name; keep the earlier.
        if m.group(1) not in out or when < out[m.group(1)]:
            out[m.group(1)] = when
    try:
        for d in sorted((repo / "work-items" / "notes").glob("WI-*")):
            stamps = [s for s in (_note_stamp(p.name) for p in d.glob("*.md")) if s]
            if stamps and (d.name not in out or min(stamps) < out[d.name]):
                out[d.name] = min(stamps)
    except Exception:
        pass                     # no notes tree; the add dates stand on their own
    return out


def _wi_closures(repo: Path, a, b) -> list[tuple[str, _dt.datetime]]:
    """[(item id, when it was closed)] in [a, b) — `_wi_window`'s closed side, dated.

    Same scan, same attribution rule, same caveats (a reverted or cherry-picked
    `status: done` line re-counts); the only addition is the commit date each hunk was
    seen under, which is what turns a count into an interval."""
    out: list[tuple[str, _dt.datetime]] = []
    current, when = "", None
    for line in _git(repo, "log", f"--since={a}", f"--until={b}", "-p",
                     "--format=%x00%aI", "--", "work-items").splitlines():
        if line.startswith("\x00"):
            try:
                when = _dt.datetime.fromisoformat(line[1:].strip())
            except ValueError:
                when = None
        elif line.startswith("+++ b/"):
            m = _WI_ITEM_PATH.match(line[len("+++ b/"):].strip())
            current = m.group(1) if m else ""
        elif line.startswith("+- status: done") and current and when:
            out.append((current, when))
    return out


def outcome_closures(repo: Path | None = None) -> list[tuple[str, _dt.datetime]]:
    """The completed items both outcome measures divide by — ONE list, deduplicated,
    earliest closure per id, over `OUTCOME_WINDOW_DAYS`.

    SHARED ON PURPOSE. The two R3 rows are both rates over "items the system finished",
    and the first cut of them computed that population twice: this one from `_wi_closures`
    (deduplicated) and the intervention row from `_wi_window` (not), which printed 240
    completions on one line and 271 on the next from the same window and the same store.
    Two denominators for one noun is the defect that makes a board unreadable — a reader
    who notices cannot tell which number is wrong, and one who does not compares rates
    computed against different populations."""
    repo = repo if repo is not None else ROOT
    today = _dt.date.today()
    out: dict[str, _dt.datetime] = {}
    for wid, when in _wi_closures(repo, today - _dt.timedelta(days=OUTCOME_WINDOW_DAYS),
                                  today):
        # A `status: done` line re-applied by a rebase, revert or cherry-pick is the SAME
        # closure seen twice (`_wi_window` documents the case). Keep the earliest.
        if wid not in out or when < out[wid]:
            out[wid] = when
    return sorted(out.items(), key=lambda kv: kv[1])


def wi_acceptance_verified(repo: Path, wid: str) -> bool:
    """Does this item's record carry a receipt against its own ACCEPTANCE line?

    The brief's rule for the interval: a `done` counts only when the closure "cites
    verification evidence against the item's acceptance line", and a `done` without one is
    UNVERIFIED — shown as such, and it does not end the interval.

    The receipt vocabulary is the harness's own `WI_ACCEPTANCE_RECEIPTS` — ACCEPTANCE MET
    / RECORDED / VERIFIED — imported rather than respelled here, because a second list of
    the accepted spellings is a second definition of "verified" that can drift from the one
    the store's own readiness check uses. Item body AND note records both count: the notes
    are where a lane writes its closing evidence, and `_note_append` is the only writer
    that does not touch the item file."""
    try:
        import session
        receipts = tuple(r.lower() for r in session.WI_ACCEPTANCE_RECEIPTS)
    except Exception:
        return False
    notes = repo / "work-items" / "notes" / wid
    paths = list((repo / "work-items").glob(f"{wid}-*.md"))
    paths += sorted(notes.glob("*.md")) if notes.is_dir() else []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(r in text for r in receipts):
            return True
    return False


def wi_weeks(repo: Path | None = None, weeks: int = WEEKS_FOR_TREND + 1) -> list[tuple[int, int]]:
    """[(created, done)] per week, oldest..newest — the totals the exit-1 verdict reads.
    Counting and its caveats are documented on `_wi_window`."""
    repo = repo if repo is not None else ROOT   # bound at CALL time, so --root works
    out = []
    today = _dt.date.today()
    for w in range(weeks, 0, -1):
        created, closed = _wi_window(repo, today - _dt.timedelta(days=7 * w),
                                     today - _dt.timedelta(days=7 * (w - 1)))
        out.append((len(created), len(closed)))
    return out


def wi_window_by_scope(repo: Path | None = None,
                       weeks: int = WEEKS_FOR_TREND + 1) -> dict[str, tuple[int, int]]:
    """{scope bucket: (created, done)} over the whole `weeks`-week window (R4, WI-0323).

    The SAME ids `wi_weeks` totals, grouped — so the buckets sum to the headline by
    construction and the two can never tell different stories about one window. Per-week
    AND per-scope would be four weeks by four buckets on a fixed-width board, which is a
    wall rather than a reading; the weeks carry the trend and the buckets carry the mix."""
    repo = repo if repo is not None else ROOT
    scopes = wi_scope_map(repo)
    out = {b: [0, 0] for b in SCOPE_BUCKETS}
    today = _dt.date.today()
    for w in range(weeks, 0, -1):
        created, closed = _wi_window(repo, today - _dt.timedelta(days=7 * w),
                                     today - _dt.timedelta(days=7 * (w - 1)))
        for wid in created:
            out[scopes.get(wid) or SCOPE_UNSET_BUCKET][0] += 1
        for wid in closed:
            out[scopes.get(wid) or SCOPE_UNSET_BUCKET][1] += 1
    return {b: (c, d) for b, (c, d) in out.items()}


def probe_ratio() -> tuple[str, str]:
    try:
        weeks = wi_weeks()
        v = verdict_ratio(weeks)
        ev = ("created/done by week, oldest→newest: "
              + "  ".join(f"{c}/{d}" for c, d in weeks))
        # R4: the trend reads BY SCOPE. Printed as a second segment rather than replacing
        # the weeks, because the verdict is still about the total and evidence that does
        # not contain the number the verdict was computed from is evidence for something
        # else. Every bucket prints even at 0/0, `unscoped` included — a bucket that
        # disappears when empty is one a reader cannot tell from a bucket nobody counted.
        try:
            by = wi_window_by_scope()
            ev += (f" · {7 * (WEEKS_FOR_TREND + 1)}d by scope: "
                   + "  ".join(f"{b} {by[b][0]}/{by[b][1]}" for b in SCOPE_BUCKETS))
        except Exception as e:
            ev += f" · scope split unavailable ({e})"
        return v, ev
    except Exception as e:
        return "UNKNOWN", f"could not read the store's history ({e})"


#: The harness's BODY — the entry point plus every part of the package behind it. Matched
#: against a path listing rather than walked on disk, because the same expression has to
#: answer at a revision from BEFORE the ADR-0118 split, where `sessionlib/` yields nothing
#: and `session.py` carries the whole body on its own. Nothing else at the repo root is
#: the harness: `bootstrap.py`, `poga_cli.py` and `standard_check.py` are separate tools
#: and were separate tools on both sides of the split, so including them would make the
#: two endpoints measure different populations.
_HARNESS_PATH = re.compile(r"^(session\.py|sessionlib/[^/]+\.py)$")


def harness_lines_at(repo: Path, rev: str) -> tuple[int, int]:
    """(lines, files) in the harness body at `rev`, read out of the tree at that revision.

    The file list comes from the revision's own tree, never from today's directory: at a
    revision predating ADR-0118 the answer is `session.py` alone, and at one after it the
    answer is however many parts the package had THEN. A hardcoded list of parts would
    read 0 for every part added since, which is the failure mode of measuring a filename
    instead of a subject ([`derive-a-checks-subjects-from-the-authority`](habits/master.md#derive-a-checks-subjects-from-the-authority))."""
    names = [n for n in _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()
             if _HARNESS_PATH.fullmatch(n.strip())]
    return sum(_git(repo, "show", f"{rev}:{n}").count("\n") for n in names), len(names)


def harness_lines_now(repo: Path) -> tuple[int, int]:
    """(lines, files) in the harness body as it stands in the working tree."""
    paths = [repo / "session.py", *sorted((repo / "sessionlib").glob("*.py"))]
    paths = [p for p in paths if p.is_file()]
    return (sum(p.read_text(encoding="utf-8", errors="ignore").count("\n") for p in paths),
            len(paths))


def probe_flat() -> tuple[str, str]:
    """Exit criterion 2: has the harness stopped growing?

    WHY NOT `session.py` ALONE, which is what this read until WI-0405. ADR-0118 made
    `session.py` a ~160-line entry point in front of `sessionlib/`, so the file this probe
    watched became one that structurally CANNOT grow — the probe reported flatness about a
    subject that had moved out from under it, and reported it with a number. It was not
    merely uninformative: measured on this checkout on 2026-09-20 it read 15,003 → 160
    lines, a −99% "shrinkage" that is the split being mistaken for progress. The same
    window measured over the body reads 15,003 → 38,937, +159%.

    Both of those are FAIL, which is the point worth keeping: correcting the measurement
    did not move the verdict, so this is the evidence becoming honest rather than a
    criterion being redefined until it reports something new (the same discipline
    `_wi_window` records for the created/closed count)."""
    try:
        then_rev = _git(ROOT, "rev-list", "-1", "--before=30 days ago", "main").strip()
        if not then_rev:
            return "UNKNOWN", "no commit 30 days back"
        then, then_files = harness_lines_at(ROOT, then_rev)
        if not then:
            return "UNKNOWN", f"no harness files in the tree at {then_rev[:8]}"
        now, now_files = harness_lines_now(ROOT)
        return verdict_flat(then, now), (
            f"harness {then} → {now} lines over 30 days "
            f"({(now - then) / max(then, 1):+.0%}) — session.py + sessionlib/, "
            f"{then_files} → {now_files} file(s)")
    except Exception as e:
        return "UNKNOWN", f"could not read the harness history ({e})"


def probe_open_items() -> tuple[str, str]:
    try:
        n = 0
        for p in (ROOT / "work-items").glob("WI-*.md"):
            head = p.read_text(encoding="utf-8", errors="ignore")[:600]
            if re.search(r"(?m)^- status: (open|in-progress)\b", head):
                n += 1
        return ("PASS" if n <= OPEN_ITEMS_TARGET else "FAIL"), f"{n} open items (target ≤{OPEN_ITEMS_TARGET})"
    except Exception as e:
        return "UNKNOWN", f"could not read the store ({e})"


def probe_member_substrate_items() -> tuple[str, str]:
    try:
        _, members = _fleet()
        hits = []
        for sid, (repo, _cfg) in members.items():
            if not (repo / "work-items").is_dir():
                continue
            names = _git(repo, "log", f"--since={WINDOW_DAYS} days ago", "--diff-filter=A",
                         "--name-only", "--format=", "--", "work-items").split()
            for n in names:
                title = Path(n).stem.split("-", 2)[-1].replace("-", " ")
                if is_substrate_internal(title):
                    hits.append(f"{sid}:{Path(n).stem[:9]}")
        if not members:
            return "UNKNOWN", "no members located"
        v = "PASS" if not hits else "FAIL"
        return v, (f"{len(hits)} substrate-internal items filed by product members in {WINDOW_DAYS}d"
                   + (": " + ", ".join(hits[:6]) if hits else ""))
    except Exception as e:
        return "UNKNOWN", f"could not read member stores ({e})"


# `probe_parked_questions` WAS HERE, and it is retired rather than fixed (WI-0405, R2).
#
# It reported `N/M sessions in 14d ended holding a question for operator (target 0)` and went
# red on any N. Two things were wrong with it and only one was a bug.
#
# The bug: it was a SECOND hand-rolled miner of the same journal section `curate/
# metrics.py` already mines, with its own regex, its own "does this bullet start with
# None" rule, and its own window — two counters over one source that could disagree, which
# is the thing WI-0278 wrote down as the ledger's standing constraint ("do not make this a
# second source of truth for the count").
#
# The design fault, which is the reason it is not simply rewritten against metrics: a
# target of zero questions is wrong in a system where operator holds approval authority BY
# DESIGN. A session that ends on a genuinely required decision is the harness working, and
# a row that scores it as a failure pays sessions to suppress legitimate questions — a
# metric improvable by behaving worse, which is the one property the outcome measures were
# specified to avoid. `probe_interventions` replaces it with the split that was missing:
# required decisions counted and shown with NO target, avoidable interventions carrying
# the number.


def _metrics():
    """`curate/metrics.py`, loaded from the checkout being measured.

    By path, like `_fleet` loads the parity tool, and for the same reason: `--root` has to
    reach it. Loading it from ROOT also lands its own `__file__`-derived `FED_ROOT` on the
    same checkout, so the module measures the tree this board was pointed at rather than
    the one this script happens to live in."""
    spec = importlib.util.spec_from_file_location("metrics", ROOT / "curate" / "metrics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scope_of(wid: str, scopes: dict) -> str:
    return scopes.get(wid) or SCOPE_UNSET_BUCKET


def _open_ids(repo: Path) -> set[str]:
    """Ids the store shows as not-yet-finished. Parsed the same way `probe_open_items`
    counts them, and for the same reason `wi_scope_map` states: `session._wi_parse` would
    resolve its directory from `session.ROOT` and ignore this script's `--root`."""
    out: set[str] = set()
    for p in (repo / "work-items").glob("WI-*.md"):
        m = _WI_ITEM_PATH.match(f"work-items/{p.name}")
        if not m:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:600]
        except OSError:
            continue
        if re.search(r"(?m)^- status: (open|in-progress|held)\b", head):
            out.add(m.group(1))
    return out


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def probe_claim_to_result() -> tuple[str, str]:
    """R3's first outcome measure: how long does an item take to reach a VERIFIED result?

    A REPORT, NOT A VERDICT, for the reason `probe_interventions` gives: the brief
    specifies a diagnostic distribution and sets no target, and a row that invents one
    would be the retired `target 0` row's mistake in a new column.

    THREE THINGS IT DELIBERATELY REFUSES TO FLATTER:

    - An UNVERIFIED closure does not end the interval. A `done` whose record carries no
      acceptance receipt is counted in its own column and left out of the duration, so
      closing an item without evidence cannot shorten the number — it moves the item into
      a column that is visibly larger instead.
    - UNFINISHED ITEMS ARE SHOWN BESIDE THE COMPLETED ONES, as the brief requires by name.
      A median over completed work alone improves every time something hard is abandoned;
      the age of what is still open is the correction, and it belongs on the same line.
    - WAITS ARE NOT SUBTRACTED. Time spent waiting on operator is inside the elapsed number.
      Netting it out would make the measure improvable by escalating more, which is the
      opposite of what its sibling row is for.

    THE START IS A SUBSTITUTE and `_wi_first_touch` says why: no first-claim stamp
    survives in the tracked store. This measures filed-or-first-noted → verified done, and
    prints which start each item used rather than presenting a mixture as one thing."""
    try:
        closures = outcome_closures(ROOT)
        starts = _wi_first_touch(ROOT)
        scopes = wi_scope_map(ROOT)
    except Exception as e:
        return "UNKNOWN", f"could not read the store's history ({e})"
    days: dict[str, list[float]] = {b: [] for b in SCOPE_BUCKETS}
    unverified = nostart = 0
    seen = {wid for wid, _ in closures}
    for wid, when in closures:
        if not wi_acceptance_verified(ROOT, wid):
            unverified += 1
            continue
        start = starts.get(wid)
        if start is None:
            nostart += 1
            continue
        days[_scope_of(wid, scopes)].append((when - start).total_seconds() / 86400)
    measured = [d for v in days.values() for d in v]
    # The open-item ages, so abandoning hard work cannot flatter the line above.
    now = _dt.datetime.now(_dt.timezone.utc)
    still_open = _open_ids(ROOT)
    ages = [(now - s).total_seconds() / 86400
            for wid, s in starts.items() if wid in still_open]
    if not measured:
        # A WINDOW THAT CLOSED NOTHING STILL REPORTS, rather than going UNKNOWN. A quarter
        # spent on work that never finished is a real reading of this measure, and it is
        # the exact state the open-item ages exist to make visible — answering "unknown"
        # there would hide the number in precisely the case it was specified for.
        return REPORT, (
            f"no verified closure in {OUTCOME_WINDOW_DAYS}d — {len(seen)} item(s) closed, "
            f"{unverified} of them with no acceptance receipt, {nostart} with no datable "
            f"start · still open: {len(ages)} item(s), median age {_median(ages):.0f}d")
    return REPORT, (
        f"median {_median(measured):.1f}d to verified done over {OUTCOME_WINDOW_DAYS}d "
        f"({len(measured)} of {len(seen)} closure(s); {unverified} unverified — no "
        f"acceptance receipt — and {nostart} with no datable start, both excluded) · "
        "by scope: "
        + "  ".join(f"{b} {_median(days[b]):.1f}d/{len(days[b])}" for b in SCOPE_BUCKETS)
        + f" · still open: {len(ages)} item(s), median age {_median(ages):.0f}d")


def probe_interventions() -> tuple[str, str]:
    """R3's avoidable-interventions measure: how often did operator have to step in per item
    the system actually finished?

    THE NUMERATOR COMES FROM `curate/metrics.py`, never from a read of the journals here.
    That is the constraint WI-0278 set on this ledger in as many words, and the probe this
    one replaces broke it — `probe_parked_questions` re-mined the same section with its own
    regex and its own window, so the board and the metrics run could report different
    numbers about one night and both be internally consistent.

    A REPORT, NOT A VERDICT. The brief specifies both outcome measures as diagnostic
    distributions, "neither a quota nor a gate", and required decisions explicitly carry no
    target. A PASS/FAIL row would have to invent a bar nobody set, and the board's own
    history here is the argument: the retired row's `target 0` was exactly such an invented
    bar, and it made asking operator a legitimate question score as a failure.

    BOTH ENDS SHARE ONE WINDOW. The rate is a ratio of two independently-mined populations,
    so `OUTCOME_WINDOW_DAYS` is passed to the numerator and used for the denominator rather
    than each side taking its own default — a ratio whose halves cover different spans is a
    number about nothing."""
    try:
        m = _metrics()
        journals = m.mine_journal_sessions(ROOT)
        s = m.summarize_interventions(journals, _dt.date.today(), OUTCOME_WINDOW_DAYS)
    except Exception as e:
        return "UNKNOWN", f"could not read the intervention ledger ({e})"
    try:
        closed = [wid for wid, _ in outcome_closures(ROOT)]
        scopes = wi_scope_map(ROOT)
    except Exception as e:
        return "UNKNOWN", f"could not read the store's history ({e})"
    if not s["sessions"]:
        return "UNKNOWN", f"no journals in {OUTCOME_WINDOW_DAYS}d"
    done = {b: 0 for b in SCOPE_BUCKETS}
    for wid in closed:
        done[_scope_of(wid, scopes)] += 1
    # An event is charged to every item it names — an escalation that blocked two items
    # interrupted the work on both — and to `unscoped` when it names none, which keeps the
    # by-scope columns summing to the headline instead of quietly dropping those events.
    avoidable_by = {b: 0 for b in SCOPE_BUCKETS}
    for e in s["events"]:
        if e["kind"] == "required":
            continue
        for b in ({_scope_of(w, scopes) for w in e["items"]} or {SCOPE_UNSET_BUCKET}):
            avoidable_by[b] += 1
    total_done = len(closed)
    head = (f"{s['avoidable_headline'] / total_done:.2f} avoidable/completed item"
            if total_done else "no completed items — rate unavailable")
    return REPORT, (
        f"{head} over {OUTCOME_WINDOW_DAYS}d — {s['avoidable_headline']} avoidable "
        f"({s['untagged']} untagged, counted as avoidable), {s['required']} required "
        f"(no target), {total_done} item(s) completed · by scope (avoidable/done): "
        + "  ".join(f"{b} {avoidable_by[b]}/{done[b]}" for b in SCOPE_BUCKETS)
        + f" · {s['silent']}/{s['sessions']} session(s) recorded no ledger section at all")


def probe_tokens() -> tuple[str, str, dict]:
    try:
        import token_ledger
        rows = token_ledger.read_sessions(days=WINDOW_DAYS)
        s = token_ledger.summarize(rows)
        if not s["sessions"]:
            return "UNKNOWN", "no transcripts in the window", s
        return verdict_ctx(s["ctx_median"]), token_ledger.status_line(s, WINDOW_DAYS), s
    except Exception as e:
        return "UNKNOWN", f"ledger could not read transcripts ({e})", {}


def probe_land_lock_hold() -> tuple[str, str]:
    """FL8 since ADR-0148 D7 (was FL7, WI-0329 / ADR-0124 D4): did any land in the window
    hold the queue too long? Renumbered, not retired — the operator's 2026-09-10 rule on the lock
    still stands; it is just no longer the number that says whether landing is fast.

    THE CLAIM BEING WATCHED. ADR-0124 took validation out of the one-at-a-time land lock
    so the serialized section holds only the merge and its publication. That is a claim
    about wall-clock, and a claim about wall-clock decays silently the moment somebody
    puts one more defensible step under the lock — which is exactly how the section grew
    to 329 seconds in the first place, one reasonable addition at a time.

    WHY THE WORST LAND AND NOT THE MEDIAN. A median hides the case this exists to catch:
    one land that holds the gate for four minutes is four minutes every other lane in the
    repo spends queueing, and it does not matter that ninety-nine others were quick. operator
    set the rule as "any land" and that is the right shape — this is a budget on the
    shared resource, not a performance average.

    LANDS STILL COMPLETE. Nothing here blocks anything; the board is a report. The land's
    own output already carries the OVER BUDGET line for the operator watching it.

    UNKNOWN, not PASS, when there is nothing to read. A window with no lands in it is not
    evidence that lands are fast ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes))."""
    try:
        import session
        receipts = session.land_receipts(days=WINDOW_DAYS)
    except Exception as e:
        return "UNKNOWN", f"land receipts unreadable ({e})"
    held = [r for r in receipts if isinstance(r.get("lock_seconds"), (int, float))]
    if not held:
        return "UNKNOWN", (f"no land receipts in {WINDOW_DAYS}d "
                           f"({session.LAND_RECEIPTS_NAME} in the git common dir)")
    budget = session.LAND_LOCK_HOLD_BUDGET_SECONDS
    worst = max(held, key=lambda r: r["lock_seconds"])
    over = [r for r in held if r["lock_seconds"] > budget]
    # GATE HOLDS, not lands. WI-0326 added a second event that holds this lock — a
    # `resume-lane` receipt, written when a stopped delivery is finished at the stage it
    # stopped. It queues a sibling exactly as a land does, so it belongs in this
    # population; calling the population "lands" once it contains something else is the
    # `declare-what-a-check-assumes` miss, in the summary line rather than the verdict.
    # The receipt's `verb` distinguishes them for anyone reading the file itself.
    summary = (f"{len(held)} gate hold(s)/{WINDOW_DAYS}d, worst hold "
               f"{worst['lock_seconds']:.1f}s (budget {budget:.0f}s)")
    if not over:
        return "PASS", summary
    stages = ", ".join(f"{k} {v}s" for k, v in sorted(worst.get("stages", {}).items())
                       if isinstance(v, (int, float)))
    return "FAIL", (f"{len(over)} gate hold(s) over budget; worst {worst['lock_seconds']:.1f}s "
                    f"on {worst.get('lane', '?')} at {worst.get('at', '?')}"
                    + (f" — {stages}" if stages else ""))


def probe_land_duration() -> tuple[str, str]:
    """FL7 (ADR-0148 D7, WI-0427): does landing take under a minute, as operator feels it?

    `total_seconds` — ready-to-land through on-trunk, every wait included — at the p90
    over the last 7 days, split code / bookkeeping, each against its own target (60 s /
    30 s). RED if either class is over. The previous FL7 read the LOCK hold and was
    green while the median land took three and a half minutes: the consultant reported
    a "30-second land" off that number on 2026-09-10. The metric was wrong, not the
    build; this is the number the person landing actually waits for.

    Receipts older than ADR-0148 carry no `diff_class`; `receipt_diff_class` classifies
    them from their own diff, so the window is full from the first day.

    UNKNOWN, not PASS, on an empty window (`declare-what-a-check-assumes`)."""
    try:
        import session
        days = session.LAND_DURATION_WINDOW_DAYS
        split = {cls: session.land_duration_stats(days=days, diff_class=cls)
                 for cls in ("code", "bookkeeping")}
    except Exception as e:
        return "UNKNOWN", f"land receipts unreadable ({e})"
    goals = {"code": session.LAND_DURATION_GOAL_SECONDS,
             "bookkeeping": session.LAND_BOOKKEEPING_GOAL_SECONDS}
    if not any(st.get("n") for st in split.values()):
        return "UNKNOWN", (f"no measured land in {days:.0f}d "
                           f"({session.LAND_RECEIPTS_NAME} in the git common dir)")
    red, parts = False, []
    for cls, st in split.items():
        if not st.get("n"):
            parts.append(f"{cls}: none")
            continue
        over = st["p90"] > goals[cls]
        red = red or over
        parts.append(f"{cls} p90 {st['p90']:.1f}s over {st['n']} land(s), goal "
                     f"{goals[cls]:.0f}s{' — OVER' if over else ''}")
    return ("FAIL" if red else "PASS"), f"{days:.0f}d: " + "; ".join(parts)

# ---- the board -------------------------------------------------------------------------

def board(run_tests: bool = True) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    caps = []
    if run_tests:
        caps.append(("capability suite (7 statements)",) + probe_tests())
    caps.append(("start budget + role doc",) + probe_start_budget())
    caps.append(("land duration (FL7)",) + probe_land_duration())
    caps.append(("land lock hold (FL8)",) + probe_land_lock_hold())
    caps.append(("fleet byte identity",) + probe_fleet_identity())
    v, ev, _ = probe_tokens()
    caps.append(("context per turn",) + (v, ev))
    caps.append(("time to verified done",) + probe_claim_to_result())
    caps.append(("avoidable interventions",) + probe_interventions())
    exits = [
        ("1. created ≤ closed, 3 weeks",) + probe_ratio(),
        ("2. harness flat 30 days",) + probe_flat(),
        ("3. no substrate items from members",) + probe_member_substrate_items(),
        ("   open items under target",) + probe_open_items(),
    ]
    return caps, exits


#: A row that carries a measured number and NO bar. The two outcome measures (R3) are
#: specified as diagnostic distributions — "neither a quota nor a gate", and required
#: decisions explicitly with no target — so scoring them PASS/FAIL would mean inventing a
#: threshold nobody set. That is not a hypothetical risk here: the row these two replace
#: carried `target 0` for questions asked of operator, and it is retired for exactly that.
#:
#: REPORT rows are excluded from the green tally rather than counted as failures. A
#: measurement with no bar cannot be green, and counting it red would make adopting a
#: diagnostic look like a regression — which is how a board teaches people not to add one.
REPORT = "REPORT"


def _scored(rows):
    """The rows a green tally is computed over: everything that has a bar."""
    return [r for r in rows if r[1] != REPORT]


def render(caps, exits) -> str:
    out = ["FINISH LINE — is the harness mature yet?", ""]
    out.append("Capabilities (live):")
    for name, v, ev in caps:
        out.append(f"  [{v:7s}] {name:34s} {ev}")
    out.append("")
    out.append("Exit criteria (all three, then it is mature):")
    for name, v, ev in exits:
        out.append(f"  [{v:7s}] {name:34s} {ev}")
    passed = sum(1 for _, v, _ in exits[:3] if v == "PASS")
    out.append("")
    out.append(f"verdict: {'MATURE' if passed == 3 else 'NOT YET'} — {passed}/3 exit criteria hold.")
    return "\n".join(out)


def status_line(caps, exits) -> str:
    scored = _scored(caps)
    cp = sum(1 for _, v, _ in scored if v == "PASS")
    ep = sum(1 for _, v, _ in exits[:3] if v == "PASS")
    reports = len(caps) - len(scored)
    return (f"finish-line: {cp}/{len(scored)} capabilities green, {ep}/3 exit criteria"
            + (f", {reports} measure(s) reported without a bar" if reports else "")
            + " — `python3 curate/finish_line.py` for the board.")


def main(argv=None) -> int:
    # THE WHOLE DOCSTRING, not its first line. R3 requires the two outcome measures'
    # definitions to be readable from the board itself rather than only from the brief
    # that specified them — a definition that lives only in a consultant document is one
    # the next reader of a surprising number cannot reach.
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--tests", action="store_true",
                    help="also run the capability suite (WRITES to users/ and "
                         "proposed-edits/ via the real bootstrap adopt; see probe_tests)")
    ap.add_argument("--no-tests", action="store_true",
                    help="accepted and ignored; the suite is opt-in via --tests")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", help="measure another checkout (default: this one)")
    a = ap.parse_args(argv)
    _set_root(a.root)
    try:
        # Default OFF (was: on unless --no-tests). A board that mutates the checkout by
        # default cannot be the thing a session runs to orient itself.
        caps, exits = board(run_tests=a.tests and not a.status)
    except Exception as e:
        print(f"finish-line: could not compute the board ({e}).")
        return 0
    if a.json:
        print(json.dumps({"capabilities": caps, "exit_criteria": exits}, indent=1))
    elif a.status:
        print(status_line(caps, exits))
    else:
        print(render(caps, exits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
