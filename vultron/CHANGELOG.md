## 🧩 Changelog

### **7.0 - Friedman Unit (FU)**

- Nowości i Architektura
    - Playwright zamiast Selenium: Pełne porzucenie ciężkiego Selenium (usunięto pyvirtualdisplay). Logowanie jest natywne, w ukrytym kontekście Chromium (headless=True), co omija potrzebę emulacji ekranu.
    - Stealth Mode (Anti-Detection): WebGL spoof (emulacja grafiki Intel Iris), randomizacja pluginów przeglądarki, szumy audio (audio noise) oraz ukrycie właściwości webdriver – zabezpiecza przed blokadami anty-bot po stronie serwerów Vulcana.
    - Fail Fast + Watchdog HA: Krytyczny timeout logowania lub crash wywołuje fatal_error_stop(), co bezpiecznie (graceful stop) wstrzymuje dodatek przez API Supervisora, pozwalając HA na czysty restart.
    - Inteligentna detekcja błędów API: Przepełnienie ciastek (błąd 400) automatycznie usuwa uszkodzony plik bul.pkl. Błędy 502/503/429 wywołują natychmiastowe 3-krotne ponowienie (Retry) z losowym opóźnieniem (Jitter), a CAPTCHA wywołuje twardą blokadę z postępującym czasem oczekiwania (Exponential Backoff).
    - Jedna współdzielona baza (aiosqlite): Zrezygnowano z otwierania bazy w każdej funkcji. Otwarte jest jedno główne połączenie AsyncDB, co eliminuje mrożenie dysku (Connection Churn) i kolizje ("database locked").
    - HTMLStripper (Bezpieczny Parser): Oczyszcza kod zachowując formatowanie (pogrubienia jako **), wyciąga linki i aktywnie blokuje ataki XSS (odrzuca javascript:, waliduje adresy poprzez bezpieczny moduł urllib.parse.urlparse).
    - httpx Event Hooks (TRACE Logs): Rekurencyjne maskowanie haseł/tokenów w zagnieżdżonych JSONach. Tryb TRACE pozwala na bezpieczny debug payloadów sieciowych.

- Poprawki i Optymalizacje
    - Poprawki stabilności: Wyciągnięto blokujące HTTP poza transakcje bazodanowe. Zadbano o pełny proces czyszczenia "zombie" procesów Playwrighta po wyjściu (cleanup_all_playwright).
    - Semafory concurrency (Semaphore=5): Limiter jednoczesnych zapytań (zarówno przy odpytywaniu frekwencji w API Vulcana, jak i przy POSTowaniu sensorów do API Home Assistanta) chroni przed ucięciem połączenia (DDoS self-protection).
    - Cache HA (2500 encji): Deduplikacja zapytań POST (kod nie wysyła do HA danych, jeśli nie uległy zmianie na podstawie hasha SHA256) + funkcja natychmiastowego przywracania stanu sensorów po wykryciu restartu HA.
    - Oceny (new_g counter): Poprawnie zlicza nowe oceny przy użyciu logiki SQL ON CONFLICT DO UPDATE połączonej ze sprawdzeniem rowcount.
    - Zero Wycieków Zasobów: Eliminacja starych reliktów contextlib.closing na rzecz bezpiecznych menedżerów kontekstu async with.
    - Wiadomości: Deduplikacja przebiega w 100% poprawnie przy użyciu globalKeySkrzynka. Długie HTML są skracane do 2000 znaków (limit encji HA).
    - Indeksy SQL (idx_*_slug_data): Zoptymalizowano schemat DDL – dodane indeksy dramatycznie przyspieszają wyszukiwanie historycznych wpisów przy starcie.
    - Edge cases (Krawędziowe przypadki): Fallback dla funkcji slugify() (jeśli uczeń ma imię składające się z samych znaków specjalnych, system wygeneruje hash), null-safe iteracje i sprawdzanie formatu zwracanego JSONa.

