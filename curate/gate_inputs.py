#!/usr/bin/env python3
"""Derive the land gate's INPUT SET by measuring it, never by listing it.

WI-0294, from the 2026-09-05 consultant brief: D1 requires the input set to come
from an `sys.addaudithook` measurement "never hand-listed"; D1a applies the
resulting classification at land ENTRY, so a lane whose diff the suite cannot read
skips the suite instead of queueing behind it.

Deciding *which* paths the suite reads is the whole safety question. A wrong entry
skips a gate that was needed, and it fails silently — the `declare-what-a-check-
assumes` failure in its purest form, where "I could not tell" and "I checked and
it's fine" collapse into one answer. Session ~206 already found one counterexample
by hand: `_attention_session_is_over` globs and reads every file under
`sessions/journal/`, so journals are not self-evidently neutral however obvious it
looks. Hence: measure.

Two properties the record must keep, because the consumer depends on both:

- **It records what was READ, not what is neutral.** The neutral set is the
  complement, computed at classification time. Recording the complement would mean a
  path nobody has ever measured defaults to "safe", which is exactly backwards.
- **It carries its own provenance** — the commit measured at, when, how many tests
  ran, the suite command. A record with zero tests, an unknown schema, or no
  prefixes is not a measurement, and `load()` collapses all of those to None so the
  caller gates rather than trusting it.

Usage:
    python3 curate/gate_inputs.py --derive     # run the suite, write gate-inputs.json
    python3 curate/gate_inputs.py --status     # what the current record says
"""

from __future__ import annotations

import os as _os
import sys as _sys

# WI-0328 — the interpreter pin, before this module does anything. The gate spawns
# this file with `sys.executable`, so under a land it already inherits the right
# answer; run BY HAND it inherited the operator's PATH instead, and on one machine
# those two resolve to two different Python installs at two different versions,
# which disagree about the suite's verdict. Appended to `sys.path`, never prepended:
# this is a lookup for one module, not a claim to shadow anything already importable.
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.append(_REPO)
try:
    import interpreter as _interpreter
except Exception:  # a member without the module runs exactly as it did before
    _interpreter = None
else:
    # ONLY when this file IS the program. Re-execing a process that merely imported
    # us would replace somebody else's program with ours — and it did: `import
    # session` from a test module restarted the whole test runner under the pin.
    if __name__ == "__main__":
        _interpreter.ensure(_REPO)

import hashlib
import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORD = ROOT / "gate-inputs.json"

#: The serial probe's own ceiling, and the knob a caller that knows its runtime turns.
#: 1800s is sized for an interactive shell. The nightly runner is a LaunchDaemon with
#: `ProcessType: Background`, which macOS throttles on CPU and I/O, so under that policy
#: a suite of several thousand tests can need about an hour, and a fixed 1800 would kill
#: it with a bare TimeoutExpired traceback. The default stays where it was for
#: an interactive derive; the runner passes its own, sized under its backstop.
PROBE_TIMEOUT_ENV = "POGA_GATE_PROBE_TIMEOUT"
PROBE_TIMEOUT_DEFAULT = 1800


def probe_timeout(environ=None) -> int:
    """Seconds the serial probe may run: `POGA_GATE_PROBE_TIMEOUT` if it is a positive
    integer, else the default. A malformed value falls back rather than raising, because
    the caller is an unattended nightly and a typo must not cost the whole night — but the
    value it fell back to is printed in the timeout refusal, so the typo is visible."""
    raw = (os.environ if environ is None else environ).get(PROBE_TIMEOUT_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return PROBE_TIMEOUT_DEFAULT
    return value if value > 0 else PROBE_TIMEOUT_DEFAULT

#: The child-side audit shim's directory. On `sys.path` here so the parent can reuse
#: the child's read-classification rule, and handed to every descendant on `PYTHONPATH`
#: so each one measures itself (WI-0337). Two module names only — `poga_child_audit`
#: and `sitecustomize` — because everything a child imports from us is a thing we have
#: put into its namespace whether it wanted it or not.
CHILD_AUDIT_DIR = ROOT / "curate" / "childaudit"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CHILD_AUDIT_DIR))
import session  # noqa: E402  (deferred: ROOT has to be on the path first)
import poga_evidence as evidence  # noqa: E402  (same)
import poga_child_audit  # noqa: E402  (same)

#: Record schema. A consumer that does not recognise it gates.
#:
#: 2 — WI-0337 added the child-process measurement and the `child_audit` receipt. The
#: bump is the point, not a side effect: every schema-1 record was derived by a probe
#: that could not see subprocess reads, so it under-reports its input set and
#: under-reporting is what licenses a skip. Making them unreadable retires the whole
#: generation at once rather than leaving them to age out one nightly at a time.
#:
#: 3 — WI-0347 added the per-command `commands` block. Same argument in a new place: a
#: schema-2 record states ONE input set and the land applied it to the whole `gate`
#: list, so there is no way to read one command-wise at all. It is not that such a
#: record is wrong — it is that it cannot answer the question the new classifier asks,
#: and a reader that cannot answer must gate rather than guess.
SCHEMA = 3

#: WI-0431. How many failing ids of each kind the record keeps. An additive field, so the
#: schema is unchanged: a record that lacks it reads as "names not recorded", never as
#: "nothing failed" — the counts in the verdict are the authority on how many.
FAILING_IDS_CAP = 200
#: WI-0432. How long one failure's reason may be. The 2026-09-27 red night named all 72
#: tests and still needed a reproduction to learn that every one of them said the same
#: sentence ("REFUSED to run under com.federation.gate-inputs"); the sentence's head is
#: enough to see that, and the cap keeps 400 of them from bloating the record.
FAILING_REASON_CAP = 200


