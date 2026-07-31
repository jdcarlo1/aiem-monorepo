import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useApi } from '@/hooks/use-api';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { formatDate } from '@/lib/utils';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface PipelineCandidate {
  id: number;
  ticker: string;
  scan_date: string;
  direction: string;
  status: string;
  trace_id: string;
  alert_id: number;
  selected_score: number;
  trigger_source: string;
  error_text?: string;
  completed_at?: string;
  decision_id?: number;
  verification_status?: string;
  gate_events_count?: number;
}

export default function LiveDecisionsPage() {
  const { apiFetch } = useApi();
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  const { data: candidates, isLoading } = useQuery({
    queryKey: ['pipeline-candidates'],
    queryFn: () =>
      apiFetch<PipelineCandidate[]>(
        '/admin/options-pipeline/candidates?limit=50'
      ),
    refetchInterval: 10000, // Poll every 10 seconds
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'DONE':
        return <Badge variant="success">DONE</Badge>;
      case 'FAILED':
        return <Badge variant="destructive">FAILED</Badge>;
      case 'NO_TRADE_GATES':
        return <Badge variant="warning">NO_TRADE_GATES</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-muted rounded w-48" />
          <div className="h-64 bg-muted rounded" />
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Live Decisions</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Real-time pipeline execution — polling every 10s
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
          <span className="text-xs text-muted-foreground font-mono">
            Live Feed Active
          </span>
        </div>
      </div>

      <div className="border border-border rounded-lg bg-card overflow-hidden">
        {candidates && candidates.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10"></TableHead>
                <TableHead>Ticker</TableHead>
                <TableHead>Scan Date</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="font-mono">Trace ID</TableHead>
                <TableHead>Alert ID</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidates.map((candidate) => (
                <>
                  <TableRow
                    key={candidate.id}
                    className="cursor-pointer"
                    onClick={() =>
                      setExpandedRow(
                        expandedRow === candidate.id ? null : candidate.id
                      )
                    }
                    data-testid={`row-candidate-${candidate.id}`}
                  >
                    <TableCell>
                      {expandedRow === candidate.id ? (
                        <ChevronDown className="w-4 h-4 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-muted-foreground" />
                      )}
                    </TableCell>
                    <TableCell className="font-semibold font-mono">
                      {candidate.ticker}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {candidate.scan_date}
                    </TableCell>
                    <TableCell>
                      <span
                        className={`font-mono text-xs ${
                          candidate.direction === 'CALL'
                            ? 'text-chart-2'
                            : 'text-chart-4'
                        }`}
                      >
                        {candidate.direction}
                      </span>
                    </TableCell>
                    <TableCell>{getStatusBadge(candidate.status)}</TableCell>
                    <TableCell className="font-mono text-xs text-primary">
                      {candidate.trace_id}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {candidate.alert_id}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {candidate.selected_score?.toFixed(3) ?? '—'}
                    </TableCell>
                    <TableCell className="text-xs">
                      {candidate.trigger_source}
                    </TableCell>
                  </TableRow>
                  {expandedRow === candidate.id && (
                    <TableRow>
                      <TableCell colSpan={9} className="bg-muted/30">
                        <div className="p-4 space-y-3">
                          <div className="grid grid-cols-3 gap-4">
                            <div>
                              <p className="text-xs text-muted-foreground mb-1">
                                Decision ID
                              </p>
                              <p className="font-mono text-sm">
                                {candidate.decision_id ?? 'None'}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-muted-foreground mb-1">
                                Verification Status
                              </p>
                              {candidate.verification_status ? (
                                <Badge variant="success">
                                  {candidate.verification_status}
                                </Badge>
                              ) : (
                                <span className="text-sm text-muted-foreground">
                                  —
                                </span>
                              )}
                            </div>
                            <div>
                              <p className="text-xs text-muted-foreground mb-1">
                                Gate Events
                              </p>
                              <p className="font-mono text-sm">
                                {candidate.gate_events_count ?? 0} events
                              </p>
                            </div>
                          </div>
                          {candidate.error_text && (
                            <div>
                              <p className="text-xs text-muted-foreground mb-1">
                                Error Details
                              </p>
                              <pre className="text-xs font-mono bg-destructive/10 text-destructive p-2 rounded overflow-auto">
                                {candidate.error_text}
                              </pre>
                            </div>
                          )}
                          {candidate.completed_at && (
                            <div>
                              <p className="text-xs text-muted-foreground mb-1">
                                Completed At
                              </p>
                              <p className="font-mono text-xs">
                                {formatDate(candidate.completed_at)}
                              </p>
                            </div>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">
              0 records matched — pipeline has not run today
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
