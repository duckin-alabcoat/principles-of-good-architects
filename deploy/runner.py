#!/usr/bin/env python3
"""The Runner-side deploy runner — ADR-0103.

**A version developed on devbox becomes a running program here by transiting `origin`
as a TAG, which this program deploys.** Nothing else is a promotion: not a push, not a
land, not a pull someone remembered to run. `if it isn't tagged, it isn't deployable`.

This is PLAIN CODE and it stays plain code (ADR-0103 D4). A deploy is deterministic —
fetch, resolve a tag, read that tag's contract, seed state, check out, sync deps, smoke,
restart, verify, record — so routing it through an LLM would buy nothing and inherit
skipping, drift and fabrication ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).
It also means the verb works headless and over SSH, unbothered by the Aqua-session
keychain constraint that binds the adopt-runner. An agent session enters this story only
when a deploy has already failed and its escalation summons one.

THE SHAPE OF THE SAFETY ARGUMENT. There are two failure points and they are not
symmetric, which is why they get different treatment:

  * **Smoke fails** — the new tree is checked out but NOTHING has been restarted, so the
    old version is still the one serving. Cost of the failure is zero. We put the files
    back and stop.
  * **Verify fails** — production is already switched. Cost is real and running. We roll
    the tree back to the previous tag, re-sync, restart again, and then CONFIRM THE OLD
    VERSION IS HEALTHY before saying anything reassuring, because "rolled back" with an
    unverified old version is the same class of claim as an unverified backup.

Deploys are boring or they are loud. There is nothing in between, and in particular there
is no retry-until-green: a smoke check that fails twice is a broken release, not a flaky
one, and pretending otherwise is how a gate stops being a gate.

WHAT THIS PROGRAM WILL NOT DO. It will not prompt. It will not print a command for a
human to run. It will not deploy an untagged tip, and it will not deploy into the
developer's own clone. Each of those is a rule with an incident behind it.

A CANARY IS THE THIRD THING, and it is not a deploy at all. `--canary <tag>` checks a
tag out into a SEPARATE tree, runs that tag's own smoke check there, records the verdict,
and stops. It restarts nothing and it cannot reach a production tree — the canary root is
derived from the deploy root with an unconditional suffix, so no configuration exists in
which the two are the same directory. That is what makes a release candidate safe to have:
`vX.Y.Z-rc.N` is a tag this runner can MATCH and CANARY but will never SELECT, so tagging
a candidate to try it can never become deploying it.

Usage:
    runner.py <system> [--version vX.Y.Z] [--dry-run] [--trigger manual|sweep]
    runner.py <system> --rollback [--dry-run]
    runner.py <system> --canary vX.Y.Z[-rc.N] [--dry-run]
    runner.py --status [<system>]
    runner.py --sweep [--dry-run]
    runner.py --drill [<system>] [--dry-run]
    runner.py --unattended [--dry-run]

Exit codes: 0 deployed (or nothing to do), 1 refused/failed with the old version intact,
2 FAILED AND THE ROLLBACK ALSO FAILED — the only state that needs a human, and it is
loud on purpose.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import plistlib
import re
import contextlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from xml.parsers.expat import ExpatError

REPO_ROOT = Path(__file__).resolve().parents[1]
# The outbox's own module, so the queue convention and the scrub gate have ONE
# implementation rather than a second one written here — `outbox/to-<id>/` has been the
# fleet's outbound shape since WI-0079 and a runner that spelled it out itself would be
# the third place to keep in agreement by hand.
sys.path.insert(0, str(REPO_ROOT / "curate"))
sys.path.insert(0, str(REPO_ROOT))
import outbox                                                        # noqa: E402
# The Runner channel, for the same reason `outbox` is imported rather than re-spelled: the
# rule about where a sealed machine may write has ONE implementation, and this program is
# the one that seals the machine.
import channel                                                       # noqa: E402
import production                                                    # noqa: E402
# One rule for every evidence cut in this program (WI-0371): a truncated verdict says
# so and says by how much, because a tail keeps the END and a failed deploy usually
# explains itself at the HEAD.
import poga_evidence as evidence                                                      # noqa: E402
# `this_machine` is NOT re-spelled here. `curate/common.py`'s own docstring commits the
# project to one implementation of it ("a second implementation here would be free to drift
# into disagreeing with the thing it describes"), `channel.py` imports it rather than
# re-deriving it, and the first cut of WI-0316 added a private copy to this file before the
# rebase showed what was already there.
import common                                                        # noqa: E402
# Every path this program writes to is overridable by environment, and that is a TEST
# SAFETY property, not a convenience: a fixture that derives a path from REPO_ROOT writes
# into the federation's own repo, which has already happened once here (a live ADR
# reservation left behind in `.git/poga-coord`). A suite that can reach production paths
# will eventually reach them.
CONTRACT_RELPATH = "deploy/deploy.json"
SUPPORTED_CONTRACT_VERSION = 1

# These four are READ AT CALL TIME, never frozen into module constants at import. A
# constant would be captured before a test could redirect it, so the suite would write
# into the federation's own repo and the real ledger — which has happened here before (a
# live coordination reservation left behind by a fixture). Late binding is the fix.
def registry_path() -> Path:
    return Path(os.environ.get("POGA_DEPLOY_REGISTRY",
                               REPO_ROOT / "deploy" / "registry.json"))


def comms_dir() -> Path:
    roots = production.resolve(REPO_ROOT)
    if roots:
        return roots.state_path("comms")
    return Path(os.environ.get("POGA_DEPLOY_COMMS_DIR", REPO_ROOT / "comms"))


def ledger_dir() -> Path:
    """MACHINE-LOCAL and outside the repo on purpose: it records what is running on THIS
    machine, and a tracked file two machines both write is WI-0132's defect. It is also
    deliberately re-derivable — ground truth is the deploy tree's own checked-out tag — so
    losing it costs a `--status` refresh, not a fact."""
    return Path(os.environ.get("POGA_DEPLOY_LEDGER_DIR",
                               Path.home() / ".local" / "state" / "poga" / "deploy"))


def launch_agents_dir() -> Path:
    """Where a per-user launchd job is installed from. Redirectable for the same reason
    every other production path here is: a suite that writes the real one would install
    jobs on the machine running the tests."""
    return Path(os.environ.get("POGA_DEPLOY_LAUNCH_AGENTS",
                               Path.home() / "Library" / "LaunchAgents"))


def cli_link() -> Path:
    """The `poga` command on this machine's PATH — a SYMLINK, not a copy.

    Overridable like every other production path here, and for the sharper version of the
    usual reason: this one lives outside the repo in the user's own `bin`, so a fixture that
    resolved it for real would repoint the command the operator types."""
    return Path(os.environ.get("POGA_CLI_LINK",
                               Path.home() / ".local" / "bin" / "poga"))


def deploy_root() -> Path:
    return Path(os.environ.get("POGA_DEPLOY_ROOT", Path.home() / "deploy"))


def outbox_dir() -> Path:
    roots = production.resolve(REPO_ROOT)
    if roots:
        return roots.queue_root
    return Path(os.environ.get("POGA_DEPLOY_OUTBOX_DIR", REPO_ROOT / "outbox"))


def mailbox_registry_path() -> Path:
    return production.config_path(REPO_ROOT, "mailboxes.json",
                                  os.environ.get("POGA_DEPLOY_MAILBOXES", REPO_ROOT / "mailboxes.json"))

# A RELEASE TAG, AND NOW ALSO A RELEASE CANDIDATE. The fourth group is the whole of the
# pre-release vocabulary this runner speaks — `-rc.N`, nothing else. Semver permits a far
# richer suffix (`-alpha.2+build.7`); admitting all of it would mean every consumer of this
# pattern has to decide what `v1.2.3-alpha` ranks against `v1.2.3-beta`, and this machine
# has no use for that ordering. One shape, ordered by an integer, is a vocabulary the
# comparison cannot get wrong.
#
# WIDENING A PATTERN WIDENS EVERY READER OF IT, which is why this is not a one-line change.
# `newest_tag` is what picks the version a sweep deploys with nobody watching, so a regex
# that merely *matches* an rc would have made an unfinished candidate the thing that
# deploys itself at 03:00 — the accidental-promotion failure ADR-0103 D2 exists to prevent,
# arriving through a match instead of through a push. The admission and the exclusion are
# therefore ONE change: matching is what makes an rc ADDRESSABLE (to `--canary`), and
# `newest_tag`'s `prereleases=False` default is what keeps it UNSELECTABLE.
SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")


def tag_order(m: "re.Match") -> tuple:
    """Sort key for a matched semver tag. `v1.2.3-rc.1` ranks BELOW `v1.2.3`.

    The pre-release slot is `1` for a release and `0` for a candidate, which is the only
    part of semver's precedence rule this vocabulary needs and the only part it can get
    wrong. It cannot be `None`: a tuple mixing `None` and `int` raises on comparison in
    Python 3, so the sort would fail on exactly the first repo carrying both forms."""
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch), 0 if rc else 1, int(rc or 0))


def is_prerelease(tag: str) -> bool:
    """Is this tag a release CANDIDATE rather than a release?

    A string that is not a semver tag at all answers False — it is not a candidate, and
    this predicate is only ever asked about tags that have already been resolved. A caller
    asking "is this deployable at all" asks `SEMVER_TAG_RE` itself."""
    m = SEMVER_TAG_RE.match(tag)
    return bool(m and m.group(4) is not None)


# ── plumbing ─────────────────────────────────────────────────────────────────────

class DeployError(Exception):
    """A refusal or failure that leaves the previous version intact."""


class RollbackFailed(Exception):
    """The deploy failed AND putting it back failed. Exit 2, loudly."""


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 120,
        env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """One place where a subprocess is launched, so every command is logged the same way
    and no call site can quietly skip the timeout. `check=False` throughout: this program
    decides what a non-zero exit MEANS at each step, and the meanings differ."""
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def git(args: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd, timeout=timeout)


def git_ok(args: list[str], cwd: Path, what: str, timeout: int = 300) -> str:
    r = git(args, cwd, timeout)
    if r.returncode != 0:
        raise DeployError(f"{what}: git {' '.join(args)} failed — "
                          f"{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def load_registry() -> dict:
    if not registry_path().exists():
        raise DeployError(f"no deploy registry at {registry_path()}")
    return json.loads(registry_path().read_text())


def registry_entry(system: str) -> dict:
    reg = load_registry()
    systems = reg.get("systems", {})
    if system not in systems:
        # Absent is its own answer, never "nothing to deploy"
        # ([`declare-what-a-check-assumes`]).
        raise DeployError(
            f"'{system}' is not a deployable system on this machine — it has no entry in "
            f"{registry_path()}. That is NOT the same as 'up to date'; it means nobody has "
            f"declared how this system deploys. Known: {', '.join(sorted(systems)) or '(none)'}")
    return systems[system]


def deploy_tree(system: str) -> Path:
    return deploy_root() / system


def work_dir(system: str, entry: dict) -> Path:
    """Where the system's runnable code actually sits — the tree, or a subdirectory of it
    for a repo that holds more than the program."""
    sub = entry.get("subdir")
    return deploy_tree(system) / sub if sub else deploy_tree(system)


def canary_root() -> Path:
    """Where candidate tags are exercised. A SIBLING of the deploy root, never inside it.

    DERIVED FROM `deploy_root()` RATHER THAN GIVEN ITS OWN ENVIRONMENT VARIABLE, and that
    is the safety property, not a shortcut. Every other production path here is redirectable
    because a fixture that wrote the real one would damage the machine — and the suite has a
    guard (`TestEveryProductionPathIsRedirected`) asserting that every `POGA_*` name this
    file reads is redirected by the fixture. A second knob would be a second thing to
    forget, and the thing it would be protecting is the tree a *probe* writes to. Deriving
    it means redirecting `POGA_DEPLOY_ROOT` moves the canary too, automatically, forever.

    It also makes the separation structural rather than conventional: the two roots differ
    by a suffix that is appended unconditionally, so no configuration exists in which a
    canary resolves to a production tree. That is the property `--canary` sells — "touches
    no production tree" — and it is worth more as arithmetic than as care."""
    prod = deploy_root()
    return prod.parent / f"{prod.name}-canary"


def canary_tree(system: str) -> Path:
    return canary_root() / system


def canary_work_dir(system: str, entry: dict) -> Path:
    sub = entry.get("subdir")
    return canary_tree(system) / sub if sub else canary_tree(system)


# ── ledger ───────────────────────────────────────────────────────────────────────

def ledger_path(system: str) -> Path:
    return ledger_dir() / f"{system}.json"


def read_ledger(system: str) -> dict:
    p = ledger_path(system)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger must read as UNKNOWN, never as "never deployed" — the two
        # would otherwise be the same output and one of them is a warning.
        return {"_unreadable": True}


def write_ledger(system: str, **fields: Any) -> None:
    # A STATUS IS BOUND TO THE TAG IT WAS DECIDED FOR, OR IT IS NOT WRITTEN (WI-0412).
    #
    # This is a guard rather than a convention because the defect it closes is a MERGE
    # defect, and a merge defect is invisible at the call site that causes it. `status` is
    # the one field that reads as a verdict on whatever `current` happens to say beside it,
    # and `write_ledger` is a read-modify-write — so a call naming `current` and not
    # `status` drags a verdict decided three releases ago forward onto today's version,
    # with `checked_at` freshly stamped to vouch for it: a ledger could carry
    # `status: deployed` beside a `current` whose commit postdates its `deployed_at`.
    #
    # FAILING CLOSED IS THE RIGHT DIRECTION HERE, against the usual preference. What is on
    # the other side of this guard is a green verdict attached to a release that never
    # deployed — the silent wrong answer the whole diagnosis surface exists to prevent — so
    # letting it through is the unsafe direction. It can only be tripped by CODE (a new
    # call site that decides a status without saying what it decided it about), never by
    # data, so it fails in a test rather than at 03:15.
    if "status" in fields and "status_tag" not in fields:
        raise ValueError(
            f"write_ledger({system!r}, status={fields['status']!r}) does not say which tag "
            f"that status was decided for. Pass **_decided(status, tag) instead: an "
            f"unbound status survives the merge into every later record and reads as a "
            f"verdict on whatever `current` says beside it (WI-0412).")
    ledger_dir().mkdir(parents=True, exist_ok=True)
    rec = read_ledger(system)
    rec.pop("_unreadable", None)
    rec.update(fields)
    rec["system"] = system
    tmp = ledger_path(system).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    tmp.replace(ledger_path(system))


def replace_ledger(system: str, rec: dict) -> None:
    """Write a record WHOLESALE, replacing what is there instead of merging into it.

    `write_ledger` merges, which is right for a deploy: each step adds what it learned. The
    drill needs the opposite. It deliberately drives the ledger through `rolled-back` and
    then has to put back exactly what it found, and a merge cannot REMOVE the `failed_at`
    and `attempted` keys that transit leaves behind — so a merge-restored record would
    describe a failure that never happened, on a system that is running fine."""
    ledger_dir().mkdir(parents=True, exist_ok=True)
    rec = dict(rec)
    rec["system"] = system
    tmp = ledger_path(system).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    tmp.replace(ledger_path(system))


def _decided(status: str, tag: Optional[str]) -> dict:
    """A status and the tag it was decided FOR, written together or not at all (WI-0412).

    The pairing is the whole point. `cutover_event` already carries its tag for this exact
    reason and says so in its own docstring: every field outlives the run that wrote it, so
    a record that does not name its subject is read as being about the current one.

    `tag` may be None where a refusal genuinely precedes tag resolution — written as null,
    which `diagnose` renders as "decided for an unnamed tag" rather than silently treating
    it as matching."""
    return {"status": status, "status_tag": tag}


def _status_vouches_for(system: str, tag: str) -> bool:
    """Does the ledger's verdict actually describe THIS tag? (WI-0416)

    `before == target` says the tag is on the tree. That is an observation about a
    directory, and a directory can hold a tag that a refusal put there — `checkout`
    precedes `seed_state`, so a deploy that refuses at the state gate leaves the target
    checked out with nothing deployed. The ledger's `status_tag` is the only field that
    says which tag the verdict was decided FOR, so it is the only one that can turn
    "the tag is here" into "the tag is running".

    AN ABSENT `status_tag` COUNTS AS NOT VOUCHED, and this is the decision in the
    function. Every ledger written before WI-0412 lacks the field, so the conservative
    reading costs one extra full deploy per member, once, after which every record carries
    a binding. The other reading — absent means fine — lets a `status: deployed` decided
    for an older tag vouch for a newer one that refused on the previous sweep. This is the case where "cannot tell" and
    "fine" lead to opposite actions, so they must not share an answer."""
    rec = read_ledger(system)
    if not rec or rec.get("_unreadable"):
        # No record, or one we cannot parse: nothing vouches for anything.
        return False
    return rec.get("status_tag") == tag


def _why_unvouched(system: str, tag: str) -> str:
    """The reason, for the operator, in the sentence that announces the redeploy."""
    rec = read_ledger(system)
    if rec.get("_unreadable"):
        return "the ledger could not be parsed"
    if not rec:
        return "there is no ledger record for this system"
    if "status_tag" not in rec:
        return (f"its status={rec.get('status')!r} predates status binding and does not "
                f"say which tag it was decided for")
    decided = rec.get("status_tag")
    return (f"its status={rec.get('status')!r} was decided for "
            f"{decided or 'an unnamed tag'}, not {tag}")


def _apply_receipt(tag: str) -> dict:
    """Which tag's apply actually RAN on this machine (WI-0411).

    STAMPED WITH ITS TAG, for the reason `cutover_event` is: `write_ledger` merges, so
    every field outlives the run that wrote it. An unstamped `applied: true` would survive
    into the next release's record and report last month's apply as this one's — and this
    field's whole job is to be trusted about exactly that."""
    return {"tag": tag, "at": _now()}


def rerun_apply_refused(contract: dict) -> bool:
    """Is re-running this member's apply something the member has NOT declared safe?

    DEFAULT IS REFUSE, and it is the conservative direction on purpose. The receipt is new,
    so on the first sweep after it ships NO member has one — and an opt-out default would
    re-run every apply-strategy member's apply at once, on machines nobody is watching,
    on the strength of an assumption about scripts this program did not write.

    Consider an `apply.sh` that is a thin delegation to a verb some other tool installs on
    the machine's PATH. Reading that verb's script may show it recomputes everything from
    HEAD and passes a tag, so re-running it CONVERGES — same tag in, same state out. But its
    contract may also have side effects elsewhere, in code this Architect cannot reach and
    did not read. Convergence can be established while idempotency of the thing actually
    being invoked is not, and the difference is a side effect somewhere else.

    So the member declares it, in its contract, where the apply script it describes also
    lives. `{"apply": {"cmd": [...], "rerunnable": true}}`. A member that has not declared
    keeps exactly the behaviour it has today."""
    return not bool((contract.get("apply") or {}).get("rerunnable"))


def record_refusal(system: str, message: str, attempted: Optional[str],
                   trigger: str) -> None:
    """Record that a deploy REFUSED, without disturbing what is still running (WI-0410).

    ADDITIVE ON PURPOSE, and the restraint is the design. The obvious move is to write
    `status="refused"`, and it is wrong: `status` is the verdict on the version that is
    LIVE, and three readers act on it — `anything_cut_over` gates the whole sealed-model
    question on `status == "deployed"` (runner.py), `drillable_system` refuses to drill
    anything else, and `install-deploy-sweep.sh` greps the literal string. Overwriting it
    to describe an attempt that never touched production would make a machine that refused
    one deploy look like a machine that has never cut over at all.

    So the refusal is its OWN key, beside the live fields rather than on top of them. A
    reader that knows nothing about it is unaffected; a reader that does can tell all three
    states apart — never deployed, deployed and idle, deployed and the last attempt was
    turned away.

    `attempted` is None when the refusal preceded tag resolution, and that is written as a
    null rather than omitted: "we could not even work out what we were reaching for" and
    "we know exactly which tag was refused" are different facts, and the bundle renders
    them differently ([`declare-what-a-check-assumes`])."""
    write_ledger(system, refusal={
        "at": _now(),
        "attempted": attempted,
        "trigger": trigger,
        # CLIPPED, and from the HEAD. A DeployError carries the operator's whole
        # explanation — `seed_state`'s refusal alone is five sentences naming three
        # sources — and the ledger is read by surfaces that render it inline. The head is
        # the half that names what failed; the tail is the advice.
        "message": evidence.clip((message or "").strip(), 2000, keep="head"),
    })


def deployed_tag(system: str, entry: dict) -> Optional[str]:
    """The tag this runner actually DEPLOYED into the tree — ground truth, consulted
    instead of trusting the ledger's claim about itself. The ledger is a convenience;
    this is the fact ([`a-close-is-the-banner-not-the-sentence`] applied to a deploy).

    HEAD MUST BE DETACHED for this to answer with a tag, and that condition is the whole
    correctness of the function rather than a detail. A deploy always leaves the tree
    detached at a tag (ADR-0103 D3), so anything still sitting on a *branch* has not been
    deployed — including a freshly cloned tree, whose HEAD points at the default branch
    and whose working tree may be entirely empty.

    Without the detachment test this returned the branch tip's tag on a brand-new clone,
    the caller compared it to the target, found them equal, and reported *"already running
    v1.0.0 — nothing to do"* over an empty directory. A first deploy would have silently
    done nothing and exited 0. Caught by the suite before it ever ran for real, which is
    the entire argument for writing the suite first."""
    tree = deploy_tree(system)
    if not (tree / ".git").exists():
        return None
    if git(["symbolic-ref", "--quiet", "HEAD"], tree, timeout=30).returncode == 0:
        return None      # on a branch → never deployed by this runner
    r = git(["describe", "--tags", "--exact-match", "HEAD"], tree, timeout=30)
    return r.stdout.strip() if r.returncode == 0 else None


# ── contract ─────────────────────────────────────────────────────────────────────

def _json_type(value: Any) -> str:
    """JSON types, keeping Python's bool subclass distinct from integer."""
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", bool: "boolean",
            int: "integer", float: "number"}[type(value)]


def _validate_contract(value: Any, schema: dict, system: str, path: str = "") -> None:
    """Walk the contract schema's structural vocabulary without inserting defaults.

    This is a small stdlib validator, not a general JSON Schema implementation.
    Version compatibility and system identity remain read_contract's semantic checks.
    """
    def refuse(expected: str, found: str) -> None:
        raise DeployError(
            f"{system}: contract {path or '$'}: expected {expected}, found {found}")

    kind = _json_type(value)
    found = f"{kind} {json.dumps(value, ensure_ascii=False)}"

    # `oneOf` FIRST, because it decides which of several shapes the rest of this function
    # should even be reading. A contract is sealed inside its tag and cannot be edited
    # after release, so a field whose accepted shape CHANGED must keep accepting the older
    # form for as long as any tagged contract still uses it — `state` entries were once
    # bare strings, and a real member's early tag still carries them (WI-0358). A branch that
    # validates is the answer; only when none does is this a refusal, and then the message
    # names every shape that would have been accepted rather than just the last one tried.
    if "oneOf" in schema:
        for branch in schema["oneOf"]:
            try:
                _validate_contract(value, branch, system, path)
                return
            except DeployError:
                continue
        shapes = " or ".join(
            (b.get("type") or "object") + (
                " with " + ", ".join(json.dumps(k) for k in b["required"])
                if b.get("required") else "")
            for b in schema["oneOf"])
        raise DeployError(
            f"{system}: contract {path or '$'}: expected {shapes}, found {found}")

    expected = schema.get("type")
    if expected and kind != expected and not (expected == "number" and kind == "integer"):
        if expected == "object" and schema.get("required"):
            expected += " with " + ", ".join(json.dumps(k) for k in schema["required"])
        refuse(expected, found)
    if "enum" in schema and not any(
            value == allowed and _json_type(value) == _json_type(allowed)
            for allowed in schema["enum"]):
        refuse(f"one of {json.dumps(schema['enum'])}", found)
    if isinstance(value, dict):
        def child(key: str) -> str:
            return f"{path}.{key}" if path else key

        for key in schema.get("required", []):
            if key not in value:
                raise DeployError(f"{system}: contract {child(key)}: "
                                  "expected required property, found missing")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate_contract(item, properties[key], system, child(key))
                continue
            # patternProperties is checked BEFORE additionalProperties, per JSON Schema:
            # a key matching a declared pattern is not "additional" at all. Without this
            # the schema could declare a pattern the validator silently ignored, and a
            # contract already sealed inside a tag would be refused by a rule its own
            # schema says it satisfies.
            matched = [p for p in patterns if re.search(p, key)]
            if matched:
                for p in matched:
                    _validate_contract(item, patterns[p], system, child(key))
                continue
            if additional is False:
                raise DeployError(
                    f"{system}: contract {child(key)}: expected no additional properties, "
                    f"found {_json_type(item)} {json.dumps(item, ensure_ascii=False)}")
            elif isinstance(additional, dict):
                _validate_contract(item, additional, system, child(key))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_contract(item, schema["items"], system, f"{path}[{index}]")


