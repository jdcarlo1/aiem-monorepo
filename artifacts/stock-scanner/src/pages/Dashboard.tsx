import React, { useState, useCallback, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  analyzeStock, scanStocks, fetchPortfolio, buyStock, sellStock,
  runBacktest, runHistoricalAnalytics, fetchAlerts, createAlert, deleteAlert,
  propScan, propTrade, propReset, smartMoneyScan,
  fetchCongressTrades, subscribeEmail, fetchSubscriberCount,
  createStockScannerCheckout, manageStockScannerSubscription,
  fetchBullFlow, fetchMarketOverview, fetchSqueezeSignals, fetchInsiderTrades, fetchAIThesis, fetchBreakoutRadar,
  fetchSignalOutcomes, fetchDailyTop10, fetchAIAnalysis,
  StockAnalysis, ScanResult, BacktestResult, AnalyticsResult, Alert,
  PropSignal, PropPosition, PropTrade, PropDeskResult, SmartMoneySignal, SmartMoneyResult,
  CongressTrade, CongressResult, BullFlowRow, MarketOverview, SqueezeSignal, InsiderTrade, BreakoutSignal,
  SignalOutcome, DailyTop10Result,
} from "@/lib/api";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Legend, Cell,
} from "recharts";

const DEFAULT_SCAN = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META", "JPM", "V", "SPY"];
const DEFAULT_ANALYTICS = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META", "JPM"];
const BUCKET_COLORS: Record<string, string> = {
  "1–3": "#ef4444", "3–5": "#f97316", "5–6": "#eab308",
  "6–7": "#84cc16", "7–8": "#10b981", "8–10": "#06b6d4",
};

function fmt(n?: number | null, d = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtMktCap(n?: number | null): string {
  if (!n) return "—";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString()}`;
}
function retColor(v: number | null) {
  if (v == null) return "text-slate-500";
  return v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-slate-400";
}
function wrColor(v: number | null) {
  if (v == null) return "text-slate-500";
  return v >= 55 ? "text-emerald-400" : v >= 50 ? "text-yellow-400" : "text-red-400";
}

function Spinner() {
  return <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />;
}

function ClaudeMarkdown({ text }: { text: string }) {
  const PROSE: React.CSSProperties = {
    color: "#d4d4d4",
    fontSize: 13,
    lineHeight: 1.75,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    margin: 0,
  };
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  let listBuf: string[] = [];
  const flushList = (key: string) => {
    if (!listBuf.length) return;
    nodes.push(
      <ul key={key} style={{ margin: "6px 0 8px 0", paddingLeft: 18 }}>
        {listBuf.map((li, i) => (
          <li key={i} style={{ ...PROSE, marginBottom: 4, listStyleType: "disc", listStylePosition: "outside" }}>
            <InlineMd text={li} />
          </li>
        ))}
      </ul>
    );
    listBuf = [];
  };
  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (!trimmed) { flushList(`ul-${i}`); nodes.push(<div key={`br-${i}`} style={{ height: 4 }} />); return; }
    if (trimmed.startsWith("# ")) {
      flushList(`ul-${i}`);
      nodes.push(<div key={i} style={{ ...PROSE, fontSize: 15, fontWeight: 700, color: "#e8e8e8", marginBottom: 8, marginTop: 4 }}><InlineMd text={trimmed.slice(2)} /></div>);
      return;
    }
    if (trimmed.startsWith("## ")) {
      flushList(`ul-${i}`);
      nodes.push(<div key={i} style={{ ...PROSE, fontSize: 13, fontWeight: 700, color: "#e0e0e0", marginBottom: 6, marginTop: 6 }}><InlineMd text={trimmed.slice(3)} /></div>);
      return;
    }
    if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
      listBuf.push(trimmed.slice(2));
      return;
    }
    flushList(`ul-${i}`);
    nodes.push(<p key={i} style={{ ...PROSE, marginBottom: 6 }}><InlineMd text={trimmed} /></p>);
  });
  flushList("ul-end");
  return <div>{nodes}</div>;
}

function InlineMd({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`/g;
  let last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[1] != null) parts.push(<strong key={m.index} style={{ color: "#f0f0f0", fontWeight: 700 }}>{m[1]}</strong>);
    else if (m[2] != null) parts.push(<em key={m.index} style={{ color: "#ccc" }}>{m[2]}</em>);
    else if (m[3] != null) parts.push(<code key={m.index} style={{ background: "#1a1a1a", color: "#cc785c", padding: "1px 5px", borderRadius: 3, fontSize: 12 }}>{m[3]}</code>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function ScoreBadge({ score, rating }: { score: number; rating: string }) {
  const color = score >= 8 ? "text-emerald-400 border-emerald-500"
    : score >= 6.5 ? "text-green-400 border-green-500"
    : score >= 5   ? "text-yellow-400 border-yellow-500"
    : score >= 3   ? "text-orange-400 border-orange-500"
    : "text-red-400 border-red-500";
  return (
    <div className={`inline-flex flex-col items-center border rounded-lg px-3 py-1 ${color}`}>
      <span className="text-2xl font-bold">{score.toFixed(1)}</span>
      <span className="text-xs font-medium">{rating}</span>
    </div>
  );
}

function DirectionBadge({ direction, confidence, probUp }: { direction: string; confidence: string; probUp: number }) {
  const color = direction === "Up"   ? "bg-emerald-900/50 text-emerald-300 border-emerald-700"
    : direction === "Down" ? "bg-red-900/50 text-red-300 border-red-700"
    : "bg-slate-700 text-slate-300 border-slate-600";
  const arrow = direction === "Up" ? "↑" : direction === "Down" ? "↓" : "→";
  return (
    <div className={`inline-flex items-center gap-2 border rounded-lg px-3 py-1 text-sm ${color}`}>
      <span className="text-lg">{arrow}</span>
      <div>
        <div className="font-semibold">{direction} ({probUp.toFixed(0)}%)</div>
        <div className="text-xs opacity-75">{confidence} confidence</div>
      </div>
    </div>
  );
}

function RsiGauge({ rsi }: { rsi: number }) {
  const color = rsi < 30 ? "#ef4444" : rsi > 70 ? "#f97316" : "#10b981";
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="text-xs text-slate-400">RSI</div>
      <div className="relative w-16 h-2 bg-slate-700 rounded-full overflow-hidden">
        <div style={{ width: `${Math.min(100, rsi)}%`, background: color }} className="h-full rounded-full transition-all" />
      </div>
      <div style={{ color }} className="text-sm font-bold">{fmt(rsi, 1)}</div>
      <div className="text-xs text-slate-500">{rsi < 30 ? "Oversold" : rsi > 70 ? "Overbought" : "Neutral"}</div>
    </div>
  );
}

