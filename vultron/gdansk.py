"""Read-only GPE/UONET+ adapter, separate from the eduVULCAN API.

The MVC contracts are documented by wulkanowy/sdk's StudentService and
confirmed against GPE's 26.06.0007 frontend. No messages are marked as read.
Credentials and session cookies stay in the add-on; they are never logged.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

PORTAL = "https://uonetplus.edu.gdansk.pl/gdansk/"
STUDENT_HOST = "uonetplus-uczen.edu.gdansk.pl"
MESSAGES_HOST = "uonetplus-wiadomosciplus.edu.gdansk.pl"
LOGIN_HOST = "logowanie.edu.gdansk.pl"
READ_ENDPOINTS = frozenset({
    "UczenCache", "UczenDziennik", "Oceny", "PlanZajec", "Frekwencja",
    "FrekwencjaStatystyki", "Sprawdziany", "Homework", "UwagiIOsiagniecia",
    "Zebrania",
})


class GdanskError(Exception):
    """Safe to log: messages must not include server bodies or credentials."""


class AuthenticationError(GdanskError):
    pass


def checked_url(url: str, host: str, prefix: str = "/gdansk/") -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != host
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or not parsed.path.startswith(prefix)):
        raise GdanskError("Odrzucono nieoczekiwany adres serwera GPE")
    return url


def plain(value) -> str:
    """Keep paragraphs and full descriptions, dropping executable markup."""
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li"]):
        tag.append("\n")
    return soup.get_text().strip()


def slugify(value: str) -> str:
    value = value.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def iso_day(value) -> str:
    value = str(value or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
    if match:
        d, m, y = map(int, match.groups())
        return date(y, m, d).isoformat()
    return ""


def clock_time(value) -> str:
    match = re.search(r"(?:T|\s)(\d{2}:\d{2})", str(value or ""))
    return match.group(1) if match else ""


def module_headers(html: str) -> dict:
    headers = {}
    for name, header in (("antiForgeryToken", "X-V-RequestVerificationToken"),
                         ("appGuid", "X-V-AppGuid"), ("version", "X-V-AppVersion")):
        match = re.search(r"[\"']?" + name + r"[\"']?\s*[:=]\s*([\"'])(.*?)\1", html)
        if not match and name == "version":
            match = re.search(r"[\"']?appVersion[\"']?\s*[:=]\s*([\"'])(.*?)\1", html)
        if not match or not match.group(2):
            raise AuthenticationError("Brak nagłówków sesji GPE; ponów logowanie")
        headers[header] = match.group(2)
    return {**headers, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


@dataclass
class Module:
    base: str
    headers: dict
    cookies: list


@dataclass
class Reading:
    entity: str
    state: object
    name: str
    attrs: dict


def authenticate(config, driver_factory, cleanup) -> tuple[list[Module], Module | None]:
    """Use the normal login form. Stop rather than bypass an interactive check."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    driver = driver_factory()
    try:
        driver.get(PORTAL)
        wait = WebDriverWait(driver, 40)
        username = wait.until(EC.visibility_of_element_located((By.ID, "Username")))
        checked_url(driver.current_url, LOGIN_HOST, "/")
        password = driver.find_element(By.ID, "Password")
        form = password.find_element(By.XPATH, "ancestor::form")
        checked_url(urljoin(driver.current_url, form.get_attribute("action")), LOGIN_HOST, "/")
        username.send_keys(config.get("username", ""))
        password.send_keys(config.get("password", ""))
        form.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        selector = f'a[href^="https://{STUDENT_HOST}/gdansk/"]'
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
        except TimeoutException as exc:
            # Portal error pages can include personal data or session tokens.
            raise AuthenticationError(
                "Logowanie GPE nie powiodło się. Sprawdź konto w portalu Gdańska; "
                "dodatek nie ponawia hasła ani nie rozwiązuje CAPTCHA."
            ) from exc
        links = list(dict.fromkeys(e.get_attribute("href") for e in driver.find_elements(By.CSS_SELECTOR, selector)))
        message_links = driver.find_elements(By.CSS_SELECTOR, f'a[href^="https://{MESSAGES_HOST}/gdansk/"]')
        message_url = message_links[0].get_attribute("href") if message_links else None

        def capture(url, host):
            driver.get(checked_url(url, host))
            wait.until(lambda d: "antiForgeryToken" in d.page_source)
            checked_url(driver.current_url, host)
            path = urlsplit(driver.current_url).path
            # Messages redirects /App to /App/odebrane; API remains /gdansk/api.
            base = "https://" + host + path.split("/App", 1)[0].rstrip("/") + "/"
            return Module(base, module_headers(driver.page_source), driver.get_cookies())

        modules = [capture(link, STUDENT_HOST) for link in links]
        try:
            messages = capture(message_url, MESSAGES_HOST) if message_url else None
        except (GdanskError, TimeoutException):
            messages = None
        return modules, messages
    finally:
        try:
            driver.quit()
        finally:
            cleanup(driver.service)


