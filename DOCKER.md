# Vultron w Dockerze — przewodnik "krok po kroku dla każdego"

[← Powrót do README](README.md)

Ten przewodnik zakłada, że nic nie wiesz o Dockerze tak jak JA. Każdy krok jest opisany osobno. Nie przechodź do kolejnego kroku, dopóki poprzedni nie zadziała.

**Dla kogo jest ten przewodnik?** Dla osób, które mają Home Assistant uruchomiony jako zwykły kontener Docker (bez tzw. "Supervisora" — jeśli nie wiesz co to jest, prawdopodobnie ten przewodnik jest dla Ciebie).

---

## Zanim zaczniesz — co będzie Ci potrzebne

- Komputer/serwer z zainstalowanym Dockerem, na którym już działa Twój
  Home Assistant.
- Login i hasło do dziennika eduVULCAN.
- Konto w Home Assistant, do którego możesz się zalogować przez
  przeglądarkę.
- 15-20 minut czasu.

---

## Krok 1: Zdobądź "przepustkę" dla Vultrona do Home Assistant

Vultron musi umieć "rozmawiać" z Twoim Home Assistant. Żeby to zrobić
bezpiecznie, potrzebuje specjalnego kodu — nazywa się **token**. To jak
klucz do drzwi, tylko cyfrowy.

1. Otwórz Home Assistant w przeglądarce.
2. Kliknij swoje imię/nazwę użytkownika w lewym dolnym rogu ekranu.
3. Zobaczysz stronę "Profil". Przewiń ją w dół, aż zobaczysz napis
   **"Bezpieczeństwo"**.
4. Znajdź tam **"Długoterminowe tokeny dostępu"**.
5. Kliknij przycisk **"Utwórz token"**.
6. Pojawi się długi ciąg liter i cyfr. **To jest Twój token.**
7. Kliknij, żeby go skopiować.
8. **Bardzo ważne:** ten token pokazuje się tylko RAZ. Jeśli go zamkniesz
   bez skopiowania, będziesz musiał zrobić nowy. Wklej go od razu w jakimś
   bezpiecznym miejscu (np. notatnik na komputerze) — będzie potrzebny za
   chwilę.
9. **Uwaga:** taki token daje pełny dostęp do Twojego Home Assistanta (tak jak
   Twoje konto). Nie wysyłaj go nikomu, a gdy przestaniesz używać Vultrona —
   usuń go w tym samym miejscu, w którym go utworzyłeś. Po kroku 6 warto też
   ograniczyć dostęp do plików z tokenem i hasłem tylko dla siebie:
   `chmod 600 secrets/ha_token.txt data/options.json`.

---

## Krok 2: Otwórz terminal (czarne okienko z tekstem)

Wszystkie kolejne kroki wykonujesz w terminalu na komputerze/serwerze, na
którym działa Twój Home Assistant. Jeśli już wiesz jak to otworzyć —
otwórz. Jeśli nie — poszukaj w systemie aplikacji o nazwie "Terminal" albo
połącz się przez SSH, jeśli Twój serwer jest zdalny.

---

## Krok 3: Ściągnij pliki Vultrona na dysk

Wpisz w terminalu (i wciśnij Enter):

```bash
git clone https://github.com/htomasz/vultron.git
```

To pobierze wszystkie potrzebne pliki do nowego folderu o nazwie `vultron`.

Teraz wejdź do tego folderu:

```bash
cd vultron
```

---

## Krok 4: Przygotuj dwa foldery na Twoje dane

Wpisz:

```bash
mkdir -p secrets data
```

To tworzy dwa puste foldery: `secrets` (na Twój token) i `data` (na dane
logowania do eduVULCAN).

---

## Krok 5: Zapisz token w pliku

Wpisz:

```bash
nano secrets/ha_token.txt
```

Otworzy się prosty edytor tekstu. Wklej tam token, który skopiowałeś w
Kroku 1 (samo kliknięcie prawym przyciskiem myszy → "Wklej", albo
`Ctrl+Shift+V` w większości terminali).

Nie wpisuj nic więcej — sam token, nic ponad to.

Zapisz plik: wciśnij `Ctrl+O`, potem `Enter`, potem `Ctrl+X` żeby wyjść z
edytora.

---

## Krok 6: Zapisz dane do logowania do eduVULCAN

Wpisz:

```bash
nano data/options.json
```

