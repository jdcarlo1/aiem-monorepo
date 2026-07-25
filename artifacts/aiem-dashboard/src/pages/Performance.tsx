import { useApi } from "@/hooks/use-api";
import {
  TrendingUp, TrendingDown, BarChart2, ShieldAlert, Target, RefreshCw,
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { DataFooter } from "@/components/data-footer";

function Stat({
  label, value, sub, color,
}: {
  label: string; value: string | number | null; sub?: string; color?: string;
}) {
  return (
    <div className="border border-border bg-black p-4">
      <div className="text-xs font-mono text-muted-foreground mb-1">{label}</div>
      <div className={`text-xl font-mono font-bold ${color ?? "text-white"}`}>
        {value ?? <span className="text-muted-foreground text-sm italic">N/A</span>}
      </div>
      {sub && <div className="text-xs font-mono text-muted-foreground mt-1">{sub}</div>}
    </div>
  );
}

function fmt(v: number | null | undefined, decimals = 2, suffix = "") {
  if (v === null || v === undefined) return "—";
  return `${v.toFixed(decimals)}${suffix}`;
}

function signColor(v: number | null | undefined) {
  if (v === null || v === undefined) return "";
  return v >= 0 ? "text-green-400" : "text-destructive";
}

export default function Performance() {
  const { data, loading, lastUpdated, refetch } = useApi<any>(
    "/stock-api/paper-performance",
    {},
    120_000,
  );

  const equity: { idx: number; equity: number }[] =
    (data?.equity_curve ?? []).map((e: number, i: number) => ({ idx: i + 1, equity: e }));

  const startEq = data?.account_start ?? 20000;

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">
            Performance Analytics
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            PERF-001–041 · Paper Trading Quant Metrics · aiem_paper_trades
          </p>
        </div>
        <button
          onClick={refetch}
          className="flex items-center gap-2 text-xs font-mono text-muted-foreground hover:text-white transition-colors"
        >
          <RefreshCw size={12} /> REFRESH
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center font-mono text-muted-foreground text-sm">
          LOADING PERF-001–041…
        </div>
      ) : !data || data.error ? (
        <div className="flex-1 flex items-center justify-center font-mono text-destructive text-sm">
          {data?.error ?? "FETCH FAILED — /stock-api/paper-performance"}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-6 min-h-0">

          {/* Top KPI strip */}
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3 shrink-0">
            <Stat label="CLOSED TRADES" value={data.n_closed} />
            <Stat
              label="WIN RATE"
              value={fmt(data.win_rate_pct, 1, "%")}
              color={data.win_rate_pct >= 50 ? "text-green-400" : "text-destructive"}
            />
            <Stat
              label="NET P&L"
              value={`$${fmt(data.net_profit, 2)}`}
              color={signColor(data.net_profit)}
            />
            <Stat
              label="TOTAL RETURN"
              value={fmt(data.total_return_pct, 2, "%")}
              sub={`Start $${data.account_start?.toLocaleString()}`}
              color={signColor(data.total_return_pct)}
            />
            <Stat
              label="SHARPE"
              value={data.quant_insufficient_n ? "INSUFF N" : fmt(data.sharpe_per_trade, 3)}
              sub="per-trade"
              color={!data.quant_insufficient_n && data.sharpe_per_trade >= 0 ? "text-green-400" : ""}
            />
            <Stat
              label="SORTINO"
              value={data.quant_insufficient_n ? "INSUFF N" : fmt(data.sortino_per_trade, 3)}
              sub="per-trade"
            />
            <Stat
              label="CALMAR"
              value={data.quant_insufficient_n ? "INSUFF N" : fmt(data.calmar_ratio, 3)}
            />
            <Stat
              label="MAX DRAWDOWN"
              value={fmt(data.max_drawdown_pct, 2, "%")}
              sub={`Dur: ${data.drawdown_duration_trades ?? "—"} trades`}
              color={data.max_drawdown_pct < 0 ? "text-destructive" : ""}
            />
          </div>

          {/* Equity Curve */}
          <div className="border border-border bg-card shrink-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
              <TrendingUp size={14} className="text-primary" />
              <span className="text-sm font-mono font-bold text-primary">EQUITY CURVE</span>
              <span className="ml-auto text-xs font-mono text-muted-foreground">
                {equity.length} closed trades · source: aiem_paper_trades.pnl cumulative
              </span>
            </div>
            <div className="p-4">
              {equity.length === 0 ? (
                <div className="h-40 flex items-center justify-center text-muted-foreground font-mono text-sm">
                  NO CLOSED TRADES YET
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={equity} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="idx" tick={{ fontSize: 10, fontFamily: "monospace" }} label={{ value: "trade #", position: "insideBottomRight", offset: -5, fontSize: 10 }} />
                    <YAxis
                      domain={["auto", "auto"]}
                      tick={{ fontSize: 10, fontFamily: "monospace" }}
                      tickFormatter={(v) => `$${v.toLocaleString()}`}
                    />
                    <Tooltip
                      formatter={(v: number) => [`$${v.toFixed(2)}`, "Equity"]}
                      labelFormatter={(l) => `Trade #${l}`}
                      contentStyle={{ background: "#000", border: "1px solid hsl(var(--border))", fontFamily: "monospace", fontSize: 11 }}
                    />
                    <ReferenceLine y={startEq} stroke="hsl(var(--muted-foreground))" strokeDasharray="4 2" />
                    <Area
                      type="monotone"
                      dataKey="equity"
                      stroke="hsl(var(--primary))"
                      strokeWidth={1.5}
                      fill="url(#eqGrad)"
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Risk metrics + Trade stats side-by-side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 shrink-0">

            {/* Risk metrics */}
            <div className="border border-border bg-card">
              <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
                <ShieldAlert size={14} className="text-destructive" />
                <span className="text-sm font-mono font-bold text-white">RISK METRICS</span>
                <span className="ml-auto text-xs font-mono text-muted-foreground">Basel II / Acerbi-Tasche</span>
              </div>
              <div className="p-4 font-mono text-sm space-y-2">
                {[
                  { label: "VaR 95%",           val: fmt(data.var_95_pct, 3, "%"), sub: "per-trade historical simulation" },
                  { label: "CVaR 95%",           val: fmt(data.cvar_95_pct, 3, "%"), sub: "expected shortfall" },
                  { label: "Vol of Returns",     val: fmt(data.volatility_of_returns_pct, 3, "%"), sub: "per-trade" },
                  { label: "Downside Dev",       val: fmt(data.downside_deviation_pct, 3, "%"), sub: "semi-deviation" },
                  { label: "Max Drawdown",       val: fmt(data.max_drawdown_pct, 2, "%"), sub: `${data.drawdown_duration_trades ?? "—"} trade duration` },
                  { label: "Current Drawdown",   val: fmt(data.current_drawdown_pct, 2, "%"), sub: "" },
                  { label: "Recovery Duration",  val: data.recovery_duration_trades != null ? `${data.recovery_duration_trades} trades` : "—", sub: "" },
                ].map(({ label, val, sub }) => (
                  <div key={label} className="flex justify-between items-start border-b border-border/30 pb-2">
                    <div>
                      <div className="text-xs text-muted-foreground">{label}</div>
                      {sub && <div className="text-[10px] text-muted-foreground/60">{sub}</div>}
                    </div>
                    <div className="text-white text-right">{val}</div>
                  </div>
                ))}
                {data.quant_insufficient_n && (
                  <div className="text-xs text-accent mt-2 border border-accent/30 p-2">
                    ⚠ {data.quant_note ?? "Insufficient sample for quant metrics"}
                  </div>
                )}
              </div>
            </div>

            {/* Trade distribution */}
            <div className="border border-border bg-card">
              <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
                <Target size={14} className="text-secondary" />
                <span className="text-sm font-mono font-bold text-white">TRADE DISTRIBUTION</span>
              </div>
              <div className="p-4 font-mono text-sm space-y-2">
                {[
                  { label: "Profit Factor",      val: fmt(data.profit_factor, 3) },
                  { label: "Payoff Ratio",        val: fmt(data.payoff_ratio, 3) },
                  { label: "Expected Value",      val: `$${fmt(data.expected_value_per_trade, 4)}`, sub: "per trade" },
                  { label: "Avg Win",             val: `$${fmt(data.avg_winning_trade, 4)}` },
                  { label: "Avg Loss",            val: `$${fmt(data.avg_losing_trade, 4)}` },
                  { label: "Largest Win",         val: `$${fmt(data.largest_winning_trade, 4)}` },
                  { label: "Largest Loss",        val: `$${fmt(data.largest_losing_trade, 4)}` },
                  { label: "Gross Profit",        val: `$${fmt(data.gross_profit, 2)}` },
                  { label: "Gross Loss",          val: `$${fmt(data.gross_loss, 2)}` },
                  { label: "Open Unrealized P&L", val: `$${fmt(data.open_unrealized_pnl, 2)}` },
                  { label: "Open Trades",         val: data.n_open },
                ].map(({ label, val }) => (
                  <div key={label} className="flex justify-between border-b border-border/30 pb-2">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="text-white">{val}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Breakdown tables */}
          {[
            { key: "by_strategy",          label: "BY STRATEGY",          icon: <BarChart2 size={14} className="text-primary" /> },
            { key: "by_market_regime",     label: "BY MARKET REGIME",     icon: <TrendingUp size={14} className="text-secondary" /> },
            { key: "by_confidence_band",   label: "BY CONFIDENCE BAND",   icon: <Target size={14} className="text-accent" /> },
            { key: "by_vol_regime",        label: "BY VOL REGIME",        icon: <ShieldAlert size={14} className="text-muted-foreground" /> },
          ].map(({ key, label, icon }) => {
            const breakdown: Record<string, any> = data[key] ?? {};
            const entries = Object.entries(breakdown);
            const note = data[`${key}_note`] as string | undefined;

            return (
              <div key={key} className="border border-border bg-card shrink-0">
                <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
                  {icon}
                  <span className="text-sm font-mono font-bold text-white">{label}</span>
                  {note && (
                    <span className="ml-auto text-[10px] font-mono text-accent">{note}</span>
                  )}
                </div>
                <div className="overflow-x-auto">
                  {entries.length === 0 ? (
                    <div className="p-4 text-xs font-mono text-muted-foreground">
                      NO DATA — INSUFFICIENT CLOSED TRADES
                    </div>
                  ) : (
                    <table className="w-full font-mono text-xs border-collapse">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="p-3 text-left font-normal">GROUP</th>
                          <th className="p-3 text-right font-normal">N</th>
                          <th className="p-3 text-right font-normal">WIN %</th>
                          <th className="p-3 text-right font-normal">NET P&L</th>
                          <th className="p-3 text-right font-normal">AVG P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {entries.map(([grp, v]) => (
                          <tr key={grp} className="border-b border-border/30 hover:bg-white/5">
                            <td className="p-3 text-white">{grp}</td>
                            <td className="p-3 text-right text-muted-foreground">{v.n}</td>
                            <td className={`p-3 text-right ${v.win_rate >= 50 ? "text-green-400" : "text-destructive"}`}>
                              {fmt(v.win_rate, 1, "%")}
                            </td>
                            <td className={`p-3 text-right ${v.net_pnl >= 0 ? "text-green-400" : "text-destructive"}`}>
                              ${fmt(v.net_pnl, 2)}
                            </td>
                            <td className={`p-3 text-right ${(v.net_pnl / v.n) >= 0 ? "text-green-400" : "text-destructive"}`}>
                              ${fmt(v.net_pnl / v.n, 2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            );
          })}

          {/* By-ticker table */}
          <div className="border border-border bg-card shrink-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
              <TrendingDown size={14} className="text-white" />
              <span className="text-sm font-mono font-bold text-white">BY TICKER</span>
            </div>
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              {Object.keys(data.by_ticker ?? {}).length === 0 ? (
                <div className="p-4 text-xs font-mono text-muted-foreground">NO DATA</div>
              ) : (
                <table className="w-full font-mono text-xs border-collapse">
                  <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground z-10">
                    <tr>
                      <th className="p-3 text-left font-normal">TICKER</th>
                      <th className="p-3 text-right font-normal">N</th>
                      <th className="p-3 text-right font-normal">WIN %</th>
                      <th className="p-3 text-right font-normal">NET P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.by_ticker as Record<string, any>)
                      .sort(([, a], [, b]) => (b.net_pnl ?? 0) - (a.net_pnl ?? 0))
                      .map(([tkr, v]) => (
                        <tr key={tkr} className="border-b border-border/30 hover:bg-white/5">
                          <td className="p-3 text-white">{tkr}</td>
                          <td className="p-3 text-right text-muted-foreground">{v.n}</td>
                          <td className={`p-3 text-right ${v.win_rate >= 50 ? "text-green-400" : "text-destructive"}`}>
                            {fmt(v.win_rate, 1, "%")}
                          </td>
                          <td className={`p-3 text-right ${v.net_pnl >= 0 ? "text-green-400" : "text-destructive"}`}>
                            ${fmt(v.net_pnl, 2)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

        </div>
      )}

      <DataFooter
        source="/stock-api/paper-performance · paper_performance.py PERF-001–041"
        lastUpdated={lastUpdated}
        operatingMode="PAPER TRADING — SIMULATION ONLY"
        samplePeriod="All-time (closed trades only)"
      />
    </div>
  );
}