def client_for(module: Module) -> httpx.Client:
    cookies = httpx.Cookies()
    expected_host = urlsplit(module.base).hostname
    for cookie in module.cookies:
        domain = cookie.get("domain", expected_host)
        if expected_host == domain.lstrip(".") or expected_host.endswith("." + domain.lstrip(".")):
            cookies.set(cookie["name"], cookie["value"], domain=domain, path=cookie.get("path", "/"))
    # Never follow an API redirect with session headers attached.
    return httpx.Client(cookies=cookies, headers={**module.headers, "Referer": module.base + "App"},
                        timeout=25, follow_redirects=False)


def response_json(response: httpx.Response):
    if response.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise AuthenticationError("Sesja GPE wygasła lub odrzucono uprawnienia")
    if response.status_code != 200:
        raise GdanskError(f"API GPE: HTTP {response.status_code}")
    try:
        value = response.json()
    except ValueError as exc:
        raise GdanskError("API GPE zwróciło stronę zamiast JSON") from exc
    if isinstance(value, dict) and "success" in value:
        if value["success"] is not True:
            raise GdanskError("API GPE zgłosiło niepowodzenie operacji odczytu")
        return value.get("data")
    return value


def read_api(client, module, endpoint, **body):
    if endpoint not in READ_ENDPOINTS:
        raise GdanskError("Operacja spoza listy dozwolonych odczytów")
    checked_url(module.base, STUDENT_HOST)
    return response_json(client.post(module.base + endpoint + ".mvc/Get", json=body))


def current_diaries(data, today: date, student_name="") -> list[dict]:
    if not isinstance(data, list):
        raise GdanskError("Niepoprawny format listy uczniów")
    year = today.year if today.month >= 9 else today.year - 1
    chosen = []
    for diary in data:
        if diary.get("DziennikRokSzkolny") != year or diary.get("IsArchiwalny"):
            continue
        name = f"{diary.get('UczenImie', '')} {diary.get('UczenNazwisko', '')}".strip()
        if student_name and name.casefold() != student_name.strip().casefold():
            continue
        if diary.get("IsPrzedszkola") or not diary.get("IsDziennik"):
            continue
        if not diary.get("IdUczen") or not diary.get("IdDziennik") or not diary.get("Okresy"):
            raise GdanskError("Niekompletny dziennik ucznia")
        chosen.append({**diary, "name": name, "slug": slugify(name)})
    return chosen


def select_diary(client, module, diary):
    for cookie in list(client.cookies.jar):
        if cookie.name in {"idBiezacyUczen", "idBiezacyDziennik", "biezacyRokSzkolny", "idBiezacyDziennikPrzedszkole"}:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    for key, value in {"idBiezacyUczen": diary["IdUczen"], "idBiezacyDziennik": diary["IdDziennik"],
                       "biezacyRokSzkolny": diary["DziennikRokSzkolny"], "idBiezacyDziennikPrzedszkole": 0}.items():
        client.cookies.set(key, str(value), domain=STUDENT_HOST, path="/")


def grades(data, period, diary, today) -> Reading:
    if not isinstance(data, dict) or not isinstance(data.get("Oceny"), list):
        raise GdanskError("Niepoprawny format ocen")
    subjects = []
    count = 0
    for subject in data["Oceny"]:
        items = []
        for grade in subject.get("OcenyCzastkowe") or []:
            day = iso_day(grade.get("DataOceny"))
            items.append({"w": str(grade.get("Wpis") or ""),
                          "d": datetime.fromisoformat(day).strftime("%d.%m") if day else "",
                          "data": day, "i": plain(grade.get("NazwaKolumny")),
                          "nauczyciel": grade.get("Nauczyciel") or "", "waga": grade.get("Waga")})
        count += len(items)
        subjects.append({"przedmiot": subject.get("Przedmiot", ""), "oceny": items,
                         # Respect the school's own policy. Point marks such as 6p
                         # are not converted into a made-up numerical average.
                         "srednia": subject.get("Srednia") if data.get("IsSrednia") else None,
                         "proponowana": plain(subject.get("ProponowanaOcenaRoczna")) or None,
                         "okresowa": plain(subject.get("OcenaRoczna")) or None})
    active = iso_day(period.get("DataOd")) <= today.isoformat() <= iso_day(period.get("DataDo"))
    return Reading(f"sensor.vultron_oceny_{diary['slug']}_p{period['NumerOkresu']}", count,
                   f"Oceny: {diary['name']} (P{period['NumerOkresu']})",
                   {"lista_przedmiotow": subjects, "period_number": period["NumerOkresu"],
                    "student_slug": diary["slug"], "active_period": active,
                    "oceny_opisowe": data.get("OcenyOpisowe") or [], "state_meaning": "liczba_ocen"})


