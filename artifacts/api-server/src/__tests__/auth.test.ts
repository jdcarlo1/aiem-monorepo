/**
 * Authentication — HTTP-level tests for POST /session/claim
 *
 * getSessionAccessDecision (pure) and verifySessionAccess (middleware) are
 * exhaustively covered in migration.test.ts — this file covers the /session/claim
 * route handler logic and the E2E authentication happy path.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks (hoisted by Vitest) ─────────────────────────────────────────

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

vi.mock("../stripeClient", () => ({
  getUncachableStripeClient: vi.fn(),
  getStripeSync: vi.fn(),
}));

vi.mock("@clerk/express", () => ({
  clerkMiddleware: vi.fn(() => (_req: any, _res: any, next: any) => next()),
  getAuth: vi.fn(),
}));

vi.mock("@clerk/shared/keys", () => ({
  publishableKeyFromHost: vi.fn().mockReturnValue("pk_test_mock"),
}));

vi.mock("../middlewares/clerkProxyMiddleware", () => ({
  CLERK_PROXY_PATH: "/__clerk_proxy",
  clerkProxyMiddleware: vi.fn(() => (_req: any, _res: any, next: any) => next()),
  getClerkProxyHost: vi.fn().mockReturnValue(""),
}));

// ── Import after mocks are registered ────────────────────────────────────────

import { db } from "@workspace/db";
import { getAuth } from "@clerk/express";
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

function insertValues() {
  (db.insert as Mock).mockReturnValueOnce({
    values: vi.fn().mockResolvedValue(undefined),
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("POST /session/claim — route logic", () => {
  const USER_A = "user_clerk_aaaa";
  const SESSION_X = "anon-uuid-xxxx";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("no Clerk JWT → 401 UNAUTHENTICATED", async () => {
    (getAuth as Mock).mockReturnValue({ userId: null });

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(401);
    expect(res.body.code).toBe("UNAUTHENTICATED");
  });

  it("missing sessionId body → 400", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });

    const res = await request(app)
      .post("/api/session/claim")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/sessionId/i);
  });

  it("session not found in DB → 404 SESSION_NOT_FOUND", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    selectOnce([]); // sessions lookup → not found

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(404);
    expect(res.body.code).toBe("SESSION_NOT_FOUND");
  });

  it("session already claimed by different user → 409 SESSION_ALREADY_CLAIMED", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    selectOnce([{ sessionId: SESSION_X }]);                               // session exists
    selectOnce([{ clerkUserId: "user_clerk_other", sessionId: SESSION_X }]); // claimed by someone else

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("SESSION_ALREADY_CLAIMED");
  });

  it("session already claimed by same user → 200 idempotent", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    selectOnce([{ sessionId: SESSION_X }]);                       // session exists
    selectOnce([{ clerkUserId: USER_A, sessionId: SESSION_X }]); // claimed by same user

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.success).toBe(true);
    expect(res.body.idempotent).toBe(true);
  });

  it("user already has a different session claimed → 409 USER_ALREADY_HAS_CLAIM", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    selectOnce([{ sessionId: SESSION_X }]);        // session exists
    selectOnce([]);                                  // SESSION_X not claimed by anyone
    selectOnce([{ clerkUserId: USER_A, sessionId: "other-session" }]); // user owns a different session

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(409);
    expect(res.body.code).toBe("USER_ALREADY_HAS_CLAIM");
    expect(res.body.claimedSessionId).toBe("other-session");
  });

  it("valid new claim → 201 with success:true", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    selectOnce([{ sessionId: SESSION_X }]); // session exists
    selectOnce([]);                           // not claimed by anyone
    selectOnce([]);                           // user has no prior claim
    insertValues();                           // insert the claim

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_X })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(201);
    expect(res.body.success).toBe(true);
    expect(res.body.clerkUserId).toBe(USER_A);
    expect(res.body.sessionId).toBe(SESSION_X);
  });
});

// ── Blocked items — cannot test in this environment ──────────────────────────

// CLERK JWT VERIFICATION: clerkMiddleware() validates the Authorization Bearer
// token against live Clerk JWKS. In tests, @clerk/express is fully mocked and
// getAuth() returns a configured stub. Real token issuance and expiry checks
// require a live Clerk instance and a valid signed JWT — not available here.
it.skip("BLOCKED: real Clerk JWT signature verification — requires live Clerk instance and signed token", () => {});

// ── E2E happy path: unauthenticated → claim session ──────────────────────────

describe("Auth E2E — anonymous session claim happy path", () => {
  const USER_A = "user_clerk_e2e_auth";
  const SESSION_E2E = "e2e-auth-session-uuid";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("claim a session after it has been created: session exists, unclaimed, new user → 201", async () => {
    (getAuth as Mock).mockReturnValue({ userId: USER_A });

    // Step 1: session already exists in DB (established during quiz)
    selectOnce([{ sessionId: SESSION_E2E, questionsAnswered: 3, isSubscribed: false }]);
    // Step 2: no existing claim on this session
    selectOnce([]);
    // Step 3: user has no prior claim
    selectOnce([]);
    // Step 4: insert succeeds
    insertValues();

    const res = await request(app)
      .post("/api/session/claim")
      .send({ sessionId: SESSION_E2E })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(201);
    expect(res.body).toMatchObject({
      success: true,
      clerkUserId: USER_A,
      sessionId: SESSION_E2E,
    });
  });
});
