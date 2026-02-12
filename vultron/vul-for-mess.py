#!/usr/bin/python3.6
import json, time, pickle, os, requests
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
DATA_TEMP = '/data/messages_cache.json'

if not os.path.exists(VUL_PKL):
    log("BŁĄD: Brak pliku vul.pkl.")
    exit(1)

with open(VUL_PKL, 'rb') as f:
    vul_data = pickle.load(f)
    CITY = vul_data['students'][0]['city']
    log(f"Wykryte miasto: {CITY}")

display = Display(visible=0, size=(1366, 768))
display.start()

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
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
        except: pass

    # Wejście do aplikacji wiadomości
    app_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/App"
    driver.get(app_url)
    time.sleep(5)
    
    if "Alias" in driver.page_source or "logowanie" in driver.current_url:
        log("Sesja wygasła. Logowanie...")
        driver.get('https://eduvulcan.pl/logowanie')
        time.sleep(2)
        try:
            driver.switch_to.frame(1)
            driver.find_element(By.ID, 'save-default-button').click()
            driver.switch_to.default_content()
        except: pass

        wait.until(EC.visibility_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
        time.sleep(1)
        wait.until(EC.visibility_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))
        driver.get(app_url)
        time.sleep(5)

    # 1. Pobranie listy przez Selenium (metoda pewna)
    api_list_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/Odebrane?idLastWiadomosc=0&pageSize=50"
    driver.get(api_list_url)
    time.sleep(2)
    messages = json.loads(driver.execute_script("return document.body.innerText"))

    # 2. Przygotowanie sesji requests do pobrania treści
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie['name'], cookie['value'])
    
    # Bardzo ważne nagłówki, żeby Vulcan nie odrzucił połączenia
    session.headers.update({
        "User-Agent": driver.execute_script("return navigator.userAgent"),
        "Referer": f"https://wiadomosci.eduvulcan.pl/{CITY}/App",
        "X-Requested-With": "XMLHttpRequest"
    })

    log(f"Pobieram treści dla {len(messages)} wiadomości...")
    
    success_count = 0
    for m in messages:
        key = m.get('apiGlobalKey')
        if key:
            detail_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/WiadomoscSzczegoly?apiGlobalKey={key}"
            try:
                r = session.get(detail_url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    # Zapisujemy treść. Jeśli JSON jest w innym formacie, próbujemy go wyłuskać
                    m['tresc'] = data.get('tresc', 'Brak treści wewnątrz JSON')
                    success_count += 1
                else:
                    m['tresc'] = f"Błąd HTTP {r.status_code}"
            except Exception as e:
                m['tresc'] = f"Błąd requests: {str(e)}"
    
    log(f"Pobrano treści dla {success_count} wiadomości.")

    # Zapis danych do JSON
    with open(DATA_TEMP, 'w', encoding='utf-8') as f:
        json.dump({"all": messages}, f, ensure_ascii=False)
    
    # Zapis ciasteczek
    with open(BUL_PKL, 'wb') as f:
        pickle.dump(driver.get_cookies(), f)
            
    log("Zakończono pomyślnie.")

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {str(e)}")
finally:
    driver.quit()
    display.stop()

