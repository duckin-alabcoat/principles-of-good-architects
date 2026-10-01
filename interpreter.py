#!/usr/bin/env python3
"""Which Python the substrate runs on — resolved, never inherited from PATH.

WI-0328. The land and test verbs were started as a bare `python3`, so the interpreter
was whatever the operator's shell happened to resolve. On one machine that was a newer,
separately installed Python while `/usr/bin/python3` is the older system one, and the two
do not agree about the suite's verdict. On 2026-09-10 (dispatch D-069e3d) five lanes built green and then every one
of them was refused at the gate; two lanes independently patched the same file to
satisfy the newer interpreter, which is the collision an undeclared dependency always eventually
produces. WI-0320 had already normalised every SUBPROCESS the gate spawns to
`sys.executable`. What it could not reach is the process that CHOOSES `sys.executable`
in the first place — the entry point.

So the fix sits at the entry: each entry point calls `ensure()` before it imports
anything heavy, and re-execs into the resolved interpreter when it is not already
running under it. Everything downstream then inherits the right answer for free,
because everything downstream already spawns `sys.executable`. That is why this is a
re-exec and not, say, a rewrite of the ~35 `python3` call sites in `poga`: fixing the
callers fixes the callers, while fixing the entry also fixes a human typing
`python3 session.py merge`, which is exactly how the 09-10 wave was landed by hand.

RESOLUTION ORDER, highest first:
  1. $POGA_PYTHON         — the operator's explicit override, for one command
  2. session.config.json  — `layout.interpreter`, then top-level `interpreter`; the
                            same two tiers `sessionlib.config.layout_of` reads, so a
                            member declares this like any other arrangement key
  3. /usr/bin/python3     — on macOS only, and only when it is actually there
  4. nothing              — no pin; run on under whatever started us

WHY `/usr/bin/python3` IS THE macOS DEFAULT, rather than "whatever is newest": it is
at a fixed path on every macOS carrying CommandLineTools, so it is the same answer on
every machine in the fleet and it does not move when somebody runs `brew upgrade`.
The property being bought here is that two machines agree, not that the interpreter is
current. A member that needs a newer one declares `interpreter` and gets it — which is
also the escape hatch for any member whose code needs syntax 3.9 cannot parse.

FAILS OPEN, everywhere. A guard that cannot run the tool it guards is worse than the
drift it prevents. An unreadable config, a declared interpreter that is missing or not
executable, an `execv` that raises — each leaves the process running exactly as it
would have without this module. The one thing that is NOT silent is
`session.py interpreter`, which prints the resolution and names any declared value it
had to discard: a check that folds "couldn't tell" into "checked and it's fine" is the
`declare-what-a-check-assumes` failure, and a silent pin that isn't in force is worse
than no pin, because it certifies a determinism nobody has.

NEVER LOOPS. `ensure` puts POGA_PYTHON_REEXEC in the environment before `execv` and
returns immediately when it is already set, so a target that somehow is not the target
costs one extra exec, not a fork bomb.
"""

from __future__ import annotations

import json
import os
import sys

CONFIG_NAME = "session.config.json"
KEY = "interpreter"
ENV_OVERRIDE = "POGA_PYTHON"
ENV_GUARD = "POGA_PYTHON_REEXEC"
CACHE_REL = os.path.join(".session-state", "interpreter-identity.json")

# macOS only, deliberately. On Linux `/usr/bin/python3` is the distro interpreter and
# pinning the fleet to it is a policy this item never argued for; a Linux member
# declares the key instead. An undeclared default that silently reaches further than
# its evidence is how the original defect got in.
PLATFORM_DEFAULT = "/usr/bin/python3" if sys.platform == "darwin" else ""


def declared(cfg):
    """The `interpreter` a config declares, by the two tiers `layout_of` reads.

    Deliberately six lines of its own rather than an import of
    `sessionlib.config.layout_of`: this runs BEFORE the heavy imports and inside
    processes (`curate/run_suite.py`) that never load `sessionlib` at all, so paying a
    7000-line module import to read one string would cost more than the drift it
    prevents. `tests/test_interpreter.py` pins the two implementations against each
    other over a matrix of config shapes, so the equivalence is checked rather than
    believed — which is the condition under which a second copy is allowed at all."""
    if isinstance(cfg, dict):
        block = cfg.get("layout")
        if isinstance(block, dict):
            v = block.get(KEY)
            if isinstance(v, str) and v:
                return v
        v = cfg.get(KEY)
        if isinstance(v, str) and v:
            return v
    return ""


def _read_config(root):
    """The member's config as a dict, or None. Never raises — a malformed config must
    not be able to stop the harness from starting."""
    try:
        with open(os.path.join(str(root), CONFIG_NAME), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return None
    return cfg if isinstance(cfg, dict) else None


def usable(path):
    """True when `path` is something this process could actually exec."""
    try:
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)
    except Exception:
        return False


def resolve(root):
    """`(path, source, problem)` — what to run under, where that came from, and what
    went wrong if a value that WAS declared had to be discarded.

    `path` is "" when nothing is pinned; `problem` is "" unless a declared value could
    not be used. A broken declaration falls through to "no pin", NOT to the platform
    default: silently substituting a different interpreter for the one a member asked
    for would hide the config error behind a machine that still worked."""
    env = (os.environ.get(ENV_OVERRIDE) or "").strip()
    if env:
        if usable(env):
            return env, "$" + ENV_OVERRIDE, ""
        return "", "$" + ENV_OVERRIDE, (
            "%s=%s is not an executable file" % (ENV_OVERRIDE, env))

    decl = declared(_read_config(root))
    if decl:
        cand = decl if os.path.isabs(decl) else os.path.join(str(root), decl)
        if usable(cand):
            return cand, CONFIG_NAME, ""
        return "", CONFIG_NAME, (
            "%s declares %s=%r, which is not an executable file" % (CONFIG_NAME, KEY, decl))

    if usable(PLATFORM_DEFAULT):
        return PLATFORM_DEFAULT, "macOS default", ""
    return "", "", ""


