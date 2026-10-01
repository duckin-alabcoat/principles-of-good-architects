#!/usr/bin/env python3
"""Build the public cut of POGA from the internal trunk, audited, from one manifest.

The public mirror is a GENERIC POGA SYSTEM: no trace of any member project, cut fresh as a
single commit on every update (the 2026-09-17 scrub-boundary rulings; WI-0381).

What the manifest ENFORCES is directory boundaries: a withheld tree (sessions, work items,
comms, the roster's rows) is never copied. That is structural. It says nothing about the
PROSE inside an included file -- an ADR can still describe a member, a machine or the
operator in plain words. That prose is GATED, not proven: the classes below refuse every
known shape of such a sentence, and a clean gate means no known shape matched, never that
the prose is harmless (consultant brief 2026-09-27, item 8). Where an included file cannot
pass, it is withheld or replaced by an authored generic version (`replace`, the overlays).

    python3 curate/public_cut.py --out <dir> [--check] [--edition community]
    python3 curate/public_cut.py --verify-publish <scratch-clone> [--edition community]

`--edition` picks which publication identity the cut is built for (WI-0450). The default is
the manifest's own identity; any other edition is a row of the manifest's `editions` table.
An edition changes the identity only: the boundary, the overlays and every privacy gate are
the default's, at full strength.

`--check` does everything except write, and exits non-zero on any finding. That is the form
the audit and the tests call.

`--out` FAILS CLOSED. The cut is built and gated in memory first. A refused cut is written,
if at all, only to `<out>.REJECTED` with a `REJECTED-DO-NOT-PUBLISH.txt` at its top, and
`<out>` is left exactly as it was. A clean cut is written to a staging directory and then
swapped into `<out>`, so a half-written cut never sits at the publishable path.

`--verify-publish` gates the COMMITTED BYTES of the one commit about to be pushed (its tree
read blob by blob from git, its author, committer, message, refs and tags), refuses a dirty
or untracked working tree, and prints the commit and tree hashes it checked.

Two gates run over every file that would be published:

  * `curate/scrub.py`, REUSED WHOLE. Its nine known-shape classes are the floor and this
    tool does not reimplement them -- scrub's own module docstring names this tool as its
    intended caller.

  * an IDENTITY gate of more classes this tool owns: `member-name`, `descriptive-marker`,
    `home-path`, `email` and `private-account`. They are here and not in scrub.py because scrub gates the production mail
    queue and the outbox, and a new refusal class there would fire on live traffic that has
    nothing to do with the public mirror. Scrub's nine are about a host and a person; they
    carry no email class at all and know nothing about member project names.

    It also carries the DISCLOSURE classes (`network-topology`, `operator-availability`,
    `operator-preference`): vocabulary, driven from the manifest, that describes the
    operator's network posture, whereabouts or habits in prose. Their exemptions are
    value-level -- one row shields one exact occurrence in one file, never a whole file.

One more check reads the MODE, not the prose: `doc-script-not-executable` refuses a cut in
which a published Markdown doc tells a reader to run a published `*.sh` directly (a
backticked span or a fenced line that starts with its path, not with `bash`/`sh`) while
that script is not executable -- in the cut for `--check`/`--out`, in the committed tree
(100755) for `--verify-publish`.

The manifest's optional `add` table ships a file that exists ONLY in the public cuts
(`.github/workflows/ci.yml`: the internal origin is private and must run no Actions). Its
source lives under public-overlay/, and it is gated like any other file.

The boundary itself is `curate/public_cut_manifest.json` -- a file, not a table in here, so
a person can read it and a diff shows when it moved.

Run it from a lane on devbox. Never from the Runner, never from a deploy tree.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import datetime
import fnmatch
import json
import os
import pathlib
import posixpath
import re
import shutil
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import scrub  # noqa: E402  (curate/ is on the path from above)

#: The repo the cut is taken FROM. A module global so a test can point the tool at a
#: fixture tree instead of the real trunk -- the `gather.ROOT` pattern.
ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The boundary. Also a module global, and for the same reason.
MANIFEST_PATH = ROOT / "curate" / "public_cut_manifest.json"

RECEIPT_NAME = "PUBLIC-CUT-RECEIPT.md"

#: The five lists the manifest must carry. A manifest missing one is refused rather than
#: defaulted: an empty `exclude` that arrived by accident looks exactly like a boundary
#: that deliberately excludes nothing, and only one of those is safe to publish. The same
#: holds for `replace`: a lost list would silently ship every source it was replacing.
REQUIRED_LISTS = ("include", "exclude", "sample", "generalize", "replace")


class ManifestError(Exception):
    """The boundary could not be read. Never recoverable by guessing."""


# --------------------------------------------------------------------------- the manifest

def load_manifest(path=None):
    """Read and validate the boundary."""
    p = pathlib.Path(path) if path else MANIFEST_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ManifestError(f"the manifest could not be read at {p}: {e}")
    except ValueError as e:
        raise ManifestError(f"the manifest at {p} is not valid JSON: {e}")
    missing = [k for k in REQUIRED_LISTS if k not in data]
    if missing:
        raise ManifestError(
            f"the manifest at {p} is missing {', '.join(missing)}. Every list is required "
            f"even when empty -- a list that is absent cannot be told apart from a "
            f"boundary that deliberately holds nothing.")
    return data


#: The edition a manifest describes when no `--edition` is given.
DEFAULT_EDITION = "showcase"


def apply_edition(manifest, name=None):
    """The manifest as one EDITION of the cut sees it (WI-0450).

    The showcase is the manifest as written. Any other edition is a row of `editions` and
    swaps the identity, nothing else: its own `public_identity`, its own `private_accounts`
    (the names it must refuse), more `extra_names` and
    `substring_names` (a name inside `x_name_y` has no word boundary), and rows it runs
    before the table. Every substitution that WRITES the showcase's repo or address writes
    the edition's instead, so no row can put a refused identity into this cut. The boundary,
    the overlays and every gate class stay exactly as strong as the showcase's.

    Identities are configured independently, so each one refuses every name it is told to
    keep out, on letters and digits, as the showcase does.
    """
    if not name or name == DEFAULT_EDITION:
        return manifest
    eds = manifest.get("editions", {})
    if name not in eds:
        raise ManifestError("the manifest has no edition %r (it has: %s)"
                            % (name, ", ".join([DEFAULT_EDITION] + sorted(eds)) or "none"))
    ed = eds[name]
    for k in ("public_identity", "private_accounts"):
        if not ed.get(k):
            raise ManifestError("edition %r names no %s; an edition without one cannot be "
                                "told apart from the showcase" % (name, k))
    import copy
    man = copy.deepcopy(manifest)
    ig = man.setdefault("identity_gate", {})
    old, new = ig.get("public_identity", {}), ed["public_identity"]
    swaps = [(old[k], new[k]) for k in ("repo", "email") if old.get(k) and new.get(k)]
    g = man["generalize"]
    for row in g.get("substitutions", []):
        for a, b in swaps:
            for k in ("replace", "code_replace"):
                if a in row.get(k, ""):
                    row[k] = row[k].replace(a, b)
    g["substitutions"] = list(ed.get("substitutions", [])) + g.get("substitutions", [])
    ig["public_identity"] = dict(new)
    ig["private_accounts"] = list(ed["private_accounts"])
    ig["extra_names"] = sorted(set(ig.get("extra_names", [])) | set(ed.get("extra_names", [])))
    ig["substring_names"] = sorted(set(ig.get("substring_names", []))
                                   | set(ed.get("substring_names", [])))
    man["edition"] = name
    return man


def member_roster(root, exempt=()):
    """Member system ids, read from portfolio.md AT RUN TIME.

    Read rather than listed in the manifest on purpose: the roster is the thing a new
    member joins, so a hard-coded copy would let a member added tomorrow slip into the cut
    unnamed. That is the brief's own reason for the refusal existing at all.
    """
    src = root / "portfolio.md"
    if not src.is_file():
        return []
    rows = re.findall(r"\|\s*`([a-z0-9-]+)`\s*\|\s*`([a-z0-9-]+-arch)`\s*\|",
                      src.read_text(encoding="utf-8"))
    low = {e.lower() for e in exempt}
    return [sid for sid, _arch in rows if sid.lower() not in low]


# --------------------------------------------------------------------- what gets copied

def _rel(p, root):
    return str(pathlib.PurePosixPath(p.relative_to(root)))


def _exclude_reason(rel, manifest):
    """Why `rel` is not published, in a phrase -- or None if it may be."""
    for row in manifest["exclude"]:
        ex = row["path"]
        if rel == ex or rel.startswith(ex + "/"):
            return row.get("why") or f"excluded by the manifest ({ex})"
    for row in manifest.get("exclude_suffixes", []):
        if rel.endswith(row["suffix"]):
            return row.get("why") or f"excluded suffix {row['suffix']}"
    name = rel.rsplit("/", 1)[-1]
    for row in manifest.get("exclude_glob", []):
        # `root_only`: the glob names a top-level file, and matching it by basename anywhere
        # took bootstrap-kit/session-handoff-template.md with it -- a kit bootstrap needs.
        if row.get("root_only"):
            if "/" not in rel and fnmatch.fnmatch(rel, row["glob"]):
                return row.get("why") or f"excluded glob {row['glob']}"
            continue
        if fnmatch.fnmatch(name, row["glob"]) or fnmatch.fnmatch(rel, row["glob"]):
            return row.get("why") or f"excluded glob {row['glob']}"
    return None


def _is_binary(rel, manifest):
    sufs = tuple(manifest.get("binary_suffixes", []))
    return bool(sufs) and rel.lower().endswith(sufs)


def plan(root, manifest):
    """Decide, for the whole tree, what is published and what is not.

    Returns (published, dropped, pending, binaries) where `published` is a list of
    repo-relative paths and the other three are lists of (path, reason).
    """
    published, dropped, pending, binaries = [], [], [], []
    seen = set()
    for row in manifest["include"]:
        inc = row["path"]
        src = root / inc
        if not src.exists():
            # NOT a failure: a listed include may be authored after the manifest lists it
            # (WI-0381 listed AGENTS.md, LICENSE and NOTICE before WI-0383 wrote them), and
            # failing would make the manifest un-landable in between.
            pending.append((inc, row.get("why") or "listed in include, absent from the tree"))
            continue
        cands = [src] if src.is_file() else sorted(q for q in src.rglob("*") if q.is_file())
        for q in cands:
            rel = _rel(q, root)
            if rel in seen:
                continue
            seen.add(rel)
            why = _exclude_reason(rel, manifest)
            if why is not None:
                dropped.append((rel, why))
            elif _is_binary(rel, manifest):
                # scrub reads with errors="replace", so a binary decodes to mojibake and
                # pattern-scans rather than skipping -- which invents mac-address hits out
                # of byte runs. Dropped and NAMED, because a file that was never checked
                # must not read as a file that passed.
                binaries.append((rel, "binary: not scannable as text, so not published"))
            else:
                published.append(rel)
    return sorted(published), sorted(dropped), pending, sorted(binaries)


def measure_boundary(root, manifest, shipped=()):
    """Every exclude entry, with how many files actually sit behind it.

    The tree walk alone cannot produce this. It only ever visits paths under an `include`
    entry, so a wholly separate tree — `sessions/`, `work-items/`, `comms/` — is never
    reached and would be reported as nothing dropped. A receipt whose "withheld" section is
    empty because the walk never looked is a false clean read of exactly the kind the
    exclude list exists to prevent: the brief's own words are that these are "listed
    explicitly so their absence is a decision and not an accident".

    So the boundary is measured from the MANIFEST against the tree, not from the walk.
    """
    out = []
    for row in manifest["exclude"]:
        src = root / row["path"]
        if not src.exists():
            n = 0
        elif src.is_file():
            n = 1
        else:
            n = sum(1 for q in src.rglob("*") if q.is_file())
        # A sample file under an excluded tree ships; counting it as withheld overstates
        # the boundary (the first cut's receipt said `drills` withheld 6 while 4 shipped).
        n -= sum(1 for s in shipped if s == row["path"] or s.startswith(row["path"] + "/"))
        out.append((row["path"], row.get("why", ""), max(n, 0), row.get("ships")))
    return out


# ------------------------------------------------------------------- the reduced files

def reduce_portfolio(text, ctx=None):
    """The roster's heading and Notes, with an EMPTY table.

    The roster IS the member list -- the one file whose whole content is the trace the
    rulings forbid. It still ships, because a public cut that simply lacks the file says
    nothing about whether a roster exists; one that ships it empty says the shape is real
    and the rows are withheld.
    """
    out, dropped = [], 0
    for line in text.splitlines():
        s = line.strip()
        is_row = s.startswith("|") and s.endswith("|")
        is_head = is_row and "System ID" in s
        is_sep = is_row and set(s) <= set("|-: ")
        if is_row and not is_head and not is_sep:
            dropped += 1
            continue
        out.append(line)
    return "\n".join(out) + "\n", dropped


def reduce_registry(text, ctx=None):
    """The deploy registry with the `self` row alone -- POGA deploying itself."""
    data = json.loads(text)
    if isinstance(data.get("systems"), dict):
        # The registry keeps its rows under `systems`; the first reducer looked only at the
        # top level and shipped no row at all (round-2 audit). POGA's own row is the one
        # marked `self`, or, failing that, the one named `federation`.
        rows = data["systems"]
        keep = {k: v for k, v in rows.items()
                if isinstance(v, dict) and (v.get("self") is True or k == "federation")}
        data["systems"] = keep
        _drop_notes(data)  # the notes count the fleet and name its rows (WI-0448)
        data = _strip_comment_keys(data)  # nested `//` notes too (round 4)
        return json.dumps(data, indent=2) + "\n", len(rows) - len(keep)
    kept, dropped = {}, 0
    for k, v in data.items():
        if k.startswith("_"):
            kept[k] = v
        elif isinstance(v, dict) and v.get("self") is True:
            kept[k] = v
        else:
            dropped += 1
    return json.dumps(kept, indent=2) + "\n", dropped


def reduce_mailboxes(text, ctx=None):
    """Structurally valid and empty: the mailbox list is a member list with residency."""
    data = json.loads(text)
    dropped = 0
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k.startswith("_") or _is_note_key(k):
                continue  # the notes count members and say where they reside (WI-0448)
            elif isinstance(v, list):
                dropped += len(v)
                out[k] = []
            elif isinstance(v, dict):
                dropped += len(v)
                out[k] = {}
            else:
                out[k] = v
    else:
        dropped, out = len(data), []
    return json.dumps(out, indent=2) + "\n", dropped


def _drop_notes(data):
    """Drop every `_`-prefixed note key at the top level; returns how many went.

    The notes on the member-list files are working history: how many members were
    locatable, which were parked, which row pointed where (WI-0448).
    """
    notes = [k for k in data if isinstance(k, str) and (k.startswith("_") or _is_note_key(k))]
    for k in notes:
        del data[k]
    return len(notes)


def fleet_cadence_public(text, ctx=None):
    """The cadence config with the federation's own row only.

    Every other row names a member and whether it is parked -- a member list with status.
    """
    data = json.loads(text)
    dropped = _drop_notes(data)
    data = _strip_comment_keys(data)  # a row's own notes too, at any depth
    rows = data.get("systems", {})
    keep = {k: v for k, v in rows.items() if k == "federation"}
    dropped += len(rows) - len(keep)
    data["systems"] = keep
    data = {"_note": "Per-system cadence expectations. Parked systems are staleness-exempt; "
                     "a system not listed inherits the defaults.", **data}
    return json.dumps(data, indent=2) + "\n", dropped


def blank_profile(text, ctx=None):
    """The kit's blank profile template, verbatim. Nothing of the real profile is read."""
    return text, 0