Wklej dokładnie to (i podmień dwa miejsca — swój login i swoje hasło):

```json
{"provider": "eduvulcan", "username": "TUTAJ_TWOJ_LOGIN", "password": "TUTAJ_TWOJE_HASLO", "log_level": "info", "test_mode": false}
```

Przykład jak to powinno wyglądać po wypełnieniu:
```json
{"provider": "eduvulcan", "username": "rodzic@email.pl", "password": "MojeHaslo123", "log_level": "info", "test_mode": false}
```

Zapisz: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Krok 7: Przygotuj miejsce na "karty" (ładny wygląd danych w Home Assistant)

Vultron pokazuje dane (oceny, plan lekcji itd.) w ładnej formie na
dashboardzie Home Assistant. Żeby to zadziałało, potrzebny jest jeden
dodatkowy folder — wewnątrz miejsca, gdzie Home Assistant trzyma swoje
pliki.

**7a. Znajdź, gdzie na dysku są pliki Twojego Home Assistant:**

```bash
docker inspect homeassistant --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

Zobaczysz linijkę podobną do tej:
```text
/home/user/ha-config -> /config
```

Zapisz sobie tę część **przed strzałką** (u Ciebie może być inna niż w tym
przykładzie) — będzie potrzebna za chwilę.

**7b. Stwórz w tym miejscu folder `www`:**

```bash
mkdir -p /home/user/ha-config/www
```

(Podmień `/home/user/ha-config` na to, co zobaczyłeś w kroku 7a).

Nic więcej nie musisz robić z Home Assistantem — sam zacznie widzieć ten
folder.

---

## Krok 8: Sprawdź jedną rzecz w ustawieniach Home Assistant

Vultron sam dodaje swoje karty do ustawień Home Assistant, ale tylko wtedy,
gdy token z Kroku 1 należy do konta **administratora**.

1. Wejdź w Home Assistant do **Ustawienia → Osoby → Twoje konto**.
2. Sprawdź, czy jest włączona opcja **Administrator**. Jeśli tak — wszystko
   gra, idź dalej.
3. Jeśli nie — token trzeba utworzyć na koncie administratora (Krok 1).

---

## Krok 9: Uzupełnij plik z instrukcjami dla Dockera

Wpisz:

```bash
nano compose.yaml
```

Plik `compose.yaml` jest już w pobranym folderze. Powinien wyglądać tak (jeszcze nic nie uruchamiamy, tylko zapisujemy
instrukcje):

```yaml
services:
  vultron:
    # Gotowy obraz z GitHuba (amd64 i aarch64, także Raspberry Pi).
    image: ghcr.io/htomasz/vultron:latest
    # Wolisz budować obraz sam? Zakomentuj "image:" wyżej, odkomentuj poniżej.
    # build:
    #   context: ./vultron
    container_name: vultron
    restart: unless-stopped
    mem_limit: 2g

    networks:
      - ha-net

    environment:
      TZ: Europe/Warsaw
      HA_URL: http://homeassistant:8123/api
      HA_WS_URL: ws://homeassistant:8123/api/websocket

    volumes:
      - ./data:/data:z
      - /home/user/ha-config/www:/config/www:z
      - ./secrets/ha_token.txt:/run/secrets/ha_token:ro,z

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  ha-net:
    external: true
