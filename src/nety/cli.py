"""Command line: sign, watch, settings and signature commands."""

import argparse
import datetime as dt
import json
import logging
import math
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import questionary
import requests
import tomlkit

from . import __doc__ as DESCRIPTION, __version__
from .client import AuthError, Lesson, Netypareo, SessionExpired
from .config import (KEYS, TEMPLATE, ConfigError, config_problems, load_config, partner,
                     read_doc, read_png, resolve, signature_path, target_config_path, write_doc)
from .draw import draw_signature
from .paths import APP, config_paths
from .selfcheck import selfcheck
from .secrets import (credentials, keyring_available, keyring_delete, keyring_get, keyring_set,
                      login_hint)
from .files import write_private
from .ui import CONSOLE, ask_password, done, keys_enabled, print_lessons, select_lessons, wait_key
from .watch import pick_lesson, wait_seconds, window

log = logging.getLogger(__name__).debug
# Module-level so tests can run `watch` on a fake clock.
now = dt.datetime.now
# Returns True when a key cut the wait short.
sleep = wait_key
# Fewest seconds between two checks, however fast keys are pressed.
MIN_GAP = 10


def authenticate(client, cfg):
    """Reuse the cached session or log in. Returns the login."""
    hint = login_hint(cfg)
    with CONSOLE.status("Checking saved session…"):
        user = client.restore_cookies(hint)
    if user:
        done(f"Reused saved session for {user}")
        return user
    return log_in(client, cfg, hint)


def log_in(client, cfg, hint=None):
    """Log in with fresh credentials and cache the cookies. Returns the login."""
    # Prompts, if any, must run outside the spinner.
    user, password, source = credentials(cfg, hint or login_hint(cfg))
    with CONSOLE.status(f"Logging in as {user}…"):
        try:
            client.login(user, password)
        except AuthError as e:
            if source == "keyring":
                e.hint = (f"The keyring password was rejected: run "
                          f"`{APP} settings set password --keyring` to replace it.")
            raise
    client.save_cookies(user)
    done(f"Logged in as {user}" + (" (password from keyring)" if source == "keyring" else ""))
    if source == "prompt" and keyring_available() and questionary.confirm(
            "Save the password in the system keyring?", default=True).unsafe_ask():
        keyring_set(user, password)
        done("Password saved in the system keyring")
    return user


def unsigned_lessons(client, date):
    """The day's unsigned lessons, with the one open for signing flagged."""
    with CONSOLE.status(f"Loading schedule for {date:%d/%m/%Y}…") as status:
        lessons = client.lessons(date)
        unsigned = [lesson for lesson in lessons if not lesson.signed]
        done(f"Schedule {date:%d/%m/%Y}: {len(lessons)} session(s), "
             f"{len(lessons) - len(unsigned)} already signed")

        status.update("Checking for an open attendance sheet…")
        # The signature modal answers for any lesson: only emargement-ouvert tells which is open.
        open_code = client.open_lesson_code()
        log(f"emargement-ouvert codeSeance={open_code}")
        # A lesson may be listed twice (one entry per resource): flag every copy.
        opened = [lesson for lesson in lessons if lesson.code == open_code]
        if open_code and not opened:
            opened = [Lesson(open_code)]
            unsigned.append(opened[0])
        for lesson in opened:
            lesson.is_open = True
        if opened and all(lesson.signed for lesson in opened):
            done(f"Open attendance sheet: {opened[0].label} (already signed)")
        else:
            done(f"Open attendance sheet: {opened[0].label}" if opened
                 else "Open attendance sheet: none")
    return unsigned


def sign_lessons(client, lessons, png, dry_run):
    """Sign each lesson; return True if any signature failed."""
    failed = False
    with CONSOLE.status("Signing…") as status:
        for lesson in lessons:
            if dry_run:
                print(f"[dry-run] would sign {lesson.label} (codeSeance={lesson.code})")
                continue
            # The CSRF token belongs to one modal opening: fetch a fresh one per signature.
            status.update(f"{lesson.label}: opening signature form…")
            form = client.signature_form(lesson.code)
            if not form:
                print(f"FAILED {lesson.label}: no longer open", file=sys.stderr)
                failed = True
                continue
            status.update(f"{lesson.label}: sending signature…")
            try:
                ts = client.submit_signature(form[0], lesson.code, form[1], png)
            except (requests.RequestException, RuntimeError) as e:
                print(f"FAILED {lesson.label}: {e}", file=sys.stderr)
                failed = True
                continue
            print(f"Signed {lesson.label} at {dt.datetime.fromtimestamp(ts):%H:%M:%S}")
    return failed


