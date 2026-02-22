import json
import os
import time
import re
import sys
import sqlite3
import pickle
import logging
import shutil
import signal
import secrets
import requests
import asyncio
import hashlib
import httpx
from datetime import datetime, timedelta
from websocket import create_connection
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from pyvirtualdisplay import Display

# --- 1. DEFINICJA ŚCIEŻEK I KONFIGURACJA ŚRODOWISKA ---
os.environ["SE_STATS"] = "0"
DB_PATH = "/data/vultron.db"
VUL_PKL = "/data/vul.pkl"
BUL_PKL = "/data/bul.pkl"
OPTIONS_PATH = "/data/options.json"
HA_TOKEN = os.getenv("SUPERVISOR_TOKEN")
HA_URL = "http://supervisor/core/api"

if not os.path.exists(OPTIONS_PATH):
    print("BŁĄD KRYTYCZNY: Brak pliku options.json")
    sys.exit(1)

with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# --- 2. DYNAMICZNA KONFIGURACJA LOGOWANIA (Z Configu HA) ---
LOG_LEVEL = logging.DEBUG if CONFIG.get("debug") else logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Vultron")

MAPA_STATUSOW = {0: "", 1: "ZAST", 2: "PRZEN", 3: "ODWOL", 4: "NIEOB"}
LAST_SENT_HASHES = {}

# ==========================================================
# POMOCNIKI
# ==========================================================


def slugify(text):
    if not text:
        return "unknown"
    chars = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
    }
    text = text.lower()
    for k, v in chars.items():
        text = text.replace(k, v)
    return re.sub(r"[^a-z0-9]", "_", text).strip("_")


def clean_html(raw):
    if not raw:
        return "Brak opisu"
    return re.sub("<.*?>", "", raw).replace("&nbsp;", " ").strip()


def clean_text(text):
    if not text:
        return ""
    text = (
        str(text)
        .replace('"', "&quot;")
        .replace("'", "&apos;")
        .replace("\n", " ")
        .replace("\r", "")
    )
    if len(text) > 200:
        return text[:197] + "..."
    return text


def send_to_ha_sync(entity_id, state, attributes):
    """Wysyłka danych do HA z kontrolą zmian (Delta-Sync)."""
    payload_dict = {"state": state, "attributes": attributes}
    payload_json = json.dumps(payload_dict, sort_keys=True)

    # Fix B324 Bandit: usedforsecurity=False informuje, że to nie kryptografia
    current_hash = hashlib.md5(payload_json.encode(), usedforsecurity=False).hexdigest()

    if LAST_SENT_HASHES.get(entity_id) == current_hash:
        return

    try:
        url = f"{HA_URL}/states/{entity_id}"
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        }
        res = requests.post(url, headers=headers, data=payload_json, timeout=15)
        if res.status_code in [200, 201]:
            LAST_SENT_HASHES[entity_id] = current_hash
    except Exception as e:
        logger.error(f"Błąd wysyłki do HA ({entity_id}): {e}")


def get_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.binary_location = "/usr/bin/chromium-browser"
    return webdriver.Chrome(options=opts)


# ==========================================================
# SYSTEM: IMPLEMENTACJA SETUP UI
# ==========================================================


def copy_resources():
    target_dir = "/config/www/vultron"
    os.makedirs(target_dir, exist_ok=True)
    app_dir = "/app"
    copied = 0
    if os.path.exists(app_dir):
        for file in os.listdir(app_dir):
            if file.lower().endswith(".js"):
                shutil.copy(os.path.join(app_dir, file), os.path.join(target_dir, file))
                copied += 1
    logger.info(f"Skopiowano {copied} plików kart do /local/vultron/")


async def wait_for_ha_api():
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    async with httpx.AsyncClient() as client:
        while True:
            try:
                r = await client.get(f"{HA_URL}/config", headers=headers, timeout=5)
                if r.status_code == 200:
                    logger.info("API Home Assistant jest gotowe.")
                    break
            except Exception:
                pass
            logger.info("Oczekiwanie na API Home Assistant Core (5s)...")
            await asyncio.sleep(5)


