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
  fetchConvergence, fetchPremarket, fetchCatalyst, fetchMorningBrief, refreshMorningBrief, fetchDarkPool, fetchPutIntent,
  fetchVolCrush, fetchCallIntent, fetchSmartVsRetail, fetchMaxPain, fetchGammaWall,
  fetchAITrades, fetchAIShortCalls, AIShortCall, fetchSignalFeed, fetchCompositeScore,
  StockAnalysis, ScanResult, BacktestResult, AnalyticsResult, Alert,
  PropSignal, PropPosition, PropTrade, PropDeskResult, SmartMoneySignal, SmartMoneyResult,
  CongressTrade, CongressResult, BullFlowRow, MarketOverview, SqueezeSignal, InsiderTrade, BreakoutSignal,
  SignalOutcome, DailyTop10Result, ConvergenceRow, PremarketRow, MorningBrief, DarkPoolRow, PutIntentRow,
  VolCrushRow, CallIntentRow, SmartVsRetailRow, MaxPainRow, GammaWallRow, GammaStrike,
  AITradeSetup, SignalEvent, CompositeScoreRow,
  fetchAITradeLog, AITradeLogEntry, AITradeLogResult,
  fetchWhaleActivity, fetchWhaleHistory, WhaleBlock, WhaleHistoryBlock,
  fetchTradeWatchlist, addTradeWatchlist, deleteTradeWatchlist, TradeWatchlistEntry,
  fetchUnusualCalls, UnusualCall,
  fetchUnusualCallsLog, UnusualCallsLogEntry,
  saveMyTrade, fetchMyTrades, updateMyTrade, deleteMyTrade, MyTrade,
  fetchNetFlow, NetFlowRow, NetFlowMicrocapResult, fetchNetFlowSingle, NetFlowSingleResult, fetchNetFlowMicrocap,
  NetFlowStreakRow, NetFlowStreakResult, fetchNetFlowMultiday,
  AISignal, AISignalResult, fetchAISignal,
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

  const winKey  = `win_rate_${horizon}` as keyof NonNullable<typeof result>["bucket_stats"][0];
  const retKey  = `avg_ret_${horizon}`  as keyof NonNullable<typeof result>["bucket_stats"][0];

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
          <span className="text-white font-bold">$100/month</span> · cancel anytime
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

// ---- Unusual Calls Tab ---------------------------------------------------
function UnusualCallsTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const BB_F = "JetBrains Mono, monospace";
  const [data, setData]       = useState<{ hits: UnusualCall[]; total: number; scanned: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [saved, setSaved]     = useState<Record<string, boolean>>({});
  const [filter, setFilter]   = useState<"ALL"|"EXPIRING"|"NEAR"|"SHORT">("ALL");

  useEffect(() => {
    setLoading(true);
    fetchUnusualCalls()
      .then(d => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async (e: React.MouseEvent, h: UnusualCall) => {
    e.stopPropagation();
    const key = `${h.ticker}-${h.strike}-${h.expiry}`;
    try {
      await addTradeWatchlist({ ticker: h.ticker, strike: h.strike, expiry: h.expiry, option_type: "CALL", notes: `Unusual Call: ${h.vol_oi}x vol/OI · $${Math.round(h.prem/1000)}K prem · ${h.urgency}` });
      setSaved(s => ({ ...s, [key]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [key]: false })), 2500);
    } catch {}
  };

  const filtered = (data?.hits ?? []).filter(h => filter === "ALL" || h.urgency === filter);

  const urgencyStyle = (u: string) => {
    if (u === "EXPIRING") return { color: "#f87171", bg: "rgba(248,113,113,0.12)", border: "rgba(248,113,113,0.35)", label: "🔴 EXPIRING ≤7d" };
    if (u === "NEAR")     return { color: "#fb923c", bg: "rgba(251,146,60,0.12)",  border: "rgba(251,146,60,0.3)",  label: "🟠 NEAR ≤14d" };
    return                       { color: "#facc15", bg: "rgba(250,204,21,0.1)",   border: "rgba(250,204,21,0.25)", label: "🟡 SHORT ≤30d" };
  };

  const volOiBadge = (r: number) => {
    if (r >= 20) return { color: "#f87171", label: `${r}x 🚨` };
    if (r >= 10) return { color: "#fb923c", label: `${r}x 🔥` };
    if (r >= 5)  return { color: "#facc15", label: `${r}x ⚡` };
    return              { color: "#4ade80", label: `${r}x` };
  };

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ fontFamily: BB_F, fontWeight: 900, color: "#fff", fontSize: 22, margin: 0, marginBottom: 4 }}>🚨 Unusual Calls</h2>
          <p style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12, margin: 0 }}>
            Pure bullish calls · Expiring ≤30 days · Vol/OI ≥3x · "Someone knows something"
            {data ? ` · ${data.scanned} tickers scanned` : " · scanning…"}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["ALL","EXPIRING","NEAR","SHORT"] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{
              padding: "6px 14px", borderRadius: 8, fontFamily: BB_F, fontSize: 11, fontWeight: 700, cursor: "pointer", transition: "all 0.15s",
              background: filter === f ? "rgba(248,113,113,0.15)" : "rgba(255,255,255,0.04)",
              border: `1px solid ${filter === f ? "rgba(248,113,113,0.4)" : "rgba(255,255,255,0.1)"}`,
              color: filter === f ? "#f87171" : "#64748b",
            }}>{f}</button>
          ))}
        </div>
      </div>

      {/* Stats row */}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Unusual Signals Found", val: data.total,                                                  color: "#f87171" },
            { label: "Expiring ≤7 Days",       val: data.hits.filter(h => h.urgency === "EXPIRING").length,     color: "#fb923c" },
            { label: "Vol/OI ≥10x (Extreme)",  val: data.hits.filter(h => h.vol_oi >= 10).length,              color: "#facc15" },
          ].map(s => (
            <div key={s.label} style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 16, padding: "16px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 28, color: s.color, letterSpacing: "-0.04em", marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Legend */}
      <div style={{ background: "rgba(248,113,113,0.04)", border: "1px solid rgba(248,113,113,0.15)", borderRadius: 12, padding: "12px 18px", marginBottom: 20,
        fontFamily: BB_F, fontSize: 11, color: "#94a3b8", lineHeight: 1.7 }}>
        <span style={{ color: "#f87171", fontWeight: 700 }}>How to read: </span>
        Vol/OI = today's volume ÷ existing open interest. A ratio ≥3x means more contracts traded today than exist in the market — someone is aggressively opening <em>new</em> positions.
        Only OTM/slightly-ITM calls (not deep ITM hedges) · ≤30 day expiry = high conviction, short timeframe.
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 6, marginBottom: 16 }}>
            {[0,1,2].map(i => (
              <span key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: "#f87171", display: "inline-block",
                animation: "bounce 1s infinite", animationDelay: `${i*0.15}s` }} />
            ))}
          </div>
          <p style={{ fontFamily: BB_F, color: "#475569", fontSize: 13 }}>Scanning all tickers for unusual near-term call activity… ~30s</p>
        </div>
      )}

      {/* Empty */}
      {!loading && filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🚨</div>
          <p style={{ fontFamily: BB_F, color: "#475569" }}>No unusual call activity detected right now. Markets may be quiet — check back after open.</p>
        </div>
      )}

      {/* Hits list */}
      {!loading && filtered.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {filtered.map((h, i) => {
            const urg  = urgencyStyle(h.urgency);
            const voib = volOiBadge(h.vol_oi);
            const key  = `${h.ticker}-${h.strike}-${h.expiry}`;
            const premK = h.prem >= 1_000_000 ? `$${(h.prem/1_000_000).toFixed(1)}M` : `$${(h.prem/1000).toFixed(0)}k`;
            const otmLabel = h.otm_pct > 0 ? `+${h.otm_pct}% OTM` : h.otm_pct < 0 ? `${Math.abs(h.otm_pct)}% ITM` : "ATM";
            return (
              <div key={i} onClick={() => onSelectTicker(h.ticker)} style={{
                background: "rgba(255,255,255,0.025)", border: `1px solid ${i < 3 ? urg.border : "rgba(255,255,255,0.07)"}`,
                borderRadius: 18, padding: "16px 20px", display: "flex", alignItems: "center",
                justifyContent: "space-between", gap: 16, flexWrap: "wrap", cursor: "pointer",
                transition: "background 0.15s",
              }}
                onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.04)")}
                onMouseLeave={e => (e.currentTarget.style.background = "rgba(255,255,255,0.025)")}
              >
                {/* Left */}
                <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#334155", fontSize: 16, minWidth: 28 }}>#{i+1}</span>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#f1f5f9", fontSize: 20 }}>{h.ticker}</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12 }}>${h.price.toFixed(2)}</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: "rgba(74,222,128,0.12)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.3)" }}>CALL</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: urg.bg, color: urg.color, border: `1px solid ${urg.border}` }}>{urg.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 13, fontWeight: 700 }}>${h.strike} strike</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12 }}>exp {h.expiry}</span>
                      <span style={{ fontFamily: BB_F, color: "#94a3b8", fontSize: 11 }}>{otmLabel}</span>
                      {h.iv > 0 && <span style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>IV {h.iv}%</span>}
                    </div>
                  </div>
                </div>
                {/* Right */}
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 26, letterSpacing: "-0.04em", marginBottom: 2, color: voib.color }}>
                    {voib.label}
                  </div>
                  <div style={{ fontFamily: BB_F, color: "#94a3b8", fontSize: 11, marginBottom: 1 }}>Vol/OI ratio</div>
                  <div style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 13, fontWeight: 700 }}>{premK} premium</div>
                  <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{h.volume.toLocaleString()} vol · {h.oi.toLocaleString()} OI · {h.days_out}d left</div>
                  <button onClick={e => handleSave(e, h)} style={{ marginTop: 8, padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[key] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[key] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[key] ? "#4ade80" : "#64748b" }}>
                    {saved[key] ? "✓ Saved" : "📌 Save"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---- Unusual Calls Log Tab -----------------------------------------------
function UnusualCallsLogTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const BB_F = "JetBrains Mono, monospace";
  const [data, setData]       = useState<{ signals: UnusualCallsLogEntry[]; total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch]   = useState("");
  const [saved, setSaved]     = useState<Record<string, boolean>>({});

  useEffect(() => {
    setLoading(true);
    fetchUnusualCallsLog()
      .then(d => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async (e: React.MouseEvent, h: UnusualCallsLogEntry) => {
    e.stopPropagation();
    const key = `${h.ticker}-${h.strike}-${h.expiry}`;
    try {
      await saveMyTrade({ ticker: h.ticker, strike: h.strike, expiry: h.expiry, vol_oi: h.vol_oi, prem: h.prem, otm_pct: h.otm_pct, urgency: h.urgency, signal_detected_at: h.first_seen });
      setSaved(s => ({ ...s, [key]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [key]: false })), 2500);
    } catch {}
  };

  const filtered = (data?.signals ?? []).filter(h =>
    !search || h.ticker.includes(search.toUpperCase())
  );

  const urgencyStyle = (u: string) => {
    if (u === "EXPIRING") return { color: "#f87171", bg: "rgba(248,113,113,0.12)", border: "rgba(248,113,113,0.35)", label: "🔴 EXPIRING ≤7d" };
    if (u === "NEAR")     return { color: "#fb923c", bg: "rgba(251,146,60,0.12)",  border: "rgba(251,146,60,0.3)",  label: "🟠 NEAR ≤14d" };
    return                       { color: "#facc15", bg: "rgba(250,204,21,0.1)",   border: "rgba(250,204,21,0.25)", label: "🟡 SHORT ≤30d" };
  };

  const volOiBadge = (r: number) => {
    if (r >= 20) return { color: "#f87171" };
    if (r >= 10) return { color: "#fb923c" };
    if (r >= 5)  return { color: "#facc15" };
    return              { color: "#4ade80" };
  };

  const fmt = (iso: string) => {
    try { return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" }) + " ET"; }
    catch { return iso; }
  };

  const totalPrem = filtered.reduce((s, h) => s + h.prem, 0);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ fontFamily: BB_F, fontWeight: 900, color: "#fff", fontSize: 22, margin: 0, marginBottom: 4 }}>📋 Unusual Calls Log</h2>
          <p style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12, margin: 0 }}>
            All-time history · Every unusual call signal ever detected · Newest first
            {data ? ` · ${data.total} signals on record` : " · loading…"}
          </p>
        </div>
      </div>

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Signals on Record",    val: data.total,                                                        color: "#f87171" },
            { label: "Extreme (≥20x Vol/OI)", val: filtered.filter(h => h.vol_oi >= 20).length,                     color: "#fb923c" },
            { label: "Total Premium Tracked", val: `$${(totalPrem/1_000_000).toFixed(1)}M`,                          color: "#facc15" },
          ].map(s => (
            <div key={s.label} style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 16, padding: "16px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 24, color: s.color, letterSpacing: "-0.04em", marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 20, alignItems: "center" }}>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter by ticker…"
          style={{ fontFamily: BB_F, fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.04)", color: "#f1f5f9", outline: "none", width: 180 }} />
        {filtered.length !== (data?.total ?? 0) && (
          <span style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{filtered.length} shown</span>
        )}
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 6, marginBottom: 16 }}>
            {[0,1,2].map(i => <span key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: "#f87171", display: "inline-block", animation: "bounce 1s infinite", animationDelay: `${i*0.15}s` }} />)}
          </div>
          <p style={{ fontFamily: BB_F, color: "#475569", fontSize: 13 }}>Loading historical signals…</p>
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>📋</div>
          <p style={{ fontFamily: BB_F, color: "#475569" }}>No signals logged yet. Open the 🚨 Unusual Calls tab during market hours to start capturing history.</p>
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {filtered.map((h, i) => {
            const urg  = urgencyStyle(h.urgency);
            const voib = volOiBadge(h.vol_oi);
            const key  = `${h.ticker}-${h.strike}-${h.expiry}`;
            const premK = h.prem >= 1_000_000 ? `$${(h.prem/1_000_000).toFixed(1)}M` : `$${(h.prem/1000).toFixed(0)}k`;
            const otmLabel = h.otm_pct > 0 ? `+${h.otm_pct}% OTM` : h.otm_pct < 0 ? `${Math.abs(h.otm_pct)}% ITM` : "ATM";
            return (
              <div key={i} onClick={() => onSelectTicker(h.ticker)} style={{
                background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)",
                borderRadius: 14, padding: "14px 18px", display: "flex", alignItems: "center",
                justifyContent: "space-between", gap: 12, flexWrap: "wrap", cursor: "pointer",
                transition: "background 0.15s",
              }}
                onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.04)")}
                onMouseLeave={e => (e.currentTarget.style.background = "rgba(255,255,255,0.02)")}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#f1f5f9", fontSize: 17 }}>{h.ticker}</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 11 }}>${h.price?.toFixed(2)}</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 10, padding: "2px 7px", borderRadius: 99, background: "rgba(74,222,128,0.12)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.3)" }}>CALL</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 10, padding: "2px 7px", borderRadius: 99, background: urg.bg, color: urg.color, border: `1px solid ${urg.border}` }}>{urg.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 12, fontWeight: 700 }}>${h.strike} strike</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 11 }}>exp {h.expiry}</span>
                      <span style={{ fontFamily: BB_F, color: "#94a3b8", fontSize: 11 }}>{otmLabel}</span>
                      <span style={{ fontFamily: BB_F, color: "#334155", fontSize: 10 }}>Detected {fmt(h.first_seen)}</span>
                    </div>
                  </div>
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 22, letterSpacing: "-0.04em", marginBottom: 1, color: voib.color }}>{h.vol_oi}x</div>
                  <div style={{ fontFamily: BB_F, color: "#94a3b8", fontSize: 10, marginBottom: 1 }}>Vol/OI</div>
                  <div style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 12, fontWeight: 700 }}>{premK}</div>
                  <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 10 }}>{h.volume?.toLocaleString()} vol · {h.oi?.toLocaleString()} OI</div>
                  <button onClick={e => handleSave(e, h)} style={{ marginTop: 6, padding: "4px 10px", borderRadius: 6, fontSize: 10, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[key] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[key] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[key] ? "#4ade80" : "#64748b" }}>
                    {saved[key] ? "✓ Saved" : "📌 Save"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <p style={{ fontFamily: BB_F, color: "#334155", fontSize: 10, marginTop: 20, textAlign: "center" }}>
        Captured every time 🚨 Unusual Calls is scanned · Signals never deleted · Max 500 shown · first_seen = when first detected
      </p>
    </div>
  );
}

// ---- AI Short Calls Tab --------------------------------------------------
function AIShortCallsTab() {
  const BB_BG   = "#0a0a0a";
  const BB_FONT = "JetBrains Mono, monospace";
  const BB_BORDER = "#1a1a1a";
  const BB_ORANGE = "#ff6600";
  const BB_DIM  = "#555";
  const BB_GREEN = "#00e676";

  const [picks, setPicks]         = useState<AIShortCall[]>([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [signalsEvaluated, setSignalsEvaluated] = useState(0);
  const [expanded, setExpanded]   = useState<number | null>(null);
  const [saved, setSaved]         = useState<Record<number, boolean>>({});

  const handleSave = async (e: React.MouseEvent, p: AIShortCall, i: number) => {
    e.stopPropagation();
    try {
      await addTradeWatchlist({ ticker: p.ticker, strike: p.strike, expiry: p.expiry, option_type: "CALL", notes: `AI Short Call: ${p.vol_oi}x vol/OI · $${Math.round(p.prem/1000)}K · ${p.urgency}` });
      setSaved(s => ({ ...s, [i]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [i]: false })), 2500);
    } catch { /* silent */ }
  };

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchAIShortCalls();
      if (d.error) { setError(d.error); setPicks([]); }
      else { setPicks(d.picks || []); setGeneratedAt(d.generated_at); setSignalsEvaluated(d.signals_evaluated || 0); }
    } catch (e: any) {
      setError(e.message || "Request failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { run(); }, []);

  const urgencyColor = (u: string) => {
    if (!u) return BB_DIM;
    const up = u.toUpperCase();
    if (up.includes("HIGH") || up.includes("EXTREME")) return "#ff4444";
    if (up.includes("MED")) return BB_ORANGE;
    return "#aaa";
  };

  return (
    <div style={{ fontFamily: BB_FONT, background: BB_BG, minHeight: "100%", padding: 16 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <span style={{ fontSize: 13, color: BB_ORANGE, fontWeight: 700, letterSpacing: "0.08em" }}>⚡ AI SHORT CALLS</span>
          <span style={{ fontSize: 10, color: BB_DIM, marginLeft: 10 }}>5 AI-PICKED CALLS · ≤30 DAY EXPIRY</span>
          {generatedAt && (
            <span style={{ fontSize: 9, color: BB_DIM, marginLeft: 10 }}>
              Generated {new Date(generatedAt).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          {signalsEvaluated > 0 && (
            <span style={{ fontSize: 9, color: BB_DIM, marginLeft: 8 }}>· {signalsEvaluated} signals evaluated</span>
          )}
        </div>
        <button
          onClick={run}
          disabled={loading}
          style={{ fontSize: 10, fontFamily: BB_FONT, background: loading ? "#111" : BB_ORANGE, color: loading ? BB_DIM : "#000", border: "none", borderRadius: 3, padding: "5px 12px", cursor: loading ? "default" : "pointer", fontWeight: 700 }}
        >
          {loading ? "GENERATING…" : "↻ REGENERATE"}
        </button>
      </div>

      {/* Error / no-data states */}
      {error && (
        <div style={{ background: "#1a0a00", border: `1px solid #ff4400`, borderRadius: 4, padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: "#ff6644", fontWeight: 700, marginBottom: 4 }}>⚠ NOTE</div>
          <div style={{ fontSize: 11, color: "#ccc", lineHeight: 1.6 }}>{error}</div>
          {error.includes("Unusual Calls") && (
            <div style={{ fontSize: 10, color: BB_DIM, marginTop: 8 }}>
              Run a scan in the 🚨 Unusual Calls tab first, then come back and hit Regenerate.
            </div>
          )}
        </div>
      )}

      {!loading && !error && picks.length === 0 && (
        <div style={{ textAlign: "center", color: BB_DIM, fontSize: 11, padding: 40 }}>
          No picks generated yet. Hit Regenerate to run.
        </div>
      )}

      {/* Picks cards */}
      {picks.map((p, i) => {
        const isHigh = p.conviction === "HIGH";
        const accentColor = isHigh ? BB_ORANGE : "#888";
        const isOpen = expanded === i;
        const pnlPct = p.stock_price > 0
          ? (((p.strike - p.stock_price) / p.stock_price) * 100).toFixed(1)
          : "—";

        return (
          <div key={i} style={{ background: "#0d0d0d", border: `1px solid ${isHigh ? "#ff660033" : BB_BORDER}`, borderRadius: 5, marginBottom: 10, overflow: "hidden" }}>
            {/* Card header row */}
            <div
              onClick={() => setExpanded(isOpen ? null : i)}
              style={{ display: "flex", alignItems: "center", padding: "10px 14px", cursor: "pointer", gap: 10 }}
            >
              {/* Rank badge */}
              <div style={{ width: 22, height: 22, borderRadius: "50%", background: accentColor, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <span style={{ fontSize: 10, fontWeight: 900, color: "#000" }}>{i + 1}</span>
              </div>

              {/* Ticker + strike/expiry */}
              <div style={{ flex: "0 0 auto", minWidth: 80 }}>
                <div style={{ fontSize: 14, fontWeight: 900, color: "#fff" }}>{p.ticker}</div>
                <div style={{ fontSize: 9, color: BB_DIM }}>${p.strike} CALL · {p.expiry}</div>
              </div>

              {/* Stats row */}
              <div style={{ flex: 1, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: BB_GREEN, fontWeight: 700 }}>{p.vol_oi}x</div>
                  <div style={{ fontSize: 8, color: BB_DIM }}>VOL/OI</div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: "#fff", fontWeight: 700 }}>${(p.prem / 1000).toFixed(0)}K</div>
                  <div style={{ fontSize: 8, color: BB_DIM }}>PREM</div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: "#fff", fontWeight: 700 }}>{p.days_out}d</div>
                  <div style={{ fontSize: 8, color: BB_DIM }}>DAYS OUT</div>
                </div>
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: p.otm_pct > 5 ? "#aaa" : BB_GREEN, fontWeight: 700 }}>
                    {p.otm_pct > 0 ? "+" : ""}{p.otm_pct}%
                  </div>
                  <div style={{ fontSize: 8, color: BB_DIM }}>OTM</div>
                </div>
              </div>

              {/* Right side: conviction + urgency */}
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4, flexShrink: 0 }}>
                <span style={{ fontSize: 9, fontWeight: 700, color: accentColor, border: `1px solid ${accentColor}`, borderRadius: 3, padding: "1px 5px" }}>
                  {p.conviction}
                </span>
                <span style={{ fontSize: 9, color: urgencyColor(p.urgency) }}>{p.urgency}</span>
              </div>

              <span style={{ fontSize: 9, color: BB_DIM, marginLeft: 4 }}>{isOpen ? "▲" : "▼"}</span>
            </div>

            {/* Expanded detail */}
            {isOpen && (
              <div style={{ borderTop: `1px solid ${BB_BORDER}`, padding: "12px 14px", background: "#0a0a0a" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: 12 }}>
                  <div>
                    <div style={{ fontSize: 9, color: BB_DIM }}>STOCK PRICE</div>
                    <div style={{ fontSize: 12, color: "#fff", fontWeight: 700 }}>${p.stock_price?.toFixed(2)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9, color: BB_DIM }}>BREAKEVEN</div>
                    <div style={{ fontSize: 12, color: BB_ORANGE, fontWeight: 700 }}>${p.breakeven?.toFixed(2)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9, color: BB_DIM }}>NEEDS TO MOVE</div>
                    <div style={{ fontSize: 12, color: "#fff", fontWeight: 700 }}>{pnlPct}% to strike</div>
                  </div>
                </div>

                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 9, color: BB_DIM, marginBottom: 4 }}>⚡ WHY IT STANDS OUT</div>
                  <div style={{ fontSize: 11, color: BB_ORANGE, lineHeight: 1.5 }}>{p.why_it_stands_out}</div>
                </div>

                <div>
                  <div style={{ fontSize: 9, color: BB_DIM, marginBottom: 4 }}>AI THESIS</div>
                  <div style={{ fontSize: 11, color: "#ccc", lineHeight: 1.6 }}>{p.thesis}</div>
                </div>
                <button
                  onClick={e => handleSave(e, p, i)}
                  style={{ marginTop: 12, padding: "5px 14px", borderRadius: 6, fontSize: 10, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[i] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[i] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[i] ? "#00e676" : "#555", fontFamily: BB_FONT }}
                >
                  {saved[i] ? "✓ SAVED TO WATCHLIST" : "📌 SAVE TO WATCHLIST"}
                </button>
              </div>
            )}
          </div>
        );
      })}

      {picks.length > 0 && (
        <div style={{ fontSize: 9, color: BB_DIM, marginTop: 14, lineHeight: 1.7 }}>
          ⚠ These are AI-generated picks based on unusual options flow. Not financial advice. Always verify with your own research before trading.
        </div>
      )}
    </div>
  );
}