def failure_reason(formatted: str) -> str:
    """The first line of the exception in a unittest-formatted traceback, capped.

    The exception line is the first unindented line after the last `  File` frame — not
    simply the last line, because a multi-line message puts its tail there. With no frame
    at all (a bare string), the first non-empty line stands in."""
    lines = formatted.splitlines()
    frames = [i for i, l in enumerate(lines) if l.startswith("  File ")]
    rest = lines[frames[-1] + 1:] if frames else lines
    line = next((l for l in rest if l.strip() and not l.startswith(" ")),
                next((l for l in lines if l.strip()), ""))
    line = line.strip()
    return line if len(line) <= FAILING_REASON_CAP else line[:FAILING_REASON_CAP - 1] + "…"


def failing_ids(result) -> dict:
    """WI-0431: the failing test ids from a unittest result, for the record's verdict.

    The probe's runner writes to devnull, so without these a red derive knew it was red
    and could not say which tests. Capped; the counts beside them stay whole, so a
    truncated list still says so. WI-0432 adds `failing_reasons`, id → the first line of
    its exception, for the same ids: a name says WHERE, and the night it matters nobody
    is there to rerun it and learn WHY."""
    failures = sorted((t.id(), tb) for t, tb in result.failures)[:FAILING_IDS_CAP]
    errors = sorted((t.id(), tb) for t, tb in result.errors)[:FAILING_IDS_CAP]
    return {"failure_ids": [i for i, _ in failures],
            "error_ids": [i for i, _ in errors],
            "failing_reasons": {i: failure_reason(tb) for i, tb in failures + errors}}

#: The suite the gate runs — kept identical to `_run_gate`'s default in session.py.
def GATE_SUITE():
    """Build the SERIAL measurement command with the invoking interpreter.

    SERIAL IS THE WHOLE POINT, and it is the one thing about this module that looks
    like an oversight and is not. The land gate runs `curate/run_suite.py`, which is
    the same suite sharded across cores — 331.7s to 71.8s — and the obvious
    improvement is to point the deriver at it too. It would measure almost nothing:
    the runner puts each shard in a `subprocess.run`, and `sys.addaudithook` is
    per-interpreter. Worse, it would not fail loudly, because `derive()` unions the
    declared prefixes in afterwards and a near-empty measurement still assembles a
    well-formed record. Every path outside the declared few would classify
    gate-neutral and skip the gate.

    That was written in three prose places and enforced in none until WI-0338;
    `tests/test_gate_input_deriver_is_serial.py` is the guard, and it watches this
    command, `_probe`'s ordering, and an actual measurement.
    """
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests"]

#: Prefixes read by gate commands that are NOT the measured suite (WI-0307).
#:
#: The probe below measures ONE command — `unittest discover -s tests` — under an audit
#: hook. The gate runs four more beside it, and their inputs are therefore measured by
#: nothing at all. For three of them that never mattered, because what they read
#: (`curate/`, `CANON.md`, `STANDARD.md`, …) is also read by the suite and so appears in
#: the measurement anyway. `session.py wi-check` broke that accident: it reads the two
#: store directories, the suite does not, and so a land whose diff touched only the store
#: classified as gate-neutral and skipped the whole gate — including the validator that
#: exists for exactly that diff.
#:
#: DECLARED, AND SAID TO BE DECLARED. The module header's first property is that the
#: record holds what was READ, never what someone believed was safe, and a hand-added
#: entry silently mixed into a measurement would make that provenance a lie. So these are
#: unioned into `read_prefixes` — which is what the land consumes and must be complete —
#: AND recorded separately as `declared_prefixes`, so a reader can always tell which
#: entries a machine observed and which a person asserted
#: ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
#:
#: The honest fix one layer up is per-command input sets, so each gate check declares or
#: measures its own and the classifier skips checks rather than the whole gate. That is a
#: change to a load-bearing path and is left as a finding rather than smuggled in here.
#:
#: `sessionlib/` IS THE SECOND REASON, and it is a different one — not a command the probe
#: does not run, but a read the probe cannot see. `import session` happens at THIS
#: module's import, before `sys.addaudithook` is installed, and every test module's own
#: `import session` then hits `sys.modules` without touching disk again. So the harness's
#: own source was read exactly once, in the blind window, and never appeared in the
#: measurement: the record carried 20 prefixes and `sessionlib/` was not among them.
#: After ADR-0118 moved essentially all of the harness there, that meant a diff touching
#: ONLY harness code classified as gate-neutral and skipped the entire gate — the most
#: dangerous possible thing to leave ungated, and invisible because the record looked
#: well-formed and confident. It shows up as measured now only because one WI-0307 test
#: happens to copy the directory at runtime; a prefix that depends on an incidental read
#: in an unrelated test is not a guarantee, so it is declared rather than relied upon
#: ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).
#:
#: `interpreter.py` IS THE SAME REASON AS `sessionlib/`, one file further out (WI-0328).
#: It is imported by `session.py` before `sessionlib`, by `curate/gate_inputs.py` at THIS
#: module's own import, and by `curate/run_suite.py` — every one of those reads lands in
#: the blind window before the audit hook exists. Undeclared, a diff touching only the
#: module that decides WHICH INTERPRETER THE GATE RUNS ON would classify gate-neutral and
#: skip the gate entirely. That is the same "most dangerous possible thing to leave
#: ungated" as above, pointed at the one file whose whole job is to make the gate's
#: verdict reproducible.
#: `adr/` IS THE THIRD REASON, and it is the `wi-check` story again with a new reader
#: (WI-0342). `curate/check_citations.py` sweeps the two store directories AND `adr/` for
#: `file.py:NNN` citations whose line no longer exists. The store half was already
#: declared above; `adr/` was not, and the suite does not read it — so a land whose diff
#: touched only ADRs classified gate-neutral and skipped the entire gate, including the
#: one check that exists to read ADRs. That is not hypothetical for this class: the 119
#: dead citations WI-0342 was filed for included 18 in `adr/`, and adding the checker
#: without adding the prefix would have left it running on every land except the ones
#: carrying the defect it detects.
#:
#: NONE OF THESE IS THE CHILD-PROCESS BLIND SPOT, and WI-0337 was filed because the
#: absence of any note saying so read as coverage. These four were added for two other
#: reasons — a gate command the probe does not run, and a read in the import-time blind
#: window before the hook exists — and neither of them is "the hook does not follow a
#: `fork` + `exec`". That third gap was real, undeclared here, and larger than both: 67
#: of 112 test modules spawn subprocesses. It is now MEASURED rather than declared, in
#: `_probe` via `curate/childaudit/`, which is the right answer for a gap this size —
#: a declaration is a person asserting a set, and the set here was "whatever 67 modules
#: happen to open", which nobody can assert.
#:
#: `sessionlib/` and `interpreter.py` STAY DECLARED even though the child measurement
#: now sees them (a child's `import session` reads both from disk, since its `sys.modules`
#: starts empty). The reason they were declared has not changed — the PARENT still
#: reads them in its own blind window — and a prefix that holds only because some child
#: happened to import the harness is the same incidental-read argument the `sessionlib/`
#: note above already rejects once.
NON_SUITE_READ_PREFIXES = ("work-items/", "ops-items/", "sessionlib/", "interpreter.py",
                           "adr/")


