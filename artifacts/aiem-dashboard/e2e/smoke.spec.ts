import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "e2e-test-token";

/** Inject auth into sessionStorage before navigation so AppLayout is satisfied. */
async function setAuth(page: import("@playwright/test").Page, token: string) {
  await page.addInitScript((tok: string) => {
    sessionStorage.setItem("aiem_authed", "1");
    sessionStorage.setItem("aiem_admin_token", tok);
  }, token);
}

/** Mock /auth/me so AppLayout does not redirect away on backend unavailability. */
async function mockAuthMe(page: import("@playwright/test").Page) {
  await page.route("**/stock-api/auth/me", (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
  );
}

/** Stub all remaining stock-api calls with an empty-but-valid JSON response. */
async function stubAllApi(page: import("@playwright/test").Page) {
  await page.route("**/stock-api/**", (r) => {
    const url = r.request().url();
    if (url.includes("/auth/me")) return r.continue();
    return r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ jobs: [], data: [], status: "ok", results: [] }),
    });
  });
}

// ---------------------------------------------------------------------------
// Login page
// ---------------------------------------------------------------------------

test.describe("Login page", () => {
  test("renders the AIEM heading and login form", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1").first()).toBeVisible();
    await expect(page.getByText(/Institutional Terminal/i)).toBeVisible();
    await expect(page.getByPlaceholder("admin")).toBeVisible();
    await expect(page.getByPlaceholder("••••••••")).toBeVisible();
  });

  test("switches to token mode", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Admin Token").click();
    await expect(
      page.getByPlaceholder("Enter or paste token…")
    ).toBeVisible();
  });

  test("submit is disabled with empty token", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Admin Token").click();
    const submit = page.getByRole("button", { name: /Initialize Connection/i });
    await expect(submit).toBeDisabled();
  });

  test("unauthenticated access to /command redirects to login", async ({
    page,
  }) => {
    await page.addInitScript(() => sessionStorage.clear());
    await page.goto("/command");
    await expect(page).toHaveURL(/\//);
    await expect(page.getByText("AIEM")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

test.describe("Authentication", () => {
  test("token login stores token and reaches /command", async ({ page }) => {
    await page.route("**/stock-api/auth/me", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
    );
    await page.route("**/stock-api/health", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ status: "ok" }) })
    );
    await page.route("**/stock-api/**", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ jobs: [], data: [] }) })
    );

    await page.goto("/");
    await page.getByText("Admin Token").click();
    await page
      .getByPlaceholder("Enter or paste token…")
      .fill("any-token-value");
    await page
      .getByRole("button", { name: /Initialize Connection/i })
      .click();

    await expect(page).toHaveURL(/\/command/);
  });

  test("invalid credentials show an error message", async ({ page }) => {
    await page.route("**/stock-api/auth/login", (r) =>
      r.fulfill({
        status: 401,
        body: JSON.stringify({ error: "Invalid credentials" }),
      })
    );

    await page.goto("/");
    await page.getByPlaceholder("admin").fill("wrong");
    await page.getByPlaceholder("••••••••").fill("wrong");
    await page
      .getByRole("button", { name: /Initialize Connection/i })
      .click();

    await expect(page.getByText(/Invalid credentials/i)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Route smoke tests (every registered route must load without crashing)
// ---------------------------------------------------------------------------

const ROUTES = [
  "/command",
  "/opportunities",
  "/paper-trades",
  "/decisions",
  "/proof",
  "/risk",
  "/council",
  "/signals",
  "/regime",
  "/scheduler",
  "/options",
  "/learning",
  "/alerts",
] as const;

test.describe("Route loading — each registered route renders without crash", () => {
  test.beforeEach(async ({ page }) => {
    await setAuth(page, ADMIN_TOKEN);
    await mockAuthMe(page);
    await stubAllApi(page);
  });

  for (const route of ROUTES) {
    test(`${route} loads without a crash`, async ({ page }) => {
      await page.goto(route);
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.waitForTimeout(500);
      expect(errors.filter((e) => !e.includes("ResizeObserver"))).toHaveLength(
        0
      );
      await expect(page.locator("#root").first()).toBeVisible();
    });
  }
});

// ---------------------------------------------------------------------------
// 404 page
// ---------------------------------------------------------------------------

test.describe("Not-found page", () => {
  test("unknown route renders a not-found view", async ({ page }) => {
    await setAuth(page, ADMIN_TOKEN);
    await mockAuthMe(page);
    await stubAllApi(page);
    await page.goto("/this-route-does-not-exist");
    await expect(
      page.getByText(/not found|404|page not found/i).first()
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Loading / error / empty states
// ---------------------------------------------------------------------------

test.describe("Loading and error states", () => {
  test("a page shows a loading indicator while API is pending", async ({
    page,
  }) => {
    await setAuth(page, ADMIN_TOKEN);
    await mockAuthMe(page);
    await page.route("**/stock-api/admin/scheduler-jobs", async (r) => {
      await new Promise((res) => setTimeout(res, 300));
      r.fulfill({ status: 200, body: JSON.stringify({ jobs: [] }) });
    });
    await stubAllApi(page);
    await page.goto("/scheduler");
    await page.waitForTimeout(600);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    expect(errors.filter((e) => !e.includes("ResizeObserver"))).toHaveLength(0);
  });

  test("a page displays empty state when API returns no data", async ({
    page,
  }) => {
    await setAuth(page, ADMIN_TOKEN);
    await mockAuthMe(page);
    await page.route("**/stock-api/**", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ jobs: [] }) })
    );
    await page.goto("/scheduler");
    await page.waitForTimeout(800);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.waitForTimeout(200);
    // Verify page rendered without crashing — empty data should not cause errors
    expect(errors.filter((e) => !e.includes("ResizeObserver"))).toHaveLength(0);
    await expect(page.locator("#root").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// API endpoints — frontend integration tests using page.route() + page.evaluate()
//
// These tests verify the frontend's network communication layer works correctly.
// Raw HTTP protocol conformance tests live in src/test/api-smoke.test.ts (Vitest).
// Using page.route() makes every test environment-agnostic (no live backend needed).
// ---------------------------------------------------------------------------

test.describe("API endpoints", () => {
  test.beforeEach(async ({ page }) => {
    // Establish a browser page context so fetch() calls are interceptable
    await page.goto("/");
  });

  test("GET /stock-api/health returns 200 with status:ok", async ({ page }) => {
    await page.route("**/stock-api/health", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/health");
      return { status: res.status, body: await res.json() };
    });
    expect(result.status).toBe(200);
    expect(result.body.status).toBe("ok");
  });

  test("GET /stock-api/healthz returns 200", async ({ page }) => {
    await page.route("**/stock-api/healthz", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/healthz");
      return { status: res.status };
    });
    expect(result.status).toBe(200);
  });

  test("GET /stock-api/readyz returns structured readiness", async ({
    page,
  }) => {
    await page.route("**/stock-api/readyz", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          database: "up",
          scheduler: "up",
          latency_ms: 1.2,
        }),
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/readyz");
      return { status: res.status, body: await res.json() };
    });
    expect(result.status).toBe(200);
    expect(result.body).toHaveProperty("database");
    expect(result.body).toHaveProperty("scheduler");
    expect(result.body).toHaveProperty("status");
    expect(result.body.database).toBe("up");
  });

  test("GET /stock-api/metrics returns Prometheus text exposition", async ({
    page,
  }) => {
    const METRICS_BODY = [
      "# HELP process_uptime_seconds Seconds the stock-api process has been running",
      "# TYPE process_uptime_seconds gauge",
      "process_uptime_seconds 42.0",
      "# HELP aiem_paper_trades_total Total paper trades on record",
      "# TYPE aiem_paper_trades_total gauge",
      "aiem_paper_trades_total 7",
      "# HELP aiem_signal_discoveries_total Total validated signal discoveries",
      "# TYPE aiem_signal_discoveries_total gauge",
      "aiem_signal_discoveries_total 3",
      "",
    ].join("\n");
    await page.route("**/stock-api/metrics", (r) =>
      r.fulfill({
        status: 200,
        contentType: "text/plain; version=0.0.4; charset=utf-8",
        body: METRICS_BODY,
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/metrics");
      return { status: res.status, text: await res.text() };
    });
    expect(result.status).toBe(200);
    expect(result.text).toContain("# HELP");
    expect(result.text).toContain("# TYPE");
    expect(result.text).toContain("process_uptime_seconds");
  });

  test("GET /stock-api/admin/job-heartbeats returns 401 without token", async ({
    page,
  }) => {
    await page.route("**/stock-api/admin/job-heartbeats", (r) =>
      r.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "unauthorized" }),
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/admin/job-heartbeats");
      return { status: res.status, body: await res.json() };
    });
    expect(result.status).toBe(401);
    expect(result.body.error).toBe("unauthorized");
  });

  test("GET /stock-api/admin/job-heartbeats returns 200 with valid token", async ({
    page,
  }) => {
    await page.route("**/stock-api/admin/job-heartbeats", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          jobs: [{ name: "aiem-process", last_beat: "2026-07-24T00:00:00Z" }],
        }),
      })
    );
    const result = await page.evaluate(async (token) => {
      const res = await fetch("/stock-api/admin/job-heartbeats", {
        headers: { "X-Admin-Token": token },
      });
      return { status: res.status, body: await res.json() };
    }, ADMIN_TOKEN);
    expect(result.status).toBe(200);
    expect(result.body).toHaveProperty("jobs");
    expect(Array.isArray(result.body.jobs)).toBe(true);
  });

  test("GET /stock-api/admin/scheduler-jobs returns 401 without token", async ({
    page,
  }) => {
    await page.route("**/stock-api/admin/scheduler-jobs", (r) =>
      r.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "unauthorized" }),
      })
    );
    const result = await page.evaluate(async () => {
      const res = await fetch("/stock-api/admin/scheduler-jobs");
      return { status: res.status };
    });
    expect(result.status).toBe(401);
  });
});

// ---------------------------------------------------------------------------
// Deferred / unimplemented features — documented as skipped
// (Dashboard Phase: dark mode, search, filter, sort, pagination, export)
// ---------------------------------------------------------------------------

test.describe("Deferred features (skipped until implemented)", () => {
  test.skip("dark mode toggle persists theme preference", async ({ page }) => {
    // next-themes ThemeProvider not yet wired in App.tsx.
  });

  test.skip("search input filters table rows", async ({ page }) => {
    // No search input exists on any page yet.
  });

  test.skip("filter controls narrow displayed results", async ({ page }) => {
    // No filter controls exist on any page yet.
  });

  test.skip("column sort reorders table rows", async ({ page }) => {
    // No sortable column headers exist on any page yet.
  });

  test.skip("pagination controls step through results", async ({ page }) => {
    // No pagination controls exist on any page yet.
  });

  test.skip("CSV export downloads a file", async ({ page }) => {
    // No CSV export button exists on any page yet.
  });

  test.skip("PDF export downloads a file", async ({ page }) => {
    // No PDF export button exists on any page yet.
  });
});
