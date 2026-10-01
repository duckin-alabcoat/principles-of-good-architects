#!/usr/bin/env python3
"""poga lifecycle engine — `bootstrap` and `restore` on ONE code path (ADR-0061).

The binding claim of [ADR-0061](adr/0061-poga-unified-lifecycle-cli-and-state-manifest.md)
D1 is that **restore IS bootstrap plus a hydration step**. That is not a slogan about
code reuse; it is the whole argument for why restore can be trusted. Restore is
exercised *never* in ordinary operation — drill #1's root finding — so a restore that
owns its own code path rots between drills no schedule can hold. Sharing the path with
bootstrap means every onboarding is also a partial restore drill.

So both verbs run the SAME seven phases, in the same order:

    resolve -> identity -> acquire -> materialize -> hydrate -> verify -> report

and differ in exactly two of them:

    materialize   bootstrap renders the kit into an empty clone; restore no-ops
                  (a restored system's files are already IN git history).
    hydrate       restore verifies the non-derivable data state-manifest.<system>.json
                  declares, and reports what the backup layer still owes (ADR-0094);
                  bootstrap has nothing to hydrate (a new system has no prior state).

Five of the seven phases are therefore exercised by every single bootstrap, which is
the exercise-frequency payoff D1 was after.

THE HONESTY CONTRACT (D2). `restore` never reports plain success. Credentials travel
through neither git nor backup media by design, so the run ends by printing the
credentials manifest as a checklist and exiting

    incomplete — N credentials to re-provision

with exit code 3. Exit 0 means N == 0 AND every data leg landed and verified. The named
anti-pattern is a restore that exits green while the restored system cannot authenticate
to anything.

WHY RESTORE VERIFIES AND DOES NOT RETRIEVE ([ADR-0094](adr/0094-a-project-declares-its-data-the-backup-layer-owns-retrieval.md)).
A project declares WHAT its non-derivable data is and HOW to know it came back correct;
the backup layer owns where it is kept and how to retrieve it. So
the manifest names no backup medium and this file drives none. Hydrate reads the
declaration, checks each path at the install root, and reports whatever is not there
yet as owed to the backup layer. Its checks are typed existence tests built here from
the manifest's fields. No manifest string is ever executed
([P20](principles/master.md#p20--untrusted-data-stays-untrusted)).

Usage:
  python3 poga_cli.py bootstrap --spec <spec.json> [--plan] [--into <dir>]
  python3 poga_cli.py bootstrap --spec <spec.json> --adopt <path> [--create-remote] [--plan]
                              # adopt-in-place (ADR-0067) — engine-only, no acquire phase
  python3 poga_cli.py bootstrap --adopt          # adopt the CURRENT directory; --spec
                              # defaults to bootstrap-spec.json found inside it
  python3 poga_cli.py init [PATH] [--yes] [--name N] [--purpose P] [--create-remote]
                              # local-only project + local federation (WI-0453)
  python3 poga_cli.py share     # send shareable lessons between local projects
  python3 poga_cli.py restore <system> [--plan] [--into <dir>]
                              [--scenario machine-loss|site-loss]

Normally reached through the `poga` wrapper: `poga bootstrap …` / `poga restore …`.

Exit codes (machine-checkable, so the honesty contract is testable):
  0  complete            — everything landed; no credentials owed
  1  failure             — a phase failed; nothing half-applied
  2  usage               — bad invocation / missing artifact
  3  incomplete          — data legs done, N credentials owed to a human (D2)
"""

from __future__ import annotations

import os as _os
import sys as _sys

# WI-0328 — the interpreter pin, before this module does anything. The gate spawns
# this file with `sys.executable`, so under a land it already inherits the right
# answer; run BY HAND it inherited the operator's PATH instead, and on one machine those
# two were a package-manager Python and the system /usr/bin/python3, which disagree about the suite's
# verdict. Appended to `sys.path`, never prepended: this is a lookup for one module,
# not a claim to shadow anything already importable.
_REPO = _os.path.dirname(_os.path.abspath(__file__))
if _REPO not in _sys.path:
    _sys.path.append(_REPO)
try:
    import interpreter as _interpreter
except Exception:  # a member without the module runs exactly as it did before
    _interpreter = None
else:
    # ONLY when this file IS the program. Re-execing a process that merely imported
    # us would replace somebody else's program with ours — and it did: `import
    # session` from a test module restarted the whole test runner under the pin.
    if __name__ == "__main__":
        _interpreter.ensure(_REPO)

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import bootstrap as bs
import poga_evidence as evidence

ROOT = Path(__file__).resolve().parent

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INCOMPLETE = 0, 1, 2, 3

SCENARIOS = {"machine-loss": "local", "site-loss": "offsite"}


class Abort(Exception):
    """A phase refused to continue. Carries the exit code to surface."""

    def __init__(self, msg: str, code: int = EXIT_FAIL):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@dataclass
class Leg:
    """One data_sources entry, after the hydrate phase has had its say."""

    name: str
    kind: str
    automation: str
    dest: Path
    paths: list[str]
    status: str = "pending"      # done | human-owed | blocked | pending
    detail: str = ""
    checklist: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    verb: str
    plan: bool
    spec: dict
    spec_path: Path
    ids: dict
    target: Path
    repo_url: str
    root_commit: str | None
    scenario: str = "machine-loss"
    create_remote: bool = False   # WI-0452: create the remote ONLY when asked
    state_manifest: dict | None = None
    creds_manifest: dict | None = None
    legs: list[Leg] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    acquired: bool = False        # did THIS run create the checkout? (never delete a pre-existing one)
    materialized: bool = False

    @property
    def system_id(self) -> str:
        return self.ids["system_id"]


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(phase: str, msg: str) -> None:
    print(f"  [{phase}] {msg}", flush=True)


