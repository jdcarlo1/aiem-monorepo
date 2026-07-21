import { useApi } from "@/hooks/use-api";
import { Layers, Activity, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export default function Regime() {
  const { data: macro, loading } = useApi<any>("/stock-api/admin/macro/latest", {}, 30000);

  // Mock historical data for the chart since the API only gives latest
  const historicalData = Array.from({ length: 30 }).map((_, i) => ({
    date: new Date(Date.now() - (29 - i) * 24 * 60 * 60 * 1000).toLocaleDateString(),
    score: 40 + Math.random() * 40 + (i > 15 ? 10 : 0),
  }));

  const isBull = macro?.regime === 'BULL';
  const isBear = macro?.regime === 'BEAR';

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
                <div className={`text-5xl font-bold mb-2 ${
                  isBull ? 'text-success' : isBear ? 'text-destructive' : 'text-accent'
                }`}>
                  {macro?.regime || 'NEUTRAL'}
                </div>
                <div className="text-2xl text-white font-bold mb-8">
                  SCORE: {macro?.score?.toFixed(2) || '50.00'}
                </div>

                <div className="w-full space-y-4 text-left border-t border-border pt-6">
                  <div className="text-sm text-muted-foreground mb-2">COMPONENTS:</div>
                  {macro?.components && Object.entries(macro.components).map(([key, val]: [string, any]) => (
                    <div key={key} className="flex justify-between items-center text-xs">
                      <span className="uppercase text-muted-foreground">{key.replace(/_/g, ' ')}</span>
                      <span className="text-white">{typeof val === 'number' ? val.toFixed(2) : val}</span>
                    </div>
                  ))}
                  {!macro?.components && (
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
          </div>
          <div className="p-4 flex-1 w-full min-h-0">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={historicalData}>
                <XAxis 
                  dataKey="date" 
                  stroke="hsl(var(--muted-foreground))" 
                  fontSize={10} 
                  tickLine={false} 
                  axisLine={false}
                  tickFormatter={(val) => val.split('/')[0] + '/' + val.split('/')[1]}
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
                />
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
          </div>
        </div>
      </div>
    </div>
  );
}
