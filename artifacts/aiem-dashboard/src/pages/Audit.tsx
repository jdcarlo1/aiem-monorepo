import { useState, useCallback } from "react";
import { useApi } from "@/hooks/use-api";
import { getToken } from "@/lib/auth";
import { useToast } from "@/hooks/use-toast";
import {
  ShieldCheck, FileText, PlayCircle, Archive,
  CheckCircle2, XCircle, Clock, AlertTriangle, ChevronRight
} from "lucide-react";
import { DataFooter } from "@/components/data-footer";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ShaCheck {
  file: string;
  live?: string;
  canonical?: string;
  match: boolean;
  error?: string;
}

interface ChainInfo {
  path: string;
  line_count: number | null;
  last_modified: string;
  last_seq?: number | null;
  last_timestamp?: string | null;
  last_cmd?: string;
  last_exit_code?: number | null;
  error?: string;
}

interface ChainStatus {
  last_seq: number | null;
  last_pass_count?: number;
  last_fail_count?: number;
  last_log_tail?: string;
  last_log_error?: string;
  seq_error?: string;
  root_chain: ChainInfo;
  ape_chain: ChainInfo;
  sha_checks: ShaCheck[];
}

interface DocEntry {
  name: string;
  size_bytes: number;
  last_modified: string;
  type: string;
}

interface RunEntry {
  seq: string;
  timestamp?: string;
  exit_code?: string;
  entry_hash?: string;
  cmd?: string;
  raw?: string;
}

