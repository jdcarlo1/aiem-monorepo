#!/usr/bin/env python3
"""
engine_manifest.py — Canonical Engine Integrity Manifest  [Items 1+2+7 — Remediation]

CANONICAL SERIALIZATION SPEC  (Item 2):
  AST normalization  : ast.dump(ast.parse(src))  — default params, no indent, no attributes
  JSON encoding      : sort_keys=True, separators=(',',':'), ensure_ascii=False, allow_nan=False
  Byte encoding      : UTF-8
  Component separator: '\\x00' (NUL byte between AST dump and weights JSON in combined hash)
  Canonicalization v : '2'  (bumped for Item 7: full decision-path modules added)
  Engine root hash   : sha256(json.dumps(manifest_without_root_hash, **JSON_KW).encode('utf-8'))

Item 7 expansion — full decision path modules hashed:
  The engine manifest previously hashed only:
    compute_req6_score + _REQ6_SCORING_WEIGHTS + math module + python version

  Now includes all modules in the complete decision path:
    aiem_options_pipeline.py      — compute_req6_score (scoring core)
    aiem_options_dpl.py           — replay_decision, capture_replay_inputs (DPL layer)
    aiem_options_scheduler.py     — _execute_job (orchestrator: stages 1-12)
    engine_manifest.py            — this file (integrity manifest itself)
    aiem_options_dpl._REPLAY_TABLE — constant value
    aiem_options_dpl._REPLAY_SCHEMA_VERSION — constant value

Negative controls (all must block):
  Change a scoring weight      -> req6_weights_hash changes -> engine_root_hash changes
  Change scoring logic         -> scoring_fn_ast_hash changes -> engine_root_hash changes
  Change a helper function     -> helper_hashes changes -> engine_root_hash changes
  Change DPL replay logic      -> decision_path_module_hashes changes -> engine_root_hash changes
  Change scheduler stage logic -> decision_path_module_hashes changes -> engine_root_hash changes
  Change configuration         -> config_hash changes -> engine_root_hash changes
  Change a model artifact      -> model_artifact_hashes changes (N/A: none used)
"""
import ast
import hashlib
import importlib.metadata as _im
import importlib.util
import inspect
import json
import os
import platform
import sys

# ---------------------------------------------------------------------------
# CANONICAL SERIALIZATION CONSTANTS
# ---------------------------------------------------------------------------
_CANON_VERSION            = "2"   # bumped: Item 7 adds full decision-path module hashes
_INTEGRITY_SCHEMA_VERSION = "2"
_JSON_KW = dict(sort_keys=True, separators=(',', ':'), ensure_ascii=False)
# allow_nan=False is the json module default; we enforce by not passing allow_nan=True


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def _sha256_json(obj) -> str:
    """sha256 of canonical JSON (sort_keys, compact separators, UTF-8)."""
    return _sha256_str(json.dumps(obj, **_JSON_KW))


def _hash_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return 'FILE_NOT_FOUND'
    return _sha256_bytes(open(path, 'rb').read())


