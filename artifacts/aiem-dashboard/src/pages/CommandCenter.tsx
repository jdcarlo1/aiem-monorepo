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
    <div className="space-y-7 max-w-7xl">
      {/* Page header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between pb-5 border-b border-border">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Command Center</h1>
          <p className="text-base text-muted-foreground mt-1.5">Live system health, macro regime, and job orchestration</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap sm:justify-end">
          <div className="badge-live ok font-mono uppercase tracking-wide" title="D3 governance mode">
            {govModeLabel}
          </div>
          <div className={`badge-live ${sseConnected ? "ok" : "error"}`} title="SSE /stock-api/events/stream">
            <Radio size={14} className={sseConnected ? "text-success" : "text-destructive"} />
            {sseConnected ? "SSE Live" : "SSE Off"}
          </div>
          <div className={`badge-live ${engineOk && !healthStale ? "ok" : "error"}`}>
            <div className={`w-2 h-2 rounded-full ${engineOk && !healthStale ? "bg-success animate-pulse" : "bg-destructive"}`} />
            {engineOk && !healthStale ? "Engine Connected" : "Engine Offline"}
          </div>
        </div>
      </div>

      {/* Top stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {/* Macro Regime */}
        <div className="stat-card col-span-1 md:col-span-2 xl:col-span-1">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-mono text-muted-foreground uppercase tracking-wider">Macro Regime</span>
            <Activity size={16} className="text-primary" />
          </div>
          <div className={`text-3xl font-bold font-mono tracking-tight ${regimeColor}`}>
            {macro?.regime ?? (macroStale ? "STALE" : "—")}
          </div>
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono">Score</span>
              <span className="text-base font-mono font-bold text-white">
                {macroScore != null ? `${Number(macroScore).toFixed(1)} / 100` : "—"}
              </span>
            </div>
            {macroScore != null && (
              <div className="mt-2.5 h-1.5 bg-border rounded-full overflow-hidden">
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
            <span className="text-sm font-mono text-muted-foreground uppercase tracking-wider">Engine</span>
            <Server size={16} className="text-secondary" />
          </div>
          <div className={`text-3xl font-bold font-mono ${engineOk ? "text-success" : "text-destructive"}`}>
            {health?.status?.toUpperCase() ?? "—"}
          </div>
          <div className="mt-3 pt-3 border-t border-border space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono flex items-center gap-1.5"><Database size={12} />Database</span>
              <span className={`text-sm font-mono font-bold ${dbOk ? "text-success" : "text-destructive"}`}>
                {readyz?.database?.toUpperCase() ?? "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono flex items-center gap-1.5"><Cpu size={12} />Memory</span>
              <span className="text-sm font-mono text-white">{health?.rss_mb ? `${health.rss_mb.toFixed(0)} MB` : "—"}</span>
            </div>
          </div>
        </div>

        {/* Scheduler */}
        <div className="stat-card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-mono text-muted-foreground uppercase tracking-wider">Scheduler</span>
            <AlertCircle size={16} className="text-accent" />
          </div>
          <div className="text-3xl font-bold font-mono text-white">
            {jobs?.job_count ?? schedulerJobs.length ?? "—"}
          </div>
          <div className="text-sm text-muted-foreground font-mono mt-1">active jobs</div>
          <div className="mt-3 pt-3 border-t border-border">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono flex items-center gap-1.5"><Clock size={12} />Next fire</span>
              <span className="text-sm font-mono text-white">
                {schedulerJobs[0]?.next_run ? new Date(schedulerJobs[0].next_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
              </span>
            </div>
          </div>
        </div>

        {/* Heartbeat summary */}
        <div className="stat-card">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-mono text-muted-foreground uppercase tracking-wider">Heartbeats</span>
            <Zap size={16} className="text-primary" />
          </div>
          <div className="text-3xl font-bold font-mono text-white">{hbJobs.length}</div>
          <div className="text-sm text-muted-foreground font-mono mt-1">monitored jobs</div>
          <div className="mt-3 pt-3 border-t border-border space-y-1.5">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono">Healthy</span>
              <span className="text-sm font-mono font-bold text-success">{healthyJobs.length}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground font-mono">Failing</span>
              <span className={`text-sm font-mono font-bold ${failingJobs.length > 0 ? "text-destructive" : "text-muted-foreground"}`}>
                {failingJobs.length}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Failing jobs alert — surface last_error so schema/deploy issues are visible */}
      {failingJobs.length > 0 && (
        <div className="flex items-start gap-3 p-5 bg-destructive/10 border border-destructive/40 rounded-md">
          <AlertTriangle size={18} className="text-destructive shrink-0 mt-0.5" />
          <div className="min-w-0 space-y-3">
            <div>
              <div className="text-base font-semibold text-destructive mb-1">
                {failingJobs.length} job{failingJobs.length > 1 ? "s" : ""} reporting failures
              </div>
              <div className="text-sm text-muted-foreground font-mono">
                {failingJobs.map((j: any) => j.job_name).join(" · ")}
              </div>
            </div>
            {failingJobs.map((j: any) => (
              <div
                key={j.job_name}
                className="rounded border border-destructive/25 bg-background/50 p-3.5 space-y-1.5"
              >
                <div className="text-sm font-mono font-semibold text-destructive">
                  {j.job_name}
                  {j.consecutive_failures > 0
                    ? ` · ${j.consecutive_failures} consecutive fail${j.consecutive_failures > 1 ? "s" : ""}`
                    : ""}
                </div>
                {j.last_error ? (
                  <pre className="text-sm font-mono text-destructive/90 whitespace-pre-wrap break-words leading-relaxed">
                    {String(j.last_error).trim()}
                  </pre>
                ) : (
                  <div className="text-sm font-mono text-muted-foreground">
                    No last_error recorded — check job_heartbeats / deploy freshness
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Job Heartbeat Grid — readable density (was jammed 6-col tiny cards) */}
      <div className="panel">
        <div className="panel-header">
          <div className="flex items-center gap-2">
            <Zap size={14} className="text-primary" />
            <h2 className="text-base font-semibold text-white">Live Job Heartbeats</h2>
          </div>
          <span className="text-sm font-mono text-muted-foreground">job_heartbeats table · 30s refresh</span>
        </div>
        <div className="p-4 md:p-5">
          {hbJobs.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-muted-foreground">
              <AlertTriangle size={20} className="text-primary/50" />
              <span className="text-base font-mono">No heartbeat data — engine warming up</span>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3.5">
              {[...hbJobs]
                .sort((a: any, b: any) => (b.consecutive_failures > 0 ? 1 : 0) - (a.consecutive_failures > 0 ? 1 : 0))
                .map((job: any, i: number) => {
                const failing = job.consecutive_failures > 0;
                const lastSuccess = job.last_success ? new Date(job.last_success) : null;
                const age = lastSuccess ? Math.round((Date.now() - lastSuccess.getTime()) / 60000) : null;
                return (
                  <div
                    key={job.job_name ?? i}
                    className={`p-4 rounded-md border transition-all min-w-0 ${
                      failing
                        ? "border-destructive/50 bg-destructive/10 shadow-[0_0_18px_hsla(0,84%,58%,0.12)]"
                        : "border-border bg-card hover:border-primary/30 hover:shadow-[0_0_18px_hsla(38,95%,58%,0.1)]"
                    }`}
                    title={job.last_error ?? job.job_name}
                  >
                    <div className="flex items-center justify-between gap-2 mb-2.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${failing ? "bg-destructive animate-pulse" : "bg-success animate-pulse"}`} />
                        {failing && <AlertTriangle size={14} className="text-destructive shrink-0" />}
                        <div className="text-sm font-mono text-white font-semibold truncate" title={job.job_name}>
                          {job.job_name}
                        </div>
                      </div>
                      <div className="text-sm font-mono text-muted-foreground shrink-0">
                        {age != null ? (age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`) : "never"}
                      </div>
                    </div>
                    {failing ? (
                      <div className="space-y-1.5">
                        <div className="text-sm font-mono text-destructive font-semibold">
                          {job.consecutive_failures} consecutive fail{job.consecutive_failures > 1 ? "s" : ""}
                        </div>
                        {job.last_error ? (
                          <div className="text-sm font-mono text-destructive/85 line-clamp-3 leading-relaxed break-words">
                            {String(job.last_error).trim()}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      <div className="text-sm font-mono text-success/90">healthy</div>
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
          <div className="flex items-center gap-2.5">
            <Radio size={16} className="text-primary" />
            <h2 className="text-base font-semibold text-white">Live Event Stream</h2>
          </div>
          <span className="text-sm font-mono text-muted-foreground">
            /stock-api/events/stream · {sseConnected ? "connected" : "reconnecting"}
          </span>
        </div>
        <div className="p-4 space-y-2 max-h-56 overflow-auto">
          {liveEvents.length === 0 ? (
            <div className="text-sm font-mono text-muted-foreground py-5 text-center">
              Waiting for SSE events (paper_trades / decisions / system_health / candidates / gate_events)
            </div>
          ) : (
            liveEvents.map((evt, i) => (
              <div key={i} className="flex gap-3 text-sm font-mono border-b border-border/40 pb-1.5">
                <span className="text-primary shrink-0 font-semibold">{evt.category}</span>
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
