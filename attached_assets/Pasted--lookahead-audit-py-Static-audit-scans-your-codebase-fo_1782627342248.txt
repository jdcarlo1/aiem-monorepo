"""
lookahead_audit.py

Static audit: scans your codebase for the two concrete lookahead-risk
patterns identified in the StockScanner AI point-in-time review:

  1. Calls to yf.download(...) without an explicit auto_adjust=False, inside
     files whose names suggest they're used for backtesting/training/
     historical analysis (yfinance defaults auto_adjust to True, which
     retroactively rewrites historical prices using future splits/dividends).

  2. Any use of `.info` (yfinance's always-current-day fundamentals) inside
     those same backtest/training files.

This does NOT prove a leak exists at every flagged line - it surfaces
candidate locations for a human (or Replit) to judge: "is this computing a
feature for a PAST date (risk), or running a LIVE scan (fine)?" Live scans
are fine as-is; historical feature computation is the actual risk.

USAGE
  python lookahead_audit.py /path/to/stock-scanner-api
"""

import ast
import sys
from pathlib import Path

BACKTEST_FILE_HINTS = (
    "backtest", "retrain", "training", "evaluation",
    "walk_forward", "historical", "model_training",
)


def is_backtest_relevant(filepath: Path) -> bool:
    name = filepath.stem.lower()
    return any(hint in name for hint in BACKTEST_FILE_HINTS)


class LookaheadVisitor(ast.NodeVisitor):
    def __init__(self):
        self.findings = []

    def visit_Call(self, node):
        func_name = self._get_func_name(node.func)

        if func_name in ("yf.download", "download", "yfinance.download"):
            has_explicit_auto_adjust_false = any(
                kw.arg == "auto_adjust"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if not has_explicit_auto_adjust_false:
                self.findings.append((
                    node.lineno,
                    "auto_adjust is True or unspecified (yfinance defaults "
                    "to True) - retroactive split/dividend adjustment risk "
                    "if this row represents a past date",
                ))

        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr == "info":
            self.findings.append((
                node.lineno,
                ".info accessed - yfinance fundamentals are always "
                "current-day, never historical",
            ))
        self.generic_visit(node)

    def _get_func_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._get_func_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None


def audit_file(filepath: Path):
    try:
        source = filepath.read_text(errors="ignore")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [(0, f"could not parse file: {e}")]

    visitor = LookaheadVisitor()
    visitor.visit(tree)
    return visitor.findings


def main(root_dir: str):
    root = Path(root_dir)
    py_files = sorted(root.rglob("*.py"))

    total_findings = 0
    backtest_findings = 0
    for f in py_files:
        relevant = is_backtest_relevant(f)
        findings = audit_file(f)
        if findings:
            tag = "[BACKTEST-RELEVANT]" if relevant else "[other - lower priority]"
            for lineno, msg in findings:
                print(f"{tag} {f}:{lineno} - {msg}")
                total_findings += 1
                if relevant:
                    backtest_findings += 1

    print(f"\n{total_findings} total candidate locations found "
          f"({backtest_findings} in backtest/training-relevant files).")
    print("Each flagged line needs a judgment call: feature computed for a "
          "PAST date (real risk - fix with point_in_time_guard.py) or a "
          "live/current-day scan (no change needed).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
