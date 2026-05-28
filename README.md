![GitHub release](https://img.shields.io/github/v/release/htomasz/vultron?style=flat-square)
![GitHub last commit](https://img.shields.io/github/last-commit/htomasz/vultron?style=flat-square&logo=git&logoColor=white)
![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg?style=flat-square)
![GitHub license](https://img.shields.io/github/license/htomasz/vultron?style=flat)
![GitHub issues](https://img.shields.io/github/issues/htomasz/vultron?style=flat-square)
[![Add-on Repository](https://img.shields.io/badge/Home%20Assistant-Add--on%20Repository-blue?style=flat-square&logo=home-assistant)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron)
![Security Scan](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/codeql.yml?branch=main&label=CodeQL&style=flat-square&logo=github&logoColor=white)
![Python Security](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/python-security.yml?branch=main&label=Python%20Security&style=flat-square&logo=python&logoColor=white)
![Docker Linter](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/hadolint.yml?branch=main&label=Docker%20Linter&style=flat-square&logo=docker&logoColor=white)
![Vulnerability Scan](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/trivy.yml?branch=main&label=Trivy&style=flat-square&logo=aquasecurity&logoColor=white)
![Secret Scan](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/gitleaks.yml?branch=main&label=Secrets&style=flat-square&logo=keycdn&logoColor=white)
![Bash Scan](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/bash-security.yml?branch=main&label=Bash&style=flat-square&logo=gnu-bash&logoColor=white)
![Lint & Semgrep](https://img.shields.io/github/actions/workflow/status/htomasz/vultron/lint-semgrep.yml?branch=main&label=Lint%20Semgrep&style=flat-square&logo=npm&logoColor=white)
![Python](https://img.shields.io/badge/Python-blue?style=flat-square&logo=python&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?style=flat-square&logo=selenium&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)
![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?style=flat-square&logo=dependabot&logoColor=white)
[![Open in VS Code](https://img.shields.io/badge/Open%20in-VS%20Code-007ACC?style=flat-square&logo=visual-studio-code&logoColor=white)](https://github.dev/htomasz/vultron)
![Vultron](https://img.shields.io/badge/Vultron-Twój%20asystent%20w%20byciu%20zajebistym%20rodzicem-black?style=flat-square&logo=darkreader&logoColor=white)
<p align="center">
  <img src="icon.png" alt="Vultron Logo" width="500">
<br><b>Używanie projektu jest jawnym łamaniem regulaminu EduVulcan.pl. <br>Nie rób tego.</b>
</p>

# Vultron (Kurkkuviipale)

**Vultron** to **totalnieNIEzaawansowana** integracja Home Assistant z systemem dziennika elektronicznego **EduVulcan.pl**. Dodatek został zaprojektowany, aby dostarczać rodzicom i uczniom kluczowe informacje o edukacji w sposób przejrzysty, zautomatyzowany i bezpieczny.

**Autor:** AI i Tomasz H. \
**Wersja:** 6.3.5 \
**Nazwa Kodowa:** Kurkkuviipale 🐂🏕️⚒️

# 📖 Spis treści
* [🚨 Achtung](#-achtung-achtung-)
* [🧩 Changelog](#-changelog)
* [✨ Główne Funkcje](#-główne-funkcje)
* [🏗️ Architektura Systemu](#️-architektura-systemu)
* [🚀 Instalacja](#-instalacja)
* [⚙️ Konfiguracja](#️-konfiguracja)
* [📊 Konfiguracja Kart Dashboardu](#-konfiguracja-kart-dashboardu)
* [🔄 Automatyzacja](#-automatyzacja)
* [🔍 Monitoring](#-monitoring)
* [📸 Próbki/screenshoty](#-próbkiscreenshoty)
* [🪲 Zgłaszanie błędów](#-zgłaszanie-błędów-i-bezpieczeństwo)
* [🗑️ Odinstalowanie](#️-odinstalowanie)
* [⚖️ Nota prawna](#️-nota-prawna)


## 🚨🚨🚨 Achtung Achtung 🚨🚨🚨
**Przy pierwszym uruchomieniu ZALECANE śledzenie zakładki LOGI**
czy proces logowania przechodzi poprawnie.

**W razie błędów skrypt SAMOCZYNNIE zabije kontener.**

**Przed ponownym startem:**
Sprawdź ręcznie logowanie w oryginalnym dzienniku przez W W W.

## 🧩 Changelog

📝  [Changelog](vultron/CHANGELOG.md)

## ✨ Główne Funkcje

- 👨‍👩‍👧‍👦 **Multi-Student Support:** Automatyczne wykrywanie wszystkich(wszystkie dzieci nasze są) dzieci przypisanych do konta rodzica. Każde dziecko otrzymuje własny zestaw sensorów (np. `adam_nowak`, `jan_kowalski`).
- 📅 **Profesjonalny Plan Lekcji:** Klasyczny układ tabelaryczny z nawigacją tygodniową (poprzedni / obecny / następny — łącznie 3 tygodnie).
- 📈 **Monitoring Ocen:** Śledzenie ocen cząstkowych z systemem powiadomień o nowych wpisach i zmianach.
- 💬 **Uwagi i Pochwały:** Pełny wgląd w zachowanie ucznia z podziałem na wpisy pozytywne, negatywne oraz informacyjne.
- ✉️ **Centrum Wiadomości:** Licznik wiadomości nieprzeczytanych oraz odczytanych wraz z listą ostatnich nadawców i tematów.
- 🎒 **Terminarz Wydarzeń:** Podgląd sprawdzianów, kartkówek i zadań domowych z kolorystycznym rozróżnieniem priorytetów.
- ✔️ **Frekwencja:** Szczegółowe informacje o frekwencji na zajęciach.
- 🏆 **Osiągnięcia:** Szczegółowe informacje o osiągnięciach.
- 👩‍🏫 **Zebrania:** Szczegółowe informacje o zebraniach.
- 📊 **Monitoring:** Monitoring 16KB.
- 🛠️ **Zero-Click UI:** Dodatek automatycznie rejestruje wymagane karty JavaScript w zasobach Lovelace (Resources) przy każdym starcie.
- 🕵️ **System Anty-Detekcyjny:**
  - Zapytania do serwerów Vulcan wysyłane są w losowych odstępach (40-60 min).
  - **Tryb Nocny:** Całkowite wstrzymanie aktywności między 01:00 a 05:59.
- 📝 **Precyzyjne Logowanie:** Wszystkie zdarzenia logowane są z timestampem w formacie `[YYYY-MM-DD HH:MM:SS]`.


## 🏗️ Architektura Systemu

System opiera się na modularnej strukturze współpracujących funkcji:

| Moduł | Role | Opis techniczny |
| :--- | :--- | :--- |
| `vultron.py` | 🔑 Logowanie <br>📝 Oceny <br>💬 Uwagi <br>✉️ Wiadomości <br>📅 Plan lekcji <br>🎒 Zadania <br>✔️ Frekwencja <br>🏆 Osiągnięcia <br>📊 Monitoring <br>🎨 UI Setup <br>⚙️ Orkiestrator <br> 👩‍🏫 Zebrania <br>| Główny silnik aplikacji. Obsługuje logowanie **Selenium Headless** (Panel Rodzica + Panel Wiadomości), ekstrakcję kluczy sesji (`key`), pobieranie ocen, uwag, wiadomości, planu lekcji, zadań, frekwencji i osiągnięć. Zarządza bazą **SQLite** (`vultron.db`), monitoringiem zasobów, automatyczną rejestracją kart w Home Assistant oraz pętlą czasową z mechanizmem anty-detekcji. |
| `vultron-card.js` | 🎨 **Stylizacja** | Karta Lovelace — plan lekcji. |
| `vultron-grades-card.js` | 🎨 **Stylizacja** | Karta Lovelace — oceny. |
| `vultron-messages-card.js` | 🎨 **Stylizacja** | Karta Lovelace — wiadomości. |
| `vultron-stats-card.js` | 🎨 **Stylizacja** | Karta Lovelace — frekwencja. |
| `vultron-osiagniecia-card.js` | 🎨 **Stylizacja** | Karta Lovelace — osiągnięcia. |
| `vultron-uwagi-card.js` | 🎨 **Stylizacja** | Karta Lovelace — uwagi i pochwały. |
| `vultron-work-card.js` | 🎨 **Stylizacja** | Karta Lovelace — zadania domowe i sprawdziany. |
| `vultron-zebrania-card.js` | 🎨 **Stylizacja** | Karta Lovelace — zebrania. |
| `automation/node-red` | 🔄 **Automatyzacje** | Przykładowe przepływy Node-RED. |
| `automation/ha` | 🔄 **Automatyzacje** | Przykładowe natywne automatyzacje Home Assistant. |
| `automation/blueprints` | 🔄 **Automatyzacje** | Przykładowe blueprinty automatyzacji. |
| `lovelace/` | 🎨 **Stylizacja** | Przykładowe konfiguracje kart Lovelace. Zamiast *** wstaw osobe imie_nazwisko |
| `vultron-szczesliwy-numerek-card.js` | 🎨 **Stylizacja** | Karta Lovelace — szczęśliwy numerek. |


## 🚀 Instalacja

Vultron jest dostępny jako standardowe repozytorium Home Assistant.

### 🚨 Metoda 1: Automatyczna (Zalecana)

Kliknij poniższy przycisk, aby dodać repozytorium do swojego Home Assistanta jednym kliknięciem:

[![Dodaj repozytorium do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron)

### 🚨 Coś popsuli w HA :D
Jezeli powyzszy link nie dziala to uzyj:
Ustawienia → Aplikacje → Sklep z aplikacjami → ⋮ → Repozytoria → wpisz URL https://github.com/htomasz/vultron → Dodaj

Po dodaniu repozytorium, wejdź w **Ustawienia -> Dodatki -> Sklep z dodatkami**, wyszukaj **Vultron** i kliknij **Zainstaluj**.

<br>

<details>
<summary><b>Metoda 2: Ręczna / Deweloperska (SSH)</b></summary>

<br>

Jeśli wolisz zainstalować dodatek ręcznie przez konsolę:

1. Zainstaluj dodatek [SSH & Web Terminal](https://github.com/hassio-addons/addon-ssh).
2. Po instalacji wyłącz **Protection mode** oraz włącz **Show in sidebar**.
3. Wejdź w dodatek SSH i przejdź do folderu addons:
```bash
cd /addons
```
4. Sklonuj repozytorium:
```bash
git clone https://github.com/htomasz/vultron.git
```
5. W interfejsie HA przejdź do **Ustawienia -> Dodatki -> Sklep z dodatkami**, kliknij trzy kropki (prawy górny róg) i wybierz **Odśwież**.

</details>

---

## ⚙️ Konfiguracja

W zakładce **Konfiguracja** zainstalowanego dodatku wypełnij dane dostępowe:

| Parametr | Opis | Przykład |
| :--- | :--- | :--- |
| `username` | Adres e-mail do EduVulcan | `rodzic@email.pl` |
| `password` | Hasło do portalu | `TwojeTajneHasło` |
| `Poziom logowania` | Określa szczegółowość logów w zakładce Logi. Domyślnie: info. | `info`,`debug`,`trace` (nie rozumiesz, nie zmieniaj)|
| `Tryb testowy ` | Włączenie tej opcji sprawia, że skrypt całkowicie ignoruje nocne oraz weekendowe przerwy i pobiera dane w trybie ciągłym. Używaj tylko do testowania modyfikacji! | `true`,`false` (nie rozumiesz, nie zmieniaj) |

1. Kliknij **Zapisz**.
2. Kliknij **Uruchom**.

**Ważne:** Przy pierwszym uruchomieniu zalecane jest śledzenie zakładki **Logi**, aby upewnić się, że proces logowania przebiega pomyślnie. Skrypt posiada zabezpieczenie, które w razie błędnego hasła automatycznie zatrzyma kontener, chroniąc Twoje konto przed blokadą.

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
Dodatek posiada wbudowaną funkcję `run_setup_ui()` (część `vultron.py`), która automatycznie dodaje karty do zasobów Lovelace przy każdym starcie. Home Assistant czasami potrzebuje chwili (lub restartu interfejsu), aby "zauważyć" nową ścieżkę `/local/vultron/vultron-*.js`. Jeśli po instalacji nie widzisz kart, przejdź do:
`Ustawienia -> Pulpity sterujące -> Trzy kropki -> Zasoby`
i upewnij się, że wpisy dla Vultrona są obecne na liście.



## 📊 Konfiguracja Kart Dashboardu

Po uruchomieniu dodatku sensory zostaną utworzone automatycznie (np. `sensor.vultron_oceny_jan_kowalski`). Dodaj nową kartę (Manual Card) na swoim Dashboardzie, korzystając z poniższych wzorów:

### 📅 Plan Lekcji (Tabelaryczny z nawigacją)
```yaml
type: custom:vultron-card
entity: sensor.vultron_plan_jan_kowalski_curr
freq_entity: sensor.vultron_freq_jan_kowalski
```

### 📈 Oceny Cząstkowe
```yaml
type: custom:vultron-grades-card
entity: sensor.vultron_oceny_jan_kowalski_p2 # tu sensor ma p1 okres 1 i p2 okres 2
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

### 👩‍🏫 Zebrania
```yaml
type: custom:vultron-zebrania-card
entity: sensor.vultron_zebrania_jan_kowalski
```

### 🍀 Szczęśliwy Numerek
```yaml
type: custom:vultron-szczesliwy-numerek-card
entity: sensor.vultron_szczesliwy_numerek_jan_kowalski
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
## 🔍 Monitoring
Oblicza sumaryczny rozmiar atrybutów wszystkich encji sensor.vultron_* w Home Assistant (w bajtach). Tworzy szczegółowy raport z rozmiarem każdej encji. Generuje listę ostrzeżeń dla encji przekraczających próg ostrzegawczy (14 000 B). Cel: wczesne wykrycie dużych encji, które mogą spowolnić HA lub przekroczyć limity ~16 kB. Sensory tworzone automatycznie i automatycznie aktualizowane.

```yaml
sensor.vultron_system_monitor
binary_sensor.vultron_rozmiar_alert
```
Aby zwizualizować wartosci monitoringu uzyj karty markdown dla sensor.vultron_system_monitor
```yaml
type: markdown
content: >
  <table> {%- set szczegoly = state_attr('sensor.vultron_system_monitor',
  'szczegoly') -%} {%- set last_update =
  state_attr('sensor.vultron_system_monitor', 'last_update') -%} {%- if
  szczegoly -%}
    {%- for item in szczegoly.split(' | ') -%}
      {%- set dane = item.split(': ') -%}
      <tr>
        <td style="padding: 0px 15px 0px 0px; border: none;">{{ dane[0].replace('sensor.vultron_', '') }}</td>
        <td style="padding: 0px; border: none; text-align: right;"><b>{{ dane[1].replace('B', ' B') }}</b></td>
      </tr>
    {%- endfor -%}
    <tr>
      <td colspan="2" style="padding: 4px 0px 0px 0px; border: none; font-size: 0.8em; color: gray;">
        🕐 {{ last_update }}
      </td>
    </tr>
  {%- endif -%} </table>

  {% if is_state('binary_sensor.vultron_rozmiar_alert', 'on') -%} ### ⚠️
  OSTRZEŻENIE! Przekroczono próg 15 500 B dla co najmniej jednej encji. Sprawdź listę powyżej. {%- endif %}
```
aby zwizualizowac alarm uzyj karty encji dla binary_sensor.vultron_rozmiar_alert
```yaml
type: tile
entity: binary_sensor.vultron_rozmiar_alert
vertical: false
features_position: bottom
```

## 🔄 Automatyzacja

IMPLEMENTUJ PO TYM JAK DODATEK WYKONA CAŁY JEDEN CYKL bo inaczej wszystko bedzie powiadomieniem.

### 🔄 Automatyzacje (Blueprints)

Zapomnij o ręcznym kopiowaniu kodu YAML. Dzięki **Blueprints (Schematom)** możesz skonfigurować powiadomienia o ocenach, nieobecnościach czy wiadomościach w kilka sekund za pomocą prostego interfejsu graficznego.

#### 🎓 Jak używać?
1. Kliknij przycisk **Importuj** przy wybranym schemacie.
2. Zatwierdź import w swojej instancji Home Assistant.
3. Wybierz odpowiedni sensor Twojego dziecka (np. `sensor.vultron_oceny_jan_kowalski`).
4. Wybierz telefon, na który mają przychodzić powiadomienia, i kliknij **Zapisz**.

---

#### 📦 Vultron Alert Pack

| Funkcja | Opis | Import |
| :--- | :--- | :---: |
| **Nowe Oceny** | Zaawansowane powiadomienia o ocenach (obsługuje wiele ocen naraz). | [![Importuj Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron%2Fblob%2Fmain%2Fautomation%2Fblueprints%2Foceny.yaml) |
| **Frekwencja** | Alert o nieobecnościach i spóźnieniach z nazwą przedmiotu z planu. | [![Importuj Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron%2Fblob%2Fmain%2Fautomation%2Fblueprints%2Ffrekwencja.yaml) |
| **Zmiana Planu** | Powiadomienia o zastępstwach, odwołanych lekcjach i przeniesieniach. | [![Importuj Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron%2Fblob%2Fmain%2Fautomation%2Fblueprints%2Fplan.yaml) |
| **Uwagi i Pochwały** | Informacja o zachowaniu dziecka z automatycznym doborem emoji (`🌟`/`⚠️`). | [![Importuj Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron%2Fblob%2Fmain%2Fautomation%2Fblueprints%2Fuwagi.yaml) |
| **Wiadomości** | Powiadomienie o nowej wiadomości od nauczyciela lub dyrekcji. | [![Importuj Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fhtomasz%2Fvultron%2Fblob%2Fmain%2Fautomation%2Fblueprints%2Fwiadomosci.yaml) |

---

#### 💡 Personalizacja powiadomień
W sekcji **Akcje** każdego Blueprintf-a możesz używać dynamicznych zmiennych, aby dostosować treść powiadomienia:

*   **Oceny:** `{{ uczen }}`, `{{ przedmiot }}`, `{{ ocena }}`, `{{ opis }}`, `{{ data }}`
*   **Frekwencja:** `{{ uczen }}`, `{{ wiadomosc }}`
*   **Zmiana Planu:** `{{ uczen }}`, `{{ wiadomosc }}`
*   **Uwagi:** `{{ uczen }}`, `{{ kategoria }}`, `{{ tresc }}`, `{{ autor }}`, `{{ wiadomosc }}`
*   **Wiadomości:** `{{ uczen }}`, `{{ nadawca }}`, `{{ temat }}`, `{{ wiadomosc }}`

*Przykład wiadomości:* `{{ uczen }} otrzymał ocenę {{ ocena }} z przedmiotu {{ przedmiot }}!`

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
                moze byc tu druga encja w przypadku planu _curr,_next oraz ocen p1,p2
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
- [uwagi.yaml](./automation/ha/uwagi.yaml#L12-L16) - powiadomienia o zmianach w uwagach
- [wiadomosc.yaml](./automation/ha/wiadomosci.yaml#L12-L16) - powiadomienia o nowych wiadomościach



```yaml
...
alias: "Vultron: Alert Frekwencji"
description: ""
triggers:
  - entity_id:
      - sensor.vultron_freq_jan_kowalski <-- TU WPISZ SWOJĄ ENCJE
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
![Oceny1](samples/oceny1.jpg) ![Oceny3](samples/oceny3.jpg)

#### 💬 Wiadomości
![Wiadomości](samples/wiadomosci.jpg)

#### ⚠️ Uwagi
![Uwagi](samples/uwagi.jpg)

#### 📊 Monitoring
![Monitoring](samples/alert.jpg)

### 🍀 Szczęśliwy Numerek
![Numerek](samples/sznumerek.png)

## ⚠️ Debugowanie
Jeśli napotkasz problemy z logowaniem:
1. Sprawdź zakładkę **Logi** dodatku. Wszystkie błędy są tam opisywane w czasie rzeczywistym.

## 🪲 Zgłaszanie błędów i Bezpieczeństwo

Znalazłeś błąd lub masz pomysł na nową funkcję? Postępuj zgodnie z poniższymi krokami:

1. **Błędy bezpieczeństwa (Security):** Jeśli znalazłeś lukę dotyczącą haseł, sesji, wycieku danych lub prywatności, **NIE OTWIERAJ** publicznego zgłoszenia w Issues. Przeczytaj naszą politykę [🛡️ SECURITY.md](https://github.com/htomasz/vultron/blob/main/SECURITY.md) i postępuj zgodnie z zawartą tam instrukcją prywatnego zgłoszenia.

2. **Błędy techniczne (Bugs):** Jeśli błąd nie dotyczy bezpieczeństwa (np. błąd w planie lekcji, błąd w karcie UI):
   - Sprawdź, czy problem nie został już zgłoszony w [GitHub Issues](https://github.com/htomasz/vultron/issues).
   - Jeśli nie, otwórz nowe zgłoszenie [tutaj](https://github.com/htomasz/vultron/issues).

3. **Sugestie (Features):** Masz pomysł na nową funkcję? Otwórz zgłoszenie typu "Feature Request" w zakładce Issues.

## 🗑️ Odinstalowanie
Jeśli zdecydujesz się usunąć dodatek:
1. Odinstaluj Vultron w zakładce Dodatki.
2. Ręcznie usuń folder `/config/www/vultron`.
3. Usuń wpisy kart (filtr po vultron_) w `Ustawienia -> Pulpity sterujące -> Zasoby`

## ⚖️ Nota prawna
> [!IMPORTANT]
> Projekt **Vultron** jest narzędziem edukacyjnym i służy TYLKO wyłącznie do użytku prywatnego. Autor nie bierze odpowiedzialności za ewentualne blokady kont, błędy w synchronizacji danych czy inne konsekwencje wynikające z automatyzacji dostępu do portalu EduVulcan.pl. Korzystasz z dodatku na własną odpowiedzialność.

## 🏚️️ Łamanie prawa
> [!IMPORTANT]
> Używanie projektu jest jawnym łamaniem regulaminu EduVulcan.pl. Nie rób tego.