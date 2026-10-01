#!/usr/bin/env python3
"""Scrub gate — refuse to put known-shape personal data into tracked history.

WHY THIS EXISTS, and why it is a REFUSAL rather than a report. The outbox
(`outbox/to-<architect-id>/`) is TRACKED, because ADR-0103 D1 makes the git remote the
only bridge between the two machines and a gitignored directory cannot cross it. Tracked
means permanent: a brief written there is in the history forever, and the repo has a
public mirror whose sanitization is, today, a hand-scrub performed per sync (ADR-0018;
the harness is WI-0020, still open). A one-way door with a hand-operated lock is the
shape that eventually goes wrong, so the lock becomes code.

THE MEASUREMENT THAT PROMPTED IT. Scanning the federation's `proposed-edits/`
tree found no private personal or credential data at all — every apparent hit was vocabulary ("diagnosis" and "symptom" in the debugging
sense, "routing" as in message routing, `apiKeyHelper` as a config key name). It found
exactly one genuinely sensitive brief, and it was one the FEDERATION AUTHORED AND SENT.
Outbound, federation-authored, exactly
what the outbox carries. The risk is measured, not hypothetical.

WHAT THIS IS NOT. A pattern scan finds shapes it knows. It cannot catch a family
member's name, a private quote, or something sensitive described in ordinary prose. So a
clean result means NO KNOWN-SHAPE PII, never "proven harmless"
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)) —
which is the argument for the gate being a refusal a human clears rather than an
automated pass. `--force` exists, it is recorded in the brief's own staging receipt, and
it says so on the way past.

Kept as its own module rather than folded into `deliver.py` so that WI-0020's public
mirror harness has one implementation to call rather than growing a second (P16).
"""
import argparse
import pathlib
import re
import sys

# Each entry is (class, compiled pattern, why it matters). The `why` is printed with the
# refusal: a gate that says only "line 12 matched" makes the author guess at the rule,
# and a guessed rule gets bypassed.
_SPECS = (
    # THE `10` ALTERNATIVE MATCHES TWO OCTETS, like both its siblings (WI-0229).
    # It read `10\.`, a bare prefix-plus-dot, while `192\.168` and `172\.(…)` each supply
    # the first TWO octets and the group is followed by `\.\d{1,3}\.\d{1,3}` for the other
    # two. So the alternative was wrong twice over: the trailing `\.` made it need a
    # DOUBLED dot (`10..1.2` matched, `10.1.2.3` did not), and one octet short meant that
    # correcting only the dot masked `10.4.5` out of `10.4.5.6` and left the last octet
    # standing. Every address in 10.0.0.0/8 — the largest private range there is — passed
    # a gate that then reported the file clean. Found by a redaction test asserting the
    # COUNT of masked spans; reading the pattern is what had missed it until then.
    ("private-ip",
     r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
     "a LAN address maps the host's network position"),
    ("overlay-ip",
     r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
     "an overlay-range address is a durable handle on a specific machine"),
    ("mac-address",
     r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b",
     "a MAC address identifies one physical device"),
    ("credential",
     r"(?i)(?:\bapi[_-]?key\s*[:=]|\bsecret[_-]?key\s*[:=]|\baccess[_-]?token\s*[:=]"
     r"|\bpassword\s*[:=]|\bpasswd\s*[:=]|\bBearer\s+[A-Za-z0-9._-]{16,}"
     r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
     "a secret in tracked history is a rotation event, not a cleanup"),
    ("ssn",
     r"\b\d{3}-\d{2}-\d{4}\b",
     "a government identifier"),
    ("phone",
     r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ -]\d{3}-\d{4}\b",
     "a personal phone number"),
    ("street-address",
     r"\b\d{2,5}\s+[A-Z][a-z]+\s+(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd)\b",
     "a physical address"),
    ("wifi-secret",
     r"(?i)\b(?:ssid|psk|wpa2?[- ]?(?:key|password)|network key|wifi password)\s*[:=]",
     "a wireless credential or network name"),
    ("dob",
     r"(?i)\b(?:date of birth|d\.o\.b\.)\b",
     "a date of birth"),
)

PATTERNS = tuple((name, re.compile(pat), why) for name, pat, why in _SPECS)

