import { Link, useLocation } from "wouter";
import {
  Terminal, Activity, BarChart2, Layers, ShieldCheck,
  AlertTriangle, Users, Search, ActivitySquare, Calendar,
  Workflow, RefreshCw, Bell, TrendingUp, BrainCircuit, Target,
  X, LogOut, Wifi, ChevronRight, ClipboardCheck, GitBranch, Shield
} from "lucide-react";
import { serverLogout } from "@/lib/auth";

const NAV_GROUPS = [
  {
    label: "Operations",
    items: [
      { href: "/command", label: "Command Center", icon: Terminal },
      { href: "/scheduler", label: "Scheduler", icon: Calendar },
      { href: "/alerts", label: "Alerts", icon: Bell },
      { href: "/trace", label: "Trace Explorer", icon: GitBranch },
    ],
  },
  {
    label: "Trading",
    items: [
      { href: "/opportunities", label: "Opportunities", icon: Search },
      { href: "/paper-trades", label: "Paper Trades", icon: BarChart2 },
      { href: "/decisions", label: "Decisions", icon: Activity },
      { href: "/risk", label: "Risk", icon: AlertTriangle },
      { href: "/options", label: "Options", icon: Workflow },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/regime", label: "Regime", icon: Layers },
      { href: "/signals", label: "Signals", icon: ActivitySquare },
      { href: "/module4", label: "Signal Gate", icon: Shield },
      { href: "/council", label: "Specialist Council", icon: Users },
      { href: "/probability", label: "Probability", icon: BrainCircuit },
    ],
  },
  {
    label: "Analytics",
    items: [
      { href: "/performance", label: "Performance", icon: TrendingUp },
      { href: "/calibration", label: "Calibration", icon: Target },
      { href: "/learning", label: "Learning", icon: RefreshCw },
      { href: "/proof", label: "Evidence", icon: ShieldCheck },
      { href: "/audit", label: "Audit / Compliance", icon: ClipboardCheck },
    ],
  },
];

interface SidebarProps {
  onClose?: () => void;
}

export function Sidebar({ onClose }: SidebarProps) {
  const [location] = useLocation();

  const handleLogout = async () => {
    await serverLogout();
    sessionStorage.removeItem("aiem_authed");
    sessionStorage.removeItem("aiem_username");
    window.location.href = "/aiem/";
  };

  const username = sessionStorage.getItem("aiem_username") || "operator";

  return (
    <aside className="w-64 flex flex-col h-full bg-sidebar border-r border-sidebar-border shrink-0">
      {/* Logo / Header */}
      <div className="px-5 py-5 border-b border-sidebar-border relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-transparent pointer-events-none" />
        <div className="flex items-start justify-between relative">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <div className="w-6 h-6 bg-primary rounded-sm flex items-center justify-center shrink-0">
                <Terminal size={13} className="text-black" />
              </div>
              <span className="text-white font-bold text-base tracking-tight">AIEM</span>
            </div>
            <p className="text-muted-foreground text-[10px] font-mono uppercase tracking-widest ml-8">
              Institutional Terminal
            </p>
          </div>
          {onClose && (
            <button onClick={onClose} className="md:hidden text-muted-foreground hover:text-white transition-colors mt-0.5">
              <X size={16} />
            </button>
          )}
        </div>
        {/* Live indicator */}
        <div className="flex items-center gap-1.5 mt-3 ml-8">
          <div className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
          <span className="text-[10px] font-mono text-success uppercase tracking-wider">System Live</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4">
            <div className="px-3 py-1.5 text-[9px] font-mono uppercase tracking-[0.15em] text-muted-foreground/60 font-semibold">
              {group.label}
            </div>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = location === item.href;
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link href={item.href}>
                      <div
                        onClick={onClose}
                        className={`flex items-center gap-2.5 px-3 py-2 rounded-md cursor-pointer transition-all group ${
                          isActive
                            ? "bg-primary/15 text-primary"
                            : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-white"
                        }`}
                      >
                        <Icon
                          size={14}
                          className={`shrink-0 transition-colors ${isActive ? "text-primary" : "text-muted-foreground group-hover:text-white"}`}
                        />
                        <span className="text-sm font-medium flex-1">{item.label}</span>
                        {isActive && <ChevronRight size={12} className="text-primary/60" />}
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-sidebar-border space-y-1">
        <div className="flex items-center gap-2.5 px-3 py-2 rounded-md bg-sidebar-accent/50 mb-2">
          <div className="w-6 h-6 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center shrink-0">
            <span className="text-[9px] font-mono font-bold text-primary uppercase">
              {username.charAt(0)}
            </span>
          </div>
          <div className="min-w-0">
            <div className="text-xs font-medium text-white truncate">{username}</div>
            <div className="text-[9px] font-mono text-muted-foreground uppercase tracking-wider">Administrator</div>
          </div>
          <Wifi size={12} className="text-success shrink-0" />
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-3 py-2 rounded-md w-full text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all text-sm"
        >
          <LogOut size={14} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
