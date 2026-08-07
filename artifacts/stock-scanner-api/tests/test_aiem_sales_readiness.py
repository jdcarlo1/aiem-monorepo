"""Structural tests for AIEM sales readiness aggregator."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_build_sales_readiness_without_db():
    import aiem_sales_readiness as asr

    # Force empty DB URL path branches
    old = os.environ.get("DATABASE_URL")
    try:
        os.environ.pop("DATABASE_URL", None)
        out = asr.build_sales_readiness(db_url="")
    finally:
        if old is not None:
            os.environ["DATABASE_URL"] = old

    assert out["ok"] is True
    assert out["sku"] == "AIEM_TERMINAL"
    assert "reliability" in out
    assert "honest_pnl" in out
    assert "live_path" in out
    assert out["live_path"]["can_place_live_orders"] is False
    assert out["live_path"]["mode"] == "PAPER_ONLY"
    assert out["commercial"]["sku_separation"]["oe_terminal"]
    assert any(d["name"] == "Due Diligence Pack" for d in out["commercial"]["docs"])
    assert out["overall_score"] >= 0


def test_roles_and_api_surface_present():
    import aiem_sales_readiness as asr

    roles = {r["role"] for r in asr.ROLES_MODEL}
    assert roles == {"Viewer", "Trader", "Auditor", "Admin"}
    paths = {a["path"] for a in asr.API_SURFACE}
    assert "/stock-api/aiem-sales-readiness" in paths
