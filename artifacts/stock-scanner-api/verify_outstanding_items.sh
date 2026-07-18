#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "=== ITEM 1: git diff 47680fc 2cb97e5 --stat ==="
git --no-optional-locks diff 47680fc 2cb97e5 --stat -- aiem_options_scheduler.py aiem_options_pipeline.py

echo ""
echo "=== ITEM 1: git diff 47680fc 2cb97e5 -- aiem_options_scheduler.py ==="
git --no-optional-locks diff 47680fc 2cb97e5 -- aiem_options_scheduler.py

echo ""
echo "=== ITEM 1: git diff 47680fc 2cb97e5 -- aiem_options_pipeline.py ==="
git --no-optional-locks diff 47680fc 2cb97e5 -- aiem_options_pipeline.py

echo ""
echo "=== ITEM 2: exact SQL queries + live results ==="
python3 - << 'PYEOF'
import os, psycopg2
db = os.environ["DATABASE_URL"]
tables = [
    "oe_root_cause_records","oe_attribution_runs","oe_indicator_attribution",
    "oe_interaction_hypotheses","oe_interaction_results","oe_strategy_scorecards",
    "oe_knowledge_base","oe_kb_confidence_log","oe_regime_performance",
]
with psycopg2.connect(db, connect_timeout=4) as conn, conn.cursor() as cur:
    for t in tables:
        sql = f"SELECT COUNT(*) FROM {t};"
        cur.execute(sql)
        print(f"QUERY: {sql}  RESULT: {cur.fetchone()[0]}")
PYEOF

echo ""
echo "=== ITEM 3a: grep -n _ROOT_CAUSE_CATEGORIES ==="
grep -n "_ROOT_CAUSE_CATEGORIES" aiem_options_phase3.py

echo ""
echo "=== ITEM 3a: _ROOT_CAUSE_CATEGORIES block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_ROOT_CAUSE_CATEGORIES\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3b: grep -n _OUTCOME_TYPES ==="
grep -n "_OUTCOME_TYPES" aiem_options_phase3.py

echo ""
echo "=== ITEM 3b: _OUTCOME_TYPES block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_OUTCOME_TYPES\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3c: grep -n _DECISION_QUALITY ==="
grep -n "_DECISION_QUALITY" aiem_options_phase3.py

echo ""
echo "=== ITEM 3c: _DECISION_QUALITY block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_DECISION_QUALITY\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3d: grep -n _ATTRIBUTION_METHODS ==="
grep -n "_ATTRIBUTION_METHODS" aiem_options_phase3.py

echo ""
echo "=== ITEM 3d: _ATTRIBUTION_METHODS block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_ATTRIBUTION_METHODS\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3e: grep -n _SEGMENT_TYPES ==="
grep -n "_SEGMENT_TYPES" aiem_options_phase3.py

echo ""
echo "=== ITEM 3e: _SEGMENT_TYPES block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_SEGMENT_TYPES\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3f: grep -n _KB_TYPES ==="
grep -n "_KB_TYPES" aiem_options_phase3.py

echo ""
echo "=== ITEM 3f: _KB_TYPES block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_KB_TYPES\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 3g: grep -n _REGIME_TYPES ==="
grep -n "_REGIME_TYPES" aiem_options_phase3.py

echo ""
echo "=== ITEM 3g: _REGIME_TYPES block ==="
python3 - << 'PYEOF'
import re
lines = open("aiem_options_phase3.py").readlines()
start = next(i for i,l in enumerate(lines) if re.match(r"^_REGIME_TYPES\b", l))
depth = 0
for i in range(start, len(lines)):
    print(f"{i+1}:{lines[i]}", end="")
    for ch in lines[i]:
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
    if i > start and depth <= 0:
        break
PYEOF

echo ""
echo "=== ITEM 4a: grep -n aiem_closed_loop ==="
grep -n "aiem_closed_loop" aiem_options_phase3.py || echo "(no matches)"

echo ""
echo "=== ITEM 4b: grep -n aiem_d3 ==="
grep -n "aiem_d3" aiem_options_phase3.py || echo "(no matches)"

echo ""
echo "=== ITEM 4c: grep -n aiem_d2 ==="
grep -n "aiem_d2" aiem_options_phase3.py || echo "(no matches)"

echo ""
echo "=== ITEM 4d: grep -n d3_governance ==="
grep -n "d3_governance" aiem_options_phase3.py || echo "(no matches)"

echo ""
echo "=== ITEM 4e: grep -n aiem_paper_trades ==="
grep -n "aiem_paper_trades" aiem_options_phase3.py || echo "(no matches)"

echo ""
echo "=== ITEM 4f: grep -n aiem_learning_loop ==="
grep -n "aiem_learning_loop" aiem_options_phase3.py || echo "(no matches)"
