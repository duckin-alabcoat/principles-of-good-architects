"""Brief parsing and transformation — a real module, with explicit arguments.

Refactor-cleanup package 4. Before this, the strict appliable-brief engine (ADR-0039)
lived as globals spread across four exec'd parts — the schema regexes, frontmatter,
op-section split and `Plan` in config.py, `extract_fields_and_blocks` in coord.py,
`apply_changelog_prepend` in journal.py, `SurfaceBrief` and `evaluate_brief` in lanes.py —
and it could only be reached by running the whole harness assembler. This module holds
the PURE half: nothing here reads `ROOT`, `CFG` or any other harness global, and
importing it runs nothing and touches no live state (`tests/test_brief_module.py` pins
that). The one input that depends on where the harness is running — which paths a brief
may write — is an explicit argument: `evaluate_brief(brief_path, resolve_dest=...)`. The
security guard behind it (`_brief_dest` / `_brief_code_reason` in lanes.py) did not move
and is not weakened; it is passed in.

It also holds the ONE flat-frontmatter reader. Five copies existed (session's brief
parser, curate/adopt-runner.py, curate/check-apply.py, curate/reconcile.py,
curate/deliver.py). They were not all the same dialect, so they were not collapsed into
one function — they share one block finder (`frontmatter_block`) and one field reader
(`frontmatter_fields`), and each keeps its own return shape:

  brief  (session, adopt-runner, check-apply): no leading-whitespace tolerance, keys
         lower-cased, `#`-commented lines skipped.
  status (reconcile, STATUS.md):               keys kept AS WRITTEN, no comment skipping
         (a STATUS.md key is read case-sensitively by its callers, e.g. `id`).
  deliver (delivered-stamp scan):              leading whitespace tolerated, the RAW
         block text returned, and the scan is a regex over that block.

COMPATIBILITY FACADE (temporary). The session namespace still binds the old names
(`parse_frontmatter`, `evaluate_brief`, `SurfaceBrief`, ...) to the objects here — see the
"Brief parsing" block in sessionlib/config.py — so every caller and test that reaches them
as `session.<name>` is unchanged. REMOVAL STEP: once no caller in sessionlib/, curate/ or
tests/ reaches these names through the session namespace (grep each name in the facade's
list), delete that block and import this module where it is used. Until then a name must
not be re-defined in a part: tests/test_pid_liveness.py's duplicate-name guard catches a
part that binds one twice, and tests/test_brief_module.py pins that the facade names ARE
these objects.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Flat `key: value` frontmatter — the one block finder and field reader.
# --------------------------------------------------------------------------- #


def frontmatter_block(text, *, lstrip=False):
    """(block, body) for a leading `---` frontmatter; block is None when there is none.

    The block is everything after the opening `---` up to the newline before the first
    later line starting `---`; body is the text after that closing line. With
    `lstrip=True` leading whitespace before the opening `---` is tolerated."""
    t = (text or "").lstrip() if lstrip else text
    if not t.startswith("---"):
        return None, text
    end = t.find("\n---", 3)
    if end == -1:
        return None, text
    nl = t.find("\n", end + 1)
    return t[3:end], (t[nl + 1:] if nl != -1 else "")


def frontmatter_fields(block, *, lower_keys=True, skip_comments=True):
    """{key: value} from a frontmatter block's `key: value` lines (split at the first
    colon, both sides stripped). Lines without a colon are ignored."""
    out = {}
    for line in block.splitlines():
        if ":" in line and not (skip_comments and line.strip().startswith("#")):
            k, _, v = line.partition(":")
            k = k.strip()
            out[k.lower() if lower_keys else k] = v.strip()
    return out


def parse_frontmatter(text):
    """(header_dict, body_text) — the BRIEF dialect. header is {} if there is no leading
    `---` frontmatter, and body is then the whole text."""
    block, body = frontmatter_block(text)
    if block is None:
        return {}, text
    return frontmatter_fields(block), body


def brief_header(text):
    """(header_dict, has_frontmatter) — the brief dialect in the shape adopt-runner and
    check-apply use, where an empty-but-present block differs from no block."""
    block, _ = frontmatter_block(text)
    if block is None:
        return {}, False
    return frontmatter_fields(block), True


def status_frontmatter(text):
    """STATUS.md frontmatter as a dict, or {} — keys as written, no comment skipping."""
    block, _ = frontmatter_block(text)
    if block is None:
        return {}
    return frontmatter_fields(block, lower_keys=False, skip_comments=False)


def raw_frontmatter(text):
    """The raw frontmatter block text, tolerating leading whitespace; "" when none."""
    block, _ = frontmatter_block(text, lstrip=True)
    return block if block is not None else ""


# --------------------------------------------------------------------------- #
# The strict appliable-brief schema (ADR-0039). The prose spec is the comment block
# above the facade in sessionlib/config.py.
# --------------------------------------------------------------------------- #

REQUIRED_BRIEF_HEADER = ("edit-id", "target-file", "expected-base-version",
                         "proposed-new-version")
OP_HEADING = re.compile(r"^##\s+op:\s*([a-z][a-z-]*)\s*$", re.IGNORECASE)
FENCE_OPEN = re.compile(r"^(~{3,})[ \t]*([A-Za-z][A-Za-z-]*)[ \t]*$")
FENCE_CLOSE = re.compile(r"^(~{3,})[ \t]*$")
BRIEF_FIELD = re.compile(r"^([A-Za-z][A-Za-z-]*):[ \t]?(.*)$")
# Tolerant of the same version-line variants `role_doc_version()` accepts, so the
# apply engine can verify/bump a differently-styled role doc (e.g. a non-Claude
# member's — `**Version**: `v0.1.7``): colon inside OR outside the bold, an
# optional backtick wrapper, an optional `v`. Three capture groups so `version-bump`
# rewrites style-preservingly: (1) prefix through the opening backtick/`v`, (2) the
# semver, (3) closing backtick + trailing whitespace. Anchored full-line so `findall`
# still counts occurrences for the exactly-one uniqueness rule.
VERSION_LINE = re.compile(r"(?m)^(\*\*Version:?\*\*:?[ \t]*`?v?)(\d+\.\d+\.\d+)(`?[ \t]*)$")


class SurfaceBrief(Exception):
    """Raised to route a brief to the surface path with a human-readable reason. Not an
    error — a classification. A malformed or ambiguous brief is surfaced, never
    half-applied."""


class Plan:
    """The result of evaluating one brief: apply (with a file plan) or surface."""

    def __init__(self, brief_path, edit_id):
        self.brief_path = brief_path
        self.edit_id = edit_id
        self.action = None          # "apply" | "surface"
        self.reason = None
        self.touched = {}           # abs Path -> new text
        self.proposed_version = None


def split_op_sections(body):
    """(op_type, section_lines) pairs on `## op:` headings; preamble ignored. Surfaces
    if there are no operations."""
    sections, current = [], None
    for line in body.split("\n"):
        m = OP_HEADING.match(line)
        if m:
            current = (m.group(1).lower(), [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)
    if not sections:
        raise SurfaceBrief("no `## op:` operations found")
    return sections


def extract_fields_and_blocks(section_lines):
    """(fields, blocks) for one op section. blocks maps a tilde-fence label to its
    verbatim content; fields maps a `Key: value` key to its value."""
    fields, blocks = {}, {}
    i, n = 0, len(section_lines)
    while i < n:
        m = FENCE_OPEN.match(section_lines[i])
        if m:
            fence, label = m.group(1), m.group(2).lower()
            buf, j, closed = [], i + 1, False
            while j < n:
                cm = FENCE_CLOSE.match(section_lines[j])
                if cm and len(cm.group(1)) >= len(fence):
                    closed = True
                    break
                buf.append(section_lines[j])
                j += 1
            if not closed:
                raise SurfaceBrief(f"unterminated ~~~{label} block")
            blocks[label] = "\n".join(buf)
            i = j + 1
            continue
        fm = BRIEF_FIELD.match(section_lines[i])
        if fm:
            fields[fm.group(1).strip().lower()] = fm.group(2).rstrip()
        i += 1
    return fields, blocks


def norm_version(v):
    return v.strip().lstrip("v").strip() if v else v


def current_version(text):
    """The single `**Version:** X.Y.Z` value in a role doc, or None if not exactly one."""
    matches = VERSION_LINE.findall(text)
    return matches[0][1] if len(matches) == 1 else None


def apply_replace(text, before, after):
    count = text.count(before)
    if count == 0:
        raise SurfaceBrief("replace: `before` block not found in target (0 matches)")
    if count > 1:
        raise SurfaceBrief(f"replace: `before` block is ambiguous ({count} matches) — never guess")
    return text.replace(before, after, 1)


def apply_version_bump(text, expected, proposed):
    matches = VERSION_LINE.findall(text)
    if len(matches) != 1:
        raise SurfaceBrief(f"version-bump: expected exactly one `**Version:**` line, found {len(matches)}")
    prefix, found, suffix = matches[0]
    if norm_version(found) != norm_version(expected):
        raise SurfaceBrief(f"version-bump: target version {found} != expected {norm_version(expected)}")
    return VERSION_LINE.sub(lambda _m: f"{prefix}{norm_version(proposed)}{suffix}", text, count=1)


def apply_changelog_prepend(text, anchor, content):
    lines = text.split("\n")
    hits = [i for i, ln in enumerate(lines) if ln == anchor]
    if len(hits) == 0:
        raise SurfaceBrief(f"changelog-prepend: anchor line not found: {anchor!r}")
    if len(hits) > 1:
        raise SurfaceBrief(f"changelog-prepend: anchor line is ambiguous ({len(hits)} matches): {anchor!r}")
    i = hits[0]
    return "\n".join(lines[: i + 1] + content.split("\n") + lines[i + 1:])


def stamp_applied(text, stamp, proposed_version):
    """`text` with the applied-metadata lines inserted at the end of its frontmatter
    (unchanged when it has none) — the pure half of filing a brief to `applied/`."""
    stamp_lines = (f"applied: {stamp}\napplied-at-version: "
                   f"{norm_version(proposed_version)}\nstate: applied\n")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[: end + 1] + stamp_lines + text[end + 1:]
    return text


def evaluate_brief(brief_path, *, resolve_dest):
    """Classify + plan one brief. Returns a Plan; never writes. Applies only if the
    brief is `apply: auto`, its base version matches, and every op verifies against an
    in-memory copy (whole-brief atomicity).

    `resolve_dest(rel, op)` turns every brief-named path (`target-file`,
    `changelog-prepend`'s `File:`, `create-file`'s `Path:`) into an absolute Path, and
    raises SurfaceBrief for a path the brief may not write. The harness passes
    `_brief_dest` — containment to the repo plus the decides-what-code-runs refusal."""
    text = brief_path.read_text(encoding="utf-8")
    header, body = parse_frontmatter(text)
    plan = Plan(brief_path, header.get("edit-id", brief_path.stem))
    try:
        apply_mode = header.get("apply", "").strip().lower()
        if apply_mode != "auto":
            raise SurfaceBrief(
                "Apply: manual (or no `apply: auto` header)" if apply_mode in ("", "manual")
                else f"unrecognised apply mode: {apply_mode!r}")
        missing = [k for k in REQUIRED_BRIEF_HEADER if not header.get(k)]
        if missing:
            raise SurfaceBrief(f"header missing required field(s): {', '.join(missing)}")
        # THROUGH `resolve_dest` — the third site of this class, swept after fixing
        # `create-file` rather than waiting to be shown it separately. `target-file`
        # cannot invent a file: it needs one that exists and carries a matching
        # `**Version:**` line. That is not narrow enough to be safe, because EVERY OTHER
        # MEMBER'S ROLE DOC is exactly such a file in a sibling directory — so an
        # unguarded join let a brief in this Architect's gitignored mailbox edit another
        # system's role doc unattended, in a repo it has no authority over.
        target = resolve_dest(header["target-file"], "target-file")
        if not target.is_file():
            raise SurfaceBrief(f"target-file does not exist: {header['target-file']}")
        target_text = target.read_text(encoding="utf-8")
        cur = current_version(target_text)
        expected = header["expected-base-version"]
        proposed = header["proposed-new-version"]
        plan.proposed_version = proposed
        if cur is None:
            raise SurfaceBrief("target-file has no single `**Version:**` line — cannot verify base")
        if norm_version(cur) != norm_version(expected):
            raise SurfaceBrief(f"version drift: target is v{cur}, brief expects v{norm_version(expected)}")
        # `originals` is what each touched file held BEFORE this brief. It exists because
        # `changelog-prepend` can now edit a file that already exists (ADR-0123), so
        # "in `copies` but not the target" stopped meaning "created by this brief" — the
        # distinction the no-op check and the `+N new file(s)` reason both ride on.
        copies = {target: target_text}
        originals = {target: target_text}
        op_count = 0
        for op_type, section_lines in split_op_sections(body):
            fields, blocks = extract_fields_and_blocks(section_lines)
            op_count += 1
            if op_type == "replace":
                if "before" not in blocks or "after" not in blocks:
                    raise SurfaceBrief("replace: needs both ~~~before and ~~~after blocks")
                copies[target] = apply_replace(copies[target], blocks["before"], blocks["after"])
            elif op_type == "version-bump":
                copies[target] = apply_version_bump(copies[target], expected, proposed)
            elif op_type == "changelog-prepend":
                if "after" not in fields:
                    raise SurfaceBrief("changelog-prepend: needs an `After:` anchor line")
                if "content" not in blocks:
                    raise SurfaceBrief("changelog-prepend: needs a ~~~content block")
                # OPTIONAL `File:` — the changelog need not live in the target file
                # (ADR-0123). Without it the op is exactly what it was: prepend into the
                # brief's one `target-file`, which is what every member's brief does and
                # what every existing brief on disk means.
                #
                # WHY THE OP AND NOT THE HEADER. `target-file` is the file whose
                # `**Version:**` line gates the whole brief — the base-version check, the
                # bump, and the did-it-move verify all key on it. A changelog sibling
                # carries no version line, so it can never be a second `target-file`; it
                # is one op's destination, and that is the grain the field belongs at.
                # Without this, a split member's every role-doc brief regresses to
                # `Apply: manual`, against ADR-0049.
                dest = target
                if fields.get("file"):
                    dest = resolve_dest(fields["file"], "changelog-prepend")
                    if not dest.is_file():
                        raise SurfaceBrief(
                            f"changelog-prepend: File: does not exist: {fields['file']}")
                    if dest not in copies:
                        copies[dest] = originals[dest] = dest.read_text(encoding="utf-8")
                copies[dest] = apply_changelog_prepend(copies[dest], fields["after"], blocks["content"])
            elif op_type == "create-file":
                if "path" not in fields:
                    raise SurfaceBrief("create-file: needs a `Path:` line")
                if "content" not in blocks:
                    raise SurfaceBrief("create-file: needs a ~~~content block")
                # THROUGH `resolve_dest`, not a bare join. This op chooses its own
                # destination, which makes it the one that most needs the containment
                # check — and it was the sibling that did not call it. Measured
                # 2026-09-11 (OPS-0007 sitting): `../escape.md`, `docs/../../escape2.md`
                # and an ABSOLUTE `/tmp/...` path each planned `apply`, the last escaping
                # without containing `..` at all because joining an absolute path
                # discards ROOT entirely.
                newp = resolve_dest(fields["path"], "create-file")
                if newp.exists() or newp in copies:
                    raise SurfaceBrief(f"create-file: path already exists: {fields['path']}")
                content = blocks["content"]
                copies[newp] = content if content.endswith("\n") else content + "\n"
            else:
                raise SurfaceBrief(f"unknown operation: `## op: {op_type}`")
        after_ver = current_version(copies[target])
        if norm_version(after_ver or "") != norm_version(proposed):
            raise SurfaceBrief(
                f"version not moved to proposed v{norm_version(proposed)} "
                f"(target ends at v{after_ver}) — a version-bump op is required")
        created = [p for p in copies if p not in originals]
        edited = [p for p in copies if p in originals and copies[p] != originals[p]]
        if not created and not edited:
            raise SurfaceBrief("brief is a no-op (nothing would change)")
        plan.action = "apply"
        plan.touched = copies
        plan.reason = (
            f"{op_count} op(s); {header['target-file']} v{norm_version(expected)} -> "
            f"v{norm_version(proposed)}"
            + (f"; +{len(created)} new file(s)" if created else "")
            + (f"; also edits {', '.join(sorted(q.name for q in edited if q != target))}"
               if len(edited) > 1 else ""))
    except SurfaceBrief as s:
        plan.action = "surface"
        plan.reason = str(s)
    return plan
