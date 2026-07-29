/**
 * Admin routes and StockScanner product routes in stripe.ts — full coverage.
 *
 * These are all reachable HTTP endpoints in the same Express router.  The
 * StockScanner routes serve a different product but are NOT dead code; every
 * Stripe operation is covered through mockStripe, no real account needed.
 *
 * Routes tested:
 *   POST /admin/seed-questions   (lines 216–235)
 *   POST /admin/fix-sessions     (lines 238–248)
 *   POST /admin/activate-sessions (lines 328–340)
 *   POST /stock-scanner/checkout  (lines 252–302)
 *   POST /stock-scanner/manage    (lines 304–325)
 */

import { describe, it, expect, vi, beforeEach, beforeAll, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks ──────────────────────────────────────────────────────────────

vi.mock("express-rate-limit", () => ({
  default: vi.fn(() => (_req: any, _res: any, next: any) => next()),
  rateLimit: vi.fn(() => (_req: any, _res: any, next: any) => next()),
}));

vi.mock("@workspace/db", () => ({
  db: {
    select: vi.fn(),
    insert: vi.fn(),
    update: vi.fn(),
    transaction: vi.fn(),
    execute: vi.fn().mockResolvedValue({ rows: [] }),
  },
  sessionsTable: {},
  answersTable: {},
  questionsTable: {},
  sessionClaimsTable: {},
  affiliatesTable: {},
}));

vi.mock("drizzle-orm", () => ({
  eq: vi.fn((col: unknown, val: unknown) => ({ col, val })),
  sql: vi.fn(),
  and: vi.fn(),
  isNull: vi.fn(),
  inArray: vi.fn(),
  notInArray: vi.fn(),
}));

const mockStripe = {
  customers: { create: vi.fn(), update: vi.fn(), list: vi.fn() },
  products: { search: vi.fn(), list: vi.fn() },
  prices: { list: vi.fn() },
  checkout: {
    sessions: { create: vi.fn(), retrieve: vi.fn(), list: vi.fn(), search: vi.fn() },
  },
  subscriptions: { cancel: vi.fn() },
  accounts: { retrieve: vi.fn() },
  transfers: { create: vi.fn() },
  billingPortal: { sessions: { create: vi.fn() } },
};

vi.mock("../stripeClient", () => ({
  getUncachableStripeClient: vi.fn(),
  getStripeSync: vi.fn().mockResolvedValue({ processWebhook: vi.fn() }),
}));

vi.mock("@clerk/express", () => ({
  clerkMiddleware: vi.fn(() => (_req: any, _res: any, next: any) => next()),
  getAuth: vi.fn().mockReturnValue({ userId: null }),
}));

vi.mock("@clerk/shared/keys", () => ({
  publishableKeyFromHost: vi.fn().mockReturnValue("pk_test_mock"),
}));

vi.mock("../middlewares/clerkProxyMiddleware", () => ({
  CLERK_PROXY_PATH: "/__clerk_proxy",
  clerkProxyMiddleware: vi.fn(() => (_req: any, _res: any, next: any) => next()),
  getClerkProxyHost: vi.fn().mockReturnValue(""),
}));

// ── Imports after mocks ───────────────────────────────────────────────────────

import { db } from "@workspace/db";
import { getUncachableStripeClient } from "../stripeClient";
import app from "../app";

// ── Constants and helpers ─────────────────────────────────────────────────────

// requireAdmin checks req.headers["x-admin-secret"] against process.env.ADMIN_TOKEN.
// Both values are read at call time, so setting the env var in beforeAll is sufficient.
const ADMIN_TOKEN_VALUE = "test-admin-secret-xyz";
const ADMIN_HEADER = { "x-admin-secret": ADMIN_TOKEN_VALUE };

beforeAll(() => {
  process.env.ADMIN_TOKEN = ADMIN_TOKEN_VALUE;
});

function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

function updateOnce() {
  (db.update as Mock).mockReturnValueOnce({
    set: vi.fn().mockReturnValue({
      where: vi.fn().mockResolvedValue(undefined),
    }),
  });
}

/**
 * For routes that use .returning() on the update chain:
 *   db.update().set().where().returning()
 */
function updateWithReturningOnce(returnedRows: any[] = []) {
  (db.update as Mock).mockReturnValueOnce({
    set: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        returning: vi.fn().mockResolvedValue(returnedRows),
      }),
    }),
  });
}

