import argparse
import datetime as dt
import re
import sys
import tomllib

import pytest
import questionary
import requests

from nety import cli, client, config, ui
from nety.client import AuthError, Lesson, SessionExpired
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


pauses = []


def menu_main(monkeypatch, tty, *argv):
    monkeypatch.setattr("builtins.input", lambda prompt="": pauses.append(prompt) or "")
    monkeypatch.setattr("sys.argv", ["nety", *argv])
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    monkeypatch.setattr("sys.stdout.isatty", lambda: tty)
    return cli.main()


def test_no_command_without_tty_prints_help(monkeypatch, capsys):
    assert menu_main(monkeypatch, False) == 2
    assert "commands" in capsys.readouterr().out


def test_menu_quit(monkeypatch, answers):
    pauses.clear()
    answers.queue.append("Quit")
    assert menu_main(monkeypatch, True) == 0
    assert pauses == []


def test_menu_runs_sign_with_config(monkeypatch, answers, tmp_path):
    pauses.clear()
    seen = []
    monkeypatch.setattr(cli, "cmd_sign", lambda args: seen.append(args) or 0)
    answers.queue.append("Sign attendance sheets")
    assert menu_main(monkeypatch, True, "--config", str(tmp_path / "c.toml")) == 0
    assert seen[0].config == tmp_path / "c.toml"
    assert seen[0].all is False
    assert pauses == ["Press Enter to exit…"]


def test_menu_settings_show_then_back(monkeypatch, answers, tmp_path, capsys):
    path = tmp_path / "c.toml"
    path.write_text('login = "040-1"\n')
    # Back returns to the main menu; then Settings -> Show.
    answers.queue += ["Settings", 7, "Settings", 1]
    assert menu_main(monkeypatch, True, "--config", str(path)) == 0
    assert 'login = 040-1' in capsys.readouterr().out


def test_menu_settings_set_value(monkeypatch, answers, tmp_path):
    path = tmp_path / "c.toml"
    answers.queue += ["Settings", 3, "login", "040-9"]
    assert menu_main(monkeypatch, True, "--config", str(path)) == 0
    assert read(path) == {"login": "040-9"}


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


class WatchClient:
    """Fake extranet for `watch`.

    `polls` lists what each open_lesson_code() call returns or raises; `schedules` what each
    lessons() call returns after the first (default: the first schedule again).
    """

    def __init__(self, lessons, polls, schedules=(), fail_forms=0):
        self._lessons, self.polls, self.schedules = lessons, list(polls), list(schedules)
        self.fail_forms = fail_forms
        self.submitted, self.user = [], None
        self.http = argparse.Namespace(cookies=argparse.Namespace(clear=lambda: None))

    @staticmethod
    def _next(queue, default):
        result = queue.pop(0) if queue else default
        if isinstance(result, Exception):
            raise result
        return result

    def lessons(self, date):
        if self._lessons is None or hasattr(self, "_loaded"):
            self._lessons = self._next(self.schedules, self._lessons)
        self._loaded = True
        return self._lessons

    def open_lesson_code(self):
        return self._next(self.polls, None)

    def signature_form(self, code):
        if self.fail_forms:
            self.fail_forms -= 1
            return None
        return 7, "token"

    def submit_signature(self, code_apprenant, code, token, png):
        self.submitted.append(code)
        return 1_760_882_400

    def save_cookies(self, user):
        pass


@pytest.fixture
def watch_env(monkeypatch, cfg_file, tmp_path):
    """Run cmd_watch on a fake clock (13h00 on 19/10/2026) against a WatchClient."""
    clock = {"t": dt.datetime(2026, 10, 19, 13, 0)}
    logins = []

    def sleep(seconds):
        clock["t"] += dt.timedelta(seconds=seconds)

    def login(client, cfg, hint=None):
        logins.append(1)
        client.user = "040-1"
        return "040-1"

    monkeypatch.setattr(cli, "now", lambda: clock["t"])
    monkeypatch.setattr(cli, "sleep", sleep)
    monkeypatch.setattr(cli, "authenticate", login)
    monkeypatch.setattr(cli, "log_in", login)
    sig = tmp_path / "sig.png"
    sig.write_bytes(PNG)

    def run(fake, **kw):
        monkeypatch.setattr(cli, "Netypareo", lambda: fake)
        base = dict(config=cfg_file, signature=sig, buffer=45, interval=120, dry_run=False)
        return cli.cmd_watch(argparse.Namespace(**{**base, **kw}))

    run.clock, run.logins = clock, logins
    return run


WATCHED = Lesson(4819992, "Info", start=14 * 60, end=18 * 60)
SIGNED = Lesson(4819992, "Info", start=14 * 60, end=18 * 60, signed=True)


def test_watch_signs_when_sheet_opens(watch_env, capsys):
    fake = WatchClient([WATCHED], [None, None, 4819992])
    assert watch_env(fake) == 0
    assert fake.submitted == [4819992]
    # Polling starts at 13h15 (45 min early); the 3rd poll is 4 minutes later.
    assert watch_env.clock["t"] == dt.datetime(2026, 10, 19, 13, 19)
    out = capsys.readouterr().out
    assert "Waiting until 13:15 for 14h00-18h00 Info" in out
    assert "Signed 14h00-18h00 Info" in out


