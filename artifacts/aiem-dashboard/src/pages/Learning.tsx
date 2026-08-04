import { useApi } from "@/hooks/use-api";
import { RefreshCw, BookOpen, BrainCircuit, AlertTriangle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

export default function Learning() {
  const { data: summary, loading, lastUpdated: loopUpdated } = useApi<any>("/stock-api/admin/closed-loop-summary", {});
  const { data: mlRuns, loading: mlLoading, lastUpdated: mlUpdated } = useApi<any>("/stock-api/admin/ml-training-runs?limit=30", {});
  const { data: policies, loading: polLoading, lastUpdated: polUpdated } = useApi<any>("/stock-api/admin/adaptive-policies", {});

  const hasLoopData = summary && Object.keys(summary).length > 0;
  const runs = Array.isArray(mlRuns?.runs) ? mlRuns.runs : [];
  const trust = Array.isArray(policies?.signal_trust_weights) ? policies.signal_trust_weights : [];
  const thompson = Array.isArray(policies?.thompson) ? policies.thompson : [];
  const retrains = Array.isArray(policies?.retrain_history) ? policies.retrain_history : [];
  const lastUpdated = mlUpdated || polUpdated || loopUpdated;

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
            <div className="flex-1 overflow-auto p-4 font-mono text-sm">
              {mlLoading ? (
                <div className="text-muted-foreground">LOADING...</div>
              ) : runs.length === 0 ? (
                <div className="text-center text-muted-foreground mt-8 space-y-2">
                  <AlertTriangle size={24} className="mx-auto text-accent" />
                  <div className="text-xs">No ml_training_runs yet — runs appear after model_training.train_model()</div>
                  <div className="text-xs">Source: /stock-api/admin/ml-training-runs</div>
                </div>
              ) : (
                <div className="space-y-2">
                  {runs.map((r: any) => (
                    <div key={r.id} className="border border-border p-3 bg-black">
                      <div className="flex justify-between text-xs text-muted-foreground">
                        <span>{r.model_name}</span>
                        <span>{r.started_at || r.finished_at || ""}</span>
                      </div>
                      <div className="mt-1 text-white text-xs">
                        n={r.n_train ?? "—"}
                        {r.val_auc != null ? ` · auc=${Number(r.val_auc).toFixed(3)}` : ""}
                        {r.val_accuracy != null ? ` · acc=${Number(r.val_accuracy).toFixed(3)}` : ""}
                        {r.status ? ` · ${r.status}` : ""}
                      </div>
                      {r.note ? <div className="text-[10px] text-muted-foreground mt-1">{r.note}</div> : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
            <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
              <h2 className="text-sm font-mono font-bold text-white flex items-center gap-2">
                <BookOpen size={14} /> ADAPTIVE POLICIES
              </h2>
            </div>
            <div className="flex-1 overflow-auto p-4 font-mono text-sm">
              {polLoading ? (
                <div className="text-muted-foreground">LOADING...</div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <div className="text-xs text-muted-foreground mb-2">SIGNAL TRUST WEIGHTS</div>
                    {trust.length === 0 ? (
                      <div className="text-xs text-muted-foreground">No trust weights yet</div>
                    ) : (
                      trust.slice(0, 8).map((t: any) => (
                        <div key={t.signal_source} className="flex justify-between text-xs border-b border-border/40 py-1">
                          <span className="text-white">{t.signal_source}</span>
                          <span className="text-secondary">{t.trust_weight != null ? Number(t.trust_weight).toFixed(3) : "—"}</span>
                        </div>
                      ))
                    )}
                  </div>
                  <div>
                    <div className="text-xs text-muted-foreground mb-2">THOMPSON SAMPLER</div>
                    {thompson.length === 0 ? (
                      <div className="text-xs text-muted-foreground">No thompson rows yet</div>
                    ) : (
                      thompson.slice(0, 8).map((t: any) => (
                        <div key={t.signal_source} className="flex justify-between text-xs border-b border-border/40 py-1">
                          <span className="text-white">{t.signal_source}</span>
                          <span className="text-secondary">
                            W{t.wins}/L{t.losses}
                            {t.sampled_score != null ? ` · ${Number(t.sampled_score).toFixed(3)}` : ""}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                  {retrains.length > 0 ? (
                    <div>
                      <div className="text-xs text-muted-foreground mb-2">RETRAIN HISTORY</div>
                      {retrains.slice(0, 5).map((r: any, i: number) => (
                        <div key={i} className="text-[10px] text-muted-foreground py-0.5">
                          {r.model_name} · {r.status} · {r.created_at}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
      <DataFooter lastUpdated={lastUpdated} source="/stock-api/admin/closed-loop-summary + ml-training-runs + adaptive-policies" />
    </div>
  );
}
