"""
signal_discovery_gp.py
------------------------
Genetic-programming / symbolic-regression signal discovery.
Highest-overfit-risk module in the package — every design decision exists to
make that failure mode structurally harder:

  1. Fitness computed ONLY on training window; test set never seen during search.
  2. Every generation's best individual is logged for post-hoc sanity check.
  3. Complexity penalized in fitness (parsimony pressure against overfit expressions).
  4. Winning formula still requires hypothesis_registry + adversarial_critique.

Produces CANDIDATES, never production signals.
"""

import random
import operator
import math
from dataclasses import dataclass, field
from typing import List, Callable, Tuple, Optional, Dict, Any

import numpy as np
import pandas as pd


BINARY_OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": lambda a, b: np.where(np.abs(b) > 1e-9, a / b, 0.0),
}

UNARY_OPS = {
    "neg":       lambda a: -a,
    "abs":       np.abs,
    "log1p_abs": lambda a: np.log1p(np.abs(a)),
    "sign":      np.sign,
}


@dataclass
class Node:
    op: Optional[str] = None
    children: List["Node"] = field(default_factory=list)
    feature: Optional[str] = None
    constant: Optional[float] = None

    def is_terminal(self) -> bool:
        return self.op is None

    def complexity(self) -> int:
        if self.is_terminal():
            return 1
        return 1 + sum(c.complexity() for c in self.children)

    def evaluate(self, df: pd.DataFrame) -> np.ndarray:
        if self.is_terminal():
            if self.feature is not None:
                return df[self.feature].values.astype(float)
            return np.full(len(df), self.constant)
        if self.op in BINARY_OPS:
            a = self.children[0].evaluate(df)
            b = self.children[1].evaluate(df)
            return BINARY_OPS[self.op](a, b)
        if self.op in UNARY_OPS:
            a = self.children[0].evaluate(df)
            return UNARY_OPS[self.op](a)
        raise ValueError(f"Unknown op {self.op}")

    def to_string(self) -> str:
        if self.is_terminal():
            return self.feature if self.feature is not None else f"{self.constant:.3f}"
        if self.op in BINARY_OPS:
            return f"({self.children[0].to_string()} {self.op} {self.children[1].to_string()})"
        return f"{self.op}({self.children[0].to_string()})"


def random_terminal(feature_names: List[str]) -> Node:
    if random.random() < 0.7:
        return Node(feature=random.choice(feature_names))
    return Node(constant=round(random.uniform(-2, 2), 3))


def random_tree(feature_names: List[str], max_depth: int = 3) -> Node:
    if max_depth <= 0 or random.random() < 0.3:
        return random_terminal(feature_names)
    if random.random() < 0.7:
        op = random.choice(list(BINARY_OPS.keys()))
        return Node(op=op, children=[
            random_tree(feature_names, max_depth - 1),
            random_tree(feature_names, max_depth - 1),
        ])
    op = random.choice(list(UNARY_OPS.keys()))
    return Node(op=op, children=[random_tree(feature_names, max_depth - 1)])


def mutate(node: Node, feature_names: List[str], rate: float = 0.1) -> Node:
    if random.random() < rate:
        return random_tree(feature_names, max_depth=2)
    if node.is_terminal():
        return node
    new_children = [mutate(c, feature_names, rate) for c in node.children]
    return Node(op=node.op, children=new_children, feature=node.feature, constant=node.constant)


def crossover(a: Node, b: Node) -> Node:
    if a.is_terminal() or b.is_terminal() or random.random() < 0.3:
        return a
    idx = random.randrange(len(a.children))
    new_children = list(a.children)
    new_children[idx] = (
        b.children[random.randrange(len(b.children))] if not b.is_terminal() else b
    )
    return Node(op=a.op, children=new_children, feature=a.feature, constant=a.constant)


def fitness(
    node: Node,
    train_df: pd.DataFrame,
    forward_return_col: str,
    complexity_penalty: float = 0.01,
) -> float:
    try:
        scores = node.evaluate(train_df)
        if np.std(scores) < 1e-9 or not np.all(np.isfinite(scores)):
            return -1e9
        fwd  = train_df[forward_return_col].values
        corr = np.corrcoef(scores, fwd)[0, 1]
        if np.isnan(corr):
            return -1e9
        return abs(corr) - complexity_penalty * node.complexity()
    except Exception:
        return -1e9


