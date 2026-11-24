"""Sibling-module loader with bytecode cache (replaces runpy.run_path chains).

runpy.run_path перекомпилирует модуль из исходника на каждый вызов и не
использует __pycache__. importlib загружает тот же файл с кэшем байткода.
Возвращается словарь глобальных имён — как у runpy, поэтому обращения
вида module["name"] в вызывающем коде сохраняются без изменений.
"""

import importlib.util
import os
import sys
from pathlib import Path


_CACHE = {}


def kit_home(base=None):
    """Каталог данных установки: $CODEX_HOME/loomharness (legacy: harness-kit).

    Пока существует только прежний каталог harness-kit, продолжаем использовать
    его — базы выбора проекта и доски команд должны пережить переименование.
    """
    root = Path(base) if base is not None else Path(
        os.environ.get("CODEX_HOME") or Path.home() / ".codex"
    )
    current = root / "loomharness"
    legacy = root / "harness-kit"
    if legacy.is_dir() and not current.is_dir():
        return legacy
    return current


def load(name):
    if name in _CACHE:
        return _CACHE[name]
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(f"hk_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sibling module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"hk_{name}"] = module
    spec.loader.exec_module(module)
    namespace = module.__dict__
    _CACHE[name] = namespace
    return namespace
