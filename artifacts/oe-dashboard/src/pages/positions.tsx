import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import { Link } from 'wouter';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCurrency, formatPercent, formatDateShort } from '@/lib/utils';
import { ExternalLink } from 'lucide-react';
import { PerformancePanel } from '@/components/performance-panel';

interface TradeRecord {
  trace_id: string;
  ticker: string;
  scan_date: string;
  strategy_family: string;
  direction: string;
  entry_price: number;
  exit_price: number | null;
  exit_ts: string | null;
  realized_pnl: number;
  return_pct: number;
  holding_days: number | null;
  exit_reason: string | null;
  fill_quality?: string | null;
}

interface OptionsMetrics {
  trace_id: string;
  ticker: string;
  scan_date: string;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  vanna?: number;
  charm?: number;
  ev: number;
  pop: number;
  return_on_risk: number;
  max_profit?: number;
  max_loss?: number;
  breakeven?: number;
  iv?: number;
  iv_percentile?: number;
}

// ── Response shape normalisers ────────────────────────────────────────────────
function extractRows<T>(resp: unknown): T[] {
  if (Array.isArray(resp)) return resp as T[];
  const r = resp as Record<string, unknown>;
  if (Array.isArray(r?.rows)) return r.rows as T[];
  if (Array.isArray(r?.positions)) return r.positions as T[];
  if (Array.isArray(r?.trades)) return r.trades as T[];
  return [];
}

function isOpenTrade(t: TradeRecord): boolean {
  return t.exit_ts === null || t.exit_ts === undefined;
}

