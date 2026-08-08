#!/usr/bin/env bash
# Narrow-wing call butterfly — same rules as weekdays asym BT (Mon–Fri, 2y, no stop)
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
  --strategies 24_narrow_wing_call_butterfly \
  --tp 50,75,100,125,150,200,225,250,275,300 \
  --sl 0 \
  --archive-subdir spy-narrow-wing-weekdays

echo "DONE — see docs/verification/spy-narrow-wing-weekdays/RANKING_NOSTOP_TPGRID_*.json"
