const BASE = "/stock-api";

export async function fetchJson<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, opts);
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
  return fetchJson<{ ok: boolean; error?: string }>("/alerts/subscribe", {
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
  return fetchJson<{ results: BullFlowRow[]; scanned: number; returned: number }>(
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

export async function createStockScannerCheckout(email: string): Promise<{ url: string }> {
  const res = await fetch("/api/stock-scanner/checkout", {
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
  return fetchJson<{ results: DarkPoolRow[]; date: string | null; total_in_db: number }>("/darkpool");
}

export interface PutIntentRow {
  ticker: string;
  price: number;
  hedge_prem_m: number;
  bear_prem_m: number;
  hedge_pct: number;
  bear_pct: number;
  verdict: "BEARISH BET" | "HEDGE" | "MIXED";
  top_bear_strike: number | null;
  top_bear_expiry: string | null;
}

export function fetchPutIntent() {
  return fetchJson<{ results: PutIntentRow[]; scanned: number }>("/options-intent");
}

export interface VolCrushRow {
  ticker: string; price: number; current_iv: number; hv_30: number;
  iv_hv_ratio: number | null; iv_rank: number;
  verdict: "HIGH FEAR" | "ELEVATED" | "NORMAL" | "LOW IV";
  earnings_date: string | null;
}
export function fetchVolCrush() {
  return fetchJson<{ results: VolCrushRow[]; scanned: number }>("/vol-crush");
}

export interface CallIntentRow {
  ticker: string; price: number; fomo_prem_m: number; accum_prem_m: number;
  accum_vol_m: number; accum_oi_m: number;
  fomo_vol_m: number; fomo_oi_m: number;
  fomo_pct: number; accum_pct: number;
  verdict: "FOMO" | "ACCUMULATION" | "MIXED";
  top_accum_strike: number | null; top_accum_expiry: string | null;
  top_accum_otm_pct: number;
}
export function fetchCallIntent() {
  return fetchJson<{ results: CallIntentRow[]; scanned: number }>("/call-intent");
}

export interface SmartVsRetailRow {
  ticker: string; price: number; smart_prem_m: number; retail_prem_m: number;
  smart_cp: number; retail_cp: number;
  divergence: "SMART BULLISH"|"SMART BEARISH"|"RETAIL BULLISH"|"RETAIL BEARISH"|"ALIGNED"|"NEUTRAL";
  signal_strength: "STRONG" | "MODERATE" | "WEAK";
}
export function fetchSmartVsRetail() {
  return fetchJson<{ results: SmartVsRetailRow[]; scanned: number }>("/smart-vs-retail");
}

export interface MaxPainRow {
  ticker: string; price: number; max_pain: number; distance_pct: number;
  direction: "ABOVE PAIN" | "BELOW PAIN"; nearest_expiry: string; days_to_exp: number;
}
export function fetchMaxPain() {
  return fetchJson<{ results: MaxPainRow[]; scanned: number }>("/max-pain");
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

export interface AIShortCall {
  ticker: string;
  strike: number;
  expiry: string;
  days_out: number;
  vol_oi: number;
  prem: number;
  stock_price: number;
  otm_pct: number;
  breakeven: number;
  conviction: "HIGH" | "MEDIUM";
  urgency: string;
  thesis: string;
  why_it_stands_out: string;
}
export function fetchAIShortCalls() {
  return fetchJson<{ picks: AIShortCall[]; generated_at: string | null; signals_evaluated: number; error?: string }>("/ai-short-calls");
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
  strikes: ConvictionCallStrike[];
}
export function fetchConvictionCalls(force = false) {
  return fetchJson<{ signals: ConvictionCallSignal[]; generated_at: string; total: number; note?: string; error?: string }>(`/conviction-calls${force ? "?force=1" : ""}`);
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

export interface SignalEvent {
  ticker: string; price: number; type: string;
  icon: string; color: string; msg: string;
}
export function fetchSignalFeed() {
  return fetchJson<{ events: SignalEvent[]; generated_at: string }>("/signal-feed");
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
  mkt_cap_m: number | null;
}
export interface MorningInflowsData {
  standouts: MorningInflowResult[];
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
  signals: MicroCapCall[];
  total:   number;
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
}

export interface NetFlowStreakResult {
  results: NetFlowStreakRow[];
  scanned: number;
  found:   number;
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
