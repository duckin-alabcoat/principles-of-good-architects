"""Extract every command the substrate PRINTS, and judge it against the guard stack
FOR THE AUDIENCE THAT WILL READ IT (WI-0158, ADR-0099 D4).

WHY THIS IS A LIBRARY AND NOT JUST A TEST. The extraction half is the part that can be
wrong in a way nothing notices: a naive grep both misses real advice (f-strings assembled
across branches, strings that are RETURNED rather than printed) and flags examples inside
docstrings — and a lint that misses the contradicted string does not merely fail to
detect, it certifies the problem as solved (`ship-the-detector-with-the-capability`, read
the right way round). Keeping the extractor separate from the assertions is what lets
`test_substrate_voice.py` feed it known-bad fixtures and prove it FIRES, rather than
asserting green over a detector that had quietly stopped looking.

THE AUDIENCE MODEL IS THE WHOLE PROBLEM. `git -C <main> reset --hard main` is ordinary
advice to a session standing in the main checkout and an unfollowable escalation to a
session in a lane — same bytes, opposite verdict. So this cannot be a blocklist. Two
guards decide, and they are not the same kind of thing:

  * `check-bash` (session.py, repo-owned) is CONTEXT-FREE — it reads its stdin JSON and
    nothing else: no env, no cwd, no notion of a lane. Whatever it denies, it denies to
    everybody. We call `session.destructive_git_violation` directly rather than restating
    the predicate, so this half can never drift from the guard it models.
  * The worktree-ISOLATION guard is the Claude Code harness's, has no source in this
    repo, and fires ONLY in a lane. Nothing here can call it, so this file carries a model
    of it — the one place the lint asserts a rule it cannot execute. The model is the
    contract the substrate already documents to itself inside its own deny message:
    refused is `git -C <other tree>`; sanctioned is `cd <lane> && python3 session.py
    <verb>` (session.py ~16213). Note the two oracles do NOT overlap: `git -C <main>
    status` is auto-ALLOWED by check-bash and refused by the isolation guard, so dropping
    either model loses a whole class.

DEFAULT AUDIENCE IS A LANE, and that is a decision, not a shortcut. ADR-0059 steers every
session into a worktree lane, so the lane reader IS the ordinary reader; assuming a
main-checkout audience is precisely the assumption that let `reap-lanes` advise
`git branch -D`. A site that genuinely addresses a main-checkout reader says so in
MAIN_AUDIENCE below, with a reason — an exemption list you can read in one screen, rather
than an inference the lint makes silently or a per-call-site annotation that decays.

MENTION IS NOT USE, and this is the subtlety that decides whether the lint is usable at
all. The substrate deliberately quotes the commands it REFUSES — ADR-0099 D3 requires
every denial to name the refused form beside the allowed one, so `check-bash`'s own deny
message reads "(the isolation guard refuses `git -C <lane>`, NOT this form)". A lint that
cannot tell naming from advising reports the R4 message as the defect it exists to
prevent. `_mentioned_as_refused` draws that line, and it is pinned from both sides in the
tests: a refused form named as refused must PASS, and the same form offered as a remedy
must FAIL.

stdlib only; imported by tests/test_substrate_voice.py.
"""

from __future__ import annotations

import ast
import functools
import json
import pathlib
import re
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import harness_fixture  # noqa: E402


