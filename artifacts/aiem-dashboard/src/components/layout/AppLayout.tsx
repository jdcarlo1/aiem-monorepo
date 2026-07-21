import { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { getToken } from "@/lib/auth";
import { useLocation } from "wouter";

export function AppLayout({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const token = getToken();

  // If unauthenticated and trying to access protected routes, should redirect to login
  // This is a simple protection mechanism
  if (!token && location !== "/") {
    window.location.href = "/";
    return null;
  }

  // Hide sidebar on login page
  if (location === "/") {
    return <div className="min-h-screen bg-background text-foreground dark">{children}</div>;
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