#: The read-classification rule, and the ignore list it applies, now live in
#: `curate/childaudit/poga_child_audit.py` — because the SAME rule has to run in the
#: probe and in every child it measures, and two copies of it would mean one
#: measurement classifying its own halves differently
#: ([P16](../principles/master.md#p16--avoid-duplication)).
_rel = poga_child_audit.rel
_IGNORED_PREFIXES = poga_child_audit.IGNORED_PREFIXES

#: Floors the DERIVE refuses to write below. They answer one question only — *is this a
#: measurement at all?* — and they exist because nothing used to ask it: a probe that
#: discovered no tests, or whose hook never fired, produced a perfectly well-formed
#: record with a handful of prefixes in it, and the land then read that record as an
#: authoritative statement that almost everything is gate-neutral. The failure was
#: silent and it failed permissive, which is the pair this whole module is built to
#: avoid.
#:
#: They are floors, NOT targets, and they are set far below any real run on purpose
#: (the live measurement is ~3500 tests and ~25 measured prefixes). A floor tuned close
#: to the real number becomes a second thing to maintain and starts refusing honest
#: measurements; a floor this low can only ever catch a probe that did not run.
#:
#: `SENTINEL_PREFIXES` is the non-arbitrary half and carries most of the weight.
#: `unittest discover -s tests` cannot complete without reading `tests/`, so a
#: measurement that does not contain it did not observe the suite it claims to have
#: measured, whatever its counts say. It is checked against the MEASURED prefixes, never
#: the declared ones — a declaration cannot vouch for a hook that never fired.
MIN_TESTS = 100
MIN_MEASURED_PREFIXES = 8
SENTINEL_PREFIXES = ("tests/",)


def gate_commands() -> list:
    """The gate's configured command list — read from the authority, never from here.

    WI-0347. The record is a claim about `session.config.json`'s `gate` list, so the
    list has to come from it ([`derive-a-checks-subjects-from-the-authority`]). A member
    whose gate holds commands this repo has never seen gets them measured with no edit
    to this file, and a command added between two derives is simply absent from the
    record — which the land reads as "runs unconditionally", the safe answer."""
    try:
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cmds = cfg.get("gate")
    return list(cmds) if isinstance(cmds, list) else []


def measure_command(cmd, tracked: set) -> dict:
    """Run ONE gate command under the child-audit shim and record what it read.

    WI-0347, and the whole item in one function. The old probe measured
    `unittest discover -s tests` and the land applied that measurement to all seven
    configured commands — none of which was the command measured. The other six were
    covered by an accident (the suite happens to read most of what they read) patched
    twice by hand when the accident failed (`work-items/` in WI-0307, `adr/` in
    WI-0342). Measuring each command as itself is what retires the class instead of the
    two instances.

    THE MECHANISM IS WI-0337'S, NOT A NEW ONE. `poga_child_audit.child_env` puts the
    shim on the child's `PYTHONPATH`; the child and every descendant arm themselves and
    write what they read into `box`. That is the same channel the suite's 67
    subprocess-spawning modules already report through, and its canary already runs in
    `_probe`. Nothing here installs an audit hook in THIS process, so it is safe to call
    from `derive()` and it cannot disturb the serial probe's own measurement.

    `POGA_GATE=1` because that is the environment the gate runs these commands in
    (ADR-0119 D4), and a check that holds back machine-scoped probes under the gate
    reads different files than one that does not. Measuring it in any other environment
    would measure a command the gate does not run — which is the defect this function
    exists to close, wearing a different hat
    ([`verify-in-the-created-configuration`]).

    FAILURE IS `measured: False`, NEVER AN EMPTY SET. A command whose process exited
    nonzero, or whose shim never armed, or one of whose children died mid-write, has
    not been measured — and an empty `read_prefixes` is indistinguishable from "reads
    nothing", which the classifier would read as "always skippable". The reason is
    carried so a reader is told which it is."""
    key = session._gate_command_key(cmd)
    argv = key.split()
    if argv and argv[0] == "python3":
        argv[0] = sys.executable
    box = pathlib.Path(tempfile.mkdtemp(prefix="poga-gate-cmd-"))
    try:
        env = poga_child_audit.child_env(os.environ, box, ROOT)
        env["POGA_GATE"] = "1"
        try:
            r = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=1800)
        except Exception as e:
            return {"cmd": key, "measured": False, "read_prefixes": [],
                    "unmeasured_reason": "could not launch: %s" % e}
        paths, records, torn = poga_child_audit.collect(box)
    finally:
        shutil.rmtree(box, ignore_errors=True)
    entry = {"cmd": key, "exit_code": r.returncode, "reports": len(records),
             "torn_reports": torn}
    if r.returncode != 0:
        # A red check measures a SHORTER path than a green one — it stops at the first
        # failure. Recording that as this command's input set would license skips on
        # everything it never got to read.
        # The tail alone named one of the 2026-09-29 night's two failures; unittest's
        # `FAIL: <id>` / `ERROR: <id>` headers sit far above it, so keep them too.
        headers = [ln for ln in f"{r.stdout or ''}\n{r.stderr or ''}".splitlines()
                   if ln.startswith(("FAIL: ", "ERROR: "))]
        return dict(entry, measured=False, read_prefixes=[],
                    unmeasured_reason="exited %d — a failing check stops early, so what "
                                      "it read is not its input set" % r.returncode,
                    stderr=evidence.clip(r.stderr or r.stdout or "", 400,
                                         keep="tail"),
                    failed_tests=headers[:25])
    if not records:
        return dict(entry, measured=False, read_prefixes=[],
                    unmeasured_reason="the shim never reported — this command was "
                                      "measured by nothing")
    if torn:
        return dict(entry, measured=False, read_prefixes=[],
                    unmeasured_reason="%d torn report(s) — a child died mid-write and "
                                      "its reads are missing" % torn)
    prefixes = measured_prefixes(paths, tracked)
    if not prefixes:
        return dict(entry, measured=False, read_prefixes=[],
                    unmeasured_reason="reported, but read nothing inside the repo — a "
                                      "command that reads no tracked path cannot be "
                                      "told apart from one that was not measured")
    return dict(entry, measured=True, read_prefixes=prefixes, unmeasured_reason=None)