@dataclass
class GenerationLog:
    generation: int
    best_fitness: float
    best_formula: str
    best_complexity: int
    population_mean_fitness: float


def evolve_signal(
    train_df: pd.DataFrame,
    forward_return_col: str,
    feature_names: List[str],
    population_size: int = 200,
    generations: int = 40,
    tournament_size: int = 5,
    elite_fraction: float = 0.05,
    complexity_penalty: float = 0.01,
    seed: Optional[int] = None,
) -> Tuple[Node, List[GenerationLog]]:
    """Run genetic search ENTIRELY on train_df. Never pass test data here."""
    if seed is not None:
        random.seed(seed)

    population = [random_tree(feature_names, max_depth=3) for _ in range(population_size)]
    log: List[GenerationLog] = []
    n_elite = max(1, int(population_size * elite_fraction))

    best_overall: Optional[Node] = None
    best_overall_fitness = -1e18

    for gen in range(generations):
        scored = [
            (fitness(ind, train_df, forward_return_col, complexity_penalty), ind)
            for ind in population
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        gen_best_fit, gen_best_ind = scored[0]
        if gen_best_fit > best_overall_fitness:
            best_overall_fitness = gen_best_fit
            best_overall = gen_best_ind

        log.append(GenerationLog(
            generation=gen,
            best_fitness=round(float(gen_best_fit), 5),
            best_formula=gen_best_ind.to_string(),
            best_complexity=gen_best_ind.complexity(),
            population_mean_fitness=round(float(np.mean([s for s, _ in scored])), 5),
        ))

        new_population = [ind for _, ind in scored[:n_elite]]
        while len(new_population) < population_size:
            t1 = random.sample(scored, tournament_size)
            t2 = random.sample(scored, tournament_size)
            parent1 = max(t1, key=lambda x: x[0])[1]
            parent2 = max(t2, key=lambda x: x[0])[1]
            child = crossover(parent1, parent2)
            child = mutate(child, feature_names)
            new_population.append(child)

        population = new_population

    return best_overall, log


def evaluate_on_holdout(
    formula: Node,
    holdout_df: pd.DataFrame,
    forward_return_col: str,
) -> Dict[str, Any]:
    """The ONE legitimate touch of held-out data. Call exactly once per formula."""
    scores    = formula.evaluate(holdout_df)
    fwd       = holdout_df[forward_return_col].values
    corr      = float(np.corrcoef(scores, fwd)[0, 1]) if np.std(scores) > 1e-9 else 0.0
    direction = np.sign(scores)
    realized  = direction * fwd
    win_rate  = float(np.mean(realized > 0)) if len(realized) else 0.0

    return {
        "formula":             formula.to_string(),
        "complexity":          formula.complexity(),
        "holdout_correlation": round(corr, 4),
        "holdout_win_rate":    round(win_rate, 4),
        "holdout_n":           len(holdout_df),
        "warning": (
            "GP search effectively tries hundreds of formulas. Register this "
            "formula in hypothesis_registry as its own hypothesis and run it "
            "through adversarial_critique before treating this result as meaningful."
        ),
    }


def parse_formula(formula_str: str, feature_names: Optional[List[str]] = None) -> Node:
    """Reconstruct a Node tree from Node.to_string() output.

    Supports binary ops (+ - * /), unary ops (neg/abs/log1p_abs/sign),
    feature terminals, and numeric constants (including signed floats).
    """
    feature_names = list(feature_names or [])
    s = (formula_str or "").strip()
    if not s:
        raise ValueError("empty formula")
    i = [0]

    def peek() -> str:
        while i[0] < len(s) and s[i[0]].isspace():
            i[0] += 1
        return s[i[0]] if i[0] < len(s) else ""

    def parse_number() -> Node:
        start = i[0]
        if peek() in "+-":
            i[0] += 1
        while i[0] < len(s) and (s[i[0]].isdigit() or s[i[0]] == "."):
            i[0] += 1
        return Node(constant=float(s[start:i[0]]))

    def parse_expr() -> Node:
        peek()
        if peek() == "(":
            i[0] += 1
            left = parse_expr()
            peek()
            op = peek()
            if op not in BINARY_OPS:
                raise ValueError(f"bad binary op {op!r} at pos {i[0]}")
            i[0] += 1
            right = parse_expr()
            peek()
            if peek() != ")":
                raise ValueError(f"missing ')' at pos {i[0]}")
            i[0] += 1
            return Node(op=op, children=[left, right])

        ch = peek()
        if ch.isdigit() or ch == "." or (
            ch in "+-" and i[0] + 1 < len(s) and (s[i[0] + 1].isdigit() or s[i[0] + 1] == ".")
        ):
            return parse_number()

        start = i[0]
        while i[0] < len(s) and (s[i[0]].isalnum() or s[i[0]] == "_"):
            i[0] += 1
        tok = s[start:i[0]]
        if peek() == "(" and tok in UNARY_OPS:
            i[0] += 1
            child = parse_expr()
            if peek() != ")":
                raise ValueError(f"missing ')' after unary {tok}")
            i[0] += 1
            return Node(op=tok, children=[child])
        if feature_names and tok in feature_names:
            return Node(feature=tok)
        if not feature_names:
            # Allow any identifier as a feature when the feature list is unknown.
            return Node(feature=tok)
        raise ValueError(f"unknown token {tok!r}")

    node = parse_expr()
    peek()
    if i[0] != len(s):
        raise ValueError(f"unparsed leftover: {s[i[0]]!r}")
    return node


DEFAULT_GP_FEATURES = ["gap_pct", "rvol", "close_strength", "range_pct"]


def score_features_with_formula(
    formula_str: str,
    features: Dict[str, float],
    feature_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Evaluate a stored GP formula on a single feature row (orchestrator path)."""
    names = list(feature_names or DEFAULT_GP_FEATURES)
    node = parse_formula(formula_str, names)
    row = {k: float(features.get(k, 0.0) or 0.0) for k in names}
    df = pd.DataFrame([row])
    raw = float(node.evaluate(df)[0])
    if not np.isfinite(raw):
        raw = 0.0
    # Map unbounded formula output to a 0-100 score centered at 50.
    score = float(np.clip(50.0 + np.tanh(raw) * 50.0, 0.0, 100.0))
    return {
        "formula": formula_str,
        "raw": round(raw, 6),
        "score": round(score, 2),
        "features_used": row,
        "complexity": node.complexity(),
    }


def load_promoted_formulas(db_url: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Load promoted (else pending_review) GP formulas for live packet scoring."""
    import psycopg2
    import psycopg2.extras

    with psycopg2.connect(db_url, connect_timeout=3) as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        # Holdout columns are added by the weekly GP job (ALTER IF NOT EXISTS).
        try:
            cur.execute("""
                SELECT id, formula, fitness, complexity, training_n, status,
                       holdout_correlation, holdout_win_rate, holdout_n, evolved_at
                FROM gp_discovered_templates
                WHERE status IN ('promoted', 'pending_review')
                ORDER BY
                    CASE WHEN status = 'promoted' THEN 0 ELSE 1 END,
                    evolved_at DESC
                LIMIT %s
            """, (limit,))
        except Exception:
            conn.rollback()
            cur.execute("""
                SELECT id, formula, fitness, complexity, training_n, status, evolved_at
                FROM gp_discovered_templates
                WHERE status IN ('promoted', 'pending_review')
                ORDER BY
                    CASE WHEN status = 'promoted' THEN 0 ELSE 1 END,
                    evolved_at DESC
                LIMIT %s
            """, (limit,))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("evolved_at"):
                d["evolved_at"] = d["evolved_at"].isoformat()
            rows.append(d)
        return rows


if __name__ == "__main__":
    print("signal_discovery_gp: train-only genetic search engine.")
    print("Call evolve_signal() on a TRAIN split, then evaluate_on_holdout() exactly once.")
