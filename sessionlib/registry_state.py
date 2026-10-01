"""Small content-hash registry receipts; no git history or registry text in output.

The caller routes shared state to main, as the land's main-side stamps do. Tracked
registries reflect this checkout; the gitignored profile lives in the shared root.
Entry bodies end at the next ##/### heading (parents do not absorb children).
Rewording counts as one removed old entry and one added replacement.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import os
import tempfile

FILES = ('principles/master.md', 'habits/master.md', 'users/operator/profile.md')
BASELINE = 'registries-baseline.json'


def _digest(data):
    return sha256(data).hexdigest()


def _snapshot(root, shared):
    files = {}
    for name in FILES:
        path = (shared if name.startswith('users/') else root) / name
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            files[name] = {'hash': None, 'entries': {}}
            continue
        entries, counts = {}, {}
        heading, body = None, []
        for line in data.decode('utf-8').splitlines(keepends=True):
            if re.match(r'^#{2,3} ', line):
                if heading is not None:
                    entries[heading] = _digest(''.join(body).encode('utf-8'))
                heading = line.strip()
                counts[heading] = counts.get(heading, 0) + 1
                if counts[heading] > 1:
                    heading += ' [occurrence %d]' % counts[heading]
                body = []
            body.append(line)
        if heading is not None:
            entries[heading] = _digest(''.join(body).encode('utf-8'))
        files[name] = {'hash': _digest(data), 'entries': entries}
    return files


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_end(root, shared, session_id):
    """Every successful end replaces the shared baseline atomically."""
    data = {'version': 1, 'session_id': session_id, 'files': _snapshot(root, shared)}
    _write(shared / '.session-state' / BASELINE, json.dumps(data, ensure_ascii=False) + '\n')


def orientation(root, shared, dry_run=False):
    """One line; never call absent, malformed or unreadable evidence unchanged."""
    try:
        prior = json.loads((shared / '.session-state' / BASELINE).read_text(encoding='utf-8'))
        assert prior['version'] == 1 and prior['session_id']
        old = prior['files']
        for name in FILES:
            assert isinstance(old[name]['entries'], dict)
            assert old[name]['hash'] is None or re.fullmatch('[0-9a-f]{64}', old[name]['hash'])
        current = _snapshot(root, shared)
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        return 'registries: unknown (no baseline)'
    changed = [name for name in FILES if old[name]['hash'] != current[name]['hash']]
    if not changed:
        return 'registries: unchanged since ' + prior['session_id']
    details, summaries = ['Registry changes since ' + prior['session_id']], []
    for name in changed:
        before, after = old[name]['entries'], current[name]['entries']
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        reworded = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
        summaries.append('%s (+%d −%d entries' %
                         (name, len(added) + len(reworded), len(removed) + len(reworded)))
        details.append('\n' + name)
        for label, entries in [('added', added), ('removed', removed), ('reworded', reworded)]:
            details.extend('%s: %s' % (label, heading) for heading in entries)
        if not (added or removed or reworded):
            details.append('File content changed outside entry bodies or entry order changed.')
    if dry_run:
        return 'registries: unknown (no baseline)'  # no CHANGED promise without a written diff
    text = '\n'.join(details) + '\n'
    path = root / '.session-state' / ('registries-diff-' + _digest(text.encode())[:16] + '.md')
    try:
        _write(path, text)
    except OSError:
        return 'registries: unknown (no baseline)'
    return 'registries: CHANGED — ' + '; '.join(s + '; diff: ' + str(path) + ')' for s in summaries)
