import { useState } from "react";
import { useLocation } from "wouter";
import { setToken, setCsrfToken, setRole } from "@/lib/auth";
import { Terminal, Lock, Key, ArrowRight, Clipboard } from "lucide-react";

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
        setError("Too many attempts — try again in 15 minutes");
        return;
      }
      if (!res.ok) {
        setError(`Authentication error (${res.status})`);
        return;
      }
      const data = await res.json();
      const csrf = data.csrf_token || "";
      if (csrf) setCsrfToken(csrf);
      const cookieMatch = document.cookie.match(/(?:^|;\s*)aiem_csrf=([^;]+)/);
      if (!csrf && cookieMatch) setCsrfToken(decodeURIComponent(cookieMatch[1]));
      if (data.session_token) setToken(data.session_token);
      setRole("Admin");
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
      setRole("Admin");
      sessionStorage.setItem("aiem_authed", "1");
      setLocation("/command");
    }
  };

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      setTokenInput(text);
    } catch {
      // user must paste manually
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden">
      {/* Background effects */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,hsla(38,92%,55%,0.08)_0%,transparent_60%)] pointer-events-none" />
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.015)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.015)_1px,transparent_1px)] bg-[size:48px_48px] pointer-events-none" />

      <div className="w-full max-w-sm px-4 relative z-10">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-xl bg-primary/15 border border-primary/30 flex items-center justify-center mb-4 glow-primary">
            <Terminal size={26} className="text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">AIEM Terminal</h1>
          <p className="text-sm text-muted-foreground mt-1 font-mono tracking-wider">INSTITUTIONAL ACCESS</p>
        </div>

        {/* Card */}
        <div className="bg-card border border-border rounded-lg overflow-hidden shadow-2xl shadow-black/50">
          {/* Tab switcher */}
          <div className="flex border-b border-border">
            {([["password", "Password", Lock], ["token", "Admin Token", Key]] as [Mode, string, any][]).map(([m, label, Icon]) => (
              <button
                key={m}
                onClick={() => { setMode(m); setError(""); }}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-all ${
                  mode === m
                    ? "bg-primary/10 text-primary border-b-2 border-primary"
                    : "text-muted-foreground hover:text-white hover:bg-white/5 border-b-2 border-transparent"
                }`}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
          </div>

          <div className="p-6">
            {mode === "password" ? (
              <form onSubmit={handlePasswordLogin} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Username</label>
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full bg-background border border-border rounded-md px-3 py-2.5 text-sm text-white placeholder:text-muted-foreground/50 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all"
                    placeholder="Enter username"
                    autoFocus
                    autoComplete="username"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-background border border-border rounded-md px-3 py-2.5 text-sm text-white placeholder:text-muted-foreground/50 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all"
                    placeholder="••••••••"
                    autoComplete="current-password"
                  />
                </div>

                {error && (
                  <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive/30 rounded-md">
                    <span className="text-xs text-destructive">{error}</span>
                  </div>
                )}

                <button
                  type="submit"
                  disabled={!username.trim() || !password || loading}
                  className="w-full flex items-center justify-center gap-2 bg-primary text-black font-semibold py-2.5 rounded-md hover:bg-primary/90 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <span className="text-sm">Authenticating…</span>
                  ) : (
                    <>
                      <span className="text-sm">Sign In</span>
                      <ArrowRight size={15} />
                    </>
                  )}
                </button>
              </form>
            ) : (
              <form onSubmit={handleTokenLogin} className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Admin Token</label>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={token}
                      onChange={(e) => setTokenInput(e.target.value)}
                      className="flex-1 bg-background border border-border rounded-md px-3 py-2.5 text-sm text-white placeholder:text-muted-foreground/50 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all font-mono"
                      placeholder="Paste token…"
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={handlePaste}
                      className="px-3 py-2.5 bg-background border border-border rounded-md text-muted-foreground hover:text-primary hover:border-primary/50 transition-all"
                    >
                      <Clipboard size={15} />
                    </button>
                  </div>
                </div>

                {error && (
                  <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive/30 rounded-md">
                    <span className="text-xs text-destructive">{error}</span>
                  </div>
                )}

                <button
                  type="submit"
                  disabled={!token.trim()}
                  className="w-full flex items-center justify-center gap-2 bg-primary text-black font-semibold py-2.5 rounded-md hover:bg-primary/90 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <span className="text-sm">Authenticate</span>
                  <ArrowRight size={15} />
                </button>
              </form>
            )}
          </div>
        </div>

        <p className="text-center text-[10px] font-mono text-muted-foreground/50 mt-6 uppercase tracking-widest">
          Unauthorized access is prohibited · All sessions are logged
        </p>
      </div>
    </div>
  );
}
