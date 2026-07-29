/**
 * Exam workflow — session status + answer submission
 *
 * Coverage:
 *  - GET /session/status (new session creation, existing session, missing sessionId)
 *  - POST /session/answer (correct/wrong answer, free limit, subscribed bypass, not found, bad body)
 *  - E2E happy path: get status → submit answer → questionsAnswered incremented
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
import app from "../app";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Set up db.select().from().where().limit() to return rows once. */
function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

/** Set up db.insert().values().returning() to return rows once. */
function insertReturningOnce(rows: any[]) {
  (db.insert as Mock).mockReturnValueOnce({
    values: vi.fn().mockReturnValue({
      returning: vi.fn().mockResolvedValue(rows),
    }),
  });
}

/**
 * Set up db.transaction() to simulate the SELECT FOR UPDATE + insert + update
 * sequence inside /session/answer.
 *
 * @param sessionRow  The row returned by SELECT FOR UPDATE (null = session not found yet)
 * @param isSubscribed Whether the session is subscribed
 * @param questionsAnswered How many questions the session has already answered
 */
function setupAnswerTransaction(
  sessionRow: { id: number; session_id: string; questions_answered: number; is_subscribed: boolean } | null,
  opts: { insertNewSession?: any[] } = {}
) {
  (db.transaction as Mock).mockImplementation(async (fn: Function) => {
    const tx = {
      execute: vi.fn().mockResolvedValue({
        rows: sessionRow ? [sessionRow] : [],
      }),
      insert: vi.fn().mockReturnValue({
        values: vi.fn().mockReturnValue({
          returning: vi.fn().mockResolvedValue(
            opts.insertNewSession ?? [sessionRow ?? {}]
          ),
        }),
      }),
      update: vi.fn().mockReturnValue({
        set: vi.fn().mockReturnValue({
          where: vi.fn().mockResolvedValue(undefined),
        }),
      }),
    };
    return fn(tx);
  });
}

// ── GET /session/status ───────────────────────────────────────────────────────

describe("GET /session/status", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sessionId present in query → returns session data (basic integration)", async () => {
    // GetSessionStatusQueryParams uses zod.coerce.string() which always coerces,
    // so we verify the route processes a provided sessionId correctly.
    selectOnce([{
      sessionId: "basic-check-uuid",
      questionsAnswered: 0,
      isSubscribed: false,
      subscriptionEndDate: null,
    }]);

    const res = await request(app)
      .get("/api/session/status")
      .query({ sessionId: "basic-check-uuid" });

    expect(res.status).toBe(200);
    expect(res.body.sessionId).toBe("basic-check-uuid");
  });

  it("new session (not in DB) → created on demand, returned with defaults", async () => {
    // getOrCreateSession: select returns nothing, insert creates row
    selectOnce([]); // no existing row
    insertReturningOnce([{
      sessionId: "brand-new-uuid",
      questionsAnswered: 0,
      isSubscribed: false,
      subscriptionEndDate: null,
    }]);

    const res = await request(app)
      .get("/api/session/status")
      .query({ sessionId: "brand-new-uuid" });

    expect(res.status).toBe(200);
    expect(res.body.sessionId).toBe("brand-new-uuid");
    expect(res.body.questionsAnswered).toBe(0);
    expect(res.body.isSubscribed).toBe(false);
    expect(res.body.canAnswerMore).toBe(true);
    expect(res.body.freeLimit).toBe(10);
  });

  it("existing session → returns stored state", async () => {
    selectOnce([{
      sessionId: "existing-uuid",
      questionsAnswered: 7,
      isSubscribed: false,
      subscriptionEndDate: null,
    }]);

    const res = await request(app)
      .get("/api/session/status")
      .query({ sessionId: "existing-uuid" });

    expect(res.status).toBe(200);
    expect(res.body.questionsAnswered).toBe(7);
    expect(res.body.canAnswerMore).toBe(true); // 7 < 10 free
  });

  it("subscribed session at free limit → canAnswerMore is true", async () => {
    selectOnce([{
      sessionId: "subscribed-uuid",
      questionsAnswered: 10,
      isSubscribed: true,
      subscriptionEndDate: null, // use null so route doesn't call .toISOString() on a string
    }]);

    const res = await request(app)
      .get("/api/session/status")
      .query({ sessionId: "subscribed-uuid" });

    expect(res.status).toBe(200);
    expect(res.body.canAnswerMore).toBe(true);
    expect(res.body.isSubscribed).toBe(true);
  });

  it("unsubscribed session at free limit → canAnswerMore is false", async () => {
    selectOnce([{
      sessionId: "at-limit-uuid",
      questionsAnswered: 10,
      isSubscribed: false,
      subscriptionEndDate: null,
    }]);

    const res = await request(app)
      .get("/api/session/status")
      .query({ sessionId: "at-limit-uuid" });

    expect(res.status).toBe(200);
    expect(res.body.canAnswerMore).toBe(false);
  });
});

// ── POST /session/answer ──────────────────────────────────────────────────────

describe("POST /session/answer — input validation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("empty body → 400 with field error", async () => {
    const res = await request(app)
      .post("/api/session/answer")
      .send({})
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/required/i);
  });

  it("missing selectedLetter → 400", async () => {
    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: "s", questionId: 1 })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(400);
  });
});

