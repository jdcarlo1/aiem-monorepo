"""
model_registry.py - point-in-time model versioning for the AIEM Probability
Engine.

THE BUG THIS FIXES: before this file existed, train.py always overwrote
MODEL_DIR/model_horizon_{h}d.pkl in place, and predict.py's load_models()
always loaded whichever file happened to be on disk "right now" - there was
no way to ask "what model would have existed as of some earlier date?".
That made it structurally impossible for reports.py to score a historical
signal_date without accidentally using a model trained on data from AFTER
that date (a model that has, in a real sense, already seen the future
relative to the row being scored). This was confirmed live in the DB:
213/225 shadow-log rows had a signal_date weeks before the model_version
that scored them was even trained (created_at::date - signal_date up to 23
days), because generate_and_log_predictions() scored the ENTIRE historical
backlog with the one model that happened to be on disk at that moment.

THE FIX: every train.py run now ALSO writes a permanent, NEVER-overwritten
versioned copy of each horizon's model (models/versions/model_horizon_{h}d
__cutoff_<date>.pkl) and registers it here with two dates:

  - cutoff_date: the latest trade_date actually included in that model's
    training rows (train.py's `sub["trade_date"].max()` for that horizon).
    Per-horizon, not per-run - horizon 4's label needs 4 more trading days
    to exist than horizon 1's, so the SAME train.py invocation can (and
    does) produce a different cutoff_date per horizon. That is correct,
    not a bug - each horizon's own point-in-time eligibility is judged
    independently.

  - label_settled_through: the latest calendar date whose OUTCOME
    information this model could possibly have absorbed. A training row
    with trade_date == cutoff_date has a label_{h}d computed from a close
    price h TRADING days later, which can land several CALENDAR days later
    once weekends/holidays are counted. _label_settle_buffer_days() is a
    deliberately conservative OVER-estimate - erring toward excluding a
    borderline-eligible model is safe; silently including a leaky one is
    the exact bug this file exists to prevent.

get_as_of(horizon, as_of_date) is the ONLY function anything should use to
pick a model for scoring a specific signal_date (historical OR live-today).
It requires label_settled_through < as_of_date (strict) and returns the
most recent qualifying entry - never "closest," never a fallback to a
disqualified newer model. If nothing qualifies it returns None, and the
caller must skip that row/date rather than guess.

get_latest(horizon) is for genuinely-live, no-as-of-date use only (e.g. a
demo run's "what does the model say right now" printout) - it must never
be used to score a row that has its own signal_date.
"""
import hashlib
import json
import os
import pickle
from datetime import date, timedelta
from typing import Optional

from config import MODEL_DIR

REGISTRY_PATH = os.path.join(MODEL_DIR, "registry.json")
VERSIONS_DIR = os.path.join(MODEL_DIR, "versions")
os.makedirs(VERSIONS_DIR, exist_ok=True)


def _label_settle_buffer_days(horizon_days: int) -> int:
    """
    Conservative calendar-day buffer covering `horizon_days` TRADING days,
    padded for at least one weekend plus slack for a holiday. horizon_days
    is 1-4 in this package, so the max buffer is small (8 calendar days) -
    deliberately generous rather than exact, since overestimating only
    makes get_as_of() MORE conservative (excludes more), never less.
    """
    return horizon_days + 4


def _load_registry() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH, "r") as f:
        return json.load(f)


def _save_registry(reg: dict) -> None:
    tmp_path = REGISTRY_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp_path, REGISTRY_PATH)


