#!/usr/bin/env python3
"""Read-only WI-0366 observations and acceptance-gap report.

Snapshot runs local read-only probes and writes JSON to stdout. It never fetches,
contacts another host, delivers mail, changes services, or starts the seven-day clock.
Fixture observations cannot satisfy live requirements. Check returns readiness for
human review only: supplied artifacts cannot authenticate their own origin or approve
promotion, retirement, acceptance, or an operational-obligation status change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

# A read-only probe must not leave import caches in the released program tree.
sys.dont_write_bytecode = True

import mailqueue
import production

ROOT = Path(__file__).resolve().parent.parent
LABELS = {"com.federation.deploy-sweep": "deploy/runner.py",
          "com.federation.adopt-runner": "curate/adopt-runner.py",
          "com.federation.mail-poller": "curate/mailworker.py"}
REHEARSAL_CHECKS = ("migration", "second_release", "both_directions", "concurrent_producers",
                    "lost_publication_confirmation", "worker_restart", "duplicate_delivery",
                    "rollback", "code_unchanged", "old_checkout_unchanged")


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return stamp


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def serialized(value):
    """Exact newline-terminated wire bytes emitted by the snapshot command."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(value, size=64):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{%d}" % size, value))


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest requires schema_version: 1")
    hosts = manifest.get("hosts", {})
    releases = manifest.get("releases", [])
    if not isinstance(hosts, dict) or len(hosts) != 2 or len(releases) != 2:
        raise ValueError("manifest must name exactly two hosts and two releases")
    if len({release["commit"] for release in releases}) != 2:
        raise ValueError("the second release must have a different commit")
    for release in releases:
        if not _digest(release.get("commit"), 40) or not release.get("tag"):
            raise ValueError("release needs a tag and full commit hash")
        if not release.get("approval_evidence"):
            raise ValueError("release approval evidence must be explicitly referenced")
        _date(release["approved_at"])
    age_limit = manifest.get("max_observation_age_seconds", 1800)
    if type(age_limit) is not int or not 30 <= age_limit <= 86400:
        raise ValueError("max_observation_age_seconds must be an integer between 30 and 86400")
    if _date(releases[0]["approved_at"]) > _date(releases[1]["approved_at"]):
        raise ValueError("release approval records must be in chronological order")
    all_labels = set()
    hostnames = set()
    refs = set()
    for host, spec in hosts.items():
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", host):
            raise ValueError("invalid host ID")
        if not spec.get("hostname") or spec["hostname"] in hostnames:
            raise ValueError("each host requires a distinct actual hostname")
        hostnames.add(spec["hostname"])
        for key in ("code_root", "old_code_root", "poga_path", "config_path"):
            if not Path(spec[key]).is_absolute():
                raise ValueError(f"host {host} {key} must be absolute")
        if spec["code_root"] == spec["old_code_root"] or \
                Path(spec["old_code_root"]) in Path(spec["code_root"]).parents:
            raise ValueError("released and old program roots must differ")
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", spec.get("owned_ref", "")):
            raise ValueError("host owned_ref must be explicit")
        if spec["owned_ref"] in refs:
            raise ValueError("hosts must own different transport refs")
        refs.add(spec["owned_ref"])
        targets = spec.get("services", [])
        labels = set()
        for target in targets:
            match = re.fullmatch(r"(?:user|gui)/[0-9]+/(com\.federation\.[a-z-]+)", target)
            if not match or match.group(1) not in LABELS:
                raise ValueError("only the three declared federation job labels are accepted")
            labels.add(match.group(1))
        if len(labels) != len(targets) or "com.federation.mail-poller" not in labels:
            raise ValueError("each host requires exactly one declared mail-poller target")
        all_labels.update(labels)
    if all_labels != set(LABELS):
        raise ValueError("manifest must cover all three federation jobs across the two hosts")
    probes = manifest.get("probes", [])
    ids = set()
    for probe in probes:
        if not probe.get("message_id") or probe["message_id"] in ids:
            raise ValueError("probe identities must be distinct and explicit")
        ids.add(probe["message_id"])
        if probe.get("sender") not in hosts or probe.get("recipient") not in hosts or \
                probe["sender"] == probe["recipient"] or not probe.get("destination"):
            raise ValueError("probe must name distinct declared sender/recipient hosts and an inbox destination")
        if probe.get("release") not in {item["commit"] for item in releases}:
            raise ValueError("probe release is not one of the two approved releases")
    return manifest


