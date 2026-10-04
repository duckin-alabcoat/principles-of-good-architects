#!/usr/bin/env python3
"""The devbox side of the mail transport (WI-0419): a release pin, a service config and
the LaunchDaemon that runs the mail worker from them.

THE DEVBOX DIRECTION HAD NO PRODUCER AT ALL. The Runner's delivery reported `ref
unavailable` for its input ref because nothing on devbox ever created it: there was no
devbox service config and no devbox mail worker. This module is the mirror of the
Runner's pair, and `deploy/install-mail-worker-daemon.sh` is its only caller.

THE REF PAIR IS DERIVED, NEVER SPELLED AGAIN. The Runner owns
`migrate.TRANSPORT_OWNED_REF` and reads `migrate.TRANSPORT_INPUT_REFS`; devbox owns the
Runner's input and reads the Runner's owned ref. Writing the names a second time here would
make a rename on one side a silent partition on the other.

THREE CHOICES THE INSTALLER DOES NOT ASK ABOUT, and why:

- **The worker runs from a pinned release clone, not from the development checkout.**
  `production.resolve` refuses a code root on a writable branch, and ADR-0135 says a trunk
  clone is never a process root. The clone lives at `RELEASE_DIR`, NOT `~/deploy/federation`:
  `install-gate-inputs.sh` refuses to install on a machine that has a sealed federation
  deploy tree it is not inside, so a tree at the conventional address would break the
  gate-input daemon's own re-install. It is a separate clone rather than a `git worktree`,
  because every linked worktree of the development checkout is read as a lane at session
  start.
- **The remote is the development checkout's own `origin`.** It is the one remote this
  machine is designed to push to unattended, as a daemon (OPS-0009's land). The
  registry's https spelling needs a credential helper a daemon does not have.
- **A LaunchDaemon, not an agent.** The design assumes this machine is headless (no
  graphical login), so an agent never loads; the reasoning is recorded in
  `com.federation.gate-inputs.plist.template`.
  The plist is built here rather than shipped as `deploy/*.plist.template`: a template is
  a unit of the deploy CONTRACT and would be rendered by the Runner's cutover, and this one
  belongs to a machine no deploy runs on.
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
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "curate"))
sys.path.insert(0, str(ROOT / "deploy"))
import mailworker                                                    # noqa: E402
import migrate                                                       # noqa: E402
import production                                                    # noqa: E402

LABEL = "com.federation.mail-worker"

#: The Runner's pair, mirrored. See the module docstring for why this is derived.
OWNED_REF = migrate.TRANSPORT_INPUT_REFS[0]
INPUT_REFS = (migrate.TRANSPORT_OWNED_REF,)

#: Every path is under the operator's home and none is under a Git checkout, which
#: `production.resolve` requires of state, config and transport alike.
RELEASE_DIR = Path(".local/share/poga/federation-mail-release")
STATE_DIR = Path(".local/state/poga/federation")
TRANSPORT_DIR = Path(".local/state/poga/federation-transport.git")
CONFIG_FILE = Path(".config/poga/federation-service.json")

#: Where the federation's own inbox lives in the development checkout. Devbox is the
#: federation's owning host, so its installation is the authoritative one; the Runner's
#: delivery answers "elsewhere" for this recipient precisely so that this one delivers.
INBOX = Path("proposed-edits/federation-arch/pending")

POLL_SECONDS_DEFAULT = 60
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class InstallError(RuntimeError):
    """The daemon cannot be prepared. Nothing outside the named paths was changed."""


def _git(args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise InstallError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def latest_tag(clone: Path) -> str:
    """The highest `vX.Y.Z` tag by VERSION, never by date or by name: `v7.10.0` sorts
    before `v7.9.0` as text, and a tag cut late for an old line is not the newest."""
    tags = [t for t in _git(["tag", "--list", "v*"], cwd=clone).splitlines() if TAG.match(t)]
    if not tags:
        raise InstallError(f"no release tag in {clone}; nothing to pin the worker to")
    return max(tags, key=lambda t: tuple(int(n) for n in TAG.match(t).groups()))


def pin_release(clone: Path, remote: str, tag: str | None = None) -> str:
    """Clone once, then fetch and check out a release tag DETACHED. Returns the tag.

    Idempotent, and re-running it is how the pin advances. A clone whose `origin` names a
    different repository is refused rather than repointed: it is somebody else's tree."""
    if not (clone / ".git").exists():
        if clone.exists() and any(clone.iterdir()):
            raise InstallError(f"{clone} exists, is not empty and is not a clone")
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--quiet", "--no-checkout", remote, str(clone)])
    present = _git(["remote", "get-url", "origin"], cwd=clone)
    if present != remote:
        raise InstallError(f"{clone} tracks {present}, not {remote}; refusing to adopt it")
    _git(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=clone)
    tag = tag or latest_tag(clone)
    _git(["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach",
          f"refs/tags/{tag}"], cwd=clone)
    return tag


