import { useApi } from "@/hooks/use-api";
import { FlaskConical, TrendingUp, TrendingDown, Activity } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

type ActivePosition = {
  symbol: string;
  shares?: number;
  contracts?: number;
  side: string;
  direction?: string;
  entry: number;
  entry_premium?: number;
  stop: number;
  target: number;
  strike?: number;
  option_symbol?: string;
  mark_premium?: number;
  unrealized_pnl?: number;
} | null;

type PatternSnap = {
  pattern: string;
  account_balance_usd: number;
  net_liquidation_usd: number;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  profit_rate_pct: number;
  active_position: ActivePosition;
  signal_state?: { status?: string; note?: string; direction?: string };
  pm_direction?: string | null;
  orb_high?: number | null;
  orb_low?: number | null;
  rules?: Record<string, unknown>;
};

type Snapshot = {
  gap_fill?: PatternSnap;
  orb?: PatternSnap;
  f3?: PatternSnap;
  error?: string;
};

function PatternCard({
  snap,
  title,
  optionsMode = false,
}: {
  snap?: PatternSnap;
  title: string;
  optionsMode?: boolean;
}) {
  const pos = snap?.active_position;
  const profit = snap?.profit_rate_pct ?? 0;
  const profitColor =
    profit > 0 ? "text-success" : profit < 0 ? "text-destructive" : "text-muted-foreground";
  const qty = pos?.contracts ?? pos?.shares;
  const dir = pos?.direction || pos?.side;

  return (
    <div className="border border-border bg-card flex flex-col h-full">
      <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
        <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2 uppercase">
          <FlaskConical size={14} /> {title}
        </h2>
        <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-wide">
          {snap?.pattern || "—"}
        </span>
      </div>

      <div className="p-4 space-y-4 flex-1">
        <div className="grid grid-cols-2 gap-3">
          <div className="border border-border bg-black/30 p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase">Account Balance</div>
            <div className="text-xl font-mono font-bold text-white mt-1">
              ${(snap?.account_balance_usd ?? 10000).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </div>
          </div>
          <div className="border border-border bg-black/30 p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase">Net Liquidation</div>
            <div className="text-xl font-mono font-bold text-secondary mt-1">
              ${(snap?.net_liquidation_usd ?? 10000).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="border border-border p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase">Win Rate</div>
            <div className="text-lg font-mono font-bold text-primary mt-1">
              {(snap?.win_rate_pct ?? 0).toFixed(2)}%
            </div>
          </div>
          <div className="border border-border p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase">Profit Rate</div>
            <div className={`text-lg font-mono font-bold mt-1 ${profitColor}`}>
              {profit.toFixed(2)}%
            </div>
          </div>
          <div className="border border-border p-3">
            <div className="text-[10px] font-mono text-muted-foreground uppercase">Trades</div>
            <div className="text-lg font-mono font-bold text-white mt-1">
              {snap?.total_trades ?? 0}
              <span className="text-xs text-muted-foreground ml-2">
                W{snap?.wins ?? 0}/L{snap?.losses ?? 0}
              </span>
            </div>
          </div>
        </div>

        {optionsMode ? (
          <div className="border border-border bg-black/20 p-3 font-mono text-[11px] text-muted-foreground space-y-1">
            <div>
              PM{" "}
              <span className="text-white">{snap?.pm_direction || "—"}</span>
              {" · "}ORB H{" "}
              <span className="text-white">
                {snap?.orb_high != null ? `$${Number(snap.orb_high).toFixed(2)}` : "—"}
              </span>
              {" / "}L{" "}
              <span className="text-white">
                {snap?.orb_low != null ? `$${Number(snap.orb_low).toFixed(2)}` : "—"}
              </span>
            </div>
            <div>
              SIGNAL{" "}
              <span className="text-primary">{snap?.signal_state?.status || "—"}</span>
              {snap?.signal_state?.note ? (
                <span className="block text-muted-foreground mt-1">{snap.signal_state.note}</span>
              ) : null}
            </div>
            <div className="text-[10px] uppercase tracking-wide pt-1">
              $200 notional · ATM 0DTE long · −65% premium stop · else exit 16:00
            </div>
          </div>
        ) : null}

        <div className="border border-border bg-black/20 p-3">
          <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2 flex items-center gap-2">
            <Activity size={12} /> Active Position
          </div>
          {pos ? (
            <div className="grid grid-cols-2 gap-2 font-mono text-sm">
              <div className="flex items-center gap-2 flex-wrap">
                {dir === "CALL" || dir === "LONG" ? (
                  <TrendingUp size={14} className="text-success" />
                ) : (
                  <TrendingDown size={14} className="text-destructive" />
                )}
                <span
                  className={
                    dir === "CALL" || dir === "LONG" ? "text-success" : "text-destructive"
                  }
                >
                  {dir}
                </span>
                <span className="text-white">
                  {qty != null ? `${qty}${optionsMode ? " ctr" : " sh"}` : ""}
                </span>
                <span className="text-muted-foreground">
                  {pos.option_symbol || pos.symbol}
                </span>
              </div>
              <div className="text-right text-muted-foreground text-xs space-y-0.5">
                <div>
                  ENTRY{" "}
                  <span className="text-white">
                    ${Number(pos.entry_premium ?? pos.entry).toFixed(optionsMode ? 3 : 2)}
                  </span>
                </div>
                {optionsMode ? (
                  <>
                    {pos.strike != null ? (
                      <div>
                        STRIKE <span className="text-white">${Number(pos.strike).toFixed(0)}</span>
                      </div>
                    ) : null}
                    {pos.mark_premium != null ? (
                      <div>
                        MARK{" "}
                        <span className="text-white">${Number(pos.mark_premium).toFixed(3)}</span>
                      </div>
                    ) : null}
                    <div>
                      STOP{" "}
                      <span className="text-destructive">
                        ${Number(pos.stop).toFixed(3)} (−65%)
                      </span>
                    </div>
                    <div>ELSE EXIT <span className="text-success">16:00 ET</span></div>
                  </>
                ) : (
                  <>
                    <div>
                      STOP <span className="text-destructive">${Number(pos.stop).toFixed(2)}</span>
                    </div>
                    <div>
                      TARGET <span className="text-success">${Number(pos.target).toFixed(2)}</span>
                    </div>
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="font-mono text-xs text-muted-foreground">NO ACTIVE POSITION</div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PatternLab() {
  const { data, loading, lastUpdated, error } = useApi<Snapshot>(
    "/stock-api/pattern-lab/snapshot",
    {},
    30000
  );

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">
            Pattern Lab
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Gap Fill &amp; ORB equity paper ($10k / 1.5% / ORB 3.0R) · F3 SPY 0DTE ATM options
            ($200 notional, PM-aligned breakout, exit 16:00)
          </p>
        </div>
        <div className="font-mono text-xs text-muted-foreground text-right">
          <div>POLL 30s</div>
          <div className="text-primary">{loading ? "LOADING…" : error ? "ERROR" : "LIVE"}</div>
        </div>
      </div>

      {error ? (
        <div className="border border-destructive/40 bg-destructive/10 p-3 font-mono text-xs text-destructive">
          {String(error)}
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <PatternCard snap={data?.gap_fill} title="Gap Fill" />
        <PatternCard snap={data?.orb} title="Opening Range Breakout" />
        <PatternCard snap={data?.f3} title="F3 SPY 0DTE" optionsMode />
      </div>

      <DataFooter lastUpdated={lastUpdated} source="/stock-api/pattern-lab/snapshot" />
    </div>
  );
}