def read_contract(system: str, entry: dict, tag: str, *,
                  tree: Optional[Path] = None) -> dict:
    """The contract comes FROM THE TAG, never from the working tree (ADR-0103 D5).

    Reading it from the checkout would mean a deploy is configured by whatever happens to
    be on disk — including, after a failed deploy, the contract of a version that is no
    longer running. From the tag, the contract and the code it describes are the same
    object and cannot disagree.

    `tree` names WHICH clone to read the tag out of, defaulting to the production tree.
    The answer is the same either way — a tag is the same object in every clone that has
    fetched it — so this is about which clone has the object, not about which contract is
    authoritative. The canary path passes its own tree because that is the only one it is
    allowed to have fetched into."""
    tree = tree or deploy_tree(system)
    sub = entry.get("subdir")
    relpath = f"{sub}/{CONTRACT_RELPATH}" if sub else CONTRACT_RELPATH
    r = git(["show", f"{tag}:{relpath}"], tree, timeout=60)
    if r.returncode != 0:
        raise DeployError(
            f"{system} {tag} carries no deploy contract at {relpath}. A tag without a "
            f"contract is not deployable — its Architect owes one (ADR-0103 D11). "
            f"Nothing was changed.")
    try:
        contract = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise DeployError(f"{system} {tag}: {relpath} is not valid JSON — {e}")

    schema = json.loads((REPO_ROOT / "deploy" / "contract.schema.json").read_text())
    try:
        _validate_contract(contract, schema, system)
    except DeployError as e:
        write_ledger(system, message=str(e), **_decided("contract-invalid", tag))
        raise

    cv = contract.get("contract_version")
    if cv != SUPPORTED_CONTRACT_VERSION:
        # Refuse an unknown generation rather than guess which fields still mean what.
        raise DeployError(
            f"{system} {tag}: contract_version {cv!r} — this runner speaks "
            f"{SUPPORTED_CONTRACT_VERSION}. Refusing rather than guessing.")
    if contract.get("system") != system:
        raise DeployError(
            f"{system} {tag}: contract names system {contract.get('system')!r}. A repo "
            f"being deployed as something it is not is a copy-paste, not a deploy.")

    # NORMALISED ONCE, HERE, so exactly one shape exists past this boundary. The schema
    # accepts a `state` entry as either a bare string (the pre-object form, still sealed
    # in a real member's early tag) or an object; `seed_state` reads `item["path"]` and would raise
    # TypeError on a string. Accepting a shape at the gate and crashing on it two
    # functions later is worse than refusing it, so the compatibility is completed here
    # rather than spread across every consumer (WI-0358).
    state = contract.get("state")
    if isinstance(state, list):
        contract["state"] = [{"path": s, "required": False} if isinstance(s, str) else s
                             for s in state]
    return contract


def refuse_self_restart(system: str, entry: dict, contract: dict, tag: str) -> None:
    """A self system may only declare `restart: "none"`.

    Every unit of the self system runs code this process is made of — the sweep's own
    unit is necessarily in the list. `kickstart -k` on it kills the sweep that is issuing
    the command, mid-deploy, before any ledger write: the next sweep finds the work undone
    and does it again, forever, and nothing ever reports why. `none` is the correct
    strategy and not a skipped step — these are scheduled one-shots, so the next fire
    already runs the new tag (WI-0224).

    Called on the FORWARD path only, deliberately. It lived inside `read_contract` first,
    which also reads the PREVIOUS tag's contract during a rollback — so a restore that had
    already put the old tree back correctly would raise here, be caught as
    `RollbackFailed`, and exit 2: the loudest state this program has, raised over a
    working rollback. A guard that manufactures its own worst alarm is worse than the
    thing it guards.

    What it does NOT cover: an `apply`-strategy contract (ADR-0103 D8 shape 2) runs an
    arbitrary command, so a self system could restart its own units from inside one and no
    check here could tell. Nothing declares that today; it is named so the next reader
    knows the guard's edge rather than inferring a promise it does not make."""
    if not (entry.get("self") is True and contract.get("units")):
        return
    if contract.get("restart", "kickstart") == "none":
        return
    raise DeployError(
        f"{system} {tag}: contract declares restart "
        f"{contract.get('restart', 'kickstart')!r}, but this system is the registry's "
        f"'self' — the runner is made of its code. Restarting its units from inside a "
        f"sweep restarts the sweep. Declare restart 'none'; the next scheduled fire "
        f"runs the new tag. Nothing was changed.")


#: The contract keys whose value is a command this machine has to be able to run.
#: `smoke`, `verify` and `apply` are every key in `deploy/contract.schema.json` that
#: carries a `cmd`. A fourth added there belongs here too, and what says so is
#: `TheContractVerbsAreResolvedBeforeTheCheckoutTest`, which derives the set from the
#: schema and fails on the difference — not a reader remembering to come back.
COMMAND_KEYS = ("smoke", "verify", "apply")


def refuse_unresolvable_verbs(system: str, contract: dict, log) -> None:
    """Name a contract verb this machine cannot run BEFORE the deploy touches anything.

    THE COST THIS REMOVES. Today a verb that is not there is discovered at the step that
    needs it: after the fetch, after the checkout, after the state seed and the dependency
    sync, after the smoke gate — with the tree already moved to the new tag. For `verify`
    it is worse than late, because a verify that cannot RUN is indistinguishable from a
    verify that FAILED, and the answer to a failed verify is to roll a healthy deploy
    back. Asked here, the same fact costs one line and the running version is never
    disturbed.

    WHAT IT RESOLVES, AND WHAT IT DELIBERATELY DOES NOT ([`declare-what-a-check-assumes`]).
    It asks `shutil.which` of `cmd[0]`, and of `cmd[0]` only, for each declared command,
    and it asks it of THIS PROCESS'S PATH — which under launchd is the unit's PATH, the
    same environment `run()` hands the subprocess. Two things are outside it, and both are
    logged as their own answer rather than folded into a pass:

      * **An argv[0] naming a path** — `/bin/sh`, `deploy/apply.sh` — is not a PATH
        question. A relative one resolves inside the deploy tree *after* the checkout, so
        judging it here would refuse every new tag that ADDS its own apply script.
      * **A verb the member's own script resolves internally.** A real incident took this
        shape exactly: the contract declared `/bin/sh deploy/apply.sh`, and the verb name
        that could not be found was inside that script, which refused correctly and by
        name. No preflight in this runner can see that, and implying
        otherwise would make this check read as a guarantee it cannot give. What the
        runner CAN do for that case it now does: `run_apply` reports the PATH it searched
        alongside the failure, so the next reader is not left to guess at it.

    A refusal, not a warning. A missing verb does not become present between here and the
    step that needs it, so continuing only buys a worse-placed failure."""
    unresolved: list[str] = []
    deferred: list[str] = []
    resolved: list[str] = []
    for key in COMMAND_KEYS:
        cmd = (contract.get(key) or {}).get("cmd") or []
        if not cmd:
            continue
        verb = cmd[0]
        if os.sep in verb:
            deferred.append(f"{key}:{verb}")
        elif shutil.which(verb) is None:
            unresolved.append(f"{key} names {verb!r}")
        else:
            resolved.append(f"{key}:{verb}")
    log("preflight: "
        + (f"resolved {', '.join(resolved)}" if resolved else "no verb to resolve on PATH")
        + (f"; not a PATH question, left to the checkout: {', '.join(deferred)}"
           if deferred else ""))
    if not unresolved:
        return
    raise DeployError(
        f"{system}: the contract names a command this machine cannot resolve — "
        f"{'; '.join(unresolved)}. PATH is {os.environ.get('PATH', '')!r}. "
        f"Refused BEFORE the checkout, so the running version is untouched and nothing "
        f"was restarted. Install the verb, or add its directory to the PATH declared by "
        f"the unit running this sweep; the next sweep then deploys with no further act.")


# ── phases ───────────────────────────────────────────────────────────────────────

def ensure_tree(system: str, entry: dict, log) -> Path:
    """Clone the deploy tree if it does not exist. A separate tree from the developer's
    clone is the whole point (ADR-0103 D3) — production is not a folder anyone edits."""
    tree = deploy_tree(system)
    if (tree / ".git").exists():
        return tree
    if tree.exists() and any(tree.iterdir()):
        raise DeployError(f"{tree} exists and is not a git clone — refusing to touch it.")
    tree.parent.mkdir(parents=True, exist_ok=True)
    log(f"cloning {entry['remote']} → {tree}")
    r = run(["git", "clone", "--no-checkout", entry["remote"], str(tree)], timeout=900)
    if r.returncode != 0:
        raise DeployError(f"clone failed — {(r.stderr or r.stdout).strip()}")
    return tree


def newest_tag(tree: Path, prereleases: bool = False) -> Optional[str]:
    """Highest semver tag. Sorted numerically, never lexically — `v0.10.0` must beat
    `v0.9.0`, which a string sort gets backwards and which is the kind of bug that only
    shows up on the tenth release.

    RELEASE CANDIDATES ARE EXCLUDED BY DEFAULT, and the default is the load-bearing half.
    This function is what the unattended sweep asks *"what should be running?"*, so if a
    candidate could win here, tagging `v2.0.0-rc.1` to try something would deploy it to
    production within ten minutes with nobody in the room. `prereleases=True` exists for
    the one caller that is explicitly asking about candidates — `--canary` resolving "the
    newest thing to try" — and that caller restarts nothing."""
    out = git(["tag", "--list"], tree, timeout=60).stdout.split()
    versions = [(tag_order(m), t) for t in out
                if (m := SEMVER_TAG_RE.match(t))
                and (prereleases or m.group(4) is None)]
    return max(versions)[1] if versions else None


def resolve_target(system: str, tree: Path, version: Optional[str],
                   rollback: bool) -> str:
    if rollback:
        prev = read_ledger(system).get("previous")
        if not prev:
            raise DeployError(
                f"{system}: nothing to roll back to — the ledger records no previous "
                f"version. A first deploy has no 'back'.")
        version = prev
    if version is None:
        tag = newest_tag(tree)
        if tag is None:
            raise DeployError(
                f"{system}: no semver tag on origin. Nothing is deployable until its "
                f"Architect cuts a release — deploying the branch tip is exactly what "
                f"ADR-0103 D2 refuses.")
        return tag
    if not version.startswith("v"):
        version = f"v{version}"
    if is_prerelease(version):
        # THE NAMED PATH NEEDS THE SAME ANSWER AS THE RESOLVED ONE. `newest_tag` will never
        # hand a candidate to production, but `--version v2.0.0-rc.1` walks straight past
        # it, and a rule enforced on one of two routes is a rule about which route was
        # taken rather than about the hazard. A candidate is by definition the tag whose
        # release decision has NOT been made; deploying one to production makes that
        # decision by typing, which is the promotion-by-accident D2 exists to refuse. The
        # escape is a real path rather than a suggestion, so this costs nobody the
        # capability — it moves it somewhere that restarts nothing.
        raise DeployError(
            f"{system}: {version} is a release CANDIDATE, not a release. Production runs "
            f"releases (ADR-0103 D2) — cutting the final tag is the promotion decision, "
            f"and a candidate is the tag that says it has not been made yet. To exercise "
            f"this tag without touching production: poga deploy {system} --canary {version}")
    if git(["rev-parse", "--verify", f"{version}^{{commit}}"], tree, 30).returncode != 0:
        raise DeployError(f"{system}: tag {version} does not exist on origin.")
    return version


def state_vault(system: str) -> Path:
    """Machine-local copies of a system's seeded state, OUTSIDE every tree that gets wiped.

    THE SEED SOURCE HAS TO OUTLIVE THE TREE. The first cut of `write_seed_map` pointed each
    entry at the deploy tree's own copy, which reads sensibly and cannot work: `seed_state`
    skips any path already present, so the map is consulted only when the file is ABSENT from
    the tree — and an entry pointing into that same tree is then absent too. It could satisfy
    the `required` check and nothing else. `seed_state` therefore asks the vault BY NAME, as
    its second source tier, rather than reaching it through the map: the map is read from the
    runner's REPO_ROOT and written into the deploy tree, which are the same file only for the
    `self` row after cutover, so for every other system a map-only route is write-only. Both
    defects were caught in review by the tests for this change — written against the claim
    rather than against the code, which is the only way they could have been.

    Beside the ledger, for the ledger's own reason: it describes what runs on THIS machine,
    it is machine-local by construction, and it is deliberately outside the repo so a
    `checkout --force`, a rollback, or deleting the deploy tree outright cannot take it
    ([P21](../principles/master.md#p21--state-survives-failure)). A tree can be thrown away
    and re-cloned and the state comes back."""
    return ledger_dir() / f"{system}-state"


def vault_state(system: str, entry: dict, contract: dict, log) -> None:
    """Mirror whatever state the deploy tree now holds into the vault.

    ONE DIRECTION ONLY, and never a move. The tree is the live copy the units read; the vault
    is a recovery source, and a vault that could write back into the tree would be a second
    writer on state the running system owns (P13). Files are copied only when the tree's copy
    is newer; directories are copied every time, for the reason below."""
    wd = work_dir(system, entry)
    for item in contract.get("state", []):
        src, dest = wd / item["path"], state_vault(system) / item["path"]
        if not src.exists():
            continue
        try:
            # COPY-IF-NEWER APPLIES TO FILES ONLY. A directory's mtime moves when an entry is
            # added or removed and NOT when a file inside it is edited in place, so an
            # mtime comparison would silently stop re-vaulting a directory state path after
            # its first in-place edit — the contract shape `seed-paths.local.example.json`
            # documents as supported. The federation's own three paths are files and none of
            # them would have hit this; a guard that is only correct for today's data is how
            # it reaches tomorrow's. Directories are re-copied wholesale: they are small,
            # and the tree is the live copy the units read, so it is always the authority.
            if (src.is_file() and dest.exists()
                    and dest.stat().st_mtime >= src.stat().st_mtime):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
        except OSError as e:
            log(f"state: could not vault {item['path']} — {e}")


def seed_paths(system: str) -> dict:
    """This machine's answer to "where does <system>'s state come from the first time?".

    Machine-local BY CONSTRUCTION, and excluded from version control. The two obvious
    places to put these — the system's own tracked contract, or the federation's tracked
    registry — are wrong for the same reason: an absolute Runner path committed to a
    tracked file is a per-machine fact stored at one shared path, which is WI-0132
    exactly. Keeping them here means a contract stays true on every machine, and this
    file only has to be true on one."""
    p = production.config_path(REPO_ROOT, "seed-paths.local.json",
                               os.environ.get("POGA_DEPLOY_SEEDS",
                                              REPO_ROOT / "deploy" / "seed-paths.local.json"))
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("systems", {}).get(system, {})
    except (json.JSONDecodeError, OSError):
        return {}


def retiring_root(system: str, entry: dict, contract: dict) -> Optional[Path]:
    """The checkout this system is being MOVED OFF, or None.

    THE SEED PROBLEM THIS ANSWERS. `seed_paths` is a machine-local map somebody has to
    write, and nothing guarantees anybody does: without one, a sweep reports every one
    of the federation's state files "absent and this machine declares no seed source",
    which after cutover means the adopt-runner cannot locate a single member repo. A first
    deploy that needs a human to hand-write a path map before it works is the manual step
    this wave exists to delete -- so the map stops being a precondition and becomes an
    OUTPUT (see `write_seed_map`), and the first deploy derives what it needs.

    DERIVED FROM THE RUNNING UNITS, which is the one source that cannot be stale: a loaded
    job keeps the WorkingDirectory it was bootstrapped with, so `unit_target` reports where
    this system is executing from RIGHT NOW, whatever any file claims. Anything already
    inside the deploy tree is not a source -- that is where we are going, not where we came
    from.

    The fallback is this runner's own checkout, and only for the `self` row: on a first
    self-deploy the program doing the seeding IS the retiring process root, which makes it
    the most direct answer available. It is second rather than first because a unit's live
    WorkingDirectory is evidence and an executable's location is an inference -- they agree
    on the ordinary path, and when they disagree the running job is the one that is true."""
    wd = str(work_dir(system, entry))
    for unit in contract.get("units", []):
        target = unit_target(unit)
        if not target or target == wd or target.startswith(wd + "/"):
            continue
        cand = Path(target)
        if (cand / ".git").exists():
            return cand
    if entry.get("self") is True and not channel.is_deploy_tree(REPO_ROOT):
        return REPO_ROOT
    return None


def write_seed_map(system: str, entry: dict, contract: dict, log, dry_run: bool) -> bool:
    """Record the deploy tree as its own seed source, so no later deploy has to derive one.

    THE MAP IS AN OUTPUT, NOT A PRECONDITION, and that inversion is the whole of W2. Read
    as an input it is a file a human must write on each machine before the first deploy
    works -- and the machine it was most needed on had never had one written. Written as an
    output it is produced by the one deploy that could still see the retiring checkout, and
    from then on every deploy answers "where does this state come from?" with "from where it
    already is", which is true, machine-local, and needs nobody.

    Still `.local` and still gitignored (P3): it carries this machine's absolute paths, and
    committing those is WI-0132 exactly -- the reason `seed_paths` explains at length."""
    wd = work_dir(system, entry)
    vault = state_vault(system)
    paths = [i["path"] for i in contract.get("state", [])]
    if not paths:
        return False
    dest = wd / "deploy" / "seed-paths.local.json"
    # ONLY WHAT THE VAULT ACTUALLY HOLDS. A map entry naming a path that is not there is
    # worse than a missing entry: `seed_state` reports "seed source does not exist" and
    # stops looking, so a declared-but-absent source SUPPRESSES the derivation that would
    # have found a real one.
    mapped = {rel: str(vault / rel) for rel in paths if (vault / rel).exists()}
    doc = {
        "//": "GENERATED by deploy/runner.py at deploy time (WI-0360). Machine-local and "
              "gitignored: it names absolute paths on THIS machine only. Each entry points "
              "at the machine-local STATE VAULT beside the ledger, never at the deploy tree "
              "— the tree is what a rollback or a re-clone throws away, and a map pointing "
              "inside it could only ever name a file that is missing for the same reason "
              "the seed was needed.",
        "systems": {system: mapped},
    }
    if dry_run:
        log(f"state: would record {system}'s seed map at {dest}")
        return True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        log(f"state: could not write the seed map at {dest} — {e}")
        return False
    log(f"state: recorded {system}'s seed map at {dest.name}")
    return True


def member_root() -> Path:
    """Where this machine keeps member repositories — the walk root a derived
    `reconcile-roots.local` names.

    REDIRECTABLE, and that is not a testing convenience. A bare `Path.home() / "Projects"`
    reads the developer's real home from inside the suite, so `seed_state`'s refusal tests
    would pass or fail depending on whether the person running them happens to have that
    directory — the fixture-leak shape WI-0361's redirect guard exists to catch, and it
    caught this one. Every other machine-local root the runner resolves (`deploy_root`,
    `ledger_dir`, `comms_dir`) is an env override over a home-relative default; this is the
    same shape for the same reason."""
    return Path(os.environ.get("POGA_DEPLOY_MEMBER_ROOT", Path.home() / "Projects"))


def _derive_reconcile_roots(system: str, entry: dict) -> Optional[str]:
    """This machine's member-walk roots, or None when none can be established.

    NONE IS A REAL ANSWER HERE and is the reason this returns Optional rather than always
    producing a file. An empty roots file is syntactically valid and semantically a lie: it
    parses to `[]`, every member reads as UNLOCATED, and the deploy that wrote it reports
    success. Refusing instead leaves the required-state check to fail — which since WI-0410
    reaches the ledger and the posted bundle rather than only a launchd log, so the gap is
    visible rather than silently certified ([`ship-the-detector-with-the-capability`])."""
    root = member_root()
    if not root.is_dir():
        return None
    return (
        "# reconcile-roots — DERIVED by deploy/runner.py on first deploy (WI-0410).\n"
        "#\n"
        "# NOT hand-authored, and worth knowing which. No source on this machine held this\n"
        "# file — not the seed map, not the state vault, not a retiring checkout — so the\n"
        "# deploy derived it rather than refusing, on the reasoning that a first deploy\n"
        "# that needs a human to write a path map before it works is the manual step this\n"
        "# arrangement exists to delete.\n"
        "#\n"
        "# THE ASSUMPTION, stated so it can be falsified: members live under ~/Projects on\n"
        "# this host. That is true of devbox and is the portfolio convention; it was NOT\n"
        "# verified for any other machine. If this host keeps member repos anywhere else,\n"
        "# edit this file — an existing file is never overwritten by a later deploy.\n"
        f"\n{root}\n")


def _derive_repo_paths(system: str, entry: dict) -> Optional[str]:
    """Locate the system being deployed, for the members a walk root cannot reach.

    ONLY THE `self` ROW, and only its own tree. This map's job is to name repos that sit
    outside the walk roots, and the one such repo this program can name with certainty is
    the one it is deploying INTO — `work_dir` is where that system's code lives on this
    machine, now, by construction rather than by inference.

    Deliberately NOT derived for any other row: guessing another member's location from a
    machine that may never have cloned it would write a confident wrong locator, and a
    wrong locator is worse than an absent one — `reconcile` reports a mapped path whose
    STATUS.md does not resolve as BROKEN MAP, but only after it has stopped walking for
    that id."""
    if entry.get("self") is not True:
        return None
    return (
        "# repo-paths — DERIVED by deploy/runner.py on first deploy (WI-0410).\n"
        "#\n"
        "# Only systems whose repo is NOT under a reconcile-roots.local walk root need a\n"
        "# row; the rest are found by the walk. The single row below is this machine's\n"
        "# deploy tree for the system that wrote it, which is where that system's code\n"
        "# actually runs here after cutover.\n"
        "#\n"
        "# An existing file is never overwritten by a later deploy — edit freely.\n"
        f"\n{system} = {work_dir(system, entry)}\n")


# WHICH STATE FILES CAN BE DERIVED, AND WHY THIS IS A TABLE RATHER THAN A CONTRACT KEY.
#
# The obvious shape is a `"derive": true` flag on the contract's own `state` entry, and it
# was rejected for a reason that is easy to forget: the contract is read from the TAG
# (`read_contract(system, entry, target)`), and a tag is immutable — so a contract-side
# flag cannot be set for any release already cut, which is the entire population this
# derivation exists to unstick. The registry's `cutover: auto` key carries a comment making
# exactly this argument for exactly this reason.
#
# Keyed by the contract-relative path, so a member whose contract happens to declare a state
# file of the same name gets the same treatment — which is right: these two names are
# federation substrate, and a member declaring them means the same thing by them.
#
# A path absent from this table derives nothing and refuses exactly as it did before, so no
# existing member changes behaviour by being upgraded past this commit.
DERIVABLE_STATE = {
    "reconcile-roots.local": _derive_reconcile_roots,
    "repo-paths.local": _derive_repo_paths,
}


