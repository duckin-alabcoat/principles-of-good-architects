#!/usr/bin/env python3
"""Upstream reconcile — read each managed system's own STATUS.md and compare it
to the federation's local picture, so the federation never acts on a stale mirror.

This is P18 `verify-everything` applied to the federation's own records, and the
deterministic half of the reconcile (P15 — code does the read/compare; the
Architect keeps the judgment about what to do with the drift). It MUTATES NOTHING:
it reads each member's own STATUS.md and role doc and prints a drift report.

Background, and what changed (WI-0090, session ~140): the federation used to keep
hand-maintained copies of each member's role doc (`inputs/<id>-arch.md`) and compare
those against the member's live `STATUS.md`. Those copies drifted the instant a system
had a session the federation didn't see (sessions 35/37 both got bitten — acted on a
months-stale mirror), and there was no refresh mechanism at all: clearing them was a
recurring hand-cleanup, not a fix. Worse, the comparison could not say WHICH side was
behind — "our copy is stale" and "their file is behind" printed as the same DRIFT row.

The mirrors are retired. Both numbers now come from the member's own repo: its
`STATUS.md` version (ADR-0021: id/phase/version/last_active/blocked) against the
version in the role doc its own `session.config.json` declares. So the flag says
something true about the MEMBER — their two self-reports disagree — instead of
something about the freshness of our filing. There is nothing left to hand-refresh,
which is the point ([`verify-against-self-report-not-mirrors`]; P16).

The declared roster in `portfolio.md` supplies the membership the mirror set used to
imply, so "expected here but not reachable" is still a statable outcome rather than a
silent absence.

P3: machine-specific repo paths are NOT stored here. Pass the search root(s) as
arguments; the federation session supplies its own machine's roots. Example:

    python3 curate/reconcile.py /Users/you/Projects /Users/you/Work

Each root is scanned (bounded depth) for `STATUS.md`; found systems are matched to the
`portfolio.md` roster by the STATUS `id` field.

A system whose repo does NOT live under any walk root is located instead via the gitignored `repo-paths.local` map
(`<system-id> = <repo path>`, see `repo-paths.example`). The map is read directly and
takes precedence over a walked hit for the same id. This is the structural guard
against the session-35/37/47 recurrence: a rostered system with no located STATUS is
reported as UNLOCATED — "not reachable from this machine" — and NEVER conflated with
"not onboarded." A map entry that points somewhere without a valid STATUS.md is
reported as BROKEN MAP (fix the map, not the system).

A STATUS.md that is located but declares no `id:` is a FOURTH outcome, ID-LESS, and it
is reported at its real path (WI-0373). It used to be dropped by both locators and then
misdiagnosed in opposite directions: the walk silently skipped it, so the member fell
into UNLOCATED and the report told the reader to go look on another machine for a file
sitting right there, broken; the map read it under a truthy guard (`if live_id and
live_id != sid`) and filed it as a converged member, certifying a gap. The fix is the
DIAGNOSIS, not the silence — the drop was always surfaced, it was surfaced as the wrong
thing, and a wrong diagnosis is worse than none because it is actionable.

A rostered member the roster declares lives on ANOTHER machine is reported as
ELSEWHERE BY DESIGN, not as UNLOCATED (WI-0253). devbox deliberately carries only a
subset of the fleet, so for those members absence here is the arrangement working; for
every other member it is a gap, and the two share the symptom. The residency label is
DECLARED in `portfolio.md`'s `Resides` column and never inferred from the absence
itself, which is the same rule (and, since WI-0253, literally the same code in
`curate/common.py`) that `curate/deliver.py` applies to `mailboxes.json`. The two tools
print into the same startup banner and must not contradict each other there.

Any located checkout is also checked against its own origin, and that read answers
two separate questions: a repo far behind `origin` is a possible STALE-CLONE (the
dead-clone tell — one retired member copy was 5 ahead / 125 behind), while
a repo both ahead AND behind at ANY depth is DIVERGED — it can no longer run the
`git pull --ff-only` its own session-start ritual performs. Read-only, no fetch: this
report never touches a member's refs. A caller about to WRITE wants the fetching mode
(`common.git_divergence(..., fetch=True)`), because a cached ref cannot report that
the cache is old.

Session-start signal:

    python3 curate/reconcile.py --status

reads its roots from a gitignored machine-local config (`reconcile-roots.local`,
one path per line, `#` comments allowed) instead of argv — so the search roots
stay out of tracked files (P3) — and prints a single summary line for the
session-start hook (like `curate/gather.py --status`). Missing config is reported,
never an error. See `reconcile-roots.example` for the format.
"""

import datetime
import json
import pathlib
import re
import subprocess
import sys

