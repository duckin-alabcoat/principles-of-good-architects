#!/usr/bin/env python3
"""Render or explicitly install the existing mail-poller label as a Background worker.

No scheduler is changed unless --apply is present. The user/<uid> launchd domain is
explicit and must exist; it does not require a GUI/Architect session. Registration is
verified, but persistence through user-domain destruction or reboot requires the
later host cutover rehearsal. No claim follows from the presence of a plist alone.

The local launchctl(1) manual documents distinct user and GUI services and permits
Background agents without GUI login. launchd.plist(5) documents session restrictions.
Old same-label plists must be explicitly supplied and point to an approved code root.
Unknown active paths are refused before any mutation. Only this service is stopped.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
sys.path.insert(0, str(ROOT / "deploy"))
import mailworker
import production
import schedulerguard

LABEL = "com.federation.mail-poller"


class InstallError(RuntimeError):
    pass


def render(roots, config, python):
    python = Path(python)
    config = Path(config)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise InstallError("python must name an absolute executable")
    if not config.is_absolute() or not config.is_file():
        raise InstallError("config must name an absolute external file")
    if not (roots.code_root / "curate/mailworker.py").is_file():
        raise InstallError("configured release does not contain mailworker.py")
    result = plistlib.loads((ROOT / "deploy/mail-worker.plist.partial").read_bytes())
    result.update(ProgramArguments=[str(python), str(roots.code_root / "curate/mailworker.py"),
                                    "--config", str(config), "--quiet"],
                  WorkingDirectory=str(roots.code_root),
                  StartInterval=mailworker.options(roots)["poll_interval_seconds"],
                  StandardOutPath=str(roots.state_path("runtime", "mail-worker.launchd.out.log")),
                  StandardErrorPath=str(roots.state_path("runtime", "mail-worker.launchd.err.log")))
    result["EnvironmentVariables"][production.CONFIG_ENV] = str(config)
    return plistlib.dumps(result, sort_keys=False)


def _write(path, data):
    # THE ONE CHOKE POINT. Every plist this module puts on disk — the rendered file, the
    # installed destination and both `.pre-mail-worker` backups — goes through here, so the
    # guard costs one placement rather than one per caller and cannot be forgotten by the
    # next writer added. A no-op outside a test run; see `deploy/schedulerguard.py` for why
    # the redirect that already exists is not enough.
    schedulerguard.refuse_live_scheduler_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mail-worker-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _launchctl(args):
    try:
        result = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=15)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("launchctl unavailable or timed out") from exc


def _validate_plist(path, approved_roots):
    if path.is_symlink():
        raise InstallError(f"refusing symlinked service plist: {path}")
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise InstallError(f"cannot read existing service plist: {path}") from exc
    args = value.get("ProgramArguments", [])
    accepted = {str(root / "curate" / name) for root in approved_roots
                for name in ("mailworker.py", "mail-poller.py")}
    if value.get("Label") != LABEL or len(args) < 2 or args[1] not in accepted:
        raise InstallError(f"refusing service outside approved code roots: {path}")


def change(destination, data, uid, *, legacy=(), approved_roots=(), uninstall=False, ctl=_launchctl):
    """Apply to a real or injected scheduler; tests use only isolated injected fixtures."""
    destination = Path(destination).absolute()
    if destination.name != LABEL + ".plist":
        raise InstallError("destination must use the existing mail-poller label filename")
    if not isinstance(uid, int) or uid < 0:
        raise InstallError("uid must be a nonnegative integer")
    domain = "user/" + str(uid)
    gui = "gui/" + str(uid)
    paths = {destination, *(Path(path).absolute() for path in legacy)}
    approved = {Path(path).resolve() for path in approved_roots}
    # Check every existing file/service before touching any of them.
    for path in paths:
        if path.exists() or path.is_symlink():
            _validate_plist(path, approved)
    if ctl(["print", domain])[0]:
        raise InstallError(f"required Background user domain is unavailable: {domain}")
    active = []
    for target in (domain, gui):
        code, output = ctl(["print", target + "/" + LABEL])
        if code == 0:
            # launchctl print is a diagnostic format, not a stable API. Fail closed
            # when it changes; never guess that an unknown same-label job is ours.
            match = re.search(r"^\s*path = (.+?)\s*$", output, re.MULTILINE)
            if not match or Path(match.group(1)) not in paths:
                raise InstallError(f"active {target}/{LABEL} has an unapproved or unreadable plist path")
            _validate_plist(Path(match.group(1)), approved)
            active.append(target)
    def checked(args):
        if ctl(args)[0]:
            raise InstallError("launchctl failed: " + " ".join(args[:2]))
    for target in active:
        checked(["bootout", target + "/" + LABEL])
    # Retire explicitly reviewed legacy files so a later GUI login cannot resurrect
    # the old checkout publisher. Preserve their bytes alongside for rollback.
    for path in paths - {destination}:
        if path.exists():
            backup = path.with_suffix(path.suffix + ".pre-mail-worker")
            if not backup.exists():
                _write(backup, path.read_bytes())
            path.unlink()
    if uninstall:
        if destination.exists():
            destination.unlink()
        return {"status": "uninstalled", "domain": domain, "label": LABEL}
    previous = destination.read_bytes() if destination.exists() else None
    if previous and not destination.with_suffix(".plist.pre-mail-worker").exists():
        _write(destination.with_suffix(".plist.pre-mail-worker"), previous)
    _write(destination, data)
    checked(["enable", domain + "/" + LABEL])
    checked(["bootstrap", domain, str(destination)])
    checked(["print", domain + "/" + LABEL])
    if ctl(["print", gui + "/" + LABEL])[0] == 0:
        raise InstallError("unexpected duplicate GUI service after installation")
    return {"status": "registered", "domain": domain, "label": LABEL,
            "plist": str(destination), "execution_verified": False,
            "reboot_persistence_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uid", type=int)
    parser.add_argument("--legacy-plist", action="append", default=[])
    parser.add_argument("--replace-code-root", action="append", default=[])
    parser.add_argument("--apply", action="store_true", help="explicitly modify this host's scheduler")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    try:
        destination = Path(args.destination)
        if not destination.is_absolute():
            raise InstallError("destination must be absolute")
        roots = production.resolve(ROOT, {production.CONFIG_ENV: args.config})
        data = render(roots, args.config, args.python)
        if not args.apply:
            if args.uninstall:
                raise InstallError("uninstall requires explicit --apply")
            scheduler_roots = (Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons"),
                               Path.home() / "Library/LaunchAgents")
            if any(destination.resolve().is_relative_to(path) for path in scheduler_roots):
                raise InstallError("render destination must be outside live scheduler directories")
            _write(destination, data)
            print(json.dumps({"status": "rendered", "plist": str(destination), "scheduler_changed": False}))
            return 0
        if args.uid is None:
            raise InstallError("apply requires an explicit --uid for the Background user domain")
        roots.state_path("runtime").mkdir(parents=True, exist_ok=True, mode=0o700)
        result = change(destination, data, args.uid, legacy=args.legacy_plist,
                        approved_roots=[roots.code_root, *args.replace_code_root], uninstall=args.uninstall)
        print(json.dumps(result, sort_keys=True))
    except (InstallError, OSError, ValueError) as exc:
        print(f"mail worker install failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