def manifest_public(text, ctx=None):
    """The boundary as it ships: every list that SAYS a member name is withheld.

    The first cut published this file after running it through its own table, which
    turned the table into `Member 1` -> `Member 1` -- a tool that substitutes nothing --
    while the protect list beside it still told a reader which placeholder was which.
    Shipping the table verbatim would be worse: it is the decoder ring. So the public copy
    keeps the structure (include, exclude, sample, the gate's shape) and withholds the
    three name lists, saying so in place.
    """
    import json as _json
    data = _json.loads(text)
    # The editions block names every configured identity side by side, so shipping it
    # would join them. The cut ships only its own edition's identity.
    data.pop("editions", None)
    data.pop("_editions_note", None)
    ident = (ctx or {}).get("public_identity")
    if ident:
        data.setdefault("identity_gate", {})["public_identity"] = dict(ident)
    g = data.get("generalize", {})
    n = len(g.get("substitutions", [])) + len(g.get("protect", []))
    g["substitutions"] = []
    g["protect"] = []
    g["_withheld"] = ("The substitution table and the protected phrases are withheld from "
                      "the public cut: they name the members they exist to hide. A public "
                      "run of this tool therefore substitutes nothing and relies on the "
                      "scrub gate and the identity gate alone.")
    for k in [k for k in g if (k.startswith("_") and k != "_withheld") or k == "retired_rows"]:
        del g[k]  # the notes explain the table by example -- and the examples are names
    ig = data.get("identity_gate", {})
    n += len(ig.get("extra_names", [])) + len(ig.get("substring_names", []))
    n += len(ig.get("descriptive_markers", []))
    ig["extra_names"] = []
    ig["substring_names"] = []
    ig["descriptive_markers"] = []  # a list of domains is a description of the members
    n += len(ig.get("private_accounts", []))
    ig["private_accounts"] = []  # the list IS the link the class exists to refuse
    for k in [k for k in ig if k.startswith("_private_accounts")]:
        del ig[k]
    ig.pop("english_ids", None)  # names the English word a placeholder stands for
    terms = (ctx or {}).get("terms", [])
    for row in data.get("exclude", []):
        if _names_member(row.get("path", ""), terms):
            row["path"] = MASKED_PATH
    # WITHHELD, all four (WI-0448). The disclosure vocabulary reads as a list of what the
    # operator's own network and habits look like (its examples were drawn from the leaks it
    # refuses), the exemptions name the files that tripped it, and the replace and rename
    # rows carry the ORIGINAL paths -- a slug like the one a rename exists to hide.
    n += len(ig.get("disclosure_classes", [])) + len(ig.get("disclosure_exempt", []))
    n += len(data.get("replace", [])) + len(data.get("rename", []))
    if "disclosure_classes" in ig:
        ig["disclosure_classes"] = []
    if "disclosure_exempt" in ig:
        ig["disclosure_exempt"] = []
    n += len(g.get("relabel", [])) + len(g.get("review", []))
    g["relabel"] = []  # a relabel table is the decoder from role name back to hardware
    g.pop("review", None)  # rulings on terms name the terms
    data["replace"] = []
    if "rename" in data:
        data["rename"] = []
    # A single withheld file's NAME and its reason can describe what it holds (a port
    # table, the founding brief, a credential inventory) -- round 3. Trees and reduced
    # slots keep their names; the rest are masked. The long notes go too: they explain the
    # boundary by the leaks it was built against.
    for row in data.get("exclude", []):
        base = row.get("path", "").rsplit("/", 1)[-1]
        if not row.get("ships") and "." in base and row["path"] != MASKED_PATH:
            row["path"] = MASKED_PATH
            row["why"] = WITHHELD_WHY
    for d in (data, ig):
        for k in [k for k in d if k.startswith("_") and not k.startswith("_withheld")]:
            del d[k]
    data["_withheld_rows"] = ("The disclosure vocabulary, its exemptions, and the replace and "
                              "rename rows are withheld from the public cut: they describe "
                              "or point at what they exist to hide.")
    return _json.dumps(data, indent=2, ensure_ascii=False) + "\n", n


MASKED_PATH = "(a withheld file)"

#: The ONLY reason the public copy prints for a masked row. A reason that says what a
#: withheld record is about (its kind, its subject, whose it is) characterises the thing
#: the row exists to hide (WI-0449, reconstruction audit).
WITHHELD_WHY = "withheld from this copy"


def _names_member(path, terms):
    """True when a path names a member -- the receipt and the shipped manifest print such a
    path masked, because a renamed path can keep the half of a compound name the table
    did not cover."""
    return any(_name_pattern(t).search(path) or t in path.lower() for t in terms)


def gitignore_public(text, ctx=None):
    """The data/system boundary as it ships, plus a re-include for each top-level path the
    cut publishes that the internal ignore rules would hide.

    Internally `users/` and `proposed-edits/` are data and ignored. In the cut they hold
    the blank profile and the sampled briefs, and a `git add -A` of the tree silently left
    them out (the first cut's audit: 522 of 527 files tracked).
    """
    published = sorted({r.split("/")[0] for r in (ctx or {}).get("published", [])})
    lines = text.splitlines()
    add = []
    for pat in lines:
        s = pat.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        bare = s.strip("/")
        if bare in published:
            add.append("!" + s)
    if add:
        lines += ["", "# PUBLIC CUT: these hold what the cut publishes (the sample, the blank",
                  "# profile), so the internal data rules above are lifted for them."] + add
    return "\n".join(lines) + "\n", 0


#: What the public session config says in place of the operator's own values (WI-0448).
#: The live file names the operator's time zone, machine names and profile path;
#: the shape and every behavioural key stay, so the cut's harness still runs.
PUBLIC_SESSION_CONFIG = {
    "timezone": "UTC",
    "machine_map": {"example-laptop": "Laptop", "example-runner": "Runner",
                    "example-dev": "DevBox"},
    "user_profile": "users/operator/profile.md",
}


def _is_note_key(k):
    """A key that carries prose ABOUT the file rather than configuration: `notes`, `_note*`,
    `_comment*`, `//*`. Every "ships without its notes" reducer drops all four shapes at
    every depth -- the first reducer dropped only `//`, and deploy.json shipped its `notes`
    field of measured host facts under a receipt that said the notes were stripped
    (WI-0449, reconstruction audit)."""
    return isinstance(k, str) and (k == "notes" or k.startswith(("_note", "_comment", "//")))


def _strip_comment_keys(v):
    if isinstance(v, dict):
        return {k: _strip_comment_keys(x) for k, x in v.items() if not _is_note_key(k)}
    if isinstance(v, list):
        return [_strip_comment_keys(x) for x in v]
    return v


def session_config_public(text, ctx=None):
    """The session config with the operator's values replaced and the `//` notes dropped.

    Generated from the live file on every cut rather than kept as an authored copy, so a new
    key reaches the cut the day it lands. The notes go because they are the operator's
    working history (what was measured on which machine, whose terminal, which member's
    file grew); the ADRs they cite carry the reasoning. Returns the count of values changed
    or notes dropped.
    """
    data = json.loads(text)
    n = sum(1 for k in _walk_keys(data) if _is_note_key(k))
    out = _strip_comment_keys(data)
    for k, v in PUBLIC_SESSION_CONFIG.items():
        if k in out:
            n += out[k] != v
            out[k] = v
    out = {"//": "The public copy of this system's session config. The operator's own values "
                 "(time zone, machine names, profile path) are replaced and the per-key notes "
                 "are withheld; the ADRs each key cites carry the reasoning.", **out}
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n", n


def _walk_keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _walk_keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_keys(x)


REDUCERS = {
    "session-config-public": session_config_public,
    "fleet-cadence-public": fleet_cadence_public,
    "json-notes-stripped": lambda text, ctx=None: (
        json.dumps(_strip_comment_keys(json.loads(text)), indent=2, ensure_ascii=False) + "\n",
        sum(1 for k in _walk_keys(json.loads(text)) if _is_note_key(k))),
    "portfolio-shell": reduce_portfolio,
    "registry-self-row-only": reduce_registry,
    "empty-mailboxes": reduce_mailboxes,
    "blank-operator-profile": blank_profile,
    "manifest-public": manifest_public,
    "gitignore-public": gitignore_public,
}


def reduced_files(root, manifest, ctx=None, overlays=None):
    """Emit the exclude rows that ship in a reduced form rather than being dropped.

    Driven from the exclude list and NOT from the tree walk, because two of the three
    (`portfolio.md`, `mailboxes.json`) sit outside every include path and the walk would
    never reach them.

    A slot an overlay fills (`overlays`, from `replace`) ships the overlay's authored text
    and its reducer never runs. `ships: "public-overlay"` names a slot that has no reducer
    at all: with no `replace` row behind it, it is owed and nothing ships.
    """
    overlays = overlays or {}
    made, owed = [], []
    for row in manifest["exclude"]:
        ships = row.get("ships")
        if not ships:
            continue
        out_rel = row.get("ships_to", row["path"])
        if out_rel in overlays:
            if overlays[out_rel] is not None:  # None: the overlay failed, nothing ships
                made.append((out_rel, OVERLAY_SHIPS, overlays[out_rel]["text"], 0))
            continue
        if ships not in REDUCERS:
            # A stated intention with no reducer behind it yet.
            owed.append((row["path"], ships, row.get("why", "")))
            continue
        # `ships_from` / `ships_to`: the emitted file is built from something OTHER than the
        # withheld one -- `users/` ships a blank profile from the kit template, never a
        # reduced copy of the operator's real one.
        src = root / row.get("ships_from", row["path"])
        if not src.is_file():
            owed.append((row["path"], ships, "absent from the tree"))
            continue
        text, n = REDUCERS[ships](src.read_text(encoding="utf-8"), ctx)
        made.append((out_rel, ships, text, n))
    return made, owed


# --------------------------------------------------------- authored replacements (overlays)

#: Where authored public versions live. Excluded from the cut as a tree: only a `replace`
#: row ships one of its files, and only at the path that row names.
OVERLAY_DIR = "public-overlay"

#: The `ships` value of a reduced slot that only an overlay can fill.
OVERLAY_SHIPS = "public-overlay"