# ── What gets linted ────────────────────────────────────────────────────────────
# session.py and poga are BYTE-IDENTICAL fleet substrate (curate/push-substrate.py
# BYTE_IDENTICAL), so a bad advice string in either is a bad advice string on every
# converged member — which is where this lint's leverage is, since the test itself is not
# shipped. The rest are federation-local but are read by the same lane sessions.
PY_SUBJECTS = [
    # The harness is `session.py` PLUS every `sessionlib/*.py` (ADR-0118), and the lint
    # has to name all of them: the advice strings this sweep exists to judge moved into
    # the package, and a subject list still saying "session.py" alone would lint a
    # 121-line entry point, find no commands, and report a clean fleet
    # ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
    # Derived, never transcribed — a part added to the package is linted the day it lands.
    # A pre-split member has no package and the list is just `session.py`, as before.
    *harness_fixture.harness_files(ROOT),
    "poga_cli.py",
    "bootstrap.py",
    "standard_check.py",
    "deploy/runner.py",
    "deploy/diagnose.py",
    "curate/common.py",
    "curate/push-substrate.py",
    "curate/deliver.py",
    "curate/gather.py",
    "curate/metrics.py",
    "curate/reconcile.py",
    "curate/standard_version.py",
    "curate/distill.py",
    "curate/standardize.py",
    "curate/mail-poller.py",
]
SH_SUBJECTS = [
    "poga",
    "bootstrap-kit/git-hooks/pre-commit",
    "bootstrap-kit/git-hooks/pre-push",
    "deploy/install-adopt-runner.sh",
    "deploy/install-deploy-sweep.sh",
    "deploy/install-gate-inputs.sh",
    "deploy/install-mail-poller.sh",
    "drills/attach-operator-setup.sh",
    "restore/step-zero.sh",
]
# Advice that lives in DATA, not code: `poga restore` prints these arrays verbatim as the
# operator's checklist (poga_cli.py ~838 and ~849). A command is no less printed for
# having been stored in JSON first.
JSON_SUBJECTS = {
    "credentials-manifest.federation.json": ("credentials", "provision"),
}
# Subjects the public cut withholds (curate/public_cut_manifest.json). The trunk scans
# them; the exported tree does not have them, so there they are skipped by name. Any
# OTHER missing subject still fails loudly: a subject list that silently shrinks is the
# failure this lint exists to prevent.
INTERNAL_ONLY = {
    "drills/attach-operator-setup.sh",
    "restore/step-zero.sh",
    "credentials-manifest.federation.json",
}


def shipped(subject: str) -> bool:
    """False only for an internal-only subject that is absent from this tree."""
    return subject not in INTERNAL_ONLY or (ROOT / subject).exists()
# state-manifest.federation.json is no longer a subject: since ADR-0094 it carries no
# retrieval prose for `poga restore` to print. (Its old entry read a top-level `state`
# key the file never had, so it scanned nothing even before that.)


# ── Audience ────────────────────────────────────────────────────────────────────
LANE = "lane"
MAIN = "main"

# Sites whose reader is provably standing in the main checkout. Keyed `<subject>::<fn>`.
# EVERY entry carries a reason: an exemption whose argument is not written down is
# indistinguishable from an oversight, and it is the exemption list, not the rule, that a
# lint dies of. Keep this short enough to read in one screen — if it is not, the default
# is wrong and the model should be revisited rather than the list extended.
MAIN_AUDIENCE: dict[str, str] = {}


def audience_for(subject: str, qualname: str) -> str:
    """The reader of a string emitted from `qualname` in `subject`.

    Default LANE — see the module docstring. A MAIN answer is an explicit, reasoned entry
    above, never an inference the lint makes on its own.
    """
    return MAIN if f"{subject}::{qualname}" in MAIN_AUDIENCE else LANE


# ── Extraction ──────────────────────────────────────────────────────────────────
# Sink-blind on purpose. Of WI-0158's three named specimens exactly ONE is a print()
# literal — the second is a returned f-string tuple element, the third an atomic_write
# argument — so a print-only extractor (which is what the precursor
# tests/test_substrate_advice.py was) detects one defect in three. argparse `help=` text
# is included for the same reason: it reaches a reader as advice.

_PLACEHOLDER = "<…>"  # what an f-string's {expr} renders as


class Found:
    """One string the substrate emits, with enough provenance to fix it."""

    __slots__ = ("subject", "line", "qualname", "text", "context")

    def __init__(self, subject: str, line: int, qualname: str, text: str,
                 context: str = ""):
        self.subject, self.line, self.qualname = subject, line, qualname
        self.text, self.context = text, context

    @property
    def where(self) -> str:
        return f"{self.subject}:{self.line}"

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<Found {self.where} {self.text[:60]!r}>"


def _render_joined(node: ast.JoinedStr) -> str:
    out = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            out.append(part.value)
        elif isinstance(part, ast.JoinedStr):
            out.append(_render_joined(part))
        else:
            out.append(_PLACEHOLDER)
    return "".join(out)


def _prose_nodes(tree: ast.AST) -> set[int]:
    """Nodes that are PROSE, not output: docstrings and any bare string statement.

    This is the false-positive hazard WI-0158 names outright. `cmd_reap_lanes`'s own
    docstring reads *"This docstring used to name `git branch -D`, which check-bash
    denies"* — a sentence that exists to record the fix, and the single highest-value
    false positive in the repo. Comments are absent from the AST for free.
    """
    marked: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for stmt in body:
                if (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)):
                    marked.add(id(stmt.value))
    return marked


