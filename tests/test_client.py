import datetime as dt
import json
import re

import pytest

from nety import client, paths
from nety.client import Lesson, Netypareo
from conftest import fixture

DAY = dt.date(2026, 10, 19)


@pytest.fixture
def nety():
    return Netypareo()


def test_lessons_parse_planning(nety, requests_mock):
    requests_mock.get(re.compile(r".*/planning/jour/ajax/19/10/2026"),
                      text=fixture("planning_day.json"))
    lessons = nety.lessons(DAY)
    assert [(s.code, s.time, s.signed) for s in lessons] == [
        (4819987, "08h30-12h30", True),
        (4819992, "14h00-18h00", False),
    ]
    assert lessons[0].course == "Informatique et Bureautique"
    assert lessons[0].teacher == "M. TEACHERA, M. TEACHERB"
    assert lessons[0].room == "E-011"


def test_unknown_lesson_label():
    assert Lesson(42).label == "? ?"


def test_open_lesson_code(nety, requests_mock):
    requests_mock.post(re.compile(r".*/has-emargement/"), json={"hasEmargement": True})
    requests_mock.get(re.compile(r".*/emargement-ouvert/"), text=fixture("emargement_ouvert.html"))
    assert nety.open_lesson_code() == 4819992


def test_open_lesson_code_none(nety, requests_mock):
    requests_mock.post(re.compile(r".*/has-emargement/"), text=fixture("has_emargement_false.json"))
    ouvert = requests_mock.get(re.compile(r".*/emargement-ouvert/"), status_code=500)
    assert nety.open_lesson_code() is None
    assert not ouvert.called


def test_signature_form(nety, requests_mock):
    requests_mock.get(re.compile(r".*/modal/signature/4819992"), text=fixture("signature_modal.html"))
    assert nety.signature_form(4819992) == (1000001, "signature-csrf-token")


def test_signature_form_error(nety, requests_mock):
    requests_mock.get(re.compile(r".*/modal/signature/1"), status_code=500)
    assert nety.signature_form(1) is None


def test_submit_signature(nety, requests_mock):
    post = requests_mock.post(re.compile(r".*/enregistrer-signature/"), text=fixture("submit_ok.json"))
    ts = nety.submit_signature(1000001, 4819992, "tok", b"\x89PNG\r\n\x1a\nxx")
    assert ts == 1792410113
    body = post.last_request.text
    assert "codeSeance=4819992" in body and "antiCSRFToken=tok" in body


def test_submit_signature_rejected(nety, requests_mock):
    requests_mock.post(re.compile(r".*/enregistrer-signature/"), json={"erreurFermeture": True})
    with pytest.raises(RuntimeError):
        nety.submit_signature(1, 2, "tok", b"png")


def mock_login(requests_mock, location):
    requests_mock.get(client.BASE, text=fixture("login_page.html"))
    auth = requests_mock.post(f"{client.BASE}/authentication/", status_code=302,
                              headers={"Location": location})
    requests_mock.get(client.HOME, text="home")
    return auth


def test_login_sends_csrf_token(nety, requests_mock):
    auth = mock_login(requests_mock, "/netypareo/index.php/apprenant/accueil")
    nety.login("040-1", "pw")
    assert "token_csrf=login-csrf-token" in auth.last_request.text


def test_login_rejected(nety, requests_mock):
    mock_login(requests_mock, "/netypareo/index.php")
    with pytest.raises(client.AuthError):
        nety.login("040-1", "bad")


def cache_cookie(nety):
    nety.http.cookies.set("PHPSESSID", "abc", domain="extranet.cfai-alsace.fr", path="/")
    nety.save_cookies("040-1")


def test_cookie_cache_round_trip(nety, requests_mock):
    cache_cookie(nety)
    assert paths.cache_path().stat().st_mode & 0o777 == 0o600
    assert json.loads(paths.cache_path().read_text())["login"] == "040-1"

    requests_mock.get(client.HOME, text="home")
    fresh = Netypareo()
    assert fresh.restore_cookies(None) == "040-1"
    assert fresh.http.cookies["PHPSESSID"] == "abc"


@pytest.mark.parametrize("hint,status,text", [
    ("040-2", 200, "home"),
    (None, 302, ""),
    (None, 200, fixture("login_page.html")),
])
def test_cookie_cache_rejected(nety, requests_mock, hint, status, text):
    cache_cookie(nety)
    requests_mock.get(client.HOME, status_code=status, text=text)
    fresh = Netypareo()
    assert fresh.restore_cookies(hint) is None
    assert not fresh.http.cookies


def test_restore_without_cache(nety):
    assert nety.restore_cookies(None) is None


@pytest.mark.parametrize("status, text, alive", [
    (200, "<html>accueil</html>", True),
    (302, "", False),
    (200, '<input name="btnSeConnecter">', False),
])
def test_session_alive(nety, requests_mock, status, text, alive):
    requests_mock.get(client.HOME, status_code=status, text=text)
    assert nety.session_alive() is alive
