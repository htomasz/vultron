"""Testy hebe_web.py: dane z API mobilnego w kształcie odpowiedzi stron WWW.

Kształty pól jak na prawdziwym koncie (faza 2: porównanie z danymi WWW).
Dane osobowe w testach są zmyślone."""
import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "vultron"))
import hebe
import hebe_web as hw

TODAY = date(2026, 11, 8)   # niedziela


@pytest.fixture(autouse=True)
def _fresh_year_cache():
    hw._year_lessons_cache.clear()
    yield
    hw._year_lessons_cache.clear()
REST = "https://lekcjaplus.vulcan.net.pl/warszawa/000123/api"
TEACHER = {"Id": 1, "Name": "Anna", "Surname": "Nowak", "DisplayName": "Anna Nowak"}


def _account(**over):
    a = {"Pupil": {"Id": 7, "FirstName": "Jan", "Surname": "Kowalski"},
         "Unit": {"Id": 3, "RestURL": REST}, "ConstituentUnit": {"Id": 5},
         "Periods": [{"Id": 10, "Number": 1, "Level": 2, "Current": False,
                      "Start": "2026-09-01", "End": "2027-01-24"},
                     {"Id": 11, "Number": 2, "Level": 2, "Current": True,
                      "Start": "2027-01-25", "End": "2027-06-25"},
                     {"Id": 5, "Number": 1, "Level": 1, "Current": False,
                      "Start": "2025-09-01", "End": "2026-01-25"}],
         "MessageBox": {"GlobalKey": "box-1"}, "Journal": {"Id": 1}, "State": 0}
    a.update(over)
    return a


# ── okresy i oceny ───────────────────────────────────────────────────────

def test_periods_web_only_current_level():
    assert hw.periods_web(_account()) == [{"id": 10, "numerOkresu": 1}, {"id": 11, "numerOkresu": 2}]
    assert hw.periods_web(_account(Periods=[{"Id": 1, "Number": 1, "Level": 1, "Current": False}])) == []


def _grade(gid, col_id, subject, content, modified, weight=2.0, category="Sprawdziany", col_name="Dział 1"):
    return {"Id": gid, "ContentRaw": content, "Content": content.split(" ")[0], "ModifiedAt": modified,
            "CreatedAt": "2026-09-01T08:00:00", "Column": {
                "Id": col_id, "PeriodId": 11, "Name": col_name, "Weight": weight,
                "Subject": {"Id": 1, "Name": subject}, "Category": {"Name": category}}}


def test_grades_web_shape():
    grades = [_grade(1, 100, "Matematyka", "5", "2026-10-03T10:00:00"),
              _grade(2, 100, "Matematyka", "4+", "2026-10-05T10:00:00"),
              _grade(3, 101, "Chemia", "47 (%)", "2026-10-07T10:00:00", category="Kartkówki", col_name="")]
    summaries = [{"Subject": {"Name": "Zachowanie"}, "Entry_1": "bardzo dobre", "Entry_2": None},
                 {"Subject": {"Name": "Matematyka"}, "Entry_1": "5", "Entry_2": None}]
    averages = [{"Subject": {"Name": "Matematyka"}, "Average": "4,50"}, {"Subject": {"Name": "Chemia"}, "Average": ""}]
    out = hw.grades_web(grades, summaries, averages)
    assert "ustawienia" not in out
    mat, chem, zach = out["ocenyPrzedmioty"]
    assert mat == {"przedmiotNazwa": "Matematyka", "proponowanaOcenaOkresowa": "5", "ocenaOkresowa": None,
                   "srednia": "4,50", "kolumnyOcenyCzastkowe": [{
                       "idKolumny": 100, "kategoriaKolumny": "Sprawdziany", "nazwaKolumny": "Dział 1",
                       "oceny": [{"wpis": "5", "dataOceny": "03.10.2026", "waga": 2.0, "idOcenaPoprawiona": None},
                                 {"wpis": "4+", "dataOceny": "05.10.2026", "waga": 2.0, "idOcenaPoprawiona": None}]}]}
    assert chem["kolumnyOcenyCzastkowe"][0]["oceny"][0]["wpis"] == "47 (%)" and "srednia" not in chem
    assert zach == {"przedmiotNazwa": "Zachowanie", "kolumnyOcenyCzastkowe": [],
                    "proponowanaOcenaOkresowa": "bardzo dobre", "ocenaOkresowa": None}