def measured_prefixes(paths, tracked: set) -> list:
    """The tracked prefixes a set of read paths implies.

    Two rules, and the second was found by WI-0337's own test failing.

    INTERSECT WITH WHAT GIT TRACKS. A lane's diff can only ever name tracked paths, so
    this throws away any remaining phantom without discarding a single real read — and
    it is a second, independent guard against the resolve-a-relative-path-into-the-repo
    class of error rather than trusting one fix to have caught them all. The child
    measurement makes it load-bearing rather than belt-and-braces: children run in
    tmpdirs with fixture directories named `member`, `pin`, `hostile-bin`, and a
    relative open from a child whose cwd IS the repo resolves every one of them into a
    repo-relative path that does not exist.

    PROMOTE A BARE DIRECTORY READ TO ITS PREFIX. `top_prefix` renders `tests/x.py` as
    `tests/` and a bare `tests` — which is what an `os.scandir` of a directory produces
    — as `tests`. The land only ever classifies FILE paths out of a diff, so those never
    equal a bare name and every directory-only read was silently worth nothing. That was
    tolerable while the measurement was one process reading source files. It is not
    tolerable now: a child's `import sessionlib.land` reads
    `sessionlib/__pycache__/land.cpython-39.pyc`, which `IGNORED_PREFIXES` drops, so the
    ONLY surviving evidence that the child read the harness at all is the scandir of
    `sessionlib` — and dropping that turns a real read into no read, in the permissive
    direction, on a warm cache. The promotion is conservative by construction: it can
    only ever ADD a prefix, and only one git already tracks as a directory.
    """
    out = set()
    for p in paths:
        top = top_prefix(p)
        if top in tracked:
            out.add(top)
        elif "/" not in top and top + "/" in tracked:
            out.add(top + "/")
    return sorted(out)


def top_prefix(rel: str) -> str:
    """The grain the record is written at — imported, never re-implemented.

    The deriver writes prefixes and the land compares against them, so the two must
    agree exactly; a second copy of this three-line rule is the drift
    ([P16](../principles/master.md#p16--avoid-duplication)) that would make the
    record mean one thing when written and another when read. `session.py` owns it
    because that is the file every member ships.

    Bound at module import, which in the probe happens strictly BEFORE the audit hook
    is installed — so importing `session` does not appear in its own measurement.
    """
    return session._gate_top_prefix(rel)


#: The file the canary child opens: THIS FILE. Derived rather than named, because the
#: canary must never be able to fail for the boring reason — a checkout that happens not
#: to carry the file someone picked. The deriver's own source is the one path whose
#: existence is implied by the probe running at all, so "the canary did not see its read"
#: can only ever mean the channel is broken. (Named constants were tried first and the
#: fixture repo in `tests/test_gate_input_deriver_is_serial.py`, which copies four
#: harness paths into a tmp repo, does not carry `session.config.json`.)
CANARY_REL = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()

#: Read once absolutely, then again relative after a `chdir` into the repo. The second
#: read is the one that matters: the suite `chdir`s into tmpdirs constantly, and the
#: original in-process measurement was destroyed once already by resolving relative
#: paths against a captured cwd instead of the live one. The canary proves the CHILD
#: does not repeat that, rather than trusting that the shared rule is shared.
_CANARY_SRC = (
    "import os, sys\n"
    "open(sys.argv[1]).close()\n"
    "os.chdir(sys.argv[2])\n"
    "open(sys.argv[3]).close()\n"
)


