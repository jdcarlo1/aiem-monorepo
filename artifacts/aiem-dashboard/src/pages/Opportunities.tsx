import { useApi } from "@/hooks/use-api";
import { Search, TrendingUp, AlertCircle, RefreshCw } from "lucide-react";

export default function Opportunities() {
  const { data: predictions, loading: predLoading } = useApi<any>("/stock-api/aiem-predictions", {}, 60000);
  const { data: gaps, loading: gapLoading } = useApi<any>("/stock-api/gap-volume-signal", {}, 60000);
  
  // On-demand signals
  const { data: washouts, loading: washoutLoading, refetch: refetchWashout } = useApi<any>("/stock-api/washout-ignition-signal", {});
  const { data: pullbacks, loading: pullLoading, refetch: refetchPullbacks } = useApi<any>("/stock-api/pullback-reentry", {});
  const { data: momentum, loading: momLoading, refetch: refetchMomentum } = useApi<any>("/stock-api/momentum-exhaustion", {});

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Opportunity Queue</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Ranked Candidates & Signal Discoveries</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => refetchWashout()} className="px-3 py-1 border border-border bg-card text-xs font-mono text-muted-foreground hover:text-primary transition-colors flex items-center gap-2">
            <RefreshCw size={12} className={washoutLoading ? "animate-spin" : ""} /> WASHOUT
          </button>
          <button onClick={() => refetchPullbacks()} className="px-3 py-1 border border-border bg-card text-xs font-mono text-muted-foreground hover:text-primary transition-colors flex items-center gap-2">
            <RefreshCw size={12} className={pullLoading ? "animate-spin" : ""} /> PULLBACK
          </button>
          <button onClick={() => refetchMomentum()} className="px-3 py-1 border border-border bg-card text-xs font-mono text-muted-foreground hover:text-primary transition-colors flex items-center gap-2">
            <RefreshCw size={12} className={momLoading ? "animate-spin" : ""} /> MOMENTUM
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        {/* Main Predictions */}
        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <TrendingUp size={14} /> AIEM PREDICTIONS
            </h2>
            <span className="text-xs font-mono text-muted-foreground">{predictions?.predictions?.length || 0} CANDIDATES</span>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs">
                <tr>
                  <th className="p-3 font-normal">TICKER</th>
                  <th className="p-3 font-normal">SCORE</th>
                  <th className="p-3 font-normal">CONFIDENCE</th>
                  <th className="p-3 font-normal">SOURCE</th>
                </tr>
              </thead>
              <tbody>
                {predLoading ? (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">SCANNING...</td></tr>
                ) : predictions?.predictions?.length ? (
                  predictions.predictions.map((p: any, i: number) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                      <td className="p-3 font-bold text-white">{p.ticker}</td>
                      <td className="p-3 text-primary">{p.score?.toFixed(2)}</td>
                      <td className="p-3">{(p.confidence * 100).toFixed(1)}%</td>
                      <td className="p-3 text-xs text-muted-foreground">{p.signal_source}</td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">NO PREDICTIONS FOUND</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="flex flex-col gap-6 h-full min-h-0">
          {/* Gap Volume */}
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
                <Search size={14} /> GAP VOLUME SIGNALS
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-0">
              <table className="w-full text-left font-mono text-sm border-collapse">
                <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs">
                  <tr>
                    <th className="p-3 font-normal">TICKER</th>
                    <th className="p-3 font-normal">DIRECTION</th>
                    <th className="p-3 font-normal">MAGNITUDE</th>
                  </tr>
                </thead>
                <tbody>
                  {gapLoading ? (
                    <tr><td colSpan={3} className="p-4 text-center text-muted-foreground">LOADING...</td></tr>
                  ) : gaps?.signals?.length ? (
                    gaps.signals.map((s: any, i: number) => (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                        <td className="p-3 font-bold text-white">{s.ticker}</td>
                        <td className={`p-3 ${s.direction === 'UP' ? 'text-success' : 'text-destructive'}`}>{s.direction}</td>
                        <td className="p-3">{s.magnitude}</td>
                      </tr>
                    ))
                  ) : (
                    <tr><td colSpan={3} className="p-4 text-center text-muted-foreground">NO SIGNALS</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          
          {/* Recent Signal Discoveries */}
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <AlertCircle size={14} className="text-accent" /> ON-DEMAND SCANS
              </h2>
            </div>
            <div className="p-4 space-y-4 font-mono text-sm overflow-auto">
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">WASHOUT IGNITION</span>
                <span className="text-white">{washouts?.signals?.length || 0} HITS</span>
              </div>
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">PULLBACK REENTRY</span>
                <span className="text-white">{pullbacks?.signals?.length || 0} HITS</span>
              </div>
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">MOMENTUM EXHAUSTION</span>
                <span className="text-white">{momentum?.signals?.length || 0} HITS</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