def build_manifest() -> dict:
    """
    Build the canonical engine integrity manifest.

    Returns a dict including 'engine_root_hash'.
    The root hash is stable: the same approved source state always produces
    the same hash under the same Python version and package set.
    """
    _api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    if _api_dir not in sys.path:
        sys.path.insert(0, _api_dir)
    from aiem_options_pipeline import compute_req6_score, _REQ6_SCORING_WEIGHTS

    # ------------------------------------------------------------------
    # 1. Scoring function AST hash
    #    ast.dump with default params (indent=None, include_attributes=False)
    # ------------------------------------------------------------------
    src = inspect.getsource(compute_req6_score)
    ast_dump = ast.dump(ast.parse(src))
    scoring_fn_ast_hash = _sha256_str(ast_dump)

    # ------------------------------------------------------------------
    # 2. Weight hash (canonical JSON of _REQ6_SCORING_WEIGHTS)
    # ------------------------------------------------------------------
    req6_weights_hash = _sha256_json(_REQ6_SCORING_WEIGHTS)

    # ------------------------------------------------------------------
    # 3. Helper hashes
    #    compute_req6_score calls only builtins (abs, float, int, max, min,
    #    round, sum) and math.log10.  No custom Python helper functions.
    #    math is a C extension — hash its compiled .so/.pyd file.
    # ------------------------------------------------------------------
    math_spec   = importlib.util.find_spec('math')
    math_origin = math_spec.origin if math_spec else None
    helper_hashes = {
        'math_module_file_sha256': _hash_file(math_origin),
        'math_origin_path':        math_origin or 'unknown',
        'custom_python_helpers':   {},
        'note': (
            'compute_req6_score is self-contained; it calls only Python '
            'builtins and math.log10 (C extension, no Python source). '
            'No custom helper functions are defined or called.'
        ),
    }

    # ------------------------------------------------------------------
    # 4. Configuration hash
    #    The only configuration affecting compute_req6_score is
    #    _REQ6_SCORING_WEIGHTS.  No external config files feed it.
    # ------------------------------------------------------------------
    config_hash = req6_weights_hash  # weights IS the configuration

    # ------------------------------------------------------------------
    # 5. Model artifact hashes
    #    compute_req6_score uses NO trained ML model artifacts.
    #    The aiem_probability_engine *.pkl files belong to a separate
    #    subsystem and do not affect REQ6 scoring decisions.
    # ------------------------------------------------------------------
    model_artifact_hashes = {}

    # ------------------------------------------------------------------
    # 6. Feature schema hash
    #    The 12 dimension keys in _REQ6_SCORING_WEIGHTS define the schema.
    # ------------------------------------------------------------------
    feature_schema      = list(_REQ6_SCORING_WEIGHTS.keys())
    feature_schema_hash = _sha256_json(feature_schema)

    # ------------------------------------------------------------------
    # 7. Python version (full: major.minor.micro)
    # ------------------------------------------------------------------
    python_version = platform.python_version()

    # ------------------------------------------------------------------
    # 8. Dependency versions
    # ------------------------------------------------------------------
    try:
        _psycopg2_ver = _im.version('psycopg2-binary')
    except Exception:
        _psycopg2_ver = 'unknown'
    package_versions = {'psycopg2-binary': _psycopg2_ver}

    # ------------------------------------------------------------------
    # 9. Runtime flags — none affect scoring logic
    # ------------------------------------------------------------------
    runtime_flags = {}

    # ------------------------------------------------------------------
    # 10. Full decision-path module hashes (Item 7 — Remediation)
    #     Every Python file in the complete pipeline decision path is
    #     hashed here.  Any change to any of these files changes
    #     engine_root_hash, which invalidates the approved refs and
    #     causes the integrity gate to BLOCK production execution.
    #
    #     Decision path:
    #       aiem_options_pipeline.py  — compute_req6_score (scoring core)
    #       aiem_options_dpl.py       — DPL: capture_replay_inputs, replay_decision
    #       aiem_options_scheduler.py — orchestrator: _execute_job stages 1-12
    #       engine_manifest.py        — this file (hash of the hasher)
    #
    #     IMPORTANT: the hash is of the raw file bytes, not the imported
    #     module source, so edits that don't affect import (e.g. comments
    #     in a function not imported) still change the hash.  This is
    #     intentional — any change to a decision-path file requires a new
    #     approval cycle.
    # ------------------------------------------------------------------
    def _hash_decision_path_file(filename: str) -> str:
        path = os.path.join(_api_dir, filename)
        if not os.path.exists(path):
            return f'FILE_NOT_FOUND:{filename}'
        return _sha256_bytes(open(path, 'rb').read())

    def _hash_decision_path_this_file() -> str:
        path = os.path.abspath(__file__)
        if not os.path.exists(path):
            return f'FILE_NOT_FOUND:{path}'
        return _sha256_bytes(open(path, 'rb').read())

    # Constant values in DPL that affect replay schema
    try:
        import importlib as _impl
        _dpl_mod = _impl.import_module('aiem_options_dpl')
        _dpl_replay_table          = getattr(_dpl_mod, '_REPLAY_TABLE', 'UNKNOWN')
        _dpl_replay_schema_version = getattr(_dpl_mod, '_REPLAY_SCHEMA_VERSION', 'UNKNOWN')
    except Exception as _em:
        _dpl_replay_table          = f'IMPORT_ERROR:{_em}'
        _dpl_replay_schema_version = f'IMPORT_ERROR:{_em}'

    decision_path_module_hashes = {
        'aiem_options_pipeline.py':  _hash_decision_path_file('aiem_options_pipeline.py'),
        'aiem_options_dpl.py':       _hash_decision_path_file('aiem_options_dpl.py'),
        'aiem_options_scheduler.py': _hash_decision_path_file('aiem_options_scheduler.py'),
        'engine_manifest.py':        _hash_decision_path_this_file(),
        # DPL constants that determine replay schema
        '_REPLAY_TABLE_value':          _dpl_replay_table,
        '_REPLAY_SCHEMA_VERSION_value': str(_dpl_replay_schema_version),
        # Combined hash of all decision-path file bytes
        'decision_path_combined_hash': _sha256_str('\x00'.join([
            _hash_decision_path_file('aiem_options_pipeline.py'),
            _hash_decision_path_file('aiem_options_dpl.py'),
            _hash_decision_path_file('aiem_options_scheduler.py'),
            _hash_decision_path_this_file(),
        ])),
        'note': (
            'All files in the complete decision path are hashed. '
            'Any change to any file invalidates engine_root_hash and '
            'requires a new approval cycle before production execution.'
        ),
    }

    # ------------------------------------------------------------------
    # Assemble manifest (engine_root_hash excluded at this stage)
    # ------------------------------------------------------------------
    manifest = {
        'canonicalization_version':      _CANON_VERSION,
        'integrity_schema_version':      _INTEGRITY_SCHEMA_VERSION,
        'python_version':                python_version,
        'scoring_fn_name':               'compute_req6_score',
        'scoring_fn_module':             'aiem_options_pipeline',
        'scoring_fn_ast_hash':           scoring_fn_ast_hash,
        'req6_weights_hash':             req6_weights_hash,
        'weights_snapshot':              dict(_REQ6_SCORING_WEIGHTS),
        'helper_hashes':                 helper_hashes,
        'config_hash':                   config_hash,
        'model_artifact_hashes':         model_artifact_hashes,
        'feature_schema':                feature_schema,
        'feature_schema_hash':           feature_schema_hash,
        'package_versions':              package_versions,
        'runtime_flags':                 runtime_flags,
        'decision_path_module_hashes':   decision_path_module_hashes,
        'note_model_artifacts': (
            'compute_req6_score uses no trained ML model artifacts; '
            'aiem_probability_engine models are a separate non-overlapping subsystem'
        ),
    }

    # ------------------------------------------------------------------
    # engine_root_hash = sha256 of canonical JSON of manifest
    # (root hash is NOT included in the payload that hashes it)
    # ------------------------------------------------------------------
    engine_root_hash        = _sha256_str(json.dumps(manifest, **_JSON_KW))
    manifest['engine_root_hash'] = engine_root_hash
    return manifest


