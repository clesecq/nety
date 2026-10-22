"""Per-platform config and cache locations."""

import os
import sys
from pathlib import Path

APP = "nety"


def config_paths():
    """Candidate config files, in lookup order."""
    paths = [Path.cwd() / f"{APP}.toml"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / APP / "config.toml")
        return paths
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Application Support" / APP / "config.toml")
    xdg = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    paths.append(Path(xdg) / APP / "config.toml")
    return paths


def cache_path():
    """Session cookie cache: %LOCALAPPDATA%, macOS Caches or XDG cache."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / APP / "cookies.json"


def default_config_path():
    """Platform config file: XDG, macOS Application Support or %APPDATA%."""
    paths = config_paths()
    return paths[1] if len(paths) > 1 else paths[0]
