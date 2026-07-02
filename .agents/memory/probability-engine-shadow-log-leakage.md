---
name: Probability engine shadow-log point-in-time leakage
description: aiem_probability_engine's shadow prediction log scores old backlog rows with the current (future-trained) model, not a point-in-time-safe one.
---

`aiem_probability_engine/reports.py`'s `generate_and_log_predictions()` always scores any unlogged backlog row with whatever `model_horizon_{h}d.pkl` is currently on disk, regardless of how old that row's `signal_date` is. `train.py` always trains on the full dataset available at run time, so a backfilled row can be "predicted" by a model that was trained on data from after that row's signal_date — the model has seen the future relative to that logged prediction.

**Why:** discovered during a 2026-07-02 audit by tracing one specific historical row end-to-end (signal_date=2026-06-08, created_at=2026-07-01, model trained on the full 438-row dataset). Confirmed by comparing `n_training_samples` on the logged row against a fresh live training run on the full current dataset — they matched exactly.

**How to apply:** only `walk_forward.py`'s backtest (real train/val date split, `overlap=False` enforced and asserted) is point-in-time trustworthy for historical accuracy claims. Treat `aiem_probability_engine_predictions` rows as model-health/shadow monitoring only, never as "what the model would have called in real time" unless `created_at` is very close to `signal_date`. Documented inline in both `config.py` and `reports.py`. Not yet fixed — would need per-retrain-date model snapshots or a PIT-safety flag on shadow-log rows.

Related: the probability engine is NOT wired into any automatic scheduler — it's isolated from main.py by design ("isolation contract"), only runs via manual admin "force run" or direct script invocation, and as of the audit had only 9-11 unique trade dates (below its own `MIN_UNIQUE_DATES_FOR_CV_TRUST=20` gate), so `predict.py` hard-caps confidence at 0.55 regardless of model output.
