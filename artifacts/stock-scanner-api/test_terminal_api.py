#!/usr/bin/env python3
"""
AIEM Terminal API Test Suite — Phase 2 API Standardization
Covers: API-021 (contract tests), API-022 (field mismatch), API-023 (schema),
        API-033 (latency recording), API-034 (count reconciliation),
        API-035 (live endpoints), API-036 (missing auth), API-037 (wrong role),
        API-038 (malformed input), API-039 (empty results).
Run: python3 test_terminal_api.py
Requires: requests (pip install requests), ADMIN_TOKEN env var, stock-api on localhost:5050.
"""
import os
import sys
import unittest
import requests

BASE = os.environ.get("STOCK_API_BASE", "http://localhost:5050")
TOKEN = os.environ.get("ADMIN_TOKEN", "")
AUTH = {"X-Admin-Token": TOKEN}
WRONG_AUTH = {"X-Admin-Token": "WRONGTOKEN_INVALID_PHASE2_TEST"}
NO_AUTH = {}

TERMINAL_ROUTES = [
    "/stock-api/admin/decision-audit",
    "/stock-api/admin/gate-events",
    "/stock-api/admin/council-runs",
    "/stock-api/admin/position-sizing-log",
    "/stock-api/admin/evidence-chain/status",
]

DATE_ROUTES = [
    "/stock-api/admin/decision-audit",
    "/stock-api/admin/gate-events",
    "/stock-api/admin/council-runs",
    "/stock-api/admin/position-sizing-log",
]

PAGINATED_ROUTES = [
    "/stock-api/admin/decision-audit",
    "/stock-api/admin/gate-events",
    "/stock-api/admin/council-runs",
    "/stock-api/admin/position-sizing-log",
]


class TestAPI036MissingAuth(unittest.TestCase):
    """API-036: Missing X-Admin-Token → 403 with code=AUTH_REQUIRED on all Terminal routes."""

    def test_no_token_all_terminal_routes(self):
        for route in TERMINAL_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}", headers=NO_AUTH, timeout=15)
                self.assertEqual(r.status_code, 403,
                    f"[API-036] {route}: expected 403 without token, got {r.status_code}")
                body = r.json()
                self.assertIn("error", body,
                    f"[API-036] {route}: 403 response missing 'error' key")
                self.assertIn("code", body,
                    f"[API-036] {route}: 403 response missing 'code' key — API-018 not applied")
                self.assertEqual(body["code"], "AUTH_REQUIRED",
                    f"[API-036] {route}: expected code=AUTH_REQUIRED, got {body.get('code')}")


class TestAPI037WrongRole(unittest.TestCase):
    """API-037: Wrong X-Admin-Token → 403 on all Terminal routes."""

    def test_wrong_token_all_terminal_routes(self):
        for route in TERMINAL_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}", headers=WRONG_AUTH, timeout=15)
                self.assertEqual(r.status_code, 403,
                    f"[API-037] {route}: expected 403 with wrong token, got {r.status_code}")
                body = r.json()
                self.assertIn("code", body,
                    f"[API-037] {route}: 403 response missing 'code' key")
                self.assertEqual(body["code"], "AUTH_REQUIRED",
                    f"[API-037] {route}: expected AUTH_REQUIRED, got {body.get('code')}")


