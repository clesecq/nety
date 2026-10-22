"""Command line: sign and settings commands."""

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import questionary
import requests
import tomlkit

from . import __doc__ as DESCRIPTION
from .client import AuthError, Lesson, Netypareo
from .config import (KEYS, TEMPLATE, ConfigError, config_problems, load_config, partner,
                     read_doc, read_png, resolve, signature_path, target_config_path, write_doc)
from .paths import APP, config_paths
from .secrets import (credentials, keyring_available, keyring_delete, keyring_get, keyring_set,
                      login_hint)
from .files import write_private
from .ui import CONSOLE, ask_password, done, print_lessons, select_lessons

log = logging.getLogger(__name__).debug


def authenticate(client, cfg):
    """Reuse the cached session or log in. Returns the login."""
    with CONSOLE.status("Checking saved session…"):
        user = client.restore_cookies(login_hint(cfg))
    if user:
        done(f"Reused saved session for {user}")
        return user
    # Prompts, if any, must run outside the spinner.
    user, password, source = credentials(cfg)
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
        opened = next((lesson for lesson in unsigned if lesson.code == open_code), None)
        if open_code and not opened:
            opened = Lesson(open_code)
            unsigned.append(opened)
        if opened:
            opened.is_open = True
        done(f"Open attendance sheet: {opened.label}" if opened else "Open attendance sheet: none")
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


def cmd_sign(args):
    cfg, cfg_path = load_config(args.config)
    png = None if args.dry_run else read_png(signature_path(args.signature, cfg, cfg_path))

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
    login_value = None
    for key in ("login", "password"):
        try:
            value = resolve(cfg, key)
        except ConfigError as e:
            print(f"  {key}: error: {e}")
            failed = True
            continue
        if key == "login":
            login_value = value
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
    """Login the keyring entry belongs to, from env or config."""
    if args.key != "password":
        raise ConfigError("--keyring only applies to 'password'")
    user = login_hint(load_config(args.config)[0])
    if not user:
        raise ConfigError("set 'login' first: the keyring entry is stored per login")
    return user


def cmd_settings_set(args):
    if args.keyring:
        user = keyring_user(args)
        if args.value:
            raise ConfigError("--keyring prompts for the password: do not pass it as an argument")
        keyring_set(user, ask_password())
        print(f"Saved the password for {user} in the system keyring.")
        cfg = load_config(args.config)[0]
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
        user = keyring_user(args)
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

    answer = questionary.path("Signature PNG (empty to skip):", validate=check_png).unsafe_ask()
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
    subprocess.run(f'{editor} "{path}"', shell=True)
    try:
        problems = config_problems(tomllib.loads(path.read_text(encoding="utf-8")))
    except tomllib.TOMLDecodeError as e:
        problems = [str(e)]
    for problem in problems:
        print(f"Problem in {path}: {problem}", file=sys.stderr)
    return 2 if problems else 0


def build_parser():
    # SUPPRESS keeps a subcommand's unset option from overwriting the top-level one.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="TOML config file (default: searched, see README)")
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(description=DESCRIPTION, parents=[common])
    sub = p.add_subparsers(title="commands", metavar="<command>")

    ps = sub.add_parser("sign", parents=[common], help="list attendance sheets and sign open ones")
    ps.add_argument("--all", action="store_true", help="sign every open sheet without asking")
    ps.add_argument("--date", type=lambda s: dt.datetime.strptime(s, "%d/%m/%Y").date(),
                    default=dt.date.today(), help="day to list (DD/MM/YYYY, default today)")
    ps.add_argument("--signature", type=Path, help="PNG signature image")
    ps.add_argument("--dry-run", action="store_true", help="list and select, but do not sign")
    ps.set_defaults(func=cmd_sign)

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
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.WARNING, format="[debug] %(message)s")
        logging.getLogger(__package__).setLevel(logging.DEBUG)
    args.config = getattr(args, "config", None)
    if not hasattr(args, "func"):
        p.print_help()
        return 2
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


def run():
    try:
        sys.exit(main())
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
