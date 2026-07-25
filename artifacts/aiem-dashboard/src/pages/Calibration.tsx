import { useApi } from "@/hooks/use-api";
import {
  Target, AlertTriangle, CheckCircle2, RefreshCw, BarChart2, ShieldAlert,
} from "lucide-react";
import {
  ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, CartesianGrid,
} from "recharts";
import { DataFooter } from "@/components/data-footer";

const PIT_COLORS: Record<string, string> = {
  contaminated: "text-destructive",
  corrected:    "text-yellow-400",
  genuine:      "text-green-400",
};
const PIT_LABELS: Record<string, string> = {
  contaminated: "CONTAMINATED",
  corrected:    "CORRECTED",
  genuine:      "GENUINE",
};

function fmt(v: number | null | undefined, d = 4) {
  return v == null ? "—" : v.toFixed(d);
}

function CalibrationCurveChart({ data }: { data: any[] }) {
  if (!data || data.length === 0) {
    return (
      <div className="h-40 flex items-center justify-center text-muted-foreground font-mono text-xs">
        NO SETTLED ROWS FOR THIS HORIZON YET
      </div>
    );
  }
  const pts = data.map((row: any) => ({
    x: row.predicted_avg != null ? +row.predicted_avg.toFixed(4) : null,
    y: row.actual_rate   != null ? +row.actual_rate.toFixed(4)   : null,
    n: row.n,
  })).filter(p => p.x != null && p.y != null);

  return (
    <ResponsiveContainer width="100%" height={180}>
      <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
        <XAxis
          type="number" dataKey="x" domain={[0, 1]}
          tick={{ fontSize: 9, fontFamily: "monospace" }}
          label={{ value: "predicted prob", position: "insideBottomRight", offset: -4, fontSize: 9 }}
        />
        <YAxis
          type="number" dataKey="y" domain={[0, 1]}
          tick={{ fontSize: 9, fontFamily: "monospace" }}
          label={{ value: "actual rate", angle: -90, position: "insideLeft", offset: 4, fontSize: 9 }}
        />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={({ payload }) => {
            if (!payload?.length) return null;
            const d = payload[0].payload;
            return (
              <div className="bg-black border border-border p-2 text-[10px] font-mono">
                <div>predicted: {d.x?.toFixed(3)}</div>
                <div>actual: {d.y?.toFixed(3)}</div>
                <div>n: {d.n}</div>
              </div>
            );
          }}
        />
        <ReferenceLine x={0} y={0} stroke="transparent" />
        {/* Perfect calibration diagonal */}
        <ReferenceLine
          segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
          stroke="hsl(var(--muted-foreground))"
          strokeDasharray="4 2"
          label={{ value: "perfect", position: "insideTopLeft", fontSize: 9, fill: "hsl(var(--muted-foreground))" }}
        />
        <Scatter data={pts} fill="hsl(var(--primary))" r={4} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

function HorizonMetrics({
  data, bucket,
}: { data: any; bucket: string }) {
  const horizons = ["1", "2", "3", "4"];
  const ph = data?.per_horizon ?? {};

  return (
    <div className="space-y-4">
      {horizons.map((h) => {
        const m = ph[h] ?? ph[parseInt(h)] ?? {};
        const hasData = m.n_settled && m.n_settled > 0;

        return (
          <div key={h} className="border border-border/50">
            <div className="p-2 border-b border-border/30 flex items-center justify-between bg-black/30">
              <span className="text-xs font-mono font-bold text-white">T+{h}D HORIZON</span>
              <span className="text-[10px] font-mono text-muted-foreground">
                {hasData ? `n_settled=${m.n_settled}` : "no settled rows"}
              </span>
            </div>
            {!hasData ? (
              <div className="p-3 text-[10px] font-mono text-muted-foreground">
                {m.note ?? "No rows with both a settled outcome and a non-null score for this horizon."}
              </div>
            ) : (
              <div className="p-3 space-y-3">
                {/* Metrics row */}
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { label: "BRIER", val: fmt(m.brier_score), sub: "lower = better" },
                    { label: "AUC",   val: fmt(m.auc, 3),        sub: "ROC AUC" },
                    { label: "WIN %", val: m.actual_win_rate != null ? `${(m.actual_win_rate * 100).toFixed(1)}%` : "—", sub: "base rate" },
                    { label: "N",     val: m.n_settled,           sub: "settled" },
                  ].map(({ label, val, sub }) => (
                    <div key={label} className="text-center border border-border/30 p-2">
                      <div className="text-[9px] font-mono text-muted-foreground">{label}</div>
                      <div className="text-sm font-mono font-bold text-white">{val}</div>
                      <div className="text-[9px] font-mono text-muted-foreground">{sub}</div>
                    </div>
                  ))}
                </div>

                {/* Calibration curve chart */}
                <div>
                  <div className="text-[9px] font-mono text-muted-foreground mb-1">
                    CALIBRATION CURVE — predicted probability vs. actual outcome rate per bin
                  </div>
                  <CalibrationCurveChart data={m.calibration_table ?? []} />
                </div>

                {/* Precision-at-threshold table */}
                {m.precision_at_threshold && m.precision_at_threshold.length > 0 && (
                  <div>
                    <div className="text-[9px] font-mono text-muted-foreground mb-1">
                      PRECISION AT CONFIDENCE THRESHOLD
                    </div>
                    <table className="w-full font-mono text-[10px] border-collapse">
                      <thead>
                        <tr className="border-b border-border/30 text-muted-foreground">
                          <th className="p-1 text-left font-normal">THRESHOLD</th>
                          <th className="p-1 text-right font-normal">N PREDICTIONS</th>
                          <th className="p-1 text-right font-normal">ACTUAL WIN %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(m.precision_at_threshold as any[]).map((row: any) => (
                          <tr key={row.threshold} className="border-b border-border/20">
                            <td className="p-1 text-white">≥{(row.threshold * 100).toFixed(0)}%</td>
                            <td className="p-1 text-right text-muted-foreground">{row.n_predictions}</td>
                            <td className={`p-1 text-right ${row.actual_win_rate != null && row.actual_win_rate > 0.5 ? "text-green-400" : "text-muted-foreground"}`}>
                              {row.actual_win_rate != null ? `${(row.actual_win_rate * 100).toFixed(1)}%` : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Calibration() {
  const { data, loading, lastUpdated, refetch } = useApi<any>(
    "/stock-api/aiem-probability-engine/calibration",
    {},
    300_000,
  );

  const pm  = data?.pit_metrics ?? {};
  const arts = data?.calibrator_artifacts ?? {};

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">
            Calibration
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            pit_metrics.run_pit_metrics() · Brier scores + calibration curves · PIT-bucketed
          </p>
        </div>
        <button
          onClick={refetch}
          className="flex items-center gap-2 text-xs font-mono text-muted-foreground hover:text-white transition-colors"
        >
          <RefreshCw size={12} /> REFRESH
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center font-mono text-muted-foreground text-sm">
          LOADING — calling pit_metrics.run_pit_metrics()…
        </div>
      ) : !data || data.error ? (
        <div className="flex-1 flex items-center justify-center font-mono text-destructive text-sm">
          {data?.error ?? "FETCH FAILED — /stock-api/aiem-probability-engine/calibration"}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-6 min-h-0">

          {/* PIT contamination warning */}
          <div className="border border-destructive/50 bg-destructive/10 p-3 flex items-start gap-3 font-mono text-xs shrink-0">
            <AlertTriangle size={14} className="text-destructive shrink-0 mt-0.5" />
            <div>
              <span className="text-destructive font-bold">PIT LEAKAGE NOTICE — </span>
              <span className="text-muted-foreground">
                CONTAMINATED bucket reflects a model that had already seen outcome-adjacent future
                data — its Brier/AUC are <span className="text-destructive">optimistic by construction</span>.
                GENUINE is the only bucket representing an honest forward track record.
                CORRECTED is embargo-retrained on a small, explicitly disclosed n.
              </span>
            </div>
          </div>

          {/* Calibrator artifacts — training-time Brier */}
          <div className="border border-border bg-card shrink-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2">
              <Target size={14} className="text-primary" />
              <span className="text-sm font-mono font-bold text-primary">
                CALIBRATOR ARTIFACTS — TRAINING TIME
              </span>
              <span className="ml-auto text-xs font-mono text-muted-foreground">
                from calibrated_horizon_Nd.pkl · calibration.calibrate_all_horizons()
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-xs border-collapse">
                <thead className="border-b border-border text-muted-foreground">
                  <tr>
                    <th className="p-3 text-left font-normal">HORIZON</th>
                    <th className="p-3 text-right font-normal">METHOD</th>
                    <th className="p-3 text-right font-normal">RAW BRIER (test)</th>
                    <th className="p-3 text-right font-normal">CAL BRIER (test)</th>
                    <th className="p-3 text-right font-normal">IMPROVEMENT</th>
                    <th className="p-3 text-right font-normal">N TRAIN</th>
                    <th className="p-3 text-right font-normal">N VAL</th>
                    <th className="p-3 text-right font-normal">N TEST</th>
                  </tr>
                </thead>
                <tbody>
                  {[1, 2, 3, 4].map((h) => {
                    const a = arts[`${h}d`] ?? {};
                    const note = a.note ?? a.error;
                    return (
                      <tr key={h} className="border-b border-border/30 hover:bg-white/5">
                        <td className="p-3 text-white">T+{h}D</td>
                        {note ? (
                          <td colSpan={7} className="p-3 text-muted-foreground italic">{note}</td>
                        ) : (
                          <>
                            <td className="p-3 text-right text-white">{a.method?.toUpperCase() ?? "—"}</td>
                            <td className="p-3 text-right text-muted-foreground">{fmt(a.raw_brier_test_fold)}</td>
                            <td className="p-3 text-right text-muted-foreground">{fmt(a.cal_brier_test_fold)}</td>
                            <td className={`p-3 text-right font-bold ${a.brier_improvement != null && a.brier_improvement > 0 ? "text-green-400" : "text-destructive"}`}>
                              {a.brier_improvement != null
                                ? `${a.brier_improvement > 0 ? "↓" : "↑"}${Math.abs(a.brier_improvement).toFixed(4)}`
                                : "—"}
                            </td>
                            <td className="p-3 text-right text-muted-foreground">{a.n_train ?? "—"}</td>
                            <td className="p-3 text-right text-muted-foreground">{a.n_val ?? "—"}</td>
                            <td className="p-3 text-right text-muted-foreground">{a.n_test ?? "—"}</td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="p-3 border-t border-border text-[10px] font-mono text-muted-foreground">
              ↓ improvement = calibration reduced Brier score on the held-out test fold.
              ↑ = calibration did not improve Brier on this fold (Platt scaling is not guaranteed
              to improve; isotonic requires ≥300 val-fold rows per config).
            </div>
          </div>

          {/* Per-bucket PIT panels */}
          {[
            {
              bucket: "contaminated",
              label:  "CONTAMINATED — leaked rows, original (inflated) scores",
              icon:   <AlertTriangle size={14} className="text-destructive" />,
              note:   "These rows were scored by a model that had already seen outcome-adjacent future data. Optimistic by construction — NOT a valid accuracy estimate.",
            },
            {
              bucket: "corrected",
              label:  "CORRECTED — embargo-retrained, same leaked rows",
              icon:   <ShieldAlert size={14} className="text-yellow-400" />,
              note:   "pit_correction.py's embargo-retrained scores for leaked rows where a correction exists. Small, explicitly disclosed n — uncorrectable rows excluded, never guessed.",
            },
            {
              bucket: "genuine",
              label:  "GENUINE — post-fix pit_safe rows (the ONLY valid track record)",
              icon:   <CheckCircle2 size={14} className="text-green-400" />,
              note:   "Rows logged after the 2026-07-02 PIT fix with pit_status=pit_safe. Never contaminated. n_settled grows daily as more horizons settle.",
            },
          ].map(({ bucket, label, icon, note }) => {
            const bdata = pm[bucket] ?? {};
            return (
              <div key={bucket} className="border border-border bg-card shrink-0">
                <div className="p-3 border-b border-border bg-sidebar/50 flex items-start gap-2">
                  {icon}
                  <div className="min-w-0">
                    <div className={`text-sm font-mono font-bold ${PIT_COLORS[bucket]}`}>{label}</div>
                    <div className="text-[10px] font-mono text-muted-foreground mt-0.5">{note}</div>
                  </div>
                  <div className="ml-auto text-xs font-mono text-muted-foreground whitespace-nowrap">
                    n_total={bdata.n_rows_total ?? "—"}
                  </div>
                </div>
                <div className="p-4">
                  <HorizonMetrics data={bdata} bucket={bucket} />
                </div>
              </div>
            );
          })}

          {/* Data attribution */}
          {data?.note && (
            <div className="border border-border/30 p-3 font-mono text-[10px] text-muted-foreground shrink-0">
              ℹ {data.note}
            </div>
          )}

        </div>
      )}

      <DataFooter
        source="/stock-api/aiem-probability-engine/calibration · pit_metrics.run_pit_metrics()"
        lastUpdated={lastUpdated}
        operatingMode="READ-ONLY · DB QUERY + PKL LOAD · NO TRAINING"
        samplePeriod="aiem_probability_engine_predictions (PIT-bucketed)"
      />
    </div>
  );
}
