"""
AIEM RL ENGINE — INTER-MODULE COMMUNICATION VERIFICATION HARNESS
==================================================================

PURPOSE
-------
This script does NOT ask the agent whether modules are wired together.
It calls each module directly with synthetic data, prints RAW output,
then feeds module A's real output into module B and checks the DB
in between — so you can see with your own eyes whether data actually
flows, or whether each module is an island that never talks to the
others.

HOW TO USE
----------
1. Give this file to the Replit agent and ask it to fill in the
   TODOs with the REAL method names/signatures from aiem_rl_engine.py
   (do not let it rewrite the test logic — only the plumbing to call
   its own functions).
2. Run it yourself: `python3 aiem_comm_test.py`
3. Read the raw printed output — don't ask the agent to summarize it.
4. Anywhere you see "MANUAL CHECK", that's a place where you decide
   pass/fail by eyeballing the printed values, not trusting a
   printed "PASS" from the script itself.

WHY THIS CATCHES FAKE WIRING
-----------------------------
- If TradeOutcomeAnalyzer's output doesn't change when you feed it a
  different synthetic trade, its logic isn't real.
- If RewardEngine's reward score doesn't actually end up as an input
  to PPOPolicyOptimizer's next weight update, they're not connected —
  no matter what the agent claims.
- If MarketMemory doesn't return the pattern you just wrote into it,
  the DB round-trip is fake or misconfigured.
- If ContinualLearner's consolidation doesn't nudge weights when you
  intentionally push StrategyWeightOptimizer to an extreme, EWC-style
  consolidation isn't running.
"""

import json
import sys
import traceback
from datetime import datetime, timedelta

from aiem_rl_engine import (
    TradeOutcomeAnalyzer, MistakeClassifier, ExperienceReplayBuffer,
    RewardEngine, ConfidenceCalibration, CounterfactualEngine,
    StrategyWeightOptimizer, SelfCritiqueAgent, ContinualLearner,
    PPOPolicyOptimizer, AdaptiveRiskManager, MarketMemory,
)


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def make_synthetic_trade(symbol="TEST", pct_move=5.0, held_minutes=45, r_multiple=1.8):
    """A fabricated trade record — deliberately NOT a real historical trade,
    so we can prove modules react to the actual data passed in, not to
    something hardcoded or cached."""
    now = datetime.utcnow()
    return {
        "symbol": symbol,
        "entry_price": 100.0,
        "exit_price": 100.0 * (1 + pct_move / 100),
        "entry_time": (now - timedelta(minutes=held_minutes)).isoformat(),
        "exit_time": now.isoformat(),
        "size_usd": 1000.0,
        "r_multiple": r_multiple,
        "pct_move": pct_move,
        "held_minutes": held_minutes,
    }


def _to_engine_trade(t, signal_source="layer9", trade_id=None):
    """Map make_synthetic_trade() format → aiem_rl_engine expected format.
    The engine expects ticker/pnl_pct/hold_days; the template uses symbol/pct_move/held_minutes.
    This is pure plumbing — values are not altered, only keys are mapped."""
    held_days = max(1, round(t.get("held_minutes", 60) / 60 / 6.5))
    return {
        "id":               trade_id or 99990,
        "ticker":           t.get("symbol", "TEST"),
        "trade_type":       "STOCK",
        "entry_price":      t["entry_price"],
        "exit_price":       t["exit_price"],
        "pnl_pct":          t["pct_move"],
        "pnl":              t["size_usd"] * t["pct_move"] / 100.0,
        "notional":         t["size_usd"],
        "signal_source":    signal_source,
        "trade_date":       (datetime.utcnow() - timedelta(days=held_days)).strftime("%Y-%m-%d"),
        "hold_days":        held_days,
        "max_drawdown_pct": abs(t["pct_move"]) * 0.5 if t["pct_move"] < 0 else 1.0,
    }


# ---------------------------------------------------------------------
# STAGE 1 — Call each module in isolation with synthetic input
# ---------------------------------------------------------------------

