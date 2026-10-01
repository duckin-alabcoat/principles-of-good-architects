#!/usr/bin/env bash
# Install the federation deploy sweep (ADR-0103 D8) on THIS machine.
#
# SUPERSEDED (WI-0316, ADR-0103 amendment 2026-09-13). You almost certainly do not want
# this. The sweep's trigger is now `curate/mail-poller.py`, which already runs on a 600s
# schedule on every machine and calls `deploy/runner.py --unattended` after a refresh that
# completed. That verb carries the machine gate — the machine the channel declares as the
# federation's unattended writer, NOT a registry key; `deploy_host` was proposed and
# withdrawn, so do not go looking for it in registry.json — and the
# D9 drill, and it needs no install step, no `launchctl bootstrap`, and nobody to run it.
#
# This script is KEPT rather than deleted for one reason: deleting an installer does not
# retire a job that is already bootstrapped on a machine, and the Runner is not reachable
# from where the federation's sessions run. If this job IS loaded somewhere, both triggers
# call the same `sweep()` and the runner tolerates that; retire it with the bootout below
# when someone is next at that machine.
#
# Its D9 gate still works, and now opens by itself: `runner.py --drill` writes a `drill`
# record whose nested `rollback_verify` key is exactly what the grep below looks for.
#
# READ THIS BEFORE RUNNING IT. Per ADR-0103 D9 the sweep is installed only AFTER the
# on-demand verb has deployed successfully twice and a rollback has been drilled. That is
# not ceremony: an unattended deployer whose failure path has never executed is a
# deployer with an unproven claim at the centre of it, and the failure path is the entire
# safety argument. This script therefore checks the ledger and REFUSES to install until
# the evidence exists — pass --force only if you are deliberately overriding that, and
# know that you are.
#
# Idempotent: re-running re-renders and reloads. Uninstall with:
#   launchctl bootout gui/$(id -u)/com.federation.deploy-sweep
#   rm ~/Library/LaunchAgents/com.federation.deploy-sweep.plist
#
# Unlike the adoption runner this has no auth story at all: the deploy runner is plain
# code and never invokes `claude`, so there is no keychain to keep fresh and nothing that
# breaks when a token expires.

set -euo pipefail

LABEL="com.federation.deploy-sweep"
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
LEDGER_DIR="${POGA_DEPLOY_LEDGER_DIR:-$HOME/.local/state/poga/deploy}"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

if [ ! -f "$FED_ROOT/deploy/runner.py" ]; then
  echo "ERROR: runner not found at '$FED_ROOT/deploy/runner.py' — run this from inside the federation repo." >&2
  exit 1
fi

# ---- the D9 gate -------------------------------------------------------------------
# Evidence, not assertion: at least one system must have a ledger entry showing a
# completed deploy, and at least one must record a rollback that actually happened.
deployed=0
rolled_back=0
if [ -d "$LEDGER_DIR" ]; then
  for f in "$LEDGER_DIR"/*.json; do
    [ -e "$f" ] || continue
    grep -q '"status": "deployed"' "$f" && deployed=$((deployed + 1))
    grep -q '"rollback_verify"' "$f" && rolled_back=$((rolled_back + 1))
  done
fi

if [ "$FORCE" -eq 0 ] && { [ "$deployed" -lt 1 ] || [ "$rolled_back" -lt 1 ]; }; then
  echo "REFUSED: the deploy sweep is not installable yet (ADR-0103 D9)." >&2
  echo >&2
  echo "  systems with a completed deploy in the ledger: $deployed  (need at least 1)" >&2
  echo "  systems with a drilled rollback in the ledger: $rolled_back  (need at least 1)" >&2
  echo "  ledger: $LEDGER_DIR" >&2
  echo >&2
  echo "The sweep runs unattended and its whole safety story is the smoke gate and the" >&2
  echo "rollback. Installing it before either has run for real would be trusting a claim" >&2
  echo "nothing has tested. Deploy a system on demand, then break its smoke check once on" >&2
  echo "purpose and watch the rollback happen — then this gate opens by itself." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$FED_ROOT/.session-state"

sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__FED_ROOT__#${FED_ROOT}#g" \
    "$TEMPLATE" > "$DEST"

plutil -lint "$DEST"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Installed and bootstrapped ${LABEL}."
echo "  plist:    $DEST"
echo "  runner:   $FED_ROOT/deploy/runner.py --sweep"
echo "  python:   $PYTHON"
echo "  schedule: every 600s (the tag is the promotion — the sweep only notices one)"
[ "$FORCE" -eq 1 ] && echo "  NOTE: installed with --force, overriding the ADR-0103 D9 evidence gate."
echo
echo "What is running where:  $PYTHON $FED_ROOT/deploy/runner.py --status"
echo "Rehearse the sweep:     $PYTHON $FED_ROOT/deploy/runner.py --sweep --dry-run"