def test_grades_web_school_without_averages():
    out = hw.grades_web([_grade(1, 100, "Fizyka", "3", "2026-10-03T10:00:00", weight=0.0)], [], [])
    assert out["ustawienia"] == {"isSredniaAndPunkty": False}


# ── plan, dni wolne ──────────────────────────────────────────────────────

def _lesson(**over):
    raw = {"Id": 5, "DateAt": "2026-11-09", "TimeSlot": {"Start": "08:00", "End": "08:45"},
           "Subject": {"Name": "Matematyka"}, "Room": {"Code": "12"}, "TeacherPrimary": TEACHER,
           "Substitution": None}
    raw.update(over)
    return raw


def test_lesson_web():
    sub = {"Subject": {"Name": "Fizyka"}, "TeacherPrimary": {"Name": "Jan", "Surname": "Nowak"}, "Room": {"Code": "7"}}
    assert hw.lesson_web(_lesson()) == {
        "data": "2026-11-09T00:00:00", "godzinaOd": "2026-11-09T08:00:00", "godzinaDo": "2026-11-09T08:45:00",
        "przedmiot": "Matematyka", "sala": "12", "prowadzacy": "Nowak Anna", "adnotacja": 0, "zmiany": []}
    zast = hw.lesson_web(_lesson(Substitution=sub))
    assert (zast["przedmiot"], zast["prowadzacy"], zast["sala"], zast["adnotacja"]) == ("Fizyka", "Nowak Jan", "7", 1)
    assert hw.lesson_web(_lesson(Substitution={"ClassAbsence": True}))["adnotacja"] == 3
    assert hw.lesson_web(_lesson(Substitution={"Subject": None, "TeacherPrimary": None}))["adnotacja"] == 3
    moved = hw.lesson_web(_lesson(Substitution={**sub, "ChangeDateAt": "2026-11-10",
                                                 "TimeSlot": {"Start": "09:00", "End": "09:45"}}))
    assert (moved["data"], moved["godzinaOd"], moved["adnotacja"]) == ("2026-11-10T00:00:00", "2026-11-10T09:00:00", 2)
    assert hw.lesson_web(_lesson(Subject=None, Event="Wycieczka"))["przedmiot"] == "Wycieczka"
    assert hw.lesson_web(_lesson(TimeSlot=None)) is None
    assert hw.lesson_web(_lesson(DateAt=None)) is None


def test_free_days_web():
    assert hw.free_days_web([{"Name": "Ferie", "From": "2027-01-26", "To": "2027-02-08"}]) == [
        {"wszystkieSkladowe": True, "jednostkiSkladowe": None, "dataOd": "2027-01-26", "dataDo": "2027-02-08",
         "nazwa": "Ferie"}]


# ── terminarz ────────────────────────────────────────────────────────────

def _exam(eid, day, subject="Biologia", type_id=2):
    return {"Id": eid, "TypeId": type_id, "DeadlineAt": day, "Subject": {"Name": subject},
            "Content": "Dział 3", "Creator": TEACHER}