export default function PositionsPage() {
  const { apiFetch } = useApi();
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  // OE SKU only — oe_trade_records. AIEM equity paper lives on /aiem/ (separate product).
  // Same login password as AIEM; books are not mixed in the UI (Phase 0 product honesty).
  const { data: allTrades, isLoading: tradesLoading } = useQuery({
    queryKey: ['trade-records-all'],
    queryFn: () =>
      apiFetch<unknown>('/admin/trade-records?limit=500').then(extractRows<TradeRecord>),
  });

  const trades = (allTrades ?? []).filter((t) => !isOpenTrade(t));
  const openOePositions = (allTrades ?? []).filter(isOpenTrade);

  const { data: allMetrics } = useQuery({
    queryKey: ['options-metrics'],
    queryFn: () =>
      apiFetch<unknown>('/admin/options-metrics?limit=200').then(extractRows<OptionsMetrics>),
  });

  const metrics = selectedTraceId
    ? allMetrics?.filter((m) => m.trace_id === selectedTraceId)
    : allMetrics;

  if (tradesLoading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 bg-muted rounded w-48" />
        <div className="h-64 bg-muted rounded" />
      </div>
    );
  }

  return (
    <>
      <div className="border-b border-border pb-5">
        <h1 className="text-3xl font-bold text-foreground tracking-tight">
          Positions & P&L
        </h1>
        <p className="text-base text-muted-foreground mt-1.5">
          Options Engine book only · AIEM equity paper is a separate product
        </p>
      </div>

      <PerformancePanel trades={trades ?? []} />

      {/* Stack on smaller screens — side-by-side 10-col tables were jammed */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Left Panel: Closed Trades */}
        <div className="border border-border rounded-lg bg-card overflow-hidden min-w-0">
          <div className="p-4 border-b border-border">
            <h2 className="font-semibold">Closed Trades</h2>
          </div>
          {trades && trades.length > 0 ? (
            <div className="overflow-auto max-h-[600px]">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ticker</TableHead>
                    <TableHead>Date</TableHead>
                    <TableHead>Strategy</TableHead>
                    <TableHead>Dir</TableHead>
                    <TableHead>Entry</TableHead>
                    <TableHead>Exit</TableHead>
                    <TableHead>P&L</TableHead>
                    <TableHead>Return</TableHead>
                    <TableHead>Days</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {trades.map((trade) => (
                    <TableRow
                      key={trade.trace_id}
                      className={`cursor-pointer ${
                        selectedTraceId === trade.trace_id ? 'bg-muted' : ''
                      }`}
                      onClick={() => setSelectedTraceId(trade.trace_id)}
                      data-testid={`row-trade-${trade.trace_id}`}
                    >
                      <TableCell className="font-semibold font-mono">
                        {trade.ticker}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatDateShort(trade.scan_date)}
                      </TableCell>
                      <TableCell className="text-sm">
                        {trade.strategy_family}
                      </TableCell>
                      <TableCell>
                        <span
                          className={`font-mono text-sm ${
                            trade.direction === 'CALL'
                              ? 'text-chart-2'
                              : 'text-chart-4'
                          }`}
                        >
                          {trade.direction}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatCurrency(trade.entry_price)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatCurrency(trade.exit_price)}
                      </TableCell>
                      <TableCell
                        className={`font-mono text-sm font-semibold ${
                          trade.realized_pnl >= 0 ? 'text-chart-2' : 'text-chart-4'
                        }`}
                      >
                        {formatCurrency(trade.realized_pnl)}
                      </TableCell>
                      <TableCell
                        className={`font-mono text-sm ${
                          trade.return_pct >= 0 ? 'text-chart-2' : 'text-chart-4'
                        }`}
                      >
                        {formatPercent(trade.return_pct)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {trade.holding_days}d
                      </TableCell>
                      <TableCell>
                        <Link
                          href={`/why/${trade.trace_id}`}
                          className="text-primary hover:text-primary/80"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </Link>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="p-12 text-center">
              <p className="text-muted-foreground">0 closed trades found</p>
            </div>
          )}
        </div>

        {/* Right Panel: Greeks */}
        <div className="border border-border rounded-lg bg-card overflow-hidden min-w-0">
          <div className="p-4 border-b border-border">
            <h2 className="font-semibold truncate">
              Greeks {selectedTraceId && `(${selectedTraceId})`}
            </h2>
          </div>
          {metrics && metrics.length > 0 ? (
            <div className="overflow-auto max-h-[600px]">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ticker</TableHead>
                    <TableHead>Delta</TableHead>
                    <TableHead>Gamma</TableHead>
                    <TableHead>Theta</TableHead>
                    <TableHead>Vega</TableHead>
                    <TableHead>EV</TableHead>
                    <TableHead>PoP</TableHead>
                    <TableHead>RoR</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {metrics.map((metric) => (
                    <TableRow
                      key={`${metric.trace_id}-${metric.ticker}`}
                      data-testid={`row-metric-${metric.trace_id}`}
                    >
                      <TableCell className="font-semibold font-mono">
                        {metric.ticker}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {metric.delta?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {metric.gamma?.toFixed(4) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {metric.theta?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {metric.vega?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatCurrency(metric.ev)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatPercent(metric.pop)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {metric.return_on_risk?.toFixed(2) ?? '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="p-12 text-center">
              <p className="text-muted-foreground">
                {selectedTraceId
                  ? 'No metrics found for this trace_id'
                  : '0 metrics records found'}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Open OE positions from oe_trade_records (not AIEM equity paper) */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border flex items-center justify-between gap-3">
          <h2 className="font-semibold">Open OE Positions</h2>
          <span className="text-xs text-muted-foreground font-mono">
            {openOePositions.length} open · oe_trade_records
          </span>
        </div>
        {openOePositions.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead>Dir</TableHead>
                <TableHead>Entry</TableHead>
                <TableHead>Scan</TableHead>
                <TableHead>Trace</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {openOePositions.map((position) => (
                <TableRow key={position.trace_id} data-testid={`row-oe-open-${position.trace_id}`}>
                  <TableCell className="font-semibold font-mono">
                    {position.ticker}
                  </TableCell>
                  <TableCell className="text-sm">
                    {position.strategy_family || '—'}
                  </TableCell>
                  <TableCell className="text-sm font-mono">
                    {position.direction || '—'}
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {position.entry_price != null ? formatCurrency(position.entry_price) : '—'}
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {position.scan_date || '—'}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {position.trace_id}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center space-y-2">
            <p className="text-muted-foreground">0 open OE positions</p>
            <p className="text-xs text-muted-foreground font-mono">
              AIEM equity paper book is on /aiem/ — same password, separate product
            </p>
          </div>
        )}
      </div>
    </>
  );
}
