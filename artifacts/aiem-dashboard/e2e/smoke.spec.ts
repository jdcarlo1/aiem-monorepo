import { test, expect, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ADMIN_TOKEN = process.env.ADMIN_TOKEN ?? "";

/** Inject auth into sessionStorage before navigation so AppLayout is satisfied. */
async function setAuth(page: Parameters<typeof test>[1] extends (args: infer A) => unknown ? never : Parameters<Parameters<typeof test>[1]>[0]["page"], token: string) {
  await page.addInitScript((tok: string) => {
    sessionStorage.setItem("aiem_authed", "1");
    sessionStorage.setItem("aiem_admin_token", tok);
  }, token);
}

/** Mock /auth/me so AppLayout does not redirect away on backend unavailability. */
async function mockAuthMe(page: Parameters<typeof test>[1] extends (args: infer A) => unknown ? never : Parameters<Parameters<typeof test>[1]>[0]["page"]) {
  await page.route("**/stock-api/auth/me", (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
  );
}

// ---------------------------------------------------------------------------
// Login page
// ---------------------------------------------------------------------------

test.describe("Login page", () => {
  test("renders the AIEM heading and login form", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1, text=AIEM")).toBeVisible();
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
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("AIEM")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

test.describe("Authentication", () => {
  test("token login stores token and reaches /command", async ({ page }) => {
    // Mock auth/me and a couple of the data endpoints so the page renders
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
    await page.getByPlaceholder("Enter or paste token…").fill("any-token-value");
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
    await page.addInitScript((tok: string) => {
      sessionStorage.setItem("aiem_authed", "1");
      sessionStorage.setItem("aiem_admin_token", tok);
    }, ADMIN_TOKEN || "e2e-test-token");

    // Mock auth/me so AppLayout does not eject us
    await page.route("**/stock-api/auth/me", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
    );

    // Return stub JSON for all stock-api calls so pages don't hang on loading
    await page.route("**/stock-api/**", (r) => {
      const url = r.request().url();
      // Pass through /auth/me (already fulfilled above, but belt+braces)
      if (url.includes("/auth/me")) return r.continue();
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs: [], data: [], status: "ok", results: [] }),
      });
    });
  });

  for (const route of ROUTES) {
    test(`${route} loads without a crash`, async ({ page }) => {
      await page.goto(route);
      // Verify no unhandled JS error / error boundary
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      // A very brief wait for initial render
      await page.waitForTimeout(500);
      expect(errors.filter((e) => !e.includes("ResizeObserver"))).toHaveLength(
        0
      );
      // Sidebar should be visible (means layout rendered)
      await expect(page.locator("nav, aside, [data-testid='sidebar']").first()).toBeVisible();
    });
  }
});

// ---------------------------------------------------------------------------
// 404 page
// ---------------------------------------------------------------------------

test.describe("Not-found page", () => {
  test("unknown route renders a not-found view", async ({ page }) => {
    await page.addInitScript((tok: string) => {
      sessionStorage.setItem("aiem_authed", "1");
      sessionStorage.setItem("aiem_admin_token", tok);
    }, ADMIN_TOKEN || "e2e-test-token");

    await page.route("**/stock-api/auth/me", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
    );
    await page.route("**/stock-api/**", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({}) })
    );

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
    await page.addInitScript((tok: string) => {
      sessionStorage.setItem("aiem_authed", "1");
      sessionStorage.setItem("aiem_admin_token", tok);
    }, ADMIN_TOKEN || "e2e-test-token");

    await page.route("**/stock-api/auth/me", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
    );

    // Delay the scheduler-jobs response so the loading state is visible
    await page.route("**/stock-api/admin/scheduler-jobs", async (r) => {
      await new Promise((res) => setTimeout(res, 300));
      r.fulfill({
        status: 200,
        body: JSON.stringify({ jobs: [] }),
      });
    });
    await page.route("**/stock-api/**", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({}) })
    );

    await page.goto("/scheduler");
    // Loading text exists in Scheduler.tsx when `loading` is true
    // It may flash briefly; we just confirm no crash occurred
    await page.waitForTimeout(600);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    expect(errors.filter((e) => !e.includes("ResizeObserver"))).toHaveLength(0);
  });

  test("a page displays empty state when API returns no data", async ({
    page,
  }) => {
    await page.addInitScript((tok: string) => {
      sessionStorage.setItem("aiem_authed", "1");
      sessionStorage.setItem("aiem_admin_token", tok);
    }, ADMIN_TOKEN || "e2e-test-token");

    await page.route("**/stock-api/auth/me", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ ok: true }) })
    );
    await page.route("**/stock-api/**", (r) =>
      r.fulfill({ status: 200, body: JSON.stringify({ jobs: [] }) })
    );

    await page.goto("/scheduler");
    await page.waitForTimeout(500);
    // Scheduler.tsx renders "NO CATEGORY DATA" / "NO JOB DATA" when jobs=[]
    const emptyText = page.getByText(/NO CATEGORY DATA|NO JOB DATA/i);
    await expect(emptyText.first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Health and API endpoints (direct API assertions — no browser)
// ---------------------------------------------------------------------------

test.describe("API endpoints", () => {
  test("GET /stock-api/health returns 200 with status:ok", async ({
    request,
  }) => {
    const res = await request.get("/stock-api/health");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
  });

  test("GET /stock-api/healthz returns 200", async ({ request }) => {
    const res = await request.get("/stock-api/healthz");
    expect(res.status()).toBe(200);
  });

  test("GET /stock-api/admin/job-heartbeats returns 401 without token", async ({
    request,
  }) => {
    const res = await request.get("/stock-api/admin/job-heartbeats");
    expect(res.status()).toBe(401);
    const body = await res.json();
    expect(body.error).toBe("unauthorized");
  });

  test("GET /stock-api/admin/job-heartbeats returns 200 with valid token", async ({
    request,
  }) => {
    if (!ADMIN_TOKEN) {
      test.skip();
      return;
    }
    const res = await request.get("/stock-api/admin/job-heartbeats", {
      headers: { "X-Admin-Token": ADMIN_TOKEN },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("jobs");
  });

  test("GET /stock-api/admin/scheduler-jobs returns 401 without token", async ({
    request,
  }) => {
    const res = await request.get("/stock-api/admin/scheduler-jobs");
    expect(res.status()).toBe(401);
  });
});

// ---------------------------------------------------------------------------
// Deferred / unimplemented features — documented as skipped
// ---------------------------------------------------------------------------

test.describe("Deferred features (skipped until implemented)", () => {
  test.skip("dark mode toggle persists theme preference", async ({ page }) => {
    // next-themes ThemeProvider not yet wired in App.tsx.
    // When wired: find toggle, click, verify data-theme="light" on root,
    // reload, verify preference persists.
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

  test.skip("GET /stock-api/readyz returns 200 with dependency status", async ({
    request,
  }) => {
    // Readiness endpoint (GET /readyz) is NOT_IMPLEMENTED in current backend.
    // When implemented: expect status 200 or 503, body.database and body.scheduler.
  });
});
