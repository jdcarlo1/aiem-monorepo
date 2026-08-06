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
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface DecisionAudit {
  decision_id: number;
  verification_status: string;
  engine_version: string;
  created_at: string;
  identity_json: Record<string, unknown>;
  technical_json: Record<string, unknown>;
  options_intel_json: Record<string, unknown>;
  probability_risk_json?: Record<string, unknown>;
  justification_json?: Record<string, unknown>;
}

interface GateEvent {
  id: number;
  trace_id: string;
  gate_type: string;
  action_taken: string;
  chain_hash: string;
  recorded_at: string;
}

interface EvidenceChainStatus {
  chain_seq: number;
  last_timestamp_utc: string;
  total_entries: number;
  last_entry_hash: string;
}

// ── response-shape normalisers ─────────────────────────────────────────────────
function normaliseDecisions(resp: unknown): DecisionAudit[] {
  if (Array.isArray(resp)) return resp as DecisionAudit[];
  const r = resp as Record<string, unknown>;
  return (Array.isArray(r?.rows) ? r.rows : []) as DecisionAudit[];
}

function normaliseGateEvents(resp: unknown): GateEvent[] {
  const rows: Record<string, unknown>[] = Array.isArray(resp)
    ? (resp as Record<string, unknown>[])
    : Array.isArray((resp as Record<string, unknown>)?.rows)
    ? ((resp as Record<string, unknown>).rows as Record<string, unknown>[])
    : [];
  return rows.map((e) => ({
    id: (e.gate_event_id ?? e.id) as number,
    trace_id: e.trace_id as string,
    gate_type: (e.gate_name ?? e.gate_type) as string,
    action_taken: e.action_taken as string,
    chain_hash: e.chain_hash as string,
    recorded_at: (e.fired_at ?? e.recorded_at) as string,
  }));
}

function normaliseChainStatus(resp: unknown): EvidenceChainStatus {
  const r = resp as Record<string, unknown>;
  return {
    chain_seq: (r?.chain_seq ?? r?.seq ?? 0) as number,
    last_timestamp_utc: (r?.last_timestamp_utc ?? '') as string,
    total_entries: (r?.total_entries ?? 0) as number,
    last_entry_hash: (r?.last_entry_hash ?? '') as string,
  };
}

export default function DecisionsPage() {
  const { apiFetch } = useApi();
  const [selectedDecision, setSelectedDecision] = useState<DecisionAudit | null>(null);

  const { data: decisions, isLoading: decisionsLoading } = useQuery({
    queryKey: ['decision-audit'],
    queryFn: () =>
      apiFetch<unknown>('/admin/decision-audit?limit=50').then(normaliseDecisions),
  });

  const { data: gateEvents } = useQuery({
    queryKey: ['gate-events'],
    queryFn: () => apiFetch<unknown>('/admin/gate-events?limit=20').then(normaliseGateEvents),
  });

  const { data: chainStatus } = useQuery({
    queryKey: ['evidence-chain-status'],
    queryFn: () => apiFetch<unknown>('/admin/evidence-chain/status').then(normaliseChainStatus),
  });

  const JsonViewer = ({ label, data }: { label: string; data?: Record<string, unknown> | null }) => {
    const [isOpen, setIsOpen] = useState(false);
    return (
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger className="flex items-center gap-2 w-full p-3 bg-muted/50 hover:bg-muted rounded text-sm font-medium">
          {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          {label}
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2">
          <pre className="text-xs font-mono bg-card border border-border p-3 rounded overflow-auto max-h-96">
            {JSON.stringify(data ?? null, null, 2)}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    );
  };

  if (decisionsLoading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 bg-muted rounded w-48" />
        <div className="h-64 bg-muted rounded" />
      </div>
    );
  }

  return (
    <>
      <div className="border-b border-border pb-5">
        <h1 className="text-2xl font-bold text-foreground tracking-tight">
          Decision Proof
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Cryptographically sealed decision audit trail
        </p>
      </div>

      {chainStatus && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground mb-1">Chain Sequence</p>
            <p className="text-2xl font-bold font-mono text-primary">
              {chainStatus.chain_seq}
            </p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground mb-1">Total Entries</p>
            <p className="text-2xl font-bold font-mono">{chainStatus.total_entries}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground mb-1">Last Timestamp</p>
            <p className="text-sm font-mono">{formatDate(chainStatus.last_timestamp_utc)}</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-4 min-w-0">
            <p className="text-xs text-muted-foreground mb-1">Last Entry Hash</p>
            <p className="text-xs font-mono text-primary truncate">
              {chainStatus.last_entry_hash}
            </p>
          </div>
        </div>
      )}

      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Decision Audit Records</h2>
        </div>
        {decisions && decisions.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Decision ID</TableHead>
                <TableHead>Verification</TableHead>
                <TableHead>Engine Version</TableHead>
                <TableHead>Created At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {decisions.map((decision) => (
                <TableRow
                  key={decision.decision_id}
                  className="cursor-pointer"
                  onClick={() => setSelectedDecision(decision)}
                  data-testid={`row-decision-${decision.decision_id}`}
                >
                  <TableCell className="font-mono font-semibold">
                    {decision.decision_id}
                  </TableCell>
                  <TableCell>
                    <Badge variant="success">{decision.verification_status}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {decision.engine_version}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatDate(decision.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 decision records found</p>
          </div>
        )}
      </div>

      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Gate Events</h2>
        </div>
        {gateEvents && gateEvents.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="font-mono">Trace ID</TableHead>
                <TableHead>Gate Type</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="font-mono">Chain Hash</TableHead>
                <TableHead>Recorded At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {gateEvents.map((event) => (
                <TableRow key={event.id} data-testid={`row-gate-${event.id}`}>
                  <TableCell className="font-mono text-xs text-primary">
                    {event.trace_id}
                  </TableCell>
                  <TableCell className="text-xs">{event.gate_type}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{event.action_taken}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs truncate max-w-xs">
                    {event.chain_hash}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatDate(event.recorded_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 gate events found</p>
          </div>
        )}
      </div>

      <Sheet open={!!selectedDecision} onOpenChange={() => setSelectedDecision(null)}>
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          {selectedDecision && (
            <>
              <SheetHeader>
                <SheetTitle className="font-mono">
                  Decision {selectedDecision.decision_id}
                </SheetTitle>
              </SheetHeader>
              <div className="mt-6 space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Verification Status</p>
                    <Badge variant="success">{selectedDecision.verification_status}</Badge>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Engine Version</p>
                    <p className="font-mono text-sm">{selectedDecision.engine_version}</p>
                  </div>
                </div>

                <div className="space-y-2">
                  <h3 className="font-semibold text-sm">Decision Layers</h3>
                  <JsonViewer label="Identity JSON" data={selectedDecision.identity_json} />
                  <JsonViewer label="Technical JSON" data={selectedDecision.technical_json} />
                  <JsonViewer label="Options Intel JSON" data={selectedDecision.options_intel_json} />
                  <JsonViewer label="Probability & Risk JSON" data={selectedDecision.probability_risk_json} />
                  <JsonViewer label="Justification JSON" data={selectedDecision.justification_json} />
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
