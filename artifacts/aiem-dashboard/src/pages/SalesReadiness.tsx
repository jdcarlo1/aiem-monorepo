import { useMemo, useState } from "react";
import { Link } from "wouter";
import { useApi } from "@/hooks/use-api";
import { DataFooter } from "@/components/data-footer";
import {
  BadgeCheck, BarChart3, Briefcase, FileText, Lock, RefreshCw,
  Shield, AlertTriangle, CheckCircle2, XCircle, Rocket,
} from "lucide-react";

type Tab = "reliability" | "pnl" | "live" | "commercial";

function Pill({ ok, label }: { ok: boolean; label?: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono font-bold bg-green-900/40 text-green-400 border border-green-800">
      <CheckCircle2 size={11} /> {label || "GREEN"}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-mono font-bold bg-red-900/40 text-red-400 border border-red-800">
      <XCircle size={11} /> {label || "RED"}
    </span>
  );
}

function ScoreCard({ title, score, sub }: { title: string; score: number; sub?: string }) {
  const color = score >= 80 ? "text-green-400" : score >= 50 ? "text-amber-400" : "text-destructive";
  return (
    <div className="border border-border bg-black p-4">
      <div className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">{title}</div>
      <div className={`text-3xl font-mono font-bold mt-1 ${color}`}>{score}</div>
      {sub && <div className="text-xs font-mono text-muted-foreground mt-1">{sub}</div>}
    </div>
  );
}