interface ScriptResult {
  name: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  run_at: string;
  cached: boolean;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtBytes(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso: string) {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function StatusBadge({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === null)
    return <span className="px-2 py-0.5 text-[10px] font-mono font-bold bg-amber-900/40 text-amber-400 border border-amber-700">{label}</span>;
  return ok
    ? <span className="px-2 py-0.5 text-[10px] font-mono font-bold bg-green-900/40 text-green-400 border border-green-800">✓ {label}</span>
    : <span className="px-2 py-0.5 text-[10px] font-mono font-bold bg-red-900/40 text-red-400 border border-red-800">✗ {label}</span>;
}

// ── Panels ─────────────────────────────────────────────────────────────────────

function PanelHeader({ icon: Icon, label, color = "text-primary" }: {
  icon: React.ElementType; label: string; color?: string;
}) {
  return (
    <div className="p-3 border-b border-border bg-sidebar/50 flex items-center gap-2 shrink-0">
      <Icon size={13} className={color} />
      <h2 className={`text-sm font-mono font-bold ${color}`}>{label}</h2>
    </div>
  );
}

// Panel 1 — Chain Health
function ChainHealthPanel() {
  const { data, loading, lastUpdated, refetch } =
    useApi<ChainStatus>("/stock-api/admin/audit/chain-status", {}, 120000);

  return (
    <div className="border border-border bg-card flex flex-col">
      <PanelHeader icon={ShieldCheck} label="CHAIN HEALTH" />
      <div className="p-4 flex-1 overflow-auto">
        {loading && <div className="font-mono text-muted-foreground text-sm">READING CHAIN...</div>}
        {!loading && !data && <div className="font-mono text-destructive text-sm">CHAIN STATUS UNAVAILABLE</div>}
        {data && (
          <div className="space-y-4">
            {/* Sequence + last run summary */}
            <div className="grid grid-cols-4 gap-3">
              <div className="border border-border bg-black p-3">
                <div className="text-[10px] font-mono text-muted-foreground mb-1">LAST SEQ</div>
                <div className="text-xl font-mono font-bold text-white">
                  {data.last_seq ?? <span className="text-amber-400">—</span>}
                </div>
              </div>
              <div className="border border-border bg-black p-3">
                <div className="text-[10px] font-mono text-muted-foreground mb-1">LAST PASS</div>
                <div className="text-xl font-mono font-bold text-green-400">
                  {data.last_pass_count ?? <span className="text-muted-foreground">—</span>}
                </div>
              </div>
              <div className="border border-border bg-black p-3">
                <div className="text-[10px] font-mono text-muted-foreground mb-1">LAST FAIL</div>
                <div className={`text-xl font-mono font-bold ${(data.last_fail_count ?? 0) > 0 ? "text-red-400" : "text-green-400"}`}>
                  {data.last_fail_count ?? <span className="text-muted-foreground">—</span>}
                </div>
              </div>
              <div className="border border-border bg-black p-3">
                <div className="text-[10px] font-mono text-muted-foreground mb-1">SHA CHECKS</div>
                <div className="text-xl font-mono font-bold text-white">
                  {data.sha_checks?.filter(c => c.match).length ?? 0}/{data.sha_checks?.length ?? 0}
                </div>
              </div>
            </div>

            {/* Evidence chain instances */}
            <div className="grid grid-cols-2 gap-3">
              {([
                { key: "root_chain", label: "ROOT CHAIN" },
                { key: "ape_chain", label: "APE CHAIN" },
              ] as const).map(({ key, label }) => {
                const ch = data[key] as ChainInfo;
                return (
                  <div key={key} className="border border-border bg-black p-3 space-y-1">
                    <div className="text-[10px] font-mono text-muted-foreground">{label}</div>
                    {ch.error
                      ? <div className="text-xs font-mono text-red-400">{ch.error}</div>
                      : <>
                          <div className="text-xs font-mono text-muted-foreground truncate">{ch.path}</div>
                          <div className="text-sm font-mono text-white">
                            {ch.line_count != null ? `${ch.line_count} entries` : "—"}
                          </div>
                          <div className="text-[10px] font-mono text-muted-foreground">
                            modified {fmtDate(ch.last_modified)}
                          </div>
                          {ch.last_seq != null && (
                            <div className="text-[10px] font-mono text-secondary">last SEQ {ch.last_seq}</div>
                          )}
                        </>
                    }
                  </div>
                );
              })}
            </div>

            {/* SHA cross-checks */}
            <div>
              <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">SHA-256 Cross-Checks</div>
              <div className="space-y-2">
                {data.sha_checks?.map((c) => (
                  <div key={c.file} className="border border-border bg-black p-3">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <div className="text-[10px] font-mono text-muted-foreground truncate flex-1">{c.file}</div>
                      <StatusBadge ok={c.error ? false : c.match} label={c.error ? "ERROR" : c.match ? "MATCH" : "MISMATCH"} />
                    </div>
                    {c.error
                      ? <div className="text-[10px] font-mono text-red-400">{c.error}</div>
                      : <>
                          <div className="text-[10px] font-mono text-muted-foreground break-all">
                            live:      {c.live}
                          </div>
                          <div className="text-[10px] font-mono text-muted-foreground break-all">
                            canonical: {c.canonical}
                          </div>
                        </>
                    }
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="px-4 py-2 border-t border-border flex justify-between items-center shrink-0">
        <span className="text-[10px] font-mono text-muted-foreground">
          {lastUpdated ? `fetched ${fmtDate(lastUpdated.toISOString())}` : "pending"}
        </span>
        <button onClick={refetch} className="text-[10px] font-mono text-primary hover:underline">REFRESH</button>
      </div>
    </div>
  );
}

// Panel 2 — Live Script Results
const SCRIPTS = [
  { name: "independent_recomputation", label: "INDEPENDENT RECOMPUTATION", desc: "EVID-013 / NEG-038/039/040" },
  { name: "load_security_e2e",         label: "LOAD / SECURITY E2E",        desc: "Standalone — not Phase 11" },
  { name: "staging_neg_controls",      label: "STAGING NEG CONTROLS",       desc: "NEG-002/005/007/009" },
] as const;

function LiveScriptsPanel() {
  const [results, setResults] = useState<Record<string, ScriptResult & { pending?: boolean }>>({});
  const { toast } = useToast();

  const runScript = useCallback(async (name: string) => {
    setResults(prev => ({ ...prev, [name]: { ...prev[name], pending: true } as any }));
    try {
      const tok = getToken();
      const res = await fetch("/stock-api/admin/audit/run-script", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(tok ? { "X-Admin-Token": tok } : {}),
        },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setResults(prev => ({ ...prev, [name]: { ...data, pending: false } }));
    } catch (e: any) {
      toast({ title: "Script error", description: e.message, variant: "destructive" });
      setResults(prev => ({ ...prev, [name]: { ...prev[name], pending: false } as any }));
    }
  }, [toast]);

  return (
    <div className="border border-border bg-card flex flex-col">
      <PanelHeader icon={PlayCircle} label="LIVE SCRIPT RUNNER" color="text-secondary" />
      <div className="p-4 flex-1 overflow-auto space-y-4">
        {SCRIPTS.map(({ name, label, desc }) => {
          const r = results[name];
          const isPending = r?.pending;
          const hasResult = r && !isPending && "exit_code" in r;

          return (
            <div key={name} className="border border-border bg-black">
              <div className="p-3 border-b border-border flex items-center justify-between gap-2">
                <div>
                  <div className="text-xs font-mono font-bold text-white">{label}</div>
                  <div className="text-[10px] font-mono text-muted-foreground">{desc}</div>
                </div>
                <button
                  onClick={() => runScript(name)}
                  disabled={isPending}
                  className="shrink-0 px-3 py-1.5 text-[10px] font-mono font-bold border border-primary text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-wait"
                >
                  {isPending ? "RUNNING..." : "RE-RUN"}
                </button>
              </div>
              <div className="p-3">
                {!hasResult && !isPending && (
                  <div className="flex items-center gap-2 text-amber-400">
                    <AlertTriangle size={12} />
                    <span className="text-[10px] font-mono">Never run — result unknown</span>
                  </div>
                )}
                {isPending && (
                  <div className="text-[10px] font-mono text-muted-foreground animate-pulse">Executing script...</div>
                )}
                {hasResult && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      {r.exit_code === 0
                        ? <CheckCircle2 size={13} className="text-green-400 shrink-0" />
                        : <XCircle size={13} className="text-red-400 shrink-0" />
                      }
                      <span className={`text-[10px] font-mono font-bold ${r.exit_code === 0 ? "text-green-400" : "text-red-400"}`}>
                        exit {r.exit_code}
                      </span>
                      <span className="text-[10px] font-mono text-muted-foreground">
                        {r.cached ? "cached" : "live"} · {fmtDate(r.run_at)}
                      </span>
                    </div>
                    {r.stdout && (
                      <pre className="text-[10px] font-mono text-muted-foreground bg-sidebar p-2 overflow-auto max-h-40 border border-border whitespace-pre-wrap break-all">
                        {r.stdout.trim()}
                      </pre>
                    )}
                    {r.stderr && (
                      <pre className="text-[10px] font-mono text-red-400 bg-red-950/20 p-2 overflow-auto max-h-24 border border-red-900 whitespace-pre-wrap break-all">
                        {r.stderr.trim()}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Panel 3 — Sealed Document Index
function DocModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, loading } = useApi<{ name: string; content: string; size_bytes: number }>(
    `/stock-api/admin/audit/doc-content?name=${encodeURIComponent(name)}`
  );
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
      <div className="bg-card border border-border w-full max-w-4xl max-h-[85vh] flex flex-col">
        <div className="p-3 border-b border-border flex items-center justify-between shrink-0">
          <span className="text-sm font-mono text-white">{name}</span>
          <button onClick={onClose} className="text-muted-foreground hover:text-white font-mono text-xs">CLOSE ✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {loading && <div className="font-mono text-muted-foreground text-sm">LOADING...</div>}
          {!loading && data && (
            <pre className="font-mono text-xs text-muted-foreground whitespace-pre-wrap break-all">
              {data.content}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function SealedDocsPanel() {
  const { data, loading } = useApi<{ docs: DocEntry[]; count: number }>(
    "/stock-api/admin/audit/docs", {}, 300000
  );
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  return (
    <div className="border border-border bg-card flex flex-col">
      <PanelHeader icon={FileText} label="SEALED DOCUMENTS" color="text-accent" />
      <div className="p-3 flex-1 overflow-auto">
        {loading && <div className="font-mono text-muted-foreground text-sm">LISTING...</div>}
        {!loading && !data && <div className="font-mono text-destructive text-sm">UNAVAILABLE</div>}
        {data && (
          <>
            <div className="text-[10px] font-mono text-muted-foreground mb-2">{data.count} files · live ls at page-load</div>
            <div className="space-y-0.5">
              {data.docs.map((doc) => (
                <button
                  key={doc.name}
                  onClick={() => setOpenDoc(doc.name)}
                  className="w-full text-left flex items-center gap-2 px-2 py-1.5 hover:bg-sidebar transition-colors group"
                >
                  <FileText size={10} className="text-muted-foreground shrink-0" />
                  <span className="text-[10px] font-mono text-muted-foreground group-hover:text-white flex-1 truncate">{doc.name}</span>
                  <span className="text-[10px] font-mono text-muted-foreground/60 shrink-0">{fmtBytes(doc.size_bytes)}</span>
                  <ChevronRight size={10} className="text-muted-foreground/40 shrink-0" />
                </button>
              ))}
            </div>
          </>
        )}
      </div>
      {openDoc && <DocModal name={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}

// Panel 4 — Run Log Archive
function RunLogPanel() {
  const { data, loading } = useApi<{ runs: RunEntry[]; count: number }>(
    "/stock-api/admin/audit/run-log", {}, 300000
  );
  const [openSeq, setOpenSeq] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<{ seq: string; content: string } | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const { toast } = useToast();

  const fetchLog = useCallback(async (seq: string) => {
    setOpenSeq(seq);
    setLogContent(null);
    setLogLoading(true);
    try {
      const tok = getToken();
      const res = await fetch(`/stock-api/admin/audit/run-log-detail?seq=${seq}`, {
        headers: tok ? { "X-Admin-Token": tok } : {},
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      setLogContent({ seq, content: d.content });
    } catch (e: any) {
      toast({ title: "Log fetch error", description: e.message, variant: "destructive" });
      setOpenSeq(null);
    } finally {
      setLogLoading(false);
    }
  }, [toast]);

  return (
    <div className="border border-border bg-card flex flex-col">
      <PanelHeader icon={Archive} label="RUN LOG ARCHIVE" color="text-muted-foreground" />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* List */}
        <div className="w-80 shrink-0 border-r border-border overflow-auto">
          {loading && <div className="p-4 font-mono text-muted-foreground text-sm">LOADING INDEX...</div>}
          {!loading && !data && <div className="p-4 font-mono text-destructive text-sm">INDEX UNAVAILABLE</div>}
          {data && (
            <>
              <div className="px-3 py-2 border-b border-border text-[10px] font-mono text-muted-foreground">
                {data.count} runs · verified_run_index.tsv
              </div>
              <div className="divide-y divide-border">
                {[...data.runs].reverse().map((run) => (
                  <button
                    key={run.seq ?? run.raw}
                    onClick={() => run.seq && fetchLog(run.seq)}
                    className={`w-full text-left px-3 py-2 hover:bg-sidebar transition-colors ${openSeq === run.seq ? "bg-primary/10 border-l-2 border-primary" : ""}`}
                  >
                    {run.seq ? (
                      <>
                        <div className="text-[10px] font-mono text-white">SEQ {run.seq}</div>
                        <div className="text-[9px] font-mono text-muted-foreground truncate">{run.cmd ?? ""}</div>
                        <div className="text-[9px] font-mono text-muted-foreground/60">{run.timestamp ?? ""}</div>
                      </>
                    ) : (
                      <div className="text-[10px] font-mono text-muted-foreground truncate">{run.raw}</div>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        {/* Detail */}
        <div className="flex-1 overflow-auto p-4">
          {!openSeq && <div className="text-[11px] font-mono text-muted-foreground">Select a run to view log</div>}
          {openSeq && logLoading && <div className="text-[11px] font-mono text-muted-foreground animate-pulse">LOADING SEQ {openSeq}...</div>}
          {openSeq && !logLoading && logContent && (
            <>
              <div className="text-[10px] font-mono text-muted-foreground mb-2">
                verified_run_{logContent.seq}.log · {fmtBytes(logContent.content.length)}
              </div>
              <pre className="text-[10px] font-mono text-muted-foreground whitespace-pre-wrap break-all">
                {logContent.content}
              </pre>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function Audit() {
  const { lastUpdated } = useApi<ChainStatus>("/stock-api/admin/audit/chain-status", {}, 120000);

  return (
    <div className="space-y-4 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Audit / Compliance</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Live verification chain · sealed documents · re-runnable scripts
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Clock size={12} className="text-muted-foreground" />
          <span className="text-[10px] font-mono text-muted-foreground">
            All values fetched live at page-load — no hardcoded status
          </span>
        </div>
      </div>

      {/* Panel 1 — Chain Health (full width) */}
      <div className="shrink-0">
        <ChainHealthPanel />
      </div>

      {/* Panels 2 + 3 — Scripts (60%) + Docs (40%) */}
      <div className="grid grid-cols-5 gap-4 min-h-0" style={{ height: "420px" }}>
        <div className="col-span-3 min-h-0 overflow-hidden flex flex-col">
          <LiveScriptsPanel />
        </div>
        <div className="col-span-2 min-h-0 overflow-hidden flex flex-col">
          <SealedDocsPanel />
        </div>
      </div>

      {/* Panel 4 — Run Log Archive (full width) */}
      <div className="flex-1 min-h-0" style={{ minHeight: "320px" }}>
        <RunLogPanel />
      </div>

      <DataFooter
        source="evidence_chain.log · docs/verification/ · tools/verified_run_*.log"
        lastUpdated={lastUpdated}
        pollIntervalSec={120}
        operatingMode="AUDIT READ-ONLY"
      />
    </div>
  );
}