### **6.1 - Smoot**
- Nowości (New Features)
    - Tryb testowy (test_mode): Dodano nowy przełącznik w konfiguracji dodatku (config.yaml). Jego włączenie całkowicie wyłącza nocne oraz weekendowe blokady harmonogramu. Skrypt w trybie testowym działa w trybie ciągłym, co ułatwia i przyspiesza testowanie wprowadzanych zmian.
    - Informacja o klasie ucznia: Skrypt podczas logowania pobiera teraz z systemu Vulcan informację o oddziale/klasie ucznia (np. "8a", "3c") i zapisuje ją w wewnętrznej strukturze danych (przygotowanie bazy pod przyszłe, specyficzne dla roczników funkcje).
    - Obsługa ocen literowych (Klasy 1-3): Karta Lovelace w Home Assistant w pełni wspiera teraz wyświetlanie ocen literowych. Dodano odpowiednie formatowanie kolorystyczne: oceny A/B (zielony), C/D (pomarańczowy), E/F (czerwony) oraz % (niebieski) i NB (szary).
    - Frekwencja - Dodano możliwość filtrowania statystyk frekwencji według przedmiotu. Skrypt pobiera teraz listę przedmiotów z /api/Przedmioty oraz statystyki dla każdego z nich równolegle przez asyncio.gather. Zamiast pakować wszystko do jednej encji (limit 16 000 B), tworzone są osobne małe encje sensor.vultron_stats_{slug}_{przedmiot} — encja główna przechowuje tylko lekki spis przedmiotów. Przedmioty bez statystyk (podsumowanie=null) są cicho pomijane na poziomie DEBUG. Karta HA dostała dropdown do wyboru przedmiotu, który przełącza się między encjami lokalnie bez żadnych dodatkowych requestów.

- Poprawki (Bug Fixes)
    - Przeniesienie logiki obliczeniowej na Backend: Karta interfejsu (JavaScript) nie wylicza już średniej ocen samodzielnie. Od teraz pobiera ona gotową, precyzyjnie wyliczoną średnią prosto z atrybutów sensora dostarczanego przez skrypt Pythona.
    - Inteligentne wyliczanie średniej: Naprawiono błąd, który powodował, że duże wartości liczbowe wpisane bez znaku procenta (np. punkty ze sprawdzianu: "60", "68") były błędnie wykrywane jako ocena celująca (6) i wliczane do średniej.
    - Nowy algorytm wyliczania średniej jest wysoce rygorystyczny: szuka wyłącznie samodzielnych cyfr od 1 do 6 z opcjonalnymi znakami (+, -, lub .5).
    - Wszystkie litery (A-F), statusy (NB, np, bz), wartości procentowe (%) oraz liczby dwu- i trzycyfrowe (np. "60", "100") są całkowicie ignorowane przy liczeniu średniej. Zignorowanie wartości przy średniej nie wpływa na jej pobieranie – każda wpisana ocena/punkty nadal wyświetla się na karcie ucznia w oryginalnej formie.
    - Aktualizacja wszystkich kart vultron‑* po serii poprawek optymalizacyjnych i porządkowych.
        - Zmiany techniczne:
            - Dodano mechanizmy cache i early return ograniczające niepotrzebne rerendery (szczególnie w kartach stats, plan, numerek).
            - Wprowadzono jednolite zarządzanie event listenerami z czyszczeniem zasobów w disconnectedCallback() – brak duplikatów i wycieków pamięci.
            - Karta planu lekcji (vultron‑card.js) czyści teraz poprawnie aktywny timer linii czasu.
            - Utrzymano pełną zgodność wizualną – brak zmian w wyglądzie, CSS i strukturze HTML.
        - Efekt dla użytkownika:
            - Szybsze działanie i mniejsze obciążenie interfejsu.
            - Stabilne sortowanie i filtrowanie w kartach z listami.
            - Brak znikających lub duplikujących się przycisków po aktualizacji stanu Home Assistanta.
