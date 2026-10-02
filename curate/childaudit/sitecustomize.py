"""Arm the child-read audit, then hand `sitecustomize` back to whoever owned it.

WI-0337. This file exists to be FOUND. `curate/gate_inputs.py` puts this directory on
the probe's `PYTHONPATH`, and `site` imports the first `sitecustomize` on `sys.path` in
every Python process that starts — so this runs in every descendant of the probe, and
in nothing else, because `poga_child_audit.arm()` is a no-op without the probe's two
environment variables.

CHAIN, DO NOT SHADOW. Being first on the path is what makes the arming reliable; it is
also what hides any `sitecustomize` that was already there. A package-managed Python
install can already ship one that reshuffles `sys.path`, rewrites `sys.executable` to
its own install prefix and fixes up `site.PREFIXES`. Swallowing
it would change what a measured child IS — and a measurement that alters its subject is
not a measurement. So this drops its own directory from `sys.path`, re-imports under
the same name to let the displaced one run, and only then puts the entry back. The
audit itself is armed only after that chain, so the displaced file's own reads are
never charged to the child.

Two details that are easy to get wrong and were:

  - The entry is REMOVED AND RE-ADDED, never snapshot-and-restored. The displaced
    `sitecustomize` mutates `sys.path` deliberately — that is most of what a package
    manager's does — so restoring a saved copy of the list afterwards would quietly undo the very
    work that was chained to.
  - `site` ignores the module object `import sitecustomize` returns, so replacing the
    `sys.modules` entry mid-import is harmless; what matters is only that the other
    file's top level executes.

Nothing here may raise: this runs before the child's own program does, and a traceback
out of `site` is an unexplainable failure in a process that never asked to be measured.
"""

import sys as _sys

try:
    import os as _os

    _here = _os.path.dirname(_os.path.abspath(__file__))
    _entries = [i for i, p in enumerate(_sys.path)
                if p and _os.path.abspath(p) == _here]
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

# ARM LAST, after the chain (WI-0484). Whatever this file displaced is harness, not the
# child's program: under the sharded runner it is the store guard's shim
# (curate/storeguard_child), and armed first, its own .py/.pyc opens were charged to
# every measured child. Only where that bytecode was not already cached outside the repo,
# though: Linux's in-tree __pycache__, or a cold cache on macOS, so the record depended
# on the machine rather than on what the child read.
try:
    import poga_child_audit as _poga_child_audit

    _poga_child_audit.arm()
except Exception:  # never break a child over its own measurement
    pass
