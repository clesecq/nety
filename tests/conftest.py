from pathlib import Path

import keyring
import keyring.backend
import keyring.backends.fail
import keyring.errors
import pytest
import questionary

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real config, cache, env credentials or keyring leak into a test."""
    for var in ("NETYPAREO_LOGIN", "NETYPAREO_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # macOS and Windows ignore XDG: their dirs hang off the home dir or %APPDATA%.
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.chdir(tmp_path)
    # A test that wants a keyring asks for memory_keyring; the others see none.
    previous = keyring.get_keyring()
    keyring.set_keyring(keyring.backends.fail.Keyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


class Answers:
    """Stand-in for questionary prompts: returns queued answers in order."""

    def __init__(self, monkeypatch):
        self.queue = []
        for name in ("text", "password", "confirm", "select", "path", "checkbox"):
            monkeypatch.setattr(questionary, name, self._prompt)

    def _prompt(self, *args, **kwargs):
        answers = self

        class Question:
            def unsafe_ask(self):
                value = answers.queue.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value

            def ask(self):
                try:
                    return self.unsafe_ask()
                except KeyboardInterrupt:
                    return None

        return Question()
