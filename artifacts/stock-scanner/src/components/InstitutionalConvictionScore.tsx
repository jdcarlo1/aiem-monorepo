import { useState } from "react";

const C = {
  bg:       "#0a0e1a",
  surface:  "#0f1629",
  card:     "#141c35",
  border:   "#1e2d50",
  accent:   "#00d4ff",
  green:    "#00ff88",
  yellow:   "#ffd700",
  red:      "#ff4444",
  muted:    "#4a5a7a",
  text:     "#e2e8f0",
  textDim:  "#8899bb",
};

const CRITERIA = [
  { id: "nakedCall",     label: "Naked Call (No Hedge)",               weight: 20, description: "Pure directional bet — no puts, no spreads, no collar",                  tooltip: "The single strongest signal. Institutions don't hedge when they have conviction." },
  { id: "askSide",       label: "Ask-Side Execution",                  weight: 15, description: "Buyer hitting the ask aggressively, not passive",                         tooltip: "Urgency = conviction. Passive bids wait; aggressive asks chase." },
  { id: "multiLegSweep", label: "Multi-Leg / Multi-Exchange Sweep",    weight: 15, description: "Same strike swept across multiple exchanges in seconds",                   tooltip: "Nearly impossible to fake. Strongly directional institutional activity." },
  { id: "premiumSize",   label: "Premium Size $500K+",                 weight: 12, description: "$500K+ single order / $1M+ = major statement",                            tooltip: "Size matters. Small premium = retail noise. Large premium = real money." },
  { id: "shortDatedOTM", label: "Short-Dated OTM Calls",               weight: 10, description: "Weekly or near-term out-of-the-money calls",                               tooltip: "Nobody buys weekly OTM calls as a hedge. Pure speculation with a timeline." },
  { id: "aboveVWAP",     label: "Activity Above VWAP",                 weight: 8,  description: "Sweep/block happening while price is above intraday VWAP",                 tooltip: "Bullish momentum confirmed. Smart money buying into strength." },
  { id: "heavyVolume",   label: "Heavy Relative Volume",               weight: 8,  description: "Options volume 3x+ above normal daily average",                            tooltip: "Unusual volume is the first flag. Confirms something is happening." },
  { id: "repeatActivity",label: "Repeat Sweeps (2–3 Days)",            weight: 6,  description: "Same ticker swept on consecutive days",                                     tooltip: "Institutional accumulation pattern. They're building a position over time." },
  { id: "redDayBuy",     label: "Buying on a Red Day",                 weight: 5,  description: "Stock is down but calls are being loaded",                                  tooltip: "Counter-trend accumulation. They're not scared of the dip — they caused it." },
  { id: "lowIVR",        label: "Low IV Rank (IVR < 30)",              weight: 5,  description: "Options are cheap — buyer expects a real move",                             tooltip: "Cheap options = higher leverage. Institutions love buying before IV expands." },
  { id: "oiSpike",       label: "Open Interest Spike Next Day",        weight: 4,  description: "OI jumps significantly after the sweep",                                    tooltip: "Confirms real new positioning, not just intraday speculation." },
  { id: "earlyMorning",  label: "Early Morning Timing (9:30–10am)",    weight: 3,  description: "Activity in the first 30 minutes of trading",                               tooltip: "Institutions front-run catalysts at open. Early prints = informed money." },
  { id: "quietTicker",   label: "Unusual Activity in Low-Volume Ticker",weight: 3, description: "Normally quiet stock suddenly sees massive flow",                           tooltip: "A small-cap or low-volume name getting hit hard is the loudest signal." },
  { id: "darkPool",      label: "Dark Pool Print Near Support",        weight: 3,  description: "Large dark pool buy near a key technical level",                            tooltip: "Smart money accumulating quietly before a move. Stealth accumulation." },
  { id: "preCatalyst",   label: "Pre-Catalyst (Earnings / FDA / Event)",weight: 3, description: "Activity happening before a known or unknown catalyst",                    tooltip: "They know something. Pre-earnings naked calls are the most informed bets." },
];

const TOTAL_WEIGHT = CRITERIA.reduce((s, c) => s + c.weight, 0);

function getScoreColor(score: number) {
  if (score >= 80) return C.green;
  if (score >= 55) return C.yellow;
  return C.red;
}

function getScoreLabel(score: number) {
  if (score >= 80) return { label: "EXTREME CONVICTION",  emoji: "🔥🔥🔥" };
  if (score >= 65) return { label: "HIGH CONVICTION",     emoji: "⭐⭐⭐" };
  if (score >= 45) return { label: "MODERATE SIGNAL",     emoji: "⭐⭐" };
  if (score >= 25) return { label: "WEAK SIGNAL",         emoji: "⭐" };
  return             { label: "NO SIGNAL",                 emoji: "—" };
}

