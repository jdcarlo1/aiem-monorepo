import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import { FlaskConical, TrendingUp, TrendingDown, Activity } from 'lucide-react';

type StrategySnap = {
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
    packages?: number;
    option_symbol?: string;
    symbol?: string;
    entry?: number;
    entry_premium?: number;
    entry_debit_usd?: number;
    stop?: number;
    target?: number;
    strike?: number;
    mark_premium?: number;
    unrealized_pnl?: number;
    expiration?: string;
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
  f3?: StrategySnap;
  put_butterfly?: StrategySnap;
  call_butterfly?: StrategySnap;
  put_ladder?: StrategySnap;
  call_condor?: StrategySnap;
  put_condor?: StrategySnap;
  narrow_wing_butterfly?: StrategySnap;
  bullish_risk_reversal?: StrategySnap;
  gap_fill?: unknown;
  orb?: unknown;
  error?: string;
};

const ASYM_CARDS: Array<{
  key: string;
  title: string;
  blurb: string;
}> = [
  {
    key: 'narrow_wing_butterfly',
    title: 'Narrow-Wing Call Butterfly',
    blurb:
      'ATM ±2 call fly · Mon–Fri 09:30 ET · ≤$500 debit · TP +300% · no stop · Tradier NBBO paper · Joel #1',
  },
  {
    key: 'put_butterfly',
    title: 'Long Put Butterfly',
    blurb:
      'ATM ±5 put fly · Mon–Fri 09:30 ET · ≤$500 debit · TP +275% · no stop · Tradier NBBO paper · Joel #2',
  },
  {
    key: 'call_butterfly',
    title: 'Long Call Butterfly',
    blurb:
      'ATM ±5 call fly · Mon–Fri 09:30 ET · ≤$500 debit · TP +275% · no stop · Tradier NBBO paper · Joel #3',
  },
  {
    key: 'put_ladder',
    title: 'Put Ladder Defined',
    blurb:
      'Long ATM / short −5/−10 / long −15 puts · Mon–Fri 09:30 ET · ≤$500 debit · TP +300% · Tradier NBBO paper · Joel #4',
  },
  {
    key: 'call_condor',
    title: 'Long Call Condor',
    blurb:
      'ATM ±5 / ±10 call condor · Mon–Fri 09:30 ET · ≤$500 debit · TP +300% · no stop · Tradier NBBO paper · Joel #5',
  },
  {
    key: 'put_condor',
    title: 'Long Put Condor',
    blurb:
      'ATM ±5 / ±10 put condor · Mon–Fri 09:30 ET · ≤$500 debit · TP +300% · no stop · Tradier NBBO paper · Joel #6',
  },
  {
    key: 'bullish_risk_reversal',
    title: 'Bullish Risk Reversal',
    blurb:
      'Long call k+5 / short put k−5 · Mon–Fri 09:30 ET · cash-secured credit · TP +75% · Tradier NBBO paper',
  },
];