def test_timetable_web_keeps_known_ids(monkeypatch):
    monkeypatch.setattr(hw, "_with_offset", lambda day: f"{day}T00:00:00+01:00")
    exams = [_exam(12345678, "2026-11-12"), _exam(12345679, "2026-11-12"), _exam(12345680, "2026-11-13", type_id=9)]
    homework = [{"Id": 87654321, "DateAt": "2026-11-09", "DeadlineAt": "2026-11-10", "Subject": {"Name": "Fizyka"},
                 "Content": "Zad. 1", "Creator": None}]
    known = {("2026-11-12", "Biologia", 2): ["1234567"], ("2026-11-10", "Fizyka", 4): ["7654321"]}
    out = hw.timetable_web(exams, homework, known)
    assert [i["id"] for i in out] == ["1234567", "12345679", "12345680", "7654321"]
    assert known == {("2026-11-12", "Biologia", 2): ["1234567"], ("2026-11-10", "Fizyka", 4): ["7654321"]}
    assert out[0] == {"id": "1234567", "typ": 2, "data": "2026-11-12T00:00:00+01:00",
                      "terminOdpowiedzi": "2026-11-12T00:00:00+01:00", "opis": "Dział 3",
                      "przedmiotNazwa": "Biologia", "nauczycielImieNazwisko": "Anna Nowak"}
    assert out[2]["typ"] == 1                                    # nieznany typ sprawdzianu
    assert (out[3]["typ"], out[3]["data"][:10], out[3]["terminOdpowiedzi"][:10]) == (4, "2026-11-09", "2026-11-10")


def test_with_offset_is_local_midnight():
    value = hw._with_offset("2026-11-10")
    assert value.startswith("2026-11-10T00:00:00") and value[19] in "+-Z"


# ── frekwencja ───────────────────────────────────────────────────────────

def _att(day, start, cat, subject_id=1, removed=False):
    return {"Id": hash((day, start)) % 10**9, "DayAt": day, "TimeSlot": {"Start": start},
            "Subject": {"Id": subject_id, "Name": f"P{subject_id}"},
            "PresenceType": {"CategoryId": cat, "Removed": removed}}


def test_attendance_web():
    lessons = [_att("2026-11-05", "08:00", 1), _att("2026-11-05", "08:55", 2), _att("2026-10-01", "08:00", 1),
               _att("2026-11-06", "08:00", 1, removed=True), {"DayAt": "2026-11-06", "TimeSlot": {"Start": "09:00"},
                                                            "PresenceType": None}]
    assert hw.attendance_web(lessons, date(2026, 10, 25), TODAY) == [
        {"data": "2026-11-05T00:00:00", "godzinaOd": "2026-11-05T08:00:00", "kategoriaFrekwencji": 1},
        {"data": "2026-11-05T00:00:00", "godzinaOd": "2026-11-05T08:55:00", "kategoriaFrekwencji": 2}]


def test_presence_percent():
    assert hw.presence_percent({1: 10, 2: 1, 3: 1, 4: 1, 7: 5}) == round(11 * 100 / 13, 2)
    assert hw.presence_percent({7: 3}) is None


def test_subjects_and_stats_web():
    lessons = [_att("2026-09-10", "08:00", 1), _att("2026-10-10", "08:00", 2), _att("2026-10-11", "08:00", 1, 2),
               _att("2027-02-01", "08:00", 3)]
    assert hw.subjects_web(lessons) == [{"id": -1, "nazwa": "Wszystkie"}, {"id": 1, "nazwa": "P1"},
                                        {"id": 2, "nazwa": "P2"}]
    periods = [(1, date(2026, 9, 1), date(2027, 1, 24)), (2, date(2027, 1, 25), date(2027, 6, 25))]
    all_stats = hw.stats_web(lessons, periods)
    assert all_stats["podsumowanie"] == 50.0
    assert all_stats["statystyki"][0] == {"kategoriaFrekwencji": 1, "miesiace": [{"miesiac": 9, "wartosc": 1},
                                                                                 {"miesiac": 10, "wartosc": 1}],
                                          "okresy": [2, 0], "razem": 2}
    assert hw.stats_web(lessons, periods, 2) == {"podsumowanie": 100.0, "statystyki": [
        {"kategoriaFrekwencji": 1, "miesiace": [{"miesiac": 10, "wartosc": 1}], "okresy": [1, 0], "razem": 1}]}
    assert hw.stats_web([], periods, 9) == {"podsumowanie": None, "statystyki": []}