def draw_to(path):
    """Open the drawing window for `path`. Returns True once the PNG is saved."""
    if path.exists() and not questionary.confirm(
            f"{path} exists. Overwrite?", default=False).unsafe_ask():
        return False
    if not draw_signature(path):
        print("Cancelled.")
        return False
    done(f"Saved signature to {path}")
    return True


def draw_and_store(config, path=None):
    """Draw a signature and set `signature` in the config file. Returns the PNG path or None."""
    cfg_file = target_config_path(config)
    path = (path or cfg_file.parent / "signature.png").expanduser().resolve()
    if not draw_to(path):
        return None
    doc = read_doc(cfg_file)
    doc["signature"] = str(path)
    write_doc(cfg_file, doc)
    print(f"Set 'signature' in {cfg_file}")
    return path


def load_signature(args, cfg, cfg_path):
    """The signature PNG bytes; offers to draw one when the file is missing."""
    path = signature_path(args.signature, cfg, cfg_path)
    if not path.exists() and sys.stdin.isatty() and questionary.confirm(
            f"No signature at {path}. Draw one now?", default=True).unsafe_ask():
        # An explicit --signature is drawn in place; otherwise it goes next to the config.
        if args.signature:
            if not draw_to(path):
                raise ConfigError("no signature drawn")
        else:
            path = draw_and_store(args.config)
            if not path:
                raise ConfigError("no signature drawn")
    return read_png(path)


def cmd_sign(args):
    cfg, cfg_path = load_config(args.config)
    png = None if args.dry_run else load_signature(args, cfg, cfg_path)

    client = Netypareo()
    user = authenticate(client, cfg)
    lessons = unsigned_lessons(client, args.date)
    if not lessons:
        print(f"Nothing left to sign on {args.date:%d/%m/%Y}.")
        return 0
    print_lessons(lessons, args.date)
    if not any(lesson.is_open for lesson in lessons):
        print("No open attendance sheet.")
        return 0

    chosen = [lesson for lesson in lessons if lesson.is_open] if args.all else select_lessons(lessons)
    if not chosen:
        print("Nothing selected.")
        return 0
    failed = sign_lessons(client, chosen, png, args.dry_run)
    # The server may have refreshed the cookies while we worked.
    client.save_cookies(user)
    return 1 if failed else 0


def cmd_watch(args):
    cfg, cfg_path = load_config(args.config)
    png = None if args.dry_run else load_signature(args, cfg, cfg_path)
    client = Netypareo()
    today = now().date()

    # A cron or systemd start may run before the network is up: retry until it is.
    while True:
        try:
            authenticate(client, cfg)
            with CONSOLE.status(f"Loading schedule for {today:%d/%m/%Y}…"):
                lessons = client.lessons(today)
            break
        except (requests.RequestException, SessionExpired) as e:
            if now().date() != today:
                raise
            print(f"Network error, retrying in {args.interval} s: {e}", file=sys.stderr)
            sleep(args.interval)
    lesson = pick_lesson(lessons, now())
    if not lesson:
        print("Nothing left to watch today.")
        return 0
    start, end = window(lesson, today, args.buffer)
    others = {other.code: other for other in lessons if not other.signed and other.code != lesson.code}

    key = ", press a key to check now" if keys_enabled() else ""
    if now() < start:
        print(f"Waiting until {start:%H:%M} for {lesson.label}")
        with CONSOLE.status(f"Waiting until {start:%H:%M} for {lesson.label}{key}…"):
            while (t := now()) < start:
                if sleep(wait_seconds(t, start, args.interval)):
                    break
    done(f"Watching {lesson.label} until {end:%H:%M}, checking every {args.interval} s")

    warned, failed = set(), False
    while True:
        last_check = now()
        try:
            result = check_and_sign(client, cfg, lesson, today, png, args.dry_run)
        except (requests.RequestException, AuthError, SessionExpired) as e:
            # Transient: the server may answer again before the lesson ends.
            kind = "Network" if isinstance(e, requests.RequestException) else "Login"
            print(f"{kind} error, retrying: {e}", file=sys.stderr)
            result = None
        if result in ("signed", "already signed"):
            if result == "already signed":
                done(f"{lesson.label} is already signed")
            client.save_cookies(client.user)
            return 0
        if result == "failed":
            failed = True
            print("Signing failed, retrying at the next check.", file=sys.stderr)
        if result in others and result not in warned:
            print(f"Open attendance sheet: {others[result].label}, not the one watched: "
                  f"run `{APP} sign` to sign it.")
            warned.add(result)

        t = now()
        if t >= end:
            break
        pause = wait_seconds(t, end, args.interval)
        next_check = t + dt.timedelta(seconds=pause)
        with CONSOLE.status(f"Waiting for the sheet of {lesson.label}, "
                            f"next check {next_check:%H:%M:%S}{key}…") as status:
            if sleep(pause):
                # A key asks for a check now, but no more than one every MIN_GAP seconds:
                # count down so the key press visibly did something.
                while (gap := MIN_GAP - (now() - last_check).total_seconds()) > 0:
                    status.update(f"Checking again in {math.ceil(gap)} s "
                                  f"(at most one check every {MIN_GAP} s)…")
                    sleep(min(1, gap))

    client.save_cookies(client.user)
    if failed:
        print(f"{lesson.label} ended before a signature went through.", file=sys.stderr)
        return 1
    print(f"{lesson.label} ended without an open attendance sheet.")
    return 3


