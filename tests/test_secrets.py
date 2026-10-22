import pytest

import nety
from conftest import Answers


@pytest.fixture
def answers(monkeypatch):
    return Answers(monkeypatch, nety)


def test_env_wins(monkeypatch, memory_keyring, answers):
    monkeypatch.setenv("NETYPAREO_LOGIN", "env-user")
    monkeypatch.setenv("NETYPAREO_PASSWORD", "env-pw")
    memory_keyring.set_password("nety", "env-user", "kr")
    assert nety.credentials({"login": "cfg", "password": "cfg-pw"}) == ("env-user", "env-pw", "env")


def test_config_before_keyring(memory_keyring, answers):
    memory_keyring.set_password("nety", "040-1", "kr")
    assert nety.credentials({"login": "040-1", "password": "cfg"}) == ("040-1", "cfg", "config")


def test_keyring_before_prompt(memory_keyring, answers):
    memory_keyring.set_password("nety", "040-1", "kr")
    assert nety.credentials({"login": "040-1"}) == ("040-1", "kr", "keyring")


def test_prompt_last(memory_keyring, answers):
    answers.queue += [" 040-9 ", "typed"]
    assert nety.credentials({}) == ("040-9", "typed", "prompt")


def test_keyring_set_and_delete(memory_keyring):
    nety.keyring_set("040-1", "pw")
    assert nety.keyring_get("040-1") == "pw"
    assert nety.keyring_delete("040-1") is True
    assert nety.keyring_delete("040-1") is False
    assert nety.keyring_get("040-1") is None