function ScoreArc({ score }: { score: number }) {
  const color = getScoreColor(score);
  const radius = 70, stroke = 10, cx = 90, cy = 90;
  const circumference = Math.PI * radius;
  const dash = (score / 100) * circumference;
  return (
    <svg width="180" height="100" viewBox="0 0 180 100">
      <path d={`M ${cx-radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx+radius} ${cy}`}
        fill="none" stroke={C.border} strokeWidth={stroke} strokeLinecap="round" />
      <path d={`M ${cx-radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx+radius} ${cy}`}
        fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference}`}
        style={{ transition: "stroke-dasharray 0.6s ease, stroke 0.4s ease" }} />
      <text x={cx} y={cy-12} textAnchor="middle" fill={color} fontSize="28" fontWeight="700" fontFamily="monospace">{score}</text>
      <text x={cx} y={cy+8}  textAnchor="middle" fill={C.textDim} fontSize="10" fontFamily="monospace">/ 100</text>
    </svg>
  );
}

function Tip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <span onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}
        style={{ cursor: "help", color: C.muted, fontSize: 12, marginLeft: 4 }}>ⓘ</span>
      {show && (
        <span style={{
          position: "absolute", bottom: "120%", left: "50%", transform: "translateX(-50%)",
          background: "#1a2540", border: `1px solid ${C.border}`, borderRadius: 6,
          padding: "6px 10px", fontSize: 11, color: C.textDim, whiteSpace: "normal",
          zIndex: 100, boxShadow: "0 4px 20px rgba(0,0,0,0.5)", maxWidth: 220, lineHeight: 1.4,
        }}>{text}</span>
      )}
    </span>
  );
}

export default function InstitutionalConvictionScore() {
  const [ticker,     setTicker]     = useState("");
  const [checked,    setChecked]    = useState<Record<string, boolean>>({});
  const [aiAnalysis, setAiAnalysis] = useState("");
  const [loading,    setLoading]    = useState(false);
  const [submitted,  setSubmitted]  = useState(false);
  const [error,      setError]      = useState("");

  const score = Math.round(
    CRITERIA.reduce((sum, c) => sum + (checked[c.id] ? c.weight : 0), 0) / TOTAL_WEIGHT * 100
  );
  const activeSignals = CRITERIA.filter(c => checked[c.id]);
  const { label, emoji } = getScoreLabel(score);
  const scoreColor = getScoreColor(score);

  const toggle = (id: string) => setChecked(prev => ({ ...prev, [id]: !prev[id] }));
  const reset  = () => { setChecked({}); setAiAnalysis(""); setSubmitted(false); setTicker(""); setError(""); };

  const runAnalysis = async () => {
    if (activeSignals.length === 0) return;
    setLoading(true); setSubmitted(true); setAiAnalysis(""); setError("");
    try {
      const res = await fetch("/stock-api/ics-thesis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: ticker || "this ticker",
          score,
          label,
          signals: activeSignals.map(s => ({ label: s.label, description: s.description })),
        }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setAiAnalysis(data.thesis || "No response received.");
    } catch (err: any) {
      setError(err.message || "Analysis failed.");
    }
    setLoading(false);
  };

  return (
    <div style={{ minHeight: "100%", background: C.bg, color: C.text, fontFamily: "'Inter','Segoe UI',sans-serif", padding: "24px 16px" }}>
      <div style={{ maxWidth: 860, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{ fontSize: 11, letterSpacing: 4, color: C.accent, marginBottom: 8, fontWeight: 700 }}>STOCKSCANNER AI — FLOW ANALYSIS</div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800, color: C.text, letterSpacing: "-0.5px" }}>Institutional Conviction Score</h1>
          <p style={{ color: C.textDim, fontSize: 13, marginTop: 8 }}>Score options flow signals to identify smart money positioning</p>
        </div>

        {/* Ticker + Arc */}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", background: C.surface, border: `1px solid ${C.border}`, borderRadius: 14, padding: "24px 20px", marginBottom: 24, gap: 12 }}>
          <input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            placeholder="TICKER (e.g. NVDA)"
            style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, color: C.accent, fontSize: 20, fontWeight: 700, fontFamily: "monospace", padding: "10px 20px", width: 180, textAlign: "center", outline: "none", letterSpacing: 3 }} />
          <ScoreArc score={score} />
          <div style={{ fontSize: 13, fontWeight: 700, color: scoreColor, letterSpacing: 2 }}>{emoji} {label}</div>
          <div style={{ fontSize: 11, color: C.textDim }}>{activeSignals.length} of {CRITERIA.length} signals active</div>
        </div>

        {/* Checklist */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 11, letterSpacing: 3, color: C.muted, marginBottom: 12, fontWeight: 700 }}>SIGNAL CHECKLIST</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {CRITERIA.map(c => {
              const active = !!checked[c.id];
              return (
                <div key={c.id} onClick={() => toggle(c.id)} style={{
                  display: "flex", alignItems: "center", gap: 14,
                  background: active ? `${C.accent}12` : C.card,
                  border: `1px solid ${active ? C.accent : C.border}`,
                  borderRadius: 10, padding: "12px 16px", cursor: "pointer",
                  transition: "all 0.18s ease",
                }}>
                  <div style={{ width: 20, height: 20, borderRadius: 5, flexShrink: 0, border: `2px solid ${active ? C.accent : C.muted}`, background: active ? C.accent : "transparent", display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.18s" }}>
                    {active && <span style={{ color: "#000", fontSize: 13, fontWeight: 900 }}>✓</span>}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: active ? C.accent : C.text }}>
                      {c.label}<Tip text={c.tooltip} />
                    </div>
                    <div style={{ fontSize: 11, color: C.textDim, marginTop: 2 }}>{c.description}</div>
                  </div>
                  <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: active ? C.green : C.muted, background: active ? `${C.green}18` : C.surface, border: `1px solid ${active ? C.green : C.border}`, borderRadius: 5, padding: "2px 8px", flexShrink: 0 }}>
                    +{c.weight}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Buttons */}
        <div style={{ display: "flex", gap: 12, marginBottom: 24 }}>
          <button onClick={runAnalysis} disabled={loading || activeSignals.length === 0} style={{
            flex: 1, padding: "14px 0", borderRadius: 10, border: "none",
            background: activeSignals.length === 0 ? C.muted : `linear-gradient(135deg, ${C.accent}, #0099cc)`,
            color: activeSignals.length === 0 ? C.bg : "#000",
            fontSize: 14, fontWeight: 800, cursor: activeSignals.length === 0 ? "not-allowed" : "pointer",
            letterSpacing: 1, transition: "opacity 0.2s", opacity: loading ? 0.7 : 1,
          }}>
            {loading ? "⚡ ANALYZING..." : "⚡ GENERATE AI THESIS"}
          </button>
          <button onClick={reset} style={{ padding: "14px 20px", borderRadius: 10, border: `1px solid ${C.border}`, background: C.card, color: C.textDim, fontSize: 13, fontWeight: 600, cursor: "pointer" }}>
            Reset
          </button>
        </div>

        {/* Score bar */}
        {activeSignals.length > 0 && (
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: "16px", marginBottom: 24 }}>
            <div style={{ fontSize: 11, letterSpacing: 2, color: C.muted, marginBottom: 10, fontWeight: 700 }}>SCORE BREAKDOWN</div>
            <div style={{ height: 8, background: C.border, borderRadius: 99, overflow: "hidden" }}>
              <div style={{ height: "100%", borderRadius: 99, width: `${score}%`, background: `linear-gradient(90deg, ${C.accent}, ${scoreColor})`, transition: "width 0.5s ease" }} />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
              <span style={{ fontSize: 11, color: C.red }}>0 — No Signal</span>
              <span style={{ fontSize: 11, color: C.yellow }}>45 — Moderate</span>
              <span style={{ fontSize: 11, color: C.green }}>80+ — Extreme</span>
            </div>
          </div>
        )}

        {/* AI output */}
        {submitted && (
          <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 12, padding: "20px", marginBottom: 24 }}>
            <div style={{ fontSize: 11, letterSpacing: 3, color: C.accent, marginBottom: 14, fontWeight: 700 }}>
              AI TRADE THESIS {ticker ? `— ${ticker}` : ""}
            </div>
            {loading ? (
              <div style={{ color: C.textDim, fontSize: 13, textAlign: "center", padding: "20px 0" }}>
                Analyzing {activeSignals.length} signals...
              </div>
            ) : error ? (
              <div style={{ color: C.red, fontSize: 13 }}>{error}</div>
            ) : (
              <div style={{ fontSize: 13, lineHeight: 1.8, color: C.text, whiteSpace: "pre-wrap", fontFamily: "'Courier New', monospace" }}>
                {aiAnalysis}
              </div>
            )}
          </div>
        )}

        {/* Active signal tags */}
        {activeSignals.length > 0 && (
          <div style={{ marginBottom: 24 }}>
            <div style={{ fontSize: 11, letterSpacing: 3, color: C.muted, marginBottom: 10, fontWeight: 700 }}>ACTIVE SIGNALS</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {activeSignals.map(s => (
                <span key={s.id} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 20, background: `${C.green}15`, border: `1px solid ${C.green}40`, color: C.green, fontWeight: 600 }}>
                  {s.label}
                </span>
              ))}
            </div>
          </div>
        )}

        <div style={{ textAlign: "center", fontSize: 11, color: C.muted, marginTop: 32 }}>
          StockScanner AI — For educational purposes only. Not financial advice.
        </div>
      </div>
    </div>
  );
}