def _canary(root: pathlib.Path) -> "tuple[bool, dict]":
    """Prove the child channel works BEFORE trusting a measurement taken through it.

    `ship-the-detector-with-the-capability`, read the way that habit asks to be read: a
    child-audit that silently fails to arm does not merely stop measuring children, it
    certifies the parent's half as the whole input set — which is exactly the state
    WI-0337 was filed about, now wearing a receipt. So the probe spawns one child whose
    reads it knows in advance, and refuses the entire derive if they do not come back.

    Runs in its OWN collection directory, so the canary's reads never enter the
    measurement it is vouching for.
    """
    box = pathlib.Path(tempfile.mkdtemp(prefix="poga-gate-canary-"))
    try:
        env = poga_child_audit.child_env(os.environ, box, root)
        r = subprocess.run(
            [sys.executable, "-c", _CANARY_SRC,
             str(root / CANARY_REL), str(root), CANARY_REL],
            cwd=str(box), env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=120)
        paths, records, torn = poga_child_audit.collect(box)
        detail = {
            "file": CANARY_REL,
            "exit_code": r.returncode,
            "reports": len(records),
            "torn_reports": torn,
            "saw": sorted(paths),
        }
        ok = (r.returncode == 0 and len(records) == 1 and torn == 0
              and paths == {CANARY_REL})
        if not ok:
            detail["stderr"] = evidence.clip(r.stderr or "", 800, keep="tail")
        detail["ok"] = ok
        return ok, detail
    finally:
        shutil.rmtree(box, ignore_errors=True)


def _spawn_note(args: tuple, root: pathlib.Path) -> dict:
    """Classify one `subprocess.Popen` audit event: will this child report back?

    Called from inside the audit hook, so it does string work and nothing else — a
    `stat` here would be a read the measurement attributes to whatever the suite was
    doing at the time.

    The `why` field is None for a child the shim should reach and a short reason
    otherwise. Those reasons are DERIVED from the event, never guessed: `env` is the
    mapping the caller actually passed (None means "inherit ours"), so "this spawn
    stripped the shim" is a fact about the call rather than a suspicion about it.

    `read` is the executable as a repo-relative path when the child is executing a file
    from this repo. RESOLVED AGAINST THE SPAWN'S OWN `cwd`, not the live one — a test
    that runs `./poga` with `cwd=ROOT` from inside a tmpdir is the normal case here, and
    joining that relative path to the live cwd is precisely the phantom-path mistake
    `poga_child_audit.rel` carries a paragraph about.
    """
    exe, argv, cwd, env = (list(args) + [None] * 4)[:4]

    def text(v):
        try:
            return os.fsdecode(v) if isinstance(v, (bytes, bytearray)) else str(v)
        except Exception:
            return ""

    words = ([text(argv)] if isinstance(argv, (str, bytes))
             else [text(a) for a in (argv or [])])
    exe_s = text(exe) if exe is not None else (words[0] if words else "")
    cmd = " ".join([exe_s] + words[1:])[:200]

    read = None
    if exe_s:
        p = pathlib.Path(exe_s)
        if not p.is_absolute() and cwd is not None:
            p = pathlib.Path(text(cwd)) / p
        if p.is_absolute():
            read = poga_child_audit.rel(p, root)

    note = {"cmd": cmd, "read": read}
    if "python" not in os.path.basename(exe_s):
        return dict(note, why="non-python")
    # Leading interpreter flags only — everything from the first non-flag word on
    # belongs to the child's own program. `-I` implies `-E`, and `-S` skips `site`,
    # which is the only place the shim can be imported from.
    for w in words[1:]:
        if not w.startswith("-"):
            break
        if not w.startswith("--") and set(w[1:]) & set("IES"):
            return dict(note, why="isolated-interpreter")
    if env is not None:
        try:
            path = env.get("PYTHONPATH") if hasattr(env, "get") else None
        except Exception:
            path = None
        if not poga_child_audit.marker_on_path(path):
            return dict(note, why="env-without-shim")
    return dict(note, why=None)


