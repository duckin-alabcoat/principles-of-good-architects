# ADR-0100: Dispatch spawns headless; the operator's machine attaches

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** the operator (the design — dispatch runs on the work machine, entirely inside tmux and out of sight; a second command, run on the machine the operator is sitting at, opens windows and attaches each one to a lane's tmux session on the work machine; the pre-approval; the mosh-then-ssh call; *"build for both"*). Federation Architect (the topology diagnosis, the watcher-not-script correction, the terminal-template shape, the probe).
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (no master session), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (lanes), [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) (dispatch is a launcher), [ADR-0099](0099-the-user-is-not-an-execution-surface.md) (the user is not an execution surface)
**Supersedes in part:** [ADR-0077](0077-spawn-surface-is-detected-and-tmux-is-first-class.md) — its surface *detection* stands; its assumption that the spawning machine is also the machine with windows does not.
**Reality:** **Built** — drilled end to end (session ~172), the operator on one machine, lanes on another. Tabs opened in one terminal window, two dispatches were discovered and attached without an id being typed, both lanes took live input, and **closing a tab detached rather than killing the lane** — verified from the work machine's side (`tmux ls` showed the closed lane alive with its scrollback intact), not taken on the operator's word. D4 records why both transports ship. Six defects sat between "built" and "works" — see the drill note below; none were findable by reading.
**Work items:** WI-0160 (the `attach` verb + watcher), WI-0161 (terminal templates + transport config), WI-0183 (a waiting lane raises its own tab), WI-0185/0186/0187/0188 (defects the drill surfaced)

## Context

A dispatch for two items spawned nothing, reported
`spawned 0, queued 2`, and sat `[draining]`. Two separate defects produced
that, and only the second is this ADR's subject.

The immediate cause was the concurrency cap: `_dispatch_advance` tests
`_dispatch_live_lane_count() >= cap` *before* attempting any spawn, two lanes already
existed, and the cap was 2 — so the loop broke on its first iteration and the launcher was
never reached. Confirmed rather than inferred: the `dispatch-spawn` coordination directory
does not exist, and it is created on the first call of `_dispatch_try_spawn`; the queue is
also still in its original order, where a failed spawn rotates it. That defect and its
unrecoverable aftermath are WI-0171 (withheld).

The deeper question surfaced when the operator asked why lanes could not simply open as ordinary
Claude Code sessions he could see, *"which is how it works today."* The answer is that it
already is how it works today — and that is exactly why dispatch cannot do it.

**The design assumes every lane on the work machine runs under a remote shell server**
(mosh or ssh). Each is a separate remote connection, each with its own shell running
`claude --worktree poga-N`. The windows are on the machine the operator sits at; the
sessions are on the work machine. The operator reaches in through N pipes.

That is the whole constraint in one sentence: **a process on the work machine cannot open a
window on the operator's machine.** A process started from a remote shell runs outside
the GUI login session, so window-opening calls cannot leave it — and this is not a
permission that can be granted. [ADR-0077](0077-spawn-surface-is-detected-and-tmux-is-first-class.md) reached the
same conclusion from the SSH case and drew the right local lesson (detect the surface, treat
tmux as first-class) but kept an assumption that no longer holds: that the machine spawning
lanes is the machine with windows. The GUI branch it preserved has been dead code ever
since, because it kept executing on the wrong side of the wire.

## Decision

**The window opens where the operator is. Spawning and attaching are two halves on two
machines, and the half that opens windows is initiated locally.**

### D1 — The two halves, and which one initiates

`poga dispatch` continues to run **on the work machine** and spawn headless tmux lanes. It
is unchanged in kind: it resolves, orders, gates, spawns, and stops being special.

`poga attach <dispatch-id>` runs **on the operator's machine** and opens one local window
per lane, each attached to that lane's tmux session on the work machine.

**The initiator is local.** What the operator types runs where their eyes are; the remote half is just
work. The inverse — the work machine reaching back to open a window on the laptop — requires
a listener over there, a reverse tunnel, and the laptop awake and reachable. Same feature,
three more failure modes, and it fails in the direction that produces silence.

### D2 — Attach is a watcher, not a script

Per [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D2
the wave trigger is the landing session: past the cap, lanes spawn *later*, as others land. A
one-shot attacher therefore opens windows for the first wave and goes quiet while the rest of
the queue runs headless — the silent-strand failure arriving through the fix for it.

`attach` polls the dispatch record, opens a window for each lane it has not yet attached, and
exits when the dispatch is complete or the operator stops it. It tracks what it has opened so
a second invocation does not double-attach.

### D3 — The terminal is a template, not a code branch

The existing spawn code hardcodes AppleScript for Terminal and iTerm2. **Ghostty** is a
third answer, and the next terminal will be a fourth.

The operator half takes a **launch template** — a command string with a `{cmd}` placeholder —
resolved from `attach_terminal` in `session.config.json`, with built-ins for Terminal,
iTerm2 and Ghostty and a free-form override. Adding a terminal becomes a config line rather
than a release. The first design gave Ghostty CLI templates of this shape:

```
ghostty -e "<cmd>"
open -na Ghostty --args -e "<cmd>"
```

#### Update, session ~172: both of those forms are wrong, and Ghostty is not a template at all

Running the built-in template refuted the paragraph above twice.

**The quoted form is broken.** `ghostty -e "<cmd>"` hands Ghostty the whole command line as
*one* argv element, which it passes to `/usr/bin/login`, which then looks for a program by
that entire name: `ghostty -e "sleep 60"` opens a window reading
`login: sleep 60: No such file or directory`, while `ghostty -e sleep 60` is silent. The
forms work when a human types a bare command, not
when a quoted command line is substituted into them. **A template therefore needs two
conventions, not one**: `{cmd}` (one shell-quoted argument, which is what
`tmux new-window` wants) and `{argv}` (raw, so the shell word-splits it into separate
arguments, which is what Ghostty wants). Each template declares which it takes.

**And Ghostty stopped being a template.** The requirement was *"one window, multiple tabs"*, and
Ghostty's CLI cannot do tabs by any route: `+new-window` is a GTK action that answers
`not supported on this platform`, and native macOS window tabbing does not apply either —
setting *Prefer tabs when opening documents* to **Always** still produced a window. What it
does have is a full AppleScript dictionary (`macos-applescript`, default true), so
**`ghostty` joins Terminal and iTerm2 on the AppleScript branch** and the CLI form survives
as the named escape hatch `ghostty-window`:

```applescript
new window with configuration {command:"…", wait after command:true}   -- first lane
new tab in <window> with configuration {…}                             -- every lane after
```

The load-bearing detail is that Ghostty's `window` class carries a **stable `id`**. `attach`
is a watcher, so tab four is opened twenty minutes after the window from a separate
`osascript` process; without a durable handle the design is impossible rather than merely
awkward. The id is held for the run and persisted per-desk, so a re-run rejoins the window
and two dispatches share one.

**Supported terminals.** Terminal, iTerm2 and Ghostty, all driven through AppleScript because
that is the route that gives one window with a tab per lane and a durable window handle;
`ghostty-window` for a window per lane from the CLI; and a free-form template for anything
else, declaring `{cmd}` or `{argv}`.

D3's *claim* survives — the terminal stays configuration rather than a release — but its
*mechanism* does not generalise: a scriptable terminal is a different kind of thing from a
launchable one, and treating them alike ships a template that cannot work.

**None of this was found by reading.** It was found by running the command, which is the
same lesson as the [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md)
update note: a path that has never executed is not built, however well covered.

### D4 — Transport is configured, with both built

`attach_transport` selects `mosh` (default) or `ssh`. the operator's call: *"build for both"* — start
with mosh, fall back to ssh if it misbehaves.

The reasoning on each side is real and unresolved by argument, which is why both ship. mosh
survives lid-close and roaming, which is the entire point of detach-and-reattach. Against it: mosh + tmux + a Claude TUI is three layers of terminal
handling stacked, and scrollback can be lost between them (mouse reporting captures the
wheel). mosh also needs its server binary on the remote PATH of a non-login shell. ssh is
better behaved with tmux scrollback and dies with the link. Both are supported so the operator
can pick per desk.

### D5 — Local is the degenerate case, not a separate path

When the work machine *is* the operator's machine, the attach command is a bare
`tmux attach -t <name>` with no transport wrapper. This is one branch in template resolution,
not a second code path — and it is the configuration in which the operator half can actually
be tested by an Architect, which matters given the Reality note above.

### D6 — Attach opens views; it never assigns work

The attaching half reads the dispatch record and opens windows. It does not spawn lanes, draw
spawn slots, hold claims, or advance the queue. Every one of those would reintroduce the
coordinator [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) retired,
wearing a local process instead of a session.

### D7 — What the probe established, and what it did not

Run live (`capture-the-probe`; scratch directory outside the repo, so no session
machinery fired):

| Probe | Command | Result |
|---|---|---|
| TUI in a never-attached tmux | `tmux new-session -d -s tuiprobe -x 80 -y 24 'claude'` then `capture-pane` | Renders correctly — full interactive prompt at 80x24 |
| Redraw at a client's real size | `tmux resize-window -t tuiprobe -x 150 -y 45` then `capture-pane` | Reflows correctly; rule spans 150 columns, no 80-column artifacts |
| External input into the TUI | `tmux send-keys -t tuiprobe Enter` | Accepted; trust prompt cleared and Claude Code started |

The first two are what this design rests on and they hold. The third was not required by this
design and was run because it settles a question the alternatives depend on — see
[ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md).

**Not established:** anything involving mosh, scrollback, or mouse reporting, and anything
involving a window actually opening. Those require the operator's machine and are the first
acceptance drill.

## Alternatives Considered

- **Open GUI tabs from the work machine** (the ADR-0077 branch as written). Topologically
  impossible across a remote connection, and no permission grant changes it. This is the
  option that has silently failed for three ADRs.
- **Reverse push — the work machine opens windows on the laptop.** Needs a listener, a
  reverse tunnel, and the laptop reachable. Rejected in D1: same feature, strictly more
  failure modes, failing toward silence.
- **The lane pool — the operator opens windows, idle lanes check in and are assigned work.**
  Genuinely attractive, and briefly recommended before the operator's design displaced it: it needs no
  spawning at all and every lane is attended by construction. Rejected because it caps
  concurrency at however many windows the operator felt like opening beforehand, and it
  requires preparation before dispatching. Kept on the shelf; it is the right shape if
  window-opening ever proves unreliable.
- **tmux windows inside one attached session, instead of one window per lane.** Cheaper and
  works today with no local half at all — new lanes appear in the operator's status bar as
  they spawn. Retained as the **overflow and fallback path**, not the default, because it
  requires the operator to live inside a multiplexer and gives up native window management.
- **Leave it as it is and let the operator attach by hand.** This is what the substrate does
  today, and it is precisely the pattern [ADR-0099](0099-the-user-is-not-an-execution-surface.md)
  named as the defect: work the Architect owns, escalated into the operator's shell.

## The first drill (session ~172)

The operator half had been "built and covered" — a test suite, the launch path
exercised with a recording template. It had never been *run*. When it finally was, **six
defects sat between built and working, and not one was findable by reading the code**:

| What broke | Why it survived being "covered" |
|---|---|
| `poga attach` was not a verb at all | It was a `session.py` subcommand; nothing tested the *typing* of the documented command |
| The operator had to carry a `D-xxxxxx` between machines | The id was an argument, so every test supplied one |
| `ghostty -e "<cmd>"` could never have worked | The template was a string nobody executed ([D3 update](#update-session-172-20260824-both-of-those-forms-are-wrong-and-ghostty-is-not-a-template-at-all)) |
| `mosh` unreachable — the tab's shell reads no profile | PATH is an environment fact; tests mock the launcher (WI-0187) |
| `attach` demanded a working directory | Every test ran inside a repo |
| The drill spec itself could only be run on real backlog items | Nobody had run it |

The last one is the sharpest. WI-0160's acceptance criteria said *"dispatch two items"*, so
the first drill claimed two real items, spawned two agents, drew two session numbers and
wrote two journals — to answer whether a window opens. the operator stopped it and ruled that a test
run uses no real work items, records no permanent data, consumes no real session numbers and
touches no journal or history. `poga drill` now spawns fake lanes; the
rehearsal costs nothing. **A drill that is unsafe to run is a drill that does not get run**,
and that is how a path stays unexercised for three sessions while looking finished.

The general lesson is [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md)'s,
restated with more evidence: *coverage is not execution.* Every one of these six lived in
the gap between a mocked boundary and a real one, and the drill is the only instrument that
sees into it.

## Consequences

**Closing a window stops producing an orphan.** Under the old arrangement, closing a remote
window leaves the shell server and its `claude` alive with nobody attached. Such a lane becomes
a ghost: the session roster reports it `interactive · idle` and the reaper reports it
`live — protected`, while no human is near it. Under this design, closing a window is a tmux **detach**: explicit,
legible, and reattachable. The cleanup those ghosts blocked is WI-0172 (withheld).

**The operator half cannot be verified by the Architect that builds it.** Opening a window
requires a GUI login session; the design assumes Architect sessions run in the background,
under a remote shell. This is a standing condition, not a one-time gap: it means D5's local case is the only
configuration an Architect can self-test, and every remote path ships owed a human drill. That
is `verify-in-the-created-configuration` naming its own limit rather than being quietly
violated.

**Cross-machine visibility does not come with this.** Attach is per-dispatch and per-machine.
Lanes running on two work machines produce two sets of windows and no aggregate view.
POGA publishes a machine-local status feed; any optional consumer may read it and build an
aggregate view. A fleet-wide view would also need the feed published somewhere shared, which
is WI-0004.

**Any such consumer is an enhancement, never a dependency.** POGA owns the feed, not what
renders it. This design is complete without a consumer. Dispatch health (`stalled` vs
`draining`) and lanes waiting on the operator are in the feed for whoever reads it.

**One more advice-string instance is now known-live.** `reap-lanes` still prints
`git branch -D <branch>` as its discard remedy — the exact string ADR-0099's evidence list
cites, still emitted after that ADR landed. Recorded against WI-0158 rather than patched here.

## References

- [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) — dispatch is a launcher; the wave trigger is the landing session
- [ADR-0077](0077-spawn-surface-is-detected-and-tmux-is-first-class.md) — surface detection and tmux; partially superseded here
- [ADR-0099](0099-the-user-is-not-an-execution-surface.md) — commands belong to the Architect
- [ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) — the attention floor
- Source conversation: Federation Architect session ~166, 2026-08-22
