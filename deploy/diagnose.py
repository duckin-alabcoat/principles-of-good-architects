#!/usr/bin/env python3
"""Read-only evidence about a deployed system, gathered where it runs and mailed home.

WI-0229, closing Exit C of the 2026-09-03 consultant brief. The three exits that brief
opened all end the same way — a result travelling *to* devbox — and this is the third:
*"Ask for diagnostics and receive a bundle (logs, launchd state, ledger) delivered by
the mail path. Never open a Runner session to look."*

THE DEFECT THIS CLOSES is not that the facts were unavailable. Every one of them is
readable on the Runner, by a person, in about four commands. It is that the only reader
was a person on the other machine, so every question about a deployed system spent the operator's
keyboard — the shape the 09-03 brief rules is a substrate defect and counts as one.

WHAT IT MAY NOT DO, and this is the load-bearing half. ADR-0103 D4 makes the runner plain
code and this is plainer: no `claude`, no model, no network, and **nothing that changes
the system it is looking at**. The one exception is the contract's own `verify`, which is
executed rather than observed, and which is therefore reported under its own heading with
that fact stated — see `_verify`. Everything else is a read.

WHY A SEPARATE MODULE AND NOT A FLAG ON `runner.py`. Every other function in that file
exists to change this machine: check out a tag, seed state, restart units, roll back. A
read-only collector living among them is one careless edit away from not being one, and
the property that makes this safe to run unattended on production is exactly that it
cannot write. The separation is the guarantee. It also keeps the two in-flight items off
each other's file — WI-0228 owns `runner.py`'s canary path.

THE POSTING RULE IS A STATE CHANGE, NEVER A SWEEP. `post_if_changed` compares a DIGEST of
the stable facts — deployed tag, per-unit liveness, contract validity, verify exit, ledger
status — and posts only when one of them moves. The sweep runs every 600s; a bundle per
run would be 144 tracked files per system per day and the record would bury what it
records. That argument is not new here: it is the one WI-0230's receipt path already made
and won, and the digest deliberately excludes the LOG TEXT for the same reason. Log lines
change constantly; including them would make every sweep a "change" and rebuild the
problem inside the fix. The logs still travel — they are the evidence IN the bundle, cut
at the moment the stable state moved, which is the moment worth having them from.

MACHINE OUTPUT IS REDACTED BEFORE IT TRAVELS. The outbox is tracked and pushed, so a
bundle is permanent. `launchctl print` can carry the loaded job's whole environment, and
`curate/mailacceptance.py` already refuses to store its raw output for that reason; a log
tail can carry anything at all. So every quoted byte goes through `scrub.redact` and the
counts are reported in the bundle — and `outbox.post`'s scrub gate stays in front of that
unchanged, as an independent check that the redaction worked rather than a door this
module argued its way past with `force`. Nothing here ever passes `force`.

WHAT IT CANNOT SEE IS NAMED, never omitted. Every probe that could not answer appends to
`gaps`. A bundle that simply left out the unit it could not read would be reporting
"looked and found nothing" in the same shape as "never looked", which is the collapse
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) exists
to prevent, and which this repo has paid for more than once.

stdlib only. Run it directly (`poga diagnose <system>`) or let the sweep call it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import plistlib
import socket
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The runner owns every fact about where a deployed system lives — the registry, the tree,
# the contract, the ledger, the launchd domain. Importing it is what keeps this module
# from becoming a second, disagreeing answer to "what is deployed here" (P16). The import
# also installs the `curate/` and repo-root paths this file's other imports rely on.
import runner                                                        # noqa: E402
import outbox                                                        # noqa: E402
import scrub                                                         # noqa: E402
import production                                                    # noqa: E402
import poga_evidence as evidence                                     # noqa: E402
# WI-0396. The acceptance COLLECTOR, imported rather than reimplemented: its `snapshot`
# has read every input WI-0366's acceptance names since the day it was written, and
# nothing outside its own tests ever called it — so the evidence existed only when
# somebody sat down and ran a command, on the machine the operator's 2026-09-13 ruling says no
# session reaches. `_acceptance` below is that missing call.
import mailacceptance as acceptance                                  # noqa: E402

SCHEMA_VERSION = 1

#: How much of each log file travels. A tail, because a long-running unit's interesting
#: line is its most recent one — the opposite of a failed deploy, whose explanation is at
#: the head, which is why `runner.run_gate` cuts the other way. Both go through
#: `evidence.clip`, so whichever end is dropped says so and says by how much.
LOG_TAIL_LINES = 40


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ── redaction ────────────────────────────────────────────────────────────────────

def _clean(text: str, gaps: list, where: str) -> str:
    """Redact machine output and record on `gaps` that it happened.

    A redaction nobody mentions is indistinguishable from output that never carried
    anything, and a reader reasoning about an altered line needs to know it was altered.
    """
    if not text:
        return text
    out, counts = scrub.redact(text)
    for cls, n in sorted(counts.items()):
        gaps.append(f"{where}: {n} {cls} value(s) redacted before this bundle was written "
                    f"— the outbox is tracked, so what travels is permanent.")
    return out


# ── the probes ───────────────────────────────────────────────────────────────────

def _ledger(system: str, tag: Optional[str], gaps: list) -> dict:
    """The ledger record, with every field that belongs to a DIFFERENT tag marked stale.

    `runner.write_ledger` merges — it is a read-modify-write, so a field written three
    releases ago survives beside today's `status` and reads exactly like a current fact.
    The runner's own answer is to stamp each cutover event with the tag it belongs to and
    refuse to render one whose tag does not match; this applies the same discipline to the
    whole record, because a diagnosis that presents a stale field as live is worse than
    one that presents nothing.
    """
    rec = runner.read_ledger(system)
    if rec.get("_unreadable"):
        gaps.append(f"ledger: {runner.ledger_path(system)} exists and could not be parsed. "
                    f"The running version below is read from the tree, which is ground "
                    f"truth; every other ledger field is unavailable, not absent.")
        return {"readable": False}
    if not rec:
        return {"readable": True, "present": False}

    for key in ("smoke", "verify", "rollback_verify"):
        g = rec.get(key)
        if isinstance(g, dict) and g.get("detail"):
            g["detail"] = _clean(g["detail"], gaps, f"ledger {key} output")

    out = {"readable": True, "present": True, "record": rec}
    ev = rec.get("cutover_event") or {}
    if ev and tag and ev.get("tag") and ev["tag"] != tag:
        out["stale_cutover_event"] = (
            f"the ledger's cutover_event describes {ev['tag']}, not the {tag} now on the "
            f"tree — it is a record of an older release and says nothing about this one")
    if tag and rec.get("current") and rec["current"] != tag:
        out["ledger_disagrees_with_tree"] = (
            f"the ledger says current={rec['current']} and the tree is actually at {tag}. "
            f"The tree is ground truth; the ledger is stale or was written by another run.")
    # THE STATUS WAS DECIDED FOR A DIFFERENT RELEASE (WI-0412).
    #
    # This is the check the two above could not make, and the case that defeated them is
    # the one that was LIVE on the Runner: `current` matched the tree (the no-op write set
    # it from the tree), so `ledger_disagrees_with_tree` was silent; the stale field was
    # `status`, which neither check examined; and `cutover_event` was absent entirely for
    # an apply-strategy member, so `stale_cutover_event` had nothing to compare. The
    # federation's production ledger read `status: deployed` beside `current: v7.4.0` with
    # a `deployed_at` six days older than v7.4.0's own commit, and every existing check
    # passed over it.
    #
    # A RECORD WITH NO `status_tag` IS "CANNOT TELL", NOT "FINE". Every status written
    # since WI-0412 carries one; a record that predates it, or was written by an older
    # runner, genuinely cannot say what its status was about — and saying so is the point
    # ([`declare-what-a-check-assumes`]). Folding it into the clean case would certify
    # exactly the records most likely to be carrying the defect.
    if rec.get("status"):
        decided_for = rec.get("status_tag")
        if "status_tag" not in rec:
            out["unbound_status"] = (
                f"the ledger's status={rec['status']!r} does not say which tag it was "
                f"decided for. It was written before statuses were bound to their tag "
                f"(WI-0412), so whether it describes {tag or 'the tree'} cannot be "
                f"determined from this record — treat it as unknown, not as current.")
        elif tag and decided_for != tag:
            out["stale_status"] = (
                f"the ledger's status={rec['status']!r} was decided for "
                f"{decided_for or 'an unnamed tag'}, not the {tag} now on the tree. It is "
                f"a verdict on an earlier release and says nothing about this one — "
                f"including when it reads as a success.")

    # THE LAST ATTEMPT WAS TURNED AWAY (WI-0410), and it is its own answer rather than a
    # missing one. Without this key a bundle from a machine whose deploy refused is
    # byte-identical to a bundle from a machine that had nothing to do — the state the
    # runner is in and the state it would be in if everything were fine read the same, and
    # the only copy of the truth is a launchd stdout log on the host.
    #
    # PRESENT MEANS OUTSTANDING. `deploy()` clears the key on any deploy that ran without
    # refusing, so this is never a record of a refusal that has since been resolved — it
    # does not need the tag-matching guard the two checks above carry.
    ref = rec.get("refusal") or {}
    if ref:
        attempted = ref.get("attempted")
        out["deploy_refused"] = (
            f"the last deploy attempt REFUSED at {ref.get('at', 'an unrecorded time')} "
            + (f"reaching for {attempted}" if attempted else
               "before it could resolve which tag it was reaching for")
            + f" (trigger: {ref.get('trigger', 'unknown')}). Production was not touched and "
            f"every field above still describes the version that is running — this is what "
            f"did NOT happen, not a verdict on what is live. The refusal said: "
            + (ref.get("message") or "(no message recorded)"))
    return out


def _contract(system: str, entry: dict, tag: Optional[str], gaps: list) -> dict:
    """Read and validate the contract WITHOUT writing the ledger.

    `runner.read_contract` calls `write_ledger(status="contract-invalid")` on a schema
    failure. That is right for a deploy — the refusal is a fact about this machine worth
    recording — and wrong for a diagnosis, which must not change what it is reporting on.
    An observer whose act of observing rewrites the record is not an observer. So the two
    halves are done here by hand against the runner's own validator, which keeps the
    verdict identical without the side effect.
    """
    if not tag:
        return {"read": False, "why": "nothing is deployed in the tree, so there is no "
                                      "tag to read a contract from"}
    tree = runner.deploy_tree(system)
    sub = entry.get("subdir")
    relpath = f"{sub}/{runner.CONTRACT_RELPATH}" if sub else runner.CONTRACT_RELPATH
    r = runner.git(["show", f"{tag}:{relpath}"], tree, timeout=60)
    if r.returncode != 0:
        gaps.append(f"contract: {tag} carries no {relpath}. A tag without a contract is "
                    f"not deployable and its Architect owes one (ADR-0103 D11).")
        return {"read": False, "why": f"{tag} carries no contract at {relpath}"}
    try:
        contract = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"read": False, "why": f"{relpath} at {tag} is not valid JSON — {e}"}

    try:
        schema = json.loads(
            (runner.REPO_ROOT / "deploy" / "contract.schema.json").read_text())
        runner._validate_contract(contract, schema, system)
        valid, why = True, None
    except runner.DeployError as e:
        valid, why = False, str(e)
    except OSError as e:
        gaps.append(f"contract: the schema itself could not be read ({e}), so the "
                    f"contract below is UNVALIDATED rather than valid.")
        valid, why = None, f"schema unreadable: {e}"

    cv = contract.get("contract_version")
    if valid and cv != runner.SUPPORTED_CONTRACT_VERSION:
        valid, why = False, (f"contract_version {cv!r} — this runner speaks "
                             f"{runner.SUPPORTED_CONTRACT_VERSION}")

    state = contract.get("state")
    if isinstance(state, list):
        contract["state"] = [{"path": s, "required": False} if isinstance(s, str) else s
                             for s in state]
    return {"read": True, "valid": valid, "why": why, "contract": contract}


def _launchctl(label: str, gaps: list) -> dict:
    """One unit's loaded record, by NAMED FIELD — the raw output is never kept.

    `curate/mailacceptance.py` established the rule and the reason: `launchctl print`
    dumps the loaded job's whole environment, which can include credentials that have
    nothing to do with this system. This bundle is written into a TRACKED directory and
    pushed. So the fields are parsed out one at a time and the rest is discarded here,
    where it cannot be forgotten later — never stored and then redacted downstream.

    `runner.unit_target` already reads this output for the working directory alone. The
    extra fields are what the diagnosis needs and the deploy does not: whether it is
    loaded at all, its pid, and the exit status of its last run.
    """
    r = runner.run(["launchctl", "print", f"{runner.launchctl_domain()}/{label}"],
                   timeout=30)
    if r.returncode != 0:
        return {"label": label, "loaded": False,
                "detail": "not loaded in this launchd domain (or unreadable from here)"}
    rec: dict[str, Any] = {"label": label, "loaded": True}
    wanted = {"working directory": "working_directory", "pid": "pid",
              "last exit code": "last_exit_code", "state": "state",
              "runs": "runs"}
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key in wanted:
            rec.setdefault(wanted[key], value)

    # THE LOADED PROGRAM ITSELF — WorkingDirectory, the whole argument vector and the
    # production selection in its environment (WI-0366's acceptance names all three).
    # Parsed by `curate/mailacceptance.py`'s parser and NOT by a second one written here:
    # `deploy/legacydeps.py` already declined to add a second reader of `launchctl print`,
    # and the reason holds harder now that there would be three — two parsers of one
    # format disagree the day the format moves, and only one of them gets fixed.
    #
    # Read from the output ALREADY IN HAND, never a second probe of the same unit: two
    # probes are two answers to one question, and the raw text still leaves this function
    # without being stored. The parser fails CLOSED, so an unrecognised shape becomes
    # `read: False` carrying the reason, never a silent default that reads as evidence.
    try:
        job = acceptance.parse_loaded_job(r.stdout)
        rec["loaded_job"] = {
            "read": True,
            "working_directory": job["working_directory"],
            "arguments": [_clean(a, gaps, f"unit {label} argument") for a in job["arguments"]],
            "config_environment": _clean(job["config_environment"] or "", gaps,
                                         f"unit {label} config environment") or None,
        }
    except ValueError as e:
        rec["loaded_job"] = {"read": False, "why": str(e)}
        gaps.append(f"unit {label}: loaded, and its program could not be read — {e}. The "
                    f"unit is running SOMETHING and this bundle cannot say what, which is "
                    f"UNREAD and not 'no arguments'.")

    missing = [k for k in ("state", "pid", "last_exit_code") if k not in rec]
    if missing:
        gaps.append(f"unit {label}: loaded, but `launchctl print` did not report "
                    f"{', '.join(missing)} in a shape this parser recognises. Absent here "
                    f"means UNREAD, not zero.")
    return rec


def _log_paths(system: str, entry: dict, label: str) -> dict:
    """Where a unit's stdout/stderr go, derived from the plist rather than assumed.

    NOTHING DECLARES A LOG PATH. The registry has no key for one and the contract schema
    is `additionalProperties: false` — and it is read from an immutable tag, so a key
    added today could never be set for any release already cut (the argument that put
    `cutover` in the registry instead). What does carry the answer is the plist the unit
    was bootstrapped from, in `StandardOutPath` / `StandardErrorPath`.

    Two plists can answer, and they are different questions. The INSTALLED one in
    `~/Library/LaunchAgents` is what launchd actually loaded; the one in the deploy tree is
    what the current tag would install. The installed one is preferred because this is a
    question about the process that is running. When they disagree that is itself worth
    seeing, so both are reported.
    """
    out: dict[str, Any] = {"label": label}
    installed = runner.launch_agents_dir() / f"{label}.plist"
    candidates = [("installed", installed)]
    try:
        candidates.append(("tag", runner.unit_plist(system, entry, label)))
    except Exception:                                                # noqa: BLE001
        pass
    for which, path in candidates:
        try:
            data = plistlib.loads(Path(path).read_bytes())
        except Exception as e:                                       # noqa: BLE001
            out[which] = {"plist": str(path), "read": False, "why": e.__class__.__name__}
            continue
        out[which] = {"plist": str(path), "read": True,
                      "stdout": data.get("StandardOutPath"),
                      "stderr": data.get("StandardErrorPath")}
    return out


def _tail(path: Optional[str], gaps: list, where: str) -> dict:
    """The last `LOG_TAIL_LINES` of one log, redacted, naming the file it was cut from."""
    if not path:
        return {"path": None, "read": False, "why": "the plist declares no path for this "
                                                    "stream, so launchd discards it"}
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"path": str(p), "read": False,
                "why": "declared by the plist and absent on disk — the unit has not "
                       "written to this stream (or something removed it)"}
    except OSError as e:
        gaps.append(f"{where}: {p} could not be read ({e.__class__.__name__}), so this "
                    f"stream is UNREAD rather than empty.")
        return {"path": str(p), "read": False, "why": f"unreadable: {e.__class__.__name__}"}
    if not text.strip():
        return {"path": str(p), "read": True, "empty": True,
                "why": "the file exists and is empty — the unit ran and said nothing, "
                       "which is not the same as never having run"}
    cut = evidence.clip(text.rstrip("\n"), LOG_TAIL_LINES, keep="tail", unit="lines",
                        full_text_at=str(p))
    return {"path": str(p), "read": True, "empty": False,
            "tail": _clean(cut, gaps, where)}


def _verify(system: str, entry: dict, contract: dict, cut_over: Optional[bool],
            gaps: list, run_it: bool) -> dict:
    """The contract's `verify`, EXECUTED — the one thing in this module that is not a read.

    It earns the exception because it is the only probe that answers the question an
    operator actually has. Everything else here reports what is installed and what is
    loaded; `verify` is the system's own Architect's answer to *is it working*, and a
    diagnosis without it can say a unit is running while the thing it serves is dead.

    It is not free, and the bundle says so rather than folding it in with the observations.
    A `verify` is declared by the deployed tag and this module does not know what is in it;
    ADR-0103 D5 calls it a post-restart aliveness check, which is a contract about intent,
    not a guarantee about side effects. `--no-verify` exists for the caller who wants the
    observations alone, and what it produces says `skipped` rather than passing silently.

    WHEN THE UNITS ARE NOT CUT OVER the result is reported and qualified rather than
    suppressed: it ran in the deploy tree, and the process serving requests is somewhere
    else, so a pass here is a statement about code that is installed and not about code
    that is running. The runner records exactly this distinction on a deploy and it would
    be a strange diagnosis that dropped it.
    """
    spec = (contract or {}).get("verify")
    if not spec:
        return {"declared": False,
                "why": "the contract declares no verify — recorded as unchecked, never "
                       "as passed"}
    if not run_it:
        return {"declared": True, "skipped": "--no-verify: the command was not run, so "
                                             "this says nothing about whether it passes"}
    rec = runner.run_gate(system, entry, spec, "verify", lambda _m: None, dry_run=False)
    rec["executed"] = True
    if rec.get("detail"):
        rec["detail"] = _clean(rec["detail"], gaps, "verify output")
    if cut_over is False:
        rec["qualified"] = ("the units do not point at the deploy tree, so this ran "
                            "against installed code that nothing is currently executing")
    return rec


# ── WI-0366's host evidence ──────────────────────────────────────────────────────

def _acceptance(units: dict, tree: dict, gaps: list) -> Optional[dict]:
    """WI-0366's host evidence, emitted BY the host instead of asked for by hand.

    WHAT IT IS. WI-0366's acceptance names a specific list — the loaded WorkingDirectory
    and program arguments of all three federation jobs, the running release, the resolved
    `poga` target, the member lookup and worker liveness. Every one of those was already
    collectable: `curate/mailacceptance.py::snapshot` has read them since it was written.
    What did not exist was a CALLER. So the evidence was only ever produced by a person
    running a command on the Runner, which is the shape the 2026-09-13 ruling forbids and
    the reason the acceptance has sat open. This attaches the collector to the sweep that
    already runs there, and returns it down the channel `post_if_changed` already owns.

    SCOPED BY THE CONTRACT, NEVER BY A SYSTEM NAME. The subject is the three job labels
    `mailacceptance.LABELS` declares, and a system whose contract names none of them gets
    `None` here — no section at all rather than an empty one. That is the distinction this
    whole module exists to keep: an empty acceptance block would read as a finding about a
    system nobody asked the question of. Reading the labels from the collector also means a
    fourth federation job added there is collected here without a second edit.

    A GAP IS THE POINT, not an exception to it. An emitter that reported "collected" while
    a unit was unloaded, or while `launchctl` answered in a shape nothing could parse,
    would be a rubber stamp — the acceptance would then rest on this module's green line
    instead of on the parsed evidence WI-0366 actually asks for, which is precisely the
    substitution its OWNERSHIP clause refuses ("a log line saying installed is NOT a
    loaded-unit probe"). So `complete` is false whenever anything named here could not
    answer, each reason is named in `gaps`, and `complete` is IN THE DIGEST: a job dropping
    out of the loaded set is a state change and posts.
    """
    rows = {r.get("label"): r for r in units.get("rows") or []}
    labels = sorted(label for label in rows if label in acceptance.LABELS)
    if not labels:
        return None

    mine: list = []
    jobs: dict = {}
    for label in labels:
        row = rows[label]
        job = row.get("loaded_job") or {}
        rec: dict = {"loaded": bool(row.get("loaded"))}
        if not row.get("loaded"):
            rec["why"] = row.get("detail", "not loaded")
            mine.append(f"{label}: not loaded in {runner.launchctl_domain()}, so there is "
                        f"no loaded WorkingDirectory and no program vector to report for "
                        f"it. NOT LOADED is not a release running.")
        elif not job.get("read"):
            rec["why"] = job.get("why", "the loaded program could not be read")
            mine.append(f"{label}: loaded, and `launchctl print` did not answer in a shape "
                        f"the parser recognises — {rec['why']}.")
        else:
            rec.update(working_directory=job.get("working_directory"),
                       arguments=job.get("arguments"),
                       config_environment=job.get("config_environment"),
                       points_at_deploy_tree=row.get("points_at_deploy_tree"))
            if not job.get("config_environment"):
                mine.append(f"{label}: loaded with no POGA_FEDERATION_CONFIG in its "
                            f"environment, so which service configuration it runs against "
                            f"is not readable from the loaded unit.")
        jobs[label] = rec

    absent = sorted(set(acceptance.LABELS) - set(labels))
    if absent:
        mine.append("this system's contract declares only " + ", ".join(labels) + ", so "
                    + ", ".join(absent) + " was never asked about here. WI-0366's "
                    "acceptance is about all three federation jobs together.")

    out: dict = {"declared": labels, "jobs": jobs,
                 "release": {"deployed_tag": tree.get("deployed_tag"),
                             "note": tree.get("note")}}
    if not tree.get("deployed_tag"):
        mine.append("the deploy tree is not detached at a tag, so there is no running "
                    "release to name and the loaded units above could be running anything.")

    # `production.resolve` returning None is not a failure — it means this machine never
    # selected a production service, which is the DEVELOPMENT path. It is still a gap for
    # this purpose: worker liveness and the member lookup are both questions about a
    # production service, and with none selected they were not asked.
    try:
        roots = production.resolve(runner.REPO_ROOT)
    except Exception as e:                                           # noqa: BLE001
        roots, host_why = None, f"the production configuration is unreadable — {e}"
    else:
        host_why = ("this machine selects no production service "
                    f"(`{production.CONFIG_ENV}` is unset)")
    if roots is None:
        out["host"] = {"read": False, "why": host_why}
        mine.append(f"the resolved `poga` symlink, worker liveness and member lookup were "
                    f"NOT COLLECTED: {host_why}. Three of the acceptance's named inputs "
                    f"are missing from this bundle and none is reported as healthy.")
    else:
        host = acceptance.observe_host(roots, runner.cli_link())
        mine.extend(host.pop("gaps"))
        out["host"] = {"read": True, **host}

    out["gaps"] = mine
    out["complete"] = not mine
    gaps.extend(mine)
    return out


# ── the bundle ───────────────────────────────────────────────────────────────────

def snapshot(system: str, run_verify: bool = True) -> dict:
    """Everything readable about one deployed system on THIS machine. Never writes."""
    gaps: list[str] = []
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "diagnose",
        "read_only": True,
        "system": system,
        "machine": socket.gethostname().split(".")[0],
        "collected_at": _now(),
        "gaps": gaps,
    }

    try:
        reg = runner.load_registry()
    except runner.DeployError as e:
        bundle["registered"] = None
        gaps.append(f"registry: {e}")
        return bundle
    entry = reg.get("systems", {}).get(system)
    if entry is None:
        # Absent is its own answer and never "nothing to diagnose" — the same distinction
        # `registry_entry` refuses to collapse.
        bundle["registered"] = False
        bundle["stopped_at"] = "registry"
        bundle["why"] = (f"'{system}' has no row in {runner.registry_path()}, so nothing "
                         f"here declares how it deploys. That is NOT 'up to date' and it "
                         f"is not 'healthy'. Known: "
                         f"{', '.join(sorted(reg.get('systems', {}))) or '(none)'}")
        return bundle
    bundle["registered"] = True

    tree = runner.deploy_tree(system)
    if not (tree / ".git").exists():
        bundle["tree"] = {"path": str(tree), "present": False,
                          "why": "never deployed on this machine — there is no tree"}
        bundle["ledger"] = _ledger(system, None, gaps)
        # STOP HERE, and say so. The contract is read from the deployed tag, the units
        # come from the contract, and the logs come from the units — with no tree there is
        # no tag, so none of those questions was ASKED. Rendering their empty defaults
        # would print "no units" and "verify not declared", which read as findings about
        # the system and are findings about nothing at all
        # ([`declare-what-a-check-assumes`]). This is the collapse this module exists to
        # prevent, and the first live run of it produced exactly that output.
        bundle["stopped_at"] = "tree"
        return bundle

    tag = runner.deployed_tag(system, entry)
    bundle["tree"] = {
        "path": str(tree), "present": True, "deployed_tag": tag,
        "newest_tag_known_here": runner.newest_tag(tree),
        "note": None if tag else
        ("HEAD is on a branch, not detached at a tag — so this runner has not deployed "
         "into this tree. A freshly cloned tree looks exactly like this."),
    }
    bundle["ledger"] = _ledger(system, tag, gaps)

    con = _contract(system, entry, tag, gaps)
    contract = con.get("contract") or {}
    bundle["contract"] = {k: v for k, v in con.items() if k != "contract"}
    bundle["contract"]["units"] = contract.get("units", [])
    bundle["contract"]["restart"] = contract.get("restart")
    bundle["contract"]["declares_smoke"] = bool(contract.get("smoke"))
    bundle["contract"]["declares_verify"] = bool(contract.get("verify"))
    bundle["contract"]["declares_apply"] = bool(contract.get("apply"))

    units = contract.get("units", [])
    cut_over: Optional[bool] = None
    if units:
        expected = str(runner.work_dir(system, entry))
        rows = []
        for label in units:
            rec = _launchctl(label, gaps)
            wd = rec.get("working_directory")
            rec["points_at_deploy_tree"] = (
                None if not rec.get("loaded") or wd is None
                else (wd == expected or wd.startswith(expected + "/")))
            rows.append(rec)
        bundle["units"] = {"expected_working_directory": expected, "rows": rows}
        cut_over = bool(rows) and all(r.get("points_at_deploy_tree") for r in rows)
        bundle["units"]["cut_over"] = cut_over
    elif contract.get("apply"):
        bundle["units"] = {"rows": [], "why": (
            "this system declares an `apply` command and no launchd units, so there is no "
            "local process to ask about. Its `verify` is the only liveness answer "
            "available from here.")}
        gaps.append("units: none declared — liveness for an apply-strategy system lives "
                    "on whatever host actually runs it, which this machine cannot reach.")
    else:
        bundle["units"] = {"rows": [], "why": "the contract declares no units"}

    logs = []
    for label in units:
        where = _log_paths(system, entry, label)
        src = where.get("installed") if where.get("installed", {}).get("read") \
            else where.get("tag", {})
        logs.append({
            "label": label,
            "plists": where,
            "stdout": _tail(src.get("stdout"), gaps, f"{label} stdout"),
            "stderr": _tail(src.get("stderr"), gaps, f"{label} stderr"),
        })
    if units and not logs:
        gaps.append("logs: units are declared and no log location could be derived "
                    "from any plist.")
    bundle["logs"] = logs

    bundle["verify"] = _verify(system, entry, contract, cut_over, gaps, run_verify)

    acc = _acceptance(bundle["units"], bundle["tree"], gaps)
    if acc is not None:
        bundle["acceptance"] = acc
    return bundle


def digest(bundle: dict) -> dict:
    """The STABLE facts, for deciding whether anything has actually changed.

    Deliberately excludes every log tail, every timestamp and every byte of command
    output. Those move on their own — a log gains a line a second — and a digest that
    included them would report a change on every sweep, which is the failure the posting
    rule exists to avoid, rebuilt inside the thing meant to prevent it.

    It excludes `gaps` for a subtler reason: a gap is a statement about the PROBE, not
    about the system, and a bundle that re-posted because a log file happened to be
    unreadable this minute would be reporting on itself. The acceptance block's own
    `complete` flag is the one deliberate exception, and it is not a gap: it says whether
    the host evidence WI-0366 asks for was obtained at all, and a job dropping out of the
    loaded set is a change in the SYSTEM that happens to be visible as a gap.

    EVERY VALUE HERE SURVIVES A JSON ROUND TRIP, and nothing nested is a tuple. The
    comparison in `post_if_changed` is between this dict and one read back with
    `json.loads`, which returns lists — so a tuple anywhere inside could never compare
    equal to its own recorded form, and the sweep would post an identical bundle every 600
    seconds forever. That is 144 a day, the exact failure the posting rule exists to
    prevent, arriving through the mechanism meant to prevent it. The fixture that would
    have caught it declares no units, so the only list in here was empty in every test
    (WI-0396); `test_the_digest_survives_the_round_trip_it_is_compared_across` is the
    control that now fails instead.
    """
    units = sorted(
        [r.get("label"), bool(r.get("loaded")), r.get("points_at_deploy_tree"),
         r.get("last_exit_code")]
        for r in (bundle.get("units") or {}).get("rows", []))
    led = bundle.get("ledger") or {}
    refusal = (led.get("record") or {}).get("refusal") or {}
    ver = bundle.get("verify") or {}
    acc = bundle.get("acceptance")
    host = (acc or {}).get("host") or {}
    return {
        "registered": bundle.get("registered"),
        "tree_present": (bundle.get("tree") or {}).get("present"),
        "deployed_tag": (bundle.get("tree") or {}).get("deployed_tag"),
        "contract_valid": (bundle.get("contract") or {}).get("valid"),
        "ledger_status": (led.get("record") or {}).get("status") if led.get("present")
        else ("UNREADABLE" if led.get("readable") is False else None),
        # WI-0412. `ledger_status` alone cannot move when a stale green is dragged forward
        # — the status IS the thing that did not change, which is the defect. Carrying the
        # tag it was decided for means the digest differs the moment a status stops being
        # about the tag on the tree, so `post_if_changed` speaks up instead of comparing
        # equal across the exact transition that matters.
        "ledger_status_tag": (led.get("record") or {}).get("status_tag")
        if led.get("present") else None,
        # WI-0410. WITHOUT THIS LINE THE WHOLE ITEM IS HALF-BUILT. The digest is what
        # `post_if_changed` compares to decide whether anything is worth mailing, and a
        # refusal changes no other key in it: `status`, `deployed_tag`, `units` and the
        # verify block all keep describing the version that is still running, because the
        # refusal deliberately did not touch them. So a bundle that now CARRIES the refusal
        # would still have compared equal to yesterday's, and the sweep would have stayed
        # silent about it — the bundle would be right and nobody would be told, which is
        # the same outcome the item is about reached one step later.
        #
        # The tag rather than a bare bool, so a second refusal for a DIFFERENT tag is also
        # a change; the timestamp is deliberately NOT in here, or every sweep would differ
        # from the last and the digest would lose the ability to say "unchanged" at all.
        #
        # `"unresolved"` rather than the attempted tag's own None: a refusal raised before
        # tag resolution has `attempted: null`, and passing that through would digest
        # IDENTICALLY to "no refusal at all" — the exact collapse this key exists to end,
        # rebuilt one level down. Three states, three values: None, "unresolved", "<tag>".
        "deploy_refused": (
            (refusal.get("attempted") or "unresolved") if refusal else None),
        "units": units,
        "verify_ok": ver.get("ok"),
        "verify_exit": ver.get("exit"),
        "acceptance": None if acc is None else {
            "complete": acc.get("complete"),
            "jobs": sorted([label, rec.get("loaded"), rec.get("working_directory"),
                            list(rec.get("arguments") or []),
                            rec.get("config_environment")]
                           for label, rec in (acc.get("jobs") or {}).items()),
            "poga_target": (host.get("poga") or {}).get("target"),
            "worker_status": (host.get("worker") or {}).get("status"),
            "members": sorted((host.get("member_lookup") or {}).get("members") or []),
        },
    }


# ── rendering ────────────────────────────────────────────────────────────────────

def _yn(value, yes="yes", no="NO", unknown="unknown"):
    return unknown if value is None else (yes if value else no)


def _gate_line(g: dict) -> str:
    """A gate record in one line, keeping all five of its outcomes apart.

    `runner.run_gate` is careful that a gate which was never declared reads as UNCHECKED
    and not as passed, and that a timeout (`exit: None`) reads differently from a non-zero
    exit. A renderer that collapsed either distinction would undo the care at the last
    step, which is where it is least likely to be noticed.
    """
    if not g.get("declared"):
        return "NOT DECLARED by the contract — unchecked, never 'passed'"
    if g.get("skipped"):
        return f"skipped — {g['skipped']}"
    if g.get("dry_run"):
        return "not run (dry run)"
    if g.get("exit") is None and "ok" in g:
        return f"TIMED OUT — {g.get('detail', 'no detail')}"
    if "ok" not in g:
        return "declared, and this record says nothing about whether it ran"
    return ("PASS" if g.get("ok") else "FAIL") + f" (exit {g.get('exit')})"


def render(bundle: dict) -> str:
    """The bundle as prose. Same content as the JSON — this is what a person reads."""
    L: list[str] = []
    sysid, machine = bundle["system"], bundle["machine"]
    L += [f"# {sysid}: diagnosis from {machine}", "",
          f"- **Collected:** {bundle['collected_at']}",
          f"- **Collected by:** `deploy/diagnose.py` — read-only, no model, no network "
          f"(ADR-0103 D4). Nothing here was changed by looking at it.", ""]

    if bundle.get("registered") is False:
        L += ["## Not deployable here", "", bundle.get("why", ""), ""]
        return "\n".join(L)
    if bundle.get("registered") is None:
        L += ["## The registry could not be read", "",
              "No system could be resolved, so this bundle reports nothing about "
              f"`{sysid}` — it reports that the question could not be asked.", ""]
        L += _gaps_section(bundle)
        return "\n".join(L)

    tree = bundle.get("tree") or {}
    L += ["## Running", ""]
    if not tree.get("present"):
        L += [f"Never deployed on this machine — there is no tree at `{tree.get('path')}`.",
              ""]
    else:
        L += [f"- **Deployed tag:** {tree.get('deployed_tag') or 'NONE'}"
              + (f" — {tree['note']}" if tree.get("note") else ""),
              f"- **Newest tag known here:** {tree.get('newest_tag_known_here') or '(none)'}"
              f" — read from local refs; nothing was fetched, so a newer tag may exist on "
              f"origin.", ""]

    led = bundle.get("ledger") or {}
    L += ["## Ledger", ""]
    if led.get("readable") is False:
        L += ["UNREADABLE. The running version above is read from the tree and stands; "
              "every other ledger field is unavailable, not absent.", ""]
    elif not led.get("present"):
        L += ["No record — this system has never been deployed by this runner on this "
              "machine. That is `unknown`, never `up to date`.", ""]
    else:
        rec = led.get("record", {})
        # `message` is first because it is the only field that EXPLAINS a status rather
        # than restating it: a `contract-invalid` row's message is the whole diagnosis,
        # and the first live run of this renderer dropped it.
        for key in ("status", "message", "current", "previous", "attempted", "trigger",
                    "deployed_at", "failed_at", "checked_at"):
            if rec.get(key) is not None:
                L.append(f"- **{key}:** {rec[key]}")
        for note in rec.get("state_notes") or []:
            L.append(f"- **state:** {note}")
        cut = rec.get("cutover") or {}
        if cut:
            L.append(f"- **cutover:** cut_over={_yn(cut.get('cut_over'))}; "
                     f"pending={[u.get('unit') for u in cut.get('pending') or []] or 'none'}; "
                     f"unloaded={cut.get('unloaded') or 'none'}")
        for warn in ("ledger_disagrees_with_tree", "stale_cutover_event", "stale_status",
                     "unbound_status"):
            if led.get(warn):
                L.append(f"- **STALE:** {led[warn]}")
        # ITS OWN LABEL, not `STALE`. A stale field is one that WAS true and has been
        # overtaken; an outstanding refusal is true right now and is the most actionable
        # line in the bundle. Filing it under the same word as "this cutover_event is old
        # news" would bury the one sentence a reader of a dormant system needs (WI-0410).
        if led.get("deploy_refused"):
            L.append(f"- **REFUSED:** {led['deploy_refused']}")
        L.append("")

        # THE GATES THE LAST DEPLOY SAW, kept apart from the verify this bundle ran a
        # moment ago. They answer different questions — one is what was true when the
        # release went on, the other is what is true now — and a reader who merged them
        # would take a months-old pass for a current one.
        gates = [(k, rec.get(k)) for k in ("smoke", "verify", "rollback_verify")
                 if isinstance(rec.get(k), dict)]
        if gates:
            L += ["**Gates recorded by the last deploy** (not re-run here):", ""]
            for name, g in gates:
                L.append(f"- **{name}:** {_gate_line(g)}")
                if g.get("detail"):
                    L += ["", "    ```", *(f"    {ln}" for ln in
                                           str(g["detail"]).splitlines()), "    ```"]
            L.append("")

    if bundle.get("stopped_at") == "tree":
        L += ["## Everything below was not reached", "",
              "The contract is read from the deployed tag, the units are named by the "
              "contract, and the logs belong to the units. With no tree there is no tag, "
              "so none of those questions was asked. Nothing here reports that this "
              "system has no units, no contract or no verify — it reports that this "
              "machine has no copy of it to look at.", ""]
        L += _gaps_section(bundle)
        return "\n".join(L)

    con = bundle.get("contract") or {}
    L += ["## Contract", ""]
    if not con.get("read"):
        L += [f"Not read — {con.get('why') or 'no reason was recorded, which is itself a defect in this collector'}.", ""]
    else:
        L += [f"- **Valid:** {_yn(con.get('valid'), 'yes', 'NO — ' + str(con.get('why')), 'UNVALIDATED')}",
              f"- **Units:** {', '.join(con.get('units') or []) or '(none declared)'}",
              f"- **Restart:** {con.get('restart')}",
              f"- **Declares smoke / verify / apply:** {_yn(con.get('declares_smoke'))} / "
              f"{_yn(con.get('declares_verify'))} / {_yn(con.get('declares_apply'))}", ""]

    units = bundle.get("units") or {}
    L += ["## Units", ""]
    if not units.get("rows"):
        L += [units.get("why", "none"), ""]
    else:
        L.append(f"Expected working directory: `{units.get('expected_working_directory')}`")
        L.append("")
        for r in units["rows"]:
            L.append(f"- **{r['label']}** — "
                     + ("loaded" if r.get("loaded") else "NOT LOADED: " + r.get("detail", "")))
            if r.get("loaded"):
                L.append(f"    - points at the deploy tree: "
                         f"{_yn(r.get('points_at_deploy_tree'))}"
                         + (f" (runs in `{r['working_directory']}`)"
                            if r.get("working_directory") else ""))
                for key, label in (("state", "state"), ("pid", "pid"),
                                   ("last_exit_code", "last exit code")):
                    if r.get(key) is not None:
                        L.append(f"    - {label}: {r[key]}")
        L += ["", f"**Cut over:** {_yn(units.get('cut_over'))}"
                  + ("" if units.get("cut_over") is not False else
                     " — a unit is running code from somewhere other than the deploy "
                     "tree, so the tag above is installed and not live."), ""]

    ver = bundle.get("verify") or {}
    L += ["## Verify", ""]
    if not ver.get("declared"):
        L += [ver.get("why", "not declared"), ""]
    elif ver.get("skipped"):
        L += [f"Skipped — {ver['skipped']}", ""]
    else:
        L += [f"- **Ran the contract's own verify command** (the one thing in this bundle "
              f"that executed rather than observed).",
              f"- **Result:** {_yn(ver.get('ok'), 'PASS', 'FAIL')}"
              f" (exit {ver.get('exit') if ver.get('exit') is not None else 'TIMED OUT'})"]
        if ver.get("qualified"):
            L.append(f"- **Qualified:** {ver['qualified']}")
        if ver.get("detail"):
            L += ["", "```", ver["detail"], "```"]
        L.append("")

    acc = bundle.get("acceptance")
    if acc is not None:
        L += ["## Host evidence for WI-0366's acceptance", "",
              "Collected here because this is the machine the jobs run on. It is evidence, "
              "never a verdict — `curate/mailacceptance.py::check` decides acceptance and "
              "this cannot, and a reader must authenticate where this bundle came from "
              "before treating any line below as live.", "",
              f"- **Complete:** {_yn(acc.get('complete'), 'yes', 'NO — see the gaps below')}",
              f"- **Running release:** "
              f"{(acc.get('release') or {}).get('deployed_tag') or 'NONE — not at a tag'}",
              ""]
        for label, rec in sorted((acc.get("jobs") or {}).items()):
            L.append(f"- **{label}** — "
                     + ("loaded" if rec.get("loaded") else "NOT LOADED"))
            if rec.get("why"):
                L.append(f"    - {rec['why']}")
            if rec.get("working_directory"):
                L.append(f"    - loaded working directory: `{rec['working_directory']}`")
            if rec.get("arguments") is not None:
                L.append("    - loaded program: `" + " ".join(rec["arguments"]) + "`")
            if rec.get("loaded") and not rec.get("why"):
                L.append(f"    - POGA_FEDERATION_CONFIG: "
                         f"{('`' + rec['config_environment'] + '`') if rec.get('config_environment') else 'NOT SET in the loaded unit'}")
        host = acc.get("host") or {}
        L.append("")
        if not host.get("read"):
            L += [f"The `poga` target, worker liveness and member lookup were NOT "
                  f"collected — {host.get('why')}. Absent, not healthy.", ""]
        else:
            poga = host.get("poga") or {}
            worker = host.get("worker") or {}
            members = host.get("member_lookup") or {}
            L += [f"- **poga resolves to:** `{poga.get('target')}`"
                  f" (symlink: {_yn(poga.get('is_symlink'))}, target exists: "
                  f"{_yn(poga.get('target_exists'))})",
                  f"- **Worker:** {worker.get('status')}"
                  + (f" — {worker['failure']}" if worker.get("failure") else ""),
                  f"- **Member lookup:** {members.get('status')}, "
                  f"{len(members.get('members') or [])} member(s)", ""]

    L += ["## Logs", ""]
    if not bundle.get("logs"):
        L += ["No units, so no unit logs.", ""]
    for entry in bundle.get("logs", []):
        L.append(f"### {entry['label']}")
        L.append("")
        for stream in ("stdout", "stderr"):
            s = entry.get(stream) or {}
            L.append(f"**{stream}** — `{s.get('path') or '(none declared)'}`")
            if not s.get("read"):
                L += ["", s.get("why", "not read"), ""]
            elif s.get("empty"):
                L += ["", s.get("why", "empty"), ""]
            else:
                L += ["", "```", s.get("tail", ""), "```", ""]

    L += _gaps_section(bundle)
    return "\n".join(L)


def _gaps_section(bundle: dict) -> list:
    gaps = bundle.get("gaps") or []
    if not gaps:
        return ["## What this could not see", "",
                "Every probe answered. That is a statement about the probes listed above "
                "and nothing else — a bundle reports what it looked at, never that "
                "there is nothing else wrong.", ""]
    return (["## What this could not see", ""]
            + [f"- {g}" for g in gaps]
            + ["", "Each line above is a question that was ASKED and not answered. None "
                   "of them is a clean result.", ""])


# ── posting ──────────────────────────────────────────────────────────────────────

def _brief(bundle: dict, how: str) -> tuple:
    """The bundle as a deliverable brief. Header shape is WI-0230's, and for its reasons.

    `apply: manual` / `manual-reason: attended`: a diagnosis has nothing to apply, so the
    strict `auto` schema would be a costume, and the other manual route hands the brief to
    the headless adopt-runner, which would spend a session adopting a report that asks for
    no change. A header satisfying neither is bucketed `malformed` by the poller forever,
    and that bucket does not move the exit code — so getting this wrong builds a return
    path whose failure is silent.
    """
    stamp = bundle["collected_at"]
    sysid = bundle["system"]
    digits = "".join(c for c in stamp if c.isdigit())
    edit_id = f"diagnose-{sysid}-{bundle['machine']}-{digits}"
    filename = f"{stamp[:10]}-diagnose-{sysid}.md"
    body = [
        "---",
        "apply: manual",
        "manual-reason: attended",
        "attended-because: a diagnosis reports the state of a deployed system; there is "
        "nothing here to apply",
        f"edit-id: {edit_id}",
        "---",
        "",
        render(bundle),
        "",
        "---",
        "",
        f"Posted by `deploy/diagnose.py` on {bundle['machine']} because this system's "
        f"observable state CHANGED since the last bundle ({how}). A sweep that found "
        f"nothing changed posts nothing — silence here means the stable facts above are "
        f"the same ones the previous bundle carried, not that nobody looked.",
    ]
    return filename, "\n".join(body)


def _digest_path(system: str) -> Path:
    """Where the last posted digest is remembered.

    MACHINE-LOCAL, beside the ledger and for the same reason (ADR-0103 D7): it records
    what THIS machine has already said about what is running HERE, and a tracked file two
    machines both write is WI-0132's defect. Losing it costs one redundant bundle, which
    is why it is safe to keep it somewhere disposable.
    """
    return runner.ledger_dir() / f"{system}-diagnosis.json"


POSTED_PER_TAG = 32


def _posted_for_tag(before, deployed_tag) -> list:
    """Every digest already mailed for THIS deployed tag, oldest first.

    OPS-0010 soak. Comparing with only the LAST digest let a unit whose exit code
    flaps 0/1 between sweeps post every sweep: hundreds of bundles for one real member
    over a few days, five distinct states. A state already reported for the running tag is not news,
    so it is not mailed again; a new tag starts a fresh list, because the same state
    under a new release IS news. The cost, stated: a return to a state already
    reported for this tag (fail → ok → fail) is not re-mailed. The ledger and the
    next new state still say where the system is.

    A pre-list record is one bare digest; it reads as a list of that one.
    """
    if not isinstance(before, dict):
        return []
    if before.get("schema") != 2:
        return [before] if before.get("deployed_tag") == deployed_tag else []
    if before.get("deployed_tag") != deployed_tag:
        return []
    return [d for d in before.get("posted") or [] if isinstance(d, dict)]


def post_if_changed(system: str, log=print, run_verify: bool = True,
                    dry_run: bool = False) -> dict:
    """Collect, and mail the bundle only if the stable state moved. Never raises.

    EVERY FAILURE IS LOGGED AND SWALLOWED. The caller is a sweep that has real work to do;
    a diagnosis that could not be written must not turn a successful deploy run into a
    failed one. This is the same rule `outbox.publish` states for the same reason.
    """
    try:
        bundle = snapshot(system, run_verify=run_verify)
    except Exception as e:                                           # noqa: BLE001
        log(f"diagnose: {system}: could not collect — {e.__class__.__name__}: {e}")
        return {"posted": False, "why": f"collection failed: {e}"}

    now = digest(bundle)
    path = _digest_path(system)
    try:
        before = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        before = None
    posted = _posted_for_tag(before, now.get("deployed_tag"))

    if now in posted:
        return {"posted": False, "why": "no change in the stable state", "digest": now}
    how = "first bundle from this machine" if before is None else "stable state changed"

    if dry_run:
        return {"posted": False, "why": "dry run", "would_post": True, "digest": now}

    aid, resolved = runner.recipient_architect(system)
    try:
        filename, text = _brief(bundle, how)
        roots = production.resolve(runner.REPO_ROOT)
        if roots:
            import mailqueue
            dest = mailqueue.enqueue(
                roots, aid, filename, text,
                message_id=f"diagnose:{system}:{bundle['machine']}:{bundle['collected_at']}",
                provenance={"producer": "deploy.diagnose", "system": system})
        else:
            dest = outbox.post(aid, filename, text, root=runner.outbox_dir())
    except (ValueError, OSError) as e:
        # A scrub refusal lands here, and it is the one failure worth saying loudly: it
        # means `_clean` missed a class the gate still catches, and the bundle is NOT sent.
        log(f"diagnose: could not queue {system}'s bundle for {aid} — {e}")
        return {"posted": False, "why": str(e), "architect_id": aid}

    log(f"diagnose: queued {dest.name} for {aid} ({resolved})")
    # The digest is recorded only after the brief is safely queued. Recording it first
    # would mean a failed post still suppressed the next run's attempt, and the state
    # change would never be reported by anyone.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        record = {"schema": 2, "deployed_tag": now.get("deployed_tag"),
                  "posted": (posted + [now])[-POSTED_PER_TAG:]}
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
        tmp.replace(path)
    except OSError as e:
        log(f"diagnose: bundle sent, but the digest could not be recorded ({e}) — the "
            f"next run will post an identical bundle rather than stay silent.")

    if roots:
        return {"posted": True, "architect_id": aid, "brief": dest.name, "queued": True}
    committed = runner.publish_outbox(
        f"chore(outbox): {system} diagnosis from {bundle['machine']}",
        "Written by deploy/diagnose.py on the machine the system runs on (WI-0229). "
        "Read-only: nothing about the system was changed to produce it. The bundle "
        "travels because the outbox is tracked; the facts it reports stay here.",
        log)
    return {"posted": True, "architect_id": aid, "brief": dest.name,
            "committed": committed}


def sweep(log=print, run_verify: bool = True, dry_run: bool = False) -> int:
    """Diagnose every registered system. Returns 0 unless the registry itself is unreadable.

    A per-system failure is NOT an error exit. This runs attached to the deploy sweep, and
    a launchd job that exits non-zero for a condition no re-run can clear is an alarm
    channel that gets muted — the argument `curate/mail-poller.py` already makes about its
    own unroutable bucket. What a failed collection does instead is say so on stdout, and
    leave the previous digest in place so the next run tries again.
    """
    try:
        reg = runner.load_registry()
    except runner.DeployError as e:
        log(f"diagnose: {e}")
        return 1
    for name in sorted(reg.get("systems", {})):
        result = post_if_changed(name, log=log, run_verify=run_verify, dry_run=dry_run)
        if result.get("posted"):
            log(f"diagnose: {name}: bundle sent")
        elif result.get("would_post"):
            log(f"diagnose: {name}: WOULD post (dry run)")
    return 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="poga diagnose",
        description="Read-only evidence about a deployed system (ADR-0103, WI-0229).")
    p.add_argument("system", nargs="?",
                   help="System id from deploy/registry.json. Omit with --all.")
    p.add_argument("--all", action="store_true",
                   help="every registered system")
    p.add_argument("--json", action="store_true",
                   help="the bundle as JSON rather than prose")
    p.add_argument("--no-verify", action="store_true",
                   help="do not run the contract's verify command — the one probe here "
                        "that executes rather than observes")
    p.add_argument("--post", action="store_true",
                   help="mail the bundle if the stable state changed (what the sweep does)")
    p.add_argument("--dry-run", action="store_true",
                   help="with --post, say what would be sent and write nothing")
    args = p.parse_args(argv)

    if not args.system and not args.all:
        p.error("name a system, or pass --all")

    run_verify = not args.no_verify
    if args.post:
        if args.all or not args.system:
            return sweep(run_verify=run_verify, dry_run=args.dry_run)
        r = post_if_changed(args.system, run_verify=run_verify, dry_run=args.dry_run)
        if not r.get("posted"):
            print(f"diagnose: not sent — {r.get('why')}")
        return 0

    names = (sorted(runner.load_registry().get("systems", {}))
             if args.all else [args.system])
    bundles = [snapshot(n, run_verify=run_verify) for n in names]
    if args.json:
        print(json.dumps(bundles if args.all else bundles[0], indent=2, default=str))
    else:
        print("\n\n".join(render(b) for b in bundles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
