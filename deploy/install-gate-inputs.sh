#!/usr/bin/env bash
# Install the federation gate-input deriver (OPS-0009, WI-0407) on THIS machine.
#
# Run this ON DEVBOX, from a shell on that box. It fills the path-free template with this
# machine's absolute paths, injects the invoking user, installs to /Library/LaunchDaemons,
# and bootstraps the job.
#
# IT NEEDS sudo, AND THAT IS NOT INCIDENTAL. This is the one federation unit that is a
# LaunchDAEMON rather than a LaunchAgent, because the machine it belongs on has no
# graphical login: agents load at graphical login, and the design assumes devbox is
# headless. On such a box `launchctl managername` says Background, `gui/<uid>` is
# unreachable, and `launchctl bootstrap user/<uid>` fails with "5: Input/output error".
# `deploy/install-mail-poller.sh` reaches the same finding and REFUSES to install there;
# that is right for the poller, which can be drained from any session start instead, and
# wrong here, because a long serial full-suite run cannot ride a session start. That is exactly why OPS-0009 kept lapsing.
#
# Idempotent: re-running re-renders and reloads. Uninstall with:
#   sudo launchctl bootout system/com.federation.gate-inputs
#   sudo rm /Library/LaunchDaemons/com.federation.gate-inputs.plist
#
# It provisions no credentials and spawns no Claude session. The derive is local work; the
# only network calls are a git fetch and the land's push, which use the account's own ssh
# key.

set -euo pipefail

LABEL="com.federation.gate-inputs"
# Repo root = parent of this script's dir (deploy/ lives at the repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$SCRIPT_DIR/${LABEL}.plist.template"
DEST="/Library/LaunchDaemons/${LABEL}.plist"

PYTHON="$(command -v python3 || echo /usr/bin/python3)"
RUN_AS="$(id -un)"

if [ "$(id -u)" = "0" ]; then
  echo "ERROR: run this as yourself, not with sudo in front of the whole script." >&2
  echo "       It needs root only for the two writes into /Library/LaunchDaemons, and it" >&2
  echo "       asks for that itself. Run as root it would install a daemon whose UserName" >&2
  echo "       is 'root', and the deriver refuses to start as root -- so the unit would" >&2
  echo "       load cleanly and refuse every night." >&2
  exit 1
fi

# ── R4(a): a unit may not be pointed back at a working clone once this machine is sealed ──
#
# Every installer here resolves FED_ROOT to the checkout it was RUN FROM, which on the
# Runner can be the live trunk clone -- so the federation units would run from a tree
# development keeps advancing, and their own writes would strand it. Sealing the machine fixes the
# arrangement; only this stops somebody re-creating it by running the obvious script from
# the obvious place.
#
# SCOPED TO A MACHINE THAT HAS ADOPTED THE SEALED MODEL, which keeps it free of false
# positives: the test is whether a deploy tree for the federation EXISTS here. On a
# development machine there is none, nothing changes, and this installer behaves as it
# always has. POGA_DEPLOY_ROOT is honoured so the check follows the runner's own notion of
# where deploy trees live.
DEPLOY_ROOT="${POGA_DEPLOY_ROOT:-$HOME/deploy}"
SEALED_TREE="$DEPLOY_ROOT/federation"
if [ -d "$SEALED_TREE" ] && [ "${FED_ROOT#"$SEALED_TREE"}" = "$FED_ROOT" ]; then
  echo "ERROR: this machine has a sealed federation deploy tree at '$SEALED_TREE', but this" >&2
  echo "       installer would point $LABEL at '$FED_ROOT' -- a checkout somebody develops in." >&2
  echo "       A trunk clone is never a process root (ADR-0135)." >&2
  exit 1
fi

# THE INVERSE REFUSAL, and it is this unit's own. The three Runner units belong IN the
# sealed tree; this one must never be there. It derives the DEVELOPMENT trunk, and a
# deploy tree is a detached clone at a release tag -- deriving there would measure a tree
# nobody develops in and land nothing. The runner refuses this at run time too; refusing
# here as well means the mistake is caught when somebody is watching.
if [ -d "$SEALED_TREE" ] && [ "${FED_ROOT#"$SEALED_TREE"}" != "$FED_ROOT" ]; then
  echo "ERROR: '$FED_ROOT' is inside the sealed federation deploy tree." >&2
  echo "       $LABEL derives the DEVELOPMENT trunk and must be installed from the" >&2
  echo "       development checkout, on the machine that holds it." >&2
  exit 1
