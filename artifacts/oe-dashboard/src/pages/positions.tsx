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

interface TradeRecord {
  trace_id: string;
  ticker: string;
  scan_date: string;
  strategy_family: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  realized_pnl: number;
  return_pct: number;
  holding_days: number;
  exit_reason: string;
  fill_quality?: string;
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

interface PaperPosition {
  id: number;
  ticker: string;
  position_type: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  opened_at: string;
}

export default function PositionsPage() {
  const { apiFetch } = useApi();
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const { data: trades, isLoading: tradesLoading } = useQuery({
    queryKey: ['trade-records'],
    queryFn: () => apiFetch<TradeRecord[]>('/admin/trade-records?limit=50'),
  });

  const { data: allMetrics } = useQuery({
    queryKey: ['options-metrics'],
    queryFn: () => apiFetch<OptionsMetrics[]>('/admin/options-metrics?limit=50'),
  });

  const { data: paperPositions } = useQuery({
    queryKey: ['paper-portfolio'],
    queryFn: () => apiFetch<PaperPosition[]>('/aiem-paper-portfolio'),
  });

  const metrics = selectedTraceId
    ? allMetrics?.filter((m) => m.trace_id === selectedTraceId)
    : allMetrics;

  if (tradesLoading) {
    return (
      <div className="p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-muted rounded w-48" />
          <div className="h-64 bg-muted rounded" />
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Positions & P&L</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Closed trades and options analytics
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Left Panel: Closed Trades */}
        <div className="border border-border rounded-lg bg-card overflow-hidden">
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
                      <TableCell className="font-mono text-xs">
                        {formatDateShort(trade.scan_date)}
                      </TableCell>
                      <TableCell className="text-xs">
                        {trade.strategy_family}
                      </TableCell>
                      <TableCell>
                        <span
                          className={`font-mono text-xs ${
                            trade.direction === 'CALL'
                              ? 'text-chart-2'
                              : 'text-chart-4'
                          }`}
                        >
                          {trade.direction}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {formatCurrency(trade.entry_price)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {formatCurrency(trade.exit_price)}
                      </TableCell>
                      <TableCell
                        className={`font-mono text-xs font-semibold ${
                          trade.realized_pnl >= 0 ? 'text-chart-2' : 'text-chart-4'
                        }`}
                      >
                        {formatCurrency(trade.realized_pnl)}
                      </TableCell>
                      <TableCell
                        className={`font-mono text-xs ${
                          trade.return_pct >= 0 ? 'text-chart-2' : 'text-chart-4'
                        }`}
                      >
                        {formatPercent(trade.return_pct)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
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
        <div className="border border-border rounded-lg bg-card overflow-hidden">
          <div className="p-4 border-b border-border">
            <h2 className="font-semibold">
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
                      <TableCell className="font-mono text-xs">
                        {metric.delta?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {metric.gamma?.toFixed(4) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {metric.theta?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {metric.vega?.toFixed(3) ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {formatCurrency(metric.ev)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {formatPercent(metric.pop)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
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

      {/* Paper Positions */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Active Paper Positions</h2>
        </div>
        {paperPositions && paperPositions.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Position Type</TableHead>
                <TableHead>Quantity</TableHead>
                <TableHead>Entry Price</TableHead>
                <TableHead>Current Price</TableHead>
                <TableHead>Unrealized P&L</TableHead>
                <TableHead>Opened At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paperPositions.map((position) => (
                <TableRow key={position.id} data-testid={`row-paper-${position.id}`}>
                  <TableCell className="font-semibold font-mono">
                    {position.ticker}
                  </TableCell>
                  <TableCell className="text-xs">
                    {position.position_type}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {position.quantity}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatCurrency(position.entry_price)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatCurrency(position.current_price)}
                  </TableCell>
                  <TableCell
                    className={`font-mono text-xs font-semibold ${
                      position.unrealized_pnl >= 0 ? 'text-chart-2' : 'text-chart-4'
                    }`}
                  >
                    {formatCurrency(position.unrealized_pnl)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatDateShort(position.opened_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 active paper positions</p>
          </div>
        )}
      </div>
    </div>
  );
}
