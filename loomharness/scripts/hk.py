"""Sibling-module loader with bytecode cache (replaces runpy.run_path chains).

runpy.run_path recompiles each source file on every call. importlib loads
the same file via the bytecode cache.
Return a dictionary of globals like runpy so callers can keep the existing
module["name"] interface.
"""

import importlib.util
import os
import sys
from pathlib import Path


_CACHE = {}


def kit_home(base=None):
    """Installation data directory under $CODEX_HOME/loomharness.

    Continue using the legacy harness-kit directory when it is the only one.
    This preserves project preferences and team boards after a rename.
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