from common import (
    declared_elsewhere,
    git_divergence,
    provenance_clause,
    read_repo_paths_config as _read_repo_paths_config,
    read_roots_config as _read_roots_config,
    shared_work_root,
    this_machine,
    walk_tree,
)

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent

# TWO ROOTS, DELIBERATELY (WI-0246). This module reads federation files of two
# different kinds, and they do NOT share an answer to "which checkout?":
#
#   FED_ROOT        the tree this script is RUNNING IN. Authored, tracked content
#                   any lane may edit — `portfolio.md`. A lane that has edited the
#                   roster IS the truth about the roster; main is not yet.
#   _MAIN_CHECKOUT  the main working tree, shared by every lane. Two kinds of file
#                   legitimately live only there: machine-local config excluded from
#                   version control (`reconcile-roots.local`, `repo-paths.local` — a
#                   lane has no copy at all), and trunk-only GENERATED views whose
#                   single writer is the trunk's own compile (`STATUS.md`, ADR-0056 /
#                   `_stamp_status_only` in session.py) — a lane never writes those,
#                   so its copy is only ever a stale duplicate.
#
# The test is NOT "tracked vs gitignored" — it is WHO WRITES THE FILE. Route by that.
# Before WI-0246 both kinds went through `shared_work_root()`, so a roster row applied
# and verified inside a lane read back as `off-roster(not in portfolio.md)` — a tool
# reporting a real problem that had already been fixed, which invites fixing it twice.
# `shared_work_root()` itself is right and stays: nine of its ten callers want exactly
# the main checkout. Only this module ever passed tracked content through it.
from common import member_config_root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sessionlib.brief import status_frontmatter as _status_frontmatter  # noqa: E402
_MAIN_CHECKOUT = member_config_root(FED_ROOT)
ROOTS_CONFIG = _MAIN_CHECKOUT / "reconcile-roots.local"
REPO_PATHS_CONFIG = _MAIN_CHECKOUT / "repo-paths.local"
MAX_DEPTH = 4
STALE_DAYS = 14  # last_active older than this is flagged (not an error — a signal)
BEHIND_COMMITS = 40  # a checkout this far behind its own origin is the stale-clone tell


def parse_frontmatter(text):
    """Return the YAML-frontmatter key/values of a STATUS.md as a dict, or {}.

    Deliberately tiny: STATUS.md frontmatter is flat `key: value` lines between
    the opening and closing `---`. No yaml dependency, no nested structures. The
    STATUS dialect of the one shared reader (`sessionlib/brief.py`): keys kept as
    written, and a `#` line is not special.
    """
    return _status_frontmatter(text)


def _idless_reason(fm):
    """Why a located STATUS.md carries no `id:` — the two cases are different facts.

    `parse_frontmatter` returns `{}` both for a file with no frontmatter block at all
    and for one whose block parsed to nothing, and it returns a populated dict for a
    file that simply never declared `id`. Folding those into one message would make the
    report say "no id" about a file that is missing its whole header, which points the
    reader at the wrong line ([`declare-what-a-check-assumes`]).
    """
    return "no frontmatter block" if not fm else "frontmatter carries no `id:`"


VERSION_RE = re.compile(
    # Tolerate the value being wrapped in backticks and/or a `v` prefix — a role doc
    # may write `**Version**: `v1.2.3`` (a spelling some runtimes use), which the bare
    # `v?` form couldn't parse, leaving such a member forever "version-unreadable".
    # Same class as the session-73 metrics fix; clean spellings (`version: 4.5.6`,
    # `**Version:** v7.8.9`) still parse.
    r"(?im)^\**version:?\**\s*[:\-]?\s*[`v]*([0-9]+\.[0-9]+(?:\.[0-9]+)?)")


def roster_ids():
    """The declared member roster — system ids from `portfolio.md` (ADR-0006 authority).

    This replaces the retired `inputs/<id>-arch.md` mirror set as the answer to "who
    should be here?" (WI-0090). The mirrors were never a roster on purpose; they were a
    by-product of having once copied someone's role doc, which made membership a
    function of our own filing rather than of the registry. Losing the roster entirely
    when the mirrors went would have been the worse bug: an unreachable member would
    simply not appear, which reads as "fine" instead of UNLOCATED.

    The federation itself is excluded — it does not reconcile against itself.

    Read from FED_ROOT — the tree we are running in — not from the main checkout
    (WI-0246). `portfolio.md` is authored content whose writer may be any lane, so the
    lane holding an applied roster row is the truth about membership; anchoring on main
    made that lane's own verified fix read back as `off-roster`. See the two-roots note
    at the top of this module for the routing rule.
    """
    path = FED_ROOT / "portfolio.md"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    ids = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # System ID is the 4th data column, written in backticks in the roster table.
        for cell in cells[4:6]:
            m = re.fullmatch(r"`([a-z0-9][a-z0-9-]*)`", cell)
            if m and not m.group(1).endswith("-arch"):
                ids.add(m.group(1))
                break
    return ids


