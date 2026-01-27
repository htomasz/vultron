#!/usr/bin/python3.6
import json, time, pickle, os
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

# 1. Konfiguracja ścieżek
with open('/data/options.json') as f:
    config = json.load(f)

USERNAME, PASSWORD = config.get('username'), config.get('password')
BUL_PKL = '/data/bul.pkl'      # Plik sesji wiadomości (tylko ciasteczka)
VUL_PKL = '/data/vul.pkl'      # Plik z danymi uczniów (stąd bierzemy miasto)
DATA_TEMP = '/data/messages_cache.json'

# 2. Pobranie miasta z vul.pkl (zamiast z configa)
if not os.path.exists(VUL_PKL):
    log("BŁĄD: Brak pliku vul.pkl. Nie mogę określić miasta.")
    exit(1)

with open(VUL_PKL, 'rb') as f:
    vul_data = pickle.load(f)
    # Pobieramy miasto od pierwszego ucznia na liście
    if vul_data.get('students'):
        CITY = vul_data['students'][0]['city']
        log(f"Wykryte miasto z sesji: {CITY}")
    else:
        log("BŁĄD: Plik vul.pkl nie zawiera danych uczniów.")
        exit(1)

display = Display(visible=0, size=(1366, 768))
display.start()

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.binary_location = "/usr/bin/chromium-browser"
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 30)

try:
    log("Inicjacja sesji dla wiadomości...")
    driver.get('https://eduvulcan.pl/logowanie')

    # Ładowanie ciasteczek z pliku bul.pkl (jeśli istnieje)
    if os.path.exists(BUL_PKL):
        try:
            with open(BUL_PKL, 'rb') as f:
                cookies = pickle.load(f)
            for cookie in cookies:
                driver.add_cookie(cookie)
            log("Załadowano ciasteczka z bul.pkl")
        except: pass

    # Próba wejścia bezpośrednio do wiadomości
    driver.get(f"https://wiadomosci.eduvulcan.pl/{CITY}/App")
    time.sleep(5)
    
    # Jeśli wylądowaliśmy na stronie logowania, logujemy się ponownie
    if "Alias" in driver.page_source or "logowanie" in driver.current_url:
        log("Sesja wygasła. Logowanie...")
        driver.get('https://eduvulcan.pl/logowanie')
        time.sleep(2)
        
        try:
            driver.switch_to.frame(1)
            driver.find_element(By.ID, 'save-default-button').click()
            driver.switch_to.default_content()
        except:
            driver.switch_to.default_content()

        wait.until(EC.visibility_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
        time.sleep(1)
        wait.until(EC.visibility_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))

        # Po zalogowaniu przechodzimy do wiadomości
        driver.get(f"https://wiadomosci.eduvulcan.pl/{CITY}/App")
        time.sleep(10)

    # Pobieranie danych API
    api_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/Odebrane?idLastWiadomosc=0&pageSize=50"
    log(f"Pobieram JSON z: {api_url}")
    driver.get(api_url)
    time.sleep(5)

    # Odczyt treści JSON
    raw_content = driver.execute_script("return document.body.innerText")

    if raw_content.strip().startswith('['):
        messages = json.loads(raw_content)
        # Zapisujemy wiadomości do cache'u
        with open(DATA_TEMP, 'w', encoding='utf-8') as f:
            json.dump({"all": messages}, f, ensure_ascii=False)
            
        # Zapisujemy TYLKO ciasteczka do bul.pkl (zgodnie z oryginałem)
        with open(BUL_PKL, 'wb') as f:
            pickle.dump(driver.get_cookies(), f)
            
        log(f"Sukces! Pobrano {len(messages)} wiadomości. Sesja zapisana w bul.pkl.")
    else:
        log("BŁĄD: Serwer nie zwrócił poprawnego JSON-a.")

except Exception as e:
    log(f"BŁĄD: {str(e)}")
finally:
    driver.quit()
    display.stop()
