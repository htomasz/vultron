# Dostawca GPE Gdańsk (eksperymentalny)

[English version](GDANSK.md)

Konta z Gdańska logują się przez `https://uonetplus.edu.gdansk.pl/gdansk/`,
które używa innego procesu logowania i innego API niż eduVULCAN. Aby użyć tego
dostawcy, wybierz `gdansk` w opcji dodatku **Portal dziennika**. Domyślnie
nadal jest `eduvulcan`, także w instalacjach bez ustawionej opcji `provider`.

W opcjach dodatku wpisz login i hasło do GPE. Login konta widoczny w mailu
rejestracyjnym może się różnić od dodatkowego adresu e-mail. Opcjonalny filtr
`student_name` dopasowuje dokładne imię i nazwisko widoczne w dzienniku; pozostaw
go pusty, aby objąć wszystkie dzienniki szkolne z bieżącego roku. Dzienniki
przedszkolne nie są przez tego dostawcę obsługiwane.

Przeglądarka przeprowadza zwykłe logowanie do portalu i przekazuje sesję
klientowi HTTP tylko do odczytu. Dostawca nie rejestruje urządzenia mobilnego,
nie rozwiązuje zadań CAPTCHA i nie akceptuje zgód. Interaktywne kroki na koncie
trzeba najpierw wykonać w portalu. Nie są potrzebne żadne dodatkowe uprawnienia
dodatku.

## Dane i karty

Dostawca zwraca oceny ze wszystkich okresów klasyfikacyjnych, plan lekcji z
poprzedniego, bieżącego i następnego tygodnia, frekwencję z ostatnich 14 dni,
statystyki frekwencji, nadchodzące zadania z czterech tygodni, uwagi,
osiągnięcia, zebrania i nagłówki wiadomości. Zachowuje opisy w tych zakresach i
używa istniejących nazw sensorów oraz schematów kart Vultrona.

Oceny punktowe, takie jak `6p`, są zachowywane. Średnia jest dołączana tylko
wtedy, gdy GPE ją udostępnia. Stanem sensora ocen jest łączna liczba ocen, a nie
liczba nowo wykrytych ocen jak w eduVULCAN; przykłady powiadomień o nowych
ocenach trzeba dostosować do tej różnicy. Szczęśliwy numerek i statystyki
frekwencji według przedmiotów nie są zaimplementowane. Brakujące wartości
miesięczne, semestralne i roczne są w karcie statystyk wyświetlane jako myślnik.

Karta wiadomości łączy `wiadomosci` z sensora głównego z sensorami wymienionymi
w jego `page_entities`, dzięki czemu duże skrzynki mieszczą się w limicie
atrybutów HA. Opcja `limit` nadal obowiązuje po posortowaniu wszystkich stron.
Nagłówki prowadzą do skrzynki odbiorczej GPE w przeglądarce, gdzie można
wiadomości czytać i na nie odpowiadać. Dostawca nie pobiera treści wiadomości,
nie oznacza ich jako przeczytanych, nie wysyła wiadomości ani nie usprawiedliwia
nieobecności. Treści wiadomości eduVULCAN nadal otwierają się w lokalnym
podglądzie.

Automatyzacje czytające `wiadomosci` bezpośrednio widzą tylko pierwszą stronę.
Przy dużych skrzynkach dodaj `page_entities`, aby objąć wszystkie nagłówki.
Dodatkowe strony są publikowane przed sensorem głównym; sensor główny wymienia
tylko bieżące strony. Stanem sensora głównego jest liczba nieprzeczytanych, a
atrybut `total` oznacza rozmiar skrzynki. Skrzynki są przypisywane do uczniów
według imienia i nazwiska; niejednoznaczne dopasowania kończą się jawnym błędem,
zamiast przypisać wiadomości jednego ucznia drugiemu.

## Synchronizacja i diagnostyka

Pierwsza synchronizacja uruchamia się od razu, z wyjątkiem nocnej przerwy
01:00-06:00. Kolejne uruchomienia czekają 40-60 minut między pobraniami, także
w weekendy. Tryb testowy omija nocną przerwę. Nieudane logowanie lub wygaśnięcie
sesji API zatrzymuje tego dostawcę: w dodatku HA od razu, a w samodzielnym
kontenerze Docker po drugim takim błędzie z rzędu (pierwszy daje ponowną próbę
w następnym cyklu; potem kontener działa dalej bez logowania do
`docker compose restart`). Inne błędy dodają ograniczone opóźnienie ponowienia.
Każdy cykl otwiera krótkotrwałą sesję Chromium. Ponowne użycie sesji
i odbieranie powiadomień push ze szkoły nie są zaimplementowane.

`sensor.vultron_gdansk_status` zgłasza `ok`, `partial`, `error` lub
`authentication_error`. Sekcje, które się nie powiodły, zachowują wcześniej
opublikowane dane. Zbyt duże sekcje inne niż wiadomości są zgłaszane w sensorze
statusu, a nie po cichu przycinane; podział takich sekcji na strony to przyszła
praca. Dane logowania i ciasteczka sesji nie trafiają do diagnostyki. Tak jak w
przypadku istniejącego dostawcy, tryb `trace` (włączany opcjonalnie) może
zapisywać w logach dane odpowiedzi szkoły.

Nagłówki sesji otrzymują tylko zweryfikowane źródła HTTPS GPE. Przekierowania
API nie są śledzone, a API ucznia ma jawną listę dozwolonych punktów końcowych
tylko do odczytu. Wszystkie dołączone przykładowe odpowiedzi to publiczne dane
testowe Wulkanowy, z podaniem autorstwa i licencją Apache 2.0 w
`tests/fixtures/gdansk`.

## Weryfikacja

Zainstaluj `vultron/requirements.txt` i `pytest` w środowisku wirtualnym, a
następnie uruchom `python -m pytest -q tests`. Testy kart w przeglądarce używają
wbudowanego narzędzia Node: `node --test tests/test_messages_card.cjs`. Żaden z
zestawów nie wymaga konta szkolnego ani dostępu do sieci. Workflow testów GPE
uruchamia oba zestawy.

Dostawca został sprawdzony z GPE 26.06 na HAOS 18.3 / Home Assistant 2026.9.3,
przy użyciu osobno zainstalowanej lokalnej kompilacji. Testy na żywo objęły
oceny, trzy tygodnie planu lekcji, frekwencję, zadania, sekcje szkolne i dużą
skrzynkę wiadomości, ze statusem diagnostycznym `ok`. Dodatkowa obsługa
natywnej karty wiadomości jest pokryta odizolowanymi testami zachowania. Ten
wkład nie zawiera prawdziwych danych szkolnych ani danych logowania.

Podczas testowania lokalnej kompilacji zatrzymaj każdą inną instancję Vultrona
używającą tych samych nazw encji i ścieżek zasobów. Przed włączeniem startu
automatycznego porównaj dane z portalem i zachowaj poprzednią kompilację na
wypadek powrotu. Po przebudowaniu kart bez zmiany wersji dodatku zmień
parametr wersji w adresie zasobu w Ustawienia > Pulpity > Zasoby i przeładuj
HA, aby uniknąć starych plików z pamięci podręcznej.