def seed_config_files(config_root: Path, checkout: Path, log) -> list:
    """Copy the three files `production.resolve` requires, once, from the checkout.

    Never overwritten, like the Runner's `provision`: an existing file is authored or
    already seeded. A missing source is refused, not skipped, because `resolve` would
    refuse the whole configuration a moment later with a less useful message."""
    config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    seeded = []
    for name in migrate.CONFIG_FILES:
        destination = config_root / name
        if destination.exists():
            continue
        source = checkout / name
        if not source.is_file():
            raise InstallError(f"{source} is missing, and production requires {name}")
        migrate.copy_atomic(source, destination)
        seeded.append(name)
        log(f"config: seeded {name} from {checkout}")
    return seeded


def config_document(*, home: Path, checkout: Path, remote: str) -> dict:
    """The devbox service configuration. Pure, so the ref pair is testable on its own."""
    state = home / STATE_DIR
    return {
        "//": "GENERATED by deploy/devbox_mail.py (WI-0419). Machine-local: absolute "
              "paths on THIS host only, never committed. An existing file is used as "
              "authored and never rewritten.",
        "schema_version": migrate.SERVICE_CONFIG_SCHEMA,
        "code_root": str(home / RELEASE_DIR),
        "state_root": str(state),
        "config_root": str(state / "config"),
        "transport_root": str(home / TRANSPORT_DIR),
        "inbox_root": str(checkout / INBOX),
        "transport": {"remote": remote, "owned_ref": OWNED_REF,
                      "input_refs": list(INPUT_REFS)},
    }


def author_config(path: Path, document: dict, log) -> str:
    if path.exists():
        log(f"config: {path} exists — using it as authored")
        return "authored"
    migrate._write_json(path, document)
    log(f"config: wrote {path} — owns {OWNED_REF}, reads {list(INPUT_REFS)}")
    return "written"


def render_plist(roots, *, config: Path, python: Path, user: str, home: Path) -> bytes:
    """The daemon definition. `UserName` and `HOME` are set because a daemon has neither by
    default: without the first it runs as root, and without the second git has no global
    config and ssh reads the wrong home. Both are machine facts, which is why this plist is
    rendered here and never tracked."""
    if not python.is_absolute():
        raise InstallError("python must be an absolute path")
    if not user or user == "root":
        raise InstallError("the mail worker must run as the operator, never as root")
    poll = roots.transport.get("poll_interval_seconds", POLL_SECONDS_DEFAULT)
    runtime = roots.state_path("runtime")
    return plistlib.dumps({
        "Label": LABEL,
        "UserName": user,
        "ProgramArguments": [str(python), str(roots.code_root / "curate/mailworker.py"),
                             "--config", str(config), "--quiet"],
        "WorkingDirectory": str(roots.code_root),
        "StartInterval": int(poll),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_TERMINAL_PROMPT": "0",
            production.CONFIG_ENV: str(config),
        },
        "StandardOutPath": str(runtime / "mail-worker.launchd.out.log"),
        "StandardErrorPath": str(runtime / "mail-worker.launchd.err.log"),
    }, sort_keys=False)