# ── numerek, zebrania, wiadomości ────────────────────────────────────────

def test_lucky_meetings_inbox():
    assert hw.lucky_web({"Number": 13}) == {"numer": 13, "id": ""}
    assert hw.lucky_web(None) == {"numer": None, "id": ""}
    assert hw.lucky_web({"Number": 0}) == {"numer": None, "id": ""}
    assert hw.meetings_web([{"Id": 3, "DateAt": "2026-11-20 17:00:00", "Where": "s. 12", "Why": "Wywiadówka",
                             "Agenda": "Oceny", "Online": None}]) == [
        {"id": 3, "dataCzas": "2026-11-20T17:00:00", "sala": "s. 12", "opis": "Wywiadówka", "zebranieOnline": ""}]
    msgs = [{"Id": "a", "SentAt": "2026-11-01T10:15:00.1234567+01:00", "ReadAt": None, "Subject": "T1",
             "Sender": {"Name": "Nowak Anna [AN] - nauczyciel"}, "Content": "<p>x</p>"},
            {"Id": "b", "SentAt": "2026-11-02T08:00:00", "ReadAt": "2026-11-02T09:00:00", "Subject": "T2",
             "Sender": None, "Content": ""},
            {"Id": "c", "SentAt": "2026-11-03T08:00:00", "Withdrawn": True},
            {"Id": None, "SentAt": "2026-11-04T08:00:00"}]
    inbox = hw.inbox_web(msgs)
    assert [m["apiGlobalKey"] for m in inbox] == ["b", "a"]
    assert inbox[1] == {"apiGlobalKey": "a", "przeczytana": False, "data": "2026-11-01T10:15:00",
                        "korespondenci": "Nowak Anna [AN] - nauczyciel", "temat": "T1", "_tresc": "<p>x</p>"}
    many = [{"Id": str(i), "SentAt": f"2026-10-{i % 28 + 1:02d}T08:00:00"} for i in range(60)]
    assert len(hw.inbox_web(many)) == 50


# ── most ─────────────────────────────────────────────────────────────────

class FakeHebe:
    """Atrapa hebe.HebeClient: endpoint -> rekordy; zapisuje wywołania."""

    def __init__(self, data, fail=(), missing=()):
        self.data, self.fail, self.missing, self.calls = data, set(fail), set(missing), []

    def get_all(self, rest_url, endpoint, pupil_id, *, last_sync=hebe.SYNC_FROM_START, **query):
        self.calls.append((endpoint, query))
        if endpoint in self.missing:
            raise hebe.HebeError(f"GET {endpoint}: HTTP 404", http_status=404)
        if endpoint in self.fail:
            raise hebe.HebeError(endpoint, 500)
        return self.data.get(endpoint, [])

    def call(self, method, rest_url, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs.get("query")))
        if endpoint in self.fail:
            raise hebe.HebeError(endpoint, 500)
        return self.data.get(endpoint)


def _bridge(data, fail=(), known=None, missing=()):
    account = _account()
    return hw.PupilBridge(FakeHebe(data, fail, missing), hebe.pupil_from_account(account), account, TODAY, known)


