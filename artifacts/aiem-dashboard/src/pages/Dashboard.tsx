import { useEffect, useState } from "react"

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") + "/stock-api"

interface AlertRow {
  id: number
  ticker: string
  alert_date: string
  direction: string
  delta_val: number | null
  iv_rank: number | null
  expected_return: number | null
  created_at: string
}

interface ReconcileResult {
  db_count: number
  last_alert_date: string | null
  last_created_at: string | null
  sample: AlertRow[]
  reconcile_ok: boolean
  display_count: number
  error?: string
}

function reconcile(
  fetched: AlertRow[],
  dbCount: number
): { ok: boolean; note: string } {
  const display = fetched.length
  if (dbCount === 0 && display === 0)
    return { ok: true, note: "No options alerts in DB yet — pipeline has not run." }
  if (display === 0 && dbCount > 0)
    return { ok: false, note: `DB has ${dbCount} rows but display received 0 — API gap.` }
  return {
    ok: display <= dbCount,
    note: `Display shows ${display} of ${dbCount} DB rows — RECONCILED.`,
  }
}

export default function Dashboard() {
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [dbCount, setDbCount] = useState<number>(0)
  const [lastAlertDate, setLastAlertDate] = useState<string | null>(null)
  const [lastCreatedAt, setLastCreatedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/options/reconcile`)
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data: ReconcileResult = await r.json()
        setAlerts(data.sample ?? [])
        setDbCount(data.db_count ?? 0)
        setLastAlertDate(data.last_alert_date ?? null)
        setLastCreatedAt(data.last_created_at ?? null)
        setError(data.error ?? null)
      } catch (e) {
        setError(String(e))
        setAlerts([])
        setDbCount(0)
      } finally {
        setLoading(false)
      }
    }
    load()
    const iv = setInterval(load, 60_000)
    return () => clearInterval(iv)
  }, [])

  const { ok: reconcileOk, note: reconcileNote } = reconcile(alerts, dbCount)

  return (
    <div className="min-h-screen bg-black text-white p-6 font-mono space-y-6">
      <header className="border-b border-zinc-800 pb-4">
        <h1 className="text-xl font-bold uppercase tracking-widest text-white">
          AIEM — Options Pipeline Dashboard
        </h1>
        <p className="text-xs text-zinc-500 mt-1">
          Runtime ↔ Display reconciliation for{" "}
          <code className="text-cyan-400">aiem_options_alerts</code>
        </p>
      </header>

      {/* Runtime-vs-Display Reconciliation */}
      <section className="bg-zinc-900 border border-zinc-700 rounded p-4 space-y-3">
        <div className="flex items-center gap-3">
          <span className="text-xs uppercase tracking-widest text-zinc-400">
            Reconciliation
          </span>
          {loading ? (
            <span className="text-xs text-zinc-500">loading…</span>
          ) : reconcileOk ? (
            <span className="text-xs text-green-400">✓ OK</span>
          ) : (
            <span className="text-xs text-yellow-400">⚠ MISMATCH</span>
          )}
        </div>

        <div className="grid grid-cols-3 gap-3 text-xs">
          <div className="bg-zinc-800 rounded p-3">
            <div className="text-zinc-400 uppercase tracking-wide mb-1">DB Count</div>
            <div className="text-white text-2xl font-bold">{dbCount}</div>
            <div className="text-zinc-600 mt-1">aiem_options_alerts</div>
          </div>
          <div className="bg-zinc-800 rounded p-3">
            <div className="text-zinc-400 uppercase tracking-wide mb-1">Display Count</div>
            <div className="text-white text-2xl font-bold">{alerts.length}</div>
            <div className="text-zinc-600 mt-1">rows this fetch</div>
          </div>
          <div className="bg-zinc-800 rounded p-3">
            <div className="text-zinc-400 uppercase tracking-wide mb-1">Last Alert</div>
            <div className="text-white text-sm font-bold">{lastAlertDate ?? "—"}</div>
            <div className="text-zinc-600 mt-1">
              created {lastCreatedAt ? lastCreatedAt.slice(0, 19) : "—"}
            </div>
          </div>
        </div>

        <p
          className={`text-xs px-3 py-2 rounded ${
            reconcileOk
              ? "bg-green-950 text-green-300"
              : "bg-yellow-950 text-yellow-300"
          }`}
        >
          {reconcileNote}
        </p>

        {error && (
          <p className="text-xs text-red-400 bg-red-950 px-3 py-2 rounded">
            {error}
          </p>
        )}
      </section>

      {/* Options Alerts Sample Table */}
      <section className="bg-zinc-900 border border-zinc-700 rounded p-4 space-y-2">
        <h2 className="text-xs uppercase tracking-widest text-zinc-400">
          Recent Options Alerts
        </h2>
        {loading && (
          <p className="text-xs text-zinc-500">Loading options alerts…</p>
        )}
        {!loading && alerts.length === 0 && !error && (
          <p className="text-xs text-zinc-500">
            No options alerts found — pipeline may not have run yet today.
          </p>
        )}
        {!loading && alerts.length > 0 && (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-zinc-500 uppercase tracking-wide border-b border-zinc-800">
                <th className="text-left py-2 pr-4">Ticker</th>
                <th className="text-left py-2 pr-4">Date</th>
                <th className="text-left py-2 pr-4">Direction</th>
                <th className="text-right py-2 pr-4">Delta</th>
                <th className="text-right py-2 pr-4">IV Rank</th>
                <th className="text-right py-2">EV Return</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((row) => (
                <tr
                  key={row.id}
                  className="border-t border-zinc-800 text-zinc-300 hover:bg-zinc-800/30"
                >
                  <td className="py-2 pr-4 font-bold text-white">{row.ticker}</td>
                  <td className="py-2 pr-4 text-zinc-400">{row.alert_date}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`px-1.5 py-0.5 rounded text-xs font-semibold ${
                        row.direction === "LONG_CALL"
                          ? "bg-green-900 text-green-300"
                          : row.direction === "LONG_PUT"
                          ? "bg-red-900 text-red-300"
                          : "bg-zinc-800 text-zinc-400"
                      }`}
                    >
                      {row.direction}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-right">
                    {row.delta_val != null ? row.delta_val.toFixed(3) : "—"}
                  </td>
                  <td className="py-2 pr-4 text-right">
                    {row.iv_rank != null ? row.iv_rank.toFixed(1) : "—"}
                  </td>
                  <td className="py-2 text-right">
                    {row.expected_return != null
                      ? row.expected_return.toFixed(3)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <footer className="text-xs text-zinc-600 text-center border-t border-zinc-800 pt-4">
        Polling every 60s · Source:{" "}
        <code className="text-zinc-500">aiem_options_alerts</code> via{" "}
        <code className="text-zinc-500">/stock-api/options/reconcile</code>
      </footer>
    </div>
  )
}
