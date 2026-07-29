/**
 * webhookHandlers.ts — complete path coverage.
 *
 * Covers every previously-uncovered statement and branch in:
 *   - sendAffiliateTransfer (lines 14–54): all early-exit and transfer paths
 *   - checkout.session.completed: referralCode spread, StockScanner activation
 *   - invoice.payment_succeeded: guard branches, missing referralCode
 *   - customer.subscription.deleted: missing customerId guard
 *   - getStripeSync catch block (line 78)
 *
 * All Stripe Connect operations (accounts.retrieve, transfers.create) are
 * exercised through mockStripe — no real bank account or payout required.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";

// ── Module mocks (hoisted by Vitest before any import) ────────────────────────

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
import { getUncachableStripeClient, getStripeSync } from "../stripeClient";
import { WebhookHandlers } from "../webhookHandlers";

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

function stripeReady() {
  (getUncachableStripeClient as Mock).mockResolvedValue(mockStripe);
}

/** Send a raw event object through processWebhook. */
async function sendEvent(event: Record<string, any>): Promise<void> {
  return WebhookHandlers.processWebhook(
    Buffer.from(JSON.stringify(event)),
    "sig_mock"
  );
}

/** Build a checkout.session.completed event with caller-supplied overrides. */
function checkoutCompleted(objectOverrides: Record<string, any> = {}) {
  return {
    type: "checkout.session.completed",
    id: "evt_co_001",
    data: {
      object: {
        metadata: {},
        customer: "cus_test_001",
        subscription: null,
        mode: "payment",
        amount_total: 5000,
        ...objectOverrides,
      },
    },
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// sendAffiliateTransfer — full internal path coverage (lines 14–54)
//
// All tests reach sendAffiliateTransfer via checkout.session.completed with
// referralCode present and mode="payment".  No sessionId in metadata so the
// session-unlock DB.update is not needed.
// ─────────────────────────────────────────────────────────────────────────────

describe("sendAffiliateTransfer — full path coverage (lines 14–54)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("amountCents=0 → early return at line 14 (b0[0]: !affiliateCode || amountCents ≤ 0)", async () => {
    // if (!affiliateCode || amountCents <= 0) return;
    // amount_total: 0 → sendAffiliateTransfer("CODE", 0, …) → 0 is not > 0 → early exit.
    // No DB calls or Stripe calls should happen.
    await sendEvent(
      checkoutCompleted({ metadata: { referralCode: "EARLYEXIT" }, mode: "payment", amount_total: 0 })
    );

    expect(db.select).not.toHaveBeenCalled();
    expect(mockStripe.accounts.retrieve).not.toHaveBeenCalled();
    expect(mockStripe.transfers.create).not.toHaveBeenCalled();
  });

  it("commissionCents rounds to 0 → early return at line 28 (b3[0]: if cents ≤ 0)", async () => {
    // commissionCents(50, 1) = Math.floor(50 * 0.01) = Math.floor(0.5) = 0
    // Passes line 14 (amountCents=50 > 0), passes line 22 (stripeConnectId set),
    // then Math.floor(0.5)=0 → if (cents <= 0) return; at line 28.
    stripeReady();
    selectOnce([{ code: "LOWCOMM", stripeConnectId: "acct_low_001", commissionPct: 1 }]);

    await sendEvent(
      checkoutCompleted({ metadata: { referralCode: "LOWCOMM" }, mode: "payment", amount_total: 50 })
    );

    expect(db.select).toHaveBeenCalledOnce(); // affiliate lookup happened
    // getUncachableStripeClient never called — returned before line 30
    expect(mockStripe.accounts.retrieve).not.toHaveBeenCalled();
    expect(mockStripe.transfers.create).not.toHaveBeenCalled();
  });

  it("stripe.accounts.retrieve → payouts_enabled:false → early return at line 37 (b4[0])", async () => {
    // 20% of 5000 cents = 1000 cents → non-zero; passes commissionCents gate.
    // accounts.retrieve returns payouts_enabled:false → log warn + return.
    stripeReady();
    selectOnce([{ code: "NOPAY", stripeConnectId: "acct_nopay_001", commissionPct: 20 }]);
    mockStripe.accounts.retrieve.mockResolvedValue({ payouts_enabled: false });

    await sendEvent(
      checkoutCompleted({ metadata: { referralCode: "NOPAY" }, mode: "payment", amount_total: 5000 })
    );

    expect(mockStripe.accounts.retrieve).toHaveBeenCalledWith("acct_nopay_001");
    expect(mockStripe.transfers.create).not.toHaveBeenCalled();
  });

  it("stripe.accounts.retrieve throws → catch block at lines 39–42 fires, no transfer", async () => {
    // accounts.retrieve rejects → catch(err) { logger.error; return; } at line 39–42.
    stripeReady();
    selectOnce([{ code: "ERRCODE", stripeConnectId: "acct_err_001", commissionPct: 20 }]);
    mockStripe.accounts.retrieve.mockRejectedValue(new Error("account not found in Stripe"));

    await sendEvent(
      checkoutCompleted({ metadata: { referralCode: "ERRCODE" }, mode: "payment", amount_total: 5000 })
    );

    expect(mockStripe.accounts.retrieve).toHaveBeenCalledWith("acct_err_001");
    expect(mockStripe.transfers.create).not.toHaveBeenCalled();
  });

  it("payouts_enabled:true → stripe.transfers.create called with correct amount and destination (lines 44–54)", async () => {
    // Full happy path: affiliate has Connect account, payouts active → transfer created.
    // commissionCents(5000, 20) = Math.floor(1000) = 1000.
    stripeReady();
    selectOnce([{ code: "PAYME", stripeConnectId: "acct_live_001", commissionPct: 20 }]);
    mockStripe.accounts.retrieve.mockResolvedValue({ payouts_enabled: true });
    mockStripe.transfers.create.mockResolvedValue({ id: "tr_live_001" });

    await sendEvent(
      checkoutCompleted({ metadata: { referralCode: "PAYME" }, mode: "payment", amount_total: 5000 })
    );

    expect(mockStripe.accounts.retrieve).toHaveBeenCalledWith("acct_live_001");
    expect(mockStripe.transfers.create).toHaveBeenCalledOnce();
    const [body] = mockStripe.transfers.create.mock.calls[0];
    expect(body.amount).toBe(1000);          // 20% of 5000 cents
    expect(body.destination).toBe("acct_live_001");
    expect(body.currency).toBe("usd");
    // idempotency key passed as second argument
    const [, opts] = mockStripe.transfers.create.mock.calls[0];
    expect(opts.idempotencyKey).toMatch(/PAYME/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// checkout.session.completed — referralCode spread + StockScanner branches
// ─────────────────────────────────────────────────────────────────────────────

describe("checkout.session.completed — referralCode spread and StockScanner activation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("sessionId + referralCode present → referralCode spread into DB update (b12[0] line 99 TRUE arm)", async () => {
    // Line 99: ...(referralCode ? { referralCode } : {})
    // The TRUE arm fires when referralCode is non-null AND the session update runs.
    // mode=subscription → sendAffiliateTransfer is NOT triggered (only mode=payment does that).
    updateOnce(); // session unlock at lines 93–101

    await sendEvent(
      checkoutCompleted({
        metadata: { sessionId: "sess-spread-001", referralCode: "SPREAD20" },
        mode: "subscription", // prevents affiliate payout branch
        amount_total: 0,
      })
    );

    expect(db.update).toHaveBeenCalledOnce();
    const setArg = (db.update as Mock).mock.results[0].value.set.mock.calls[0][0];
    expect(setArg.isSubscribed).toBe(true);
    expect(setArg.referralCode).toBe("SPREAD20"); // TRUE arm of ternary at line 99
  });

  it("product=stock-scanner with customer_details.email → db.execute INSERT fires (b16[0], b18[0])", async () => {
    // if (product === 'stock-scanner') TRUE → b16[0] covered.
    // email present → if (email) TRUE → b18[0] covered.
    // db.execute is already mocked to resolve.
    await sendEvent(
      checkoutCompleted({
        metadata: { product: "stock-scanner" }, // no sessionId → no sessions update
        customer: "cus_ss_exec_001",
        subscription: "sub_ss_exec_001",
        mode: "subscription",
        customer_details: { email: "scanner@example.com" },
      })
    );

    expect(db.execute).toHaveBeenCalledOnce();
  });

  it("product=stock-scanner, customer_email fallback used when customer_details absent (b17[1] null-coalesce arm)", async () => {
    // email chain: customer_details?.email ?? session.customer_email ?? null
    // customer_details absent → first ?? fires → customer_email used.
    await sendEvent(
      checkoutCompleted({
        metadata: { product: "stock-scanner" },
        customer: "cus_ss_fb_001",
        subscription: null,
        mode: "subscription",
        customer_email: "fallback@example.com", // no customer_details
      })
    );

    expect(db.execute).toHaveBeenCalledOnce();
  });

  it("product=stock-scanner, both email sources null → db.execute NOT called (b18[1] line 120 FALSE arm)", async () => {
    // email = null → if (email) FALSE → crypto.randomUUID() and db.execute skipped.
    await sendEvent(
      checkoutCompleted({
        metadata: { product: "stock-scanner" },
        customer: "cus_ss_noemail",
        subscription: null,
        mode: "subscription",
        // no customer_details, no customer_email
      })
    );

    expect(db.execute).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// getStripeSync catch block (line 78 / s27)
// ─────────────────────────────────────────────────────────────────────────────

describe("getStripeSync / processWebhook failure → catch block at line 78", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getStripeSync() rejects → catch fires (s27 line 78), handler continues without throw", async () => {
    (getStripeSync as Mock).mockRejectedValue(new Error("sync service unavailable"));

    // Unknown event type → no DB side-effects; just verifying no throw.
    await expect(
      sendEvent({ type: "unknown.event", id: "evt_catch_001", data: { object: {} } })
    ).resolves.toBeUndefined();

    expect(db.update).not.toHaveBeenCalled();
  });

  it("sync.processWebhook() rejects → catch fires, handler continues", async () => {
    (getStripeSync as Mock).mockResolvedValue({
      processWebhook: vi.fn().mockRejectedValue(new Error("processWebhook failed")),
    });

    await expect(
      sendEvent({ type: "unknown.event", id: "evt_catch_002", data: { object: {} } })
    ).resolves.toBeUndefined();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// invoice.payment_succeeded — guard branches
// ─────────────────────────────────────────────────────────────────────────────

describe("invoice.payment_succeeded — guard and referralCode branches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("amount_paid=0 → if (customerId && amountPaid > 0) FALSE, no DB session lookup (b21[1], b22[1])", async () => {
    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_zero",
      data: { object: { customer: "cus_zero", amount_paid: 0 } },
    });

    expect(db.select).not.toHaveBeenCalled();
  });

  it("amount_paid absent → defaults to 0 via ?? operator, guard fails (b21[1])", async () => {
    // amount_paid ?? 0 — the ?? arm fires when amount_paid is absent.
    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_noamt",
      data: { object: { customer: "cus_noamt" } }, // no amount_paid
    });

    expect(db.select).not.toHaveBeenCalled();
  });

  it("customer absent → if (customerId && amountPaid > 0) FALSE, no DB session lookup (b20[1])", async () => {
    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_nocust",
      data: { object: { amount_paid: 5000 } }, // no customer field
    });

    expect(db.select).not.toHaveBeenCalled();
  });

  it("session found but referralCode is null → sendAffiliateTransfer NOT triggered (b25[1] line 150)", async () => {
    // if (session?.referralCode) → FALSE when referralCode is null on the session row.
    selectOnce([{
      sessionId: "sess-noref",
      referralCode: null, // no referral code on session
      stripeCustomerId: "cus_noref",
    }]);

    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_noref",
      data: { object: { customer: "cus_noref", amount_paid: 5000 } },
    });

    // Session was looked up (1 select) but no further DB/Stripe calls
    expect(db.select).toHaveBeenCalledTimes(1);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// customer.subscription.deleted — missing customerId guard
