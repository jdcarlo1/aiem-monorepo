import { useParams } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { formatCurrency, formatPercent } from '@/lib/utils';

interface IndicatorSnapshot {
  canonical_id: string;
  normalized_value: number;
  contribution_score: number;
  weight: number;
  quality_status: string;
  signal_direction: string;
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
  if (Array.isArray(r?.snapshots)) return r.snapshots as T[];
  return [];
}

export default function WhyTradePage() {
  const params = useParams();
  const traceId = params.traceId;
  const { apiFetch } = useApi();

  const { data: indicators, isLoading: indicatorsLoading } = useQuery({
    queryKey: ['indicator-snapshots', traceId],
    queryFn: () =>
      apiFetch<unknown>(
        `/admin/indicator-snapshots?trace_id=${traceId}`
      ).then(extractRows<IndicatorSnapshot>),
    enabled: !!traceId,
  });

  const { data: metrics } = useQuery({
    queryKey: ['options-metrics-why', traceId],
    queryFn: () =>
      apiFetch<unknown>(`/admin/options-metrics?trace_id=${traceId}`)
        .then(extractRows<OptionsMetrics>),
    enabled: !!traceId,
  });

  const sortedIndicators = indicators
    ? [...indicators].sort((a, b) => b.contribution_score - a.contribution_score)
    : [];

  const chartData = sortedIndicators.map((ind) => ({
    name: ind.canonical_id,
    contribution: ind.contribution_score,
    normalized: ind.normalized_value,
    quality: ind.quality_status,
  }));

  if (indicatorsLoading) {
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
        <h1 className="text-2xl font-bold text-foreground">Why This Trade</h1>
        <p className="text-sm text-muted-foreground mt-1 font-mono">
          Trace ID: {traceId}
        </p>
      </div>

      {/* Indicator Contribution Chart */}
      <div className="border border-border rounded-lg bg-card p-6">
        <h2 className="font-semibold mb-4">Indicator Contributions</h2>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={chartData} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis type="number" stroke="hsl(var(--muted-foreground))" />
              <YAxis
                type="category"
                dataKey="name"
                width={150}
                stroke="hsl(var(--muted-foreground))"
                style={{ fontSize: '11px', fontFamily: 'var(--app-font-mono)' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '4px',
                  fontFamily: 'var(--app-font-mono)',
                  fontSize: '12px',
                }}
                labelStyle={{ color: 'hsl(var(--foreground))' }}
              />
              <Bar dataKey="contribution" radius={[0, 4, 4, 0]}>
                {chartData.map((entry, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={
                      entry.quality === 'OK'
                        ? 'hsl(var(--chart-1))'
                        : 'hsl(var(--chart-3))'
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-64 flex items-center justify-center">
            <p className="text-muted-foreground">No indicator data found</p>
          </div>
        )}
      </div>

      {/* Indicator Details Table */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Indicator Details</h2>
        </div>
        {sortedIndicators.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Canonical ID</TableHead>
                <TableHead>Normalized Value</TableHead>
                <TableHead>Contribution</TableHead>
                <TableHead>Weight</TableHead>
                <TableHead>Quality</TableHead>
                <TableHead>Signal</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedIndicators.map((indicator, idx) => (
                <TableRow key={idx} data-testid={`row-indicator-${idx}`}>
                  <TableCell className="font-mono text-xs">
                    {indicator.canonical_id}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {indicator.normalized_value?.toFixed(4) ?? '—'}
                  </TableCell>
                  <TableCell className="font-mono text-xs font-semibold">
                    {indicator.contribution_score?.toFixed(4) ?? '—'}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {indicator.weight?.toFixed(4) ?? '—'}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        indicator.quality_status === 'OK' ? 'success' : 'warning'
                      }
                    >
                      {indicator.quality_status}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {indicator.signal_direction}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 indicators found</p>
          </div>
        )}
      </div>

      {/* Full Greeks for this Decision */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Options Metrics (Greeks)</h2>
        </div>
        {metrics && metrics.length > 0 ? (
          <div className="p-4 grid grid-cols-4 gap-4">
            {metrics.map((metric, idx) => (
              <div key={idx} className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Delta</p>
                    <p className="font-mono text-sm">{metric.delta?.toFixed(3)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Gamma</p>
                    <p className="font-mono text-sm">{metric.gamma?.toFixed(4)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Theta</p>
                    <p className="font-mono text-sm">{metric.theta?.toFixed(3)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Vega</p>
                    <p className="font-mono text-sm">{metric.vega?.toFixed(3)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Rho</p>
                    <p className="font-mono text-sm">{metric.rho?.toFixed(3)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">EV</p>
                    <p className="font-mono text-sm">{formatCurrency(metric.ev)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">PoP</p>
                    <p className="font-mono text-sm">{formatPercent(metric.pop)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">RoR</p>
                    <p className="font-mono text-sm">
                      {metric.return_on_risk?.toFixed(2)}
                    </p>
                  </div>
                  {metric.iv && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">IV</p>
                      <p className="font-mono text-sm">{formatPercent(metric.iv)}</p>
                    </div>
                  )}
                  {metric.iv_percentile && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">
                        IV Percentile
                      </p>
                      <p className="font-mono text-sm">
                        {metric.iv_percentile.toFixed(1)}%
                      </p>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">No Greeks data found</p>
          </div>
        )}
      </div>

      <div className="p-4 bg-muted/30 border border-border rounded text-xs text-muted-foreground">
        Note: Decision audit linked by timestamp proximity (no direct join exists in schema)
      </div>
    </div>
  );
}
