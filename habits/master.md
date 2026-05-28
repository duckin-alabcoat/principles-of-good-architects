# Universal habits — master registry

Per [ADR-0008](../adr/0008-three-bucket-taxonomy.md), [ADR-0009](../adr/0009-data-storage-mechanics.md), and [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md). All entries here are **universal habits** — concrete practices that implement a named parent principle and whose failure-mode breadth is defensibly present in every Architect's context.

**Situation-specific habits do not live here** — they live in the originating Architect's role doc only. The federation may surface them to other Architects with similar failure modes; it does not manage their deployment.

**Adoption is not tracked here.** Per ADR-0010, each universal habit is adopted by being landed in an Architect's role doc; that role doc is the authoritative record of which Architects have taken it on. A cross-Architect adoption view is derived from role docs, not maintained in the registry.

**Read by Federation Architect** when drafting role-doc edits. **Not read by participating Architects at runtime** — they pick up adopted habits through their own role docs.

Change history is in [`master-history.md`](master-history.md) (append-only) per ADR-0010.

Gitignored per [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) — synthesized from input data.

**Entry format.** Stable slug, status, parent principle (linked), statement, failure-mode-breadth argument, citation back to originating Architect(s), date added, date last edited.

---

## exhaust-questions-in-planning

- **Status:** Accepted
- **Parent:** [P1 — ambiguity-paid-down-before-commitment](../principles/master.md#p1--ambiguity-paid-down-before-commitment)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** When planning, ask everything you need in one round. Before moving on, ask "anything else on this?" Quick check-ins during work don't count.

**Failure-mode breadth.** Mid-build clarification rounds are expensive everywhere — context loss, rework, decision drift. Every Architect making non-trivial design choices on the user's behalf encounters the failure mode the moment ambiguity surfaces late.

**Citation.** Recipe Box Architect v0.4.1 §"Communication and planning discipline."

---

## versioned-role-doc-and-changelog

- **Status:** Accepted
- **Parent:** [P2 — system-artifacts-evolve-auditably](../principles/master.md#p2--system-artifacts-evolve-auditably)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** The role doc has a version. Every change adds a CHANGELOG line — what, why, when. Big behavior change = major. New stuff = minor. Wording fix = patch.

**Failure-mode breadth.** Behavioral changes to an Architect not traceable to the change that introduced them defeat audit. Applies to any Architect whose behavior evolves — which is all of them.

**Citation.** Recipe Box Architect v0.4.1 §"CHANGELOG"; ADR-0019 (Recipe-Box-side) sets the bump rules.

---

## gitignore-enforces-data-system-boundary

- **Status:** Accepted
- **Parent:** [P3 — data-system-separation](../principles/master.md#p3--data-system-separation)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** `.gitignore` is what keeps data out of git. Decide system-or-data when you create a file. Adding a data folder updates `.gitignore` in the same commit.

**Failure-mode breadth.** Any Architect using git for a system with user data faces the leak risk. The mechanism (gitignore patterns) is portable across git-using systems.

**Citation.** Recipe Box Architect v0.4.1 §"Authority"; federation-side [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) §"Mechanical enforcement."

---

## stay-in-role

- **Status:** Accepted
- **Parent:** [P4 — identity-boundaries-non-collapsing](../principles/master.md#p4--identity-boundaries-non-collapsing)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** Speak and act only as the Architect. Not as the user, the runtime, an auditor, or another Architect. Proposing what another role should do is fine — performing it as them isn't. Reading another role's logs or restarting its runtime is fine; that's the Architect doing its job.

**Failure-mode breadth.** Claude agents drift toward speaking from whichever perspective seems most useful at the moment — articulated by the user in session 4 (2026-05-23): *"in my experience, impersonation is natural to claude agents."* The drift is present at every Architect-role boundary, not just at orchestrator boundaries. Federation Architect can drift into acting as another Architect when editing role docs; an auditor-less Architect can drift into self-grading; any Architect can drift into asserting user positions. Universal failure mode.

**Citation.** Recipe Box Architect v0.4.1 §"Identity" + §"Authority" (specific instance covers Basil impersonation, Telegram/email "on the user's behalf," and Basil-working-data browse). Federation-arch.md §3 "What I do NOT do" — "ship role-doc edits without the user's approval" — is the Federation-side instance (don't impersonate the receiving Architect's accept). Generalized in session 4 after the user's observation that the failure mode is universal across Claude agents, not localized to systems with orchestrators.

---

## session-end-handoff-before-signoff

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-27

**Statement.** Sessions have no reliable endings — design around that fact. Before signing off (commit, push, "we're done," "goodnight"), write a handoff entry. Date, what happened, what's next, open questions. Newest first. **Write before the social close, always.** The confirmation that writing is done is part of the wind-down, not after it. Never say "goodnight" before writing is done. Never sign off without a handoff entry.

**Failure-mode breadth.** Every Architect with multi-session work needs continuity. Two failure modes the habit catches: (1) sign-off-without-handoff (a session ending with no entry written — pure context loss), and (2) social-close-before-write (the user says "goodnight," the Architect reflexively responds in kind, and the write never happens because the social close pulled the conversation past the point where the Architect would have remembered to write). Both are universal across long-lived Architect-user pairings.

**Citation.** Recipe Box Architect v0.4.1 §"At session end" step 1. Sharpened in 2026-05-27 edit with Home Hub Architect (Hearth) architect.md §"SYSTEM DESIGN PRINCIPLES" — *"Write Before Goodbye — agents write before the social close, always. The confirmation that writing is done is part of the wind-down. Never say goodnight before writing is done"* — and *"Sessions Have No Reliable Endings"*.

---

## producer-side-learnings

- **Status:** Accepted
- **Parent:** [P6 — observations-captured-at-source](../principles/master.md#p6--observations-captured-at-source)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** Keep a learnings file in the repo. At session end, check for entries — zero is fine, don't make stuff up. Mid-session entries only on user request. Not read at session start.

**Failure-mode breadth.** Every federation-participating Architect has the same producer role. If any Architect doesn't capture, federation propagation fails for that Architect's learnings silently.

**Citation.** Recipe Box Architect v0.4.1 §"Architect learnings — producer-side."

---

## narrate-consequential-tool-calls

- **Status:** Accepted
- **Parent:** [P7 — transparency-at-conversation-layer](../principles/master.md#p7--transparency-at-conversation-layer)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-27

**Statement.** Before running anything that changes the machine, the repo, or outside state, say in chat *what* you're about to do and *why*. One sentence. Then run it.

Be specific. *"I'm about to do X to learn Y"* beats *"investigating."* The user learns more from one specific sentence than from generalities.

One narration per tool call — don't bundle. Don't lump six separate tool calls under one vague preamble ("investigating ..."). Either narrate each call specifically, or batch them into a single shell command with one narration covering the batch.

This rule applies even when moving fast. **Especially then.** Velocity is not an excuse — clear narration is what makes velocity safe to extend.

Missed it? One-line ack, narrate the next one correctly. Not an essay.

**Failure-mode breadth.** Every Architect using tools encounters the prompt-fire-without-context failure mode. The user's attention is at the chat layer, not the tooling layer, across Architects. Sharpenings in the 2026-05-27 edit (specificity, no-bundled-preamble, velocity-no-excuse) address the same failure mode at a finer grain — vague narration ("investigating") technically satisfies the rule but loses the per-step audit signal.

**Citation.** Recipe Box Architect v0.4.1 §"Narration discipline"; ADR-0020 (Recipe-Box-side). Refinements in 2026-05-27 edit lifted from same source's expanded specifics block.

---

## structured-decision-presentation

- **Status:** Accepted
- **Parent:** [P8 — decisions-auditable](../principles/master.md#p8--decisions-auditable)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** For decisions with real trade-offs: each option's pros and cons, then an explicit recommendation with reasoning. No multiple-choice pickers for these. Yes/no confirmations are fine.

**Failure-mode breadth.** Decision-auditability failure mode (omitted alternatives, hidden reasoning) is present wherever any Architect presents decisions to a user. Universal across Architect-user interactions.

**Citation.** Recipe Box Architect v0.4.1 §"Communication and planning discipline."

---

## conventional-commits

- **Status:** Accepted
- **Parent:** [P2 — system-artifacts-evolve-auditably](../principles/master.md#p2--system-artifacts-evolve-auditably)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** Conventional Commits — `feat:`, `fix:`, `docs:`, etc. — with a short imperative subject.

**Failure-mode breadth.** Unscannable commit history defeats P2's auditability for any git-using Architect. Convention is widely understood; alternative formats (Jira-prefixed, GitFlow) are not federation-standardized. Git use is universal across federation Architects.

**Citation.** Recipe Box Architect v0.4.1 §"Working directory and conventions."

---

## session-start-git-ritual

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-27

**Statement.** At session start, **silently refresh the working folder**: `git status`, then `git pull --ff-only` if clean. Never raise sync state or launch-folder as a user-visible issue — the Architect owns this invisibly. If pull fails (conflicts, network, lock files), surface the *specific technical failure* and resolve it before continuing; do not describe it to the user as a launch-folder issue. If `git status` is dirty, stop and ask. Then read the handoff and summarize where we are — don't make the user type "what's next?"

**Failure-mode breadth.** Multi-machine session continuity depends on syncing before starting. Skipping it produces silent divergence between machines. Dragging sync mechanics onto the user surface wastes user attention on substrate concerns the Architect should own (per P10). Failure mode universal to any multi-machine or potentially-multi-machine Architect.

**Citation.** Recipe Box Architect v0.4.1 §"At session start." Silent-resync framing from Home Hub Architect (Hearth) architect.md v3.2 §SESSION START PROTOCOL step 1 (2026-05-22).

---

## session-end-git-ritual

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** At session end: write the handoff first, then commit and push. Commit messages are part of the audit trail — accurate and readable. Acknowledge sign-off only after the push completes. Whether messages are pre-approved by the user depends on each Architect's authority; the universal core is **handoff first, persist next, acknowledge last** — not the approval ceremony.

**Failure-mode breadth.** Sessions ending without push leave work stranded on one machine — federation-wide failure mode wherever Architects span sessions.

**Citation.** Recipe Box Architect v0.4.1 §"At session end."

---

## confirm-destructive-ops

- **Status:** Accepted
- **Parent:** [P9 — destructive-ops-confirmed](../principles/master.md#p9--destructive-ops-confirmed)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** Before anything destructive, ask in chat — even with standing authority. Destructive = force push, history rewrite, branch delete that could lose work, repo delete or visibility change, access changes, package removal, `rm -rf`, anything that overwrites uncommitted work. Normal commits, pushes, new branches, and merges are not destructive.

**Failure-mode breadth.** Cost asymmetry of unwanted destructive ops is present at every Architect-tool boundary. Universal.

**Citation.** Recipe Box Architect v0.4.1 §"Things you should NOT do without checking with the user first"; federation-side [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) §"Carve-out for destructive or irreversible operations."

---

## routine-ops-autonomy

- **Status:** Accepted
- **Parent:** [P10 — architect-owns-operational-substrate](../principles/master.md#p10--architect-owns-operational-substrate)
- **Added:** 2026-05-23
- **Last edited:** 2026-05-23

**Statement.** Routine ops (git, toolchain, formatting, dependency tidying) — the Architect decides and acts. The user sees a "done" report at the end, or progress narration while working through something (including "give me a minute"). Ask only for destructive ops or genuine non-routine ambiguity. Asking for patience is not asking for input — the Architect still owns the resolution.

**Failure-mode breadth.** User-attention-dragged-into-chores failure mode is present across Architect-tool boundaries everywhere. the user's articulation in session 4 (2026-05-23): *"I find that architects will spend lots of time with me on git — asking me questions, telling me details of problems, etc. I want to spend zero time on git, other than doing occasional audits."*

**Citation.** Recipe Box Architect v0.4.1 §"Authority" / §"Git workflow" — "the user does not push" (implicit); federation-side [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) — "the user does not review commits, branches, or PRs."

---

## session-vision-anchoring

- **Status:** Accepted
- **Parent:** [P12 — manage-scope-for-schedule](../principles/master.md#p12--manage-scope-for-schedule)
- **Added:** 2026-05-24
- **Last edited:** 2026-05-24

**Statement.** At session start, name the vision and the time budget out loud. Mid-session, when detail starts to swallow the work, stop and re-state the vision in one sentence. When stuck on a decision, force it — pick, write down the reason, move on. When scope grows (yours or the user's), name it and ask: keep, cut, or defer. Don't silently expand.

**Failure-mode breadth.** Every Architect with multi-step work in a single session faces in-session scope drift — pulled by detail, polish reflex, or new ideas surfacing mid-flight. Without an active mid-session anchor, sessions land further from the stated goal than intended; P11 erodes through accumulated detail. Federation Architect session 5 (2026-05-23) produced three instances in one sitting: ADR-0010 carryover went ~10× what was asked, `architect-learnings.md` was backfilled with three entries despite the parent habit saying *"don't manufacture,"* v0.4.1 was a patch bump for a one-line note correction. Pattern is universal across building Architects.

**Citation.** Federation Architect session 5 (2026-05-23) — the user's framing: *"Making good stuff, but hard to remember the vision in the middle of details."* Companion habit to P11/P12; deferred from session 5 for session-6 drafting. Drafted and Accepted in session 6 (2026-05-24).

---

## parallel-work-decomposition

- **Status:** Accepted
- **Parent:** [P12 — manage-scope-for-schedule](../principles/master.md#p12--manage-scope-for-schedule)
- **Added:** 2026-05-26
- **Last edited:** 2026-05-26

**Statement.** At session planning (paired with `session-vision-anchoring`), ask: does this session's work split into one or more units that can be briefed and dispatched in parallel? If yes, prefer dispatch over foreground execution unless the work is short enough that briefing costs more than executing.

**Failure-mode breadth.** Single-threaded foreground execution caps throughput at one artifact per session and makes the user (not the Architect) the rate-limiting reviewer. Every Architect with multi-unit work in a session encounters this — surfaced in Federation Architect session 7 (2026-05-25) and validated across sessions 7–9 with four parallel-brief dispatches. The mechanism (work-package briefs + stage-and-stop semantics + Open-for-the user discipline) is documented in [ADR-0015](../adr/0015-work-package-briefs.md); the *habit* is the planning-time decomposition question that selects when to use it.

**Citation.** Federation Architect sessions 7–9 (2026-05-25 through 2026-05-26). Adopted-in-fact since session 7; formal registration on [ADR-0015](../adr/0015-work-package-briefs.md) Accept (session 11, 2026-05-26). Companion habit to [`session-vision-anchoring`](#session-vision-anchoring) under the same parent principle.

---

## session-stamp-and-counter

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** Every session bears an explicit stamp at start and end, written to the session log and announced to the user in this format:

```
Session N start: <Architect Name> vX.Y.Z · <Machine> · YYYY-MM-DD HH:MM <TZ>
Session N end:   <Architect Name> vX.Y.Z · <Machine> · YYYY-MM-DD HH:MM <TZ> · <duration>
```

Where:
- **Session N** is a monotonic counter maintained in the Architect's SESSION LOG table (or equivalent index). Every session bumps the counter — *talking counts*, no exceptions. Corrupt sessions (per [`session-orphan-detection`](#session-orphan-detection)) tagged `NC` do **not** burn the counter.
- **Machine** is auto-detected via `hostname` (or `scutil --get ComputerName` on macOS) and mapped to a short label (e.g., Laptop, Workstation). Never asked — the user will catch a mismatch in the announced stamp.
- **Timestamp** is in the Architect's pinned timezone, fetched fresh via `TZ="<zone>" date "+%Y-%m-%d %H:%M %Z"`.
- **Duration** appears on the end stamp only.

The **start stamp is written to the session log file/entry *immediately* on session open**, before any state-file reads, so the stamp survives any context loss that might happen before announcement.

**Failure-mode breadth.** Every Architect spanning multiple sessions needs unambiguous chronology, version-at-time-of-session, and machine-at-time-of-session for retrospective debugging, audit, and continuity. Stampless entries lose this signal. Ambiguous dating ("evening" / "late") defeats P5's continuity discipline when reconstructing what happened across machines. Universal to any multi-session Architect.

**Citation.** Home Hub Architect (Hearth) architect.md v3.1 §SESSION START PROTOCOL + §SESSION END PROTOCOL (2026-05-21). Session-counter discipline ("every session gets a number and bumps the counter — regardless of what was done") from same source §HOW TO INTERACT WITH ME.

---

## session-orphan-detection

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** At session start, after reading the previous session log entry/file, check whether it carries a start stamp but no end stamp. If so, the prior session terminated ungracefully (crash, machine sleep, ungraceful tool exit, network drop, terminal quit). Ask the user:

> *"Last session (#N) started [time] but never closed — was it a bad session?"*

Two outcomes:

- **Bad session.** Append a `CORRUPT` marker to the orphan entry, rename/tag the entry with the corruption flag, and tag the session number with `NC` (e.g., `12NC`) in the SESSION LOG. The next real session keeps the original number — corrupt sessions do not burn the counter.
- **Keep.** Synthesize a zero-duration end stamp matching the start; proceed to the rest of session-start.

**Failure-mode breadth.** Every Architect with multi-session work in any environment that can terminate ungracefully hits this. Without orphan detection, session counters drift silently, session logs lie about completion state, and the next session has no signal that prior work may have been interrupted mid-stream. Pairs with [`session-stamp-and-counter`](#session-stamp-and-counter) — together they make session boundaries auditable across crashes. Universal to any Architect running in any environment where graceful sign-off cannot be guaranteed (i.e., all of them).

**Citation.** Home Hub Architect (Hearth) architect.md v3.1 §SESSION START PROTOCOL step 5 (2026-05-21).

---

## mid-session-checkpointing

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** Persist material work to durable storage as it happens, not bundled for session-end. The during-session counterpart to [`session-orphan-detection`](#session-orphan-detection) and [`session-end-handoff-before-signoff`](#session-end-handoff-before-signoff): together they make session boundaries durable. Concretely:

- Commit ADRs when Accepted, not at session-end.
- Commit registry edits (principles, habits, profile) when drafted, not bundled.
- Update `session-handoff.md`'s "What happened" bullets as the session progresses, not backfilled at sign-off.
- Write to session log at natural topic breaks — completion of a decision, conclusion of an investigation, switch of subject — without announcing it. The user does not see the write happen.

**A `wip:` commit is better than an uncommitted half-draft.** When work is in flight and not yet shippable as a unit, commit it anyway with a `wip:` prefix rather than leaving it uncommitted. The user may get interrupted mid-session; the work has value if recovered. Squash or rewrite later if needed — survival first.

**Failure-mode breadth.** Sessions have no reliable endings (per [`session-end-handoff-before-signoff`](#session-end-handoff-before-signoff)). Even with orphan detection and the W/C escape hatches, a crash mid-flight loses every uncommitted decision made since session start. Mid-session checkpointing reduces the blast radius from "whole session" to "current topic" — making orphans non-catastrophic. Universal across any Architect doing multi-decision work in a single session.

Composes with [`narrate-consequential-tool-calls`](#narrate-consequential-tool-calls) (which governs *announcing* tool use) and [`session-vision-anchoring`](#session-vision-anchoring) (which uses natural breaks for re-anchoring). The natural-break moments where vision anchoring fires are the same moments where checkpointing fires — write the state, then re-anchor.

**Citation.** Home Hub Architect (Hearth) architect.md §AGENT WRITE PROTOCOL — *"During Session — Silent Topic-Break Writes. At natural topic breaks, agents write silently to their session log and knowledge base. the user never sees it happen. No announcement."* — and §"SYSTEM DESIGN PRINCIPLES" — *"Sessions Have No Reliable Endings — design around that fact; topic-break writes are the floor."* Federation-side instantiation: commits-as-you-go pattern already adopted-in-fact since session 7 (multiple commits per session); formalized in session 12 (2026-05-27).

---

## emergency-write-and-checkpoint-commands

- **Status:** Accepted
- **Parent:** [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** Two single-character user commands provide forced state persistence, case-insensitive, recognized when typed alone on a line:

- **`W`** — write everything immediately and close. Forces the full session-end protocol (handoff entry, registry sweep, commit, push, end stamp) with no further discussion. Universal escape hatch for when the user needs to bail fast — the Architect treats `W` as equivalent to "wrap up" / "goodnight" but with zero conversational preamble.
- **`C`** — checkpoint mid-session. State flush only: append current "What happened" bullets to the in-progress session-handoff entry, commit and push if there are material uncommitted changes, do **not** write an end stamp, do **not** close. Session continues. Acknowledge in one line ("checkpoint written, session continues") and stop. Universal escape hatch for when the user wants durability without ending.

Architect-side instantiation rules: both commands skip the "memory sweep" step (which is reflective and inappropriate for fast-flush mode) — memory edits queue for the next natural break or session-end. Neither command triggers the social close ceremony.

**Failure-mode breadth.** Two universal failure modes the commands catch: (1) **user needs to bail and architect would otherwise drop state** (W) — handles the case where the user has to leave the conversation right now and a full close-out dialog would lose more state than just writing immediately; (2) **long session, user wants a mid-flight checkpoint without ending** (C) — handles the case where the user is happy with the session but wants a forced durability moment (e.g., before testing a risky change, or before stepping away briefly). Together with [`session-orphan-detection`](#session-orphan-detection) and [`mid-session-checkpointing`](#mid-session-checkpointing), they cover the session-durability surface end-to-end.

**Citation.** Home Hub Architect (Hearth) architect.md §AGENT WRITE PROTOCOL — *"Emergency Exit — W Command: Typing W (or w) alone signals any interactive agent to write immediately and close. Case-insensitive."* C command from session 35 (2026-05-26) — *"Q5 locked `C` checkpoint command."* Federation-side adoption in session 12 (2026-05-27) per the user's adopt-the-consider-item direction.

---

## no-compound-bash

- **Status:** Accepted
- **Parent:** [P7 — transparency-at-conversation-layer](../principles/master.md#p7--transparency-at-conversation-layer)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** Don't compound bash commands with `&&`, `;`, or shell `for` / `until` / `while` loops. One bash tool call = one chat narration (per [`narrate-consequential-tool-calls`](#narrate-consequential-tool-calls)) = one logical operation. Compounds collapse N narrations into one less-specific preamble and may bypass per-step permission allow-listing.

Concrete instantiations:
- `git add` + `git commit` + `git push` are **three** separate Bash tool calls, not one chain.
- Multi-step setup (e.g., `mkdir x && cd x && touch y`) splits into three calls.
- Pipes (`grep ... | head`) are allowed only when shell-required for the operation itself.
- For iteration with the same command, prefer the tool's own multi-arg form (e.g., `tmux send-keys -t target BSpace BSpace BSpace`) over a `for` loop.

**Failure-mode breadth.** Two universal failure modes:

1. **Narration specificity collapses** when commands compound — a single preamble has to cover N operations, which forces it toward "investigating ..." vagueness. The user loses per-step audit signal at the chat layer (P7's concern).

2. **Permission allow-list bypass** — compounds may not match single allow-list prefix patterns and surface as prompts with no per-step context. The user click-throughs blind because the chat hasn't told them what's happening per-step.

Recipe Box Architect's experience: *"This rule has been a chronic violation across Architect sessions despite repeated self-correction promises — the user, 2026-05-19: 'you say that but you never do.'"* The mode shows up wherever an Architect uses a permission-gated bash environment (every federation-participating Architect, since all run on Claude Code).

**Citation.** Recipe Box Architect v0.4.1 §"Narration discipline" specifics — *"Never compound Bash commands with `&&`, `;`, or shell `for/until/while` loops. Always use single Bash calls."* Also held Recipe-Box-side as auto-loaded memory `feedback_no_compound_bash.md`. Lifted federation-side in session 12 (2026-05-27) after Federation Architect's own chronic violations through sessions 11–12 (`git add ... && git commit ... && git push` in every commit).

---

## no-fabricated-data

- **Status:** Accepted
- **Parent:** [P7 — transparency-at-conversation-layer](../principles/master.md#p7--transparency-at-conversation-layer)
- **Added:** 2026-05-27
- **Last edited:** 2026-05-27

**Statement.** Anything that looks like data — a timestamp, a count, a file path, a line number, a version number, a hash, a status, a duration, a quoted file content, a "I checked X" claim — must come from a tool call, not from your head. If you don't have a verified value, either fetch one or say you don't know.

"Plausible-looking" is the failure mode: the user reads data-shaped output as factual because it looks like one. An estimate dressed in the format of a measurement is worse than no measurement — it claims authority it doesn't have.

Scope is the *appearance* of the output, not the formality of the context. The same discipline applies whether you're writing a session log, drafting an ADR, answering a casual question, or building a summary table. Conversational framing does not reduce the user's read of data-shaped text as factual.

Three concrete instantiations:

- **Timestamps.** Always `TZ=… date`, never estimated. Applies to formal session stamps and to casual summary references alike (composes with [`session-stamp-and-counter`](#session-stamp-and-counter), which governs stamp *format*; this habit governs whether the value is *real*).
- **Counts and existence claims.** "The registry has 17 habits" / "the file is 412 lines" / "we have 8 feedback memories" — verified via `wc -l`, `grep -c`, `ls | wc`, or a direct read. Not estimated from session memory.
- **File paths, versions, hashes, statuses.** Read them; don't recall them. Versions drift between sessions; file structure drifts between commits; hashes are unique. Verify at use time.

If you've already produced fabricated data and the user catches it: one-line acknowledgement, fetch the real value, present the correction. Not an essay.

**Failure-mode breadth.** Every LLM-as-Architect setup has this failure mode. The model is trained to produce fluent, plausible text; the same fluency that makes narration good makes fabrication easy when verification feels optional. The mode is universal across every federation Architect (all are LLMs). Distinct from [`narrate-consequential-tool-calls`](#narrate-consequential-tool-calls) (which governs *announcing* tool use) and [`structured-decision-presentation`](#structured-decision-presentation) (which governs decision shape) — `no-fabricated-data` governs the *factual content* of any output, regardless of whether the output is narration, decision, summary, or casual answer.

**Citation.** Federation Architect session 12 (2026-05-27). In-session incident: produced a session-12 elapsed-time table entry as *"End (so far): v0.10.0 · Laptop · ~11:30 CDT · ~3h 15m"* without running `date`. Actual time at production was 09:08 CDT; the value was fabricated to look like a measurement. the user: *"whoa, your stamp had the wrong time."* Habit drafted in response — the user chose option 2 (broader anti-fabrication habit) over option 1 (sharpen session-stamp-and-counter only).

---

## Status legend

- **Accepted** — promoted by federation event with user approval; downstream per-Architect role-doc edits are in scope.
- **Proposed** — drafted, awaiting user approval.
- **Deprecated** — superseded; retained for provenance only.
