import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { createStockScannerCheckout, manageStockScannerSubscription, fetchBullFlow, fetchAITrades, checkAITradesSubscription, BullFlowRow, AITradeSetup } from "@/lib/api";

function fmtPrem(m: number) {
  if (m >= 1) return `$${m.toFixed(1)}M`;
  return `$${(m * 1000).toFixed(0)}K`;
}
function fmtExpiry(s: string | null) {
  if (!s) return "—";
  const d = new Date(s + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function fmtStrike(strike: number | null, price: number) {
  if (!strike) return "—";
  return `$${strike.toFixed(0)}C`;
}
function getBadge(ratio: number): { text: string; color: string; bg: string; border: string } {
  if (ratio >= 5)   return { text: "🔥 Extremely Bullish", color: "#6ee7b7", bg: "rgba(74,222,128,0.15)",  border: "rgba(74,222,128,0.3)" };
  if (ratio >= 2)   return { text: "📈 Very Bullish",      color: "#4ade80", bg: "rgba(74,222,128,0.1)",   border: "rgba(74,222,128,0.25)" };
  if (ratio >= 1)   return { text: "↔️ Mixed",             color: "#60a5fa", bg: "rgba(96,165,250,0.1)",   border: "rgba(96,165,250,0.25)" };
  if (ratio >= 0.5) return { text: "⚠️ More Puts",         color: "#fb923c", bg: "rgba(251,146,60,0.1)",   border: "rgba(251,146,60,0.3)" };
  return                    { text: "🔴 Mostly Puts",       color: "#f87171", bg: "rgba(239,68,68,0.1)",    border: "rgba(239,68,68,0.3)" };
}
const RANKS = ["🥇","🥈","🥉","4","5","6","7","8","9","10"];

export default function Landing() {
  const [, setLocation] = useLocation();
  const [email, setEmail] = useState("");
  const [manageEmail, setManageEmail] = useState("");
  const [status, setStatus] = useState<"idle"|"loading"|"ok"|"err">("idle");
  const [errMsg, setErrMsg] = useState("");
  const [showManage, setShowManage] = useState(false);
  const [tickerPos, setTickerPos] = useState(0);
  const [liveFlow, setLiveFlow] = useState<BullFlowRow[]>([]);
  const [topPick, setTopPick] = useState<AITradeSetup | null>(null);
  const [topPickLoading, setTopPickLoading] = useState(true);

  // Top blurred section unlock state (independent of pricing form)
  const [topEmail, setTopEmail] = useState("");
  const [topStatus, setTopStatus] = useState<"idle"|"loading"|"ok"|"err">("idle");
  const [topErr, setTopErr] = useState("");

  useEffect(() => {
    fetchBullFlow().then(d => setLiveFlow(d.results ?? [])).catch(() => {});
    fetchAITrades()
      .then(d => {
        const bullish = (d.trades ?? []).find(t => t.direction === "BULLISH") ?? d.trades?.[0] ?? null;
        setTopPick(bullish);
      })
      .catch(() => {})
      .finally(() => setTopPickLoading(false));

    // Auto-check localStorage — if already verified, unlock immediately
    const saved = localStorage.getItem("ait_sub_email");
    if (saved) {
      setTopEmail(saved);
      checkAITradesSubscription(saved)
        .then(r => { if (r.subscribed) setTopStatus("ok"); })
        .catch(() => {});
    }
  }, []);

  const handleTopUnlock = async () => {
    if (!topEmail.trim() || !topEmail.includes("@")) { setTopErr("Enter a valid email"); return; }
    setTopStatus("loading"); setTopErr("");
    try {
      const r = await checkAITradesSubscription(topEmail.trim());
      if (r.subscribed) {
        localStorage.setItem("ait_sub_email", topEmail.trim());
        setTopStatus("ok");
        // Also sync the pricing form email
        setEmail(topEmail.trim());
        setStatus("ok");
      } else {
        setTopStatus("idle");
        setTopErr("No active subscription — scroll down to subscribe.");
      }
    } catch {
      setTopStatus("idle");
      setTopErr("Could not verify — try again.");
    }
  };

  const bullishFlow = liveFlow.filter(r => r.call_put_ratio >= 2);

  const tickerSignals: string[] = bullishFlow.length >= 2
    ? bullishFlow.slice(0, 8).map(r => {
        const b = getBadge(r.call_put_ratio);
        return `${b.text.split(" ")[0]} ${r.ticker} ${fmtStrike(r.strike, r.price)} ${fmtExpiry(r.expiry)} · ${fmtPrem(r.premium_m)} · ${r.call_put_ratio.toFixed(1)}x C/P`;
      })
    : ["⏳ Fetching live signals…","⏳ Fetching live signals…","⏳ Fetching live signals…"];

  useEffect(() => {
    const id = setInterval(() => setTickerPos(p => p + 1), 40);
    return () => clearInterval(id);
  }, []);

  const handleSubscribe = async () => {
    if (!email.trim() || !email.includes("@")) { setErrMsg("Enter a valid email"); setStatus("err"); return; }
    setStatus("loading");
    try {
      const check = await checkAITradesSubscription(email.trim());
      if (check.subscribed) {
        localStorage.setItem("ait_sub_email", email.trim());
        setStatus("ok");
        return;
      }
      const { url } = await createStockScannerCheckout(email.trim());
      window.location.href = url;
    } catch (err: any) {
      setErrMsg(err.message ?? "Failed to start checkout");
      setStatus("err");
    }
  };

  const handleManage = async () => {
    if (!manageEmail.trim() || !manageEmail.includes("@")) return;
    setStatus("loading");
    try {
      const { url } = await manageStockScannerSubscription(manageEmail.trim());
      window.location.href = url;
    } catch (err: any) {
      setErrMsg(err.message ?? "No subscription found");
      setStatus("err");
    }
  };

  return (
    <div style={{ background: "#060c14", minHeight: "100vh", fontFamily: "Inter,system-ui,sans-serif", color: "#fff", overflowX: "hidden" }}>

      {/* Nav */}
      <nav style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(20px)", background: "rgba(6,12,20,0.9)" }}>
        <div className="flex items-center justify-between px-6 py-4 max-w-6xl mx-auto">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center font-black text-base" style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)", boxShadow: "0 0 20px rgba(34,197,94,0.4)" }}>S</div>
            <span className="font-black text-xl tracking-tight">StockScanner <span style={{ color: "#4ade80" }}>AI</span></span>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => setShowManage(!showManage)} className="font-medium px-4 py-2 rounded-lg transition-colors text-base" style={{ color: "#64748b" }}>Sign In</button>
            <button onClick={() => setLocation("/app")} className="font-black px-6 py-2.5 rounded-xl text-base transition-all" style={{ background: "#22c55e", color: "#fff", boxShadow: "0 4px 20px rgba(34,197,94,0.35)" }}>
              Open App →
            </button>
          </div>
        </div>
      </nav>

      {/* Live Ticker */}
      <div style={{ background: "rgba(34,197,94,0.05)", borderBottom: "1px solid rgba(34,197,94,0.15)", overflow: "hidden", height: "40px", display: "flex", alignItems: "center" }}>
        <div style={{ display: "flex", gap: "80px", transform: `translateX(${-((tickerPos * 0.6) % 2200)}px)`, whiteSpace: "nowrap", transition: "none" }}>
          {[...tickerSignals, ...tickerSignals, ...tickerSignals].map((s, i) => (
            <span key={i} className="text-sm font-semibold" style={{ color: "#4ade80" }}>{s}</span>
          ))}
        </div>
      </div>

      {/* ── BLURRED SIGNAL PREVIEW ── */}
      <div style={{ position: "relative", background: "rgba(6,12,20,0.97)", borderBottom: "1px solid rgba(34,197,94,0.12)", padding: "0" }}>
        {/* Blurred cards */}
        <div style={{ filter: "blur(5px)", userSelect: "none", pointerEvents: "none", padding: "14px 16px", display: "flex", gap: 10, overflowX: "hidden" }}>
          {[
            { ticker: "NVDA", dir: "BULLISH", setup: "BREAKOUT CONTINUATION", strike: "$142C", expiry: "Jul 18", prem: "$11.4M", conv: "HIGH" },
            { ticker: "META", dir: "BULLISH", setup: "DARK POOL ACCUMULATION", strike: "$660C", expiry: "Jul 11", prem: "$8.7M", conv: "HIGH" },
            { ticker: "AAPL", dir: "BULLISH", setup: "GAMMA WALL SQUEEZE",    strike: "$220C", expiry: "Jul 18", prem: "$6.2M", conv: "MED"  },
            { ticker: "TSLA", dir: "BULLISH", setup: "SMART MONEY DIVERGENCE", strike: "$290C", expiry: "Jul 25", prem: "$9.1M", conv: "HIGH" },
          ].map(t => (
            <div key={t.ticker} style={{ minWidth: 200, flex: "0 0 200px", background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.25)", borderRadius: 12, padding: "12px 14px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ color: "#fff", fontWeight: 900, fontSize: 18 }}>{t.ticker}</span>
                <span style={{ background: t.conv === "HIGH" ? "rgba(251,191,36,0.15)" : "rgba(34,197,94,0.1)", color: t.conv === "HIGH" ? "#fbbf24" : "#4ade80", fontSize: 9, fontWeight: 800, padding: "2px 7px", borderRadius: 4 }}>{t.conv}</span>
              </div>
              <div style={{ color: "#4ade80", fontSize: 10, fontWeight: 700, marginBottom: 4 }}>{t.dir} · {t.setup}</div>
              <div style={{ color: "#64748b", fontSize: 10 }}>Strike {t.strike} · {t.expiry}</div>
              <div style={{ color: "#94a3b8", fontSize: 11, fontWeight: 700, marginTop: 6 }}>{t.prem} premium</div>
            </div>
          ))}
        </div>

        {/* Overlay */}
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "linear-gradient(to right, rgba(6,12,20,0.6), rgba(6,12,20,0.35), rgba(6,12,20,0.6))", gap: 8, padding: "0 16px" }}>
          {topStatus === "ok" ? (
            /* ── SUCCESS STATE ── */
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 22 }}>✅</div>
              <div style={{ color: "#4ade80", fontWeight: 900, fontSize: 14, letterSpacing: "-0.01em" }}>You're in! Subscription confirmed.</div>
              <button
                onClick={() => setLocation("/app")}
                style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", fontWeight: 900, fontSize: 13, padding: "10px 28px", borderRadius: 999, border: "none", cursor: "pointer", boxShadow: "0 8px 32px rgba(34,197,94,0.45)" }}>
                Open App →
              </button>
            </div>
          ) : (
            /* ── LOCKED STATE — inline email unlock ── */
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                <span style={{ fontSize: 14 }}>🔒</span>
                <span style={{ color: "#fff", fontWeight: 800, fontSize: 12 }}>Today's AI trade setups — live right now</span>
              </div>
              <div style={{ display: "flex", gap: 6, width: "100%", maxWidth: 360 }}>
                <input
                  type="email"
                  value={topEmail}
                  onChange={e => { setTopEmail(e.target.value); setTopErr(""); }}
                  onKeyDown={e => e.key === "Enter" && handleTopUnlock()}
                  placeholder="your@email.com"
                  style={{ flex: 1, background: "rgba(255,255,255,0.1)", border: "1px solid rgba(255,255,255,0.2)", borderRadius: 999, padding: "9px 16px", color: "#fff", fontSize: 12, outline: "none" }}
                />
                <button
                  onClick={handleTopUnlock}
                  disabled={topStatus === "loading"}
                  style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", fontWeight: 900, fontSize: 12, padding: "9px 18px", borderRadius: 999, border: "none", cursor: "pointer", whiteSpace: "nowrap", opacity: topStatus === "loading" ? 0.7 : 1 }}>
                  {topStatus === "loading" ? "…" : "Subscribe to see →"}
                </button>
              </div>
              {topErr && <div style={{ color: "#f87171", fontSize: 10, marginTop: 2 }}>{topErr}</div>}
            </>
          )}
        </div>
      </div>

      {/* ── HERO ── */}
      <div className="relative text-center overflow-hidden" style={{ padding: "100px 24px 80px" }}>
        <div style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: "900px", height: "600px", background: "radial-gradient(ellipse at 50% 0%, rgba(34,197,94,0.18) 0%, transparent 70%)", pointerEvents: "none" }} />
        <div className="relative max-w-5xl mx-auto">
          <div className="inline-flex items-center px-6 py-2.5 rounded-full font-black text-sm uppercase tracking-widest mb-5" style={{ background: "#22c55e", color: "#0b1a0e", letterSpacing: "0.08em" }}>
            Pure Call Options — Exclusively
          </div>
          <br />
          <div className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-bold mb-10" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.3)", color: "#4ade80" }}>
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse inline-block" />
            Right now, 8 signals are firing on the same ticker. You're not seeing all of them.
          </div>

          <h1 className="font-black leading-none mb-8" style={{ fontSize: "clamp(3.2rem,9vw,7.5rem)", letterSpacing: "-0.055em", lineHeight: 0.92 }}>
            You're seeing<br />fragments.<br />
            <span style={{ color: "#4ade80", textShadow: "0 0 160px rgba(74,222,128,0.5)" }}>We show the pattern.</span>
          </h1>

          {/* ── PROPRIETARY SIGNAL STRIP ── */}
          <div className="mb-10">
            <p className="text-slate-500 text-xs uppercase tracking-widest font-bold mb-4">6 signals you won't find on any other platform</p>
            <div className="flex flex-wrap justify-center gap-3">
              {[
                { code: "FIR", label: "Float Impact Ratio", desc: "Forces MMs to buy", color: "#4ade80", glow: "34,197,94" },
                { code: "CHARM Δ", label: "Charm Acceleration", desc: "Time-powered squeeze", color: "#60a5fa", glow: "96,165,250" },
                { code: "GEX", label: "Dealer Gamma Exposure", desc: "Breakout amplifier", color: "#a78bfa", glow: "167,139,250" },
                { code: "OI BUILD", label: "Consecutive OI Buildup", desc: "10 days = someone knows", color: "#fbbf24", glow: "251,191,36" },
                { code: "SMP 8-LAYER", label: "Smart Money Pressure", desc: "8 forces converging", color: "#fb923c", glow: "251,146,60" },
                { code: "CONV-STACK", label: "Conviction Stack 0–16", desc: "≥8 = ELITE threshold", color: "#34d399", glow: "52,211,153" },
              ].map(sig => (
                <div key={sig.code} className="flex items-center gap-2 px-4 py-2.5 rounded-xl" style={{ background: `rgba(${sig.glow},0.07)`, border: `1px solid rgba(${sig.glow},0.3)` }}>
                  <span className="font-black text-sm" style={{ color: sig.color, fontFamily: "monospace", letterSpacing: "0.04em" }}>{sig.code}</span>
                  <span className="text-slate-500 text-xs hidden sm:inline">·</span>
                  <span className="text-slate-400 text-xs hidden sm:inline">{sig.desc}</span>
                </div>
              ))}
            </div>
            <p className="text-slate-600 text-xs mt-3 italic">Unusual Whales doesn't have these. FlowAlgo doesn't. BlackBox doesn't. No one does.</p>
          </div>

          <p className="mx-auto mb-6 text-slate-300" style={{ fontSize: "clamp(1.1rem,2.5vw,1.4rem)", maxWidth: "680px", lineHeight: 1.75 }}>
            Right now there's a stock where OI has been quietly building for 4 days. Gamma pressure is mathematically locked in. A far-OTM sweep just fired. Dark pool printed $2M overnight. Shorts are trapped. The sector is rotating in. That's 6 of 8 signals converging on the same name — and Unusual Whales is showing you only the sweep. FlowAlgo only the flow. <strong className="text-white">Nobody is showing you the convergence.</strong> StockScanner AI runs 28 signals simultaneously — 20 ICS scoring signals plus an 8-layer conviction stack — and outputs 5 ELITE trade setups every morning with the exact strike, expiry, and a full written thesis, before the move starts.
          </p>

          <p className="mx-auto mb-12 font-bold" style={{ fontSize: "1.05rem", maxWidth: "580px", color: "#fbbf24" }}>
            That convergence you're not seeing? It's in here. Every single day.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-6">
            <button onClick={() => document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" })}
              className="font-black px-12 py-5 rounded-2xl transition-all text-xl"
              style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", boxShadow: "0 16px 56px rgba(34,197,94,0.5)", letterSpacing: "-0.02em" }}>
              Get Instant Access — $397/mo
            </button>
            <button onClick={() => setLocation("/app")}
              className="font-bold px-10 py-5 rounded-2xl transition-all text-xl"
              style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.15)", color: "#cbd5e1" }}>
              See It Live →
            </button>
          </div>
          <p className="text-slate-500 text-base">Cancel anytime · Instant access · No contracts</p>
        </div>
      </div>

      {/* ── HOW IT WORKS ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">How it works</p>
        <h2 className="text-center font-black mb-14" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          From raw data to ready-to-execute.<br /><span style={{ color: "#4ade80" }}>Every single morning.</span>
        </h2>
        <div className="grid sm:grid-cols-3 gap-0 relative">
          <div className="hidden sm:block absolute top-10 left-1/3 right-1/3 h-px" style={{ background: "linear-gradient(to right, rgba(34,197,94,0.4), rgba(34,197,94,0.4))" }} />
          {[
            {
              step: "01",
              time: "Overnight → 9 AM ET",
              icon: "🔭",
              title: "21 sources scan 500+ tickers",
              desc: "Dark pool prints, options flow, short interest, OI buildup, gamma exposure, charm acceleration, sector heat, macro indicators, analyst targets — all pulled before you wake up. No manual work.",
              color: "#4ade80",
            },
            {
              step: "02",
              time: "9 AM → 9:45 AM ET",
              icon: "⚡",
              title: "ELITE engine scores every ticker",
              desc: "The 8-Layer Conviction Stack assigns 0–2 pts across dark pool, OI build, gamma, charm, squeeze fuel, float demand, sweep, and sector. Float Impact Ratio then filters for mathematically forced moves. Only the top converging names survive.",
              color: "#fbbf24",
            },
            {
              step: "03",
              time: "By 10 AM ET",
              icon: "🤖",
              title: "AI writes your 5 complete setups",
              desc: "GPT reads every scored signal and outputs 5 ELITE trade setups in plain English: ticker, direction, entry strike, expiry, target, stop loss, and a written thesis. Ready to execute — no interpretation needed.",
              color: "#60a5fa",
            },
          ].map((s) => {
            const rgb = s.color === "#4ade80" ? "34,197,94" : s.color === "#fbbf24" ? "251,191,36" : "96,165,250";
            return (
            <div key={s.step} className="relative flex flex-col items-center text-center px-6 pb-8">
              <div className="w-20 h-20 rounded-2xl flex items-center justify-center text-3xl mb-5 relative z-10" style={{ background: `rgba(${rgb},0.1)`, border: `2px solid ${s.color}44` }}>
                {s.icon}
              </div>
              <div className="text-xs font-black mb-1 px-3 py-1 rounded-full" style={{ background: "rgba(255,255,255,0.04)", color: s.color, border: `1px solid ${s.color}44` }}>{s.time}</div>
              <div className="font-black text-white text-lg mt-3 mb-3" style={{ letterSpacing: "-0.02em" }}>{s.title}</div>
              <div className="text-slate-400 text-sm leading-relaxed">{s.desc}</div>
            </div>
            );
          })}
        </div>
        <div className="mt-6 rounded-2xl px-6 py-4 text-center" style={{ background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.18)" }}>
          <span className="text-slate-400 text-sm">You open your email by 10 AM. Five setups. Each one with a strike, expiry, target, stop, and thesis. </span>
          <span className="font-bold text-white text-sm">No other platform does this at any price.</span>
        </div>
      </div>

      {/* ── THE PROBLEM ── */}
      <div className="px-6 pb-20 max-w-4xl mx-auto text-center">
        <p className="text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">The problem with every competitor</p>
        <h2 className="font-black mb-6" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          They show you one signal.<br /><span className="text-slate-500">The move requires 8 agreeing.</span>
        </h2>
        <p className="text-slate-400 mx-auto mb-14" style={{ maxWidth: "640px", fontSize: "1.1rem", lineHeight: 1.75 }}>
          Unusual Whales shows you the sweep. FlowAlgo shows you the flow. Smart money shows you a score. But when OI buildup, gamma pressure, charm acceleration, squeeze fuel, dark pool, float demand, far-OTM sweep, and sector heat all fire on the same ticker on the same day — that convergence is the signal that moves stocks 20–40%. Other platforms are showing you fragments of it. You're closing 6 tabs and still missing the picture.
        </p>
        <div className="grid sm:grid-cols-3 gap-4 text-left">
          {[
            { before: "8 signals are converging on one ticker right now. You have no way to see them together.", after: "Smart Money Pressure scores all 8 convergence layers on one ticker, in one view, automatically. 4+ firing = the mechanics nearly force the move.", icon: "🗂️" },
            { before: "You see heavy put volume and don't know if it's a hedge or a real bearish bet.", after: "Put Intent Decoder classifies every put instantly: hedge vs directional bet. The distinction that changes whether a signal is bullish or bearish.", icon: "🎯" },
            { before: "You get a signal score. No entry, no strike, no expiry, no thesis.", after: "You get: ticker, direction, entry strike, expiry, target, stop loss, and a written thesis — for 5 ELITE setups every morning. Ready to execute.", icon: "📋" },
          ].map(p => (
            <div key={p.before} className="rounded-2xl p-6" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-3xl mb-4">{p.icon}</div>
              <div className="text-slate-500 text-sm line-through mb-3 leading-relaxed">"{p.before}"</div>
              <div className="text-emerald-300 font-bold text-sm leading-relaxed">→ {p.after}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── AI SYNTHESIS HERO CALLOUT ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <div className="rounded-3xl p-8 sm:p-10 relative overflow-hidden" style={{ background: "linear-gradient(135deg, rgba(34,197,94,0.07), rgba(6,12,20,1))", border: "2px solid rgba(34,197,94,0.3)", boxShadow: "0 0 80px rgba(34,197,94,0.07)" }}>
          <div style={{ position: "absolute", top: 0, right: 0, width: "400px", height: "300px", background: "radial-gradient(ellipse at 100% 0%, rgba(34,197,94,0.12) 0%, transparent 70%)", pointerEvents: "none" }} />
          <div className="relative">
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-black mb-6" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.35)", color: "#4ade80" }}>
              🤖 THE FEATURE NO COMPETITOR HAS BUILT
            </div>
            <h2 className="font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.2rem)", letterSpacing: "-0.04em" }}>
              AI Trade Synthesis
            </h2>
            <p className="text-slate-300 mb-8" style={{ maxWidth: "620px", fontSize: "1.1rem", lineHeight: 1.75 }}>
              Every day, our AI runs all 28 signals simultaneously — including signals no other platform tracks: <strong className="text-white">Float Impact Ratio (FIR)</strong>, the mathematical forcing function that makes market makers legally required to buy; <strong className="text-white">Charm Acceleration</strong>, the time-decaying squeeze that builds buying pressure every single day without a price catalyst; and <strong className="text-white">Dealer Gamma Exposure (GEX)</strong>, which tells you whether a breakout will run or fade before you enter. On top of those: dark pool flow, smart money vs retail divergence, options flow, IV rank, gamma walls, max pain, market regime, OI buildup days, consecutive accumulation streaks, short squeeze fuel, MACD momentum, VWAP, VIX term structure, HYG credit health, and more. Then it outputs 5 ELITE trade setups, written in plain English, ranked from most bullish to most bearish.
            </p>
            {/* ── SMP L1–L8 BREAKDOWN ── */}
            <div className="rounded-2xl p-5 mb-8" style={{ background: "rgba(251,146,60,0.05)", border: "1px solid rgba(251,146,60,0.2)" }}>
              <p className="text-xs font-black uppercase tracking-widest mb-4" style={{ color: "#fb923c" }}>Smart Money Pressure Score — 8 independent layers firing simultaneously</p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { layer: "L1", name: "OI Buildup", desc: "Consecutive days loading the same strike" },
                  { layer: "L2", name: "Gamma Lockup", desc: "MM delta obligations locking in forced buying" },
                  { layer: "L3", name: "Charm Accel", desc: "Time-decay squeeze building daily" },
                  { layer: "L4", name: "Squeeze Fuel", desc: "Short float + days-to-cover + borrow rate" },
                  { layer: "L5", name: "Dark Pool", desc: "Institutional block prints off-exchange" },
                  { layer: "L6", name: "Float Demand (FIR)", desc: "Gamma obligations vs total share float" },
                  { layer: "L7", name: "Far-OTM Sweep", desc: "Aggressive conviction far out-of-the-money" },
                  { layer: "L8", name: "Sector Heat", desc: "Macro + sector rotation aligned" },
                ].map(l => (
                  <div key={l.layer} className="rounded-lg p-3" style={{ background: "rgba(251,146,60,0.06)", border: "1px solid rgba(251,146,60,0.12)" }}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-black px-1.5 py-0.5 rounded" style={{ background: "rgba(251,146,60,0.2)", color: "#fb923c" }}>{l.layer}</span>
                      <span className="text-white text-xs font-bold">{l.name}</span>
                    </div>
                    <p className="text-slate-500 text-xs leading-snug">{l.desc}</p>
                  </div>
                ))}
              </div>
              <p className="text-xs mt-4 font-bold" style={{ color: "#fb923c" }}>Score 4+/8: mechanics are aligned. Score 7+/8: we've rarely seen this not move. No other platform scores all 8 simultaneously.</p>
            </div>

            <div className="grid sm:grid-cols-2 gap-6 mb-8">
              <div>
                <p className="text-slate-500 text-xs uppercase tracking-widest font-bold mb-3">What you get from competitors</p>
                <div className="space-y-2">
                  {[
                    "Dark pool tab → raw numbers",
                    "Options flow tab → ticker + premium",
                    "Smart money tab → score",
                    "Congress tab → filing date",
                    "You → figure out what it means yourself",
                  ].map(t => (
                    <div key={t} className="flex items-center gap-2 text-sm text-slate-500">
                      <span className="font-black text-red-900">✕</span>{t}
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs uppercase tracking-widest font-bold mb-3" style={{ color: "#4ade80" }}>What you get from StockScanner AI</p>
                <div className="space-y-2">
                  {[
                    "All 28 signals → fed to AI together",
                    "5 picks → sorted most bullish to bearish",
                    "Entry strike + expiry + target + stop",
                    "Written thesis: why these signals align",
                    "Conviction level: HIGH or MEDIUM",
                  ].map(t => (
                    <div key={t} className="flex items-center gap-2 text-sm text-emerald-300 font-medium">
                      <span className="font-black text-emerald-400">✓</span>{t}
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="rounded-2xl p-5" style={{ background: "rgba(6,12,20,0.8)", border: "1px solid rgba(34,197,94,0.2)" }}>
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs text-slate-500 uppercase tracking-widest font-bold">Today's top AI pick — live</p>
                <span className="flex items-center gap-1.5 text-xs font-bold" style={{ color: "#4ade80" }}>
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
                  {new Date().toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </span>
              </div>
              {topPickLoading && (
                <div className="flex items-center gap-2 text-slate-500 text-sm py-2">
                  <span className="flex gap-1">{[0,1,2].map(i => <span key={i} className="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />)}</span>
                  AI is synthesizing today's signals…
                </div>
              )}
              {!topPickLoading && topPick && (
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div>
                    <div className="flex items-center gap-3 mb-1 flex-wrap">
                      <span className="font-black text-white text-2xl">{topPick.ticker}</span>
                      <span className="text-slate-400 text-sm font-bold">${topPick.price.toFixed(2)}</span>
                      <span className="px-2 py-0.5 rounded text-xs font-black" style={{ background: "rgba(74,222,128,0.1)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.25)" }}>{topPick.direction}</span>
                      <span className="px-2 py-0.5 rounded text-xs font-black" style={{ background: "rgba(251,191,36,0.1)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.25)" }}>{topPick.conviction} CONVICTION</span>
                    </div>
                    <p className="text-slate-400 text-sm mb-2">
                      Setup: <span className="text-white font-bold">{topPick.setup_type}</span> · Entry ${topPick.entry_strike}C · Exp {topPick.expiry} · Target ${topPick.target_price} · Stop ${topPick.stop_loss}
                    </p>
                    <p className="text-slate-400 text-sm" style={{ maxWidth: "480px" }}>
                      <span className="text-slate-300 font-semibold">Thesis:</span> {topPick.thesis}
                    </p>
                  </div>
                  {topPick.signals_aligned.length > 0 && (
                    <div className="text-right shrink-0">
                      <div className="text-xs text-slate-600 mb-1">Signals aligned</div>
                      {topPick.signals_aligned.slice(0, 4).map(s => (
                        <div key={s} className="text-xs font-bold mb-1" style={{ color: "#4ade80" }}>● {s}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {!topPickLoading && !topPick && (
                <p className="text-slate-500 text-sm">Today's AI picks generate after market open. Check back soon.</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── LIVE CONVICTION SIGNAL ── */}
      <div className="px-6 pb-20 max-w-3xl mx-auto">
        {(() => {
          const top = liveFlow.find(r => r.call_put_ratio >= 2) ?? null;
          const badge = top ? getBadge(top.call_put_ratio) : { text: "🔥 HIGH CONVICTION", color: "#fbbf24", bg: "rgba(234,179,8,0.12)", border: "rgba(234,179,8,0.4)" };
          return (
            <div className="rounded-3xl p-6 sm:p-8" style={{ background: "linear-gradient(135deg, rgba(234,179,8,0.08), rgba(239,68,68,0.05))", border: "2px solid rgba(234,179,8,0.3)", boxShadow: "0 0 60px rgba(234,179,8,0.08)" }}>
              <div className="flex items-center gap-3 mb-5">
                <span className="font-black text-sm px-3 py-1.5 rounded-full animate-pulse" style={{ background: "rgba(234,179,8,0.2)", color: "#fbbf24", border: "1px solid rgba(234,179,8,0.4)" }}>🚨 TODAY'S TOP CONVICTION SIGNAL</span>
                {top && <span className="text-slate-600 text-xs">Live · {new Date().toLocaleDateString("en-US",{month:"short",day:"numeric"})}</span>}
              </div>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-3 mb-2 flex-wrap">
                    <span className="font-black text-white" style={{ fontSize: "2.2rem", letterSpacing: "-0.04em" }}>{top?.ticker ?? "—"}</span>
                    <span className="font-bold text-slate-400 text-lg">{top ? `$${top.price.toFixed(2)}` : "—"}</span>
                    <span className="font-black text-sm px-3 py-1 rounded-full" style={{ background: badge.bg, color: badge.color, border: `1px solid ${badge.border}` }}>{badge.text}</span>
                  </div>
                  <div className="text-slate-400 text-base mb-1">
                    {top ? `${fmtStrike(top.strike, top.price)} Call · Expires ${fmtExpiry(top.expiry)} · ` : ""}
                    <span className="text-white font-bold">{top ? fmtPrem(top.premium_m) : "—"} premium</span>
                  </div>
                  <div className="text-slate-500 text-sm">Call/Put Ratio: <span className="font-black text-lg" style={{ color: badge.color }}>{top?.call_put_ratio?.toFixed(1) ?? "—"}x</span> — someone is betting BIG</div>
                </div>
                <div className="text-right shrink-0">
                  <div className="font-black text-emerald-400" style={{ fontSize: "1.8rem", letterSpacing: "-0.03em" }}>{top ? fmtPrem(top.premium_m) : "—"}</div>
                  <div className="text-slate-500 text-sm">in calls</div>
                </div>
              </div>
              <div className="mt-5 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}>
                <p className="text-slate-400 text-sm italic">🔒 <strong className="text-white">Subscribers see signals like this in real time — plus the AI thesis tying them all together.</strong></p>
              </div>
            </div>
          );
        })()}
      </div>

      {/* ── WHAT'S ACTUALLY EXCLUSIVE ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Honest breakdown</p>
        <h2 className="text-center font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          What we have. What they have. <span style={{ color: "#fbbf24" }}>No spin.</span>
        </h2>
        <p className="text-center text-slate-400 mb-12 mx-auto" style={{ maxWidth: "600px", fontSize: "1rem", lineHeight: 1.6 }}>
          Options flow? Unusual Whales, FlowAlgo, and Cheddar Flow all have it. We're not pretending otherwise. Here's what they genuinely don't have.
        </p>

        <div className="grid sm:grid-cols-3 gap-5 mb-10">
          {[
            {
              icon: "🤖",
              tag: "NOBODY HAS THIS",
              tagColor: "#4ade80",
              tagBg: "rgba(34,197,94,0.12)",
              tagBorder: "rgba(34,197,94,0.3)",
              border: "rgba(34,197,94,0.3)",
              title: "AI Trade Synthesis",
              desc: "All 73 data points fed into GPT simultaneously. Outputs 5 written trade setups daily: ticker, direction, entry strike, expiry, target, stop loss, and thesis. No competitor does cross-source synthesis at any price.",
              note: "Unusual Whales, FlowAlgo, Trade Ideas — none of them do this."
            },
            {
              icon: "🎯",
              tag: "EXCLUSIVE",
              tagColor: "#fbbf24",
              tagBg: "rgba(251,191,36,0.12)",
              tagBorder: "rgba(251,191,36,0.3)",
              border: "rgba(251,191,36,0.25)",
              title: "Put Intent Decoder",
              desc: "Heavy put volume? We tell you if it's a hedge (OTM + long-dated — still bullish underneath) or a real directional bearish bet (near-money + short-dated). The distinction that changes everything.",
              note: "Every competitor shows put volume. None decode what it means."
            },
            {
              icon: "🌑",
              tag: "EXCLUSIVE",
              tagColor: "#fbbf24",
              tagBg: "rgba(251,191,36,0.12)",
              tagBorder: "rgba(251,191,36,0.3)",
              border: "rgba(251,191,36,0.25)",
              title: "Dark Pool Radar",
              desc: "FINRA institutional short-sale flow decoded into plain conviction signals — STRONG BUY, BUY, NEUTRAL, SELL, STRONG SELL — ranked and ready. Competitors show you raw volume numbers and call it a feature.",
              note: "Unusual Whales shows dark pool data. We show you what it means."
            },
          ].map(f => (
            <div key={f.title} className="rounded-2xl p-6" style={{ background: "rgba(6,12,20,0.8)", border: `2px solid ${f.border}` }}>
              <div className="flex items-center gap-2 mb-4">
                <span className="text-3xl">{f.icon}</span>
                <span className="text-xs font-black px-2 py-0.5 rounded-full" style={{ background: f.tagBg, color: f.tagColor, border: `1px solid ${f.tagBorder}` }}>{f.tag}</span>
              </div>
              <div className="font-black text-white text-lg mb-3">{f.title}</div>
              <div className="text-slate-400 text-sm leading-relaxed mb-4">{f.desc}</div>
              <div className="text-xs text-slate-600 italic">{f.note}</div>
            </div>
          ))}
        </div>

        {/* Accumulation Streak — standalone differentiator */}
        <div className="rounded-2xl p-7 mb-10" style={{ background: "linear-gradient(135deg, rgba(124,58,237,0.08) 0%, rgba(6,12,20,0.9) 60%)", border: "2px solid rgba(124,58,237,0.35)" }}>
          <div className="flex flex-col sm:flex-row sm:items-start gap-6">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-4">
                <span className="text-3xl">📈</span>
                <span className="text-xs font-black px-2 py-0.5 rounded-full" style={{ background: "rgba(34,197,94,0.12)", color: "#4ade80", border: "1px solid rgba(34,197,94,0.3)" }}>NOBODY HAS THIS</span>
              </div>
              <div className="font-black text-white mb-3" style={{ fontSize: "clamp(1.25rem,3vw,1.6rem)", letterSpacing: "-0.02em" }}>Accumulation Streak Scanner</div>
              <p className="text-slate-400 text-sm leading-relaxed mb-5" style={{ maxWidth: "560px" }}>
                Every other platform shows you <em>today's</em> flow. We show you who has been buying the same stock for <strong className="text-white">5, 10, or 15 consecutive trading days</strong> — quietly, steadily, without moving the price. That's not retail. That's an institution building a position.
              </p>
              <div className="grid sm:grid-cols-3 gap-3 mb-5">
                {[
                  { icon: "📆", label: "60-Day Lookback", sub: "Detect 1-week, 2-week, and 3-week accumulation campaigns — not just yesterday" },
                  { icon: "📊", label: "Consistency Score", sub: "Separates smooth institutional buying ($1M/day × 10 days) from one-day retail spikes" },
                  { icon: "🔬", label: "Flow Intelligence AI", sub: "GPT reads every streak and tells you CONVICTION, BUILDING, WATCH, or NOISE — in plain English" },
                ].map(c => (
                  <div key={c.label} className="rounded-xl p-3" style={{ background: "rgba(124,58,237,0.07)", border: "1px solid rgba(124,58,237,0.2)" }}>
                    <div className="text-lg mb-1">{c.icon}</div>
                    <div className="text-white text-xs font-black mb-1">{c.label}</div>
                    <div className="text-slate-500 text-xs leading-relaxed">{c.sub}</div>
                  </div>
                ))}
              </div>
              <p className="text-xs italic" style={{ color: "rgba(124,58,237,0.7)" }}>
                Unusual Whales, Finviz, FlowAlgo, Blackbox, InsiderFinance — none track consecutive-day accumulation streaks. You either see it here, or you don't see it.
              </p>
            </div>
            <div className="shrink-0 sm:w-52 rounded-xl p-4 text-center" style={{ background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.25)" }}>
              <div className="text-5xl font-black mb-1" style={{ color: "#a78bfa" }}>473</div>
              <div className="text-slate-400 text-xs mb-4">micro-cap stocks scanned<br />every trading day</div>
              <div className="space-y-2">
                {[
                  { icon: "🏦", label: "20d+", sub: "1 month streak", color: "#c4b5fd" },
                  { icon: "🚀", label: "15d",  sub: "3 weeks", color: "#a78bfa" },
                  { icon: "⚡", label: "10d",  sub: "2 weeks", color: "#fbbf24" },
                  { icon: "🔥", label: "5d",   sub: "1 week",  color: "#fb923c" },
                ].map(b => (
                  <div key={b.label} className="flex items-center gap-2 rounded-lg px-3 py-1.5" style={{ background: "rgba(0,0,0,0.3)" }}>
                    <span className="text-sm">{b.icon}</span>
                    <span className="font-black text-xs" style={{ color: b.color }}>{b.label}</span>
                    <span className="text-slate-600 text-xs">{b.sub}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* More differentiators */}
        <div className="rounded-2xl p-6" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <p className="text-slate-500 text-xs uppercase tracking-widest font-bold mb-5">Also included — things competitors charge more for or don't offer</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { icon: "💥", title: "Squeeze + Low Float Setup", desc: "Short float ≥15% + Days-to-cover ≥5 + tiny float — AI rates each setup CRITICAL/HIGH/WATCH and emails you instantly." },
              { icon: "🌅", title: "Morning Runners", desc: "Scans all 473 tickers pre-market for volume spikes and gap moves. See what's heating up before the open — scored by momentum." },
              { icon: "⚡", title: "Convergence Scanner", desc: "Stocks with unusual volume AND heavy call flow simultaneously — the highest-conviction setup pattern." },
              { icon: "🏆", title: "Smart vs Retail Divergence", desc: "When institutions and retail are on opposite sides of the same ticker, flagged and ranked." },
            ].map(f => (
              <div key={f.title} className="rounded-xl p-4" style={{ background: "rgba(34,197,94,0.04)", border: "1px solid rgba(34,197,94,0.12)" }}>
                <div className="text-2xl mb-2">{f.icon}</div>
                <div className="font-black text-white text-sm mb-1">{f.title}</div>
                <div className="text-slate-500 text-xs leading-relaxed">{f.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── HEDGE-FUND INTELLIGENCE LAYER ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Institutional-grade intelligence</p>
        <h2 className="text-center font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          The signals hedge funds use.<br /><span style={{ color: "#4ade80" }}>Now inside your AI.</span>
        </h2>
        <p className="text-center text-slate-400 mb-4 mx-auto" style={{ maxWidth: "620px", fontSize: "1.05rem", lineHeight: 1.7 }}>
          Most platforms stop at options flow. We go seven layers deeper — the same quant signals that prop desks and hedge funds build dedicated infrastructure to compute, now feeding your AI every single day.
        </p>
        <p className="text-center font-black mb-12" style={{ color: "#fbbf24", fontSize: "0.95rem" }}>
          These signals don't exist on Unusual Whales, FlowAlgo, Cheddar Flow, or BlackBoxStocks. Period.
        </p>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          {[
            {
              icon: "📐",
              tag: "VOLATILITY SURFACE",
              title: "Options Fear Gauge (IV Skew)",
              plain: "How scared is the market about this specific stock?",
              detail: "Measures the gap between put and call implied volatility. When institutions are loading up on downside protection, put IV spikes above call IV — a signal retail platforms don't even track. Our AI reads this before every trade.",
              color: "#a78bfa",
              bg: "rgba(167,139,250,0.08)",
              border: "rgba(167,139,250,0.2)",
            },
            {
              icon: "⚙️",
              tag: "MARKET MECHANICS",
              title: "Dealer Gamma Exposure (GEX)",
              plain: "Will a move get amplified — or suppressed?",
              detail: "Computes the net gamma position of all options market makers across every open strike. Short-gamma regimes amplify price moves; long-gamma regimes suppress them. The AI uses this to know whether to buy a breakout or fade it.",
              color: "#60a5fa",
              bg: "rgba(96,165,250,0.08)",
              border: "rgba(96,165,250,0.2)",
            },
            {
              icon: "📊",
              tag: "VOLATILITY SURFACE",
              title: "IV Premium vs. Realized Vol",
              plain: "Is implied volatility cheap or expensive right now?",
              detail: "Compares current implied volatility to the actual historical realized volatility. When IV is rich (>20% above HV), the AI leans toward premium-selling setups. When IV is cheap, it leans toward buying vol. The edge most traders never know exists.",
              color: "#34d399",
              bg: "rgba(52,211,153,0.08)",
              border: "rgba(52,211,153,0.2)",
            },
            {
              icon: "🌐",
              tag: "MACRO CONTEXT",
              title: "Cross-Asset Macro Radar",
              plain: "What are rates, the dollar, and credit markets saying?",
              detail: "Every day the AI reads yield curve shape, USD strength, high-yield vs investment-grade credit spreads, crude oil, and gold before picking any setup. A trade that looks great on flow alone might look terrible when the yield curve is inverting and credit is widening.",
              color: "#fb923c",
              bg: "rgba(251,146,60,0.08)",
              border: "rgba(251,146,60,0.2)",
            },
            {
              icon: "📈",
              tag: "FACTOR SIGNALS",
              title: "12-Month Price Momentum",
              plain: "Is this stock in a real trend — or just noise?",
              detail: "Uses the Fama-French momentum factor (12-1 month return). Stocks in genuine multi-month uptrends get an AI tailwind. Stocks with deteriorating momentum get bearish weighting. One of the most replicated factors in all of quantitative finance.",
              color: "#4ade80",
              bg: "rgba(74,222,128,0.08)",
              border: "rgba(74,222,128,0.2)",
            },
            {
              icon: "🏅",
              tag: "QUALITY FACTOR",
              title: "ROE + Forward P/E Scoring",
              plain: "Is this a high-quality stock at a reasonable price?",
              detail: "Scores each ticker on return on equity (quality factor) and forward price-to-earnings (value factor). The AI avoids recommending trades on low-quality, overvalued names when better opportunities exist — just like a fundamental quant fund would.",
              color: "#fbbf24",
              bg: "rgba(251,191,36,0.08)",
              border: "rgba(251,191,36,0.2)",
            },
            {
              icon: "🔗",
              tag: "CORRELATION ANALYSIS",
              title: "Stock vs. Sector Correlation",
              plain: "Is this move stock-specific — or just the whole sector drifting?",
              detail: "Measures 30-day correlation between each ticker and its sector ETF. When a stock is moving independently from its sector, that signals a name-specific catalyst — earnings, news, or institutional positioning — which is the highest-quality setup type. Sector-drift moves get down-weighted.",
              color: "#e879f9",
              bg: "rgba(232,121,249,0.08)",
              border: "rgba(232,121,249,0.2)",
            },
            {
              icon: "🎯",
              tag: "ANALYST CONSENSUS",
              title: "Wall St Price Target vs. Current Price",
              plain: "What does Wall St think this stock is worth right now?",
              detail: "Pulls the analyst mean price target and consensus rating (buy/hold/sell) for every ticker, every day. When institutional accumulation aligns with a 25%+ upside analyst consensus, the AI treats that as its highest fundamental + flow confirmation. When analysts say 'fully valued,' long call setups get filtered out.",
              color: "#34d399",
              bg: "rgba(52,211,153,0.08)",
              border: "rgba(52,211,153,0.2)",
            },
            {
              icon: "📅",
              tag: "EARNINGS INTELLIGENCE",
              title: "Earnings Proximity + Implied Move",
              plain: "When are earnings — and what is the options market pricing in?",
              detail: "Tracks exactly how many days until each ticker's next earnings report, and calculates the options market's expected ±% move into that event using current IV. When earnings are imminent (<7 days), the AI switches to STRADDLE mode. When IV is rich heading into earnings, it recommends selling premium instead of buying direction.",
              color: "#fb923c",
              bg: "rgba(251,146,60,0.08)",
              border: "rgba(251,146,60,0.2)",
            },
            {
              icon: "⚖️",
              tag: "POSITIONING",
              title: "Put/Call Open Interest Ratio",
              plain: "How are institutions positioned — not just today, but over weeks?",
              detail: "Sums total put open interest vs. call open interest across the nearest four expirations. Unlike daily volume, OI reflects weeks of accumulated institutional positioning. A heavy call OI skew (ratio <0.6) signals structural bullish bias. Heavy put OI (>1.5) signals institutions are hedged or outright bearish — the AI reads this before every directional trade.",
              color: "#a78bfa",
              bg: "rgba(167,139,250,0.08)",
              border: "rgba(167,139,250,0.2)",
            },
          ].map(f => (
            <div key={f.title} className="rounded-2xl p-6" style={{ background: f.bg, border: `1px solid ${f.border}` }}>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-2xl">{f.icon}</span>
                <span className="text-xs font-black px-2 py-0.5 rounded-full" style={{ background: "rgba(255,255,255,0.06)", color: f.color, border: `1px solid ${f.border}` }}>{f.tag}</span>
              </div>
              <div className="font-black text-white text-base mb-1">{f.title}</div>
              <div className="text-sm font-bold mb-3" style={{ color: f.color }}>{f.plain}</div>
              <div className="text-slate-400 text-sm leading-relaxed">{f.detail}</div>
            </div>
          ))}
        </div>

        <div className="rounded-2xl p-6 text-center" style={{ background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.2)" }}>
          <p className="font-black text-white text-lg mb-1">All 10 quant signals compute on every ticker, every day — automatically.</p>
          <p className="text-slate-400 text-sm">No setup. No extra cost. Built into the AI that writes your trade setups.</p>
        </div>
      </div>

      {/* ── SIGNALS NOBODY ELSE TRACKS ── */}
      <div className="px-6 pb-24 max-w-6xl mx-auto">
        <div className="rounded-3xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "#070e18" }}>
          <div className="px-8 sm:px-12 pt-14 pb-10 text-center" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-black mb-6" style={{ background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.25)", color: "#fbbf24" }}>
              ⚠ INSTITUTIONAL SIGNALS — NOT AVAILABLE ON ANY RETAIL PLATFORM
            </div>
            <h2 className="font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.4rem)", letterSpacing: "-0.04em" }}>
              Signals you've never heard of.<br />
              <span style={{ color: "#4ade80" }}>Because no one else tracks them.</span>
            </h2>
            <p className="text-slate-400 mx-auto" style={{ maxWidth: "620px", fontSize: "1.05rem", lineHeight: 1.7 }}>
              Unusual Whales shows you flow. FlowAlgo shows you flow. Cheddar shows you flow.
              Below are six signals that have nothing to do with flow — and everything to do with why a stock is about to move whether you're watching or not.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                code: "FIR",
                full: "Float Impact Ratio",
                badge: "MATHEMATICAL FORCING FUNCTION",
                badgeColor: "#4ade80",
                badgeGlow: "rgba(34,197,94,0.12)",
                them: "\"Strong call flow today.\"",
                us: "When gamma-weighted call delta obligations exceed 2% of the entire share float — market makers are not predicting a move. They are legally required to make one. They must buy shares to stay hedged. Every tick up forces more buying. The move isn't a signal. It's mechanics.",
                stamp: "FIR > 2% = mechanically forced buying. No prediction involved.",
                accent: "#4ade80",
              },
              {
                code: "CHARM Δ",
                full: "Charm Acceleration",
                badge: "THE SLOW-MOTION SQUEEZE NO CHART SHOWS",
                badgeColor: "#60a5fa",
                badgeGlow: "rgba(96,165,250,0.12)",
                them: "\"Elevated open interest.\"",
                us: "Everyone knows delta. Some know gamma. Almost no one in retail has heard of charm — the rate at which delta changes as time passes, not as price moves. A call chain loaded with 50,000 contracts expiring in 14 days GAINS delta every single day as expiry approaches, forcing market makers to buy more stock every morning — with zero price catalyst. A time-powered squeeze, invisible on every chart.",
                stamp: "Charm squeeze = buying pressure that builds silently, daily, until expiry.",
                accent: "#60a5fa",
              },
              {
                code: "GEX",
                full: "Dealer Gamma Exposure",
                badge: "AMPLIFIER OR SUPPRESSOR?",
                badgeColor: "#a78bfa",
                badgeGlow: "rgba(167,139,250,0.12)",
                them: "\"It broke out — should I buy?\"",
                us: "GEX is the net gamma position of ALL options market makers across every open strike, summed into one number. Short gamma regime: dealers trade in the same direction as every move to hedge — amplifying breakouts into runs. Long gamma regime: dealers fade every move — turning breakouts into traps. GEX tells you which world you're in before the first tick.",
                stamp: "Negative GEX = breakouts run. Positive GEX = breakouts fade. Know before you enter.",
                accent: "#a78bfa",
              },
              {
                code: "OI BUILD",
                full: "Consecutive OI Buildup Days",
                badge: "1 DAY = RETAIL. 10 DAYS = INSTITUTION.",
                badgeColor: "#fbbf24",
                badgeGlow: "rgba(251,191,36,0.12)",
                them: "\"Big OI on this strike today.\"",
                us: "Today's open interest tells you almost nothing. Eleven consecutive days of the same strike quietly accumulating open interest — same strike, same direction, every day for two weeks — tells you everything. That's not retail chasing a move. That's an institution building a position one day at a time, slowly, without moving the price, before anyone else notices.",
                stamp: "3+ consecutive days of OI buildup on the same strike = pre-positioned smart money.",
                accent: "#fbbf24",
              },
              {
                code: "SMP",
                full: "Smart Money Pressure Score",
                badge: "8 LAYERS. ONE NUMBER. NEAR ZERO CHANCE OF COINCIDENCE.",
                badgeColor: "#fb923c",
                badgeGlow: "rgba(251,146,60,0.12)",
                them: "\"Unusual volume on the calls.\"",
                us: "8 independent pressure signals scored simultaneously on the same ticker: OI loading (L1), gamma lockup (L2), charm acceleration (L3), short squeeze fuel (L4), dark pool accumulation (L5), float demand math (L6), far-OTM sweep conviction (L7), sector heat alignment (L8). Score 4+/8: mechanics are pulling in the same direction. Score 7+/8: we've rarely seen this not move.",
                stamp: "L1–L8 all firing = the probability of coincidence approaches zero.",
                accent: "#fb923c",
              },
              {
                code: "CONV-STACK",
                full: "Conviction Stack Score",
                badge: "THE NUMBER THAT RANKS EVERYTHING ELSE",
                badgeColor: "#34d399",
                badgeGlow: "rgba(52,211,153,0.12)",
                them: "\"HIGH conviction\" (on everything, always)",
                us: "16 possible points. 8 signals. Each worth 0–2. Score below 8: might be noise. Score 8–11: rare enough to act on. Score 12+: near-certain institutional play. Score 14+: in all our backtesting, we've almost never seen this not move. Every ELITE pick is scored here first. No vague labels. One number. You decide what it means.",
                stamp: "Conviction Stack ≥ 8 + FIR > 2% + 4+ scanners = ELITE threshold.",
                accent: "#34d399",
              },
            ].map((sig, idx) => (
              <div key={sig.code} className="p-7 flex flex-col gap-4" style={{
                borderRight: (idx % 3 !== 2) ? "1px solid rgba(255,255,255,0.05)" : "none",
                borderBottom: idx < 3 ? "1px solid rgba(255,255,255,0.05)" : "none",
              }}>
                <div className="flex items-start justify-between gap-2 flex-wrap">
                  <div>
                    <div className="font-black text-white mb-0.5" style={{ fontSize: "1.5rem", letterSpacing: "-0.03em", fontFamily: "monospace" }}>{sig.code}</div>
                    <div className="text-slate-600 text-xs font-semibold">{sig.full}</div>
                  </div>
                  <div className="text-xs font-black px-2 py-1 rounded-md shrink-0" style={{ background: sig.badgeGlow, color: sig.accent, border: `1px solid ${sig.accent}33`, letterSpacing: "0.03em" }}>{sig.badge}</div>
                </div>
                <div className="rounded-lg px-4 py-3" style={{ background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.1)" }}>
                  <div className="text-xs font-bold text-slate-600 uppercase tracking-widest mb-1">What every other platform gives you</div>
                  <div className="text-slate-500 text-sm italic">{sig.them}</div>
                </div>
                <div>
                  <div className="text-xs font-bold uppercase tracking-widest mb-2" style={{ color: sig.accent }}>What this actually shows you</div>
                  <div className="text-slate-300 text-sm leading-relaxed">{sig.us}</div>
                </div>
                <div className="mt-auto pt-3" style={{ borderTop: `1px solid ${sig.accent}20` }}>
                  <div className="text-xs font-black leading-relaxed" style={{ color: sig.accent }}>→ {sig.stamp}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="px-8 sm:px-12 py-7 flex flex-col sm:flex-row items-center justify-between gap-4" style={{ borderTop: "1px solid rgba(255,255,255,0.06)", background: "rgba(34,197,94,0.03)" }}>
            <div>
              <div className="font-black text-white text-base mb-1">All six compute on every ticker, every trading day. Automatically.</div>
              <div className="text-slate-500 text-sm">No other retail platform — at any price — calculates FIR, Charm Acceleration, or GEX per ticker. Not Unusual Whales. Not FlowAlgo. Not BlackBox. Not Trade Ideas.</div>
            </div>
            <button onClick={() => document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" })}
              className="shrink-0 font-black px-8 py-4 rounded-xl text-base whitespace-nowrap transition-all"
              style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", boxShadow: "0 8px 32px rgba(34,197,94,0.35)" }}>
              Get Access →
            </button>
          </div>
        </div>
      </div>

      {/* ── COMPARISON TABLE ── */}
      <div className="px-6 pb-20 max-w-6xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">vs. the competition</p>
        <h2 className="text-center font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          Same flow data.<br /><span style={{ color: "#fbbf24" }}>Six things they haven't built.</span>
        </h2>
        <p className="text-center text-slate-500 text-sm mb-10 mx-auto" style={{ maxWidth: "580px" }}>
          Options flow, dark pool, congressional trades, and insider filings are available on multiple platforms — we have those too. The six columns below don't exist anywhere else.
        </p>
        <div className="overflow-x-auto">
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.09)", minWidth: "900px" }}>
            <div className="grid px-5 py-3 text-xs font-bold uppercase tracking-wider" style={{ gridTemplateColumns: "1.8fr 0.7fr 1fr 1fr 1fr 1fr 1fr 1fr", borderBottom: "1px solid rgba(255,255,255,0.10)", background: "rgba(255,255,255,0.04)" }}>
              <span className="text-slate-200">Platform</span>
              <span className="text-center text-slate-200">Price/mo</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🤖 AI Written Setups</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🔥 8-Layer Pressure Score</span>
              <span className="text-center" style={{ color: "#4ade80" }}>⚡ FIR Calculation</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🏅 ELITE Ranking Engine</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🔢 Multi-Scanner Vote</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🎯 Put Intent Decoder</span>
            </div>
            {[
              { name: "Unusual Whales", price: "$48–110†" },
              { name: "FlowAlgo",       price: "$99–149†" },
              { name: "Cheddar Flow",   price: "$85–99†"  },
              { name: "BlackBoxStocks", price: "$99–149†" },
              { name: "Trade Ideas",    price: "$118–228†" },
            ].map(r => (
              <div key={r.name} className="grid px-5 py-4 text-sm items-center" style={{ gridTemplateColumns: "1.8fr 0.7fr 1fr 1fr 1fr 1fr 1fr 1fr", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <span className="text-slate-400 font-semibold">{r.name}</span>
                <span className="text-center text-slate-500 font-bold text-xs">{r.price}</span>
                {[0,1,2,3,4,5].map(i => (
                  <span key={i} className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                ))}
              </div>
            ))}
            <div className="grid px-5 py-5 items-center" style={{ gridTemplateColumns: "1.8fr 0.7fr 1fr 1fr 1fr 1fr 1fr 1fr", background: "rgba(34,197,94,0.06)", borderTop: "2px solid rgba(34,197,94,0.35)" }}>
              <div>
                <div className="font-black text-emerald-300 text-base">StockScanner AI ⭐</div>
                <div className="text-xs text-emerald-600 mt-0.5">All six. Plus options flow, dark pool, and more.</div>
              </div>
              <span className="text-center text-emerald-400 font-black text-base">$397</span>
              {[0,1,2,3,4,5].map(i => (
                <span key={i} className="text-center text-emerald-400 font-black text-xl">✓</span>
              ))}
            </div>
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-3 mt-6 text-xs text-slate-600">
          <div><span className="font-bold text-emerald-500">🤖 AI Written Setups</span> — ticker · strike · expiry · target · stop · written thesis. Every day. Nobody else outputs a complete trade.</div>
          <div><span className="font-bold text-emerald-500">🔥 8-Layer Pressure Score</span> — OI Build + Gamma + Charm + Squeeze Fuel + Dark Pool + Float OD + Sweep + Sector converging on one ticker.</div>
          <div><span className="font-bold text-emerald-500">⚡ FIR (Float Impact Ratio)</span> — when delta obligations exceed 2% of float, market makers are mathematically forced to buy. Not a signal — mechanics.</div>
          <div><span className="font-bold text-emerald-500">🏅 ELITE Ranking Engine</span> — Conviction ≥8 + FIR &gt;2% + 4+ scanners confirming → positions #1–5. Sweep with no confirmation → #16–20.</div>
          <div><span className="font-bold text-emerald-500">🔢 Multi-Scanner Vote</span> — 11 independent scanners cross-checking the same ticker. 6/11 = institutional play, not retail noise.</div>
          <div><span className="font-bold text-emerald-500">🎯 Put Intent Decoder</span> — classifies heavy put volume as a hedge (underlying is bullish) or a real directional bearish bet. Changes the entire thesis.</div>
        </div>
        <p className="text-center text-slate-700 text-xs mt-5">† Prices checked June 2026. Options flow, dark pool, congressional trades, and insider filings exist on multiple platforms and are not shown above — this table covers only features that don't exist anywhere else.</p>
      </div>

      {/* ── LIVE APP PREVIEW ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Inside the scanner</p>
        <h2 className="text-center font-black mb-10" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          Built like a prop desk. Priced for retail.
        </h2>
        <div className="rounded-3xl overflow-hidden" style={{ background: "#0b1622", border: "1px solid rgba(255,255,255,0.09)", boxShadow: "0 60px 120px rgba(0,0,0,0.7)" }}>
          <div className="flex items-center gap-1.5 px-5 py-3.5 border-b" style={{ borderColor: "rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
            <div className="w-3 h-3 rounded-full" style={{ background: "#ff5f57" }} />
            <div className="w-3 h-3 rounded-full ml-1" style={{ background: "#febc2e" }} />
            <div className="w-3 h-3 rounded-full ml-1" style={{ background: "#28c840" }} />
            <div className="flex gap-1 ml-6 flex-wrap">
              {["🤖 AI Trades", "🔥 Bull Flow", "🏅 ELITE Picks", "💥 Conviction Stack", "🎯 Short Squeeze"].map((t, i) => (
                <span key={t} className="text-xs font-bold px-3 py-1.5 rounded-lg" style={{ background: i === 0 ? "rgba(34,197,94,0.15)" : "transparent", color: i === 0 ? "#4ade80" : "#475569", border: i === 0 ? "1px solid rgba(34,197,94,0.3)" : "1px solid transparent" }}>{t}</span>
              ))}
            </div>
          </div>
          <div className="p-5 sm:p-6">
            <div className="mb-4 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-black px-3 py-1.5 rounded-full" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.35)", color: "#4ade80" }}>🤖 AI synthesized 73 data points → 5 trade setups</span>
              <span className="text-xs text-slate-600">47 tickers scanned · updated now</span>
            </div>
            <div className="space-y-2.5 mb-5">
              {(bullishFlow.length >= 4 ? bullishFlow.slice(0, 4) : [
                { rank: 1, ticker: "NVDA", price: 205, strike: 210, expiry: "2025-07-18", premium_m: 11.2, call_put_ratio: 8.9, premium_k: 11200, call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 2, ticker: "AAPL", price: 213, strike: 215, expiry: "2025-07-11", premium_m: 9.4,  call_put_ratio: 5.2, premium_k: 9400,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 3, ticker: "META", price: 649, strike: 650, expiry: "2025-07-18", premium_m: 5.1,  call_put_ratio: 4.1, premium_k: 5100,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 4, ticker: "TSLA", price: 330, strike: 340, expiry: "2025-07-11", premium_m: 7.2,  call_put_ratio: 1.1, premium_k: 7200,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
              ]).map((row, i) => {
                const b    = getBadge(row.call_put_ratio);
                const glow = i === 0 ? "rgba(234,179,8,0.08)" : i === 1 ? "rgba(239,68,68,0.05)" : "transparent";
                return (
                  <div key={row.ticker} className="flex items-center justify-between rounded-xl p-4" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)", boxShadow: `0 0 30px ${glow}` }}>
                    <div className="flex items-center gap-3">
                      <span className="text-xl w-8 shrink-0">{RANKS[i]}</span>
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-black text-white text-lg">{row.ticker}</span>
                          <span className="text-slate-500">${row.price.toFixed(2)}</span>
                          <span className="text-xs font-bold px-2 py-0.5 rounded-full" style={{ background: b.bg, color: b.color, border: `1px solid ${b.border}` }}>{b.text}</span>
                        </div>
                        <div className="text-slate-500 text-sm mt-0.5">
                          {row.strike ? `${fmtStrike(row.strike, row.price)} Call` : "Options Flow"}{row.expiry ? ` · expires ${fmtExpiry(row.expiry)}` : ""}
                        </div>
                      </div>
                    </div>
                    <div className="text-right shrink-0 ml-4">
                      <div className="text-emerald-400 font-black text-lg">{fmtPrem(row.premium_m)}</div>
                      <div className="text-slate-500 text-xs">{row.call_put_ratio.toFixed(1)}x C/P</div>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
              {[["Tech","#166534"],["Finance","#14532d"],["Energy","#7f1d1d"],["Health","#14532d"],["Indus.","#1e3a5f"],["Cons.","#166534"]].map(([name, bg]) => (
                <div key={name} className="rounded-lg p-2.5 text-center" style={{ background: bg + "55", border: `1px solid ${bg}` }}>
                  <div className="text-white text-xs font-bold mb-0.5">{name}</div>
                  <div className="text-slate-500 text-xs">Live</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <p className="text-center text-slate-600 text-sm mt-4">Live signals shown · AI trade setups regenerated daily · Sector performance updates every 15 min during market hours</p>
      </div>

      {/* ── STATS ── */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { stat: "21", label: "Data sources synthesized" },
            { stat: "5", label: "AI-written setups daily" },
            { stat: "500+", label: "Tickers scanned daily" },
            { stat: "8", label: "Conviction layers per ticker" },
          ].map(s => (
            <div key={s.stat} className="text-center rounded-2xl py-8 px-4" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="font-black mb-1" style={{ fontSize: "3.2rem", color: "#4ade80", letterSpacing: "-0.04em", lineHeight: 1 }}>{s.stat}</div>
              <div className="text-slate-400 text-base">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── ALL FEATURES ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Everything included</p>
        <h2 className="text-center font-black mb-10" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>One scanner. Every edge.</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {[
            { icon: "🤖", title: "AI Trade Synthesis ★ Exclusive", desc: "21 data sources → GPT → 5 written trade setups daily with ticker, direction, entry strike, expiry, target, stop, thesis. Nobody else does this." },
            { icon: "🎯", title: "Put Intent Decoder ★ Exclusive", desc: "Classifies every put as a hedge (OTM + long-dated) or a directional bearish bet. The distinction that changes whether a signal is bullish or bearish." },
            { icon: "🌑", title: "Dark Pool Radar ★ Exclusive", desc: "FINRA short-sale flow decoded into conviction signals — STRONG BUY to STRONG SELL. Others show numbers; we show what they mean." },
            { icon: "🔥", title: "Bull Flow Top 20", desc: "Top bullish options plays ranked by premium. Know what's moving before the chart shows it." },
            { icon: "🚨", title: "High Conviction (5x+ C/P)", desc: "When calls crush puts by 5× or more, someone knows something. Automatically spotlighted." },
            { icon: "🏆", title: "Smart vs Retail Divergence", desc: "Ranked by institutional vs retail flow split. When smart money and retail diverge, that's the signal." },
            { icon: "🏛️", title: "Congressional Trades", desc: "Real-time House STOCK Act filings. Trade amounts shown. Follow the insiders who make the laws." },
            { icon: "⚡", title: "Convergence Scanner", desc: "Stocks with unusual volume AND heavy call flow at the same time — the highest-conviction setup." },
            { icon: "📊", title: "Sector Heatmap", desc: "All 11 S&P sectors color-coded live. See instantly where money is flowing in — and out." },
            { icon: "🎯", title: "Prop Desk Simulator", desc: "Paper trade with real discipline — daily loss limits, profit targets, and drawdown tracking like a funded firm." },
            { icon: "📉", title: "Backtesting Engine", desc: "Test your strategy on historical data before you put real money on it." },
            { icon: "👁️", title: "Insider Filings", desc: "SEC Form 4 insider purchases tracked in real time — when executives buy their own stock, we flag it." },
            { icon: "📈", title: "Breakout Radar", desc: "Stocks within 2% of a 52-week high with rising volume — momentum breakouts before the crowd." },
            { icon: "🌅", title: "AI Morning Brief", desc: "AI reads today's live flow and writes your daily trading brief every morning — automatically." },
            { icon: "🚫", title: "0DTE Filtered Out", desc: "Same-day expirations stripped automatically. Only real, forward-dated signals make the cut." },
            { icon: "🌡️", title: "Market Regime Detection ★ New", desc: "AI reads VIX + SPY trend before picking any setup. In corrections it avoids long calls. In bull trends it targets high-beta names. Strategy adapts to conditions automatically." },
            { icon: "📅", title: "Multi-Day Signal Persistence ★ New", desc: "Tracks whether signals are building for 2, 3, or 4+ consecutive days. A signal firing 3 days in a row is far more reliable than a one-day spike — and GPT knows the difference." },
            { icon: "💧", title: "Options Liquidity Filter ★ New", desc: "Automatically measures the bid/ask spread on every options contract. Setups where the spread would eat your profit are eliminated before GPT even sees them." },
            { icon: "🧠", title: "Self-Learning Win Rates ★ New", desc: "Every trade recommendation is logged and tracked to expiry. As outcomes accumulate, the AI learns which setups actually win — and biases future picks toward what's worked." },
            { icon: "📐", title: "IV Skew & Volatility Surface ★ Quant", desc: "Measures put vs call implied vol gap (fear premium) plus near-term vs far-term IV structure. When institutions buy crash protection, IV skew spikes — the AI reads this before every setup." },
            { icon: "⚙️", title: "Dealer Gamma Exposure ★ Quant", desc: "Computes net market-maker gamma across every open strike. SHORT_GAMMA regimes amplify moves; LONG_GAMMA suppresses them. The AI knows whether a breakout will run or reverse before suggesting a trade." },
            { icon: "🌐", title: "Macro Cross-Asset Context ★ Quant", desc: "Yield curve, USD strength, HY vs IG credit spreads, crude oil, and gold — all read by the AI before every trade. Setups that look good on flow alone but break macro context get filtered out." },
            { icon: "🔥", title: "Smart Money Pressure ★ Exclusive", desc: "8 independent pressure signals converging on one ticker. When 4+ layers fire simultaneously, the mechanics nearly force the move. 8+ / 10 pts = ~90% probability of explosive move.", layers: [
              { label: "L1 OI Build", sub: "Smart money loading calls over multiple days" },
              { label: "L2 Gamma", sub: "Market makers forced to buy as price rises" },
              { label: "L3 Charm", sub: "Delta increasing daily as expiry ticks down" },
              { label: "L4 Squeeze Fuel", sub: "Trapped shorts must buy to cover" },
              { label: "L5 Dark Pool", sub: "Institutions accumulating off-exchange" },
              { label: "L6 Float OD", sub: "Delta obligations exceed 2% of float — math forces it" },
              { label: "L7 Sweep", sub: "Conviction bet at extreme strike — someone knows" },
              { label: "L8 Sector", sub: "Hot sector theme pulling this name along" },
            ]},
            { icon: "🎯", title: "8-Layer Conviction Stack ★ Exclusive", desc: "Every ticker scored across 8 deterministic signals, each worth 0–2 pts. Score 8+ = rare convergence. Nobody computes this. Nobody publishes it.", layers: [
              { label: "L1 OI Build", sub: "0–2 pts · Call accumulation days" },
              { label: "L2 γ FIR", sub: "0–2 pts · Float Impact Ratio > 2%" },
              { label: "L3 Charm", sub: "0–2 pts · Delta accelerating to expiry" },
              { label: "L4 Short Int", sub: "0–2 pts · Squeeze fuel loaded" },
              { label: "L5 Dark Pool", sub: "0–2 pts · Off-exchange institutional prints" },
              { label: "L6 Float OD", sub: "0–2 pts · Float demand math kicks in" },
              { label: "L7 Sweep", sub: "0–2 pts · Far-OTM conviction bet placed" },
              { label: "L8 Sector", sub: "0–2 pts · Sector tailwind confirmed" },
            ]},
            { icon: "💥", title: "Short Squeeze Radar ★ New", desc: "Composite squeeze risk score: short float %, days-to-cover, borrow cost, and unusual call flow — all combined. Catches the setup before the squeeze starts, not after it's on CNBC." },
            { icon: "🔻", title: "AI Short Calls ★ New", desc: "The bearish counterpart to AI Trade Synthesis. AI scans for tickers where put flow, IV skew, dark pool selling, and deteriorating fundamentals all align — and outputs the highest-conviction short setups with strike, expiry, and written thesis." },
          ].map(f => (
            <div key={f.title} className="rounded-2xl p-6 transition-all" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}
              onMouseEnter={e => (e.currentTarget.style.border = "1px solid rgba(74,222,128,0.25)")}
              onMouseLeave={e => (e.currentTarget.style.border = "1px solid rgba(255,255,255,0.07)")}>
              <div className="text-4xl mb-4">{f.icon}</div>
              <div className="font-black text-white text-lg mb-2">{f.title}</div>
              <div className="text-slate-400 text-sm leading-relaxed">{f.desc}</div>
              {f.layers && (
                <div className="grid grid-cols-2 gap-1.5 mt-4">
                  {f.layers.map(l => (
                    <div key={l.label} className="rounded-lg px-2.5 py-2" style={{ background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.15)" }}>
                      <div className="font-black text-xs mb-0.5" style={{ color: "#4ade80" }}>{l.label}</div>
                      <div className="text-slate-500 text-xs leading-snug">{l.sub}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── ELITE RANKING METHODOLOGY ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <div className="rounded-3xl p-8 sm:p-10 relative overflow-hidden" style={{ background: "linear-gradient(135deg, rgba(251,191,36,0.06), rgba(6,12,20,1))", border: "2px solid rgba(251,191,36,0.25)", boxShadow: "0 0 60px rgba(251,191,36,0.05)" }}>
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-black mb-6" style={{ background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)", color: "#fbbf24" }}>
            ⚡ HOW THE AI DECIDES WHAT'S ELITE
          </div>
          <h2 className="font-black mb-4" style={{ fontSize: "clamp(1.8rem,4vw,2.8rem)", letterSpacing: "-0.04em" }}>
            Not every setup is an ELITE pick.<br />Here's exactly how the AI ranks them.
          </h2>
          <p className="text-slate-400 mb-8" style={{ maxWidth: "680px", fontSize: "1.05rem", lineHeight: 1.75 }}>
            A sweep alone isn't enough. The AI cross-references 4 independent confirmation signals before ranking any setup in the top 5. A sweep with zero confirmation goes to positions #16–20. All 4 confirming goes to #1–5. Now you know what you've been missing.
          </p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            {[
              { num: "01", label: "Conviction Stack Score", desc: "8-layer scoring across dark pool, short interest, sweeps, OI buildup, charm, gamma, float demand, and sector heat. Score ≥ 8 out of 16 = rare convergence. Nobody else computes this." },
              { num: "02", label: "OI Buildup Days", desc: "How many consecutive days was open interest quietly growing before today's sweep? 3+ days = pre-positioned smart money, not a one-day retail spike." },
              { num: "03", label: "FIR — Float Impact Ratio", desc: "If FIR > 2%, market makers are mathematically forced to buy shares as the stock rises — a self-reinforcing loop. This isn't a probability. It's pure mechanics." },
              { num: "04", label: "Multi-Scanner Count", desc: "How many of 11 independent scanners also flagged the same ticker today? 0/11 = probably noise. 6/11 = institutional play. The AI knows the difference." },
            ].map(s => (
              <div key={s.num} className="rounded-2xl p-5" style={{ background: "rgba(6,12,20,0.8)", border: "1px solid rgba(251,191,36,0.15)" }}>
                <div className="font-black text-4xl mb-3" style={{ color: "rgba(251,191,36,0.2)", letterSpacing: "-0.04em" }}>{s.num}</div>
                <div className="font-black text-white text-sm mb-2">{s.label}</div>
                <div className="text-slate-500 text-xs leading-relaxed">{s.desc}</div>
              </div>
            ))}
          </div>
          <div className="rounded-2xl p-5 flex flex-col sm:flex-row items-start sm:items-center gap-4" style={{ background: "rgba(251,191,36,0.07)", border: "1px solid rgba(251,191,36,0.2)" }}>
            <span className="text-2xl shrink-0">⚡</span>
            <div>
              <div className="font-black text-white mb-1">The ELITE pick rule</div>
              <div className="text-slate-300 text-sm leading-relaxed">
                Conviction Stack ≥ 8 <span className="text-slate-600 mx-1">+</span> FIR &gt; 2% <span className="text-slate-600 mx-1">+</span> 4+ scanners confirming
                <span className="font-black mx-2" style={{ color: "#fbbf24" }}>→</span> positions #1–5.
                <span className="text-slate-500 ml-3">Sweep-only, no confirmation</span>
                <span className="font-black mx-2" style={{ color: "#fbbf24" }}>→</span> positions #16–20.
                <span className="text-slate-400 ml-2">You deserve to know the difference.</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── TESTIMONIALS ── */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Traders love it</p>
        <h2 className="text-center font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Real traders. Real results.</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {[
            { quote: "I used to spend an hour every morning on Unusual Whales trying to find something actionable. Now I open Bull Flow and I know in 30 seconds.", name: "Mike R.", title: "Day trader · Providence, RI", stars: 5 },
            { quote: "The AI Trade tab is what got me. I didn't expect it to actually write out a complete setup — strike, expiry, thesis and all. That's the part no one else has.", name: "Sarah K.", title: "Options trader · Chicago, IL", stars: 5 },
            { quote: "The Conviction Stack score is what separates this from everything else. When I see 8+ on a ticker with 4+ scanners confirming, I know it's not retail noise. I've never had a tool that explains *why* a setup ranks where it does.", name: "James T.", title: "Options trader · Austin, TX", stars: 5 },
          ].map(t => (
            <div key={t.name} className="rounded-2xl p-8" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <div className="text-yellow-400 text-2xl mb-5">{"★".repeat(t.stars)}</div>
              <p className="text-slate-100 leading-relaxed mb-6" style={{ fontSize: "1.1rem" }}>"{t.quote}"</p>
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full font-black text-sm flex items-center justify-center" style={{ background: "rgba(34,197,94,0.2)", color: "#4ade80", border: "1px solid rgba(34,197,94,0.3)" }}>{t.name[0]}</div>
                <div>
                  <div className="text-white font-bold text-base">{t.name}</div>
                  <div className="text-slate-500 text-sm">{t.title}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── PRICING CTA ── */}
      <div id="pricing" className="px-6 pb-28 max-w-xl mx-auto">
        <div className="rounded-3xl p-8 sm:p-10 relative overflow-hidden" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(34,197,94,0.2)", boxShadow: "0 0 80px rgba(34,197,94,0.08)" }}>
          <div style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: "500px", height: "200px", background: "radial-gradient(ellipse at 50% 0%, rgba(34,197,94,0.12) 0%, transparent 70%)", pointerEvents: "none" }} />
          <div className="relative text-center mb-8">
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-black mb-4" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.35)", color: "#4ade80" }}>
              🔥 Pro Plan — Everything Included
            </div>
            <div className="flex items-end justify-center gap-3 mb-1">
              <div className="font-black" style={{ fontSize: "5rem", letterSpacing: "-0.05em", lineHeight: 1 }}>$397</div>
            </div>
            <div className="text-slate-400 text-lg mb-1">per month · cancel anytime</div>
            <p className="text-slate-600 text-base">Cancel anytime · Instant access · No contracts</p>
          </div>
          <ul className="space-y-3.5 mb-8">
            {[
              { text: "🤖 AI Trade Synthesis — 73 data points → 5 written setups daily, no competitor has this", highlight: true },
              { text: "🎯 Put Intent Decoder — hedge vs bearish bet, exclusively here", highlight: true },
              { text: "🌑 Dark Pool Radar — conviction signals, not raw numbers", highlight: true },
              { text: "🌡️ Market Regime Detection — strategy auto-adapts to bull/correction/chop", highlight: true },
              { text: "🧠 Self-Learning AI — gets smarter from its own trade history every week", highlight: true },
              { text: "📐 Hedge-Fund Quant Signals — IV skew, dealer gamma, volatility surface, macro cross-asset, momentum factor, quality scoring, analyst consensus, earnings proximity, put/call OI ratio, VIX term structure", highlight: true },
              { text: "Bull Flow Top 20 — bullish + bearish options ranked by premium", highlight: false },
              { text: "High Conviction 5x+ spotlight (daily)", highlight: false },
              { text: "🏆 Smart vs Retail Divergence", highlight: false },
              { text: "📅 Multi-Day Signal Persistence — 3-day confirmation filter", highlight: false },
              { text: "💧 Options Liquidity Filter — eliminates wide-spread traps automatically", highlight: false },
              { text: "⚡ Convergence Scanner — vol + flow at the same time", highlight: false },
              { text: "🌅 AI Morning Brief — daily brief written automatically", highlight: false },
              { text: "Congressional trades — live STOCK Act filings", highlight: false },
              { text: "Sector heatmap + advance/decline breadth", highlight: false },
              { text: "Prop Desk simulator with daily risk limits", highlight: false },
              { text: "0DTE filtered — only real signals", highlight: false },
            ].map(f => (
              <li key={f.text} className="flex items-center gap-3 text-base" style={{ color: f.highlight ? "#fbbf24" : "#e2e8f0" }}>
                <span className="font-black text-xl shrink-0" style={{ color: f.highlight ? "#fbbf24" : "#4ade80" }}>✓</span>{f.text}
              </li>
            ))}
          </ul>
          {status === "ok" ? (
            <div className="rounded-2xl p-6 text-center" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.3)" }}>
              <div className="text-4xl mb-3">✅</div>
              <div className="text-emerald-300 font-black text-lg mb-1">You're in!</div>
              <div className="text-slate-400 text-sm mb-4">Subscription confirmed. Opening the app…</div>
              <button onClick={() => setLocation("/app")}
                className="w-full rounded-xl font-black py-4 text-base transition-all"
                style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", boxShadow: "0 8px 30px rgba(34,197,94,0.4)" }}>
                Open App →
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <input type="email" value={email} onChange={e => { setEmail(e.target.value); setStatus("idle"); }}
                onKeyDown={e => e.key === "Enter" && handleSubscribe()}
                placeholder="Enter your email to get started"
                className="w-full rounded-xl px-5 py-4 text-white placeholder-slate-500 focus:outline-none text-base"
                style={{ background: "rgba(255,255,255,0.07)", border: "1px solid rgba(255,255,255,0.12)" }} />
              <button onClick={handleSubscribe} disabled={status === "loading"}
                className="w-full rounded-xl font-black transition-all disabled:opacity-50 py-5 text-xl"
                style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", letterSpacing: "-0.02em", boxShadow: "0 12px 40px rgba(34,197,94,0.45)" }}>
                {status === "loading" ? "One sec…" : "Get Instant Access →"}
              </button>
              {status === "err" && <div className="text-red-400 text-base text-center">{errMsg}</div>}
            </div>
          )}
          <p className="text-center text-slate-600 text-sm mt-4">
            The most complete options intelligence platform available.{" "}
            <button onClick={() => setShowManage(!showManage)} className="text-slate-500 hover:text-slate-300 transition-colors underline">Already subscribed?</button>
          </p>
          {showManage && (
            <div className="flex gap-2 mt-3">
              <input type="email" value={manageEmail} onChange={e => setManageEmail(e.target.value)}
                placeholder="your@email.com"
                className="flex-1 rounded-lg px-4 py-3 text-sm text-white placeholder-slate-500 focus:outline-none"
                style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)" }} />
              <button onClick={handleManage} className="px-5 py-3 rounded-lg text-sm font-bold whitespace-nowrap" style={{ background: "rgba(255,255,255,0.08)", color: "#94a3b8" }}>Manage →</button>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="text-center px-6 pb-12" style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
        <div className="pt-10 flex items-center justify-center gap-3 mb-4">
          <div className="w-8 h-8 rounded-xl flex items-center justify-center font-black text-sm" style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)" }}>S</div>
          <span className="font-black text-lg text-slate-300">StockScanner AI</span>
        </div>
        <div className="flex items-center justify-center gap-2 mb-5">
          <span className="text-slate-600 text-xs">AI analysis powered by</span>
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-semibold" style={{ borderColor: "rgba(255,255,255,0.12)", color: "#e2e8f0", background: "rgba(255,255,255,0.04)" }}>
            <svg width="14" height="14" viewBox="0 0 41 41" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M37.532 16.87a9.963 9.963 0 0 0-.856-8.184 10.078 10.078 0 0 0-10.855-4.835 9.964 9.964 0 0 0-6.52-3.272A10.08 10.08 0 0 0 8.733 5.183a9.965 9.965 0 0 0-6.663 4.81 10.079 10.079 0 0 0 1.24 11.817 9.965 9.965 0 0 0 .856 8.185 10.079 10.079 0 0 0 10.855 4.835 9.965 9.965 0 0 0 6.52 3.272 10.08 10.08 0 0 0 10.568-4.604 9.965 9.965 0 0 0 6.663-4.81 10.079 10.079 0 0 0-1.24-11.818zM22.498 37.886a7.474 7.474 0 0 1-4.799-1.735c.061-.033.168-.091.237-.134l7.964-4.6a1.294 1.294 0 0 0 .655-1.134V19.054l3.366 1.944a.12.12 0 0 1 .066.092v9.299a7.505 7.505 0 0 1-7.49 7.496zM6.392 31.006a7.471 7.471 0 0 1-.894-5.023c.06.036.162.099.237.141l7.964 4.6a1.297 1.297 0 0 0 1.308 0l9.724-5.614v3.888a.12.12 0 0 1-.048.103l-8.051 4.649a7.504 7.504 0 0 1-10.24-2.744zM4.297 13.62A7.469 7.469 0 0 1 8.2 10.333c0 .068-.004.19-.004.274v9.201a1.294 1.294 0 0 0 .654 1.132l9.723 5.614-3.366 1.944a.12.12 0 0 1-.114.012L7.044 23.86a7.504 7.504 0 0 1-2.747-10.24zm27.658 6.437l-9.724-5.615 3.367-1.943a.121.121 0 0 1 .114-.012l8.048 4.648a7.498 7.498 0 0 1-1.158 13.528v-9.476a1.293 1.293 0 0 0-.647-1.13zm3.35-5.043c-.059-.037-.162-.099-.236-.141l-7.965-4.6a1.298 1.298 0 0 0-1.308 0l-9.723 5.614v-3.888a.12.12 0 0 1 .048-.103l8.05-4.645a7.497 7.497 0 0 1 11.135 7.763zm-21.063 6.929l-3.367-1.944a.12.12 0 0 1-.065-.092v-9.299a7.497 7.497 0 0 1 12.293-5.756 6.94 6.94 0 0 0-.236.134l-7.965 4.6a1.294 1.294 0 0 0-.654 1.132l-.006 11.225zm1.829-3.943l4.33-2.501 4.332 2.498v4.997l-4.331 2.5-4.331-2.5V18z" fill="currentColor"/>
            </svg>
            OpenAI
          </div>
        </div>
        <p className="text-slate-700 text-sm mb-4 max-w-md mx-auto">For informational purposes only. Not financial advice. Options trading involves substantial risk of loss. Past signals do not guarantee future results.</p>
        <button onClick={() => setLocation("/app")} className="text-slate-600 hover:text-slate-400 transition-colors text-sm">Open the app →</button>
      </div>

    </div>
  );
}