def check_and_sign(client, cfg, lesson, day, png, dry_run):
    """One check of the watched lesson.

    Returns "signed", "already signed", "failed", the code of another open sheet, or None."""
    try:
        with CONSOLE.status(f"Checking the attendance sheet of {lesson.label}…"):
            code = client.open_lesson_code()
    except SessionExpired as e:
        log(f"session expired: {e}")
        print("Session expired, logging in again.")
        client.http.cookies.clear()
        log_in(client, cfg)
        code = client.open_lesson_code()
    log(f"emargement-ouvert codeSeance={code}")
    if code != lesson.code:
        return code
    done(f"Open attendance sheet: {lesson.label}")
    # It may have been signed meanwhile (in the browser, or by an attempt that timed out).
    if any(other.code == lesson.code and other.signed for other in client.lessons(day)):
        return "already signed"
    return "failed" if sign_lessons(client, [lesson], png, dry_run) else "signed"


def interval(value):
    seconds = int(value)
    if seconds < 30:
        raise argparse.ArgumentTypeError("at least 30 seconds")
    return seconds


def minutes(value):
    count = int(value)
    if count < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return count


def cmd_settings_path(args):
    path = target_config_path(args.config)
    print(path if path.is_file() else f"{path} (not created yet)")
    return 0


def cmd_settings_show(args):
    cfg, path = load_config(args.config)
    print(f"Config file: {path or 'none'}")
    if not args.config:
        for p in config_paths():
            print(f"  [{'found' if p.is_file() else 'missing'}] {p}")
    for var in ("NETYPAREO_LOGIN", "NETYPAREO_PASSWORD"):
        if os.environ.get(var):
            print(f"Env {var} is set and overrides the config file.")
    if cfg:
        print("Settings:")
        for key, value in cfg.items():
            if key == "password":
                value = "********"
            elif isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False) + "  (no shell)"
            print(f"  {key} = {value}")
    problems = config_problems(cfg)
    for problem in problems:
        print(f"Problem: {problem}")
    if not args.resolve:
        return 2 if problems else 0

    print("Resolved:")
    failed = bool(problems)
    # Same precedence as sign: the env login picks the keyring entry, even if login_command fails.
    login_value = os.environ.get("NETYPAREO_LOGIN")
    for key in ("login", "password"):
        try:
            value = resolve(cfg, key)
        except ConfigError as e:
            print(f"  {key}: error: {e}")
            failed = True
            continue
        if key == "login":
            login_value = login_value or value
        if key == "password" and value is not None:
            value = "******** (ok)"
        elif key == "password" and login_value and keyring_get(login_value):
            value = "******** (system keyring)"
        elif value is None:
            value = "not set (prompted at runtime)"
        print(f"  {key}: {value}")
    signature = signature_path(None, cfg, path)
    try:
        read_png(signature)
        print(f"  signature: {signature} (ok)")
    except ConfigError as e:
        print(f"  signature: error: {e}")
        failed = True
    return 2 if failed else 0


