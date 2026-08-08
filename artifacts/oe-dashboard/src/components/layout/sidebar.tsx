import { useState } from 'react';
import { Link, useRoute } from 'wouter';
import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import {
  Activity,
  BarChart3,
  FileCheck,
  TrendingUp,
  AlertCircle,
  FlaskConical,
  Menu,
} from 'lucide-react';

const navigation = [
  { name: 'Live Decisions', href: '/', icon: Activity },
  { name: 'Decision Proof', href: '/decisions', icon: FileCheck },
  { name: 'Positions & P&L', href: '/positions', icon: TrendingUp },
  { name: 'Strategies', href: '/strategies', icon: FlaskConical },
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

function useChainBadge() {
  const { apiFetch } = useApi();
  const { data: chainStatus, isError } = useQuery({
    queryKey: ['sidebar-chain-status'],
    queryFn: () =>
      apiFetch<unknown>('/admin/evidence-chain/status').then(normaliseChainStatus),
    refetchInterval: 60_000,
    retry: false,
  });

  const chainVerified =
    !isError &&
    chainStatus !== undefined &&
    chainStatus.total_entries > 0 &&
    chainStatus.last_entry_hash !== '';

  const chainUnknown = chainStatus === undefined && !isError;

  const dotClass = chainUnknown
    ? 'w-2 h-2 rounded-full bg-muted animate-pulse'
    : chainVerified
      ? 'w-2 h-2 rounded-full bg-chart-2 animate-pulse'
      : 'w-2 h-2 rounded-full bg-destructive animate-pulse';

  const dotLabel = chainUnknown
    ? 'Chain Loading…'
    : chainVerified
      ? 'Chain Verified'
      : 'Chain UNVERIFIED';

  const labelClass = chainUnknown
    ? 'text-sm text-muted-foreground font-mono'
    : chainVerified
      ? 'text-sm text-muted-foreground font-mono'
      : 'text-sm text-destructive font-mono font-semibold';

  return { dotClass, dotLabel, labelClass };
}

function NavItem({
  href,
  name,
  icon: Icon,
  onNavigate,
}: {
  href: string;
  name: string;
  icon: typeof Activity;
  onNavigate?: () => void;
}) {
  const [isActive] = useRoute(href);
  return (
    <Link
      href={href}
      onClick={onNavigate}
      className={`flex items-center gap-3 px-3 py-2.5 rounded text-[15px] font-medium transition-colors ${
        isActive
          ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_0_0_0_1px_hsla(191,100%,52%,0.25)]'
          : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
      }`}
      data-testid={`nav-${name.toLowerCase().replace(/\s+/g, '-')}`}
    >
      <Icon className="w-5 h-5 shrink-0" />
      <span className="truncate">{name}</span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 overflow-y-auto p-3 space-y-1.5">
      {navigation.map((item) => (
        <NavItem
          key={item.href}
          href={item.href}
          name={item.name}
          icon={item.icon}
          onNavigate={onNavigate}
        />
      ))}
    </nav>
  );
}

function BrandHeader() {
  return (
    <div className="px-4 py-5 border-b border-sidebar-border relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-br from-primary/15 via-transparent to-transparent pointer-events-none" />
      <h1 className="relative text-xl font-bold text-sidebar-foreground tracking-tight">
        Options Engine
      </h1>
      <p className="relative text-sm text-muted-foreground mt-1 font-mono">
        Terminal v2.1
      </p>
    </div>
  );
}

function ChainFooter() {
  const { dotClass, dotLabel, labelClass } = useChainBadge();
  return (
    <div className="p-3 border-t border-sidebar-border">
      <div className="flex items-center gap-2.5 px-3 py-2.5">
        <div className={dotClass} />
        <span className={labelClass}>{dotLabel}</span>
      </div>
    </div>
  );
}

function DesktopSidebar() {
  return (
    <aside className="hidden md:flex w-60 shrink-0 h-full bg-sidebar border-r border-sidebar-border flex-col">
      <BrandHeader />
      <NavLinks />
      <ChainFooter />
    </aside>
  );
}

function MobileTopBar() {
  const [open, setOpen] = useState(false);
  const { dotClass, dotLabel, labelClass } = useChainBadge();

  return (
    <div className="md:hidden sticky top-0 z-40 flex items-center gap-3 border-b border-sidebar-border bg-sidebar px-3 py-2.5">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0 border-sidebar-border bg-sidebar-accent/40"
            aria-label="Open navigation"
            data-testid="nav-mobile-menu"
          >
            <Menu className="h-5 w-5" />
          </Button>
        </SheetTrigger>
        <SheetContent
          side="left"
          className="w-[min(18rem,85vw)] p-0 bg-sidebar text-sidebar-foreground border-sidebar-border"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>Options Engine navigation</SheetTitle>
          </SheetHeader>
          <div className="flex h-full flex-col">
            <BrandHeader />
            <NavLinks onNavigate={() => setOpen(false)} />
            <ChainFooter />
          </div>
        </SheetContent>
      </Sheet>

      <div className="min-w-0 flex-1">
        <div className="text-sm font-bold tracking-tight truncate">Options Engine</div>
        <div className="text-xs font-mono text-muted-foreground">Terminal v2.1</div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <div className={dotClass} />
        <span className={`${labelClass} text-xs hidden xs:inline`}>{dotLabel}</span>
      </div>
    </div>
  );
}

/**
 * Desktop: fixed left rail.
 * Mobile: hamburger drawer so Strategies / cards get full width (no crushed metrics).
 */
export function Sidebar() {
  // Desktop aside is `hidden md:flex`; mobile top bar is `md:hidden`.
  // Always mount both so first paint does not flash the wrong chrome.
  return (
    <>
      <DesktopSidebar />
      <MobileTopBar />
    </>
  );
}
