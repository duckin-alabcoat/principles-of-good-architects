#!/usr/bin/env python3
"""Fresh-Architect bootstrap harness — deterministic, no LLM (ADR-0025).

The mechanical half of the PROCESS.md fresh-Architect onboarding path, as code.
Same problem session.py solved for the session rituals: the LLM kept hand-doing
file copies, token substitution, and path/convention resolution, and kept getting
the mechanical parts wrong (session 38: wrong clone path, absolute inbox path,
/Users-vs-/Volumes confusion). None of that needs a language model — it's pure
mechanism (P15 code-for-mechanism-not-judgment).

Split of responsibility:
  - The LLM (the Federation Architect, from the intake conversation) authors a
    SPEC FILE (JSON) carrying ONLY the judgment: mission/scope/voice prose,
    system-specific principles + session steps, the friendly folder name, the
    orchestrator-agent presence, and the intake answers. It then runs the CLI and
    REVIEWS the generated repo.
  - This module does everything mechanical, deterministically: derive the 4-slot
    identity (ADR-0006), detect machine/timezone/date/gh account, copy the kit
    files with renames, substitute every <<TOKEN>>, strip the template guidance
    blocks, write the inbox path RELATIVE, declare Data root as the /Users
    (canonical) view, make the inbox dirs, seed the user profile, commit + push
    the new repo, and file a portfolio.md registration REQUEST in the Federation
    Architect's inbox.

SINCE ADR-0061 THIS MODULE IS THE *ENGINE*, NOT THE ORCHESTRATOR.
`poga_cli.py` owns the seven-phase lifecycle pipeline that `poga bootstrap` and
`poga restore` BOTH run; the repo-creation and clone phases moved there, because
restore shares them (restore = bootstrap + hydrate, ADR-0061 D1). What stays here
is the part only bootstrap does: rendering the kit into a fresh clone
(`materialize_kit`), plus the identity/detection/render primitives both verbs use.
`python3 bootstrap.py --spec <spec>` still works — it delegates to `poga_cli` — so
PROCESS.md's documented command keeps functioning, but `poga bootstrap` is the
current surface.

ADOPT-IN-PLACE (ADR-0067) also lives here: the third onboarding path, for a
directory with real content and no Architect (`git init` + an initial commit are
made automatically if the target has no git history yet). It is engine-only — it
skips the pipeline's repo-create/clone (`acquire`) phase entirely, because the
target already exists; blurring it into `bootstrap` would let install-the-pipes
grow build-the-house ambitions.

It deliberately does NOT WRITE the federation repo at all. `portfolio.md` is a
federation registry whose single writer is the Federation Architect (P13), and this
module runs inside the NEW system's onboarding — under `poga new`, inside an agent
session standing in that system's own folder. It therefore files a registration
REQUEST into `proposed-edits/federation-arch/pending/` (gitignored, so no tracked
tree is touched) and the Federation Architect applies it in its own session.

Until session ~118 it edited and staged `portfolio.md` directly. Staging rather than
committing did not make that a single-writer story — the write had already happened,
it left the federation's working tree dirty for whatever session swept next, and a
worktree lane could not commit it by any route (WI-0056). The receipt ritual
(ADR-0013) is the sanctioned cross-writer mechanism and this is its case.

This module is FEDERATION-ONLY. Like curate/distill.py and curate/standardize.py,
it is not copied into a fresh Architect's repo and is not in the PROCESS.md §4
copy list.

Usage:
  poga bootstrap --spec path/to/spec.json           # current surface
  poga bootstrap --spec path/to/spec.json --plan    # plan only; write/create nothing
  python3 bootstrap.py --spec path/to/spec.json     # legacy alias; same code path
  python3 bootstrap.py --spec spec.json --adopt PATH [--create-remote] [--dry-run]
                                                    # adopt-in-place (ADR-0067)

Spec schema (JSON) — keys the LLM fills:
  system_name           "Example App"                (function name; ids derive from it)
  friendly_folder       "ExampleApp"                 (folder under projects_dir; CamelCase, like the other member folders)
  orchestrator_agent    null | "Orchestrator"        (null => no user-facing agent)
  orchestrator_note     "(none — CLI runtime)"       (optional; portfolio orchestrator-cell text when no agent)
  user_id               "operator"                   (optional; derived from user_name)
  user_name             "Operator"                   (optional; the federation config's, else the login name)
  repo_owner            "example-owner"              (optional; read only when a remote is created — WI-0452)
  repo_name             null                          (optional; defaults to <system_id>)
  repo_url              null                          (optional; ADR-0061 D4 identity pin, defaults from owner/name)
  root_commit           null                          (optional for bootstrap, REQUIRED for restore — the D4 finding-9 pin)
  install_dir           null                          (optional; overrides projects_dir/friendly_folder)
  canonical_install_dir null                          (optional; the /Users view of install_dir)
  bootstrappable        true                          (false => restore-only spec; `poga bootstrap` refuses it)
  create_remote         false                         (optional; true = create the private GitHub remote, same as --create-remote)
  timezone              "UTC"                         (optional; else the federation config's, else this machine's)
  machine_map           {}                            (optional; else the federation config's; this machine is always added)
  local_federation      null                          (optional; set by `poga init` — where the operator profile may live)
  projects_dir          "/Volumes/you/Projects"      (THIS machine's view — where to clone)
  canonical_projects_dir"/Users/you/Projects"        (the /Users view — for the declared Data root)
  federation_repo_url   "https://github.com/<owner>/<repo>/blob/main"   (optional doc-link base; defaults to this checkout)
  mission_prose         "...markdown paragraphs..."
  scope_do              ["...", "..."]
  scope_dont            ["...", "..."]               (appended after the two required don'ts)
  voice                 ["...", "..."]
  system_principles     []                            ([] => placeholder)
  session_steps         []                            ([] => placeholder)
  extra_artifact_rows   []                            (full "| a | b | c |" rows; [] => row removed)
  extra_input_rows      []
  extra_gate_bullets    []
  next_bullets          []                            (handoff "what's next" items 3+)
  open_questions        []                            ([] => "*None.*")
  pending               []                            ([] => "*None.*")
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from session import atomic_write

ROOT = Path(__file__).resolve().parent          # the federation repo root
KIT = ROOT / "bootstrap-kit"
FED_CONFIG = ROOT / "session.config.json"        # the federation's machine_map + timezone, when it has them

# Kit file -> (destination relative path in the new repo, substitute-tokens?)
# Verbatim files are copied byte-for-byte (CANON/STANDARD are generated; gitignore/
# settings/adr-template carry no tokens).
COPY_PLAN = [
    ("claude-md-template.md",        "CLAUDE.md",                      True),
    ("role-doc-template.md",         None,                             True),   # -> <architect-id>.md
    ("status-template.md",           "STATUS.md",                      True),
    ("roadmap-template.md",          "ROADMAP.md",                     True),   # ADR-0030 (carries <<SYSTEM_ID>>/<<TODAY>>)
    ("session-handoff-template.md",  "session-handoff.md",             True),
    ("architect-learnings-template.md", "architect-learnings.md",      True),
    ("readme-template.md",           "README.md",                      True),
    ("adr-readme-template.md",       "adr/README.md",                  True),
    ("session.config.json",          "session.config.json",            True),
    ("adr-template.md",              "adr/template.md",                False),
    ("gitignore-template",           ".gitignore",                     False),
    ("CANON.md",                     "CANON.md",                       False),
    ("STANDARD.md",                  "STANDARD.md",                    False),
    ("STANDARD-REFERENCE.md",        "STANDARD-REFERENCE.md",          False),
    ("claude-settings-template.json", ".claude/settings.json",         False),
]

# ---- adopt-in-place (ADR-0067) --------------------------------------------
# The third onboarding path: an existing repo with real content and NO Architect.
# Fresh assumes an empty/absent repo; retrofit (ADR-0014) assumes an evolved role
# doc to reconcile. A built system that never had an Architect is neither.
#
# The copy plan is deliberately a SUBSET of COPY_PLAN — substrate only. Everything
# omitted is omitted because the adopted system already owns that surface, and
# standardization stops at the operating substrate (ADR-0023: same pipes, different
# houses):
#   STATUS.md      — an adopted system may already publish one (the first adopted member did, correctly)
#   README.md      — belongs to the system, not the Architect
#   .gitignore     — MERGED, never replaced; the existing one is usually load-bearing
#                    (an adopted member's may be all that keeps its irreplaceable data out of git)
#   adr/README.md  — the system may already keep ADRs elsewhere (e.g. docs/adr/).
#   adr/template.md  Installing a second ADR home would duplicate it (P16) and impose
#                    our layout on its house.
# Anything in this plan that already exists is SKIPPED and reported, never clobbered.
ADOPT_COPY_PLAN = [
    ("claude-md-template.md",        "CLAUDE.md",                      True),
    ("role-doc-template.md",         None,                             True),   # -> <architect-id>.md
    ("roadmap-template.md",          "ROADMAP.md",                     True),
    ("session-handoff-template.md",  "session-handoff.md",             True),
    ("architect-learnings-template.md", "architect-learnings.md",      True),
    ("session.config.json",          "session.config.json",            True),
    ("CANON.md",                     "CANON.md",                       False),
    ("STANDARD.md",                  "STANDARD.md",                    False),
    ("STANDARD-REFERENCE.md",        "STANDARD-REFERENCE.md",          False),
    ("claude-settings-template.json", ".claude/settings.json",         False),
]

# Markers that mean "this repo already has an Architect" — adopt is for systems that
# have NONE. A repo carrying either is retrofit territory (ADR-0014), and running
# adopt on it would write a template role doc over an evolved practice.
ADOPTED_MARKERS = ["session.py", "CANON.md", "STANDARD.md"]


def target_own_content(adopt_dir: Path) -> list[str]:
    """What the `--adopt` target already owns, ignoring git's own directory and the
    spec file the intake wrote there moments ago. An empty list means the directory is
    FRESH — so every "it already owns that surface" omission in ADOPT_COPY_PLAN would
    fire against a file that does not exist. See `adopt_in_place`'s plan selection."""
    ignore = {".git", ".DS_Store", "bootstrap-spec.json"}
    return sorted(p.name for p in adopt_dir.iterdir() if p.name not in ignore)


