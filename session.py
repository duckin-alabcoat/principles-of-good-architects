#!/usr/bin/env python3
"""Session-ritual harness — deterministic, no LLM.

Encodes the mechanical half of the federation-arch.md §11 session rituals as
code, so the Architect stops executing the checklist by hand every session.
Code does the mechanics; the Architect keeps the judgment (the orphan bad/keep
question to operator, the "what happened / what's owed" summary prose, the commit
message, the memory/registry sweeps).

`start` is wired into the SessionStart hook in .claude/settings.json (next to
the curate gather). It runs automatically when a session opens and WRITES THE
START STAMP itself, so the stamp survives any context loss before the Architect
announces it. It prints a structured orientation block the Architect reads to
produce the announce + summary.

What `start` does (all mechanical, per §11 steps 1-7):
  1. git status; ff-pull only when clean (a dirty tree defers sync) — NEVER blocks
  2. detect machine (scutil --get ComputerName, else hostname off macOS -> machine_map label)
  3. stamp current time in the configured timezone
  4. parse the SESSION LOG table -> next session number (NC rows don't burn it)
  5. orphan-detect: topmost entry has Start but no End -> triage (ADR-0036)
  6. write the start-stamp table row + prose entry header to session-handoff.md
  7. emit the orientation block

Startup is never fragile: it opens the session regardless of what is
uncommitted (a session-50 ruling: deal with whatever is there and start). Any
uncommitted files are the Architect's in-flight work, left as-is and swept by
the session-end commit; they are surfaced in the orientation, not blocked on.

What it deliberately does NOT do: decide the orphan keep/bad question or write
any summary prose. Those stay with the Architect.

`end` does the mechanical close (§11 steps 6-9): compute duration, append the
end stamp, fill the SESSION LOG title, and (with --commit) stage/commit/push
with a message the Architect supplies. The handoff body, the memory/registry/
producer sweeps, and the commit message itself remain the Architect's job.

`start` also sets the Claude Code session title (the picker / left-pane label)
to the structural session name (e.g. 'POGA·S26·Laptop·ThuJan1') via the
SessionStart hook's `sessionTitle` output. That is the ONLY hook event that
honors `sessionTitle` — UserPromptSubmit silently ignores the field, which is
why the prior UserPromptSubmit-based naming never applied. The orientation block
rides along in the same JSON as `additionalContext`. Because the title is set
only at SessionStart, a mid-session manual /rename survives untouched.

`start` also injects two generated artifacts into the session as `additionalContext`,
alongside the orientation block:
  - CANON.md — the inherited universal set (principles + habits, one line each); the
    ADR-0022 code channel, generated from the registries by curate/distill.py.
  - STANDARD.md — the standard role-doc section (session rituals + standard substrate);
    the ADR-0024 code channel, generated from standard-source.md by curate/standardize.py.
Both deliver text the LLM would otherwise have to remember to read (P15), and both make
the content un-droppable: no per-Architect copy of the standard prose exists to
re-author and lose a step from (the stamp bug one member hit; ADR-0024 §3). Either absent is
tolerated (federation always has them; a fresh kit ships them).

(There is no hook that can title a session at its close, so the per-session
"done" / close-icon tag is not buildable on the current platform — parked until
Claude Code exposes a SessionEnd title path.)

`check-bash` is wired as a `PreToolUse` hook (matcher `Bash`) in
.claude/settings.json: it reads the PreToolUse JSON on stdin, parses the proposed
Bash command, and applies three policies. The parser is quote-, heredoc-, and
command-substitution-aware so single commands that merely contain `;`/`&&` inside
a commit message are unaffected. It FAILS OPEN (allows on any parse/IO error) — a
guard must never brick the Bash tool.

The COMPOUND deny is RETIRED and OFF BY DEFAULT ([ADR-0087], 2026-08-04). It was
the structural guard for the `no-compound-bash` universal habit — built under
`add-structural-guard-on-recurrence` after the habit was violated twice in session
26 despite being text-only, then broadened in session 31 from the git-only subset
to any 2+-operation chain. Its motivation was a HARNESS LIMITATION: settings
allow-strings cannot match a compound command, so every chain prompted, and
one-op-per-call was what let the allow-list work. The harness's auto permission
mode judges commands directly, so that motivation is gone and the habit is retired
in `habits/master.md` alongside this switch. The code stays live and tested behind
`compound_bash_guard: true` in `session.config.json` — absent or false, the check
is skipped entirely. No member declares it, so the next substrate push turns the
deny off fleet-wide with no per-member action.

`check-bash` also DENIES A FALSE-GREEN RUN unconditionally (WI-0344): a test
suite or a land with anything running AFTER it in the same command — a pipe, a
`;`, an `||`, a newline — because the exit status that comes back is then the
downstream command's and a failing run reports 0. `&&` is exempt (a non-zero left
side short-circuits) and so is a run that is the last thing in the command. The
message names the sanctioned form, which is `python3 session.py test` run bare:
it writes stdout and stderr to separate FILES, never a pipe, and prints its own
PASSED / FAILED / DID NOT REPORT verdict. Eight recorded occurrences across
WI-0139 and WI-0344 are what made this code rather than prose.

`check-bash` DENIES a destructive git op UNCONDITIONALLY — regardless of the flag
above, of a `git -C <path>` prefix, and of whether it sits inside a compound
(confirm-destructive-ops / P9). That guard's motivation was never prompt-matching,
so ADR-0087 leaves it alone; under a harness that approves by judgment it is the
deterministic floor underneath, and no settings deny-string can match the `-C`
form. It also — since session 60 —
AUTO-APPROVES the safe git surface (`permissionDecision: "allow"`) so that
`git -C <path> <verb>` no longer prompts. Claude Code's settings permission
matcher can't match a `git -C <path>` command against any allow-string (the
`-C /path` global option defeats the prefix match — confirmed against the CC
permission docs), so the exact-command allows piled up one-per-session in
settings.local.json; the fix is structural, in this `-C`-aware hook, not another
dead allow-string (P15 / add-structural-guard-on-recurrence). The allow set
mirrors the floor's already-blessed plain-git verbs (parity, not new trust);
destructive git is denied first and a hook "allow" can't override a settings
`deny` (deny-first precedence), so the safe fast-path can never unlock a
destructive op. See _AUTO_ALLOW_GIT_SUB / safe_git_auto_allow.

Usage:
  python3 session.py start                      # ff-pull, stamp, write start row+header, set title
  python3 session.py start --dry-run            # print what it would do; write nothing
  python3 session.py end --title "..." \
        --commit "docs(handoff): close session N"      # lands AND pushes (WI-0355)
  python3 session.py end --title "..." --commit --no-push   # land locally; push reported owed
  python3 session.py end --title "..." --dry-run
  python3 session.py rotate [--keep N]          # archive old prose entries into session-handoff-archive/; SESSION LOG stays
  python3 session.py check-bash                 # PreToolUse Bash guard; always denies destructive git + a false-green run (WI-0344), denies compounds only where `compound_bash_guard: true` (ADR-0087); auto-approves the safe git surface (incl. `git -C …`); reads hook JSON on stdin
  python3 session.py interpreter                # WI-0328: which interpreter the harness resolved, from where, and whether the pin is in force
  python3 session.py check-question             # PreToolUse AskUserQuestion guard; denies multiple-choice pickers (P17 ask-in-prose-not-pickers); reads hook JSON on stdin
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.abspath(__file__))
_sys.path.insert(0, _ROOT)

# WI-0468 — NEVER AS ROOT, the same refusal the `poga` wrapper makes at its top. A root
# run writes root-owned files (journals, .session-state, the lane pool, git objects) into
# a checkout the operator's own sessions must later write, and fails them far from the
# cause — the class `curate/gate-inputs-runner.py` already refuses for its runner. Only
# when this file IS the program (an `import session` from a test is not a run). EXIT 2,
# not 1, on purpose: this file is also every PreToolUse hook, where exit 2 BLOCKS and
# any other non-zero is a non-blocking error — a refusal that exited 1 would silently
# switch the destructive-git guard OFF for a root session instead of stopping it.
#
# WI-0484 — the one exception is a Claude cloud container: always root, thrown away
# after the session, so nobody is left to trip over root-owned files. The same two
# signals as `poga_cloud_container` in the wrapper: the remote marker Claude Code itself
# tests, and Linux with no launchd. tests/test_cloud_container.py holds both to one table.
# The sessionlib parts load into this namespace, so `start` uses this same test for the
# cloud start mode (it writes nothing tracked there; the container's work comes back as
# a branch and the operator's own machine owns the journal, handoff and STATUS).
def _cloud_container():
    import shutil
    return (_os.environ.get("CLAUDE_CODE_REMOTE") == "true"
            and _sys.platform.startswith("linux")
            and shutil.which("launchctl") is None)


if (__name__ == "__main__" and getattr(_os, "geteuid", lambda: -1)() == 0
        and not _cloud_container()):
    print("session.py: refusing to run as root — POGA writes files your own sessions "
          "must later edit. Run it as your normal user, not with sudo or in a root "
          "shell (WI-0468).", file=_sys.stderr)
    _sys.exit(2)

# WI-0328 — BEFORE sessionlib, before anything heavy. Every gate, derive, suite and
# land subprocess already spawns `sys.executable` (WI-0320); what nothing pinned was
# the process that CHOOSES it, so the harness ran on whatever `python3` the operator's
# PATH resolved. `ensure` re-execs into the interpreter this member declares (or
# /usr/bin/python3 on macOS) when PATH handed us a different one, which makes
# `sys.executable` the same answer on every machine in the fleet. Fails open, never
# loops, and no-ops in the overwhelmingly common case where we are already it.
#
# TRY/EXCEPT, and this is the load-bearing part rather than defensive habit:
# `session.py` is shipped byte-identical to every member, and a member that has not yet
# received `interpreter.py` from a substrate push must keep starting exactly as it did
# before. A hard import here would turn "this member is one push behind" into "this
# member's harness will not start" — the entry point failing on the line whose whole
# purpose is to make the entry point deterministic.
try:
    import interpreter as _interpreter
except Exception:
    _interpreter = None
else:
    # ONLY when this file IS the program. Re-execing a process that merely imported
    # us would replace somebody else's program with ours — and it did: `import
    # session` from a test module restarted the whole test runner under the pin.
    if __name__ == "__main__":
        _interpreter.ensure(_ROOT)

import sessionlib as _sessionlib

_sessionlib.load(globals())



if __name__ == "__main__":
    main()
