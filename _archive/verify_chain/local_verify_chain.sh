#!/usr/bin/env bash
# Evidence Chain Protocol — verify_chain.sh
# Reads d12_evidence_chain.log and re-computes sha256 for each entry.
# Prints PASS or FAIL per entry. Exits 1 if any FAIL.

CHAIN_LOG="/home/runner/workspace/.local/d12_evidence_chain.log"
ERRORS=0
ENTRIES=0
LABEL=""
CMD=""
TS=""
EXPECTED_HASH=""
IN_BODY=0
BODY_LINES=""

while IFS= read -r line; do
    case "$line" in
        "---ENTRY---")
            LABEL=""
            CMD=""
            TS=""
            EXPECTED_HASH=""
            IN_BODY=0
            BODY_LINES=""
            ;;
        timestamp=*)
            TS="${line#timestamp=}"
            ;;
        label=*)
            LABEL="${line#label=}"
            ;;
        cmd=*)
            CMD="${line#cmd=}"
            ;;
        sha256=*)
            EXPECTED_HASH="${line#sha256=}"
            ;;
        "output_begin")
            IN_BODY=1
            BODY_LINES=""
            ;;
        "output_end")
            IN_BODY=0
            ACTUAL_HASH=$(printf '%s' "$BODY_LINES" | sha256sum | awk '{print $1}')
            ENTRIES=$((ENTRIES + 1))
            if [ "$ACTUAL_HASH" = "$EXPECTED_HASH" ]; then
                echo "PASS  [$TS] [$LABEL] sha256=$ACTUAL_HASH"
            else
                echo "FAIL  [$TS] [$LABEL] expected=$EXPECTED_HASH got=$ACTUAL_HASH"
                ERRORS=$((ERRORS + 1))
            fi
            ;;
        *)
            if [ "$IN_BODY" = "1" ]; then
                if [ -z "$BODY_LINES" ]; then
                    BODY_LINES="$line"
                else
                    BODY_LINES="$BODY_LINES
$line"
                fi
            fi
            ;;
    esac
done < "$CHAIN_LOG"

echo ""
echo "=== CHAIN SUMMARY: $ENTRIES entries, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ] && exit 0 || exit 1
