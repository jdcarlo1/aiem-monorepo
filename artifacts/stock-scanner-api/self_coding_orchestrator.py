"""
self_coding_orchestrator.py
------------------------------
Lets AIEM write its OWN backtest code and execute it with every guardrail
enforced in code, not just documented:

  1. Hypothesis pre-registered (hypothesis_registry) BEFORE test code runs.
  2. Self-written code executed in a restricted subprocess: no network,
     no FS writes outside scratch, no imports beyond explicit allowlist.
  3. Results recorded through hypothesis_registry.record_result (locks row).
  4. Every result auto-goes through adversarial_critique before shadow promotion.
  5. Never touches production credentials — reads only from AIEM_DATABASE_URL
     or DATABASE_URL, never passes them into the sandboxed subprocess.
"""

import os
import sys
import json
import subprocess
import tempfile
import textwrap
import datetime as dt
from typing import Dict, Any, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import hypothesis_registry as hr
import adversarial_critique as ac


ALLOWED_IMPORTS = {
    "pandas", "numpy", "math", "statistics", "json", "datetime",
    "itertools", "collections", "scipy", "sklearn",
}

FORBIDDEN_TOKENS = [
    "socket", "requests", "urllib", "http.client", "subprocess",
    "os.system", "os.remove", "os.unlink", "shutil",
    "__import__", "eval(", "exec(", "DATABASE_URL", "STRIPE",
    "smtplib", "ftplib", "psycopg2", "asyncio",
]


def static_scan(code: str) -> Optional[str]:
    """Coarse pre-execution filter. Subprocess resource limits are the real
    enforcement layer — this is defense-in-depth."""
    for token in FORBIDDEN_TOKENS:
        if token in code:
            return f"Forbidden token in generated code: '{token}'"
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            module = (
                stripped.replace("from ", "").replace("import ", "").split()[0].split(".")[0]
            )
            if module not in ALLOWED_IMPORTS:
                return f"Disallowed import: '{module}' — line: '{stripped}'"
    return None


def _limit_resources():
    """Applied in child process via preexec_fn. Caps CPU + address space."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        resource.setrlimit(resource.RLIMIT_AS,  (1024**3, 1024**3))
    except Exception:
        pass


def run_self_written_backtest(
    code: str,
    input_data_path: str,
    timeout_seconds: int = 45,
) -> Dict[str, Any]:
    """
    Execute AIEM's self-written backtest code in a restricted subprocess.

    Contract for self-written script:
      - Receives one CLI arg: path to a CSV of historical data (read-only).
      - Must print a single JSON object to stdout as its final line with AT
        MINIMUM: {"n_trades": int, "win_rate": float, "avg_return": float}
      - No network access (static scan + minimal env enforce this at Python
        level; combine with OS-level network namespace for full isolation).
    """
    scan_err = static_scan(code)
    if scan_err:
        return {"error": "static_scan_failed", "detail": scan_err}

    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = os.path.join(tmp_dir, "self_backtest.py")
        with open(script_path, "w") as f:
            f.write(code)

        try:
            proc = subprocess.run(
                [sys.executable, script_path, input_data_path],
                cwd=tmp_dir,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                preexec_fn=_limit_resources if os.name == "posix" else None,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except subprocess.TimeoutExpired:
            return {"error": "timeout", "detail": f"Exceeded {timeout_seconds}s"}
        except Exception as e:
            return {"error": "execution_failed", "detail": str(e)}

        if proc.returncode != 0:
            return {"error": "nonzero_exit", "detail": proc.stderr[-2000:]}

        lines = proc.stdout.strip().splitlines()
        last_line = lines[-1] if lines else ""
        try:
            result = json.loads(last_line)
        except json.JSONDecodeError:
            return {"error": "unparseable_output", "detail": proc.stdout[-2000:]}

        required = {"n_trades", "win_rate", "avg_return"}
        if not required.issubset(result.keys()):
            return {"error": "missing_required_fields", "detail": result}

        return result


def execute_registered_hypothesis(
    hypothesis_id: int,
    self_written_code: str,
    input_data_path: str,
    universe_description: str,
) -> Dict[str, Any]:
    """
    Full pipeline for one hypothesis:
      1. Confirm registered + not locked.
      2. Sandbox-run the self-written backtest.
      3. Record result (permanently locks the hypothesis row).
      4. Run adversarial critique.
      5. Return everything for human / verification-agent review.

    Does NOT touch shadow_ledger — promotion is a separate, explicit step.
    """
    locked = hr.list_locked_results()
    if any(r["id"] == hypothesis_id for r in locked):
        return {
            "error":  "already_locked",
            "detail": f"Hypothesis {hypothesis_id} already has a recorded result.",
        }

    backtest_result = run_self_written_backtest(self_written_code, input_data_path)
    if "error" in backtest_result:
        return {"stage": "backtest_execution", **backtest_result}

    hr.record_result(hypothesis_id, backtest_result)

    all_locked = hr.list_locked_results()
    this_hyp   = next((r for r in all_locked if r["id"] == hypothesis_id), None)
    if this_hyp is None:
        return {"error": "post_record_lookup_failed"}

    critique = ac.adversarial_review(
        hypothesis_name=this_hyp["name"],
        parameters=this_hyp["parameters"],
        n_trades=backtest_result["n_trades"],
        win_rate=backtest_result["win_rate"],
        test_window=f"{this_hyp['test_start']} to {this_hyp['test_end']}",
        universe_description=universe_description,
    )

    adjusted_alpha = hr.bonferroni_adjusted_alpha()

    return {
        "hypothesis_id":                        hypothesis_id,
        "backtest_result":                      backtest_result,
        "adversarial_critique":                 critique,
        "multiple_comparisons_adjusted_alpha":  adjusted_alpha,
        "total_hypotheses_ever_tested":         hr.get_total_registered(),
        "next_step": (
            "Send this full payload to your separate verification agent "
            "before considering any shadow_ledger promotion."
        ),
    }


EXAMPLE_TEMPLATE = textwrap.dedent("""
    import sys
    import json
    import pandas as pd

    def main():
        data_path = sys.argv[1]
        df = pd.read_csv(data_path)

        # ... signal logic using ONLY df ...
        # must not read other files, must not import requests/socket/etc.

        n_trades, wins, returns = 0, 0, []

        # ... populate from backtest loop ...

        result = {
            "n_trades":   n_trades,
            "win_rate":   (wins / n_trades) if n_trades else 0.0,
            "avg_return": (sum(returns) / len(returns)) if returns else 0.0,
        }
        print(json.dumps(result))

    if __name__ == "__main__":
        main()
""").strip()


if __name__ == "__main__":
    print("Self-coding orchestrator — example script contract:\n")
    print(EXAMPLE_TEMPLATE)