def test_bridge_routes_and_caches():
    b = _bridge({"mobile/note/byPupil": [{"Id": 1, "Content": "x"}],
                 "mobile/lesson/byPupil": [_att("2026-11-05", "08:00", 1)]})
    res = b.respond("https://uczen.eduvulcan.pl/warszawa/api/Uwagi", {"key": "k"})
    assert res.status_code == 200 and res.json()[0]["id"] == 1
    b.respond("/x/api/Przedmioty", {})
    b.respond("/x/api/FrekwencjaStatystyki", {"idPrzedmiot": "1"})
    b.respond("/x/api/Frekwencja", {"dataOd": "2026-10-25T00:00:00.000Z", "dataDo": "2026-11-08T23:59:59.999Z"})
    lesson_calls = [c for c in b.client.calls if c[0] == "mobile/lesson/byPupil"]
    assert len(lesson_calls) == 2                       # rok lekcji raz + okno frekwencji osobno
    assert lesson_calls[0][1]["dateFrom"] == date(2026, 9, 1)       # od początku pierwszego okresu
    assert lesson_calls[1][1]["dateFrom"] == date(2026, 10, 25)
    assert b.respond("/x/api/Osiagniecia", {}).status_code == 404   # brak w API mobilnym


def test_bridge_api_error_is_503_and_logged(caplog):
    b = _bridge({}, fail={"mobile/note/byPupil"})
    with caplog.at_level("WARNING", logger="Vultron"):
        assert b.respond("/x/api/Uwagi", {}).status_code == 503
    assert "[MOBILE] Uwagi: mobile/note/byPupil" in caplog.text


def test_async_client_falls_back_to_web_when_mobile_fails():
    class Web:
        async def get(self, url, params=None, **kwargs):
            return hw.Response(200, ["z WWW"])
    b = _bridge({}, fail={"mobile/school/vacation"})
    assert asyncio.run(hw.AsyncBridgeClient(b, web=Web()).get("https://u/x/api/DniWolne")).json() == ["z WWW"]
    assert asyncio.run(hw.AsyncBridgeClient(b).get("https://u/x/api/DniWolne")).status_code == 503


def test_bridge_grades_per_period_and_missing_averages():
    b = _bridge({"mobile/grade/byPupil": [_grade(1, 100, "Fizyka", "3", "2026-10-03T10:00:00")]},
                fail={"mobile/grade/average/byPupil"})
    out = b.respond("/x/api/Oceny", {"idOkresKlasyfikacyjny": "10"}).json()
    assert out["ocenyPrzedmioty"][0]["przedmiotNazwa"] == "Fizyka"
    periods = {c[1]["periodId"] for c in b.client.calls if c[0] == "mobile/grade/byPupil"}
    assert periods == {10}


def test_bridge_timetable_details_and_plan_window(monkeypatch):
    monkeypatch.setattr(hw, "_with_offset", lambda day: f"{day}T00:00:00+01:00")
    b = _bridge({"mobile/exam/byPupil": [_exam(12345678, "2026-11-12")],
                 "mobile/schedule/withchanges/byPupil": [_lesson()]})
    items = b.respond("/x/api/SprawdzianyZadaniaDomowe", {"dataOd": "2026-10-31T00:00:00.000Z"}).json()
    detail = b.respond("/x/api/SprawdzianSzczegoly", {"id": items[0]["id"]})
    assert detail.status_code == 200 and detail.json() == items[0]
    assert b.respond("/x/api/ZadanieDomoweSzczegoly", {"id": "brak"}).status_code == 503
    b.respond("/x/api/PlanZajec", {"dataOd": "2026-10-26T00:00:00.000Z", "dataDo": "2026-11-29T23:59:59.999Z"})
    plan = [c for c in b.client.calls if c[0] == "mobile/schedule/withchanges/byPupil"][0][1]
    assert (plan["dateFrom"], plan["dateTo"]) == (date(2026, 10, 26), date(2026, 11, 29))


def test_async_client_falls_back_to_web_for_unknown_paths():
    class Web:
        async def get(self, url, params=None, **kwargs):
            return hw.Response(200, {"web": url})
    b = _bridge({"mobile/note/byPupil": []})
    with_web = hw.AsyncBridgeClient(b, web=Web())
    assert asyncio.run(with_web.get("https://u/x/api/Osiagniecia", params={"key": "k"})).json() == {
        "web": "https://u/x/api/Osiagniecia"}
    assert asyncio.run(with_web.get("https://u/x/api/Uwagi")).json() == []
    assert asyncio.run(hw.AsyncBridgeClient(b).get("https://u/x/api/Osiagniecia")).status_code == 404