def has_git_history(adopt_dir: Path) -> bool:
    """Does this directory already carry git history? The question that separates a
    system with an established layout from a folder that merely has files in it."""
    if not (adopt_dir / ".git").exists():
        return False
    head = subprocess.run(["git", "-C", str(adopt_dir), "rev-parse", "HEAD"],
                          text=True, capture_output=True)
    return head.returncode == 0


def select_copy_plan(adopt_dir: Path) -> list[tuple]:
    """Pick the copy plan from what the target ACTUALLY is, not from the verb.

    `poga new` on an empty folder ends with `bootstrap --adopt --create-remote`, so
    an empty directory was being handed ADOPT_COPY_PLAN — whose every omission is
    justified by the target already owning that surface. Against a folder that owns
    nothing, each omission fires for a file that does not exist, and the new system
    lands with no STATUS.md (the ADR-0021 surface `curate/reconcile.py` reads, so the
    system cannot reconcile) and no README.md. Found live when a new member was stood up.

    A bare `--adopt` on an empty directory is a FRESH install wearing an adopt flag.

    THE TEST IS GIT HISTORY, NOT LOOSE FILES. It used to be "does this directory
    contain anything", which got a new member wrong: its folder held a build brief and a
    bootstrap spec — the INPUTS to the bootstrap, written before the substrate existed
    — and one loose markdown file was enough to classify a brand-new system as an
    adopted one and strip STATUS.md and README.md out of its install. the operator's ruling,
    2026-08-18: the spec was wrong — an empty directory is not adopt-in-place, because
    there is nothing to adopt.

    History is the honest discriminator because it is what ADR-0067 actually assumes.
    Every ADOPT_COPY_PLAN omission defers to a system that already made a choice —
    already publishes a STATUS.md, already keeps ADRs in `docs/adr/`, already has a
    README that belongs to the project. A directory with no commits has made no such
    choices; there is nothing to defer to. An adopted member, like any other real repo, keeps the
    adopt plan (they have history); a folder someone dropped files into gets the full
    kit, and `adopt_in_place` still never overwrites a file that already exists.

    `.gitignore` is dropped from whichever plan is chosen: on this path the merge
    block in `adopt_in_place` is its single writer, because a target's own rules are
    load-bearing (an adopted member's `.gitignore` may be all that keeps irreplaceable data
    out of git, so a wholesale copy there is a data-loss bug, not a style regression).

    Kept as its own function so the DECISION is what the tests exercise — a test that
    re-derives the choice from the same inputs cannot catch the selection reverting
    (`verify-in-the-created-configuration`)."""
    plan = ADOPT_COPY_PLAN if has_git_history(adopt_dir) else COPY_PLAN
    return [e for e in plan if e[1] != ".gitignore"]

# The shared substrate copied VERBATIM from the federation root into every new member —
# never forked, never templated. The same set curate/push-substrate.py keeps refreshed
# (BYTE_IDENTICAL there), so a freshly-seeded member and a long-lived refreshed one hold
# identical bytes. Kept as ONE list used by both onboarding paths (fresh + adopt-in-place)
# rather than two hand-maintained copy blocks (P16).
SHARED_SUBSTRATE = ["session.py", "interpreter.py", "standard_check.py", "poga"]
# Must land executable — `poga` is invoked as a command. shutil.copyfile explicitly does
# NOT carry mode, so a seeded member would get a present-but-unrunnable launcher.
SUBSTRATE_EXECUTABLE = {"poga"}


SETTINGS_TEMPLATE = "claude-settings-template.json"


def install_settings(kit_src: Path, dest: Path, project_dir: Path) -> None:
    """Write a new project's `.claude/settings.json` from the settings FLOOR
    (`standard-settings.json`, rendered by `curate/gen_settings.py` with the project's own
    `session.config.json`) -- the same render `push-substrate` gives every member (ADR-0034).

    WI-0465: the kit's copy of the floor had drifted. It lacked the `WorktreeRemove` hook
    the floor gained with ADR-0070, so `standard_check.py` read every brand-new project as
    v1.4.0 "with a rollout to" the latest open, on its first session. Rendering from the
    floor here means a new project is stamped at the standard this checkout ships, and the
    kit copy can no longer hold an install back. The kit file stays the fallback for a
    tree without the generator."""
    try:
        curate = str(ROOT / "curate")
        if curate not in sys.path:
            sys.path.insert(0, curate)
        import gen_settings  # noqa: E402  (federation-side generator, beside this script)
        cfg_path = project_dir / "session.config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        dest.write_text(gen_settings.render(cfg), encoding="utf-8")
    except Exception as exc:
        print(f"  settings: could not render the floor ({type(exc).__name__}: {exc}) — "
              f"copied the kit template instead")
        shutil.copyfile(kit_src, dest)


def seed_shared_substrate(dest_dir: pathlib.Path) -> None:
    """Copy the shared harness + launcher into a newly-onboarded repo, preserving the
    executable bit where it is load-bearing."""
    for name in SHARED_SUBSTRATE + sessionlib_files():
        src, dest = ROOT / name, dest_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        if name in SUBSTRATE_EXECUTABLE:
            dest.chmod(dest.stat().st_mode | 0o111)


def sessionlib_files() -> list[str]:
    """The harness package `session.py` imports (ADR-0118), as repo-relative paths, read
    from this checkout's own tree exactly as `curate/push-substrate.py` derives it.

    Without it a freshly seeded member's `session.py` dies on its first line with
    `No module named 'sessionlib'`: the push path learned the package when session.py
    was split, and this seeding path did not (found by the WI-0453 drill)."""
    pkg = ROOT / "sessionlib"
    try:
        return sorted(f"sessionlib/{m.name}" for m in pkg.iterdir()
                      if m.suffix == ".py" and m.is_file())
    except OSError:
        return []


