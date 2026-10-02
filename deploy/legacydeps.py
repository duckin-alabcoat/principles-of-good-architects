#!/usr/bin/env python3
"""Read-only inventory of what on THIS host still points at an old checkout (WI-0366).

WI-0366's retirement clause asks someone to "verify no remaining federation
launchd/cron/poga dependency uses the old checkout", and `curate/mailacceptance.py`
turns that sentence into an artifact: `legacy_dependencies_inspected` must carry an
EMPTY LIST of remaining dependencies. Nothing in this repo produced that list.

WHY A NEW MODULE AND NOT A FLAG SOMEWHERE. Every existing launchd probe here asks about
a job it already knows the name of — `runner.unit_target` prints one LABEL, and
`diagnose._launchctl` parses named fields out of one LABEL. Three labels are declared in
`mailacceptance.LABELS` and those three are what get looked at. That is the right shape
for "is the deployed thing healthy" and exactly the wrong shape for "is anything still
pointing at the old tree", because the answer to the second question is, by construction,
something nobody declared. A by-label probe can only ever confirm the labels it was
handed; it cannot come back with a name you did not give it. So this module ENUMERATES
first — every plist in the LaunchAgents directory, every label `launchctl list` reports,
every line `crontab -l` prints — and matches second.

WHAT IT MAY NOT DO. It never modifies, disables, unloads, bootouts or deletes anything,
and there is deliberately no flag that would. The only three commands it runs are
`launchctl list`, `launchctl print <target>` and `crontab -l`; every one of them is a
read, they are spelled out in code rather than assembled from arguments, and the child
gets no stdin at all (`crontab` with the wrong argv reads stdin and REPLACES the user's
crontab — that footgun is closed here by construction, not by care).

FAIL CLOSED, AND SAY WHICH KIND OF ANSWER THIS IS. Three outcomes are kept apart and
never folded: a REMAINING dependency, a source read cleanly, and a source that could not
be read. An unparseable plist, a `launchctl print` that fails for a label `launchctl
list` just named, a `crontab -l` that fails for any reason other than "no crontab" — each
is a GAP, and a gap can never be reported as an absent dependency. `remaining == []` is
only evidence when `gaps == []` too, which is why gaps decide the exit status even when
something remaining was also found: an incomplete list of leftovers is not the complete
list `legacy_dependencies_inspected` is asking for.

  exit 0  every source read, nothing remaining   -> the acceptance artifact's empty list
  exit 1  every source read, something remains   -> `remaining` names each one
  exit 2  a source could not be read             -> cannot tell; `gaps` names each one

PATHS ARE COMPARED AS PATHS, RESOLVED, AND CASE-FOLDED. Two traps, both paid for here:
`/a/b` must not match `/a/bc` (component boundary, not substring), and macOS APFS is
case-insensitive by default, so `/Users/x/Projects/Foo` and `/Users/x/projects/Foo` are
ONE directory. WI-0392 is the local receipt for the second one: a production data path
can reach a clone through a symlink stub on a case-insensitive filesystem, against a
written premise that it cannot. So every candidate is compared in its normalized form AND its `realpath` form (the
symlink half), against the old root in both of its forms, exactly and then case-folded
(the APFS half). A case-folded-only hit is reported as such rather than silently equated.

RAW `launchctl print` OUTPUT IS NEVER STORED, only a SHA-256 of it plus the named fields.
`curate/mailacceptance.py` set that rule and `deploy/diagnose.py` restated it: the loaded
job's whole environment can carry credentials that have nothing to do with this question,
and this output is evidence that gets retained. The parsing of those named fields is
`mailacceptance.parse_loaded_job`, imported rather than rewritten — a second parser for
the same format is a second answer to the same question (P16).

stdlib only. Nothing here writes to disk; import caches are disabled below for the same
reason, because a read-only probe that leaves `__pycache__` in a released tree has
written to it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import plistlib
import re
import socket
import subprocess
import sys
from pathlib import Path

# A read-only probe must not leave import caches in the released program tree.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "curate"))
import mailacceptance                                                # noqa: E402

SCHEMA_VERSION = 1
KIND = "legacy-dependency-inspection"

#: Only these labels get a `launchctl print`. The prefix is the federation's own
#: namespace (`mailacceptance.LABELS` is the declared subset of it); everything else
#: `launchctl list` names is counted and declared under `assumptions`, never inspected.
FEDERATION_PREFIX = "com.federation."

TIMEOUT = 15

DEFAULT_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
DEFAULT_LAUNCHCTL = Path("/bin/launchctl")
DEFAULT_CRONTAB = Path("/usr/bin/crontab")
DEFAULT_POGA = Path.home() / ".local" / "bin" / "poga"

STATUS_CLEAN = "clean"
STATUS_REMAINING = "remaining"
STATUS_UNREADABLE = "unreadable"

#: An absolute-path-looking run of characters. Separators that end a path inside a larger
#: string (`PATH=/a:/b`, `--config=/x`) terminate the token; `/` deliberately does not, so
#: a whole path comes back as one token. A RELATIVE reference cannot be recognised here
#: and does not need to be: the old root is absolute, so anything naming it names it
#: absolutely, and a relative argument is reached through a WorkingDirectory that is not.
_PATH_TOKEN = re.compile(r"/[^\s:;,'\"=]*")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _run(argv):
    """One child process, no stdin, captured output. Never raises."""
    try:
        r = subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                           timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        return {"returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "",
                "failure": f"timed out after {TIMEOUT}s"}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "stderr": "",
                "failure": f"{exc.__class__.__name__}: {exc}"}


# ── path comparison ──────────────────────────────────────────────────────────────

def root_forms(path) -> list:
    """Every spelling of one root that a candidate might legitimately be under.

    Both the normalized path and its `realpath` are kept: the old checkout may be named
    directly or reached through a symlink stub, and WI-0392 is this repo's receipt that
    the second one happens in production.
    """
    raw = os.path.normpath(os.path.abspath(str(path)))
    forms = [raw]
    try:
        real = os.path.realpath(raw)
    except OSError:                                                  # pragma: no cover
        real = raw
    if real not in forms:
        forms.append(real)
    return forms


def _is_under(path: str, root: str) -> bool:
    """True when `path` IS `root` or lies beneath it — at a component boundary.

    `/a/bc` is not under `/a/b`. A bare `in` test says it is, and that is the whole
    difference between a path comparison and a substring search.
    """
    if path == root:
        return True
    return path.startswith(root if root.endswith("/") else root + "/")


def _tokens(value: str) -> list:
    out = []
    for token in _PATH_TOKEN.findall(value):
        if token not in out:
            out.append(token)
    return out


def _candidate_forms(token: str) -> list:
    forms = [os.path.normpath(token)]
    try:
        real = os.path.realpath(token)
    except OSError:                                                  # pragma: no cover
        real = None
    if real and real not in forms:
        forms.append(real)
    return forms


def match(value, forms) -> dict:
    """The first path inside `value` that lies under one of `forms`, or None.

    A hit that only survives case-folding is reported with `case_folded: true` rather
    than being quietly equated: on a case-SENSITIVE volume those really are two
    directories, and a reader deciding whether to delete one needs to be told which kind
    of match this was.
    """
    if not isinstance(value, str) or not value:
        return None
    for token in _tokens(value):
        for candidate in _candidate_forms(token):
            for root in forms:
                if _is_under(candidate, root):
                    return {"matched": token, "resolved": candidate,
                            "under": root, "case_folded": False}
                if _is_under(candidate.casefold(), root.casefold()):
                    return {"matched": token, "resolved": candidate,
                            "under": root, "case_folded": True}
    return None


def _scan(fields, forms) -> list:
    hits = []
    for name, value in fields:
        hit = match(value, forms)
        if hit:
            hits.append(dict(hit, field=name, value=value))
    return hits


def _touches(fields, forms) -> bool:
    return bool(forms) and any(match(value, forms) for _, value in fields)


def _remaining(source, where, hits) -> list:
    return [dict(hit, source=source, where=where) for hit in hits]


def _gap(source, where, why) -> dict:
    return {"source": source, "where": where, "why": why}


# ── the sources ──────────────────────────────────────────────────────────────────

def _plist_fields(data) -> list:
    """The declared fields a path can hide in, named one at a time.

    `Program` is read alongside `ProgramArguments` because a plist may use either, and a
    job declared with the one this tool did not read would report as clean.
    """
    fields = []
    if isinstance(data.get("Program"), str):
        fields.append(("Program", data["Program"]))
    if isinstance(data.get("ProgramArguments"), list):
        for index, item in enumerate(data["ProgramArguments"]):
            if isinstance(item, str):
                fields.append((f"ProgramArguments[{index}]", item))
    if isinstance(data.get("WorkingDirectory"), str):
        fields.append(("WorkingDirectory", data["WorkingDirectory"]))
    if isinstance(data.get("EnvironmentVariables"), dict):
        for key in sorted(data["EnvironmentVariables"]):
            value = data["EnvironmentVariables"][key]
            if isinstance(value, str):
                fields.append((f"EnvironmentVariables[{key}]", value))
    for key in ("StandardOutPath", "StandardErrorPath"):
        if isinstance(data.get(key), str):
            fields.append((key, data[key]))
    return fields


def inspect_launch_agents(directory, forms, release_forms):
    """Every `*.plist` directly under the LaunchAgents directory.

    Not recursive: launchd loads the files at the top level of this directory, so a
    deeper walk would report paths launchd never reads as live dependencies.

    An ABSENT directory is a gap, not an empty answer. "Nothing is installed here" and
    "this is not where they are installed" produce the identical observation, and the
    list this feeds is acceptance evidence.
    """
    source = "launch_agents"
    detail = {"directory": str(directory), "plists": []}
    remaining, gaps = [], []
    try:
        entries = sorted(p for p in Path(directory).iterdir() if p.suffix == ".plist")
    except FileNotFoundError:
        gaps.append(_gap(source, str(directory),
                         "the LaunchAgents directory does not exist, so nothing was "
                         "enumerated — absent here cannot be told apart from looking in "
                         "the wrong place"))
        detail["status"] = STATUS_UNREADABLE
        return detail, remaining, gaps
    except OSError as exc:
        gaps.append(_gap(source, str(directory),
                         f"the LaunchAgents directory could not be listed "
                         f"({exc.__class__.__name__})"))
        detail["status"] = STATUS_UNREADABLE
        return detail, remaining, gaps

    for path in entries:
        record = {"plist": str(path)}
        try:
            raw = path.read_bytes()
            data = plistlib.loads(raw)
        except Exception as exc:                                     # noqa: BLE001
            record.update(read=False, why=exc.__class__.__name__)
            detail["plists"].append(record)
            gaps.append(_gap(source, str(path),
                             f"this plist could not be parsed ({exc.__class__.__name__}), "
                             f"so what it declares is UNKNOWN, not absent"))
            continue
        if not isinstance(data, dict):
            record.update(read=False, why="not a plist dictionary")
            detail["plists"].append(record)
            gaps.append(_gap(source, str(path),
                             "this plist is not a dictionary, so its declared paths "
                             "could not be read"))
            continue
        fields = _plist_fields(data)
        hits = _scan(fields, forms)
        record.update(read=True, sha256=_sha(raw.decode("utf-8", errors="replace")),
                      label=data.get("Label") if isinstance(data.get("Label"), str) else None,
                      fields_read=len(fields),
                      references_old_root=bool(hits),
                      references_release=_touches(fields, release_forms))
        detail["plists"].append(record)
        remaining.extend(_remaining(source, str(path), hits))
    detail["status"] = STATUS_REMAINING if remaining else STATUS_CLEAN
    return detail, remaining, gaps


def launchctl_domain() -> str:
    """The per-user domain, spelled as `runner.launchctl_domain` spells it."""
    return "gui/%d" % os.getuid()


def parse_labels(stdout) -> list:
    """The Label column of `launchctl list` — tab-separated PID/Status/Label."""
    labels = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        label = parts[-1].strip()
        if not label or label == "Label":
            continue
        labels.append(label)
    return labels


def inspect_launchd(launchctl, forms, release_forms, run=_run):
    """The label inventory, then the loaded record of each federation job.

    The inventory is what makes this an enumeration rather than a roll call, so a
    `launchctl list` that fails — or that comes back in a shape this parser does not
    recognise — is a gap covering the whole source. Reporting zero labels from an
    unreadable inventory would say "no jobs" where the truth is "no answer".
    """
    source = "launchd"
    domain = launchctl_domain()
    detail = {"command": f"{launchctl} list", "domain": domain, "jobs": []}
    remaining, gaps = [], []

    listing = run([launchctl, "list"])
    detail["list"] = {"returncode": listing["returncode"]}
    if "failure" in listing:
        detail["list"]["failure"] = listing["failure"]
    if listing["returncode"] != 0:
        gaps.append(_gap(source, f"{launchctl} list",
                         "the loaded-job inventory could not be read "
                         f"({listing.get('failure') or 'exit %s' % listing['returncode']}), "
                         "so no label was enumerated"))
        detail["status"] = STATUS_UNREADABLE
        return detail, remaining, gaps

    labels = parse_labels(listing["stdout"])
    detail["labels_total"] = len(labels)
    if not labels:
        gaps.append(_gap(source, f"{launchctl} list",
                         "the inventory parsed to zero labels — the PID/Status/Label "
                         "format this reader expects is not what came back, so this is "
                         "an unread inventory, not an empty one"))
        detail["status"] = STATUS_UNREADABLE
        return detail, remaining, gaps

    federation = [label for label in labels if label.startswith(FEDERATION_PREFIX)]
    detail["federation_labels"] = federation
    for label in federation:
        target = f"{domain}/{label}"
        printed = run([launchctl, "print", target])
        # The raw output can carry the loaded job's whole environment. Only the named
        # fields and a digest of the observation are kept — never the bytes themselves.
        job = {"label": label, "target": target, "returncode": printed["returncode"],
               "raw_output_sha256": _sha(printed["stdout"])}
        if printed["returncode"] != 0:
            job["read"] = False
            detail["jobs"].append(job)
            gaps.append(_gap(source, target,
                             "`launchctl list` named this job but `launchctl print` "
                             "would not report it "
                             f"({printed.get('failure') or 'exit %s' % printed['returncode']})"
                             " — its loaded paths are UNKNOWN"))
            continue
        try:
            loaded = mailacceptance.parse_loaded_job(printed["stdout"])
        except ValueError as exc:
            job["read"] = False
            job["why"] = str(exc)
            detail["jobs"].append(job)
            gaps.append(_gap(source, target,
                             f"the loaded record could not be parsed ({exc})"))
            continue
        fields = [("working directory", loaded["working_directory"])]
        fields += [(f"arguments[{i}]", a) for i, a in enumerate(loaded["arguments"])]
        if loaded.get("config_environment"):
            fields.append(("POGA_FEDERATION_CONFIG", loaded["config_environment"]))
        hits = _scan(fields, forms)
        job.update(read=True, working_directory=loaded["working_directory"],
                   arguments=loaded["arguments"],
                   config_environment=loaded.get("config_environment"),
                   references_old_root=bool(hits),
                   references_release=_touches(fields, release_forms))
        detail["jobs"].append(job)
        remaining.extend(_remaining(source, target, hits))
    detail["status"] = STATUS_REMAINING if remaining else STATUS_CLEAN
    return detail, remaining, gaps


def inspect_cron(crontab, forms, release_forms, run=_run):
    """`crontab -l`, and only ever `-l`.

    Exit 1 with "no crontab" is this user having no cron entries — a clean read, not a
    failure. ANY other non-zero status, or a timeout, is a gap: cron could not be
    enumerated and an empty list from here would be a guess.
    """
    source = "cron"
    detail = {"command": f"{crontab} -l"}
    remaining, gaps = [], []
    result = run([crontab, "-l"])
    detail["returncode"] = result["returncode"]
    if "failure" in result:
        detail["failure"] = result["failure"]
    combined = (result["stdout"] + "\n" + result["stderr"]).lower()

    if result["returncode"] == 1 and "no crontab" in combined:
        detail.update(read=True, no_crontab=True, entries=0, status=STATUS_CLEAN)
        return detail, remaining, gaps
    if result["returncode"] != 0:
        gaps.append(_gap(source, f"{crontab} -l",
                         "cron entries could not be listed "
                         f"({result.get('failure') or 'exit %s' % result['returncode']})"))
        detail.update(read=False, status=STATUS_UNREADABLE)
        return detail, remaining, gaps

    lines = [line for line in result["stdout"].splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    detail.update(read=True, no_crontab=False, entries=len(lines))
    fields = [(f"line[{index}]", line) for index, line in enumerate(lines)]
    hits = _scan(fields, forms)
    detail["references_release"] = _touches(fields, release_forms)
    remaining.extend(_remaining(source, f"{crontab} -l", hits))
    detail["status"] = STATUS_REMAINING if remaining else STATUS_CLEAN
    return detail, remaining, gaps


def inspect_poga(path, forms, release_forms):
    """The `poga` CLI link: whether it is a symlink, and what it resolves to.

    Both the link's own path and its resolved target are compared — a `poga` living
    INSIDE the old checkout is as much a remaining dependency as one pointing into it.
    An absent link is a gap for the same reason an absent LaunchAgents directory is.
    """
    source = "poga"
    detail = {"path": str(path)}
    remaining, gaps = [], []
    try:
        exists = os.path.lexists(str(path))
        detail["exists"] = exists
        if not exists:
            gaps.append(_gap(source, str(path),
                             "there is no poga link at this path, so the CLI entrypoint "
                             "was not inspected — say where it is with --poga"))
            detail["status"] = STATUS_UNREADABLE
            return detail, remaining, gaps
        detail["is_symlink"] = os.path.islink(str(path))
        target = os.path.realpath(str(path))
        detail["resolves_to"] = target
        detail["target_exists"] = os.path.exists(target)
    except OSError as exc:
        gaps.append(_gap(source, str(path),
                         f"the poga link could not be resolved ({exc.__class__.__name__})"))
        detail["status"] = STATUS_UNREADABLE
        return detail, remaining, gaps
    # The target first, because it is the fact an operator acts on; the link's own path
    # second, for the case where `poga` is a plain file living INSIDE the old checkout.
    # Both resolve through the symlink, so a hit they share is ONE finding reported once
    # — two rows for one fact would inflate a list whose length is the whole verdict.
    fields = [("resolves_to", target), ("link", str(path))]
    hits, seen = [], set()
    for hit in _scan(fields, forms):
        if hit["resolved"] in seen:
            continue
        seen.add(hit["resolved"])
        hits.append(hit)
    detail["references_old_root"] = bool(hits)
    detail["references_release"] = _touches(fields, release_forms)
    remaining.extend(_remaining(source, str(path), hits))
    detail["status"] = STATUS_REMAINING if remaining else STATUS_CLEAN
    return detail, remaining, gaps


# ── the report ───────────────────────────────────────────────────────────────────

def inspect(old_root, release_root=None, poga=DEFAULT_POGA,
            launch_agents=DEFAULT_LAUNCH_AGENTS, launchctl=DEFAULT_LAUNCHCTL,
            crontab=DEFAULT_CRONTAB, run=_run) -> dict:
    """Inspect this host and return the WI-0366 inspection record."""
    old = Path(old_root)
    if not old.is_absolute():
        raise ValueError("--old-root must be an absolute path")
    if release_root is not None:
        release = Path(release_root)
        if not release.is_absolute():
            raise ValueError("--release-root must be an absolute path")
        if _is_under(os.path.normpath(str(release)), os.path.normpath(str(old))):
            raise ValueError("the release root must not be the old root or inside it")

    forms = root_forms(old)
    release_forms = root_forms(release_root) if release_root else []

    report = {"schema_version": SCHEMA_VERSION, "kind": KIND, "observed_at": _now(),
              "hostname": socket.gethostname(),
              "old_root": str(old), "old_root_forms": forms,
              "release_root": str(release_root) if release_root else None,
              "read_only": True, "sources": {}, "remaining": [], "gaps": [],
              "assumptions": []}

    collected = [
        ("launch_agents", inspect_launch_agents(launch_agents, forms, release_forms)),
        ("launchd", inspect_launchd(launchctl, forms, release_forms, run=run)),
        ("cron", inspect_cron(crontab, forms, release_forms, run=run)),
        ("poga", inspect_poga(poga, forms, release_forms)),
    ]
    for name, (detail, remaining, gaps) in collected:
        report["sources"][name] = detail
        report["remaining"].extend(remaining)
        report["gaps"].extend(gaps)

    launchd = report["sources"]["launchd"]
    if launchd.get("labels_total"):
        skipped = launchd["labels_total"] - len(launchd.get("federation_labels", []))
        if skipped:
            report["assumptions"].append(
                f"{skipped} loaded job(s) outside {FEDERATION_PREFIX}* were listed but not "
                "printed, so their loaded arguments were not inspected. One installed "
                "under the inspected LaunchAgents directory was still read from its "
                "plist; one loaded from anywhere else was not read at all.")
    report["assumptions"].append(
        "System-domain jobs (/Library/LaunchAgents, /Library/LaunchDaemons) and other "
        "users' crontabs are outside this per-user inspection.")
    report["assumptions"].append(
        "A dependency expressed relatively — a path this tool cannot recognise as "
        "absolute — is found only through the absolute directory it is resolved against.")

    report["verdict"] = ("cannot-tell" if report["gaps"]
                         else STATUS_REMAINING if report["remaining"] else STATUS_CLEAN)
    return report


def exit_code(report) -> int:
    """Gaps outrank findings: an incomplete list of leftovers is not a complete one."""
    if report["gaps"]:
        return 2
    return 1 if report["remaining"] else 0


def render(report) -> str:
    lines = [f"legacy dependency inspection — {report['hostname']} — {report['observed_at']}",
             f"old checkout: {report['old_root']}"]
    if report["release_root"]:
        lines.append(f"release tree: {report['release_root']}")
    lines.append("")
    for name in ("launch_agents", "launchd", "cron", "poga"):
        lines.append(f"  {name:<14} {report['sources'][name].get('status', '?')}")
    lines.append("")
    if report["remaining"]:
        lines.append(f"REMAINING DEPENDENCIES ({len(report['remaining'])}):")
        for item in report["remaining"]:
            folded = "  [matched case-insensitively]" if item["case_folded"] else ""
            lines.append(f"  - {item['source']}: {item['where']}")
            lines.append(f"      {item['field']} = {item['value']}")
            lines.append(f"      matched: {item['matched']}{folded}")
    else:
        lines.append("No remaining dependency was found in the sources that were read.")
    if report["gaps"]:
        lines.append("")
        lines.append(f"COULD NOT BE READ ({len(report['gaps'])}) — this is not a clean bill:")
        for gap in report["gaps"]:
            lines.append(f"  - {gap['source']}: {gap['where']}")
            lines.append(f"      {gap['why']}")
    lines.append("")
    for note in report["assumptions"]:
        lines.append(f"  assumes: {note}")
    lines.append("")
    verdict = {"clean": "CLEAN — every source read, nothing points at the old checkout.",
               "remaining": "REMAINING — the old checkout is still in use; see above.",
               "cannot-tell": "CANNOT TELL — a source could not be read, so an empty list "
                              "here would be a guess."}[report["verdict"]]
    lines.append(verdict)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="WI-0366: which launchd jobs, cron entries or CLI links on THIS "
                    "machine still reference an old checkout. Read-only; it never "
                    "changes, unloads or deletes anything.")
    parser.add_argument("--old-root", required=True,
                        help="absolute path of the checkout being retired")
    parser.add_argument("--release-root", default=None,
                        help="absolute path of the released tree, reported for contrast")
    parser.add_argument("--poga", default=str(DEFAULT_POGA),
                        help=f"the poga CLI link (default {DEFAULT_POGA})")
    parser.add_argument("--json", action="store_true",
                        help="emit the inspection record as JSON instead of text")
    parser.add_argument("--launch-agents", default=str(DEFAULT_LAUNCH_AGENTS),
                        help=f"LaunchAgents directory (default {DEFAULT_LAUNCH_AGENTS})")
    parser.add_argument("--launchctl", default=str(DEFAULT_LAUNCHCTL),
                        help=f"launchctl binary (default {DEFAULT_LAUNCHCTL})")
    parser.add_argument("--crontab", default=str(DEFAULT_CRONTAB),
                        help=f"crontab binary (default {DEFAULT_CRONTAB})")
    args = parser.parse_args(argv)

    try:
        report = inspect(args.old_root, release_root=args.release_root, poga=args.poga,
                         launch_agents=args.launch_agents, launchctl=args.launchctl,
                         crontab=args.crontab)
    except ValueError as exc:
        print(f"CANNOT INSPECT: {exc}", file=sys.stderr)
        print("Nothing was established — this is not a clean result.", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