def _probe() -> int:
    """Run the suite in THIS process under an audit hook and write the raw reads.

    Separate process from `derive()` on purpose: an audit hook can never be removed
    once installed, and a suite that calls `sys.exit` or dies must not take the
    deriver's own bookkeeping with it.

    WI-0337 — THE HOOK IS ARMED IN TWO PLACES, not one. `sys.addaudithook` binds this
    interpreter only, so the suite's 67 subprocess-spawning modules were reading repo
    files that nothing recorded. `curate/childaudit/` goes on the children's
    `PYTHONPATH`; each descendant measures itself and writes what it read into
    `child_dir`, which is collected below and unioned in. The parent additionally
    watches the `subprocess.Popen` event so the record can say how many children it
    expected to hear from, how many it heard from, and which spawns it knew it could
    not reach.
    """
    root = ROOT
    out = pathlib.Path(os.environ["POGA_GATE_INPUTS_OUT"])
    paths: set[str] = set()
    measured_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    interpreter = sys.executable
    version = list(sys.version_info[:3])
    environment = {"POGA_GATE": os.environ.get("POGA_GATE")}

    # The repo's real top-level entries, read BEFORE the hook is armed. Every prefix
    # in the record must be one of these. A lane's diff can only ever name tracked
    # paths, so intersecting with what git actually tracks throws away any remaining
    # phantom without discarding a single real read — and it is a second, independent
    # guard against the resolve-a-relative-path-into-the-repo class of error rather
    # than trusting one fix to have caught them all.
    tracked = {
        top_prefix(line.strip())
        for line in subprocess.run(["git", "ls-files"], cwd=root,
                                   capture_output=True, text=True).stdout.splitlines()
        if line.strip()
    }

    # The child channel, armed before the hook so the canary's own spawn is not
    # counted as one of the suite's. A failure here is fatal and says so: a derive that
    # cannot see children must not write a record that a land will read as complete.
    canary_ok, canary = _canary(root)
    if not canary_ok:
        sys.stderr.write(
            "derive: the child-process audit did not come back.\n"
            "The suite spawns subprocesses and their reads would go unmeasured, which\n"
            "makes MORE diffs classify gate-neutral — the failure is a skipped suite,\n"
            "so nothing is written.\n" + json.dumps(canary, indent=2) + "\n")
        return 1
    # Outside the repo by construction (`mkdtemp` honours TMPDIR), so the children's
    # own bookkeeping never resolves into the measurement it is feeding.
    child_dir = pathlib.Path(tempfile.mkdtemp(prefix="poga-gate-children-"))
    # `os.environ`, not a private copy: `subprocess` hands the live environment to any
    # child spawned without an explicit `env=`, which is nearly all of them. This
    # process's OWN `sys.path` is already built, so arming the children cannot arm the
    # probe — the probe's hook is the one installed below.
    os.environ.update(poga_child_audit.child_env(os.environ, child_dir, root))
    spawns: list = []

    def hook(event: str, args: tuple) -> None:
        # The read events: `open` covers builtins.open, io.open, os.open and pathlib;
        # `os.scandir` and `os.listdir` cover the glob/iterdir traversals that make a
        # whole directory an input even when no individual file is opened. The rule
        # itself is `poga_child_audit.read_event`, shared with the children so one
        # measurement cannot classify its own halves by two different rules.
        if event == "subprocess.Popen":
            try:
                note = _spawn_note(args, root)
            except Exception:
                # A hook that raises kills the call it was watching. An unparseable
                # spawn event costs one accounting entry; it must not cost the suite.
                return
            spawns.append(note)
            # The exec itself IS a read of the file, and for a non-Python child it is
            # the only read we will ever get — `./poga`, `drills/*.sh`, a deploy
            # installer. Cheap, exact, and it covers the case the shim cannot reach.
            if note["read"]:
                paths.add(note["read"])
            return
        rel = poga_child_audit.read_event(event, args, root)
        if rel:
            paths.add(rel)

    sys.addaudithook(hook)

    import unittest
    loader = unittest.TestLoader()
    # No `top_level_dir` — `unittest discover -s tests` defaults it to the start dir,
    # and `tests/` carries no `__init__.py`. Passing ROOT here made the start dir
    # "not importable" and the probe measured nothing at all.
    suite = loader.discover(start_dir=str(root / "tests"))
    count = suite.countTestCases()
    executed = []
    class MeasuredResult(unittest.TextTestResult):
        def startTest(self, test):
            executed.append(test.id())
            super().startTest(test)
    result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"),
                                     resultclass=MeasuredResult).run(suite)
    ids = sorted(executed)

    # ── what the children read ────────────────────────────────────────────────
    child_paths, child_records, torn = poga_child_audit.collect(child_dir)
    shutil.rmtree(child_dir, ignore_errors=True)
    expected = [s for s in spawns if s["why"] is None]
    unaudited = sorted({"%s: %s" % (s["why"], s["cmd"]) for s in spawns if s["why"]})
    silent = accounting_failure(spawns, child_records)
    if silent:
        sys.stderr.write("derive: " + silent + "\nRefusing to write.\n")
        return 1
    parent_only = set(measured_prefixes(paths, tracked))
    paths |= child_paths
    prefixes = measured_prefixes(paths, tracked)
    child_audit = {
        "ok": True,
        "canary": canary,
        "spawns": len(spawns),
        "expected_reports": len(expected),
        "reports": len(child_records),
        "torn_reports": torn,
        # The answer to "did this matter?", carried in the record rather than argued
        # about: prefixes no in-process read ever produced. An empty list is a real
        # finding too — it says the children read nothing the parent did not.
        "child_only_prefixes": sorted(set(prefixes) - set(parent_only)),
        "unaudited": unaudited[:80],
        "unaudited_total": len(unaudited),
    }
    out.write_text(json.dumps({
        "child_audit": child_audit,
        "verdict": {"exit_code": 0 if result.wasSuccessful() else 1,
                    "tests_run": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skips": len(result.skipped),
                    **failing_ids(result),
                    "test_ids": ids,
                    "test_ids_sha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
                    "interpreter": interpreter, "version": version,
                    "environment": environment, "measured_tree": measured_tree},
        "tests": count,
        "gate_skips": len(result.skipped),
        "gate_skip_ids": sorted(test.id() for test, _ in result.skipped),
        "ok": result.wasSuccessful() and result.testsRun == count and not loader.errors,
        "paths": sorted(p for p in paths if measured_prefixes([p], tracked)),
        "prefixes": prefixes,
        # What the measurement saw and could not place. Mostly the phantom class the
        # tracked-intersection exists for, and worth keeping visible: the child
        # measurement multiplies it, because children run in tmpdirs whose fixture
        # directory names resolve into perfectly plausible repo-relative paths.
        "dropped_untracked": sorted({top_prefix(p) for p in paths}
                                    - set(prefixes)
                                    - {p.rstrip("/") for p in prefixes})[:80],
    }))
    return 0


def accounting_failure(spawns: list, child_records: list) -> "str | None":
    """Did the shim arm at all? The only spawn-vs-report test that is actually valid.

    An EQUALITY between spawns and reports is not one, and reaching for it is the
    obvious mistake here. A grandchild reports without the parent ever seeing its spawn
    — `PYTHONPATH` is inherited, the audit event is not — so reports legitimately exceed
    expectations, and a check that fired on the difference would be noise on every run.
    Nor can a shortfall be read as loss: a child killed by a test on purpose, or one
    that calls `os._exit`, is a normal thing for this suite to do.

    What cannot be explained away is SILENCE. If the probe watched children start and
    heard back from none of them, the shim did not arm — which is the WI-0337 defect
    exactly, and from outside it is indistinguishable from a healthy measurement.

    Separate from `_probe` so a test can reach it. The condition lives inside a
    four-minute full-suite run and would otherwise be pinned by nothing, which is the
    same reason `refusal` was extracted.
    """
    if any(s["why"] is None for s in spawns) and not child_records:
        return ("%d spawn(s) should have reported child reads and none did — every "
                "subprocess the suite ran was measured by nothing"
                % sum(1 for s in spawns if s["why"] is None))
    return None


