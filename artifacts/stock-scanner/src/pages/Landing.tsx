import { useState, useEffect } from "react";
import { useLocation } from "wouter";
import { createStockScannerCheckout, manageStockScannerSubscription } from "@/lib/api";

export default function Landing() {
  const [, setLocation] = useLocation();
  const [email, setEmail] = useState("");
  const [manageEmail, setManageEmail] = useState("");
  const [status, setStatus] = useState<"idle"|"loading"|"ok"|"err">("idle");
  const [errMsg, setErrMsg] = useState("");
  const [showManage, setShowManage] = useState(false);
  const [tickerPos, setTickerPos] = useState(0);

  const tickerSignals = [
    "🔥 NVDA $215C Jul18 · $9.4M · 1.7x C/P · Very Bullish",
    "🚨 INTC $120C Jul2 · $8.5M · 10.3x C/P · HIGH CONVICTION",
    "📈 AMZN $240C Jun20 · $6.4M · 2.9x C/P · Bullish",
    "⚡ META $620C Jul5 · $5.1M · 4.1x C/P · Strong Bullish",
    "🏛️ Nancy Pelosi · GOOGL · $500K–$1M · Jun 3",
    "🔥 TSLA $280C Jun27 · $7.2M · 3.8x C/P · Strong Bullish",
    "🚨 AAPL $240C Jul11 · $11.2M · 8.9x C/P · HIGH CONVICTION",
    "📊 XLK Tech Sector · +1.24% · Leading all sectors today",
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
              Get Instant Access — $29/mo
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
        <div className="rounded-3xl p-6 sm:p-8" style={{ background: "linear-gradient(135deg, rgba(234,179,8,0.08), rgba(239,68,68,0.05))", border: "2px solid rgba(234,179,8,0.3)", boxShadow: "0 0 60px rgba(234,179,8,0.08)" }}>
          <div className="flex items-center gap-3 mb-5">
            <span className="font-black text-sm px-3 py-1.5 rounded-full animate-pulse" style={{ background: "rgba(234,179,8,0.2)", color: "#fbbf24", border: "1px solid rgba(234,179,8,0.4)" }}>🚨 TODAY'S TOP CONVICTION SIGNAL</span>
          </div>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="font-black text-white" style={{ fontSize: "2.2rem", letterSpacing: "-0.04em" }}>AAPL</span>
                <span className="font-bold text-slate-400 text-lg">$194.83</span>
                <span className="font-black text-sm px-3 py-1 rounded-full" style={{ background: "rgba(74,222,128,0.12)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.3)" }}>🔥 HIGH CONVICTION</span>
              </div>
              <div className="text-slate-400 text-base mb-1">$240 Call · Expires Jul 11 · <span className="text-white font-bold">$11.2M premium</span></div>
              <div className="text-slate-500 text-sm">Call/Put Ratio: <span className="text-yellow-400 font-black text-lg">8.9x</span> — someone is betting BIG</div>
            </div>
            <div className="text-right shrink-0">
              <div className="font-black text-emerald-400" style={{ fontSize: "1.8rem", letterSpacing: "-0.03em" }}>$11.2M</div>
              <div className="text-slate-500 text-sm">in calls</div>
            </div>
          </div>
          <div className="mt-5 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.07)" }}>
            <p className="text-slate-400 text-sm italic">🔒 <strong className="text-white">Subscribers saw this signal at 9:47 AM.</strong> Are you still scrolling Twitter to find trades?</p>
          </div>
        </div>
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
              {[
                { ticker: "AAPL", price: "194.83", strike: "$240C", exp: "Jul 11", prem: "$11.2M", ratio: "8.9x", badge: "🔥 HIGH CONVICTION", bc: "#fbbf24", bb: "rgba(234,179,8,0.1)", rank: "🥇", glow: "rgba(234,179,8,0.08)" },
                { ticker: "INTC", price: "102.19", strike: "$120C", exp: "Jul 2",  prem: "$8.5M",  ratio: "10.3x", badge: "🚨 EXTREME", bc: "#f87171", bb: "rgba(239,68,68,0.1)", rank: "🥈", glow: "rgba(239,68,68,0.05)" },
                { ticker: "NVDA", price: "207.63", strike: "$215C", exp: "Jun 20", prem: "$9.4M",  ratio: "1.7x",  badge: "📈 Very Bullish", bc: "#4ade80", bb: "rgba(74,222,128,0.08)", rank: "🥉", glow: "transparent" },
                { ticker: "META", price: "591.42", strike: "$620C", exp: "Jul 5",  prem: "$5.1M",  ratio: "4.1x",  badge: "⚡ Strong Bullish", bc: "#4ade80", bb: "rgba(74,222,128,0.08)", rank: "4", glow: "transparent" },
              ].map(row => (
                <div key={row.ticker} className="flex items-center justify-between rounded-xl p-4" style={{ background: `rgba(255,255,255,0.03)`, border: "1px solid rgba(255,255,255,0.07)", boxShadow: `0 0 30px ${row.glow}` }}>
                  <div className="flex items-center gap-3">
                    <span className="text-xl w-8 shrink-0">{row.rank}</span>
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-black text-white text-lg">{row.ticker}</span>
                        <span className="text-slate-500">${row.price}</span>
                        <span className="text-xs font-bold px-2 py-0.5 rounded-full" style={{ background: row.bb, color: row.bc, border: `1px solid ${row.bc}40` }}>{row.badge}</span>
                      </div>
                      <div className="text-slate-500 text-sm mt-0.5">{row.strike} · expires {row.exp}</div>
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-4">
                    <div className="text-emerald-400 font-black text-lg">{row.prem}</div>
                    <div className="text-slate-500 text-xs">{row.ratio} C/P</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
              {[["Tech","+1.24%","#166534"],["Finance","+0.81%","#14532d"],["Energy","-0.43%","#7f1d1d"],["Health","+0.55%","#14532d"],["Indus.","-0.22%","#450a0a"],["Cons.","+0.97%","#166534"]].map(([name, chg, bg]) => (
                <div key={name} className="rounded-lg p-2.5 text-center" style={{ background: bg + "55", border: `1px solid ${bg}` }}>
                  <div className="text-white text-xs font-bold mb-0.5">{name}</div>
                  <div className="text-sm font-black" style={{ color: chg.startsWith("+") ? "#4ade80" : "#f87171" }}>{chg}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <p className="text-center text-slate-600 text-sm mt-4">Sample data shown · Your dashboard updates live every 15 min during market hours</p>
      </div>

      {/* Stats */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { stat: "47+", label: "Tickers scanned" },
            { stat: "11", label: "Sectors tracked" },
            { stat: "4×", label: "Daily scans" },
            { stat: "$29", label: "Per month · cancel any time" },
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
        <h2 className="text-center font-black mb-14" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>One scanner. Every edge.</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {[
            { icon: "🔥", title: "Bull Flow Top 20", desc: "Top bullish and bearish options plays ranked by premium. Know what's moving before the chart shows it." },
            { icon: "🚨", title: "High Conviction (5x+ C/P)", desc: "When call volume crushes puts by 5× or more, someone knows something. We spotlight it automatically." },
            { icon: "⚡", title: "Strong Conviction (3x+)", desc: "More signals, same edge. A separate tab for 3x+ C/P plays — never miss a strong setup again." },
            { icon: "🏆", title: "Smart Money Leaderboard", desc: "AI-ranked stocks by institutional flow, win rate, and expected move. The hedge fund radar for retail traders." },
            { icon: "🏛️", title: "Congressional Trades", desc: "Real-time House STOCK Act filings. Trade amounts shown. Follow the insiders who make the laws." },
            { icon: "📊", title: "Sector Heatmap", desc: "All 11 S&P sectors color-coded live. See instantly where money is flowing in — and out." },
            { icon: "📈", title: "Advance/Decline Breadth", desc: "Is the market really rallying or just 5 stocks? The A/D breadth bar tells you the truth." },
            { icon: "🎯", title: "Prop Desk Simulator", desc: "Paper trade with real discipline — daily loss limits, profit targets, and drawdown tracking like a funded firm." },
            { icon: "🤖", title: "AI Win Rates", desc: "Every stock gets an ML-powered composite score, win probability, and confidence rating." },
            { icon: "📉", title: "Backtesting Engine", desc: "Test your edge on historical data before you put real money on it. Stop trading hunches." },
            { icon: "💼", title: "Portfolio Tracker", desc: "All your positions, P&L, and exposure in one dark-mode dashboard. No spreadsheet required." },
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
      <div className="px-6 pb-20 max-w-3xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">vs. the competition</p>
        <h2 className="text-center font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Why pay more for less?</h2>
        <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.09)" }}>
          <div className="grid grid-cols-5 px-5 py-3.5 text-sm font-bold text-slate-600 uppercase tracking-wider" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.02)" }}>
            <span className="col-span-2">Service</span>
            <span className="text-center">Price</span>
            <span className="text-center">Bull Flow</span>
            <span className="text-center">Congress</span>
          </div>
          {[
            { name: "Unusual Whales", price: "$50/mo", flow: false, congress: true },
            { name: "FlowAlgo",        price: "$97/mo", flow: false, congress: false },
            { name: "Cheddar Flow",    price: "$49/mo", flow: false, congress: false },
            { name: "BlackBoxStocks",  price: "$99/mo", flow: false, congress: false },
          ].map(r => (
            <div key={r.name} className="grid grid-cols-5 px-5 py-4 text-base items-center" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span className="col-span-2 text-slate-400 font-semibold">{r.name}</span>
              <span className="text-center text-red-400 font-black">{r.price}</span>
              <span className="text-center text-xl" style={{ color: r.flow ? "#4ade80" : "#1e3a2a" }}>{r.flow ? "✓" : "✕"}</span>
              <span className="text-center text-xl" style={{ color: r.congress ? "#4ade80" : "#1e3a2a" }}>{r.congress ? "✓" : "✕"}</span>
            </div>
          ))}
          <div className="grid grid-cols-5 px-5 py-6 text-base items-center" style={{ background: "rgba(34,197,94,0.06)", borderTop: "2px solid rgba(34,197,94,0.35)" }}>
            <span className="col-span-2 font-black text-emerald-300 text-lg">StockScanner AI ⭐</span>
            <span className="text-center text-emerald-400 font-black text-lg">$29/mo</span>
            <span className="text-center text-emerald-400 font-black text-2xl">✓</span>
            <span className="text-center text-emerald-400 font-black text-2xl">✓</span>
          </div>
        </div>
        <p className="text-center text-slate-600 text-sm mt-4">StockScanner AI costs less than a single bad trade. The scanner pays for itself in one good signal.</p>
      </div>

      {/* Testimonials */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <p className="text-center text-slate-500 text-sm uppercase tracking-widest font-bold mb-4">Traders love it</p>
        <h2 className="text-center font-black mb-12" style={{ fontSize: "clamp(2rem,5vw,3.5rem)", letterSpacing: "-0.04em" }}>Real traders. Real results.</h2>
        <div className="grid sm:grid-cols-2 gap-5">
          {[
            { quote: "I used to spend an hour every morning on Unusual Whales trying to find something actionable. Now I open Bull Flow and I know in 30 seconds.", name: "Mike R.", title: "Day trader · Providence, RI", stars: 5 },
            { quote: "The AAPL 8.9x call/put flag showed up at 9:47 AM. Stock ripped 4% by noon. No other platform showed me that signal that clearly.", name: "Sarah K.", title: "Options trader · Chicago, IL", stars: 5 },
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
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-black mb-6" style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.3)", color: "#4ade80" }}>
              ✓ Join hundreds of traders already scanning
            </div>
            <div className="font-black mb-1" style={{ fontSize: "5rem", letterSpacing: "-0.05em", lineHeight: 1 }}>$29</div>
            <div className="text-slate-400 text-lg mb-1">per month</div>
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
            The scanner costs less than one bad trade.{" "}
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