def seed_state(system: str, entry: dict, contract: dict, log, dry_run: bool,
               notes: Optional[list] = None) -> list[str]:
    """Put declared state into the deploy tree ON FIRST DEPLOY only.

    `notes` is an optional sink for the events that must reach a HUMAN rather than only a
    log. A fall-through — the operator's declared source was missing and state came from
    somewhere else — is loud in launchd's log and silent in everything that leaves the
    machine, so a 03:15 sweep that quietly seeded from a weeks-old vault copy reaches a
    person as "DEPLOYED". That is a silent wrong answer wearing a success, which is the one
    outcome this whole arrangement exists to stop. What goes in here is rendered into the
    posted receipt by `result_brief`.

    COPY, never move, and never overwrite something already there. The developer clone
    keeps its copy as a fallback, and a second deploy finds the state present and leaves
    it entirely alone — which is what makes state survive both deploys and rollbacks."""
    seeded: list[str] = []
    wd = work_dir(system, entry)
    seeds = seed_paths(system)
    # Derived ONCE, and only if something is actually missing: it costs a `launchctl print`
    # per unit, and the steady state is that every state file is already there.
    derived: Any = False
    for item in contract.get("state", []):
        dest = wd / item["path"]
        if dest.exists():
            continue
        # THREE TIERS, TRIED IN ORDER, AND A TIER THAT NAMES A MISSING PATH IS NOT AN ANSWER.
        #
        #   1. this machine's declared seed map  — what an operator wrote down;
        #   2. this machine's state vault        — what the last deploy of this system kept;
        #   3. the retiring checkout             — where the system runs from today.
        #
        # A NAMED-BUT-ABSENT SOURCE FALLS THROUGH rather than ending the search. The first
        # cut short-circuited on tier 1: a declared entry was taken as the answer, and when
        # the file was not there it logged "seed source does not exist" and gave up — so ONE
        # stale line in a hand-written map, on a machine whose paths had moved, would silence
        # both the vault and the derivation and refuse a deploy with two working sources
        # sitting right there. A map is a hint about where state MIGHT be, never a claim that
        # it is nowhere else.
        #
        # Tier 2 is asked BY NAME rather than reached through the map, because the map is
        # read from the runner's REPO_ROOT and written into the deploy tree — the same file
        # only for the `self` row after cutover, so for every other system a map-only route
        # to the vault is write-only. Needing no pointer is the whole reason the vault sits
        # outside the tree.
        src = how = None
        tiers = [("declared in this machine's seed map", seeds.get(item["path"])),
                 ("recovered from this machine's state vault",
                  str(state_vault(system) / item["path"]))]
        for i, (why, cand) in enumerate(tiers):
            if not cand:
                continue
            if not Path(cand).exists():
                if i == 0:
                    log(f"state: the seed map names {cand} for {item['path']} and it is not "
                        f"there — looking further rather than giving up")
                    if notes is not None:
                        notes.append(
                            f"`{item['path']}`: this machine's seed map names `{cand}`, "
                            f"which is NOT THERE. The deploy looked further rather than "
                            f"refusing — if that path is an unmounted volume holding the "
                            f"authoritative copy, the state seeded below is not the state "
                            f"you declared.")
                continue
            src, how = Path(cand), why
            break
        if src is None:
            # Tier 3 is derived ONCE per call and only once the cheap tiers have missed: it
            # costs a `launchctl print` per unit, and the steady state is that the file is
            # simply already present and this loop never runs at all.
            if derived is False:
                derived = retiring_root(system, entry, contract)
            if derived is not None and (derived / item["path"]).exists():
                src = derived / item["path"]
                how = (f"derived from the checkout this system is being moved off "
                       f"({derived})")
        if src is None and item["path"] in DERIVABLE_STATE:
            # TIER 4: DERIVE IT FROM THE MACHINE, the way `write_seed_map` already derives
            # `seed-paths.local.json`. Last, never first — a real copy from any of the
            # three sources is evidence about what this system was actually using, and a
            # derivation is a reasoned default. When evidence exists it wins.
            #
            # This tier is why a SECOND federation release can reach `apply` at all. A
            # host with all three sources empty (no seed map, an empty state vault, no
            # retiring root) refuses at this line, and a seed-from-source design refuses
            # at the same line on every rebuild, forever, because the source it wants is
            # the thing that does not exist. Ruled plumbing; reversible at the sitting.
            body = DERIVABLE_STATE[item["path"]](system, entry)
            if body is not None:
                log(f"state: deriving {item['path']} from this machine — no source held it")
                if notes is not None:
                    # ALWAYS a note, never conditional. Seeding from a declared map is the
                    # operator getting what they asked for; a DERIVED file is this program
                    # deciding something on their behalf, on a machine nobody was watching.
                    # That reaches the posted receipt or it reaches nobody.
                    notes.append(
                        f"`{item['path']}`: DERIVED from this machine — no source held it "
                        f"(seed map, state vault and retiring checkout were all tried). "
                        f"The file records what was assumed and is never overwritten by a "
                        f"later deploy; read it and correct it if this host differs.")
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(body, encoding="utf-8")
                seeded.append(item["path"])
                continue
            log(f"state: {item['path']} could not be derived on this machine either")

        if src is None:
            log(f"state: {item['path']} absent, and none of the three sources has it — "
                f"the seed map, this machine's state vault, and the checkout this system "
                f"runs on today were all tried. Leaving it to the system to create.")
            continue
        log(f"state: seeding {item['path']} from {src} ({how})")
        if notes is not None and not how.startswith("declared"):
            # Only the non-declared sources are news. Seeding from the map is the operator
            # getting what they asked for and does not need to reach anybody.
            notes.append(f"`{item['path']}`: seeded from {src} ({how}).")
        if dry_run:
            seeded.append(item["path"])
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        seeded.append(item["path"])

    # ORDER IS LOAD-BEARING: vault what the tree now holds, THEN write a map that can only
    # name what the vault actually has, THEN judge. The map is itself one of the entries the
    # verdict reads, and it is produced here rather than copied from anywhere.
    if not dry_run:
        vault_state(system, entry, contract, log)
    write_seed_map(system, entry, contract, log, dry_run)

    missing = [i["path"] for i in contract.get("state", [])
               if i.get("required") and not (wd / i["path"]).exists()]
    if missing and not dry_run:
        raise DeployError(
            f"{system}: required state absent after seeding — {', '.join(missing)}. "
            f"All THREE sources were tried and none of them had it: this machine's seed map, "
            f"its state vault at {state_vault(system)}, and the checkout this system runs on "
            f"today. The system cannot start without it, so nothing was "
            f"restarted and the previous version is still running; the deploy refuses HERE, "
            f"loudly, rather than leaving a unit to fail silently at 03:15.")
    return seeded


