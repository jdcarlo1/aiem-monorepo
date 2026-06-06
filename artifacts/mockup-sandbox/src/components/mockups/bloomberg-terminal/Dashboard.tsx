import { useState, useEffect } from "react";

const ORANGE = "#FF6600";
const AMBER = "#FFB300";
const GREEN = "#00E676";
const RED = "#FF1744";
const BLUE = "#29B6F6";
const CYAN = "#00BCD4";
const PURPLE = "#CE93D8";
const BG = "#000000";
const PANEL = "#0A0A0A";
const BORDER = "#1A1A1A";
const BORDER2 = "#222222";
const DIM = "#444444";
const LABEL = "#666666";
const WHITE = "#E0E0E0";
const FONT = "'IBM Plex Mono', 'Courier New', monospace";

const TOP10 = [
  { rank: 1, ticker: "NVDA", name: "NVIDIA Corp", score: 9.4, price: 138.21, chg: 3.82, vol: "2.4x", sector: "TECH" },
  { rank: 2, ticker: "META", name: "Meta Platforms", score: 9.1, price: 612.44, chg: 2.17, vol: "1.9x", sector: "TECH" },
  { rank: 3, ticker: "AMD",  name: "Adv Micro Dev", score: 8.9, price: 162.88, chg: 1.54, vol: "1.7x", sector: "TECH" },
  { rank: 4, ticker: "AMZN", name: "Amazon.com",    score: 8.7, price: 228.15, chg: 0.92, vol: "1.4x", sector: "TECH" },
  { rank: 5, ticker: "TSLA", name: "Tesla Inc",     score: 8.4, price: 314.72, chg: -1.23, vol: "3.1x", sector: "AUTO" },
  { rank: 6, ticker: "AAPL", name: "Apple Inc",     score: 8.2, price: 212.49, chg: 0.44, vol: "1.2x", sector: "TECH" },
  { rank: 7, ticker: "MSFT", name: "Microsoft",     score: 7.9, price: 442.81, chg: 0.88, vol: "1.1x", sector: "TECH" },
  { rank: 8, ticker: "GOOGL",name: "Alphabet Inc",  score: 7.8, price: 191.28, chg: 1.02, vol: "1.3x", sector: "TECH" },
  { rank: 9, ticker: "JPM",  name: "JPMorgan Chase",score: 7.6, price: 265.44, chg: -0.34, vol: "1.0x", sector: "FIN" },
  { rank:10, ticker: "MU",   name: "Micron Tech",   score: 7.4, price: 112.39, chg: 2.88, vol: "2.8x", sector: "SEMI" },
];

const BULL_FLOW = [
  { ticker: "NVDA", cp: 4.2, premium: 18.4, strike: 140, expiry: "06/20", bias: "BULL" },
  { ticker: "META", cp: 3.8, premium: 12.1, strike: 620, expiry: "06/20", bias: "BULL" },
  { ticker: "SPY",  cp: 3.1, premium: 31.2, strike: 560, expiry: "06/27", bias: "BULL" },
  { ticker: "AMD",  cp: 2.9, premium: 8.8,  strike: 165, expiry: "07/18", bias: "BULL" },
  { ticker: "TSLA", cp: 0.4, premium: 14.7, strike: 300, expiry: "06/20", bias: "BEAR" },
  { ticker: "QQQ",  cp: 2.7, premium: 22.3, strike: 490, expiry: "06/27", bias: "BULL" },
  { ticker: "AMZN", cp: 2.4, premium: 9.2,  strike: 230, expiry: "07/18", bias: "BULL" },
  { ticker: "MU",   cp: 2.2, premium: 5.9,  strike: 115, expiry: "06/20", bias: "BULL" },
];

const SECTORS = [
  { name: "TECH",    chg: 1.84, strength: 92 },
  { name: "SEMI",    chg: 2.41, strength: 88 },
  { name: "FINANCE", chg: -0.32, strength: 44 },
  { name: "ENERGY",  chg: -0.91, strength: 31 },
  { name: "HEALTH",  chg: 0.22, strength: 58 },
  { name: "CONSUMER",chg: 0.67, strength: 63 },
  { name: "DEFENSE", chg: 1.12, strength: 71 },
  { name: "CRYPTO",  chg: 3.44, strength: 95 },
];

const MARKET = [
  { label: "SPY",  val: "556.42", chg: "+1.12%", up: true },
  { label: "QQQ",  val: "484.88", chg: "+1.84%", up: true },
  { label: "DIA",  val: "428.14", chg: "+0.44%", up: true },
  { label: "IWM",  val: "214.62", chg: "-0.23%", up: false },
  { label: "VIX",  val: "13.44",  chg: "-4.21%", up: false },
  { label: "BTC",  val: "103,812", chg: "+2.88%", up: true },
];

