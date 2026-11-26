"""Zamrożenie ścieżki GPE Gdańsk na czas refaktoru (plan: claude/refaktor-plan.md, faza 0).

Tryb provider: gdansk wykonuje kod modułu vultron.py przy imporcie, pierwsze `if`
w main_loop (rozgałęzienie GPE), gdansk_main_loop, cały gdansk.py i przechodnio
funkcje, do których te miejsca się odwołują. Ten kod nie może się zmienić ani
o bajt bez zgody autora.

Test działa na AST (bez importu vultron.py, bez /data):
- granica GPE liczona z grafu odwołań; nowa funkcja w strefie GPE = błąd,
- hash źródła każdej zamrożonej funkcji, rozgałęzienia GPE, każdej instrukcji
  na poziomie modułu (poza importami edu_*) i całego gdansk.py,
- żaden moduł obok vultron.py nie importuje vultron (podwójne wykonanie
  konfiguracji, logów, chmod).

Graf jest zachowawczy: odwołanie przez atrybut (np. logger.trace) też łączy
z funkcją o tej nazwie, więc strefa może być nieco większa niż w rzeczywistości.

Wzorzec (tests/gpe_frozen.txt) aktualizuje się świadomie, tylko za zgodą autora:
    python tests/test_gpe_frozen.py --update

Format wzorca: "<sha256> <rodzaj> [nazwa]", skrót zawsze na początku linii.
Skrót po nazwie typu _read_secret / import secrets skanery sekretów (gitleaks)
biorą za klucz API, więc nazwa nigdy nie poprzedza skrótu.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VULTRON = ROOT / "vultron" / "vultron.py"
GDANSK = ROOT / "vultron" / "gdansk.py"
PATTERN = Path(__file__).resolve().parent / "gpe_frozen.txt"

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _names(node: ast.AST) -> set[str]:
    out = set()
    for x in ast.walk(node):
        if isinstance(x, ast.Name):
            out.add(x.id)
        elif isinstance(x, ast.Attribute):
            out.add(x.attr)
    return out


def _source(lines: list[str], node: ast.AST) -> str:
    # Od pierwszego dekoratora do końca węzła, z komentarzami w środku.
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return "".join(lines[start - 1:node.end_lineno])


def _is_edu_import(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").startswith("edu_")
    if isinstance(node, ast.Import):
        return all(a.name.startswith("edu_") for a in node.names)
    return False


def snapshot() -> dict:
    text = VULTRON.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    defs = {n.name: n for n in tree.body if isinstance(n, _DEFS)}
    module_stmts = [n for n in tree.body if not isinstance(n, _DEFS) and not _is_edu_import(n)]

    gpe_branch = defs["main_loop"].body[0]
    roots = set().union(*(_names(n) for n in module_stmts)) | _names(gpe_branch) | {"gdansk_main_loop"}
    frozen: set[str] = set()
    stack = [r for r in roots if r in defs]
    while stack:
        name = stack.pop()
        if name in frozen or name == "main_loop":
            continue
        frozen.add(name)
        stack.extend(x for x in _names(defs[name]) if x in defs and x not in frozen)

    return {
        "frozen": {name: _sha(_source(lines, defs[name])) for name in sorted(frozen)},
        # Etykieta (pierwsza linia instrukcji) tylko do komunikatu błędu, nie do wzorca.
        "module_level": [(lines[n.lineno - 1].strip()[:80], _sha(_source(lines, n))) for n in module_stmts],
        "main_loop_gpe_branch": _sha(_source(lines, gpe_branch)),
        "gdansk.py": _sha(GDANSK.read_text(encoding="utf-8")),
    }


def _dump(snap: dict) -> str:
    out = ["# Wzorzec zamrożenia GPE: python tests/test_gpe_frozen.py --update (tylko za zgodą autora)"]
    out += [f"{h} func {name}" for name, h in snap["frozen"].items()]
    out += [f"{h} module" for _, h in snap["module_level"]]
    out.append(f"{snap['main_loop_gpe_branch']} branch main_loop")
    out.append(f"{snap['gdansk.py']} file gdansk.py")
    return "\n".join(out) + "\n"


def _pattern() -> dict:
    pat: dict = {"frozen": {}, "module_level": []}
    for line in PATTERN.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        h, kind, *name = line.split(" ", 2)
        if kind == "func":
            pat["frozen"][name[0]] = h
        elif kind == "module":
            pat["module_level"].append(h)
        elif kind == "branch":
            pat["main_loop_gpe_branch"] = h
        elif kind == "file":
            pat[name[0]] = h
    return pat


def test_gpe_zone_has_no_new_functions():
    now, pattern = snapshot(), _pattern()
    added = sorted(set(now["frozen"]) - set(pattern["frozen"]))
    removed = sorted(set(pattern["frozen"]) - set(now["frozen"]))
    assert not added, f"Nowe funkcje na ścieżce GPE (wymaga zgody autora): {added}"
    assert not removed, f"Funkcje zniknęły ze ścieżki GPE (wymaga zgody autora): {removed}"


def test_gpe_frozen_functions_unchanged():
    now, pattern = snapshot(), _pattern()
    changed = sorted(n for n, h in pattern["frozen"].items() if now["frozen"].get(n, h) != h)
    assert not changed, f"Zmienione funkcje na ścieżce GPE (wymaga zgody autora): {changed}"


def test_gpe_main_loop_branch_unchanged():
    assert snapshot()["main_loop_gpe_branch"] == _pattern()["main_loop_gpe_branch"], \
        "Zmienione rozgałęzienie GPE na początku main_loop (wymaga zgody autora)"


def test_gpe_module_level_code_unchanged():
    now = snapshot()["module_level"]
    pattern = _pattern()["module_level"]
    if [h for _, h in now] != pattern:
        changed = [label for label, h in now if h not in pattern]
        gone = len([h for h in pattern if h not in {x for _, x in now}])
        raise AssertionError("Zmieniony kod modułu vultron.py, wykonywany też w trybie GPE "
                             f"(wymaga zgody autora). Nowe/zmienione: {changed}; "
                             f"usuniętych/zmienionych we wzorcu: {gone}")


def test_gdansk_py_unchanged():
    assert snapshot()["gdansk.py"] == _pattern()["gdansk.py"], "Zmieniony gdansk.py (wymaga zgody autora)"


def test_no_module_imports_vultron():
    # vultron.py działa jako __main__; import pod nazwą vultron wykonałby drugi raz
    # cały kod modułu (konfiguracja, handlery logu, chmod /data).
    offenders = []
    for path in sorted((ROOT / "vultron").glob("*.py")):
        if path == VULTRON:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "vultron" for a in node.names):
                offenders.append(path.name)
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "vultron":
                offenders.append(path.name)
    assert not offenders, f"Moduły importujące vultron: {sorted(set(offenders))}"


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        sys.exit("Użycie: python tests/test_gpe_frozen.py --update  (tylko za zgodą autora)")
    PATTERN.write_text(_dump(snapshot()), encoding="utf-8")
    print(f"Zapisano wzorzec: {PATTERN}")
