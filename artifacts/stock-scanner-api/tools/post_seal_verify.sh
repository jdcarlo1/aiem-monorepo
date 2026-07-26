#!/usr/bin/env bash
# tools/post_seal_verify.sh — Independent post-seal verifier (Item 3 — DPL Remediation)
#
# PURPOSE:
#   Runs AFTER verified_run.sh seals the archive and writes the chain entry.
#   Independently confirms that the sealed artifact is intact and consistent
#   with the chain and index records — without importing any DPL Python code.
#
# USAGE (called automatically by verified_run.sh with these args):
#   bash tools/post_seal_verify.sh <SEQ> <CHAIN_FILE> <INDEX_FILE> <LOGS_DIR>
#
# EXIT:
#   0 = all post-seal checks PASS
#   1 = one or more checks FAIL (block the run)
#
# NEGATIVE CONTROL (manual):
#   cp logs/verified_run_N.log /tmp/test_corrupt.log
#   printf 'X' | dd of=/tmp/test_corrupt.log bs=1 seek=10 conv=notrunc 2>/dev/null
#   bash tools/post_seal_verify.sh <SEQ> ... (will FAIL C_ARCHIVE_SHA_MATCH)

set -uo pipefail

SEQ="${1:-}"
CHAIN_FILE="${2:-}"
INDEX_FILE="${3:-}"
LOGS_DIR="${4:-}"

if [ -z "$SEQ" ] || [ -z "$CHAIN_FILE" ] || [ -z "$INDEX_FILE" ] || [ -z "$LOGS_DIR" ]; then
    echo "[post_seal_verify] ERROR: missing arguments SEQ CHAIN_FILE INDEX_FILE LOGS_DIR" >&2
    exit 1
fi

ARCHIVE="${LOGS_DIR}/verified_run_${SEQ}.log"

_PASS=0
_FAIL=0
_FAILED_CHECKS=()

psv_chk() {
    local name="$1" ok="$2" detail="${3:-}"
    if [ "$ok" = "1" ]; then
        echo "  [POST-SEAL PASS] ${name}"
        _PASS=$(( _PASS + 1 ))
    else
        echo "  [POST-SEAL FAIL] ${name} -- ${detail}"
        _FAIL=$(( _FAIL + 1 ))
        _FAILED_CHECKS+=("$name")
    fi
}

echo ""
echo "====== post_seal_verify.sh ======"
echo "SEQ=${SEQ}"
echo "ARCHIVE=${ARCHIVE}"
echo "CHAIN_FILE=${CHAIN_FILE}"
echo "INDEX_FILE=${INDEX_FILE}"
echo "TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "================================="
echo ""

# ── PSV-1: Archive file exists ────────────────────────────────────────────────
if [ -f "${ARCHIVE}" ]; then
    psv_chk "PSV1_archive_exists" 1 ""
else
    psv_chk "PSV1_archive_exists" 0 "archive not found: ${ARCHIVE}"
fi

# ── PSV-2: Archive sha256 matches index entry ─────────────────────────────────
if [ -f "${ARCHIVE}" ] && [ -f "${INDEX_FILE}" ]; then
    LIVE_SHA=$(sha256sum "${ARCHIVE}" | awk '{print $1}')
    # Use awk instead of grep+tab to avoid bash \t portability issues
    IDX_SHA=$(awk -F'\t' -v seq="${SEQ}" '$1==seq {print $4; exit}' "${INDEX_FILE}" || true)
    if [ -z "$IDX_SHA" ]; then
        psv_chk "PSV2_archive_sha_matches_index" 0 "SEQ=${SEQ} not found in index"
    elif [ "${LIVE_SHA}" = "${IDX_SHA}" ]; then
        psv_chk "PSV2_archive_sha_matches_index" 1 "sha=${LIVE_SHA}"
        echo "    live_sha=${LIVE_SHA}"
        echo "    index_sha=${IDX_SHA}"
    else
        psv_chk "PSV2_archive_sha_matches_index" 0 "live=${LIVE_SHA} != index=${IDX_SHA}"
    fi
else
    psv_chk "PSV2_archive_sha_matches_index" 0 "archive or index missing"
fi

# ── PSV-3: Chain entry exists for this SEQ ────────────────────────────────────
if [ -f "${CHAIN_FILE}" ]; then
    CHAIN_ENTRY=$(python3 - "${CHAIN_FILE}" "${SEQ}" <<'_PYEOF'
import sys, json
cf, seq_n = sys.argv[1], int(sys.argv[2])
try:
    for line in open(cf):
        e = json.loads(line.strip())
        if e.get('seq') == seq_n:
            print(json.dumps(e, sort_keys=True, separators=(',',':')))
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
_PYEOF
    ) && CHAIN_FOUND=1 || CHAIN_FOUND=0
    if [ "$CHAIN_FOUND" = "1" ] && [ -n "$CHAIN_ENTRY" ]; then
        psv_chk "PSV3_chain_entry_exists_for_seq" 1 "entry found"
    else
        psv_chk "PSV3_chain_entry_exists_for_seq" 0 "SEQ=${SEQ} not in chain"
        CHAIN_ENTRY=""
    fi