def _qualnames(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(start, end, name) per function, narrowest first, so a line resolves to the
    tightest enclosing definition. Narrowest-first matters for deploy/runner.py, whose
    output helpers are NESTED defs a module-level scan would attribute to `deploy()`."""
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name))
    spans.sort(key=lambda s: (s[1] - s[0]))
    return spans


def _window(lines: list[str], start: int, end: int) -> str:
    """Raw source around a string, used ONLY for the mention/use judgement.

    A refusal cue often sits in the preceding print() call or an adjacent comment rather
    than inside the string itself — `session.py` splits one sentence across three prints,
    with "refuses" landing in the third. Reading a small source window catches that
    without the fragility of merging statements.
    """
    lo, hi = max(0, start - 3), min(len(lines), end + 1)
    return "\n".join(lines[lo:hi])


def strings_in_python(subject: str) -> list[Found]:
    path = ROOT / subject
    return _strings_in_python_cached(subject, str(path), path.stat().st_mtime_ns)


@functools.lru_cache(maxsize=None)
def _strings_in_python_cached(subject: str, path_str: str, _mtime: int) -> list[Found]:
    """Keyed on the RESOLVED path and mtime, not on `subject` alone: the extractor tests
    repoint ROOT at a temp fixture, and a subject-only cache would serve them session.py.
    Parsing a 20k-line module once instead of once per caller is what keeps this lint
    cheap enough to sit in the merge gate every lane runs."""
    src = pathlib.Path(path_str).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    prose = _prose_nodes(tree)
    spans = _qualnames(tree)

    def qual(line: int) -> str:
        for start, end, name in spans:
            if start <= line <= end:
                return name
        return "<module>"

    consumed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for child in ast.walk(node):
                if child is not node:
                    consumed.add(id(child))

    out: list[Found] = []
    for node in ast.walk(tree):
        if id(node) in prose or id(node) in consumed:
            continue
        if isinstance(node, ast.JoinedStr):
            text = _render_joined(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        out.append(Found(subject, node.lineno, qual(node.lineno), text,
                         _window(lines, node.lineno, end)))
    return out


_ECHO_RE = re.compile(r"^\s*(?:echo|printf)\b(.*)$")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def strings_in_shell(subject: str) -> list[Found]:
    """Bash has no AST here, so read what the script actually emits: echo/printf payloads
    and heredoc bodies. Comment lines are skipped — `poga`'s header is a long design essay
    that names half the verb set in prose, and every line of it would otherwise read as
    advice."""
    out: list[Found] = []
    lines = (ROOT / subject).read_text(encoding="utf-8").splitlines()
    heredoc: str | None = None
    for i, raw in enumerate(lines, start=1):
        if heredoc is not None:
            if raw.strip() == heredoc:
                heredoc = None
            else:
                out.append(Found(subject, i, "<heredoc>", raw.replace("\\`", "`"),
                                 _window(lines, i, i)))
            continue
        if raw.lstrip().startswith("#"):
            continue
        m = _HEREDOC_RE.search(raw)
        if m:
            heredoc = m.group(1)
            continue
        m = _ECHO_RE.match(raw)
        if m:
            out.append(Found(subject, i, "<echo>", m.group(1).replace("\\`", "`"),
                             _window(lines, i, i)))
    return out


def strings_in_json(subject: str, top: str, key: str) -> list[Found]:
    raw = json.loads((ROOT / subject).read_text(encoding="utf-8"))
    out: list[Found] = []
    entries = raw.get(top)
    if isinstance(entries, dict):
        entries = list(entries.values())
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for line in entry.get(key, []) or []:
            if isinstance(line, str):
                out.append(Found(subject, 0, f"<{top}.{key}>", line, line))
    return out


def emitted_strings() -> list[Found]:
    out: list[Found] = []
    for s in PY_SUBJECTS:
        if shipped(s):
            out.extend(strings_in_python(s))
    for s in SH_SUBJECTS:
        if shipped(s):
            out.extend(strings_in_shell(s))
    for s, (top, key) in JSON_SUBJECTS.items():
        if shipped(s):
            out.extend(strings_in_json(s, top, key))
    return out


# ── Command shape ───────────────────────────────────────────────────────────────
# Two span sources, because neither alone is honest. Backticks are the substrate's own
# convention for "I mean this as a command" and give high precision — every one of
# WI-0158's specimens is backticked, and so is every sanctioned verb in the R4 deny
# message. But session.py:12812 prints `Inspect: git -C {path} status` with no backticks
# at all, and that is a live instance of the WI-0099 class, so backticks alone
# under-detect. The unbackticked pass is therefore CUE-ANCHORED — an imperative or a
# colon must introduce it — which is what keeps prose like "poga owns the lane pool" out
# of the command set without a hand-maintained ignore list.
_BACKTICK_RE = re.compile(r"`([^`\n]{2,300})`")
_CUE_RE = re.compile(
    r"(?:^|[.—;]\s|\b(?:run|inspect|fix|try|land|clear|sync|use|do|repair|retry)\b"
    r"\s*(?:it|this|them|that)?\s*[:\s])\s*"
    r"((?:git|gh|poga|python3|python|cd|ssh|rsync|launchctl)\b[^`\n.;]{2,200})",
    re.I)

EXECUTABLES = frozenset({
    "git", "gh", "python3", "python", "poga", "cd", "rm", "mv", "cp", "ln", "mkdir",
    "chmod", "ssh", "scp", "rsync", "brew", "npm", "claude", "open", "launchctl",
    "tmux", "pkill", "kill", "sudo", "caffeinate", "osascript", "defaults", "tmutil",
})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _argv(segment: str) -> list[str]:
    try:
        argv = shlex.split(segment.replace(_PLACEHOLDER, "PLACEHOLDER"))
    except ValueError:
        return []
    while argv and _ENV_ASSIGN_RE.match(argv[0]):
        argv.pop(0)
    return argv


def segments(command: str) -> list[str]:
    """Split a compound on `&&`, `||`, `;`, `|`. Each half is judged separately: in
    `cd <lane> && python3 session.py merge` the `cd` is innocuous and the verb is the part
    that has to exist."""
    return [s.strip() for s in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command) if s.strip()]


# A backticked span whose first word happens to be a tool name is often PROSE — the
# substrate names its own components inside backticks too (`poga lane · <branch>
# (isolated worktree…)`, `poga lane launcher + WorktreeRemove teardown hook (ADR-0060)`).
# These markers never appear in a command and always appear in a description, so they are
# the cheap discriminator; a token-count heuristic would have cut real advice like
# `poga deploy --status   # what is running where` instead.
_PROSE_MARKERS = re.compile(r"[·→←]|\s\+\s|\(\w[^)]{3,}\)")
_TRAILING_COMMENT = re.compile(r"\s{2,}#.*$")

# `<verb>`, `<…>`, `$1`, `${verb}`, `PLACEHOLDER` — a slot, not a name. Judging a slot as
# a verb reports every correctly-parameterised usage line as a defect, which is how a
# lint teaches people to ignore it.
_PLACEHOLDERISH = re.compile(r"^(?:PLACEHOLDER|<.*|\$\{?\w+\}?|…|\.\.\.)")


def _clean(span: str) -> str:
    return _TRAILING_COMMENT.sub("", span).strip()


def looks_like_command(span: str) -> bool:
    if _PROSE_MARKERS.search(span):
        return False
    for seg in segments(_clean(span)):
        argv = _argv(seg)
        if argv and argv[0] in EXECUTABLES:
            return True
    return False


# A backticked span quoted as REFUSED is documentation, not advice — ADR-0099 D3 requires
# exactly that quoting, so this is the substrate obeying canon, not violating it. The cue
# must sit before the command and close to it; a refusal three sentences upstream says
# nothing about this one.
_REFUSAL_CUE = re.compile(
    r"\b(?:refuse[sd]?|refusing|denie[sd]|deny|denied|DENIED|block(?:s|ed)?|reject(?:s|ed)?"
    r"|forbid(?:s|den)?|never print|used to print|used to say|cannot|can't|not denied"
    r"|instead of|rather than|no longer)\b", re.I)
_NEGATION_SPAN = 90  # characters of lookback; one clause, not one paragraph


# A cue can also FOLLOW the command: "The `git -C <path> …` form can't be matched by a
# settings allow-string" names a shape in order to explain the guard, and reads as advice
# only to a lint that looks in one direction.
_MENTION_TAIL = re.compile(
    r"^\W{0,3}(?:form|forms|shape|syntax|prompts?)\b"
    r"|^.{0,60}?\b(?:can't|cannot|is refused|are refused|is denied|are denied)\b", re.I)

# A REPORT is not an instruction. "it means this machine reached the work machine, ran
# `tmux ls` there, and got a definite empty" narrates what the tool already did; reading
# it as advice would have the lint demanding that a drill stop describing its own probe.
# Past-tense reporting verbs are the tell, and they are unambiguous in this corpus.
_DESCRIPTIVE_LEAD = re.compile(
    r"\b(?:ran|runs|ran the|executed|printed|prints|said|says|used|issued|called|invoked|"
    r"reached|advised|advises|carried out|emitted|emits|reports?|reported)\s+\W{0,3}$",
    re.I)


def _mentioned_as_refused(found: Found, command: str) -> bool:
    for hay in (found.text, found.context):
        idx = hay.find(command)
        while idx != -1:
            before = hay[max(0, idx - _NEGATION_SPAN):idx]
            after = hay[idx + len(command): idx + len(command) + _NEGATION_SPAN]
            if (_REFUSAL_CUE.search(before) or _MENTION_TAIL.search(after)
                    or _DESCRIPTIVE_LEAD.search(before)):
                return True
            idx = hay.find(command, idx + 1)
    return False


# Deixis and host-boundness are claims about WHERE THIS COMMAND RUNS, so they have to be
# read against the command's own clause. Judged against the whole string instead, they
# convict two sentences that are doing their job: "main: no live session there — `python3
# session.py main-restore --dry-run` says which…" (the "there" is a statement of fact
# about main, and the verb offered IS lane-runnable), and "Develop on devbox; ship here
# with `poga deploy <system>`" (the host phrase belongs to the clause before the
# semicolon; the command runs right here).
_CLAUSE_BREAK = re.compile(r"[.;—\n]|\s-\s")


def _clause_around(text: str, command: str, span: int = 60) -> str:
    idx = text.find(command)
    if idx == -1:
        return ""
    head = text[max(0, idx - span):idx]
    breaks = list(_CLAUSE_BREAK.finditer(head))
    if breaks:
        head = head[breaks[-1].end():]
    tail = text[idx + len(command): idx + len(command) + span]
    m = _CLAUSE_BREAK.search(tail)
    if m:
        tail = tail[:m.start()]
    return head + " " + tail


def commands_in(text: str) -> list[str]:
    out = [s.strip() for s in _BACKTICK_RE.findall(text)]
    stripped = _BACKTICK_RE.sub(" ", text)
    out += [m.group(1).strip() for m in _CUE_RE.finditer(stripped)]
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen and looks_like_command(c):
            seen.add(c)
            uniq.append(c)
    return uniq


# ── The verb sets, read from the tools themselves ───────────────────────────────
# Derived, never transcribed. A hand-copied verb list is the same defect one level up: it
# is how `poga`'s unknown-verb message came to print a set missing seven live verbs.

@functools.lru_cache(maxsize=None)
def session_verbs() -> set[str]:
    src = harness_fixture.harness_source(ROOT)
    return set(re.findall(r'sub\.add_parser\(\s*"([a-z0-9][a-z0-9-]*)"', src))


@functools.lru_cache(maxsize=None)
def poga_verbs() -> set[str]:
    """poga's authoritative verb set is the top-level `case "$verb" in` dispatch table —
    not its --help text and not its error message, which are prose and can rot."""
    lines = (ROOT / "poga").read_text(encoding="utf-8").splitlines()
    # Scope to main()'s dispatch. `cmd_work` and `cmd_ops` carry their own `case "$verb"
    # in` blocks at the same indentation for SUB-verbs (`poga work list`, `poga ops ran`),
    # and folding those into the top-level set would quietly make the check permissive —
    # `poga status` would pass because `status` is a sub-verb of something else.
    start = next((i for i, ln in enumerate(lines) if ln.startswith("main() {")), 0)
    verbs: set[str] = set()
    for raw in lines[start:]:
        # No trailing-whitespace requirement: half the branches are `deploy)` alone on
        # the line with the body below it, and requiring `)\s` silently dropped SEVEN
        # live verbs — deploy, recover, promote, integrate, main-sync, main-restore,
        # test. A verb-existence check that under-reads the verb table manufactures the
        # exact defect it hunts, which is why this list is derived and pinned in tests.
        m = re.match(r"^ {4}([a-z][a-z0-9|-]*)\)", raw)
        if m:
            verbs.update(v for v in m.group(1).split("|") if v)
    return verbs


@functools.lru_cache(maxsize=None)
def settings_denials() -> list[str]:
    """Prefix-anchored `permissions.deny` entries — a SECOND refusal surface. It carries
    `gh repo delete`, `gh repo edit --visibility` and `rm -rf .git`, none of which
    check-bash sees, so consulting only the guard would miss them."""
    raw = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    out = []
    for entry in raw.get("permissions", {}).get("deny", []):
        m = re.match(r"^Bash\((.*)\)$", entry)
        if not m:
            continue
        pat = m.group(1)
        for tail in (":*", " *"):
            if pat.endswith(tail):
                pat = pat[: -len(tail)]
        out.append(" ".join(pat.split()))
    return out


# ── Rules ───────────────────────────────────────────────────────────────────────

class Violation:
    __slots__ = ("rule", "found", "command", "detail")

    def __init__(self, rule: str, found: Found, command: str, detail: str):
        self.rule, self.found, self.command, self.detail = rule, found, command, detail

    def __str__(self) -> str:
        return (f"[{self.rule}] {self.found.where} in {self.found.qualname}()\n"
                f"    emitted: `{self.command}`\n"
                f"    {self.detail}")


_PARK = ("Until the verb exists, say so and park additively — commit in-lane, file or "
         "point at the work item — rather than emitting a command the reader cannot run "
         "(ADR-0099 D4, `never-route-your-own-work-through-the-user`).")


def _rule_destructive_git(found: Found, command: str, audience: str) -> Violation | None:
    """Delegated wholesale to the guard itself. Restating the predicate here would create
    exactly the drift the precursor's CheckBashAgreesTest existed to catch."""
    import session  # lazily, so this module imports without the repo already on sys.path

    reason = session.destructive_git_violation(command)
    if reason is None:
        return None
    return Violation("destructive-git", found, command,
                     "check-bash DENIES this, so the substrate is advising a command it "
                     "then refuses. " + reason.splitlines()[0])


def _rule_settings_deny(found: Found, command: str, audience: str) -> Violation | None:
    flat = " ".join(command.split())
    for seg in segments(flat):
        for pat in settings_denials():
            if seg.startswith(pat):
                return Violation("settings-deny", found, command,
                                 f"matches the permissions.deny entry `{pat}` — refused "
                                 f"before check-bash ever sees it.")
    return None


def _rule_verb_exists(found: Found, command: str, audience: str) -> Violation | None:
    """The class the 2026-09-03 live example belongs to: a remedy naming a CLI and a verb
    that CLI does not implement. `poga dispatch-retry D-xxxx` is not a poga verb — the
    verb is real but it lives on session.py — and poga's unknown-verb branch exits 0, so
    the reader gets a usage message and a success code."""
    for seg in segments(_clean(command)):
        argv = _argv(seg)
        if not argv:
            continue
        if argv[0] == "poga" and len(argv) > 1 and not argv[1].startswith("-"):
            if _PLACEHOLDERISH.match(argv[1]):
                continue
            if argv[1] not in poga_verbs():
                extra = (f" It IS a session.py subcommand — `python3 session.py {argv[1]}`."
                         if argv[1] in session_verbs() else "")
                return Violation("verb-exists", found, command,
                                 f"`{argv[1]}` is not a poga verb.{extra}")
        if argv[0] in ("python3", "python") and len(argv) > 2:
            if argv[1].rsplit("/", 1)[-1] == "session.py" and not argv[2].startswith("-"):
                if _PLACEHOLDERISH.match(argv[2]):
                    continue
                if argv[2] not in session_verbs():
                    return Violation("verb-exists", found, command,
                                     f"`{argv[2]}` is not a session.py subcommand.")
    return None


_GIT_C_RE = re.compile(r"\bgit\s+(?:-c\s+\S+\s+)*-C\b")


def _rule_lane_isolation(found: Found, command: str, audience: str) -> Violation | None:
    """The audience-dependent rule, and the only one this file MODELS rather than calls.

    In a lane the harness refuses a git invocation aimed at another working tree, and it
    refuses `cd <other> && git …` too — it objects to the redirect, not to the syntax used
    to reach it. The sanctioned shape, which the substrate's own deny message already
    names, routes through a verb instead: `cd <lane> && python3 session.py <verb>`.
    """
    if audience != LANE:
        return None
    if _GIT_C_RE.search(command):
        return Violation("lane-isolation", found, command,
                         "a lane reader's harness refuses `git -C <other tree>`. "
                         "Sanctioned: `cd <lane> && python3 session.py <verb>`, or the "
                         "verb that owns this repair. " + _PARK)
    segs = segments(command)
    if any(_argv(s)[:1] == ["cd"] for s in segs):
        for seg in segs:
            argv = _argv(seg)
            if argv and argv[0] == "git":
                return Violation("lane-isolation", found, command,
                                 "a bare git call after `cd` into another tree is refused "
                                 "in a lane. Sanctioned: `cd <lane> && python3 session.py "
                                 "<verb>`. " + _PARK)
    return None


# Deixis: an instruction whose LOCATION is unnamed, or names a place the reader has no
# sanctioned route to. WI-0158's third class, and the one with no command-level tell at
# all — `python3 session.py compile` is a perfectly good command; "there" is what makes it
# unfollowable.
_DEIXIS = [
    # Locative "there" only. Existential "there is no verb yet" — the exact sentence
    # ADR-0099 D4 tells a blocked tool to print — is the OPPOSITE of this defect, and
    # flagging it would have the lint condemning the sanctioned fallback.
    (re.compile(r"\bthere\b(?!\s+(?:is|are|was|were|'s|will|would|may|might|has|have|had|"
                r"remains?|exists?))", re.I),
     "'there' names no location the reader can resolve"),
    (re.compile(r"\brun (?:it|this|that|the plain command)\b", re.I),
     "'run it/this' has no runnable subject"),
    (re.compile(r"\bin (?:a|your) terminal\b", re.I),
     "asks for a terminal — the reader IS the terminal (ADR-0099)"),
    (re.compile(r"\bopen a (?:main-checkout|non-lane|plain|different)[a-z -]*session\b", re.I),
     "asks for a session of a particular shape (ADR-0099, operator 2026-08-19)"),
    (re.compile(r"\bnot in a lane\b", re.I),
     "asks for a session of a particular shape (ADR-0099)"),
    (re.compile(r"\bpaste\b.{0,40}\b(?:command|prompt)\b", re.I),
     "asks the reader to paste a command (ADR-0099)"),
]

# The hardware/host half the 2026-09-03 consultant brief asks this lint to carry. Advice
# that can only be carried out on ANOTHER machine is unfollowable in exactly the way
# "there" is, and a lane has no route across the machine boundary (ADR-0103 D1: neither
# machine holds credentials for the other). WI-0232 carries the paired canon change and
# needs the operator's registry-Accept; this detector needs none to exist, which is the right way
# round — ship the detector, let canon catch up.
_HOST = re.compile(
    r"\b(?:on|from|over on) (?:the )?"
    r"(?:Runner|devbox|DevBox|Laptop\w*|other machine|work machine)\b")


def _rule_deixis(found: Found, command: str, audience: str) -> Violation | None:
    clause = _clause_around(found.text, command)
    for pat, why in _DEIXIS:
        if pat.search(clause):
            return Violation("deixis", found, command, why + ". " + _PARK)
    return None


def _rule_host_bound(found: Found, command: str, audience: str) -> Violation | None:
    m = _HOST.search(_clause_around(found.text, command))
    if m:
        return Violation("host-bound", found, command,
                         f"the remedy is bound to another host ({m.group(0)!r}), which the "
                         f"reader cannot reach. " + _PARK)
    return None


RULES = [
    _rule_destructive_git,
    _rule_settings_deny,
    _rule_verb_exists,
    _rule_lane_isolation,
    _rule_deixis,
    _rule_host_bound,
]


def judge(found: Found, audience: str | None = None) -> list[Violation]:
    aud = audience or audience_for(found.subject, found.qualname)
    out = []
    for command in commands_in(found.text):
        if _mentioned_as_refused(found, command):
            continue
        for rule in RULES:
            v = rule(found, command, aud)
            if v is not None:
                out.append(v)
    return out


def sweep() -> list[Violation]:
    out = []
    for found in emitted_strings():
        out.extend(judge(found))
    return out


if __name__ == "__main__":  # `python3 tests/substrate_voice.py` — the operator's view
    import collections
    import sys

    sys.path.insert(0, str(ROOT))
    vs = sweep()
    by_rule = collections.Counter(v.rule for v in vs)
    for v in vs:
        print(v)
        print()
    print(f"{len(vs)} violation(s): "
          + ", ".join(f"{k}={n}" for k, n in sorted(by_rule.items())))
