import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import { FlaskConical, TrendingUp, TrendingDown, Activity } from 'lucide-react';

type F3Snap = {
  pattern?: string;
  account_balance_usd?: number;
  net_liquidation_usd?: number;
  total_trades?: number;
  wins?: number;
  losses?: number;
  win_rate_pct?: number;
  profit_rate_pct?: number;
  pm_direction?: string | null;
  orb_high?: number | null;
  orb_low?: number | null;
  signal_state?: { status?: string; note?: string; direction?: string };
  active_position?: {
    direction?: string;
    side?: string;
    contracts?: number;
    shares?: number;
    option_symbol?: string;
    symbol?: string;
    entry?: number;
    entry_premium?: number;
    stop?: number;
    strike?: number;
    mark_premium?: number;
    unrealized_pnl?: number;
  } | null;
  recent_trades?: Array<{
    direction?: string;
    pnl_usd?: number;
    result?: string;
    reason?: string;
    thin_exit?: boolean;
  }>;
  rules?: Record<string, unknown>;
};

type Snapshot = {
  f3?: F3Snap;
  gap_fill?: unknown;
  orb?: unknown;
  error?: string;
};

export default function StrategiesPage() {
  const { apiFetch } = useApi();

  const { data, isLoading, isError, error, dataUpdatedAt } = useQuery({
    queryKey: ['pattern-lab-f3'],
    queryFn: () => apiFetch<Snapshot>('/pattern-lab/snapshot'),
    refetchInterval: 30_000,
    retry: false,
  });

  const f3 = data?.f3;
  const pos = f3?.active_position;
  const profit = f3?.profit_rate_pct ?? 0;
  const profitColor =
    profit > 0 ? 'text-chart-2' : profit < 0 ? 'text-destructive' : 'text-muted-foreground';
  const dir = pos?.direction || pos?.side;
  const qty = pos?.contracts ?? pos?.shares;

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex justify-between items-end border-b border-border pb-4">
        <div>
          <h1 className="text-2xl font-mono font-bold tracking-tight uppercase">
            Strategies
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Live paper strategies on the Options Engine terminal — F3 SPY 0DTE
          </p>
        </div>
        <div className="font-mono text-xs text-muted-foreground text-right">
          <div>POLL 30s</div>
          <div className="text-primary">
            {isLoading ? 'LOADING…' : isError ? 'ERROR' : 'LIVE'}
          </div>
          {dataUpdatedAt ? (
            <div>{new Date(dataUpdatedAt).toLocaleTimeString()}</div>
          ) : null}
        </div>
      </div>

      {isError ? (
        <div className="border border-destructive/40 bg-destructive/10 p-3 font-mono text-xs text-destructive">
          {String((error as Error)?.message || error)}
        </div>
      ) : null}

      <div className="border border-border bg-card">
        <div className="p-3 border-b border-border bg-sidebar/40 flex justify-between items-center">
          <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2 uppercase">
            <FlaskConical size={14} /> F3 SPY 0DTE
          </h2>
          <span className="text-[10px] font-mono text-muted-foreground uppercase">
            {f3?.pattern || 'F3_SPY_0DTE'}
          </span>
        </div>

        <div className="p-4 space-y-4">
          <p className="font-mono text-xs text-muted-foreground leading-relaxed">
            Premarket direction → ORB 9:30–9:44 → breakout with PM → buy ATM CALL/PUT
            ($200 notional) → auto-sell at −65% premium stop, else exit 16:00 ET.
            No profit target. Real Tradier premiums when available (no synthetic leverage).
          </p>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric
              label="Account"
              value={`$${(f3?.account_balance_usd ?? 10000).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}`}
            />
            <Metric
              label="NLV"
              value={`$${(f3?.net_liquidation_usd ?? 10000).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}`}
              valueClass="text-secondary"
            />
            <Metric
              label="Win Rate"
              value={`${(f3?.win_rate_pct ?? 0).toFixed(2)}%`}
              valueClass="text-primary"
            />
            <Metric
              label="Profit Rate"
              value={`${profit.toFixed(2)}%`}
              valueClass={profitColor}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-xs">
            <div className="border border-border p-3">
              <div className="text-muted-foreground uppercase text-[10px]">Premarket</div>
              <div className="text-white mt-1">{f3?.pm_direction || '—'}</div>
            </div>
            <div className="border border-border p-3">
              <div className="text-muted-foreground uppercase text-[10px]">ORB High</div>
              <div className="text-white mt-1">
                {f3?.orb_high != null ? `$${Number(f3.orb_high).toFixed(2)}` : '—'}
              </div>
            </div>
            <div className="border border-border p-3">
              <div className="text-muted-foreground uppercase text-[10px]">ORB Low</div>
              <div className="text-white mt-1">
                {f3?.orb_low != null ? `$${Number(f3.orb_low).toFixed(2)}` : '—'}
              </div>
            </div>
            <div className="border border-border p-3">
              <div className="text-muted-foreground uppercase text-[10px]">Signal</div>
              <div className="text-primary mt-1">{f3?.signal_state?.status || '—'}</div>
            </div>
          </div>

          {f3?.signal_state?.note ? (
            <div className="font-mono text-[11px] text-muted-foreground border border-border p-2">
              {f3.signal_state.note}
            </div>
          ) : null}

          <div className="border border-border bg-black/20 p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2 flex items-center gap-2">
              <Activity size={12} /> Active Position
            </div>
            {pos ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 font-mono text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  {dir === 'CALL' || dir === 'LONG' ? (
                    <TrendingUp size={14} className="text-chart-2" />
                  ) : (
                    <TrendingDown size={14} className="text-destructive" />
                  )}
                  <span
                    className={
                      dir === 'CALL' || dir === 'LONG' ? 'text-chart-2' : 'text-destructive'
                    }
                  >
                    {dir}
                  </span>
                  <span className="text-foreground">
                    {qty != null ? `${qty} ctr` : ''}
                  </span>
                  <span className="text-muted-foreground">
                    {pos.option_symbol || pos.symbol}
                  </span>
                </div>
                <div className="text-muted-foreground text-xs space-y-0.5 md:text-right">
                  <div>
                    ENTRY{' '}
                    <span className="text-foreground">
                      ${Number(pos.entry_premium ?? pos.entry ?? 0).toFixed(3)}
                    </span>
                  </div>
                  {pos.strike != null ? (
                    <div>
                      STRIKE <span className="text-foreground">${Number(pos.strike).toFixed(0)}</span>
                    </div>
                  ) : null}
                  {pos.mark_premium != null ? (
                    <div>
                      MARK{' '}
                      <span className="text-foreground">${Number(pos.mark_premium).toFixed(3)}</span>
                    </div>
                  ) : null}
                  <div>
                    STOP{' '}
                    <span className="text-destructive">
                      ${Number(pos.stop ?? 0).toFixed(3)} (−65%)
                    </span>
                  </div>
                  <div>
                    ELSE EXIT <span className="text-chart-2">16:00 ET</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="font-mono text-xs text-muted-foreground">NO ACTIVE POSITION</div>
            )}
          </div>

          <div>
            <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">
              Recent Trades · W{f3?.wins ?? 0}/L{f3?.losses ?? 0} · {f3?.total_trades ?? 0} total
            </div>
            {(f3?.recent_trades || []).length === 0 ? (
              <div className="font-mono text-xs text-muted-foreground">No closed F3 trades yet</div>
            ) : (
              <div className="border border-border divide-y divide-border font-mono text-xs">
                {(f3?.recent_trades || []).slice().reverse().map((t, i) => (
                  <div key={i} className="flex justify-between p-2 gap-3">
                    <span>
                      {t.direction || '—'} · {t.reason || '—'}
                      {t.thin_exit ? ' · THIN_EXIT' : ''}
                    </span>
                    <span
                      className={
                        (t.pnl_usd ?? 0) >= 0 ? 'text-chart-2' : 'text-destructive'
                      }
                    >
                      ${(t.pnl_usd ?? 0).toFixed(2)} ({t.result})
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  valueClass = 'text-foreground',
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="border border-border bg-black/30 p-3">
      <div className="text-[10px] font-mono text-muted-foreground uppercase">{label}</div>
      <div className={`text-lg font-mono font-bold mt-1 ${valueClass}`}>{value}</div>
    </div>
  );
}
