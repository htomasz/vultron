#!/usr/bin/python3
import json, time, pickle, os, re, sys
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
wait = WebDriverWait(driver, 10)

def get_json_from_page():
    try:
        content = driver.execute_script("return document.body.innerText")
        return json.loads(content)
    except Exception as e:
        log(f"Błąd parsowania JSON: {str(e)[:50]}")
        return None

try:
    log("Inicjacja logowania do eduvulcan.pl...")
    driver.get('https://eduvulcan.pl/logowanie')

    # Wpisanie loginu
    wait.until(EC.presence_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
    time.sleep(1.5)
    
    # Wpisanie hasła
    wait.until(EC.presence_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
    
    log("Weryfikacja poświadczeń...")
    
    # Pętla sprawdzająca co się stało po kliknięciu "Zaloguj"
    authenticated = False
    for _ in range(15): # sprawdzaj przez ok 7-8 sekund
        page_source = driver.page_source
        
        # Sprawdzenie błędu hasła
        if "Zła nazwa użytkownika lub hasło" in page_source:
            log(" ")
            log("!!! ===================================================================== !!!")
            log("!!! BŁĄD KRYTYCZNY: Nieprawidłowe hasło do EduVulcan. Przerywam wszystko. !!!")
            log("!!!     ZALOGUJ SIE PRZEZ STRONE EDUVULCAN.PL I SPRAWDZ LOGIN I HASLO     !!!")
            log("!!! ===================================================================== !!!")
            log(" ")
            driver.quit()
            display.stop()
            sys.exit(1) # Zwracamy kod 1 do Basha
            
        # Sprawdzenie sukcesu (czy pojawiły się kafelki)
        if len(driver.find_elements(By.XPATH, "//a[contains(@href, 'dziennik')]")) > 0:
            authenticated = True
            log("Zalogowano pomyślnie.")
            break
            
        time.sleep(0.5)

    if not authenticated:
        raise Exception("Timeout przy logowaniu lub nieznany błąd strony.")

    # Bierzemy pierwszy link, żeby "odpalić" sesję ucznia
    child_link = driver.find_element(By.XPATH, "//a[contains(@href, 'dziennik')]").get_attribute('href')
    driver.get(child_link)
    
    time.sleep(5)
    
    city_match = re.search(r'uczen.eduvulcan.pl/([^/]+)', driver.current_url)
    if not city_match:
        log("BŁĄD: Nie udało się wykryć miasta.")
        sys.exit(1)
    
    CITY = city_match.group(1)
    log(f"Wykryte miasto: {CITY}. Pobieram Context...")

    driver.get(f"https://uczen.eduvulcan.pl/{CITY}/api/Context")
    time.sleep(2)
    context_data = get_json_from_page()

    if not context_data or 'uczniowie' not in context_data:
        raise Exception("Pusty Context API")

    students_to_save = []
    now = datetime.now()

    for u in context_data['uczniowie']:
        name = u['uczen']
        log(f"Przetwarzanie dziecka: {name}")
        
        driver.get(f"https://uczen.eduvulcan.pl/{CITY}/api/OkresyKlasyfikacyjne?key={u['key']}&idDziennik={u['idDziennik']}")
        time.sleep(2)
        okresy_list = get_json_from_page()
        
        current_period_id = None
        if okresy_list:
            for o in okresy_list:
                try:
                    # Naprawa dla Python 3.6/3.7+
                    d_od = datetime.strptime(o['dataOd'][:19], '%Y-%m-%dT%H:%M:%S')
                    d_do = datetime.strptime(o['dataDo'][:19], '%Y-%m-%dT%H:%M:%S')
                    if d_od <= now <= d_do:
                        current_period_id = o['id']
                        break
                except: continue
            
            if not current_period_id:
                current_period_id = okresy_list[-1]['id']

        s_info = u.copy()
        s_info['city'] = CITY
        s_info['periodId'] = current_period_id
        s_info['slug'] = slugify(name)
        students_to_save.append(s_info)

    if students_to_save:
        with open('/data/vul.pkl', 'wb') as f:
            pickle.dump({'cookies': driver.get_cookies(), 'students': students_to_save}, f)
        log(f"SUKCES! Zidentyfikowano {len(students_to_save)} uczniów.")
    else:
        log("BŁĄD: Nie znaleziono uczniów.")
        sys.exit(1)

except Exception as e:
    log(f"BŁĄD KRYTYCZNY: {str(e)}")
    sys.exit(1)
finally:
    driver.quit()
    display.stop()