def _run(argv):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    try:
        result = subprocess.run(argv, capture_output=True, timeout=15, env=env)
        return {"returncode": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace")}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "stdout": "", "failure": type(exc).__name__}


def parse_loaded_job(output):
    """Fail closed when launchctl's diagnostic format is absent or changes."""
    cwd = re.search(r"^\s*working directory = (.+?)\s*$", output, re.MULTILINE)
    block = re.search(r"^\s*arguments = \{\s*\n(.*?)^\s*\}", output, re.MULTILINE | re.DOTALL)
    if not cwd or not block:
        raise ValueError("loaded WorkingDirectory and arguments are not readable")
    args = [line.strip() for line in block.group(1).splitlines() if line.strip()]
    if not args:
        raise ValueError("loaded arguments are empty")
    cfg = re.search(r"^\s*POGA_FEDERATION_CONFIG\s*(?:=>|=)\s*(.+?)\s*$", output, re.MULTILINE)
    return {"working_directory": cwd.group(1), "arguments": args,
            "config_environment": cfg.group(1) if cfg else None}


def _member_lookup(roots):
    import deliver
    repos = deliver.locate_repos(production_roots=roots)
    return {"status": "observed", "members": sorted(repos), "count": len(repos)}


def observe_host(roots, poga_path, *, lookup=_member_lookup):
    """Resolved entrypoint, worker liveness and member lookup for THIS host. No manifest.

    The seam `snapshot` collects these three through, and the one `deploy/diagnose.py`
    emits them from on the host's own schedule (WI-0396). A manifest names two hosts, two
    approved releases and the directional probes between them; a machine reporting what it
    can see about itself every ten minutes has none of that in hand and must not need it to
    answer. So the manifest-dependent half stays in `snapshot` below and this half takes
    only what it actually reads — which is why it is an EXTRACTION and not a second
    collector: one implementation, two callers, and no way for the two to disagree about
    what the host looks like.

    NOTHING HERE RETURNS A VERDICT. `check` still owns acceptance and this still cannot
    grant it. What it does own is `gaps`: every probe that could not answer is NAMED rather
    than left out, because a collector that silently omitted an unreadable probe would
    report "looked and found nothing" in the same shape as "never looked".
    """
    gaps = []
    link = Path(poga_path)
    poga = {"path": str(link), "is_symlink": link.is_symlink(),
            "target": str(link.resolve()), "target_exists": link.resolve().is_file()}
    if not poga["is_symlink"]:
        gaps.append(f"poga at {link} is not a symlink, so nothing here resolves a released "
                    f"entrypoint. Absent is not the same as pointing at the release.")
    elif not poga["target_exists"]:
        gaps.append(f"poga at {link} resolves to {poga['target']}, which is not a file — "
                    f"the link exists and its target does not.")
    import mailworker
    worker = mailworker.read_status(roots)
    if worker.get("status") != "ok":
        gaps.append("worker liveness: the local observation says status "
                    f"{worker.get('status')!r}"
                    + (f" ({worker['failure']})" if worker.get("failure") else "")
                    + ". That is what was read, never an inference that the worker is fine.")
    try:
        members = lookup(roots)
    except Exception as exc:                                         # noqa: BLE001
        members = {"status": "unknown", "failure": type(exc).__name__}
    if members.get("status") != "observed" or not members.get("members"):
        gaps.append("member lookup has no demonstrated result "
                    f"({members.get('failure') or members.get('status')}) — finding zero "
                    f"members is a failure to look, not a fleet that has none.")
    return {"poga": poga, "worker": worker, "member_lookup": members, "gaps": gaps}


def snapshot(roots, config_path, manifest, host_id, *, live=False, run=_run, lookup=_member_lookup):
    validate_manifest(manifest)
    if host_id not in manifest["hosts"]:
        raise ValueError("unknown host ID")
    spec = manifest["hosts"][host_id]
    result = {"schema_version": 1, "kind": "live-local" if live else "fixture",
              "host_id": host_id, "hostname": socket.gethostname(), "observed_at": _now().isoformat(),
              "config_path": str(config_path), "code_root": str(roots.code_root),
              "state_root": str(roots.state_root), "transport_root": str(roots.transport_root),
              "owned_ref": roots.transport.get("owned_ref"), "jobs": {}, "probe_messages": [],
              "gaps": [], "read_only": True}
    def git(*args):
        return run(["git", "-C", str(roots.code_root), "-c", "core.fsmonitor=false", *args])
    head = git("rev-parse", "HEAD")
    branch = git("symbolic-ref", "--quiet", "HEAD")
    tags = git("tag", "--points-at", "HEAD")
    tracked = git("status", "--porcelain", "--untracked-files=no")
    result["code"] = {"head": head["stdout"].strip() if head["returncode"] == 0 else None,
                      "symbolic_ref": branch["stdout"].strip() if branch["returncode"] == 0 else None,
                      "detached": branch["returncode"] == 1,
                      "tags": tags["stdout"].splitlines() if tags["returncode"] == 0 else [],
                      "tracked_clean": tracked["returncode"] == 0 and not tracked["stdout"],
                      "tracked_status_sha256": _hash(tracked["stdout"].encode())}
    if any(item["returncode"] is None for item in (head, branch, tags, tracked)):
        result["gaps"].append("program Git identity probe unavailable")
    for target in spec["services"]:
        probe = run(["/bin/launchctl", "print", target])
        # launchctl's full output can include unrelated environment credentials.
        # Keep only the required loaded fields and a hash of the raw observation.
        observed = {"probe": "launchctl print", "returncode": probe["returncode"],
                    "raw_output_sha256": _hash(probe["stdout"].encode())}
        if probe["returncode"] == 0:
            try:
                observed.update(parse_loaded_job(probe["stdout"]))
            except ValueError as exc:
                observed["failure"] = str(exc)
        result["jobs"][target] = observed
    observed = observe_host(roots, spec["poga_path"], lookup=lookup)
    result["gaps"].extend(observed.pop("gaps"))
    result.update(observed)
    for item in manifest["probes"]:
        if item["sender"] != host_id or item["release"] != result["code"]["head"]:
            continue
        probe = {"message_id": item["message_id"]}
        try:
            env = mailqueue.read(roots.state_path("queue", mailqueue.identity_key(item["message_id"])))
            state = mailqueue.lifecycle(roots, item["message_id"])
            probe.update(destination=env.destination, sha256=env.sha256, lifecycle=state)
        except (OSError, ValueError, TypeError) as exc:
            probe["failure"] = type(exc).__name__
        result["probe_messages"].append(probe)
    # These require before/after and operational coverage; a single snapshot cannot
    # honestly turn no journal entry into zero interventions or certify retirement.
    result["gaps"].extend(["return-channel provenance requires independent verification",
                           "two-release intervention and pending-work coverage requires operational evidence",
                           "complete legacy launchd/cron dependency inspection requires retirement evidence"])
    return result


def _snapshot_gaps(observation, spec, release, now):
    gaps = []
    if observation.get("kind") != "live-local":
        gaps.append("fixture observations are not live evidence")
    if observation.get("hostname") != spec["hostname"]:
        gaps.append("actual hostname does not match declared host")
    if observation.get("code_root") != spec["code_root"] or observation.get("owned_ref") != spec["owned_ref"]:
        gaps.append("code root or owned transport ref differs from declaration")
    if observation.get("config_path") != spec["config_path"]:
        gaps.append("external config path differs from declaration")
    try:
        stamp = _date(observation["observed_at"])
        if stamp < _date(release["approved_at"]) or stamp > now:
            gaps.append("observation predates approval or is in the future")
    except (KeyError, TypeError, ValueError):
        gaps.append("observation time is unknown")
    code = observation.get("code", {})
    if code.get("head") != release["commit"] or release["tag"] not in code.get("tags", []) \
            or code.get("detached") is not True or code.get("tracked_clean") is not True:
        gaps.append("program is not clean detached code at the declared release")
    for key in ("state_root", "transport_root"):
        path = Path(observation.get(key, "."))
        if not path.is_absolute() or path == Path(spec["code_root"]) or Path(spec["code_root"]) in path.parents:
            gaps.append(key + " is not explicitly outside the program root")
    for target in spec["services"]:
        job = observation.get("jobs", {}).get(target, {})
        label = target.rsplit("/", 1)[1]
        if job.get("probe") != "launchctl print" or job.get("returncode") != 0:
            gaps.append(target + ": no successful loaded-unit probe")
            continue
        if not _digest(job.get("raw_output_sha256")) or not isinstance(job.get("arguments"), list) \
                or not job.get("working_directory"):
            gaps.append(target + ": unreadable loaded-unit fields")
            continue
        loaded = job
        args = loaded["arguments"]
        expected = str(Path(spec["code_root"]) / LABELS[label])
        if loaded["working_directory"] != spec["code_root"] or len(args) < 2 or args[1] != expected:
            gaps.append(target + ": loaded code paths differ from release")
        config = observation.get("config_path")
        has_config_arg = any(args[index:index + 2] == ["--config", config] for index in range(len(args) - 1))
        if not config or (loaded.get("config_environment") != config and not has_config_arg):
            gaps.append(target + ": explicit external configuration not loaded")
        if any(spec["old_code_root"] in str(value) for value in [*args, loaded["working_directory"],
                                                                   loaded.get("config_environment", "")]):
            gaps.append(target + ": loaded unit still references old checkout")
    poga = observation.get("poga", {})
    if not poga.get("is_symlink") or not poga.get("target_exists") or \
            poga.get("target") != str(Path(spec["code_root"]) / "poga"):
        gaps.append("poga does not resolve to the released entrypoint")
    worker = observation.get("worker", {})
    if worker.get("status") != "ok" or worker.get("release") != release["commit"]:
        gaps.append("worker has no healthy observation for this release")
    try:
        age = (_date(observation["observed_at"]) - _date(worker["observed_at"])).total_seconds()
        if age < 0 or age > int(worker["stale_after_seconds"]):
            gaps.append("worker observation is stale or from the future")
    except (KeyError, ValueError, TypeError):
        gaps.append("worker observation freshness is unknown")
    lookup = observation.get("member_lookup", {})
    if lookup.get("status") != "observed" or not isinstance(lookup.get("members"), list) \
            or not lookup["members"]:
        gaps.append("member lookup has no demonstrated result")
    return gaps


def check(manifest, observations, *, rehearsal=None, operational=None, now=None):
    """Identify concrete missing artifacts. Never emit acceptance or start OPS-0010."""
    validate_manifest(manifest)
    now = now or _now()
    gaps = []
    local_gaps = [key for key in REHEARSAL_CHECKS
                  if (rehearsal or {}).get("checks", {}).get(key) != "passed"]
    if not (rehearsal or {}).get("command") or not _digest((rehearsal or {}).get("artifact_sha256")):
        local_gaps.append("rehearsal command and output artifact digest")
    selected = {}
    for host, spec in manifest["hosts"].items():
        for release in manifest["releases"]:
            key = (host, release["commit"])
            candidates = [item for item in observations if item.get("host_id") == host and
                          item.get("code", {}).get("head") == release["commit"]]
            if len(candidates) != 1:
                gaps.append(f"{host}/{release['tag']}: need exactly one host observation")
                continue
            observation = candidates[0]
            selected[key] = observation
            gaps.extend(f"{host}/{release['tag']}: {issue}"
                        for issue in _snapshot_gaps(observation, spec, release, now))
            provenance = observation.get("return_channel", {})
            if provenance.get("source_ref") != spec["owned_ref"] or \
                    not _digest(provenance.get("source_commit"), 40) or \
                    provenance.get("payload_sha256") != _hash(serialized({key: value for key, value in observation.items()
                                                                          if key != "return_channel"})) or \
                    not provenance.get("receipt_evidence"):
                gaps.append(f"{host}/{release['tag']}: verified return-channel receipt must be supplied")
    for host in manifest["hosts"]:
        first, second = (selected.get((host, item["commit"]), {}) for item in manifest["releases"])
        try:
            if _date(first["observed_at"]) >= _date(second["observed_at"]):
                gaps.append(host + ": release observations are not sequential")
            if (now - _date(second["observed_at"])).total_seconds() > manifest.get("max_observation_age_seconds", 1800):
                gaps.append(host + ": second release observation is stale")
        except (KeyError, ValueError, TypeError):
            pass  # Missing/invalid observations are already reported above.
    for release in manifest["releases"]:
        for sender in manifest["hosts"]:
            required = [probe for probe in manifest["probes"]
                        if probe["sender"] == sender and probe["release"] == release["commit"]]
            if not required:
                gaps.append(f"{sender}/{release['tag']}: designated directional mail probe is missing")
            for probe in required:
                observed = selected.get((sender, release["commit"]), {})
                mail = next((item for item in observed.get("probe_messages", [])
                             if item.get("message_id") == probe["message_id"]), {})
                state = mail.get("lifecycle", {})
                ack = state.get("acknowledgment", {})
                source = manifest["hosts"][sender]["owned_ref"]
                ack_source = manifest["hosts"][probe["recipient"]]["owned_ref"]
                if state.get("state") != "delivered" or not _digest(mail.get("sha256")) or \
                        mail.get("destination") != probe["destination"] or \
                        ack.get("message_id") != probe["message_id"] or ack.get("sha256") != mail["sha256"] or \
                        ack.get("destination") != probe["destination"] or ack.get("source_ref") != source or \
                        state.get("acknowledgment_ref") != ack_source or ack.get("outcome") != "delivered":
                    gaps.append(f"{sender}/{release['tag']}: matching delivery acknowledgment missing for {probe['message_id']}")
    operational = operational or {}
    expected_operations = {"two_unattended_releases": [item["commit"] for item in manifest["releases"]],
                           "no_runner_hand_operation": 0, "no_production_main_writes": 0,
                           "old_checkout_preserved": True, "no_pending_work_lost": True,
                           "rollback_verified": True, "legacy_schedules_retired": True,
                           "legacy_dependencies_inspected": []}
    for key, expected in expected_operations.items():
        item = operational.get(key, {})
        if item.get("kind") != "live-operations" or item.get("status") != "observed" or \
                not item.get("evidence") or not _digest(item.get("artifact_sha256")) or \
                item.get("value") != expected or type(item.get("value")) is not type(expected):
            gaps.append("operational evidence missing: " + key)
    return {"schema_version": 1, "status": "blocked" if gaps or local_gaps else "ready-for-review",
            "local_rehearsal": "incomplete" if local_gaps else "recorded",
            "local_gaps": local_gaps, "live_gaps": gaps, "accepted": False,
            "ops_observation_started_at": None,
            "review_required": ["authenticate host/return-channel artifacts against actual receipt records",
                                "verify the two referenced promotion approvals and operational coverage",
                                "record acceptance and start OPS-0010 only through the authorized workflow"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    collect = sub.add_parser("snapshot")
    collect.add_argument("--config", required=True)
    collect.add_argument("--manifest", required=True)
    collect.add_argument("--host", required=True)
    collect.add_argument("--live-observation", action="store_true")
    verify = sub.add_parser("check")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--observation", action="append", default=[])
    verify.add_argument("--rehearsal")
    verify.add_argument("--operational")
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(Path(path).read_text()) if path else None
    try:
        manifest = validate_manifest(read(args.manifest))
        if args.action == "snapshot":
            roots = production.resolve(ROOT, {production.CONFIG_ENV: args.config})
            result = snapshot(roots, args.config, manifest, args.host, live=args.live_observation)
        else:
            result = check(manifest, [read(path) for path in args.observation],
                           rehearsal=read(args.rehearsal), operational=read(args.operational))
        if args.action == "snapshot":
            sys.stdout.write(serialized(result).decode())
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("status") == "blocked" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"mail acceptance evidence unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
