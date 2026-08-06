import { useState, useCallback, useEffect } from "react";
import { useApi } from "@/hooks/use-api";
import { getToken } from "@/lib/auth";
import { useEventStream, type SseEvent } from "@/hooks/use-event-stream";
import { Activity, Server, AlertCircle, AlertTriangle, Database, Clock, Cpu, Zap, Radio } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

const SSE_CATEGORIES = [
  "paper_trades",
  "decisions",
  "system_health",
  "candidates",
  "gate_events",
];

const SHADOW_BADGE = "D3 GOVERNANCE: SHADOW (logs only, does not block)";

export default function CommandCenter() {
  const { data: health, isStale: healthStale, lastUpdated: healthUpdated } = useApi<any>("/stock-api/health", {}, 30000);
  const { data: readyz } = useApi<any>("/stock-api/readyz", {}, 30000);
  const { data: macro, isStale: macroStale } = useApi<any>("/stock-api/admin/macro/latest", {}, 60000);
  const { data: jobs } = useApi<any>("/stock-api/admin/scheduler-jobs", {}, 60000);
  const { data: heartbeats } = useApi<any>("/stock-api/admin/job-heartbeats", {}, 30000);
  const [liveEvents, setLiveEvents] = useState<SseEvent[]>([]);
  const [govModeLabel, setGovModeLabel] = useState(SHADOW_BADGE);
  const onSse = useCallback((evt: SseEvent) => {
    setLiveEvents((prev) => [evt, ...prev].slice(0, 12));
  }, []);
  const { connected: sseConnected } = useEventStream(SSE_CATEGORIES, onSse, true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = getToken();
        const res = await fetch("/stock-api/admin/governance-modes", {
          headers: token ? { "X-Admin-Token": token } : {},
          credentials: "include",
        });
        if (cancelled) return;
        if (!res.ok) {
          setGovModeLabel(SHADOW_BADGE);
          return;
        }
        const body = await res.json();
        const mode = (body?.mode || body?.governance_mode || body?.d3_mode || "SHADOW").toString().toUpperCase();
        const blocking = body?.blocking === true || mode === "ENFORCE" || mode === "ACTIVE";
        setGovModeLabel(
          blocking
            ? `D3 GOVERNANCE: ${mode} (blocking)`
            : mode === "SHADOW"
              ? SHADOW_BADGE
              : `D3 GOVERNANCE: ${mode} (logs only, does not block)`
        );
      } catch {
        if (!cancelled) setGovModeLabel(SHADOW_BADGE);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const hbJobs: any[] = heartbeats?.jobs ?? [];
  const schedulerJobs: any[] = jobs?.jobs ?? [];
  const macroScore = macro?.macro_score ?? macro?.score;
  const engineOk = health?.status === "ok";
  const dbOk = readyz?.database === "up";
  const failingJobs = hbJobs.filter((j: any) => j.consecutive_failures > 0);
  const healthyJobs = hbJobs.filter((j: any) => j.consecutive_failures === 0);

  const regimeColor =
    macro?.regime?.startsWith("BULL") ? "text-success" :
    macro?.regime?.startsWith("BEAR") ? "text-destructive" :
    "text-primary";

  return (
    <div className="space-y-6 max-w-7xl">
      {/* Page header */}
      <div className="flex items-end justify-between pb-5 border-b border-border">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Command Center</h1>
          <p className="text-sm text-muted-foreground mt-1">Live system health, macro regime, and job orchestration</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <div className="badge-live ok font-mono text-[10px] uppercase tracking-wide" title="D3 governance mode">
            {govModeLabel}
          </div>
          <div className={`badge-live ${sseConnected ? "ok" : "error"}`} title="SSE /stock-api/events/stream">
            <Radio size={12} className={sseConnected ? "text-success" : "text-destructive"} />
            {sseConnected ? "SSE Live" : "SSE Off"}
          </div>
          <div className={`badge-live ${engineOk && !healthStale ? "ok" : "error"}`}>
            <div className={`w-1.5 h-1.5 rounded-full ${engineOk && !healthStale ? "bg-success animate-pulse" : "bg-destructive"}`} />
            {engineOk && !healthStale ? "Engine Connected" : "Engine Offline"}
          </div>
        </div>
      </div>

      {/* Top stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {/* Macro Regime */}
        <div className="stat-card col-span-1 md:col-span-2 xl:col-span-1">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">Macro Regime</span>
            <Activity size={14} className="text-primary" />
          </div>
          <div className={`text-2xl font-bold font-mono tracking-tight ${regimeColor}`}>
            {macro?.regime ?? (macroStale ? "STALE" : "—")}
          </div>
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono">Score</span>
              <span className="text-sm font-mono font-bold text-white">
                {macroScore != null ? `${Number(macroScore).toFixed(1)} / 100` : "—"}
              </span>
            </div>
            {macroScore != null && (
              <div className="mt-2 h-1 bg-border rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${macroScore >= 60 ? "bg-success" : macroScore >= 40 ? "bg-primary" : "bg-destructive"}`}
                  style={{ width: `${macroScore}%` }}
                />
              </div>
            )}
          </div>
        </div>

        {/* Engine Status */}
        <div className="stat-card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">Engine</span>
            <Server size={14} className="text-secondary" />
          </div>
          <div className={`text-2xl font-bold font-mono ${engineOk ? "text-success" : "text-destructive"}`}>
            {health?.status?.toUpperCase() ?? "—"}
          </div>
          <div className="mt-3 pt-3 border-t border-border space-y-1.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono flex items-center gap-1"><Database size={10} />Database</span>
              <span className={`text-xs font-mono font-bold ${dbOk ? "text-success" : "text-destructive"}`}>
                {readyz?.database?.toUpperCase() ?? "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono flex items-center gap-1"><Cpu size={10} />Memory</span>
              <span className="text-xs font-mono text-white">{health?.rss_mb ? `${health.rss_mb.toFixed(0)} MB` : "—"}</span>
            </div>
          </div>
        </div>

        {/* Scheduler */}
        <div className="stat-card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">Scheduler</span>
            <AlertCircle size={14} className="text-accent" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">
            {jobs?.job_count ?? schedulerJobs.length ?? "—"}
          </div>
          <div className="text-xs text-muted-foreground font-mono mt-0.5">active jobs</div>
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono flex items-center gap-1"><Clock size={10} />Next fire</span>
              <span className="text-xs font-mono text-white">
                {schedulerJobs[0]?.next_run ? new Date(schedulerJobs[0].next_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
              </span>
            </div>
          </div>
        </div>

        {/* Heartbeat summary */}
        <div className="stat-card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">Heartbeats</span>
            <Zap size={14} className="text-primary" />
          </div>
          <div className="text-2xl font-bold font-mono text-white">{hbJobs.length}</div>
          <div className="text-xs text-muted-foreground font-mono mt-0.5">monitored jobs</div>
          <div className="mt-3 pt-3 border-t border-border space-y-1.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono">Healthy</span>
              <span className="text-xs font-mono font-bold text-success">{healthyJobs.length}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground font-mono">Failing</span>
              <span className={`text-xs font-mono font-bold ${failingJobs.length > 0 ? "text-destructive" : "text-muted-foreground"}`}>
                {failingJobs.length}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Failing jobs alert */}
      {failingJobs.length > 0 && (
        <div className="flex items-start gap-3 p-4 bg-destructive/10 border border-destructive/30 rounded-md">
          <AlertTriangle size={16} className="text-destructive shrink-0 mt-0.5" />
          <div>
            <div className="text-sm font-semibold text-destructive mb-1">
              {failingJobs.length} job{failingJobs.length > 1 ? "s" : ""} reporting failures
            </div>
            <div className="text-xs text-muted-foreground font-mono">
              {failingJobs.map((j: any) => j.job_name).join(" · ")}
            </div>
          </div>
        </div>
      )}

      {/* Job Heartbeat Grid */}
      <div className="panel">
        <div className="panel-header">
          <div className="flex items-center gap-2">
            <Zap size={14} className="text-primary" />
            <h2 className="text-sm font-semibold text-white">Live Job Heartbeats</h2>
          </div>
          <span className="text-xs font-mono text-muted-foreground">job_heartbeats table · 30s refresh</span>
        </div>
        <div className="p-4">
          {hbJobs.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-muted-foreground">
              <AlertTriangle size={20} className="text-primary/50" />
              <span className="text-sm font-mono">No heartbeat data — engine warming up</span>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-2">
              {hbJobs.map((job: any, i: number) => {
                const failing = job.consecutive_failures > 0;
                const lastSuccess = job.last_success ? new Date(job.last_success) : null;
                const age = lastSuccess ? Math.round((Date.now() - lastSuccess.getTime()) / 60000) : null;
                return (
                  <div
                    key={i}
                    className={`p-2.5 rounded-md border transition-all ${
                      failing
                        ? "border-destructive/40 bg-destructive/5"
                        : "border-border bg-card hover:border-border/80"
                    }`}
                    title={job.last_error ?? job.job_name}
                  >
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${failing ? "bg-destructive" : "bg-success"}`} />
                      {failing && <AlertTriangle size={9} className="text-destructive shrink-0" />}
                    </div>
                    <div className="text-[10px] font-mono text-white truncate font-medium" title={job.job_name}>
                      {job.job_name.replace(/_/g, " ")}
                    </div>
                    <div className="text-[9px] font-mono text-muted-foreground mt-0.5">
                      {age != null ? (age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`) : "never"}
                    </div>
                    {failing && (
                      <div className="text-[9px] font-mono text-destructive mt-0.5">
                        {job.consecutive_failures} fail{job.consecutive_failures > 1 ? "s" : ""}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Live SSE event strip — previously useEventStream was defined but never mounted */}
      <div className="panel">
        <div className="panel-header">
          <div className="flex items-center gap-2">
            <Radio size={14} className="text-primary" />
            <h2 className="text-sm font-semibold text-white">Live Event Stream</h2>
          </div>
          <span className="text-xs font-mono text-muted-foreground">
            /stock-api/events/stream · {sseConnected ? "connected" : "reconnecting"}
          </span>
        </div>
        <div className="p-3 space-y-1.5 max-h-48 overflow-auto">
          {liveEvents.length === 0 ? (
            <div className="text-xs font-mono text-muted-foreground py-4 text-center">
              Waiting for SSE events (paper_trades / decisions / system_health / candidates / gate_events)
            </div>
          ) : (
            liveEvents.map((evt, i) => (
              <div key={i} className="flex gap-2 text-[11px] font-mono border-b border-border/40 pb-1">
                <span className="text-primary shrink-0">{evt.category}</span>
                <span className="text-muted-foreground truncate">
                  {typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      <DataFooter
        source="job_heartbeats, aiem_macro_daily, APScheduler, events/stream"
        lastUpdated={healthUpdated}
        pollIntervalSec={30}
        operatingMode="LIVE DATA"
      />
    </div>
  );
}
