import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Brain, Copy, Check, RefreshCw, ExternalLink, Users, Plus } from "lucide-react";

interface Affiliate {
  id: number;
  code: string;
  name: string;
  stripeConnectId: string | null;
  commissionPct: number;
  referralCount: number;
  stripeStatus: "active" | "pending" | "onboarding_incomplete" | "not_started" | "error";
  createdAt: string;
}

const statusLabel: Record<string, { label: string; color: string }> = {
  active: { label: "Active — receiving payouts", color: "text-green-600 bg-green-50 border-green-200" },
  pending: { label: "Submitted — awaiting Stripe approval", color: "text-yellow-700 bg-yellow-50 border-yellow-200" },
  onboarding_incomplete: { label: "Needs to finish onboarding", color: "text-orange-700 bg-orange-50 border-orange-200" },
  not_started: { label: "Onboarding not started", color: "text-slate-600 bg-slate-50 border-slate-200" },
  error: { label: "Error checking status", color: "text-red-600 bg-red-50 border-red-200" },
};

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={copy} className="p-1.5 rounded hover:bg-slate-100 transition-colors">
      {copied ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4 text-slate-500" />}
    </button>
  );
}

export default function AdminAffiliates() {
  const [authed, setAuthed] = useState(false);
  const [secretInput, setSecretInput] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [affiliates, setAffiliates] = useState<Affiliate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [newPct, setNewPct] = useState("50");
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState<{ onboardingUrl: string; code: string } | null>(null);

  const [refreshLinks, setRefreshLinks] = useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const headers = (token: string) => ({ "Content-Type": "application/json", "x-admin-secret": token });

  const load = async (token = adminToken) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/admin/affiliates", { headers: headers(token) });
      if (!r.ok) { setError("Failed to load affiliates"); return; }
      const data = await r.json();
      setAffiliates(data.affiliates ?? []);
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (authed) load(); }, [authed]);

  const handleAuth = async () => {
    setError(null);
    try {
      const r = await fetch("/api/admin/affiliates", { headers: headers(secretInput) });
      if (r.ok) {
        setAdminToken(secretInput);
        const data = await r.json();
        setAffiliates(data.affiliates ?? []);
        setAuthed(true);
      } else {
        setError("Wrong password");
      }
    } catch {
      setError("Network error");
    }
  };

  const handleCreate = async () => {
    if (!newCode || !newName) return;
    setCreating(true);
    setCreateResult(null);
    setError(null);
    try {
      const r = await fetch("/api/admin/affiliates", {
        method: "POST",
        headers: headers(adminToken),
        body: JSON.stringify({ code: newCode, name: newName, commissionPct: parseInt(newPct) }),
      });
      const data = await r.json();
      if (!r.ok) { setError(data.error ?? "Failed to create affiliate"); return; }
      setCreateResult({ onboardingUrl: data.onboardingUrl, code: data.code });
      setNewCode(""); setNewName(""); setNewPct("50");
      await load();
    } catch {
      setError("Network error");
    } finally {
      setCreating(false);
    }
  };

  const handleRefreshLink = async (code: string) => {
    setRefreshing(code);
    try {
      const r = await fetch(`/api/admin/affiliates/${code}/refresh-link`, { method: "POST", headers: headers(adminToken) });
      const data = await r.json();
      if (r.ok) setRefreshLinks(prev => ({ ...prev, [code]: data.onboardingUrl }));
    } finally {
      setRefreshing(null);
    }
  };

  if (!authed) {
    return (
      <div className="min-h-[100dvh] flex flex-col items-center justify-center bg-background p-4">
        <div className="w-full max-w-sm space-y-4">
          <div className="flex items-center gap-2 mb-6">
            <Brain className="w-6 h-6 text-primary" />
            <span className="font-bold text-lg">NCLEX AI — Admin</span>
          </div>
          <input
            type="password"
            placeholder="Admin password"
            value={secretInput}
            onChange={e => setSecretInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAuth()}
            className="w-full px-4 py-3 rounded-xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button onClick={handleAuth} className="w-full rounded-xl">Sign in</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-background p-4 sm:p-8">
      <div className="max-w-3xl mx-auto space-y-8">
        <div className="flex items-center gap-3">
          <Brain className="w-6 h-6 text-primary" />
          <h1 className="text-2xl font-bold">Affiliate Partners</h1>
          <button onClick={load} className="ml-auto p-2 rounded-lg hover:bg-slate-100">
            <RefreshCw className={`w-4 h-4 text-slate-500 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* Create new affiliate */}
        <div className="bg-card border border-border rounded-2xl p-6 space-y-4">
          <h2 className="font-semibold flex items-center gap-2">
            <Plus className="w-4 h-4" /> Add a new affiliate
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input
              placeholder="Code (e.g. JOHN50)"
              value={newCode}
              onChange={e => setNewCode(e.target.value.toUpperCase())}
              className="px-4 py-2.5 rounded-xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary font-mono"
            />
            <input
              placeholder="Their name"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              className="px-4 py-2.5 rounded-xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <div className="flex gap-2 items-center">
              <input
                type="number"
                min="1"
                max="99"
                value={newPct}
                onChange={e => setNewPct(e.target.value)}
                className="w-20 px-3 py-2.5 rounded-xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <span className="text-sm text-muted-foreground">% commission</span>
            </div>
          </div>
          <Button onClick={handleCreate} disabled={creating || !newCode || !newName} className="rounded-xl">
            {creating ? "Creating..." : "Create affiliate + get onboarding link"}
          </Button>

          {createResult && (
            <div className="bg-green-50 border border-green-200 rounded-xl p-4 space-y-2">
              <p className="text-sm font-semibold text-green-800">
                ✅ Affiliate <span className="font-mono">{createResult.code}</span> created! Send them this link:
              </p>
              <div className="flex items-center gap-2 bg-white border border-green-200 rounded-lg px-3 py-2">
                <span className="text-xs text-slate-700 break-all flex-1 font-mono">{createResult.onboardingUrl}</span>
                <CopyButton value={createResult.onboardingUrl} />
                <a href={createResult.onboardingUrl} target="_blank" rel="noopener noreferrer" className="p-1.5 rounded hover:bg-slate-100">
                  <ExternalLink className="w-4 h-4 text-slate-500" />
                </a>
              </div>
              <p className="text-xs text-green-700">This link expires in 24 hours. Use "Refresh link" below to generate a new one anytime.</p>
            </div>
          )}
        </div>

        {/* Affiliate list */}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {affiliates.length === 0 && !loading && (
          <div className="text-center py-12 text-muted-foreground">
            <Users className="w-10 h-10 mx-auto mb-3 opacity-30" />
            <p>No affiliates yet. Add your first one above.</p>
          </div>
        )}

        <div className="space-y-4">
          {affiliates.map(aff => {
            const st = statusLabel[aff.stripeStatus] ?? statusLabel.error;
            const link = refreshLinks[aff.code];
            return (
              <div key={aff.id} className="bg-card border border-border rounded-2xl p-5 space-y-3">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-lg">{aff.name}</span>
                      <span className="font-mono text-sm bg-slate-100 px-2 py-0.5 rounded">{aff.code}</span>
                    </div>
                    <p className="text-sm text-muted-foreground mt-0.5">
                      {aff.commissionPct}% commission · {aff.referralCount} subscriber{aff.referralCount !== 1 ? "s" : ""} referred
                    </p>
                  </div>
                  <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${st.color}`}>
                    {st.label}
                  </span>
                </div>

                <div className="flex flex-wrap gap-2 pt-1">
                  <button
                    onClick={() => handleRefreshLink(aff.code)}
                    disabled={refreshing === aff.code}
                    className="text-xs px-3 py-1.5 rounded-lg border border-border hover:bg-slate-50 flex items-center gap-1.5 transition-colors"
                  >
                    <RefreshCw className={`w-3 h-3 ${refreshing === aff.code ? "animate-spin" : ""}`} />
                    {refreshing === aff.code ? "Generating..." : "Refresh onboarding link"}
                  </button>
                </div>

                {link && (
                  <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
                    <span className="text-xs text-slate-700 break-all flex-1 font-mono">{link}</span>
                    <CopyButton value={link} />
                    <a href={link} target="_blank" rel="noopener noreferrer" className="p-1.5 rounded hover:bg-slate-100">
                      <ExternalLink className="w-4 h-4 text-slate-500" />
                    </a>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
