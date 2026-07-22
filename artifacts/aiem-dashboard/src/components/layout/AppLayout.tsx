import { ReactNode, useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { getToken, clearToken } from "@/lib/auth";
import { useLocation } from "wouter";

function isAuthed(): boolean {
  return !!(getToken() || sessionStorage.getItem("aiem_authed"));
}

export function AppLayout({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const [checked, setChecked] = useState(false);
  const redirected = useRef(false);

  const isLoginPage = location === "/";

  useEffect(() => {
    if (isLoginPage) {
      setChecked(true);
      return;
    }
    if (!isAuthed()) {
      if (!redirected.current) {
        redirected.current = true;
        setLocation("/");
      }
      setChecked(true);
      return;
    }
    // Validate session server-side via /auth/me
    fetch("/stock-api/auth/me", { credentials: "include", headers: getToken() ? { "X-Admin-Token": getToken()! } : {} })
      .then((res) => {
        if (res.status === 401 || res.status === 403) {
          clearToken();
          sessionStorage.removeItem("aiem_authed");
          sessionStorage.removeItem("aiem_username");
          if (!redirected.current) {
            redirected.current = true;
            setLocation("/");
          }
        }
      })
      .catch(() => { /* network error — allow local auth to stand */ })
      .finally(() => setChecked(true));
  }, [location, isLoginPage, setLocation]);

  if (isLoginPage) {
    return <div className="min-h-screen bg-background text-foreground dark">{children}</div>;
  }

  if (!checked) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <span className="font-mono text-xs text-muted-foreground animate-pulse">Verifying session…</span>
      </div>
    );
  }

  if (!isAuthed()) {
    return null;
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground dark font-sans">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-black relative">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/5 via-black to-black pointer-events-none" />
        <div className="relative z-10 h-full flex flex-col p-6">
          {children}
        </div>
      </main>
    </div>
  );
}
