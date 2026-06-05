import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  analyzeStock,
  scanStocks,
  fetchPortfolio,
  buyStock,
  sellStock,
  StockAnalysis,
  ScanResult,
} from "@/lib/api";
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

const DEFAULT_SCAN = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META", "JPM", "V", "SPY"];

function fmt(n?: number | null, decimals = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtMktCap(n?: number | null): string {
  if (!n) return "—";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString()}`;
}

function ScoreBadge({ score, rating }: { score: number; rating: string }) {
  const color =
    score >= 8 ? "text-emerald-400 border-emerald-500" :
    score >= 6.5 ? "text-green-400 border-green-500" :
    score >= 5 ? "text-yellow-400 border-yellow-500" :
    score >= 3 ? "text-orange-400 border-orange-500" :
    "text-red-400 border-red-500";
  return (
    <div className={`inline-flex flex-col items-center border rounded-lg px-3 py-1 ${color}`}>
      <span className="text-2xl font-bold">{score.toFixed(1)}</span>
      <span className="text-xs font-medium">{rating}</span>
    </div>
  );
}

function DirectionBadge({ direction, confidence, probUp }: { direction: string; confidence: string; probUp: number }) {
  const color =
    direction === "Up" ? "bg-emerald-900/50 text-emerald-300 border-emerald-700" :
    direction === "Down" ? "bg-red-900/50 text-red-300 border-red-700" :
    "bg-slate-700 text-slate-300 border-slate-600";
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
  const pct = Math.min(100, Math.max(0, rsi));
  const color = rsi < 30 ? "#ef4444" : rsi > 70 ? "#f97316" : "#10b981";
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="text-xs text-slate-400">RSI</div>
      <div className="relative w-16 h-2 bg-slate-700 rounded-full overflow-hidden">
        <div style={{ width: `${pct}%`, background: color }} className="h-full rounded-full transition-all" />
      </div>
      <div style={{ color }} className="text-sm font-bold">{fmt(rsi, 1)}</div>
      <div className="text-xs text-slate-500">
        {rsi < 30 ? "Oversold" : rsi > 70 ? "Overbought" : "Neutral"}
      </div>
    </div>
  );
}

function PriceChart({ history }: { history: StockAnalysis["history"] }) {
  if (!history || history.length === 0) return <div className="text-slate-500 text-sm text-center py-8">No price data</div>;
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
          <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} width={55}
            tickFormatter={v => `$${v.toFixed(0)}`} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
            labelStyle={{ color: "#94a3b8" }}
            itemStyle={{ color: "#60a5fa" }}
            formatter={(v: number) => [`$${v.toFixed(2)}`, "Price"]}
          />
          <Area type="monotone" dataKey="close" stroke="#3b82f6" fill="url(#priceGrad)" strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <ResponsiveContainer width="100%" height={60}>
        <BarChart data={data}>
          <Bar dataKey="volume" fill="#334155" radius={[2, 2, 0, 0]} />
          <XAxis dataKey="date" hide />
          <YAxis hide />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
            formatter={(v: number) => [v.toLocaleString(), "Volume"]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScoreBreakdown({ breakdown }: { breakdown: StockAnalysis["score"]["breakdown"] }) {
  return (
    <div className="space-y-2">
      {breakdown.map((item) => (
        <div key={item.factor} className="flex items-center gap-3">
          <div className="w-28 text-xs text-slate-400 shrink-0">{item.factor}</div>
          <div className="flex-1 bg-slate-800 rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-500 transition-all"
              style={{ width: `${(item.points / item.max) * 100}%` }}
            />
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
            <th className="text-right py-2 px-3">Vol Ratio</th>
            <th className="text-right py-2 px-3">Score</th>
            <th className="text-right py-2 px-3">ML</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr
              key={r.ticker}
              onClick={() => onSelect(r.ticker)}
              className="border-b border-slate-800/50 hover:bg-slate-800/50 cursor-pointer transition-colors"
            >
              <td className="py-2.5 px-3">
                <div className="font-semibold text-white">{r.ticker}</div>
                <div className="text-xs text-slate-500 truncate max-w-[120px]">{r.name}</div>
              </td>
              <td className="text-right py-2.5 px-3 text-slate-200">${fmt(r.price)}</td>
              <td className={`text-right py-2.5 px-3 font-medium ${(r.price_change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {r.price_change_pct != null ? `${r.price_change_pct >= 0 ? "+" : ""}${fmt(r.price_change_pct)}%` : "—"}
              </td>
              <td className={`text-right py-2.5 px-3 ${
                (r.rsi ?? 50) < 30 ? "text-red-400" : (r.rsi ?? 50) > 70 ? "text-orange-400" : "text-slate-300"
              }`}>
                {fmt(r.rsi, 1)}
              </td>
              <td className={`text-right py-2.5 px-3 ${(r.volume_ratio ?? 1) >= 1.5 ? "text-yellow-400" : "text-slate-400"}`}>
                {r.volume_ratio != null ? `${fmt(r.volume_ratio, 1)}x` : "—"}
              </td>
              <td className="text-right py-2.5 px-3">
                {r.score != null && (
                  <span className={`font-bold ${
                    r.score >= 8 ? "text-emerald-400" : r.score >= 6 ? "text-green-400" :
                    r.score >= 5 ? "text-yellow-400" : "text-red-400"
                  }`}>
                    {r.score.toFixed(1)}
                  </span>
                )}
              </td>
              <td className="text-right py-2.5 px-3">
                {r.direction && (
                  <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                    r.direction === "Up" ? "bg-emerald-900/60 text-emerald-300" :
                    r.direction === "Down" ? "bg-red-900/60 text-red-300" :
                    "bg-slate-700 text-slate-400"
                  }`}>
                    {r.direction === "Up" ? "↑" : r.direction === "Down" ? "↓" : "→"} {r.prob_up?.toFixed(0)}%
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Dashboard() {
  const [ticker, setTicker] = useState("AAPL");
  const [inputTicker, setInputTicker] = useState("AAPL");
  const [scanTickers, setScanTickers] = useState(DEFAULT_SCAN.join(", "));
  const [tab, setTab] = useState<"lookup" | "scanner" | "portfolio">("lookup");
  const [tradeMode, setTradeMode] = useState<"buy" | "sell">("buy");
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

  const { data: portfolio, isLoading: loadingPortfolio, refetch: refreshPortfolio } = useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    enabled: tab === "portfolio",
  });

  const tradeMutation = useMutation({
    mutationFn: ({ mode, t, shares, price }: { mode: "buy" | "sell"; t: string; shares: number; price: number }) =>
      mode === "buy" ? buyStock(t, shares, price) : sellStock(t, shares, price),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      setTradeShares("");
    },
  });

  const handleLookup = useCallback(() => {
    const t = inputTicker.trim().toUpperCase();
    if (t) setTicker(t);
  }, [inputTicker]);

  const handleTrade = useCallback(() => {
    const shares = parseFloat(tradeShares);
    if (!shares || shares <= 0 || !analysis?.indicators.price) return;
    tradeMutation.mutate({
      mode: tradeMode,
      t: analysis.ticker,
      shares,
      price: analysis.indicators.price,
    });
  }, [tradeShares, tradeMode, analysis, tradeMutation]);

  const ind = analysis?.indicators;
  const score = analysis?.score;
  const ml = analysis?.ml;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center font-bold text-sm">S</div>
            <h1 className="text-lg font-bold text-white">StockScanner AI</h1>
          </div>
          <nav className="flex gap-1">
            {(["lookup", "scanner", "portfolio"] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors capitalize ${
                  tab === t ? "bg-blue-600 text-white" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
                }`}
              >
                {t === "lookup" ? "Stock Lookup" : t === "scanner" ? "Scanner" : "Portfolio"}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">

        {/* STOCK LOOKUP TAB */}
        {tab === "lookup" && (
          <div className="space-y-6">
            {/* Search */}
            <div className="flex gap-2">
              <input
                value={inputTicker}
                onChange={e => setInputTicker(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === "Enter" && handleLookup()}
                placeholder="Enter ticker (e.g. AAPL)"
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 uppercase"
              />
              <button
                onClick={handleLookup}
                className="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2.5 rounded-lg font-medium transition-colors"
              >
                Analyze
              </button>
            </div>

            {loadingAnalysis && (
              <div className="flex items-center justify-center py-16 gap-3 text-slate-400">
                <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                Fetching data & running analysis…
              </div>
            )}

            {analysisError && (
              <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300">
                {analysisError instanceof Error ? analysisError.message : "Failed to analyze — make sure the Python backend is running."}
              </div>
            )}

            {analysis && !loadingAnalysis && (
              <div className="space-y-4">
                {/* Top row */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {/* Price card */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-1">{analysis.info.name || analysis.ticker}</div>
                    <div className="flex items-end gap-3 mb-3">
                      <span className="text-4xl font-bold text-white">${fmt(ind?.price)}</span>
                      <span className={`text-lg font-medium mb-0.5 ${(ind?.price_change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {(ind?.price_change_pct ?? 0) >= 0 ? "+" : ""}{fmt(ind?.price_change_pct)}%
                      </span>
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

                  {/* Score card */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-3">Composite Score</div>
                    <div className="flex items-center gap-4 mb-4">
                      {score && <ScoreBadge score={score.score} rating={score.rating} />}
                      {ml && <DirectionBadge direction={ml.direction} confidence={ml.confidence} probUp={ml.probability_up} />}
                    </div>
                    {ml?.model_accuracy && (
                      <div className="text-xs text-slate-500 mb-3">Model accuracy: {ml.model_accuracy.toFixed(1)}%</div>
                    )}
                    {score && <ScoreBreakdown breakdown={score.breakdown} />}
                  </div>

                  {/* Indicators */}
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-4">Technical Indicators</div>
                    <div className="space-y-3 text-sm">
                      {ind?.rsi != null && <RsiGauge rsi={ind.rsi} />}
                      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm pt-2 border-t border-slate-800">
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
                </div>

                {/* Price chart */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-4">Price History (90 days)</div>
                  <PriceChart history={analysis.history} />
                </div>

                {/* Paper trade */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-3">Paper Trade</div>
                  <div className="flex items-center gap-3">
                    <div className="flex bg-slate-800 rounded-lg p-1">
                      {(["buy", "sell"] as const).map(m => (
                        <button
                          key={m}
                          onClick={() => setTradeMode(m)}
                          className={`px-4 py-1.5 rounded text-sm font-medium capitalize transition-colors ${
                            tradeMode === m
                              ? m === "buy" ? "bg-emerald-600 text-white" : "bg-red-600 text-white"
                              : "text-slate-400 hover:text-slate-200"
                          }`}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                    <input
                      type="number"
                      value={tradeShares}
                      onChange={e => setTradeShares(e.target.value)}
                      placeholder="Shares"
                      className="w-28 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                    />
                    <div className="text-slate-400 text-sm">
                      @ ${fmt(ind?.price)} = <span className="text-white font-medium">
                        ${tradeShares && ind?.price ? fmt(parseFloat(tradeShares) * ind.price, 2) : "—"}
                      </span>
                    </div>
                    <button
                      onClick={handleTrade}
                      disabled={tradeMutation.isPending}
                      className={`px-5 py-2 rounded-lg text-sm font-medium transition-colors ${
                        tradeMode === "buy"
                          ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                          : "bg-red-600 hover:bg-red-500 text-white"
                      } disabled:opacity-50`}
                    >
                      {tradeMutation.isPending ? "…" : `${tradeMode === "buy" ? "Buy" : "Sell"} ${analysis.ticker}`}
                    </button>
                    {tradeMutation.data?.message && (
                      <span className="text-emerald-400 text-sm">{tradeMutation.data.message}</span>
                    )}
                    {tradeMutation.data?.error && (
                      <span className="text-red-400 text-sm">{tradeMutation.data.error}</span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {!analysis && !loadingAnalysis && !analysisError && (
              <div className="text-center py-16 text-slate-500">
                Enter a ticker symbol above to get started
              </div>
            )}
          </div>
        )}

        {/* SCANNER TAB */}
        {tab === "scanner" && (
          <div className="space-y-4">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <div className="text-slate-400 text-sm mb-3">Tickers to scan (comma-separated, max 20)</div>
              <div className="flex gap-2">
                <input
                  value={scanTickers}
                  onChange={e => setScanTickers(e.target.value.toUpperCase())}
                  className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
                <button
                  onClick={() => runScan()}
                  disabled={loadingScan}
                  className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2"
                >
                  {loadingScan && <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                  Scan
                </button>
              </div>
              <div className="text-xs text-slate-500 mt-2">
                ⚠️ Scanning many tickers may take 1–2 minutes — each stock fetches 2 years of data and runs ML analysis
              </div>
            </div>

            {loadingScan && (
              <div className="flex items-center justify-center py-16 gap-3 text-slate-400">
                <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                Scanning {parsedScanTickers.length} tickers…
              </div>
            )}

            {scanData && !loadingScan && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="text-slate-400 text-sm">{scanData.results.filter(r => !r.error).length} stocks analyzed</div>
                  <div className="text-xs text-slate-500">Click a row to analyze</div>
                </div>
                <ScanTable
                  results={scanData.results.filter(r => !r.error)}
                  onSelect={t => { setTicker(t); setInputTicker(t); setTab("lookup"); }}
                />
              </div>
            )}

            {!scanData && !loadingScan && (
              <div className="text-center py-16 text-slate-500">
                Click "Scan" to analyze the tickers above
              </div>
            )}
          </div>
        )}

        {/* PORTFOLIO TAB */}
        {tab === "portfolio" && (
          <div className="space-y-4">
            {loadingPortfolio && (
              <div className="flex items-center justify-center py-16 gap-3 text-slate-400">
                <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                Loading portfolio…
              </div>
            )}

            {portfolio && (
              <>
                {/* Summary */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {[
                    { label: "Total Value", value: `$${portfolio.total_value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: "text-white" },
                    { label: "Cash", value: `$${portfolio.cash.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: "text-slate-300" },
                    { label: "Positions Value", value: `$${portfolio.positions_value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: "text-slate-300" },
                    { label: "Total P&L", value: `${portfolio.total_pnl >= 0 ? "+" : ""}$${portfolio.total_pnl.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${portfolio.total_pnl_pct >= 0 ? "+" : ""}${portfolio.total_pnl_pct.toFixed(2)}%)`, color: portfolio.total_pnl >= 0 ? "text-emerald-400" : "text-red-400" },
                  ].map(item => (
                    <div key={item.label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
                      <div className="text-slate-500 text-xs mb-1">{item.label}</div>
                      <div className={`text-lg font-bold ${item.color}`}>{item.value}</div>
                    </div>
                  ))}
                </div>

                {/* Positions */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="text-slate-400 text-sm mb-4">Positions</div>
                  {portfolio.positions.length === 0 ? (
                    <div className="text-center py-8 text-slate-500">No open positions. Go to Stock Lookup to paper trade.</div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase">
                            <th className="text-left py-2 px-3">Ticker</th>
                            <th className="text-right py-2 px-3">Shares</th>
                            <th className="text-right py-2 px-3">Avg Cost</th>
                            <th className="text-right py-2 px-3">Current</th>
                            <th className="text-right py-2 px-3">Value</th>
                            <th className="text-right py-2 px-3">P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {portfolio.positions.map(pos => (
                            <tr key={pos.ticker} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                              <td className="py-2.5 px-3 font-semibold text-white">{pos.ticker}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">{pos.shares}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">${fmt(pos.avg_cost)}</td>
                              <td className="text-right py-2.5 px-3 text-slate-300">${fmt(pos.current_price)}</td>
                              <td className="text-right py-2.5 px-3 text-slate-200">${fmt(pos.value)}</td>
                              <td className={`text-right py-2.5 px-3 font-medium ${pos.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                                {pos.pnl >= 0 ? "+" : ""}${fmt(pos.pnl)} ({pos.pnl_pct >= 0 ? "+" : ""}{fmt(pos.pnl_pct)}%)
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* Trade history */}
                {portfolio.trades.length > 0 && (
                  <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                    <div className="text-slate-400 text-sm mb-4">Recent Trades</div>
                    <div className="space-y-2">
                      {[...portfolio.trades].reverse().map((t, i) => (
                        <div key={i} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-800/50">
                          <div className="flex items-center gap-3">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${t.type === "BUY" ? "bg-emerald-900/50 text-emerald-300" : "bg-red-900/50 text-red-300"}`}>
                              {t.type}
                            </span>
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

            {!portfolio && !loadingPortfolio && (
              <div className="text-center py-16 text-slate-500">Portfolio data unavailable — make sure the Python backend is running</div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