def keyring_user(args):
    """(login, config): the login the keyring entry belongs to, from env or config."""
    if args.key != "password":
        raise ConfigError("--keyring only applies to 'password'")
    if not keyring_available():
        raise ConfigError("no system keyring available")
    cfg = load_config(args.config)[0]
    user = login_hint(cfg)
    if not user:
        raise ConfigError("set 'login' first: the keyring entry is stored per login")
    return user, cfg


def cmd_settings_set(args):
    if args.keyring:
        if args.value:
            raise ConfigError("--keyring prompts for the password: do not pass it as an argument")
        user, cfg = keyring_user(args)
        keyring_set(user, ask_password())
        print(f"Saved the password for {user} in the system keyring.")
        if os.environ.get("NETYPAREO_PASSWORD"):
            print("Note: env NETYPAREO_PASSWORD is set and takes precedence.")
        if "password" in cfg or "password_command" in cfg:
            print("Note: 'password'/'password_command' in the config file take precedence.")
        return 0
    key, values = args.key, args.value
    if key == "password" and not values:
        values = [ask_password()]
    if not values or not values[0]:
        raise ConfigError(f"no value given for '{key}'")
    if len(values) > 1 and not key.endswith("_command"):
        raise ConfigError(f"'{key}' takes one value (quote it)")
    value = values[0] if len(values) == 1 else values
    if key == "signature":
        value = str(Path(values[0]).expanduser().resolve())

    path = target_config_path(args.config)
    doc = read_doc(path)
    doc[key] = value
    other = partner(key)
    if other and other in doc:
        del doc[other]
        print(f"Removed '{other}' (replaced by '{key}').")
    write_doc(path, doc)
    print(f"Set '{key}' in {path}")
    return 0


def cmd_settings_unset(args):
    if args.keyring:
        user = keyring_user(args)[0]
        if keyring_delete(user):
            print(f"Removed the password for {user} from the system keyring.")
        else:
            print(f"No password for {user} in the system keyring.")
        return 0
    path = target_config_path(args.config)
    doc = read_doc(path)
    if args.key not in doc:
        print(f"'{args.key}' is not set in {path}")
        return 0
    del doc[args.key]
    write_doc(path, doc)
    print(f"Removed '{args.key}' from {path}")
    return 0


def cmd_settings_init(args):
    path = target_config_path(args.config)
    # unsafe_ask() lets Ctrl-C propagate, so run() exits with 130.
    if path.exists() and not questionary.confirm(
            f"{path} exists. Overwrite?", default=False).unsafe_ask():
        print("Aborted.")
        return 1

    doc = tomlkit.document()
    doc.add(tomlkit.comment(f"{APP} configuration (see `{APP} settings --help`)"))
    login_value = questionary.text("Login:").unsafe_ask().strip()
    if login_value:
        doc["login"] = login_value

    choices = [
        questionary.Choice("Ask each time", value="none"),
        questionary.Choice("Store the value", value="value"),
        questionary.Choice("Run a command that prints it", value="command"),
    ]
    # The keyring entry is keyed by login, so it needs one.
    if login_value and keyring_available():
        choices.insert(1, questionary.Choice("Store it in the system keyring", value="keyring"))
    choice = questionary.select("Password:", choices=choices).unsafe_ask()
    if choice == "keyring":
        keyring_set(login_value, ask_password())
        print(f"Saved the password for {login_value} in the system keyring.")
    elif choice == "value":
        doc["password"] = ask_password()
    elif choice == "command":
        doc["password_command"] = questionary.text(
            "Command printing the password:", instruction="(e.g. pass show cfai)",
            validate=lambda v: bool(v.strip()) or "Enter a command").unsafe_ask().strip()

    def check_png(answer):
        if not answer.strip():
            return True
        try:
            read_png(Path(answer.strip()).expanduser())
        except ConfigError as e:
            return str(e)
        return True

    choice = questionary.select("Signature:", choices=[
        questionary.Choice("Draw one", value="draw"),
        questionary.Choice("Use a PNG file", value="file"),
        questionary.Choice("Skip", value="skip"),
    ]).unsafe_ask()
    if choice == "draw":
        signature = path.parent.expanduser().resolve() / "signature.png"
        if draw_to(signature):
            doc["signature"] = str(signature)
    elif choice == "file":
        answer = questionary.path("Signature PNG:", validate=check_png).unsafe_ask()
        if answer.strip():
            doc["signature"] = str(Path(answer.strip()).expanduser().resolve())

    write_doc(path, doc)
    print(f"Wrote {path}")
    if "password_command" in doc:
        try:
            resolve(doc.unwrap(), "password")
            print("password_command: ok")
        except ConfigError as e:
            print(f"Warning: {e}", file=sys.stderr)
    return 0