- Kron :D
    - Dni Robocze: Zachowano działanie 40-60 min pomiędzy cyklami z cichą przerwą na sen od 01:00 do 05:59. Zawsze budzi się o 06:00.
    - Soboty i Niedziele: Skrypt patrzy na to, o jakiej godzinie skończył procesowanie i "budzi się" dopiero punktualnie na sztywnych godzinach (z dokładnością do minuty).
    - Przejście Dni: Jeśli skończy zadanie w niedzielę po 20:00, automatycznie uśpi się aż do poniedziałku do 06:00 rano.
    - Odporność na Restart Skryptu: Jeśli wyłączysz i włączysz wtyczkę w Home Assistant np. w sobotę o godzinie 11:00, skrypt nie zacznie agresywnie pobierać danych od razu - rozpozna, że nie nadszedł jego czas i po cichu poczeka do 16:00 z pierwszym startem. Zależnie od godziny dostosuje się z automatu do grafiku.
    - Poniedziałek – Piątek \
    | 00:00 – 05:59 | 🔴 Przerwa nocna (śpi do 06:00) \
    | 06:00 – 23:59 | 🟢 Aktywny — cykle co ~40–60 min (losowy interwał)
    - Sobota \
    | 00:00 – 07:59 | 🔴 Czeka do 08:00 | \
    | 08:00 – 08:59 | 🟢 Cykl | \
    | 09:00 – 15:59 | 🔴 Czeka do 16:00 | \
    | 16:00 – 16:59 | 🟢 Cykl | \
    | 17:00 – 22:59 | 🔴 Czeka do 23:00 | \
    | 23:00 – 23:59 | 🟢 Cykl |
    - Niedziela \
    | 00:00 – 07:59 | 🔴 Czeka do 08:00 | \
    | 08:00 – 08:59 | 🟢 Cykl | \
    | 09:00 – 11:59 | 🔴 Czeka do 12:00 | \
    | 12:00 – 12:59 | 🟢 Cykl | \
    | 13:00 – 19:59 | 🔴 Czeka do 20:00 | \
    | 20:00 – 20:59 | 🟢 Cykl | \
    | 21:00 – 23:59 | 🔴 Czeka do poniedziałku 06:00 | \
        - Skrypt sprawdza warunek godzinowy przez `now.hour not in (...)`, więc cykl może odpalić się w dowolnym momencie danej godziny, nie dokładnie o jej początku.
        - W trybie `test_mode` wszystkie filtry czasowe są pomijane, a interwał między cyklami wynosi również ~40–60 min.


### **6.0 - Oxgang**
- Połączono wszystkie główne skrypty w jeden plik:
    - `vultron/vultron.py`
- Usunięto osobne pliki:
    - `vul.py`, `vulf.py`, `vulm.py`, `vulo.py`, `vulos.py`, `vulp.py`, `vuls.py`
    - `vuluw.py`, `vul-for-mess.py`, `vul-monitor.py`, `run.sh`, `setup_ui.py`
- Asynchroniczność "API": Dane pobierane równolegle – synchronizacja trwa 1–2 s zamiast kilkunastu.
    - Connection Pooling: Jedno stałe połączenie httpx.AsyncClient bez wielokrotnego handshake TLS.
    - asyncio.Lock: Ochrona przed błędem database is locked.
    - Timeouty: timeout=10s – koniec z zawieszaniem się skryptu.
    - WAL dla SQLite: Bezpieczna praca bazy przy wysokiej współbieżności.
- JSON zamiast Pickle: Eliminacja podatności RCE
- State Mirroring: Dane odtwarzane z cache natychmiast po restarcie HA.
- Delta-Sync: Aktualizacja tylko przy faktycznej zmianie danych.
- Graceful Shutdown: Bezpieczne zamknięcie przez SIGTERM/SIGINT.
- HTMLParser zamiast Regex: Precyzyjniejsze parsowanie HTML.
- Rotacja logów: Max 5 × 1 MB.
- Usunięcie requests: Cały HTTP ujednolicony na httpx.
- Zunifikowanie wygladu i działania wszystkich kart.
- Dodano poziomy debug: INFO, ERROR, DEBUG
- Zaktualizowano automatyzacje
- Gdzie wersja 5? No tam....