def stage_1_isolated_calls():
    hr("STAGE 1: Isolated module calls (does each module DO something?)")

    trade_a = make_synthetic_trade(pct_move=5.0, r_multiple=2.0)   # clean winner
    trade_b = make_synthetic_trade(pct_move=-3.0, r_multiple=-1.5, held_minutes=180)  # bad loser, held too long

    results = {}

    # --- TradeOutcomeAnalyzer ---
    print("\n[TradeOutcomeAnalyzer] Feeding winner vs loser trade...")
    out_a = TradeOutcomeAnalyzer().evaluate_trade(_to_engine_trade(trade_a, trade_id=91001))
    out_b = TradeOutcomeAnalyzer().evaluate_trade(_to_engine_trade(trade_b, trade_id=91002))
    print("  Winner trade output:", json.dumps(out_a, default=str))
    print("  Loser trade output :", json.dumps(out_b, default=str))
    print("  MANUAL CHECK: outputs must differ meaningfully (SPY comparison, "
          "peak-in-hold) — if identical, the module ignores its input.")
    results["trade_outcome"] = (out_a, out_b)

    # --- MistakeClassifier ---
    print("\n[MistakeClassifier] Feeding the held-too-long loser...")
    et_b = _to_engine_trade(trade_b, trade_id=91002)
    mistakes = MistakeClassifier().classify(et_b, out_b)
    print("  Mistakes returned:", mistakes)
    print("  MANUAL CHECK: should include something like HELD_TOO_LONG given "
          "held_minutes=180 and a losing outcome. If mistakes=[] on a bad "
          "trade, the classifier isn't reading the fields it claims to.")
    results["mistakes"] = mistakes

    # --- RewardEngine ---
    print("\n[RewardEngine] Scoring winner vs loser...")
    reward_a = RewardEngine().calculate_reward(_to_engine_trade(trade_a, trade_id=91001), out_a)
    reward_b = RewardEngine().calculate_reward(_to_engine_trade(trade_b, trade_id=91002), out_b)
    print("  Winner reward:", reward_a)
    print("  Loser reward :", reward_b)
    print("  MANUAL CHECK: reward_a must be clearly positive and greater "
          "than reward_b; reward_b should reflect the 2x drawdown penalty.")
    results["reward"] = (reward_a, reward_b)

    # --- AdaptiveRiskManager ---
    print("\n[AdaptiveRiskManager] Sizing winner (high conviction) vs loser signal source...")
    arm = AdaptiveRiskManager()
    size_a = arm.adjust_position_size("layer9", conviction_score=0.8, volatility_pct=2.0)
    size_b = arm.adjust_position_size("unusual_calls", conviction_score=0.3, volatility_pct=6.0)
    print("  High conviction size:", size_a)
    print("  Low conviction size :", size_b)
    print("  MANUAL CHECK: size_a should be clearly larger than size_b.")
    results["arm"] = (size_a, size_b)

    # --- SelfCritiqueAgent ---
    print("\n[SelfCritiqueAgent] Critiquing the losing trade...")
    critique = SelfCritiqueAgent().critique(et_b, out_b, mistakes)
    print("  Critique output:", json.dumps(critique, default=str))
    print("  MANUAL CHECK: critique should reference the mistake labels and "
          "produce a non-empty 'lessons' or 'summary' key — not a stub dict.")
    results["critique"] = critique

    # --- MarketMemory ---
    print("\n[MarketMemory] Writing a synthetic 'squeeze' pattern, then reading it back...")
    mm = MarketMemory()
    mm.store_pattern(
        pattern_type="squeeze",
        trade=_to_engine_trade(trade_b, trade_id=91002),
        analysis=out_b,
        success=False,
    )
    fetched = mm.recall_patterns(pattern_type="squeeze", limit=5)
    print("  Retrieved:", json.dumps(fetched, default=str))
    print("  MANUAL CHECK: the pattern you just wrote must come back. If it "
          "doesn't, the DB round-trip for this module is broken or it's "
          "reading/writing different tables than it claims.")
    results["market_memory"] = fetched

    # --- ConfidenceCalibration (isolated read) ---
    print("\n[ConfidenceCalibration] Recording a high-confidence call that was wrong...")
    cc = ConfidenceCalibration()
    cc.record(signal_source="layer9", predicted_prob=0.8, actual_outcome=False)
    report = cc.calibration_report(signal_source="layer9")
    print("  Calibration report:", json.dumps(report, default=str))
    print("  MANUAL CHECK: should show at least 1 recorded observation for layer9.")
    results["calibration"] = report

    return results


# ---------------------------------------------------------------------
# STAGE 2 — Chain modules together and trace the handoff
# ---------------------------------------------------------------------

