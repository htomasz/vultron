"""Golden master eduVULCAN na czas refaktoru (plan: claude/refaktor-plan.md, faza 0).

Każdy fetcher, wiadomości szkolne i przedszkolne dostają stałe, syntetyczne
odpowiedzi API (Jan Kowalski, Anna Nowak, test@example.com) przy zamrożonej
dacie. Wynik, czyli opublikowane sensory i pełny zrzut bazy, porównujemy
z zapisanym wzorcem tests/golden/edu_fetchers.json. Po każdym kroku refaktoru
wynik musi być identyczny co do bajtu.

Wzorzec aktualizuje się świadomie (zmiana zachowania, za zgodą autora):
    GOLDEN_UPDATE=1 python -m pytest -q tests/test_golden_master.py

Dane prawdziwe idą tylko do lokalnego wzorca w katalogu golden-local/ (poza
repo, w .gitignore), nigdy tutaj.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest


GOLDEN = Path(__file__).resolve().parent / "golden" / "edu_fetchers.json"
NOW = datetime(2026, 11, 25, 10, 0, 0)   # środa: plan ma dziś, wczoraj i jutro
BASE = "https://uczen.eduvulcan.pl/warszawa"


def d(days: int, time: str = "00:00:00") -> str:
    return f"{(NOW + timedelta(days=days)).date().isoformat()}T{time}"


def dotted(days: int) -> str:
    x = (NOW + timedelta(days=days)).date()
    return f"{x.day:02d}.{x.month:02d}.{x.year}"


STUDENT = {"slug": "jan_kowalski", "key": "k1", "uczen": "Jan Kowalski", "idDziennik": 1, "periodId": 10,
           "city": "warszawa", "domain": "eduvulcan.pl", "globalKeySkrzynka": "box-1"}
KID = {"slug": "ola_kowalska", "key": "k2", "uczen": "Ola Kowalska", "idDziennik": 2,
       "city": "warszawa", "domain": "eduvulcan.pl", "globalKeySkrzynka": "box-2"}

# Odpowiedzi stron WWW eduVULCAN: endpoint -> dane albo (status, dane).
# Klucz "Endpoint?param=wartość" ma pierwszeństwo przed samym "Endpoint".
SCHOOL_API = {
    "OkresyKlasyfikacyjne": [
        {"id": 10, "numerOkresu": 1, "dataOd": "2026-09-01T00:00:00", "dataDo": "2027-01-25T23:59:59"},
        {"id": 11, "numerOkresu": 2, "dataOd": "2027-01-26T00:00:00", "dataDo": "2027-06-26T23:59:59"},
    ],
    "Oceny?idOkresKlasyfikacyjny=10": {
        "ustawienia": {"isSredniaAndPunkty": True},
        "ocenyPrzedmioty": [
            {"przedmiotNazwa": "Matematyka", "proponowanaOcenaOkresowa": "4+", "ocenaOkresowa": "",
             "kolumnyOcenyCzastkowe": [
                 {"idKolumny": 100, "kategoriaKolumny": "Sprawdzian", "nazwaKolumny": "Ułamki",
                  "oceny": [{"wpis": "5", "dataOceny": dotted(-5), "waga": 3}]},
                 {"idKolumny": 101, "kategoriaKolumny": "Kartkówka", "nazwaKolumny": "Procenty",
                  "oceny": [{"wpis": "2", "dataOceny": dotted(-9), "waga": 1},
                            {"wpis": "4", "dataOceny": dotted(-2), "waga": 1, "idOcenaPoprawiona": 1}]},
             ]},
            {"przedmiotNazwa": "Chemia", "proponowanaOcenaOkresowa": "", "ocenaOkresowa": "",
             "kolumnyOcenyCzastkowe": [
                 {"idKolumny": 200, "kategoriaKolumny": "Kartkówki", "nazwaKolumny": "Kartkówka 1",
                  "oceny": [{"wpis": "47 (%)", "dataOceny": dotted(-1), "waga": 1}]}]},
            {"przedmiotNazwa": "Zachowanie", "proponowanaOcenaOkresowa": "bardzo dobre", "ocenaOkresowa": "",
             "kolumnyOcenyCzastkowe": []},
        ]},
    "Oceny?idOkresKlasyfikacyjny=11": {"ustawienia": {"isSredniaAndPunkty": False}, "ocenyPrzedmioty": []},
    "PlanZajec": [
        {"data": d(-1), "godzinaOd": d(-1, "08:00:00"), "godzinaDo": d(-1, "08:45:00"), "przedmiot": "Matematyka",
         "sala": "12", "prowadzacy": "Nowak Anna", "adnotacja": 0, "idJednostkaSkladowa": 5},
        {"data": d(0), "godzinaOd": d(0, "08:00:00"), "godzinaDo": d(0, "08:45:00"), "przedmiot": "Chemia",
         "sala": "7", "prowadzacy": "Nowak Anna", "adnotacja": 1, "idJednostkaSkladowa": 5},
        {"data": d(0), "godzinaOd": d(0, "08:55:00"), "godzinaDo": d(0, "09:40:00"), "przedmiot": "Biologia",
         "sala": "3", "prowadzacy": "Nowak Anna", "adnotacja": 3, "idJednostkaSkladowa": 5,
         "zmiany": [{"informacjeNieobecnosc": "Wycieczka klasowa"}]},
        {"data": d(1), "godzinaOd": d(1, "10:00:00"), "godzinaDo": d(1, "10:45:00"), "przedmiot": "Fizyka",
         "sala": "", "prowadzacy": "", "adnotacja": 2, "idJednostkaSkladowa": 5},
        {"data": d(8), "godzinaOd": None, "godzinaDo": None, "przedmiot": "Bez godziny"},
    ],
    "DniWolne": [
        {"nazwa": "Dzień wolny dyrektora", "dataOd": d(2), "dataDo": d(2), "wszystkieSkladowe": False,
         "jednostkiSkladowe": [{"id": 5}]},
        {"nazwa": "Inna jednostka", "dataOd": d(9), "dataDo": d(9), "wszystkieSkladowe": False,
         "jednostkiSkladowe": [{"id": 99}]},
        {"nazwa": "Ferie", "dataOd": d(10), "dataDo": d(12), "wszystkieSkladowe": True, "jednostkiSkladowe": None},
    ],
    "SprawdzianyZadaniaDomowe": [
        {"id": 501, "typ": 1, "data": d(3), "przedmiotNazwa": "Matematyka", "nauczycielImieNazwisko": "Anna Nowak"},
        {"id": 502, "typ": 4, "data": d(1), "terminOdpowiedzi": d(2), "przedmiotNazwa": "Chemia",
         "nauczycielImieNazwisko": "Anna Nowak"},
    ],
    "SprawdzianSzczegoly": {"opis": "Dział 3: ułamki", "data": d(3)},
    "ZadanieDomoweSzczegoly": {"opis": "Zadania 1-5 str. 40", "terminOdpowiedzi": d(2)},
    "Uwagi": [
        {"id": 77, "data": d(-3), "tresc": "<p>Pochwała za pomoc</p>", "autor": "Anna Nowak",
         "kategoria": "Zachowanie", "liczbaPunktow": 5, "typ": 1},
        {"id": 78, "data": d(-6), "tresc": "Brak zadania", "autor": "Anna Nowak", "kategoria": "Obowiązki",
         "liczbaPunktow": -2},
    ],
    "Frekwencja": [
        {"data": d(-1), "godzinaOd": d(-1, "08:00:00"), "kategoriaFrekwencji": 2},
        {"data": d(-1), "godzinaOd": d(-1, "08:55:00"), "kategoriaFrekwencji": 1},
        {"data": d(-2), "godzinaOd": d(-2, "08:00:00"), "kategoriaFrekwencji": 4},
    ],
    "Przedmioty": [{"id": -1, "nazwa": "Wszystkie"}, {"id": 1, "nazwa": "Matematyka"}, {"id": 2, "nazwa": "Chemia"}],
    "FrekwencjaStatystyki?idPrzedmiot=-1": {"podsumowanie": 93.5, "statystyki": [
        {"kategoriaFrekwencji": 1, "razem": 40, "miesiace": [{"miesiac": 10, "wartosc": 20}, {"miesiac": 11, "wartosc": 20}]},
        {"kategoriaFrekwencji": 2, "razem": 3, "miesiace": [{"miesiac": 11, "wartosc": 3}]}]},
    "FrekwencjaStatystyki?idPrzedmiot=1": {"podsumowanie": 90.0, "statystyki": [
        {"kategoriaFrekwencji": 1, "razem": 9, "miesiace": [{"miesiac": 11, "wartosc": 9}]}]},
    "FrekwencjaStatystyki?idPrzedmiot=2": (500, {}),
    "Osiagniecia": [{"id": 1, "tresc": "I miejsce w konkursie matematycznym"}, "nie-obiekt"],
    "SzczesliwyNumerTablica": {"id": 3, "numer": 13},
    "Zebrania": [
        {"id": 31, "dataCzas": d(5, "17:00:00"), "sala": "s. 12", "opis": "Wywiadówka", "zebranieOnline": None},
        {"id": 32, "dataCzas": d(-20, "17:30:00"), "sala": "", "opis": "Zebranie online",
         "zebranieOnline": "https://example.com/spotkanie"},
    ],
}

PRESCHOOL_API = {
    "PlanZajec": [
        {"data": d(0), "godzinaOd": d(0, "08:00:00"), "godzinaDo": d(0, "12:00:00"), "przedmiot": "Zajęcia dydaktyczne",
         "prowadzacy": "Nowak Anna"},
        {"data": d(1), "godzinaOd": d(1, "08:00:00"), "godzinaDo": d(1, "09:00:00"), "przedmiot": "Rytmika",
         "prowadzacy": ""},
    ],
    "Jadlospis": [
        {"id": 1, "data": d(0), "dieta": "Przedszkole - podstawowa", "posilki": [
            {"nazwa": "Śniadanie", "sklad": ["bułka", "masło"], "alergeny": ["gluten", "mleko"]},
            {"nazwa": "Obiad", "sklad": ["zupa pomidorowa"], "alergeny": []}]},
        {"id": 2, "data": d(0), "dieta": "Szkoła", "posilki": [{"nazwa": "Obiad szkolny", "sklad": [], "alergeny": []}]},
        {"id": 3, "data": d(1), "dieta": "przedszkolna", "posilki": [{"nazwa": "Podwieczorek", "sklad": ["jabłko"]}]},
    ],
    "Zebrania": [{"id": 41, "dataCzas": d(6, "16:30:00"), "sala": "sala 2", "temat": "Zebranie z rodzicami",
                  "agenda": "Wycieczka", "zebranieOnline": None}],
    "Oplaty": {"kontaBankowe": [
        {"platnoscGlobalKey": "p-1", "numerKonta": "00 0000 0000 0000 0000 0000 0000", "tytulPlatnosci": "Wyżywienie",
         "kwotaDoZaplaty": 120.5, "aktywne": True, "statusPlatnosci": 1,
         "informacje": {"dataSynchronizacji": d(-1, "06:00:00"), "szczegoly": [
             {"nazwa": "Kwota kosztów upomnień", "wartosc": 0}, {"nazwa": "Kwota odsetek", "wartosc": 1.2}]}},
        {"platnoscGlobalKey": "p-2", "numerKonta": "", "tytulPlatnosci": "Rada rodziców", "kwotaDoZaplaty": 0,
         "aktywne": False, "statusPlatnosci": 2, "informacje": None}]},
    "Informacje": {"nazwa": "Przedszkole nr 1", "dyrektor": "Anna Nowak", "miejscowosc": "Warszawa",
                   "ulica": "Przykładowa", "nrDomu": "1", "nrMieszkania": "", "kodPocztowy": "00-001",
                   "telSluzbowy": "22 000 00 00", "telKomorkowy": "", "telDomowy": "", "mail": "test@example.com",
                   "stronaWwwUrl": "https://example.com"},
    "Nauczyciele": {"nauczyciele": [
        {"imie": "Anna", "nazwisko": "Nowak", "przedmiot": "Wychowanie przedszkolne", "wychowawca": True,
         "globalKeySkrzynka": "box-n1"},
        {"imie": "Jan", "nazwisko": "Nowak", "przedmiot": "Rytmika", "wychowawca": False}]},
    "EwidencjaObecnosciTablica": [
        {"data": d(-2), "obecnosc": True}, {"data": d(-1), "obecnosc": False}, {"data": d(0), "obecnosc": True}],
    "EwidencjaObecnosci": [
        {"data": d(-2), "godzinaOd": d(-2, "07:45:00"), "godzinaDo": d(-2, "15:10:00")},
        {"data": d(0), "godzinaOd": d(0, "08:05:00"), "godzinaDo": None}],
}

MESSAGES_API = {
    "OdebraneSkrzynka?globalKeySkrzynka=box-1": [
        {"apiGlobalKey": "m-1", "data": d(-1, "10:00:00"), "korespondenci": "Anna Nowak", "temat": "Wycieczka",
         "przeczytana": False},
        {"apiGlobalKey": "m-2", "data": d(-4, "12:00:00"), "korespondenci": "Sekretariat", "temat": "Zebranie",
         "przeczytana": True}],
    "OdebraneSkrzynka?globalKeySkrzynka=box-2": [
        {"apiGlobalKey": "p-m-1", "data": d(-2, "09:00:00"), "korespondenci": "Anna Nowak", "temat": "Strój na bal",
         "przeczytana": False}],
    "WiadomoscSzczegoly?apiGlobalKey=m-1": {"tresc": "<p>Wycieczka w <b>piątek</b>.</p>"},
    "WiadomoscSzczegoly?apiGlobalKey=m-2": {"tresc": "Zebranie o 17:00"},
    "WiadomoscSzczegoly?apiGlobalKey=p-m-1": {"tresc": "Proszę przynieść strój."},
}

CALENDAR = [
    {"summary": "Jan: Basen", "start": {"dateTime": d(1, "15:00:00+01:00")}, "end": {"dateTime": d(1, "16:00:00+01:00")},
     "location": "Pływalnia", "description": "Czepek", "uid": "ev-1"},
    {"summary": "Ola: Rytmika", "start": {"dateTime": d(1, "15:00:00+01:00")}, "end": {"dateTime": d(1, "16:00:00+01:00")},
     "uid": "ev-2"},
]


def _route(api: dict, url: str, params: dict | None):
    endpoint = url.rsplit("/api/", 1)[-1]
    for k, val in (params or {}).items():
        hit = api.get(f"{endpoint}?{k}={val}")
        if hit is not None:
            return hit
    return api.get(endpoint, (404, {}))


def _response(hit, request: httpx.Request) -> httpx.Response:
    status, data = hit if isinstance(hit, tuple) else (200, hit)
    return httpx.Response(status, json=data, request=request)


def _transport(api: dict) -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: _response(_route(api, str(r.url).split("?")[0], dict(r.url.params)), r))


class _Ha:
    """Atrapa HA dla fetcherów: tylko kalendarz z własnymi zajęciami."""

    async def get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        if "/calendars/" in url:
            return httpx.Response(200, json=CALENDAR, request=req)
        return httpx.Response(404, json={}, request=req)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _dump_db(path: str) -> dict:
    # iterdump: pełny zrzut bez składania zapytań; wiersze sortowane, bo kolejność
    # wstawiania nie jest częścią zachowania.
    conn = sqlite3.connect(path)
    try:
        out: dict[str, list[str]] = {}
        for line in conn.iterdump():
            m = re.match(r'INSERT INTO "(\w+)" VALUES', line)
            if m and m.group(1) != "ha_cache":
                out.setdefault(m.group(1), []).append(line[m.end():])
        return {t: sorted(rows) for t, rows in sorted(out.items())}
    finally:
        conn.close()


def _run_all(v, monkeypatch, published) -> dict:
    monkeypatch.setattr(v, "datetime", _FixedDatetime)
    sync_sent = {}
    monkeypatch.setattr(v, "publish_sensor_sync",
                        lambda e, st, n, a=None: sync_sent.update({e: [st, n, a or {}]}))

    async def school_and_preschool():
        ha = _Ha()
        async with httpx.AsyncClient(transport=_transport(SCHOOL_API)) as c:
            for fetch in (v._fetch_grades, v._fetch_timetable, v._fetch_remarks, v._fetch_frequency,
                          v._fetch_achievements, v._fetch_lucky_number, v._fetch_meetings):
                await fetch(c, ha, BASE, STUDENT)
            await v._fetch_schedule(c, ha, BASE, STUDENT, set())
        async with httpx.AsyncClient(transport=_transport(PRESCHOOL_API)) as c:
            for fetch in (v._fetch_przedszkole_plan, v._fetch_przedszkole_jadlospis, v._fetch_przedszkole_zebrania,
                          v._fetch_przedszkole_oplaty, v._fetch_przedszkole_informacje,
                          v._fetch_przedszkole_nauczyciele, v._fetch_przedszkole_obecnosc):
                await fetch(c, ha, BASE, KID)

    asyncio.run(school_and_preschool())
    monkeypatch.setattr(v, "_build_wiadomosci_session",
                        lambda *a: httpx.Client(transport=_transport(MESSAGES_API)))
    v._fetch_przedszkole_wiadomosci(KID)
    v.run_messages_sync([STUDENT], lambda: httpx.Client(transport=_transport(MESSAGES_API)))

    return {
        "sensors": {e: [st, attrs] for e, (st, attrs) in sorted(published.items())},
        "sensors_sync": dict(sorted(sync_sent.items())),
        "db": _dump_db(v.DB_PATH),
    }


def test_golden_master_edu(v, db, published, monkeypatch):
    result = json.loads(json.dumps(_run_all(v, monkeypatch, published), ensure_ascii=False, default=str))
    if os.environ.get("GOLDEN_UPDATE") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("GOLDEN_UPDATE=1: zapisano nowy wzorzec")
    assert GOLDEN.exists(), "Brak wzorca: GOLDEN_UPDATE=1 python -m pytest -q tests/test_golden_master.py"
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for part in ("sensors", "sensors_sync", "db"):
        for name in sorted(set(expected[part]) | set(result[part])):
            assert result[part].get(name) == expected[part].get(name), f"Inny wynik: {part} / {name}"


def test_golden_master_covers_every_section(v, db, published, monkeypatch):
    # Wzorzec z pustymi sekcjami niczego nie pilnuje: każdy fetcher musi coś zapisać.
    result = _run_all(v, monkeypatch, published)
    empty = [t for t in ("grades", "schedule", "free_days", "timetable", "remarks", "frequency", "frequency_stats",
                         "achievements", "lucky_number", "meetings", "messages", "przedszkole_plan",
                         "przedszkole_jadlospis", "przedszkole_zebrania", "przedszkole_oplaty",
                         "przedszkole_informacje", "przedszkole_nauczyciele", "przedszkole_obecnosc",
                         "przedszkole_wiadomosci") if not result["db"].get(t)]
    assert not empty, f"Puste tabele po przebiegu (dane testowe nie trafiają w kod): {empty}"
