"""Root stub — the live module lives under artifacts/stock-scanner-api/.

This file was previously empty (0 bytes), which is a landmine if anything
imports `aiem_security` with the repo root on sys.path. Re-export the live
module so accidental root imports still resolve to real code.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_LIVE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "artifacts",
    "stock-scanner-api",
    "aiem_security.py",
)

if not os.path.isfile(_LIVE):
    raise ImportError(
        "Root aiem_security.py stub: live module missing at "
        f"{_LIVE}. Import from artifacts/stock-scanner-api/aiem_security.py."
    )

_spec = importlib.util.spec_from_file_location("aiem_security_live", _LIVE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load live aiem_security from {_LIVE}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules[__name__] = _mod
