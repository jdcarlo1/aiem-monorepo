import { useApi } from "@/hooks/use-api";
import { BarChart2, Clock } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function PaperTrades() {
  // AIEM SKU only — equity paper book. OE oe_trade_records live on /oe-dashboard (separate product).
  // Auth/password remains shared with OE; books are intentionally not mixed in the UI.
  const { data: openTrades, loading: openLoading, lastUpdated: openUpdated } = useApi<any>("/stock-api/aiem-paper-portfolio", {}, 30000);
  const { data: fills, loading: fillsLoading } = useApi<any>("/stock-api/admin/paper-fill-audit", {}, 60000);
  const { data: ledger, loading: ledgerLoading } = useApi<any>("/stock-api/admin/paper-job-ledger?limit=14", {}, 60000);

  const calculateTotalPnl = () => {
    if (!openTrades?.open_positions) return 0;
    return openTrades.open_positions.reduce((acc: number, t: any) => acc + (t.pnl || 0), 0);
  };

  const totalPnl = calculateTotalPnl();

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Paper Trading</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            AIEM equity book only · Options Engine is a separate product
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground mb-1">OPEN P&L (USD)</div>
          <div className={`text-2xl font-mono font-bold ${totalPnl >= 0 ? 'text-success' : 'text-destructive'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 flex-1 min-h-0">
        {/* Open Positions */}
        <div className="xl:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <BarChart2 size={14} /> OPEN POSITIONS
            </h2>
            <span className="text-xs font-mono text-muted-foreground">{openTrades?.open_positions?.length || 0} ACTIVE</span>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TICKER</th>
                  <th className="p-3 font-normal">DIR</th>
                  <th className="p-3 font-normal">ENTRY</th>
                  <th className="p-3 font-normal">QTY</th>
                  <th className="p-3 font-normal">P&L %</th>
                  <th className="p-3 font-normal">P&L $</th>
                  <th className="p-3 font-normal">SOURCE</th>
                </tr>
              </thead>
              <tbody>
                {openLoading ? (
                  <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : openTrades?.open_positions?.length ? (
                  openTrades.open_positions.map((t: any, i: number) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                      <td className="p-3 font-bold text-white">{t.ticker}</td>
                      <td className={`p-3 font-bold ${t.trade_type?.includes('CALL') || t.trade_type === 'STOCK' ? 'text-success' : 'text-destructive'}`}>{t.trade_type}</td>
                      <td className="p-3">{t.entry_price?.toFixed(2)}</td>
                      <td className="p-3">{t.quantity}</td>
                      <td className={`p-3 ${(t.pnl_pct ?? 0) >= 0 ? 'text-success' : 'text-destructive'}`}>
                        {(t.pnl_pct ?? 0) >= 0 ? '+' : ''}{((t.pnl_pct ?? 0)).toFixed(2)}%
                      </td>
                      <td className={`p-3 ${(t.pnl ?? 0) >= 0 ? 'text-success' : 'text-destructive'}`}>
                        {(t.pnl ?? 0) >= 0 ? '+' : ''}{(t.pnl ?? 0).toFixed(2)}
                      </td>
                      <td className="p-3 text-xs text-muted-foreground max-w-[120px] truncate">{t.signal_source}</td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">NO OPEN POSITIONS</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="flex flex-col gap-6 h-full min-h-0">
          {/* Fill Audit */}
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <Clock size={14} className="text-secondary" /> EXECUTION FILLS
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-0">
              <div className="divide-y divide-border/50">
                {fillsLoading ? (
                  <div className="p-4 text-center text-muted-foreground font-mono text-sm">LOADING...</div>
                ) : fills?.recent_rows?.length ? (
                  fills.recent_rows.slice(0, 50).map((f: any, i: number) => (
                    <div key={i} className="p-3 font-mono text-xs hover:bg-white/5">
                      <div className="flex justify-between items-center mb-1">
                        <span className="font-bold text-white">{f.ticker} <span className="text-muted-foreground ml-1">{f.action}</span></span>
                        <span className="text-muted-foreground">{new Date(f.filled_at || f.created_at).toLocaleTimeString()}</span>
                      </div>
                      <div className="flex justify-between text-muted-foreground">
                        <span>{f.quantity} @ ${f.price?.toFixed(2)}</span>
                        <span>{f.status}</span>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-4 text-center text-muted-foreground font-mono text-sm">NO RECENT FILLS</div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="border border-border bg-card flex flex-col min-h-[180px]">
        <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
          <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
            <Clock size={14} /> PAPER JOB LEDGER
          </h2>
          <span className="text-xs font-mono text-muted-foreground">
            exactly-once execute · {ledger?.count ?? 0} days
          </span>
        </div>
        <div className="flex-1 overflow-auto p-0">
          <table className="w-full text-left font-mono text-sm border-collapse">
            <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
              <tr>
                <th className="p-3 font-normal">DATE</th>
                <th className="p-3 font-normal">STATUS</th>
                <th className="p-3 font-normal">TRIGGER</th>
                <th className="p-3 font-normal">PICKS</th>
                <th className="p-3 font-normal">STARTED</th>
                <th className="p-3 font-normal">COMPLETED</th>
              </tr>
            </thead>
            <tbody>
              {ledgerLoading ? (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
              ) : ledger?.rows?.length ? (
                ledger.rows.map((row: any) => (
                  <tr key={row.id} className="border-b border-border/50 hover:bg-white/5">
                    <td className="p-3 text-white font-bold">{row.business_date}</td>
                    <td className="p-3 text-secondary">{row.status}</td>
                    <td className="p-3 text-xs text-muted-foreground">{row.trigger_source || "—"}</td>
                    <td className="p-3">{row.picks_count ?? "—"}</td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {row.started_at ? new Date(row.started_at).toLocaleString() : "—"}
                    </td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {row.completed_at ? new Date(row.completed_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))
              ) : (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">NO LEDGER ROWS</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="border border-border/60 bg-sidebar/30 px-4 py-3 text-xs font-mono text-muted-foreground">
        <span className="text-primary font-bold">SKU note:</span> Options Engine paper book
        (<span className="text-white">oe_trade_records</span>) is sold/viewed separately at{" "}
        <span className="text-white">/oe-dashboard/</span>. Same login password — separate product UI.
      </div>

      <DataFooter
        source="aiem_paper_trades, aiem_paper_fill_audit, paper_trade_job_ledger"
        lastUpdated={openUpdated}
        pollIntervalSec={30}
        operatingMode="AIEM PAPER — SIMULATION ONLY"
        samplePeriod="Since June 2026"
      />
    </div>
  );
}
