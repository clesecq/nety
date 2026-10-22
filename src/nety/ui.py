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


def select_lessons(lessons):
    """Ask which open sheets to sign. Returns the chosen lessons."""
    open_lessons = [lesson for lesson in lessons if lesson.is_open]
    choices = [
        questionary.Choice(f"{lesson.time}  {lesson.course}", value=lesson,
                           checked=len(open_lessons) == 1)
        for lesson in open_lessons
    ]
    return questionary.checkbox("Sign which?", choices=choices).unsafe_ask() or []


def print_lessons(lessons, date):
    table = Table(title=f"Sessions on {date:%d/%m/%Y}", box=box.ROUNDED, header_style="bold")
    for column in ("Time", "Course", "Teacher", "Room", "Status"):
        table.add_column(column, no_wrap=column in ("Time", "Status"))
    for lesson in lessons:
        status = "open" if lesson.is_open else "not open"
        # Text() keeps server strings such as "[bold]" from being read as markup.
        table.add_row(*map(Text, (lesson.time, lesson.course, lesson.teacher, lesson.room)),
                      Text(status, style="bold green" if lesson.is_open else "dim"),
                      style=None if lesson.is_open else "dim")
    Console().print(table)


def wait_key(seconds):
    """Sleep up to `seconds`. Returns True early when a key is pressed in the terminal."""
    if not sys.stdin.isatty():
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
