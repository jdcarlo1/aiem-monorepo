import { useApi } from "@/hooks/use-api";
import { Activity, ShieldCheck, ShieldAlert, FileWarning, Search } from "lucide-react";

export default function Decisions() {
  const { data: decisions, loading: decLoading } = useApi<any>("/stock-api/admin/decision-audit?limit=50", {}, 30000);
  const { data: gateEvents, loading: gateLoading } = useApi<any>("/stock-api/admin/gate-events?limit=50", {}, 30000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Live Decisions</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Audit Trail & Gate Events</p>
        </div>
        <div className="flex gap-4 font-mono text-xs">
          <div className="text-right">
            <div className="text-muted-foreground">DECISION ROWS</div>
            <div className="text-primary font-bold text-lg">{decisions?.count || 0}</div>
          </div>
          <div className="text-right border-l border-border pl-4">
            <div className="text-muted-foreground">GATE EVENTS</div>
            <div className="text-secondary font-bold text-lg">{gateEvents?.count || 0}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        {/* Decision Audit Trail */}
        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Activity size={14} /> DECISION AUDIT TRAIL
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TIMESTAMP</th>
                  <th className="p-3 font-normal">DECISION ID</th>
                  <th className="p-3 font-normal">TARGET</th>
                  <th className="p-3 font-normal">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {decLoading ? (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : decisions?.rows?.length ? (
                  decisions.rows.map((row: any, i: number) => {
                    const statusColor = 
                      row.verification_status === 'VERIFIED' ? 'text-success' :
                      row.verification_status === 'FAILED' ? 'text-destructive' : 'text-accent';
                      
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{new Date(row.created_at).toLocaleString()}</td>
                        <td className="p-3 text-white truncate max-w-[120px]">{row.decision_id}</td>
                        <td className="p-3">
                          {row.identity_json?.ticker} <span className="text-muted-foreground text-xs ml-1">{row.identity_json?.direction}</span>
                        </td>
                        <td className={`p-3 font-bold flex items-center gap-1 ${statusColor}`}>
                          {row.verification_status === 'VERIFIED' ? <ShieldCheck size={12} /> : 
                           row.verification_status === 'FAILED' ? <ShieldAlert size={12} /> : <FileWarning size={12} />}
                          {row.verification_status}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">NO AUDIT ROWS</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Gate Events */}
        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <ShieldAlert size={14} /> GATE EVENTS
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TIME</th>
                  <th className="p-3 font-normal">GATE</th>
                  <th className="p-3 font-normal">TICKER</th>
                  <th className="p-3 font-normal">ACTION</th>
                </tr>
              </thead>
              <tbody>
                {gateLoading ? (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : gateEvents?.rows?.length ? (
                  gateEvents.rows.map((event: any, i: number) => {
                    const actionColor = 
                      event.action_taken === 'ALLOW' ? 'text-success' :
                      event.action_taken === 'HALT' ? 'text-destructive' : 'text-accent';
                      
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{new Date(event.fired_at).toLocaleTimeString()}</td>
                        <td className="p-3 text-white truncate max-w-[150px]">{event.gate_name}</td>
                        <td className="p-3 font-bold">{event.ticker}</td>
                        <td className={`p-3 font-bold ${actionColor}`}>
                          <div className="flex flex-col">
                            <span>{event.action_taken}</span>
                            <span className="text-[10px] font-normal text-muted-foreground truncate max-w-[150px]" title={event.reason}>{event.reason}</span>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">NO GATE EVENTS</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