export default function SalesReadiness() {
  const { data, loading, error, lastUpdated, refetch } = useApi<any>(
    "/stock-api/aiem-sales-readiness",
    {},
    60_000,
  );
  const [tab, setTab] = useState<Tab>("reliability");

  const pillars = data?.pillars || {};
  const checks = data?.reliability?.checks || [];
  const streak = data?.reliability?.prediction_streak || {};
  const pnl = data?.honest_pnl || {};
  const live = data?.live_path || {};
  const commercial = data?.commercial || {};

  const tabs = useMemo(
    () => [
      { id: "reliability" as Tab, label: "1. Reliability Proof", icon: BadgeCheck },
      { id: "pnl" as Tab, label: "2. Honest P&L", icon: BarChart3 },
      { id: "live" as Tab, label: "3. Live Path", icon: Rocket },
      { id: "commercial" as Tab, label: "4. Commercial Layer", icon: Briefcase },
    ],
    [],
  );

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">
            Sales Readiness
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            AIEM-only buyer proof · OE sold separately · research/paper positioning
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
          LOADING SALES READINESS…
        </div>
      ) : error || !data?.ok ? (
        <div className="flex-1 flex items-center justify-center font-mono text-destructive text-sm">
          {data?.error || error?.message || "FETCH FAILED — /stock-api/aiem-sales-readiness"}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-5 min-h-0">
          <div className="border border-primary/30 bg-primary/5 p-4">
            <div className="flex items-start gap-3">
              <Shield size={18} className="text-primary mt-0.5" />
              <div>
                <div className="text-sm font-mono text-white font-bold">
                  {data.buyer_summary?.positioning}
                </div>
                <div className="text-xs font-mono text-muted-foreground mt-1">
                  {data.buyer_summary?.must_publish_note}
                </div>
                <ul className="mt-2 text-xs font-mono text-muted-foreground space-y-0.5">
                  {(data.buyer_summary?.not_included || []).map((x: string) => (
                    <li key={x}>• {x}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <ScoreCard title="Overall" score={data.overall_score ?? 0} sub="buyer readiness" />
            <ScoreCard title="Reliability" score={pillars.reliability_proof ?? 0} />
            <ScoreCard title="Honest P&L" score={pillars.honest_pnl ?? 0} />
            <ScoreCard title="Live Path" score={pillars.live_path ?? 0} sub="optional / locked" />
            <ScoreCard title="Commercial" score={pillars.commercial_layer ?? 0} />
          </div>

          <div className="flex flex-wrap gap-2 border-b border-border pb-2">
            {tabs.map((t) => {
              const Icon = t.icon;
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-2 px-3 py-2 text-xs font-mono border transition-colors ${
                    active
                      ? "border-primary/50 bg-primary/15 text-primary"
                      : "border-border text-muted-foreground hover:text-white"
                  }`}
                >
                  <Icon size={13} /> {t.label}
                </button>
              );
            })}
          </div>

          {tab === "reliability" && (
            <div className="space-y-4">
              <div className="grid md:grid-cols-3 gap-3">
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">MORNING STREAK</div>
                  <div className="text-2xl font-mono text-white mt-1">
                    {streak.consecutive_green_from_latest ?? 0}
                    <span className="text-sm text-muted-foreground"> / {streak.target_consecutive_days ?? 5}</span>
                  </div>
                  <div className="mt-2"><Pill ok={!!streak.meets_sale_bar} label={streak.meets_sale_bar ? "SALE BAR MET" : "NEED 5 GREEN DAYS"} /></div>
                </div>
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">TODAY PREDICTIONS</div>
                  <div className="text-2xl font-mono text-white mt-1">{streak.today_count ?? 0}</div>
                  <div className="mt-2"><Pill ok={!!streak.today_green} /></div>
                </div>
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">CHECKS GREEN</div>
                  <div className="text-2xl font-mono text-white mt-1">
                    {data.reliability?.green_count ?? 0}/{data.reliability?.total_checks ?? 0}
                  </div>
                  <div className="mt-2"><Pill ok={!!data.reliability?.sale_ready_reliability} label="SALE-READY RELIABILITY" /></div>
                </div>
              </div>

              <div className="border border-border">
                <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground">CHECKLIST</div>
                <div className="divide-y divide-border">
                  {checks.map((c: any) => (
                    <div key={c.name} className="px-3 py-2.5 flex items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-mono text-white">{c.name}</div>
                        <div className="text-[11px] font-mono text-muted-foreground">{c.detail}</div>
                      </div>
                      <Pill ok={!!c.green} />
                    </div>
                  ))}
                </div>
              </div>

              {(streak.days_with_predictions || []).length > 0 && (
                <div className="border border-border">
                  <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground">
                    RECENT PREDICTION DAYS
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-2 p-3">
                    {streak.days_with_predictions.slice(0, 10).map((d: any) => (
                      <div key={d.date} className="border border-border p-2">
                        <div className="text-[11px] font-mono text-muted-foreground">{d.date}</div>
                        <div className="text-sm font-mono text-white">{d.count} picks</div>
                        <Pill ok={!!d.green} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === "pnl" && (
            <div className="space-y-4">
              <div className="grid md:grid-cols-4 gap-3">
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">OPEN MARKS</div>
                  <div className="mt-2"><Pill ok={!!pnl.marks_green} label={pnl.marks_green ? "NO NULL MARKS" : "NULL MARKS PRESENT"} /></div>
                  <div className="text-xs font-mono text-muted-foreground mt-2">
                    open={pnl.open_positions ?? "—"} null_price={pnl.open_null_marks ?? "—"} null_pnl={pnl.open_null_pnl ?? "—"}
                  </div>
                </div>
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">CLOSED REALIZED P&L</div>
                  <div className="text-2xl font-mono text-white mt-1">
                    ${Number(pnl.closed_realized_pnl || 0).toFixed(2)}
                  </div>
                  <div className="text-xs font-mono text-muted-foreground mt-1">{pnl.closed_trades ?? 0} closed</div>
                </div>
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">OPTION ENTRY MIDS</div>
                  <div className="text-2xl font-mono text-white mt-1">
                    {pnl.option_honesty?.with_option_entry_mid ?? 0}
                    <span className="text-sm text-muted-foreground"> / {pnl.option_honesty?.open_options ?? 0}</span>
                  </div>
                  <div className="text-xs font-mono text-muted-foreground mt-1">
                    ready {pnl.option_honesty?.real_option_mtm_ready_pct ?? "—"}%
                  </div>
                </div>
                <div className="border border-border bg-black p-4">
                  <div className="text-[11px] font-mono text-muted-foreground">BUYER TRUST</div>
                  <div className="mt-2"><Pill ok={!!pnl.buyer_trust_ready} label={pnl.buyer_trust_ready ? "TRUST READY" : "NOT YET"} /></div>
                </div>
              </div>
              <div className="border border-amber-700/50 bg-amber-950/20 p-4 flex gap-3">
                <AlertTriangle size={16} className="text-amber-400 shrink-0 mt-0.5" />
                <div className="text-xs font-mono text-amber-100/90 leading-relaxed">
                  {pnl.option_honesty?.note ||
                    "Option P&L is honest only when option_entry_mid exists; otherwise it is labeled synthetic."}
                  {" "}Full buyer report also available under Analytics → Performance.
                </div>
              </div>
              <Link href="/performance">
                <span className="inline-flex items-center gap-2 text-xs font-mono text-primary hover:underline cursor-pointer">
                  Open Performance Analytics →
                </span>
              </Link>
            </div>
          )}

          {tab === "live" && (
            <div className="space-y-4">
              <div className="border border-border bg-black p-5">
                <div className="flex items-center gap-2 mb-3">
                  <Lock size={16} className="text-primary" />
                  <div className="text-sm font-mono text-white font-bold">LIVE PATH STATUS</div>
                  <Pill ok={live.mode === "PAPER_ONLY"} label={live.mode || "UNKNOWN"} />
                </div>
                <div className="grid md:grid-cols-2 gap-3 text-xs font-mono">
                  <div className="border border-border p-3">
                    <div className="text-muted-foreground">Can place live orders</div>
                    <div className="text-white mt-1">{String(!!live.can_place_live_orders)}</div>
                  </div>
                  <div className="border border-border p-3">
                    <div className="text-muted-foreground">Active provider</div>
                    <div className="text-white mt-1">{live.broker_adapter?.active_provider || "paper"}</div>
                  </div>
                  <div className="border border-border p-3">
                    <div className="text-muted-foreground">LIVE_TRADING_ENABLED</div>
                    <div className="text-white mt-1">{String(!!live.live_trading_enabled_env)}</div>
                  </div>
                  <div className="border border-border p-3">
                    <div className="text-muted-foreground">Dual-lock armed</div>
                    <div className="text-white mt-1">{String(!!live.dual_lock_armed)}</div>
                  </div>
                </div>
                <div className="mt-4 text-xs font-mono text-muted-foreground leading-relaxed">
                  Broker adapter: <span className="text-white">{live.broker_adapter?.status}</span>
                  {" — "}{live.broker_adapter?.note}
                </div>
              </div>

              <div className="border border-border">
                <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground">
                  PROVIDERS (paper active · stubs ready to hook up later)
                </div>
                <div className="divide-y divide-border">
                  {Object.entries(live.broker_adapter?.providers || {}).map(([pid, st]: any) => (
                    <div key={pid} className="px-3 py-2.5 flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-mono text-white font-bold">{pid}</div>
                        <div className="text-[11px] font-mono text-muted-foreground mt-1">
                          {st?.note || st?.hookup_notes || st?.mode || "—"}
                        </div>
                      </div>
                      <span className="text-[10px] font-mono border border-border px-2 py-0.5 text-muted-foreground">
                        {st?.connected ? "CONNECTED" : pid === "paper" ? "ACTIVE SIM" : "STUB"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {(live.broker_adapter?.how_to_hookup_later || []).length > 0 && (
                <div className="border border-primary/30 bg-primary/5 p-4">
                  <div className="text-xs font-mono text-primary font-bold mb-2">HOOK UP LATER</div>
                  <ol className="text-xs font-mono text-muted-foreground space-y-1 list-decimal pl-4">
                    {(live.broker_adapter.how_to_hookup_later as string[]).map((step) => (
                      <li key={step}>{step.replace(/^\d+\.\s*/, "")}</li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="border border-border p-4 text-xs font-mono text-muted-foreground leading-relaxed">
                Adapter interface is ready (Tradier / Alpaca / IBKR stubs). No live broker is connected.
                When you are ready, implement the stub&apos;s <span className="text-white">place_order()</span>,
                arm the dual live locks + <span className="text-white">AIEM_ALLOW_LIVE_ORDERS=1</span>, then
                flip <span className="text-white">AIEM_BROKER_PROVIDER</span>.
              </div>
            </div>
          )}

          {tab === "commercial" && (
            <div className="space-y-4">
              <div className="border border-border">
                <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground flex items-center gap-2">
                  <Shield size={12} /> ROLES MODEL
                </div>
                <div className="divide-y divide-border">
                  {(commercial.roles || []).map((r: any) => (
                    <div key={r.role} className="px-3 py-3 flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-mono text-white font-bold">{r.role}</div>
                        <div className="text-[11px] font-mono text-muted-foreground mt-1">
                          {(r.permissions || []).join(" · ")}
                        </div>
                        {r.note && <div className="text-[11px] font-mono text-amber-400/90 mt-1">{r.note}</div>}
                      </div>
                      <span className="text-[10px] font-mono border border-border px-2 py-0.5 text-muted-foreground">
                        {r.enforced ? "ENFORCED" : "DOCUMENTED"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border border-border">
                <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground flex items-center gap-2">
                  <FileText size={12} /> DUE DILIGENCE / DOCS
                </div>
                <div className="divide-y divide-border">
                  {(commercial.docs || []).map((d: any) => (
                    <div key={d.path} className="px-3 py-2.5 flex items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-mono text-white">{d.name}</div>
                        <div className="text-[11px] font-mono text-muted-foreground">{d.path}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border border-border">
                <div className="px-3 py-2 border-b border-border text-xs font-mono text-muted-foreground">
                  AIEM API SURFACE (SKU-SCOPED)
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs font-mono">
                    <thead>
                      <tr className="text-muted-foreground border-b border-border">
                        <th className="text-left px-3 py-2">Method</th>
                        <th className="text-left px-3 py-2">Path</th>
                        <th className="text-left px-3 py-2">SKU</th>
                        <th className="text-left px-3 py-2">Auth</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(commercial.api_surface || []).map((a: any) => (
                        <tr key={a.path + a.method} className="border-b border-border/60">
                          <td className="px-3 py-2 text-primary">{a.method}</td>
                          <td className="px-3 py-2 text-white">{a.path}</td>
                          <td className="px-3 py-2">{a.sku}</td>
                          <td className="px-3 py-2 text-muted-foreground">{a.auth}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="border border-primary/30 bg-primary/5 p-4 text-xs font-mono text-muted-foreground leading-relaxed">
                <span className="text-primary font-bold">SKU note:</span>{" "}
                {commercial.sku_separation?.aiem_terminal}.{" "}
                {commercial.sku_separation?.oe_terminal}.{" "}
                Bundle: {commercial.sku_separation?.bundle}.
              </div>
            </div>
          )}
        </div>
      )}

      <DataFooter source="/stock-api/aiem-sales-readiness" lastUpdated={lastUpdated} />
    </div>
  );
}
