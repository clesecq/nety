import os
import sys
import time

import pytest

from nety import ui

unix = pytest.mark.skipif(os.name == "nt", reason="pty is Unix-only")


@pytest.fixture
def tty_stdin(monkeypatch):
    import pty

    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    # The pty is not this process's controlling terminal: count it as the foreground.
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp())
    yield master
    stdin.close()
    os.close(master)


@unix
def test_wait_key_returns_on_keypress(tty_stdin):
    os.write(tty_stdin, b"x")
    start = time.monotonic()
    assert ui.wait_key(5) is True
    assert time.monotonic() - start < 1


@unix
def test_wait_key_times_out_and_restores_terminal(tty_stdin):
    import termios

    before = termios.tcgetattr(sys.stdin.fileno())
    assert ui.wait_key(0.05) is False
    assert termios.tcgetattr(sys.stdin.fileno()) == before


def test_wait_key_without_terminal_sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    monkeypatch.setattr(ui.time, "sleep", slept.append)
    assert ui.wait_key(3) is False
    assert slept == [3]


@unix
def test_wait_key_in_background_job_just_sleeps(tty_stdin, monkeypatch):
    # `nety watch &`: another process group owns the terminal.
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
    slept = []
    monkeypatch.setattr(ui.time, "sleep", slept.append)
    os.write(tty_stdin, b"x")
    assert not ui.keys_enabled()
    assert ui.wait_key(3) is False
    assert slept == [3]