def cmd_settings_edit(args):
    path = target_config_path(args.config)
    if not path.exists():
        write_private(path, TEMPLATE)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or (
        "notepad" if sys.platform == "win32" else "vi")
    # No shell: the path may contain quotes or spaces. The editor may carry flags ("code -w").
    command = shlex.split(editor, posix=os.name == "posix")
    if os.name != "posix":
        # Non-POSIX shlex keeps the quotes around "C:\Program Files\...\editor.exe".
        command = [arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] == '"' else arg
                   for arg in command]
    if not command:
        raise ConfigError("$VISUAL/$EDITOR is empty")
    try:
        subprocess.run(command + [str(path)])
    except OSError as e:
        raise ConfigError(f"cannot run editor {command[0]!r}: {e}")
    try:
        problems = config_problems(tomllib.loads(path.read_text(encoding="utf-8")))
    except tomllib.TOMLDecodeError as e:
        problems = [str(e)]
    for problem in problems:
        print(f"Problem in {path}: {problem}", file=sys.stderr)
    return 2 if problems else 0


def cmd_signature_draw(args):
    return 0 if draw_and_store(args.config, args.output) else 1


def build_parser():
    # SUPPRESS keeps a subcommand's unset option from overwriting the top-level one.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="TOML config file (default: searched, see README)")
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(description=DESCRIPTION, parents=[common])
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(title="commands", metavar="<command>")

    ps = sub.add_parser("sign", parents=[common], help="list attendance sheets and sign open ones")
    ps.add_argument("--all", action="store_true", help="sign every open sheet without asking")
    ps.add_argument("--date", type=lambda s: dt.datetime.strptime(s, "%d/%m/%Y").date(),
                    default=dt.date.today(), help="day to list (DD/MM/YYYY, default today)")
    ps.add_argument("--signature", type=Path, help="PNG signature image")
    ps.add_argument("--dry-run", action="store_true", help="list and select, but do not sign")
    ps.set_defaults(func=cmd_sign)

    pw = sub.add_parser("watch", parents=[common],
                        help="wait for the current lesson's attendance sheet and sign it")
    pw.add_argument("--buffer", type=minutes, default=45, metavar="MIN",
                    help="start watching MIN minutes before the listed start (default 45)")
    pw.add_argument("--interval", type=interval, default=120, metavar="SEC",
                    help="seconds between checks, at least 30 (default 120)")
    pw.add_argument("--signature", type=Path, help="PNG signature image")
    pw.add_argument("--dry-run", action="store_true", help="watch, but do not sign")
    pw.set_defaults(func=cmd_watch)

    pc = sub.add_parser("settings", parents=[common], help="create, show or change the config file")
    pc.set_defaults(func=lambda _args: pc.print_help() or 2)
    actions = pc.add_subparsers(title="actions", metavar="<action>")
    a = actions.add_parser("init", parents=[common], help="create the config file interactively")
    a.set_defaults(func=cmd_settings_init)
    a = actions.add_parser("show", parents=[common], help="show the config file and its settings")
    a.add_argument("--resolve", action="store_true",
                   help="run *_command entries and check the signature file")
    a.set_defaults(func=cmd_settings_show)
    a = actions.add_parser("set", parents=[common], help="set a key (password prompts if no value)")
    a.add_argument("key", choices=KEYS)
    a.add_argument("--keyring", action="store_true",
                   help="store the password in the system keyring instead of the file")
    a.add_argument("value", nargs="*", help="several values make a *_command list (no shell)")
    a.set_defaults(func=cmd_settings_set)
    a = actions.add_parser("unset", parents=[common], help="remove a key")
    a.add_argument("key", choices=KEYS)
    a.add_argument("--keyring", action="store_true",
                   help="remove the password from the system keyring instead of the file")
    a.set_defaults(func=cmd_settings_unset)
    a = actions.add_parser("path", parents=[common], help="print the config file path")
    a.set_defaults(func=cmd_settings_path)
    a = actions.add_parser("edit", parents=[common], help="open the config file in $VISUAL/$EDITOR")
    a.set_defaults(func=cmd_settings_edit)

    pd = sub.add_parser("signature", parents=[common], help="create the signature image")
    pd.set_defaults(func=lambda _args: pd.print_help() or 2)
    actions = pd.add_subparsers(title="actions", metavar="<action>")
    a = actions.add_parser("draw", parents=[common],
                           help="draw it in a window, save the PNG and set 'signature'")
    a.add_argument("--output", type=Path,
                   help="PNG to write (default: signature.png next to the config file)")
    a.set_defaults(func=cmd_signature_draw)

    # No help: kept out of --help, CI runs it on the release binaries.
    a = sub.add_parser("selfcheck")
    a.set_defaults(func=lambda _args: 1 if selfcheck() else 0)
    return p