def roster_residency():
    """{system-id: declared machine label} from `portfolio.md`'s `Resides` column.

    The column that lets this module tell "absent on purpose" from "absent and that is a
    gap" (WI-0253). Only members with a non-empty, non-placeholder label appear; an empty
    cell, an em-dash, or no such column at all yields NO entry for that member, which is
    the safe direction — an undeclared member keeps reporting as UNLOCATED, so a roster
    that has not been given residency yet detects exactly what it detected before rather
    than quietly excusing the whole fleet ([`ship-the-detector-with-the-capability`]).

    Read from FED_ROOT for the same reason `roster_ids()` is (WI-0246): `portfolio.md` is
    authored content and the lane holding an edited roster is the truth about it.

    The column is located BY ITS HEADER NAME, not by a fixed index. `roster_ids()` above,
    `curate/standard_version.py:read_roster` and `poga_cli.py:portfolio_system_ids` all
    anchor on the adjacent `system-id` / `system-id-arch` pair, so none of them cares
    where other columns sit; hard-coding an index here would have been the one reader
    that a later column insertion silently mis-parsed — reading a Status cell as a
    machine label, which `declared_elsewhere` would then compare against `DevBox` and
    find "different", i.e. it would EXCUSE members rather than fail loudly.
    """
    path = FED_ROOT / "portfolio.md"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}

    col = None
    out = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if col is None:
            for i, cell in enumerate(cells):
                if cell.lower() == "resides":
                    col = i
                    break
            continue
        # A data row is one carrying the sid/aid convention pair (ADR-0006) — the same
        # anchor every other reader of this file uses, so the disposition tables further
        # down the page cannot leak in.
        for i, cell in enumerate(cells[:-1]):
            m = re.fullmatch(r"`([a-z0-9][a-z0-9-]*)`", cell)
            if not m or cells[i + 1].strip("`") != f"{m.group(1)}-arch":
                continue
            if col < len(cells):
                label = cells[col]
                # `-`/`—`/`n/a` are how a human writes "nothing here" in a table; treat
                # them as the empty cell they mean rather than as a machine named "—".
                if label and label.lower() not in ("-", "\u2014", "\u2013", "n/a", "none"):
                    out[m.group(1)] = label
            break
    return out


def live_roledoc_version(repo_dir):
    """Read the member's OWN role-doc version, from its own repo (WI-0090).

    Returns a version string, or a sentinel: "<no-config>" when the repo declares no
    `session.config.json`, "<no-roledoc>" when the declared role doc is missing, and
    None when the file exists but carries no parseable version line. Each is a
    DIFFERENT fact and the report prints them differently
    ([`declare-what-a-check-assumes`](habits/master.md#declare-what-a-check-assumes)) —
    folding them together is how "couldn't check" once printed as "checked, fine."

    Why the member's own file rather than a federation-held copy: a mirror can only
    ever tell us how stale our own filing is, and the drift it reported was
    indistinguishable in the output from the member's live file being behind (two
    identically-labelled DRIFT rows with opposite causes, seen live session ~140).
    Reading the source removes the category instead of detecting it faster.
    """
    cfg = pathlib.Path(repo_dir) / "session.config.json"
    if not cfg.is_file():
        return "<no-config>"
    try:
        declared = json.loads(cfg.read_text(errors="replace")).get("role_doc")
    except (OSError, ValueError):
        return "<no-config>"
    if not declared:
        return "<no-roledoc>"
    doc = pathlib.Path(repo_dir) / declared
    if not doc.is_file():
        return "<no-roledoc>"
    try:
        head = doc.read_text(errors="replace")[:4000]
    except OSError:
        return "<no-roledoc>"
    m = VERSION_RE.search(head)
    return m.group(1) if m else None