def overlay_plan(root, manifest, published, slots):
    """Resolve the manifest's `replace` rows. Returns (overlays, findings).

    `overlays` maps a published path to {"from", "why", "text"}. Every failure is a
    FINDING, never a skip: a replacement that silently did not happen ships the source it
    was written to replace, which is the leak the overlay exists to stop.

    `published` is the walk's plan; `slots` the reduced files' output paths. A row whose
    `path` is in neither is refused: an overlay for a file the cut would not otherwise
    publish is a new file smuggled in under a replacement's name.
    """
    overlays, found = {}, []
    for row in manifest.get("replace", []):
        path, src, why = row.get("path", ""), row.get("from", ""), (row.get("why") or "").strip()
        label = path or src or "(replace row)"
        if not path or not src or not why:
            found.append(scrub.Finding(label, 0, "overlay-malformed",
                                       "a replace row needs `path`, `from` and a `why`", ""))
            continue
        if not src.startswith(OVERLAY_DIR + "/"):
            found.append(scrub.Finding(label, 0, "overlay-malformed",
                                       "a replace row's `from` must sit under %s/" % OVERLAY_DIR,
                                       ""))
            continue
        if path in overlays:
            found.append(scrub.Finding(label, 0, "overlay-malformed",
                                       "two replace rows name the same `path`", ""))
            continue
        if path not in published and path not in slots:
            found.append(scrub.Finding(
                label, 0, "overlay-orphan",
                "the replace row's `path` is not otherwise published (not in the plan and "
                "not a reduced file), so the overlay would add a file, not replace one", ""))
            continue
        try:
            text = (root / src).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            found.append(scrub.Finding(
                label, 0, "overlay-missing",
                "the overlay `from` could not be read (%s); the source it replaces is NOT "
                "shipped in its place" % e.__class__.__name__, ""))
            # Poison the slot: the path must not fall back to the source it replaces.
            overlays[path] = None
            continue
        overlays[path] = {"from": src, "why": why, "text": text}
    return overlays, found


def add_plan(root, manifest, taken):
    """Resolve the manifest's `add` rows. Returns (adds, findings).

    `add` ships a file that exists ONLY in the public cuts: its source sits under
    public-overlay/ and the internal tree never carries it at its published path. The case
    it exists for is `.github/workflows/ci.yml` -- the internal repo's origin is private and
    must not run Actions, so it can have no root `.github/` of its own.

    Each row is {path, from, why}. `adds` maps a published path to {"from", "why", "text",
    "executable"}. Optional, unlike the five required lists: a lost `add` list omits files,
    it never ships a source in place of an overlay. Every failure is a FINDING:

      * `add-malformed` -- no `path`, `from` or `why`; a `from` outside public-overlay/; a
        `path` that is absolute, climbs out, or lands inside public-overlay/; two rows for
        one `path`.
      * `add-collides`  -- the `path` is already published (the walk, a reduced slot, a
        sample, an overlay): an `add` adds, it never replaces. `replace` does that.
      * `add-shadowed`  -- the internal tree itself carries a file at `path`, which is
        exactly what an `add` exists to keep out of it.
      * `add-missing`   -- the `from` could not be read.

    `from` is read from the trunk. In an exported cut there is no public-overlay/ and the
    file already sits at `path`, so that is read instead -- the `sample` rule.

    The added file is then generalized and gated like any other (the caller does that).
    """
    adds, found = {}, []
    in_cut = not (root / OVERLAY_DIR).is_dir()
    for row in manifest.get("add", []):
        path, src, why = row.get("path", ""), row.get("from", ""), (row.get("why") or "").strip()
        label = path or src or "(add row)"
        if not path or not src or not why:
            found.append(scrub.Finding(label, 0, "add-malformed",
                                       "an add row needs `path`, `from` and a `why`", ""))
            continue
        norm = posixpath.normpath(path)
        if (path.startswith("/") or norm != path or norm.startswith("../")
                or norm == OVERLAY_DIR or norm.startswith(OVERLAY_DIR + "/")):
            found.append(scrub.Finding(label, 0, "add-malformed",
                                       "an add row's `path` must be a plain relative path "
                                       "outside %s/" % OVERLAY_DIR, ""))
            continue
        if not src.startswith(OVERLAY_DIR + "/") or posixpath.normpath(src) != src:
            found.append(scrub.Finding(label, 0, "add-malformed",
                                       "an add row's `from` must sit under %s/" % OVERLAY_DIR,
                                       ""))
            continue
        if path in adds:
            found.append(scrub.Finding(label, 0, "add-malformed",
                                       "two add rows name the same `path`", ""))
            continue
        if path in taken:
            found.append(scrub.Finding(
                label, 0, "add-collides",
                "the add row's `path` is already published; an add never replaces a file "
                "(that is what `replace` is for)", ""))
            continue
        if not in_cut and (root / path).exists():
            found.append(scrub.Finding(
                label, 0, "add-shadowed",
                "the internal tree carries a file at the add row's `path`; an added file "
                "lives only under %s/" % OVERLAY_DIR, ""))
            continue
        f = root / path if in_cut else root / src
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            found.append(scrub.Finding(
                label, 0, "add-missing",
                "the add row's `from` could not be read (%s), so nothing ships at its "
                "`path`" % e.__class__.__name__, ""))
            continue
        adds[path] = {"from": src, "why": why, "text": text,
                      "executable": os.access(f, os.X_OK)}
    return adds, found


# --------------------------------------------------- scripts the docs tell a reader to run

#: The finding class for a published `.sh` that a published doc tells a reader to run
#: directly, while the published file is not executable. External review 2026-09-30: the
#: drill doc said to run `drills/basic-acceptance.sh`, the script shipped 0644, and the
#: command exited 126.
DOC_SCRIPT_CLASS = "doc-script-not-executable"

#: A backticked code span: the text inside one pair of single backticks.
_TICK_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
#: A command that STARTS with a shell script's path (optionally after a `$ ` prompt). One
#: that starts with `bash`, `sh` or anything else does not match: `bash x.sh` runs a
#: script whatever its mode.
_DIRECT_RUN = re.compile(r"^(?:\$\s+)?((?:\./)?[A-Za-z0-9_.][A-Za-z0-9_./-]*\.sh)(?=\s|$)")


def doc_run_scripts(rel, text):
    """(line, path-as-written) for every shell script a Markdown doc tells a reader to run
    directly: a backticked span, or a line inside a fenced block, that begins with the
    script's path. A bare mention in backticks counts too -- the reader cannot tell a
    mention from a command, and copying it into a shell is what they will do."""
    if not rel.lower().endswith(".md"):
        return []
    out, fence = [], None
    for i, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            mark = m.group(1)[0]
            if fence is None:
                fence = mark
            elif fence == mark:
                fence = None
            continue
        spans = [line.strip()] if fence else [t.strip() for t in _TICK_SPAN.findall(line)]
        for sp in spans:
            hit = _DIRECT_RUN.match(sp)
            if hit:
                out.append((i, hit.group(1)))
    return out


def _resolve_script(doc, raw, have):
    """The published path `raw` names, read from the repo root (how the docs give
    commands) or from the doc's own directory (how a link is written). None if neither is
    a published file: a script in the reader's own project is not this cut's to check."""
    for base in ("", posixpath.dirname(doc)):
        cand = posixpath.normpath(posixpath.join(base, raw))
        if cand in have:
            return cand
    return None


def doc_script_findings(texts, have, executable):
    """A finding for each published `.sh` a published doc tells a reader to run directly
    that is not executable. `texts` maps a published path to its text, `have` is every
    published path, `executable` the published paths carrying the bit."""
    found, seen = [], set()
    for rel in sorted(texts):
        for line, raw in doc_run_scripts(rel, texts[rel]):
            target = _resolve_script(rel, raw, have)
            if target is None or target in executable or (rel, line, target) in seen:
                continue
            seen.add((rel, line, target))
            found.append(scrub.Finding(
                rel, line, DOC_SCRIPT_CLASS,
                "the doc tells a reader to run `%s` directly, and it is not executable "
                "(it would exit 126)" % target, ""))
    return found


# ------------------------------------------------------------------- the generalization

def public_shield(manifest):
    """The exact spellings of the identity the cut is published under, longest first.

    the operator's 2026-09-27 ruling: the public version belongs to one named account, whose name
    is also the operator's -- which the table rewrites and the name class refuses. Only these literals are exempt from both -- a bare name elsewhere
    still fires -- so the cut can say whose repo it is and nothing more.
    """
    ident = manifest.get("identity_gate", {}).get("public_identity", {})
    return sorted({s for s in ident.get("shield", []) if s}, key=len, reverse=True)


def shield_pattern(lit):
    """A shield literal, bounded so it never covers the PREFIX of a longer name. Unbounded,
    the community repo's address shielded the head of the internal repo's
    (`<repo>-internal`), and the table could not rewrite what the shield held (WI-0450)."""
    return re.compile(r"(?<![\w-])" + re.escape(lit) + r"(?![\w-])")