def timetable(data) -> tuple[list, list]:
    if not isinstance(data, dict) or "Rows" not in data or "Headers" not in data:
        raise GdanskError("Niepoprawny format planu lekcji")
    days, free = [], []
    for header in data["Headers"][1:]:
        content = plain(header.get("Text"))
        day = iso_day(content)
        if not day:
            raise GdanskError("Brak daty w nagłówku planu")
        days.append(day)
        if header.get("Distinction"):
            free.append({"d": day, "n": "\n".join(content.splitlines()[2:])})
    lessons = []
    for row in data["Rows"]:
        if not row:
            continue
        # GPE 26.06 uses field1..fieldN objects with Description/Tooltip;
        # older UONET versions return a simple array of HTML strings.
        if isinstance(row, dict):
            row = [row.get(f"field{i + 1}", "") for i in range(len(data["Headers"]))]
        tooltips = [plain(cell.get("Tooltip")) if isinstance(cell, dict) else "" for cell in row]
        row = [cell.get("Description", "") if isinstance(cell, dict) else cell for cell in row]
        times = plain(row[0]).splitlines()
        if len(times) < 3:
            raise GdanskError("Niepoprawne godziny lekcji")
        for day, cell, tooltip in zip(days, row[1:], tooltips[1:]):
            soup = BeautifulSoup(cell or "", "html.parser")
            divs = [d for d in soup.find_all("div", recursive=False) if not d.get("class") and d.find("span")]
            for div in divs:
                spans = div.find_all("span")
                # A substitution may include struck-out old values and new values
                # inside the same div. Preserve the original description as well.
                groups = []
                for span in spans:
                    classes = set(span.get("class") or [])
                    if "x-treelabel-rlz" in classes:
                        continue
                    style = ("x-treelabel-inv" in classes, "x-treelabel-zas" in classes)
                    if not groups or groups[-1][0] != style:
                        groups.append((style, []))
                    groups[-1][1].append(span.get_text().strip())
                if not groups:
                    continue
                candidates = [g for g in groups if not g[0][0]] or groups
                (cancelled, changed), values = candidates[-1]
                if not values or not values[0]:
                    continue
                offset = 1 if len(values) >= 4 else 0
                room = values[1 + offset] if len(values) > 1 + offset else ""
                teacher = values[2 + offset] if len(values) > 2 + offset else ""
                # The older combined substitution format swaps teacher and room.
                if len(groups) > 1 and changed:
                    teacher, room = room, teacher
                details = plain(str(div))
                replacement = re.search(r"\(zastępstwo:\s*([^)]*)\)", details, re.I)
                if replacement:
                    teacher = replacement.group(1)
                    changed = True
                remarks = "\n".join(d.get_text() for d in soup.select(".uwaga-panel"))
                if tooltip:
                    remarks = "\n".join(filter(None, [remarks, tooltip]))
                lessons.append({"d": day, "g": times[1][:5] + "-" + times[2][:5],
                                "p": values[0], "s": room, "n": teacher,
                                "st": "ODWOL" if cancelled else "ZAST" if changed else "PLAN",
                                "opis": details + ("\n" + remarks if remarks else ""), "numer": times[0]})
    for extra in data.get("Additionals") or []:
        day = iso_day(extra.get("Header"))
        for item in extra.get("Descriptions") or []:
            description = plain(item.get("Description"))
            match = re.match(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s+(.+)", description, re.S)
            if day and match:
                lessons.append({"d": day, "g": match[1] + "-" + match[2], "p": match[3],
                                "s": "", "n": "", "st": "WLASNE", "opis": description})
    return sorted(lessons, key=lambda x: (x["d"], x["g"])), free


def attendance(data, cache) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("Frekwencje"), list):
        raise GdanskError("Niepoprawny format frekwencji")
    times = {t["Id"]: clock_time(t.get("Poczatek")) for t in cache.get("poryLekcji") or []}
    return [{"d": iso_day(row.get("Data")), "t": times.get(row.get("IdPoraLekcji"), ""),
             "k": row.get("IdKategoria", 0), "p": row.get("PrzedmiotNazwa") or ""}
            for row in data["Frekwencje"]]


