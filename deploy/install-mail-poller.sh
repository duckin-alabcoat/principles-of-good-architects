#!/usr/bin/env bash
# Install the federation outbox mail-poller LaunchAgent on THIS machine.
#
# Run this on EVERY machine that holds member repos — it is not Runner-only. Each
# machine's poller delivers the share of the outbox it can actually reach and leaves the
# rest queued for the machine that can: each machine's share is the members whose repos
# it holds.
#
# Run it from an interactive shell so the fetch can use your git credential helper. It
# fills the path-free template with this machine's absolute paths, installs to
# ~/Library/LaunchAgents, and bootstraps the agent.
#
# Idempotent: re-running re-renders and reloads. Uninstall with:
#   launchctl bootout user/$(id -u)/com.federation.mail-poller   # or gui/ — see below
#   rm ~/Library/LaunchAgents/com.federation.mail-poller.plist
#
# It provisions no credentials and spawns no Claude session. Delivery is a file copy;
# the only network call is a git fetch, and a fetch that fails degrades to "deliver what
# is already staged on disk" rather than failing the run.

set -euo pipefail

LABEL="com.federation.mail-poller"
# Repo root = parent of this script's dir (deploy/ lives at the repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/${LABEL}.plist.template"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

PYTHON="$(command -v python3 || echo /usr/bin/python3)"

# ── R4(a): a unit may not be pointed back at a working clone once this machine is sealed ──
#
# This is the exact route the defect took. Every installer here resolves FED_ROOT to the
# checkout it was RUN FROM, which on the Runner was the live trunk clone -- so the three
# federation units ran from a tree devbox advanced all day, and their own writes stranded
# it. Sealing the machine fixes the arrangement; only this stops somebody re-creating it by
# running the obvious script from the obvious place.
#
# SCOPED TO A MACHINE THAT HAS ADOPTED THE SEALED MODEL, which is what keeps it free of
# false positives: the test is whether a deploy tree for the federation EXISTS here. On a
# development machine there is none, nothing changes, and this installer behaves exactly as
# it always has. On a machine that has one, pointing a unit anywhere else is the regression
# (ADR-0135, WI-0360). POGA_DEPLOY_ROOT is honoured so the check follows the runner's own
# notion of where deploy trees live.
DEPLOY_ROOT="${POGA_DEPLOY_ROOT:-$HOME/deploy}"
SEALED_TREE="$DEPLOY_ROOT/federation"
if [ -d "$SEALED_TREE" ] && [ "${FED_ROOT#"$SEALED_TREE"}" = "$FED_ROOT" ]; then
  echo "ERROR: this machine has a sealed federation deploy tree at '$SEALED_TREE', but this" >&2
  echo "       installer would point $LABEL at '$FED_ROOT' -- a checkout somebody develops in." >&2
  echo "       A trunk clone is never a process root (ADR-0135). Its own unattended writes" >&2
  echo "       strand it against the moving trunk, which is what this arrangement replaced." >&2
  echo "       Run this installer from '$SEALED_TREE', or let the deploy cut the units over." >&2
  exit 1
fi

if [ ! -f "$FED_ROOT/curate/mail-poller.py" ]; then
  echo "ERROR: poller not found at '$FED_ROOT/curate/mail-poller.py' — run this from inside the federation repo." >&2
  exit 1
fi

# The poller's whole purpose is reaching member repos. A machine that can reach none of
# them will install a job that correctly does nothing forever, which is worth saying out
# loud at install time rather than discovering from an empty log a week later.
REACHABLE="$("$PYTHON" - "$FED_ROOT" <<'PY' 2>/dev/null || echo "?"
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "curate"))
try:
    import deliver
    print(len(deliver.locate_repos()))
except Exception:
    print("?")
PY
)"
echo "This machine can locate ${REACHABLE} member repo(s)."
if [ "$REACHABLE" = "0" ]; then
  echo "WARNING: none. The poller will install and deliver nothing until this machine" >&2
  echo "         can see at least one member repo (check reconcile-roots.local)." >&2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$FED_ROOT/.session-state"

sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__FED_ROOT__#${FED_ROOT}#g" \
    "$TEMPLATE" > "$DEST"

plutil -lint "$DEST"