def self_entry():
    """Our own `({sid: (fm, path)}, idless)` from the known work root.

    `idless` is the `[(path, reason)]` list — at most one entry here, our own STATUS.md
    when it carries no `id:`. It used to be dropped (`if fm.get("id") else {}`), which
    put the hub itself into `absent` and rendered it UNLOCATED: "the live repo isn't
    reachable from this machine", said about the tree the script is executing in
    (WI-0373). Same defect shape as `find_status_files`, one line apart, which is why
    both locators now return the same pair rather than one of them being taught alone.

    Kept OUT of `find_status_files`, which is a root-scoped primitive that
    `curate/metrics.py` also calls: seeding it there put the real federation repo into
    every caller's result, including a mine over empty roots, so "the fleet is empty"
    silently stopped being true. A shared primitive answers exactly the question it was
    asked; this is reconcile's own addition, applied at reconcile's own entry points.

    Why we include ourselves at all: we are on the roster and now reconcile like any
    other member (ADR-0003), but our repo need not sit under a walk root or in the
    locator map — without this the hub would report ITSELF as UNLOCATED on any machine
    whose map lacks an entry for it, a false alarm about the one repo we are standing in.

    This one stays on the MAIN checkout, and that is not the WI-0246 defect repeated:
    `STATUS.md` is a trunk-only generated view (ADR-0056) that a lane never writes — the
    land stamps the main checkout's copy precisely so the trunk's is the one every other
    system reads. A lane's copy is therefore a frozen duplicate, never a newer truth, and
    the role-doc version this is compared against is read from the same tree, so the
    self-consistency check compares two files with one writer. Contrast `roster_ids()`
    above, whose file any lane may author.
    """
    status = _MAIN_CHECKOUT / "STATUS.md"
    if not status.is_file():
        return {}, []
    try:
        fm = parse_frontmatter(status.read_text(errors="replace"))
    except OSError:
        return {}, []
    sid = fm.get("id")
    if not sid:
        return {}, [(status, _idless_reason(fm))]
    return {sid: (fm, status)}, []


def find_status_files(roots):
    """Walk the roots for STATUS.md — returns `(seen, idless)`.

    `seen` is `{sid: (fm, path)}`, first hit per id wins. `idless` is the
    `[(path, reason)]` list of STATUS.md files that declare no `id:`, sorted by path.

    WI-0373 — WHY THE SECOND VALUE EXISTS, AND WHY IT IS MANDATORY. An id-less file
    used to be `continue`d away here. The member whose file it was then fell out of
    `found`, landed in `classify_absent`'s `absent`, and printed under UNLOCATED —
    whose prose reads "not reachable from this machine; add the repo's path to
    repo-paths.local". So the one surface that had the evidence (a broken STATUS.md at
    a real path on this disk) emitted a confident instruction to go look on another
    machine. The drop was surfaced; the DIAGNOSIS was wrong, and a wrong diagnosis is
    worse than silence exactly because it is actionable.

    Returned as a tuple rather than collected into a caller-supplied list because an
    out-parameter a caller can forget to pass is the same silent drop one indirection
    further away — the callers are made to see the fact, as `age_days`'s clock is.
    """
    candidates, idless = find_status_candidates(roots)
    return {sid: hits[0] for sid, hits in candidates.items()}, idless


def find_status_candidates(roots):
    """Walk the roots for STATUS.md — returns `({sid: [(fm, path), ...]}, idless)`.

    EVERY hit per id, in walk order, where `find_status_files` keeps only the first.
    A caller that must CHOOSE between two repos claiming one id needs to see both:
    `deliver.locate_repos` skips linked git worktrees and refuses two main checkouts,
    and a first-hit map had already thrown away the evidence either judgement reads
    (2026-09-29: devbox's walk met an `Orchestrator 1-wt-*` worktree before the member's main
    checkout, and delivery aimed at a mailbox that did not exist there)."""
    seen, idless = {}, []
    for root in roots:
        base = pathlib.Path(root).resolve()
        if not base.is_dir():
            print(f"  (skipped non-directory root: {root})", file=sys.stderr)
            continue
        # Pruned, depth-bounded walk (curate/common.py) — rglob enumerates the
        # whole tree before filtering, which made this SessionStart hook crawl
        # any deep node_modules/venv under a root. Same reach as before: the
        # STATUS.md file itself sits at most MAX_DEPTH path components below
        # the root, i.e. its directory at most MAX_DEPTH - 1.
        for dirpath, _dirnames, filenames in walk_tree(base, MAX_DEPTH - 1):
            if "STATUS.md" not in filenames:
                continue
            path = pathlib.Path(dirpath) / "STATUS.md"
            try:
                fm = parse_frontmatter(path.read_text(errors="replace"))
            except OSError:
                continue
            sid = fm.get("id")
            if not sid:
                idless.append((path, _idless_reason(fm)))
                continue
            # The federation is NOT skipped any more (WI-0090). The old rule existed
            # because the comparison was "your live file vs OUR copy of it", which is
            # meaningless against ourselves. Now both numbers come from one repo's own
            # two self-reports, so the check means the same thing for every member —
            # and run against ourselves it immediately caught our own STATUS.md frozen
            # a minor behind the role doc. ADR-0003: a participant on equal footing.
            # every hit, in walk order; `find_status_files` keeps the first
            seen.setdefault(sid, []).append((fm, path))
    return seen, sorted(idless, key=lambda e: str(e[0]))


