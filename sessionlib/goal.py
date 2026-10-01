"""Session goals — what a goal is made of, and what became of each part of it.

WI-0401. A goal is a sentence a human types at `/goal`, and a Stop hook then asks,
every time the session tries to stop, whether that sentence is true yet. Two recorded
sessions could not answer, and so could not stop:

* **2026-09-18, session eba5 on poga-19.** Its goal's third clause was *"close WI-0324
  only if the reading passes"*. operator then instructed it not to run the reading at all,
  which left the clause with no readable truth value — not met, not unmet, simply
  unanswerable. The session had closed its journal and spent 34 further turns
  being refused. The workaround on the day was to replace the goal with one already
  true, which clears the block by destroying the record of why it was there.
* **2026-09-19, session ~382**, while building this fix. Six of its eight clauses were
  met. *"cut v7.4.0 through the pipeline"* had been taken back by operator mid-session (he
  would cut the release himself from a session on main after this one closed), and *"end
  the session"* was blocked on the consent `end` requires. The evaluator fired three times
  and returned the same verdict each time, because it has no representation for either
  state; its third reading counted the session's honest dry-run against it.

So this module answers two different questions, and both halves are needed — one gives
the evaluator something to read, the other stops the unreadable clause being written.

1. **`goal_clauses` + `goal_disposition_block`** — the goal is split into the clauses
   its author wrote, and each is rendered with what became of it. A clause that was
   taken away, or that is blocked on something the session cannot supply, says so AND
   QUOTES THE INSTRUCTION that did it. A verdict without its evidence is just a
   different unreadable answer, so the evidence is required by the recorder, not
   requested by convention.
2. **`goal_conditional_offences`** — a goal line carrying a conditional clause is
   refused where it is AUTHORED, because the cost otherwise lands on a session that has
   already finished its work and cannot leave. Refusing at authoring time costs the
   author one rewrite.

WHY THIS IS NOT THE STOP HOOK'S JOB. The evaluator reads prose and returns a verdict;
it is judgment, and it is the runtime's, not ours. What it lacked was an artifact
stating the facts it cannot derive — which of these clauses the session was told to
drop, and on whose words. That artifact is mechanism, and mechanism is ours
([P15](principles/master.md#p15--code-for-mechanism-not-judgment)). Nothing here
decides whether a clause was met: the session records that, and this module refuses to
let it record the two unreadable states without evidence.

Part of the harness namespace; loaded by `sessionlib.load()` after `config`, whose
imports it shares. Do not import this file directly.
"""

# Per-compilation-unit directive: each part is compiled on its own, so the monolith's
# single future import could not carry across the split.
from __future__ import annotations



#: Clause boundaries, in the author's own punctuation. A goal is one or more sentences
#: of ordinary English and its obligations are separated by `;`, by `,`, and by full
#: stops — so those are the boundaries, rather than a grammar this would have to be
#: right about. ANCHORED ON THE RECORDED CASE: the 2026-09-18 goal splits here into
#: exactly the eight clauses the incident counts ("six of eight clauses were met"), so
#: the splitter is checked against a real goal rather than a fixture written to agree
#: with it (see `tests/test_goal.py`).
#:
#: A full stop only ends a sentence when what follows looks like a new one — whitespace
#: then a capital or an opening bracket. `session.py test`, `v7.4.0` and `poga-19.` mid
#: sentence are the shapes this protects, and they are not hypothetical: every goal in
#: the recorded corpus names a file or a version.
#:
#: The separator is CAPTURED, because the two kinds are not interchangeable: a comma may
#: turn out to have been separating list items rather than clauses and is rejoinable, a
#: sentence end never is.
GOAL_CLAUSE_SPLIT_RE = re.compile(r"""
    (   \s*;\s*                      # an explicit clause break
      | \s*,\s*                      # a comma — the commonest break in the corpus
      | (?<=[.!?])\s+(?=[A-Z(\[])    # a sentence end, only where a sentence follows
    )
""", re.VERBOSE)


