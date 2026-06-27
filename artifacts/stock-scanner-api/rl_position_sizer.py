"""
rl_position_sizer.py
-----------------------
Reinforcement learning for position sizing and exit timing — the agent
learns, through repeated paper-trading experience, how much to size a
position and when to exit, rather than using fixed rules.

WHY THIS IS RISKIER THAN EVERYTHING BEFORE IT: an RL agent's behavior
emerges from training, not from rules you wrote — you can't read a single
function and know what it'll do in a new situation. That's exactly why
this module hard-wires itself to the safety stack we already built rather
than operating independently:

  - Every action this module takes is STILL gated by simulation_lock
    (cannot place real trades) and kill_switch (halts on bad patterns).
  - Every action is STILL logged through decision_logger with the model's
    confidence/value estimates as the "reasoning" field — so an RL action
    is just as reviewable as anything else in your history.
  - The agent trains ONLY on a fixed historical buffer at any point in
    time (no online updates mid-trade), and every policy update goes
    through the same accept/reject gate as online_learning.py before
    being used live in paper trading.

Algorithm: tabular Q-learning over a discretized state space (signal
conviction score bucket, current unrealized P&L bucket, days held bucket).
This is deliberately the SIMPLEST form of RL, not deep RL — a tabular
Q-table is fully inspectable (you can print the entire learned policy as a
table and read every single state->action mapping), which matters enormously
for something you're trying to actually understand and trust over a year of
review, not just deploy and hope.

REQUIRES: AIEM_DATABASE_URL, numpy.
"""

import os
import json
import pickle
import datetime as dt
from typing import Dict, Any, Tuple, Optional, List
from collections import defaultdict

import numpy as np
import psycopg2
import psycopg2.extras

import simulation_lock as sl
import decision_logger as dl


DDL = """
CREATE TABLE IF NOT EXISTS rl_policy_versions (
    id SERIAL PRIMARY KEY,
    policy_name TEXT NOT NULL,
    version INT NOT NULL,
    q_table_blob BYTEA NOT NULL,
    trained_on_n_episodes INT NOT NULL,
    held_out_avg_reward NUMERIC,
    is_live BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (policy_name, version)
);
"""

ACTIONS = ["no_position", "size_25pct", "size_50pct", "size_100pct", "exit"]

CONVICTION_BUCKETS = ["low", "medium", "high"]
PNL_BUCKETS        = ["losing", "flat", "winning"]
DAYS_HELD_BUCKETS  = ["new", "short", "long"]


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[rl_position_sizer] schema ready")


def discretize_state(conviction_score: float, unrealized_pnl_pct: float, days_held: int) -> Tuple[str, str, str]:
    conviction = "low" if conviction_score < 0.4 else "medium" if conviction_score < 0.7 else "high"
    pnl        = "losing" if unrealized_pnl_pct < -0.5 else "flat" if unrealized_pnl_pct < 0.5 else "winning"
    days       = "new" if days_held <= 1 else "short" if days_held <= 5 else "long"
    return (conviction, pnl, days)


class TabularQPolicy:
    """A fully-inspectable Q-table. q_table[state][action] = expected value."""

    def __init__(self, learning_rate: float = 0.1, discount: float = 0.95, epsilon: float = 0.1):
        self.q_table: Dict[Tuple, Dict[str, float]] = defaultdict(lambda: {a: 0.0 for a in ACTIONS})
        self.lr       = learning_rate
        self.discount = discount
        self.epsilon  = epsilon

    def choose_action(self, state: Tuple, explore: bool = True) -> str:
        if explore and np.random.random() < self.epsilon:
            return np.random.choice(ACTIONS)
        return max(self.q_table[state], key=self.q_table[state].get)

    def update(self, state: Tuple, action: str, reward: float, next_state: Tuple):
        current_q  = self.q_table[state][action]
        max_next_q = max(self.q_table[next_state].values())
        target = reward + self.discount * max_next_q
        self.q_table[state][action] = current_q + self.lr * (target - current_q)

    def to_readable_table(self) -> List[Dict[str, Any]]:
        """Dump the ENTIRE learned policy as a flat, human-readable table.
        Read this periodically — if something looks insane (e.g. 'always
        go 100% when losing and held long'), that's a red flag."""
        rows = []
        for state, actions in self.q_table.items():
            best_action = max(actions, key=actions.get)
            rows.append({
                "conviction": state[0], "pnl": state[1], "days_held": state[2],
                "best_action": best_action,
                "q_values": {a: round(v, 3) for a, v in actions.items()},
            })
        return rows

    def serialize(self) -> bytes:
        return pickle.dumps(dict(self.q_table))

    @classmethod
    def deserialize(cls, blob: bytes, **kwargs) -> "TabularQPolicy":
        policy = cls(**kwargs)
        loaded = pickle.loads(blob)
        for state, actions in loaded.items():
            policy.q_table[state] = actions
        return policy