def statistics(data) -> tuple[object, list]:
    if not isinstance(data, dict):
        raise GdanskError("Niepoprawny format statystyk frekwencji")
    # Classic MVC uses named month columns; newer releases use arrays.
    source = data.get("Statystyki", data.get("statystyki"))
    if not isinstance(source, list):
        raise GdanskError("Brak statystyk frekwencji")
    months = [(9, "Wrzesien"), (10, "Pazdziernik"), (11, "Listopad"), (12, "Grudzien"),
              (1, "Styczen"), (2, "Luty"), (3, "Marzec"), (4, "Kwiecien"), (5, "Maj"), (6, "Czerwiec"), (7, "Lipiec"), (8, "Sierpien")]
    rows = []
    labels = {1: "Obecność", 2: "Nieobecność nieusprawiedliwiona", 3: "Nieobecność usprawiedliwiona",
              4: "Spóźnienie nieusprawiedliwione", 5: "Spóźnienie usprawiedliwione",
              6: "Nieobecność z przyczyn szkolnych", 7: "Zwolnienie"}
    for row in source:
        if "NazwaTypuFrekwencji" in row:
            rows.append({"k": row["NazwaTypuFrekwencji"],
                         "m": {str(n): row.get(k) for n, k in months}, "r": row.get("Razem"),
                         "s1": row.get("Semestr1", row.get("Okres1")), "s2": row.get("Semestr2", row.get("Okres2"))})
        else:
            periods = row.get("okresy") or []
            rows.append({"k": labels.get(row.get("kategoriaFrekwencji"), "Inne"),
                         "m": {str(m["miesiac"]): m.get("wartosc") for m in row.get("miesiace") or []},
                         "r": row.get("razem"), "s1": periods[0] if periods else None,
                         "s2": periods[1] if len(periods) > 1 else None})
    return data.get("Podsumowanie", data.get("podsumowanie")), rows


def assignments(exams, homework) -> list:
    if not isinstance(exams, list) or not isinstance(homework, list):
        raise GdanskError("Niepoprawny format terminarza")
    out = []
    for week in exams:
        for day in week.get("SprawdzianyGroupedByDayList") or []:
            for item in day.get("Sprawdziany") or []:
                out.append({"id": "exam_" + str(item["Id"]), "data": iso_day(day.get("Data")),
                            "przedmiot": item.get("Nazwa") or "", "autor": item.get("Pracownik") or "",
                            "typ": {1: "Sprawdzian", 2: "Kartkówka", 3: "Praca klasowa"}.get(item.get("Rodzaj"), "Sprawdzian"),
                            "opis": plain(item.get("Opis"))})
    for day in homework:
        for item in day.get("Homework") or []:
            out.append({"id": "homework_" + str(item["Id"]),
                        "data": iso_day(item.get("TimeLimit") or day.get("Date")),
                        "przedmiot": item.get("Subject") or "", "autor": item.get("Teacher") or "",
                        "typ": "Zadanie domowe", "opis": plain(item.get("Description"))})
    return sorted({x["id"]: x for x in out}.values(), key=lambda x: x["data"])


def partition_messages(messages, diary) -> list[Reading]:
    """Keep every title while staying below HA's attribute storage limit."""
    chunks, current = [], []
    for message in messages:
        if len(json.dumps(message, ensure_ascii=False).encode("utf-8")) > 12000:
            raise GdanskError("Pojedynczy nagłówek wiadomości przekracza limit HA")
        candidate = current + [message]
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > 12000:
            chunks.append(current)
            current = [message]
        else:
            current = candidate
    chunks.append(current)
    entity = f"sensor.vultron_wiadomosci_{diary['slug']}"
    pages = [f"{entity}_page_{i}" for i in range(2, len(chunks) + 1)]
    unread = sum(not message["przeczytana"] for message in messages)
    attrs = {"wiadomosci": chunks[0], "page_entities": pages,
             "total": len(messages), "stats": f"{unread} / {len(messages)}"}
    if len(json.dumps(attrs, ensure_ascii=False).encode("utf-8")) > 14000:
        raise GdanskError("Lista części skrzynki przekracza limit HA")
    # Publish additional pages first, then expose the current page list.
    readings = [Reading(page, len(chunk), f"Wiadomości: {diary['name']} (część {i})",
                        {"wiadomosci": chunk, "parent_entity": entity})
                for i, (page, chunk) in enumerate(zip(pages, chunks[1:]), 2)]
    readings.append(Reading(entity, unread, f"Wiadomości: {diary['name']}", attrs))
    return readings


