"""Testy hebe.py (API mobilne eduVULCAN): /api/ap, klucz urządzenia, podpis, klient.

Bez sieci i bez prawdziwego konta: tokeny JWT i odpowiedzi serwera są
zbudowane w teście."""
import base64
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

sys.path.insert(0, str(Path(__file__).parents[1] / "vultron"))
import hebe

PUPIL_NAME = "Jan Kowalski"


def _jwt(**claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJh"


def _token(tenant="warszawa", **extra):
    claims = {"name": PUPIL_NAME, "uid": "u-1", "tenant": tenant, "service": "False",
              "caps": "[]", "nbf": 1000, "iat": 1000, "exp": 4600}
    return _jwt(**{**claims, **extra})


def _ap(**over):
    data = {"Tokens": [_token(), _token(service="True")], "Email": "jan@example.com",
            "GivenName": "Jan", "Surname": "Kowalski", "IsConsentAccepted": True,
            "AccessToken": _jwt(name="x"), "Success": True, "ErrorMessage": None}
    data.update(over)
    return json.dumps(data)


@pytest.fixture(scope="module")
def device():
    return hebe.Device.new()


# ── /api/ap ──────────────────────────────────────────────────────────────

def test_parse_ap_groups_tokens_by_tenant():
    t1, t2, t3 = _token("warszawa"), _token("warszawa"), _token("torun")
    assert hebe.parse_ap(_ap(Tokens=[t1, t2, t3])) == {"warszawa": [t1, t2], "torun": [t3]}


@pytest.mark.parametrize("raw", [
    "<html>",                                   # sesja wygasła, strona logowania
    "[]",
    _ap(Success=False),
    _ap(Success="true"),                        # tylko prawdziwe true
    _ap(IsConsentAccepted=False),
    _ap(Tokens=[]),
    _ap(Tokens="abc"),
    _ap(Tokens=[123]),
    _ap(Tokens=["nie-jwt"]),
    _ap(Tokens=["a.!!!.c"]),
    _ap(Tokens=[_jwt(name="x")]),               # bez tenanta
    _ap(Tokens=[_token(tenant="../evil")]),     # tenant trafia do adresu URL
    _ap(Tokens=[_token(tenant="Bydgoszcz")]),
    None,
])
def test_parse_ap_rejects(raw):
    with pytest.raises(hebe.ApError):
        hebe.parse_ap(raw)


def test_ap_summary_has_no_secrets():
    raw = _ap()
    summary = hebe.ap_summary(raw)
    assert "tokeny=2" in summary and "tenant=warszawa" in summary and "ważny=60 min" in summary
    for secret in (PUPIL_NAME, "Kowalski", "jan@example.com", "u-1", *json.loads(raw)["Tokens"]):
        assert secret not in summary
    assert hebe.ap_summary("<html>").startswith("odpowiedź /api/ap to nie JSON")


def test_ap_error_messages_have_no_secrets():
    token = _token(tenant="../evil")
    with pytest.raises(hebe.ApError) as e:
        hebe.parse_ap(_ap(Tokens=[token]))
    assert token not in str(e.value) and PUPIL_NAME not in str(e.value)


# ── klucz urządzenia ─────────────────────────────────────────────────────

def test_device_save_load_roundtrip(tmp_path, device):
    path = str(tmp_path / "hebe_device.json")
    device.registrations["warszawa"] = "https://lekcjaplus.vulcan.net.pl/warszawa/api"
    device.save(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert sorted(os.listdir(tmp_path)) == ["hebe_device.json"]   # bez plików tymczasowych
    loaded = hebe.Device.load(path)
    assert (loaded.private_key_pem, loaded.device_id, loaded.registrations) == \
        (device.private_key_pem, device.device_id, device.registrations)
    assert loaded.fingerprint == device.fingerprint


def test_device_load_missing_and_corrupt(tmp_path):
    assert hebe.Device.load(str(tmp_path / "brak.json")) is None
    bad = tmp_path / "zly.json"
    for content in ('{"version": 1}', '{"version": 2, "private_key": "", "device_id": "", "registrations": {}}',
                    json.dumps({"version": 1, "private_key": "nie-klucz", "device_id": "d", "registrations": {}})):
        bad.write_text(content)
        with pytest.raises(ValueError):
            hebe.Device.load(str(bad))


def test_device_certificate_and_fingerprint(device):
    pub = device._private_key().public_key()
    der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pem = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert base64.b64decode(device.certificate) == der
    # Certyfikat = treść PEM bez nagłówków i złamań linii.
    assert device.certificate == "".join(pem.decode().strip().splitlines()[1:-1])
    assert device.fingerprint == hashlib.md5(pem, usedforsecurity=False).hexdigest()


# ── podpis ───────────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 8, 2, 11, 35, tzinfo=timezone.utc)
URL = "https://lekcjaplus.vulcan.net.pl/warszawa/api/mobile/register/jwt"


def _verify(device, headers, names, values):
    m = headers["Signature"]
    assert m.startswith(f'keyId="{device.fingerprint}",headers="{" ".join(names)}",algorithm="sha256withrsa",')
    sig = base64.b64decode(m.split("SHA256withRSA(")[1].rstrip(")"))
    device._private_key().public_key().verify(sig, "".join(values).encode(), padding.PKCS1v15(), hashes.SHA256())


def test_signed_headers_with_body(device):
    body = '{"a": "ż"}'
    h = hebe.signed_headers(device, URL, body, NOW)
    assert h["vCanonicalUrl"] == "api%2fmobile%2fregister%2fjwt"
    assert unquote(h["vCanonicalUrl"]) == "api/mobile/register/jwt"
    assert h["vDate"] == "Sun, 08 Nov 2026 02:11:35 GMT"
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert h["Digest"] == f"SHA-256={digest}"
    _verify(device, h, ["vCanonicalUrl", "Digest", "vDate"], [h["vCanonicalUrl"], digest, h["vDate"]])


def test_signed_headers_without_body(device):
    url = "https://lekcjaplus.vulcan.net.pl/warszawa/api/mobile/register/hebe?mode=2"
    h = hebe.signed_headers(device, url, None, NOW)
    assert "Digest" not in h
    assert h["vCanonicalUrl"] == "api%2fmobile%2fregister%2fhebe"   # zapytanie poza podpisem
    _verify(device, h, ["vCanonicalUrl", "vDate"], [h["vCanonicalUrl"], h["vDate"]])


def test_vdate_is_utc_for_local_time(device):
    local = NOW.astimezone(timezone(__import__("datetime").timedelta(hours=1)))
    assert hebe.signed_headers(device, URL, None, local)["vDate"] == "Sun, 08 Nov 2026 02:11:35 GMT"


def test_canonical_url_requires_mobile_api():
    with pytest.raises(ValueError):
        hebe.canonical_url("https://lekcjaplus.vulcan.net.pl/warszawa/api/inne")


@pytest.mark.parametrize("url", [
    "http://lekcjaplus.vulcan.net.pl/x/api",
    "https://lekcjaplus.vulcan.net.pl.evil.pl/x/api",
    "https://evil.pl/x/api",
    "https://u:p@lekcjaplus.vulcan.net.pl/x/api",
    "https://lekcjaplus.vulcan.net.pl:8443/x/api",
    "https://lekcjaplus.vulcan.net.pl:99999/x/api",
    "https://lekcjaplus.vulcan.net.pl/x/api?a=1",
])
def test_check_rest_url_rejects(url):
    with pytest.raises(hebe.HebeError):
        hebe.check_rest_url(url)


# ── klient ───────────────────────────────────────────────────────────────

def _client(device, handler):
    return hebe.HebeClient(device, httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(envelope):
    return httpx.Response(200, json={"Status": {"Code": 0, "Message": "OK"}, "Envelope": envelope})


def test_register_and_accounts(tmp_path):
    device = hebe.Device.new()
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/mobile/register/jwt"):
            return _ok({})
        return _ok([{"Pupil": {"Id": 1}}, "śmieć"])

    c = _client(device, handler)
    tokens = [_token()]
    rest_url = c.register("warszawa", tokens)
    assert rest_url == "https://lekcjaplus.vulcan.net.pl/warszawa/api"
    assert device.registrations == {"warszawa": rest_url}
    assert c.accounts(rest_url) == [{"Pupil": {"Id": 1}}]

    reg, acc = seen
    assert reg.method == "POST" and str(reg.url) == URL
    body = json.loads(reg.content)
    assert body["AppName"] == hebe.APP_NAME and body["Envelope"]["Tokens"] == tokens
    env = body["Envelope"]
    assert env["Certificate"] == device.certificate and env["CertificateThumbprint"] == device.fingerprint
    assert env["SelfIdentifier"] == device.device_id and env["CertificateType"] == "RSA_PEM"
    digest = base64.b64encode(hashlib.sha256(reg.content).digest()).decode()
    assert reg.headers["Digest"] == f"SHA-256={digest}"   # podpisane jest to, co wysłane
    _verify(device, reg.headers, ["vCanonicalUrl", "Digest", "vDate"],
            [reg.headers["vCanonicalUrl"], digest, reg.headers["vDate"]])
    assert acc.method == "GET" and acc.url.params["mode"] == "2" and not acc.content


@pytest.mark.parametrize("response,code", [
    (httpx.Response(200, json={"Status": {"Code": 154, "Message": "Certificate not found"}}), 154),
    (httpx.Response(200, json={"Status": {"Code": "0"}}), None),
    (httpx.Response(200, json=[]), None),
    (httpx.Response(200, text="<!DOCTYPE html>"), None),
    (httpx.Response(500, text="błąd"), None),
    (httpx.Response(302, headers={"Location": "https://evil.pl/"}), None),
])
def test_call_errors(device, response, code):
    c = _client(device, lambda request: response)
    with pytest.raises(hebe.HebeError) as e:
        c.call("GET", "https://lekcjaplus.vulcan.net.pl/x/api", "mobile/register/hebe")
    assert e.value.code == code


def test_transport_error_and_bad_tenant(device):
    def boom(request):
        raise httpx.ConnectError("brak sieci")
    with pytest.raises(hebe.HebeError):
        _client(device, boom).accounts("https://lekcjaplus.vulcan.net.pl/x/api")
    with pytest.raises(hebe.HebeError):
        _client(device, lambda r: _ok({})).register("../evil", [_token()])


def test_accounts_rejects_non_list(device):
    with pytest.raises(hebe.HebeError):
        _client(device, lambda r: _ok({})).accounts("https://lekcjaplus.vulcan.net.pl/x/api")


# ── faza 2: zapytania, stronicowanie, konta, tryb cienia ─────────────────

REST = "https://lekcjaplus.vulcan.net.pl/warszawa/000123/api"


def test_vhint_is_sent_but_not_signed(device):
    h = hebe.signed_headers(device, URL, None, NOW, pupil_id=42)
    assert h["vHint"] == "42"
    assert 'headers="vCanonicalUrl vDate"' in h["Signature"]
    assert "vHint" not in hebe.signed_headers(device, URL, None, NOW)


@pytest.mark.parametrize("value,expected", [
    (datetime(2026, 11, 8, 2, 5, 9), "2026-11-08 02:05:09"),
    (datetime(2026, 11, 8).date(), "2026-11-08"),
    (True, "true"), (-5, "-5"), ("box-1", "box-1"),
])
def test_query_value(value, expected):
    assert hebe.query_value(value) == expected


def test_query_value_rejects_unknown_types():
    with pytest.raises(TypeError):
        hebe.query_value(1.5)


def test_get_all_pages_by_last_id(device, monkeypatch):
    monkeypatch.setattr(hebe, "PAGE_SIZE", 2)
    pages = {hebe.INT_MIN: [{"Id": 1}, {"Id": 2}], 2: [{"Id": 3}, {"Id": 4}], 4: [{"Id": 5}]}
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return _ok(pages[int(request.url.params["lastId"])])

    rows = _client(device, handler).get_all(REST, "mobile/note/byPupil", 7, pupilId=7)
    assert [r["Id"] for r in rows] == [1, 2, 3, 4, 5]
    assert [q["lastId"] for q in seen] == [str(hebe.INT_MIN), "2", "4"]
    assert seen[0]["pageSize"] == "2" and seen[0]["lastSyncDate"] == "1970-01-01 01:00:00"


def test_get_all_stops_when_server_ignores_last_id(device, monkeypatch):
    monkeypatch.setattr(hebe, "PAGE_SIZE", 2)
    calls = []

    def handler(request):
        calls.append(1)
        return _ok([{"Id": 1}, {"Id": 2}])   # zawsze ta sama pełna strona

    rows = _client(device, handler).get_all(REST, "mobile/note/byPupil", 7)
    assert [r["Id"] for r in rows] == [1, 2] and len(calls) == 2


def test_get_all_is_bounded_by_max_pages(device, monkeypatch):
    monkeypatch.setattr(hebe, "PAGE_SIZE", 1)
    monkeypatch.setattr(hebe, "MAX_PAGES", 3)

    def handler(request):
        n = int(request.url.params["lastId"])
        return _ok([{"Id": max(n, 0) + 1}])   # nieskończenie wiele stron

    assert len(_client(device, handler).get_all(REST, "mobile/note/byPupil", 7)) == 3


def test_get_all_without_last_sync(device):
    seen = []
    _client(device, lambda r: (seen.append(r), _ok([]))[1]).get_all(
        REST, "mobile/grade/summary/byPupil", 7, last_sync=None)
    assert "lastSyncDate" not in seen[0].url.params and seen[0].headers["vHint"] == "7"


def _account(**over):
    a = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"},
         "Unit": {"Id": 3, "RestURL": REST}, "ConstituentUnit": {"Id": 5},
         "Periods": [{"Id": 10, "Current": False}, {"Id": 11, "Current": True}],
         "MessageBox": {"GlobalKey": "box-1"}, "Journal": {"Id": 1}, "State": 0}
    a.update(over)
    return a


def test_pupil_from_account():
    p = hebe.pupil_from_account(_account())
    assert (p.pupil_id, p.unit_id, p.constituent_id, p.rest_url, p.period_id, p.box_key, p.has_journal) == \
        (7, 3, 5, REST, 11, "box-1", True)
    q = hebe.pupil_from_account(_account(Periods=[], MessageBox=None, Journal=None))
    assert (q.period_id, q.box_key, q.has_journal) == (None, None, False)


@pytest.mark.parametrize("over", [
    {"Pupil": None}, {"Unit": {"Id": 3}}, {"Pupil": {"Id": "7"}}, {"Pupil": {"Id": True}},
    {"Unit": {"Id": 3, "RestURL": "https://evil.pl/api"}},
    {"Unit": {"Id": 3, "RestURL": "http://lekcjaplus.vulcan.net.pl/x/api"}},
])
def test_pupil_from_account_rejects(over):
    with pytest.raises(hebe.HebeError):
        hebe.pupil_from_account(_account(**over))


def test_get_all_tolerates_odd_ids(device):
    rows = _client(device, lambda r: _ok([{"Id": {"x": 1}}, {"Id": [1]}, {"Id": 3}, {"Id": 3}, {}])).get_all(
        REST, "mobile/note/byPupil", 7)
    assert len(rows) == 4   # nietypowe Id nie wywracają odczytu; duplikat Id=3 usunięty


# ── faza 2b: kształt danych i odpowiedniki stron WWW ─────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-11-08", "2026-11-08"), ("2026-11-08 07:10:00", "2026-11-08"),
    ({"Date": "2026-11-08", "Time": "07:10"}, "2026-11-08"),
    ("08.11.2026", ""), (None, ""), (20261108, ""),
])
def test_api_date(value, expected):
    assert hebe.api_date(value) == expected


