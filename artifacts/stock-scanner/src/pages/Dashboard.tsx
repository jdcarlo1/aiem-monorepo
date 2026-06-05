import React, { useState, useCallback, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  analyzeStock, scanStocks, fetchPortfolio, buyStock, sellStock,
  runBacktest, runHistoricalAnalytics, fetchAlerts, createAlert, deleteAlert,
  propScan, propTrade, propReset, smartMoneyScan,
  StockAnalysis, ScanResult, BacktestResult, AnalyticsResult, Alert,
  PropSignal, PropPosition, PropTrade, PropDeskResult, SmartMoneySignal, SmartMoneyResult,
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
                              <div>
                                <h4 className="text-white text-sm font-semibold mb-3">📊 Score Breakdown <span className="text-slate-500 font-normal">(out of 100)</span></h4>
                                <div className="space-y-2.5">
                                  <SmScoreBar label="Call Sweep Proxy"     value={s.score_breakdown.call_sweep}       max={25} color="#a855f7" />
                                  <SmScoreBar label="Volume / OI"          value={s.score_breakdown.volume_oi}        max={20} color="#06b6d4" />
                                  <SmScoreBar label="Ask-Side Aggression"  value={s.score_breakdown.ask_aggression}   max={15} color="#10b981" />
                                  <SmScoreBar label="Dark Pool Proxy"      value={s.score_breakdown.dark_pool}        max={15} color="#6366f1" />
                                  <SmScoreBar label="Sector Strength"      value={s.score_breakdown.sector_strength}  max={10} color="#f59e0b" />
                                  <SmScoreBar label="Historical Similarity" value={s.score_breakdown.historical}      max={15} color="#f97316" />
                                </div>
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

// ---- Main Dashboard ------------------------------------------------------

export default function Dashboard() {
  const [ticker, setTicker]         = useState("AAPL");
  const [inputTicker, setInputTicker] = useState("AAPL");
  const [scanTickers, setScanTickers] = useState(DEFAULT_SCAN.join(", "));
  const [tab, setTab]               = useState<"lookup"|"scanner"|"analytics"|"backtest"|"alerts"|"portfolio"|"propdesk">("lookup");
  const [tradeMode, setTradeMode]   = useState<"buy"|"sell">("buy");
  const [tradeShares, setTradeShares] = useState("");
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
    if (t) setTicker(t);
  }, [inputTicker]);

  const handleTrade = useCallback(() => {
    const shares = parseFloat(tradeShares);
    if (!shares || shares <= 0 || !analysis?.indicators.price) return;
    tradeMutation.mutate({ mode: tradeMode, t: analysis.ticker, shares, price: analysis.indicators.price });
  }, [tradeShares, tradeMode, analysis, tradeMutation]);

  const ind   = analysis?.indicators;
  const score = analysis?.score;
  const ml    = analysis?.ml;

  const TABS = [
    { id: "lookup",    label: "Stock Lookup" },
    { id: "scanner",   label: "Scanner" },
    { id: "analytics", label: "Analytics" },
    { id: "backtest",  label: "Backtest" },
    { id: "alerts",    label: "Alerts" },
    { id: "portfolio", label: "Portfolio" },
    { id: "propdesk",   label: "⚡ Prop Desk" },
    { id: "smartmoney", label: "🏆 Smart Money" },
  ] as const;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 shrink-0">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center font-bold text-sm">S</div>
            <h1 className="text-lg font-bold text-white hidden sm:block">StockScanner AI</h1>
          </div>
          <nav className="flex gap-1 flex-wrap">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${tab === t.id ? "bg-blue-600 text-white" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"}`}>
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">

        {/* --- Stock Lookup --- */}
        {tab === "lookup" && (
          <div className="space-y-6">
            <div className="flex gap-2">
              <input value={inputTicker} onChange={e => setInputTicker(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === "Enter" && handleLookup()}
                placeholder="Enter ticker (e.g. AAPL)"
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 uppercase" />
              <button onClick={handleLookup} className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2.5 rounded-lg font-medium transition-colors">Analyze</button>
            </div>

            {loadingAnalysis && <div className="flex items-center justify-center py-16 gap-3 text-slate-400"><Spinner /> Fetching data & running analysis…</div>}
            {analysisError  && <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300">{analysisError instanceof Error ? analysisError.message : "Failed to analyze"}</div>}

            {analysis && !loadingAnalysis && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {/* Price card */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-1">{analysis.info.name || analysis.ticker}</div>
                    <div className="flex items-end gap-3 mb-3">
                      <span className="text-4xl font-bold text-white">${fmt(ind?.price)}</span>
                      <span className={`text-lg font-medium mb-0.5 ${(ind?.price_change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{(ind?.price_change_pct ?? 0) >= 0 ? "+" : ""}{fmt(ind?.price_change_pct)}%</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div><span className="text-slate-500">Sector</span><div className="text-slate-300 truncate">{analysis.info.sector || "—"}</div></div>
                      <div><span className="text-slate-500">Mkt Cap</span><div className="text-slate-300">{fmtMktCap(analysis.info.market_cap)}</div></div>
                      <div><span className="text-slate-500">P/E</span><div className="text-slate-300">{fmt(analysis.info.pe_ratio)}</div></div>
                      <div><span className="text-slate-500">Beta</span><div className="text-slate-300">{fmt(analysis.info.beta)}</div></div>
                      <div><span className="text-slate-500">52w High</span><div className="text-slate-300">${fmt(ind?.high_52w)}</div></div>
                      <div><span className="text-slate-500">52w Low</span><div className="text-slate-300">${fmt(ind?.low_52w)}</div></div>
                    </div>
                  </div>

                  {/* Score + ML card */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-3">Composite Score &amp; ML Probability</div>
                    <div className="flex items-center gap-4 mb-4">
                      {score && <ScoreBadge score={score.score} rating={score.rating} />}
                      {ml    && <DirectionBadge direction={ml.direction} confidence={ml.confidence} probUp={ml.probability_up} />}
                    </div>
                    {ml?.model_accuracy && <div className="text-xs text-slate-500 mb-3">Model accuracy: {ml.model_accuracy.toFixed(1)}%</div>}
                    {score && <ScoreBreakdown breakdown={score.breakdown} />}
                  </div>

                  {/* Indicators card */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-4">Technical Indicators</div>
                    {ind?.rsi != null && <RsiGauge rsi={ind.rsi} />}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm pt-3 mt-3 border-t border-slate-800">
                      <div><span className="text-slate-500">MACD</span><div className={`font-medium ${(ind?.macd ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{fmt(ind?.macd, 3)}</div></div>
                      <div><span className="text-slate-500">Signal</span><div className="text-slate-300">{fmt(ind?.macd_signal, 3)}</div></div>
                      <div><span className="text-slate-500">SMA 50</span><div className="text-slate-300">${fmt(ind?.sma50)}</div></div>
                      <div><span className="text-slate-500">SMA 200</span><div className="text-slate-300">${fmt(ind?.sma200)}</div></div>
                      <div><span className="text-slate-500">BB Upper</span><div className="text-slate-300">${fmt(ind?.bb_upper)}</div></div>
                      <div><span className="text-slate-500">BB Lower</span><div className="text-slate-300">${fmt(ind?.bb_lower)}</div></div>
                      <div><span className="text-slate-500">Vol Ratio</span><div className={`font-medium ${(ind?.volume_ratio ?? 1) >= 1.5 ? "text-yellow-400" : "text-slate-300"}`}>{fmt(ind?.volume_ratio, 1)}x</div></div>
                      <div><span className="text-slate-500">ATR</span><div className="text-slate-300">{fmt(ind?.atr)}</div></div>
                    </div>
                  </div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-4">Price History (90 days)</div>
                  <PriceChart history={analysis.history} />
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-3">Paper Trade</div>
                  <div className="flex items-center gap-3 flex-wrap">
                    <div className="flex bg-slate-800 rounded-lg p-1">
                      {(["buy","sell"] as const).map(m => (
                        <button key={m} onClick={() => setTradeMode(m)} className={`px-4 py-1.5 rounded text-sm font-medium capitalize transition-colors ${tradeMode === m ? m === "buy" ? "bg-emerald-600 text-white" : "bg-red-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>{m}</button>
                      ))}
                    </div>
                    <input type="number" value={tradeShares} onChange={e => setTradeShares(e.target.value)} placeholder="Shares" className="w-28 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
                    <div className="text-slate-400 text-sm">@ ${fmt(ind?.price)} = <span className="text-white font-medium">${tradeShares && ind?.price ? fmt(parseFloat(tradeShares) * ind.price, 2) : "—"}</span></div>
                    <button onClick={handleTrade} disabled={tradeMutation.isPending} className={`px-5 py-2 rounded-lg text-sm font-medium transition-colors ${tradeMode === "buy" ? "bg-emerald-600 hover:bg-emerald-500 text-white" : "bg-red-600 hover:bg-red-500 text-white"} disabled:opacity-50`}>
                      {tradeMutation.isPending ? "…" : `${tradeMode === "buy" ? "Buy" : "Sell"} ${analysis.ticker}`}
                    </button>
                    {tradeMutation.data?.message && <span className="text-emerald-400 text-sm">{tradeMutation.data.message}</span>}
                    {tradeMutation.data?.error   && <span className="text-red-400 text-sm">{tradeMutation.data.error}</span>}
                  </div>
                </div>
              </div>
            )}
            {!analysis && !loadingAnalysis && !analysisError && <div className="text-center py-16 text-slate-500">Enter a ticker symbol above to get started</div>}
          </div>
        )}

        {/* --- Scanner --- */}
        {tab === "scanner" && (
          <div className="space-y-4">
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

        {tab === "analytics" && <AnalyticsTab />}
        {tab === "backtest"  && <BacktestTab />}
        {tab === "alerts"    && <AlertsTab />}
        {tab === "propdesk"   && <PropDeskTab />}
        {tab === "smartmoney" && <SmartMoneyTab />}

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
      </main>
    </div>
  );
}
