# /// script
# requires-python = ">=3.11"
# dependencies = ["questionary", "requests", "rich", "tomlkit"]
# ///
"""Sign Netypareo attendance sheets (émargement) on extranet.cfai-alsace.fr."""

import argparse
import base64
import datetime as dt
import getpass
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import questionary
import requests
import tomlkit
from rich.console import Console
from rich.text import Text

APP = "nety"
BASE = "https://extranet.cfai-alsace.fr/netypareo/index.php"
HOME = f"{BASE}/apprenant/accueil"
XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": HOME}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SIGNED_ICON = "picto-green-e"
KEYS = ("login", "login_command", "password", "password_command", "signature")
TEMPLATE = f"""\
# {APP} configuration
# Each of login/password takes a plain value or a *_command whose first
# output line is the value (string: run by the shell, list: no shell).

# login = "040-xxxxx"
# password_command = "pass show cfai/netypareo"
# signature = "~/signature.png"
"""

VERBOSE = False
# Progress goes to stderr; rich drops the spinner when it is not a terminal.
CONSOLE = Console(stderr=True)


class AuthError(Exception):
    pass


class ConfigError(Exception):
    pass


def done(msg):
    CONSOLE.print(Text.assemble(("✓ ", "green"), msg))


def log(msg):
    if VERBOSE:
        print(f"[debug] {msg}", file=sys.stderr)


def config_paths():
    """Candidate config files, in lookup order."""
    paths = [Path.cwd() / f"{APP}.toml"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / APP / "config.toml")
        return paths
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Application Support" / APP / "config.toml")
    xdg = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    paths.append(Path(xdg) / APP / "config.toml")
    return paths


def load_config(explicit):
    """Return (config dict, path) of the first config file found, or ({}, None)."""
    if explicit:
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        candidates = [explicit]
    else:
        candidates = config_paths()
    for path in candidates:
        if path.is_file():
            log(f"config: {path}")
            try:
                cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{path}: {e}")
            if os.name == "posix" and "password" in cfg and path.stat().st_mode & 0o077:
                print(f"Warning: {path} holds a password and is readable by others "
                      f"(chmod 600 it).", file=sys.stderr)
            return cfg, path
    log("config: none found in " + ", ".join(map(str, candidates)))
    return {}, None


def resolve(cfg, key):
    """Value of `key`, or the output of `key_command`, or None."""
    command = cfg.get(f"{key}_command")
    if key in cfg and command is not None:
        raise ConfigError(f"set either '{key}' or '{key}_command', not both")
    if command is None:
        return cfg.get(key)
    if not isinstance(command, (str, list)):
        raise ConfigError(f"'{key}_command' must be a string or a list")
    try:
        result = subprocess.run(command, shell=isinstance(command, str),
                                capture_output=True, text=True)
    except OSError as e:
        raise ConfigError(f"'{key}_command' failed: {e}")
    out = result.stdout.splitlines()[0].strip() if result.stdout.strip() else ""
    if result.returncode != 0 or not out:
        detail = result.stderr.strip() or f"exit code {result.returncode}, empty output"
        raise ConfigError(f"'{key}_command' failed: {detail}")
    return out


def credentials(cfg):
    login = os.environ.get("NETYPAREO_LOGIN") or resolve(cfg, "login")
    password = os.environ.get("NETYPAREO_PASSWORD") or resolve(cfg, "password")
    login = login or input("Login: ")
    password = password or getpass.getpass("Password: ")
    return login, password