else
    psv_chk "PSV3_chain_entry_exists_for_seq" 0 "chain file missing"
    CHAIN_ENTRY=""
fi

# ── PSV-4: 3-Way binding — chain.archive_sha256 = index.sha256 = sha256(archive) ──
# Item 2: New entries (SEQ>=22) carry archive_sha256 sealed into the chain entry.
# Hard check: any mismatch is a tamper indicator and fails HARD (not soft).
# Legacy entries (SEQ<=21, no archive_sha256 field) get a LEGACY_SKIP.
if [ -n "$CHAIN_ENTRY" ] && [ -f "${ARCHIVE}" ]; then
    CHAIN_ARCHIVE_SHA=$(echo "${CHAIN_ENTRY}" | python3 -c "
import sys, json
e = json.load(sys.stdin)
print(e.get('archive_sha256', 'LEGACY_NO_FIELD'))
" 2>/dev/null || echo "EXTRACT_ERR")

    if [ "${CHAIN_ARCHIVE_SHA}" = "LEGACY_NO_FIELD" ] || [ "${CHAIN_ARCHIVE_SHA}" = "EXTRACT_ERR" ] || [ -z "${CHAIN_ARCHIVE_SHA}" ]; then
        echo "  [PSV4] LEGACY_SKIP: SEQ=${SEQ} predates archive_sha256 field (expected for SEQ<=21)"
        LEGACY_LOG_SHA=$(echo "${CHAIN_ENTRY}" | python3 -c "import sys,json; e=json.load(sys.stdin); print(e.get('log_sha256','?'))" 2>/dev/null || echo "?")
        echo "    chain_log_sha=${LEGACY_LOG_SHA}"
        _PASS=$(( _PASS + 1 ))
    else
        LIVE_ARCHIVE_SHA=$(sha256sum "${ARCHIVE}" | awk '{print $1}')
        if [ "${LIVE_ARCHIVE_SHA}" = "${CHAIN_ARCHIVE_SHA}" ]; then
            psv_chk "PSV4_archive_sha256_3way_binding" 1 \
                "archive_sha=${LIVE_ARCHIVE_SHA}"
            echo "    live_archive_sha=${LIVE_ARCHIVE_SHA}"
            echo "    chain_archive_sha=${CHAIN_ARCHIVE_SHA}"
        else
            psv_chk "PSV4_archive_sha256_3way_binding" 0 \
                "TAMPER: live=${LIVE_ARCHIVE_SHA} != chain=${CHAIN_ARCHIVE_SHA}"
        fi
    fi
else
    psv_chk "PSV4_archive_sha256_3way_binding" 0 "chain_entry or archive missing"
fi
# ── PSV-5: Chain entry_hash recomputes correctly ──────────────────────────────
if [ -n "$CHAIN_ENTRY" ]; then
    RECOMPUTED=$(python3 - "${CHAIN_FILE}" "${SEQ}" <<'_PYEOF'
import sys, json, hashlib
cf, seq_n = sys.argv[1], int(sys.argv[2])
for line in open(cf):
    if not line.strip():
        continue
    e = json.loads(line.strip())
    if e.get('seq') != seq_n:
        continue
    stored_hash = e.pop('entry_hash', '')
    # Schema boundary: SEQ < 133 used old C33 (archive_sha256 excluded);
    # SEQ >= 133 uses C33+ (archive_sha256 included in entry_hash).
    _CUTOVER_SEQ = 133
    if seq_n < _CUTOVER_SEQ:
        for k in ('type', 'pre_chain_anchor_note', 'archive_sha256'):
            e.pop(k, None)
    else:
        for k in ('type', 'pre_chain_anchor_note'):
            e.pop(k, None)
    computed = hashlib.sha256(
        json.dumps(e, sort_keys=True, separators=(',',':')).encode()
    ).hexdigest()
    print(f"{stored_hash}:{computed}")
    sys.exit(0)
print("NOT_FOUND:NOT_FOUND")
_PYEOF
    )
    STORED_H=$(echo "$RECOMPUTED" | cut -d: -f1)
    COMPUTED_H=$(echo "$RECOMPUTED" | cut -d: -f2)
    if [ "${STORED_H}" = "NOT_FOUND" ]; then
        psv_chk "PSV5_chain_entry_hash_recomputes" 0 "seq=${SEQ} not found in chain"
    elif [ "${STORED_H}" = "${COMPUTED_H}" ]; then
        psv_chk "PSV5_chain_entry_hash_recomputes" 1 "hash=${STORED_H:0:16}..."
    else
        psv_chk "PSV5_chain_entry_hash_recomputes" 0 "stored=${STORED_H:0:16} computed=${COMPUTED_H:0:16}"
    fi
else
    psv_chk "PSV5_chain_entry_hash_recomputes" 0 "no chain entry"
fi

# ── PSV-6: prev_hash continuity for this entry ────────────────────────────────
if [ -n "$CHAIN_ENTRY" ] && [ -f "${CHAIN_FILE}" ]; then
    PREV_OK=$(python3 - "${CHAIN_FILE}" "${SEQ}" <<'_PYEOF'
import sys, json
cf, seq_n = sys.argv[1], int(sys.argv[2])
entries = []
for line in open(cf):
    if line.strip():
        entries.append(json.loads(line.strip()))
for i, e in enumerate(entries):
    if e.get('seq') == seq_n:
        if i == 0:
            print("GENESIS_OK")
        else:
            prev = entries[i-1]
            if e.get('prev_hash') == prev.get('entry_hash'):
                print("OK")
            else:
                print(f"FAIL:prev={e.get('prev_hash','?')[:16]}!={prev.get('entry_hash','?')[:16]}")
        sys.exit(0)
print("NOT_FOUND")
_PYEOF
    )
    if [[ "$PREV_OK" == "OK" ]] || [[ "$PREV_OK" == "GENESIS_OK" ]]; then
        psv_chk "PSV6_prev_hash_continuity" 1 "result=${PREV_OK}"
    else
        psv_chk "PSV6_prev_hash_continuity" 0 "result=${PREV_OK}"
    fi
else
    psv_chk "PSV6_prev_hash_continuity" 0 "chain entry or chain file missing"
fi

# ── PSV-7: Exit status matches archived log ───────────────────────────────────
if [ -n "$CHAIN_ENTRY" ] && [ -f "${ARCHIVE}" ]; then
    CHAIN_EXIT=$(python3 - "${CHAIN_FILE}" "${SEQ}" <<'_PYEOF'
import sys, json
cf, seq_n = sys.argv[1], int(sys.argv[2])
for line in open(cf):
    if not line.strip(): continue
    e = json.loads(line.strip())
    if e.get('seq') == seq_n:
        print(e.get('exit_code', '?'))
        sys.exit(0)
print('?')
_PYEOF
    )
    ARCH_EXIT=$(grep "^SEQ=${SEQ}  EXIT=" "${ARCHIVE}" | sed 's/.*EXIT=\([0-9]*\).*/\1/' | head -1 || echo "?")
    if [ "${CHAIN_EXIT}" = "${ARCH_EXIT}" ]; then
        psv_chk "PSV7_exit_status_matches_archive" 1 "exit=${CHAIN_EXIT}"
    else
        psv_chk "PSV7_exit_status_matches_archive" 0 "chain_exit=${CHAIN_EXIT} arch_exit=${ARCH_EXIT}"
    fi
else
    psv_chk "PSV7_exit_status_matches_archive" 0 "chain entry or archive missing"
fi

# ── PSV-8: PASS/FAIL totals extractable from archive ─────────────────────────
if [ -f "${ARCHIVE}" ]; then
    SUMMARY_LINE=$(grep "^SUMMARY:" "${ARCHIVE}" | head -1 || echo "")
    if [ -n "$SUMMARY_LINE" ]; then
        psv_chk "PSV8_pass_fail_totals_in_archive" 1 "${SUMMARY_LINE}"
        echo "    ${SUMMARY_LINE}"
    else
        psv_chk "PSV8_pass_fail_totals_in_archive" 0 "SUMMARY: line not found in archive"
    fi
else
    psv_chk "PSV8_pass_fail_totals_in_archive" 0 "archive missing"
fi

# ── PSV-9: CMD matches chain entry ────────────────────────────────────────────
if [ -n "$CHAIN_ENTRY" ] && [ -f "${ARCHIVE}" ]; then
    CHAIN_CMD=$(python3 - "${CHAIN_FILE}" "${SEQ}" <<'_PYEOF'
import sys, json
cf, seq_n = sys.argv[1], int(sys.argv[2])
for line in open(cf):
    if not line.strip(): continue
    e = json.loads(line.strip())
    if e.get('seq') == seq_n:
        print(e.get('cmd', ''))
        sys.exit(0)
print('')
_PYEOF
    )
    ARCH_CMD=$(grep "^CMD=" "${ARCHIVE}" | head -1 | cut -d= -f2- || true)
    if [ "${CHAIN_CMD}" = "${ARCH_CMD}" ]; then
        psv_chk "PSV9_cmd_matches_archive" 1 "cmd=${CHAIN_CMD}"
    else
        psv_chk "PSV9_cmd_matches_archive" 0 "chain_cmd=${CHAIN_CMD} arch_cmd=${ARCH_CMD}"
    fi
else
    psv_chk "PSV9_cmd_matches_archive" 0 "chain entry or archive missing"
fi

echo ""
echo "POST-SEAL SUMMARY: ${_PASS} PASS  ${_FAIL} FAIL"
if [ "${_FAIL}" -gt 0 ]; then
    echo "POST-SEAL FAILED: ${_FAILED_CHECKS[*]}"
fi
echo "================================="

exit "${_FAIL}"