def age_days(last_active, today):
    """Days from `last_active` to `today`, or None when `last_active` is unparseable.

    `today` is REQUIRED and deliberately has no default (WI-0264). This function used to
    read `datetime.date.today()` itself, so a caller running on an *injected* clock —
    `curate/metrics.py`'s `compute_exceptions`, whose entire point is that a fixture can
    pin the date — silently measured ages against whatever day the suite happened to run
    on. Nothing went red, because being MORE stale than the fixture meant still reads as
    stale; the direction that rots is the negative assertion ("NOT stale"), which starts
    failing on a date nobody chose. A default parameter would let exactly that omission
    recur in silence at the next call site, so the parameter is mandatory and the one
    genuine real-clock caller (`main()`) names its clock out loud
    ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
    """

    try:
        d = datetime.date.fromisoformat(last_active)
    except (ValueError, TypeError):
        return None
    return (today - d).days


def read_roots_config(warn=True):
    """The gitignored machine-local search roots (P3) — shared mechanics in
    curate/common.py; this system's config file is bound here. `warn=False` asks for the
    roots without the WI-0132 no-usable-roots line, for a caller that will report the
    condition itself."""
    return _read_roots_config(ROOTS_CONFIG, warn=warn)


def read_repo_paths():
    """The gitignored machine-local `<system-id> = repo-path` locator map (P3).

    Read directly (not walked) so a system whose repo is not under any walk root
    still reconciles. This is the
    structural guard against the session-35/37/47 recurrence: mislocating a live,
    converged system as "never onboarded" because its repo wasn't under a root.
    """
    return _read_repo_paths_config(REPO_PATHS_CONFIG)


def resolve_mapped(repo_paths):
    """Read STATUS.md directly at each mapped repo path.

    Returns (found, broken): `found` is {sid: (fm, path)} for map entries whose
    `<path>/STATUS.md` parses to that sid; `broken` is {sid: (path, reason)} for
    entries that don't resolve — the mapped path is missing, unreadable, carries no
    STATUS.md, declares no `id:` at all, or its STATUS `id` disagrees with the mapped
    key. A broken entry means the LOCATOR CHAIN is wrong, never that the system is
    un-onboarded.

    THE QUIET HALF OF WI-0373. The disagreement guard used to read
    `if live_id and live_id != sid`, so an id-less STATUS.md was truthy-guarded past it
    and filed into `found` as a healthy `sid`. One input, two entry points, two opposite
    wrong answers: the walk over-reported it as a missing repo, the map under-reported
    it as a converged member. Fixing only the loud half leaves the quiet half in place,
    and the quiet half is the one that certifies a gap rather than merely mislabelling
    one. The map key is a claim about WHICH REPO this is; it was never a substitute for
    the file's own self-report, and a STATUS.md with no `id:` has not made one.
    """
    found, broken = {}, {}
    for sid, raw in repo_paths.items():
        base = pathlib.Path(raw).expanduser()
        if not base.is_dir():
            broken[sid] = (raw, "path is not a directory")
            continue
        status = base / "STATUS.md"
        if not status.is_file():
            broken[sid] = (raw, "no STATUS.md at mapped path")
            continue
        try:
            fm = parse_frontmatter(status.read_text(errors="replace"))
        except OSError as e:
            broken[sid] = (raw, f"unreadable STATUS.md ({e})")
            continue
        live_id = fm.get("id")
        if not live_id:
            broken[sid] = (raw, f"STATUS.md declares no id ({_idless_reason(fm)})")
            continue
        if live_id != sid:
            broken[sid] = (raw, f"STATUS id '{live_id}' != mapped key '{sid}'")
            continue
        found[sid] = (fm, status)
    return found, broken


def git_staleness(repo_path):
    """Behind/diverged-from-origin signal for a located checkout — the dead-clone
    tell (P18). Read-only: compares HEAD to the ALREADY-KNOWN upstream ref, never
    fetches (no network, no ref mutation). Returns a flag string or None.

    TWO QUESTIONS, TWO VERDICTS (WI-0213). They shared one threshold and the
    cheaper-to-fix one was invisible:

      STALE-CLONE?  "is this an abandoned copy?" — a retired member copy at
                    5 ahead / 125 behind. Keeps its `BEHIND_COMMITS` threshold, which
                    is the right number for that question.
      DIVERGED      "can this member still run its session-start git ritual?" — and
                    the answer is no for ANY ahead>0 with behind>0, because a diverged
                    branch is not fast-forwardable whether behind is 1 or 125. Reported
                    at any depth, with no threshold at all.

    Both verdicts read the same predicate (`common.git_divergence`), so the write-side
    freshness precondition in `push-substrate.py` is implementable from one fact rather
    than from a second copy of this arithmetic. Any git error (no upstream, not a repo,
    git absent, timeout) returns None — this is a best-effort signal, never a hard
    failure of the read-only reconcile.
    """
    d = git_divergence(repo_path)  # read-only by default: this caller never fetches
    if not d["readable"]:
        return None
    ahead, behind = d["ahead"], d["behind"]
    if behind >= BEHIND_COMMITS and ahead > 0:
        return f"STALE-CLONE?(diverged {ahead} ahead / {behind} behind origin)"
    if behind >= BEHIND_COMMITS:
        return f"STALE-CLONE?({behind} behind origin)"
    if d["diverged"]:
        return f"DIVERGED({ahead} ahead / {behind} behind origin)"
    return None


