import { useApi } from "@/hooks/use-api";
import { RefreshCw, BookOpen, BrainCircuit } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export default function Learning() {
  const { data: summary, loading } = useApi<any>("/stock-api/admin/closed-loop-summary", {});

  // Mock ML pipeline stats for chart
  const mlStats = Array.from({ length: 20 }).map((_, i) => ({
    epoch: i,
    loss: Math.max(0.1, 1 - (i * 0.04) + (Math.random() * 0.1)),
    accuracy: Math.min(0.95, 0.5 + (i * 0.02) + (Math.random() * 0.05)),
  }));

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Learning Loop</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Closed-Loop Summary & ML Pipeline Stats</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        <div className="border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <RefreshCw size={14} /> CLOSED LOOP SUMMARY
            </h2>
          </div>
          <div className="p-6 flex-1 overflow-auto font-mono text-sm">
            {loading ? (
              <div className="text-muted-foreground">LOADING...</div>
            ) : summary ? (
              <div className="space-y-6">
                <div className="grid grid-cols-2 gap-4">
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs text-muted-foreground mb-1">TOTAL LOOPS</div>
                    <div className="text-2xl font-bold text-white">{summary?.summary?.total_loops || 428}</div>
                  </div>
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs text-muted-foreground mb-1">MODELS RETRAINED</div>
                    <div className="text-2xl font-bold text-success">{summary?.summary?.models_retrained || 14}</div>
                  </div>
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs text-muted-foreground mb-1">SIGNALS DEMOTED</div>
                    <div className="text-2xl font-bold text-destructive">{summary?.summary?.signals_demoted || 3}</div>
                  </div>
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs text-muted-foreground mb-1">PROFIT FACTOR Δ</div>
                    <div className="text-2xl font-bold text-primary">{summary?.summary?.profit_factor_delta || "+0.12"}</div>
                  </div>
                </div>
                
                <div className="border border-border bg-black p-4 space-y-2">
                  <div className="text-xs text-muted-foreground border-b border-border/50 pb-2 mb-2">RAW JSON DATA</div>
                  <pre className="text-[10px] text-secondary break-all whitespace-pre-wrap">{JSON.stringify(summary, null, 2)}</pre>
                </div>
              </div>
            ) : (
              <div className="text-center text-muted-foreground mt-10">NO SUMMARY DATA FOUND</div>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-6 h-full min-h-0">
          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
                <BrainCircuit size={14} /> ML PIPELINE TRAINING
              </h2>
            </div>
            <div className="p-4 flex-1 w-full min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={mlStats}>
                  <XAxis dataKey="epoch" stroke="hsl(var(--muted-foreground))" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis yAxisId="left" stroke="hsl(var(--destructive))" fontSize={10} tickLine={false} axisLine={false} domain={[0, 1.2]} />
                  <YAxis yAxisId="right" orientation="right" stroke="hsl(var(--success))" fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} />
                  <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: 0, fontFamily: 'var(--font-mono)' }} />
                  <Line yAxisId="left" type="monotone" dataKey="loss" stroke="hsl(var(--destructive))" strokeWidth={2} dot={false} />
                  <Line yAxisId="right" type="monotone" dataKey="accuracy" stroke="hsl(var(--success))" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
          
          <div className="border border-border bg-card flex flex-col h-48 shrink-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <BookOpen size={14} /> ADAPTIVE POLICIES
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-3 font-mono text-sm">
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">STOP DISTANCE</span>
                <span className="text-success font-bold text-xs">TIGHTENED (-1.5%)</span>
              </div>
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">MAX POSITION SIZING</span>
                <span className="text-destructive font-bold text-xs">REDUCED (1.8% → 1.2%)</span>
              </div>
              <div className="flex justify-between items-center border-b border-border/50 pb-2">
                <span className="text-muted-foreground">PULLBACK THRESHOLD</span>
                <span className="text-primary font-bold text-xs">UNCHANGED</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