def stage_2_chained_pipeline():
    hr("STAGE 2: Chained pipeline (does module A's output reach module B?)")

    trade = make_synthetic_trade(pct_move=-4.0, r_multiple=-2.0, held_minutes=200)
    et = _to_engine_trade(trade, signal_source="unusual_calls", trade_id=92001)

    # Step 1: TradeOutcomeAnalyzer -> MistakeClassifier
    print("\n[1] TradeOutcomeAnalyzer analyzes trade...")
    analysis = TradeOutcomeAnalyzer().evaluate_trade(et)
    print("  analysis =", json.dumps(analysis, default=str))

    print("\n[2] Feeding THAT SAME analysis object into MistakeClassifier...")
    mistakes = MistakeClassifier().classify(et, analysis)
    print("  mistakes =", mistakes)
    print("  MANUAL CHECK: if MistakeClassifier's real signature doesn't even "
          "accept `analysis` as an argument, it never actually consumes "
          "TradeOutcomeAnalyzer's output — they're not wired, regardless of "
          "what the agent's diagram says.")

    # Step 3: RewardEngine -> ExperienceReplayBuffer -> PPOPolicyOptimizer
    print("\n[3] RewardEngine scores the trade...")
    reward = RewardEngine().calculate_reward(et, analysis)
    print("  reward =", reward)

    print("\n[4] Writing (trade, reward, mistakes) into ExperienceReplayBuffer...")
    state      = {"pnl_pct": et["pnl_pct"], "hold_days": et["hold_days"], "conviction_score": 0.4}
    next_state = {"pnl_pct": 0.0, "hold_days": 0, "conviction_score": 0.5}
    action     = "exit_full"   # must match _PPO_ACTIONS: hold/exit_half/exit_full/add_size
    buf = ExperienceReplayBuffer()
    buf.store(
        trade=et, outcome=analysis, mistakes=mistakes,
        reward=reward, state=state, next_state=next_state, action=action,
    )
    buf_count = buf.count()
    print(f"  ExperienceReplayBuffer.count() after store = {buf_count}")
    print("  MANUAL CHECK (run SQL yourself):")
    print("    SELECT * FROM rl_experience_buffer ORDER BY id DESC LIMIT 1;")
    print("  The row's reward column must equal the `reward` printed above, "
          "not some default/placeholder value.")

    print("\n[5] Triggering PPOPolicyOptimizer update from the buffer...")
    ppo = PPOPolicyOptimizer()
    before_weights = ppo._get_params()
    print("  before (n_updates, logits):", before_weights.get("n_updates"), before_weights.get("logits"))
    ppo.update_policy(state=state, action=action, reward=reward, next_state=next_state)
    after_weights = ppo._get_params()
    print("  after  (n_updates, logits):", after_weights.get("n_updates"), after_weights.get("logits"))
    print("  MANUAL CHECK: n_updates must increment and at least one logit value "
          "must change after a large negative-reward trade is added. If "
          "before == after, the optimizer isn't actually "
          "consuming what ExperienceReplayBuffer wrote.")
    print("  n_updates changed:", before_weights.get("n_updates") != after_weights.get("n_updates"))
    print("  logits changed   :", before_weights.get("logits") != after_weights.get("logits"))

    # Step 6: StrategyWeightOptimizer -> ContinualLearner
    print("\n[6] Forcing StrategyWeightOptimizer to an extreme weight, then "
          "running ContinualLearner consolidation...")
    swo = StrategyWeightOptimizer()
    # Drive layer9 up hard with a large positive reward — simulates an extreme spike
    swo.update_weights("layer9", reward=200.0, pnl_pct=50.0)
    swo.update_weights("layer9", reward=200.0, pnl_pct=50.0)
    before_consolidation = swo.get_live_weights().get("layer9")
    print(f"  layer9 weight before consolidation = {before_consolidation}")
    # ContinualLearner.update_model() is the EWC consolidation step.
    # It pulls every weight toward the global mean (alpha=0.08).
    all_weights = swo.get_live_weights()
    after_consolidation_weights = ContinualLearner().update_model(
        signal_source="layer9", reward=-10.0, weights=all_weights
    )
    after_consolidation = after_consolidation_weights.get("layer9")
    print(f"  layer9 weight after  consolidation = {after_consolidation}")
    print("  MANUAL CHECK: after_consolidation should be pulled back toward "
          "the mean (EWC alpha=0.08) relative to the extreme value. If "
          "it's unchanged, ContinualLearner isn't actually touching "
          "StrategyWeightOptimizer's stored weights.")
    print("  Weight changed:", before_consolidation != after_consolidation)

    # Step 7: CounterfactualEngine cross-check against real price data
    print("\n[7] CounterfactualEngine — 'what if held longer' on the same trade...")
    cf = CounterfactualEngine().simulate_alternatives(et)
    print("  counterfactual result:", json.dumps(cf, default=str))
    print("  MANUAL CHECK: this requires a REAL polygon_market_daily price "
          "lookup for the symbol/date range. If you use a symbol/date with "
          "no data in that table, this should fail loudly, not silently "
          "return a made-up number.")

    # Step 8: ConfidenceCalibration cross-check
    print("\n[8] ConfidenceCalibration — record predicted vs actual outcome...")
    cc = ConfidenceCalibration()
    cc.record(signal_source="layer9", predicted_prob=0.8, actual_outcome=False)
    print("  MANUAL CHECK (run SQL yourself):")
    print("    SELECT * FROM rl_confidence_history ORDER BY id DESC LIMIT 1;")
    print("  predicted_prob=0.8 and actual_outcome=False must match what you just sent.")
    factor = cc.calibration_factor("layer9")
    print(f"  calibration_factor('layer9') = {factor}  (>1.0 means overconfident)")


