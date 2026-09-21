"""Contract tests use public Wulkanowy fixtures, never real student data."""
import copy
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "vultron"))
import gdansk

FIXTURES = Path(__file__).parent / "fixtures" / "gdansk"


def fixture(name):
    value = json.loads((FIXTURES / name).read_text())
    return value.get("data", value) if isinstance(value, dict) else value


def test_failed_login_does_not_expose_portal_text_and_cleans_up_browser(monkeypatch):
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    entered, clicked, cleaned = [], [], []
    field = SimpleNamespace(send_keys=entered.append)
    form = SimpleNamespace(
        get_attribute=lambda _: "https://logowanie.edu.gdansk.pl/Login",
        find_element=lambda *_: SimpleNamespace(click=lambda: clicked.append(True)),
    )
    password = SimpleNamespace(send_keys=entered.append, find_element=lambda *_: form)

    def find_element(by, value):
        assert (by, value) == (By.ID, "Password")  # Never read the page's body.
        return password

    driver = SimpleNamespace(
        get=lambda _: None, current_url="https://logowanie.edu.gdansk.pl/Login",
        find_element=find_element, service=object(), quit=lambda: cleaned.append("quit"),
    )
    calls = []

    def until(*_):
        calls.append(True)
        if len(calls) == 1:
            return field
        raise TimeoutException()

    monkeypatch.setattr(WebDriverWait, "until", until)
    config = {"username": "synthetic-account", "password": "synthetic-value"}
    with pytest.raises(gdansk.AuthenticationError) as error:
        gdansk.authenticate(config, lambda: driver, lambda service: cleaned.append(service))
    assert entered == list(config.values())
    assert clicked == [True]
    assert cleaned == ["quit", driver.service]
    assert all(value not in str(error.value) for value in config.values())


@pytest.mark.parametrize("url", [
    "http://uonetplus-uczen.edu.gdansk.pl/gdansk/zsp2/App",
    "https://uonetplus-uczen.edu.gdansk.pl.evil.example/gdansk/App",
    "https://uonetplus-uczen.edu.gdansk.pl@evil.example/gdansk/App",
    "https://user:password@uonetplus-uczen.edu.gdansk.pl/gdansk/App",
    "https://uonetplus-uczen.edu.gdansk.pl:8443/gdansk/App",
    "https://uczen.eduvulcan.pl/gdansk/App",
    "https://uonetplus-uczen.edu.gdansk.pl/other/App",
])
def test_session_headers_never_go_to_unexpected_origin(url):
    with pytest.raises(gdansk.GdanskError):
        gdansk.checked_url(url, gdansk.STUDENT_HOST)


def test_module_headers_parse_both_javascript_object_and_assignment():
    value = gdansk.module_headers("antiForgeryToken: 'a-token', appGuid = 'a-guid'; version:'26.06.0007.66577'")
    assert value["X-V-AppVersion"] == "26.06.0007.66577"
    assert value["X-V-RequestVerificationToken"] == "a-token"
    with pytest.raises(gdansk.AuthenticationError):
        gdansk.module_headers("<form>Sign in again</form>")


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://evil.example/"}),
    httpx.Response(401), httpx.Response(403),
])
def test_expired_session_is_not_an_empty_diary(response):
    with pytest.raises(gdansk.AuthenticationError):
        gdansk.response_json(response)


@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>Login</html>"),
                                           httpx.Response(200, json={"success": False, "data": []}),
                                           httpx.Response(500)])
def test_server_failure_is_not_published_as_zero(response):
    with pytest.raises(gdansk.GdanskError):
        gdansk.response_json(response)


def test_read_client_rejects_write_endpoints():
    with pytest.raises(gdansk.GdanskError):
        gdansk.read_api(None, None, "Usprawiedliwienia")


def test_current_school_year_and_explicit_student_selection():
    rows = fixture("UczenDziennik.json")
    old = copy.deepcopy(rows[0])
    old["DziennikRokSzkolny"] = 2015
    rows.append(old)
    student = gdansk.current_diaries(rows, date(2016, 10, 3), "Jan Kowalski")
    assert len(student) == 1
    assert student[0]["slug"] == "jan_kowalski"
    assert gdansk.current_diaries(rows, date(2016, 10, 3), "Inny Uczeń") == []
    next_year = gdansk.current_diaries(rows, date(2017, 9, 1))
    assert next_year and all(x["DziennikRokSzkolny"] == 2017 for x in next_year)


