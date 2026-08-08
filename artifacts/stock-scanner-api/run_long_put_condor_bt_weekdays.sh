#!/usr/bin/env bash
# AIEM / Replit Shell — 2y weekdays BT for ONE strategy: long put condor only.
# Requires: POLYGON_API_KEY (or POLYGON_KEY)
set -euo pipefail
cd "$(dirname "$0")"
export POLYGON_API_KEY="${POLYGON_API_KEY:-${POLYGON_KEY:-}}"
if [[ -z "${POLYGON_API_KEY}" ]]; then
  echo "FATAL: POLYGON_API_KEY / POLYGON_KEY not set" >&2
  exit 2
fi

python3 spy_asymmetric_bt.py \
  --years 2 \
  --entry weekdays \
  --strategies 09_long_put_condor \
  --tp 50,75,100,125,150,200,225,250,275,300 \
  --sl 0 \
  --archive-subdir spy-long-put-condor-weekdays

echo "DONE — see docs/verification/spy-long-put-condor-weekdays/"
echo "  RANKING_NOSTOP_TPGRID_*.json"