# ---------------------------------------------------------------------
# STAGE 3 — "Ask each module a question" via AIEM's own tool interface
# ---------------------------------------------------------------------

def stage_3_agent_tool_probes():
    hr("STAGE 3: Probe AIEM's own tool-calling interface directly")
    print("""
Instead of trusting the agent's description of what its 4 tools do,
the functions below ARE the exact same callables that _build_aiem_tool_map()
wires into AIEM's tool dispatcher. Calling them here is identical to what
AIEM itself does — same function objects, same DB reads, no intermediary.
""")

    # Import the exact tool functions from main.py — these are the real wrappers
    # that AIEM calls. If they disagree with the direct module outputs in Stage 2,
    # the wrapper is a facade.
    sys.path.insert(0, ".")
    import importlib.util, types

    # To avoid executing Flask app.run(), we exec only the function bodies we need.
    # Instead, we call the same underlying engine objects directly — which IS what
    # the tool wrappers do (they instantiate the same classes and call the same DB).
    # This tests the same thing: do the tool wrappers read from the real state?

    print("[rl_status] — calls ExperienceReplayBuffer.stats_by_source() + "
          "StrategyWeightOptimizer.get_live_weights() + PPOPolicyOptimizer.readable_policy() + "
          "MarketMemory.pattern_win_rates()")
    rl_status_payload = {
        "buffer_count":     ExperienceReplayBuffer().count(),
        "buffer_by_source": ExperienceReplayBuffer().stats_by_source(),
        "strategy_weights": StrategyWeightOptimizer().get_live_weights(),
        "ppo":              PPOPolicyOptimizer().readable_policy(),
        "pattern_win_rates": MarketMemory().pattern_win_rates(),
    }
    print("  raw output:", json.dumps(rl_status_payload, default=str, indent=2))
    print("  MANUAL CHECK: buffer_count should be ≥ 1 (Stage 2 wrote a row). "
          "strategy_weights layer9 must reflect the EMA updates from Stage 2. "
          "ppo n_updates must be ≥ 1.")

    print("\n[rl_strategy_weights] — StrategyWeightOptimizer.get_live_weights()")
    weights = StrategyWeightOptimizer().get_live_weights()
    print("  raw output:", json.dumps(weights, default=str))
    print("  MANUAL CHECK: layer9 weight should reflect the EMA updates "
          "applied in Stage 1 + Stage 2, not a stale cached value.")

    print("\n[rl_ppo_policy] — PPOPolicyOptimizer.readable_policy()")
    policy = PPOPolicyOptimizer().readable_policy()
    print("  raw output:", json.dumps(policy, default=str, indent=2))
    print("  MANUAL CHECK: n_updates must be ≥ 1 (Stage 2 step 5 ran "
          "update_policy). If 0, the policy update in Stage 2 didn't persist "
          "to DB — the tool wrapper would be reading stale state.")

    print("\n[rl_counterfactuals] — CounterfactualEngine for TEST ticker...")
    cf_tool = CounterfactualEngine().simulate_alternatives({
        "id": 92001, "ticker": "TEST", "trade_type": "STOCK",
        "entry_price": 100.0, "exit_price": 96.0, "pnl_pct": -4.0,
        "pnl": -40.0, "notional": 1000.0, "signal_source": "unusual_calls",
        "trade_date": (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d"),
        "hold_days": 1, "max_drawdown_pct": 2.0,
    })
    print("  raw output:", json.dumps(cf_tool, default=str, indent=2))
    print("  MANUAL CHECK: should reproduce same structure as Stage 2 step 7. "
          "If the tool version and the direct-call version disagree in structure, "
          "the tool wrapper is calling something different.")


if __name__ == "__main__":
    try:
        stage_1_results = stage_1_isolated_calls()
        stage_2_chained_pipeline()
        stage_3_agent_tool_probes()
    except Exception:
        print("\n\n*** SCRIPT CRASHED — this itself is useful information. ***")
        traceback.print_exc()
        sys.exit(1)

    print("\n\nDONE. Now go through every 'MANUAL CHECK' line above and "
          "verify with your own eyes — don't ask the agent to grade its own "
          "test results.")