def message_readings(client, module, diaries) -> list[Reading]:
    checked_url(module.base, MESSAGES_HOST)
    mailboxes = response_json(client.get(module.base + "api/Skrzynki"))
    if not isinstance(mailboxes, list):
        raise GdanskError("Niepoprawny format skrzynek wiadomości")
    readings = []
    for diary in diaries:
        variants = {diary["name"].casefold(), " ".join(reversed(diary["name"].split())).casefold()}
        matches = [b for b in mailboxes if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)",
                   (b.get("nazwa") or "").casefold()) for name in variants)]
        if len(matches) != 1:
            raise GdanskError("Nie można jednoznacznie przypisać skrzynki do ucznia")
        box = matches[0]
        last_id, rows = 0, []
        for _ in range(100):
            page = response_json(client.get(module.base + "api/OdebraneSkrzynka",
                                           params={"globalKeySkrzynka": box["globalKey"], "idLastWiadomosc": last_id, "pageSize": 50}))
            if not isinstance(page, list):
                raise GdanskError("Niepoprawny format wiadomości")
            rows.extend(page)
            if len(page) < 50:
                break
            next_id = page[-1]["id"]
            if next_id == last_id:
                raise GdanskError("API wiadomości nie przesuwa strony")
            last_id = next_id
        else:
            raise GdanskError("Skrzynka przekroczyła limit bezpiecznej liczby zapytań")
        messages = [{"data": (row.get("data") or "").replace("T", " ")[:16],
                     "nadawca": row.get("korespondenci") or "", "temat": row.get("temat") or "",
                     "przeczytana": row.get("przeczytana") is True, "tresc": "",
                     "url": "https://uonetplus-wiadomosciplus.edu.gdansk.pl/gdansk/App/odebrane"} for row in rows]
        readings.extend(partition_messages(messages, diary))
    return readings


