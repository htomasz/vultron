# Jak pomóc przy Vultronie 🦅

Cześć! Skoro tu jesteś, to albo coś się zepsuło, albo masz pomysł, albo eduVULCAN znowu coś zmienił
i nie mógł się powstrzymać. W każdym przypadku: dzięki, że wpadłeś.

## Kto tu siedzi

| Kto | Czym się zajmuje |
|---|---|
| [@htomasz](https://github.com/htomasz) | Autor i opiekun. Dodatek, karty, wydania, łatanie po każdej „drobnej zmianie” w eduVULCAN. |
| [@KamillJot](https://github.com/KamillJot) | Dostawca GPE Gdańsk (`gdansk.py`) i karty wiadomości z nim zgodne. |
| Dependabot 🤖 | Co tydzień podbija wersje zależności. Nie pije kawy, nie narzeka. |

Twoje nazwisko może być następne.

## Zgłaszasz błąd?

Otwórz [issue](https://github.com/htomasz/vultron/issues) i dołącz:

- wersję Vultrona i gdzie działa (HAOS / osobny Docker, sprzęt),
- dziennik: **eduVULCAN** czy **GPE Gdańsk**,
- logi z *Add-on → Vultron → Logs*,
- co miało się stać, a co się stało.

Im więcej konkretów, tym szybciej naprawa. „Nie działa” to też opis, ale słaby.

## 🔒 Najpierw bezpieczeństwo, potem reszta

Vultron dotyka danych Twoich dzieci i Twojego konta. Zanim cokolwiek wkleisz publicznie:

- **Nigdy** nie wklejaj loginu, hasła, cookies, tokenów, nagłówków `Authorization`.
- **Nigdy** nie dołączaj plików `vultron.db`, `vul.pkl`, `bul.pkl` ani zawartości `/data`.
- Zamaż imiona, nazwiska, oceny, uwagi i treść wiadomości w logach i na zrzutach ekranu.
- Znalazłeś lukę bezpieczeństwa? **Nie** zakładaj issue. Zgłoś ją prywatnie, opis jest w [SECURITY.md](SECURITY.md).

## Chcesz coś poprawić w kodzie?

1. Zrób forka i gałąź, np. `fix/plan-lekcji` albo `feat/nowa-karta`.
2. Zmieniaj tylko to, czego dotyczy poprawka. Mniejszy PR to szybszy review.
3. Uruchom testy (te same co w CI):

   ```bash
   pip install -r vultron/requirements.txt pytest
   python -m pytest -q tests
   node --test tests/*.cjs
   ```

4. Otwórz Pull Request do `main` i opisz, **co** zmieniasz i **dlaczego**.
5. CI musi być zielone. Skanów jest sporo (CodeQL, Semgrep, Bandit, Trivy, Gitleaks, zizmor…),
   więc jeśli coś zaświeci na czerwono, to raczej nie złośliwość, tylko troska.

Wersje i wydania robi opiekun, więc w PR nie zmieniaj numeru wersji.

## Gdzie co leży

| Plik / katalog | Co to jest |
|---|---|
| `vultron/vultron.py` | Główny dodatek: logowanie Selenium, pobieranie z eduVULCAN, encje w HA. |
| `vultron/gdansk.py` | Dostawca GPE Gdańsk, osobna ścieżka logowania. |
| `vultron/vultron-*.js` | Karty Lovelace. |
| `vultron/Dockerfile` | Obraz na `alpine:latest`. Tak, celowo `latest`. |
| `tests/` | Testy Pythona i kart. |

## Zasady, których się trzymamy

- **Dane z API to niezaufany input.** W kartach wszystko, co przychodzi z dziennika, trafia na stronę
  przez `textContent` albo przez `_esc()` przed wstawieniem do HTML. Pilnują tego testy w `tests/*.cjs`.
- **Żadnych sekretów w kodzie.** Gitleaks i tak je znajdzie, a potem będzie niezręcznie.
- **Akcje w workflowach przypinamy do SHA**, nie do tagu (`uses: owner/action@<sha> # vX.Y.Z`).
- **Komentarze piszemy po polsku**, a kod jest prosty: lepiej 50 czytelnych linii niż 200 sprytnych.

## Na koniec

Każda pomoc się liczy: poprawiona literówka, lepszy opis w README, zgłoszony błąd z porządnymi logami.
Bądźmy dla siebie mili, szczegóły są w [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Dzięki! 🙌