# Standard role-doc section — reference

> **GENERATED — do not edit by hand.** The reference tier of the standard section
> (ADR-0136): material you consult when you deliberately use a named surface.
> Delivered to every member and **exactly as binding as `STANDARD.md`**; it is
> simply not injected into every session. Read it on demand. Edit the source.

## Standard substrate artifacts

Every Architect runs on this substrate. The script is identical everywhere; only
`session.config.json` differs per system (ADR-0022 (withheld) code channel).

| Artifact | Path | Role |
|---|---|---|
| Session-ritual harness | `session.py` | The mechanical half of the session rituals (ADR-0020 (withheld)) — identical in every repo. `start` (a `SessionStart` hook) diagnoses git state, stamps machine/time, reaps provably-dead journals, writes this session's journal from first activity, compiles the handoff/STATUS views, derives the ordinal, and injects `CANON.md` + `STANDARD.md` + the prior entry + the user profile; a second hook, `announce`, prints the banner. `end` closes this session's journal, recompiles, stamps `STATUS.md`, prints the end banner, and commits. `compile` regenerates the handoff + census standalone. `heartbeat`/`SessionEnd` hooks maintain the gitignored `.session-state/` liveness sidecar (ADR-0054 (withheld)); `check-bash` + `check-question` are its `PreToolUse` guards; `stamp` is the non-Claude binding's entry point (ADR-0041 (withheld)). |
| Harness config | `session.config.json` | The only per-system part of the harness — architect name/id, role-doc + handoff filenames, timezone, machine map, inbox. The session-picker label is **derived** from `architect_id` (the ADR-0006 identifier), never hand-set, so it cannot be given a non-standard or conflated value. |
| Canon digest | `CANON.md` | The inherited universal set (every Accepted principle + habit, one line each) per ADR-0022 (withheld). Generated in the federation; injected each session. Do not hand-edit. |
| Standard section | `STANDARD.md` | The standard role-doc section, generated in the federation and **injected** into every session per ADR-0024 (withheld). Do not hand-edit. |
| Standard reference | `STANDARD-REFERENCE.md` | This file — the reference tier of the standard section, generated from the same source and **delivered but not injected** per ADR-0136 (withheld). Exactly as binding as `STANDARD.md`; read it on demand. Do not hand-edit. |
| Harness hooks | `.claude/settings.json` | Wires the two harness hooks: `SessionStart` → `session.py start`; `PreToolUse(Bash)` → `session.py check-bash`. |
| Session journals | `sessions/journal/<id>.md` | The durable per-session narrative, one file per session, written from first activity — **the file you author** (ADR-0051 (withheld) C2 / ADR-0055 (withheld)). Each session appends only to its own; frontmatter records causality as facts (`base-commit`, `started`, `ended`, `claude-session-id`). Tracked in git. |
| Session handoff | `session-handoff.md` | **GENERATED** newest-first state-transfer view, compiled from the journals + the frozen `sessions/pre-journal-archive.md` by `session.py compile`. Carries the SESSION LOG index; ordinals are a derived label, not a counter. Do not hand-edit — write your journal instead. |
| Roadmap & status | `ROADMAP.md` | The human-facing **outcome** view of the system — its format is defined just below and is **federation-owned**; its content is yours. |
| Work-item store | `work-items/WI-NNNN-slug.md` | Your backlog as data, one file per item, with durable `WI-NNNN` ids. **You already have this** — see below. Access it through `poga work`; do not hand-edit item files. |
| Operational obligations | `ops-items/OPS-NNNN-slug.md` | The things that get **run** rather than built — checks, drills, rollouts — with a cadence (or none, for a one-time check) and a run history. **You already have this too** — see below. Access it through `poga ops`; renders in the same list as your work items. |

## Where your files live — the `layout` block

**You do not have to do anything here.** The defaults are the layout every system
already runs, so an untouched `session.config.json` behaves exactly as it does today.
This section exists so a system whose folders differ has somewhere to *say so* — the
shared harness looks things up inside your repo (handoff, ADRs, mailbox, work-item
store, journals), and a hardcoded federation folder name read against a differently
arranged member returns a wrong answer shaped like a right one: not an error, but
good news, which is worse.

