import { useApi } from "@/hooks/use-api";
import { Users, Vote, Target, MessageSquare } from "lucide-react";

export default function Council() {
  const { data: council, loading } = useApi<any>("/stock-api/admin/council-runs?limit=50", {}, 60000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Specialist Council</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">AI Member Votes & Opinions</p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground mb-1">TOTAL RUNS</div>
          <div className="text-2xl font-mono font-bold text-primary">
            {council?.count || 0}
          </div>
        </div>
      </div>

      <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
        <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
          <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
            <Users size={14} /> COUNCIL RUNS
          </h2>
        </div>
        <div className="flex-1 overflow-auto p-0">
          <table className="w-full text-left font-mono text-sm border-collapse">
            <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
              <tr>
                <th className="p-3 font-normal">TIME</th>
                <th className="p-3 font-normal">CONTEXT</th>
                <th className="p-3 font-normal">TICKER</th>
                <th className="p-3 font-normal">WEIGHTED VOTE</th>
                <th className="p-3 font-normal">VARIANCE</th>
                <th className="p-3 font-normal">OPINIONS</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
              ) : council?.rows?.length ? (
                council.rows.map((row: any, i: number) => {
                  const voteColor = 
                    row.weighted_vote >= 0.6 ? 'text-success' :
                    row.weighted_vote <= 0.4 ? 'text-destructive' : 'text-accent';
                    
                  return (
                    <tr key={i} className="border-b border-border/50 hover:bg-white/5 group">
                      <td className="p-3 text-muted-foreground">{new Date(row.run_time).toLocaleTimeString()}</td>
                      <td className="p-3">
                        <span className="px-2 py-0.5 border border-border bg-black text-xs text-secondary">{row.context}</span>
                      </td>
                      <td className="p-3 font-bold text-white">{row.ticker}</td>
                      <td className={`p-3 font-bold ${voteColor}`}>{(row.weighted_vote * 100).toFixed(1)}%</td>
                      <td className="p-3">{row.variance?.toFixed(3)}</td>
                      <td className="p-3">
                        <div className="flex flex-col gap-1 max-h-24 overflow-y-auto pr-2">
                          {row.opinions && Object.entries(row.opinions).map(([member, details]: [string, any]) => (
                            <div key={member} className="text-xs flex flex-col border-b border-border/30 pb-1 mb-1 last:border-0 last:pb-0 last:mb-0">
                              <span className="font-bold text-muted-foreground">{member}: {details.vote > 0.5 ? 'YES' : 'NO'} ({(details.vote * 100).toFixed(0)}%)</span>
                              <span className="text-muted-foreground truncate" title={details.rationale}>{details.rationale}</span>
                            </div>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">NO COUNCIL RUNS</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