function PriceChart({ history }: { history: StockAnalysis["history"] }) {
  if (!history?.length) return <div className="text-slate-500 text-sm text-center py-8">No price data</div>;
  const data = history.map(h => ({ date: h.date.slice(5), close: h.close, volume: h.volume }));
  return (
    <div className="space-y-4">
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} interval={14} />
          <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={55} tickFormatter={v => `$${v.toFixed(0)}`} domain={["auto","auto"]} />
          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }} labelStyle={{ color: "#94a3b8" }} itemStyle={{ color: "#60a5fa" }} formatter={(v: number) => [`$${v.toFixed(2)}`, "Price"]} />
          <Area type="monotone" dataKey="close" stroke="#3b82f6" fill="url(#priceGrad)" strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <ResponsiveContainer width="100%" height={60}>
        <BarChart data={data}>
          <Bar dataKey="volume" fill="#334155" radius={[2,2,0,0]} />
          <XAxis dataKey="date" hide /><YAxis hide />
          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }} formatter={(v: number) => [v.toLocaleString(), "Volume"]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScoreBreakdown({ breakdown }: { breakdown: StockAnalysis["score"]["breakdown"] }) {
  return (
    <div className="space-y-2">
      {breakdown.map(item => (
        <div key={item.factor} className="flex items-center gap-3">
          <div className="w-28 text-xs text-slate-400 shrink-0">{item.factor}</div>
          <div className="flex-1 bg-slate-800 rounded-full h-1.5 overflow-hidden">
            <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${(item.points / item.max) * 100}%` }} />
          </div>
          <div className="w-8 text-xs text-right text-slate-300">{item.points}/{item.max}</div>
          <div className="text-xs text-slate-500 hidden sm:block w-48 truncate">{item.label}</div>
        </div>
      ))}
    </div>
  );
}

function ScanTable({ results, onSelect }: { results: ScanResult[]; onSelect: (t: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider">
            <th className="text-left py-2 px-3">Ticker</th>
            <th className="text-right py-2 px-3">Price</th>
            <th className="text-right py-2 px-3">Chg%</th>
            <th className="text-right py-2 px-3">RSI</th>
            <th className="text-right py-2 px-3">Vol</th>
            <th className="text-right py-2 px-3">Score</th>
            <th className="text-right py-2 px-3">ML</th>
          </tr>
        </thead>
        <tbody>
          {results.map(r => (
            <tr key={r.ticker} onClick={() => onSelect(r.ticker)} className="border-b border-slate-800/50 hover:bg-slate-800/50 cursor-pointer transition-colors">
              <td className="py-2.5 px-3"><div className="font-semibold text-white">{r.ticker}</div><div className="text-xs text-slate-500 truncate max-w-[120px]">{r.name}</div></td>
              <td className="text-right py-2.5 px-3 text-slate-200">${fmt(r.price)}</td>
              <td className={`text-right py-2.5 px-3 font-medium ${(r.price_change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{r.price_change_pct != null ? `${r.price_change_pct >= 0 ? "+" : ""}${fmt(r.price_change_pct)}%` : "—"}</td>
              <td className={`text-right py-2.5 px-3 ${(r.rsi ?? 50) < 30 ? "text-red-400" : (r.rsi ?? 50) > 70 ? "text-orange-400" : "text-slate-300"}`}>{fmt(r.rsi, 1)}</td>
              <td className={`text-right py-2.5 px-3 ${(r.volume_ratio ?? 1) >= 1.5 ? "text-yellow-400" : "text-slate-400"}`}>{r.volume_ratio != null ? `${fmt(r.volume_ratio, 1)}x` : "—"}</td>
              <td className="text-right py-2.5 px-3">{r.score != null && <span className={`font-bold ${r.score >= 8 ? "text-emerald-400" : r.score >= 6 ? "text-green-400" : r.score >= 5 ? "text-yellow-400" : "text-red-400"}`}>{r.score.toFixed(1)}</span>}</td>
              <td className="text-right py-2.5 px-3">{r.direction && <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${r.direction === "Up" ? "bg-emerald-900/60 text-emerald-300" : r.direction === "Down" ? "bg-red-900/60 text-red-300" : "bg-slate-700 text-slate-400"}`}>{r.direction === "Up" ? "↑" : r.direction === "Down" ? "↓" : "→"} {r.prob_up?.toFixed(0)}%</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- Daily Top 10 Banner -------------------------------------------------
function DailyTop10Banner({ onSelect }: { onSelect: (t: string) => void }) {
  const [data, setData]       = useState<DailyTop10Result | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    fetchDailyTop10()
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message ?? "Failed"); setLoading(false); });
  }, []);

  const scoreColor = (s?: number) => {
    if (!s) return "text-slate-400";
    if (s >= 8) return "text-emerald-400";
    if (s >= 6) return "text-green-400";
    if (s >= 5) return "text-yellow-400";
    return "text-red-400";
  };

  const scoreBg = (s?: number) => {
    if (!s) return "bg-slate-800";
    if (s >= 8) return "bg-emerald-900/40 border border-emerald-800/50";
    if (s >= 6) return "bg-green-900/30 border border-green-800/40";
    if (s >= 5) return "bg-yellow-900/20 border border-yellow-800/30";
    return "bg-red-900/20 border border-red-800/30";
  };

  const chgColor = (v?: number) =>
    v == null ? "text-slate-500" : v >= 0 ? "text-emerald-400" : "text-red-400";

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-xl overflow-hidden">
      {/* Header row */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <span className="text-base font-black text-white">🏆 Today's Top 10</span>
          {data && (
            <span className="text-xs text-slate-500 font-normal">
              Highest-scoring stocks from {data.total_scanned}-ticker universe · {data.date}
            </span>
          )}
          {loading && <span className="text-xs text-slate-500">Scanning {"\u2014"} this may take ~60 sec the first time…</span>}
        </div>
        <button onClick={() => setCollapsed(c => !c)}
          className="text-slate-500 hover:text-slate-300 text-xs px-2 py-1 rounded transition-colors">
          {collapsed ? "▼ Show" : "▲ Hide"}
        </button>
      </div>

      {!collapsed && (
        <>
          {loading && (
            <div className="flex items-center justify-center gap-3 py-10 text-slate-400 text-sm">
              <Spinner /> Scanning {"\u2014"} computing scores across the full watchlist…
            </div>
          )}
          {error && (
            <div className="px-5 py-4 text-red-400 text-sm">{error}</div>
          )}
          {!loading && data && data.top10.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-5 divide-x divide-y divide-slate-800">
              {data.top10.map(r => (
                <button key={r.ticker} onClick={() => onSelect(r.ticker)}
                  className={`text-left px-4 py-3 hover:bg-slate-800/60 transition-colors group ${scoreBg(r.score)}`}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="font-black text-white text-sm group-hover:text-blue-300 transition-colors">{r.ticker}</span>
                    <span className={`text-xs font-bold ${scoreColor(r.score)}`}>{r.score?.toFixed(1)}</span>
                  </div>
                  <div className="text-slate-500 text-xs truncate">{r.name ?? r.sector}</div>
                  <div className="flex items-center gap-2 mt-1">
                    {r.price != null && <span className="text-slate-300 text-xs">${r.price.toFixed(2)}</span>}
                    {r.price_change_pct != null && (
                      <span className={`text-xs font-medium ${chgColor(r.price_change_pct)}`}>
                        {r.price_change_pct >= 0 ? "+" : ""}{r.price_change_pct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
          {!loading && data && data.top10.length === 0 && (
            <div className="px-5 py-6 text-slate-500 text-sm text-center">No results available yet — try again after market open.</div>
          )}
          <div className="px-5 py-2 border-t border-slate-800 flex items-center justify-between">
            <p className="text-slate-600 text-xs">Click any ticker to open full analysis · Score 0–10 · Updates daily at market open</p>
            <span className="text-slate-700 text-xs">Not financial advice</span>
          </div>
        </>
      )}
    </div>
  );
}

// ---- Analytics Tab -------------------------------------------------------

function AnalyticsTab() {
  const [tickerInput, setTickerInput] = useState(DEFAULT_ANALYTICS.join(", "));
  const [result, setResult] = useState<AnalyticsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [horizon, setHorizon] = useState<"1d" | "3d" | "5d">("1d");

  const run = async () => {
    setLoading(true); setError(""); setResult(null);
    const tickers = tickerInput.split(/[\s,]+/).filter(Boolean).map(t => t.toUpperCase()).slice(0, 15);
    try {
      const r = await runHistoricalAnalytics(tickers);
      if (r.error) { setError(r.error); } else { setResult(r); }
    } catch (e: any) { setError(e.message || "Analytics failed"); }
    finally { setLoading(false); }
  };

  const winKey  = `win_rate_${horizon}` as keyof typeof result.bucket_stats[0];
  const retKey  = `avg_ret_${horizon}`  as keyof typeof result.bucket_stats[0];

  const winData  = result?.bucket_stats.map(b => ({ bucket: b.bucket, "Win Rate %": b[winKey] ?? 0 }));
  const retData  = result?.bucket_stats.map(b => ({ bucket: b.bucket, "Avg Return %": b[retKey] ?? 0 }));
  const distData = result?.score_distribution ?? [];

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="text-slate-300 font-medium mb-1">Historical Score Analytics</div>
        <div className="text-slate-500 text-xs mb-4">
          Scores every trading day over 2 years for each ticker, then shows win-rates and average returns
          grouped by score bucket — so you can see which score ranges actually predicted gains.
        </div>
        <div className="mb-3">
          <label className="text-xs text-slate-400 block mb-1">Tickers (up to 15, comma-separated)</label>
          <input value={tickerInput} onChange={e => setTickerInput(e.target.value.toUpperCase())}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
        </div>
        <button onClick={run} disabled={loading}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2">
          {loading && <Spinner />}
          {loading ? "Analyzing history… (30–90 s)" : "Run Analytics"}
        </button>
        {error && <div className="mt-3 text-red-400 text-sm">{error}</div>}
      </div>

      {loading && (
        <div className="flex flex-col items-center justify-center py-20 gap-4 text-slate-400">
          <Spinner />
          <div className="text-sm">Scoring every trading day for {tickerInput.split(/[\s,]+/).filter(Boolean).length} tickers…</div>
          <div className="text-xs text-slate-600">This takes 30–90 seconds</div>
        </div>
      )}

      {result && !loading && (
        <>
          {/* Summary row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: "Tickers analyzed", value: result.tickers_analyzed.length.toString() },
              { label: "Total observations", value: result.total_observations.toLocaleString() },
              { label: "Overall 1-day win rate", value: `${result.overall_win_rate_1d}%`, color: wrColor(result.overall_win_rate_1d) },
              { label: "Failed tickers", value: result.failed.length > 0 ? result.failed.join(", ") : "None", color: result.failed.length ? "text-yellow-400" : "text-slate-400" },
            ].map(c => (
              <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="text-slate-500 text-xs mb-1">{c.label}</div>
                <div className={`font-bold text-lg ${c.color ?? "text-white"}`}>{c.value}</div>
              </div>
            ))}
          </div>

          {/* Horizon selector */}
          <div className="flex items-center gap-2">
            <span className="text-slate-500 text-sm">Return horizon:</span>
            {(["1d","3d","5d"] as const).map(h => (
              <button key={h} onClick={() => setHorizon(h)}
                className={`px-3 py-1 rounded-lg text-sm font-medium transition-colors ${horizon === h ? "bg-blue-600 text-white" : "text-slate-400 hover:text-slate-200 bg-slate-800"}`}>
                {h === "1d" ? "Next Day" : h === "3d" ? "3 Days" : "5 Days"}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Score Distribution */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-1">Score Distribution</div>
              <div className="text-slate-600 text-xs mb-4">How often each score range appeared across all trading days</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={distData} barCategoryGap="25%">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="bucket" tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} />
                  <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={40} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
                    labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [v.toLocaleString(), "Days"]} />
                  <Bar dataKey="count" radius={[4,4,0,0]}>
                    {distData.map(d => (
                      <Cell key={d.bucket} fill={BUCKET_COLORS[d.bucket] ?? "#6366f1"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Win Rate by Bucket */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-1">Win Rate by Score Bucket</div>
              <div className="text-slate-600 text-xs mb-4">% of days with positive return in the selected horizon</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={winData} barCategoryGap="25%">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="bucket" tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} />
                  <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={40} domain={[0, 100]} tickFormatter={v => `${v}%`} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
                    labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [`${v.toFixed(1)}%`, "Win Rate"]} />
                  <ReferenceLine y={50} stroke="#475569" strokeDasharray="4 4" />
                  <Bar dataKey="Win Rate %" radius={[4,4,0,0]}>
                    {winData?.map(d => (
                      <Cell key={d.bucket} fill={(d["Win Rate %"] as number) >= 55 ? "#10b981" : (d["Win Rate %"] as number) >= 50 ? "#eab308" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Avg Return by Bucket */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-1">Average Return by Score Bucket</div>
              <div className="text-slate-600 text-xs mb-4">Mean % return in the selected horizon per score range</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={retData} barCategoryGap="25%">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="bucket" tick={{ fill: "#64748b", fontSize: 11 }} tickLine={false} />
                  <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={50} tickFormatter={v => `${v.toFixed(2)}%`} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
                    labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [`${v.toFixed(3)}%`, "Avg Return"]} />
                  <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
                  <Bar dataKey="Avg Return %" radius={[4,4,0,0]}>
                    {retData?.map(d => (
                      <Cell key={d.bucket} fill={(d["Avg Return %"] as number) > 0 ? "#10b981" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Best Thresholds */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-1">Best Score Thresholds</div>
              <div className="text-slate-600 text-xs mb-4">Results for "score ≥ X" across all observations</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase">
                      <th className="text-left py-2">Score ≥</th>
                      <th className="text-right py-2">Count</th>
                      <th className="text-right py-2">WR 1d</th>
                      <th className="text-right py-2">WR 3d</th>
                      <th className="text-right py-2">WR 5d</th>
                      <th className="text-right py-2">Avg 1d</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.best_thresholds.map(t => (
                      <tr key={t.threshold} className="border-b border-slate-800/50">
                        <td className="py-2 font-semibold text-blue-400">{t.threshold}</td>
                        <td className="text-right py-2 text-slate-400">{t.count.toLocaleString()}</td>
                        <td className={`text-right py-2 font-medium ${wrColor(t.win_rate_1d)}`}>{t.win_rate_1d}%</td>
                        <td className={`text-right py-2 font-medium ${wrColor(t.win_rate_3d)}`}>{t.win_rate_3d}%</td>
                        <td className={`text-right py-2 font-medium ${wrColor(t.win_rate_5d)}`}>{t.win_rate_5d}%</td>
                        <td className={`text-right py-2 font-medium ${retColor(t.avg_ret_1d)}`}>{t.avg_ret_1d > 0 ? "+" : ""}{t.avg_ret_1d.toFixed(3)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* Full stats table */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="text-slate-400 text-sm mb-4">Full Bucket Statistics</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase">
                    <th className="text-left py-2 px-3">Score Range</th>
                    <th className="text-right py-2 px-3">Days</th>
                    <th className="text-right py-2 px-3">WR 1d</th>
                    <th className="text-right py-2 px-3">WR 3d</th>
                    <th className="text-right py-2 px-3">WR 5d</th>
                    <th className="text-right py-2 px-3">Avg 1d</th>
                    <th className="text-right py-2 px-3">Avg 3d</th>
                    <th className="text-right py-2 px-3">Avg 5d</th>
                    <th className="text-right py-2 px-3">Median 1d</th>
                  </tr>
                </thead>
                <tbody>
                  {result.bucket_stats.map(b => (
                    <tr key={b.bucket} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="py-2.5 px-3">
                        <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ background: BUCKET_COLORS[b.bucket] + "33", color: BUCKET_COLORS[b.bucket] }}>
                          {b.bucket}
                        </span>
                      </td>
                      <td className="text-right py-2.5 px-3 text-slate-400">{b.count > 0 ? b.count.toLocaleString() : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${wrColor(b.win_rate_1d)}`}>{b.win_rate_1d != null ? `${b.win_rate_1d}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${wrColor(b.win_rate_3d)}`}>{b.win_rate_3d != null ? `${b.win_rate_3d}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${wrColor(b.win_rate_5d)}`}>{b.win_rate_5d != null ? `${b.win_rate_5d}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${retColor(b.avg_ret_1d)}`}>{b.avg_ret_1d != null ? `${b.avg_ret_1d > 0 ? "+" : ""}${b.avg_ret_1d.toFixed(3)}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${retColor(b.avg_ret_3d)}`}>{b.avg_ret_3d != null ? `${b.avg_ret_3d > 0 ? "+" : ""}${b.avg_ret_3d.toFixed(3)}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 font-medium ${retColor(b.avg_ret_5d)}`}>{b.avg_ret_5d != null ? `${b.avg_ret_5d > 0 ? "+" : ""}${b.avg_ret_5d.toFixed(3)}%` : "—"}</td>
                      <td className={`text-right py-2.5 px-3 ${retColor(b.median_ret_1d)}`}>{b.median_ret_1d != null ? `${b.median_ret_1d > 0 ? "+" : ""}${b.median_ret_1d.toFixed(3)}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {!result && !loading && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-4xl mb-4">📊</div>
          <div className="text-sm">Click "Run Analytics" to score 2 years of history for each ticker</div>
          <div className="text-xs mt-2 text-slate-600">and see which score ranges predicted the best next-day returns</div>
        </div>
      )}
    </div>
  );
}

// ---- Backtest Tab --------------------------------------------------------

function BacktestTab() {
  const [ticker, setTicker] = useState("AAPL");
  const [buyThresh, setBuyThresh] = useState(6.5);
  const [sellThresh, setSellThresh] = useState(4.5);
  const [cash, setCash] = useState(10000);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true); setError(""); setResult(null);
    try { setResult(await runBacktest(ticker.trim().toUpperCase(), buyThresh, sellThresh, cash)); }
    catch (e: any) { setError(e.message || "Backtest failed"); }
    finally { setLoading(false); }
  };

  const statCard = (label: string, value: string, color = "text-white") => (
    <div className="bg-slate-800/60 rounded-lg p-4 border border-slate-700">
      <div className="text-slate-400 text-xs mb-1">{label}</div>
      <div className={`text-xl font-bold ${color}`}>{value}</div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="text-slate-300 font-medium mb-1">Strategy Backtester</div>
        <div className="text-slate-500 text-xs mb-4">Buy when composite score ≥ threshold, sell when ≤ exit threshold. Uses 2 years of data.</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div><label className="text-xs text-slate-400 block mb-1">Ticker</label><input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 uppercase" /></div>
          <div><label className="text-xs text-slate-400 block mb-1">Buy when score ≥</label><input type="number" min={1} max={10} step={0.5} value={buyThresh} onChange={e => setBuyThresh(parseFloat(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" /></div>
          <div><label className="text-xs text-slate-400 block mb-1">Sell when score ≤</label><input type="number" min={1} max={10} step={0.5} value={sellThresh} onChange={e => setSellThresh(parseFloat(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" /></div>
          <div><label className="text-xs text-slate-400 block mb-1">Starting Cash ($)</label><input type="number" value={cash} onChange={e => setCash(parseFloat(e.target.value))} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" /></div>
        </div>
        <button onClick={run} disabled={loading} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2">
          {loading && <Spinner />}{loading ? "Running backtest…" : "Run Backtest"}
        </button>
        {error && <div className="mt-3 text-red-400 text-sm">{error}</div>}
      </div>

      {result && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {statCard("Strategy Return", `${result.total_return_pct >= 0 ? "+" : ""}${result.total_return_pct.toFixed(2)}%`, result.total_return_pct >= 0 ? "text-emerald-400" : "text-red-400")}
            {statCard("Buy & Hold Return", `${result.buy_hold_return_pct >= 0 ? "+" : ""}${result.buy_hold_return_pct.toFixed(2)}%`, result.buy_hold_return_pct >= 0 ? "text-emerald-400" : "text-red-400")}
            {statCard("Alpha vs B&H", `${result.alpha >= 0 ? "+" : ""}${result.alpha.toFixed(2)}%`, result.alpha >= 0 ? "text-emerald-400" : "text-red-400")}
            {statCard("Final Portfolio", `$${result.final_value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)}
            {statCard("Trades", `${result.n_trades}`)}
            {statCard("Win Rate", `${result.win_rate.toFixed(1)}%`, result.win_rate >= 50 ? "text-emerald-400" : "text-orange-400")}
            {statCard("Max Drawdown", `-${result.max_drawdown_pct.toFixed(2)}%`, "text-red-400")}
            {statCard("Strategy", `Buy ≥${result.buy_threshold} / Sell ≤${result.sell_threshold}`, "text-blue-400")}
          </div>

          {result.equity_curve?.length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-4">Equity Curve — {result.ticker}</div>
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={result.equity_curve}>
                  <defs><linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} /><stop offset="95%" stopColor="#6366f1" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} interval={20} />
                  <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={70} tickFormatter={v => `$${v.toFixed(0)}`} domain={["auto","auto"]} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }} labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [`$${v.toFixed(2)}`, "Portfolio"]} />
                  <ReferenceLine y={result.initial_cash} stroke="#475569" strokeDasharray="4 4" />
                  <Area type="monotone" dataKey="value" stroke="#6366f1" fill="url(#eqGrad)" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {result.trades?.length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-4">Trade Log ({result.trades.length} trades)</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-slate-800 text-slate-400 text-xs uppercase"><th className="text-left py-2 px-3">Type</th><th className="text-left py-2 px-3">Date</th><th className="text-right py-2 px-3">Price</th><th className="text-right py-2 px-3">Shares</th><th className="text-right py-2 px-3">Score</th><th className="text-right py-2 px-3">P&L</th></tr></thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} className="border-b border-slate-800/50">
                        <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded text-xs font-medium ${t.type === "BUY" ? "bg-emerald-900/50 text-emerald-300" : "bg-red-900/50 text-red-300"}`}>{t.type}</span></td>
                        <td className="py-2 px-3 text-slate-400">{t.date}</td>
                        <td className="text-right py-2 px-3 text-slate-200">${fmt(t.price)}</td>
                        <td className="text-right py-2 px-3 text-slate-400">{t.shares?.toFixed(3)}</td>
                        <td className="text-right py-2 px-3"><span className={`font-medium ${t.score >= 6.5 ? "text-emerald-400" : t.score >= 5 ? "text-yellow-400" : "text-red-400"}`}>{t.score?.toFixed(1)}</span></td>
                        <td className={`text-right py-2 px-3 font-medium ${t.pnl == null ? "text-slate-500" : t.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>{t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${fmt(t.pnl)} (${t.pnl_pct! >= 0 ? "+" : ""}${fmt(t.pnl_pct)}%)` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---- Alerts Tab ----------------------------------------------------------

function AlertsTab() {
  const qc = useQueryClient();
  const [ticker, setTicker] = useState("");
  const [type, setType]     = useState("price");
  const [value, setValue]   = useState("");
  const [direction, setDirection] = useState("above");

  const { data, isLoading } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });

  const createMutation = useMutation({
    mutationFn: () => createAlert(ticker.trim().toUpperCase(), type, parseFloat(value), direction),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["alerts"] }); setTicker(""); setValue(""); },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const alerts  = data?.alerts ?? [];
  const active  = alerts.filter(a => !a.triggered);
  const fired   = alerts.filter(a => a.triggered);

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="text-slate-300 font-medium mb-1">Create Alert</div>
        <div className="text-slate-500 text-xs mb-4">Get notified when a stock hits your target price, RSI level, or composite score.</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div><label className="text-xs text-slate-400 block mb-1">Ticker</label><input value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} placeholder="AAPL" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500 uppercase" /></div>
          <div><label className="text-xs text-slate-400 block mb-1">Alert Type</label><select value={type} onChange={e => setType(e.target.value)} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"><option value="price">Price ($)</option><option value="rsi">RSI</option><option value="score">Score (1–10)</option></select></div>
          <div><label className="text-xs text-slate-400 block mb-1">Direction</label><select value={direction} onChange={e => setDirection(e.target.value)} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"><option value="above">Crosses Above</option><option value="below">Falls Below</option></select></div>
          <div><label className="text-xs text-slate-400 block mb-1">Target Value</label><input type="number" value={value} onChange={e => setValue(e.target.value)} placeholder="e.g. 200" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" /></div>
        </div>
        <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !ticker || !value} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors">
          {createMutation.isPending ? "Creating…" : "Add Alert"}
        </button>
        <div className="text-xs text-slate-500 mt-2">Alerts are checked when you analyze a stock in the Stock Lookup tab.</div>
      </div>

      {isLoading && <div className="flex items-center gap-3 text-slate-400 py-8 justify-center"><Spinner /> Loading alerts…</div>}

      {active.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <div className="text-slate-400 text-sm mb-4">Active Alerts ({active.length})</div>
          <div className="space-y-2">
            {active.map(a => (
              <div key={a.id} className="flex items-center justify-between py-2.5 px-3 bg-slate-800/60 rounded-lg border border-slate-700">
                <div className="flex items-center gap-4">
                  <span className="text-white font-semibold">{a.ticker}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-900/50 text-blue-300 border border-blue-800">{a.type === "price" ? "Price" : a.type === "rsi" ? "RSI" : "Score"} {a.direction === "above" ? "≥" : "≤"} {a.type === "price" ? `$${a.value}` : a.value}</span>
                </div>
                <button onClick={() => deleteMutation.mutate(a.id)} className="text-slate-500 hover:text-red-400 transition-colors text-xs px-2 py-1 rounded hover:bg-red-900/20">Delete</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {fired.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <div className="text-slate-400 text-sm mb-4">Triggered Alerts ({fired.length})</div>
          <div className="space-y-2">
            {fired.map(a => (
              <div key={a.id} className="flex items-center justify-between py-2.5 px-3 bg-emerald-900/20 rounded-lg border border-emerald-800/40">
                <div className="flex items-center gap-4">
                  <span className="text-white font-semibold">{a.ticker}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-emerald-900/50 text-emerald-300 border border-emerald-800">✓ {a.type} {a.direction} {a.value} — hit {a.triggered_value?.toFixed(2)} on {a.triggered_at?.slice(0,10)}</span>
                </div>
                <button onClick={() => deleteMutation.mutate(a.id)} className="text-slate-500 hover:text-red-400 transition-colors text-xs px-2 py-1 rounded hover:bg-red-900/20">Clear</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {!isLoading && alerts.length === 0 && (
        <div className="text-center py-16 text-slate-500">No alerts yet. Create one above to track price, RSI, or score targets.</div>
      )}
    </div>
  );
}

// ---- Prop Desk Tab -------------------------------------------------------

const REGIME_COLORS: Record<string, string> = {
  TRENDING: "bg-emerald-900/50 text-emerald-300 border-emerald-700",
  HIGH_VOL:  "bg-yellow-900/50 text-yellow-300 border-yellow-700",
  CHOPPY:    "bg-slate-700 text-slate-300 border-slate-600",
};
const REGIME_ICONS: Record<string, string> = {
  TRENDING: "📈", HIGH_VOL: "⚡", CHOPPY: "〰️",
};

function PropScoreBar({ value, label, color = "#3b82f6" }: { value: number; label: string; color?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="w-16 text-slate-400 shrink-0">{label}</div>
      <div className="flex-1 bg-slate-800 rounded-full h-1.5 overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${Math.round(value * 100)}%`, background: color }} />
      </div>
      <div className="w-8 text-right text-slate-400">{(value * 100).toFixed(0)}%</div>
    </div>
  );
}

// ─── Email Signup Banner (paid subscription) ──────────────────────────────────

function EmailSignupBanner() {
  const [email, setEmail]           = useState("");
  const [manageEmail, setManageEmail] = useState("");
  const [status, setStatus]         = useState<"idle"|"loading"|"ok"|"err">("idle");
  const [errMsg, setErrMsg]         = useState("");
  const [showManage, setShowManage] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("subscribed") === "true") {
      setStatus("ok");
      const url = new URL(window.location.href);
      url.searchParams.delete("subscribed");
      window.history.replaceState({}, "", url.toString());
    }
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
      setErrMsg(err.message ?? "No subscription found for that email");
      setStatus("err");
    }
  };

  if (status === "ok") {
    return (
      <div className="bg-emerald-950/40 border border-emerald-800/50 rounded-xl p-5 flex items-start gap-4">
        <span className="text-3xl">✅</span>
        <div className="flex-1">
          <div className="text-emerald-300 font-bold text-sm">You're subscribed to StockScanner AI Pro!</div>
          <div className="text-emerald-700 text-xs mt-1">Daily text alerts + 4 scans every trading day — pre-market, opening bell, pre-close, and EOD wrap — with ranked signals, real options flow &amp; high premium alerts.</div>
          <button onClick={() => setShowManage(!showManage)} className="mt-2 text-xs text-slate-500 hover:text-slate-300 underline transition-colors">
            Manage or cancel subscription →
          </button>
          {showManage && (
            <div className="flex gap-2 mt-2">
              <input type="email" value={manageEmail} onChange={e => setManageEmail(e.target.value)}
                placeholder="your@email.com"
                className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500" />
              <button onClick={handleManage} className="bg-slate-700 hover:bg-slate-600 text-white px-3 py-1.5 rounded text-xs transition-colors">Manage →</button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl overflow-hidden" style={{background:"#0a0f1a",border:"1px solid rgba(34,197,94,0.25)",boxShadow:"0 0 80px rgba(34,197,94,0.07)"}}>

      {/* Top attention bar */}
      <div className="text-center py-2.5 px-4 text-xs font-bold tracking-wide" style={{background:"linear-gradient(90deg,#14532d,#166534,#14532d)",color:"#86efac"}}>
        📱 The only stock scanner that texts you signals every morning
      </div>

      {/* Hero */}
      <div className="px-6 pt-9 pb-8 text-center">

        {/* Trust badges */}
        <div className="flex flex-wrap justify-center gap-2 mb-7">
          {["📡 Real yfinance options data","⚡ 4 scans per trading day","🤖 AI win rates included"].map(b => (
            <span key={b} className="text-xs px-3 py-1 rounded-full font-medium" style={{background:"rgba(255,255,255,0.05)",border:"1px solid rgba(255,255,255,0.1)",color:"#94a3b8"}}>{b}</span>
          ))}
        </div>

        {/* Headline */}
        <h2 className="font-black text-white leading-none mb-4" style={{fontSize:"clamp(2rem,7vw,3rem)",letterSpacing:"-0.04em",lineHeight:1.08}}>
          Beat the market<br/>
          <span style={{color:"#4ade80",textShadow:"0 0 40px rgba(74,222,128,0.5)"}}>before it opens.</span>
        </h2>

        <p className="mx-auto mb-7 text-slate-400" style={{fontSize:"clamp(0.95rem,3vw,1.1rem)",maxWidth:"340px",lineHeight:1.55}}>
          4× every trading day we scan the options flow and alert you exactly what smart money is betting on — at the open, mid-morning, pre-close, and end of day.
        </p>

        {/* iPhone SMS preview */}
        <div className="mx-auto mb-8" style={{maxWidth:"300px"}}>
          <div className="rounded-3xl overflow-hidden" style={{background:"#1c1c1e",border:"2px solid #3a3a3c"}}>
            <div className="px-4 pt-3 pb-1 flex items-center gap-2 border-b" style={{borderColor:"#2c2c2e"}}>
              <div className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" style={{background:"#1d4ed8"}}>S</div>
              <div className="text-left">
                <div className="text-white text-xs font-semibold">StockScanner AI</div>
                <div className="text-slate-500" style={{fontSize:"10px"}}>Today 9:45 AM</div>
              </div>
            </div>
            <div className="px-3 py-3 text-left space-y-1">
              <div className="rounded-2xl rounded-tl-sm px-3 py-2.5 inline-block w-full" style={{background:"#2c2c2e"}}>
                <p className="text-white font-semibold mb-1.5" style={{fontSize:"11px"}}>🔔 Opening Bell Alert</p>
                <p className="text-emerald-400 font-mono mb-1" style={{fontSize:"11px"}}>GS $860C Jun18 · $10.9M 🔥</p>
                <p className="text-emerald-400 font-mono mb-2" style={{fontSize:"11px"}}>ORCL $180C Jun18 · $6.6M</p>
                <p className="text-slate-400 mb-1" style={{fontSize:"11px"}}>🏆 Top signal: <span className="text-white font-semibold">LLY</span> — 67% win rate</p>
                <p className="text-slate-500" style={{fontSize:"10px"}}>Full leaderboard: stockscannerai.com</p>
              </div>
            </div>
            <div className="px-4 pb-3 text-right">
              <span className="text-slate-600" style={{fontSize:"10px"}}>Delivered ✓✓</span>
            </div>
          </div>
        </div>

        {/* Email + CTA */}
        <div className="space-y-3 mb-4">
          <input
            type="email"
            value={email}
            onChange={e => { setEmail(e.target.value); setStatus("idle"); }}
            onKeyDown={e => e.key === "Enter" && handleSubscribe()}
            placeholder="your@email.com"
            className="w-full rounded-xl px-4 py-3.5 text-white text-sm placeholder-slate-500 focus:outline-none"
            style={{background:"rgba(255,255,255,0.06)",border:"1px solid rgba(255,255,255,0.12)",fontSize:"1rem"}}
          />
          <button
            onClick={handleSubscribe}
            disabled={status === "loading"}
            className="w-full rounded-xl font-black transition-all disabled:opacity-50"
            style={{padding:"1rem 1.5rem",background:"linear-gradient(135deg,#15803d,#22c55e)",color:"#fff",fontSize:"1.1rem",letterSpacing:"-0.02em",boxShadow:"0 6px 30px rgba(34,197,94,0.4)"}}
          >
            {status === "loading" ? "Starting…" : "Start Getting Alerts →"}
          </button>
        </div>

        {status === "err" && <div className="text-red-400 text-xs mb-2 text-center">{errMsg}</div>}

        {/* Pricing line */}
        <p className="text-slate-500 text-sm mb-1">
          <span className="text-white font-bold">$29/month</span> · or pay once, keep forever
        </p>
        <p className="text-slate-600 text-xs mb-6">Cancel anytime · No contracts · Works on any phone</p>

        {/* Testimonial */}
        <div className="rounded-2xl px-5 py-4 text-left" style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.07)"}}>
          <div className="text-yellow-400 text-sm mb-2">★★★★★</div>
          <p className="text-slate-300 text-sm leading-relaxed italic mb-2">
            "I used to stare at Unusual Whales for an hour every morning. Now I just wait for the text and I know exactly what to watch."
          </p>
          <p className="text-slate-500 text-xs font-semibold">— Mike R., day trader · Providence, RI</p>
        </div>

        {/* Manage link */}
        <button onClick={() => setShowManage(!showManage)} className="mt-4 text-xs text-slate-600 hover:text-slate-400 transition-colors">
          Already subscribed? Manage subscription →
        </button>
        {showManage && (
          <div className="flex gap-2 mt-2">
            <input type="email" value={manageEmail} onChange={e => setManageEmail(e.target.value)}
              placeholder="your@email.com"
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none" />
            <button onClick={handleManage} className="bg-slate-700 hover:bg-slate-600 text-white px-3 py-2 rounded-lg text-xs transition-colors whitespace-nowrap">Manage →</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Congress Trades ─────────────────────────────────────────────────────────

function CongressTab() {
  const [filterTicker, setFilterTicker] = useState("");
  const [filterParty, setFilterParty] = useState<"all"|"D"|"R">("all");
  const [filterType, setFilterType] = useState<"all"|"buy"|"sell">("all");

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["congress-trades"],
    queryFn: () => fetchCongressTrades(),
    staleTime: 1000 * 60 * 60 * 6,
  });

  const trades = (data?.trades ?? []).filter(t => {
    if (filterTicker && !t.ticker.includes(filterTicker.toUpperCase())) return false;
    if (filterParty !== "all" && !t.party.startsWith(filterParty)) return false;
    if (filterType === "buy"  && !t.type.toLowerCase().includes("purchase")) return false;
    if (filterType === "sell" && !t.type.toLowerCase().includes("sale")) return false;
    return true;
  });

  const isBuy  = (t: CongressTrade) => t.type.toLowerCase().includes("purchase");
  const isSell = (t: CongressTrade) => t.type.toLowerCase().includes("sale");
  const partyColor = (p: string) => p.startsWith("D") ? "text-blue-400" : p.startsWith("R") ? "text-red-400" : "text-slate-400";
  const partyBg    = (p: string) => p.startsWith("D") ? "bg-blue-900/40 text-blue-300" : p.startsWith("R") ? "bg-red-900/40 text-red-300" : "bg-slate-800 text-slate-400";

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
          <div>
            <h3 className="text-white font-semibold text-base">🏛️ Congressional Stock Trades</h3>
            <p className="text-slate-500 text-xs mt-0.5">
              Real public disclosures · House STOCK Act filings · Last 90 days · {data?.count ?? "—"} trades
            </p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-white transition-colors disabled:opacity-50"
          >
            {isFetching ? "Loading…" : "↻ Refresh"}
          </button>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 mt-3">
          <input
            value={filterTicker}
            onChange={e => setFilterTicker(e.target.value)}
            placeholder="Filter ticker…"
            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 w-36"
          />
          {(["all","D","R"] as const).map(p => (
            <button key={p} onClick={() => setFilterParty(p)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${filterParty===p ? "border-blue-500 text-blue-400 bg-blue-950/30" : "border-slate-700 text-slate-400 hover:text-slate-300"}`}>
              {p === "all" ? "All Parties" : p === "D" ? "🔵 Democrat" : "🔴 Republican"}
            </button>
          ))}
          {(["all","buy","sell"] as const).map(t => (
            <button key={t} onClick={() => setFilterType(t)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${filterType===t ? "border-slate-500 text-white bg-slate-700" : "border-slate-700 text-slate-400 hover:text-slate-300"}`}>
              {t === "all" ? "All Types" : t === "buy" ? "🟢 Purchases" : "🔴 Sales"}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-center py-16 text-slate-500">
          <div className="text-3xl mb-3 animate-spin inline-block">⟳</div>
          <p>Loading congressional disclosures…</p>
        </div>
      ) : trades.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <div className="text-5xl mb-4">🏛️</div>
          <p className="text-lg font-medium text-slate-400">No trades found</p>
          <p className="text-sm text-slate-500 mt-1">Try adjusting your filters or click Refresh</p>
        </div>
      ) : (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
            <span className="text-white font-semibold text-sm">
              {trades.length} trade{trades.length !== 1 ? "s" : ""}
            </span>
            <span className="text-slate-500 text-xs">Source: disclosures.house.gov</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-slate-500 text-xs uppercase tracking-wide">
                  <th className="text-left py-3 px-4">Date</th>
                  <th className="text-left py-3 px-3">Member</th>
                  <th className="text-left py-3 px-3 hidden sm:table-cell">Party</th>
                  <th className="text-left py-3 px-3">Ticker</th>
                  <th className="text-left py-3 px-3">Trade</th>
                  <th className="text-left py-3 px-3 hidden md:table-cell">Amount</th>
                  <th className="text-left py-3 px-4 hidden lg:table-cell">Asset</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 100).map((t, i) => (
                  <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                    <td className="py-3 px-4 text-slate-500 text-xs font-mono whitespace-nowrap">{t.date}</td>
                    <td className="py-3 px-3">
                      <div className="font-medium text-white text-xs leading-tight">{t.member}</div>
                      {t.amount && <div className="text-emerald-400 text-xs font-semibold mt-0.5">{t.amount}</div>}
                    </td>
                    <td className="py-3 px-3 hidden sm:table-cell">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${partyBg(t.party)}`}>
                        {t.party || "?"}
                      </span>
                    </td>
                    <td className="py-3 px-3">
                      <span className="font-bold text-white">{t.ticker}</span>
                    </td>
                    <td className="py-3 px-3">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${isBuy(t) ? "bg-emerald-900/50 text-emerald-400" : isSell(t) ? "bg-red-900/50 text-red-400" : "bg-slate-800 text-slate-400"}`}>
                        {isBuy(t) ? "▲ Purchase" : isSell(t) ? "▼ Sale" : t.type}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-slate-400 text-xs hidden md:table-cell">{t.amount}</td>
                    <td className="py-3 px-4 text-slate-500 text-xs hidden lg:table-cell max-w-xs truncate">{t.asset}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
        <p className="text-slate-500 text-xs leading-relaxed">
          ⚠️ Data sourced from public House STOCK Act disclosures via house-stock-watcher.com.
          Members have up to 45 days to report trades — filings may lag actual trade dates.
          This is not financial advice.
        </p>
      </div>
    </div>
  );
}

// ─── Smart Money Leaderboard ─────────────────────────────────────────────────

const SM_DEFAULT = [
  "AAPL","MSFT","NVDA","TSLA","AMZN","META","GOOGL","AMD",
  "NFLX","PLTR","COIN","SOFI","MARA","RBLX","UBER","SMCI",
  "ARM","INTC","MU","AI","SPY","QQQ","JPM","V","PYPL",
].join(", ");

function SmScoreBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="text-white font-medium">{value}<span className="text-slate-500">/{max}</span></span>
      </div>
      <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function SmScoreBadge({ score }: { score: number }) {
  const cls = score >= 80 ? "text-emerald-400 border-emerald-500/60 bg-emerald-950/50"
    : score >= 65 ? "text-cyan-400 border-cyan-500/60 bg-cyan-950/50"
    : score >= 50 ? "text-yellow-400 border-yellow-500/60 bg-yellow-950/50"
    : score >= 35 ? "text-orange-400 border-orange-500/60 bg-orange-950/50"
    : "text-red-400 border-red-500/60 bg-red-950/50";
  return (
    <div className={`inline-flex items-center justify-center border rounded-full w-11 h-11 text-sm font-bold ${cls}`}>
      {score}
    </div>
  );
}

function SmartMoneyTab() {
  const [tickerInput, setTickerInput] = useState(SM_DEFAULT);
  const [result, setResult] = useState<SmartMoneyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [countdown, setCountdown] = useState(60);
  const inputRef = useRef(tickerInput);
  inputRef.current = tickerInput;

  const runScan = useCallback(async () => {
    const tickers = inputRef.current.split(/[\s,]+/).filter(Boolean).map(t => t.toUpperCase()).slice(0, 50);
    setLoading(true);
    setMsg("");
    try {
      const data = await smartMoneyScan(tickers);
      setResult(data);
      setCountdown(60);
    } catch (e: any) {
      setMsg("Scan failed: " + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) { runScan(); return 60; }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [autoRefresh, runScan]);

  const board = result?.leaderboard ?? [];

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="flex-1">
            <label className="text-slate-400 text-xs mb-1 block">Tickers (comma-separated, up to 50)</label>
            <textarea
              value={tickerInput}
              onChange={e => setTickerInput(e.target.value)}
              rows={2}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-purple-500 resize-none"
              placeholder="AAPL, MSFT, NVDA…"
            />
          </div>
          <div className="flex flex-col gap-2 justify-end shrink-0">
            <button
              onClick={runScan}
              disabled={loading}
              className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg font-medium text-sm transition-colors flex items-center justify-center gap-2"
            >
              {loading ? <><Spinner /> Scanning…</> : "🏆 Run Leaderboard"}
            </button>
            <button
              onClick={() => setAutoRefresh(a => !a)}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${autoRefresh ? "border-green-600 text-green-400 bg-green-950/30" : "border-slate-700 text-slate-400 hover:text-slate-300"}`}
            >
              {autoRefresh ? `↻ Auto (${countdown}s)` : "↻ Auto-refresh off"}
            </button>
          </div>
        </div>
        {msg && <p className="mt-2 text-sm text-red-400">{msg}</p>}
      </div>

      {/* Email Signup Banner */}
      <EmailSignupBanner />

      {/* Leaderboard Table */}
      {board.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-white font-semibold">Smart Money Leaderboard</span>
              <span className="bg-purple-900/50 text-purple-300 text-xs px-2 py-0.5 rounded-full">{board.length} stocks</span>
            </div>
            {result?.timestamp && (
              <span className="text-slate-500 text-xs hidden sm:block">
                Updated {new Date(result.timestamp).toLocaleTimeString()}
              </span>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase">
                  <th className="text-left py-2.5 px-4 w-8">#</th>
                  <th className="text-left py-2.5 px-3">Ticker</th>
                  <th className="text-center py-2.5 px-3">Score</th>
                  <th className="text-left py-2.5 px-3 hidden md:table-cell">Signal</th>
                  <th className="text-right py-2.5 px-3">Win Rate</th>
                  <th className="text-right py-2.5 px-4 hidden sm:table-cell">Exp. Move</th>
                </tr>
              </thead>
              <tbody>
                {board.map((s, i) => {
                  const open = selected === s.ticker;
                  const dirColor = s.direction === "Bullish" ? "text-emerald-400" : s.direction === "Bearish" ? "text-red-400" : "text-yellow-400";
                  const dirArrow = s.direction === "Bullish" ? "▲" : s.direction === "Bearish" ? "▼" : "→";
                  return (
                    <React.Fragment key={s.ticker}>
                      <tr
                        onClick={() => setSelected(open ? null : s.ticker)}
                        className={`border-b border-slate-800/50 cursor-pointer transition-colors ${open ? "bg-purple-950/25" : "hover:bg-slate-800/40"}`}
                      >
                        <td className="py-3 px-4 text-slate-500 text-xs font-mono">{i + 1}</td>
                        <td className="py-3 px-3">
                          <div className="font-bold text-white">{s.ticker}</div>
                          <div className="text-slate-500 text-xs">${s.price.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
                        </td>
                        <td className="py-3 px-3 text-center">
                          <SmScoreBadge score={s.smart_money_score} />
                        </td>
                        <td className="py-3 px-3 hidden md:table-cell">
                          <div className={`text-xs font-medium ${dirColor}`}>{dirArrow} {s.signal}</div>
                          <div className="text-slate-500 text-xs mt-0.5">{s.confidence} confidence · RVOL {s.rvol}x</div>
                        </td>
                        <td className="py-3 px-3 text-right">
                          <span className={`font-semibold ${s.win_rate >= 60 ? "text-emerald-400" : s.win_rate >= 50 ? "text-yellow-400" : "text-red-400"}`}>
                            {s.win_rate.toFixed(0)}%
                          </span>
                          <div className={`text-xs ${s.avg_5d_return >= 0 ? "text-emerald-500" : "text-red-500"}`}>
                            {s.avg_5d_return >= 0 ? "+" : ""}{s.avg_5d_return.toFixed(1)}% avg
                          </div>
                        </td>
                        <td className="py-3 px-4 text-right hidden sm:table-cell">
                          <span className="text-slate-300 text-xs">{s.expected_move_low}%–{s.expected_move_high}%</span>
                          <div className="text-slate-500 text-xs">5-day</div>
                        </td>
                      </tr>

                      {/* Expanded detail */}
                      {open && (
                        <tr className="bg-purple-950/10 border-b border-purple-800/20">
                          <td colSpan={6} className="p-4">
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                              {/* Score Breakdown */}
                              <div className="space-y-4">
                                <div>
                                  <h4 className="text-white text-sm font-semibold mb-3">📊 Score Breakdown <span className="text-slate-500 font-normal">(out of 100)</span></h4>
                                  <div className="space-y-2.5">
                                    <SmScoreBar label={s.options_summary ? "Call Sweep (Real Vol/OI)" : "Call Sweep Proxy"}     value={s.score_breakdown.call_sweep}       max={25} color="#a855f7" />
                                    <SmScoreBar label={s.options_summary ? "Volume / OI (Real Data)"  : "Volume / OI"}          value={s.score_breakdown.volume_oi}        max={20} color="#06b6d4" />
                                    <SmScoreBar label="Ask-Side Aggression"  value={s.score_breakdown.ask_aggression}   max={15} color="#10b981" />
                                    <SmScoreBar label="Dark Pool Proxy"      value={s.score_breakdown.dark_pool}        max={15} color="#6366f1" />
                                    <SmScoreBar label="Sector Strength"      value={s.score_breakdown.sector_strength}  max={10} color="#f59e0b" />
                                    <SmScoreBar label="Historical Similarity" value={s.score_breakdown.historical}      max={15} color="#f97316" />
                                  </div>
                                </div>

                                {/* Real Options Chain Panel */}
                                {s.options_summary && (() => {
                                  const opts = s.options_summary as any;
                                  const todayStr = new Date().toISOString().slice(0, 10);
                                  const fmtExp = (d: string) => { try { return new Date(d + "T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric"}); } catch { return d; } };
                                  const daysOut = (d: string) => { try { return Math.round((new Date(d + "T12:00:00").getTime() - Date.now()) / 86400000); } catch { return 999; } };
                                  const tvs = opts.top_vol_strike, tve = opts.top_vol_expiry || opts.expiry || "";
                                  const tps = opts.top_prem_strike, tpe = opts.top_prem_expiry || opts.expiry || "";
                                  const tvc = opts.top_vol_contracts, tpk = opts.top_prem_value_k, tpc = opts.top_prem_contracts;
                                  const showVol  = tvs != null && tve !== todayStr;
                                  const showPrem = tps != null && tpe !== todayStr;
                                  return (
                                    <div className="bg-slate-900/80 border border-cyan-800/30 rounded-xl p-3.5">
                                      <div className="flex items-center justify-between mb-2.5">
                                        <span className="text-cyan-400 text-xs font-semibold">📡 Real Options Chain</span>
                                        <span className="text-slate-500 text-xs">15-min delayed</span>
                                      </div>

                                      {/* Actionable strikes — only non-0DTE */}
                                      {(showVol || showPrem) && (
                                        <div className="space-y-2 mb-3">
                                          {showVol && (
                                            <div className="border-l-4 border-emerald-500 bg-emerald-950/40 rounded-r-lg px-3 py-2">
                                              <div className="text-emerald-400 text-[9px] font-bold uppercase tracking-widest mb-1">⚡ Actionable · 🔥 Most Active Strike</div>
                                              <span className="text-emerald-300 text-lg font-black">${tvs}</span>
                                              <span className="text-emerald-600 text-xs font-semibold ml-2">
                                                Exp <strong className="text-emerald-400">{fmtExp(tve)}</strong>
                                                {daysOut(tve) < 999 && <> · {daysOut(tve)}d out</>}
                                                {tvc && <> · {tvc.toLocaleString()} contracts</>}
                                              </span>
                                            </div>
                                          )}
                                          {showPrem && (
                                            <div className="border-l-4 border-orange-500 bg-orange-950/40 rounded-r-lg px-3 py-2">
                                              <div className="text-orange-400 text-[9px] font-bold uppercase tracking-widest mb-1">⚡ Actionable · 💰 Most Premium Traded</div>
                                              <span className="text-orange-300 text-lg font-black">${tps}</span>
                                              <span className="text-orange-600 text-xs font-semibold ml-2">
                                                Exp <strong className="text-orange-400">{fmtExp(tpe)}</strong>
                                                {daysOut(tpe) < 999 && <> · {daysOut(tpe)}d out</>}
                                                {tpk != null && <> · {tpk >= 1000 ? `$${(tpk/1000).toFixed(1)}M` : `$${tpk.toFixed(0)}K`}</>}
                                                {tpc && <> · {tpc.toLocaleString()} contracts</>}
                                              </span>
                                            </div>
                                          )}
                                        </div>
                                      )}

                                      <div className="grid grid-cols-2 gap-2 text-xs">
                                        {[
                                          { label: "Call Vol / OI",    value: opts.call_vol_oi.toFixed(3),   highlight: opts.call_vol_oi >= 0.5 ? "text-purple-400" : opts.call_vol_oi >= 0.2 ? "text-yellow-400" : "text-slate-300" },
                                          { label: "Put Vol / OI",     value: opts.put_vol_oi.toFixed(3),    highlight: "text-slate-300" },
                                          { label: "Call / Put Vol",   value: `${opts.call_put_ratio.toFixed(2)}x`, highlight: opts.call_put_ratio >= 2 ? "text-emerald-400" : opts.call_put_ratio >= 1.3 ? "text-yellow-400" : "text-slate-300" },
                                          { label: "Call / Put OI",    value: `${opts.cp_oi_ratio.toFixed(2)}x`,   highlight: opts.cp_oi_ratio >= 1.5 ? "text-emerald-400" : "text-slate-300" },
                                          { label: "Total Call Vol",   value: opts.total_call_vol.toLocaleString(), highlight: "text-slate-300" },
                                          { label: "Total Call OI",    value: opts.total_call_oi.toLocaleString(),  highlight: "text-slate-300" },
                                          { label: "OTM Call Vol",     value: opts.otm_call_vol.toLocaleString(),   highlight: opts.otm_call_vol > 0 ? "text-cyan-400" : "text-slate-300" },
                                          { label: "ATM IV",           value: opts.atm_iv != null ? `${opts.atm_iv}%` : "N/A", highlight: opts.atm_iv != null && opts.atm_iv > 60 ? "text-red-400" : opts.atm_iv != null && opts.atm_iv > 35 ? "text-yellow-400" : "text-slate-300" },
                                        ].map(item => (
                                          <div key={item.label} className="bg-slate-800/60 rounded-lg p-2">
                                            <div className="text-slate-500 text-xs">{item.label}</div>
                                            <div className={`font-mono font-semibold mt-0.5 ${item.highlight}`}>{item.value}</div>
                                          </div>
                                        ))}
                                      </div>
                                      <div className="mt-2 text-slate-600 text-xs">
                                        Vol/OI &gt; 0.5 = unusual activity · &gt; 1.0 = sweep territory · Call/Put &gt; 2x = directional conviction
                                      </div>
                                    </div>
                                  );
                                })()}
                              </div>

                              {/* Stats + Thesis */}
                              <div className="space-y-3">
                                <h4 className="text-white text-sm font-semibold">📋 Final Analysis</h4>
                                <div className="grid grid-cols-2 gap-2 text-sm">
                                  {[
                                    { label: "Signal",           value: s.signal,    color: dirColor },
                                    { label: "Suggested Trade",  value: s.direction, color: dirColor },
                                    { label: "Confidence",       value: s.confidence, color: "text-slate-200" },
                                    { label: "Risk Rating",      value: s.risk_rating, color: s.risk_rating === "Low" ? "text-emerald-400" : s.risk_rating === "Moderate" ? "text-yellow-400" : "text-red-400" },
                                    { label: "Avg 5-Day Return", value: `${s.avg_5d_return >= 0 ? "+" : ""}${s.avg_5d_return.toFixed(1)}%`, color: s.avg_5d_return >= 0 ? "text-emerald-400" : "text-red-400" },
                                    { label: "Historical Occurrences", value: `${s.occurrences}`, color: "text-slate-200" },
                                    { label: "Win Rate",         value: `${s.win_rate.toFixed(0)}%`, color: s.win_rate >= 60 ? "text-emerald-400" : s.win_rate >= 50 ? "text-yellow-400" : "text-red-400" },
                                    { label: "Expected Move",    value: `${s.expected_move_low}%–${s.expected_move_high}%`, color: "text-slate-200" },
                                  ].map(item => (
                                    <div key={item.label} className="bg-slate-900/70 rounded-lg p-2.5">
                                      <div className="text-slate-500 text-xs">{item.label}</div>
                                      <div className={`font-semibold text-sm mt-0.5 leading-tight ${item.color}`}>{item.value}</div>
                                    </div>
                                  ))}
                                </div>

                                {/* AI Thesis */}
                                <div className="bg-slate-900/60 border border-purple-800/30 rounded-xl p-3.5">
                                  <div className="flex items-center gap-1.5 mb-2">
                                    <span className="text-purple-400 text-xs font-semibold">🤖 AI Trade Thesis</span>
                                  </div>
                                  <p className="text-slate-300 text-sm leading-relaxed">{s.thesis}</p>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && board.length === 0 && !msg && (
        <div className="text-center py-16 text-slate-500">
          <div className="text-5xl mb-4">🏆</div>
          <p className="text-lg font-medium text-slate-400 mb-1">Click "Run Leaderboard" to scan your watchlist</p>
          <p className="text-sm max-w-md mx-auto">
            Ranks each stock 0–100 by Smart Money Score — combining call sweep flow, dark pool accumulation,
            volume anomalies, sector strength, and historical win rate from similar setups.
          </p>
        </div>
      )}
    </div>
  );
}

function PropDeskTab() {
  const [tickerInput, setTickerInput] = useState(DEFAULT_SCAN.join(", "));
  const [result, setResult] = useState<PropDeskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [tradeMsg, setTradeMsg] = useState<string>("");

  const parsedTickers = tickerInput.split(/[\s,]+/).filter(Boolean).map(t => t.toUpperCase()).slice(0, 20);

  const runScan = async () => {
    setLoading(true);
    setTradeMsg("");
    try {
      const data = await propScan(parsedTickers);
      setResult(data);
    } catch (e: any) {
      setTradeMsg("Scan failed: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const executeTrade = async (ticker: string, action: "buy" | "sell") => {
    setTradeMsg("");
    try {
      const res = await propTrade(ticker, action);
      if (res.error) {
        setTradeMsg(`❌ ${res.error}`);
      } else if (action === "buy") {
        setTradeMsg(`✅ Bought 10 shares of ${ticker} @ $${res.price?.toFixed(2)} | Cash: $${res.cash?.toLocaleString("en-US", { minimumFractionDigits: 2 })}`);
      } else {
        const pnlStr = res.pnl != null ? ` | P&L: ${res.pnl >= 0 ? "+" : ""}$${res.pnl.toFixed(2)}` : "";
        setTradeMsg(`✅ Sold ${ticker} @ $${res.price?.toFixed(2)}${pnlStr} | Cash: $${res.cash?.toLocaleString("en-US", { minimumFractionDigits: 2 })}`);
      }
      const refreshed = result ? await propScan(parsedTickers) : null;
      if (refreshed) setResult(refreshed);
    } catch (e: any) {
      setTradeMsg("❌ " + e.message);
    }
  };

  const handleReset = async () => {
    await propReset();
    setTradeMsg("🔄 Paper account reset to $100,000");
    if (result) {
      const refreshed = await propScan(parsedTickers);
      setResult(refreshed);
    }
  };

  const totalUnrealized = result
    ? Object.values(result.positions).reduce((s, p) => s + p.unrealized_pnl, 0)
    : 0;

  return (
    <div className="space-y-4">
      {/* Header controls */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="text-white font-semibold">Prop Desk Simulator</div>
          <button onClick={handleReset} className="text-xs text-slate-400 hover:text-red-400 border border-slate-700 hover:border-red-700 px-3 py-1 rounded-lg transition-colors">Reset Account</button>
        </div>
        <div className="text-slate-400 text-sm mb-3">Tickers (comma-separated)</div>
        <div className="flex gap-2">
          <input value={tickerInput} onChange={e => setTickerInput(e.target.value.toUpperCase())}
            className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500" />
          <button onClick={runScan} disabled={loading}
            className="bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2">
            {loading && <Spinner />} Run Signals
          </button>
        </div>
        {tradeMsg && <div className="mt-3 text-sm text-slate-300 bg-slate-800 rounded-lg px-4 py-2.5">{tradeMsg}</div>}
      </div>

      {loading && <div className="flex items-center justify-center py-16 gap-3 text-slate-400"><Spinner /> Running prop signals…</div>}

      {result && !loading && (
        <>
          {/* Account summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: "Cash", value: `$${result.cash.toLocaleString("en-US", { minimumFractionDigits: 2 })}`, color: "text-slate-200" },
              { label: "Realized P&L", value: `${result.realized_pnl >= 0 ? "+" : ""}$${result.realized_pnl.toFixed(2)}`, color: result.realized_pnl >= 0 ? "text-emerald-400" : "text-red-400" },
              { label: "Unrealized P&L", value: `${totalUnrealized >= 0 ? "+" : ""}$${totalUnrealized.toFixed(2)}`, color: totalUnrealized >= 0 ? "text-emerald-400" : "text-red-400" },
              { label: "Open Positions", value: String(Object.keys(result.positions).length), color: "text-slate-200" },
            ].map(item => (
              <div key={item.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                <div className="text-slate-500 text-xs mb-1">{item.label}</div>
                <div className={`text-lg font-bold ${item.color}`}>{item.value}</div>
              </div>
            ))}
          </div>

          {/* Open positions */}
          {Object.keys(result.positions).length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-4">Open Positions</div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-slate-800 text-slate-400 text-xs uppercase">
                    <th className="text-left py-2 px-3">Ticker</th>
                    <th className="text-right py-2 px-3">Size</th>
                    <th className="text-right py-2 px-3">Entry</th>
                    <th className="text-right py-2 px-3">Current</th>
                    <th className="text-right py-2 px-3">Unrealized P&L</th>
                    <th className="text-right py-2 px-3">Action</th>
                  </tr></thead>
                  <tbody>
                    {Object.entries(result.positions).map(([ticker, pos]) => (
                      <tr key={ticker} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                        <td className="py-2.5 px-3 font-semibold text-white">{ticker}</td>
                        <td className="text-right py-2.5 px-3 text-slate-300">{pos.size}</td>
                        <td className="text-right py-2.5 px-3 text-slate-300">${pos.entry.toFixed(2)}</td>
                        <td className="text-right py-2.5 px-3 text-slate-300">${pos.current_price.toFixed(2)}</td>
                        <td className={`text-right py-2.5 px-3 font-medium ${pos.unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {pos.unrealized_pnl >= 0 ? "+" : ""}${pos.unrealized_pnl.toFixed(2)}
                        </td>
                        <td className="text-right py-2.5 px-3">
                          <button onClick={() => executeTrade(ticker, "sell")}
                            className="bg-red-600 hover:bg-red-500 text-white text-xs px-3 py-1 rounded-lg font-medium transition-colors">
                            Sell
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Signals table */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="text-slate-400 text-sm mb-4">{result.signals.length} signals — sorted by prop score</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider">
                  <th className="text-left py-2 px-3">Ticker</th>
                  <th className="text-right py-2 px-3">Price</th>
                  <th className="text-center py-2 px-3">Regime</th>
                  <th className="text-right py-2 px-3">Score</th>
                  <th className="text-right py-2 px-3">ML%</th>
                  <th className="text-left py-2 px-3 hidden md:table-cell">Factors</th>
                  <th className="text-right py-2 px-3">Trade</th>
                </tr></thead>
                <tbody>
                  {result.signals.map(sig => {
                    const inPosition = sig.ticker in result.positions;
                    return (
                      <tr key={sig.ticker} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                        <td className="py-2.5 px-3 font-semibold text-white">{sig.ticker}</td>
                        <td className="text-right py-2.5 px-3 text-slate-300">${sig.price.toFixed(2)}</td>
                        <td className="py-2.5 px-3 text-center">
                          <span className={`text-xs px-2 py-0.5 rounded border font-medium ${REGIME_COLORS[sig.regime]}`}>
                            {REGIME_ICONS[sig.regime]} {sig.regime}
                          </span>
                        </td>
                        <td className="text-right py-2.5 px-3">
                          <span className={`font-bold text-base ${sig.score >= 8 ? "text-emerald-400" : sig.score >= 6 ? "text-green-400" : sig.score >= 4 ? "text-yellow-400" : "text-red-400"}`}>
                            {sig.score}
                          </span>
                        </td>
                        <td className={`text-right py-2.5 px-3 font-medium ${(sig.ml_probability ?? 50) >= 60 ? "text-emerald-400" : (sig.ml_probability ?? 50) <= 40 ? "text-red-400" : "text-slate-300"}`}>
                          {sig.ml_probability != null ? `${sig.ml_probability.toFixed(1)}%` : "—"}
                        </td>
                        <td className="py-2.5 px-3 hidden md:table-cell">
                          <div className="space-y-1 min-w-[180px]">
                            <PropScoreBar value={sig.trend}    label="Trend"    color="#10b981" />
                            <PropScoreBar value={sig.momentum} label="Momentum" color="#3b82f6" />
                            <PropScoreBar value={sig.volume}   label="Volume"   color="#f59e0b" />
                          </div>
                        </td>
                        <td className="text-right py-2.5 px-3">
                          {inPosition ? (
                            <button onClick={() => executeTrade(sig.ticker, "sell")}
                              className="bg-red-600 hover:bg-red-500 text-white text-xs px-3 py-1 rounded-lg font-medium transition-colors">
                              Sell
                            </button>
                          ) : (
                            <button onClick={() => executeTrade(sig.ticker, "buy")}
                              disabled={sig.score < 4}
                              className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-30 disabled:cursor-not-allowed text-white text-xs px-3 py-1 rounded-lg font-medium transition-colors">
                              Buy 10
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Trade log */}
          {result.trades.length > 0 && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-4">Trade Log (last 20)</div>
              <div className="space-y-2">
                {[...result.trades].reverse().map((t, i) => (
                  <div key={i} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-800/50">
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-white">{t.ticker}</span>
                      <span className="text-slate-400 text-xs">{t.date}</span>
                    </div>
                    <span className={`font-medium ${t.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {!result && !loading && (
        <div className="text-center py-16 text-slate-500">Click "Run Signals" to generate prop desk signals for your watchlist</div>
      )}
    </div>
  );
}

// ─── Market Overview ─────────────────────────────────────────────────────────

function MarketTab() {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["market-overview"],
    queryFn: fetchMarketOverview,
    staleTime: 1000 * 60 * 5,
  });

  const chgColor  = (v: number) => v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-slate-400";
  const chgBg     = (v: number) => {
    if (v >=  1.5) return "bg-emerald-700";
    if (v >=  0.5) return "bg-emerald-900";
    if (v >=  0)   return "bg-emerald-950";
    if (v >= -0.5) return "bg-red-950";
    if (v >= -1.5) return "bg-red-900";
    return "bg-red-700";
  };
  const fmtChg = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;

  const ad   = data?.advance_decline;
  const total = (ad?.up ?? 0) + (ad?.down ?? 0) + (ad?.unchanged ?? 0);
  const upPct = total > 0 ? Math.round((ad!.up   / total) * 100) : 0;
  const dnPct = total > 0 ? Math.round((ad!.down / total) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between">
        <div>
          <h3 className="text-white font-semibold">📊 Market Overview</h3>
          <p className="text-slate-500 text-xs mt-0.5">Sector heatmap · Major indices · Advance/Decline breadth</p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-white transition-colors disabled:opacity-50">
          {isFetching ? "Loading…" : "↻ Refresh"}
        </button>
      </div>

      {isLoading && (
        <div className="text-center py-16 text-slate-500">
          <div className="text-3xl mb-3 animate-spin inline-block">⟳</div>
          <p>Fetching market data…</p>
        </div>
      )}

      {data && (
        <>
          {/* Indices Row */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
            {data.indices.map(idx => (
              <div key={idx.ticker} className="bg-slate-900 border border-slate-800 rounded-xl p-3 text-center">
                <div className="text-slate-400 text-xs mb-1">{idx.label}</div>
                <div className="text-white font-bold text-base">${idx.price.toLocaleString()}</div>
                <div className={`text-sm font-bold mt-0.5 ${chgColor(idx.change_pct)}`}>{fmtChg(idx.change_pct)}</div>
              </div>
            ))}
          </div>

          {/* Advance / Decline */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-white font-semibold text-sm">📈 Advance / Decline Breadth</h4>
              <span className="text-slate-500 text-xs">{total} stocks tracked</span>
            </div>
            <div className="flex rounded-lg overflow-hidden h-8 mb-3">
              <div className="bg-emerald-600 flex items-center justify-center text-white text-xs font-bold transition-all"
                style={{ width: `${upPct}%` }}>{ad!.up > 0 ? ad!.up : ""}</div>
              <div className="bg-slate-700 flex items-center justify-center text-slate-300 text-xs transition-all"
                style={{ width: `${100 - upPct - dnPct}%` }}>{ad!.unchanged > 0 && ad!.unchanged}</div>
              <div className="bg-red-600 flex items-center justify-center text-white text-xs font-bold transition-all"
                style={{ width: `${dnPct}%` }}>{ad!.down > 0 ? ad!.down : ""}</div>
            </div>
            <div className="flex gap-4 text-xs">
              <span className="text-emerald-400 font-semibold">▲ Advancing: {ad!.up} ({upPct}%)</span>
              <span className="text-slate-400">→ Unchanged: {ad!.unchanged}</span>
              <span className="text-red-400 font-semibold">▼ Declining: {ad!.down} ({dnPct}%)</span>
            </div>
            <p className="text-slate-600 text-xs mt-2">
              {upPct >= 60 ? "🟢 Broad market strength — majority of stocks advancing"
               : dnPct >= 60 ? "🔴 Broad market weakness — majority of stocks declining"
               : "🟡 Mixed breadth — market indecisive"}
            </p>
          </div>

          {/* Sector Heatmap */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <h4 className="text-white font-semibold text-sm mb-3">🗺️ Sector Heatmap</h4>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {data.sectors.map(s => (
                <div key={s.ticker} className={`rounded-lg p-3 ${chgBg(s.change_pct)}`}>
                  <div className="text-white text-xs font-semibold">{s.name}</div>
                  <div className={`text-lg font-black mt-1 ${chgColor(s.change_pct)}`}>{fmtChg(s.change_pct)}</div>
                  <div className="text-slate-400 text-xs">{s.ticker} · ${s.price.toFixed(2)}</div>
                </div>
              ))}
            </div>
            <p className="text-slate-600 text-xs mt-3">Sorted best → worst · Data via yfinance (15-min delayed)</p>
          </div>
        </>
      )}
    </div>
  );
}

// ─── Bull Flow Top 20 ────────────────────────────────────────────────────────

function BreakoutTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<BreakoutSignal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const data = await fetchBreakoutRadar();
      setResults(data.results);
      setScanned(data.scanned);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { run(); }, []);

  const scoreColor = (s: number) =>
    s >= 80 ? "text-emerald-300" : s >= 60 ? "text-green-400" : s >= 40 ? "text-yellow-400" : "text-slate-400";
  const scoreBg = (s: number) =>
    s >= 80 ? "bg-emerald-400" : s >= 60 ? "bg-green-500" : s >= 40 ? "bg-yellow-500" : "bg-slate-500";
  const rankBg = (rank: number) =>
    rank === 1 ? "bg-yellow-900/20 border-yellow-700/30"
    : rank === 2 ? "bg-slate-700/20 border-slate-600/30"
    : rank === 3 ? "bg-orange-900/20 border-orange-700/30"
    : "bg-slate-900/40 border-slate-800/40";

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div>
            <h2 className="text-white font-bold text-lg">🚀 Breakout Radar</h2>
            <p className="text-slate-400 text-sm mt-1">
              Stocks showing technical breakout signals across MACD, RSI, volume surge, and 52-week high proximity. Breakout Score is 0–100 composite.
            </p>
          </div>
          <button
            onClick={run} disabled={loading}
            className="shrink-0 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "🚀 Run Scan"}
          </button>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-2 mt-3">
          {[
            { label: "MACD ✓", desc: "MACD above signal line", color: "bg-blue-900/40 text-blue-300 border-blue-700/40" },
            { label: "MACD ⚡", desc: "Fresh crossover today", color: "bg-purple-900/40 text-purple-300 border-purple-700/40" },
            { label: "RSI ✓",  desc: "RSI in momentum zone 55–70", color: "bg-cyan-900/40 text-cyan-300 border-cyan-700/40" },
            { label: "Vol ⬆",  desc: "Volume surge vs 20d avg", color: "bg-orange-900/40 text-orange-300 border-orange-700/40" },
            { label: "Near High", desc: "Within 7% of 52W high", color: "bg-emerald-900/40 text-emerald-300 border-emerald-700/40" },
            { label: "Golden ✕", desc: "SMA50 > SMA200", color: "bg-yellow-900/40 text-yellow-300 border-yellow-700/40" },
          ].map(b => (
            <span key={b.label} title={b.desc} className={`text-xs font-bold px-2 py-0.5 rounded-full border ${b.color}`}>{b.label}</span>
          ))}
        </div>

        {lastRun && <p className="text-slate-600 text-xs mt-2">Scanned {scanned} tickers · {lastRun.toLocaleTimeString()}</p>}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {!loading && results.length === 0 && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🚀</div>
          <div className="font-semibold text-slate-400 mb-1">Run the scan to find breakout candidates</div>
          <div className="text-sm">Scores every ticker across MACD, RSI, volume, and 52W high proximity</div>
        </div>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          {results.map(row => (
            <button
              key={row.ticker}
              onClick={() => onSelectTicker(row.ticker)}
              className={`w-full text-left rounded-xl border p-4 transition-all hover:border-emerald-700/50 hover:bg-emerald-950/10 ${rankBg(row.rank)}`}
            >
              <div className="flex items-center gap-4">
                {/* Rank */}
                <span className="text-xl w-8 text-center shrink-0">
                  {row.rank === 1 ? "🥇" : row.rank === 2 ? "🥈" : row.rank === 3 ? "🥉" : `#${row.rank}`}
                </span>

                {/* Ticker + signals */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <span className="text-white font-black text-lg">{row.ticker}</span>
                    <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
                    {row.macd_cross && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-purple-900/40 text-purple-300 border border-purple-700/40">⚡ MACD Cross</span>
                    )}
                    {!row.macd_cross && row.macd_bullish && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-blue-900/40 text-blue-300 border border-blue-700/40">MACD ✓</span>
                    )}
                    {row.rsi >= 55 && row.rsi <= 70 && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-cyan-900/40 text-cyan-300 border border-cyan-700/40">RSI {row.rsi.toFixed(0)}</span>
                    )}
                    {row.volume_ratio >= 1.5 && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-orange-900/40 text-orange-300 border border-orange-700/40">Vol {row.volume_ratio.toFixed(1)}x ⬆</span>
                    )}
                    {row.pct_from_52w_high >= -7 && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-900/40 text-emerald-300 border border-emerald-700/40">
                        {row.pct_from_52w_high === 0 ? "🏆 52W High" : `Near High ${row.pct_from_52w_high.toFixed(1)}%`}
                      </span>
                    )}
                    {row.golden_cross && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-yellow-900/40 text-yellow-300 border border-yellow-700/40">Golden ✕</span>
                    )}
                  </div>

                  {/* Score bar */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1 bg-slate-800 rounded-full h-1.5 max-w-48">
                      <div
                        className={`h-1.5 rounded-full transition-all ${scoreBg(row.breakout_score)}`}
                        style={{ width: `${Math.min(row.breakout_score, 100)}%` }}
                      />
                    </div>
                    <span className={`text-xs font-semibold ${scoreColor(row.breakout_score)}`}>
                      {row.breakout_score}/100
                    </span>
                  </div>
                </div>

                {/* Right: score + SMAs */}
                <div className="text-right shrink-0">
                  <div className={`font-black text-2xl ${scoreColor(row.breakout_score)}`}>{row.breakout_score}</div>
                  <div className="flex gap-1 mt-0.5 justify-end">
                    <span className={`text-xs px-1.5 py-0.5 rounded ${row.above_sma50 ? "bg-emerald-900/30 text-emerald-400" : "bg-slate-800 text-slate-600"}`}>50</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${row.above_sma200 ? "bg-emerald-900/30 text-emerald-400" : "bg-slate-800 text-slate-600"}`}>200</span>
                  </div>
                </div>
              </div>
            </button>
          ))}
          <p className="text-center text-slate-600 text-xs pt-2">
            Tap any stock to deep-dive in Stock Lookup · Technical data from yfinance
          </p>
        </div>
      )}
    </div>
  );
}

function SqueezeTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<SqueezeSignal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const data = await fetchSqueezeSignals();
      setResults(data.results);
      setScanned(data.scanned);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { run(); }, []);

  const scoreColor = (s: number) =>
    s >= 80 ? "text-red-400"
    : s >= 60 ? "text-orange-400"
    : s >= 40 ? "text-yellow-400"
    : "text-slate-400";

  const scoreBg = (s: number) =>
    s >= 80 ? "bg-red-500"
    : s >= 60 ? "bg-orange-500"
    : s >= 40 ? "bg-yellow-500"
    : "bg-slate-500";

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div>
            <h2 className="text-white font-bold text-lg flex items-center gap-2">💥 Short Squeeze Detector</h2>
            <p className="text-slate-400 text-sm mt-1">
              Stocks with high short interest + bullish options flow — classic squeeze setup. Squeeze Score combines short float (max 50 pts) + options conviction (max 50 pts).
            </p>
          </div>
          <button
            onClick={run} disabled={loading}
            className="shrink-0 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "💥 Run Scan"}
          </button>
        </div>
        {lastRun && <p className="text-slate-600 text-xs">Scanned {scanned} tickers · {lastRun.toLocaleTimeString()}</p>}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {!loading && results.length === 0 && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">💥</div>
          <div className="font-semibold text-slate-400 mb-1">Run the scan to find squeeze candidates</div>
          <div className="text-sm">Combines short interest + options flow to surface potential squeezes</div>
        </div>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          {results.map(row => (
            <button
              key={row.ticker}
              onClick={() => onSelectTicker(row.ticker)}
              className="w-full text-left bg-slate-900/60 hover:bg-slate-800/60 border border-slate-800 hover:border-red-700/40 rounded-xl p-4 transition-all"
            >
              <div className="flex items-center gap-4">
                <span className="text-slate-500 text-sm w-6 shrink-0">#{row.rank}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-white font-black text-lg">{row.ticker}</span>
                    <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
                    <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-red-900/40 text-red-300 border border-red-700/40">
                      🔥 {row.short_float_pct}% short
                    </span>
                    {row.call_put_ratio >= 2 && (
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-900/30 text-emerald-400 border border-emerald-700/30">
                        {row.call_put_ratio.toFixed(1)}x C/P
                      </span>
                    )}
                  </div>
                  {/* Score bar */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1 bg-slate-800 rounded-full h-2 max-w-48">
                      <div
                        className={`h-2 rounded-full ${scoreBg(row.squeeze_score)}`}
                        style={{ width: `${Math.min(row.squeeze_score, 100)}%` }}
                      />
                    </div>
                    <span className={`text-xs font-bold ${scoreColor(row.squeeze_score)}`}>
                      {row.squeeze_score.toFixed(0)} Squeeze Score
                    </span>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className={`font-black text-lg ${scoreColor(row.squeeze_score)}`}>
                    {row.squeeze_score.toFixed(0)}
                  </div>
                  <div className="text-slate-600 text-xs">{row.short_ratio.toFixed(1)}d to cover</div>
                </div>
              </div>
            </button>
          ))}
          <p className="text-center text-slate-600 text-xs pt-2">
            Tap any stock to analyze it · Short data from yfinance · Scores are relative, not absolute
          </p>
        </div>
      )}
    </div>
  );
}

function InsidersTab() {
  const [trades,  setTrades]  = useState<InsiderTrade[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const [days,    setDays]    = useState(30);
  const [filter,  setFilter]  = useState<"all"|"Buy"|"Sell">("all");

  const run = async (d = days) => {
    setLoading(true); setError(null);
    try {
      const data = await fetchInsiderTrades(d);
      setTrades(data.trades);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Fetch failed");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { run(); }, []);

  const displayed = filter === "all" ? trades : trades.filter(t => t.trade_type === filter);
  const fmtVal = (v: number) =>
    v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(1)}M`
    : v >= 1_000 ? `$${(v / 1_000).toFixed(0)}K`
    : `$${v.toLocaleString()}`;

  return (
    <div className="space-y-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-white font-bold text-lg flex items-center gap-2">🏢 C-Suite Insider Trades</h2>
            <p className="text-slate-400 text-sm mt-1">
              Form 4 filings from SEC EDGAR. CEOs, CFOs, and directors putting their own money in — or cashing out.
            </p>
          </div>
          <button
            onClick={() => run(days)} disabled={loading}
            className="shrink-0 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Loading…</> : "🔄 Refresh"}
          </button>
        </div>

        <div className="flex gap-2 flex-wrap">
          {([30, 14, 7] as const).map(d => (
            <button key={d} onClick={() => { setDays(d); run(d); }}
              className={`px-3 py-1 rounded-lg text-xs font-bold border transition-colors ${days === d ? "bg-blue-700 border-blue-600 text-white" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}>
              {d}d
            </button>
          ))}
          <span className="w-px bg-slate-700 mx-1" />
          {(["all", "Buy", "Sell"] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-3 py-1 rounded-lg text-xs font-bold border transition-colors ${
                filter === f
                  ? f === "Buy" ? "bg-emerald-700 border-emerald-600 text-white"
                    : f === "Sell" ? "bg-red-800 border-red-700 text-white"
                    : "bg-slate-700 border-slate-600 text-white"
                  : "border-slate-700 text-slate-400 hover:text-slate-200"
              }`}>
              {f === "all" ? "All" : f === "Buy" ? "🟢 Buys" : "🔴 Sells"}
            </button>
          ))}
        </div>
        {lastRun && <p className="text-slate-600 text-xs mt-2">Fetched via SEC EDGAR · {lastRun.toLocaleTimeString()}</p>}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {!loading && displayed.length === 0 && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🏢</div>
          <div className="font-semibold text-slate-400 mb-1">
            {trades.length === 0 ? "Loading Form 4 filings from SEC EDGAR…" : "No trades match current filter"}
          </div>
          <div className="text-sm">SEC EDGAR may take a moment to respond</div>
        </div>
      )}

      {displayed.length > 0 && (
        <div className="space-y-2">
          {displayed.map((t, i) => (
            <div key={i} className={`rounded-xl border p-4 ${t.trade_type === "Buy" ? "bg-emerald-950/20 border-emerald-800/30" : "bg-red-950/20 border-red-800/30"}`}>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3 min-w-0">
                  <span className={`text-xs font-black px-2.5 py-1 rounded-full ${t.trade_type === "Buy" ? "bg-emerald-600 text-white" : "bg-red-700 text-white"}`}>
                    {t.trade_type === "Buy" ? "BUY" : "SELL"}
                  </span>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-white font-black text-lg">{t.ticker}</span>
                      <span className="text-slate-400 text-sm truncate">{t.insider_name}</span>
                      {t.title && (
                        <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400">{t.title}</span>
                      )}
                    </div>
                    <div className="text-slate-500 text-xs mt-0.5">
                      {t.shares.toLocaleString()} shares @ ${t.price.toFixed(2)} · {t.date}
                    </div>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className={`font-black text-lg ${t.trade_type === "Buy" ? "text-emerald-400" : "text-red-400"}`}>
                    {fmtVal(t.value)}
                  </div>
                  <div className="text-slate-600 text-xs">total value</div>
                </div>
              </div>
            </div>
          ))}
          <p className="text-center text-slate-600 text-xs pt-2">
            Data sourced from SEC EDGAR Form 4 filings · Not financial advice
          </p>
        </div>
      )}
    </div>
  );
}

// ---- Signal Outcome Tracker Tab ------------------------------------------
function OutcomesTab() {
  const [data, setData]       = useState<{ outcomes: SignalOutcome[]; count: number; win_rates: { t3: number | null; t5: number | null; t10: number | null } } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try {
      const d = await fetchSignalOutcomes();
      setData(d);
    } catch (e: any) {
      setError(e.message ?? "Failed to load outcomes");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const fmtDate = (s: string) => {
    const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  const pctColor = (v: number | null) => {
    if (v === null) return "text-slate-600";
    if (v > 2)  return "text-emerald-400";
    if (v > 0)  return "text-emerald-600";
    if (v < -2) return "text-red-400";
    return "text-red-600";
  };

  const winBadge = (w: boolean | null) => {
    if (w === null) return <span className="text-slate-600 text-xs">–</span>;
    return w
      ? <span className="text-xs font-bold text-emerald-400">✓ Win</span>
      : <span className="text-xs font-bold text-red-400">✗ Loss</span>;
  };

  const WinRatePill = ({ rate, label }: { rate: number | null; label: string }) => (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-center">
      <div className="text-slate-500 text-xs uppercase tracking-widest mb-1">{label}</div>
      {rate === null
        ? <div className="text-slate-600 text-2xl font-black">–</div>
        : <div className={`text-2xl font-black ${rate >= 60 ? "text-emerald-400" : rate >= 50 ? "text-yellow-400" : "text-red-400"}`}>
            {rate}%
          </div>}
      <div className="text-slate-600 text-xs mt-0.5">win rate</div>
    </div>
  );

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-1">
          <div>
            <h2 className="text-white font-bold text-lg flex items-center gap-2">📈 Signal Outcome Tracker</h2>
            <p className="text-slate-400 text-sm mt-1">
              What happened to bullish flow signals (C/P ≥ 2×) at T+3, T+5, and T+10 trading days.
              Smart money positions for moves 3-10 days out — this is how you measure edge.
            </p>
          </div>
          <button onClick={load} disabled={loading}
            className="shrink-0 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-bold transition-colors flex items-center gap-2">
            {loading ? <><Spinner /> Loading…</> : "↻ Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 text-red-300 text-sm">{error}</div>}

      {loading && (
        <div className="flex items-center justify-center py-20 gap-3 text-slate-400">
          <Spinner /> Fetching outcomes — looking up historical prices…
        </div>
      )}

      {!loading && data && (
        <>
          {/* Win Rate Summary */}
          <div className="grid grid-cols-3 gap-3">
            <WinRatePill rate={data.win_rates.t3}  label="T+3 days" />
            <WinRatePill rate={data.win_rates.t5}  label="T+5 days" />
            <WinRatePill rate={data.win_rates.t10} label="T+10 days" />
          </div>

          {/* Outcomes Table */}
          {data.outcomes.length === 0 ? (
            <div className="text-center py-16 text-slate-500">
              <div className="text-4xl mb-3">📊</div>
              <div className="font-semibold text-slate-400 mb-1">No outcomes yet</div>
              <div className="text-sm">Run the Bull Flow scan first. Outcomes appear after 3 trading days.</div>
            </div>
          ) : (
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-800">
                <span className="text-white font-semibold text-sm">{data.count} signals tracked</span>
                <span className="text-slate-600 text-xs ml-2">· Bullish flow C/P ≥ 2× · last 45 days</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" style={{ minWidth: "680px" }}>
                  <thead>
                    <tr className="border-b border-slate-800 text-slate-500 text-xs uppercase tracking-wider">
                      <th className="text-left px-4 py-3">Ticker</th>
                      <th className="text-left px-3 py-3">Date</th>
                      <th className="text-right px-3 py-3">Price</th>
                      <th className="text-right px-3 py-3">C/P</th>
                      <th className="text-right px-3 py-3">T+3</th>
                      <th className="text-right px-3 py-3">T+5</th>
                      <th className="text-right px-3 py-3">T+10</th>
                      <th className="text-center px-4 py-3">Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.outcomes.map((o, i) => (
                      <tr key={`${o.ticker}-${o.signal_date}-${i}`}
                        className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-black text-white">{o.ticker}</div>
                          {o.premium_m != null && (
                            <div className="text-slate-600 text-xs">${o.premium_m.toFixed(1)}M flow</div>
                          )}
                        </td>
                        <td className="px-3 py-3 text-slate-400 text-xs">{fmtDate(o.signal_date)}</td>
                        <td className="px-3 py-3 text-right text-slate-300">${o.price_at_signal.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className="text-emerald-400 font-bold">{o.call_put_ratio.toFixed(1)}x</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          {o.t3_pct !== null
                            ? <span className={`font-semibold ${pctColor(o.t3_pct)}`}>{o.t3_pct > 0 ? "+" : ""}{o.t3_pct.toFixed(1)}%</span>
                            : <span className="text-slate-600">–</span>}
                        </td>
                        <td className="px-3 py-3 text-right">
                          {o.t5_pct !== null
                            ? <span className={`font-semibold ${pctColor(o.t5_pct)}`}>{o.t5_pct > 0 ? "+" : ""}{o.t5_pct.toFixed(1)}%</span>
                            : <span className="text-slate-600">–</span>}
                        </td>
                        <td className="px-3 py-3 text-right">
                          {o.t10_pct !== null
                            ? <span className={`font-semibold ${pctColor(o.t10_pct)}`}>{o.t10_pct > 0 ? "+" : ""}{o.t10_pct.toFixed(1)}%</span>
                            : <span className="text-slate-600">–</span>}
                        </td>
                        <td className="px-4 py-3 text-center">{winBadge(o.t3_win ?? o.t5_win ?? o.t10_win)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <p className="text-center text-slate-600 text-xs">
            Signals auto-stored each time Bull Flow is scanned · T+3/5/10 = trading days after signal · Win = stock closed higher
          </p>
        </>
      )}

      {!loading && !data && !error && (
        <div className="text-center py-16 text-slate-500">
          <div className="text-4xl mb-3">📈</div>
          <div className="text-sm">Click Refresh to load outcome data</div>
        </div>
      )}
    </div>
  );
}

function BullFlowTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults]   = useState<BullFlowRow[]>([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [scanned, setScanned]   = useState(0);
  const [lastRun, setLastRun]   = useState<Date | null>(null);
  const [flowView, setFlowView] = useState<"bullish"|"strong"|"bearish">("bullish");
  const [theses,       setTheses]       = useState<Record<string, string>>({});
  const [loadThesis,   setLoadThesis]   = useState<Record<string, boolean>>({});
  const [expandThesis, setExpandThesis] = useState<Set<string>>(new Set());

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const data = await fetchBullFlow();
      setResults(data.results);
      setScanned(data.scanned);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  const handleThesis = async (row: BullFlowRow) => {
    if (theses[row.ticker]) {
      setExpandThesis(prev => {
        const next = new Set(prev);
        if (next.has(row.ticker)) next.delete(row.ticker); else next.add(row.ticker);
        return next;
      });
      return;
    }
    setLoadThesis(prev => ({ ...prev, [row.ticker]: true }));
    try {
      const data = await fetchAIThesis(row);
      setTheses(prev => ({ ...prev, [row.ticker]: data.thesis }));
      setExpandThesis(prev => new Set([...prev, row.ticker]));
    } catch {
      setTheses(prev => ({ ...prev, [row.ticker]: "Thesis unavailable." }));
      setExpandThesis(prev => new Set([...prev, row.ticker]));
    } finally {
      setLoadThesis(prev => ({ ...prev, [row.ticker]: false }));
    }
  };

  useEffect(() => { run(); }, []);

  const rankLabel = (rank: number) =>
    rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : `#${rank}`;

  const rankBg = (rank: number) =>
    rank === 1 ? "bg-yellow-900/20 border-yellow-700/30" :
    rank === 2 ? "bg-slate-700/20 border-slate-600/30" :
    rank === 3 ? "bg-orange-900/20 border-orange-700/30" :
    "bg-slate-900/40 border-slate-800/40";

  const fmtExp = (exp: string | null) => {
    if (!exp) return "—";
    const d = new Date(exp + "T00:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  const fmtPrem = (m: number) =>
    m >= 1 ? `$${m.toFixed(1)}M` : `$${(m * 1000).toFixed(0)}K`;

  const bullish = results.filter(r => r.call_put_ratio >= 1).slice(0, 20);
  const strong  = results.filter(r => r.call_put_ratio >= 3).sort((a, b) => b.call_put_ratio - a.call_put_ratio).slice(0, 20);
  const bearish = results.filter(r => r.call_put_ratio < 1).slice(0, 20);
  const displayed = flowView === "bullish" ? bullish : flowView === "strong" ? strong : bearish;
  const highConviction = flowView === "bearish"
    ? bearish.filter(r => r.call_put_ratio < 0.2).sort((a, b) => a.call_put_ratio - b.call_put_ratio)
    : displayed.filter(r => r.call_put_ratio >= 5).sort((a, b) => b.call_put_ratio - a.call_put_ratio);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-white font-bold text-lg flex items-center gap-2">
              {flowView === "bullish" ? "🟢 Bullish Flow" : flowView === "strong" ? "⚡ Strong Conviction (3x+)" : "🔴 Bearish Flow"}
            </h2>
            <p className="text-slate-400 text-sm mt-1">
              {flowView === "bullish"
                ? "Calls dominating — smart money betting stocks go up."
                : flowView === "strong"
                ? "Call/put ratio 3x or higher — high-conviction bullish bets, sorted strongest first."
                : "Puts dominating — smart money hedging or betting stocks drop."}
            </p>
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="shrink-0 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "🔥 Run Scan"}
          </button>
        </div>

        {/* Bullish / Strong / Bearish toggle */}
        <div className="flex gap-2">
          <button
            onClick={() => setFlowView("bullish")}
            className={`flex-1 py-2 rounded-lg text-sm font-bold border transition-colors ${flowView === "bullish" ? "bg-emerald-600 border-emerald-500 text-white" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}
          >
            🟢 Bullish {results.length > 0 && `(${bullish.length})`}
          </button>
          <button
            onClick={() => setFlowView("strong")}
            className={`flex-1 py-2 rounded-lg text-sm font-bold border transition-colors ${flowView === "strong" ? "bg-yellow-600 border-yellow-500 text-white" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}
          >
            ⚡ Strong {results.length > 0 && `(${strong.length})`}
          </button>
          <button
            onClick={() => setFlowView("bearish")}
            className={`flex-1 py-2 rounded-lg text-sm font-bold border transition-colors ${flowView === "bearish" ? "bg-red-700 border-red-600 text-white" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}
          >
            🔴 Bearish {results.length > 0 && `(${bearish.length})`}
          </button>
        </div>

        {lastRun && (
          <p className="text-slate-600 text-xs mt-2">
            Last scanned {scanned} tickers · {lastRun.toLocaleTimeString()}
          </p>
        )}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Empty state */}
      {!loading && results.length === 0 && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🔥</div>
          <div className="font-semibold text-slate-400 mb-1">Run the scan to see today's flow</div>
          <div className="text-sm">Ranks {scanned || 25}+ stocks by options premium — then splits by direction</div>
        </div>
      )}

      {/* Results */}
      {/* 🚨 High Conviction Spotlight */}
      {highConviction.length > 0 && (
        <div className="bg-yellow-950/30 border border-yellow-600/40 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-yellow-400 font-black text-sm">🚨 HIGH CONVICTION — SOMEBODY KNOWS SOMETHING</span>
          </div>
          <div className="space-y-2">
            {highConviction.map(row => (
              <button
                key={row.ticker}
                onClick={() => onSelectTicker(row.ticker)}
                className="w-full text-left bg-yellow-900/20 hover:bg-yellow-900/40 border border-yellow-700/30 rounded-lg p-3 transition-all"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className="text-white font-black text-xl">{row.ticker}</span>
                    <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
                    <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-yellow-500/20 text-yellow-300 border border-yellow-500/30">
                      {row.call_put_ratio.toFixed(1)}x C/P
                    </span>
                  </div>
                  <div className="text-right">
                    <div className="text-yellow-400 font-black">{fmtPrem(row.premium_m)}</div>
                    <div className="text-slate-500 text-xs">{row.strike ? `$${row.strike}C` : "—"} · {fmtExp(row.expiry)}</div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {displayed.length > 0 && (
        <div className="space-y-2">
          {displayed.map(row => (
            <div
              key={row.ticker}
              className={`rounded-xl border transition-all hover:border-emerald-700/50 hover:bg-emerald-950/10 ${rankBg(row.rank)}`}
            >
              <div
                className="p-4 cursor-pointer"
                onClick={() => onSelectTicker(row.ticker)}
              >
                <div className="flex items-center justify-between gap-3">
                  {/* Rank + Ticker */}
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-xl w-8 text-center shrink-0">{rankLabel(row.rank)}</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-white font-black text-lg">{row.ticker}</span>
                        <span className="text-slate-500 text-sm">${row.price.toLocaleString()}</span>
                        {(() => {
                          const r = row.call_put_ratio;
                          if (r >= 5)   return <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">🔥 Extremely Bullish</span>;
                          if (r >= 2)   return <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-emerald-900/40 text-emerald-400 border border-emerald-700/30">📈 Very Bullish</span>;
                          if (r >= 1)   return <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-blue-900/30 text-blue-400 border border-blue-700/30">↔️ Mixed</span>;
                          if (r >= 0.5) return <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-orange-900/30 text-orange-400 border border-orange-700/30">⚠️ More Puts</span>;
                          return <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-red-900/30 text-red-400 border border-red-700/30">🔴 Mostly Puts</span>;
                        })()}
                        {row.days_to_earnings != null && row.days_to_earnings <= 30 && (
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${
                            row.days_to_earnings <= 5
                              ? "bg-orange-900/40 text-orange-300 border-orange-600/40"
                              : "bg-blue-900/30 text-blue-300 border-blue-700/30"
                          }`}>
                            📅 {row.days_to_earnings}d to earnings
                          </span>
                        )}
                        {row.short_float_pct != null && row.short_float_pct >= 10 && (
                          <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-red-900/30 text-red-300 border border-red-700/30">
                            💥 {row.short_float_pct}% short
                          </span>
                        )}
                      </div>
                      <div className="text-slate-400 text-xs mt-0.5">
                        {row.strike ? `$${row.strike}C` : "—"}
                        {row.expiry ? ` · ${fmtExp(row.expiry)}` : ""}
                        {" · "}
                        <span className="text-slate-500">{row.total_call_vol.toLocaleString()} contracts</span>
                      </div>
                    </div>
                  </div>

                  {/* Stats */}
                  <div className="flex items-center gap-3 shrink-0 text-right">
                    <div>
                      <div className={`font-black text-lg ${row.rank <= 3 ? "text-emerald-400" : "text-emerald-500"}`}>
                        {fmtPrem(row.premium_m)}
                      </div>
                      <div className="text-slate-600 text-xs">premium</div>
                    </div>
                    <div className="hidden sm:block">
                      <div className={`font-bold text-sm ${row.call_put_ratio >= 2 ? "text-emerald-400" : row.call_put_ratio >= 1 ? "text-slate-300" : "text-slate-500"}`}>
                        {row.call_put_ratio.toFixed(1)}x
                      </div>
                      <div className="text-slate-600 text-xs">C/P</div>
                    </div>
                    <div className="hidden md:block">
                      <div className={`font-bold text-sm ${row.call_vol_oi >= 1 ? "text-yellow-400" : "text-slate-400"}`}>
                        {row.call_vol_oi.toFixed(2)}
                      </div>
                      <div className="text-slate-600 text-xs">Vol/OI</div>
                    </div>
                  </div>
                </div>
              </div>

              {/* AI Thesis button + expanded thesis */}
              <div className="px-4 pb-3">
                <button
                  onClick={e => { e.stopPropagation(); handleThesis(row); }}
                  className="flex items-center gap-1.5 text-xs font-semibold text-purple-400 hover:text-purple-300 transition-colors"
                >
                  {loadThesis[row.ticker] ? (
                    <><Spinner /> Generating thesis…</>
                  ) : expandThesis.has(row.ticker) ? (
                    <>🤖 Hide AI Thesis</>
                  ) : (
                    <>🤖 AI Trade Thesis</>
                  )}
                </button>
                {expandThesis.has(row.ticker) && theses[row.ticker] && (
                  <div className="mt-2 text-sm text-slate-300 bg-purple-950/20 border border-purple-800/30 rounded-lg p-3 leading-relaxed">
                    {theses[row.ticker]}
                  </div>
                )}
              </div>
            </div>
          ))}

          <p className="text-center text-slate-600 text-xs pt-2">
            Tap any stock to analyze it in Stock Lookup · Data from yfinance
          </p>
        </div>
      )}
    </div>
  );
}

// ---- Bloomberg Terminal Chrome -------------------------------------------

const BB_ORANGE = "#22c55e";
const BB_AMBER  = "#4ade80";
const BB_GREEN  = "#4ade80";
const BB_RED    = "#f87171";
const BB_BLUE   = "#60a5fa";
const BB_CYAN   = "#34d399";
const BB_BG     = "#060c14";
const BB_PANEL  = "#0b1320";
const BB_BORDER = "rgba(255,255,255,0.07)";
const BB_BDR2   = "rgba(255,255,255,0.1)";
const BB_LABEL  = "#475569";
const BB_WHITE  = "#f1f5f9";
const BB_FONT   = "Inter, system-ui, -apple-system, sans-serif";

function useNow() {
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  return now;
}

function BBBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{ height: 4, background: BB_BDR2, borderRadius: 2, overflow: "hidden", width: "100%" }}>
      <div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, height: "100%", background: color }} />
    </div>
  );
}

function BBPanel({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ background: BB_PANEL, border: `1px solid ${BB_BORDER}`, display: "flex", flexDirection: "column", ...style }}>{children}</div>;
}

function BBPanelHeader({ label, sub, accent = BB_ORANGE }: { label: string; sub?: string; accent?: string }) {
  return (
    <div style={{ borderBottom: `1px solid ${BB_BORDER}`, padding: "6px 10px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 3, height: 14, background: accent, borderRadius: 1 }} />
        <span style={{ fontFamily: BB_FONT, fontSize: 11, fontWeight: 700, color: accent, letterSpacing: "0.12em" }}>{label.toUpperCase()}</span>
      </div>
      {sub && <span style={{ fontFamily: BB_FONT, fontSize: 10, color: BB_LABEL }}>{sub}</span>}
    </div>
  );
}

function bbScoreColor(s: number) {
  if (s >= 9) return BB_GREEN;
  if (s >= 8) return BB_AMBER;
  if (s >= 7) return BB_ORANGE;
  return BB_RED;
}

function OverviewTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const { data: top10Data } = useQuery({ queryKey: ["daily-top10"], queryFn: fetchDailyTop10, refetchInterval: 60000 });
  const { data: bullData }  = useQuery({ queryKey: ["bull-flow-overview"], queryFn: () => fetchBullFlow(), refetchInterval: 60000 });
  const { data: mktData }   = useQuery({ queryKey: ["market-overview"], queryFn: fetchMarketOverview, refetchInterval: 30000 });

  const top10    = top10Data?.top10 ?? [];
  const bullFlow = (bullData?.results ?? []).slice(0, 8);
  const indices  = mktData?.indices ?? [];
  const sectors  = mktData?.sectors ?? [];
  const adv      = mktData?.advance_decline;
  const cc       = (v: number) => v >= 0 ? BB_GREEN : BB_RED;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "300px 1fr 240px", gridTemplateRows: "1fr 160px", flex: 1, overflow: "hidden", background: BB_BG }}>

      {/* LEFT: Top 10 */}
      <BBPanel style={{ gridRow: "1 / 3", borderRight: `1px solid ${BB_BORDER}`, overflow: "hidden" }}>
        <BBPanelHeader label="Today's Top 10" sub={`${top10Data?.total_scanned ?? 0} SCANNED`} />
        <div style={{ flex: 1, overflowY: "auto" }}>
          {top10.length === 0 && <div style={{ padding: 20, color: BB_LABEL, fontFamily: BB_FONT, fontSize: 11, textAlign: "center" }}>Run a scan to populate Top 10</div>}
          {top10.map((r, i) => (
            <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)} style={{
              display: "grid", gridTemplateColumns: "18px 52px 1fr 44px 36px", padding: "0 8px",
              borderBottom: `1px solid ${BB_BDR2}`, background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent", cursor: "pointer",
            }}>
              <span style={{ fontSize: 10, color: BB_LABEL, padding: "7px 4px", fontFamily: BB_FONT }}>{i + 1}</span>
              <div style={{ padding: "5px 4px" }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: BB_WHITE, fontFamily: BB_FONT }}>{r.ticker}</div>
                <div style={{ fontSize: 8, color: BB_LABEL, fontFamily: BB_FONT }}>${r.price?.toFixed(2) ?? "—"}</div>
              </div>
              <div style={{ padding: "5px 4px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 900, color: bbScoreColor(r.score ?? 0), fontFamily: BB_FONT }}>{(r.score ?? 0).toFixed(1)}</span>
                  <div style={{ flex: 1, maxWidth: 50 }}><BBBar pct={(r.score ?? 0) * 10} color={bbScoreColor(r.score ?? 0)} /></div>
                </div>
              </div>
              <span style={{ fontSize: 10, fontWeight: 700, color: cc(r.price_change_pct ?? 0), padding: "7px 4px", textAlign: "right", fontFamily: BB_FONT }}>
                {(r.price_change_pct ?? 0) >= 0 ? "+" : ""}{(r.price_change_pct ?? 0).toFixed(1)}%
              </span>
              <span style={{ fontSize: 10, color: (r.volume_ratio ?? 0) >= 2 ? BB_AMBER : BB_LABEL, padding: "7px 4px", textAlign: "right", fontFamily: BB_FONT }}>
                {r.volume_ratio != null ? `${r.volume_ratio.toFixed(1)}x` : "—"}
              </span>
            </div>
          ))}
        </div>
        <div style={{ padding: "6px 10px", borderTop: `1px solid ${BB_BORDER}`, display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontSize: 9, color: BB_LABEL, fontFamily: BB_FONT }}>REFRESHES DAILY AT OPEN</span>
          <span style={{ fontSize: 9, color: BB_ORANGE, fontFamily: BB_FONT }}>CLICK TO ANALYZE</span>
        </div>
      </BBPanel>

      {/* CENTER TOP: Bull Flow */}
      <BBPanel style={{ borderRight: `1px solid ${BB_BORDER}`, borderBottom: `1px solid ${BB_BORDER}`, overflow: "hidden" }}>
        <BBPanelHeader label="Bull Flow Signals" sub="C/P ≥ 2× · INSTITUTIONAL OPTIONS" />
        <div style={{ flex: 1, overflowY: "auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "60px 48px 72px 60px 70px 1fr", padding: "4px 12px 2px", borderBottom: `1px solid ${BB_BDR2}` }}>
            {["TICKER","C/P","PREMIUM","STRIKE","EXPIRY","BIAS"].map(h => (
              <span key={h} style={{ fontSize: 9, color: BB_LABEL, letterSpacing: "0.08em", fontFamily: BB_FONT }}>{h}</span>
            ))}
          </div>
          {bullFlow.length === 0 && <div style={{ padding: 16, color: BB_LABEL, fontFamily: BB_FONT, fontSize: 11, textAlign: "center" }}>Loading flow data…</div>}
          {bullFlow.map((r, i) => {
            const isBull = r.call_put_ratio >= 2;
            return (
              <div key={r.ticker + i} onClick={() => onSelectTicker(r.ticker)} style={{
                display: "grid", gridTemplateColumns: "60px 48px 72px 60px 70px 1fr",
                padding: "8px 12px", borderBottom: `1px solid ${BB_BDR2}`,
                background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent", cursor: "pointer",
                alignItems: "center",
              }}>
                <span style={{ fontSize: 12, fontWeight: 900, color: BB_WHITE, fontFamily: BB_FONT }}>{r.ticker}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: isBull ? BB_GREEN : BB_RED, fontFamily: BB_FONT }}>{r.call_put_ratio.toFixed(1)}×</span>
                <span style={{ fontSize: 11, color: BB_AMBER, fontWeight: 700, fontFamily: BB_FONT }}>${r.premium_m.toFixed(1)}M</span>
                <span style={{ fontSize: 11, color: BB_WHITE, fontFamily: BB_FONT }}>{r.strike ? `$${r.strike}` : "—"}</span>
                <span style={{ fontSize: 11, color: BB_LABEL, fontFamily: BB_FONT }}>{r.expiry ?? "—"}</span>
                <span style={{ fontSize: 9, fontWeight: 700, color: isBull ? BB_GREEN : BB_RED, background: isBull ? "rgba(0,230,118,0.08)" : "rgba(255,23,68,0.08)", border: `1px solid ${isBull ? "rgba(0,230,118,0.2)" : "rgba(255,23,68,0.2)"}`, padding: "2px 6px", borderRadius: 2, fontFamily: BB_FONT }}>
                  {isBull ? "▲ BULLISH" : "▼ BEARISH"}
                </span>
              </div>
            );
          })}
        </div>
      </BBPanel>

      {/* CENTER BOTTOM: Sector Heatmap */}
      <BBPanel style={{ borderRight: `1px solid ${BB_BORDER}`, overflow: "hidden" }}>
        <BBPanelHeader label="Sector Strength" sub="BREADTH" accent={BB_CYAN} />
        <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, padding: 6, background: BB_BORDER }}>
          {(sectors.length > 0 ? sectors : Array(8).fill({ ticker: "—", name: "—", change_pct: 0 })).slice(0, 8).map((s: any, idx: number) => {
            const chg = s.change_pct ?? 0;
            const name = (s.name ?? s.ticker ?? "—").slice(0, 8);
            return (
              <div key={idx} style={{ background: chg >= 0 ? "rgba(0,230,118,0.04)" : "rgba(255,23,68,0.04)", border: `1px solid ${chg >= 0 ? "rgba(0,230,118,0.12)" : "rgba(255,23,68,0.12)"}`, padding: "6px 8px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 3 }}>
                  <span style={{ fontSize: 9, fontWeight: 700, color: BB_WHITE, fontFamily: BB_FONT }}>{name}</span>
                  <span style={{ fontSize: 10, fontWeight: 700, color: cc(chg), fontFamily: BB_FONT }}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span>
                </div>
                <BBBar pct={50 + chg * 5} color={chg >= 0 ? BB_GREEN : BB_RED} />
              </div>
            );
          })}
        </div>
      </BBPanel>

      {/* RIGHT TOP: Top Flow */}
      <BBPanel style={{ gridRow: "1 / 2", overflow: "hidden" }}>
        <BBPanelHeader label="Top Flow Today" sub="BY PREMIUM" accent={BB_RED} />
        <div style={{ flex: 1, overflowY: "auto" }}>
          {bullFlow.length === 0 && <div style={{ padding: 16, color: BB_LABEL, fontFamily: BB_FONT, fontSize: 11, textAlign: "center" }}>Loading…</div>}
          {bullFlow.slice(0, 5).map((r, i) => {
            const isBull = r.call_put_ratio >= 2;
            return (
              <div key={i} onClick={() => onSelectTicker(r.ticker)} style={{ padding: "8px 10px", borderBottom: `1px solid ${BB_BDR2}`, borderLeft: `3px solid ${isBull ? BB_GREEN : BB_RED}`, cursor: "pointer" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 }}>
                  <span style={{ fontSize: 13, fontWeight: 900, color: BB_WHITE, fontFamily: BB_FONT }}>{r.ticker}</span>
                  <span style={{ fontSize: 11, color: BB_AMBER, fontWeight: 700, fontFamily: BB_FONT }}>${r.premium_m.toFixed(1)}M</span>
                </div>
                <div style={{ fontSize: 9, color: isBull ? BB_GREEN : BB_RED, fontWeight: 700, fontFamily: BB_FONT }}>
                  {isBull ? "▲ BULL FLOW" : "▼ BEAR FLOW"} · C/P {r.call_put_ratio.toFixed(1)}×
                </div>
              </div>
            );
          })}
        </div>
      </BBPanel>

      {/* RIGHT BOTTOM: Market Indices */}
      <BBPanel style={{ gridRow: "2 / 3", overflow: "hidden" }}>
        <BBPanelHeader label="Market" sub="INDICES" accent={BB_BLUE} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          {(indices.length > 0 ? indices : Array(4).fill({ label: "—", price: 0, change_pct: 0 })).slice(0, 5).map((m: any, i: number) => {
            const chg = m.change_pct ?? 0;
            return (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "44px 1fr auto", padding: "5px 10px", borderBottom: `1px solid ${BB_BDR2}`, alignItems: "center", background: i % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent" }}>
                <span style={{ fontSize: 10, color: BB_LABEL, fontWeight: 700, fontFamily: BB_FONT }}>{m.label ?? m.ticker}</span>
                <div style={{ paddingLeft: 4 }}>
                  <div style={{ width: "100%", height: 3, background: BB_BDR2, borderRadius: 1 }}>
                    <div style={{ width: chg >= 0 ? "65%" : "35%", height: "100%", background: cc(chg), borderRadius: 1 }} />
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 11, color: BB_WHITE, fontWeight: 700, fontFamily: BB_FONT }}>${m.price?.toFixed(2) ?? "—"}</div>
                  <div style={{ fontSize: 9, color: cc(chg), fontFamily: BB_FONT }}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</div>
                </div>
              </div>
            );
          })}
          {adv && (
            <div style={{ padding: "5px 10px", display: "flex", justifyContent: "space-between", borderTop: `1px solid ${BB_BORDER}`, marginTop: "auto" }}>
              <span style={{ fontSize: 9, color: BB_LABEL, fontFamily: BB_FONT }}>A/D RATIO</span>
              <span style={{ fontSize: 9, color: BB_GREEN, fontFamily: BB_FONT }}>▲{adv.up}</span>
              <span style={{ fontSize: 9, color: BB_RED, fontFamily: BB_FONT }}>▼{adv.down}</span>
            </div>
          )}
        </div>
      </BBPanel>
    </div>
  );
}

// ---- Main Dashboard ------------------------------------------------------

export default function Dashboard() {
  const [ticker, setTicker]         = useState("AAPL");
  const [inputTicker, setInputTicker] = useState("AAPL");
  const [scanTickers, setScanTickers] = useState(DEFAULT_SCAN.join(", "));
  const [tab, setTab]               = useState<"overview"|"lookup"|"scanner"|"analytics"|"backtest"|"alerts"|"portfolio"|"propdesk"|"bullflow"|"smartmoney"|"congress"|"market"|"squeeze"|"insiders"|"breakout">("lookup");
  const now = useNow();
  const [blink, setBlink] = useState(true);
  const [tickPos, setTickPos] = useState(0);
  useEffect(() => { const t = setInterval(() => setBlink(b => !b), 800); return () => clearInterval(t); }, []);
  useEffect(() => { const t = setInterval(() => setTickPos(p => p - 1), 22); return () => clearInterval(t); }, []);
  const [tradeMode, setTradeMode]   = useState<"buy"|"sell">("buy");
  const [tradeShares, setTradeShares] = useState("");
  const [lookupSubTab, setLookupSubTab] = useState<"analysis"|"technicals"|"chart">("analysis");
  const [aiText, setAiText] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiTicker, setAiTicker] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: analysis, isLoading: loadingAnalysis, error: analysisError } = useQuery({
    queryKey: ["stock", ticker],
    queryFn: () => analyzeStock(ticker),
    enabled: !!ticker,
  });

  const parsedScanTickers = scanTickers.split(/[\s,]+/).filter(Boolean).map(t => t.toUpperCase()).slice(0, 20);
  const { data: scanData, isLoading: loadingScan, refetch: runScan } = useQuery({
    queryKey: ["scan", parsedScanTickers.join(",")],
    queryFn: () => scanStocks(parsedScanTickers),
    enabled: false,
  });

  const { data: portfolio, isLoading: loadingPortfolio } = useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    enabled: tab === "portfolio",
  });

  const tradeMutation = useMutation({
    mutationFn: ({ mode, t, shares, price }: { mode:"buy"|"sell"; t:string; shares:number; price:number }) =>
      mode === "buy" ? buyStock(t, shares, price) : sellStock(t, shares, price),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["portfolio"] }); setTradeShares(""); },
  });

  const handleLookup = useCallback(() => {
    const t = inputTicker.trim().toUpperCase();
    if (t) { setTicker(t); setAiText(null); setAiError(null); setAiTicker(null); }
  }, [inputTicker]);

  const runAIAnalysis = useCallback(async (a: typeof analysis) => {
    if (!a) return;
    setAiLoading(true); setAiError(null); setAiTicker(a.ticker);
    try {
      const result = await fetchAIAnalysis({
        ticker: a.ticker,
        rsi: a.indicators.rsi, macd: a.indicators.macd,
        volume_ratio: a.indicators.volume_ratio,
        price: a.indicators.price, change_pct: a.indicators.price_change_pct,
        score: a.score?.score, rating: a.score?.rating,
        sector: a.info.sector, sma50: a.indicators.sma50, sma200: a.indicators.sma200,
      });
      setAiText(result.analysis);
    } catch (e: any) {
      setAiError(e?.message ?? "AI analysis failed");
    } finally {
      setAiLoading(false);
    }
  }, []);

  const handleTrade = useCallback(() => {
    const shares = parseFloat(tradeShares);
    if (!shares || shares <= 0 || !analysis?.indicators.price) return;
    tradeMutation.mutate({ mode: tradeMode, t: analysis.ticker, shares, price: analysis.indicators.price });
  }, [tradeShares, tradeMode, analysis, tradeMutation]);

  const ind   = analysis?.indicators;
  const score = analysis?.score;
  const ml    = analysis?.ml;

  const TABS = [
    { id: "overview",   label: "OVERVIEW" },
    { id: "bullflow",   label: "BULL FLOW" },
    { id: "smartmoney", label: "SMART MONEY" },
    { id: "congress",   label: "CONGRESS" },
    { id: "lookup",     label: "STOCK LOOKUP" },
    { id: "scanner",    label: "SCANNER" },
    { id: "outcomes",   label: "OUTCOMES" },
    { id: "analytics",  label: "ANALYTICS" },
    { id: "propdesk",   label: "PROP DESK" },
    { id: "squeeze",    label: "SQUEEZE" },
    { id: "breakout",   label: "BREAKOUT" },
    { id: "insiders",   label: "INSIDERS" },
    { id: "market",     label: "MARKET" },
    { id: "portfolio",  label: "PORTFOLIO" },
  ] as const;

  const timeStr = now.toLocaleTimeString("en-US", { hour12: false, timeZone: "America/New_York" });
  const dateStr = now.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  const { data: headerMkt } = useQuery({ queryKey: ["market-overview"], queryFn: fetchMarketOverview, refetchInterval: 30000 });
  const headerIndices = (headerMkt?.indices ?? []).slice(0, 4);
  const tickerStr = "  SPY  ·  QQQ  ·  DIA  ·  IWM  ·  VIX  ·  NVDA  ·  META  ·  TSLA  ·  AMD  ·  AMZN  ·  AAPL  ·  MSFT  ·  GOOGL  ·  MU  ·  JPM  ";

  return (
    <div style={{ height: "100dvh", background: BB_BG, display: "flex", flexDirection: "column", overflow: "hidden", fontFamily: BB_FONT }}>
      <style>{`
        .bb-quotes { display: flex; gap: 20px; align-items: center; }
        .bb-divider { display: block; }
        .bb-clock-date { display: block; }
        .bb-tabs::-webkit-scrollbar { display: none; }
        .bb-tabs { -ms-overflow-style: none; scrollbar-width: none; }
        @media (max-width: 640px) {
          .bb-quotes { display: none !important; }
          .bb-divider { display: none !important; }
          .bb-clock-date { display: none !important; }
        }
      `}</style>

      {/* ── TOP BAR ── */}
      <div style={{ background: "rgba(6,12,20,0.97)", borderBottom: `1px solid rgba(255,255,255,0.07)`, backdropFilter: "blur(20px)", padding: "0 16px", height: 52, display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
          <button onClick={() => window.location.href = import.meta.env.BASE_URL} style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 10, padding: 0, flexShrink: 0 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: "linear-gradient(135deg,#16a34a,#22c55e)", boxShadow: "0 0 16px rgba(34,197,94,0.4)", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 900, fontSize: 15, color: "#fff" }}>S</div>
            <span style={{ color: "#f1f5f9", fontWeight: 900, fontSize: 15, letterSpacing: "-0.02em", fontFamily: BB_FONT }}>StockScanner <span style={{ color: "#4ade80" }}>AI</span></span>
          </button>
          <div className="bb-divider" style={{ width: 1, height: 22, background: "rgba(255,255,255,0.08)", flexShrink: 0 }} />
          <div className="bb-quotes">
            {headerIndices.map(m => (
              <div key={m.label ?? m.ticker} style={{ display: "flex", gap: 5, alignItems: "baseline" }}>
                <span style={{ color: BB_LABEL, fontSize: 11, fontWeight: 600, fontFamily: BB_FONT }}>{m.label ?? m.ticker}</span>
                <span style={{ color: BB_WHITE, fontSize: 12, fontFamily: BB_FONT, fontWeight: 600 }}>${m.price?.toFixed(2) ?? "—"}</span>
                <span style={{ color: m.change_pct >= 0 ? BB_GREEN : BB_RED, fontSize: 11, fontFamily: BB_FONT, fontWeight: 600 }}>{m.change_pct >= 0 ? "+" : ""}{m.change_pct?.toFixed(2) ?? "0.00"}%</span>
              </div>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.2)", borderRadius: 20, padding: "4px 10px" }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: BB_GREEN, boxShadow: `0 0 6px ${BB_GREEN}`, opacity: blink ? 1 : 0.4, transition: "opacity 0.2s" }} />
            <span style={{ color: BB_GREEN, fontSize: 11, fontWeight: 700, fontFamily: BB_FONT }}>LIVE</span>
          </div>
          <div className="bb-divider" style={{ width: 1, height: 22, background: "rgba(255,255,255,0.08)" }} />
          <div style={{ textAlign: "right" }}>
            <div style={{ color: BB_WHITE, fontSize: 13, fontWeight: 700, fontFamily: BB_FONT }}>{timeStr}</div>
            <div className="bb-clock-date" style={{ color: BB_LABEL, fontSize: 10, fontFamily: BB_FONT }}>NY · {dateStr}</div>
          </div>
        </div>
      </div>

      {/* ── NAV TABS ── */}
      <div className="bb-tabs" style={{ background: "rgba(6,12,20,0.95)", borderBottom: `1px solid rgba(255,255,255,0.07)`, display: "flex", alignItems: "center", flexShrink: 0, height: 40, overflowX: "auto" }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id as typeof tab)} style={{
            padding: "0 16px", height: "100%", background: "transparent",
            borderBottom: tab === t.id ? `2px solid #22c55e` : "2px solid transparent",
            borderTop: "none", borderLeft: "none", borderRight: "none", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
            transition: "all 0.15s",
          }}>
            <span style={{ fontSize: 12, fontWeight: tab === t.id ? 700 : 500, color: tab === t.id ? "#4ade80" : BB_LABEL, fontFamily: BB_FONT }}>{t.label}</span>
          </button>
        ))}
        <div style={{ padding: "0 14px", display: "flex", alignItems: "center", gap: 8, height: "100%", flexShrink: 0, marginLeft: "auto" }}>
          <span style={{ fontSize: 10, color: BB_LABEL, fontFamily: BB_FONT }}>A/D</span>
          <span style={{ fontSize: 10, color: BB_GREEN, fontFamily: BB_FONT, fontWeight: 700 }}>▲{headerMkt?.advance_decline?.up ?? "—"}</span>
          <span style={{ fontSize: 10, color: BB_RED, fontFamily: BB_FONT, fontWeight: 700 }}>▼{headerMkt?.advance_decline?.down ?? "—"}</span>
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      {tab === "overview" ? (
        <OverviewTab onSelectTicker={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />
      ) : (
      <main style={{ flex: 1, overflowY: "auto", background: "#060c14" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px" }}>

        {/* --- Stock Lookup --- */}
        {tab === "lookup" && (() => {
          const fmtVol = (v?: number | null) => {
            if (v == null) return "—";
            if (v >= 1e9) return `${(v/1e9).toFixed(1)}B`;
            if (v >= 1e6) return `${(v/1e6).toFixed(1)}M`;
            if (v >= 1e3) return `${(v/1e3).toFixed(1)}K`;
            return v.toFixed(0);
          };
          const rsiVal = ind?.rsi ?? 0;
          const rsiColor = rsiVal > 70 ? BB_RED : rsiVal < 30 ? BB_GREEN : BB_WHITE;
          const macdVal = ind?.macd ?? 0;
          const scoreColor = !score ? BB_WHITE
            : score.score >= 8 ? BB_GREEN
            : score.score >= 6 ? "#84cc16"
            : score.score >= 5 ? "#eab308"
            : score.score >= 3 ? BB_ORANGE
            : BB_RED;
          const catalystText = !score ? "Enter a ticker to see analysis"
            : score.rating === "Strong Buy" ? "Strong bullish confluence — momentum, technicals, and volume all aligned"
            : score.rating === "Buy" ? "Positive setup — technicals and momentum favor upside continuation"
            : score.rating === "Strong Sell" ? "Heavy bearish pressure — multiple indicators signal downside risk"
            : score.rating === "Sell" ? "Bearish signals detected — caution advised, risk/reward unfavorable"
            : "Neutral — no clear directional edge, monitor for catalyst or breakout";
          const subTabStyle = (id: string) => ({
            padding: "8px 16px", background: "none", border: "none",
            borderBottom: lookupSubTab === id ? `2px solid ${BB_GREEN}` : "2px solid transparent",
            color: lookupSubTab === id ? BB_GREEN : BB_LABEL,
            fontSize: 10, fontWeight: 700 as const, fontFamily: BB_FONT, cursor: "pointer", letterSpacing: "0.1em",
          });
          return (
            <div style={{ fontFamily: BB_FONT }}>
              {/* Search bar */}
              <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
                <input value={inputTicker} onChange={e => setInputTicker(e.target.value.toUpperCase())}
                  onKeyDown={e => e.key === "Enter" && handleLookup()}
                  placeholder="ENTER TICKER (e.g. AAPL)"
                  style={{ flex: 1, background: "#0a0a0a", border: `1px solid ${BB_BORDER}`, padding: "10px 14px", color: BB_WHITE, fontFamily: BB_FONT, fontSize: 12, outline: "none", textTransform: "uppercase", letterSpacing: "0.08em" }} />
                <button onClick={handleLookup} style={{ background: BB_ORANGE, border: "none", padding: "10px 24px", color: "#000", fontFamily: BB_FONT, fontSize: 11, fontWeight: 900, letterSpacing: "0.12em", cursor: "pointer" }}>ANALYZE</button>
              </div>

              {loadingAnalysis && <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, padding: "60px 0", color: BB_LABEL, fontSize: 12 }}><Spinner /> FETCHING DATA &amp; RUNNING ANALYSIS…</div>}
              {analysisError  && <div style={{ background: "#1a0000", border: `1px solid ${BB_RED}`, padding: 14, color: BB_RED, fontSize: 12 }}>{analysisError instanceof Error ? analysisError.message : "FAILED TO ANALYZE"}</div>}

              {!analysis && !loadingAnalysis && !analysisError && (
                <div style={{ textAlign: "center", padding: "60px 0", color: BB_LABEL, fontSize: 12, letterSpacing: "0.1em" }}>ENTER A TICKER SYMBOL ABOVE TO GET STARTED</div>
              )}

              {analysis && !loadingAnalysis && (
                <>
                  {/* ── STOCK HEADER ── */}
                  <div style={{ borderBottom: `2px solid ${BB_ORANGE}`, paddingBottom: 14, marginBottom: 14, display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 8 }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                        <span style={{ color: BB_ORANGE, fontSize: 26, fontWeight: 900, letterSpacing: "0.05em" }}>{analysis.ticker}</span>
                        <span style={{ color: BB_LABEL, fontSize: 12, letterSpacing: "0.05em" }}>{analysis.info.name || ""}</span>
                      </div>
                      <div style={{ display: "flex", gap: 16, marginTop: 5, flexWrap: "wrap" }}>
                        {analysis.info.sector && <span style={{ color: "#444", fontSize: 9, letterSpacing: "0.08em" }}>{analysis.info.sector.toUpperCase()}</span>}
                        <span style={{ color: "#444", fontSize: 9, letterSpacing: "0.08em" }}>CAP: {fmtMktCap(analysis.info.market_cap)}</span>
                        <span style={{ color: "#444", fontSize: 9, letterSpacing: "0.08em" }}>P/E: {fmt(analysis.info.pe_ratio)}</span>
                        <span style={{ color: "#444", fontSize: 9, letterSpacing: "0.08em" }}>BETA: {fmt(analysis.info.beta)}</span>
                        <span style={{ color: "#444", fontSize: 9, letterSpacing: "0.08em" }}>52W: ${fmt(ind?.low_52w)} – ${fmt(ind?.high_52w)}</span>
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ color: BB_WHITE, fontSize: 32, fontWeight: 700, lineHeight: 1, letterSpacing: "-0.02em" }}>${fmt(ind?.price)}</div>
                      <div style={{ color: (ind?.price_change_pct ?? 0) >= 0 ? BB_GREEN : BB_RED, fontSize: 15, fontWeight: 700, marginTop: 5, letterSpacing: "0.05em" }}>
                        {(ind?.price_change_pct ?? 0) >= 0 ? "▲" : "▼"} {Math.abs(ind?.price_change_pct ?? 0).toFixed(2)}%
                      </div>
                    </div>
                  </div>

                  {/* ── 6 METRIC CARDS ── */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginBottom: 10 }}>
                    {/* RSI */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>RSI (14)</div>
                      <div style={{ color: rsiColor, fontSize: 22, fontWeight: 700 }}>{ind?.rsi != null ? ind.rsi.toFixed(1) : "—"}</div>
                      {ind?.rsi != null && (
                        <div style={{ height: 2, background: "#1c1c1c", marginTop: 8, borderRadius: 1 }}>
                          <div style={{ width: `${Math.min(ind.rsi, 100)}%`, height: "100%", background: rsiColor, borderRadius: 1 }} />
                        </div>
                      )}
                    </div>
                    {/* MACD */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>MACD</div>
                      <div style={{ color: macdVal >= 0 ? BB_GREEN : BB_RED, fontSize: 22, fontWeight: 700 }}>
                        {ind?.macd != null ? (macdVal >= 0 ? "+" : "") + fmt(ind.macd, 2) : "—"}
                      </div>
                      {ind?.macd_signal != null && <div style={{ color: BB_LABEL, fontSize: 9, marginTop: 6 }}>SIG {fmt(ind.macd_signal, 2)}</div>}
                    </div>
                    {/* VOL RATIO */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>VOL RATIO</div>
                      <div style={{ color: (ind?.volume_ratio ?? 1) >= 1.5 ? "#FFD700" : BB_WHITE, fontSize: 22, fontWeight: 700 }}>
                        {ind?.volume_ratio != null ? fmt(ind.volume_ratio, 1) + "x" : "—"}
                      </div>
                      {(ind?.volume_ratio ?? 0) >= 1.5 && <div style={{ color: "#FFD700", fontSize: 9, marginTop: 6 }}>ELEVATED</div>}
                    </div>
                    {/* VOLUME */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>VOLUME</div>
                      <div style={{ color: BB_WHITE, fontSize: 22, fontWeight: 700 }}>{fmtVol(ind?.volume)}</div>
                    </div>
                    {/* AVG VOL */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>AVG VOL</div>
                      <div style={{ color: BB_LABEL, fontSize: 22, fontWeight: 700 }}>{fmtVol(ind?.avg_volume_20)}</div>
                    </div>
                    {/* SIGNAL */}
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 6 }}>SIGNAL</div>
                      <div style={{ color: scoreColor, fontSize: score && score.rating.length > 8 ? 14 : 18, fontWeight: 700, lineHeight: 1.2 }}>{score?.rating ?? "—"}</div>
                    </div>
                  </div>

                  {/* ── OPTIONS FLOW + CATALYST ── */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 8 }}>OPTIONS FLOW</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ width: 8, height: 8, borderRadius: "50%", background: (ind?.volume_ratio ?? 1) >= 1.5 ? BB_GREEN : BB_LABEL, flexShrink: 0 }} />
                        <span style={{ color: (ind?.volume_ratio ?? 1) >= 1.5 ? BB_GREEN : BB_LABEL, fontSize: 13, fontWeight: 700, letterSpacing: "0.05em" }}>
                          {(ind?.volume_ratio ?? 1) >= 2 ? "HEAVY CALL FLOW" : (ind?.volume_ratio ?? 1) >= 1.5 ? "ELEVATED FLOW" : "NORMAL FLOW"}
                        </span>
                      </div>
                      <div style={{ color: "#444", fontSize: 9, marginTop: 6 }}>VOL {fmtVol(ind?.volume)} vs AVG {fmtVol(ind?.avg_volume_20)}</div>
                    </div>
                    <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "12px 14px" }}>
                      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 8 }}>CATALYST</div>
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 7 }}>
                        <span style={{ color: BB_ORANGE, fontSize: 10, flexShrink: 0, marginTop: 1 }}>◆</span>
                        <span style={{ color: BB_WHITE, fontSize: 10, lineHeight: 1.5 }}>{catalystText}</span>
                      </div>
                    </div>
                  </div>

                  {/* ── AI ANALYSIS / TECHNICALS / CHART TABS ── */}
                  <div style={{ background: "#080808", border: `1px solid ${BB_BORDER}`, marginBottom: 10 }}>
                    <div style={{ display: "flex", borderBottom: `1px solid ${BB_BORDER}` }}>
                      <button onClick={() => setLookupSubTab("analysis")} style={subTabStyle("analysis")}>AI ANALYSIS</button>
                      <button onClick={() => setLookupSubTab("technicals")} style={subTabStyle("technicals")}>TECHNICALS</button>
                      <button onClick={() => setLookupSubTab("chart")} style={subTabStyle("chart")}>CHART</button>
                    </div>

                    {lookupSubTab === "analysis" && (
                      <div style={{ padding: 16 }}>
                        {/* Claude-style AI response card */}
                        <div style={{ background: "#0d0d0d", border: "1px solid #1e1e1e", borderRadius: 12, overflow: "hidden", marginBottom: 14 }}>
                          {/* Card header — Claude avatar + name + refresh */}
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: "1px solid #1a1a1a" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                              {/* Claude logo mark */}
                              <div style={{ width: 28, height: 28, borderRadius: 6, background: "linear-gradient(135deg, #cc785c 0%, #d4956a 100%)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z" fill="white" opacity="0.9"/>
                                </svg>
                              </div>
                              <div>
                                <div style={{ color: "#e5e5e5", fontSize: 12, fontWeight: 600, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", letterSpacing: 0 }}>Claude</div>
                                <div style={{ color: "#555", fontSize: 10, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>Swing Analysis · {analysis.ticker}</div>
                              </div>
                            </div>
                            <button
                              onClick={() => runAIAnalysis(analysis)}
                              disabled={aiLoading}
                              style={{ background: aiLoading ? "transparent" : "#1a1a1a", border: "1px solid #2a2a2a", color: aiLoading ? "#444" : "#ccc", padding: "5px 12px", borderRadius: 6, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", fontSize: 11, fontWeight: 500, cursor: aiLoading ? "default" : "pointer", display: "flex", alignItems: "center", gap: 5, transition: "all 0.15s" }}
                            >
                              {aiLoading ? (
                                <><Spinner /><span>Analyzing…</span></>
                              ) : (
                                <><span style={{ fontSize: 13 }}>↻</span><span>Refresh</span></>
                              )}
                            </button>
                          </div>

                          {/* Message body */}
                          <div style={{ padding: "16px 18px", minHeight: 80 }}>
                            {aiLoading && (
                              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                                {[0,1,2].map(i => (
                                  <div key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "#cc785c", opacity: 0.7, animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />
                                ))}
                              </div>
                            )}
                            {!aiLoading && aiError && (
                              <div style={{ color: "#e05c5c", fontSize: 13, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", lineHeight: 1.6 }}>
                                ⚠ {aiError}
                              </div>
                            )}
                            {!aiLoading && aiText && aiTicker === analysis.ticker && (
                              <ClaudeMarkdown text={aiText} />
                            )}
                            {!aiLoading && !aiText && !aiError && (
                              <div style={{ color: "#444", fontSize: 13, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", lineHeight: 1.6 }}>
                                Click <span style={{ color: "#cc785c", fontWeight: 600 }}>↻ Refresh</span> to get an AI-powered swing trade analysis for <strong style={{ color: "#777" }}>{analysis.ticker}</strong> from Claude.
                              </div>
                            )}
                          </div>

                          {/* Footer */}
                          {aiText && aiTicker === analysis.ticker && (
                            <div style={{ padding: "8px 18px 12px", borderTop: "1px solid #161616" }}>
                              <span style={{ color: "#333", fontSize: 10, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>
                                Powered by Claude · For informational purposes only, not financial advice
                              </span>
                            </div>
                          )}
                        </div>

                        {/* Score + ML below */}
                        {score && (
                          <>
                            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
                              <span style={{ color: scoreColor, fontSize: 28, fontWeight: 900 }}>{score.score.toFixed(1)}</span>
                              <span style={{ color: "#333", fontSize: 16 }}>/10</span>
                              <span style={{ color: scoreColor, fontSize: 13, fontWeight: 700, letterSpacing: "0.05em", marginLeft: 4 }}>{score.rating.toUpperCase()}</span>
                            </div>
                            <ScoreBreakdown breakdown={score.breakdown} />
                          </>
                        )}
                        {ml && (
                          <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${BB_BORDER}` }}>
                            <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 8 }}>ML PROBABILITY MODEL</div>
                            <DirectionBadge direction={ml.direction} confidence={ml.confidence} probUp={ml.probability_up} />
                            {ml.model_accuracy && <div style={{ color: "#333", fontSize: 9, marginTop: 6 }}>MODEL ACCURACY: {ml.model_accuracy.toFixed(1)}%</div>}
                          </div>
                        )}
                      </div>
                    )}

                    {lookupSubTab === "technicals" && (
                      <div style={{ padding: 16 }}>
                        {ind?.rsi != null && <div style={{ marginBottom: 14 }}><RsiGauge rsi={ind.rsi} /></div>}
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px" }}>
                          {[
                            { label: "SMA 50",    value: `$${fmt(ind?.sma50)}` },
                            { label: "SMA 200",   value: `$${fmt(ind?.sma200)}` },
                            { label: "BB UPPER",  value: `$${fmt(ind?.bb_upper)}` },
                            { label: "BB LOWER",  value: `$${fmt(ind?.bb_lower)}` },
                            { label: "ATR",       value: fmt(ind?.atr) },
                            { label: "MOMENTUM",  value: fmt((ind as any)?.momentum, 3) },
                          ].map(row => (
                            <div key={row.label} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid #111` }}>
                              <span style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.08em" }}>{row.label}</span>
                              <span style={{ color: BB_WHITE, fontSize: 10, fontWeight: 700 }}>{row.value}</span>
                            </div>
                          ))}
                        </div>
                        <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${BB_BORDER}` }}>
                          {score && <ScoreBadge score={score.score} rating={score.rating} />}
                        </div>
                      </div>
                    )}

                    {lookupSubTab === "chart" && (
                      <div style={{ padding: 16 }}>
                        <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 12 }}>PRICE HISTORY (90 DAYS)</div>
                        <PriceChart history={analysis.history} />
                      </div>
                    )}
                  </div>

                  {/* ── PAPER TRADE ── */}
                  <div style={{ background: "#0d0d0d", border: `1px solid ${BB_BORDER}`, padding: "14px 16px" }}>
                    <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 12 }}>PAPER TRADE — {analysis.ticker}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <div style={{ display: "flex", border: `1px solid ${BB_BORDER}` }}>
                        {(["buy","sell"] as const).map(m => (
                          <button key={m} onClick={() => setTradeMode(m)} style={{
                            padding: "7px 18px", border: "none", cursor: "pointer", fontFamily: BB_FONT, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em",
                            background: tradeMode === m ? (m === "buy" ? "#003300" : "#330000") : "transparent",
                            color: tradeMode === m ? (m === "buy" ? BB_GREEN : BB_RED) : BB_LABEL,
                          }}>{m.toUpperCase()}</button>
                        ))}
                      </div>
                      <input type="number" value={tradeShares} onChange={e => setTradeShares(e.target.value)} placeholder="SHARES"
                        style={{ width: 100, background: "#0a0a0a", border: `1px solid ${BB_BORDER}`, padding: "7px 10px", color: BB_WHITE, fontFamily: BB_FONT, fontSize: 11, outline: "none" }} />
                      <span style={{ color: BB_LABEL, fontSize: 10 }}>@ ${fmt(ind?.price)} = <span style={{ color: BB_WHITE, fontWeight: 700 }}>${tradeShares && ind?.price ? fmt(parseFloat(tradeShares) * ind.price, 2) : "—"}</span></span>
                      <button onClick={handleTrade} disabled={tradeMutation.isPending} style={{
                        background: tradeMode === "buy" ? "#003300" : "#330000",
                        border: `1px solid ${tradeMode === "buy" ? BB_GREEN : BB_RED}`,
                        color: tradeMode === "buy" ? BB_GREEN : BB_RED,
                        padding: "7px 20px", fontFamily: BB_FONT, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", cursor: "pointer", opacity: tradeMutation.isPending ? 0.5 : 1,
                      }}>
                        {tradeMutation.isPending ? "…" : `${tradeMode === "buy" ? "BUY" : "SELL"} ${analysis.ticker}`}
                      </button>
                      {tradeMutation.data?.message && <span style={{ color: BB_GREEN, fontSize: 10 }}>{tradeMutation.data.message}</span>}
                      {tradeMutation.data?.error   && <span style={{ color: BB_RED, fontSize: 10 }}>{tradeMutation.data.error}</span>}
                    </div>
                  </div>
                </>
              )}
            </div>
          );
        })()}

        {/* --- Scanner --- */}
        {tab === "scanner" && (
          <div className="space-y-4">
            <DailyTop10Banner onSelect={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-3">Tickers to scan (comma-separated, max 20)</div>
              <div className="flex gap-2">
                <input value={scanTickers} onChange={e => setScanTickers(e.target.value.toUpperCase())} className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500" />
                <button onClick={() => runScan()} disabled={loadingScan} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2">{loadingScan && <Spinner />} Scan</button>
              </div>
              <div className="text-xs text-slate-500 mt-2">⚠️ Scanning many tickers may take 1–2 minutes</div>
            </div>
            {loadingScan && <div className="flex items-center justify-center py-16 gap-3 text-slate-400"><Spinner /> Scanning {parsedScanTickers.length} tickers…</div>}
            {scanData && !loadingScan && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="text-slate-400 text-sm">{scanData.results.filter(r => !r.error).length} stocks analyzed</div>
                  <div className="text-xs text-slate-500">Click a row to analyze</div>
                </div>
                <ScanTable results={scanData.results.filter(r => !r.error)} onSelect={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />
              </div>
            )}
            {!scanData && !loadingScan && <div className="text-center py-16 text-slate-500">Click "Scan" to analyze the tickers above</div>}
          </div>
        )}

        {tab === "analytics" && (
          <div className="space-y-4">
            <DailyTop10Banner onSelect={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />
            <AnalyticsTab />
          </div>
        )}
        {tab === "backtest"  && <BacktestTab />}
        {tab === "alerts"    && <AlertsTab />}
        {tab === "propdesk"   && <PropDeskTab />}
        {tab === "smartmoney" && <SmartMoneyTab />}
        {tab === "congress"   && <CongressTab />}
        {tab === "market"     && <MarketTab />}
        {tab === "squeeze"    && <SqueezeTab onSelectTicker={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />}
        {tab === "insiders"   && <InsidersTab />}
        {tab === "breakout"   && <BreakoutTab onSelectTicker={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />}

        {/* --- Portfolio --- */}
        {tab === "portfolio" && (
          <div className="space-y-4">
            {loadingPortfolio && <div className="flex items-center justify-center py-16 gap-3 text-slate-400"><Spinner /> Loading portfolio…</div>}
            {portfolio && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {[
                    { label: "Total Value", value: `$${portfolio.total_value.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`, color: "text-white" },
                    { label: "Cash",        value: `$${portfolio.cash.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`, color: "text-slate-300" },
                    { label: "Positions Value", value: `$${portfolio.positions_value.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`, color: "text-slate-300" },
                    { label: "Total P&L",   value: `${portfolio.total_pnl >= 0 ? "+" : ""}$${Math.abs(portfolio.total_pnl).toFixed(2)} (${portfolio.total_pnl_pct >= 0 ? "+" : ""}${portfolio.total_pnl_pct.toFixed(2)}%)`, color: portfolio.total_pnl >= 0 ? "text-emerald-400" : "text-red-400" },
                  ].map(item => (
                    <div key={item.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                      <div className="text-slate-500 text-xs mb-1">{item.label}</div>
                      <div className={`text-lg font-bold ${item.color}`}>{item.value}</div>
                    </div>
                  ))}
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-4">Positions</div>
                  {portfolio.positions.length === 0 ? (
                    <div className="text-center py-8 text-slate-500">No open positions. Go to Stock Lookup to paper trade.</div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead><tr className="border-b border-slate-800 text-slate-400 text-xs uppercase"><th className="text-left py-2 px-3">Ticker</th><th className="text-right py-2 px-3">Shares</th><th className="text-right py-2 px-3">Avg Cost</th><th className="text-right py-2 px-3">Current</th><th className="text-right py-2 px-3">Value</th><th className="text-right py-2 px-3">P&L</th></tr></thead>
                        <tbody>
                          {portfolio.positions.map(pos => (
                            <tr key={pos.ticker} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                              <td className="py-2.5 px-3 font-semibold text-white">{pos.ticker}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">{pos.shares}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">${fmt(pos.avg_cost)}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">${fmt(pos.current_price)}</td>
                              <td className="text-right py-2.5 px-3 text-slate-200">${fmt(pos.value)}</td>
                              <td className={`text-right py-2.5 px-3 font-medium ${pos.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>{pos.pnl >= 0 ? "+" : ""}${fmt(pos.pnl)} ({pos.pnl_pct >= 0 ? "+" : ""}{fmt(pos.pnl_pct)}%)</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {portfolio.trades.length > 0 && (
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-4">Recent Trades</div>
                    <div className="space-y-2">
                      {[...portfolio.trades].reverse().map((t, i) => (
                        <div key={i} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-800/50">
                          <div className="flex items-center gap-3">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${t.type === "BUY" ? "bg-emerald-900/50 text-emerald-300" : "bg-red-900/50 text-red-300"}`}>{t.type}</span>
                            <span className="font-medium text-white">{t.ticker}</span>
                            <span className="text-slate-400">{t.shares} shares @ ${fmt(t.price)}</span>
                          </div>
                          <div className="text-slate-300">${fmt(t.total)}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
            {!portfolio && !loadingPortfolio && <div className="text-center py-16 text-slate-500">Portfolio data unavailable</div>}
          </div>
        )}
        {/* --- Bull Flow Top 10 --- */}
        {tab === "bullflow" && (
          <BullFlowTab onSelectTicker={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }} />
        )}

        {tab === "outcomes" && <OutcomesTab />}

      </div>
      </main>
      )}

      {/* ── BOTTOM TICKER ── */}
      <div style={{ height: 22, background: "#050505", borderTop: `1px solid ${BB_BORDER}`, overflow: "hidden", display: "flex", alignItems: "center", flexShrink: 0 }}>
        <div style={{ width: 72, background: "#050505", zIndex: 2, height: "100%", display: "flex", alignItems: "center", paddingLeft: 10, borderRight: `1px solid ${BB_BDR2}`, flexShrink: 0 }}>
          <span style={{ fontSize: 9, color: BB_ORANGE, fontWeight: 700, letterSpacing: "0.1em", fontFamily: BB_FONT }}>LIVE</span>
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <div style={{ display: "flex", whiteSpace: "nowrap", transform: `translateX(${tickPos % 1400}px)`, color: BB_WHITE, fontSize: 10, fontFamily: BB_FONT }}>
            {[tickerStr, tickerStr, tickerStr].map((seg, i) => (
              <span key={i} style={{ paddingRight: 0 }}>
                {seg.split("·").map((part, j) => (
                  <span key={j}>
                    {j > 0 && <span style={{ color: BB_BDR2, margin: "0 6px" }}>·</span>}
                    <span style={{ color: BB_LABEL }}>{part.trim()}</span>
                  </span>
                ))}
              </span>
            ))}
          </div>
        </div>
        <div style={{ width: 100, flexShrink: 0, paddingRight: 10, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, borderLeft: `1px solid ${BB_BDR2}` }}>
          <span style={{ fontSize: 9, color: BB_LABEL, fontFamily: BB_FONT }}>A/D</span>
          <span style={{ fontSize: 9, color: BB_GREEN, fontFamily: BB_FONT }}>▲{headerMkt?.advance_decline?.up ?? "—"}</span>
          <span style={{ fontSize: 9, color: BB_RED, fontFamily: BB_FONT }}>▼{headerMkt?.advance_decline?.down ?? "—"}</span>
        </div>
      </div>
    </div>
  );
}
