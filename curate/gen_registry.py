#!/usr/bin/env python3
"""A member-specific registry view: not included in the public cut.

Internally this script renders a view that belongs to one member system. The public cut
describes no member (WI-0448), so this copy keeps only the interface the rest of the
harness calls: `config_root()`, and a `--status` entry point for the SessionStart hook.

CLI:
  python3 curate/gen_registry.py --status    # one-line signal (SessionStart hook)
  python3 curate/gen_registry.py --check     # always clean here: nothing is rendered
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import member_config_root  # noqa: E402

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent


def config_root():
    """The machine-local config root, resolved the same way every reader resolves it."""
    return member_config_root(FED_ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="one-line signal for the SessionStart hook")
    ap.add_argument("--check", action="store_true", help="exit 0: nothing is rendered in this copy")
    ap.parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
