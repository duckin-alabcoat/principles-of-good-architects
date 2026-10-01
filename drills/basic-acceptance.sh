#!/usr/bin/env bash
# The basic acceptance drill (WI-0455). Record: drills/basic-acceptance.md.
#
# One machine, one person, a fresh folder. Only a fresh export of the public cut, with
# Git, Python and one runtime installed. No GitHub, no second host, no scheduler, no
# board, no broker, no pre-existing profile.
#
# What this script does, in order:
#   1. takes the candidate: by default, the checkout this script lives in (a clone or an
#      export of the public cut), copied into $DRILL/cut without its .git; with
#      --from-source, a fresh export of a source repo made by curate/public_cut.py
#   2. copies ONE runtime binary (claude) to $DRILL/bin — "installed", nothing more
#   3. writes a macOS sandbox profile that denies every read and write under /Users, all
#      network, reads of /opt/homebrew and /usr/local, and writes outside $DRILL (and the
#      system temp dir), and runs everything else inside it with an empty environment
#      and HOME=$DRILL/home
#   4. publishes the export as a one-commit git repo (the "upstream" a newcomer clones)
#   5. runs the drill steps and writes $DRILL/transcript.txt
#
# Usage:  drills/basic-acceptance.sh                     test this checkout, as downloaded
#         drills/basic-acceptance.sh --from-source SRC   export SRC's public cut, test that
#   DRILL=/some/neutral/path  where to work (default /private/tmp/poga-drill-basic).
#                             It is DELETED first. Keep it neutral: the record ships.
#   CLAUDE_BIN=/path/to/claude  the runtime binary to copy (default: `command -v claude`)
#
# macOS only (sandbox-exec). Exit 0 only when every step passed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC=""
case "${1:-}" in
  "") ;;
  --from-source)
    [ -n "${2:-}" ] || { echo "basic-acceptance: --from-source needs the source repo's path" >&2; exit 2; }
    SRC="$(cd "$2" && pwd)" ;;
  *) echo "usage: drills/basic-acceptance.sh [--from-source SRC]" >&2; exit 2 ;;
esac
DRILL="${DRILL:-/private/tmp/poga-drill-basic}"

