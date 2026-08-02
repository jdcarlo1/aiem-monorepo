import { Link, useRoute } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import {
  Activity,
  BarChart3,
  FileCheck,
  TrendingUp,
  AlertCircle,
} from 'lucide-react';

const navigation = [
  { name: 'Live Decisions', href: '/', icon: Activity },
  { name: 'Decision Proof', href: '/decisions', icon: FileCheck },
  { name: 'Positions & P&L', href: '/positions', icon: TrendingUp },
  { name: 'Calibration', href: '/calibration', icon: BarChart3 },
  { name: 'System Status', href: '/status', icon: AlertCircle },
];

interface ChainStatus {
  total_entries: number;
  last_entry_hash: string;
}

function normaliseChainStatus(resp: unknown): ChainStatus {
  const r = resp as Record<string, unknown>;
  return {
    total_entries: (r?.total_entries ?? 0) as number,
    last_entry_hash: (r?.last_entry_hash ?? '') as string,
  };
}

export function Sidebar() {
  const { apiFetch } = useApi();

  // Chain health poll — every 60 s, silent on error
  const { data: chainStatus, isError } = useQuery({
    queryKey: ['sidebar-chain-status'],
    queryFn: () =>
      apiFetch<unknown>('/admin/evidence-chain/status').then(normaliseChainStatus),
    refetchInterval: 60_000,
    retry: false,
  });

  // Derive display state:
  //   loading (undefined) → dim pulse (neutral)
  //   error or 0 entries or empty hash → red (destructive)
  //   entries present + hash non-empty → green (chart-2)
  const chainVerified =
    !isError &&
    chainStatus !== undefined &&
    chainStatus.total_entries > 0 &&
    chainStatus.last_entry_hash !== '';

  const chainUnknown = chainStatus === undefined && !isError;

  const dotClass = chainUnknown
    ? 'w-2 h-2 rounded-full bg-muted animate-pulse'          // loading — neutral
    : chainVerified
    ? 'w-2 h-2 rounded-full bg-chart-2 animate-pulse'        // green — verified
    : 'w-2 h-2 rounded-full bg-destructive animate-pulse';   // red — failed/unverified

  const dotLabel = chainUnknown
    ? 'Chain Loading…'
    : chainVerified
    ? 'Chain Verified'
    : 'Chain UNVERIFIED';

  const labelClass = chainUnknown
    ? 'text-xs text-muted-foreground font-mono'
    : chainVerified
    ? 'text-xs text-muted-foreground font-mono'
    : 'text-xs text-destructive font-mono font-semibold';

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
          <div className={dotClass} />
          <span className={labelClass}>{dotLabel}</span>
        </div>
      </div>
    </aside>
  );
}
