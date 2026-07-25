import { ReactNode, useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { getToken, clearToken } from "@/lib/auth";
import { useLocation } from "wouter";
import { Menu, Terminal } from "lucide-react";

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
    if (isLoginPage) { setChecked(true); return; }
    if (!isAuthed()) {
      if (!redirected.current) { redirected.current = true; setLocation("/"); }
      setChecked(true);
      return;
    }
    fetch("/stock-api/auth/me", {
      credentials: "include",
      headers: getToken() ? { "X-Admin-Token": getToken()! } : {},
    })
      .then((res) => {
        if (res.status === 401 || res.status === 403) {
          clearToken();
          sessionStorage.removeItem("aiem_authed");
          sessionStorage.removeItem("aiem_username");
          if (!redirected.current) { redirected.current = true; setLocation("/"); }
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
      <div className="min-h-screen bg-background flex items-center justify-center dark">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 bg-primary/20 rounded-sm flex items-center justify-center">
            <Terminal size={16} className="text-primary animate-pulse" />
          </div>
          <span className="font-mono text-xs text-muted-foreground tracking-widest uppercase">Authenticating…</span>
        </div>
      </div>
    );
  }

  if (!isAuthed()) return null;

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground dark">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div className={`
        fixed inset-y-0 left-0 z-50 md:relative md:translate-x-0 md:flex
        transition-transform duration-200 ease-in-out
        ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
      `}>
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </div>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto flex flex-col min-w-0">
        {/* Mobile top bar */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border md:hidden shrink-0 bg-sidebar/80 backdrop-blur-sm">
          <button
            onClick={() => setSidebarOpen(true)}
            className="text-muted-foreground hover:text-primary transition-colors p-1"
          >
            <Menu size={20} />
          </button>
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 bg-primary rounded-sm flex items-center justify-center">
              <Terminal size={11} className="text-black" />
            </div>
            <span className="font-bold text-sm text-white tracking-tight">AIEM Terminal</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 md:p-6 lg:p-8">
          {children}
        </div>
      </main>
    </div>
  );
}