const ALERTS = [
  { time: "09:45", ticker: "NVDA", type: "BULL FLOW", detail: "$18.4M calls · C/P 4.2×" },
  { time: "09:47", ticker: "META", type: "BULL FLOW", detail: "$12.1M calls · C/P 3.8×" },
  { time: "09:52", ticker: "SPY",  type: "MEGA FLOW", detail: "$31.2M calls · C/P 3.1×" },
  { time: "10:01", ticker: "TSLA", type: "BEAR FLOW", detail: "$14.7M puts · C/P 0.4×" },
  { time: "10:14", ticker: "AMD",  type: "BULL FLOW", detail: "$8.8M calls · C/P 2.9×" },
];

function useNow() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function scoreColor(s: number) {
  if (s >= 9) return GREEN;
  if (s >= 8) return AMBER;
  if (s >= 7) return ORANGE;
  return RED;
}

function chgColor(v: number) { return v >= 0 ? GREEN : RED; }

function Bar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{ height: 4, background: BORDER2, borderRadius: 2, overflow: "hidden", width: "100%" }}>
      <div style={{ width: `${Math.min(100, pct)}%`, height: "100%", background: color, transition: "width 0.3s" }} />
    </div>
  );
}

function Panel({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ background: PANEL, border: `1px solid ${BORDER}`, display: "flex", flexDirection: "column", ...style }}>
      {children}
    </div>
  );
}