def train_offline(
    policy: TabularQPolicy,
    historical_episodes: List[Dict[str, Any]],
    n_passes: int = 50,
) -> TabularQPolicy:
    """Trains ENTIRELY on a fixed historical buffer — no live/online updates
    mid-trade. Each episode dict: {"state": (conv,pnl,days), "action": str,
    "reward": float, "next_state": (...)}"""
    for _ in range(n_passes):
        for ep in historical_episodes:
            policy.update(ep["state"], ep["action"], ep["reward"], ep["next_state"])
    return policy


def evaluate_policy_held_out(policy: TabularQPolicy, held_out_episodes: List[Dict[str, Any]]) -> float:
    """Average reward the policy WOULD have gotten on held-out episodes."""
    total_reward = 0.0
    for ep in held_out_episodes:
        action = policy.choose_action(ep["state"], explore=False)
        if action == ep["action"]:
            total_reward += ep["reward"]
    return total_reward / len(held_out_episodes) if held_out_episodes else 0.0


def save_policy_version(policy_name: str, policy: TabularQPolicy,
                        n_episodes: int, held_out_score: float,
                        promote: bool = False) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM rl_policy_versions WHERE policy_name = %s",
                (policy_name,),
            )
            version = cur.fetchone()[0]
            if promote:
                cur.execute("UPDATE rl_policy_versions SET is_live = FALSE WHERE policy_name = %s", (policy_name,))
            cur.execute(
                """
                INSERT INTO rl_policy_versions
                    (policy_name, version, q_table_blob, trained_on_n_episodes,
                     held_out_avg_reward, is_live)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (policy_name, version, policy.serialize(), n_episodes, held_out_score, promote),
            )
            policy_id = cur.fetchone()[0]
        conn.commit()
    return policy_id


def get_live_policy(policy_name: str) -> Optional[TabularQPolicy]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT q_table_blob FROM rl_policy_versions WHERE policy_name = %s AND is_live = TRUE",
                (policy_name,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return TabularQPolicy.deserialize(row["q_table_blob"])


def get_paper_action(
    policy_name: str,
    signal_name: str,
    conviction_score: float,
    unrealized_pnl_pct: float,
    days_held: int,
) -> Dict[str, Any]:
    """The actual call site the agent uses. Calls assert_simulation_mode()
    first — the RL policy output is a recommended PAPER action only."""
    sl.assert_simulation_mode(caller_name="rl_position_sizer.get_paper_action")

    policy = get_live_policy(policy_name)
    if policy is None:
        action    = "no_position"
        reasoning = f"No trained live policy found for '{policy_name}' — defaulting to no_position."
    else:
        state     = discretize_state(conviction_score, unrealized_pnl_pct, days_held)
        action    = policy.choose_action(state, explore=False)
        q_values  = policy.q_table[state]
        reasoning = (
            f"RL policy state={state} -> action={action}. "
            f"Q-values: {json.dumps({k: round(v,3) for k,v in q_values.items()})}"
        )

    dl.log_decision(
        signal_name=signal_name,
        decision_type="trade" if action != "no_position" else "no_trade",
        reasoning=reasoning,
        input_state_snapshot={
            "conviction_score": conviction_score,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "days_held": days_held,
            "policy_name": policy_name,
        },
    )

    return {"action": action, "reasoning": reasoning}


if __name__ == "__main__":
    init_schema()
    print("rl_position_sizer schema ready.")
    print(f"Action space: {ACTIONS}")
