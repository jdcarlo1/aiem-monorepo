/**
 * Payments — unit tests for WebhookHandlers + HTTP tests for Stripe checkout routes
 *
 * Coverage:
 *  - WebhookHandlers.processWebhook (unit, mocked DB + Stripe)
 *  - POST /stripe/checkout (body validation + happy paths for monthly + lifetime)
 *  - POST /stripe/verify-checkout (paid + not-paid branches)
 *  - E2E happy path: valid checkout body → Stripe session URL returned
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
  products: {
    search: vi.fn(),
    list: vi.fn(),
  },
  prices: {
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
  accounts: {
    retrieve: vi.fn(),
  },
  transfers: {
    create: vi.fn(),
  },
  billingPortal: {
    sessions: { create: vi.fn() },
  },
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
import { getUncachableStripeClient, getStripeSync } from "../stripeClient";
import { WebhookHandlers } from "../webhookHandlers";
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

// ── WebhookHandlers unit tests ────────────────────────────────────────────────

describe("WebhookHandlers.processWebhook — unit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("non-Buffer payload → throws with diagnostic message", async () => {
    await expect(
      WebhookHandlers.processWebhook("not a buffer" as any, "sig")
    ).rejects.toThrow(/Payload must be a Buffer/);
  });

  it("checkout.session.completed with sessionId → DB update called with isSubscribed:true", async () => {
    updateOnce();

    const event = {
      type: "checkout.session.completed",
      id: "evt_001",
      data: {
        object: {
          metadata: { sessionId: "session-abc" },
          customer: "cus_001",
          subscription: null,
          mode: "payment",
          amount_total: 11000,
        },
      },
    };

    await WebhookHandlers.processWebhook(
      Buffer.from(JSON.stringify(event)),
      "sig_mock"
    );

    expect(db.update).toHaveBeenCalledOnce();
    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
  });

  it("checkout.session.completed without sessionId → DB update NOT called", async () => {
    const event = {
      type: "checkout.session.completed",
      id: "evt_002",
      data: {
        object: {
          metadata: {},          // no sessionId
          customer: "cus_002",
          subscription: null,
          mode: "payment",
          amount_total: 11000,
        },
      },
    };

    await WebhookHandlers.processWebhook(
      Buffer.from(JSON.stringify(event)),
      "sig_mock"
    );

    expect(db.update).not.toHaveBeenCalled();
  });

  it("customer.subscription.deleted → DB update called with isSubscribed:false", async () => {
    updateOnce();
    // The second update is for sm_subscribers via db.execute
    (db.execute as Mock).mockResolvedValue({ rows: [] });

    const event = {
      type: "customer.subscription.deleted",
      id: "evt_003",
      data: { object: { customer: "cus_003" } },
    };

    await WebhookHandlers.processWebhook(
      Buffer.from(JSON.stringify(event)),
      "sig_mock"
    );

    expect(db.update).toHaveBeenCalledOnce();
    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(false);
    expect(setArg.stripeSubscriptionId).toBeNull();
  });

  it("unrecognised event type → no DB calls", async () => {
    const event = { type: "payment_intent.created", id: "evt_004", data: { object: {} } };

    await WebhookHandlers.processWebhook(
      Buffer.from(JSON.stringify(event)),
      "sig_mock"
    );

    expect(db.update).not.toHaveBeenCalled();
    expect(db.insert).not.toHaveBeenCalled();
  });

  it("malformed JSON payload → silently returns (no throw)", async () => {
    await expect(
      WebhookHandlers.processWebhook(Buffer.from("not json"), "sig_mock")
    ).resolves.toBeUndefined();
  });
});

// ── POST /stripe/checkout ─────────────────────────────────────────────────────

describe("POST /stripe/checkout — body validation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("empty body → 400 with error message", async () => {
    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/sessionId and plan/i);
  });

  it("missing plan → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: "sess-001" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("invalid plan value → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: "sess-001", plan: "annual" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });
});

describe("POST /stripe/checkout — monthly and lifetime happy paths", () => {
  const SESSION_ID = "pay-test-session-001";
  const CHECKOUT_URL = "https://checkout.stripe.com/pay/cs_test_abc123";

  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("monthly plan → creates Stripe customer + subscription checkout → 200 with url", async () => {
    // DB: no existing session
    selectOnce([]);
    // DB: insert new session
    insertOnce();

    mockStripe.customers.create.mockResolvedValue({ id: "cus_monthly_001" });
    mockStripe.products.search.mockResolvedValue({
      data: [{ id: "prod_monthly_001" }],
    });
    mockStripe.prices.list.mockResolvedValue({
      data: [{ id: "price_monthly_001" }],
    });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: CHECKOUT_URL });

    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: SESSION_ID, plan: "monthly" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.url).toBe(CHECKOUT_URL);

    const createCall = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createCall.mode).toBe("subscription");
  });

  it("lifetime plan → creates one-time payment checkout → 200 with url", async () => {
    selectOnce([]);
    insertOnce();

    mockStripe.customers.create.mockResolvedValue({ id: "cus_lifetime_001" });
    mockStripe.products.search.mockResolvedValue({
      data: [{ id: "prod_lifetime_001" }],
    });
    mockStripe.prices.list.mockResolvedValue({
      data: [{ id: "price_lifetime_001" }],
    });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: CHECKOUT_URL });

    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: SESSION_ID, plan: "lifetime" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.url).toBe(CHECKOUT_URL);

    const createCall = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createCall.mode).toBe("payment");
  });

  it("monthly plan — reuses existing Stripe customer if already stored", async () => {
    // DB: session already has a stripeCustomerId
    selectOnce([{ sessionId: SESSION_ID, stripeCustomerId: "cus_existing_001" }]);
    updateOnce();

    mockStripe.products.search.mockResolvedValue({
      data: [{ id: "prod_monthly_001" }],
    });
    mockStripe.prices.list.mockResolvedValue({
      data: [{ id: "price_monthly_001" }],
    });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: CHECKOUT_URL });

    await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: SESSION_ID, plan: "monthly" })
      .set("Content-Type", "application/json");

    // customers.create should NOT have been called
    expect(mockStripe.customers.create).not.toHaveBeenCalled();
    const createCall = mockStripe.checkout.sessions.create.mock.calls[0][0];
    expect(createCall.customer).toBe("cus_existing_001");
  });
});

// ── POST /stripe/verify-checkout ─────────────────────────────────────────────

describe("POST /stripe/verify-checkout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("missing body → 400", async () => {
    const res = await request(app)
      .post("/api/stripe/verify-checkout")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });

  it("checkout paid → 200 success:true, DB updated with isSubscribed:true", async () => {
    mockStripe.checkout.sessions.retrieve.mockResolvedValue({
      payment_status: "paid",
      status: "complete",
      subscription: "sub_001",
      customer: "cus_001",
      customer_details: { email: "test@example.com" },
    });
    mockStripe.customers.update.mockResolvedValue({});
    updateOnce();

    const res = await request(app)
      .post("/api/stripe/verify-checkout")
      .send({ sessionId: "sess-verify-001", checkoutSessionId: "cs_test_001" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.isSubscribed).toBe(true);
    expect(res.body.email).toBe("test@example.com");

    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
  });

  it("checkout not paid → 200 success:false, isSubscribed:false", async () => {
    mockStripe.checkout.sessions.retrieve.mockResolvedValue({
      payment_status: "unpaid",
      status: "open",
    });

    const res = await request(app)
      .post("/api/stripe/verify-checkout")
      .send({ sessionId: "sess-verify-002", checkoutSessionId: "cs_test_002" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(false);
    expect(res.body.isSubscribed).toBe(false);
    expect(db.update).not.toHaveBeenCalled();
  });
});

// ── E2E happy path ────────────────────────────────────────────────────────────

describe("Payments E2E — checkout session creation happy path", () => {
  const CHECKOUT_URL = "https://checkout.stripe.com/pay/cs_test_e2e";

  beforeEach(() => {
    vi.clearAllMocks();
    stripeReady();
  });

  it("full checkout flow: validate body → create customer → create session → return URL", async () => {
    selectOnce([]);  // no existing session in DB
    insertOnce();    // session inserted

    mockStripe.customers.create.mockResolvedValue({ id: "cus_e2e_001" });
    mockStripe.products.search.mockResolvedValue({ data: [{ id: "prod_e2e_001" }] });
    mockStripe.prices.list.mockResolvedValue({ data: [{ id: "price_e2e_001" }] });
    mockStripe.checkout.sessions.create.mockResolvedValue({ url: CHECKOUT_URL });

    const res = await request(app)
      .post("/api/stripe/checkout")
      .send({ sessionId: "e2e-payment-session", plan: "lifetime" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.url).toBe(CHECKOUT_URL);
    expect(mockStripe.customers.create).toHaveBeenCalledOnce();
    expect(mockStripe.checkout.sessions.create).toHaveBeenCalledOnce();
  });
});
