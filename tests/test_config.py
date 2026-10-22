import sys
from pathlib import Path

import pytest

from nety import config

PNG = b"\x89PNG\r\n\x1a\nxx"


def test_resolve_value():
    assert config.resolve({"login": "040-1"}, "login") == "040-1"
    assert config.resolve({}, "login") is None


def test_resolve_command_shell_and_list():
    assert config.resolve({"password_command": "echo s3cret; echo second"}, "password") == "s3cret"
    assert config.resolve({"password_command": [sys.executable, "-c", "print('pw')"]},
                        "password") == "pw"


@pytest.mark.parametrize("cfg", [
    {"password": "a", "password_command": "echo b"},
    {"password_command": 3},
    {"password_command": "exit 1"},
    {"password_command": "true"},
])
def test_resolve_errors(cfg):
    with pytest.raises(config.ConfigError):
        config.resolve(cfg, "password")


def test_config_problems():
    assert config.config_problems({"login": "a", "signature": "x"}) == []
    assert config.config_problems({"nope": 1, "login": "a", "login_command": "b"}) == [
        "unknown key 'nope'", "both 'login' and 'login_command' are set"]


def test_partner():
    assert config.partner("password") == "password_command"
    assert config.partner("login_command") == "login"
    assert config.partner("signature") is None


def test_signature_path(tmp_path):
    cfg_path = tmp_path / "c" / "config.toml"
    assert config.signature_path(Path("cli.png"), {"signature": "x"}, cfg_path) == Path("cli.png")
    assert config.signature_path(None, {"signature": "s.png"}, cfg_path) == tmp_path / "c" / "s.png"
    assert config.signature_path(None, {"signature": "/abs.png"}, cfg_path) == Path("/abs.png")
    assert config.signature_path(None, {}, None) == Path("signature.png")


def test_read_png(tmp_path):
    good, bad = tmp_path / "a.png", tmp_path / "b.png"
    good.write_bytes(PNG)
    bad.write_bytes(b"GIF89a")
    assert config.read_png(good) == PNG
    with pytest.raises(config.ConfigError):
        config.read_png(bad)
    with pytest.raises(config.ConfigError):
        config.read_png(tmp_path / "missing.png")


def test_load_config_search_order(tmp_path):
    xdg = tmp_path / "config" / "nety" / "config.toml"
    xdg.parent.mkdir(parents=True)
    xdg.write_text('login = "xdg"\n')
    assert config.load_config(None) == ({"login": "xdg"}, xdg)
    local = tmp_path / "nety.toml"
    local.write_text('login = "local"\n')
    assert config.load_config(None)[0] == {"login": "local"}


def test_load_config_errors(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("login = \n")
    with pytest.raises(config.ConfigError):
        config.load_config(broken)
