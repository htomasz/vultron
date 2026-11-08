# 🧩 Changelog
## **8.0.1 - Sydharb** — 2026-11-08
- **Nowość: API mobilne eduVULCAN (opcja `mobile_api`, domyślnie wyłączona)** — Vultron rejestruje się jako urządzenie, jak aplikacja w telefonie (tokeny z `eduvulcan.pl/api/ap`, własny klucz RSA, model urządzenia „Vultron (Home Assistant)”), i pobiera dane podpisanymi zapytaniami, bez przeglądarki. Cykl trwa kilka sekund. Oceny (cząstkowe, proponowane, końcowe, średnie), plan ze zmianami, terminarz, uwagi, frekwencja ze statystykami, szczęśliwy numerek, zebrania i wiadomości przechodzą przez ten sam kod co dane ze stron WWW, więc encje, karty, blueprinty i automatyzacje działają bez zmian. Dane porównane pole po polu ze stronami WWW na prawdziwym koncie: zgodne (te same identyfikatory ocen, uwag i wiadomości, ta sama frekwencja co do setnej procenta).
- **Pomysł kradziony 🏴‍☠️** — z [dampud/vulcan](https://github.com/dampud/vulcan) (AGPL-3.0), który oparł się na [iris](https://github.com/bbrjpl1310b/iris) (AGPL-3.0). Implementacja w Vultronie (`vultron/hebe.py`, `vultron/hebe_web.py`) napisana od nowa, na GPL-3.0-or-later. Dzięki!
- **Nowa opcja `mobile_interval`** — odstęp cykli z API mobilnym w dni robocze w dzień: domyślnie 15 minut (±2), od 10 do 120. Przerwa nocna i weekendowe godziny bez zmian; cykl przez strony WWW dalej co 40-60 minut. Lekcje całego roku (statystyki frekwencji) pobierane raz na godzinę, wpisy z ostatnich 14 dni co cykl.
- **Czego API mobilne nie ma** — osiągnięć i dni wolnych (serwer odpowiada 404), przedszkola w Vultronie jeszcze nie przeniesiono. Te sekcje idą przez sesję stron WWW; gdy wygaśnie, Vultron loguje się przez przeglądarkę najwyżej raz na dobę. Odrzucone hasło przy tym logowaniu daje ostrzeżenie zamiast zatrzymania dodatku. Wygasła sesja jest sprawdzana raz, nie co cykl, a log mówi o niej raz, przy zmianie stanu („Sesja stron WWW wygasła … za ok. N h”, „Planowe odświeżenie sesji stron WWW”), zamiast „wygasła - wymuszam logowanie Selenium” co cykl.
- **Nowa opcja `mobile_web_refresh`** (domyślnie włączona) — `false` wyłącza codzienne logowanie przez przeglądarkę. Osiągnięcia, dni wolne i przedszkole przestają się wtedy aktualizować; przeglądarka startuje tylko do rejestracji urządzenia albo gdy API mobilne nie odpowiada.
- **Przedszkole w API mobilnym, krok 1 (tryb cienia)** — raz na dobę log `[MOBILE][PRZEDSZKOLE-CIEŃ]`: co API mobilne zwraca dla przedszkolaka (obecność, nieobecności, nauczyciele, zebrania, ogłoszenia, jadłospis, godziny), tylko liczby rekordów i nazwy pól, bez wartości. Dane przedszkola dalej idą ze stron WWW. Konto przedszkolaka nie trafia do sekcji szkolnych z API mobilnego.
- **Odporność** — API mobilne niedostępne albo cykl dłuższy niż 15 minut: ten cykl przez strony WWW. Jedna sekcja z błędem: ta sekcja ze stron WWW, przyczyna w logu (`[MOBILE] …`). Urządzenie unieważnione (np. po zmianie hasła): logowanie przez przeglądarkę i ponowna rejestracja, najwyżej raz na 6 godzin. Wpisy terminarza zapisane wcześniej zachowują swoje id (różnią się między stronami WWW a API mobilnym), więc nie wracają jako „nowe”.
- **Bezpieczeństwo** — klucz urządzenia w `/data/hebe_device.json` (zapis atomowy, prawa 0600); tokeny, klucz, imiona i adresy e-mail nie trafiają do logu; podpisane zapytania tylko po https do `*.vulcan.net.pl`, bez przekierowań; odpowiedź `/api/ap` walidowana, zanim cokolwiek z niej trafi do adresu URL.
- **Znane różnice z API mobilnym** — ocena poprawiona: API mobilne podaje tylko obowiązującą, więc karta nie pokazuje starej („pop”). Ocena proponowana i końcowa z pól `Entry_1` / `Entry_2` (jak w Wulkanowym; potwierdzi się przy pierwszej klasyfikacji).
- **Zależność:** `cryptography` 50.0.2 (z `cffi` i `pycparser`), przypięta hashami; gotowe paczki dla Alpine na amd64 i aarch64.
- **Naprawa: ocena z bardzo długim tekstem** — wyrażenia regularne rozpoznające oceny procentowe miały koszt kwadratowy: wpis z kilkudziesięcioma tysiącami spacji liczył się sekundy (karta ocen ok. 8 s, wyliczanie średnich w dodatku ok. 5 s na wpis). Teraz kilka ms. Kolory ocen i średnie bez zmian. Znika też ostrzeżenie ESLint `security/detect-unsafe-regex`. Usunięty nieużywany `logo.png` z katalogu głównego.
- **Dodatek HA:** zakładka **Dokumentacja** (`vultron/DOCS.md`), ikona 128×128 i logo 250×100 zgodne z wymaganiami HA. README: opcja `username` dla GPE (login konta, nie zawsze e-mail), nowa sekcja o API mobilnym.
- **CI i repozytorium:** opis GitHub Release z sekcji wersji w tym pliku, testy i skan bezpieczeństwa na Pythonie 3.14, reguły ruff F/E9, limity czasu jobów i anulowanie przestarzałych przebiegów, nagłówki SPDX w źródłach, `.gitattributes` i `.editorconfig`.
- **Testy:** 234 testy Pythona i testy kolorów ocen w karcie (node), w tym wartości ocen i średnie przedmiotów, walidacja adresu dziennika, klient API mobilnego (podpis weryfikowany kluczem publicznym, walidacja `/api/ap`, stronicowanie, konta), most (każda sekcja w formacie stron WWW) i cała ścieżka: dane z API mobilnego → istniejące fetchery → wiersze bazy i encje.

## **7.2.1 - Garn** — 2026-11-07
- **Naprawa: encja znikała po restarcie Home Assistanta** — encja, której dane nie zmieniły się przez 60 dni (np. uwagi bez nowych wpisów), była usuwana z lokalnej kopii używanej do odtwarzania po restarcie HA. Po restarcie HA nie było jej czym odtworzyć, a kolejne cykle jej nie wysyłały, bo dane się nie zmieniły. Encja wracała dopiero po zmianie danych albo restarcie dodatku. Teraz po wykryciu restartu HA następny cykl publikuje wszystkie encje. Dotyczy eduVULCAN i GPE Gdańsk.
- **Naprawa: karty terminarza i zebrań po północy** — „dzisiaj” było liczone w UTC, więc przez 1-2 godziny po północy terminarz pokazywał jeszcze wczorajsze wpisy, a karty zebrań (szkoła i przedszkole) oznaczały wczorajsze zebranie jako nadchodzące. Teraz liczona jest data lokalna.
- **Naprawa: skracanie opisu w karcie terminarza** — opis długości 81-100 znaków dostawał „...”, choć był pokazany w całości.
- **Dokumentacja:** GDANSK.md / GDANSK.pl.md opisują zachowanie z 7.2.0 (Docker: druga próba logowania przed zatrzymaniem). README: zachowanie GPE przy błędzie logowania, harmonogram GPE, stan `partial` w `sensor.vultron_gdansk_status`, opcje `default_sort: final` i zakres `limit` w karcie ocen, opis jednostki monitora rozmiaru.
- **Narzędzia deweloperskie:** z `package.json` i `package-lock.json` usunięta zależność `repomix` dodana przypadkiem w 7.2.0 (nie trafiała do dodatku ani obrazu Docker; CI instalował ją niepotrzebnie). Pliki są takie jak przed nią.
- **Testy:** encja usunięta z kopii lokalnej wraca do HA w pierwszym cyklu po jego restarcie.

## **7.2.0 - Garn** — 2026-11-07
- **Bezpieczeństwo: GPE Gdańsk w Dockerze nie ponawia złego hasła** — dotąd po odrzuconym logowaniu do GPE proces się kończył, a `restart: unless-stopped` uruchamiał go ponownie, więc logowanie powtarzało się mniej więcej co minutę, co groziło blokadą konta. Teraz jak w eduVULCAN: druga próba w następnym cyklu, a po drugim błędzie z rzędu kontener dalej działa, ale już się nie loguje (`sensor.vultron_gdansk_status` = `authentication_error`). Po poprawie `data/options.json`: `docker compose restart`. Udane logowanie zeruje licznik. Dodatek HA bez zmian (zatrzymuje się po pierwszym błędzie). `gdansk.py` bez zmian.
- **README: poprawione opisy niezgodne z kodem** — trzecia sytuacja samoczynnego zatrzymania (3 timeouty wiadomości z rzędu), licznik odrzuconych logowań tylko w pamięci, wydłużanie przerwy od drugiego nieudanego logowania, limit 16 384 B jako limit recordera HA (historia), kalendarz: samo nazwisko jako prefiks nie działa i dokładny zakres pobierania, opcja `limit` w kartach zebrań, osiągnięć i przedszkola, pole „Akcje do wykonania” w blueprintach, linki do automatyzacji HA, plik `/data/vultron.log` przy `trace`, krok 9 Dockera, nota prawna zgodna z GPL-3.0.
- **README: odznaki** — nowe: Cards XSS, Add-on Linter, minimalna wersja HA; „Docker Build” wskazuje `build-image.yml`, „Lint & Semgrep” z logo ESLint, „Signed Releases” z dopiskiem (Docker + zip).
- **Testy:** pętla GPE przy odrzuconym logowaniu (dodatek HA, Docker, zerowanie licznika po udanym logowaniu).

## **7.1.16 - Jungfru** — 2026-11-06
- **Nowe: powiadomienia o sprawdzianach i zadaniach domowych** (#84) — blueprint „Vultron: Nowe sprawdziany i zadania domowe” (import jednym kliknięciem z README → Vultron Alert Pack) oraz gotowa automatyzacja HA `automation/ha/terminarz.yaml`. Działają jak blueprint ocen: powiadamiają o nowym sprawdzianie, kartkówce, klasówce lub zadaniu domowym, także o drugim zadaniu z tego samego przedmiotu na ten sam dzień i o wpisie poprawionym przez nauczyciela. Po restarcie HA nie wysyłają starych wpisów.
- **Naprawa: Node-RED `terminarz.json`** — drugie zadanie z tego samego przedmiotu na ten sam dzień i poprawiony opis nie dawały powiadomienia, a przy bardzo długim terminarzu (atrybut `pominiete`) przychodziły fałszywe powiadomienia o starych wpisach. Logika jest teraz taka sama jak w blueprincie; format powiadomień bez zmian. Wystarczy ponownie zaimportować przepływ.
- **Naprawa: terminarz przy chwilowym błędzie eduVULCAN** — gdy nie udało się pobrać szczegółów wpisu (błąd sieci, odpowiedź inna niż 200), zapisany wpis był nadpisywany danymi z samej listy, zwykle z opisem „Brak opisu”, a w następnym cyklu wracał pełny opis. Automatyzacje widziały to jako nowe wpisy. Teraz zapisany wpis zostaje bez zmian; nowy wpis pojawia się od razu z danymi z listy, a opis dochodzi, gdy szczegóły się pobiorą.
- **Testy:** zachowanie terminarza przy błędzie szczegółów (odpowiedź 500, błąd sieci, nowy wpis bez szczegółów).

## **7.1.15 - Jungfru** — 2026-11-05
- **Karty wiadomości (szkoła i przedszkole):** nieprzeczytana wiadomość, której treść nie zmieściła się w limicie Home Assistanta, pokazuje teraz „Treść nie zmieściła się w Home Assistant – przeczytaj ją w aplikacji EduVulcan.” zamiast mylącego tekstu o wiadomości archiwalnej. Przeczytane wiadomości bez zmian.
- **Karty wiadomości:** usunięta nieużywana linia w sanitizerze treści (wynik czyszczenia bez zmian).
- **Plan lekcji i plan przedszkola:** lekcja z pustą godziną (`null`) w odpowiedzi eduVULCAN jest pomijana z ostrzeżeniem w logu. Wcześniej taki jeden wpis powodował brak całego planu w danym cyklu.
- **Dni wolne:** pusta lista jednostek (`null`) nie przerywa już synchronizacji planu.
- **Osiągnięcia:** nieoczekiwany format odpowiedzi jest pomijany z ostrzeżeniem, jak w pozostałych sekcjach.
- **Jadłospis (przedszkole):** gdy żadna dieta placówki nie zawiera „przedszkol”, log podaje nazwy dostępnych diet (wcześniej karta była pusta bez wyjaśnienia).
- **Log:** po nieudanym logowaniu komunikat cyklu brzmi „Cykl bez danych (logowanie nieudane)” zamiast „Cykl OK”.
- **Testy:** test XSS obejmuje wszystkie 17 kart (wcześniej 3), przez ich prawdziwe wejście (`setConfig` + `hass`); testy tekstów pustej treści w obu kartach wiadomości; testy powyższych poprawek na nietypowych danych z API.

## **7.1.14 - Jungfru** — 2026-11-05
- **Bezpieczeństwo: skanery widzą cały kod SQL** — zapytania z nazwą tabeli (kasowanie wpisów usuniętych w dzienniku, retencja) są teraz stałymi zamiast składanych w locie, więc zniknęły wyjątki `nosec`/`noqa` wyłączające bandit i ruff. Nieznana tabela kończy się błędem zamiast budować zapytanie. Działanie bez zmian; nowy test `tests/test_sql_constants.py` pilnuje zgodności zapytań.
- **CI:** Trivy 0.76.1 (wcześniej 0.70.0, domyślna w `trivy-action`).

## **7.1.13 - Jungfru** — 2026-11-04
- **Bezpieczeństwo: uprawnienia `/data` prościej i pełniej** — zamiast zmiany uprawnień nowych plików (7.1.12) dodatek przy starcie zamyka cały katalog `/data` (0700). Chroni to także `options.json` z hasłem oraz pliki utworzone wcześniej, więc ręczne `chmod 600 data/*` z opisu 7.1.12 nie jest już potrzebne. Karty i zrzut ekranu w `/config/www/vultron` mają znów zwykłe uprawnienia, jak przed 7.1.12.

## **7.1.12 - Jungfru** — 2026-11-04
- **Bezpieczeństwo: Docker nie ponawia złego hasła** — po dwóch odrzuconych logowaniach kontener dalej działa, ale już się nie loguje (`sensor.vultron_status` = `authentication_error`). Wcześniej `restart: unless-stopped` uruchamiał go ponownie i dodatek próbował znowu, co groziło blokadą konta. Po poprawie `data/options.json`: `docker compose restart`. Dodatek HA bez zmian (zatrzymuje się).
- **Bezpieczeństwo: uprawnienia plików** — nowe pliki w `/data` (zapisana sesja `vul.pkl`, baza, log) są dostępne tylko dla właściciela. **Docker, istniejąca instalacja:** jednorazowo `sudo chmod 600 data/*` (zmiana dotyczy tylko nowych plików).
- **Bezpieczeństwo: zrzut ekranu błędu logowania** ma losową nazwę (`/local/vultron/vultron_auth_error_<losowy_ciąg>.png`, dokładny adres jest w logu), a nowy zrzut usuwa poprzedni. Stały adres był dostępny bez logowania dla każdego, kto go znał.
- **Naprawa: za duża encja wiadomości** — przy kilku długich nieprzeczytanych wiadomościach encja przekraczała limit Home Assistanta (16384 B). Teraz lista zostaje cała (10 ostatnich, automatyzacje bez zmian), a przy braku miejsca znika tylko treść najstarszych nieprzeczytanych; atrybut `skrocone` mówi ile. Szkoła i przedszkole.
- **Dokumentacja:** w dodatku HA automatyczny restart po zawieszeniu przeglądarki wymaga przełącznika **Watchdog**.

## **7.1.11 - Jungfru** — 2026-11-02
- **Bezpieczeństwo:** Selenium 4.52.0 (biblioteka `zip` 9.0.5 w Selenium Manager; Vultron go nie uruchamia, ale skanery to zgłaszały).

## **7.1.10 - Jungfru** — 2026-11-02
- **Naprawa: aktualizacja do 7.1.9 kończyła się błędem** („An unknown error occurred with app …”). Dodatek HA znów buduje obraz lokalnie, jak przed 7.1.9 — gotowy obraz z GHCR jest używany tylko przez samodzielny kontener (`compose.yaml`). Reszta zmian z 7.1.9 (przypięte wersje Alpine i pakietów Pythona, porządki) zostaje.

## **7.1.9 - Jungfru** — 2026-11-02
- **Zmiana: gotowy obraz zamiast budowania u Ciebie** — dodatek HA pobiera teraz gotowy obraz `ghcr.io/htomasz/vultron` (amd64 i aarch64), zbudowany, podpisany i przeskanowany w GitHub Actions. Aktualizacja jest dużo szybsza, zwłaszcza na Raspberry Pi, i nie obciąża pamięci budowaniem. Nic nie musisz robić.
    - **Docker:** `compose.yaml` używa `ghcr.io/htomasz/vultron:latest`; aktualizacja to `docker compose pull && docker compose up -d`. Stary `compose.yaml` z `build:` dalej działa.
    - Tuż po wydaniu nowej wersji (kilka-kilkanaście minut) aktualizacja może się nie udać, bo obraz jeszcze się buduje — wystarczy ponowić.
- **Bezpieczeństwo: powtarzalny obraz** — baza Alpine przypięta do wersji i digestu (`3.24.3`), pakiety Pythona do dokładnych wersji z hashami (`requirements.txt`). Te same wersje co dotychczas, więc działanie się nie zmienia; nowe wersje przychodzą przez Dependabota.
- **Bezpieczeństwo: podpis obrazu** — sprawdzisz go komendą `gh attestation verify oci://ghcr.io/htomasz/vultron:X.Y.Z -R htomasz/vultron`.
- **Diagnostyka** — błędy dotąd połykane po cichu trafiają do logu na poziomie `debug`; logi pokazują nazwę sekcji z błędem (np. „Sekcja uwagi”) zamiast numeru.
- **Dokumentacja**
    - README: ostrzeżenie, że zrzut ekranu błędu logowania (`/local/vultron/vultron_auth_error.png`) może zawierać login lub imiona dzieci i jest dostępny bez logowania.
    - Opisy opcji: `test_mode` (pomija przerwy z harmonogramu, odstęp 40-60 min zostaje) i `log_level` (ostrzeżenie, że `trace` zapisuje dane osobowe).
    - SECURITY.md i CONTRIBUTING.md zgodne z nowym sposobem budowania i wydawania.
- **CI:** spójność wersji (config.yaml, Dockerfile, README) sprawdzana przy wydaniu; automatyczny build obrazu przy pushu z nową wersją; zakaz cichych `except` (S110/S112); `package.json` z licencją GPL-3.0-or-later.

## **7.1.8 - Jungfru** — 2026-10-27
- **Bezpieczeństwo:** przebudowa obrazu ze świeżymi pakietami Alpine (poprawki CVE w `curl` i `brotli`).

## **7.1.7 - Jungfru** — 2026-10-22
- **Naprawa: automatyzacje i przepływy Node-RED** [#65](https://github.com/htomasz/vultron/issues/65)
    - Plan: brak podwójnych powiadomień po zmianie tygodnia (`_curr` + `_next`); własne zajęcia z kalendarza HA nie są już zgłaszane jako zmiana planu.
    - Oceny: poprawny semestr (wcześniej zawsze „2. semestr” lub `?`); w blueprincie `{{ data }}` ma rok (`20.10.2026`) — wcześniej daty z października pokazywały się jako `20.1`.
    - Frekwencja: przykład `automation/ha/frekwencja.yaml` w ogóle nie wysyłał alertów — naprawione.
    - Brak błędu `UndefinedError` w logach HA po restarcie (frekwencja, oraz przykłady planu, uwag i wiadomości).
    - Przykłady `automation/ha/` i Node-RED: imię ucznia w powiadomieniu (wcześniej puste / „Uczeń”), ℹ️ dla wpisów informacyjnych.
- **Naprawa: Node-RED**
    - `frekwencja.json` działa także bez „Enable global context store” (wtedy bez nazwy przedmiotu); poprawna encja planu (`_curr`).
    - `plan.json`: zgłasza zastępstwa z innym przedmiotem; nowe linie w treści, `notification_id` bez kropek, „dziś” w czasie lokalnym.
    - `terminarz.json`, `uwagi.json`, `wiadomosci.json`: osobna pamięć dla każdego dziecka (przy kilku encjach w jednym węźle wpisy jednego dziecka wysyłały się ponownie); terminarz nie gubi pierwszego wpisu po okresie bez sprawdzianów; kartkówka z ikoną 📝.
    - `patusek.json` przepisany na obecny format ocen (wcześniej kończył się błędem).
    - Powiadomienia frekwencji i planu nie nadpisują się wzajemnie.
- **Dokumentacja (README):** przykładowe encje z sufiksami (`_p1`, `_curr`), pełna lista zmiennych blueprintów (`okres`, `data`, `typ`).
- **Uwaga:** zaimportowane blueprinty nie aktualizują się same — w HA: **Ustawienia → Automatyzacje i sceny → Blueprinty** → menu przy blueprincie → **Importuj ponownie** (Oceny, Zmiana Planu, Frekwencja). Przykłady z `automation/ha/` i `automation/node-red/` podmień ręcznie.
- Dzięki [@elgatho](https://github.com/elgatho) za zgłoszenie.

## **7.1.6 - Jungfru** — 2026-10-12
- **Naprawa: wiadomość ucznia szkoły pojawiała się też w wiadomościach przedszkolaka** — przy jednym koncie rodzica z uczniem szkoły i przedszkolakiem Vultron pobierał wiadomości przedszkolaka bez wskazania skrzynki, więc dostawał skrzynkę ucznia. Teraz pyta o skrzynkę przedszkolaka jawnie (tak jak dla uczniów szkół). **Uwaga:** wiadomość, która już trafiła do przedszkolaka, zostaje w bazie — usuń ją ręcznie (opis w README/FAQ lub zapytaj w zgłoszeniu).
- **Naprawa: logowanie kończyło się błędem „element not interactable"** — eduVULCAN ma teraz pole hasła w kodzie strony od razu, ale ukryte do czasu kliknięcia „Dalej”, a Vultron wpisywał hasło, gdy tylko pole istniało. Teraz czeka, aż pole będzie widoczne. Jeśli mimo to się nie pojawi, zapisuje zrzut ekranu (`/local/vultron/vultron_auth_error.png`).
- **Naprawa: oceny z procentem w nawiasie** — ocena typu `1 (23%)` liczy się teraz jako 1 i ma kolor oceny (wcześniej była pomijana w średniej i niebieska jak ocena procentowa).
- **Naprawa: opis oceny „None"** — gdy kolumna w dzienniku nie miała kategorii, dymek pokazywał „None". **Uwaga:** jeśli używasz automatyzacji „nowa ocena", po aktualizacji możesz dostać jednorazowe powiadomienie o takich ocenach (zmienił się ich opis).
- **Bezpieczeństwo: logowanie**
    - Dodatek zatrzymuje się, gdy eduVULCAN **dwa razy odrzuci logowanie** (złe hasło albo weryfikacja) bez udanego logowania pomiędzy — wcześniej próbował w kółko, co groziło blokadą konta.
    - Chwilowe awarie portalu (przerwa techniczna, błąd 502) **nie wyłączają już dodatku na stałe** — wcześniej każda taka odpowiedź była brana za CAPTCHA. Teraz kolejna próba nastąpi z wydłużoną przerwą.
    - Nowa encja `sensor.vultron_status` (`ok` / `error` / `authentication_error`) z ostatnim błędem i czasem ostatniej udanej synchronizacji — jak `sensor.vultron_gdansk_status`.
    - Poprawiona literówka w adresie logowania (`ReturnUrl`) + zapasowe przejście na stronę dostępu do dziennika, gdy kafelki się nie pokażą.
- **Bezpieczeństwo: sesja**
    - Ciasteczka sesji eduVULCAN są wysyłane **wyłącznie do domeny dziennika** — wcześniej poszłyby do dowolnej strony, na którą przekierowałby serwer.
    - Adres dziennika po zalogowaniu jest ściśle sprawdzany (tylko https, bez portów i podejrzanych adresów).
    - Karty uwag, zadań i ocen escapują wszystkie dane z dziennika (zostały pominięte daty) + nowy test (`tests/test_cards_xss.cjs`), który to pilnuje.
- **Plan przedszkola: kreska aktualnej godziny** — żółta kreska z godziną w kolumnie dzisiejszej oddziela zajęcia rozpoczęte od przyszłych. Karta odświeża się też po przełączeniu widoku (wcześniej „TERAZ" zamierało do przeładowania strony).
- **Naprawa: usunięte w dzienniku wpisy znikają** — odwołany sprawdzian, usunięta uwaga, odwołane zebranie czy dzień wolny zostawały w Vultronie na zawsze. Starsze wpisy spoza okresu, który zwraca dziennik (np. z poprzedniego roku), zostają jak dotąd.
- **Naprawa: za duże encje** — przy bardzo wielu uwagach, zebraniach czy osiągnięciach Home Assistant po cichu odrzucał całą encję (pusta karta). Teraz pomijane są najstarsze wpisy, a atrybut `pominiete` mówi ile.
- **Stabilność**
    - Błąd jednej wiadomości (sieć, uszkodzona odpowiedź) nie przerywa już synchronizacji wiadomości wszystkich dzieci.
    - Błąd jednego zapytania frekwencji nie przerywa całej sekcji frekwencji i statystyk.
    - Rejestracja kart nie blokuje już równoległego pobierania danych.
- **Dokumentacja (README)** — opis `sensor.vultron_status`, jak wyłączyć zapis historii wiadomości, uwag i ocen dzieci w bazie HA (`recorder`), oraz uwaga o bezpieczeństwie tokena w trybie samodzielnego kontenera.

## **7.1.4 - Jungfru** — 2026-10-10
- **Naprawa: poprawione oceny psuły średnią** [#59](https://github.com/htomasz/vultron/discussions/59) — po poprawie (np. 2 → 5-) obie oceny liczyły się do średniej. Teraz liczy się tylko poprawa, a stara ocena jest pokazana w nawiasie: `5- (2)`.
- **Zmiana: średnia taka sama jak w dzienniku** — jeśli szkoła ma włączone średnie, Vultron pokazuje dokładnie tę z eduVULCAN (z wagami i zasadami szkoły). Uwaga: przez to średnie mogą się lekko zmienić także u osób bez poprawionych ocen.
- **Szkoły bez średnich w dzienniku** — Vultron liczy średnią sam, jak dotąd, ale już bez poprawionych ocen. Taka średnia ma znak `≈`.
- **Zmiana: nowy wygląd planu przedszkola** — każdy dzień to osobna kolumna z listą zajęć po kolei. „TERAZ" świeci tylko przy zajęciach, które właśnie trwają. Koniec z dziesiątkami pustych wierszy.

## **7.1.3 - Jungfru** — 2026-10-08
- **Naprawa: Kolejne zmiany po stronie eduvulcan i problemy z logowaniem. Fix**

## **7.1.2 - Jungfru** — 2026-10-02
- **Nowość: średnia procentowa dla przedmiotów oceniających w %** — oceny w formacie `47 (%)` były wyświetlane, ale nie wchodziły do żadnej średniej.
    - Nowy atrybut `srednia_proc` (średnia arytmetyczna procentów, jedno miejsce po przecinku) w sensorze ocen; karta ocen pokazuje go jako „Średnia: 55%".
    - Skale nie są mieszane: oceny 1–6 liczą się do dotychczasowej `srednia`, procenty wyłącznie do `srednia_proc`. Vultron nie przelicza % na oceny, bo progi ustala szkoła/nauczyciel.
    - Rozpoznawane formaty: `47 (%)`, `47%`, `47,5 (%)` (wartości spoza 0–100 są pomijane). Średnia jest zwykła, nieważona (tak jak dotychczasowa).
- **Naprawa: kolory ocen w karcie ocen** — oceny procentowe były kolorowane według cyfr (np. `63 (%)` na zielono przez „6"); teraz zawsze niebieskie. `NB`, `NP` i `BZ` były zielone przez literę „B" — teraz są szare.
- **Dokumentacja:** nowa sekcja README „Plan Lekcji — własne zajęcia z kalendarza HA" (format tytułu wydarzenia `Imię: Nazwa zajęć`, lokalizacja → sala, opis → notatka, limity).
- **Zależności i CI:** `beautifulsoup4` >= 4.15.0; aktualizacja akcji GitHub w workflow wydań (`setup-qemu-action`, `login-action`, `setup-buildx-action`, `build-push-action`).

## **7.1.1 - Jungfru** — 2026-09-22
- **Naprawa: kolizje globalnych klas CSS między kartami** [#52](https://github.com/htomasz/vultron/issues/52)— karty renderują w light DOM (bez Shadow DOM), więc ich `<style>` był globalny dla całej strony. Powodowało to dwa realne błędy przy jednoczesnym wyświetleniu kilku kart na jednym dashboardzie:
    - `vultron-card` (plan) + `vultron-grades-card` (oceny): współdzielona klasa `.vultron-tooltip` powodowała poziomy scroll całego widoku na telefonie (dymek ocen dziedziczył `white-space: nowrap` z planu lekcji, wystając poza ekran mimo `visibility: hidden`).
    - Karty z modalami (`wiadomości`, `uwagi`, `zadania`, `osiągnięcia`) współdzieliły `.modal-body` — w zależności od kolejności renderowania, treść wiadomości mogła dostać niechciane `white-space: pre-wrap`, albo uwagi/zadania tracić zachowanie ręcznych złamań linii.
    - Naprawa: każda karta dostała własny, unikalny prefiks dla wszystkich swoich klas CSS (np. `.modal-header` → `.uwagi-modal-header`, `.work-modal-header` itd.) — 52 unikalne nazwy w 10 plikach, zero współdzielenia między kartami.
    - **Uwaga, zmiana łamiąca**: jeśli masz własne style przez `card-mod` celujące w stare, gołe nazwy klas (np. `.vultron-tooltip`, `.modal-header`) — trzeba je zaktualizować do nowych, sprefiksowanych nazw.

## **7.1.0 - Jungfru** — 2026-09-21
- **Nowość: opcjonalne wsparcie dla GPE Gdańsk (`uonetplus.edu.gdansk.pl`)** — Vultron może teraz obsługiwać konta na Gdańskiej Platformie Edukacyjnej, obok dotychczasowego eduVULCAN. Domyślne zachowanie (`provider: eduvulcan`) zostaje bez zmian — nowy dostawca aktywuje się wyłącznie jawnym ustawieniem `provider: gdansk` w konfiguracji.
    - Obsługuje: oceny, plan lekcji (3 tygodnie wstecz/obecny/w przód), frekwencję i statystyki, zadania/sprawdziany (terminarz), uwagi, osiągnięcia, zebrania oraz nagłówki wiadomości (treść wiadomości GPE otwiera się w webowej skrzynce portalu, z zachowaniem lokalnego podglądu treści dla eduVULCAN).
    - Reużywa istniejących formatów sensorów i kart Lovelace — zero nowych kart do zainstalowania.
    - Duże skrzynki wiadomości dzielone są automatycznie na kilka sensorów, żeby zmieścić się w limicie atrybutów Home Assistant; karta wiadomości scala je przezroczyście.
    - Kod dostawcy w pełni odizolowany w osobnym module (`gdansk.py`) - zero zmian w istniejącej logice eduVULCAN, zero zależności od bazy SQLite dla tego dostawcy.
    - Bezpieczeństwo: rygorystyczna walidacja adresu URL przed każdą nawigacją/requestem (host, port, schemat, brak danych logowania w URL), blokada podążania za przekierowaniami z nagłówkami sesji, izolacja ciasteczek między usługami portalu.
    - **Poprawka dotycząca też eduVULCAN**: karta statystyk (`vultron-stats-card.js`) poprawnie odróżnia teraz brak danych od realnego zera (wcześniej oba przypadki wyświetlały "0").
    - Zweryfikowane na żywym koncie GPE 26.06 (HAOS 18.3 / HA 2026.9.3), pokryte 30 testami Python i 5 testami karty wiadomości.
    - Znane ograniczenia: brak obsługi przedszkoli GPE, brak treści wiadomości/wysyłania/odbierania powiadomień push, brak usprawiedliwiania nieobecności, inna semantyka liczenia ocen niż w eduVULCAN, brak szczęśliwego numerka. Szczegóły i wymagania w `GDANSK.md`.
    - Dzięki [@KamillJot](https://github.com/KamillJot) za wkład i szczegółowo udokumentowany, przetestowany kod.

## **7.0.7 - Jungfru** — 2026-09-19
- **Nowość: samodzielny kontener Docker (bez Home Assistant Supervisor)** — Vultron może teraz działać obok Home Assistant Container, dla osób bez Supervisora. Tryb wykrywany automatycznie po obecności `SUPERVISOR_TOKEN` (`IS_ADDON`) — zero zmian w zachowaniu dodatku HA, bit-identyczna ścieżka co dotychczas.
    - W trybie standalone: `HA_TOKEN`, `HA_URL`, `HA_WS_URL` zamiast `SUPERVISOR_TOKEN`/adresów Supervisora, dokładnie zgodnie ze zgłoszoną prośbą.
    - `HA_TOKEN` czytany domyślnie z pliku-sekretu (`/run/secrets/ha_token`, konfigurowalna ścieżka przez `HA_TOKEN_FILE`), z fallbackiem na zwykłą zmienną środowiskową — pliki nie są widoczne w `docker inspect`, w przeciwieństwie do env.
    - Nowy przykładowy `compose.example.yaml` oraz sekcja "Metoda 3: Samodzielny kontener Docker" w README, z pełną instrukcją (token, `options.json`, dobór sieci Docker/`network_mode: host`, rozwiązywanie typowych problemów w tym SELinux na RHEL/Fedora/Rocky/AlmaLinux).
    - Sprawdzone end-to-end na realnej, niezależnej instancji Home Assistant Container: logowanie do eduVULCAN, publikacja sensorów przez REST API i rejestracja kart Lovelace przez WebSocket — wszystko potwierdzone działające, na koncie ucznia szkoły i przedszkolaka.
    - CI: nowy job `release-docker` w `release.yml` — przy każdym tagu wersji obraz jest teraz automatycznie budowany (multi-arch: `amd64`+`aarch64`) i publikowany do `ghcr.io/htomasz/vultron`, obok dotychczasowego release'u dodatku jako zip.

## **7.0.6 - Jungfru** — 2026-09-19
- Logowanie (Reliability)
    - **Wykrywanie okna zgody na cookies**: zamiast jednorazowego sprawdzenia zaraz po załadowaniu strony logowania (które mogło łapać moment PRZED pojawieniem się banera), dodatek czeka teraz do 6 sekund, aż którykolwiek z okien cookies stanie się widoczny, zanim uzna że go nie ma.
    - **Odporność na trzy wersje strony wyboru profilu**: eduVULCAN serwuje różnym userom (w zależności od regionu/CDN) różne wersje strony po zalogowaniu — nową (WordPress), starą (ASP.NET) i historyczną. Dodatek próbuje teraz wszystkie trzy warianty selektorów po kolei i loguje, którą wersję wykrył.
    - **Pomijanie nieaktywnych dostępów do dziennika**: dziennik dziecka, które ukończyło jedną szkołę i zaczęło kolejną, jest teraz wykrywany od razu (przekierowanie na `/End/NieaktywnyUczen`) i pomijany, zamiast marnować czas na pełne odpytywanie API.
    - **Podniesiono limit ładowania strony** z 45 do 75 sekund — obserwowane timeouty renderera lądowały tuż poniżej starego limitu u kilku niezależnych userów, co sugeruje, że strona logowania stała się cięższa do wyrenderowania.
    - **Wyłączono flagę `--js-flags=--max-old-space-size=128`** (ograniczenie pamięci JS na proces renderera Chromium) — był to relikt z czasów, gdy dodatek wymuszał jeden proces renderera na całość; po usunięciu tego wymuszenia limit nadal obowiązywał każdy proces (główny + iframe) z osobna, co mogło przyczyniać się do spowolnień na słabszym sprzęcie.
- **Nowość: obsługa kont przedszkolnych** — dodatek automatycznie rozpoznaje konta przedszkolne (odróżnia je od uczniów szkół) i udostępnia dla nich osobny, kompletny zestaw sensorów i kart:
    - **Plan zajęć** — tabela tydzień/godziny, ten sam zakres co dla uczniów szkół (tydzień wstecz + obecny + następny).
    - **Ewidencja obecności** — kalendarz miesięczny z kolorowymi znacznikami obecny/nieobecny/brak danych, plus zakładka z dokładnymi godzinami wejścia/wyjścia i wyliczonym czasem pobytu dla bieżącego miesiąca.
    - **Jadłospis** — skład posiłków i alergeny dla dziś i jutro, z zakładkami; wartości odżywcze celowo pominięte (przekraczały limit rozmiaru encji Home Assistant — patrz naprawa poniżej).
    - **Zebrania** — temat i pełna agenda, z rozróżnieniem przeszłych/nadchodzących.
    - **Opłaty** — kwota do zapłaty, status, kwoty upomnień/odsetek/umorzeń. Numer konta bankowego i tytuł przelewu (z danymi dziecka) są zapisywane tylko w lokalnej bazie dodatku, nigdy w atrybutach encji Home Assistant.
    - **Informacje o placówce** — adres, dyrektor, dane kontaktowe.
    - **Nauczyciele** — lista z wyróżnieniem wychowawców.
- **Naprawiono pusty jadłospis przedszkola**: pełne wartości odżywcze (kalorie, białko, witaminy itd.) powodowały przekroczenie limitu Home Assistant na rozmiar atrybutów encji (16384 B) — encja urastała do ~18 kB i HA po cichu odrzucał zapis atrybutów. Usunięto wartości odżywcze z sensora i karty, zostawiając skład i alergeny.
- **Audyt bezpieczeństwa**: pełny przegląd pod kątem XSS, SQL Injection, memory leak i błędów logicznych (pyflakes, bandit, ręczna weryfikacja wszystkich kart JS i zapytań SQL). Dodano walidację schematu URL przed nawigacją Selenium na linki pobrane ze strony logowania (defense in depth, chroni przed nawigacją na `javascript:`/`data:` URI, gdyby taki href kiedykolwiek się tam znalazł).
- **Karta obecności przedszkola — usunięto weekendy z kalendarza**: zakładka "Kalendarz" pokazywała 7 kolumn (Pon-Niedz), mimo że przedszkole nie ma zajęć w weekendy — soboty i niedziele zostają teraz całkowicie pominięte, kalendarz pokazuje 5 kolumn (Pon-Pt).
- **Nowość: wiadomości dla przedszkola** — potwierdzono, że przedszkole ma dostęp do pełnego systemu wiadomości (ten sam co uczniowie szkół), z jedną różnicą architektoniczną: skrzynka jest wybierana przez sesję, nie przez jawny parametr, więc obsługa jest osobna funkcją niż dla szkół. Nowy sensor pokazuje liczbę nieprzeczytanych, treść (oczyszczoną z HTML) tylko dla nieprzeczytanych wiadomości, z tym samym mechanizmem oszczędzania requestów co dla uczniów szkół (nie pobiera ponownie treści już znanych wiadomości).

## **7.0.4 - Jungfru** — 2026-09-10
- **Chyba naprawiono wykrywanie iframe**

## **7.0.3 - Jungfru** — 2026-09-10
- **Zmiana logowania na EduVulcan**: usunieto `/dostep-do-dziennika/`. Dodano wykrywanie iframe.

## **7.0.2 - Jungfru** — 2026-09-10
- **Zmiana logowania na EduVulcan**: platforma przestała automatycznie przekierowywać po zalogowaniu na listę profili — dodatek teraz jawnie wchodzi na `/dostep-do-dziennika/`.
- **Poprawne wykrywanie kafelków uczniów**: usunięto fałszywe dopasowanie do linku menu "Dostęp do dziennika" (wcześniej liczony jako dodatkowy, nieistniejący profil).
- **Dzienniki bez okresów klasyfikacyjnych** (np. przedszkola): dziecko nie jest już całkowicie pomijane z synchronizacji — plan, frekwencja, wiadomości i uwagi działają normalnie, tylko sekcja ocen zostaje pusta.


## **7.0.1 - Jungfru** — 2026-09-09
- Bezpieczeństwo (Security)
    - **XSS w `vultron-stats-card.js`**: karta jako jedyna nie miała metody `_esc()`. Dodano ją i zastosowano do `p.nazwa`/`p.id` (nazwa i identyfikator przedmiotu z listy rozwijanej — dane pochodzące z zewnętrznego API Vulcan, wstrzykiwane bezpośrednio do `innerHTML`) oraz do `r.k` i wartości liczbowych w tabeli statystyk (defensywnie).
    - **Wsparcie białoetykietowych domen Vulcana**: niektóre samorządy hostują platformę Vulcan pod własną domeną (np. `edu.lublin.eu`) zamiast współdzielonej `eduvulcan.pl`. Domena jest teraz wykrywana dynamicznie z rzeczywistego URL-a po zalogowaniu i zapisywana per uczeń, zamiast być zakładana na sztywno — naprawia brak sensora dla uczniów z takich samorządów. Punkt wejścia logowania SSO (`eduvulcan.pl/logowanie`) pozostaje wspólny, niezależnie od domeny docelowej.

- Wydajność i stabilność, zwłaszcza na Raspberry Pi (Performance / Reliability)
    - **Reużycie sesji zamiast logowania Selenium w każdym cyklu**: przed uruchomieniem Chromium dodatek tanimi zapytaniami httpx sprawdza, czy zapisana sesja (z limitem wieku 6h) wciąż działa — osobno dla dziennika i dla wiadomości, dla każdego miasta/domeny. Selenium uruchamiane jest tylko, gdy sesja faktycznie wygasła. W logu widać jednoznacznie, co zadziałało: `--> Logowanie poprzez COOKIES - OK` / `--> Logowanie poprzez COOKIES - NO - USE CHROMIUM`.
    - **Zabezpieczenie przed osieroconymi procesami Chromium**: po zawieszeniu logowania (obserwowane na RPi4 z 2GB RAM bez swapu/zram, prowadzące do narastającego zużycia pamięci i restartu całego HA) `driver.quit()` zamykał wyłącznie sam proces chromedrivera, nigdy jego procesy potomne (chrome, zygote, renderer, GPU). Chromedriver uruchamiany teraz we własnej grupie procesów (`popen_kw={"start_new_session": True}`); po nieudanym `quit()` cała grupa jest zabijana jednym `os.killpg()`; dodatek rejestruje się jako "subreaper" (`PR_SET_CHILD_SUBREAPER`) i aktywnie odbiera osierocone procesy, niezależnie od tego, czy jest akurat PID 1 kontenera.
    - **Pętla retry logowania rozróżnia dwa rodzaje niepowodzenia**: zwykły, przejściowy timeout ładowania strony (Selenium odpowiedział — nadal ponawia do 3 razy jak dotychczas) od zawieszenia samej przeglądarki (lokalny błąd komunikacji z chromedriverem — rezygnuje natychmiast, zamiast tracić dodatkowe ~240s na ponawianie na martwej instancji).
    - **Backoff po kolejnych nieudanych logowaniach z rzędu**: +10 min przerwy za każde kolejne niepowodzenie (pierwsze niepowodzenie nie wydłuża przerwy), z twardym limitem +60 min — ogranicza ryzyko powtórnej blokady CAPTCHA przy uporczywym problemie.
    - **`_sent_hashes` (deduplikacja publikacji sensorów) chroniony wspólnym `threading.Lock`** zamiast `asyncio.Lock` — eliminuje realny wyścig między wątkiem synchronizacji wiadomości a główną pętlą asynchroniczną, możliwy przy porzuconym (po timeoucie) wątku.
    - Dodatkowe flagi Chromium ograniczające zużycie RAM (`--disable-component-update`, `--disable-domain-reliability`, `--disable-client-side-phishing-detection`, `--disable-hang-monitor`, `--disable-backgrounding-occluded-windows`).
    - Usunięcie zbędnej zależności `xvfb` z obrazu Dockera (Chromium działa w trybie `--headless`, serwer X nigdy nie jest potrzebny).
    - Diagnostyka: log dostępnej pamięci RAM (`/proc/meminfo`) tuż przed każdym uruchomieniem Selenium, oraz log wykrytej strefy czasowej/przesunięcia UTC przy starcie dodatku — czysto informacyjne, dają twardy dowód przy przyszłych zgłoszeniach zamiast zgadywania.

- Zarządzanie danymi (Data management)
    - **Retencja danych**: wpisy starsze niż ~1,5 roku są usuwane raz na dobę z tabel `schedule`, `remarks`, `timetable`, `frequency`, `free_days`, `meetings`, `frequency_stats`, `lucky_number`, `messages`, a zwolnione miejsce odzyskiwane przez `VACUUM`. Data wiersza jest kasowana WYŁĄCZNIE, gdy da się ją jednoznacznie rozpoznać I jest kalendarzowo poprawna (pełna konstrukcja `datetime`, poprawnie obsługuje lata przestępne) — przy jakiejkolwiek niepewności wiersz zostaje. `achievements` i `grades` świadomie WYŁĄCZONE z tej retencji: pierwsze nie ma kolumny z datą, drugie to dane, które rodzic prawdopodobnie chce mieć w wieloletniej historii. Operacja wykonywana pod pełną blokadą bazy, żeby `VACUUM` nie kolidował z żadnym równoległym zapisem.
    - **`ha_cache`** (pomocniczy cache do przywracania sensorów po restarcie HA) ma własną, znacznie krótszą retencję (60 dni) — usuwa wpisy po usuniętych/zmienionych dzieciach zamiast rosnąć bez końca.

- Automatyzacje / blueprinty
    - Naprawiono zalew powiadomień po restarcie Home Assistanta (`oceny.yaml`, `frekwencja.yaml`, `uwagi.yaml`, `wiadomosci.yaml`, `plan.yaml`): przywrócenie stanu encji z cache po restarcie HA daje `from_state: null`, co każdy blueprint błędnie interpretował jako "wszystko jest nowe", wysyłając powiadomienie o każdym już istniejącym wpisie naraz. Dodano warunek `trigger.from_state is not none` do każdego blueprintu.

- Dokumentacja
    - README: nowa sekcja "Wymagania sprzętowe (ważne dla Raspberry Pi)" z rekomendacją włączenia zram na urządzeniach z 2GB RAM bez swapu.

## **7.0.0 - Jungfru** — 2026-09-08
- Bezpieczeństwo (Security)
    - **XSS w kartach**: dodano brakującą metodę `_esc()` i escape'owanie danych z API w `vultron-uwagi-card.js` (treść, kategoria, autor, punkty), `vultron-grades-card.js` (przedmiot, ocena, data, opis kolumny, oceny proponowana/okresowa — we wszystkich 3 widokach) oraz `vultron-work-card.js` (przedmiot, typ, opis).
    - **Treść uwag** (`tresc`) czyszczona przez `clean_html()` przed zapisem do bazy — było to jedyne pole tekstowe trafiające do bazy bez żadnego oczyszczania.
    - `copy_resources()` kopiuje do publicznego `/config/www/vultron` wyłącznie pliki `vultron-*.js`, a nie każdy plik `.js` z katalogu dodatku.
    - Ostrzeżenie przy włączeniu trybu `trace`, że log zawiera dane osobowe.

- Poprawki danych (Data Fixes)
    - **Licznik nowych ocen**: porównywane są teraz pełne wpisy (kolumna + ocena + data). Kolumna z poprawą (np. 3 → 5) zawiera dwie oceny i przy poprzedniej logice zawsze była liczona jako nowa — stan `sensor.vultron_oceny_*` nigdy nie wracał do zera.
    - **Duchy i duplikaty w planie lekcji**: lekcje z Vulcana są resynchronizowane w obsługiwanym oknie dat. Wcześniej były wyłącznie wstawiane — odwołana lekcja zostawała w karcie, a przesunięta tworzyła duplikat (godzina wchodzi w skład klucza). Zajęcia własne z kalendarza HA pozostają nietknięte.
    - **Oceny wycofane** w dzienniku są usuwane z bazy (pełna podmiana ocen okresu).
    - **Oceny z modyfikatorem**: `_map_grade_to_num()` rozpoznaje `4+`, `5-`, `4.5`, `4,5` w ocenie proponowanej/końcowej; wynik przycinany do skali 1–6.
    - **Stan `sensor.vultron_freq_*`**: liczba nieobecności nieusprawiedliwionych zamiast stałego `0` (dotąd każda automatyzacja na `state` była martwa).
    - **Szczęśliwy numerek**: wartość `null` z API dawała dosłowny napis `"None"` jako stan encji.

- Odporność na błędne dane (Robustness)
    - **Wartości `null` z API**: `dict.get(klucz, domyślna)` nie chroni przed `null` — zwraca wartość domyślną tylko gdy klucza NIE MA. Zabezpieczono wszystkie miejsca, w których pojedyncze `null` wywalało całą sekcję ucznia: `adnotacja` i `sala`/`prowadzacy` (plan), `kategoriaFrekwencji` i `okresy` (frekwencja), `data` (uwagi), `tresc` (osiągnięcia), `dataOd`/`dataDo` (dni wolne), `uczen` (synchronizacja) oraz `okresy[-1]["id"]` (logowanie).
    - **Format godzin bez `T`**: zabezpieczono `split("T")[1]` w planie lekcji i frekwencji — dotąd `IndexError` wywalał całą sekcję; teraz pomijany jest wyłącznie wadliwy wpis z ostrzeżeniem w logu.
    - **Izolacja błędów**: wadliwy wpis okresu klasyfikacyjnego lub przedmiotu nie przerywa już całej sekcji ocen/frekwencji.
    - Widoczność kolizji slugów uczniów podniesiona z `DEBUG` na `WARNING`.

- Stabilność (Stability)
    - **Blokady bazy**: ruch sieciowy wyprowadzony poza sekcje krytyczne SQLite w `run_messages_sync` (4 etapy: odczyt → sieć → zapis → publikacja) oraz `_fetch_timetable` (równoległe zapytania o szczegóły odpalały się pod `db_lock`).
    - **Limity czasu**: 10 min na logowanie Selenium (`os._exit(1)` → restart przez Supervisora) i 10 min na synchronizację wiadomości (restart po 3 timeoutach z rzędu, by nie wyczerpać puli wątków `asyncio.to_thread`).
    - **SIGTERM** respektowany między etapami cyklu — dotąd sygnał był ignorowany do końca cyklu.
    - **Cache encji**: hash zapisywany dopiero po udanym POST. Wcześniej nieudane odtworzenie (HA jeszcze wstaje, timeout) trwale blokowało publikację encji aż do restartu dodatku. Dodano brakujące `finally` zamykające połączenie z bazą.
    - `driver.quit()` przy błędzie tuż po starcie Chromium (zapobiega zombie-procesom) oraz zamykanie gniazda WebSocket przy nieudanym handshake'u.

- Migracje (Database)
    - Dodano **wersjonowanie schematu** (`PRAGMA user_version`) wraz z mechanizmem migracji — `CREATE TABLE IF NOT EXISTS` nie zmienia istniejącej tabeli, więc bez tego działające instalacje zostawały na starym schemacie.
    - Migracja v1: przebudowa tabeli `grades` (usunięcie ograniczającego klucza głównego uniemożliwiającego przechowanie dwóch ocen w jednej kolumnie). Dotychczasowe oceny są zachowywane.

- Optymalizacja (Performance)
    - **Wiadomości**: treść pobierana wyłącznie dla nowych wiadomości; dla znanych aktualizowany jest tylko status przeczytania. Ograniczenie z ~50 zapytań na cykl do 0 (mniejsze ryzyko CAPTCHA).
    - **`executemany` we wszystkich 8 sekcjach** (oceny, uwagi, wiadomości, plan lekcji, zadania, frekwencja, osiągnięcia, zebrania) — krótszy czas trzymania locków bazy.
    - **Usunięto Xvfb**: Chromium działa w `--headless`, więc `pyvirtualdisplay` uruchamiał zbędny serwer X przy każdym cyklu.
    - **Trwałe połączenie SQLite dla `ha_cache`** zamiast otwierania i zamykania go przy każdej publikacji sensora (~25 razy na ucznia na cykl).
    - Dodatkowe flagi Chromium ograniczające zużycie RAM: `--renderer-process-limit=1`, `--js-flags=--max-old-space-size=128`, `--disable-features=Translate,BackForwardCache,AcceptCHFrame`.

- Porządki (Chore)
    - Usunięto nieużywane zależności: `requests`, `aiosqlite`, `pyvirtualdisplay`.

## **6.4.0 - Kurkkuviipale** — 2026-09-07
- Nowości (New Features)
    - **Własne zajęcia w planie lekcji**: Dodano możliwość ręcznego dopisywania dodatkowych zajęć (np. korepetycje, zajęcia dodatkowe) do planu lekcji dziecka, bez ingerencji w dane pobierane z EduVulcan.
        - Źródłem własnych zajęć jest zwykła encja kalendarza Home Assistant (domyślnie `calendar.local_szkola`, konfigurowalna przez nową opcję `calendar_entity`). Nazwa to local_szkola
        - Format wydarzenia w kalendarzu: `Imię Nazwisko: Nazwa zajęć` (np. `Jan Kowalski: Dodatkowy angielski`). Pole „Lokalizacja” trafia jako sala, „Opis” jako notatka. Pamietaj aby trzymac sie godzin start/stop lekcja z Twojego kalendarza.
        - Skrypt (`vultron.py`) w każdym cyklu synchronizacji dociąga wydarzenia z kalendarza w tym samym oknie dat co plan (poprzedni/obecny/następny tydzień), dopasowuje je do ucznia po prefiksie imienia i publikuje jako pełnoprawne wpisy w encji planu, obok danych ze szkoły.
        - Obsługiwana jest edycja i usuwanie wydarzeń w kalendarzu HA — zmiany są w pełni synchronizowane (stare wpisy znikają, gdy wydarzenie zniknie z kalendarza).
        - Karta planu (`vultron-card.js`) oznacza własne zajęcia odrębną, turkusową plakietką „Z kalendarza”, żeby nie pomylić ich z danymi oficjalnymi.
        - Zabezpieczenia: ścisła walidacja i przycinanie długości pól (przedmiot/sala/notatka), twardy limit 200 własnych wpisów na ucznia w oknie synchronizacji, ignorowanie wydarzeń całodniowych, brak modyfikacji istniejącej bazy (nowy status `WLASNE` w już istniejącej kolumnie `status` — bez migracji schematu).
        - Funkcja jest w pełni opcjonalna: brak kalendarza o podanej nazwie (404) lub jego niedostępność są ciche (log na poziomie DEBUG) i nie wpływają na resztę synchronizacji.

## **6.3.6 - Kurkkuviipale** — 2026-06-03
- Nowości (New Features)
    - Oceny końcowe: Dodano zakładkę **KOŃCOWE** w karcie ocen (`vultron-grades-card.js`).
        - Wyświetla dla każdego przedmiotu ocenę proponowaną oraz końcową w postaci cyfr (1–6).
        - Mapowanie ocen słownych na cyfry: `celujący→6`, `bardzo dobry→5`, `dobry→4`, `dostateczny→3`, `mierny→2`, `niedostateczny→1`.
        - Format `cyfra/cyfra` (np. `4/5`) — brana jest mniejsza wartość.
        - Zachowanie wyświetlane zawsze (nawet bez oceny liczbowej), ale **nie wliczane do średniej**.
        - Dwie osobne średnie na dole tabeli: średnia proponowanych i średnia końcowych (zaokrąglone do 3 miejsc po przecinku).
    - Backend (`vultron.py`): Pobieranie `proponowanaOcenaOkresowa` i `ocenaOkresowa` z API i publikowanie ich w atrybutach sensora jako `proponowana`, `proponowana_num`, `okresowa`, `okresowa_num`, `srednia_proponowanych`, `srednia_okresowych`.

## **6.3.5 - Kurkkuviipale** — 2026-05-28
- Poprawki (Bug Fixes)
    - Dodano klasę `Service` z jawnym wskazaniem ścieżki do systemowego sterownika (`/usr/bin/chromedriver`).
    - Rozwiązuje to problem z Selenium Managerem, który na architekturze aarch64/Alpine ignorował systemowe pakiety i pobierał z internetu pliki binarne niekompatybilne z Raspberry Pi.
    - Ścieżka do przeglądarki (`chromium-browser`) pozostawiona bez zmian.
    - Podbito wersję dodatku do 6.3.5.

## **6.3.4 - Kurkkuviipale** — 2026-05-27
- Poprawki (Bug Fixes)
    - Usunięcie błedu mrugania kafli.

## **6.3.3 - Kurkkuviipale** — 2026-05-14
- Nowości (New Features)
    - Obsługa wielu szkół

## **6.3.1 - Kurkkuviipale** — 2026-05-06
- Poprawki (Bug Fixes)
    - Naprawa krytyczna zmian po stronie eduvulkan.

## **6.2.3 - Kurkkuviipale** — 2026-04-08
- Nowości (New Features)
    - Dodano zebrania
- Poprawki (Bug Fixes)
    - Poprawiono szczesliwy numerek

## **6.2.2 - Kurkkuviipale** — 2026-03-28
- Nowości (New Features)
    - Dodano logike dni wolny od zajęć

## **6.2.1 - Kurkkuviipale** — 2026-03-18

- Poprawki (Bug Fixes)
    - Crash runtime - naprawiono błąd powodujący awarię aplikacji po każdym cyklu synchronizacji wywołaną nieprawidłowym użyciem `secrets.SystemRandom()`.
    - SQLite - wyeliminowano ryzyko korupcji bazy danych podczas synchronizacji wiadomości działającej w osobnym wątku systemowym.
    - Cache sensorów - usunięto race condition mogący powodować zduplikowane wysyłki lub utratę danych przy równoległym pobieraniu informacji.
    - Zamykanie dodatku - naprawiono brak czyszczenia zasobów HTTP przy zatrzymaniu spowodowanym wykryciem blokady CAPTCHA.

## **6.2 - Kurkkuviipale** — 2026-03-17
- Nowości (New Features)
    - Nowy HTMLStripper z obsługą pogrubienia, kursywy, linków i obrazów
    - Ochrona XSS (is_safe_url() blokuje javascript:, data:)
    - Zachowanie formatowania Markdown w clean_html()

## **6.1.6 - Smoot** — 2026-03-16
- Poprawki (Bug Fixes)
    - Wiadomości - naprawiono dublowanie wiadomości między kontami rodzeństwa – system przypisuje je teraz wyłącznie po unikalnym globalKeySkrzynka zamiast po imieniu, co eliminuje błędne dopasowania.
    - Terminarz - usunięto problem „Brak opisu" i ucinania długich treści - wprowadzono hybrydowe pobieranie danych, które gwarantuje pełne opisy zadań i sprawdzianów (z enterami i linkami).

## **6.1.3 - Smoot** — 2026-03-11
- Poprawki (Bug Fixes)
    - Dodano zamykanie dodatku w momencie problemow z logowaniem. (Problemy edu z 11.0-3.2026)

## **6.1.2 - Smoot** — 2026-03-10
- Poprawki (Bug Fixes)
    - Dodano mechanizm autoleczenia. Skrypt teraz automatycznie usuwa plik cache ciasteczek (bul.pkl), gdy serwer odrzuci żądanie z powodu zbyt dużych nagłówków. Zapobiega to trwałemu zablokowaniu synchronizacji wiadomości.

## **6.1 - Smoot** — 2026-03-03
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


## **6.0 - Oxgang** — 2026-02-26
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

## **4.2 - 200kcal** — 2026-02-19
- Skrypt Python (vulp.py) - Podział na 3 encje: Skrypt generuje teraz oddzielne sensory dla każdego dziecka:
    - sensor.vultron_plan_[slug]_prev (Tydzień poprzedni)
    - sensor.vultron_plan_[slug]_curr (Tydzień obecny)
    - sensor.vultron_plan_[slug]_next (Tydzień przyszły)
- Karta Lovelace (vultron-card.js)
    - Dynamiczne przełączanie: Karta automatycznie wykrywa bazową nazwę encji i podmienia końcówki (`_curr`, `_prev`, `_next`) podczas klikania strzałek.
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

## **4.1 - „16KB"** — 2026-02-19
- **Oceny**
    - Podział Ocen: Rozbito oceny na dwie niezależne encje: `_p1` (Okres 1) oraz `_p2` (Okres 2).
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
    - (`vul-monitor.py`)sumuje rozmiary danych Vultron i raportuje szczegóły oraz ostrzeżenia, a alert włącza się, gdy dane encji przekroczą krytyczny limit 16 KB. Wszystkie przekroczenia progów WARNING(14000B) i CRITICAL(15500B) są logowane w konsoli [MONITOR].
    Wiecej w dziale [🔍 Monitoring](https://github.com/htomasz/vultron#-monitoring)

## **4.0 - „Furlong/fortnight"** — 2026-02-17
- Pełna migracja na SQLite: Dane są teraz trwałe, dostępne offline w bazie vultron.db.
- Optymalizacja płynności UI: Ograniczono przesył danych do HA do obecnego i poprzedniego tygodnia, co wyeliminowało lagi w interfejsie.
- Redukcja zasobów: Znacząco zmniejszono zużycie RAM przez Chromium (blokada obrazów/GPU) oraz wprowadzono bezpieczny zapis plików.
- Najważniejsze: kolor w planie zgadza sie z resztą

## **3.4.2 - „Siriustek"** — 2026-02-16
- Dodano 5 gotowych schematów automatyzacji dla HA (Oceny, Frekwencja, Plan, Uwagi, Wiadomości).

## **3.4.1 - „Siriustek"** — 2026-02-16
- Implementacja zaawansowanego skanowania bezpieczeństwa (CodeQL, Bandit, Trivy, Hadolint).
- Dodanie mechanizmu pre-commit i automatyzacji GitHub Actions.
- Optymalizacja dokumentacji i integracja z "My Home Assistant"

## **3.2 - „Siriustek"** — 2026-02-12
- Automatyczne odświeżanie kart (Cache-busting): Koniec z ręcznym czyszczeniem ciasteczek i cache'u przeglądarki po aktualizacji dodatku. System automatycznie wersjonuje pliki .js.
- Auto-Discovery kart UI: Skrypt `setup_ui.py` sam wykrywa wszystkie pliki kart w folderze i rejestruje je w zasobach Lovelace.
- Inteligentny start: Dodano pętlę "retry" przy łączeniu z API Home Assistant. Jeśli system startuje po awarii prądu, dodatek cierpliwie poczeka, aż rdzeń HA będzie gotowy.
- Bezpieczna północ: Naprawiono błąd w skrypcie `run.sh`, który mógł powodować błędy w cyklu synchronizacji o godzinie 00:00.
- Optymalizacja obrazu: Przebudowano `Dockerfile` (czyszczenie cache apk, instalacja przez requirements.txt), co owocuje mniejszym i stabilniejszym kontenerem.
- Nowy system instalacji: Dodano obsługę przycisku "My Home Assistant" oraz uporządkowano dokumentację README.

## **3.000009 - „Muggeseggele"** — 2026-02-10
- Dodano acziwmenety :D
    - vulos.py
    - vultron-osiagniecia-card.js
    - automatyzacji do niej nie będzie - nikt nie ma tak zajebistego dziecka
- Chyba wszystko juz mamy :P
- Wszędzie będzie Glassmorphism + ban
- Koniec rozwoju (nieeee :D)

## **2.5 - „Peninkulma"** — 2026-02-05
- Dodano wyświetlanie za co ocena (po najechaniu na ocenę)
    - vulo.py - zmiany w zapisywaniu ocen
    - Dostosowano karte vultron-grades-card.js
    - Dostosowano automatyzacje Node-RED oraz HA do powiadomień o nowych ocenach
    - Dodano Glassmorphism i artretyzm

## **2.4 - „Iteru"** — 2026-02-01
- Zrefactoryzowano kod dla vulp.py oraz vulf.py
- Zmieniono days=61 w vuls.py
- Naprawiono "zielonkę kreskę" zeby nie konczyła sie na czwartku.
- Dodano natywne automatyzacje dla HA
- Poprawiono automatyzacje dla Node-RED
- Zaktualizowane karte planu o ładne owalne cosie
- Dodano awaryjne zabijanie kontenera w sytuacji ze `vul.py` logowanie do portalu nie przejdzie.

## **2.3 - „Sheppey"** — 2026-01-30
- Dodano automatyczne zabijanie kontenera w momencie gdy system wykryje ze nie moze sie zalogowac na strone.
- Czytanie treści wiadomości.
- Karta wiadomosci
    - Po kliknieciu mozna zobaczyć (oraz skopiować :D) treść wiadomosci.

## **2.2 - „Saunakalja"** — 2026-01-29
- Karta terminarz. oceny
    - Dodano limit
- Karty *.js
    - Próba ujednolicenia wyglądu
- Dokumentacja
    - Dodano zrzuty ekranów wszystkich kart.
- Automatyzacja
    - Dodano przykładowe automatyzacje w Node-Red (dział automatyzacja)

## **2.1 - „Kenno"** — 2026-01-27
- Dodano do karty planu
    - Dodano status frekwencji na danym przedmiocie w ciagu dnia (informacja pokaze sie tylko jak nauczyciel ją wprowadzi)
    - Dodano pasek pokazujacy aktualna godzine
    - Dodano inny kolor dla kolumny aktualnego dnia
- Dodano funkcje pobierania frekwencji oraz karte frekwencji lovelace
    - statystyka frekwencji od poczatku roku wraz z procentową reprezentacja

## **2.0 - „Poronkusema"**
- Dodano chyba pełna obsługę multi-kinderpunkow

## **1.2.5 - „नीलो चूहा"** — 2026-01-24
- Dodano sortowanie do kart
    - karta Oceny - sortowanie (data|subject)
    - karta Terminarz - sortowanie rosnąco, malejąco (desc,asc)
    - karta Uwagi - sortowanie rosnąco, malejąco (desc,asc)

## **1.2.4 - „Shǎbī de Tómǎsī"**
- Karta plan - dodano podział na 2 lekcje o tej samej godzinie. Grupy albo błąd eduvulcan

## **1.2.3 - „Chokochoko Mfunguo"**
- Karta plan - dodano daty do aktualnego tygodnia, oraz dane nauczycieli danego przedmiotu
- Karta oceny - dodano sortowanie
- Karta wiadomosci - dodano sortowanie oraz limit
- Karta uwagi - dodano sortowanie oraz limit

## **1.2.2 - „EKEN 4K :P"** — 2026-01-19
- Dodano podswietlanie aktywnego dnia na dzienniku
- Dodano sortowanie w zadaniach domowych/sprawdzianach

## **1.2.1 - „Tin short"** — 2026-01-18
- Dodano informacje o "zwolnieniu uczniów do domu"

## **1.2 – „Messenger Burger"** — 2026-01-17
- Dodano obsługę
    - wiadomości i licznik nieprzeczytanych.

## **1.1 – „Feedback boobs"**
- Dodano obsługę
    - uwag i pochwał

## **1.0 – „First Contact"**
- Pierwsza wersja integracji z EduVulcan.
- Dodano:
    - plan lekcji
    - oceny
    - sprawdziany i zadania
