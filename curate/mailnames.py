"""Mail names and edit-ids for GENERATED briefs — one rule every producer shares.

WHY THIS EXISTS. The receiver (`maildelivery.deliver_one`) delivers a brief whose
filename is already taken ONLY when the brief carries an `edit-id`: the edit-id is what
proves two same-named briefs are different briefs. A brief with no edit-id that meets a
taken name is refused, never acknowledged, and retried forever. On the Runner that grew
pending mail from 49 to 154, because several producers named their mail by the DAY and
wrote no edit-id — every deploy escalation after the first of the day, every repeated
adoption failure, every refresh of a standing adopt-runner condition.

TWO RULES, both derived from the message id so a retry of the same message stays the
same message (`mailqueue.enqueue` refuses one identity with different content):

- `mail_name`: the producer's readable name, time-qualified to the second (UTC) and
  suffixed with a short digest of the message id. Two messages never share a name.
- `edit_id` / `ensure_edit_id`: every brief carries an `edit-id`, written as the YAML
  frontmatter line `edit-id: <id>` — the form `deploy/diagnose.py` and the deploy
  receipts already write, and the first form `deliver.brief_edit_id` reads.

THE LOCAL NAME IS NOT THE MAIL NAME. A producer may keep a stable local file (a standing
condition note, today's escalation) — that is a view for whoever reads the host. Only the
name the mail carries has to be unique, so these helpers are applied at the enqueue.

Stdlib only, and importing nothing from `curate/`: `deliver` imports `outbox`, which
imports this, so the two header regexes are restated here rather than imported
(tests/test_mailnames.py pins them to `deliver.brief_edit_id`).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import PurePath
import re
import uuid

# Restated from curate/deliver.py (`_YAML_EDIT_ID`, `_MD_EDIT_ID`) — see module docstring.
_YAML_EDIT_ID = re.compile(r"^edit-id:\s*(\S+)\s*$", re.M)
_MD_EDIT_ID = re.compile(r"^\s*[-*]\s*\*\*Edit ID:\*\*\s*(\S+)", re.M)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def digest(message_id: str, n: int = 12) -> str:
    """The first `n` hex digits of sha256(message_id)."""
    return hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()[:n]


def new_message_id() -> str:
    """A fresh message identity for an EVENT that is never retried under the same id."""
    return str(uuid.uuid4())


def utc_stamp(when=None) -> str:
    """`YYYYMMDDTHHMMSSZ` for `when` (a datetime or ISO string) or now, in UTC."""
    if when is None:
        when = datetime.now(timezone.utc)
    elif isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _label(filename: str) -> str:
    stem = PurePath(filename or "").stem if filename else ""
    return _UNSAFE.sub("-", stem).strip("-.") or "mail"


def mail_name(filename: str, message_id: str, when=None) -> str:
    """`<stem>-<UTC stamp>-<digest8><suffix>`: the producer's name, made unique."""
    suffix = PurePath(filename).suffix or ".md"
    return f"{_label(filename)}-{utc_stamp(when)}-{digest(message_id, 8)}{suffix}"


def edit_id(message_id: str, filename: str = "") -> str:
    """`<stem>-<digest12>` — deterministic in (filename, message_id), one token."""
    return f"{_label(filename)}-{digest(message_id)}"


def brief_edit_id(text: str):
    """The brief's declared edit-id, read exactly as `deliver.brief_edit_id` reads it."""
    for pat in (_YAML_EDIT_ID, _MD_EDIT_ID):
        m = pat.search(text or "")
        if m:
            return m.group(1).strip().rstrip(".")
    return None


def ensure_edit_id(text: str, message_id: str, filename: str = "") -> str:
    """`text` unchanged if it declares an edit-id; otherwise with one derived from
    `message_id`, added as the last line of its YAML frontmatter, or as a new
    frontmatter block when it has none (a brief with no frontmatter already routes to
    a person, and a frontmatter holding only `edit-id` routes the same way)."""
    if brief_edit_id(text):
        return text
    line = f"edit-id: {edit_id(message_id, filename)}"
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end] + "\n" + line + text[end:]
    return f"---\n{line}\n---\n\n{text}"


def identity(filename: str, text: str, message_id=None, when=None):
    """`(message_id, mail_filename, text_with_edit_id)` for one generated brief."""
    message_id = message_id or new_message_id()
    return (message_id, mail_name(filename, message_id, when),
            ensure_edit_id(text, message_id, filename))
