"""Deliver pinned remote mail snapshots and acknowledge verified content.

Transport refs are data sources, never executable instructions or updates to main.
A receive ledger and content comparison make retries after a crash safe.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import deliver
import mailqueue
import mailtransport
import scrub


class DeliveryError(ValueError):
    pass


class Undeliverable(DeliveryError):
    """A refusal no retry can repair: the MESSAGE would have to change to succeed.

    `undeliverable` is the reason code `cycle` declares and acknowledges. Subclasses
    `DeliveryError`, so a direct caller of `deliver_one` sees exactly the refusal it
    always saw; only the unattended cycle turns it into a terminal negative ack.
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.undeliverable = code


# WHICH REFUSALS ARE TERMINAL. Retrying forever was the failure: a message no retry can
# repair sat in the sender's pending set, retried every cycle, while health said `ok`.
#
# IMMEDIATE — the message itself must change, so the first refusal is final:
#   malformed        apply-mode header routes it to nobody (deliver.MalformedBrief)
#   name-taken       its filename is held and it carries no edit-id to rename by
#   held-mismatch    the recipient holds its edit-id with different content
#   identity-reused  this message id was already received with different content
#   unsafe-filename  its filename cannot be written safely
#   scrub-refused    the scrub gate refuses its content
# AFTER A THRESHOLD — a registry fix could repair it, so it is only declared once the
# same refusal has held for `undeliverable_after_attempts` cycles AND
# `undeliverable_after_seconds`:
#   unroutable       no mailboxes.json row, or the row says `reachable: false`
#   retired          the row resolves into the retired read-federation-direct inbox
# NEVER — everything else stays retried: `elsewhere` (another host delivers it),
# `unreachable` (repo-paths.local cannot locate it), a missing mailbox directory, a
# tracked mailbox, an unreadable mailbox tree, a missing checker, locks, IO and git
# faults, and transport integrity refusals (hash or wire-path mismatch — a sender bug,
# which must stay loud in `errors`, not be settled by an ack).
#
# Re-driving a declared message after a fix: delete its `undeliverable.json` under
# `received/`. A later `delivered` ack outranks `undeliverable` on the sender.
THRESHOLD_CODES = ("unroutable", "retired")