def test_messages_session():
    msgs = [{"Id": "m-1", "SentAt": "2026-11-01T10:00:00", "ReadAt": None, "Subject": "T",
             "Sender": {"Name": "N"}, "Content": "treść"}]
    b = _bridge({"mobile/messages/received/byBox": msgs})
    session = hw.MessagesBridgeSession({"box-1": b})
    inbox = session.get("https://wiadomosci.eduvulcan.pl/warszawa/api/OdebraneSkrzynka",
                        params={"globalKeySkrzynka": "box-1"})
    assert inbox.json() == [{"apiGlobalKey": "m-1", "przeczytana": False, "data": "2026-11-01T10:00:00",
                             "korespondenci": "N", "temat": "T"}]
    detail = session.get("https://wiadomosci.eduvulcan.pl/warszawa/api/WiadomoscSzczegoly",
                         params={"apiGlobalKey": "m-1"})
    assert detail.json() == {"tresc": "treść"}
    assert session.get("/x/api/OdebraneSkrzynka", params={"globalKeySkrzynka": "obca"}).status_code == 404
    assert session.get("/x/api/WiadomoscSzczegoly", params={"apiGlobalKey": "brak"}).status_code == 404
    session.close()


def test_bridge_without_box_has_no_inbox():
    account = _account(MessageBox=None)
    b = hw.PupilBridge(FakeHebe({}), hebe.pupil_from_account(account), account, TODAY)
    assert b.respond("/x/api/OdebraneSkrzynka", {}).status_code == 503


@pytest.mark.parametrize("params,expected", [
    ({"dataOd": "2026-10-26T00:00:00.000Z"}, date(2026, 10, 26)),
    ({"dataOd": "zle"}, TODAY), ({}, TODAY)])
def test_param_date(params, expected):
    assert hw._param_date(params, "dataOd", TODAY) == expected


def test_endpoint_missing_in_mobile_api(caplog):
    class Web:
        async def get(self, url, params=None, **kwargs):
            return hw.Response(200, ["z WWW"])
    b = _bridge({}, missing={"mobile/school/vacation"})
    with caplog.at_level("WARNING", logger="Vultron"):
        assert b.respond("/x/api/DniWolne", {}).status_code == 404       # nie 503, bez ostrzeżenia
    assert "[MOBILE]" not in caplog.text
    assert asyncio.run(hw.AsyncBridgeClient(b, web=Web()).get("https://u/x/api/DniWolne")).json() == ["z WWW"]
    no_web = asyncio.run(hw.AsyncBridgeClient(b).get("https://u/x/api/DniWolne"))
    assert (no_web.status_code, no_web.json()) == (200, [])                # bez stron WWW: pusto
    assert asyncio.run(hw.AsyncBridgeClient(b).get("https://u/x/api/Osiagniecia")).status_code == 404


def test_year_lessons_cached_between_cycles(monkeypatch):
    data = {"mobile/lesson/byPupil": [_att("2026-11-05", "08:00", 1)]}
    first = _bridge(data)
    first.respond("/x/api/Przedmioty", {})
    second = _bridge(data)                                  # następny cykl: nowy most
    second.respond("/x/api/FrekwencjaStatystyki", {})
    assert [c for c in second.client.calls if c[0] == "mobile/lesson/byPupil"] == []
    monkeypatch.setattr(hw.time, "monotonic", lambda: 10**9)  # po godzinie
    third = _bridge(data)
    third.respond("/x/api/Przedmioty", {})
    assert len([c for c in third.client.calls if c[0] == "mobile/lesson/byPupil"]) == 1
