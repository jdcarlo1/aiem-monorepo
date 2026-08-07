import { describe, expect, it } from 'vitest';
import { computePerformance, performanceToCsv } from '../performance';

describe('computePerformance', () => {
  it('builds equity curve, win rate, expectancy, and drawdown', () => {
    const summary = computePerformance([
      {
        trace_id: 'a',
        ticker: 'AAA',
        scan_date: '2026-08-01',
        exit_ts: '2026-08-02T15:00:00Z',
        realized_pnl: 100,
        holding_days: 1,
      },
      {
        trace_id: 'b',
        ticker: 'BBB',
        scan_date: '2026-08-03',
        exit_ts: '2026-08-04T15:00:00Z',
        realized_pnl: -40,
        holding_days: 1,
      },
      {
        trace_id: 'c',
        ticker: 'CCC',
        scan_date: '2026-08-05',
        exit_ts: '2026-08-06T15:00:00Z',
        realized_pnl: 20,
        holding_days: 1,
      },
    ]);

    expect(summary.tradeCount).toBe(3);
    expect(summary.wins).toBe(2);
    expect(summary.losses).toBe(1);
    expect(summary.winRate).toBeCloseTo(2 / 3, 5);
    expect(summary.totalPnl).toBe(80);
    expect(summary.expectancy).toBeCloseTo(80 / 3, 1);
    expect(summary.profitFactor).toBeCloseTo(120 / 40, 5);
    expect(summary.maxDrawdown).toBe(40);
    expect(summary.equityCurve.map((p) => p.equity)).toEqual([0, 100, 60, 80]);
  });

  it('ignores open trades without exit_ts', () => {
    const summary = computePerformance([
      {
        trace_id: 'open',
        ticker: 'OPEN',
        scan_date: '2026-08-06',
        exit_ts: null,
        realized_pnl: 999,
      },
    ]);
    expect(summary.tradeCount).toBe(0);
    expect(summary.totalPnl).toBe(0);
  });

  it('exports a CSV report with summary + trades', () => {
    const trades = [
      {
        trace_id: 'a',
        ticker: 'AAA',
        scan_date: '2026-08-01',
        exit_ts: '2026-08-02T15:00:00Z',
        realized_pnl: 50,
        return_pct: 0.1,
        direction: 'CALL',
        strategy_family: 'OE',
      },
    ];
    const summary = computePerformance(trades);
    const csv = performanceToCsv(trades, summary);
    expect(csv).toContain('PERFORMANCE REPORT');
    expect(csv).toContain('win_rate');
    expect(csv).toContain('EQUITY_CURVE');
    expect(csv).toContain('CLOSED_TRADES');
    expect(csv).toContain('AAA');
  });
});
