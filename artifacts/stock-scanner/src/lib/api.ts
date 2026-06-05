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

export interface StockAnalysis {
  ticker: string;
  info: {
    name: string;
    sector: string;
    industry: string;
    market_cap?: number;
    pe_ratio?: number;
    forward_pe?: number;
    dividend_yield?: number;
    beta?: number;
    description?: string;
  };
  indicators: {
    price?: number;
    price_change?: number;
    price_change_pct?: number;
    rsi?: number;
    macd?: number;
    macd_signal?: number;
    macd_hist?: number;
    bb_upper?: number;
    bb_mid?: number;
    bb_lower?: number;
    sma50?: number;
    sma200?: number;
    volume?: number;
    avg_volume_20?: number;
    volume_ratio?: number;
    atr?: number;
    momentum?: number;
    high_52w?: number;
    low_52w?: number;
    pct_from_52w_high?: number;
  };
  score: {
    score: number;
    rating: string;
    breakdown: { factor: string; points: number; max: number; label: string; value: number }[];
  };
  ml: {
    probability_up: number;
    probability_down: number;
    direction: string;
    confidence: string;
    model_accuracy?: number;
  };
  history: {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }[];
}

export interface ScanResult {
  ticker: string;
  name: string;
  sector: string;
  price?: number;
  price_change_pct?: number;
  rsi?: number;
  volume_ratio?: number;
  score?: number;
  rating?: string;
  direction?: string;
  prob_up?: number;
  error?: string;
}

export interface Portfolio {
  cash: number;
  positions_value: number;
  total_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  positions: Position[];
  trades: Trade[];
}

export interface Position {
  ticker: string;
  shares: number;
  avg_cost: number;
  current_price: number;
  value: number;
  cost_basis: number;
  pnl: number;
  pnl_pct: number;
}

export interface Trade {
  type: string;
  ticker: string;
  shares: number;
  price: number;
  total: number;
  date: string;
}

export interface TradeResult {
  success?: boolean;
  error?: string;
  message?: string;
  cash_remaining?: number;
}

export interface BacktestResult {
  ticker: string;
  initial_cash: number;
  final_value: number;
  total_return_pct: number;
  buy_hold_return_pct: number;
  alpha: number;
  n_trades: number;
  win_rate: number;
  max_drawdown_pct: number;
  buy_threshold: number;
  sell_threshold: number;
  trades: BacktestTrade[];
  equity_curve: EquityPoint[];
}

export interface BacktestTrade {
  type: string;
  date: string;
  price: number;
  shares: number;
  score: number;
  pnl?: number;
  pnl_pct?: number;
}

export interface EquityPoint {
  date: string;
  value: number;
  close?: number;
  in_position?: boolean;
}

export interface Alert {
  id: number;
  ticker: string;
  type: string;
  value: number;
  direction: string;
  triggered: boolean;
  created: string;
  triggered_at?: string;
  triggered_value?: number;
}