def refusal(data: dict) -> "str | None":
    """Why this measurement must not become a record, or None if it may.

    THE FAILURE THIS EXISTS FOR IS A WELL-FORMED RECORD OVER NOTHING. Every guard in
    this module used to sit on the READING side — `load` and `_gate_input_prefixes`
    reject an absent, unparseable, wrong-schema or zero-test record — and all of them
    take the record's own word for it. A probe that discovered no tests, or whose hook
    never armed, wrote a record with a schema, a timestamp, a commit and a short list
    of prefixes, and every reader accepted it: near-empty passes every "is this
    usable?" test ever written here, and its consequence is that nearly every diff
    classifies gate-neutral. So the refusal belongs at the WRITE, where the evidence
    still exists, and it is loud — `derive` raises and no record is written, leaving the
    previous (honestly-derived, possibly stale) one in place, which a stale record's own
    warning then disqualifies from licensing any skip.

    Separate from `derive` so it can be exercised against a synthetic measurement. The
    alternative — testing it only through a real 4-minute derive — is why the condition
    had no test before.
    """
    measured = set(data.get("prefixes") or [])
    tests = data.get("tests")
    child = data.get("child_audit") or {}
    if not child.get("ok"):
        return "the child-process audit did not complete"
    if not isinstance(tests, int) or tests < MIN_TESTS:
        return ("measured over %r tests, below the floor of %d — the probe did not run "
                "the suite" % (tests, MIN_TESTS))
    missing = [p for p in SENTINEL_PREFIXES if p not in measured]
    if missing:
        return ("the suite's own directory is missing from the MEASURED prefixes (%s) — "
                "the audit hook did not observe the run it claims to have measured"
                % ", ".join(missing))
    if len(measured) < MIN_MEASURED_PREFIXES:
        return ("only %d measured prefix(es), below the floor of %d — a near-empty "
                "measurement would classify nearly every diff gate-neutral"
                % (len(measured), MIN_MEASURED_PREFIXES))
    return None


def derive() -> dict:
    """Measure serially, recording a content hash of tests/** as suite_tree.

    A content hash measures the actual checkout, including candidate tests not yet
    committed, whereas HEAD:tests could certify different bytes. Ignore bytecode
    caches, which discovery generates rather than consumes as suite source.
    """
    measured_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    clean_before = subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT).returncode == 0
    suite_tree = session._gate_suite_tree(ROOT)
    raw = ROOT / ".gate-inputs.raw"
    raw.unlink(missing_ok=True)
    env = dict(os.environ)
    env["POGA_GATE_INPUTS_OUT"] = str(raw)
    env["POGA_GATE_INPUTS_PROBE"] = "1"
    env["POGA_GATE"] = "1"
    limit = probe_timeout()
    try:
        r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve())],
                           cwd=ROOT, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=limit)
    except subprocess.TimeoutExpired:
        raw.unlink(missing_ok=True)
        raise SystemExit(
            f"derive: the serial probe did not finish within {limit}s and was killed; no "
            f"record written. The probe runs the whole suite one test at a time, so a "
            f"throttled process (a Background LaunchDaemon) needs far longer than an "
            f"interactive shell. Raise {PROBE_TIMEOUT_ENV} for this caller.")
    if r.returncode or not raw.exists():
        raise SystemExit("derive: the probe wrote no measurement.\n"
                         + evidence.clip(r.stderr or r.stdout, 2000, keep="tail"))
    data = json.loads(raw.read_text())
    raw.unlink()
    bad = refusal(data)
    if bad:
        raise SystemExit("derive: refusing to write a record — " + bad + ".\n"
                         "A record the land can read is a claim that the input set is "
                         "COMPLETE, and an incomplete one licenses skips, not gates.")
    if session._gate_suite_tree(ROOT) != suite_tree:
        raise SystemExit("derive: tests changed during measurement; retry the derive")
    # ── each gate command's OWN input set (WI-0347) ───────────────────────────────
    # AFTER the probe, never before: these spawn processes that write into the repo's
    # own directories (`wi-check` touches the stores), and doing that while the probe is
    # measuring would put this function's footprint into the suite's measurement.
    tracked = {top_prefix(line.strip())
               for line in subprocess.run(["git", "ls-files"], cwd=ROOT,
                                          capture_output=True, text=True).stdout.splitlines()
               if line.strip()}
    serial = set(data["prefixes"])
    commands = []
    for cmd in gate_commands():
        entry = measure_command(cmd, tracked)
        if session._gate_is_suite_command(cmd):
            # THE ONE UNION, and it is insurance rather than a correction. Measured
            # 2026-09-11 on this repo: the shim's measurement of `curate/run_suite.py`
            # (67.2s, 814 reports, 0 torn) produced the same 38 prefixes as the serial
            # in-process probe (~300s), exactly — no prefix in either direction. So this
            # adds nothing today. It is here because the two mechanisms could diverge on
            # a future suite, and the direction of a divergence matters: a prefix the
            # serial probe saw and the sharded run did not would otherwise become a
            # licensed skip. Unioning can only ever make this command run more often.
            entry["serial_probe_only_prefixes"] = sorted(serial - set(entry["read_prefixes"]))
            if entry["measured"]:
                entry["read_prefixes"] = sorted(set(entry["read_prefixes"]) | serial)
        # The declared set goes on EVERY command, not on one. Its two surviving members
        # (`sessionlib/`, `interpreter.py`) are declared because the PARENT reads them in
        # the import-time blind window before any hook exists — a window every measured
        # command has — so attaching them to one command would leave the others able to
        # skip on a diff that changes the harness they run on.
        entry["declared_prefixes"] = sorted(NON_SUITE_READ_PREFIXES)
        if entry["measured"]:
            entry["read_prefixes"] = sorted(set(entry["read_prefixes"])
                                            | set(NON_SUITE_READ_PREFIXES))
        commands.append(entry)

    clean_after = subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=ROOT).returncode == 0
    final_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    data["verdict"]["tree_clean"] = (clean_before and clean_after
                                      and measured_tree == final_tree == data["verdict"]["measured_tree"])
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    return {
        "schema": SCHEMA,
        "derived_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "derived_at_commit": sha,
        "suite": " ".join(GATE_SUITE()),
        "suite_tree": suite_tree,
        "verdict": data["verdict"],
        "read_paths": data["paths"],
        "tests": data["tests"],
        "gate_skips": data["gate_skips"],
        "gate_skip_ids": data["gate_skip_ids"],
        "suite_ok": data["ok"],
        # The receipt for the half of the measurement that happens outside this
        # process. A reader that does not find it is reading a record derived by a
        # probe that could not see subprocess reads, and gates.
        "child_audit": data["child_audit"],
        # WI-0347. One entry per configured gate command, each carrying the set IT was
        # measured to read. This is what the land classifies against now; a command
        # absent from here, or present with `measured: false`, runs unconditionally.
        "commands": commands,
        # Measured ∪ declared ∪ every command's own set. Still the whole-gate answer —
        # a diff touching nothing in here cannot change what ANY command concludes, so
        # the all-seven skip that schema 2 licensed survives unchanged as the case where
        # every per-command set is untouched. Kept as one flat list because the floors
        # in `refusal()` and every "is this a measurement at all" reader ask about the
        # measurement as a whole, not about one command.
        "read_prefixes": sorted(set(data["prefixes"]) | set(NON_SUITE_READ_PREFIXES)
                                | {p for c in commands for p in c["read_prefixes"]}),
        "declared_prefixes": sorted(NON_SUITE_READ_PREFIXES),
        "read_paths_sampled": data["paths"][:400],
    }


