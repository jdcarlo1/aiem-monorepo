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

export default function StatusPage() {
  const { apiFetch } = useApi();

  const { data: jobHealth, isLoading: jobHealthLoading } = useQuery({
    queryKey: ['job-health'],
    queryFn: () => apiFetch<JobHealth[]>('/admin/job-health'),
  });

  const { data: schedulerJobs } = useQuery({
    queryKey: ['scheduler-jobs'],
    queryFn: () => apiFetch<SchedulerJob[]>('/admin/scheduler-jobs'),
  });

  const { data: reconcileStatus } = useQuery({
    queryKey: ['reconcile-status'],
    queryFn: () => apiFetch<ReconcileStatus>('/options/reconcile'),
  });

  const { data: pipelineCheckpoint } = useQuery({
    queryKey: ['pipeline-checkpoint'],
    queryFn: () => apiFetch<PipelineCheckpoint>('/admin/pipeline-checkpoint'),
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
      <div>
        <h1 className="text-2xl font-bold text-foreground">System Status</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Job health, scheduler state, and pipeline checkpoints
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground mb-1">Total Jobs</p>
          <p className="text-2xl font-bold font-mono">{totalJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground mb-1">Jobs Healthy</p>
          <p className="text-2xl font-bold font-mono text-chart-2">{healthyJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground mb-1">Jobs Degraded</p>
          <p className="text-2xl font-bold font-mono text-chart-3">{degradedJobs}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground mb-1">Last Pipeline Run</p>
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
                  <TableCell className="font-mono text-xs">
                    {formatDate(job.last_heartbeat)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {job.consecutive_failures}
                  </TableCell>
                  <TableCell>{getHealthBadge(job.consecutive_failures)}</TableCell>
                  <TableCell className="text-xs max-w-xs truncate">
                    {job.last_error ?? '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
                  <TableCell className="font-mono text-xs">{job.job_id}</TableCell>
                  <TableCell className="font-mono text-sm">
                    {job.job_name}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatDate(job.next_run_time)}
                  </TableCell>
                  <TableCell className="text-xs">{job.trigger_type}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-muted-foreground mb-1">Status</p>
              <Badge variant="success">{reconcileStatus.status}</Badge>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Last Run</p>
              <p className="font-mono text-sm">{formatDate(reconcileStatus.last_run)}</p>
            </div>
            {reconcileStatus.discrepancies !== undefined && (
              <div>
                <p className="text-xs text-muted-foreground mb-1">Discrepancies</p>
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
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-muted-foreground mb-1">Stage</p>
              <p className="font-mono text-sm">{pipelineCheckpoint.stage}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Status</p>
              <Badge variant="success">{pipelineCheckpoint.status}</Badge>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Updated At</p>
              <p className="font-mono text-sm">
                {formatDate(pipelineCheckpoint.updated_at)}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