def merge_gitignore(existing: str, kit: str) -> str:
    """Append the kit's ignore rules that the target lacks — never replace.

    An adopted repo's `.gitignore` is usually load-bearing (it may be what excludes
    the system's irreplaceable data), so replacing it is unacceptable. But without
    the kit's rules the federation's own data surfaces — `users/` (the operator's
    profile), `proposed-edits/` (the inbox), `architect-learnings.md`, `.session-state/`
    — would be committed into the adopted repo, breaking the P3 data/system boundary
    the moment the Architect's first session runs.

    Compares on stripped non-comment lines so re-running adds nothing twice.
    """
    have = {ln.strip() for ln in existing.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}
    missing = [ln for ln in kit.splitlines()
               if ln.strip() and not ln.lstrip().startswith("#") and ln.strip() not in have]
    if not missing:
        return existing
    block = ("\n# --- federation substrate (added at adopt; ADR-0067) ---\n"
             "# Architect data that must never enter the system repo (P3).\n"
             + "\n".join(missing) + "\n")
    return existing.rstrip("\n") + "\n" + block


def die(msg: str) -> "NoReturn":
    sys.exit(f"bootstrap.py: {msg}")


def sh(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        # Surface the captured stderr — a swallowed "repo already exists" /
        # auth failure left nothing to debug from.
        die(f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def kit_version() -> str:
    """Read the kit version from bootstrap-kit/README.md — the single source of
    truth, so this script can never disagree with the kit it ships."""
    for line in (KIT / "README.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\*\*Kit version:\*\*\s*(v[0-9.]+)", line)
        if m:
            return m.group(1)
    return "v?"


# ---- identity derivation (ADR-0006) ---------------------------------------

def derive_ids(system_name: str) -> dict:
    system_id = re.sub(r"[^a-z0-9]+", "-", system_name.lower()).strip("-")
    return {
        "system_id": system_id,
        "architect_id": f"{system_id}-arch",
        "architect_name": f"{system_name} Architect",
    }


def detect_gh_account() -> str:
    # One invocation; gh has moved this output between stdout and stderr across
    # versions, so read both. (Previously ran gh twice and mixed the first
    # call's stdout with the second's stderr.)
    proc = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True)
    out = proc.stdout + proc.stderr
    # find the account line nearest an "Active account: true"
    acct = None
    last = None
    for line in out.splitlines():
        a = re.search(r"account (\S+)", line)
        if a:
            last = a.group(1)
        if "Active account: true" in line and last:
            acct = last
    return acct or last or ""


def owner_from_url(url: str | None) -> str:
    """The owner inside a repo URL: `https://github.com/owner/repo(.git)`, or an
    scp-style SSH URL (git at host:owner/repo), gives `owner`. Empty when the URL names no owner we can read. Lets an explicit `repo_url` answer the
    owner question without asking `gh` (WI-0452)."""
    if not url:
        return ""
    trimmed = url.strip().rstrip("/").removesuffix(".git")
    parts = re.split(r"[/:]", trimmed)
    return parts[-2] if len(parts) >= 2 else ""


def machine_name() -> str:
    """This machine's own name: the macOS ComputerName when there is one, else the
    hostname. Never raises — an empty string means neither command answered."""
    for cmd in (["scutil", "--get", "ComputerName"], ["hostname"]):
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True)
        except OSError:
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return ""


def machine_label(name: str) -> str:
    """A short label for a machine name: the first DNS label, punctuation folded to `-`.
    Only a DEFAULT — `poga init` offers it and the operator may type another."""
    base = name.split(".")[0]
    return re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-") or "this-machine"


def detect_machine(machine_map: dict) -> str:
    name = machine_name()
    return machine_map.get(name, name or "unknown")


def pins_interpreter() -> bool:
    """Whether an install on this host records `layout.interpreter` (WI-0468). Linux
    only: macOS already has its fixed-path default in `interpreter.PLATFORM_DEFAULT`,
    and its behaviour is unchanged. A function, not an inline test, so a test can take
    either branch on either machine."""
    return sys.platform.startswith("linux")


def find_install_interpreter() -> tuple[str, str]:
    """`(path, refusal)` — the interpreter a Linux install pins ("" off Linux), or the
    one-line reason none qualifies. Never raises; `poga init` turns the refusal into its
    own InitError, `resolve_install_interpreter` into `die`."""
    if not pins_interpreter():
        return "", ""
    import interpreter  # noqa: PLC0415  (repo root; a sibling of this file)
    path, rejected = interpreter.first_on_path()
    if path:
        return path, ""
    floor = ".".join(map(str, interpreter.MIN_VERSION))
    found = ("found only: " + ", ".join(rejected)) if rejected else "found none"
    return "", (f"POGA needs Python {floor} or newer as `python3` on PATH ({found}). "
                f"Install a newer python3 (e.g. your distro's python3 package) or put "
                f"one first on PATH, then re-run — nothing created.")


def resolve_install_interpreter() -> str:
    """The interpreter a Linux install pins, or "" off Linux. Dies — before anything is
    written — when no `python3` on PATH is new enough.

    WI-0468. Linux has no `/usr/bin/python3` default (it is the distro's, and pinning
    the fleet to it was never argued for), so without this a Linux project ran under
    whatever `python3` its shell resolved on the day — the WI-0328 drift, one OS over.
    Refusing is the honest answer to "none qualifies": installing a project whose
    harness cannot parse on the only python available would fail on its first verb
    instead of here, with the reason."""
    path, refusal = find_install_interpreter()
    if refusal:
        die(refusal)
    return path


def pin_interpreter(dest_dir: Path, path: str) -> bool:
    """Record `path` as `layout.interpreter` in `dest_dir`'s session.config.json and say
    so. Returns whether it wrote. Never overrides an interpreter the config already
    declares (either tier `interpreter.declared` reads) — a member's own choice wins."""
    if not path:
        return False
    import interpreter  # noqa: PLC0415
    cfg_path = dest_dir / "session.config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(cfg, dict) or interpreter.declared(cfg):
        return False
    layout = cfg.get("layout")
    if not isinstance(layout, dict):
        layout = cfg["layout"] = {}
    layout["interpreter"] = path
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"  interpreter: pinned {path} as layout.interpreter in session.config.json "
          f"(the first python3 >= 3.9 on PATH; change it there, WI-0468)")
    return True


def detect_timezone() -> str:
    """The system's IANA time zone, from `TZ`, then the `/etc/localtime` link, then
    `/etc/timezone`. Every candidate is checked against the zone database; anything that
    does not load falls through, and the last resort is UTC — a stated default, never a
    guess dressed up as a detection."""
    candidates = []
    tz_env = os.environ.get("TZ", "").lstrip(":")
    if tz_env:
        candidates.append(tz_env)
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    for c in candidates:
        try:
            ZoneInfo(c)
            return c
        except Exception:
            continue
    return "UTC"


# ---- where the values come from (WI-0453) ---------------------------------
#
# A render used to read the time zone and the machine map ONLY from the federation's own
# session.config.json, and died without it. They now resolve in one order everywhere:
# the spec first (what `poga init` writes from the local defaults it detected and the
# operator confirmed), then the federation config when it declares them (an existing
# fleet renders exactly as it did), then this machine. The profile and the doc-link base
# are optional the same way.