DEPS_COMMANDS = {
    "none": None,
    "uv": ["/opt/homebrew/bin/uv", "sync", "--frozen"],
    "flox": ["/usr/local/bin/flox", "activate", "--", "true"],
    "npm": ["npm", "ci"],
    "pip": [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
}


def sync_deps(system: str, entry: dict, contract: dict, log, dry_run: bool, *,
              workdir: Optional[Path] = None) -> None:
    """Materialize the runtime environment. Absolute binary paths for `uv` and `flox`
    because this can run from launchd, which sources no shell profile — the lesson
    WI-0187 paid for twice in one session."""
    deps = contract.get("deps") or {"kind": "none"}
    kind = deps.get("kind", "none")
    if kind not in DEPS_COMMANDS:
        raise DeployError(f"{system}: unknown deps kind {kind!r}")
    base = DEPS_COMMANDS[kind]
    if base is None:
        return
    cmd = [*base, *deps.get("args", [])]
    log(f"deps: {' '.join(cmd)}")
    if dry_run:
        return
    r = run(cmd, cwd=workdir or work_dir(system, entry),
            timeout=deps.get("timeout_seconds", 600))
    if r.returncode != 0:
        raise DeployError(f"{system}: dependency sync failed — "
                          f"{evidence.clip((r.stderr or r.stdout).strip(), 800, keep='head')}")


def checkout(system: str, entry: dict, tag: str, log, dry_run: bool) -> None:
    tree = deploy_tree(system)
    log(f"checkout: {tag} (detached)")
    if dry_run:
        return
    git_ok(["checkout", "--force", "--detach", tag], tree, f"{system} checkout {tag}")
    # A tag checkout leaves ignored files alone — that is the property state survival
    # rests on — but it can leave TRACKED files from the old tag behind if the new one
    # deleted them. `clean -fd` with -e for nothing would also delete the seeded state,
    # so the force-checkout above is deliberately as far as this goes.


def run_gate(system: str, entry: dict, spec: Optional[dict], label: str,
             log, dry_run: bool, *, workdir: Optional[Path] = None) -> dict:
    """Run a smoke/verify gate. Returns a result record for the ledger.

    A gate that is not declared returns `declared: False` — recorded as such, never as a
    pass. A system with no smoke check has not passed its smoke check; it has no smoke
    check, and the ledger says the difference out loud.

    `workdir` is where the command runs, defaulting to the production work directory. It is
    a parameter rather than something this function derives because the canary runs the
    very same contract command against a different checkout, and a gate that could only
    ever address production would have to be re-implemented to say anything about a
    candidate — a second implementation of the one step whose verdict decides a release."""
    if not spec:
        log(f"{label}: not declared by the contract — recorded as unchecked, not as passed")
        return {"declared": False}
    cmd = spec["cmd"]
    log(f"{label}: {' '.join(cmd)}")
    if dry_run:
        return {"declared": True, "dry_run": True}
    try:
        r = run(cmd, cwd=workdir or work_dir(system, entry),
                timeout=spec.get("timeout_seconds", 120))
    except subprocess.TimeoutExpired:
        return {"declared": True, "ok": False, "exit": None,
                "detail": f"timed out after {spec.get('timeout_seconds', 120)}s"}
    except OSError as e:
        # A COMMAND THAT CANNOT START IS A FAILED GATE, NOT A DEAD SWEEP. `subprocess`
        # raises rather than returning a code when argv[0] does not exist, and `sweep()`
        # catches `DeployError` and `RollbackFailed` — so the bare `FileNotFoundError`
        # this used to be escaped the per-system loop and took every LATER system with
        # it, self row included. Measured under WI-0395 by deleting the preflight and
        # watching three tests turn from a refusal into a traceback. Recorded as its own
        # detail rather than folded into "failed", so the ledger says the gate could not
        # RUN and not that it ran and said no ([`declare-what-a-check-assumes`]).
        return {"declared": True, "ok": False, "exit": None,
                "detail": f"could not execute {cmd[0]!r} — {e}. PATH was "
                          f"{os.environ.get('PATH', '')!r}"}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return {"declared": True, "ok": r.returncode == 0, "exit": r.returncode,
            "detail": evidence.clip(out, 1500, keep="tail")}


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def unit_target(label: str) -> Optional[str]:
    """The working directory the LOADED unit actually runs in, or None if it is not
    loaded. Read from `launchctl print` rather than from a plist file on disk, because
    what is loaded and what is on disk are different questions and only one of them is
    the process that is running."""
    r = run(["launchctl", "print", f"{launchctl_domain()}/{label}"], timeout=30)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if "working directory =" in line:
            return line.split("=", 1)[1].strip()
    return None


def cutover_state(system: str, entry: dict, contract: dict, log) -> dict:
    """Do this system's units actually point at the tree we just deployed into?

    Until a unit's `WorkingDirectory` names the deploy tree, the process that is running
    belongs to some OTHER directory — normally the developer's clone, which is where every
    one of these units pointed before ADR-0103. Restarting such a unit would restart code
    this deploy did not place, and then reporting the deploy as live would be false in the
    most confusing way available: the ledger would name a version that is checked out
    somewhere nothing executes.

    So the cutover is a FIRST-CLASS STATE rather than an assumption. A tree can be fully
    deployed and honestly not yet live, and the runner says exactly that instead of
    picking one of the two comfortable lies."""
    wd = str(work_dir(system, entry))
    # WI-0420: another machine's units are not "pending" here, they are NOT THIS MACHINE'S
    # BUSINESS. Classified rather than filtered, they land in `unloaded` (nothing on this
    # host has them loaded, truthfully) and the cutover installs them -- which is how the
    # Runner came to hold `com.federation.gate-inputs`, a devbox LaunchDaemon, firing at
    # 02:30 against the sealed tree.
    units, pending, unloaded = units_for_this_host(system, entry, contract, log), [], []
    for unit in units:
        target = unit_target(unit)
        if target is None:
            unloaded.append(unit)
        elif not (target == wd or target.startswith(wd + "/")):
            pending.append({"unit": unit, "runs_in": target})
    return {"expected": wd, "pending": pending, "unloaded": unloaded,
            "units": list(units),
            "cut_over": not pending and not unloaded and bool(units)}


CUTOVER_MODES = ("manual", "auto")


def cutover_mode(system: str, entry: dict) -> str:
    """`auto` = this runner performs the first cutover itself; `manual` = it stops at
    `awaiting-cutover` and leaves the unit repoint to a person.

    WHY THIS IS A REGISTRY KEY AND NOT A CONTRACT KEY. The contract is read FROM THE TAG
    (`read_contract`), which is what makes a release and the instructions for deploying
    it one object. That property is exactly what disqualifies the contract here: a real
    member is the system this setting exists for, its deployable tag is already cut, and
    a tag is immutable — so a contract-side flag could not be set for it without that member
    cutting a new release, which is another Architect's act and another conversation. The
    registry is read from the deploying machine's own tree, so this is the federation
    changing its own file about a system it deploys, effective on the next sweep. The
    2026-09-11 go brief said `contract`; WI-0350 said `registry` and is right, for this
    reason.

    DEFAULT `manual`, which is the opposite of that brief's default and matches WI-0350:
    nothing may change how it deploys merely by being upgraded past this commit. Opting a
    system in is a visible one-line edit to the registry, not a silent consequence."""
    for key in entry:
        if key != "cutover" and key.lower() == "cutover":
            raise DeployError(
                f"registry row {system!r} has key {key!r}. The cutover setting is spelled "
                f"'cutover', lowercase; a near-miss reads as the default and the system "
                f"would sit at awaiting-cutover forever with nothing saying why.")
    mode = entry.get("cutover", "manual")
    if mode not in CUTOVER_MODES:
        # The `self` flag's argument, one row over: a malformed value that quietly reads
        # as the default is a setting that is present, wrong, and invisible.
        raise DeployError(
            f"registry row {system!r} sets cutover to {mode!r}. It must be one of "
            f"{', '.join(CUTOVER_MODES)} — anything else reads as {CUTOVER_MODES[0]!r} "
            f"and the opt-in silently does nothing.")
    return mode


def pre_cutover_dir(system: str) -> Path:
    """Where the plist a cutover REPLACES is kept.

    BESIDE the deploy tree, never inside it. Every deploy runs `checkout --force
    --detach`, so a backup stored in the tree exists right up until the first moment
    anyone would want it."""
    return deploy_root() / f"{system}.pre-cutover"


#: What `plistlib.loads` raises on bytes it cannot read, and it is three things rather
#: than two. `InvalidFileException` covers a well-formed document in the wrong format and
#: `ValueError` a bad binary header — but MALFORMED XML surfaces as `expat.ExpatError`,
#: which subclasses neither: `plistlib.loads(b"<plist><dict>")` raises
#: `ExpatError: no element found`. The two-name tuple this replaces therefore did not
#: catch the commonest way a plist is broken, so a member tag shipping a template with an
#: unclosed tag would escape `render_unit_plist`'s own handler and, through it,
#: `finish_self_cutover` — whose docstring promises its caller that it never raises.
#: Found by the WI-0395 control that asserted the documented behaviour and got a crash.
PLIST_PARSE_ERRORS = (ValueError, plistlib.InvalidFileException, ExpatError)


#: How a unit template names the machine it belongs to: a directive line inside the
#: template's own comment block, matched against the raw text before rendering.
#:
#: NOT A PLIST KEY. The rendered file is what launchd loads, and a key launchd does not
#: know has no business in it -- `_unsubstituted_tokens` already parses those values and a
#: fifth token would have to be threaded through the substitution guard for nothing.
#:
#: NOT A REGISTRY KEY EITHER, and that is a decision this repo already made once:
#: `unattended_refusal` records that a `deploy_host` row in `deploy/registry.json` was
#: tried and rejected -- "Two keys that both say Runner today are two keys that can
#: disagree tomorrow." The claim already lives in the template header as prose;
#: `com.federation.gate-inputs.plist.template` has said "THIS UNIT IS DEVBOX'S, AND THAT
#: IS THE POINT" since it was written. A renderer that reads only structure renders it
#: anyway, because a comment is something a PERSON reads. This is that same sentence in a form the
#: renderer reads, in the same file, so there is still one place to change.
UNIT_HOST_DIRECTIVE = re.compile(r"^[^\S\n]*poga-host:[^\S\n]*(\S+)[^\S\n]*$", re.MULTILINE)

#: A unit that genuinely belongs on every machine SAYS SO rather than saying nothing.
#: `com.federation.mail-poller`'s header reads "INSTALL IT ON EVERY MACHINE THAT HOLDS
#: MEMBER REPOS", which is a real answer and not a missing one. Collapsing the two would
#: make "I forgot" indistinguishable from "everywhere" -- and "I forgot, so render it
#: here" is precisely the behaviour being removed.
UNIT_HOST_ANY = "any"


def unit_template(system: str, entry: dict, unit: str) -> Optional[Path]:
    """The `<label>.plist.template` in the deploy tree, or None when the tag ships none.

    None is not an error and must not become one: a real member ships a real
    `deploy/com.example-app.service.plist` and has no template, and another member may declare
    a unit with neither. By design only the federation ships unit templates, so a rule
    about templates reaches exactly the files this defect came from and no member is
    asked to edit anything."""
    src = unit_plist(system, entry, unit)
    template = src.with_suffix(".plist.template") if src.suffix == ".plist" else None
    return template if template is not None and template.exists() else None


def declared_unit_host(template: Path) -> str:
    """The machine label a unit template declares. Raises `DeployError` if it declares none.

    A REFUSAL RATHER THAN A DEFAULT, because the default is what did the damage. Before
    WI-0420 the cutover rendered whatever it found, so `com.federation.gate-inputs` -- a
    devbox LaunchDaemon -- could be installed as a LaunchAgent on the Runner and fire
    at 02:30 against the sealed deploy tree. Any default answers
    the question "whose unit is this?" without being told, which is the same mistake in a
    politer voice; the only safe unknown here is a stop.

    Two declarations are refused as hard as none. A template carrying both `runner` and
    `devbox` has no single answer, and picking the first would make the file's meaning
    depend on its line order."""
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeployError(f"cannot read {template.name} to find out whose unit it is: {exc}")
    found = UNIT_HOST_DIRECTIVE.findall(text)
    if not found:
        raise DeployError(
            f"{template.name} does not declare which machine it belongs to. Add a line "
            f"`poga-host: <machine>` to its header comment -- a machine label such as "
            f"`Runner` or `DevBox`, or `{UNIT_HOST_ANY}` for a unit that really does "
            f"belong on every machine. Rendering it here would be a guess, and the guess "
            f"is what installed a devbox daemon on the Runner (WI-0420). Nothing was "
            f"changed.")
    if len(found) > 1:
        raise DeployError(
            f"{template.name} declares {len(found)} hosts ({', '.join(sorted(set(found)))}); "
            f"a unit belongs to one machine or to `{UNIT_HOST_ANY}`. Nothing was changed.")
    declared = found[0]
    if declared.lower() == UNIT_HOST_ANY:
        return UNIT_HOST_ANY
    known = {label.lower(): label for label in common.machine_labels()}
    if not known:
        raise DeployError(
            f"{template.name} declares host {declared!r}, but this machine's config "
            f"produced no machine labels to check it against, so a typo cannot be told "
            f"from a real machine. Nothing was changed.")
    if declared.lower() not in known:
        raise DeployError(
            f"{template.name} declares host {declared!r}, which is not a machine this "
            f"config knows ({', '.join(sorted(known.values()))}). An unrecognised label is "
            f"a typo, not a machine that is merely elsewhere -- left to match nothing it "
            f"would skip the unit on EVERY host, silently and forever. Nothing was "
            f"changed.")
    return known[declared.lower()]


def units_for_this_host(system: str, entry: dict, contract: dict, log) -> list:
    """The declared units that belong on THIS machine, in contract order.

    ONE FILTER, TWO CALLERS, and that is the whole shape of it. `cutover_state` decides
    what to install and `restart_units` decides what to kick; before WI-0420 they each
    walked `contract["units"]` themselves and neither knew whose units they were. WI-0417
    was the same lesson one layer down -- two call sites is how one of them acquires a
    working route and the other does not -- so the answer lives in one function both ask.

    A unit with NO template passes through untouched. Nothing renders it, so this rule has
    nothing to say about it, and saying something anyway would fail those same members
    for a defect that is structurally not theirs."""
    units = contract.get("units", [])
    if not units:
        return []
    here = common.this_machine()
    mine, foreign = [], []
    for unit in units:
        template = unit_template(system, entry, unit)
        if template is None:
            mine.append(unit)
            continue
        host = declared_unit_host(template)
        if host == UNIT_HOST_ANY:
            mine.append(unit)
            continue
        # ASKED ONLY ONCE THERE IS A HOST TO COMPARE AGAINST. A machine that cannot name
        # itself must not install another machine's units on a shrug: `unattended_refusal`
        # already rules that "not knowing where you are standing is a reason to withhold
        # an unattended deploy, never to grant one", and installing is the granting half.
        if not here:
            raise DeployError(
                f"{unit} belongs to {host!r}, but this machine cannot name itself "
                f"(`scutil --get ComputerName` gave nothing the machine map knows), so it "
                f"cannot tell whether that is here. Withholding rather than guessing; the "
                f"next sweep retries. Nothing was changed.")
        if host == here:
            mine.append(unit)
        else:
            foreign.append({"unit": unit, "host": host})
    if foreign:
        log(f"units: skipping {len(foreign)} unit(s) that belong to another machine — "
            + ", ".join(f"{u['unit']} ({u['host']})" for u in foreign)
            + f"; this machine is {here!r}")
    return mine


def unit_plist(system: str, entry: dict, unit: str) -> Path:
    """The plist for a unit, inside the deploy tree, named by its LABEL.

    Beside the contract that declares the unit — `deploy/<label>.plist` next to
    `deploy/deploy.json` — because that is where the one system this has to work for
    already puts it: a hypothetical member `example-app`'s `v1.0.0` tag carries
    `deploy/com.example-app.service.plist`, declaring Label `com.example-app.service` and
    WorkingDirectory `~/deploy/example-app` (verified by reading the tag). Deriving the
    directory from `CONTRACT_RELPATH` rather than spelling "deploy" again keeps the two
    from drifting.

    A CONVENTION rather than a declared path key, for `cutover_mode`'s reason: a new
    contract key cannot be added to a tag that is already cut, and this member's is."""
    return work_dir(system, entry) / Path(CONTRACT_RELPATH).parent / f"{unit}.plist"


def render_unit_plist(system: str, entry: dict, unit: str, log) -> Optional[Path]:
    """Render `<label>.plist.template` from the deploy tree, the way install-*.sh does.

    WHY THE TAG DOES NOT SHIP A RENDERED PLIST, and must not start. The template says it
    itself: the concrete file "carries machine-specific absolute paths, P3" and is never
    committed. `__FED_ROOT__` is wherever this machine keeps the deploy tree and
    `__PYTHON__` is whichever interpreter it resolves — committing either into a tracked
    file is WI-0132's defect wearing a new hat, and it would be wrong on every other
    machine that ever deployed the same tag.

    So the rendering moves to the cutover, which is the moment the machine-shaped values
    are known. A tag that carries only templates has nothing an install-time step can
    install, so without rendering here every unit would be REFUSED, and a deferred
    finisher would refuse for the same reason and could not say so.

    `__FED_ROOT__` is the DEPLOY TREE, never the checkout the runner is standing in —
    pointing a cut-over unit back at the developer clone is the exact thing cutover exists
    to end, and `_cutover_plist_is_usable` re-checks WorkingDirectory afterwards rather
    than trusting this.

    Returns the rendered path, or None when there is no template to render (which is not
    an error: a real member ships a real plist and needs none)."""
    template = unit_template(system, entry, unit)
    if template is None:
        return None
    # WI-0420, AND IT REFUSES RATHER THAN RAISES. `unit_plist_source` promises
    # `finish_self_cutover` it never raises, and this is the shape every other refusal in
    # this function already has: log what is wrong, return None, let the next sweep retry.
    # `units_for_this_host` raises instead, because a hostless template there is a broken
    # TAG and a deploy that half-installs one is worse than a deploy that stops. Both
    # doors, because the detached finisher reaches this one without passing the other.
    try:
        host = declared_unit_host(template)
    except DeployError as exc:
        log(f"cutover: {exc} — REFUSING to render {unit}.")
        return None
    here = common.this_machine()
    if host != UNIT_HOST_ANY and host != here:
        log(f"cutover: {template.name} declares host {host!r} and this machine is "
            f"{here or 'unnameable'!r} — REFUSING to render {unit}. Nothing was changed.")
        return None
    fed_root = str(work_dir(system, entry))
    python = shutil.which("python3") or "/usr/bin/python3"
    claude = shutil.which("claude")
    try:
        # RESOLVED INSIDE THE TRY. `production.resolve` RAISES on a selector that is set
        # but unusable — a moved config file, a vanished state root — it does not return
        # None. Outside the try that exception escaped into `perform_cutover` and, through
        # it, into `finish_self_cutover`, whose docstring promises it never raises.
        roots = production.resolve(REPO_ROOT)
        text = template.read_text(encoding="utf-8")
        text = text.replace("__FED_ROOT__", fed_root).replace("__PYTHON__", python)
        # SUBSTITUTED ONLY WHEN THERE IS SOMETHING TO SUBSTITUTE. Filling `__CLAUDE_BIN__`
        # with an empty string when `claude` is not on the PATH produces a plist whose
        # ProgramArguments begin with "" — which passes `plutil`, installs, and fails to
        # spawn, the same silent outcome as leaving the token in but without the token to
        # name it. Left alone, the placeholder guard below catches it and says which one.
        if claude:
            text = (text.replace("__CLAUDE_BIN__", claude)
                        .replace("__CLAUDE_DIR__", str(Path(claude).parent)))
        # A PLACEHOLDER THAT SURVIVED IS NOT A PLIST. `plutil -lint` accepts
        # `__CLAUDE_BIN__` as an ordinary string, so an unsubstituted token passes every
        # check `_cutover_plist_is_usable` makes and installs a job launchd cannot spawn —
        # a unit that reports as loaded and never runs. Refusing here costs one sweep and
        # names the token; the next sweep retries once `claude` is on the PATH.
        #
        # ASKED OF THE PARSED VALUES, never of the raw text. All three templates DOCUMENT
        # themselves as carrying "__PLACEHOLDER__ tokens" in an XML comment, so a regex
        # over the source would refuse every correct render. A guard that fires on correct code is one somebody deletes.
        missing = sorted(set(_unsubstituted_tokens(text)))
        if missing:
            log(f"cutover: {template.name} still holds {', '.join(missing)} after "
                f"rendering — REFUSING to install a plist with an unsubstituted "
                f"placeholder. Nothing was changed.")
            return None
        # AFTER the placeholder guard, so a template whose PATH still carries
        # `__CLAUDE_DIR__` is refused by name rather than quietly widened. Before the
        # production binding, so both the bound and the unbound render carry it.
        text = _prepend_operator_bin(text)
        if roots:
            text = _bind_unit_to_external_state(text, roots, unit)
            out = roots.state_path("runtime", "units", f"{unit}.plist")
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            out = template.with_name(f"{unit}.plist")
        out.write_text(text, encoding="utf-8")
    except (production.ConfigurationError, OSError, *PLIST_PARSE_ERRORS) as e:
        log(f"cutover: could not render {template.name} — {e}")
        return None
    log(f"cutover: rendered {template.name} with FED_ROOT={fed_root} PYTHON={python} "
        f"PATH={_declared_path(text) or '(none declared)'}"
        + (f" bound to external state at {roots.state_root}" if roots else ""))
    return out


def _unsubstituted_tokens(text: str) -> list[str]:
    """`__TOKEN__` spellings surviving in a rendered plist's VALUES, not in its prose.

    The distinction is the whole of it. An XML comment explaining that this file carries
    placeholders is documentation; a `__CLAUDE_BIN__` sitting in `ProgramArguments` is a
    job that cannot start. Parsing and walking the values asks about the second and is
    blind to the first, which is the only way this guard can be strict without being
    wrong. An unparseable render answers with no tokens and leaves the refusal to
    `plutil`, whose job that is."""
    try:
        document = plistlib.loads(text.encode("utf-8"))
    except PLIST_PARSE_ERRORS:
        return []
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.extend(re.findall(r"__[A-Z][A-Z0-9_]*__", value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(document)
    return found


#: Where an operator installs the verbs a member's contract resolves on PATH. Held
#: RELATIVE TO HOME and joined on the rendering machine: `/Users/you/.local/bin`
#: written into a tracked file is WI-0132's defect, which is the whole reason the contract
#: that needs this directory refuses to name it either.
OPERATOR_BIN_RELATIVE = ".local/bin"


def _declared_path(text: str) -> str:
    """The PATH a rendered unit declares, for the receipt — "" when it declares none.

    Read back off the rendered text rather than assembled from what the rewrite below
    meant to write, so the log line is evidence about the plist that is about to be
    installed ([`capture-the-probe`])."""
    try:
        document = plistlib.loads(text.encode("utf-8"))
    except PLIST_PARSE_ERRORS:
        return ""
    return (document.get("EnvironmentVariables") or {}).get("PATH", "")


def _prepend_operator_bin(text: str) -> str:
    """Put the rendering machine's `~/.local/bin` at the FRONT of a rendered unit's PATH.

    THE FAILURE THIS FIXES. A member's tag can pass
    its smoke gate and then refuse at apply: a verb "is not on PATH on this machine".
    The verb is installed in the operator's local bin; the sweep runs under
    `com.federation.deploy-sweep`, whose PATH is a bare system PATH with no local bin
    on it. A member's contract resolves that verb on PATH BY DESIGN (ADR-0103 D8
    shape 2) and is right to — a machine path in a tracked contract is WI-0132's defect.
    The contract is right, the verb is there, and the unit between them is the gap.
    Without this, the apply has to be run by hand, which is the errand this closes.

    RENDERED RATHER THAN WRITTEN INTO THE TEMPLATE, for the reason `__FED_ROOT__` is a
    token and not a path: the three templates are tracked, `~` is a different directory on
    every machine, and a committed `/Users/...` would be wrong everywhere else that
    deployed the same tag. Because the value is computed here, every later cutover
    reproduces it and no regen can lose it.

    AND NOT A `__PATH__` PLACEHOLDER, which was the other shape on offer. The same three
    templates are also rendered by `deploy/install-*.sh`, whose `sed` fills a FIXED list of
    tokens — `__PYTHON__`, `__FED_ROOT__`, and for the adopt-runner the two `__CLAUDE_*__`
    — with no `_unsubstituted_tokens` guard behind it. A new token would therefore install,
    from that path, a job whose PATH is the literal string `__PATH__`: strictly worse than
    the hard-coded list it replaced, and silent. Rewriting the parsed value leaves those
    installers rendering exactly what they render today.

    ONLY WHERE A PATH IS ALREADY DECLARED. A unit that declares none inherits launchd's
    own minimal PATH; "prepending" to that would hand it a PATH of one directory and take
    away `/usr/bin`, which is a larger break than the one being fixed.

    Idempotent, and it keeps the declared order: the operator's directory goes first
    because it is the most specific, and every entry the template named follows unchanged.
    """
    try:
        document = plistlib.loads(text.encode("utf-8"))
    except PLIST_PARSE_ERRORS:
        # UNPARSEABLE IS NOT THIS FUNCTION'S REFUSAL TO MAKE. `_unsubstituted_tokens`
        # already leaves that verdict to `plutil`, which answers it with a line number;
        # raising here would convert a linted refusal into "could not render".
        return text
    environment = dict(document.get("EnvironmentVariables") or {})
    declared = environment.get("PATH")
    if not declared:
        return text
    operator_bin = str(Path.home() / OPERATOR_BIN_RELATIVE)
    entries = [p for p in declared.split(":") if p]
    if operator_bin in entries:
        return text
    environment["PATH"] = ":".join([operator_bin, *entries])
    document["EnvironmentVariables"] = environment
    return plistlib.dumps(document).decode("utf-8")


def _bind_unit_to_external_state(text: str, roots, unit: str) -> str:
    """Point one rendered unit at production state instead of at the release tree.

    THE TEMPLATES WRITE INTO `__FED_ROOT__/.session-state/`, which after cutover is the
    SEALED DEPLOY TREE — so every log line, and every `.pyc` the interpreter caches, lands
    inside a checkout the next tag overwrites and a detached commit strands.
    `deploy/production-state.md` files this as migration's obligation by name: *"Launchd
    stdout/stderr and Python bytecode cache: scheduler must bind these to external runtime
    state."* This is that binding, and it is the whole of what separates a unit running
    released code from a unit running released code on production state.

    Inert off production — the caller only reaches this with resolved roots, and nothing
    resolves roots until a service configuration exists."""
    document = plistlib.loads(text.encode("utf-8"))
    logs = roots.state_path("runtime", "logs")
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    document["StandardOutPath"] = str(logs / f"{unit}.out.log")
    document["StandardErrorPath"] = str(logs / f"{unit}.err.log")
    environment = dict(document.get("EnvironmentVariables") or {})
    # The selector itself. Without it the job runs released code with production dormant,
    # which is exactly the state this migration exists to leave.
    environment[production.CONFIG_ENV] = str(
        Path(os.environ[production.CONFIG_ENV]).resolve())
    # Bytecode belongs outside the sealed tree for the same reason the logs do. Both keys
    # are set: the prefix relocates what CPython writes, and the flag covers an
    # interpreter too old to honour the prefix.
    environment["PYTHONPYCACHEPREFIX"] = str(roots.state_path("runtime", "pycache"))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    document["EnvironmentVariables"] = environment
    return plistlib.dumps(document).decode("utf-8")


def unit_plist_source(system: str, entry: dict, unit: str, log) -> Optional[Path]:
    """The plist to install for `unit` — ONE answer for both callers, or None.

    `perform_cutover` and `finish_self_cutover` each resolved this themselves, with the
    same two lines in the same order: the tag's own `deploy/<label>.plist` if it ships
    one, else a render of its template. That was correct while a render was a pure
    function of the tag, and stopped being correct the moment a render could be bound to
    production state.

    UNDER PRODUCTION A REFUSED RENDER RETURNS None, AND MUST NOT FALL BACK. The obvious
    `render(...) or src` is the bug that shape invites: `render_unit_plist` returns None
    for a REFUSAL as well as for "no template", and `src` is the in-tree file — which
    under production is the stale artifact of an earlier, UNBOUND render. v7.3.0's
    renderer wrote exactly such a file into the deploy tree and never substituted
    `__CLAUDE_BIN__`, and `checkout --force --detach` does not remove untracked files, so
    it is still there. Falling back would install the very plist the placeholder guard
    just refused: a unit carrying no `POGA_FEDERATION_CONFIG`, logging into the sealed
    tree, with an unspawnable argv — recorded as a success. Off production nothing
    changes.

    A production selector that is set but UNUSABLE is also None rather than an exception:
    the honest answer to "which plist should I install?" when the configuration cannot be
    read is "none", and `finish_self_cutover` promises its caller it never raises."""
    try:
        roots = production.resolve(REPO_ROOT)
    except production.ConfigurationError as exc:
        log(f"cutover: the production configuration is unusable — {exc}. Installing "
            f"nothing for {unit}.")
        return None
    if roots:
        return render_unit_plist(system, entry, unit, log)
    src = unit_plist(system, entry, unit)
    if not src.exists():
        return render_unit_plist(system, entry, unit, log) or src
    return src


def unit_plist_relpath(entry: dict, unit: str) -> str:
    """`unit_plist`'s path as the TAG spells it — repo-relative, subdir included."""
    rel = Path(CONTRACT_RELPATH).parent / f"{unit}.plist"
    sub = entry.get("subdir")
    return f"{sub}/{rel}" if sub else str(rel)


#: How long the detached self-cutover finisher waits for the sweep to exit before giving
#: up. Generous: the sweep it is waiting on may still be deploying other systems, and the
#: cost of waiting is a delayed cutover the next sweep retries, while the cost of giving up
#: early is booting out a running sweep — the exact hazard the deferral exists to avoid.
SELF_CUTOVER_WAIT_SECONDS = 1800


def self_cutover_would_regress(system: str, entry: dict, tag: str) -> str:
    """Why the self system must NOT cut over to `tag`, or "" when it may.

    THE FAILURE THIS PREVENTS. When the newest federation tag on origin predates the
    code the units already run, and the registry row is set to `cutover: auto`, a sweep
    would deploy that older tag into ~/deploy/federation and REPOINT THE UNITS AT IT:
    production moved backwards, by automation, to code older than the checkout it was
    already running.

    A rollback is a deliberate act with a ledger entry behind it. This would have been a
    silent regression wearing a deploy's clothes, and `cutover_state` would have reported
    the system live and correct afterwards — the unit's working directory really would be
    in the deploy tree. Nothing downstream could tell the difference.

    THE TEST IS ANCESTRY, NOT DATES. Tag timestamps can be rewritten, clocks disagree
    between machines, and "newer tag" is not the question — the question is whether the
    commit the tag names already exists in the history the runner is executing. If the
    tagged commit is an ancestor of the running checkout's HEAD, the tag carries strictly
    less than what is running, and cutting over to it loses work.

    SELF ONLY. Every other system's deploy tree is unrelated to the code this process is
    made of, so "older than the runner's checkout" is meaningless for them — another
    member deploying an old tag is an ordinary rollback and the operator's business.

    UNREADABLE ANCESTRY IS A REFUSAL. If the comparison cannot be made, we do not know
    whether this moves production backwards, and the direction that fails safe is to leave
    the units where they are: the cost is a deferred cutover the next sweep retries, and
    the cost of guessing is a silent regression of the runner itself."""
    if entry.get("self") is not True:
        return ""

    # THE SUBJECT IS THE DEPLOYED TAG, NOT THE TRUNK — and the first version of this guard
    # got that wrong in a way that made it unpassable. It compared the candidate against
    # the running CHECKOUT's HEAD. A trunk checkout is by construction ahead of the newest
    # tag (the tag is cut from a commit, and the trunk keeps moving), so the first cutover
    # could never pass: a refresh that fast-forwards the process checkout between the
    # sync and the sweep makes a correct tag read as "an ancestor". A guard that
    # fires on correct code is one somebody eventually deletes, taking the real protection
    # with it.
    #
    # What the guard is actually for is a system ALREADY LIVE on a tag being moved to an
    # older one. So the comparison is candidate-vs-deployed, and the bootstrap case —
    # units still running from a developer checkout, no deployed tag to regress from —
    # is not a regression at all and proceeds.
    live = read_ledger(system).get("current")
    if not live:
        return ""              # never cut over: there is no deployed tag to move backwards from
    if live == tag:
        return ""

    tree = deploy_tree(system)
    cand = git(["rev-list", "-n", "1", tag], tree, timeout=60)
    cur = git(["rev-list", "-n", "1", live], tree, timeout=60)
    if cand.returncode != 0 or cur.returncode != 0:
        return (f"cannot compare {tag} with the live tag {live} — refusing rather than "
                f"risking a backwards cutover of the runner itself")
    tc, lc = cand.stdout.strip(), cur.stdout.strip()
    if not tc or not lc:
        return (f"cannot resolve {tag} or the live tag {live} — refusing rather than "
                f"risking a backwards cutover of the runner itself")
    if tc == lc:
        return ""
    anc = git(["merge-base", "--is-ancestor", tc, lc], tree, timeout=60)
    if anc.returncode == 0:
        return (f"{tag} ({tc[:8]}) is an ANCESTOR of the live tag {live} ({lc[:8]}), so "
                f"cutting over would move production BACKWARDS — a silent regression that "
                f"`cutover_state` would afterwards report as live and correct. Deploy a "
                f"tag at or after {live} instead")
    return ""


def _cutover_plist_is_usable(src: Path, unit: str, expected: str) -> tuple[bool, str]:
    """The four checks a plist must pass before it is installed. Shared, not duplicated.

    `perform_cutover` and `finish_self_cutover` both install units; two copies of these
    checks would drift, and the one that drifted would be the rarely-run self path — so
    the self system, the one whose mistake restarts the runner itself, would be guarded by
    the stalest copy. Each check REFUSES rather than guessing, because a cutover that
    installs the wrong plist reports a system live while something else runs."""
    if not src.exists():
        return False, (f"the deployed tag carries no {src.name}; a system opting into "
                       f"automatic cutover must ship the plist it wants bootstrapped")
    try:
        declared = plistlib.loads(src.read_bytes())
    except Exception as e:                           # noqa: BLE001 — any parse failure
        return False, f"{src} is not a readable plist — {e}"
    if declared.get("Label") != unit:
        return False, (f"{src} declares Label {declared.get('Label')!r}, not {unit!r}; "
                       f"bootstrapping it would load a different job and restart nothing")
    wd = declared.get("WorkingDirectory")
    if not (wd == expected or (wd or "").startswith(expected + "/")):
        return False, (f"{src} sets WorkingDirectory to {wd!r}, which is outside the "
                       f"deploy tree {expected}; installing it would leave production "
                       f"running code this deploy did not place")
    lint = run(["plutil", "-lint", str(src)], timeout=30)
    if lint.returncode != 0:
        return False, f"plutil rejected {src} — {(lint.stderr or lint.stdout).strip()}"
    return True, ""


def _cutover_install(unit: str, src: Path, log) -> int:
    """bootout, copy, bootstrap, enable — the install half, shared for the same reason."""
    domain = launchctl_domain()
    installed = launch_agents_dir() / f"{unit}.plist"
    run(["launchctl", "bootout", f"{domain}/{unit}"], timeout=60)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, installed)
    boot = run(["launchctl", "bootstrap", domain, str(installed)], timeout=120)
    if boot.returncode != 0:
        log(f"cutover: bootstrap FAILED for {unit} — "
            f"{((boot.stderr or boot.stdout) or '').strip()}. The unit is NOT loaded.")
        return 1
    run(["launchctl", "enable", f"{domain}/{unit}"], timeout=60)
    return 0


def running_under_unit() -> str:
    """The launchd job label this process is running under, or "" when it is not one.

    launchd sets `XPC_SERVICE_NAME` to the job's label for every process it starts, and
    leaves it unset (or a placeholder) for a shell. That makes it the honest witness for
    the one question the self-cutover has to answer: which unit, if any, am I?

    RETURNS "" WHEN IT CANNOT TELL, and the caller treats that as "not one of ours" — the
    direction that cuts over rather than deferring. A wrong "" costs a killed sweep that
    the next fire redoes; a wrong label would silently never cut that unit over at all,
    leaving production on the old tree forever with the ledger claiming success."""
    name = (os.environ.get("XPC_SERVICE_NAME") or "").strip()
    return "" if (not name or name == "0" or name.startswith("com.apple.")) else name


def _spawn_self_cutover_finisher(system: str, unit: str, pid: int) -> bool:
    """Start the detached finisher. Returns whether it was started, never raises.

    Detached for the same reason `_dispatch_close_runtime`'s reaper is: the thing it waits
    on is our own process, so it has to outlive us. A failure to spawn is reported rather
    than swallowed — the caller logs it, because "deferred" and "deferred to nothing" look
    identical in a ledger otherwise, and the second one leaves production on the old tree
    while the record says the cutover was handled."""
    # ITS OUTPUT WENT TO DEVNULL, so the finisher could not report anything: the sweep
    # could exit, the units stay on the old checkout, and no log line, file or process
    # would say whether it had run, refused, or never spawned. Three outcomes rendering identically as silence is the false-green
    # shape, and it was in the tail nobody watches. It gets its own log now.
    log = self_cutover_log(system)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log, "a", encoding="utf-8")
    except Exception:                                # noqa: BLE001
        fh = None
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--finish-self-cutover", system, "--unit", unit, "--wait-pid", str(pid)],
            stdout=(fh or subprocess.DEVNULL), stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:                                # noqa: BLE001 — detached tail
        if fh:
            with contextlib.suppress(Exception):
                fh.write(f"{_now()}  spawn FAILED for {unit}\n")
        return False
    finally:
        if fh:
            with contextlib.suppress(Exception):
                fh.close()


def self_cutover_log(system: str) -> Path:
    """Where the detached finisher writes. Machine-local and gitignored, beside the other
    session state — it is a receipt about THIS machine's units, not repo content."""
    return production.state_path(REPO_ROOT, f"self-cutover-{system}.log",
                                 REPO_ROOT / ".session-state" / f"self-cutover-{system}.log")


def report_self_cutover_log(system: str, log) -> None:
    """Say what the finisher did, in the NEXT sweep's own output.

    A file nobody reads is the same as no file. The finisher runs after the sweep exits,
    so its result can only ever reach a human through the sweep that follows — which makes
    surfacing it here the difference between a receipt and a diary."""
    p = self_cutover_log(system)
    try:
        lines = [ln.rstrip() for ln in p.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
    except FileNotFoundError:
        return                       # never deferred on this machine; nothing to say
    except Exception as e:           # noqa: BLE001
        log(f"self-cutover: cannot read {p} — {e}")
        return
    if not lines:
        log(f"self-cutover: {p} exists but is EMPTY — the finisher was spawned and wrote "
            f"nothing. Treat that as 'did not run', not 'nothing to report'.")
        return
    # The one site in this program that can take acceptance route (a) rather than (b):
    # the whole text is still on disk at `p`, so the cut names the file instead of just
    # confessing to a number. "A file nobody reads is the same as no file" cuts both
    # ways — a three-line window nobody is told is a window is the same as a lie.
    for ln in evidence.clip("\n".join(lines), 3, keep="tail", unit="lines",
                            full_text_at=str(p)).splitlines():
        log(f"self-cutover (previous): {ln}")


def finish_self_cutover(system: str, unit: str, wait_pid: int, log=print) -> int:
    """Cut over the unit THIS SWEEP IS RUNNING UNDER, after the sweep has exited.

    `bootout` on our own job terminates the process issuing it, mid-deploy and before any
    ledger write — the same hazard `refuse_self_restart` names for `kickstart`, arriving
    by a different verb. But deferring alone is not a fix: a loaded job keeps the
    WorkingDirectory it was bootstrapped with, so a unit that is never booted out never
    moves to the deploy tree, however many times the tag advances. "Defer" without a
    finisher means "never".

    So this runs DETACHED, waits for the sweep's pid to leave the process table, and then
    performs the same checked bootout/bootstrap/enable `perform_cutover` performs for
    every other unit. The wait is on the pid rather than a fixed sleep because the thing
    that must be true is *the sweep has exited*, and a sleep asserts that by hope.

    Never raises: this is a detached tail with nobody to report to. What it has instead of
    an exception is the ledger note and its exit code."""
    def log(msg: str) -> None:       # noqa: F811 — shadows the parameter deliberately
        print(f"{_now()}  {msg}", flush=True)

    log(f"finisher started for {unit}, waiting on pid {wait_pid}")
    deadline = time.time() + SELF_CUTOVER_WAIT_SECONDS
    while time.time() < deadline:
        try:
            os.kill(wait_pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            break                      # exists but not ours to signal — good enough
        time.sleep(1.0)
    else:
        log(f"self-cutover: pid {wait_pid} still alive after "
            f"{SELF_CUTOVER_WAIT_SECONDS}s — NOT cutting over {unit}; the next sweep "
            f"retries. Nothing was booted out.")
        return 1

    entry = registry_entry(system)
    expected = str(work_dir(system, entry))
    src = unit_plist_source(system, entry, unit, log)
    if src is None:
        # A refusal, not an absence: the render declined (an unsubstituted placeholder, an
        # unusable production configuration) and there is no safe file to fall back to.
        log(f"self-cutover: REFUSED for {unit} — no installable plist could be produced; "
            f"nothing was booted out.")
        return 1
    ok, why = _cutover_plist_is_usable(src, unit, expected)
    if not ok:
        log(f"self-cutover: REFUSED for {unit} — {why}")
        return 1
    rc = _cutover_install(unit, src, log)
    log(f"self-cutover: {unit} {'cut over to' if rc == 0 else 'FAILED to reach'} "
        f"{expected}")
    return rc


def repoint_cli_link(system: str, entry: dict, log, dry_run: bool) -> bool:
    """Point `~/.local/bin/poga` at the deploy tree. True if it was moved.

    THE THING THAT BREAKS WHEN THE PROCESS ROOT IS RETIRED, and it is not a unit, so nothing
    in the cutover was looking at it. By design `~/.local/bin/poga` is a symlink to
    `<process clone>/poga` on every machine, and Runner-RESIDENT Architects open their
    sessions through that command.
    Retire the process root under R3 with the link still pointing into it and every one of
    those sessions breaks, with nothing in this program having mentioned it.

    It is also a gain rather than a repair: once the link resolves into the deploy tree, the
    CLI on the Runner updates through the TAG like everything else, which it never did. It
    was whatever the clone happened to hold.

    ONLY A SYMLINK, and only one pointing OUTSIDE the deploy tree. A real file there is
    somebody's own install and not ours to replace ([P9](../principles/master.md#p9--destructive-ops-confirmed)
    — this is the one irreversible-shaped act in the cutover, so it refuses on anything it
    did not expect). A link already inside the tree is the steady state and is left alone,
    which is what makes this safe to run on every cutover rather than once."""
    link, target = cli_link(), work_dir(system, entry) / "poga"
    if not target.is_file():
        log(f"cli: {target} is not present in the deploy tree — leaving {link} alone")
        return False
    if not link.is_symlink():
        if link.exists():
            log(f"cli: {link} is a real file, not a symlink — REFUSING to replace it. "
                f"Point it at {target} by hand if it should follow the deploy tree.")
        return False
    try:
        current = link.resolve()
    except OSError:
        current = None
    tree = work_dir(system, entry).resolve()
    if current is not None and (current == tree or str(current).startswith(str(tree) + "/")):
        return False                       # already ours; the steady state
    log(f"cli: repointing {link} → {target} (was {current})")
    if dry_run:
        return True
    try:
        tmp = link.with_name(link.name + ".repoint")
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(target)
        os.replace(tmp, link)              # atomic: the command is never briefly absent
    except OSError as e:
        # CLEAN THE TEMP UP HERE, not on the next cutover. The rename is what makes this
        # atomic, and a failure before it leaves a `poga.repoint` symlink sitting in the
        # directory that is on everybody's PATH. The next call does clear it, so this
        # self-heals — but "self-heals at the next deploy" is a week on a machine that
        # deploys when a tag says so, and the log line said the repoint failed, not that it
        # left a file behind. A failure must leave no trace, or the next reader has to guess
        # what the trace meant.
        try:
            if tmp.exists() or tmp.is_symlink():
                tmp.unlink()
        except OSError:
            pass
        log(f"cli: could not repoint {link} — {e}. The command still runs the old tree.")
        return False
    return True


def perform_cutover(system: str, entry: dict, contract: dict, cut: dict, tag: str,
                    log, dry_run: bool) -> dict:
    """Make the units point at the deploy tree, so a green smoke does not need a person.

    This is the last manual link in the deploy loop (WI-0350). `restart_units` cannot do
    it: `launchctl kickstart` restarts a unit that is ALREADY LOADED and pointing
    somewhere, and the case that actually blocks a real member is a unit that is not loaded
    at all — kickstart has nothing to kick. The verbs are `bootout` / `bootstrap` /
    `enable`, the same triple `deploy/install-*.sh` has always used by hand.

    The plist is taken from the deploy tree, not synthesized and not edited: the tree is
    the tag, so the plist that gets bootstrapped is the one that release shipped, and it
    is checked before installation rather than trusted. Every check below REFUSES rather
    than guessing, because a cutover that installs the wrong plist reports a system live
    while something else is running — the precise lie `cutover_state` was written to make
    impossible:

      1. the file exists in the tree at all, and parses as a plist;
      2. its Label is the unit we were asked to cut over. A file found at the right PATH
         is not evidence that it defines the right JOB — label and filename differ in
         this fleet (one member's `com.<member>.plist` holds label `com.<member>.<job>`), and
         bootstrapping the wrong label restarts nothing while reporting success;
      3. its WorkingDirectory is inside the deploy tree. A plist that still names the
         developer's clone would satisfy launchd and leave production running unreleased
         code, which is the whole failure this state exists to prevent;
      4. `plutil` accepts it, the same lint `deploy/install-*.sh` runs before bootstrap.

    And none of that is taken as proof: the caller re-asks `cutover_state` afterwards,
    because what makes a system live is the unit reporting a working directory in the
    deploy tree, not these commands exiting 0.

    A refusal is not an error: the caller falls back to `awaiting-cutover` with the reason
    recorded, which is exactly where the system would have sat anyway."""
    expected = str(work_dir(system, entry))
    domain = launchctl_domain()
    acted, refused, deferred = [], [], []
    # The units are not the only thing on this machine pointing at the retiring checkout.
    # Done for the self row only: `poga` is this system's own command, and repointing it for
    # somebody else's deploy would be this program editing a tool it does not own.
    if entry.get("self") is True:
        repoint_cli_link(system, entry, log, dry_run)
    # THE ONE UNIT THIS PROCESS CANNOT BOOT OUT (WI-0359). `bootout` on our own job
    # terminates the process issuing it — mid-deploy, before any ledger write — which is
    # `refuse_self_restart`'s hazard arriving by a different verb. Only the self system can
    # be in this position, and only for the single unit we are running under.
    own_unit = running_under_unit() if entry.get("self") is True else ""

    for unit in [p["unit"] for p in cut["pending"]] + list(cut["unloaded"]):
        if unit == own_unit and not dry_run:
            # Deferring alone would mean NEVER: a loaded job keeps the WorkingDirectory it
            # was bootstrapped with, so a unit never booted out never reaches the deploy
            # tree however often the tag advances. The detached finisher is what makes
            # this a deferral rather than a silent omission.
            pid = os.getpid()
            spawned = _spawn_self_cutover_finisher(system, unit, pid)
            log(f"cutover: {unit} is this process's own unit — deferred to a detached "
                f"finisher that waits for pid {pid} to exit"
                + ("" if spawned else " — WHICH COULD NOT BE SPAWNED; this unit stays on "
                                     "the old tree and the next sweep retries"))
            deferred.append({"unit": unit, "finisher_spawned": spawned, "pid": pid})
            continue
        # The tag may ship a TEMPLATE rather than a rendered plist — the federation's own
        # units do, and must, because a rendered one carries machine paths (P3).
        src = unit_plist_source(system, entry, unit, log)

        def refuse(why: str) -> None:
            log(f"cutover: REFUSED for {unit} — {why}")
            refused.append({"unit": unit, "reason": why})

        rel = unit_plist_relpath(entry, unit)

        if dry_run:
            # A dry run never checked the tree out, so `src` is absent for a reason that
            # has nothing to do with the tag — and reporting that absence as "this
            # release ships no plist" would be a rehearsal confidently answering a
            # question it did not ask. Ask the TAG instead, which is a question a dry
            # run can honestly answer, and say plainly what is left unchecked.
            present = git(["cat-file", "-e", f"{tag}:{rel}"],
                          deploy_tree(system), timeout=60).returncode == 0
            if not present:
                refuse(f"{tag} carries no {rel}; a system opting into automatic cutover "
                       f"must ship the plist it wants bootstrapped")
                continue
            log(f"cutover: would bootout {unit}, install {rel} from {tag} to "
                f"{launch_agents_dir() / f'{unit}.plist'}, and bootstrap it "
                f"(its Label and WorkingDirectory are checked against the checked-out "
                f"tree, which a dry run does not write)")
            acted.append({"unit": unit, "dry_run": True})
            continue

        if src is None:
            # DISTINCT FROM ABSENCE, and it has to be: the render declined — an
            # unsubstituted placeholder, or a production configuration that cannot be
            # read — and the in-tree file it would otherwise fall back to is the stale
            # unbound artifact this refusal exists to keep out of production.
            refuse("no installable plist could be produced for this unit; the render "
                   "refused and there is no safe file to fall back to")
            continue
        if not src.exists():
            refuse(f"the deployed tag carries no {rel}; a system opting into automatic "
                   f"cutover must ship the plist it wants bootstrapped")
            continue
        try:
            declared = plistlib.loads(src.read_bytes())
        except Exception as e:                       # noqa: BLE001 — any parse failure
            refuse(f"{src} is not a readable plist — {e}")
            continue
        if declared.get("Label") != unit:
            refuse(f"{src} declares Label {declared.get('Label')!r}, not {unit!r}; "
                   f"bootstrapping it would load a different job and restart nothing")
            continue
        wd = declared.get("WorkingDirectory")
        if not (wd == expected or (wd or "").startswith(expected + "/")):
            refuse(f"{src} sets WorkingDirectory to {wd!r}, which is outside the deploy "
                   f"tree {expected}; installing it would leave production running code "
                   f"this deploy did not place")
            continue

        installed = launch_agents_dir() / f"{unit}.plist"
        lint = run(["plutil", "-lint", str(src)], timeout=30)
        if lint.returncode != 0:
            refuse(f"plutil rejected {src} — {(lint.stderr or lint.stdout).strip()}")
            continue

        # Keep the plist this replaces, but only the FIRST one: that is the pre-cutover
        # article, the one naming wherever production used to run. Overwriting it on a
        # later cutover would replace the only record of that with a copy of what we
        # ourselves installed.
        backup = pre_cutover_dir(system) / f"{unit}.plist"
        if installed.exists() and not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed, backup)
            log(f"cutover: saved the pre-cutover {unit}.plist to {backup}")

        # Tolerated: a unit that is not loaded cannot be booted out, and that is the
        # normal case here rather than an error.
        run(["launchctl", "bootout", f"{domain}/{unit}"], timeout=60)
        installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, installed)
        log(f"cutover: installed {src} to {installed}")

        boot = run(["launchctl", "bootstrap", domain, str(installed)], timeout=120)
        if boot.returncode != 0:
            detail = ((boot.stderr or boot.stdout) or "").strip()
            raise DeployError(
                f"{system}: installed {installed} but launchctl bootstrap failed — "
                f"{detail}. The unit is now NOT loaded. The plist it replaced, if there "
                f"was one, is at {backup}.")
        run(["launchctl", "enable", f"{domain}/{unit}"], timeout=60)
        log(f"cutover: bootstrapped {unit} in {domain}")
        acted.append({"unit": unit, "ok": True})

    return {"mode": "auto", "acted": acted, "refused": refused, "deferred": deferred}


def maybe_auto_cutover(system: str, entry: dict, contract: dict, cut: dict, tag: str,
                       log, dry_run: bool) -> tuple[dict, Optional[dict]]:
    """Cut over if the registry says to, then RE-ASK the cutover question.

    The answer is re-measured through `cutover_state` rather than inferred from "the
    commands exited 0", because what makes a system live is that its loaded unit reports
    a working directory inside the deploy tree — and that is a fact to read back, not one
    to conclude. A dry run performs nothing, so it also cannot become cut over."""
    if cutover_mode(system, entry) != "auto":
        return cut, None
    # Checked HERE rather than inside `perform_cutover`, so it is answered once for the
    # system instead of per unit: a tag that would move the runner backwards is wrong for
    # every unit at once, and refusing three times says the same thing three times.
    regress = self_cutover_would_regress(system, entry, tag)
    if regress:
        log(f"cutover: REFUSED for {system} — {regress}.")
        return cut, {"mode": "auto", "acted": [], "deferred": [],
                     "refused": [{"unit": "(all)", "reason": regress}]}
    record = perform_cutover(system, entry, contract, cut, tag, log, dry_run)
    if dry_run or not record["acted"]:
        return cut, record
    return cutover_state(system, entry, contract, log), record


CUTOVER_EVENT_KEY = "cutover_event"


def cutover_event(tag: str, cut: dict, act: Optional[dict]) -> dict:
    """What this run did about the cutover, STAMPED WITH THE TAG IT BELONGS TO.

    TWO FACTS THAT EXIST NOWHERE ELSE, and WI-0350's acceptance is the second one.

    `how` first. "The runner performed the cutover" and "the runner found it already
    done" leave ledgers that differ only by the PRESENCE of a `cutover_action` key, and
    receipts that do not differ at all -- `result_brief` rendered neither. So the question
    this whole feature exists to answer, *did this reach live with no human step*, was
    unanswerable from any artifact that left the deploying machine. It was asked for real
    once: a hypothetical member `example-app`'s own cutover receipt
    (`outbox/to-example-app-arch/2020-01-01-deploy-example-app-deployed-v1.0.0.md`) reported
    `deployed` and verify-passed and could not distinguish the runner having bootstrapped the
    unit from a person having done it and the next sweep merely noticing. The ledger is
    machine-local by design (ADR-0103 D7) and the machine is the Runner, which no
    Architect can reach -- so "read the ledger" is not an available answer, and the
    receipt is the whole of what anyone else gets.

    `tag` second, and it is what keeps the first one honest. `write_ledger` MERGES, so
    every field outlives the run that wrote it: `cutover_action` survives untouched into
    the next release's record, and a receipt that rendered it unguarded would present last
    month's cutover as this release's. That is the stale-field-read-as-current defect
    arriving in the one place where being wrong means asserting no human was involved when
    one was. Stamping the tag makes staleness DETECTABLE by a reader instead of erasing
    the record to avoid it -- how this machine became live stays on file, and a renderer
    compares the stamp against the tag it is describing and says nothing when they differ.

    Deliberately NOT derived at render time from `cutover` + `cutover_action`. Both are
    sticky, so a renderer inferring `how` from them would be inferring it from fields that
    may belong to different runs -- which is the same defect one layer up."""
    act = act or {}
    if act.get("acted"):
        how = "performed"
    elif act.get("deferred"):
        how = "deferred"
    elif act.get("refused"):
        how = "refused"
    elif cut.get("cut_over"):
        how = "confirmed"
    else:
        how = "pending"
    return {"how": how, "tag": tag, "at": _now()}


def restart_units(system: str, entry: dict, contract: dict, log, dry_run: bool) -> list[dict]:
    """Make the new code the code that runs.

    `none` is a real strategy, not a skipped step: a SCHEDULED one-shot re-execs its
    program at each fire, so the next fire already runs the new tag — while kickstarting
    it would fire the job at an arbitrary hour, which for a job whose timing is its point
    means running it at 3am."""
    mode = contract.get("restart", "kickstart")
    # THE SAME FILTER `cutover_state` USES, for WI-0417's reason one layer down: two call
    # sites walking the same list separately is how one of them learns a rule and the other
    # does not. Kicking a unit that belongs to another machine is a `launchctl kickstart`
    # against a label this host should never have had.
    units = units_for_this_host(system, entry, contract, log)
    if mode == "none" or not units:
        log(f"restart: {mode} — {len(units)} unit(s) left alone; the next scheduled fire "
            f"runs the new tag")
        return [{"unit": u, "mode": "none"} for u in units]
    results = []
    for unit in units:
        target = f"{launchctl_domain()}/{unit}"
        cmd = (["launchctl", "kickstart", "-k", target] if mode == "kickstart"
               else ["launchctl", "kickstart", target])
        log(f"restart: {' '.join(cmd)}")
        if dry_run:
            results.append({"unit": unit, "mode": mode, "dry_run": True})
            continue
        r = run(cmd, timeout=120)
        ok = r.returncode == 0
        results.append({"unit": unit, "mode": mode, "ok": ok,
                        "detail": evidence.clip(((r.stderr or r.stdout) or "").strip(),
                                                400, keep="head")})
        if not ok:
            raise DeployError(f"{system}: could not restart {unit} — "
                              f"{((r.stderr or r.stdout) or '').strip()}")
    return results


def run_apply(system: str, entry: dict, contract: dict, log, dry_run: bool) -> dict:
    spec = contract.get("apply")
    if not spec:
        return {"declared": False}
    log(f"apply: {' '.join(spec['cmd'])}")
    if dry_run:
        return {"declared": True, "dry_run": True}
    try:
        r = run(spec["cmd"], cwd=work_dir(system, entry),
                timeout=spec.get("timeout_seconds", 1800))
    except OSError as e:
        # The same hazard `run_gate` now handles, in the step that speaks in refusals
        # instead of records. `refuse_unresolvable_verbs` catches this before the
        # checkout for a verb resolved on PATH; what reaches here is the case it
        # deliberately defers — an argv[0] naming a path the new tag turned out not to
        # ship. Either way it must be this system's refusal, not the sweep's death.
        raise DeployError(
            f"{system}: apply could not start — {e}. The contract names "
            f"{spec['cmd'][0]!r}, which is not something this machine can execute from "
            f"{work_dir(system, entry)}. PATH was {os.environ.get('PATH', '')!r}.") from e
    if r.returncode != 0:
        # THE PATH TRAVELS WITH THE FAILURE, rather than living in a log line nobody
        # keeps. An apply verb that resolves names on PATH inside its OWN script is the
        # one shape `refuse_unresolvable_verbs` structurally cannot preflight — a real
        # member's `deploy/apply.sh` once refused with "<verb> is not on PATH on
        # this machine", a name this runner never saw — and the PATH it was refused
        # against is the single fact that turns that sentence from a mystery into a
        # diagnosis. This message is what reaches the comms note and the Architect's
        # brief, so it is where the fact has to be.
        raise DeployError(
            f"{system}: apply failed — "
            f"{evidence.clip(((r.stderr or r.stdout) or '').strip(), 800, keep='head')} "
            f"[the apply ran with PATH={os.environ.get('PATH', '')!r}]")
    return {"declared": True, "ok": True}


# ── escalation ───────────────────────────────────────────────────────────────────

def escalate(system: str, headline: str, body: str, log) -> Optional[Path]:
    """A failed deploy has to be LOUD. Per ADR-0046 the federation's channel to operator is a
    comms note, so that is where this lands — plus stderr, plus a non-zero exit, plus the
    ledger. Four surfaces because the one that gets read depends on how the deploy was
    triggered, and an escalation nobody happens to be looking at is not an escalation."""
    day = _dt.date.today().isoformat()
    path = comms_dir() / f"{day}-deploy-{system}-failed.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {headline}\n\n"
            f"- **Kind:** blocked\n"
            f"- **System:** {system}\n"
            f"- **Raised:** {_now()}\n"
            f"- **Raised by:** `deploy/runner.py` (ADR-0103 D6) — no human was in the loop.\n\n"
            f"{body}\n")
    except OSError as e:
        log(f"escalation: could not write comms note — {e}")
        return None
    log(f"escalation: {path}")
    publish_note(path, system, log)
    return path


