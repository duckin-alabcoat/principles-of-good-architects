#!/usr/bin/env python3
"""Refuse a reply that talks to the user about git.

WHY THIS IS CODE AND NOT A RULE. The rule exists — `standard-source.md` §"What reaches
the user" says a session never hands operator a git command, and the operator's 2026-09-13 standing
ruling widened it: no session asks him about or tells him about git in any form, unless
the git architecture itself is being redesigned or something has been LOST.

It was then violated four times in one sitting by the session that wrote it down, twice
after being told directly. `add-structural-guard-on-recurrence` is unambiguous about what
that means: the first violation is a miss, a repeat means discipline is not enough and the
enforcement belongs in code. This is that code.

WHAT IT READS. The `Stop` hook receives the transcript path; the last assistant message is
the reply about to stand as the session's answer. Nothing else is inspected — not the
user's words, not tool output, not the journal. Recording git detail in the journal is
REQUIRED, and a guard that could not tell those apart would suppress the record while
permitting the sentence.

FAIL-OPEN BY CONSTRUCTION. Any error — unreadable transcript, unexpected shape, missing
key — permits the reply. A guard on prose must never be able to brick the conversation,
and the thing on the other side of it is an irritated user, not lost data
(`add-structural-guard-on-recurrence`: direction is set by what the guard protects).
"""
from __future__ import annotations

import json
import re
import sys

#: Words that only appear when the reply is narrating version control TO the user. Kept
#: deliberately narrow: each one has been in an actual violation. A broad list would fire
#: on ordinary English ("the branch of the tree", "commit to a plan") and a guard that
#: fires on correct output is one that gets deleted, taking the protection with it
#: (`a-guard-that-fires-on-correct-code-gets-deleted`).
GIT_TERMS = re.compile(
    r"(?:"
    r"\bgit\b|"
    r"\bcommits?\b|\bcommitted\b|\bcommitting\b|"
    r"\bpush(?:e[sd]|ing)?\b|\bunpushed\b|"
    r"\bpull(?:e[sd]|ing)?\b|"
    r"\brebas(?:e[sd]?|ing)\b|"
    r"\bfast-forward(?:ed|s)?\b|"
    r"\borigin/\w+|\b(?:on|to|against|from)\s+origin\b|\bthe\s+trunk\b|\btrunk\b|"
    r"\bstranded\b|\bdiverged\b|\bdivergence\b|"
    r"\bahead\b.{0,20}\bbehind\b|"
    # `land` is the version-control verb in this repo AND an ordinary English one —
    # "escalations land where nobody reads them" is not a git sentence, and blocking it
    # made a correct reply unsendable. Matched only in its version-control sense.
    r"\bland(?:ed|s|ing)?\s+(?:it|them|on\s+\w+|onto\s+\w+|the\s+(?:fix|change|commit|work|wave|lane))\b|"
    r"\bthe\s+land\b|\bland[- ](?:gate|lock|queue)\b|\block\s+hold\b|"
    # NOT a bare "the gate": this repo gates several things that are not version control —
    # the canon budget gate is the operator's own subject and his own word, and blocking it made a
    # reply about HIS ruling unsendable. Only the land-gate senses match.
    r"\bgate\s+(?:ran|red|green|fail\w*|refused)\b|\bFL7\b|"
    r"\brecompile\b|\bworktree\b|"
    # `merge` only in its version-control sense. Bare "merge" is ordinary English —
    # merging learnings, merging two lists — and blocking it made a clean outcome report
    # unsendable, which is how a guard earns its own deletion.
    r"\bmerge[sd]?\s+(?:origin|main|into|the\s+trunk)\b|\bmerge\s+commit\b"
    r")", re.I)

#: The two sanctioned exceptions, verbatim from the ruling.
REDESIGN = re.compile(
    r"\b(architecture|redesign|restructur\w+|how\s+(?:the\s+)?land(?:ing)?\s+works)\b", re.I)
LOST = re.compile(
    r"\b(lost|losing|destroyed|unrecoverable|gone\s+for\s+good|cannot\s+be\s+recovered|"
    r"overwritten|deleted\s+work)\b", re.I)


def last_assistant_text(path: str) -> str:
    """The text of the reply about to stand. "" when it cannot be read — see fail-open."""
    text = ""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content") or []
            parts = [c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            if parts:
                text = "\n".join(parts)
    return text


def violation(text: str) -> str:
    """The reason to refuse, or "" to permit."""
    if not text.strip():
        return ""
    if REDESIGN.search(text) or LOST.search(text):
        return ""                    # (a) redesign or (b) something lost — both sanctioned
    hits = sorted({m.group(0).lower() for m in GIT_TERMS.finditer(text)})
    if not hits:
        return ""
    return (
        "This reply talks to operator about git: " + ", ".join(hits[:6])
        + ". His standing ruling (2026-09-13, standard-source.md \"What reaches the user\"): "
          "no session asks him about or tells him about git in ANY form, unless the git "
          "architecture itself is being redesigned or something has been LOST. Commits, "
          "pushes, pulls, branches, rebases, stranded checkouts, diverged trunks, lock "
          "budgets: fix it, record it, never mention it. The reassuring direction counts "
          "too — \"pushed and verified\", \"the trunk reconciled\" are git sentences. "
          "Rewrite the reply in terms of OUTCOMES: what now works, what is blocked on a "
          "decision only he can make. Put the mechanics in the journal, where the review "
          "reads them. A git sentence in front of operator is a WI-0159 escalation defect.")


def main() -> int:
    try:
        payload = json.load(sys.stdin)

        # NEVER BLOCK TWICE IN A ROW. `stop_hook_active` is true when this hook already
        # blocked and the model is producing its replacement. Blocking again is how a
        # guard on prose becomes a LIVELOCK: measured 2026-09-13, the rewrite was clean —
        # zero matches when the text was tested directly — and the hook refused it anyway,
        # because the transcript it reads does not yet carry the new message and the last
        # assistant record is still the one that was already rejected. So the second
        # refusal is not a judgement about the new reply at all; it is the old verdict
        # read a second time.
        #
        # One refusal carries the whole value of this guard: it makes the rule impossible
        # to forget at the moment it matters. A second refusal adds nothing and can wedge
        # the conversation, which is a worse failure than the sentence it was stopping.
        if payload.get("stop_hook_active"):
            return 0

        path = payload.get("transcript_path")
        if not path:
            return 0
        why = violation(last_assistant_text(path))
        if not why:
            return 0
        print(json.dumps({"decision": "block", "reason": why}))
        return 0
    except Exception:
        return 0                     # fail open, always


if __name__ == "__main__":
    sys.exit(main())
