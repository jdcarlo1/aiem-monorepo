"""
Unit-level proof for AIEM Verification Directive 2026-07-09, Part 1.3.2/1.3.3.

This test does NOT reimplement the enforcement logic. It extracts the literal
source lines of the sizing-gate block from main.py (via `sed`, same technique
used throughout the audit session) and `exec()`s that exact text inside a
minimal harness, so the assertions are against the real committed code, not
a paraphrase of it.

Run: python3 artifacts/stock-scanner-api/tests/test_sizing_gate_enforcement.py
"""
import subprocess
import sys
import os

MAIN_PY = os.path.join(os.path.dirname(__file__), "..", "main.py")

START_MARKER = "_notional  = 1000.0"
END_MARKER = 'if _trade_type == "CALL_OPTION":'


def _extract_block() -> str:
    with open(MAIN_PY) as f:
        lines = f.readlines()
    start = next(i for i, l in enumerate(lines) if START_MARKER in l)
    end = next(i for i, l in enumerate(lines) if END_MARKER in l and i > start)
    block_lines = lines[start:end]
    # dedent (block is at 16 spaces of indentation inside main.py)
    dedented = []
    for l in block_lines:
        if l.strip() == "":
            dedented.append("\n")
        else:
            assert l.startswith(" " * 16), f"unexpected indent: {l!r}"
            dedented.append(l[16:])
    return "".join(dedented)


class _FakeSizer:
    """Stands in for the real aiem_position_sizing module import (_pos_sizer)."""

    def __init__(self, gate_result, notional=0.0, raise_exc=None):
        self._gate_result = gate_result
        self._notional = notional
        self._raise_exc = raise_exc

    def compute_position_size(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return {
            "gate_result": self._gate_result,
            "calculated_notional": self._notional,
            "calculated_stop_price": 95.0 if self._gate_result == "APPROVED" else None,
            "stop_basis": "unit_test_stop",
            "risk_pct_used": 1.0,
            "gate_detail": f"unit_test gate={self._gate_result}",
        }


def run_case(name, pos_sizer, expect_insert_reached, expect_notional=None):
    block_src = _extract_block()
    reached = {"insert": False, "notional": None, "gate": None}

    ns = {
        "_pos_sizer": pos_sizer,
        "_t": "ZZTEST",
        "_fill_price": 100.0,
        "pick": {"trade_type": "STOCK", "source": "unusual_calls", "score": 10.0},
        "_reached": reached,
    }

    # Wrap the literal extracted block in a one-iteration for-loop so its
    # `continue` statement is legal and observable, exactly like the real
    # `for pick in picks:` loop in _aiem_paper_execute_today.
    harness = (
        "for _iter in [0]:\n"
        + "".join("    " + l if l.strip() else "\n" for l in block_src.splitlines(keepends=True))
        + "    _reached['insert'] = True\n"
        + "    _reached['notional'] = _notional\n"
        + "    _reached['gate'] = _sizing_gate\n"
    )

    exec(compile(harness, "<extracted_sizing_block>", "exec"), ns)

    status = "PASS" if reached["insert"] == expect_insert_reached else "FAIL"
    print(f"[{status}] case={name} gate_result_seen={reached['gate']!r} "
          f"insert_reached={reached['insert']} notional={reached['notional']}")
    assert reached["insert"] == expect_insert_reached, (
        f"{name}: expected insert_reached={expect_insert_reached}, got {reached['insert']}"
    )
    if expect_notional is not None:
        assert reached["notional"] == expect_notional, (
            f"{name}: expected notional={expect_notional}, got {reached['notional']}"
        )


if __name__ == "__main__":
    print("=== Extracted real source block from main.py ===")
    print(_extract_block())
    print("=== Running cases against the literal extracted code ===")

    # 1. Blocked cases -- must NOT reach insert path
    run_case("NO_STOP_DEFINED (trade 179's actual real-world gate)",
              _FakeSizer("NO_STOP_DEFINED"), expect_insert_reached=False)
    run_case("CONVICTION_BELOW_MIN",
              _FakeSizer("CONVICTION_BELOW_MIN"), expect_insert_reached=False)
    run_case("STOP_UNDEFINED",
              _FakeSizer("STOP_UNDEFINED"), expect_insert_reached=False)
    run_case("POSITION_TOO_SMALL",
              _FakeSizer("POSITION_TOO_SMALL"), expect_insert_reached=False)
    run_case("kill_switch",
              _FakeSizer("kill_switch"), expect_insert_reached=False)
    run_case("max_positions",
              _FakeSizer("max_positions"), expect_insert_reached=False)
    run_case("max_sector_positions",
              _FakeSizer("max_sector_positions"), expect_insert_reached=False)
    run_case("daily_loss",
              _FakeSizer("daily_loss"), expect_insert_reached=False)
    run_case("unknown/future gate value (fail-closed allowlist check)",
              _FakeSizer("SOME_FUTURE_GATE_NOBODY_HAS_SEEN"), expect_insert_reached=False)
    run_case("sizer raises exception -> SIZING_ERROR (fail-closed, not $1000 default)",
              _FakeSizer("irrelevant", raise_exc=RuntimeError("simulated DB timeout")),
              expect_insert_reached=False)

    # 2. Allowed pass-through cases -- MUST reach insert path
    run_case("APPROVED with real calculated_notional (not $1000 default)",
              _FakeSizer("APPROVED", notional=842.17),
              expect_insert_reached=True, expect_notional=842.17)
    run_case("_pos_sizer is None (module not deployed) -> PARAMS_NOT_CONFIRMED default",
              None, expect_insert_reached=True, expect_notional=1000.0)

    print("\nALL CASES PASSED")
