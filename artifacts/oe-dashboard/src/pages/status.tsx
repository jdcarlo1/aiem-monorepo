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

interface JobHealth {
  job_name: string;
  last_heartbeat: string;
  consecutive_failures: number;
  last_error?: string;
}

interface SchedulerJob {
  job_id: string;
  job_name: string;
  next_run_time: string;
  trigger_type: string;
}

interface ReconcileStatus {
  status: string;
  last_run: string;
  discrepancies?: number;
}

interface PipelineCheckpoint {
  checkpoint_id: number;
  stage: string;
  status: string;
  updated_at: string;
}

// ── Response shape normalisers ────────────────────────────────────────────────
function normaliseJobHealth(resp: unknown): JobHealth[] {
  // /admin/job-heartbeats returns { status, jobs: [{job_name, last_success, last_attempt, consecutive_failures, last_error}] }
  // /admin/job-health    returns { alerts, checked_at, healthy, status, total_jobs }
  if (Array.isArray(resp)) return resp as JobHealth[];
  const r = resp as Record<string, unknown>;
  const jobs = r?.jobs;
  if (Array.isArray(jobs)) {
    return (jobs as Record<string, unknown>[]).map((j) => ({
      job_name: j.job_name as string,
      last_heartbeat: (j.last_attempt ?? j.last_success ?? '') as string,
      consecutive_failures: (j.consecutive_failures ?? 0) as number,
      last_error: j.last_error as string | undefined,
    }));
  }
  return [];
}

function normaliseSchedulerJobs(resp: unknown): SchedulerJob[] {
  if (Array.isArray(resp)) return resp as SchedulerJob[];
  const r = resp as Record<string, unknown>;
  const jobs = r?.jobs;
  if (!Array.isArray(jobs)) return [];
  return (jobs as Record<string, unknown>[]).map((j) => ({
    job_id: (j.id ?? j.job_id ?? '') as string,
    job_name: (j.name ?? j.job_name ?? '') as string,
    next_run_time: (j.next_run ?? j.next_run_time ?? '') as string,
    trigger_type: (j.trigger ?? j.trigger_type ?? '') as string,
  }));
}

function normalisePipelineCheckpoint(resp: unknown): PipelineCheckpoint | null {
  if (!resp || typeof resp !== 'object') return null;
  const r = resp as Record<string, unknown>;
  // /admin/pipeline-checkpoint returns:
  // {date, jobs:[{ticker,status}], pending, done, pipeline_run:{status,trigger_source}, needs_recovery}
  if (r?.pipeline_run || r?.date) {
    const pr = (r.pipeline_run ?? {}) as Record<string, unknown>;
    return {
      checkpoint_id: 0,
      stage: (pr.trigger_source ?? 'primary') as string,
      status: (pr.status ?? r.status ?? 'UNKNOWN') as string,
      updated_at: (r.date ?? '') as string,
    };
  }
  // Fallback: generic object with updated_at
  if (!r?.updated_at && !r?.status) return null;
  return {
    checkpoint_id: (r.checkpoint_id ?? r.id ?? 0) as number,
    stage: (r.stage ?? r.phase ?? '') as string,
    status: (r.status ?? '') as string,
    updated_at: (r.updated_at ?? r.completed_at ?? '') as string,
  };
}

/** OE-scoped job names — hide AIEM equity / morning / paper jobs from OE Status. */
const OE_JOB_RE =
  /option|oe_|gex|0dte|spy_0dte|f3_|options_pipeline|options_structure|reconcile|gamma/i;

function isOeJob(name: string | undefined | null): boolean {
  return !!name && OE_JOB_RE.test(String(name));
}

