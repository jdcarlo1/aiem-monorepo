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
import { AlertTriangle } from 'lucide-react';

interface CalibrationHorizon {
  horizon: string;
  raw_brier: number;
  cal_brier: number;
  n_genuine: number;
  contamination_pct: number;
  n_contaminated?: number;
  n_corrected?: number;
}

interface DailyPick {
  ticker: string;
  scan_date: string;
  prob_up_1d: number;
  prob_up_2d: number;
  prob_up_3d: number;
  prob_up_4d: number;
  confidence?: number;
}

interface TrackRecord {
  ticker: string;
  scan_date: string;
  horizon: string;
  predicted_prob: number;
  actual_outcome: number;
  resolved_at: string;
}

// ── Response shape normalisers ─────────────────────────────────────────────────

/**
 * /aiem-probability-engine/calibration returns:
 * {
 *   calibrator_artifacts: { "1d": {raw_brier_test_fold, cal_brier_test_fold, n_test, ...}, ... }
 *   pit_metrics: { genuine: {n_rows_total}, contaminated: {n_rows_total}, corrected: {n_rows_total} }
 * }
 */
function normaliseCalibration(resp: unknown): CalibrationHorizon[] {
  if (Array.isArray(resp)) return resp as CalibrationHorizon[];
  const r = resp as Record<string, unknown>;
  const arts = r?.calibrator_artifacts as Record<string, Record<string, number>> | undefined;
  const pit  = r?.pit_metrics as Record<string, Record<string, number>> | undefined;

  if (!arts) return [];

  const totalContaminated = (pit?.contaminated as Record<string, number>)?.n_rows_total ?? 0;
  const totalGenuine      = (pit?.genuine as Record<string, number>)?.n_rows_total ?? 0;
  const totalCorrected    = (pit?.corrected as Record<string, number>)?.n_rows_total ?? 0;
  const totalRows = totalGenuine + totalContaminated + totalCorrected;

  return Object.entries(arts).map(([horizon, art]) => ({
    horizon,
    raw_brier: art.raw_brier_test_fold ?? 0,
    cal_brier: art.cal_brier_test_fold ?? 0,
    n_genuine: (art.n_test ?? totalGenuine),
    contamination_pct: totalRows > 0 ? (totalContaminated / totalRows) * 100 : 0,
    n_contaminated: totalContaminated,
    n_corrected: totalCorrected,
  }));
}

/**
 * /aiem-probability-engine/daily-picks returns:
 * { pick_date: "YYYY-MM-DD", picks: [{ticker, prob_up_1d, prob_up_2d, ...}, ...] }
 */
function normaliseDailyPicks(resp: unknown): DailyPick[] {
  if (Array.isArray(resp)) return resp as DailyPick[];
  const r = resp as Record<string, unknown>;
  const picks = r?.picks;
  const scanDate = (r?.pick_date as string) ?? '';
  if (!Array.isArray(picks)) return [];
  return (picks as Record<string, unknown>[]).map((p) => ({
    ticker:      (p.ticker as string) ?? '',
    scan_date:   scanDate,
    prob_up_1d:  (p.prob_up_1d as number) ?? 0,
    prob_up_2d:  (p.prob_up_2d as number) ?? 0,
    prob_up_3d:  (p.prob_up_3d as number) ?? 0,
    prob_up_4d:  (p.prob_up_4d as number) ?? 0,
    confidence:  p.confidence as number | undefined,
  }));
}

/**
 * /aiem-probability-engine/track-record returns:
 * { rows: [{ticker, signal_date, prob_up_1d, correct_1d, outcome_label_1d, ...}], ... }
 * Each row contains 4 horizons; we expand into individual records (settled only).
 */
function normaliseTrackRecord(resp: unknown): TrackRecord[] {
  if (Array.isArray(resp)) return resp as TrackRecord[];
  const r = resp as Record<string, unknown>;
  const rows = r?.rows;
  if (!Array.isArray(rows)) return [];

  const result: TrackRecord[] = [];
  for (const row of rows as Record<string, unknown>[]) {
    for (const h of [1, 2, 3, 4] as const) {
      const correct = row[`correct_${h}d`];
      if (correct === null || correct === undefined) continue; // not settled yet
      result.push({
        ticker:         (row.ticker as string) ?? '',
        scan_date:      (row.signal_date as string) ?? '',
        horizon:        `${h}d`,
        predicted_prob: (row[`prob_up_${h}d`] as number) ?? 0,
        actual_outcome: correct === true ? 1 : 0,
        resolved_at:    (row.signal_date as string) ?? '',
      });
    }
  }
  return result;
}

