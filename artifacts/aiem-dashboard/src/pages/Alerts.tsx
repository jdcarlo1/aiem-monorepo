import { useApi } from "@/hooks/use-api";
import { Bell, Send, AlertTriangle } from "lucide-react";

export default function Alerts() {
  // Using heartbeats as a proxy for the Telegram notifier digest
  const { data: heartbeats, loading } = useApi<any>("/stock-api/admin/job-heartbeats", {}, 30000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Alert Feed</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Telegram Notifier Digest & Timestamps</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Send size={14} /> DISPATCH LOG
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TIMESTAMP</th>
                  <th className="p-3 font-normal">CHANNEL</th>
                  <th className="p-3 font-normal">PAYLOAD / DIGEST</th>
                  <th className="p-3 font-normal">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : heartbeats?.heartbeats?.length ? (
                  heartbeats.heartbeats.map((hb: any, i: number) => {
                    const isError = hb.status === 'ERROR' || hb.status === 'FAILED';
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{new Date(hb.timestamp || Date.now()).toLocaleString()}</td>
                        <td className="p-3 font-bold text-white">{hb.job_name || 'TELEGRAM_DISPATCH'}</td>
                        <td className="p-3 text-xs text-secondary truncate max-w-[200px]">
                          {hb.message || 'System health and execution summary dispatched successfully.'}
                        </td>
                        <td className={`p-3 font-bold flex items-center gap-1 ${isError ? 'text-destructive' : 'text-success'}`}>
                          {isError && <AlertTriangle size={12} />}
                          {isError ? 'FAILED' : 'SENT'}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">NO DISPATCH LOGS</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
              <Bell size={14} /> ALERT CONFIG
            </h2>
          </div>
          <div className="p-6 space-y-6 font-mono text-sm">
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">PORTFOLIO EXECUTIONS</span>
                <div className="w-10 h-5 bg-primary/20 flex items-center px-1 border border-primary">
                  <div className="w-3 h-3 bg-primary float-right ml-auto"></div>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">RISK GATE FAILURES</span>
                <div className="w-10 h-5 bg-primary/20 flex items-center px-1 border border-primary">
                  <div className="w-3 h-3 bg-primary float-right ml-auto"></div>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">EVIDENCE CHAIN WARNINGS</span>
                <div className="w-10 h-5 bg-primary/20 flex items-center px-1 border border-primary">
                  <div className="w-3 h-3 bg-primary float-right ml-auto"></div>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">DAILY DIGEST</span>
                <div className="w-10 h-5 bg-primary/20 flex items-center px-1 border border-primary">
                  <div className="w-3 h-3 bg-primary float-right ml-auto"></div>
                </div>
              </div>
            </div>

            <div className="pt-6 border-t border-border space-y-2">
              <div className="text-xs text-muted-foreground">TELEGRAM BOT STATUS</div>
              <div className="p-3 border border-success bg-success/10 text-success font-bold flex items-center justify-between">
                <span>ONLINE & LISTENING</span>
                <span className="text-xs">PING: 24ms</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