def prepare(*, checkout: Path, home: Path, python: Path, user: str, out: Path,
            tag: str | None = None, log=print) -> dict:
    """Everything the installer needs short of root: pin, seed, configure, validate, render.

    `production.resolve` is asked BEFORE the plist is written, through the same key the
    worker reads, so a configuration the worker would refuse is refused here while
    somebody is watching instead of at every StartInterval after."""
    if os.geteuid() == 0:
        raise InstallError("run as yourself; the installer asks for root only to install")
    remote = _git(["remote", "get-url", "origin"], cwd=checkout)
    document = config_document(home=home, checkout=checkout, remote=remote)
    pinned = pin_release(Path(document["code_root"]), remote, tag)
    log(f"release: {document['code_root']} pinned at {pinned}")
    config = home / CONFIG_FILE
    outcome = author_config(config, document, log)
    try:
        authored = migrate.raw_config(config)
    except migrate.MigrationError as exc:
        raise InstallError(str(exc)) from exc
    Path(authored["state_root"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    seed_config_files(Path(authored.get("config_root", Path(authored["state_root"]) / "config")),
                      checkout, log)
    try:
        roots = production.resolve(Path(authored["code_root"]),
                                   {production.CONFIG_ENV: str(config), "HOME": str(home)})
    except production.ConfigurationError as exc:
        raise InstallError(f"the service configuration is not usable: {exc}") from exc
    # WI-0429's devbox half: the reverse map, derived at install so no host starts without one.
    if migrate.ensure_recipient_refs(config, roots.config_root / "mailboxes.json",
                                     release_systems(roots.code_root), log):
        roots = production.resolve(Path(authored["code_root"]),
                                   {production.CONFIG_ENV: str(config), "HOME": str(home)})
    roots.state_path("runtime").mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_bytes(render_plist(roots, config=config, python=python, user=user, home=home))
    return {"tag": pinned, "config": str(config), "config_outcome": outcome,
            "code_root": str(roots.code_root), "owned_ref": roots.transport.get("owned_ref"),
            "input_refs": roots.transport.get("input_refs"), "plist": str(out)}


def release_systems(release: Path) -> list:
    """The deploy-registry systems of the pinned release, for `migrate.recipient_ids`. An
    unreadable registry yields none, so the map falls back to the roster's own ids."""
    try:
        return list(json.loads((Path(release) / "deploy/registry.json").read_text())
                    .get("systems", {}))
    except (OSError, ValueError, AttributeError):
        return []


def _pinned_tag(clone: Path):
    result = subprocess.run(["git", "describe", "--tags", "--exact-match", "HEAD"], cwd=clone,
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def advance(*, home: Path, tag: str | None = None, wait_seconds: int = 300, log=print) -> dict:
    """Devbox's deploy: pin the worker's release, refresh its roster, fill its ack map.

    DEVBOX HAD NO DEPLOY AT ALL, which is how its worker sat on v7.6.0 and a 09-13 roster
    while the Runner took v7.7.0 unattended (WI-0428): the pin moved only when somebody re-ran
    the sudo installer. `poga release cut` calls this after publishing a tag. No root is
    needed — the daemon's plist names the clone's path, not a tag, so its next StartInterval
    runs the new release.

    Taken under the worker's own whole-cycle lock, so no cycle runs across the checkout. A
    pin the resolver then refuses is put back, so a bad tag cannot leave the worker without
    a configuration it accepts."""
    config = home / CONFIG_FILE
    try:
        document = migrate.raw_config(config)
    except migrate.MigrationError as exc:
        raise InstallError(f"no devbox mail worker to advance: {exc}") from exc
    code_root = Path(document["code_root"])
    remote = (document.get("transport") or {}).get("remote")
    if not remote:
        raise InstallError(f"{config} names no transport remote")
    env = {production.CONFIG_ENV: str(config), "HOME": str(home)}
    try:
        roots = production.resolve(code_root, env)
    except production.ConfigurationError as exc:
        raise InstallError(f"the service configuration is not usable: {exc}") from exc
    waited = 0
    while True:
        with mailworker.cycle_lock(roots) as acquired:
            if acquired:
                previous = _pinned_tag(code_root)
                pinned = pin_release(code_root, remote, tag)
                refreshed = migrate.refresh_release_config(roots.config_root, code_root, log)
                added = migrate.ensure_recipient_refs(
                    config, roots.config_root / "mailboxes.json", release_systems(code_root), log)
                try:
                    production.resolve(code_root, env)
                except production.ConfigurationError as exc:
                    if previous and previous != pinned:
                        pin_release(code_root, remote, previous)
                    raise InstallError(f"{pinned} left a configuration the worker refuses "
                                       f"({exc}); the pin is back at {previous}") from exc
                log(f"advance: {code_root} {previous} -> {pinned}")
                return {"tag": pinned, "previous": previous, "refreshed": refreshed,
                        "recipient_refs_added": added}
        if waited >= wait_seconds:
            raise InstallError(f"a mail cycle held the lock for {waited}s; nothing was moved")
        time.sleep(5)
        waited += 5


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["advance"]:
        parser = argparse.ArgumentParser(prog="devbox_mail.py advance",
                                         description=advance.__doc__.splitlines()[0])
        parser.add_argument("--tag", help="release tag to pin (default: the highest vX.Y.Z)")
        args = parser.parse_args(argv[1:])
        try:
            result = advance(home=Path.home(), tag=args.tag,
                             log=lambda m: print(m, file=sys.stderr))
        except (InstallError, OSError) as exc:
            print(f"mail worker advance: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkout", required=True, help="the development checkout")
    parser.add_argument("--python", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--out", required=True, help="where to write the rendered plist")
    parser.add_argument("--tag", help="release tag to pin (default: the highest vX.Y.Z)")
    args = parser.parse_args(argv)
    try:
        result = prepare(checkout=Path(args.checkout).resolve(), home=Path.home(),
                         python=Path(args.python), user=args.user, out=Path(args.out),
                         tag=args.tag, log=lambda m: print(m, file=sys.stderr))
    except (InstallError, OSError) as exc:
        print(f"mail worker daemon: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
