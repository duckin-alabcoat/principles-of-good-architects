#!/usr/bin/env python3
"""Unattended mail cycle shared by launchd and production session hooks (WI-0364).

An explicit external config is required. Each invocation publishes, consumes remote
snapshots using maildelivery, then publishes generated acknowledgments. Failed cycles
retain queued work; the next scheduled invocation retries without a session or release.
The external health file is readable even while the channel itself is unavailable.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading

import mailqueue
import production

ROOT = Path(__file__).resolve().parent.parent
HEALTH_NAME = "mail-worker.health.json"


class CycleDeadline(BaseException):
    """Must escape per-message RuntimeError handlers without swallowing the deadline."""


class CycleInterrupted(BaseException):
    """Let transport kill/wait children before a service shutdown releases locks."""


def _now():
    return datetime.now(timezone.utc)


def _date(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return stamp


def options(roots):
    limits = {"poll_interval_seconds": (600, 30, 86400),
              "stale_after_seconds": (1800, 60, 604800),
              "aged_after_seconds": (3600, 60, 604800),
              "max_cycle_seconds": (240, 1, 3600)}
    values = {}
    for key, (default, low, high) in limits.items():
        value = roots.transport.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"transport.{key} must be an integer between {low} and {high}")
        values[key] = value
    if values["stale_after_seconds"] <= values["poll_interval_seconds"]:
        raise ValueError("stale_after_seconds must exceed poll_interval_seconds")
    return values


def _write(roots, value):
    directory = roots.state_path("runtime")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".mail-health-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, roots.state_path("runtime", HEALTH_NAME))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record(roots):
    try:
        data = json.loads(roots.state_path("runtime", HEALTH_NAME).read_text())
        if data.get("schema_version") != 1:
            raise ValueError("unknown health version")
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return None


@contextmanager
def cycle_lock(roots):
    # A separate whole-cycle lock surrounds the lower-level transport lock. Placing
    # both beside the transport also fences mistaken configs with different state roots.
    root = Path(roots.transport_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(root.parent / ("." + root.name + ".mail-cycle.lock"),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


@contextmanager
def deadline(seconds):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("mail worker must run in the main thread to enforce its deadline")
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise RuntimeError("mail worker cannot replace an existing process alarm")
    previous = signal.getsignal(signal.SIGALRM)
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminated(_signum, _frame):
        raise CycleInterrupted()
    def expired(_signum, _frame):
        raise CycleDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, terminated)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        signal.signal(signal.SIGTERM, previous_term)


def _pending(roots):
    errors = []
    pending = mailqueue.pending(roots, on_error=lambda path, exc: errors.append(
        {"entry": path.name, "failure": type(exc).__name__}))
    return pending, errors


def queue_status(roots, now=None):
    now = now or _now()
    pending, errors = _pending(roots)
    data = [item for item in pending if item.provenance.get("kind") != "delivery-ack"]
    unpublished = [item for item in pending
                   if mailqueue.lifecycle(roots, item.message_id)["state"] == "queued"]
    ages = []
    for item in data:
        try:
            ages.append(max(0, (now - _date(item.created_at)).total_seconds()))
        except (ValueError, TypeError):
            errors.append({"entry": mailqueue.identity_key(item.message_id), "failure": "invalid timestamp"})
    oldest = max(ages, default=None)
    # Terminal but NOT hidden: left `pending` because retrying cannot deliver them, so
    # counted here with the recipient's reason codes, and `read_status` is never `ok`
    # while any remain.
    reasons = {}
    for item, code in mailqueue.undeliverable(roots):
        if item.provenance.get("kind") != "delivery-ack":
            reasons[code] = reasons.get(code, 0) + 1
    return {"pending_count": None if errors else len(data), "known_pending_count": len(data),
            "undeliverable_count": sum(reasons.values()),
            "undeliverable_reasons": dict(sorted(reasons.items())),
            "corrupt_queue_count": len(errors), "queue_errors": errors,
            "unpublished_count": len(unpublished),
            "pending_ack_publication_count": sum(item.provenance.get("kind") == "delivery-ack"
                                                for item in unpublished),
            "oldest_pending_age_seconds": oldest}


def _release(roots):
    # Read-only local identity. A commit hash is still useful when no tag is available;
    # detached HEAD alone is never reported as evidence of an approved release.
    try:
        result = subprocess.run(["git", "-C", str(roots.code_root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def run_cycle(roots, *, publish=None, consume=None):
    import mailtransport
    import maildelivery
    publish = publish or mailtransport.cycle
    consume = consume or maildelivery.cycle
    config = options(roots)
    with cycle_lock(roots) as acquired:
        if not acquired:
            return {"status": "busy", "failure": "another whole mail cycle is running"}
        started = _now()
        previous = _record(roots) or {}
        record = {key: previous.get(key) for key in
                  ("last_completed_cycle", "last_successful_fetch", "last_successful_publication",
                   "last_successful_delivery", "last_observed_ack_latency_seconds")}
        record.update(schema_version=1, host=socket.gethostname(), release=_release(roots),
                      code_root=str(roots.code_root), status="running", failure=None,
                      started_at=started.isoformat(), observed_at=started.isoformat(), **config)
        _write(roots, record)
        stages = {}
        stage = "queue"
        # Observe original timestamps before acknowledgments transition them to delivered.
        ages = {}
        try:
            with deadline(config["max_cycle_seconds"]):
                # OPS-0010 soak: exact-duplicate diagnoses defer to one representative
                # before anything is counted or published. Payloads stay in the queue.
                stage = "collapse"
                stages["collapse"] = {"superseded": len(mailqueue.collapse(roots))}
                stage = "queue"
                record.update(queue_status(roots))
                if record["corrupt_queue_count"]:
                    record["failure"] = "queue: corrupt entries retained"
                for item in _pending(roots)[0]:
                    if item.provenance.get("kind") != "delivery-ack":
                        try:
                            ages[item.message_id] = _date(item.created_at)
                        except (ValueError, TypeError):
                            pass  # Already reported in queue health; don't starve valid mail.
                for stage, operation in (("publication", publish), ("delivery", consume),
                                         ("ack_publication", publish)):
                    value = operation(roots)
                    stages[stage] = value
                    stamp = _now().isoformat()
                    if stage == "delivery":
                        if value.get("snapshots"):
                            record["last_successful_fetch"] = stamp
                        outcomes = value.get("outcomes", [])
                        # `unmatched-ack` belongs here: queue payloads are retained forever
                        # (WI-0362), so an ack about our own owned ref naming a message this
                        # host never queued means queue loss or a forged ack, never a benign
                        # race. Without it the one genuinely anomalous outcome the delivery
                        # path can report is dropped and the cycle reads healthy (WI-0363).
                        record["attention"] = [item for item in outcomes if item.get("outcome") in
                                               ("unroutable", "unreachable", "malformed",
                                                "unmatched-ack", "undeliverable")]
                        record["elsewhere_count"] = sum(item.get("outcome") == "elsewhere"
                                                         for item in outcomes)
                        record["superseded_count"] = sum(item.get("outcome") == "superseded"
                                                          for item in outcomes)
                        if any(item.get("outcome") in ("delivered", "already-delivered") for item in outcomes):
                            record["last_successful_delivery"] = stamp
                        for message_id, created in ages.items():
                            if mailqueue.lifecycle(roots, message_id)["state"] == "delivered":
                                record["last_observed_ack_latency_seconds"] = max(
                                    0, (_now() - created).total_seconds())
                        if value.get("busy") or value.get("errors"):
                            record["failure"] = "delivery: busy" if value.get("busy") else "delivery: " + "; ".join(
                                str(item.get("error", "failed")) for item in value["errors"][:5])
                    elif value.get("status") == "ok":
                        record["last_successful_fetch"] = stamp
                        if value.get("last_publication"):
                            record["last_successful_publication"] = value["last_publication"]
                    else:
                        record["failure"] = stage + ": " + str(value.get("failure") or value.get("status", "failed"))
                record.update(queue_status(roots))
        except CycleDeadline:
            record["failure"] = stage + ": cycle deadline exceeded; queued work retained"
        except CycleInterrupted:
            record["failure"] = stage + ": service termination; queued work retained"
        except Exception as exc:
            # Exception content may include remote credentials; don't copy it to logs.
            record["failure"] = stage + ": " + type(exc).__name__
        finished = _now()
        record.update(status="failed" if record["failure"] else "ok", stages=stages,
                      last_completed_cycle=finished.isoformat(), observed_at=finished.isoformat(),
                      duration_seconds=(finished - started).total_seconds())
        _write(roots, record)
        return record


def read_status(roots, now=None):
    """Local, read-only status. Old success never becomes current empty/healthy."""
    now = now or _now()
    config = options(roots)
    record = _record(roots)
    if record is None:
        return {"status": "unknown", "failure": "no readable local worker observation",
                "health_path": str(roots.state_path("runtime", HEALTH_NAME))}
    result = dict(record)
    try:
        age = (now - _date(record["observed_at"])).total_seconds()
        result["observation_age_seconds"] = max(0, age)
        if age < -60:
            result.update(status="unknown", failure="worker observation is in the future")
        elif age > config["stale_after_seconds"]:
            result["status"] = "stale"
        else:
            result.update(queue_status(roots, now))
            if result["corrupt_queue_count"]:
                result.update(status="unknown", failure="queue contains unreadable entries")
            elif record["status"] == "ok" and result["undeliverable_count"]:
                # A settled-but-failed message is a fact someone must act on; leaving
                # `pending` must not read as health. Outranks `aged` (the age is still
                # reported in its own field).
                result["status"] = "undeliverable"
            elif record["status"] == "ok" and result["oldest_pending_age_seconds"] is not None and \
                    result["oldest_pending_age_seconds"] > config["aged_after_seconds"]:
                result["status"] = "aged"
    except (OSError, ValueError, KeyError, TypeError):
        result.update(status="unknown", failure="worker observation or queue is unreadable")
    return result


@contextmanager
def _configuration_environment(config):
    # Existing delivery/resolver public APIs read this explicit selection. Set it
    # before their lazy imports as well as passing Roots to the transport functions.
    previous = os.environ.get(production.CONFIG_ENV)
    os.environ[production.CONFIG_ENV] = config
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(production.CONFIG_ENV, None)
        else:
            os.environ[production.CONFIG_ENV] = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="absolute external production config; otherwise explicit environment")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.config:
        env[production.CONFIG_ENV] = args.config
    try:
        roots = production.resolve(ROOT, env)
        if roots is None:
            raise ValueError("mail worker requires an explicit POGA_FEDERATION_CONFIG or --config")
        with _configuration_environment(env[production.CONFIG_ENV]):
            result = read_status(roots) if args.status else run_cycle(roots)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"mail worker configuration/operation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif not args.quiet or result["status"] not in ("ok", "busy") or result.get("attention"):
        print(f"mail worker {result['status'].upper()}: pending={result.get('pending_count', 'unknown')}; "
              f"oldest={result.get('oldest_pending_age_seconds', 'unknown')}s; "
              + (f"undeliverable={result['undeliverable_count']}; "
                 if result.get("undeliverable_count") else "") +
              f"attention={len(result.get('attention', []))}; "
              f"failure={result.get('failure') or 'none'}")
        for item in result.get("attention", [])[:5]:
            print(f"  {item['outcome']}: {item.get('path', '?')} — {item.get('detail', '')}")
    return 0 if result["status"] in ("ok", "busy", "running") else 1


if __name__ == "__main__":
    raise SystemExit(main())
