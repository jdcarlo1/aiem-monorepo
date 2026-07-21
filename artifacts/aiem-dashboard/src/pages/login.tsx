import { useState } from "react";
import { useLocation } from "wouter";
import { setToken } from "@/lib/auth";
import { Terminal } from "lucide-react";

export default function Login() {
  const [, setLocation] = useLocation();
  const [token, setTokenInput] = useState("");

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (token.trim()) {
      setToken(token.trim());
      setLocation("/command");
    }
  };

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      setTokenInput(text);
    } catch (err) {
      console.error("Failed to read clipboard", err);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-black">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(30,30,30,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(30,30,30,0.5)_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none opacity-20" />
      
      <div className="w-full max-w-md p-8 border border-border bg-card relative z-10 shadow-[0_0_50px_rgba(255,165,0,0.05)]">
        <div className="flex flex-col items-center mb-8">
          <Terminal size={48} className="text-primary mb-4" />
          <h1 className="text-3xl font-mono font-bold tracking-tighter text-white">AIEM</h1>
          <p className="text-primary font-mono text-sm mt-2 tracking-widest uppercase">Institutional Terminal</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-6">
          <div className="space-y-2">
            <label className="text-xs font-mono text-muted-foreground uppercase">Admin Authentication Token</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={token}
                onChange={(e) => setTokenInput(e.target.value)}
                className="flex-1 bg-black border border-border px-3 py-2 font-mono text-sm text-primary focus:outline-none focus:border-primary transition-colors rounded-none"
                placeholder="Enter or paste token..."
                autoFocus
              />
              <button
                type="button"
                onClick={handlePaste}
                className="px-4 py-2 border border-border bg-sidebar hover:bg-primary/10 hover:text-primary transition-colors font-mono text-xs text-muted-foreground rounded-none"
              >
                PASTE
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={!token.trim()}
            className="w-full bg-primary text-black font-mono font-bold py-3 uppercase tracking-wider hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed rounded-none"
          >
            Initialize Connection
          </button>
        </form>

        <div className="mt-8 text-center">
          <p className="text-[10px] font-mono text-muted-foreground">
            UNAUTHORIZED ACCESS IS STRICTLY PROHIBITED
            <br />
            ALL ACTIONS ARE LOGGED TO THE EVIDENCE CHAIN
          </p>
        </div>
      </div>
    </div>
  );
}