# Vocabulary that reads like PII to a regex and is not. Anything matching a pattern is
# checked against these BEFORE it counts, so the gate does not train people to --force.
# Deliberately narrow: an allowlist that grows to cover real hits stops being a gate.
_ALLOW = re.compile(
    r"(?i)\b(?:example|placeholder|redacted|xxx|0\.0\.0\.0|127\.0\.0\.1|"
    r"10\.0\.0\.0/8|192\.168\.0\.0/16|172\.16\.0\.0/12)\b")

#: A CIDR suffix immediately after a match, which the match itself can never contain.
#: THREE OF THE ALLOWLIST ENTRIES ABOVE WERE DEAD without this (WI-0229). The private-ip
#: pattern captures `192.168.0.0` and stops; the allowlist entry is `192.168.0.0/16`; so
#: testing the entry against the captured span could not succeed for any of the three
#: documentation ranges, and `the 192.168.0.0/16 range` was REFUSED as a LAN address.
#: Nobody noticed because the only one the test suite exercised was `10.0.0.0/8`, which
#: the broken `10\.` alternative never matched in the first place — a test that was green
#: because of a second defect.
#:
#: The suffix is added to the CANDIDATE and the line is still not consulted, deliberately.
#: Allowlisting against the whole line would mean one occurrence of the word "example"
#: anywhere on it excused a real address beside it, which is a much larger hole than the
#: one being closed.
_CIDR = re.compile(r"/\d{1,3}")


def _allowed(line, match):
    """True if this match is allowlisted vocabulary rather than a finding."""
    span = match.group(0)
    if _ALLOW.search(span):
        return True
    suffix = _CIDR.match(line, match.end())
    return bool(suffix and _ALLOW.search(span + suffix.group(0)))


class Finding:
    """One hit: where, which class, and the reason the class exists."""

    __slots__ = ("path", "line", "cls", "why", "excerpt")

    def __init__(self, path, line, cls, why, excerpt):
        self.path, self.line, self.cls = path, line, cls
        self.why, self.excerpt = why, excerpt

    def __repr__(self):                                    # pragma: no cover - debugging
        return f"<Finding {self.cls} {self.path}:{self.line}>"

    def render(self):
        """One printable line. The matched VALUE is masked — the gate's job is to say
        that something is there, not to reproduce it into a terminal, a log, or a
        transcript (P14 `no-confidential-data-in-chats`)."""
        return (f"  {self.path}:{self.line}  [{self.cls}] {self.why}\n"
                f"      …{self.excerpt}…")


def _mask(value):
    """Digits and hex to `x`, letters kept, so the SHAPE is legible and the VALUE is not.
    `192.0.2.81` (a documentation-range example, RFC 5737) renders as `xxx.x.x.xx` —
    enough to recognise what tripped, not enough to use."""
    return re.sub(r"[0-9A-Fa-f]", "x", value) if len(value) > 3 else "xxx"


def findings(text, path="<text>"):
    """Every known-shape hit in `text`, as Findings. Pure: no filesystem, no config.

    Line numbers are 1-based against the ORIGINAL text so the author can go straight to
    the line — a gate that reports an offset into a normalised copy is a gate people
    argue with instead of fixing."""
    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for cls, pat, why in PATTERNS:
            for m in pat.finditer(line):
                if _allowed(line, m):
                    continue
                s, e = m.span()
                excerpt = (line[max(0, s - 30):s] + "[[" + _mask(m.group(0)) + "]]"
                           + line[e:e + 30]).strip()
                out.append(Finding(path, lineno, cls, why, excerpt))
    return out


def scan_file(path):
    """Findings for one file. An unreadable file is a REFUSAL, not a pass — "could not
    read it" and "read it and it was clean" are the two answers this whole repo keeps
    insisting must not share an exit code."""
    p = pathlib.Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [Finding(str(p), 0, "unreadable", f"could not be read ({e.__class__.__name__}), "
                        f"so it has NOT been checked", "")]
    return findings(text, str(p))


