#!/usr/bin/env python3
"""Substrate push — the federation writes its OWN generated substrate into every
reachable converged Architect repo, instead of asking each target to fetch it.

Rationale (refines ADR-0027): `CANON.md` and `STANDARD.md` are *federation-
generated* (distill.py / standardize.py); `session.py` is federation-root and
byte-identical across every Architect. The federation is their **single writer**.
So delivering them is the federation writing its own state into a place it is
allowed to write — NOT authoring a target's own files. ADR-0027 sanctioned
dropping briefs into a target's inbox; this refines that boundary: the federation
may also write+commit its OWN generated substrate anywhere it reaches, but still
never a target's *authored* files (its role doc, its config, its working files).

Why a push at all: a brief that says "copy from `bootstrap-kit/CANON.md`" sends
the target hunting for a path that exists only in the federation's filesystem
(the session-43 example-app failure). Generated/identical substrate is the federation's
output — it should be pushed, not briefed. Briefs are then left carrying only the
genuine target edits (see `curate/check-brief.py`).

It commits ONLY the specific substrate file paths it changed — the `git add` is
path-scoped AND the `git commit` carries the same pathspec, because a bare
commit ships the whole index and a target whose main ref advanced under its
checkout (a lane land) has the pre-land tree sitting staged (the 2026-08-07
sample-service clobber). A stale-checkout guard skips such targets by name before
anything is written. Idempotent: a repo already current is a no-op.

THREE PER-TARGET GATES, and each answers a question the other two cannot
(`target_base_is_current` and `_refresh_lines` carry the long form):

  freshness       is the target's local trunk current with its REMOTE? FETCHES
                  first, because a cached ref cannot report that the cache is
                  old. Added by WI-0213 — the two gates below both compare a
                  target only to ITSELF, which a clone that has not fetched in
                  weeks satisfies perfectly, so an earlier run committed
                  into members on a stale base and found out at push time.
  uncommitted     does the target have local edits to a substrate path? Those
                  are a policy violation, but they are the Architects' to
                  resolve and not this script's to erase.
  stale-checkout  does the target's index diverge from HEAD outside the
                  substrate manifest? The sample-service clobber above.

All three SKIP the target by name and write nothing; none of them halts the run
over the rest of the fleet. Refusing to write is strictly better than writing
and failing to push — the rejected push is what leaves a member diverged, and a
diverged member fails the `git pull --ff-only` in its own session-start ritual.
`--repair` exists for members already in that state and is not a substitute for
not putting them there.

P3: machine-specific repo paths are NOT stored here. The search roots come from
the gitignored machine-local `reconcile-roots.local` (one path per line, `#`
comments) — the same file `reconcile.py` reads. P15: this is the deterministic,
no-judgment mechanism; there is nothing for an LLM to decide.

Federation-only (like reconcile.py / distill.py) — not shipped to fresh Architects.

Usage:
    python3 curate/push-substrate.py --help     # the interface, without doing anything
    python3 curate/push-substrate.py            # PLAN: report what WOULD change, write nothing
    python3 curate/push-substrate.py --go       # actually write and commit

PLANS BY DEFAULT (WI-0178). This writes to and commits in every member repo it finds,
so the safe mode is the one you get without asking. `--go` runs the full test suite and
the source-integrity check first and refuses on either; a plan runs neither and says so.
"""

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

from common import (
    declared_elsewhere,
    git_divergence,
    read_roots_config as _read_roots_config,
    read_repo_paths_config,
    roots_diagnosis,
    member_config_root,
    this_machine,
    walk_tree,
)
from gen_settings import render as render_settings

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent
# The federation session harness — for the C5 push-substrate LEASE (ADR-0051): two poga
# lanes must not both push to the shared member repos at once. Fail-open: if session can't
# be imported the push proceeds unguarded (never brick the rollout on a coord-layer hiccup).
sys.path.insert(0, str(FED_ROOT))
import poga_evidence as evidence  # noqa: E402  (FED_ROOT is the repo root, just added)
try:
    import session as _session
except Exception:
    _session = None
# WI-0361: the one resolver for the machine-local locator — external under a declared
# production service, and `shared_work_root` everywhere else.
ROOTS_CONFIG = member_config_root(FED_ROOT) / "reconcile-roots.local"
REPO_PATHS_CONFIG = member_config_root(FED_ROOT) / "repo-paths.local"
FLOOR_PATH = FED_ROOT / "standard-settings.json"
MAX_DEPTH = 4

# `.claude/settings.json` is NOT byte-identical across repos — it is GENERATED
# per-target from the shared floor (standard-settings.json) + that target's own
# settings_extras (ADR-0034), and is delivered only to repos that DECLARE a
# config (see settings_target — the clobber guard). The other three substrate
# files are pushed byte-identical from the federation root.
SETTINGS_PATH = ".claude/settings.json"

# Pushed byte-identical from the federation root (STANDARD.md is refresh-only).
# standard_check.py is the ADR-0047 member-side self-check — shipped like session.py —
# and `standard-capabilities.json` is the versioned capability description it detects
# against, so the two always travel in the same push (Package 3).
# `poga` is the ADR-0060 lane launcher, fleet-promoted session 98: it resolves the repo
# it drives from the CALLER'S CWD, so the identical bytes correctly drive whichever
# member you are standing in — which is what makes it substrate rather than a
# federation-only tool. Its bootstrap/restore verbs need poga_cli.py (not shipped) and
# refuse with a named message on a member copy.
# The harness PACKAGE (`sessionlib/`) is substrate too, and its member list is NOT
# hardcoded here. session.py was split into modules; which modules exist is a fact of
# the federation's own tree, so it is READ from there. Hardcoding the filenames would
# mean every future module needed an edit in this file before it could reach the fleet
# — a capability shipped without its delivery path, which is the shape this tool exists
# to prevent (`ship-the-detector-with-the-capability`). Sorted for a stable print and
# commit order; empty — and therefore a no-op — if the split has not landed yet.
SESSIONLIB_DIRNAME = "sessionlib"


def federation_sessionlib_files():
    """`sessionlib/*.py` as repo-relative paths, read from the federation's own tree."""
    try:
        return sorted(f"{SESSIONLIB_DIRNAME}/{mod.name}"
                      for mod in (FED_ROOT / SESSIONLIB_DIRNAME).iterdir()
                      if mod.suffix == ".py" and mod.is_file())
    except OSError:
        return []


# `interpreter.py` rides with `session.py` because `session.py` IMPORTS it (WI-0328).
# Not executable — it is imported, never invoked — so it stays out of EXECUTABLE, on the
# same argument as the sessionlib modules below.
BYTE_IDENTICAL = ["CANON.md", "STANDARD.md", "STANDARD-REFERENCE.md",
                  "session.py", "interpreter.py",
                  "standard_check.py", "standard-capabilities.json", "poga",
                  *federation_sessionlib_files()]
# Substrate that must land EXECUTABLE. write_bytes() alone leaves a fresh file at the
# umask default (0644), so a pushed `poga` would be present-but-unrunnable — the
# capability detector would read it as present while `./poga` returned "permission
# denied". Applied after every write, not only on create: a target whose mode drifted
# is repaired on the next push.
# The `sessionlib/` modules are deliberately NOT here: they are imported, never
# invoked, so 0644 is correct for them and a chmod would be noise in every target's
# history.
EXECUTABLE = {"poga"}
# Substrate the federation ONCE shipped and has since RETIRED (WI-0341, found by
# OPS-0007's first run). `diff_files` can only ever enumerate what the federation HAS,
# so a file dropped from BYTE_IDENTICAL — or deleted from the federation's own tree —
# simply stops being pushed, and the copy already sitting in every member becomes
# IMMORTAL. Nothing in this file has ever had a target-side deletion path.
#
# Two kinds of retirement, and only one of them can be derived:
#
#   - `sessionlib/*.py` is DERIVED. The federation's own package is the authority on
#     which modules exist — the same argument that keeps the member list out of
#     BYTE_IDENTICAL — so a module a target carries that the federation does not is
#     retired BY CONSTRUCTION. No list to maintain, and a module deleted tomorrow is
#     detected without anyone editing this file
#     ([`derive-a-checks-subjects-from-the-authority`]).
#   - A ROOT file must be DECLARED. Nothing distinguishes a retired `interpreter.py`
#     from a file the member wrote itself; the federation's *absence* of a file is not
#     evidence it ever shipped one. So a root retirement is named here, once, when it
#     is made — and not naming it is what leaves the file immortal.
#
# EMPTY TODAY, AND THAT IS THE POINT. Read back over every revision of this file, the
# manifest has only ever grown (CANON/STANDARD/session.py -> +standard_check -> +poga
# -> +sessionlib -> +interpreter), so there is no retired root file to seed this with.
# The mechanism ships BEFORE the first retirement rather than after it, because the
# failure it prevents is silent and permanent, and the day someone deletes a module is
# not the day anyone thinks to build this
# ([`ship-the-detector-with-the-capability`]).
RETIRED = []
# The federation's OWN generated substrate, single-writer here — for display and
# the federation-source presence check only. Settings is handled separately,
# gated by settings_target, because its target path AND its very eligibility are
# per-repo, not a fixed byte-identical file.
SUBSTRATE_FILES = [*BYTE_IDENTICAL, SETTINGS_PATH]
# A directory qualifies as a converged Architect repo if it is a git repo and
# carries both of these at its root. Deliberately NOT extended with `sessionlib/`:
# these markers say "this is a member we service", and a member that has not yet
# received the package is exactly the member that most needs servicing. `session.py`
# stays at the root as the entry point in both shapes, so it remains the right marker.
CONVERGENCE_MARKERS = ["CANON.md", "session.py"]


