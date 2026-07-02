"""
aiem_isolation_guard.py — Runtime + static isolation guard for the 24/7 free
research loop (Loop B: indicator grid battery + continuous scheduler).

This loop must run 100% independently of OpenAI - no import, no client
construction, no cost - even though OTHER AIEM features (the paid weekly
GPT agent, mkt_generate_hypotheses, mkt_invent_indicator, specialist
council, bull/bear debate) are allowed to call OpenAI. This module enforces
that isolation two ways:

  1. RUNTIME GUARD - `isolated_research_scope()` context manager.
     Wrap the loop's entry point with it. If code running inside the scope
     (in THIS thread) imports `openai`/`anthropic`, or constructs a fresh
     `openai.OpenAI(...)` client, it raises AIEMIsolationViolation
     immediately and loudly instead of silently succeeding. Thread-local, so
     it never interferes with other AIEM features legitimately calling
     OpenAI concurrently in other threads (Flask request threads, the
     weekly agent, etc).

  2. STATIC GUARD - `verify_source_isolation()` starts from the known entry
     points, walks main.py's AST to find every module-level function they
     transitively call (so it survives future edits without needing a
     manually maintained allowlist), and scans the whole closure's source
     for forbidden tokens (openai, anthropic, gpt-, OPENAI_API_KEY, the
     specific bridge function _get_openai_client, etc).

Run standalone for a one-off check:
    python3 aiem_isolation_guard.py
Exits 0 with a PASSED line if clean, exits 1 with a FAILED breakdown if not.

This is also called automatically at process startup (see
_mkt_start_continuous_loop in main.py) - the continuous loop refuses to
start if the static check fails, so a future edit that accidentally
introduces an OpenAI dependency into this loop is caught immediately
instead of silently shipping.
"""

import ast
import builtins
import contextlib
import copy
import os
import sys
import threading

ENTRY_POINTS = [
    "_mkt_indicator_grid_battery",
    "_mkt_continuous_research_loop",
    "_mkt_research_loop_allowed",
    "_mkt_start_continuous_loop",
]

FORBIDDEN_TOKENS = (
    "openai",
    "anthropic",
    "gpt-",
    "chat.completions",
    "responses.create",
    "OPENAI_API_KEY",
    "AI_INTEGRATIONS_OPENAI",
    "_get_openai_client",
    "_mkt_tool_generate_hypotheses",
    "_mkt_tool_invent_indicator",
)


class AIEMIsolationViolation(RuntimeError):
    """Raised the instant guarded code tries to touch OpenAI/Anthropic."""


# ── Layer 1: runtime guard ──────────────────────────────────────────────────

_GUARD_ACTIVE = threading.local()
_FORBIDDEN_MODULES = ("openai", "anthropic")
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if getattr(_GUARD_ACTIVE, "on", False) and name.split(".")[0] in _FORBIDDEN_MODULES:
        msg = (f"[aiem_isolation_guard] BLOCKED attempted import of '{name}' "
               f"inside the isolated 24/7 free research loop scope")
        print(msg, file=sys.stderr, flush=True)
        raise AIEMIsolationViolation(msg)
    return _real_import(name, globals, locals, fromlist, level)


@contextlib.contextmanager
def isolated_research_scope():
    """Thread-local runtime guard around the free research loop's execution."""
    _GUARD_ACTIVE.on = True
    builtins.__import__ = _guarded_import
    _patched_cls, _orig_init = None, None
    try:
        if "openai" in sys.modules:
            try:
                _oa = sys.modules["openai"]
                _orig_init = _oa.OpenAI.__init__

                def _blocked_init(self, *a, **k):
                    msg = ("[aiem_isolation_guard] BLOCKED construction of an "
                           "openai.OpenAI() client inside the isolated research loop scope")
                    print(msg, file=sys.stderr, flush=True)
                    raise AIEMIsolationViolation(msg)

                _oa.OpenAI.__init__ = _blocked_init
                _patched_cls = _oa.OpenAI
            except Exception:
                _patched_cls = None
        yield
    finally:
        builtins.__import__ = _real_import
        _GUARD_ACTIVE.on = False
        if _patched_cls is not None and _orig_init is not None:
            _patched_cls.__init__ = _orig_init


# ── Layer 2: static call-graph closure check ────────────────────────────────

def _code_only_source(node):
    """Return the function's source with its docstring stripped, via
    ast.unparse() on a copy with the docstring Expr removed. Comments are
    already absent from the AST entirely (unparse never sees them), so this
    yields real code only - imports, calls, string-literal arguments like
    os.environ.get("OPENAI_API_KEY") - with none of the explanatory prose
    that would otherwise trip false positives (e.g. a docstring describing
    what this very guard blocks)."""
    node_copy = copy.deepcopy(node)
    if (node_copy.body and isinstance(node_copy.body[0], ast.Expr)
            and isinstance(node_copy.body[0].value, ast.Constant)
            and isinstance(node_copy.body[0].value.value, str)):
        node_copy.body = node_copy.body[1:] or [ast.Pass()]
    try:
        return ast.unparse(node_copy)
    except Exception:
        return ""


def _parse_functions(pyfile):
    """Return {func_name: (code_only_source, {names it calls via ast.Name})}.

    NOTE: deliberately does NOT use ast.get_source_segment() - on a file this
    size (1000+ function defs) it re-splitlines() the whole file on every
    single call, which is O(n * calls) and takes minutes."""
    src = open(pyfile, "r", encoding="utf-8").read()
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            seg = _code_only_source(node)
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    called.add(sub.func.id)
            # last definition wins if a name is redefined (matches Python semantics)
            out[node.name] = (seg, called)
    return out


def verify_source_isolation(pyfile, entry_points=None, forbidden_tokens=None, quiet=False):
    """Walk the transitive call-graph closure of entry_points (module-level
    functions only - object-method calls like cur.execute() can't collide
    with a def name and are correctly ignored) and scan every function's
    source for forbidden tokens."""
    entry_points = entry_points or ENTRY_POINTS
    forbidden_tokens = forbidden_tokens or FORBIDDEN_TOKENS
    all_funcs = _parse_functions(pyfile)

    missing = [n for n in entry_points if n not in all_funcs]
    violations = []
    if missing:
        violations.append(
            f"entry point(s) not found in {pyfile} (renamed/removed?): {missing}"
        )

    visited = set()
    queue = list(entry_points)
    while queue:
        name = queue.pop()
        if name in visited or name not in all_funcs:
            continue
        visited.add(name)
        _src, called = all_funcs[name]
        for c in called:
            if c in all_funcs and c not in visited:
                queue.append(c)

    for name in sorted(visited):
        src, _ = all_funcs[name]
        low = src.lower()
        for tok in forbidden_tokens:
            if tok.lower() in low:
                violations.append(f"{name}: forbidden token '{tok}' found in source")

    ok = not violations
    if not quiet:
        if ok:
            print(f"[aiem_isolation_guard] STATIC CHECK PASSED: "
                  f"{len(visited)} functions in the closure ({sorted(visited)}), "
                  f"zero forbidden tokens.")
        else:
            print("[aiem_isolation_guard] STATIC CHECK FAILED:")
            for v in violations:
                print(f"  - {v}")
    return ok, sorted(visited), violations


if __name__ == "__main__":
    _pyfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    _ok, _closure, _violations = verify_source_isolation(_pyfile)
    sys.exit(0 if _ok else 1)