Declare any path that differs, in one place:

```json
"layout": {
  "adr_dir":      "docs/adr",
  "inbox":        "mail/<your-arch-id>/pending",
  "work_items":   "backlog",
  "journal_dir":  "sessions/journal",
  "handoff":      "session-handoff.md",
  "status_file":  "STATUS.md",
  "role_doc":     "your-arch.md",
  "user_profile": ""
}
```

Only include the ones you actually move. Resolution runs most-specific-first — the
`layout` block, then the older top-level key of the same name (which keeps working, so
nothing you have declared needs rewriting), then the standard default.

An **empty** value means *no honest default exists*, not *use the usual place*: an
absent `inbox` means you have **no mailbox**, a different fact from having one at the
standard path, and the substrate reports those differently. This is also the **only**
place these paths are named — shared code naming one as a literal is a defect worth
sending back through the receipt ritual.

## Roadmap deliverable (`ROADMAP.md`)

Every system emits a `ROADMAP.md` at its repo root — the human-facing answer to *"what's the real status, without the day-to-day minutia?"* It is the middle layer between `STATUS.md` (the machine signal another system reads) and `session-handoff.md` (the per-session blow-by-blow). Compiled on the trunk at session-end, like both of them.

**This format is standard and federation-owned** — the shape is injected and rolls out, the content is yours (single-source-and-deliver (withheld)). When the federation changes it, reshape at your next session-end. Don't invent your own sections or reorder these; add system-specific detail *within* a section.

Required shape:

1. **Title + orientation blurb** — name the system; one line on what this file is and how it pairs with `STATUS.md` / `session-handoff.md`.
2. **As-of line** — date · session number · version · phase. (Real values from a tool call — `no-fabricated-data` (withheld).)
3. **`## Now — where we are`** — current state in plain English; the present focus.
4. **`## Recently shipped — outcomes`** — **GENERATED**, compiled from each session's own journal `### Outcome` section (ADR-0105 (withheld)). Recent sessions stated as **what changed for the system**, not which artifacts were committed (`frame-work-in-outcome-terms` (withheld)). *"X can no longer be fooled by a stale mirror,"* not *"committed reconcile.py."* You write this in your journal; never here.
5. **`## Next — immediate`** — what's being picked up now / next.
6. **`## Backlog — the full plan`** — everything planned for the future, grouped sensibly (initiatives, blocked items, open policy questions); items graduate into *Next* when picked up. The full list the day-to-day hides.
7. **Maintenance footer** — a one-line reminder: refresh at session-end alongside `STATUS.md`; move shipped items Next → Recently-shipped as an outcome; pull up Backlog items as picked up; an entry that can't be written as "what changed for the system" belongs in the handoff, not here.

Single-writer: **the trunk's compile**, not a lane (ADR-0105 (withheld)). *Recently shipped* compiles from journals, *Next*/*Backlog* from the work-item store, and the prose under `## Now` is hand-authored — edited from the main checkout. The rule that decides which is which is the markers, not the heading: whatever sits between a `BEGIN GENERATED` / `END GENERATED` pair belongs to the compile, anywhere in this file, and `## Now` may open with such a region above its prose (the federation renders its finish-line verdict there). A system with nothing to render there simply omits the markers. A blocked item is named in its section with the blocker, not hidden.

## Work-item store (`work-items/`) — you already have this

The store's code has shipped inside your `session.py` and `poga` for months — they are
byte-identical substrate — so if you have never heard of it, that is our omission, not
yours.

**What it is.** Your backlog as *data* rather than prose: one file per item under
`work-items/`, each with a durable `WI-NNNN` id (ADR-0073 (withheld)).
Ids are **drawn** from a shared allocator, never chosen
(`numbers-are-drawn-never-picked` (withheld)), so
two concurrent lanes cannot mint the same one. An item owns its `id` / `title` / `status`
/ `section` / `blocked-by` / `group` / `source`; claims live in the coordination store, so
there is exactly one writer per fact (P13 (withheld)).