```

**Ważne podmiany, których musisz dokonać w tym tekście, zanim zapiszesz:**

- `/home/user/ha-config/www` → wstaw swoją prawdziwą ścieżkę z Kroku 7a
  (z dopisanym `/www` na końcu).
- `Europe/Warsaw` → jeśli mieszkasz w Polsce, zostaw jak jest. Jeśli w
  innym kraju, zamień na swoją strefę czasową.

Zapisz: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## Krok 10: Podłącz Home Assistant do specjalnej "sieci" dla Vultrona

Żeby Vultron i Home Assistant mogły się ze sobą "widzieć", muszą być w tej
samej wirtualnej sieci. Wpisz po kolei:

```bash
docker network create ha-net
docker network connect ha-net homeassistant
```

(Jeśli druga komenda pokaże błąd, że sieć już istnieje/jest podłączona —
to znaczy że ktoś to już zrobił wcześniej, w porządku, idź dalej).

---

## Krok 11: Uruchom Vultrona

To jest ten moment. Wpisz:

```bash
docker compose up -d
```

Za pierwszym razem Docker pobiera gotowy obraz Vultrona (spory, bo zawiera przeglądarkę) —
chwilę to potrwa. Nie przerywaj, poczekaj aż komenda się zakończy i wróci
do zwykłego wiersza poleceń.

(Jeśli w Kroku 9 wybrałeś budowanie obrazu samemu, wpisz
`docker compose up --build -d` — to potrwa dłużej, kilka minut.)

**Aktualizacja do nowej wersji** (później, kiedy wyjdzie nowe wydanie):

```bash
docker compose pull
docker compose up -d
```

---

## Krok 12: Sprawdź, czy działa

Wpisz:

```bash
docker compose logs -f
```

Zobaczysz strumień tekstu — to jest "dziennik" tego, co Vultron robi na
żywo. Poczekaj chwilę i patrz, czy nie pojawia się czerwony napis
`CRITICAL` (to oznaczałoby błąd). Jeśli widzisz, że próbuje się logować do
eduVULCAN i nie ma błędów — świetnie, działa!

Żeby przestać patrzeć na ten strumień (nie wyłącza to Vultrona, tylko
podgląd): wciśnij `Ctrl+C`.

---

## Krok 13: Zobacz swoje dane w Home Assistant

1. W Home Assistant wejdź do **Narzędzia deweloperskie → Stany**.
2. W polu wyszukiwania wpisz `vultron`.
3. Jeśli po kilku minutach zobaczysz tam pozycje takie jak
   `sensor.vultron_plan_...` czy `sensor.vultron_oceny_...` — to znaczy, że
   dane faktycznie przyszły z eduVULCAN do Home Assistant. Gratulacje, udało
   się!

---

## Coś nie działa? Sprawdź to

### Widzę czerwony napis "Permission denied" (odmowa dostępu)

To zdarza się na niektórych systemach (Fedora, CentOS, RHEL, Rocky,
AlmaLinux). Sprawdź:
```bash
getenforce
```
Jeśli pokaże `Enforcing` — to jest przyczyna. Upewnij się, że **każda**
linijka w `volumes:` w pliku `compose.yaml` (Krok 9) kończy się literką
`:z` na samym końcu — dokładnie tak, jak w przykładzie wyżej.

### Karty w Home Assistant są puste albo nic się nie pokazuje

1. Wciśnij `Ctrl+Shift+R` w przeglądarce (to "twarde" odświeżenie strony).
2. W Home Assistant wejdź do **Ustawienia → Panel sterowania**, kliknij
   trzy kropki w prawym górnym rogu, wybierz **Zasoby**. Jeśli lista jest
   pusta — dodaj tam ręcznie wpisy, po jednym dla każdej karty Vultrona,
   którą chcesz widzieć (typ: **JavaScript Module**, adres:
   `/local/vultron/nazwa-karty.js`).

### Wpisałem złe hasło - co robi Vultron?

Po dwóch odrzuconych logowaniach Vultron przestaje próbować
(`sensor.vultron_status` ma stan `authentication_error`). Kontener dalej
działa, ale już się nie loguje, więc `restart: unless-stopped` nie powtarza
prób. Popraw hasło w `data/options.json`, zaloguj się ręcznie na
eduvulcan.pl, potem `docker compose restart`. Przy GPE Gdańsk działa to tak
samo, a stan pokazuje `sensor.vultron_gdansk_status`.

### Vultron ciągle się restartuje / wyłącza

Wpisz:
```bash
docker compose logs vultron --tail 50
```
Poszukaj **pierwszej** czerwonej linijki (`CRITICAL`) na samej górze
wyniku — ona zwykle mówi wprost, czego brakuje (np. że plik z tokenem jest
pusty, albo że coś jest źle wpisane w `data/options.json`).

### Nie wiem, jaki adres wpisać przy `HA_URL`

Sprawdź, jak jest ustawiony Twój Home Assistant:
```bash
docker inspect homeassistant --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```
Jeśli wynikiem jest słowo `host` — napisz o tym, to wymaga innej wersji
pliku `compose.yaml` niż ta z Kroku 9.

---

## To wszystko!

Jeśli dotarłeś do końca i widzisz swoje dane w Home Assistant — Vultron
działa poprawnie. Możesz teraz dodać ładne "karty" na swój dashboard,
żeby widzieć te dane w wygodnej formie (to już osobny, dodatkowy krok —
zapytaj, jeśli chcesz instrukcję).
