#!/usr/bin/python3.6
import json, time, pickle, os, re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from pyvirtualdisplay import Display

def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [AUTH] {message}")

def slugify(text):
    chars = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z'}
    text = text.lower()
    for k, v in chars.items(): text = text.replace(k, v)
    return re.sub(r'[^a-z0-9]', '_', text).strip('_')

# Ładowanie konfigu
with open('/data/options.json') as f:
    config = json.load(f)

USERNAME, PASSWORD = config.get('username'), config.get('password')

display = Display(visible=0, size=(1366, 768))
display.start()

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.binary_location = "/usr/bin/chromium-browser"
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 20) # Skrócony timeout dla szybszej reakcji

def get_json_from_page():
    """Pobiera tekst JSON bezpośrednio z body strony, ignorując tagi HTML."""
    try:
        # Próba wyciągnięcia tekstu przez JavaScript - najpewniejsza metoda
        content = driver.execute_script("return document.body.innerText")
        return json.loads(content)
    except Exception as e:
        log(f"Błąd parsowania JSON: {str(e)[:50]}")
        return None

try:
    log("Inicjacja logowania do eduvulcan.pl...")
    driver.get('https://eduvulcan.pl/logowanie')

    # Logowanie
    wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
    time.sleep(2)
    wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
    
    # Czekamy na kafelki
    log("Czekam na panel z dziennikami...")
    wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))
    
    # Bierzemy pierwszy link, żeby "odpalić" sesję ucznia
    child_link = driver.find_element(By.XPATH, "//a[contains(@href, 'dziennik')]").get_attribute('href')
    driver.get(child_link)
    
    # Czekamy na przeskoczenie na subdomenę uczen
    time.sleep(5)
    
    # Wyciągamy miasto z URL
    city_match = re.search(r'uczen.eduvulcan.pl/([^/]+)', driver.current_url)
    if not city_match:
        log("BŁĄD: Nie udało się wykryć miasta z adresu URL.")
        driver.quit()
        exit(1)
    
    CITY = city_match.group(1)
    log(f"Wykryte miasto: {CITY}. Pobieram Context...")

    # Wchodzimy na Context
    driver.get(f"https://uczen.eduvulcan.pl/{CITY}/api/Context")
    time.sleep(2)
    context_data = get_json_from_page()

    if not context_data or 'uczniowie' not in context_data:
        log("BŁĄD: Nie udało się pobrać danych z Context API.")
        # Debug: zapisz co widzi przeglądarka
        log(f"Treść strony: {driver.page_source[:200]}")
        raise Exception("Pusty Context")

    students_to_save = []
    now = datetime.now()

    for u in context_data['uczniowie']:
        name = u['uczen']
        log(f"Przetwarzanie dziecka: {name}")
        
        # Pobieramy okresy
        driver.get(f"https://uczen.eduvulcan.pl/{CITY}/api/OkresyKlasyfikacyjne?key={u['key']}&idDziennik={u['idDziennik']}")
        time.sleep(2)
        okresy_list = get_json_from_page()
        
        current_period_id = None
        if okresy_list:
            for o in okresy_list:
                try:
                    d_od = datetime.fromisoformat(o['dataOd'].split('+')[0].split('Z')[0])
                    d_do = datetime.fromisoformat(o['dataDo'].split('+')[0].split('Z')[0])
                    if d_od <= now <= d_do:
                        current_period_id = o['id']
                        break
                except: continue
            
            if not current_period_id:
                current_period_id = okresy_list[-1]['id']

        # Kopiujemy dane i dodajemy nasze pola
        s_info = u.copy()
        s_info['city'] = CITY
        s_info['periodId'] = current_period_id
        s_info['slug'] = slugify(name)
        students_to_save.append(s_info)

    if students_to_save:
        with open('/data/vul.pkl', 'wb') as f:
            pickle.dump({
                'cookies': driver.get_cookies(),
                'students': students_to_save
            }, f)
        log(f"SUKCES! Zidentyfikowano {len(students_to_save)} uczniów.")
    else:
        log("BŁĄD: Nie znaleziono uczniów w Context.")

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {str(e)}")
finally:
    driver.quit()
    display.stop()
