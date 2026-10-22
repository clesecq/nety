"""Login and password lookup: env, config, system keyring, prompt."""

import logging
import os

import keyring
import keyring.backends.fail
import keyring.errors

from .config import ConfigError, resolve
from .paths import APP
from .ui import ask_login, ask_password

log = logging.getLogger(__name__).debug


def keyring_available():
    return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)


def keyring_get(user):
    """Password stored for `user` in the system keyring, or None."""
    try:
        return keyring.get_password(APP, user)
    except keyring.errors.KeyringError as e:
        log(f"keyring: {e}")
        return None


def keyring_set(user, password):
    try:
        keyring.set_password(APP, user, password)
    except keyring.errors.KeyringError as e:
        raise ConfigError(f"keyring: {e}")


def keyring_delete(user):
    """Remove the stored password; False if there was none."""
    try:
        keyring.delete_password(APP, user)
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as e:
        raise ConfigError(f"keyring: {e}")
    return True


def login_hint(cfg):
    """Configured login, without prompting."""
    return os.environ.get("NETYPAREO_LOGIN") or resolve(cfg, "login")


def credentials(cfg):
    """Return (user, password, source); source is env, config, keyring or prompt."""
    user = login_hint(cfg) or ask_login()
    if os.environ.get("NETYPAREO_PASSWORD"):
        return user, os.environ["NETYPAREO_PASSWORD"], "env"
    password = resolve(cfg, "password")
    if password:
        return user, password, "config"
    password = keyring_get(user)
    if password:
        return user, password, "keyring"
    return user, ask_password(), "prompt"