def login(session, user, pw):
    # The first hit on the login page sometimes answers 500; retry once.
    resp = session.get(BASE)
    if not resp.ok:
        resp = session.get(BASE)
    resp.raise_for_status()
    tag = re.search(r'<input[^>]*name="token_csrf"[^>]*>', resp.text)
    value = re.search(r'value="([^"]*)"', tag.group(0)) if tag else None
    token = value.group(1) if value else ""
    log(f"token_csrf={token!r}")

    resp = session.post(
        f"{BASE}/authentication/",
        data={
            "login": user,
            "password": pw,
            "screenWidth": "1920",
            "screenHeight": "1200",
            "token_csrf": token,
            "btnSeConnecter": "Se connecter",
        },
        headers={"Referer": BASE},
        allow_redirects=False,
    )
    location = resp.headers.get("Location", "")
    log(f"auth status={resp.status_code} location={location!r}")
    if resp.status_code not in (302, 303) or "accueil" not in location:
        raise AuthError("login failed (check login/password)")
    session.get(HOME).raise_for_status()


def list_seances(session, date):
    url = f"{BASE}/planning/jour/ajax/{date:%d/%m/%Y}"
    resp = session.get(url, params={"_": int(time.time() * 1000)}, headers=XHR)
    resp.raise_for_status()
    seances = []
    for semaine in resp.json().get("semaines", []):
        for ressource in semaine.get("ressources", []):
            for s in ressource.get("seances", []):
                detail = s.get("detail") or []
                start = s.get("minuteDebut", 0)
                end = start + s.get("duree", 0)
                seances.append({
                    "code": s["code"],
                    "course": s.get("libelle", ""),
                    "time": f"{start // 60:02d}h{start % 60:02d}-{end // 60:02d}h{end % 60:02d}",
                    "start": start,
                    "teacher": detail[0] if len(detail) > 0 else "",
                    "room": detail[1] if len(detail) > 1 else "",
                    "signed": any(SIGNED_ICON in i.get("classe", "") for i in s.get("icones", [])),
                })
    return sorted(seances, key=lambda s: s["start"])


def find_open_seance(session):
    # Same order as the browser: the modal answers 500 when nothing is open.
    resp = session.post(f"{BASE}/emargement/ajax/has-emargement/", headers=XHR)
    log(f"has-emargement: HTTP {resp.status_code} {resp.text[:100]!r}")
    try:
        if resp.ok and not resp.json().get("hasEmargement"):
            return None
    except json.JSONDecodeError:
        pass
    resp = session.get(
        f"{BASE}/emargement/modal/emargement-ouvert/",
        params={"_": int(time.time() * 1000)},
        headers=XHR,
    )
    if not resp.ok:
        log(f"emargement-ouvert: HTTP {resp.status_code}")
        return None
    m = re.search(r"var codeSeance\s*=\s*(\d+);", resp.text) or re.search(
        r'id="modal-emargement-apprenant-(\d+)"', resp.text
    )
    return int(m.group(1)) if m else None


def get_signature_form(session, code):
    """Return (codeApprenant, antiCSRFToken), or None if the modal has no form."""
    resp = session.get(
        f"{BASE}/emargement/modal/signature/{code}",
        params={"_": int(time.time() * 1000)},
        headers=XHR,
    )
    if not resp.ok:
        log(f"signature modal {code}: HTTP {resp.status_code}")
        return None
    token = re.search(r'name="antiCSRFTokenSignature"\s+value="([^"]*)"', resp.text)
    apprenant = re.search(r"var codeApprenant\s*=\s*(\d+);", resp.text)
    if not token or not apprenant:
        log(f"signature modal {code}: no token/codeApprenant")
        return None
    return int(apprenant.group(1)), token.group(1)


def submit_signature(session, code_apprenant, code_seance, token, png):
    resp = session.post(
        f"{BASE}/emargement-apprenant/enregistrer-signature/",
        data={
            "codeApprenant": code_apprenant,
            "codeSeance": code_seance,
            "base64": "data:image/png;base64," + base64.b64encode(png).decode(),
            "antiCSRFToken": token,
        },
        headers=XHR,
    )
    resp.raise_for_status()
    try:
        body = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"unexpected response: {resp.text[:200]}")
    if "timeStampAjout" not in body:
        raise RuntimeError(f"unexpected response: {body}")
    return body["timeStampAjout"]


