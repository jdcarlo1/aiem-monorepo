#!/usr/bin/env bash
# Evidence Chain Protocol — verified_run.sh
# Usage: verified_run.sh <label> <command...>
# Runs command, sha256s stdout+stderr, appends to d12_evidence_chain.log
# Prints raw output to stdout as well.

LABEL="$1"
shift
CMD="$*"
CHAIN_LOG="/home/runner/workspace/.local/d12_evidence_chain.log"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OUTPUT=$(eval "$CMD" 2>&1)
HASH=$(printf '%s' "$OUTPUT" | sha256sum | awk '{print $1}')

cat >> "$CHAIN_LOG" << CHAINENTRY
---ENTRY---
timestamp=$TIMESTAMP
label=$LABEL
cmd=$CMD
sha256=$HASH
output_begin
$OUTPUT
output_end
CHAINENTRY

printf '%s\n' "$OUTPUT"
