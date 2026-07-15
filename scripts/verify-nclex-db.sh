#!/bin/bash
# Post-deployment verification for NCLEX production DB
# Run after every publish: bash scripts/verify-nclex-db.sh
# Exit 0 = pass, exit 1 = fail

BASE="https://nclexai.org"
ADMIN="nclexai-admin-2026"
FAIL=0
EMPTY=()

echo "=== NCLEX DB Verification $(date -u) ==="

# 1. API health
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/questions?limit=1")
[ "$STATUS" = "200" ] && echo "PASS  API reachable (HTTP $STATUS)" \
  || { echo "FAIL  API unreachable (HTTP $STATUS)"; FAIL=1; }

# 2. Total count >= 2000
TOTAL=$(curl -s "$BASE/api/questions" | python3 -c "import sys,json; a=json.load(sys.stdin); print(len(a))" 2>/dev/null || echo 0)
[ "$TOTAL" -ge 2000 ] && echo "PASS  Total questions: $TOTAL (>= 2000)" \
  || { echo "FAIL  Total questions: $TOTAL (expected >= 2000)"; FAIL=1; }

# 3. Per-category check (every tab must have >= 1 question)
check() {
  local CAT="$1"
  local ENC
  ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$CAT")
  local COUNT
  COUNT=$(curl -s "$BASE/api/questions?category=$ENC" \
    | python3 -c "import sys,json; a=json.load(sys.stdin); print(len(a) if isinstance(a,list) else 0)" 2>/dev/null || echo 0)
  if [ "$COUNT" -eq 0 ]; then
    echo "FAIL  Empty: $CAT"
    EMPTY+=("$CAT")
    FAIL=1
  else
    echo "PASS  $CAT: ${COUNT}q"
  fi
}

CATS=(
  "Fundamentals of Nursing" "MedSurg: Cardiac" "MedSurg: Respiratory" "MedSurg: Neurological"
  "MedSurg: Endocrine" "MedSurg: Renal & Urology" "MedSurg: Gastrointestinal"
  "MedSurg: Burns & Integumentary" "MedSurg: Orthopedic" "MedSurg: Chest Tubes"
  "Infectious Disease: Tuberculosis" "Infectious Disease: HIV/AIDS"
  "Pediatric Nursing" "Maternity & OB Nursing" "NICU & Neonatal Care"
  "Psychiatric/Mental Health" "Oncology Nursing" "Seizure & Epilepsy Nursing" "Reproductive System"
  "Assessment: Cardiac" "Assessment: Respiratory" "Assessment: Neurological"
  "Assessment: Gastrointestinal" "Assessment: Genitourinary" "Assessment: Musculoskeletal"
  "GI High-Yield NCLEX" "GU High-Yield NCLEX" "Assessment: Integumentary"
  "Critical Care/ICU" "Fluid & Electrolytes" "ABG Interpretation" "EKG Interpretation"
  "Laboratory & Diagnostics" "Pharmacology: Antidepressants" "Pharmacology: Antipsychotic Drugs"
  "Pharmacology: Cardiac Meds" "Pharmacology: Respiratory Meds" "Pharmacology: Diabetes & Insulin"
  "Pharmacology: Anticoagulation" "Nursing Skills Lab" "Wound Care Management"
  "Dosage Calculations" "IV Therapy Skills" "Lines & Vascular Access" "Central Line Management"
  "Hygiene & ADLs" "Safety & Mobility" "Wound Care & Dressing Changes"
  "Elimination Skills" "Respiratory Care Skills" "GI & Nutrition Skills"
  "Select All That Apply" "NGN - Clinical Judgment" "NGN: Matrix/Grid"
  "NGN: Cloze/Drop-Down" "NGN: Trend/Graphic" "NGN: Highlight" "Drag & Drop Ordering"
  "EKG Strip Recognition" "Hematology-Oncology" "Immune & Rheumatologic Disorders"
  "Sensory Disorders" "Perioperative Care" "Pain Management" "Infection & Inflammation"
  "Shock, Sepsis & Multi-Organ Dysfunction" "End-of-Life & Palliative Care"
  "Emergency & Critical Care" "ACLS: Adult Advanced Cardiac Life Support"
  "PALS: Pediatric Advanced Life Support"
)

for CAT in "${CATS[@]}"; do check "$CAT"; done

echo ""
if [ $FAIL -eq 0 ]; then
  echo "RESULT: ALL CHECKS PASSED ✓"
  exit 0
else
  echo "RESULT: ${#EMPTY[@]} empty categories — run: python3 /tmp/seed_final.py"
  exit 1
fi
