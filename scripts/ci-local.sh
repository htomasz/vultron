#!/usr/bin/env bash
# Kontrole jak w CI, lokalnie, jednym poleceniem (plan refaktoru, faza 0).
#
# Wymaga: aktywnego venv poza repo z
#   pip install --require-hashes -r vultron/requirements.txt \
#       -r .github/requirements/tests.txt -r .github/requirements/python-security.txt
# oraz node + npm. Opcjonalnie gitleaks (skan sekretów jak w CI).
#
# Testy vultron.py potrzebują zapisywalnego /data z options.json. Prawdziwego
# /data skrypt NIE rusza: testy dostają tymczasowy katalog podmontowany jako
# /data tylko dla siebie (unshare -rm, bez sudo). Po zakończeniu znika.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -n "${VIRTUAL_ENV:-}" && "$VIRTUAL_ENV" == "$PWD"/* ]]; then
    echo "venv jest w repo ($VIRTUAL_ENV) - eslint i bandit przeskanują jego pliki. Przenieś go poza repo." >&2
    exit 1
fi

for tool in python ruff bandit node npx; do
    command -v "$tool" >/dev/null || { echo "Brak narzędzia: $tool" >&2; exit 1; }
done
python -c "import pytest" 2>/dev/null || { echo "Brak pytest w aktywnym venv" >&2; exit 1; }

step() { printf '\n== %s\n' "$*"; }

step "ruff (bezpieczeństwo)"
ruff check . --select S --extend-ignore S301,S113
step "ruff (błędy, nieużywane importy)"
ruff check . --select F,E9
step "bandit"
bandit -r . -ll -ii --skip B301 -q -x ./node_modules

if command -v gitleaks >/dev/null; then
    # Jak workflow Secret Scanning: cała historia bieżącej gałęzi.
    step "gitleaks"
    gitleaks git --redact --no-banner .
else
    step "gitleaks: brak programu - pomijam (Fedora: dnf install gitleaks)"
fi

step "zależności npm"
npm ci --ignore-scripts --no-audit --no-fund >/dev/null
step "eslint"
npx eslint .
step "testy kart"
node --test tests/*.cjs

step "pytest (tymczasowe /data)"
# tests/test_startup.py montuje tymczasowe /config: potrzebny pusty punkt montowania.
[[ -d /config ]] || { echo "Brak /config - utwórz raz pusty katalog: sudo mkdir /config" >&2; exit 1; }
data_dir=$(mktemp -d)
trap 'rm -rf "$data_dir"' EXIT
printf '%s' '{"username":"test@example.com","password":"x","log_level":"info"}' > "$data_dir/options.json"
# Pominięte testy (brak /data) = błąd: inaczej zielony wynik niczego nie sprawdza.
unshare -rm sh -c "mount --bind '$data_dir' /data && python -m pytest -q tests -p no:cacheprovider -rs" \
    | tee "$data_dir/pytest.log"
if grep -q "skipped" "$data_dir/pytest.log"; then
    echo "Część testów pominięta - sprawdź /data" >&2
    exit 1
fi

step "OK: wszystkie kontrole zielone"
