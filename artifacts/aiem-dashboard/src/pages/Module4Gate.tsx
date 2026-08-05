import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { getToken } from "@/lib/auth";
import { ShieldCheck, Loader2, AlertTriangle } from "lucide-react";
import { DataFooter } from "@/components/data-footer";

type PendingSignal = {
  discovery_id: number;
  decay_verdict?: string;
  recommended_action?: string;
  realized_n?: number | null;
  realized_win_rate?: number | null;
  realized_p_value?: number | null;
  win_rate_at_discovery?: number | null;
  delta_vs_discovery_pp?: number | null;
  hypothesis_text?: string | null;
  status?: string;
  disc_win_rate?: number | null;
  disc_n?: number | null;
};

const ACTIONS = ["retire", "downgrade", "keep"] as const;
type GateAction = (typeof ACTIONS)[number];

function fmtWr(wr: number | null | undefined) {
  if (wr == null) return "N/A";
  const n = Number(wr);
  // Backend may return fraction (0.55) or already-percent
  return n <= 1.5 ? `${(n * 100).toFixed(1)}%` : `${n.toFixed(1)}%`;
}

export default function Module4Gate() {
  const { data, loading, lastUpdated, refetch, error } = useApi<any>(
    "/stock-api/admin/module4-pending",
    {},
    60000
  );
  const [actingId, setActingId] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const pending: PendingSignal[] = data?.pending ?? [];
  const pendingCount = data?.pending_count ?? pending.length;

  async function approve(discoveryId: number, action: GateAction) {
    if (actingId != null) return;
    setActingId(discoveryId);
    setMsg(null);
    try {
      const token = getToken();
      const res = await fetch("/stock-api/admin/module4-approve", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "X-Admin-Token": token } : {}),
        },
        credentials: "include",
        body: JSON.stringify({
          discovery_id: discoveryId,
          action,
          reason: `dashboard Module4Gate: ${action}`,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg(`FAILED id=${discoveryId} ${action}: ${body.error || body.detail || res.status}`);
      } else {
        setMsg(
          `OK id=${discoveryId} ${action.toUpperCase()}` +
            (body.from_status && body.to_status ? ` (${body.from_status} → ${body.to_status})` : "")
        );
        await refetch();
      }
    } catch (e: any) {
      setMsg(`ERROR: ${e?.message || String(e)}`);
    } finally {
      setActingId(null);
    }
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Signal Gate</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Module 4 — human approval for failing / decaying signals
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground mb-1">PENDING COUNT</div>
          <div
            className={`text-3xl font-mono font-bold ${
              pendingCount > 0 ? "text-accent" : "text-success"
            }`}
          >
            {loading && data == null ? "…" : pendingCount}
          </div>
        </div>
      </div>

      {msg && (
        <div className="px-3 py-2 border border-border bg-black font-mono text-xs text-primary shrink-0">
          {msg}
        </div>
      )}

      {error && (
        <div className="px-3 py-2 border border-destructive/40 bg-destructive/10 font-mono text-xs text-destructive flex items-center gap-2 shrink-0">
          <AlertTriangle size={12} />
          {error.message}
        </div>
      )}

      <div className="border border-border bg-card flex flex-col flex-1 min-h-0">
        <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
          <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
            <ShieldCheck size={14} /> PENDING SIGNALS
          </h2>
          <span className="text-xs font-mono text-muted-foreground">poll 60s · module4-pending</span>
        </div>
        <div className="flex-1 overflow-auto p-0">
          <table className="w-full text-left font-mono text-sm border-collapse">
            <thead className="sticky top-0 bg-card border-b border-border text-muted-foreground text-xs z-10">
              <tr>
                <th className="p-3 font-normal">ID</th>
                <th className="p-3 font-normal">VERDICT</th>
                <th className="p-3 font-normal">RECOMMENDED</th>
                <th className="p-3 font-normal">WR (REALIZED)</th>
                <th className="p-3 font-normal">N</th>
                <th className="p-3 font-normal">HYPOTHESIS</th>
                <th className="p-3 font-normal">ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {loading && data == null ? (
                <tr>
                  <td colSpan={7} className="p-4 text-center text-muted-foreground">
                    LOADING...
                  </td>
                </tr>
              ) : pending.length ? (
                pending.map((row) => {
                  const verdict = row.decay_verdict || "—";
                  const verdictColor =
                    verdict === "failing"
                      ? "text-destructive"
                      : verdict === "decaying"
                        ? "text-accent"
                        : "text-muted-foreground";
                  const busy = actingId === row.discovery_id;
                  return (
                    <tr key={row.discovery_id} className="border-b border-border/50 hover:bg-white/5">
                      <td className="p-3 font-bold text-white">{row.discovery_id}</td>
                      <td className={`p-3 font-bold uppercase ${verdictColor}`}>{verdict}</td>
                      <td className="p-3 text-secondary uppercase">
                        {row.recommended_action || "—"}
                      </td>
                      <td className="p-3 text-white">{fmtWr(row.realized_win_rate)}</td>
                      <td className="p-3 text-muted-foreground">{row.realized_n ?? "—"}</td>
                      <td
                        className="p-3 text-xs text-muted-foreground max-w-[220px] truncate"
                        title={row.hypothesis_text || undefined}
                      >
                        {row.hypothesis_text
                          ? row.hypothesis_text.slice(0, 80) +
                            (row.hypothesis_text.length > 80 ? "…" : "")
                          : "—"}
                      </td>
                      <td className="p-3">
                        <div className="flex flex-wrap gap-1.5">
                          {ACTIONS.map((action) => (
                            <button
                              key={action}
                              type="button"
                              disabled={busy || actingId != null}
                              onClick={() => approve(row.discovery_id, action)}
                              className={`px-2 py-1 text-[10px] font-mono font-bold uppercase border transition-colors disabled:opacity-40 ${
                                action === "retire"
                                  ? "border-destructive/50 text-destructive hover:bg-destructive/10"
                                  : action === "downgrade"
                                    ? "border-accent/50 text-accent hover:bg-accent/10"
                                    : "border-border text-muted-foreground hover:bg-white/5"
                              }`}
                            >
                              {busy ? <Loader2 size={10} className="animate-spin inline" /> : null}{" "}
                              {action}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={7} className="p-6 text-center text-muted-foreground">
                    NO PENDING MODULE 4 ACTIONS
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <DataFooter
        source="aiem_module2_evaluations, aiem_signal_discoveries, aiem_signal_actions"
        lastUpdated={lastUpdated}
        pollIntervalSec={60}
        operatingMode="HUMAN GATE — APPROVAL REQUIRED"
      />
    </div>
  );
}
