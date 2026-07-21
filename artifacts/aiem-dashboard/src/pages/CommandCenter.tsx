import { useApi } from "@/hooks/use-api";
import { Activity, Server, AlertCircle, AlertTriangle } from "lucide-react";

export default function CommandCenter() {
  const { data: health, isStale: healthStale } = useApi<any>("/stock-api/health", {}, 30000);
  const { data: macro, isStale: macroStale } = useApi<any>("/stock-api/admin/macro/latest", {}, 30000);
  const { data: jobs } = useApi<any>("/stock-api/admin/scheduler-jobs", {}, 60000);
  const { data: heartbeats } = useApi<any>("/stock-api/admin/job-heartbeats", {}, 30000);

  const hbJobs: any[] = heartbeats?.jobs ?? [];
  const schedulerJobs: any[] = jobs?.jobs ?? [];
  const macroScore = macro?.macro_score ?? macro?.score;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-end border-b border-border pb-4">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Command Center</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">System Health & Macro Regime Overview</p>
        </div>
        <div className="flex items-center gap-4">
          <div className={`flex items-center gap-2 font-mono text-xs px-3 py-1 border ${healthStale ? 'border-destructive text-destructive' : 'border-success text-success'}`}>
            <div className={`w-2 h-2 rounded-full ${healthStale ? 'bg-destructive' : 'bg-success animate-pulse'}`} />
            {healthStale ? 'API STALE' : 'API CONNECTED'}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">MACRO REGIME</h2>
            <Activity className="text-primary" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className={`text-3xl font-mono font-bold ${
              macro?.regime?.startsWith('BULL') ? 'text-success' :
              macro?.regime?.startsWith('BEAR') ? 'text-destructive' : 'text-primary'
            }`}>
              {macro?.regime ?? (macroStale ? 'STALE' : 'LOADING')}
            </span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">SCORE:</span>
              <span className="text-white">
                {macroScore != null ? `${Number(macroScore).toFixed(1)} / 100` : '---'}
              </span>
            </div>
          </div>
        </div>

        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">ENGINE STATUS</h2>
            <Server className="text-secondary" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className={`text-3xl font-mono font-bold ${health?.status === 'ok' ? 'text-success' : 'text-destructive'}`}>
              {health?.status?.toUpperCase() ?? 'UNAVAILABLE'}
            </span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">HEARTBEATS:</span>
              <span className="text-white">{hbJobs.length > 0 ? `${hbJobs.length} ACTIVE` : '---'}</span>
            </div>
          </div>
        </div>

        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">SCHEDULER</h2>
            <AlertCircle className="text-accent" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-mono font-bold text-white">
              {jobs?.job_count ?? schedulerJobs.length ?? '---'}
            </span>
            <span className="text-sm font-mono text-muted-foreground">JOBS</span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">NEXT FIRE:</span>
              <span className="text-white text-xs">
                {schedulerJobs[0]?.next_run
                  ? new Date(schedulerJobs[0].next_run).toLocaleTimeString()
                  : '---'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Live Job Health Grid — sourced from real job_heartbeats table */}
      <div className="border border-border bg-card">
        <div className="p-4 border-b border-border bg-sidebar/50 flex justify-between items-center">
          <h2 className="text-sm font-mono font-bold text-white">LIVE JOB HEARTBEATS</h2>
          <span className="text-xs font-mono text-muted-foreground">SOURCE: job_heartbeats table</span>
        </div>
        <div className="p-4">
          {hbJobs.length === 0 ? (
            <div className="text-center font-mono text-sm text-muted-foreground py-4 space-y-1">
              <AlertTriangle size={16} className="mx-auto text-accent" />
              <div>NO HEARTBEAT DATA — stock-api may be warming up</div>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
              {hbJobs.map((job: any, i: number) => {
                const hasFail = job.consecutive_failures > 0;
                const hasError = !!job.last_error;
                const isWarn = hasFail || hasError;
                return (
                  <div key={i} className="border border-border p-2 bg-black" title={job.last_error ?? ''}>
                    <div className="flex items-center justify-between mb-1">
                      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isWarn ? 'bg-destructive' : 'bg-success'}`} />
                      {isWarn && <AlertTriangle size={10} className="text-accent flex-shrink-0" />}
                    </div>
                    <div className="text-[9px] font-mono text-muted-foreground truncate" title={job.job_name}>
                      {job.job_name}
                    </div>
                    {hasFail && (
                      <div className="text-[8px] font-mono text-destructive">{job.consecutive_failures} fail(s)</div>
                    )}
                    <div className="text-[8px] font-mono text-muted-foreground/60 mt-0.5">
                      {job.last_success
                        ? new Date(job.last_success).toLocaleTimeString()
                        : 'never'}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
