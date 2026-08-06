import { useEffect } from "react";
import { useLocation } from "wouter";

/**
 * Orphaned legacy options-reconcile dashboard.
 * Superseded by TraceExplorer at /trace — redirect so this file isn't a dead trap.
 */
export default function Dashboard() {
  const [, setLocation] = useLocation();

  useEffect(() => {
    setLocation("/trace");
  }, [setLocation]);

  return (
    <div className="font-mono text-sm text-muted-foreground p-6">
      Redirecting to Trace Explorer (/trace)…
    </div>
  );
}