export default function CalibrationPage() {
  const { apiFetch } = useApi();

  const { data: calibration, isLoading: calibrationLoading } = useQuery({
    queryKey: ['calibration'],
    queryFn: () =>
      apiFetch<unknown>('/aiem-probability-engine/calibration').then(normaliseCalibration),
  });

  const { data: dailyPicks } = useQuery({
    queryKey: ['daily-picks'],
    queryFn: () =>
      apiFetch<unknown>('/aiem-probability-engine/daily-picks').then(normaliseDailyPicks),
  });

  const { data: trackRecord } = useQuery({
    queryKey: ['track-record'],
    queryFn: () =>
      apiFetch<unknown>('/aiem-probability-engine/track-record').then(normaliseTrackRecord),
  });

  if (calibrationLoading) {
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
          Probability Calibration
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Model accuracy and Platt scaling effectiveness
        </p>
      </div>

      {/* Calibration Warning */}
      <div className="bg-destructive/10 border border-destructive/30 rounded-lg p-4 flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-destructive flex-shrink-0 mt-0.5" />
        <div>
          <p className="font-semibold text-destructive text-sm">
            Platt Scaling Currently Degraded
          </p>
          <p className="text-xs text-destructive/90 mt-1">
            Raw Brier scores are the honest accuracy metric. All 4 horizons show
            calibrated Brier &gt; raw Brier, indicating scaling worsens predictions.
            Use raw scores for all decisions.
          </p>
        </div>
      </div>

      {/* Horizon Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {calibration &&
          calibration.map((horizon) => {
            const isDegraded = horizon.cal_brier > horizon.raw_brier;
            return (
              <div
                key={horizon.horizon}
                className="bg-card border border-border rounded-lg p-5"
                data-testid={`card-horizon-${horizon.horizon}`}
              >
                <div className="flex items-center justify-between mb-4">
                  <h3 className="font-semibold text-foreground">
                    {horizon.horizon.toUpperCase()}
                  </h3>
                  {isDegraded && (
                    <Badge variant="warning">DEGRADED</Badge>
                  )}
                </div>
                <div className="space-y-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">
                      Model Accuracy (Raw Brier)
                    </p>
                    <p className="text-2xl font-bold font-mono text-primary">
                      {horizon.raw_brier.toFixed(4)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">
                      After Platt Scaling
                    </p>
                    <p
                      className={`text-lg font-mono ${
                        isDegraded ? 'text-destructive' : 'text-foreground'
                      }`}
                    >
                      {horizon.cal_brier.toFixed(4)}
                    </p>
                  </div>
                  <div className="pt-2 border-t border-border">
                    <p className="text-xs text-muted-foreground mb-1">
                      Data Quality
                    </p>
                    <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                      <div>
                        <span className="text-muted-foreground">Genuine:</span>{' '}
                        <span className="text-foreground">{horizon.n_genuine}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Contam:</span>{' '}
                        <span className="text-chart-3">
                          {horizon.contamination_pct.toFixed(1)}%
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
      </div>

      {/* Latest Daily Picks */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Latest Daily Picks</h2>
        </div>
        {dailyPicks && dailyPicks.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Scan Date</TableHead>
                <TableHead>Prob Up 1D</TableHead>
                <TableHead>Prob Up 2D</TableHead>
                <TableHead>Prob Up 3D</TableHead>
                <TableHead>Prob Up 4D</TableHead>
                <TableHead>Confidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {dailyPicks.map((pick, idx) => (
                <TableRow key={idx} data-testid={`row-pick-${idx}`}>
                  <TableCell className="font-semibold font-mono">
                    {pick.ticker}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {pick.scan_date}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(pick.prob_up_1d * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(pick.prob_up_2d * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(pick.prob_up_3d * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(pick.prob_up_4d * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {pick.confidence !== undefined
                      ? `${(pick.confidence * 100).toFixed(0)}%`
                      : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 daily picks found</p>
          </div>
        )}
      </div>

      {/* Track Record */}
      <div className="border border-border rounded-lg bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="font-semibold">Outcome Resolution (Track Record)</h2>
        </div>
        {trackRecord && trackRecord.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ticker</TableHead>
                <TableHead>Scan Date</TableHead>
                <TableHead>Horizon</TableHead>
                <TableHead>Predicted Prob</TableHead>
                <TableHead>Actual Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trackRecord.map((record, idx) => (
                <TableRow key={idx} data-testid={`row-track-${idx}`}>
                  <TableCell className="font-semibold font-mono">
                    {record.ticker}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {record.scan_date}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {record.horizon}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(record.predicted_prob * 100).toFixed(1)}%
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={record.actual_outcome === 1 ? 'success' : 'destructive'}
                    >
                      {record.actual_outcome === 1 ? 'UP' : 'DOWN'}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">0 track record entries found</p>
          </div>
        )}
      </div>
    </>
  );
}
