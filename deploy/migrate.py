#!/usr/bin/env python3
"""Move the running federation onto external state without losing state or pending mail (WI-0365).

THE STARTING STATE THIS PROGRAM ASSUMES, established from host-produced evidence (the
runner's own deploy receipt, mailed over the ADR-0135 channel) rather than from this
repo's prose. A receipt recording outcome **`deployed`** proves the cutover: `deployed`
is not reachable for a contract that declares units unless `cutover_state` read every
unit's live `launchctl` working directory back as being inside the deploy tree, and a
contract that declares its units and no `apply` has no shortcut past that branch. **So
the units already run released code from `~/deploy/federation`.** A claim made from the
development machine that they still run from the trunk clone is not evidence about the
Runner.

SO THE TRANSITION THAT REMAINS IS FROM IN-TREE STATE TO EXTERNAL STATE. The units run
released code with production DORMANT — nothing sets `POGA_FEDERATION_CONFIG` — so every
receipt, queued message, comms note and `.session-state` record they write lands inside a
SEALED, DETACHED deploy tree, where a commit belongs to no branch and the next tag
checkout discards it. That is the failure WI-0360 predicted for this exact moment
("RECEIPTS STOP TRAVELLING AT CUTOVER"). This program is the move from in-tree state to
external state.

**Reachable from the installed version, and there is exactly one door.** The deployed
program's own `--help`, as the host reports it, is the whole CLI surface: `[--version] [--rollback] [--dry-run] [--status] [--offline] [--sweep]
[--trigger {manual,sweep}]`. No `--unattended`, no `--drill`. So the scheduled entrypoint
is `deploy/runner.py --sweep`, fired every 600s by `com.federation.deploy-sweep`. Within
a sweep, `read_contract` validates an incoming tag's contract against
`deploy/contract.schema.json` **read from the RUNNER's own checkout** (runner.py:482),
and that schema sets `additionalProperties: false` — so a NEW contract key added by a new
tag is refused by the installed version, whatever the tag carries. Of the keys it does
know, `smoke` and `verify` are gates and this item forbids disguising an installation as
one. That leaves `apply` (runner.py:1696), which runs between the smoke gate and the
cutover, from the deploy tree, with `verify` still bracketing it. `run_apply` has existed
since the contract did (`ab13beb4`) and is present in v7.3.0 — checked, not assumed.

**`apply` replaces the runner's cutover** (runner.py:2470), which costs nothing here
because the cutover is already done, and this program re-asks `cutover_state` anyway for
the installation where it is not.

**Preparation is explicit and ordered so the refusal comes first.** Everything that reads
the installation happens before anything that changes it; `production.resolve` — the same
authority the units themselves will apply at 03:15 — must accept the configuration before
a single plist is touched. Each phase records a checkpoint before the next begins and
every phase is idempotent, so an interrupted run resumes at the boundary it reached.

**Nothing authored is moved or overwritten.** State is COPIED and verified by digest; the
source tree is left byte-identical. Mail is re-enqueued under a content-derived identity,
so a resumed run finds the message already queued rather than sending it twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import tempfile

RELEASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_ROOT / "curate"))
sys.path.insert(0, str(RELEASE_ROOT / "deploy"))
sys.path.insert(0, str(RELEASE_ROOT))
import production                                                    # noqa: E402
import channel                                                       # noqa: E402
import mailqueue                                                     # noqa: E402
import mailnames                                                     # noqa: E402
import mailtransport                                                 # noqa: E402
import outbox                                                        # noqa: E402
import runner                                                        # noqa: E402

SYSTEM = "federation"
JOURNAL_NAME = "migration.json"
JOURNAL_VERSION = 1

#: Ordered, and the order IS the safety argument. Everything that reads the installation
#: runs before anything that changes it, so a fault in discovery, provisioning or import
#: costs a refusal while the working setup is still the one running. `fence` is the first
#: step that touches this machine's scheduler and it is fifth of seven.
PHASES = ("provision", "inventory", "import-state", "import-mail", "fence", "rebind",
          "repoint")

#: Runtime state the installation holds that the service cannot lose. NAMED, never
#: globbed: `.session-state/` is also the units' log and scratch directory, and the deploy
#: contract deliberately does not seed it. These are the adoption attempt ledgers and
#: quarantine records `deploy/production-state.md` warns "must not be discarded as if they
#: were expendable logs"; everything else in that directory each unit recreates.
DURABLE_STATE = ("adopt-runner.briefs.json", "adopt-runner.status", "adopt-runner.jsonl",
                 "mail-poller.status.json")

#: Machine-local configuration `production.resolve` requires to exist. The first three are
#: how the adopt-runner and reconcile locate member repositories at all after cutover; the
#: deploy contract already declares them `required: true`, so a deploy that reached
#: `deployed` proves the deploy tree holds them.
CONFIG_FILES = ("mailboxes.json", "repo-paths.local", "reconcile-roots.local")

#: The transport's own refs, and the entire point is that they are NOT the ADR-0135
#: channel branch. `curate/channel.py` writes a FULL TREE there — thousands of files,
#: some of them executable — while `mailtransport._validate_tree` refuses an
#: owned ref holding anything but `messages/<sha256>.json` at 100644. Pointing both
#: writers at one ref name is WI-0418: the worker then fails every cycle with "owned ref
#: contains an unauthorized path or mode", its queue growing with nothing published,
#: because the transport is refusing its own ref.
TRANSPORT_OWNED_REF = "refs/heads/runner/messages"
TRANSPORT_INPUT_REFS = ("refs/heads/dev/messages",)

#: DERIVED from the channel's own constant rather than spelled again here, so renaming
#: the channel branch carries the collision check with it instead of leaving a literal
#: that silently stops matching the thing it was written to catch.
CHANNEL_REF = "refs/heads/" + channel.BRANCH

#: Version 2 differs from version 1 in nothing a reader parses — same keys, same types.
#: The bump is a MARKER: a version-2 document has been examined for the channel-branch
#: collision above. Both versions are therefore accepted. Refusing version 1 would make
#: production dormant on every host between this release landing and that host's own
#: migration running, which is a broader outage than the defect being fixed.
SERVICE_CONFIG_SCHEMA = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)


class MigrationError(RuntimeError):
    """The transition cannot proceed. Nothing after the raise has been changed."""


# ── configuration ────────────────────────────────────────────────────────────────

def default_config_path() -> Path:
    """Where this machine keeps the federation's service configuration.

    A CONVENTION, and it has to be one. The file names absolute paths on a single host, so
    it can live neither in the tag (wrong on every other machine — WI-0132) nor be found
    through `POGA_FEDERATION_CONFIG`, which is precisely the variable that is unset until
    this program has run. A fixed default under the user's own config directory is the
    only address a first run can know; `--config` overrides it."""
    return Path(os.environ.get("POGA_FEDERATION_SERVICE_CONFIG",
                               Path.home() / ".config/poga/federation-service.json"))


def qualified_ref(value):
    """A bare branch name as the fully-qualified ref the service configuration stores.

    The same ref is spelled two ways in this system on purpose — `channel.BRANCH` is bare
    (`runner/mail`) and the config key is qualified (`refs/heads/runner/mail`) — so a
    comparison between them has to normalise or it silently never matches."""
    return value if value.startswith("refs/") else "refs/heads/" + value


def names_channel_ref(transport) -> bool:
    """Does this transport block point either of its refs at the channel branch?"""
    if not isinstance(transport, dict):
        return False
    refs = transport.get("input_refs")
    candidates = [transport.get("owned_ref"), *(refs if isinstance(refs, list) else [])]
    return any(isinstance(ref, str) and ref and qualified_ref(ref) == CHANNEL_REF
               for ref in candidates)


def author_config(path: Path, *, code_root: Path, remote: str, log) -> str:
    """Write a service configuration derived from this host, correct one whose transport
    refs name the channel branch, or leave an authored one alone. Returns which it did:
    `"written"`, `"upgraded"` or `"authored"`.

    THE ALTERNATIVE IS AN ERRAND, and it is the errand nobody ran. Requiring a hand-written
    file before the transition works is the manual step this wave exists to delete — and
    when a path map is a precondition and nobody wrote one, a sweep reports
    every seeded path "absent and this machine declares no seed source". So the default is
    derived here and written once. An operator who wants different roots writes the file
    first: an existing file is used as authored and never rewritten.

    EXCEPT ON ONE POINT, ADDED BY WI-0418, and it is the one point an operator cannot be
    left holding. A config naming `CHANNEL_REF` does not express a preference — it
    expresses the defect. The transport's own validator refuses that ref by construction,
    so the configuration is unserviceable whoever wrote it, and the only other way to
    correct it is a hand edit on a production host. That is the thing this program exists
    to abolish, so the migration corrects it and says so."""
    if path.exists():
        return _upgrade_config(path, log)
    state = Path.home() / ".local/state/poga/federation"
    _write_json(path, {
        "//": "GENERATED by deploy/migrate.py (WI-0365). Machine-local: it names absolute "
              "paths on THIS host only and is never committed. Edit it and re-run — an "
              "existing file is used as authored and never rewritten, EXCEPT that transport "
              "refs naming the channel branch are corrected (WI-0418).",
        "schema_version": SERVICE_CONFIG_SCHEMA,
        "code_root": str(code_root),
        "state_root": str(state),
        "config_root": str(state / "config"),
        "transport_root": str(Path.home() / ".local/state/poga/federation-transport.git"),
        "transport": {"remote": remote, "owned_ref": TRANSPORT_OWNED_REF,
                      "input_refs": list(TRANSPORT_INPUT_REFS)},
    })
    log(f"config: wrote a derived service configuration at {path}")
    return "written"


def _upgrade_config(path: Path, log) -> str:
    """Move an existing configuration off the channel branch, or leave it exactly alone.

    NARROW ON PURPOSE. The trigger is not "the refs differ from the defaults" — a host is
    entitled to its own ref names and this must not drag every authored config onto them.
    The trigger is naming `CHANNEL_REF` specifically, which is unserviceable rather than
    unusual. When it fires BOTH refs move to the current pair, because the pair is what
    makes a working transport: correcting the owned ref while leaving an input ref pointed
    at a branch the peer never publishes to buys a worker that publishes into silence.

    An unreadable or unshaped document is left untouched, NOT repaired. `raw_config` reads
    this same file three lines later in `prepare` and refuses it there, with a message
    about the actual fault; guessing at a rewrite here would destroy the evidence for that
    refusal."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"config: {path} exists — using it as authored")
        return "authored"
    if not isinstance(document, dict) or not names_channel_ref(document.get("transport")):
        log(f"config: {path} exists — using it as authored")
        return "authored"
    transport = dict(document["transport"])
    was = transport.get("owned_ref")
    transport["owned_ref"] = TRANSPORT_OWNED_REF
    transport["input_refs"] = list(TRANSPORT_INPUT_REFS)
    document["transport"] = transport
    document["schema_version"] = SERVICE_CONFIG_SCHEMA
    _write_json(path, document)
    log(f"config: upgraded {path} — its transport refs named the channel branch "
        f"{CHANNEL_REF} (owned_ref was {was!r}), which the transport refuses by "
        f"construction. Now {TRANSPORT_OWNED_REF} <- {list(TRANSPORT_INPUT_REFS)}.")
    return "upgraded"


