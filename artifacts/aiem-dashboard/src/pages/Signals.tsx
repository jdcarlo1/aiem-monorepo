import { useApi } from "@/hooks/use-api";
import { ActivitySquare, Target, AlertTriangle } from "lucide-react";

export default function Signals() {
  const { data: discoveries, loading: discLoading } = useApi<any>("/stock-api/admin/signal-discoveries", {}, 120000);
  const { data: gap } = useApi<any>("/stock-api/gap-volume-signal", {}, 60000);

  const rows: any[] = discoveries?.rows ?? [];

  const statusColor = (s: string) => {
    if (s === "validated") return "text-success";
    if (s === "hypothesis") return "text-accent";
    if (s === "retired") return "text-destructive";
    return "text-muted-foreground";
  };

  const fmtWr = (wr: number | null | undefined) =>
    wr != null ? `${(Number(wr) * 100).toFixed(1)}%` : "N/A";

  const fmtP = (p: number | null | undefined) => {
    if (p == null) return "N/A";
    const n = Number(p);
    if (n === 0) return "<0.001";
    return n.toFixed(4);
  };

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Signal Discoveries</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Live: aiem_signal_discoveries — {rows.length} row{rows.length !== 1 ? "s" : ""}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <ActivitySquare size={14} /> STATISTICAL FINDINGS
            </h2>
            <span className="text-xs font-mono text-muted-foreground">SOURCE: aiem_signal_discoveries</span>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">SIGNAL</th>
                  <th className="p-3 font-normal">WIN RATE</th>
                  <th className="p-3 font-normal">N</th>
                  <th className="p-3 font-normal">P-VALUE</th>
                  <th className="p-3 font-normal">OOS EDGE</th>
                  <th className="p-3 font-normal">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {discLoading ? (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : rows.length ? (
                  rows.map((r: any) => (
                    <tr key={r.id} className="border-b border-border/50 hover:bg-white/5">
                      <td className="p-3 font-bold text-white max-w-[160px] truncate" title={r.hypothesis_text}>
                        {r.hypothesis_text ?? `ID ${r.id}`}
                      </td>
                      <td className={`p-3 font-bold ${r.signal_win_rate != null && Number(r.signal_win_rate) > 0.55 ? "text-success" : r.signal_win_rate != null ? "text-accent" : "text-muted-foreground"}`}>
                        {fmtWr(r.signal_win_rate)}
                      </td>
                      <td className="p-3 text-white">{r.signal_n ?? "N/A"}</td>
                      <td className="p-3 text-secondary">{fmtP(r.p_value)}</td>
                      <td className="p-3 text-white">{r.oos_edge != null ? Number(r.oos_edge).toFixed(2) + "%" : "N/A"}</td>
                      <td className={`p-3 font-bold uppercase ${statusColor(r.status)}`}>{r.status ?? "UNKNOWN"}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6} className="p-6 text-center">
                      <div className="text-muted-foreground space-y-1">
                        <AlertTriangle size={16} className="mx-auto text-accent" />
                        <div>NO SIGNAL DISCOVERIES FOUND</div>
                        <div className="text-xs">Source: /stock-api/admin/signal-discoveries → aiem_signal_discoveries</div>
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <Target size={14} /> DIAGNOSTICS
            </h2>
          </div>
          <div className="p-4 space-y-6 overflow-auto font-mono text-sm">
            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">TOTAL DISCOVERIES (DB)</div>
              <div className="text-3xl font-bold text-white">{discoveries?.count ?? (discLoading ? "..." : "N/A")}</div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">VALIDATED</div>
              <div className="text-3xl font-bold text-success">
                {rows.filter((r: any) => r.status === "validated").length || (discLoading ? "..." : 0)}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">HYPOTHESIS</div>
              <div className="text-3xl font-bold text-accent">
                {rows.filter((r: any) => r.status === "hypothesis").length || (discLoading ? "..." : 0)}
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">GAP+VOL SIGNALS TODAY</div>
              <div className="text-2xl font-bold text-primary">{gap?.count ?? (gap === null ? "N/A" : "...")}</div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground text-xs">CONFIDENCE DISTRIBUTION</div>
              <div className="p-3 border border-border bg-black text-xs text-accent">
                <AlertTriangle size={12} className="inline mr-1" />
                NOT AVAILABLE — no backend metric for per-signal confidence distribution stored yet.
              </div>
            </div>

            <div className="pt-2 text-xs text-muted-foreground italic border-t border-border">
              All values sourced live from DB. No fabricated data.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
