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
| `test_mode` | Pomija przerwy z harmonogramu (nocne i weekendowe godziny pobierania). Odstęp 40-60 minut między pobraniami zostaje. Tylko do testów. | `false` |

## Kiedy dodatek się zatrzymuje

- **eduVULCAN dwa razy z rzędu odrzuci logowanie** (złe hasło albo weryfikacja). Dodatek kończy pracę, żeby nie zablokować konta, a `sensor.vultron_status` ma stan `authentication_error`. Sprawdź logowanie w dzienniku przez przeglądarkę, popraw dane i uruchom dodatek ponownie.
- **GPE Gdańsk** zatrzymuje dodatek już po pierwszym błędzie logowania. Szczegóły: [GDANSK.pl.md](https://github.com/htomasz/vultron/blob/main/GDANSK.pl.md).
- **Zawieszona przeglądarka** albo **trzy razy z rzędu przekroczony czas synchronizacji wiadomości**: proces kończy się twardo. Dodatek wstanie sam tylko z włączonym przełącznikiem **Watchdog** (zakładka Informacje).

Chwilowe błędy portalu (przerwa techniczna, błąd 502) nie zatrzymują dodatku: kolejna próba nastąpi w następnym cyklu.

## Linki

- [README](https://github.com/htomasz/vultron#readme)
- [Changelog](https://github.com/htomasz/vultron/blob/main/vultron/CHANGELOG.md)
- [GPE Gdańsk (PL)](https://github.com/htomasz/vultron/blob/main/GDANSK.pl.md) / [(EN)](https://github.com/htomasz/vultron/blob/main/GDANSK.md)
- [Instalacja bez Supervisora (Docker)](https://github.com/htomasz/vultron/blob/main/DOCKER.md)
- [Zgłoś błąd](https://github.com/htomasz/vultron/issues)
