"""The `session.py` CLI surface, pinned across the per-family registration split.

Refactor-cleanup package 5. `main()` was one 1,329-line function registering ~95 verbs.
It is now `_cli_parser()` calling one registration function per command family
(`CLI_FAMILIES`), in the original order. The split had to be invisible: same verb
names and aliases, same flags, defaults, help text and dispatch targets, byte for byte.

HOW IT IS PINNED. `describe_cli` captures the parser `main()` really builds (by
intercepting `parse_args`, so no verb runs) and renders each verb canonically: its full
`--help` text at a fixed width, every action's option strings, dest, default, nargs,
const, choices, required, type, metavar and help, and the parser defaults (a callable,
such as the `func=` dispatch target, by name). `tests/cli_surface.snapshot.json` holds a
SHA-256 of each rendering, taken from the pre-split parser at trunk 6672bbd5 and checked
to be equal, in full text, to the post-split one.

WHEN YOU CHANGE THE CLI ON PURPOSE (a new verb, flag or help line), this test fails
naming the verbs that moved. Regenerate the snapshot and commit it with the change:

    python3 -B tests/test_cli_registration.py --regen

stdlib unittest: python3 -m unittest tests.test_cli_registration
"""

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = pathlib.Path(__file__).resolve().parent / "cli_surface.snapshot.json"
HELP_COLUMNS = "100"


class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("captured")
        self.parser = parser


def capture_main_parser(session_module):
    """The ArgumentParser `session_module.main()` builds, without running any verb."""
    def grab(self, *a, **k):
        raise _Captured(self)
    with mock.patch.object(argparse.ArgumentParser, "parse_args", grab), \
            mock.patch.object(sys, "argv", ["session.py"]):
        try:
            session_module.main()
        except _Captured as c:
            return c.parser
    raise AssertionError("main() returned without calling parse_args")


def _plain(v):
    if callable(v) and hasattr(v, "__name__"):
        return f"<callable {v.__name__}>"
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return repr(v)


def _describe_parser(parser):
    with mock.patch.dict(os.environ, {"COLUMNS": HELP_COLUMNS}):
        help_text = parser.format_help()
    # Python 3.10 renamed argparse's "optional arguments:" heading to "options:". The
    # macOS harness pins /usr/bin/python3 (3.9) and the cloud box runs a newer one, so
    # the heading is normalised before hashing: the snapshot pins this CLI, not argparse.
    help_text = help_text.replace("\noptional arguments:\n", "\noptions:\n")
    actions = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            actions.append({"subparsers": a.dest, "required": a.required,
                            "choices": list(a.choices)})
            continue
        actions.append({
            "kind": type(a).__name__, "options": list(a.option_strings), "dest": a.dest,
            "default": _plain(a.default), "nargs": _plain(a.nargs), "const": _plain(a.const),
            "choices": _plain(list(a.choices) if a.choices is not None else None),
            "required": a.required, "type": _plain(a.type), "metavar": _plain(a.metavar),
            "help": a.help})
    return {"prog": parser.prog, "help": help_text, "actions": actions,
            "defaults": {k: _plain(v) for k, v in sorted(parser._defaults.items())}}


def describe_cli(session_module):
    """{name: canonical description} for the top-level parser ("__top__") and every verb
    name (an alias is its own entry, described by the parser it maps to)."""
    top = capture_main_parser(session_module)
    out = {"__top__": _describe_parser(top)}
    sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
    for name, parser in sub.choices.items():
        out[name] = _describe_parser(parser)
    return out


def digests(desc):
    return {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode("utf-8")).hexdigest()
            for k, v in desc.items()}


def _session():
    sys.path.insert(0, str(ROOT))
    import session
    return session


class CliSurfaceIsUnchanged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = _session()
        cls.desc = describe_cli(cls.session)
        cls.snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    def test_verb_names_and_order(self):
        self.assertEqual(list(self.desc), self.snap["order"])

    def test_every_verb_matches_the_snapshot(self):
        now = digests(self.desc)
        moved = sorted(k for k in set(now) | set(self.snap["digests"])
                       if now.get(k) != self.snap["digests"].get(k))
        self.assertEqual(moved, [], "CLI surface changed for these verbs (names, flags, "
                         "defaults, help text or dispatch target). If on purpose, run "
                         "`python3 -B tests/test_cli_registration.py --regen`.")

    def test_dispatch_targets_are_real_commands(self):
        for name, d in self.desc.items():
            if name == "__top__":
                continue
            func = d["defaults"].get("func")
            with self.subTest(verb=name):
                self.assertTrue(isinstance(func, str) and func.startswith("<callable cmd_"),
                                func)

    def test_parser_is_built_from_the_families(self):
        fams = self.session.CLI_FAMILIES
        self.assertGreaterEqual(len(fams), 2)
        sub_names = []

        class Rec:
            def add_parser(self, name, *a, **k):
                sub_names.append(name)
                return argparse.ArgumentParser(prog=name, add_help=False)
        for fam in fams:
            fam(Rec())
        self.assertEqual(sub_names,
                         [n for n in self.snap["order"] if n != "__top__"
                          and n not in self.snap.get("aliases", {})])

    def test_the_snapshot_check_is_live(self):
        # A changed help line must be caught, not averaged away.
        desc = json.loads(json.dumps(self.desc))
        desc["start"]["help"] += "x"
        self.assertNotEqual(digests(desc)["start"], self.snap["digests"]["start"])


def _aliases(desc):
    """{alias: canonical} for names whose parser was registered under another name."""
    out = {}
    for name, d in desc.items():
        if name != "__top__" and d["prog"].split()[-1] != name:
            out[name] = d["prog"].split()[-1]
    return out


def main(argv):
    with contextlib.redirect_stdout(sys.stderr):
        desc = describe_cli(_session())
    if argv[:1] == ["--regen"]:
        SNAPSHOT.write_text(json.dumps({"order": list(desc), "aliases": _aliases(desc),
                                        "digests": digests(desc)}, indent=1,
                                       sort_keys=False) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT.name}: {len(desc)} entries")
    elif argv[:1] == ["--dump"]:
        pathlib.Path(argv[1]).write_text(json.dumps(desc, indent=1), encoding="utf-8")
        print(f"wrote {argv[1]}")
    else:
        unittest.main(argv=[sys.argv[0]] + argv)


if __name__ == "__main__":
    main(sys.argv[1:])