**The front door is `poga work`.** Every field an operator touches has a command:

```
poga work list                 # the whole store
poga work show <id>            # one item in full
poga work new "<title>"        # draw an id and create an item
poga work edit <id> …          # title / notes / group
poga work status <id> …        # status / section / blocked-by
poga work check                # is the store structurally sound?
```

**Hand-editing an item file is not a supported path** — it is how an id once ended up
with two files, and how another was deleted rather than superseded. `poga work check` is
what tells you the store is sound (sequence gaps, unknown statuses, a `blocked-by` naming
a nonexistent id, an item blocking itself). Use `poga work`, not an editor; it anchors on
your **main checkout**, so a lane still drives the one real store, not its frozen copy —
and a write aimed past it, straight at `session.py` from inside a lane, is refused with
the front-door command to run instead (see the obligations section below for why).

**The store commits itself — never hand that job to the user.** Every `poga work` write
is saved by the same command that made it, and prints the receipt: the short sha and the
repo it landed in.

That commit lands in your **main checkout**, because that is where the store lives. So
from a lane your own `git status` shows **nothing** and `git add work-items` stages
nothing at all — the system working correctly, not a stranded edit. Read that silence
the wrong way and you will reach for the one move that is always wrong: telling the user
to run git by hand in another checkout. **Do not** — it has been done for work already
committed, in a checkout already clean, with a command that could not have worked.
Before concluding anything is unsaved, check what the store actually holds:
`poga work show <id>` (or `poga work check`). `poga work` commands anchor on your main
checkout directly, so reading through them confirms the write landed without raw git
commands across checkout boundaries. If a write genuinely fails it says so on stderr
and names its own retry (`poga work commit`) — the only case where anything is owed,
and yours to run, not the user's.

A prose backlog cannot be claimed by a lane or say what blocks what. `ROADMAP.md`'s
*Next* and *Backlog* render **from** the store, so the roadmap is not a second copy that
drifts (P16 (withheld)).

**An empty `work-items/` is perfectly conformant** — the capability is the ability to run
a store, not evidence that you do.

## Operational obligations (`ops-items/`) — the second half you also already have

Same as above: the code shipped inside your `session.py` and `poga` before anything in
your injected context named it.

**What it is.** A work item is a thing to *build*; an **obligation** is a thing to
**run** — a backup check, a restore drill, a fleet push. They are different kinds and
mixing them corrupts both lists, so obligations live under `ops-items/` with their own
`OPS-NNNN` ids, drawn from the same allocator. An obligation carries a `cadence`
(`monthly` / `quarterly` / `annual`) or, if you omit one, is a **one-time** check — the
absence of a cadence is what tells the two apart. A recurring item's `due` date is
**derived** from its last run and never typed.

**The front door is `poga ops`**, exactly mirroring `poga work`:

```
poga ops new "<title>" [--cadence …|--due …]   # draw an id and create an obligation
poga ops ran <id> --result pass|fail|findings  # record a run; rolls `due` forward
poga ops status|edit|show <id> …               # the fields, and one item in full
poga ops commit                                # save anything left uncommitted
poga ops list                                  # the SHARED list — ops rows render
                                               # alongside the work items, not separately
```

`--result findings` is deliberately **not** `fail`. A restore drill that runs clean and
discovers the data does not restore has not failed; it has succeeded at telling you
something bad. A store that renders those identically records the wrong fact about the
most valuable thing it did.

**Everything the work-item store guarantees, this does too**: `poga ops` anchors on your
main checkout and commits its own write, printing the same receipt.

