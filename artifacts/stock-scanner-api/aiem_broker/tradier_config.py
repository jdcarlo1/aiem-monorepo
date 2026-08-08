"""Single source for Tradier API base URL (env-driven).

Default matches production brokerage API. Override with TRADIER_API_BASE.
Sandbox order host is separate: TRADIER_SANDBOX_BASE (see tradier_sandbox.py).
"""
from __future__ import annotations

import os

TRADIER_API_BASE = os.environ.get("TRADIER_API_BASE", "https://api.tradier.com").rstrip("/")