# CAN A LAUNCHAGENT EVEN LOAD ON THIS MACHINE? Ask before installing one.
#
# A LaunchAgent loads at GRAPHICAL LOGIN. The design assumes the dev box is headless: no
# user logs in graphically, so there is no Dock, Finder or loginwindow for the user. An
# agent installed on such a machine would sit in ~/Library/LaunchAgents forever, correct
# and never loaded, while every surface reported a successful install. That is the
# certifies-without-looking failure this repo keeps writing detectors for, and a naive
# installer produces exactly it: `launchctl bootstrap` fails with a bare "5: Input/output
# error" and `load -w` aborts with no message at all, neither of which says the actual
# thing, which is that this machine cannot host an agent.
#
# Three states, kept apart on purpose (`declare-what-a-check-assumes`):
#   reachable   — bootstrap now, the normal path on a machine someone logs into.
#   present but unreachable from here (ssh/background) — install, skip the bootstrap, and
#                 say which one command loads it; it also loads by itself at next login.
#   absent      — REFUSE, and leave no plist behind. An agent that can never load is not
#                 an install, and pretending otherwise is worse than declining.
GUI_PROCS="$(pgrep -u "$(id -u)" -l "Dock|Finder|loginwindow" 2>/dev/null || true)"
if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
  DOMAIN="gui/$(id -u)"
elif [ -n "$GUI_PROCS" ]; then
  echo
  echo "This machine HAS a graphical session, but it is not reachable from this context"
  echo "(launchctl managername = $(launchctl managername 2>/dev/null || echo unknown))."
  echo "The plist is installed at:"
  echo "  $DEST"
  echo "It will load by itself at the next graphical login. To load it right now, run this"
  echo "from a terminal inside that session:"
  echo "  launchctl bootstrap gui/$(id -u) \"$DEST\" && launchctl enable gui/$(id -u)/${LABEL}"
  exit 0
else
  rm -f "$DEST"
  echo
  echo "REFUSING: this machine has no graphical login, so a LaunchAgent can never load" >&2
  echo "here — agents are started at graphical login and there is no Dock, Finder or" >&2
  echo "loginwindow for this user. No plist was left behind." >&2
  echo >&2
  echo "This is a property of the machine, not a failure of the poller. Drain the outbox" >&2
  echo "from the session harness instead — a headless box does work when a session runs," >&2
  echo "so a session-start drain costs nothing and needs no daemon:" >&2
  echo "  python3 $FED_ROOT/curate/mail-poller.py --quiet" >&2
  echo >&2
  echo "Install this agent on a machine someone logs into graphically (the Runner)." >&2
  exit 3
fi

# WHICH DOMAIN, AND WHY IT IS NOT THE ADOPT-RUNNER'S ANSWER.
#
# The adopt-runner MUST live in `gui/<uid>` — it spawns `claude -p`, which reads the
# login-keychain OAuth token, and only a logged-in Aqua session can unlock that. This job
# spawns nothing and reads no keychain: delivery is a file copy, and the only network
# call is a fetch whose credentials come from a config file. So `user/<uid>` is not a
# lesser fallback here, it is the correct domain for a job with no GUI dependency.
#
# We still PREFER `gui/` when it is reachable, because an agent bootstrapped there is
# loaded the same way login would load it and needs no special case later. But a headless
# or ssh context has no Aqua session at all — `launchctl managername` says `Background`
# and `gui/<uid>` returns "125: Domain does not support specified action" — and refusing
# to install there would mean the machine that most needs an unattended poller is the one
# that cannot have one.
#
# Double-loading (user/ now, gui/ at a later graphical login) is safe: the poller takes a
# lock and a second instance exits without doing anything.
# Reload cleanly (ignore "not loaded" on first install).
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "$DEST"
launchctl enable "${DOMAIN}/${LABEL}"

echo "Installed and bootstrapped ${LABEL}."
echo "  plist:    $DEST"
echo "  poller:   $FED_ROOT/curate/mail-poller.py"
echo "  python:   $PYTHON"
echo "  schedule: every 600s (StartInterval), not at load"
echo "  domain:   ${DOMAIN}"
echo
echo "Verify it is loaded:   launchctl print ${DOMAIN}/${LABEL} | head -20"
echo "Fire it once now:      launchctl kickstart -k ${DOMAIN}/${LABEL}"
echo "Then read its verdict: $PYTHON $FED_ROOT/curate/mail-poller.py --status"
echo
echo "--status is the liveness check: it reports NEVER RUN / STALE / OK separately, so a"
echo "job that silently stopped firing does not read as a quiet one."
