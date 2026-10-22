"""Checks that a frozen build bundles what the commands load at run time.

Run by CI on each release binary (`nety selfcheck`, not listed in --help).
"""

import sys
from importlib.metadata import entry_points

from .draw import render_png

# keyring loads its backends through entry points: these need the package metadata.
KEYRING_BACKENDS = {"linux": "SecretService", "darwin": "macOS", "win32": "Windows"}


def check_tk():
    import tkinter as tk

    # Tcl() reads init.tcl without opening a window.
    version = tk.Tcl().eval("info patchlevel")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        # Tk is loaded: only the display is missing (headless Linux runner).
        if "display" not in str(e):
            raise
        return f"Tcl/Tk {version} (no display: {e})"
    root.withdraw()
    root.destroy()
    return f"Tcl/Tk {version}"


def check_keyring():
    import keyring

    found = {ep.name: ep for ep in entry_points(group="keyring.backends")}
    name = KEYRING_BACKENDS.get(sys.platform)
    if name:
        if name not in found:
            raise RuntimeError(f"backend {name} missing (found: {', '.join(sorted(found)) or 'none'})")
        found[name].load()
    active = type(keyring.get_keyring())
    return f"backends {', '.join(sorted(found))}; active {active.__module__}.{active.__name__}"


def check_pillow():
    if not render_png([[(0, 0), (10, 10)]]):
        raise RuntimeError("no PNG rendered")
    return "PNG rendered"


CHECKS = {"tk": check_tk, "keyring": check_keyring, "pillow": check_pillow}


def selfcheck():
    """Run every check, print one line each. Returns the failed check names."""
    failed = []
    for name, check in CHECKS.items():
        try:
            print(f"ok    {name}: {check()}")
        except Exception as e:
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
            failed.append(name)
    return failed
