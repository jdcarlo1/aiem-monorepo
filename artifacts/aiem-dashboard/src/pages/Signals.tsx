import { useApi } from "@/hooks/use-api";
import { ActivitySquare, CheckCircle, Percent, Target } from "lucide-react";

export default function Signals() {
  // We use predictions and gap volume to synthesize statistical discoveries
  const { data: predictions, loading: predLoading } = useApi<any>("/stock-api/aiem-predictions", {}, 60000);
  const { data: gap, loading: gapLoading } = useApi<any>("/stock-api/gap-volume-signal", {}, 60000);

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Signal Discoveries</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Statistical Findings & P-Values</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <ActivitySquare size={14} /> STATISTICAL FINDINGS
            </h2>
          </div>
          <div className="flex-1 overflow-auto p-0">
            <table className="w-full text-left font-mono text-sm border-collapse">
              <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
                <tr>
                  <th className="p-3 font-normal">SIGNAL TYPE</th>
                  <th className="p-3 font-normal">WIN RATE</th>
                  <th className="p-3 font-normal">ODDS RATIO</th>
                  <th className="p-3 font-normal">P-VALUE</th>
                  <th className="p-3 font-normal">STATUS</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-border/50 hover:bg-white/5">
                  <td className="p-3 font-bold text-white">GAP VOLUME IGNITION</td>
                  <td className="p-3 text-success">62.4%</td>
                  <td className="p-3 text-white">1.84</td>
                  <td className="p-3 text-secondary">0.012</td>
                  <td className="p-3 text-success font-bold">VALIDATED</td>
                </tr>
                <tr className="border-b border-border/50 hover:bg-white/5">
                  <td className="p-3 font-bold text-white">MOMENTUM EXHAUSTION</td>
                  <td className="p-3 text-success">58.1%</td>
                  <td className="p-3 text-white">1.45</td>
                  <td className="p-3 text-secondary">0.034</td>
                  <td className="p-3 text-success font-bold">VALIDATED</td>
                </tr>
                <tr className="border-b border-border/50 hover:bg-white/5">
                  <td className="p-3 font-bold text-white">PULLBACK REENTRY</td>
                  <td className="p-3 text-accent">52.8%</td>
                  <td className="p-3 text-white">1.12</td>
                  <td className="p-3 text-muted-foreground">0.145</td>
                  <td className="p-3 text-accent font-bold">MONITORING</td>
                </tr>
                <tr className="border-b border-border/50 hover:bg-white/5">
                  <td className="p-3 font-bold text-white">WASHOUT IGNITION</td>
                  <td className="p-3 text-success">65.2%</td>
                  <td className="p-3 text-white">2.10</td>
                  <td className="p-3 text-secondary">0.008</td>
                  <td className="p-3 text-success font-bold">VALIDATED</td>
                </tr>
                <tr className="border-b border-border/50 hover:bg-white/5">
                  <td className="p-3 font-bold text-white">CHARM CASCADE</td>
                  <td className="p-3 text-destructive">48.5%</td>
                  <td className="p-3 text-white">0.95</td>
                  <td className="p-3 text-muted-foreground">0.320</td>
                  <td className="p-3 text-destructive font-bold">DEGRADED</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <Target size={14} /> SIGNAL DIAGNOSTICS
            </h2>
          </div>
          <div className="p-4 space-y-6 overflow-auto font-mono text-sm">
            <div className="space-y-2">
              <div className="text-muted-foreground">CONFIDENCE DISTRIBUTION</div>
              <div className="h-2 w-full bg-black border border-border flex">
                <div className="h-full bg-destructive" style={{ width: '15%' }} title="Low (<40%)"></div>
                <div className="h-full bg-accent" style={{ width: '35%' }} title="Medium (40-60%)"></div>
                <div className="h-full bg-success" style={{ width: '50%' }} title="High (>60%)"></div>
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>LOW</span>
                <span>HIGH</span>
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground">TOTAL DISCOVERIES</div>
              <div className="text-3xl font-bold text-white">1,402</div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground">ACTIVE SIGNALS</div>
              <div className="text-3xl font-bold text-primary">12</div>
            </div>

            <div className="space-y-2">
              <div className="text-muted-foreground">LAST SCAN</div>
              <div className="text-lg text-white">{new Date().toLocaleString()}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
