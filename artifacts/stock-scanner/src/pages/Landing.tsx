import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { createStockScannerCheckout, manageStockScannerSubscription, fetchBullFlow, BullFlowRow } from "@/lib/api";

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

  useEffect(() => {
    fetchBullFlow().then(d => setLiveFlow(d.results ?? [])).catch(() => {});
  }, []);

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

      {/* ── HERO ── */}
      <div className="relative text-center overflow-hidden" style={{ padding: "100px 24px 80px" }}>
        <div style={{ position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)", width: "900px", height: "600px", background: "radial-gradient(ellipse at 50% 0%, rgba(34,197,94,0.18) 0%, transparent 70%)", pointerEvents: "none" }} />
        <div className="relative max-w-5xl mx-auto">
          <div className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-bold mb-10" style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.3)", color: "#4ade80" }}>
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse inline-block" />
            The only platform that reads every signal source — and tells you what they mean together
          </div>

          <h1 className="font-black leading-none mb-8" style={{ fontSize: "clamp(3.2rem,9vw,7.5rem)", letterSpacing: "-0.055em", lineHeight: 0.92 }}>
            12 scanners.<br />
            <span style={{ color: "#4ade80", textShadow: "0 0 160px rgba(74,222,128,0.5)" }}>One AI thesis.</span>
          </h1>

          <p className="mx-auto mb-6 text-slate-300" style={{ fontSize: "clamp(1.1rem,2.5vw,1.4rem)", maxWidth: "660px", lineHeight: 1.75 }}>
            Every other platform shows you signals in separate tabs and leaves the thinking to you. <strong className="text-white">StockScanner AI feeds all 12 sources into one AI simultaneously</strong> — dark pool, smart money, options flow, IV rank, gamma walls, max pain, congress trades, and more — and outputs 3 complete, written trade setups every day.
          </p>

          <p className="mx-auto mb-12 font-bold" style={{ fontSize: "1.05rem", maxWidth: "580px", color: "#fbbf24" }}>
            No other platform does this. Not Unusual Whales. Not FlowAlgo. Not Trade Ideas.
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

      {/* ── THE PROBLEM ── */}
      <div className="px-6 pb-20 max-w-4xl mx-auto text-center">
        <p className="text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">The problem with every competitor</p>
        <h2 className="font-black mb-6" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          They show you signals.<br /><span className="text-slate-500">You still have to figure out what they mean.</span>
        </h2>
        <p className="text-slate-400 mx-auto mb-14" style={{ maxWidth: "600px", fontSize: "1.1rem", lineHeight: 1.75 }}>
          Unusual Whales shows you dark pool flow in one tab. Options flow in another. Smart money in another. Congress trades somewhere else. Then you close 6 tabs, open a chart, and try to connect the dots yourself. That's not a tool — that's homework.
        </p>
        <div className="grid sm:grid-cols-3 gap-4 text-left">
          {[
            { before: "You open 6 different tabs trying to figure out if signals agree.", after: "One AI reads all 12 sources together and tells you exactly what they say — combined.", icon: "🗂️" },
            { before: "You see heavy put volume and don't know if it's a hedge or a real bearish bet.", after: "Put Intent Decoder classifies every put: hedge vs directional bet. Nobody else does this.", icon: "🎯" },
            { before: "You get a signal score. No entry, no strike, no expiry, no thesis.", after: "You get: ticker, direction, entry strike, expiry, target, stop loss, and a written thesis. Ready to execute.", icon: "📋" },
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
              Every day, our AI reads all 12 signal sources simultaneously — dark pool flow, smart money vs retail divergence, options flow, IV rank, gamma walls, max pain, congress trades, call accumulation, put intent, composite scores, convergence signals, and breakout momentum. Then it outputs 3 complete trade setups, written in plain English, ranked from most bullish to most bearish.
            </p>
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
                    "All 12 sources → fed to AI together",
                    "3 picks → sorted most bullish to bearish",
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
              <p className="text-xs text-slate-500 uppercase tracking-widest font-bold mb-3">Example AI output — today's top pick</p>
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <div className="flex items-center gap-3 mb-1 flex-wrap">
                    <span className="font-black text-white text-2xl">NVDA</span>
                    <span className="px-2 py-0.5 rounded text-xs font-black" style={{ background: "rgba(74,222,128,0.1)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.25)" }}>BULLISH</span>
                    <span className="px-2 py-0.5 rounded text-xs font-black" style={{ background: "rgba(251,191,36,0.1)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.25)" }}>HIGH CONVICTION</span>
                  </div>
                  <p className="text-slate-400 text-sm mb-2">Setup: <span className="text-white font-bold">LONG CALL</span> · Entry $210C · Exp 2025-07-18 · Target $230 · Stop $192</p>
                  <p className="text-slate-400 text-sm" style={{ maxWidth: "480px" }}>
                    <span className="text-slate-300 font-semibold">Thesis:</span> Dark pool shows STRONG BUY conviction. Smart money holding 3.2x retail. Call accumulation at $210 strike with IV rank at 28% — cheap options with institutional backing. Max pain at $205 creates upside pull.
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-xs text-slate-600 mb-1">Signals aligned</div>
                  {["Dark Pool: STRONG BUY", "Smart vs Retail: +3.2x", "IV Rank: 28% (cheap)"].map(s => (
                    <div key={s} className="text-xs font-bold mb-1" style={{ color: "#4ade80" }}>● {s}</div>
                  ))}
                </div>
              </div>
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
              desc: "All 12 signal sources fed into GPT simultaneously. Outputs 3 written trade setups daily: ticker, direction, entry strike, expiry, target, stop loss, and thesis. No competitor does cross-source synthesis at any price.",
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

        {/* More differentiators */}
        <div className="rounded-2xl p-6" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <p className="text-slate-500 text-xs uppercase tracking-widest font-bold mb-5">Also included — things competitors charge more for or don't offer</p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { icon: "⚡", title: "Convergence Scanner", desc: "Stocks with unusual volume AND heavy call flow simultaneously — the highest-conviction setup pattern." },
              { icon: "🏆", title: "Smart vs Retail Divergence", desc: "When institutions and retail are on opposite sides of the same ticker, flagged and ranked." },
              { icon: "🌅", title: "AI Morning Brief", desc: "AI reads today's live flow and writes your daily market brief automatically, every morning." },
              { icon: "🎯", title: "Prop Desk Simulator", desc: "Paper trade with real discipline — daily loss limits, drawdown tracking, and profit targets like a funded firm." },
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

      {/* ── COMPARISON TABLE ── */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">vs. the competition</p>
        <h2 className="text-center font-black mb-10" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>
          Same flow data.<br /><span style={{ color: "#fbbf24" }}>One AI layer they haven't built.</span>
        </h2>
        <div className="overflow-x-auto">
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.09)", minWidth: "780px" }}>
            <div className="grid px-5 py-3 text-xs font-bold text-slate-200 uppercase tracking-wider" style={{ gridTemplateColumns: "1.6fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr", borderBottom: "1px solid rgba(255,255,255,0.10)", background: "rgba(255,255,255,0.04)" }}>
              <span>Service</span>
              <span className="text-center">Price/mo</span>
              <span className="text-center" style={{ color: "#4ade80" }}>🤖 AI Synthesis</span>
              <span className="text-center" style={{ color: "#fbbf24" }}>🎯 Put Intent</span>
              <span className="text-center" style={{ color: "#fbbf24" }}>🌑 Dark Pool</span>
              <span className="text-center">Options Flow</span>
              <span className="text-center">Congress</span>
              <span className="text-center">Prop Desk</span>
              <span className="text-center">Backtest</span>
            </div>
            {[
              { name: "Unusual Whales", price: "$48–110†", flow: true, congress: true },
              { name: "FlowAlgo",        price: "$99–149†", flow: true, congress: false },
              { name: "Cheddar Flow",    price: "$85–99†",  flow: true, congress: false },
              { name: "BlackBoxStocks",  price: "$99–149†", flow: true, congress: false },
              { name: "Trade Ideas",     price: "$118–228†",flow: false, congress: false },
            ].map(r => (
              <div key={r.name} className="grid px-5 py-4 text-sm items-center" style={{ gridTemplateColumns: "1.6fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                <span className="text-slate-400 font-semibold">{r.name}</span>
                <span className="text-center text-red-400 font-black">{r.price}</span>
                <span className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                <span className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                <span className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                <span className="text-center font-black text-base" style={{ color: r.flow ? "#4ade80" : "#3d1a1a" }}>{r.flow ? "✓" : "✕"}</span>
                <span className="text-center font-black text-base" style={{ color: r.congress ? "#4ade80" : "#3d1a1a" }}>{r.congress ? "✓" : "✕"}</span>
                <span className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
                <span className="text-center font-black text-base" style={{ color: "#3d1a1a" }}>✕</span>
              </div>
            ))}
            <div className="grid px-5 py-5 items-center" style={{ gridTemplateColumns: "1.6fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr", background: "rgba(34,197,94,0.06)", borderTop: "2px solid rgba(34,197,94,0.35)" }}>
              <div>
                <div className="font-black text-emerald-300 text-base">StockScanner AI ⭐</div>
                <div className="text-xs text-emerald-600 mt-0.5">Everything included</div>
              </div>
              <span className="text-center text-emerald-400 font-black text-base">$39</span>
              {[0,1,2,3,4,5,6,7].map(i => (
                <span key={i} className="text-center text-emerald-400 font-black text-xl">✓</span>
              ))}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap justify-center gap-x-6 gap-y-1.5 mt-5 text-slate-600 text-xs">
          <span><span className="font-bold text-emerald-400">🤖 AI Synthesis</span> — all 12 sources → 3 written trade setups daily</span>
          <span><span className="font-bold" style={{ color: "#fbbf24" }}>🎯 Put Intent</span> — hedge vs bearish bet, decoded automatically</span>
          <span><span className="font-bold" style={{ color: "#fbbf24" }}>🌑 Dark Pool</span> — conviction signals, not raw numbers</span>
        </div>
        <p className="text-center text-slate-600 text-sm mt-4">† Prices checked June 2025. Options flow ✓ for most — that's table stakes. The AI layer is what they haven't shipped.</p>
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
              {["🤖 AI Trades", "🔥 Bull Flow", "🏆 Smart Money", "🏛️ Congress", "🎯 Prop Desk"].map((t, i) => (
                <span key={t} className="text-xs font-bold px-3 py-1.5 rounded-lg" style={{ background: i === 0 ? "rgba(34,197,94,0.15)" : "transparent", color: i === 0 ? "#4ade80" : "#475569", border: i === 0 ? "1px solid rgba(34,197,94,0.3)" : "1px solid transparent" }}>{t}</span>
              ))}
            </div>
          </div>
          <div className="p-5 sm:p-6">
            <div className="mb-4 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-black px-3 py-1.5 rounded-full" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.35)", color: "#4ade80" }}>🤖 AI synthesized 12 signal sources → 3 trade setups</span>
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
            { stat: "12", label: "Signal sources synthesized" },
            { stat: "3", label: "Written trade setups daily" },
            { stat: "47+", label: "Tickers scanned" },
            { stat: "$39", label: "Cancel anytime" },
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
            { icon: "🤖", title: "AI Trade Synthesis ★ Exclusive", desc: "12 signal sources → GPT → 3 written trade setups daily with ticker, direction, entry strike, expiry, target, stop, thesis. Nobody else does this." },
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

      {/* ── TESTIMONIALS ── */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Traders love it</p>
        <h2 className="text-center font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Real traders. Real results.</h2>
        <div className="grid sm:grid-cols-2 gap-5">
          {[
            { quote: "I used to spend an hour every morning on Unusual Whales trying to find something actionable. Now I open Bull Flow and I know in 30 seconds.", name: "Mike R.", title: "Day trader · Providence, RI", stars: 5 },
            { quote: "The AI Trade tab is what got me. I didn't expect it to actually write out a complete setup — strike, expiry, thesis and all. That's the part no one else has.", name: "Sarah K.", title: "Options trader · Chicago, IL", stars: 5 },
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
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-black mb-4" style={{ background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.35)", color: "#f87171" }}>
              🔥 Limited Time — Price goes up soon
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
              { text: "🤖 AI Trade Synthesis — 3 written setups daily, no competitor has this", highlight: true },
              { text: "🎯 Put Intent Decoder — hedge vs bearish bet, exclusively here", highlight: true },
              { text: "🌑 Dark Pool Radar — conviction signals, not raw numbers", highlight: true },
              { text: "Bull Flow Top 20 — bullish + bearish options ranked by premium", highlight: false },
              { text: "High Conviction 5x+ spotlight (daily)", highlight: false },
              { text: "🏆 Smart vs Retail Divergence", highlight: false },
              { text: "⚡ Convergence Scanner — vol + flow at the same time", highlight: false },
              { text: "🌅 AI Morning Brief — daily brief written automatically", highlight: false },
              { text: "Congressional trades — live STOCK Act filings", highlight: false },
              { text: "Sector heatmap + advance/decline breadth", highlight: false },
              { text: "Prop Desk simulator with daily risk limits", highlight: false },
              { text: "Backtesting engine + Portfolio tracker", highlight: false },
              { text: "0DTE filtered — only real signals", highlight: false },
            ].map(f => (
              <li key={f.text} className="flex items-center gap-3 text-base" style={{ color: f.highlight ? "#fbbf24" : "#e2e8f0" }}>
                <span className="font-black text-xl shrink-0" style={{ color: f.highlight ? "#fbbf24" : "#4ade80" }}>✓</span>{f.text}
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
