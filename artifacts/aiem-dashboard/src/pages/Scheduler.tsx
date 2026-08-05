import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { getToken } from "@/lib/auth";
import { Calendar, Play, Clock, Server, Loader2 } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function Scheduler() {
  const { data, loading, lastUpdated: schedUpdated, refetch } = useApi<any>("/stock-api/admin/scheduler-jobs", {}, 60000);
  const [forcingId, setForcingId] = useState<string | null>(null);
  const [forceMsg, setForceMsg] = useState<string | null>(null);

  const jobs = data?.jobs || [];
  
  // Categorize jobs
  const categories = jobs.reduce((acc: any, job: any) => {
    let category = "UNCATEGORIZED";
    if (job.name?.includes('scan') || job.name?.includes('signal')) category = "SCANNING";
    else if (job.name?.includes('train') || job.name?.includes('model')) category = "ML PIPELINE";
    else if (job.name?.includes('macro') || job.name?.includes('regime')) category = "MACRO";
    else if (job.name?.includes('portfolio') || job.name?.includes('risk')) category = "PORTFOLIO";
    else if (job.name?.includes('audit') || job.name?.includes('verify')) category = "EVIDENCE";
    
    if (!acc[category]) acc[category] = [];
    acc[category].push(job);
    return acc;
  }, {});

  async function forceJob(jobId: string) {
    if (!jobId || forcingId) return;
    setForcingId(jobId);
    setForceMsg(null);
    try {
      const token = getToken();
      const res = await fetch(`/stock-api/admin/scheduler-jobs/${encodeURIComponent(jobId)}/force`, {
        method: "POST",
        headers: token ? { "X-Admin-Token": token } : {},
        credentials: "include",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setForceMsg(`FORCE failed (${res.status}): ${body.error || body.detail || "unknown"}`);
      } else {
        setForceMsg(`Forced ${body.name || jobId} — next_run=${body.forced_next_run || "now"}`);
        await refetch();
      }
    } catch (e: any) {
      setForceMsg(`FORCE error: ${e?.message || String(e)}`);
    } finally {
      setForcingId(null);
    }
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Scheduler Status</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Background Jobs & Executions</p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground mb-1">TOTAL JOBS</div>
          <div className="text-2xl font-mono font-bold text-primary">
            {data?.job_count ?? jobs.length}
          </div>
        </div>
      </div>

      {forceMsg && (
        <div className="px-3 py-2 border border-border bg-black font-mono text-xs text-primary">
          {forceMsg}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6 flex-1 min-h-0">
        <div className="xl:col-span-1 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Server size={14} /> CATEGORIES
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-4 space-y-2 font-mono text-sm">
            {Object.entries(categories).map(([cat, catJobs]: [string, any]) => (
              <div key={cat} className="flex justify-between items-center p-2 border border-border bg-black">
                <span className="text-muted-foreground">{cat}</span>
                <span className="text-white font-bold">{catJobs.length}</span>
              </div>
            ))}
            {Object.keys(categories).length === 0 && (
              <div className="text-center text-muted-foreground py-4">NO CATEGORY DATA</div>
            )}
          </div>
        </div>

        <div className="xl:col-span-3 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <Calendar size={14} /> JOB SCHEDULE
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">ID</th>
                  <th className="p-3 font-normal">JOB NAME</th>
                  <th className="p-3 font-normal">TRIGGER</th>
                  <th className="p-3 font-normal">NEXT RUN</th>
                  <th className="p-3 font-normal text-right">ACTION</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : jobs.length ? (
                  jobs.map((job: any, i: number) => {
                    const isSoon = job.next_run && new Date(job.next_run).getTime() - Date.now() < 3600000;
                    const busy = forcingId === job.id;
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{job.id}</td>
                        <td className="p-3 font-bold text-white">{job.name}</td>
                        <td className="p-3 text-secondary">{job.trigger}</td>
                        <td className={`p-3 font-bold flex items-center gap-2 ${isSoon ? 'text-primary animate-pulse' : 'text-success'}`}>
                          <Clock size={12} />
                          {job.next_run ? new Date(job.next_run).toLocaleString() : 'N/A'}
                        </td>
                        <td className="p-3 text-right">
                          <button
                            type="button"
                            disabled={!job.id || !!forcingId}
                            onClick={() => forceJob(job.id)}
                            className="px-2 py-1 bg-primary text-black text-xs hover:bg-primary/90 transition-colors inline-flex items-center gap-1 disabled:opacity-50"
                          >
                            {busy ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
                            FORCE
                          </button>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={5} className="p-6 text-center font-mono text-sm text-muted-foreground">
                      NO JOB DATA — API returned empty job list. Scheduler may be warming up.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <DataFooter
        source={`APScheduler (in-process) — ${data?.job_count ?? jobs.length} registered jobs`}
        lastUpdated={schedUpdated}
        pollIntervalSec={60}
        operatingMode="LIVE SCHEDULER STATE"
      />
    </div>
  );
}
