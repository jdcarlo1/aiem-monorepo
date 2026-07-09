const BASE = "/stock-api";

export async function fetchJson<T>(path: string, opts?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...opts, cache: "no-store", signal: controller.signal });
  } catch (e: any) {
    clearTimeout(timer);
    throw new Error(e.name === "AbortError" ? "Request timed out — server is busy, retry in a moment" : (e.message || "Network error"));
  }
  clearTimeout(timer);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export function analyzeStock(ticker: string) {
  return fetchJson<StockAnalysis>(`/stock/analyze?ticker=${encodeURIComponent(ticker)}`);
}

export interface DailyTop10Result {
  top10: ScanResult[];
  date: string;
  total_scanned: number;
}

export function fetchDailyTop10() {
  return fetchJson<DailyTop10Result>("/daily-top10");
}

export function scanStocks(tickers: string[]) {
  return fetchJson<{ results: ScanResult[] }>("/stock/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers }),
  });
}

export function fetchWatchlist() {
  return fetchJson<{ tickers: string[] }>("/stock/watchlist");
}

export function fetchPortfolio() {
  return fetchJson<Portfolio>("/portfolio");
}

export function buyStock(ticker: string, shares: number, price: number) {
  return fetchJson<TradeResult>("/portfolio/buy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, shares, price }),
  });
}

export function sellStock(ticker: string, shares: number, price: number) {
  return fetchJson<TradeResult>("/portfolio/sell", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, shares, price }),
  });
}

export function runBacktest(ticker: string, buyThreshold: number, sellThreshold: number, initialCash: number) {
  return fetchJson<BacktestResult>("/backtest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ticker,
      buy_threshold: buyThreshold,
      sell_threshold: sellThreshold,
      initial_cash: initialCash,
    }),
  });
}

export function runHistoricalAnalytics(tickers: string[]) {
  return fetchJson<AnalyticsResult>("/analytics/historical", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers }),
  });
}

export function fetchAlerts() {
  return fetchJson<{ alerts: Alert[] }>("/alerts");
}

export function createAlert(ticker: string, type: string, value: number, direction: string) {
  return fetchJson<{ success: boolean; alert: Alert }>("/alerts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, type, value, direction }),
  });
}

export function deleteAlert(id: number) {
  return fetchJson<{ success: boolean }>(`/alerts/${id}`, { method: "DELETE" });
}

export function propScan(tickers: string[]) {
  return fetchJson<PropDeskResult>("/prop/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers }),
  });
}

export function propTrade(ticker: string, action: "buy" | "sell") {
  return fetchJson<PropTradeResult>(`/prop/trade/${ticker}/${action}`, { method: "POST" });
}

export function propReset() {
  return fetchJson<{ status: string; cash: number }>("/prop/reset", { method: "POST" });
}

// ---- Types ---------------------------------------------------------------

export interface StockAnalysis {
  ticker: string;
  info: {
    name: string; sector: string; industry: string;
    market_cap?: number; pe_ratio?: number; forward_pe?: number;
    dividend_yield?: number; beta?: number; description?: string;
  };
  indicators: {
    price?: number; price_change?: number; price_change_pct?: number;
    rsi?: number; macd?: number; macd_signal?: number; macd_hist?: number;
    bb_upper?: number; bb_mid?: number; bb_lower?: number;
    sma50?: number; sma200?: number; volume?: number; avg_volume_20?: number;
    volume_ratio?: number; atr?: number; momentum?: number;
    high_52w?: number; low_52w?: number; pct_from_52w_high?: number;
  };
  score: { score: number; rating: string; breakdown: { factor: string; points: number; max: number; label: string; value: number }[] };
  ml: { probability_up: number; probability_down: number; direction: string; confidence: string; model_accuracy?: number };
  history: { date: string; open: number; high: number; low: number; close: number; volume: number }[];
}

export interface ScanResult {
  ticker: string; name: string; sector: string;
  price?: number; price_change_pct?: number; rsi?: number;
  volume_ratio?: number; score?: number; rating?: string;
  direction?: string; prob_up?: number; error?: string;
}

export interface Portfolio {
  cash: number; positions_value: number; total_value: number;
  total_pnl: number; total_pnl_pct: number;
  positions: Position[]; trades: Trade[];
}

export interface Position {
  ticker: string; shares: number; avg_cost: number; current_price: number;
  value: number; cost_basis: number; pnl: number; pnl_pct: number;
}

export interface Trade {
  type: string; ticker: string; shares: number; price: number; total: number; date: string;
}

export interface TradeResult {
  success?: boolean; error?: string; message?: string; cash_remaining?: number;
}

export interface BacktestResult {
  ticker: string; initial_cash: number; final_value: number;
  total_return_pct: number; buy_hold_return_pct: number; alpha: number;
  n_trades: number; win_rate: number; max_drawdown_pct: number;
  buy_threshold: number; sell_threshold: number;
  trades: BacktestTrade[]; equity_curve: EquityPoint[];
}

export interface BacktestTrade {
  type: string; date: string; price: number; shares: number; score: number;
  pnl?: number; pnl_pct?: number;
}

export interface EquityPoint { date: string; value: number; close?: number; in_position?: boolean }

export interface Alert {
  id: number; ticker: string; type: string; value: number; direction: string;
  triggered: boolean; created: string; triggered_at?: string; triggered_value?: number;
}

export interface BucketStat {
  bucket: string; count: number;
  win_rate_1d: number | null; win_rate_3d: number | null; win_rate_5d: number | null;
  avg_ret_1d: number | null; avg_ret_3d: number | null; avg_ret_5d: number | null;
  median_ret_1d: number | null;
}

export interface ThresholdStat {
  threshold: number; count: number;
  win_rate_1d: number; win_rate_3d: number; win_rate_5d: number;
  avg_ret_1d: number; avg_ret_3d: number; avg_ret_5d: number;
}

export interface ScoreDist { bucket: string; count: number }

export interface PropSignal {
  ticker: string; price: number; score: number;
  regime: "TRENDING" | "HIGH_VOL" | "CHOPPY";
  ml_probability: number; momentum: number;
  volatility: number; volume: number; trend: number;
}

export interface PropPosition {
  entry: number; size: number;
  current_price: number; unrealized_pnl: number;
}

export interface PropTrade {
  ticker: string; pnl: number; reason: string; date: string;
}

export interface PropDeskResult {
  signals: PropSignal[];
  positions: Record<string, PropPosition>;
  cash: number;
  realized_pnl: number;
  trades: PropTrade[];
}

export interface PropTradeResult {
  status?: string; action?: string; ticker?: string;
  price?: number; pnl?: number; cash?: number; error?: string;
}

export interface OptionsSummary {
  expiry: string;
  call_vol_oi: number;
  put_vol_oi: number;
  call_put_ratio: number;
  cp_oi_ratio: number;
  total_call_vol: number;
  total_put_vol: number;
  total_call_oi: number;
  total_put_oi: number;
  otm_call_vol: number;
  atm_iv: number | null;
  data_source: string;
}

export interface SmartMoneySignal {
  ticker: string;
  price: number;
  smart_money_score: number;
  confidence: string;
  signal: string;
  direction: "Bullish" | "Bearish" | "Neutral";
  risk_rating: string;
  win_rate: number;
  avg_5d_return: number;
  occurrences: number;
  expected_move_low: number;
  expected_move_high: number;
  rvol: number;
  thesis: string;
  options_summary: OptionsSummary | null;
  score_breakdown: {
    call_sweep: number;
    volume_oi: number;
    ask_aggression: number;
    dark_pool: number;
    sector_strength: number;
    historical: number;
  };
}

export interface SmartMoneyResult {
  leaderboard: SmartMoneySignal[];
  timestamp: string;
  data_source?: string;
}

export function smartMoneyScan(tickers: string[], forceRefresh = false) {
  return fetchJson<SmartMoneyResult & { cached?: boolean; cache_age_secs?: number }>("/smart-money/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers, force_refresh: forceRefresh }),
  });
}

export interface MarketSector { ticker: string; name: string; price: number; change_pct: number; }
export interface MarketIndex  { ticker: string; label: string; price: number; change_pct: number; }
export interface MarketOverview {
  sectors: MarketSector[];
  indices: MarketIndex[];
  advance_decline: { up: number; down: number; unchanged: number };
  as_of: string;
}
export function fetchMarketOverview() {
  return fetchJson<MarketOverview>("/market/overview");
}

export interface CongressTrade {
  member: string;
  party: string;
  chamber: string;
  ticker: string;
  type: string;
  amount: string;
  date: string;
  asset: string;
}

export interface CongressResult {
  trades: CongressTrade[];
  count: number;
}

export function fetchCongressTrades(refresh = false) {
  return fetchJson<CongressResult>(`/congress/trades${refresh ? "?refresh=true" : ""}`);
}