export default function StatusPage() {
  const { apiFetch } = useApi();

  // Use /admin/job-heartbeats (raw heartbeat table) instead of /admin/job-health (aggregate)
  // Phase 0: filter to OE-owned jobs so OE Status does not look like the AIEM terminal.
  const { data: jobHealth, isLoading: jobHealthLoading } = useQuery({
    queryKey: ['job-health-oe'],
    queryFn: () =>
      apiFetch<unknown>('/admin/job-heartbeats')
        .then(normaliseJobHealth)
        .then((jobs) => jobs.filter((j) => isOeJob(j.job_name))),
  });

  const { data: schedulerJobs } = useQuery({
    queryKey: ['scheduler-jobs-oe'],
    queryFn: () =>
      apiFetch<unknown>('/admin/scheduler-jobs')
        .then(normaliseSchedulerJobs)
        .then((jobs) =>
          jobs.filter((j) => isOeJob(j.job_name) || isOeJob(j.job_id)),
        ),
  });

  const { data: reconcileStatus } = useQuery({
    queryKey: ['reconcile-status'],
    queryFn: () => apiFetch<ReconcileStatus>('/options/reconcile'),
  });

  const { data: pipelineCheckpoint } = useQuery({
    queryKey: ['pipeline-checkpoint'],
    queryFn: () =>
      apiFetch<unknown>('/admin/pipeline-checkpoint').then(normalisePipelineCheckpoint),
  });

  const getHealthBadge = (consecutiveFailures: number) => {
    if (consecutiveFailures === 0) {
      return <Badge variant="success">HEALTHY</Badge>;
    } else if (consecutiveFailures < 3) {
      return <Badge variant="warning">DEGRADED</Badge>;
    } else {
      return <Badge variant="destructive">FAILED</Badge>;
    }
  };

  const totalJobs = jobHealth?.length ?? 0;
  const healthyJobs = jobHealth?.filter((j) => j.consecutive_failures === 0).length ?? 0;
  const degradedJobs = totalJobs - healthyJobs;

  if (jobHealthLoading) {
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
        <h1 className="text-3xl font-bold text-foreground tracking-tight">
          System Status
        </h1>
        <p className="text-base text-muted-foreground mt-1.5">
          OE-scoped jobs only · AIEM equity/morning jobs live on /aiem/
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground mb-1">Total Jobs</p>
          <p className="text-3xl font-bold font-mono">{totalJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground mb-1">Jobs Healthy</p>
          <p className="text-3xl font-bold font-mono text-chart-2">{healthyJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground mb-1">Jobs Degraded</p>
          <p className="text-3xl font-bold font-mono text-chart-3">{degradedJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-sm text-muted-foreground mb-1">Last Pipeline Run</p>
          <p className="text-sm font-mono">
            {pipelineCheckpoint
              ? formatDate(pipelineCheckpoint.updated_at)
              : '—'}
          </p>
        </div>
      </div>

      {/* Job Health */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Job Heartbeats</h2>
        </div>
        {jobHealth && jobHealth.length > 0 ? (
          <div className="overflow-auto max-h-96">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job Name</TableHead>
                  <TableHead>Last Heartbeat</TableHead>
                  <TableHead>Consecutive Failures</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobHealth.map((job, idx) => (
                  <TableRow key={idx} data-testid={`row-job-${idx}`}>
                    <TableCell className="font-mono text-sm">
                      {job.job_name}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {formatDate(job.last_heartbeat)}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {job.consecutive_failures}
                    </TableCell>
                    <TableCell>{getHealthBadge(job.consecutive_failures)}</TableCell>
                    <TableCell className="text-sm max-w-xs truncate">
                      {job.last_error ?? '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 job health records found</p>
          </div>
        )}
      </div>

      {/* Scheduler Jobs */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">APScheduler Jobs</h2>
        </div>
        {schedulerJobs && schedulerJobs.length > 0 ? (
          <div className="overflow-auto max-h-96">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job ID</TableHead>
                  <TableHead>Job Name</TableHead>
                  <TableHead>Next Run Time</TableHead>
                  <TableHead>Trigger Type</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {schedulerJobs.map((job, idx) => (
                  <TableRow key={idx} data-testid={`row-scheduler-${idx}`}>
                    <TableCell className="font-mono text-sm">{job.job_id}</TableCell>
                    <TableCell className="font-mono text-sm">
                      {job.job_name}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {formatDate(job.next_run_time)}
                    </TableCell>
                    <TableCell className="text-sm">{job.trigger_type}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 scheduler jobs found</p>
          </div>
        )}
      </div>

      {/* Reconciliation Status */}
      {reconcileStatus && (
        <div className="border border-border rounded-lg bg-card p-4">
          <h2 className="font-semibold mb-3">Reconciliation Status</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <p className="text-sm text-muted-foreground mb-1">Status</p>
              <Badge variant="success">{reconcileStatus.status}</Badge>
            </div>
            <div>
              <p className="text-sm text-muted-foreground mb-1">Last Run</p>
              <p className="font-mono text-sm">{formatDate(reconcileStatus.last_run)}</p>
            </div>
            {reconcileStatus.discrepancies !== undefined && (
              <div>
                <p className="text-sm text-muted-foreground mb-1">Discrepancies</p>
                <p className="font-mono text-sm">{reconcileStatus.discrepancies}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Pipeline Checkpoint */}
      {pipelineCheckpoint && (
        <div className="border border-border rounded-lg bg-card p-4">
          <h2 className="font-semibold mb-3">Pipeline Checkpoint</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <p className="text-sm text-muted-foreground mb-1">Stage</p>
              <p className="font-mono text-sm">{pipelineCheckpoint.stage}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground mb-1">Status</p>
              <Badge variant="success">{pipelineCheckpoint.status}</Badge>
            </div>
            <div>
              <p className="text-sm text-muted-foreground mb-1">Updated At</p>
              <p className="font-mono text-sm">
                {formatDate(pipelineCheckpoint.updated_at)}
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
