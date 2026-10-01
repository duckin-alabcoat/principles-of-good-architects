#!/usr/bin/env python3
"""Standard-version self-check — a member verifies its OWN standard substrate.

SHIPPED substrate (ADR-0047): this file is byte-identical across every Architect,
like session.py — pushed by the federation (curate/push-substrate.py) and seeded by
bootstrap. It runs as a session-start hook in the shared floor, so each member
verifies itself LOCALLY (no network) at every session start, and prints a one-line
signal. It is the ring-1 half of the rollout-reconciliation the federation runs
fleet-wide (curate/standard_version.py), sharing this one manifest as the single
source of truth (P16 — the federation tool imports from here, never duplicates it).

It MUTATES NOTHING and makes NO judgment (P15): it reads the member's own files,
runs the per-capability detectors, and reports. The detectors are the ONLY version
signal — there is no self-reported claim to compare against (ADR-0068 retired the
`standard_version` declaration: a hand-written claim no tool ever wrote or updated,
blank on every established member and already stale on the one that set it).
Fails open — any error prints a benign line and exits 0, never bricking startup.

Runtime-aware (ADR-0041 contract-vs-binding): a capability whose only structural
evidence is a Claude-Code hook binding is N/A — not "missing" — for a member bound
another way (a resident runtime that launches the harness itself, or a non-Claude
runtime whose obligation is met via its own binding this detector cannot see).

CLI:
  python3 standard_check.py --status   # one-line session-start signal (this repo)
  python3 standard_check.py            # full self-report (this repo)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent


# --------------------------------------------------------------------------- detectors
#
# A detector is a pure function (repo: Path, cfg: dict) -> bool, reading only the
# member's own files. Structural (grep / file-exists), never behavioural. Total:
# any read error -> False (absent), never an exception.


def _read(repo, rel):
    try:
        return (repo / rel).read_text(errors="replace")
    except OSError:
        return None


def _exists(repo, rel):
    return (repo / rel).is_file()


def _harness_src(repo):
    """The member's harness SOURCE, as one concatenated string.

    Every capability detector below greps the harness for a marker. For years that
    harness was one file, so the detectors read `session.py` directly. When the code
    moved into the `sessionlib/` package, `session.py` stayed at the root as a thin
    entry point — every one of those greps would have found nothing and reported the
    capability ABSENT, reading the whole fleet as below floor overnight. That is
    `declare-what-a-check-assumes` exactly: a check whose assumption about the shape
    of its subject silently stopped being true, failing in the direction that looks
    like a real regression.

    So the assumption is stated here, in ONE place: the harness is `session.py` PLUS
    every `sessionlib/*.py` module, and a detector greps the whole of it. A member
    that has not yet received the package has no `sessionlib/` and reads exactly as
    before — the concatenation is just `session.py`. Both shapes pass, which is what
    lets the package roll out across the fleet a member at a time.

    Modules are read in sorted order and joined with a newline so a marker scoped to
    one function's own text (`d_residency`, `d_dispatched_close_authorization`) still
    terminates at the next `def`, whichever file it came from.

    Total, like every read here: an unreadable file contributes nothing rather than
    raising, and the result is always a string (never None)."""
    parts = []
    src = _read(repo, "session.py")
    if src is not None:
        parts.append(src)
    # WI-0328: `interpreter.py` is harness source too, and it ships with `session.py`.
    # `tests/harness_fixture.harness_files` must answer the same question this does —
    # the shipped detector and the tests that pin it are not allowed to disagree about
    # what the harness IS, and now a test says so rather than a docstring.
    pin = _read(repo, "interpreter.py")
    if pin is not None:
        parts.append(pin)
    try:
        mods = sorted(pth.name for pth in (repo / "sessionlib").iterdir()
                      if pth.suffix == ".py" and pth.is_file())
    except OSError:
        mods = []
    for name in mods:
        mod = _read(repo, "sessionlib/" + name)
        if mod is not None:
            parts.append(mod)
    return "\n".join(parts)


def _settings(repo, cfg=None):
    """The member's ARCHITECT settings file.

    Default `<repo>/.claude/settings.json` — right for every kit member. But in the
    supported resident-runtime layout the repo-root settings can belong to a different
    agent (the runtime persona's own settings), while the Architect's own settings sit
    OUTSIDE the repo, e.g. at `../architect/.claude/settings.json`. Reading the default there measures the
    wrong file, finds no harness reference, and silently exempts the member from
    every hook-scoped capability — reporting "current" by exemption rather than
    evidence, with each new hook capability compounding the gap (session 98).

    So a member may DECLARE where its Architect settings live. ONE location, read
    through a documented precedence (ADR-0034 / the resident-runtime pattern):

      1. `settings_path`, when it names a path — the file the federation GENERATES
         and pushes. Once a member is on the settings channel this is by definition
         the Architect's settings file, so reading anything else would measure a
         copy the channel does not maintain.
      2. `architect_settings_path` — the read-side-only declaration from session 98,
         for a member not (yet) on the write channel: such a member declares `settings_path:
         false` (never push — the persona's root file is not ours to overwrite) plus this,
         and the two must not disagree about which file is being measured.
      3. `<repo>/.claude/settings.json` — the kit default.

    Relative to the repo root; `..` is permitted precisely because the off-repo case
    is the one this exists for.

    An exemption that defaults to "pass" ages badly, so this makes the binding a
    declaration rather than an inference."""
    cfg = cfg or {}
    rel = cfg.get("settings_path")
    if not (isinstance(rel, str) and rel.strip()):
        # `settings_path` may legitimately be False/absent (opt-out, or a member
        # that predates the channel) — fall through to the read-side declaration.
        rel = cfg.get("architect_settings_path")
    if isinstance(rel, str) and rel.strip():
        try:
            p = (repo / rel.strip()).resolve()
            return p.read_text(errors="replace")
        except OSError:
            # A declared-but-unreadable path is NOT silently downgraded to the
            # default: that would restore the false-pass this field exists to kill.
            # Empty string -> not hook-bound -> the member reads n/a and the
            # federation-side parity view shows it, rather than inventing a verdict.
            return ""
    return _read(repo, ".claude/settings.json") or ""


def d_session_harness(repo, cfg):
    return _exists(repo, "session.py")


def d_session_config(repo, cfg):
    return bool(cfg) and bool(cfg.get("architect_id"))


# The banner every federation-GENERATED substrate file opens with. Five renderings
# exist across `distill.py` and `standardize.py` (federation / architect / reference
# audiences) and they differ in every word EXCEPT this clause — so it is the one
# substring a member can test that does not date the moment a header is reworded.
_GENERATED_MARK = "do not edit by hand."


def _generated(repo, rel, min_sections=2):
    """A member's copy of a federation-GENERATED file, as text — or None.

    WI-0375. `_exists` was the whole of `d_canon`, `d_standard_section` and
    `d_standard_reference`, so a member whose CANON.md was an empty file, a
    half-written push or a hand-authored stub reported the capability PRESENT and the
    member read `ok`. Presence is not the capability: the capability is that the member
    CARRIES the federation's canon and standard section, and the strongest version of
    that a member can verify about ITSELF is that the file is a well-formed instance of
    the artifact it claims to be.

    WHAT THIS CANNOT DO, STATED SO NOBODY READS MORE INTO IT. It cannot detect
    STALENESS. A detector here is `(repo, cfg) -> bool` over the member's OWN files and
    there is no federation reference on the member's disk to compare against — and
    embedding one in this file would not help, because `push-substrate` ships this file
    and `CANON.md` in the same commit, so a three-week-stale member holds a
    three-week-stale expected digest that matches its three-week-stale canon perfectly.
    Currency needs the reference, so it lives where the reference is: the
    substrate-currency axis in `curate/standard_version.py`, which compares every
    byte-identical substrate file against the federation's own copy and names the stale
    ones. The two halves together are the fix; this half alone is not.

    Returns None unless the file is present, carries the generated banner, and has at
    least `min_sections` `##` headings — the shape test that fails a stub and passes
    every generated copy of every vintage, without pinning any heading TEXT. A member
    one release behind carries an older-but-correct file, and a floor detector that
    only accepted the CURRENT wording would read the whole fleet below floor the day a
    heading was renamed."""
    text = _read(repo, rel)
    if not text or _GENERATED_MARK not in text:
        return None
    if len(re.findall(r"^## ", text, re.M)) < min_sections:
        return None
    return text


# `**21 principles · 51 universal habits** (Accepted only).` — written by
# `distill.py:render` into both audiences' copies straight from `len(...)` of what it
# just rendered. That makes it a checksum the member can verify against the body.
_CANON_TALLY = re.compile(r"\*\*(\d+) principles · (\d+) universal habits\*\*")
_CANON_PRINCIPLE = re.compile(r"^- \*\*P\d+ ", re.M)
_CANON_HABIT = re.compile(r"^- \*\*`", re.M)


def d_canon(repo, cfg):
    """CONTENT, not presence (WI-0375). CANON.md is generated and declares its own tally
    in the line above the body, so the member checks the body against it. A truncated
    push, an empty file or a hand-edited digest fails the count; the federation's own
    copy, and every member copy of every vintage, passes it."""
    text = _generated(repo, "CANON.md")
    if text is None:
        return False
    m = _CANON_TALLY.search(text)
    if not m:
        return False
    want_p, want_h = int(m.group(1)), int(m.group(2))
    if want_p < 1 or want_h < 1:
        return False
    return (len(_CANON_PRINCIPLE.findall(text)) == want_p
            and len(_CANON_HABIT.findall(text)) == want_h)


def d_standard_section(repo, cfg):
    """CONTENT, not presence (WI-0375). No self-declared tally to check against, so this
    is the generated-shape test: the banner plus a real sectioned body. Deliberately NOT
    coupled to any heading text nor to the STANDARD-REFERENCE pointer — this is a v1.0.0
    FLOOR capability, and see `_generated` on why a floor detector must not pin the
    current wording."""
    return _generated(repo, "STANDARD.md") is not None


def d_standard_reference(repo, cfg):
    """CONTENT, not presence (WI-0375). This detector's job is "is the pointer
    dangling?" (WI-0208 / ADR-0136) — and a stub at the end of the pointer is a dangling
    pointer that happens to resolve, which is exactly what presence-only could not tell
    apart. Same generated-shape test as its two siblings: fixing two of three members of
    a class and leaving the third is the shape this repo keeps paying for."""
    return _generated(repo, "STANDARD-REFERENCE.md") is not None


def d_handoff(repo, cfg):
    name = (cfg or {}).get("handoff") or "session-handoff.md"
    return _exists(repo, name)


def d_status_surface(repo, cfg):
    return _exists(repo, "STATUS.md")


def d_roadmap(repo, cfg):
    return _exists(repo, "ROADMAP.md")


def d_settings_harness_wired(repo, cfg):
    s = _settings(repo, cfg)
    return "session.py" in s and "start" in s and "check-bash" in s


def d_announce_stamp(repo, cfg):
    return "announce" in _settings(repo, cfg)


def d_rollout_selfcheck(repo, cfg):
    """ADR-0047: the member carries this self-check module (the real capability —
    it can verify itself locally), not merely a declared marker."""
    return _exists(repo, "standard_check.py")


def d_session_journals(repo, cfg):
    """ADR-0051 1b: the member's session.py compiles session-handoff.md from per-session
    journals (the compactor). Evidence is the shipped substrate — `run_compile` in
    session.py — not a runtime-created dir (a fresh member has journals but no archive)."""
    return "def run_compile" in _harness_src(repo)


def d_lazy_start(repo, cfg):
    """ADR-0055: the member's session.py defers mutating start work (journal,
    compile, reap, push) to the first heartbeat, so a phantom SessionStart (the
    desktop-app pre-spawn class) is a no-op by construction. Evidence is the
    shipped substrate — the completion hook in session.py."""
    return "def _complete_lazy_start" in _harness_src(repo)


def d_cross_lane_coordination(repo, cfg):
    """ADR-0062: the member's session.py can coordinate across worktree lanes —
    claims/leases/ADR-number reservations held in the git COMMON dir (the one store
    every lane shares; the liveness sidecar is deliberately per-tree and cannot see a
    sibling). Evidence is the shipped substrate — the acquisition primitive in
    session.py — not a runtime-created store (a member that has never run a second
    lane has the capability and an empty store)."""
    src = _harness_src(repo)
    return "def _coord_try_acquire" in src and "def _git_common_dir" in src


def d_false_green_guard(repo, cfg):
    """WI-0344: the member's check-bash denies a run whose EXIT CODE is the
    verdict when anything runs after it in the same command — a pipe, a `;`, an
    `||`, a newline — because the status that comes back then belongs to the
    downstream command and a failing run reports 0.

    BOTH HALVES ARE REQUIRED, which is the whole reason this is not a one-line
    source grep. The code alone protects nothing: the guard only ever fires
    through the PreToolUse binding, so a member carrying `false_green_violation`
    with no wired `check-bash` has the file and not the capability — and a
    detector satisfied by the file would certify exactly that gap."""
    if "def false_green_violation" not in _harness_src(repo):
        return False
    return "check-bash" in _settings(repo, cfg)


def d_heartbeat_on_tooluse(repo, cfg):
    """ADR-0054: liveness heartbeats fire DURING working turns, not only between them —
    the settings floor wires `session.py heartbeat` as an all-tools PreToolUse hook (the
    session-74 false-reap fix). Parsed, not grepped: the Stop hook runs the very same
    command, so only a PreToolUse entry counts as evidence."""
    try:
        data = json.loads(_settings(repo, cfg) or "{}")
        for entry in (data.get("hooks", {}).get("PreToolUse") or []):
            for h in (entry.get("hooks") or []):
                if "heartbeat" in (h.get("command") or ""):
                    return True
    except Exception:
        pass
    return False


def _launches_off_repo(cfg):
    """True when this member's Architect launches from OUTSIDE its repo.

    Two signals, and the second is why this is a function rather than one key lookup.
    `pad_dir` is the explicit declaration — but a member that has not yet adopted it is
    exactly the member whose lanes are broken, so keying only on it would leave the
    detector inert for every system that still needs the finding. The second signal is
    not a guess: an Architect settings path that escapes the repo (`../architect/...`)
    means the launch cwd is outside the repo, by definition — that is what the path
    being pad-relative MEANS. A member that declares the second and not yet the first
    would otherwise read `present` for `worktree-lanes` while its lanes are broken."""
    c = cfg or {}
    if str(c.get("pad_dir") or "").strip():
        return True
    return str(c.get("architect_settings_path") or "").strip().startswith("..")


def d_worktree_lanes(repo, cfg):
    """ADR-0060, fleet-promoted session 98: the member can RUN concurrent sessions,
    not merely coordinate them. Two halves, both required — a member with one and not
    the other is the exact half-state this capability exists to make visible:

      launch   `poga` at the repo root — the wrapper that auto-allocates the lowest
               free lane and execs `claude --worktree poga-<n>`. Byte-identical
               substrate; resolves its repo from the caller's cwd, so one installed
               symlink drives every member.
      teardown the WorktreeRemove hook running `session.py worktree-remove` — removes
               the lane dir, prunes, deletes the merged branch, releases coord holds.

    The LAND half needs no detector: it is `session.py end`, already covered by
    session-harness. Parsed, not grepped, on the hook side — the same discipline as
    d_heartbeat_on_tooluse.

    THIRD half, for a RESIDENT-RUNTIME member only: the launch must be
    REACHABLE. A resident member could report this capability `present` and its
    overall status `clean` while it cannot open a lane at all — and an attempt
    would boot the resident agent under its own permission posture. Both files
    can be there; neither witnesses WHERE the launch lands. That is ADR-0070's failure
    inverted: there a shipped capability had no detector and the surface certified the
    gap by silence, here a detector certified a capability that did not function. A
    check that cannot tell "installed" from "works here" is not checking the
    capability, and over-reporting is worse than not checking — it converts an unknown
    into a false assurance. So a member declaring `pad_dir` must ALSO carry the
    pad-launch path in its `poga` and the `lane-pad` builder in its harness."""
    if not _exists(repo, "poga"):
        return False
    try:
        data = json.loads(_settings(repo, cfg) or "{}")
        bound = any("worktree-remove" in (h.get("command") or "")
                    for entry in (data.get("hooks", {}).get("WorktreeRemove") or [])
                    for h in (entry.get("hooks") or []))
    except Exception:
        return False
    if not bound:
        return False
    if not _launches_off_repo(cfg):
        return True          # ordinary member: launch lands at the worktree root
    return ("lane-pad" in (_read(repo, "poga") or "")
            and "lane-pad" in _harness_src(repo))


def d_interactive_close_guard(repo, cfg):
    """Session ~102: outside a worktree lane the member's `session.py end` REFUSES
    without `--confirm "<what the user said>"`, and records that reply as the journal's
    `close-confirm` field. The Architect asks ("Close out the session?") and closes only
    once the user agrees — never on its own judgment that its task list is done (session
    98 closed itself off the back of "yes build that"). A lane's `end` is the land step
    and stays exempt.

    Both halves required, deliberately: the flag without the recorded field is a gate
    with no receipt (nothing survives the session to audit), and the field without the
    flag is a receipt nothing enforces. A member holding one and not the other is the
    half-state this capability exists to make visible.

    Scope "all", not "claude-hook": enforcement lives in the harness itself, so any
    member that runs `session.py end` inherits it. There is deliberately no hook half —
    the earlier draft added a PreToolUse prompt and it was cut (session ~102 ruling:
    conversation, not a prompt), which is also the surface P7 names as the
    weak one. What this detector CANNOT witness is whether the Architect actually asked
    before passing a value; that is behavioural, not substrate. It checks that the
    refusal and the receipt exist, and the `close-confirm` field is what makes the
    behavioural half auditable after the fact (a-close-is-the-banner-not-the-sentence)."""
    src = _harness_src(repo)
    return '"--confirm"' in src and '"close-confirm"' in src


def d_work_item_store(repo, cfg):
    """ADR-0073: the member can run a work-item store — durable `WI-NNNN` items with
    drawn ids, and `poga work` as the front door onto them.

    THIS DETECTOR IS LATE, AND THAT IS THE POINT. The store's CODE reached every member
    months ago, because `session.py` and `poga` ship byte-identical
    ([ADR-0070](adr/0070-worktree-lanes-are-fleet-substrate.md)) and both carry it. What
    never shipped was any way to find out: `CANON.md` and `STANDARD.md` — the only two
    documents injected into a member's context — said nothing, and this manifest had no
    marker. A member could hold the `wi-*` verbs in its harness, run `poga work list`
    correctly, and find ZERO mentions across both doctrine files — so an Architect
    searching for the term would correctly report it absent. A capability with no marker does not read as missing — it reads
    as fine, to the very surface that exists to make it visible
    ([`ship-the-detector-with-the-capability`](habits/master.md#ship-the-detector-with-the-capability)).

    Both halves required. The harness verbs are the mechanism; `poga work` is the
    supported access path, and a member holding the verbs without the front door has the
    store only via commands it was told not to reach for directly (hand-editing an item
    file is explicitly not a supported path — `wi-check` is what says the store is sound).

    Deliberately does NOT require `work-items/` to exist. The capability is the ABILITY
    to run a store, not evidence of use — a member that has drawn no items yet is
    conformant, and reporting it behind would make an empty backlog look like a defect.
    Whether a member is actually USING the store is a different question, and one this
    surface should not answer by implication."""
    src = _harness_src(repo)
    if not ('"wi-new"' in src and '"wi-check"' in src):
        return False
    return "cmd_work()" in (_read(repo, "poga") or "")


def d_counter_renumber(repo, cfg):
    """WI-0169 / WI-0245: the member's land REMAPS a colliding store number instead of
    handing the repair back as an errand, for every counter whose registry row can.

    THE GAP THIS CLOSES IS THIS SURFACE'S OWN. WI-0169 shipped the capability and
    deliberately did not ship its marker, on the reading that a new row would make
    already-stale members newly non-conformant. That premise does not hold once members
    are already behind the latest version: the row then moves the target inside an amber
    line they were showing anyway and takes nobody from ok to behind. Deferring a second
    time would leave the capability in the state
    [`ship-the-detector-with-the-capability`](habits/master.md#ship-the-detector-with-the-capability)
    names as the worst one: an exemption that defaults to pass does not merely fail to
    detect a gap, it certifies it — a member whose substrate predates the renumberer
    reads as conformant on a land that will hand its operator a nine-item hand-repair.

    BOTH HALVES REQUIRED, and they answer different questions. `_store_renumber_batch`
    proves the renumberer is IMPLEMENTED; the `renumberer=` binding on a `_Counter` row
    proves it is REACHABLE from the registry the land actually iterates
    (`_auto_renumber_collisions` walks `_counters()` and skips every row whose
    `renumberer` is None). A harness carrying the function but no bound row would remap
    nothing and refuse exactly as before — the silent-no-op shape this manifest exists to
    catch, and not a hypothetical: the function and the binding live ~1300 lines apart.

    Deliberately does NOT require the ADR row to have one. Per WI-0245 the ADR remap is a
    different job (an ADR cites its own number in its body and in a hand-maintained index,
    and accepted ADRs are immutable), and asking for it here would report every member
    behind on a capability the federation has not built either.

    `scope "all"`: the remap runs inside `session.py merge`, not a Claude-Code hook
    binding, so a non-Claude member reads BEHIND rather than n/a."""
    src = _harness_src(repo)
    if "def _store_renumber_batch" not in src:
        return False
    return "renumberer=_store_renumber_batch" in src


def d_janitor(repo, cfg):
    """ADR-0089: the member can clean up its own session records without opening a
    session — the reaper's evidence-based close plus the sweeps the reaper cannot see.

    LATE, for the same reason `work-item-store` was late, and caught the same way. The
    verb ships inside byte-identical `session.py`, so the code reached every member;
    what did not exist was any way for this surface to say so. The de-facto detector was
    `curate/adopt-runner.py` printing `JANITOR N/A` on its nightly pass — which genuinely
    worked (it read every member lacking the verb, then every member sweeping once the
    harness was pushed), but a capability whose only visibility is a line in a nightly
    log is invisible to the surface built to answer "which members can do this?"
    ([`ship-the-detector-with-the-capability`](habits/master.md#ship-the-detector-with-the-capability)).

    Both halves required, and they are not redundant: the subcommand registration proves
    the verb is REACHABLE and `cmd_janitor` proves it is IMPLEMENTED. A partial or
    truncated harness copy is the realistic failure here — there is no second file to
    check, unlike the store's `poga` front door, because the janitor has no separate
    access path — and a member carrying the parser entry without the implementation would
    fail at the moment it was needed rather than at the moment it was measured.

    `scope "all"`: the janitor is harness-level, invoked directly, not a Claude-Code hook
    binding — so a non-Claude member reads BEHIND rather than n/a. It runs the same
    `session.py`, and a repo nobody opens is exactly the case the verb exists for."""
    src = _harness_src(repo)
    return '"janitor"' in src and "def cmd_janitor" in src


def d_session_work_items(repo, cfg):
    """ADR-0093: the member's close harvests the work items its session held into the
    journal, so "what did this session do?" survives the 8h claim TTL.

    Shipped WITH its capability rather than a release later — the three releases before
    this one (1.5.0 worktree-lanes, 1.7.0 work-item-store, 1.8.0 janitor) each had to
    retrofit a marker for code that was already everywhere, and an unmarked capability
    does not read as missing to this surface, it reads as fine.

    Both halves again: the harvest must EXIST and the close must CALL it. A member whose
    `end` does not pass `work_items=` has the function and never writes the field, which
    is precisely the silent-no-op shape this manifest exists to catch — and it is the
    realistic partial, since the two live in different regions of the same file."""
    src = _harness_src(repo)
    return "_harvest_session_work_items" in src and "work_items=" in src


def d_ops_front_door(repo, cfg):
    """WI-0125: the ops namespace (ADR-0076) has the same front door and the same
    self-commit as the work-item store — so an ops write lands in the member's ONE store
    and is saved by the command that made it.

    The asymmetry this marks is invisible from the inside, which is why it needs a
    detector rather than a note. Both namespaces render in the same chart, the same
    startup view and the same feed, so `ops-items/` reads as a peer of `work-items/`
    everywhere a user looks; it diverged at exactly the two points nobody can see —
    where a write LANDS (no front door, so `session.py ops-new` from a lane wrote the
    lane's frozen store) and whether it is SAVED (no auto-commit, and `wi-commit` swept
    only `work-items/`). It cost OPS-0004, drawn in a lane and permanently burned.

    Both halves, like `work-item-store`: `poga`'s `cmd_ops` proves the door is REACHABLE
    with main-checkout anchoring, and the `_store_autocommit(OPS_DIRNAME` call proves the
    write actually SAVES. A member carrying one without the other has the shape of the
    fix and none of its effect, which is the silent-partial this manifest exists to catch.

    `scope "all"`: both halves are harness-level (`session.py` + `poga`), not a
    Claude-Code hook binding, so a non-Claude member reads BEHIND rather than n/a."""
    src = _harness_src(repo)
    if not ('"ops-new"' in src and "_store_autocommit(OPS_DIRNAME" in src):
        return False
    return "cmd_ops()" in (_read(repo, "poga") or "")


def d_trunk_integrate(repo, cfg):
    """WI-0143 / ADR-0097: a lane can reconcile the trunk with its remote, so a land
    whose push is rejected publishes itself instead of asking a human to push.

    The gap this marks is invisible from the inside and only appears under CONCURRENCY,
    which is why it needs a detector rather than a note. A member with lanes but without
    this lands fine, gates fine, advances the trunk fine — and then, the first time a peer
    pushes during a land, prints `run `git push``: advice that cannot succeed from a lane,
    because the fast-forward it asks for needs the trunk checked out and the trunk lives in
    the main checkout the isolation guard refuses to reach into. The work sits on one disk
    and every surface reports a successful land, which it was.

    Both halves, like `work-item-store` and `ops-front-door`, and for the same reason: the
    realistic partial is having the verb and never reaching it. `cmd_integrate` proves the
    verb EXISTS; a SECOND call site proves the LAND runs it for itself, which is the half
    that makes the escalation actually gone rather than merely available. A member carrying
    the first without the second has the shape of the fix and none of its effect.

    COUNTING call sites rather than matching a literal `_integrate_trunk_with_remote()` —
    which is what this asked for until WI-0153, and which went ABSENT the moment the land
    grew a `resolve=` pass-through. The capability had not moved; the marker was pinned to
    an argument list. A detector that reports a present capability as missing because a
    caller gained a keyword is testing the spelling, not the wiring (WI-0158's family), and
    it fails in the direction that wastes a rollout rather than the one that hides a gap.

    `scope "all"`: both halves are harness-level (`session.py`), not a Claude-Code hook
    binding, so a non-Claude member reads BEHIND rather than n/a."""
    src = _harness_src(repo)
    call_sites = (src.count("_integrate_trunk_with_remote(")
                  - src.count("def _integrate_trunk_with_remote("))
    return "def cmd_integrate" in src and call_sites >= 2

def d_lane_repairs_main(repo, cfg):
    """WI-0097 + WI-0109 / ADR-0098: a lane can repair the main checkout's working tree
    under proof, and clear its own blocked rebase under a named policy.

    ONE marker for two verbs, on the ADR-0060 reasoning rather than for tidiness: both ride
    the same `session.py` bytes, so a member holding one necessarily holds the other, and
    the federation-side harness-currency axis catches a partial copy. Two markers here would
    be two rows that can never disagree — noise in a view whose value is that every amber
    means something.

    What the absence looks like from the inside is *nothing at all*, which is why it needs a
    detector. A member without this lands fine and reports fine; the cost only appears when
    main's tree goes stale (an authorised repair becomes a human's terminal, and the banner
    tells them to open a session in a checkout they never run in) or when a rebase conflicts
    (the lane reports BLOCKED and names no next verb, so the work sits unmerged).

    Both halves, and the realistic partial is having the verb without the wiring:
    `cmd_main_restore` proves the repair EXISTS; `_resolve_rebase_conflict(resolve)` proves
    the LAND actually consults the policy rather than shipping a function nothing calls.

    `scope "all"`: harness-level, not a Claude-Code hook binding, so a non-Claude member
    reads BEHIND rather than n/a."""
    src = _harness_src(repo)
    return "def cmd_main_restore" in src and "_resolve_rebase_conflict(resolve)" in src

def d_trunk_merge_integrate(repo, cfg):
    """WI-0153 / ADR-0102: `integrate`'s diverged case MERGES origin in, instead of
    replaying our commits onto it.

    This needs its own row rather than being folded into `trunk-integrate`, and the reason
    is the whole point of the manifest. A member carrying the replay-only integrate passes
    `trunk-integrate` — the verb exists, the land calls it — and is nonetheless unable to
    resolve a real divergence. So the conformance surface would report that member GREEN on
    exactly the capability it lacks. That is the wrong-answer-shaped-like-a-right-one this
    file exists to catch, and the reason [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
    says the detector ships in the same pass as the fix: an unmarked capability of this
    shape does not read as missing, it reads as fine.

    Invisible from the inside, like 1.11.0 and 1.12.0 and worth stating a third time: a
    replay-only member lands fine, integrates fine whenever origin is merely ahead or
    behind, and only fails on the genuine divergence — where it either refuses forever or
    silently rewrites its own history so the two machines never converge. Nothing errors.

    Both halves, because the realistic partial is the function without the wiring:
    `def _merge_onto` proves the mechanism EXISTS, and `_merge_onto(local, remote` proves
    the integrate actually reaches it AND passes the arguments in the load-bearing order
    (ours first — first-parent history stays on this machine's line, which is what the
    checkout-base walk and the main-checkout sync both depend on).

    `scope "all"`: harness-level (`session.py`), not a Claude-Code hook binding, so a
    non-Claude member reads BEHIND rather than n/a."""
    src = _harness_src(repo)
    return "def _merge_onto" in src and "_merge_onto(local, remote" in src

def d_notes_as_records(repo, cfg):
    """WI-0181: an append-only note is its own RECORD, so two machines appending to the
    same item cannot collide.

    The failure is invisible from the inside in the sharpest way yet — it is invisible from
    ONE machine entirely. A member storing notes as one growing string appends fine, renders
    fine, and reads fine forever, right up until a SECOND machine appends to the same item
    between reconciles. Then two writes land at the end of one string with an empty common
    base, git cannot order two appends, and the trunk reconcile stops. That is not a
    hypothetical: it is what WI-0153 was chasing when this was found.

    Both halves, and the realistic partial is the mechanism without the reader:
    `def _note_append` proves notes CAN be written as records; `_notes_compiled(` proves
    they are read back into what every surface shows. A member with the first and not the
    second writes notes nobody ever sees again, which is worse than the bug.

    Covers `ops-items/` too without a second row, on the `d_lane_repairs_main` reasoning:
    both stores ride the same `session.py` bytes, so a member holding one necessarily holds
    the other, and two rows that can never disagree are noise in a view whose value is that
    every amber means something.

    `scope "all"`: harness-level, not a Claude-Code hook binding."""
    src = _harness_src(repo)
    return "def _note_append" in src and "_notes_compiled(" in src


def d_residency(repo, cfg):
    """ADR-0106: the member can say where it runs, refuse dev work on its production host,
    and promote to it.

    THREE HALVES, and the realistic partials are why it is not one string match. A member
    could carry the residency vocabulary with no promote verb (it can describe the split it
    cannot act on); or carry `cmd_promote` with the gate unwired (the refusal exists and
    never fires, which reads as compliance while the production host is developed on daily).
    That second shape is the `d_trunk_integrate` lesson exactly — a verb present and not
    called is the wrong-answer-shaped-like-a-right-one this manifest exists to catch.

    So: `def residency_state` proves the four-state vocabulary; `def cmd_promote` proves the
    pull-side verb; and `_pf_residency` appearing in `PREFLIGHT_GATE_CHECKS` proves the D6
    refusal is actually WIRED rather than merely written. The last is checked by finding the
    name inside the gate tuple's own text, not merely somewhere in the file.

    NOT a check that the member has DECLARED its residency. Declaring is the member's act
    (ADR-0106 D5) and the federation may not perform it for them; this row asks only whether
    the substrate can carry the declaration. An undeclared member reads as a `residency`
    UNKNOWN in its own `poga preflight`, which is where that question belongs.

    `scope "all"`: harness-level, not a Claude-Code hook binding."""
    src = _harness_src(repo)
    if "def residency_state" not in src or "def cmd_promote" not in src:
        return False
    m = re.search(r"PREFLIGHT_GATE_CHECKS\s*=\s*\(([^)]*)\)", src)
    return bool(m) and "_pf_residency" in m.group(1)


def d_interpreter_pin(repo, cfg):
    """WI-0328: the member resolves the Python it runs on instead of taking PATH's.

    TWO HALVES, and either alone is the wrong-answer-shaped-like-a-right-one this
    manifest exists to catch. `interpreter.py` present with nothing calling it is a pin
    that never fires — and it fires from exactly one place, so its absence is silent:
    `session.py` guards the import in a try/except precisely so a member one push behind
    keeps starting, which means a half-delivered pin looks identical to a working one
    from the outside. So the module must be THERE and the entry point must CALL it.

    The call marker is the call site, `_interpreter.ensure(_ROOT)`, not `def ensure` —
    `_harness_src` now includes `interpreter.py` itself, so matching the definition
    would report every member carrying the file as compliant whether or not anything
    invoked it. Same lesson as residency's gate tuple and trunk-integrate's call count.

    `scope "all"`: choosing an interpreter is harness behaviour on every runtime, not a
    Claude-Code hook binding."""
    if _read(repo, "interpreter.py") is None:
        return False
    return "_interpreter.ensure(_ROOT)" in _harness_src(repo)


def d_dispatched_close_authorization(repo, cfg):
    """ADR-0113 (WI-0249 (a), closing WI-0144): `end`'s close guard keys on what
    AUTHORIZED the close rather than on where it runs — and a dispatched close writes the
    dispatch it is closing for into `close-confirm`, resolved against the record, instead
    of leaving the receipt empty.

    This supersedes ADR-0104 D5's lane exemption, and the move is the whole point. v1.6.0
    exempted a WORKTREE LANE because in 2026-07 a lane's `end` was always the land step of
    work a human had just asked for, and D5 named that proxy as the hole it was leaving
    open. Dispatch (ADR-0104) broke the equivalence in both directions at once: a
    dispatched lane closes on an authorization that is real and that nobody typed at it,
    while a HAND-LAUNCHED lane — now the ordinary way a session runs, since ADR-0059 steers
    every session into one — closed with no authorization at all and the guard waved it
    through on its address. A guard keyed on location excused the case it should have asked
    about. Keyed on the dispatch, the exemption finally names its own reason, and `end`
    refuses without `--confirm` in a lane too.

    FOUR PROBES, TWO HALVES, on `d_interactive_close_guard`'s reasoning one turn further
    along. That row already argues that the flag without the recorded field is a gate with
    no receipt and the field without the flag is a receipt nothing enforces; the same split
    reappears here, sharper, because the dispatched path has no human to fall back on.

      exemption `def _dispatched_close_authorization` proves the authorization is
                COMPUTED, and a SECOND call site proves the guard actually CONSULTS it
                rather than shipping a function nothing calls — the `d_trunk_integrate` /
                `d_residency` lesson, where a refusal written and not wired never refuses.
                A member with the function and the old `not on_lane` condition reads
                exactly as compliant as one that ships it, which is the
                wrong-answer-shaped-like-a-right-one this file exists to catch.
      receipt   `_dispatch_read(` INSIDE that function proves the citation is RESOLVED
                against the dispatch record rather than asserted from the environment
                (ADR-0112 D7: `POGA_DISPATCH` is a marker, not a control — a lane that
                merely echoes its own env vouches for a claim it never checked), and
                `confirm=confirm` at the `finalize_journal` call proves the merged value
                — the user's words where they gave them, the citation otherwise — is what
                actually reaches `close-confirm`. A member exempting a dispatched close
                WITHOUT the citation has widened the guard and recorded nothing: every
                dispatched session then closes with an empty `close-confirm`,
                indistinguishable in the record from the unprompted self-close the guard
                exists to prevent, and a wider hole than v1.6.0 opened on lanes because a
                dispatch runs unattended for hours.

    The `_dispatch_read` probe is scoped to the function's own text, not merely to the
    file, for `d_residency`'s reason: the harness has read dispatch records since ADR-0104,
    so a file-wide match would be satisfied by code that predates this capability entirely
    and would certify the fleet as carrying it.

    WHAT THIS CANNOT WITNESS, stated plainly because the gap is the interesting part. It
    cannot see whether a dispatched lane actually FINISHED the item it was dispatched on
    before closing — a lane that gives up, or closes against the wrong item, produces a
    citation as well-formed as one that shipped. That is behavioural, not structural, and
    it is mined from the record (ADR-0053) rather than detected here; the resolvable
    citation is what makes that mining possible at all, which is the same division of
    labour v1.6.0 drew between the flag and the quoted sentence. Nor can it witness that
    the OLD lane exemption is GONE — a member carrying both refuses nothing in a lane, and
    the marker for that would be an absence, which this file does not check on. The
    federation-side harness-currency axis catches that partial, as it does every other
    stale-copy shape.

    `scope "all"`, exactly as `interactive-close-guard` is scoped: enforcement lives in the
    harness, so every member that runs `session.py end` inherits it and a non-Claude member
    reads BEHIND rather than n/a. There is still deliberately no hook half."""
    src = _harness_src(repo)
    if "def _dispatched_close_authorization" not in src:
        return False
    call_sites = (src.count("_dispatched_close_authorization(")
                  - src.count("def _dispatched_close_authorization("))
    if call_sites < 1:
        return False
    body = re.search(r"def _dispatched_close_authorization\(.*?"
                     r"(?=\ndef |\nclass |\n#|\Z)", src, re.S)
    if not body or "_dispatch_read(" not in body.group(0):
        return False
    return "confirm=confirm" in src


def d_unattended_close_authorization(repo, cfg):
    """WI-0331: `end` also exempts an UNATTENDED RUN — and writes a citation that says a
    machine ran it and no human agreed, instead of leaving the path with a fabricated
    `--confirm` as its only way forward.

    ADR-0113 redrew the close guard on WHAT AUTHORIZED the close and found exactly one
    non-human authorization: a dispatch. There were two. `curate/adopt-runner.py` — the
    fleet's only unattended adoption path (ADR-0050) — spawns a headless session in a
    member repo and instructs it to close with `session.py end --commit`. That session is
    not dispatched, is not in a lane, and has nobody attached, so `end` refused it every
    time and the runner's verify-or-reset rolled the applied brief back. The path could
    not complete by construction, on every member at once.

    WHY THIS IS ITS OWN ROW RATHER THAN A WIDENING OF v1.15.0's. The row above is
    satisfied by the federation and by every member that picks the harness up; re-cutting
    it would leave those members reading GREEN on a capability they do not have, which is
    the 1.13.0 / 1.14.0 precedent this file has applied twice since. New code, so members
    read BEHIND until the harness is pushed; that amber is honest.

    THREE PROBES, and the third is the one that matters most here.

      exemption `def _unattended_close_authorization` proves the authorization is
                COMPUTED, and a SECOND call site proves the guard CONSULTS it — the
                `d_trunk_integrate` / `d_residency` lesson, where a refusal written and
                not wired never refuses. A member carrying the function with the old
                two-term condition reads exactly as compliant as one that wired it.
      receipt   `_unattended_run_read(` INSIDE that function proves the citation is
                RESOLVED against the record the runner wrote, not asserted from the
                environment. `POGA_UNATTENDED_RUN` is a marker and not a control
                (ADR-0112 D7, the same argument that variable's sibling gets), so a
                member that merely echoes its own env vouches for a claim it never
                checked.
      honesty   the `NO HUMAN CONFIRMED THIS CLOSE` label, which is the WHOLE POINT of
                fixing this rather than documenting it. The defect's real cost was never
                the stuck verb: it was that the only escape the substrate offered was
                `--confirm "<an invented user quote>"`, written into the audited field
                that exists to prove a human agreed. A member that exempts the unattended
                close WITHOUT the label has made every unattended close
                indistinguishable in the permanent record from a human-confirmed one —
                which is the corruption, arrived at politely. So the label is checked,
                not merely the exemption.

    Scoped to the function's own text for `d_residency`'s reason: a file-wide match for
    `_unattended_run_read(` would be satisfied by the reader alone and certify a member
    that never wired the citation.

    WHAT THIS CANNOT WITNESS, stated because the gap is the interesting part. It cannot
    see the RUNNER half — that a sweep actually writes the record and sets the marker —
    because the runner is federation-only (`curate/`) and never ships to a member; that
    half is pinned by `tests/test_adopt_runner_e2e.py` against the real spawn. Nor can it
    witness that an unattended close actually adopted the brief it was started for; that
    is behavioural and is mined from the record (ADR-0053), which the resolvable citation
    is what makes possible at all.

    `scope "all"`, exactly as its two siblings: enforcement lives in the harness, so every
    member running `session.py end` inherits it and a non-Claude member reads BEHIND
    rather than n/a. No hook half."""
    src = _harness_src(repo)
    if "def _unattended_close_authorization" not in src:
        return False
    call_sites = (src.count("_unattended_close_authorization(")
                  - src.count("def _unattended_close_authorization("))
    if call_sites < 1:
        return False
    body = re.search(r"def _unattended_close_authorization\(.*?"
                     r"(?=\ndef |\nclass |\n#|\Z)", src, re.S)
    if not body:
        return False
    text = body.group(0)
    return "_unattended_run_read(" in text and "NO HUMAN CONFIRMED THIS CLOSE" in text


# --------------------------------------------------------------------------- manifest
#
#   scope "all"         — runtime-agnostic; applies to every member.
#   scope "claude-hook" — evidence is the Claude-Code hook binding; applies only to
#                         a member that binds the harness via .claude/settings.json
#                         (see _hook_bound). N/A for a resident/non-Claude binding
#                         (ADR-0041). A symmetric multi-runtime member that DOES carry a
#                         hook binding is still checked on it.

CAPABILITIES = {
    "session-harness":       ("session.py session-ritual harness (ADR-0020)", d_session_harness, "all"),
    "session-config":        ("session.config.json with architect_id (ADR-0022)", d_session_config, "all"),
    "canon":                 ("CANON.md injected canon digest (ADR-0022)", d_canon, "all"),
    "standard-section":      ("STANDARD.md generated standard section (ADR-0024)", d_standard_section, "all"),
    "standard-reference":    ("STANDARD-REFERENCE.md delivered reference tier (ADR-0136)", d_standard_reference, "all"),
    "handoff":               ("session-handoff continuity doc", d_handoff, "all"),
    "status-surface":        ("STATUS.md cross-system status surface (ADR-0021)", d_status_surface, "all"),
    "roadmap":               ("ROADMAP.md outcome view (ADR-0030)", d_roadmap, "all"),
    "settings-harness-wired": (".claude/settings.json wires session.py start + check-bash (ADR-0034)", d_settings_harness_wired, "claude-hook"),
    "announce-stamp":        ("session.py announce stamp hook (ADR-0043, v2.21.0 fix)", d_announce_stamp, "claude-hook"),
    "rollout-selfcheck":     ("standard_check.py present — self-checks locally (ADR-0047)", d_rollout_selfcheck, "all"),
    "interpreter-pin":       ("interpreter.py resolves the harness's Python instead of PATH (WI-0328)", d_interpreter_pin, "all"),
    "session-journals":      ("session.py compiles the handoff from per-session journals (ADR-0051 1b)", d_session_journals, "all"),
    "false-green-guard":     ("check-bash denies a run whose exit code is swallowed by a pipe/;/|| (WI-0344)", d_false_green_guard, "claude-hook"),
    "heartbeat-on-tooluse":  ("all-tools PreToolUse heartbeat — liveness during long turns (ADR-0054)", d_heartbeat_on_tooluse, "claude-hook"),
    "lazy-start":            ("session.py lazy start — mutating start work deferred to first beat (ADR-0055)", d_lazy_start, "all"),
    "cross-lane-coordination": ("session.py claims/leases/adr-next in the git common dir (ADR-0062)", d_cross_lane_coordination, "all"),
    "worktree-lanes":        ("poga lane launcher + WorktreeRemove teardown hook (ADR-0060)", d_worktree_lanes, "claude-hook"),
    "interactive-close-guard": ("session.py end requires the user's --confirm outside a lane", d_interactive_close_guard, "all"),
    "work-item-store":       ("work-item store — wi-* verbs + `poga work` front door (ADR-0073)", d_work_item_store, "all"),
    "janitor":               ("session.py janitor — session-record cleanup without opening a session (ADR-0089)", d_janitor, "all"),
    "session-work-items":    ("the close harvests the session's claimed work items into its journal (ADR-0093)", d_session_work_items, "all"),
    "ops-front-door":        ("poga ops front door + ops writes self-commit (ADR-0076 / WI-0125)", d_ops_front_door, "all"),
    "trunk-integrate":       ("session.py integrate + the land runs it when its push is rejected (ADR-0097)", d_trunk_integrate, "all"),
    "lane-repairs-main":     ("session.py main-restore + the land's bounded rebase-conflict policy (ADR-0098)", d_lane_repairs_main, "all"),
    "trunk-merge-integrate": ("integrate MERGES origin into a diverged trunk instead of replaying onto it (ADR-0102)", d_trunk_merge_integrate, "all"),
    "notes-as-records":      ("an appended work/ops item note is its own record, so two machines cannot collide (WI-0181)", d_notes_as_records, "all"),
    "dispatched-close-authorization": ("`end` exempts a DISPATCHED close, not a lane, and writes the resolved dispatch citation into `close-confirm` (ADR-0113 / WI-0249, superseding ADR-0104 D5)", d_dispatched_close_authorization, "all"),
    "counter-renumber":      ("the land REMAPS a colliding WI/OPS number onto a drawn one instead of refusing (WI-0169 / WI-0245)", d_counter_renumber, "all"),
    "unattended-close-authorization": ("`end` exempts an UNATTENDED RUN and cites the run record, so the headless adoption path closes without fabricating a user quote (WI-0331)", d_unattended_close_authorization, "all"),
    "residency":             ("residency declared on two axes + the (withdrawn) `promote` verb still present as the pull-side marker + the production-host dev refusal, wired (ADR-0106)", d_residency, "all"),
}

RELEASES = [
    ("1.0.0", {"add": [
        "session-harness", "session-config", "canon", "standard-section",
        "handoff", "status-surface", "roadmap",
        "settings-harness-wired", "announce-stamp",
    ], "remove": []}),
    ("1.1.0", {"add": ["rollout-selfcheck"], "remove": []}),
    ("1.2.0", {"add": ["session-journals"], "remove": []}),
    # 1.3.0 carries BOTH session-74 fixes (ADR-0054 + ADR-0055) — authored the same
    # night, folded into one release because 1.3.0 had not yet rolled out anywhere.
    ("1.3.0", {"add": ["heartbeat-on-tooluse", "lazy-start"], "remove": []}),
    # 1.4.0 — the concurrency arc's coordination half (ADR-0062), shipped with the
    # same session.py bytes that carry detect-and-refuse (ADR-0059), the automatic
    # lane reap (ADR-0060), and the accept-lag land. Only the coordination store gets
    # its own detector: the four ride one byte-identical harness, so a member holding
    # the store necessarily holds the rest, and the federation-side harness-currency
    # axis catches any partial copy. One marker, not four — the parity view stays
    # low-noise (cf. the `mark-adr-reality-status` low-noise-column reasoning).
    ("1.4.0", {"add": ["cross-lane-coordination"], "remove": []}),
    # 1.5.0 — the concurrency arc's EXECUTION half. 1.4.0 shipped the coordination
    # store, so every member could already arbitrate between lanes it had no way to
    # launch: the `poga` wrapper was federation-only and the WorktreeRemove teardown
    # hook lived in the federation's settings_extras. Promoting both (poga into the
    # byte-identical push set, the hook into the settings floor) closes that gap —
    # concurrency stops being a federation privilege. `claude-hook` scope, unlike
    # 1.4.0's coordination store: `claude --worktree` and the WorktreeRemove event are
    # Claude-Code bindings, so a non-Claude member reads n/a rather than behind.
    ("1.5.0", {"add": ["worktree-lanes"], "remove": []}),
    # 1.6.0 — the interactive-close guard. Session 98 closed itself off the back of a
    # work instruction, so a session's `ended` stamp could record a close the user never
    # asked for. `scope "all"`, unlike 1.5.0: enforcement is in the harness (`session.py
    # end` refuses without `--confirm`), not in a Claude-Code hook binding, so a
    # non-Claude member is BEHIND rather than n/a — it runs the same `end`. Shipped with
    # its detector in the same pass on purpose: the capability that preceded it
    # (worktree-lanes) spent a release invisible to this very surface because it had no
    # marker, and an unmarked capability does not read as missing, it reads as fine
    # (ship-the-detector-with-the-capability).
    ("1.6.0", {"add": ["interactive-close-guard"], "remove": []}),
    # 1.7.0 — the work-item store, and the most awkward release on this list: the
    # capability it names has been present in every member for months. `session.py` and
    # `poga` both carry it and both ship byte-identical (ADR-0070), so the CODE channel
    # delivered it silently while the CANON channel delivered nothing and this manifest
    # had no marker. One member's Architect, 2026-08-04: it "can't find the term anywhere" — and
    # it could not, since the only place the store existed in its world was inside a
    # script nothing told it to read (25 wi-* references in its harness; 0 mentions
    # across CANON.md and STANDARD.md).
    #
    # So 1.7.0 does not ship a capability. It ships the two things that were supposed to
    # ride alongside one: the doctrine (standard-source.md gains the store, regenerated
    # into STANDARD.md and injected at every member's next session start) and this
    # detector. That is 1.6.0's own comment coming true a second time — a capability that
    # spent a release invisible to this surface because nothing marked it — which is why
    # the rule is ship-the-detector-WITH-the-capability rather than eventually.
    #
    # `scope "all"`: the store is harness-level, not a Claude-Code binding, so a
    # non-Claude member reads BEHIND rather than n/a — it runs the same session.py.
    # Expect this release to read as immediately-satisfied fleet-wide once the harness
    # is current; that is not a bug in the detector, it is the measurement of how long
    # the gap had been open.
    ("1.7.0", {"add": ["work-item-store"], "remove": []}),
    # 1.8.0 — the janitor, and the third release in a row to ship a marker for code that
    # was already everywhere. The pattern is now established well enough to name: a
    # capability that rides byte-identical `session.py` arrives fleet-wide the moment the
    # harness is pushed, and unless the same pass adds a marker here, this surface keeps
    # reporting the fleet clean on a question it was never asked to answer.
    #
    # What makes this one worth recording rather than just fixing: the janitor DID have a
    # detector, in the sense that somebody could find out — `curate/adopt-runner.py`
    # printed `JANITOR N/A` nightly, and that reading was accurate (every member
    # lacking the verb, then every member sweeping once pushed). It was in the wrong place. A
    # true fact in a nightly log is not a conformance surface, because nobody asking
    # "which members can clean up after themselves?" reads a runner log to find out —
    # they read this. Detection existing somewhere is not the bar; the bar is detection
    # where the question gets asked.
    #
    # `scope "all"`, like 1.7.0: harness-level, not a hook binding. Expect this to read
    # immediately-satisfied wherever the harness is current — which, as with 1.7.0, is
    # the measurement of how long the gap was open, not a detector that does nothing.
    ("1.8.0", {"add": ["janitor"], "remove": []}),
    # 1.9.0 — the first release in four whose detector is not a retrofit. ADR-0093's
    # harvest and this marker were written in the same pass, which is what
    # ship-the-detector-with-the-capability actually asks for; 1.5.0, 1.7.0 and 1.8.0 all
    # had to come back for it afterwards, and each time the surface reported the fleet
    # clean on a question nobody had taught it to ask.
    #
    # Unlike those three, this one will genuinely read BEHIND until the harness is pushed
    # — the capability is new code, not a marker catching up with old code. That is the
    # normal shape of a release and worth naming here only because the last three were
    # not: a fleet that goes amber after this is the detector working.
    ("1.9.0", {"add": ["session-work-items"], "remove": []}),
    # 1.10.0 — the ops namespace gets the front door and the self-commit the work-item
    # store has had since ADR-0073/WI-0056. Detector written in the same pass as the fix,
    # like 1.9.0 and unlike the three before it.
    #
    # Worth recording because of WHAT was missing rather than that something was. Neither
    # half was a capability nobody had built; both were halves of a capability that WAS
    # built, for one of two namespaces that render identically to every reader. That is a
    # harder shape to see than an absent feature: the chart, the startup view and the feed
    # all showed ops as a peer of work-items, and the divergence lived only at the point
    # a write lands and the point it is saved. The cost was a permanently burned id
    # (OPS-0004) and an obligation left uncommitted in the main checkout where the next
    # session would have met it as unexplained authored dirt.
    #
    # Like 1.9.0 this is new code, so members read BEHIND until the harness is pushed —
    # the amber is honest, not a marker catching up with itself.
    ("1.10.0", {"add": ["ops-front-door"], "remove": []}),
    # 1.11.0 — a lane can finish the last mile. Until ADR-0097 a land whose push was
    # rejected (a peer pushed while we were landing) had NO path from a lane: the
    # fast-forward needs the trunk checked out, and the trunk is in the main checkout
    # the isolation guard correctly refuses to reach into. The land printed
    # `run `git push``, which is an instruction a human cannot carry out from there.
    #
    # Marked because the absence is invisible from the inside and only fires under
    # concurrency: everything about the land succeeds, the work simply never leaves
    # the machine. That is the wrong-answer-shaped-like-a-right-one this manifest
    # exists to catch, and an unmarked capability does not read as missing here — it
    # reads as fine.
    #
    # New code, like 1.9.0 and 1.10.0, so members read BEHIND until the harness is
    # pushed. The amber is honest.
    ("1.11.0", {"add": ["trunk-integrate"], "remove": []}),
    # 1.12.0 — the other two thirds of "a lane can do everything except the last mile
    # that touches main". WI-0097: an authorised repair of main's working tree could
    # not be run from a lane, so it went to a human terminal, and the banner told him
    # to open a session in a checkout he never runs in. WI-0109: a rebase conflict
    # aborted cleanly and named no next verb, which is what left a lane unmerged
    # while its siblings landed.
    #
    # Marked for the same reason as 1.11.0 and worth stating twice: the absence is
    # invisible from the inside. Nothing errors, nothing reports amber — the cost is
    # paid later, by a person, in a terminal. An unmarked capability of that shape
    # does not read as missing to this surface, it reads as fine.
    ("1.12.0", {"add": ["lane-repairs-main"], "remove": []}),
    # 1.13.0 — the correction to 1.11.0, found by the acceptance test 1.11.0 never got.
    # `integrate` reconciled a diverged trunk by REPLAYING our commits onto origin, which
    # rewrites our side: the peer's clone then diverges from a trunk it was part of, so two
    # machines take turns rewriting each other and never converge. The genuine resolution —
    # a merge commit, which makes origin an ancestor without rewriting anything — was the
    # one thing neither `integrate` nor `merge` could produce, so an operator hand-built it
    # in a lane (WI-0153, session ~161).
    #
    # A SEPARATE ROW, not a silent re-cut of 1.11.0, and this is the honest half. The
    # replay-only member passes `trunk-integrate`: the verb is there and the land calls it.
    # Folding the fix into that row would leave every member reading GREEN on a capability
    # it does not have, which is worse than reading BEHIND — a version bump that changes
    # what a member DOES needs a marker that changes what the surface SEES.
    #
    # `notes-as-records` (WI-0181) is folded into the SAME release rather than cut as
    # 1.14.0, on the 1.3.0 precedent and for the identical reason: both were authored in
    # one session and 1.13.0 had not rolled out ANYWHERE when the second landed (verified
    # against the fleet view — every located member read v1.10.0 or lower). Folding costs
    # the rollout nothing and saves it a step; cutting a version nobody has ever seen would
    # be bookkeeping performed on an audience of zero. They stay TWO capability rows, so
    # each is still separately detectable — the version is the shipping unit, the row is
    # the truth-telling unit, and only the first of those is worth economising.
    ("1.13.0", {"add": ["trunk-merge-integrate", "notes-as-records"], "remove": []}),
    # 1.14.0 — a member can say WHERE it runs, and the substrate can act on the answer
    # (ADR-0106). the operator's 2026-08-22 ruling split the machines by role: development on DevBox,
    # production on the Runner. Until now that lived in two work-item notes and nothing
    # in the substrate could read it, so every part of the split was held by someone
    # remembering it.
    #
    # A SEPARATE ROW rather than a fold into 1.13.0, on the 1.13.0 precedent and for the
    # opposite reason: 1.13.0 has now rolled out (the fleet view reads members ON it), so
    # folding would silently re-cut a version members already report as satisfied and leave
    # them green on a capability they do not have.
    #
    # New code, like 1.9.0 through 1.13.0, so members read BEHIND until the harness is
    # pushed. The amber is honest.
    #
    # The row deliberately does NOT check that a member has declared its own residency.
    # Declaring is the member's act (ADR-0106 D5) and the federation may not perform it for
    # them; this asks only whether the substrate can carry a declaration. The declaration
    # itself surfaces in each member's own `poga preflight`, as UNKNOWN until they answer.
    ("1.14.0", {"add": ["residency"], "remove": []}),
    # 1.15.0 — the close guard stops asking WHERE a session runs and starts asking WHAT
    # authorized it (ADR-0113 / WI-0249 (a), closing WI-0144). v1.6.0 exempted a worktree
    # lane, a true proxy in 2026-07 and not one now: dispatch (ADR-0104) produces closes
    # carrying a real authorization nobody typed, and ADR-0059 made the hand-launched lane
    # the ordinary session — so the row exempted the case that should have been asked about
    # and asked the case that needed no asking. The exemption moves onto the dispatch, and
    # it brings a receipt: a dispatched close cites the dispatch id in `close-confirm`,
    # resolved against the dispatch record, instead of closing with the field empty.
    #
    # A SEPARATE RELEASE, not a fold into 1.14.0, and the 1.3.0 / 1.13.0 folding precedent
    # is what rules it out rather than what permits it: folding is only ever free when the
    # prior release rolled out NOWHERE. 1.14.0 has rolled out. Verified against the fleet
    # view on 2026-09-04 rather than asserted — `curate/standard_version.py` reads the
    # federation itself at v1.14.0 current, with every other located member at
    # v1.13.0 and below, and the 1.14.0 residency brief (WI-0209) already out to them
    # naming 1.14.0 as the version to pull. Re-cutting a version a member has satisfied,
    # or that a brief in flight names, would leave that member reporting GREEN on a
    # capability it does not have — the precise failure 1.13.0's comment refuses.
    #
    # `scope "all"`, like 1.6.0 and for the same reason: the refusal is in `session.py end`,
    # not in a Claude-Code hook binding, so a non-Claude member is BEHIND rather than n/a.
    #
    # New code, so members read BEHIND until the harness is pushed. The amber is honest:
    # this release ships the detector in the SAME pass as the capability it marks, which is
    # ADR-0104 D6's precedent and the point at which this surface can tell at all.
    ("1.15.0", {"add": ["dispatched-close-authorization"], "remove": []}),
    # 1.16.0 — the land stops handing back a number collision as a hand-repair, for the
    # OPS store as well as WI (WI-0169 shipped the capability, WI-0245 the marker and the
    # second counter). A colliding id used to cost a rename, an H1 edit, a notes-directory
    # move, a claim that silently stayed on the old number, and a grep through every
    # citation to decide which meant this lane's item and which meant the trunk's — done
    # inside a lane, at a land gate, which is the worst available moment for careful
    # manual edits.
    #
    # LATE, and the lateness is the honest part. WI-0169 shipped the verb in 2026-08 and
    # deliberately left this row unwritten, reasoning that adding it would mark the
    # already-stale members newly non-conformant. That premise is checked, not inherited:
    # `curate/standard_version.py` reads which located members are ALREADY behind, and
    # where they are, this row moves the target inside an amber line they were showing
    # anyway and takes nobody from ok to behind. The deferral was answering a fleet question that had since
    # answered itself, and re-deferring would have left a real capability invisible to the
    # one surface built to say which members have it.
    #
    # A SEPARATE RELEASE, not a fold into 1.15.0, on the 1.13.0 / 1.14.0 precedent:
    # folding is free only when the prior release rolled out NOWHERE, and 1.15.0 is
    # already satisfied by the federation itself. Re-cutting it would leave that member
    # reporting GREEN on a capability it did not yet have.
    #
    # New code, so members read BEHIND until the harness is pushed. The amber is honest.
    ("1.16.0", {"add": ["counter-renumber"], "remove": []}),
    # 1.17.0 — the interpreter pin (WI-0328). Lanes can build green and every land be
    # refused when `python3` on a machine is a newer, separately installed Python while
    # `/usr/bin/python3` is the older system one, and the suite's verdict differs between
    # them.
    # WI-0320 had already pinned every subprocess the gate spawns to `sys.executable`;
    # what nothing pinned was the process that CHOOSES it, so the answer was whatever
    # the operator's PATH said.
    #
    # `scope "all"`, and the row is cut RATHER THAN DEFERRED on the 1.16.0 precedent
    # immediately above: deferring answers a fleet question ("this marks members newly
    # behind") that the rollout has already answered for itself, and re-deferring leaves
    # a real capability invisible to the one surface built to say who has it. New code,
    # so members read BEHIND until the harness is pushed; that amber is honest.
    #
    # This is also the release whose capability is hardest to see from outside, which is
    # the argument for cutting it at all. `session.py` guards the import so a member
    # without `interpreter.py` still starts — correctly — and therefore looks exactly
    # like a member with a working pin right up until two machines disagree about a
    # verdict. The detector is the only thing that can tell them apart.
    ("1.17.0", {"add": ["interpreter-pin"], "remove": []}),
    # 1.18.0 — the unattended close (WI-0331, found on OPS-0007's first run). ADR-0113
    # gave a DISPATCHED session a way to close without a human; the fleet's unattended
    # ADOPTION path (ADR-0050) still had none, so `curate/adopt-runner.py` applied each
    # brief, could not close, produced no commit, and reset its own work — on every
    # member, every sweep.
    #
    # CUT AS A CAPABILITY RATHER THAN LEFT AS A RUNNER FIX, which is the judgment in this
    # row. The runner is federation-only and could have been made to work against members
    # exactly as they are (set the dispatch marker and let the fail-open path write
    # `dispatch D-xxxxxx — UNRESOLVED`). That closes the verb and corrupts the vocabulary:
    # every unattended adoption would then be recorded, permanently and fleet-wide, as a
    # dispatch that could not be read. The capability exists so the record can say what
    # actually happened.
    #
    # `scope "all"` and a SEPARATE release from 1.15.0, on the 1.13.0 / 1.14.0 / 1.16.0
    # precedent: folding is free only when the prior release rolled out nowhere, and
    # 1.15.0 is already satisfied by the federation and by every member that has taken the
    # harness since. New code, so members read BEHIND until the harness is pushed; that
    # amber is honest, and it is the detector working.
    ("1.18.0", {"add": ["unattended-close-authorization"], "remove": []}),
    # 1.19.0 — the false-green guard (WI-0344). `check-bash` now denies a run whose EXIT
    # CODE is the verdict when anything runs after it in the same command. WI-0139 built
    # the honest verb in 2026-08 so the right form would be the default; the class has
    # recurred EIGHT times since, five of them after that verb shipped, and every one
    # produced a confident wrong answer rather than an error. One of the eight reported a
    # land as successful that had exited 1 on a conflict.
    #
    # CUT AS A CAPABILITY RATHER THAN LEFT AS A HARNESS DETAIL. The alternative was to
    # let it ride in on the next substrate push unmarked, and that is precisely the shape
    # `ship-the-detector-with-the-capability` names: a member with no marker reads as
    # having the guard when the surface cannot see either way. The whole point of this
    # guard is that a thing which merely LOOKS verified is not evidence, so shipping it
    # invisibly would be the defect wearing its own fix.
    #
    # `scope "claude-hook"`, NOT "all", and the distinction is load-bearing. The code
    # alone protects nothing — the guard only ever fires through the PreToolUse binding,
    # so a resident/non-Claude member carrying `false_green_violation` has the file and
    # not the capability. The detector therefore requires BOTH the shipped function and a
    # wired `check-bash`; a detector satisfied by the file would certify the gap.
    #
    # A SEPARATE RELEASE, not a fold into 1.18.0, on the 1.13.0 / 1.14.0 / 1.16.0 / 1.18.0
    # precedent: folding is free only when the prior release rolled out NOWHERE, and
    # 1.18.0 is already satisfied by the federation. Re-cutting it would leave that member
    # reporting GREEN on a capability it did not yet have.
    #
    # MEMBERS WILL READ BEHIND FOR AT LEAST A WEEK, deliberately, and that amber is the
    # most honest thing in this row. WI-0344's acceptance holds the fleet push until
    # 2026-09-19 so the federation dogfoods a deny that ships byte-identical to every
    # member. During that hold the surface says these members do not have the
    # guard, which is true, and is exactly what a deliberate hold should look like from
    # outside.
    ("1.19.0", {"add": ["false-green-guard"], "remove": []}),
    # 1.20.0 — the standard section split into an injected tier and a delivered
    # reference tier (ADR-0136). A SEPARATE RELEASE for the 1.19.0 reason, which now
    # applies with more force: 1.19.0 is itself mid-rollout, and folding this in would
    # let a member that has neither report on one.
    #
    # NOT A FLOOR ADDITION. `required_set` walks RELEASES cumulatively and the 1.0.0
    # floor is what makes a member BELOW-FLOOR rather than merely behind; adding this
    # key there would flip every existing member to below-floor the moment it landed,
    # for a file none of them can have yet. Behind is the honest reading until the push
    # runs, and the push is sequenced AFTER the consolidation on purpose (WI-0010 owed
    # the delivery; running it before the pass would have made the fleet's per-session
    # cost worse, not better).
    ("1.20.0", {"add": ["standard-reference"], "remove": []}),
]

LATEST = RELEASES[-1][0]


def required_set(version):
    req = []
    for ver, delta in RELEASES:
        for k in delta.get("add", []):
            if k not in req:
                req.append(k)
        for k in delta.get("remove", []):
            if k in req:
                req.remove(k)
        if ver == version:
            break
    return req


def retired_set(version):
    gone = []
    for ver, delta in RELEASES:
        for k in delta.get("remove", []):
            if k not in gone:
                gone.append(k)
        for k in delta.get("add", []):
            if k in gone:
                gone.remove(k)
        if ver == version:
            break
    return gone


# --------------------------------------------------------------------------- evaluate


def _hook_bound(repo, cfg=None):
    """True when the member binds the harness via the Claude-Code SessionStart hook —
    its .claude/settings.json references session.py. A resident runtime
    carries a settings.json but launches the harness itself, so the file's presence
    is not the hook binding; a non-Claude runtime has no such file. In both cases the
    claude-hook capabilities read N/A (obligation met via a binding this detector
    cannot see, ADR-0041). A kit member that lost only its hook line still references
    session.py, so real regressions surface — this only excuses other bindings."""
    return "session.py" in _settings(repo, cfg)


def _cap_applies(scope, repo, runtime, cfg=None):
    if scope == "all":
        return True
    if scope == "claude-hook":
        return _hook_bound(repo, cfg)
    return scope == runtime


def evaluate(repo, cfg):
    """Run every detector against `repo`. Returns a dict with detected/caps/
    status/note. No harness-currency axis here (a member cannot compare its session.py
    to the federation's — that lives in the federation-side fleet tool)."""
    cfg = cfg or {}
    runtime = (cfg.get("runtime") or "claude").lower()

    caps = {}
    present = set()
    for key, (_desc, detector, scope) in CAPABILITIES.items():
        if not _cap_applies(scope, repo, runtime, cfg):
            caps[key] = "n/a"
            present.add(key)
            continue
        try:
            ok = bool(detector(repo, cfg))
        except Exception:
            ok = False
        caps[key] = "present" if ok else "absent"
        if ok:
            present.add(key)

    detected = None
    for ver, _delta in RELEASES:
        if all(k in present for k in required_set(ver)):
            detected = ver

    target = detected or LATEST
    stale = [k for k in retired_set(target) if caps.get(k) == "present"]
    missing = [k for k in required_set(LATEST) if caps.get(k) == "absent"]
    floor_missing = [k for k in required_set(RELEASES[0][0]) if caps.get(k) == "absent"]

    status, note = _classify(detected, floor_missing, stale)
    return {
        "runtime": runtime, "detected": detected,
        "caps": caps, "missing": missing, "stale": stale,
        "status": status, "note": note,
    }


def _classify(detected, floor_missing, stale):
    """Status from the DETECTORS alone (ADR-0068). There is no declared-version axis:
    a member's `standard_version` was a hand-written claim no tool ever wrote or
    updated, so the comparison ran a real value against a blank on every established
    member. Detection is ground truth (ADR-0047's own rule), which makes the claim
    redundant where the two agree and wrong where they don't."""
    if stale:
        return "drift", f"stale-not-removed: {', '.join(stale)}"
    if detected is None:
        return "below-floor", f"below v1.0.0 floor — missing {', '.join(floor_missing)}"
    if _cmp(detected, LATEST) < 0:
        return "behind", f"v{detected} — open rollout to v{LATEST} not yet applied"
    return "clean", f"v{detected} (current)"


def _norm(v):
    if not v:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v))
    return ".".join(m.groups()) if m else None


def _cmp(a, b):
    ta = tuple(int(x) for x in a.split("."))
    tb = tuple(int(x) for x in b.split("."))
    return (ta > tb) - (ta < tb)


# --------------------------------------------------------------------------- CLI


def _load_cfg(repo):
    try:
        return json.loads((repo / "session.config.json").read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


_MARK = {"clean": "ok", "behind": "BEHIND", "drift": "DRIFT",
         "below-floor": "BELOW-FLOOR"}


def status_line():
    cfg = _load_cfg(ROOT)
    r = evaluate(ROOT, cfg)
    det = f"v{r['detected']}" if r["detected"] else "below floor"
    if r["status"] == "clean":
        return f"Standard-version: {det} — self-check clean (latest v{LATEST})."
    return f"Standard-version: {det} — {_MARK.get(r['status'], r['status'])}: {r['note']}."


def self_report():
    cfg = _load_cfg(ROOT)
    r = evaluate(ROOT, cfg)
    aid = (cfg or {}).get("architect_id", "?")
    print(f"Standard-version self-check — {aid} (runtime: {r['runtime']})\n")
    print(f"  detected: v{r['detected']}" if r["detected"] else "  detected: below floor")
    print(f"  latest:   v{LATEST}")
    print(f"  status:   {_MARK.get(r['status'], r['status'])} — {r['note']}\n")
    for key, (desc, _det, _scope) in CAPABILITIES.items():
        print(f"    [{r['caps'][key]:>7}] {key:<24} {desc}")
    return 0 if r["status"] in ("clean", "behind") else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Standard-version self-check (ADR-0047).")
    ap.add_argument("--status", action="store_true", help="one-line session-start signal")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.status:
            print(status_line())
            return 0
        return self_report()
    except Exception:  # fail open — never brick a session start
        print("Standard-version: self-check errored; verified nothing.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