def run_git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise Abort(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


# ---------------------------------------------------------------------------
# phase 1 — resolve
# ---------------------------------------------------------------------------


def load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise Abort(f"{what} not found at {path}", EXIT_USAGE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Abort(f"{what} at {path} is not valid JSON: {e}", EXIT_USAGE)


def spec_path_from_arg(spec_arg: str) -> Path:
    """Resolve a `--spec` argument. Shared by the ordinary bootstrap path and the
    `--adopt` dispatch in `main()`, which both need the same resolution.

    A relative path resolves against WHERE THE USER WAS STANDING first, and only then
    against ROOT. This used to be ROOT-only, which made the obvious command fail in the
    least helpful way available: standing in the project folder next to the spec,
    `--spec bootstrap-spec.json` reported "not found at
    /Users/.../principles-of-good-architects/bootstrap-spec.json" — a path the user
    never typed and has no reason to know about, because the `poga` wrapper has already
    `cd`'d here by the time this runs (the operator's ruling, 2026-08-18: the verb finds the
    files it needs in the current folder without being told).

    ROOT stays as the FALLBACK, not the default: `--spec bootstrap-spec.widget.json`
    naming a spec that conventionally lives in the federation repo keeps working from
    anywhere, and `restore` reaches its spec through the state manifest this same way.
    Invocation-dir first is the right precedence because a file the user can see from
    where they are standing is the one they meant."""
    spec_path = Path(spec_arg).expanduser()
    if spec_path.is_absolute():
        return spec_path
    here = bs.resolve_from_invocation(spec_arg)
    return here if here.is_file() else ROOT / spec_path


def derive_repo_url(spec: dict, ids: dict, need: bool = True) -> str:
    """The repo this spec names. Extracted so `cmd_bootstrap`'s engine routing and
    `phase_resolve`'s Ctx cannot disagree about which repo the target is being compared
    against — the routing decision turns on exactly that comparison (P16).

    WI-0452: an explicit `repo_url` is honoured FIRST, before any owner is resolved, so a
    spec that names its remote never runs `gh`. Only with no URL does the owner matter:
    `repo_owner` from the spec, and then — only when `need` says a remote is actually
    wanted — the active `gh` account. With `need=False` and nothing named, the answer is
    "" (a local-only system), and `gh` is never called."""
    if spec.get("repo_url"):
        return spec["repo_url"]
    repo_name = spec.get("repo_name") or ids["system_id"]
    repo_owner = spec.get("repo_owner")
    if not repo_owner:
        if not need:
            return ""
        repo_owner = bs.detect_gh_account()
    if not repo_owner:
        raise Abort("could not determine repo owner (set `repo_owner` or `repo_url` in the "
                    "spec, or run `gh auth login`)")
    return f"https://github.com/{repo_owner}/{repo_name}.git"


def wants_remote(spec: dict, flag: bool = False) -> bool:
    """Remote creation is opt-in on EVERY path (WI-0452): the `--create-remote` flag, or
    `"create_remote": true` in the spec. Nothing else — not a brand-new folder, not an
    absent remote — turns it on."""
    return bool(flag) or spec.get("create_remote") is True


DEFAULT_SPEC_NAME = "bootstrap-spec.json"


def discover_spec() -> Path | None:
    """The spec sitting in the folder the user ran `poga` from, if there is one.

    `poga bootstrap` with no arguments, standing in a folder that holds a
    `bootstrap-spec.json`, has exactly one sensible meaning. It used to answer
    "bootstrap needs --spec <spec.json>" while the spec was in plain sight one
    directory listing away. `--adopt` already worked this way (`load_adopt_spec`,
    2026-07-24); the fresh path never learned it."""
    here = bs.resolve_from_invocation(DEFAULT_SPEC_NAME)
    return here if here.is_file() else None


def phase_resolve(verb: str, spec_arg: str | None, system: str | None,
                  into: str | None, scenario: str, plan: bool,
                  create_remote: bool = False) -> Ctx:
    """Load the spec (+ manifests for restore), derive identity, fix the install path.

    Shared by both verbs. For `restore` the spec is reached THROUGH the state
    manifest's `spec` field, which is what makes the manifest and the build input one
    connected artifact set rather than two things that can drift apart silently.
    """
    state_manifest = creds_manifest = None

    if verb == "restore":
        if not system:
            raise Abort("restore needs a <system> (e.g. `poga restore federation`)", EXIT_USAGE)
        sm_path = ROOT / f"state-manifest.{system}.json"
        state_manifest = load_json(sm_path, f"state manifest for '{system}'")
        declared_data(state_manifest, sm_path.name)   # refuse an old shape before building
        if state_manifest.get("system") != system:
            raise Abort(f"{sm_path.name} declares system "
                        f"{state_manifest.get('system')!r}, not {system!r}")
        spec_path = ROOT / state_manifest["spec"]
        cm_path = ROOT / f"credentials-manifest.{system}.json"
        creds_manifest = load_json(cm_path, f"credentials manifest for '{system}'")
        step("resolve", f"state manifest: {sm_path.name}")
        step("resolve", f"credentials manifest: {cm_path.name}")
    else:
        spec_path = spec_path_from_arg(spec_arg) if spec_arg else discover_spec()
        if spec_path is None:
            where = bs.resolve_from_invocation(".")
            # `poga new`, not `poga intake`. The interview is reached through the `new)`
            # branch of poga's dispatch table (it routes to `cmd_intake`, which is where the
            # old name came from); `intake` is not a verb and never was. So the one abort
            # whose job is to hand a spec-less operator the thing that AUTHORS a spec named
            # it in a form poga answers with "'intake' is not a poga verb" (WI-0158).
            raise Abort(f"no {DEFAULT_SPEC_NAME} in {where}, and no --spec given.\n"
                        f"  Write the spec into that folder and re-run `poga bootstrap` "
                        f"with no arguments, or run `poga new` to have one authored.",
                        EXIT_USAGE)

    spec = load_json(spec_path, "bootstrap spec")
    step("resolve", f"spec: {spec_path.name}")

    if verb == "bootstrap" and spec.get("bootstrappable") is False:
        raise Abort(
            f"{spec_path.name} declares `bootstrappable: false` — this system's repo already "
            f"exists and its files are authored, not kit-rendered. Use `poga restore "
            f"{spec.get('system_name', '<system>').lower()}` instead.", EXIT_USAGE)

    if not spec.get("system_name"):
        raise Abort(f"{spec_path.name} is missing `system_name`", EXIT_USAGE)
    ids = bs.derive_ids(spec["system_name"])

    if state_manifest and ids["system_id"] != state_manifest["system"]:
        raise Abort(f"identity mismatch: spec derives system-id {ids['system_id']!r} but the "
                    f"state manifest declares {state_manifest['system']!r}")

    target = resolve_target(spec, into, spec_path)
    create = verb == "bootstrap" and wants_remote(spec, create_remote)
    repo_url = derive_repo_url(spec, ids)

    step("resolve", f"identity: {ids['architect_name']} ({ids['architect_id']})")
    step("resolve", f"install into: {target}")

    return Ctx(verb=verb, plan=plan, spec=spec, spec_path=spec_path, ids=ids,
               target=target, repo_url=repo_url, root_commit=spec.get("root_commit"),
               scenario=scenario, state_manifest=state_manifest, creds_manifest=creds_manifest,
               create_remote=create)


def resolve_target(spec: dict, into: str | None, spec_path: Path | None = None) -> Path:
    """Where the checkout lands. `--into` always wins.

    D4 / drill-#1 finding 5: never a hardcoded home. The spec carries a DEFAULT for the
    authoring machine; a restore onto a fresh machine (different account, different path
    view — a local /Users path vs a mounted /Volumes one) passes `--into` and the default is ignored.
    A relative `--into` resolves against where the user actually invoked `poga` from
    (`bs.resolve_from_invocation`), not this process's cwd — the `poga` wrapper has
    already `cd`'d into the federation checkout by the time this runs.

    LAST RESORT: THE SPEC'S OWN FOLDER. A spec that declares no location at all used to
    abort with "spec must provide either `install_dir` or both `projects_dir` and
    `friendly_folder`" — a demand for information the situation already answered, since
    the spec was sitting in the folder the system is being built in. Falling back to
    `spec_path.parent` is not a guess: a spec authored INTO a project folder is that
    project's spec, which is exactly how `poga new` writes one and how a hand-written
    member spec is laid out. A spec that lives in the federation repo still needs its own
    `install_dir`/`projects_dir` — the fallback would resolve to the federation root,
    which `phase_acquire` refuses as an existing foreign checkout rather than building
    into it.
    """
    if into:
        return bs.resolve_from_invocation(into)
    if spec.get("install_dir"):
        return Path(spec["install_dir"]).expanduser()
    projects = spec.get("projects_dir")
    friendly = spec.get("friendly_folder")
    if projects and friendly:
        return Path(projects) / friendly
    if spec_path is not None:
        return spec_path.resolve().parent
    raise Abort("spec must provide either `install_dir` or both `projects_dir` and "
                "`friendly_folder` (or pass --into <dir>)", EXIT_USAGE)


# ---------------------------------------------------------------------------
# phase 2 — identity  (D4 finding 9: the pin)
# ---------------------------------------------------------------------------


def phase_identity(ctx: Ctx) -> None:
    """Pin the repo identity BEFORE anything is built.

    Finding 9: the federation has a public, sanitized sibling repo. A restore that
    rebuilt from it would produce a plausible-looking system missing exactly the content
    that was scrubbed — the worst failure mode available, because it looks like success.
    The root-commit hash is the strongest identity a repo has: names change, remotes
    change, the root commit cannot.
    """
    step("identity", f"repo: {ctx.repo_url}")

    if ctx.verb == "restore":
        if not ctx.root_commit:
            raise Abort(f"{ctx.spec_path.name} has no `root_commit` — restore refuses to build "
                        f"without an identity pin (D4 finding 9: it could rebuild from the "
                        f"public sanitized sibling repo and never know).")
        step("identity", f"pinned root commit: {ctx.root_commit[:12]}")

    proc = subprocess.run(["git", "ls-remote", ctx.repo_url, "HEAD"],
                          text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        hint = detail[-1] if detail else "no detail"
        if ctx.verb == "restore":
            raise Abort(f"cannot reach {ctx.repo_url} ({hint}).\n"
                        f"  A private repo needs credentials FIRST — see the `github-access` "
                        f"entry in credentials-manifest.{ctx.system_id}.json (`gh auth login`).")
        if ctx.create_remote:
            step("identity", "remote does not exist yet — bootstrap will create it "
                             "(--create-remote)")
        else:
            step("identity", "remote does not exist, and creating one was not asked for")
        ctx.notes.append("remote-absent")
    else:
        step("identity", "remote reachable")
        if ctx.verb == "bootstrap":
            step("identity", "remote ALREADY EXISTS — will reuse it, not re-create "
                             "(idempotent: no duplicate/orphan repo)")
            ctx.notes.append("remote-exists")


# ---------------------------------------------------------------------------
# phase 3 — acquire  (D4: idempotent, resumable, atomic-or-cleanup)
# ---------------------------------------------------------------------------


def checkout_state(target: Path, repo_url: str) -> str:
    """Classify what is already on disk at `target`. Never mutates.

    Returns one of:
      absent      nothing there
      ours        a healthy clone of repo_url
      skeleton    a POISONED partial clone (finding 7) — `.git` exists but there is no
                  HEAD. An interrupted `git clone` leaves exactly this, and every retry
                  then fails with "already exists", so the CLI must recognise and clear
                  it rather than dying forever.
      plain       a directory with NO git history at all. Not a repo, so there is
                  nothing here that could belong to anyone else's project — the
                  distinction `foreign` used to swallow. `bootstrap` builds into it in
                  place; `restore` still refuses (see `phase_acquire`).
      foreign     a checkout of a DIFFERENT repo — never touched, never deleted
    """
    if not target.exists():
        return "absent"
    if not (target / ".git").exists():
        return "plain"
    head = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"],
                          text=True, capture_output=True)
    if head.returncode != 0:
        return "skeleton"
    origin = subprocess.run(["git", "-C", str(target), "remote", "get-url", "origin"],
                            text=True, capture_output=True)
    if origin.returncode != 0:
        return "foreign"
    if normalize_url(origin.stdout.strip()) != normalize_url(repo_url):
        return "foreign"
    return "ours"


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/").removesuffix(".git").lower()


def root_commit_of(target: Path) -> str:
    proc = run_git(["rev-list", "--max-parents=0", "HEAD"], cwd=target)
    return proc.stdout.strip().splitlines()[0]


def phase_acquire(ctx: Ctx) -> None:
    """Get the repo on disk at ctx.target, atomically or not at all."""
    state = checkout_state(ctx.target, ctx.repo_url)
    step("acquire", f"target state: {state}")

    if state == "foreign":
        raise Abort(f"{ctx.target} is a checkout of another repo, not {ctx.repo_url} — "
                    f"refusing to touch it. Move it aside or pass --into <other dir>.")

    if state == "plain":
        # `bootstrap` never reaches here with a plain directory — `main()` routes that
        # to the in-place engine, which is the whole point of the plain/foreign split.
        # `restore` is a different promise: it rebuilds a repo that already exists
        # elsewhere, verified against the root-commit pin, and a directory with no git
        # history cannot satisfy that pin. Refusing is correct, and saying which of the
        # two reasons applies is the part the old shared message got wrong.
        raise Abort(f"{ctx.target} exists but has no git history — restore rebuilds from "
                    f"the pinned remote and will not write into it. Move it aside or "
                    f"pass --into <other dir>.")

    if state == "ours":
        step("acquire", "existing checkout reused (resumed run)")
        if ctx.verb == "restore":
            verify_pin(ctx, owned=False)
        return

    if ctx.verb == "bootstrap" and "remote-absent" in ctx.notes and not ctx.create_remote:
        # WI-0452: no step of first use creates a remote unless asked. The spec named a
        # remote that does not exist, so there is nothing to clone and nothing this run
        # may create — say both ways forward instead of guessing which one was meant.
        raise Abort(f"{ctx.repo_url} does not exist, and nothing creates a remote unless "
                    f"asked.\n  Re-run with --create-remote (or set \"create_remote\": true "
                    f"in the spec) to create it, or drop `repo_url`/`repo_owner` from the "
                    f"spec to build a local-only repo in place.", EXIT_USAGE)

    if ctx.plan:
        if state == "skeleton":
            step("acquire", f"[plan] would clear the poisoned partial clone at {ctx.target}")
        step("acquire", f"[plan] would clone {ctx.repo_url} -> {ctx.target}")
        return

    if state == "skeleton":
        step("acquire", "clearing poisoned partial clone (interrupted earlier run)")
        shutil.rmtree(ctx.target)

    if ctx.verb == "bootstrap" and "remote-absent" in ctx.notes and ctx.create_remote:
        step("acquire", "creating the remote repo (private)")
        bs.sh(["gh", "repo", "create", slug_from_url(ctx.repo_url), "--private"])

    clone_atomically(ctx)
    ctx.acquired = True

    if ctx.verb == "restore":
        verify_pin(ctx, owned=True)


def slug_from_url(url: str) -> str:
    """`https://github.com/owner/repo.git` -> `owner/repo` (what `gh repo create` wants)."""
    trimmed = normalize_url(url)
    parts = trimmed.split("/")
    return "/".join(parts[-2:])


def clone_atomically(ctx: Ctx) -> None:
    """Clone into a sibling staging dir, then rename into place.

    Finding 7 in its structural form: because the checkout only ever appears at its final
    path as a COMPLETE clone, an interruption can no longer leave a skeleton `.git` that
    poisons every retry. The failure path removes the staging dir; the target is never
    touched until git has succeeded.
    """
    ctx.target.parent.mkdir(parents=True, exist_ok=True)
    staging = ctx.target.with_name(ctx.target.name + f".poga-partial-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    step("acquire", f"cloning -> {staging.name} (staged)")
    try:
        proc = subprocess.run(["git", "clone", ctx.repo_url, str(staging)],
                              text=True, capture_output=True)
        if proc.returncode != 0:
            raise Abort(f"clone failed: {(proc.stderr or proc.stdout).strip()}")
        os.replace(staging, ctx.target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    step("acquire", f"clone landed at {ctx.target}")


def verify_pin(ctx: Ctx, owned: bool) -> None:
    """Check the checkout really is the pinned repo. Refuse loudly if not."""
    actual = root_commit_of(ctx.target)
    if actual != ctx.root_commit:
        if owned:
            shutil.rmtree(ctx.target, ignore_errors=True)
            extra = " (the clone this run created has been removed)"
        else:
            extra = " (pre-existing checkout left untouched)"
        raise Abort(
            f"IDENTITY MISMATCH — refusing to restore.{extra}\n"
            f"  expected root commit {ctx.root_commit}\n"
            f"  actual   root commit {actual}\n"
            f"  This is the D4 finding-9 guard: the clone is a DIFFERENT repository than the "
            f"spec pins (most likely the public sanitized sibling). Restoring from it would "
            f"produce a system silently missing whatever the sanitizer stripped.")
    step("acquire", f"identity pin verified: root commit {actual[:12]}")


# ---------------------------------------------------------------------------
# phase 4 — materialize  (bootstrap only)
# ---------------------------------------------------------------------------


def phase_materialize(ctx: Ctx) -> None:
    """Render the bootstrap kit into a fresh clone. No-op for restore.

    This is the first of the two phases the verbs differ on. A restored system's role
    doc, ADRs, and registries are AUTHORED artifacts already carried in git history —
    re-rendering the kit over them would replace real content with templates.
    """
    if ctx.verb == "restore":
        step("materialize", "skipped — restore's files come from git history, not the kit")
        return

    role_doc = ctx.target / f"{ctx.ids['architect_id']}.md"
    if role_doc.exists():
        step("materialize", "already materialized (role doc present) — skipped (resumed run)")
        return

    if ctx.plan:
        step("materialize", f"[plan] would render {len(bs.COPY_PLAN)} kit files "
                            f"(kit {bs.kit_version()}), seed the inbox + profile, commit and push")
        return

    step("materialize", f"rendering bootstrap kit {bs.kit_version()}")
    bs.materialize_kit(ctx.spec, ctx.ids, ctx.target)
    ctx.materialized = True
    step("materialize", "kit rendered, initial commit pushed, portfolio.md staged")


# ---------------------------------------------------------------------------
# phase 5 — hydrate  (restore only)
# ---------------------------------------------------------------------------


def resolve_dest(dest: str, ctx: Ctx) -> Path:
    """Resolve a manifest `dest` against the actual install path.

    The schema forbids a hardcoded home, so `dest` is a token or a relative path. The
    only tokens honoured are this system's own root (`${FEDERATION_ROOT}` for the
    federation) and the generic `${SYSTEM_ROOT}`. An unrecognised token is an ERROR, not
    a best guess — a restore that guesses where data goes is worse than one that stops.
    """
    token = "${" + ctx.system_id.replace("-", "_").upper() + "_ROOT}"
    resolved = dest.replace(token, str(ctx.target)).replace("${SYSTEM_ROOT}", str(ctx.target))
    if "${" in resolved:
        unknown = re.findall(r"\$\{[^}]*\}", resolved)
        raise Abort(f"unresolved token(s) {unknown} in dest {dest!r} — the only tokens this "
                    f"restore can resolve are {token} and ${{SYSTEM_ROOT}}.")
    path = Path(resolved)
    return path if path.is_absolute() else ctx.target / path


def declared_data(manifest: dict, name: str) -> dict:
    """The manifest's `data` block (schema 2.0.0), or a refusal that names why.

    ADR-0094 is a breaking change. A 1.x manifest groups `data_sources` by backup medium
    and carries retrieval steps the project no longer owns. Reading it anyway would
    quietly restore the ownership the ADR removed, so it is refused by name, with the
    way out: nothing is guessed from the old shape.
    """
    if "data_sources" in manifest:
        raise Abort(f"{name} is the old (1.x) shape: it lists `data_sources` grouped by "
                    f"backup medium, which ADR-0094 retired. Schema 2.0.0 declares the data "
                    f"set once under `data` and names no backup medium. state-manifest.md "
                    f"shows the new shape.", EXIT_USAGE)
    data = manifest.get("data")
    if (not isinstance(data, dict) or not isinstance(data.get("dest"), str)
            or not isinstance(data.get("paths"), list)):
        raise Abort(f"{name} has no `data` block with a `dest` and a `paths` list "
                    f"(schema 2.0.0, ADR-0094).", EXIT_USAGE)
    return data


def phase_hydrate(ctx: Ctx) -> None:
    """Verify the declared data set. No-op for bootstrap.

    ADR-0094: the backup layer PLACES the data, and the project VERIFIES it. So this
    phase never retrieves anything. It checks each declared path at the install root:
    whatever is missing is owed to the backup layer, and whatever is present but fails
    its `contains` check is blocked. The honesty contract is unchanged, because either
    outcome keeps the exit at 3.
    """
    if ctx.verb == "bootstrap":
        step("hydrate", "skipped — a new system has no prior state to hydrate")
        return

    data = declared_data(ctx.state_manifest, f"state-manifest.{ctx.system_id}.json")
    declared = data["paths"]
    if not declared:
        step("hydrate", "manifest declares NO non-derivable state — nothing to hydrate "
                        "(a positive claim, not an omission)")
        return

    leg = Leg(name=f"{ctx.system_id} declared data", kind="declared",
              automation="placed by the backup layer",
              dest=resolve_dest(data["dest"], ctx), paths=[p["path"] for p in declared])
    ctx.legs.append(leg)

    if ctx.plan:
        leg.detail = f"[plan] would verify {len(declared)} declared path(s) under {leg.dest}"
        step("hydrate", f"{leg.name}: {leg.detail}")
        count_hydrated_briefs(ctx)
        return

    missing = [p["path"] for p in declared if not present(leg.dest / p["path"])]
    wrong = [f"{p['path']} lacks {c}" for p in declared
             if p["path"] not in missing for c in p.get("contains", [])
             if not (leg.dest / p["path"] / c).exists()]
    if missing:
        leg.status = "human-owed"
        leg.detail = f"not placed yet: {', '.join(missing)}"
        leg.checklist = [
            f"Have the backup layer place {', '.join(missing)} under {leg.dest}. Where "
            f"this data is kept and how to retrieve it is the backup layer's "
            f"(runbooks/backup-retrieval.md), not this project's (ADR-0094).",
            f"Re-run `poga restore {ctx.system_id}`. It is resumable, and it checks "
            f"what landed.",
        ]
        step("hydrate", f"{leg.name}: OWED — {len(missing)} of {len(declared)} path(s) "
                        f"not placed yet")
    elif wrong:
        leg.status = "blocked"
        leg.detail = f"present but not correct: {'; '.join(wrong)}"
        step("hydrate", f"{leg.name}: BLOCKED — {leg.detail}")
    else:
        leg.status = "done"
        leg.detail = f"all {len(declared)} declared path(s) placed"
        step("hydrate", f"{leg.name}: {leg.detail}")

    count_hydrated_briefs(ctx)


def count_hydrated_briefs(ctx: Ctx) -> None:
    """Count briefs that came off backup media, so the report can name them.

    ADR-0065 D3: these are NOT held. They are gated by the check that already exists —
    `apply-briefs` refuses any brief whose `expected-base-version` does not equal the
    target role doc's current version, and that role doc arrived over git from a remote
    this restore identity-verified. So an unverified brief is gated by a verified
    artifact, and the realistic failure (a stale or already-applied brief sitting in a
    backup) is refused automatically: applying it would have bumped the very version it
    claims to expect.

    This function therefore observes; it does not intervene. Reporting the count is the
    whole job — the operator should know unverified briefs are queued, without a human
    step being wedged into a disaster rebuild.
    """
    inbox = restored_inbox(ctx)
    if inbox is None or not inbox.is_dir():
        return
    briefs = [p for p in inbox.glob("*.md") if p.name != "index.md"]
    if not briefs:
        return
    ctx.notes.append(f"hydrated-briefs:{len(briefs)}")
    step("hydrate", f"{len(briefs)} brief(s) hydrated into the inbox — not applied here; "
                    f"each is version-checked against the git-verified role doc at first session")


def restored_inbox(ctx: Ctx) -> Path | None:
    """The restored repo's own inbox dir, read from ITS session.config.json.

    Read from the restored tree rather than assumed, because the inbox path is a
    per-system config value. A repo that declares no inbox (a fresh Architect) has
    nothing to report.
    """
    cfg = ctx.target / "session.config.json"
    if not cfg.is_file():
        return None
    try:
        inbox = json.loads(cfg.read_text(encoding="utf-8")).get("inbox")
    except (OSError, json.JSONDecodeError):
        return None
    if not inbox:
        return None
    path = Path(inbox)
    return path if path.is_absolute() else ctx.target / path


# ---------------------------------------------------------------------------
# phase 6 — verify
# ---------------------------------------------------------------------------


def phase_verify(ctx: Ctx) -> None:
    """Check what actually landed. A restore's own claim is not evidence (P18)."""
    if ctx.plan:
        step("verify", "[plan] skipped")
        return

    if ctx.verb == "bootstrap":
        role_doc = ctx.target / f"{ctx.ids['architect_id']}.md"
        if not role_doc.is_file():
            raise Abort(f"post-build check failed: {role_doc} is missing")
        step("verify", "role doc present")
        return

    for leg in ctx.legs:
        if leg.status != "done":
            continue
        missing = [p for p in leg.paths if not present(leg.dest / p)]
        if missing:
            leg.status = "blocked"
            leg.detail += f"; landed but EMPTY/MISSING after pull: {', '.join(missing)}"
            step("verify", f"{leg.name}: {len(missing)} path(s) missing or empty")
        else:
            step("verify", f"{leg.name}: all {len(leg.paths)} path(s) present and non-empty")


def present(path: Path) -> bool:
    if path.is_dir():
        return any(path.iterdir())
    return path.is_file() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# phase 7 — report  (D2: restore never reports plain success)
# ---------------------------------------------------------------------------


def phase_report(ctx: Ctx) -> int:
    say()
    say("=" * 72)

    if ctx.verb == "bootstrap":
        say(f"bootstrap: {ctx.ids['architect_name']} ({ctx.ids['architect_id']})")
        say(f"  repo:   {ctx.repo_url}")
        say(f"  path:   {ctx.target}")
        if ctx.plan:
            say("\n[plan] nothing was created or written.")
            return EXIT_OK
        if ctx.materialized:
            say("\nportfolio.md edited + STAGED in the federation repo — not committed.")
            say("Commit it within this Federation Architect session (P13 single-writer).")
        say("\ncomplete.")
        return EXIT_OK

    say(f"restore: {ctx.ids['architect_name']} ({ctx.system_id})")
    say(f"  scenario: {ctx.scenario}  ->  {SCENARIOS[ctx.scenario]} source(s)")
    say(f"  path:     {ctx.target}")

    say("\nData legs")
    if not ctx.legs:
        say("  (none — the manifest declares no non-derivable state)")
    for leg in ctx.legs:
        say(f"  [{leg.status.upper():>10}] {leg.name}")
        say(f"               kind={leg.kind}  automation={leg.automation}  dest={leg.dest}")
        if leg.detail:
            say(f"               {leg.detail}")
        if leg.checklist:
            say("               steps a human must perform:")
            for i, line in enumerate(leg.checklist, 1):
                say(f"                 {i}. {line}")

    creds = ctx.creds_manifest.get("credentials", []) if ctx.creds_manifest else []
    human = [c for c in creds if c.get("transport") == "human-only"]

    say("\nCredentials checklist (ordered — each gates the next)")
    for i, c in enumerate(creds, 1):
        mark = "HUMAN" if c.get("transport") == "human-only" else "auto "
        say(f"  {i}. [{mark}] {c['name']}")
        for line in c.get("provision", []):
            say(f"         - {line}")

    blocked = [l for l in ctx.legs if l.status in ("blocked", "human-owed", "pending")]

    # ADR-0065 D4: the report names what restore deliberately did NOT trust, not just
    # what it did not do. The operator is told the boundary rather than assumed to know it.
    say("\nTrust boundary (ADR-0065)")
    say("  activated: NOTHING — no persona adopted, no hook installed, no restored code run.")
    hydrated = next((n for n in ctx.notes if n.startswith("hydrated-briefs:")), None)
    if hydrated:
        n = hydrated.split(":", 1)[1]
        say(f"  inbox:     {n} brief(s) came off backup media and are NOT applied here.")
        say("             Each is version-checked at first session — a brief whose")
        say("             expected-base-version disagrees with the git-verified role doc")
        say("             is refused, not applied. No action needed from you.")
    else:
        say("  inbox:     no briefs hydrated.")

    say()
    say("=" * 72)
    if ctx.plan:
        say(f"[plan] nothing was created, written, or pulled.")
        say(f"[plan] a real run would owe: {len(blocked)} data leg(s) needing a human, "
            f"{len(human)} credential(s) to re-provision.")
        return EXIT_OK

    if blocked:
        say(f"INCOMPLETE — {len(blocked)} data leg(s) not landed by this run "
            f"(see the steps above), and {len(human)} credential(s) to re-provision.")
        return EXIT_INCOMPLETE
    if human:
        # D2, verbatim: this is the defined shape of a CORRECT restore exit.
        say(f"incomplete — {len(human)} credentials to re-provision")
        say("Data legs landed and verified. The system cannot authenticate until the "
            "checklist above is worked.")
        return EXIT_INCOMPLETE

    say("complete — data landed and no credentials owed.")
    return EXIT_OK


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def execute(verb: str, spec_arg: str | None, system: str | None, into: str | None,
            scenario: str, plan: bool, create_remote: bool = False) -> int:
    banner = f"poga {verb}" + (f" {system}" if system else "")
    say(f"== {banner}{' [plan]' if plan else ''} ==")
    ctx = phase_resolve(verb, spec_arg, system, into, scenario, plan, create_remote)
    phase_identity(ctx)
    phase_acquire(ctx)
    phase_materialize(ctx)
    phase_hydrate(ctx)
    phase_verify(ctx)
    return phase_report(ctx)


# ---------------------------------------------------------------------------
# discovery — `poga restore --list`
# ---------------------------------------------------------------------------


def portfolio_system_ids() -> list[str]:
    """Every system-id registered in portfolio.md.

    The roster is the authority for name resolution (ADR-0006), so it is also the
    honest denominator for "what fraction of the portfolio can actually be restored."
    Matched on the `system-id` / `system-id-arch` column pair, which no other table in
    the file has — so the disposition tables further down can't leak in.
    """
    path = ROOT / "portfolio.md"
    if not path.is_file():
        return []
    pattern = re.compile(r"`([a-z0-9][a-z0-9-]*)`\s*\|\s*`\1-arch`")
    return list(dict.fromkeys(pattern.findall(path.read_text(encoding="utf-8"))))


def restorable_systems() -> dict[str, dict]:
    """What each system has on file. A system is restorable only with all three."""
    found: dict[str, dict] = {}
    for sm in sorted(ROOT.glob("state-manifest.*.json")):
        system = sm.name[len("state-manifest."):-len(".json")]
        if system == "schema":
            continue
        spec = None
        try:
            spec = json.loads(sm.read_text(encoding="utf-8")).get("spec")
        except (OSError, json.JSONDecodeError):
            pass
        found[system] = {
            "state": True,
            "creds": (ROOT / f"credentials-manifest.{system}.json").is_file(),
            "spec": bool(spec) and (ROOT / spec).is_file(),
        }
    return found


def cmd_list() -> int:
    """Say what can be rebuilt, and — just as importantly — what cannot.

    A restore CLI that only lists its successes would hide the thing that matters most
    on a fresh machine: a system with no manifest has an RPO of total data loss, and
    the operator needs to learn that BEFORE the disaster, not during it.
    """
    have = restorable_systems()
    roster = portfolio_system_ids()

    say("Restorable systems — `poga restore <system> --into <dir>` rebuilds ONE system.")
    say()
    if have:
        say(f"  {'system':<28} {'spec':<6} {'state':<6} {'creds':<6} status")
        for system, f in sorted(have.items()):
            ready = all(f.values())
            mark = lambda b: "yes" if b else "NO"  # noqa: E731
            say(f"  {system:<28} {mark(f['spec']):<6} {mark(f['state']):<6} "
                f"{mark(f['creds']):<6} {'ready' if ready else 'INCOMPLETE'}")
    else:
        say("  (none — no state-manifest.<system>.json files found)")

    missing = [s for s in roster if s not in have]
    if missing:
        say()
        say(f"NOT restorable — {len(missing)} of {len(roster)} portfolio systems have no "
            f"state manifest:")
        for system in missing:
            say(f"  - {system}")
        say()
        say("  These cannot be rebuilt from backup: nothing declares where their")
        say("  non-derivable data lives, so their RPO is total data loss (ADR-0061 D3).")
        say("  Author each one's bootstrap-spec + state-manifest + credentials-manifest;")
        say("  see state-manifest.md for the procedure.")
    return EXIT_OK


# ---------------------------------------------------------------------------
# local federation — `poga init` and `poga share` (WI-0453)
# ---------------------------------------------------------------------------
#
# The local federation — one person, one machine, no GitHub (WI-0453).
#
# POGA's core is one person on one machine with one project and one runtime. The fleet
# federation (a checkout that carries the operator's profile, a roster and a mailbox, and
# reaches its members over Git remotes) is an optional capability on top of that. Before
# this module, bootstrap could not run without it: it demanded a federation doc URL and a
# profile inside the federation, and copied the time zone and machine map from the
# federation's own config.
#
# A LOCAL FEDERATION is the minimum that lets a second project benefit from the first:
#
#     ~/.config/poga/federation/          (override: $POGA_FEDERATION_HOME)
#       federation.json                   who, where, when: user, time zone, machine map,
#                                         and the member projects on this machine
#       users/<id>/profile.md             the operator profile (blank until written)
#       inputs/<architect-id>-learnings.md
#                                         each member's producer file, mirrored — the same
#                                         layout `curate/gather.py` reads in the fleet
#
# It lives in the machine-level poga config directory, so every project on the machine
# finds the same one without being told. It is plain files: no remote, no scheduler, no
# board, no broker.
#
# HOW A LESSON REACHES ANOTHER PROJECT. It uses the mechanisms every POGA repo already has:
#
#   1. An Architect records a lesson in its own `architect-learnings.md` — the producer
#      file, in the standard entry format (`## YYYY-MM-DD · scope: <tag> · Title`).
#   2. `poga share` mirrors every member's producer file into the federation's `inputs/`,
#      parses entries with the fleet's own parser (`curate/gather.py`, so an entry's id is
#      the same content hash the fleet uses), and picks the entries whose scope says they
#      travel: `architect-general`, `domain-general`, `auditor-general`. A
#      `<system>-specific` entry stays home.
#   3. Each travelling entry is filed as a brief into every OTHER member's receipt-ritual
#      inbox (`proposed-edits/<architect-id>/pending/`), where `session.py start` names it
#      in the `inbox:` line of the start banner. It is `apply: manual` with
#      `manual-reason: attended`: a lesson is judgment, so the receiving Architect and its
#      operator decide whether it holds there. Nothing edits another project's files.
#
# Idempotent: a brief is named after the entry's content hash, and a member that already
# holds that name in ANY of its inbox folders (pending, applied, rejected, withdrawn) is not
# sent it again. `poga init` runs a share at the end, so a project added later receives the
# lessons already recorded.
#
# Federation-only, like the rest of this file: it ships with the POGA checkout, not into members.

FED_ENV = "POGA_FEDERATION_HOME"
FED_FILE = "federation.json"

#: Scope tags whose entries travel to the other projects. The producer-file template
#: defines them; `<system>-specific` is the tag that stays home.
SHARED_SCOPES = ("architect-general", "domain-general", "auditor-general")

INBOX_STATES = ("pending", "applied", "rejected", "withdrawn", "accepted")

SCOPE_RE = re.compile(r"scope:\s*([A-Za-z0-9_-]+)")


class InitError(Abort):
    """A refusal from `init`/`share`. Nothing was changed when it is raised."""

    def __init__(self, msg: str, code: int = EXIT_USAGE):
        super().__init__(msg, code)


# ---------------------------------------------------------------------------
# where it lives, and what it holds
# ---------------------------------------------------------------------------


def federation_home(explicit: str | None = None) -> Path:
    """`--federation DIR`, else `$POGA_FEDERATION_HOME`, else ~/.config/poga/federation.

    The default is the machine-level poga config directory (the same one that already
    holds `poga.local`), so a second project on this machine finds the first one's
    federation with no flag and no path typed."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(FED_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".config" / "poga" / "federation"


def load_federation(fed: Path) -> dict | None:
    """The federation record, or None when there is no local federation there yet."""
    path = fed / FED_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise InitError(f"{path} is not valid JSON ({e}) — fix or move it; nothing changed.")
    data.setdefault("members", {})
    return data


def save_federation(fed: Path, data: dict) -> None:
    fed.mkdir(parents=True, exist_ok=True)
    bs.atomic_write(fed / FED_FILE, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def create_federation(fed: Path, user_id: str, user_name: str, timezone: str,
                      machine_map: dict) -> dict:
    """Make the minimal local federation. Plain files; see the module docstring."""
    data = {
        "//": "The local POGA federation on this machine (WI-0453). Written by `poga init` "
              "and `poga share`; plain data, safe to read. Delete this directory to start over.",
        "version": 1,
        "user_id": user_id,
        "user_name": user_name,
        "timezone": timezone,
        "machine_map": machine_map,
        "members": {},
    }
    for sub in ("inputs", f"users/{user_id}"):
        (fed / sub).mkdir(parents=True, exist_ok=True)
    profile = fed / "users" / user_id / "profile.md"
    if not profile.exists():
        profile.write_text(bs.blank_profile_text(user_id), encoding="utf-8")
    save_federation(fed, data)
    return data


def register_member(fed: Path, data: dict, system_id: str, architect_id: str,
             system_name: str, path: Path) -> bool:
    """Put a project on this federation's member list. True when it was not there."""
    members = data.setdefault("members", {})
    entry = {"system_name": system_name, "architect_id": architect_id,
             "path": str(path.resolve())}
    if members.get(system_id, {}).get("path") == entry["path"]:
        return False
    members[system_id] = {**entry, "added": datetime.now(ZoneInfo(
        data.get("timezone") or "UTC")).strftime("%Y-%m-%d")}
    save_federation(fed, data)
    return True


# ---------------------------------------------------------------------------
# sharing lessons
# ---------------------------------------------------------------------------


def _parse_entries(path: Path, text: str) -> list[dict]:
    """Entries of one producer file, by the fleet's own parser — so an entry's id here
    is the id `curate/gather.py` would give it."""
    curate = str(ROOT / "curate")
    if curate not in sys.path:
        sys.path.insert(0, curate)
    import gather  # noqa: E402  (curate/ is on sys.path only from here)
    return gather.parse_entries(path, text)


def entry_title(entry: dict) -> str:
    """The title without the `scope: <tag> ·` prefix the parser leaves on it."""
    return re.sub(r"^scope:\s*\S+\s*·\s*", "", entry["title"]).strip()


def entry_scope(entry: dict) -> str:
    first = entry["text"].splitlines()[0] if entry["text"] else ""
    m = SCOPE_RE.search(first)
    return m.group(1) if m else ""


def _inbox(member_path: Path, architect_id: str) -> Path:
    """The member's pending inbox, as its own session config declares it."""
    try:
        cfg = json.loads((member_path / "session.config.json").read_text(encoding="utf-8"))
        rel = cfg.get("inbox") or f"proposed-edits/{architect_id}/pending"
    except (OSError, ValueError):
        rel = f"proposed-edits/{architect_id}/pending"
    return member_path / rel


def _already_holds(pending: Path, name: str) -> bool:
    return any((pending.parent / state / name).exists() for state in INBOX_STATES)


def lesson_brief(entry: dict, src_id: str, src: dict, dst_id: str, dst: dict,
                 today: str) -> str:
    return (
        f"---\n"
        f"edit-id: lesson-{entry['id']}\n"
        f"from: {src['architect_id']}\n"
        f"to: {dst['architect_id']}\n"
        f"date: {today}\n"
        f"apply: manual\n"
        f"manual-reason: attended\n"
        f"attended-because: a lesson is judgment — this project's Architect and its "
        f"operator decide whether it holds here\n"
        f"bears-on: architect-learnings\n"
        f"---\n\n"
        f"# Lesson from {src['system_name']}: {entry_title(entry)}\n\n"
        f"`{src_id}` recorded this in its `architect-learnings.md` with scope "
        f"`{entry_scope(entry)}`, so the local federation shared it with every other "
        f"project on this machine (`poga share`).\n\n"
        f"Nothing has been changed in this repo. Read it. If it holds for `{dst_id}`, "
        f"act on it — a habit, a check, a line in the role doc, or an entry in this "
        f"project's own `architect-learnings.md` in its own words. Then move this file "
        f"to `applied/` (acted on) or `rejected/` (does not hold here), beside "
        f"`pending/`.\n\n"
        f"## The lesson, verbatim\n\n"
        f"~~~markdown\n{entry['text']}\n~~~\n"
    )


def share_lessons(fed: Path, data: dict, only_to: str | None = None) -> dict:
    """Mirror every member's producer file, then deliver each travelling entry to every
    other member's inbox. Returns a summary; prints one line per delivery."""
    members = data.get("members", {})
    today = datetime.now(ZoneInfo(data.get("timezone") or "UTC")).strftime("%Y-%m-%d")
    inputs = fed / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    summary = {"mirrored": 0, "shared_entries": 0, "kept_home": 0, "delivered": [],
               "missing": []}

    lessons: list[tuple[str, dict]] = []
    for sid, m in sorted(members.items()):
        mpath = Path(m["path"])
        producer = mpath / "architect-learnings.md"
        if not mpath.is_dir():
            summary["missing"].append(f"{sid}: {mpath} is gone")
            continue
        if not producer.is_file():
            continue
        text = producer.read_text(encoding="utf-8")
        mirror = inputs / f"{m['architect_id']}-learnings.md"
        if not mirror.is_file() or mirror.read_text(encoding="utf-8") != text:
            bs.atomic_write(mirror, text)
            summary["mirrored"] += 1
        for e in _parse_entries(producer, text):
            if entry_scope(e) in SHARED_SCOPES:
                lessons.append((sid, e))
                summary["shared_entries"] += 1
            else:
                summary["kept_home"] += 1

    for src_id, entry in lessons:
        src = members[src_id]
        for dst_id, dst in sorted(members.items()):
            if dst_id == src_id or (only_to and dst_id != only_to):
                continue
            dpath = Path(dst["path"])
            if not dpath.is_dir():
                continue
            pending = _inbox(dpath, dst["architect_id"])
            name = f"{entry['date']}-lesson-from-{src_id}-{entry['id']}.md"
            if _already_holds(pending, name):
                continue
            pending.mkdir(parents=True, exist_ok=True)
            (pending / name).write_text(lesson_brief(entry, src_id, src, dst_id, dst, today),
                                        encoding="utf-8")
            summary["delivered"].append((dst_id, name))
            say(f"  shared: {src_id} -> {dst_id}  {entry_title(entry)}")
    return summary


def report_share(summary: dict, fed: Path) -> None:
    n = len(summary["delivered"])
    say(f"  lessons: {summary['shared_entries']} shareable "
        f"({', '.join(SHARED_SCOPES)}), {summary['kept_home']} kept home; "
        f"{n} new deliver{'y' if n == 1 else 'ies'}; "
        f"{summary['mirrored']} producer file(s) mirrored into {fed / 'inputs'}")
    for line in summary["missing"]:
        say(f"  NOT REACHED: {line}")


# ---------------------------------------------------------------------------
# poga init
# ---------------------------------------------------------------------------


class Interview:
    """A few prompts, each with a default. `--yes` takes every default, so the whole
    thing can be scripted; a flag answers its question without asking it."""

    def __init__(self, yes: bool):
        self.yes = yes

    def _need_terminal(self) -> None:
        """Checked at the first question that is really asked, not up front: a run whose
        every answer is already known (a flag, a spec, an existing federation) needs no
        terminal. Nothing is written until the last answer is in, so refusing here
        changes nothing."""
        if not sys.stdin.isatty():
            raise InitError(
                "it asks a few short questions, each with a default, and needs an "
                "interactive terminal for them. Run it in a terminal, or pass --yes to take "
                "every default (flags such as --name set individual answers). Nothing changed.")

    def ask(self, prompt: str, default: str, given: str | None = None) -> str:
        if given is not None:
            say(f"  {prompt}: {given}")
            return given
        if self.yes:
            say(f"  {prompt}: {default}")
            return default
        self._need_terminal()
        print(f"  {prompt} [{default}]: ", end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            raise InitError("input ended before the interview finished — nothing changed.")
        return line.strip() or default

    def yes_no(self, prompt: str, default: bool, given: bool | None = None) -> bool:
        if given is not None:
            say(f"  {prompt}: {'yes' if given else 'no'}")
            return given
        if self.yes:
            say(f"  {prompt}: {'yes' if default else 'no'}")
            return default
        self._need_terminal()
        while True:
            print(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                raise InitError("input ended before the interview finished — nothing changed.")
            ans = line.strip().lower()
            if not ans:
                return default
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no"):
                return False
            say("  Please answer y or n.")


def _default_system_name(target: Path) -> str:
    words = re.split(r"[-_\s]+", target.name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "My Project"


def _git_identity(target: Path) -> tuple[str, str]:
    probe = target if target.is_dir() else target.parent
    out = []
    for key in ("user.name", "user.email"):
        proc = subprocess.run(["git", "-C", str(probe), "config", "--get", key],
                              text=True, capture_output=True)
        out.append(proc.stdout.strip() if proc.returncode == 0 else "")
    return out[0], out[1]


def _installed(target: Path) -> dict | None:
    """The session config of a project that already carries the substrate, else None."""
    if not all((target / m).exists() for m in bs.ADOPTED_MARKERS):
        return None
    try:
        return json.loads((target / "session.config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _federation_questions(iv: Interview, args) -> tuple[str, str, str, dict]:
    import getpass
    say("  No local federation on this machine yet — making one. Three questions.")
    user_name = iv.ask("Your name", getpass.getuser() or "operator", args.user_name)
    user_id = re.sub(r"[^a-z0-9]+", "-", user_name.lower()).strip("-") or "operator"
    tz = iv.ask("Time zone", bs.detect_timezone(), args.timezone)
    try:
        ZoneInfo(tz)
    except Exception:
        raise InitError(f"{tz!r} is not a time zone this machine knows (an IANA name such "
                        f"as UTC or Europe/Paris) — nothing changed.")
    name = bs.machine_name() or "this-machine"
    label = iv.ask("A short label for this machine", bs.machine_label(name), args.machine_label)
    return user_id, user_name, tz, {name: label}


def cmd_init(args) -> int:
    target = bs.resolve_from_invocation(args.path or ".")
    fed = federation_home(args.federation)
    iv = Interview(args.yes)
    say(f"== poga init{' [plan]' if args.plan else ''}: {target} ==")
    say(f"  local federation: {fed}")

    data = load_federation(fed)
    fed_answers = None
    if data is None:
        fed_answers = _federation_questions(iv, args)

    installed = _installed(target)
    if installed is not None:
        # Already a POGA project: joining the federation is all that is left to do.
        aid = installed.get("architect_id") or ""
        if not aid.endswith("-arch"):
            raise InitError(f"{target} carries the substrate but its session.config.json "
                            f"names no architect_id — nothing changed.")
        sid = aid[: -len("-arch")]
        sname = (installed.get("architect_name") or sid).removesuffix(" Architect")
        say(f"  {target.name} is already a POGA project ({aid}) — registering it only.")
        if args.plan:
            say("  [plan] nothing written.")
            return 0
        if data is None:
            data = create_federation(fed, *fed_answers)
        added = register_member(fed, data, sid, aid, sname, target)
        say(f"  registered: {'yes' if added else 'already on the member list'}")
        report_share(share_lessons(fed, data), fed)
        return 0

    for marker in bs.ADOPTED_MARKERS:
        if (target / marker).exists():
            raise InitError(f"{target} carries part of the substrate ({marker}) but not all "
                            f"of it — that is a retrofit (ADR-0014), not an init. Nothing changed.")

    spec: dict = {}
    spec_file = target / "bootstrap-spec.json" if target.is_dir() else None
    if spec_file is not None and spec_file.is_file():
        try:
            spec = json.loads(spec_file.read_text(encoding="utf-8"))
        except ValueError as e:
            raise InitError(f"{spec_file} is not valid JSON ({e}) — nothing changed.")
        say(f"  using the spec already in the folder: {spec_file.name}")

    system_name = spec.get("system_name") or iv.ask(
        "Project name", _default_system_name(target), args.name)
    ids = bs.derive_ids(system_name)
    if not ids["system_id"]:
        raise InitError(f"{system_name!r} gives an empty id — use letters or digits. "
                        f"Nothing changed.")
    members = (data or {}).get("members", {})
    other = members.get(ids["system_id"])
    if other and Path(other["path"]).resolve() != target.resolve():
        raise InitError(f"the federation already has a project called {ids['system_id']!r} "
                        f"at {other['path']} — pick another --name. Nothing changed.")
    if data is not None:
        user_id = data.get("user_id") or "operator"
        fed_user_name = data.get("user_name") or user_id
        tz = data.get("timezone") or "UTC"
        # A second machine joining an existing federation directory is added to the map
        # with its default label, rather than rendering as an unknown host.
        machine_map = dict(data.get("machine_map") or {})
        here = bs.machine_name()
        if here and here not in machine_map:
            machine_map[here] = bs.machine_label(here)
    else:
        user_id, fed_user_name, tz, machine_map = fed_answers
    user_name = spec.get("user_name") or fed_user_name
    mission = spec.get("mission_prose")
    if not mission:
        purpose = iv.ask("What is it for, in one sentence (Enter to write it later)", "",
                         args.purpose)
        mission = (f"I am the Architect of **{system_name}**. {purpose}".strip() if purpose
                   else f"I am the Architect of **{system_name}**. The mission is not written "
                        f"yet: the first session writes it with {user_name} and replaces "
                        f"this paragraph.")
    create_remote = spec.get("create_remote") is True or iv.yes_no(
        "Create a private GitHub remote and push to it? (Local only is complete; add a "
        "remote any time)", False, True if args.create_remote else None)

    full = {
        "system_name": system_name,
        "user_id": user_id,
        "user_name": user_name,
        "mission_prose": mission,
        "timezone": tz,
        "machine_map": machine_map,
        "local_federation": str(fed),
        "create_remote": create_remote,
        **{k: v for k, v in spec.items() if v not in (None, "", [])},
    }
    full["create_remote"] = create_remote

    if args.plan:
        say(f"  [plan] would create {'the federation and ' if data is None else ''}"
            f"{ids['architect_id']} in {target} "
            f"({'with a GitHub remote' if create_remote else 'local only'}); nothing written.")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    if data is None:
        data = create_federation(fed, *fed_answers)
        say(f"  created the local federation at {fed}")
    elif data.get("machine_map") != machine_map:
        data["machine_map"] = machine_map
        save_federation(fed, data)

    # A fresh HOME has no git identity, and the install commits. Use the operator's name
    # for THIS repo only, and say so — never write the global git config.
    name, email = _git_identity(target)
    set_identity = not (name and email)
    if set_identity:
        name = name or user_name
        email = email or f"{user_id}@localhost"
        for k, v in (("GIT_AUTHOR_NAME", name), ("GIT_COMMITTER_NAME", name),
                     ("GIT_AUTHOR_EMAIL", email), ("GIT_COMMITTER_EMAIL", email)):
            os.environ[k] = v

    bs.adopt_in_place(full, target, create_remote=create_remote, dry_run=False, register=False)

    if set_identity:
        for k, v in (("user.name", name), ("user.email", email)):
            subprocess.run(["git", "-C", str(target), "config", k, v],
                           text=True, capture_output=True)
        say(f"  git identity: none was configured — set {name} <{email}> for this repo "
            f"only (`git config user.email ...` to change it)")

    register_member(fed, data, ids["system_id"], ids["architect_id"], system_name, target)
    say(f"  registered {ids['system_id']} in the local federation "
        f"({len(data['members'])} project{'s' if len(data['members']) != 1 else ''})")
    report_share(share_lessons(fed, data), fed)
    say("")
    say("  next:  cd into the project and run `poga` to open a session.")
    say("         After an Architect records a lesson with scope architect-general in its")
    say("         architect-learnings.md, run `poga share` to send it to your other projects.")
    return 0


def cmd_share(args) -> int:
    fed = federation_home(args.federation)
    data = load_federation(fed)
    if data is None:
        raise InitError(f"no local federation at {fed} — run `poga init` in a project first.")
    say(f"== poga share: {fed} ==")
    say(f"  projects: {', '.join(sorted(data.get('members', {}))) or '(none)'}")
    report_share(share_lessons(fed, data), fed)
    return 0


# ---------------------------------------------------------------------------
# which intake a folder with no repo gets
# ---------------------------------------------------------------------------


def profile_has_entries(path: Path) -> bool:
    """A profile with at least one entry heading outside HTML comments. The kit's blank
    profile has none (its only `###` sits inside the example comment)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return any(line.startswith("### ") for line in text.splitlines())


def intake_route(fed: Path | None = None) -> str:
    """`init` or `agent`: what `poga` in a folder with no repo should run.

    The agent-run interview is for a checkout that IS an operating fleet federation —
    one whose configured operator profile has real entries. Everything else (a fresh
    clone, the public cut with its blank profile, or any machine that already has a local
    federation) gets `poga init`: a short local interview that needs no GitHub, no
    runtime and no existing federation."""
    fed = fed or federation_home()
    if (fed / FED_FILE).is_file():
        return "init"
    prof = bs.fed_config().get("user_profile")
    if prof and profile_has_entries(ROOT / prof):
        return "agent"
    return "init"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="poga", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    b = sub.add_parser("bootstrap", help="build a new Architect from its spec")
    b.add_argument("--spec", help="path to the spec; defaults to bootstrap-spec.json in "
                   "the folder you are standing in. A relative path resolves against "
                   "that folder first, then the federation checkout")
    b.add_argument("--into", help="override the install path; defaults to the spec's own "
                   "folder when the spec names no location")
    b.add_argument("--plan", "--dry-run", dest="plan", action="store_true",
                   help="print the plan; touch nothing (alias: --dry-run)")
    b.add_argument("--adopt", nargs="?", const=".", metavar="PATH",
                   help="ADOPT-IN-PLACE (ADR-0067) at PATH: an existing REPO that has real "
                        "content but no Architect. Never overwrites an existing file, "
                        "merges .gitignore, leaves the system's own surfaces alone. Not "
                        "needed to build into the folder you are standing in — plain "
                        "`poga bootstrap` already does that; use this to point at another "
                        "directory, or to keep --create-remote off by default.")
    b.add_argument("--create-remote", action="store_true",
                   help="Create the private GitHub repo and set it as origin when the "
                        "target has no remote. Outward-facing, so OFF on every path "
                        "(WI-0452): without it a new system is a local-only git repo. "
                        "`\"create_remote\": true` in the spec says the same thing.")

    i = sub.add_parser("init", help="make THIS folder a POGA project, local only, and join "
                                    "it to the local federation on this machine (WI-0453)")
    i.add_argument("path", nargs="?", help="the project folder; defaults to where you stand. "
                   "Created if it does not exist")
    i.add_argument("--yes", "-y", action="store_true",
                   help="take every default without asking (scriptable)")
    i.add_argument("--name", help="project name (default: from the folder name)")
    i.add_argument("--purpose", help="one sentence: what the project is for")
    i.add_argument("--user-name", help="your name, asked only when the federation is new")
    i.add_argument("--timezone", help="IANA time zone, asked only when the federation is "
                   "new (default: this machine's)")
    i.add_argument("--machine-label", help="short label for this machine, asked only when "
                   "the federation is new")
    i.add_argument("--create-remote", action="store_true",
                   help="also create a private GitHub remote and push (needs `gh`). Off "
                        "by default: a local-only project is complete")
    i.add_argument("--federation", metavar="DIR",
                   help="the local federation to use (default: $POGA_FEDERATION_HOME, else "
                        "~/.config/poga/federation)")
    i.add_argument("--plan", "--dry-run", dest="plan", action="store_true",
                   help="ask, then print what would happen; write nothing")

    s = sub.add_parser("share", help="send each project's shareable lessons to the other "
                                     "projects in the local federation (WI-0453)")
    s.add_argument("--federation", metavar="DIR", help="as for `init`")

    sub.add_parser("intake-route", help=argparse.SUPPRESS)

    r = sub.add_parser("restore", help="rebuild ONE system from its spec + state manifest")
    # Optional so a bare `poga restore` LISTS what can be rebuilt instead of erroring
    # with "argument required" — on a fresh machine that error is the least useful
    # thing the tool could say.
    r.add_argument("system", nargs="?", help="system-id to rebuild, e.g. `federation`")
    r.add_argument("--list", action="store_true",
                   help="list which systems can be restored, and which cannot")
    r.add_argument("--into", help="override the install path from the spec")
    r.add_argument("--scenario", choices=sorted(SCENARIOS), default="machine-loss",
                   help="machine-loss -> local backup source (scripted); "
                        "site-loss -> offsite backup source (human checklist)")
    r.add_argument("--plan", action="store_true", help="print the plan; touch nothing")
    return ap


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """`poga bootstrap` — pick the engine from what is actually on disk at the target.

    THE RULE, ONE SENTENCE: the folder you ran the command in is the answer to every
    question this verb used to ask. It finds the spec there, it builds there, and it
    does not require a flag to be told either (the operator's ruling, 2026-08-18, after four
    refusals in a row: bootstrap works in the folder it is run in and asks for nothing
    else.)

    Two engines, and the DIRECTORY chooses — not a flag:

    * The target does not exist, or is a half-finished clone of the spec's own repo ->
      the seven-phase pipeline (`execute`): clone the remote the spec names (creating it
      only under `--create-remote`, WI-0452), render the kit into the checkout. This is
      the only case with anything to ACQUIRE. A spec that names no remote and asks for
      none never comes here: its target is made if absent and built in place.
    * The target already exists -> the in-place engine. There is nothing to clone into
      and the directory is the system's home already, so: `git init` if it needs one,
      commit whatever is sitting there as the root commit, render the kit around it.

    That second branch covers a folder holding only a brief and a spec AND a repo that
    has real code but no Architect — the two cases differ in the COPY PLAN
    (`select_copy_plan`, which keys on git history), not in which engine runs. Routing
    on "does it exist" rather than on "is it a checkout of the spec's repo" is what
    fixes the last of the four refusals: `phase_acquire` calls anything that is not our
    own clone `foreign` and stops, which is right for `restore` (it rebuilds from a
    pinned remote) and wrong for `bootstrap` (it is *installing into* that directory).
    The engine carries the real guards — it refuses a repo that already has substrate
    (ADOPTED_MARKERS: that is a retrofit, ADR-0014), refuses a dirty tree, and never
    overwrites an existing file.

    A folder holding a brief and a spec is not "an existing project to adopt" (operator:
    *"there's nothing to adopt"*); it is a fresh build whose inputs arrived before the
    substrate did, and it must end up with the full kit, STATUS.md and README.md
    included.
    """
    if args.adopt is not None:
        # Explicit --adopt keeps its 2026-07-24 behaviour exactly: PATH (or the folder
        # you are standing in), spec found inside it, engine, done.
        adopt_dir = bs.resolve_from_invocation(args.adopt)
        spec = bs.load_adopt_spec(adopt_dir, args.spec, ROOT)
        bs.adopt_in_place(spec, adopt_dir, create_remote=wants_remote(spec, args.create_remote),
                          dry_run=args.plan)
        return EXIT_OK

    spec_path = spec_path_from_arg(args.spec) if args.spec else discover_spec()
    if spec_path is not None and spec_path.is_file():
        # Resolve the target the same way `phase_resolve` will, so the routing decision
        # and the pipeline cannot disagree about where the system is going (P16). The
        # spec is re-read there rather than threaded through — it is a small file, and a
        # second parse is cheaper than a second code path that could drift.
        spec = load_json(spec_path, "bootstrap spec")
        if not spec.get("system_name"):
            raise Abort(f"{spec_path.name} is missing `system_name`", EXIT_USAGE)
        target = resolve_target(spec, args.into, spec_path)
        create = wants_remote(spec, args.create_remote)
        # WI-0452: a spec that names no remote and asks for none is LOCAL-ONLY. It gets no
        # URL, so `gh` is never asked for an owner, and it never reaches the pipeline —
        # there is nothing to clone. An absent target is simply made, then built in place.
        repo_url = derive_repo_url(spec, bs.derive_ids(spec["system_name"]), need=create)
        state = checkout_state(target, repo_url) if repo_url else (
            "absent" if not target.exists() else "plain")
        if not repo_url or state not in ("absent", "ours"):
            # Remote creation is OPT-IN on every path (WI-0452). It used to be switched on
            # here for any directory with no git history — "a genuinely new system" —
            # which made first use create a GitHub repo nobody asked for. A new system
            # now gets a local repo; `--create-remote` (or `"create_remote": true` in the
            # spec) is the one way to ask for the remote, exactly as on `--adopt`.
            say(f"== poga bootstrap{' [plan]' if args.plan else ''} ==")
            step("resolve", f"spec: {spec_path.name}")
            if not target.exists():
                step("resolve", f"target absent — will create {target} and build in place")
                if args.plan:
                    step("resolve", "[plan] nothing created; the in-place plan needs the "
                                    "folder to exist, so it is not printed")
                    return EXIT_OK
                target.mkdir(parents=True)
            else:
                step("resolve", f"target exists — installing in place at {target}")
            bs.adopt_in_place(spec, target, create_remote=create, dry_run=args.plan)
            return EXIT_OK
        # Everything else — absent target, our own checkout, someone else's repo — is
        # the pipeline's business. Hand it the ABSOLUTE spec path so it does not redo
        # the relative-path resolution and risk landing somewhere else.
        args.spec = str(spec_path)

    return execute("bootstrap", args.spec, None, args.into, "machine-loss", args.plan,
                   create_remote=args.create_remote)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verb == "init":
            return cmd_init(args)
        if args.verb == "share":
            return cmd_share(args)
        if args.verb == "intake-route":
            say(intake_route())
            return EXIT_OK
        if args.verb == "bootstrap":
            return cmd_bootstrap(args)
        if args.verb == "restore" and (args.list or not args.system):
            return cmd_list()
        return execute(args.verb, getattr(args, "spec", None), getattr(args, "system", None),
                       args.into, getattr(args, "scenario", "machine-loss"), args.plan)
    except Abort as e:
        say(f"\npoga {args.verb}: {e}")
        return e.code
    except KeyboardInterrupt:
        say("\npoga: interrupted — re-run to resume (the CLI is idempotent).")
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
