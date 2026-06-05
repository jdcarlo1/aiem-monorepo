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
    <div style={{background:"#070d17",minHeight:"100vh",fontFamily:"Inter,system-ui,sans-serif"}}>

      {/* Nav */}
      <nav style={{borderBottom:"1px solid rgba(255,255,255,0.06)"}} className="sticky top-0 z-50 backdrop-blur-md" >
        <div style={{background:"rgba(7,13,23,0.85)"}} className="flex items-center justify-between px-5 py-3 max-w-5xl mx-auto">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center font-black text-sm" style={{background:"linear-gradient(135deg,#15803d,#22c55e)",color:"#fff"}}>S</div>
            <span className="text-white font-bold text-base tracking-tight">StockScanner <span className="text-emerald-400">AI</span></span>
          </div>
          <button
            onClick={() => setLocation("/app")}
            className="text-sm font-semibold px-4 py-2 rounded-lg transition-all"
            style={{background:"rgba(34,197,94,0.12)",border:"1px solid rgba(34,197,94,0.3)",color:"#4ade80"}}
          >
            Open App →
          </button>
        </div>
      </nav>

      {/* Top attention bar */}
      <div className="text-center py-2.5 px-4 text-xs font-bold tracking-wide" style={{background:"linear-gradient(90deg,#14532d,#166534,#14532d)",color:"#86efac"}}>
        📱 The only stock scanner that texts you signals every morning
      </div>

      {/* Hero */}
      <div className="px-5 pt-14 pb-10 text-center max-w-2xl mx-auto">

        {/* Trust badges */}
        <div className="flex flex-wrap justify-center gap-2 mb-8">
          {["📡 Real yfinance options data","⚡ 4 scans per trading day","🤖 AI win rates included"].map(b => (
            <span key={b} className="text-xs px-3 py-1.5 rounded-full font-medium" style={{background:"rgba(255,255,255,0.05)",border:"1px solid rgba(255,255,255,0.1)",color:"#94a3b8"}}>{b}</span>
          ))}
        </div>

        {/* Headline */}
        <h1 className="font-black text-white leading-none mb-5" style={{fontSize:"clamp(2.4rem,8vw,3.75rem)",letterSpacing:"-0.04em",lineHeight:1.05}}>
          Beat the market<br/>
          <span style={{color:"#4ade80",textShadow:"0 0 50px rgba(74,222,128,0.45)"}}>before it opens.</span>
        </h1>

        <p className="mx-auto mb-10 text-slate-400" style={{fontSize:"clamp(1rem,3vw,1.2rem)",maxWidth:"400px",lineHeight:1.6}}>
          Every morning we scan the options flow and text you exactly what smart money is betting on — before the bell rings.
        </p>

        {/* iPhone SMS preview */}
        <div className="mx-auto mb-10" style={{maxWidth:"310px"}}>
          <p className="text-slate-600 text-xs mb-3 uppercase tracking-widest font-semibold">What lands on your phone every morning</p>
          <div className="rounded-3xl overflow-hidden text-left" style={{background:"#1c1c1e",border:"2px solid #3a3a3c",boxShadow:"0 20px 60px rgba(0,0,0,0.6)"}}>
            <div className="px-4 pt-3 pb-2 flex items-center gap-2.5" style={{borderBottom:"1px solid #2c2c2e"}}>
              <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-black" style={{background:"linear-gradient(135deg,#1d4ed8,#3b82f6)",color:"#fff"}}>S</div>
              <div>
                <div className="text-white text-sm font-semibold">StockScanner AI</div>
                <div className="text-slate-500" style={{fontSize:"11px"}}>Today · 9:01 AM</div>
              </div>
            </div>
            <div className="px-4 py-4">
              <div className="rounded-2xl rounded-tl-sm px-4 py-3.5" style={{background:"#2c2c2e"}}>
                <p className="text-white font-bold mb-2" style={{fontSize:"12px"}}>🚨 Pre-Market Alert</p>
                <p className="font-mono mb-1" style={{fontSize:"12px",color:"#4ade80"}}>GS $860C Jun18 · $10.9M 🔥</p>
                <p className="font-mono mb-3" style={{fontSize:"12px",color:"#4ade80"}}>ORCL $180C Jun18 · $6.6M</p>
                <p className="text-slate-400 mb-1" style={{fontSize:"12px"}}>🏆 Top signal: <span className="text-white font-semibold">LLY</span> — 67% win rate</p>
                <p className="text-slate-600" style={{fontSize:"11px"}}>Full leaderboard at stockscannerai.com</p>
              </div>
            </div>
            <div className="px-4 pb-3 text-right">
              <span className="text-slate-600" style={{fontSize:"11px"}}>Delivered ✓✓</span>
            </div>
          </div>
        </div>

        {/* Email + CTA */}
        <div className="space-y-3 mb-4 max-w-sm mx-auto">
          <input
            type="email"
            value={email}
            onChange={e => { setEmail(e.target.value); setStatus("idle"); }}
            onKeyDown={e => e.key === "Enter" && handleSubscribe()}
            placeholder="your@email.com"
            className="w-full rounded-xl px-4 py-4 text-white placeholder-slate-500 focus:outline-none"
            style={{background:"rgba(255,255,255,0.06)",border:"1px solid rgba(255,255,255,0.12)",fontSize:"1rem"}}
          />
          <button
            onClick={handleSubscribe}
            disabled={status === "loading"}
            className="w-full rounded-xl font-black transition-all disabled:opacity-50"
            style={{padding:"1.1rem 1.5rem",background:"linear-gradient(135deg,#15803d,#22c55e)",color:"#fff",fontSize:"1.15rem",letterSpacing:"-0.02em",boxShadow:"0 8px 32px rgba(34,197,94,0.4)"}}
          >
            {status === "loading" ? "Starting…" : "Start Getting Alerts →"}
          </button>
        </div>

        {status === "err" && <div className="text-red-400 text-sm mb-3 text-center">{errMsg}</div>}

        <p className="text-slate-500 text-sm mb-1">
          <span className="text-white font-bold">$29/month</span> · Cancel anytime · No contracts
        </p>
        <p className="text-slate-600 text-xs mb-2">Works on any phone · No app to download</p>

        <button onClick={() => setShowManage(!showManage)} className="text-xs text-slate-600 hover:text-slate-400 transition-colors mt-1">
          Already subscribed? Manage →
        </button>
        {showManage && (
          <div className="flex gap-2 mt-2 max-w-sm mx-auto">
            <input type="email" value={manageEmail} onChange={e => setManageEmail(e.target.value)}
              placeholder="your@email.com"
              className="flex-1 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none"
              style={{background:"rgba(255,255,255,0.06)",border:"1px solid rgba(255,255,255,0.1)"}} />
            <button onClick={handleManage} className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors whitespace-nowrap" style={{background:"rgba(255,255,255,0.08)",color:"#94a3b8"}}>Manage →</button>
          </div>
        )}
      </div>

      {/* Testimonial */}
      <div className="px-5 pb-12 max-w-lg mx-auto">
        <div className="rounded-2xl px-6 py-5 text-center" style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.07)"}}>
          <div className="text-yellow-400 text-base mb-3">★★★★★</div>
          <p className="text-slate-300 leading-relaxed italic mb-3" style={{fontSize:"1rem"}}>
            "I used to stare at Unusual Whales for an hour every morning. Now I just wait for the text and I know exactly what to watch."
          </p>
          <p className="text-slate-500 text-sm font-semibold">— Mike R., day trader · Providence, RI</p>
        </div>
      </div>

      {/* Features */}
      <div className="px-5 pb-12 max-w-2xl mx-auto">
        <p className="text-center text-slate-600 text-xs uppercase tracking-widest font-semibold mb-6">What you get that no one else offers</p>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {[
            { icon: "📱", title: "Daily Text Alerts", sub: "No other service texts you" },
            { icon: "🚨", title: "$5M+ Flow Alerts", sub: "Biggest options bets flagged" },
            { icon: "🤖", title: "AI Win Rates", sub: "Know the odds before you trade" },
            { icon: "⚡", title: "0DTE Filtered", sub: "Only actionable strikes shown" },
            { icon: "🏦", title: "Hedge Fund Scan", sub: "See where they're loading up" },
            { icon: "🏛️", title: "Congress Trades", sub: "Follow the ultimate insiders" },
          ].map(f => (
            <div key={f.title} className="rounded-xl p-4 text-center" style={{background:"rgba(255,255,255,0.025)",border:"1px solid rgba(255,255,255,0.06)"}}>
              <div className="text-2xl mb-2">{f.icon}</div>
              <div className="text-white font-bold text-xs mb-1">{f.title}</div>
              <div className="text-slate-500 text-xs leading-snug">{f.sub}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Vs competitors */}
      <div className="px-5 pb-14 max-w-lg mx-auto">
        <p className="text-center text-slate-600 text-xs uppercase tracking-widest font-semibold mb-5">How we compare</p>
        <div className="rounded-2xl overflow-hidden" style={{border:"1px solid rgba(255,255,255,0.07)"}}>
          <div className="grid grid-cols-4 px-4 py-2.5 text-xs font-semibold text-slate-600 uppercase tracking-wider" style={{borderBottom:"1px solid rgba(255,255,255,0.06)"}}>
            <span>Service</span><span className="text-center">Price</span><span className="text-center">Texts</span><span className="text-center">AI</span>
          </div>
          {[
            { name:"Unusual Whales", price:"$50/mo", texts:false, ai:false },
            { name:"FlowAlgo",        price:"$97/mo", texts:false, ai:false },
            { name:"Cheddar Flow",    price:"$49/mo", texts:false, ai:false },
            { name:"BlackBoxStocks",  price:"$99/mo", texts:false, ai:false },
          ].map(r => (
            <div key={r.name} className="grid grid-cols-4 px-4 py-3 text-xs items-center" style={{borderBottom:"1px solid rgba(255,255,255,0.04)"}}>
              <span className="text-slate-400">{r.name}</span>
              <span className="text-center text-red-400 font-bold font-mono">{r.price}</span>
              <span className="text-center text-slate-700 text-base">✕</span>
              <span className="text-center text-slate-700 text-base">✕</span>
            </div>
          ))}
          <div className="grid grid-cols-4 px-4 py-3.5 text-xs items-center" style={{background:"rgba(34,197,94,0.07)",borderTop:"1px solid rgba(34,197,94,0.2)"}}>
            <span className="text-emerald-300 font-black">StockScanner AI ⭐</span>
            <span className="text-center text-emerald-400 font-black font-mono">$29/mo</span>
            <span className="text-center text-emerald-400 text-base font-bold">✓</span>
            <span className="text-center text-emerald-400 text-base font-bold">✓</span>
          </div>
        </div>
      </div>

      {/* Bottom CTA */}
      <div className="px-5 pb-16 text-center max-w-sm mx-auto">
        <h2 className="text-white font-black mb-2" style={{fontSize:"1.6rem",letterSpacing:"-0.03em"}}>Ready to trade smarter?</h2>
        <p className="text-slate-500 text-sm mb-6">Join traders getting daily alerts — $29/mo, cancel anytime.</p>
        <button
          onClick={() => window.scrollTo({top:0,behavior:"smooth"})}
          className="w-full rounded-xl font-black transition-all"
          style={{padding:"1.05rem",background:"linear-gradient(135deg,#15803d,#22c55e)",color:"#fff",fontSize:"1.05rem",letterSpacing:"-0.02em",boxShadow:"0 6px 28px rgba(34,197,94,0.35)"}}
        >
          Get Started — $29/mo →
        </button>
      </div>

      {/* Footer */}
      <div className="text-center px-5 pb-8" style={{borderTop:"1px solid rgba(255,255,255,0.05)"}}>
        <div className="pt-6 flex items-center justify-center gap-2 mb-2">
          <div className="w-6 h-6 rounded-md flex items-center justify-center font-black text-xs" style={{background:"linear-gradient(135deg,#15803d,#22c55e)",color:"#fff"}}>S</div>
          <span className="text-slate-400 font-semibold text-sm">StockScanner AI</span>
        </div>
        <p className="text-slate-700 text-xs">For informational purposes only. Not financial advice.</p>
        <button onClick={() => setLocation("/app")} className="mt-3 text-xs text-slate-700 hover:text-slate-500 transition-colors">
          Open the app →
        </button>
      </div>

    </div>
  );
}