**Reaching past the front door from a lane is now refused, not merely discouraged.**
`python3 session.py ops-new` run inside a lane used to write that lane's *frozen* copy of
`ops-items/` — a tracked directory no other checkout ever reads. That is how OPS-0006, a
monthly audit already 43 days overdue, existed and was invisible to every surface but the
one lane's own banner; and how OPS-0004's number was burned outright. The store-writing
verbs (`wi-new`, `wi-edit`, `wi-status`, `wi-move`, `wi-stale`, `ops-new`, `ops-edit`,
`ops-status`, `ops-ran`) now exit non-zero from a linked worktree and print the front-door
command to run instead — which works from right where you are standing. `renumber` and
`wi-commit` are deliberately exempt: their subject *is* the lane's own copy.

## Running a program on the other machine

The machine map in `session.config.json` names each machine by its role label (for
example `Laptop` and `Runner`). Programs that must stay up usually **run on the
always-on machine**. **Check the start banner's `machine:` line before composing any
`ssh` command.**

**The link may run one way only.** An `ssh` alias on one machine can reach the other
while nothing reaches back; an Architect with no path to the machine it needs should
re-scope the work rather than debug the connection. Host reachability and remote-access
posture are owned outside this repo — do not probe, diagnose, or record them here.

**Same bytes, different paths.** When one machine reaches another's storage through a
network mount, there is never a copy step — but the mount's path (for example under
`/Volumes/…`) is not the owning machine's own path (under `/Users/…`), and the owning
machine has no such mount at all. **Translate the prefix** in any remote command. Get it
wrong and the failure is misattributed: `No such file or directory` for a file you can
see in your own tree reads as a broken script, so the next move is to debug the program
rather than the path.

```
ssh <host-alias> 'cd <remote-abs-path> && <cmd>'                        # run to completion
ssh <host-alias> 'tmux new-session -d -s <name> "cd <path> && <cmd>"'   # long-running: detached
ssh <host-alias> 'tmux capture-pane -pt <name>'                         # read its output
ssh <host-alias> 'tmux kill-session -t <name>'                          # stop it
```

**Anything that must outlive the SSH connection goes in a named `tmux` session** —
unlike `nohup … &` it can be reconnected to, read, restarted, and killed cleanly.
`launchd` is the escalation only when a program must survive a reboot; build that
per-program, never as the default. One-time per machine: a non-interactive `ssh` skips
`.zprofile`/`.zshrc`, so tools a package manager installed may be off PATH and bare
`tmux` won't resolve — put the PATH setup in the target's `~/.zshenv`.

Issuing these is routine operational work
(`routine-ops-autonomy` (withheld)): say what you are
starting and how to reach it (the tmux session name), then do it — do not ask.

## Session journal format

Author the **body** of `sessions/journal/<your-id>.md` (the frontmatter is written for
you at your first activity). The compactor supplies the `## YYYY-MM-DD — Session N — <title>` heading
and the Start/End stamp lines from the frontmatter, so the body is just the sections:

```markdown
### What happened
- ...

### State at close
- This item only: outcome and verification.

### Parked question
None, or the unanswered question.

### Notes filed
- Findings for review; no new work minted.
```

Edit only your own journal — never the generated `session-handoff.md`. Two concurrent
sessions writing their own journals never conflict; the handoff is regenerated from both.

## Why a relay by `send-keys` is indistinguishable from the user

**The measurement** (session ~202, across every lane transcript on the machine): 46
peer-to-peer relays had been sent by `send-keys`. Every one of them was recorded by the
receiving session as `origin: {"kind":"human"}, promptSource: "typed"` — **identical to
the user's own typing**, and identical to the nine disclaimers the user hand-typed
(*"the operator HERE, this is not a relay"*) trying to work around the ambiguity. `send-keys`
writes keystrokes to a pty, so the runtime never sees an injection; it sees a person
typing, because at the only layer it can observe, that is what happened.

**So a label in the text cannot fix it.** Anything able to send keys can also write
*"Relay from X, NOT the user"* — or omit it. Every one of those 46 was honestly labelled,
and the labelling is exactly what could not be checked. **39 of the 46 told the receiving
session to run something.**

`SendMessage` has the property `send-keys` cannot: the runtime wraps the message in a
sender-identified envelope **applied on the receiving side**, so the sender can neither
forge nor suppress it. The receiver is told who sent it and that it was not typed by its
user.