def normalized(text):
    """Lowercase letters and digits only: `Foo-Bar`, `foo_bar` and `FooBar` all read `foobar`."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def protected_spans(text, manifest):
    """Every (start, end) a `protect` phrase covers, longest phrase first.

    A member id can also be an ordinary English word. The table cannot tell the two apart
    and neither can the roster check, so both consult this instead of guessing from case or
    surrounding words -- guesses that would silently rewrite a quoted ruling.
    """
    spans, hits = [], []
    # The public identity first, exact and case-sensitive: the cut is published AS it, so
    # the operator-name rows must not rewrite it and the name class must not refuse it.
    for lit in public_shield(manifest):
        for m in shield_pattern(lit).finditer(text):
            if not any(s <= m.start() and m.end() <= e for s, e in spans):
                spans.append((m.start(), m.end()))
    rows = sorted(manifest["generalize"].get("protect", []),
                  key=lambda r: len(r["phrase"]), reverse=True)
    for row in rows:
        phrase = row["phrase"]
        # `match_case`: a phrase whose capitalised form is the member's product name --
        # a lowercase phrase can be ordinary vocabulary while its capitalised form is a name.
        flags = 0 if row.get("match_case") else re.IGNORECASE
        pat = re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", flags)
        for m in pat.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in spans):
                continue  # already covered by a longer phrase
            spans.append((m.start(), m.end()))
            hits.append({"line": text.count("\n", 0, m.start()) + 1,
                         "phrase": phrase, "why": row.get("why", "")})
    return spans, hits


def generalize(text, manifest, code=False, prose=True, structured=None):
    """Apply the substitution table mechanically; report every hit with its line.

    A BACKSTOP under an editorial pass, never a replacement for it. The receipt lists every
    substitution so the pass that owns the prose (the brief's second item) can see exactly
    what it has to repair.
    """
    # Ordinary English that happens to contain a member id is masked to a sentinel no
    # pattern in the table can match, then restored. Without this, a member id that is
    # also an English word rewrites a quoted ADR ruling and breaks a file name in code.
    spans, protected = protected_spans(text, manifest)
    vault = []
    for start, end in sorted(spans, reverse=True):
        token = "\x00PROTECT%d\x00" % len(vault)
        vault.append(text[start:end])
        text = text[:start] + token + text[end:]

    def _unmask(s):
        for i, original in enumerate(vault):
            s = s.replace("\x00PROTECT%d\x00" % i, original)
        return s

    shield = public_shield(manifest)

    def _mask_public(s):
        # A row may WRITE the public identity (the private owner's URL becomes the public
        # repo's). Vault it at once, or a shorter operator-name row rewrites it next.
        def _vault(m):
            vault.append(m.group(0))
            return "\x00PROTECT%d\x00" % (len(vault) - 1)
        for lit in shield:
            s = shield_pattern(lit).sub(_vault, s)
        return s

    # Longest `find` first, so a compound's own row runs before the bare name inside it
    # can split it (`Member 1` before `Member 1`). Stable: ties keep file order.
    subs = sorted(manifest["generalize"].get("substitutions", []),
                  key=lambda r: -len(r["find"]))
    hits = []
    for row in subs:
        find, repl = row["find"], row["replace"]
        # In Python a bare host name is often an identifier (`devbox = {...}`), and
        # `<host>` is not one. A row may name an identifier-safe spelling for code.
        if code and row.get("code_replace"):
            repl = row["code_replace"]
        # `match_case` exists for a row whose capitalised spelling is a DIFFERENT, kept
        # term, where the capitalised form is a label and the lowercase one is not.
        flags = 0 if row.get("match_case") else re.IGNORECASE
        # Word-boundary where the term is word-shaped; literal where it is a path.
        if re.match(r"^[\w-]+$", find):
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(find) + r"(?![A-Za-z0-9_])", flags)
        else:
            pat = re.compile(re.escape(find), flags)  # literal rows honour match_case too
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                hits.append({"line": i, "find": find, "replace": repl,
                             "risk": row.get("risk", ""), "kind": row.get("kind", ""),
                             "excerpt": _unmask(line.strip())[:120]})
        text = pat.sub(repl.replace("\\", "\\\\"), text)
        text = _mask_public(text)

    text = _unmask(text)
    text, n_relabel = relabel(text, manifest)
    if structured is None:
        structured = code
    text, n_dealias = dealias_numbered(text) if structured else dealias(text)
    if prose and not structured:
        # Prose only: code parses both shapes, and its tests pin them; a path segment is
        # not prose either (rename_paths passes prose=False).
        text, k = _JOURNAL_ID.subn(r"\1-\3", text)  # a clock time of day is a schedule
        n_dealias += k
        text, k = _ISO_CLOCK.subn(r"\1", text)  # ...and so is an ISO timestamp's
        n_dealias += k
        text, k = _KEBAB_ALIAS.subn("example-member", text)  # a joinable letter id
        n_dealias += k
    if n_relabel or n_dealias:
        hits.append({"line": 0, "find": "(relabel/dealias)", "replace": "(neutral)",
                     "risk": "", "kind": "relabel",
                     "excerpt": "%d machine label(s) relabelled, %d letter alias(es) "
                                "neutralised" % (n_relabel, n_dealias)})
    if protected:
        hits.append({"line": 0, "find": "(protected)", "replace": "(unchanged)",
                     "risk": "", "excerpt": "%d ordinary-English phrase(s) preserved: %s"
                     % (len(protected), ", ".join(sorted({h["phrase"] for h in protected})))})
    return text, hits


def relabel(text, manifest):
    """Case-preserving SUBSTRING relabel of the machine-role labels (WI-0448).

    The internal labels read as product names, so the cut carries neutral role names
    instead. A substring pass, not a word
    pass, and over paths as well as text: the labels live inside identifiers and module
    names (`<label>_mail.py`, `test_<label>_channel`), and one consistent spelling across
    every file keeps each import pointing at its module. Rows run longest first. Returns
    (text, count).
    """
    rows = sorted(manifest["generalize"].get("relabel", []), key=lambda r: -len(r["find"]))
    n = 0
    for row in rows:
        find, repl = row["find"], row["replace"]
        variants = {find: repl, find.lower(): repl.lower(), find.upper(): repl.upper(),
                    find[:1].upper() + find[1:].lower(): repl[:1].upper() + repl[1:].lower()}
        for f, r in sorted(variants.items(), key=lambda kv: -len(kv[0])):
            c = text.count(f)
            if c:
                n += c
                text = text.replace(f, r)
    return text, n


#: A letter alias (`Member 2`, `Orchestrator 1`) is a pseudonym: it lets a reader JOIN the
#: facts about one member across files (consultant brief 2026-09-27, item 2). The cut says
#: "a member" instead, and no two sentences can be joined on a letter.
_DEALIAS = [
    (re.compile(r"\b(?:the |The )?Member [A-Z](?:'s|’s)(?![\w-])"), "a member's"),
    (re.compile(r"\b(?:the |The )?Member [A-Z](?![\w’'-])"), "a member"),
    (re.compile(r"\b(?:the |The )?Member [A-Z]-"), "a member-"),
    (re.compile(r"\b(?:the |The )?Orchestrator [A-Z](?![\w-])"), "an orchestrator agent"),
    (re.compile(r"\b(?:the |The )?Orchestrator [A-Z]-"), "an-orchestrator-"),
    (re.compile(r"\b(?:the |The )?Agent [A-Z](?![\w-])"), "an agent"),
]
_ARTICLE_FIX = [
    (re.compile(r"\b([Aa]n?|[Tt]he) (a member|an orchestrator agent|an agent)\b"),
     lambda m: m.group(2)),
    (re.compile(r"(^\s*(?:[-*]\s+)?|[.!?]\s+|\|\s*|#\s+)(a member|an orchestrator agent|an agent)",
                re.M),
     lambda m: m.group(1) + m.group(2)[:1].upper() + m.group(2)[1:]),
]


#: A durable session id, `20260717T2036Z-<host>-a3f9`: its clock time, read across many
#: citations, forms a pattern. Prose keeps the date and the suffix.
_JOURNAL_ID = re.compile(r"\b(\d{8})T\d{4}Z-([A-Za-z0-9]+)-([0-9a-f]{4})\b")

#: An ISO date-time in prose, `2031-01-02T03:04` or `2031-01-02 03:04:05+09:00`: two of
#: them beside each other say when the operator works (round-2 audit, WI-0449). Prose
#: keeps the date; the time and any offset go.
_ISO_CLOCK = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?(?![\d:])")

#: The kebab form of a letter alias (`member-3`, `member-3-arch`) joins facts the same way.
_KEBAB_ALIAS = re.compile(r"(?<![\w-])member-[a-z](?![a-z0-9_])")


_NUMBERED = [
    (re.compile(r"\bMember ([A-Z])(?![A-Za-z0-9_])"), "Member ", "M"),
    (re.compile(r"(?<![\w-])member([-_])([a-z])(?![a-z0-9_])"), None, "M"),
    (re.compile(r"\bOrchestrator ([A-Z])(?![A-Za-z0-9_])"), "Orchestrator ", "O"),
    (re.compile(r"\bAgent ([A-Z])(?![A-Za-z0-9_])"), "Agent ", "A"),
]


def _structured(rel):
    """Everything but prose (Markdown, text) is code or data, where values must stay distinct."""
    return not rel.lower().endswith((".md", ".txt"))


def dealias_numbered(text):
    """Letter aliases in CODE and data become per-file numbers. Returns (text, count).

    Collapsing every alias to "a member" is right for prose, and wrong for code: it turned
    a set of three distinct parked ids into one repeated string (cold audit round 3). So
    code keeps distinct values, numbered in order of first appearance WITHIN THE FILE, and
    one file's `Member 1` says nothing about another's.
    """
    maps, n = {"M": {}, "O": {}, "A": {}}, 0

    def num(family, letter):
        m = maps[family]
        return m.setdefault(letter.upper(), str(len(m) + 1))

    for pat, prefix, family in _NUMBERED:
        def repl(mo, prefix=prefix, family=family):
            if prefix is None:  # the kebab / snake form keeps its separator
                return "member" + mo.group(1) + num(family, mo.group(2))
            return prefix + num(family, mo.group(1))
        text, k = pat.subn(repl, text)
        n += k
    return text, n


def dealias(text):
    """Neutralise every letter alias. Returns (text, count)."""
    n = 0
    for pat, repl in _DEALIAS:
        text, k = pat.subn(repl, text)
        n += k
    for pat, repl in _ARTICLE_FIX:
        text = pat.sub(repl, text)
    return text, n


# --------------------------------------------------------------------- the identity gate

def _name_pattern(term):
    """`term` bounded by anything that is not a letter, digit or underscore -- so a hyphen
    ends a name (`x-arch`) while an identifier (`x_mail`) stays whole, exactly as the
    table sees it. An identifier that carries a name is `substring_names`' job."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
                      re.IGNORECASE)


def marker_pattern(raw):
    """A descriptive marker's regex, bounded like a name so it never fires inside a longer
    word that merely contains it. Each manifest row carries an `example` the marker must
    match, and a test over the real manifest holds every row to it."""
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + raw + r")(?![A-Za-z0-9_])", re.IGNORECASE)


def detection_terms(manifest, roster):
    """Every name the member-name class refuses: the live roster, every MEMBER row of the
    substitution table (a row exists because the name is a trace), and the manifest's
    `identity_gate.extra_names` -- names that are off the roster (retired, never enrolled,
    a persona, the operator's own) and still identify.

    Host and operator-path rows are not member names; their own classes check them. A
    path-shaped `find` is covered by the plain names inside it.
    """
    skip_kinds = {"host", "operator-path"}
    terms = {s.lower() for s in roster}
    for row in manifest.get("generalize", {}).get("substitutions", []):
        f = row.get("find", "")
        if row.get("kind") in skip_kinds or "/" in f or not f.strip():
            continue
        terms.add(f.lower())
    for n in manifest.get("identity_gate", {}).get("extra_names", []):
        terms.add(n.lower())
    exempt = {e.lower() for e in manifest.get("generalize", {}).get("roster_exempt", [])}
    # A roster id that is also an ordinary English word is refused only in its
    # member FORMS, each of which has a table row and so is a term above. Refusing the bare
    # word fired 228 times on ordinary English in round 2, and substituting it told a
    # reader what the placeholder stood for (WI-0383).
    exempt |= {e.lower() for e in manifest.get("identity_gate", {}).get("english_ids", [])}
    return sorted(terms - exempt, key=lambda s: (-len(s), s))


def identity_findings(text, rel, manifest, roster):
    """The three classes scrub does not carry. Same shape as a scrub Finding."""
    cfg = manifest.get("identity_gate", {})
    out = []

    # The same spans the table protected: an ordinary-English phrase is not a
    # surviving member name, and reporting it as one would train the reader to ignore the
    # class that matters.
    keep, _h = protected_spans(text, manifest)

    def _shielded(line_no, col_start, col_end):
        off = sum(len(s) + 1 for s in text.splitlines()[:line_no - 1])
        return any(s <= off + col_start and off + col_end <= e for s, e in keep)

    # A name is caught INSIDE a compound as well: `x-arch`, `-Volumes-x-`, `x_app.py`.
    # The first cut used the substitution table's boundary here, which counts `-` as part
    # of the word, and every hyphenated compound walked through both the table and the gate.
    # The excerpt is left EMPTY: findings are printed, and a finding that quotes the name
    # it caught republishes it.
    for sid in detection_terms(manifest, roster):
        pat = _name_pattern(sid)
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                if _shielded(i, m.start(), m.end()):
                    continue
                out.append(scrub.Finding(
                    rel, i, "member-name",
                    "a member or operator name survived the generalization table -- the "
                    "public cut carries no trace of any member project",
                    ""))
                break

    # A CamelCase compound (`XyzShapeTest`) has no letter boundary, so the bounded match
    # above cannot see it. Names distinctive enough to mean one thing anywhere are also
    # searched as bare substrings; an English word never is -- it would fire inside
    # every longer word that contains it.
    for sub in cfg.get("substring_names", []):
        pat = re.compile(re.escape(sub), re.IGNORECASE)
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                out.append(scrub.Finding(
                    rel, i, "member-name",
                    "a member or operator name survived inside a compound word", ""))

    # A member is traced by what the prose SAYS about it as surely as by its name: a
    # persona's integrations, a member's domain, the size of its data (WI-0447, the
    # STRICT ruling). Each marker is a regex, bounded like a name and case-blind, naming a
    # domain, an integration or a datum that fits one member rather than many. The excerpt
    # stays empty for the member-name reason: a finding that quotes the marker republishes it.
    for row in cfg.get("descriptive_markers", []):
        pat = marker_pattern(row["re"])
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                if _shielded(i, m.start(), m.end()):
                    continue
                out.append(scrub.Finding(
                    rel, i, "descriptive-marker",
                    "the prose describes a member by its domain, integration or data -- the "
                    "strict cut lets no reader describe a member, not only name one", ""))
                break

    # A publication identity must not name any account the manifest lists as private. A
    # host can refuse a push that exposes the publishing account's OWN email, but it cannot
    # know which other names to keep out, so this class is the only thing that can. Matched on letters and digits alone, so no
    # separator, case or CamelCase hides one; never shielded by `protect` and never
    # quoted, because a finding that prints the handle republishes it.
    for acct in cfg.get("private_accounts", []):
        want = normalized(acct)
        if not want:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if want in normalized(line):
                out.append(scrub.Finding(
                    rel, i, "private-account",
                    "a name of the private account survived -- the public identity carries "
                    "no link to it", ""))

    # The class exists to catch a path that identifies a PERSON, so the placeholder SEGMENT
    # is allowlisted rather than the file that uses it — and the operator's own names are
    # deliberately absent from that list, so the operator's own home path keeps firing.
    allow_h = set(cfg.get("home_path_allow", []))
    ph = {s.lower() for s in cfg.get("home_path_placeholder_segments", [])}
    for raw in cfg.get("home_path_patterns", []):
        pat = re.compile(raw)
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                if m.group(0) in allow_h:
                    continue
                if m.group(0).rsplit("/", 1)[-1].lower() in ph:
                    continue
                out.append(scrub.Finding(
                    rel, i, "home-path",
                    "an operator home path identifies a person and a machine",
                    line.strip()[:120]))

    # An entry starting `@` or `.` is a SUFFIX test, so a reserved TLD (`.invalid`,
    # `.example`, `.test`) covers every fixture repo's commit identity in one line without
    # naming each file; those cannot resolve and cannot belong to a person. Any other entry
    # is a whole address, matched EXACTLY: a suffix `...noreply.github.com` would let the
    # private account's own noreply address through, and that address names it.
    allow = list(cfg.get("email_allow", []))
    public_email = cfg.get("public_identity", {}).get("email")
    if public_email:
        allow.append(public_email)
    allow_sfx = tuple(a for a in allow if a[:1] in "@.")
    allow_exact = {a for a in allow if a[:1] not in "@."}
    raw = cfg.get("email_pattern")
    if raw:
        pat = re.compile(raw)
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                got = m.group(0)
                if got in allow_exact or (allow_sfx and got.endswith(allow_sfx)):
                    continue
                out.append(scrub.Finding(
                    rel, i, "email",
                    "an email address identifies a person; scrub.py has no email class",
                    line.strip()[:120]))
    return out


def disclosure_classes(manifest):
    """The prose-disclosure classes: [(cls, why, [compiled marker, ...])]."""
    out = []
    for row in manifest.get("identity_gate", {}).get("disclosure_classes", []):
        pats = [marker_pattern(m["re"]) for m in row.get("markers", [])]
        out.append((row["cls"], row.get("why", ""), pats))
    return out