#: Below this many characters, a comma-separated fragment is a list item rather than a
#: clause. Set from the corpus, not from taste: the shortest real clause measured across
#: 34 recorded goal lines is "fix the sweep PATH" (18), and the enumeration items that
#: need rejoining are two words ("met", "not met").
#:
#: DELIBERATELY AT THE BOTTOM OF THAT GAP RATHER THAN THE MIDDLE. A larger threshold
#: swallows more enumerations, and it also swallows any genuinely terse clause an author
#: writes — "and stop." is nine characters and is an obligation. Merging a real clause is
#: the error that loses information; leaving a list item standing is the error that looks
#: untidy. Eight is the last value that cannot reach "and stop.".
GOAL_CLAUSE_MIN_CHARS = 8


def goal_clauses(text: str) -> list:
    """The clauses of `text`, in order, verbatim apart from surrounding whitespace.

    Verbatim is the point: the block quotes what the author wrote back to them, so a
    clause the splitter read differently than they meant is visible rather than
    paraphrased into agreement. Fragments carrying no letter at all — a stray `(1)`, a
    dangling bracket — are dropped; they are punctuation, not obligations."""
    if not text:
        return []
    out = []
    parts = GOAL_CLAUSE_SPLIT_RE.split(" ".join(str(text).split()))
    # `split` with one capturing group yields piece, sep, piece, sep, … — so the
    # separator that PRECEDES each piece is the one before it in the list.
    for index, piece in enumerate(parts):
        if index % 2:
            continue                                  # a separator, not a clause
        preceded_by_comma = index > 0 and "," in parts[index - 1]
        piece = (piece or "").strip().strip("—-").strip()
        if not piece or not any(ch.isalpha() for ch in piece):
            continue
        # A fragment this short is not an obligation — it is an item in a list the
        # author wrote inside one clause ("met, not met, moot or blocked"), and the
        # comma between them is not a clause break. Rejoin it to the clause it came
        # from, with the author's own comma. MEASURED, then chosen: this fires on the
        # enumerations in the recorded corpus and on none of its real clauses, the
        # shortest of which is "fix the sweep PATH". The two errors are not
        # symmetrical, which is what sets the direction — under-splitting shows two
        # obligations on one line, still verbatim and still disposable, while
        # over-splitting invents an obligation the author never wrote and then reports
        # it NOT RECORDED for ever.
        #
        # ONLY ACROSS A COMMA. A new sentence is a clause boundary however short the
        # sentence is — "Then close." is an obligation, and rejoining it into the
        # previous clause would hide one behind another. The two separators are not the
        # same claim: a comma is ambiguous between a clause break and a list, a full stop
        # is not ambiguous at all.
        if out and preceded_by_comma and len(piece) < GOAL_CLAUSE_MIN_CHARS:
            out[-1] = f"{out[-1]}, {piece}"
        else:
            out.append(piece)
    return out


#: The conditional connectives this refuses. DELIBERATELY THE THREE THE ITEM NAMES.
#: `when` was measured against the same corpus and left out: it reads as a schedule far
#: more often than as a condition ("when the five have landed, append the results"), and
#: a guard sitting directly in the dispatch path that fires on correct goals gets turned
#: off rather than fixed.
GOAL_CONDITIONAL_MARKS = ("only if", "unless", "if")

#: Matches a conditional connective as a WORD, not as a substring. `if` inside
#: "notify", "modified" or "diff" is not a conditional, and this is the whole
#: difference between a shape match and a grep.
_GOAL_COND_RE = re.compile(r"\b(only\s+if|unless|if)\b", re.IGNORECASE)

#: A clause that OPENS with its condition is not the defect. "If the gate refuses, leave
#: it on the branch and record why" states the antecedent first and the action second,
#: so the false branch is inside the sentence: the session knows what to do either way
#: and the clause is decidable. Leading conjunctions, list numbering and brackets are
#: skipped before deciding what the clause opens with, because "…; and if that evidence
#: is missing, record the gap" is fronted too.
#:
#: QUOTES AND EMPHASIS ARE SKIPPED WITH THEM, and that was measured rather than
#: anticipated: scanning 1,474 markdown files, the guard's single false positive was a
#: fronted conditional inside a quotation — *"if X works, do Y…"* — where the opening
#: `*"` was all that stood between it and the exemption.
_GOAL_FRONTED_RE = re.compile(r"""
    ^[\s(\[*_\"'“‘]*      # space, brackets, emphasis, and opening quotes
    (?:\(?\d+[.)]\s*)?              # list numbering: (3) or 3.
    (?:(?:and|then|but|so|also|next|finally)\s+)*
    (?:only\s+)?(?:if|unless)\b
""", re.IGNORECASE | re.VERBOSE)

