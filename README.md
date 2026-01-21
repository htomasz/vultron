![Vultron](https://img.shields.io/badge/Vultron-Kalsarik%C3%A4nnit🛋️🩲🍺-663399?style=flat-square)
![GitHub license](https://img.shields.io/github/license/htomasz/vultron?style=flat-square)
![GitHub release](https://img.shields.io/github/v/release/htomasz/vultron?style=flat-square)

<p align="center">
  <img src="icon.png" alt="Vultron Logo" width="500">
</p>

# Vultron (Kalsarikännit)

**Vultron** to NIEzaawansowana integracja Home Assistant z systemem dziennika elektronicznego **EduVulcan**. Dodatek został zaprojektowany, aby dostarczać rodzicom i uczniom kluczowe informacje o edukacji w sposób przejrzysty, zautomatyzowany i bezpieczny.

**Autor:** Tomasz H. i pare AI  
**Wersja:** 1.2  
**Nazwa Kodowa:** Kalsarikännit 🛋️🩲🍺 

---

## 🧩 Changelog

### **1.0 – „First Contact”**
- Pierwsza wersja integracji z EduVulcan.  
- Dodano: 
    - plan lekcji  
    - oceny 
    - sprawdziany i zadania

### **1.1 – „Feedback boobs”**
- Dodano obsługę 
    - uwag i pochwał

### **1.2 – „Messenger Burger”**
- Dodano obsługę 
    - wiadomości i licznik nieprzeczytanych.  

### **1.2.1 - „Tin short”**
- Dodano informacje o "zwolnieniu uczniów do domu"

### **1.2.2 - „EKEN 4K :P”**
- Dodano podswietlanie aktywnego dnia na dzienniku
- Dodano sortowanie w zadaniach domowych/sprawdzianach

### **1.2.3 - „Chokochoko Mfunguo"**
- Karta plan - dodano daty do aktulnego tygodnia, oraz dane nauczycieli danego prezdmiotu
- Karta oceny - dodano sortowanie
- Karta wiadomosci - dodano sortowanie oraz limit
- Karta uwagi - dodano sortowanie oraz limit

---

## ✨ Główne Funkcje

- 👨‍👩‍👧‍👦 **Multi-Student Support:** Automatyczne wykrywanie wszystkich dzieci przypisanych do konta rodzica. Każde dziecko otrzymuje własny zestaw sensorów (np. `adam_nowak`, `jan_kowalski`).
- 📅 **Profesjonalny Plan Lekcji:** Klasyczny układ tabelaryczny z nieograniczoną nawigacją tygodniową (poprzedni / obecny / następny).
- 📈 **Monitoring Ocen:** Śledzenie ocen cząstkowych z systemem powiadomień o nowych wpisach i zmianach.
- 💬 **Uwagi i Pochwały:** Pełny wgląd w zachowanie ucznia z podziałem na wpisy pozytywne, negatywne oraz informacyjne.
- ✉️ **Centrum Wiadomości:** Licznik wiadomości nieprzeczytanych oraz odczytanych wraz z listą ostatnich nadawców i tematów.
- 🎒 **Terminarz Wydarzeń:** Podgląd sprawdzianów, kartkówek i zadań domowych z kolorystycznym rozróżnieniem priorytetów.
- 🛠️ **Zero-Click UI:** Dodatek automatycznie rejestruje wymagane karty JavaScript w zasobach Lovelace (Resources) przy każdym starcie.
- 🕵️ **System Anty-Detekcyjny:** 
  - Zapytania do serwerów Vulcan wysyłane są w losowych odstępach (40-60 min).
  - **Tryb Nocny:** Całkowite wstrzymanie aktywności bota między 01:00 a 05:59.
- 📝 **Precyzyjne Logowanie:** Wszystkie zdarzenia logowane są z timestampem w formacie `[YYYY-MM-DD HH:MM:SS]`.

---

## 🏗️ Architektura Systemu

System opiera się na modularnej strukturze współpracujących skryptów:

| Moduł | Rola | Opis techniczny |
| :--- | :--- | :--- |
| `vul.py` | 🔑 **Logowanie** | Silnik **Selenium Headless**. Obsługuje logowanie, akceptację cookies (iframe) oraz ekstrakcję unikalnych kluczy sesji (`app_key`) bezpośrednio z nowego Panelu Rodzica. |
| `vul-for-mess.py` |  🔑 **Logowanie** | Silnik **Selenium Headless**. Obsługuje logowanie do panelu Wiadomosci |
| `vulo.py` | 📝 **Oceny** | Pobiera oceny i zarządza bazą **SQLite** (`vultron.db`), porównując stany w celu wykrycia nowych ocen. |
| `vuluw.py` | 💬 **Uwagi** | Pobiera uwagi i pochwały. Monitoruje ID wpisów, umożliwiając automatyzację powiadomień o zachowaniu. |
| `vulm.py` | ✉️ **Wiadomości** | **Nowość!** Obsługuje bezpieczną komunikację z wykorzystaniem tokenów **X-XSRF-TOKEN** oraz ciasteczek SSO. Zlicza wiadomości przeczytane i nieprzeczytane. |
| `vulp.py` | 📅 **Plan Lekcji** | Synchronizuje plan zajęć w szerokim zakresie dat, wspierając nawigację w kartach UI. |
| `vuls.py` | 🎒 **Zadania** | Pobiera szczegółowe informacje o sprawdzianach i zadaniach domowych (detale nauczyciela, opisy). |
| `setup_ui.py` | 🎨 **UI Setup** | Automatycznie dodaje karty do zasobów HA przez **WebSocket API**, eliminując konfigurację ręczną. |
| `run.sh` | ⚙️ **Orkiestrator** | Skrypt nadrzędny Bash. Zarządza pętlą czasu, kopiowaniem plików UI i anty-detekcją. |

---

## 🚀 Instalacja i Konfiguracja

1. Skopiuj pliki dodatku do folderu `/addons/vultron` w swojej instalacji Home Assistant.
2. W interfejsie HA przejdź do **Ustawienia -> Dodatki -> Sklep z dodatkami**, kliknij trzy kropki i wybierz **Odśwież**.
3. Zainstaluj dodatek **Vultron**.
4. W zakładce **Konfiguracja** wypełnij dane dostępowe:

| Parametr | Opis | Przykład |
| :--- | :--- | :--- |
| `city_slug` | Nazwa miasta z adresu URL dziennika | `radom` |
| `username` | Adres e-mail do EduVulcan | `rodzic@email.pl` |
| `password` | Hasło do portalu | `TwojeTajneHaslo` |
| `period_id` | ID semestru (wyciągnięte z konsoli F12 - parametr `idOkresKlasyfikacyjny`) | `40732` |

5. Uruchom dodatek.
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

--- 

## 📊 Konfiguracja Kart Dashboardu

Po uruchomieniu dodatku sensory zostaną utworzone automatycznie (np. `sensor.vultron_oceny_jan_kowalski`). Dodaj nową kartę (Manual Card) na swoim Dashboardzie, korzystając z poniższych wzorów:

### 📅 Plan Lekcji (Tabelaryczny z nawigacją)
```yaml
type: custom:vultron-card
entity: sensor.vultron_plan_jan_kowalski
```

### 📈 Oceny Cząstkowe
```yaml
type: custom:vultron-grades-card
entity: sensor.vultron_oceny_jan_kowalski
```

### ✉️ Wiadomości (Licznik i Lista)
```yaml
type: custom:vultron-messages-card
entity: sensor.vultron_wiadomosci_jan_kowalski
```

### 💬 Uwagi i Pochwały
```yaml
type: custom:vultron-uwagi-card
entity: sensor.vultron_uwagi_jan_kowalski
```

### 🎒 Terminarz (Sprawdziany i Zadania)
```yaml
type: custom:vultron-work-card
entity: sensor.vultron_terminarz_jan_kowalski
```

### ⚠️ Debugowanie
Jeśli napotkasz problemy z logowaniem:
1. Sprawdź zakładkę **Logi** dodatku. Wszystkie błędy są tam opisywane w czasie rzeczywistym.

### ⚖️ Nota prawna
> [!IMPORTANT]
> Projekt **Vultron** jest narzędziem edukacyjnym i służy wyłącznie do użytku prywatnego. Autor nie bierze odpowiedzialności za ewentualne blokady kont, błędy w synchronizacji danych czy inne konsekwencje wynikające z automatyzacji dostępu do portalu EduVulcan. Korzystasz z dodatku na własną odpowiedzialność.
