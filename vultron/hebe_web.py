# SPDX-License-Identifier: GPL-3.0-or-later
"""Most: dane z API mobilnego (hebe.py) w kształcie odpowiedzi stron WWW.

Fetchery w vultron.py (zapis do bazy, wykrywanie nowości, encje) zostają bez
zmian: zamiast klienta HTTP stron WWW dostają klienta, który odpowiada danymi
z API mobilnego, przełożonymi na ten sam format. Formaty pól sprawdzone
porównaniem z prawdziwym kontem (faza 2 eksperymentu mobile_api).

Ścieżki, których API mobilne nie ma (np. osiągnięcia), dostają 404, a gdy jest
zapisana sesja WWW - idą do prawdziwego klienta stron WWW.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta

import hebe

# Kategorie frekwencji (te same kody na stronach WWW i w PresenceType.CategoryId).
OBECNOSC, NIEOBECNOSC, USPRAWIEDLIWIONA, SPOZNIENIE, SPOZNIENIE_USPR, SZKOLNE, ZWOLNIENIE = range(1, 8)
# Status lekcji jak "adnotacja" na stronach WWW.
ADNOTACJA = {"": 0, "ZAST": 1, "PRZEN": 2, "ODWOL": 3}
TYP_ZADANIE = 4

logger = logging.getLogger("Vultron")

# Lekcje całego roku (statystyki frekwencji, lista przedmiotów) to kilka stron
# odpowiedzi: między cyklami trzymamy je godzinę. Wpisy frekwencji z ostatnich
# dni idą osobnym, małym zapytaniem co cykl.
YEAR_LESSONS_TTL = 3600
_year_lessons_cache: dict[tuple, tuple[float, list]] = {}


# ── pomocnicze ───────────────────────────────────────────────────────────

def _param_date(params: dict, key: str, default: date) -> date:
    """Data z parametru stron WWW ("RRRR-MM-DDT00:00:00.000Z")."""
    try:
        return date.fromisoformat(str(params.get(key) or "")[:10])
    except ValueError:
        return default


def _with_offset(day: str) -> str:
    """"RRRR-MM-DD" -> "RRRR-MM-DDT00:00:00+01:00" (lokalna strefa w tym dniu),
    jak terminy na stronach WWW."""
    return datetime.fromisoformat(day).astimezone().isoformat(timespec="seconds")


def _dotted(iso_day: str) -> str:
    return f"{iso_day[8:10]}.{iso_day[5:7]}.{iso_day[:4]}" if iso_day else ""


def _subject(record: dict) -> str:
    return hebe.name_of(record.get("Subject"), "Name")


# ── okresy, oceny ────────────────────────────────────────────────────────

def periods_web(account: dict) -> list[dict]:
    """Okresy bieżącego roku (ten sam poziom co okres bieżący) jak /api/OkresyKlasyfikacyjne."""
    periods = [p for p in (account.get("Periods") or []) if isinstance(p, dict)]
    current = next((p for p in periods if p.get("Current") is True), None)
    if current is None:
        return []
    return [{"id": p.get("Id"), "numerOkresu": p.get("Number")}
            for p in periods if p.get("Level") == current.get("Level")
            and type(p.get("Id")) is int and type(p.get("Number")) is int]


def grades_web(grades: list[dict], summaries: list[dict], averages: list[dict]) -> dict:
    """Oceny okresu jak /api/Oceny: przedmioty -> kolumny -> oceny.

    Data oceny = ModifiedAt (tak podają strony WWW). Przedmioty bez ocen
    (np. Zachowanie) biorą się z ocen końcowych. Średnia z API, jeśli jest;
    szkoła bez średnich (wszystkie wagi 0) -> isSredniaAndPunkty = false,
    wtedy Vultron liczy zwykłą średnią, jak dla stron WWW."""
    subjects: dict[str, dict] = {}

    def subject_entry(name: str) -> dict:
        return subjects.setdefault(name, {"przedmiotNazwa": name, "kolumnyOcenyCzastkowe": [],
                                          "proponowanaOcenaOkresowa": None, "ocenaOkresowa": None})

    columns: dict[tuple[str, object], dict] = {}
    weights = []
    for g in grades:
        col = g.get("Column") if isinstance(g.get("Column"), dict) else {}
        name = hebe.name_of(col.get("Subject"), "Name") or "Inne"
        entry = subject_entry(name)
        key = (name, col.get("Id"))
        if key not in columns:
            columns[key] = {"idKolumny": col.get("Id"),
                            "kategoriaKolumny": hebe.name_of(col.get("Category"), "Name"),
                            "nazwaKolumny": col.get("Name") or "", "oceny": []}
            entry["kolumnyOcenyCzastkowe"].append(columns[key])
        changed = hebe.local_date(g.get("ModifiedAt")) or hebe.local_date(g.get("CreatedAt"))
        weight = col.get("Weight")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            weights.append(weight)
        columns[key]["oceny"].append({
            "wpis": g.get("ContentRaw") or g.get("Content") or "",
            "dataOceny": _dotted(changed),
            "waga": weight,
            "idOcenaPoprawiona": None,
        })

    for s in summaries:
        name = _subject(s)
        if not name:
            continue
        entry = subject_entry(name)
        # Entry_1 = ocena przewidywana, Entry_2 = końcowa (jak w Wulkanowym).
        entry["proponowanaOcenaOkresowa"] = s.get("Entry_1")
        entry["ocenaOkresowa"] = s.get("Entry_2")

    for a in averages:
        name, avg = _subject(a), a.get("Average")
        if name in subjects and isinstance(avg, str) and avg.strip():
            subjects[name]["srednia"] = avg.strip()

    out = {"ocenyPrzedmioty": list(subjects.values())}
    if weights and not any(w > 0 for w in weights):
        out["ustawienia"] = {"isSredniaAndPunkty": False}
    return out


# ── plan, dni wolne ──────────────────────────────────────────────────────

def lesson_web(raw: dict) -> dict | None:
    """Lekcja jak w /api/PlanZajec. Zastępstwo (Substitution) nadpisuje dzień,
    godzinę, przedmiot, salę i nauczyciela; odwołanie: ClassAbsence albo
    zastępstwo bez przedmiotu i nauczyciela."""
    sub = raw.get("Substitution") if isinstance(raw.get("Substitution"), dict) else None
    src = sub or {}
    slot = src.get("TimeSlot") if isinstance(src.get("TimeSlot"), dict) else raw.get("TimeSlot")
    slot = slot if isinstance(slot, dict) else {}
    day = hebe.api_date(src.get("ChangeDateAt")) or hebe.api_date(raw.get("DateAt"))
    start, end = str(slot.get("Start") or "")[:5], str(slot.get("End") or "")[:5]
    if not day or len(start) != 5 or len(end) != 5:
        return None
    status = ""
    if sub is not None:
        if sub.get("ClassAbsence") or (not sub.get("Subject") and not sub.get("TeacherPrimary")):
            status = "ODWOL"
        elif hebe.api_date(sub.get("ChangeDateAt")) not in ("", hebe.api_date(raw.get("DateAt"))):
            status = "PRZEN"
        else:
            status = "ZAST"
    return {
        "data": f"{day}T00:00:00",
        "godzinaOd": f"{day}T{start}:00",
        "godzinaDo": f"{day}T{end}:00",
        "przedmiot": (_subject(src) or _subject(raw) or raw.get("Event") or "Zajęcia"),
        "sala": hebe.name_of(src.get("Room"), "Code") or hebe.name_of(raw.get("Room"), "Code"),
        "prowadzacy": hebe.surname_first(src.get("TeacherPrimary")) or hebe.surname_first(raw.get("TeacherPrimary")),
        "adnotacja": ADNOTACJA[status],
        "zmiany": [],
    }


def free_days_web(vacations: list[dict]) -> list[dict]:
    """Dni wolne jak /api/DniWolne (API mobilne podaje je już dla ucznia)."""
    return [{"wszystkieSkladowe": True, "jednostkiSkladowe": None,
             "dataOd": hebe.api_date(v.get("From")), "dataDo": hebe.api_date(v.get("To")),
             "nazwa": v.get("Name") or ""} for v in vacations]


# ── terminarz ────────────────────────────────────────────────────────────

def timetable_web(exams: list[dict], homework: list[dict],
                  known_ids: dict[tuple[str, str, int], list[str]] | None = None) -> list[dict]:
    """Sprawdziany i zadania jak /api/SprawdzianyZadaniaDomowe (+ szczegóły).

    Identyfikatory stron WWW i API mobilnego są różne. known_ids (dzień,
    przedmiot, typ) -> id już zapisane w bazie: wpis, który już był, zachowuje
    swoje id i nie wraca jako "nowy"."""
    pool = {k: list(v) for k, v in (known_ids or {}).items()}
    out = []
    for raw, is_homework in [(e, False) for e in exams] + [(h, True) for h in homework]:
        day = hebe.api_date(raw.get("DeadlineAt")) or hebe.api_date(raw.get("DateAt"))
        if not day:
            continue
        typ = TYP_ZADANIE if is_homework else (raw.get("TypeId") if raw.get("TypeId") in (1, 2, 3) else 1)
        subject = _subject(raw)
        ids = pool.get((day, subject, typ))
        item_id = ids.pop(0) if ids else str(raw.get("Id"))
        out.append({
            "id": item_id,
            "typ": typ,
            "data": _with_offset(hebe.api_date(raw.get("DateAt")) or day),
            "terminOdpowiedzi": _with_offset(day),
            "opis": raw.get("Content") or "",
            "przedmiotNazwa": subject,
            "nauczycielImieNazwisko": hebe.name_of(raw.get("Creator"), "DisplayName"),
        })
    return out


# ── frekwencja ───────────────────────────────────────────────────────────

def _category(raw: dict) -> int | None:
    pres = raw.get("PresenceType")
    if not isinstance(pres, dict) or pres.get("Removed") is True:
        return None
    cat = pres.get("CategoryId")
    return cat if type(cat) is int and OBECNOSC <= cat <= ZWOLNIENIE else None


def attendance_web(lessons: list[dict], date_from: date, date_to: date) -> list[dict]:
    """Wpisy frekwencji jak /api/Frekwencja (tylko lekcje z odnotowaną obecnością)."""
    out = []
    for raw in lessons:
        day = hebe.api_date(raw.get("DayAt"))
        slot = raw.get("TimeSlot") if isinstance(raw.get("TimeSlot"), dict) else {}
        start = str(slot.get("Start") or "")[:5]
        cat = _category(raw)
        if not day or len(start) != 5 or cat is None or not date_from.isoformat() <= day <= date_to.isoformat():
            continue
        out.append({"data": f"{day}T00:00:00", "godzinaOd": f"{day}T{start}:00", "kategoriaFrekwencji": cat})
    return out


def presence_percent(counts: dict[int, int]) -> float | None:
    """Frekwencja jak w dzienniku: obecności (z spóźnieniami i nieobecnościami
    z przyczyn szkolnych) / wszystkie lekcje bez zwolnień."""
    present = sum(counts.get(c, 0) for c in (OBECNOSC, SPOZNIENIE, SPOZNIENIE_USPR, SZKOLNE))
    total = present + counts.get(NIEOBECNOSC, 0) + counts.get(USPRAWIEDLIWIONA, 0)
    return round(present * 100 / total, 2) if total else None


def subjects_web(lessons: list[dict]) -> list[dict]:
    """Przedmioty z lekcji jak /api/Przedmioty (id -1 = "Wszystkie")."""
    seen: dict[int, str] = {}
    for raw in lessons:
        s = raw.get("Subject")
        if isinstance(s, dict) and type(s.get("Id")) is int and s.get("Name"):
            seen.setdefault(s["Id"], s["Name"])
    return [{"id": -1, "nazwa": "Wszystkie"}] + [{"id": i, "nazwa": n} for i, n in seen.items()]


def stats_web(lessons: list[dict], periods: list[tuple[int, date, date]], subject_id: int = -1) -> dict:
    """Statystyki jak /api/FrekwencjaStatystyki, policzone z lekcji roku szkolnego.
    periods: [(numer, od, do)]; subject_id -1 = wszystkie przedmioty."""
    per_cat: dict[int, dict] = {}
    totals: dict[int, int] = {}
    for raw in lessons:
        if subject_id != -1 and (raw.get("Subject") or {}).get("Id") != subject_id:
            continue
        cat, day = _category(raw), hebe.api_date(raw.get("DayAt"))
        if cat is None or not day:
            continue
        d = date.fromisoformat(day)
        row = per_cat.setdefault(cat, {"m": {}, "o": [0] * max(2, len(periods)), "r": 0})
        row["m"][d.month] = row["m"].get(d.month, 0) + 1
        for i, (_, start, end) in enumerate(periods):
            if start <= d <= end:
                row["o"][i] += 1
        row["r"] += 1
        totals[cat] = totals.get(cat, 0) + 1
    return {
        "podsumowanie": presence_percent(totals),
        "statystyki": [{"kategoriaFrekwencji": cat,
                        "miesiace": [{"miesiac": m, "wartosc": v} for m, v in sorted(row["m"].items())],
                        "okresy": row["o"], "razem": row["r"]} for cat, row in sorted(per_cat.items())],
    }


# ── numerek, zebrania, wiadomości ────────────────────────────────────────

def lucky_web(lucky: object) -> dict:
    number = lucky.get("Number") if isinstance(lucky, dict) else None
    return {"numer": number if number else None, "id": ""}


def meetings_web(meetings: list[dict]) -> list[dict]:
    out = []
    for m in meetings:
        when = str(m.get("DateAt") or "")
        if "T" not in when and " " in when:
            when = when.replace(" ", "T", 1)
        out.append({"id": m.get("Id"), "dataCzas": when[:19], "sala": m.get("Where") or "",
                    "opis": m.get("Why") or m.get("Agenda") or "", "zebranieOnline": m.get("Online") or ""})
    return out


def inbox_web(messages: list[dict]) -> list[dict]:
    """Odebrane jak /api/OdebraneSkrzynka (klucz = Id: zgodny ze stronami WWW)."""
    out = [{"apiGlobalKey": m.get("Id"), "przeczytana": m.get("ReadAt") is not None,
            "data": str(m.get("SentAt") or "")[:19], "korespondenci": hebe.name_of(m.get("Sender"), "Name"),
            "temat": m.get("Subject") or "", "_tresc": m.get("Content") or ""}
           for m in messages if isinstance(m.get("Id"), str) and not m.get("Withdrawn")]
    out.sort(key=lambda m: m["data"], reverse=True)
    return out[:50]   # jak strona WWW: najnowsze 50


# ── most HTTP ────────────────────────────────────────────────────────────

class Response:
    """Minimum interfejsu httpx.Response, którego używają fetchery."""

    def __init__(self, status_code: int, payload: object = None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self) -> object:
        return self._payload


class PupilBridge:
    """Odpowiada na zapytania fetcherów stron WWW danymi jednego ucznia z API
    mobilnego. Wyniki zapytań do API są zapamiętywane na czas jednego cyklu."""

    def __init__(self, client: hebe.HebeClient, pupil: hebe.Pupil, account: dict,
                 today: date, known_timetable_ids: dict | None = None):
        self.client, self.pupil, self.account, self.today = client, pupil, account, today
        self.known_timetable_ids = known_timetable_ids or {}
        self._cache: dict[tuple, object] = {}
        # Fetchery pytają równolegle (wątki), a klient HTTP i pamięć podręczna są
        # wspólne: zapytania jednego ucznia idą po kolei.
        self._lock = threading.Lock()

    def _memo(self, key: tuple, fetch):
        if key not in self._cache:
            self._cache[key] = fetch()
        return self._cache[key]

    def _get_all(self, endpoint: str, **query):
        p = self.pupil
        return self._memo((endpoint, tuple(sorted(query.items()))),
                          lambda: self.client.get_all(p.rest_url, endpoint, p.pupil_id, pupilId=p.pupil_id, **query))

    def _year(self) -> tuple[date, date, list[tuple[int, date, date]]]:
        """Początek i koniec roku szkolnego oraz okresy (z okresów bieżącego poziomu)."""
        periods = [p for p in (self.account.get("Periods") or []) if isinstance(p, dict)]
        current = next((p for p in periods if p.get("Current") is True), None)
        ranges = []
        for p in periods:
            if current is not None and p.get("Level") == current.get("Level"):
                try:
                    ranges.append((p.get("Number"), date.fromisoformat(hebe.api_date(p.get("Start"))
                                                                         or hebe.api_date(p.get("StartAt"))),
                                   date.fromisoformat(hebe.api_date(p.get("End")) or hebe.api_date(p.get("EndAt")))))
                except ValueError:
                    continue
        ranges.sort(key=lambda r: r[1])
        if ranges:
            return ranges[0][1], ranges[-1][2], ranges
        start = date(self.today.year if self.today.month >= 9 else self.today.year - 1, 9, 1)
        return start, self.today, []

    def _year_lessons(self) -> list[dict]:
        start, _, _ = self._year()
        key = (self.pupil.rest_url, self.pupil.pupil_id, start, self.today)
        hit = _year_lessons_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < YEAR_LESSONS_TTL:
            return hit[1]
        lessons = self._get_all("mobile/lesson/byPupil", dateFrom=start, dateTo=self.today)
        now = time.monotonic()
        for old in [k for k, (t, _) in _year_lessons_cache.items() if now - t >= YEAR_LESSONS_TTL]:
            del _year_lessons_cache[old]   # bez rozrostu: wygasłe wpisy (np. z wczoraj) wypadają
        _year_lessons_cache[key] = (now, lessons)
        return lessons

    def respond(self, path: str, params: dict) -> Response:
        """Ścieżka stron WWW (".../api/Uwagi") -> odpowiedź z danymi z API mobilnego."""
        name = path.rstrip("/").rsplit("/", 1)[-1]
        handler = getattr(self, f"_api_{name}", None)
        if handler is None:
            return Response(404)
        with self._lock:
            try:
                return Response(200, handler(params or {}))
            except hebe.HebeError as e:
                if e.http_status == 404:
                    # Endpointu nie ma w API mobilnym tej szkoły (np. dni wolne):
                    # jak ścieżka nieobsługiwana - strony WWW albo wartość domyślna.
                    logger.debug("[MOBILE] %s: brak w API mobilnym (%s)", name, e)
                    return Response(404)
                # Komunikaty HebeError nie zawierają sekretów (endpoint, kod, opis serwera).
                logger.warning("[MOBILE] %s: %s", name, e)
                return Response(503)

    # Endpointy stron WWW obsługiwane przez API mobilne.

    def _api_OkresyKlasyfikacyjne(self, params):
        return periods_web(self.account)

    def _api_Oceny(self, params):
        p, period = self.pupil, params.get("idOkresKlasyfikacyjny")
        period_id = int(period) if str(period).lstrip("-").isdigit() else p.period_id
        grades = self._get_all("mobile/grade/byPupil", unitId=p.unit_id, periodId=period_id)
        summaries = self._memo(("summary", period_id), lambda: self.client.get_all(
            p.rest_url, "mobile/grade/summary/byPupil", p.pupil_id, last_sync=None,
            unitId=p.unit_id, pupilId=p.pupil_id, periodId=period_id))
        try:
            averages = self._memo(("average", period_id), lambda: self.client.get_all(
                p.rest_url, "mobile/grade/average/byPupil", p.pupil_id, last_sync=None,
                unitId=p.unit_id, pupilId=p.pupil_id, periodId=period_id, scope="auto"))
        except hebe.HebeError:
            averages = []   # bez średnich z API Vultron policzy własną
        return grades_web(grades, summaries, averages)

    def _api_PlanZajec(self, params):
        start = _param_date(params, "dataOd", self.today)
        end = _param_date(params, "dataDo", self.today + timedelta(days=21))
        lessons = self._get_all("mobile/schedule/withchanges/byPupil", dateFrom=start, dateTo=end)
        return [w for w in (lesson_web(r) for r in lessons) if w is not None]

    def _api_DniWolne(self, params):
        start = _param_date(params, "dataOd", self.today)
        end = _param_date(params, "dataDo", self.today + timedelta(days=21))
        return free_days_web(self._get_all("mobile/school/vacation", dateFrom=start, dateTo=end))

    def _timetable(self, params) -> list[dict]:
        start = _param_date(params, "dataOd", self.today - timedelta(days=31))
        end = _param_date(params, "dataDo", self.today + timedelta(days=61))
        exams = self._get_all("mobile/exam/byPupil", dateFrom=start, dateTo=end)
        homework = self._get_all("mobile/homework/byPupil", dateFrom=start, dateTo=end)
        items = self._memo(("timetable", start, end),
                           lambda: timetable_web(exams, homework, self.known_timetable_ids))
        self._cache["timetable_by_id"] = {str(i["id"]): i for i in items}
        return items

    def _api_SprawdzianyZadaniaDomowe(self, params):
        return self._timetable(params)

    def _api_SprawdzianSzczegoly(self, params):
        item = (self._cache.get("timetable_by_id") or {}).get(str(params.get("id")))
        if item is None:
            raise hebe.HebeError("brak wpisu terminarza")
        return item

    _api_ZadanieDomoweSzczegoly = _api_SprawdzianSzczegoly

    def _api_Uwagi(self, params):
        return hebe.web_remarks(self._get_all("mobile/note/byPupil"))

    def _api_Frekwencja(self, params):
        start = _param_date(params, "dataOd", self.today - timedelta(days=14))
        end = _param_date(params, "dataDo", self.today)
        return attendance_web(self._get_all("mobile/lesson/byPupil", dateFrom=start, dateTo=end), start, end)

    def _api_Przedmioty(self, params):
        return subjects_web(self._year_lessons())

    def _api_FrekwencjaStatystyki(self, params):
        _, _, periods = self._year()
        sid = params.get("idPrzedmiot", -1)
        sid = int(sid) if str(sid).lstrip("-").isdigit() else -1
        return stats_web(self._year_lessons(), periods, sid)

    def _api_SzczesliwyNumerTablica(self, params):
        p = self.pupil
        return lucky_web(self._memo(("lucky", self.today), lambda: self.client.call(
            "GET", p.rest_url, "mobile/school/lucky", pupil_id=p.pupil_id,
            query={"pupilId": p.pupil_id, "constituentId": p.constituent_id, "day": self.today})))

    def _api_Zebrania(self, params):
        return meetings_web(self._get_all("mobile/meetings/byPupil", **{"from": self.today - timedelta(days=60)}))

    # Wiadomości (session.get w run_messages_sync).

    def _inbox(self) -> list[dict]:
        if self.pupil.box_key is None:
            raise hebe.HebeError("brak skrzynki wiadomości")
        since = datetime.combine(self.today - timedelta(days=90), datetime.min.time())
        return self._memo(("inbox",), lambda: inbox_web(self._get_all(
            "mobile/messages/received/byBox", box=self.pupil.box_key, last_sync=since)))

    def _api_OdebraneSkrzynka(self, params):
        return [{k: v for k, v in m.items() if k != "_tresc"} for m in self._inbox()]

    def _api_WiadomoscSzczegoly(self, params):
        for m in self._inbox():
            if m["apiGlobalKey"] == params.get("apiGlobalKey"):
                return {"tresc": m["_tresc"]}
        raise hebe.HebeError("brak wiadomości")


# Sekcje, które bez API mobilnego i bez stron WWW mogą być po prostu puste
# (fetcher i tak radzi sobie bez nich), zamiast ostrzeżenia co cykl.
EMPTY_WHEN_UNAVAILABLE = {"DniWolne": []}


class AsyncBridgeClient:
    """Zamiast httpx.AsyncClient w fetcherach ucznia. Ścieżki spoza API mobilnego
    (404) i te, przy których API mobilne zawiodło (503), idą do `web`
    (prawdziwy klient stron WWW), jeśli jest."""

    def __init__(self, bridge: PupilBridge, web=None):
        self.bridge, self.web = bridge, web

    async def get(self, url: str, params: dict | None = None, **kwargs):
        res = await asyncio.to_thread(self.bridge.respond, url.split("?", 1)[0], params or {})
        if res.status_code in (404, 503) and self.web is not None:
            return await self.web.get(url, params=params, **kwargs)
        name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if res.status_code == 404 and name in EMPTY_WHEN_UNAVAILABLE:
            return Response(200, list(EMPTY_WHEN_UNAVAILABLE[name]))
        return res


class MessagesBridgeSession:
    """Zamiast httpx.Client w run_messages_sync: skrzynka wybierana po globalKeySkrzynka."""

    def __init__(self, bridges_by_box: dict[str, PupilBridge]):
        self.bridges_by_box = bridges_by_box

    def get(self, url: str, params: dict | None = None, **kwargs):
        params = params or {}
        if url.endswith("/OdebraneSkrzynka"):
            bridge = self.bridges_by_box.get(params.get("globalKeySkrzynka"))
            return bridge.respond(url, params) if bridge else Response(404)
        for bridge in self.bridges_by_box.values():   # szczegóły: klucz wiadomości jest unikalny
            res = bridge.respond(url, params)
            if res.status_code == 200:
                return res
        return Response(404)

    def close(self) -> None:
        pass
