"""Evidence that says when it was cut.

WI-0371. Six sites in this repo cut a captured verdict down to a window and handed
the window on as if it were the whole thing. That is not a formatting nit. A tail
keeps the END of the output, and when a run fails the part that says WHY is
frequently at the HEAD — so the surviving window is a *biased sample that reads as
"no errors."* The `curate/adopt-runner.py` path was worse than any single site: the
same text was cut at three separate hops, so the record at the far end was a tail of
a tail of a tail and nothing anywhere said so. One member's adoption note is the
recorded instance: a 400-char tail that began mid-token,
having already lost the traceback header that would have explained it.

THE RULE, one rule for all six sites: **no site may cut silently.** A cut either

  (a) keeps the full text beside the record — a file on disk, not a field — and the
      record names that file (`clip(..., full_text_at=...)`), or
  (b) states in the record that the text was cut and by how much (`clip`).

Route (b) is the floor and every site gets at least that much. Route (a) is one
argument away for a caller that already has a durable directory to write into; this
module deliberately does NOT write that file itself, because choosing a path, a
naming scheme and a retention policy is the caller's business and an artifact
directory nobody retires is a new operational problem, not a fix for this one.

Three properties this module makes structural rather than a convention each caller
has to remember:

  * **The marker sits on the side the text was lost from.** A head-cut announces
    itself ABOVE the surviving window, so a reader meets the words "6713 of 8213
    chars above" before the sentence that starts mid-token — rather than inferring
    the cut from the wreckage.
  * **`limit` counts the kept window, never the marker.** The marker is overhead
    added on top. A budget that the marker ate into would quietly shrink the
    evidence every time this module got more honest.
  * **Cuts compose instead of erasing each other.** Cutting an already-cut text
    re-states the loss against the ORIGINAL size, so hop three reports "8213 chars"
    and not "the 400 chars hop two handed me." Without this, a tail-cut applied to a
    head-marked string would drop the marker itself and restore the exact silence
    this module exists to end.

Lives at the repo root, beside `interpreter.py`, for the same reason that one does:
it is imported by files in `curate/`, `deploy/` and the root, and the repo root is
the only directory all of them already put on `sys.path`.

PREFIXED, like `poga_child_audit`, and not called `evidence`. Two of its callers —
`poga_cli.py` and `curate/run_suite.py` — APPEND the repo root to `sys.path` rather
than prepending it, each with a comment saying that is deliberate: "a lookup for one
module, not a claim to shadow anything already importable." That politeness cuts both
ways: any third-party distribution named `evidence` on the interpreter running the
suite would win the lookup, and the failure would surface as `AttributeError: module
'evidence' has no attribute 'clip'` somewhere far from here. None of the six callers
is pushed to members (`BYTE_IDENTICAL` in `curate/push-substrate.py` carries neither
`poga_cli.py` nor anything under `curate/` or `deploy/`), so this file is
federation-only and does not belong in the substrate manifest — but the federation's
own machines are enough surface to be worth not gambling a generic name on.
"""

import re

__all__ = ["clip", "was_cut", "describe_cut", "MARKER_RE"]

#: One line, upper-case, bracketed. Upper-case so it cannot be mistaken for content
#: and can be grepped out of a JSONL record or a comms note by eye.
MARKER_RE = re.compile(
    r"\[EVIDENCE CUT: (?P<dropped>\d+) of (?P<total>\d+) (?P<unit>chars|lines) "
    r"(?P<side>above|below)(?:; full text in (?P<path>[^\]]+))?\]")

#: The marker sits at one end of the string, separated from the surviving window by a
#: newline — or, for a field contracted to stay on ONE line (a verdict, a table cell,
#: a `detail` rendered inside backticks), by a single space. Both separators parse.
_LEAD_RE = re.compile(r"^" + MARKER_RE.pattern + r"[ \n]")
_TRAIL_RE = re.compile(r"[ \n]" + MARKER_RE.pattern + r"$")


def _marker(dropped, total, unit, side, full_text_at=None):
    tail = "; full text in %s" % full_text_at if full_text_at else ""
    return "[EVIDENCE CUT: %d of %d %s %s%s]" % (dropped, total, unit, side, tail)