def _publish_note_on_channel(path: Path, system: str, log) -> bool:
    """The pre-production escalation path: publish from a sealed tree, and only there.

    In a lane or on the trunk the note lands in a checkout a session is already about
    to commit, and committing it from underneath that session is the second writer P13
    forbids -- so on any tree with a symbolic branch head this does nothing at all,
    deliberately, and the session's own close carries it as it always has.
    """
    if not channel.is_deploy_tree(REPO_ROOT):
        return False
    try:
        rel = str(path.relative_to(channel.data_root(REPO_ROOT)))
        text = path.read_text(encoding="utf-8")
    except (ValueError, OSError) as e:
        log(f"escalation: could not prepare the note for the channel — {e}")
        return False
    return channel.publish(
        {rel: text},
        f"chore(comms): {system} deploy escalation from the Runner",
        "Written by deploy/runner.py with no human in the loop (ADR-0103 D6) and carried "
        "on the Runner channel (WI-0360), because a sealed deploy tree has no branch to "
        "commit on and an escalation that cannot travel has not been raised.",
        origin=channel.origin_of(REPO_ROOT), log=log)


def publish_note(path: Path, system: str, log) -> bool:
    """Enqueue an escalation in explicitly configured production.

    Development retains its session-owned comms file. Production only persists local
    mail here; the dedicated worker owns publication and acknowledgment tracking.

    A SEALED TREE WITH NO SERVICE CONFIG STILL PUBLISHES THE OLD WAY (WI-0361). This
    item's contract is to preserve current behaviour unless production is opted into,
    and nothing sets `POGA_FEDERATION_CONFIG` yet — provisioning is WI-0365's. Routing
    the deploy-tree case straight to `return False` retired the channel publish before
    its replacement could run, so on the first cutover every escalation would be
    written into a release clone and silently go nowhere. That is verbatim the failure
    the channel path was built to end: several escalations for a single real member sat
    untracked in the process clone, one per deploy attempt, each a deploy failure nobody
    was told about. Writing the file was never the escalation; publishing it is.
    """
    try:
        roots = production.resolve(REPO_ROOT)
        if roots is None:
            return _publish_note_on_channel(path, system, log)
        text = path.read_text(encoding="utf-8")
        import mailqueue
        import mailnames
        # The local note keeps its day name (today's escalation, rewritten per attempt);
        # the MAIL carries a unique name and an edit-id. A day-named, id-less escalation
        # collided at the receiver with the day's first one and was retried forever.
        #
        # ONE MESSAGE PER DISTINCT FAILURE PER DAY, not one per attempt. A failing
        # system is retried about every 20 minutes; with unique names each attempt would
        # now be DELIVERED — ~70 identical escalations a day in the federation inbox. The
        # identity is the note minus its `Raised` time, so a repeat is the same message
        # and a changed failure is a new one.
        stable = re.sub(r"^- \*\*Raised:\*\*.*$", "", text, flags=re.M)
        message_id = (f"deploy-escalate-{path.stem}-"
                      f"{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]}")
        if mailqueue.holds(roots, message_id):
            log("escalation: the same failure is already queued today — not re-sent")
            return True
        message_id, mail_name, text = mailnames.identity(
            path.name,
            "---\napply: manual\nmanual-reason: attended\n"
            "attended-because: deployment escalation requires review\n---\n\n" + text,
            message_id=message_id)
        mailqueue.enqueue(roots, "federation-arch", mail_name, text, message_id=message_id,
                          provenance={"producer": "deploy.escalate", "system": system})
        log("escalation: queued locally for federation-arch")
        return True
    except (ValueError, OSError) as exc:
        log(f"escalation: could not queue notice — {exc}")
        return False



def _publish_outbox_on_channel(subject: str, body: str, log) -> bool:
    """Carry everything in the outbox on the Runner channel. True if it was published.

    THE ESCAPE FOR EVIDENCE ABOUT A DEPLOY THAT DID NOT HAPPEN (WI-0417). `outbox.publish`
    refuses to commit from a sealed tree, and that refusal is right: a detached HEAD has no
    branch and no push destination, so a commit made there is discarded by the next tag
    checkout. But until a release binds this machine to a service configuration,
    `production.resolve` answers None, so `post_result` and `diagnose.post_if_changed` fall
    PAST the mail-worker branch into that refusal and the evidence is dropped. The channel
    the migration would provision is gated on the migration, and the migration is the thing
    whose failure needs reporting — so deploy failures on the Runner would produce no
    outbound word at all and could only be found by a human reading the host.

    NOTHING IS MADE TO PUBLISH FROM A SEALED TREE THAT DOES NOT ALREADY PUBLISH FROM ONE.
    This is the same route `_publish_note_on_channel` has used for escalations since
    WI-0360, taken by the same program, and `outbox/` is already in
    `channel.LANDABLE_PREFIXES` — the branch was always meant to carry these files. What
    changes is only that the refusal hands them over instead of dropping them.

    WI-0361'S PRINCIPLE IS UNTOUCHED, and this is the line that keeps it that way. Production
    use is explicitly selected and never inferred from a detached HEAD: where a service
    configuration EXISTS, this returns False without looking at anything else and the mail
    worker remains the only writer. The detached-HEAD test below selects the *pre-production*
    fallback, which is exactly what it selected for escalations before this item.

    THE WHOLE BOX, not just the brief that was written a moment ago. Anything an earlier
    sweep dropped is still sitting there unpublished, and re-offering a file the branch
    already carries byte-for-byte is a no-op in `channel.publish` by construction. One bad
    file is skipped rather than failing the rest: a receipt that cannot be decoded must not
    take the bundle down with it."""
    try:
        if production.resolve(REPO_ROOT) is not None:
            return False
    except production.ConfigurationError as e:
        log(f"outbox: not carrying the box on the channel — {e}")
        return False
    if not channel.is_deploy_tree(REPO_ROOT):
        return False

    box = outbox_dir()
    base = channel.data_root(REPO_ROOT)
    files: dict[str, str] = {}
    for path in sorted(box.rglob("*")):
        if path.name.startswith(".") or not path.is_file():
            continue
        try:
            rel = str(path.relative_to(base))
            # UnicodeDecodeError IS a ValueError and `relative_to` raises ValueError too;
            # a file deleted between the walk and the read, or one this process cannot
            # open, raises OSError. Two different faults, and a handler naming only one of
            # them lets the other out of a function whose whole contract is to swallow.
            files[rel] = path.read_text(encoding="utf-8")
        except (ValueError, OSError) as e:
            log(f"outbox: could not prepare {path.name} for the channel — {e}")
            continue
    if not files:
        return False
    return channel.publish(files, subject, body,
                           origin=channel.origin_of(REPO_ROOT), log=log)


def publish_outbox(subject: str, body: str, log) -> bool:
    """Publish the outbox, falling back to the channel when the tree is sealed (WI-0417).

    The one door both evidence writers use — `post_result` for a deploy receipt and
    `diagnose.post_if_changed` for a diagnosis bundle — so neither can acquire the escape
    without the other, which is how they came to differ from `escalate` in the first
    place."""
    if outbox.publish(subject, body, root=outbox_dir(), log=log):
        return True
    return _publish_outbox_on_channel(subject, body, log)



# ── the result's return path (WI-0230) ───────────────────────────────────────────

