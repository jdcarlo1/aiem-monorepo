import { useState } from "react";
import { useLocation } from "wouter";
import { setToken, setCsrfToken } from "@/lib/auth";
import { Terminal } from "lucide-react";

type Mode = "password" | "token";

export default function Login() {
  const [, setLocation] = useLocation();
  const [mode, setMode] = useState<Mode>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/stock-api/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (res.status === 401 || res.status === 403) {
        const body = await res.json().catch(() => ({}));
        setError(body.error || "Invalid credentials");
        return;
      }
      if (res.status === 429) {
        setError("Too many attempts — account locked for 15 minutes");
        return;
      }
      if (!res.ok) {
        setError(`Authentication error (${res.status})`);
        return;
      }
      const data = await res.json();
      // Store CSRF token from response or cookie
      const csrf = data.csrf_token || "";
      if (csrf) setCsrfToken(csrf);
      // Read CSRF cookie if not in body
      const cookieMatch = document.cookie.match(/(?:^|;\s*)aiem_csrf=([^;]+)/);
      if (!csrf && cookieMatch) setCsrfToken(decodeURIComponent(cookieMatch[1]));
      // Mark as authenticated (cookie-based session)
      sessionStorage.setItem("aiem_authed", "1");
      sessionStorage.setItem("aiem_username", data.user?.username || username.trim());
      setLocation("/command");
    } catch {
      setError("Network error — unable to reach server");
    } finally {
      setLoading(false);
    }
  };

  const handleTokenLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (token.trim()) {
      setToken(token.trim());
      sessionStorage.setItem("aiem_authed", "1");
      setLocation("/command");
    }
  };

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      setTokenInput(text);
    } catch {
      // clipboard read failed — user must paste manually
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

        {/* Mode toggle */}
        <div className="flex mb-6 border border-border">
          {(["password", "token"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(""); }}
              className={`flex-1 py-2 font-mono text-xs uppercase tracking-wider transition-colors ${
                mode === m
                  ? "bg-primary text-black font-bold"
                  : "bg-transparent text-muted-foreground hover:text-primary"
              }`}
            >
              {m === "password" ? "Password" : "Admin Token"}
            </button>
          ))}
        </div>

        {mode === "password" ? (
          <form onSubmit={handlePasswordLogin} className="space-y-4">
            <div className="space-y-2">
              <label className="text-xs font-mono text-muted-foreground uppercase">Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-black border border-border px-3 py-2 font-mono text-sm text-primary focus:outline-none focus:border-primary transition-colors rounded-none"
                placeholder="admin"
                autoFocus
                autoComplete="username"
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-mono text-muted-foreground uppercase">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-black border border-border px-3 py-2 font-mono text-sm text-primary focus:outline-none focus:border-primary transition-colors rounded-none"
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>
            {error && (
              <p className="text-red-400 font-mono text-xs border border-red-900 bg-red-950/20 px-3 py-2">
                ⚠ {error}
              </p>
            )}
            <button
              type="submit"
              disabled={!username.trim() || !password || loading}
              className="w-full bg-primary text-black font-mono font-bold py-3 uppercase tracking-wider hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed rounded-none"
            >
              {loading ? "Authenticating…" : "Initialize Connection"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleTokenLogin} className="space-y-4">
            <div className="space-y-2">
              <label className="text-xs font-mono text-muted-foreground uppercase">Admin Authentication Token</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={token}
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="flex-1 bg-black border border-border px-3 py-2 font-mono text-sm text-primary focus:outline-none focus:border-primary transition-colors rounded-none"
                  placeholder="Enter or paste token…"
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
        )}

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
