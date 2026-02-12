![Vultron](https://img.shields.io/badge/Vultron-Muggeseggele%20🪰🥚🥚=🤏-black?style=flat-square) ![GitHub release](https://img.shields.io/github/v/release/htomasz/vultron?style=flat-square) ![CodeQL](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/codeql.yml?branch=main&label=Security%20Scan&style=flat-square&logo=github&logoColor=white) ![Bash Scan](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/bash-security.yml?branch=main&label=Bash%20Scan&style=flat-square&logo=gnu-bash&logoColor=white)![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?style=flat-square&logo=dependabot)
![GitHub license](https://img.shields.io/github/license/htomasz/vultron?style=flat-square)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12+-blue?style=flat-square&logo=homeassistant&logoColor=white) ![Node-RED](https://img.shields.io/badge/Node--RED-v4-green?style=flat-square&logo=node-red&logoColor=white)
![Python](https://img.shields.io/badge/Python-blue?style=flat-square&logo=python&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black) ![Bash](https://img.shields.io/badge/Bash-293137?style=flat-square&logo=gnu-bash&logoColor=white) ![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)

<p align="center">
  <img src="icon.png" alt="Vultron Logo" width="500">
<br><b>Używanie projektu jest jawnym łamaniem regulaminu EduVulcan.pl. <br>Nie rób tego.</b>
</p>

# Vultron (Muggeseggele)

**Vultron** to **totalnieNIEzaawansowana** integracja Home Assistant z systemem dziennika elektronicznego **EduVulcan.pl**. Dodatek został zaprojektowany, aby dostarczać rodzicom i uczniom kluczowe informacje o edukacji w sposób przejrzysty, zautomatyzowany i bezpieczny.

**Autor:** Tomasz H. i parę AI  
**Wersja:** 3.000009  
**Nazwa Kodowa:** Muggeseggele 🪰🥚🥚=🤏 

## 🚨🚨🚨 ACHTUNG ACHTUNG 🚨🚨🚨
**Przy pierwszym uruchomieniu ZALECANE śledzenie zakładki LOGI**  
czy proces logowania przechodzi poprawnie.

**W razie błędów skrypt SAMOCZYNNIE zabije kontener.**

**Przed ponownym startem:**  
Sprawdź ręcznie logowanie w oryginalnym dzienniku przez W W W.


## 🧩 Changelog

### **3.000009 - „Muggeseggele"**
- Dodano acziwmenety :D
    - vulos.py
    - vultron-osiagniecia-card.js
    - automatyzacji do niej nie będzie - nikt nie ma tak zajebistego dziecka
- Chyba wszystko juz mamy :P
- Wszędzie będzie Glassmorphism + ban
- Koniec rozwoju

### **2.5 - „Peninkulma"**
- Dodano wyświetlanie za co ocena (po najechaniu na ocenę)
    - vulo.py - zmiany w zapisywaniu ocen 
    - Dostosowano karte vultron-grades-card.js
    - Dostosowano automatyzacje Node-RED oraz HA do powiadomień o nowych ocenach
    - Dodano Glassmorphism i artretyzm

### **2.4 - „Iteru"**
- Zrefactoryzowano kod dla vulp.py oraz vulf.py
- Zmieniono days=61 w vuls.py
- Naprawiono "zielonkę kreskę" zeby nie konczyła sie na czwartku.
- Dodano natywne automatyzacje dla HA
- Poprawiono automatyzacje dla Node-RED
- Zaktualizowane karte planu o ładne owalne cosie
- Dodano awaryjne zabijanie kontenera w sytuacji ze `vul.py` logowanie do portalu nie przejdzie.

### **2.3 - „Sheppey"**
- Dodano automatyczne zabijanie kontenera w momencie gdy system wykryje ze nie moze sie zalogowac na strone.
- Czytanie treści wiadomości.
- Karta wiadomosci
    - Po kliknieciu mozna zobaczyć (oraz skopiować :D) treść wiadomosci.

### **2.2 - „Saunakalja"**
- Karta terminarz. oceny
    - Dodano limit
- Karty *.js 
    - Próba ujednolicenia wyglądu
- Dokumentacja
    - Dodano zrzuty ekranów wszystkich kart.
- Automatyzacja
    - Dodano przykładowe automatyzacje w Node-Red (dział automatyzacja)

### **2.1 - „Kenno"**
- Dodano do karty planu
	- Dodano status frekwencji na danym przedmiocie w ciagu dnia (informacja pokaze sie tylko jak nauczyciel ją wprowadzi)
	- Dodano pasek pokazujacy aktualna godzine
	- Dodano inny kolor dla kolumny aktualnego dnia
- Dodano funkcje pobierania frekwencji oraz karte frekwencji lovelace
	- statystyka frekwencji od poczatku roku wraz z procentową reprezentacja
	
### **2.0 - „Poronkusema"**
- Dodano chyba pełna obsługę multi-kinderpunkow

### **1.2.5 - „नीलो चूहा"**
- Dodano sortowanie do kart 
    - karta Oceny - sortowanie (data|subject)
    - karta Terminarz - sortowanie rosnąco, malejąco (desc,asc)
    - karta Uwagi - sortowanie rosnąco, malejąco (desc,asc)

### **1.2.4 - „Shǎbī de Tómǎsī"**
- Karta plan - dodano podział na 2 lekcje o tej samej godzinie. Grupy albo błąd eduvulcan

### **1.2.3 - „Chokochoko Mfunguo"**
- Karta plan - dodano daty do aktualnego tygodnia, oraz dane nauczycieli danego przedmiotu
- Karta oceny - dodano sortowanie
- Karta wiadomosci - dodano sortowanie oraz limit
- Karta uwagi - dodano sortowanie oraz limit

### **1.2.2 - „EKEN 4K :P”**
- Dodano podswietlanie aktywnego dnia na dzienniku
- Dodano sortowanie w zadaniach domowych/sprawdzianach

### **1.2.1 - „Tin short”**
- Dodano informacje o "zwolnieniu uczniów do domu"

### **1.2 – „Messenger Burger”**
- Dodano obsługę
    - wiadomości i licznik nieprzeczytanych.

### **1.1 – „Feedback boobs”**
- Dodano obsługę
    - uwag i pochwał

### **1.0 – „First Contact”**
- Pierwsza wersja integracji z EduVulcan.
- Dodano: 
    - plan lekcji   
    - oceny
    - sprawdziany i zadania



## ✨ Główne Funkcje

- 👨‍👩‍👧‍👦 **Multi-Student Support:** Automatyczne wykrywanie wszystkich dzieci przypisanych do konta rodzica. Każde dziecko otrzymuje własny zestaw sensorów (np. `adam_nowak`, `jan_kowalski`).
- 📅 **Profesjonalny Plan Lekcji:** Klasyczny układ tabelaryczny z nieograniczoną nawigacją tygodniową (poprzedni / obecny / następny).
- 📈 **Monitoring Ocen:** Śledzenie ocen cząstkowych z systemem powiadomień o nowych wpisach i zmianach.
- 💬 **Uwagi i Pochwały:** Pełny wgląd w zachowanie ucznia z podziałem na wpisy pozytywne, negatywne oraz informacyjne.
- ✉️ **Centrum Wiadomości:** Licznik wiadomości nieprzeczytanych oraz odczytanych wraz z listą ostatnich nadawców i tematów.
- 🎒 **Terminarz Wydarzeń:** Podgląd sprawdzianów, kartkówek i zadań domowych z kolorystycznym rozróżnieniem priorytetów.
- ✔️ **Frekwencja** Szczegółowe informacje o frekwencji na zajęciach.
- 🏆 **Osiągnięcia** Szczegółowe informacje o osiągnięciach.
- 🛠️ **Zero-Click UI:** Dodatek automatycznie rejestruje wymagane karty JavaScript w zasobach Lovelace (Resources) przy każdym starcie.
- 🕵️ **System Anty-Detekcyjny:** 
  - Zapytania do serwerów Vulcan wysyłane są w losowych odstępach (40-60 min).
  - **Tryb Nocny:** Całkowite wstrzymanie aktywności bota między 01:00 a 05:59.
- 📝 **Precyzyjne Logowanie:** Wszystkie zdarzenia logowane są z timestampem w formacie `[YYYY-MM-DD HH:MM:SS]`.
  

## 🏗️ Architektura Systemu

System opiera się na modularnej strukturze współpracujących skryptów:

| Moduł | Rola | Opis techniczny |
| :--- | :--- | :--- |
| `vul.py` | 🔑 **Logowanie** | Silnik **Selenium Headless**. Obsługuje logowanie, akceptację cookies (iframe) oraz ekstrakcję unikalnych kluczy sesji (`app_key`) bezpośrednio z nowego Panelu Rodzica. |
| `vul-for-mess.py` |  🔑 **Logowanie** | Silnik **Selenium Headless**. Obsługuje logowanie do panelu Wiadomosci |
| `vulo.py` | 📝 **Oceny** | Pobiera oceny i zarządza bazą **SQLite** (`vultron.db`), porównując stany w celu wykrycia nowych ocen. |
| `vuluw.py` | 💬 **Uwagi** | Pobiera uwagi i pochwały. Monitoruje ID wpisów, umożliwiając automatyzację powiadomień o zachowaniu. |
| `vulm.py` | ✉️ **Wiadomości** | **Wiadomości** Zlicza wiadomości przeczytane i nieprzeczytane. |
| `vulp.py` | 📅 **Plan Lekcji** | Synchronizuje plan zajęć w szerokim zakresie dat, wspierając nawigację w kartach UI. |
| `vuls.py` | 🎒 **Zadania** | Pobiera szczegółowe informacje o sprawdzianach i zadaniach domowych (detale nauczyciela, opisy). |
| `vulf.py` | ✔️ **Frekwencja** | Pobiera szczegółowe informacje o frekwencji na zajęciach. |
| `vulos.py` | 🏆 **Osiągnięcia** | Pobiera szczegółowe informacje osiągnięciach |
| `setup_ui.py` | 🎨 **UI Setup** | Automatycznie dodaje karty do zasobów HA przez, eliminując konfigurację ręczną. |
| `run.sh` | ⚙️ **Orkiestrator** | Skrypt nadrzędny Bash. Zarządza pętlą czasu, kopiowaniem plików UI i anty-detekcją. |
| `vultron-card.js` | 🎨 **Stylizacja** | Karta stylizacji planu lekcji |
| `vultron-grades-card.js` | 🎨 **Stylizacja** | Karta stylizacji ocen |
| `vultron-messages-card.js` | 🎨 **Stylizacja** | Karta stylizacji wiadomości |
| `vultron-stats-card.js` | 🎨 **Stylizacja** | Karta stylizacji frekwencji |
| `vultron-osiagniecia-card.js` | 🎨 **Stylizacja** | Karta stylizacji osiągnięć |
| `vultron-uwagi-card.js` | 🎨 **Stylizacja** | Karta stylizacji uwag i pochwał |
| `vultron-work-card.js` | 🎨 **Stylizacja** | Karta stylizacji zadań domowych oraz sprawdzianów |
| `automation/node-red` | 🔄 **Automatyzacje** | Przykładowe automatyzacje w node-red |
| `automation/ha` | 🔄 **Automatyzacje** | Przykładowe natywne automatyzacje |

  
## 🚀 Instalacja i Konfiguracja
### Automatyczna

[![Dodaj repozytorium do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron)

### Manualna
0. Zainstaluj https://github.com/hassio-addons/app-ssh 
   i po instalacji wYłącz Protection mode oraz wŁącz Show in sidebar
1. Wejdz w dodatek SSH (na sidebar)
2. Skopiuj pliki dodatku do folderu `/addons/vultron` w swojej instalacji Home Assistant.
```bash
cd /
cd addons/
git clone https://github.com/htomasz/vultron.git
```
3. W interfejsie HA przejdź do **Ustawienia -> Dodatki (Aplikacje w HA 2026+) ->**, kliknij trzy kropki i wybierz **Odśwież**.
4. **Zainstaluj dodatek(w HA 2026+ Zainstaluj Aplikacje)**, Local add-ons **Vultron**.
5. W zakładce **Konfiguracja** wypełnij dane dostępowe:

| Parametr | Opis | Przykład |
| :--- | :--- | :--- |
| `username` | Adres e-mail do EduVulcan | `rodzic@email.pl` |
| `password` | Hasło do portalu | `TwojeTajneHasło` |


5. Uruchom dodatek.
6. Usuń ciasteczka (aby przeładowac karty *.js).
7. Zaloguj się ponownie.

---

### 💡 Ważna uwaga dotycząca kart UI i odświeżania

Z powodu sposobu, w jaki Home Assistant oraz przeglądarki internetowe zarządzają plikami interfejsu (Lovelace), po instalacji dodatku lub jego aktualizacji możesz napotkać problemy z wyświetlaniem kart (np. błąd `Custom element doesn't exist` lub brak nowych funkcji). 

Oto jak sobie z tym poradzić:

#### 1. "Zwykłe" Odświeżanie vs "Twarde" Odświeżanie
Przeglądarki często przechowują starą wersję plików `.js`, aby przyspieszyć ładowanie strony. Jeśli karta nie wygląda tak, jak powinna:
*   **Na komputerze:** Użyj kombinacji **`Ctrl + F5`** (Windows/Linux) lub **`Cmd + Shift + R`** (Mac). Wymusza to na przeglądarce ponowne pobranie plików z serwera Home Assistant zamiast czytania ich z dysku.
*   **W aplikacji mobilnej:** Zamknij całkowicie aplikację Home Assistant i uruchom ją ponownie. Możesz również wejść w *Ustawienia -> Aplikacja towarzysząca -> Debugowanie -> Wyczyść pamięć podręczną*.

#### 2. Kiedy wyczyścić ciasteczka i dane strony?
Jeśli "Twarde odświeżanie" nie pomaga, może to oznaczać, że w pamięci podręcznej przeglądarki utknął błędny stan zasobów. 
*   W takim przypadku zalecane jest wyczyszczenie danych podręcznych dla adresu IP/domeny Twojego Home Assistanta.
*   **Wskazówka:** Często najszybszym testem jest otwarcie panelu w **trybie Inkognito**. Jeśli tam karty działają poprawnie, oznacza to, że Twoja główna sesja przeglądarki wymaga czyszczenia cache.

#### 3. Rejestracja Zasobów
Mimo że dodatek posiada moduł `setup_ui.py`, który automatycznie dodaje karty do zasobów, Home Assistant czasami potrzebuje chwili (lub restartu interfejsu), aby "zauważyć" nową ścieżkę `/local/vultron/vultron-*.js`. Jeśli po instalacji nie widzisz kart, przejdź do:
`Ustawienia -> Pulpity sterujące -> Trzy kropki -> Zasoby`
i upewnij się, że wpisy dla Vultrona są obecne na liście.



## 📊 Konfiguracja Kart Dashboardu

Po uruchomieniu dodatku sensory zostaną utworzone automatycznie (np. `sensor.vultron_oceny_jan_kowalski`). Dodaj nową kartę (Manual Card) na swoim Dashboardzie, korzystając z poniższych wzorów:

### 📅 Plan Lekcji (Tabelaryczny z nawigacją)
```yaml
type: custom:vultron-card
entity: sensor.vultron_plan_jan_kowalski
freq_entity: sensor.vultron_freq_jan_kowalski
```

### 📈 Oceny Cząstkowe
```yaml
type: custom:vultron-grades-card
entity: sensor.vultron_oceny_jan_kowalski
default_sort: date or subject
limit: 10   #0 - pokazuje wszystkie
```

### ✉️ Wiadomości (Licznik i Lista)
```yaml
type: custom:vultron-messages-card
entity: sensor.vultron_wiadomosci_jan_kowalski
limit: 10   #0 - pokazuje wszystkie
```

### 💬 Uwagi i Pochwały
```yaml
type: custom:vultron-uwagi-card
entity: sensor.vultron_uwagi_jan_kowalski
default_sort: desc or asc
limit: 10   #0 - pokazuje wszystkie
```

### 🎒 Terminarz (Sprawdziany i Zadania)
```yaml
type: custom:vultron-work-card
entity: sensor.vultron_terminarz_jan_kowalski
default_sort: desc or asc
limit: 10   #0 - pokazuje wszystkie
```

### ✔️  Frekwencja
```yaml
type: custom:vultron-stats-card
entity: sensor.vultron_stats_jan_kowalski
```

### 🏆 Osiągnięcia
```yaml
type: custom:vultron-osiagniecia-card
entity: sensor.vultron_osiagniecia_jan_kowalski
```

mozna też użyć
```yaml
- type: gauge
  entity: sensor.vultron_stats_jan_kowalski
  min: 0
  max: 100
  name: Frekwencja Jan Kowalski
  needle: true
  severity:
    green: 80
    yellow: 50
    red: 0
```
## 🔄 Automatyzacja

IMPLEMENTUJ PO TYM JAK DODATEK WYKONA CAŁY JEDEN CYKL bo inaczej wszystko bedzie powiadomieniem.

### 🛑 Node-RED

Do działania wymagany jest [node-red-contrib-home-assistant-websocket](https://flows.nodered.org/node/node-red-contrib-home-assistant-websocket) dla Node-RED. (najprościej zainstalowac poprzez manage-palette)

Ponizsze automatyzacje instaluje się poprzez import i wklej :D

W plikach

- [plan.json](./automation/node-red/plan.json#L12-L16) - powiadomienia o zmianach w planie
- [frekwencja.json](./automation/node-red/frekwencja.json#L12-L16) - powiadomienia o zmianach we frekwencji
- [oceny.json](./automation/node-red/oceny.json#L12-L16) - powiadomienia o zmianach w ocenach
- [terminarz.json](./automation/node-red/terminarz.json#L12-L16) - powiadomienia o zmianach w zdaniach domowych/sprawdzianach
- [uwagi.json](./automation/node-red/uwagi.json#L12-L16) - powiadomienia o zmianach w uwagach
- [wiadomosc.json](./automation/node-red/wiadomosci.json#L12-L16) - powiadomienia o nowych wiadomościach
- [patusek.json](./automation/node-red/patusek.json#L12-L16) - wyjscie do "odłącz prąd i zablokuj MAC" :D

odszukaj sekcję `entities` i zmień nazwę sensora.

```json
...
[
    {
        "id": "vultron_plan_trigger",
        "type": "server-state-changed",
        "z": "vultron_grades_flow",
        "name": "Zmiana w Planie",
        "server": "a8398b8a.edbcf8",
        "version": 6,
        "outputs": 1,
        "exposeAsEntityConfig": "",
        "entities": {
            "entity": [
                "sensor.vultron_plan_jan_kowalski" <-- TU WPISZ SWOJĄ ENCJE
            ],
            "substring": [],
            "regex": []
        },
...
```

### 🏠 HA Automations

Najprosciej dodać:  

Ustawienia -> Automatyzacje oraz sceny -> Utwórz automatyzację  -> Utwórz nową automatyzację -> ⋮ -> Edycja w YAML -> Wklej i zmien "entity"

- [plan.yaml](./automation/ha/plan.yaml#L12-L16) - powiadomienia o zmianach w planie
- [frekwencja.yaml](./automation/ha/frekwencja.yaml#L12-L16) - powiadomienia o zmianach we frekwencji
- [oceny.yaml](./automation/ha/oceny.yaml#L12-L16) - powiadomienia o zmianach w ocenach
- [terminarz.yaml](./automation/ha/terminarz.yaml#L12-L16) - powiadomienia o zmianach w zdaniach domowych/sprawdzianach
- [uwagi.yaml](./automation/ha/uwagi.yaml#L12-L16) - powiadomienia o zmianach w uwagach
- [wiadomosc.yaml](./automation/ha/wiadomosci.yaml#L12-L16) - powiadomienia o nowych wiadomościach
  


```yaml
...
alias: "Vultron: Alert Frekwencji"
description: ""
triggers:
  - entity_id:
      - sensor.vultron_freq_jan_kowalski <-- TU WPISZ SWOJĄ ENCJEENCJE
    attribute: wpisy
    trigger: state
actions:
...
```


## 📸 Próbki/screenshoty

#### 📚 Plan lekcji
![Plan lekcji](samples/planlekcji.jpg)

#### 📅 Terminarz
![Terminarz](samples/terminarz.jpg)

#### 📊 Frekwencja
![Frekwencja](samples/frekwencja.jpg)

#### 📝 Oceny
![Oceny1](samples/oceny1.jpg) ![Oceny2](samples/oceny2.jpg)

#### 💬 Wiadomości
![Wiadomości](samples/wiadomosci.jpg)

#### ⚠️ Uwagi
![Uwagi](samples/uwagi.jpg)


## ⚠️ Debugowanie
Jeśli napotkasz problemy z logowaniem:
1. Sprawdź zakładkę **Logi** dodatku. Wszystkie błędy są tam opisywane w czasie rzeczywistym.

## ⚖️ Nota prawna
> [!IMPORTANT]
> Projekt **Vultron** jest narzędziem edukacyjnym i służy wyłącznie do użytku prywatnego. Autor nie bierze odpowiedzialności za ewentualne blokady kont, błędy w synchronizacji danych czy inne konsekwencje wynikające z automatyzacji dostępu do portalu EduVulcan.pl. Korzystasz z dodatku na własną odpowiedzialność.

## 🏚️️ Łamanie prawa
> [!IMPORTANT]
> Używanie projektu jest jawnym łamaniem regulaminu EduVulcan.pl. Nie rób tego.