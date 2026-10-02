"""Named skips for tests of macOS-only capabilities (WI-0468).

POGA's core is supported on Linux; the deploy runner, scheduled jobs and anything else
built on launchd or plutil stay macOS-only. A test of one of those, run on Linux, used to
ERROR on `FileNotFoundError: 'plutil'`, which reads as a broken core. It now skips, and
the reason names the tool, so the skip count says exactly what was not exercised.

Keyed on the tool being on PATH rather than on `sys.platform`: the test needs the tool,
not the brand, and on macOS (where every one of these tools ships) nothing changes.
"""

import shutil
import unittest


def requires_macos(tool):
    """Skip unless `tool` is on PATH, with the reason `requires macOS: <tool>`."""
    return unittest.skipUnless(shutil.which(tool), "requires macOS: %s" % tool)
