"""Child-process descriptor hygiene tests.

seal_open_fds_for_exec walks /proc/self/fd, which only exists on Linux — the
deployment target. On macOS the helper is a documented no-op, so the module is
skipped rather than reported as a failure.
"""

import os
import sys
import tempfile

import pytest

from core.shims import seal_open_fds_for_exec

pytestmark = pytest.mark.skipif(
    not os.path.isdir("/proc/self/fd"),
    reason=f"/proc/self/fd is absent on {sys.platform}; seal_open_fds_for_exec "
           f"has nothing to enumerate")


def test_seal_open_fds_clears_inheritable_flag():
    with tempfile.TemporaryFile() as handle:
        fd = handle.fileno()
        os.set_inheritable(fd, True)
        assert os.get_inheritable(fd)

        seal_open_fds_for_exec()

        assert not os.get_inheritable(fd)
