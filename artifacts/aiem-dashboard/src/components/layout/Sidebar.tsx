import { Link, useLocation } from "wouter";
import {
  Terminal, Activity, BarChart2, Layers, ShieldCheck,
  AlertTriangle, Users, Search, ActivitySquare, Calendar,
  Workflow, RefreshCw, Bell, TrendingUp, BrainCircuit, Target,
  X, LogOut, Wifi, ChevronRight, ClipboardCheck, GitBranch, Shield,
  FlaskConical
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
      { href: "/pattern-lab", label: "Pattern Lab", icon: FlaskConical },
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
        <div className="absolute inset-0 bg-gradient-to-br from-primary/20 via-secondary/5 to-transparent pointer-events-none" />
        <div className="flex items-start justify-between relative">
          <div>
            <div className="flex items-center gap-2.5 mb-1">
              <div className="w-7 h-7 bg-primary rounded-sm flex items-center justify-center shrink-0 shadow-[0_0_16px_hsla(38,95%,58%,0.35)]">
                <Terminal size={14} className="text-black" />
              </div>
              <span className="text-white font-bold text-lg tracking-tight">AIEM</span>
            </div>
            <p className="text-muted-foreground text-xs font-mono uppercase tracking-widest ml-9">
              Institutional Terminal
            </p>
          </div>
          {onClose && (
            <button onClick={onClose} className="md:hidden text-muted-foreground hover:text-white transition-colors mt-0.5">
              <X size={18} />
            </button>
          )}
        </div>
        {/* Live indicator */}
        <div className="flex items-center gap-2 mt-3 ml-9">
          <div className="w-2 h-2 rounded-full bg-success animate-pulse shadow-[0_0_8px_hsla(142,72%,48%,0.7)]" />
          <span className="text-xs font-mono text-success uppercase tracking-wider font-semibold">System Live</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4">
            <div className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[0.14em] text-muted-foreground font-semibold">
              {group.label}
            </div>
            <ul className="space-y-1">
              {group.items.map((item) => {
                const isActive = location === item.href;
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link href={item.href}>
                      <div
                        onClick={onClose}
                        className={`flex items-center gap-2.5 px-3 py-2.5 rounded-md cursor-pointer transition-all group ${
                          isActive
                            ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_hsla(38,95%,58%,0.25)]"
                            : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-white"
                        }`}
                      >
                        <Icon
                          size={16}
                          className={`shrink-0 transition-colors ${isActive ? "text-primary" : "text-muted-foreground group-hover:text-white"}`}
                        />
                        <span className="text-[15px] font-medium flex-1">{item.label}</span>
                        {isActive && <ChevronRight size={14} className="text-primary/70" />}
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
        <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-md bg-sidebar-accent/50 mb-2">
          <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center shrink-0">
            <span className="text-xs font-mono font-bold text-primary uppercase">
              {username.charAt(0)}
            </span>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-white truncate">{username}</div>
            <div className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Administrator</div>
          </div>
          <Wifi size={14} className="text-success shrink-0" />
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-3 py-2.5 rounded-md w-full text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all text-[15px]"
        >
          <LogOut size={15} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
