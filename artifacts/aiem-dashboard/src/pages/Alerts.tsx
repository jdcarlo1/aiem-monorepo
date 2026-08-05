import { useApi } from "@/hooks/use-api";
import { Bell, Send, AlertTriangle, CheckCircle, XCircle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function Alerts() {
  const { data: heartbeats, loading, lastUpdated: alertUpdated } = useApi<any>("/stock-api/admin/job-heartbeats", {}, 30000);
  const { data: tgAlerts, loading: tgLoading } = useApi<any>("/stock-api/admin/telegram-alerts?limit=40", {}, 30000);

  const jobs: any[] = heartbeats?.jobs ?? [];
  const failedJobs = jobs.filter((j: any) => j.consecutive_failures > 0 || j.last_error);
  const okJobs = jobs.filter((j: any) => !j.consecutive_failures && !j.last_error);
  const telegramJob = jobs.find((j: any) => j.job_name?.toLowerCase().includes("telegram") || j.job_name?.toLowerCase().includes("notif"));
  const telegramOk = telegramJob ? !telegramJob.consecutive_failures && !telegramJob.last_error : null;
  const alertRows: any[] = tgAlerts?.rows ?? [];

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Alert Feed</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Job Heartbeat Failures & Notification Status — source: job_heartbeats
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Send size={14} /> JOB FAILURE LOG
            </h2>
            <span className="text-xs font-mono text-muted-foreground">
              {failedJobs.length} FAILED / {okJobs.length} OK
            </span>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">JOB NAME</th>
                  <th className="p-3 font-normal">LAST SUCCESS</th>
                  <th className="p-3 font-normal">LAST ATTEMPT</th>
                  <th className="p-3 font-normal">FAILURES</th>
                  <th className="p-3 font-normal">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : jobs.length ? (
                  jobs.map((job: any, i: number) => {
                    const isFailed = job.consecutive_failures > 0 || !!job.last_error;
                    return (
                      <tr key={i} className={`border-b border-border/50 hover:bg-white/5 ${isFailed ? 'bg-destructive/5' : ''}`}>
                        <td className="p-3 font-bold text-white text-xs">{job.job_name}</td>
                        <td className="p-3 text-xs text-muted-foreground">
                          {job.last_success ? new Date(job.last_success).toLocaleString() : <span className="text-destructive">NEVER</span>}
                        </td>
                        <td className="p-3 text-xs text-muted-foreground">
                          {job.last_attempt ? new Date(job.last_attempt).toLocaleString() : '---'}
                        </td>
                        <td className={`p-3 font-bold ${job.consecutive_failures > 0 ? 'text-destructive' : 'text-success'}`}>
                          {job.consecutive_failures ?? 0}
                        </td>
                        <td className="p-3">
                          {isFailed
                            ? <span className="flex items-center gap-1 text-destructive font-bold"><XCircle size={12} /> FAILED</span>
                            : <span className="flex items-center gap-1 text-success"><CheckCircle size={12} /> OK</span>
                          }
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">NO HEARTBEAT DATA</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
              <Bell size={14} /> ALERT STATUS
            </h2>
          </div>
          <div className="p-6 space-y-6 font-mono text-sm">
            <div className="space-y-3">
              <div className="text-xs text-muted-foreground border-b border-border pb-2">SUMMARY</div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">TOTAL JOBS TRACKED</span>
                <span className="text-white font-bold">{jobs.length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">JOBS OK</span>
                <span className="text-success font-bold">{okJobs.length}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">JOBS FAILED</span>
                <span className={`font-bold ${failedJobs.length > 0 ? 'text-destructive' : 'text-success'}`}>{failedJobs.length}</span>
              </div>
            </div>

            <div className="pt-4 border-t border-border space-y-2">
              <div className="text-xs text-muted-foreground">TELEGRAM NOTIFIER</div>
              {telegramJob == null ? (
                <div className="p-3 border border-accent bg-accent/10 text-accent flex items-center gap-2 text-xs">
                  <AlertTriangle size={14} />
                  <span>NOT TRACKED IN HEARTBEATS — no job_heartbeats entry matching "telegram" found</span>
                </div>
              ) : telegramOk ? (
                <div className="p-3 border border-success bg-success/10 text-success flex items-center justify-between">
                  <span className="font-bold">HEARTBEAT OK</span>
                  <span className="text-xs">{telegramJob.last_success ? new Date(telegramJob.last_success).toLocaleTimeString() : '---'}</span>
                </div>
              ) : (
                <div className="p-3 border border-destructive bg-destructive/10 text-destructive flex items-center gap-2 text-xs">
                  <XCircle size={14} />
                  <span>FAILURE DETECTED: {telegramJob.consecutive_failures} consecutive fail(s)</span>
                </div>
              )}
            </div>

            <div className="pt-4 border-t border-border">
              <div className="text-xs text-muted-foreground mb-2">LEDGER COUNT</div>
              <div className="text-white font-bold">{tgAlerts?.count ?? (tgLoading ? "…" : 0)}</div>
              <div className="text-xs text-muted-foreground mt-1">telegram_alert_ledger rows</div>
            </div>
          </div>
        </div>
      </div>

      <div className="border border-border bg-card flex flex-col min-h-[240px]">
        <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
          <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
            <Send size={14} /> TELEGRAM ALERT LEDGER
          </h2>
          <span className="text-xs font-mono text-muted-foreground">
            {alertRows.length} shown
          </span>
        </div>
        <div className="flex-1 overflow-auto p-0">
          <table className="w-full text-left font-mono text-sm border-collapse">
            <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
              <tr>
                <th className="p-3 font-normal">SENT</th>
                <th className="p-3 font-normal">TICKER</th>
                <th className="p-3 font-normal">SOURCE</th>
                <th className="p-3 font-normal">CLASS</th>
                <th className="p-3 font-normal">OK</th>
                <th className="p-3 font-normal">RESULT</th>
              </tr>
            </thead>
            <tbody>
              {tgLoading ? (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
              ) : alertRows.length ? (
                alertRows.map((row: any) => (
                  <tr key={row.id} className="border-b border-border/50 hover:bg-white/5">
                    <td className="p-3 text-xs text-muted-foreground">
                      {row.sent_at ? new Date(row.sent_at).toLocaleString() : "—"}
                    </td>
                    <td className="p-3 font-bold text-white">{row.ticker || "—"}</td>
                    <td className="p-3 text-xs text-secondary">{row.signal_source}</td>
                    <td className="p-3 text-xs">{row.alert_class}</td>
                    <td className={`p-3 font-bold ${row.sent_ok ? "text-success" : "text-destructive"}`}>
                      {row.sent_ok == null ? "—" : row.sent_ok ? "YES" : "NO"}
                    </td>
                    <td className="p-3 text-xs text-muted-foreground">{row.win_loss || (row.graded ? "graded" : "ungraded")}</td>
                  </tr>
                ))
              ) : (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">NO TELEGRAM LEDGER ROWS</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <DataFooter
        source="job_heartbeats, telegram_alert_ledger"
        lastUpdated={alertUpdated}
        pollIntervalSec={30}
        operatingMode="LIVE MONITORING"
      />
    </div>
  );
}
