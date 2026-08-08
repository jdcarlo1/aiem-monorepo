import type { ReactNode } from 'react';
import { Sidebar } from '@/components/layout/sidebar';

/**
 * Shared terminal chrome for authenticated OE pages.
 * Desktop: fixed sidebar + scrollable main.
 * Mobile: top bar / drawer nav so cards are not crushed beside a 240px rail.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-[100dvh] flex-col md:flex-row overflow-hidden bg-background text-foreground">
      <Sidebar />
      <main className="flex-1 min-w-0 min-h-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1600px] p-4 sm:p-6 lg:p-8 space-y-6">
          {children}
        </div>
      </main>
    </div>
  );
}
