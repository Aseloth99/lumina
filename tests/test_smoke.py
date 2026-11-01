"""Duman testi: her modul import edilebilmeli ve tanimsiz global isim kalmamali.

Calistirma (proje kokunden):  python -m unittest discover -s tests -v
Qt ekran acmaz (offscreen). Eksik import / NameError tipi hatalari yakalamak icin vardir.
"""

from __future__ import annotations

import builtins
import importlib
import os
import pathlib
import symtable
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "app"  # "." = duz yerlesim (modul dogrudan kokte)
SKIP_DIRS = {"build", "dist", "__pycache__", "docs", "vendor", "tests", ".git", ".venv", "venv"}
SKIP_MODULES = {"main", "setup", "__main__"}  # import edilince uygulama baslatabilir

_BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__spec__", "__path__",
    "__qualname__", "__module__", "__class__",
}

sys.path.insert(0, str(ROOT))


def _source_files() -> list[pathlib.Path]:
    base = ROOT if PACKAGE == "." else ROOT / PACKAGE
    return [
        f for f in sorted(base.rglob("*.py"))
        if not any(part in SKIP_DIRS for part in f.relative_to(ROOT).parts)
    ]


def _module_name(path: pathlib.Path) -> str | None:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[-1] in SKIP_MODULES:
        return None
    return ".".join(parts)


def _undefined_names(path: pathlib.Path) -> list[str]:
    top = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    module_names = {
        s.get_name() for s in top.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace()
    }
    found: list[str] = []

    def walk(table: symtable.SymbolTable, scope: str) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced() or sym.is_assigned() or sym.is_imported():
                continue
            if sym.is_parameter() or sym.is_namespace():
                continue
            free_global = sym.is_global() or table.get_type() == "module"
            if free_global and name not in module_names and name not in _BUILTINS:
                found.append(f"{scope}{table.get_name()} satir {table.get_lineno()}: {name}")
        for child in table.get_children():
            walk(child, f"{scope}{table.get_name()}.")

    walk(top, "")
    return found


class SmokeTest(unittest.TestCase):
    def test_every_module_imports(self) -> None:
        failures = []
        for path in _source_files():
            name = _module_name(path)
            if name is None:
                continue
            try:
                importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001 - tum import hatalarini topla
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "Import edilemeyen moduller:\n" + "\n".join(failures))

    def test_no_undefined_global_names(self) -> None:
        problems = []
        for path in _source_files():
            for item in _undefined_names(path):
                problems.append(f"{path.relative_to(ROOT)} [{item}]")
        self.assertEqual(problems, [], "Tanimsiz isimler:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
