#!/usr/bin/python3.6
import json
import time
import pickle
import os
import requests
import sqlite3
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from pyvirtualdisplay import Display

def log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [MESS-FETCH] {message}")

# 1. Konfiguracja
with open('/data/options.json') as f:
    config = json.load(f)

USERNAME, PASSWORD = config.get('username'), config.get('password')
BUL_PKL = '/data/bul.pkl'
VUL_PKL = '/data/vul.pkl'
DB_PATH = '/data/vultron.db'
DATA_TEMP = '/data/messages_cache.json'

if not os.path.exists(VUL_PKL):
    log("BŁĄD: Brak pliku vul.pkl.")
    exit(1)

with open(VUL_PKL, 'rb') as f:
    vul_data = pickle.load(f)
    students = vul_data.get('students', [])
    CITY = students[0].get('city')
    log(f"Wykryte miasto: {CITY}")

display = Display(visible=0, size=(1366, 768))
display.start()

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--disable-extensions")
options.add_argument("--blink-settings=imagesEnabled=false")
options.binary_location = "/usr/bin/chromium-browser"
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 30)

try:
    log("Inicjacja sesji...")
    driver.get('https://eduvulcan.pl/logowanie')

    if os.path.exists(BUL_PKL):
        try:
            with open(BUL_PKL, 'rb') as f:
                cookies = pickle.load(f)
            for cookie in cookies:
                driver.add_cookie(cookie)
            log("Załadowano ciasteczka z bul.pkl")
        except:
            pass

    # Wejście do aplikacji wiadomości
    app_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/App"
    driver.get(app_url)
    time.sleep(5)

    if "Alias" in driver.page_source or "logowanie" in driver.current_url:
        log("Sesja wygasła. Logowanie...")
        driver.get('https://eduvulcan.pl/logowanie')
        time.sleep(2)
        try:
            # Oryginalna logika ramek i przycisku oszczędzania
            driver.switch_to.frame(1)
            driver.find_element(By.ID, 'save-default-button').click()
            driver.switch_to.default_content()
        except:
            pass

        wait.until(EC.visibility_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
        time.sleep(1)
        wait.until(EC.visibility_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))
        driver.get(app_url)
        time.sleep(5)

    # 1. Pobranie listy przez Selenium
    api_list_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/Odebrane?idLastWiadomosc=0&pageSize=10"
    driver.get(api_list_url)
    time.sleep(2)
    messages = json.loads(driver.execute_script("return document.body.innerText"))

    # 2. Przygotowanie sesji requests do pobrania treści
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie.get('name'), cookie.get('value'))

    # Bardzo ważne nagłówki (zachowane oryginały)
    session.headers.update({
        "User-Agent": driver.execute_script("return navigator.userAgent"),
        "Referer": app_url,
        "X-Requested-With": "XMLHttpRequest"
    })

    log(f"Pobieram treści dla {len(messages)} wiadomości...")

    conn = sqlite3.connect(DB_PATH, timeout=20)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS messages
                      (key TEXT PRIMARY KEY, student_slug TEXT, data TEXT,
                       nadawca TEXT, temat TEXT, tresc TEXT, przeczytana INTEGER)''')

    success_count = 0
    for m in messages:
        key = m.get('apiGlobalKey')
        if key:
            # Punkt 4: Precyzyjne dopasowanie studenta do skrzynki
            box_name = m.get('skrzynka', '').lower()
            assigned_slug = "unknown"
            for s in students:
                # Ulepszone dopasowanie
                u_name = s.get('uczen', '').lower()
                if u_name in box_name or (len(u_name.split()) > 1 and u_name.split()[-1] in box_name):
                    assigned_slug = s.get('slug')
                    break

            detail_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/WiadomoscSzczegoly?apiGlobalKey={key}"
            try:
                r = session.get(detail_url, timeout=10)
                if r.status_code == 200:
                    tresc_data = r.json().get('tresc', 'Brak treści')
                    m['tresc'] = tresc_data

                    # 3. ZAPIS DO BAZY
                    cursor.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)",
                        (key, assigned_slug, m.get('data'), m.get('korespondenci'),
                         m.get('temat'), tresc_data, 1 if m.get('przeczytana') else 0))

                    success_count += 1
            except Exception as e:
                m['tresc'] = f"Błąd requests: {str(e)}"

    conn.commit()
    conn.close()

    # Zapis danych do JSON (kompatybilność wsteczna)
    with open(DATA_TEMP, 'w', encoding='utf-8') as f:
        json.dump({"all": messages}, f, ensure_ascii=False)

    # Zapis ciasteczek do bul.pkl
    temp_bul = BUL_PKL + ".tmp"
    with open(temp_bul, 'wb') as f:
        pickle.dump(driver.get_cookies(), f)
    os.replace(temp_bul, BUL_PKL)

    log(f"Zakończono pomyślnie. Zaktualizowano {success_count} wiadomości.")

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {str(e)}")
finally:
    driver.quit()
    display.stop()