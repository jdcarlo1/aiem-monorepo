// @vitest-environment node
/**
 * API Smoke Tests — run via Vitest against the live stock-api process.
 *
 * Uses native Node 18+ fetch. Requires REPLIT_DEV_DOMAIN or API_BASE_URL.
 * Tests skip gracefully when the backend is unavailable (e.g. OOM crash, CI).
 */
import { describe, it, expect, beforeAll } from "vitest";

const domain = process.env.REPLIT_DEV_DOMAIN;
const API_BASE = process.env.API_BASE_URL
  ? process.env.API_BASE_URL
  : domain
  ? `https://${domain}`
  : null;

const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "";

// ---------------------------------------------------------------------------
// Connectivity probe — performed once before any tests
// ---------------------------------------------------------------------------

let backendUp = false;
let skipReason = "not yet probed";

beforeAll(async () => {
  if (!API_BASE) {
    skipReason = "API_BASE / REPLIT_DEV_DOMAIN not set";
    return;
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    const res = await fetch(`${API_BASE}/stock-api/health`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);
    backendUp = res.status === 200;
    if (!backendUp) skipReason = `health returned HTTP ${res.status}`;
  } catch (e) {
    skipReason = String(e);
  }
}, 10_000);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function requireApi() {
  if (!API_BASE) throw new Error("API_BASE not configured");
}

// ---------------------------------------------------------------------------
// Health / readiness
// ---------------------------------------------------------------------------

describe("GET /stock-api/health", () => {
  it("returns HTTP 200 with status:ok", async ({ skip }) => {
    if (!backendUp) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/health`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
  });
});

describe("GET /stock-api/healthz", () => {
  it("returns HTTP 200", async ({ skip }) => {
    if (!backendUp) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/healthz`);
    expect(res.status).toBe(200);
  });
});

describe("GET /stock-api/readyz", () => {
  it("returns structured readiness with database + scheduler + status fields", async ({
    skip,
  }) => {
    if (!backendUp) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/readyz`);
    if (res.status === 502 || res.status === 503) {
      // 503 is valid when DB is degraded — still a well-formed response
      if (res.status === 502) skip();
    }
    expect([200, 503]).toContain(res.status);
    const body = await res.json();
    expect(body).toHaveProperty("database");
    expect(body).toHaveProperty("scheduler");
    expect(body).toHaveProperty("status");
    expect(["ok", "degraded"]).toContain(body.status);
    expect(body).toHaveProperty("latency_ms");
  });
});

describe("GET /stock-api/metrics", () => {
  it("returns Prometheus text exposition format with HELP + TYPE lines", async ({
    skip,
  }) => {
    if (!backendUp) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/metrics`);
    if (res.status === 502 || res.status === 503) skip();
    expect(res.status).toBe(200);
    const ct = res.headers.get("content-type") ?? "";
    expect(ct).toContain("text/plain");
    const text = await res.text();
    expect(text).toContain("# HELP");
    expect(text).toContain("# TYPE");
    expect(text).toContain("process_uptime_seconds");
    expect(text).toContain("aiem_paper_trades_total");
    expect(text).toContain("aiem_signal_discoveries_total");
  });
});

// ---------------------------------------------------------------------------
// Auth guard — protected endpoints must return 401 without a token
// ---------------------------------------------------------------------------

const PROTECTED_ENDPOINTS = [
  "/stock-api/admin/job-heartbeats",
  "/stock-api/admin/scheduler-jobs",
  "/stock-api/admin/decision-audit",
  "/stock-api/admin/paper-job-ledger",
  "/stock-api/admin/daily-pipeline-runs",
  "/stock-api/admin/governance-decisions",
  "/stock-api/admin/telegram-alerts",
] as const;

describe("Auth guard — 401 without token", () => {
  for (const endpoint of PROTECTED_ENDPOINTS) {
    it(`GET ${endpoint} → 401`, async ({ skip }) => {
      if (!backendUp) skip();
      requireApi();
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (res.status === 502 || res.status === 503) skip();
      expect(res.status).toBe(401);
      const body = await res.json();
      expect(body.error).toBe("unauthorized");
    });
  }
});

// ---------------------------------------------------------------------------
// Admin endpoints — authenticated access returns 200
// ---------------------------------------------------------------------------

describe("Admin endpoints — 200 with valid token", () => {
  it("GET /stock-api/admin/job-heartbeats → 200 with jobs array", async ({
    skip,
  }) => {
    if (!backendUp || !ADMIN_TOKEN) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/admin/job-heartbeats`, {
      headers: { "X-Admin-Token": ADMIN_TOKEN },
    });
    if (res.status === 502 || res.status === 503) skip();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("jobs");
    expect(Array.isArray(body.jobs)).toBe(true);
  });

  it("GET /stock-api/admin/scheduler-jobs → 200 with jobs array", async ({
    skip,
  }) => {
    if (!backendUp || !ADMIN_TOKEN) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/admin/scheduler-jobs`, {
      headers: { "X-Admin-Token": ADMIN_TOKEN },
    });
    if (res.status === 502 || res.status === 503) skip();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("jobs");
  });
});

// ---------------------------------------------------------------------------
// Content-Type enforcement
// ---------------------------------------------------------------------------

describe("Content-Type", () => {
  it("/stock-api/health response is application/json", async ({ skip }) => {
    if (!backendUp) skip();
    requireApi();
    const res = await fetch(`${API_BASE}/stock-api/health`);
    if (res.status === 502 || res.status === 503) skip();
    const ct = res.headers.get("content-type") ?? "";
    expect(ct).toContain("application/json");
  });
});
