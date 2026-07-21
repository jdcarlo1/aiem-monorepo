import { useApi } from "@/hooks/use-api";
import { RefreshCw, BookOpen, BrainCircuit, AlertTriangle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function Learning() {
  const { data: summary, loading, lastUpdated: loopUpdated } = useApi<any>("/stock-api/admin/closed-loop-summary", {});

  const hasLoopData = summary && Object.keys(summary).length > 0;

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
            ) : hasLoopData ? (
              <div className="space-y-6">
                <div className="grid grid-cols-2 gap-4">
                  {[
                    { label: "GAP1 AUDIT TRACE", key: "gap1_audit_trace" },
                    { label: "GAP2 TRUST HISTORY", key: "gap2_trust_history" },
                    { label: "GAP3 THOMPSON", key: "gap3_thompson" },
                    { label: "GAP4 PPO TRAINING", key: "gap4_ppo_training" },
                    { label: "GAP5 CANDIDATE RANKS", key: "gap5_candidate_rankings" },
                  ].map(({ label, key }) => {
                    const val = summary?.[key];
                    const isObj = val && typeof val === "object";
                    return (
                      <div key={key} className="border border-border p-4 bg-black col-span-2">
                        <div className="text-xs text-muted-foreground mb-2">{label}</div>
                        {isObj ? (
                          <pre className="text-[10px] text-secondary break-all whitespace-pre-wrap">{JSON.stringify(val, null, 2)}</pre>
                        ) : (
                          <div className="text-sm text-white">{val ?? <span className="text-muted-foreground italic">NOT AVAILABLE</span>}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="text-center text-muted-foreground mt-10 space-y-2">
                <div>NO CLOSED-LOOP SUMMARY DATA</div>
                <div className="text-xs">Source: /stock-api/admin/closed-loop-summary</div>
              </div>
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
            <div className="flex-1 flex items-center justify-center p-6 font-mono text-sm">
              <div className="text-center space-y-3 text-muted-foreground">
                <AlertTriangle size={32} className="mx-auto text-accent" />
                <div className="font-bold text-accent">DATA UNAVAILABLE</div>
                <div className="text-xs max-w-xs">
                  XGBoost training epoch metrics (loss / accuracy per epoch) are not stored
                  in a queryable table. No authoritative backend source exists for this chart.
                </div>
                <div className="text-xs border border-border p-2 bg-black text-left">
                  <span className="text-muted-foreground">Source required:</span> ml_training_runs table or equivalent<br />
                  <span className="text-muted-foreground">Status:</span> NOT IMPLEMENTED
                </div>
              </div>
            </div>
          </div>

          <div className="border border-border bg-card flex flex-col shrink-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <BookOpen size={14} /> ADAPTIVE POLICIES
              </h2>
            </div>
            <div className="p-6 font-mono text-sm">
              <div className="text-center text-muted-foreground space-y-2">
                <AlertTriangle size={20} className="mx-auto text-accent" />
                <div className="text-xs text-accent font-bold">NOT AVAILABLE</div>
                <div className="text-xs">
                  Adaptive policy changes (stop distance, position sizing, pullback threshold)
                  are not yet surfaced by the backend API. No fabricated values are shown.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <DataFooter
        source="aiem_closed_loop_learning tables"
        lastUpdated={loopUpdated}
        operatingMode="ML LEARNING PIPELINE"
        samplePeriod="Cumulative since June 2026"
      />
    </div>
  );
}
