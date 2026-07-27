import { Router } from "express";
import Anthropic from "@anthropic-ai/sdk";

const router = Router();
const client = new Anthropic({
  baseURL: process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL,
  apiKey: process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY,
});

router.post("/catalyst", async (req, res) => {
  const { ticker, call_put_ratio, premium_m, price, vol_ratio, score, expiry } = req.body || {};

  if (!ticker) { res.status(400).json({ error: "ticker required" }); return; }

  const hasOptions = call_put_ratio != null && call_put_ratio > 0;
  const hasVolume  = vol_ratio != null && vol_ratio > 0;

  const conviction =
    !hasOptions ? "no options data — analyzing technicals only" :
    call_put_ratio >= 5 ? "extremely unusual — top 1% of all options activity" :
    call_put_ratio >= 3 ? "very unusual — strong institutional conviction" :
    "unusual — above-normal call buying";

  let dataBlock = `• Current Price: $${price != null ? Number(price).toFixed(2) : "N/A"}`;
  if (hasVolume) dataBlock += `\n• Volume vs Avg: ${Number(vol_ratio).toFixed(1)}× normal`;
  if (score != null) dataBlock += `\n• Scanner Score: ${Number(score).toFixed(1)}/10`;
  if (hasOptions) {
    dataBlock += `\n• Call/Put Ratio: ${Number(call_put_ratio).toFixed(2)}× (${conviction})`;
    if (premium_m != null) dataBlock += `\n• Call Premium: $${Number(premium_m).toFixed(1)}M`;
    if (expiry) dataBlock += `\n• Target Expiry: ${expiry}`;
  }

  const prompt = hasOptions
    ? `You are a professional equity analyst specializing in options flow and market microstructure.

UNUSUAL FLOW DETECTED — ${ticker}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
${dataBlock}

Analyze in 3 short paragraphs:

1. CATALYST THESIS: What specific catalysts most likely explain this positioning? Consider earnings, M&A, FDA/regulatory events, analyst upgrades, sector rotation, or short squeeze. Be specific — name real possibilities for ${ticker}.

2. WHAT THE FLOW TELLS US: What does the size and structure tell us about who is buying and what they expect? Hedgers, speculators, or informed money?

3. KEY LEVELS & RISK: What price level confirms or invalidates this thesis? What is the bear case?

Write like a Bloomberg analyst note — sharp, specific, no fluff. Under 200 words total.`
    : `You are a professional equity analyst. A trader is asking why ${ticker} is moving or showing unusual activity.

TECHNICAL SNAPSHOT — ${ticker}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
${dataBlock}

Analyze in 3 short paragraphs:

1. CATALYST THESIS: What are the most likely reasons ${ticker} is showing unusual activity right now? Consider sector trends, earnings cycle, macro environment, and recent news you know about this company.

2. TECHNICAL CONTEXT: What does the volume and price action suggest about the nature of this move — breakout, squeeze, distribution, or accumulation?

3. WHAT TO WATCH: What catalyst or price level in the next 5–10 trading days would confirm or invalidate a bullish thesis?

Write like a Bloomberg analyst note — sharp, specific, no fluff. Under 200 words total.`;

  try {
    const message = await client.messages.create({
      model: "claude-opus-4-5",
      max_tokens: 450,
      messages: [{ role: "user", content: prompt }],
    });

    const content = message.content[0];
    const explanation = content.type === "text" ? content.text : "";
    res.json({ explanation, ticker });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