#: What makes a postposed conditional safe: the author said what happens when it is
#: false. "fold WI-0376 into this lane if dispatch lives in lanes.py, OTHERWISE give it
#: its own lane" is total — both branches terminate — and refusing it would be refusing
#: a correct goal. The marker may sit in the clause itself or open the next one, because
#: a comma is a clause boundary here and `otherwise` lands just past it.
_GOAL_ALTERNATIVE_RE = re.compile(
    r"\b(otherwise|else|instead|rather\s+than|failing\s+that|or\s+else|if\s+not)\b",
    re.IGNORECASE)

#: Verbs that take a `whether`-complement. In "record whatever the sweep says and check
#: if the gate is red", `if` means *whether* — it names something to find out, not a
#: condition on acting. Substituting "whether" is the test a person applies; this is the
#: mechanical stand-in for it, and it is why the guard matches clause shape rather than
#: the substring "if". `only if` is never whether-sense, so the exemption cannot reach
#: the exact wording the 2026-09-18 goal used.
_GOAL_WHETHER_RE = re.compile(
    r"\b(check|checks|see|verify|verifies|confirm|confirms|determine|determines|test|"
    r"tests|ask|asks|establish|establishes|find\s+out|know|knows|report|reports|say|"
    r"says|note|notes|decide|decides|tell|tells|judge|judges|assess|assesses|evaluate|"
    r"evaluates|record|records|watch|watches)\b(?:\s+\S+){0,3}\s+if\b",
    re.IGNORECASE)


def goal_conditional_offences(text: str) -> list:
    """Every clause of `text` whose truth is gated on a condition it cannot settle.

    Returns a list of `(index, clause, marker)`, one per offending clause, indices
    1-based so they line up with what `goal_disposition_block` prints. Empty means the
    goal is fine — which is the answer for every correct goal in the recorded corpus.

    THE SHAPE, precisely: a conditional connective appearing AFTER the clause's action,
    with nothing said about the false branch. That is the clause that cannot be
    disposed of — "close WI-0324 only if the reading passes" is neither met nor unmet
    once the reading is called off, and a session holding one cannot stop. The three
    shapes it deliberately does not catch are all real goals operator has typed: a fronted
    conditional, a postposed one carrying its own alternative, and `if` meaning
    *whether*."""
    offences = []
    clauses = goal_clauses(text)
    for i, clause in enumerate(clauses, 1):
        match = _GOAL_COND_RE.search(clause)
        if not match:
            continue
        if _GOAL_FRONTED_RE.match(clause):
            continue
        tail = clause[match.end():]
        following = clauses[i] if i < len(clauses) else ""
        if _GOAL_ALTERNATIVE_RE.search(tail) or _GOAL_ALTERNATIVE_RE.match(following):
            continue
        if _GOAL_WHETHER_RE.search(clause):
            continue
        offences.append((i, clause, " ".join(match.group(1).split()).lower()))
    return offences


def goal_conditional_refusal(text: str, where: str) -> str:
    """The refusal for a goal line carrying a conditional clause, or `""` to allow it.

    `where` names the surface that is refusing, so the author reads a sentence about
    the thing they were doing. The message names the offending clause, says why a goal
    is the wrong home for it, and says where it belongs — a refusal that does not name
    the fix is a diagnosis, and the author is left to guess."""
    offences = goal_conditional_offences(text)
    if not offences:
        return ""
    lines = [f"{where}: refused — a goal clause is gated on a condition this session "
             f"may never be able to settle."]
    for index, clause, marker in offences:
        lines.append(f"  clause {index} ({marker!r}): {clause}")
    lines.append(
        "A goal is the sentence a Stop hook re-reads every time the session tries to "
        "stop. A clause like this has no readable answer once the thing it waits on is "
        "called off — not met, not unmet — and the session cannot leave (WI-0401: 34 "
        "turns, 2026-09-18). PUT THE CONDITION IN THE PROMPT BODY, where it informs the "
        "work without becoming a stop condition, and leave the goal stating what DONE "
        "looks like. A conditional that says what happens when it is false ('…, "
        "otherwise …') is fine here, and so is one that opens the clause ('If X, do Y').")
    return "\n".join(lines)