def read_roots_config():
    """The gitignored machine-local search roots (P3) — shared mechanics in
    curate/common.py; this system's config file is bound here."""
    return _read_roots_config(ROOTS_CONFIG)


def is_git_repo(path):
    return (path / ".git").is_dir()


# ADR-0041: a system declares its runtime in session.config.json ("runtime",
# default "claude-code"). A non-Claude system that keeps Claude for governance carries
# the governance substrate (CANON.md + session.py) at root, so it MATCHES the
# convergence markers — but the fleet push is the *Claude binding's* delivery
# mechanism and must skip it (ADR-0041 §5: skip, never clobber, a non-Claude
# target). Its governance substrate is serviced at retrofit / in a governance
# session, not by the blanket fleet push (which would also fail on a repo with no
# git remote). Absent the field, a repo is Claude-Code as before.
CLAUDE_RUNTIME = "claude-code"


def repo_runtimes(repo):
    """The declared runtime(s) for this repo, as a list. A system may
    declare co-equal runtimes in session.config.json "runtimes" (an array); the
    legacy single "runtime" string still reads as a one-element list. Defaults to
    [claude-code] when unconfigured or unreadable (don't guess non-Claude)."""
    cfg_path = repo / "session.config.json"
    if not cfg_path.is_file():
        return [CLAUDE_RUNTIME]
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return [CLAUDE_RUNTIME]  # unreadable config — treat as default, don't guess non-Claude
    rts = cfg.get("runtimes")
    if isinstance(rts, list) and rts:
        return [str(r) for r in rts]
    single = cfg.get("runtime", CLAUDE_RUNTIME) or CLAUDE_RUNTIME
    return [single]


def find_converged_repos(roots):
    """Scan roots (bounded depth) for converged Architect repos.

    A converged repo is a directory that is a git repo AND carries both
    CONVERGENCE_MARKERS at its root AND is not the federation repo itself.
    A resident-runtime member on the CANON floor (2026-07-01) carries CANON.md +
    session.py at its root, so it MATCHES here — but it has no session.config.json, so
    settings_target() gates it out of the settings push (that root is a resident
    runtime persona's repo, not an Architect harness repo). Truly unconverged
    systems lack the markers and are skipped here.
    """
    found = {}
    for root in roots:
        base = pathlib.Path(root).resolve()
        if not base.is_dir():
            print(f"  (skipped non-directory root: {root})", file=sys.stderr)
            continue
        # Pruned, depth-bounded walk (curate/common.py) — rglob enumerates the
        # whole tree before filtering. A repo is any dir with a .git child; the
        # walk reports .git in dirnames but never descends into it.
        for dirpath, dirnames, _filenames in walk_tree(base, MAX_DEPTH - 1):
            if ".git" not in dirnames:
                continue
            repo = pathlib.Path(dirpath)
            if not (repo / ".git").is_dir():
                continue
            if repo.resolve() == FED_ROOT:
                continue  # never push to the federation itself
            if not all((repo / m).is_file() for m in CONVERGENCE_MARKERS):
                continue
            found.setdefault(repo.resolve(), repo)
    return found