export default function StrategiesPage() {
  const { apiFetch } = useApi();

  const { data, isLoading, isError, error, dataUpdatedAt } = useQuery({
    queryKey: ['pattern-lab-strategies'],
    queryFn: () => apiFetch<Snapshot>('/pattern-lab/snapshot'),
    refetchInterval: 30_000,
    retry: false,
  });

  return (
    <>
      <div className="flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-end border-b border-border pb-5 min-w-0">
        <div className="min-w-0">
          <h1 className="text-2xl sm:text-3xl font-mono font-bold tracking-tight uppercase truncate">
            Strategies
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1 break-words">
            Live paper on Options Engine — F3 0DTE + asym packages (flies, ladder, condors, narrow-wing, bullish RR)
          </p>
        </div>
        <div className="font-mono text-sm text-muted-foreground text-left sm:text-right shrink-0">
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
        <div className="border border-destructive/40 bg-destructive/10 p-3 font-mono text-sm text-destructive break-words">
          {String((error as Error)?.message || error)}
        </div>
      ) : null}

      <div className="space-y-5 max-w-6xl min-w-0">
        <F3Card snap={data?.f3} />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 min-w-0">
          {ASYM_CARDS.slice(0, 3).map((c) => (
            <AsymCard
              key={c.key}
              title={c.title}
              blurb={c.blurb}
              snap={data?.[c.key]}
            />
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 min-w-0">
          {ASYM_CARDS.slice(3, 6).map((c) => (
            <AsymCard
              key={c.key}
              title={c.title}
              blurb={c.blurb}
              snap={data?.[c.key]}
            />
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 min-w-0">
          {ASYM_CARDS.slice(6).map((c) => (
            <AsymCard
              key={c.key}
              title={c.title}
              blurb={c.blurb}
              snap={data?.[c.key]}
            />
          ))}
        </div>
      </div>
    </>
  );
}

function F3Card({ snap }: { snap?: StrategySnap }) {
  const pos = snap?.active_position;
  const profit = snap?.profit_rate_pct ?? 0;
  const profitColor =
    profit > 0 ? 'text-chart-2' : profit < 0 ? 'text-destructive' : 'text-muted-foreground';
  const dir = pos?.direction || pos?.side;
  const qty = pos?.contracts ?? pos?.shares;

  return (
    <div className="border border-border bg-card rounded-lg overflow-hidden">
      <div className="p-4 border-b border-border bg-sidebar/40 flex justify-between items-center gap-3">
        <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2 uppercase">
          <FlaskConical size={14} /> F3 SPY 0DTE
        </h2>
        <span className="text-sm font-mono text-muted-foreground uppercase">
          {snap?.pattern || 'F3_SPY_0DTE'}
        </span>
      </div>

      <div className="p-5 space-y-5">
        <p className="font-mono text-sm text-muted-foreground leading-relaxed">
          Premarket direction → ORB 9:30–9:44 → breakout with PM → buy ATM CALL/PUT
          ($200 notional) → auto-sell at −65% premium stop, else exit 16:00 ET.
          No profit target. Real Tradier premiums when available (no synthetic leverage).
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 min-w-0">
          <Metric
            label="Account"
            value={`$${(snap?.account_balance_usd ?? 10000).toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}`}
          />
          <Metric
            label="NLV"
            value={`$${(snap?.net_liquidation_usd ?? 10000).toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}`}
            valueClass="text-secondary"
          />
          <Metric
            label="Win Rate"
            value={`${(snap?.win_rate_pct ?? 0).toFixed(2)}%`}
            valueClass="text-primary"
          />
          <Metric
            label="Profit Rate"
            value={`${profit.toFixed(2)}%`}
            valueClass={profitColor}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 font-mono text-sm">
          <div className="border border-border rounded-md p-3">
            <div className="text-muted-foreground uppercase text-xs font-semibold tracking-wide">Premarket</div>
            <div className="text-foreground mt-1">{snap?.pm_direction || '—'}</div>
          </div>
          <div className="border border-border rounded-md p-3">
            <div className="text-muted-foreground uppercase text-xs font-semibold tracking-wide">ORB High</div>
            <div className="text-foreground mt-1">
              {snap?.orb_high != null ? `$${Number(snap.orb_high).toFixed(2)}` : '—'}
            </div>
          </div>
          <div className="border border-border rounded-md p-3">
            <div className="text-muted-foreground uppercase text-xs font-semibold tracking-wide">ORB Low</div>
            <div className="text-foreground mt-1">
              {snap?.orb_low != null ? `$${Number(snap.orb_low).toFixed(2)}` : '—'}
            </div>
          </div>
          <div className="border border-border rounded-md p-3">
            <div className="text-muted-foreground uppercase text-xs font-semibold tracking-wide">Signal</div>
            <div className="text-primary mt-1">{snap?.signal_state?.status || '—'}</div>
          </div>
        </div>

        {snap?.signal_state?.note ? (
          <div className="font-mono text-sm text-muted-foreground border border-border rounded-md p-3">
            {snap.signal_state.note}
          </div>
        ) : null}

        <div className="border border-border rounded-md bg-muted/20 p-4">
          <div className="text-sm font-mono text-muted-foreground uppercase mb-3 flex items-center gap-2">
            <Activity size={12} /> Active Position
          </div>
          {pos ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 font-mono text-sm">
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
              <div className="text-muted-foreground text-sm space-y-1 md:text-right">
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
            <div className="font-mono text-sm text-muted-foreground">NO ACTIVE POSITION</div>
          )}
        </div>

        <RecentTrades snap={snap} emptyLabel="No closed F3 trades yet" />
      </div>
    </div>
  );
}

function AsymCard({
  title,
  blurb,
  snap,
}: {
  title: string;
  blurb: string;
  snap?: StrategySnap;
}) {
  const pos = snap?.active_position;
  const profit = snap?.profit_rate_pct ?? 0;
  const profitColor =
    profit > 0 ? 'text-chart-2' : profit < 0 ? 'text-destructive' : 'text-muted-foreground';
  const dir = pos?.direction || pos?.side;
  const qty = pos?.packages ?? pos?.contracts ?? pos?.shares;
  const tpPct = Number(snap?.rules?.take_profit_pct ?? 0);

  return (
    <div className="border border-border bg-card rounded-lg overflow-hidden flex flex-col min-w-0">
      <div className="p-4 border-b border-border bg-sidebar/40 flex flex-col gap-1 sm:flex-row sm:justify-between sm:items-center sm:gap-2">
        <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2 uppercase min-w-0">
          <FlaskConical size={14} className="shrink-0" />{' '}
          <span className="truncate">{title}</span>
        </h2>
        <span className="text-[10px] sm:text-xs font-mono text-muted-foreground uppercase truncate max-w-full">
          {snap?.pattern || '—'}
        </span>
      </div>

      <div className="p-4 space-y-3 flex-1 flex flex-col min-w-0">
        <p className="font-mono text-xs text-muted-foreground leading-relaxed break-words">
          {blurb}
        </p>

        <div className="grid grid-cols-2 gap-2 min-w-0">
          <Metric
            label="Account"
            value={`$${(snap?.account_balance_usd ?? 10000).toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}`}
          />
          <Metric
            label="Profit"
            value={`${profit.toFixed(2)}%`}
            valueClass={profitColor}
          />
          <Metric
            label="Win Rate"
            value={`${(snap?.win_rate_pct ?? 0).toFixed(2)}%`}
            valueClass="text-primary"
          />
          <Metric
            label="Trades"
            value={`${snap?.total_trades ?? 0}`}
            subValue={`W${snap?.wins ?? 0}/L${snap?.losses ?? 0}`}
          />
        </div>

        <div className="border border-border rounded-md p-3 font-mono text-xs">
          <div className="text-muted-foreground uppercase text-xs font-semibold tracking-wide">Signal</div>
          <div className="text-primary mt-1">{snap?.signal_state?.status || '—'}</div>
          {snap?.signal_state?.note ? (
            <div className="text-muted-foreground mt-1">{snap.signal_state.note}</div>
          ) : null}
        </div>

        <div className="border border-border rounded-md bg-muted/20 p-3 flex-1">
          <div className="text-xs font-mono text-muted-foreground uppercase mb-2 flex items-center gap-2">
            <Activity size={12} /> Active Position
          </div>
          {pos ? (
            <div className="font-mono text-xs space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                {String(dir || '').includes('CALL') ? (
                  <TrendingUp size={14} className="text-chart-2" />
                ) : (
                  <TrendingDown size={14} className="text-destructive" />
                )}
                <span className="text-foreground">{dir}</span>
                <span>{qty != null ? `${qty} pkg` : ''}</span>
              </div>
              <div className="text-muted-foreground">
                {Number(pos.entry_debit_usd ?? 0) < 0 ? 'CREDIT' : 'DEBIT'}{' '}
                <span className="text-foreground">
                  $
                  {Number(
                    pos.entry_debit_usd ?? (pos.entry_premium ?? pos.entry ?? 0) * 100
                  ).toFixed(2)}
                </span>
              </div>
              {pos.expiration ? (
                <div className="text-muted-foreground">
                  EXP <span className="text-foreground">{pos.expiration}</span>
                </div>
              ) : null}
              {pos.unrealized_pnl != null ? (
                <div className="text-muted-foreground">
                  UPNL{' '}
                  <span
                    className={
                      pos.unrealized_pnl >= 0 ? 'text-chart-2' : 'text-destructive'
                    }
                  >
                    ${Number(pos.unrealized_pnl).toFixed(2)}
                  </span>
                </div>
              ) : null}
              <div className="text-muted-foreground">
                TP <span className="text-chart-2">{tpPct ? `+${tpPct}%` : '—'}</span>
                {' · '}STOP <span>NONE</span>
              </div>
            </div>
          ) : (
            <div className="font-mono text-xs text-muted-foreground">NO ACTIVE POSITION</div>
          )}
        </div>

        <RecentTrades snap={snap} emptyLabel="No closed trades yet" compact />
      </div>
    </div>
  );
}

function RecentTrades({
  snap,
  emptyLabel,
  compact = false,
}: {
  snap?: StrategySnap;
  emptyLabel: string;
  compact?: boolean;
}) {
  const trades = snap?.recent_trades || [];
  return (
    <div>
      <div className="text-sm font-mono text-muted-foreground uppercase mb-2">
        Recent Trades · W{snap?.wins ?? 0}/L{snap?.losses ?? 0}
        {!compact && snap?.total_trades != null ? ` · ${snap.total_trades} total` : ''}
      </div>
      {trades.length === 0 ? (
        <div className="font-mono text-sm text-muted-foreground">{emptyLabel}</div>
      ) : (
        <div
          className={`border border-border rounded-md divide-y divide-border font-mono text-sm ${
            compact ? 'max-h-28 overflow-auto text-xs' : ''
          }`}
        >
          {trades
            .slice()
            .reverse()
            .map((t, i) => (
              <div key={i} className="flex justify-between p-3 gap-3">
                <span>
                  {t.direction || '—'} · {t.reason || '—'}
                  {t.thin_exit ? ' · THIN_EXIT' : ''}
                </span>
                <span
                  className={(t.pnl_usd ?? 0) >= 0 ? 'text-chart-2' : 'text-destructive'}
                >
                  ${(t.pnl_usd ?? 0).toFixed(2)} ({t.result})
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  subValue,
  valueClass = 'text-foreground',
}: {
  label: string;
  value: string;
  subValue?: string;
  valueClass?: string;
}) {
  return (
    <div className="border border-border rounded-md bg-muted/30 p-2.5 sm:p-3 min-w-0 overflow-hidden">
      <div className="text-[10px] sm:text-xs font-mono text-muted-foreground uppercase tracking-wide truncate">
        {label}
      </div>
      <div
        className={`text-base sm:text-xl font-mono font-bold mt-1 tabular-nums leading-tight break-all ${valueClass}`}
      >
        {value}
      </div>
      {subValue ? (
        <div className="text-[10px] sm:text-xs font-mono text-muted-foreground mt-0.5 truncate">
          {subValue}
        </div>
      ) : null}
    </div>
  );
}