class TestAPI038MalformedInput(unittest.TestCase):
    """API-038: Malformed input → 400 with code=INVALID_PARAM."""

    def test_invalid_limit_all_paginated_routes(self):
        for route in PAGINATED_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}?limit=abc", headers=AUTH, timeout=15)
                self.assertEqual(r.status_code, 400,
                    f"[API-038] {route}?limit=abc: expected 400, got {r.status_code}")
                body = r.json()
                self.assertIn("error", body)
                self.assertIn("code", body,
                    f"[API-038] {route}: 400 response missing 'code' key")
                self.assertEqual(body["code"], "INVALID_PARAM",
                    f"[API-038] {route}: expected INVALID_PARAM, got {body.get('code')}")

    def test_invalid_date_all_date_routes(self):
        for route in DATE_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}?date=not-a-date", headers=AUTH, timeout=15)
                self.assertEqual(r.status_code, 400,
                    f"[API-038] {route}?date=not-a-date: expected 400, got {r.status_code}")
                body = r.json()
                self.assertIn("code", body,
                    f"[API-038] {route}: bad date 400 response missing 'code' key")
                self.assertEqual(body["code"], "INVALID_PARAM",
                    f"[API-038] {route}: expected INVALID_PARAM, got {body.get('code')}")

    def test_invalid_date_format_variations(self):
        bad_dates = ["2026/07/22", "22-07-2026", "July 22", "20260722", "9999-99-99"]
        for bad in bad_dates:
            with self.subTest(date=bad):
                r = requests.get(
                    f"{BASE}/stock-api/admin/decision-audit?date={bad}",
                    headers=AUTH, timeout=15)
                self.assertEqual(r.status_code, 400,
                    f"[API-038] date={bad!r}: expected 400, got {r.status_code}")

    def test_limit_clamped_not_rejected(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?limit=999",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(r.json()["limit"], 200,
            "[API-027] limit=999 should be clamped to ≤200, not rejected")

    def test_invalid_paper_trade_id(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/position-sizing-log?paper_trade_id=notanint",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 400,
            "[API-038] paper_trade_id=notanint: expected 400")


class TestAPI039EmptyResults(unittest.TestCase):
    """API-039: Empty-result handling — count=0, rows=[], HTTP 200."""

    def test_empty_results_ancient_date(self):
        for route in DATE_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}?date=1900-01-01", headers=AUTH, timeout=15)
                self.assertEqual(r.status_code, 200,
                    f"[API-039] {route}?date=1900-01-01: expected 200, got {r.status_code}")
                body = r.json()
                self.assertEqual(body["count"], 0,
                    f"[API-039] {route}: expected count=0 for 1900-01-01, got {body.get('count')}")
                self.assertEqual(body["rows"], [],
                    f"[API-039] {route}: expected rows=[] for 1900-01-01")

    def test_empty_results_nonexistent_ticker(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?ticker=ZZZZNOTREAL99",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 0,
            "[API-039] ticker=ZZZZNOTREAL99: expected count=0")
        self.assertEqual(body["rows"], [])

    def test_empty_results_nonexistent_ticker_gate_events(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/gate-events?ticker=ZZZZNOTREAL99",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)


class TestAPI033LatencyRecording(unittest.TestCase):
    """API-033: elapsed_ms present in all SQL Terminal route responses."""

    def test_elapsed_ms_in_all_sql_routes(self):
        for route in PAGINATED_ROUTES:
            with self.subTest(route=route):
                r = requests.get(f"{BASE}{route}?limit=1", headers=AUTH, timeout=15)
                self.assertEqual(r.status_code, 200,
                    f"[API-033] {route}: expected 200, got {r.status_code}")
                body = r.json()
                self.assertIn("elapsed_ms", body,
                    f"[API-033] {route}: missing elapsed_ms in response")
                self.assertIsInstance(body["elapsed_ms"], (int, float),
                    f"[API-033] {route}: elapsed_ms not numeric")
                self.assertGreaterEqual(body["elapsed_ms"], 0,
                    f"[API-033] {route}: elapsed_ms < 0")
                self.assertLess(body["elapsed_ms"], 10000,
                    f"[API-033] {route}: elapsed_ms >= 10s (statement_timeout=5000 should have fired)")

    def test_elapsed_ms_in_empty_result(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?date=1900-01-01",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        self.assertIn("elapsed_ms", r.json(),
            "[API-033] elapsed_ms missing from empty-result response")


class TestAPI034CountReconciliation(unittest.TestCase):
    """API-034: count field matches DB total; len(rows) ≤ limit."""

    def test_count_gte_rows_decision_audit(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?limit=5",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["count"], len(body["rows"]),
            "[API-034] decision-audit: count < len(rows)")
        self.assertLessEqual(len(body["rows"]), 5)

    def test_count_gte_rows_council_runs(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/council-runs?limit=10",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["count"], len(body["rows"]),
            "[API-034] council-runs: count < len(rows)")
        self.assertLessEqual(len(body["rows"]), 10)
        self.assertGreater(body["count"], 0,
            "[API-034] council-runs: count=0 but baseline is 219 rows")

    def test_count_gte_rows_position_sizing(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/position-sizing-log?limit=3",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["count"], len(body["rows"]))
        self.assertGreater(body["count"], 0,
            "[API-034] position-sizing-log: count=0 but baseline is 207 rows")

    def test_count_zero_means_empty_rows(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?date=1900-01-01",
            headers=AUTH, timeout=15)
        body = r.json()
        if body["count"] == 0:
            self.assertEqual(body["rows"], [],
                "[API-034] count=0 but rows is not empty")


class TestAPI021022023SchemaContract(unittest.TestCase):
    """API-021/022/023: Response fields match documented schema (aiem-terminal-openapi.yaml)."""

    def test_decision_audit_required_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?limit=1",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for field in ("count", "limit", "offset", "rows", "elapsed_ms"):
            self.assertIn(field, body,
                f"[API-022] decision-audit: missing required field '{field}'")
        self.assertIsInstance(body["count"], int)
        self.assertIsInstance(body["rows"], list)
        self.assertIsInstance(body["elapsed_ms"], (int, float))

    def test_gate_events_required_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/gate-events?limit=1",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for field in ("count", "limit", "rows", "elapsed_ms"):
            self.assertIn(field, body,
                f"[API-022] gate-events: missing required field '{field}'")

    def test_council_runs_required_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/council-runs?limit=1",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for field in ("count", "limit", "offset", "rows", "elapsed_ms"):
            self.assertIn(field, body,
                f"[API-022] council-runs: missing required field '{field}'")

    def test_position_sizing_required_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/position-sizing-log?limit=1",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for field in ("count", "limit", "rows", "elapsed_ms"):
            self.assertIn(field, body,
                f"[API-022] position-sizing-log: missing required field '{field}'")

    def test_evidence_chain_required_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/evidence-chain/status",
            headers=AUTH, timeout=15)
        self.assertIn(r.status_code, (200, 404),
            f"[API-022] evidence-chain: unexpected status {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            for field in ("seq", "last_command", "last_exit_code",
                          "last_timestamp_utc", "last_entry_hash", "total_entries"):
                self.assertIn(field, body,
                    f"[API-022] evidence-chain: missing required field '{field}'")

    def test_error_response_has_code_field(self):
        r = requests.get(f"{BASE}/stock-api/admin/decision-audit", timeout=10)
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertIn("error", body, "[API-018] 403 response missing 'error'")
        self.assertIn("code", body, "[API-018] 403 response missing 'code'")

    def test_decision_audit_row_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?limit=1",
            headers=AUTH, timeout=15)
        body = r.json()
        if body["rows"]:
            row = body["rows"][0]
            for field in ("decision_id", "created_at", "verification_status"):
                self.assertIn(field, row,
                    f"[API-022] decision-audit row: missing field '{field}'")

    def test_council_run_row_fields(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/council-runs?limit=1",
            headers=AUTH, timeout=15)
        body = r.json()
        if body["rows"]:
            row = body["rows"][0]
            for field in ("id", "run_time", "context", "ticker"):
                self.assertIn(field, row,
                    f"[API-022] council-runs row: missing field '{field}'")


