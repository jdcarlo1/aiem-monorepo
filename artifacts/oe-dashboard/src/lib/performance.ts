/** Closed-trade performance helpers for the Options Engine terminal. */

export type PerformanceTrade = {
  trace_id: string;
  ticker: string;
  scan_date: string;
  strategy_family?: string | null;
  direction?: string | null;
  entry_price?: number | null;
  exit_price?: number | null;
  exit_ts?: string | null;
  realized_pnl: number | null | undefined;
  return_pct?: number | null;
  holding_days?: number | null;
  exit_reason?: string | null;
};

export type EquityPoint = {
  index: number;
  date: string;
  label: string;
  ticker: string;
  tradePnl: number;
  equity: number;
};

export type PerformanceSummary = {
  tradeCount: number;
  wins: number;
  losses: number;
  scratches: number;
  winRate: number | null;
  totalPnl: number;
  avgPnl: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  expectancy: number | null;
  profitFactor: number | null;
  maxDrawdown: number;
  maxDrawdownPct: number | null;
  avgHoldingDays: number | null;
  bestTrade: number | null;
  worstTrade: number | null;
  equityCurve: EquityPoint[];
};

function toNum(v: unknown): number {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function sortKey(t: PerformanceTrade): number {
  const raw = t.exit_ts || t.scan_date || '';
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : 0;
}

function shortDate(iso: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function computePerformance(
  trades: PerformanceTrade[],
  startingEquity = 0
): PerformanceSummary {
  const closed = [...trades]
    .filter((t) => t.exit_ts != null && t.exit_ts !== '')
    .sort((a, b) => sortKey(a) - sortKey(b));

  let equity = startingEquity;
  let peak = startingEquity;
  let maxDrawdown = 0;
  const curve: EquityPoint[] = [
    {
      index: 0,
      date: '',
      label: 'Start',
      ticker: '',
      tradePnl: 0,
      equity: startingEquity,
    },
  ];

  let wins = 0;
  let losses = 0;
  let scratches = 0;
  let sumWin = 0;
  let sumLoss = 0; // absolute value of losses
  let sumHold = 0;
  let holdN = 0;
  let best: number | null = null;
  let worst: number | null = null;

  closed.forEach((t, i) => {
    const pnl = toNum(t.realized_pnl);
    equity += pnl;
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDrawdown) maxDrawdown = dd;

    if (pnl > 0) {
      wins += 1;
      sumWin += pnl;
    } else if (pnl < 0) {
      losses += 1;
      sumLoss += Math.abs(pnl);
    } else {
      scratches += 1;
    }

    if (best === null || pnl > best) best = pnl;
    if (worst === null || pnl < worst) worst = pnl;

    if (t.holding_days != null && Number.isFinite(Number(t.holding_days))) {
      sumHold += Number(t.holding_days);
      holdN += 1;
    }

    const date = t.exit_ts || t.scan_date || '';
    curve.push({
      index: i + 1,
      date,
      label: shortDate(date),
      ticker: t.ticker,
      tradePnl: pnl,
      equity: Math.round(equity * 100) / 100,
    });
  });

  const tradeCount = closed.length;
  const decided = wins + losses;
  const totalPnl = equity - startingEquity;
  const winRate = decided > 0 ? wins / decided : null;
  const avgPnl = tradeCount > 0 ? totalPnl / tradeCount : null;
  const avgWin = wins > 0 ? sumWin / wins : null;
  const avgLoss = losses > 0 ? sumLoss / losses : null;
  const expectancy =
    winRate != null && avgWin != null && avgLoss != null
      ? winRate * avgWin - (1 - winRate) * avgLoss
      : avgPnl;
  const profitFactor =
    sumLoss > 0 ? sumWin / sumLoss : sumWin > 0 ? Number.POSITIVE_INFINITY : null;
  const maxDrawdownPct =
    peak > 0 ? maxDrawdown / peak : startingEquity === 0 && maxDrawdown > 0 ? null : 0;

  return {
    tradeCount,
    wins,
    losses,
    scratches,
    winRate,
    totalPnl: Math.round(totalPnl * 100) / 100,
    avgPnl: avgPnl == null ? null : Math.round(avgPnl * 100) / 100,
    avgWin: avgWin == null ? null : Math.round(avgWin * 100) / 100,
    avgLoss: avgLoss == null ? null : Math.round(avgLoss * 100) / 100,
    expectancy: expectancy == null ? null : Math.round(expectancy * 100) / 100,
    profitFactor:
      profitFactor == null
        ? null
        : profitFactor === Number.POSITIVE_INFINITY
          ? Number.POSITIVE_INFINITY
          : Math.round(profitFactor * 100) / 100,
    maxDrawdown: Math.round(maxDrawdown * 100) / 100,
    maxDrawdownPct,
    avgHoldingDays: holdN > 0 ? Math.round((sumHold / holdN) * 10) / 10 : null,
    bestTrade: best,
    worstTrade: worst,
    equityCurve: curve,
  };
}

export function performanceToCsv(
  trades: PerformanceTrade[],
  summary: PerformanceSummary
): string {
  const lines: string[] = [];
  lines.push('OPTIONS ENGINE — PERFORMANCE REPORT');
  lines.push(`generated_at,${new Date().toISOString()}`);
  lines.push('');
  lines.push('SUMMARY');
  lines.push(`trade_count,${summary.tradeCount}`);
  lines.push(`wins,${summary.wins}`);
  lines.push(`losses,${summary.losses}`);
  lines.push(`scratches,${summary.scratches}`);
  lines.push(
    `win_rate,${summary.winRate == null ? '' : (summary.winRate * 100).toFixed(2) + '%'}`
  );
  lines.push(`total_pnl,${summary.totalPnl}`);
  lines.push(`avg_pnl,${summary.avgPnl ?? ''}`);
  lines.push(`expectancy,${summary.expectancy ?? ''}`);
  lines.push(
    `profit_factor,${
      summary.profitFactor == null
        ? ''
        : summary.profitFactor === Number.POSITIVE_INFINITY
          ? 'Inf'
          : summary.profitFactor
    }`
  );
  lines.push(`max_drawdown,${summary.maxDrawdown}`);
  lines.push(
    `max_drawdown_pct,${
      summary.maxDrawdownPct == null
        ? ''
        : (summary.maxDrawdownPct * 100).toFixed(2) + '%'
    }`
  );
  lines.push(`avg_holding_days,${summary.avgHoldingDays ?? ''}`);
  lines.push(`best_trade,${summary.bestTrade ?? ''}`);
  lines.push(`worst_trade,${summary.worstTrade ?? ''}`);
  lines.push('');
  lines.push('EQUITY_CURVE');
  lines.push('index,date,ticker,trade_pnl,equity');
  for (const p of summary.equityCurve) {
    lines.push(
      [p.index, csvEscape(p.date), csvEscape(p.ticker), p.tradePnl, p.equity].join(',')
    );
  }
  lines.push('');
  lines.push('CLOSED_TRADES');
  lines.push(
    'trace_id,ticker,scan_date,exit_ts,strategy_family,direction,entry_price,exit_price,realized_pnl,return_pct,holding_days,exit_reason'
  );
  const closed = [...trades]
    .filter((t) => t.exit_ts != null && t.exit_ts !== '')
    .sort((a, b) => sortKey(a) - sortKey(b));
  for (const t of closed) {
    lines.push(
      [
        csvEscape(t.trace_id),
        csvEscape(t.ticker),
        csvEscape(t.scan_date),
        csvEscape(t.exit_ts || ''),
        csvEscape(t.strategy_family || ''),
        csvEscape(t.direction || ''),
        t.entry_price ?? '',
        t.exit_price ?? '',
        t.realized_pnl ?? '',
        t.return_pct ?? '',
        t.holding_days ?? '',
        csvEscape(t.exit_reason || ''),
      ].join(',')
    );
  }
  return lines.join('\n');
}

function csvEscape(v: string): string {
  if (v.includes(',') || v.includes('"') || v.includes('\n')) {
    return `"${v.replace(/"/g, '""')}"`;
  }
  return v;
}

export function downloadTextFile(filename: string, contents: string, mime = 'text/csv') {
  const blob = new Blob([contents], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