def compute_drift(roots, repo_paths=None):
    """Return (found, roster, broken, idless) — the raw reconcile inputs, no printing.

    Mapped repos (read directly at their `repo-paths.local` path) take precedence
    over the same id discovered by walking the roots — the map is the authoritative
    locator when present. `roster` is the declared membership (`portfolio.md`), which
    is what makes "expected but not reachable here" a statable outcome at all.

    `idless` (WI-0373) is the `[(path, reason)]` list of STATUS.md files located on
    THIS machine that carry no `id:`. It is a fourth outcome alongside located /
    broken-map / absent, and it is deliberately not folded into any of them: such a
    file is present and broken, which is the opposite of the absent-and-elsewhere
    story `unlocated` tells about it.
    """
    found, idless = self_entry()
    walked, walked_idless = find_status_files(roots)
    found.update(walked)
    idless.extend(walked_idless)
    broken = {}
    if repo_paths:
        mapped, broken = resolve_mapped(repo_paths)
        found.update(mapped)  # mapped wins over walked
    return (found, roster_ids(), broken,
            sorted(idless, key=lambda e: str(e[0])))


def classify_absent(roster, found, residency=None, machine=None, broken=()):
    """Split the rostered-but-not-located members into (elsewhere, unlocated).

    `elsewhere` is {sid: label} for members the roster declares live on a DIFFERENT
    machine; `unlocated` is the sorted rest — the members whose absence is a gap.

    This is the WI-0253 fix in one function, and it is a function rather than two
    inline expressions because both surfaces of this module — the `--status` banner line
    and the full report — have to give the same answer. WI-0175 is the precedent: the
    last time these two computed a roster fact separately, the hook printed 7 unlocated
    and the command it told a human to run printed 15.

    `broken` — the ids `resolve_mapped` could not resolve — is subtracted from `absent`
    too (WI-0373). A member whose repo path IS mapped has been located; what failed is
    the file at the end of the path, and that is already reported with its real path and
    its real reason in its own BROKEN MAP section. Leaving it in `unlocated` as well
    printed one member twice under two contradictory diagnoses, and the louder of the
    two told the reader to add a path that was already there. An empty default keeps
    every existing caller's answer unchanged.

    Only members that were NOT located are considered, so a declaration can never hide a
    member that is present and broken; and `machine` defaulting to `this_machine()` —
    which returns "" when `scutil` cannot answer — means an unnamed machine produces an
    empty `elsewhere` and every absence stays a gap. Withhold the exemption, never grant
    it ([`declare-what-a-check-assumes`]).
    """
    if residency is None:
        residency = roster_residency()
    if machine is None:
        machine = this_machine()
    absent = set(roster) - set(found) - set(broken)
    elsewhere = {}
    for sid in absent:
        # Wrapped as a `{"resides": …}` row on purpose: `declared_elsewhere` takes a
        # REGISTRY ROW, and `mailboxes.json` and `portfolio.md` deliberately share the
        # field name so one declaration reads identically to both tools (WI-0253).
        label = declared_elsewhere({"resides": residency.get(sid)}, machine)
        if label:
            elsewhere[sid] = label
    return elsewhere, sorted(absent - set(elsewhere))


