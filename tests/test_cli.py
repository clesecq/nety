import argparse
import datetime as dt
import re
import sys
import tomllib

import pytest
import questionary

from nety import cli, client, config, ui
from nety.client import Lesson
from conftest import Answers, fixture

PNG = b"\x89PNG\r\n\x1a\nxx"


@pytest.fixture
def answers(monkeypatch):
    return Answers(monkeypatch)


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
    cli.cmd_settings_set(ns(cfg_file, key="password_command", value=["echo", "x"], keyring=False))
    cli.cmd_settings_set(ns(cfg_file, key="password", value=["pw"], keyring=False))
    assert read(cfg_file) == {"login": "040-1", "password": "pw"}
    assert cfg_file.stat().st_mode & 0o777 == 0o600


def test_set_rejects_extra_values(cfg_file):
    with pytest.raises(config.ConfigError):
        cli.cmd_settings_set(ns(cfg_file, key="login", value=["a", "b"], keyring=False))


def test_unset(cfg_file, capsys):
    cli.cmd_settings_unset(ns(cfg_file, key="login", keyring=False))
    assert read(cfg_file) == {}
    cli.cmd_settings_unset(ns(cfg_file, key="login", keyring=False))
    assert "is not set" in capsys.readouterr().out


def test_keyring_set_show_unset(cfg_file, memory_keyring, answers, capsys):
    answers.queue.append("s3cret")
    cli.cmd_settings_set(ns(cfg_file, key="password", value=[], keyring=True))
    assert memory_keyring.get_password("nety", "040-1") == "s3cret"
    assert "password" not in read(cfg_file)

    cli.cmd_settings_show(argparse.Namespace(config=cfg_file, resolve=True))
    out = capsys.readouterr().out
    assert "password: ******** (system keyring)" in out
    assert "s3cret" not in out

    cli.cmd_settings_unset(ns(cfg_file, key="password", keyring=True))
    assert memory_keyring.get_password("nety", "040-1") is None


def test_keyring_needs_password_key(cfg_file):
    with pytest.raises(config.ConfigError):
        cli.cmd_settings_set(ns(cfg_file, key="login", value=[], keyring=True))


def test_show_masks_password(cfg_file, capsys):
    cfg_file.write_text('login = "040-1"\npassword = "*secret"\n')
    cli.cmd_settings_show(argparse.Namespace(config=cfg_file, resolve=True))
    out = capsys.readouterr().out
    assert "*secret" not in out
    assert "password: ******** (ok)" in out


def test_init_writes_config(tmp_path, answers):
    sig = tmp_path / "sig.png"
    sig.write_bytes(PNG)
    target = tmp_path / "new.toml"
    answers.queue += ["040-7", "command", "pass show x", "file", str(sig)]
    assert cli.cmd_settings_init(argparse.Namespace(config=target)) == 0
    assert read(target) == {"login": "040-7", "password_command": "pass show x",
                            "signature": str(sig)}
    assert target.stat().st_mode & 0o777 == 0o600


def test_init_keeps_existing_file(cfg_file, answers):
    answers.queue.append(False)
    assert cli.cmd_settings_init(argparse.Namespace(config=cfg_file)) == 1
    assert read(cfg_file) == {"login": "040-1"}


def test_main_reports_config_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["nety", "settings", "show", "--config",
                                     str(tmp_path / "missing.toml")])
    assert cli.main() == 2
    assert "Config error" in capsys.readouterr().err


def mock_extranet(requests_mock):
    requests_mock.get(client.BASE, text=fixture("login_page.html"))
    requests_mock.post(f"{client.BASE}/authentication/", status_code=302,
                       headers={"Location": "/netypareo/index.php/apprenant/accueil"})
    requests_mock.get(client.HOME, text="home")
    requests_mock.get(re.compile(r".*/planning/jour/ajax/"), text=fixture("planning_day.json"))
    requests_mock.post(re.compile(r".*/has-emargement/"), json={"hasEmargement": True})
    requests_mock.get(re.compile(r".*/emargement-ouvert/"), text=fixture("emargement_ouvert.html"))
    requests_mock.get(re.compile(r".*/modal/signature/"), text=fixture("signature_modal.html"))
    return requests_mock.post(re.compile(r".*/enregistrer-signature/"),
                              text=fixture("submit_ok.json"))


def sign_args(cfg_file, sig, **kw):
    base = dict(config=cfg_file, signature=sig, date=dt.date(2026, 10, 19), all=True,
                dry_run=False)
    return argparse.Namespace(**{**base, **kw})


def test_sign_all(cfg_file, tmp_path, requests_mock, capsys):
    cfg_file.write_text('login = "040-1"\npassword = "pw"\n')
    sig = tmp_path / "sig.png"
    sig.write_bytes(PNG)
    submit = mock_extranet(requests_mock)
    assert cli.cmd_sign(sign_args(cfg_file, sig)) == 0
    out = capsys.readouterr().out
    assert "Signed 14h00-18h00 Informatique et Bureautique" in out
    assert "08h30-12h30" not in out  # already signed, not listed
    assert submit.call_count == 1 and "codeSeance=4819992" in submit.last_request.text


def test_sign_dry_run_does_not_submit(cfg_file, requests_mock, capsys):
    cfg_file.write_text('login = "040-1"\npassword = "pw"\n')
    submit = mock_extranet(requests_mock)
    assert cli.cmd_sign(sign_args(cfg_file, None, dry_run=True)) == 0
    assert "[dry-run] would sign 14h00-18h00" in capsys.readouterr().out
    assert not submit.called


