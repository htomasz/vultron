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

with open('/data/options.json') as f:
    config = json.load(f)

USERNAME, PASSWORD = config.get('username'), config.get('password')
CITY = config.get('city_slug')
MY_PICKLE = '/data/bul.pkl'
DATA_TEMP = '/data/messages_cache.json'

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
    log("Inicjacja niezależnej sesji dla wiadomości...")
    driver.get('https://eduvulcan.pl/logowanie')

    if os.path.exists(MY_PICKLE):
        try:
            with open(MY_PICKLE, 'rb') as f:
                cookies = pickle.load(f)
            for cookie in cookies:
                driver.add_cookie(cookie)
        except: pass

    driver.get('https://eduvulcan.pl/logowanie')
    time.sleep(2)
    
    if "Alias" in driver.page_source:
        log("Logowanie...")
        try:
            driver.switch_to.frame(1)
            wait.until(EC.element_to_be_clickable((By.ID, 'save-default-button'))).click()
            driver.switch_to.default_content()
        except: pass

        wait.until(EC.visibility_of_element_located((By.ID, "Alias"))).send_keys(USERNAME + Keys.ENTER)
        time.sleep(1)
        wait.until(EC.visibility_of_element_located((By.ID, "Password"))).send_keys(PASSWORD + Keys.ENTER)
        wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'dziennik')]")))

    log("Przejście do subdomeny wiadomości...")
    driver.get(f"https://wiadomosci.eduvulcan.pl/{CITY}/App")
    time.sleep(10)

    api_url = f"https://wiadomosci.eduvulcan.pl/{CITY}/api/Odebrane?idLastWiadomosc=0&pageSize=50"
    log(f"Pobieram JSON z: {api_url}")
    driver.get(api_url)
    time.sleep(5)

    raw_content = driver.find_element(By.TAG_NAME, "body").text
    if not raw_content.strip().startswith('['):
        raw_content = driver.find_element(By.TAG_NAME, "pre").text

    if raw_content.strip().startswith('['):
        messages = json.loads(raw_content)
        with open(DATA_TEMP, 'w', encoding='utf-8') as f:
            json.dump({"all": messages}, f, ensure_ascii=False)
        with open(MY_PICKLE, 'wb') as f:
            pickle.dump(driver.get_cookies(), f)
        log(f"Sukces! Pobrano {len(messages)} wiadomości.")

except Exception as e:
    log(f"BŁĄD: {str(e)}")
finally:
    driver.quit()
    display.stop()
