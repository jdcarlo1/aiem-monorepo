import type { ReactNode } from 'react';
import { Sidebar } from '@/components/layout/sidebar';

/**
 * Shared terminal chrome for authenticated OE pages.
 * Keeps the sidebar fixed-width and prevents main content from
 * crushing nav / packing tables edge-to-edge.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-[100dvh] overflow-hidden bg-background text-foreground">
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-y-auto">
        <div className="mx-auto w-full max-w-[1600px] p-4 sm:p-6 lg:p-8 space-y-6">
          {children}
        </div>
      </main>
    </div>
  );
}
