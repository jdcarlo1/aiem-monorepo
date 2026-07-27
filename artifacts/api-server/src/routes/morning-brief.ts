import { Router } from "express";
import Anthropic from "@anthropic-ai/sdk";

const router = Router();
const client = new Anthropic({
  baseURL: process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL,
  apiKey: process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY,
});

let _cache: { date: string; brief: string; tickers: string[]; generated_at: string } = {
  date: "",
  brief: "",
  tickers: [],
  generated_at: "",
};

router.get("/morning-brief", async (req, res) => {
  const today = new Date().toISOString().slice(0, 10);

  if (_cache.date === today && _cache.brief) {
    res.json({ ..._cache, cached: true });
    return;
  }

  try {
    const flaskBase = process.env.FLASK_BASE_URL || "http://localhost:5050";
    const flowResp = await fetch(`${flaskBase}/stock-api/bull-flow/top10`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: [] }),
    });
    const flowData = await flowResp.json() as any;
    const topFlow: any[] = (flowData.results || [])
      .filter((r: any) => r.call_put_ratio >= 2)
      .slice(0, 5);

    if (!topFlow.length) {
      res.json({
        brief: "Pre-market data is loading. Check back after market open for today's top setups.",
        date: today,
        tickers: [],
        generated_at: new Date().toISOString(),
        cached: false,
      });
      return;
    }

    const flowLines = topFlow
      .map(
        (r: any, i: number) =>
          `${i + 1}. ${r.ticker} — $${r.premium_m?.toFixed(1)}M call premium, ${r.call_put_ratio?.toFixed(1)}× C/P ratio, price $${r.price?.toFixed(2)}, expiry ${r.expiry ?? "near-term"}`
      )
      .join("\n");

    const dateStr = new Date().toLocaleDateString("en-US", {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
      timeZone: "America/New_York",
    });

    const prompt = `You are a veteran Wall Street analyst writing the morning flow brief for a premium trading desk. Your readers are experienced active traders who want sharp, actionable intelligence — not generic advice.

${dateStr} — Today's Unusual Options Flow:
${flowLines}

Write a morning brief in 3 parts (no headers, no bullet points, flowing paragraphs):

First paragraph: Set the macro tone in 1-2 sentences — what does today's options flow collectively signal about market sentiment?

Second paragraph: Deep-dive on the top 1-2 names. What catalyst is most likely driving each? What does the size of the bet imply about the conviction of the buyer? What price target does the options positioning imply?

Third paragraph: What is the single most important trade setup from today's flow, and what level should traders watch? Close with a one-line market gut-check.

Style: Write like a Bloomberg Intelligence note crossed with a hedge fund morning call. Sharp, specific, professional. No platitudes. Under 220 words.`;

    const message = await client.messages.create({
      model: "claude-opus-4-5",
      max_tokens: 550,
      messages: [{ role: "user", content: prompt }],
    });

    const content = message.content[0];
    const brief = content.type === "text" ? content.text : "";
    const tickers = topFlow.map((r: any) => r.ticker);
    const generated_at = new Date().toISOString();

    _cache = { date: today, brief, tickers, generated_at };
    res.json({ ..._cache, cached: false });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

router.post("/morning-brief/refresh", async (_req, res) => {
  _cache = { date: "", brief: "", tickers: [], generated_at: "" };
  res.json({ ok: true, message: "Cache cleared — next GET will regenerate" });
});

export default router;