export function subscribeEmail(email: string) {
  return fetchJson<{ ok: boolean; pending?: boolean; error?: string }>("/alerts/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function fetchSubscriberCount() {
  return fetchJson<{ subscribers: number; smtp_configured: boolean }>("/alerts/count");
}

export interface BullFlowRow {
  rank: number;
  ticker: string;
  price: number;
  strike: number | null;
  expiry: string | null;
  premium_m: number;
  premium_k: number;
  call_put_ratio: number;
  call_vol_oi: number;
  total_call_vol: number;
  days_to_earnings: number | null;
  short_float_pct: number | null;
}

export interface SqueezeSignal {
  rank: number;
  ticker: string;
  price: number;
  short_float_pct: number;
  short_ratio: number;
  call_put_ratio: number;
  premium_m: number;
  squeeze_score: number;
}

export interface InsiderTrade {
  ticker: string;
  insider_name: string;
  title: string;
  trade_type: "Buy" | "Sell";
  shares: number;
  price: number;
  value: number;
  date: string;
}

export interface BreakoutSignal {
  rank: number;
  ticker: string;
  price: number;
  breakout_score: number;
  rsi: number;
  macd_bullish: boolean;
  macd_cross: boolean;
  volume_ratio: number;
  pct_from_52w_high: number;
  above_sma50: boolean;
  above_sma200: boolean;
  golden_cross: boolean;
}

export function fetchBreakoutRadar(tickers?: string[]) {
  return fetchJson<{ results: BreakoutSignal[]; scanned: number }>(
    "/breakout/radar",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers ?? [] }),
    }
  );
}

export function fetchSqueezeSignals(tickers?: string[]) {
  return fetchJson<{ results: SqueezeSignal[]; scanned: number }>(
    "/squeeze/detector",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers ?? [] }),
    }
  );
}

export function fetchInsiderTrades(days = 30) {
  return fetchJson<{ trades: InsiderTrade[]; count: number }>(`/insider/trades?days=${days}`);
}

export async function fetchAIAnalysis(data: {
  ticker: string; rsi?: number; macd?: number; volume_ratio?: number;
  price?: number; change_pct?: number; score?: number; rating?: string;
  sector?: string; sma50?: number; sma200?: number;
}): Promise<{ analysis: string; ticker: string }> {
  const res = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export function fetchAIThesis(row: Pick<BullFlowRow, "ticker"|"call_put_ratio"|"premium_m"|"days_to_earnings"|"short_float_pct"|"strike"|"expiry">) {
  return fetchJson<{ ticker: string; thesis: string }>(
    "/ai/thesis",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker:           row.ticker,
        call_put_ratio:   row.call_put_ratio,
        premium_m:        row.premium_m,
        days_to_earnings: row.days_to_earnings,
        short_float_pct:  row.short_float_pct,
        strike:           row.strike,
        expiry:           row.expiry,
      }),
    }
  );
}

export function fetchBullFlow(tickers?: string[]) {
  return fetchJson<{ results: BullFlowRow[]; scanned: number; returned: number; stale?: boolean; note?: string | null }>(
    "/bull-flow/top10",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers ?? [] }),
    }
  );
}

export interface BullFlowHistorySignal {
  ticker: string;
  signal_date: string;
  session: string;
  price_at_signal: number | null;
  call_put_ratio: number;
  premium_m: number | null;
  strike: number | null;
  expiry: string | null;
}

export function fetchBullFlowHistory() {
  return fetchJson<{ signals: BullFlowHistorySignal[]; dates: string[]; count: number }>(
    "/bull-flow/history",
    { method: "GET" }
  );
}

export interface PersistenceDayRecord {
  date: string;
  price_at_signal: number | null;
  call_put_ratio: number;
  premium_m: number | null;
  strike: number | null;
  expiry: string | null;
}

export interface PersistenceSignal {
  ticker: string;
  days_count: number;
  first_seen: string;
  last_seen: string;
  days: PersistenceDayRecord[];
  max_call_put_ratio: number;
  max_premium_m: number | null;
}

export function fetchBullFlowPersistence() {
  return fetchJson<{ signals: PersistenceSignal[]; count: number }>(
    "/bull-flow/persistence",
    { method: "GET" }
  );
}