fi

if [ ! -f "$FED_ROOT/curate/gate-inputs-runner.py" ]; then
  echo "ERROR: runner not found at '$FED_ROOT/curate/gate-inputs-runner.py' -- run this from inside the federation repo." >&2
  exit 1
fi

# A LANE CANNOT HOST THIS. The ops-ledger write refuses from a linked worktree by design,
# so a unit installed against one would derive and land every night and then fail to
# record the run -- the expensive half done, the cheap half silently missing. Said here as
# well as in the runner because an install is when somebody is present to fix it.
if [ "$(git -C "$FED_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
   != "$(git -C "$FED_ROOT" rev-parse --path-format=absolute --git-dir 2>/dev/null)" ]; then
  echo "ERROR: '$FED_ROOT' is a linked worktree, not the main checkout." >&2
  echo "       The ops-ledger write refuses from a lane, so this unit would do the" >&2
  echo "       expensive half nightly and never record it. Install from the main checkout." >&2
  exit 1
fi

# WOULD AN AGENT HAVE WORKED HERE? Ask, rather than assume the answer that produced this
# daemon. On a machine that DOES have a graphical login, an agent is the better unit --
# no root, no system directory -- and somebody should be told that rather than left with
# a daemon because the first machine needed one.
if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
  echo "NOTE: this machine has a reachable graphical session (gui/$(id -u))."
  echo "      A LaunchAgent would work here and would need no root. This unit is a daemon"
  echo "      because devbox, the machine it was built for, has no graphical login at all."
  echo "      Installing the daemon anyway -- it is correct on both, just heavier here."
  echo
fi

mkdir -p "$FED_ROOT/.session-state"

TMP_PLIST="$(mktemp -t "${LABEL}")"
trap 'rm -f "$TMP_PLIST"' EXIT

sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__FED_ROOT__#${FED_ROOT}#g" \
    "$TEMPLATE" > "$TMP_PLIST"

# UserName is injected HERE and is not in the tracked template, because `render_unit_plist`
# substitutes exactly four tokens and refuses a plist carrying any other __UPPER__ one. A
# daemon with no UserName runs as root; the runner refuses to start as root, so a missing
# injection fails loudly at 02:30 rather than filling the checkout with root-owned files.
/usr/libexec/PlistBuddy -c "Add :UserName string ${RUN_AS}" "$TMP_PLIST" >/dev/null
plutil -lint "$TMP_PLIST"

if ! /usr/libexec/PlistBuddy -c "Print :UserName" "$TMP_PLIST" | grep -qx "${RUN_AS}"; then
  echo "ERROR: UserName was not injected -- refusing to install a daemon that would run as root." >&2
  exit 1
fi

echo "Installing ${LABEL} (needs root for /Library/LaunchDaemons) ..."
sudo install -o root -g wheel -m 0644 "$TMP_PLIST" "$DEST"

# Reload cleanly (ignore "not loaded" on first install).
sudo launchctl bootout "system/${LABEL}" 2>/dev/null || true
sudo launchctl bootstrap system "$DEST"
sudo launchctl enable "system/${LABEL}"

echo "Installed and bootstrapped ${LABEL}."
echo "  plist:    $DEST"
echo "  runner:   $FED_ROOT/curate/gate-inputs-runner.py"
echo "  python:   $PYTHON"
echo "  runs as:  $RUN_AS"
echo "  schedule: nightly 02:30 (StartCalendarInterval), not at load"
echo "  domain:   system (a daemon -- this machine has no graphical login for an agent)"
echo
echo "Verify it is loaded:   sudo launchctl print system/${LABEL} | head -20"
echo "Fire it once now:      sudo launchctl kickstart -k system/${LABEL}"
echo "                       (it takes ~18 minutes -- it runs the whole suite serially)"
echo "Then read its verdict: cat $FED_ROOT/.session-state/gate-inputs-runner.status.json"
echo
echo "The status file reports the outcome, whether the record landed, and what went into"
echo "the OPS-0009 ledger. A night that does not land also leaves a note in comms/ and"
echo "lets OPS-0009 go overdue, which the next session start prints first -- so a job that"
echo "silently stopped firing does not read as a quiet one."
