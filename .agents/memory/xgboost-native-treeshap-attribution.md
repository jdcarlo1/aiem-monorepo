---
name: XGBoost native TreeSHAP for per-row feature attribution
description: How to get signed, additive per-feature contributions from a trained XGBClassifier without installing the separate `shap` package, and how to make the attribution provably correspond to the real model.
---

`Booster.predict(dmatrix, pred_contribs=True)` returns exact TreeSHAP contributions per feature in margin (log-odds) space, with the last column as the bias/expected-value term. `sum(contribs) == margin`, and `sigmoid(margin) == predict_proba()`. This is a proper Shapley (game-theoretic additive) attribution, not an approximation — no `shap` package needed, it ships inside xgboost itself.

**Why this matters:** before trusting the decomposition for anything user-facing (e.g. explaining "why did the model say X"), assert the manually-replayed probability (sigmoid of contribs+bias) matches `pipeline.predict_proba()` on the same row. Tolerance should be ~1e-4, not 1e-6 — `pred_contribs` returns float32, so ~1e-7 relative drift per contribution is expected and compounds; 1e-6 causes spurious assertion failures on legitimate correct output.

**How to apply:** any time a pipeline could legitimately train either a linear model (LogisticRegression, use `coef_ * scaled_value`) or a tree ensemble (XGBoost, use `pred_contribs=True`) depending on environment/data, branch on the actual fitted estimator type at attribution time — don't assume which one trained based on prior sessions' notes. Check `xgboost.__version__` / whether the package is actually installed rather than trusting old assumptions; envs can gain packages between sessions.