def test_watch_lesson_ends_without_sheet(watch_env, capsys):
    fake = WatchClient([WATCHED], [])
    assert watch_env(fake) == 3
    assert watch_env.clock["t"] == dt.datetime(2026, 10, 19, 18, 0)
    assert "ended without an open attendance sheet" in capsys.readouterr().out


def test_watch_logs_in_again_when_session_expires(watch_env, capsys):
    fake = WatchClient([WATCHED], [None, SessionExpired("HTTP 404"), 4819992])
    assert watch_env(fake) == 0
    assert len(watch_env.logins) == 2
    assert fake.submitted == [4819992]
    assert "Session expired, logging in again." in capsys.readouterr().out


def test_watch_retries_after_network_error(watch_env, capsys):
    fake = WatchClient([WATCHED], [requests.ConnectionError("down"), 4819992])
    assert watch_env(fake) == 0
    assert "Network error, retrying: down" in capsys.readouterr().err


def test_watch_retries_failed_login(watch_env, monkeypatch, capsys):
    fake = WatchClient([WATCHED], [SessionExpired("HTTP 404"), 4819992])
    calls = []

    def flaky_login(client, cfg, hint=None):
        calls.append(1)
        if len(calls) == 1:
            raise AuthError("login failed (check login/password)")
        client.user = "040-1"

    monkeypatch.setattr(cli, "log_in", flaky_login)
    assert watch_env(fake) == 0
    assert "Login error, retrying: login failed" in capsys.readouterr().err


def test_watch_retries_startup_network_error(watch_env, monkeypatch, capsys):
    fake = WatchClient(None, [4819992], schedules=[requests.ConnectionError("no route"), [WATCHED]])
    assert watch_env(fake) == 0
    assert fake.submitted == [4819992]
    assert "Network error, retrying in 120 s: no route" in capsys.readouterr().err


def test_watch_retries_signature_until_it_goes_through(watch_env, capsys):
    fake = WatchClient([WATCHED], [4819992, 4819992], fail_forms=1)
    assert watch_env(fake) == 0
    assert fake.submitted == [4819992]
    assert "Signing failed, retrying at the next check." in capsys.readouterr().err


def test_watch_signature_never_goes_through(watch_env, capsys):
    fake = WatchClient([WATCHED], [4819992], fail_forms=1)
    assert watch_env(fake) == 1
    assert "ended before a signature went through" in capsys.readouterr().err


def test_watch_skips_lesson_signed_meanwhile(watch_env, capsys):
    fake = WatchClient([WATCHED], [4819992], schedules=[[SIGNED]])
    assert watch_env(fake) == 0
    assert not fake.submitted
    assert "14h00-18h00 Info is already signed" in capsys.readouterr().err


def test_watch_dry_run_does_not_submit(watch_env, capsys):
    fake = WatchClient([WATCHED], [4819992])
    assert watch_env(fake, dry_run=True, signature=None) == 0
    assert not fake.submitted
    assert "[dry-run] would sign 14h00-18h00 Info" in capsys.readouterr().out


def test_watch_flags_other_open_sheet(watch_env, capsys):
    other = Lesson(5, "Maths", start=16 * 60, end=17 * 60)
    fake = WatchClient([WATCHED, other], [5, 5, 4819992])
    assert watch_env(fake) == 0
    out = capsys.readouterr().out
    assert out.count("16h00-17h00 Maths, not the one watched") == 1


def test_watch_nothing_left(watch_env, capsys):
    done = Lesson(1, start=8 * 60, end=12 * 60)
    assert watch_env(WatchClient([done], [])) == 0
    assert "Nothing left to watch today." in capsys.readouterr().out


@pytest.mark.parametrize("flag, value", [("--interval", "10"), ("--buffer", "-5")])
def test_watch_rejects_bad_values(flag, value):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["watch", flag, value])


def test_watch_key_starts_polling_early(watch_env, monkeypatch):
    fake = WatchClient([WATCHED], [4819992])
    real_sleep = cli.sleep
    # The first wait (13h00, window opens 13h15) is cut short by a key press.
    calls = []
    monkeypatch.setattr(cli, "sleep", lambda s: calls.append(s) or real_sleep(1) or len(calls) == 1)
    assert watch_env(fake) == 0
    assert watch_env.clock["t"] == dt.datetime(2026, 10, 19, 13, 0, 1)


def test_watch_key_presses_are_rate_limited(watch_env, monkeypatch):
    fake = WatchClient([WATCHED], [None, None, 4819992])
    real_sleep = cli.sleep
    checks = []
    real_poll = fake.open_lesson_code
    fake.open_lesson_code = lambda: checks.append(cli.now()) or real_poll()

    def key_after_a_second(seconds):
        real_sleep(min(seconds, 1))
        return cli.now() >= dt.datetime(2026, 10, 19, 13, 15)  # every wait in the window

    monkeypatch.setattr(cli, "sleep", key_after_a_second)
    assert watch_env(fake) == 0
    gaps = [(b - a).total_seconds() for a, b in zip(checks, checks[1:])]
    assert gaps and all(gap >= cli.MIN_GAP for gap in gaps)
