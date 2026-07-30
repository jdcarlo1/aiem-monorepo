import { Link, useRoute } from 'wouter';
import {
  Activity,
  BarChart3,
  FileCheck,
  TrendingUp,
  AlertCircle,
  ListChecks,
} from 'lucide-react';

const navigation = [
  { name: 'Live Decisions', href: '/', icon: Activity },
  { name: 'Decision Proof', href: '/decisions', icon: FileCheck },
  { name: 'Positions & P&L', href: '/positions', icon: TrendingUp },
  { name: 'Calibration', href: '/calibration', icon: BarChart3 },
  { name: 'System Status', href: '/status', icon: AlertCircle },
];

export function Sidebar() {
  return (
    <aside className="w-56 bg-sidebar border-r border-sidebar-border flex flex-col">
      <div className="p-4 border-b border-sidebar-border">
        <h1 className="text-lg font-bold text-sidebar-foreground tracking-tight">
          Options Engine
        </h1>
        <p className="text-xs text-muted-foreground mt-0.5 font-mono">
          Terminal v2.1
        </p>
      </div>

      <nav className="flex-1 p-3 space-y-1">
        {navigation.map((item) => {
          const [isActive] = useRoute(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2 rounded text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                  : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
              }`}
              data-testid={`nav-${item.name.toLowerCase().replace(/\s+/g, '-')}`}
            >
              <item.icon className="w-4 h-4" />
              {item.name}
            </Link>
          );
        })}
      </nav>

      <div className="p-3 border-t border-sidebar-border">
        <div className="flex items-center gap-2 px-3 py-2">
          <div className="w-2 h-2 rounded-full bg-chart-2 animate-pulse" />
          <span className="text-xs text-muted-foreground font-mono">
            Chain Verified
          </span>
        </div>
      </div>
    </aside>
  );
}