def recipient_architect(system: str) -> tuple[str, str]:
    """(architect-id, how it was determined) for the system whose deploy this was.

    RESOLVED beats DERIVED and the two are reported apart, because they are not equally
    true. `mailboxes.json` is the roster delivery actually resolves against (ADR-0088 D5);
    a system with a row there has an id somebody registered. Everything else gets
    `<system>-arch` — which is the convention every member does follow, and still a guess,
    so the brief says which one it is rather than presenting both as the same fact
    ([`declare-what-a-check-assumes`]).

    A derived id is NOT a reason to refuse to write the receipt. A real member has been
    registered for deploy here while absent from the roster; queueing its receipt at the
    conventional address puts the record in a TRACKED directory that reaches the
    development machine by `origin` whether or not any mailbox can be resolved for it —
    which is the whole of what this item asked for. The poller reports it `unroutable`
    until somebody adds the row, and that is the visible, correct state."""
    try:
        members = json.loads(mailbox_registry_path().read_text()).get("members", {})
        row = members.get(system) or {}
        aid = (row.get("architect_id") or "").strip()
        if aid:
            return aid, "resolved from mailboxes.json"
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return f"{system}-arch", "derived from the <system>-arch convention; no roster row"


def _gate_prose(label: str, rec: Any) -> str:
    """One gate's result, rendered so "not declared" can never read as "passed"."""
    if not isinstance(rec, dict) or not rec:
        return f"- **{label}:** not recorded."
    if rec.get("skipped"):
        return f"- **{label}:** skipped — {rec['skipped']}."
    if not rec.get("declared"):
        return f"- **{label}:** NOT DECLARED by the contract — unchecked, not passed."
    if rec.get("dry_run"):
        return f"- **{label}:** declared; not run (dry run)."
    verdict = "passed" if rec.get("ok") else "FAILED"
    line = f"- **{label}:** {verdict} (exit {rec.get('exit')})."
    detail = (rec.get("detail") or "").strip()
    return line + (f"\n\n```\n{detail}\n```\n" if detail else "")


#: How a `cutover_event["how"]` reads to somebody who was not there. The first line of
#: each is the answer to WI-0350's acceptance question and is deliberately blunt about the
#: human: a receipt that leaves it to be inferred is the receipt we already had.
_CUTOVER_PROSE = {
    "performed": "**Cut over by the runner** — `cutover: auto` bootstrapped the unit(s) "
                 "below out of the deploy tree. No human step.",
    "confirmed": "**Already cut over before this run measured it.** The runner did not "
                 "perform it — `cutover` is `manual` for this system, or the unit(s) were "
                 "pointed at the deploy tree by something outside this program. A person "
                 "was in the loop, or an earlier run was.",
    "deferred":  "**Deferred, not skipped.** The unit(s) below could not be booted out by "
                 "this process; a detached finisher was spawned to do it once the sweep "
                 "exits. Nothing is live until that lands — the next sweep reports it.",
    "refused":   "**Automatic cutover REFUSED.** The runner declined rather than guessing; "
                 "the reason is below. The tree holds the tag and nothing executes it.",
    "pending":   "**Not cut over.** The unit(s) below still run somewhere other than the "
                 "deploy tree, so the tag on disk is not the code that executes.",
}


def _cutover_prose(rec: dict, tag: str) -> list[str]:
    """The `## Cutover` block, or NOTHING — and the nothing is most of the time.

    SILENCE IS THE NORMAL CASE, for `_gate_prose`'s sibling reason and one of its own. A
    section on every receipt is a section nobody reads by the third one, and the fact that
    has to carry — *a person was needed for this* — would be invisible again, this time
    inside a heading that is always present. So this renders only when the run actually
    ANSWERED the cutover question: an ordinary redeploy of a system that was already live
    says nothing here, because nothing about its cutover happened.

    THE TAG GUARD IS NOT BELT-AND-BRACES. `write_ledger` merges, so a `cutover_event`
    written for `v1.0.0` is still sitting in the record when `v1.1.0` deploys. Rendering it
    unguarded would put "no human step" under a release that never asked the question. When
    the stamp names a different tag this says nothing at all rather than hedging: a reader
    who needs the older event can read the ledger, and a receipt that hedges about whether
    its own headline applies is worse than one that omits it."""
    ev = rec.get(CUTOVER_EVENT_KEY) or {}
    how = ev.get("how")
    if how not in _CUTOVER_PROSE:
        return []
    if ev.get("tag") != tag:
        return []
    out = ["## Cutover", "", _CUTOVER_PROSE[how], ""]
    act = rec.get("cutover_action") or {}
    for a in act.get("acted", []):
        out.append(f"- bootstrapped `{a['unit']}` out of the deploy tree")
    for d in act.get("deferred", []):
        # `finisher_spawned` false is the case that must NOT read as a deferral: the unit
        # was skipped and nothing is coming for it, which is a silent omission wearing a
        # deferral's word (`_spawn_self_cutover_finisher`'s own hazard).
        out.append(
            f"- `{d['unit']}` is the sweep's own unit — "
            + (f"a detached finisher is waiting for pid {d.get('pid')} to exit and will "
               f"cut it over; its result appears in the NEXT sweep's output"
               if d.get("finisher_spawned") else
               "**the finisher could not be spawned**, so nothing is coming for it and "
               "it stays where it is"))
    for r in act.get("refused", []):
        out.append(f"- `{r['unit']}` REFUSED — {r.get('reason', 'no reason recorded')}")
    cut = rec.get("cutover") or {}
    # The measured state, not the commands' exit codes -- the same distinction
    # `cutover_state` is built on, carried into the thing that travels.
    for pend in cut.get("pending", []):
        out.append(f"- `{pend['unit']}` still runs in `{pend.get('runs_in')}`")
    for u in cut.get("unloaded", []):
        out.append(f"- `{u}` is not loaded on that machine")
    if cut.get("expected"):
        out.append(f"- expected working directory: `{cut['expected']}`")
    out.append(f"- measured at {ev.get('at', '(unstamped)')}, for `{ev.get('tag')}`")
    out.append("")
    return out


def result_brief(system: str, rec: dict, how: str) -> tuple[str, str]:
    """(filename, text) for one deploy outcome, rendered FROM THE LEDGER RECORD.

    The brief is not a second account of the deploy — it is the ledger made to travel. The
    ledger is machine-local and deliberately so (ADR-0103 D7: it records what runs on THIS
    machine, and a tracked file two machines both write is WI-0132's defect). That decision
    is why the answer never left the Runner, and the fix is not to un-decide it: the record
    stays local and a COPY of it is sent, so there remains exactly one writer of what runs
    here ([P13](../principles/master.md#p13--single-writer-per-state)) and the development
    machine still gets told.

    `apply: manual` / `manual-reason: attended` is the header, and it is a routing fact
    rather than a formality. A receipt has nothing to apply, so the strict `auto` schema
    would be a costume — and the OTHER manual route hands the brief to the headless
    adopt-runner (ADR-0050), which would open a session per deploy to adopt a record that
    asks for no change. `attended` is the route that ends in somebody reading it, which is
    the entire request. A brief whose header satisfies neither is bucketed `malformed` by
    the poller forever, and that bucket does not move the exit code — so getting this wrong
    builds a return path whose failure is silent."""
    status = rec.get("status") or "unknown"
    tag = rec.get("current") or rec.get("attempted") or "(none)"
    # A REFUSED automatic cutover is a request; `awaiting-cutover` on its own is not. The
    # two share a status and mean opposite things: a row on `manual` stopping there is the
    # runner doing exactly what it was told, while a row that opted into `auto` and was
    # refused is the runner UNABLE to finish -- and every refusal it can raise (the tag
    # ships no plist, the plist declares another Label, its WorkingDirectory is outside the
    # deploy tree, plutil rejects it) is fixed by cutting a corrected release, which is a
    # person's act in a repo this program does not own. Left in the `Nothing.` bucket it
    # reads as a parked system and waits for a sweep that will refuse identically forever.
    refused_cutover = bool(((rec.get("cutover_action") or {}).get("refused"))
                           and (rec.get(CUTOVER_EVENT_KEY) or {}).get("tag") == tag)
    # ONE verdict, read twice. `Kind` and the closing section answer the same question and
    # were two separate spellings of it; a third outcome added to one of them is a receipt
    # whose headline and whose ask disagree.
    bad = ("fail" in status or status in ("rolled-back", "rollback-failed")
           or refused_cutover)
    stamp = rec.get("deployed_at") or rec.get("failed_at") or rec.get("checked_at") or _now()
    # STATUS IS PART OF THE IDENTITY. A tag's stamp need not move when its status does
    # (`smoke-failed`, then `awaiting-cutover`, for one tag), so an id without the status
    # named two different receipts alike and the receiver read the second as AlreadyHeld
    # with content it could not verify — never delivered, never acknowledged.
    edit_id = (f"deploy-{system}-{re.sub(r'[^A-Za-z0-9._-]', '-', status)}-{tag}-"
               f"{re.sub(r'[^0-9]', '', stamp)}")
    filename = f"{stamp[:10]}-deploy-{system}-{status}-{tag}.md"

    body = [
        "---",
        "apply: manual",
        "manual-reason: attended",
        "attended-because: a deploy receipt records what already happened on the Runner; "
        "there is nothing here to apply",
        f"edit-id: {edit_id}",
        "---",
        "",
        f"# {system}: {tag} — {status}",
        "",
        f"- **Kind:** {'blocked' if bad else 'milestone'}",
        f"- **System:** {system}",
        # SAYS WHAT IT IS ABOUT, because it used to be read as something else. This is a
        # claim about the AUTHOR of the receipt -- nobody typed it -- and it sat two lines
        # above an Outcome whose cutover the receipt did not report, where it reads as a
        # finding that no human touched the DEPLOY. That reading was once wrong twice over
        # for a real member: the cutover verb is not recorded either way
        # in this file's old form, and the checkout had been hand-pulled that
        # day to make the run possible at all. `## Cutover` is where the deploy's own
        # answer lives; this line stays in its lane.
        f"- **Written by:** `deploy/runner.py` on the machine that deployed it — this "
        f"record was generated, not typed. (Whether the DEPLOY needed a human is the "
        f"`Cutover` section's question, not this line's.)",
        f"- **Recipient:** `{recipient_architect(system)[0]}` ({how}).",
        "",
        "## What happened",
        "",
        f"- **Outcome:** `{status}`",
        f"- **Tag now on the deploy tree:** `{rec.get('current') or '(nothing)'}`",
        # The ledger's own `previous` — what the last SUCCESSFUL deploy replaced, and what
        # `--rollback` would return to. On a failed run it is deliberately not touched, so
        # it can name a tag older than the one that just came back; labelled for what it
        # is rather than as "the tag before this one", which it is not on every path.
        f"- **Rollback target (ledger `previous`):** `{rec.get('previous') or '(none recorded)'}`",
        f"- **Tag attempted:** `{rec.get('attempted') or tag}`",
        f"- **Trigger:** `{rec.get('trigger') or 'unknown'}`",
        f"- **Recorded at:** {stamp}",
        f"- **Brief written at:** {stamp}",
        "",
    ]
    # CUTOVER IS WHAT `Outcome` MEANS, so it sits directly under it rather than below the
    # gates: `deployed` and `awaiting-cutover` are both honest and they differ by exactly
    # this, and a reader who stops after the first section has the answer either way.
    body += _cutover_prose(rec, tag)
    # STATE IS PART OF THE OUTCOME, not a footnote. A deploy that seeded from somewhere
    # other than what the operator declared is still `deployed`, and reporting only the
    # status makes those two indistinguishable to the one person who could tell whether it
    # mattered. Absent here means nothing unusual happened, which is the normal case.
    if rec.get("state_notes"):
        body += ["## State", ""]
        body += [f"- {n}" for n in rec["state_notes"]]
        body += [""]
    body += [
        "## Gates",
        "",
        _gate_prose("Smoke", rec.get("smoke")),
        _gate_prose("Verify", rec.get("verify")),
    ]
    if rec.get("rollback_verify"):
        body.append(_gate_prose("Verify after rollback", rec["rollback_verify"]))
    if rec.get("detail"):
        body += ["", "## Failure", "", "```", str(rec["detail"]).strip(), "```"]
    body += [
        "",
        "## What this asks of you",
        "",
        (f"**Something needs you.** This system asked for automatic cutover and the runner "
         f"REFUSED to perform it — the reason is under `Cutover` above, and it will refuse "
         f"the same way on every sweep until a corrected release is cut. `{tag}` is on "
         f"disk and nothing executes it."
         if refused_cutover else
         f"**Something needs you.** `{tag}` did not reach a healthy running state — see the "
         f"outcome and the gate output above. The deploying machine has already done what "
         f"it can; the next move is a fix in this system's code or its deploy contract."
         if bad else
         "Nothing. This is a record, not a request. The deploy tree, the ledger and the "
         "running units belong to the machine that deployed; this is the copy that travels "
         "so the answer is readable where the code is written."),
        "",
    ]
    return filename, "\n".join(body)


def post_result(system: str, before: dict, dry_run: bool, log,
                failure: Optional[str] = None) -> dict:
    """Queue this deploy's outcome for the system's Architect, and commit it so it travels.

    WHEN IT POSTS, and the rule is the whole design: **a state change, or a rollback that
    failed.** The sweep re-runs every ten minutes, so a receipt per invocation would be a
    tracked file per system per ten minutes forever — the record would bury the thing it
    records. Comparing `(status, current, attempted)` across the run is what separates *the
    deploy moved* from *we looked again*, and it cannot drift the way a list of call sites
    would: a terminal outcome added later posts a brief without anyone remembering to add
    one ([`ship-the-detector-with-the-capability`]).

    The exception is `RollbackFailed`, which is raised BEFORE any ledger write — so its
    record shows no change at all, and the one outcome where production is in an unknown
    state would be the one that stayed silent. It posts on the raise instead.

    A DRY RUN POSTS NOTHING. A rehearsal that writes a tracked file is not a rehearsal, and
    this program has already shipped that bug once against the ledger.

    NOTHING HERE CAN FAIL THE DEPLOY. The deploy has already happened; a mail problem is
    not a deploy problem, and reporting one as the other is how a green release starts
    reading as red. Every failure is logged and recorded, and the exception is swallowed."""
    if dry_run:
        return {"posted": False, "why": "dry run"}

    rec = read_ledger(system)
    if failure is not None:
        rec = {**rec, "status": "rollback-failed", "failed_at": _now(), "detail": failure}
    else:
        keys = ("status", "current", "attempted")
        if all(before.get(k) == rec.get(k) for k in keys):
            return {"posted": False, "why": "no state change"}

    aid, how = recipient_architect(system)
    try:
        filename, text = result_brief(system, rec, how)
        roots = production.resolve(REPO_ROOT)
        if roots:
            import mailqueue
            event = rec.get("deployed_at") or rec.get("failed_at") or rec.get("checked_at") or _now()
            dest = mailqueue.enqueue(roots, aid, filename, text,
                                     message_id=f"deploy:{system}:{rec.get('status')}:{event}",
                                     provenance={"producer": "deploy.runner", "system": system})
        else:
            dest = outbox.post(aid, filename, text, root=outbox_dir())
    except (ValueError, OSError) as e:
        log(f"outbox: could not queue the result for {aid} — {e}")
        return {"posted": False, "why": str(e), "architect_id": aid}
    log(f"outbox: queued {dest.name} for {aid} ({how})")
    if roots:
        return {"posted": True, "architect_id": aid, "resolved": how,
                "brief": dest.name, "queued": True, "published": False, "delivered": False}
    pushed = publish_outbox(
        f"chore(outbox): {system} {rec.get('status')} — deploy receipt for {aid}",
        f"Written by deploy/runner.py on the machine that deployed it (WI-0230). The "
        f"ledger stays machine-local per ADR-0103 D7; this is the copy that travels to "
        f"the machine the code is written on.",
        log)
    return {"posted": True, "architect_id": aid, "resolved": how,
            "brief": dest.name, "committed": pushed}


# ── the deploy ───────────────────────────────────────────────────────────────────

# -- canary ----------------------------------------------------------------------
#
# A CANARY IS EVIDENCE, AND EVIDENCE MAY NOT ACT AS THE SYSTEM. Everything below is written
# to that rule ([`evidence-is-separated-from-state-by-construction`]). The canary answers
# one question — *does this tag pass its own smoke check on this machine?* — and the
# value of the answer depends entirely on it having been obtained without changing what is
# running. So the separation is arithmetic rather than discipline: a different root
# (`canary_root()`, derived with an unconditional suffix), a different ledger directory,
# and a code path that never calls `checkout`, `seed_state`, `restart_units`,
# `perform_cutover`, `repoint_cli_link`, `write_ledger` or `post_result` at all. There is
# no `canary=True` flag threaded through the deploy path, deliberately: a boolean default
# is one wrong argument away from a probe that writes production, and the whole point is
# that no argument can get there from here.
#
# WHAT IT DELIBERATELY DOES NOT DO. It does not seed state. `seed_state` copies from the
# machine-local vault and WRITES a seed map, which is a production-shaped write performed
# on behalf of a probe, and a smoke check is specified as a fast offline command anyway
# (ADR-0103 D5). A contract whose smoke needs seeded state will fail here, and the verdict
# says that is a canary limitation rather than a defect in the release — which is the
# distinction `declare-what-a-check-assumes` exists to force. It also does not mail the
# result home; that is B2 of the 2026-09-03 brief and is somebody else's item.


def canary_ledger_path(system: str) -> Path:
    """A SUBDIRECTORY, not a `<system>.canary.json` sibling, and the reason is a live one:
    `anything_cut_over()` globs `ledger_dir()/*.json` and reads `status` out of every file
    it finds. A canary record sitting in that glob would be a probe's opinion feeding a
    guard about what this machine has adopted. One directory level makes it unreachable."""
    return ledger_dir() / "canary" / f"{system}.json"


def read_canary_ledger(system: str) -> dict:
    """The last canary run for this system, or `{}` if there has never been one.

    Unreadable reads as `{}` WITH a marker, never as "never canaried" — the same rule
    `read_ledger` follows, for the same reason: the two states look identical at the call
    site and only one of them means "go ahead"."""
    path = canary_ledger_path(system)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"_unreadable": True}


def write_canary_ledger(system: str, record: dict) -> None:
    """REPLACES rather than merges, which is the opposite of `write_ledger` and is correct
    here. The production ledger merges because its fields have different lifetimes --
    `previous` outlives the deploy that set it and rollback depends on it. A canary record
    has exactly one lifetime: the run that wrote it. Merging would leave last week's
    passing smoke sitting beside this morning's failing one under one timestamp, which is
    the single most misreadable thing a release gate could say."""
    path = canary_ledger_path(system)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def ensure_canary_tree(system: str, entry: dict, log) -> Path:
    """Clone the canary tree if it does not exist. Mirrors `ensure_tree`'s refusal: a
    non-empty directory that is not a git clone is somebody's something, and this program
    does not find out what by overwriting it."""
    tree = canary_tree(system)
    if (tree / ".git").exists():
        return tree
    if tree.exists() and any(tree.iterdir()):
        raise DeployError(f"{tree} exists and is not a git clone — refusing to touch it.")
    tree.parent.mkdir(parents=True, exist_ok=True)
    log(f"cloning {entry['remote']} → {tree}")
    r = run(["git", "clone", "--no-checkout", entry["remote"], str(tree)], timeout=900)
    if r.returncode != 0:
        raise DeployError(f"clone failed — {(r.stderr or r.stdout).strip()}")
    return tree


def canary(system: str, tag: str, *, dry_run: bool = False, quiet: bool = False) -> int:
    """Exercise a tag in an isolated tree and report whether it passes its own smoke check.

    Exit 0 PASSED, 1 FAILED **or INCONCLUSIVE**. Three outcomes, two exit codes, and the
    collapse is deliberate: a candidate that has not been shown to work must not read as
    green to a caller that only looks at the status, and "no smoke check declared" has not
    shown anything. The word on stdout and in the record keeps the three apart for whoever
    reads them; the exit code answers the only question a script gets to ask, which is
    *may I promote this?*"""
    def log(msg: str) -> None:
        if not quiet:
            print(f"  {msg}", flush=True)

    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    entry = registry_entry(system)
    if not tag.startswith("v"):
        tag = f"v{tag}"
    if not SEMVER_TAG_RE.match(tag):
        # A VOCABULARY, not a ref resolver. Admitting any ref would make this "run the
        # contract against an arbitrary commit", which is a different and much larger verb
        # — and one whose answer nobody could file against a release, because the thing it
        # tested has no version. Releases and candidates are what this machine promotes.
        raise DeployError(
            f"{system}: {tag} is not a release or a release candidate. A canary exercises "
            f"a tag of the form vX.Y.Z or vX.Y.Z-rc.N; anything else has no version to "
            f"report a verdict against. Nothing was changed.")

    tree = ensure_canary_tree(system, entry, log)
    log("fetching tags from origin")
    git_ok(["fetch", "--tags", "--prune", "--force", "origin"], tree, f"{system} fetch")
    if git(["rev-parse", "--verify", f"{tag}^{{commit}}"], tree, 30).returncode != 0:
        raise DeployError(f"{system}: tag {tag} does not exist on origin.")

    # Read from the CANARY tree, which is the only clone this path is allowed to have
    # fetched into. Same tag, same object, same contract — ADR-0103 D5 is about the
    # contract coming from the tag rather than from a working tree, and it does.
    contract = read_contract(system, entry, tag, tree=tree)
    running = deployed_tag(system, entry)
    wd = canary_work_dir(system, entry)
    say(f"{system}: canary {tag} in {tree}"
        + (f"  (production holds {running})" if running else "")
        + ("  [dry-run]" if dry_run else ""))

    log(f"checkout: {tag} (detached)")
    if not dry_run:
        git_ok(["checkout", "--force", "--detach", tag], tree, f"{system} canary {tag}")
    sync_deps(system, entry, contract, log, dry_run, workdir=wd)
    smoke = run_gate(system, entry, contract.get("smoke"), "smoke", log, dry_run,
                     workdir=wd)

    # DRY RUN FIRST. An undeclared smoke on a dry run is still "nothing was measured", but
    # the inconclusive verdict below tells the reader the tree checked out — which a dry run
    # did not do. Two true statements, one of them about the wrong run.
    if dry_run:
        outcome = "dry-run"
    elif not smoke.get("declared"):
        outcome = "inconclusive"
    else:
        outcome = "passed" if smoke.get("ok") else "failed"

    if not dry_run:
        write_canary_ledger(system, {
            "kind": "canary",
            "system": system,
            "tag": tag,
            "outcome": outcome,
            "ran_at": _now(),
            "tree": str(tree),
            "smoke": smoke,
            # WHAT PRODUCTION HELD WHILE THIS RAN, read and never written. A verdict about
            # a candidate is only useful next to the thing it is a candidate to replace,
            # and six weeks later nobody remembers which that was.
            "production_tag_at_run": running,
        })

    if outcome == "inconclusive":
        say(f"{system}: CANARY INCONCLUSIVE — {tag} declares no smoke check, so nothing "
            f"was measured. That is not a pass. Its Architect owes the contract a `smoke` "
            f"command (ADR-0103 D5); until then a canary of this system can only report "
            f"that the tree checked out.")
        return 1
    if outcome == "dry-run":
        gate = ("its smoke check run there" if smoke.get("declared")
                else "NO smoke check run — this tag's contract declares none, so a real "
                     "canary of it would come back inconclusive")
        say(f"{system}: canary dry run — {tag} would be checked out into {tree} and "
            f"{gate}. Nothing was restarted and nothing was recorded.")
        return 0
    if outcome == "failed":
        say(f"{system}: CANARY FAILED — {tag} did not pass its own smoke check. "
            f"Production is untouched and still holds {running or '(nothing deployed)'}; "
            f"no unit was restarted. Detail:")
        say(f"  {smoke.get('detail', '').strip() or '(the command produced no output)'}")
        return 1
    # A PASSING CANARY IS NOT A PROMOTION, and the sentence says so for a candidate
    # because that is the moment somebody is most likely to think it was one. Cutting the
    # release tag remains the promotion decision (ADR-0103 D2); this only removes the
    # excuse for making it blind.
    next_step = (" Promoting it is still a separate act: cut the release tag, then deploy."
                 if is_prerelease(tag) else "")
    say(f"{system}: CANARY PASSED — {tag} smoked clean in {tree}. Nothing was restarted "
        f"and production still holds {running or '(nothing deployed)'}.{next_step}")
    return 0


