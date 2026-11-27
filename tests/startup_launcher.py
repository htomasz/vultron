"""Uruchamia vultron.py jak `python vultron.py`, ale bez przeglądarki (tests/test_startup.py).

Test uruchamia go w osobnej przestrzeni sieci (tylko pętla zwrotna z atrapą HA),
więc vultron.py i tak nie ma dostępu do internetu. Tu blokujemy jeszcze start
Chromium: zamiast niego błąd jak przy braku przeglądarki, bez czekania na timeout.
Kod aplikacji jest wykonywany bez zmian (runpy).
"""
import os
import runpy
import sys

from selenium import webdriver


class _NoBrowser:
    def __init__(self, *args, **kwargs):
        print("[TEST] zablokowano uruchomienie przeglądarki", file=sys.stderr, flush=True)
        raise RuntimeError("test: brak przeglądarki")


webdriver.Chrome = _NoBrowser
# Jak `python vultron.py`: katalog skryptu na początku sys.path (gdansk, hebe, hebe_web).
sys.path.insert(0, os.getcwd())
sys.argv = ["vultron.py"]
runpy.run_path("vultron.py", run_name="__main__")
