"""Terminal output and prompts.

Prompts use unsafe_ask(): Ctrl-C propagates, so run() exits with 130.
"""

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
    return questionary.checkbox("Sign which?", choices=choices).ask() or []


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
