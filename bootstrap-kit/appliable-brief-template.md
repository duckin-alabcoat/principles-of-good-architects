---
edit-id: YYYY-MM-DD-short-slug
target-file: <role-doc>.md
expected-base-version: vX.Y.Z
proposed-new-version: vX.Y.Z
apply: auto
---

# <Human title of the edit>

<!--
  APPLIABLE-BRIEF TEMPLATE (ADR-0039). A brief authored to THIS schema is applied
  deterministically at the receiving Architect's startup by curate/apply.py (later
  session.py apply-briefs) — before the model wakes — and never half-applied.

  Fill the frontmatter above:
    edit-id                a unique id (also the filename stem)
    target-file            repo-relative path to the file the ops below mutate
    expected-base-version  MUST equal target-file's current **Version:** line
    proposed-new-version   the version after this brief applies (explicit)
    apply: auto            keep literally — this is what marks the brief appliable

  Then list operations in order. Each is a `## op: <type>` heading. Payloads go in
  TILDE fences (~~~), never backtick fences (role docs contain ``` fences).
  Uniqueness is the anchor: a `before` block / changelog anchor must match EXACTLY
  ONCE. Anything that needs judgment ("renumber the following", "also update X
  elsewhere") does NOT belong in an auto brief — author it as `apply: manual` prose.

  changelog-prepend takes an OPTIONAL `File:` line above `After:`, naming a
  repo-relative file to prepend into instead of target-file (ADR-0123). Add it
  ONLY for a system that keeps its role-doc changelog in a tracked sibling — that
  file has no **Version:** line, so it can never be the target-file. The file must
  already exist. Omit the line entirely otherwise; omitted means target-file.

  Delete the ops you don't use and this comment before sending.
-->

## op: version-bump

## op: replace
~~~before
<the EXACT text to find — must occur exactly once in target-file>
~~~
~~~after
<the replacement text>
~~~

## op: changelog-prepend
After: <the exact anchor line to insert after — e.g. the CHANGELOG marker>
~~~content

- **X.Y.Z** (YYYY-MM-DD) — <literal changelog entry; not a template to adapt>.
~~~

## op: create-file
Path: <repo-relative path that must NOT already exist>
~~~content
<full contents of the new file>
~~~
