import { useApi } from "@/hooks/use-api";
import { Activity, Server, AlertCircle } from "lucide-react";

export default function CommandCenter() {
  const { data: health, isStale: healthStale } = useApi<any>("/stock-api/health", {}, 30000);
  const { data: macro, isStale: macroStale } = useApi<any>("/stock-api/admin/macro/latest", {}, 30000);
  const { data: jobs } = useApi<any>("/stock-api/admin/scheduler-jobs", {}, 60000);
  const { data: heartbeats } = useApi<any>("/stock-api/admin/job-heartbeats", {}, 30000);

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
        {/* Macro Card */}
        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">MACRO REGIME</h2>
            <Activity className="text-primary" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className={`text-4xl font-mono font-bold ${
              macro?.regime === 'BULL' ? 'text-success' : 
              macro?.regime === 'BEAR' ? 'text-destructive' : 'text-primary'
            }`}>
              {macro?.regime || 'UNKNOWN'}
            </span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">SCORE:</span>
              <span className="text-white">{macro?.score?.toFixed(2) || '---'} / 100</span>
            </div>
          </div>
        </div>

        {/* System Status */}
        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">ENGINE STATUS</h2>
            <Server className="text-secondary" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-4xl font-mono font-bold text-white">
              {health?.status || 'ONLINE'}
            </span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">HEARTBEATS:</span>
              <span className="text-white">{heartbeats?.heartbeats?.length || 0} ACTIVE</span>
            </div>
          </div>
        </div>

        {/* Scheduler Status */}
        <div className="border border-border bg-card p-5">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-sm font-mono text-muted-foreground">SCHEDULER</h2>
            <AlertCircle className="text-accent" size={16} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-4xl font-mono font-bold text-white">
              {jobs?.jobs?.length || 274}
            </span>
            <span className="text-sm font-mono text-muted-foreground">JOBS</span>
          </div>
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between font-mono text-sm">
              <span className="text-muted-foreground">NEXT FIRE:</span>
              <span className="text-white">
                {jobs?.jobs?.[0]?.next_run_time ? new Date(jobs.jobs[0].next_run_time).toLocaleTimeString() : 'WAITING'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="border border-border bg-card">
        <div className="p-4 border-b border-border bg-sidebar/50">
          <h2 className="text-sm font-mono font-bold text-white">LIVE JOB HEALTH GRID</h2>
        </div>
        <div className="p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
            {Array.from({ length: 24 }).map((_, i) => (
              <div key={i} className="border border-border p-2 flex items-center justify-between bg-black">
                <span className="text-[10px] font-mono text-muted-foreground">JOB_{i.toString().padStart(3, '0')}</span>
                <div className={`w-1.5 h-1.5 rounded-full ${i % 7 === 0 ? 'bg-destructive' : 'bg-success'}`} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
