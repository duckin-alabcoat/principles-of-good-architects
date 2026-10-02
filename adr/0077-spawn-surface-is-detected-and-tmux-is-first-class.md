# ADR-0077: The spawn surface is detected, and tmux is a first-class one

**Status:** Accepted
**Date:** 2026-07-29 (session ~107)
**Builds on:** [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) (dispatch — the launcher whose spawn step this fixes), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (`poga` lanes — what is being spawned), [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) (`poga` is fleet substrate, so a spawn assumption ships to every member)
**Deciders:** the operator (*"3 both"* — detect the session and pick, 2026-07-29); Federation Architect (the detection signal, the allowlist direction, and the honesty properties)
**Reality:** Built — session ~107, 25 tests (suite 954 → 979); the tmux surface verified live from a non-GUI login, the GUI surface unchanged and still pending one live confirmation
**Work item:** WI-0049 (withheld)

## Context

[ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md)
shipped `poga dispatch` with its Reality marked `Partial` for one reason: the terminal
spawn had never opened a tab. `osascript` did not error — it **blocked ~120 s** on the
Apple Event and returned `-1712`. The diagnosis recorded at the time was *macOS Automation
permission is not granted*, and **WI-0049** was filed as a chore for the operator: grant it in
System Settings, then one live dispatch confirms end-to-end.

That diagnosis was incomplete, and the incompleteness mattered.

A session reached over a remote shell is not a process inside the user's GUI login:
there, `launchctl managername` returns **`Background`**, not `Aqua`. Apple Events cannot
reach a GUI application from outside the user's Aqua login session **at all**, and the
Automation grant is scoped to applications *inside* that session. There was nothing to
grant. An Apple Event fired from such a session blocks, which is the original signature.

The consequence is larger than one ungranted permission. An always-on runner host is
routinely driven without a GUI login — a mode the standard section documents. `poga` is
fleet substrate ([ADR-0070](0070-worktree-lanes-are-fleet-substrate.md)),
so a spawn mechanism that only works from a GUI login was shipped to every member as the
only way to spawn. **Dispatch's whole design depends on this step**: the wave trigger is the
landing session, so a lane that cannot spawn its successor does not merely fail once — it
silently ends the chain.

This ADR also records a second-order finding about the earlier one: the ~105 conclusion was
drawn from an error code and a plausible cause, and it named the fix (*grant permission*)
without checking the assumption underneath it (*that a grant could apply here*). It is the
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) failure
one level up — a diagnosis that folded "cannot work from here" into "not yet permitted."

## Decision

### D1 — The surface is detected, and `launchctl managername` is the signal

`spawn_surface` in `session.config.json` defaults to **`auto`**. Under `auto`:

| `launchctl managername` | Surface chosen |
|---|---|
| `Aqua` | `Terminal` (GUI tab, as before) |
| anything else, or undeterminable | `tmux` |

The signal is deliberately this one rather than `SSH_CONNECTION` or `TERM_PROGRAM`: it
answers the question actually being asked — *is this process inside the GUI login session?*
— and so it is also right for a LaunchAgent, a cron job, and a `screen` session, none of
which set an SSH variable. The
[adoption runner](0050-headless-background-adoption-runner.md) already runs in exactly such
a context.

The detector must be cheap and synchronous. Probing with a real Apple Event would be the
authoritative test and is refused: **it is the ~120 s block being detected.** A detector
that reproduces the failure it detects is not a detector.

### D2 — `tmux` is a first-class spawn surface, not a fallback

`tmux` joins `SPAWN_SURFACES` as a peer of `Terminal` and `iTerm2`, and a new
`GUI_SPAWN_SURFACES` names the subset that drives an app over Apple Events. A dispatched
lane on this surface is a detached, named session — `poga-<dispatch-id>-<item-id>` — started
as a plain child process, which is why it works identically from a GUI tab, a remote shell,
and a LaunchAgent.

The name carries the dispatch id **and** the item, so `tmux ls` answers *what is running and
why* without consulting the dispatch record — the same provenance-in-the-artifact argument
that put those fields in the spawn slot.

