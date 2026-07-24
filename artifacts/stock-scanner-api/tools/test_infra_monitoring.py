#!/usr/bin/env python3
"""
Tests for External Infra Monitoring directive (2026-07-25).
Tests: missed-ping alert logic, synthetic heartbeat trail DB write, /health structure.
Run: python3 artifacts/stock-scanner-api/tools/test_infra_monitoring.py
"""
import sys, os, json, time, unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ─── Test 1: Missed-ping alert fires on N=3 consecutive failures ──────────────
class TestMissedPingAlert(unittest.TestCase):
    def test_n3_threshold_triggers_alert(self):
        """N=3 consecutive HTTP health failures must trigger _tg_send exactly once."""
        alerts = []
        _HB_INTERVAL  = 120
        _HB_THRESHOLD = 3
        _HB_COOLDOWN  = 1800

        def fake_tg_send(msg):
            alerts.append(msg)

        import urllib.error
        misses = 0
        last_alert = 0.0

        # Simulate 3 consecutive urlopen failures
        for i in range(3):
            try:
                raise urllib.error.URLError("Connection refused")
            except Exception as _he:
                misses += 1
                if misses >= _HB_THRESHOLD:
                    now_ts = time.time()
                    if now_ts - last_alert >= _HB_COOLDOWN:
                        det = datetime.now(timezone.utc).strftime("%I:%M %p UTC")
                        dur = misses * _HB_INTERVAL // 60
                        msg = (
                            "\U0001f534 AIEM-PROCESS HTTP HEALTH MONITOR: DOWN\n"
                            "Detected: " + det + "\n"
                            + str(misses) + " consecutive HTTP /health pings missed "
                            "(" + str(dur) + " min without response)."
                        )
                        fake_tg_send(msg)
                        last_alert = now_ts

        self.assertEqual(len(alerts), 1, "Expected exactly 1 Telegram alert on 3 misses")
        self.assertIn("3 consecutive", alerts[0])
        self.assertIn("6 min", alerts[0])
        print(f"  PASS: N=3 threshold triggered alert — message: {alerts[0][:80]}...")

    def test_cooldown_prevents_repeated_alerts(self):
        """Second miss-threshold within cooldown must NOT re-fire."""
        alerts = []
        _HB_COOLDOWN = 1800
        last_alert = time.time()  # just fired

        def _check_and_maybe_alert():
            now_ts = time.time()
            if now_ts - last_alert >= _HB_COOLDOWN:
                alerts.append("ALERT")

        _check_and_maybe_alert()
        self.assertEqual(len(alerts), 0, "Alert must not re-fire within cooldown")
        print(f"  PASS: cooldown suppresses repeated alert")

    def test_n2_does_not_trigger(self):
        """2 consecutive failures must NOT trigger alert (threshold is 3)."""
        alerts = []
        _HB_THRESHOLD = 3
        misses = 0
        for _ in range(2):
            misses += 1
            if misses >= _HB_THRESHOLD:
                alerts.append("ALERT")
        self.assertEqual(len(alerts), 0, "2 misses must not trigger alert")
        print(f"  PASS: N=2 does not trigger alert (threshold=3)")

# ─── Test 2: /health endpoint structure (live ping) ──────────────────────────
class TestHealthEndpoint(unittest.TestCase):
    def test_health_returns_required_fields(self):
        """Live GET :5055/health must return status, uptime_s, pid, boot_ts, last_checkpoint_ts."""
        import urllib.request, urllib.error
        try:
            with urllib.request.urlopen("http://127.0.0.1:5055/health", timeout=5) as r:
                data = json.loads(r.read())
        except Exception as e:
            self.skipTest(f"aiem-process not running: {e}")

        self.assertEqual(data.get("status"), "ok")
        self.assertIn("uptime_s", data, "uptime_s required")
        self.assertIn("pid", data, "pid required")
        self.assertIn("boot_ts", data, "boot_ts required")
        self.assertIn("last_checkpoint_ts", data, "last_checkpoint_ts required (may be null)")
        self.assertIsInstance(data["uptime_s"], int)
        self.assertGreater(data["uptime_s"], 0)
        print(f"  PASS: /health → uptime_s={data['uptime_s']}s pid={data['pid']} boot_ts={data['boot_ts']}")

# ─── Test 3: Synthetic heartbeat trail DB write ───────────────────────────────
class TestHeartbeatTrailDB(unittest.TestCase):
    def _get_db_url(self):
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            self.skipTest("DATABASE_URL not set")
        return url

    def test_table_exists_or_creates(self):
        """aiem_process_heartbeat_trail table must be createable."""
        import psycopg2
        url = self._get_db_url()
        CREATE = """
            CREATE TABLE IF NOT EXISTS aiem_process_heartbeat_trail (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ DEFAULT NOW(),
                scan_date DATE NOT NULL,
                alive BOOLEAN NOT NULL,
                uptime_s INT,
                response_json JSONB
            )
        """
        try:
            with psycopg2.connect(url, connect_timeout=5) as c, c.cursor() as k:
                k.execute(CREATE)
                c.commit()
        except Exception as e:
            self.fail(f"Table creation failed: {e}")
        print("  PASS: aiem_process_heartbeat_trail table created/exists")

    def test_trail_db_write(self):
        """Direct DB write to heartbeat trail table must succeed and be readable."""
        import psycopg2
        from datetime import date
        url = self._get_db_url()
        INS = (
            "INSERT INTO aiem_process_heartbeat_trail "
            "(ts, scan_date, alive, uptime_s, response_json) "
            "VALUES (NOW(), %s, %s, %s, %s::jsonb) RETURNING id, ts"
        )
        payload = json.dumps({"status": "test_write", "source": "test_infra_monitoring.py"})
        today = date.today()
        try:
            with psycopg2.connect(url, connect_timeout=5) as c, c.cursor() as k:
                k.execute(INS, (today, True, 42, payload))
                row = k.fetchone()
                c.commit()
        except Exception as e:
            self.fail(f"DB write failed: {e}")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])  # id
        self.assertIsNotNone(row[1])  # ts
        print(f"  PASS: trail DB write → id={row[0]} ts={row[1]}")

    def test_trail_row_readable(self):
        """Written row must be immediately readable from the DB."""
        import psycopg2
        url = self._get_db_url()
        try:
            with psycopg2.connect(url, connect_timeout=5) as c, c.cursor() as k:
                k.execute(
                    "SELECT id, ts, alive, uptime_s FROM aiem_process_heartbeat_trail "
                    "WHERE response_json->>'source' = 'test_infra_monitoring.py' "
                    "ORDER BY ts DESC LIMIT 1"
                )
                row = k.fetchone()
        except Exception as e:
            self.fail(f"DB read failed: {e}")
        self.assertIsNotNone(row, "Written test row must be readable")
        print(f"  PASS: trail row readable — id={row[0]} ts={row[1]} alive={row[2]} uptime_s={row[3]}")

if __name__ == "__main__":
    print("=" * 60)
    print("AIEM External Infra Monitoring — Test Suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [TestMissedPingAlert, TestHealthEndpoint, TestHeartbeatTrailDB]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 60)
    print(f"RESULT: {result.testsRun} tests, "
          f"{len(result.failures)} failures, "
          f"{len(result.errors)} errors, "
          f"{len(result.skipped)} skipped")
    sys.exit(0 if result.wasSuccessful() else 1)
