"""Explicit federation service roots (WI-0361). No Git or machine-name inference.

Only POGA_FEDERATION_CONFIG selects production. Provisioning owns the external config
and root directories; resolving never creates them or silently substitutes a checkout.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess

CONFIG_ENV = "POGA_FEDERATION_CONFIG"


class ConfigurationError(ValueError):
    """A declared production configuration is absent, unsafe or inconsistent."""


def _absolute(value, name):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ConfigurationError(f"production {name} must be an absolute path")
    return Path(value).resolve()


def _outside_checkout(path, code_root, name):
    if path == code_root or code_root in path.parents:
        raise ConfigurationError(f"production {name} is inside the program checkout: {path}")
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise ConfigurationError(f"production {name} is inside a Git checkout: {path}")


@dataclass(frozen=True)
class Roots:
    code_root: Path
    state_root: Path
    transport_root: Path
    config_root: Path
    inbox_root: Path | None
    transport: dict

    @property
    def queue_root(self):
        return self.state_root / "queue"

    @property
    def session_state_root(self):
        return self.state_root / "runtime"

    @property
    def comms_root(self):
        return self.state_root / "comms"

    @property
    def mailboxes_path(self):
        return self.config_root / "mailboxes.json"

    def state_path(self, *parts):
        """Validate a writer's path again, including symlinks created since startup."""
        path = self.state_root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.state_root):
            raise ConfigurationError(f"production state path escapes state_root: {path}")
        _outside_checkout(path.resolve(), self.code_root, "state path")
        return path


def resolve(code_root, env=None):
    """Validated Roots, or None ONLY when production was not selected."""
    env = os.environ if env is None else env
    if CONFIG_ENV not in env:
        return None
    config = _absolute(env[CONFIG_ENV], CONFIG_ENV)
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"production config unreadable: {config}: {exc}") from exc
    # Version 2 (WI-0418) differs from 1 in nothing this function parses — same keys, same
    # types — so both are read. The bump marks a document examined for the channel-branch
    # collision; refusing version 1 here would make production dormant on every host between
    # the release landing and that host's own migration running.
    # `type(...) is not int` rather than `!= 1`: `True == 1`, so the old spelling accepted a
    # boolean as a version number.
    version = data.get("schema_version") if isinstance(data, dict) else None
    if not isinstance(data, dict) or type(version) is not int or version not in (1, 2):
        raise ConfigurationError("production config requires schema_version: 1 or 2")
    code = _absolute(data.get("code_root"), "code_root")
    if code != Path(code_root).resolve() or not code.is_dir():
        raise ConfigurationError(f"production code_root does not match running program: {code_root}")
    # Production may run from a pinned release checkout, never an editable branch.
    if (code / ".git").exists():
        branch = subprocess.run(["git", "-C", str(code), "symbolic-ref", "--quiet", "HEAD"],
                                capture_output=True, text=True, check=False)
        if branch.returncode == 0:
            raise ConfigurationError(f"production code_root is a writable branch: {branch.stdout.strip()}")
        if branch.returncode != 1:
            raise ConfigurationError("production code_root Git identity is unreadable")
    home = env.get("HOME")
    default_state = str(Path(home) / ".local/state/poga/federation") if home else None
    state = _absolute(data.get("state_root", default_state), "state_root")
    cfg = _absolute(data.get("config_root", str(state / "config")), "config_root")
    transport = _absolute(data.get("transport_root"), "transport_root")
    inbox = _absolute(data["inbox_root"], "inbox_root") if data.get("inbox_root") else None
    for name, path in (("config file", config), ("state_root", state), ("config_root", cfg)):
        _outside_checkout(path, code, name)
    if transport == code or code in transport.parents:
        raise ConfigurationError("production transport_root is inside the program checkout")
    # A transport repository may contain .git, but its parent must not be another checkout.
    _outside_checkout(transport.parent, code, "transport_root parent")
    if transport == state or transport in state.parents or state in transport.parents:
        raise ConfigurationError("production state and transport roots must be separate")
    if transport == cfg or transport in cfg.parents or cfg in transport.parents:
        raise ConfigurationError("production config and transport roots must be separate")
    for name, path in (("state_root", state), ("config_root", cfg)):
        if not path.is_dir():
            raise ConfigurationError(f"production {name} is missing: {path}")
    for filename in ("mailboxes.json", "repo-paths.local", "reconcile-roots.local"):
        if not (cfg / filename).is_file():
            raise ConfigurationError(f"production config is missing {cfg / filename}")
        _outside_checkout((cfg / filename).resolve(), code, filename)
    if inbox:
        if inbox == code or code in inbox.parents:
            raise ConfigurationError("authoritative inbox must not live in the release tree")
        # Existing gitignored inbox in the authoritative development checkout is valid.
        # The service config names that address; it must never invent a Runner duplicate.
        if not inbox.is_dir():
            raise ConfigurationError(f"authoritative federation inbox is missing: {inbox}")
        # Collision checks search the entire mailbox parent, including filed messages.
        # A receive spool in that tree could otherwise impersonate delivered content.
        tree = inbox.parent
        for path in (state, cfg, transport):
            if path == tree or tree in path.parents or path in tree.parents:
                raise ConfigurationError("authoritative mailbox tree must be separate from production state/config/transport")
    transport_config = data.get("transport", {})
    if not isinstance(transport_config, dict):
        raise ConfigurationError("production transport must be an object")
    for key in ("remote", "owned_ref"):
        value = transport_config.get(key)
        if value is not None and (not isinstance(value, str) or not value or "\n" in value):
            raise ConfigurationError(f"production transport.{key} must be a nonempty string")
    refs = transport_config.get("input_refs", [])
    if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
        raise ConfigurationError("production transport.input_refs must be a list of refs")
    recipients = transport_config.get("recipient_refs", {})
    if not isinstance(recipients, dict) or any(
        not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key)
        or not isinstance(value, str) for key, value in recipients.items()
    ):
        raise ConfigurationError("production transport.recipient_refs must map recipient IDs to refs")
    for ref in [*refs, *recipients.values(), *([transport_config["owned_ref"]] if "owned_ref" in transport_config else [])]:
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", ref) or ".." in ref or ref.endswith(("/", ".")):
            raise ConfigurationError(f"unsafe production transport ref: {ref!r}")
    roots = Roots(code, state, transport, cfg, inbox, dict(transport_config))
    for name in ("queue", "runtime", "comms", "message-state"):
        roots.state_path(name)
    return roots


def config_path(code_root, filename, development):
    roots = resolve(code_root)
    return roots.config_root / filename if roots else Path(development)


def state_path(code_root, filename, development):
    roots = resolve(code_root)
    return roots.state_path("runtime", filename) if roots else Path(development)
