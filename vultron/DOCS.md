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
| `log_level` | `info`, `debug` albo `trace`. `trace` zapisuje do logu pełne odpowiedzi dziennika (oceny, uwagi, treści wiadomości): używaj tylko do diagnozy i wyłącz po niej. | `info` |
| `test_mode` | Pomija przerwy z harmonogramu (nocne i weekendowe godziny pobierania). Odstęp między pobraniami zostaje. Tylko do testów. | `false` |
| `mobile_api` | Opcjonalna, tylko eduVULCAN. Dane przez API mobilne, bez przeglądarki (opis niżej). | wyłączona |
| `mobile_interval` | Opcjonalna. Minuty między cyklami z API mobilnym w dni robocze w dzień (10-120). | `15` |
| `mobile_web_refresh` | Opcjonalna, tylko z `mobile_api`. Wyłączona: bez codziennego logowania przez przeglądarkę, ale osiągnięcia, dni wolne i przedszkole przestają się aktualizować. | włączona |

`mobile_api`, `mobile_interval` i `mobile_web_refresh` pokazuje przełącznik nieużywanych opcji opcjonalnych w zakładce Konfiguracja.

## API mobilne (`mobile_api`)

- Przy pierwszym cyklu po włączeniu opcji Vultron loguje się przez przeglądarkę i rejestruje jako urządzenie „Vultron (Home Assistant)”, jak aplikacja w telefonie.
- Potem oceny, plan, terminarz, uwagi, frekwencja, numerek, zebrania i wiadomości idą z API mobilnego, bez przeglądarki, co `mobile_interval` minut. Encje i karty bez zmian.
- Osiągnięcia, dni wolne i przedszkole idą przez strony WWW: przeglądarka loguje się do nich najwyżej raz na dobę. `mobile_web_refresh: false` wyłącza to logowanie (te sekcje przestają się wtedy aktualizować).
- Gdy API mobilne nie odpowiada, cykl idzie przez strony WWW jak dotąd.
- Klucz urządzenia leży w `/data/hebe_device.json`: kto ma ten plik, ma dostęp do dziennika.

Szczegóły: [README → API mobilne](https://github.com/htomasz/vultron#-api-mobilne-opcja-mobile_api).

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
