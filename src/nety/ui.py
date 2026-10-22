"""Terminal output and prompts."""

import questionary
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text



# Progress goes to stderr; rich drops the spinner when it is not a terminal.
CONSOLE = Console(stderr=True)


def done(msg):
    CONSOLE.print(Text.assemble(("✓ ", "green"), msg))


def select(items):
    """Ask which open sheets to sign. Returns the chosen items."""
    open_items = [s for s in items if s["status"] == "open"]
    choices = [
        questionary.Choice(f"{s['time']}  {s['course']}", value=s, checked=len(open_items) == 1)
        for s in open_items
    ]
    return questionary.checkbox("Sign which?", choices=choices).ask() or []


def print_table(items, date):
    table = Table(title=f"Sessions on {date:%d/%m/%Y}", box=box.ROUNDED, header_style="bold")
    for column in ("Time", "Course", "Teacher", "Room", "Status"):
        table.add_column(column, no_wrap=column in ("Time", "Status"))
    for s in items:
        is_open = s["status"] == "open"
        # Text() keeps server strings such as "[bold]" from being read as markup.
        table.add_row(*(Text(s[k]) for k in ("time", "course", "teacher", "room")),
                      Text(s["status"], style="bold green" if is_open else "dim"),
                      style=None if is_open else "dim")
    Console().print(table)