export async function createStockScannerCheckout(email: string, referralCode?: string): Promise<{ url: string }> {
  const res = await fetch("/api/stock-scanner/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, ...(referralCode ? { referralCode } : {}) }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export async function manageStockScannerSubscription(email: string): Promise<{ url: string }> {
  const res = await fetch("/api/stock-scanner/manage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export interface SignalOutcome {
  ticker:          string;
  signal_date:     string;
  price_at_signal: number;
  call_put_ratio:  number;
  premium_m:       number | null;
  strike:          number | null;
  expiry:          string | null;
  t3_price:        number | null;
  t5_price:        number | null;
  t10_price:       number | null;
  t3_pct:          number | null;
  t5_pct:          number | null;
  t10_pct:         number | null;
  t3_win:          boolean | null;
  t5_win:          boolean | null;
  t10_win:         boolean | null;
}

export function fetchSignalOutcomes() {
  return fetchJson<{ outcomes: SignalOutcome[]; count: number; win_rates: { t3: number | null; t5: number | null; t10: number | null } }>("/outcomes");
}

export interface AnalyticsResult {
  tickers_analyzed: string[];
  failed: string[];
  total_observations: number;
  overall_win_rate_1d: number;
  score_distribution: ScoreDist[];
  bucket_stats: BucketStat[];
  best_thresholds: ThresholdStat[];
  error?: string;
}

export interface ConvergenceRow {
  ticker: string;
  price: number;
  vol_ratio: number;
  call_put_ratio: number;
  premium_m: number;
  convergence_score: number;
  expiry: string | null;
  strike: number | null;
  rank: number;
}

export function fetchConvergence() {
  return fetchJson<{ results: ConvergenceRow[]; scanned: number }>("/convergence");
}

export interface PremarketRow {
  ticker: string;
  price: number;
  prev_close: number;
  change_pct: number;
  vol_ratio: number;
  mkt_cap_b: number | null;
}

export function fetchPremarket() {
  return fetchJson<{ gainers: PremarketRow[]; losers: PremarketRow[]; scanned: number }>("/premarket");
}

export async function fetchCatalyst(data: {
  ticker: string;
  price: number;
  call_put_ratio?: number;
  premium_m?: number;
  vol_ratio?: number;
  score?: number;
  expiry?: string;
}): Promise<{ explanation: string; ticker: string }> {
  const res = await fetch("/api/catalyst", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Catalyst fetch failed");
  return res.json();
}

export interface MorningBrief {
  brief: string;
  date: string;
  tickers: string[];
  generated_at: string;
  cached: boolean;
}

export async function fetchMorningBrief(): Promise<MorningBrief> {
  const res = await fetch("/api/morning-brief");
  if (!res.ok) throw new Error("Morning brief fetch failed");
  return res.json();
}

export async function refreshMorningBrief(): Promise<void> {
  await fetch("/api/morning-brief/refresh", { method: "POST" });
}

export interface DarkPoolRow {
  rank: number;
  ticker: string;
  short_vol: number;
  total_vol: number;
  short_pct: number;
  score: number;
  signal: "EXTREME" | "HIGH" | "ELEVATED" | "NOTABLE";
  call_put_ratio: number | null;
  bias: "BULLISH" | "BEARISH" | "NEUTRAL" | "UNKNOWN";
  flow: "INFLOW" | "OUTFLOW" | "NEUTRAL" | "UNKNOWN";
  conviction: "STRONG BUY" | "BUY" | "INFLOW" | "WATCH" | "OUTFLOW" | "SELL" | "STRONG SELL";
}

export function fetchDarkPool() {
  return fetchJson<{ results: DarkPoolRow[]; date: string | null; total_in_db: number; generating?: boolean; stale?: boolean }>("/darkpool");
}

export interface GammaStrike {
  strike: number; call_oi: number; put_oi: number; total_oi: number; net_gamma: number;
}
export interface GammaWallRow {
  ticker: string; price: number; wall_strike: number; wall_distance_pct: number;
  expiry: string; strikes: GammaStrike[]; flip_strike: number | null;
}
export function fetchGammaWall() {
  return fetchJson<{ results: GammaWallRow[] }>("/gamma-wall");
}

export interface AITradeSetup {
  ticker: string; price: number;
  setup_type: string; direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  conviction: "HIGH" | "MEDIUM";
  entry_strike: number; expiry: string;
  target_price: number; stop_loss: number;
  signals_aligned: string[];
  thesis: string; risk_level: "LOW" | "MEDIUM" | "HIGH";
  smp_score?: number;
  smp_label?: string;
  smp_layers?: string[];
}
export function fetchAITrades() {
  return fetchJson<{ trades: AITradeSetup[]; generated_at?: string; tickers_scanned?: number; signal_sources?: string[]; warming?: boolean; loading?: boolean; refreshing?: boolean; error?: string }>("/ai-trades");
}
export function triggerAITradesRegenerate() {
  return fetchJson<{ status: string; message: string }>("/ai-trades/regenerate", { method: "POST" });
}

export function checkAITradesSubscription(email: string) {
  return fetchJson<{ subscribed: boolean; admin?: boolean; error?: string }>("/check-subscription", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export interface AIEarlyMover {
  ticker: string;
  rec_type: "BUY_CALL" | "BUY_STOCK";
  strike: number | null;
  expiry: string | null;
  days_out: number | null;
  stock_price: number;
  day_ret: number;
  confirmed_2d: boolean;
  vol_oi: number | null;
  prem: number | null;
  conviction: "HIGH" | "MEDIUM";
  thesis: string;
  why_it_stands_out: string;
}
export function fetchAIEarlyMovers(force = false) {
  return fetchJson<{ picks: AIEarlyMover[]; generated_at: string | null; signals_evaluated: number; generating?: boolean; stale?: boolean }>(`/ai-early-movers${force ? "?force=1" : ""}`);
}

export interface AIShortCall {
  ticker: string;
  rec_type?: "BUY_CALL" | "BUY_STOCK";
  strike: number | null;
  expiry: string | null;
  days_out: number | null;
  vol_oi: number | null;
  prem: number | null;
  stock_price: number;
  otm_pct: number | null;
  breakeven: number | null;
  day_ret?: number;
  confirmed_2d?: boolean;
  conviction: "HIGH" | "MEDIUM";
  urgency?: string;
  thesis: string;
  why_it_stands_out: string;
  smp_score?: number;
  smp_label?: string;
  smp_layers?: string[];
}
export function fetchAIShortCalls(force = false) {
  return fetchJson<{ picks: AIShortCall[]; generated_at: string | null; signals_evaluated: number; error?: string }>(`/ai-short-calls${force ? "?force=1" : ""}`);
}

export interface AIShortCallLogEntry {
  id: number;
  trade_date: string;
  rank: number;
  ticker: string;
  strike: number;
  expiry: string;
  days_out: number;
  vol_oi: number;
  prem: number;
  stock_price: number;
  otm_pct: number;
  breakeven: number | null;
  conviction: string;
  urgency: string;
  thesis: string;
  why_it_stands_out: string;
  outcome: "WIN" | "LOSS" | "OPEN";
  t1_price: number | null; t3_price: number | null; t5_price: number | null;
  t1_pct: number | null;   t3_pct: number | null;   t5_pct: number | null;
  t1_win: boolean | null;  t3_win: boolean | null;  t5_win: boolean | null;
  expiry_price: number | null; expiry_pct: number | null; expiry_win: boolean | null;
  created_at: string;
}
export interface AIShortCallLogResult {
  picks: AIShortCallLogEntry[];
  count: number;
  win_rates: { expiry: number | null; t1: number | null; t3: number | null; t5: number | null };
  by_date: Record<string, { total: number; wins: number; losses: number; open: number }>;
}
export function fetchAIShortCallsLog() {
  return fetchJson<AIShortCallLogResult>("/ai-short-calls-log");
}

export interface ConvictionCallStrike {
  ticker: string; price: number; strike: number; expiry: string;
  days_out: number; vol_oi: number; prem: number; otm_pct: number;
  iv: number; urgency: string; last_seen: string;
}
export interface ConvictionCallSignal {
  ticker: string; price: number; score: number; conviction: string;
  rank: number; num_strikes: number; total_prem_m: number;
  max_vol_oi: number; avg_iv: number; urgency: string;
  last_seen?: string;
  strikes: ConvictionCallStrike[];
}
export function fetchConvictionCalls(force = false, fallback = false) {
  const params = new URLSearchParams();
  if (force) params.set("force", "1");
  if (fallback) params.set("fallback", "1");
  const qs = params.toString();
  return fetchJson<{ signals: ConvictionCallSignal[]; generated_at: string; total: number; window?: string; note?: string; error?: string }>(`/conviction-calls${qs ? `?${qs}` : ""}`);
}

export function triggerConvictionScan() {
  return fetchJson<{ status: string; message: string }>("/admin/run-eod-scan", { method: "POST" });
}

export interface ConvictionOutcomePick {
  snap_date: string; ticker: string; conviction: string; score: number;
  entry_price: number | null;
  d1_price: number | null; d1_pct: number | null;
  d3_price: number | null; d3_pct: number | null;
  d5_price: number | null; d5_pct: number | null;
  updated_at: string | null;
}
export interface ConvictionOutcomeStats {
  signals: number; settled: number; wins: number; losses: number;
  win_rate: number | null; avg_gain: number | null; avg_loss: number | null; ev: number | null;
}
export interface ConvictionOutcomeResult {
  picks: ConvictionOutcomePick[];
  stats: {
    overall: { d1: ConvictionOutcomeStats; d3: ConvictionOutcomeStats; d5: ConvictionOutcomeStats };
    extreme: { d1: ConvictionOutcomeStats; d3: ConvictionOutcomeStats; d5: ConvictionOutcomeStats };
    high:    { d1: ConvictionOutcomeStats; d3: ConvictionOutcomeStats; d5: ConvictionOutcomeStats };
  };
  total: number;
}
export function fetchConvictionOutcomes() {
  return fetchJson<ConvictionOutcomeResult>(`/conviction-outcomes`);
}

// ---- Composite 8+ ("Top Score") ----------------------------------------
// Today's full single-name 8+ list (ETFs/funds excluded), ranked most-bullish
// (highest composite score, then volume confirmation, then momentum) first.
export interface CompositeLeaderEntry {
  ticker: string;
  score: number;
  rating: string;
  price: number | null;
  rsi: number | null;
  volume_ratio: number | null;
  price_change_pct: number | null;
  quote_type: string;
}
export interface CompositeLeaderboard {
  scan_date: string;
  min_score: number;
  exclude_etf: boolean;
  count: number;
  leaderboard: CompositeLeaderEntry[];
}
export function fetchCompositeLeaderboard(min = 8, excludeEtf = true) {
  const params = new URLSearchParams();
  params.set("min", String(min));
  if (excludeEtf) params.set("exclude_etf", "1");
  return fetchJson<CompositeLeaderboard>(`/composite-leaderboard?${params.toString()}`);
}

// Daily track record of the actionable cohort (score≥8, vol≥1.5×, non-ETF).
// Entry = next session OPEN; returns = 1/2/3/4 weeks held (5/10/15/20 sessions).
export interface CompositeTrackPick {
  snap_date: string;
  ticker: string;
  score: number | null;
  rating: string | null;
  scan_price: number | null;
  volume_ratio: number | null;
  price_change_pct: number | null;
  entry_date: string | null;
  entry_open: number | null;
  w1_pct: number | null;
  w2_pct: number | null;
  w3_pct: number | null;
  w4_pct: number | null;
}
export interface CompositeTrackStat {
  count: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_pct: number | null;
}
export interface CompositeTrackRecord {
  picks: CompositeTrackPick[];
  stats: {
    w1: CompositeTrackStat;
    w2: CompositeTrackStat;
    w3: CompositeTrackStat;
    w4: CompositeTrackStat;
  };
  today_count: number;
}
export function fetchCompositeTrackRecord(days = 120) {
  return fetchJson<CompositeTrackRecord>(`/composite-track-record?days=${days}`);
}

export interface CompositeSnapshotStatus {
  running: boolean;
  phase: string;
  snap_date: string | null;
  logged: number;
  error: string | null;
  finished_at: string | null;
}
export function fetchCompositeSnapshotStatus() {
  return fetchJson<CompositeSnapshotStatus>(`/composite-snapshot/status`);
}
export function triggerCompositeSnapshot() {
  return fetchJson<{ started: boolean; reason?: string }>(
    `/composite-snapshot/trigger`,
    { method: "POST" },
  );
}

export interface EodSweepStrike {
  ticker: string; price: number; strike: number; expiry: string;
  days_out: number; vol_oi: number; prem: number; otm_pct: number;
  iv: number; urgency: string; detected_at: string; minutes_to_close: number;
}
export interface EodSweepSignal {
  ticker: string; price: number; score: number; grade: string;
  rank: number; num_strikes: number; total_prem_m: number;
  max_vol_oi: number; avg_iv: number; latest_at: string;
  minutes_to_close: number; urgency: string;
  strikes: EodSweepStrike[];
}
export function fetchEodSweeps(bust = false) {
  return fetchJson<{ signals: EodSweepSignal[]; generated_at: string; total: number; note?: string }>(`/eod-sweeps${bust ? "?bust=1" : ""}`);
}

export interface EodSweepRecord {
  ticker: string; signal_date: string; session: string;
  score: number; grade: string; num_strikes: number;
  total_prem_m: number; max_vol_oi: number; avg_iv: number;
  price_at_signal: number | null;
  close_t1: number | null; close_t3: number | null; close_t5: number | null;
  return_t1: number | null; return_t3: number | null; return_t5: number | null;
}
export interface EodSweepStat {
  n: number; win_rate: number | null; avg_return: number | null;
}
export interface EodSweepTrackData {
  total_signals: number;
  overall: { t1: EodSweepStat; t3: EodSweepStat; t5: EodSweepStat };
  by_session: Array<{ session: string; total: number; t1: EodSweepStat; t3: EodSweepStat; t5: EodSweepStat }>;
  by_grade: Array<{ grade: string; total: number; t1: EodSweepStat; t3: EodSweepStat; t5: EodSweepStat }>;
  recent: EodSweepRecord[];
  generated_at: string;
}
export function fetchEodSweepTrackRecord() {
  return fetchJson<EodSweepTrackData>("/eod-sweep-track-record");
}


export interface CompositeScoreRow {
  ticker: string; price: number; score: number;
  bias: "STRONG BULL" | "BULLISH" | "NEUTRAL" | "BEARISH" | "STRONG BEAR";
  components: {
    iv_rank: number; iv_score: number;
    smart_cp: number; retail_cp: number; sm_score: number;
    accum_pct: number; accum_score: number;
    top_accum: { strike: number | null; expiry: string | null; otm_pct: number };
    max_pain: number | null; mp_score: number;
  };
  nearest_exp: string;
}
export function fetchCompositeScore() {
  return fetchJson<{ results: CompositeScoreRow[]; scanned: number }>("/composite-score");
}

export interface AITradeLogEntry {
  id: number;
  trade_date: string;
  ticker: string;
  direction: "BULLISH" | "BEARISH" | "NEUTRAL";
  setup_type: string;
  conviction: string;
  price_at_signal: number;
  entry_strike: number | null;
  expiry: string | null;
  target_price: number | null;
  stop_loss: number | null;
  option_premium: number | null;
  breakeven_price: number | null;
  total_premium_usd: number | null;
  signals_aligned: string[];
  thesis: string;
  risk_level: string;
  t1_price: number | null; t3_price: number | null; t5_price: number | null; t10_price: number | null;
  t1_pct: number | null;   t3_pct: number | null;   t5_pct: number | null;   t10_pct: number | null;
  t1_win: boolean | null;  t3_win: boolean | null;  t5_win: boolean | null;  t10_win: boolean | null;
  expiry_price: number | null;
  expiry_pct: number | null;
  expiry_win: boolean | null;
  outcome: "OPEN" | "WIN" | "LOSS";
  created_at: string;
  source: "AI_TRADE" | "MULTI_SIGNAL" | "BOTH";
}

export interface AITradeLogResult {
  trades: AITradeLogEntry[];
  count: number;
  win_rates: { expiry: number | null; t1: number | null; t3: number | null; t5: number | null; t10: number | null };
  by_direction: Record<string, { count: number; win_rate_expiry: number | null; win_rate_t5: number | null }>;
  by_source: Record<string, { count: number; win_rate_expiry: number | null; win_rate_t5: number | null }>;
}

export function fetchAITradeLog() {
  return fetchJson<AITradeLogResult>("/ai-trade-log");
}

export function logMultiSignalThesis(payload: {
  ticker: string;
  signals: string[];
  score: number;
  price: number;
  thesis: string;
}) {
  return fetchJson<{ ok: boolean; ticker: string; date: string }>("/multi-signal/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface WhaleBlock {
  ticker: string;
  price: number;
  direction: "CALL" | "PUT";
  strike: number;
  expiry: string;
  days_out: number;
  prem_m: number;
  volume: number;
  otm_pct: number;
  category: "LEAPS" | "AGGRESSIVE" | "MEDIUM";
  tier: "MEGA_WHALE" | "WHALE" | "BIG_BLOCK";
}

export interface WhaleActivityResult {
  blocks: WhaleBlock[];
  total: number;
  scanned: number;
}

export function fetchWhaleActivity() {
  return fetchJson<WhaleActivityResult>("/whale-activity");
}

export interface WhaleHistoryBlock extends WhaleBlock {
  first_seen: string;
}

export interface WhaleHistoryResult {
  blocks: WhaleHistoryBlock[];
  total: number;
}

export function fetchWhaleHistory(ticker?: string) {
  const q = ticker ? `?ticker=${encodeURIComponent(ticker)}` : "";
  return fetchJson<WhaleHistoryResult>(`/whale-history${q}`);
}

export interface TradeWatchlistEntry {
  id: number;
  ticker: string;
  strike: number;
  expiry: string;
  option_type: "CALL" | "PUT";
  entry_price: number | null;
  contracts: number;
  notes: string | null;
  saved_at: string;
  current_price: number | null;
  days_to_expiry: number | null;
  days_held: number;
  strike_vs_price_pct: number | null;
  total_cost: number | null;
}

export interface TradeWatchlistResult {
  trades: TradeWatchlistEntry[];
  count: number;
}

export function fetchTradeWatchlist() {
  return fetchJson<TradeWatchlistResult>("/trade-watchlist");
}

export function addTradeWatchlist(payload: {
  ticker: string;
  strike?: number | null;
  expiry?: string | null;
  option_type: string;
  entry_price?: number | null;
  contracts?: number;
  notes?: string;
}) {
  return fetchJson<{ ok: boolean; id: number }>("/trade-watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteTradeWatchlist(id: number) {
  return fetchJson<{ ok: boolean }>(`/trade-watchlist/${id}`, { method: "DELETE" });
}

export interface UnusualCall {
  ticker: string;
  price: number;
  strike: number;
  expiry: string;
  days_out: number;
  volume: number;
  oi: number;
  vol_oi: number;
  prem: number;
  otm_pct: number;
  iv: number;
  urgency: "EXPIRING" | "NEAR" | "SHORT";
  first_seen?: string;
  detected_label?: string;
  is_etf?: boolean;
}

export interface UnusualCallsResult {
  hits: UnusualCall[];
  total: number;
  scanned: number;
}

export interface MorningRunnerRow {
  ticker: string;
  price: number;
  prev_close: number;
  gap_pct: number;
  rel_vol: number;
  avg_vol: number;
  today_vol: number;
  mkt_cap_b: number | null;
  score: number;
  squeeze: boolean;
}

export function fetchMorningRunners() {
  return fetchJson<{ runners: MorningRunnerRow[]; total: number; scanned: number }>("/morning-runners");
}

export interface MultiSignalRow {
  ticker: string;
  price: number;
  day_chg: number;
  rel_vol: number;
  pct_from_high: number;
  mkt_cap_b: number | null;
  signals: string[];
  score: number;
}

export interface SignalDef {
  id: string;
  label: string;
  desc: string;
}

export function fetchMultiSignal() {
  return fetchJson<{
    hits: MultiSignalRow[];
    total: number;
    scanned: number;
    max_signals: number;
    signal_defs: Record<string, SignalDef>;
    sector_context: {
      top:    { ticker: string; name: string; day_chg: number; flow: string } | null;
      bottom: { ticker: string; name: string; day_chg: number; flow: string } | null;
    };
    cache_status: Record<string, number>;
    market_regime_on: boolean;
    vix_contango: boolean;
    hyg_healthy: boolean;
    generating?: boolean;
    stale?: boolean;
    note?: string;
  }>("/multi-signal");
}

export function fetchMultiSignalAIThesis(payload: {
  ticker: string;
  signals: string[];
  price: number;
  day_chg: number;
  rel_vol: number;
  pct_from_high: number;
  mkt_cap_b: number | null;
}) {
  return fetchJson<{ ticker: string; thesis: string }>("/multi-signal/ai-thesis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface IVRankResult {
  ticker: string;
  price: number;
  day_chg: number;
  hv30: number | null;
  hv60: number | null;
  hv90: number | null;
  hv_min: number;
  hv_max: number;
  hv_rank: number | null;
  iv30: number | null;
  iv_rank: number | null;
  iv_hv_ratio: number | null;
  expiry_used: string | null;
}

export function fetchIVRank(ticker: string) {
  return fetchJson<IVRankResult>(`/iv-rank?ticker=${encodeURIComponent(ticker)}`);
}

export interface IVScanRow {
  ticker: string;
  price: number;
  day_chg: number;
  hv30: number;
  hv_rank: number;
  iv30: number | null;
  iv_rank: number;
  iv_hv_ratio: number | null;
  setup: "CHEAP_OPTIONS" | "EXPENSIVE_OPTIONS" | "IV_PREMIUM" | "NEUTRAL";
}

export function fetchIVRankScan() {
  return fetchJson<{ rows: IVScanRow[]; scanned: number }>("/iv-rank/scan");
}

export interface BreakoutRow {
  ticker: string;
  price: number;
  high_52: number;
  low_52: number;
  pct_from_high: number;
  range_pos: number;
  rel_vol: number;
  day_chg_pct: number;
  mkt_cap_b: number | null;
  score: number;
  breakout: boolean;
}

export function fetch52WeekBreakout() {
  return fetchJson<{ hits: BreakoutRow[]; total: number; scanned: number }>("/52week-breakout");
}

export interface SectorRow {
  ticker: string;
  name: string;
  price: number;
  day_chg: number;
  wk1_chg: number | null;
  mo1_chg: number | null;
  rel_vol: number;
  range_pos: number;
  flow: "INFLOW" | "OUTFLOW" | "RISING" | "FALLING" | "NEUTRAL";
}

export function fetchSectorRotation() {
  return fetchJson<{ sectors: SectorRow[]; scanned: number }>("/sector-rotation");
}

export interface SqueezeSetupRow {
  ticker: string;
  price: number;
  signal_type: "SQUEEZE" | "LOW_FLOAT" | "BOTH";
  short_float_pct: number;
  days_to_cover: number;
  float_m: number | null;
  vol_pct_float: number | null;
  rel_vol: number;
  mkt_cap_b: number | null;
  score: number;
}

export function fetchSqueezeSetup() {
  return fetchJson<{ setups: SqueezeSetupRow[]; total: number; scanned: number }>("/squeeze-setup");
}

export function fetchSqueezeSetupAI(rows: SqueezeSetupRow[]) {
  return fetchJson<{ signals: Array<{ ticker: string; signal: string; thesis: string; confidence: number }>; sms_sent: string[] }>(
    "/squeeze-setup/ai-signal",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) }
  );
}

export function fetchUnusualCalls() {
  return fetchJson<UnusualCallsResult>("/unusual-calls");
}

export interface UnusualCallsLogEntry extends UnusualCall {
  first_seen: string;
  last_seen: string;
}

export interface UnusualCallsLogResult {
  signals: UnusualCallsLogEntry[];
  total: number;
}

export function fetchUnusualCallsLog(ticker?: string) {
  const q = ticker ? `?ticker=${encodeURIComponent(ticker)}` : "";
  return fetchJson<UnusualCallsLogResult>(`/unusual-calls-log${q}`);
}

export interface EtfCallsResult {
  signals: UnusualCallsLogEntry[];
  total: number;
  today_count: number;
}

export function fetchEtfCalls(todayOnly = false) {
  return fetchJson<EtfCallsResult>(`/etf-calls${todayOnly ? "?today=1" : ""}`);
}

export interface GammaPressureRow {
  ticker:             string;
  price:              number;
  price_change_pct:   number;
  fir:                number;
  fsd:                number;
  float_m:            number;
  call_volume:        number;
  avg_delta:          number;
  vol_oi:             number;
  top_strike:         number | null;
  top_strike_expiry:  string | null;
  score:              number;
  sms_sent:           boolean;
  alerted_at:         string;
  alert_date:         string;
}

export interface GammaPressureResult {
  signals:   GammaPressureRow[];
  count:     number;
  last_scan: string | null;
}

export function fetchGammaPressure(date?: string) {
  const q = date ? `?date=${date}` : "";
  return fetchJson<GammaPressureResult>(`/gamma-pressure${q}`);
}

export function triggerGammaScan() {
  return fetch(`${BASE}/gamma-pressure/trigger`, { method: "POST" })
    .then(r => r.json())
    .catch(() => null);
}

export interface OiAccumRow {
  ticker:         string;
  price:          number;
  strike:         number;
  expiry:         string;
  oi_today:       number;
  oi_yesterday:   number;
  oi_change:      number;
  oi_pct_change:  number;
  otm_pct:        number;
  days_out:       number;
}

export interface OiAccumResult {
  signals:        OiAccumRow[];
  count:          number;
  snapshot_dates: string[];
  compared_day1?: string | null;
  compared_day2?: string | null;
}

export function fetchOiAccumulation(days = 1) {
  return fetchJson<OiAccumResult>(`/oi-accumulation?days=${days}`);
}

export function triggerOiSnapshot() {
  return fetch(`${BASE}/oi-snapshot/trigger`, { method: "POST" })
    .then(r => r.json())
    .catch(() => null);
}

export interface ConvictionLayers {
  oi_accum?:       number;
  gamma_fir?:      number;
  charm?:          number;
  short_int?:      number;
  dark_pool?:      number;
  float_pressure?: number;
  far_otm_sweep?:  number;
  sector_sympathy?: number;
}

export interface ConvictionMeta {
  oi_pct?:      number;
  oi_chg?:      number;
  strike?:      number;
  expiry?:      string;
  days_out?:    number;
  fir?:         number;
  charm_score?: number;
  si_pct?:      number;
  dtc?:         number;
  dp_pct?:      number;
  dp_vol?:      number;
}

export interface ConvictionResult {
  ticker:          string;
  price:           number;
  total_pts:       number;
  conviction_pct:  number;
  label:           string;
  layers:          ConvictionLayers;
  meta:            ConvictionMeta;
}

export interface ConvictionStackResult {
  results:         ConvictionResult[];
  count:           number;
  source?:         string;
  universe_count?: number;
}

export function fetchConvictionStack() {
  return fetchJson<ConvictionStackResult>(`/conviction-stack`);
}

// ---- TOP SCORE 8+ track record (L1-L8 money-pressure, next-open entry) ------
export interface ConvictionStackTrackPick {
  snap_date:       string;
  ticker:          string;
  total_pts:       number | null;
  conviction_pct:  number | null;
  label:           string | null;
  price:           number | null;
  entry_date:      string | null;
  entry_open:      number | null;
  w1_pct:          number | null;
  w2_pct:          number | null;
  w3_pct:          number | null;
  w4_pct:          number | null;
  layers:          ConvictionLayers;
  meta:            ConvictionMeta;
  universe_count:  number | null;
  source?:         string | null;
}

export interface ConvictionStackTrackStat {
  count:    number;
  wins:     number;
  losses:   number;
  win_rate: number | null;
  avg_pct:  number | null;
}

export interface ConvictionStackTrackRecord {
  picks: ConvictionStackTrackPick[];
  stats: {
    w1: ConvictionStackTrackStat;
    w2: ConvictionStackTrackStat;
    w3: ConvictionStackTrackStat;
    w4: ConvictionStackTrackStat;
  };
  today_count: number;
}

export function fetchConvictionStackTrackRecord(days = 120) {
  return fetchJson<ConvictionStackTrackRecord>(`/conviction-stack-track-record?days=${days}`);
}

// ── L6: Float-Adjusted Options Demand ────────────────────────────────────────
export interface FloatPressureRow {
  ticker:           string;
  pressure_pct:     number;
  float_shares:     number;
  float_m:          number;
  call_oi:          number;
  delta_demand:     number;
  l6_pts:           number;
}
export interface FloatPressureResult {
  results:    FloatPressureRow[];
  total:      number;
  note:       string;
  threshold:  string;
}
export function fetchFloatPressure() {
  return fetchJson<FloatPressureResult>(`/float-pressure`);
}

// ── L7: Far-OTM Sweep Detector ────────────────────────────────────────────────
export interface FarOtmSweepRow {
  ticker:       string;
  price:        number;
  strike:       number;
  expiry:       string;
  days_out:     number;
  volume:       number;
  oi:           number;
  vol_oi:       number;
  prem:         number;
  otm_pct:      number;
  iv:           number;
  urgency:      string;
  cap_tier:     string;
  last_seen_et: string;
}
export interface FarOtmSweepResult {
  sweeps: FarOtmSweepRow[];
  total:  number;
  filter: string;
  note:   string;
}
export function fetchFarOtmSweeps(days = 5) {
  return fetchJson<FarOtmSweepResult>(`/far-otm-sweeps?days=${days}`);
}

// ── L8: Sector Theme Correlation ─────────────────────────────────────────────
export interface HotSector {
  sector:          string;
  lead_tickers:    string[];
  sympathy_plays:  string[];
  heat_score:      number;
}
export interface SectorHeatResult {
  hot_sectors:           HotSector[];
  sector_tickers_fired:  Record<string, string[]>;
  total_sectors_hot:     number;
}
export function fetchSectorHeat(days = 2) {
  return fetchJson<SectorHeatResult>(`/sector-heat?days=${days}`);
}

export interface InsiderRadarRow extends UnusualCallsLogEntry {
  suspicion_score:    number;
  ticker_appearances: number;
  earnings_date:      string | null;
  days_to_earnings:   number | null;
  verdict:            string;
  pre_positioned:     boolean;
}

export interface InsiderRadarResult {
  signals:         InsiderRadarRow[];
  total:           number;
  earnings_linked: number;
  high_suspicion:  number;
  rare_tickers:    number;
  as_of:           string;
}

export function fetchInsiderRadar(bust = false) {
  return fetchJson<InsiderRadarResult>(`/insider-radar${bust ? "?bust=1" : ""}`);
}

export interface InsiderAlert {
  id: number;
  ticker: string;
  detected_at: string;
  suspicion_score: number;
  prem: number | null;
  strike: number | null;
  expiry: string | null;
  price_at_detection: number | null;
  vol_oi: number | null;
  earnings_date: string | null;
  days_to_earnings: number | null;
  ticker_appearances: number | null;
  verdict: string | null;
  pre_positioned: boolean;
  outcome_checked: boolean;
  outcome_verdict: string | null;
  pct_move: number | null;
  called_it: boolean | null;
  price_at_earnings: number | null;
  outcome_at: string | null;
}

export interface InsiderAlertsResult {
  alerts: InsiderAlert[];
  total: number;
  resolved: number;
  called_it: number;
  misses: number;
}

export interface InsiderOutcome {
  id: number;
  ticker: string;
  earnings_date: string | null;
  price_at_detection: number | null;
  price_at_earnings: number | null;
  pct_move: number | null;
  called_it: boolean | null;
  outcome_verdict: string | null;
  checked_at: string;
  suspicion_score: number;
  prem: number | null;
  alert_verdict: string | null;
  detected_at: string;
}

export interface InsiderOutcomesResult {
  outcomes: InsiderOutcome[];
  total: number;
  called_it: number;
  misses: number;
  accuracy_pct: number;
  avg_gain_pct: number;
}

export function fetchInsiderAlerts() {
  return fetchJson<InsiderAlertsResult>(`/insider-alerts`);
}

export function fetchInsiderOutcomes() {
  return fetchJson<InsiderOutcomesResult>(`/insider-outcomes`);
}

export interface MorningInflowResult {
  ticker: string;
  price: number;
  prev_close: number;
  price_chg_pct: number;
  rel_vol: number;
  rel_vol_raw: number;
  today_vol: number;
  projected_vol: number;
  avg_vol: number;
  mins_elapsed: number;
  inflow_m: number;
  outflow_m: number;
  net_m: number;
  flow_ratio: number;
  standout_score: number;
  gap_pct: number;
  gap_multiplier: number;
  momentum_open: number;
  exhaustion_ratio: number;
  fade_risk: "HIGH" | "WATCH" | "HOLD";
  first_bar_pct?: number;
  first_bar_green?: boolean;
  has_first_bar?: boolean;
  mkt_cap_m: number | null;
  micro_pump?: boolean;
}
export interface MorningInflowsData {
  standouts: MorningInflowResult[];
  micro_pumps?: MorningInflowResult[];
  extreme_pumps?: MorningInflowResult[];
  total_found: number;
  scanned: number;
  generated_at: string;
  criteria: string;
}
export function fetchMorningInflows(bust = false) {
  return fetchJson<MorningInflowsData>(`/morning-inflows${bust ? "?bust=1" : ""}`);
}

export interface MyTrade {
  id: number;
  ticker: string;
  strike: number;
  expiry: string;
  vol_oi: number | null;
  prem: number | null;
  otm_pct: number | null;
  urgency: string | null;
  signal_detected_at: string | null;
  saved_at: string;
  entry_price: number | null;
  exit_price: number | null;
  contracts: number;
  notes: string | null;
  status: string;
}

export interface MyTradesResult {
  trades: MyTrade[];
  total: number;
}

export function fetchMyTrades() {
  return fetchJson<MyTradesResult>("/my-trades");
}

export function saveMyTrade(payload: {
  ticker: string; strike: number; expiry: string;
  vol_oi?: number; prem?: number; otm_pct?: number;
  urgency?: string; signal_detected_at?: string;
}) {
  return fetchJson<{ ok: boolean; created: boolean }>("/my-trades", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateMyTrade(id: number, payload: {
  entry_price?: number | null; exit_price?: number | null;
  contracts?: number; notes?: string; status?: string;
}) {
  return fetchJson<{ ok: boolean }>(`/my-trades/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteMyTrade(id: number) {
  return fetchJson<{ ok: boolean }>(`/my-trades/${id}`, { method: "DELETE" });
}

export interface NetFlowRow {
  rank: number;
  ticker: string;
  price: number;
  inflow_m: number;
  outflow_m: number;
  net_m: number;
  total_vol_m: number;
  flow_ratio: number;
  market_cap_m: number | null;
  net_pct_mktcap: number | null;
  cap_tier: "nano" | "micro" | "small" | "mid" | "unknown";
}

export interface NetFlowMicrocapResult {
  micro:   NetFlowRow[];
  small:   NetFlowRow[];
  nano:    NetFlowRow[];
  mid:     NetFlowRow[];
  unknown: NetFlowRow[];
  scanned: number;
  warming?:    boolean;  // no cache yet — server is running the first scan
  refreshing?: boolean;  // serving last good scan while a fresh one runs
}

export interface NetFlowSingleResult {
  ticker: string;
  price: number;
  inflow_m: number;
  outflow_m: number;
  net_m: number;
  total_vol_m: number;
  flow_ratio: number;
  bars: { t: string; v: number; dir: "buy" | "sell" }[];
}

export function fetchNetFlow() {
  return fetchJson<{ results: NetFlowRow[]; scanned: number }>(
    "/net-flow",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
  );
}

export function fetchNetFlowSingle(ticker: string) {
  return fetchJson<NetFlowSingleResult>(`/net-flow/single?ticker=${encodeURIComponent(ticker)}`);
}

export function fetchNetFlowMicrocap() {
  return fetchJson<NetFlowMicrocapResult>(
    "/net-flow/microcap",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
  );
}

export interface MicroCapCall {
  ticker:     string;
  price:      number;
  strike:     number;
  expiry:     string;
  days_out:   number;
  volume:     number;
  oi:         number;
  vol_oi:     number;
  prem:       number;
  otm_pct:    number;
  iv:         number;
  urgency:    string;
  cap_tier:   string;
  first_seen: string;
  last_seen:  string;
}

export interface MicroCapCallsResult {
  signals:    MicroCapCall[];
  total:      number;
  stale?:     boolean;
  stale_note?: string | null;
}

export function fetchUnusualCallsMicrocap(days = 3) {
  return fetchJson<MicroCapCallsResult & { scan_triggered?: boolean }>(`/unusual-calls/microcap?days=${days}`);
}

export function triggerMicrocapScan() {
  return fetchJson<{ status: string; note: string }>("/unusual-calls/microcap/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export interface NetFlowDayDot {
  date:     string;
  net_m:    number;
  positive: boolean;
}

export interface NetFlowStreakRow {
  rank:             number;
  ticker:           string;
  price:            number;
  streak:           number;
  total_net_m:      number;
  avg_daily_net_m:  number;
  min_daily_net_m:  number;
  consistency:      number;       // 0–1; min/avg ratio over streak days
  market_cap_m:     number | null;
  total_pct_mktcap: number | null;
  avg_pct_per_day:  number | null; // avg % of mktcap per day
  cap_tier:         string;
  days:             NetFlowDayDot[];
  // Ignition signal (ASTE/AMLX backtest-derived)
  has_ignition:     boolean;      // rvol≥1.5x + day≥3% + close in top 70% of range
  max_rvol:         number;       // peak relative volume during streak
  max_day_pct:      number;       // peak single-day gain during streak
}

export interface NetFlowStreakResult {
  results: NetFlowStreakRow[];
  scanned: number;
  found:   number;
  stale?:  boolean;
  note?:   string;
}

export function fetchNetFlowMultiday() {
  return fetchJson<NetFlowStreakResult>(
    "/net-flow/multiday",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
  );
}

export interface AISignal {
  ticker:     string;
  signal:     "CONVICTION" | "BUILDING" | "WATCH" | "NOISE";
  thesis:     string;
  confidence: number;
}

export interface AISignalResult {
  signals:  AISignal[];
  model:    string;
  analyzed: number;
}

export function fetchAISignal(rows: NetFlowStreakRow[]) {
  return fetchJson<AISignalResult>(
    "/net-flow/ai-signal",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) }
  );
}

// ── Market Press ──────────────────────────────────────────────────────────────
export interface MarketPressArticle {
  title:        string;
  url:          string;
  source:       string;
  category:     "MARKETS" | "TECH" | "COMMODITIES" | "RATES";
  published_at: string;
  age:          string;
  summary:      string;
}
export interface MarketPressResult {
  articles:   MarketPressArticle[];
  count:      number;
  fetched_at: string;
}
export function fetchMarketPress() {
  return fetchJson<MarketPressResult>("/market-press");
}

// ── Earnings Calendar ─────────────────────────────────────────────────────────
export interface EarningsRow {
  ticker:           string;
  name:             string;
  earnings_date:    string;
  days_until:       number;
  price:            number;
  eps_estimate:     number | null;
  implied_move_pct: number | null;
  mkt_cap_b:        number | null;
}
export interface EarningsCalendarResult {
  earnings:    EarningsRow[];
  count:       number;
  as_of:       string;
  window_days: number;
}
export function fetchEarningsCalendar() {
  return fetchJson<EarningsCalendarResult>("/earnings-calendar");
}

export interface EodAccumResult {
  ticker: string;
  close: number;
  prev_close: number;
  price_chg_pct: number;
  day_high: number;
  day_low: number;
  closing_range: number;
  eod_vol: number;
  eod_rel_vol: number;
  late_flow: number;
  late_surge_pct: number;
  quiet_surge: number;
  accum_score: number;
  signal_type: "accum" | "squeeze";
  mkt_cap_m: number | null;
  has_news: boolean;
  news_headline: string | null;
  news_today_cnt: number;
  news_type: "hard" | "soft" | "none";
  short_float?: number | null;
  days_to_cover?: number | null;
  above_avwap?: boolean | null;
  avwap_5d?: number | null;
  above_avwap_20d?: boolean | null;
  avwap_20d?: number | null;
  rsi_14?: number | null;
  vol_ratio_20d?: number | null;
  new_high_15d?: boolean;
  was_consolidating?: boolean;
  obv_divergence?: boolean;
  macd_bullish?: boolean;
  bb_squeeze_releasing?: boolean;
  buyers_dominant?: boolean;
  above_sma20?: boolean;
  sma20_rising?: boolean;
  pre_ignition_count?: number;
}
export interface EodAccumData {
  candidates: EodAccumResult[];
  squeeze_setups: EodAccumResult[];
  total_found: number;
  scanned: number;
  generated_at: string;
}
export function fetchEodAccumulation(bust = false) {
  return fetchJson<EodAccumData>(`/eod-accumulation${bust ? "?bust=1" : ""}`);
}

export interface EodAccumPickRow {
  scan_date: string;
  ticker: string;
  entry_price: number;
  accum_score: number;
  news_type: "hard" | "soft" | "none";
  news_headline: string | null;
  eod_rel_vol: number;
  late_flow: number;
  closing_range: number;
  price_chg_pct: number;
  next_open: number | null;
  next_open_chg_pct: number | null;
  morning_high: number | null;
  morning_high_chg_pct: number | null;
  gapped_up: boolean | null;
}
export interface EodAccumStats {
  picks: number;
  graded: number;
  hit_rate_pct: number | null;
  avg_gap_pct: number | null;
  avg_high_pct: number | null;
  best_gap_pct: number | null;
}
export interface EodAccumTrackData {
  picks: EodAccumPickRow[];
  summary: {
    all: EodAccumStats;
    pure: EodAccumStats;
    soft: EodAccumStats;
    hard: EodAccumStats;
  };
  as_of: string;
}
export function fetchEodAccumTrack() {
  return fetchJson<EodAccumTrackData>("/eod-accum-track");
}

export interface StandoutPickRow {
  scan_date: string;
  ticker: string;
  entry_price: number;
  price_chg_pct: number;
  rel_vol: number;
  flow_ratio: number;
  standout_score: number;
  mkt_cap_m: number | null;
  close_price: number | null;
  high_price: number | null;
  open_to_close_pct: number | null;
  open_to_high_pct: number | null;
  fade_risk_signal: string | null;
}
export interface StandoutStats {
  picks: number;
  graded: number;
  hit_rate_pct: number | null;
  avg_close_pct: number | null;
  avg_high_pct: number | null;
  best_high_pct: number | null;
}
export interface StandoutTrackData {
  picks: StandoutPickRow[];
  summary: {
    all: StandoutStats;
    extreme: StandoutStats;
    high: StandoutStats;
    standard: StandoutStats;
  };
  as_of: string;
}
export function fetchStandoutTrack() {
  return fetchJson<StandoutTrackData>("/standout-track");
}

export interface CrossScannerRow {
  signal_date: string;
  ticker: string;
  morning_price: number;
  morning_chg_pct: number;
  standout_score: number;
  flow_ratio: number;
  morning_rel_vol?: number;
  eod_close: number;
  accum_score: number;
  news_type: "hard" | "soft" | "none";
  news_headline: string | null;
  eod_rel_vol?: number;
  closing_range?: number;
  late_flow?: number;
  same_day_close_pct?: number | null;
  same_day_high_pct?: number | null;
  short_float?: number | null;
  days_to_cover?: number | null;
  above_avwap?: boolean | null;
}

export interface ShortSqueezeResult {
  ticker: string;
  short_float: number;
  days_to_cover: number | null;
  above_avwap: boolean;
  above_avwap_20d: boolean;
  avwap_5d: number | null;
  avwap_20d: number | null;
  current_price: number | null;
  price_chg_pct: number;
  vol_ratio_20d: number;
  new_high_15d: boolean;
  range_pct_15d: number | null;
  was_consolidating: boolean;
  closing_range_today: number | null;
  rsi_14: number | null;
  obv_divergence: boolean;
  macd_bullish: boolean;
  macd_histogram: number | null;
  bb_squeeze_releasing: boolean;
  up_vol_ratio: number | null;
  buyers_dominant: boolean;
  above_sma20: boolean;
  sma20_rising: boolean;
  sma20_val: number | null;
  pre_ignition_count: number;
  squeeze_score: number;
}
export interface ShortSqueezeData {
  candidates: ShortSqueezeResult[];
  total_found: number;
  scanned: number;
  as_of: string;
  stale?: boolean;
  note?: string;
  stale_label?: string;
}
export function fetchShortSqueeze() {
  return fetchJson<ShortSqueezeData>("/short-squeeze");
}
export interface CrossScannerData {
  today_signals: CrossScannerRow[];
  history: CrossScannerRow[];
  hist_stats: {
    total_signals: number;
    graded: number;
    hit_rate_pct: number | null;
    avg_close_pct: number | null;
    avg_high_pct: number | null;
  };
  as_of: string;
}
export function fetchCrossScanner() {
  return fetchJson<CrossScannerData>("/cross-scanner");
}

export interface NanoMorningCandidate {
  snap_date: string;
  ticker: string;
  rank: number;
  conviction: number;
  price: number;
  mcap_m: number;
  avg_vol: number;
  accum_pts: number;
  steady_pts: number;
  vol_pts: number;
  mom_pts: number;
  net_flow_m: number;
  up_days: number;
  nano_tql: number;
  nano_fired: number;
  nano_predictor: number;
  nano_predictor_risky: boolean;
  nano_predictor_reasons: string[];
  nano_v2_risky?: boolean;
  nano_v2_grade?: string;
  nano_v2_pct?: number;
  nano_v2_risk_reasons?: string[];
  gap_pct?: number;
  meta: Record<string, any>;
}
export interface NanoMorningData {
  count: number;
  candidates: NanoMorningCandidate[];
}
export function fetchNanoMorningCandidates() {
  return fetchJson<NanoMorningData>("/nano-morning/candidates");
}

export interface NanoCarryPick {
  ticker: string;
  rank: number;
  confidence: number;
  tier: "S1c" | "S1d" | "S1b" | "other";
  tier_label: string;
  tier_color: string;
  signals: string[];
  reasoning: string[];
  predicted_move: string;
  scan_time: string | null;
  open_price: number | null;
  close_price: number | null;
  high_price: number | null;
  gain_pct: number | null;
  best_pct: number | null;
}

export interface NanoCarryPerf {
  winners: number;
  total_graded: number;
  avg_gain: number | null;
  avg_best: number | null;
}

export interface NanoCarryData {
  date: string;
  picks: NanoCarryPick[];
  total: number;
  scan_time: string | null;
  perf: NanoCarryPerf;
  s1c: NanoCarryPick[];
  s1d: NanoCarryPick[];
  s1b: NanoCarryPick[];
  other: NanoCarryPick[];
}

export function fetchNanoCarryPicks() {
  return fetchJson<NanoCarryData>("/nano-carry/picks");
}

export interface MultidayRunnerRow {
  ticker: string;
  d1_date: string;
  d2_date?: string;
  cap_tier?: string;
  d1_pct: number;
  d2_pct?: number;
  d1_close?: number;
  d1_rvol?: number;
  d1_strong?: boolean;
  conviction_score?: number;
  intraday_hit?: boolean;
  intraday_entry?: number;
  confirmed?: boolean;
  entry_price?: number;
  stop_price?: number;
  d2_close_pos?: number;
  status?: string;
  exit_pct?: number;
}

export interface MultidayRunnersData {
  watch: MultidayRunnerRow[];
  confirmed: MultidayRunnerRow[];
  active: MultidayRunnerRow[];
  stats: {
    total_confirmed?: number;
    wins?: number;
    losses?: number;
    avg_gain?: number;
    best_gain?: number;
    worst_loss?: number;
  };
  as_of?: string;
}

export function fetchMultidayRunners() {
  return fetchJson<MultidayRunnersData>("/multiday-runners");
}

export interface RunnerSignalRow {
  ticker: string;
  d1_date: string;
  cap_tier: string;
  d1_pct: number;
  d1_strong?: boolean;
  intraday_hit?: boolean;
  intraday_entry?: number;
  entry_price?: number;
  d3_pct?: number;
  d5_pct?: number;
  d10_pct?: number;
  confirmed?: boolean;
  status?: string;
}

export interface RunnerTierStat {
  cap_tier: string;
  total: number;
  graded_d5: number;
  avg_d3?: number;
  avg_d5?: number;
  avg_d10?: number;
  wins_d5: number;
  losses_d5: number;
  best_d5?: number;
  worst_d5?: number;
}

export interface RunnerOutcomesData {
  signals: RunnerSignalRow[];
  tier_stats: RunnerTierStat[];
  as_of?: string;
}

export function fetchRunnerOutcomes() {
  return fetchJson<RunnerOutcomesData>("/runner-outcomes");
}

// ── Steady Grinder Scan ──────────────────────────────────────────────────

export interface GrinderResult {
  ticker: string;
  score: number;
  pattern: "SHAKEOUT_REENTRY" | "STEADY_LOAD" | "EARLY_ACCUMULATION" | "WATCH";
  sweep_confirmed: boolean;
  high_cs_days: number;
  days_seen: number;
  avg_cs: number;
  avg_rvol: number;
  rvol_recent: number;
  rvol_older: number;
  avg_range: number;
  pos_gap_days: number;
  price: number;
  last_seen: string | null;
  cs_yesterday: number;
  cs_best_recent: number;
  cs_min_mid: number;
  vol_yesterday: number;
  avg_vol_7d: number;
  vol_building: boolean;
  shakeout: boolean;
}

export interface GrinderScanData {
  results: GrinderResult[];
  count: number;
  sweep_confirmed_count: number;
  stale: boolean;
  as_of: string;
  note?: string;
}

export function fetchGrinderScan() {
  return fetchJson<GrinderScanData>("/grinder-scan");
}

// ── Gap + Volume Signal (OOS-validated) ──────────────────────────────────────

export interface GapVolumeRow {
  ticker: string;
  price: number;
  open_price: number | null;
  high: number | null;
  low: number | null;
  gap_pct: number;
  volume: number;
  avg_volume: number;
  rvol: number;
  close_strength: number;
  range_pct: number | null;
  score: number;
  scan_date: string;
}

export interface GapVolumeResult {
  signals: GapVolumeRow[];
  count: number;
  scan_date: string | null;
  total_scanned: number;
  edge_note: string;
  stale: boolean;
}

export function fetchGapVolumeSignal() {
  return fetchJson<GapVolumeResult>("/gap-volume-signal");
}

export function fetchFlowScores(tickers: string[]): Promise<Record<string, number | null>> {
  if (!tickers.length) return Promise.resolve({});
  const params = new URLSearchParams({ tickers: tickers.join(",") });
  return fetchJson<Record<string, number | null>>(`/flow-scores?${params}`);
}

export function fetchCallWinRates(tickers: string[]): Promise<Record<string, { wr: number; n: number } | null>> {
  if (!tickers.length) return Promise.resolve({});
  const params = new URLSearchParams({ tickers: tickers.join(",") });
  return fetchJson<Record<string, { wr: number; n: number } | null>>(`/call-win-rates?${params}`);
}

export interface HistSimEntry {
  n: number;
  wr3d: number;
  avg3d: number;
  signal: "BULLISH" | "NEUTRAL" | "BEARISH";
  mode: "breakeven" | "strike" | "stock";
  strike?: number | null;
  breakeven?: number | null;
}
export interface HistSimRequest {
  ticker: string;
  strike?: number | null;
  breakeven?: number | null;
}
export function fetchHistoricalSimilarity(items: HistSimRequest[]): Promise<Record<string, HistSimEntry | null>> {
  if (!items.length) return Promise.resolve({});
  const encoded = items.map(i => {
    if (i.breakeven && i.breakeven > 0) return `${i.ticker}:${i.strike ?? ""}:${i.breakeven}`;
    if (i.strike && i.strike > 0) return `${i.ticker}:${i.strike}`;
    return i.ticker;
  }).join(",");
  const params = new URLSearchParams({ tickers: encoded });
  return fetchJson<Record<string, HistSimEntry | null>>(`/historical-similarity?${params}`);
}

// ── AIEM Autonomous Paper Trading ────────────────────────────────────────────

export interface AiemPaperTrade {
  id: number;
  trade_date: string;
  ticker: string;
  trade_type: "STOCK" | "CALL_OPTION" | "ETF";
  entry_price: number;
  quantity: number;
  notional: number;
  signal_source: string;
  signal_detail: string;
  hold_days_max: number;
  last_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  pnl_is_synthetic_proxy: boolean;
  status: string;
  created_at: string;
  strike: number | null;
  expiry: string | null;
}

export interface AiemPaperClosedTrade {
  id: number;
  trade_date: string;
  ticker: string;
  trade_type: "STOCK" | "CALL_OPTION" | "ETF";
  entry_price: number;
  quantity: number;
  notional: number;
  signal_source: string;
  signal_detail: string;
  exit_price: number | null;
  exit_date: string | null;
  pnl: number | null;
  pnl_pct: number | null;
  pnl_is_synthetic_proxy: boolean;
  status: string;
  strike: number | null;
  expiry: string | null;
  exit_reason: string | null;
}

export interface AiemDailyPnl {
  date: string;
  pnl: number;
  trades: number;
}

export interface AiemPaperPortfolio {
  account_start: number;
  account_value: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  total_pnl_pct: number;
  // Fix #10: total_pnl_pct / avg_pnl_pct above blend CALL_OPTION synthetic
  // 2x-underlying-move proxy % with real STOCK/ETF % into one number.
  // The *_synthetic / *_real variants below are the like-for-like figures
  // per trade methodology; null when no trades of that type exist yet.
  total_pnl_pct_is_blended?: boolean;
  total_pnl_pct_synthetic?: number | null;
  total_pnl_pct_real?: number | null;
  win_rate: number | null;
  total_closed: number;
  winners: number;
  avg_pnl_pct: number;
  avg_pnl_pct_is_blended?: boolean;
  avg_pnl_pct_synthetic?: number | null;
  avg_pnl_pct_real?: number | null;
  pnl_methodology_note?: string;
  open_positions: AiemPaperTrade[];
  open_count: number;
  closed_trades: AiemPaperClosedTrade[];
  daily_pnl: AiemDailyPnl[];
  as_of: string;
}

export function fetchAiemPaperPortfolio(days = 30): Promise<AiemPaperPortfolio> {
  return fetchJson<AiemPaperPortfolio>(`/aiem-paper-portfolio?days=${days}`);
}

export function forceAiemExecute(): Promise<{ status: string; message: string }> {
  return fetchJson<{ status: string; message: string }>("/aiem-paper-portfolio/force-execute", { method: "POST" });
}

export function forceAiemMtm(): Promise<{ status: string; message: string }> {
  return fetchJson<{ status: string; message: string }>("/aiem-paper-portfolio/force-mtm", { method: "POST" });
}

export interface AiemProbabilityPick {
  rank: number;
  ticker: string;
  model_version: string;
  score: number;
  prob_up_1d: number | null;
  prob_up_2d: number | null;
  prob_up_3d: number | null;
  prob_up_4d: number | null;
  confidence: number | null;
  edge_after_cost_prob_pts: number | null;
  regime_tag: string | null;
  top_contributing_layers: string[] | null;
  warnings: string[] | null;
}

export interface AiemProbabilityDailyPicks {
  pick_date: string | null;
  picks: AiemProbabilityPick[];
  methodology?: string;
  note?: string;
}

export function fetchAiemProbabilityDailyPicks(): Promise<AiemProbabilityDailyPicks> {
  return fetchJson<AiemProbabilityDailyPicks>("/aiem-probability-engine/daily-picks");
}

export interface AiemProbabilityTrackRow {
  signal_date: string;
  ticker: string;
  model_version: string;
  prob_up_1d: number | null;
  prob_up_2d: number | null;
  prob_up_3d: number | null;
  prob_up_4d: number | null;
  confidence: number | null;
  regime_tag: string | null;
  outcome_ret_1d: number | null;
  outcome_ret_2d: number | null;
  outcome_ret_3d: number | null;
  outcome_ret_4d: number | null;
  outcome_label_1d: number | null;
  outcome_label_2d: number | null;
  outcome_label_3d: number | null;
  outcome_label_4d: number | null;
  correct_1d: boolean | null;
  correct_2d: boolean | null;
  correct_3d: boolean | null;
  correct_4d: boolean | null;
}

export interface AiemProbabilityHorizonSummary {
  n_graded: number;
  accuracy_pct: number | null;
  avg_outcome_ret_pct: number | null;
  note?: string;
}

export interface AiemProbabilityTrackRecordSummary {
  contaminated: Record<string, AiemProbabilityHorizonSummary>;
  corrected: Record<string, AiemProbabilityHorizonSummary>;
  genuine: Record<string, AiemProbabilityHorizonSummary>;
}

export interface AiemProbabilityTrackRecord {
  rows: AiemProbabilityTrackRow[];
  summary: AiemProbabilityTrackRecordSummary;
  pit_status_counts?: Record<string, number>;
  total_logged: number;
  note?: string;
}

export function fetchAiemProbabilityTrackRecord(limit = 60): Promise<AiemProbabilityTrackRecord> {
  return fetchJson<AiemProbabilityTrackRecord>(`/aiem-probability-engine/track-record?limit=${limit}`);
}

export function forceAiemProbabilityEngineRun(): Promise<{ status: string; message: string }> {
  return fetchJson<{ status: string; message: string }>("/aiem-probability-engine/force-run", { method: "POST" });
}

export interface WashoutCompleteQualityFilters {
  price_min: number;
  bad_months_str: string;
  trend_max10d: number;
  description?: string;
}

export interface WashoutCompleteSignal {
  ticker: string;
  alert_date: string;
  coil_date: string;
  coil_price: number;
  washout_low: number | null;
  washout_low_date: string | null;
  alert_price: number;
  entry_discount_pct: number;
  vol_ratio: number;
  close_strength: number;
  range_ratio: number;
  days_in_washout: number | null;
  prior_ret10d: number | null;
}

export interface WashoutCompleteResult {
  signals: WashoutCompleteSignal[];
  watching_count: number;
  scan_date: string | null;
  quality_filters?: WashoutCompleteQualityFilters;
  backtest: {
    filtered_wr_1m?: number;
    filtered_wr_3m?: number;
    unfiltered_wr_1m?: number;
    lose_gt_20pct?: number;
    avg_entry_discount: number;
    note?: string;
    wr_20pct?: number;
    wr_50pct?: number;
    avg_return?: number;
  };
  stale: boolean;
}

export function fetchWashoutComplete(): Promise<WashoutCompleteResult> {
  return fetchJson<WashoutCompleteResult>("/stock-api/momentum-washout-complete");
}

export interface CandlestickConfluenceSignal {
  scan_date: string;
  ticker: string;
  close_price: number;
  volume: number;
  patterns_detected: string[];
  vol_confirmed: boolean;
  at_support: boolean;
  rsi_oversold: boolean;
  rsi_value: number | null;
  confluence_count: number;
}

export interface CandlestickConfluenceResult {
  signals: CandlestickConfluenceSignal[];
  count: number;
  scan_date: string | null;
  stale: boolean;
}

export function fetchCandlestickConfluence(): Promise<CandlestickConfluenceResult> {
  return fetchJson<CandlestickConfluenceResult>("/stock-api/candlestick-confluence");
}
