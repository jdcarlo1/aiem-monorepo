#!/usr/bin/env bash
# AIEM / ops runner — SPY asymmetric BT A/B: Monday vs any weekday
# Requires: POLYGON_API_KEY (or POLYGON_KEY)
#
# Baseline archive already exists: docs/verification/spy-asymmetric-bt/
# This run writes weekdays to: docs/verification/spy-asymmetric-bt-weekdays/
set -euo pipefail
cd "$(dirname "$0")"
export POLYGON_API_KEY="${POLYGON_API_KEY:-${POLYGON_KEY:-}}"
if [[ -z "${POLYGON_API_KEY}" ]]; then
  echo "FATAL: POLYGON_API_KEY / POLYGON_KEY not set" >&2
  exit 2
fi

# Full TP grid, no stop — same as original Monday ranking (23 × 6 = 138 combos)
python3 spy_asymmetric_bt.py \
  --years 2 \
  --entry weekdays \
  --strategies all \
  --tp 50,75,100,125,150,200 \
  --sl 0 \
  --archive-subdir spy-asymmetric-bt-weekdays

echo "DONE — compare RANKING_NOSTOP_TPGRID_*.json under"
echo "  docs/verification/spy-asymmetric-bt/           (Monday)"
echo "  docs/verification/spy-asymmetric-bt-weekdays/  (any weekday)"