def test_student_switch_replaces_old_context_cookies():
    client = httpx.Client()
    client.cookies.set("idBiezacyUczen", "999", domain=".edu.gdansk.pl", path="/gdansk")
    client.cookies.set("idBiezacyUczen", "777", domain=gdansk.STUDENT_HOST, path="/")
    diary = fixture("UczenDziennik.json")[0]
    gdansk.select_diary(client, None, diary)
    assert client.cookies.get("idBiezacyUczen") == str(diary["IdUczen"])
    client.close()


def test_grade_text_and_full_description_survive_without_invented_average():
    diary = gdansk.current_diaries(fixture("UczenDziennik.json"), date(2016, 10, 3))[0]
    data = fixture("Oceny.json")
    data["Oceny"][0]["OcenyCzastkowe"][0]["Wpis"] = "6p"
    description = "Na sprawdzian:\n" + "Pełny zakres materiału\n" * 200
    data["Oceny"][0]["OcenyCzastkowe"][0]["NazwaKolumny"] = description
    reading = gdansk.grades(data, diary["Okresy"][0], diary, date(2016, 10, 3))
    first = reading.attrs["lista_przedmiotow"][0]
    assert first["oceny"][0]["w"] == "6p"
    assert first["oceny"][0]["i"] == description.strip()
    assert first["srednia"] is None
    assert reading.attrs["active_period"] is True
    assert reading.state == sum(len(s["oceny"]) for s in reading.attrs["lista_przedmiotow"])


def test_timetable_dates_cancellations_substitutions_and_free_days():
    lessons, free = gdansk.timetable(fixture("PlanLekcji.json"))
    assert any(x["p"] == "Matematyka" and x["d"] == "2018-09-24" and x["g"] == "07:10-07:55" for x in lessons)
    assert any(x["st"] == "ODWOL" for x in lessons)
    assert any(x["st"] == "ZAST" and x["p"] == "Geografia" and x["s"] == "23" and x["n"] == "Światowy Michał" for x in lessons)
    assert free[0]["d"] == "2018-09-27"
    assert "przerwa" in free[0]["n"]


def test_gpe_object_cells_preserve_timetable_and_tooltips():
    old = fixture("PlanLekcji.json")
    modern = copy.deepcopy(old)
    modern["Rows"] = [{f"field{i + 1}": {"Description": cell, "Tooltip": ""}
                       for i, cell in enumerate(row)} for row in old["Rows"]]
    assert gdansk.timetable(modern) == gdansk.timetable(old)
    modern["Rows"][0]["field2"]["Tooltip"] = "<p>Pełna informacja</p><p>Drugi akapit</p>"
    lessons, _ = gdansk.timetable(modern)
    lesson = next(x for x in lessons if x["p"] == "Matematyka" and x["d"] == "2018-09-24")
    assert "Pełna informacja\nDrugi akapit" in lesson["opis"]
    assert any(x["st"] == "WLASNE" for x in lessons)


def test_attendance_categories_and_lesson_times_are_preserved():
    rows = gdansk.attendance(fixture("Frekwencja.json"), fixture("UczenCache.json"))
    assert rows[0]["d"] == "2018-10-02"
    assert rows[0]["t"] == "08:00"
    assert rows[0]["k"] == 1


def test_both_attendance_statistics_contracts_render_on_vultron_card():
    pct, rows = gdansk.statistics(fixture("FrekwencjaStatystyki.json"))
    assert pct == 76.19
    assert set(rows[0]) == {"k", "m", "s1", "s2", "r"}
    pct, rows = gdansk.statistics({"Podsumowanie": 90, "Statystyki": [
        {"NazwaTypuFrekwencji": "Obecność", "Wrzesien": 9, "Razem": 9}
    ]})
    assert pct == 90
    assert rows[0]["m"]["9"] == 9
    assert rows[0]["s1"] is None


def test_assignments_preserve_multiline_description():
    homework = fixture("Homework.json")
    result = gdansk.assignments(fixture("Sprawdziany.json"), homework)
    assert any(x["typ"] == "Kartkówka" for x in result)
    task = next(x for x in result if x["id"] == "homework_1445838")
    assert "str 231" in task["opis"] and "str 254" in task["opis"]
    assert "\n" in task["opis"]