def run_setup_ui():
    """Twoja wierna implementacja setup_resources."""
    log_ui = logging.getLogger("UI-SETUP")

    def get_version():
        try:
            config_path = (
                "config.yaml" if os.path.exists("config.yaml") else "/app/config.yaml"
            )
            with open(config_path, "r") as f:
                content = f.read()
                match = re.search(r'version:\s*["\']?([^"\']+)["\']?', content)
                return match.group(1) if match else "1.0"
        except Exception:
            return "1.0"

    version = get_version()
    ws = None
    max_retries = 10
    ws_url = "ws://supervisor/core/websocket"

    for attempt in range(max_retries):
        try:
            ws = create_connection(ws_url, timeout=10)
            ws.recv()  # Powitanie
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
            auth_res = json.loads(ws.recv())

            if auth_res.get("type") == "auth_ok":
                log_ui.info(f"Połączono z API Home Assistant (próba {attempt + 1})")
                time.sleep(1)
                break
            else:
                log_ui.error("Błąd autoryzacji tokena.")
                if ws:
                    ws.close()
                return
        except Exception as e:
            if attempt < max_retries - 1:
                log_ui.info(
                    f"Oczekiwanie na WebSocket API... (Próba {attempt + 1}/{max_retries})"
                )
                time.sleep(5)
            else:
                log_ui.error(f"BŁĄD KRYTYCZNY WebSocket: {e}")
                return

    try:
        ws.send(json.dumps({"id": 1, "type": "lovelace/resources"}))
        raw_res_data = json.loads(ws.recv())
        raw_res = raw_res_data.get("result", [])

        existing_resources = {
            re.sub(r"\?v=.*", "", r.get("url", "")): r.get("id") for r in raw_res
        }
        existing_full_urls = {
            re.sub(r"\?v=.*", "", r.get("url", "")): r.get("url") for r in raw_res
        }

        path = "/app" if os.path.exists("/app") else "."
        cards = [
            f
            for f in os.listdir(path)
            if f.startswith("vultron-") and f.endswith(".js")
        ]

        msg_id = 2
        for card_file in cards:
            base_url = f"/local/vultron/{card_file}"
            versioned_url = f"{base_url}?v={version}"

            if base_url in existing_resources:
                if versioned_url != existing_full_urls.get(base_url):
                    log_ui.info(f"Aktualizacja wersji zasobu: {card_file} -> {version}")
                    ws.send(
                        json.dumps(
                            {
                                "id": msg_id,
                                "type": "lovelace/resources/update",
                                "resource_id": existing_resources[base_url],
                                "url": versioned_url,
                            }
                        )
                    )
                    ws.recv()
            else:
                log_ui.info(f"Rejestrowanie nowej karty: {card_file} (v{version})")
                ws.send(
                    json.dumps(
                        {
                            "id": msg_id,
                            "type": "lovelace/resources/create",
                            "res_type": "module",
                            "url": versioned_url,
                        }
                    )
                )
                ws.recv()
            msg_id += 1

        log_ui.info("Konfiguracja UI Lovelace zakończona.")
    except Exception as e:
        log_ui.error(f"Błąd podczas rejestracji zasobów: {e}")
    finally:
        if ws:
            ws.close()


# ==========================================================
# MODUŁ 1: DZIENNIK (Selenium Auth + Requests Sync)
# ==========================================================