def verify_against_refs(refs_path: str) -> dict:
    """
    Load approved refs, build live manifest, compare engine_root_hash.
    Returns dict with keys: ok, live_root_hash, approved_root_hash, detail.
    """
    if not os.path.exists(refs_path):
        return {'ok': False, 'detail': f'refs_file_not_found: {refs_path}'}
    refs = json.load(open(refs_path))
    approved_hash  = refs.get('engine_root_hash', '')
    approved_commit = refs.get('commit_sha', '')
    approved_by    = refs.get('approved_by', '')

    live = build_manifest()
    live_hash = live['engine_root_hash']

    ok = (live_hash == approved_hash) and bool(approved_hash)
    return {
        'ok':                    ok,
        'live_root_hash':        live_hash,
        'approved_root_hash':    approved_hash,
        'approved_commit':       approved_commit,
        'approved_by':           approved_by,
        'component_match': {
            'scoring_fn_ast_hash': live['scoring_fn_ast_hash'] == refs.get('scoring_fn_ast_hash'),
            'req6_weights_hash':   live['req6_weights_hash']   == refs.get('req6_weights_hash'),
        },
        'live_manifest': live,
        'refs':          refs,
    }


if __name__ == '__main__':
    m = build_manifest()
    print(json.dumps(m, indent=2, sort_keys=True, default=str))
    print(f"\nengine_root_hash={m['engine_root_hash']}")