def test_full_snapshot_contract_and_partial_failure_preserve_other_sections(monkeypatch):
    requests = []
    mapping = {"UczenDziennik": "UczenDziennik.json", "UczenCache": "UczenCache.json",
               "Oceny": "Oceny.json", "PlanZajec": "PlanLekcji.json", "Frekwencja": "Frekwencja.json",
               "FrekwencjaStatystyki": "FrekwencjaStatystyki.json", "Sprawdziany": "Sprawdziany.json",
               "Homework": "Homework.json", "UwagiIOsiagniecia": "UwagiIOsiagniecia.json"}

    def handler(request):
        requests.append(request)
        assert request.method == "POST" and request.url.path.endswith(".mvc/Get")
        endpoint = request.url.path.split("/")[-2].removesuffix(".mvc")
        body = json.loads(request.content)
        if endpoint == "Homework":
            assert body["statusFilter"] == -1
        if endpoint == "Zebrania":
            return httpx.Response(503)
        return httpx.Response(200, json={"success": True, "data": fixture(mapping[endpoint])})

    monkeypatch.setattr(gdansk, "client_for", lambda _: httpx.Client(transport=httpx.MockTransport(handler)))
    module = gdansk.Module("https://uonetplus-uczen.edu.gdansk.pl/gdansk/test/", {}, [])
    readings, errors = gdansk.snapshot([module], None, {}, today=date(2016, 10, 3))
    entities = {r.entity: r for r in readings}
    assert "sensor.vultron_oceny_jan_kowalski_p1" in entities
    assert "sensor.vultron_plan_jan_kowalski_next" in entities
    assert "sensor.vultron_zebrania_jan_kowalski" not in entities
    assert any(e.startswith("Zebrania:") for e in errors)
    assert any(e.startswith("Wiadomości:") for e in errors)
    assert len(requests) >= 20


@pytest.mark.parametrize("mailbox_name", ["Jan Kowalski – R", "Opiekun Testowy Adam, Kowalski Jan (SP1)"])
def test_messages_only_use_get_and_never_mark_as_read(mailbox_name):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("Skrzynki"):
            return httpx.Response(200, json=[{"globalKey": "fake-key", "nazwa": mailbox_name}])
        assert request.url.path.endswith("OdebraneSkrzynka")
        return httpx.Response(200, json=[{"id": 123, "data": "2026-09-20T12:00:00", "przeczytana": False,
                                         "temat": "Wycieczka", "korespondenci": "Nauczyciel"}])
    module = gdansk.Module("https://uonetplus-wiadomosciplus.edu.gdansk.pl/gdansk/", {}, [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        readings = gdansk.message_readings(client, module, [{"name": "Jan Kowalski", "slug": "jan_kowalski"}])
    assert readings[0].state == 1
    assert readings[0].attrs["wiadomosci"][0]["przeczytana"] is False
    assert readings[0].attrs["wiadomosci"][0]["url"].endswith("/gdansk/App/odebrane")
    assert len(calls) == 2


def test_ambiguous_mailbox_is_not_assigned_to_wrong_child():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=[
        {"globalKey": "1", "nazwa": "Jan Kowalski – R"}, {"globalKey": "2", "nazwa": "Jan Kowalski – U"}]))
    module = gdansk.Module("https://uonetplus-wiadomosciplus.edu.gdansk.pl/gdansk/", {}, [])
    with httpx.Client(transport=transport) as client, pytest.raises(gdansk.GdanskError):
        gdansk.message_readings(client, module, [{"name": "Jan Kowalski", "slug": "jan_kowalski"}])


def test_large_mailbox_preserves_order_titles_and_unread_total_across_pages():
    messages = [{"temat": f"Wiadomość {i}: " + "Pełny tytuł łóżź " * 90,
                 "przeczytana": i % 3 == 0} for i in range(71)]
    readings = gdansk.partition_messages(messages, {"name": "Jan Kowalski", "slug": "jan_kowalski"})
    root = readings[-1]
    entities = {r.entity: r for r in readings}
    assert root.state == sum(not m["przeczytana"] for m in messages)
    assert root.attrs["total"] == len(messages)
    assert root.attrs["page_entities"]
    restored = root.attrs["wiadomosci"] + [m for e in root.attrs["page_entities"]
                                          for m in entities[e].attrs["wiadomosci"]]
    assert restored == messages
    assert all(len(json.dumps(r.attrs, ensure_ascii=False).encode("utf-8")) < 14000 for r in readings)


def test_single_oversize_message_fails_explicitly_without_truncation():
    with pytest.raises(gdansk.GdanskError):
        gdansk.partition_messages([{"temat": "ł" * 13000, "przeczytana": False}],
                                  {"name": "Jan Kowalski", "slug": "jan_kowalski"})
