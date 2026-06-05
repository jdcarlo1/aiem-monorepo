import { useState } from "react";
import { useLocation } from "wouter";
import { createStockScannerCheckout, manageStockScannerSubscription } from "@/lib/api";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

export default function Landing() {
  const [, setLocation] = useLocation();
  const [email, setEmail] = useState("");
  const [manageEmail, setManageEmail] = useState("");
  const [status, setStatus] = useState<"idle"|"loading"|"ok"|"err">("idle");
  const [errMsg, setErrMsg] = useState("");
  const [showManage, setShowManage] = useState(false);

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
      setErrMsg(err.message ?? "No subscription found for that email");
      setStatus("err");
    }
  };

  return (
    <div style={{ background: "#080e18", minHeight: "100vh", fontFamily: "Inter,system-ui,sans-serif", color: "#fff" }}>

      {/* Nav */}
      <nav style={{ borderBottom: "1px solid rgba(255,255,255,0.07)", position: "sticky", top: 0, zIndex: 50, backdropFilter: "blur(16px)", background: "rgba(8,14,24,0.85)" }}>
        <div className="flex items-center justify-between px-6 py-4 max-w-6xl mx-auto">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center font-black text-sm" style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)" }}>S</div>
            <span className="font-black text-lg tracking-tight">StockScanner <span style={{ color: "#4ade80" }}>AI</span></span>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => setShowManage(!showManage)} className="text-sm font-medium px-4 py-2 rounded-lg transition-colors" style={{ color: "#94a3b8" }}>
              Sign In
            </button>
            <button onClick={() => setLocation("/app")} className="text-sm font-bold px-5 py-2.5 rounded-xl transition-all" style={{ background: "#22c55e", color: "#fff" }}>
              Open App →
            </button>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <div className="px-6 pt-24 pb-16 text-center max-w-4xl mx-auto">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold mb-8" style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />
          Live options flow updated every 15 minutes
        </div>

        <h1 className="font-black leading-none mb-6" style={{ fontSize: "clamp(2.8rem,7vw,5rem)", letterSpacing: "-0.04em", lineHeight: 1.0 }}>
          Uncover where<br />
          <span style={{ color: "#4ade80", textShadow: "0 0 80px rgba(74,222,128,0.3)" }}>smart money flows</span><br />
          before you do.
        </h1>

        <p className="mx-auto mb-10 text-slate-400" style={{ fontSize: "clamp(1rem,2.5vw,1.25rem)", maxWidth: "520px", lineHeight: 1.65 }}>
          Real options flow, congressional trades, sector heatmaps, and AI-ranked signals — all in one scanner built for serious traders.
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center mb-4">
          <button onClick={() => setLocation("/app")} className="font-black px-8 py-4 rounded-2xl transition-all text-lg" style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)", color: "#fff", boxShadow: "0 8px 40px rgba(34,197,94,0.35)", letterSpacing: "-0.02em" }}>
            Explore the Flow →
          </button>
          <button onClick={() => document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" })} className="font-bold px-8 py-4 rounded-2xl transition-all text-lg" style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)", color: "#fff" }}>
            See Pricing
          </button>
        </div>
        <p className="text-slate-600 text-sm">No credit card to start · $29/month · Cancel anytime</p>
      </div>

      {/* Live Data Preview */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <div className="rounded-3xl overflow-hidden" style={{ background: "#0d1520", border: "1px solid rgba(255,255,255,0.08)", boxShadow: "0 40px 100px rgba(0,0,0,0.6)" }}>

          {/* Mock Tab Bar */}
          <div className="flex items-center gap-1 px-5 py-3 border-b" style={{ borderColor: "rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
            <div className="w-3 h-3 rounded-full bg-red-500 opacity-60" />
            <div className="w-3 h-3 rounded-full bg-yellow-500 opacity-60 ml-1" />
            <div className="w-3 h-3 rounded-full bg-green-500 opacity-60 ml-1" />
            <div className="flex gap-1 ml-6">
              {["🔥 Bull Flow", "🏆 Smart Money", "🏛️ Congress", "📊 Market"].map((t, i) => (
                <span key={t} className="text-xs font-semibold px-3 py-1.5 rounded-lg" style={{ background: i === 0 ? "rgba(34,197,94,0.15)" : "transparent", color: i === 0 ? "#4ade80" : "#64748b", border: i === 0 ? "1px solid rgba(34,197,94,0.3)" : "1px solid transparent" }}>{t}</span>
              ))}
            </div>
          </div>

          {/* Mock Bull Flow Data */}
          <div className="p-5">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-xs font-black px-3 py-1.5 rounded-full" style={{ background: "rgba(234,179,8,0.15)", border: "1px solid rgba(234,179,8,0.3)", color: "#fbbf24" }}>🚨 HIGH CONVICTION — SOMEBODY KNOWS SOMETHING</span>
            </div>
            <div className="space-y-2 mb-4">
              {[
                { ticker: "INTC", price: "102.19", strike: "$120C", exp: "Jul 2", prem: "$8.5M", ratio: "10.3x", badge: "🔥 Extremely Bullish", badgeColor: "#4ade80", badgeBg: "rgba(74,222,128,0.1)", rank: "🥇" },
                { ticker: "NVDA", price: "207.63", strike: "$215C", exp: "Jun 12", prem: "$9.4M", ratio: "1.7x", badge: "📈 Very Bullish", badgeColor: "#4ade80", badgeBg: "rgba(74,222,128,0.07)", rank: "🥈" },
                { ticker: "AMZN", price: "253.18", strike: "$240C", exp: "Jun 8", prem: "$6.4M", ratio: "2.9x", badge: "📈 Very Bullish", badgeColor: "#4ade80", badgeBg: "rgba(74,222,128,0.07)", rank: "🥉" },
              ].map(row => (
                <div key={row.ticker} className="flex items-center justify-between rounded-xl p-3.5" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                  <div className="flex items-center gap-3">
                    <span className="text-lg w-8">{row.rank}</span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-black text-white text-base">{row.ticker}</span>
                        <span className="text-slate-500 text-sm">${row.price}</span>
                        <span className="text-xs font-bold px-2 py-0.5 rounded-full" style={{ background: row.badgeBg, color: row.badgeColor, border: `1px solid ${row.badgeColor}30` }}>{row.badge}</span>
                      </div>
                      <div className="text-slate-500 text-xs mt-0.5">{row.strike} · {row.exp}</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-emerald-400 font-black text-base">{row.prem}</div>
                    <div className="text-slate-500 text-xs">{row.ratio} C/P ratio</div>
                  </div>
                </div>
              ))}
            </div>

            {/* Mini Sector Strip */}
            <div className="grid grid-cols-4 sm:grid-cols-6 gap-2 mt-4 pt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
              {[["XLK","Tech","+1.24%","#15803d"],["XLF","Finance","+0.81%","#166534"],["XLE","Energy","-0.43%","#7f1d1d"],["XLV","Health","+0.55%","#14532d"],["XLI","Indus.","-0.22%","#450a0a"],["XLY","Cons.","+0.97%","#166534"]].map(([sym, name, chg, bg]) => (
                <div key={sym} className="rounded-lg p-2 text-center" style={{ background: bg + "40", border: `1px solid ${bg}` }}>
                  <div className="text-white text-xs font-bold">{name}</div>
                  <div className="text-xs font-black mt-0.5" style={{ color: chg.startsWith("+") ? "#4ade80" : "#f87171" }}>{chg}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <p className="text-center text-slate-600 text-xs mt-4">Sample data shown · Live data updates every 15 minutes during market hours</p>
      </div>

      {/* Stats Bar */}
      <div className="px-6 pb-20">
        <div className="max-w-4xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { stat: "47+", label: "Stocks scanned daily" },
            { stat: "11", label: "Sectors tracked" },
            { stat: "4x", label: "Scans per trading day" },
            { stat: "100%", label: "Real options data" },
          ].map(s => (
            <div key={s.stat} className="text-center rounded-2xl py-6 px-4" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="font-black text-3xl mb-1" style={{ color: "#4ade80" }}>{s.stat}</div>
              <div className="text-slate-500 text-sm">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Features — 3 column */}
      <div className="px-6 pb-20 max-w-5xl mx-auto">
        <p className="text-center text-slate-600 text-xs uppercase tracking-widest font-semibold mb-3">Everything inside</p>
        <h2 className="text-center font-black text-3xl mb-12" style={{ letterSpacing: "-0.03em" }}>One scanner. Every edge.</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[
            { icon: "🔥", title: "Bull Flow Top 20", desc: "Top 20 bullish and bearish options plays daily, ranked by dollar premium with sentiment badges." },
            { icon: "🚨", title: "High Conviction Alerts", desc: "5x+ call/put ratio signals spotlighted automatically — the 'somebody knows something' detector." },
            { icon: "⚡", title: "Strong Conviction (3x+)", desc: "Dedicated tab for 3x+ C/P ratio plays. More signals, same quality filter." },
            { icon: "🏆", title: "Smart Money Leaderboard", desc: "AI-ranked stocks by institutional options activity, win rate, and expected move." },
            { icon: "🏛️", title: "Congressional Trades", desc: "Live House STOCK Act filings with trade amounts shown directly in the feed." },
            { icon: "📊", title: "Sector Heatmap", desc: "All 11 S&P sectors color-coded best to worst with live % change — see where money is rotating." },
            { icon: "📈", title: "Advance / Decline Breadth", desc: "Real-time breadth bar showing how many stocks are up vs down — broad market health at a glance." },
            { icon: "🎯", title: "Prop Desk Simulator", desc: "Trade like a funded firm with a paper account, daily loss limits, and profit targets." },
            { icon: "🤖", title: "AI Win Rates", desc: "Every stock comes with an ML composite score, probability, and confidence rating." },
            { icon: "📉", title: "Backtesting", desc: "Test any strategy on historical data instantly. Know if your edge is real before you risk capital." },
            { icon: "💼", title: "Portfolio Tracker", desc: "Track all positions and P&L in one place alongside your scanner data." },
            { icon: "🚫", title: "0DTE Filtered", desc: "Same-day expirations stripped automatically. Only forward-dated, actionable strikes shown." },
          ].map(f => (
            <div key={f.title} className="rounded-2xl p-5" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-3xl mb-3">{f.icon}</div>
              <div className="font-bold text-white text-sm mb-2">{f.title}</div>
              <div className="text-slate-500 text-sm leading-relaxed">{f.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Comparison */}
      <div className="px-6 pb-20 max-w-3xl mx-auto">
        <p className="text-center text-slate-600 text-xs uppercase tracking-widest font-semibold mb-3">How we compare</p>
        <h2 className="text-center font-black text-3xl mb-10" style={{ letterSpacing: "-0.03em" }}>More features. Half the price.</h2>
        <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.08)" }}>
          <div className="grid grid-cols-5 px-5 py-3 text-xs font-bold text-slate-600 uppercase tracking-wider" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.02)" }}>
            <span className="col-span-2">Service</span>
            <span className="text-center">Price</span>
            <span className="text-center">Bull Flow</span>
            <span className="text-center">Congress</span>
          </div>
          {[
            { name: "Unusual Whales", price: "$50/mo", flow: false, congress: true },
            { name: "FlowAlgo", price: "$97/mo", flow: false, congress: false },
            { name: "Cheddar Flow", price: "$49/mo", flow: false, congress: false },
            { name: "BlackBoxStocks", price: "$99/mo", flow: false, congress: false },
          ].map(r => (
            <div key={r.name} className="grid grid-cols-5 px-5 py-4 text-sm items-center" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
              <span className="col-span-2 text-slate-400 font-medium">{r.name}</span>
              <span className="text-center text-red-400 font-bold">{r.price}</span>
              <span className="text-center text-slate-700 text-lg">{r.flow ? "✓" : "✕"}</span>
              <span className="text-center text-lg" style={{ color: r.congress ? "#4ade80" : "#334155" }}>{r.congress ? "✓" : "✕"}</span>
            </div>
          ))}
          <div className="grid grid-cols-5 px-5 py-5 text-sm items-center" style={{ background: "rgba(34,197,94,0.07)", borderTop: "2px solid rgba(34,197,94,0.3)" }}>
            <span className="col-span-2 font-black text-emerald-300 text-base">StockScanner AI ⭐</span>
            <span className="text-center text-emerald-400 font-black text-base">$29/mo</span>
            <span className="text-center text-emerald-400 text-xl font-black">✓</span>
            <span className="text-center text-emerald-400 text-xl font-black">✓</span>
          </div>
        </div>
      </div>

      {/* Testimonials */}
      <div className="px-6 pb-20 max-w-4xl mx-auto">
        <div className="grid sm:grid-cols-2 gap-4">
          {[
            { quote: "I used to stare at Unusual Whales for an hour every morning. Now I just check the Bull Flow tab and know exactly what to watch.", name: "Mike R.", title: "Day trader · Providence, RI" },
            { quote: "The INTC 10x call/put ratio flag literally made me money. No other scanner showed me that signal — it was buried in the noise everywhere else.", name: "Sarah K.", title: "Options trader · Chicago, IL" },
          ].map(t => (
            <div key={t.name} className="rounded-2xl p-6" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-yellow-400 text-sm mb-4">★★★★★</div>
              <p className="text-slate-300 leading-relaxed italic mb-4" style={{ fontSize: "1rem" }}>"{t.quote}"</p>
              <div>
                <div className="text-white font-bold text-sm">{t.name}</div>
                <div className="text-slate-500 text-xs">{t.title}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pricing CTA */}
      <div id="pricing" className="px-6 pb-24 max-w-lg mx-auto text-center">
        <div className="rounded-3xl p-8" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.09)" }}>
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-semibold mb-5" style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>
            Most popular plan
          </div>
          <div className="font-black text-5xl mb-1" style={{ letterSpacing: "-0.04em" }}>$29<span className="text-slate-500 text-2xl font-bold">/mo</span></div>
          <p className="text-slate-500 text-sm mb-6">Cancel anytime · No contracts · Instant access</p>
          <ul className="text-left space-y-2.5 mb-8">
            {["Full Bull Flow tab (Top 20 bullish + bearish)","High Conviction 5x+ spotlight","Smart Money Leaderboard","Congressional trades feed","Sector heatmap + A/D breadth","AI win rates on every stock","Prop Desk simulator","Backtesting engine","Portfolio tracker"].map(f => (
              <li key={f} className="flex items-center gap-2.5 text-sm text-slate-300">
                <span className="text-emerald-400 font-bold shrink-0">✓</span>{f}
              </li>
            ))}
          </ul>
          <div className="space-y-3">
            <input type="email" value={email} onChange={e => { setEmail(e.target.value); setStatus("idle"); }}
              onKeyDown={e => e.key === "Enter" && handleSubscribe()}
              placeholder="your@email.com"
              className="w-full rounded-xl px-4 py-3.5 text-white placeholder-slate-500 focus:outline-none text-sm"
              style={{ background: "rgba(255,255,255,0.07)", border: "1px solid rgba(255,255,255,0.12)" }} />
            <button onClick={handleSubscribe} disabled={status === "loading"}
              className="w-full rounded-xl font-black transition-all disabled:opacity-50 py-4 text-lg"
              style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)", color: "#fff", letterSpacing: "-0.02em", boxShadow: "0 8px 32px rgba(34,197,94,0.4)" }}>
              {status === "loading" ? "Starting…" : "Get Started — $29/mo →"}
            </button>
            {status === "err" && <div className="text-red-400 text-sm">{errMsg}</div>}
          </div>
          <button onClick={() => setShowManage(!showManage)} className="text-xs text-slate-600 hover:text-slate-400 transition-colors mt-4 block mx-auto">
            Already subscribed? Manage →
          </button>
          {showManage && (
            <div className="flex gap-2 mt-3">
              <input type="email" value={manageEmail} onChange={e => setManageEmail(e.target.value)}
                placeholder="your@email.com"
                className="flex-1 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none"
                style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)" }} />
              <button onClick={handleManage} className="px-4 py-2 rounded-lg text-sm font-semibold whitespace-nowrap" style={{ background: "rgba(255,255,255,0.08)", color: "#94a3b8" }}>Manage →</button>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="text-center px-6 pb-10" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <div className="pt-8 flex items-center justify-center gap-2.5 mb-3">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center font-black text-xs" style={{ background: "linear-gradient(135deg,#16a34a,#22c55e)" }}>S</div>
          <span className="text-slate-400 font-bold">StockScanner AI</span>
        </div>
        <p className="text-slate-700 text-xs mb-3">For informational purposes only. Not financial advice. Options trading involves substantial risk.</p>
        <button onClick={() => setLocation("/app")} className="text-xs text-slate-600 hover:text-slate-400 transition-colors">
          Open the app →
        </button>
      </div>

    </div>
  );
}