class TestAPI035LiveEndpoints(unittest.TestCase):
    """API-035: All 5 Terminal routes + health respond live. Scope: Terminal routes only."""

    def test_health_public(self):
        r = requests.get(f"{BASE}/stock-api/health", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok",
            "[API-035] /stock-api/health: status != ok")

    def test_decision_audit_200(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/decision-audit?limit=5",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200,
            f"[API-035] decision-audit: got {r.status_code}")

    def test_gate_events_200(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/gate-events?limit=5",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200,
            f"[API-035] gate-events: got {r.status_code}")

    def test_council_runs_200(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/council-runs?limit=5",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200,
            f"[API-035] council-runs: got {r.status_code}")

    def test_position_sizing_log_200(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/position-sizing-log?limit=5",
            headers=AUTH, timeout=15)
        self.assertEqual(r.status_code, 200,
            f"[API-035] position-sizing-log: got {r.status_code}")

    def test_evidence_chain_status(self):
        r = requests.get(
            f"{BASE}/stock-api/admin/evidence-chain/status",
            headers=AUTH, timeout=15)
        self.assertIn(r.status_code, (200, 404),
            f"[API-035] evidence-chain/status: unexpected {r.status_code}")


if __name__ == "__main__":
    if not TOKEN:
        print("WARNING: ADMIN_TOKEN not set — auth tests will fail", file=sys.stderr)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
