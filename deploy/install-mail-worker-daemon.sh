#!/usr/bin/env bash
# Install the devbox mail worker (WI-0419) on THIS machine, as a LaunchDaemon.
#
# Run this ON DEVBOX, from a shell on that box, from the MAIN checkout -- and WITHOUT sudo
# in front: it asks for root itself, only for the two writes into /Library/LaunchDaemons.
#
# What it does, in order, and everything before the sudo is done as you:
#   1. pins a release clone at the highest vX.Y.Z tag (~/.local/share/poga/federation-mail-release)
#   2. writes ~/.config/poga/federation-service.json if absent -- owns refs/heads/dev/messages,
#      reads refs/heads/runner/messages (the Runner's pair, mirrored; deploy/devbox_mail.py)
#   3. seeds the three config files production requires from this checkout, once
#   4. validates the whole configuration through production.resolve, as the worker will
#   5. renders the daemon plist, then installs and bootstraps it (the only root step)
#   6. advances: makes the seeded mailboxes.json equal the pinned release's and fills the
#      acknowledgment map (WI-0428/0429). `poga release cut` runs the same step on every tag.
#
# A DAEMON because the design assumes devbox is headless (no graphical login), and an
# agent never loads there; the reasoning is recorded in com.federation.gate-inputs.plist.template.
#
# Idempotent: re-running advances the release pin, re-renders and reloads. Uninstall with:
#   sudo launchctl bootout system/com.federation.mail-worker
#   sudo rm /Library/LaunchDaemons/com.federation.mail-worker.plist

set -euo pipefail

LABEL="com.federation.mail-worker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="/Library/LaunchDaemons/${LABEL}.plist"
PYTHON="$(command -v python3 || echo /usr/bin/python3)"
RUN_AS="$(id -un)"

if [ "$(id -u)" = "0" ]; then
  echo "ERROR: run this as yourself, not with sudo in front of the whole script." >&2
  echo "       It asks for root itself, only to install the daemon. Run as root, the release" >&2
  echo "       clone, config and state would be root-owned and the worker could not write them." >&2
  exit 1
fi

# The config seeds from this checkout and names its inbox, so it must be the development
# checkout that owns the federation inbox -- never a lane, whose tree is gone at the land.
if [ "$(git -C "$FED_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
   != "$(git -C "$FED_ROOT" rev-parse --path-format=absolute --git-dir 2>/dev/null)" ]; then
  echo "ERROR: '$FED_ROOT' is a linked worktree, not the main checkout." >&2
  echo "       Install from the main checkout, which owns the federation inbox." >&2
  exit 1
fi

TMP_PLIST="$(mktemp -t "${LABEL}")"
trap 'rm -f "$TMP_PLIST"' EXIT

PREPARED="$("$PYTHON" "$FED_ROOT/deploy/devbox_mail.py" --checkout "$FED_ROOT" --python "$PYTHON" \
  --user "$RUN_AS" --out "$TMP_PLIST" "$@")"
echo "$PREPARED"
PINNED="$(printf '%s' "$PREPARED" | "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["tag"])')"
plutil -lint "$TMP_PLIST"

echo "Installing ${LABEL} (needs root for /Library/LaunchDaemons) ..."
sudo install -o root -g wheel -m 0644 "$TMP_PLIST" "$DEST"
sudo launchctl bootout "system/${LABEL}" 2>/dev/null || true
sudo launchctl bootstrap system "$DEST"
sudo launchctl enable "system/${LABEL}"

# The installer seeds the roster once; advance makes it equal the pinned release's (WI-0428).
"$PYTHON" "$FED_ROOT/deploy/devbox_mail.py" advance --tag "$PINNED"

echo "Installed and bootstrapped ${LABEL}."
echo "  plist:   $DEST"
echo "  runs as: $RUN_AS, every poll interval and at load"
echo
echo "Read its health:  cat ~/.local/state/poga/federation/runtime/mail-worker.health.json"
echo "Its ref appears on origin (refs/heads/dev/messages) with the first message it"
echo "publishes -- the acknowledgment of the first Runner message it delivers."