def _exemption_covers(row, line, start, end):
    """True when this value-level exemption row shields the hit at [start, end) on `line`.

    Both forms are SPAN-scoped: the phrase, or a `line_re` match, must CONTAIN the hit. A
    `line_re` that only had to match somewhere on the line waived every disclosure of the
    class on it -- `by design$` shielded a marker at the other end (round-2 review, item 5).
    """
    phrase = row.get("phrase")
    if phrase:
        at = line.find(phrase)
        while at != -1:
            if at <= start and end <= at + len(phrase):
                return True
            at = line.find(phrase, at + 1)
        return False
    raw = row.get("line_re")
    if not raw:
        return False
    return any(m.start() <= start and end <= m.end() for m in re.finditer(raw, line))


def _exemption_lines(row, lines):
    """How many lines of the file this row could apply to."""
    phrase, raw = row.get("phrase"), row.get("line_re")
    if phrase:
        return sum(1 for ln in lines if phrase in ln)
    return sum(1 for ln in lines if re.search(raw, ln))


def disclosure_findings(text, rel, manifest):
    """Prose that describes the operator's network, whereabouts or habits. (kept, waived).

    A known shape of a sentence, not a name: a named network segment, a whereabouts,
    `prefers`. The vocabulary lives in the manifest (`identity_gate.disclosure_classes`),
    each marker held to its own example by a test.

    Exemptions (`identity_gate.disclosure_exempt`) are VALUE-LEVEL: a row names a file and
    one exact `phrase` (or a `line_re`) and shields only a hit inside it. A row must apply
    to EXACTLY ONE line of its file and shield at least one hit there; otherwise it is a
    finding itself -- an exemption that cannot match reads exactly like one that works,
    and one that matches many lines has quietly become a file exemption.

    Findings carry file and line and an EMPTY excerpt: a finding that quotes the sentence
    republishes it.
    """
    classes = disclosure_classes(manifest)
    rows = [r for r in manifest.get("identity_gate", {}).get("disclosure_exempt", [])
            if r.get("path") == rel]
    kept, waived = [], []
    lines = text.splitlines()
    used = set()
    for cls, why, pats in classes:
        for i, line in enumerate(lines, 1):
            for pat in pats:
                hit = None
                for m in pat.finditer(line):
                    shield = [k for k, r in enumerate(rows)
                              if r.get("cls", cls) == cls
                              and _exemption_covers(r, line, m.start(), m.end())]
                    if shield:
                        used.update(shield)
                        waived.append((scrub.Finding(rel, i, cls, why, ""),
                                       rows[shield[0]].get("why", "")))
                        continue
                    hit = m
                    break
                if hit is not None:
                    kept.append(scrub.Finding(rel, i, cls, why, ""))
                    break  # one finding per class per line is enough to point at it
    for k, r in enumerate(rows):
        if not (r.get("why") or "").strip() or bool(r.get("phrase")) == bool(r.get("line_re")):
            kept.append(scrub.Finding(
                rel, 0, "disclosure-exempt-malformed",
                "an exemption row needs a `why` and exactly one of `phrase` or `line_re`", ""))
            continue
        n = _exemption_lines(r, lines)
        if n != 1 or k not in used:
            kept.append(scrub.Finding(
                rel, 0, "disclosure-exempt-stale",
                "an exemption row applies to %d line(s) and shielded %s hit; a value-level "
                "exemption shields exactly one occurrence" % (n, "a" if k in used else "no"),
                ""))
    return kept, waived


def code_findings(text, rel):
    """A substitution inside code can leave code that does not run. Python must compile
    and JSON must parse; anything else is not checked and is not claimed to be."""
    if "<<" in text and ">>" in text and rel.startswith("bootstrap-kit/"):
        return []  # a kit TEMPLATE: `<<TOKEN>>` is filled in at install, not valid yet
    try:
        if rel.endswith(".py"):
            compile(text, rel, "exec")
        elif rel.endswith(".json"):
            import json as _json
            _json.loads(text)
    except SyntaxError as e:
        return [scrub.Finding(rel, e.lineno or 0, "code-broken",
                              "the published Python no longer compiles", "")]
    except ValueError as e:
        return [scrub.Finding(rel, getattr(e, "lineno", 0), "code-broken",
                              "the published JSON no longer parses", "")]
    return []


def _line_classes(line, rel, manifest, roster):
    """Every class the gates find on one line, taken alone."""
    return ({f.cls for f in scrub.findings(line, rel)}
            | {f.cls for f in identity_findings(line, rel, manifest, roster)}
            | {f.cls for f in disclosure_findings(line, rel, manifest)[0]})


def _gate_exempt_rows(rel, manifest):
    """This file's `gate_exempt` rows, each with its span patterns compiled (None: malformed)."""
    out = []
    for row in manifest.get("gate_exempt", []):
        if row.get("path") != rel:
            continue
        spans = row.get("spans")
        ok = ((row.get("why") or "").strip() and row.get("classes") and isinstance(spans, list)
              and spans and all(isinstance(s, str) and s for s in spans))
        pats = None
        if ok:
            try:
                pats = [re.compile(s) for s in spans]
            except re.error:
                pats = None
        out.append((row, pats))
    return out


def gate_exempt_findings(hits, text, rel, manifest, roster):
    """Split `hits` by the file's `gate_exempt` rows. Returns (kept, waived, row_findings).

    A row is SPAN-SCOPED, never a file-wide class waiver (round-2 review, item 5): it names
    the classes and a list of `spans`, each a regex for one exact fixture value. A hit of an
    exempt class is waived only when masking every span match on its line leaves that class
    with nothing to find there -- so the span contains the hit, and a second value of the
    same class on the same line, or anywhere else in the file, still fires. A row without
    spans waives nothing (`gate-exempt-malformed`); a span that shields no hit is
    `gate-exempt-stale`, because an exemption that cannot match reads like one that works.
    """
    rows = _gate_exempt_rows(rel, manifest)
    if not rows:
        return hits, [], []
    lines = text.splitlines()
    used, memo = set(), {}
    kept, waived = [], []
    for f in hits:
        why = None
        for k, (row, pats) in enumerate(rows):
            if pats is None or f.cls not in row["classes"] or not 0 < f.line <= len(lines):
                continue
            key = (k, f.line, f.cls)
            if key not in memo:
                line = lines[f.line - 1]
                hit = [j for j, p in enumerate(pats) if p.search(line)]
                masked = line
                for j in hit:
                    masked = pats[j].sub(lambda m: "\x00" * len(m.group(0)), masked)
                memo[key] = hit if hit and f.cls not in _line_classes(
                    masked, rel, manifest, roster) else []
            if memo[key]:
                used.update((k, j) for j in memo[key])
                why = row["why"]
                break
        if why is None:
            kept.append(f)
        else:
            waived.append((f, why))
    extra = []
    for k, (row, pats) in enumerate(rows):
        if pats is None:
            extra.append(scrub.Finding(
                rel, 0, "gate-exempt-malformed",
                "a gate_exempt row needs `classes`, a `why` and a non-empty list of `spans` "
                "(valid regexes); a row without spans would waive a class file-wide", ""))
            continue
        for j in range(len(pats)):
            if (k, j) not in used:
                extra.append(scrub.Finding(
                    rel, 0, "gate-exempt-stale",
                    "a gate_exempt span shields no hit; an exemption that cannot match reads "
                    "exactly like one that works", ""))
    return kept, waived, extra


def gate_text(text, rel, manifest, roster):
    """Both gates over one file's published content. Scrub first: it is the floor.

    Returns (findings, waived). A waived finding is NOT discarded — it is returned
    separately so the receipt can print it. An exemption that does not show its work is how
    a gate rots into decoration: the reader has to see WHAT was waived, not merely that
    something was.
    """
    d_kept, d_waived = disclosure_findings(text, rel, manifest)
    hits = (list(scrub.findings(text, rel)) + identity_findings(text, rel, manifest, roster)
            + d_kept + code_findings(text, rel))
    kept, waived, extra = gate_exempt_findings(hits, text, rel, manifest, roster)
    return kept + extra, d_waived + waived


# ------------------------------------------------------------------- the name gate

def _strict(manifest):
    """The manifest with every exemption removed: no protect phrase, no public shield, no
    disclosure or gate exemption. A NAME is gated through this and nothing else."""
    man = dict(manifest)
    man["generalize"] = dict(manifest.get("generalize", {}), protect=[])
    ig = dict(manifest.get("identity_gate", {}), disclosure_exempt=[])
    ig["public_identity"] = dict(ig.get("public_identity", {}), shield=[])
    man["identity_gate"] = ig
    man["gate_exempt"] = []
    return man


def name_findings(rel, manifest, roster):
    """Every gate class over a PATH and each of its components, with NO exemption.

    Returns [(cls, bad)] where `bad` is the set of component indexes that tripped (all of
    them when only the whole path did). A leak in a file or directory name ships with a
    push exactly as a leak in the body does, and the body gate never read the name: a
    commit adding `<private-account>-notes.md` with clean content passed (round-2 review,
    item 3). The whole path is gated as well as each part, so a name split across a
    separator is still seen.
    """
    strict = _strict(manifest)
    parts = rel.split("/")
    text = "\n".join([rel] + parts) + "\n"
    found = (list(scrub.findings(text, rel)) + identity_findings(text, rel, strict, roster)
             + disclosure_findings(text, rel, strict)[0])
    by = {}
    for f in found:
        bad = by.setdefault(f.cls, set())
        if f.line >= 2:
            bad.add(f.line - 2)
    return [(cls, bad or set(range(len(parts)))) for cls, bad in sorted(by.items())]


def masked_path(rel, bad):
    """`rel` with each tripped component withheld: a finding that quotes a name republishes it."""
    return "/".join("<withheld>" if i in bad else p for i, p in enumerate(rel.split("/")))


# ---------------------------------------------------------------------------- the sample

def sample_file(root, row, man, roster, cut=None):
    """One curated-sample entry through both gates, plus the sample's own rule.

    Returns (text, hits, findings, waived); `text` is None when there is nothing to publish.

    The sample is CHOSEN, NOT EDITED (WI-0383). It passes the gates like any other file, and
    it may be touched only by substitution rows whose `kind` is in
    `sample_rules.allowed_kinds` -- the machine and operator-path pass every file gets. A
    member substitution means the candidate carried a member trace: the table would hide
    it, and a sample that needed hiding was the wrong pick.

    `from` is read from the trunk. In an exported tree the file already sits at `to`, so
    that is read instead -- which lets the same check run in the public cut's own suite.
    """
    src, to = row.get("from", ""), row.get("to", "")
    rel = to or src
    if not src or not to or not (row.get("why") or "").strip():
        return None, [], [scrub.Finding(
            rel, 0, "sample-malformed",
            "a sample entry needs `from`, `to` and a one-line `why`", "")], []
    if cut is not None and to in cut.published:
        return None, [], [scrub.Finding(
            to, 0, "sample-collides",
            "the sample's `to` is already published from an include path", "")], []
    path = root / src if (root / src).is_file() else root / to
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, [], [scrub.Finding(
            rel, 0, "sample-missing",
            f"the sample source `{src}` could not be read ({e.__class__.__name__})", "")], []

    text, hits = generalize(raw, man, code=to.endswith(".py"), structured=_structured(to))
    allowed = set(man.get("sample_rules", {}).get("allowed_kinds", []))
    edited = [h for h in hits if h["find"] != "(protected)" and h.get("kind") not in allowed]
    kept, waived = gate_text(text, to, man, roster)
    kept = list(kept) + [scrub.Finding(
        to, h["line"], "sample-edited",
        f"the sample needed the substitution `{h['find']}` -> `{h['replace']}` to pass; a "
        f"sample is chosen, not edited -- pick a different one", h["excerpt"])
        for h in edited]
    return text, hits, kept, waived


# ------------------------------------------------------------------------------- the cut

class Cut(object):
    """What one run decided, so the receipt and the exit code read the same source."""

    def __init__(self):
        self.files = []        # (rel, text)
        self.published = []
        self.dropped = []
        self.pending = []
        self.binaries = []
        self.reduced = []
        self.owed = []
        self.subs = {}         # rel -> [hit]
        self.findings = []
        self.waived = []       # (Finding, why) — suppressed, and printed anyway
        self.boundary = []     # (path, why, files_present) — the exclude list, measured
        self.executable = set()  # published paths whose source carries an exec bit
        self.renamed = []        # (old, new) published paths renamed off a member name
        self.replaced = []       # (path, from, why) shipped from an authored overlay
        self.added = []          # (path, why) shipped ONLY in the public cut, from an overlay
        self.unlinked = 0        # links into a withheld path, reduced to their text
        self.receipt_text = ""
        self.private_receipt = ""

    @property
    def clean(self):
        return not self.findings