### **4.2 - 200kcal**
- Skrypt Python (vulp.py) - Podział na 3 encje: Skrypt generuje teraz oddzielne sensory dla każdego dziecka:
    - sensor.vultron_plan_[slug]_prev (Tydzień poprzedni)
    - sensor.vultron_plan_[slug]_curr (Tydzień obecny)
    - sensor.vultron_plan_[slug]_next (Tydzień przyszły)
- Karta Lovelace (vultron-card.js)
    - Dynamiczne przełączanie: Karta automatycznie wykrywa bazową nazwę encji i podmienia końcówki (_curr, _prev, _next) podczas klikania strzałek.
    - Nawigacja: Wprowadzono blokadę nawigacji (zakres od -1 do +1 tygodnia), odpowiadający dostępnym danym.
- Automatyzacje HA/Node-RED
    - Multi-Trigger: Automatyzacje nasłuchują teraz jednocześnie na zmiany w tygodniu obecnym (_curr) i przyszłym (_next).
- Blueprint
    - Obsługa wielu encji: Nowa wersja pozwala wybrać listę sensorów (np. zaznaczenie obu tygodni naraz).
    - Zwiększona stabilność: Dodano sprawdzanie istnienia stanów poprzednich (old_state), co eliminuje błędy po restarcie HA.
- Karta Lovelace (vultron-grades-card.js)
    - System Obliczania Średniej
        - Widok **Przedmiotów**: Pod każdą nazwą przedmiotu pojawia się teraz automatycznie wyliczona średnia ocen (z dokładnością do dwóch miejsc po przecinku).
        - Inteligentne Filtrowanie: Algorytm bierze pod uwagę wyłącznie oceny numeryczne. Wpisy typu "np" (nieprzygotowanie), "bz" (brak zadania) czy inne adnotacje tekstowe są pomijane w obliczeniach.
        - Obsługa Ocen Złożonych:
            - Plusy i Minusy: Średnia traktuje oceny typu 4+ czy 5- jako ich bazowe wartości (4 i 5).
            - Wartości Dziesiętne: Pełne wsparcie dla ocen cząstkowych zapisanych zarówno z kropką, jak i przecinkiem (np. 4.5 lub 4,5 są liczone jako 4.5).
        - Zakres Bezpieczeństwa: System uwzględnia w średniej tylko wartości w przedziale 1-6, co zapobiega błędom w przypadku nietypowych wag lub punktacji procentowej.
    - Ulepszenia Interfejsu (UI)
        - Sub-label Średniej: Średnia jest wyświetlana subtelnym drukiem (opacity 0.6) pod nazwą przedmiotu, aby nie zaburzać czytelności głównej listy.
        - Poprawiona Kolorystyka: Udoskonalono metodę getGradeColor, dzięki czemu kolorowanie ocen (zielony dla 5-6, pomarańczowy dla 3-4, czerwony dla 1-2) działa teraz precyzyjniej przy ocenach z sufiksami.
    - Stabilność i Logika
        - Decimal Parser: Wprowadzono konwersję znaków regionalnych (zamiana , na .), co gwarantuje poprawność matematyczną w środowisku JavaScript.
        - Dynamiczne Renderowanie: Średnia pojawia się tylko wtedy, gdy w danym przedmiocie znajduje się co najmniej jedna ocena kwalifikująca się do obliczeń.

### **4.1 - „16KB"**
- **Oceny**
    - Podział Ocen: Rozbito oceny na dwie niezależne encje: _p1 (Okres 1) oraz _p2 (Okres 2).
    - vultron-grades-card (Oceny):
        - Dodano zakładki OKRES 1 i OKRES 2 w nagłówku.
        - Karta pozwala na płynne przełączanie widoku między semestrami.