def _paths_equal(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return a == b


def _stamp(path):
    st = os.stat(path)
    return "%d:%d:%d" % (st.st_mtime_ns, st.st_size, st.st_ino)


def resolved_executable(want, root):
    """What `sys.executable` reports inside a process started as `want`, or "".

    `/usr/bin/python3` on macOS is Apple's xcrun SHIM, not a symlink: a process started
    through it reports `sys.executable` as the CommandLineTools binary, and `realpath`
    of the two agree about nothing. Without this the "am I already it?" test would be
    false forever and EVERY entry point would pay one extra interpreter start — about a
    tenth of a second, which is precisely the per-verb tax the `poga` staleness check
    refused to pay (WI-0222). So ask `want` once and cache the answer against the file's
    mtime/size/inode.

    Worst case equals the no-cache case: a miss costs the same interpreter start the redundant
    exec would have cost, and then never again. Every failure here — no cache dir, a
    read-only scratch worktree, a hung interpreter — degrades to that same one exec."""
    try:
        stamp = _stamp(want)
    except Exception:
        return ""
    cache = os.path.join(str(root), CACHE_REL)
    try:
        with open(cache, encoding="utf-8") as fh:
            hit = json.load(fh)
        if isinstance(hit, dict) and hit.get("path") == want and hit.get("stamp") == stamp:
            got = hit.get("executable")
            if isinstance(got, str) and got:
                return got
    except Exception:
        pass

    import subprocess  # local: the cache-hit path must not pay this import
    try:
        proc = subprocess.run(
            [want, "-c", "import sys; print(sys.executable)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    got = proc.stdout.decode("utf-8", "replace").strip()
    if not got:
        return ""
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        tmp = cache + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"path": want, "stamp": stamp, "executable": got}, fh)
        os.replace(tmp, cache)
    except Exception:
        pass
    return got


def same(want, running, root):
    """Whether a process started as `want` is the process we are already in."""
    if _paths_equal(want, running):
        return True
    ident = resolved_executable(want, root)
    return bool(ident) and _paths_equal(ident, running)


def _flags():
    """The interpreter flags worth carrying across the exec.

    `sys.argv` holds none of them, so an exec that forwarded only argv would silently
    drop them. `-B` is the load-bearing one: the mutation harness runs with bytecode
    writing off, and losing it makes a rewritten module read from a stale `.pyc` — a
    mutation that reads as "not caught" when it never ran."""
    out = []
    if sys.dont_write_bytecode:
        out.append("-B")
    if sys.flags.optimize:
        out.append("-" + "O" * min(sys.flags.optimize, 2))
    if sys.flags.no_user_site:
        out.append("-s")
    if sys.flags.ignore_environment:
        out.append("-E")
    if sys.flags.isolated:
        out.append("-I")
    for w in sys.warnoptions:
        out += ["-W", w]
    return out


def ensure(root):
    """Re-exec into the resolved interpreter unless we are already running under it.

    Returns None and never raises. Call it from an entry point BEFORE the heavy
    imports — everything imported before the exec is thrown away."""
    try:
        if os.environ.get(ENV_GUARD):
            return
        argv0 = sys.argv[0] if sys.argv else ""
        if argv0 in ("-c", "-", ""):
            # `python3 -c '...'`: the source is not in argv, so an exec would run a
            # DIFFERENT program rather than the same one elsewhere. Nothing imports
            # this from a `-c` snippet today; the failure if something did would be
            # silent and total, which is what earns the two lines.
            return
        if not os.path.isfile(argv0):
            # `sys.argv[0]` is not always a path. `python3 -m unittest` REWRITES it to
            # the descriptive string "python3.14 -m unittest", and forwarding that to
            # `execv` produces `can't open file '<cwd>/python3.14 -m unittest'` — the
            # process dies on the line whose entire job is to make it deterministic.
            # Found by running it, not by reading it: the 3.9 pin matched so nothing
            # re-execed, and the failure only appeared when the suite was started on
            # the other interpreter.
            return
        want, _source, _problem = resolve(root)
        if not want or same(want, sys.executable, root):
            return
        os.environ[ENV_GUARD] = want
        try:
            os.execv(want, [want] + _flags() + list(sys.argv))
        except Exception:
            os.environ.pop(ENV_GUARD, None)
            raise
    except Exception:
        return


def running_line():
    """One line naming the interpreter this process is actually running on."""
    return "python %s (%s)" % (
        ".".join(map(str, sys.version_info[:3])), sys.executable)


def report(root):
    """The lines `session.py interpreter` prints: what was chosen, from where, and
    whether the pin is actually in force."""
    want, source, problem = resolve(root)
    lines = ["running:  " + running_line()]
    if want:
        lines.append("pinned:   %s  (from %s)" % (want, source))
        lines.append("in force: %s" % (
            "yes" if same(want, sys.executable, root)
            else "NO — this process is not running on the pinned interpreter"))
    else:
        lines.append("pinned:   nothing — this process runs on whatever started it")
    if problem:
        lines.append("problem:  " + problem)
    lines.append("order:    $%s, then %s (layout.%s / %s), then %s" % (
        ENV_OVERRIDE, CONFIG_NAME, KEY, KEY,
        PLATFORM_DEFAULT or "no platform default on this OS"))
    return lines