def register_model(horizon: int, cutoff_date: date, path: str,
                    n_samples: int, n_unique_dates: int, is_trustworthy: bool,
                    trained_at: str) -> dict:
    """
    Records one trained, versioned model artifact. Idempotent per
    (horizon, cutoff_date): re-registering the same cutoff_date (e.g. a
    same-day re-run against an unchanged dataset) overwrites that entry
    rather than duplicating it. Returns the entry that was written.
    """
    reg = _load_registry()
    key = str(horizon)
    entries = reg.setdefault(key, [])

    settle_through = cutoff_date + timedelta(days=_label_settle_buffer_days(horizon))

    entry = {
        "cutoff_date": cutoff_date.isoformat(),
        "label_settled_through": settle_through.isoformat(),
        "path": path,
        "n_samples": n_samples,
        "n_unique_dates": n_unique_dates,
        "is_trustworthy": is_trustworthy,
        "trained_at": trained_at,
    }

    entries[:] = [e for e in entries if e["cutoff_date"] != entry["cutoff_date"]]
    entries.append(entry)
    entries.sort(key=lambda e: e["cutoff_date"])

    _save_registry(reg)
    return entry


def _entries_for(horizon: int) -> list:
    reg = _load_registry()
    return reg.get(str(horizon), [])


def get_latest(horizon: int) -> Optional[dict]:
    """Most recently trained entry for this horizon, no date constraint.
    Live/current use ONLY - never for scoring a row that has its own
    signal_date; use get_as_of for that."""
    entries = _entries_for(horizon)
    return entries[-1] if entries else None


def get_as_of(horizon: int, as_of_date: date) -> Optional[dict]:
    """
    Most recent entry whose label_settled_through is STRICTLY before
    as_of_date - i.e. this model could not possibly have absorbed any
    outcome information from as_of_date or later. Returns None (never a
    disqualified fallback) if no entry qualifies yet.
    """
    eligible = [
        e for e in _entries_for(horizon)
        if date.fromisoformat(e["label_settled_through"]) < as_of_date
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda e: e["cutoff_date"])


def load_model_from_entry(entry: dict):
    with open(entry["path"], "rb") as f:
        return pickle.load(f)


def version_string_for_entries(entries: dict, include_calibrated: bool = True) -> str:
    """
    entries: {horizon: registry entry dict} ACTUALLY used to score a batch.

    Hashes the real bytes of each entry's own versioned artifact (never
    "whatever happens to be on MODEL_DIR right now") so a model_version tag
    always identifies the exact models that produced a given prediction -
    including when they are deliberately NOT the current/latest ones (PIT
    backfill correction uses an older entry on purpose).

    include_calibrated=True also mixes in the current
    calibrated_horizon_{h}d.pkl bytes (if present) for horizons in
    `entries`, matching predict.compute_model_version()'s legacy hash
    exactly for the common case where entries == "today's latest models" -
    this keeps daily_picks.py's model_version lookups working unchanged.
    Calibrated artifacts are NOT themselves versioned by cutoff_date in
    this fix (calibration is never selected today - see predict.py's
    gating - so this is a deliberately small, disclosed scope boundary).
    PIT correction runs (pit_correction.py) never use calibration and must
    pass include_calibrated=False so a historical correction's version tag
    isn't falsely implied to reflect today's calibration artifact.
    """
    h = hashlib.sha256()
    for horizon in sorted(entries.keys()):
        with open(entries[horizon]["path"], "rb") as f:
            h.update(f.read())
        if include_calibrated:
            cal_path = os.path.join(MODEL_DIR, f"calibrated_horizon_{horizon}d.pkl")
            if os.path.exists(cal_path):
                with open(cal_path, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()[:12]


if __name__ == "__main__":
    from config import HORIZONS
    reg = _load_registry()
    if not reg:
        print("[model_registry] empty - run train.py to populate it")
    for h in HORIZONS:
        entries = _entries_for(h)
        print(f"\nhorizon={h}d: {len(entries)} registered entries")
        for e in entries:
            print(f"  cutoff={e['cutoff_date']}  settled_through={e['label_settled_through']}  "
                  f"n={e['n_samples']}  dates={e['n_unique_dates']}  "
                  f"trustworthy={e['is_trustworthy']}  path={e['path']}")