def build(root=None, manifest=None):
    """Compute the whole cut in memory. Writes nothing; `--out` writes what this returns."""
    root = pathlib.Path(root) if root else ROOT
    man = manifest if isinstance(manifest, dict) else load_manifest(manifest)
    roster = member_roster(root, man["generalize"].get("roster_exempt", []))

    cut = Cut()
    cut.published, cut.dropped, cut.pending, cut.binaries = plan(root, man)
    slots = {r.get("ships_to", r["path"]) for r in man["exclude"] if r.get("ships")}
    overlays, bad = overlay_plan(root, man, set(cut.published), slots)
    cut.findings.extend(bad)
    cut.replaced = sorted((p, o["from"], o["why"]) for p, o in overlays.items() if o)

    for rel in cut.published:
        try:
            if rel in overlays:
                if overlays[rel] is None:
                    continue  # the overlay failed (a finding says so); the source never ships
                text = overlays[rel]["text"]
            else:
                text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            cut.findings.append(scrub.Finding(
                rel, 0, "unreadable",
                f"could not be read ({e.__class__.__name__}), so it has NOT been checked",
                ""))
            continue
        text, hits = generalize(text, man, code=rel.endswith(".py"), structured=_structured(rel))
        if hits:
            cut.subs[rel] = hits
        kept, waived = gate_text(text, rel, man, roster)
        cut.findings.extend(kept)
        cut.waived.extend(waived)
        cut.files.append((rel, text))
        # `poga` is started as `./poga`; a cut that drops the bit ships a launcher that
        # cannot launch.
        if os.access(root / rel, os.X_OK):
            cut.executable.add(rel)

    ctx = {"public_identity": man.get("identity_gate", {}).get("public_identity"),
           "terms": detection_terms(man, roster)
                    + [s.lower() for s in man.get("identity_gate", {}).get("substring_names", [])],
           "published": list(cut.published) + [r.get("to", "") for r in man.get("sample", [])]
           + [r.get("ships_to", r["path"]) for r in man["exclude"] if r.get("ships")]
           + [r.get("path", "") for r in man.get("add", [])]}
    cut.reduced, cut.owed = reduced_files(root, man, ctx, overlays)
    for rel, ships, text, n in cut.reduced:
        text, hits = generalize(text, man, code=rel.endswith(".py"), structured=_structured(rel))
        if hits:
            cut.subs[rel] = hits
        kept, waived = gate_text(text, rel, man, roster)
        cut.findings.extend(kept)
        cut.waived.extend(waived)
        cut.files.append((rel, text))

    for row in man.get("sample", []):
        text, hits, kept, waived = sample_file(root, row, man, roster, cut)
        cut.findings.extend(kept)
        cut.waived.extend(waived)
        if text is None:
            continue
        if hits:
            cut.subs[row["to"]] = hits
        cut.files.append((row["to"], text))
        # A sampled script keeps its bit too: the drill driver is run as
        # `drills/basic-acceptance.sh`, and drills/ ships only through sample rows.
        src = root / row["from"] if (root / row["from"]).is_file() else root / row["to"]
        if os.access(src, os.X_OK):
            cut.executable.add(row["to"])

    # `add`: files that exist only in the public cuts. Gated like every other file.
    adds, bad = add_plan(root, man, {rel for rel, _t in cut.files} | set(cut.published))
    cut.findings.extend(bad)
    for rel in sorted(adds):
        text, hits = generalize(adds[rel]["text"], man, code=rel.endswith(".py"),
                                structured=_structured(rel))
        if hits:
            cut.subs[rel] = hits
        kept, waived = gate_text(text, rel, man, roster)
        cut.findings.extend(kept)
        cut.waived.extend(waived)
        cut.files.append((rel, text))
        cut.added.append((rel, adds[rel]["why"]))
        if adds[rel]["executable"]:
            cut.executable.add(rel)

    # A value-level exemption naming a file the cut never gated cannot have shielded
    # anything; left silent it reads exactly like one that works.
    gated = {rel for rel, _t in cut.files}
    for row in man.get("identity_gate", {}).get("disclosure_exempt", []):
        if row.get("path") not in gated:
            cut.findings.append(scrub.Finding(
                row.get("path") or "(exemption row)", 0, "disclosure-exempt-stale",
                "an exemption row names a file the cut does not publish", ""))
    for row in man.get("gate_exempt", []):
        if row.get("path") not in gated:
            cut.findings.append(scrub.Finding(
                row.get("path") or "(gate_exempt row)", 0, "gate-exempt-stale",
                "a gate_exempt row names a file the cut does not publish", ""))

    shipped =[r.get("from", "") for r in man.get("sample", [])]
    shipped += [r.get("ships_from", r["path"]) for r in man["exclude"] if r.get("ships")]
    shipped += [src for _p, src, _w in cut.replaced]
    shipped += [adds[p]["from"] for p in adds]
    cut.boundary = measure_boundary(root, man, shipped)
    reduced_paths = {r[0] for r in cut.reduced} | {r.get("ships_from", r["path"])
                                                  for r in man["exclude"] if r.get("ships")}
    cut.dropped = [(r, w) for r, w in cut.dropped if r not in reduced_paths]

    rename_paths(cut, man)
    # A kit template is also read at the slot it fills; its links are written for there.
    alt = {r["ships_from"]: r.get("ships_to", r["path"]) for r in man["exclude"]
           if r.get("ships") and r.get("ships_from")}
    unlink_withheld(cut, alt)
    have = {rel for rel, _t in cut.files} | {RECEIPT_NAME}
    for rel, text in cut.files:
        cut.findings.extend(dangling_links(rel, text, have, alt.get(rel)))
    # A script the docs say to run must be executable in the cut (`--out` writes the bit
    # from `cut.executable`, so this reads the same set the written tree gets).
    cut.findings.extend(doc_script_findings(dict(cut.files), have, cut.executable))

    # Every OUTPUT path, after the renames, through every class with no exemption -- the
    # same name gate --verify-publish runs on the committed tree, so a leak in a file or
    # directory name is refused at export time too. The finding prints the path with the
    # tripped part withheld; the full path goes only to the private receipt.
    for rel, _text in cut.files:
        for cls, bad in name_findings(rel, man, roster):
            cut.findings.append(scrub.Finding(
                masked_path(rel, bad), 0, cls,
                "the PATH carries a name or a marker the table did not rename", rel))

    finish_receipt(cut, man, root, roster)
    return cut, man


def _slug(s):
    return re.sub(r"\s+", "-", s.strip()).lower()


def rename_paths(cut, man):
    """Rename a published path whose segments carry a member name, and point links at it.

    The path goes through the MEMBER rows of the table (never the host rows: a module
    named for a host is imported by that name) and is slugged, so `adr/0007-<member>-x.md`
    becomes `adr/0007-member-e-x.md`. The file's own content already went through the
    whole table, so a link to it now reads `0007-Member 2-x.md`; that exact string is
    replaced with the new basename everywhere, which keeps every link pointing at a file.
    """
    member_only = {"generalize": dict(man["generalize"])}
    member_only["generalize"]["substitutions"] = [
        r for r in man["generalize"].get("substitutions", [])
        if r.get("kind") not in ("host", "operator-path")]
    member_only["generalize"]["protect"] = []
    # `rename`: an explicit public name for a path whose slug describes the operator or a
    # member in words the table cannot see (a slug naming a protocol or a product), or whose
    # table-made slug would re-attach a letter alias to a description (`member-2-owns-...`).
    # It wins over the table. A row naming a path the cut does not publish is a finding.
    explicit = {r["from"]: r["to"] for r in man.get("rename", [])}
    published = {rel for rel, _t in cut.files}
    for src in sorted(set(explicit) - published):
        cut.findings.append(scrub.Finding(
            src, 0, "rename-stale", "a rename row names a path the cut does not publish", ""))
    renames = {}
    for rel, _text in cut.files:
        if rel in explicit:
            renames[rel] = explicit[rel]
            continue
        parts = rel.split("/")
        new = []
        for seg in parts:
            g, _h = generalize(seg, member_only, prose=False)
            new.append(_slug(g) if g != seg else seg)
        new_rel = "/".join(new)
        if new_rel != rel:
            renames[rel] = new_rel
    if not renames:
        return
    swaps = []
    for old, new in renames.items():
        old_base, new_base = old.rsplit("/", 1)[-1], new.rsplit("/", 1)[-1]
        g_old, _h = generalize(old_base, man, prose=False)
        swaps.append((g_old, new_base))
        if old in explicit and old_base != g_old:
            swaps.append((old_base, new_base))
    files = []
    for rel, text in cut.files:
        for g_old, new_base in swaps:
            if g_old != new_base:
                text = text.replace(g_old, new_base)
        new_rel = renames.get(rel, rel)
        if rel in cut.executable:
            cut.executable.discard(rel)
            cut.executable.add(new_rel)
        files.append((new_rel, text))
    cut.files = files
    cut.renamed = sorted(renames.items())


#: An inline Markdown link or image: `[text](target)` / `![alt](target "title")`.
_MD_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\s*\)")
#: A reference-style definition: `[label]: target`.
_MD_REFDEF = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(<[^>\n]*>|\S+)")
_CODE_SPAN = re.compile(r"`+[^`\n]*`+")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _link_target(rel, raw):
    """The cut path a relative link in `rel` points at, or None when it is not a relative
    path (a URL, a mail link, an anchor, an absolute path). A target that climbs out of the
    tree resolves to a path starting `../`."""
    t = raw.strip("<>").strip()
    if not t or t.startswith(("#", "/")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", t):
        return None
    t = urllib.parse.unquote(t.split("#", 1)[0].split("?", 1)[0])
    if not t:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(rel), t))


def _markdown_lines(text):
    """(index, line, code-span ranges) for each line outside a fenced code block."""
    fence = None
    for i, line in enumerate(text.split("\n")):
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)[0]
            elif m.group(1)[0] == fence:
                fence = None
            continue
        if fence is None:
            yield i, line, [s.span() for s in _CODE_SPAN.finditer(line)]


def _link_state(rel, raw, have, dirs, alt=None):
    """None when a link is fine or not a relative path; "withheld" when its target is inside
    the tree and not in the cut; "outside" when it climbs out of the tree.

    `alt` is a second place the same text ships from: a kit template (`ships_from`) is
    written to be read at its `ships_to`, so a link that resolves from there is not broken.
    """
    state = None
    for base in (rel, alt) if alt else (rel,):
        t = _link_target(base, raw)
        if t is None or t in have or t in dirs:
            return None
        state = "outside" if t == ".." or t.startswith("../") else (state or "withheld")
    return state


def _cut_dirs(have):
    return {"."} | {"/".join(p.split("/")[:i]) for p in have for i in range(1, p.count("/") + 1)}


def unlink_withheld(cut, alt=None):
    """In every shipped Markdown file, a link whose target is not in the cut becomes its
    text followed by ` (withheld)`, unlinked, so nothing dangles.

    Mechanical and general: whatever the manifest withholds (records withheld from this
    copy, the work-item and session stores, a binary), a link into it survives as words and nothing else. A target that climbs out of
    the tree (`../x` from the root) is a broken link, not a withheld file, and is left for
    `dangling_links` to refuse. Returns the count unlinked; code blocks and code spans are
    never touched.
    """
    alt = alt or {}
    have = {rel for rel, _t in cut.files} | {RECEIPT_NAME}
    dirs = _cut_dirs(have)
    n, files = 0, []
    for rel, text in cut.files:
        if not rel.lower().endswith(".md"):
            files.append((rel, text))
            continue
        lines = text.split("\n")
        for i, line, code in _markdown_lines(text):
            def repl(m, code=code):
                nonlocal n
                if any(s <= m.start() < e for s, e in code):
                    return m.group(0)
                if _link_state(rel, m.group(3), have, dirs, alt.get(rel)) != "withheld":
                    return m.group(0)
                n += 1
                return (m.group(2) or "link") + " (withheld)"
            lines[i] = _MD_LINK.sub(repl, line)
        files.append((rel, "\n".join(lines)))
    cut.files = files
    cut.unlinked = n
    return n


def dangling_links(rel, text, have, alt=None):
    """A relative Markdown link or reference definition in `rel` whose target is not in the
    cut, as findings. After `unlink_withheld` only the forms it leaves can remain."""
    if not rel.lower().endswith(".md"):
        return []
    dirs = _cut_dirs(have)
    out = []
    for i, line, code in _markdown_lines(text):
        raws = [(m.start(), m.group(3)) for m in _MD_LINK.finditer(line)]
        d = _MD_REFDEF.match(line)
        if d:
            raws.append((d.start(1), d.group(1)))
        for at, raw in raws:
            if any(s <= at < e for s, e in code):
                continue
            if _link_state(rel, raw, have, dirs, alt) is not None:
                out.append(scrub.Finding(
                    rel, i + 1, "dangling-link",
                    "a relative link points at a path the cut does not ship", ""))
                break
    return out


def finish_receipt(cut, man, root, roster):
    """The public receipt, itself generalized and gated like any published file.

    The first cut's receipt quoted every original the table replaced -- a decoder ring for
    the whole cut, written after the gates had run and never through them. The public
    receipt now carries counts; the detail goes to `cut.private_receipt`, which `--out`
    writes BESIDE the cut directory, never inside it.
    """
    for _ in range(2):  # twice: a finding in the receipt changes the receipt's own count
        text, _h = generalize(receipt(cut, man, root), man)
        kept, _w = gate_text(text, RECEIPT_NAME, man, roster)
        new = [f for f in kept if (f.path, f.line, f.cls) not in
               {(g.path, g.line, g.cls) for g in cut.findings}]
        cut.findings.extend(new)
        if not new:
            break
    cut.receipt_text = text
    cut.private_receipt = private_receipt(cut, man)


