import { useApi } from "@/hooks/use-api";
import { AlertTriangle, TrendingDown, Layers, Crosshair } from "lucide-react";

export default function Risk() {
  const { data: positions, loading: posLoading } = useApi<any>("/stock-api/admin/position-sizing-log?limit=50", {}, 60000);
  const { data: gamma, loading: gammaLoading } = useApi<any>("/stock-api/gamma-wall", {}, 60000);
  const { data: charm, loading: charmLoading } = useApi<any>("/stock-api/charm-cascade", {}, 60000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Portfolio Risk</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Sizing Limits & Option Greeks Exposures</p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground mb-1">TOTAL SIZING ROWS</div>
          <div className="text-2xl font-mono font-bold text-primary">
            {positions?.count || 0}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 flex-1 min-h-0">
        {/* Position Sizing Log */}
        <div className="xl:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Crosshair size={14} /> POSITION SIZING LOG
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">TIME</th>
                  <th className="p-3 font-normal">TICKER</th>
                  <th className="p-3 font-normal">NOTIONAL</th>
                  <th className="p-3 font-normal">RISK %</th>
                  <th className="p-3 font-normal">STOP DIST</th>
                  <th className="p-3 font-normal">GATE</th>
                </tr>
              </thead>
              <tbody>
                {posLoading ? (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                ) : positions?.rows?.length ? (
                  positions.rows.map((row: any, i: number) => {
                    const gateColor = 
                      row.gate_result === 'PASS' ? 'text-success' :
                      row.gate_result === 'FAIL' ? 'text-destructive' : 'text-accent';
                      
                    return (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{new Date(row.logged_at).toLocaleTimeString()}</td>
                        <td className="p-3 font-bold text-white">{row.ticker}</td>
                        <td className="p-3">${row.calculated_notional?.toLocaleString() || '0'}</td>
                        <td className="p-3 text-secondary">{(row.risk_pct_used * 100).toFixed(2)}%</td>
                        <td className="p-3">{(row.stop_distance_pct * 100).toFixed(2)}%</td>
                        <td className={`p-3 font-bold ${gateColor}`}>{row.gate_result}</td>
                      </tr>
                    );
                  })
                ) : (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">NO SIZING LOGS</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="flex flex-col gap-6 h-full min-h-0">
          {/* Gamma Wall */}
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <Layers size={14} className="text-secondary" /> GAMMA WALLS
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-0">
              <table className="w-full text-left font-mono text-sm border-collapse">
                <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs">
                  <tr>
                    <th className="p-3 font-normal">TICKER</th>
                    <th className="p-3 font-normal">LEVEL</th>
                  </tr>
                </thead>
                <tbody>
                  {gammaLoading ? (
                    <tr><td colSpan={2} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                  ) : gamma?.walls?.length ? (
                    gamma.walls.map((w: any, i: number) => (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 font-bold text-white">{w.ticker}</td>
                        <td className="p-3 text-primary">{w.level || w.strike}</td>
                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan={2} className="p-4 text-center text-muted-foreground">NO GAMMA WALLS</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          
          {/* Charm Cascade */}
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <TrendingDown size={14} className="text-destructive" /> CHARM CASCADE
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-0">
              <table className="w-full text-left font-mono text-sm border-collapse">
                <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs">
                  <tr>
                    <th className="p-3 font-normal">TICKER</th>
                    <th className="p-3 font-normal">RISK METRIC</th>
                  </tr>
                </thead>
                <tbody>
                  {charmLoading ? (
                    <tr><td colSpan={2} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                  ) : charm?.signals?.length ? (
                    charm.signals.map((c: any, i: number) => (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 font-bold text-white">{c.ticker}</td>
                        <td className="p-3 text-destructive">{c.metric || c.score?.toFixed(2)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan={2} className="p-4 text-center text-muted-foreground">NO CASCADE RISKS</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
