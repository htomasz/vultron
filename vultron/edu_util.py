"""Czyste funkcje eduVULCAN (faza 1 refaktoru, claude/refaktor-plan.md).

Bez stanu: bez logów, bazy, HA i konfiguracji. Tylko przekształcenia danych
z API (daty, tekst, oceny, rozmiar atrybutów encji). Ścieżka GPE z tego
modułu nie korzysta. Nie importuje vultron (podwójne wykonanie modułu).
"""
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit


_PL_TRANS = str.maketrans("ąćęłńóśźż", "acelnoszz")


_RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')


_RE_SPACES = re.compile(r' {2,}')


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []
        self.current_href = ""

    def is_safe_url(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip().lower()
        if u.startswith("javascript:") or u.startswith("data:") or u.startswith("vbscript:"):
            return False
        return True

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div', 'li', 'tr'):
            self.text.append('\n')
        elif tag in ('b', 'strong'):
            self.text.append('**')
        elif tag in ('i', 'em'):
            self.text.append('*')
        elif tag == 'a':
            href = dict(attrs).get('href', '')
            if self.is_safe_url(href):
                self.current_href = href.strip()
        elif tag == 'img':
            src = dict(attrs).get('src', '')
            alt = dict(attrs).get('alt', '')
            if self.is_safe_url(src):
                img_text = f" {alt} ({src}) " if alt else f" {src} "
                self.text.append(img_text)

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li', 'tr'):
            self.text.append('\n')
        elif tag in ('b', 'strong'):
            self.text.append('**')
        elif tag in ('i', 'em'):
            self.text.append('*')
        elif tag == 'a':
            if self.current_href:
                self.text.append(f" ({self.current_href})")
                self.current_href = ""

    def handle_data(self, d):
        self.text.append(d)

    def get_data(self):
        return ''.join(self.text)


def _safe_int(value, default: int = 0) -> int:
    """int z wartości JSON; null i śmieci dają `default`.
    (dict.get(klucz, 0) nie wystarcza: przy kluczu z wartością null zwraca None.)"""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def slugify(text: str) -> str:
    if not text:
        return "unknown"
    return re.sub(r"[^a-z0-9]+", "_", text.lower().translate(_PL_TRANS)).strip("_")


def _fold_pl(text: str) -> str:
    """Małe litery bez polskich znaków, do porównań. Długość zostaje taka sama,
    więc indeksy dopasowania w wersji zwiniętej pasują do tekstu oryginalnego."""
    return (text or "").lower().translate(_PL_TRANS)


def clean_html(raw: str) -> str:
    """HTML -> zwykły tekst: zachowuje nowe linie i listy, pogrubienia zamienia
    na Markdown, adresy linków i obrazków dopisuje do tekstu (tylko bezpieczne
    adresy, bez javascript: i data:)."""
    if not raw:
        return "Brak opisu"

    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_data().replace("&nbsp;", " ")

    text = _RE_MULTIPLE_NEWLINES.sub('\n\n', text)
    text = _RE_SPACES.sub(' ', text)

    return text.strip() or "Brak opisu"


_RE_DATE_ISO    = re.compile(r"\d{4}-\d{2}-\d{2}")


_RE_DATE_DOTTED = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")


def _date_or_empty(raw) -> str:
    """Data z API do atrybutu sensora: "YYYY-MM-DD" lub "D.M.RRRR" (część przed
    "T"), cokolwiek innego -> "". Nie przepuszczamy dowolnego tekstu z API
    (druga warstwa obok _esc() w kartach)."""
    value = str(raw or "").split("T")[0].strip()
    return value if (_RE_DATE_ISO.fullmatch(value) or _RE_DATE_DOTTED.fullmatch(value)) else ""


def _txt(value) -> str:
    """Pole z API do SQLite: None -> "", nie-tekst -> str(), żeby jedna dziwna
    wartość nie przerwała zapisu całej partii."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def clean_text(text: str, max_len: int = 200) -> str:
    t = str(text).replace("\n", " ").replace("\r", "") if text else ""
    return t[: max_len - 3] + "..." if len(t) > max_len else t


def _parse_cal_dt(raw) -> datetime | None:
    """Pole start/end wydarzenia kalendarza HA -> datetime.

    HA zwraca obiekt {"dateTime": "2026-09-07T07:00:00+02:00"} (z godziną),
    obiekt {"date": "2026-09-07"} (całodniowe) albo gołe stringi w tych formatach.
    Dla całodniowych i niepoprawnych zwraca None - nie mieszczą się w slocie planu.
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        raw = raw.get("dateTime") or None  # brak "dateTime" (np. tylko "date") -> całodniowe, None
    if not raw or not isinstance(raw, str):
        return None
    if len(raw) <= 10:  # samo "YYYY-MM-DD" -> wydarzenie całodniowe
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _url_for_log(url: str) -> str:
    """Adres do logu bez zapytania, fragmentu i danych logowania
    (np. returnTo z tokenem na ekranie 2FA)."""
    try:
        p = urlsplit(url or "")
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
    except ValueError:
        return "<niepoprawny URL>"
    return f"{p.scheme}://{host}{port}{p.path}" if p.scheme else ""


def _grade_value(wpis) -> tuple[str, float] | None:
    """Wartość liczbowa oceny cząstkowej: ("pct", 47.0) dla ocen procentowych,
    ("num", 4.5) dla skali 1-6, None dla wpisów nieliczonych (np, nb, bz, litery).

    Oceny procentowe nie są mieszane ze skalą 1-6 (progi ustala szkoła).
    Formaty: "47 (%)", "47%", "47,5 (%)"; wartość spoza 0-100 jest odrzucana.
    """
    w_str = str(wpis).strip().upper()
    m_pct = re.fullmatch(r"(\d{1,3}|\d{1,3}[.,]\d+)\s*(?:%|\(\s*%)\s*\)?", w_str)
    if m_pct:
        pv = float(m_pct.group(1).replace(",", "."))
        return ("pct", pv) if 0.0 <= pv <= 100.0 else None
    # "1 (23%)": liczy się ocena 1-6, procent jest tylko informacją.
    # Bez wiodącego \s* (koszt kwadratowy przy długim tekście); spacje zdejmuje rstrip.
    w_str = re.sub(r"\(\s*(?:\d{1,3}|\d{1,3}[.,]\d+)\s*%\s*\)$", "", w_str).rstrip()
    if re.search(r"[A-F%]|NB|NP|BZ", w_str):
        return None
    m_dec = re.search(r"(?<!\d)([1-6])(?:[.,](\d+))?(?!\d)", w_str)
    if not m_dec:
        return None
    v = float(m_dec.group(1))
    if m_dec.group(2):
        v += float("0." + m_dec.group(2))
    elif "+" in w_str:
        v += 0.5
    elif "-" in w_str:
        v -= 0.25
    return ("num", v)


def _subject_summary(p_item: dict, srednie_wlaczone: bool = True) -> dict:
    """Oceny cząstkowe przedmiotu do atrybutu sensora + średnie.

    Ocena z "idOcenaPoprawiona" została poprawiona: nie liczy się do średniej
    i jest doklejana jako "pop" do oceny obowiązującej z tej samej kolumny.
    Pole "w" zostaje oceną obowiązującą, bo automatyzacje wykrywają nowe oceny
    po w|d|i (zmiana "w" dawałaby fałszywe powiadomienia).

    Średnia (szkoła z włączonymi średnimi, ustawienia.isSredniaAndPunkty):
    pole "srednia" z API - jedyne zgodne z Vulcanem (poprawki, wagi, wartości
    +/- ustawione przez szkołę); 0.0 = brak ocen liczonych. Gdy pola brak -
    własna średnia ważona, bez ocen poprawionych.
    Szkoła z WYŁĄCZONYMI średnimi: API daje 0.0, a wagi są zwykle 0.00
    (nieustawiane, bo nieużywane) - liczymy zwykłą średnią bez wag, bez ocen
    poprawionych, jak dawniej. Brak ocen liczonych -> None.
    """
    oceny: list[dict] = []
    liczone: list[tuple] = []   # (wpis, waga) ocen obowiązujących
    for kol in (p_item.get("kolumnyOcenyCzastkowe") or []):
        desc = f"{kol.get('kategoriaKolumny') or ''}: {kol.get('nazwaKolumny') or ''}".strip(": ")
        obowiazujace: list[dict] = []
        poprawione: list[dict] = []
        for o in (kol.get("oceny") or []):
            v, dt = str(o.get("wpis", "")), str(o.get("dataOceny", ""))
            wpis = {"w": v, "d": dt[:5], "i": clean_text(desc)}
            if o.get("idOcenaPoprawiona") is not None:
                poprawione.append(wpis)
            else:
                obowiazujace.append(wpis)
                liczone.append((v, o.get("waga")))
        if obowiazujace:
            if poprawione:
                # Oceny nie mają własnego id, więc parujemy po kolumnie (poprawa
                # jest w kolumnie oryginału). Kolejność jak w API.
                obowiazujace[0]["pop"] = [p["w"] for p in poprawione]
            oceny.extend(obowiazujace)
        else:
            # Teoretycznie niemożliwe: sama ocena poprawiona bez poprawy.
            for p in poprawione:
                p["poprawiona"] = True
            oceny.extend(poprawione)

    vals: list[float] = []
    wagi: list[float] = []
    pvals: list[float] = []
    for v, waga in liczone:
        gv = _grade_value(v)
        if gv is None:
            continue
        kind, x = gv
        if kind == "pct":
            pvals.append(x)
            continue
        try:
            w = 1.0 if waga is None or not srednie_wlaczone else float(waga)
        except (TypeError, ValueError):
            w = 1.0
        if w > 0:
            vals.append(x)
            wagi.append(w)

    api_avg = p_item.get("srednia")
    try:
        api_avg = float(str(api_avg).replace(",", ".")) if api_avg is not None else None
    except ValueError:
        api_avg = None

    srednia, zrodlo = None, None
    if srednie_wlaczone and api_avg is not None:
        if api_avg > 0:
            srednia, zrodlo = round(api_avg, 2), "vulcan"
    elif vals:
        srednia = round(sum(x * w for x, w in zip(vals, wagi)) / sum(wagi), 2)
        zrodlo = "wyliczona"

    return {
        "oceny":          oceny,
        "srednia":        srednia,
        "srednia_zrodlo": zrodlo,
        "srednia_proc":   round(sum(pvals) / len(pvals), 1) if pvals else None,
    }


# Limit listy w atrybucie sensora: HA po cichu odrzuca atrybuty > 16384 B
# (patrz migracja v2). Zapas na pozostałe pola (friendly_name, last_update...).
ATTR_LIST_MAX_BYTES = 14000


def _fit_attr_list(items: list) -> tuple[list, int]:
    """Przycina listę (posortowaną od najważniejszych) do ATTR_LIST_MAX_BYTES.
    Zwraca (zachowane, liczba_pominiętych); pominięte trafiają do atrybutu "pominiete"."""
    kept: list = []
    size = 2
    for item in items:
        item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 1
        if size + item_size > ATTR_LIST_MAX_BYTES:
            break
        kept.append(item)
        size += item_size
    return kept, len(items) - len(kept)


def _fit_messages(msgs: list) -> tuple[list, int]:
    """Wiadomości do atrybutu sensora w limicie ATTR_LIST_MAX_BYTES. Lista
    zostaje cała (automatyzacje porównują nagłówki: skrócenie listy dałoby
    fałszywe "nowe" wiadomości), a treść jest czyszczona od najstarszych.
    Zwraca (lista, liczba_wyczyszczonych_treści)."""
    def _size() -> int:
        return len(json.dumps(msgs, ensure_ascii=False).encode("utf-8"))
    skrocone = 0
    for m in reversed(msgs):  # lista od najnowszych
        if _size() <= ATTR_LIST_MAX_BYTES:
            break
        if m.get("tresc"):
            m["tresc"] = ""
            skrocone += 1
    return msgs, skrocone


def _min_api_date(items, field: str) -> str | None:
    """Najwcześniejsza data w odpowiedzi API (dolna granica dla _delete_missing).
    None = brak dat, wtedy nic nie kasujemy."""
    days = [d for d in (_normalize_date_prefix(_txt(i.get(field))) for i in items if isinstance(i, dict)) if d]
    return min(days) if days else None


def _remark_row(item: dict, slug: str) -> tuple | None:
    """Wpis z /api/Uwagi -> wiersz tabeli remarks (None: wpis bez id)."""
    item_id = item.get("id")
    if not item_id:
        return None
    # Treść uwagi czyścimy z HTML (druga warstwa obok escape'owania w karcie).
    tr    = clean_html(item.get("tresc") or "")
    typ_u = ("pozytywna" if "pochwa" in tr.lower()
             else "negatywna" if "uwaga" in tr.lower()
             else "informacja")
    return (str(item_id), slug, _date_or_empty(item.get("data")),
            tr, item.get("autor") or "", item.get("kategoria") or "",
            str(item.get("liczbaPunktow") or ""), typ_u)


def _zebrania_attrs(lista: list) -> dict:
    """Atrybuty sensora zebrań z limitem rozmiaru (lista DESC - najpierw przyszłe)."""
    zebrania, pominiete = _fit_attr_list(lista)
    attrs = {"zebrania": zebrania, "icon": "mdi:account-group"}
    if pominiete:
        attrs["pominiete"] = pominiete
    return attrs


def _pupil_name(account: dict) -> str:
    p = account.get("Pupil") if isinstance(account.get("Pupil"), dict) else {}
    return f"{p.get('FirstName') or ''} {p.get('Surname') or ''}".strip()


def _cut_utf8(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes - 3].decode("utf-8", errors="ignore") + "..."


_DATE_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


_DATE_DOTTED_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")


def _normalize_date_prefix(raw) -> str | None:
    """Zwraca datę w formacie 'YYYY-MM-DD' albo None, gdy formatu daty w
    danym wierszu nie da się rozpoznać z pewnością LUB gdy data - mimo
    poprawnego formatu - nie jest kalendarzowo poprawna (np. dzień 32,
    miesiąc 13, 29 lutego w roku nieprzestępnym).

    Dane w bazie pochodzą z różnych pól API Vulcan. Większość tabel ma ISO 8601
    ("2026-09-09", czasem z 'T', godziną i strefą, np. "messages.data":
    "2026-10-29T07:38:50.85+01:00" - sprawdzone na prawdziwej bazie). Format
    kropkowy "09.09.2026" jest obsługiwany na wszelki wypadek (karty JS też
    obsługują obie postacie).

    W niejasnych przypadkach (brak roku w dacie kropkowej, data niepoprawna
    kalendarzowo, np. "2026-13-45") zwraca None - _prune_old_data takiego
    wiersza nie kasuje. Każdy kandydat jest weryfikowany przez zbudowanie daty.
    """
    if not raw or not isinstance(raw, str):
        return None

    candidate: str | None = None

    m = _DATE_ISO_RE.match(raw.strip())
    if m:
        candidate = m.group(1)
    else:
        m = _DATE_DOTTED_RE.match(raw.strip())
        if m:
            day, month, year = m.groups()
            if not year:
                return None  # brak roku w dacie kropkowej - nie zgadujemy
            if len(year) == 2:
                year = "20" + year
            try:
                candidate = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            except ValueError:
                return None

    if candidate is None:
        return None

    # Walidacja kalendarzowa przez faktyczne zbudowanie daty (dzień 32, 29 lutego itp.).
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None

    return candidate

