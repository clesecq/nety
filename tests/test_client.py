import datetime as dt
import json
import re

import pytest
import requests

from nety import client, paths
from conftest import fixture

DAY = dt.date(2026, 10, 19)


@pytest.fixture
def session():
    return requests.Session()


def test_list_seances_parses_planning(session, requests_mock):
    requests_mock.get(re.compile(r".*/planning/jour/ajax/19/10/2026"),
                      text=fixture("planning_day.json"))
    seances = client.list_seances(session, DAY)
    assert [(s["code"], s["time"], s["signed"]) for s in seances] == [
        (4819987, "08h30-12h30", True),
        (4819992, "14h00-18h00", False),
    ]
    assert seances[0]["course"] == "Informatique et Bureautique"
    assert seances[0]["teacher"] == "M. TEACHERA, M. TEACHERB"
    assert seances[0]["room"] == "E-011"


def test_find_open_seance(session, requests_mock):
    requests_mock.post(re.compile(r".*/has-emargement/"), json={"hasEmargement": True})
    requests_mock.get(re.compile(r".*/emargement-ouvert/"), text=fixture("emargement_ouvert.html"))
    assert client.find_open_seance(session) == 4819992


def test_find_open_seance_none(session, requests_mock):
    requests_mock.post(re.compile(r".*/has-emargement/"), text=fixture("has_emargement_false.json"))
    ouvert = requests_mock.get(re.compile(r".*/emargement-ouvert/"), status_code=500)
    assert client.find_open_seance(session) is None
    assert not ouvert.called


def test_get_signature_form(session, requests_mock):
    requests_mock.get(re.compile(r".*/modal/signature/4819992"), text=fixture("signature_modal.html"))
    assert client.get_signature_form(session, 4819992) == (1000001, "signature-csrf-token")


def test_get_signature_form_error(session, requests_mock):
    requests_mock.get(re.compile(r".*/modal/signature/1"), status_code=500)
    assert client.get_signature_form(session, 1) is None


def test_submit_signature(session, requests_mock):
    post = requests_mock.post(re.compile(r".*/enregistrer-signature/"), text=fixture("submit_ok.json"))
    ts = client.submit_signature(session, 1000001, 4819992, "tok", b"\x89PNG\r\n\x1a\nxx")
    assert ts == 1792410113
    body = post.last_request.text
    assert "codeSeance=4819992" in body and "antiCSRFToken=tok" in body


def test_submit_signature_rejected(session, requests_mock):
    requests_mock.post(re.compile(r".*/enregistrer-signature/"), json={"erreurFermeture": True})
    with pytest.raises(RuntimeError):
        client.submit_signature(session, 1, 2, "tok", b"png")


def mock_login(requests_mock, location):
    requests_mock.get(client.BASE, text=fixture("login_page.html"))
    auth = requests_mock.post(f"{client.BASE}/authentication/", status_code=302,
                              headers={"Location": location})
    requests_mock.get(client.HOME, text="home")
    return auth


def test_login_sends_csrf_token(session, requests_mock):
    auth = mock_login(requests_mock, "/netypareo/index.php/apprenant/accueil")
    client.login(session, "040-1", "pw")
    assert "token_csrf=login-csrf-token" in auth.last_request.text


def test_login_rejected(session, requests_mock):
    mock_login(requests_mock, "/netypareo/index.php")
    with pytest.raises(client.AuthError):
        client.login(session, "040-1", "bad")


def test_session_cache_round_trip(session, requests_mock):
    session.cookies.set("PHPSESSID", "abc", domain="extranet.cfai-alsace.fr", path="/")
    client.save_session(session, "040-1")
    assert paths.cache_path().stat().st_mode & 0o777 == 0o600
    assert json.loads(paths.cache_path().read_text())["login"] == "040-1"

    requests_mock.get(client.HOME, text="home")
    fresh = requests.Session()
    assert client.restore_session(fresh, None) == "040-1"
    assert fresh.cookies["PHPSESSID"] == "abc"


@pytest.mark.parametrize("hint,status,text", [
    ("040-2", 200, "home"),
    (None, 302, ""),
    (None, 200, fixture("login_page.html")),
])
def test_session_cache_rejected(session, requests_mock, hint, status, text):
    session.cookies.set("PHPSESSID", "abc", domain="extranet.cfai-alsace.fr", path="/")
    client.save_session(session, "040-1")
    requests_mock.get(client.HOME, status_code=status, text=text)
    fresh = requests.Session()
    assert client.restore_session(fresh, hint) is None
    assert not fresh.cookies


def test_restore_without_cache(session):
    assert client.restore_session(session, None) is None