/**
 * For routes that use .values().onConflictDoNothing() on the insert chain:
 *   db.insert().values().onConflictDoNothing()
 */
function insertWithConflictOnce() {
  (db.insert as Mock).mockReturnValueOnce({
    values: vi.fn().mockReturnValue({
      onConflictDoNothing: vi.fn().mockResolvedValue(undefined),
    }),
  });
}

function stripeReady() {
  (getUncachableStripeClient as Mock).mockResolvedValue(mockStripe);
}

// ── Minimal valid question fixture ────────────────────────────────────────────

const VALID_QUESTION = {
  questionNumber: 1,
  category: "Pharmacology",
  text: "Which drug is safest?",
  options: { A: "Aspirin", B: "Warfarin" },
  correctLetter: "A",
  explanation: "Aspirin at low dose is safer for most patients.",
  questionType: "single",
  imageUrl: null,
};

// ─────────────────────────────────────────────────────────────────────────────
// POST /admin/seed-questions  (lines 216–235)
// ─────────────────────────────────────────────────────────────────────────────

describe("POST /admin/seed-questions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("no admin token → 401 Unauthorized (line 217 requireAdmin false-exit b30[1])", async () => {
    const res = await request(app)
      .post("/api/admin/seed-questions")
      .send({ questions: [VALID_QUESTION] })
      .set("Content-Type", "application/json");
    // No x-admin-secret header → requireAdmin returns false → 401

    expect(res.status).toBe(401);
    expect(db.insert).not.toHaveBeenCalled();
  });

  it("wrong admin token → 401 Unauthorized", async () => {
    const res = await request(app)
      .post("/api/admin/seed-questions")
      .send({ questions: [VALID_QUESTION] })
      .set({ "Content-Type": "application/json", "x-admin-secret": "wrong-token" });

    expect(res.status).toBe(401);
  });

  it("valid token, invalid body (empty questions array) → 400 (b31[0] line 220 validation-fail exit)", async () => {
    // SeedQuestionsBody requires questions.min(1); empty array fails validation.
    const res = await request(app)
      .post("/api/admin/seed-questions")
      .send({ questions: [] })
      .set({ "Content-Type": "application/json", ...ADMIN_HEADER });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/questions array required/i);
    expect(db.insert).not.toHaveBeenCalled();
  });

  it("valid token + valid questions array → 200, batch insert called (lines 228–232)", async () => {
    // Batch size is 50; a single question triggers one insert batch.
    insertWithConflictOnce();

    const res = await request(app)
      .post("/api/admin/seed-questions")
      .send({ questions: [VALID_QUESTION] })
      .set({ "Content-Type": "application/json", ...ADMIN_HEADER });

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.message).toMatch(/inserted 1 question/i);
    expect(db.insert).toHaveBeenCalledOnce();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /admin/fix-sessions  (lines 238–248)
// ─────────────────────────────────────────────────────────────────────────────

describe("POST /admin/fix-sessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("no admin token → 401 (line 239 requireAdmin false-exit b32[1])", async () => {
    const res = await request(app)
      .post("/api/admin/fix-sessions")
      .set("Content-Type", "application/json");

    expect(res.status).toBe(401);
    expect(db.update).not.toHaveBeenCalled();
  });

  it("valid admin token → 200 with count of fixed rows (lines 241–247)", async () => {
    // fix-sessions uses db.update().set().where().returning() — needs the
    // extended mock chain.
    updateWithReturningOnce([{ id: 5 }, { id: 12 }]); // 2 rows fixed

    const res = await request(app)
      .post("/api/admin/fix-sessions")
      .set({ "Content-Type": "application/json", ...ADMIN_HEADER });

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.fixed).toBe(2); // result.length
    expect(db.update).toHaveBeenCalledOnce();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /admin/activate-sessions  (lines 328–340)
// ─────────────────────────────────────────────────────────────────────────────

describe("POST /admin/activate-sessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("no admin token → 401 (line 329 requireAdmin false-exit b47[1])", async () => {
    const res = await request(app)
      .post("/api/admin/activate-sessions")
      .send({ sessionId: "sess-001" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(401);
    expect(db.update).not.toHaveBeenCalled();
  });

  it("valid token, body missing sessionId → 400 (b48[0] line 332 validation-fail exit)", async () => {
    const res = await request(app)
      .post("/api/admin/activate-sessions")
      .send({})
      .set({ "Content-Type": "application/json", ...ADMIN_HEADER });

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/sessionId is required/i);
    expect(db.update).not.toHaveBeenCalled();
  });

  it("valid token + sessionId → 200, session isSubscribed set to true (lines 338–339)", async () => {
    updateOnce();

    const res = await request(app)
      .post("/api/admin/activate-sessions")
      .send({ sessionId: "sess-to-activate" })
      .set({ "Content-Type": "application/json", ...ADMIN_HEADER });

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.message).toContain("sess-to-activate");

    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /stock-scanner/checkout  (lines 252–302)
// ─────────────────────────────────────────────────────────────────────────────

describe("POST /stock-scanner/checkout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("missing email → 400 (lines 253–256 b33[0] b34[0])", async () => {
    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Valid email is required/i);
  });

  it("email without '@' → 400 (lines 253–256 b34[1])", async () => {
    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "notanemail" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("StockScanner AI Pro product not found in Stripe → 500 (b35[0] lines 263–265)", async () => {
    // stripe.products.list returns products that don't include 'StockScanner AI Pro'.
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_other", name: "Other Product" }],
    });

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "buyer@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(500);
    expect(res.body.error).toBe("StockScanner AI Pro product not found.");
  });

  it("product found but price list empty → 500 (b36[0] line 269)", async () => {
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
    });
    mockStripe.prices.list.mockResolvedValue({ data: [] }); // no prices

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "buyer@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(500);
    expect(res.body.error).toBe("Subscription price not found.");
  });

  it("no existing Stripe customer → customer created, checkout URL returned (b37[1] line 275–277)", async () => {
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
    });
    mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_ss_001" }] });
    mockStripe.customers.list.mockResolvedValue({ data: [] }); // no existing customer
    mockStripe.customers.create.mockResolvedValue({ id: "cus_ss_new_001" });
    mockStripe.checkout.sessions.create.mockResolvedValue({
      url: "https://checkout.stripe.com/pay/ss_new",
    });

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "newbuyer@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.url).toMatch(/checkout\.stripe\.com/);
    expect(mockStripe.customers.create).toHaveBeenCalledOnce();

    const createArg = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createArg.mode).toBe("subscription");
    expect(createArg.metadata.product).toBe("stock-scanner");
    expect(createArg.customer).toBe("cus_ss_new_001");
  });

  it("existing Stripe customer → reused, customers.create NOT called (b37[0] line 274)", async () => {
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
    });
    mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_ss_001" }] });
    mockStripe.customers.list.mockResolvedValue({
      data: [{ id: "cus_ss_existing_001" }],
    });
    mockStripe.checkout.sessions.create.mockResolvedValue({
      url: "https://checkout.stripe.com/pay/ss_existing",
    });

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "existing@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(mockStripe.customers.create).not.toHaveBeenCalled();
    const createArg = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createArg.customer).toBe("cus_ss_existing_001");
  });

  it("valid referralCode → affiliate found, uppercased, written to checkout metadata (lines 285–298 b40[0], b41[0])", async () => {
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
    });
    mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_ss_001" }] });
    mockStripe.customers.list.mockResolvedValue({ data: [] });
    mockStripe.customers.create.mockResolvedValue({ id: "cus_ss_ref_001" });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: "https://checkout.stripe.com/pay/ref" });

    selectOnce([{ id: 1, code: "SCANNER20", commissionPct: 20 }]); // affiliate found

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "ref@example.com", referralCode: "scanner20" }) // lowercase input
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.referralCode).toBe("SCANNER20"); // uppercased in response
    const createArg = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createArg.metadata.referralCode).toBe("SCANNER20"); // in Stripe metadata
  });

  it("referralCode provided but affiliate not found → validatedCode stays null (b41[1] line 288 false branch)", async () => {
    mockStripe.products.list.mockResolvedValue({
      data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
    });
    mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_ss_001" }] });
    mockStripe.customers.list.mockResolvedValue({ data: [] });
    mockStripe.customers.create.mockResolvedValue({ id: "cus_ss_noaff_001" });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: "https://checkout.stripe.com/pay/noaff" });

    selectOnce([]); // no affiliate found

    const res = await request(app)
      .post("/api/stock-scanner/checkout")
      .send({ email: "noaff@example.com", referralCode: "BADCODE" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.referralCode).toBeNull(); // not written — affiliate not found
    const createArg = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createArg.metadata.referralCode).toBeUndefined(); // not in metadata
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /stock-scanner/manage  (lines 304–325)
// ─────────────────────────────────────────────────────────────────────────────

