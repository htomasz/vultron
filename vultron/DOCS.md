# Vultron

Dodatek pobiera dane z dziennika eduVULCAN (opcjonalnie GPE/UONET+ Gdańsk) i udostępnia je w Home Assistant jako encje oraz karty Lovelace.

Pełna dokumentacja (karty dashboardu, automatyzacje, monitoring, debugowanie): [README](https://github.com/htomasz/vultron#readme).

## Pierwsze uruchomienie

1. W zakładce **Konfiguracja** wpisz dane logowania (tabela niżej).
2. Kliknij **Zapisz**, a potem **Uruchom**.
3. Obserwuj zakładkę **Logi**, czy logowanie przechodzi poprawnie.

Karty JavaScript dodatek rejestruje w zasobach Lovelace sam, przy każdym starcie. Jeśli karta się nie pojawia, odśwież przeglądarkę z pominięciem pamięci podręcznej (`Ctrl + F5`).

## Konfiguracja

| Opcja | Opis | Domyślnie |
| :--- | :--- | :--- |
| `provider` | `eduvulcan` albo `gdansk` (GPE/UONET+ pod adresem uonetplus.edu.gdansk.pl). | `eduvulcan` |
| `student_name` | Tylko GPE: dokładne imię i nazwisko ucznia. Puste pole pobiera wszystkich uczniów. Dla eduVULCAN jest ignorowane. | puste |
| `username` | eduVULCAN: adres e-mail. GPE Gdańsk: login konta podany przy rejestracji. | — |
| `password` | Hasło do dziennika. | — |
| `log_level` | `info`, `debug` albo `trace`. `trace` zapisuje do logu pełne odpowiedzi dziennika (oceny, uwagi, treści wiadomości): używaj tylko do diagnozy i wyłącz po niej. Linie synchronizacji mają znacznik źródła: `[INFO/API]` albo `[INFO/WWW]`. | `info` |
| `test_mode` | Pomija przerwy z harmonogramu (nocne i weekendowe godziny pobierania). Odstęp między pobraniami zostaje. Tylko do testów. | `false` |
| `mobile_api` | Dane bez przeglądarki, co 15 minut. Kiedy włączyć: niżej. | wyłączona |
| `mobile_interval` | Tylko z `mobile_api`: co ile minut pobierać dane (10-120). | `15` |
| `mobile_web_refresh` | Tylko z `mobile_api`: codzienne logowanie przeglądarką po osiągnięcia i przedszkole. Wyłącz, jeśli ich nie używasz. | włączona |

`mobile_api`, `mobile_interval` i `mobile_web_refresh` pokazuje przełącznik nieużywanych opcji opcjonalnych w zakładce Konfiguracja.

## Co zaznaczyć? (szybki wybór)

| Twoja sytuacja | Portal dziennika | API mobilne | Imię i nazwisko ucznia |
| :--- | :--- | :--- | :--- |
| **eduVULCAN płatny**, dziecko w szkole | `eduvulcan` | **włączone** | puste |
| **eduVULCAN płatny**, dziecko w przedszkolu (też obok ucznia) | `eduvulcan` | wyłączone | puste |
| **eduVULCAN darmowy** | `eduvulcan` | wyłączone | puste |
| **Samorząd z własną domeną Vulcana** (np. Lublin) | `eduvulcan` | wyłączone (niesprawdzone) | puste |
| **GPE Gdańsk** | `gdansk` | nie dotyczy | dokładne imię i nazwisko albo puste (wszyscy) |

Resztę zostaw domyślnie.

## API mobilne (`mobile_api`)

**Bez API mobilnego (domyślnie):** Vultron loguje się do dziennika przeglądarką, tak jak Ty, i pobiera wszystko co 40-60 minut.

**Z API mobilnym:** Vultron raz loguje się przeglądarką i rejestruje jako telefon („Vultron (Home Assistant)”). Potem pobiera dane bez przeglądarki, co 15 minut. Tylko osiągnięcia i przedszkole dalej idą przez przeglądarkę (API mobilne ich nie ma), więc odświeżają się tylko przez kilka godzin dziennie.

**Kiedy włączyć:**
- płatny eduVULCAN i dziecko w szkole: włącz;
- darmowy eduVULCAN: nie włączaj, nie będzie wiadomości;
- dziecko w przedszkolu: nie włączaj, przedszkole będzie stare;
- GPE Gdańsk: nie dotyczy.

Encje i karty są takie same w obu trybach. Gdy API mobilne nie odpowiada, Vultron sam wraca do przeglądarki. Klucz urządzenia leży w `/data/hebe_device.json`: kto ma ten plik, ma dostęp do dziennika.

Szczegóły: [README → API mobilne](https://github.com/htomasz/vultron#-api-mobilne-opcja-mobile_api).

## Wiadomości: treść przeczytanych i status

- Kliknięcie przeczytanej wiadomości w karcie (szkoła i przedszkole) pokazuje jej treść z lokalnej bazy Vultrona, bez zapytania do eduVULCAN.
- Przycisk **Oznacz jako przeczytaną / nieprzeczytaną** zmienia status tylko w Home Assistant (licznik, karta, automatyzacje). eduVULCAN nie dostaje nic.
- Karta wysyła żądanie przez pomocnik `input_text.vultron_zadanie`, który dodatek tworzy sam przy starcie. Odpowiedź przychodzi w `sensor.vultron_wiadomosc_otwarta`: wyklucz go z historii (`recorder: exclude`), bo zawiera treść wiadomości.

Szczegóły: [README](https://github.com/htomasz/vultron#readme), sekcja „✉️ Wiadomości (Licznik i Lista)”.

## Kiedy dodatek się zatrzymuje

- **eduVULCAN dwa razy z rzędu odrzuci logowanie** (złe hasło albo weryfikacja). Dodatek kończy pracę, żeby nie zablokować konta, a `sensor.vultron_status` ma stan `authentication_error`. Sprawdź logowanie w dzienniku przez przeglądarkę, popraw dane i uruchom dodatek ponownie. Z `mobile_api` odrzucone hasło przy codziennym odświeżeniu sesji stron WWW daje tylko ostrzeżenie, bo dane z API mobilnego płyną dalej.
- **GPE Gdańsk** zatrzymuje dodatek już po pierwszym błędzie logowania. Szczegóły: [GDANSK.pl.md](https://github.com/htomasz/vultron/blob/main/GDANSK.pl.md).
- **Zawieszona przeglądarka** albo **trzy razy z rzędu przekroczony czas synchronizacji wiadomości**: proces kończy się twardo. Dodatek wstanie sam tylko z włączonym przełącznikiem **Watchdog** (zakładka Informacje).

Chwilowe błędy portalu (przerwa techniczna, błąd 502) nie zatrzymują dodatku: kolejna próba nastąpi w następnym cyklu.

## Linki

- [README](https://github.com/htomasz/vultron#readme)
- [Changelog](https://github.com/htomasz/vultron/blob/main/vultron/CHANGELOG.md)
- [GPE Gdańsk (PL)](https://github.com/htomasz/vultron/blob/main/GDANSK.pl.md) / [(EN)](https://github.com/htomasz/vultron/blob/main/GDANSK.md)
- [Instalacja bez Supervisora (Docker)](https://github.com/htomasz/vultron/blob/main/DOCKER.md)
- [Zgłoś błąd](https://github.com/htomasz/vultron/issues)
