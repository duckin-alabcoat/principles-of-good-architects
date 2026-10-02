"""Immutable local mail with atomic durable enqueue and separate delivery evidence.

An envelope and payload become visible together via a directory rename. fsync precedes
publication; a per-identity flock protects producers, and an interrupted staging directory
is invisible to readers. Only transport records published; only verified acknowledgments
record delivered. Queue payloads are never moved or rewritten by lifecycle changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager

import scrub


@dataclass(frozen=True)
class Envelope:
    schema_version: int
    message_id: str
    destination: str
    filename: str
    sha256: str
    provenance: dict
    created_at: str
    payload_path: Path

    def as_dict(self):
        return {key: getattr(self, key) for key in (
            "schema_version", "message_id", "destination", "filename", "sha256",
            "provenance", "created_at")}


def identity_key(message_id):
    if not isinstance(message_id, str) or not message_id or len(message_id) > 512:
        raise ValueError("message_id must be a nonempty string of at most 512 characters")
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, payload):
    with path.open("xb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


@contextmanager
def _lock(roots, key):
    directory = roots.state_path("queue-locks")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = roots.state_path("queue-locks", key + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _safe_name(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value in (".", "..", "envelope.json"):
        raise ValueError(f"unsafe {label}: {value!r}")


def read(path):
    path = Path(path)
    directory = path if path.is_dir() else path.parent
    if directory.is_symlink() or (directory / "envelope.json").is_symlink():
        raise ValueError(f"queue envelope must not be a symlink: {directory}")
    data = json.loads((directory / "envelope.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"unsupported envelope version: {directory}")
    identity_key(data.get("message_id"))
    _safe_name(data.get("destination"), "destination")
    _safe_name(data.get("filename"), "filename")
    payload = directory / data["filename"]
    if payload.is_symlink() or hashlib.sha256(payload.read_bytes()).hexdigest() != data.get("sha256"):
        raise ValueError(f"queue payload hash mismatch or symlink: {payload}")
    if not isinstance(data.get("provenance"), dict) or not isinstance(data.get("created_at"), str):
        raise ValueError(f"invalid queue provenance: {directory}")
    return Envelope(**data, payload_path=payload)


def holds(roots, message_id):
    """True when this queue already holds a message with this identity."""
    return roots.state_path("queue", identity_key(message_id)).exists()


def enqueue(roots, destination, filename, text, message_id=None, provenance=None):
    """Scrub first, then durable enqueue. Repeated identity must have identical content."""
    _safe_name(destination, "destination")
    _safe_name(filename, "filename")
    message_id = message_id or str(uuid.uuid4())
    key = identity_key(message_id)
    provenance = dict(provenance or {})
    # Provenance travels too; it must pass the same gate as the message.
    metadata = {"message_id": message_id, "destination": destination,
                "filename": filename, "provenance": provenance}
    hits = scrub.findings(text + "\n" + json.dumps(metadata, sort_keys=True), filename)
    if hits:
        raise ValueError(f"REFUSED by scrub gate: {filename} has {len(hits)} finding(s)")
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    queue = roots.state_path("queue")
    queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    _fsync_dir(roots.state_root)  # retain the queue directory itself after power loss
    dest = roots.state_path("queue", key)
    with _lock(roots, key):
        if dest.exists():
            previous = read(dest)
            if (previous.sha256, previous.destination, previous.filename, previous.provenance) != (digest, destination, filename, provenance):
                raise ValueError(f"message identity {message_id!r} already has different content or routing")
            return previous.payload_path
        staging = Path(tempfile.mkdtemp(prefix=".enqueue-", dir=queue))
        try:
            envelope = Envelope(1, message_id, destination, filename, digest, provenance,
                                datetime.now(timezone.utc).isoformat(), dest / filename)
            _write(staging / filename, payload)
            _write(staging / "envelope.json", (json.dumps(envelope.as_dict(), sort_keys=True) + "\n").encode())
            _fsync_dir(staging)
            os.rename(staging, dest)
            _fsync_dir(queue)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return read(dest).payload_path


# `undeliverable` is terminal for `pending` but still ranks BELOW `delivered`: the
# recipient declared it could not deliver (a negative ack, with a reason), and a later
# real delivery — after the recipient's registry is fixed and it re-drives — still wins.
RANK = {"queued": 0, "published": 1, "superseded": 2, "undeliverable": 3, "delivered": 4}

# States that end a message's claim on the pending set. `undeliverable` is not hidden by
# leaving it: `undeliverable()` below counts it, and the worker's health reports it.
SETTLED = ("delivered", "superseded", "undeliverable")

# A diagnosis names its own collection moment twice (edit-id, "Collected") and carries
# the runner's `checked_at` — when the runner last LOOKED, not what it found. The
# producer's own digest excludes all three as not-state (deploy/diagnose.py `digest`).
_BOOKKEEPING = re.compile(r"^(?:edit-id:|- \*\*checked_at:\*\*).*$", re.M)


def duplicate_key(destination, message_id, provenance, payload):
    """Identity of a diagnosis's CONTENT, or None for mail that never collapses.

    OPS-0010 soak. Two bundles with one key say the same thing to the same recipient: they
    differ only in the bookkeeping `_BOOKKEEPING` masks and the collection stamp their
    own message id carries. Only `deploy.diagnose` mail has a key; every other producer's
    mail is distinct by construction and is never superseded. Sender and receiver both
    call this, so both pick the same representative (`representative`) without talking.
    """
    if (provenance or {}).get("producer") != "deploy.diagnose" or \
            not isinstance(message_id, str) or not message_id.startswith("diagnose:") or \
            message_id.count(":") < 3:
        return None
    stamp = message_id.split(":", 3)[3]
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    except UnicodeError:
        return None
    body = _BOOKKEEPING.sub("", text)
    if stamp:
        body = body.replace(stamp, "<collected>")
    return hashlib.sha256("\0".join(
        (destination, str(provenance.get("system")), body)).encode("utf-8")).hexdigest()


def representative(members, delivered):
    """The one message of a duplicate group that stands for the rest.

    `members` are (created_at, message_id) pairs; `delivered` is the set of their ids
    this side already knows arrived. A delivered member wins — the recipient has that
    one — else the oldest. The receiver's `delivered` is its receipts and the sender's is
    its acknowledgments; an ack in flight can make them differ for a cycle, but each
    side only ever moves its pick TO a delivered member, so both settle on the same one.
    """
    pool = [m for m in members if m[1] in delivered] or list(members)
    return min(pool)[1]


def lifecycle(roots, message_id):
    path = roots.state_path("message-state", identity_key(message_id) + ".json")
    if not path.exists():
        return {"state": "queued"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("state") not in RANK or data.get("message_id") != message_id:
        raise ValueError(f"invalid message lifecycle: {path}")
    return data


def mark(roots, message_id, state, **evidence):
    """Monotonic lifecycle update; receipt evidence is retained across transitions.

    `superseded` ranks below `delivered`: an ack arriving for a superseded message
    still records it delivered, because the recipient's word outranks our bookkeeping.
    `undeliverable` ranks below `delivered` for the same reason: a later real delivery
    overrides the recipient's earlier negative ack, never the other way round.
    """
    rank = RANK
    if state not in rank:
        raise ValueError(f"unknown message state: {state}")
    if any(k in evidence for k in ("state", "message_id")):
        raise ValueError("evidence cannot override lifecycle identity/state")
    key = identity_key(message_id)
    with _lock(roots, key):
        message = read(roots.state_path("queue", key))
        previous = lifecycle(roots, message_id)
        if rank[state] < rank[previous["state"]]:
            return previous
        if state != "queued" and not evidence:
            raise ValueError("published/superseded/undeliverable/delivered state requires evidence")
        directory = roots.state_path("message-state")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _fsync_dir(roots.state_root)
        value = {**previous, **evidence, "message_id": message.message_id,
                 "sha256": message.sha256, "state": state}
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(value, file, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, roots.state_path("message-state", key + ".json"))
            _fsync_dir(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return value


def collapse(roots):
    """Mark queued exact-duplicate diagnoses `superseded`. Returns the ids marked.

    OPS-0010 soak. Nothing is deleted or rewritten: the payload stays in the queue, the
    lifecycle file names the representative it defers to, and an acknowledgment that
    later arrives for it still records `delivered`. Mail with no `duplicate_key` — every
    producer but diagnose — is never touched, so distinct undelivered mail stays pending.
    """
    groups = {}
    for path in sorted(roots.state_path("queue").iterdir()) \
            if roots.state_path("queue").exists() else ():
        if path.name.startswith("."):
            continue
        try:
            env = read(path)
            state = lifecycle(roots, env.message_id)["state"]
            key = duplicate_key(env.destination, env.message_id, env.provenance,
                                env.payload_path.read_bytes())
        except (ValueError, OSError, TypeError):
            continue          # `pending` reports corrupt entries; collapse only skips them
        if key:
            groups.setdefault(key, []).append((env.created_at, env.message_id, state))
    marked = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = representative([m[:2] for m in members],
                              {m[1] for m in members if m[2] == "delivered"})
        for _created, message_id, state in members:
            if message_id != keep and state in ("queued", "published"):
                mark(roots, message_id, "superseded", superseded_by=keep)
                marked.append(message_id)
    return marked


def pending(roots, on_error=None):
    """Unacknowledged envelopes; optional callback(path, error) reports corrupt entries.

    Without a callback corruption raises. Directory enumeration faults always raise:
    an unreadable queue must never look empty, even in a best-effort worker cycle.
    A `superseded` duplicate is not pending: its representative carries its content.
    An `undeliverable` message is not pending either — retrying cannot deliver it — but it
    is counted by `undeliverable()`, never dropped from sight.
    """
    directory = roots.state_path("queue")
    result = []
    if not directory.exists():
        return result
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            envelope = read(path)
            if identity_key(envelope.message_id) != path.name:
                raise ValueError(f"queue identity differs from directory: {path}")
            if lifecycle(roots, envelope.message_id)["state"] not in SETTLED:
                result.append(envelope)
        except (ValueError, OSError, TypeError) as exc:
            if on_error is None:
                raise
            on_error(path, exc)
    return result


def undeliverable(roots):
    """[(envelope, reason_code)] the recipient declared it cannot deliver.

    Best-effort by design: an entry `pending` cannot read is already reported there as
    corrupt, so it is skipped here rather than counted twice."""
    directory = roots.state_path("queue")
    result = []
    if not directory.exists():
        return result
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            envelope = read(path)
            state = lifecycle(roots, envelope.message_id)
        except (ValueError, OSError, TypeError):
            continue
        if state["state"] == "undeliverable":
            reason = (state.get("acknowledgment") or {}).get("reason") or {}
            result.append((envelope, str(reason.get("code") or "unknown")))
    return result
