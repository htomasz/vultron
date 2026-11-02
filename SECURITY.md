# Polityka bezpieczeństwa 🔒

## Wspierane wersje

Poprawki bezpieczeństwa trafiają tylko do **najnowszej wersji**. Jeśli masz starszą, zaktualizuj dodatek,
zanim zgłosisz problem. Może już być naprawiony.

## Znalazłeś lukę?

1. **Nie zakładaj publicznego issue.** Luka opisana publicznie to instrukcja dla każdego.
2. Zgłoś ją prywatnie: zakładka **[Security → Report a vulnerability](https://github.com/htomasz/vultron/security/advisories/new)**.
   Zgłoszenie widzi tylko opiekun projektu.
3. Opisz, czego dotyczy, jak to odtworzyć i jaki może mieć skutek.
4. Postaram się odpowiedzieć w ciągu 48 godzin, a po naprawie opublikować advisory z podziękowaniem
   (chyba że wolisz zostać anonimowy).

Przykłady tego, co nas interesuje: wyciek haseł, cookies lub sesji, XSS w kartach Lovelace,
dostęp do danych innego użytkownika, wykonanie kodu w kontenerze.

## O Twoich danych

Vultron przechowuje dane logowania i sesję **lokalnie** w Twojej instancji Home Assistant.
Nikomu nie udostępniaj plików `vultron.db` (z towarzyszącymi `-wal` i `-shm`) ani `vul.pkl` (mimo rozszerzenia to plik JSON z zapisaną sesją), a w logach dołączanych do zgłoszeń
zamazuj hasła, cookies, tokeny i dane dzieci.

## Jak dbamy o bezpieczeństwo

- Dodatek HA buduje obraz u Ciebie, lokalnie, z `vultron/Dockerfile`. Obraz jest powtarzalny: baza
  Alpine przypięta do wersji i digestu, a pakiety Pythona do dokładnych wersji z hashami
  (`vultron/requirements.txt`, instalacja z `--require-hashes`). Pakiety systemowe Alpine (np. Chromium)
  mogą mieć nowsze poprawki z tej samej gałęzi Alpine.
- Samodzielny kontener (`compose.yaml`) domyślnie pobiera gotowy obraz `ghcr.io/htomasz/vultron`,
  zbudowany w GitHub Actions z tego repozytorium i skanowany przez Trivy. Obraz jest podpisany
  (Sigstore, build provenance):
  `gh attestation verify oci://ghcr.io/htomasz/vultron:X.Y.Z -R htomasz/vultron`
- Zip dołączony do każdego release też jest podpisany:
  `gh attestation verify vultron-X.Y.Z.zip -R htomasz/vultron`
- Kod i workflowy skanują automatycznie: CodeQL, Semgrep, Bandit, Trivy, Gitleaks, zizmor i OpenSSF Scorecard.
- Zależności (obraz bazowy, pakiety Pythona, akcje GitHub) aktualizuje Dependabot.
