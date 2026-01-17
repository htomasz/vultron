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
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] [AUTH] {message}")

def slugify(text):
    chars = {'ą':'a','ć':'c','ę':'e','ł':'l','ń':'n','ó':'o','ś':'s','ź':'z','ż':'z'}
    text = text.lower()
    for k, v in chars.items(): text = text.replace(k, v)
    return re.sub(r'[^a-z0-9]', '_', text).strip('_')

with open('/data/options.json') as f:
    config = json.load(f)

USERNAME, PASSWORD = config.get('username'), config.get('password')
display = Display(visible=0, size=(1366, 768))
display.start()

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1366,768")
options.binary_location = "/usr/bin/chromium-browser"
driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, 30)

try:
    log(f"Inicjacja logowania: {USERNAME}")
    driver.get('https://eduvulcan.pl/logowanie')

    # Cookies
    try:
        time.sleep(3)
        driver.switch_to.frame(1)
        wait.until(EC.element_to_be_clickable((By.ID, 'save-default-button'))).click()
        driver.switch_to.default_content()
    except:
        driver.switch_to.default_content()

    # Logowanie
    wait.until(EC.visibility_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
    time.sleep(2)
    wait.until(EC.visibility_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
    
    # Czekamy na załadowanie modułu
    wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'user-info')] | //a[contains(@href, 'dziennik')]")))
    time.sleep(5)

    students_found = []
    links = [l.get_attribute('href') for l in driver.find_elements(By.XPATH, "//a[contains(@href, 'dziennik')]")]
    if not links: links = [driver.current_url]

    for h in links:
        driver.get(h)
        # Czekamy na subdomenę uczen
        for _ in range(15):
            if "uczen.eduvulcan.pl" in driver.current_url: break
            time.sleep(1)
        
        curr_url = driver.current_url
        parts = curr_url.split('/')
        
        if "App" in parts:
            app_key = parts[parts.index("App") + 1]
            name = None
            
            # NOWE SELEKTORY DLA TWOJEGO WIDOKU
            selectors = [
                "//div[contains(@class, 'user-info')]//h3",
                "//div[contains(@class, 'user-info')]//span[1]",
                "//div[contains(text(), 'PANEL RODZICA')]/following-sibling::div",
                "//aside//div[2]/div[1]", # Częsta struktura paska bocznego
                "//div[contains(@class, 'panel-parent-name')]"
            ]
            
            for sel in selectors:
                try:
                    el = driver.find_element(By.XPATH, sel)
                    txt = el.text.strip()
                    if txt and len(txt) > 2 and "PANEL" not in txt.upper():
                        name = txt.split('\n')[0] # Bierzemy tylko pierwszą linię (imię bez klasy)
                        break
                except: continue
            
            if not name: name = f"Student_{len(students_found)+1}"

            students_found.append({
                'name': name,
                'slug': slugify(name),
                'key': app_key
            })
            log(f"Zidentyfikowano: {name} (Slug: {slugify(name)})")

    if students_found:
        unique = {s['slug']: s for s in students_found}.values()
        with open('/data/vul.pkl', 'wb') as f:
            pickle.dump({'cookies': driver.get_cookies(), 'students': list(unique)}, f)
        log(f"Sukces! Zapisano {len(unique)} profili.")

except Exception as e: log(f"BŁĄD: {str(e)}")
finally:
    driver.quit()
    display.stop()
