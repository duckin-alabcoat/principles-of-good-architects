#!/usr/bin/env python3
"""OPS-0009's runner — derive the gate-input record nightly, land it, record the run.

WHY THIS EXISTS (WI-0407). `curate/gate_inputs.py --derive` measures the land gate's
INPUT SET by running the whole suite serially under an audit hook. It is the ONLY serial
full-suite run the federation performs: the land gate is sharded, so it structurally
cannot see order-dependent contamination between tests. The record the derive produces is
therefore the one thing standing between the fleet and a class of defect nothing else
looks for.

Until this file existed, that run happened when a person remembered. OPS-0009 made the
obligation visible and dated and stopped there, which is the WI-0087 shape half-closed:
the obligation was legible and still nothing RAN it. On 2026-09-19 the record was a day
stale, the obligation was overdue, and nothing noticed.

WHICH MACHINE. DEVBOX, and that is not an implementation detail. The federation's other
three LaunchAgents live on the Runner because they need a logged-in Aqua session —
`com.federation.adopt-runner` spawns `claude -p` and reads the login-keychain OAuth token.
This one spawns no `claude`, reads no keychain, and touches no member repo. It is dev work
on the dev trunk, so it belongs on the machine holding the dev trunk. `refuse_wrong_tree` below
is what keeps it from drifting to the runner host.

THE FOUR OUTCOMES, because a runner with two is what produced today's defect.

  GREEN    — suite green, record written. Land the record-only diff, then record the run
             `pass`. Landing is the point: a runner that derives and stops leaves the
             ledger exactly one run behind reality, which is today's defect with a cron
             entry attached (WI-0407's negative control, in those words).
  RED      — the suite failed. `gate_inputs.py` still WRITES a record in this case, with
             `suite_ok: false`, and `load()` folds that to None — so landing it would
             replace a good record with one that licenses no gate skips at all. We do not
             land it. We record the run `fail`, because it ran and produced a verdict.
  REFUSED  — `tests/` changed under the derive while it ran, so it refused rather than
             measure a moving tree. NOTHING is recorded in the ops ledger, deliberately:
             `ops ran` rolls `due` forward from the cadence whatever the result, so a
             refusal recorded as a run would mark the obligation met and drop it off the
             startup view until tomorrow. Leaving it unrecorded is what makes OPS-0009 go
             overdue, and overdue ops rows render FIRST in the startup work view. The
             refusal must be "visible as a skipped run, not a silent one" (WI-0407) — the
             overdue row is that visibility, and the comms note says why.
  ERROR    — the probe died, a floor refusal tripped, the land failed, or the run timed
             out. Same treatment as REFUSED: no ledger write, a comms note, the
             obligation stays overdue.

THE SUITE INSIDE A PUBLIC CUT (WI-0495). A green derive is not yet a green night. The
runner then builds a scratch public cut from the lane and runs the suite inside it
(`run_cut_suite`; the showcase nightly, the community edition on alternate nights). A cut
whose suite fails, or that cannot be built, turns the night RED with the failing tests
named, the same as a failing gate command: the record is not landed and the run is
recorded `fail`. It exists because twice a test reading a trunk-only path was green on
every land and red only in the cut's CI, on release day.

WHAT MAKES A BAD NIGHT VISIBLE. Two surfaces, because the ops ledger alone cannot carry
it. (1) The obligation goes overdue and the next session start prints it. (2) A comms note
at `comms/gate-inputs-runner-blocked.md`, ADR-0046's channel to operator, written with a
STABLE filename so a run of bad nights leaves one current note rather than a pile, and
DELETED on the next night that lands. That is `curate/adopt-runner.py`'s standing-condition
shape (its `STALE_NOTE`), copied deliberately rather than reinvented. Filesystem only, no
commit: `comms/` is tracked, so the next interactive session commits it and it rides the
ADR-0035 auto-push.

WHERE ARTIFACTS ARE WRITTEN, and why it is not obvious. This runner does its work in a
lane it creates and then destroys. Anything written relative to that lane dies with it —
the same defect as a receipt written into a detached deploy tree. Every artifact that must
survive the night (the comms note, the status file, the ops ledger write) is addressed to
ROOT, the main checkout, never to the lane.

stdlib only, by construction: launchd sources no profile and this must run on a machine
with nothing installed but the repo.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import pwd
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import interpreter  # noqa: E402

if __name__ == "__main__":
    interpreter.ensure(ROOT)

OPS_ID = "OPS-0009"
RECORD_NAME = "gate-inputs.json"
STATE_DIR = ROOT / ".session-state"
STATUS_FILE = STATE_DIR / "gate-inputs-runner.status.json"
LOCK_FILE = STATE_DIR / "gate-inputs-runner.lock"
BLOCKED_NOTE = ROOT / "comms" / "gate-inputs-runner-blocked.md"

#: A BACKSTOP, NOT A BUDGET. The derive is expected to take well over the few minutes a
#: quick estimate would budget, because it runs the suite serially
#: in-process AND then measures each of the eight gate commands separately. A timeout set
#: from the prose number would kill a healthy run every night. This sits well above the
#: deriver's own internal probe timeout so that its refusal, which is legible, is
#: what we see rather than our own kill, which is not.
DERIVE_TIMEOUT_SECONDS = 7200

#: The deriver's serial-probe ceiling for THIS caller, passed as `POGA_GATE_PROBE_TIMEOUT`.
#: The deriver's own default (1800s) is sized for an interactive shell, and this unit is a
#: `ProcessType: Background` daemon, which macOS throttles: under background policy a
#: suite of several thousand tests can need about an hour, and the 1800 default would
#: kill it. Raising it here,
#: rather than dropping the daemon to normal priority, keeps the unit polite at 02:30 and
#: needs no re-install (the plist is root-owned; the runner is not). It must stay under
#: DERIVE_TIMEOUT_SECONDS with room for the eight gate-command measurements that follow the
#: probe, or our own kill pre-empts the deriver's legible refusal.
PROBE_TIMEOUT_SECONDS = 5400

#: The land can sit in the serialized land queue (ADR-0114) for up to 90 minutes before it
#: fails open, and the gate runs after that. This bounds the whole wait.
LAND_TIMEOUT_SECONDS = 7200

#: Handles that mean "you are inside a dispatched lane" or "a human launched you from
#: there". This process is neither, and leaking one changes how the harness classifies the
#: land and the close. `curate/adopt-runner.py` strips the same set for the same reason
#: before it spawns.
INHERITED_SESSION_ENV = (
    "POGA_DISPATCH", "POGA_DISPATCH_ITEM", "POGA_DISPATCH_BRIEF", "POGA_LANE_CLOSE",
    "POGA_TURN_BUDGET", "POGA_INVOKED_FROM", "POGA_RUNTIME_ID",
    # gate_inputs.py turns into its own in-process probe if it sees either of these,
    # instead of behaving as the CLI. Inheriting one would be near-impossible to diagnose
    # from a log.
    "POGA_GATE_INPUTS_PROBE", "POGA_GATE_INPUTS_OUT",
)

GREEN, RED, REFUSED, ERROR = "green", "red", "refused", "error"

#: WI-0495. THE SUITE INSIDE A PUBLIC CUT, after the serial suite. Twice in a row a test
#: that read a trunk-only path (2026.10.4: `public-overlay/adr`) passed every lane and land
#: and failed only in the cut's own CI, on release day. So each night that the derive is
#: green, the runner builds a scratch cut from the lane (`curate/public_cut.py --out`),
#: commits it into a throwaway git repo as the public clone is, and runs the suite there
#: with `curate/run_suite.py` -- the same partition of the same tests the land gate
#: runs, sharded. A failure turns the night RED with the failing tests named, exactly as a
#: failing gate command does: not landed, the ledger records `fail`, the note names them.
#:
#: THE SHOWCASE EVERY NIGHT; THE COMMUNITY EDITION ON ALTERNATE NIGHTS (the brief: over
#: about ten minutes, the second edition runs on alternate nights). Measured 2026-10-02 on
#: devbox, by `run_cut_suite` itself from a lane, showcase: build 211 s, suite 632 s
#: (6,977 tests, sharded), 844 s in all -- about 14 minutes a cut, with other lanes
#: loading the machine (load average ~17 on 10 cores). A second run at load ~12: build
#: 150 s, suite 355 s, 505 s. So one cut is 8 to 14 minutes; both editions nightly would
#: be 17 to 28. The editions differ in identity only (the boundary and every gate class
#: are the showcase's), so the second one buys little nightly. Nights alternate by the
#: calendar date, so the schedule needs no state. Each night's own cost is written to the
#: status file (`cut_suite`), so this number is re-measured every night.
CUT_SUITE_EVERY_NIGHT = ("showcase",)
CUT_SUITE_ALTERNATE_NIGHTS = ("community",)

#: Backstops, not budgets. The build spends minutes in `generalize`; the sharded suite
#: runs in minutes interactively, longer under launchd's background throttling.
CUT_BUILD_TIMEOUT_SECONDS = 1800
CUT_SUITE_TIMEOUT_SECONDS = 3600

#: How the cut's commit is signed. Not the public identity: this repo is never pushed,
#: and nothing in the suite reads its author.
CUT_GIT_IDENTITY = {"GIT_AUTHOR_NAME": "cut-suite", "GIT_AUTHOR_EMAIL": "cut-suite@localhost",
                    "GIT_COMMITTER_NAME": "cut-suite",
                    "GIT_COMMITTER_EMAIL": "cut-suite@localhost"}


# ─────────────────────────────────────────────────────────────────────────────
# Pure decisions. Kept free of subprocess and filesystem so the suite can put every
# branch under test without a 7200-second derive.
# ─────────────────────────────────────────────────────────────────────────────

def classify_derive(rc: int, stdout: str, stderr: str,
                    record: dict | None) -> tuple[str, str]:
    """(outcome, one-line reason) from what the derive actually did.

    THE ORDER MATTERS. A red suite and a refusal both exit 1, and a red suite still leaves
    a record on disk — so "is there a record?" cannot be the discriminator and neither can
    the exit code alone. The refusal is checked first because it is the one case that
    writes no record at all while an OLD record may still be sitting there from last
    night, which would otherwise read as a red suite.
    """
    combined = f"{stdout}\n{stderr}"
    if "tests changed during measurement" in combined:
        return REFUSED, ("tests/ changed while the derive was measuring, so it refused "
                         "rather than measure a moving tree")
    if rc == 0:
        # The derive's exit speaks for the serial suite only. It then runs every gate
        # command for real to measure it, and one that failed is a gate that would refuse
        # a land — the 2026-09-29 night read GREEN over `run_suite.py` exiting 1.
        failed = failing_gate_commands(record)
        if failed:
            first = failed[0]
            return RED, (f"serial suite green, but gate command `{first['cmd']}` "
                         f"{first['why']}"
                         + (f": {first['last']}" if first["last"] else "")
                         + (f" (+{len(failed) - 1} more)" if len(failed) > 1 else ""))
        return GREEN, "suite green; record derived"
    if record is not None and record.get("suite_ok") is False:
        verdict = record.get("verdict") or {}
        return RED, (f"suite RED — {verdict.get('failures', '?')} failure(s), "
                     f"{verdict.get('errors', '?')} error(s) over "
                     f"{record.get('tests', '?')} tests")
    if rc < 0:
        return ERROR, f"the derive was killed by signal {-rc}"
    first = next((ln for ln in stderr.splitlines() if ln.strip()), "")
    return ERROR, f"the derive exited {rc}" + (f": {first.strip()}" if first else "")


def failing_gate_commands(record: dict | None) -> list[dict]:
    """The gate commands the derive ran that did not pass: [{cmd, why, last, tail}].

    Failed means a nonzero `exit_code`, or no exit code because it could not launch.
    `measured: False` alone is NOT failure — a command whose shim never reported ran
    fine and simply runs on every land."""
    out = []
    for c in (record or {}).get("commands") or []:
        if not isinstance(c, dict):
            continue
        rc = c.get("exit_code")
        if rc == 0 or (rc is None and c.get("measured")):
            continue
        tail = str(c.get("stderr") or "").strip()
        last = next((ln.strip() for ln in reversed(tail.splitlines()) if ln.strip()), "")
        why = (f"exited {rc}" if isinstance(rc, int)
               else str(c.get("unmeasured_reason") or "did not run"))
        out.append({"cmd": str(c.get("cmd", "?")), "why": why, "last": last, "tail": tail,
                    "tests": [str(t) for t in c.get("failed_tests") or []]})
    return out


def gate_command_block(failed: list[dict]) -> str:
    """The failing gate commands as markdown for the comms note. Empty when none."""
    if not failed:
        return ""
    lines = [f"**Gate commands that did not pass ({len(failed)}).** The serial suite "
             f"was green; these ran as the land gate runs them (`POGA_GATE=1`)."]
    for f in failed:
        lines.append(f"- `{f['cmd']}` — {f['why']}")
        lines += [f"  - `{t}`" for t in f["tests"]]
        if f["tail"]:
            lines += ["", "  ```", *(f"  {ln}" for ln in f["tail"].splitlines()), "  ```"]
    return "\n".join(lines) + "\n\n"


def cut_editions_for(day: _dt.date) -> tuple[str, ...]:
    """Which editions' cuts tonight's runner builds and tests (WI-0495).

    Every night runs `CUT_SUITE_EVERY_NIGHT`; `CUT_SUITE_ALTERNATE_NIGHTS` join on nights
    whose date ordinal is even. Read from the date, so a skipped night cannot shift the
    rhythm and a test can name the night it means."""
    extra = CUT_SUITE_ALTERNATE_NIGHTS if day.toordinal() % 2 == 0 else ()
    return tuple(CUT_SUITE_EVERY_NIGHT) + tuple(extra)


def suite_failures(text: str) -> list[str]:
    """`FAIL: <id>` / `ERROR: <id>` headers from a unittest-shaped report, deduplicated in
    order -- the identities the summary's tail never carries (WI-0334)."""
    out = []
    for ln in text.splitlines():
        if ln.startswith(("FAIL: ", "ERROR: ")) and ln.strip() not in out:
            out.append(ln.strip())
    return out


