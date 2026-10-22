import sys

import pytest

from nety import cli, selfcheck


def test_keyring_and_pillow_pass():
    assert selfcheck.KEYRING_BACKENDS[sys.platform] in selfcheck.check_keyring()
    assert selfcheck.check_pillow() == "PNG rendered"


def test_missing_backend_fails(monkeypatch):
    monkeypatch.setitem(selfcheck.KEYRING_BACKENDS, sys.platform, "Nope")
    with pytest.raises(RuntimeError, match="backend Nope missing"):
        selfcheck.check_keyring()


def test_command_reports_failures(monkeypatch, capsys):
    def broken():
        raise ImportError("libtcl9tk9.0.so")

    monkeypatch.setitem(selfcheck.CHECKS, "tk", broken)
    monkeypatch.setattr(sys, "argv", ["nety", "selfcheck"])
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "FAIL  tk: ImportError: libtcl9tk9.0.so" in out
    assert "ok    pillow" in out


def test_hidden_from_help(capsys):
    cli.build_parser().print_help()
    assert "selfcheck" not in capsys.readouterr().out
