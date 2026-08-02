#!/usr/bin/env python3
"""Field-level comparison of scheduler inline dict literals vs their test copies."""
import ast, pathlib, sys

SCHED = pathlib.Path("artifacts/stock-scanner-api/aiem_options_scheduler.py")
TEST  = pathlib.Path("artifacts/stock-scanner-api/tests/test_e2e_pipeline_replay.py")
TARGETS = [("_build_call_data", 1920, 1947), ("_build_put_data", 1948, 1975)]

def _sched_dict(tree, lo, hi):
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict) and lo <= n.lineno <= hi:
            return n
    return None

def _base_fields(tree, before):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and n.lineno < before:
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "base_fields" and isinstance(n.value, ast.Dict):
                    if best is None or n.lineno > best.lineno:
                        best = n
    return best.value if best else None

def _keys(dnode, tree, lineno):
    out = {}
    for k, v in zip(dnode.keys, dnode.values):
        if k is None:
            if isinstance(v, ast.Name) and v.id == "base_fields":
                bf = _base_fields(tree, lineno)
                if bf is None:
                    out["**base_fields<UNRESOLVED>"] = None
                else:
                    out.update(_keys(bf, tree, bf.lineno))
            else:
                out[f"**{ast.unparse(v)}"] = None
        else:
            out[ast.literal_eval(k)] = ast.dump(v)
    return out

def _copy_dict(tree, fname):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            for m in ast.walk(n):
                if isinstance(m, ast.Return) and isinstance(m.value, ast.Dict):
                    return m.value, n.lineno
    return None, None

st, tt = ast.parse(SCHED.read_text()), ast.parse(TEST.read_text())
fails = 0
for name, lo, hi in TARGETS:
    cd = _sched_dict(st, lo, hi)
    pd, pl = _copy_dict(tt, name)
    if cd is None or pd is None:
        print(f"FAIL {name}: canonical_found={cd is not None} copy_found={pd is not None}")
        fails += 1
        continue
    ck, pk = _keys(cd, st, lo), _keys(pd, tt, pl)
    missing = sorted(set(ck) - set(pk))
    extra   = sorted(set(pk) - set(ck))
    changed = sorted(k for k in set(ck) & set(pk) if ck[k] != pk[k])
    print(f"--- {name} ---")
    print(f"  canonical_fields ({len(ck)}): {sorted(ck)}")
    print(f"  copy_fields      ({len(pk)}): {sorted(pk)}")
    print(f"  missing_from_copy: {missing}")
    print(f"  extra_in_copy    : {extra}")
    print(f"  value_expr_diff  : {changed}")
    if missing or extra or changed:
        print(f"  FAIL {name}")
        fails += 1
    else:
        print(f"  PASS {name}: {len(ck)} fields identical")
print(f"RESULT: {'PASS' if fails == 0 else 'FAIL'} ({fails} failing of {len(TARGETS)})")
sys.exit(0 if fails == 0 else 1)