describe("POST /stock-scanner/manage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("missing email → 400 (line 306 b43[0] b43[1])", async () => {
    const res = await request(app)
      .post("/api/stock-scanner/manage")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Email required/i);
    expect(mockStripe.customers.list).not.toHaveBeenCalled();
  });

  it("email not found in Stripe → 404 (b44[0] lines 311–314)", async () => {
    mockStripe.customers.list.mockResolvedValue({ data: [] });

    const res = await request(app)
      .post("/api/stock-scanner/manage")
      .send({ email: "unknown@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/No subscription found/i);
    expect(mockStripe.billingPortal.sessions.create).not.toHaveBeenCalled();
  });

  it("customer found → billing portal session created, URL returned (lines 316–324)", async () => {
    mockStripe.customers.list.mockResolvedValue({
      data: [{ id: "cus_manage_001" }],
    });
    mockStripe.billingPortal.sessions.create.mockResolvedValue({
      url: "https://billing.stripe.com/session/manage_001",
    });

    const res = await request(app)
      .post("/api/stock-scanner/manage")
      .send({ email: "subscriber@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.url).toMatch(/billing\.stripe\.com/);
    expect(mockStripe.billingPortal.sessions.create).toHaveBeenCalledWith(
      expect.objectContaining({ customer: "cus_manage_001" })
    );
  });

  it("REPLIT_DOMAINS absent → ?? [] fires → domains[0] absent → ?? 'localhost' fires (b45[1] b46[1] line 316-317)", async () => {
    // const domains = process.env.REPLIT_DOMAINS?.split(",") ?? [];
    // const host = domains[0] ?? "localhost";
    // When REPLIT_DOMAINS is not set, domains=[] and host="localhost".
    const saved = process.env.REPLIT_DOMAINS;
    delete process.env.REPLIT_DOMAINS;

    try {
      mockStripe.customers.list.mockResolvedValue({
        data: [{ id: "cus_localhost_001" }],
      });
      mockStripe.billingPortal.sessions.create.mockResolvedValue({
        url: "https://billing.stripe.com/session/localhost_001",
      });

      const res = await request(app)
        .post("/api/stock-scanner/manage")
        .send({ email: "local@example.com" })
        .set("Content-Type", "application/json");

      expect(res.status).toBe(200);
      // return_url built with "localhost" as host
      const createArg = mockStripe.billingPortal.sessions.create.mock.calls[0][0];
      expect(createArg.return_url).toMatch(/localhost/);
    } finally {
      // Always restore the env var so subsequent tests are unaffected
      if (saved !== undefined) process.env.REPLIT_DOMAINS = saved;
    }
  });
});

// ── REPLIT_DOMAINS absent → null-coalescing fallback in checkout (b38[1], b39[1]) ──

describe("POST /stock-scanner/checkout — REPLIT_DOMAINS absent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("REPLIT_DOMAINS absent → ?? [] fires → domains[0] absent → ?? 'localhost' fires (b38[1] b39[1] lines 280-281)", async () => {
    // const domains = process.env.REPLIT_DOMAINS?.split(",") ?? [];
    // const host = domains[0] ?? "localhost";
    // When REPLIT_DOMAINS is not set, both right-hand-side (??) arms fire.
    const saved = process.env.REPLIT_DOMAINS;
    delete process.env.REPLIT_DOMAINS;

    try {
      mockStripe.products.list.mockResolvedValue({
        data: [{ id: "prod_ss_001", name: "StockScanner AI Pro" }],
      });
      mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_ss_001" }] });
      mockStripe.customers.list.mockResolvedValue({ data: [] });
      mockStripe.customers.create.mockResolvedValue({ id: "cus_local_001" });
      mockStripe.checkout.sessions.create.mockResolvedValue({
        url: "https://checkout.stripe.com/pay/local_001",
      });

      const res = await request(app)
        .post("/api/stock-scanner/checkout")
        .send({ email: "local@example.com" })
        .set("Content-Type", "application/json");

      expect(res.status).toBe(200);
      // success_url and cancel_url built with "localhost" as host
      const createArg = mockStripe.checkout.sessions.create.mock.calls[0][0];
      expect(createArg.success_url).toMatch(/localhost/);
      expect(createArg.cancel_url).toMatch(/localhost/);
    } finally {
      if (saved !== undefined) process.env.REPLIT_DOMAINS = saved;
    }
  });
});