def snapshot(modules, message_module, config, today=None) -> tuple[list[Reading], list[str]]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    readings, errors, all_diaries = [], [], []
    used_slugs = set()
    for module in modules:
        with client_for(module) as client:
            diaries = current_diaries(read_api(client, module, "UczenDziennik"), today, config.get("student_name", ""))
            for diary in diaries:
                if diary["slug"] in used_slugs:
                    raise GdanskError("Powtarzający się uczeń; wymagane jednoznaczne przypisanie dziennika")
                used_slugs.add(diary["slug"])
                all_diaries.append(diary)
                select_diary(client, module, diary)
                cache = read_api(client, module, "UczenCache")
                if not isinstance(cache, dict):
                    raise GdanskError("Niepoprawna konfiguracja godzin lekcji")

                def section(label, callback):
                    try:
                        readings.extend(callback())
                    except AuthenticationError:
                        raise
                    except (GdanskError, ValueError, TypeError, KeyError, httpx.HTTPError) as exc:
                        # No raw server errors: these can contain cookies or personal data.
                        detail = str(exc) if isinstance(exc, GdanskError) else type(exc).__name__
                        errors.append(f"{label}: {detail}")

                for period in diary["Okresy"]:
                    section("Oceny", lambda p=period: [grades(read_api(client, module, "Oceny", okres=p["Id"]), p, diary, today)])
                for delta, suffix in ((-7, "prev"), (0, "curr"), (7, "next")):
                    def plan(delta=delta, suffix=suffix):
                        start = monday + timedelta(days=delta)
                        lessons, free = timetable(read_api(client, module, "PlanZajec", data=start.isoformat() + "T00:00:00"))
                        return [Reading(f"sensor.vultron_plan_{diary['slug']}_{suffix}", len(lessons),
                                        f"Plan lekcji: {diary['name']} ({suffix})", {"lekcje": lessons, "dni_wolne": free})]
                    section("Plan " + suffix, plan)

                def frequency():
                    entries = []
                    for delta in (-14, -7, 0):
                        start = monday + timedelta(days=delta)
                        entries.extend(attendance(read_api(client, module, "Frekwencja", data=start.isoformat() + "T00:00:00", idTypWpisuFrekwencji=-1), cache))
                    entries = sorted({(x["d"], x["t"], x["p"]): x for x in entries}.values(), key=lambda x: (x["d"], x["t"]))
                    entries = [x for x in entries if (today - timedelta(days=14)).isoformat() <= x["d"] <= today.isoformat()]
                    return [Reading(f"sensor.vultron_freq_{diary['slug']}", sum(x["k"] == 2 for x in entries),
                                    f"Frekwencja: {diary['name']}", {"wpisy": entries, "unit_of_measurement": "nieob."})]
                section("Frekwencja", frequency)

                def stats():
                    pct, rows = statistics(read_api(client, module, "FrekwencjaStatystyki", idPrzedmiot=-1))
                    return [Reading(f"sensor.vultron_stats_{diary['slug']}", pct if pct is not None else "unknown",
                                    f"Statystyki: {diary['name']}", {"unit_of_measurement": "%", "rows": rows,
                                                                   "przedmioty": [{"id": -1, "nazwa": "Wszystkie"}]})]
                section("Statystyki", stats)

                def work():
                    items = []
                    for delta in (0, 7, 14, 21):
                        day = (monday + timedelta(days=delta)).isoformat() + "T00:00:00"
                        items.extend(assignments(read_api(client, module, "Sprawdziany", data=day, rokSzkolny=diary["DziennikRokSzkolny"]),
                                                 read_api(client, module, "Homework", date=day, schoolYear=diary["DziennikRokSzkolny"], statusFilter=-1)))
                    items = sorted({x["id"]: x for x in items if x["data"] >= today.isoformat()}.values(), key=lambda x: x["data"])
                    return [Reading(f"sensor.vultron_terminarz_{diary['slug']}", len(items), f"Terminarz: {diary['name']}", {"lista": items})]
                section("Terminarz", work)

                def notes():
                    data = read_api(client, module, "UwagiIOsiagniecia")
                    remarks = [{"data": iso_day(x.get("DataWpisu")), "tresc": plain(x.get("TrescUwagi")),
                                "autor": x.get("Nauczyciel") or "", "kategoria": x.get("Kategoria") or "",
                                "punkty": x.get("Punkty"), "typ": {1: "informacyjna", 2: "pozytywna", 3: "negatywna"}.get(x.get("KategoriaTyp"), "informacyjna"),
                                "id": hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:16]}
                               for x in data["Uwagi"]]
                    achievements = [{"id": i, "tresc": plain(x) if isinstance(x, str) else plain(x.get("Tresc", x.get("Opis", "")))} for i, x in enumerate(data.get("Osiagniecia") or [], 1)]
                    return [Reading(f"sensor.vultron_uwagi_{diary['slug']}", len(remarks), f"Uwagi: {diary['name']}", {"uwagi": remarks}),
                            Reading(f"sensor.vultron_osiagniecia_{diary['slug']}", len(achievements), f"Osiągnięcia: {diary['name']}", {"osiagniecia": achievements})]
                section("Uwagi", notes)

                def meetings():
                    data = read_api(client, module, "Zebrania")
                    if not isinstance(data, list):
                        raise GdanskError("Niepoprawny format zebrań")
                    items = [{"id": x.get("Id"), "opis": "\n".join(filter(None, [plain(x.get("Tytul")), plain(x.get("TematZebrania")), plain(x.get("Agenda"))])),
                              "sala": "", "online": str(x.get("ZebranieOnline") or ""),
                              "data": iso_day(x.get("Tytul")), "godzina": clock_time(x.get("Tytul"))} for x in data]
                    return [Reading(f"sensor.vultron_zebrania_{diary['slug']}", len(items), f"Zebrania: {diary['name']}", {"zebrania": items})]
                section("Zebrania", meetings)

    if not all_diaries:
        raise GdanskError("Nie znaleziono pasującego ucznia w bieżącym roku szkolnym")
    if message_module:
        try:
            with client_for(message_module) as client:
                readings.extend(message_readings(client, message_module, all_diaries))
        except (GdanskError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
            errors.append("Wiadomości: " + (str(exc) if isinstance(exc, GdanskError) else type(exc).__name__))
    else:
        errors.append("Wiadomości: brak modułu na stronie startowej")
    for reading in readings:
        reading.attrs.update({"provider": "gdansk", "source_url": PORTAL})
    return readings, errors