def run_diary_auth():
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = get_driver()
    wait = WebDriverWait(driver, 25)
    try:
        logger.info("[AUTH] Logowanie Selenium do Dziennika...")
        driver.get("https://eduvulcan.pl/logowanie")
        wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
            CONFIG["username"] + Keys.ENTER
        )
        time.sleep(1.5)
        wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
            CONFIG["password"] + Keys.ENTER
        )
        time.sleep(3)

        child_link = driver.find_element(
            By.XPATH, "//a[contains(@href, 'dziennik')]"
        ).get_attribute("href")
        driver.get(child_link)
        time.sleep(5)

        city = re.search(r"uczen.eduvulcan.pl/([^/]+)", driver.current_url).group(1)
        driver.get(f"https://uczen.eduvulcan.pl/{city}/api/Context")
        time.sleep(2)
        context = json.loads(driver.execute_script("return document.body.innerText"))

        students_to_save = []
        session = requests.Session()
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])

        for u in context.get("uczniowie", []):
            u_key = u.get("key")
            u_id_dz = str(u.get("idDziennik"))
            res_p = session.get(
                f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne?key={u_key}&idDziennik={u_id_dz}"
            )
            okresy = res_p.json()
            curr_p = okresy[-1].get("id")
            for o in okresy:
                try:
                    d_od = datetime.strptime(o["dataOd"][:19], "%Y-%m-%dT%H:%M:%S")
                    d_do = datetime.strptime(o["dataDo"][:19], "%Y-%m-%dT%H:%M:%S")
                    if d_od <= datetime.now() <= d_do:
                        curr_p = o["id"]
                        break
                except Exception:
                    continue
            students_to_save.append(
                {
                    "slug": slugify(u.get("uczen")),
                    "uczen": u.get("uczen"),
                    "city": city,
                    "key": u_key,
                    "idDziennik": u_id_dz,
                    "periodId": curr_p,
                }
            )

        with open(VUL_PKL, "wb") as f:
            pickle.dump({"cookies": driver.get_cookies(), "students": students_to_save}, f)

        return students_to_save, driver.get_cookies()
    except Exception as e:
        logger.error(f"[AUTH] Błąd logowania dziennika: {e}")
        return None, None
    finally:
        driver.quit()
        display.stop()