def _units(text, unit):
    return text.splitlines() if unit == "lines" else text


def _join(parts, unit, sep="\n"):
    return sep.join(parts) if unit == "lines" else parts


def _strip_marker(text, unit):
    """`(body, original_total)` for text that already carries a marker.

    Only a marker at the very START or very END of the string is honoured. A
    marker-shaped run of characters in the MIDDLE belongs to the captured output
    itself — some other tool's evidence quoted inside this one — and re-basing our
    arithmetic on somebody else's number would fabricate a total.
    """
    m = _LEAD_RE.match(text)
    if m and m.group("unit") == unit:
        return text[m.end():], int(m.group("total"))
    m = _TRAIL_RE.search(text)
    if m and m.group("unit") == unit:
        return text[:m.start()], int(m.group("total"))
    return text, None


def clip(text, limit, keep="tail", unit="chars", full_text_at=None, one_line=False):
    """`text` reduced to `limit` units, with a marker naming what was dropped.

    Returns `text` UNCHANGED when it already fits — a marker on a complete text
    would be a lie, and a reader who sees no marker is entitled to conclude nothing
    was lost. That equivalence is the whole contract.

    `keep` is ``"tail"`` (keep the end, lose the head) or ``"head"``. `unit` is
    ``"chars"`` or ``"lines"``. `limit` counts the kept window only; the marker is
    added on top of it. `full_text_at` names the file that holds the whole text, for
    callers that took acceptance route (a). `one_line` joins the marker to the window
    with a space instead of a newline and collapses the window's own whitespace, for
    a field whose contract is that a reader gets exactly one line.

    The marker states the WHOLE loss in one note, never a per-hop tally — the rule
    `sessionlib/land.py::_gate_failure_receipt` states for the gate receipt: the
    notes partition the loss and never double-count it.
    """
    if keep not in ("tail", "head"):
        raise ValueError("keep must be 'tail' or 'head', not %r" % (keep,))
    if unit not in ("chars", "lines"):
        raise ValueError("unit must be 'chars' or 'lines', not %r" % (unit,))
    if limit < 0:
        raise ValueError("limit must not be negative, got %r" % (limit,))

    text = text or ""
    body, prior_total = _strip_marker(text, unit)
    if one_line and unit == "chars":
        # Collapse BEFORE measuring, but only when the budget is in CHARS. A budget
        # spent on the newlines a one-line field is about to lose anyway would report
        # a loss that never reached the reader. A budget in LINES cannot collapse
        # first — there would be nothing left to count — so it selects its lines and
        # collapses the surviving window afterwards.
        body = " ".join(body.split())
    seq = _units(body, unit)
    total = prior_total if prior_total is not None else len(seq)
    sep = " " if one_line else "\n"

    if len(seq) <= limit and prior_total is None:
        return _join(seq, unit, sep) if one_line else text
    if len(seq) <= limit:
        # Already cut upstream and this hop takes nothing more. The marker must
        # survive anyway: the loss is real, it just did not happen here.
        window, dropped = _join(seq, unit, sep), total - len(seq)
    elif keep == "tail":
        window, dropped = _join(seq[len(seq) - limit:], unit, sep), total - limit
    else:
        window, dropped = _join(seq[:limit], unit, sep), total - limit

    if dropped <= 0:
        return window
    side = "above" if keep == "tail" else "below"
    mark = _marker(dropped, total, unit, side, full_text_at)
    return mark + sep + window if keep == "tail" else window + sep + mark


def was_cut(text):
    """True when `text` carries a cut marker — the negative every caller relies on."""
    return describe_cut(text) is not None


def describe_cut(text):
    """The parsed marker as a dict, or None. `total` is the ORIGINAL size, across
    however many hops cut it."""
    body = text or ""
    m = _LEAD_RE.match(body) or _TRAIL_RE.search(body)
    if not m:
        return None
    return {"dropped": int(m.group("dropped")), "total": int(m.group("total")),
            "unit": m.group("unit"), "side": m.group("side"),
            "full_text_at": m.group("path")}
