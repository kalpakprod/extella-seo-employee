#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка манифеста зависимостей продукта (№29) — канон, копируется в продукты.

ЗАЧЕМ. Зависимость, не названная заранее, проверяется в момент ПАДЕНИЯ у коллеги:
«Ollama недоступен», «нет модуля docx», «порт занят» — и дальше день слепой
переписки. Названная в MANIFEST.yaml проверяется установщиком ДО первого
использования и говорит человеку, что именно сделать.

ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ. Продукты раздаются самостоятельными архивами, общей
библиотеки у нас нет и быть не может — модуль живёт копией в каждом продукте.
Копии сверяет гейт `check_manifest_copies.py`: расхождение = один продукт
останавливает установку там, где другой её тихо продолжает.

Формат манифеста (обычный YAML, но читается своим разбором — PyYAML на машине
коллеги может не быть):

    checks:
      - kind: python
        min_version: "3.10"
        fix_ru: "поставь Python 3.10+"
      - kind: module
        name: "docx"
        level: warn            # без него не работает только выгрузка в Word
        fix_ru: "pip3 install python-docx"

Виды проверок: python (версия), module (импортируется ли), file / dir (есть ли),
port (СВОБОДЕН ли), command (есть ли в PATH). `level: warn` — предупредить и
продолжить; по умолчанию отказ останавливает установку.

Запуск как диагностика: python3 manifest_check.py [путь-к-MANIFEST.yaml]
Коды выхода: 0 — всё, что обязательно, на месте; 1 — нет.
"""
import os
import shutil
import socket
import sys


def parse(text):
    """Разбор манифеста без PyYAML: список записей вида {'kind': ..., ...}."""
    records = []
    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip() if not _quoted(raw) else raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.endswith(":") and ":" not in stripped[:-1]:
            continue
        if stripped.startswith("- "):
            records.append({})
            stripped = stripped[2:].strip()
        if not records or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        records[-1][key.strip()] = _clean(value)
    return [r for r in records if r.get("kind")]


def _quoted(raw):
    """Есть ли в строке значение в кавычках — тогда '#' внутри не комментарий."""
    body = raw.split(":", 1)[-1].strip()
    return body.startswith('"') or body.startswith("'")


def _clean(value):
    value = value.strip()
    if value[:1] in ('"', "'") and value[-1:] == value[:1] and len(value) > 1:
        return value[1:-1]
    return value.split("#")[0].strip()


def _check_one(rec):
    """(хорошо ли, что проверяли) — по одной записи манифеста."""
    kind = rec.get("kind")
    if kind == "python":
        need = tuple(int(x) for x in str(rec.get("min_version", "3.10")).split("."))
        return sys.version_info[: len(need)] >= need, "Python %s+" % rec.get("min_version")
    if kind == "module":
        name = rec.get("name", "")
        try:
            import importlib.util
            good = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            good = False
        return good, "модуль %s" % name
    if kind in ("file", "dir"):
        path = os.path.expanduser(rec.get("path", ""))
        good = os.path.isdir(path) if kind == "dir" else os.path.exists(path)
        return good, "%s %s" % ("каталог" if kind == "dir" else "файл", rec.get("path"))
    if kind == "port":
        probe = socket.socket()
        probe.settimeout(0.4)
        try:
            # Свободен = хорошо: продукт поднимет на нём свой сервер.
            good = probe.connect_ex(("127.0.0.1", int(rec.get("port", 0)))) != 0
        finally:
            probe.close()
        return good, "порт %s свободен" % rec.get("port")
    if kind == "command":
        name = rec.get("name", "")
        return shutil.which(name) is not None, "команда %s" % name
    return None, "неизвестная проверка %s" % kind


def run(manifest_path, printer=print):
    """Проверить манифест. Возвращает True, только если всё обязательное на месте."""
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        # Молчать нельзя: манифеста нет — значит проверять нечего, и об этом
        # обязан узнать человек, а не только лог.
        printer("  FAIL: манифест не прочитан — %s" % exc)
        return False
    ok = True
    for rec in parse(text):
        good, what = _check_one(rec)
        if good is None:
            printer("  ~ %s — пропускаю" % what)
            continue
        warn_only = rec.get("level") == "warn"
        if good:
            printer("  ok %s" % what)
            continue
        printer("  %s %s — %s" % ("~" if warn_only else "FAIL", what, rec.get("fix_ru", "")))
        if not warn_only:
            ok = False
    return ok


def main(argv):
    path = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "MANIFEST.yaml")
    print("== Манифест зависимостей ==")
    return 0 if run(path) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