# What travels on the wire is the code and this fixed sentence, never exception text:
# a local path or a credential in an error message must not be published to a peer.
REASONS = {
    "malformed": "the brief's apply-mode header routes it to no recipient",
    "name-taken": "the recipient holds a different file of this name and the brief has no edit-id",
    "held-mismatch": "the recipient holds this edit-id with different content",
    "identity-reused": "this message id was already received with different content",
    "unsafe-filename": "the filename cannot be written safely",
    "scrub-refused": "the scrub gate refused the payload",
    "unroutable": "no routable mailbox row for the recipient",
    "retired": "the recipient row resolves to a retired inbox",
}
_CODE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_path(root: Path, *parts: str) -> Path:
    root = Path(root).resolve()
    result = root.joinpath(*parts)
    if result.resolve() != result or root not in result.parents:
        raise DeliveryError("receive state must not traverse a symbolic link")
    return result


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".receive-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _json(path: Path, value: dict) -> None:
    _atomic(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def _identity(roots, source_ref: str, envelope: dict) -> tuple[Path, dict]:
    message_id = envelope["message_id"]
    directory = _safe_path(roots.state_root, "received", _digest(source_ref),
                           _digest(message_id))
    identity = {"source_ref": source_ref, "message_id": message_id,
                "sha256": envelope["sha256"],
                "destination": envelope["destination"]}
    return directory, identity


def _acknowledge(roots, identity: dict, reason: dict | None = None) -> Path:
    """Queue an acknowledgment; publishing it is the single worker's responsibility.

    With `reason` it is a NEGATIVE ack: `outcome: undeliverable`. The payload is built
    only from stored fields, so a re-ack on a later cycle is the same queue identity."""
    ack = {"schema_version": 1, "kind": "delivery-ack", **identity,
           "outcome": "delivered"}
    if reason is not None:
        ack.update(outcome="undeliverable",
                   reason={"code": reason["code"], "detail": reason["detail"]})
    payload = json.dumps(ack, sort_keys=True) + "\n"
    key = _digest(payload)
    return mailqueue.enqueue(roots, "transport-ack", "ack-" + key + ".json", payload,
                             message_id="ack:" + key,
                             provenance={"kind": "delivery-ack"})


def _apply_ack(roots, source_ref: str, envelope: dict, payload: bytes) -> str:
    try:
        ack = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise DeliveryError("invalid acknowledgment JSON") from exc
    if not isinstance(ack, dict) or ack.get("schema_version") != 1 or \
            ack.get("kind") != "delivery-ack" or \
            ack.get("outcome") not in ("delivered", "undeliverable"):
        raise DeliveryError("invalid acknowledgment outcome or version")
    if ack["outcome"] == "undeliverable":
        reason = ack.get("reason")
        if not isinstance(reason, dict) or not isinstance(reason.get("code"), str) or \
                not _CODE.match(reason["code"]) or not isinstance(reason.get("detail"), str):
            raise DeliveryError("undeliverable acknowledgment has no valid reason")
    if envelope.get("destination") != "transport-ack":
        raise DeliveryError("acknowledgment must be addressed to transport-ack")
    owned = roots.transport["owned_ref"]
    owned = owned if owned.startswith("refs/") else "refs/heads/" + owned
    if ack.get("source_ref") != owned:
        return "elsewhere"
    message_id = ack.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise DeliveryError("acknowledgment has no message identity")
    item_dir = roots.state_path("queue", _digest(message_id))
    if not item_dir.exists():
        return "unmatched-ack"
    original = mailqueue.read(item_dir)
    if ack.get("sha256") != original.sha256 or \
            ack.get("destination") != original.destination:
        raise DeliveryError("acknowledgment does not match queued content and recipient")
    expected = roots.transport.get("recipient_refs", {}).get(original.destination)
    if expected and not expected.startswith("refs/"):
        expected = "refs/heads/" + expected
    if not expected and ack["outcome"] == "undeliverable":
        # A recipient nobody routes (no mailboxes.json row, so no derived recipient ref)
        # can only ever be answered negatively, and only by a peer we already read. A
        # positive ack still needs the configured ref; a forged negative one can only
        # make a message VISIBLE as undeliverable, and a real delivery still outranks it.
        inputs = {ref if ref.startswith("refs/") else "refs/heads/" + ref
                  for ref in roots.transport.get("input_refs", [])}
        expected = source_ref if source_ref in inputs else None
    if not expected or expected != source_ref:
        raise DeliveryError("acknowledgment source is not configured for this recipient")
    if original.provenance.get("kind") == "delivery-ack":
        raise DeliveryError("acknowledgments are not acknowledged recursively")
    if mailqueue.lifecycle(roots, original.message_id).get("state") == "queued":
        raise DeliveryError("cannot acknowledge mail not yet confirmed published")
    # `undeliverable` ranks below `delivered`: a later real delivery still wins, and a
    # stale negative ack can never demote a message the recipient already confirmed.
    mailqueue.mark(roots, original.message_id, ack["outcome"], acknowledgment=ack,
                   acknowledgment_ref=source_ref)
    return "acknowledged" if ack["outcome"] == "delivered" else "acknowledged-undeliverable"


def _thresholds(roots) -> tuple[int, int]:
    attempts = roots.transport.get("undeliverable_after_attempts", 10)
    seconds = roots.transport.get("undeliverable_after_seconds", 86400)
    for key, value, low, high in (("undeliverable_after_attempts", attempts, 1, 100000),
                                  ("undeliverable_after_seconds", seconds, 0, 30 * 86400)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise DeliveryError(f"{key} must be an integer between {low} and {high}")
    return attempts, seconds


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _declared(roots, source_ref: str, envelope: dict):
    """The stored declaration for THIS content, or None. A changed payload under the
    same id is a new message and is attempted afresh."""
    directory, identity = _identity(roots, source_ref, envelope)
    path = _safe_path(roots.state_root, str(directory.relative_to(roots.state_root)),
                      "undeliverable.json")
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if any(record.get(key) != value for key, value in identity.items()):
        return None
    return record


def declare_failure(roots, source_ref: str, envelope: dict, code: str, detail: str,
                    *, now=None) -> bool:
    """Record one permanent-class refusal; True once it is declared undeliverable.

    An immediate code is declared on its first refusal. A threshold code is declared
    only after the SAME code has refused for enough attempts over enough time; any
    other code, or changed content, restarts the count."""
    now = now or _now()
    directory, identity = _identity(roots, source_ref, envelope)
    directory = _safe_path(roots.state_root, str(directory.relative_to(roots.state_root)))
    if code in THRESHOLD_CODES:
        attempts_needed, seconds_needed = _thresholds(roots)
        failure_path = directory / "failure.json"
        record = json.loads(failure_path.read_text()) if failure_path.exists() else {}
        if record.get("code") != code or \
                any(record.get(key) != value for key, value in identity.items()):
            record = {**identity, "code": code, "first_seen": now.isoformat(), "attempts": 0}
        record["attempts"] += 1
        record["last_seen"] = now.isoformat()
        _json(failure_path, record)
        age = (now - datetime.fromisoformat(record["first_seen"])).total_seconds()
        if record["attempts"] < attempts_needed or age < seconds_needed:
            return False
    reason = {"code": code, "detail": REASONS.get(code, code)}
    _json(directory / "undeliverable.json",
          {**identity, "reason": reason, "local_detail": detail[:2000],
           "declared_at": now.isoformat()})
    _acknowledge(roots, identity, reason)
    return True


def classify(exc) -> str | None:
    """The terminal reason code for a refusal, or None for one that stays retried."""
    code = getattr(exc, "undeliverable", None)
    if isinstance(code, str):
        return code
    if isinstance(exc, deliver.MalformedBrief):
        return "malformed"
    if isinstance(exc, deliver.RecipientUnavailable) and \
            getattr(exc, "outcome", None) in THRESHOLD_CODES:
        return exc.outcome
    return None


def deliver_one(roots, source_ref: str, source_commit: str, envelope: dict,
                payload: bytes, *, mailboxes=None, repos=None) -> str:
    """One checked snapshot object. Called while holding the worker's receive lock."""
    if _sha(payload) != envelope["sha256"]:
        raise DeliveryError("payload hash does not match envelope")
    if envelope.get("provenance", {}).get("kind") == "delivery-ack":
        return _apply_ack(roots, source_ref, envelope, payload)

    directory, identity = _identity(roots, source_ref, envelope)
    ledger = _safe_path(roots.state_root, str(directory.relative_to(roots.state_root)),
                        "receipt.json")
    # Already declared undeliverable: re-queue the same negative ack (idempotent) and do
    # not attempt the recipient again. Mirrors `already-delivered` below.
    declared = _declared(roots, source_ref, envelope)
    if declared is not None:
        _acknowledge(roots, identity, declared["reason"])
        return "already-undeliverable"
    if ledger.exists():
        record = json.loads(ledger.read_text())
        if any(record.get(key) != value for key, value in identity.items()):
            raise Undeliverable("message identity reused with different content",
                                "identity-reused")
        _acknowledge(roots, identity)
        return "already-delivered"

    filename = envelope["filename"]
    if not filename or Path(filename).name != filename or filename in (".", "..") \
            or "\\" in filename or "\0" in filename:
        raise Undeliverable("unsafe incoming filename", "unsafe-filename")
    staged = _safe_path(roots.state_root, str(directory.relative_to(roots.state_root)),
                        "payload", filename)
    if staged.exists() and staged.read_bytes() != payload:
        raise DeliveryError("staged message content changed")
    if not staged.exists():
        _atomic(staged, payload)

    # Scrub is the same gate the send path uses; remote storage does not imply clearance.
    if not scrub.gate(staged, force=False):
        raise Undeliverable("incoming payload refused by scrub gate", "scrub-refused")

    def attempt(path):
        try:
            return deliver.deliver(envelope["destination"], path,
                                   mailboxes=mailboxes, repos=repos, production_roots=roots)
        except deliver.AlreadyHeld as held:
            # An ID in a changelog alone cannot prove this exact payload arrived. A moved
            # inbox file can, and covers the crash between copy and recording receipt.json.
            where = Path(held.where)
            if not where.is_file() or where.is_symlink() or \
                    _sha(where.read_bytes()) != envelope["sha256"]:
                raise Undeliverable("recipient holds the ID but matching content is unverified",
                                    "held-mismatch")
            return where

    try:
        dest = attempt(staged)
    except deliver.NameTaken as taken:
        # OPS-0010 soak. Only the LABEL collides: the edit-id is not held, so this is a
        # different brief that happens to share a name — diagnose names every bundle of a
        # day alike. Refusing here left it with no receipt and no ack, retried forever,
        # and the sender's pending count only grew. A brief carrying its own edit-id is
        # delivered beside the other under the next free name; one without an edit-id
        # keeps the refusal, because nothing then proves the two briefs are different.
        if not deliver.brief_edit_id(payload.decode("utf-8", "replace")):
            taken.undeliverable = "name-taken"    # permanent: nothing to rename it by
            raise
        renamed = _safe_path(roots.state_root, str(directory.relative_to(roots.state_root)),
                             "payload", deliver.free_name(filename, taken.taken))
        if not renamed.exists() or renamed.read_bytes() != payload:
            _atomic(renamed, payload)
        if not scrub.gate(renamed, force=False):
            raise Undeliverable("incoming payload refused by scrub gate", "scrub-refused")
        dest = attempt(renamed)
    if not Path(dest).is_file() or _sha(Path(dest).read_bytes()) != envelope["sha256"]:
        raise DeliveryError("recipient readback failed")
    _json(ledger, {**identity, "source_commit": source_commit,
                   "outcome": "delivered"})
    _acknowledge(roots, identity)
    return "delivered"


def _legacy(path: str, payload: bytes, source_ref: str) -> dict:
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "outbox" or not parts[1].startswith("to-"):
        raise DeliveryError("unsupported legacy queue path")
    destination = parts[1][3:]
    if not destination:
        raise DeliveryError("legacy message has no recipient")
    content_hash = _sha(payload)
    return {"schema_version": 1,
            "message_id": "legacy:" + _digest(source_ref + "\0" + path + "\0" + content_hash),
            "destination": destination, "filename": parts[2], "sha256": content_hash,
            "provenance": {"kind": "legacy-outbox", "source_path": path},
            "created_at": None}


def _received(roots, source_ref: str, envelope: dict) -> bool:
    directory, _identity_ = _identity(roots, source_ref, envelope)
    return (directory / "receipt.json").exists()


def _stand_ins(roots, source_ref: str, commit: str, objects: list) -> tuple[dict, dict]:
    """({message_id: representative_id}, {path: blob}) for one pinned snapshot.

    OPS-0010 soak. The sender's ref is additive — a message it has marked superseded
    stays on the wire — so the receiver applies the SAME rule the sender's
    `mailqueue.collapse` does, from what it can see: one member of each duplicate group
    is delivered, the rest are skipped without error and without an ack. The sender stops
    counting them pending on its own. Blobs read here are returned so the main loop does
    not pay two git processes per message twice.
    """
    groups, blobs = {}, {}
    for path, mode, _oid in objects:
        if not path.startswith("messages/") or str(mode) not in ("100644", "100755"):
            continue
        try:
            blobs[path] = mailtransport.read_blob(roots, commit, path)
            envelope, payload = mailtransport.decode_message(blobs[path])
            key = mailqueue.duplicate_key(envelope["destination"], envelope["message_id"],
                                          envelope.get("provenance"), payload)
        except (ValueError, RuntimeError, OSError, KeyError):
            continue          # the main loop reports it against its own path
        if key:
            groups.setdefault(key, []).append(envelope)
    stand_in = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        received = {e["message_id"] for e in members if _received(roots, source_ref, e)}
        keep = mailqueue.representative(
            [(e["created_at"], e["message_id"]) for e in members], received)
        for e in members:
            if e["message_id"] != keep and e["message_id"] not in received:
                stand_in[e["message_id"]] = keep
    return stand_in, blobs


def cycle(roots, *, mailboxes=None, repos=None) -> dict:
    """Consume configured snapshots. Individual bad messages do not suppress siblings."""
    result = {"snapshots": [], "outcomes": [], "errors": [], "busy": False}
    limit = int(roots.transport.get("max_receive_messages", 1000))
    if limit < 1:
        raise DeliveryError("max_receive_messages must be positive")
    try:
        with mailtransport.transport_lock(roots):
            mailtransport.claim_repository(roots)
            count = 0
            cursor_path = _safe_path(roots.state_root, "runtime", "receive-cursor.json")
            cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
            refs = roots.transport.get("input_refs", [])
            start = int(cursor.get("next_ref", 0)) % max(len(refs), 1)
            for index in list(range(start, len(refs))) + list(range(start)):
                source_ref = refs[index]
                source_ref = source_ref if source_ref.startswith("refs/") else "refs/heads/" + source_ref
                try:
                    commit = mailtransport.fetch_snapshot(roots, source_ref)
                    if not commit:
                        result["errors"].append({"ref": source_ref, "error": "ref unavailable"})
                        continue
                    result["snapshots"].append({"ref": source_ref, "commit": commit})
                    objects = mailtransport.list_blobs(roots, commit, "messages/")
                    objects += [entry for entry in mailtransport.list_blobs(roots, commit, "outbox/")
                                if entry[0].startswith("outbox/to-")]
                    objects.sort(key=lambda entry: entry[0])
                    previous = cursor.get(source_ref, "")
                    objects = ([entry for entry in objects if entry[0] > previous] +
                               [entry for entry in objects if entry[0] <= previous])
                    stand_in, blobs = _stand_ins(roots, source_ref, commit, objects)
                except (ValueError, RuntimeError, OSError) as exc:
                    result["errors"].append({"ref": source_ref, "error": str(exc)})
                    continue
                processed_here = 0
                for path, mode, _oid in objects:
                    if count >= limit:
                        cursor["next_ref"] = ((index + 1) if processed_here else index) % len(refs)
                        _json(cursor_path, cursor)
                        result["errors"].append({"ref": source_ref, "error": "receive limit reached; remaining mail retained"})
                        return result
                    count += 1
                    processed_here += 1
                    envelope = None
                    try:
                        if str(mode) not in ("100644", "100755"):
                            raise DeliveryError("remote mail must be a regular blob")
                        payload = blobs.pop(path, None) or mailtransport.read_blob(roots, commit, path)
                        if path.startswith("messages/"):
                            envelope, payload = mailtransport.decode_message(payload)
                            if path != "messages/" + _digest(envelope["message_id"]) + ".json":
                                raise DeliveryError("wire path does not match message identity")
                        else:
                            envelope = _legacy(path, payload, source_ref)
                        if envelope["message_id"] in stand_in:
                            result["outcomes"].append({
                                "ref": source_ref, "message_id": envelope["message_id"],
                                "outcome": "superseded",
                                "representative": stand_in[envelope["message_id"]]})
                        else:
                            outcome = deliver_one(roots, source_ref, commit, envelope, payload,
                                                  mailboxes=mailboxes, repos=repos)
                            result["outcomes"].append({"ref": source_ref,
                                                       "message_id": envelope["message_id"],
                                                       "outcome": outcome})
                    except (ValueError, RuntimeError, OSError, KeyError) as exc:
                        # `envelope` is None when the blob never decoded: no identity to
                        # declare against, so integrity refusals stay plain errors.
                        code = classify(exc) if envelope is not None else None
                        declared = False
                        if code is not None:
                            try:
                                declared = declare_failure(roots, source_ref, envelope,
                                                           code, str(exc))
                            except (ValueError, OSError) as record_exc:
                                result["errors"].append({"ref": source_ref, "path": path,
                                                         "error": "cannot record undeliverable: "
                                                                  + str(record_exc)})
                        if declared:
                            result["outcomes"].append({"ref": source_ref, "path": path,
                                                       "message_id": envelope["message_id"],
                                                       "outcome": "undeliverable",
                                                       "reason": code, "detail": str(exc)})
                        elif isinstance(exc, (deliver.RecipientUnavailable,
                                              deliver.MalformedBrief)):
                            result["outcomes"].append({"ref": source_ref, "path": path,
                                                       "outcome": getattr(exc, "outcome", "malformed"),
                                                       "detail": str(exc)})
                        else:
                            result["errors"].append({"ref": source_ref, "path": path,
                                                     "error": str(exc)})
                    # Rotate even on a malformed message: it remains visible and retryable,
                    # but neither old receipts nor one poison entry may starve later mail.
                    cursor[source_ref] = path
                    cursor["next_ref"] = (index + 1) % len(refs)
                    _json(cursor_path, cursor)
    except mailtransport.TransportBusy:
        result["busy"] = True
    return result
