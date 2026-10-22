"""HTTP calls to the Netypareo extranet."""

import base64
import json
import logging
import os
import re
import time

from .paths import cache_path

log = logging.getLogger(__name__).debug


BASE = "https://extranet.cfai-alsace.fr/netypareo/index.php"


HOME = f"{BASE}/apprenant/accueil"


XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": HOME}


SIGNED_ICON = "picto-green-e"


class AuthError(Exception):
    pass


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


def save_session(session, user):
    """Store the session cookies (they grant access: owner-only file)."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                "secure": c.secure, "expires": c.expires} for c in session.cookies]
    path.write_text(json.dumps({"login": user, "cookies": cookies}), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    log(f"cookies saved: {path}")


def restore_session(session, hint):
    """Load cached cookies; return the login if the server still accepts them, else None."""
    path = cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        user, cookies = data["login"], data["cookies"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if hint and hint != user:
        log(f"cookie cache is for {user}, not {hint}")
        return None
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                            secure=c["secure"], expires=c["expires"])
    # An expired session redirects or serves the login form instead of the home page.
    resp = session.get(HOME, allow_redirects=False)
    log(f"cached session: HTTP {resp.status_code}")
    if resp.status_code == 200 and "btnSeConnecter" not in resp.text:
        return user
    session.cookies.clear()
    return None


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