def sync_diary_data(students, cookies):
    session = requests.Session()
    for c in cookies:
        session.cookies.set(c["name"], c["value"])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Inicjalizacja tabel
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS grades (id_kolumny TEXT, student_slug TEXT, przedmiot TEXT, ocena TEXT, data TEXT, opis TEXT, period_id TEXT, PRIMARY KEY(id_kolumny, student_slug, period_id))"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS schedule (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT, przedmiot TEXT, sala TEXT, prowadzacy TEXT, status TEXT)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS frequency (id TEXT PRIMARY KEY, student_slug TEXT, data TEXT, godzina TEXT, kategoria INTEGER)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS frequency_stats (student_slug TEXT PRIMARY KEY, podsumowanie REAL, rows_json TEXT)"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS timetable (id TEXT, student_slug TEXT, data TEXT, przedmiot TEXT, typ TEXT, opis TEXT, autor TEXT, PRIMARY KEY(id, student_slug))"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS remarks (remark_id TEXT, student_slug TEXT, data TEXT, tresc TEXT, autor TEXT, kategoria TEXT, punkty TEXT, typ TEXT, PRIMARY KEY(remark_id, student_slug))"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS achievements (achievement_id TEXT, student_slug TEXT, tresc TEXT, PRIMARY KEY(achievement_id, student_slug))"
    )

    for s in students:
        slug, city, key, id_dz, name = (
            s["slug"], s["city"], s["key"], s["idDziennik"], s["uczen"]
        )
        logger.info(f"--- Synchronizacja dziecka: {name} ---")

        # --- 1. OCENY (Logika vulo.py) ---
        log_g = logging.getLogger("GRADES")
        res_per = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/OkresyKlasyfikacyjne",
            params={"key": key, "idDziennik": id_dz},
        )
        if res_per.status_code == 200:
            for period in res_per.json():
                p_id, p_num = str(period.get("id")), period.get("numerOkresu")
                log_g.info(f"Pobieram oceny dla {name} (Okres {p_num})")
                res_g = session.get(
                    f"https://uczen.eduvulcan.pl/{city}/api/Oceny",
                    params={"key": key, "idOkresKlasyfikacyjny": p_id},
                )
                if res_g.status_code == 200:
                    subjects_ha, new_g = {}, 0
                    for p_item in res_g.json().get("ocenyPrzedmioty", []):
                        p_n = p_item.get("przedmiotNazwa", "Inne")
                        for kol in p_item.get("kolumnyOcenyCzastkowe", []):
                            id_k = str(kol.get("idKolumny", "0"))
                            desc = f"{kol.get('kategoriaKolumny', '')}: {kol.get('nazwaKolumny', '')}".strip(": ")
                            for o in kol.get("oceny", []):
                                v, dt = str(o["wpis"]), str(o["dataOceny"])
                                cursor.execute(
                                    "SELECT ocena FROM grades WHERE id_kolumny=? AND student_slug=? AND period_id=?",
                                    (id_k, slug, p_id),
                                )
                                if not cursor.fetchone():
                                    cursor.execute(
                                        "INSERT INTO grades VALUES (?,?,?,?,?,?,?)",
                                        (id_k, slug, p_n, v, dt, desc, p_id),
                                    )
                                    new_g += 1
                                if p_n not in subjects_ha:
                                    subjects_ha[p_n] = []
                                subjects_ha[p_n].append(
                                    {"w": v, "d": dt[:5], "i": clean_text(desc)}
                                )

                    lista_ha = []
                    for k, v_list in subjects_ha.items():
                        sum_g, count_g = 0, 0
                        for o in v_list:
                            try:
                                # Wyliczanie średniej (Twój kod)
                                m_avg = re.search(r"\d+", o["w"].replace(",", "."))
                                if m_avg:
                                    val = float(m_avg.group())
                                    if 1 <= val <= 6:
                                        sum_g += val
                                        count_g += 1
                            except Exception:
                                continue
                        avg = round(sum_g / count_g, 2) if count_g > 0 else None
                        lista_ha.append({"przedmiot": k, "oceny": v_list, "srednia": avg})

                    send_to_ha_sync(
                        f"sensor.vultron_oceny_{slug}_p{p_num}",
                        new_g,
                        {
                            "lista_przedmiotow": lista_ha,
                            "friendly_name": f"Oceny: {name} (P{p_num})",
                            "period_number": int(p_num),
                            "student_slug": slug,
                            "active_period": p_id == str(s["periodId"]),
                            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )

        # --- 2. PLAN LEKCJI (Logika vulp.py) ---
        log_p = logging.getLogger("PLAN")
        log_p.info(f"Synchronizacja planu: {name}...")
        now = datetime.now()
        s_p = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%dT00:00:00.000Z")
        e_p = (now + timedelta(days=21)).strftime("%Y-%m-%dT23:59:59.999Z")
        res_plan = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/PlanZajec",
            params={"key": key, "dataOd": s_p, "dataDo": e_p, "zakresDanych": "2"},
        )
        if res_plan.status_code == 200:
            for l in res_plan.json():
                st = MAPA_STATUSOW.get(int(l.get("adnotacja", 0)), "")
                inf = " ".join(
                    [(c.get("informacjeNieobecnosc") or "").lower() for c in l.get("zmiany", [])]
                )
                if "zwolnieni" in inf or "okienko" in inf:
                    st = "ODWOL"
                l_id = f"{slug}_{l['data']}_{l['godzinaOd']}"
                g_r = f"{l['godzinaOd'].split('T')[1][:5]}-{l['godzinaDo'].split('T')[1][:5]}"
                cursor.execute(
                    "INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                    (
                        l_id, slug, l["data"].split("T")[0],
                        g_r, l.get("przedmiot") or "Zajęcia", l.get("sala", ""),
                        l.get("prowadzacy", ""), st,
                    ),
                )
            monday = now - timedelta(days=now.weekday())
            weeks = {
                "prev": (monday - timedelta(days=7), monday - timedelta(days=1)),
                "curr": (monday, monday + timedelta(days=6)),
                "next": (monday + timedelta(days=7), monday + timedelta(days=13)),
            }
            for suf, (sd, ed) in weeks.items():
                s_str, e_str = sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT data, godzina, przedmiot, sala, prowadzacy, status FROM schedule WHERE student_slug=? AND data BETWEEN ? AND ? ORDER BY data, godzina",
                    (slug, s_str, e_str),
                )
                rows = cursor.fetchall()
                proc = [
                    {"d": r[0], "g": r[1], "p": r[2], "s": r[3], "n": r[4], "st": r[5]}
                    for r in rows
                ]
                state = (
                    len([l for l in proc if l["d"] == now.strftime("%Y-%m-%d")])
                    if suf == "curr" else len(proc)
                )
                send_to_ha_sync(
                    f"sensor.vultron_plan_{slug}_{suf}",
                    state,
                    {
                        "lekcje": proc,
                        "friendly_name": f"Plan {suf}: {name}",
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )

        # --- 3. TERMINARZ (Logika vuls.py) ---
        log_w = logging.getLogger("WORK")
        log_w.info(f"Pobieram terminarz dla: {name}...")
        res_t = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/SprawdzianyZadaniaDomowe",
            params={
                "key": key,
                "dataOd": now.strftime("%Y-%m-%dT00:00:00.000Z"),
                "dataDo": (now + timedelta(days=61)).strftime("%Y-%m-%dT23:59:59.999Z"),
            },
        )
        if res_t.status_code == 200:
            for i in res_t.json():
                ep = "ZadanieDomoweSzczegoly" if i.get("typ") == 4 else "SprawdzianSzczegoly"
                dr = session.get(
                    f"https://uczen.eduvulcan.pl/{city}/api/{ep}",
                    params={"key": key, "id": i.get("id")},
                )
                if dr.status_code == 200:
                    dj = dr.json()
                    typ_t = {
                        1: "Sprawdzian", 2: "Kartkówka", 3: "Klasówka", 4: "Zadanie domowe"
                    }.get(i.get("typ"), "Inne")
                    cursor.execute(
                        "INSERT OR REPLACE INTO timetable VALUES (?,?,?,?,?,?,?)",
                        (
                            str(i.get("id")), slug, dj.get("data", ""),
                            dj.get("przedmiotNazwa", ""), typ_t,
                            clean_html(dj.get("opis") or dj.get("temat")),
                            dj.get("nauczycielImieNazwisko", ""),
                        ),
                    )
            cursor.execute(
                "SELECT data, przedmiot, typ, opis, autor FROM timetable WHERE student_slug=? AND data >= ? ORDER BY data",
                (slug, now.strftime("%Y-%m-%d")),
            )
            rows_t = cursor.fetchall()
            send_to_ha_sync(
                f"sensor.vultron_terminarz_{slug}",
                len(rows_t),
                {
                    "lista": [
                        {
                            "data": r[0].split("T")[0], "przedmiot": r[1],
                            "typ": r[2], "opis": r[3], "autor": r[4],
                        }
                        for r in rows_t
                    ],
                    "friendly_name": f"Terminarz: {name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

        # --- 4. UWAGI (Logika vuluw.py) ---
        log_r = logging.getLogger("REMARKS")
        log_r.info(f"Pobieram uwagi dla: {name}...")
        res_rem = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/Uwagi", params={"key": key}
        )
        if res_rem.status_code == 200:
            new_r = 0
            for item in res_rem.json():
                tr = item.get("tresc", "")
                typ_u = (
                    "pozytywna" if "pochwa" in tr.lower()
                    else "negatywna" if "uwaga" in tr.lower()
                    else "informacja"
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO remarks VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(item.get("id")), slug, item.get("data", "").split("T")[0],
                        tr, item.get("autor", ""), item.get("kategoria", ""),
                        str(item.get("liczbaPunktow") or ""), typ_u,
                    ),
                )
                new_r += 1
            cursor.execute(
                "SELECT data, tresc, autor, kategoria, punkty, typ, remark_id FROM remarks WHERE student_slug=? ORDER BY data DESC",
                (slug,),
            )
            lista_u = [
                {
                    "data": r[0], "tresc": r[1], "autor": r[2],
                    "kategoria": r[3], "punkty": r[4], "typ": r[5], "id": r[6],
                }
                for r in cursor.fetchall()
            ]
            send_to_ha_sync(
                f"sensor.vultron_uwagi_{slug}",
                new_r,
                {
                    "uwagi": lista_u, "friendly_name": f"Uwagi: {name}",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

        # --- 5. FREKWENCJA (Logika vulf.py) ---
        log_st = logging.getLogger("STATS")
        log_st.info(f"Synchronizacja frekwencji: {name}...")
        res_f = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/Frekwencja",
            params={
                "key": key,
                "dataOd": (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00.000Z"),
                "dataDo": now.strftime("%Y-%m-%dT23:59:59.999Z"),
            },
        )
        if res_f.status_code == 200:
            recs = res_f.json()
            if isinstance(recs, dict):
                recs = recs.get("oddzialy", [])
            for f_i in recs:
                if f_i.get("data") and f_i.get("godzinaOd"):
                    cursor.execute(
                        "INSERT OR REPLACE INTO frequency VALUES (?,?,?,?,?)",
                        (
                            f"{slug}_{f_i['data']}_{f_i['godzinaOd']}", slug,
                            f_i["data"].split("T")[0], f_i["godzinaOd"].split("T")[1][:5],
                            int(f_i.get("kategoriaFrekwencji", 0)),
                        ),
                    )
            cursor.execute(
                "SELECT data, godzina, kategoria FROM frequency WHERE student_slug=? AND data >= ? ORDER BY data DESC",
                (slug, (now - timedelta(days=14)).strftime("%Y-%m-%d")),
            )
            send_to_ha_sync(
                f"sensor.vultron_freq_{slug}",
                0,
                {
                    "wpisy": [{"d": r[0], "t": r[1], "k": int(r[2])} for r in cursor.fetchall()],
                    "friendly_name": f"Frekwencja: {name}",
                    "last_update": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

        # Statystyki procentowe
        res_fs = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/FrekwencjaStatystyki",
            params={"key": key, "idPrzedmiot": -1},
        )
        if res_fs.status_code == 200:
            cat_m = {1: "Obecność", 2: "Nieobecność", 3: "Usprawiedliwiona", 4: "Spóźnienie", 5: "Spóźnienie uspraw.", 6: "Szkolne", 7: "Zwolnienie"}
            proc_fs = [{"k": cat_m.get(row.get("kategoriaFrekwencji"), "Inna"), "m": {str(m["miesiac"]): m["wartosc"] for m in row.get("miesiace", [])}, "s1": row.get("okresy", [0,0])[0], "s2": row.get("okresy", [0,0])[1], "r": row.get("razem", 0)} for row in res_fs.json().get("statystyki", [])]
            send_to_ha_sync(f"sensor.vultron_stats_{slug}", res_fs.json().get("podsumowanie", 0), {"unit_of_measurement": "%", "rows": proc_fs, "friendly_name": f"Statystyki: {name}", "last_update": now.strftime("%Y-%m-%d %H:%M:%S")})

        # --- 6. OSIĄGNIĘCIA (Logika vulos.py) ---
        log_a = logging.getLogger("ACHIEVEMENTS")
        log_a.info(f"Pobieram osiągnięcia dla: {name}...")
        res_ach = session.get(
            f"https://uczen.eduvulcan.pl/{city}/api/Osiagniecia", params={"key": key}
        )
        if res_ach.status_code == 200:
            new_a = 0
            for item in res_ach.json():
                cursor.execute(
                    "INSERT OR REPLACE INTO achievements VALUES (?,?,?)",
                    (str(item.get("id")), slug, item.get("tresc", "")),
                )
                new_a += 1
            cursor.execute(
                "SELECT achievement_id, tresc FROM achievements WHERE student_slug=?",
                (slug,),
            )
            rows_ach = cursor.fetchall()
            send_to_ha_sync(
                f"sensor.vultron_osiagniecia_{slug}",
                new_a,
                {
                    "osiagniecia": [{"id": r[0], "tresc": r[1]} for r in rows_ach],
                    "nowe": new_a, "friendly_name": f"Osiągnięcia: {name}",
                    "last_update": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

    conn.commit()
    conn.close()


# ==========================================================
# MODUŁ 2: WIADOMOŚCI (Selenium Auth + Requests Sync)
# ==========================================================


def run_messages_sync(city, students_list):
    display = Display(visible=0, size=(1366, 768))
    display.start()
    driver = get_driver()
    wait = WebDriverWait(driver, 25)
    try:
        logger.info("[AUTH-MESS] Logowanie Selenium...")
        driver.get("https://eduvulcan.pl/logowanie")
        if os.path.exists(BUL_PKL):
            try:
                with open(BUL_PKL, "rb") as f:
                    for cookie in pickle.load(f):
                        driver.add_cookie(cookie)
                driver.get("https://eduvulcan.pl/logowanie")
                time.sleep(2)
            except Exception:
                pass
        if "Alias" in driver.page_source:
            wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
                CONFIG["username"] + Keys.ENTER
            )
            wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
                CONFIG["password"] + Keys.ENTER
            )
            time.sleep(3)

        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))
        child_link = driver.find_element(By.XPATH, "//a[contains(@href, 'dziennik')]").get_attribute("href")
        driver.get(child_link)
        time.sleep(3)
        app_url = f"https://wiadomosci.eduvulcan.pl/{city}/App"
        driver.get(app_url)
        time.sleep(5)

        if "logowanie" in driver.current_url:
            try:
                driver.switch_to.frame(1)
                driver.find_element(By.ID, "save-default-button").click()
                driver.switch_to.default_content()
            except Exception:
                pass
            wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(
                CONFIG["username"] + Keys.ENTER
            )
            wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(
                CONFIG["password"] + Keys.ENTER
            )
            driver.get(app_url)
            time.sleep(5)

        session = requests.Session()
        for c in driver.get_cookies():
            session.cookies.set(c["name"], c["value"])
        ua = driver.execute_script("return navigator.userAgent")
        session.headers.update(
            {"User-Agent": ua, "Referer": app_url, "X-Requested-With": "XMLHttpRequest"}
        )

        res_m = session.get(
            f"https://wiadomosci.eduvulcan.pl/{city}/api/Odebrane?idLastWiadomosc=0&pageSize=15"
        )
        if res_m.status_code == 200:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, student_slug TEXT, data TEXT, nadawca TEXT, temat TEXT, tresc TEXT, przeczytana INTEGER)"
            )

            for m in res_m.json():
                m_k = m.get("apiGlobalKey")
                if not m_k:
                    continue
                box = m.get("skrzynka", "").lower()
                assigned = "unknown"
                for st in students_list:
                    if st["uczen"].lower() in box:
                        assigned = st["slug"]
                        break
                r_det = session.get(
                    f"https://wiadomosci.eduvulcan.pl/{city}/api/WiadomoscSzczegoly?apiGlobalKey={m_k}"
                )
                if r_det.status_code == 200:
                    cursor.execute(
                        "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                        (
                            m_k, assigned, m["data"], m["korespondenci"],
                            m["temat"], r_det.json().get("tresc", "Brak"),
                            1 if m["przeczytana"] else 0,
                        ),
                    )
            conn.commit()

            for st in students_list:
                cursor.execute(
                    "SELECT data, nadawca, temat, tresc, przeczytana FROM messages WHERE student_slug=? OR student_slug='unknown' ORDER BY data DESC LIMIT 10",
                    (st["slug"],),
                )
                rows = cursor.fetchall()
                unread_cur = cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE (student_slug=? OR student_slug='unknown') AND przeczytana=0",
                    (st["slug"],),
                )
                unread = unread_cur.fetchone()[0]
                total_cur = cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE student_slug=? OR student_slug='unknown'",
                    (st["slug"],),
                )
                total = total_cur.fetchone()[0]

                msgs_ha = []
                for r in rows:
                    is_u = int(r[4]) == 0
                    body = r[3] if is_u else ""
                    if is_u and len(body) > 2000:
                        body = body[:1997] + "..."
                    msgs_ha.append(
                        {
                            "data": r[0].replace("T", " ")[:16], "nadawca": r[1],
                            "temat": r[2], "tresc": body, "przeczytana": not is_u,
                        }
                    )
                send_to_ha_sync(
                    f"sensor.vultron_wiadomosci_{st['slug']}",
                    unread,
                    {
                        "wiadomosci": msgs_ha, "friendly_name": f"Wiadomości: {st['uczen']}",
                        "stats": f"{unread} / {total}", "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            conn.close()
        with open(BUL_PKL, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
    except Exception as e:
        logger.error(f"[MESSAGES] Błąd: {e}")
    finally:
        driver.quit()
        display.stop()


# ==========================================================
# GŁÓWNA PĘTLA APLIKACJI
# ==========================================================


async def main_loop():
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    copy_resources()
    await wait_for_ha_api()
    run_setup_ui()

    while True:
        if 1 <= datetime.now().hour <= 5:
            logger.info("Przerwa nocna (01:00-05:59).")
            await asyncio.sleep(1800)
            continue

        logger.info("--- ROZPOCZYNAM PEŁNY CYKL SYNCHRONIZACJI ---")

        # --- NOWY BLOK: WYKRYWANIE RESTARTU HA ---
        try:
            logger.info("--- WYKRYWANIE RESTARTU HA ---")
            # Sprawdzamy, czy w HA nadal istnieje nasza główna encja
            check_url = f"{HA_URL}/states/sensor.vultron_system_monitor"
            r = requests.get(check_url, headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=10)
            if r.status_code == 404:
                logger.warning("Wykryto brak encji w HA (Restart HA?). Wymuszam pełną synchronizację!")
                LAST_SENT_HASHES.clear() # Czyści cache, zmuszając funkcję send_to_ha_sync do wysłania danych
        except Exception as e:
            logger.error(f"Błąd sprawdzania stanu HA: {e}")
        # -----------------------------------------

        # 1. FAZA DZIENNIKA
        students, cookies = await asyncio.to_thread(run_diary_auth)

        if students:
            # Synchronizacja danych dziennika (requests)
            await asyncio.to_thread(sync_diary_data, students, cookies)
            # 2. FAZA WIADOMOŚCI
            await asyncio.to_thread(run_messages_sync, students[0]["city"], students)

        # 3. MONITOR SYSTEMU
        q = {
            "template": "[{% for state in states.sensor if state.entity_id.startswith('sensor.vultron_') and not state.entity_id == 'sensor.vultron_system_monitor' %}{\"id\": \"{{ state.entity_id }}\",\"size\": {{ state.attributes | tojson | length }}}{{ \",\" if not loop.last }}{% endfor %}]"
        }
        try:
            res = requests.post(
                f"{HA_URL}/template",
                headers={"Authorization": f"Bearer {HA_TOKEN}"},
                json=q,
                timeout=15,
            )
            if res.status_code == 200:
                ents = res.json()
                tot = sum(e["size"] for e in ents)
                send_to_ha_sync(
                    "sensor.vultron_system_monitor",
                    tot,
                    {
                        "unit_of_measurement": "B",
                        "szczegoly": " | ".join([f"{e['id']}: {e['size']}B" for e in ents]),
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                send_to_ha_sync(
                    "binary_sensor.vultron_rozmiar_alert",
                    "on" if any(e["size"] > 15500 for e in ents) else "off",
                    {"device_class": "problem"},
                )
        except Exception:
            pass

        wait_time = secrets.SystemRandom().randint(2400, 3600)
        logger.info(f"Cykl zakończony. Kolejna próba za {wait_time // 60} min.")

        # --- ULEPSZONA PĘTLA OCZEKIWANIA (Monitoruje restart HA w locie) ---
        for i in range(wait_time // 10):
            await asyncio.sleep(10)

            # Sprawdzamy stan HA co 60 sekund (6 pętli po 10s)
            if i > 0 and i % 6 == 0:
                try:
                    check_url = f"{HA_URL}/states/sensor.vultron_system_monitor"
                    r = requests.get(check_url, headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=3)

                    if r.status_code == 404:
                        logger.warning("Wykryto brak encji w HA podczas oczekiwania (Restart HA?). Przerywam sen i wymuszam synchronizację!")
                        LAST_SENT_HASHES.clear()
                        break # Wychodzi z pętli for i od razu zaczyna nowy cykl 'while True'

                except Exception:
                    # Ignorujemy błędy połączenia (np. gdy HA jest w trakcie uruchamiania i nie odpowiada)
                    pass
        # -------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        pass