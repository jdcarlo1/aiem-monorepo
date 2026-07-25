import { ReactNode, useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { getToken, clearToken } from "@/lib/auth";
import { useLocation } from "wouter";
import { Menu } from "lucide-react";

function isAuthed(): boolean {
  return !!(getToken() || sessionStorage.getItem("aiem_authed"));
}

export function AppLayout({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const [checked, setChecked] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const redirected = useRef(false);

  const isLoginPage = location === "/";

  useEffect(() => {
    setSidebarOpen(false);
  }, [location]);

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
      .catch(() => {})
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
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div className={`
        fixed inset-y-0 left-0 z-50 md:relative md:translate-x-0 md:flex
        transition-transform duration-200 ease-in-out
        ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
      `}>
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </div>

      <main className="flex-1 overflow-y-auto bg-black relative w-full">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/5 via-black to-black pointer-events-none" />
        <div className="relative z-10 h-full flex flex-col">
          <div className="flex items-center gap-3 px-4 py-3 border-b border-border md:hidden">
            <button
              onClick={() => setSidebarOpen(true)}
              className="text-muted-foreground hover:text-primary transition-colors"
            >
              <Menu size={20} />
            </button>
            <span className="font-mono font-bold text-primary text-sm tracking-tighter">AIEM TERMINAL</span>
          </div>
          <div className="flex-1 overflow-y-auto p-4 md:p-6">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