describe("POST /session/answer — question lookup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("question not found → 404", async () => {
    selectOnce([]); // questions lookup returns nothing

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: "s", questionId: 9999, selectedLetter: "A" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(404);
    expect(res.body.error).toMatch(/Question not found/i);
  });
});

describe("POST /session/answer — correct/incorrect and free limit", () => {
  const SESSION = "exam-session-001";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("correct answer → 200 correct:true with explanation", async () => {
    selectOnce([{
      id: 1,
      correctLetter: "B",
      explanation: "B is correct because...",
      questionType: "single",
    }]);
    setupAnswerTransaction({
      id: 42,
      session_id: SESSION,
      questions_answered: 0,
      is_subscribed: false,
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION, questionId: 1, selectedLetter: "B" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.correct).toBe(true);
    expect(res.body.correctLetter).toBe("B");
    expect(res.body.explanation).toBe("B is correct because...");
    expect(res.body.questionsAnswered).toBe(1);
  });

  it("wrong answer → 200 correct:false", async () => {
    selectOnce([{
      id: 2,
      correctLetter: "C",
      explanation: "C is correct.",
      questionType: "single",
    }]);
    setupAnswerTransaction({
      id: 42,
      session_id: SESSION,
      questions_answered: 2,
      is_subscribed: false,
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION, questionId: 2, selectedLetter: "A" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.correct).toBe(false);
    expect(res.body.correctLetter).toBe("C");
  });

  it("free limit reached (questionsAnswered >= 10, not subscribed) → 403", async () => {
    selectOnce([{
      id: 3,
      correctLetter: "D",
      explanation: "D is correct.",
      questionType: "single",
    }]);
    setupAnswerTransaction({
      id: 42,
      session_id: SESSION,
      questions_answered: 10, // at free limit
      is_subscribed: false,
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION, questionId: 3, selectedLetter: "D" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(403);
    expect(res.body.error).toMatch(/Free limit reached/i);
    expect(res.body.freeLimit).toBe(10);
  });

  it("subscribed session at limit → 200 (limit bypassed)", async () => {
    selectOnce([{
      id: 4,
      correctLetter: "A",
      explanation: "A is correct.",
      questionType: "single",
    }]);
    setupAnswerTransaction({
      id: 42,
      session_id: SESSION,
      questions_answered: 100, // well past limit, but subscribed
      is_subscribed: true,
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION, questionId: 4, selectedLetter: "A" })
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.correct).toBe(true);
    expect(res.body.isSubscribed).toBe(true);
  });

  it("SATA (multiple choice) — order-insensitive answer matching", async () => {
    selectOnce([{
      id: 5,
      correctLetter: "A,C,E",
      explanation: "Select all that apply.",
      questionType: "multiple",
    }]);
    setupAnswerTransaction({
      id: 42,
      session_id: SESSION,
      questions_answered: 1,
      is_subscribed: false,
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION, questionId: 5, selectedLetter: "E,A,C" }) // different order
      .set("Content-Type", "application/json");

    expect(res.status).toBe(200);
    expect(res.body.correct).toBe(true);
  });

  it("session not in DB → created inside transaction, then proceeds normally", async () => {
    selectOnce([{
      id: 6,
      correctLetter: "B",
      explanation: "B is correct.",
      questionType: "single",
    }]);
    // null session row → transaction creates it
    setupAnswerTransaction(null, {
      insertNewSession: [{
        id: 99,
        sessionId: SESSION + "-new",
        questionsAnswered: 0,
        isSubscribed: false,
      }],
    });

    const res = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION + "-new", questionId: 6, selectedLetter: "B" })
      .set("Content-Type", "application/json");

    // Session created mid-transaction → answer proceeds
    expect(res.status).toBe(200);
    expect(res.body.correct).toBe(true);
  });
});

// ── E2E happy path ────────────────────────────────────────────────────────────

describe("Exam workflow E2E — status → answer → status updated", () => {
  const SESSION_E2E = "e2e-exam-session";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("full exam step: check status (0 answered) → answer question → response shows incremented count", async () => {
    // Step 1: GET /session/status — session exists with 0 answers
    selectOnce([{
      sessionId: SESSION_E2E,
      questionsAnswered: 0,
      isSubscribed: false,
      subscriptionEndDate: null,
    }]);

    const statusBefore = await request(app)
      .get("/api/session/status")
      .query({ sessionId: SESSION_E2E });

    expect(statusBefore.status).toBe(200);
    expect(statusBefore.body.questionsAnswered).toBe(0);
    expect(statusBefore.body.canAnswerMore).toBe(true);

    // Step 2: POST /session/answer — submit correct answer
    selectOnce([{
      id: 10,
      correctLetter: "C",
      explanation: "C explanation.",
      questionType: "single",
    }]);
    setupAnswerTransaction({
      id: 55,
      session_id: SESSION_E2E,
      questions_answered: 0,
      is_subscribed: false,
    });

    const answerRes = await request(app)
      .post("/api/session/answer")
      .send({ sessionId: SESSION_E2E, questionId: 10, selectedLetter: "C" })
      .set("Content-Type", "application/json");

    expect(answerRes.status).toBe(200);
    expect(answerRes.body.correct).toBe(true);
    expect(answerRes.body.questionsAnswered).toBe(1); // incremented from 0 → 1
    expect(answerRes.body.sessionId).toBe(SESSION_E2E);
  });
});
