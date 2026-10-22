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