def _write_json(path: Path, document) -> None:
    """Atomic, fsynced, parent-created. Every record here is a checkpoint something else
    resumes from, so a half-written one is a corrupted resume rather than a lost file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def copy_atomic(source, destination) -> None:
    """Copy so that the destination is either absent or complete — never half a file.

    `shutil.copy2` writes in place, and every reader here treats "the destination exists"
    as "it arrived". A crash mid-copy therefore leaves a TRUNCATED file that the resume
    skips: `import_state` records it `already_present` without comparing its digest, and
    `fence` records the digest OF THE TRUNCATION as the byte-for-byte article a rollback
    will bootstrap. Staging beside the destination and renaming makes the partial state
    unobservable, which is the same discipline `_write_json` already uses and the reason
    it uses it."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + destination.name + "-",
                                         dir=destination.parent)
    os.close(handle)
    try:
        shutil.copyfile(source, temporary)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        shutil.copystat(source, temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def raw_config(path: Path) -> dict:
    """The declared configuration, before `production.resolve` gets to judge it.

    Read directly because `resolve` cannot be asked yet: it requires `state_root` and
    `config_root` to exist and to hold three files, which is what provisioning is for."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"service configuration unreadable: {path}: {exc}") from exc
    version = document.get("schema_version") if isinstance(document, dict) else None
    # `type(...) is not int` rather than `!= 1`: `True == 1`, so the old spelling accepted a
    # boolean as a version number.
    if (not isinstance(document, dict) or type(version) is not int
            or version not in SUPPORTED_SCHEMA_VERSIONS):
        allowed = " or ".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)
        raise MigrationError(
            f"service configuration requires schema_version {allowed}: {path}")
    for key in ("code_root", "state_root", "transport_root"):
        value = document.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise MigrationError(
                f"service configuration {key} must be an absolute path: {path}")
    return document


# ── the journal ──────────────────────────────────────────────────────────────────

def journal_path(state_root) -> Path:
    return Path(state_root) / JOURNAL_NAME


def read_journal(state_root) -> dict:
    path = journal_path(state_root)
    if not path.exists():
        return {"schema_version": JOURNAL_VERSION, "system": SYSTEM, "phases": {},
                "completed_at": None}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A journal that cannot be read is worse than no journal: resuming from a guess is
        # how a fenced scheduler gets fenced twice and an imported queue is re-imported
        # against a lost record of what was already there.
        raise MigrationError(f"migration journal unreadable: {path}: {exc}") from exc
    if document.get("schema_version") != JOURNAL_VERSION:
        raise MigrationError(f"migration journal is not schema {JOURNAL_VERSION}: {path}")
    return document


def record(state_root, journal: dict, phase: str, **fields) -> dict:
    """Commit one phase's checkpoint. The next phase does not begin until this returns."""
    journal.setdefault("phases", {})[phase] = {"at": runner._now(), **fields}
    _write_json(journal_path(state_root), journal)
    return journal


def done(journal: dict, phase: str) -> bool:
    return phase in journal.get("phases", {})


# ── discovery (pure read) ────────────────────────────────────────────────────────

def discover(entry: dict, contract: dict, code_root: Path, log) -> dict:
    """What this host is running RIGHT NOW, read from the host rather than from a document.

    Unit working directories come from `launchctl` through `runner.unit_target`, which is
    the one source that cannot be stale — a loaded job keeps the directory it was
    bootstrapped with whatever any file claims. The `poga` target is read the same way,
    from the link itself, which is the "check its existing target" this item asks for
    before anything repoints it."""
    link = runner.cli_link()
    try:
        cli_target = str(link.resolve()) if link.is_symlink() else None
    except OSError:
        cli_target = None
    units = {}
    for unit in contract.get("units", []):
        target = runner.unit_target(unit)
        units[unit] = {
            "working_directory": target,
            "loaded": bool(target),
            "in_release": bool(target) and (target == str(code_root)
                                            or target.startswith(str(code_root) + "/")),
        }
    retiring = runner.retiring_root(SYSTEM, entry, contract)
    found = {
        "units": units,
        "retiring_root": str(retiring) if retiring else None,
        "cli_link": str(link),
        "cli_link_is_symlink": link.is_symlink(),
        "cli_target": cli_target,
        "own_unit": runner.running_under_unit(),
        "already_in_release": all(u["in_release"] for u in units.values()) if units else False,
    }
    log(f"discover: {sum(1 for u in units.values() if u['in_release'])} of {len(units)} "
        f"unit(s) already run from {code_root}; retiring checkout "
        f"{found['retiring_root'] or '(none — the units left it already)'}")
    return found


def config_sources(code_root: Path, retiring, log) -> list:
    """Where the machine-local configuration can be copied from, best first.

    THE ORDER IS EVIDENCE-SHAPED, not a preference. A retiring checkout is the source when
    one still exists, because it is where an operator's own edits live. Once the units
    have left it `runner.retiring_root` correctly answers None — a loaded job outside the
    deploy tree is its whole definition — and the honest second answer is the DEPLOY TREE:
    the contract declares all three files `required: true`, so a deploy that reached
    `deployed` could not have got there without `seed_state` placing them. The state vault
    is third and is the runner's own machine-local mirror, which survives a tree that a
    rollback or a re-clone threw away.

    Every candidate is returned with what it actually holds, so a source that is present
    but empty is visible rather than silently skipped — a declared-but-absent source
    suppressing a real one is the exact failure `seed_state`'s tier fall-through was
    written for."""
    candidates = []
    if retiring:
        candidates.append(("retiring checkout", Path(retiring)))
    candidates.append(("deploy tree", code_root))
    candidates.append(("state vault", runner.state_vault(SYSTEM)))
    rows = []
    for name, root in candidates:
        holds = [f for f in CONFIG_FILES if (root / f).is_file()]
        rows.append({"source": name, "root": str(root), "holds": holds})
        log(f"config: {name} at {root} holds {len(holds)} of {len(CONFIG_FILES)} file(s)")
    return rows


# ── phase 1: provision ───────────────────────────────────────────────────────────

def provision(document: dict, sources: list, log) -> None:
    """Create the external roots and seed the configuration `production.resolve` requires.

    REFUSES BEFORE IT DISABLES ANYTHING. This runs fifth-from-last for a reason: a missing
    member-location file is found here, while the scheduler is untouched and the current
    arrangement is still the one running. That is the acceptance's "fail missing
    configuration before disabling the last working setup", and it is a property of the
    ORDER rather than of any single check."""
    state = Path(document["state_root"])
    config = Path(document.get("config_root", str(state / "config")))
    for directory in (state, config):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in CONFIG_FILES:
        destination = config / name
        if destination.exists():
            continue                    # authored or already seeded; never overwritten
        source = next((Path(row["root"]) / name for row in sources
                       if name in row["holds"]), None)
        if source is None:
            raise MigrationError(
                f"no source on this machine holds {name}, and production requires it: "
                f"after cutover this file is how the adopt-runner and reconcile locate "
                f"member repositories at all. Looked in "
                f"{', '.join(row['root'] for row in sources)}. Nothing was changed.")
        copy_atomic(source, destination)
        log(f"provision: seeded {name} from {source.parent}")


#: The one seeded file the RELEASE owns. `repo-paths.local` and `reconcile-roots.local` name
#: paths on this machine and stay seed-once; `mailboxes.json` is tracked, carries no machine
#: path (its own `//scope`), and is the roster the worker routes by. Seeding it once froze
#: every host on the roster of its first install: one machine's copy was byte-identical to
#: an older release, so a later roster fix and a member's own repair never reached it and a
#: batch of messages sat unroutable (WI-0428).
RELEASE_CONFIG_FILES = ("mailboxes.json",)


def refresh_release_config(config_root: Path, release_root: Path, log) -> list:
    """Make each release-owned config file equal the running release's copy. Returns the
    names it rewrote. A release that lacks the file leaves the seeded one alone rather than
    deleting the only roster this host has."""
    refreshed = []
    for name in RELEASE_CONFIG_FILES:
        source, destination = Path(release_root) / name, Path(config_root) / name
        if not source.is_file():
            log(f"refresh: {source} is absent — kept the seeded {name}")
            continue
        if destination.is_file() and digest(destination) == digest(source):
            continue
        was = digest(destination)[:12] if destination.is_file() else "absent"
        copy_atomic(source, destination)
        refreshed.append(name)
        log(f"refresh: {name} now matches the release at {release_root} "
            f"(was {was}, now {digest(destination)[:12]})")
    return refreshed


def recipient_ids(mailboxes: dict, systems) -> set:
    """Every destination this federation can address mail to: each roster row's
    `architect_id`, plus `<system>-arch` for each deploy-registry system — the id
    `runner.recipient_architect` derives when a roster lacks the row, which is how a batch
    of messages for one member's own named orchestrator agent were once queued at the
    derived convention address instead of the roster's declared one.

    A row's `aliases` are addresses too: mail queued under a member's old id keeps that
    destination for life, and `maildelivery._apply_ack` looks the acknowledgment's ref up
    by it, so an alias with no ref here is an ack refused forever."""
    members = mailboxes.get("members", {}) if isinstance(mailboxes, dict) else {}
    ids = {row["architect_id"].strip() for row in members.values()
           if isinstance(row, dict) and isinstance(row.get("architect_id"), str)
           and row["architect_id"].strip()}
    ids.update(alias.strip() for row in members.values()
               if isinstance(row, dict) and isinstance(row.get("aliases"), list)
               for alias in row["aliases"] if isinstance(alias, str) and alias.strip())
    ids.update(f"{system}-arch" for system in systems)
    return ids


def derive_recipient_refs(document: dict, mailboxes: dict, systems) -> dict:
    """`recipient_refs` for a service configuration: every recipient id mapped to the ref its
    acknowledgments arrive on, which is the host's single input ref — its peer's owned ref.
    Empty when that ref is ambiguous (none, or several), because guessing which peer
    delivers a recipient is the hand edit this replaces (WI-0429)."""
    transport = document.get("transport") if isinstance(document, dict) else None
    refs = transport.get("input_refs") if isinstance(transport, dict) else None
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str):
        return {}
    return {rid: qualified_ref(refs[0]) for rid in sorted(recipient_ids(mailboxes, systems))}