#: What can become of a clause. `met` and `not-met` are the evaluator's own vocabulary;
#: the other two are the states the 2026-09-18 and 2026-09-19 sessions had no way to
#: say, and both of them are claims ABOUT SOMEBODY ELSE'S WORDS.
GOAL_DISPOSITIONS = ("met", "not-met", "moot", "blocked")

#: The two that cannot be recorded without evidence. "Moot" and "blocked" are how a
#: clause stops counting against a session, so they are exactly the two a session has an
#: interest in claiming — and a bare "moot" is no more readable than the "No." it
#: replaces. The recorder REFUSES them without the instruction that mooted the clause or
#: the consent that is missing, quoted. This is the whole difference between the fix and
#: the workaround it must not become.
GOAL_EVIDENCE_REQUIRED = ("moot", "blocked")

GOAL_DISPOSITION_LABELS = {
    "met": "MET",
    "not-met": "NOT MET",
    "moot": "MOOT BY INSTRUCTION",
    "blocked": "BLOCKED",
}


def goal_disposition_refused(state: str, evidence: str) -> str:
    """`""` when this disposition may be recorded, else why it may not."""
    state = (state or "").strip().lower()
    if state not in GOAL_DISPOSITIONS:
        return (f"unknown disposition {state!r} — one of "
                f"{', '.join(GOAL_DISPOSITIONS)}.")
    if state in GOAL_EVIDENCE_REQUIRED and not (evidence or "").strip():
        return (f"{state!r} needs the words that did it — pass --because "
                f"\"<what was said>\" (or --because-file <path> when the sentence "
                f"carries backticks or $(…), which a shell eats silently). A clause "
                f"marked {state} without its evidence is a different unreadable "
                f"answer, not a readable one (WI-0401).")
    return ""


#: The sidecar suffix this session's goal record lands on, beside `.live` and
#: `.end-ran`. Per-machine residue by design: the DURABLE record of a goal's disposition
#: is the block `end` prints and the journal it is written beside, not this file.
GOAL_SIDECAR_SUFFIX = "goal"


def goal_record_path(session_id: str):
    """Where this session's goal record lives. Never creates anything."""
    return SESSION_STATE_DIR / f"{session_id}.{GOAL_SIDECAR_SUFFIX}"