case "$DRILL" in
  /private/tmp/*|/tmp/*) ;;
  *) echo "basic-acceptance: DRILL must be under /tmp or /private/tmp (it is deleted first)" >&2; exit 2 ;;
esac
command -v sandbox-exec >/dev/null || { echo "basic-acceptance: needs macOS sandbox-exec" >&2; exit 2; }
# The default candidate is a public cut, which carries its receipt. A tree without one
# is a source repo: testing it as downloaded would put its private content in the
# sandbox and the transcript, so it must go through the export instead.
if [ -z "$SRC" ] && [ ! -f "$HERE/PUBLIC-CUT-RECEIPT.md" ]; then
  echo "basic-acceptance: $HERE has no PUBLIC-CUT-RECEIPT.md, so it is not a public cut." >&2
  echo "  To test a source repo's export, run: drills/basic-acceptance.sh --from-source <repo>" >&2
  exit 2
fi

rm -rf "$DRILL"
mkdir -p "$DRILL/bin" "$DRILL/home" "$DRILL/publisher-home" "$DRILL/tmp"
# The sandbox matches REAL paths (/tmp is /private/tmp on macOS).
DRILL="$(cd "$DRILL" && pwd -P)"

# 1. The candidate. The only step that reads outside $DRILL, and it runs OUTSIDE the
#    sandbox for exactly that reason.
if [ -z "$SRC" ]; then
  # This checkout, as downloaded: a disposable copy, without its git history, so the
  # newcomer's clone below starts from exactly these files.
  python3 - "$HERE" "$DRILL/cut" > "$DRILL/export.log" 2>&1 <<'PY'
import shutil, sys
shutil.copytree(sys.argv[1], sys.argv[2], symlinks=True,
                ignore=shutil.ignore_patterns(".git", "__pycache__"))
print("copied the checkout as the candidate")
PY
else
  # A fresh export of a source repo. Its gate runs with the source's own manifest.
  python3 "$SRC/curate/public_cut.py" --out "$DRILL/cut" > "$DRILL/export.log" 2>&1
  grep -q '^public_cut: clean' "$DRILL/export.log" || {
    echo "basic-acceptance: the export is not clean — see $DRILL/export.log" >&2; exit 1; }
  # The private receipt sits beside the cut. It is not part of the export; remove it so
  # nothing inside the sandbox can read it.
  rm -f "$DRILL/cut.PRIVATE-RECEIPT.md"
fi

# 2. One runtime, installed. A copy of the binary, not a link into the operator's home.
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
[ -n "$CLAUDE_BIN" ] || { echo "basic-acceptance: no claude binary to copy" >&2; exit 2; }
cp "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$CLAUDE_BIN")" "$DRILL/bin/claude"
chmod 755 "$DRILL/bin/claude"

# 3. The sandbox. Last matching rule wins, so read it bottom-up:
#    - no network at all;
#    - writes ONLY under $DRILL, /dev (for /dev/null and the like) and this user's
#      system temp directory — macOS `mktemp` ignores TMPDIR and poga's shell uses it;
#    - no reads or writes under /Users — every real home directory, the operator's
#      federation, profile, git config and runtime login included;
#    - no reads of the package-manager trees (/opt/homebrew, /usr/local), so the only
#      tools are the system's Git and Python and the one runtime copied into $DRILL/bin.
SYSTMP="$(cd "$(getconf DARWIN_USER_TEMP_DIR)" && pwd -P)"
cat > "$DRILL/drill.sb" <<SB
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "$DRILL") (subpath "/dev") (subpath "$SYSTMP"))
(deny file-read* file-write* (subpath "/Users"))
(deny file-read* (subpath "/opt/homebrew") (subpath "/usr/local"))
(deny network*)
SB

sandboxed() {
  # $1 = HOME for this run; the rest is the command.
  local home="$1"; shift
  # Start in $DRILL: the caller's cwd is usually under /Users, which the sandbox denies.
  # TMPDIR inside $DRILL, so temporary files (the land gate's scratch checkout among
  # them) stay inside the one writable tree. TERM is a real terminal type: a newcomer
  # runs this in a terminal, and poga's preflight checks the terminal's capabilities.
  ( cd "$DRILL" && sandbox-exec -f "$DRILL/drill.sb" /usr/bin/env -i \
    HOME="$home" \
    PATH="$home/.local/bin:$DRILL/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    TMPDIR="$DRILL/tmp" TZ=UTC LANG=C.UTF-8 TERM=xterm-256color DRILL="$DRILL" \
    "$@" )
}

# 4. Publish the export as the upstream a newcomer clones. Neutral identity, UTC dates,
#    its own empty HOME — the operator's git config never touches it.
cat > "$DRILL/publish.sh" <<'PUB'
set -eu
cd "$DRILL/cut"
git init -q -b main
git add -A
git -c user.name=drill -c user.email=drill@example.invalid commit -q -m "public cut (drill export)"
git log --oneline
PUB
sandboxed "$DRILL/publisher-home" bash "$DRILL/publish.sh" > "$DRILL/publish.log" 2>&1

# 5. The drill steps.
cat > "$DRILL/steps.sh" <<'STEPS'
# Runs INSIDE the sandbox. Every command is echoed with `$ ` and followed by its exit
# code, so the transcript is the evidence. `x` runs a command and keeps its output in
# $OUT for the checks after it; `check` records a CHECK PASS / CHECK FAIL line, and the
# exit code of this script is the number of failed checks.
set -u
FAILS=0
OUT="$DRILL/tmp/last.out"
step()  { printf '\n==== %s\n' "$*"; }
x()     { printf '$ %s\n' "$*"; "$@" > "$OUT" 2>&1; local rc=$?; cat "$OUT"; printf '[exit %s]\n' "$rc"; return "$rc"; }
check() { local label="$1"; shift
          if "$@" >/dev/null 2>&1; then printf 'CHECK PASS: %s\n' "$label"
          else printf 'CHECK FAIL: %s\n' "$label"; FAILS=$((FAILS + 1)); fi; }
no()    { ! "$@"; }
said()  { grep -q -- "$1" "$OUT"; }          # the last command's output contains $1
rcis()  { [ "$LAST" -eq "$1" ]; }
run()   { x "$@"; LAST=$?; }
jobs_now() { { launchctl list 2>/dev/null | awk 'NR>1 {print $3}' | grep -i -e poga -e drill -e federation; \
               crontab -l 2>/dev/null; } | sort; }

TODAY="$(date -u +%Y-%m-%d)"
ALPHA="$HOME/projects/alpha"
BETA="$HOME/projects/beta"
LANE="$ALPHA/.claude/worktrees/poga-1"

step "0. Isolation"
jobs_now > "$DRILL/tmp/jobs.before"       # scheduler baseline, compared in step g
run env
run ls -An "$HOME"
check "HOME is empty: no .gitconfig, no .config/poga, no .claude" test -z "$(ls -A "$HOME")"
run git config --global --list
check "no global git config" no git config --global --list
run command -v gh
check "gh is not on PATH" no command -v gh
run ls /opt/homebrew/bin
check "package-manager tools cannot be read" no ls /opt/homebrew/bin
run ls /Users
check "/Users cannot be read (no real home, profile, federation or login)" no ls /Users
run touch /private/tmp/poga-drill-outside
check "nothing can be written outside \$DRILL" no test -e /private/tmp/poga-drill-outside
run git ls-remote https://github.com/git/git
check "no network" said "Could not resolve host"
run python3 --version
run git --version
run command -v claude
run claude --version
check "one runtime is installed" rcis 0

step "a. Clone the export and install poga"
run git clone -q "$DRILL/cut" "$HOME/poga"
cd "$HOME/poga" || exit 99
run ./poga install
run command -v poga
check "poga is on PATH" rcis 0

step "b. Project alpha: poga init"
mkdir -p "$ALPHA"; cd "$ALPHA" || exit 99
run poga init --yes --purpose "A small ledger of household expenses." \
    --user-name "Drill User" --timezone UTC --machine-label laptop
check "init succeeded" rcis 0
check "init made the local federation" said "created the local federation"
check "init created no remote" said "no remote, nothing pushed"
run git remote -v
check "alpha has no remote" test -z "$(git remote)"
run git log --oneline
run find "$HOME/.config/poga" -type f

step "c1. Open a lane the real way: poga"
cd "$ALPHA" || exit 99
run poga
check "poga refuses a lane before the runtime is signed in" no rcis 0
check "the refusal names onboarding (a person must sign in)" said "onboarding"

step "c2. The same launch past the preflight: the lane opens, the runtime stops at login"
run env POGA_SKIP_PREFLIGHT=1 poga
check "the runtime stops at login" said "Not logged in"
check "the lane worktree exists" test -d "$LANE"
check "the lane branch exists" git show-ref --verify --quiet refs/heads/worktree-poga-1

step "c3. In the lane (the operator plays the agent): start, draw an item, claim it, start work"
cd "$LANE" || exit 99
# The runtime would run this as its SessionStart hook. Only the banner's head is shown.
printf '$ python3 session.py start   (banner head only)\n'
python3 session.py start </dev/null > "$DRILL/tmp/start1.json" 2>/dev/null
check "start's stdout is one JSON object (what the runtime's hook reads)" \
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$DRILL/tmp/start1.json"
python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["hookSpecificOutput"]["additionalContext"]; h=c.split("\n\n")[0].splitlines(); i=[n for n,l in enumerate(h) if l.startswith("version:")][0]; print("\n".join(h[i:i+19]))' "$DRILL/tmp/start1.json"
JOURNAL="$(ls sessions/journal/*.md)"
echo "journal: $JOURNAL"
check "the session opened a journal" test -f "$JOURNAL"
run poga work new "Add a tiny expense ledger" --scope product
check "work item WI-0001 drawn" said "WI-0001"
run poga work claim 1
check "WI-0001 claimed by this lane" said "claimed 'WI-0001'"
cat > ledger.py <<'PY'
"""A tiny expense ledger."""


def total(entries):
    """Sum the amounts of (label, amount) entries."""
    return sum(amount for _, amount in entries)
PY
run cat ledger.py

step "c4. Checkpoint: journal note + commit on the lane branch, then leave"
python3 - "$JOURNAL" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
t = p.read_text()
t = t.replace("- Session opened.\n",
              "- Session opened.\n- WI-0001: ledger.total written. Tests not written yet. Checkpointed.\n", 1)
p.write_text(t)
PY
run git add ledger.py sessions
run git commit -q -m "wip(WI-0001): ledger total; tests next (checkpoint)"
run git status --short
check "nothing is left uncommitted in the lane" test -z "$(git status --short)"
cd "$ALPHA" || exit 99
run poga lanes
check "the lane is listed as not landed" said "UNMERGED"

step "c5. Resume"
# A lane this young answers WAIT: its liveness evidence can take up to 30s to appear.
# A person coming back to the work would not notice; the drill waits it out.
printf '(the person comes back later: waiting until `poga resume` offers the lane)\n'
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  poga resume 2>&1 | grep -q "poga-1 *yes" && break
  sleep 5
done
run poga resume
check "poga resume offers the lane" said "poga-1 *yes"
run poga resume 1
check "resume also refuses before the runtime is signed in" no rcis 0
run env POGA_SKIP_PREFLIGHT=1 poga resume 1
check "resume re-enters the EXISTING lane" said "EXISTING lane 'poga-1'"
check "the runtime stops at login again" said "Not logged in"
cd "$LANE" || exit 99
run python3 session.py start
check "the same session and journal resume" said "$(basename "$JOURNAL" .md)"
run git log --oneline -3
check "the checkpoint commit is on the lane branch" said "checkpoint"
run grep -n "Checkpointed" "$JOURNAL"

step "c6. Finish and verify: poga test"
mkdir -p tests
: > tests/__init__.py
cat > tests/test_ledger.py <<'PY'
import unittest

from ledger import total


class TotalTest(unittest.TestCase):
    def test_sums_the_amounts(self):
        self.assertEqual(total([("rent", 900), ("food", 120)]), 1020)

    def test_nothing_is_zero(self):
        self.assertEqual(total([]), 0)


if __name__ == "__main__":
    unittest.main()
PY
run poga test
check "poga test PASSED" said "test: PASSED"

step "c7. Land: python3 session.py merge"
run python3 session.py merge --title "Tiny expense ledger" --confirm "land it" \
    --commit "feat(WI-0001): tiny expense ledger with tests"
check "the land succeeded" rcis 0
check "the lane landed on main" said "landed:"
cd "$ALPHA" || exit 99
run git log --oneline
check "main has ledger.py" git cat-file -e main:ledger.py
check "main has the tests" git cat-file -e main:tests/test_ledger.py
run poga work status WI-0001 --status done
run poga work show 1
check "WI-0001 is done" said "status: *done"
run poga lanes
check "the lane reads as merged" said "poga-1 .*merged"

step "d. Record a lesson in alpha, and share it"
cat >> "$ALPHA/architect-learnings.md" <<EOF

## $TODAY · scope: architect-general · Read the verdict line, not the tail

\`poga test\` prints PASSED, FAILED or DID NOT REPORT. A test run piped to \`tail\`
reports tail's exit code, so a red run can read green. Read the verdict line.
EOF
run tail -5 "$ALPHA/architect-learnings.md"
run poga share
check "share ran" rcis 0

step "e. Project beta: poga init, and the lesson arrives"
mkdir -p "$BETA"; cd "$BETA" || exit 99
run poga init --yes
check "init succeeded" rcis 0
check "the lesson was shared from alpha to beta" said "shared: alpha -> beta"
run ls proposed-edits/beta-arch/pending
check "the lesson is in beta's inbox" test -n "$(ls proposed-edits/beta-arch/pending 2>/dev/null)"
run grep -rh "Read the verdict line" proposed-edits/beta-arch/pending
printf '$ python3 session.py start   (banner head only)\n'
python3 session.py start </dev/null > "$DRILL/tmp/start2.json" 2>/dev/null
check "start's stdout is one JSON object (what the runtime's hook reads)" \
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$DRILL/tmp/start2.json"
python3 -c 'import json,sys; c=json.load(open(sys.argv[1]))["hookSpecificOutput"]["additionalContext"]; print("\n".join(l for l in c.split("\n\n")[0].splitlines() if l.startswith(("session:", "journal:", "inbox:", "announce:"))))' "$DRILL/tmp/start2.json" > "$OUT"
cat "$OUT"
check "beta's first session names the lesson in its inbox" said "inbox:.*alpha"
check "beta's first session is told the picker ban (the default)" \
  grep -q "pickers: denied" "$DRILL/tmp/start2.json"

step "f. The picker guard in a fresh install"
cd "$ALPHA" || exit 99
PAYLOAD='{"hook_event_name":"PreToolUse","tool_name":"AskUserQuestion","tool_input":{"questions":[]}}'
printf '$ echo <AskUserQuestion payload> | python3 session.py check-question\n'
printf '%s' "$PAYLOAD" | python3 session.py check-question > "$OUT" 2>&1; cat "$OUT"; echo
check "the picker is denied by default" said '"permissionDecision": "deny"'
python3 - <<'PY'
import json
c = json.load(open("session.config.json"))
c["interaction"] = {"pickers": "allow"}
json.dump(c, open("session.config.json", "w"), indent=2)
PY
run grep -A2 '"interaction"' session.config.json
printf '$ echo <AskUserQuestion payload> | python3 session.py check-question\n'
printf '%s' "$PAYLOAD" | python3 session.py check-question > "$OUT" 2>&1; cat "$OUT"; echo "(no output)"
check "with interaction.pickers = allow, the picker is allowed" test ! -s "$OUT"
run git checkout -- session.config.json
check "the setting is reverted" test -z "$(git status --short session.config.json)"

step "g. What was not used"
run git -C "$ALPHA" remote -v
run git -C "$BETA" remote -v
check "neither project has a remote" test -z "$(git -C "$ALPHA" remote)$(git -C "$BETA" remote)"
run find "$HOME/.config/poga" -type f
jobs_now > "$DRILL/tmp/jobs.after"
run diff "$DRILL/tmp/jobs.before" "$DRILL/tmp/jobs.after"
check "no scheduled job was added (launchd, cron)" rcis 0
run ls -A "$HOME"
run test -e /private/tmp/poga-drill-outside
check "still nothing written outside \$DRILL" no rcis 0

printf '\n==== Summary: %s check(s) failed\n' "$FAILS"
exit "$FAILS"
STEPS
set +e
sandboxed "$DRILL/home" bash "$DRILL/steps.sh" < /dev/null > "$DRILL/transcript.txt" 2>&1
rc=$?
set -e
echo "basic-acceptance: transcript at $DRILL/transcript.txt (exit $rc)"
exit "$rc"
