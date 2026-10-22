import pytest

from nety import secrets
from conftest import Answers


@pytest.fixture
def answers(monkeypatch):
    return Answers(monkeypatch, secrets)


def test_env_wins(monkeypatch, memory_keyring, answers):
    monkeypatch.setenv("NETYPAREO_LOGIN", "env-user")
    monkeypatch.setenv("NETYPAREO_PASSWORD", "env-pw")
    memory_keyring.set_password("nety", "env-user", "kr")
    assert secrets.credentials({"login": "cfg", "password": "cfg-pw"}) == ("env-user", "env-pw", "env")


def test_config_before_keyring(memory_keyring, answers):
    memory_keyring.set_password("nety", "040-1", "kr")
    assert secrets.credentials({"login": "040-1", "password": "cfg"}) == ("040-1", "cfg", "config")


def test_keyring_before_prompt(memory_keyring, answers):
    memory_keyring.set_password("nety", "040-1", "kr")
    assert secrets.credentials({"login": "040-1"}) == ("040-1", "kr", "keyring")


def test_prompt_last(memory_keyring, answers):
    answers.queue += [" 040-9 ", "typed"]
    assert secrets.credentials({}) == ("040-9", "typed", "prompt")


def test_keyring_set_and_delete(memory_keyring):
    secrets.keyring_set("040-1", "pw")
    assert secrets.keyring_get("040-1") == "pw"
    assert secrets.keyring_delete("040-1") is True
    assert secrets.keyring_delete("040-1") is False
    assert secrets.keyring_get("040-1") is None