def goal_record_read(session_id: str) -> dict:
    """This session's goal record, or an empty one. Never raises — a goal record that
    cannot be read must not stop a close, which is the failure this item is about."""
    empty = {"goal": "", "source": "", "dispositions": []}
    if not session_id:
        return empty
    try:
        data = json.loads(goal_record_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("goal", "")
    data.setdefault("source", "")
    if not isinstance(data.get("dispositions"), list):
        data["dispositions"] = []
    return data


def goal_record_write(session_id: str, record: dict) -> None:
    """Best-effort write, through the sidecar's own stem guard."""
    _sidecar_write(session_id, GOAL_SIDECAR_SUFFIX, record)


def _goal_key(clause: str) -> str:
    """The match key for a clause: its letters and digits, folded.

    DISPOSITIONS ARE KEYED ON THE CLAUSE, NOT ON ITS NUMBER. A session that records
    "clause 4 is moot" and then receives a second `/goal` would otherwise carry its
    verdicts onto whatever now sits in position 4 — a wrong answer produced silently,
    which is worse than no answer. Keying on the text means a re-worded goal simply
    loses the dispositions that no longer apply, and they print as NOT RECORDED."""
    return "".join(ch.lower() for ch in (clause or "") if ch.isalnum())


def goal_dispositions_by_clause(record: dict) -> dict:
    """`{clause-key: {state, because}}` from a goal record."""
    out = {}
    for entry in (record or {}).get("dispositions") or []:
        if isinstance(entry, dict) and entry.get("clause"):
            out[_goal_key(entry["clause"])] = entry
    return out


#: The words `/goal` accepts for "there is no goal any more". Taken from the runtime's
#: own command, not guessed: a session whose goal was cleared has no goal, and reporting
#: the last one it was given would be a block about an obligation nobody is holding it to.
GOAL_CLEAR_WORDS = ("clear", "stop", "off", "reset", "none", "cancel")


def _goal_from_transcript() -> tuple:
    """`(goal, "transcript")` from the Claude transcript, or `("", "")`.

    TWO RECORDS ARE READ, and the runtime's own is preferred. Claude Code writes the
    active goal into the transcript as a `goal_status` attachment carrying the condition
    verbatim — that is what it rehydrates from on resume, so it is the authority on what
    the Stop hook is evaluating right now. The typed `/goal` prompt is the fallback,
    because it is the only record when a goal was set before the harness learned to look
    and because it is what a person recognises.

    THE CLAUDE BINDING, and the only runtime-specific thing in this module
    ([ADR-0041](adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)): on
    this runtime a goal is a `/goal` prompt and lives nowhere but the transcript, so
    there is no store to read and asking the operator to re-type it into one would make
    the automatic case an errand. A runtime that has no such record answers `("", "")`
    and its sessions use `session.py goal set`, which is the contract-level path.

    The LAST `/goal` wins: a session that sets a second goal has replaced the first, and
    the Stop hook is evaluating the new one. Never raises."""
    sid = (_claude_session_id() or "").strip()
    if not sid:
        return "", ""
    try:
        roots = sorted((Path.home() / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
    except OSError:
        return "", ""
    # LAST ROW WINS, whichever kind it is. The two records interleave — a typed `/goal`
    # comes first and the runtime's `goal_status` rows follow it on every stop — so
    # reading in file order and keeping the latest is what tracks a goal that was
    # replaced mid-session. Preferring one record type wholesale would report the
    # superseded goal for as long as it took the other to catch up.
    found = ""
    for path in roots:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "goal" not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    kind = row.get("type")
                    if kind == "attachment":
                        att = row.get("attachment") or {}
                        if att.get("type") == "goal_status":
                            found = " ".join(str(att.get("condition") or "").split())
                        continue
                    if kind == "active_goal":
                        value = row.get("value")
                        if isinstance(value, dict):
                            found = " ".join(str(value.get("condition") or "").split())
                        elif value in (None, ""):
                            found = ""
                        continue
                    if kind != "user":
                        continue
                    content = (row.get("message") or {}).get("content")
                    if not isinstance(content, str):
                        continue
                    stripped = content.strip()
                    # A prompt that merely MENTIONS /goal is not one: the command has to
                    # open the message, exactly as the runtime parses it.
                    if not (stripped.startswith("/goal") and stripped[5:6] in (" ", "\n")):
                        continue
                    text = " ".join(stripped[5:].split())
                    if text.lower() in GOAL_CLEAR_WORDS:
                        found = ""
                    elif text:
                        found = text
        except OSError:
            continue
    return (found, "transcript") if found else ("", "")


def _goal_from_dispatch() -> tuple:
    """`(goal, "dispatch")` from this lane's own dispatch record, or `("", "")`.

    DERIVED, NOT TRANSMITTED, on the same argument `_dispatch_brief_block` makes for the
    brief itself: `POGA_DISPATCH` is already in the environment and the record is already
    in the shared coord store, so a goal copied into a sidecar at start would be a second
    copy to keep in step and a second way to arrive stale. It also means this works for a
    lane that was already running when the goal was set on the wave."""
    did = (os.environ.get("POGA_DISPATCH", "") or "").strip()
    if not did:
        return "", ""
    try:
        rec = _dispatch_read(did)
    except Exception:                                    # noqa: BLE001 — fail-open
        return "", ""
    goal = ((rec or {}).get("goal") or "").strip()
    return (goal, "dispatch") if goal else ("", "")


def active_goal(session_id: str) -> tuple:
    """`(goal_text, source)` for this session — `("", "")` when it has no goal.

    Three sources, strongest first, and the order is about who said it most recently and
    most specifically. An explicitly recorded goal wins: a session that ran
    `session.py goal-set` has stated what it is being held to, and a stale `/goal`
    further up the transcript is not a correction of it. A `/goal` typed into this lane
    beats the wave's goal for the same reason — it is about this lane, and it is what the
    Stop hook is actually evaluating."""
    record = goal_record_read(session_id)
    if (record.get("goal") or "").strip():
        return record["goal"].strip(), (record.get("source") or "recorded")
    text, source = _goal_from_transcript()
    if text:
        return text, source
    return _goal_from_dispatch()


def goal_disposition_block(text: str, records: dict, source: str = "") -> list:
    """The GOAL DISPOSITION block, as lines. Empty list when there is no goal.

    Silent when the session has no goal, which is nearly every session — a block
    announcing that nothing applies is noise on every close.

    A clause nobody disposed of prints `NOT RECORDED`, never `NOT MET`. The session may
    simply not have said, and reading silence as failure is the same fabrication as
    reading it as success ([`no-fabricated-data`](habits/master.md#no-fabricated-data));
    it also tells the reader precisely what to do about it."""
    clauses = goal_clauses(text)
    if not clauses:
        return []
    records = records or {}
    lines = ["", "--- GOAL DISPOSITION " + "-" * 45]
    if source:
        lines.append(f"goal ({source}): {text.strip()}")
    else:
        lines.append(f"goal: {text.strip()}")
    lines.append("")
    counts = {}
    for i, clause in enumerate(clauses, 1):
        rec = records.get(_goal_key(clause)) or {}
        state = (rec.get("state") or "").strip().lower()
        label = GOAL_DISPOSITION_LABELS.get(state, "NOT RECORDED")
        counts[label] = counts.get(label, 0) + 1
        lines.append(f"  {i}. [{label}] {clause}")
        because = " ".join((rec.get("because") or "").split())
        if because:
            # The quote is the load-bearing half of a moot or blocked clause. Indented
            # under the clause it disposes of and marked as a quotation, so the reader
            # can tell whose sentence took the clause away.
            lines.append(f"     because: “{because}”")
    lines.append("")
    lines.append("  " + " · ".join(f"{n} {label.lower()}"
                                        for label, n in sorted(counts.items())))
    if counts.get("NOT RECORDED"):
        lines.append("  NOT RECORDED is not a verdict — it means this session never said. "
                     "`session.py goal-dispose <n> <state>` records one.")
    lines.append("-" * 67)
    return lines


def goal_block_for_session(session_id: str) -> list:
    """The GOAL DISPOSITION block for `session_id`, as lines. `[]` when it has no goal.

    Never raises. `end` calls this on its way past, and a goal record that cannot be
    read is not a reason to refuse a close — refusing to close is the defect."""
    try:
        text, source = active_goal(session_id)
        if not text:
            return []
        record = goal_record_read(session_id)
        return goal_disposition_block(text, goal_dispositions_by_clause(record), source)
    except Exception:                                    # noqa: BLE001 — fail-open
        return []


def _goal_session_id() -> str:
    """This session's durable journal id, or `""` when it cannot be established."""
    found = _find_holder_journal()
    if not found:
        return ""
    return (found[1].get("session-id") or "").strip()


def _goal_text_from_args(args, verb: str) -> str:
    """The goal text an operator passed, from `--text` or `--file`.

    `--file` exists for the same reason `poga work edit` grew `--append-notes-file`
    (WI-0289): a sentence with backticks or `$(…)` in it is eaten by the shell on its
    way to `--text`, silently, and a goal is quoted prose about commands."""
    path = getattr(args, "file", None)
    if path:
        try:
            return " ".join(Path(path).read_text(encoding="utf-8").split())
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"session.py {verb}: cannot read {path} — {exc}")
    return " ".join((getattr(args, "text", None) or "").split())


def cmd_goal(args) -> None:
    """Print this session's goal, its clauses numbered, and what became of each."""
    sid = getattr(args, "session_id", None) or _goal_session_id()
    text, source = active_goal(sid)
    if not text:
        print("goal: none recorded for this session. A `/goal` prompt is picked up from "
              "the transcript; `session.py goal-set --text \"...\"` records one "
              "explicitly.")
        return
    for line in goal_block_for_session(sid):
        print(line)


def cmd_goal_set(args) -> None:
    """Record this session's goal explicitly, for a runtime that has no `/goal`."""
    sid = getattr(args, "session_id", None) or _goal_session_id()
    if not sid:
        raise SystemExit("session.py goal-set: cannot tell which session this is — no "
                         "open journal found. Pass --session-id.")
    text = _goal_text_from_args(args, "goal-set")
    if not text:
        raise SystemExit("session.py goal-set: pass --text \"<the goal>\" or --file <path>.")
    record = goal_record_read(sid)
    record["goal"] = text
    record["source"] = "recorded"
    goal_record_write(sid, record)
    # RECORDED, THEN WARNED — deliberately that way round. This verb TRANSCRIBES a goal
    # somebody already set; refusing it would leave the unreadable goal in force and the
    # record of it missing, which is the 2026-09-18 failure with an extra step. The
    # authoring surfaces (`poga dispatch --goal`, `curate/check-brief.py`) are where a
    # conditional is refused, because there the author can still rewrite it.
    refusal = goal_conditional_refusal(text, "session.py goal-set")
    if refusal:
        print(refusal.replace("refused —", "WARNING —", 1))
    print(f"goal recorded for {sid}: {text}")
    for line in goal_block_for_session(sid):
        print(line)


def cmd_goal_dispose(args) -> None:
    """Record what became of one clause of this session's goal."""
    sid = getattr(args, "session_id", None) or _goal_session_id()
    if not sid:
        raise SystemExit("session.py goal-dispose: cannot tell which session this is — "
                         "no open journal found. Pass --session-id.")
    text, _source = active_goal(sid)
    clauses = goal_clauses(text)
    if not clauses:
        raise SystemExit("session.py goal-dispose: this session has no goal to dispose "
                         "of. `session.py goal` shows what it has.")
    because = ""
    if getattr(args, "because_file", None):
        try:
            because = Path(args.because_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"session.py goal-dispose: cannot read "
                             f"{args.because_file} — {exc}")
    else:
        because = (getattr(args, "because", None) or "").strip()

    state = (args.state or "").strip().lower()
    refused = goal_disposition_refused(state, because)
    if refused:
        raise SystemExit(f"session.py goal-dispose: {refused}")

    wanted = []
    for token in str(args.clause).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token.strip("-"):
            lo, _, hi = token.partition("-")
            try:
                wanted.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                raise SystemExit(f"session.py goal-dispose: {token!r} is not a clause "
                                 f"number or range.")
        else:
            try:
                wanted.append(int(token))
            except ValueError:
                raise SystemExit(f"session.py goal-dispose: {token!r} is not a clause "
                                 f"number or range.")
    bad = [n for n in wanted if not 1 <= n <= len(clauses)]
    if bad:
        raise SystemExit(f"session.py goal-dispose: no clause "
                         f"{', '.join(str(n) for n in bad)} — this goal has "
                         f"{len(clauses)}. `session.py goal` numbers them.")

    record = goal_record_read(sid)
    kept = [e for e in record.get("dispositions") or []
            if isinstance(e, dict)
            and _goal_key(e.get("clause", "")) not in {_goal_key(clauses[n - 1])
                                                       for n in wanted}]
    for n in wanted:
        kept.append({"clause": clauses[n - 1], "state": state, "because": because,
                     "at": _now_iso()})
    record["dispositions"] = kept
    goal_record_write(sid, record)
    for line in goal_block_for_session(sid):
        print(line)


def cmd_goal_check(args) -> None:
    """Check a goal line for a conditional clause. Exit 2 when it carries one.

    THE VERB THE OTHER AUTHORING SURFACES CALL. `poga dispatch --goal` and
    `curate/check-brief.py` run this check in-process; a template that lives outside
    this repo — the Consultant's, kept in its own checkout — has no other way to
    apply the same rule, and a rule re-implemented over there would drift from this one
    by the second edit."""
    text = _goal_text_from_args(args, "goal-check")
    if not text:
        raise SystemExit("session.py goal-check: pass --text \"<the goal>\" or "
                         "--file <path>.")
    refusal = goal_conditional_refusal(text, "session.py goal-check")
    if refusal:
        print(refusal)
        raise SystemExit(2)
    print(f"goal-check: OK — {len(goal_clauses(text))} clause(s), none gated on a "
          f"condition this session could not settle.")
