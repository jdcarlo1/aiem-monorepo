import { Router } from "express";
import Anthropic from "@anthropic-ai/sdk";

const router = Router();

router.post("/analyze", async (req, res) => {
  const {
    ticker, rsi, macd, volume_ratio, price, change_pct,
    score, rating, sector, sma50, sma200,
  } = req.body as Record<string, any>;

  if (!ticker) {
    res.status(400).json({ error: "ticker is required" });
    return;
  }

  const f = (v: any, d = 2): string => {
    const n = parseFloat(v);
    return isNaN(n) ? "N/A" : n.toFixed(d);
  };

  const prompt = `You are a professional swing trader. Provide a concise, actionable swing trade analysis for ${String(ticker).toUpperCase()}.

Data:
- Sector: ${sector || "N/A"}
- Price: $${f(price)} (${parseFloat(change_pct) >= 0 ? "+" : ""}${f(change_pct, 2)}% today)
- RSI (14): ${f(rsi, 1)}${rsi && parseFloat(rsi) > 70 ? " [OVERBOUGHT]" : rsi && parseFloat(rsi) < 30 ? " [OVERSOLD]" : ""}
- MACD: ${f(macd, 3)}${macd && parseFloat(macd) > 0 ? " [BULLISH]" : " [BEARISH]"}
- Volume Ratio: ${f(volume_ratio, 1)}x${volume_ratio && parseFloat(volume_ratio) >= 1.5 ? " [ELEVATED]" : ""}
- SMA 50: $${f(sma50)} | SMA 200: $${f(sma200)}
- Composite Score: ${f(score, 1)}/10 — ${rating || "Neutral"}

Write 3–4 sentences covering: (1) technical setup & momentum, (2) risk/reward, (3) swing trade thesis. Be direct and data-driven. Under 90 words.`;

  try {
    const client = new Anthropic({
      baseURL: process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL,
      apiKey: process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY ?? "placeholder",
    });

    const message = await client.messages.create({
      model: "claude-haiku-4-5",
      max_tokens: 8192,
      messages: [{ role: "user", content: prompt }],
    });

    const text =
      message.content[0]?.type === "text"
        ? message.content[0].text
        : "Analysis unavailable.";

    res.json({ analysis: text, ticker: String(ticker).toUpperCase() });
  } catch (err: any) {
    res.status(500).json({ error: err?.message ?? "AI analysis failed" });
  }
});

export default router;