def deploy(system: str, version: Optional[str] = None, rollback: bool = False,
           dry_run: bool = False, trigger: str = "manual", quiet: bool = False,
           drill: bool = False) -> int:
    """The verb, plus the one thing that must happen whichever way it ends: the outcome is
    queued for the system's Architect and committed, so a result stops being a fact that
    only the deploying machine holds (WI-0230). The wrapper is what makes that unmissable —
    every `return` in `_deploy` passes through here, and so does every raise."""
    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        if not quiet:
            print(f"  {msg}", flush=True)

    def say(msg: str) -> None:
        lines.append(msg)
        if not quiet:
            print(msg, flush=True)

    before = read_ledger(system)
    # THE ONE EXEMPTION FROM "A STATE CHANGE POSTS", AND IT IS NOT A CONVENIENCE. A drill
    # ends in `rolled-back` on purpose, so the unexempted rule would queue a brief telling
    # a real member's Architect that its release failed verification and was withdrawn. That
    # sentence would be false, it would be committed and pushed, and it would be read by
    # someone who cannot see this flag. A false alarm sent to another system's Architect is
    # worse than a missing receipt, and the drill's own record — the `drill` block the
    # ledger keeps, and the DRILL-labelled comms note — is where the event genuinely
    # belongs. `drill()` reports the outcome itself.
    posts = not drill
    # WHAT THE REFUSAL WAS REACHING FOR, captured on the way past. A refusal can be raised
    # before `resolve_target` has run at all (no tags, unreadable tree), so the attempted
    # tag is genuinely unknown for some of them — and `record_refusal` writes that as its
    # own answer rather than folding it into the same shape as a known one.
    attempt: dict = {}
    try:
        rc = _deploy(system, log, say, version=version, rollback=rollback,
                     dry_run=dry_run, trigger=trigger, drill=drill, attempt=attempt)
    except RollbackFailed as e:
        if posts:
            post_result(system, before, dry_run, log, failure=str(e))
        raise
    except DeployError as e:
        # A REFUSAL NOW REACHES THE LEDGER, and this is the whole of WI-0410.
        #
        # This comment used to say "a refusal leaves production untouched and the ledger
        # unchanged" and treat the second half as a property worth keeping. The first half
        # is right and is not weakened below: nothing here touches `status`, `current`,
        # `cutover`, `verify` or `deployed_at`, so every field describing what is STILL
        # RUNNING keeps describing it. The second half was the defect. A refusal reached
        # the operator as one line of launchd stdout and reached every surface that reads
        # the ledger — `--status`, `deploy/diagnose.py`, the posted bundle — as nothing at
        # all, so "this machine refused to deploy" and "this machine had nothing to do"
        # produced the identical bundle. That is the `declare-what-a-check-assumes`
        # collapse, and it would leave a dormant production invisible until a human read
        # a log on the host.
        #
        # HERE rather than in `sweep()`, because this wrapper is the one place every raise
        # from `_deploy` passes through — the scheduled sweep and the on-demand CLI verb
        # both arrive here. Recording it in the sweep's own handler would have fixed the
        # instance and left `poga deploy <system>` silent, which is
        # `retire-the-class-not-the-instance` exactly.
        if not dry_run and not drill:
            record_refusal(system, str(e), attempt.get("target"), trigger)
        if posts:
            post_result(system, before, dry_run, log)
        raise
    # A DEPLOY THAT GOT THIS FAR DID NOT REFUSE, so any outstanding refusal is history.
    # Clearing it here — one site, on the way out — rather than adding `refusal=None` to
    # each of the six terminal `write_ledger` calls: a clear that has to be remembered at
    # every future success path is one a future success path will forget, and a refusal
    # that outlives its own resolution reads as current forever.
    #
    # `rc` is deliberately not consulted. `awaiting-cutover` and `verify-failed` return
    # non-zero and are NOT refusals — they are verdicts, recorded in `status`, reached by a
    # deploy that ran. The distinction this field carries is "could not proceed", not
    # "proceeded and came out badly".
    if not dry_run and not drill and read_ledger(system).get("refusal"):
        write_ledger(system, refusal=None)
    if posts:
        post_result(system, before, dry_run, log)
    return rc


def _deploy(system: str, log, say, *, version: Optional[str] = None,
            rollback: bool = False, dry_run: bool = False,
            trigger: str = "manual", drill: bool = False,
            attempt: Optional[dict] = None) -> int:
    # Checked on the single-system verb as well as the sweep: the sweep is not the only way
    # a unit can invoke this program, and a guard with a way around it is a guard about
    # which route was taken rather than about the hazard.
    #
    # A DRILL IS SUBJECT TO IT TOO, deliberately. A drill is the most unattended thing this
    # program does — it is run by a scheduled poller with nobody watching — so exempting it
    # would put the one caller the guard was written for on the far side of it.
    refuse_unsealed_process_root()
    entry = registry_entry(system)
    tree = ensure_tree(system, entry, log)

    # Fetched even on a dry run. Fetching is read-only, and a dry run that resolves the
    # target against stale local refs answers a question nobody asked — it reports what
    # would happen against yesterday's tags, which is the one answer guaranteed to be
    # wrong exactly when someone is checking before a real deploy.
    log("fetching tags from origin")
    git_ok(["fetch", "--tags", "--prune", "--force", "origin"], tree, f"{system} fetch")

    target = resolve_target(system, tree, version, rollback)
    # The caller's refusal record reads this. Set the moment it is known and never unset:
    # every raise below this line was reaching for THIS tag, and a refusal that can name
    # the tag it was refused for is the difference between "federation is stuck" and
    # "federation is stuck trying to reach v7.5.0".
    if attempt is not None:
        attempt["target"] = target
    before = deployed_tag(system, entry)

    if before == target and not rollback and not drill:
        # A DRILL DELIBERATELY REDEPLOYS THE TAG ALREADY RUNNING, so it must not stop here.
        # That is the whole shape of it: the failure path is exercised against the version
        # production is on, which is what makes the rollback a no-op in VERSION terms and
        # a full execution in every other term.
        #
        # "The tree HOLDS the target" and "the target is what RUNS" are different facts,
        # and short-circuiting on the first is how this runner became unable to observe
        # its own last step. A deploy that ends at `awaiting-cutover` deliberately leaves
        # the unit repoint to the operator — which happens OUTSIDE this program. With a
        # version-only check, the next deploy says "already running, nothing to do", the
        # ledger keeps its frozen `awaiting-cutover`, `--status` replays that snapshot
        # forever, and the D9 sweep gate never sees the evidence it requires. The system
        # is live and every surface reports that it is not. Re-probe instead.
        #
        # The two guards below mirror the deploy path's own cutover branch exactly, and
        # must keep mirroring it: a system whose contract declares an `apply` strategy
        # has no launchd units to point anywhere, and a contract with NO units cannot be
        # "cut over" at all (cutover_state returns False for an empty unit list, which is
        # correct there and would be a permanent false alarm here). Asking the cutover
        # question of a system that has no units is how this fix first broke the
        # redeploy-is-a-no-op case.
        contract = read_contract(system, entry, target)
        applied_kind = bool((contract.get("apply") or {}).get("cmd"))
        cut = ({} if applied_kind or not contract.get("units")
               else cutover_state(system, entry, contract, log))
        was = read_ledger(system).get("status")
        act = None
        if cut and not cut["cut_over"]:
            # THE BRANCH THAT MATTERS FOR AN ALREADY-STRANDED SYSTEM. A real member's tree
            # once held a tag for a long stretch, so `before == target` and a sweep arrives
            # HERE, never on the forward path below. An auto-cutover wired only into the
            # forward path would leave the one system it was built for exactly where it
            # was, because there is no new tag coming to carry it down the other road.
            cut, act = maybe_auto_cutover(system, entry, contract, cut, target,
                                          log, dry_run)
        if cut and not cut["cut_over"]:
            say(f"{system}: {target} is on disk but NOT cut over — "
                f"{len(cut['pending'])} unit(s) still run elsewhere.")
            for p in cut["pending"]:
                say(f"  {p['unit']} runs in {p['runs_in']}")
            for u in cut["unloaded"]:
                say(f"  {u} is not loaded")
            for r in (act or {}).get("refused", []):
                say(f"  automatic cutover refused for {r['unit']}: {r['reason']}")
            if not dry_run:
                write_ledger(system, checked_at=_now(), current=target, cutover=cut,
                             cutover_event=cutover_event(target, cut, act),
                             **_decided("awaiting-cutover", target),
                             **({"cutover_action": act} if act else {}))
            return 1
        if cut and (was == "awaiting-cutover" or (act or {}).get("acted")):
            # The first moment this runner can see that the cutover happened. Verify NOW:
            # the deploy skipped verification precisely because nothing was executing the
            # new code yet, so `deployed` has never been an honest status for this version.
            verify = run_gate(system, entry, contract.get("verify"), "verify", log, dry_run)
            failed = verify.get("declared") and verify.get("ok") is False
            if not dry_run:
                write_ledger(system, checked_at=_now(), current=target, cutover=cut,
                             verify=verify,
                             **_decided("verify-failed" if failed else "deployed", target),
                             cutover_event=cutover_event(target, cut, act),
                             **({"cutover_action": act} if act else {}))
            say(f"{system}: CUTOVER "
                + ("PERFORMED" if (act or {}).get("acted") else "CONFIRMED")
                + f" — {target} is now the code that runs"
                + (", but VERIFY FAILED." if failed else " and it verifies."))
            return 1 if failed else 0
        # AN APPLY-STRATEGY MEMBER GETS THE EQUIVALENT OF THE CUTOVER RE-PROBE (WI-0411).
        #
        # The branch above re-probes a UNIT-based member, because "the tree holds the
        # target" and "the target is what RUNS" are different facts. An apply-strategy
        # member is excluded from that re-probe by `applied_kind` at the top of this branch
        # — correctly, since it has no units to point anywhere — and until now nothing was
        # put in its place. So the fix landed for the shape that has units and the exposed
        # shape was the one that does not.
        #
        # The fact a receipt adds is the one the tree cannot carry: the tree says which tag
        # is ON DISK, and `apply_receipt` says which tag was actually APPLIED on this
        # machine. A real member is the live instance — its tree has held its tag since a
        # hand-run, so every sweep since has arrived here and returned 0
        # having executed nothing, and the member reads as current with its apply never
        # having run under the runner at all.
        # DOES THE LEDGER ACTUALLY VOUCH FOR THIS TAG? (WI-0416)
        #
        # Asked BEFORE the apply re-probe below, and before the "nothing to do" tail,
        # because the answer decides between doing one step and doing all of them.
        #
        # THE SEQUENCE THIS EXISTS TO STOP, in three moves. One sweep refuses a tag at the
        # state gate; `checkout` precedes `seed_state`, so the refusal leaves that tag ON
        # THE TREE with nothing deployed. The next sweep asks `before == target` — a
        # question about the TREE — finds it true, and reports "already running — nothing
        # to do", writing the new `current` beside a `status=deployed` decided for an
        # older tag. A green verdict for a release that refused, and the member is then
        # unreachable by any later sweep for the same reason, forever.
        #
        # So the tree is necessary and not sufficient. `status_tag` is the ledger saying
        # which tag its verdict is ABOUT, and only that makes "already running" a claim
        # rather than an observation about a directory.
        if not _status_vouches_for(system, target):
            say(f"{system}: {target} is on the tree, but the ledger does not vouch for it "
                f"— {_why_unvouched(system, target)}. Deploying it properly.")
            # Deliberately FALLING THROUGH to the full forward path rather than running
            # apply alone: a tag that got onto the tree by a refusal may have missed
            # seeding, deps, smoke and verify too, and the refusal that put it there told
            # us only where it stopped, not what it had managed first.
            pass
        else:
            # THE LEDGER VOUCHES. Everything below this line is the pre-WI-0416 behaviour,
            # unchanged, and it must stay reachable: the apply re-probe and the
            # nothing-to-do tail are both correct once the verdict is known to be about
            # this tag.
            if applied_kind and not rerun_apply_refused(contract):
                receipt = read_ledger(system).get("apply_receipt") or {}
                if receipt.get("tag") != target:
                    why = ("the receipt on file is for " + receipt["tag"]
                           if receipt.get("tag") else "there is no apply receipt")
                    say(f"{system}: {target} is on disk but its apply has not run here — "
                        f"{why}. Running it now.")
                    applied = run_apply(system, entry, contract, log, dry_run)
                    if not dry_run:
                        write_ledger(system, checked_at=_now(), current=target,
                                     cutover=cut, apply=applied,
                                     apply_receipt=_apply_receipt(target))
                    return 0
            say(f"{system}: already running {target} — nothing to do.")
            if not dry_run:
                write_ledger(system, checked_at=_now(), current=target, cutover=cut)
            return 0

    say(f"{system}: {before or '(nothing deployed)'} → {target}"
        f"{'  [dry-run]' if dry_run else ''}")

    contract = read_contract(system, entry, target)
    refuse_self_restart(system, entry, contract, target)
    # BEFORE THE CHECKOUT, and the placement is the whole value: a verb this machine
    # cannot resolve fails the step that needs it whatever we do, and the only question
    # left is whether it fails with the tree still on the running version or half-way
    # through a deploy (WI-0395).
    refuse_unresolvable_verbs(system, contract, log)

    # ---- put the new code in place, WITHOUT switching production ----
    checkout(system, entry, target, log, dry_run)
    state_notes: list = []
    seed_state(system, entry, contract, log, dry_run, notes=state_notes)
    sync_deps(system, entry, contract, log, dry_run)

    smoke = run_gate(system, entry, contract.get("smoke"), "smoke", log, dry_run)
    if smoke.get("declared") and smoke.get("ok") is False:
        # NOTHING has been restarted. The old version is still serving, so this costs a
        # file-level undo and no downtime at all.
        log("smoke FAILED — putting the tree back; nothing was restarted")
        if before and not dry_run:
            try:
                checkout(system, entry, before, log, dry_run)
                sync_deps(system, entry, read_contract(system, entry, before), log, dry_run)
            except DeployError as e:
                raise RollbackFailed(
                    f"{system}: smoke failed on {target} AND restoring {before} failed "
                    f"— {e}") from e
        # DECIDED FOR `target`, NOT FOR `before`, and the distinction is the item's whole
        # point: this verdict is about the version that was REFUSED, while `current` goes
        # back to naming the one still running. Binding it to `before` would make the
        # smoke failure read as a verdict on the healthy version it just protected.
        write_ledger(system, attempted=target, current=before, failed_at=_now(),
                     trigger=trigger, smoke=smoke, **_decided("smoke-failed", target))
        escalate(system, f"{system}: deploy of {target} refused at the smoke gate",
                 f"The tree was returned to `{before or '(nothing)'}` and **no unit was "
                 f"restarted** — the previously running version was never interrupted.\n\n"
                 f"```\n{smoke.get('detail', '')}\n```\n", log)
        say(f"{system}: REFUSED — {target} failed its smoke check. "
            f"{before or 'nothing'} is still what runs.")
        return 1

    # ---- switch production ----
    applied = run_apply(system, entry, contract, log, dry_run)

    cut = cutover_state(system, entry, contract, log) if not applied.get("declared") else {}
    act = None
    if cut and contract.get("units") and not cut["cut_over"]:
        cut, act = maybe_auto_cutover(system, entry, contract, cut, target,
                                          log, dry_run)
    if cut and contract.get("units") and not cut["cut_over"]:
        # Deployed, honestly not live. Not a failure — a named state, with the reason.
        for p in cut["pending"]:
            log(f"cutover: {p['unit']} still runs in {p['runs_in']} — not restarted")
        for u in cut["unloaded"]:
            log(f"cutover: {u} is not loaded on this machine — not restarted")
        for r in (act or {}).get("refused", []):
            log(f"cutover: automatic cutover refused for {r['unit']} — {r['reason']}")
        if dry_run:
            # A dry run that writes the ledger is not a dry run. This branch returns
            # before the shared dry-run exit below, so it has to say so for itself —
            # which is exactly how it shipped writing a real ledger entry on the first
            # rehearsal, and why the early return now carries its own guard.
            say(f"{system}: dry-run complete — would deploy {target} to "
                f"{work_dir(system, entry)} and STOP THERE: its unit(s) still run "
                f"elsewhere, so nothing would be restarted.")
            return 0
        write_ledger(system, state_notes=state_notes,
                     **_decided("awaiting-cutover", target),
                     current=target, previous=before,
                     deployed_at=_now(), trigger=trigger, smoke=smoke, cutover=cut,
                     verify={"declared": bool(contract.get("verify")), "skipped":
                             "units do not point at the deploy tree yet"},
                     cutover_event=cutover_event(target, cut, act),
                     **({"cutover_action": act} if act else {}))
        say(f"{system}: DEPLOYED {target} to {work_dir(system, entry)} — but AWAITING "
            f"CUTOVER: its unit(s) still run elsewhere, so {target} is on disk and not "
            f"yet the code that executes.")
        return 0

    units = [] if applied.get("declared") else restart_units(system, entry, contract, log,
                                                             dry_run)

    settle = (contract.get("verify") or {}).get("settle_seconds", 3)
    if settle and not dry_run:
        time.sleep(settle)
    if drill and not dry_run:
        # THE DRILL'S ONE FICTION, TOLD HERE AND NOWHERE ELSE. Everything before this line
        # really happened — the tag was checked out, state seeded, deps synced, the smoke
        # gate RUN, the units really restarted — and everything after it is the untouched
        # production failure path. Injecting at the verify gate rather than the smoke gate
        # is not a preference: a smoke failure returns at `smoke-failed` and never reaches
        # `rollback_verify`, which is the exact key ADR-0103 D9's evidence test looks for.
        # A smoke-injected drill would run, report success, and leave the gate shut.
        log("verify: FORCED TO FAIL — this is a --drill, not a fault")
        verify = {"declared": True, "ok": False, "exit": None,
                  "detail": "forced by --drill. The verify gate was not run; everything "
                            "after this point is the real rollback path."}
    else:
        verify = run_gate(system, entry, contract.get("verify"), "verify", log, dry_run)

    if verify.get("declared") and verify.get("ok") is False:
        log("verify FAILED after restart — rolling back")
        if not before:
            write_ledger(system, current=target, failed_at=_now(), trigger=trigger,
                         verify=verify,
                         **_decided("verify-failed-no-rollback", target))
            escalate(system, f"{system}: {target} failed verification and there is no "
                             f"previous version to roll back to",
                     f"This was a first deploy, so there is no earlier tag. The system is "
                     f"running `{target}`, which did **not** pass verification.\n\n"
                     f"```\n{verify.get('detail', '')}\n```\n", log)
            say(f"{system}: FAILED — {target} is running but unverified, and there is "
                f"no previous version to return to.")
            return 1
        try:
            checkout(system, entry, before, log, dry_run)
            prev_contract = read_contract(system, entry, before)
            sync_deps(system, entry, prev_contract, log, dry_run)
            # RESTORING production mirrors SWITCHING it, and for the same reason: the
            # verb that makes a tag the running code is contract-shaped. An apply-strategy
            # contract (ADR-0103 D8 shape 2) has no units at all — its tag reaches
            # production on another host, through `apply`. A rollback that only restarted
            # units therefore put the FILES back and left that host running the bad tag;
            # the previous tag's verify then failed against it and the run ended
            # `RollbackFailed`, with a correct ledger and a correct escalation sitting on
            # top of a restoration that never happened. So this fork must keep mirroring
            # the forward one above: apply when declared, units otherwise.
            rolled_back = run_apply(system, entry, prev_contract, log, dry_run)
            if not rolled_back.get("declared"):
                restart_units(system, entry, prev_contract, log, dry_run)
            if (prev_contract.get("verify") or {}).get("settle_seconds", 3):
                time.sleep(prev_contract["verify"].get("settle_seconds", 3))
            # CONFIRM THE OLD VERSION IS HEALTHY before saying "rolled back". A rollback
            # whose result was never verified is the same class of claim as an
            # unverified backup.
            back = run_gate(system, entry, prev_contract.get("verify"), "verify(rollback)",
                            log, dry_run)
        except DeployError as e:
            raise RollbackFailed(f"{system}: {target} failed verification AND the "
                                 f"rollback to {before} failed — {e}") from e
        if back.get("declared") and back.get("ok") is False:
            raise RollbackFailed(
                f"{system}: rolled back to {before} but {before} does not verify either "
                f"— {back.get('detail', '')}")
        # Bound to `target` — the tag whose verification failed — while `current` returns
        # to `before`. Same reasoning as the smoke-failed write above.
        write_ledger(system, attempted=target, current=before,
                     failed_at=_now(), trigger=trigger, verify=verify,
                     rollback_apply=rolled_back, rollback_verify=back,
                     **_decided("rolled-back", target))
        mark = "DRILL — " if drill else ""
        escalate(system, f"{mark}{system}: {target} failed verification and was rolled "
                         f"back to {before}",
                 (f"**This was a rehearsal, not an outage** (ADR-0103 D9). The verify gate "
                  f"was forced to fail so the rollback path would execute for real.\n\n"
                  if drill else "") +
                 f"`{before}` is running again and **its own verification passed**.\n\n"
                 f"Failure from `{target}`:\n\n```\n{verify.get('detail', '')}\n```\n", log)
        say(f"{system}: {'DRILL ROLLED BACK' if drill else 'ROLLED BACK'} — {target} "
            f"failed verification; {before} is running and verified.")
        return 1

    if dry_run:
        say(f"{system}: dry-run complete — would be running {target}.")
        return 0

    write_ledger(system, state_notes=state_notes,
                 **_decided("deployed", target),
                 current=target, previous=before,
                 deployed_at=_now(), trigger=trigger, smoke=smoke, verify=verify,
                 units=units, apply=applied,
                 # WI-0411. The forward path is where a receipt is EARNED — this is the
                 # branch that actually called `run_apply`. Written only when the contract
                 # declared an apply, so a units-based member never grows the key and a
                 # later reader cannot mistake its absence for a missed apply.
                 **({"apply_receipt": _apply_receipt(target)}
                    if applied.get("declared") else {}),
                 **({"cutover_action": act, "cutover": cut,
                     "cutover_event": cutover_event(target, cut, act)} if act else {}))
    say(f"{system}: DEPLOYED {target}"
        + (f" (was {before})" if before else " (first deploy)"))
    return 0


# ── status and sweep ─────────────────────────────────────────────────────────────

def anything_cut_over() -> bool:
    """Has ANY system on this machine reached the deployed state? (ledger-read, no network)

    `awaiting-cutover` is its own recorded status, so `deployed` is exactly the ledger's
    word for "this system's units are running out of its deploy tree". One such row is
    enough: the guard below is about whether this MACHINE has adopted the sealed model, not
    about which system did it first."""
    try:
        rows = sorted(ledger_dir().glob("*.json"))
    except OSError:
        return False
    for row in rows:
        try:
            if json.loads(row.read_text()).get("status") == "deployed":
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def refuse_unsealed_process_root() -> None:
    """REFUSE to run as a scheduled job out of a checkout somebody develops in (R4b).

    THE FAILURE THIS FORBIDS, which is the one the whole wave is about. The federation's
    units pointed at the live trunk checkout -- the same one development advances -- and
    their own jobs committed receipts and queued mail into it and then tried to push from
    a checkout far behind its remote, stranding commits that had to be cleared by hand.
    Sealing the tree fixes it; nothing
    but a guard stops a future install script pointing a unit back at a working clone,
    because that is what every `install-*.sh` in this repo does by default -- it resolves
    `FED_ROOT` to the checkout it was run from.

    NARROWED, DELIBERATELY, to a job running UNDER LAUNCHD. The ruling says "refuses to
    start from a checkout that has local commits or a symbolic branch head once any system
    has been cut over", and taken literally that also refuses `python3 deploy/runner.py
    example-app` typed by a person in a trunk clone, on any machine that has ever completed one
    deploy. That is not the hazard -- a human at a terminal is watching the output and is
    not a scheduled writer -- and a guard that fires on correct use is a guard somebody
    deletes. `running_under_unit()` separates the two exactly: it is non-empty only when
    launchd spawned us, which is precisely the unattended writer whose strand nobody sees
    for hours.

    Raises DeployError, which leaves the previous version intact and running -- the refusal
    costs one sweep, and the sweep it costs is one that should not have existed."""
    unit = running_under_unit()
    if not unit:
        return
    if channel.is_deploy_tree(REPO_ROOT):
        return
    if not anything_cut_over():
        # Before the first cutover this IS the supported arrangement, and refusing here
        # would brick the very sweep that performs the cutover.
        return
    ahead = ""
    r = run(["git", "-C", str(REPO_ROOT), "rev-list", "--count", "@{u}..HEAD"], timeout=30)
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        ahead = f" and is {r.stdout.strip()} commit(s) ahead of its upstream"
    raise DeployError(
        f"REFUSED to run under {unit}: this runner is executing from {REPO_ROOT}, which is "
        f"on a branch{ahead} — a checkout someone develops in, not a sealed deploy tree. "
        f"A system on this machine has already cut over, so the sealed model is in force "
        f"and a scheduled job writing into a working clone is the stranded-commit class "
        f"this arrangement was built to end (WI-0360, ADR-0135). Point the unit's "
        f"WorkingDirectory at the deploy tree; nothing was deployed.")