def status_line():
    """One-line session-start signal (read by the SessionStart hook)."""
    roots = read_roots_config()
    repo_paths = read_repo_paths()
    if not roots and not repo_paths:
        return ("Reconcile: no roots or repo-path map — create `reconcile-roots.local` "
                "and/or `repo-paths.local` (see the .example files) to enable the "
                "drift signal.")
    found, roster, broken, idless = compute_drift(roots, repo_paths)
    drift, unreadable = [], []
    for sid in sorted(found):
        fm, path = found[sid]
        rver = live_roledoc_version(path.parent)
        if rver is None or str(rver).startswith("<"):
            unreadable.append(sid)
        elif rver != fm.get("version", "?"):
            drift.append(sid)
    # THREE outcomes, not two (WI-0253). "unlocated": the roster declares this member,
    # no live STATUS was found by walk OR map, and nothing says that is expected — NOT
    # "not onboarded", it means the live repo isn't reachable from this machine's
    # configured locations, and mapping it in repo-paths.local resolves it. "elsewhere":
    # the roster's `Resides` column puts it on another machine, so its absence here is
    # the fleet layout and there is no path to map. Folding the middle into the last is
    # what made this line contradict the delivery-audit line directly above it in the
    # same banner.
    elsewhere, unlocated = classify_absent(roster, found, broken=broken)
    parts = []
    if drift:
        parts.append(f"{len(drift)} self-inconsistent ({', '.join(drift)})")
    if unreadable:
        parts.append(f"{len(unreadable)} version-unreadable ({', '.join(unreadable)})")
    if broken:
        parts.append(f"{len(broken)} broken-map ({', '.join(sorted(broken))})")
    if idless:
        # Counted, never named: these files have no id to name them BY — that is the
        # whole condition. The count is what stops the reader reading the `unlocated`
        # number below as the complete story of what is missing (WI-0373).
        parts.append(f"{len(idless)} id-less STATUS.md")
    off_roster = sorted(set(found) - roster) if roster else []
    if off_roster:
        parts.append(f"{len(off_roster)} off-roster ({', '.join(off_roster)})")
    tail = f"; {len(unlocated)} unlocated" if unlocated else ""
    # Counted, never listed: this is a one-line hook and the by-design members are the
    # half a reader does NOT need to act on. Naming the count keeps the line honest about
    # what it left out — silence would read as "the roster is fully located".
    if elsewhere:
        tail += f"; {len(elsewhere)} elsewhere by design"
    # ADR-0108 D1: name the tree and commit the roster was measured against. That is
    # what turns "off-roster" from a claim about the world into a claim about a specific
    # file — WI-0236 instance 2, where a roster row applied and verified in a lane read
    # back as off-roster from a source nobody could see. WI-0246 then fixed the routing
    # underneath: the roster is read from FED_ROOT, the tree we are running in, so this
    # clause names that tree — and a lane's verdict now measures against the lane's own
    # roster rather than reporting a stale main under a correctly-rendered provenance.
    src_clause = provenance_clause(FED_ROOT)
    if not parts:
        return (f"Reconcile: all {len(found)} located member(s) self-consistent{tail} "
                f"(roster {src_clause}).")
    return (f"Reconcile: {'; '.join(parts)}{tail} (roster {src_clause}) — "
            f"run `python3 curate/reconcile.py` to inspect.")


