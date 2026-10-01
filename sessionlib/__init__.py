"""The session harness, split by concern behind session.py.

`session.py` is the thin entry point and stays the fleet's byte-identical file;
the parts below carry the code. They are executed into ONE namespace — the
session module's own globals — so cross-part calls resolve exactly as they did
in the monolith and `session.<GLOBAL> = x` patching reaches every part. See
ADR-0118.
"""
import pathlib

PARTS = ('config', 'goal', 'coord', 'store', 'journal', 'land', 'trunkcheck', 'lanes', 'hooks')


def load(ns):
    """Execute each concern part into the caller's namespace, in order."""
    here = pathlib.Path(__file__).resolve().parent
    for part in PARTS:
        path = here / (part + ".py")
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        exec(code, ns)
    return ns
