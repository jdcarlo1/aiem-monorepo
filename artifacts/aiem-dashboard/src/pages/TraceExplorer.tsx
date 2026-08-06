import { useState, FormEvent } from "react";
import { getToken } from "@/lib/auth";
import { Search, FileWarning, Layers, Activity, ShieldAlert, BarChart2 } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

type TraceResult = {
  ticker?: string;
  date?: string;
  paper_trades?: any[];
  council_runs?: any[];
  position_sizing?: any[];
  governance_decisions?: any[];
  gate_events?: any[];
  paper_trades_error?: string;
  council_runs_error?: string;
  position_sizing_error?: string;
  governance_decisions_error?: string;
  gate_events_error?: string;
  elapsed_ms?: number;
  error?: string;
  detail?: string;
};

const SECTIONS: {
  key: keyof TraceResult;
  errorKey: keyof TraceResult;
  title: string;
  icon: typeof Activity;
  color: string;
}[] = [
  { key: "paper_trades", errorKey: "paper_trades_error", title: "PAPER TRADES", icon: BarChart2, color: "text-primary" },
  { key: "council_runs", errorKey: "council_runs_error", title: "COUNCIL RUNS", icon: Layers, color: "text-secondary" },
  { key: "position_sizing", errorKey: "position_sizing_error", title: "POSITION SIZING", icon: Activity, color: "text-accent" },
  { key: "governance_decisions", errorKey: "governance_decisions_error", title: "GOVERNANCE DECISIONS", icon: Search, color: "text-primary" },
  { key: "gate_events", errorKey: "gate_events_error", title: "GATE EVENTS", icon: ShieldAlert, color: "text-secondary" },
];

function RowPreview({ row }: { row: Record<string, unknown> }) {
  const entries = Object.entries(row).slice(0, 8);
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-xs font-mono">
      {entries.map(([k, v]) => (
        <div key={k} className="truncate">
          <span className="text-muted-foreground">{k}: </span>
          <span className="text-white">
            {v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function TraceExplorer() {
  const [ticker, setTicker] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TraceResult | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t || !date) {
      setError("ticker and date (YYYY-MM-DD) required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const qs = new URLSearchParams({ ticker: t, date });
      const res = await fetch(`/stock-api/admin/trace-explorer?${qs}`, {
        headers: token ? { "X-Admin-Token": token } : {},
        credentials: "include",
      });
      const body = (await res.json().catch(() => ({}))) as TraceResult;
      if (!res.ok) {
        setError(body.error || body.detail || `HTTP ${res.status}`);
        setResult(null);
      } else {
        setResult(body);
        setLastUpdated(new Date());
      }
    } catch (err: any) {
      setError(err?.message || String(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Trace Explorer</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Composite ticker+date audit across paper, council, sizing, governance, gates
          </p>
        </div>
        {result?.elapsed_ms != null && (
          <div className="text-right font-mono text-xs text-muted-foreground">
            QUERY {result.elapsed_ms}ms
          </div>
        )}
      </div>

      <form
        onSubmit={handleSubmit}
        className="border border-border bg-card p-4 flex flex-wrap items-end gap-4 shrink-0"
      >
        <div className="space-y-1.5">
          <label className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">Ticker</label>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="bg-black border border-border px-3 py-2 font-mono text-sm text-white focus:outline-none focus:border-primary w-32 uppercase"
            placeholder="AAPL"
            maxLength={12}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">Date</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-black border border-border px-3 py-2 font-mono text-sm text-white focus:outline-none focus:border-primary"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !ticker.trim() || !date}
          className="bg-sidebar border border-border text-white font-mono font-bold px-5 py-2 uppercase tracking-wider hover:bg-white/5 transition-colors flex items-center gap-2 disabled:opacity-50"
        >
          <Search size={14} />
          {loading ? "QUERYING..." : "TRACE"}
        </button>
      </form>

      {error && (
        <div className="px-3 py-2 border border-destructive/40 bg-destructive/10 font-mono text-xs text-destructive flex items-center gap-2">
          <FileWarning size={12} />
          {error}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto space-y-4">
        {!result && !loading && !error && (
          <div className="border border-border bg-card p-8 text-center font-mono text-sm text-muted-foreground">
            Enter ticker + date and submit to load the composite trace.
          </div>
        )}

        {result &&
          SECTIONS.map(({ key, errorKey, title, icon: Icon, color }) => {
            const rows = (result[key] as any[] | undefined) ?? [];
            const sectionError = result[errorKey] as string | undefined;
            return (
              <div key={key} className="border border-border bg-card flex flex-col">
                <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
                  <h2 className={`text-sm font-mono font-bold flex items-center gap-2 ${color}`}>
                    <Icon size={14} /> {title}
                  </h2>
                  <span className="text-xs font-mono text-muted-foreground">
                    {sectionError ? "ERROR" : `${rows.length} row${rows.length !== 1 ? "s" : ""}`}
                  </span>
                </div>
                {sectionError ? (
                  <div className="p-4 font-mono text-xs text-destructive flex items-center gap-2">
                    <FileWarning size={12} />
                    {sectionError}
                  </div>
                ) : rows.length === 0 ? (
                  <div className="p-4 font-mono text-xs text-muted-foreground text-center">NO ROWS</div>
                ) : (
                  <div className="divide-y divide-border/50">
                    {rows.map((row, i) => (
                      <div key={i} className="p-3 hover:bg-white/5">
                        <RowPreview row={row} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
      </div>

      <DataFooter
        source="aiem_paper_trades, council_runs, position_sizing, d3_governance, oe_gate_events"
        lastUpdated={lastUpdated}
        operatingMode="AUDIT READ-ONLY"
      />
    </div>
  );
}
