import { Link, useLocation } from "wouter";
import { 
  Terminal, Activity, BarChart2, Layers, ShieldCheck, 
  AlertTriangle, Users, Search, ActivitySquare, Calendar, 
  Workflow, RefreshCw, Bell, Moon, Sun, TrendingUp, BrainCircuit, Target, X, LogOut
} from "lucide-react";
import { useTheme } from "next-themes";
import { serverLogout } from "@/lib/auth";

const NAV_ITEMS = [
  { href: "/command", label: "CMD CENTER", icon: Terminal },
  { href: "/opportunities", label: "OPP QUEUE", icon: Search },
  { href: "/paper-trades", label: "PAPER TRADES", icon: BarChart2 },
  { href: "/decisions", label: "DECISIONS", icon: Activity },
  { href: "/proof", label: "EVIDENCE", icon: ShieldCheck },
  { href: "/risk", label: "RISK", icon: AlertTriangle },
  { href: "/council", label: "COUNCIL", icon: Users },
  { href: "/signals", label: "SIGNALS", icon: ActivitySquare },
  { href: "/regime", label: "REGIME", icon: Layers },
  { href: "/scheduler", label: "SCHEDULER", icon: Calendar },
  { href: "/options", label: "OPTIONS", icon: Workflow },
  { href: "/learning", label: "LEARNING", icon: RefreshCw },
  { href: "/alerts", label: "ALERTS", icon: Bell },
  { href: "/performance", label: "PERFORMANCE", icon: TrendingUp },
  { href: "/probability", label: "PROBABILITY", icon: BrainCircuit },
  { href: "/calibration", label: "CALIBRATION", icon: Target },
];

interface SidebarProps {
  onClose?: () => void;
}

export function Sidebar({ onClose }: SidebarProps) {
  const [location] = useLocation();
  const { theme, setTheme } = useTheme();

  const handleLogout = async () => {
    await serverLogout();
    sessionStorage.removeItem("aiem_authed");
    sessionStorage.removeItem("aiem_username");
    window.location.href = "/aiem/";
  };

  return (
    <aside className="w-64 border-r border-border bg-sidebar flex flex-col h-full shrink-0">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <div>
          <h1 className="text-xl font-mono font-bold text-primary tracking-tighter">AIEM TERMINAL</h1>
          <div className="text-xs text-muted-foreground font-mono mt-1">SYS_OP_SEC: AUTHORIZED</div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="md:hidden text-muted-foreground hover:text-primary transition-colors ml-2 shrink-0"
          >
            <X size={18} />
          </button>
        )}
      </div>
      
      <nav className="flex-1 overflow-y-auto p-2 font-mono text-sm">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const isActive = location === item.href;
            const Icon = item.icon;
            
            return (
              <li key={item.href}>
                <Link href={item.href}>
                  <div
                    onClick={onClose}
                    className={`flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors ${
                      isActive 
                        ? "bg-primary/10 text-primary border-l-2 border-primary" 
                        : "text-muted-foreground hover:bg-white/5 hover:text-foreground border-l-2 border-transparent"
                    }`}
                  >
                    <Icon size={16} className={isActive ? "text-primary" : "text-muted-foreground"} />
                    {item.label}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="p-4 border-t border-border space-y-2">
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors font-mono text-sm w-full"
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          {theme === "dark" ? "LIGHT MODE" : "DARK MODE"}
        </button>
        <button 
          onClick={handleLogout}
          className="flex items-center gap-2 text-muted-foreground hover:text-destructive transition-colors font-mono text-sm w-full"
        >
          <LogOut size={16} />
          DISCONNECT
        </button>
      </div>
    </aside>
  );
}
