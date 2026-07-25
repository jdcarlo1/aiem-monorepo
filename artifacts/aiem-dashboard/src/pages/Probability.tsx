import { useApi } from "@/hooks/use-api";
import { BrainCircuit, AlertTriangle, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

function fmt(v: number | null | undefined, decimals = 2, suffix = "") {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(decimals)}${suffix}`;
}

function pct(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function rawFmt(v: number | null | undefined, d = 3) {
  if (v === null || v === undefined) return "—";
  return v.toFixed(d);
}

const PIT_COLORS: Record<string, string> = {
  pit_safe:      "text-green-400",
  leaked:        "text-destructive",
  corrected:     "text-yellow-400",
};

const PIT_LABELS: Record<string, string> = {
  pit_safe:   "GENUINE",
  leaked:     "CONTAMINATED",
  corrected:  "CORRECTED",
};

export default function Probability() {
  const {
    data: picks, loading: picksLoading, lastUpdated: picksUpdated, refetch: refetchPicks,
  } = useApi<any>("/stock-api/aiem-probability-engine/daily-picks", {}, 300_000);

  const {
    data: trackRecord, loading: trLoading, lastUpdated: trUpdated, refetch: refetchTr,
  } = useApi<any>("/stock-api/aiem-probability-engine/track-record", {}, 300_000);

  const refetch = () => { refetchPicks(); refetchTr(); };

  const rows: any[] = trackRecord?.rows ?? [];
  const summary: Record<string, any> = trackRecord?.summary ?? {};

  const genuineRows = rows.filter(r => r.pit_status === "pit_safe");
  const contamRows  = rows.filter(r => r.pit_status === "leaked");

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">
            Probability Engine
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Calibrated multi-horizon up-move probabilities · PIT-safe track record
          </p>
        </div>
        <button
          onClick={refetch}
          className="flex items-center gap-2 text-xs font-mono text-muted-foreground hover:text-white transition-colors"
        >
          <RefreshCw size={12} /> REFRESH
        </button>
      </div>

      <div className="flex-1 overflow-y-auto space-y-6 min-h-0">

        {/* PIT contamination warning banner if leaked rows exist */}
        {!trLoading && contamRows.length > 0 && (
          <div className="border border-destructive/50 bg-destructive/10 p-3 flex items-start gap-3 font-mono text-xs shrink-0">
            <AlertTriangle size={14} className="text-destructive shrink-0 mt-0.5" />
            <div>
              <span className="text-destructive font-bold">PIT CONTAMINATION NOTICE — </span>
              <span className="text-muted-foreground">
                {contamRows.length} rows marked <span className="text-destructive">LEAKED</span> (pit_status=leaked).
                These predictions used look-ahead data during training. The "GENUINE" bucket (pit_safe rows only) is
                the only valid track record. Contaminated rows are shown below for transparency only.
              </span>
            </div>
          </div>
        )}

        {/* Today's picks */}
        <div className="border border-border bg-card shrink-0">
          <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
            <BrainCircuit size={14} className="text-primary" />
            <span className="text-sm font-mono font-bold text-primary">TODAY'S PICKS</span>
            {picks?.pick_date && (
              <span className="text-xs font-mono text-muted-foreground ml-1">— {picks.pick_date}</span>
            )}
            <span className="ml-auto text-xs font-mono text-muted-foreground">
              /stock-api/aiem-probability-engine/daily-picks
            </span>
          </div>
          {picksLoading ? (
            <div className="p-6 text-center text-muted-foreground font-mono text-sm">LOADING…</div>
          ) : picks?.picks?.length === 0 || !picks?.picks ? (
            <div className="p-6 text-center font-mono text-sm space-y-1">
              <div className="text-muted-foreground">{picks?.note ?? "NO PICKS AVAILABLE"}</div>
              <div className="text-xs text-muted-foreground">
                Source: aiem_probability_engine_daily_picks
              </div>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full font-mono text-xs border-collapse">
                  <thead className="border-b border-border text-muted-foreground">
                    <tr>
                      <th className="p-3 text-left font-normal">#</th>
                      <th className="p-3 text-left font-normal">TICKER</th>
                      <th className="p-3 text-right font-normal">SCORE</th>
                      <th className="p-3 text-right font-normal">CONF</th>
                      <th className="p-3 text-right font-normal">P↑1D</th>
                      <th className="p-3 text-right font-normal">P↑2D</th>
                      <th className="p-3 text-right font-normal">P↑3D</th>
                      <th className="p-3 text-right font-normal">EDGE</th>
                      <th className="p-3 text-left font-normal">REGIME</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(picks.picks as any[]).map((p: any) => (
                      <tr key={p.rank} className="border-b border-border/30 hover:bg-white/5">
                        <td className="p-3 text-muted-foreground">{p.rank}</td>
                        <td className="p-3 text-white font-bold">{p.ticker}</td>
                        <td className="p-3 text-right text-white">{rawFmt(p.score, 1)}</td>
                        <td className={`p-3 text-right ${p.confidence >= 0.65 ? "text-green-400" : "text-muted-foreground"}`}>
                          {pct(p.confidence)}
                        </td>
                        <td className={`p-3 text-right ${p.prob_up_1d > 0.5 ? "text-green-400" : "text-destructive"}`}>
                          {pct(p.prob_up_1d)}
                        </td>
                        <td className={`p-3 text-right ${p.prob_up_2d > 0.5 ? "text-green-400" : "text-destructive"}`}>
                          {pct(p.prob_up_2d)}
                        </td>
                        <td className={`p-3 text-right ${p.prob_up_3d > 0.5 ? "text-green-400" : "text-destructive"}`}>
                          {pct(p.prob_up_3d)}
                        </td>
                        <td className={`p-3 text-right ${(p.edge_after_cost_prob_pts ?? 0) > 0 ? "text-green-400" : "text-destructive"}`}>
                          {rawFmt(p.edge_after_cost_prob_pts, 2)} pp
                        </td>
                        <td className="p-3 text-muted-foreground text-[10px]">{p.regime_tag ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {picks?.methodology && (
                <div className="p-3 border-t border-border text-[10px] font-mono text-muted-foreground">
                  ℹ {picks.methodology}
                </div>
              )}
            </>
          )}
        </div>

        {/* Track Record — per-horizon accuracy by PIT bucket */}
        <div className="border border-border bg-card shrink-0">
          <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
            <CheckCircle2 size={14} className="text-secondary" />
            <span className="text-sm font-mono font-bold text-white">TRACK RECORD — PIT-BUCKETED ACCURACY</span>
            <span className="ml-auto text-xs font-mono text-muted-foreground">
              /stock-api/aiem-probability-engine/track-record
            </span>
          </div>
          {trLoading ? (
            <div className="p-6 text-center text-muted-foreground font-mono text-sm">LOADING…</div>
          ) : !trackRecord || rows.length === 0 ? (
            <div className="p-6 text-center font-mono text-sm space-y-1">
              <div className="text-muted-foreground">
                {trackRecord?.note ?? "NO TRACK RECORD DATA YET"}
              </div>
              <div className="text-xs text-muted-foreground">
                Source: aiem_probability_engine_predictions
              </div>
            </div>
          ) : (
            <>
              {/* Summary grid per bucket */}
              {Object.entries(summary).map(([bucket, bData]: [string, any]) => (
                <div key={bucket} className="border-b border-border">
                  <div className={`px-4 py-2 flex items-center gap-2 text-xs font-mono font-bold ${PIT_COLORS[bucket] ?? "text-white"}`}>
                    {bucket === "pit_safe" ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
                    {PIT_LABELS[bucket] ?? bucket.toUpperCase()} — {bData.n_rows ?? "?"} rows
                    {bucket === "pit_safe" && (
                      <span className="text-green-400/70 font-normal ml-1">← only valid track record</span>
                    )}
                    {bucket === "leaked" && (
                      <span className="text-destructive/70 font-normal ml-1">← look-ahead contaminated, do not use as performance claim</span>
                    )}
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full font-mono text-xs border-collapse">
                      <thead className="border-b border-border/50 text-muted-foreground">
                        <tr>
                          <th className="p-3 text-left font-normal">HORIZON</th>
                          <th className="p-3 text-right font-normal">N GRADED</th>
                          <th className="p-3 text-right font-normal">ACCURACY</th>
                          <th className="p-3 text-right font-normal">BRIER</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[1, 2, 3, 4].map(h => {
                          const hd = bData[`h${h}`] ?? {};
                          const acc = hd.accuracy != null ? `${(hd.accuracy * 100).toFixed(1)}%` : "—";
                          const br  = hd.brier != null ? hd.brier.toFixed(4) : "—";
                          return (
                            <tr key={h} className="border-b border-border/30 hover:bg-white/5">
                              <td className="p-3 text-white">T+{h}D</td>
                              <td className="p-3 text-right text-muted-foreground">{hd.n_graded ?? "—"}</td>
                              <td className={`p-3 text-right ${hd.accuracy != null && hd.accuracy > 0.55 ? "text-green-400" : "text-muted-foreground"}`}>
                                {acc}
                              </td>
                              <td className={`p-3 text-right ${hd.brier != null && hd.brier < 0.25 ? "text-green-400" : "text-muted-foreground"}`}>
                                {br}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}

              {/* Row-level table — genuine rows first */}
              <div>
                <div className="px-4 py-2 text-xs font-mono text-muted-foreground border-b border-border">
                  ROW-LEVEL HISTORY (most recent {rows.length} rows · each carries pit_status)
                </div>
                <div className="overflow-x-auto max-h-72 overflow-y-auto">
                  <table className="w-full font-mono text-xs border-collapse">
                    <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground z-10">
                      <tr>
                        <th className="p-2 text-left font-normal">DATE</th>
                        <th className="p-2 text-left font-normal">TICKER</th>
                        <th className="p-2 text-left font-normal">PIT</th>
                        <th className="p-2 text-right font-normal">P↑1D</th>
                        <th className="p-2 text-right font-normal">P↑2D</th>
                        <th className="p-2 text-right font-normal">CONF</th>
                        <th className="p-2 text-right font-normal">✓1D</th>
                        <th className="p-2 text-right font-normal">✓2D</th>
                        <th className="p-2 text-right font-normal">RET1D</th>
                        <th className="p-2 text-right font-normal">RET2D</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...genuineRows, ...rows.filter(r => r.pit_status !== "pit_safe")]
                        .map((r: any, i: number) => (
                          <tr key={i} className="border-b border-border/30 hover:bg-white/5">
                            <td className="p-2 text-muted-foreground">{r.signal_date}</td>
                            <td className="p-2 text-white">{r.ticker}</td>
                            <td className={`p-2 font-bold ${PIT_COLORS[r.pit_status] ?? "text-white"}`}>
                              {PIT_LABELS[r.pit_status]?.slice(0, 3) ?? r.pit_status}
                            </td>
                            <td className={`p-2 text-right ${r.prob_up_1d > 0.5 ? "text-green-400" : "text-destructive"}`}>
                              {pct(r.prob_up_1d)}
                            </td>
                            <td className={`p-2 text-right ${r.prob_up_2d > 0.5 ? "text-green-400" : "text-destructive"}`}>
                              {pct(r.prob_up_2d)}
                            </td>
                            <td className="p-2 text-right text-muted-foreground">{pct(r.confidence)}</td>
                            <td className="p-2 text-right">
                              {r.correct_1d === null ? <span className="text-muted-foreground">—</span>
                                : r.correct_1d ? <CheckCircle2 size={12} className="text-green-400 ml-auto" />
                                : <XCircle size={12} className="text-destructive ml-auto" />}
                            </td>
                            <td className="p-2 text-right">
                              {r.correct_2d === null ? <span className="text-muted-foreground">—</span>
                                : r.correct_2d ? <CheckCircle2 size={12} className="text-green-400 ml-auto" />
                                : <XCircle size={12} className="text-destructive ml-auto" />}
                            </td>
                            <td className={`p-2 text-right ${r.outcome_ret_1d != null && r.outcome_ret_1d >= 0 ? "text-green-400" : "text-destructive"}`}>
                              {r.outcome_ret_1d != null ? `${(r.outcome_ret_1d * 100).toFixed(2)}%` : "—"}
                            </td>
                            <td className={`p-2 text-right ${r.outcome_ret_2d != null && r.outcome_ret_2d >= 0 ? "text-green-400" : "text-destructive"}`}>
                              {r.outcome_ret_2d != null ? `${(r.outcome_ret_2d * 100).toFixed(2)}%` : "—"}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>

      </div>

      <DataFooter
        source="/stock-api/aiem-probability-engine/{daily-picks,track-record}"
        lastUpdated={picksUpdated ?? trUpdated}
        operatingMode="PROBABILITY ENGINE · PIT-SAFE"
        samplePeriod="aiem_probability_engine_predictions"
      />
    </div>
  );
}