def test_web_remarks_shape():
    notes = [{"Id": 5, "ValidAt": "2026-10-01", "Content": "<b>Pochwała</b>", "Points": 10,
              "Creator": {"DisplayName": "Anna Nowak"}, "Category": {"Name": "Zachowanie"}},
             {"Id": 6, "ValidAt": None, "Content": None, "Points": None, "Creator": None, "Category": None}]
    assert hebe.web_remarks(notes) == [
        {"id": 5, "data": "2026-10-01", "tresc": "<b>Pochwała</b>", "autor": "Anna Nowak",
         "kategoria": "Zachowanie", "liczbaPunktow": 10},
        {"id": 6, "data": "", "tresc": "", "autor": "", "kategoria": "", "liczbaPunktow": None}]


def test_surname_first():
    assert hebe.surname_first({"Name": "Anna", "Surname": "Nowak", "DisplayName": "Anna Nowak"}) == "Nowak Anna"
    assert hebe.surname_first({"DisplayName": "Anna Nowak"}) == "Anna Nowak"
    assert hebe.surname_first(None) == ""


def test_local_date(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "Europe/Warsaw")
    time.tzset()
    try:
        assert hebe.local_date("2026-10-03T23:30:00Z") == "2026-10-04"          # UTC -> czas polski
        assert hebe.local_date("2026-10-03T23:30:00.1234567") == "2026-10-03"   # bez strefy: bez zmian
        assert hebe.local_date("2026-10-03") == "2026-10-03"
        assert hebe.local_date(None) == ""
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
