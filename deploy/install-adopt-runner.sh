#!/usr/bin/env bash
# Install the federation R3 adoption-runner LaunchAgent (ADR-0050) on THIS machine.
#
# Run this ON THE RUNNER, from an interactive shell in your logged-in GUI session
# (so launchd loads the agent into the Aqua domain where claude -p can read the
# login keychain — the subscription-login auth path). It fills the path-free template with
# this machine's absolute paths, installs to ~/Library/LaunchAgents, and bootstraps
# the agent.
#
# Idempotent: re-running re-renders and reloads. Uninstall with:
#   launchctl bootout gui/$(id -u)/com.federation.adopt-runner
#   rm ~/Library/LaunchAgents/com.federation.adopt-runner.plist
#
# It does NOT provision auth. On the subscription-login path, keeping the login's token
# fresh is a human step (use Claude interactively now and then); a stale token makes
# the nightly sweep abort at preflight with a comms/ blocked note — safe, never a
# half-applied brief. For a fully-unattended path, export ANTHROPIC_API_KEY in the
# EnvironmentVariables block instead (metered billing).

set -euo pipefail

LABEL="com.federation.adopt-runner"
# Repo root = parent of this script's dir (deploy/ lives at the repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/${LABEL}.plist.template"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

PYTHON="$(command -v python3 || echo /usr/bin/python3)"

# ── R4(a): a unit may not be pointed back at a working clone once this machine is sealed ──
#
# This is the exact route the defect took. Every installer here resolves FED_ROOT to the
# checkout it was RUN FROM, which on the Runner can be the live trunk clone -- so the
# federation units would run from a tree development keeps advancing, and their own writes
# would strand it. Sealing the machine fixes the arrangement; only this stops somebody re-creating it by
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
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"
CLAUDE_DIR="$(dirname "$CLAUDE_BIN")"

if [ ! -x "$CLAUDE_BIN" ]; then
  echo "WARNING: claude not found/executable at '$CLAUDE_BIN' — the runner will fail to spawn sessions." >&2
fi
if [ ! -f "$FED_ROOT/curate/adopt-runner.py" ]; then
  echo "ERROR: runner not found at '$FED_ROOT/curate/adopt-runner.py' — run this from inside the federation repo." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$FED_ROOT/.session-state"

sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__FED_ROOT__#${FED_ROOT}#g" \
    -e "s#__CLAUDE_BIN__#${CLAUDE_BIN}#g" \
    -e "s#__CLAUDE_DIR__#${CLAUDE_DIR}#g" \
    "$TEMPLATE" > "$DEST"

plutil -lint "$DEST"

# Reload cleanly (ignore "not loaded" on first install).
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed and bootstrapped ${LABEL}."
echo "  plist:   $DEST"
echo "  runner:  $FED_ROOT/curate/adopt-runner.py"
echo "  claude:  $CLAUDE_BIN"
echo "  python:  $PYTHON"
echo "  schedule: nightly 03:15 local"
echo
echo "Verify it is loaded:   launchctl print gui/$(id -u)/${LABEL} | head -20"
echo "Fire it once now:      launchctl kickstart -k gui/$(id -u)/${LABEL}"
echo "                       (then read $FED_ROOT/.session-state/adopt-runner.launchd.*.log)"
