# ADR-0026: Cross-machine execution is standard substrate

**Status:** Accepted
**Date:** 2026-06-20
**Deciders:** the operator, Federation Architect

## Context

The federation's session model names two machine roles — a `Laptop`, where work is
written, and an always-on `Runner` host (the machine map already lives in every system's
`session.config.json`). A recurring pattern: a program is written and tested on the
`Laptop`, but it needs to **run on the Runner** once it's ready (the Runner is the
role that stays on). The design supports a shared volume mounted at the same path on each
machine, so a program's files are visible at one path everywhere. The runner is reached by
an SSH host alias (`runner`) that the operator configures in `~/.ssh/config`.

The gap was that *this knowledge lived nowhere in the system*. Every Architect that
needed to run something on the Runner rediscovered how — found the hostname, worked out
the SSH invocation, picked a way to keep a long-running program alive past the SSH
disconnect (often a bare `nohup … &`, which gives no way to check or stop it later).
Same machines, same paths, same alias, every Architect — exactly the *accidental,
identical-everywhere* substrate the Standardize mission ([ADR-0023](0023-standard-operating-substrate.md))
exists to absorb. Per [P16](../principles/master.md#p16--avoid-duplication), knowledge
that is identical for every Architect belongs in one standard place, delivered to all,
not re-derived per system.

## Decision

Cross-machine execution is **standard operating substrate**. A "Running a program on
the other machine" section is added to the federation-owned standard section
([`standard-source.md`](../standard-source.md) → generated [`STANDARD.md`](../STANDARD.md),
injected into every session by `session.py start` per [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)
and shipped to fresh Architects via the kit). The recipe:

- **Reach** the other machine via the configured `runner` SSH alias: `ssh runner <…>`.
  Not rediscovered — the alias is machine-level config (`~/.ssh/config`), shared across
  every repo and Architect on the machine, so it needs no per-system entry.
- **No copy step.** Where the files are visible at the same path on both machines, a
  program is already present on the Runner at its path — SSH over and run it in place.
- **Run-to-completion:** `ssh runner 'cd <abs-path> && <cmd>'`.
- **Long-running (the common case):** start it in a **named `tmux` session** so it
  survives the SSH disconnect and stays manageable (reconnect to check status, read
  output, restart, or kill cleanly). `launchd` (a LaunchAgent plist) is the escalation
  only when a program must survive a Runner reboot or auto-restart.
- Issuing these is routine operational work ([`routine-ops-autonomy`](../habits/master.md#routine-ops-autonomy)):
  narrate at the conversation layer, then do it — don't ask whether to proceed.

The `runner` alias abstracts the underlying hostname and user, so the standard prose
references only the alias — no raw hostname/username embedded in a git-tracked (and
public-mirrored) file.

## Alternatives Considered

- **Bare `nohup … &` for persistence.** Zero dependencies, but gives no handle on the
  process afterward — can't see whether it's still up, can't read its output, can't stop
  it cleanly. Rejected as the default for programs the operator actively iterates on;
  observability matters more than avoiding the one-time `tmux` install. Kept as the
  dependency-free fallback.
- **`launchd` LaunchAgent as the default.** Proper macOS service management (survives
  reboot, auto-restart, managed logging), but heavyweight per-program ceremony for the
  common "start this and let it run" case. Demoted to the documented escalation for
  programs that genuinely need to be always-on services.
- **Per-system `session.config.json` field for the remote host.** Would duplicate an
  identical value into every system's config — the duplication [P16](../principles/master.md#p16--avoid-duplication)
  forbids. The SSH alias already lives once in `~/.ssh/config` and is machine-level, so
  the standard references the alias and stores nothing per-system.

## Consequences

- Every Architect inherits `ssh runner` + the tmux persistence pattern at session start
  via the injected `STANDARD.md`; none rediscovers it. Fresh Architters get it through
  the kit copy.
- The recipe is robust to the one dependency: the start sequence includes a one-time
  `command -v tmux >/dev/null || brew install tmux`, so it self-provisions rather than
  assuming `tmux` is present (it isn't stock on macOS).
- The `&&` / `||` inside `ssh runner '…'` is part of the quoted *remote* command, not a
  local compound; `check-bash` is quote-aware (single-quoted content is literal), so the
  recipe passes the [`no-compound-bash`](../habits/master.md#no-compound-bash) guard as a
  single tool call. The standard prose notes this so an Architect doesn't try to "fix" a
  non-violation.
- **Validated live the same session (2026-06-20).** An early probe concluded the host
  was unreachable because it bypassed the configured alias; going through the alias, as
  the recipe says, reached it. Running the full recipe end-to-end surfaced a deeper
  defect: a non-interactive `ssh runner '<cmd>'` does not read the login shell's
  interactive startup files, so tools installed by a package manager were not on PATH
  (the self-provisioning line as written would have failed too). Fixed at the machine
  level by putting the package manager's PATH setup in the shell file that
  non-interactive sessions also read, then re-ran the recipe end-to-end (named session
  start → `capture-pane` → `kill-session`) successfully. The standard recipe gains a
  **one-time machine prerequisite** note for the PATH setup.
- Reverse direction (`Runner` → `Laptop`) is symmetric in principle but not the stated
  use case; a `laptop` alias can be added the same way if it arises.

## References

- [ADR-0023: Standard operating substrate](0023-standard-operating-substrate.md) — the
  Standardize mission this addition falls under (same pipes, different houses).
- [ADR-0024: Standard role-doc section is generated and injected](0024-standard-role-doc-section-is-generated-and-injected.md)
  — the delivery mechanism (`standard-source.md` → `STANDARD.md`, injected + kit-shipped).
- [P16 `avoid-duplication`](../principles/master.md#p16--avoid-duplication) — why
  machine-identical knowledge lives in one standard place.
- Federation session 41 (2026-06-20) — the operator's three substrate adjustments; this is the
  first.