- **Wiadomości**
    - Skrypt przesyła teraz do HA tylko 10 najnowszych wiadomości. Atrybut treść jest przesyłany wyłącznie dla wiadomości nieprzeczytanych. Wiadomości przeczytane zajmują teraz minimalną ilość miejsca (tylko meta-dane), a karta wyświetla informację o dostępie do pełnej treści w aplikacji EduVulcan.
    - vultron-messages-card:
        - Dodano licznik statystyk w nagłówku.
        - Dodano ramkę informacyjną dla wiadomości archiwalnych (bez treści).
- **Plan**
    - Redukcja Planu: Skrócono zakres planu przesyłanego do HA do 21 dni (poprzedni + obecny tydzień + następny), co pozwoliło zejść z ~20KB
    - vultron-card (Plan):
        - Zunifikowano wygląd nagłówka (Kolor Cyan #00bcd4, ikony MDI).
        - Reimplementowano znacznik "TERAZ" oraz podświetlenie aktywnej lekcji za pomocą box-shadow: inset (widoczne z każdej strony komórki).
- **Automatyzacje**
    - HA/Node_RED/Blueprints - zaktualizowano automatyzacje powiadamiania o ocenach
- **Monitoring**
    - (`vul-monitor.py`)sumuje rozmiary danych Vultron i raportuje szczegóły oraz ostrzeżenia, a alert włącza się, gdy dane encji przekroczą krytyczny limit 16 KB. Wszystkie przekroczenia progów WARNING(14000B) i CRITICAL(15500B) są logowane w konsoli [MONITOR].
    Wiecej w dziale [🔍 Monitoring](#-monitoring)

### **4.0** - „Furlong/fortnight"**
- Pełna migracja na SQLite: Dane są teraz trwałe, dostępne offline w bazie vultron.db.
- Optymalizacja płynności UI: Ograniczono przesył danych do HA do obecnego i poprzedniego tygodnia, co wyeliminowało lagi w interfejsie.
- Redukcja zasobów: Znacząco zmniejszono zużycie RAM przez Chromium (blokada obrazów/GPU) oraz wprowadzono bezpieczny zapis plików.
- Najważniejsze: kolor w planie zgadza sie z resztą

### **3.4.2 - „Siriustek"**
- Dodano 5 gotowych schematów automatyzacji dla HA (Oceny, Frekwencja, Plan, Uwagi, Wiadomości).

### **3.4.1 - „Siriustek"**
- Implementacja zaawansowanego skanowania bezpieczeństwa (CodeQL, Bandit, Trivy, Hadolint).
- Dodanie mechanizmu pre-commit i automatyzacji GitHub Actions.
- Optymalizacja dokumentacji i integracja z "My Home Assistant"

### **3.2 - „Siriustek"**
- Automatyczne odświeżanie kart (Cache-busting): Koniec z ręcznym czyszczeniem ciasteczek i cache'u przeglądarki po aktualizacji dodatku. System automatycznie wersjonuje pliki .js.
- Auto-Discovery kart UI: Skrypt `setup_ui.py` sam wykrywa wszystkie pliki kart w folderze i rejestruje je w zasobach Lovelace.
- Inteligentny start: Dodano pętlę "retry" przy łączeniu z API Home Assistant. Jeśli system startuje po awarii prądu, dodatek cierpliwie poczeka, aż rdzeń HA będzie gotowy.
- Bezpieczna północ: Naprawiono błąd w skrypcie `run.sh`, który mógł powodować błędy w cyklu synchronizacji o godzinie 00:00.
- Optymalizacja obrazu: Przebudowano `Dockerfile` (czyszczenie cache apk, instalacja przez requirements.txt), co owocuje mniejszym i stabilniejszym kontenerem.
- Nowy system instalacji: Dodano obsługę przycisku "My Home Assistant" oraz uporządkowano dokumentację README.

### **3.000009 - „Muggeseggele"**
- Dodano acziwmenety :D
    - vulos.py
    - vultron-osiagniecia-card.js
    - automatyzacji do niej nie będzie - nikt nie ma tak zajebistego dziecka
- Chyba wszystko juz mamy :P
- Wszędzie będzie Glassmorphism + ban
- Koniec rozwoju (nieeee :D)

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
