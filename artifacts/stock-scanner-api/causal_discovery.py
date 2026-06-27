"""
causal_discovery.py
----------------------
Builds an actual causal GRAPH from your signal/return data automatically,
rather than testing one confound relationship at a time (which is what
causal_inference.py does). This implements a simplified version of the
PC algorithm — a well-established, widely-cited method in causal discovery
research — to infer likely causal structure among your signals, market
factors, and forward returns.

WHAT THIS CAN AND CANNOT TELL YOU, STATED PLAINLY:
Causal discovery from observational data (no controlled experiments) always
rests on assumptions that can't be fully verified from the data alone — most
importantly, that there's no unmeasured common cause influencing two
variables you think are directly linked. This algorithm gives you the best
graph CONSISTENT with your data and the stated assumptions. It does not
prove the graph is correct. Treat the output as a strong hypothesis-
generation tool, not an oracle.

Algorithm (simplified PC):
  1. Start with a fully-connected graph among all variables.
  2. For each pair, test conditional independence given subsets of other
     variables. If independent given some subset, remove the edge.
  3. Orient remaining edges using standard PC orientation rules where
     unambiguous; leave undirected where the data doesn't determine
     direction (this is normal and expected).

REQUIRES: numpy, pandas, scipy.
"""

import itertools
from typing import Dict, Any, List, Set, Tuple, Optional

import numpy as np
import pandas as pd
from scipy import stats


def partial_correlation(df: pd.DataFrame, x: str, y: str, conditioning_set: List[str]) -> float:
    """Partial correlation between x and y controlling for conditioning_set,
    via residualization (regress both on the conditioning set, correlate residuals)."""
    if not conditioning_set:
        return float(df[x].corr(df[y]))

    Z    = df[conditioning_set].values
    Z    = np.column_stack([np.ones(len(Z)), Z])

    def residualize(target):
        beta, _, _, _ = np.linalg.lstsq(Z, target, rcond=None)
        return target - Z @ beta

    x_resid = residualize(df[x].values)
    y_resid = residualize(df[y].values)

    if np.std(x_resid) < 1e-9 or np.std(y_resid) < 1e-9:
        return 0.0
    return float(np.corrcoef(x_resid, y_resid)[0, 1])


def conditional_independence_test(
    df: pd.DataFrame, x: str, y: str,
    conditioning_set: List[str], alpha: float = 0.05,
) -> Tuple[bool, float]:
    """Fisher's z-test for conditional independence via partial correlation.
    Returns (is_independent, p_value)."""
    n = len(df)
    k = len(conditioning_set)
    if n - k - 3 <= 0:
        return True, 1.0

    r = partial_correlation(df, x, y, conditioning_set)
    r = max(min(r, 0.9999), -0.9999)

    z      = 0.5 * np.log((1 + r) / (1 - r))
    se     = 1.0 / np.sqrt(n - k - 3)
    z_stat = z / se
    p_val  = 2 * (1 - stats.norm.cdf(abs(z_stat)))

    return p_val > alpha, float(p_val)


def pc_skeleton(
    df: pd.DataFrame,
    variables: List[str],
    max_conditioning_set_size: int = 2,
    alpha: float = 0.05,
) -> Tuple[Dict[Tuple[str, str], bool], Dict[Tuple[str, str], List[str]]]:
    """Builds the undirected skeleton via conditional independence tests.
    Returns (edges dict, removal_reasons dict)."""
    edges           = {pair: True for pair in itertools.combinations(variables, 2)}
    removal_reasons = {}

    for size in range(0, max_conditioning_set_size + 1):
        for (x, y) in list(edges.keys()):
            if not edges[(x, y)]:
                continue
            other_vars = [v for v in variables if v not in (x, y)]
            for conditioning_set in itertools.combinations(other_vars, size):
                is_independent, _ = conditional_independence_test(
                    df, x, y, list(conditioning_set), alpha
                )
                if is_independent:
                    edges[(x, y)]           = False
                    removal_reasons[(x, y)] = list(conditioning_set)
                    break

    return edges, removal_reasons


def orient_edges(
    edges: Dict[Tuple[str, str], bool],
    removal_reasons: Dict[Tuple[str, str], List[str]],
    variables: List[str],
) -> Dict[str, Any]:
    """Applies basic PC orientation via collider detection.
    Undirected edges are the honest outcome when data doesn't determine direction."""
    directed         = []
    remaining_edges  = [pair for pair, present in edges.items() if present]

    for z in variables:
        neighbors_of_z = list({v for pair in remaining_edges if z in pair for v in pair if v != z})
        for x, y in itertools.combinations(neighbors_of_z, 2):
            pair_xy  = (x, y) if (x, y) in removal_reasons else (y, x)
            pair_xz  = (x, z) if (x, z) in edges else (z, x)
            pair_yz  = (y, z) if (y, z) in edges else (z, y)
            xy_removed = not edges.get((x, y), True) or not edges.get((y, x), True)
            if (xy_removed and pair_xy in removal_reasons
                    and z not in removal_reasons[pair_xy]
                    and edges.get(pair_xz, False)
                    and edges.get(pair_yz, False)):
                directed.append((x, z, "->"))
                directed.append((y, z, "->"))

    directed_set = {(a, b) for a, b, _ in directed}
    undirected   = [
        pair for pair in remaining_edges
        if pair not in directed_set and (pair[1], pair[0]) not in directed_set
    ]

    return {"directed_edges": directed, "undirected_edges": undirected}


def discover_causal_structure(
    df: pd.DataFrame,
    variables: List[str],
    max_conditioning_set_size: int = 2,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Full pipeline entry point. Pass a DataFrame with columns matching
    `variables` — typically your signal scores, market factors (VIX,
    sector ETF returns), and forward_return, all as numeric series."""
    if len(df) < 50:
        return {"error": "insufficient_data", "n_rows": len(df), "min_recommended": 50}

    edges, removal_reasons = pc_skeleton(df, variables, max_conditioning_set_size, alpha)
    orientation            = orient_edges(edges, removal_reasons, variables)

    remaining = [list(pair) for pair, present in edges.items() if present]
    removed   = [{"pair": list(pair), "explained_by": cond}
                 for pair, cond in removal_reasons.items()]

    return {
        "variables":       variables,
        "n_observations":  len(df),
        "remaining_edges": remaining,
        "removed_edges":   removed,
        "directed_edges":  orientation["directed_edges"],
        "undirected_edges": [list(p) for p in orientation["undirected_edges"]],
        "interpretation": (
            "Directed edges (A -> B) suggest A may causally influence B via collider "
            "detection — but this rests on the assumption of no unmeasured common cause. "
            "Undirected edges mean a relationship exists but direction is indeterminate — "
            "this is common and NOT a failure, it's an honest gap in what observational "
            "data alone can tell you. Removed edges indicate likely confounding by the "
            "variable(s) in 'explained_by'."
        ),
        "next_step": (
            "Any edge pointing INTO a signal you trade on should be cross-checked with "
            "causal_inference.py's confound-control test, and any hypothesis derived from "
            "this graph should go through hypothesis_registry before being trusted."
        ),
    }


if __name__ == "__main__":
    print("causal_discovery: simplified PC algorithm for multi-variable causal structure.")
    print("Call discover_causal_structure(df, variables) with your signal/factor data.")
