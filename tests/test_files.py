import os
import sys

import pytest

from nety.files import write_private


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_write_private_new_and_existing(tmp_path):
    new = tmp_path / "sub" / "new.txt"
    write_private(new, "a")
    assert new.read_text() == "a" and new.stat().st_mode & 0o777 == 0o600

    loose = tmp_path / "loose.txt"
    loose.write_text("old")
    os.chmod(loose, 0o644)
    write_private(loose, "b")
    assert loose.read_text() == "b" and loose.stat().st_mode & 0o777 == 0o600
