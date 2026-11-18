"""HTTP calls to the Netypareo extranet."""

import base64
import datetime as dt
import html
import json
import logging
import re
import time
from dataclasses import dataclass

import requests

from .files import write_private
from .paths import cache_path

log = logging.getLogger(__name__).debug

BASE = "https://extranet.cfai-alsace.fr/netypareo/index.php"
HOME = f"{BASE}/apprenant/accueil"
XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": HOME}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
SIGNED_ICON = "picto-green-e"


class AuthError(Exception):
    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


class SessionExpired(Exception):
    """The server no longer accepts the session cookies: log in again."""


def check_session(resp):
    # Without a valid session the extranet answers its XHR endpoints with a 404 HTML page.
    if resp.status_code == 404:
        raise SessionExpired(f"HTTP 404 on {resp.request.method} {resp.url.split('?')[0]}")


def hhmm(minutes):
    return f"{minutes // 60:02d}h{minutes % 60:02d}"


@dataclass
class Lesson:
    """One slot of the day's schedule (a "séance" on the server)."""

    code: int
    course: str = "?"
    start: int | None = None
    end: int | None = None
    teacher: str = ""
    room: str = ""
    signed: bool = False
    is_open: bool = False
    # Set only for a lesson outside the day's schedule: the label then names the day.
    day: dt.date | None = None

    @property
    def time(self):
        if self.start is None or self.end is None:
            return "?"
        return f"{hhmm(self.start)}-{hhmm(self.end)}"

    @property
    def label(self):
        prefix = f"{self.day:%d/%m} " if self.day else ""
        return f"{prefix}{self.time} {self.course}"


class Netypareo:
    def __init__(self):
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        # Login of the current session, set by login() and restore_cookies().
        self.user = None
        # (type, code) of the planning's resource, learned from lessons(); needed by seance().
        self.resource = None

    def _xhr_get(self, path):
        # The browser adds a cache-busting timestamp to every XHR GET.
        return self.http.get(f"{BASE}{path}", params={"_": int(time.time() * 1000)}, headers=XHR)

    def login(self, user, password):
        # The first hit on the login page sometimes answers 500; retry once.
        resp = self.http.get(BASE)
        if not resp.ok:
            resp = self.http.get(BASE)
        resp.raise_for_status()
        tag = re.search(r'<input[^>]*name="token_csrf"[^>]*>', resp.text)
        value = re.search(r'value="([^"]*)"', tag.group(0)) if tag else None
        token = value.group(1) if value else ""
        log(f"token_csrf={token!r}")

        resp = self.http.post(
            f"{BASE}/authentication/",
            data={
                "login": user,
                "password": password,
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
        self.http.get(HOME).raise_for_status()
        self.user = user

    def save_cookies(self, user):
        """Store the session cookies (they grant access: owner-only file)."""
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                    "secure": c.secure, "expires": c.expires} for c in self.http.cookies]
        write_private(cache_path(), json.dumps({"login": user, "cookies": cookies}))
        log(f"cookies saved: {cache_path()}")

    def restore_cookies(self, hint):
        """Load cached cookies; return the login if the server still accepts them, else None."""
        try:
            data = json.loads(cache_path().read_text(encoding="utf-8"))
            user, cookies = data["login"], data["cookies"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if hint and hint != user:
            log(f"cookie cache is for {user}, not {hint}")
            return None
        for c in cookies:
            self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"],
                                  secure=c["secure"], expires=c["expires"])
        if self.session_alive():
            self.user = user
            return user
        self.http.cookies.clear()
        return None

    def session_alive(self):
        """True while the server accepts the session cookies."""
        # An expired session redirects or serves the login form instead of the home page.
        resp = self.http.get(HOME, allow_redirects=False)
        log(f"session check: HTTP {resp.status_code}")
        return resp.status_code == 200 and "btnSeConnecter" not in resp.text

    def lessons(self, date):
        resp = self._xhr_get(f"/planning/jour/ajax/{date:%d/%m/%Y}")
        check_session(resp)
        resp.raise_for_status()
        lessons = []
        for semaine in resp.json().get("semaines", []):
            for ressource in semaine.get("ressources", []):
                self.resource = (ressource.get("type"), ressource.get("code"))
                for s in ressource.get("seances", []):
                    detail = s.get("detail") or []
                    start = s.get("minuteDebut", 0)
                    lessons.append(Lesson(
                        code=s["code"],
                        course=s.get("libelle", ""),
                        start=start,
                        end=start + s.get("duree", 0),
                        teacher=detail[0] if len(detail) > 0 else "",
                        room=detail[1] if len(detail) > 1 else "",
                        signed=any(SIGNED_ICON in i.get("classe", "") for i in s.get("icones", [])),
                    ))
        return sorted(lessons, key=lambda lesson: lesson.start)

    def seance(self, code):
        """A lesson looked up by code (any day), with its day set; None if unknown."""
        if not self.resource:
            return None
        resp = self._xhr_get(f"/planning/seance/{code}/{self.resource[0]}/{self.resource[1]}")
        if not resp.ok:
            log(f"seance {code}: HTTP {resp.status_code}")
            return None
        m = re.search(r'class="nom-matiere[^"]*">(.*?)</span>\s*de (\d+)h(\d+) à (\d+)h(\d+) '
                      r'le (\d+)/(\d+)/(\d+)', resp.text, re.S)
        if not m:
            log(f"seance {code}: no title")
            return None
        h1, m1, h2, m2, d, mo, y = map(int, m.groups()[1:])
        return Lesson(code, html.unescape(m.group(1)).strip(), h1 * 60 + m1, h2 * 60 + m2,
                      day=dt.date(y, mo, d))

    def open_lesson_code(self):
        """Code of the lesson the server offers for signing, or None."""
        # Same order as the browser: the modal answers 500 when nothing is open.
        resp = self.http.post(f"{BASE}/emargement/ajax/has-emargement/", headers=XHR)
        log(f"has-emargement: HTTP {resp.status_code} {resp.text[:100]!r}")
        check_session(resp)
        try:
            if resp.ok and not resp.json().get("hasEmargement"):
                return None
        except json.JSONDecodeError:
            pass
        resp = self._xhr_get("/emargement/modal/emargement-ouvert/")
        if not resp.ok:
            log(f"emargement-ouvert: HTTP {resp.status_code}")
            return None
        m = re.search(r"var codeSeance\s*=\s*(\d+);", resp.text) or re.search(
            r'id="modal-emargement-apprenant-(\d+)"', resp.text
        )
        return int(m.group(1)) if m else None

    def signature_form(self, code):
        """Return (codeApprenant, antiCSRFToken), or None if the modal has no form."""
        resp = self._xhr_get(f"/emargement/modal/signature/{code}")
        if not resp.ok:
            log(f"signature modal {code}: HTTP {resp.status_code}")
            return None
        token = re.search(r'name="antiCSRFTokenSignature"\s+value="([^"]*)"', resp.text)
        apprenant = re.search(r"var codeApprenant\s*=\s*(\d+);", resp.text)
        if not token or not apprenant:
            log(f"signature modal {code}: no token/codeApprenant")
            return None
        return int(apprenant.group(1)), token.group(1)

    def submit_signature(self, code_apprenant, code, token, png):
        resp = self.http.post(
            f"{BASE}/emargement-apprenant/enregistrer-signature/",
            data={
                "codeApprenant": code_apprenant,
                "codeSeance": code,
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
