# 144. Canon reaches a hookless runtime by delivery, not by a pointer

- **Status:** Accepted
- **Date:** 2026-09-18
- **Amends:** ADR-0082 (withheld) D5
- **Work item:** WI-0324 (R4 of the 2026-09-07 consultant brief)

## Context

ADR-0082 (withheld) D5 settled how canon
reaches a runtime with no `SessionStart` hook: the wrapper's prep phase writes the start
payload to `.session-state/session-context.md` and the launcher names that path in the
opening prompt. The doctrine was stated as *canon delivery degrades **inject → point**,
never → nothing*, and the reasoning was sound — an Architect that never reads its
principles is not bound by them, so a file the agent is told to read beats no delivery.

What D5 did not account for is the **cost of the pointer itself**, because at the time
there was no runtime to measure it against. There is now, and the pointer is expensive in
a way that reading the design cannot show.

### The measurement

An audit of Codex session transcripts found:

- **Nearly every** transcript that made any tool call hit a **truncated read inside
  its first ten model-visible tool calls**.
- Many re-read `.session-state/session-context.md` several times in that
  window, recovering from a first read the runtime had cut.
- Under the audit's final rules, **no Codex session that did real work had a full-window
  PASS**: the only PASSes were sessions which made one or two tool calls in total.

The mechanism is specific and it is invisible from inside: Codex's exec tool truncates
output at a token cap and **no marker of the truncation reaches the model**. A pointer
therefore does not buy one read — it buys one truncated read plus the guessed, overlapping
re-reads the agent makes trying to recover, before any work begins.

### What the runtime actually offers, measured rather than assumed

R4 said in terms not to assume an `AGENTS.md` equivalent behaves like the Claude hook.
`codex debug prompt-input` renders the model-visible prompt offline, so this is directly
measurable. On the Codex CLI release current at the time:

| Question | Measured |
|---|---|
| Does Codex auto-load `AGENTS.md`? | Yes, from the cwd, as a `user`-role message, no git repo required |
| Size limit | **Exactly 32,768 bytes**, controlled by `project_doc_max_bytes` (verified by overriding it to 1024 and measuring 1024) |
| Is the truncation visible to the model? | **No** — the text ends mid-document |
| Does `AGENTS.override.md` stack with `AGENTS.md`? | **No — it replaces it** |
| Does a large positional prompt survive? | **Yes** — 85,056 bytes arrived byte-complete |

The session payload is 41–81 KB, so `AGENTS.md` would truncate it silently — reproducing
the exact failure this ADR exists to remove. `AGENTS.md` is also a committed, public,
human-authored front door (WI-0383), so writing per-session lane state into it would break
[P13](../principles/master.md#p13--single-writer-per-state) and
[P3](../principles/master.md#p3--data-system-separation). `AGENTS.override.md` would
suppress that front door outright.

## Decision

**D1. Canon delivery degrades `inject → embed → point`, never → nothing.** Where a
runtime has a hook surface the payload rides in as `additionalContext`. Where it does not,
the launcher **embeds the payload text in the opening prompt**. Only where that will not
fit does it fall back to naming the path.

**D2. The file remains, and remains the single source.** `.session-state/session-context.md`
is still written by prep and is still the same generated text the hook path injects. The
prompt carries a copy of those bytes; it does not become a second composition. `poga
resume` and the operator-supplied-prompt case still point at it, and those two gaps are
unchanged by this ADR.

**D3. The embed cap is a fixed 256 KiB, declared in both the harness and the launcher.**
The prompt is an `exec` argv element, so the ceiling is ARG_MAX. A bisected real `exec`
launched at most **1,044,434 bytes** against a `getconf ARG_MAX` of 1,048,576 — the
configured constant is not the true limit, so arithmetic against it would be arithmetic
against the wrong number. 256 KiB is ~3× the largest payload observed and leaves >760 KB
of the measured ceiling unused.

**D4. Exceeding the cap degrades loudly.** The launcher prints the payload's size and the
cap it exceeded, and points instead. A cap that fails silently just re-breaks on the next
entry.

**D5. A byte-bounded reading rule ships in the payload, and the harness makes it cheap.**
The rule — never whole-file-read a role doc, registry or generated view; ask for a section
by heading or a byte-bounded chunk; do not overlap — lives in the payload rather than in
`STANDARD.md`, because it describes runtimes that read through a capped exec channel, not
every Architect. `session.py show` implements the cheap path: a heading index with byte
ranges, a single section, or a byte span, each with a footer naming the file's size, the
span shown, and the offset the next chunk starts at.

**D6. The acceptance is machine-checkable.** `curate/token_ledger.py --startup-audit`
evaluates R4's criterion over a session's first ten model-visible tool calls and reports
truncation, overlapping reads, and — separately — re-reads that follow a write to that
path. `PASS` requires positive evidence: a missing output record or an empty window reads
`UNVERIFIABLE`, and a window shorter than the limit is flagged as weak evidence.

### What a mutation sweep found that the tests did not

An AST-enumerated mutation sweep over the eight new functions caught 76 mutations and left
49 alive. Triaging the survivors turned up a **real defect, not merely a coverage gap**:
the file-change index was computed as `len(calls)` inside a pass that runs *before* the
call list is filled, so every write recorded position 0 and retroactively excused every
overlap earlier in the session. The fixture that should have caught it passed for the
wrong reason — it asserted the re-read was classified as deliberate, which the broken code
also did.

Fixed, with a test that fails against the old code. Across the same
transcripts: re-reads classified as "deliberate, after a write" nearly vanished, and
dozens of genuine overlaps that had been silently excused now surface. The session verdicts did
not move — those sessions were already FAIL on truncation — which is precisely why nothing
noticed.

## Consequences

- A dispatched Codex lane starts with canon already in context and no reason to read
  anything to obtain it. The ~57 KB that used to be spent on a truncated read and its
  recoveries is spent once, uncached, in the prompt.
- An interactive Codex pane now renders the payload where it used to render one line.
  Dispatched lanes are detached, so this is paid where nobody is reading.
- The pointer path is not deleted and must keep working: it is what `poga resume`, an
  operator-supplied prompt, and an oversized payload all fall back to. Its instruction
  now names the bounded reader rather than leaving the agent to `cat`.
- The federation gains a measured baseline it did not have. Every claim about startup
  reads is now a number out of `--startup-audit` rather than an impression.
- **Not closed by this ADR**, and stated so it is not mistaken for closed: `poga resume`
  delivers no context at all, and an operator-supplied prompt suppresses delivery
  entirely, because poga cannot tell which argv element is the prompt. Both are ADR-0082
  D3's reserved argv-shape column, still unbuilt.
- **Owed verification.** Everything above is measured offline — the payload is proven to
  arrive complete, which removes the *reason* to read the file. Whether a live Codex
  session then makes ten clean tool calls is a behavioural claim that needs a live run,
  and a live run spends paid runtime allowance. `--startup-audit` is the instrument;
  the reading is owed the first time a Codex lane is dispatched.
