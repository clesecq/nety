"""Config file: lookup, values, *_command resolution and signature file."""

import logging
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import tomlkit

from .files import write_private
from .paths import APP, config_paths, default_config_path

log = logging.getLogger(__name__).debug


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
KEYS = ("login", "login_command", "password", "password_command", "signature")
TEMPLATE = f"""\
# {APP} configuration
# Each of login/password takes a plain value or a *_command whose first
# output line is the value (string: run by the shell, list: no shell).

# login = "040-xxxxx"
# password_command = "pass show cfai/netypareo"
# signature = "~/signature.png"
"""


class ConfigError(Exception):
    pass


def load_config(explicit):
    """Return (config dict, path) of the first config file found, or ({}, None)."""
    if explicit:
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        candidates = [explicit]
    else:
        candidates = config_paths()
    for path in candidates:
        if path.is_file():
            log(f"config: {path}")
            try:
                cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{path}: {e}")
            if os.name == "posix" and "password" in cfg and path.stat().st_mode & 0o077:
                print(f"Warning: {path} holds a password and is readable by others "
                      f"(chmod 600 it).", file=sys.stderr)
            return cfg, path
    log("config: none found in " + ", ".join(map(str, candidates)))
    return {}, None


def resolve(cfg, key):
    """Value of `key`, or the output of `key_command`, or None."""
    command = cfg.get(f"{key}_command")
    if key in cfg and command is not None:
        raise ConfigError(f"set either '{key}' or '{key}_command', not both")
    if command is None:
        return cfg.get(key)
    if not isinstance(command, (str, list)):
        raise ConfigError(f"'{key}_command' must be a string or a list")
    try:
        result = subprocess.run(command, shell=isinstance(command, str),
                                capture_output=True, text=True)
    except OSError as e:
        raise ConfigError(f"'{key}_command' failed: {e}")
    out = result.stdout.splitlines()[0].strip() if result.stdout.strip() else ""
    if result.returncode != 0 or not out:
        detail = result.stderr.strip() or f"exit code {result.returncode}, empty output"
        raise ConfigError(f"'{key}_command' failed: {detail}")
    return out


def partner(key):
    """The other form of a login/password key (`password` <-> `password_command`)."""
    if key.endswith("_command"):
        return key.removesuffix("_command")
    return f"{key}_command" if key in ("login", "password") else None


def config_problems(cfg):
    problems = [f"unknown key '{k}'" for k in cfg if k not in KEYS]
    problems += [f"both '{k}' and '{k}_command' are set"
                 for k in ("login", "password") if k in cfg and f"{k}_command" in cfg]
    return problems


def target_config_path(explicit):
    """File that settings commands read and write."""
    if explicit:
        return explicit
    return next((p for p in config_paths() if p.is_file()), None) or default_config_path()


def read_doc(path):
    if not path.is_file():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except tomlkit.exceptions.TOMLKitError as e:
        raise ConfigError(f"{path}: {e}")


def write_doc(path, doc):
    write_private(path, tomlkit.dumps(doc))


def signature_path(cli, cfg, cfg_path):
    """--signature, else config `signature` (relative to the config file), else ./signature.png."""
    if cli:
        return cli
    if cfg_path and cfg.get("signature"):
        path = Path(cfg["signature"]).expanduser()
        return path if path.is_absolute() else cfg_path.parent / path
    return Path("signature.png")


def read_png(path):
    try:
        png = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read signature: {e}")
    if not png.startswith(PNG_MAGIC):
        raise ConfigError(f"{path} is not a PNG file")
    return png
