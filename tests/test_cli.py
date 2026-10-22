import argparse
import tomllib

import pytest

import nety
from conftest import Answers

PNG = b"\x89PNG\r\n\x1a\nxx"


@pytest.fixture
def answers(monkeypatch):
    return Answers(monkeypatch, nety)


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "t.toml"
    path.write_text('login = "040-1"\n')
    path.chmod(0o600)
    return path


def ns(cfg_file, **kw):
    return argparse.Namespace(config=cfg_file, **kw)


def read(path):
    return tomllib.loads(path.read_text())


def test_set_replaces_partner(cfg_file):
    nety.cmd_settings_set(ns(cfg_file, key="password_command", value=["echo", "x"], keyring=False))
    nety.cmd_settings_set(ns(cfg_file, key="password", value=["pw"], keyring=False))
    assert read(cfg_file) == {"login": "040-1", "password": "pw"}
    assert cfg_file.stat().st_mode & 0o777 == 0o600


def test_set_rejects_extra_values(cfg_file):
    with pytest.raises(nety.ConfigError):
        nety.cmd_settings_set(ns(cfg_file, key="login", value=["a", "b"], keyring=False))


def test_unset(cfg_file, capsys):
    nety.cmd_settings_unset(ns(cfg_file, key="login", keyring=False))
    assert read(cfg_file) == {}
    nety.cmd_settings_unset(ns(cfg_file, key="login", keyring=False))
    assert "is not set" in capsys.readouterr().out


def test_keyring_set_show_unset(cfg_file, memory_keyring, answers, capsys):
    answers.queue.append("s3cret")
    nety.cmd_settings_set(ns(cfg_file, key="password", value=[], keyring=True))
    assert memory_keyring.get_password("nety", "040-1") == "s3cret"
    assert "password" not in read(cfg_file)

    nety.cmd_settings_show(argparse.Namespace(config=cfg_file, resolve=True))
    out = capsys.readouterr().out
    assert "password: ******** (system keyring)" in out
    assert "s3cret" not in out

    nety.cmd_settings_unset(ns(cfg_file, key="password", keyring=True))
    assert memory_keyring.get_password("nety", "040-1") is None


def test_keyring_needs_password_key(cfg_file):
    with pytest.raises(nety.ConfigError):
        nety.cmd_settings_set(ns(cfg_file, key="login", value=[], keyring=True))


def test_show_masks_password(cfg_file, capsys):
    cfg_file.write_text('login = "040-1"\npassword = "*secret"\n')
    nety.cmd_settings_show(argparse.Namespace(config=cfg_file, resolve=True))
    out = capsys.readouterr().out
    assert "*secret" not in out
    assert "password: ******** (ok)" in out


def test_init_writes_config(tmp_path, answers):
    sig = tmp_path / "sig.png"
    sig.write_bytes(PNG)
    target = tmp_path / "new.toml"
    answers.queue += ["040-7", "command", "pass show x", str(sig)]
    assert nety.cmd_settings_init(argparse.Namespace(config=target)) == 0
    assert read(target) == {"login": "040-7", "password_command": "pass show x",
                            "signature": str(sig)}
    assert target.stat().st_mode & 0o777 == 0o600


def test_init_keeps_existing_file(cfg_file, answers):
    answers.queue.append(False)
    assert nety.cmd_settings_init(argparse.Namespace(config=cfg_file)) == 1
    assert read(cfg_file) == {"login": "040-1"}


def test_main_reports_config_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["nety", "settings", "show", "--config",
                                     str(tmp_path / "missing.toml")])
    assert nety.main() == 2
    assert "Config error" in capsys.readouterr().err