def sweep_order(reg: dict) -> list[str]:
    """The order the sweep deploys in: alphabetical, with THE SELF SYSTEM FORCED LAST.

    A registry row may declare `"self": true`, meaning *this row is the system the runner
    is made of*. Deploying it checks a new tag out over the very tree this process is
    executing from, so every step after it runs against source that has been swapped
    underneath — lazily-imported modules, rewritten bytecode, and any subprocess the
    runner spawns all come from code this run never read. Ordering it last empties the
    SWEEP's remaining work, which is what this function can promise; the self deploy's own
    remaining steps still run in a process whose tree was just replaced, and that residue
    belongs to the cutover decision rather than to the order. It is why the ordering is
    derived
    from a DECLARED FLAG rather than from the system's name. An alphabetical sweep is not
    an order guarantee: wherever a self system happens to sort, an unguarded sweep that
    reaches it early overwrites its own tree and then runs the remaining deploys against
    source swapped underneath it, and any placement the alphabet gives is an accident the
    next registered system can take away silently.

    Two selves is a contradiction, not a preference — a runner is made of one tree — so
    this refuses rather than picking one and being right half the time."""
    systems = reg.get("systems", {})
    # A malformed flag must be LOUD. There is no registry schema — `load_registry` is a
    # bare `json.loads` — so `"self": 1`, `"self": "true"` or `"Self": true` would all
    # read as *not self* (`1 is True` is False), which silently restores the pre-WI-0224
    # order AND disarms the restart guard. Failing open here fails toward the defect.
    for name, e in systems.items():
        if not isinstance(e, dict):
            continue
        for key in e:
            if key != "self" and key.lower() == "self":
                raise DeployError(
                    f"registry row {name!r} has key {key!r}. The self flag is spelled "
                    f"'self', lowercase; a near-miss reads as not-self and silently "
                    f"removes this runner's protection against overwriting itself.")
        if "self" in e and not isinstance(e["self"], bool):
            raise DeployError(
                f"registry row {name!r} sets self to {e['self']!r}. It must be a JSON "
                f"boolean: anything else reads as not-self and silently removes this "
                f"runner's protection against overwriting itself.")
    selves = sorted(n for n, e in systems.items()
                    if isinstance(e, dict) and e.get("self") is True)
    if len(selves) > 1:
        raise DeployError(
            f"registry declares {len(selves)} systems as 'self' ({', '.join(selves)}). "
            f"The runner is made of exactly one tree, so at most one row can name the "
            f"tree a deploy would overwrite. Refusing to guess which.")
    return sorted(n for n in systems if n not in selves) + selves


def status(system: Optional[str] = None, fetch_only: bool = False) -> int:
    reg = load_registry()
    order = sweep_order(reg)   # validates the registry on this surface too
    names = [system] if system else order
    if system and system not in reg.get("systems", {}):
        print(f"{system}: NOT REGISTERED — no deploy contract is declared for it here. "
              f"That is not the same as 'nothing to deploy'.")
        return 1
    print(f"{'SYSTEM':<18} {'RUNNING':<12} {'AVAILABLE':<12} STATE")
    rc = 0
    for name in names:
        entry = reg["systems"][name]
        led = read_ledger(name)
        tree = deploy_tree(name)
        if not (tree / ".git").exists():
            print(f"{name:<18} {'-':<12} {'-':<12} never deployed on this machine")
            continue
        tag = deployed_tag(name, entry)
        # "the tree exists" and "something was deployed into it" are different facts, and
        # a clone made by a dry run satisfies only the first. Calling that an "untagged
        # HEAD" invents a third thing that is not happening.
        running = tag or "not deployed"
        # AVAILABLE is a claim about ORIGIN, so it needs origin. Reading the deploy tree's
        # local tags answers "what had I fetched last time", which is a different question
        # wearing the same words — a release cut this afternoon would read as absent and
        # the row would say "up to date". Network is fine here: this is an explicit verb,
        # not the session-start path, where a probe is deliberately not allowed.
        fresh = not fetch_only and git(["fetch", "--tags", "--prune", "--force", "origin"],
                                       tree, timeout=120).returncode == 0
        available = newest_tag(tree) or "(no tags)"
        state = led.get("status", "unknown" if tag else "tree cloned, nothing deployed")
        if not fresh:
            # Could-not-check is its own answer. Silently printing a stale number as
            # current is how a status view lies without ever being wrong on purpose.
            available += " (as of last fetch — origin unreachable)"
        if led.get("_unreadable"):
            state = "LEDGER UNREADABLE — running version read from the tree"
        elif running != available and available != "(no tags)":
            state = f"{state} — {available} is available"
        if state.startswith(("smoke-failed", "rolled-back", "verify-failed",
                             "awaiting-cutover")):
            # awaiting-cutover is fine for an hour and a defect after a week: the tree
            # holds a version nothing executes, which is the most misreadable state this
            # system has. The fleet view is where that gets to be loud.
            rc = 1
        print(f"{name:<18} {running:<12} {available:<12} {state}")

    # A RECORD NOBODY CAN READ IS NOT A RECORD. The canary writes a verdict per system and
    # this is the only surface that prints it — without this block the answer to "has
    # anyone tried v2.0.0-rc.1 here?" would live in a JSON file under a directory nobody
    # has a reason to look in. Printed BELOW the table rather than as a column, because a
    # canary result is about a tag that is deliberately NOT running and putting it in the
    # RUNNING/AVAILABLE grid would invite exactly that reading.
    canaries = [(n, read_canary_ledger(n)) for n in names]
    canaries = [(n, c) for n, c in canaries if c]
    if canaries:
        print()
        for name, c in canaries:
            if c.get("_unreadable"):
                print(f"canary  {name:<18} RECORD UNREADABLE — rerun the canary")
                continue
            print(f"canary  {name:<18} {c.get('tag', '?'):<12} "
                  f"{str(c.get('outcome', '?')).upper()} at {c.get('ran_at', '?')} "
                  f"(production held {c.get('production_tag_at_run') or 'nothing'})")
    return rc


def sweep(dry_run: bool = False) -> int:
    """The scheduled reconcile. Deliberately the SAME code path as the on-demand verb — a
    sweep that re-implemented deploy would be a second implementation that can disagree
    with the first, and the one that runs unattended is the worse one to have wrong."""
    refuse_unsealed_process_root()
    reg = load_registry()
    # Provisioning creates external service roots; a sweep never clones mail transport.
    production.resolve(REPO_ROOT)  # invalid production configuration refuses before deploying
    worst = 0
    for name in sweep_order(reg):
        # BEFORE deploying, say what the PREVIOUS sweep's deferred finisher did. It runs
        # after that sweep exits, so this is the only place its result can reach anyone;
        # printing it here is the difference between a receipt and a diary.
        report_self_cutover_log(name, print)
        try:
            rc = deploy(name, trigger="sweep", dry_run=dry_run)
        except DeployError as e:
            print(f"{name}: REFUSED — {e}")
            rc = 1
        except RollbackFailed as e:
            print(f"{name}: CRITICAL — {e}", file=sys.stderr)
            rc = 2
        worst = max(worst, rc)

    # AFTER every deploy, never before: a diagnosis taken at the top of the sweep would
    # describe the state this sweep is about to change, and would then sit in the mailbox
    # as the newest word on a system it predates. Taken here it reports what the machine
    # is actually left running.
    #
    # THE IMPORT IS LOCAL because `deploy/diagnose.py` imports THIS module at its top — it
    # reads the registry, the tree, the contract and the ledger through the runner rather
    # than re-deriving them — so a module-level import here would be a cycle. The same
    # shape `post_result` already uses for `mailqueue`.
    #
    # ITS FAILURE IS NEVER THIS FUNCTION'S. The sweep's job is to deploy; a bundle that
    # could not be collected or mailed must not turn a successful deploy into a failed
    # run, and `worst` is deliberately untouched below.
    try:
        import diagnose
        diagnose.sweep(log=print, dry_run=dry_run)
    except Exception as e:                                           # noqa: BLE001
        print(f"diagnose: the evidence sweep did not run — {e.__class__.__name__}: {e}. "
              f"The deploys above are unaffected; what is lost is this run's report.")
    return worst


# ---- the drill, and the unattended trigger that gates itself on it ------------------

def drill_evidence() -> list:
    """[(system, record)] for every ledger that carries a PASSED rollback drill."""
    found = []
    for f in sorted(ledger_dir().glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        d = rec.get("drill")
        if isinstance(d, dict) and d.get("ok") is True:
            found.append((rec.get("system") or f.stem, d))
    return found


def drillable_system(reg: dict) -> Optional[str]:
    """The first system a drill could legitimately be run against, or None.

    Three conditions, each of them a refusal rather than a preference. The ledger must say
    `deployed` — drilling a system that is `awaiting-cutover` or `smoke-failed` rehearses
    nothing and disturbs a system already in a state someone needs to look at. There must
    be a tag actually checked out, because the drill redeploys the running tag and there
    has to be one. And it must not be the `self` row: a drill restarts the system's units
    twice, and for the row the runner is MADE of those are the runner's own — the hazard
    `refuse_self_restart` exists to refuse, arriving through a different verb."""
    for name in sweep_order(reg):
        entry = reg["systems"][name]
        if not isinstance(entry, dict) or entry.get("self") is True:
            continue
        if read_ledger(name).get("status") != "deployed":
            continue
        if not deployed_tag(name, entry):
            continue
        return name
    return None


def drill(system: Optional[str] = None, dry_run: bool = False) -> int:
    """Prove the rollback path by RUNNING it, against a real system, on this machine.

    ADR-0103 D9 requires a drilled rollback before the sweep is trusted to run unattended,
    and until now the only way to produce one was for a person to break a smoke check on
    purpose on the Runner — which is the routed-through-a-human step the whole deploy path
    exists to delete, and which nobody was ever going to do, so the gate stayed shut and
    the sweep stayed uninstalled. This is that act written as code.

    WHAT IT ACTUALLY DOES. It redeploys the tag the system is ALREADY RUNNING, forces the
    verify gate to fail, and lets the untouched production failure path take over: roll the
    tree back, re-read the previous tag's contract, re-sync deps, restart, settle, and
    re-verify for real. The version it rolls back TO is the version it started FROM, so
    production cannot end this anywhere but where it began — which is what makes a drill
    safe to run unattended on a live box at three in the morning.

    WHAT THAT COSTS, STATED RATHER THAN GLOSSED. Rolling back to the ledger's `previous`
    would be the more faithful rehearsal: it would exercise reading an OLDER tag's
    contract, which same-tag cannot. It was rejected because it leaves production running
    an old release until a restore step succeeds, and that restore is a second thing that
    can fail on a machine with nobody watching — a drill that can cause the outage it was
    written to predict is a worse bug than the unproven claim it set out to retire. So this
    proves the failure path, the restart, the re-verification, the ledger write and the
    escalation, and it does NOT prove that an older tag's contract still parses. That
    second question is what `--rollback` answers, attended, when someone is asking it.

    The ledger is put back EXACTLY as it was found, plus a `drill` block. The drill drives
    it through `rolled-back` on the way, and leaving that behind would tell `--status`, the
    dashboard and the next reader that a system which is running fine had a release
    withdrawn."""
    reg = load_registry()
    if system is None:
        system = drillable_system(reg)
        if system is None:
            raise DeployError(
                "no system is in a state to be drilled: a drill redeploys the tag a "
                "system is already running, so it needs one that the ledger reports as "
                "`deployed`. Deploy something first — the drill is not the first step.")
    entry = registry_entry(system)
    if entry.get("self") is True:
        raise DeployError(
            f"refusing to drill {system!r}: it is the registry's `self` row, so its units "
            f"are this runner's own and a drill restarts them twice mid-run.")
    original = deployed_tag(system, entry)
    if not original:
        raise DeployError(
            f"{system} has no tag checked out in {deploy_tree(system)}, so there is no "
            f"running version to rehearse a rollback from.")
    before = read_ledger(system)
    if before.get("status") != "deployed":
        raise DeployError(
            f"{system} reads {before.get('status') or 'unknown'!r} in the ledger, not "
            f"'deployed'. A drill disturbs a live system twice; it is for proving the "
            f"failure path on a healthy one, not for poking one that is already unwell.")

    contract = read_contract(system, entry, original)
    if not (contract.get("verify") or {}).get("cmd"):
        # REFUSED BEFORE ANYTHING IS TOUCHED, not reported inconclusive afterwards. The
        # drill forces the verify gate to fail; a contract with no verify gate has none to
        # force, so the failure would be invented and — worse — the rollback would then be
        # accepted without anything confirming the restored version is healthy, which is
        # the same class of claim as an unverified backup. Disturbing a live system twice
        # to produce evidence that could not be evidence is the wrong trade at any hour.
        raise DeployError(
            f"{system}'s contract declares no verify gate, so a rollback of it cannot be "
            f"confirmed and a drill cannot produce ADR-0103 D9 evidence. Refusing before "
            f"touching it. The fix is a `verify` block in that system's deploy.json.")

    print(f"DRILL: {system} is running {original}. Redeploying that same tag, forcing its "
          f"verify gate to fail, and letting the real rollback path run. Production ends "
          f"this on {original} either way.")
    try:
        deploy(system, version=original, drill=True, dry_run=dry_run, trigger="drill")
    except RollbackFailed as e:
        # The one outcome that matters most and the one the old gate could never have
        # produced without an outage: the failure path itself is broken. Recorded on the
        # system, not only printed, because the caller is a launchd job nobody is reading.
        replace_ledger(system, {**read_ledger(system),
                                "drill": {"drilled_at": _now(), "tag": original,
                                          "ok": False, "detail": str(e)}})
        raise
    if dry_run:
        print("DRILL (dry run): nothing was deployed, restarted, forced or written.")
        return 0

    after = read_ledger(system)
    back = after.get("rollback_verify") or {}
    if after.get("status") != "rolled-back":
        print(f"DRILL INCONCLUSIVE: {system} ended at {after.get('status')!r} rather than "
              f"'rolled-back' — the forced failure never reached the rollback path.",
              file=sys.stderr)
        return 1
    if back.get("declared") is not True:
        # A ROLLBACK NOBODY CONFIRMED IS NOT EVIDENCE — the runner's own words about the
        # verify-after-rollback gate: the same class of claim as an unverified backup.
        #
        # ONLY THE `declared` HALF IS TESTED, because only it can occur. A rollback verify
        # that RAN AND FAILED raises `RollbackFailed` upstream and never reaches this line;
        # the first cut of this check tested for that too — a clause no input can reach,
        # which a mutation run duly reported as surviving, since no test could tell the
        # difference between the clause being there and being gone.
        #
        # It is NOT redundant with the up-front `verify` guard: that one asks about the
        # contract being DEPLOYED, this one about the contract being rolled back TO. They
        # are the same file only because this drill rolls back to the same tag, which is a
        # property of today's drill rather than of the check.
        why = ("the restored version was never confirmed healthy — its contract declared "
               "no verify gate for the rollback to run")
        print(f"DRILL INCONCLUSIVE: {system} rolled back, but {why}.", file=sys.stderr)
        replace_ledger(system, {**before,
                                "drill": {"drilled_at": _now(), "tag": original,
                                          "ok": False, "detail": why}})
        return 1

    # Put the record back byte-for-byte and hang the evidence off it. `rollback_verify` is
    # kept as a key INSIDE this block deliberately: `install-deploy-sweep.sh` greps the
    # ledger text for that literal string, so a machine still carrying the old installer
    # reads this drill as the evidence it is, without needing to be updated first.
    replace_ledger(system, {**before, "drill": {
        "drilled_at": _now(), "tag": original, "ok": True,
        "how": "redeployed the running tag with the verify gate forced to fail; the "
               "rollback path ran and the restored version verified",
        "rollback_verify": back}})
    print(f"DRILL PASSED: {system} — the verify failure was detected, the rollback ran, "
          f"and {original} verified afterwards. ADR-0103 D9 evidence is on record.")
    return 0


def unattended_refusal() -> str:
    """Why this machine may not deploy unattended, or "" when it may.

    ONE DECLARATION OF THE RESIDENT MACHINE, NOT A SECOND ONE. The first cut of this added
    a `deploy_host` key to `deploy/registry.json` and a private `this_machine()` here, and
    both were already in the repo: `channel.writer_label()` names the machine that does the
    federation's unattended work (`POGA_CHANNEL_WRITER`, default `Runner`) and
    `common.this_machine()` names the machine we are on. Two keys that both say "Runner"
    today are two keys that can disagree tomorrow — the drift [P16] exists to prevent, and
    the precise complaint WI-0217 already records about declaring one fact twice. If the
    deploy host ever has to differ from the channel's writer, that is a split worth making
    deliberately at that moment, not a second key kept ready for it.

    WHY THE GATE IS NEEDED AT ALL, since ADR-0135's guards are tree-shaped rather than
    machine-shaped: the sweep's machine gate used to be INSTALLATION. `install-deploy-sweep.sh`
    was only ever run on the Runner, so the launchd job's existence WAS the gate.
    `curate/mail-poller.py` triggers the sweep now and runs on every machine, so that gate
    is gone and has to come back written down.

    SCOPED TO THE UNATTENDED PATH ONLY, matching `refuse_unsealed_process_root`'s narrowing
    for the same stated reason: a guard that fires on correct use is a guard somebody
    deletes. A person who types `--sweep` or names a system is asking on purpose."""
    here, want = common.this_machine(), channel.writer_label()
    if not here:
        return ("this machine cannot name itself (`scutil --get ComputerName` gave nothing "
                f"the machine map knows), so it cannot know whether it is {want!r}. Not "
                f"knowing where you are standing is a reason to withhold an unattended "
                f"deploy, never to grant one")
    if here != want:
        return f"this machine is {here!r} and the resident machine is {want!r}"
    return ""


def unattended(dry_run: bool = False) -> int:
    """The trigger a scheduled job calls: drill if that has never been proven, then sweep.

    This is the sweep's SECOND trigger, not a second implementation of it — D8's rule holds
    and the deploy path below is the same `deploy()` every other caller uses. What lives
    here is the policy that cannot live in `sweep()`: am I even the machine that deploys,
    and has this machine's failure path ever actually run.

    The machine question has to be asked HERE because of where the trigger moved. It used
    to be answered by installation — `install-deploy-sweep.sh` was run on the Runner and
    nowhere else, so the launchd job's existence WAS the gate. The mail poller that now
    calls this runs on every machine in the fleet on purpose, so that gate is gone and has
    to come back as a declaration — `unattended_refusal`, which reads the resident machine
    the channel already declares rather than adding a second key that says "Runner" too."""
    reg = load_registry()
    why = unattended_refusal()
    if why and not common.this_machine():
        # Cannot name itself. A real problem on a machine that may well BE the resident
        # one, so it is reported as a failure rather than passed over as routine.
        print(f"unattended: {why}. Nothing done.", file=sys.stderr)
        return 1
    if why:
        # The expected answer on most of the fleet, every ten minutes, forever. It is not
        # an error and must never be counted as one.
        print(f"unattended: {why} — nothing to do.")
        return 0

    if not dry_run and not drill_evidence():
        target = drillable_system(reg)
        if target is None:
            # THE AMENDED D9, AND THE PLACE IT IS WEAKER THAN THE ORIGINAL. A machine where
            # nothing has ever deployed has nothing to drill, so requiring the drill first
            # would deadlock permanently — which is the state this whole change was written
            # to end. The sweep is allowed to establish a first deployment; the drill
            # becomes due on the very next pass, and until it passes the sweep runs on a
            # failure path that has still never executed. That window is one pass long and
            # it is recorded rather than hidden.
            print("unattended: no drilled rollback on record and nothing deployed here yet "
                  "to drill against. Sweeping to establish one; the drill is due on the "
                  "next pass (ADR-0103 D9 as amended).")
        else:
            print(f"unattended: no drilled rollback on record — drilling {target} before "
                  f"sweeping (ADR-0103 D9).")
            try:
                rc = drill(target)
            except (DeployError, RollbackFailed) as e:
                rc, detail = 1, str(e)
                print(f"unattended: DRILL FAILED — {detail}", file=sys.stderr)
                escalate(target, f"{target}: the rollback drill FAILED — unattended "
                                 f"deploys are stopped",
                         f"The scheduled deploy sweep drilled the rollback path before "
                         f"trusting itself with it, and the drill did not pass:\n\n"
                         f"```\n{detail}\n```\n\nNo sweep ran. This is ADR-0103 D9 doing "
                         f"exactly what it is for — the failure path is the entire safety "
                         f"argument, and it has now been shown not to work.\n", print)
            if rc != 0:
                # NOT SWEEPING IS THE POINT. An unattended deployer whose recovery path has
                # just been demonstrated broken is the one thing worse than one that was
                # never tested.
                print("unattended: refusing to sweep — the failure path did not pass its "
                      "drill.", file=sys.stderr)
                return 2

    return sweep(dry_run=dry_run)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="deploy", description="Deploy a tagged release onto this machine (ADR-0103).")
    p.add_argument("system", nargs="?", help="System id from deploy/registry.json.")
    p.add_argument("--version", help="Tag to deploy (default: the newest semver tag).")
    p.add_argument("--rollback", action="store_true",
                   help="Return to the ledger's previous version.")
    p.add_argument("--canary", metavar="TAG",
                   help="Exercise TAG in a separate tree and run its smoke check there. "
                        "Restarts nothing, touches no production tree, records a verdict. "
                        "This is the only way to run a release candidate (vX.Y.Z-rc.N) on "
                        "this machine.")
    p.add_argument("--dry-run", action="store_true",
                   help="Say what would happen; touch nothing.")
    p.add_argument("--status", action="store_true", help="What is running where.")
    p.add_argument("--offline", action="store_true",
                   help="--status without contacting origin. AVAILABLE is then labelled "
                        "'as of last fetch' rather than presented as current.")
    p.add_argument("--sweep", action="store_true",
                   help="Reconcile every registered system against its newest tag.")
    p.add_argument("--drill", nargs="?", const="", metavar="SYSTEM",
                   help="Prove the rollback path for real: redeploy the tag a system is "
                        "already running, force its verify gate to fail, and let the "
                        "rollback run. Picks a system itself if none is named.")
    p.add_argument("--unattended", action="store_true",
                   help="The scheduled trigger: drill if that has never been proven on "
                        "this machine, then sweep. Declines unless this machine is the "
                        "one the channel declares resident.")
    p.add_argument("--trigger", default="manual", choices=["manual", "sweep", "drill"])
    # Internal: the detached tail of a self-cutover. Not for hand use — it waits on a pid
    # that only the sweep that spawned it knows.
    p.add_argument("--finish-self-cutover", metavar="SYSTEM", help=argparse.SUPPRESS)
    p.add_argument("--unit", help=argparse.SUPPRESS)
    p.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    try:
        if args.finish_self_cutover:
            if not args.unit or not args.wait_pid:
                p.error("--finish-self-cutover needs --unit and --wait-pid")
            return finish_self_cutover(args.finish_self_cutover, args.unit, args.wait_pid)
        if args.status:
            return status(args.system, fetch_only=args.offline)
        if args.unattended:
            return unattended(dry_run=args.dry_run)
        if args.drill is not None:
            return drill(args.drill or None, dry_run=args.dry_run)
        if args.sweep:
            return sweep(dry_run=args.dry_run)
        if not args.system:
            p.error("a system is required (or --status / --sweep / --drill / "
                    "--unattended)")
        if args.canary:
            # REFUSED RATHER THAN RESOLVED IN SOME ORDER. `--canary --rollback` and
            # `--canary --version` are each a sentence with two subjects, and a program
            # that silently picks one has decided which release runs on somebody's behalf.
            # argparse's mutually-exclusive groups would say this too, but say it as
            # "not allowed with"; these two names deserve the reason.
            if args.rollback:
                p.error("--canary and --rollback are different questions: one asks whether "
                        "a candidate works, the other puts production back. Run them "
                        "separately.")
            if args.version:
                p.error("--canary already names the tag to exercise; --version names the "
                        "tag to deploy. Pass one.")
            return canary(args.system, args.canary, dry_run=args.dry_run)
        return deploy(args.system, version=args.version, rollback=args.rollback,
                      dry_run=args.dry_run, trigger=args.trigger)
    except RollbackFailed as e:
        print(f"CRITICAL: {e}", file=sys.stderr)
        return 2
    except DeployError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