def ask_set(unset=False):
    """argv for `settings set|unset`, asking which key (and value) to use."""
    verb = "unset" if unset else "set"
    key = questionary.select(f"Which setting to {verb}?", choices=list(KEYS)).unsafe_ask()
    argv = ["settings", verb, key]
    if key == "password" and keyring_available() and questionary.confirm(
            "Use the system keyring?", default=True).unsafe_ask():
        return argv + ["--keyring"]
    if not unset and key != "password":
        value = questionary.text(f"{key}:").unsafe_ask().strip()
        # "--": a value may start with a dash.
        argv += ["--", *(shlex.split(value) if key.endswith("_command") else [value])]
    return argv


# (title, argv or a callable returning it); None quits or goes back.
MAIN_MENU = [
    ("Sign attendance sheets", ["sign"]),
    ("Watch the current lesson", ["watch"]),
    ("Settings", None),
    ("Draw a signature", ["signature", "draw"]),
    ("Quit", None),
]
SETTINGS_MENU = [
    ("Set up interactively", ["settings", "init"]),
    ("Show settings", ["settings", "show"]),
    ("Show settings, resolved", ["settings", "show", "--resolve"]),
    ("Set a value", lambda: ask_set()),
    ("Remove a value", lambda: ask_set(unset=True)),
    ("Print the config file path", ["settings", "path"]),
    ("Edit the config file", ["settings", "edit"]),
    ("Back", None),
]


def choose(message, entries):
    """argv of the picked entry, or None for a quit/back entry."""
    picked = questionary.select(
        message, choices=[questionary.Choice(title, value=i) for i, (title, _) in enumerate(entries)]
    ).unsafe_ask()
    argv = entries[picked][1]
    return argv() if callable(argv) else argv


def menu_argv():
    """argv for the action picked in the menu, or None to quit."""
    while True:
        title = questionary.select("What do you want to do?", choices=[
            questionary.Choice(t, value=t) for t, _ in MAIN_MENU]).unsafe_ask()
        if title != "Settings":
            return dict(MAIN_MENU)[title]
        argv = choose("Settings:", SETTINGS_MENU)
        if argv:
            return argv


def dispatch(args):
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        if e.hint:
            print(e.hint, file=sys.stderr)
        return 2
    except SessionExpired as e:
        print(f"Error: the session expired ({e}). Run the command again.", file=sys.stderr)
        return 2
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 1


def main():
    p = build_parser()
    args = p.parse_args()
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.WARNING, format="[debug] %(message)s")
        logging.getLogger(__package__).setLevel(logging.DEBUG)
    args.config = getattr(args, "config", None)
    if not hasattr(args, "func"):
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            p.print_help()
            return 2
        argv = menu_argv()
        if not argv:
            return 0
        # Carry the top-level options into the chosen command. They go first: the command
        # may end with values that start with a dash. A new list: argv belongs to the menu.
        options = (["--config", str(args.config)] if args.config else []) + (
            ["--verbose"] if getattr(args, "verbose", False) else [])
        # Keep the output readable when the terminal closes with the process.
        try:
            args = p.parse_args([*options, *argv])
            args.config = getattr(args, "config", None)
            code = dispatch(args)
        except SystemExit as e:
            code = e.code
        try:
            input("Press Enter to exit…")
        except EOFError:
            pass
        return code
    return dispatch(args)


def run():
    try:
        sys.exit(main())
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
