import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { ShieldCheck, Fingerprint, Network, Terminal as TerminalIcon } from "lucide-react";
import { getToken } from "@/lib/auth";
import { useToast } from "@/hooks/use-toast";

export default function Proof() {
  const { data: status, loading } = useApi<any>("/stock-api/admin/evidence-chain/status", {}, 60000);
  const [tokenInput, setTokenInput] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<{valid: boolean, message: string} | null>(null);
  const { toast } = useToast();

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;

    setVerifying(true);
    setVerifyResult(null);

    try {
      const res = await fetch("/stock-api/admin/aiem-verify-proof", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Token": getToken() || ""
        },
        body: JSON.stringify({ proof_token: tokenInput.trim() })
      });

      const data = await res.json();
      setVerifyResult(data);
      
      toast({
        title: data.valid ? "Proof Verified" : "Verification Failed",
        description: data.message,
        variant: data.valid ? "default" : "destructive",
      });
    } catch (err: any) {
      setVerifyResult({ valid: false, message: err.message || "Network error during verification" });
    } finally {
      setVerifying(false);
    }
  };

  const isWarning = status?.last_exit_code === 1;

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div className="flex justify-between items-end border-b border-border pb-4 shrink-0">
        <div>
          <h1 className="text-2xl font-mono font-bold text-white tracking-tight uppercase">Decision Proof</h1>
          <p className="text-sm font-mono text-muted-foreground mt-1">Evidence Chain Status & HMAC Verification</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        {/* Status Panel */}
        <div className="border border-border bg-card flex flex-col">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-primary flex items-center gap-2">
              <Network size={14} /> CHAIN STATUS
            </h2>
          </div>
          <div className="p-6 flex-1 overflow-auto">
            {loading ? (
              <div className="font-mono text-muted-foreground">SCANNING LEDGER...</div>
            ) : status ? (
              <div className="space-y-6">
                <div className="flex items-center gap-4 border border-border p-4 bg-black">
                  <div className={`p-3 border ${isWarning ? 'border-accent text-accent' : 'border-success text-success'}`}>
                    {isWarning ? <ShieldCheck size={24} className="opacity-50" /> : <ShieldCheck size={24} />}
                  </div>
                  <div>
                    <div className="text-sm font-mono text-muted-foreground">CHAIN INTEGRITY</div>
                    <div className={`text-xl font-mono font-bold ${isWarning ? 'text-accent' : 'text-success'}`}>
                      {isWarning ? 'WARNING (CODE 1)' : 'SECURE (CODE 0)'}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs font-mono text-muted-foreground mb-1">SEQUENCE</div>
                    <div className="text-2xl font-mono font-bold text-white">#{status.seq}</div>
                  </div>
                  <div className="border border-border p-4 bg-black">
                    <div className="text-xs font-mono text-muted-foreground mb-1">TOTAL ENTRIES</div>
                    <div className="text-2xl font-mono font-bold text-white">{status.total_entries}</div>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-xs font-mono text-muted-foreground uppercase">Last Hash</div>
                  <div className="p-3 border border-border bg-black font-mono text-xs text-secondary break-all">
                    {status.last_entry_hash || "N/A"}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-xs font-mono text-muted-foreground uppercase">Last Command</div>
                  <div className="p-3 border border-border bg-black font-mono text-xs text-muted-foreground break-all">
                    {`> ${status.last_command || "N/A"}`}
                  </div>
                </div>
                
                <div className="text-xs font-mono text-muted-foreground text-right">
                  LAST UPDATE: {status.last_timestamp_utc ? new Date(status.last_timestamp_utc).toLocaleString() : "N/A"}
                </div>
              </div>
            ) : (
              <div className="font-mono text-destructive">UNABLE TO READ CHAIN</div>
            )}
          </div>
        </div>

        {/* Verification Panel */}
        <div className="border border-border bg-card flex flex-col">
          <div className="p-3 border-b border-border bg-sidebar/50 flex justify-between items-center shrink-0">
            <h2 className="text-sm font-mono font-bold text-secondary flex items-center gap-2">
              <Fingerprint size={14} /> HMAC VERIFICATION
            </h2>
          </div>
          <div className="p-6 flex-1 flex flex-col">
            <p className="text-sm font-mono text-muted-foreground mb-6">
              Enter a proof token to verify cryptographic integrity against the local chain ledger.
            </p>

            <form onSubmit={handleVerify} className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-mono text-muted-foreground uppercase">PROOF TOKEN</label>
                <textarea
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="w-full h-32 bg-black border border-border p-3 font-mono text-sm text-primary focus:outline-none focus:border-primary resize-none"
                  placeholder="Paste JWT or HMAC string..."
                />
              </div>

              <button
                type="submit"
                disabled={!tokenInput.trim() || verifying}
                className="w-full bg-sidebar border border-border text-white font-mono font-bold py-3 uppercase tracking-wider hover:bg-white/5 transition-colors flex justify-center items-center gap-2 disabled:opacity-50"
              >
                <TerminalIcon size={16} />
                {verifying ? "VERIFYING..." : "EXECUTE VERIFICATION"}
              </button>
            </form>

            {verifyResult && (
              <div className={`mt-6 p-4 border ${verifyResult.valid ? 'border-success bg-success/10' : 'border-destructive bg-destructive/10'}`}>
                <div className={`text-sm font-mono font-bold mb-1 ${verifyResult.valid ? 'text-success' : 'text-destructive'}`}>
                  {verifyResult.valid ? 'VERIFICATION SUCCESSFUL' : 'VERIFICATION FAILED'}
                </div>
                <div className="text-xs font-mono text-white">
                  {verifyResult.message}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
