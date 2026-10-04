"""Arm the live-store guard in a child process, then chain to the displaced
`sitecustomize` (ADR-0148 D3, WI-0427).

`curate/store_guard.py` puts this directory first on a test worker's `PYTHONPATH`, so
every Python descendant of a test imports this file at startup. Without the guard's two
environment variables it does nothing, which keeps it inert for every other Python on
the machine.

It records only paths under the forbidden prefixes, by string match — no `resolve()` —
and writes them at exit, one file per process, renamed into place so the worker never
reads half a record. Chaining follows `curate/childaudit/sitecustomize.py`: drop this
directory from `sys.path`, import `sitecustomize` again so whichever one this file
displaced still runs, then put the entry back. Nothing here may raise.
"""

import sys as _sys

try:
    import atexit as _atexit
    import json as _json
    import os as _os

    _dir = _os.environ.get("POGA_STORE_GUARD_DIR")
    _raw = _os.environ.get("POGA_STORE_GUARD_PREFIXES")
    if _dir and _raw:
        _prefixes = _json.loads(_raw)
        _hits = set()

        def _match(path):
            if isinstance(path, int) or path is None:
                return None
            try:
                raw = _os.fsdecode(path)
                if not _os.path.isabs(raw) and _os.sep not in raw:
                    return None
                s = _os.path.abspath(raw)
            except Exception:
                return None
            for p in _prefixes:
                if p.endswith("/"):
                    if s.startswith(p) or s == p[:-1]:
                        return s
                elif s == p:
                    return s
            return None

        def _hook(event, args):
            if event in ("open", "os.scandir", "os.listdir") and args:
                hit = _match(args[0])
                if hit:
                    _hits.add(hit)

        _sys.addaudithook(_hook)

        def _flush():
            if not _hits:
                return
            try:
                token = "%d-%s" % (_os.getpid(), _os.urandom(6).hex())
                tmp = _os.path.join(_dir, "." + token + ".part")
                with open(tmp, "w", encoding="utf-8") as fh:
                    _json.dump({"argv": list(_sys.argv), "paths": sorted(_hits)}, fh)
                _os.replace(tmp, _os.path.join(_dir, token + ".json"))
            except Exception:
                pass

        _atexit.register(_flush)
except Exception:
    pass

try:
    import os as _os2

    _here = _os2.path.dirname(_os2.path.abspath(__file__))
    _entries = [i for i, p in enumerate(_sys.path)
                if p and _os2.path.abspath(p) == _here]
    for _i in reversed(_entries):
        del _sys.path[_i]
    try:
        _sys.modules.pop("sitecustomize", None)
        import sitecustomize  # noqa: F401  (the one this file displaced)
    except ImportError:
        pass
    finally:
        if _entries:
            _sys.path.insert(min(_entries), _here)
except Exception:
    pass