def test_sign_nothing_open(cfg_file, requests_mock, capsys):
    cfg_file.write_text('login = "040-1"\npassword = "pw"\n')
    mock_extranet(requests_mock)
    requests_mock.post(re.compile(r".*/has-emargement/"), json={"hasEmargement": False})
    assert cli.cmd_sign(sign_args(cfg_file, None, dry_run=True)) == 0
    assert "No open attendance sheet." in capsys.readouterr().out


def test_select_ctrl_c_propagates(answers):
    answers.queue.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        ui.select_lessons([Lesson(1, is_open=True)])


def test_show_uses_env_login_for_keyring(cfg_file, memory_keyring, monkeypatch, capsys):
    cfg_file.write_text("")
    monkeypatch.setenv("NETYPAREO_LOGIN", "env-user")
    memory_keyring.set_password("nety", "env-user", "kr")
    cli.cmd_settings_show(argparse.Namespace(config=cfg_file, resolve=True))
    assert "password: ******** (system keyring)" in capsys.readouterr().out


def test_set_password_prompt_rejects_empty(cfg_file, monkeypatch):
    seen = {}

    def password(message, validate=None, **kw):
        seen["validate"] = validate
        raise KeyboardInterrupt
    monkeypatch.setattr(questionary, "password", password)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_settings_set(ns(cfg_file, key="password", value=[], keyring=False))
    assert seen["validate"]("") == "Enter a password"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX editor script")
def test_edit_passes_path_without_shell(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    editor = tmp_path / "ed.sh"
    editor.write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{log}"\n')
    editor.chmod(0o755)
    target = tmp_path / 'my "odd" dir' / "config.toml"
    monkeypatch.setenv("VISUAL", str(editor))
    assert cli.cmd_settings_edit(argparse.Namespace(config=target)) == 0
    assert log.read_text() == str(target)
    assert target.stat().st_mode & 0o777 == 0o600


class FakeClient:
    def __init__(self, lessons, open_code):
        self._lessons, self._open_code = lessons, open_code

    def lessons(self, date):
        return self._lessons

    def open_lesson_code(self):
        return self._open_code


def test_open_lesson_already_signed_is_not_listed():
    lessons = [Lesson(1, signed=True), Lesson(2)]
    assert cli.unsigned_lessons(FakeClient(lessons, 1), dt.date(2026, 10, 19)) == [Lesson(2)]


def test_open_lesson_listed_twice_flags_both():
    lessons = [Lesson(1), Lesson(1)]
    unsigned = cli.unsigned_lessons(FakeClient(lessons, 1), dt.date(2026, 10, 19))
    assert [lesson.is_open for lesson in unsigned] == [True, True]


def test_edit_missing_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL", "no-such-editor-xyz")
    with pytest.raises(config.ConfigError, match="cannot run editor"):
        cli.cmd_settings_edit(argparse.Namespace(config=tmp_path / "c.toml"))


def test_edit_blank_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL", "   ")
    with pytest.raises(config.ConfigError, match="empty"):
        cli.cmd_settings_edit(argparse.Namespace(config=tmp_path / "c.toml"))


def test_keyring_set_without_backend_does_not_prompt(cfg_file, answers):
    with pytest.raises(config.ConfigError, match="no system keyring"):
        cli.cmd_settings_set(ns(cfg_file, key="password", value=[], keyring=True))


def fake_draw(monkeypatch, saved=True):
    calls = []

    def draw(path):
        calls.append(path)
        if saved:
            path.write_bytes(PNG)
        return saved

    monkeypatch.setattr(cli, "draw_signature", draw)
    return calls


def test_signature_draw_sets_config(cfg_file, monkeypatch):
    calls = fake_draw(monkeypatch)
    assert cli.cmd_signature_draw(ns(cfg_file, output=None)) == 0
    assert calls == [cfg_file.parent / "signature.png"]
    assert read(cfg_file) == {"login": "040-1", "signature": str(calls[0])}


def test_signature_draw_cancelled_keeps_config(cfg_file, monkeypatch, capsys):
    fake_draw(monkeypatch, saved=False)
    assert cli.cmd_signature_draw(ns(cfg_file, output=None)) == 1
    assert read(cfg_file) == {"login": "040-1"}
    assert "Cancelled." in capsys.readouterr().out


def test_signature_draw_asks_before_overwrite(cfg_file, monkeypatch, answers):
    calls = fake_draw(monkeypatch)
    (cfg_file.parent / "signature.png").write_bytes(PNG)
    answers.queue.append(False)
    assert cli.cmd_signature_draw(ns(cfg_file, output=None)) == 1
    assert calls == []


def test_init_draws_signature(tmp_path, answers, monkeypatch):
    calls = fake_draw(monkeypatch)
    target = tmp_path / "new.toml"
    answers.queue += ["", "none", "draw"]
    assert cli.cmd_settings_init(argparse.Namespace(config=target)) == 0
    assert read(target) == {"signature": str(tmp_path / "signature.png")}
    assert calls == [tmp_path / "signature.png"]


def test_sign_offers_to_draw_missing_signature(cfg_file, monkeypatch, answers, requests_mock,
                                               capsys):
    cfg_file.write_text('login = "040-1"\npassword = "pw"\n')
    calls = fake_draw(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers.queue.append(True)
    submit = mock_extranet(requests_mock)
    assert cli.cmd_sign(sign_args(cfg_file, None)) == 0
    assert calls == [cfg_file.parent / "signature.png"]
    assert read(cfg_file)["signature"] == str(calls[0])
    assert submit.call_count == 1


def test_sign_missing_signature_without_tty(cfg_file, monkeypatch):
    calls = fake_draw(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(config.ConfigError):
        cli.cmd_sign(sign_args(cfg_file, None))
    assert calls == []