def table_lines(items):
    """Aligned rows (header first) built from the session data."""
    rows = [("Time", "Course", "Teacher", "Room", "Status")]
    rows += [(s["time"], s["course"], s["teacher"], s["room"], s["status"]) for s in items]
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    return ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in rows]


def select(items):
    """Ask which open sheets to sign. Returns the chosen items."""
    header, *lines = table_lines(items)
    single = sum(s["status"] == "open" for s in items) == 1
    choices = [
        questionary.Choice(line, value=s, checked=single)
        if s["status"] == "open" else questionary.Choice(line, value=s, disabled=s["status"])
        for line, s in zip(lines, items)
    ]
    answer = questionary.checkbox(
        "Sign which?", choices=choices, instruction=f"\n    {header}"
    ).ask()
    return answer or []


def print_table(items):
    print("\n".join(table_lines(items)))


def signature_path(cli, cfg, cfg_path):
    """--signature, else config `signature` (relative to the config file), else ./signature.png."""
    if cli:
        return cli
    if cfg_path and cfg.get("signature"):
        path = Path(cfg["signature"]).expanduser()
        return path if path.is_absolute() else cfg_path.parent / path
    return Path("signature.png")


def read_png(path):
    try:
        png = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read signature: {e}")
    if not png.startswith(PNG_MAGIC):
        raise ConfigError(f"{path} is not a PNG file")
    return png


def cmd_sign(args):
    cfg, cfg_path = load_config(args.config)
    signature = signature_path(args.signature, cfg, cfg_path)
    png = None if args.dry_run else read_png(signature)

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
    )
    user, password = credentials(cfg)
    with CONSOLE.status(f"Logging in as {user}…") as status:
        try:
            login(session, user, password)
        except AuthError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        done(f"Logged in as {user}")

        status.update(f"Loading schedule for {args.date:%d/%m/%Y}…")
        seances = list_seances(session, args.date)
        items = [s for s in seances if not s["signed"]]
        done(f"Schedule {args.date:%d/%m/%Y}: {len(seances)} session(s), "
             f"{len(seances) - len(items)} already signed")

        status.update("Checking for an open attendance sheet…")
        open_code = find_open_seance(session)
        log(f"emargement-ouvert codeSeance={open_code}")
        if open_code and all(s["code"] != open_code for s in items):
            items.append({"code": open_code, "course": "?", "time": "?", "start": 0,
                          "teacher": "", "room": "", "signed": False})
        opened = next((s for s in items if s["code"] == open_code), None)
        done(f"Open attendance sheet: {opened['time']} {opened['course']}" if opened
             else "Open attendance sheet: none")

    # The signature modal answers for any session: only emargement-ouvert tells which is open.
    for s in items:
        s["status"] = "open" if s["code"] == open_code else "not open"

    if not items:
        print(f"Nothing left to sign on {args.date:%d/%m/%Y}.")
        return 0
    if not any(s["status"] == "open" for s in items):
        print_table(items)
        print("No open attendance sheet.")
        return 0

    if args.all:
        print_table(items)
        chosen = [s for s in items if s["status"] == "open"]
    else:
        chosen = select(items)
    if not chosen:
        print("Nothing selected.")
        return 0

    failed = False
    with CONSOLE.status("Signing…") as status:
        for s in chosen:
            label = f"{s['time']} {s['course']}"
            if args.dry_run:
                print(f"[dry-run] would sign {label} (codeSeance={s['code']})")
                continue
            # The CSRF token belongs to one modal opening: fetch a fresh one per signature.
            status.update(f"{label}: opening signature form…")
            form = get_signature_form(session, s["code"])
            if not form:
                print(f"FAILED {label}: no longer open", file=sys.stderr)
                failed = True
                continue
            status.update(f"{label}: sending signature…")
            try:
                ts = submit_signature(session, form[0], s["code"], form[1], png)
            except (requests.RequestException, RuntimeError) as e:
                print(f"FAILED {label}: {e}", file=sys.stderr)
                failed = True
                continue
            print(f"Signed {label} at {dt.datetime.fromtimestamp(ts):%H:%M:%S}")
    return 1 if failed else 0


