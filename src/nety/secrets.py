"""Login and password lookup: env, config, system keyring, prompt."""

import logging
import os

import keyring
import keyring.backends.fail
import keyring.errors
import questionary

from .config import ConfigError, resolve
from .paths import APP

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
    """Return (login, password, source); source is env, config, keyring or prompt."""
    login = login_hint(cfg) or questionary.text(
        "Login:", validate=lambda v: bool(v.strip()) or "Enter a login").unsafe_ask().strip()
    if os.environ.get("NETYPAREO_PASSWORD"):
        return login, os.environ["NETYPAREO_PASSWORD"], "env"
    password = resolve(cfg, "password")
    if password:
        return login, password, "config"
    password = keyring_get(login)
    if password:
        return login, password, "keyring"
    password = questionary.password(
        "Password:", validate=lambda v: bool(v) or "Enter a password").unsafe_ask()
    return login, password, "prompt"
