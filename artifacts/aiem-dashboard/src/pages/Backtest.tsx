import { useCallback, useEffect, useRef, useState } from "react";
import {
  BarChart3, Loader2, Send, Sparkles, AlertTriangle, History, FlaskConical,
} from "lucide-react";
import { getToken, getCsrfToken } from "@/lib/auth";
import { DataFooter } from "@/components/data-footer";

type SessionStatus = "pending" | "running" | "done" | "error";

interface ToolTraceStep {
  iteration: number;
  tool: string;
  ok?: boolean;
}

interface BacktestSession {
  job_id: string;
  question: string;
  status: SessionStatus;
  answer?: string | null;
  error?: string | null;
  current_tool?: string | null;
  tool_trace?: ToolTraceStep[] | null;
  created_at?: string;
  streaming_text?: string;
}

const TEMPLATES = [
  {
    id: "rvol-gap",
    label: "RVOL + gap-up",
    prompt:
      "Backtest a 2.5× relative volume + gap-up filter over the last 90 days. Report win rate, sample size, average return, and edge.",
  },
  {
    id: "weak-close",
    label: "Weak-close rebound",
    prompt:
      "Backtest the weak-close + high-volume rebound signal over the last 60 days. Include win rate, avg return, and drawdown notes.",
  },
  {
    id: "unusual-calls",
    label: "Unusual calls follow-through",
    prompt:
      "Backtest unusual-calls / options-flow follow-through over the last 30 trading days. Break out by premium tier if possible.",
  },
  {
    id: "loop-b",
    label: "Loop B morning setups",
    prompt:
      "Backtest AIEM Loop B morning scan setups over the last 60 days versus next-day and 3-day returns. Report hit rate and average edge.",
  },
  {
    id: "inside-day",
    label: "Inside-day breakout",
    prompt:
      "Backtest inside-day compression after a large move — breakout continuation over the last 90 days.",
  },
];

const TOOL_LABELS: Record<string, string> = {
  backtest_signal: "Running backtest",
  mkt_retrospective_backtest: "Running retrospective backtest",
  mkt_test_signal: "Testing signal hypothesis",
  test_new_signal: "Testing new signal",
  run_fisher_test: "Running statistical test",
  run_bootstrap_test: "Running bootstrap analysis",
  run_correlation_analysis: "Computing correlations",
  run_multivariate_regression: "Running regression",
  query_pick_outcomes: "Fetching outcome data",
  analyze_pick_outcomes: "Analyzing pick outcomes",
  query_signal_discoveries: "Scanning signal discoveries",
  query_unusual_calls: "Checking unusual options flow",
  query_conviction_stack: "Reading conviction scores",
  query_polygon_rvol: "Fetching relative volume",
  query_market_regime: "Checking market regime",
  scan_market: "Scanning market universe",
};

function formatToolLabel(raw: string | null | undefined): string {
  if (!raw || raw === "_timing") return "Researching";
  return TOOL_LABELS[raw] ?? raw.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function authHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["X-Admin-Token"] = token;
  const csrf = getCsrfToken();
  if (csrf) headers["X-CSRF-Token"] = csrf;
  return headers;
}