function PanelHeader({ label, sub, accent = ORANGE }: { label: string; sub?: string; accent?: string }) {
  return (
    <div style={{ borderBottom: `1px solid ${BORDER}`, padding: "6px 10px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 3, height: 14, background: accent, borderRadius: 1 }} />
        <span style={{ fontFamily: FONT, fontSize: 11, fontWeight: 700, color: accent, letterSpacing: "0.12em", textTransform: "uppercase" }}>{label}</span>
      </div>
      {sub && <span style={{ fontFamily: FONT, fontSize: 10, color: LABEL }}>{sub}</span>}
    </div>
  );
}

export function Dashboard() {
  const now = useNow();
  const timeStr = now.toLocaleTimeString("en-US", { hour12: false, timeZone: "America/New_York" });
  const dateStr = now.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });

  const [blink, setBlink] = useState(true);
  useEffect(() => {
    const t = setInterval(() => setBlink(b => !b), 800);
    return () => clearInterval(t);
  }, []);

  const [tickerPos, setTickerPos] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTickerPos(p => p - 1), 20);
    return () => clearInterval(t);
  }, []);

  const tickerText = "  SPY +1.12%  ·  QQQ +1.84%  ·  DIA +0.44%  ·  IWM -0.23%  ·  VIX -4.21%  ·  BTC +2.88%  ·  NVDA +3.82%  ·  META +2.17%  ·  TSLA -1.23%  ·  MU +2.88%  ·  AMD +1.54%  ·  AMZN +0.92%  ·  AAPL +0.44%  ";

  return (
    <div style={{ width: "100vw", height: "100vh", background: BG, fontFamily: FONT, display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* ── TOP BAR ─────────────────────────────────────────────────────────── */}
      <div style={{ background: "#050505", borderBottom: `2px solid ${ORANGE}`, padding: "0 16px", height: 40, display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: ORANGE, boxShadow: `0 0 8px ${ORANGE}` }} />
            <span style={{ color: ORANGE, fontWeight: 900, fontSize: 14, letterSpacing: "0.15em" }}>STOCKSCANNER</span>
            <span style={{ color: WHITE, fontWeight: 400, fontSize: 14, letterSpacing: "0.1em" }}>AI</span>
            <span style={{ color: LABEL, fontSize: 10, marginLeft: 4 }}>TERMINAL</span>
          </div>
          <div style={{ width: 1, height: 20, background: BORDER2 }} />
          {MARKET.slice(0, 4).map(m => (
            <div key={m.label} style={{ display: "flex", gap: 5, alignItems: "baseline" }}>
              <span style={{ color: LABEL, fontSize: 10, fontWeight: 700, letterSpacing: "0.08em" }}>{m.label}</span>
              <span style={{ color: WHITE, fontSize: 11 }}>{m.val}</span>
              <span style={{ color: m.up ? GREEN : RED, fontSize: 10 }}>{m.chg}</span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: GREEN, boxShadow: `0 0 6px ${GREEN}`, opacity: blink ? 1 : 0.3, transition: "opacity 0.2s" }} />
            <span style={{ color: GREEN, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>MARKET OPEN</span>
          </div>
          <div style={{ width: 1, height: 20, background: BORDER2 }} />
          <div style={{ textAlign: "right" }}>
            <div style={{ color: WHITE, fontSize: 13, fontWeight: 700, letterSpacing: "0.1em" }}>{timeStr}</div>
            <div style={{ color: LABEL, fontSize: 9, letterSpacing: "0.08em" }}>NEW YORK · {dateStr}</div>
          </div>
        </div>
      </div>

      {/* ── NAV TABS ────────────────────────────────────────────────────────── */}
      <div style={{ background: "#060606", borderBottom: `1px solid ${BORDER}`, display: "flex", alignItems: "center", gap: 0, flexShrink: 0, height: 28 }}>
        {[
          { label: "OVERVIEW", active: true },
          { label: "BULL FLOW" },
          { label: "SMART MONEY" },
          { label: "CONGRESS" },
          { label: "SCANNER" },
          { label: "OUTCOMES" },
          { label: "ANALYTICS" },
          { label: "PROP DESK" },
        ].map(tab => (
          <div key={tab.label} style={{
            padding: "0 14px", height: "100%", display: "flex", alignItems: "center",
            background: tab.active ? "#0F0F0F" : "transparent",
            borderRight: `1px solid ${BORDER}`,
            borderBottom: tab.active ? `2px solid ${ORANGE}` : "2px solid transparent",
            cursor: "pointer",
          }}>
            <span style={{ fontSize: 10, fontWeight: tab.active ? 700 : 400, color: tab.active ? ORANGE : LABEL, letterSpacing: "0.1em" }}>{tab.label}</span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ padding: "0 14px", display: "flex", alignItems: "center", gap: 8, height: "100%", borderLeft: `1px solid ${BORDER}` }}>
          <span style={{ fontSize: 10, color: LABEL }}>VIX</span>
          <span style={{ fontSize: 11, color: "#29B6F6", fontWeight: 700 }}>13.44</span>
          <span style={{ fontSize: 9, color: RED }}>▼4.21%</span>
        </div>
      </div>

      {/* ── MAIN GRID ───────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "320px 1fr 260px", gridTemplateRows: "1fr 180px", gap: 0, overflow: "hidden", borderBottom: `1px solid ${BORDER}` }}>

        {/* LEFT: Top 10 Leaderboard */}
        <Panel style={{ gridRow: "1 / 3", borderRight: `1px solid ${BORDER}`, overflow: "hidden" }}>
          <PanelHeader label="Today's Top 10" sub={`FROM ${TOP10.length * 5}+ TICKERS`} accent={ORANGE} />
          <div style={{ flex: 1, overflowY: "auto" }}>
            <div style={{ display: "grid", gridTemplateColumns: "18px 52px 1fr 36px 36px", gap: 0, padding: "4px 8px 2px", borderBottom: `1px solid ${BORDER2}` }}>
              {["#", "TKTR", "SCORE", "CHG%", "VOL"].map(h => (
                <span key={h} style={{ fontSize: 9, color: LABEL, letterSpacing: "0.08em", padding: "2px 4px" }}>{h}</span>
              ))}
            </div>
            {TOP10.map((r, i) => (
              <div key={r.ticker} style={{ display: "grid", gridTemplateColumns: "18px 52px 1fr 36px 36px", gap: 0, padding: "0 8px", borderBottom: `1px solid ${BORDER2}`, background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent", cursor: "pointer" }}>
                <span style={{ fontSize: 10, color: LABEL, padding: "7px 4px" }}>{r.rank}</span>
                <div style={{ padding: "5px 4px" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: WHITE }}>{r.ticker}</div>
                  <div style={{ fontSize: 8, color: LABEL, marginTop: 1 }}>{r.sector}</div>
                </div>
                <div style={{ padding: "5px 4px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 900, color: scoreColor(r.score) }}>{r.score.toFixed(1)}</span>
                    <div style={{ flex: 1, maxWidth: 60 }}>
                      <Bar pct={r.score * 10} color={scoreColor(r.score)} />
                    </div>
                  </div>
                  <div style={{ fontSize: 8, color: LABEL, marginTop: 2 }}>${r.price.toFixed(2)}</div>
                </div>
                <span style={{ fontSize: 10, fontWeight: 700, color: chgColor(r.chg), padding: "7px 4px", textAlign: "right" }}>
                  {r.chg >= 0 ? "+" : ""}{r.chg.toFixed(1)}%
                </span>
                <span style={{ fontSize: 10, color: r.vol >= "2.0x" ? AMBER : LABEL, padding: "7px 4px", textAlign: "right" }}>{r.vol}</span>
              </div>
            ))}
          </div>
          <div style={{ padding: "6px 10px", borderTop: `1px solid ${BORDER}`, display: "flex", justifyContent: "space-between" }}>
            <span style={{ fontSize: 9, color: LABEL }}>REFRESHES DAILY AT OPEN</span>
            <span style={{ fontSize: 9, color: ORANGE }}>50 SCANNED</span>
          </div>
        </Panel>

        {/* CENTER TOP: Bull Flow + Market Overview */}
        <Panel style={{ borderRight: `1px solid ${BORDER}`, borderBottom: `1px solid ${BORDER}`, overflow: "hidden" }}>
          <PanelHeader label="Bull Flow Signals" sub="C/P ≥ 2× · INSTITUTIONAL OPTIONS FLOW" accent={ORANGE} />
          <div style={{ flex: 1, overflowY: "auto" }}>
            {/* Column headers */}
            <div style={{ display: "grid", gridTemplateColumns: "64px 52px 72px 60px 70px 1fr", gap: 0, padding: "4px 12px 2px", borderBottom: `1px solid ${BORDER2}` }}>
              {["TICKER", "C/P", "PREMIUM", "STRIKE", "EXPIRY", "BIAS"].map(h => (
                <span key={h} style={{ fontSize: 9, color: LABEL, letterSpacing: "0.08em" }}>{h}</span>
              ))}
            </div>
            {BULL_FLOW.map((r, i) => (
              <div key={r.ticker + i} style={{
                display: "grid", gridTemplateColumns: "64px 52px 72px 60px 70px 1fr",
                gap: 0, padding: "8px 12px",
                borderBottom: `1px solid ${BORDER2}`,
                background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent",
                cursor: "pointer",
              }}>
                <span style={{ fontSize: 12, fontWeight: 900, color: WHITE }}>{r.ticker}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: r.bias === "BULL" ? GREEN : RED }}>{r.cp.toFixed(1)}×</span>
                <div>
                  <span style={{ fontSize: 11, color: AMBER, fontWeight: 700 }}>${r.premium.toFixed(1)}M</span>
                </div>
                <span style={{ fontSize: 11, color: WHITE }}>${r.strike}</span>
                <span style={{ fontSize: 11, color: LABEL }}>{r.expiry}</span>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700, letterSpacing: "0.1em",
                    color: r.bias === "BULL" ? GREEN : RED,
                    background: r.bias === "BULL" ? "rgba(0,230,118,0.08)" : "rgba(255,23,68,0.08)",
                    border: `1px solid ${r.bias === "BULL" ? "rgba(0,230,118,0.2)" : "rgba(255,23,68,0.2)"}`,
                    padding: "2px 6px", borderRadius: 2,
                  }}>
                    {r.bias === "BULL" ? "▲ BULLISH" : "▼ BEARISH"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Panel>

        {/* CENTER BOTTOM: Sector Heatmap */}
        <Panel style={{ borderRight: `1px solid ${BORDER}`, overflow: "hidden" }}>
          <PanelHeader label="Sector Strength" sub="BREADTH ANALYSIS" accent={CYAN} />
          <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, padding: 8, background: BORDER }}>
            {SECTORS.map(s => (
              <div key={s.name} style={{
                background: s.chg >= 0
                  ? `rgba(0,230,118,${0.03 + s.strength / 300})`
                  : `rgba(255,23,68,${0.03 + Math.abs(s.strength - 50) / 200})`,
                border: `1px solid ${s.chg >= 0 ? "rgba(0,230,118,0.15)" : "rgba(255,23,68,0.15)"}`,
                padding: "8px 10px",
                cursor: "pointer",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: WHITE, letterSpacing: "0.08em" }}>{s.name}</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: chgColor(s.chg) }}>
                    {s.chg >= 0 ? "+" : ""}{s.chg.toFixed(2)}%
                  </span>
                </div>
                <Bar pct={s.strength} color={s.chg >= 0 ? GREEN : RED} />
                <div style={{ fontSize: 9, color: LABEL, marginTop: 3 }}>STR {s.strength}/100</div>
              </div>
            ))}
          </div>
        </Panel>

        {/* RIGHT: Live Alerts */}
        <Panel style={{ gridRow: "1 / 2", overflow: "hidden" }}>
          <PanelHeader label="Live Alerts" sub="TODAY" accent={RED} />
          <div style={{ flex: 1, overflowY: "auto" }}>
            {ALERTS.map((a, i) => (
              <div key={i} style={{
                padding: "8px 10px",
                borderBottom: `1px solid ${BORDER2}`,
                borderLeft: `3px solid ${a.type === "BEAR FLOW" ? RED : a.type === "MEGA FLOW" ? AMBER : GREEN}`,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 3 }}>
                  <span style={{ fontSize: 13, fontWeight: 900, color: WHITE }}>{a.ticker}</span>
                  <span style={{ fontSize: 9, color: LABEL }}>{a.time} ET</span>
                </div>
                <div style={{ marginBottom: 2 }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700, letterSpacing: "0.1em",
                    color: a.type === "BEAR FLOW" ? RED : a.type === "MEGA FLOW" ? AMBER : GREEN,
                  }}>{a.type}</span>
                </div>
                <div style={{ fontSize: 10, color: LABEL }}>{a.detail}</div>
              </div>
            ))}
          </div>
          <div style={{ padding: "6px 10px", borderTop: `1px solid ${BORDER}` }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 5, height: 5, borderRadius: "50%", background: RED, opacity: blink ? 1 : 0.3 }} />
              <span style={{ fontSize: 9, color: LABEL }}>LIVE · UPDATES EVERY SCAN</span>
            </div>
          </div>
        </Panel>

        {/* RIGHT BOTTOM: Market Stats */}
        <Panel style={{ gridRow: "2 / 3", overflow: "hidden" }}>
          <PanelHeader label="Market" sub="INDICES" accent={BLUE} />
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 0 }}>
            {MARKET.map((m, i) => (
              <div key={m.label} style={{
                display: "grid", gridTemplateColumns: "40px 1fr auto",
                padding: "5px 10px",
                borderBottom: i < MARKET.length - 1 ? `1px solid ${BORDER2}` : "none",
                alignItems: "center",
                background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent",
              }}>
                <span style={{ fontSize: 10, color: LABEL, fontWeight: 700 }}>{m.label}</span>
                <div style={{ paddingLeft: 4 }}>
                  <div style={{ width: "100%", height: 3, background: BORDER2, borderRadius: 1 }}>
                    <div style={{ width: m.up ? "70%" : "30%", height: "100%", background: m.up ? GREEN : RED, borderRadius: 1 }} />
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 11, color: WHITE, fontWeight: 700 }}>{m.val}</div>
                  <div style={{ fontSize: 9, color: m.up ? GREEN : RED }}>{m.chg}</div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* ── BOTTOM TICKER ───────────────────────────────────────────────────── */}
      <div style={{ height: 24, background: "#050505", borderTop: `1px solid ${BORDER}`, overflow: "hidden", display: "flex", alignItems: "center", flexShrink: 0, position: "relative" }}>
        <div style={{ width: 80, background: "#050505", zIndex: 2, height: "100%", display: "flex", alignItems: "center", paddingLeft: 12, borderRight: `1px solid ${BORDER2}`, flexShrink: 0 }}>
          <span style={{ fontSize: 9, color: ORANGE, fontWeight: 700, letterSpacing: "0.1em" }}>LIVE FEED</span>
        </div>
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <div style={{
            display: "flex", whiteSpace: "nowrap",
            transform: `translateX(${tickerPos % 1200}px)`,
            color: WHITE, fontSize: 10, letterSpacing: "0.06em",
            gap: 0,
          }}>
            {[tickerText, tickerText, tickerText].map((t, i) => (
              <span key={i} style={{ paddingRight: 40 }}>
                {t.split("·").map((seg, j) => {
                  const trimmed = seg.trim();
                  const isNeg = trimmed.includes("-");
                  const isPos = trimmed.match(/\+\d/);
                  return (
                    <span key={j}>
                      {j > 0 && <span style={{ color: BORDER2, margin: "0 8px" }}>·</span>}
                      <span style={{ color: isNeg ? RED : isPos ? GREEN : LABEL }}>{trimmed}</span>
                    </span>
                  );
                })}
              </span>
            ))}
          </div>
        </div>
        <div style={{ width: 120, flexShrink: 0, paddingRight: 12, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, borderLeft: `1px solid ${BORDER2}` }}>
          <span style={{ fontSize: 9, color: LABEL }}>A/D</span>
          <span style={{ fontSize: 9, color: GREEN }}>312↑</span>
          <span style={{ fontSize: 9, color: RED }}>188↓</span>
        </div>
      </div>
    </div>
  );
}
