#!/usr/bin/env python3
"""Serialized, restartable publication of the external federation mail queue.

The repository is a dedicated bare transport. Its owned branch contains immutable
messages only; incoming refs are read as pinned snapshots. A private index keeps a
worker commit independent of any existing index. The lock covers every repository
operation, including readers' fetches, and is released by the kernel on process death.

Publication is confirmed by reading the remote ref back. Local queue payloads remain
until delivery validates an acknowledgment, and this module never marks delivery.
Delivered payloads and receipts remain the provenance archive; retention never expires
undelivered work. Configurable batch/head-size limits report backpressure while keeping
the queue intact. History compaction is a separate, explicit maintenance operation.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile


class TransportError(ValueError):
    """A transport fault; queued payloads remain available for another cycle."""


class TransportBusy(TransportError):
    """Another worker owns this transport repository."""


def _canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _limit(roots, name, default, ceiling):
    value = roots.transport.get(name, default)
    if type(value) is not int or not 1 <= value <= ceiling:
        raise TransportError(f"invalid transport limit {name}")
    return value


def _ref(value):
    if not isinstance(value, str):
        raise TransportError("transport ref must be a string")
    ref = value if value.startswith("refs/") else "refs/heads/" + value
    tail = ref.removeprefix("refs/heads/")
    if (not ref.startswith("refs/heads/") or not tail or ".." in ref or "@{" in ref
            or any(ord(c) < 33 or ord(c) == 127 or c in "~^:?*[\\" for c in ref)
            or ref.endswith(("/", ".")) or "//" in ref
            or any(p.startswith(".") or p.endswith(".lock") for p in ref.split("/"))):
        raise TransportError("invalid transport branch ref")
    return ref


def owned_ref(roots):
    ref = _ref(roots.transport.get("owned_ref"))
    if ref in ("refs/heads/main", "refs/heads/master"):
        raise TransportError("a production transport cannot own a trunk branch")
    return ref


def message_path(message_id):
    if not isinstance(message_id, str) or not message_id or len(message_id) > 1024:
        raise TransportError("invalid message identity")
    return "messages/" + hashlib.sha256(message_id.encode("utf-8")).hexdigest() + ".json"


def encode_message(envelope, payload):
    import scrub

    meta = envelope.as_dict() if hasattr(envelope, "as_dict") else dict(envelope)
    wire = {**meta, "payload_b64": base64.b64encode(payload).decode("ascii")}
    blob = _canonical(wire)
    decode_message(blob)
    if scrub.findings(payload.decode("utf-8") + "\n" + _canonical(meta).decode("utf-8"),
                      meta["filename"]):
        raise TransportError("queued message failed the publication scrub check")
    return blob


def decode_message(blob):
    """Return (validated metadata, exact payload bytes), without executing content."""
    if not isinstance(blob, bytes) or len(blob) > 24 * 1024 * 1024:
        raise TransportError("invalid or oversized wire message")
    try:
        wire = json.loads(blob)
        if not isinstance(wire, dict) or type(wire.get("schema_version")) is not int \
                or wire["schema_version"] != 1:
            raise ValueError("schema")
        message_path(wire.get("message_id"))
        filename = wire.get("filename")
        if (not isinstance(filename, str) or not filename or filename in (".", "..")
                or "/" in filename or "\\" in filename or "\x00" in filename
                or any(ord(c) < 32 for c in filename)):
            raise ValueError("filename")
        if not isinstance(wire.get("destination"), str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", wire["destination"]):
            raise ValueError("destination")
        if not isinstance(wire.get("provenance"), dict):
            raise ValueError("provenance")
        if not isinstance(wire.get("created_at"), str) or not wire["created_at"]:
            raise ValueError("timestamp")
        payload = base64.b64decode(wire.pop("payload_b64"), validate=True)
        if hashlib.sha256(payload).hexdigest() != wire.get("sha256"):
            raise ValueError("hash")
        payload.decode("utf-8")
        return wire, payload
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise TransportError("malformed message envelope or payload hash mismatch") from exc


def _environment(extra=None):
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true")
    env.update(extra or {})
    return env


def _run(roots, args, *, data=None, extra_env=None, network=False, check=True):
    timeout = _limit(roots, "network_timeout_seconds", 30, 120) if network else 30
    cmd = ["git", "-C", str(roots.transport_root), "-c", "gc.auto=0",
           "-c", "maintenance.auto=false", "-c", "commit.gpgsign=false", *args]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=_environment(extra_env),
                                start_new_session=True)
        try:
            out, _err = proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise TransportError("transport Git operation timed out") from exc
        except BaseException:
            # Full-cycle deadlines and user interrupts must not leave Git/SSH or a
            # credential helper operating after the worker releases its lock.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise
    except OSError as exc:
        raise TransportError("transport Git operation could not start") from exc
    if check and proc.returncode:
        # Raw git errors can contain credentials embedded in a remote URL.
        raise TransportError(f"transport Git operation failed (exit {proc.returncode})")
    return proc.returncode, out


def _outside_released_code(roots, label):
    """One owner for the rule; the lock is placed before the repository is validated."""
    root = Path(roots.transport_root).resolve()
    code = Path(roots.code_root).resolve()
    if root == code or code in root.parents:
        raise TransportError(f"transport {label} cannot be inside released code")


@contextmanager
def transport_lock(roots, blocking=False):
    """The publisher and remote readers must acquire this lock before using the repo."""
    # Placing it beside the repository also fences two configurations accidentally
    # naming the same transport while naming different state directories. That is a
    # write, so the release-tree rule has to hold here and not only in ensure_repository:
    # otherwise refusing the repository still leaves a lock file inside the sealed tree.
    _outside_released_code(roots, "lock")
    root = Path(roots.transport_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    path = root.parent / ("." + root.name + ".mail-worker.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise TransportError("cannot open transport worker lock") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise TransportBusy("another mail worker is running") from exc
        yield
    finally:
        os.close(fd)


def ownership_marker(roots):
    return Path(roots.transport_root) / "poga-mail-transport.json"


def repoint_ownership(roots, *, from_ref):
    """Move an existing ownership marker from `from_ref` onto the configured owned ref.

    THE MARKER IS A REFUSAL AND THE REFUSAL IS RIGHT — it is what stops this program
    adopting a transport repository another installation owns. But it binds the owned ref,
    so a DELIBERATE change of that ref turns the guard against the migration making the
    change. WI-0418 moves the transport off `refs/heads/runner/mail`; the Runner's marker
    was written naming it by the v7.5.2 apply, and without this the repository refuses with
    "transport ownership differs from configuration" on every run, forever, with no route
    out but a hand edit on the production host.

    NARROW, so it stays a migration step and never becomes an adoption: the remote must
    already match, the marker must name exactly the ref being left, and the configured ref
    must actually be a different one. Every other mismatch falls through untouched to
    `ensure_repository`, where it still refuses — that is the case that means another owner
    and this must not launder it.

    Caller holds `transport_lock`. Returns True only if the marker was rewritten."""
    marker = ownership_marker(roots)
    if not marker.exists() or marker.is_symlink():
        return False
    remote = roots.transport.get("remote")
    try:
        declared = json.loads(marker.read_bytes())
    except (OSError, ValueError):
        return False        # unreadable is ensure_repository's refusal to report, not ours
    if not isinstance(declared, dict):
        return False
    leaving = _ref(from_ref)
    target = owned_ref(roots)
    if (declared.get("remote") != remote
            or not isinstance(declared.get("owned_ref"), str)
            or _ref(declared["owned_ref"]) != leaving
            or target == leaving):
        return False
    binding = {"schema_version": 1, "remote": remote, "owned_ref": target}
    handle, temporary = tempfile.mkstemp(prefix=".poga-mail-transport-", dir=marker.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_canonical(binding))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def claim_repository(roots):
    """`ensure_repository`, preceded by the one repoint a configuration can force.

    EVERY OPENER OF THE REPOSITORY GOES THROUGH HERE, and that is the fix (WI-0425). The
    repoint first shipped inside the migration's one-time `provision` phase, so on the one
    host it was written for — the Runner, provisioned on 2026-09-21 — the phase
    gate skipped it and the worker refused "ownership differs from configuration" on every
    cycle after its config had been moved off the channel branch. A reconciliation between
    the marker and the configuration has to run wherever the two can disagree, which is
    every time the repository is opened, not once per host.

    The predicate is `repoint_ownership`'s and stays narrow: only a marker naming exactly
    the ADR-0135 channel branch is moved. Every other mismatch still reaches the refusal.

    Caller holds `transport_lock`. Returns True only if the marker was rewritten."""
    import channel      # the channel owns the name; spelling it again here would drift
    moved = repoint_ownership(roots, from_ref=channel.BRANCH)
    ensure_repository(roots)
    return moved


def ensure_repository(roots):
    """Initialize or validate the dedicated bare repository, under transport_lock."""
    root = Path(roots.transport_root)
    _outside_released_code(roots, "repository")
    remote = roots.transport.get("remote")
    if not isinstance(remote, str) or not remote or remote.startswith("-") \
            or any(ord(c) < 32 for c in remote):
        raise TransportError("an explicit transport remote is required")
    binding = {"schema_version": 1, "remote": remote, "owned_ref": owned_ref(roots)}
    marker = ownership_marker(roots)
    if root.exists() and root.is_symlink():
        raise TransportError("transport repository may not be a symlink")
    if marker.exists():
        # TransportError IS a ValueError, so raising the specific refusal inside this
        # handler made the handler swallow it: every mismatch reported the generic
        # "cannot validate", which reads as an unreadable file rather than another
        # owner. Read and compare are separate steps so each rule can be seen.
        if marker.is_symlink():
            raise TransportError("transport ownership marker may not be a symlink")
        try:
            declared = json.loads(marker.read_bytes())
        except (OSError, ValueError) as exc:
            raise TransportError("cannot validate transport ownership") from exc
        if declared != binding:
            raise TransportError("transport ownership differs from configuration")
    else:
        if root.exists() and any(root.iterdir()):
            raise TransportError("refusing to adopt a nonempty unowned transport repository")
        root.mkdir(parents=True, exist_ok=True)
        # The marker precedes init so interrupted initialization is restartable.
        with marker.open("xb") as stream:
            stream.write(_canonical(binding))
            stream.flush()
            os.fsync(stream.fileno())
    if not (root / "HEAD").exists():
        _run(roots, ["init", "--bare", "--initial-branch=mail-unborn", "--quiet"])
    if _run(roots, ["rev-parse", "--is-bare-repository"])[1].strip() != b"true":
        raise TransportError("mail transport requires a dedicated bare repository")
    rc, present = _run(roots, ["remote", "get-url", "origin"], check=False)
    if rc:
        _run(roots, ["remote", "add", "origin", remote])
    elif present.decode().strip() != remote:
        raise TransportError("transport origin differs from its configured remote")
    # Only a lock holder reaches here, so temporary indexes left by a killed worker
    # cannot belong to a live invocation. Durable refs and queued payloads are untouched.
    for path in root.glob("poga-mail-index-*"):
        if path.is_file() or path.is_symlink():
            path.unlink()


def _remote_head(roots, ref):
    out = _run(roots, ["ls-remote", "--refs", "origin", ref], network=True)[1]
    lines = out.decode("ascii").splitlines()
    if not lines:
        return None
    if len(lines) != 1:
        raise TransportError("remote ref has an ambiguous result")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40,64}", fields[0]):
        raise TransportError("remote ref has an invalid result")
    return fields[0]


def fetch_snapshot(roots, ref):
    """Fetch an explicitly configured ref and return its pinned commit, or None."""
    ref = _ref(ref)
    allowed = {owned_ref(roots), *(_ref(x) for x in roots.transport.get("input_refs", []))}
    if ref not in allowed:
        raise TransportError("input ref is not explicitly configured")
    head = _remote_head(roots, ref)
    if head is None:
        return None
    target = "refs/poga/incoming/" + hashlib.sha256(ref.encode()).hexdigest()
    _run(roots, ["fetch", "--no-tags", "--quiet", "origin", "+" + ref + ":" + target],
         network=True)
    return _run(roots, ["rev-parse", target + "^{commit}"])[1].decode().strip()


def list_blobs(roots, commit, prefix="messages/"):
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise TransportError("snapshot must be a pinned commit")
    out = _run(roots, ["ls-tree", "-r", "-z", commit, *(["--", prefix] if prefix else [])])[1]
    result = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        # Return modes so the delivery caller can explicitly reject symlinks.
        if kind not in ("blob", "commit"):
            raise TransportError("unexpected snapshot object type")
        result.append((path.decode("utf-8"), mode, oid))
    return result


def read_blob(roots, commit, path):
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit) or not isinstance(path, str) \
            or path.startswith("/") or ".." in path.split("/") or "\x00" in path:
        raise TransportError("invalid pinned blob address")
    address = commit + ":" + path
    size = int(_run(roots, ["cat-file", "-s", address])[1])
    maximum = _limit(roots, "max_wire_bytes", 2 * 1024 * 1024, 24 * 1024 * 1024)
    if size > maximum:
        raise TransportError("message exceeds configured wire size limit")
    return _run(roots, ["cat-file", "blob", address])[1]


def _ancestor(roots, ancestor, descendant):
    return _run(roots, ["merge-base", "--is-ancestor", ancestor, descendant], check=False)[0] == 0


def _local_head(roots, ref):
    rc, value = _run(roots, ["rev-parse", "--verify", "--quiet", ref], check=False)
    return value.decode().strip() if not rc else None


def _validate_tree(roots, commit):
    entries, total = {}, 0
    if commit is None:
        return entries, total
    rows = list_blobs(roots, commit, "")
    if len(rows) > _limit(roots, "max_head_messages", 10000, 100000):
        raise TransportError("owned ref exceeds configured retained message count")
    for path, mode, _oid in rows:
        if mode != "100644" or not re.fullmatch(r"messages/[0-9a-f]{64}\.json", path):
            raise TransportError("owned ref contains an unauthorized path or mode")
    if not rows:
        return entries, total
    # Batch Git reads keep an accumulated channel from requiring two subprocesses
    # per historical message on every scheduler tick. Check sizes before reading data.
    request = ("\n".join(oid for _path, _mode, oid in rows) + "\n").encode("ascii")
    checked = _run(roots, ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                   data=request)[1].decode("ascii").splitlines()
    if len(checked) != len(rows):
        raise TransportError("cannot inspect retained message objects")
    maximum = _limit(roots, "max_wire_bytes", 2 * 1024 * 1024, 24 * 1024 * 1024)
    sizes = []
    for line, (_path, _mode, oid) in zip(checked, rows):
        fields = line.split()
        if len(fields) != 3 or fields[:2] != [oid, "blob"]:
            raise TransportError("retained message object is missing or invalid")
        size = int(fields[2])
        if size > maximum:
            raise TransportError("message exceeds configured wire size limit")
        sizes.append(size)
        total += size
    if total > _limit(roots, "max_head_bytes", 256 * 1024 * 1024, 4 * 1024**3):
        raise TransportError("owned ref exceeds configured retained message bytes")
    carried = _run(roots, ["cat-file", "--batch"], data=request)[1]
    offset = 0
    for (path, _mode, oid), size in zip(rows, sizes):
        end = carried.find(b"\n", offset)
        if end < 0 or carried[offset:end] != f"{oid} blob {size}".encode("ascii"):
            raise TransportError("invalid retained message readback")
        offset = end + 1
        blob = carried[offset:offset + size]
        offset += size
        if carried[offset:offset + 1] != b"\n":
            raise TransportError("truncated retained message readback")
        offset += 1
        envelope, _payload = decode_message(blob)
        if path != message_path(envelope["message_id"]):
            raise TransportError("message path does not match its identity")
        entries[path] = blob
    return entries, total


def _commit(roots, parent, additions):
    # A unique index makes a stale/unrelated default index irrelevant. Stale private
    # files after SIGKILL are disposable; the durable commit/queue are the recovery data.
    fd, name = tempfile.mkstemp(prefix="poga-mail-index-", dir=roots.transport_root)
    os.close(fd)
    os.unlink(name)
    env = {"GIT_INDEX_FILE": name}
    try:
        _run(roots, ["read-tree", parent] if parent else ["read-tree", "--empty"], extra_env=env)
        for path, blob in additions.items():
            oid = _run(roots, ["hash-object", "-w", "--stdin"], data=blob)[1].decode().strip()
            _run(roots, ["update-index", "--add", "--cacheinfo", "100644," + oid + "," + path],
                 extra_env=env)
        tree = _run(roots, ["write-tree"], extra_env=env)[1].decode().strip()
        args = ["-c", "user.name=poga mail", "-c", "user.email=mail@localhost",
                "commit-tree", tree, *( ["-p", parent] if parent else [] )]
        commit = _run(roots, args, data=b"Publish queued federation mail\n")[1].decode().strip()
        _run(roots, ["update-ref", owned_ref(roots), commit, parent or ""])
        return commit
    finally:
        for path in (Path(name), Path(name + ".lock")):
            path.unlink(missing_ok=True)


def _checkpoint(stage):
    """No-op fault boundary used by process-crash rehearsals."""


def cycle(roots):
    """Publish a bounded batch. Never marks delivered or discards a queue payload."""
    import mailqueue

    result = {"status": "failed", "published": 0, "pending": None,
              "backpressure": False, "failure": None, "commit": None,
              "queue_errors": [], "pending_control": 0, "retained_control": 0}
    try:
        with transport_lock(roots):
            claim_repository(roots)
            def queue_error(path, exc):
                result["queue_errors"].append({"entry": path.name, "failure": str(exc)})

            pending = mailqueue.pending(roots, on_error=queue_error)
            controls = [env for env in pending if env.provenance.get("kind") == "delivery-ack"]
            published_controls = sum(mailqueue.lifecycle(roots, env.message_id)["state"] == "published"
                                     for env in controls)
            result["pending_control"] = len(controls) - published_controls
            result["retained_control"] = published_controls
            result["pending"] = len(pending) - published_controls + len(result["queue_errors"])
            result["backpressure"] = len(pending) > _limit(roots, "max_pending_messages", 10000, 1000000)
            ref = owned_ref(roots)
            remote = fetch_snapshot(roots, ref)
            local = _local_head(roots, ref)
            if local and remote and local != remote:
                if _ancestor(roots, local, remote):
                    _run(roots, ["update-ref", ref, remote, local])
                    local = remote
                elif not _ancestor(roots, remote, local):
                    raise TransportError("owned remote ref diverged; queued payloads preserved")
            elif remote and not local:
                _run(roots, ["update-ref", ref, remote, ""])
                local = remote
            entries, total = _validate_tree(roots, local)
            additions, selected = {}, []
            batch = _limit(roots, "max_batch_messages", 100, 10000)
            head_count_limit = _limit(roots, "max_head_messages", 10000, 100000)
            maximum = _limit(roots, "max_wire_bytes", 2 * 1024 * 1024, 24 * 1024 * 1024)
            head_limit = _limit(roots, "max_head_bytes", 256 * 1024 * 1024, 4 * 1024**3)
            # Already published entries are confirmed without consuming the batch;
            # old pending commits must be pushed even if no new payload is present.
            for env in pending:
                blob = encode_message(env, env.payload_path.read_bytes())
                path = message_path(env.message_id)
                if len(blob) > maximum:
                    raise TransportError("queued message exceeds configured wire size limit")
                if path in entries:
                    if entries[path] != blob:
                        raise TransportError("message identity changed content or metadata")
                    selected.append(env)
                    continue
                if len(additions) >= batch:
                    continue
                if total + len(blob) > head_limit or len(entries) + len(additions) >= head_count_limit:
                    result["backpressure"] = True
                    continue
                additions[path] = blob
                selected.append(env)
                total += len(blob)
            if additions:
                local = _commit(roots, local, additions)
                _checkpoint("committed")
            if local is not None and local != remote:
                # No force, implicit push target, merge, or rebase. A competing ref
                # update is a refusal with the queue and local commit preserved.
                _run(roots, ["push", "--quiet", "origin", local + ":" + ref], network=True)
                _checkpoint("pushed")
            confirmed = _remote_head(roots, ref)
            if local != confirmed:
                raise TransportError("remote publication could not be confirmed")
            for env in selected:
                mailqueue.mark(roots, env.message_id, "published", sha256=env.sha256,
                               remote_ref=ref, remote_commit=confirmed,
                               published_at=datetime.now(timezone.utc).isoformat())
            published_controls = sum(mailqueue.lifecycle(roots, env.message_id)["state"] == "published"
                                     for env in controls)
            result["pending_control"] = len(controls) - published_controls
            result["retained_control"] = published_controls
            result["pending"] = len(pending) - published_controls + len(result["queue_errors"])
            result.update(status="ok", published=len(selected), commit=confirmed,
                          last_publication=datetime.now(timezone.utc).isoformat()
                          if selected else None)
            if result["queue_errors"]:
                result.update(status="failed", failure="malformed local queue entries remain")
    except TransportBusy as exc:
        result.update(status="busy", failure=str(exc))
    except (TransportError, OSError, ValueError) as exc:
        result["failure"] = str(exc)
    return result
