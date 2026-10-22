"""Files that hold secrets."""

import os


def write_private(path, text):
    """Write `text` to `path`, readable by the owner only from the moment it exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        # os.open's mode only applies to new files: tighten an existing one too.
        if os.name == "posix":
            os.fchmod(f.fileno(), 0o600)
        f.write(text)