#: Classes whose pattern matches a LABEL rather than the sensitive value itself —
#: `api_key =`, `psk:`, `date of birth`. Masking the match on one of these is actively
#: WORSE than doing nothing: it rewrites the marker, leaves the secret standing one
#: character to the right, and hands the result to a gate that now finds nothing to
#: refuse. Measured on the first probe of `redact`: `api_key = sekrit12345` came back as
#: `[[xpi_kxy =]] sekrit12345` and passed `findings` clean. So for these the whole LINE
#: goes. The rest match the datum itself, where masking is exactly right and keeps the
#: surrounding line readable.
_MARKER_CLASSES = frozenset({"credential", "wifi-secret", "dob"})

_DROPPED = "[[line removed: {cls} — the value follows the marker on this line]]"


def redact(text):
    """`text` with every known-shape hit neutralised, plus a per-class count of what was.

    Returns `(redacted_text, {cls: n})`. An empty dict means nothing matched — which is
    NOT the same as "safe", for exactly the reason the module docstring gives: these are
    the shapes we know.

    WHY A REDACTION AND NOT A REFUSAL, given that the rest of this module refuses. `gate`
    and `post` guard content an author wrote and can go back and fix; a refusal there
    costs a re-edit and the author is standing right there. This exists for the other
    case — an unattended collector quoting machine output it did not author (a log tail, a
    parsed unit record) into a report that travels. Nobody is there to redact it, and the
    moment the output is most likely to carry a stray token is the moment the report is
    most worth having. Refusing then does not protect anything; it deletes the evidence
    and leaves the operator with the silence they were trying to end.

    So the caller redacts first and posts second, and `post`'s scrub gate stays in front
    of it unchanged. That ordering is deliberate: the gate becomes an independent check
    that THIS function actually worked, rather than a step the caller has talked its way
    past with `force`. A redactor that misses a class still hits a closed door.

    TWO DISPOSALS, because the patterns are two different kinds of thing — see
    `_MARKER_CLASSES`. A value-class hit is masked in place; a marker-class hit takes the
    whole line with it.

    The counts are the caller's to report. A redaction nobody mentions is
    indistinguishable from output that never contained anything
    ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)),
    and the reader of a diagnosis needs to know a line was altered before they reason
    about what it says.
    """
    counts = {}
    out = []
    for line in text.splitlines():
        dropped_as = None
        for cls, pat, _why in PATTERNS:
            if cls not in _MARKER_CLASSES:
                continue
            for m in pat.finditer(line):
                if not _allowed(line, m):
                    dropped_as = cls
                    break
            if dropped_as:
                break
        if dropped_as:
            counts[dropped_as] = counts.get(dropped_as, 0) + 1
            out.append(_DROPPED.format(cls=dropped_as))
            continue

        def _sub(cls):
            def _one(m):
                if _allowed(line, m):
                    return m.group(0)
                counts[cls] = counts.get(cls, 0) + 1
                return f"[[{_mask(m.group(0))}]]"
            return _one

        for cls, pat, _why in PATTERNS:
            if cls in _MARKER_CLASSES:
                continue
            line = pat.sub(_sub(cls), line)
        out.append(line)
    return "\n".join(out), counts


def gate(path, force=False, out=sys.stderr):
    """True if `path` may be written into tracked history.

    `force=True` passes anything and ANNOUNCES it — the ADR-0096 D7 shape, where a
    bypassed check can never be mistaken later for a clean one."""
    hits = scan_file(path)
    if force:
        if hits:
            print(f"scrub: OVERRIDDEN — {len(hits)} finding(s) in {path} passed with "
                  f"--force. This file is NOT clean; it was cleared by a human.", file=out)
        return True
    if not hits:
        return True
    print(f"scrub: REFUSING {path} — {len(hits)} finding(s) of known-shape personal "
          f"data. The outbox is TRACKED and its history is permanent.", file=out)
    for f in hits:
        print(f.render(), file=out)
    print("  Remove or redact the values, or re-run with --force to clear it "
          "deliberately (the override is recorded).", file=out)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Refuse known-shape personal data in files bound for tracked history.")
    ap.add_argument("paths", nargs="+", help="files to scan")
    ap.add_argument("--force", action="store_true",
                    help="pass despite findings, announcing the override")
    args = ap.parse_args(argv)
    ok = True
    for p in args.paths:
        if not gate(p, force=args.force):
            ok = False
    if ok and not args.force:
        print(f"scrub: {len(args.paths)} file(s) clean of known-shape PII. That is not "
              f"the same as proven harmless — prose is not pattern-matchable.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