def ensure_recipient_refs(config_path: Path, mailboxes_path: Path, systems, log) -> list:
    """Add every derived `recipient_refs` entry the configuration lacks. Returns the ids added.

    ADDITIVE ONLY. A missing entry is not a preference — `maildelivery._apply_ack` refuses
    every acknowledgment for that recipient, so the Runner's pending count can only grow —
    but an entry that is present was authored, and is kept whatever it says. An unreadable
    roster derives nothing and changes nothing; `production.resolve` judges the rest."""
    try:
        document = json.loads(Path(config_path).read_text(encoding="utf-8"))
        mailboxes = json.loads(Path(mailboxes_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"recipient_refs: not derived — {exc}")
        return []
    derived = derive_recipient_refs(document, mailboxes, systems)
    transport = document.get("transport")
    if not derived or not isinstance(transport, dict):
        log("recipient_refs: not derived — the configuration names no single input ref")
        return []
    authored = transport.get("recipient_refs")
    authored = dict(authored) if isinstance(authored, dict) else {}
    added = sorted(set(derived) - set(authored))
    if not added:
        return []
    transport["recipient_refs"] = {**derived, **authored}
    _write_json(Path(config_path), document)
    log(f"recipient_refs: added {len(added)} derived entr{'y' if len(added) == 1 else 'ies'} "
        f"-> {sorted(set(derived.values()))[0]}: {', '.join(added)}")
    return added


def check_recipient_refs(config_path: Path, mailboxes_path: Path, systems) -> dict:
    """Read-only: the derived map beside the authored one. `exact` is the WI-0429 proof that a
    fresh migrate reproduces a hand-written map."""
    document = raw_config(config_path)
    mailboxes = json.loads(Path(mailboxes_path).read_text(encoding="utf-8"))
    derived = derive_recipient_refs(document, mailboxes, systems)
    authored = (document.get("transport") or {}).get("recipient_refs") or {}
    return {"exact": derived == authored, "derived": derived, "authored": authored,
            "only_derived": sorted(set(derived) - set(authored)),
            "only_authored": sorted(set(authored) - set(derived)),
            "differing": sorted(k for k in set(derived) & set(authored)
                                if derived[k] != qualified_ref(authored[k]))}


def claim_transport(roots, log) -> dict:
    """Initialise or validate the transport repository, and take its lock while doing it.

    "Validate all member-location config and external root ownership BEFORE enabling
    dependent jobs." The member-location half is `provision` plus `production.resolve`;
    this is the ownership half. `ensure_repository` writes an ownership marker before it
    runs `git init`, so an interrupted initialisation is restartable, and it REFUSES a
    repository whose marker names a different remote or owned ref rather than adopting it.

    Under the lock for a second reason that is not about ownership: it is the same lock a
    running worker holds, so taking it here means the migration cannot rebind the units
    out from underneath a cycle that is mid-publish. A worker already running is reported
    and refused, not waited on — a migration that blocked here would sit inside an `apply`
    until the deploy timed out."""
    try:
        with mailtransport.transport_lock(roots):
            if mailtransport.claim_repository(roots):
                log(f"provision: transport ownership marker moved off {CHANNEL_REF}")
    except mailtransport.TransportBusy as exc:
        raise MigrationError(
            f"a mail worker is running and holds the transport lock: {exc}. Nothing was "
            f"changed; the next sweep retries.") from exc
    except mailtransport.TransportError as exc:
        raise MigrationError(f"the transport root is not usable: {exc}") from exc
    log(f"provision: transport repository at {roots.transport_root} is ours")
    return {"transport_root": str(roots.transport_root)}


def reconcile_transport_ownership(roots, log) -> bool:
    """Move the ownership marker off the channel branch on EVERY preparation (WI-0425).

    NOT A PHASE, AND THAT IS THE WHOLE POINT. The repoint first lived only in
    `claim_transport`, inside `provision`, and a phase runs once per host: a host that
    has already recorded `provision` skips the repoint when a later release moves its
    configuration off `refs/heads/runner/mail`, and the worker then refuses
    "transport ownership differs from configuration" on every cycle. A change the
    CONFIGURATION forces has to be reconciled wherever the configuration is re-read, which
    is here — beside `author_config`'s upgrade, before any gate. A phase checkpoint answers
    "did this one-time step happen?", never "does disk still agree with the config?".

    Same narrow predicate as the worker's own `claim_repository`: only a marker naming
    exactly the channel branch moves; any other mismatch is left for the refusal.

    A worker holding the lock is NOT a refusal here. It runs `claim_repository` under that
    same lock, which applies this same repoint, so the change still lands — just from the
    other side. Refusing the preparation over it would hold a deploy for a race that has
    no loser."""
    try:
        with mailtransport.transport_lock(roots):
            moved = mailtransport.repoint_ownership(roots, from_ref=CHANNEL_REF)
    except mailtransport.TransportBusy:
        log("config: a mail worker holds the transport lock; it repoints the ownership "
            "marker itself on its next cycle")
        return False
    except mailtransport.TransportError as exc:
        raise MigrationError(f"the transport root is not usable: {exc}") from exc
    if moved:
        log(f"config: transport ownership marker moved off {CHANNEL_REF} onto "
            f"{mailtransport.owned_ref(roots)}")
    return moved


def resolve_or_refuse(config_path: Path, code_root: Path):
    """`production.resolve`, with its refusal re-raised as this program's own.

    Asked through the same environment key the units read, so what is validated here is
    exactly what they will validate at 03:15 — not a second opinion about it."""
    try:
        roots = production.resolve(code_root, {production.CONFIG_ENV: str(config_path)})
    except production.ConfigurationError as exc:
        raise MigrationError(f"the service configuration is not usable: {exc}") from exc
    if roots is None:                   # unreachable: the key is always supplied above
        raise MigrationError("production was not selected by the supplied configuration")
    return roots


# ── phase 2: inventory (pure read) ───────────────────────────────────────────────

def digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(roots_of_state: list, code_root: Path, log) -> dict:
    """Everything that must survive, with its digest, recorded before anything is copied.

    Taken BEFORE the copy so the check afterwards compares against a reading made while
    the source was still the only copy. An inventory taken afterwards agrees with itself
    and proves nothing."""
    state_files, mail, comms = [], [], []
    seen = set()
    for root in roots_of_state:
        session_state = root / ".session-state"
        for name in DURABLE_STATE:
            path = session_state / name
            if path.is_file() and name not in seen:
                seen.add(name)
                state_files.append({"name": name, "source": str(path),
                                    "sha256": digest(path), "bytes": path.stat().st_size})
        for architect_id, path in outbox.pending_in(root):
            mail.append({"recipient": architect_id, "filename": path.name,
                         "sha256": digest(path), "source": str(path)})
        comms_dir = root / "comms"
        if comms_dir.is_dir():
            for path in sorted(comms_dir.glob("*.md")):
                comms.append({"filename": path.name, "sha256": digest(path),
                              "source": str(path)})
    stranded = stranded_in_release_history(code_root, log)
    unpublished = []
    for root in roots_of_state:
        unpublished.extend(unpublished_commits(root))
    found = {"state_files": state_files, "mail": mail, "comms": comms,
             "stranded": stranded, "unpublished_commits": unpublished}
    log(f"inventory: {len(state_files)} durable state file(s), {len(mail)} queued "
        f"message(s), {len(comms)} comms note(s), {len(stranded)} message(s) stranded in "
        f"the release tree's own history, {len(unpublished)} unpublished commit(s)")
    return found


def unpublished_commits(repo: Path) -> list:
    """Commits a source holds that its upstream does not — REPORTED, never replayed.

    The item asks for unpublished mail "including local commits", and this is the honest
    shape of that. A stranded commit's CONTENT is already in the working tree, so the
    files it touched are inventoried and copied like anything else; what is stranded is
    the COMMIT, and a commit is not something a mail queue can deliver. Recording them is
    what lets a reader afterwards see that nothing was quietly dropped.

    A checkout with no tracking ref reports none rather than blocking: `@{u}` there fails
    or, worse, prompts, and neither is an answer about mail."""
    if not (Path(repo) / ".git").exists():
        return []
    upstream = runner.git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                          Path(repo), timeout=30)
    if upstream.returncode != 0:
        return []
    listed = runner.git(["log", "--format=%H %s", "@{u}..HEAD"], Path(repo), timeout=60)
    if listed.returncode != 0:
        return []
    rows = []
    for line in listed.stdout.splitlines():
        commit, _, subject = line.partition(" ")
        if commit:
            rows.append({"repo": str(repo), "commit": commit, "subject": subject})
    return rows