def _ran_count(text: str) -> int | None:
    for ln in reversed(text.splitlines()):
        if ln.startswith("Ran ") and " test" in ln:
            try:
                return int(ln.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def cut_suite_result(edition: str, build: tuple[int, str, str] | None,
                     suite: tuple[int, str, str] | None,
                     build_seconds: float = 0.0, suite_seconds: float = 0.0) -> dict:
    """One edition's cut-suite run as a record: {edition, ok, why, tests, failing, ...}.

    `build` and `suite` are (exit code, stdout, stderr); `suite` is None when the build
    did not leave a cut to test. Pure, so every branch is tested without a nine-minute
    build. `ok` is True only for a clean build AND a suite that exited 0."""
    res = {"edition": edition, "ok": False, "why": "", "tests": None, "failing": [],
           "tail": "", "build_seconds": round(build_seconds, 1),
           "suite_seconds": round(suite_seconds, 1)}
    if build is None:
        res["why"] = "the cut was not built"
        return res
    brc, bout, berr = build
    if brc != 0:
        # The builder's own line, not whatever an interpreter printed before it.
        lines = [ln.strip() for ln in berr.splitlines() if ln.strip()]
        first = next((ln for ln in lines if ln.startswith("public_cut:")),
                     lines[0] if lines else "")
        res["why"] = (f"the {edition} cut could not be built (public_cut.py exited {brc})"
                      + (f": {first}" if first else ""))
        res["tail"] = "\n".join(berr.strip().splitlines()[-12:])
        return res
    if suite is None:
        res["why"] = f"the {edition} cut was built but its suite did not run"
        return res
    src, sout, serr = suite
    text = f"{sout}\n{serr}"
    res["tests"] = _ran_count(text)
    res["failing"] = suite_failures(text)
    res["tail"] = "\n".join(text.strip().splitlines()[-4:])
    if src == 0:
        res["ok"] = True
        res["why"] = f"suite green inside the {edition} cut ({res['tests']} tests)"
    elif res["failing"]:
        res["why"] = (f"the suite inside the {edition} public cut failed: "
                      f"{len(res['failing'])} test(s), first `{res['failing'][0]}`")
    else:
        last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
        res["why"] = (f"the suite inside the {edition} public cut exited {src}"
                      + (f": {last}" if last else ""))
    return res


def classify_cut_suites(outcome: str, reason: str, runs: list[dict]) -> tuple[str, str]:
    """The night's outcome after the cut suites. Only a GREEN night is touched: a night
    already red, refused or errored did not run them. Any edition that did not pass turns
    it RED -- it ran and returned a verdict, as a failing gate command does."""
    if outcome != GREEN or not runs:
        return outcome, reason
    bad = [r for r in runs if not r.get("ok")]
    if not bad:
        return GREEN, reason + "; " + "; ".join(r["why"] for r in runs)
    return RED, ("serial suite green, but " + bad[0]["why"]
                 + (f" (+{len(bad) - 1} more edition)" if len(bad) > 1 else ""))


def cut_failing_names(runs: list[dict]) -> list[str]:
    """The cut suites' failing tests in the status file's `failing` shape ("FAIL <id>"),
    each tagged with its edition, so the session-start suite line (sessionlib/land.py
    `_red_night_detail`) names them on a night only the cut turned red."""
    out = []
    for r in runs:
        if r.get("ok"):
            continue
        out += ["%s (in the %s cut)" % (n.replace(": ", " ", 1), r["edition"])
                for n in r.get("failing") or []]
    return out


def cut_suite_block(runs: list[dict]) -> str:
    """The cut suites that did not pass, as markdown for the comms note. Empty when none."""
    bad = [r for r in runs if not r.get("ok")]
    if not bad:
        return ""
    lines = [f"**Suite inside the public cut ({len(bad)} edition(s) did not pass).** The "
             f"serial suite was green on trunk; these tests fail in a scratch cut built by "
             f"`curate/public_cut.py`, which is what the public CI runs (WI-0495). The "
             f"usual cause is a test reading a path the cut withholds; skip it by name "
             f"when `PUBLIC-CUT-RECEIPT.md` exists."]
    for r in bad:
        lines.append(f"- `{r['edition']}` — {r['why']}")
        lines += [f"  - `{t}`" for t in r["failing"][:NOTE_FAILING_CAP]]
        if len(r["failing"]) > NOTE_FAILING_CAP:
            lines.append(f"  - …and {len(r['failing']) - NOTE_FAILING_CAP} more "
                         f"(see the status file).")
        if r["tail"]:
            lines += ["", "  ```", *(f"  {ln}" for ln in r["tail"].splitlines()), "  ```"]
    return "\n".join(lines) + "\n\n"


def ops_result_for(outcome: str) -> str | None:
    """What to write to the ops ledger — or None, meaning WRITE NOTHING.

    None is the whole point of this function and is not an oversight. `poga ops ran` rolls
    `due` forward from the cadence on every result including `fail`, so recording a night
    that never produced a verdict would mark a daily obligation met and hide it until
    tomorrow. A run that did not happen is not a run. Leaving the ledger alone is what lets
    OPS-0009 go overdue, and overdue is the startup view's loudest row.
    """
    return {GREEN: "pass", RED: "fail"}.get(outcome)


def should_land(outcome: str) -> bool:
    """Only a green derive is landable.

    A RED derive writes a well-formed record whose `suite_ok` is false, which the land's
    reader folds to None — landing it would replace a usable record with one that licenses
    no gate skip at all, slowing every land on the fleet until the next green night.
    """
    return outcome == GREEN


def outcome_after_land(outcome: str, landed: bool) -> str:
    """A green derive that did not LAND is not a met obligation.

    The distinction this function exists to hold: the suite ran and passed, so it is
    tempting to record the run `pass`. But OPS-0009's acceptance is that the record moved,
    and a record sitting in a lane that is about to be reaped has not moved. Recording
    `pass` here would roll the obligation forward on the strength of work that was thrown
    away — WI-0407's negative control ("a runner that calls `--derive` and stops does not
    close this item") arriving by a different route.
    """
    return outcome if (outcome != GREEN or landed) else ERROR


#: WI-0431. How many failing tests the comms note and the status file name. The note is
#: read by a person, so it stays short; the status file is the machine-readable form and
#: holds more. Both always carry the whole COUNT, so a capped list says it is capped.
NOTE_FAILING_CAP = 25
STATUS_FAILING_CAP = 200


def failing_tests(record: dict | None) -> tuple[int | None, list[str]]:
    """(total failing, ["FAIL <id>" | "ERROR <id>", ...]) from a derive's record.

    The total comes from the verdict's COUNTS, not from the length of the id lists: the
    record caps its lists, and a record written before WI-0431 carries none at all. So
    `(114, [])` is a real answer — "114 failed, names not recorded" — and it must never
    render as "nothing failed". `(None, [])` means the record says nothing either way.
    """
    verdict = (record or {}).get("verdict")
    if not isinstance(verdict, dict):
        return None, []
    counts = [c for c in (verdict.get("failures"), verdict.get("errors"))
              if isinstance(c, int)]
    total = sum(counts) if counts else None
    names = [f"FAIL {t}" for t in verdict.get("failure_ids") or []] + \
            [f"ERROR {t}" for t in verdict.get("error_ids") or []]
    return total, names


def failing_reasons(record: dict | None) -> dict[str, str]:
    """WI-0432: {"FAIL <id>" | "ERROR <id>": first line of its exception} from a derive's
    record, keyed like `failing_tests`' names so the two join without re-parsing. A record
    written before WI-0432 has no reasons; that is `{}`, and every name still renders."""
    verdict = (record or {}).get("verdict")
    reasons = verdict.get("failing_reasons") if isinstance(verdict, dict) else None
    if not isinstance(reasons, dict):
        return {}
    out = {}
    for kind, key in (("FAIL", "failure_ids"), ("ERROR", "error_ids")):
        for t in verdict.get(key) or []:
            if isinstance(reasons.get(t), str):
                out[f"{kind} {t}"] = reasons[t]
    return out


def failing_block(total: int | None, names: list[str], cap: int,
                  reasons: dict[str, str] | None = None) -> str:
    """The failing tests as a markdown list, capped, with the count. Empty when there is
    nothing to say. With `reasons`, each name carries the first line of its exception."""
    if not total:
        return ""
    shown = names[:cap]
    reasons = reasons or {}
    lines = [f"**Failing tests ({total}).**"]
    if not names:
        lines.append("The record carries the count but no names.")
    lines += [f"- `{n}`" + (f" — {reasons[n]}" if n in reasons else "") for n in shown]
    if total > len(shown):
        lines.append(f"- …and {total - len(shown)} more (see the status file).")
    return "\n".join(lines) + "\n\n"


def blocked_note_body(outcome: str, reason: str, when: str, machine: str,
                      log_hint: str, failing: str = "") -> str:
    """ADR-0046 comms note for a night that did not land a record."""
    ledger = ("recorded `fail` — the derive ran and returned a verdict"
              if outcome == RED else
              "NOT recorded — a run that produced no verdict is not a run, and recording "
              "one would roll the daily obligation forward and hide it until tomorrow")
    return (
        f"# Gate-input derive did not land a record ({outcome.upper()})\n"
        f"\n"
        f"- **Kind:** blocked\n"
        f"- **System:** federation\n"
        f"- **Obligation:** {OPS_ID}\n"
        f"- **Raised:** {when}\n"
        f"- **Machine:** {machine}\n"
        f"- **Raised by:** `curate/gate-inputs-runner.py` (WI-0407) — no human was in the"
        f" loop.\n"
        f"\n"
        f"**What happened.** {reason}.\n"
        f"\n"
        f"{failing}"
        f"**Ledger.** {OPS_ID} {ledger}.\n"
        f"\n"
        f"**Why you are reading this.** The derive is the only serial full-suite run the\n"
        f"federation performs; the land gate is sharded and structurally cannot see\n"
        f"order-dependent contamination. A night that does not land leaves\n"
        f"`{RECORD_NAME}` at its previous value, which ages: a stale record disqualifies\n"
        f"the land's gate skips, so lands get slower rather than wronger.\n"
        f"\n"
        f"**Where to look.** {log_hint}\n"
        f"\n"
        f"This note has a stable filename. It is overwritten while the condition holds and\n"
        f"deleted by the first night that lands a record, so its presence is current.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Mechanics.
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def runner_env() -> dict:
    env = dict(os.environ)
    for key in INHERITED_SESSION_ENV:
        env.pop(key, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def read_record(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def refuse_wrong_tree() -> str | None:
    """Say why this process must not derive, or None to proceed.

    THREE REFUSALS, and each names a real way this unit drifts.

    RUNNING AS ROOT. This is a LaunchDaemon, and a daemon runs as root unless its plist
    says otherwise. `UserName` cannot live in the tracked template — the renderer accepts
    exactly four tokens and refuses any other, so the installer injects it after
    rendering. A plist produced some other way therefore runs as root, and a root derive
    would fill the operator's checkout with root-owned files that his own session then cannot
    write. Refusing costs a night; the alternative costs a repair.

    A LINKED WORKTREE. The ops ledger write refuses from a lane by design — a lane's
    `ops-items/` is a tracked copy frozen at its base commit that nothing else reads. A
    runner installed against a lane would derive, land and then fail to record, every
    night, having done the expensive part.

    A SEALED DEPLOY TREE. This unit derives the DEVELOPMENT trunk. A production deploy
    tree is a detached clone at a release tag; deriving there would measure a tree nobody
    develops in. It is also precisely the direction this unit is expected to drift, since
    the federation's other three units DO live in the sealed tree.
    """
    if os.geteuid() == 0:
        return ("this runner is executing as root. Its plist is missing the `UserName` "
                "the installer injects, so every file it wrote would be root-owned in a "
                "checkout the operator's own session has to write. Re-run "
                "`deploy/install-gate-inputs.sh`.")
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], ROOT)
    gitdir = git(["rev-parse", "--path-format=absolute", "--git-dir"], ROOT)
    if common.returncode == 0 and gitdir.returncode == 0:
        if common.stdout.strip() != gitdir.stdout.strip():
            return (f"{ROOT} is a linked worktree, not the main checkout. The ops ledger "
                    f"write refuses from a lane, so this runner would do the expensive "
                    f"half nightly and never record it. Install from the main checkout.")
    deploy_root = pathlib.Path(
        os.environ.get("POGA_DEPLOY_ROOT", pathlib.Path.home() / "deploy"))
    sealed = deploy_root / "federation"
    if ROOT == sealed or sealed in ROOT.parents:
        return (f"{ROOT} is the sealed federation deploy tree. This unit derives the "
                f"DEVELOPMENT trunk — a deploy tree is a detached clone at a release tag "
                f"and deriving there measures a tree nobody develops in.")
    return None


def refresh_trunk() -> str:
    """Fast-forward the main checkout, or say why not. Never a pull, never a merge.

    The land refuses outright when the local trunk has diverged from origin, so a nightly
    that skipped this would spend the suite and then refuse at the very end. Fast-forward
    only, and only on a clean tree: this runs at 02:30 against a checkout a human may have
    left mid-something, and a nightly job is never the right thing to be resolving that.
    """
    if git(["status", "--porcelain"], ROOT).stdout.strip():
        return "left alone — the main checkout has uncommitted changes"
    if git(["fetch", "--quiet", "origin"], ROOT).returncode != 0:
        return "fetch failed — proceeding against the local trunk"
    if git(["rev-parse", "--abbrev-ref", "@{u}"], ROOT).returncode != 0:
        return "no upstream tracking — proceeding against the local trunk"
    result = git(["merge", "--ff-only", "--quiet", "@{u}"], ROOT)
    return "fast-forwarded to origin" if result.returncode == 0 else "already current"


def open_lane() -> tuple[str, pathlib.Path] | None:
    """Draw a lane name and create its worktree.

    The `worktree-` branch prefix is required, not cosmetic: the land keys on it to know
    it is landing a lane at all, and `reap-lanes` keys on it to know what it may clean.
    """
    alloc = subprocess.run([sys.executable, str(ROOT / "session.py"), "lane-alloc"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env=runner_env())
    lane = alloc.stdout.strip()
    if alloc.returncode != 0 or not lane:
        log(f"lane-alloc: exit {alloc.returncode} — "
            f"{alloc.stderr.strip() or 'no lane free'}")
        return None
    path = ROOT / ".claude" / "worktrees" / lane
    created = git(["worktree", "add", "-b", f"worktree-{lane}", str(path)], ROOT)
    if created.returncode != 0:
        log(f"worktree add failed: {created.stderr.strip()}")
        return None
    return lane, path


def reap_lane(lane: str, path: pathlib.Path) -> None:
    """Remove our own lane.

    Never `--force`, and the branch delete is merged-only, so a lane still holding
    unlanded work survives and `session.py reap-lanes` reports it as unmerged rather than
    this runner quietly destroying a night's evidence.
    """
    if git(["worktree", "remove", str(path)], ROOT).returncode != 0:
        log(f"lane {lane}: worktree not removed (left for `session.py reap-lanes`)")
        return
    git(["worktree", "prune"], ROOT)
    if git(["branch", "-d", f"worktree-{lane}"], ROOT).returncode != 0:
        log(f"lane {lane}: branch worktree-{lane} kept — it holds commits main does not")
    else:
        log(f"lane {lane}: reaped")


def stage_record_only(lane_path: pathlib.Path) -> str:
    """Reduce the lane to exactly one staged change: the record. Return what was discarded.

    NECESSARY, not tidiness. The land's first move is `git add -A`, so anything the derive
    left behind rides into the trunk under the runner's commit message. The deriver leaks
    `.gate-inputs.raw` when a probe dies (it is not in `.gitignore`), and the eight gate
    commands it measures run for real against the lane. Staging the record, restoring every
    other tracked file and cleaning the untracked ones leaves a diff that is provably one
    file — which is also what lets the land classify it gate-neutral and skip a suite this
    runner has already run.
    """
    git(["add", "--", RECORD_NAME], lane_path)
    leftover = git(["status", "--porcelain"], lane_path).stdout.strip()
    git(["checkout", "--", "."], lane_path)
    cleaned = git(["clean", "-fdq"], lane_path)
    if cleaned.returncode != 0:
        log(f"clean: {cleaned.stderr.strip()}")
    return "\n".join(ln for ln in leftover.splitlines()
                     if ln[3:].strip() != RECORD_NAME)


def write_status(payload: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(STATUS_FILE)


def sync_blocked_note(outcome: str, reason: str, when: str, failing: str = "") -> None:
    """Write the standing note, or clear it — the condition's own current state."""
    if outcome == GREEN:
        try:
            BLOCKED_NOTE.unlink()
            log("comms: cleared the standing blocked note")
        except FileNotFoundError:
            pass
        return
    try:
        BLOCKED_NOTE.parent.mkdir(parents=True, exist_ok=True)
        BLOCKED_NOTE.write_text(blocked_note_body(
            outcome, reason, when, os.uname().nodename,
            f"`.session-state/gate-inputs.launchd.out.log` on this machine, and "
            f"`{STATUS_FILE.relative_to(ROOT)}` for the machine-readable form.",
            failing))
        log(f"comms: wrote {BLOCKED_NOTE.relative_to(ROOT)}")
    except OSError as exc:
        log(f"comms: could not write the blocked note: {exc}")


def record_ops_run(result: str, reason: str) -> bool:
    """`poga ops ran` — the front door, which anchors on the main checkout by itself.

    Spelled through `poga` rather than `session.py ops-ran` on purpose: the ledger write
    refuses from a linked worktree, and the front door is the spelling that resolves the
    right tree without this runner having to know the rule. `poga ops` takes no preflight
    gate, so this costs a `cd` and an exec.
    """
    poga = ROOT / "poga"
    if not os.access(poga, os.X_OK):
        log(f"ops: {poga} is not executable — {OPS_ID} NOT recorded")
        return False
    ran = subprocess.run([str(poga), "ops", "ran", OPS_ID, "--result", result,
                          "--notes", f"gate-inputs-runner: {reason}"[:240]],
                         cwd=str(ROOT), capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, env=runner_env())
    if ran.returncode != 0:
        log(f"ops: `poga ops ran {OPS_ID} --result {result}` exited {ran.returncode} — "
            f"{ran.stderr.strip()[:400]}")
        return False
    log(f"ops: {OPS_ID} recorded {result}")
    return True


def run_derive(lane_path: pathlib.Path) -> tuple[int, str, str]:
    """Run the derive IN THE LANE.

    The lane's own copy of the script, not ROOT's: `gate_inputs.py` resolves its record
    from `__file__`, so ROOT's copy would write ROOT's record and leave the lane with
    nothing to land.
    """
    env = runner_env()
    env["POGA_GATE_PROBE_TIMEOUT"] = str(PROBE_TIMEOUT_SECONDS)
    try:
        done = subprocess.run(
            [sys.executable, str(lane_path / "curate" / "gate_inputs.py"), "--derive"],
            cwd=str(lane_path), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=env, timeout=DERIVE_TIMEOUT_SECONDS)
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        return 124, "", (f"the derive did not finish within {DERIVE_TIMEOUT_SECONDS}s "
                         f"and was killed")


def _timed(argv: list[str], cwd: pathlib.Path, env: dict,
           timeout: int) -> tuple[tuple[int, str, str], float]:
    t0 = _dt.datetime.now()
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=env, timeout=timeout)
        got = (done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired:
        got = (124, "", f"did not finish within {timeout}s and was killed")
    except OSError as exc:
        got = (127, "", f"could not launch: {exc}")
    return got, (_dt.datetime.now() - t0).total_seconds()


def run_cut_suite(lane_path: pathlib.Path, edition: str) -> dict:
    """Build one edition's public cut from the lane and run the suite inside it (WI-0495).

    The lane's own `public_cut.py`, for `run_derive`'s reason. The cut goes to a scratch
    directory OUTSIDE every checkout and is deleted after: it is never a publish clone,
    and `--out` writes a private receipt beside it that must not linger. It is committed
    into a fresh repo because the public CI runs in a checkout, and tests that ask git
    about their own tree must find one. `run_suite.py` is the CUT's copy: the suite is the
    cut's, run the way the land gate runs trunk's."""
    import shutil
    import tempfile
    env = runner_env()
    box = pathlib.Path(tempfile.mkdtemp(prefix="poga-cut-suite-"))
    try:
        out = box / "cut"
        build, build_s = _timed(
            [sys.executable, "-B", str(lane_path / "curate" / "public_cut.py"),
             "--out", str(out), "--edition", edition],
            lane_path, env, CUT_BUILD_TIMEOUT_SECONDS)
        if build[0] != 0 or not out.is_dir():
            return cut_suite_result(edition, build, None, build_s)
        genv = dict(env, **CUT_GIT_IDENTITY)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "scratch cut"]):
            # No signing: a key prompt at 02:30 has nobody to answer it.
            g = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=str(out),
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, env=genv)
            if g.returncode != 0:
                return cut_suite_result(
                    edition, build, (g.returncode, "", f"git {args[0]} in the scratch cut: "
                                     f"{g.stderr.strip()}"), build_s)
        suite, suite_s = _timed([sys.executable, "-B", str(out / "curate" / "run_suite.py")],
                                out, env, CUT_SUITE_TIMEOUT_SECONDS)
        return cut_suite_result(edition, build, suite, build_s, suite_s)
    finally:
        shutil.rmtree(box, ignore_errors=True)


def land_record(lane_path: pathlib.Path) -> tuple[int, str]:
    """Land the lane the way `recover-lanes` does — `merge --continue`, in the lane.

    `--continue` and not a bare `merge`: a bare merge CLOSES the session, and there is no
    session here to close and no human to confirm the close. `recover-lanes` reaches for
    the same flag for the same reason.

    THE LANE'S OWN `session.py`, never ROOT's — same rule as `run_derive`, and the one
    that cost the 2026-09-29 night. `session.py` resolves its repo from `__file__`, not
    from cwd, so ROOT's copy run with the lane as cwd reads ROOT's branch and refuses:
    `not on a session branch (on 'main')`, after a green derive.
    """
    try:
        done = subprocess.run(
            [sys.executable, str(lane_path / "session.py"), "merge", "--continue"],
            cwd=str(lane_path), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=runner_env(), timeout=LAND_TIMEOUT_SECONDS)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return 124, f"the land did not finish within {LAND_TIMEOUT_SECONDS}s"


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    started = _dt.datetime.now().astimezone()
    when = started.strftime("%Y-%m-%d %H:%M %Z")

    why = refuse_wrong_tree()
    if why:
        log(f"REFUSING: {why}")
        return 2

    # launchd hands a daemon no HOME. git needs one to find ~/.gitconfig and the ssh key
    # the push authenticates with, and without it the land fails at the very last step
    # having spent the whole suite. The password database is asked rather than $HOME
    # because it is the answer the environment cannot be missing (`schedulerguard.py`
    # makes the same argument for the same reason).
    home = pwd.getpwuid(os.getuid()).pw_dir
    if os.environ.get("HOME") != home:
        log(f"env: HOME was {os.environ.get('HOME') or 'unset'} — using {home}")
        os.environ["HOME"] = home

    # An 18-minute job must not overlap the next fire or a derive somebody started by
    # hand: two concurrent derives in one checkout fight over the same record and run the
    # same eight gate commands against each other. O_EXCL, so the check and the claim are
    # one operation rather than two with a window between them.
    try:
        handle = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, f"{os.getpid()} {when}\n".encode())
        os.close(handle)
    except FileExistsError:
        log(f"another gate-input derive holds {LOCK_FILE.name} — nothing done. If no "
            f"derive is running, that file is stale and can be removed.")
        return 0

    lane_info: tuple[str, pathlib.Path] | None = None
    failing_total: int | None = None
    failing_names: list[str] = []
    failing_why: dict[str, str] = {}
    failed_cmds: list[dict] = []
    cut_runs: list[dict] = []
    outcome, reason = ERROR, "the runner did not reach the derive"
    landed = False
    try:
        log(f"trunk: {refresh_trunk()}")
        lane_info = open_lane()
        if lane_info is None:
            outcome, reason = ERROR, ("no lane could be opened — the pool is full or the "
                                      "worktree could not be created")
        else:
            lane, lane_path = lane_info
            log(f"lane {lane}: deriving (probe cap {PROBE_TIMEOUT_SECONDS}s, backstop "
                f"{DERIVE_TIMEOUT_SECONDS}s; about an hour is normal under launchd's "
                f"background throttling)")
            code, out, err = run_derive(lane_path)
            for line in f"{out.strip()}\n{err.strip()}".splitlines():
                if line.strip():
                    log(f"  derive| {line.rstrip()}")
            record = read_record(lane_path / RECORD_NAME)
            outcome, reason = classify_derive(code, out, err, record)
            log(f"outcome: {outcome.upper()} — {reason}")
            if outcome == RED:
                failing_total, failing_names = failing_tests(record)
                failing_why = failing_reasons(record)
                failed_cmds = failing_gate_commands(record)
                for f in failed_cmds:
                    log(f"  gate-cmd| {f['cmd']} — {f['why']}")
                for name in failing_names[:NOTE_FAILING_CAP]:
                    log(f"  failing| {name}"
                        + (f" — {failing_why[name]}" if name in failing_why else ""))

            discarded = ""
            if outcome == GREEN:
                # Staged first, so the cut is built from the lane's commit plus the record
                # and not from whatever the eight measured gate commands left behind.
                discarded = stage_record_only(lane_path)
                for edition in cut_editions_for(started.date()):
                    log(f"lane {lane}: building the {edition} public cut and running the "
                        f"suite inside it (WI-0495)")
                    run = run_cut_suite(lane_path, edition)
                    cut_runs.append(run)
                    log(f"  cut-suite| {edition}: {'PASS' if run['ok'] else 'FAIL'} — "
                        f"{run['why']} (build {run['build_seconds']}s, suite "
                        f"{run['suite_seconds']}s)")
                    for name in run["failing"][:NOTE_FAILING_CAP]:
                        log(f"  cut-failing| {name}")
                outcome, reason = classify_cut_suites(outcome, reason, cut_runs)
                if outcome != GREEN:
                    log(f"outcome: {outcome.upper()} — {reason}")
                    failing_names = cut_failing_names(cut_runs)
                    failing_total = len(failing_names) or None

            if should_land(outcome):
                staged = git(["diff", "--cached", "--name-only"], lane_path).stdout.split()
                if staged != [RECORD_NAME]:
                    outcome = ERROR
                    reason = (f"refusing to land — the staged diff is "
                              f"{staged or 'empty'}, not exactly [{RECORD_NAME}]")
                    log(f"outcome: ERROR — {reason}")
                else:
                    if discarded:
                        log(f"  discarded non-record changes: {discarded.splitlines()}")
                    git(["commit", "--quiet", "-m",
                         f"chore(gate-inputs): nightly derive ({OPS_ID})"], lane_path)
                    log("lane: landing the record-only diff")
                    land_code, land_out = land_record(lane_path)
                    landed = land_code == 0
                    if landed:
                        log("lane: landed")
                    else:
                        outcome = ERROR
                        reason = (f"the derive was green but the land exited {land_code} "
                                  f"— the record is still only in the lane")
                        log(f"outcome: ERROR — {reason}")
                        log(f"  land| {land_out.strip()[-600:]}")
    finally:
        if lane_info is not None:
            # A lane that failed to land keeps its branch (the delete is merged-only), so
            # the evidence survives and `reap-lanes` reports it as unmerged.
            reap_lane(*lane_info)
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass

    outcome = outcome_after_land(outcome, landed)
    result = ops_result_for(outcome)
    recorded = record_ops_run(result, reason) if result else False
    if result is None:
        log(f"ops: {OPS_ID} deliberately NOT recorded — a run that produced no verdict is "
            f"not a run, and recording one would roll the obligation forward and hide it")
    # A night only the cut turned red names its tests in the cut block, with the edition
    # and the cause; the serial list would repeat them under a heading that is not true.
    cut_red = any(not r.get("ok") for r in cut_runs)
    sync_blocked_note(outcome, reason, when,
                      ("" if cut_red else
                       failing_block(failing_total, failing_names, NOTE_FAILING_CAP,
                                     failing_why))
                      + gate_command_block(failed_cmds) + cut_suite_block(cut_runs))

    write_status({
        "started": started.isoformat(timespec="seconds"),
        "finished": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": os.uname().nodename,
        "outcome": outcome,
        "reason": reason,
        "landed": landed,
        "ops_result": result,
        "ops_recorded": recorded,
        # WI-0431: the names, so a red night is diagnosable from this file alone and the
        # session-start suite line can say which tests. `failing_total` is the count and
        # is authoritative; the list is capped.
        "failing_total": failing_total,
        "failing": failing_names[:STATUS_FAILING_CAP],
        # WI-0432: why, keyed by the same names — the first line of each exception.
        "failing_reasons": {n: failing_why[n] for n in failing_names[:STATUS_FAILING_CAP]
                            if n in failing_why},
        # Brief 2026-09-29: gate commands that failed under a green serial suite.
        "failing_gate_commands": [{k: f[k] for k in ("cmd", "why", "tests", "tail")}
                                  for f in failed_cmds],
        # WI-0495: each edition's suite inside a scratch public cut, with its cost, so the
        # nightly price of the step is on record every night, not only on the day measured.
        "cut_suite": [dict(r, failing=r["failing"][:STATUS_FAILING_CAP]) for r in cut_runs],
    })
    return 0 if (outcome == GREEN and landed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
