"""Terminal output and prompts.

Prompts use unsafe_ask(): Ctrl-C propagates, so run() exits with 130.
"""

import os
import sys
import time

import questionary
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

# Progress goes to stderr; rich drops the spinner when it is not a terminal.
CONSOLE = Console(stderr=True)


def done(msg):
    CONSOLE.print(Text.assemble(("✓ ", "green"), msg))


def ask_login():
    return questionary.text(
        "Login:", validate=lambda v: bool(v.strip()) or "Enter a login").unsafe_ask().strip()


def ask_password():
    return questionary.password(
        "Password:", validate=lambda v: bool(v) or "Enter a password").unsafe_ask()


def select_lessons(candidates):
    """Ask which of the signable lessons to sign. Returns the chosen lessons."""
    alone = sum(lesson.is_open for lesson in candidates) == 1
    choices = [
        questionary.Choice(lesson.label, value=lesson, checked=lesson.is_open and alone)
        for lesson in candidates
    ]
    return questionary.checkbox("Sign which?", choices=choices).unsafe_ask() or []


def status(lesson):
    if lesson.is_open:
        return "open"
    return "unsigned (past)" if lesson.past else "not open"


def print_lessons(lessons, title):
    table = Table(title=title, box=box.ROUNDED, header_style="bold")
    dated = any(lesson.past for lesson in lessons)
    columns = (("Date",) if dated else ()) + ("Time", "Course", "Teacher", "Room", "Status")
    for column in columns:
        table.add_column(column, no_wrap=column in ("Date", "Time", "Status"))
    for lesson in lessons:
        cells = ((f"{lesson.day:%d/%m}" if lesson.day else "?",) if dated else ()) + (
            lesson.time, lesson.course, lesson.teacher, lesson.room)
        # Text() keeps server strings such as "[bold]" from being read as markup.
        table.add_row(*map(Text, cells),
                      Text(status(lesson), style="bold green" if lesson.is_open
                           else "yellow" if lesson.past else "dim"),
                      style=None if lesson.signable else "dim")
    Console().print(table)


def keys_enabled():
    """True when wait_key can read keys: stdin is a terminal and nety runs in the foreground."""
    if not sys.stdin.isatty():
        return False
    if os.name == "nt":
        return True
    # Changing the terminal from a background job (`nety watch &`) stops it with SIGTTOU.
    try:
        return os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
    except OSError:
        return False


def wait_key(seconds):
    """Sleep up to `seconds`. Returns True early when a key is pressed in the terminal."""
    if not keys_enabled():
        time.sleep(seconds)
        return False
    if os.name == "nt":
        import msvcrt

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if msvcrt.kbhit():
                # Special keys send two characters: drain both.
                while msvcrt.kbhit():
                    msvcrt.getwch()
                return True
            time.sleep(0.1)
        return False

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        # cbreak: any key counts, not only Enter, and it is not echoed. Ctrl-C still works.
        # TCSANOW keeps a key pressed while nety was busy (the default flushes it).
        tty.setcbreak(fd, termios.TCSANOW)
        ready, _, _ = select.select([fd], [], [], seconds)
        if ready:
            os.read(fd, 1024)  # drop the key, escape sequences included
        return bool(ready)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
