import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Download, TrendingUp } from 'lucide-react';
import { formatCurrency, formatPercent } from '@/lib/utils';
import {
  computePerformance,
  downloadTextFile,
  performanceToCsv,
  type PerformanceTrade,
} from '@/lib/performance';
import { Button } from '@/components/ui/button';

function StatCard({
  label,
  value,
  valueClass = 'text-foreground',
  sub,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: string;
}) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 min-w-0">
      <p className="text-sm text-muted-foreground mb-1 font-mono uppercase tracking-wide">
        {label}
      </p>
      <p className={`text-2xl font-bold font-mono ${valueClass}`}>{value}</p>
      {sub ? <p className="text-sm text-muted-foreground mt-1 font-mono">{sub}</p> : null}
    </div>
  );
}

export function PerformancePanel({ trades }: { trades: PerformanceTrade[] }) {
  const summary = computePerformance(trades);
  const chartData = summary.equityCurve.filter((p) => p.index > 0 || summary.tradeCount === 0);
  const equityPositive = summary.totalPnl >= 0;

  const onExport = () => {
    const csv = performanceToCsv(trades, summary);
    const stamp = new Date().toISOString().slice(0, 10);
    downloadTextFile(`oe-performance-report-${stamp}.csv`, csv);
  };

  return (
    <div className="border border-border rounded-lg bg-card overflow-hidden">
      <div className="p-4 border-b border-border flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <TrendingUp className="w-5 h-5 text-primary shrink-0" />
          <div>
            <h2 className="text-base font-semibold text-foreground">Performance Summary</h2>
            <p className="text-sm text-muted-foreground font-mono">
              Closed OE trades · equity curve · expectancy / drawdown
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onExport}
          className="font-mono gap-2 shrink-0"
          data-testid="button-export-performance"
        >
          <Download className="w-4 h-4" />
          Export report CSV
        </Button>
      </div>

      <div className="p-4 md:p-5 space-y-5">
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <StatCard
            label="Total P&L"
            value={formatCurrency(summary.totalPnl)}
            valueClass={equityPositive ? 'text-chart-2' : 'text-chart-4'}
            sub={`${summary.tradeCount} closed`}
          />
          <StatCard
            label="Win Rate"
            value={
              summary.winRate == null ? '—' : formatPercent(summary.winRate)
            }
            valueClass="text-primary"
            sub={`W${summary.wins} / L${summary.losses}`}
          />
          <StatCard
            label="Expectancy"
            value={
              summary.expectancy == null ? '—' : formatCurrency(summary.expectancy)
            }
            valueClass={
              summary.expectancy == null
                ? 'text-muted-foreground'
                : summary.expectancy >= 0
                  ? 'text-chart-2'
                  : 'text-chart-4'
            }
            sub="/ trade"
          />
          <StatCard
            label="Profit Factor"
            value={
              summary.profitFactor == null
                ? '—'
                : summary.profitFactor === Number.POSITIVE_INFINITY
                  ? '∞'
                  : summary.profitFactor.toFixed(2)
            }
          />
          <StatCard
            label="Max Drawdown"
            value={formatCurrency(summary.maxDrawdown)}
            valueClass={summary.maxDrawdown > 0 ? 'text-chart-4' : 'text-muted-foreground'}
            sub={
              summary.maxDrawdownPct == null
                ? undefined
                : formatPercent(summary.maxDrawdownPct)
            }
          />
          <StatCard
            label="Avg Hold"
            value={
              summary.avgHoldingDays == null ? '—' : `${summary.avgHoldingDays}d`
            }
            sub={
              summary.bestTrade == null
                ? undefined
                : `best ${formatCurrency(summary.bestTrade)}`
            }
          />
        </div>

        <div className="border border-border rounded-md bg-muted/20 p-3 md:p-4">
          <div className="flex items-center justify-between mb-3 gap-2">
            <h3 className="text-sm font-semibold font-mono uppercase tracking-wide text-muted-foreground">
              Equity Curve
            </h3>
            <span className="text-sm font-mono text-muted-foreground">
              {summary.tradeCount === 0
                ? 'no closed trades yet'
                : `ending ${formatCurrency(summary.totalPnl)}`}
            </span>
          </div>

          {summary.tradeCount === 0 ? (
            <div className="h-48 flex flex-col items-center justify-center text-center px-4 gap-2">
              <p className="text-base text-muted-foreground font-medium">
                No closed OE trades to chart yet
              </p>
              <p className="text-sm text-muted-foreground max-w-lg">
                When the pipeline completes trades end-to-end (DONE → graded exit),
                this curve will show cumulative realized P&L over time. Export still
                downloads a report template with today’s empty summary.
              </p>
            </div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="oeEquityFill" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="0%"
                        stopColor={
                          equityPositive ? 'hsl(var(--chart-2))' : 'hsl(var(--chart-4))'
                        }
                        stopOpacity={0.35}
                      />
                      <stop
                        offset="100%"
                        stopColor={
                          equityPositive ? 'hsl(var(--chart-2))' : 'hsl(var(--chart-4))'
                        }
                        stopOpacity={0.02}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="label"
                    stroke="hsl(var(--muted-foreground))"
                    tick={{ fontSize: 12, fontFamily: 'var(--app-font-mono)' }}
                    minTickGap={24}
                  />
                  <YAxis
                    stroke="hsl(var(--muted-foreground))"
                    tick={{ fontSize: 12, fontFamily: 'var(--app-font-mono)' }}
                    width={64}
                    tickFormatter={(v) => `$${v}`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--card))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: '6px',
                      fontFamily: 'var(--app-font-mono)',
                      fontSize: '12px',
                    }}
                    formatter={(value: number, name: string) => {
                      if (name === 'equity') return [formatCurrency(value), 'Equity'];
                      if (name === 'tradePnl') return [formatCurrency(value), 'Trade P&L'];
                      return [value, name];
                    }}
                    labelFormatter={(_, payload) => {
                      const p = payload?.[0]?.payload;
                      if (!p) return '';
                      return `${p.ticker || '—'} · ${p.label}`;
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke={
                      equityPositive ? 'hsl(var(--chart-2))' : 'hsl(var(--chart-4))'
                    }
                    fill="url(#oeEquityFill)"
                    strokeWidth={2.5}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {summary.tradeCount > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm font-mono">
            <div className="border border-border rounded-md p-3">
              <div className="text-muted-foreground uppercase text-xs mb-1">Avg Win</div>
              <div className="text-chart-2 font-semibold">
                {summary.avgWin == null ? '—' : formatCurrency(summary.avgWin)}
              </div>
            </div>
            <div className="border border-border rounded-md p-3">
              <div className="text-muted-foreground uppercase text-xs mb-1">Avg Loss</div>
              <div className="text-chart-4 font-semibold">
                {summary.avgLoss == null ? '—' : formatCurrency(-Math.abs(summary.avgLoss))}
              </div>
            </div>
            <div className="border border-border rounded-md p-3">
              <div className="text-muted-foreground uppercase text-xs mb-1">Best</div>
              <div className="text-foreground font-semibold">
                {summary.bestTrade == null ? '—' : formatCurrency(summary.bestTrade)}
              </div>
            </div>
            <div className="border border-border rounded-md p-3">
              <div className="text-muted-foreground uppercase text-xs mb-1">Worst</div>
              <div className="text-foreground font-semibold">
                {summary.worstTrade == null ? '—' : formatCurrency(summary.worstTrade)}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