// ---- My Trades Tab -------------------------------------------------------
function MyTradesTab() {
  const BB_F = "JetBrains Mono, monospace";
  const [trades, setTrades] = useState<MyTrade[]>([]);
  const [loading, setLoading]   = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [editing, setEditing]   = useState<Record<number, { entry: string; exit: string; contracts: string; notes: string }>>({});

  const load = () => {
    setLoading(true);
    fetchMyTrades().then(d => setTrades(d.trades)).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const startEdit = (t: MyTrade) => {
    setEditing(e => ({
      ...e,
      [t.id]: {
        entry:     t.entry_price != null ? String(t.entry_price) : "",
        exit:      t.exit_price  != null ? String(t.exit_price)  : "",
        contracts: String(t.contracts ?? 1),
        notes:     t.notes ?? "",
      },
    }));
  };

  const saveEdit = async (id: number) => {
    const e = editing[id];
    if (!e) return;
    const entryN  = e.entry     !== "" ? parseFloat(e.entry)     : null;
    const exitN   = e.exit      !== "" ? parseFloat(e.exit)       : null;
    const contsN  = e.contracts !== "" ? parseInt(e.contracts)    : 1;
    let status = "open";
    if (exitN != null && entryN != null) status = exitN >= entryN ? "win" : "loss";
    await updateMyTrade(id, { entry_price: entryN, exit_price: exitN, contracts: contsN, notes: e.notes, status });
    load();
    setEditing(ex => { const n = { ...ex }; delete n[id]; return n; });
  };

  const handleDelete = async (id: number) => {
    await deleteMyTrade(id);
    setTrades(t => t.filter(x => x.id !== id));
    if (expanded === id) setExpanded(null);
  };

  const pnl = (t: MyTrade) => {
    if (t.entry_price == null || t.exit_price == null) return null;
    return (t.exit_price - t.entry_price) * (t.contracts ?? 1) * 100;
  };

  const statusBadge = (t: MyTrade) => {
    const p = pnl(t);
    if (t.status === "win"  || (p != null && p > 0))  return { color: "#4ade80", bg: "rgba(74,222,128,0.12)",  border: "rgba(74,222,128,0.3)",  label: "✅ WIN"  };
    if (t.status === "loss" || (p != null && p < 0))  return { color: "#f87171", bg: "rgba(248,113,113,0.12)", border: "rgba(248,113,113,0.35)", label: "❌ LOSS" };
    return { color: "#94a3b8", bg: "rgba(148,163,184,0.08)", border: "rgba(148,163,184,0.2)", label: "⏳ OPEN" };
  };

  const fmt = (iso: string | null) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" }) + " ET"; }
    catch { return iso; }
  };

  const totalPnl = trades.reduce((s, t) => s + (pnl(t) ?? 0), 0);
  const wins     = trades.filter(t => t.status === "win"  || (pnl(t) ?? 0) > 0).length;
  const losses   = trades.filter(t => t.status === "loss" || (pnl(t) ?? 0) < 0).length;

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontFamily: BB_F, fontWeight: 900, color: "#fff", fontSize: 22, margin: 0, marginBottom: 4 }}>📈 My Trades</h2>
        <p style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12, margin: 0 }}>
          Signals you saved · Click a row to add entry/exit prices · P&amp;L calculated automatically
        </p>
      </div>

      {/* Stats */}
      {trades.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Saved Trades",  val: trades.length,                                                          color: "#94a3b8" },
            { label: "Wins",          val: wins,                                                                    color: "#4ade80" },
            { label: "Losses",        val: losses,                                                                  color: "#f87171" },
            { label: "Total P&L",     val: `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`, color: totalPnl >= 0 ? "#4ade80" : "#f87171" },
          ].map(s => (
            <div key={s.label} style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 16, padding: "16px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 26, color: s.color, letterSpacing: "-0.04em", marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {loading && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 6, marginBottom: 16 }}>
            {[0,1,2].map(i => <span key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: "#4ade80", display: "inline-block", animation: "bounce 1s infinite", animationDelay: `${i*0.15}s` }} />)}
          </div>
          <p style={{ fontFamily: BB_F, color: "#475569", fontSize: 13 }}>Loading your trades…</p>
        </div>
      )}

      {!loading && trades.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>📈</div>
          <p style={{ fontFamily: BB_F, color: "#475569" }}>No trades saved yet. Hit <strong style={{ color: "#94a3b8" }}>📌 My Trade</strong> on any signal in the 🚨 Unusual Calls or 📋 Calls Log tabs.</p>
        </div>
      )}

      {!loading && trades.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {trades.map(t => {
            const sb   = statusBadge(t);
            const p    = pnl(t);
            const isEx = expanded === t.id;
            const ed   = editing[t.id];
            const premK = t.prem != null ? (t.prem >= 1_000_000 ? `$${(t.prem/1_000_000).toFixed(1)}M` : `$${(t.prem/1000).toFixed(0)}k`) : "—";
            return (
              <div key={t.id} style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 18, overflow: "hidden", transition: "border-color 0.2s" }}>
                {/* Summary row */}
                <div onClick={() => { setExpanded(isEx ? null : t.id); if (!isEx) startEdit(t); }}
                  style={{ padding: "16px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap", cursor: "pointer" }}
                  onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
                  onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                >
                  {/* Left */}
                  <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#f1f5f9", fontSize: 19 }}>{t.ticker}</span>
                        <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99, background: "rgba(74,222,128,0.12)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.3)" }}>CALL</span>
                        <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99, background: sb.bg, color: sb.color, border: `1px solid ${sb.border}` }}>{sb.label}</span>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 13, fontWeight: 700 }}>${t.strike} strike · exp {t.expiry}</span>
                        {t.vol_oi != null && <span style={{ fontFamily: BB_F, color: "#facc15", fontSize: 12 }}>{t.vol_oi}x Vol/OI</span>}
                        {t.prem != null && <span style={{ fontFamily: BB_F, color: "#94a3b8", fontSize: 11 }}>{premK} premium</span>}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 3, flexWrap: "wrap" }}>
                        {t.signal_detected_at && <span style={{ fontFamily: BB_F, color: "#334155", fontSize: 10 }}>Signal: {fmt(t.signal_detected_at)}</span>}
                        <span style={{ fontFamily: BB_F, color: "#1e293b", fontSize: 10 }}>Saved: {fmt(t.saved_at)}</span>
                      </div>
                    </div>
                  </div>
                  {/* Right */}
                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    {t.entry_price != null && (
                      <div style={{ fontFamily: BB_F, fontSize: 12, color: "#94a3b8" }}>Entry: <strong style={{ color: "#f1f5f9" }}>${t.entry_price}</strong></div>
                    )}
                    {t.exit_price != null && (
                      <div style={{ fontFamily: BB_F, fontSize: 12, color: "#94a3b8" }}>Exit: <strong style={{ color: "#f1f5f9" }}>${t.exit_price}</strong></div>
                    )}
                    {p != null && (
                      <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 20, color: p >= 0 ? "#4ade80" : "#f87171", letterSpacing: "-0.04em", marginTop: 2 }}>
                        {p >= 0 ? "+" : ""}${Math.abs(p).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                      </div>
                    )}
                    {p == null && t.entry_price == null && (
                      <div style={{ fontFamily: BB_F, fontSize: 11, color: "#334155" }}>Click to add prices ↓</div>
                    )}
                    <div style={{ fontFamily: BB_F, fontSize: 11, color: "#334155", marginTop: 2 }}>{t.contracts} contract{t.contracts !== 1 ? "s" : ""}</div>
                  </div>
                </div>

                {/* Expanded edit panel */}
                {isEx && ed && (
                  <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", padding: "18px 20px", background: "rgba(0,0,0,0.2)" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(160px,1fr))", gap: 14, marginBottom: 14 }}>
                      {[
                        { label: "Entry price (per contract $)",  key: "entry",     placeholder: "e.g. 2.50" },
                        { label: "Exit price (per contract $)",   key: "exit",      placeholder: "e.g. 6.00" },
                        { label: "# of contracts",                key: "contracts", placeholder: "e.g. 5" },
                      ].map(f => (
                        <div key={f.key}>
                          <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 10, marginBottom: 5 }}>{f.label}</div>
                          <input
                            value={(ed as Record<string,string>)[f.key]}
                            onChange={ev => setEditing(e => ({ ...e, [t.id]: { ...e[t.id], [f.key]: ev.target.value } }))}
                            placeholder={f.placeholder}
                            style={{ width: "100%", fontFamily: BB_F, fontSize: 12, padding: "7px 10px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.05)", color: "#f1f5f9", outline: "none", boxSizing: "border-box" }}
                          />
                        </div>
                      ))}
                    </div>
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 10, marginBottom: 5 }}>Notes (optional)</div>
                      <input
                        value={ed.notes}
                        onChange={ev => setEditing(e => ({ ...e, [t.id]: { ...e[t.id], notes: ev.target.value } }))}
                        placeholder="e.g. Took profit at open · Unusual call seen day before earnings"
                        style={{ width: "100%", fontFamily: BB_F, fontSize: 12, padding: "7px 10px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.05)", color: "#f1f5f9", outline: "none", boxSizing: "border-box" }}
                      />
                    </div>
                    {/* Live P&L preview */}
                    {ed.entry !== "" && ed.exit !== "" && (
                      (() => {
                        const ep = parseFloat(ed.entry), xp = parseFloat(ed.exit), ct = parseInt(ed.contracts) || 1;
                        const preview = (!isNaN(ep) && !isNaN(xp)) ? (xp - ep) * ct * 100 : null;
                        return preview != null ? (
                          <div style={{ fontFamily: BB_F, fontSize: 13, marginBottom: 14, color: preview >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                            P&L Preview: {preview >= 0 ? "+" : ""}${preview.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                            <span style={{ fontWeight: 400, color: "#475569", fontSize: 11 }}> ({ct} contract{ct !== 1 ? "s" : ""} × 100 shares)</span>
                          </div>
                        ) : null;
                      })()
                    )}
                    <div style={{ display: "flex", gap: 10 }}>
                      <button onClick={() => saveEdit(t.id)} style={{ padding: "8px 20px", borderRadius: 9, fontFamily: BB_F, fontSize: 12, fontWeight: 700, cursor: "pointer", background: "rgba(74,222,128,0.15)", border: "1px solid rgba(74,222,128,0.4)", color: "#4ade80" }}>
                        💾 Save
                      </button>
                      <button onClick={() => { setExpanded(null); setEditing(e => { const n = {...e}; delete n[t.id]; return n; }); }} style={{ padding: "8px 16px", borderRadius: 9, fontFamily: BB_F, fontSize: 12, fontWeight: 700, cursor: "pointer", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.1)", color: "#64748b" }}>
                        Cancel
                      </button>
                      <button onClick={() => handleDelete(t.id)} style={{ marginLeft: "auto", padding: "8px 14px", borderRadius: 9, fontFamily: BB_F, fontSize: 11, fontWeight: 700, cursor: "pointer", background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.25)", color: "#f87171" }}>
                        🗑 Delete
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <p style={{ fontFamily: BB_F, color: "#334155", fontSize: 10, marginTop: 20, textAlign: "center" }}>
        P&L = (exit − entry) × contracts × 100 · Each options contract = 100 shares · All times Eastern
      </p>
    </div>
  );
}

// ---- Trade Watchlist Tab -------------------------------------------------
function TradeWatchlistTab() {
  const BB = "#060c14";
  const PANEL = "#0b1320";
  const BORDER = "rgba(255,255,255,0.07)";
  const GREEN = "#22c55e";
  const RED = "#ef4444";
  const ORANGE = "#f97316";
  const LABEL = "#64748b";

  const qc = useQueryClient();
  const [form, setForm] = useState({ ticker: "", strike: "", expiry: "", option_type: "CALL", entry_price: "", contracts: "1", notes: "" });
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["trade-watchlist"],
    queryFn: fetchTradeWatchlist,
    refetchInterval: 60000,
  });

  const handleAdd = async () => {
    if (!form.ticker || !form.strike || !form.expiry) { setAddError("Ticker, strike, and expiry are required"); return; }
    setAdding(true); setAddError(null);
    try {
      await addTradeWatchlist({
        ticker: form.ticker.toUpperCase().trim(),
        strike: parseFloat(form.strike),
        expiry: form.expiry,
        option_type: form.option_type,
        entry_price: form.entry_price ? parseFloat(form.entry_price) : null,
        contracts: parseInt(form.contracts) || 1,
        notes: form.notes.trim() || undefined,
      });
      setForm({ ticker: "", strike: "", expiry: "", option_type: "CALL", entry_price: "", contracts: "1", notes: "" });
      qc.invalidateQueries({ queryKey: ["trade-watchlist"] });
    } catch (e: any) { setAddError(e?.message ?? "Failed to save"); }
    finally { setAdding(false); }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteTradeWatchlist(id);
      qc.invalidateQueries({ queryKey: ["trade-watchlist"] });
    } catch {}
  };

  const inputStyle: React.CSSProperties = { background: "rgba(255,255,255,0.04)", border: `1px solid ${BORDER}`, borderRadius: 6, color: "#f1f5f9", fontSize: 13, padding: "7px 10px", outline: "none", width: "100%", fontFamily: "IBM Plex Mono, monospace" };
  const labelStyle: React.CSSProperties = { color: LABEL, fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 4, display: "block" };

  return (
    <div style={{ padding: "16px", maxWidth: 700, margin: "0 auto" }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ color: "#f1f5f9", fontSize: 18, fontWeight: 900, margin: 0 }}>📌 Trade Watchlist</h2>
        <p style={{ color: LABEL, fontSize: 12, marginTop: 4 }}>Save calls you're watching. Auto-expires after 30 days.</p>
      </div>

      {/* Add Form */}
      <div style={{ background: PANEL, border: `1px solid ${BORDER}`, borderRadius: 10, padding: 16, marginBottom: 20 }}>
        <div style={{ color: "#f1f5f9", fontSize: 12, fontWeight: 700, marginBottom: 12, letterSpacing: "0.05em" }}>+ ADD TRADE TO WATCH</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 10 }}>
          <div>
            <span style={labelStyle}>TICKER</span>
            <input style={inputStyle} placeholder="INTC" value={form.ticker} onChange={e => setForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))} />
          </div>
          <div>
            <span style={labelStyle}>STRIKE</span>
            <input style={inputStyle} placeholder="30" type="number" value={form.strike} onChange={e => setForm(f => ({ ...f, strike: e.target.value }))} />
          </div>
          <div>
            <span style={labelStyle}>EXPIRY</span>
            <input style={{ ...inputStyle, colorScheme: "dark" }} type="date" value={form.expiry} onChange={e => setForm(f => ({ ...f, expiry: e.target.value }))} />
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 2fr", gap: 10, marginBottom: 10 }}>
          <div>
            <span style={labelStyle}>TYPE</span>
            <div style={{ display: "flex", gap: 4 }}>
              {["CALL","PUT"].map(t => (
                <button key={t} onClick={() => setForm(f => ({ ...f, option_type: t }))}
                  style={{ flex: 1, padding: "7px 0", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.15s",
                    background: form.option_type === t ? (t === "CALL" ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)") : "rgba(255,255,255,0.03)",
                    borderColor: form.option_type === t ? (t === "CALL" ? GREEN : RED) : BORDER,
                    color: form.option_type === t ? (t === "CALL" ? GREEN : RED) : LABEL }}>
                  {t}
                </button>
              ))}
            </div>
          </div>
          <div>
            <span style={labelStyle}>ENTRY $</span>
            <input style={inputStyle} placeholder="1.50" type="number" step="0.01" value={form.entry_price} onChange={e => setForm(f => ({ ...f, entry_price: e.target.value }))} />
          </div>
          <div>
            <span style={labelStyle}>CONTRACTS</span>
            <input style={inputStyle} placeholder="1" type="number" value={form.contracts} onChange={e => setForm(f => ({ ...f, contracts: e.target.value }))} />
          </div>
          <div>
            <span style={labelStyle}>NOTES (optional)</span>
            <input style={inputStyle} placeholder="High accum, deep OTM..." value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
          </div>
        </div>
        {addError && <div style={{ color: RED, fontSize: 11, marginBottom: 8 }}>{addError}</div>}
        <button onClick={handleAdd} disabled={adding}
          style={{ background: adding ? "rgba(34,197,94,0.05)" : "rgba(34,197,94,0.12)", border: `1px solid rgba(34,197,94,0.3)`, color: GREEN, borderRadius: 7, padding: "8px 18px", fontSize: 12, fontWeight: 700, cursor: adding ? "not-allowed" : "pointer" }}>
          {adding ? "Saving…" : "SAVE TRADE"}
        </button>
      </div>

      {/* Saved Trades */}
      {isLoading && <div style={{ color: LABEL, textAlign: "center", padding: 40 }}>Loading…</div>}
      {error && <div style={{ color: RED, textAlign: "center", padding: 20 }}>Failed to load watchlist</div>}
      {!isLoading && data?.trades.length === 0 && (
        <div style={{ textAlign: "center", padding: 48, color: LABEL }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 14, color: "#94a3b8" }}>No trades saved yet</div>
          <div style={{ fontSize: 12, marginTop: 6 }}>Add an option trade above to start tracking it</div>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(data?.trades ?? []).map((trade: TradeWatchlistEntry) => {
          const isCall = trade.option_type === "CALL";
          const otmPct = trade.strike_vs_price_pct;
          const otmLabel = otmPct == null ? "—" : otmPct > 0 ? `+${otmPct.toFixed(1)}% OTM` : `${Math.abs(otmPct).toFixed(1)}% ITM`;
          const otmColor = otmPct == null ? LABEL : otmPct > 0 ? ORANGE : GREEN;
          const dteColor = (trade.days_to_expiry ?? 999) <= 14 ? RED : (trade.days_to_expiry ?? 999) <= 30 ? ORANGE : GREEN;
          const expiring = (trade.days_to_expiry ?? 999) <= 7;
          return (
            <div key={trade.id} style={{ background: PANEL, border: `1px solid ${expiring ? "rgba(239,68,68,0.3)" : BORDER}`, borderRadius: 10, padding: "14px 16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: "#f1f5f9", fontSize: 18, fontWeight: 900, fontFamily: "IBM Plex Mono, monospace" }}>{trade.ticker}</span>
                  <span style={{ background: isCall ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)", color: isCall ? GREEN : RED, border: `1px solid ${isCall ? "rgba(34,197,94,0.3)" : "rgba(239,68,68,0.3)"}`, borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 700 }}>{trade.option_type}</span>
                  {expiring && <span style={{ background: "rgba(239,68,68,0.12)", color: RED, border: "1px solid rgba(239,68,68,0.3)", borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 700 }}>⚠ EXPIRING SOON</span>}
                </div>
                <button onClick={() => handleDelete(trade.id)}
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: RED, borderRadius: 6, padding: "4px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer" }}>
                  Remove
                </button>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginTop: 12 }}>
                <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: 6, padding: "8px 10px" }}>
                  <div style={{ color: LABEL, fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 3 }}>STRIKE</div>
                  <div style={{ color: "#f1f5f9", fontSize: 14, fontWeight: 700, fontFamily: "IBM Plex Mono, monospace" }}>${trade.strike}</div>
                  <div style={{ color: otmColor, fontSize: 10, fontWeight: 600, marginTop: 2 }}>{otmLabel}</div>
                </div>
                <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: 6, padding: "8px 10px" }}>
                  <div style={{ color: LABEL, fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 3 }}>STOCK NOW</div>
                  <div style={{ color: "#f1f5f9", fontSize: 14, fontWeight: 700, fontFamily: "IBM Plex Mono, monospace" }}>{trade.current_price != null ? `$${trade.current_price.toFixed(2)}` : "—"}</div>
                </div>
                <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: 6, padding: "8px 10px" }}>
                  <div style={{ color: LABEL, fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 3 }}>EXPIRY</div>
                  <div style={{ color: dteColor, fontSize: 13, fontWeight: 700, fontFamily: "IBM Plex Mono, monospace" }}>{trade.days_to_expiry != null ? `${trade.days_to_expiry}d` : "—"}</div>
                  <div style={{ color: LABEL, fontSize: 10, marginTop: 2 }}>{trade.expiry}</div>
                </div>
                <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: 6, padding: "8px 10px" }}>
                  <div style={{ color: LABEL, fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", marginBottom: 3 }}>COST BASIS</div>
                  <div style={{ color: "#f1f5f9", fontSize: 13, fontWeight: 700, fontFamily: "IBM Plex Mono, monospace" }}>{trade.total_cost != null ? `$${trade.total_cost.toLocaleString()}` : "—"}</div>
                  <div style={{ color: LABEL, fontSize: 10, marginTop: 2 }}>{trade.contracts} × ${trade.entry_price ?? "?"} × 100</div>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10 }}>
                {trade.notes && <span style={{ color: "#94a3b8", fontSize: 11, fontStyle: "italic" }}>"{trade.notes}"</span>}
                {!trade.notes && <span />}
                <span style={{ color: LABEL, fontSize: 10 }}>Saved {trade.days_held === 0 ? "today" : `${trade.days_held}d ago`} · {trade.saved_at?.slice(0, 10)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- Morning Brief Tab ---------------------------------------------------
function MorningBriefTab() {
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (force = false) => {
    setLoading(true); setError(null);
    try {
      if (force) await refreshMorningBrief();
      const data = await fetchMorningBrief();
      setBrief(data);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const dateLabel = brief?.date ? new Date(brief.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }) : "";
  const genTime  = brief?.generated_at ? new Date(brief.generated_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" }) : "";

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">AI Morning Brief</h2>
          <p className="text-slate-500 text-sm mt-0.5">Claude analyzes today's top flow and writes your daily edge</p>
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all"
          style={{ background: loading ? "rgba(34,197,94,0.05)" : "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>
          {loading ? "Generating…" : "↻ Refresh"}
        </button>
      </div>

      {error && <div className="rounded-xl p-4 mb-4 text-red-400 text-sm" style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>{error}</div>}

      {loading && !brief && (
        <div className="rounded-2xl p-8 text-center" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="flex items-center justify-center gap-3 text-slate-400 text-sm">
            <span className="flex gap-1">{[0,1,2].map(i=><span key={i} className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-bounce" style={{animationDelay:`${i*0.15}s`}}/>)}</span>
            Claude is analyzing today's unusual flow…
          </div>
        </div>
      )}

      {brief?.brief && (
        <div className="rounded-2xl p-6 space-y-4" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm" style={{ background: "linear-gradient(135deg,#ea580c,#f97316)" }}>🤖</div>
              <div>
                <div className="text-white font-bold text-sm">Claude — Morning Brief</div>
                <div className="text-slate-500 text-xs">{dateLabel}{genTime ? ` · Generated ${genTime} ET` : ""}</div>
              </div>
            </div>
            {brief.tickers.length > 0 && (
              <div className="flex gap-1.5 flex-wrap">
                {brief.tickers.map(t => (
                  <span key={t} className="px-2 py-0.5 rounded-full text-xs font-bold" style={{ background: "rgba(34,197,94,0.12)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>{t}</span>
                ))}
              </div>
            )}
          </div>
          <div className="border-t pt-4" style={{ borderColor: "rgba(255,255,255,0.07)" }}>
            {brief.brief.split("\n\n").map((para, i) => (
              <p key={i} className="text-slate-300 leading-relaxed text-sm mb-3 last:mb-0">{para}</p>
            ))}
          </div>
          {brief.cached && <div className="text-slate-600 text-xs">Cached for today · Refreshes automatically tomorrow</div>}
        </div>
      )}
    </div>
  );
}

// ---- Convergence Tab (Volume + Options Convergence) ----------------------
function ConvergenceTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<ConvergenceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      const data = await fetchConvergence();
      setResults(data.results); setScanned(data.scanned); setLastRun(new Date());
    } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { run(); }, []);

  const scoreColor = (s: number) => s >= 10 ? "#4ade80" : s >= 6 ? "#86efac" : s >= 4 ? "#fbbf24" : "#f87171";

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Smart Money Convergence</h2>
          <p className="text-slate-500 text-sm mt-0.5">Stocks with BOTH unusual volume AND heavy call flow — the highest-conviction setup</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} signals · {scanned} scanned</span>}
          <button onClick={run} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold transition-all"
            style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>
            {loading ? "Scanning…" : "↻ Scan Now"}
          </button>
        </div>
      </div>

      <div className="rounded-xl p-4 mb-6 text-sm" style={{ background: "rgba(34,197,94,0.05)", border: "1px solid rgba(34,197,94,0.15)" }}>
        <span className="text-emerald-400 font-bold">How it works: </span>
        <span className="text-slate-400">Convergence Score = Volume Ratio × Call/Put Ratio. A score of 10+ means volume is running 5× average AND calls are 2× puts — that's institutional accumulation.</span>
      </div>

      {loading && results.length === 0 && (
        <div className="text-center py-16 text-slate-500 text-sm">Scanning {scanned || "50+"} tickers for convergence signals…</div>
      )}

      {!loading && results.length === 0 && lastRun && (
        <div className="text-center py-16 text-slate-500 text-sm">No convergence signals right now. Markets may be quiet — check back after 10am ET.</div>
      )}

      {results.length > 0 && (
        <div className="overflow-x-auto -mx-2 px-2">
          <div className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", minWidth: 520 }}>
            <div className="grid text-xs font-bold text-slate-500 uppercase px-4 py-2.5" style={{ gridTemplateColumns: "28px 60px 72px 70px 70px 70px 80px", borderBottom: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
              <span>#</span><span>Ticker</span><span className="text-right">Price</span><span className="text-right">Vol</span><span className="text-right">C/P</span><span className="text-right">Prem.</span><span className="text-right">Score</span>
            </div>
            {results.map((r, i) => (
              <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)} className="grid items-center px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors"
                style={{ gridTemplateColumns: "28px 60px 72px 70px 70px 70px 80px", borderBottom: i < results.length - 1 ? "1px solid rgba(255,255,255,0.04)" : "none" }}>
                <span className="text-slate-600 text-xs">{i + 1}</span>
                <span className="font-black text-white text-sm">{r.ticker}</span>
                <span className="text-right text-slate-300 text-sm font-medium">${r.price.toFixed(2)}</span>
                <span className="text-right text-amber-400 text-sm font-bold">{r.vol_ratio.toFixed(1)}×</span>
                <span className="text-right text-emerald-400 text-sm font-bold">{r.call_put_ratio.toFixed(1)}×</span>
                <span className="text-right text-slate-400 text-xs">${r.premium_m.toFixed(1)}M</span>
                <div className="flex justify-end">
                  <span className="px-2 py-0.5 rounded-full text-xs font-black" style={{ background: `${scoreColor(r.convergence_score)}18`, color: scoreColor(r.convergence_score), border: `1px solid ${scoreColor(r.convergence_score)}40` }}>
                    {r.convergence_score.toFixed(1)}<span style={{ opacity: 0.5, fontWeight: 400 }}>/10</span>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Dark Pool Radar Tab -------------------------------------------------
function DarkPoolTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<DarkPoolRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [date, setDate] = useState<string | null>(null);
  const [totalInDb, setTotalInDb] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      const data = await fetchDarkPool();
      setResults(data.results);
      setDate(data.date);
      setTotalInDb(data.total_in_db);
      setLastRun(new Date());
    } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { run(); }, []);

  const signalColor = (s: DarkPoolRow["signal"]) =>
    s === "EXTREME" ? "#f87171" : s === "HIGH" ? "#fb923c" : s === "ELEVATED" ? "#fbbf24" : "#64748b";

  const signalBg = (s: DarkPoolRow["signal"]) =>
    s === "EXTREME" ? "rgba(248,113,113,0.12)" : s === "HIGH" ? "rgba(251,146,60,0.12)" : s === "ELEVATED" ? "rgba(251,191,36,0.10)" : "rgba(100,116,139,0.10)";

  const scoreColor = (sc: number) => sc >= 7 ? "#f87171" : sc >= 5 ? "#fb923c" : sc >= 3 ? "#fbbf24" : "#94a3b8";

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Dark Pool Radar</h2>
          <p className="text-slate-500 text-sm mt-0.5">Off-exchange short volume from FINRA — elevated % signals institutional accumulation in the dark</p>
        </div>
        <div className="flex items-center gap-3">
          {date && <span className="text-slate-600 text-xs">Data: {date} · {results.length} signals</span>}
          <button onClick={run} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold transition-all"
            style={{ background: "rgba(139,92,246,0.1)", border: "1px solid rgba(139,92,246,0.25)", color: "#a78bfa" }}>
            {loading ? "Loading…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      <div className="rounded-xl p-4 mb-6 text-sm" style={{ background: "rgba(139,92,246,0.05)", border: "1px solid rgba(139,92,246,0.15)" }}>
        <span className="font-bold" style={{ color: "#a78bfa" }}>How to read this: </span>
        <span className="text-slate-400">
          High DP Vol % means institutions are active in the dark — but that alone doesn't tell you direction. We cross-reference each ticker's live call/put ratio to determine <span className="text-emerald-400 font-semibold">BULLISH</span> (C/P ≥ 1.5×, dark pool + calls = accumulation) vs <span className="text-red-400 font-semibold">BEARISH</span> (C/P ≤ 0.7×, dark pool + puts = distribution). Data is 1 day delayed (FINRA schedule).
        </span>
      </div>

      {loading && results.length === 0 && (
        <div className="text-center py-16 text-slate-500 text-sm">Fetching FINRA dark pool data…</div>
      )}

      {!loading && results.length === 0 && lastRun && (
        <div className="text-center py-16 text-slate-500 text-sm">No elevated dark pool signals in the watchlist right now.</div>
      )}

      {results.length > 0 && (
        <div className="overflow-x-auto -mx-2 px-2">
          <div className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", minWidth: 500 }}>
            <div className="grid text-xs font-bold text-slate-500 uppercase px-4 py-2.5"
              style={{ gridTemplateColumns: "24px 52px 58px 62px 72px 90px", borderBottom: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.02)" }}>
              <span>#</span>
              <span>Ticker</span>
              <span className="text-right">DP%</span>
              <span className="text-center">Bias</span>
              <span className="text-center">Flow</span>
              <span className="text-center">Conviction</span>
            </div>
            {results.map((r, i) => {
              const biasColor = r.bias === "BULLISH" ? "#4ade80" : r.bias === "BEARISH" ? "#f87171" : "#64748b";
              const biasBg   = r.bias === "BULLISH" ? "rgba(74,222,128,0.10)" : r.bias === "BEARISH" ? "rgba(248,113,113,0.10)" : "rgba(100,116,139,0.10)";
              const flowColor = r.flow === "INFLOW" ? "#34d399" : r.flow === "OUTFLOW" ? "#f87171" : "#64748b";
              const flowBg   = r.flow === "INFLOW" ? "rgba(52,211,153,0.10)" : r.flow === "OUTFLOW" ? "rgba(248,113,113,0.10)" : "rgba(100,116,139,0.08)";
              const flowLabel = r.flow === "INFLOW" ? "▲" : r.flow === "OUTFLOW" ? "▼" : "—";
              const cvColor = r.conviction === "STRONG BUY" ? "#4ade80"
                : r.conviction === "BUY" || r.conviction === "INFLOW" ? "#86efac"
                : r.conviction === "STRONG SELL" ? "#f87171"
                : r.conviction === "SELL" || r.conviction === "OUTFLOW" ? "#fca5a5"
                : "#64748b";
              const cvBg = r.conviction === "STRONG BUY" ? "rgba(74,222,128,0.15)"
                : r.conviction === "BUY" || r.conviction === "INFLOW" ? "rgba(134,239,172,0.10)"
                : r.conviction === "STRONG SELL" ? "rgba(248,113,113,0.15)"
                : r.conviction === "SELL" || r.conviction === "OUTFLOW" ? "rgba(252,165,165,0.10)"
                : "rgba(100,116,139,0.08)";
              return (
                <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)}
                  className="grid items-center px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors"
                  style={{ gridTemplateColumns: "24px 52px 58px 62px 72px 90px", borderBottom: i < results.length - 1 ? "1px solid rgba(255,255,255,0.04)" : "none" }}>
                  <span className="text-slate-600 text-xs">{i + 1}</span>
                  <span className="font-black text-white text-sm">{r.ticker}</span>
                  <span className="text-right font-bold text-sm" style={{ color: signalColor(r.signal) }}>{r.short_pct.toFixed(1)}%</span>
                  <div className="flex justify-center">
                    <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                      style={{ background: biasBg, color: biasColor }}
                      title={r.bias === "UNKNOWN" ? "Options data available during market hours" : `C/P ratio: ${r.call_put_ratio}×`}>
                      {r.bias === "UNKNOWN" ? "—" : r.bias}
                    </span>
                  </div>
                  <div className="flex justify-center">
                    <span className="px-2 py-0.5 rounded text-xs font-bold"
                      style={{ background: flowBg, color: flowColor }}>
                      {flowLabel} {r.flow === "UNKNOWN" ? "—" : r.flow}
                    </span>
                  </div>
                  <div className="flex justify-center">
                    <span className="px-1.5 py-0.5 rounded text-xs font-black whitespace-nowrap"
                      style={{ background: cvBg, color: cvColor, border: `1px solid ${cvColor}30` }}>
                      {r.conviction}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {results.length > 0 && totalInDb > 0 && (
        <p className="text-slate-700 text-xs mt-4 text-center">
          FINRA database contains {totalInDb.toLocaleString()} symbols · Showing watchlist tickers with elevated dark pool activity
        </p>
      )}
    </div>
  );
}

// ---- AI Trades Tab -------------------------------------------------------
function AITradesTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [trades, setTrades]           = useState<AITradeSetup[]>([]);
  const [loading, setLoading]         = useState(false);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [scanned, setScanned]         = useState(0);
  const [expanded, setExpanded]       = useState<number | null>(0);
  const [isSubscribed]                = useState(false);
  const [sources, setSources]         = useState<string[]>([]);
  const [error, setError]             = useState<string | null>(null);
  const [saved, setSaved]             = useState<Record<string, boolean>>({});

  const handleSave = async (e: React.MouseEvent, t: AITradeSetup) => {
    e.stopPropagation();
    try {
      await addTradeWatchlist({ ticker: t.ticker, strike: t.entry_strike, expiry: t.expiry, option_type: "CALL", notes: `AI Trade: ${t.setup_type} · ${t.conviction} conviction` });
      setSaved(s => ({ ...s, [t.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [t.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

  const [warming, setWarming]         = useState(false);
  const [warmCountdown, setWarmCountdown] = useState(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const countTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const run = async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const d = await fetchAITrades();
      if (d.warming) {
        setWarming(true);
        setTrades([]);
        // Start a 40s countdown then auto-retry
        let secs = 40;
        setWarmCountdown(secs);
        if (countTimerRef.current) clearInterval(countTimerRef.current);
        countTimerRef.current = setInterval(() => {
          secs -= 1;
          setWarmCountdown(secs);
          if (secs <= 0) { clearInterval(countTimerRef.current!); }
        }, 1000);
        if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
        retryTimerRef.current = setTimeout(() => { setWarming(false); run(); }, 41000);
        return;
      }
      setWarming(false);
      if (countTimerRef.current) clearInterval(countTimerRef.current);
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (d.error) { setError(d.error); setTrades([]); return; }
      setTrades(d.trades || []);
      setGeneratedAt(d.generated_at);
      setScanned(d.tickers_scanned);
      setSources(d.signal_sources || []);
    } catch (e: any) { setError(String(e)); } finally { if (!silent) setLoading(false); }
  };

  useEffect(() => {
    run();
    return () => {
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (countTimerRef.current) clearInterval(countTimerRef.current);
    };
  }, []);

  const dColor = (d: string) => d === "BULLISH" ? "#4ade80" : d === "BEARISH" ? "#f87171" : "#fbbf24";
  const dBg    = (d: string) => d === "BULLISH" ? "rgba(74,222,128,0.08)" : d === "BEARISH" ? "rgba(248,113,113,0.08)" : "rgba(251,191,36,0.08)";
  const rColor = (r: string) => r === "LOW" ? "#4ade80" : r === "HIGH" ? "#f87171" : "#fbbf24";

  return (
    <div>
      <div className="flex items-center justify-between mb-2 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight flex items-center gap-2">
            🤖 AI Trade Setups
            <span className="text-xs px-2 py-0.5 rounded-full font-normal" style={{ background: "rgba(251,191,36,0.12)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.2)" }}>GPT-4o</span>
            <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-normal" style={{ background: "rgba(255,255,255,0.04)", color: "#94a3b8", border: "1px solid rgba(255,255,255,0.1)" }}>
              <svg width="11" height="11" viewBox="0 0 41 41" fill="none"><path d="M37.532 16.87a9.963 9.963 0 0 0-.856-8.184 10.078 10.078 0 0 0-10.855-4.835 9.964 9.964 0 0 0-6.52-3.272A10.08 10.08 0 0 0 8.733 5.183a9.965 9.965 0 0 0-6.663 4.81 10.079 10.079 0 0 0 1.24 11.817 9.965 9.965 0 0 0 .856 8.185 10.079 10.079 0 0 0 10.855 4.835 9.965 9.965 0 0 0 6.52 3.272 10.08 10.08 0 0 0 10.568-4.604 9.965 9.965 0 0 0 6.663-4.81 10.079 10.079 0 0 0-1.24-11.818zM22.498 37.886a7.474 7.474 0 0 1-4.799-1.735c.061-.033.168-.091.237-.134l7.964-4.6a1.294 1.294 0 0 0 .655-1.134V19.054l3.366 1.944a.12.12 0 0 1 .066.092v9.299a7.505 7.505 0 0 1-7.49 7.496zM6.392 31.006a7.471 7.471 0 0 1-.894-5.023c.06.036.162.099.237.141l7.964 4.6a1.297 1.297 0 0 0 1.308 0l9.724-5.614v3.888a.12.12 0 0 1-.048.103l-8.051 4.649a7.504 7.504 0 0 1-10.24-2.744zM4.297 13.62A7.469 7.469 0 0 1 8.2 10.333c0 .068-.004.19-.004.274v9.201a1.294 1.294 0 0 0 .654 1.132l9.723 5.614-3.366 1.944a.12.12 0 0 1-.114.012L7.044 23.86a7.504 7.504 0 0 1-2.747-10.24zm27.658 6.437l-9.724-5.615 3.367-1.943a.121.121 0 0 1 .114-.012l8.048 4.648a7.498 7.498 0 0 1-1.158 13.528v-9.476a1.293 1.293 0 0 0-.647-1.13zm3.35-5.043c-.059-.037-.162-.099-.236-.141l-7.965-4.6a1.298 1.298 0 0 0-1.308 0l-9.723 5.614v-3.888a.12.12 0 0 1 .048-.103l8.05-4.645a7.497 7.497 0 0 1 11.135 7.763zm-21.063 6.929l-3.367-1.944a.12.12 0 0 1-.065-.092v-9.299a7.497 7.497 0 0 1 12.293-5.756 6.94 6.94 0 0 0-.236.134l-7.965 4.6a1.294 1.294 0 0 0-.654 1.132l-.006 11.225zm1.829-3.943l4.33-2.501 4.332 2.498v4.997l-4.331 2.5-4.331-2.5V18z" fill="currentColor"/></svg>
              Powered by OpenAI
            </span>
          </h2>
          <p className="text-slate-500 text-sm mt-0.5">3 high-conviction trades synthesized by OpenAI across <strong className="text-slate-400">every signal source</strong> — dark pool, smart money, vol crush, call intent, max pain, gamma wall &amp; more.</p>
        </div>
        <div className="flex items-center gap-3">
          {generatedAt && <span className="text-slate-600 text-xs hidden sm:block">{scanned} tickers · {sources.length} signal sources · {new Date(generatedAt).toLocaleTimeString()}</span>}
          <button onClick={() => run()} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.25)", color: "#fbbf24" }}>{loading ? "Analyzing…" : "↻ Regenerate"}</button>
        </div>
      </div>

      {/* Signal sources used */}
      {sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {sources.map(s => (
            <span key={s} className="text-xs px-2 py-0.5 rounded-full" style={{ background: "rgba(74,222,128,0.06)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.15)" }}>● {s}</span>
          ))}
        </div>
      )}

      <p className="text-xs text-slate-600 mb-5 italic">Not financial advice. Always do your own research. AI analysis is based on public options data and synthesized by OpenAI.</p>

      {/* Warming / first-load state */}
      {warming && (
        <div className="rounded-xl p-5 mb-4" style={{ background: "rgba(74,222,128,0.04)", border: "1px solid rgba(74,222,128,0.18)" }}>
          <div className="flex items-center gap-3 mb-3">
            <div className="text-2xl animate-spin" style={{ animationDuration: "3s" }}>⚙️</div>
            <div>
              <div className="text-white font-bold text-sm">Collecting live signals across all tabs…</div>
              <div className="text-slate-400 text-xs mt-0.5">Vol Crush · Call Intent · Smart vs Retail · Max Pain · Gamma Wall · Dark Pool</div>
            </div>
          </div>
          {/* Progress bar */}
          <div className="rounded-full overflow-hidden mb-2" style={{ height: 4, background: "rgba(255,255,255,0.06)" }}>
            <div className="h-full rounded-full transition-all" style={{ background: "linear-gradient(90deg,#16a34a,#22c55e)", width: `${Math.max(5, Math.round((40 - warmCountdown) / 40 * 100))}%` }} />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-500 text-xs">Auto-generating in {warmCountdown}s…</span>
            <button onClick={() => { setWarming(false); run(); }} className="text-xs font-bold transition-colors px-3 py-1 rounded-lg" style={{ background: "rgba(74,222,128,0.1)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.2)" }}>Try now →</button>
          </div>
        </div>
      )}

      {error && !warming && (
        <div className="rounded-xl p-4 mb-4 text-sm" style={{ background: "rgba(251,191,36,0.06)", border: "1px solid rgba(251,191,36,0.2)", color: "#fbbf24" }}>
          ⚠ {error}
        </div>
      )}

      {loading && !warming && trades.length === 0 && (
        <div className="text-center py-16">
          <div className="text-3xl mb-4 animate-pulse">🤖</div>
          <div className="text-slate-400 text-sm font-bold">OpenAI is reading all your signal sources…</div>
          <div className="text-slate-600 text-xs mt-2">Vol Crush · Call Intent · Smart vs Retail · Max Pain · Gamma Wall · Dark Pool · Composite Score</div>
          <div className="text-slate-700 text-xs mt-1">Finding the 5 trades where the most signals converge</div>
        </div>
      )}

      {trades.length > 0 && (
        <div className="space-y-3">
          {[...trades].sort((a, b) => {
            const order: Record<string, number> = { BULLISH: 0, NEUTRAL: 1, BEARISH: 2 };
            const da = order[a.direction] ?? 1;
            const db = order[b.direction] ?? 1;
            if (da !== db) return da - db;
            const ca = a.conviction === "HIGH" ? 0 : 1;
            const cb = b.conviction === "HIGH" ? 0 : 1;
            return ca - cb;
          }).map((t, i) => {
            const isOpen = expanded === i;
            const blurred = !isSubscribed && i >= 2;
            return (
              <div key={i} className="rounded-xl overflow-hidden" style={{ border: `1px solid ${t.conviction === "HIGH" ? "rgba(251,191,36,0.25)" : "rgba(255,255,255,0.07)"}`, background: "rgba(255,255,255,0.01)" }}>
                {/* Header row — always visible */}
                <div className="flex items-center gap-3 p-4 cursor-pointer select-none" onClick={() => setExpanded(isOpen ? null : i)}>
                  {t.conviction === "HIGH" && <span className="text-xs px-1.5 py-0.5 rounded font-black" style={{ background: "rgba(251,191,36,0.15)", color: "#fbbf24" }}>HIGH</span>}
                  <span className="font-black text-white text-lg">{t.ticker}</span>
                  <span className="text-slate-500 text-xs">${t.price?.toFixed(2)}</span>
                  <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ background: dBg(t.direction), color: dColor(t.direction) }}>{t.direction}</span>
                  <span className="text-slate-400 text-xs hidden sm:block">{t.setup_type}</span>
                  <div className="ml-auto flex items-center gap-3">
                    <span className="text-xs" style={{ color: rColor(t.risk_level) }}>Risk: {t.risk_level}</span>
                    <button
                      onClick={e => handleSave(e, t)}
                      style={{ padding: "4px 10px", borderRadius: 6, fontSize: 10, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                        background: saved[t.ticker] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                        borderColor: saved[t.ticker] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                        color: saved[t.ticker] ? "#4ade80" : "#64748b" }}
                    >
                      {saved[t.ticker] ? "✓ Saved" : "📌 Save"}
                    </button>
                    <span className="text-slate-600 text-xs">{isOpen ? "▲" : "▼"}</span>
                  </div>
                </div>

                {/* Expanded detail */}
                {isOpen && (
                  <div className={`px-4 pb-4 ${blurred ? "blur-sm select-none pointer-events-none" : ""}`}>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                      {[
                        { label: "Setup", val: t.setup_type },
                        { label: "Strike", val: `$${t.entry_strike}` },
                        { label: "Expiry", val: t.expiry },
                        { label: "Target", val: `$${t.target_price}` },
                        { label: "Stop", val: `$${t.stop_loss}` },
                        { label: "Risk", val: t.risk_level },
                        { label: "Conviction", val: t.conviction },
                        { label: "Direction", val: t.direction },
                      ].map(({ label, val }) => (
                        <div key={label} className="rounded-lg p-2.5 text-center" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                          <div className="text-slate-600 text-xs">{label}</div>
                          <div className="font-bold text-white text-sm mt-0.5">{val}</div>
                        </div>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {t.signals_aligned?.map(s => (
                        <span key={s} className="text-xs px-2 py-0.5 rounded-full font-bold" style={{ background: "rgba(74,222,128,0.1)", color: "#4ade80", border: "1px solid rgba(74,222,128,0.2)" }}>✓ {s}</span>
                      ))}
                    </div>
                    <p className="text-slate-300 text-sm leading-relaxed rounded-xl p-3" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.05)" }}>{t.thesis}</p>
                    <button onClick={() => onSelectTicker(t.ticker)} className="mt-3 text-xs text-slate-500 hover:text-white transition-colors">View {t.ticker} full analysis →</button>
                  </div>
                )}

                {/* Paywall overlay for trades 3–5 */}
                {blurred && isOpen && (
                  <div className="mx-4 mb-4 rounded-xl p-4 text-center" style={{ background: "rgba(251,191,36,0.06)", border: "1px solid rgba(251,191,36,0.2)" }}>
                    <div className="text-yellow-400 font-black text-sm mb-1">🔒 Pro Feature</div>
                    <div className="text-slate-400 text-xs">All 5 AI trade setups are available with a Pro subscription.</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---- Signal Feed Tab -----------------------------------------------------
function SignalFeedTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [events, setEvents]   = useState<SignalEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [filter, setFilter]   = useState<string>("ALL");

  const run = async () => {
    setLoading(true);
    try {
      const d = await fetchSignalFeed();
      setEvents(d.events || []);
      setGeneratedAt(d.generated_at);
    } catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); const t = setInterval(run, 300000); return () => clearInterval(t); }, []);

  const types = ["ALL", ...Array.from(new Set(events.map(e => e.type)))];
  const visible = filter === "ALL" ? events : events.filter(e => e.type === filter);

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight flex items-center gap-2">
            📡 Live Signal Feed
            {loading && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse inline-block" />}
          </h2>
          <p className="text-slate-500 text-sm mt-0.5">Real-time notable signals — IV spikes, smart money divergence, max pain gaps, accumulation bursts.</p>
        </div>
        <div className="flex items-center gap-3">
          {generatedAt && <span className="text-slate-600 text-xs">{new Date(generatedAt).toLocaleTimeString()} · auto-refreshes every 5 min</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(74,222,128,0.1)", border: "1px solid rgba(74,222,128,0.25)", color: "#4ade80" }}>{loading ? "Scanning…" : "↻ Refresh"}</button>
        </div>
      </div>

      {/* Filter pills */}
      <div className="flex gap-2 flex-wrap mb-5">
        {types.map(t => (
          <button key={t} onClick={() => setFilter(t)} className="px-3 py-1 rounded-full text-xs font-bold transition-all"
            style={{ background: filter === t ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.04)", color: filter === t ? "#fff" : "#64748b", border: filter === t ? "1px solid rgba(255,255,255,0.2)" : "1px solid rgba(255,255,255,0.07)" }}>{t}</button>
        ))}
      </div>

      {loading && events.length === 0 && (
        <div className="text-center py-16 text-slate-500 text-sm">Scanning 20 tickers for notable signal activity…<div className="text-xs text-slate-600 mt-2">~20 seconds</div></div>
      )}
      {!loading && visible.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">No notable signals detected right now. Markets may be quiet.</div>}

      {visible.length > 0 && (
        <div className="space-y-2">
          {visible.map((ev, i) => (
            <div key={i} onClick={() => onSelectTicker(ev.ticker)} className="flex items-start gap-4 rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" style={{ background: "rgba(255,255,255,0.01)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <span className="text-2xl shrink-0 mt-0.5">{ev.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-1">
                  <span className="font-black text-white">{ev.ticker}</span>
                  <span className="text-slate-500 text-xs">${ev.price?.toFixed(2)}</span>
                  <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ background: `${ev.color}15`, color: ev.color, border: `1px solid ${ev.color}30` }}>{ev.type}</span>
                </div>
                <p className="text-slate-400 text-sm">{ev.msg}</p>
              </div>
              <span className="text-slate-700 text-xs shrink-0 mt-1">→</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Composite Score Board Tab -------------------------------------------
function CompositeBoardTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults]     = useState<CompositeScoreRow[]>([]);
  const [loading, setLoading]     = useState(false);
  const [scanned, setScanned]     = useState(0);
  const [expanded, setExpanded]   = useState<string | null>(null);
  const [filter, setFilter]       = useState<string>("ALL");

  const run = async () => {
    setLoading(true);
    try { const d = await fetchCompositeScore(); setResults(d.results); setScanned(d.scanned); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);

  const biasColor = (b: string) => b === "STRONG BULL" ? "#4ade80" : b === "BULLISH" ? "#86efac" : b === "NEUTRAL" ? "#94a3b8" : b === "BEARISH" ? "#fca5a5" : "#f87171";
  const biasBg    = (b: string) => b.includes("BULL") ? "rgba(74,222,128,0.1)" : b === "NEUTRAL" ? "rgba(148,163,184,0.08)" : "rgba(248,113,113,0.1)";

  const biasFilters = ["ALL", "STRONG BULL", "BULLISH", "NEUTRAL", "BEARISH", "STRONG BEAR"];
  const visible = filter === "ALL" ? results : results.filter(r => r.bias === filter);

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">🎯 Signal Score Board</h2>
          <p className="text-slate-500 text-sm mt-0.5">Every ticker scored 0–100 by combining IV rank, smart money flow, call accumulation, and max pain alignment.</p>
        </div>
        <div className="flex items-center gap-3">
          {scanned > 0 && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(96,165,250,0.1)", border: "1px solid rgba(96,165,250,0.25)", color: "#60a5fa" }}>{loading ? "Scoring…" : "↻ Refresh"}</button>
        </div>
      </div>

      {/* Score legend */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-5">
        {[["STRONG BULL","75–100","#4ade80"],["BULLISH","60–74","#86efac"],["NEUTRAL","40–59","#94a3b8"],["BEARISH","25–39","#fca5a5"],["STRONG BEAR","0–24","#f87171"]].map(([b,r,c]) => (
          <div key={b} className="rounded-xl p-2 text-center" style={{ background: `${c}10`, border: `1px solid ${c}25` }}>
            <div className="font-black text-xs" style={{ color: c }}>{b}</div>
            <div className="text-slate-600 text-xs mt-0.5">{r}</div>
          </div>
        ))}
      </div>

      {/* Filter pills */}
      <div className="flex gap-2 flex-wrap mb-5">
        {biasFilters.map(f => (
          <button key={f} onClick={() => setFilter(f)} className="px-3 py-1 rounded-full text-xs font-bold transition-all"
            style={{ background: filter === f ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.04)", color: filter === f ? "#fff" : "#64748b", border: filter === f ? "1px solid rgba(255,255,255,0.2)" : "1px solid rgba(255,255,255,0.07)" }}>{f}</button>
        ))}
      </div>

      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Scoring all signals for {scanned || "50+"} tickers…<div className="text-xs text-slate-600 mt-2">First load ~40s · cached 30 min</div></div>}

      {!loading && results.length > 0 && visible.length === 0 && (
        <div className="text-center py-16">
          <div className="text-3xl mb-3">🔍</div>
          <div className="text-slate-400 text-sm font-bold">No {filter} tickers right now</div>
          <div className="text-slate-600 text-xs mt-2">Try <button onClick={() => setFilter("ALL")} className="underline text-slate-500 hover:text-white transition-colors">viewing all {results.length} tickers</button> or a different bias filter</div>
        </div>
      )}

      {visible.length > 0 && (
        <div className="space-y-2">
          {visible.map((r, i) => {
            const isOpen = expanded === r.ticker;
            const scoreColor = r.score >= 75 ? "#4ade80" : r.score >= 60 ? "#86efac" : r.score >= 40 ? "#94a3b8" : r.score >= 25 ? "#fca5a5" : "#f87171";
            return (
              <div key={r.ticker} className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.01)" }}>
                <div className="flex items-center gap-3 p-4 cursor-pointer" onClick={() => setExpanded(isOpen ? null : r.ticker)}>
                  <span className="text-slate-600 text-xs w-5">{i+1}</span>
                  {/* Score dial */}
                  <div className="w-12 h-12 shrink-0 rounded-full flex items-center justify-center font-black text-sm" style={{ background: `${scoreColor}15`, border: `2px solid ${scoreColor}40`, color: scoreColor }}>{r.score.toFixed(0)}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-black text-white">{r.ticker}</span>
                      <span className="text-slate-500 text-xs">${r.price.toFixed(2)}</span>
                      <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ background: biasBg(r.bias), color: biasColor(r.bias), border: `1px solid ${biasColor(r.bias)}30` }}>{r.bias}</span>
                    </div>
                    {/* Score bar */}
                    <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.05)" }}>
                      <div style={{ width: `${r.score}%`, background: scoreColor, opacity: 0.7, transition: "width 0.5s" }} className="h-full rounded-full" />
                    </div>
                  </div>
                  <span className="text-slate-600 text-xs">{isOpen ? "▲" : "▼"}</span>
                </div>

                {isOpen && (
                  <div className="px-4 pb-4">
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                      {[
                        { label: "IV Rank", val: `${r.components.iv_rank?.toFixed(0)}%`, sub: r.components.iv_score >= 0 ? "Cheap premium ✓" : "Expensive premium" },
                        { label: "Smart C/P", val: `${r.components.smart_cp?.toFixed(2)}×`, sub: r.components.smart_cp >= 1.5 ? "Inst. bullish ✓" : r.components.smart_cp <= 0.7 ? "Inst. bearish ✗" : "Neutral" },
                        { label: "Retail C/P", val: `${r.components.retail_cp?.toFixed(2)}×`, sub: r.components.retail_cp >= 1.5 ? "Retail bullish" : r.components.retail_cp <= 0.7 ? "Retail bearish" : "Neutral" },
                        { label: "Accum Calls", val: `${r.components.accum_pct?.toFixed(0)}%`, sub: r.components.accum_pct >= 60 ? "Inst. building ✓" : "Retail-driven" },
                        { label: "Max Pain", val: r.components.max_pain ? `$${r.components.max_pain}` : "N/A", sub: r.components.mp_score > 0 ? "Below pain ✓" : "Above pain" },
                        { label: "Exp", val: r.nearest_exp, sub: "Nearest expiry" },
                        ...(r.components.top_accum?.strike ? [{ label: "Top Accum", val: `$${r.components.top_accum.strike}`, sub: `+${r.components.top_accum.otm_pct?.toFixed(1)}% OTM · ${r.components.top_accum.expiry}` }] : []),
                      ].map(({ label, val, sub }) => (
                        <div key={label} className="rounded-lg p-2.5" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                          <div className="text-slate-600 text-xs">{label}</div>
                          <div className="font-bold text-white text-sm mt-0.5">{val}</div>
                          {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
                        </div>
                      ))}
                    </div>
                    <button onClick={() => onSelectTicker(r.ticker)} className="text-xs text-slate-500 hover:text-white transition-colors">Open {r.ticker} full analysis →</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---- Vol Crush Detector Tab ----------------------------------------------
function VolCrushTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<VolCrushRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const run = async () => {
    setLoading(true);
    try { const d = await fetchVolCrush(); setResults(d.results); setScanned(d.scanned); setLastRun(new Date()); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);
  const vColor = (v: string) => v === "HIGH FEAR" ? "#f87171" : v === "ELEVATED" ? "#fb923c" : v === "NORMAL" ? "#60a5fa" : "#4ade80";
  const vBg    = (v: string) => v === "HIGH FEAR" ? "rgba(248,113,113,0.12)" : v === "ELEVATED" ? "rgba(251,146,60,0.1)" : v === "NORMAL" ? "rgba(96,165,250,0.08)" : "rgba(74,222,128,0.1)";
  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Volatility Crush Detector</h2>
          <p className="text-slate-500 text-sm mt-0.5">When IV is at its 1-year high — sell premium, or wait for the crush after earnings.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(248,113,113,0.1)", border: "1px solid rgba(248,113,113,0.25)", color: "#f87171" }}>{loading ? "Analyzing…" : "↻ Refresh"}</button>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        {(["HIGH FEAR","ELEVATED","NORMAL","LOW IV"] as const).map(v => (
          <div key={v} className="rounded-xl p-3 text-center" style={{ background: vBg(v), border: `1px solid ${vColor(v)}25` }}>
            <div className="font-black text-sm" style={{ color: vColor(v) }}>{v}</div>
            <div className="text-slate-600 text-xs mt-1">{v==="HIGH FEAR"?"IV rank 80%+":v==="ELEVATED"?"IV rank 60–80%":v==="NORMAL"?"IV rank 30–60%":"IV rank 0–30%"}</div>
          </div>
        ))}
      </div>
      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Fetching IV & price history for {scanned || "50+"} tickers…<div className="text-xs text-slate-600 mt-2">First load ~30s · cached 30 min</div></div>}
      {!loading && results.length === 0 && lastRun && <div className="text-center py-16 text-slate-500 text-sm">No data available.</div>}
      {results.length > 0 && (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)} className="rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.01)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-slate-600 text-xs w-5">{i+1}</span>
                  <span className="font-black text-white text-base">{r.ticker}</span>
                  <span className="text-slate-500 text-xs">${r.price.toFixed(2)}</span>
                  {r.earnings_date && <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: "rgba(251,191,36,0.1)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.2)" }}>Earnings {r.earnings_date}</span>}
                </div>
                <span className="px-2.5 py-1 rounded-lg text-xs font-black" style={{ background: vBg(r.verdict), color: vColor(r.verdict), border: `1px solid ${vColor(r.verdict)}30` }}>{r.verdict}</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden mb-2.5" style={{ background: "rgba(255,255,255,0.05)" }}>
                <div style={{ width: `${r.iv_rank}%`, background: r.iv_rank >= 80 ? "rgba(248,113,113,0.6)" : r.iv_rank >= 60 ? "rgba(251,146,60,0.6)" : "rgba(96,165,250,0.5)", transition: "width 0.4s" }} className="h-full rounded-full" />
              </div>
              <div className="flex items-center justify-between text-xs">
                <div className="flex gap-4">
                  <span style={{ color: vColor(r.verdict) }}>IV Rank <span className="font-bold">{r.iv_rank.toFixed(0)}%</span></span>
                  <span className="text-slate-400">IV <span className="font-bold text-white">{r.current_iv.toFixed(1)}%</span></span>
                  <span className="text-slate-400">HV30 <span className="font-bold text-white">{r.hv_30.toFixed(1)}%</span></span>
                  {r.iv_hv_ratio && <span className="text-slate-400">IV/HV <span className="font-bold text-white">{r.iv_hv_ratio.toFixed(2)}×</span></span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Call Intent Decoder Tab ---------------------------------------------
function CallIntentTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<CallIntentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const run = async () => {
    setLoading(true);
    try { const d = await fetchCallIntent(); setResults(d.results); setScanned(d.scanned); setLastRun(new Date()); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);
  const handleSave = async (e: React.MouseEvent, r: CallIntentRow) => {
    e.stopPropagation();
    if (!r.top_accum_strike || !r.top_accum_expiry) return;
    try {
      await addTradeWatchlist({ ticker: r.ticker, strike: r.top_accum_strike, expiry: r.top_accum_expiry, option_type: "CALL", notes: `Call Intent: Accum $${r.accum_prem_m.toFixed(1)}M (${r.accum_pct}%)` });
      setSaved(s => ({ ...s, [r.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [r.ticker]: false })), 2500);
    } catch {}
  };
  const vColor = (v: string) => v === "ACCUMULATION" ? "#4ade80" : v === "FOMO" ? "#f87171" : "#fbbf24";
  const vBg    = (v: string) => v === "ACCUMULATION" ? "rgba(74,222,128,0.12)" : v === "FOMO" ? "rgba(248,113,113,0.12)" : "rgba(251,191,36,0.10)";
  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Call Intent Decoder</h2>
          <p className="text-slate-500 text-sm mt-0.5">Is the call buying a retail FOMO chase or quiet institutional accumulation?</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(74,222,128,0.1)", border: "1px solid rgba(74,222,128,0.25)", color: "#4ade80" }}>{loading ? "Analyzing…" : "↻ Refresh"}</button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-6">
        <div className="rounded-xl p-3.5" style={{ background: "rgba(74,222,128,0.05)", border: "1px solid rgba(74,222,128,0.15)" }}>
          <div className="text-emerald-400 font-bold text-sm mb-1">📦 ACCUMULATION</div>
          <div className="text-slate-400 text-xs">Strike 5%+ above price AND expiry 60+ days out. Institutions quietly building a long position.</div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.15)" }}>
          <div className="text-red-400 font-bold text-sm mb-1">🏃 FOMO</div>
          <div className="text-slate-400 text-xs">Strike within 3% AND expiry under 45 days. Retail chasing a move that's already happened.</div>
        </div>
      </div>
      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Analyzing call chains across {scanned || "50+"} tickers…<div className="text-xs text-slate-600 mt-2">First load ~30s · cached 30 min</div></div>}
      {!loading && results.length === 0 && lastRun && <div className="text-center py-16 text-slate-500 text-sm">No significant call activity found.</div>}
      {results.length > 0 && (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)} className="rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.01)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-slate-600 text-xs w-5">{i+1}</span>
                  <span className="font-black text-white text-base">{r.ticker}</span>
                  <span className="text-slate-500 text-xs">${r.price.toFixed(2)}</span>
                </div>
                <div className="flex items-center gap-2">
                  {r.top_accum_strike && r.top_accum_expiry && (
                    <button onClick={e => handleSave(e, r)}
                      style={{ padding: "4px 10px", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                        background: saved[r.ticker] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                        borderColor: saved[r.ticker] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                        color: saved[r.ticker] ? "#4ade80" : "#64748b" }}>
                      {saved[r.ticker] ? "✓ Saved" : "📌 Save"}
                    </button>
                  )}
                  <span className="px-2.5 py-1 rounded-lg text-xs font-black" style={{ background: vBg(r.verdict), color: vColor(r.verdict), border: `1px solid ${vColor(r.verdict)}30` }}>{r.verdict}</span>
                </div>
              </div>
              <div className="h-2 rounded-full overflow-hidden flex mb-2.5" style={{ background: "rgba(255,255,255,0.05)" }}>
                <div style={{ width: `${r.accum_pct}%`, background: "rgba(74,222,128,0.55)", transition: "width 0.4s" }} />
                <div style={{ width: `${r.fomo_pct}%`, background: "rgba(248,113,113,0.55)", transition: "width 0.4s" }} />
              </div>
              <div className="flex items-start justify-between gap-2 flex-wrap">
                <div className="flex gap-4 text-xs">
                  <span className="text-emerald-400">📦 Accum <span className="font-bold">${r.accum_prem_m.toFixed(1)}M</span> <span className="text-slate-600">({r.accum_pct}%)</span></span>
                  <span className="text-red-400">🏃 FOMO <span className="font-bold">${r.fomo_prem_m.toFixed(1)}M</span> <span className="text-slate-600">({r.fomo_pct}%)</span></span>
                </div>
                {r.top_accum_strike && r.top_accum_expiry && (
                  <span className="text-slate-600 text-xs">
                    Top accum: <span className="text-white font-bold">${r.top_accum_strike}</span>
                    <span className="text-emerald-500 font-bold ml-1">(+{r.top_accum_otm_pct?.toFixed(1) ?? ((r.top_accum_strike - r.price) / r.price * 100).toFixed(1)}% OTM)</span>
                    <span className="ml-1">exp <span className="text-slate-400">{r.top_accum_expiry}</span></span>
                  </span>
                )}
              </div>
              {(r.accum_vol_m > 0 || r.accum_oi_m > 0) && (
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <div className="rounded-lg px-3 py-2" style={{ background: "rgba(74,222,128,0.05)", border: "1px solid rgba(74,222,128,0.12)" }}>
                    <div className="text-slate-500 text-xs mb-1">📦 Accum breakdown</div>
                    <div className="flex gap-3 text-xs">
                      <span><span className="text-slate-400">Today </span><span className="font-bold text-white">${r.accum_vol_m.toFixed(1)}M</span></span>
                      <span><span className="text-slate-400">Built up </span><span className="font-bold text-slate-300">${r.accum_oi_m.toFixed(1)}M</span></span>
                    </div>
                  </div>
                  <div className="rounded-lg px-3 py-2" style={{ background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.12)" }}>
                    <div className="text-slate-500 text-xs mb-1">🏃 FOMO breakdown</div>
                    <div className="flex gap-3 text-xs">
                      <span><span className="text-slate-400">Today </span><span className="font-bold text-white">${r.fomo_vol_m.toFixed(1)}M</span></span>
                      <span><span className="text-slate-400">Built up </span><span className="font-bold text-slate-300">${r.fomo_oi_m.toFixed(1)}M</span></span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Smart Money vs Retail Tab -------------------------------------------
function SmartVsRetailTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<SmartVsRetailRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const run = async () => {
    setLoading(true);
    try { const d = await fetchSmartVsRetail(); setResults(d.results); setScanned(d.scanned); setLastRun(new Date()); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);
  const dColor = (d: string) => d.startsWith("SMART BULL") ? "#4ade80" : d.startsWith("SMART BEAR") ? "#f87171" : d.startsWith("RETAIL BULL") ? "#a78bfa" : d.startsWith("RETAIL BEAR") ? "#fb923c" : "#64748b";
  const dBg    = (d: string) => d.startsWith("SMART BULL") ? "rgba(74,222,128,0.1)" : d.startsWith("SMART BEAR") ? "rgba(248,113,113,0.1)" : d.startsWith("RETAIL") ? "rgba(167,139,250,0.1)" : "rgba(255,255,255,0.04)";
  const strBadge = (s: string) => s === "STRONG" ? "🔥" : s === "MODERATE" ? "⚡" : "·";
  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Smart Money vs Retail</h2>
          <p className="text-slate-500 text-sm mt-0.5">When big blocks and small traders point in opposite directions — follow the institutions.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(167,139,250,0.1)", border: "1px solid rgba(167,139,250,0.25)", color: "#a78bfa" }}>{loading ? "Analyzing…" : "↻ Refresh"}</button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-6">
        <div className="rounded-xl p-3.5" style={{ background: "rgba(74,222,128,0.04)", border: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="text-white font-bold text-sm mb-1">🏦 Smart Money</div>
          <div className="text-slate-400 text-xs">Large blocks: premium ≥ $3/contract AND volume ≥ 30 contracts ($9K+ per trade). Institutional footprint.</div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(167,139,250,0.04)", border: "1px solid rgba(255,255,255,0.07)" }}>
          <div className="text-violet-400 font-bold text-sm mb-1">👤 Retail</div>
          <div className="text-slate-400 text-xs">Small contracts: premium &lt; $2/contract OR volume &lt; 15 contracts. Scattered retail flow.</div>
        </div>
      </div>
      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Classifying options flow for {scanned || "50+"} tickers…<div className="text-xs text-slate-600 mt-2">First load ~30s · cached 30 min</div></div>}
      {!loading && results.length === 0 && lastRun && <div className="text-center py-16 text-slate-500 text-sm">No divergence signals found.</div>}
      {results.length > 0 && (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)} className="rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.01)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-slate-600 text-xs w-5">{i+1}</span>
                  <span className="font-black text-white text-base">{r.ticker}</span>
                  <span className="text-slate-500 text-xs">${r.price.toFixed(2)}</span>
                  <span className="text-xs">{strBadge(r.signal_strength)}</span>
                </div>
                <span className="px-2.5 py-1 rounded-lg text-xs font-black" style={{ background: dBg(r.divergence), color: dColor(r.divergence), border: `1px solid ${dColor(r.divergence)}30` }}>{r.divergence}</span>
              </div>
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div className="rounded-lg p-3" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                  <div className="text-slate-500 mb-1">🏦 Smart Money</div>
                  <div className="font-bold text-white">${r.smart_prem_m.toFixed(1)}M flow</div>
                  <div className="mt-1" style={{ color: r.smart_cp >= 1.5 ? "#4ade80" : r.smart_cp <= 0.7 ? "#f87171" : "#94a3b8" }}>C/P: {r.smart_cp.toFixed(2)}×</div>
                </div>
                <div className="rounded-lg p-3" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                  <div className="text-slate-500 mb-1">👤 Retail</div>
                  <div className="font-bold text-white">${r.retail_prem_m.toFixed(1)}M flow</div>
                  <div className="mt-1" style={{ color: r.retail_cp >= 1.5 ? "#4ade80" : r.retail_cp <= 0.7 ? "#f87171" : "#94a3b8" }}>C/P: {r.retail_cp.toFixed(2)}×</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Max Pain Tab --------------------------------------------------------
function MaxPainTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<MaxPainRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const run = async () => {
    setLoading(true);
    try { const d = await fetchMaxPain(); setResults(d.results); setScanned(d.scanned); setLastRun(new Date()); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);
  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Options Pinning Radar</h2>
          <p className="text-slate-500 text-sm mt-0.5">Max pain is the price where options expire most worthless. Price drifts toward it before expiry — biggest gaps = biggest opportunity.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(96,165,250,0.1)", border: "1px solid rgba(96,165,250,0.25)", color: "#60a5fa" }}>{loading ? "Calculating…" : "↻ Refresh"}</button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-6">
        <div className="rounded-xl p-3.5" style={{ background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.15)" }}>
          <div className="text-red-400 font-bold text-sm mb-1">📍 ABOVE PAIN</div>
          <div className="text-slate-400 text-xs">Price is above max pain. Market makers push it lower toward expiry to minimize payout.</div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(74,222,128,0.05)", border: "1px solid rgba(74,222,128,0.15)" }}>
          <div className="text-emerald-400 font-bold text-sm mb-1">📍 BELOW PAIN</div>
          <div className="text-slate-400 text-xs">Price is below max pain. Gravitational pull upward toward the pain strike before expiry.</div>
        </div>
      </div>
      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Computing max pain across {scanned || "50+"} tickers…<div className="text-xs text-slate-600 mt-2">First load ~30s · cached 30 min</div></div>}
      {!loading && results.length === 0 && lastRun && <div className="text-center py-16 text-slate-500 text-sm">No max pain data available.</div>}
      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[560px]">
            <thead>
              <tr className="text-left text-xs text-slate-600 uppercase tracking-wide border-b" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
                <th className="pb-3 pr-4">#</th>
                <th className="pb-3 pr-4">Ticker</th>
                <th className="pb-3 pr-4">Price</th>
                <th className="pb-3 pr-4">Max Pain</th>
                <th className="pb-3 pr-4">Distance</th>
                <th className="pb-3 pr-4">Direction</th>
                <th className="pb-3 pr-4">Expiry</th>
                <th className="pb-3">Days</th>
              </tr>
            </thead>
            <tbody className="divide-y" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
              {results.map((r, i) => {
                const isAbove = r.direction === "ABOVE PAIN";
                const distColor = Math.abs(r.distance_pct) >= 5 ? (isAbove ? "#f87171" : "#4ade80") : "#94a3b8";
                return (
                  <tr key={r.ticker} onClick={() => onSelectTicker(r.ticker)} className="cursor-pointer hover:bg-white/5 transition-colors">
                    <td className="py-3 pr-4 text-slate-600">{i+1}</td>
                    <td className="py-3 pr-4 font-black text-white">{r.ticker}</td>
                    <td className="py-3 pr-4 text-slate-400">${r.price.toFixed(2)}</td>
                    <td className="py-3 pr-4 font-bold text-blue-400">${r.max_pain.toFixed(2)}</td>
                    <td className="py-3 pr-4 font-bold" style={{ color: distColor }}>{r.distance_pct > 0 ? "+" : ""}{r.distance_pct.toFixed(1)}%</td>
                    <td className="py-3 pr-4">
                      <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ background: isAbove ? "rgba(248,113,113,0.12)" : "rgba(74,222,128,0.12)", color: isAbove ? "#f87171" : "#4ade80" }}>{r.direction}</span>
                    </td>
                    <td className="py-3 pr-4 text-slate-500">{r.nearest_expiry}</td>
                    <td className="py-3 text-slate-500">{r.days_to_exp}d</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- Gamma Wall Tab ------------------------------------------------------
function GammaWallTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<GammaWallRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<GammaWallRow | null>(null);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const run = async () => {
    setLoading(true);
    try { const d = await fetchGammaWall(); setResults(d.results); if (d.results.length) setSelected(d.results[0]); setLastRun(new Date()); }
    catch {} finally { setLoading(false); }
  };
  useEffect(() => { run(); }, []);
  const maxOI = selected ? Math.max(...selected.strikes.map(s => s.total_oi), 1) : 1;
  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Gamma Wall</h2>
          <p className="text-slate-500 text-sm mt-0.5">Where dealers have the most hedging concentration — the levels that act as price magnets or barriers.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers analyzed</span>}
          <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg text-sm font-bold transition-all" style={{ background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.25)", color: "#fbbf24" }}>{loading ? "Loading…" : "↻ Refresh"}</button>
        </div>
      </div>
      {loading && results.length === 0 && <div className="text-center py-16 text-slate-500 text-sm">Fetching OI by strike for major tickers…<div className="text-xs text-slate-600 mt-2">First load ~20s · cached 30 min</div></div>}
      {!loading && results.length === 0 && lastRun && <div className="text-center py-16 text-slate-500 text-sm">No gamma data available.</div>}
      {results.length > 0 && (
        <div className="flex flex-col lg:flex-row gap-4">
          {/* Ticker selector */}
          <div className="lg:w-48 shrink-0 space-y-1">
            {results.map(r => (
              <button key={r.ticker} onClick={() => setSelected(r)} className="w-full text-left rounded-xl px-4 py-3 transition-all"
                style={{ background: selected?.ticker === r.ticker ? "rgba(251,191,36,0.1)" : "rgba(255,255,255,0.02)", border: selected?.ticker === r.ticker ? "1px solid rgba(251,191,36,0.3)" : "1px solid rgba(255,255,255,0.06)" }}>
                <div className="font-black text-white text-sm">{r.ticker}</div>
                <div className="text-xs text-slate-500">${r.price.toFixed(2)}</div>
                <div className="text-xs mt-0.5" style={{ color: r.wall_distance_pct >= 0 ? "#f87171" : "#4ade80" }}>Wall {r.wall_distance_pct >= 0 ? "+" : ""}{r.wall_distance_pct.toFixed(1)}%</div>
              </button>
            ))}
          </div>
          {/* OI chart */}
          {selected && (
            <div className="flex-1 rounded-xl p-4" style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                <div>
                  <span className="font-black text-white text-lg">{selected.ticker}</span>
                  <span className="text-slate-500 text-sm ml-2">${selected.price.toFixed(2)} · exp {selected.expiry}</span>
                </div>
                <div className="flex gap-3 text-xs">
                  <span className="text-yellow-400 font-bold">Wall: ${selected.wall_strike}</span>
                  {selected.flip_strike && <span className="text-purple-400 font-bold">Flip: ${selected.flip_strike}</span>}
                </div>
              </div>
              <div className="flex gap-2 text-xs text-slate-600 mb-3">
                <span className="flex items-center gap-1"><span className="w-3 h-2 inline-block rounded-sm" style={{ background: "rgba(74,222,128,0.5)" }} />Calls OI</span>
                <span className="flex items-center gap-1"><span className="w-3 h-2 inline-block rounded-sm" style={{ background: "rgba(248,113,113,0.5)" }} />Puts OI</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 inline-block rounded-full bg-yellow-400" />Wall</span>
                {selected.flip_strike && <span className="flex items-center gap-1"><span className="w-2 h-2 inline-block rounded-full" style={{ background: "#c084fc" }} />Gamma Flip</span>}
              </div>
              <div className="space-y-1 max-h-96 overflow-y-auto pr-1">
                {[...selected.strikes].sort((a, b) => b.strike - a.strike).map(s => {
                  const isWall  = s.strike === selected.wall_strike;
                  const isFlip  = s.strike === selected.flip_strike;
                  const isPrice = Math.abs(s.strike - selected.price) / selected.price < 0.005;
                  const cW = (s.call_oi / maxOI) * 45;
                  const pW = (s.put_oi  / maxOI) * 45;
                  return (
                    <div key={s.strike} className="flex items-center gap-1 text-xs rounded" style={{ background: isWall ? "rgba(251,191,36,0.08)" : isFlip ? "rgba(192,132,252,0.06)" : "transparent", padding: "2px 4px" }}>
                      <div className="flex items-center justify-end gap-1" style={{ width: "50%" }}>
                        <span className="text-slate-600" style={{ minWidth: 48, textAlign: "right" }}>{s.call_oi.toLocaleString()}</span>
                        <div className="h-4 rounded-sm" style={{ width: `${cW}%`, background: "rgba(74,222,128,0.5)", minWidth: cW > 0 ? 1 : 0 }} />
                      </div>
                      <div className="text-center font-mono shrink-0 px-1" style={{ minWidth: 64, color: isWall ? "#fbbf24" : isFlip ? "#c084fc" : isPrice ? "#fff" : "#475569", fontWeight: (isWall || isPrice) ? 700 : 400, fontSize: 11 }}>
                        {isPrice && "→ "}${s.strike}{isWall ? " 🧲" : isFlip ? " ⚡" : ""}
                      </div>
                      <div className="flex items-center gap-1" style={{ width: "50%" }}>
                        <div className="h-4 rounded-sm" style={{ width: `${pW}%`, background: "rgba(248,113,113,0.5)", minWidth: pW > 0 ? 1 : 0 }} />
                        <span className="text-slate-600">{s.put_oi.toLocaleString()}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
              <button onClick={() => onSelectTicker(selected.ticker)} className="mt-4 w-full py-2 rounded-lg text-sm font-bold text-slate-400 hover:text-white transition-colors" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)" }}>View {selected.ticker} in Stock Lookup →</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Put Intent Decoder Tab ----------------------------------------------
function PutIntentTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults] = useState<PutIntentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanned, setScanned] = useState(0);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      const data = await fetchPutIntent();
      setResults(data.results); setScanned(data.scanned); setLastRun(new Date());
    } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { run(); }, []);

  const verdictColor = (v: string) =>
    v === "BEARISH BET" ? "#f87171" : v === "HEDGE" ? "#4ade80" : "#fbbf24";
  const verdictBg = (v: string) =>
    v === "BEARISH BET" ? "rgba(248,113,113,0.12)" : v === "HEDGE" ? "rgba(74,222,128,0.12)" : "rgba(251,191,36,0.10)";

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Put Intent Decoder</h2>
          <p className="text-slate-500 text-sm mt-0.5">Are those puts a hedge or a genuine bearish bet? We decode the difference.</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && <span className="text-slate-600 text-xs">{results.length} tickers · {scanned} scanned</span>}
          <button onClick={run} disabled={loading}
            className="px-4 py-2 rounded-lg text-sm font-bold transition-all"
            style={{ background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.25)", color: "#fbbf24" }}>
            {loading ? "Analyzing…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-6">
        <div className="rounded-xl p-3.5" style={{ background: "rgba(74,222,128,0.05)", border: "1px solid rgba(74,222,128,0.15)" }}>
          <div className="text-emerald-400 font-bold text-sm mb-1">🛡 HEDGE</div>
          <div className="text-slate-400 text-xs">Strike 5%+ below price AND expiry 60+ days out. Still bullish — just protecting their long position.</div>
        </div>
        <div className="rounded-xl p-3.5" style={{ background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.15)" }}>
          <div className="text-red-400 font-bold text-sm mb-1">🎯 BEARISH BET</div>
          <div className="text-xs text-slate-400">Strike within 3% of price AND expiry under 45 days. Directional — they expect a near-term drop.</div>
        </div>
      </div>

      {loading && results.length === 0 && (
        <div className="text-center py-16 text-slate-500 text-sm">
          Analyzing options chains across {scanned || "50+"} tickers…
          <div className="text-xs text-slate-600 mt-2">First load takes ~30s · results cached for 30 min</div>
        </div>
      )}
      {!loading && results.length === 0 && lastRun && (
        <div className="text-center py-16 text-slate-500 text-sm">No significant put activity found right now.</div>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          {results.map((r, i) => (
            <div key={r.ticker} onClick={() => onSelectTicker(r.ticker)}
              className="rounded-xl p-4 cursor-pointer hover:bg-white/5 transition-colors"
              style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(255,255,255,0.01)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-slate-600 text-xs w-5">{i + 1}</span>
                  <span className="font-black text-white text-base">{r.ticker}</span>
                  <span className="text-slate-500 text-xs">${r.price.toFixed(2)}</span>
                </div>
                <span className="px-2.5 py-1 rounded-lg text-xs font-black"
                  style={{ background: verdictBg(r.verdict), color: verdictColor(r.verdict), border: `1px solid ${verdictColor(r.verdict)}30` }}>
                  {r.verdict}
                </span>
              </div>

              <div className="h-2 rounded-full overflow-hidden flex mb-2.5" style={{ background: "rgba(255,255,255,0.05)" }}>
                <div style={{ width: `${r.hedge_pct}%`, background: "rgba(74,222,128,0.55)", transition: "width 0.4s" }} />
                <div style={{ width: `${r.bear_pct}%`, background: "rgba(248,113,113,0.55)", transition: "width 0.4s" }} />
              </div>

              <div className="flex items-start justify-between gap-2 flex-wrap">
                <div className="flex gap-4 text-xs">
                  <span className="text-emerald-400">🛡 Hedge <span className="font-bold">${r.hedge_prem_m.toFixed(1)}M</span> <span className="text-slate-600">({r.hedge_pct}%)</span></span>
                  <span className="text-red-400">🎯 Bear <span className="font-bold">${r.bear_prem_m.toFixed(1)}M</span> <span className="text-slate-600">({r.bear_pct}%)</span></span>
                </div>
                {r.top_bear_strike && r.top_bear_expiry && (
                  <span className="text-slate-600 text-xs">Top put: <span className="text-slate-400">${r.top_bear_strike}</span> exp <span className="text-slate-400">{r.top_bear_expiry}</span></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Pre-Market Flow Tab -------------------------------------------------
function PremarketTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData] = useState<{ gainers: PremarketRow[]; losers: PremarketRow[]; scanned: number } | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try { setData(await fetchPremarket()); } catch {}
    finally { setLoading(false); }
  };

  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, []);

  const Row = ({ r }: { r: PremarketRow }) => {
    const bull = r.change_pct > 0;
    return (
      <div onClick={() => onSelectTicker(r.ticker)} className="flex items-center justify-between px-4 py-2.5 cursor-pointer hover:bg-white/5 transition-colors" style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-black text-white text-sm">{r.ticker}</span>
            {r.vol_ratio >= 2 && <span className="text-xs px-1.5 py-0.5 rounded font-bold" style={{ background: "rgba(251,191,36,0.15)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.3)" }}>VOL {r.vol_ratio.toFixed(1)}×</span>}
          </div>
          <div className="text-slate-500 text-xs mt-0.5">${r.price.toFixed(2)} <span className="text-slate-600">prev ${r.prev_close.toFixed(2)}</span></div>
        </div>
        <span className={`font-black text-base ${bull ? "text-emerald-400" : "text-red-400"}`}>
          {bull ? "+" : ""}{r.change_pct.toFixed(2)}%
        </span>
      </div>
    );
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-black text-white tracking-tight">Pre-Market Flow</h2>
          <p className="text-slate-500 text-sm mt-0.5">Biggest movers before the open · refreshes every 60s</p>
        </div>
        <button onClick={load} disabled={loading}
          className="px-4 py-2 rounded-lg text-sm font-bold"
          style={{ background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.25)", color: "#4ade80" }}>
          {loading ? "Loading…" : "↻ Refresh"}
        </button>
      </div>

      {loading && !data && <div className="text-center py-16 text-slate-500 text-sm">Fetching pre-market prices…</div>}

      {data && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(34,197,94,0.2)" }}>
            <div className="px-4 py-2.5 font-bold text-sm text-emerald-400" style={{ background: "rgba(34,197,94,0.08)", borderBottom: "1px solid rgba(34,197,94,0.15)" }}>
              🟢 Pre-Market Gainers ({data.gainers.length})
            </div>
            {data.gainers.length === 0 && <div className="text-center py-8 text-slate-600 text-sm">No gainers right now</div>}
            {data.gainers.map(r => <Row key={r.ticker} r={r} />)}
          </div>
          <div className="rounded-xl overflow-hidden" style={{ border: "1px solid rgba(239,68,68,0.2)" }}>
            <div className="px-4 py-2.5 font-bold text-sm text-red-400" style={{ background: "rgba(239,68,68,0.08)", borderBottom: "1px solid rgba(239,68,68,0.15)" }}>
              🔴 Pre-Market Losers ({data.losers.length})
            </div>
            {data.losers.length === 0 && <div className="text-center py-8 text-slate-600 text-sm">No losers right now</div>}
            {data.losers.map(r => <Row key={r.ticker} r={r} />)}
          </div>
        </div>
      )}

      {data && <div className="text-center mt-4 text-slate-700 text-xs">{data.scanned} tickers scanned · auto-refreshes every 60s</div>}
    </div>
  );
}

// ---- Whale Activity Tab ---------------------------------------------------
function WhaleActivityTab() {
  const [whaleData, setWhaleData] = useState<{ blocks: WhaleBlock[]; total: number; scanned: number } | null>(null);
  const [loading, setLoading]     = useState(true);
  const [filter, setFilter]       = useState<"ALL"|"CALL"|"PUT"|"LEAPS">("ALL");
  const [saved, setSaved]         = useState<Record<string, boolean>>({});
  const handleSave = async (e: React.MouseEvent, b: WhaleBlock) => {
    e.stopPropagation();
    const key = `${b.ticker}-${b.strike}-${b.expiry}`;
    try {
      await addTradeWatchlist({ ticker: b.ticker, strike: b.strike, expiry: b.expiry, option_type: b.direction, notes: `Whale Block: $${b.prem_m}M · ${b.tier}` });
      setSaved(s => ({ ...s, [key]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [key]: false })), 2500);
    } catch {}
  };

  useEffect(() => {
    setLoading(true);
    fetchWhaleActivity()
      .then(d => setWhaleData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = (whaleData?.blocks ?? []).filter(b => {
    if (filter === "CALL")  return b.direction === "CALL";
    if (filter === "PUT")   return b.direction === "PUT";
    if (filter === "LEAPS") return b.category === "LEAPS";
    return true;
  });

  const tierBadge = (tier: string) => {
    if (tier === "MEGA_WHALE") return { label: "🐋 MEGA WHALE", color: "#818cf8", bg: "rgba(129,140,248,0.15)", border: "rgba(129,140,248,0.35)" };
    if (tier === "WHALE")      return { label: "🐳 WHALE",      color: "#60a5fa", bg: "rgba(96,165,250,0.12)",  border: "rgba(96,165,250,0.3)" };
    return                            { label: "⚡ BIG BLOCK",  color: "#fbbf24", bg: "rgba(251,191,36,0.1)",   border: "rgba(251,191,36,0.25)" };
  };
  const catBadge = (cat: string) => {
    if (cat === "LEAPS")      return { label: "LEAPS 6-12mo",     color: "#a78bfa", bg: "rgba(167,139,250,0.1)" };
    if (cat === "AGGRESSIVE") return { label: "AGGRESSIVE 30-90d", color: "#4ade80", bg: "rgba(74,222,128,0.08)" };
    return                           { label: "MEDIUM 90-180d",    color: "#94a3b8", bg: "rgba(148,163,184,0.08)" };
  };

  const BB_F = "JetBrains Mono, monospace";

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ fontFamily: BB_F, fontWeight: 900, color: "#fff", fontSize: 22, margin: 0, marginBottom: 4 }}>🐋 Whale Activity</h2>
          <p style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12, margin: 0 }}>
            Single-strike options blocks $5M+ · 30–365 day expirations
            {whaleData ? ` · ${whaleData.scanned} tickers scanned` : " · scanning…"}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {(["ALL","CALL","PUT","LEAPS"] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{
              padding: "6px 16px", borderRadius: 8, fontFamily: BB_F, fontSize: 12, fontWeight: 700, cursor: "pointer", transition: "all 0.15s",
              background: filter === f ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
              border: `1px solid ${filter === f ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.1)"}`,
              color: filter === f ? "#4ade80" : "#64748b",
            }}>{f}</button>
          ))}
        </div>
      </div>

      {/* Stats row */}
      {whaleData && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Total Blocks Found", val: whaleData.total,                                              color: "#4ade80" },
            { label: "Mega Whales ($20M+)", val: whaleData.blocks.filter(b => b.tier === "MEGA_WHALE").length, color: "#818cf8" },
            { label: "LEAPS Blocks (6-12mo)", val: whaleData.blocks.filter(b => b.category === "LEAPS").length, color: "#a78bfa" },
          ].map(s => (
            <div key={s.label} style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 16, padding: "16px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 28, color: s.color, letterSpacing: "-0.04em", marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 6, marginBottom: 16 }}>
            {[0,1,2].map(i => (
              <span key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", display: "inline-block",
                animation: "bounce 1s infinite", animationDelay: `${i*0.15}s` }} />
            ))}
          </div>
          <p style={{ fontFamily: BB_F, color: "#475569", fontSize: 13 }}>Scanning all tickers for whale blocks… ~30s</p>
        </div>
      )}

      {/* Empty */}
      {!loading && filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🐋</div>
          <p style={{ fontFamily: BB_F, color: "#475569" }}>No whale blocks detected. Check back after market open.</p>
        </div>
      )}

      {/* Blocks list */}
      {!loading && filtered.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {filtered.map((b, i) => {
            const tier   = tierBadge(b.tier);
            const cat    = catBadge(b.category);
            const isCall = b.direction === "CALL";
            const dirColor = isCall ? "#4ade80" : "#f87171";
            const dirBg    = isCall ? "rgba(74,222,128,0.12)" : "rgba(248,113,113,0.12)";
            const megaBorder = b.tier === "MEGA_WHALE" ? "rgba(129,140,248,0.35)"
                             : b.tier === "WHALE"      ? "rgba(96,165,250,0.2)"
                             : "rgba(255,255,255,0.07)";
            return (
              <div key={i} style={{ background: "rgba(255,255,255,0.025)", border: `1px solid ${megaBorder}`,
                borderRadius: 18, padding: "16px 20px", display: "flex", alignItems: "center",
                justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                {/* Left side */}
                <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#334155", fontSize: 16, minWidth: 28 }}>#{i+1}</span>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#f1f5f9", fontSize: 20 }}>{b.ticker}</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12 }}>${b.price.toFixed(2)}</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: dirBg, color: dirColor, border: `1px solid ${dirColor}40` }}>{b.direction}</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: tier.bg, color: tier.color, border: `1px solid ${tier.border}` }}>{tier.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 13, fontWeight: 700 }}>${b.strike} strike</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12 }}>exp {b.expiry}</span>
                      <span style={{ fontFamily: BB_F, fontSize: 11, padding: "2px 8px", borderRadius: 6,
                        background: cat.bg, color: cat.color }}>{cat.label}</span>
                      <span style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>
                        {b.otm_pct > 0 ? `+${b.otm_pct}% OTM` : b.otm_pct < 0 ? `${Math.abs(b.otm_pct)}% ITM` : "ATM"}
                      </span>
                    </div>
                  </div>
                </div>
                {/* Right side — premium + save */}
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 28, letterSpacing: "-0.04em", marginBottom: 2,
                    color: b.prem_m >= 20 ? "#818cf8" : b.prem_m >= 10 ? "#60a5fa" : "#fbbf24" }}>
                    ${b.prem_m}M
                  </div>
                  <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{b.volume.toLocaleString()} contracts</div>
                  <div style={{ fontFamily: BB_F, color: "#334155", fontSize: 11 }}>{b.days_out}d out</div>
                  <button onClick={e => handleSave(e, b)} style={{ marginTop: 8, padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "#4ade80" : "#64748b" }}>
                    {saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "✓ Saved" : "📌 Save"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p style={{ fontFamily: BB_F, textAlign: "center", color: "#1e293b", fontSize: 11, marginTop: 32 }}>
        Refreshes every 30 min · $5M+ single-strike volume premium · 30–365 day expirations only
      </p>
    </div>
  );
}

// ---- Whale Log Tab (persistent all-time history) -------------------------
function WhaleLogTab() {
  const [data, setData]         = useState<{ blocks: WhaleHistoryBlock[]; total: number } | null>(null);
  const [loading, setLoading]   = useState(true);
  const [search, setSearch]     = useState("");
  const [filter, setFilter]     = useState<"ALL"|"CALL"|"PUT"|"LEAPS"|"MEGA_WHALE">("ALL");
  const [saved, setSaved]       = useState<Record<string, boolean>>({});

  const BB_F = "JetBrains Mono, monospace";

  const handleSave = async (e: React.MouseEvent, b: WhaleHistoryBlock) => {
    e.stopPropagation();
    const key = `${b.ticker}-${b.strike}-${b.expiry}`;
    try {
      await addTradeWatchlist({ ticker: b.ticker, strike: b.strike, expiry: b.expiry, option_type: b.direction, notes: `Whale Log: $${b.prem_m}M · ${b.tier}` });
      setSaved(s => ({ ...s, [key]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [key]: false })), 2500);
    } catch {}
  };

  useEffect(() => {
    setLoading(true);
    fetchWhaleHistory()
      .then(d => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = (data?.blocks ?? []).filter(b => {
    const matchSearch = !search || b.ticker.includes(search.toUpperCase());
    const matchFilter =
      filter === "ALL"       ? true :
      filter === "CALL"      ? b.direction === "CALL" :
      filter === "PUT"       ? b.direction === "PUT" :
      filter === "LEAPS"     ? b.category === "LEAPS" :
      filter === "MEGA_WHALE"? b.tier === "MEGA_WHALE" : true;
    return matchSearch && matchFilter;
  });

  const tierBadge = (tier: string) => {
    if (tier === "MEGA_WHALE") return { label: "🐋 MEGA WHALE", color: "#818cf8", bg: "rgba(129,140,248,0.15)", border: "rgba(129,140,248,0.35)" };
    if (tier === "WHALE")      return { label: "🐳 WHALE",      color: "#60a5fa", bg: "rgba(96,165,250,0.12)",  border: "rgba(96,165,250,0.3)" };
    return                            { label: "⚡ BIG BLOCK",  color: "#fbbf24", bg: "rgba(251,191,36,0.1)",   border: "rgba(251,191,36,0.25)" };
  };

  const megaCount  = (data?.blocks ?? []).filter(b => b.tier === "MEGA_WHALE").length;
  const leapsCount = (data?.blocks ?? []).filter(b => b.category === "LEAPS").length;
  const totalPrem  = (data?.blocks ?? []).reduce((s, b) => s + b.prem_m, 0);

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ fontFamily: BB_F, fontWeight: 900, color: "#fff", fontSize: 22, margin: 0, marginBottom: 4 }}>📋 Whale Block Log</h2>
          <p style={{ fontFamily: BB_F, color: "#64748b", fontSize: 12, margin: 0 }}>
            All-time history · Every $5M+ block ever detected · Newest first
            {data ? ` · ${data.total} blocks on record` : " · loading…"}
          </p>
        </div>
      </div>

      {/* Stats */}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16, marginBottom: 24 }}>
          {[
            { label: "Total Blocks on Record", val: data.total,                         color: "#4ade80" },
            { label: "Mega Whales ($20M+)",     val: megaCount,                          color: "#818cf8" },
            { label: "Total Premium Tracked",   val: `$${totalPrem.toFixed(0)}M`,        color: "#fbbf24" },
          ].map(s => (
            <div key={s.label} style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 16, padding: "16px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 24, color: s.color, letterSpacing: "-0.04em", marginBottom: 4 }}>{s.val}</div>
              <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters + Search */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20, alignItems: "center" }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Filter by ticker…"
          style={{ fontFamily: BB_F, fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.1)", background: "rgba(255,255,255,0.04)", color: "#f1f5f9", outline: "none", width: 160 }}
        />
        {(["ALL","CALL","PUT","LEAPS","MEGA_WHALE"] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "6px 14px", borderRadius: 8, fontFamily: BB_F, fontSize: 12, fontWeight: 700, cursor: "pointer", transition: "all 0.15s",
            background: filter === f ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
            border: `1px solid ${filter === f ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.1)"}`,
            color: filter === f ? "#4ade80" : "#64748b",
          }}>{f === "MEGA_WHALE" ? "🐋 MEGA" : f}</button>
        ))}
        {filtered.length !== (data?.total ?? 0) && (
          <span style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{filtered.length} shown</span>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ display: "flex", justifyContent: "center", gap: 6, marginBottom: 16 }}>
            {[0,1,2].map(i => (
              <span key={i} style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", display: "inline-block",
                animation: "bounce 1s infinite", animationDelay: `${i*0.15}s` }} />
            ))}
          </div>
          <p style={{ fontFamily: BB_F, color: "#475569", fontSize: 13 }}>Loading whale block history…</p>
        </div>
      )}

      {/* Empty */}
      {!loading && filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "80px 0" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>📋</div>
          <p style={{ fontFamily: BB_F, color: "#475569" }}>
            {data?.total === 0
              ? "No blocks saved yet. Open the 🐋 Whale Activity tab to trigger the first scan."
              : "No blocks match your filter."}
          </p>
        </div>
      )}

      {/* Block list */}
      {!loading && filtered.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {filtered.map((b, i) => {
            const tier    = tierBadge(b.tier);
            const isCall  = b.direction === "CALL";
            const dirColor = isCall ? "#4ade80" : "#f87171";
            const dirBg    = isCall ? "rgba(74,222,128,0.12)" : "rgba(248,113,113,0.12)";
            const megaBorder = b.tier === "MEGA_WHALE" ? "rgba(129,140,248,0.35)"
                             : b.tier === "WHALE"      ? "rgba(96,165,250,0.2)"
                             : "rgba(255,255,255,0.07)";
            return (
              <div key={i} style={{ background: "rgba(255,255,255,0.02)", border: `1px solid ${megaBorder}`,
                borderRadius: 14, padding: "14px 18px", display: "flex", alignItems: "center",
                justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, fontWeight: 900, color: "#f1f5f9", fontSize: 18 }}>{b.ticker}</span>
                      {b.price && <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 11 }}>${b.price.toFixed(2)}</span>}
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: dirBg, color: dirColor, border: `1px solid ${dirColor}40` }}>{b.direction}</span>
                      <span style={{ fontFamily: BB_F, fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 99,
                        background: tier.bg, color: tier.color, border: `1px solid ${tier.border}` }}>{tier.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <span style={{ fontFamily: BB_F, color: "#e2e8f0", fontSize: 12, fontWeight: 700 }}>${b.strike} strike</span>
                      <span style={{ fontFamily: BB_F, color: "#64748b", fontSize: 11 }}>exp {b.expiry}</span>
                      {b.days_out != null && <span style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{b.days_out}d out</span>}
                      <span style={{ fontFamily: BB_F, color: "#334155", fontSize: 11 }}>first seen {b.first_seen}</span>
                    </div>
                  </div>
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div style={{ fontFamily: BB_F, fontWeight: 900, fontSize: 22, letterSpacing: "-0.04em", marginBottom: 2,
                    color: b.prem_m >= 20 ? "#818cf8" : b.prem_m >= 10 ? "#60a5fa" : "#fbbf24" }}>
                    ${b.prem_m}M
                  </div>
                  {b.volume && <div style={{ fontFamily: BB_F, color: "#475569", fontSize: 11 }}>{b.volume.toLocaleString()} contracts</div>}
                  <button onClick={e => handleSave(e, b)} style={{ marginTop: 8, padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "#4ade80" : "#64748b" }}>
                    {saved[`${b.ticker}-${b.strike}-${b.expiry}`] ? "✓ Saved" : "📌 Save"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p style={{ fontFamily: BB_F, textAlign: "center", color: "#1e293b", fontSize: 11, marginTop: 28 }}>
        Updated every time 🐋 Whale Activity is scanned · Blocks never deleted · Max 500 shown
      </p>
    </div>
  );
}


// ---- Signal Outcome Tracker Tab ------------------------------------------
function TrackRecordTab() {
  const [data, setData]         = useState<AITradeLogResult | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [dirFilter, setDirFilter] = useState<"ALL" | "BULLISH" | "BEARISH" | "NEUTRAL">("ALL");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try {
      setData(await fetchAITradeLog());
    } catch (e: any) {
      setError(e.message ?? "Failed to load track record");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const trades = (data?.trades ?? []).filter(t => dirFilter === "ALL" || t.direction === dirFilter);

  const pctColor = (v: number | null) => {
    if (v === null) return BB_LABEL;
    return v > 0 ? BB_GREEN : v < 0 ? BB_RED : BB_LABEL;
  };
  const pctFmt = (v: number | null) => v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
  const winBadge = (w: boolean | null) => {
    if (w === null) return <span style={{ color: BB_LABEL, fontSize: 9 }}>—</span>;
    return w
      ? <span style={{ color: BB_GREEN, fontSize: 9, fontWeight: 700 }}>WIN</span>
      : <span style={{ color: BB_RED, fontSize: 9, fontWeight: 700 }}>LOSS</span>;
  };
  const outcomeBadge = (o: string) => {
    if (o === "WIN")  return <span style={{ background: "#002200", color: BB_GREEN, fontSize: 9, fontWeight: 700, padding: "2px 7px", border: "1px solid #22c55e44" }}>WIN</span>;
    if (o === "LOSS") return <span style={{ background: "#220000", color: BB_RED,   fontSize: 9, fontWeight: 700, padding: "2px 7px", border: "1px solid #ef444444" }}>LOSS</span>;
    return <span style={{ color: BB_LABEL, fontSize: 9, fontWeight: 700, padding: "2px 7px", border: `1px solid ${BB_BORDER}` }}>OPEN</span>;
  };
  const dirColor = (d: string) => d === "BULLISH" ? BB_GREEN : d === "BEARISH" ? BB_RED : "#fbbf24";

  const statBox = (label: string, val: string | null, accent?: string) => (
    <div style={{ background: BB_PANEL, border: `1px solid ${BB_BORDER}`, padding: "12px 16px", minWidth: 120, flex: 1 }}>
      <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>{label}</div>
      <div style={{ color: accent ?? BB_WHITE, fontSize: 22, fontWeight: 900, fontFamily: BB_FONT }}>
        {val ?? "—"}
      </div>
    </div>
  );

  return (
    <div style={{ padding: 16, color: BB_WHITE, fontFamily: BB_FONT }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 900, letterSpacing: "0.15em", color: BB_WHITE }}>AI TRADE RECORD</div>
          <div style={{ fontSize: 9, color: BB_LABEL, marginTop: 2, letterSpacing: "0.08em" }}>
            Every daily AI pick logged · Win/loss measured at options expiry date
          </div>
        </div>
        <button onClick={load} disabled={loading} style={{
          background: "transparent", border: `1px solid ${BB_BORDER}`, color: BB_LABEL,
          padding: "5px 14px", fontFamily: BB_FONT, fontSize: 9, cursor: "pointer", letterSpacing: "0.1em",
          opacity: loading ? 0.5 : 1,
        }}>{loading ? "LOADING…" : "REFRESH"}</button>
      </div>

      {error && <div style={{ color: BB_RED, fontSize: 10, marginBottom: 12 }}>ERROR: {error}</div>}

      {/* Aggregate stats */}
      {data && (
        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          {statBox("TOTAL CALLS", String(data.count))}
          {/* Primary: expiry win rate */}
          <div style={{ background: BB_PANEL, border: `2px solid ${data.win_rates.expiry != null && data.win_rates.expiry >= 50 ? "#22c55e" : data.win_rates.expiry != null ? "#ef4444" : BB_BORDER}`, padding: "12px 16px", minWidth: 140, flex: 1 }}>
            <div style={{ color: BB_LABEL, fontSize: 9, letterSpacing: "0.1em", marginBottom: 4 }}>WIN RATE @ EXPIRY</div>
            <div style={{ color: data.win_rates.expiry != null && data.win_rates.expiry >= 50 ? BB_GREEN : data.win_rates.expiry != null ? BB_RED : BB_LABEL, fontSize: 26, fontWeight: 900, fontFamily: BB_FONT }}>
              {data.win_rates.expiry != null ? `${data.win_rates.expiry}%` : "—"}
            </div>
            <div style={{ color: BB_LABEL, fontSize: 8, marginTop: 2 }}>PRIMARY METRIC</div>
          </div>
          {statBox("WIN RATE T+1", data.win_rates.t1 != null ? `${data.win_rates.t1}%` : null, data.win_rates.t1 != null && data.win_rates.t1 >= 50 ? BB_GREEN : BB_RED)}
          {statBox("WIN RATE T+3", data.win_rates.t3 != null ? `${data.win_rates.t3}%` : null, data.win_rates.t3 != null && data.win_rates.t3 >= 50 ? BB_GREEN : BB_RED)}
          {statBox("WIN RATE T+5", data.win_rates.t5 != null ? `${data.win_rates.t5}%` : null, data.win_rates.t5 != null && data.win_rates.t5 >= 50 ? BB_GREEN : BB_RED)}
        </div>
      )}

      {/* Direction breakdown */}
      {data && (
        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          {(["BULLISH", "BEARISH", "NEUTRAL"] as const).map(d => {
            const s = data.by_direction[d];
            if (!s) return null;
            return (
              <div key={d} style={{ background: BB_PANEL, border: `1px solid ${BB_BORDER}`, padding: "10px 14px", display: "flex", gap: 20, alignItems: "center" }}>
                <span style={{ color: dirColor(d), fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>{d}</span>
                <span style={{ color: BB_LABEL, fontSize: 9 }}>{s.count} calls</span>
                <span style={{ color: s.win_rate_expiry != null && s.win_rate_expiry >= 50 ? BB_GREEN : s.win_rate_expiry != null ? BB_RED : BB_LABEL, fontSize: 11, fontWeight: 700 }}>
                  {s.win_rate_expiry != null ? `${s.win_rate_expiry}% @ EXPIRY` : s.win_rate_t5 != null ? `${s.win_rate_t5}% @ T+5` : "—"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Direction filter */}
      <div style={{ display: "flex", gap: 0, marginBottom: 12, borderBottom: `1px solid ${BB_BORDER}` }}>
        {(["ALL", "BULLISH", "BEARISH", "NEUTRAL"] as const).map(f => (
          <button key={f} onClick={() => setDirFilter(f)} style={{
            background: "transparent", border: "none", borderBottom: dirFilter === f ? `2px solid ${BB_GREEN}` : "2px solid transparent",
            color: dirFilter === f ? BB_GREEN : BB_LABEL, padding: "6px 14px", fontFamily: BB_FONT, fontSize: 9,
            fontWeight: dirFilter === f ? 700 : 500, cursor: "pointer", letterSpacing: "0.08em", marginBottom: -1,
          }}>{f}</button>
        ))}
        <span style={{ marginLeft: "auto", padding: "6px 12px", color: BB_LABEL, fontSize: 9 }}>
          {trades.length} TRADES
        </span>
      </div>

      {/* Empty state */}
      {!loading && trades.length === 0 && (
        <div style={{ textAlign: "center", padding: "40px 20px", color: BB_LABEL }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>📈</div>
          <div style={{ fontSize: 11, letterSpacing: "0.08em" }}>NO TRADES LOGGED YET</div>
          <div style={{ fontSize: 9, marginTop: 6 }}>Trades are saved automatically when the AI generates daily picks.</div>
          <div style={{ fontSize: 9, marginTop: 4 }}>Visit the <strong style={{ color: BB_WHITE }}>AI TRADE DESK</strong> tab to generate today's picks.</div>
        </div>
      )}

      {/* Trade table */}
      {trades.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          {/* Table header */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "80px 65px 85px 65px 65px 65px 60px 60px 80px 70px",
            gap: 0, borderBottom: `1px solid ${BB_BORDER}`,
            padding: "5px 8px", marginBottom: 2,
          }}>
            {["DATE","TICKER","DIRECTION","ENTRY","TARGET","STOP","T+1","T+3","@ EXPIRY","OUTCOME"].map(h => (
              <span key={h} style={{ color: h === "@ EXPIRY" ? "#fbbf24" : BB_LABEL, fontSize: 8, letterSpacing: "0.1em", fontWeight: 700 }}>{h}</span>
            ))}
          </div>

          {trades.map(t => (
            <React.Fragment key={t.id}>
              <div
                onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "80px 65px 85px 65px 65px 65px 60px 60px 80px 70px",
                  gap: 0, padding: "8px 8px", cursor: "pointer",
                  borderBottom: `1px solid ${BB_BORDER}`,
                  background: expanded === t.id ? "#0d1a0d" : "transparent",
                  transition: "background 0.15s",
                }}
              >
                <span style={{ color: BB_LABEL, fontSize: 9 }}>{t.trade_date}</span>
                <span style={{ color: BB_WHITE, fontSize: 10, fontWeight: 700 }}>{t.ticker}</span>
                <span style={{ color: dirColor(t.direction), fontSize: 9, fontWeight: 700 }}>{t.direction}</span>
                <span style={{ color: BB_WHITE, fontSize: 9 }}>${t.price_at_signal?.toFixed(2) ?? "—"}</span>
                <span style={{ color: BB_GREEN, fontSize: 9 }}>{t.target_price ? `$${t.target_price.toFixed(2)}` : "—"}</span>
                <span style={{ color: BB_RED,   fontSize: 9 }}>{t.stop_loss    ? `$${t.stop_loss.toFixed(2)}`    : "—"}</span>
                <span style={{ color: pctColor(t.t1_pct), fontSize: 9, fontWeight: 700 }}>{pctFmt(t.t1_pct)}</span>
                <span style={{ color: pctColor(t.t3_pct), fontSize: 9, fontWeight: 700 }}>{pctFmt(t.t3_pct)}</span>
                {/* Expiry column — primary outcome */}
                <span style={{ color: pctColor(t.expiry_pct), fontSize: 10, fontWeight: 900 }}>
                  {t.expiry_pct != null ? pctFmt(t.expiry_pct) : t.expiry ? <span style={{ color: BB_LABEL, fontSize: 8 }}>{t.expiry}</span> : "—"}
                </span>
                <span>{outcomeBadge(t.outcome)}</span>
              </div>

              {/* Expanded row */}
              {expanded === t.id && (
                <div style={{
                  borderBottom: `1px solid ${BB_BORDER}`,
                  background: "#0a120a", padding: "12px 16px",
                  display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 24px",
                }}>
                  {/* Left: price timeline */}
                  <div>
                    {/* Expiry result — primary */}
                    {(t.expiry_price != null || t.expiry) && (
                      <div style={{ background: "#060e06", border: `2px solid ${t.expiry_win === true ? "#22c55e" : t.expiry_win === false ? "#ef4444" : "#fbbf2444"}`, padding: "10px 12px", marginBottom: 10, display: "flex", alignItems: "center", gap: 16 }}>
                        <div>
                          <div style={{ color: "#fbbf24", fontSize: 8, letterSpacing: "0.1em", fontWeight: 700 }}>@ EXPIRY · {t.expiry}</div>
                          <div style={{ color: BB_WHITE, fontSize: 16, fontWeight: 900, marginTop: 2 }}>
                            {t.expiry_price != null ? `$${t.expiry_price.toFixed(2)}` : "PENDING"}
                          </div>
                        </div>
                        {t.expiry_pct != null && (
                          <div style={{ textAlign: "right" }}>
                            <div style={{ color: pctColor(t.expiry_pct), fontSize: 18, fontWeight: 900 }}>{pctFmt(t.expiry_pct)}</div>
                            <div style={{ marginTop: 2 }}>{winBadge(t.expiry_win)}</div>
                          </div>
                        )}
                      </div>
                    )}
                    <div style={{ color: BB_LABEL, fontSize: 8, letterSpacing: "0.1em", marginBottom: 8 }}>SUPPLEMENTAL CHECKPOINTS</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
                      {([
                        { label: "T+1",  price: t.t1_price,  pct: t.t1_pct,  win: t.t1_win },
                        { label: "T+3",  price: t.t3_price,  pct: t.t3_pct,  win: t.t3_win },
                        { label: "T+5",  price: t.t5_price,  pct: t.t5_pct,  win: t.t5_win },
                        { label: "T+10", price: t.t10_price, pct: t.t10_pct, win: t.t10_win },
                      ]).map(({ label, price, pct, win }) => (
                        <div key={label} style={{ background: "#060c06", border: `1px solid ${BB_BORDER}`, padding: "8px 10px", textAlign: "center" }}>
                          <div style={{ color: BB_LABEL, fontSize: 8, letterSpacing: "0.08em" }}>{label}</div>
                          <div style={{ color: BB_WHITE, fontSize: 11, fontWeight: 700, margin: "4px 0" }}>
                            {price != null ? `$${price.toFixed(2)}` : "—"}
                          </div>
                          <div style={{ color: pctColor(pct), fontSize: 10, fontWeight: 700 }}>{pctFmt(pct)}</div>
                          <div style={{ marginTop: 4 }}>{winBadge(win)}</div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Right: thesis + signals */}
                  <div>
                    <div style={{ color: BB_LABEL, fontSize: 8, letterSpacing: "0.1em", marginBottom: 6 }}>SETUP</div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                      {t.setup_type  && <span style={{ color: "#fbbf24", fontSize: 9 }}>{t.setup_type}</span>}
                      {t.conviction  && <span style={{ color: BB_LABEL, fontSize: 9 }}>CONVICTION: <span style={{ color: BB_WHITE }}>{t.conviction}</span></span>}
                      {t.risk_level  && <span style={{ color: BB_LABEL, fontSize: 9 }}>RISK: <span style={{ color: BB_WHITE }}>{t.risk_level}</span></span>}
                    </div>
                    {t.entry_strike && (
                      <div style={{ color: BB_LABEL, fontSize: 9, marginBottom: 6 }}>
                        STRIKE: <span style={{ color: BB_WHITE }}>${t.entry_strike}</span>
                      </div>
                    )}
                    {t.thesis && (
                      <div style={{ color: "#9ca3af", fontSize: 9, lineHeight: 1.5, marginBottom: 8 }}>{t.thesis}</div>
                    )}
                    {t.signals_aligned && t.signals_aligned.length > 0 && (
                      <div>
                        <div style={{ color: BB_LABEL, fontSize: 8, letterSpacing: "0.08em", marginBottom: 4 }}>SIGNALS ALIGNED</div>
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {t.signals_aligned.map((s, i) => (
                            <span key={i} style={{ background: "#001a00", border: "1px solid #22c55e33", color: BB_GREEN, fontSize: 8, padding: "2px 6px" }}>{s}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

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
  const [flowView, setFlowView] = useState<"bullish"|"strong"|"bearish">("strong");
  const [theses,       setTheses]       = useState<Record<string, string>>({});
  const [loadThesis,   setLoadThesis]   = useState<Record<string, boolean>>({});
  const [expandThesis, setExpandThesis] = useState<Set<string>>(new Set());
  const [saved, setSaved]               = useState<Record<string, boolean>>({});

  const handleSave = async (e: React.MouseEvent, row: BullFlowRow) => {
    e.stopPropagation();
    try {
      await addTradeWatchlist({ ticker: row.ticker, strike: row.strike ?? undefined, expiry: row.expiry ?? undefined, option_type: "CALL", notes: `Bull Flow: $${row.premium_m.toFixed(1)}M · C/P ${row.call_put_ratio.toFixed(1)}x` });
      setSaved(s => ({ ...s, [row.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [row.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

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

      {/* Empty state — not yet run */}
      {!loading && results.length === 0 && !error && !lastRun && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🔥</div>
          <div className="font-semibold text-slate-400 mb-1">Run the scan to see today's flow</div>
          <div className="text-sm">Ranks {scanned || 25}+ stocks by options premium — then splits by direction</div>
        </div>
      )}
      {/* Empty state — scan ran but no $500K+ results */}
      {!loading && results.length === 0 && !error && lastRun && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🔕</div>
          <div className="font-semibold text-slate-400 mb-1">No $500K+ institutional flow right now</div>
          <div className="text-sm">Options premium thins out after market close — check back during market hours (9:30 AM – 4 PM ET)</div>
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
              <div className="px-4 pb-3 flex items-center gap-4">
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
                <button
                  onClick={e => handleSave(e, row)}
                  style={{ marginLeft: "auto", padding: "5px 12px", borderRadius: 7, fontSize: 11, fontWeight: 700, cursor: "pointer", border: "1px solid", transition: "all 0.2s",
                    background: saved[row.ticker] ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)",
                    borderColor: saved[row.ticker] ? "rgba(34,197,94,0.4)" : "rgba(255,255,255,0.12)",
                    color: saved[row.ticker] ? "#4ade80" : "#64748b" }}
                >
                  {saved[row.ticker] ? "✓ Saved" : "📌 Save"}
                </button>
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
const BB_BORDER = "rgba(255,255,255,0.12)";
const BB_BDR2   = "rgba(255,255,255,0.18)";
const BB_LABEL  = "#94a3b8";
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
  const bullFlow = (bullData?.results ?? []).filter(r => r.call_put_ratio >= 2).slice(0, 8);
  const indices  = mktData?.indices ?? [];
  const sectors  = mktData?.sectors ?? [];
  const adv      = mktData?.advance_decline;
  const cc       = (v: number) => v >= 0 ? BB_GREEN : BB_RED;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "300px 1fr 240px", gridTemplateRows: "1fr 160px", flex: 1, overflow: "hidden", background: BB_BG }}>

      {/* LEFT: Top 10 */}
      <BBPanel style={{ gridRow: "1 / 3", borderRight: `1px solid ${BB_BORDER}`, overflow: "hidden" }}>
        <BBPanelHeader label="Top 15 · Score 8+" sub={`${top10Data?.total_scanned ?? 0} SCANNED`} />
        <div style={{ flex: 1, overflowY: "auto" }}>
          {top10.length === 0 && <div style={{ padding: 20, color: BB_LABEL, fontFamily: BB_FONT, fontSize: 11, textAlign: "center" }}>Run a scan to populate Top 15</div>}
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

// ---- Net Flow Tab --------------------------------------------------------

function NetFlowTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [results, setResults]   = useState<NetFlowRow[]>([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [scanned, setScanned]   = useState(0);
  const [lastRun, setLastRun]   = useState<Date | null>(null);
  const [minNet, setMinNet]     = useState<50 | 100 | 250>(50);
  const [saved, setSaved]       = useState<Record<string, boolean>>({});

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const data = await fetchNetFlow();
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

  const handleSave = async (e: React.MouseEvent, row: NetFlowRow) => {
    e.stopPropagation();
    try {
      await addTradeWatchlist({
        ticker: row.ticker,
        option_type: "CALL",
        notes: `Net Flow: +$${row.net_m.toFixed(1)}M net · $${row.inflow_m.toFixed(1)}M in · ratio ${row.flow_ratio.toFixed(2)}x`,
      });
      setSaved(s => ({ ...s, [row.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [row.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

  const filtered = results.filter(r => r.net_m >= minNet);

  const fmtM = (v: number) => v >= 1000 ? `$${(v / 1000).toFixed(1)}B` : `$${v.toFixed(1)}M`;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-white font-bold text-lg flex items-center gap-2">
              💰 Net Equity Flow
            </h2>
            <p className="text-slate-400 text-sm mt-1">
              Real buying pressure — stocks with more buy volume than sell volume today, largest to smallest.
            </p>
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="shrink-0 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "🔄 Run Scan"}
          </button>
        </div>

        {/* Min threshold filter */}
        <div className="flex gap-2">
          {([50, 100, 250] as const).map(v => (
            <button
              key={v}
              onClick={() => setMinNet(v)}
              className={`flex-1 py-2 rounded-lg text-sm font-bold border transition-colors ${minNet === v ? "bg-emerald-600 border-emerald-500 text-white" : "border-slate-700 text-slate-400 hover:text-slate-200"}`}
            >
              ${v}M+
            </button>
          ))}
        </div>

        {lastRun && (
          <p className="text-slate-600 text-xs mt-2">
            Scanned {scanned} tickers · {lastRun.toLocaleTimeString()} · showing {filtered.length} with ${minNet}M+ net inflow
          </p>
        )}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Not yet run */}
      {!loading && results.length === 0 && !error && !lastRun && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">💰</div>
          <div className="font-semibold text-slate-400 mb-1">Run the scan to see today's net flow</div>
          <div className="text-sm">Computes buy vs sell dollar flow for 50 stocks using intraday data</div>
        </div>
      )}

      {/* Ran but nothing above threshold */}
      {!loading && lastRun && filtered.length === 0 && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🔕</div>
          <div className="font-semibold text-slate-400 mb-1">No stocks with ${minNet}M+ net inflow right now</div>
          <div className="text-sm">Try a lower threshold, or check back during market hours (9:30 AM – 4 PM ET)</div>
        </div>
      )}

      {/* Results */}
      {filtered.map((row, i) => {
        const pctIn  = row.total_vol_m > 0 ? (row.inflow_m  / row.total_vol_m * 100) : 50;
        const isSaved = saved[row.ticker];
        return (
          <div
            key={row.ticker}
            onClick={() => onSelectTicker(row.ticker)}
            className="bg-slate-900 border border-slate-800 hover:border-slate-600 rounded-xl p-4 cursor-pointer transition-all"
          >
            {/* Top row */}
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="flex items-center gap-3">
                <span className="text-slate-500 text-sm font-bold w-8">#{row.rank}</span>
                <span className="text-white font-black text-xl">{row.ticker}</span>
                <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
              </div>
              <div className="text-right">
                <div className="text-emerald-400 font-black text-lg">+{fmtM(row.net_m)}</div>
                <div className="text-slate-500 text-xs">net inflow</div>
              </div>
            </div>

            {/* Flow bar */}
            <div className="rounded-full overflow-hidden h-2 bg-red-900/40 mb-3">
              <div
                className="h-full bg-emerald-500 rounded-full transition-all"
                style={{ width: `${Math.min(pctIn, 100)}%` }}
              />
            </div>

            {/* In / Out / Ratio row */}
            <div className="grid grid-cols-3 gap-2 text-center mb-3">
              <div className="bg-emerald-950/40 rounded-lg p-2">
                <div className="text-emerald-400 font-bold text-sm">{fmtM(row.inflow_m)}</div>
                <div className="text-slate-500 text-xs">Inflow</div>
              </div>
              <div className="bg-red-950/40 rounded-lg p-2">
                <div className="text-red-400 font-bold text-sm">{fmtM(row.outflow_m)}</div>
                <div className="text-slate-500 text-xs">Outflow</div>
              </div>
              <div className="bg-slate-800/60 rounded-lg p-2">
                <div className="text-white font-bold text-sm">{row.flow_ratio.toFixed(2)}x</div>
                <div className="text-slate-500 text-xs">Buy/Sell</div>
              </div>
            </div>

            {/* Save button */}
            <button
              onClick={e => handleSave(e, row)}
              className={`w-full py-2 rounded-lg text-xs font-bold transition-all border ${isSaved ? "bg-emerald-900/40 border-emerald-600 text-emerald-300" : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"}`}
            >
              {isSaved ? "✓ SAVED TO WATCHLIST" : "📌 Save"}
            </button>
          </div>
        );
      })}
    </div>
  );
}


// ---- Micro-Cap Net Flow Tab ----------------------------------------------

function NetFlowMicrocapTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData]       = useState<NetFlowMicrocapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const [saved, setSaved]     = useState<Record<string, boolean>>({});

  // Per-section min thresholds (in $M for small, $K for nano/micro)
  const [nanoMin,  setNanoMin]  = useState<0.05 | 0.2 | 0.5>(0.2);   // $50K / $200K / $500K
  const [microMin, setMicroMin] = useState<0.2 | 0.5 | 1>(0.5);      // $200K / $500K / $1M
  const [smallMin, setSmallMin] = useState<2 | 5 | 10>(5);            // $2M / $5M / $10M

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const d = await fetchNetFlowMicrocap();
      setData(d);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { run(); }, []);

  const handleSave = async (e: React.MouseEvent, row: NetFlowRow, tier: string) => {
    e.stopPropagation();
    try {
      const mktcap = row.market_cap_m ? `$${row.market_cap_m.toFixed(0)}M mktcap` : "";
      const pct    = row.net_pct_mktcap ? ` · ${row.net_pct_mktcap.toFixed(2)}% of mktcap` : "";
      await addTradeWatchlist({
        ticker: row.ticker,
        option_type: "CALL",
        notes: `${tier} Net Flow: +${fmtFlow(row.net_m)} net · ratio ${row.flow_ratio.toFixed(2)}x${pct} · ${mktcap}`,
      });
      setSaved(s => ({ ...s, [row.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [row.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

  // Format dollar amount: auto-scale K/M
  const fmtFlow = (v: number) => {
    if (v >= 1)    return `$${v.toFixed(2)}M`;
    if (v >= 0.01) return `$${(v * 1000).toFixed(0)}K`;
    return `$${(v * 1_000_000).toFixed(0)}`;
  };

  const fmtMktcap = (m: number | null) => {
    if (m === null) return "—";
    if (m >= 1000)  return `$${(m / 1000).toFixed(1)}B`;
    return `$${m.toFixed(0)}M`;
  };

  // Render a single stock card (shared across all sections)
  const FlowCard = ({ row, tier }: { row: NetFlowRow; tier: string }) => {
    const pctIn   = row.total_vol_m > 0 ? (row.inflow_m / row.total_vol_m * 100) : 50;
    const isSaved = saved[row.ticker];
    const isStrong = row.flow_ratio >= 1.5;
    const isHuge   = (row.net_pct_mktcap ?? 0) >= 2;   // ≥2% of mktcap = massive

    return (
      <div
        onClick={() => onSelectTicker(row.ticker)}
        className={`bg-slate-900 border rounded-xl p-4 cursor-pointer transition-all hover:border-slate-600 ${isHuge ? "border-amber-700/60" : "border-slate-800"}`}
      >
        {/* Top row */}
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500 text-xs font-bold">#{row.rank}</span>
            <span className="text-white font-black text-lg">{row.ticker}</span>
            <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
            {isHuge && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-900/50 text-amber-300 border border-amber-700/50 font-bold">
                ⚡ {row.net_pct_mktcap?.toFixed(1)}% of mktcap
              </span>
            )}
            {!isHuge && isStrong && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-900/50 text-emerald-300 border border-emerald-700/50 font-bold">
                🔥 Strong
              </span>
            )}
          </div>
          <div className="text-right shrink-0">
            <div className="text-emerald-400 font-black text-base">+{fmtFlow(row.net_m)}</div>
            {row.net_pct_mktcap !== null && (
              <div className={`text-xs font-bold ${(row.net_pct_mktcap ?? 0) >= 2 ? "text-amber-400" : "text-slate-500"}`}>
                {row.net_pct_mktcap.toFixed(2)}% of co.
              </div>
            )}
          </div>
        </div>

        {/* Market cap badge */}
        {row.market_cap_m !== null && (
          <div className="text-slate-600 text-xs mb-2">
            Mkt cap: <span className="text-slate-400 font-bold">{fmtMktcap(row.market_cap_m)}</span>
          </div>
        )}

        {/* Flow bar */}
        <div className="rounded-full overflow-hidden h-1.5 bg-red-900/40 mb-3">
          <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${Math.min(pctIn, 100)}%` }} />
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-1.5 text-center mb-3">
          <div className="bg-emerald-950/40 rounded-lg p-1.5">
            <div className="text-emerald-400 font-bold text-xs">{fmtFlow(row.inflow_m)}</div>
            <div className="text-slate-600 text-xs">Inflow</div>
          </div>
          <div className="bg-red-950/40 rounded-lg p-1.5">
            <div className="text-red-400 font-bold text-xs">{fmtFlow(row.outflow_m)}</div>
            <div className="text-slate-600 text-xs">Outflow</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-1.5">
            <div className="text-white font-bold text-xs">{row.flow_ratio.toFixed(2)}x</div>
            <div className="text-slate-600 text-xs">Buy/Sell</div>
          </div>
        </div>

        <button
          onClick={e => handleSave(e, row, tier)}
          className={`w-full py-1.5 rounded-lg text-xs font-bold transition-all border ${isSaved ? "bg-emerald-900/40 border-emerald-600 text-emerald-300" : "border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300"}`}
        >
          {isSaved ? "✓ SAVED" : "📌 Save to Watchlist"}
        </button>
      </div>
    );
  };

  // Section renderer
  const Section = ({
    emoji, title, subtitle, color, rows, minVal, setMin, thresholds, thresholdLabels,
  }: {
    emoji: string; title: string; subtitle: string; color: string;
    rows: NetFlowRow[]; minVal: number; setMin: (v: any) => void;
    thresholds: number[]; thresholdLabels: string[];
  }) => {
    const filtered = rows.filter(r => r.net_m >= minVal);
    return (
      <div className="space-y-3">
        {/* Section header */}
        <div className={`border rounded-xl p-4 ${color}`}>
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg">{emoji}</span>
                <span className="text-white font-bold">{title}</span>
              </div>
              <p className="text-slate-400 text-xs mt-0.5">{subtitle}</p>
            </div>
            {lastRun && (
              <span className="text-slate-600 text-xs shrink-0">{filtered.length} stocks</span>
            )}
          </div>
          <div className="flex gap-1.5">
            {thresholds.map((v, i) => (
              <button
                key={v}
                onClick={() => setMin(v)}
                className={`flex-1 py-1.5 rounded-lg text-xs font-bold border transition-colors ${minVal === v ? "bg-emerald-600 border-emerald-500 text-white" : "border-slate-700 text-slate-500 hover:text-slate-300"}`}
              >
                {thresholdLabels[i]}+
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 && lastRun && (
          <div className="text-center py-8 text-slate-600 text-sm">
            No {title.toLowerCase()} stocks above {thresholdLabels[thresholds.indexOf(minVal)]} right now
          </div>
        )}

        {filtered.map(row => <FlowCard key={row.ticker} row={row} tier={title} />)}
      </div>
    );
  };

  const totalFound = (data?.nano.length ?? 0) + (data?.micro.length ?? 0) + (data?.small.length ?? 0);

  return (
    <div className="space-y-6">
      {/* Master header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-white font-bold text-lg">🔬 Net Flow by Cap Size</h2>
            <p className="text-slate-400 text-sm mt-1">
              Ranked by <span className="text-amber-400 font-bold">% of market cap</span> — a $500K inflow on a $20M company is bigger than $5M on a $500M one.
            </p>
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="shrink-0 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-4 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "🔄 Run Scan"}
          </button>
        </div>

        {lastRun && (
          <p className="text-slate-600 text-xs">
            Scanned {data?.scanned ?? 473} stocks · {lastRun.toLocaleTimeString()} · {totalFound} with positive net inflow
          </p>
        )}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Key callout */}
      <div className="bg-amber-950/20 border border-amber-800/30 rounded-xl p-4">
        <p className="text-amber-300 text-xs leading-relaxed">
          <span className="font-bold">⚡ The % of market cap metric:</span> When a $20M company shows 2.5% of its entire market cap flowing in during a single trading session, that's not retail — that's someone loading a position. These moves can be 20-50%+ within days.
        </p>
      </div>

      {/* Cold state */}
      {!loading && !lastRun && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🔬</div>
          <div className="font-semibold text-slate-400 mb-1">Scan 473+ stocks across all cap sizes</div>
          <div className="text-sm">Nano · Micro · Small — each ranked by % of market cap flowing in</div>
        </div>
      )}

      {/* Three sections */}
      {lastRun && data && (
        <>
          {/* ── NANO ── */}
          <Section
            emoji="💥" title="Nano-cap" subtitle="Under $50M — even $200K inflow can move these stocks fast"
            color="bg-red-950/20 border border-red-900/40"
            rows={data.nano}
            minVal={nanoMin} setMin={setNanoMin}
            thresholds={[0.05, 0.2, 0.5]}
            thresholdLabels={["$50K", "$200K", "$500K"]}
          />

          {/* ── MICRO ── */}
          <Section
            emoji="🔬" title="Micro-cap" subtitle="$50M–$300M — under-followed, inefficient, institutional accumulation hides here"
            color="bg-violet-950/20 border border-violet-900/40"
            rows={data.micro}
            minVal={microMin} setMin={setMicroMin}
            thresholds={[0.2, 0.5, 1]}
            thresholdLabels={["$200K", "$500K", "$1M"]}
          />

          {/* ── SMALL ── */}
          <Section
            emoji="📊" title="Small-cap" subtitle="$300M–$2B — liquid enough to trade options, volatile enough to move"
            color="bg-blue-950/20 border border-blue-900/40"
            rows={data.small}
            minVal={smallMin} setMin={setSmallMin}
            thresholds={[2, 5, 10]}
            thresholdLabels={["$2M", "$5M", "$10M"]}
          />
        </>
      )}
    </div>
  );
}


// ---- Net Flow Mid-cap Tab -----------------------------------------------

function NetFlowMidcapTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData]       = useState<NetFlowMicrocapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const [saved, setSaved]     = useState<Record<string, boolean>>({});
  const [midMin, setMidMin]   = useState<5 | 10 | 20>(10);   // $5M / $10M / $20M

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const d = await fetchNetFlowMicrocap();
      setData(d);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { run(); }, []);

  const handleSave = async (e: React.MouseEvent, row: NetFlowRow) => {
    e.stopPropagation();
    try {
      const mktcap = row.market_cap_m ? `$${(row.market_cap_m / 1000).toFixed(1)}B mktcap` : "";
      const pct    = row.net_pct_mktcap ? ` · ${row.net_pct_mktcap.toFixed(2)}% of mktcap` : "";
      await addTradeWatchlist({
        ticker: row.ticker,
        option_type: "CALL",
        notes: `Mid-cap Net Flow: +${fmtMid(row.net_m)} net · ratio ${row.flow_ratio.toFixed(2)}x${pct} · ${mktcap}`,
      });
      setSaved(s => ({ ...s, [row.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [row.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

  const fmtMid = (v: number) => {
    if (v >= 1000) return `$${(v / 1000).toFixed(1)}B`;
    if (v >= 1)    return `$${v.toFixed(2)}M`;
    if (v >= 0.01) return `$${(v * 1000).toFixed(0)}K`;
    return `$${(v * 1_000_000).toFixed(0)}`;
  };

  const fmtMktcap = (m: number | null) => {
    if (m === null) return "—";
    if (m >= 1000)  return `$${(m / 1000).toFixed(1)}B`;
    return `$${m.toFixed(0)}M`;
  };

  const rows = data?.mid ?? [];
  const filtered = rows.filter(r => r.net_m >= midMin);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-white font-bold text-lg">🏢 Mid-Cap Net Flow</h2>
            <p className="text-slate-400 text-sm mt-1">
              $2B–$10B+ companies ranked by <span className="text-cyan-400 font-bold">% of market cap</span> flowing in — institutional accumulation before the crowd notices.
            </p>
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="shrink-0 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white px-4 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
          >
            {loading ? <><Spinner /> Scanning…</> : "🔄 Run Scan"}
          </button>
        </div>
        {lastRun && (
          <p className="text-slate-600 text-xs">
            Scanned {data?.scanned ?? 473} stocks · {lastRun.toLocaleTimeString()} · {filtered.length} mid-caps above threshold
          </p>
        )}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* Callout */}
      <div className="bg-cyan-950/20 border border-cyan-800/30 rounded-xl p-4">
        <p className="text-cyan-300 text-xs leading-relaxed">
          <span className="font-bold">🏢 Mid-cap sweet spot:</span> $2B–$10B companies are large enough for institutions to build meaningful positions, but small enough that a strong inflow day still moves the price. When ≥0.5% of market cap flows in, a fund is loading shares.
        </p>
      </div>

      {/* Cold state */}
      {!loading && !lastRun && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">🏢</div>
          <div className="font-semibold text-slate-400 mb-1">Scan mid-cap stocks by net flow</div>
          <div className="text-sm">$2B+ market cap — institutional money moves these names</div>
        </div>
      )}

      {/* Section */}
      {lastRun && data && (
        <div className="space-y-3">
          {/* Section header with threshold filters */}
          <div className="bg-cyan-950/20 border border-cyan-900/40 rounded-xl p-4">
            <div className="flex items-start justify-between gap-3 mb-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-lg">🏢</span>
                  <span className="text-white font-bold">Mid-cap</span>
                </div>
                <p className="text-slate-400 text-xs mt-0.5">Above $2B — institutions accumulate here before analyst upgrades and price targets</p>
              </div>
              <span className="text-slate-600 text-xs shrink-0">{filtered.length} stocks</span>
            </div>
            <div className="flex gap-1.5">
              {([5, 10, 20] as const).map((v, i) => (
                <button
                  key={v}
                  onClick={() => setMidMin(v)}
                  className={`flex-1 py-1.5 rounded-lg text-xs font-bold border transition-colors ${midMin === v ? "bg-cyan-600 border-cyan-500 text-white" : "border-slate-700 text-slate-500 hover:text-slate-300"}`}
                >
                  {["$5M", "$10M", "$20M"][i]}+
                </button>
              ))}
            </div>
          </div>

          {filtered.length === 0 && (
            <div className="text-center py-8 text-slate-600 text-sm">
              No mid-cap stocks above ${midMin}M net inflow right now
            </div>
          )}

          {/* Cards */}
          <div className="space-y-3">
            {filtered.map(row => {
              const pctIn    = row.total_vol_m > 0 ? (row.inflow_m / row.total_vol_m * 100) : 50;
              const isSaved  = saved[row.ticker];
              const isStrong = row.flow_ratio >= 1.5;
              const isBig    = (row.net_pct_mktcap ?? 0) >= 0.5;   // ≥0.5% of mktcap = notable for mid-cap

              return (
                <div
                  key={row.ticker}
                  onClick={() => onSelectTicker(row.ticker)}
                  className={`bg-slate-900 border rounded-xl p-4 cursor-pointer transition-all hover:border-slate-600 ${isBig ? "border-cyan-700/60" : "border-slate-800"}`}
                >
                  {/* Top row */}
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-slate-500 text-xs font-bold">#{row.rank}</span>
                      <span className="text-white font-black text-lg">{row.ticker}</span>
                      <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
                      {isBig && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-900/50 text-cyan-300 border border-cyan-700/50 font-bold">
                          ⚡ {row.net_pct_mktcap?.toFixed(2)}% of mktcap
                        </span>
                      )}
                      {!isBig && isStrong && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-900/50 text-emerald-300 border border-emerald-700/50 font-bold">
                          🔥 Strong
                        </span>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-emerald-400 font-black text-base">+{fmtMid(row.net_m)}</div>
                      {row.net_pct_mktcap !== null && (
                        <div className={`text-xs font-bold ${isBig ? "text-cyan-400" : "text-slate-500"}`}>
                          {row.net_pct_mktcap.toFixed(2)}% of co.
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Market cap */}
                  {row.market_cap_m !== null && (
                    <div className="text-slate-600 text-xs mb-2">
                      Mkt cap: <span className="text-slate-400 font-bold">{fmtMktcap(row.market_cap_m)}</span>
                    </div>
                  )}

                  {/* Flow bar */}
                  <div className="rounded-full overflow-hidden h-1.5 bg-red-900/40 mb-3">
                    <div className="h-full bg-cyan-500 rounded-full" style={{ width: `${Math.min(pctIn, 100)}%` }} />
                  </div>

                  {/* Stats */}
                  <div className="grid grid-cols-3 gap-1.5 text-center mb-3">
                    <div className="bg-emerald-950/40 rounded-lg p-1.5">
                      <div className="text-emerald-400 font-bold text-xs">{fmtMid(row.inflow_m)}</div>
                      <div className="text-slate-600 text-xs">Inflow</div>
                    </div>
                    <div className="bg-red-950/40 rounded-lg p-1.5">
                      <div className="text-red-400 font-bold text-xs">{fmtMid(row.outflow_m)}</div>
                      <div className="text-slate-600 text-xs">Outflow</div>
                    </div>
                    <div className="bg-slate-800/60 rounded-lg p-1.5">
                      <div className="text-white font-bold text-xs">{row.flow_ratio.toFixed(2)}x</div>
                      <div className="text-slate-600 text-xs">Buy/Sell</div>
                    </div>
                  </div>

                  <button
                    onClick={e => handleSave(e, row)}
                    className={`w-full py-1.5 rounded-lg text-xs font-bold transition-all border ${isSaved ? "bg-emerald-900/40 border-emerald-600 text-emerald-300" : "border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300"}`}
                  >
                    {isSaved ? "✓ SAVED" : "📌 Save to Watchlist"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}


// ---- Net Flow Streak Tab -------------------------------------------------

function NetFlowStreakTab({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData]           = useState<NetFlowStreakResult | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [lastRun, setLastRun]     = useState<Date | null>(null);
  const [saved, setSaved]         = useState<Record<string, boolean>>({});
  const [minStreak, setMinStreak] = useState<3 | 5 | 10 | 15>(5);
  // Institutional filter: requires consistency ≥ 0.3 (buying is evenly distributed, not spiked)
  const [instOnly, setInstOnly]   = useState(true);
  // AI signal state
  const [aiSignals, setAiSignals] = useState<AISignalResult | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError,   setAiError]   = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const d = await fetchNetFlowMultiday();
      setData(d);
      setLastRun(new Date());
    } catch (e: any) {
      setError(e.message ?? "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  const runAI = async (rows: NetFlowStreakRow[]) => {
    if (!rows.length) return;
    setAiLoading(true); setAiError(null); setAiSignals(null);
    try {
      const result = await fetchAISignal(rows);
      setAiSignals(result);
    } catch (e: any) {
      setAiError(e.message ?? "AI analysis failed");
    } finally {
      setAiLoading(false);
    }
  };

  useEffect(() => { run(); }, []);

  const handleSave = async (e: React.MouseEvent, row: NetFlowStreakRow) => {
    e.stopPropagation();
    try {
      const mktcap = row.market_cap_m
        ? (row.market_cap_m >= 1000 ? `$${(row.market_cap_m/1000).toFixed(1)}B` : `$${row.market_cap_m}M`) + " mktcap"
        : "";
      const cons = ` · consistency ${Math.round(row.consistency * 100)}%`;
      const pct  = row.total_pct_mktcap ? ` · ${row.total_pct_mktcap.toFixed(2)}% mktcap over ${row.streak}d` : "";
      await addTradeWatchlist({
        ticker: row.ticker,
        option_type: "CALL",
        notes: `${row.streak}-Day Accumulation · +${fmtNet(row.total_net_m)} cumul · avg ${fmtNet(row.avg_daily_net_m)}/day${cons}${pct} · ${mktcap}`,
      });
      setSaved(s => ({ ...s, [row.ticker]: true }));
      setTimeout(() => setSaved(s => ({ ...s, [row.ticker]: false })), 2500);
    } catch { /* silent */ }
  };

  const fmtNet = (v: number) => {
    if (v >= 1000) return `$${(v/1000).toFixed(1)}B`;
    if (v >= 1)    return `$${v.toFixed(2)}M`;
    if (v >= 0.01) return `$${(v*1000).toFixed(0)}K`;
    return `$${(v*1_000_000).toFixed(0)}`;
  };

  const fmtMktcap = (m: number | null) => {
    if (m === null) return "—";
    if (m >= 1000)  return `$${(m/1000).toFixed(1)}B`;
    return `$${m.toFixed(0)}M`;
  };

  const streakBadge = (n: number) => {
    if (n >= 20) return { icon: "🏦", label: `${n}d (1mo+)`,   color: "bg-purple-900/60 text-purple-200 border-purple-600/60" };
    if (n >= 15) return { icon: "🚀", label: `${n}d (3wk)`,    color: "bg-purple-900/50 text-purple-300 border-purple-700/50" };
    if (n >= 10) return { icon: "⚡", label: `${n}d (2wk)`,    color: "bg-amber-900/50  text-amber-300  border-amber-700/50"  };
    if (n >= 5)  return { icon: "🔥", label: `${n}d (1wk)`,    color: "bg-orange-900/50 text-orange-300 border-orange-700/50" };
    return           { icon: "📈", label: `${n}d`,              color: "bg-emerald-900/50 text-emerald-300 border-emerald-700/50" };
  };

  // Consistency label: how evenly distributed is the buying across days?
  const consLabel = (c: number) => {
    if (c >= 0.7) return { label: "High", color: "text-emerald-400" };
    if (c >= 0.4) return { label: "Med",  color: "text-yellow-400"  };
    return              { label: "Low",   color: "text-orange-400"  };
  };

  const tierColor: Record<string, string> = {
    nano:  "text-red-400",
    micro: "text-violet-400",
    small: "text-blue-400",
    mid:   "text-cyan-400",
  };

  const filtered = (data?.results ?? []).filter(r => {
    if (r.streak < minStreak) return false;
    if (instOnly && r.consistency < 0.3) return false;   // spike buyers filtered out
    return true;
  });

  // For proportional dot heights: find max absolute flow in the visible set
  const maxAbsFlow = (days: NetFlowDayDot[]) =>
    Math.max(...days.map(d => Math.abs(d.net_m)), 0.001);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h2 className="text-white font-bold text-lg">📈 Accumulation Streak</h2>
            <p className="text-slate-400 text-sm mt-1">
              Stocks with <span className="text-emerald-400 font-bold">consecutive days</span> of net buying across <span className="text-white font-bold">up to 60 days</span> — detect 1-week, 2-week, and 3-week institutional accumulation.
            </p>
          </div>
          <div className="flex flex-col gap-2 shrink-0">
            <button
              onClick={run}
              disabled={loading}
              className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-4 py-2.5 rounded-lg text-sm font-bold transition-colors flex items-center gap-2"
            >
              {loading ? <><Spinner /> Scanning…</> : "🔄 Run Scan"}
            </button>
            <button
              onClick={() => runAI(filtered.length ? filtered : (data?.results ?? []))}
              disabled={aiLoading || !data?.results?.length}
              title="Flow Intelligence — AI analysis of multi-week accumulation patterns. Separate from daily AI Options Signals."
              className="bg-violet-800 hover:bg-violet-700 disabled:opacity-40 text-white px-4 py-2 rounded-lg text-xs font-bold transition-colors flex items-center gap-2 border border-violet-700/60"
            >
              {aiLoading ? <><Spinner /> Analyzing…</> : "🔬 Flow Intelligence"}
            </button>
          </div>
        </div>
        {lastRun && (
          <p className="text-slate-600 text-xs">
            Scanned {data?.scanned ?? 473} stocks · {lastRun.toLocaleTimeString()} · {filtered.length} conviction plays
          </p>
        )}
        {error && <p className="text-red-400 text-sm mt-2">{error}</p>}
      </div>

      {/* ── Flow Intelligence Panel ─────────────────────────────────────────── */}
      {(aiSignals || aiLoading || aiError) && (
        <div className="bg-[#0f0a1e] border border-violet-900/60 rounded-xl p-5 shadow-lg shadow-violet-950/30">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-violet-400 text-lg">🔬</span>
                <span className="text-white font-bold">Flow Intelligence</span>
                <span className="text-violet-600 text-xs border border-violet-800 rounded px-1.5 py-0.5 font-mono">STREAK ANALYSIS</span>
              </div>
              {aiSignals && (
                <p className="text-violet-700 text-xs mt-0.5">
                  {aiSignals.analyzed} accumulation patterns analyzed · {aiSignals.model} · separate from daily options signals
                </p>
              )}
            </div>
            {aiSignals && (
              <button onClick={() => setAiSignals(null)} className="text-slate-700 hover:text-slate-400 text-xs">✕</button>
            )}
          </div>

          {aiLoading && (
            <div className="text-center py-10 text-slate-500">
              <Spinner />
              <div className="mt-3 text-sm text-violet-400">Analyzing {filtered.length || data?.results?.length || 0} accumulation patterns…</div>
              <div className="text-xs mt-1 text-violet-900">Detecting stealth accumulation · 1-week through 3-week streaks</div>
            </div>
          )}

          {aiError && <p className="text-red-400 text-sm">{aiError}</p>}

          {aiSignals && !aiLoading && (() => {
            const counts = {
              CONVICTION: aiSignals.signals.filter(s => s.signal === "CONVICTION").length,
              BUILDING:   aiSignals.signals.filter(s => s.signal === "BUILDING").length,
              WATCH:      aiSignals.signals.filter(s => s.signal === "WATCH").length,
              NOISE:      aiSignals.signals.filter(s => s.signal === "NOISE").length,
            };
            return (
              <div className="space-y-3">
                {/* Summary row */}
                <div className="flex flex-wrap gap-3 pb-3 border-b border-violet-900/40 text-xs font-bold">
                  {counts.CONVICTION > 0 && <span className="text-purple-300">🏦 CONVICTION ×{counts.CONVICTION}</span>}
                  {counts.BUILDING   > 0 && <span className="text-amber-300">🔥 BUILDING ×{counts.BUILDING}</span>}
                  {counts.WATCH      > 0 && <span className="text-blue-300">👁 WATCH ×{counts.WATCH}</span>}
                  {counts.NOISE      > 0 && <span className="text-slate-600">📉 NOISE ×{counts.NOISE}</span>}
                </div>

                {/* Signal cards */}
                {aiSignals.signals.map(s => {
                  const cfg = {
                    CONVICTION: { border: "border-purple-700/50", bg: "bg-purple-950/30", icon: "🏦", color: "text-purple-300", bar: "bg-purple-500" },
                    BUILDING:   { border: "border-amber-700/50",  bg: "bg-amber-950/20",  icon: "🔥", color: "text-amber-300",  bar: "bg-amber-500"  },
                    WATCH:      { border: "border-blue-700/50",   bg: "bg-blue-950/20",   icon: "👁", color: "text-blue-300",   bar: "bg-blue-500"   },
                    NOISE:      { border: "border-slate-800",     bg: "bg-slate-900/50",  icon: "📉", color: "text-slate-600",  bar: "bg-slate-600"  },
                  }[s.signal] ?? { border: "border-slate-800", bg: "bg-slate-900", icon: "📊", color: "text-slate-400", bar: "bg-slate-500" };

                  return (
                    <div
                      key={s.ticker}
                      className={`rounded-lg border p-3 cursor-pointer hover:brightness-110 transition-all ${cfg.border} ${cfg.bg}`}
                      onClick={() => onSelectTicker(s.ticker)}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center gap-2">
                          <span className="text-white font-bold text-sm">{s.ticker}</span>
                          <span className={`text-xs font-bold ${cfg.color}`}>{cfg.icon} {s.signal}</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <div className="h-1 w-14 bg-slate-800 rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${cfg.bar}`} style={{ width: `${s.confidence}%` }} />
                          </div>
                          <span className="text-slate-500 text-xs">{s.confidence}%</span>
                        </div>
                      </div>
                      <p className="text-slate-400 text-xs leading-relaxed">{s.thesis}</p>
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </div>
      )}

      {/* Institutional filter explainer */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-white font-bold text-sm">🏦 Institutional Filter</div>
            <p className="text-slate-500 text-xs mt-0.5">
              Requires <span className="text-white">consistency ≥ 30%</span> — buying evenly distributed across days, not one big spike surrounded by tiny days
            </p>
          </div>
          <button
            onClick={() => setInstOnly(v => !v)}
            className={`shrink-0 px-4 py-2 rounded-lg text-xs font-bold border transition-all ${instOnly ? "bg-emerald-600 border-emerald-500 text-white" : "border-slate-700 text-slate-500"}`}
          >
            {instOnly ? "ON" : "OFF"}
          </button>
        </div>
        {!instOnly && (
          <div className="text-xs text-orange-400 border border-orange-800/40 bg-orange-950/20 rounded-lg px-3 py-2">
            ⚠️ Institutional filter OFF — results may include retail-driven spikes
          </div>
        )}
      </div>

      {/* Streak length filter */}
      <div className="flex gap-2">
        {([3, 5, 10, 15] as const).map((n, i) => (
          <button
            key={n}
            onClick={() => setMinStreak(n)}
            className={`flex-1 py-2 rounded-lg text-xs font-bold border transition-colors ${minStreak === n ? "bg-emerald-600 border-emerald-500 text-white" : "border-slate-700 text-slate-500 hover:text-slate-300"}`}
          >
            {["3+ days", "1 week+", "2 weeks+", "3 weeks+"][i]}
          </button>
        ))}
      </div>

      {/* Cold state */}
      {!loading && !lastRun && !error && (
        <div className="text-center py-20 text-slate-500">
          <div className="text-5xl mb-4">📈</div>
          <div className="font-semibold text-slate-400 mb-1">Find institutional accumulation patterns</div>
          <div className="text-sm">Consistent multi-day buying — not one-day retail spikes</div>
        </div>
      )}

      {/* Loading */}
      {loading && !lastRun && (
        <div className="text-center py-16 text-slate-500">
          <Spinner />
          <div className="mt-4 text-sm">Fetching up to 60 days of history for 473+ stocks…</div>
          <div className="text-xs mt-1 text-slate-600">First load takes 90–120 seconds — detecting 1-week, 2-week, and 3-week streaks</div>
        </div>
      )}

      {/* Results */}
      {lastRun && (
        <>
          {filtered.length === 0 && (
            <div className="text-center py-10 text-slate-600 text-sm space-y-2">
              <div>No conviction plays found with current filters</div>
              <div className="text-xs">Try lowering the streak minimum or turning off the institutional filter</div>
            </div>
          )}

          <div className="space-y-3">
            {filtered.map(row => {
              const badge    = streakBadge(row.streak);
              const cons     = consLabel(row.consistency);
              const isSaved  = saved[row.ticker];
              const isBig    = (row.total_pct_mktcap ?? 0) >= 3;
              const maxFlow  = maxAbsFlow(row.days);

              return (
                <div
                  key={row.ticker}
                  onClick={() => onSelectTicker(row.ticker)}
                  className={`bg-slate-900 border rounded-xl p-4 cursor-pointer transition-all hover:border-slate-600 ${isBig ? "border-emerald-700/50" : "border-slate-800"}`}
                >
                  {/* Top row */}
                  <div className="flex items-start justify-between gap-2 mb-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-slate-500 text-xs font-bold">#{row.rank}</span>
                      <span className="text-white font-black text-lg">{row.ticker}</span>
                      <span className="text-slate-400 text-sm">${row.price.toLocaleString()}</span>
                      <span className={`text-xs font-medium ${tierColor[row.cap_tier] ?? "text-slate-400"}`}>
                        {row.cap_tier}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-bold ${badge.color}`}>
                        {badge.icon} {badge.label}
                      </span>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-emerald-400 font-black text-base">+{fmtNet(row.total_net_m)}</div>
                      <div className="text-slate-500 text-xs">{row.streak}d cumulative</div>
                    </div>
                  </div>

                  {/* Day flow bars — proportional height, oldest left → today right */}
                  <div className="mb-3">
                    <div className="flex items-end gap-0.5 h-8 mb-1">
                      {row.days.map((d, i) => {
                        const pct = Math.abs(d.net_m) / maxFlow;
                        const h   = Math.max(Math.round(pct * 100), 8);
                        return (
                          <div
                            key={i}
                            title={`${d.date}: ${d.net_m > 0 ? "+" : ""}${d.net_m.toFixed(2)}M`}
                            style={{ height: `${h}%` }}
                            className={`flex-1 rounded-sm ${d.positive ? "bg-emerald-500" : "bg-red-800/70"}`}
                          />
                        );
                      })}
                    </div>
                    <div className="flex justify-between text-slate-600 text-xs">
                      <span>← 60 days ago</span>
                      <span>today →</span>
                    </div>
                  </div>

                  {/* Consistency + stats */}
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    <div className="bg-slate-800/60 rounded-lg p-2 text-center">
                      <div className={`font-bold text-xs ${cons.color}`}>{cons.label}</div>
                      <div className="text-slate-600 text-xs">Consistency</div>
                      {/* Mini bar */}
                      <div className="mt-1 h-1 bg-slate-700 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full ${row.consistency >= 0.7 ? "bg-emerald-500" : row.consistency >= 0.4 ? "bg-yellow-500" : "bg-orange-500"}`}
                          style={{ width: `${Math.min(row.consistency * 100, 100)}%` }} />
                      </div>
                    </div>
                    <div className="bg-slate-800/60 rounded-lg p-2 text-center">
                      <div className="text-white font-bold text-xs">{fmtNet(row.avg_daily_net_m)}</div>
                      <div className="text-slate-600 text-xs">Avg/day</div>
                    </div>
                    <div className="bg-slate-800/60 rounded-lg p-2 text-center">
                      <div className="text-slate-300 font-bold text-xs">{fmtMktcap(row.market_cap_m)}</div>
                      <div className="text-slate-600 text-xs">Mkt cap</div>
                    </div>
                  </div>

                  {/* % of mktcap accumulated */}
                  {row.total_pct_mktcap !== null && (
                    <div className={`text-xs font-bold mb-3 ${isBig ? "text-emerald-400" : "text-slate-500"}`}>
                      {row.total_pct_mktcap.toFixed(2)}% of market cap accumulated over {row.streak} days
                      {row.avg_pct_per_day !== null && (
                        <span className="text-slate-600 font-normal"> · {row.avg_pct_per_day.toFixed(3)}%/day avg</span>
                      )}
                    </div>
                  )}

                  <button
                    onClick={e => handleSave(e, row)}
                    className={`w-full py-1.5 rounded-lg text-xs font-bold transition-all border ${isSaved ? "bg-emerald-900/40 border-emerald-600 text-emerald-300" : "border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300"}`}
                  >
                    {isSaved ? "✓ SAVED" : "📌 Save to Watchlist"}
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}


// ---- Main Dashboard ------------------------------------------------------

export default function Dashboard() {
  const [ticker, setTicker]         = useState("AAPL");
  const [inputTicker, setInputTicker] = useState("AAPL");
  const [scanTickers, setScanTickers] = useState(DEFAULT_SCAN.join(", "));
  const [tab, setTab]               = useState<"overview"|"lookup"|"scanner"|"analytics"|"backtest"|"alerts"|"portfolio"|"propdesk"|"bullflow"|"smartmoney"|"congress"|"market"|"squeeze"|"insiders"|"breakout"|"morningbrief"|"convergence"|"premarket"|"darkpool"|"putintent"|"volcrush"|"callintent"|"smartvretail"|"maxpain"|"gammawall"|"aitrades"|"signalboard"|"composite"|"outcomes"|"trackrecord"|"whale"|"whalelog"|"watchlist"|"unusualcalls"|"unusualcallslog"|"mytrades"|"aishortcalls"|"netflow"|"micronetflow"|"midnetflow"|"streakflow">("lookup");
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
  const [catalystResult, setCatalystResult] = useState<{explanation: string; ticker: string} | null>(null);
  const [catalystLoading, setCatalystLoading] = useState(false);
  const [catalystTicker, setCatalystTicker] = useState<string | null>(null);
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

  const selectTicker = useCallback((t: string) => {
    const sym = t.trim().toUpperCase();
    if (!sym) return;
    setTicker(sym);
    setInputTicker(sym);
    setAiText(null);
    setAiError(null);
    setAiTicker(null);
    setTab("lookup");
    qc.invalidateQueries({ queryKey: ["stock", sym] });
  }, [qc]);

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
    { id: "overview",     label: "OVERVIEW" },
    { id: "aitrades",     label: "🤖 AI TRADES" },
    { id: "signalboard",  label: "📡 SIGNAL FEED" },
    { id: "composite",    label: "🎯 SCORE BOARD" },
    { id: "morningbrief", label: "🌅 MORNING BRIEF" },
    { id: "convergence",  label: "⚡ CONVERGENCE" },
    { id: "darkpool",     label: "🌑 DARK POOL" },
    { id: "putintent",    label: "🎯 PUT INTENT" },
    { id: "callintent",   label: "🔵 CALL INTENT" },
    { id: "volcrush",     label: "🌡️ VOL CRUSH" },
    { id: "smartvretail", label: "⚔️ SMART vs RETAIL" },
    { id: "maxpain",      label: "📍 MAX PAIN" },
    { id: "gammawall",    label: "🧲 GAMMA WALL" },
    { id: "premarket",    label: "PRE-MARKET" },
    { id: "bullflow",     label: "BULL FLOW" },
    { id: "smartmoney",   label: "SMART MONEY" },
    { id: "congress",     label: "CONGRESS" },
    { id: "lookup",       label: "STOCK LOOKUP" },
    { id: "scanner",      label: "SCANNER" },
    { id: "outcomes",     label: "OUTCOMES" },
    { id: "analytics",    label: "ANALYTICS" },
    { id: "propdesk",     label: "PROP DESK" },
    { id: "squeeze",      label: "SQUEEZE" },
    { id: "breakout",     label: "BREAKOUT" },
    { id: "insiders",     label: "INSIDERS" },
    { id: "market",       label: "MARKET" },
    { id: "portfolio",    label: "PORTFOLIO" },
    { id: "trackrecord",  label: "📈 AI TRACK RECORD" },
    { id: "whale",        label: "🐋 WHALE ACTIVITY" },
    { id: "whalelog",    label: "📋 WHALE LOG" },
    { id: "watchlist",   label: "📌 MY WATCHLIST" },
    { id: "unusualcalls",    label: "🚨 UNUSUAL CALLS" },
    { id: "unusualcallslog", label: "📋 CALLS LOG" },
    { id: "mytrades",        label: "📈 MY TRADES" },
    { id: "aishortcalls",    label: "⚡ AI SHORT CALLS" },
    { id: "netflow",         label: "💰 NET FLOW" },
    { id: "micronetflow",    label: "🔬 MICRO NET FLOW" },
    { id: "midnetflow",      label: "🏢 MID NET FLOW" },
    { id: "streakflow",      label: "📈 FLOW STREAK" },
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
        .bb-tabs-desktop { display: flex; }
        .bb-tabs-mobile { display: none; }
        @media (max-width: 640px) {
          .bb-quotes { display: none !important; }
          .bb-divider { display: none !important; }
          .bb-clock-date { display: none !important; }
          .bb-tabs-desktop { display: none !important; }
          .bb-tabs-mobile { display: flex !important; }
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

      {/* ── NAV TABS — always dropdown ── */}
      <div style={{ background: "rgba(6,12,20,0.97)", borderBottom: `1px solid rgba(255,255,255,0.12)`, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", padding: "10px 14px", gap: 10 }}>
          <select
            value={tab}
            onChange={e => setTab(e.target.value as typeof tab)}
            style={{
              flex: 1, background: "#0f1e30", color: "#f1f5f9", border: "1px solid rgba(255,255,255,0.25)",
              borderRadius: 10, padding: "10px 16px", fontSize: 14, fontWeight: 700, fontFamily: BB_FONT,
              outline: "none", cursor: "pointer", appearance: "none",
              backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%234ade80' stroke-width='2.5'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
              backgroundRepeat: "no-repeat", backgroundPosition: "right 14px center",
              paddingRight: 40,
            }}
          >
            {TABS.map(t => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
            <span style={{ fontSize: 11, color: BB_LABEL, fontFamily: BB_FONT }}>A/D</span>
            <span style={{ fontSize: 12, color: BB_GREEN, fontFamily: BB_FONT, fontWeight: 700 }}>▲{headerMkt?.advance_decline?.up ?? "—"}</span>
            <span style={{ fontSize: 12, color: BB_RED, fontFamily: BB_FONT, fontWeight: 700 }}>▼{headerMkt?.advance_decline?.down ?? "—"}</span>
          </div>
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      {tab === "overview" ? (
        <OverviewTab onSelectTicker={selectTicker} />
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

                        {/* ── AI CATALYST CARD ── */}
                        {analysis && (
                          <div style={{ marginTop: 14, background: "#0d0d0d", border: "1px solid #1e1e1e", borderRadius: 12, overflow: "hidden", marginBottom: 14 }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: "1px solid #1a1a1a" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <div style={{ width: 28, height: 28, borderRadius: 6, background: "linear-gradient(135deg,#22c55e,#16a34a)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>⚡</div>
                                <div>
                                  <div style={{ color: "#e5e5e5", fontSize: 12, fontWeight: 600, fontFamily: BB_FONT }}>AI Catalyst</div>
                                  <div style={{ color: "#555", fontSize: 10, fontFamily: BB_FONT }}>Why is {analysis.ticker} moving?</div>
                                </div>
                              </div>
                              <button
                                onClick={async () => {
                                  setCatalystLoading(true); setCatalystTicker(analysis.ticker); setCatalystResult(null);
                                  try {
                                    const res = await fetchCatalyst({
                                      ticker: analysis.ticker,
                                      price: analysis.indicators.price ?? 0,
                                      vol_ratio: analysis.indicators.volume_ratio,
                                      score: analysis.score?.score,
                                    });
                                    setCatalystResult(res);
                                  } catch {}
                                  finally { setCatalystLoading(false); }
                                }}
                                disabled={catalystLoading}
                                style={{ background: catalystLoading ? "transparent" : "#1a1a1a", border: "1px solid #2a2a2a", color: catalystLoading ? "#444" : "#4ade80", padding: "5px 12px", borderRadius: 6, fontFamily: BB_FONT, fontSize: 11, fontWeight: 600, cursor: catalystLoading ? "default" : "pointer", display: "flex", alignItems: "center", gap: 5 }}>
                                {catalystLoading ? <><Spinner /><span>Analyzing…</span></> : <span>Ask Claude</span>}
                              </button>
                            </div>
                            <div style={{ padding: "16px 18px", minHeight: 60 }}>
                              {catalystLoading && (
                                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                                  {[0,1,2].map(i => <div key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", opacity: 0.7, animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />)}
                                </div>
                              )}
                              {!catalystLoading && catalystResult && catalystTicker === analysis.ticker && (
                                <ClaudeMarkdown text={catalystResult.explanation} />
                              )}
                              {!catalystLoading && !catalystResult && (
                                <div style={{ color: "#444", fontSize: 13, fontFamily: BB_FONT, lineHeight: 1.6 }}>
                                  Click <span style={{ color: "#4ade80", fontWeight: 600 }}>Ask Claude</span> to get an AI analysis of why <strong style={{ color: "#777" }}>{analysis.ticker}</strong> is moving and what catalysts to watch.
                                </div>
                              )}
                            </div>
                            {catalystResult && catalystTicker === analysis.ticker && (
                              <div style={{ padding: "8px 18px 12px", borderTop: "1px solid #161616" }}>
                                <span style={{ color: "#333", fontSize: 10, fontFamily: BB_FONT }}>Powered by Claude · For informational purposes only</span>
                              </div>
                            )}
                          </div>
                        )}

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
            <DailyTop10Banner onSelect={selectTicker} />
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
                <ScanTable results={scanData.results.filter(r => !r.error)} onSelect={selectTicker} />
              </div>
            )}
            {!scanData && !loadingScan && <div className="text-center py-16 text-slate-500">Click "Scan" to analyze the tickers above</div>}
          </div>
        )}

        {tab === "analytics" && (
          <div className="space-y-4">
            <DailyTop10Banner onSelect={selectTicker} />
            <AnalyticsTab />
          </div>
        )}
        {tab === "backtest"  && <BacktestTab />}
        {tab === "alerts"    && <AlertsTab />}
        {tab === "propdesk"   && <PropDeskTab />}
        {tab === "smartmoney" && <SmartMoneyTab />}
        {tab === "congress"   && <CongressTab />}
        {tab === "market"     && <MarketTab />}
        {tab === "squeeze"    && <SqueezeTab onSelectTicker={selectTicker} />}
        {tab === "insiders"   && <InsidersTab />}
        {tab === "breakout"   && <BreakoutTab onSelectTicker={selectTicker} />}

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
          <BullFlowTab onSelectTicker={selectTicker} />
        )}

        {tab === "outcomes" && <OutcomesTab />}

        {tab === "morningbrief" && <MorningBriefTab />}
        {tab === "convergence" && <ConvergenceTab onSelectTicker={selectTicker} />}
        {tab === "darkpool" && <DarkPoolTab onSelectTicker={selectTicker} />}
        {tab === "aitrades"     && <AITradesTab     onSelectTicker={selectTicker} />}
        {tab === "signalboard"  && <SignalFeedTab   onSelectTicker={selectTicker} />}
        {tab === "composite"    && <CompositeBoardTab onSelectTicker={selectTicker} />}
        {tab === "putintent"    && <PutIntentTab    onSelectTicker={selectTicker} />}
        {tab === "callintent"   && <CallIntentTab   onSelectTicker={selectTicker} />}
        {tab === "volcrush"     && <VolCrushTab     onSelectTicker={selectTicker} />}
        {tab === "smartvretail" && <SmartVsRetailTab onSelectTicker={selectTicker} />}
        {tab === "maxpain"      && <MaxPainTab      onSelectTicker={selectTicker} />}
        {tab === "gammawall"    && <GammaWallTab    onSelectTicker={selectTicker} />}
        {tab === "premarket"    && <PremarketTab    onSelectTicker={selectTicker} />}
        {tab === "trackrecord"  && <TrackRecordTab />}

        {tab === "whale"    && <WhaleActivityTab />}
        {tab === "whalelog" && <WhaleLogTab />}
        {tab === "watchlist" && <TradeWatchlistTab />}
        {tab === "unusualcalls"    && <UnusualCallsTab    onSelectTicker={selectTicker} />}
        {tab === "unusualcallslog" && <UnusualCallsLogTab onSelectTicker={selectTicker} />}
        {tab === "mytrades"        && <MyTradesTab />}
        {tab === "aishortcalls"    && <AIShortCallsTab />}
        {tab === "netflow"         && <NetFlowTab onSelectTicker={selectTicker} />}
        {tab === "micronetflow"    && <NetFlowMicrocapTab onSelectTicker={selectTicker} />}
        {tab === "midnetflow"      && <NetFlowMidcapTab  onSelectTicker={selectTicker} />}
        {tab === "streakflow"      && <NetFlowStreakTab  onSelectTicker={selectTicker} />}

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