def load() -> dict | None:
    """The current record, or None if there is not a usable one.

    None means "gate" to every caller. Absent, unparseable, wrong-schema, no
    prefixes and zero-tests all collapse to None deliberately: operationally they
    mean the same thing — nobody has measured this — and separating them here would
    invite a caller to decide one of them was good enough.
    """
    try:
        d = json.loads(RECORD.read_text())
    except Exception:
        return None
    if d.get("suite_ok") is False:
        return None
    if d.get("schema") != SCHEMA or not d.get("read_prefixes"):
        return None
    if not isinstance(d.get("tests"), int) or d["tests"] <= 0:
        return None
    # WI-0337: no child-audit receipt, no classification. Structural, not a threshold —
    # a record without this block was produced by a probe blind to every subprocess the
    # suite spawned, so its input set is known-incomplete and an incomplete set is what
    # licenses a skip. Kept in step with `_gate_input_prefixes` in sessionlib/land.py,
    # which is the copy that actually runs at land time and cannot import this module.
    child = d.get("child_audit")
    if not isinstance(child, dict) or child.get("ok") is not True:
        return None
    return d


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure which repo paths the land gate's suite actually reads.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derive", action="store_true",
                    help="run the suite under an audit hook and write the record")
    ap.add_argument("--status", action="store_true",
                    help="one line on what the current record holds")
    args = ap.parse_args()
    if args.derive:
        rec = derive()
        RECORD.write_text(json.dumps(rec, indent=2) + "\n")
        print(f"derived: {len(rec['read_prefixes'])} read prefix(es) from "
              f"{rec['tests']} tests at {rec['derived_at_commit'][:12]} "
              f"(suite {'green' if rec['suite_ok'] else 'RED'})")
        for p in rec["read_prefixes"]:
            print(f"  reads  {p}")
        # WI-0347. Printed per command, because "which commands did this derive
        # actually cover?" is the question a nightly's operator has to be able to
        # answer, and an unmeasured command is a real outcome rather than an error —
        # it simply runs on every land until the next derive measures it.
        for entry in rec["commands"]:
            if entry["measured"]:
                print(f"  cmd    {entry['cmd']} — {len(entry['read_prefixes'])} prefix(es)")
            else:
                print(f"  cmd    {entry['cmd']} — UNMEASURED, runs every land "
                      f"({entry['unmeasured_reason']})")
        return 0 if rec["suite_ok"] else 1
    rec = load()
    if not rec:
        # WI-0333, the sibling of the defect the land carried
        # ([`retire-the-class-not-the-instance`]). `load()` folds a RED suite into None
        # on purpose — for ITS question, "may a diff skip the gate", a red measurement
        # is exactly as unusable as no measurement. But this is the verb a person runs
        # to ask a DIFFERENT question, and answering "no usable record" to "is the trunk
        # broken?" throws away the one measurement that answers it. Read the raw record
        # and say the verdict out loud before falling through to the generic line.
        try:
            raw = json.loads(RECORD.read_text())
        except Exception:
            raw = {}
        if isinstance(raw, dict) and raw.get("suite_ok") is False:
            print(f"gate-inputs: THE SUITE WAS RED when this record was derived, at "
                  f"{str(raw.get('derived_at_commit', ''))[:12]} "
                  f"({str(raw.get('derived_at', ''))[:10]}) over "
                  f"{raw.get('tests', '?')} tests. The trunk needs fixing before "
                  f"anything else here matters; every land gates meanwhile.")
            return 1
        print("gate-inputs: NO USABLE RECORD — every land gates "
              "(python3 curate/gate_inputs.py --derive)")
        return 1
    measured = [c for c in rec.get("commands") or [] if c.get("measured")]
    configured = gate_commands()
    print(f"gate-inputs: {len(rec['read_prefixes'])} read prefix(es), measured over "
          f"{rec['tests']} tests at {rec['derived_at_commit'][:12]} "
          f"({rec['derived_at'][:10]}); {len(measured)}/{len(configured)} gate "
          f"command(s) carry their own input set")
    return 0


if __name__ == "__main__":
    if os.environ.get("POGA_GATE_INPUTS_PROBE"):
        raise SystemExit(_probe())
    raise SystemExit(main())
