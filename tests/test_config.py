import sys
from pathlib import Path

import pytest

import nety

PNG = b"\x89PNG\r\n\x1a\nxx"


def test_resolve_value():
    assert nety.resolve({"login": "040-1"}, "login") == "040-1"
    assert nety.resolve({}, "login") is None


def test_resolve_command_shell_and_list():
    assert nety.resolve({"password_command": "echo s3cret; echo second"}, "password") == "s3cret"
    assert nety.resolve({"password_command": [sys.executable, "-c", "print('pw')"]},
                        "password") == "pw"


@pytest.mark.parametrize("cfg", [
    {"password": "a", "password_command": "echo b"},
    {"password_command": 3},
    {"password_command": "exit 1"},
    {"password_command": "true"},
])
def test_resolve_errors(cfg):
    with pytest.raises(nety.ConfigError):
        nety.resolve(cfg, "password")


def test_config_problems():
    assert nety.config_problems({"login": "a", "signature": "x"}) == []
    assert nety.config_problems({"nope": 1, "login": "a", "login_command": "b"}) == [
        "unknown key 'nope'", "both 'login' and 'login_command' are set"]


def test_partner():
    assert nety.partner("password") == "password_command"
    assert nety.partner("login_command") == "login"
    assert nety.partner("signature") is None


def test_signature_path(tmp_path):
    cfg_path = tmp_path / "c" / "config.toml"
    assert nety.signature_path(Path("cli.png"), {"signature": "x"}, cfg_path) == Path("cli.png")
    assert nety.signature_path(None, {"signature": "s.png"}, cfg_path) == tmp_path / "c" / "s.png"
    assert nety.signature_path(None, {"signature": "/abs.png"}, cfg_path) == Path("/abs.png")
    assert nety.signature_path(None, {}, None) == Path("signature.png")


def test_read_png(tmp_path):
    good, bad = tmp_path / "a.png", tmp_path / "b.png"
    good.write_bytes(PNG)
    bad.write_bytes(b"GIF89a")
    assert nety.read_png(good) == PNG
    with pytest.raises(nety.ConfigError):
        nety.read_png(bad)
    with pytest.raises(nety.ConfigError):
        nety.read_png(tmp_path / "missing.png")


def test_load_config_search_order(tmp_path):
    xdg = tmp_path / "config" / "nety" / "config.toml"
    xdg.parent.mkdir(parents=True)
    xdg.write_text('login = "xdg"\n')
    assert nety.load_config(None) == ({"login": "xdg"}, xdg)
    local = tmp_path / "nety.toml"
    local.write_text('login = "local"\n')
    assert nety.load_config(None)[0] == {"login": "local"}


def test_load_config_errors(tmp_path):
    with pytest.raises(nety.ConfigError):
        nety.load_config(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("login = \n")
    with pytest.raises(nety.ConfigError):
        nety.load_config(broken)
