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

export function smartMoneyScan(tickers: string[]) {
  return fetchJson<SmartMoneyResult>("/smart-money/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tickers }),
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
  return fetchJson<{ trades: AITradeSetup[]; generated_at: string; tickers_scanned: number }>("/ai-trades");
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