def main():
    if "--status" in sys.argv[1:]:
        print(status_line())
        return 0

    # argv wins, then the machine-local config — the same roots `--status` already reads
    # (WI-0175). Without the fallback the two halves of this one module answered
    # differently: the hook's `--status` line read the config and reported 7 unlocated,
    # while the full report it tells a human to run took argv only, found nothing to walk,
    # and called every member unlocated. Neither said which roots it was missing, so the
    # louder, wronger surface was the one a person saw.
    roots = sys.argv[1:] or read_roots_config()
    repo_paths = read_repo_paths()
    if not roots and not repo_paths:
        print(__doc__)
        print("ERROR: pass at least one search root, or populate repo-paths.local "
              "(P3 — paths are not stored in tracked files).", file=sys.stderr)
        return 2

    found, idless = self_entry()
    walked, walked_idless = find_status_files(roots)
    found.update(walked)
    idless.extend(walked_idless)
    idless.sort(key=lambda e: str(e[0]))
    mapped, broken = resolve_mapped(repo_paths)
    mapped_ids = set(mapped)
    found.update(mapped)  # mapped repo (direct read) wins over a walked one

    roster = roster_ids()
    # The CLI is the real-clock caller: `age_days` takes the date it measures from
    # (WI-0264), so this is where the machine's day enters, named and once.
    today = datetime.date.today()

    src_note = f"{len(mapped)} via map" if mapped else "0 via map"
    print(f"Upstream reconcile — {len(found)} live STATUS.md located "
          f"({src_note}, roots: {', '.join(roots) or 'none'})\n")
    header = (f"{'system':<28} {'src':<5} {'status':<10} {'role-doc':<10} "
              f"{'last_active':<12} {'age':>5}  flags")
    print(header)
    print("-" * len(header))

    reconciled = set()
    for sid in sorted(found):
        fm, path = found[sid]
        reconciled.add(sid)
        live = fm.get("version", "?")
        rver = live_roledoc_version(path.parent)
        la = fm.get("last_active", "?")
        age = age_days(la, today)
        src = "map" if sid in mapped_ids else "walk"
        flags = []
        if rver == "<no-config>":
            flags.append("no-session-config")
        elif rver == "<no-roledoc>":
            flags.append("role-doc-missing")
        elif rver is None:
            flags.append("role-doc-version-unreadable")
        elif rver != live:
            # Both numbers now come from the MEMBER's own repo, so this says something
            # true about them rather than about our filing: their STATUS.md and their
            # role doc disagree. Session ~140: this is what a frozen `last_active`
            # looks like from outside, and briefing the member to hand-fix it would
            # ask them to correct something our harness froze (WI-0091).
            flags.append(f"SELF-DRIFT(status {live} != role-doc {rver})")
        if roster and sid not in roster:
            # Located, self-consistent, and not a member as far as the registry knows.
            # Silence here is how a half-finished rename hides: the OLD id walks up
            # clean while the NEW one reads UNLOCATED, and both look like someone else's
            # problem (WI-0073, live). Name it on the row that has the evidence.
            flags.append("off-roster(not in portfolio.md)")
        if age is not None and age > STALE_DAYS:
            flags.append(f"stale({age}d)")
        if fm.get("blocked", "false").lower() not in ("false", "", "no", "none"):
            flags.append("BLOCKED")
        stale = git_staleness(path.parent)
        if stale:
            flags.append(stale)
        age_s = f"{age}d" if age is not None else "?"
        print(f"{sid:<28} {src:<5} {live:<10} {str(rver):<10} {la:<12} {age_s:>5}  "
              f"{'; '.join(flags) if flags else 'ok'}")

    if broken:
        print("\nBROKEN MAP (repo-paths.local points somewhere that doesn't resolve — "
              "fix the MAP, not the system):")
        for sid in sorted(broken):
            raw, reason = broken[sid]
            print(f"  - {sid}  -> {raw}  ({reason})")

    if idless:
        print("\nID-LESS STATUS.md (found ON THIS MACHINE and carrying no `id:`, so it "
              "cannot be attributed to any roster member):")
        for path, reason in idless:
            print(f"  - {path}  ({reason})")
        print("  Fix the FILE, not the locator: add `id: <system-id>` to its "
              "frontmatter (ADR-0021). These are NOT unlocated members — the repo is "
              "on this disk, at the path above, and nothing in repo-paths.local or "
              "portfolio.md's `Resides` column will change what this file says about "
              "itself.")

    machine = this_machine()
    elsewhere, unlocated = classify_absent(roster, reconciled, machine=machine,
                                           broken=broken)
    if elsewhere:
        print(f"\nELSEWHERE BY DESIGN ({len(elsewhere)}) — portfolio.md's `Resides` "
              f"column puts these on another machine, so they are not reachable from "
              f"{machine} and nothing is wrong.")
        print("  There is no path to add to repo-paths.local: the repo is not on this "
              "disk. Reconcile them from the machine named beside each.")
        for sid in sorted(elsewhere):
            print(f"  · {sid:<28} lives on {elsewhere[sid]}, by declaration")
    if unlocated:
        print("\nUNLOCATED (on the portfolio.md roster; no live STATUS found by walk or "
              "map on THIS machine, and no `Resides` declaration saying that is "
              "expected).")
        print("  This means 'not reachable from here' — NOT 'not onboarded.' Add the "
              "repo's path to repo-paths.local to reconcile it; if the member is "
              "deliberately on another machine, declare that in portfolio.md's "
              "`Resides` column instead of mapping a path that does not exist.")
        if idless:
            # The one sentence that keeps the advice above honest when both states are
            # live at once (WI-0373). An id-less file cannot be matched to a member, so
            # this list may contain a member that is PRESENT and broken. Without this,
            # the reader follows correct-looking advice to a machine the repo was never
            # on. Printed only when there is something to point at, so the common case
            # keeps the shorter, true text.
            print(f"  BEFORE looking on another machine: {len(idless)} id-less "
                  f"STATUS.md file(s) were found here (listed above). An id-less file "
                  f"cannot be matched to a member, so one of the members below may in "
                  f"fact be sitting at one of those paths, broken rather than absent.")
        for sid in unlocated:
            print(f"  - {sid}")
    if not machine and roster - reconciled:
        # The withheld-exemption case, said out loud rather than left as a silently
        # longer UNLOCATED list. Without this line the degraded run is indistinguishable
        # from a run where nobody had declared residency yet.
        print("\n  (This machine could not name itself, so NO residency declaration was "
              "honoured above — every absence is listed as UNLOCATED. That is the "
              "deliberate direction to fail in, not a claim that nothing is declared.)")
    if not roster:
        print("\nROSTER NOT READ — portfolio.md is missing or unparseable, so 'expected "
              "but unreachable' could not be computed. This is NOT the same as 'every "
              "member was located'; fix the roster before trusting the run.")

    # Name both roots on the full report too, not only on `--status` (ADR-0108 D1,
    # WI-0246). `off-roster` and `UNLOCATED` above are claims about ONE portfolio.md,
    # and from a lane there are two candidates; a reader who cannot see which was read
    # cannot tell an unfixed roster from a roster fixed in the other tree.
    print(f"\nRoster source: {provenance_clause(FED_ROOT)} — the tree this run is "
          f"standing in. Machine-local locator config read from {_MAIN_CHECKOUT}.")
    print("\nThis is a read-only signal — every version above is read from the member's "
          "OWN repo, not from a federation-held copy (WI-0090). Nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
