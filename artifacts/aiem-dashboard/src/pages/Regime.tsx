import { useApi } from "@/hooks/use-api";
import { Layers, Activity, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";

export default function Regime() {
  const { data: macro, loading } = useApi<any>("/stock-api/admin/macro/latest", {}, 30000);
  const { data: history } = useApi<any>("/stock-api/admin/macro/history?days=60", {}, 300000);

  const macroScore = macro?.macro_score ?? macro?.score ?? 50;
  const regime = macro?.regime ?? "NEUTRAL";
  const isBull = regime.startsWith("BULL");
  const isBear = regime.startsWith("BEAR");

  const chartData = (history?.rows ?? []).map((r: any) => ({
    date: r.date,
    score: r.score,
  }));

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Market Regime</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Macro Score & Regime Detection</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 flex-1 min-h-0">
        <div className="lg:col-span-1 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Layers size={14} /> CURRENT REGIME
            </h2>
          </div>
          <div className="p-6 flex flex-col items-center justify-center flex-1 font-mono text-center">
            {loading ? (
              <div className="text-muted-foreground">CALCULATING...</div>
            ) : (
              <>
                <div className={`p-6 border-2 rounded-full mb-6 ${
                  isBull ? 'border-success text-success bg-success/10' : 
                  isBear ? 'border-destructive text-destructive bg-destructive/10' : 'border-accent text-accent bg-accent/10'
                }`}>
                  {isBull ? <TrendingUp size={64} /> : 
                   isBear ? <TrendingDown size={64} /> : <Minus size={64} />}
                </div>
                <div className={`text-4xl font-bold mb-2 ${
                  isBull ? 'text-success' : isBear ? 'text-destructive' : 'text-accent'
                }`}>
                  {regime}
                </div>
                <div className="text-2xl text-white font-bold mb-8">
                  SCORE: {macroScore.toFixed(1)}
                </div>

                <div className="w-full space-y-4 text-left border-t border-border pt-6">
                  <div className="text-sm text-muted-foreground mb-2">DETAILS:</div>
                  {macro?.position_size_modifier != null && (
                    <div className="flex justify-between items-center text-xs">
                      <span className="uppercase text-muted-foreground">Size Modifier</span>
                      <span className="text-white">{macro.position_size_modifier?.toFixed(2) ?? "N/A"}×</span>
                    </div>
                  )}
                  {macro?.summary && (
                    <div className="text-xs text-muted-foreground italic border border-border p-2 bg-black/30">
                      {macro.summary}
                    </div>
                  )}
                  {macro?.components && Object.entries(macro.components).map(([key, val]: [string, any]) => (
                    <div key={key} className="flex justify-between items-center text-xs">
                      <span className="uppercase text-muted-foreground">{key.replace(/_/g, ' ')}</span>
                      <span className="text-white">{typeof val === 'number' ? val.toFixed(2) : val}</span>
                    </div>
                  ))}
                  {!macro?.components && !macro?.summary && (
                    <div className="text-xs text-muted-foreground italic">NO COMPONENTS AVAILABLE</div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        <div className="lg:col-span-2 border border-border bg-card flex flex-col h-full">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <Activity size={14} /> MACRO SCORE HISTORY
            </h2>
            <span className="text-xs font-mono text-muted-foreground">{chartData.length} DAYS</span>
          </div>
          <div className="p-4 flex-1 w-full min-h-0">
            {chartData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-muted-foreground font-mono text-sm">
                NO HISTORY DATA
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <XAxis 
                    dataKey="date" 
                    stroke="hsl(var(--muted-foreground))" 
                    fontSize={10} 
                    tickLine={false} 
                    axisLine={false}
                    tickFormatter={(val: string) => {
                      const parts = val.split('-');
                      return parts[1] + '/' + parts[2];
                    }}
                  />
                  <YAxis 
                    stroke="hsl(var(--muted-foreground))" 
                    fontSize={10} 
                    tickLine={false} 
                    axisLine={false}
                    domain={[0, 100]}
                  />
                  <Tooltip 
                    contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', borderRadius: 0, fontFamily: 'var(--font-mono)' }}
                    itemStyle={{ color: 'hsl(var(--primary))' }}
                    formatter={(val: number) => [val.toFixed(1), 'Score']}
                  />
                  <ReferenceLine y={50} stroke="hsl(var(--accent))" strokeDasharray="4 2" strokeOpacity={0.5} />
                  <Line 
                    type="monotone" 
                    dataKey="score" 
                    stroke="hsl(var(--primary))" 
                    strokeWidth={2} 
                    dot={false}
                    activeDot={{ r: 4, fill: 'hsl(var(--primary))' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