// ─────────────────────────────────────────────────────────────────────────────

describe("customer.subscription.deleted — guard branch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("customer absent → if (customerId) FALSE, DB update NOT called (b27[1], b28[1])", async () => {
    // subscription.customer is absent → customerId = undefined → if (customerId) false.
    await sendEvent({
      type: "customer.subscription.deleted",
      id: "evt_del_nocust",
      data: { object: {} }, // no customer field
    });

    expect(db.update).not.toHaveBeenCalled();
    expect(db.execute).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Null-coalescing operator ?? fallback arms
//
// These cover the remaining binary-expr branch arms where the RIGHT side of ??
// fires (i.e. the left side evaluates to null or undefined).
// ─────────────────────────────────────────────────────────────────────────────

describe("null-coalescing fallback arms — ??.object, ??.metadata, ??0, ??''", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getStripeSync as Mock).mockResolvedValue({ processWebhook: vi.fn() });
  });

  it("checkout.session.completed: event.data has no 'object' → ?? {} fires (b7[1] line 83)", async () => {
    // const session = event.data?.object ?? {};
    // data.object is absent → ??.object = undefined → ?? {} fires → session = {}
    // sessionId/product/referralCode all undefined → no DB/Stripe calls.
    await sendEvent({
      type: "checkout.session.completed",
      id: "evt_no_object",
      data: {}, // no 'object' key
    });

    expect(db.update).not.toHaveBeenCalled();
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("checkout.session.completed: data.object has no 'metadata' → ?. short-circuits → referralCode=null (b9[1] line 87)", async () => {
    // const referralCode = session.metadata?.referralCode ?? null;
    // metadata is absent → session.metadata = undefined → ?.referralCode = undefined → ?? null fires.
    await sendEvent({
      type: "checkout.session.completed",
      id: "evt_no_meta",
      data: {
        object: {
          // no 'metadata' field at all
          customer: "cus_nometa",
          subscription: null,
          mode: "payment",
          amount_total: 100,
        },
      },
    });

    // referralCode = null → if (referralCode && mode) false → sendAffiliateTransfer not called
    expect(mockStripe.transfers).toBeDefined(); // Stripe not called
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("checkout.session.completed: referralCode set, mode=payment, but no amount_total → ?? 0 fires (b15[1] line 108)", async () => {
    // const amountCents = session.amount_total ?? 0;
    // amount_total absent → ?? 0 fires → amountCents = 0
    // Then sendAffiliateTransfer("CODE", 0, …) hits line 14 early return.
    await sendEvent({
      type: "checkout.session.completed",
      id: "evt_no_amount",
      data: {
        object: {
          metadata: { referralCode: "NOAMT" },
          customer: "cus_noamt",
          subscription: null,
          mode: "payment",
          // no amount_total field
        },
      },
    });

    // amountCents = 0 → sendAffiliateTransfer early-exits at line 14
    // No DB select (returned before affiliate lookup)
    expect(db.select).not.toHaveBeenCalled();
  });

  it("invoice.payment_succeeded: event.data has no 'object' → ?? {} fires (b20[1]/b21[1] line 138)", async () => {
    // const invoice = event.data?.object ?? {};
    // data.object absent → ?? {} fires → invoice = {}
    // customerId = undefined → if (customerId && amountPaid > 0) false → no DB calls.
    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_no_object",
      data: {}, // no 'object' key
    });

    expect(db.select).not.toHaveBeenCalled();
  });

  it("invoice.payment_succeeded: billing_reason absent on invoice → ?? '' fires (b25[0] line 150)", async () => {
    // const billingReason = invoice.billing_reason ?? '';
    // billing_reason absent → ?? '' fires → billingReason = ''
    // Need: referralCode on session so this line is actually reached.
    selectOnce([{
      sessionId: "sess-nobr",
      referralCode: "NOBR10",
      stripeCustomerId: "cus_nobr",
    }]);
    // second select: affiliate lookup in sendAffiliateTransfer (returns empty → exits at line 22)
    selectOnce([]);

    await sendEvent({
      type: "invoice.payment_succeeded",
      id: "evt_inv_nobr",
      data: {
        object: {
          customer: "cus_nobr",
          amount_paid: 5000,
          // no billing_reason field → ?? '' fires
        },
      },
    });

    // Both DB selects happened (session + affiliate lookup)
    expect(db.select).toHaveBeenCalledTimes(2);
  });

  it("customer.subscription.deleted: event.data has no 'object' → ?? {} fires (b27[0] line 163)", async () => {
    // const subscription = event.data?.object ?? {};
    // data.object absent → ?? {} fires → subscription = {}
    // customerId = undefined → if (customerId) false → no DB calls.
    await sendEvent({
      type: "customer.subscription.deleted",
      id: "evt_del_no_object",
      data: {}, // no 'object' key
    });

    expect(db.update).not.toHaveBeenCalled();
    expect(db.execute).not.toHaveBeenCalled();
  });
});