The standard section already blesses named tmux sessions as the mechanism for running
long-lived work on the other machine, and for the same reason given here: unlike `nohup … &`
it can be reconnected to, read, restarted, and killed cleanly. This ADR adopts an existing
federation mechanism for a new caller rather than inventing one
([P16](../principles/master.md#p16--avoid-duplication)).

### D3 — Detection resolves toward the surface that works

Only `Aqua` selects a GUI surface. Every other value — including one macOS might introduce
later — resolves to `tmux`.

The asymmetry is the safety property. Guessing wrong toward tmux costs a detached session
where a tab was wanted, visible and instantly correctable. Guessing wrong toward a GUI
surface costs a 20 s block **per item** on lanes that never open, which is the silent-strand
failure dispatch exists to prevent, arriving through the launcher
([P19](../principles/master.md#p19--cap-what-can-run-away)).

### D4 — An explicit declaration is honoured; the mismatch warns rather than rewrites

If `spawn_surface` names a GUI surface and this session cannot use it, dispatch **warns up
front, before spawning**, naming the manager it actually found, the per-item cost, and the
two ways out. It does **not** silently substitute tmux.

A detector overriding a human's stated configuration is the failure mode where the operator
can no longer predict what their own config does. Warn-and-honour keeps the declaration
authoritative and still removes the 20-s-times-N surprise. The warning is up front because
after the fact it is indistinguishable from a hung launcher.

### D5 — No usable surface is a refusal, with both ways out named

`auto` with neither an Aqua session nor tmux installed **refuses** — it does not fall back
to trying an Apple Event and hoping. The refusal names the manager found, `brew install
tmux`, and the alternative of dispatching from a Terminal tab in the GUI session. Same for
an explicit `spawn_surface: tmux` with tmux absent.

Consistent with ADR-0075's rule that an unlisted surface is refused rather than tried: the
phantom-session saga was a launcher-surface problem, and *try it and see* is how a session
ends up unowned.

### D6 — The plan states where lanes will open, and a tmux lane reports how to reach it

Two visibility obligations follow from the surface no longer being fixed:

- **The plan** — dispatch's one approval surface — names the surface in outcome terms.
  *"10 Terminal tabs"* and *"10 detached tmux sessions, each printing an attach command"* are
  materially different things to approve, and the operator must not have to infer which one
  they agreed to.
- **The spawn report** carries each tmux lane's `tmux attach -t <name>` line. No window
  appears, so the attach command is not decoration — it is the only thing that makes the lane
  reachable. This required threading success detail through a layer that previously discarded
  it, because a lane running where nobody is told to look is precisely the strand this design
  prevents.

## Consequences

**Good.**

- Dispatch works in the mode the design assumes: driven over a remote shell. Before this, the launcher's
  central step could not function outside a GUI login — the normal case, not an edge case.
- A tmux lane survives disconnection and can be reattached from another shell, which a GUI tab
  is not. For a long-running lane driven remotely this is better than what was originally specified,
  not a degradation.
- The failure mode is now named rather than mysterious: a `Background` manager produces an
  up-front sentence instead of a 20 s stall, in the one place the operator is looking.
- The GUI path is untouched, so granting Automation permission still buys the tab behaviour
  where a GUI session exists.

**Bad, and accepted.**

- **Two spawn paths to maintain**, and the GUI one remains the less-exercised. Mitigated by
  the shared `_dispatch_open_tab` entry point and by tests on both branches; not eliminated.
- **A tmux lane does not announce itself.** Nothing pops up; the operator must attach. This is
  intrinsic to the surface, and D6 is the mitigation rather than a fix.
- **`launchctl managername` is a macOS-specific, undocumented-ish signal.** If Apple changes
  the string, D3 sends everything to tmux — the safe direction, but it would silently stop
  choosing tabs where tabs work. A live GUI-session confirmation is the only thing that
  detects that, and it is owed.
- **The Aqua branch of the detector is still unverified here.** Every test mocks the signal
  precisely because the answer would otherwise depend on which session runs the suite. That
  `Aqua` is the value returned inside a GUI login is taken from the macOS session model, not
  from a probe in this session — a non-GUI session cannot produce one. Named rather than assumed
  green ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

## Alternatives considered

**Grant Automation permission and dispatch only from a local Terminal tab.** The original
WI-0049. Cheapest, and it does make tabs work. Rejected as the *only* path because it makes
the launcher unusable outside a GUI login, and because the wave trigger living in the landing session
means every dispatched lane must also be able to spawn — so the constraint would propagate to
every lane, not just the first.

**Detect with `SSH_CONNECTION` / `SSH_TTY`.** Simpler and needs no subprocess. Rejected: it
answers *did I arrive over SSH*, not *am I in the GUI session*, so it is wrong for a
LaunchAgent, a cron job, and a nested shell — including the adoption runner, which is exactly
a non-Aqua non-SSH caller.

**Probe with a real Apple Event and cache the result.** Authoritative. Rejected under D1: the
probe *is* the 120 s block, so the first dispatch of any session would pay it, and a cache
keyed on nothing stable would be wrong the moment the session type changed.

**Always use tmux; drop the GUI surface.** One path, no detection, works everywhere.
Genuinely tempting and close. Rejected because a tab appearing is real value when the operator is
at the machine, and the operator asked for both — but recorded because if the GUI branch stays unexercised
long enough, collapsing to one surface is the honest simplification rather than maintaining a
path nobody runs.

**`screen`, or a bare `nohup … &`.** Both spawn from any session. Rejected on the standard
section's existing argument: neither can be reconnected to and read the way a named tmux
session can, so a lane started that way is unreachable the moment you look away — the strand,
again.
