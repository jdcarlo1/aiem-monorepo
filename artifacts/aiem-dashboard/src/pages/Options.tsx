import { useApi } from "@/hooks/use-api";
import { Workflow, FastForward, CheckCircle2, AlertTriangle, AlertCircle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function Options() {
  const { data: checkpoint, loading: checkLoading, lastUpdated: checkUpdated } = useApi<any>("/stock-api/admin/pipeline-checkpoint", {}, 30000);
  const { data: audit, loading: auditLoading } = useApi<any>("/stock-api/admin/aiem-pipeline-audit", {}, 60000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Options Pipeline</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Pipeline Checkpoints & Phase Events</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Workflow size={14} /> PIPELINE JOBS
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TRACE ID</th>
                  <th className="p-3 font-normal">TICKER</th>
                  <th className="p-3 font-normal">PHASE</th>
                  <th className="p-3 font-normal">STATUS</th>
                  <th className="p-3 font-normal">TIME</th>
                </tr>
              </thead>
              <tbody>
                {auditLoading ? (
                  <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : audit?.entries?.length ? (
                  audit.entries.map((row: any, i: number) => {
                    const statusColor = 
                      row.status === 'COMPLETED' ? 'text-success' :
                      row.status === 'FAILED' ? 'text-destructive' : 
                      row.status === 'EXECUTING' || row.status === 'RUNNING' ? 'text-primary animate-pulse' : 'text-accent';
                      
                    // Note: "The daily_pipeline_runs table has stale RUNNING rows from 2026-07-17/2026-07-18/2026-07-19 — filter or flag these"
                    const isStale = (row.status === 'RUNNING' || row.status === 'EXECUTING') && 
                                    new Date(row.created_at).getTime() < Date.now() - 86400000;
                    
                    return (
                      <tr key={i} className={`border-b border-border/50 hover:bg-white/5 ${isStale ? 'opacity-50' : ''}`}>
                        <td className="p-3 text-muted-foreground truncate max-w-[120px]">{row.trace_id}</td>
                        <td className="p-3 font-bold text-white">{row.ticker}</td>
                        <td className="p-3 text-secondary">{row.phase}</td>
                        <td className={`p-3 font-bold flex items-center gap-1 ${isStale ? 'text-destructive' : statusColor}`}>
                          {isStale && <AlertCircle size={12} />}
                          {isStale ? 'STALE' : row.status}
                        </td>
                        <td className="p-3 text-xs text-muted-foreground">
                          {row.completed_at ? new Date(row.completed_at).toLocaleTimeString() : new Date(row.created_at).toLocaleTimeString()}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">NO PIPELINE ENTRIES</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <CheckCircle2 size={14} /> CHECKPOINT DATA
            </h2>
          </div>
          <div className="p-4 flex-1 overflow-auto font-mono text-xs text-muted-foreground break-all">
            {checkLoading ? (
              <div>LOADING CHECKPOINT...</div>
            ) : checkpoint ? (
              <pre className="whitespace-pre-wrap">{JSON.stringify(checkpoint, null, 2)}</pre>
            ) : (
              <div>NO CHECKPOINT DATA AVAILABLE</div>
            )}
          </div>
        </div>
      </div>
      <DataFooter
        source="daily_pipeline_runs, oe_decision_audit"
        lastUpdated={checkUpdated}
        pollIntervalSec={30}
        operatingMode="OPTIONS PIPELINE — PAPER SIMULATION"
      />
    </div>
  );
}