async function loadServerHistory(): Promise<BacktestSession[]> {
  const res = await fetch("/stock-api/aiem/chat/history", {
    headers: authHeaders(false),
    credentials: "include",
  });
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export default function Backtest() {
  const [history, setHistory] = useState<BacktestSession[]>([]);
  const [input, setInput] = useState("");
  const [active, setActive] = useState<BacktestSession | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [showTrace, setShowTrace] = useState(false);

  const mountedRef = useRef(true);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const clearElapsed = () => {
    if (elapsedRef.current) clearInterval(elapsedRef.current);
    elapsedRef.current = null;
  };

  const startElapsed = () => {
    clearElapsed();
    startRef.current = Date.now();
    setElapsed(0);
    elapsedRef.current = setInterval(() => {
      if (!mountedRef.current) return;
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
  };

  const refreshHistory = useCallback(async () => {
    try {
      const rows = await loadServerHistory();
      if (mountedRef.current) {
        setHistory(rows);
        setLastUpdated(new Date());
      }
    } catch {
      /* non-fatal */
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refreshHistory();
    return () => {
      mountedRef.current = false;
      clearElapsed();
    };
  }, [refreshHistory]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active, history.length, active?.streaming_text]);

  async function pollUntilDone(jobId: string, question: string) {
    const deadline = Date.now() + 6 * 60 * 1000;
    while (Date.now() < deadline && mountedRef.current) {
      await new Promise((r) => setTimeout(r, 1200));
      try {
        const res = await fetch(`/stock-api/aiem/chat/${jobId}`, {
          headers: authHeaders(false),
          credentials: "include",
        });
        if (!res.ok) continue;
        const data = await res.json();
        if (!mountedRef.current) return;
        setActive({
          job_id: jobId,
          question,
          status: data.status,
          answer: data.answer,
          error: data.error,
          current_tool: data.current_tool,
          tool_trace: data.tool_trace,
          created_at: data.created_at,
        });
        if (data.status === "done" || data.status === "error") {
          clearElapsed();
          refreshHistory();
          return;
        }
      } catch {
        /* keep polling */
      }
    }
    if (mountedRef.current) {
      setActive((prev) =>
        prev
          ? {
              ...prev,
              status: "error",
              error: "Timed out waiting for result. Check History in a moment — the job may still finish.",
            }
          : prev,
      );
      clearElapsed();
    }
  }

  async function handleSubmit(question: string) {
    const q = question.trim();
    if (!q || submitting) return;
    if (!getToken()) {
      setActive({
        job_id: "",
        question: q,
        status: "error",
        error: "Admin session missing — sign in again to run backtests.",
      });
      return;
    }

    setSubmitting(true);
    setInput("");
    setShowTrace(false);
    setActive({ job_id: "", question: q, status: "running", streaming_text: "" });
    startElapsed();

    try {
      const res = await fetch("/stock-api/aiem/chat/stream", {
        method: "POST",
        headers: authHeaders(true),
        credentials: "include",
        body: JSON.stringify({ question: q }),
      });

      if (res.status === 401 || res.status === 403) {
        clearElapsed();
        setActive({
          job_id: "",
          question: q,
          status: "error",
          error: "Unauthorized — re-authenticate with the AIEM Terminal password.",
        });
        return;
      }

      if (!res.ok) {
        // Fallback to non-stream job API (also admin-gated)
        const fallback = await fetch("/stock-api/aiem/chat", {
          method: "POST",
          headers: authHeaders(true),
          credentials: "include",
          body: JSON.stringify({ question: q }),
        });
        const data = await fallback.json().catch(() => ({}));
        if (!fallback.ok) {
          clearElapsed();
          setActive({
            job_id: "",
            question: q,
            status: "error",
            error: data.message || data.error || `Server error (${fallback.status})`,
          });
          return;
        }
        if (data.status === "done" && data.answer) {
          clearElapsed();
          setActive({
            job_id: data.job_id || "",
            question: q,
            status: "done",
            answer: data.answer,
          });
          refreshHistory();
          return;
        }
        if (data.job_id) {
          setActive({ job_id: data.job_id, question: q, status: "pending" });
          await pollUntilDone(data.job_id, q);
          return;
        }
        clearElapsed();
        setActive({
          job_id: "",
          question: q,
          status: "error",
          error: "No job_id returned from backtest session.",
        });
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        clearElapsed();
        setActive({
          job_id: "",
          question: q,
          status: "error",
          error: "Streaming unavailable in this browser.",
        });
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";
      let jobId = "";
      let streamed = "";
      let tools: ToolTraceStep[] = [];
      let finalAnswer: string | null = null;
      let finalError: string | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const line = chunk
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trim())
            .join("");
          if (!line) continue;
          let ev: any;
          try {
            ev = JSON.parse(line);
          } catch {
            continue;
          }
          if (ev.type === "started" && ev.job_id) {
            jobId = ev.job_id;
            setActive((prev) => (prev ? { ...prev, job_id: jobId, status: "running" } : prev));
          } else if (ev.type === "tool" && ev.tool) {
            tools = [...tools, { iteration: tools.length + 1, tool: ev.tool, ok: true }];
            setActive((prev) =>
              prev ? { ...prev, current_tool: ev.tool, tool_trace: tools, status: "running" } : prev,
            );
          } else if (ev.type === "token" && typeof ev.token === "string") {
            streamed += ev.token;
            setActive((prev) =>
              prev ? { ...prev, streaming_text: streamed, status: "running" } : prev,
            );
          } else if (ev.type === "done") {
            finalAnswer = ev.answer || streamed;
          } else if (ev.type === "error") {
            finalError = ev.error || "Backtest session failed.";
          }
        }
      }

      clearElapsed();
      if (finalError) {
        setActive({
          job_id: jobId,
          question: q,
          status: "error",
          error: finalError,
          tool_trace: tools,
          streaming_text: streamed || undefined,
        });
      } else {
        setActive({
          job_id: jobId,
          question: q,
          status: "done",
          answer: finalAnswer || streamed || "Session completed with no findings.",
          tool_trace: tools,
        });
        refreshHistory();
      }
    } catch (e: any) {
      clearElapsed();
      setActive({
        job_id: "",
        question: q,
        status: "error",
        error: e?.message || "Network error running backtest.",
      });
    } finally {
      if (mountedRef.current) setSubmitting(false);
    }
  }

  const isBusy = submitting || active?.status === "running" || active?.status === "pending";
  const displaySessions = [
    ...history.filter((h) => !active || h.job_id !== active.job_id),
  ].slice(0, 12);

  return (
    <div className="space-y-4 h-full flex flex-col min-h-0">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase flex items-center gap-2">
            <BarChart3 size={22} className="text-primary" />
            Backtest
          </h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">
            Ask AIEM to validate filters & signals · win rate · sample size · edge
          </p>
        </div>
        <button
          onClick={refreshHistory}
          className="flex items-center gap-2 text-xs font-mono text-muted-foreground hover:text-white transition-colors"
        >
          <History size={12} /> HISTORY
        </button>
      </div>

      <div className="border border-primary/25 bg-primary/5 px-4 py-3 text-xs font-mono text-muted-foreground shrink-0">
        <span className="text-primary font-bold">AIEM Terminal Backtest</span>
        {" · "}
        Uses your admin session (platform research key). Same engine as Stock Scanner Quant Agent —
        focused here on historical edge checks before you trust a live setup.
      </div>

      <div className="flex flex-wrap gap-2 shrink-0">
        {TEMPLATES.map((t) => (
          <button
            key={t.id}
            type="button"
            disabled={isBusy}
            onClick={() => setInput(t.prompt)}
            className="px-3 py-1.5 text-[11px] font-mono border border-border bg-black/40 text-muted-foreground hover:text-primary hover:border-primary/40 disabled:opacity-40 transition-colors"
          >
            <FlaskConical size={11} className="inline mr-1.5 -mt-0.5" />
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 border border-border bg-card flex flex-col">
        <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
          <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2 uppercase">
            <Sparkles size={14} /> Research Session
          </h2>
          {isBusy && (
            <span className="text-[11px] font-mono text-amber-400 flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" />
              {formatToolLabel(active?.current_tool)} · {elapsed}s
            </span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
          {!active && displaySessions.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-center px-6 py-12">
              <BarChart3 size={36} className="text-primary/50 mb-3" />
              <div className="text-sm font-mono text-white font-bold">Run your first backtest</div>
              <div className="text-xs font-mono text-muted-foreground mt-2 max-w-md">
                Pick a template above or ask something like:
                <span className="text-primary/90"> “Backtest 3× RVOL gap-ups under $50 over 90 days”</span>
              </div>
            </div>
          )}

          {displaySessions
            .slice()
            .reverse()
            .map((s) => (
              <SessionCard
                key={s.job_id || s.created_at || s.question}
                session={s}
                compact
                onReuse={() => setInput(s.question)}
              />
            ))}

          {active && (
            <SessionCard
              session={active}
              elapsed={elapsed}
              showTrace={showTrace}
              onToggleTrace={() => setShowTrace((v) => !v)}
            />
          )}
          <div ref={bottomRef} />
        </div>

        <form
          className="border-t border-border p-3 flex gap-2 shrink-0 bg-sidebar/30"
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isBusy}
            placeholder={
              isBusy
                ? "Backtest running…"
                : "Ask for a backtest — filter, lookback, universe, win-rate…"
            }
            className="flex-1 bg-black border border-border px-3 py-2.5 text-sm font-mono text-white placeholder:text-muted-foreground focus:outline-none focus:border-primary/50 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={isBusy || !input.trim()}
            className="px-4 py-2.5 bg-primary text-black font-mono font-bold text-xs uppercase tracking-wider hover:bg-primary/90 disabled:opacity-40 flex items-center gap-2"
          >
            {isBusy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            Run
          </button>
        </form>
      </div>

      <DataFooter source="/stock-api/aiem/chat/stream" lastUpdated={lastUpdated} />
    </div>
  );
}

function SessionCard({
  session,
  elapsed,
  compact,
  showTrace,
  onToggleTrace,
  onReuse,
}: {
  session: BacktestSession;
  elapsed?: number;
  compact?: boolean;
  showTrace?: boolean;
  onToggleTrace?: () => void;
  onReuse?: () => void;
}) {
  const realTrace = (session.tool_trace || []).filter((t) => t.tool !== "_timing");
  const body =
    session.status === "done"
      ? session.answer
      : session.status === "error"
        ? session.error
        : session.streaming_text ||
          (session.current_tool
            ? `${formatToolLabel(session.current_tool)}…`
            : "Starting research session…");

  return (
    <div
      className={`border bg-black/40 ${
        session.status === "error"
          ? "border-destructive/50"
          : session.status === "done"
            ? "border-border"
            : "border-primary/30"
      } ${compact ? "opacity-80" : ""}`}
    >
      <div className="px-3 py-2 border-b border-border/60 flex justify-between items-start gap-3">
        <div className="text-xs font-mono text-white font-semibold leading-relaxed">
          {session.question}
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {typeof elapsed === "number" &&
            (session.status === "running" || session.status === "pending") && (
              <span className="text-[10px] font-mono text-muted-foreground">{elapsed}s</span>
            )}
          <StatusPill status={session.status} />
        </div>
      </div>
      <div className="px-3 py-3">
        {session.status === "error" ? (
          <div className="flex items-start gap-2 text-xs font-mono text-destructive whitespace-pre-wrap">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            {body}
          </div>
        ) : (
          <div className="text-xs font-mono text-muted-foreground whitespace-pre-wrap leading-relaxed">
            {body || "—"}
            {(session.status === "running" || session.status === "pending") && (
              <span className="inline-block w-1.5 h-3 bg-primary/80 ml-0.5 animate-pulse align-middle" />
            )}
          </div>
        )}
        {!compact && realTrace.length > 0 && (
          <div className="mt-3">
            <button
              type="button"
              onClick={onToggleTrace}
              className="text-[10px] font-mono text-muted-foreground hover:text-primary uppercase tracking-wider"
            >
              {showTrace ? "Hide" : "Show"} tool trace ({realTrace.length})
            </button>
            {showTrace && (
              <ul className="mt-2 space-y-1">
                {realTrace.map((t, i) => (
                  <li key={`${t.tool}-${i}`} className="text-[10px] font-mono text-muted-foreground">
                    {i + 1}. {formatToolLabel(t.tool)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {compact && onReuse && (
          <button
            type="button"
            onClick={onReuse}
            className="mt-2 text-[10px] font-mono text-primary/80 hover:text-primary uppercase tracking-wider"
          >
            Reuse prompt
          </button>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: SessionStatus }) {
  const map: Record<SessionStatus, string> = {
    pending: "bg-amber-900/40 text-amber-300 border-amber-800",
    running: "bg-amber-900/40 text-amber-300 border-amber-800",
    done: "bg-green-900/40 text-green-400 border-green-800",
    error: "bg-red-900/40 text-red-400 border-red-800",
  };
  return (
    <span className={`px-2 py-0.5 text-[10px] font-mono font-bold border uppercase ${map[status]}`}>
      {status}
    </span>
  );
}
