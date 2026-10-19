# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
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

import requests

APP = "auto-sign"
BASE = "https://extranet.cfai-alsace.fr/netypareo/index.php"
HOME = f"{BASE}/apprenant/accueil"
XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": HOME}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SIGNED_ICON = "picto-green-e"

VERBOSE = False


class AuthError(Exception):
    pass


class ConfigError(Exception):
    pass


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
    """Return (codeApprenant, antiCSRFToken), or None if the sheet is not open."""
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


def parse_selection(text, count):
    picked = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            picked.update(range(int(lo), int(hi) + 1))
        else:
            picked.add(int(part))
    if any(i < 1 or i > count for i in picked):
        raise ValueError("index out of range")
    return sorted(picked)


def select(items):
    """Ask which open sheets to sign. Returns the chosen items."""
    open_items = [s for s in items if s["status"] == "open"]
    while True:
        answer = input("Sign which? (e.g. 1,3 or 1-3, a = all open, empty = cancel): ").strip().lower()
        if not answer:
            return []
        if answer == "a":
            return open_items
        try:
            chosen = [items[i - 1] for i in parse_selection(answer, len(items))]
        except ValueError as e:
            print(f"Invalid selection: {e}")
            continue
        not_open = [str(items.index(s) + 1) for s in chosen if s["status"] != "open"]
        if not_open:
            print(f"Not open for signing: {', '.join(not_open)}")
            continue
        return chosen


def print_table(items):
    rows = [("#", "Time", "Course", "Teacher", "Room", "Status")]
    rows += [
        (str(i), s["time"], s["course"], s["teacher"], s["room"], s["status"])
        for i, s in enumerate(items, 1)
    ]
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip())


def main():
    global VERBOSE
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="sign every open sheet without asking")
    p.add_argument("--date", type=lambda s: dt.datetime.strptime(s, "%d/%m/%Y").date(),
                   default=dt.date.today(), help="day to list (DD/MM/YYYY, default today)")
    p.add_argument("--signature", type=Path, help="PNG signature image (default ./signature.png)")
    p.add_argument("--config", type=Path, help="TOML config file (default: searched, see README)")
    p.add_argument("--dry-run", action="store_true", help="list and select, but do not sign")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    VERBOSE = args.verbose

    try:
        cfg, cfg_path = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    signature = args.signature
    if signature is None and cfg_path and cfg.get("signature"):
        signature = Path(cfg["signature"]).expanduser()
        if not signature.is_absolute():
            signature = cfg_path.parent / signature
    signature = signature or Path("signature.png")

    png = None
    if not args.dry_run:
        try:
            png = signature.read_bytes()
        except OSError as e:
            sys.exit(f"Cannot read signature: {e}")
        if not png.startswith(PNG_MAGIC):
            sys.exit(f"{signature} is not a PNG file")

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
    )
    try:
        login(session, *credentials(cfg))
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    items = [s for s in list_seances(session, args.date) if not s["signed"]]
    open_code = find_open_seance(session)
    log(f"emargement-ouvert codeSeance={open_code}")
    if open_code and all(s["code"] != open_code for s in items):
        items.append({"code": open_code, "course": "?", "time": "?", "start": 0,
                      "teacher": "", "room": "", "signed": False})

    for s in items:
        s["status"] = "open" if get_signature_form(session, s["code"]) else "not open"

    if not items:
        print(f"Nothing left to sign on {args.date:%d/%m/%Y}.")
        return 0
    print_table(items)
    if not any(s["status"] == "open" for s in items):
        print("No open émargement.")
        return 0

    chosen = [s for s in items if s["status"] == "open"] if args.all else select(items)
    if not chosen:
        print("Nothing selected.")
        return 0

    failed = False
    for s in chosen:
        label = f"{s['time']} {s['course']}"
        if args.dry_run:
            print(f"[dry-run] would sign {label} (codeSeance={s['code']})")
            continue
        # The CSRF token belongs to one modal opening: fetch a fresh one per signature.
        form = get_signature_form(session, s["code"])
        if not form:
            print(f"FAILED {label}: no longer open", file=sys.stderr)
            failed = True
            continue
        try:
            ts = submit_signature(session, form[0], s["code"], form[1], png)
        except (requests.RequestException, RuntimeError) as e:
            print(f"FAILED {label}: {e}", file=sys.stderr)
            failed = True
            continue
        print(f"Signed {label} at {dt.datetime.fromtimestamp(ts):%H:%M:%S}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