def run_git(repo, args):
    """Run `git -C <repo> <args>`; return (ok, combined_output)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


# Substrate files that are refreshed where present but never INTRODUCED.
# STANDARD.md is the standard-floor rituals ("the harness already ran", the
# session.py close protocol) — false in a CANON-floor repo (a resident-runtime
# member's chosen floor: CANON.md + session.py as guard source, deliberately no STANDARD.md).
# Creating it there is the convergence-brief-two-floors defect class; which
# floor a repo is on is a brief/migration decision, not this script's.
# (.claude/settings.json is ALSO refresh-only, but that is enforced inside
# diff_files together with the settings_target config gate, not via this set,
# because its target path is per-repo — see settings_target.)
REFRESH_ONLY = {"STANDARD.md"}
# STANDARD-REFERENCE.md is deliberately NOT here. A member on the standard floor
# carries an injected tier that NAMES the reference tier, so a member with
# STANDARD.md and no STANDARD-REFERENCE.md is a dangling pointer, not a floor
# choice. Introducing it is the fix for that, not a second floor (WI-0208).


def settings_target(repo):
    """Where — and WHETHER — to push a generated .claude/settings.json for this
    repo. Returns a repo-relative path, or None to SKIP settings entirely.

    This is the ADR-0034 clobber guard. settings.json is generated from the
    shared floor + this repo's OWN `settings_extras`, which live in its
    `session.config.json`; so the eligibility rule is: a settings push requires
    a declared config.

      - No session.config.json  -> None. The repo declares no extras, so the
        only thing we could push is the bare floor. For a repo that carries a
        root .claude/settings.json for a DIFFERENT reason — another agent
        that owns the repo root and keeps its own permission lockdown there — that
        push would replace that lockdown with an Architect baseline.
        That resident-runtime repo matches CONVERGENCE_MARKERS but has no config,
        so it is gated out here. (Operational + SECURITY hazard — the reason no
        live push may target it until this guard exists.)
      - Config present, no `settings_path` key -> ".claude/settings.json" (root):
        the normal converged-Architect case (its extras compose into the floor).
      - `settings_path: "<path>"` -> that path: the resident-runtime layout keeps
        the Architect's settings off the repo root (where the runtime persona's
        own settings live); the config declares where.
      - `settings_path` = false | null | "" | "none" | "off" -> None: explicit
        opt-out for a configured repo that manages its own settings.
    """
    cfg_path = repo / "session.config.json"
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None  # unreadable config — never guess a settings target
    sp = cfg.get("settings_path", SETTINGS_PATH)
    if sp in (None, False, "", "none", "off"):
        return None
    # A repo declaring a RESIDENT RUNTIME never has its ROOT settings
    # written, whatever the config says. The declared non-root path already keeps us
    # away from it, but that is correct-BY-CONFIGURATION; this is correct-by-
    # construction. The file in question is the resident agent's own permission
    # lockdown, so a
    # single mistyped config value would swap a live security boundary for an
    # Architect baseline — silently, with a green push report. The asymmetry between
    # that outcome and these four lines is the whole argument.
    if str(cfg.get("pad_dir") or "").strip() and str(sp).strip() == SETTINGS_PATH:
        return None
    return sp


def settings_content(repo):
    """Generated settings.json bytes for this repo — the federation floor + THIS
    repo's own settings_extras (read, never written, from its session.config.json),
    so a system's declared additions survive the push (ADR-0034). Only called for
    a repo settings_target() accepted, i.e. one that has a config."""
    cfg_path = repo / "session.config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    out = render_settings(cfg)
    _assert_extras_survived(repo, cfg, out)
    return out.encode("utf-8")


def _assert_extras_survived(repo, cfg, rendered):
    """Refuse to emit a settings.json that has LOST a hook the member declared.

    On 2026-08-01 one member's `.claude/settings.json` was rewritten with both of its
    `settings_extras` hooks absent — a startup sweep and a session-end teardown, both
    commands of another member's tool. A second member's was written in the same minute and the two files came out
    BYTE-IDENTICAL, which per-target rendering cannot produce. It sat undetected for three
    days; the only symptom was a dirty tree, and a dirty tree reads as ordinary.

    The harm is not a stale evidence field, which is what the first member had predicted and
    written off as benign. With those hooks gone there is no teardown and no startup
    sweep, so a dev server started from that checkout OUTLIVES ITS SESSION — the exact
    guarantee [`no-server-outlives-its-session`] exists to provide. And it is silent in
    both directions: the member's `session.config.json` still reads correctly, so an
    Architect inspecting the source sees a healthy pin while the generated file that
    actually executes has neither hook.

    This guard is that member's own suggested fix, taken as offered: *"every hook declared in
    settings_extras is present in the rendered settings.json"* is cheap and deterministic,
    and its absence is why three days passed silently
    ([`ship-the-detector-with-the-capability`]).

    Deliberately a REFUSAL, not a warning. A shrinking render is never a legitimate output
    — no member benefits from receiving one — so emitting it and reporting the fact would
    still ship the harm. Raises rather than returning a flag so it cannot be ignored by a
    caller that forgets to check ([`declare-what-a-check-assumes`]).

    NOTE ON SCOPE: the renderer itself was exonerated when this was investigated (2026-08-04)
    — unchanged since 07-28 and demonstrably preserving both hooks — so this guards the
    write point against a cause still unidentified rather than against a known bug in
    `render`. That is the right place for it either way: whatever wrote those files, this
    is where a shrinking result would have been caught."""
    declared = []
    for _event, blocks in ((cfg.get("settings_extras") or {}).get("hooks") or {}).items():
        for block in (blocks or []):
            for h in (block.get("hooks") or []):
                cmd = (h.get("command") or "").strip()
                if cmd:
                    declared.append(cmd)
    # Compare PARSED structures, not raw text. A substring check over the rendered JSON
    # looks right and is wrong: that member's teardown hook embeds quotes
    # (`--cwd "$CLAUDE_PROJECT_DIR"`), which JSON escapes, so the declared string never
    # appears literally in the output and a healthy render reads as a dropped hook. Caught
    # by running this against the real member instead of a fixture — the first version
    # refused that member's own correct settings.
    try:
        emitted = set()
        for _e, blocks in (json.loads(rendered).get("hooks") or {}).items():
            for block in (blocks or []):
                for h in (block.get("hooks") or []):
                    if h.get("command"):
                        emitted.add(h["command"].strip())
    except Exception as e:
        raise RuntimeError(f"REFUSING to write {repo.name}/.claude/settings.json — the "
                           f"render is not parseable JSON ({e}).")
    missing = [c for c in declared if c not in emitted]
    if missing:
        raise RuntimeError(
            f"REFUSING to write {repo.name}/.claude/settings.json — the render DROPPED "
            f"{len(missing)} hook(s) this member declares in settings_extras:\n" +
            "\n".join(f"    {c}" for c in missing) +
            "\n  A shrinking render is a regression, not an output. Nothing was written "
            "for this repo. Check that settings_extras parses as {event: [ {matcher, "
            "hooks:[{command}]} ]} and that the renderer read THIS repo's live config.")


def diff_files(repo):
    """Substrate whose target copy differs from what it should be. Returns a list
    of (label, rel_path, want_bytes): `label` is what to print, `rel_path` is
    where in the repo to write, `want_bytes` is the content. STANDARD.md is
    refresh-only (never introduced when absent); settings is refresh-only AND
    gated by settings_target (config-declared eligibility + per-repo path)."""
    changed = []
    for name in BYTE_IDENTICAL:
        # `name` may be NESTED (`sessionlib/config.py`). A target that lacks the
        # package RECEIVES it — it is new substrate being introduced, exactly as
        # session.py itself would be — so it takes the ordinary introduce path below
        # and the writer creates the parent directory. Only REFRESH_ONLY files are
        # withheld when absent.
        if not (FED_ROOT / name).is_file():
            continue  # federation lacks this source; nothing to push
        want = (FED_ROOT / name).read_bytes()
        dst = repo / name
        if not dst.is_file():
            if name not in REFRESH_ONLY:
                changed.append((name, name, want))
            continue
        if dst.read_bytes() != want:
            changed.append((name, name, want))
        elif name in EXECUTABLE and not (dst.stat().st_mode & 0o111):
            # Right bytes, wrong mode — a present-but-unrunnable `poga`. Bytes-only
            # comparison would call this up-to-date forever, and the capability
            # detector (which tests presence) would agree while `./poga` returned
            # "permission denied". Git tracks the exec bit, so re-writing identical
            # bytes plus the chmod produces a real, committable mode change.
            changed.append((f"{name} (mode)", name, want))

    # Settings — GENERATED per-target and gated. None => this repo gets no
    # settings push (an unconfigured resident-runtime repo, or explicit
    # opt-out). Refresh-only: never INTRODUCE a settings file that isn't there.
    sp = settings_target(repo)
    if sp is not None:
        dst = repo / sp
        if dst.is_file():
            want = settings_content(repo)
            if dst.read_bytes() != want:
                changed.append((SETTINGS_PATH, sp, want))
    return changed


def retired_root_files():
    """Declared root retirements, split into (removable, contradictory).

    A path named in BOTH `RETIRED` and `BYTE_IDENTICAL` is a contradiction: the push
    would write it and the retire would delete it, flapping every member's history
    once per run. Resolved toward NOT DELETING — the half that leaves a target exactly
    as it was — and NAMED in the report rather than quietly dropped, because a silent
    resolution is how the contradiction survives to the next reader
    ([`declare-what-a-check-assumes`]).
    """
    shipped = set(BYTE_IDENTICAL) | {SETTINGS_PATH}
    removable, contradictory = [], []
    for rel in RETIRED:
        (contradictory if rel in shipped else removable).append(rel)
    return removable, contradictory


def _classify_orphan(repo, rel, removable, notes):
    """Sort one retired path into removable-or-reported by whether git TRACKS it.

    ONLY A TRACKED FILE IS REMOVABLE, and that is the whole safety argument for
    offering removal at all: a tracked deletion sits in the member's own history, so
    anyone who disagrees with this tool's verdict can restore it with one revert. An
    untracked file has no such backstop — deleting it is unrecoverable — so it is
    reported and left, however confident the retirement list is.
    """
    ok, out = run_git(repo, ["ls-files", "--", rel])
    if not ok:
        notes.append(f"{rel}: RETIRED, but could not be checked against this member's "
                     f"index — left in place ({out}).")
    elif out.strip():
        removable.append(rel)
    else:
        notes.append(f"{rel}: RETIRED, but UNTRACKED here — left in place. A tracked "
                     f"deletion is recoverable from this member's history; an "
                     f"untracked one is not.")


def orphan_files(repo):
    """Substrate this TARGET carries that the federation has RETIRED (WI-0341).

    The mirror image of `diff_files`, which can only ever enumerate what the federation
    HAS. Returns `(removable, notes)`: paths safe to delete, and report lines for
    everything found that is NOT being deleted. The second list is half the point —
    "couldn't tell" must never render as "checked and clean"
    ([`declare-what-a-check-assumes`]).
    """
    removable, notes = [], []

    fed_mods = federation_sessionlib_files()
    pkg = repo / SESSIONLIB_DIRNAME
    if pkg.is_dir():
        if not fed_mods:
            # REFUSE TO ANSWER. With no readable federation package, every module in
            # the target compares as "not in the federation", so the naive answer is
            # DELETE THE WHOLE PACKAGE FROM EVERY MEMBER — an outcome incomparably
            # worse than the immortal file this exists to fix. The derived check is
            # only as good as its authority, so when the authority is missing the
            # honest output is "not checked", not an empty orphan list that reads as
            # clean ([`derive-a-checks-subjects-from-the-authority`]).
            notes.append(f"{SESSIONLIB_DIRNAME}/: NOT CHECKED — the federation carries "
                         f"no readable {SESSIONLIB_DIRNAME}/ package, so 'retired' and "
                         f"'cannot tell' are indistinguishable from here.")
        else:
            have = set(fed_mods)
            try:
                found = sorted(f"{SESSIONLIB_DIRNAME}/{m.name}" for m in pkg.iterdir()
                               if m.suffix == ".py" and m.is_file())
            except OSError as exc:
                found = []
                notes.append(f"{SESSIONLIB_DIRNAME}/: NOT CHECKED — could not read the "
                             f"target's package ({exc}).")
            for rel in found:
                if rel not in have:
                    _classify_orphan(repo, rel, removable, notes)

    declared, contradictory = retired_root_files()
    for rel in contradictory:
        notes.append(f"{rel}: named in RETIRED but still shipped in BYTE_IDENTICAL — "
                     f"NOT removed. A file cannot be both; fix the manifest.")
    for rel in declared:
        if (repo / rel).is_file():
            _classify_orphan(repo, rel, removable, notes)
    return removable, notes


#: The subject every substrate REFRESH commit carries. Named once because the REPAIR
#: verb (WI-0186) identifies its own work by it — a second literal would let the two
#: drift, and the failure mode of that drift is a repair that declines to fix what it
#: wrote.
SUBSTRATE_COMMIT_SUBJECT = (
    "chore(substrate): refresh generated canon/standard/harness/settings from federation")
#: The subject a RETIREMENT commit carries (WI-0341). Deliberately a SECOND subject and
#: a second commit rather than folding a deletion into the refresh: a removal is a
#: different kind of change from a refresh, it wants to be revertible on its own, and
#: labelling a deletion "refresh generated ... from federation" would make a member's
#: log actively misleading about the one operation most worth being able to read back.
SUBSTRATE_RETIRE_SUBJECT = (
    "chore(substrate): remove generated substrate the federation has retired")
#: Every subject this tool writes. `--repair` proves a commit is ours by this set, so a
#: write verb added without its subject here yields a repair that refuses to replay what
#: this very tool committed — the drift the single-constant note above exists to prevent.
SUBSTRATE_SUBJECTS = frozenset({SUBSTRATE_COMMIT_SUBJECT, SUBSTRATE_RETIRE_SUBJECT})


def is_substrate_path(rel):
    """Is this repo-relative path one the federation writes into a member?

    Named once and used by BOTH the repair's ownership proof and the push's
    stale-checkout guard, which each built the same set inline. A RETIRED path counts,
    and so does ANY `sessionlib/*.py`: the package is federation substrate wholesale,
    so a module the federation no longer carries is still a substrate path — which is
    exactly what a retirement commit touches, and exactly what a manifest built only
    from `BYTE_IDENTICAL` would call foreign.
    """
    rel = str(rel).strip()
    if rel in set(BYTE_IDENTICAL) | {SETTINGS_PATH} | set(RETIRED):
        return True
    p = pathlib.PurePosixPath(rel)
    return (len(p.parts) == 2 and p.parts[0] == SESSIONLIB_DIRNAME
            and p.suffix == ".py")


def _local_only_commits(repo, upstream):
    """[(sha, subject)] on the current branch but not on `upstream`, newest first."""
    ok, out = run_git(repo, ["log", "--format=%H%x1f%s", f"{upstream}..HEAD"])
    if not ok:
        return None
    rows = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subj = line.split("\x1f", 1)
            rows.append((sha.strip(), subj.strip()))
    return rows


def _commit_is_ours(repo, sha):
    """Is this commit the federation's own substrate write, by BOTH tests?

    Subject alone is not enough — anyone can type that subject — and touched-paths
    alone is not enough either, because a member editing its own `session.py` (a policy
    violation, but the kind that happens) would look identical. Requiring both is what
    lets the repair say "this is mine to replay" rather than "this looks like mine".
    Returns (ok, reason-if-not).
    """
    ok, subj = run_git(repo, ["log", "-1", "--format=%s", sha])
    if not ok:
        return False, f"could not read commit {sha[:8]}"
    if subj.strip() not in SUBSTRATE_SUBJECTS:
        return False, f"{sha[:8]} is not a substrate commit: {subj.strip()!r}"
    ok, files = run_git(repo, ["show", "--name-only", "--format=", sha])
    if not ok:
        return False, f"could not read the file list of {sha[:8]}"
    touched = [f.strip() for f in files.splitlines() if f.strip()]
    foreign = [f for f in touched if not is_substrate_path(f)]
    if foreign:
        return False, (f"{sha[:8]} carries the substrate subject but touches "
                       f"non-substrate path(s): {', '.join(foreign)}")
    return True, ""


def repair_target(repo, dry_run):
    """Replay this member's unpushed substrate commit(s) onto its fetched remote tip.

    WHY THIS IS A VERB AND NOT A HAND REBASE (WI-0186). An earlier push committed
    into members whose local trunk was already behind their remote — nothing in
    the tool compared a target's local trunk to its REMOTE, so it wrote onto a stale
    base and only discovered it when the push was rejected. The result is a member that
    is diverged, which fails the `git pull --ff-only` its own session-start ritual runs.
    Fixing that by hand ends in "and then someone rebases three repositories," and the
    next occurrence starts the errand over.

    THE BOUNDARY THIS STAYS INSIDE. The federation may write and commit its OWN generated
    substrate in a member (refines ADR-0027) and may never author a member's own files.
    Replaying a commit this tool made, onto a tip this tool did not move, is the first
    thing — the federation finishing a delivery it started. So the verb REFUSES the moment
    it finds a local commit it cannot prove is its own, and refusing is cheap: the state
    it declines to touch is exactly the state it found.

    Never forces, never stashes, never resolves a conflict. A dirty tree, a foreign
    commit, or a conflicting replay all end the same way — report and leave alone.
    """
    name = repo.name
    lines = [f"  {name}:"]

    ok, out = run_git(repo, ["fetch", "origin"])
    if not ok:
        lines.append(f"    SKIPPED — git fetch failed: {out}")
        return lines

    ok, branch = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not ok or branch.strip() == "HEAD":
        lines.append("    SKIPPED — detached HEAD; no branch to replay.")
        return lines
    branch = branch.strip()
    ok, upstream = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                  f"{branch}@{{u}}"])
    if not ok:
        lines.append(f"    SKIPPED — {branch} has no upstream configured.")
        return lines
    upstream = upstream.strip()

    ok, counts = run_git(repo, ["rev-list", "--left-right", "--count",
                                f"HEAD...{upstream}"])
    if not ok or len(counts.split()) != 2:
        lines.append(f"    SKIPPED — could not count against {upstream}: {counts}")
        return lines
    ahead, behind = (int(x) for x in counts.split())
    if ahead == 0:
        lines.append(f"    nothing to replay — {branch} is not ahead of {upstream}"
                     + (f" ({behind} behind)" if behind else ""))
        return lines

    commits = _local_only_commits(repo, upstream)
    if commits is None:
        lines.append("    SKIPPED — could not list local-only commits.")
        return lines
    for sha, _subj in commits:
        mine, why = _commit_is_ours(repo, sha)
        if not mine:
            lines.append(f"    REFUSED — {why}")
            lines.append("    This is the member's own history, not ours to replay. "
                         "Leave it to a session in that repo.")
            return lines

    # A rebase on a dirty tree either fails or silently autostashes depending on config,
    # and "silently autostashes someone's uncommitted work" is not a thing this may do.
    ok, dirty = run_git(repo, ["status", "--porcelain"])
    if not ok:
        lines.append(f"    SKIPPED — git status failed: {dirty}")
        return lines
    if dirty.strip():
        lines.append("    SKIPPED — working tree is dirty; a replay must not stash "
                     "someone else's uncommitted work:")
        lines.extend(f"      {l}" for l in
                     evidence.clip(dirty, 10, keep="head", unit="lines").splitlines())
        return lines

    plural = "" if len(commits) == 1 else "s"
    lines.append(f"    {len(commits)} substrate commit{plural} to replay onto "
                 f"{upstream} ({behind} behind)")
    if dry_run:
        lines.append("    (plan — nothing written)")
        return lines

    ok, out = run_git(repo, ["rebase", upstream])
    if not ok:
        run_git(repo, ["rebase", "--abort"])
        lines.append(f"    REFUSED — replay did not apply cleanly; rebase aborted and "
                     f"the repo is exactly as it was found:")
        lines.extend(f"      {l}" for l in
                     evidence.clip(out, 8, keep="head", unit="lines").splitlines())
        return lines
    lines.append(f"    replayed onto {upstream}")

    ok, out = run_git(repo, ["push"])
    if not ok:
        lines.append(f"    git push FAILED: {out}")
        return lines
    lines.append("    pushed")
    return lines


def target_base_is_current(repo):
    """FRESHNESS PRECONDITION (WI-0213): may we write into this target at all?

    Returns `(ok, note)` — `note` is a report line when the answer is no, and may also
    be set on a yes to name an assumption the caller should not have to infer.

    THE GAP THIS CLOSES. This tool had two integrity gates and neither asked whether the
    TARGET was current. `source_committed()` checks the FEDERATION's tree against its own
    trunk; the ADR-0060 per-target guard checks the target's tree against the TARGET'S OWN
    LOCAL trunk — which a stale clone satisfies perfectly, because a clone that has not
    fetched in weeks is entirely self-consistent. Nothing compared a target's local trunk
    to its REMOTE. Once that wrote and committed into several members and three
    were rejected at push time — by which point
    the commit existed and the divergence had been CREATED rather than avoided, and each
    of the three would next fail the `git pull --ff-only` its own session-start ritual
    runs. `--repair` makes that recoverable; only this makes it not happen.

    REFUSING TO WRITE IS STRICTLY BETTER THAN WRITING AND FAILING TO PUSH, which is why
    the bar is `behind > 0` rather than `diverged`. A target merely BEHIND is not diverged
    yet — committing onto it is what diverges it. Ahead-but-not-behind is fine and is not
    refused: those are unpushed local commits and the push fast-forwards over them.

    IT FETCHES, AND IN A PLAN TOO. A read-only answer cannot serve a writer — the cached
    ref is exactly as stale as the checkout, which is why `reconcile.py` reported one of them
    `ok` just before the incident. The fetch is skipped when a successful one is
    already on record within the freshness TTL, so a fleet-wide run costs one round trip
    per member. A plan pays the same cost on purpose: a plan that predicts a write the
    real run will refuse is a preview of a different operation ([`declare-what-a-check-
    assumes`]), and a fetch mutates only remote-tracking refs — it is not a write to the
    member.
    """
    d = git_divergence(repo, fetch=True)
    if d["upstream"] is None:
        # NOT a refusal. A checkout with no tracking ref has no remote to be behind and
        # no push to be rejected, so nothing can strand — but it is a different answer
        # from "checked and current" and is said rather than folded into silence.
        return True, f"    note: no upstream tracking ref — freshness NOT CHECKED ({d['reason']})"
    if d["fetch_state"] == "failed":
        return False, (f"    SKIPPED — could not reach {d['upstream']} to check whether this "
                       f"checkout is current. Writing against a base we cannot verify is "
                       f"the WI-0213 defect, and a push would be rejected anyway.")
    if not d["readable"]:
        return False, (f"    SKIPPED — cannot compare this checkout to {d['upstream']}: "
                       f"{d['reason']}.")
    if d["behind"] > 0:
        state = ("DIVERGED" if d["diverged"] else "BEHIND")
        return False, (f"    SKIPPED — {state} from {d['upstream']} "
                       f"({d['ahead']} ahead / {d['behind']} behind). Committing onto a "
                       f"stale base is what creates the divergence; the push would be "
                       f"rejected and the commit would strand. Bring this member up to "
                       f"date (or run --repair if it already carries an unpushed "
                       f"substrate commit), then re-run.")
    return True, ""


def push_to_repo(repo, dry_run, retire=False):
    """Refresh + commit + push the differing substrate files in one repo, and report
    (optionally remove) substrate the federation has RETIRED that it still carries.

    Returns a list of report lines for this repo.

    The two halves are separate commits on purpose — see SUBSTRATE_RETIRE_SUBJECT — and
    the retirement half REPORTS on every run while REMOVING only under `retire`
    (WI-0341). Removal is opt-in because the asymmetry is stark: an unremoved retired
    file is inert dead weight (`sessionlib/__init__.py`'s PARTS list ships too, so an
    orphaned module is never imported), while a deletion path running unattended across
    every member is a live, fleet-wide, hard-to-undo write ([`cap-what-can-run-away`]).
    """
    name = repo.name
    rts = repo_runtimes(repo)
    if CLAUDE_RUNTIME not in rts:
        return [f"  {name}: SKIPPED — no Claude-Code runtime declared ({', '.join(rts)}); "
                f"governance substrate serviced at retrofit, not by the fleet push "
                f"(ADR-0041 §5)"]
    # A system that declares claude-code AMONG co-equal runtimes (e.g.
    # [claude-code, <another runtime>]) IS serviced — its Claude sessions are
    # first-class and need the shared substrate both runtimes' spine depends on. The push
    # only ever writes shared substrate (session.py / CANON / STANDARD / settings), never a
    # runtime-native file, so servicing a multi-runtime repo cannot clobber its native side.
    # Before anything is read for diffing or written: is this target's trunk current
    # with its own remote? Both halves below commit, so the gate belongs here rather
    # than inside either one.
    base_ok, base_note = target_base_is_current(repo)
    if not base_ok:
        return [f"  {name}: stale base", base_note]

    changed = diff_files(repo)
    orphans, orphan_notes = orphan_files(repo)

    lines, refreshed = _refresh_lines(repo, name, changed, dry_run)
    if base_note and lines:
        lines.append(base_note)
    retire_lines, removed = _retire_lines(repo, name, orphans, orphan_notes,
                                          dry_run, retire)
    lines += retire_lines
    if not lines:
        return [f"  {name}: up to date — no change"]
    # ONE marker per repo, written after both halves, because two calls would clobber
    # each other: `write_push_marker` replaces the file, so a retirement following a
    # refresh would erase the refresh notice the member has not read yet.
    if refreshed or removed:
        write_push_marker(repo, refreshed, retired=removed)
    return lines


def _refresh_lines(repo, name, changed, dry_run):
    """The refresh half. Returns (report_lines, rels_actually_committed); `[]` lines
    when there is nothing to refresh, so the caller can tell an idle repo from a busy
    one without parsing text."""
    if not changed:
        return [], []

    labels = [label for label, _rel, _want in changed]
    paths = [rel for _label, rel, _want in changed]
    lines = [f"  {name}: refresh {', '.join(labels)}"]

    # A target with uncommitted local edits to a substrate path gets skipped,
    # not clobbered — an unconditional overwrite would destroy those edits
    # before any git failure could surface them. Same philosophy as
    # reconcile.py: surface, don't guess. (Local substrate edits are already a
    # policy violation — the federation is single-writer — but the violation is
    # for the Architects to resolve, not for this script to erase.)
    ok, dirty = run_git(repo, ["status", "--porcelain", "--", *paths])
    if not ok:
        lines.append(f"    SKIPPED — git status failed: {dirty}")
        return lines, []
    if dirty.strip():
        lines.append("    SKIPPED — uncommitted local changes on substrate path(s):")
        lines.extend(f"      {l}" for l in dirty.splitlines())
        lines.append("    Commit or discard in the target, then re-run.")
        return lines, []

    # STALE-CHECKOUT GUARD (a member's defect report, 2026-08-13). A lane land
    # advances the target's main REF without touching the main checkout's index
    # or working tree, so the index still holds the pre-land tree — which git
    # sees as staged reverts/deletes of everything the landed session shipped.
    # The scoped status check above never looks there, and a bare `git commit`
    # commits the WHOLE index: that combination once shipped a stale
    # tree into a member as `chore(substrate)`, reverting landed
    # product code (and hit other members the same
    # way). Anything staged outside the substrate manifest means this checkout
    # is not a tree we may commit from — skip and NAME the state, per
    # `declare-what-a-check-assumes`.
    ok, staged = run_git(repo, ["diff", "--cached", "--name-only"])
    if not ok:
        lines.append(f"    SKIPPED — git diff --cached failed: {staged}")
        return lines, []
    foreign = [p for p in staged.splitlines()
               if p.strip() and p not in set(paths) and not is_substrate_path(p)]
    if foreign:
        lines.append("    SKIPPED — stale checkout: index diverges from HEAD outside "
                     "substrate (a lane land advanced the ref under this checkout, or "
                     "foreign work is staged):")
        lines.extend(f"      {p}" for p in foreign)
        lines.append("    Open a session in that repo (or resync its main checkout), "
                     "then re-run.")
        return lines, []

    if dry_run:
        lines.append("    (dry-run — nothing written)")
        return lines, []

    # Overwrite each differing file with what the target should carry
    # (byte-identical federation copy, or per-target generated settings.json).
    for _label, rel, want in changed:
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(want)
        if rel in EXECUTABLE:
            dst.chmod(dst.stat().st_mode | 0o111)

    # Stage ONLY the changed substrate paths — never add -A / add .
    ok, out = run_git(repo, ["add", *paths])
    if not ok:
        lines.append(f"    git add FAILED: {out}")
        return lines, []

    # Pathspec'd commit: commits ONLY the named paths, whatever else the index
    # holds. Even if the stale-checkout guard above ever misses, a divergent
    # index entry cannot ride this commit — the sweep class is closed twice.
    ok, out = run_git(repo, ["commit", "-m", SUBSTRATE_COMMIT_SUBJECT, "--", *paths])
    if not ok:
        lines.append(f"    git commit FAILED: {out}")
        return lines, []
    lines.append("    committed")

    ok, out = run_git(repo, ["push"])
    if not ok:
        lines.append(f"    git push FAILED: {out}")
        return lines, paths
    lines.append("    pushed")
    return lines, paths


def _retire_lines(repo, name, orphans, notes, dry_run, retire):
    """The retirement half. Returns (report_lines, rels_actually_removed).

    REPORTS unconditionally — reporting is a read, it costs nothing, and a fleet that
    cannot see its own immortal files is the defect (WI-0341). REMOVES only when
    `retire` is set AND this is a real run.
    """
    if not orphans and not notes:
        return [], []
    lines = [f"  {name}: retired substrate still present"]
    lines.extend(f"    {n}" for n in notes)
    lines.extend(f"    {rel} — the federation no longer ships this file" for rel in orphans)
    if not orphans:
        return lines, []
    if not retire:
        lines.append("    NOT removed — removal is opt-in. Re-run with --retire-orphans "
                     "(and --go) to remove these.")
        return lines, []

    # The same courtesy the refresh half extends, for the same reason: a deletion must
    # not carry off someone's unsaved edits to the file it is deleting. `git rm` would
    # refuse a modified file anyway, but refusing with a NAMED state beats refusing with
    # a git error ([`declare-what-a-check-assumes`]).
    ok, dirty = run_git(repo, ["status", "--porcelain", "--", *orphans])
    if not ok:
        lines.append(f"    SKIPPED removal — git status failed: {dirty}")
        return lines, []
    if dirty.strip():
        lines.append("    SKIPPED removal — uncommitted local changes on a retired path:")
        lines.extend(f"      {l}" for l in dirty.splitlines())
        return lines, []

    if dry_run:
        lines.append("    (plan — nothing removed)")
        return lines, []

    ok, out = run_git(repo, ["rm", "--quiet", "--", *orphans])
    if not ok:
        lines.append(f"    git rm FAILED: {out}")
        return lines, []
    # Pathspec'd like the refresh commit, and for the same reason — whatever else this
    # checkout's index holds cannot ride a commit scoped to the paths we removed.
    ok, out = run_git(repo, ["commit", "-m", SUBSTRATE_RETIRE_SUBJECT, "--", *orphans])
    if not ok:
        lines.append(f"    git commit FAILED: {out}")
        return lines, []
    lines.append(f"    removed and committed {len(orphans)} retired file(s)")

    ok, out = run_git(repo, ["push"])
    if not ok:
        lines.append(f"    git push FAILED: {out}")
        return lines, orphans
    lines.append("    pushed")
    return lines, orphans


#: Where a serviced member learns its substrate moved. Inside the gitignored
#: `.session-state/` sidecar deliberately: this is a NOTICE, not substrate, and it must
#: not appear in the member's `git status` or ride any commit — a marker that dirtied a
#: target's tree would be a worse version of the tree-noise it exists to replace.
PUSH_MARKER_REL = pathlib.Path(".session-state") / "substrate-push.json"


def write_push_marker(repo, rels, now=None, retired=()):
    """Leave a one-shot notice that this member's substrate was just refreshed (WI-0131).

    Asked for by that same member's Architect, who answered our own open question with *"yes, but low
    priority — file it, don't fast-track it."* Their reasoning is the reason it is worth
    building at all and worth building SMALL: the shrinking-render guard already covers
    the HARM (a repeat of their settings-strip incident now fails at the write point
    rather than landing), so what this adds is purely DISCOVERY — a member learning its
    substrate moved from a banner rather than by noticing unexplained tree noise, which
    is how that member found the strip in the first place.

    One-shot by construction: `session.py start` prints it and the first heartbeat stamps
    `announced`, so a member is told once per push rather than nagged every session. A
    session that never acts never stamps, and re-announces — which is correct, because it
    never delivered the notice to anyone.

    Fail-open and never raises. The push has already committed by the time this runs; a
    courtesy notice must never be able to turn a completed refresh into a failure.

    RETIRED PATHS GET THEIR OWN KEY, not a slot in `files` (WI-0341). The member-side
    reader (`sessionlib/land.py::_substrate_push_line`) renders `files` as *"substrate:
    refreshed … — <names>"*, so a deleted file listed there would tell the member its
    substrate was refreshed with a file that is gone — worse than saying nothing. An
    extra key is inert to that reader (it takes only `files`/`at`/`announced`), so this
    records the fact at the member without a fleet-wide change to the banner; teaching
    the banner to render it is a separate substrate rollout."""
    try:
        p = repo / PUSH_MARKER_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = now or datetime.datetime.now().astimezone()
        rec = {
            "at": stamp.isoformat(timespec="seconds"),
            "files": sorted(str(r) for r in rels),
            "by": "federation push-substrate",
            "announced": False,
        }
        if retired:
            rec["retired"] = sorted(str(r) for r in retired)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    except Exception:
        pass


def tests_green():
    """Run the federation test suite; return (ok, output).

    Structural precondition for pushing (add-structural-guard-on-recurrence /
    P15): this script ships session.py byte-identical to every converged repo,
    check-bash fails open, so a parser regression propagates federation-wide
    silently. stdlib unittest, not pytest — the gate must not depend on a
    package that may not be installed on every federation machine.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=FED_ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def source_committed():
    """Refuse to ship substrate the trunk does not carry — return (ok, detail).

    The SOURCE-INTEGRITY gate (session 90, `add-structural-guard-on-recurrence` /
    P15). `tests_green` proves the harness in FED_ROOT's WORKING TREE is sound;
    it says nothing about whether that working tree is the trunk. Two ways they
    diverge, both observed:

    - **Stale checkout after a lane land** (ADR-0060). A `poga` lane advances
      `refs/heads/main` by CAS with no checkout, so the main tree keeps the
      pre-advance content while `main` moves ahead. A push from that tree ships
      the OLD `session.py` fleet-wide as byte-identical substrate — and reports
      success, because the old harness is perfectly green against the old tests.
      This is the session-89 near-miss: the 03:15 adopt-runner fires with
      `WorkingDirectory` = the main checkout, and the checkout sat 5 commits
      behind the trunk for ~4 hours.
    - **Uncommitted local edits.** A half-finished harness change in the working
      tree ships to every member before it is committed, reviewed, or landed.

    Both are the same defect — the pushed bytes are not the bytes the trunk
    blessed — so one gate closes both. `git diff --quiet <trunk> -- <files>`
    compares the working tree against the trunk's blob, catching the stale
    checkout (tree behind ref) and the dirty tree (tree ahead of ref) alike.

    FAIL-CLOSED, unlike most guards here: a push is a fleet-wide irreversible
    write, so an *undeterminable* comparison must block rather than proceed.
    A guard that fails open on `git` breaking is a guard that isn't there on
    exactly the day it matters.
    """
    trunk = "main"
    if _session is not None:
        try:
            trunk = _session.CFG.get("trunk", "main")
        except Exception:
            trunk = "main"

    ok, out = run_git(FED_ROOT, ["rev-parse", "--verify", f"refs/heads/{trunk}"])
    if not ok:
        return False, (f"cannot resolve refs/heads/{trunk} in {FED_ROOT} — "
                       f"refusing to push substrate of undeterminable provenance ({out})")

    proc = subprocess.run(
        ["git", "-C", str(FED_ROOT), "diff", "--name-only", trunk, "--", *BYTE_IDENTICAL],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return False, (f"`git diff {trunk}` failed in {FED_ROOT} — refusing to push "
                       f"substrate of undeterminable provenance ({(proc.stdout + proc.stderr).strip()})")

    drifted = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if drifted:
        # WI-0158 / ADR-0099 D4. The remedy used to read *"sync it: `git -C {FED_ROOT}
        # reset --hard {trunk}` (verify no local edits first)"* — a command its own reader
        # is refused twice over: check-bash denies `reset --hard` under P9
        # confirm-destructive-ops, and a lane reader's worktree-isolation guard denies the
        # `git -C <other tree>` form as well. A refusal whose remedy is guard-denied is not
        # advice; it is an escalation, and the escalation target is always operator (this exact
        # string is WI-0085's specimen, named in the 2026-08-19 consultant brief).
        #
        # `main-restore` (WI-0097 / ADR-0098) is the verb for the stale half of this
        # condition — a checkout left behind by a lane land — and it swallows the "verify
        # no local edits first" instruction rather than passing it to a human: it restores
        # only what it can prove the trunk already superseded and names everything it KEPT.
        # The other half of the sentence was already right and is unchanged. A real local
        # edit has to be landed; no sync substitutes for that, and saying so is what keeps
        # this from reading as "reset until the gate goes quiet".
        return False, (
            f"working tree differs from refs/heads/{trunk} on: {', '.join(drifted)}. "
            f"The bytes here are not the bytes the trunk carries, so pushing would "
            f"ship unblessed substrate fleet-wide. If this checkout is stale after a "
            f"lane land, `python3 session.py main-restore --dry-run` names which of "
            f"these the trunk has already superseded and repairs exactly those without "
            f"--dry-run. Anything it KEEPS is a real local edit, and a local edit has to "
            f"be landed before a push can carry it."
        )
    return True, f"working tree matches refs/heads/{trunk} on all {len(BYTE_IDENTICAL)} byte-identical file(s)"


class RosterUnreadableHere(Exception):
    """The delivery reconciliation could not read its own denominator."""


def delivery_reconciliation(repos, roots):
    """Which roster members this run did NOT service, split by WHY.

    THE DEFECT THIS EXISTS FOR (a member's own report, delivered 2026-09-21). This
    tool's only notion of "the fleet" is `find_converged_repos` — a walk of THIS
    machine's disk. A machine-role ruling moved development to another machine,
    and the resident-repo's roots are deliberately absent from that machine's walk roots, so
    a member stopped being a candidate. Not skipped, not refused, not reported:
    never considered. The push then ran and serviced the members it found while
    saying nothing whatsoever about the ones it could not see, and the gap ran for weeks
    before a member's own checksum run found it and told us by hand.

    The three per-target gates inside `push_to_repo` all report by name, because they act
    on a repo that was FOUND. A member that was never enumerated has nothing to report
    against, which is exactly why the absence has to be computed from a denominator the
    walk does not supply (`declare-what-a-check-assumes`; the same argument WI-0098 made
    for the rollout gate, applied to the delivery itself rather than to the detector).

    SPLIT, NEVER POOLED. "Resides on another machine" and "expected here and missing" are
    different facts with different repairs, and pooling them is how the first one launders
    the second — `curate/reconcile.py` already prints such a member as "elsewhere by
    design", which is true about locatability and false about delivery. This says the
    delivery half out loud.

    Returns `{"serviced": [...], "elsewhere": [(sid, machine), ...], "gap": [...]}`.
    Raises `RosterUnreadableHere` rather than reporting a clean run over a denominator it
    could not read — a reconciliation that fails open is the silence it was built to end.
    """
    try:
        import standard_version as sv
        import reconcile
    except Exception as exc:  # pragma: no cover - import failure is environmental
        raise RosterUnreadableHere(f"cannot load the roster tools: {exc}") from exc

    try:
        # REUSE THE OTHER LOCATOR WHOLESALE rather than re-deriving identity here. This
        # tool finds repos by CONVERGENCE_MARKERS; `find_members` keys on each member's
        # own `architect_id`. Those answer different questions ("is it running our
        # substrate" vs "what is it called"), and a second copy of the naming rule would
        # be free to drift from the roster this reconciles against (P16).
        located = sv.find_members(roots)
        located.update(sv.resolve_mapped(read_repo_paths_config(REPO_PATHS_CONFIG)))
        serviced_paths = {p.resolve() for p in repos}
        serviced = {sid for sid, (repo, _cfg) in located.items()
                    if pathlib.Path(repo).resolve() in serviced_paths}
        cov = sv.coverage(serviced)
        residency = reconcile.roster_residency()
    except Exception as exc:
        raise RosterUnreadableHere(str(exc)) from exc

    machine = this_machine()
    elsewhere, gap = [], []
    for sid in cov["unlocated"]:
        where = declared_elsewhere({"resides": residency.get(sid)}, machine)
        (elsewhere.append((sid, where)) if where else gap.append(sid))
    return {"serviced": sorted(cov["expected"] & cov["located"]),
            "expected": sorted(cov["expected"]),
            "elsewhere": elsewhere, "gap": gap}


def print_delivery_reconciliation(repos, roots, dry_run=False):
    """Print the reconciliation and return the exit code it earns.

    A declared-elsewhere absence is 0 — it is the fleet arrangement working, and this
    machine genuinely cannot write there. An UNDECLARED expected member that was not
    serviced is non-zero, matching the ruling `standard_version.fleet_complete` already
    applies to its own surface: a member does not leave the fleet by being unreachable.

    `dry_run` changes the VERB only, never the arithmetic or the exit code. A plan that
    reported what it "serviced" would be claiming a delivery it did not make, and the
    whole point of this surface is that it is the honest record of who was reached.
    """
    try:
        rec = delivery_reconciliation(repos, roots)
    except RosterUnreadableHere as exc:
        # Deliberately NOT fail-open, unlike the rollout gate one function down. That one
        # verifies a claim on top of a delivery that already happened; this one IS the
        # record of who was left out, and a clean-looking run over an unread denominator
        # is the precise failure it was written to prevent.
        print(f"\nDelivery reconciliation: REFUSED — cannot read the roster ({exc}). "
              f"Not reporting a complete delivery over a denominator this run could not "
              f"read.", file=sys.stderr)
        return 2

    verb = "would service" if dry_run else "serviced"
    print(f"\nDelivery reconciliation: {verb} {len(rec['serviced'])} of "
          f"{len(rec['expected'])} expected member(s).")
    if rec["elsewhere"]:
        named = ", ".join(f"{sid} ({where})" for sid, where in rec["elsewhere"])
        print(f"  NOT SERVICED — resides elsewhere: {named}.")
        print("  This tool writes into a checkout it can open locally, so these cannot "
              "be serviced from here at all. Run the push on that machine, or give the "
              "member a clone under a walk root on this one — delivery travels by "
              "`origin`, so either reaches it.")
    if rec["gap"]:
        print(f"  NOT SERVICED — UNLOCATED, and that is a gap: {', '.join(rec['gap'])}.")
        print("  Expected here by the roster and not found. Undeclared absence is a "
              "defect, not an arrangement.")
    if not rec["elsewhere"] and not rec["gap"]:
        print("  Every expected member was serviced.")
    return 1 if rec["gap"] else 0


def print_rollout_gate(roots):
    """After refreshing substrate, VERIFY the fleet actually DETECTS at the latest
    standard-version — the rollout-complete gate (ADR-0047). Moving the bytes into
    place is not the same as the rollout being complete; the detector is the
    authority. Reuses standard_version.fleet_complete so there is one definition of
    'complete' (P16). This never SELF-DECLARES complete — it prints the detector's
    verdict — and fails open (a gate-check hiccup must not mask a push that worked).
    An unreachable member is unverified, NOT passed (ADR-0047)."""
    try:
        import standard_version as sv
        repo_paths = read_repo_paths_config(REPO_PATHS_CONFIG)
        complete, blockers, n, cov = sv.fleet_complete(roots, repo_paths)
    except Exception as exc:
        print(f"\nRollout gate: could not verify fleet completeness ({exc}). "
              f"Run `python3 curate/standard_version.py --assert-complete` to check.")
        return
    if n == 0:
        print("\nRollout gate: no members located to verify — completeness unknown.")
        return
    if complete:
        print(f"\nRollout gate: COMPLETE — {sv.coverage_line(cov)} All detect at v{sv.LATEST}.")
        return
    # Coverage and detector failures print separately (WI-0098): "a member is missing"
    # and "a member is broken" send you to different places, and the line this replaced
    # could report neither while still saying COMPLETE.
    bparts, cparts = sv._blocker_parts(blockers), sv.coverage_parts(cov)
    if bparts:
        print(f"\nRollout gate: INCOMPLETE — {'; '.join(bparts)}. "
              f"NOT declaring v{sv.LATEST} fleet-complete. "
              f"Hard gate: `python3 curate/standard_version.py --assert-complete`.")
    if cparts:
        print(f"\nRollout gate: COVERAGE GAP — {sv.coverage_line(cov)} "
              f"An expected member unreachable from here is unverified, NOT passed — it "
              f"does not leave the fleet by being unreachable.")


def build_parser():
    """The interface, as an object that can be asked what it accepts (WI-0178).

    There was no parser here. `dry_run` was `"--dry-run" in args` and every other token
    not starting with `-` was taken as a search root, so an unrecognised flag was
    SILENTLY DISCARDED and the run proceeded as a real push. Two consequences, both
    measured on 2026-08-30: `--help` armed a nine-repo write and was saved only by
    `tests_green()` running the whole suite first, and a typo'd `--dry-runn` would have
    been a live push. A tool whose own docstring says it writes to every member it finds
    must be askable what it does without doing it.

    The default is now PLAN, and `--go` executes — the shape `poga dispatch` already uses
    for the substrate's other fan-out that mutates. Opt-in safety on a fleet-wide writer
    is backwards: the cost of an accidental plan is nothing, and the cost of an accidental
    push is nine repositories' histories ([`cap-what-can-run-away`], P9)."""
    p = argparse.ArgumentParser(
        prog="push-substrate.py",
        description="Write the federation's own generated substrate into every reachable "
                    "converged Architect repo. PLANS BY DEFAULT — pass --go to execute.",
        epilog="Roots default to the gitignored machine-local `reconcile-roots.local`.")
    p.add_argument("roots", nargs="*", metavar="ROOT",
                   help="Search roots, overriding reconcile-roots.local. For a machine "
                        "whose view of the shared disk differs from that file's.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--go", action="store_true",
                   help="Actually write and commit. Without this nothing is written.")
    g.add_argument("--dry-run", "-n", action="store_true",
                   help="Plan only. This is the default; the flag is kept so existing "
                        "invocations and docs keep meaning what they said.")
    p.add_argument("--repair", action="store_true",
                   help="Do not push new substrate. Instead replay any UNPUSHED "
                        "substrate commit this tool already made onto the member's "
                        "fetched remote tip, and push it (WI-0186). Refuses on any "
                        "commit it cannot prove is the federation's own.")
    p.add_argument("--retire-orphans", action="store_true",
                   help="Also REMOVE substrate a target still carries that the "
                        "federation has RETIRED (WI-0341). Retired files are REPORTED "
                        "on every run regardless of this flag; only the removal is "
                        "gated. Off by default and deliberately — a deletion path "
                        "running unattended across every member is a worse defect than "
                        "the inert stale file it removes.")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    arg_roots = args.roots
    dry_run = not args.go

    # REFUSED, not silently ignored. A repair replays commits already made and writes no
    # new bytes, so `--retire-orphans` could not do anything here — and a flag that is
    # accepted and does nothing is exactly the defect WI-0178 closed in this parser,
    # where an unrecognised flag was swallowed and the run proceeded as something other
    # than what was asked for.
    if args.repair and args.retire_orphans:
        parser.error("--retire-orphans has no meaning with --repair: a repair replays "
                     "substrate commits already made and introduces no new bytes. Run "
                     "the push instead.")

    # The suite is the gate on WRITING, so a plan does not pay for it — and saying so is
    # the point. A plan that silently skipped a gate the real run enforces would be a
    # preview of a different operation than the one --go performs
    # ([`declare-what-a-check-assumes`]).
    if dry_run:
        print("PLAN ONLY — nothing will be written. Re-run with --go to execute.")
        print("gates: NOT RUN in plan mode. --go runs the full suite and the "
              "source-integrity check first, and refuses on either.")
    elif args.repair:
        # The repair ships NO new bytes — it re-parents commits this tool already made,
        # each of which passed the suite when it was made. Re-running it would answer a
        # question already answered, so it is skipped and SAID, rather than skipped
        # quietly ([`declare-what-a-check-assumes`]).
        print("gates: suite NOT RUN — a repair replays existing substrate commits and "
              "introduces no new bytes. Every commit is still proved to be ours before "
              "it is touched, and any conflict aborts.")
    else:
        ok, test_out = tests_green()
        if not ok:
            print("ERROR: the federation test suite is not green — refusing to push "
                  "substrate (a session.py regression would propagate to every "
                  "converged repo).", file=sys.stderr)
            print(test_out, file=sys.stderr)
            return 2
        print("tests: green (python3 -m unittest discover -s tests)")

        # Green tests prove the harness in this tree WORKS; this proves it is the
        # harness the trunk blessed (ADR-0060 stale-checkout / uncommitted-edit).
        ok, src_detail = source_committed()
        if not ok:
            print(f"ERROR: source-integrity gate — {src_detail}", file=sys.stderr)
            return 2
        print(f"source: {src_detail}")

    roots = arg_roots or read_roots_config()
    if not roots:
        print("ERROR: no roots — pass search roots as arguments, or create "
              "`reconcile-roots.local` (one per line, P3).", file=sys.stderr)
        return 2
    # WI-0132: roots DECLARED but none existing is not the same as none declared, and it
    # must not be allowed to look like a clean run over zero members. This tool WRITES to
    # every member it finds, so finding none is the one outcome that reports success
    # while doing nothing — refuse instead, naming the likely cause.
    note = roots_diagnosis(roots, "the roots given" if arg_roots else ROOTS_CONFIG)
    if note:
        print(f"ERROR: {note}", file=sys.stderr)
        return 2
    if arg_roots:
        print(f"roots: {len(roots)} passed as arguments (overriding reconcile-roots.local)")

    missing = [f for f in SUBSTRATE_FILES
               if f != SETTINGS_PATH and not (FED_ROOT / f).is_file()]
    if not FLOOR_PATH.is_file():
        missing.append("standard-settings.json (the settings floor)")
    if missing:
        print(f"ERROR: federation is missing its own substrate source(s): "
              f"{', '.join(missing)}", file=sys.stderr)
        return 2

    repos = find_converged_repos(roots)
    mode = "DRY-RUN — " if dry_run else ""
    verb = "Substrate repair" if args.repair else "Substrate push"
    print(f"{mode}{verb} — {len(repos)} converged repo(s) found across roots: "
          f"{', '.join(roots)}")
    if args.repair:
        print("Replaying UNPUSHED substrate commits onto each member's fetched remote "
              "tip. No new substrate is written.\n")
    else:
        print(f"Pushing the federation's own: {', '.join(SUBSTRATE_FILES)}")
        print("Retired substrate a target still carries is REMOVED (--retire-orphans).\n"
              if args.retire_orphans else
              "Retired substrate a target still carries is REPORTED only; pass "
              "--retire-orphans to remove it.\n")

    if not repos:
        # STILL RECONCILE. "Nothing to do" is a claim about the walk, not about the
        # fleet: zero found over a non-empty roster is the loudest possible version of
        # the defect this reconciliation exists for, and it used to exit 0 in silence.
        print("No converged Architect repos reachable from these roots — nothing to do.")
        return print_delivery_reconciliation({}, roots, dry_run=dry_run)

    def _do_push():
        if args.repair:
            work = repair_target
        else:
            def work(repo, dry_run):
                return push_to_repo(repo, dry_run, retire=args.retire_orphans)
        for repo in sorted(repos.values(), key=lambda p: p.name.lower()):
            for line in work(repo, dry_run):
                print(line)
        if args.repair:
            print("\nOnly commits proved to be the federation's own were replayed; "
                  "anything else was left exactly as found. Nothing was forced.")
        else:
            print("\nThe federation is the single-writer of its own generated substrate; "
                  "this refreshed it in place (refines ADR-0027). Only the substrate "
                  "paths were committed — any other dirty state in a target is "
                  "untouched.")
        # WHO DID NOT GET IT, before anything about whether the ones that did now
        # detect. The gate below answers "is the fleet at the latest standard"; this
        # answers "did this run reach every member it was supposed to", and the second
        # question went unasked for 36 days (see `delivery_reconciliation`).
        rc = print_delivery_reconciliation(repos.values(), roots, dry_run=dry_run)
        # Rollout-complete gate (ADR-0047): the push refreshed bytes, but "rollout
        # complete" is a claim the DETECTORS must earn, not this script. Print the
        # detector verdict over the same roots (plus the repo-paths locator map, so
        # members located only through the map are included).
        print_rollout_gate(roots)
        return rc

    # C5 lease (ADR-0051): a real push mutates the shared member repos — a single-writer
    # op two lanes must not run at once. A dry-run writes nothing, so it needs no lease.
    if dry_run or _session is None:
        return _do_push()
    try:
        with _session.lease("push-substrate"):
            return _do_push()
    except _session.LeaseHeld as e:
        print(f"REFUSED: another lane holds the push-substrate lease ({e}). Wait for it "
              f"to finish, or if that lane is dead: "
              f"`python3 session.py unlease push-substrate --force`.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