def stranded_in_release_history(tree: Path, log) -> list:
    """Messages committed INTO the detached release tree, where they can reach nobody.

    THIS IS THE PRIMARY RECOVERY PATH, not an edge case. `outbox.publish` commits under
    the repository root and pushes; in a detached deploy tree there is no branch and no
    upstream, so the commit is made and stranded — the failure WI-0360 predicted for
    cutover and the arrangement this program starts from. The payloads are
    still recoverable because a detached commit stays reachable through the reflog until
    it is pruned.

    REPORTS WHAT IT COULD NOT READ rather than reporting none. An unreadable history and
    an empty one are different answers, and folding them together is how a recovery
    reports success over mail it never looked for."""
    if not (tree / ".git").exists():
        return []
    listed = runner.git(["log", "--format=%H", "-g", "--all", "--", "outbox"],
                        tree, timeout=120)
    if listed.returncode != 0:
        log("recover: the release tree's history is unreadable — reporting that, not "
            "claiming there is nothing stranded")
        return [{"unreadable": True}]
    rows, skipped = [], 0
    for commit in dict.fromkeys(listed.stdout.split()):
        if _is_published(tree, commit):
            skipped += 1
            continue
        for name in _outbox_paths_introduced(tree, commit):
            blob = runner.git(["show", f"{commit}:{name}"], tree, timeout=60)
            if blob.returncode != 0:
                continue
            payload = blob.stdout
            rows.append({"commit": commit, "path": name,
                         "recipient": Path(name).parent.name[len("to-"):],
                         "filename": Path(name).name,
                         "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()})
    log(f"recover: {len(rows)} stranded payload(s); {skipped} commit(s) skipped as "
        f"already published")
    return rows


def _is_published(tree: Path, commit: str) -> bool:
    """Is this commit reachable from a remote-tracking branch — i.e. NOT stranded?

    THE SCAN WITHOUT THIS FILTER RE-SENDS THE ARCHIVE. `git log -g --all` walks the reflog
    of EVERY ref, and a fetch writes a reflog entry at each remote-tracking ref's tip — so
    every ordinary published receipt the channel ever carried comes back as a candidate.
    Many of them are already contained in `origin/runner/mail`. Enqueued, they would be delivered again to every recipient,
    because a `migrated:` identity is new and nothing dedupes against messages already
    delivered.

    A commit no remote branch contains is the real thing: the deploy tree is a full clone,
    so every branch is tracked, and a commit made on a detached HEAD is reachable from
    none of them."""
    contained = runner.git(["branch", "-r", "--contains", commit], tree, timeout=60)
    return contained.returncode == 0 and bool(contained.stdout.strip())


def _outbox_paths_introduced(tree: Path, commit: str) -> list:
    """Outbox files this commit ADDED, NUL-separated and merge-aware.

    `git show --name-only` prints nothing for a merge commit, so a payload that arrived
    through one would be invisible; `--first-parent -m` gives the merge a diff to show.
    `-z` because a filename containing a space is shredded by whitespace splitting into
    fragments that match no rule — which reads as "no mail here" rather than as an error."""
    listed = runner.git(["diff-tree", "-r", "--no-commit-id", "--name-only", "-z",
                         "-m", "--first-parent", "--root", commit], tree, timeout=60)
    if listed.returncode != 0:
        return []
    return [name for name in listed.stdout.split("\0")
            if name.startswith("outbox/to-") and name.endswith(".md")]


# ── phases 3 and 4: import ───────────────────────────────────────────────────────

def import_state(roots, recorded: list, comms: list, log) -> dict:
    """Copy durable runtime state and unpublished comms notes across, verifying every byte.

    COPY, never move. The source keeps its own copy, which is what makes this re-runnable
    and what leaves a recovery archive behind when the old root is finally retired. A
    destination that already exists is left alone: a second run must not overwrite state
    the service has been writing since the first."""
    runtime = roots.state_path("runtime")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    comms_root = roots.comms_root
    comms_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    files, notes = [], []
    for item in recorded:
        destination = runtime / item["name"]
        if destination.exists():
            files.append({"name": item["name"], "already_present": True})
            continue
        copy_atomic(item["source"], destination)
        arrived = digest(destination)
        if arrived != item["sha256"]:
            # The source is untouched — nothing was moved — so this refuses rather than
            # repairing, and names the file and both digests.
            raise MigrationError(
                f"copied state does not match what was inventoried: {item['name']} read "
                f"{item['sha256']} at the source and {arrived} at {destination}")
        files.append({"name": item["name"], "copied": True, "sha256": arrived})
        log(f"import: {item['name']} → {destination}")
    for item in comms:
        destination = comms_root / item["filename"]
        if destination.exists():
            notes.append({"filename": item["filename"], "already_present": True})
            continue
        copy_atomic(item["source"], destination)
        notes.append({"filename": item["filename"], "copied": True,
                      "sha256": digest(destination)})
    log(f"import: {len(notes)} comms note(s) now under {comms_root}")
    return {"files": files, "comms": notes}


def import_mail(roots, recorded: dict, log) -> dict:
    """Re-enqueue queued and stranded mail into the durable production queue.

    IDENTITY IS WHAT MAKES A RESUME SAFE. Each message is enqueued under an identity
    derived from its own content and address, so a run interrupted after the enqueue and
    before the checkpoint finds the message already present on the retry and returns it
    rather than queueing a duplicate. `mailqueue.enqueue` refuses the same identity with
    different content, which turns a mistaken identity into a refusal instead of a silent
    overwrite.

    A PAYLOAD THE SCRUB GATE REFUSES IS NOT DROPPED. It stays exactly where it is and is
    reported: the gate is the same one the outbox applies, so a message it refuses could
    never have been published from the old arrangement either, and this program is not the
    place to decide it now."""
    queued, skipped = [], []

    def offer(recipient, filename, text, sha, origin, producer):
        message_id = f"migrated:{sha}:{recipient}/{filename}"
        # Imported mail flows as it is, except that a brief with no edit-id gains one
        # derived from this (deterministic) identity: the receiver can deliver a
        # name-colliding brief only when it carries one. Deterministic, so a resumed
        # import re-offers identical content and `enqueue` still reads it as the same.
        text = mailnames.ensure_edit_id(text, message_id, filename)
        try:
            mailqueue.enqueue(roots, recipient, filename, text, message_id=message_id,
                              provenance={"producer": producer, "origin": origin})
        except ValueError as exc:
            skipped.append({"origin": origin, "reason": str(exc)})
            return
        queued.append({"message_id": message_id, "recipient": recipient,
                       "filename": filename, "sha256": sha, "origin": origin})

    for item in recorded.get("mail", []):
        try:
            text = Path(item["source"]).read_text(encoding="utf-8")
        except OSError as exc:
            skipped.append({"origin": item["source"], "reason": f"unreadable: {exc}"})
            continue
        offer(item["recipient"], item["filename"], text, item["sha256"],
              item["source"], "migrate.import_mail")
    for item in recorded.get("stranded", []):
        if item.get("unreadable"):
            skipped.append({"origin": "release-tree history",
                            "reason": "the release tree's history could not be read; "
                                      "stranded mail there is neither recovered nor "
                                      "provably absent"})
            continue
        blob = runner.git(["show", f"{item['commit']}:{item['path']}"],
                          Path(roots.code_root), timeout=60)
        if blob.returncode != 0:
            skipped.append({"origin": f"{item['commit']}:{item['path']}",
                            "reason": "the blob is no longer readable — it was recorded "
                                      "in the inventory and could not be recovered"})
            continue
        offer(item["recipient"], item["filename"], blob.stdout, item["sha256"],
              f"{item['commit']}:{item['path']}", "migrate.recover_stranded")
    log(f"import: {len(queued)} message(s) queued, {len(skipped)} left in place and reported")
    return {"queued": queued, "skipped": skipped}


# ── phase 5: fence ───────────────────────────────────────────────────────────────

def fence(contract: dict, found: dict, config_path: Path, log) -> dict:
    """Preserve each loaded unit's current definition, before anything replaces it.

    THE ROLLBACK ARTICLE, and it is taken as its own checkpoint rather than inside the
    rebind, because the bytes must exist on disk before the first `bootout` and a crash
    between the two must leave the old definition recoverable. `install-mail-worker.py`
    already preserves a displaced plist this way (`.pre-mail-worker`); this is the same
    article under its own suffix.

    NOTHING IS BOOTED OUT HERE. The released and the production-bound unit share a LABEL,
    so the handoff is one `bootout` immediately followed by one `bootstrap` in `rebind` —
    which is what makes two writers for one owned ref impossible by construction rather
    than by timing. Booting out here would instead open a window with nothing running and
    a final message unwritten."""
    agents = runner.launch_agents_dir()
    preserved = []
    for unit in contract.get("units", []):
        installed = agents / f"{unit}.plist"
        if not installed.exists():
            preserved.append({"unit": unit, "installed": False})
            continue
        backup = installed.with_suffix(".plist.pre-migration")
        if not backup.exists():
            copy_atomic(installed, backup)
        preserved.append({"unit": unit, "installed": True, "backup": str(backup),
                          "sha256": digest(backup),
                          "was_production_bound": _is_production_bound(installed, unit,
                                                                      config_path)})
        log(f"fence: preserved {unit}'s current definition at {backup.name}")
    return {"preserved": preserved, "own_unit": found.get("own_unit") or ""}


def _is_production_bound(path: Path, unit: str, config_path: Path) -> bool:
    """Is this unit bound to THIS configuration AND actually loaded?

    THREE QUESTIONS, and the first cut of this asked only the weakest one. "The installed
    file mentions the selector" is not "the running job is bound":

      * A `bootstrap` that failed leaves the bound plist on disk and the unit LOADED BY
        NOTHING — `_cutover_install` boots out first. Asking the file alone reports it
        bound, so the retry skips it and the unit never comes back. `unit_target` is the
        loaded job's own answer, which is why the runner uses it for cutover state.
      * Any value counts is not this value counts. Re-running with `--config /other/path`
        would find the key present, skip every unit, and leave them reading the old state
        root while the import fills the new one — with nothing saying so.

    Absence of the key, a different value, an unreadable plist and an unloaded job all
    answer False, which is the direction that re-binds rather than the direction that
    silently skips."""
    try:
        document = plistlib.loads(Path(path).read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    declared = (document.get("EnvironmentVariables") or {}).get(production.CONFIG_ENV)
    if not declared or Path(declared) != Path(config_path).resolve():
        return False
    return bool(runner.unit_target(unit))


# ── phase 6: rebind ──────────────────────────────────────────────────────────────

def rebind(entry: dict, contract: dict, tag: str, found: dict,
           config_path: Path, log) -> dict:
    """Install each unit bound to the released program AND to external state.

    WHY THE RUNNER'S CUTOVER CANNOT DO THIS. `perform_cutover` acts only on units that are
    `pending` or `unloaded` — units that do not yet point at the deploy tree. Here they
    already do: the cutover has happened and `cutover_state` correctly reports
    nothing to do. What is missing is not the code root but the ENVIRONMENT — no unit
    carries `POGA_FEDERATION_CONFIG`, so all three run released code with production
    dormant, writing into the sealed tree. So this asks a different question of each unit
    ("is it bound?") and reuses the runner's checked install for the answer.

    THE UNIT THIS PROCESS RUNS UNDER IS NOT BOOTED OUT. `bootout` on our own job
    terminates the process issuing it, mid-transition and before the checkpoint — the
    hazard `refuse_self_restart` names, arriving by a different verb. It is deferred to
    the runner's detached finisher, which waits for this pid to leave the process table
    and then performs the same checked install. Deferring without the finisher would mean
    never: a loaded job keeps the definition it was bootstrapped with."""
    expected = str(runner.work_dir(SYSTEM, entry))
    agents = runner.launch_agents_dir()
    own = found.get("own_unit") or ""
    bound, refused, deferred = [], [], []
    # THE THIRD CALLER OF THE HOST FILTER (WI-0420), and the one that found the rule the
    # hard way. `rebind` INSTALLS, so it is bound by the same rule as the cutover: without
    # this it asks `unit_plist_source` for a unit belonging to another machine, gets the
    # refusal the renderer now issues, records "no installable plist" and RAISES — so the
    # Runner's migration would fail forever on `com.federation.gate-inputs`, a unit it
    # should never have been binding. Discovery and fencing above deliberately still walk
    # the whole contract: reporting what is loaded here, and preserving what is already
    # installed here, are true of every declared unit whoever owns it.
    for unit in runner.units_for_this_host(SYSTEM, entry, contract, log):
        installed = agents / f"{unit}.plist"
        if installed.exists() and _is_production_bound(installed, unit, config_path):
            bound.append({"unit": unit, "already_bound": True})
            continue
        if unit == own:
            spawned = runner._spawn_self_cutover_finisher(SYSTEM, unit, os.getpid())
            deferred.append({"unit": unit, "finisher_spawned": spawned, "pid": os.getpid()})
            log(f"rebind: {unit} is this process's own unit — deferred to a detached "
                f"finisher waiting on pid {os.getpid()}"
                + ("" if spawned else " — WHICH COULD NOT BE SPAWNED; this unit stays "
                                     "unbound and the next sweep retries"))
            continue
        source = runner.unit_plist_source(SYSTEM, entry, unit, log)
        if source is None:
            log(f"rebind: REFUSED for {unit} — the render declined and the in-tree file "
                f"is the stale unbound artifact, so there is nothing safe to install")
            refused.append({"unit": unit, "reason": "no installable plist"})
            continue
        ok, why = runner._cutover_plist_is_usable(source, unit, expected)
        if not ok:
            log(f"rebind: REFUSED for {unit} — {why}")
            refused.append({"unit": unit, "reason": why})
            continue
        code = runner._cutover_install(unit, source, log)
        if code != 0:
            refused.append({"unit": unit, "reason": "launchctl bootstrap failed"})
            continue
        bound.append({"unit": unit, "installed_from": str(source)})
        log(f"rebind: {unit} now runs {expected} on external state")
    result = {"bound": bound, "refused": refused, "deferred": deferred, "tag": tag}
    unspawned = [d["unit"] for d in deferred if not d.get("finisher_spawned")]
    if refused or unspawned:
        # NOT CHECKPOINTED, and that is the whole point of raising here. `record` is what
        # makes a phase never run again, so writing a checkpoint over a partial rebind
        # would leave some units bound and some not, permanently, under a journal that
        # reads complete and a log line that says "runs on external state". Raising keeps
        # the phase outstanding, so the next preparation re-enters it and retries exactly
        # the units that did not take — the ones that did are skipped as already bound.
        raise MigrationError(
            "rebind did not complete: "
            + "; ".join(f"{row['unit']} — {row['reason']}" for row in refused)
            + ("; deferred with no finisher: " + ", ".join(unspawned) if unspawned else "")
            + f". {len(bound)} unit(s) were bound and are left bound; the rest still run "
              f"the arrangement they had. Nothing was lost.")
    return result


# ── phase 7: repoint ─────────────────────────────────────────────────────────────

def repoint(entry: dict, found: dict, log) -> dict:
    """Point `poga` at the release, after reading what it currently points at.

    Delegated whole to `runner.repoint_cli_link`, which refuses anything that is not a
    symlink pointing outside the deploy tree — a real file there is somebody's own install
    and not ours to replace — and swaps atomically so the command is never briefly absent.
    The prior target was read in discovery and is recorded here, which is what lets
    `rollback` put it back."""
    moved = runner.repoint_cli_link(SYSTEM, entry, log, False)
    return {"moved": moved, "previous_target": found.get("cli_target"),
            "was_symlink": found.get("cli_link_is_symlink")}


# ── the transition ───────────────────────────────────────────────────────────────

def prepare(config_path: Path, *, log, dry_run: bool = False) -> dict:
    """Run whichever phases are not already recorded, in order. Idempotent end to end."""
    entry = runner.registry_entry(SYSTEM)
    code_root = runner.work_dir(SYSTEM, entry)
    tag = runner.deployed_tag(SYSTEM, entry)
    if not tag:
        raise MigrationError(
            f"the deploy tree at {code_root} holds no tag, so there is no released version "
            f"to bind to. Nothing was changed.")
    contract = runner.read_contract(SYSTEM, entry, tag)
    found = discover(entry, contract, code_root, log)
    retiring = found.get("retiring_root")
    sources = config_sources(code_root, retiring, log)
    state_sources = [Path(retiring)] if retiring else []
    state_sources.append(code_root)

    if dry_run:
        return {"dry_run": True, "tag": tag, "code_root": str(code_root),
                "discovery": found, "config_sources": sources,
                "inventory": inventory(state_sources, code_root, log),
                "phases_outstanding": list(PHASES)}

    author_config(config_path, code_root=code_root, remote=entry["remote"], log=log)
    document = raw_config(config_path)
    declared = Path(document["code_root"]).resolve()
    if declared != Path(code_root).resolve():
        raise MigrationError(
            f"the service configuration names code_root {declared}, but this release is at "
            f"{code_root}. A configuration pointing at another installation would bind "
            f"this host's units to a program that is not running. Nothing was changed.")
    provision(document, sources, log)
    # UNGATED, on every apply — that is, on every deploy. Seed-once is right for the two
    # machine-local files and was the defect for the roster (WI-0428); the ack map is derived
    # from the refreshed roster, so it follows the same release (WI-0429).
    config_root = Path(document.get("config_root", str(Path(document["state_root"]) / "config")))
    refresh_release_config(config_root, code_root, log)
    ensure_recipient_refs(config_path, config_root / "mailboxes.json",
                          runner.load_registry().get("systems", {}), log)
    roots = resolve_or_refuse(config_path, code_root)
    # UNGATED, like `author_config` above: a completed `provision` must not skip it (WI-0425).
    reconcile_transport_ownership(roots, log)
    # FROM HERE ON, THIS PROCESS IS A PRODUCTION PROCESS, and it has to be. `run_apply`
    # invokes this program with production dormant — nothing sets the selector, which is
    # the whole state being migrated out of — so every release-tree helper it then calls
    # (`render_unit_plist`, `escalate`, `comms_dir`) would resolve to the DEVELOPMENT
    # path and quietly bind the units to the arrangement this is replacing. The variable
    # is set only after `resolve` has accepted the configuration, so it is never set to
    # something unusable, and only in this process, which exits at the end of the apply.
    os.environ[production.CONFIG_ENV] = str(config_path)
    state_root = roots.state_root
    journal = read_journal(state_root)
    journal.update(code_root=str(code_root), config_path=str(config_path), tag=tag,
                   config_sources=sources, latest_discovery=found)
    # WRITTEN ONCE, AND ONLY ONCE. `discovery` holds the PRE-migration reading — which
    # `poga` symlink target there was before anything repointed it, what each unit's
    # working directory was. Refreshing it on every run overwrites exactly that: after
    # `repoint` has moved the link into the deploy tree, the next run would record the
    # deploy tree as the "previous" target and `rollback` would restore `poga` to where it
    # already points, with the original address gone from every record. The current
    # reading is still kept, under its own key, because it is useful and is not evidence
    # about the past.
    journal.setdefault("discovery", found)

    if not done(journal, "provision"):
        journal = record(state_root, journal, "provision", state=str(state_root),
                         config=str(roots.config_root), **claim_transport(roots, log))
    if not done(journal, "inventory"):
        counted = inventory(state_sources, code_root, log)
        journal["inventory"] = counted
        journal = record(state_root, journal, "inventory",
                         state_files=len(counted["state_files"]),
                         mail=len(counted["mail"]), comms=len(counted["comms"]),
                         stranded=len(counted["stranded"]),
                         unpublished_commits=len(counted["unpublished_commits"]))
    counted = journal["inventory"]
    if not done(journal, "import-state"):
        journal = record(state_root, journal, "import-state",
                         **import_state(roots, counted["state_files"], counted["comms"], log))
    if not done(journal, "import-mail"):
        journal = record(state_root, journal, "import-mail",
                         **import_mail(roots, counted, log))
    if not done(journal, "fence"):
        journal = record(state_root, journal, "fence", **fence(contract, found, config_path, log))
    if not done(journal, "rebind"):
        journal = record(state_root, journal, "rebind",
                         **rebind(entry, contract, tag, found, config_path, log))
    if not done(journal, "repoint"):
        journal = record(state_root, journal, "repoint", **repoint(entry, found, log))
    journal["completed_at"] = runner._now()
    _write_json(journal_path(state_root), journal)
    log(f"migrate: complete — {SYSTEM} runs {tag} from {code_root} on external state at "
        f"{state_root}")
    return journal


# ── rollback ─────────────────────────────────────────────────────────────────────

def rollback(config_path: Path, *, log) -> dict:
    """Restore the preserved unit definitions, and refuse the two ways that loses work.

    A ROLLBACK IS NOT AN UNDO OF THE IMPORT. State was copied and never moved, so every
    source still holds what it held; what this restores is the CONFIGURATION — which
    definitions are loaded and where `poga` points. The imported queue is deliberately
    left in place: deleting it is the one way this program could lose a message, and a
    queue the restored arrangement does not read is inert rather than harmful.

    IT CANNOT REACTIVATE COMPETING WRITERS. Each unit is booted out and its preserved
    definition bootstrapped under the SAME label, one label at a time, so there is never a
    moment when a bound and an unbound publisher both hold the owned ref.

    IT CANNOT DOWNGRADE BELOW THE STATE IT CREATED. Once the queue holds messages that
    exist only in production state, restoring a definition whose program cannot read that
    state would strand them where nothing looks — so that case is named and refused
    rather than performed."""
    document = raw_config(config_path)
    state_root = Path(document["state_root"])
    journal = read_journal(state_root)
    preserved = journal.get("phases", {}).get("fence", {}).get("preserved")
    if not preserved:
        raise MigrationError(
            f"no fenced unit definitions are recorded at {journal_path(state_root)} — "
            f"there is nothing to restore, and inventing something would boot out units "
            f"this program never installed. Nothing was changed.")
    code_root = Path(journal.get("code_root", document["code_root"]))
    blind = [(root, why) for root, why in
             ((root, _cannot_read_migrated_state(root)) for root in _restored_roots(preserved))
             if why]
    if blind and _queue_holds_messages(document):
        raise MigrationError(
            "REFUSING to roll back: the production queue holds messages and the "
            "definitions this would restore point at "
            + "; ".join(f"{root} ({why})" for root, why in blind)
            + ". Restoring them would leave that mail queued where nothing reads it. "
              "Nothing was changed.")
    agents = runner.launch_agents_dir()
    domain = runner.launchctl_domain()
    restored, skipped = [], []
    for item in preserved:
        unit = item["unit"]
        if not item.get("installed"):
            skipped.append({"unit": unit, "reason": "no definition was installed before "
                                                    "the migration"})
            continue
        backup = Path(item["backup"])
        if not backup.exists():
            raise MigrationError(
                f"the preserved definition for {unit} is missing at {backup}; it cannot be "
                f"restored from this record and nothing further was booted out.")
        if digest(backup) != item["sha256"]:
            raise MigrationError(
                f"the preserved definition for {unit} at {backup} no longer matches the "
                f"digest recorded when it was preserved. Refusing to bootstrap a "
                f"definition this program cannot vouch for.")
        runner.run(["launchctl", "bootout", f"{domain}/{unit}"], timeout=60)
        installed = agents / f"{unit}.plist"
        copy_atomic(backup, installed)
        boot = runner.run(["launchctl", "bootstrap", domain, str(installed)], timeout=120)
        if boot.returncode != 0:
            log(f"rollback: bootstrap FAILED for {unit} — "
                f"{((boot.stderr or boot.stdout) or '').strip()}")
            skipped.append({"unit": unit, "reason": "bootstrap failed"})
            continue
        runner.run(["launchctl", "enable", f"{domain}/{unit}"], timeout=60)
        restored.append(unit)
        log(f"rollback: restored {unit} from {backup.name}")
    result = {"restored": restored, "skipped": skipped,
              "cli": _restore_cli_link(journal, log)}
    journal["rolled_back_at"] = runner._now()
    journal["rollback"] = result
    _write_json(journal_path(state_root), journal)
    return result


def _restored_roots(preserved: list) -> list:
    """The code roots the preserved unit definitions would put back into service.

    THE SUBJECT OF THE DOWNGRADE RULE, and the first cut had the wrong one. It asked
    whether the RUNNING program could read production state — but in the shipped
    invocation the running program is this file's own release, which imports
    `production` and `mailqueue` at module load. If either were missing this program
    could not have started, so the question always answered "it can" and the refusal was
    dead code that only a fixture could reach.

    What a rollback actually changes is which definitions are loaded, and each of those
    names a `WorkingDirectory`. That is the program that would be running afterwards, and
    it is the one the rule is about."""
    roots = []
    for item in preserved:
        if not item.get("installed"):
            continue
        try:
            document = plistlib.loads(Path(item["backup"]).read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        directory = document.get("WorkingDirectory")
        if directory and directory not in roots:
            roots.append(directory)
    return roots


def _cannot_read_migrated_state(root) -> str:
    """Why `root`'s program could not read production state, or "" when it can."""
    missing = [name for name in ("curate/production.py", "curate/mailqueue.py")
               if not (Path(root) / name).is_file()]
    return ("carries no " + " or ".join(missing)) if missing else ""


def _queue_holds_messages(document: dict) -> bool:
    queue = Path(document["state_root"]) / "queue"
    if not queue.is_dir():
        return False
    return any(not path.name.startswith(".") for path in queue.iterdir())


def _restore_cli_link(journal: dict, log) -> dict:
    """Put `poga` back where discovery found it — and only if it is still ours to move."""
    recorded = journal.get("discovery", {})
    target, link = recorded.get("cli_target"), runner.cli_link()
    if not target or not recorded.get("cli_link_is_symlink"):
        return {"restored": False, "reason": "no prior symlink target was recorded"}
    if not link.is_symlink():
        return {"restored": False, "reason": f"{link} is no longer a symlink"}
    temporary = link.with_name(link.name + ".rollback")
    try:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(target)
        os.replace(temporary, link)     # atomic: the command is never briefly absent
    except OSError as exc:
        try:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        log(f"rollback: could not repoint {link} — {exc}")
        return {"restored": False, "reason": str(exc)}
    log(f"rollback: repointed {link} → {target}")
    return {"restored": True, "target": target}


# ── entry point ──────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Federation production migration (WI-0365).")
    parser.add_argument("command", choices=("prepare", "plan", "rollback", "status",
                                            "recipient-refs"))
    parser.add_argument("--config", help="the external service configuration (absolute)")
    parser.add_argument("--mailboxes", help="recipient-refs: the roster to derive from "
                                            "(default: <config_root>/mailboxes.json)")
    parser.add_argument("--quiet", action="store_true")
    # THE DEPLOY MUST NOT BECOME UNDEPLOYABLE BECAUSE PREPARATION REFUSED. `run_apply`
    # raises `DeployError` on a non-zero exit, which fails the whole deploy — so a
    # refusal here (a missing member-location file, a configuration naming another
    # installation) would block EVERY later federation release and need a human on the
    # Runner, which is the one thing this wave may not require. A refusal is therefore a
    # HELD state: production stays dormant, the old arrangement keeps running, and the
    # reason is escalated through the runner's own channel. It is loud and it is not fatal.
    parser.add_argument("--hold-on-refusal", action="store_true",
                        help="a refusal holds production dormant and escalates, instead "
                             "of failing the deploy that invoked this")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    config_path = Path(args.config) if args.config else default_config_path()
    if not config_path.is_absolute():
        print("migrate: --config must be an absolute path", file=sys.stderr)
        return 2
    try:
        if args.command == "plan":
            print(json.dumps(prepare(config_path, log=log, dry_run=True),
                             indent=2, sort_keys=True))
        elif args.command == "prepare":
            prepare(config_path, log=log)
        elif args.command == "rollback":
            print(json.dumps(rollback(config_path, log=log), indent=2, sort_keys=True))
        elif args.command == "recipient-refs":
            # READ-ONLY: what `prepare` would derive, beside what the file holds. Exit 0
            # only when they are identical (WI-0429's proof against a hand-written map).
            document = raw_config(config_path)
            mailboxes = Path(args.mailboxes) if args.mailboxes else Path(document.get(
                "config_root", str(Path(document["state_root"]) / "config"))) / "mailboxes.json"
            result = check_recipient_refs(config_path, mailboxes,
                                          runner.load_registry().get("systems", {}))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["exact"] else 1
        else:
            document = raw_config(config_path)
            print(json.dumps(read_journal(Path(document["state_root"])),
                             indent=2, sort_keys=True))
    except Exception as exc:                         # noqa: BLE001 — see below
        # DELIBERATELY EVERYTHING, and only under the flag. `--hold-on-refusal` exists so
        # that a preparation which cannot finish does not fail the deploy that invoked it.
        # Catching only `MigrationError` and `DeployError` honoured that for the refusals
        # this program writes and missed every one it does not: an `OSError` from a full
        # disk mid-copy, a `TimeoutExpired` from a slow `git show`, a `ConfigurationError`
        # from a config removed between two phases. Each of those became a traceback, a
        # non-zero exit, a failed deploy and a rollback — verbatim the outcome the flag is
        # there to prevent. `KeyboardInterrupt` and `SystemExit` are not `Exception` and
        # still propagate, so an operator's Ctrl-C is not swallowed as a held migration.
        print(f"migrate: {exc}", file=sys.stderr)
        if args.hold_on_refusal and args.command == "prepare":
            try:
                runner.escalate(
                    SYSTEM, f"{SYSTEM}: production migration is HELD",
                    f"Preparation did not finish, so production stays dormant and the "
                    f"current arrangement is untouched. The release still deployed.\n\n"
                    f"```\n{type(exc).__name__}: {exc}\n```\n", log)
            except Exception as reporting:           # noqa: BLE001
                # The escalation is the loud half; losing it must not turn a held
                # migration into a failed deploy on top of whatever went wrong first.
                print(f"migrate: the hold could not be escalated — {reporting}",
                      file=sys.stderr)
            return 0
        if isinstance(exc, (MigrationError, runner.DeployError)):
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
