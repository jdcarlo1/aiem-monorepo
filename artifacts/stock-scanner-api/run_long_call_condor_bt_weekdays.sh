#!/usr/bin/env bash
# AIEM / Replit Shell — 2y weekdays BT for ONE strategy: long call condor only.
# Requires: POLYGON_API_KEY (or POLYGON_KEY)
#
# Already archived at TP300 in spy-top6-sl-compare-weekdays/.
# This expands the full no-stop TP grid for call condor alone.
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
  --strategies 08_long_call_condor \
  --tp 50,75,100,125,150,200,225,250,275,300 \
  --sl 0 \
  --archive-subdir spy-long-call-condor-weekdays

echo "DONE — see docs/verification/spy-long-call-condor-weekdays/"
echo "  RANKING_NOSTOP_TPGRID_*.json"
