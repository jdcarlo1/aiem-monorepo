import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { createStockScannerCheckout, manageStockScannerSubscription, fetchBullFlow, BullFlowRow } from "@/lib/api";

// ── helpers ────────────────────────────────────────────────────────────────
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
  const label = strike > price ? "C" : "C";
  return `$${strike.toFixed(0)}${label}`;
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

  // Fetch live bull flow for landing page widgets
  useEffect(() => {
    fetchBullFlow().then(d => setLiveFlow(d.results ?? [])).catch(() => {});
  }, []);

  // Only bullish signals (calls significantly outpacing puts) — C/P ≥ 2
  const bullishFlow = liveFlow.filter(r => r.call_put_ratio >= 2);

  // Build ticker tape from bullish-only live data
  const tickerSignals: string[] = bullishFlow.length >= 2
    ? bullishFlow.slice(0, 8).map(r => {
        const b = getBadge(r.call_put_ratio);
        return `${b.text.split(" ")[0]} ${r.ticker} ${fmtStrike(r.strike, r.price)} ${fmtExpiry(r.expiry)} · ${fmtPrem(r.premium_m)} · ${r.call_put_ratio.toFixed(1)}x C/P`;
      })
    : [
        "⏳ Fetching live signals…",
        "⏳ Fetching live signals…",
        "⏳ Fetching live signals…",
      ];

  useEffect(() => {
    const id = setInterval(() => setTickerPos(p => p + 1), 40);
    return () => clearInterval(id);
  }, []);

  const handleSubscribe = async () => {
    if (!email.trim() || !email.includes("@")) { setErrMsg("Enter a valid email"); setStatus("err"); return; }
    setStatus("loading");
    try {
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

      {/* Hero */}
      <div className="relative text-center overflow-hidden" style={{ padding: "100px 24px 80px" }}>
        {/* Radial glow */}
        <div style={{ position: "absolute", top: "0", left: "50%", transform: "translateX(-50%)", width: "900px", height: "600px", background: "radial-gradient(ellipse at 50% 0%, rgba(34,197,94,0.18) 0%, transparent 70%)", pointerEvents: "none" }} />

        <div className="relative max-w-5xl mx-auto">
          <div className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-bold mb-10" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.3)", color: "#4ade80" }}>
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse inline-block" />
            Scanning the market right now — live data every 15 min
          </div>

          <h1 className="font-black leading-none mb-8" style={{ fontSize: "clamp(3.8rem,10vw,8rem)", letterSpacing: "-0.055em", lineHeight: 0.92 }}>
            Stop missing<br />
            <span style={{ color: "#4ade80", textShadow: "0 0 160px rgba(74,222,128,0.5)" }}>the big moves.</span>
          </h1>

          <p className="mx-auto mb-12 text-slate-300" style={{ fontSize: "clamp(1.15rem,2.5vw,1.45rem)", maxWidth: "600px", lineHeight: 1.75 }}>
            Hedge funds and insiders move millions before you even know a ticker is moving. <strong className="text-white">StockScanner AI shows you exactly where the smart money is going</strong> — before the chart makes it obvious.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-6">
            <button onClick={() => document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" })}
              className="font-black px-12 py-5 rounded-2xl transition-all text-xl"
              style={{ background: "linear-gradient(135deg,#15803d,#22c55e)", color: "#fff", boxShadow: "0 16px 56px rgba(34,197,94,0.5)", letterSpacing: "-0.02em" }}>
              Get Instant Access — $39/mo
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

      {/* TODAY'S TOP SIGNAL — FOMO Card */}
      <div className="px-6 pb-20 max-w-3xl mx-auto">
        {(() => {
          // Only show a genuinely bullish top signal (C/P ≥ 2) on the conviction card
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
                <p className="text-slate-400 text-sm italic">🔒 <strong className="text-white">Subscribers see signals like this in real time.</strong> Are you still scrolling Twitter to find trades?</p>
              </div>
            </div>
          );
        })()}
      </div>

      {/* Pain Section */}
      <div className="px-6 pb-20 max-w-4xl mx-auto text-center">
        <p className="text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Sound familiar?</p>
        <h2 className="font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          You're always a step behind.<br /><span className="text-slate-500">Not anymore.</span>
        </h2>
        <div className="grid sm:grid-cols-3 gap-4 text-left">
          {[
            { before: "You find out about a big move after it already happened.", after: "See $11M+ call bets the moment they hit.", icon: "📉" },
            { before: "You scroll Twitter/Reddit for hours looking for ideas.", after: "One dashboard. Top 20 signals ranked by size.", icon: "🕐" },
            { before: "You have no idea what sectors are actually leading.", after: "11-sector heatmap + advance/decline breadth, live.", icon: "🗺️" },
          ].map(p => (
            <div key={p.before} className="rounded-2xl p-6" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-3xl mb-4">{p.icon}</div>
              <div className="text-slate-500 text-sm line-through mb-3 leading-relaxed">"{p.before}"</div>
              <div className="text-emerald-300 font-bold text-base leading-relaxed">→ {p.after}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Live App Preview */}
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
              {["🔥 Bull Flow", "🏆 Smart Money", "🏛️ Congress", "📊 Market", "🎯 Prop Desk"].map((t, i) => (
                <span key={t} className="text-xs font-bold px-3 py-1.5 rounded-lg" style={{ background: i === 0 ? "rgba(34,197,94,0.15)" : "transparent", color: i === 0 ? "#4ade80" : "#475569", border: i === 0 ? "1px solid rgba(34,197,94,0.3)" : "1px solid transparent" }}>{t}</span>
              ))}
            </div>
          </div>
          <div className="p-5 sm:p-6">
            <div className="mb-4">
              <span className="text-xs font-black px-3 py-1.5 rounded-full" style={{ background: "rgba(234,179,8,0.12)", border: "1px solid rgba(234,179,8,0.35)", color: "#fbbf24" }}>🚨 HIGH CONVICTION — SOMEBODY KNOWS SOMETHING (5x+ C/P)</span>
            </div>
            <div className="space-y-2.5 mb-5">
              {(bullishFlow.length >= 4 ? bullishFlow.slice(0, 4) : [
                { rank: 1, ticker: "AAPL", price: 307, strike: null, expiry: null, premium_m: 11.2, call_put_ratio: 8.9, premium_k: 11200, call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 2, ticker: "NVDA", price: 135, strike: null, expiry: null, premium_m: 9.4,  call_put_ratio: 1.7, premium_k: 9400,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 3, ticker: "META", price: 650, strike: null, expiry: null, premium_m: 5.1,  call_put_ratio: 4.1, premium_k: 5100,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
                { rank: 4, ticker: "TSLA", price: 330, strike: null, expiry: null, premium_m: 7.2,  call_put_ratio: 3.8, premium_k: 7200,  call_vol_oi: 0, total_call_vol: 0, days_to_earnings: null, short_float_pct: null } as BullFlowRow,
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
        <p className="text-center text-slate-600 text-sm mt-4">Live signals shown · Sector performance updates every 15 min during market hours</p>
      </div>

      {/* Stats */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { stat: "47+", label: "Tickers scanned" },
            { stat: "11", label: "Sectors tracked" },
            { stat: "4×", label: "Daily scans" },
            { stat: "$39", label: "Limited time · cancel any time" },
          ].map(s => (
            <div key={s.stat} className="text-center rounded-2xl py-8 px-4" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="font-black mb-1" style={{ fontSize: "3.2rem", color: "#4ade80", letterSpacing: "-0.04em", lineHeight: 1 }}>{s.stat}</div>
              <div className="text-slate-400 text-base">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Features */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Everything included</p>
        <h2 className="text-center font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>One scanner. Every edge.</h2>
        <p className="text-center text-slate-500 mb-10 mx-auto" style={{ maxWidth: "540px", fontSize: "1rem", lineHeight: 1.6 }}>Including 4 new AI features just added — none of which competitors offer at any price.</p>

        {/* NEW — AI features callout strip */}
        <div className="rounded-2xl p-5 mb-8" style={{ background: "linear-gradient(135deg, rgba(34,197,94,0.06), rgba(16,163,74,0.03))", border: "1px solid rgba(34,197,94,0.25)" }}>
          <div className="text-center text-xs font-black uppercase tracking-widest mb-4" style={{ color: "#4ade80" }}>✨ New — AI-Powered Features</div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { icon: "🌅", title: "AI Morning Brief", desc: "Claude reads today's unusual flow and writes your daily trading brief every morning — automatically.", tag: "NEW" },
              { icon: "⚡", title: "Convergence Scanner", desc: "Stocks with BOTH unusual volume AND heavy call flow at the same time — the highest-conviction setup.", tag: "NEW" },
              { icon: "🔍", title: "Pre-Market Flow", desc: "Biggest movers before the open with volume spike flags. Never get blindsided by a gap again.", tag: "NEW" },
              { icon: "💡", title: "AI Catalyst", desc: "Ask Claude \"why is this moving?\" on any ticker — get a Bloomberg-style thesis in seconds.", tag: "NEW" },
            ].map(f => (
              <div key={f.title} className="rounded-xl p-4" style={{ background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.15)" }}>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-2xl">{f.icon}</span>
                  <span className="text-xs font-black px-2 py-0.5 rounded-full" style={{ background: "rgba(34,197,94,0.2)", color: "#4ade80", border: "1px solid rgba(34,197,94,0.35)" }}>{f.tag}</span>
                </div>
                <div className="font-black text-white text-sm mb-1">{f.title}</div>
                <div className="text-slate-400 text-xs leading-relaxed">{f.desc}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {[
            { icon: "🔥", title: "Bull Flow Top 20", desc: "Top bullish and bearish options plays ranked by premium. Know what's moving before the chart shows it." },
            { icon: "🚨", title: "High Conviction (5x+ C/P)", desc: "When call volume crushes puts by 5× or more, someone knows something. We spotlight it automatically." },
            { icon: "🏆", title: "Smart Money Leaderboard", desc: "AI-ranked stocks by institutional flow, win rate, and expected move. The hedge fund radar for retail traders." },
            { icon: "🏛️", title: "Congressional Trades", desc: "Real-time House STOCK Act filings. Trade amounts shown. Follow the insiders who make the laws." },
            { icon: "📊", title: "Sector Heatmap", desc: "All 11 S&P sectors color-coded live. See instantly where money is flowing in — and out." },
            { icon: "🎯", title: "Prop Desk Simulator", desc: "Paper trade with real discipline — daily loss limits, profit targets, and drawdown tracking like a funded firm." },
            { icon: "🤖", title: "AI Win Rates", desc: "Every stock gets an ML-powered composite score, win probability, and confidence rating." },
            { icon: "📉", title: "Backtesting Engine", desc: "Test your edge on historical data before you put real money on it. Stop trading hunches." },
            { icon: "💼", title: "Portfolio Tracker", desc: "All your positions, P&L, and exposure in one dark-mode dashboard. No spreadsheet required." },
            { icon: "👁️", title: "Insider Filings", desc: "SEC Form 4 insider purchases tracked in real time — when executives buy their own stock, we flag it." },
            { icon: "📈", title: "Breakout Radar", desc: "Stocks within 2% of a 52-week high with rising volume — momentum breakouts before the crowd." },
            { icon: "🚫", title: "0DTE Filtered Out", desc: "Same-day expirations stripped automatically. Only real, forward-dated signals make the cut." },
          ].map(f => (
            <div key={f.title} className="rounded-2xl p-6 transition-all" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}
              onMouseEnter={e => (e.currentTarget.style.border = "1px solid rgba(74,222,128,0.25)")}
              onMouseLeave={e => (e.currentTarget.style.border = "1px solid rgba(255,255,255,0.07)")}>
              <div className="text-4xl mb-4">{f.icon}</div>
              <div className="font-black text-white text-lg mb-2">{f.title}</div>
              <div className="text-slate-400 text-base leading-relaxed">{f.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Comparison */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">vs. the competition</p>
        <h2 className="text-center font-black mb-4" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Same data. Half the price. More AI.</h2>
        <p className="text-center text-slate-400 mb-3 mx-auto" style={{ maxWidth: "620px", fontSize: "1.05rem", lineHeight: 1.6 }}>
          Yes — Unusual Whales, FlowAlgo, and the others all scan options flow too. We're not pretending otherwise.
        </p>
        <p className="text-center mb-10 mx-auto font-bold" style={{ maxWidth: "620px", fontSize: "1.05rem", lineHeight: 1.6, color: "#4ade80" }}>
          The difference: they charge $85–149/month for scanning alone. We charge $39 — and ship AI features they haven't built at any price.
        </p>
        <div className="overflow-x-auto">
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.09)", minWidth: "860px" }}>
            {/* Column group labels */}
            <div className="grid px-5 pt-3 pb-1 text-xs font-black uppercase tracking-widest" style={{ gridTemplateColumns: "1.5fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr" }}>
              <span />
              <span />
              <span className="text-center col-span-3" style={{ color: "#4ade80" }}>— AI features (exclusive) —</span>
              <span className="text-center col-span-4 text-slate-600">— tools —</span>
            </div>
            {/* Header */}
            <div className="grid px-5 py-3 text-xs font-bold text-slate-200 uppercase tracking-wider" style={{ gridTemplateColumns: "1.5fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr", borderBottom: "1px solid rgba(255,255,255,0.10)", background: "rgba(255,255,255,0.04)" }}>
              <span>Service</span>
              <span className="text-center">Price/mo</span>
              <span className="text-center" style={{ color: "#4ade80" }}>AI Brief</span>
              <span className="text-center" style={{ color: "#4ade80" }}>Convergence</span>
              <span className="text-center" style={{ color: "#4ade80" }}>Catalyst AI</span>
              <span className="text-center">Options Flow</span>
              <span className="text-center">Prop Desk</span>
              <span className="text-center">Backtest</span>
              <span className="text-center">AI Score</span>
              <span className="text-center">Portfolio</span>
            </div>
            {/* Competitor rows */}
            {[
              { name: "Unusual Whales", price: "$48–110†" },
              { name: "FlowAlgo",        price: "$99–149†" },
              { name: "Cheddar Flow",    price: "$85–99†"  },
              { name: "BlackBoxStocks",  price: "$99–149†" },
            ].map(r => (
              <div key={r.name} className="grid px-5 py-4 text-sm items-center" style={{ gridTemplateColumns: "1.5fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <span className="text-slate-400 font-semibold">{r.name}</span>
                <span className="text-center text-red-400 font-black">{r.price}</span>
                {/* AI features — all ✕ */}
                {[0,1,2].map(i => (
                  <span key={i} className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                ))}
                {/* Options flow — all ✓ (they have it, just expensive) */}
                <span className="text-center text-slate-500 font-black text-base">✓</span>
                {/* Other tools — all ✕ */}
                {[0,1,2,3].map(i => (
                  <span key={i} className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                ))}
              </div>
            ))}
            {/* StockScanner AI row */}
            <div className="grid px-5 py-5 items-center" style={{ gridTemplateColumns: "1.5fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr 0.85fr", background: "rgba(34,197,94,0.06)", borderTop: "2px solid rgba(34,197,94,0.35)" }}>
              <div>
                <div className="font-black text-emerald-300 text-base leading-tight">StockScanner AI ⭐</div>
                <div className="text-xs text-emerald-600 mt-0.5">Everything included</div>
              </div>
              <span className="text-center text-emerald-400 font-black text-base">$39</span>
              {/* All ✓ */}
              {[0,1,2,3,4,5,6,7].map(i => (
                <span key={i} className="text-center text-emerald-400 font-black text-xl">✓</span>
              ))}
            </div>
          </div>
        </div>
        {/* Legend */}
        <div className="flex flex-wrap justify-center gap-x-6 gap-y-1.5 mt-5 text-slate-500 text-xs">
          <span><span className="text-emerald-400 font-bold">AI Brief</span> — Claude writes your daily market brief from live flow data</span>
          <span><span className="text-emerald-400 font-bold">Convergence</span> — stocks with unusual volume AND call flow at the same time</span>
          <span><span className="text-emerald-400 font-bold">Catalyst AI</span> — ask Claude why any ticker is moving, get a thesis instantly</span>
          <span><span className="text-slate-500 font-bold">Options Flow</span> — all competitors have this; we just charge 60% less for it</span>
        </div>
        <p className="text-center text-slate-600 text-sm mt-4">† Prices checked June 2025 · Options flow ✓ for all — that's the baseline. The AI layer is what they haven't built.</p>
        <p className="text-center text-slate-600 text-sm mt-1">StockScanner AI costs less than a single bad trade. It pays for itself in one good signal.</p>
      </div>

      {/* Testimonials */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Traders love it</p>
        <h2 className="text-center font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Real traders. Real results.</h2>
        <div className="grid sm:grid-cols-2 gap-5">
          {[
            { quote: "I used to spend an hour every morning on Unusual Whales trying to find something actionable. Now I open Bull Flow and I know in 30 seconds.", name: "Mike R.", title: "Day trader · Providence, RI", stars: 5 },
            { quote: "A high conviction flag on a big-cap name showed up before the open. Stock moved over 3% by noon. No other platform surfaced that signal that clearly.", name: "Sarah K.", title: "Options trader · Chicago, IL", stars: 5 },
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

      {/* Pricing CTA */}
      <div id="pricing" className="px-6 pb-28 max-w-xl mx-auto">
        <div className="rounded-3xl p-8 sm:p-10 relative overflow-hidden" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(34,197,94,0.2)", boxShadow: "0 0 80px rgba(34,197,94,0.08)" }}>
          <div style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: "500px", height: "200px", background: "radial-gradient(ellipse at 50% 0%, rgba(34,197,94,0.12) 0%, transparent 70%)", pointerEvents: "none" }} />
          <div className="relative text-center mb-8">
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-black mb-4" style={{ background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.35)", color: "#f87171" }}>
              🔥 Limited Time Offer — Price goes up soon
            </div>
            <div className="flex items-end justify-center gap-3 mb-1">
              <div className="font-black" style={{ fontSize: "5rem", letterSpacing: "-0.05em", lineHeight: 1 }}>$39</div>
              <div className="mb-4 text-slate-500 line-through text-3xl font-bold">$59</div>
            </div>
            <div className="text-slate-400 text-lg mb-1">per month · save $20/mo while it lasts</div>
            <p className="text-slate-600 text-base">Cancel anytime · Instant access · No contracts</p>
          </div>
          <ul className="space-y-3.5 mb-8">
            {[
              "Bull Flow Top 20 — bullish + bearish",
              "High Conviction 5x+ spotlight (daily)",
              "Smart Money Leaderboard + AI scores",
              "Congressional trades — live STOCK Act filings",
              "Sector heatmap + advance/decline breadth",
              "Prop Desk simulator with risk limits",
              "Backtesting engine",
              "Portfolio tracker",
              "0DTE filtered — only real signals",
            ].map(f => (
              <li key={f} className="flex items-center gap-3 text-base text-slate-200">
                <span className="text-emerald-400 font-black text-xl shrink-0">✓</span>{f}
              </li>
            ))}
          </ul>
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
          <p className="text-center text-slate-600 text-sm mt-4">
            Lock in $39/mo before the price goes up.{" "}
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
        <p className="text-slate-700 text-sm mb-4 max-w-md mx-auto">For informational purposes only. Not financial advice. Options trading involves substantial risk of loss. Past signals do not guarantee future results.</p>
        <button onClick={() => setLocation("/app")} className="text-slate-600 hover:text-slate-400 transition-colors text-sm">
          Open the app →
        </button>
      </div>

    </div>
  );
}
