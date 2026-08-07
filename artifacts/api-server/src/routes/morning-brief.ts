import { Router } from "express";

const router = Router();

/**
 * Proxy to Flask /stock-api/morning-brief — single source of truth.
 * Previously this route queried bull-flow/top10 with call_put_ratio>=2 and
 * returned a permanent "Pre-market data is loading" stub when empty.
 * Flask now reads unusual_calls_log + aiem_predictions + RVOL fallbacks.
 */
router.get("/morning-brief", async (_req, res) => {
  try {
    const flaskBase = process.env.FLASK_BASE_URL || "http://localhost:5050";
    const flowResp = await fetch(`${flaskBase}/stock-api/morning-brief`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    const data = await flowResp.json();
    res.status(flowResp.status).json(data);
  } catch (err: any) {
    res.status(500).json({
      error: err?.message || String(err),
      brief: "",
      date: "",
      tickers: [],
      generated_at: "",
      cached: false,
    });
  }
});

router.post("/morning-brief/refresh", async (_req, res) => {
  try {
    const flaskBase = process.env.FLASK_BASE_URL || "http://localhost:5050";
    const flowResp = await fetch(`${flaskBase}/stock-api/morning-brief/refresh`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const data = await flowResp.json();
    res.status(flowResp.status).json(data);
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err?.message || String(err) });
  }
});

export default router;