def partner(key):
    """The other form of a login/password key (`password` <-> `password_command`)."""
    if key.endswith("_command"):
        return key.removesuffix("_command")
    return f"{key}_command" if key in ("login", "password") else None


def config_problems(cfg):
    problems = [f"unknown key '{k}'" for k in cfg if k not in KEYS]
    problems += [f"both '{k}' and '{k}_command' are set"
                 for k in ("login", "password") if k in cfg and f"{k}_command" in cfg]
    return problems


def default_config_path():
    """Platform config file: XDG, macOS Application Support or %APPDATA%."""
    paths = config_paths()
    return paths[1] if len(paths) > 1 else paths[0]


def target_config_path(explicit):
    """File that settings commands read and write."""
    if explicit:
        return explicit
    return next((p for p in config_paths() if p.is_file()), None) or default_config_path()


def read_doc(path):
    if not path.is_file():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except tomlkit.exceptions.TOMLKitError as e:
        raise ConfigError(f"{path}: {e}")


def write_doc(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)


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
    for key in ("login", "password"):
        try:
            value = resolve(cfg, key)
        except ConfigError as e:
            print(f"  {key}: error: {e}")
            failed = True
            continue
        if value is None:
            value = "not set (prompted at runtime)"
        elif key == "password":
            value = "******** (ok)"
        print(f"  {key}: {value}")
    signature = signature_path(None, cfg, path)
    try:
        read_png(signature)
        print(f"  signature: {signature} (ok)")
    except ConfigError as e:
        print(f"  signature: error: {e}")
        failed = True
    return 2 if failed else 0


def cmd_settings_set(args):
    key, values = args.key, args.value
    if key == "password" and not values:
        values = [getpass.getpass("Password: ")]
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
    if path.exists() and input(f"{path} exists. Overwrite? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return 1

    doc = tomlkit.document()
    doc.add(tomlkit.comment(f"{APP} configuration (see `{APP} settings --help`)"))
    login_value = input("Login: ").strip()
    if login_value:
        doc["login"] = login_value

    while True:
        choice = input("Password: (v)alue, (c)ommand, or (n)one = ask each time [n]: ").strip().lower()
        if choice in ("", "n"):
            break
        if choice == "v":
            password = getpass.getpass("Password: ")
            if password:
                doc["password"] = password
                break
        elif choice == "c":
            command = input("Command printing the password (e.g. pass show cfai): ").strip()
            if command:
                doc["password_command"] = command
                break

    while True:
        answer = input("Signature PNG path (empty to skip): ").strip()
        if not answer:
            break
        signature = Path(answer).expanduser().resolve()
        try:
            read_png(signature)
        except ConfigError as e:
            print(e)
            continue
        doc["signature"] = str(signature)
        break

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)
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

    p = argparse.ArgumentParser(description=__doc__, parents=[common])
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
    a.add_argument("value", nargs="*", help="several values make a *_command list (no shell)")
    a.set_defaults(func=cmd_settings_set)
    a = actions.add_parser("unset", parents=[common], help="remove a key")
    a.add_argument("key", choices=KEYS)
    a.set_defaults(func=cmd_settings_unset)
    a = actions.add_parser("path", parents=[common], help="print the config file path")
    a.set_defaults(func=cmd_settings_path)
    a = actions.add_parser("edit", parents=[common], help="open the config file in $VISUAL/$EDITOR")
    a.set_defaults(func=cmd_settings_edit)
    return p


def main():
    global VERBOSE
    p = build_parser()
    args = p.parse_args()
    VERBOSE = getattr(args, "verbose", False)
    args.config = getattr(args, "config", None)
    if not hasattr(args, "func"):
        p.print_help()
        return 2
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2


def run():
    try:
        sys.exit(main())
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    run()