def fed_config() -> dict:
    """The federation's session config, or {} when there is none or it does not parse.
    A missing federation config is an ordinary state for a new install, not an error."""
    try:
        data = json.loads(FED_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_timezone(spec: dict) -> str:
    return spec.get("timezone") or fed_config().get("timezone") or detect_timezone()


def resolve_machine_map(spec: dict) -> dict:
    """Spec, else the federation's map, else empty — and THIS machine is always on it.
    A machine missing from an inherited map would render its raw name everywhere; adding
    it leaves every existing entry exactly as it was."""
    mm = spec.get("machine_map") or fed_config().get("machine_map") or {}
    mm = dict(mm)
    name = machine_name()
    if name and name not in mm:
        mm[name] = machine_label(name)
    return mm


def default_federation_ref() -> str:
    """The doc-link base for cross-references into this checkout's ADRs: the checkout's
    own `origin` when it is a web URL (links then work from anywhere), else the checkout's
    path on this machine (links work locally, which is all a local-only install needs)."""
    proc = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                          text=True, capture_output=True)
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    if url.startswith("https://"):
        return f"{url.removesuffix('.git')}/blob/main"
    return str(ROOT)


def with_defaults(spec: dict) -> dict:
    """A copy of `spec` with every defaultable key filled. Only `system_name` and
    `mission_prose` stay required: they are the judgment. The operator's name and id, and
    the doc-link base, have one defensible default each (WI-0453)."""
    s = dict(spec)
    if not s.get("user_name"):
        s["user_name"] = fed_config().get("user_name") or getpass.getuser() or "operator"
    if not s.get("user_id"):
        s["user_id"] = re.sub(r"[^a-z0-9]+", "-", s["user_name"].lower()).strip("-") or "operator"
    if not s.get("federation_repo_url"):
        s["federation_repo_url"] = default_federation_ref()
    return s


REQUIRED_SPEC_KEYS = ["system_name", "mission_prose"]


def profile_source(spec: dict) -> Path | None:
    """The operator profile to seed, if one exists: the federation checkout's, then the
    local federation's (`local_federation` in the spec, written by `poga init`). None
    means seed the blank kit profile — a profile is optional (WI-0453)."""
    bases = [ROOT]
    if spec.get("local_federation"):
        bases.append(Path(spec["local_federation"]).expanduser())
    for base in bases:
        p = base / "users" / spec["user_id"] / "profile.md"
        if p.is_file():
            return p
    return None


def blank_profile_text(user_id: str) -> str:
    return (KIT / "user-profile-template.md").read_text(encoding="utf-8") \
        .replace("`operator`", f"`{user_id}`")


def seed_profile(spec: dict, dest_dir: Path) -> None:
    """Write the operator profile into the new repo's (gitignored) users/ tree."""
    dest = dest_dir / "users" / spec["user_id"] / "profile.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = profile_source(spec)
    if src is not None:
        shutil.copyfile(src, dest)
    else:
        dest.write_text(blank_profile_text(spec["user_id"]), encoding="utf-8")


# ---- template rendering ----------------------------------------------------

