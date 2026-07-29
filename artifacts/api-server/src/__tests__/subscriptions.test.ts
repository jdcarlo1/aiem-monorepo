/**
 * Subscriptions — restore-access flow, plan changes, cancellation
 *
 * Coverage:
 *  - POST /stripe/restore-access (input validation + email found + email not found)
 *  - POST /subscription/cancel (session not found + no subscription ID + happy path)
 *  - E2E happy path: restore-access with valid email → session activated
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks ──────────────────────────────────────────────────────────────

// Bypass all rate limiters — tests make repeated requests from the same IP
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
  customers: {
    create: vi.fn(),
    update: vi.fn(),
    list: vi.fn(),
  },
  checkout: {
    sessions: {
      create: vi.fn(),
      retrieve: vi.fn(),
      list: vi.fn(),
      search: vi.fn(),
    },
  },
  subscriptions: {
    cancel: vi.fn(),
  },
  accounts: { retrieve: vi.fn() },
  transfers: { create: vi.fn() },
  products: { search: vi.fn(), list: vi.fn() },
  prices: { list: vi.fn() },
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

// ── Import after mocks ────────────────────────────────────────────────────────

import { db } from "@workspace/db";
import { getUncachableStripeClient } from "../stripeClient";
import app from "../app";

// ── Helpers ───────────────────────────────────────────────────────────────────

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

function insertOnce() {
  (db.insert as Mock).mockReturnValueOnce({
    values: vi.fn().mockResolvedValue(undefined),
  });
}

function stripeReady() {
  (getUncachableStripeClient as Mock).mockResolvedValue(mockStripe);
}

// ── POST /stripe/restore-access ───────────────────────────────────────────────

describe("POST /stripe/restore-access — input validation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("missing email → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: "sess-001" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("invalid email format → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: "sess-001", email: "not-an-email" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("missing sessionId → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ email: "valid@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });
});

describe("POST /stripe/restore-access — lookup paths", () => {
  const SESSION_ID = "restore-session-001";
  const EMAIL = "paid@example.com";

  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("email found via Stripe search → 200 success:true, session activated", async () => {
    mockStripe.checkout.sessions.search.mockResolvedValue({
      data: [
        {
          customer: "cus_found_001",
          subscription: "sub_found_001",
        },
      ],
    });
    mockStripe.customers.update.mockResolvedValue({});

    // activateSession: check for existing session row
    selectOnce([{ sessionId: SESSION_ID, questionsAnswered: 5, isSubscribed: false }]);
    updateOnce(); // update session with isSubscribed:true

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: EMAIL })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.message).toMatch(/restored/i);

    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
    expect(setArg.stripeCustomerId).toBe("cus_found_001");
  });

  it("email found via customer list fallback → 200 success:true", async () => {
    // Search throws (some Stripe accounts don't have search enabled)
    mockStripe.checkout.sessions.search.mockRejectedValue(new Error("not available"));

    mockStripe.customers.list.mockResolvedValue({
      data: [{ id: "cus_fallback_001" }],
    });
    mockStripe.checkout.sessions.list.mockResolvedValue({
      data: [{ customer: "cus_fallback_001", subscription: null }],
    });

    selectOnce([]); // no existing session row → insert
    insertOnce();

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: EMAIL })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
  });

  it("email not found in Stripe at all → 200 success:false", async () => {
    mockStripe.checkout.sessions.search.mockResolvedValue({ data: [] });
    mockStripe.customers.list.mockResolvedValue({ data: [] });

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: "nobody@example.com" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(false);
    expect(res.body.message).toMatch(/No completed payment/i);
    expect(db.update).not.toHaveBeenCalled();
  });

  it("restore-access search path: cs.customer and cs.subscription returned as objects → type-guard alt arms (lines 189-190)", async () => {
    // Previous tests pass customer/subscription as plain strings, hitting the
    // string-shortcut branch of:
    //   typeof cs.customer === "string" ? cs.customer : ""     (line 189)
    //   typeof cs.subscription === "string" ? cs.subscription : null  (line 190)
    // This test passes Stripe objects so the alternative (object) arms fire.
    mockStripe.checkout.sessions.search.mockResolvedValue({
      data: [
        {
          customer:     { id: "cus_obj_search" }, // object → typeof !== "string" → ""
          subscription: { id: "sub_obj_search" }, // object → typeof !== "string" → null
        },
      ],
    });
    mockStripe.customers.update.mockResolvedValue({});

    // activateSession: session found → update
    selectOnce([{ sessionId: SESSION_ID, questionsAnswered: 0, isSubscribed: false }]);
    updateOnce();

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: EMAIL })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);

    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    // customerId = "" (object → type guard returns ""), subscriptionId = null
    expect(setArg.stripeCustomerId).toBe("");
    expect(setArg.stripeSubscriptionId).toBeNull();
    // customers.update NOT called — customerId is "" (falsy)
    expect(mockStripe.customers.update).not.toHaveBeenCalled();
  });

  it("restore-access list-fallback path: cs.subscription returned as object → type-guard alt arm (line 205)", async () => {
    // Exercises the list-fallback branch (search throws → fall to customers.list).
    // cs.subscription is a full object, not a string, so:
    //   typeof cs.subscription === "string" ? cs.subscription : null   (line 205 alt)
    // produces subscriptionId = null.
    mockStripe.checkout.sessions.search.mockRejectedValue(new Error("search unavailable"));

    mockStripe.customers.list.mockResolvedValue({
      data: [{ id: "cus_list_obj" }],
    });
    mockStripe.checkout.sessions.list.mockResolvedValue({
      data: [{
        customer: "cus_list_obj",
        subscription: { id: "sub_obj_list" }, // object → typeof !== "string" → null
      }],
    });

    // activateSession: no existing session → insert
    selectOnce([]);
    insertOnce();

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: EMAIL })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    // insert was called with subscriptionId = null (subscription was an object)
    expect(db.insert).toHaveBeenCalledOnce();
  });
});

// ── POST /subscription/cancel ─────────────────────────────────────────────────

describe("POST /subscription/cancel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("missing sessionId → 400", async () => {
    const res = await request(app)
      .post("/api/subscription/cancel")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("session not found in DB → 404", async () => {
    selectOnce([]); // session lookup returns empty

    const res = await request(app)
      .post("/api/subscription/cancel")
      .send({ sessionId: "ghost-session" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(404);
    expect(mockStripe.subscriptions.cancel).not.toHaveBeenCalled();
  });

  it("session has no stripeSubscriptionId → 400 (lifetime access cannot be cancelled here)", async () => {
    selectOnce([{
      sessionId: "sess-lifetime",
      isSubscribed: true,
      stripeSubscriptionId: null,  // no subscription ID = lifetime
    }]);

    const res = await request(app)
      .post("/api/subscription/cancel")
      .send({ sessionId: "sess-lifetime" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/Lifetime access/i);
    expect(mockStripe.subscriptions.cancel).not.toHaveBeenCalled();
  });

  it("valid subscription → calls stripe.subscriptions.cancel + DB update → 200", async () => {
    selectOnce([{
      sessionId: "sess-monthly",
      isSubscribed: true,
      stripeSubscriptionId: "sub_monthly_001",
    }]);
    mockStripe.subscriptions.cancel.mockResolvedValue({});
    updateOnce();

    const res = await request(app)
      .post("/api/subscription/cancel")
      .send({ sessionId: "sess-monthly" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(mockStripe.subscriptions.cancel).toHaveBeenCalledWith("sub_monthly_001");

    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(false);
    expect(setArg.stripeSubscriptionId).toBeNull();
  });
});

// ── Blocked items — cannot test in this environment ──────────────────────────

// STRIPE CONNECT AFFILIATE TRANSFERS: the affiliate payout path inside
// invoice.payment_succeeded calls sendAffiliateTransfer(), which calls
// stripe.accounts.retrieve() + stripe.transfers.create() against a live
// Stripe Connect account with payouts_enabled. No such account exists in
// the test environment.
it.skip("BLOCKED: Stripe Connect affiliate monthly transfer — requires live Connect account with payouts_enabled", () => {});

// ── E2E happy path ────────────────────────────────────────────────────────────

describe("Subscriptions E2E — restore-access happy path", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("user enters email used at checkout → access restored, isSubscribed set to true", async () => {
    const SESSION_ID = "e2e-sub-session";
    const EMAIL = "subscriber@example.com";

    // Stripe search finds a completed checkout
    mockStripe.checkout.sessions.search.mockResolvedValue({
      data: [{ customer: "cus_e2e_sub", subscription: "sub_e2e_001" }],
    });
    mockStripe.customers.update.mockResolvedValue({});

    // Session exists in DB
    selectOnce([{ sessionId: SESSION_ID, questionsAnswered: 2, isSubscribed: false }]);
    updateOnce();

    const res = await request(app)
      .post("/api/stripe/restore-access")
      .send({ sessionId: SESSION_ID, email: EMAIL })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);

    // Confirm DB was updated with subscription data
    expect(db.update).toHaveBeenCalledOnce();
    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
    expect(setArg.stripeCustomerId).toBe("cus_e2e_sub");
    expect(setArg.stripeSubscriptionId).toBe("sub_e2e_001");
  });
});
