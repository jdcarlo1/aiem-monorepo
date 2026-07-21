import { Clock, Database, Activity, AlertTriangle } from "lucide-react";

interface DataFooterProps {
  source: string;
  lastUpdated: Date | null;
  operatingMode?: string;
  samplePeriod?: string;
  isStale?: boolean;
  pollIntervalSec?: number;
  className?: string;
}

export function DataFooter({
  source,
  lastUpdated,
  operatingMode,
  samplePeriod,
  isStale,
  pollIntervalSec,
  className = "",
}: DataFooterProps) {
  const fmtTime = (d: Date) =>
    d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

  const fmtDate = (d: Date) =>
    d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 border-t border-border/50 bg-black/30 font-mono text-[10px] text-muted-foreground/70 ${className}`}
    >
      <span className="flex items-center gap-1">
        <Database size={9} className="shrink-0" />
        <span className="uppercase tracking-wide">SOURCE:</span>
        <span className="text-muted-foreground">{source}</span>
      </span>

      <span className="flex items-center gap-1">
        <Clock size={9} className="shrink-0" />
        {lastUpdated ? (
          <>
            <span className="uppercase tracking-wide">FETCHED:</span>
            <span className={isStale ? "text-destructive" : "text-muted-foreground"}>
              {fmtDate(lastUpdated)} {fmtTime(lastUpdated)}
              {isStale && " [STALE]"}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground/50">NOT YET FETCHED</span>
        )}
        {pollIntervalSec && (
          <span className="text-muted-foreground/40">(polls every {pollIntervalSec}s)</span>
        )}
      </span>

      {operatingMode && (
        <span className="flex items-center gap-1">
          <Activity size={9} className="shrink-0" />
          <span className="uppercase tracking-wide">MODE:</span>
          <span className={`font-bold uppercase ${operatingMode.includes("PAPER") ? "text-accent" : operatingMode.includes("LIVE") ? "text-destructive" : "text-muted-foreground"}`}>
            {operatingMode}
          </span>
        </span>
      )}

      {samplePeriod && (
        <span className="flex items-center gap-1">
          <AlertTriangle size={9} className="shrink-0" />
          <span className="uppercase tracking-wide">PERIOD:</span>
          <span className="text-muted-foreground">{samplePeriod}</span>
        </span>
      )}
    </div>
  );
}