def strip_guidance(text: str) -> str:
    """Remove the `> Token guidance ...` blockquote blocks from a template.
    A guidance block is a maximal run of consecutive blockquote (`>`) lines that
    contains the marker 'token guidance' (every template guidance block opens with
    it). Non-guidance blockquotes (e.g. the handoff's `> *Onboarding-session entry
    above is the seed*`) are preserved."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith(">"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith(">"):
                j += 1
            run = lines[i:j]
            if any("token guidance" in r.lower() for r in run):
                if out and out[-1].strip() == "":   # drop the blank line before the block
                    out.pop()
                i = j
                if i < len(lines) and lines[i].strip() == "":   # and the blank line after
                    i += 1
                continue
            out.extend(run)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def expand_list_tokens(text: str, list_tokens: dict) -> str:
    """For tokens that sit on their own list/row line, expand to N lines (or drop
    the line if empty, or substitute a placeholder). Handles `- <<T>>` bullets,
    `N. <<T>>` numbered items, and `| <<T>> | | |` table rows."""
    out: list[str] = []
    for line in text.split("\n"):
        hit = next((t for t in list_tokens if f"<<{t}>>" in line), None)
        if hit is None:
            out.append(line)
            continue
        items, empty_text = list_tokens[hit]
        prefix = line[: line.index("<<")]
        if not items:
            if empty_text is not None:
                out.append(line.replace(f"<<{hit}>>", empty_text))
            # else: drop the line entirely
            continue
        mnum = re.match(r"^(\s*)(\d+)\.\s$", prefix)
        if line.lstrip().startswith("|"):                       # table row
            out.extend(items)                                   # items are full rows
        elif mnum:                                              # numbered list
            indent, start = mnum.group(1), int(mnum.group(2))
            out.extend(f"{indent}{start + k}. {it}" for k, it in enumerate(items))
        else:                                                   # bullet (or other) list
            out.append(line.replace(f"<<{hit}>>", items[0]))
            bullet_prefix = prefix if prefix.strip() else "- "
            out.extend(f"{bullet_prefix}{it}" for it in items[1:])
    return "\n".join(out)


def render(template_text: str, scalars: dict, list_tokens: dict) -> str:
    text = expand_list_tokens(template_text, list_tokens)
    for k, v in scalars.items():
        text = text.replace(f"<<{k}>>", v)
    text = strip_guidance(text)
    return text


# ---- render context --------------------------------------------------------

def render_context(spec: dict, ids: dict, clone_dir: Path) -> tuple[dict, dict, str, str]:
    """Build the token maps a kit render needs. Pure — reads no state but the spec,
    the federation's session.config.json when it has one, this machine, and the clock.

    No `gh` call: the repo owner comes from the spec (`repo_owner`, or the owner inside
    `repo_url`). A local-only system has none, and its docs say so (WI-0452)."""
    tz_long = resolve_timezone(spec)
    machine_map = resolve_machine_map(spec)
    tz = ZoneInfo(tz_long)
    now = datetime.now(tz)
    tz_short = now.strftime("%Z")
    machine = detect_machine(machine_map)
    owner = spec.get("repo_owner") or owner_from_url(spec.get("repo_url"))
    repo = spec.get("repo_name") or ids["system_id"]
    if spec.get("repo_url"):
        repo_location = f"`{spec['repo_url']}`"
    elif owner:
        repo_location = f"`{owner}/{repo}` (private)"
    else:
        repo_location = ("local only — no remote yet (add one any time with "
                         "`git remote add origin <url>`)")

    # The declared Data root is the CANONICAL (/Users) view — an install may be
    # reached through a different mount path on another machine.
    if spec.get("canonical_install_dir"):
        data_root = spec["canonical_install_dir"]
    elif spec.get("canonical_projects_dir") and spec.get("friendly_folder"):
        data_root = f"{spec['canonical_projects_dir']}/{spec['friendly_folder']}"
    else:
        data_root = str(clone_dir)

    machine_labels = ", ".join(dict.fromkeys(machine_map.values()))
    orch = spec.get("orchestrator_agent")
    role_clause = (
        f"My system has a user-facing orchestrator agent named {orch}; the agent is the runtime, I am the Architect."
        if orch else
        "My system has no orchestrator agent — it is internal-only or template-driven, not user-facing."
    )
    readme_clause = (
        f"The user-facing runtime is {orch}; this repo holds the Architect's system surface, not the runtime."
        if orch else
        "This system has no orchestrator agent; the Architect is the only entity in scope."
    )

    scalars = {
        "ARCHITECT_NAME": ids["architect_name"],
        "ARCHITECT_ID": ids["architect_id"],
        "SYSTEM_ID": ids["system_id"],
        "SYSTEM_NAME": spec["system_name"],
        "DATA_ROOT": data_root,
        "USER_ID": spec["user_id"],
        "USER_NAME": spec["user_name"],
        "ORCHESTRATOR_AGENT": orch or "(none)",
        "TODAY": now.strftime("%Y-%m-%d"),
        "TODAY_TIME": now.strftime(f"%H:%M {tz_short}"),
        "TODAY_END_TIME": now.strftime(f"%H:%M {tz_short}"),   # bootstrap seed is ~instant
        "TODAY_DURATION": "<1m",
        "TODAY_MACHINE": machine,
        "TIMEZONE": tz_short,
        "TIMEZONE_LONG": tz_long,
        "MACHINE_LABELS": machine_labels,
        "MACHINE_MAP": json.dumps(machine_map, ensure_ascii=False),
        "FEDERATION_REPO_REF": spec["federation_repo_url"],
        "REPO_OWNER": owner or "(none)",
        "REPO_NAME": repo,
        "REPO_LOCATION": repo_location,
        "MISSION_PROSE": spec["mission_prose"],
    }
    list_tokens = {
        "SCOPE_DO_BULLETS": (spec.get("scope_do", []), None),
        "SCOPE_DONT_BULLETS": (spec.get("scope_dont", []), None),
        "VOICE_STYLE_BULLETS": (spec.get("voice", []), None),
        "SYSTEM_SPECIFIC_PRINCIPLES": (spec.get("system_principles", []),
                                       "*None yet; populated as the Architect develops practice.*"),
        "SYSTEM_SPECIFIC_SESSION_STEPS": (spec.get("session_steps", []),
                                          "*None yet; the standard rituals in [`STANDARD.md`](STANDARD.md) are the whole protocol.*"),
        "EXTRA_ARTIFACT_ROWS": (spec.get("extra_artifact_rows", []), None),
        "EXTRA_INPUT_ROWS": (spec.get("extra_input_rows", []), None),
        "EXTRA_GATE_BULLETS": (spec.get("extra_gate_bullets", []), None),
        "ARCHITECT_SPECIFIC_NEXT_BULLETS": (spec.get("next_bullets", []), None),
        "ARCHITECT_SPECIFIC_OPEN_QUESTIONS": (spec.get("open_questions", []), "*None.*"),
        "ARCHITECT_SPECIFIC_PENDING": (spec.get("pending", []), "*None.*"),
    }
    return scalars, list_tokens, role_clause, readme_clause


# ---- preflight -------------------------------------------------------------

def preflight(spec: dict, ids: dict, clone_dir: Path) -> None:
    """Validate every input the render will need, BEFORE anything irreversible.

    `gh repo create` used to run first, so a missing spec key or user profile died
    halfway and orphaned the remote repo; the re-run then failed on "already exists".
    Nothing in materialize_kit should be able to fail on missing input.
    """
    missing_keys = [k for k in REQUIRED_SPEC_KEYS if not spec.get(k)]
    if missing_keys:
        die(f"spec is missing required key(s): {', '.join(missing_keys)} — nothing created.")
    if not spec.get("install_dir") and not (spec.get("projects_dir") and spec.get("friendly_folder")):
        die("spec must provide either `install_dir` or both `projects_dir` and "
            "`friendly_folder` — nothing created.")
    # No profile check: a missing profile is seeded blank from the kit (WI-0453).
    missing_kit = [k for k, _, _ in COPY_PLAN if not (KIT / k).is_file()]
    if missing_kit:
        die(f"bootstrap kit is missing file(s): {', '.join(missing_kit)} — nothing created.")

    # preflight guard (add-structural-guard-on-recurrence): the session.config
    # inbox MUST be repo-root-relative. An absolute inbox silently breaks
    # session.py's sweep — it resolves `ROOT / <abs>` to <abs>, off the real
    # clone root (esp. /Users-vs-/Volumes). This bug recurred twice (a
    # hand-run onboarding, then this harness's first run from a stale template), so fail
    # loud here — in plan mode too — rather than ship it again.
    scalars, list_tokens, _, _ = render_context(spec, ids, clone_dir)
    cfg_inbox = json.loads(
        render((KIT / "session.config.json").read_text(encoding="utf-8"), scalars, list_tokens)
    ).get("inbox", "")
    if cfg_inbox.startswith("/"):
        die(f"session.config.json inbox rendered absolute ({cfg_inbox!r}); the kit "
            f"template must use a repo-root-relative path. Fix bootstrap-kit/session.config.json.")


# ---- materialize (the bootstrap-only phase) --------------------------------

def materialize_kit(spec: dict, ids: dict, clone_dir: Path) -> None:
    """Render the bootstrap kit into an existing, empty clone, then commit + push it
    and stage the portfolio registration.

    This is the ONE phase `poga restore` does not share (ADR-0061 D1): a restored
    system's role doc, ADRs and registries are authored artifacts already carried in
    git history, so re-rendering templates over them would replace real content.
    The clone itself is created by poga_cli's `acquire` phase, which BOTH verbs run.
    """
    spec = with_defaults(spec)
    preflight(spec, ids, clone_dir)
    pinned_python = resolve_install_interpreter()   # WI-0468: Linux only; dies if none
    scalars, list_tokens, role_clause, readme_clause = render_context(spec, ids, clone_dir)
    kit_ver = kit_version()

    # --- copy + render kit files ---
    for kit_name, dest_rel, do_sub in COPY_PLAN:
        dest_rel = dest_rel or f"{ids['architect_id']}.md"
        dest = clone_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = KIT / kit_name
        if do_sub:
            file_scalars = dict(scalars)
            file_scalars["ORCHESTRATOR_AGENT_CLAUSE"] = readme_clause if dest_rel == "README.md" else role_clause
            dest.write_text(render(src.read_text(encoding="utf-8"), file_scalars, list_tokens), encoding="utf-8")
        elif kit_name == SETTINGS_TEMPLATE:
            install_settings(src, dest, clone_dir)
        else:
            shutil.copyfile(src, dest)

    pin_interpreter(clone_dir, pinned_python)

    # The shared substrate — session.py (harness), standard_check.py (ADR-0047 member-side
    # self-check), poga (ADR-0060 lane launcher) — verbatim from the federation root,
    # never forked; all three wired via the settings floor.
    seed_shared_substrate(clone_dir)

    # --- inbox + profile under the data root (co-located in the clone; gitignored) ---
    for sub in ("pending", "applied", "rejected", "withdrawn"):
        (clone_dir / "proposed-edits" / ids["architect_id"] / sub).mkdir(parents=True, exist_ok=True)
    seed_profile(spec, clone_dir)

    # --- initial commit + push (the new repo is the Architect's own; commit freely) ---
    sh(["git", "add", "-A"], cwd=clone_dir)
    sh(["git", "commit", "-m",
        f"feat: stand up {ids['architect_id']} system repo from bootstrap kit {kit_ver}\n\n"
        f"Onboarded via the deterministic bootstrap harness (ADR-0025, ADR-0061).\n\n"
        f"Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"], cwd=clone_dir)
    sh(["git", "push", "-u", "origin", "main"], cwd=clone_dir)

    # --- ask to be registered; never write the federation's roster ourselves (P13) ---
    request_portfolio_registration(spec, ids, spec.get("orchestrator_agent"))


# ---- adopt-in-place (the ADR-0067 engine path) -----------------------------

def resolve_from_invocation(path_str: str) -> Path:
    """Resolve a path the user typed against where they actually stood when they ran
    `poga` — not this process's cwd. The `poga` wrapper `cd`s into the federation
    checkout before exec'ing `poga_cli.py`, so by the time this runs, plain cwd is the
    federation repo, not the project the user is standing in. `POGA_INVOKED_FROM`
    (exported by the wrapper) carries the real directory; invoked directly (`python3
    bootstrap.py` / `poga_cli.py`, no wrapper) there is no such `cd` to correct for, so
    falling back to plain cwd is already right. An absolute path needs neither."""
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p
    base = Path(os.environ.get("POGA_INVOKED_FROM", ".")).expanduser()
    return (base / p).resolve()


def load_adopt_spec(adopt_dir: Path, spec_arg: str | None, root: Path) -> dict:
    """Resolve + load the `--adopt` spec. An explicit `--spec` wins and resolves like
    the fresh-bootstrap path. Omitted (2026-07-24, the operator's ruling: adopt with no `--spec`
    finds its files on its own), this looks for `bootstrap-spec.json` inside the
    adopt target itself — the project declaring its own onboarding judgment inline —
    so `poga bootstrap --adopt` can run with no other arguments from inside it.

    A relative `--spec` is tried against the ADOPT TARGET first, then the folder the
    user was standing in, then `root`. Root-only was the old rule and it is the wrong
    one for the same reason it was wrong on the fresh path: `--spec bootstrap-spec.json`
    typed next to the file resolved into the federation checkout and reported the file
    missing from a directory the user never mentioned."""
    if spec_arg:
        spec_path = Path(spec_arg).expanduser()
        if not spec_path.is_absolute():
            for base in (adopt_dir, resolve_from_invocation("."), root):
                if (base / spec_path).is_file():
                    spec_path = base / spec_path
                    break
            else:
                spec_path = root / spec_path
    else:
        spec_path = adopt_dir / "bootstrap-spec.json"
        if not spec_path.is_file():
            die(f"no --spec given and no {spec_path.name} found in {adopt_dir} — "
                f"pass --spec <file>, or create {spec_path.name} in the project "
                f"(required keys: system_name, mission_prose; "
                f"everything else has a default). Nothing changed.")
    if not spec_path.is_file():
        die(f"bootstrap spec not found at {spec_path} — nothing changed.")
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"bootstrap spec at {spec_path} is not valid JSON: {e} — nothing changed.")


def init_and_commit_existing_content(adopt_dir: Path) -> bool:
    """`git init` a plain directory and commit whatever it already holds as the root
    commit (ADR-0067 update note, 2026-07-24): a directory with real content and no
    git history is what adopt is for, not a reason to stop and ask a human to run
    `git init` first. Returns True if a commit was made, False for an empty directory
    (still a valid repo — just nothing to capture as history yet)."""
    sh(["git", "init", "-q", "-b", "main"], cwd=adopt_dir)
    untracked = subprocess.run(["git", "-C", str(adopt_dir), "status", "--porcelain"],
                               text=True, capture_output=True).stdout.strip()
    if not untracked:
        return False
    sh(["git", "add", "-A"], cwd=adopt_dir)
    sh(["git", "commit", "-q", "-m",
        "chore: initial commit (federation bootstrap, ADR-0067)\n\n"
        "This directory held files and no git history when the Architect was installed. "
        "This commit captures them as the repo's root commit so the install itself "
        "lands as its own reviewable commit next. The files may be an existing project "
        "being adopted, or the inputs a fresh system was specified from (a brief, a "
        "bootstrap spec) — either way they predate the substrate and belong in the "
        "history before it."],
       cwd=adopt_dir)
    return True


def adopt_in_place(spec: dict, adopt_dir: Path, create_remote: bool = False,
                   dry_run: bool = False, register: bool = True) -> None:
    """Install the standard operating substrate into a directory that has real
    content and no Architect (ADR-0067). Engine-only: there is no repo-create/clone
    (`acquire`) phase — the target already exists — so this does not run the poga_cli
    pipeline. Never overwrites an existing file; merges .gitignore; remote creation
    is opt-in (`create_remote`) because it is outward-facing. A target with no git
    history yet is `git init`'d and its content committed as the root commit
    automatically (update note, 2026-07-24) rather than refused.

    `register=False` skips the roster request into this checkout's federation inbox:
    `poga init` registers the system in the LOCAL federation itself (WI-0453)."""
    # --- pre-flight: every input validated BEFORE anything irreversible. The clone
    # location keys are fresh-only: the adopt target path answers them.
    spec = with_defaults(spec)
    missing_keys = [k for k in REQUIRED_SPEC_KEYS if not spec.get(k)]
    if missing_keys:
        die(f"spec is missing required key(s): {', '.join(missing_keys)} — nothing created.")
    # Checked against the FRESH plan because either plan may be selected below, and
    # ADOPT_COPY_PLAN is a strict subset of it.
    missing_kit = [k for k, _, _ in COPY_PLAN if not (KIT / k).is_file()]
    if missing_kit:
        die(f"bootstrap kit is missing file(s): {', '.join(missing_kit)} — nothing created.")

    # --- adopt preflight: refuse anything that isn't the shape adopt is for ---
    if not adopt_dir.is_dir():
        die(f"--adopt target {adopt_dir} does not exist — nothing changed.")
    already = [m for m in ADOPTED_MARKERS if (adopt_dir / m).exists()]
    if already:
        die(f"--adopt target already carries Architect substrate ({', '.join(already)}) "
            f"— this is a RETROFIT (ADR-0014), not an adopt. Nothing changed.")

    # ADR-0067 update note (2026-07-24): a directory with real content and NO git
    # history at all is squarely what adopt is for, not an exception to it — refusing
    # and pointing at a manual `git init` puts a human step in the middle of an
    # otherwise-automated onboarding path (the operator's ruling: adopt ends in a working session
    # with working git, with no manual step handed back). needs_init defers
    # the actual `git init` + capture-as-root-commit past the dry-run gate below, same
    # as every other mutation this function makes; the dirty-tree check only applies
    # to a repo that ALREADY has history to be dirty against.
    needs_init = not (adopt_dir / ".git").exists()
    # Captured BEFORE `git init` runs — afterwards every target has history and the
    # question can no longer be asked. This is the same predicate `select_copy_plan`
    # decides on, read once here so the plan, the banner, and the commit subject can
    # never disagree about which job this was.
    had_history = has_git_history(adopt_dir)
    if not needs_init:
        # A dirty tree would be swept into the adopt commit, mixing someone's in-flight
        # work into an onboarding commit and making the adoption unreviewable.
        st = subprocess.run(["git", "-C", str(adopt_dir), "status", "--porcelain"],
                            text=True, capture_output=True)
        if st.returncode != 0:
            die(f"--adopt target: `git status` failed — nothing changed.")
        if st.stdout.strip():
            die(f"--adopt target has {len(st.stdout.strip().splitlines())} uncommitted "
                f"change(s). Commit or stash them first so the adoption lands as its own "
                f"reviewable commit — nothing changed.")

    # The operator profile is optional (WI-0453): an existing one is copied, a missing
    # one is seeded blank from the kit. So there is no profile refusal here any more.

    # --- PLAN SELECTION: pick from what is there, not from the verb (session ~118) ---
    # `poga new` in an empty folder lands HERE — the intake's last act is
    # `poga bootstrap --adopt --create-remote` — so the adopt plan was being applied to a
    # system that owns nothing yet. Every omission in ADOPT_COPY_PLAN is justified by the
    # target ALREADY owning that surface (see its comment: an adopted system's own STATUS.md, its
    # load-bearing .gitignore, its docs/adr/ layout). Against an empty directory each one
    # fires for a file that does not exist, and the new system lands with no STATUS.md —
    # the ADR-0021 cross-system surface `curate/reconcile.py` reads, so the system cannot
    # reconcile — and no README.md. Found live when a new member was stood up (WI-0080).
    #
    # A bare `--adopt` on an empty directory is a FRESH install wearing an adopt flag.
    # Selected on observed content rather than on a new flag so it holds however the
    # caller got here, and REPORTED either way rather than inferred silently
    # (`declare-what-a-check-assumes`). See `select_copy_plan` for the full argument.
    own = target_own_content(adopt_dir)
    copy_plan = select_copy_plan(adopt_dir)

    # --- derive + detect (no asking) ---
    ids = derive_ids(spec["system_name"])
    # WI-0468: on Linux, the interpreter is chosen (or the install refused) HERE, with the
    # other refusals — before the repo is initialised, before any file is written.
    pinned_python = resolve_install_interpreter()
    scalars, list_tokens, role_clause, readme_clause = render_context(spec, ids, adopt_dir)
    kit_ver = kit_version()
    # WI-0452: `gh` is asked for the owner ONLY when a remote is being created and neither
    # `repo_owner` nor `repo_url` names one. A local-only install never runs `gh`.
    owner = spec.get("repo_owner") or owner_from_url(spec.get("repo_url"))
    repo = spec.get("repo_name") or ids["system_id"]

    # preflight guard, as in preflight(): the rendered inbox must be repo-root-relative.
    cfg_inbox = json.loads(
        render((KIT / "session.config.json").read_text(encoding="utf-8"), scalars, list_tokens)
    ).get("inbox", "")
    if cfg_inbox.startswith("/"):
        die(f"session.config.json inbox rendered absolute ({cfg_inbox!r}); the kit "
            f"template must use a repo-root-relative path. Fix bootstrap-kit/session.config.json.")

    # The banner names what is HAPPENING, not which function is running. Calling a
    # brand-new system's install "adopt-in-place" describes the code path and misleads
    # about the work: there was nothing to adopt (operator, 2026-08-18). Same engine, two
    # honest names, chosen by the same predicate the plan is.
    in_place_kind = "adopt-in-place" if had_history else "build in place"
    print(f"== bootstrap ({in_place_kind}): {ids['architect_name']} "
          f"({ids['architect_id']}) ==")
    has_remote = bool(subprocess.run(["git", "-C", str(adopt_dir), "remote"],
                                     text=True, capture_output=True).stdout.strip())
    if create_remote and not has_remote and not owner:
        owner = detect_gh_account()
        if not owner and not dry_run:
            die("--create-remote needs a GitHub owner: set `repo_owner` (or `repo_url`) in "
                "the spec, or run `gh auth login` — nothing created.")
    if needs_init:
        print(f"  build into: {adopt_dir}  (no git history — will `git init` and commit "
              f"its existing content as the root commit)")
    else:
        print(f"  adopt into: {adopt_dir}  (existing repo — kept, not recreated)")
    print(f"  remote:     " + ("present" if has_remote
          else (f"none -> will create {owner or '<owner unresolved>'}/{repo} (private)"
                if create_remote
                else "none -> LEFT AS-IS, local only (pass --create-remote to create one)")))
    # Report the decision that was ACTUALLY made, from the same predicate that made it.
    # This line used to be derived from loose-file count while the plan was selected on
    # the same basis; now that history is the discriminator, deriving the sentence any
    # other way would print a confident description of a choice nobody made.
    if had_history:
        print(f"  plan:       adopt (existing repo with {len(own)} "
              f"entr{'y' if len(own) == 1 else 'ies'} of its own — its surfaces are "
              f"left alone)")
    elif own:
        print(f"  plan:       fresh (no git history — installing the full kit, including "
              f"STATUS.md and README.md; the {len(own)} file"
              f"{'' if len(own) == 1 else 's'} already here "
              f"{'is' if len(own) == 1 else 'are'} committed as the root commit)")
    else:
        print(f"  plan:       fresh (target is empty — installing the full kit, "
              f"including STATUS.md and README.md)")
    skips = [d or f"{ids['architect_id']}.md" for _, d, _ in copy_plan
             if (adopt_dir / (d or f"{ids['architect_id']}.md")).exists()]
    print(f"  existing files preserved: {', '.join(skips) if skips else '(none conflict)'}")
    print(f"  data root:  {scalars['DATA_ROOT']}")
    print(f"  machine:    {scalars['TODAY_MACHINE']}   tz: {scalars['TIMEZONE']} "
          f"({scalars['TIMEZONE_LONG']})   stamp: {scalars['TODAY']} {scalars['TODAY_TIME']}")
    print(f"  kit:        {kit_ver}")
    if dry_run:
        print("  [dry-run] nothing created, no files written, portfolio untouched.")
        return

    if needs_init:
        if init_and_commit_existing_content(adopt_dir):
            print(f"  git init:   initialized {adopt_dir} and committed its existing "
                  f"content as the root commit")
        else:
            print(f"  git init:   initialized {adopt_dir} (no existing content to commit)")

    if create_remote and not has_remote:
        origin = spec.get("repo_url") or f"https://github.com/{owner}/{repo}.git"
        sh(["gh", "repo", "create", f"{owner}/{repo}", "--private"])
        sh(["git", "-C", str(adopt_dir), "remote", "add", "origin", origin])
        has_remote = True

    # --- copy + render kit files (never clobber — its house, not our pipes) ---
    # Read before the copy: the pin is written only into a config THIS run rendered,
    # never into one the target already had (adopt never edits the target's own files).
    cfg_was_present = (adopt_dir / "session.config.json").exists()
    for kit_name, dest_rel, do_sub in copy_plan:
        dest_rel = dest_rel or f"{ids['architect_id']}.md"
        dest = adopt_dir / dest_rel
        # Adopt NEVER clobbers. The preflight already refused a repo carrying Architect
        # substrate, so a collision here is the system's own file (its README, its
        # STATUS) — its house, not our pipes (ADR-0023).
        if dest.exists():
            print(f"  keep:  {dest_rel} (already present — not overwritten)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = KIT / kit_name
        if do_sub:
            file_scalars = dict(scalars)
            file_scalars["ORCHESTRATOR_AGENT_CLAUSE"] = readme_clause if dest_rel == "README.md" else role_clause
            dest.write_text(render(src.read_text(encoding="utf-8"), file_scalars, list_tokens), encoding="utf-8")
        elif kit_name == SETTINGS_TEMPLATE:
            install_settings(src, dest, adopt_dir)
        else:
            shutil.copyfile(src, dest)

    if not cfg_was_present:
        pin_interpreter(adopt_dir, pinned_python)

    # --- .gitignore: MERGE (the target's own rules are load-bearing) ---
    gi = adopt_dir / ".gitignore"
    kit_gi = (KIT / "gitignore-template").read_text(encoding="utf-8")
    if gi.exists():
        merged = merge_gitignore(gi.read_text(encoding="utf-8"), kit_gi)
        if merged != gi.read_text(encoding="utf-8"):
            gi.write_text(merged, encoding="utf-8")
            print("  merge: .gitignore (appended the federation data exclusions)")
        else:
            print("  keep:  .gitignore (already covers the federation exclusions)")
    else:
        gi.write_text(kit_gi, encoding="utf-8")

    # The shared substrate — harness + self-check + lane launcher, verbatim (never forked)
    seed_shared_substrate(adopt_dir)

    # --- inbox + profile under the data root (co-located; gitignored) ---
    for sub in ("pending", "applied", "rejected", "withdrawn"):
        (adopt_dir / "proposed-edits" / ids["architect_id"] / sub).mkdir(parents=True, exist_ok=True)
    seed_profile(spec, adopt_dir)

    # --- the first session's one-time writes, made here so they ship in this commit ---
    # WI-0464: a new project's FIRST `session.py start` froze the seed handoff into
    # `sessions/pre-journal-archive.md` and rewrote `session-handoff.md` as a compiled
    # view (the ADR-0051 cutover). Neither was in the install commit, and the archive is
    # not a harness-owned path (`_harness_owned`), so no sweep ever committed it: main
    # carried authored-looking dirt from its first minute. Running the installed
    # harness's own `compile` here makes that cutover part of the install, so a session
    # start writes only what every session writes (its journal and the compiled views),
    # which the harness commits itself (ADR-0091). A fresh build only: an adopted repo's
    # handoff is its own, and its cutover stays with its first session.
    if not had_history and (adopt_dir / "session.py").is_file():
        proc = subprocess.run([sys.executable, "session.py", "compile"], cwd=str(adopt_dir),
                              text=True, capture_output=True, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            # Not fatal: the install is otherwise complete, and the first session does
            # this same cutover itself. Said, so the dirt it leaves is not a surprise.
            print(f"  WARNING: `session.py compile` exited {proc.returncode} — the first "
                  f"session start will freeze the handoff instead, and leave "
                  f"sessions/pre-journal-archive.md to commit by hand: "
                  f"{(proc.stderr or proc.stdout).strip()[-400:]}")
        else:
            print("  compile: froze the seed handoff into sessions/pre-journal-archive.md "
                  "and compiled the views, so the first session finds them committed")

    # --- commit (the target repo is the Architect's own; commit freely) ---
    sh(["git", "add", "-A"], cwd=adopt_dir)
    # The subject states which of the two jobs this actually was. "adopt" written over a
    # brand-new system is a false claim in permanent history — the one place a wrong
    # word cannot be edited later.
    if had_history:
        subject = f"feat: adopt {ids['architect_id']} into this repo"
        body = (f"Federation adopt-in-place (ADR-0067): the system existed and worked; "
                f"it had no Architect. Installs the standard operating substrate only — "
                f"every pre-existing file was preserved and .gitignore was merged, not "
                f"replaced.")
    else:
        subject = f"feat: install {ids['architect_id']} (bootstrap)"
        body = (f"Federation bootstrap built in place (ADR-0067 engine): this directory "
                f"had no git history, so there was no existing system to defer to and "
                f"the full kit was installed, STATUS.md and README.md included. Any "
                f"files already present — a brief, the bootstrap spec — were committed "
                f"as the root commit first and left untouched.")
    sh(["git", "commit", "-m",
        f"{subject} (bootstrap kit {kit_ver})\n\n{body}\n\n"
        f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"], cwd=adopt_dir)

    pushed = False
    if has_remote:
        branch = subprocess.run(["git", "-C", str(adopt_dir), "branch", "--show-current"],
                                text=True, capture_output=True).stdout.strip() or "main"
        sh(["git", "push", "-u", "origin", branch], cwd=adopt_dir)
        pushed = True

    # --- ask to be registered; never write the federation's roster ourselves (P13) ---
    brief = (request_portfolio_registration(spec, ids, spec.get("orchestrator_agent"))
             if register else None)

    print(f"\n== done ==")
    if pushed:
        print(f"  pushed: {owner}/{repo}")
    else:
        print(f"  committed locally — no remote, nothing pushed "
              f"(re-run with --create-remote, or add an origin by hand).")
    if brief is not None:
        print(f"  registration requested: {brief.name}")
        print(f"  filed in the Federation Architect's inbox — this system is NOT on the roster")
        print(f"  until that brief is applied in a Federation Architect session (P13).")


def portfolio_row(spec: dict, ids: dict, orch: str | None) -> str:
    """The single definition of a member's roster row — used to compose the
    registration request, and quoted verbatim in it so applying is transcription."""
    orch_cell = orch if orch else spec.get("orchestrator_note", "(none)")
    return (f"| {spec['system_name']} | {orch_cell} | {ids['architect_name']} | "
            f"`{ids['system_id']}` | `{ids['architect_id']}` | Active (bootstrap) |")


def request_portfolio_registration(spec: dict, ids: dict, orch: str | None) -> Path:
    """File a registration REQUEST into the Federation Architect's inbox instead of
    editing `portfolio.md` here.

    WHY THIS IS NOT A DIRECT EDIT (fixed session ~118, WI-0080's sibling). The roster
    is a federation registry whose single writer is the Federation Architect
    ([P13](principles/master.md#p13--single-writer-per-state)). This function runs
    inside the NEW system's onboarding — under `poga new`, inside an agent session
    standing in the new system's own folder — so writing `portfolio.md` from here is a
    second writer reaching into another role's file. Staging-but-not-committing did not
    fix that: the write had already happened, it left the federation's working tree
    dirty for whatever session swept next, and a lane could not commit it at all
    (WI-0056). The receipt ritual ([ADR-0013](adr/0013-receipt-ritual.md)) is the
    sanctioned cross-writer mechanism, and this is exactly its case.

    It cannot be an `apply: auto` brief: `session.py apply-briefs` verifies
    `expected-base-version` against the target's `**Version:**` line, and `portfolio.md`
    is a table with no version. That is fine — putting a system on the roster is a
    judgment the Federation Architect makes, not a mechanical patch, so it is authored
    `manual` with `manual-reason: attended` and surfaces at the next federation startup.

    The inbox is gitignored, so filing here writes nothing into any tracked tree.
    Returns the brief path."""
    today = datetime.now(ZoneInfo(resolve_timezone(spec))).strftime("%Y-%m-%d")
    inbox = ROOT / "proposed-edits" / "federation-arch" / "pending"
    inbox.mkdir(parents=True, exist_ok=True)
    brief = inbox / f"{today}-register-{ids['system_id']}.md"
    row = portfolio_row(spec, ids, orch)
    brief.write_text(
        f"---\n"
        f"edit-id: {today}-register-{ids['system_id']}\n"
        f"target-file: portfolio.md\n"
        f"apply: manual\n"
        f"manual-reason: attended\n"
        f"---\n\n"
        f"# Register {spec['system_name']} ({ids['architect_id']}) on the roster\n\n"
        f"`{ids['system_id']}` was stood up on {today} by the bootstrap harness and is "
        f"installed, committed, and running. It is **not on the roster** until this "
        f"brief is applied.\n\n"
        f"The harness does not edit `portfolio.md` itself: the roster's single writer is "
        f"the Federation Architect (P13), and this request was written from inside the "
        f"new system's own onboarding session.\n\n"
        f"## Apply\n\n"
        f"Insert this row as the last row of the systems table in `portfolio.md` "
        f"(immediately before the blank line preceding `## Notes`), and set the "
        f"`Last updated:` line to the date you apply it:\n\n"
        f"~~~\n{row}\n~~~\n\n"
        f"## Verify\n\n"
        f"~~~\ngrep -F '`{ids['architect_id']}`' portfolio.md\n~~~\n",
        encoding="utf-8")
    return brief


# ---- legacy entry point ----------------------------------------------------

def main() -> int:
    """`python3 bootstrap.py --spec <spec> [--dry-run]` — kept working, delegates to
    the ADR-0061 shared pipeline so there is exactly ONE orchestration. The
    `--adopt` path (ADR-0067) runs the engine directly: adopt has no acquire phase
    (the repo already exists), so the pipeline has nothing to add."""
    import argparse
    ap = argparse.ArgumentParser(
        description="Deterministic fresh-Architect bootstrap (ADR-0025). "
                    "Legacy alias for `poga bootstrap`; also carries --adopt (ADR-0067).")
    ap.add_argument("--spec", help="Path to the bootstrap spec JSON. Required for a fresh "
                    "bootstrap; for --adopt, defaults to bootstrap-spec.json found inside "
                    "the adopt target.")
    ap.add_argument("--dry-run", "--plan", dest="dry_run", action="store_true",
                    help="Plan only; create/write nothing. (alias: --plan, poga's spelling.)")
    ap.add_argument("--into", help="Override the install path from the spec.")
    ap.add_argument("--adopt", nargs="?", const=".", metavar="PATH",
                    help="ADOPT-IN-PLACE (ADR-0067): install the substrate into a directory "
                         "that has real content but no Architect. Skips repo-create/clone, "
                         "never overwrites an existing file, merges .gitignore. Auto-`git "
                         "init`s + commits existing content as the root commit if the target "
                         "has no git history yet. Omit PATH to adopt the directory you're "
                         "standing in.")
    ap.add_argument("--create-remote", action="store_true",
                    help="With --adopt: if the target has no git remote, create the private "
                         "GitHub repo and set it as origin. Outward-facing — off by default.")
    args = ap.parse_args()

    if args.adopt is not None:
        adopt_dir = resolve_from_invocation(args.adopt)
        spec = load_adopt_spec(adopt_dir, args.spec, ROOT)
        adopt_in_place(spec, adopt_dir, create_remote=args.create_remote, dry_run=args.dry_run)
        return 0

    if not args.spec:
        ap.error("--spec is required (or use --adopt to onboard an existing project)")

    import poga_cli
    argv = ["bootstrap", "--spec", args.spec]
    if args.dry_run:
        argv.append("--plan")
    if args.into:
        argv += ["--into", args.into]
    return poga_cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