# ----------------------------------------------------------------------------- the receipt

def receipt(cut, man, root):
    """What was included, what was dropped and why, and what the pass changed."""
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    a = L.append
    a("# Public cut receipt")
    a("")
    a(f"Generated {when} by `curate/public_cut.py` from `curate/public_cut_manifest.json` "
      f"(manifest version {man.get('manifest_version', '?')}).")
    a("")
    a("This cut is meant to be a GENERIC POGA SYSTEM. The manifest enforces directory "
      "boundaries: a withheld tree is never copied. The prose inside an included file is "
      "GATED, not proven -- the gates refuse every known shape of a name or a describing "
      "sentence, and a file that could not pass was withheld or replaced by an authored "
      "generic version. What was withheld is listed below so a reader can see the shape of "
      "the boundary, not just its result.")
    a("")
    a(f"- published: {len(cut.files)} file(s)")
    a(f"- dropped: {len(cut.dropped)} file(s)")
    a(f"- reduced: {len(cut.reduced)} file(s)")
    a(f"- replaced by an authored overlay: {len(cut.replaced)} file(s)")
    a(f"- added, public cut only: {len(cut.added)} file(s)")
    a(f"- pending (listed, not yet authored): {len(cut.pending)}")
    a(f"- binaries skipped: {len(cut.binaries)}")
    a(f"- findings: {len(cut.findings)}")
    a(f"- waived by a named exemption: {len(cut.waived)}")
    a(f"- links to a withheld path, unlinked: {cut.unlinked}")
    a("")

    a("## Published")
    a("")
    for rel, _t in sorted(cut.files):
        a(f"- `{rel}`")
    a("")

    a("## Reduced rather than dropped")
    a("")
    if cut.reduced:
        # No row counts: how many rows a member list held is the size of the fleet.
        for rel, ships, _t, _n in cut.reduced:
            a(f"- `{rel}` — **{ships}**")
    else:
        a("None.")
    a("")

    a("## Replaced by an authored overlay")
    a("")
    if cut.replaced:
        a("Each of these ships the authored generic text from `public-overlay/`, not the "
          "source file at the same path. The overlay passed the same gates as any file.")
        a("")
        # The PUBLISHED name only: the source path is the name a rename exists to hide.
        renamed = dict(cut.renamed)
        for rel in sorted(renamed.get(r, r) for r, _src, _why in cut.replaced):
            a(f"- `{rel}`")
    else:
        a("None.")
    a("")

    a("## Added in the public cut only")
    a("")
    if cut.added:
        a("Each of these exists only in the public cut: it is authored under "
          "`public-overlay/` and the internal tree never carries it at this path. It "
          "passed the same gates as any file.")
        a("")
        for rel, why in sorted(cut.added):
            a(f"- `{rel}` — {why}")
    else:
        a("None.")
    a("")

    a("## Listed in the manifest and not yet authored")
    a("")
    if cut.pending or cut.owed:
        a("These are stated intentions, not accidents. The receipt prints them so an "
          "absence cannot be mistaken for a decision nobody made.")
        a("")
        for rel, why in cut.pending:
            a(f"- `{rel}` — {why}")
        for rel, ships, why in cut.owed:
            a(f"- `{rel}` — **{ships}**: {why}")
    else:
        a("None.")
    a("")

    a("## Withheld by the boundary")
    a("")
    a("Every entry in the manifest's `exclude` list. Measured from the manifest against the tree and NOT "
      "from the copy walk — the walk only visits paths under an `include` entry, so a "
      "wholly separate tree would otherwise be reported as nothing withheld at all.")
    a("")
    total = 0
    terms = detection_terms(man, member_roster(root, man["generalize"].get("roster_exempt", [])))
    terms += [s.lower() for s in man.get("identity_gate", {}).get("substring_names", [])]
    # A single operational file's NAME can describe what it holds (a port table, a
    # member's spec), so only trees and reduced slots are named; the rest are counted.
    loose_n, loose_files = 0, 0
    for rel, why, n, ships in cut.boundary:
        total += n
        if _names_member(rel, terms):
            rel, why = MASKED_PATH, WITHHELD_WHY
        if not ships and "." in rel.rsplit("/", 1)[-1]:
            loose_n += 1
            loose_files += n
            continue
        # No file counts: how many journals, items or briefs the record holds is the
        # operator's working volume (WI-0448, cold audit round 2).
        state = f"**{ships}**, " if ships else ""
        a(f"- `{rel}` — {state}withheld — {why}")
    if loose_n:
        a(f"- {loose_n} further file(s) at named paths — their names are withheld with them")
    a("")

    a("## Dropped from inside an included tree")
    a("")
    if cut.dropped:
        # Counted by reason, not listed: build artefacts name the interpreter version and
        # a machine-local file's name says what it holds.
        by_why = {}
        ruled = False
        for _rel, why in cut.dropped:
            if why.startswith(WITHHELD_WHY):
                # A ruled-out record prints the bare reason and no count: the manifest's
                # ruling note and how many there are both begin to say what they are.
                ruled = True
                continue
            by_why[why] = by_why.get(why, 0) + 1
        for why in sorted(by_why):
            a(f"- {by_why[why]} file(s) — {why}")
        if ruled:
            a(f"- some records — {WITHHELD_WHY}")
    else:
        a("None — no file under an `include` path matched an exclude rule.")
    a("")

    if cut.binaries:
        a("## Binaries skipped, not checked")
        a("")
        a("`curate/scrub.py` decodes with `errors=\"replace\"`, so a binary file pattern-"
          "scans as mojibake instead of being skipped — which invents findings out of byte "
          "runs. These were neither scanned nor published; a file that was never checked "
          "must not read as one that passed.")
        a("")
        for rel, why in cut.binaries:
            a(f"- `{rel}` — {why}")
        a("")

    a("## The curated sample")
    a("")
    if man.get("sample"):
        for row in man["sample"]:
            a(f"- `{row.get('from')}` → `{row.get('to')}` — {row.get('why', '')}")
    else:
        a("**UNSELECTED.** The manifest's `sample` list is empty. Selection is the third "
          "item in the brief's sequence, not this one. This section states that rather "
          "than printing nothing, because an empty section reads like a clean result.")
    a("")

    a("## Substitutions")
    a("")
    # No count: how many names the cut replaced, and in how many files, describes the
    # source (WI-0449; the header and this section once printed two different totals).
    a("The cut took mechanical substitutions. Neither the ORIGINALS nor a count is printed "
      "here: a receipt that quoted them would undo every placeholder in the cut, and a count "
      "describes the source. The line-by-line detail is written to a private receipt beside "
      "the cut directory, for the person who runs the cut, and is never published.")
    a("")

    if cut.renamed:
        a("## Renamed")
        a("")
        # A count only: the list says which published names were not their originals.
        a(f"{len(cut.renamed)} published path(s) were renamed; links to them were "
          "rewritten to match.")
        a("")

    review = []  # the rulings name the terms they rule on; they stay in the private receipt
    if review:
        a("## Terms ruled on by the editorial pass")
        a("")
        for row in review:
            verdict = row.get("ruling")
            a(f"- `{row['term']}`" + (f" — **{verdict}** — " if verdict else " — ")
              + row["why"])
        a("")

    a("## Waived by a named exemption")
    a("")
    if cut.waived:
        a("These matched a gate and were allowed through, because containing that shape is "
          "the file's purpose — the detector's own source, and the tests that prove it "
          "fires. Each is printed with file and line: an exemption that does not show its "
          "work is how a gate rots into decoration.")
        a("")
        for f, why in cut.waived:
            a(f"- `{f.path}`:{f.line} **[{f.cls}]** — {why}")
    else:
        a("None.")
    a("")

    a("## Findings")
    a("")
    if cut.findings:
        a(f"**{len(cut.findings)} finding(s). This cut is NOT publishable as it stands.**")
        a("")
        by = {}
        for f in cut.findings:
            by.setdefault(f.cls, []).append(f)
        for cls in sorted(by):
            a(f"### `{cls}` — {len(by[cls])}")
            a("")
            for f in by[cls][:200]:
                a(f"- `{f.path}`:{f.line} — {f.why}")
            if len(by[cls]) > 200:
                a(f"- …and {len(by[cls]) - 200} more of this class")
            a("")
    else:
        a("None. Both gates passed: `curate/scrub.py`'s nine known-shape classes and this "
          "tool's own classes (identity: `member-name`, `descriptive-marker`, `home-path`, "
          "`email`, `private-account`; disclosure: "
          + ", ".join("`%s`" % c for c, _w, _p in disclosure_classes(man)) + "), and "
          "`%s`: every shell script a published doc tells a reader to run directly is "
          "executable." % DOC_SCRIPT_CLASS)
        a("")
        a("A clean run is not the same as proven harmless — it means no KNOWN shape "
          "matched. The cold auditor session is what turns that into a claim.")
    a("")
    return "\n".join(L)


def private_receipt(cut, man):
    """The detail the public receipt withholds. Written OUTSIDE the cut; never published."""
    L = ["# PRIVATE public-cut receipt -- NOT FOR PUBLICATION", "",
         "Every substitution with its original, every protected phrase, every rename and "
         "every finding with its excerpt. This file names what the cut hides.", ""]
    L += ["## Substitutions", ""]
    for rel in sorted(cut.subs):
        L.append(f"### `{rel}`")
        for h in cut.subs[rel]:
            risk = f" [{h['risk']}]" if h.get("risk") else ""
            L.append(f"- line {h['line']}: `{h['find']}` -> `{h['replace']}`{risk} -- {h['excerpt']}")
        L.append("")
    L += ["## Protected phrases", ""]
    L += [f"- `{r['phrase']}` -- {r.get('why', '')}" for r in man["generalize"].get("protect", [])]
    L += ["", "## Renamed", ""] + [f"- `{o}` -> `{n}`" for o, n in cut.renamed]
    L += ["", "## Replaced by an overlay", ""] + [f"- `{p}` <- `{s}` -- {w}"
                                                   for p, s, w in cut.replaced]
    L += ["", "## Findings", ""] + [f"- `{f.path}`:{f.line} [{f.cls}] {f.why} -- {f.excerpt}"
                                    for f in cut.findings]
    return "\n".join(L) + "\n"


def private_receipt_path(out_dir):
    out = pathlib.Path(out_dir)
    return out.parent / (out.name + ".PRIVATE-RECEIPT.md")


REJECTED_SUFFIX = ".REJECTED"
REJECTED_NOTE = "REJECTED-DO-NOT-PUBLISH.txt"


def rejected_path(out_dir):
    out = pathlib.Path(out_dir)
    return out.parent / (out.name + REJECTED_SUFFIX)


def _write_tree(cut, dest):
    dest.mkdir(parents=True)
    for rel, text in cut.files:
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        if rel in cut.executable:
            f.chmod(0o755)
    (dest / RECEIPT_NAME).write_text(cut.receipt_text, encoding="utf-8")


