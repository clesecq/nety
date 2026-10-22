"""Per-platform config and cache locations."""

import os
import sys
from pathlib import Path

APP = "nety"


def _xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback) / APP


def user_dir(kind):
    """Per-user `config` or `cache` directory: %APPDATA%/%LOCALAPPDATA%, macOS Library or XDG."""
    config = kind == "config"
    if sys.platform == "win32":
        var, fallback = ("APPDATA", "Roaming") if config else ("LOCALAPPDATA", "Local")
        return Path(os.environ.get(var) or Path.home() / "AppData" / fallback) / APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / ("Application Support" if config else "Caches") / APP
    return _xdg("XDG_CONFIG_HOME", ".config") if config else _xdg("XDG_CACHE_HOME", ".cache")


def default_config_path():
    return user_dir("config") / "config.toml"


def config_paths():
    """Candidate config files, in lookup order."""
    paths = [Path.cwd() / f"{APP}.toml", default_config_path()]
    if sys.platform == "darwin":
        paths.append(_xdg("XDG_CONFIG_HOME", ".config") / "config.toml")
    return paths


def cache_path():
    return user_dir("cache") / "cookies.json"
