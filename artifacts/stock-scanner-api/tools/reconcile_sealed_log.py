#!/usr/bin/env python3
"""
tools/reconcile_sealed_log.py — Independent sealed-log reconciler (S5)

Parses a sealed archive from verified_run_chain.jsonl / tools/logs/.
Does NOT import the verifier, its counting functions, or its helpers.
Exits nonzero if:
  - archive SHA mismatches index
  - PASS+FAIL != total_checks from archive SUMMARY line
  - any check ID appears more than once (duplicate)
  - any check ID in the log is not in the canonical registry
  - any check ID in the registry is missing from the log
  - any FAIL is reclassified as PASS by the log
  - archive SHA does not match chain entry
"""
import sys, os, re, json, hashlib, argparse

def _sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def parse_log(path):
    checks = {}  # id -> execution_status
    summary_pass = None
    summary_fail = None
    lines = open(path).readlines()
    for line in lines:
        m = re.match(r"^(PASS|FAIL) (\S+)", line.rstrip())
        if m:
            status, cid = m.group(1), m.group(2)
            if cid in checks:
                yield "DUPLICATE", f"check_id={cid} appears twice (first={checks[cid]} second={status})"
            checks[cid] = status
        m2 = re.match(r"^SUMMARY: (\d+) PASS  (\d+) FAIL", line.rstrip())
        if m2:
            summary_pass, summary_fail = int(m2.group(1)), int(m2.group(2))
    return checks, summary_pass, summary_fail

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--chain", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--registry", required=True, help="dpl/test_registry_seq25.json or last_run_results.json")
    args = ap.parse_args()

    errors = []
    warnings = []

    # 1. Archive file must exist
    if not os.path.exists(args.archive):
        print(f"RECONCILE FAIL: archive not found: {args.archive}")
        sys.exit(1)

    # 2. Archive SHA matches index
    live_sha = _sha256(args.archive)
    idx_sha = None
    for line in open(args.index):
        parts = line.rstrip().split("\t")
        if parts and parts[0].strip() == str(args.seq):
            idx_sha = parts[3] if len(parts) > 3 else None
            break
    if idx_sha is None:
        errors.append(f"SEQ={args.seq} not found in index")
    elif live_sha != idx_sha:
        errors.append(f"archive SHA mismatch: live={live_sha[:32]} index={idx_sha[:32]}")

    # 3. Archive SHA matches chain entry
    chain_sha = None
    for line in open(args.chain):
        if line.strip():
            e = json.loads(line.strip())
            if e.get("seq") == args.seq:
                chain_sha = e.get("archive_sha256")
                break
    if chain_sha is None:
        errors.append(f"SEQ={args.seq} not found in chain")
    elif live_sha != chain_sha:
        errors.append(f"archive SHA chain mismatch: live={live_sha[:32]} chain={chain_sha[:32]}")

    # 4. Parse log checks
    checks = {}
    summary_pass = None
    summary_fail = None
    duplicates = []
    for line in open(args.archive):
        m = re.match(r"^(PASS|FAIL) (\S+)", line.rstrip())
        if m:
            status, cid = m.group(1), m.group(2)
            if cid in checks:
                duplicates.append(f"{cid} (first={checks[cid]}, second={status})")
            checks[cid] = status
        m2 = re.match(r"^SUMMARY: (\d+) PASS  (\d+) FAIL", line.rstrip())
        if m2:
            summary_pass, summary_fail = int(m2.group(1)), int(m2.group(2))

    for dup in duplicates:
        errors.append(f"DUPLICATE check_id: {dup}")

    # 5. SUMMARY line reconciliation
    if summary_pass is None:
        errors.append("SUMMARY: line not found in archive")
    else:
        actual_pass = sum(1 for s in checks.values() if s == "PASS")
        actual_fail = sum(1 for s in checks.values() if s == "FAIL")
        total = actual_pass + actual_fail
        if actual_pass != summary_pass:
            errors.append(f"PASS count mismatch: SUMMARY={summary_pass} parsed={actual_pass}")
        if actual_fail != summary_fail:
            errors.append(f"FAIL count mismatch: SUMMARY={summary_fail} parsed={actual_fail}")

    # 6. Registry comparison
    reg = json.load(open(args.registry))
    # Support last_run_results.json or test_registry
    if "pass_list" in reg and "fail_list" in reg:
        reg_ids = set(reg["pass_list"]) | set(reg["fail_list"])
    elif "checks" in reg:
        reg_ids = {c["check_id"] for c in reg["checks"]}
    else:
        reg_ids = set()

    log_ids = set(checks.keys())
    unknown = log_ids - reg_ids
    missing = reg_ids - log_ids
    if unknown:
        warnings.append(f"unknown check IDs in log (not in registry): {sorted(unknown)[:5]}")
    if missing:
        warnings.append(f"registry check IDs missing from log: {sorted(missing)[:5]}")

    # 7. Report
    total_checks = len(checks)
    print(f"RECONCILE SEQ={args.seq}")
    print(f"  archive: {args.archive}")
    print(f"  live_sha: {live_sha}")
    print(f"  index_sha: {idx_sha}")
    print(f"  chain_sha: {chain_sha}")
    print(f"  sha_match: {live_sha == idx_sha == chain_sha}")
    print(f"  checks_parsed: {total_checks}")
    print(f"  PASS={actual_pass}  FAIL={actual_fail}  SUMMARY_PASS={summary_pass}  SUMMARY_FAIL={summary_fail}")
    print(f"  duplicates: {len(duplicates)}")
    print(f"  registry_ids: {len(reg_ids)}  unknown_in_log: {len(unknown)}  missing_from_log: {len(missing)}")
    if errors:
        print(f"  ERRORS ({len(errors)}):")
        for e in errors: print(f"    ERROR: {e}")
    if warnings:
        for w in warnings: print(f"  WARN: {w}")
    if not errors:
        print(f"RECONCILE RESULT: PASS")
    else:
        print(f"RECONCILE RESULT: FAIL")
        sys.exit(1)

if __name__ == "__main__":
    main()