def _rm(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


def _rejected_note(cut):
    by = {}
    for f in cut.findings:
        by.setdefault(f.cls, []).append(f)
    L = ["REJECTED -- DO NOT PUBLISH.", "",
         "public_cut.py refused this cut: %d finding(s) in %d file(s)."
         % (len(cut.findings), len({f.path for f in cut.findings})),
         "It was written here, beside the publishable path and never at it, only so the",
         "findings can be read against the files. Nothing in this directory may be pushed.",
         ""]
    for cls in sorted(by):
        L.append("[%s] %d" % (cls, len(by[cls])))
        L += ["  %s:%d" % (f.path, f.line) for f in by[cls]]
    return "\n".join(L) + "\n"


def write_out(cut, man, root, out_dir):
    """Write the cut FAIL-CLOSED. Returns (where_it_went, refused).

    Clean: written to a staging directory beside `<out>`, then swapped in, so `<out>` is
    only ever a whole cut; a stale `<out>.REJECTED` is removed. Refused: `<out>` is left
    exactly as it was and the cut goes to `<out>.REJECTED` (replacing an earlier one) with
    REJECTED-DO-NOT-PUBLISH.txt at its top. The private receipt is written BESIDE whichever
    directory the cut went to, never inside it.
    """
    out = pathlib.Path(out_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = out.parent / (".%s.staging-%d" % (out.name, os.getpid()))
    _rm(stage)
    rej = rejected_path(out)
    try:
        _write_tree(cut, stage)
        if not cut.clean:
            (stage / REJECTED_NOTE).write_text(_rejected_note(cut), encoding="utf-8")
            _rm(rej)
            os.replace(stage, rej)
            private_receipt_path(rej).write_text(cut.private_receipt, encoding="utf-8")
            return rej, True
        old = out.parent / (".%s.old-%d" % (out.name, os.getpid()))
        _rm(old)
        if out.exists() or out.is_symlink():
            os.replace(out, old)
        os.replace(stage, out)
        _rm(old)
    finally:
        _rm(stage)
    private_receipt_path(out).write_text(cut.private_receipt, encoding="utf-8")
    _rm(rej)
    _rm(private_receipt_path(rej))
    return out, False


# ------------------------------------------------------------------- the pre-push check

def _git(clone, *args, raw=False, stdin=None):
    import subprocess
    r = subprocess.run(["git", "-C", str(clone)] + list(args), capture_output=True,
                       text=not raw, input=stdin)
    return r.returncode, r.stdout


def _cat_blobs(clone, shas):
    """Every blob's bytes, read from the object store in one `cat-file --batch` pass."""
    if not shas:
        return {}
    code, out = _git(clone, "cat-file", "--batch", raw=True,
                     stdin=("\n".join(shas) + "\n").encode())
    if code:
        return None
    got, pos = {}, 0
    for sha in shas:
        nl = out.index(b"\n", pos)
        head = out[pos:nl].split()
        if len(head) != 3 or head[1] != b"blob":
            return None
        size = int(head[2])
        got[sha] = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1  # the object, then its trailing newline
    return got


def verify_publish_report(clone, man, root=None):
    """Everything that must hold before the scratch clone is pushed.

    Returns {"commit", "tree", "problems"}; `commit`/`tree` are None when they could not
    be resolved. WI-0020. GitHub refuses a push that exposes the public account's OWN
    email; it cannot know the private account's names or address, and a working machine's
    git identity may not be the public one. So this runs from an internal lane -- where the withheld
    lists are present -- against the clone, and a problem never quotes what it found.

    It gates the COMMITTED BYTES, never the working tree: the tree of the one commit is
    listed with `ls-tree` and each blob read from the object store. Reading the working
    tree let a committed leak pass under an uncommitted cleanup (consultant brief
    2026-09-27, item 5). The working tree must also be exactly that commit -- no edit, no
    untracked file -- because a publish clone that is not its commit is not the thing that
    was checked.
    """
    root = pathlib.Path(root) if root else ROOT
    clone = pathlib.Path(clone)
    cfg = man.get("identity_gate", {})
    want = cfg.get("public_identity", {}).get("email")
    rep = {"commit": None, "tree": None, "problems": []}
    problems = rep["problems"]
    if not want:
        problems.append("the manifest names no public_identity.email")
        return rep
    if not cfg.get("private_accounts"):
        problems.append("the manifest's private_accounts list is empty -- this is the public "
                        "copy, which cannot check for the private account; run from an "
                        "internal lane")
        return rep
    code, _out = _git(clone, "rev-parse", "--git-dir")
    if code:
        problems.append("not a git checkout: %s" % clone)
        return rep
    code, head = _git(clone, "rev-parse", "--verify", "HEAD^{commit}")
    code_t, tree = _git(clone, "rev-parse", "--verify", "HEAD^{tree}")
    if code or code_t:
        problems.append("HEAD does not resolve to a commit; there is nothing to verify")
        return rep
    commit, tree = head.strip(), tree.strip()
    rep["commit"], rep["tree"] = commit, tree

    _c, local = _git(clone, "config", "--local", "--get", "user.email")
    if local.strip() != want:
        problems.append("the repo-local user.email is not the public noreply address")
    _c, n = _git(clone, "rev-list", "--all", "--count")
    if n.strip() != "1":
        problems.append("history holds %s commit(s); the public repo is one fresh commit"
                        % (n.strip() or "no"))
    _c, reach = _git(clone, "rev-list", "--all")
    if [r for r in reach.split() if r != commit]:
        problems.append("a ref points at a commit other than HEAD; only HEAD's commit is "
                        "checked, so nothing else may be reachable")

    # The working tree must BE the commit. Ignored files are left alone: a push cannot
    # carry them.
    _c, status = _git(clone, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = [e for e in status.split("\0") if e]
    dirty = sum(1 for e in entries if not e.startswith("??"))
    untracked = sum(1 for e in entries if e.startswith("??"))
    if dirty:
        problems.append("the working tree differs from the commit in %d path(s); a publish "
                        "clone must be exactly its commit" % dirty)
    if untracked:
        problems.append("the working tree holds %d untracked file(s); a publish clone must "
                        "be exactly its commit" % untracked)

    _c, emails = _git(clone, "log", "--all", "--format=%ae%n%ce")
    other = {e for e in emails.split() if e != want}
    if other or not emails.split():
        problems.append("%d author/committer address(es) are not the public noreply address"
                        % len(other))
    # The name is checked like the address, exactly: it IS the operator's name, which the
    # name class refuses anywhere else, so it cannot go through the gate. It is the owner
    # half of `repo` -- a bare copy in the manifest would itself be rewritten in the cut.
    want_name = (cfg.get("public_identity", {}).get("repo") or "").split("/")[0]
    _c, names = _git(clone, "log", "--all", "--format=%an%n%cn")
    if not want_name or {n for n in names.splitlines() if n} != {want_name}:
        problems.append("an author/committer name is not the public account's name")
    # A commit date carries the committing machine's UTC offset, and an offset is a time
    # zone (round-2 audit, WI-0449). Every date must be written in UTC.
    _c, dates = _git(clone, "log", "--all", "--format=%ai%n%ci")
    zoned = [d for d in dates.splitlines() if d and not d.endswith(" +0000")]
    if zoned:
        problems.append("%d author/committer date(s) carry a non-UTC offset; commit with "
                        "GIT_AUTHOR_DATE and GIT_COMMITTER_DATE in +0000" % len(zoned))
    roster = member_roster(root, man["generalize"].get("roster_exempt", []))
    _c, meta = _git(clone, "log", "--all", "--format=%B")
    for f in gate_text(meta, "(commit message)", man, roster)[0]:
        problems.append("%s:%d [%s] %s" % (f.path, f.line, f.cls, f.why))

    # Ref and tag NAMES ship with a push, and an annotated tag carries a message and a
    # tagger of its own.
    _c, refs = _git(clone, "for-each-ref", "--format=%(refname)%09%(objecttype)%09%(objectname)")
    names_text, tags = [], []
    for line in refs.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        names_text.append(parts[0])
        if parts[1] == "tag":
            tags.append(parts[2])
    for f in gate_text("\n".join(names_text) + "\n", "(ref names)", man, roster)[0]:
        problems.append("%s:%d [%s] %s" % (f.path, f.line, f.cls, f.why))
    for sha in tags:
        _c, body = _git(clone, "cat-file", "tag", sha)
        for f in gate_text(body, "(annotated tag)", man, roster)[0]:
            problems.append("%s:%d [%s] %s" % (f.path, f.line, f.cls, f.why))

    code, listing = _git(clone, "ls-tree", "-r", "-z", "--full-tree", commit)
    if code:
        problems.append("the commit's tree could not be listed; nothing was checked")
        return rep
    blobs, modes = [], {}
    for entry in [e for e in listing.split("\0") if e]:
        meta_part, rel = entry.split("\t", 1)
        mode, kind, sha = meta_part.split()
        modes[rel] = mode
        # The PATH ships with the push as surely as the bytes do. Every committed path and
        # each directory above it goes through every class with no exemption; the problem
        # names the path with the tripped part withheld, and the blob so it can be found.
        for cls, bad in name_findings(rel, man, roster):
            problems.append("%s (blob %s): [%s] the committed PATH carries a name or a "
                            "marker" % (masked_path(rel, bad), sha[:12], cls))
        if kind != "blob":
            problems.append("%s: a %s entry (mode %s) cannot be read as text, so it has NOT "
                            "been checked" % (rel, kind, mode))
            continue
        blobs.append((rel, sha))
    data = _cat_blobs(clone, [sha for _r, sha in blobs])
    if data is None:
        problems.append("the commit's blobs could not be read from the object store; "
                        "nothing was checked")
        return rep
    texts = {}
    for rel, sha in blobs:
        if "PRIVATE-RECEIPT" in rel:
            problems.append("%s: the private receipt is a decoder and never ships" % rel)
            continue
        if REJECTED_NOTE in rel:
            problems.append("%s: this clone was made from a REJECTED cut" % rel)
            continue
        if _is_binary(rel, man):
            # The cut never publishes a binary, so one here did not come from the cut --
            # and a file that was never checked must not read as one that passed.
            problems.append("%s: a binary blob cannot be gated, and the cut never publishes "
                            "one" % rel)
            continue
        try:
            text = data[sha].decode("utf-8")
        except UnicodeDecodeError:
            problems.append("%s: not valid UTF-8, so it has NOT been checked" % rel)
            continue
        texts[rel] = text
        for f in gate_text(text, rel, man, roster)[0]:
            problems.append("%s:%d [%s] %s" % (f.path, f.line, f.cls, f.why))
    # The MODE ships with the push too. A script the docs say to run must be committed
    # 100755; the bytes alone cannot show it, so this reads the tree's own mode.
    for f in doc_script_findings(texts, set(modes),
                                 {r for r, m in modes.items() if m == "100755"}):
        problems.append("%s:%d [%s] %s" % (f.path, f.line, f.cls, f.why))
    return rep


def verify_publish(clone, man, root=None):
    """The problems `verify_publish_report` found, alone. Empty means clean."""
    return verify_publish_report(clone, man, root)["problems"]


# --------------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="public_cut.py",
        description="Build the public cut of POGA from the internal trunk.")
    ap.add_argument("--out", help="Directory to write the cut into. Replaced only by a CLEAN cut; a refused "
                         "cut goes to <out>.REJECTED and <out> is left untouched.")
    ap.add_argument("--check", action="store_true",
                    help="Do everything except write; exit non-zero on any finding.")
    ap.add_argument("--root", help="Repo to cut FROM (default: this checkout).")
    ap.add_argument("--manifest", help="Boundary to read (default: curate/public_cut_manifest.json).")
    ap.add_argument("--edition", default=DEFAULT_EDITION,
                    help="Which publication identity the cut is for: `showcase` (default) or a row of "
                         "the manifest's `editions` (WI-0450: `community`).")
    ap.add_argument("--verify-publish", metavar="CLONE",
                    help="The pre-push check (WI-0020): the scratch clone about to be pushed "
                         "passes the gate, and its one commit carries only the public address.")
    args = ap.parse_args(argv)

    if not args.out and not args.check and not args.verify_publish:
        ap.error("one of --out, --check or --verify-publish is required")

    root = pathlib.Path(args.root) if args.root else ROOT
    try:
        man = apply_edition(load_manifest(args.manifest), args.edition)
    except ManifestError as e:
        print(f"public_cut: {e}", file=sys.stderr)
        return 2

    if args.verify_publish:
        rep = verify_publish_report(args.verify_publish, man, root)
        problems = rep["problems"]
        stamp = "commit %s tree %s" % (rep["commit"] or "(unresolved)",
                                       rep["tree"] or "(unresolved)")
        if problems:
            print(f"public_cut: verify-publish checked {stamp}", file=sys.stderr)
            print(f"public_cut: DO NOT PUSH — {len(problems)} problem(s):", file=sys.stderr)
            for p in problems[:40]:
                print(f"  {p}", file=sys.stderr)
            if len(problems) > 40:
                print(f"  …and {len(problems) - 40} more", file=sys.stderr)
            return 1
        print(f"public_cut: verify-publish checked {stamp}")
        print("public_cut: verify-publish clean — one commit, the public name and address "
              "only, the working tree exactly the commit, the gate clean over every "
              "committed blob and path name, the commit message and the ref names.")
        return 0

    cut, man = build(root, man)

    dest = refused = None
    if args.out and not args.check:
        dest, refused = write_out(cut, man, root, args.out)
        if refused:
            print(f"public_cut: REFUSED cut written to {dest} -- NOT to {args.out}, which "
                  f"was left untouched. Never publish it; see {dest / REJECTED_NOTE}",
                  file=sys.stderr)
        else:
            print(f"public_cut: wrote {len(cut.files)} file(s) to {dest} (edition {args.edition})")
            print(f"public_cut: receipt at {dest / RECEIPT_NAME}")
        print(f"public_cut: PRIVATE detail at {private_receipt_path(dest)} -- outside the cut; "
              f"never publish it")
    else:
        print(f"public_cut: --check over {len(cut.files)} file(s) from {root} (edition {args.edition})")

    if cut.pending:
        print(f"public_cut: {len(cut.pending)} include(s) listed and not yet authored "
              f"— reported in the receipt, not a failure: "
              f"{', '.join(r for r, _ in cut.pending)}")

    if cut.findings:
        by = {}
        for f in cut.findings:
            by.setdefault(f.cls, []).append(f)
        print(f"public_cut: REFUSED — {len(cut.findings)} finding(s) in "
              f"{len({f.path for f in cut.findings})} file(s).", file=sys.stderr)
        for cls in sorted(by):
            print(f"  [{cls}] {len(by[cls])}", file=sys.stderr)
            for f in by[cls][:20]:
                print(f"    {f.path}:{f.line}  {f.why}", file=sys.stderr)
            if len(by[cls]) > 20:
                print(f"    …and {len(by[cls]) - 20} more", file=sys.stderr)
        return 1

    print("public_cut: clean — no known shape matched. That is not the same as proven "
          "harmless; the cold auditor session is what turns it into a claim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
