/**
 * Questions — GET /questions (list) and GET /questions/:id (detail + reveal logic)
 *
 * Coverage targets in src/routes/questions.ts:
 *  - GET /questions — no filter, category filter
 *  - GET /questions/:id — invalid id (400), not found (404),
 *    reveal logic (subscribed / answered / neither / no sessionId),
 *    options normalization (array kept, object dict converted to array),
 *    missing session row treated as not-subscribed
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks (hoisted by Vitest) ─────────────────────────────────────────

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
  and: vi.fn((...args: unknown[]) => ({ and: args })),
  asc: vi.fn((col: unknown) => ({ asc: col })),
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

/**
 * GET /questions (no category): db.select().from().orderBy() — awaited directly.
 * mockResolvedValue on orderBy makes the query thenable.
 */
function selectOrderByOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockResolvedValue(rows),
    }),
  });
}

/**
 * GET /questions?category=X: db.select().from().orderBy().where() — awaited
 * from the .where() at the end of the chain.
 */
function selectOrderByWhereOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

/**
 * GET /questions/:id, session, and answers checks: db.select().from().where().limit(1).
 * Covers questionsTable lookup, sessionsTable subscription check, answersTable check.
 */
function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
        // answers check uses .where(and(...)).limit() — same chain
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue(rows),
        }),
      }),
    }),
  });
}

// ── Shared fixture ────────────────────────────────────────────────────────────

/** Standard question row returned by the DB — options already in array format. */
const Q_ARRAY_OPTS = {
  id: 42,
  questionNumber: 42,
  category: "Safety and Infection Control",
  text: "A nurse is caring for a client. First action?",
  options: [{ letter: "A", text: "Assess" }, { letter: "B", text: "Act" }],
  correctLetter: "A",
  explanation: "Assess before acting.",
  questionType: "single",
  imageUrl: null,
};

/** Same question but options in old dict format {A: text, B: text}. */
const Q_DICT_OPTS = {
  ...Q_ARRAY_OPTS,
  options: { A: "Assess", B: "Act" } as unknown as any,
};

// ── GET /questions ────────────────────────────────────────────────────────────

describe("GET /questions — list", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no category param → all questions returned (orderBy chain, no .where)", async () => {
    selectOrderByOnce([
      { id: 1, questionNumber: 1, category: "Safety" },
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(2);
    expect(res.body[0]).toMatchObject({ id: 1, category: "Safety" });
  });

  it("category=Pharmacology → filtered list via .where() on orderBy result", async () => {
    selectOrderByWhereOnce([
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions?category=Pharmacology");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(1);
    expect(res.body[0].category).toBe("Pharmacology");
  });
});

// ── GET /questions/:id — validation ──────────────────────────────────────────

describe("GET /questions/:id — input validation", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("id is not numeric → GetQuestionParams.safeParse fails → 400", async () => {
    const res = await request(app).get("/api/questions/not-a-number");

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid question id");
    // No DB call should have been made
    expect(db.select).not.toHaveBeenCalled();
  });

  it("numeric id but question not in DB → 404", async () => {
    selectOnce([]); // DB returns no rows

    const res = await request(app).get("/api/questions/999");

    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Question not found");
  });
});

// ── GET /questions/:id — answer reveal logic ──────────────────────────────────

describe("GET /questions/:id — reveal logic (correctLetter / explanation gating)", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no sessionId in request → correctLetter=null, explanation=null (free-tier, no DB subscription check)", async () => {
    selectOnce([Q_ARRAY_OPTS]); // question found

    const res = await request(app).get("/api/questions/42"); // no ?sessionId

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    // Only one DB select (question lookup) — no session or answer checks
    expect(db.select).toHaveBeenCalledTimes(1);
  });

  it("sessionId + isSubscribed=true → correctLetter and explanation revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: true }]);     // session → subscribed → reveal immediately

    const res = await request(app).get("/api/questions/42?sessionId=sess-subscriber");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    // Two selects: question + session; NO third select (answer check skipped)
    expect(db.select).toHaveBeenCalledTimes(2);
  });

  it("sessionId + not subscribed + already answered → answers revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([{ id: 77 }]);                 // answer record found → reveal

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-answered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId + not subscribed + not yet answered → answers hidden", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([]);                           // no answer record → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-unanswered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId present but session row missing → treated as not-subscribed, answers hidden", async () => {
    // session?.isSubscribed = undefined (falsy) → goes to answered check
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([]);                           // session not found → undefined
    selectOnce([]);                           // no answer record either → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-ghost");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
  });
});

// ── GET /questions/:id — options normalization ────────────────────────────────

describe("GET /questions/:id — options format normalization", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("options already an array → returned unchanged (Array.isArray branch FALSE, no conversion)", async () => {
    // Q_ARRAY_OPTS.options is [{letter,text}] — no conversion needed
    selectOnce([Q_ARRAY_OPTS]);
    // no sessionId so no further selects needed

    const res = await request(app).get("/api/questions/42");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    expect(res.body.options[0]).toMatchObject({ letter: "A", text: "Assess" });
  });

  it("options is a plain object {A:text} → converted to [{letter,text}] array (old DB format)", async () => {
    // Q_DICT_OPTS.options = { A: "Assess", B: "Act" } — must be converted
    selectOnce([Q_DICT_OPTS]);
    selectOnce([{ isSubscribed: true }]); // subscribed so we can also verify correctLetter

    const res = await request(app).get("/api/questions/42?sessionId=sess-sub");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    // Both entries from the dict should appear as {letter, text} objects
    const letters = res.body.options.map((o: any) => o.letter).sort();
    expect(letters).toEqual(["A", "B"]);
    expect(res.body.options[0]).toHaveProperty("letter");
    expect(res.body.options[0]).toHaveProperty("text");
  });
});* Questions — GET /questions (list) and GET /questions/:id (detail + reveal logic)
 *
 * Coverage targets in src/routes/questions.ts:
 *  - GET /questions — no filter, category filter
 *  - GET /questions/:id — invalid id (400), not found (404),
 *    reveal logic (subscribed / answered / neither / no sessionId),
 *    options normalization (array kept, object dict converted to array),
 *    missing session row treated as not-subscribed
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks (hoisted by Vitest) ─────────────────────────────────────────

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
  and: vi.fn((...args: unknown[]) => ({ and: args })),
  asc: vi.fn((col: unknown) => ({ asc: col })),
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

/**
 * GET /questions (no category): db.select().from().orderBy() — awaited directly.
 * mockResolvedValue on orderBy makes the query thenable.
 */
function selectOrderByOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockResolvedValue(rows),
    }),
  });
}

/**
 * GET /questions?category=X: db.select().from().orderBy().where() — awaited
 * from the .where() at the end of the chain.
 */
function selectOrderByWhereOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

/**
 * GET /questions/:id, session, and answers checks: db.select().from().where().limit(1).
 * Covers questionsTable lookup, sessionsTable subscription check, answersTable check.
 */
function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
        // answers check uses .where(and(...)).limit() — same chain
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue(rows),
        }),
      }),
    }),
  });
}

// ── Shared fixture ────────────────────────────────────────────────────────────

/** Standard question row returned by the DB — options already in array format. */
const Q_ARRAY_OPTS = {
  id: 42,
  questionNumber: 42,
  category: "Safety and Infection Control",
  text: "A nurse is caring for a client. First action?",
  options: [{ letter: "A", text: "Assess" }, { letter: "B", text: "Act" }],
  correctLetter: "A",
  explanation: "Assess before acting.",
  questionType: "single",
  imageUrl: null,
};

/** Same question but options in old dict format {A: text, B: text}. */
const Q_DICT_OPTS = {
  ...Q_ARRAY_OPTS,
  options: { A: "Assess", B: "Act" } as unknown as any,
};

// ── GET /questions ────────────────────────────────────────────────────────────

describe("GET /questions — list", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no category param → all questions returned (orderBy chain, no .where)", async () => {
    selectOrderByOnce([
      { id: 1, questionNumber: 1, category: "Safety" },
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(2);
    expect(res.body[0]).toMatchObject({ id: 1, category: "Safety" });
  });

  it("category=Pharmacology → filtered list via .where() on orderBy result", async () => {
    selectOrderByWhereOnce([
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions?category=Pharmacology");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(1);
    expect(res.body[0].category).toBe("Pharmacology");
  });
});

// ── GET /questions/:id — validation ──────────────────────────────────────────

describe("GET /questions/:id — input validation", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("id is not numeric → GetQuestionParams.safeParse fails → 400", async () => {
    const res = await request(app).get("/api/questions/not-a-number");

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid question id");
    // No DB call should have been made
    expect(db.select).not.toHaveBeenCalled();
  });

  it("numeric id but question not in DB → 404", async () => {
    selectOnce([]); // DB returns no rows

    const res = await request(app).get("/api/questions/999");

    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Question not found");
  });
});

// ── GET /questions/:id — answer reveal logic ──────────────────────────────────

describe("GET /questions/:id — reveal logic (correctLetter / explanation gating)", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no sessionId in request → correctLetter=null, explanation=null (free-tier, no DB subscription check)", async () => {
    selectOnce([Q_ARRAY_OPTS]); // question found

    const res = await request(app).get("/api/questions/42"); // no ?sessionId

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    // Only one DB select (question lookup) — no session or answer checks
    expect(db.select).toHaveBeenCalledTimes(1);
  });

  it("sessionId + isSubscribed=true → correctLetter and explanation revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: true }]);     // session → subscribed → reveal immediately

    const res = await request(app).get("/api/questions/42?sessionId=sess-subscriber");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    // Two selects: question + session; NO third select (answer check skipped)
    expect(db.select).toHaveBeenCalledTimes(2);
  });

  it("sessionId + not subscribed + already answered → answers revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([{ id: 77 }]);                 // answer record found → reveal

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-answered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId + not subscribed + not yet answered → answers hidden", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([]);                           // no answer record → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-unanswered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId present but session row missing → treated as not-subscribed, answers hidden", async () => {
    // session?.isSubscribed = undefined (falsy) → goes to answered check
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([]);                           // session not found → undefined
    selectOnce([]);                           // no answer record either → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-ghost");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
  });
});

// ── GET /questions/:id — options normalization ────────────────────────────────

describe("GET /questions/:id — options format normalization", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("options already an array → returned unchanged (Array.isArray branch FALSE, no conversion)", async () => {
    // Q_ARRAY_OPTS.options is [{letter,text}] — no conversion needed
    selectOnce([Q_ARRAY_OPTS]);
    // no sessionId so no further selects needed

    const res = await request(app).get("/api/questions/42");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    expect(res.body.options[0]).toMatchObject({ letter: "A", text: "Assess" });
  });

  it("options is a plain object {A:text} → converted to [{letter,text}] array (old DB format)", async () => {
    // Q_DICT_OPTS.options = { A: "Assess", B: "Act" } — must be converted
    selectOnce([Q_DICT_OPTS]);
    selectOnce([{ isSubscribed: true }]); // subscribed so we can also verify correctLetter

    const res = await request(app).get("/api/questions/42?sessionId=sess-sub");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    // Both entries from the dict should appear as {letter, text} objects
    const letters = res.body.options.map((o: any) => o.letter).sort();
    expect(letters).toEqual(["A", "B"]);
    expect(res.body.options[0]).toHaveProperty("letter");
    expect(res.body.options[0]).toHaveProperty("text");
  });
});* Questions — GET /questions (list) and GET /questions/:id (detail + reveal logic)
 *
 * Coverage targets in src/routes/questions.ts:
 *  - GET /questions — no filter, category filter
 *  - GET /questions/:id — invalid id (400), not found (404),
 *    reveal logic (subscribed / answered / neither / no sessionId),
 *    options normalization (array kept, object dict converted to array),
 *    missing session row treated as not-subscribed
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks (hoisted by Vitest) ─────────────────────────────────────────

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
  and: vi.fn((...args: unknown[]) => ({ and: args })),
  asc: vi.fn((col: unknown) => ({ asc: col })),
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

/**
 * GET /questions (no category): db.select().from().orderBy() — awaited directly.
 * mockResolvedValue on orderBy makes the query thenable.
 */
function selectOrderByOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockResolvedValue(rows),
    }),
  });
}

/**
 * GET /questions?category=X: db.select().from().orderBy().where() — awaited
 * from the .where() at the end of the chain.
 */
function selectOrderByWhereOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

/**
 * GET /questions/:id, session, and answers checks: db.select().from().where().limit(1).
 * Covers questionsTable lookup, sessionsTable subscription check, answersTable check.
 */
function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
        // answers check uses .where(and(...)).limit() — same chain
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue(rows),
        }),
      }),
    }),
  });
}

// ── Shared fixture ────────────────────────────────────────────────────────────

/** Standard question row returned by the DB — options already in array format. */
const Q_ARRAY_OPTS = {
  id: 42,
  questionNumber: 42,
  category: "Safety and Infection Control",
  text: "A nurse is caring for a client. First action?",
  options: [{ letter: "A", text: "Assess" }, { letter: "B", text: "Act" }],
  correctLetter: "A",
  explanation: "Assess before acting.",
  questionType: "single",
  imageUrl: null,
};

/** Same question but options in old dict format {A: text, B: text}. */
const Q_DICT_OPTS = {
  ...Q_ARRAY_OPTS,
  options: { A: "Assess", B: "Act" } as unknown as any,
};

// ── GET /questions ────────────────────────────────────────────────────────────

describe("GET /questions — list", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no category param → all questions returned (orderBy chain, no .where)", async () => {
    selectOrderByOnce([
      { id: 1, questionNumber: 1, category: "Safety" },
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(2);
    expect(res.body[0]).toMatchObject({ id: 1, category: "Safety" });
  });

  it("category=Pharmacology → filtered list via .where() on orderBy result", async () => {
    selectOrderByWhereOnce([
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions?category=Pharmacology");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(1);
    expect(res.body[0].category).toBe("Pharmacology");
  });
});

// ── GET /questions/:id — validation ──────────────────────────────────────────

describe("GET /questions/:id — input validation", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("id is not numeric → GetQuestionParams.safeParse fails → 400", async () => {
    const res = await request(app).get("/api/questions/not-a-number");

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid question id");
    // No DB call should have been made
    expect(db.select).not.toHaveBeenCalled();
  });

  it("numeric id but question not in DB → 404", async () => {
    selectOnce([]); // DB returns no rows

    const res = await request(app).get("/api/questions/999");

    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Question not found");
  });
});

// ── GET /questions/:id — answer reveal logic ──────────────────────────────────

describe("GET /questions/:id — reveal logic (correctLetter / explanation gating)", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no sessionId in request → correctLetter=null, explanation=null (free-tier, no DB subscription check)", async () => {
    selectOnce([Q_ARRAY_OPTS]); // question found

    const res = await request(app).get("/api/questions/42"); // no ?sessionId

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    // Only one DB select (question lookup) — no session or answer checks
    expect(db.select).toHaveBeenCalledTimes(1);
  });

  it("sessionId + isSubscribed=true → correctLetter and explanation revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: true }]);     // session → subscribed → reveal immediately

    const res = await request(app).get("/api/questions/42?sessionId=sess-subscriber");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    // Two selects: question + session; NO third select (answer check skipped)
    expect(db.select).toHaveBeenCalledTimes(2);
  });

  it("sessionId + not subscribed + already answered → answers revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([{ id: 77 }]);                 // answer record found → reveal

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-answered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId + not subscribed + not yet answered → answers hidden", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([]);                           // no answer record → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-unanswered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId present but session row missing → treated as not-subscribed, answers hidden", async () => {
    // session?.isSubscribed = undefined (falsy) → goes to answered check
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([]);                           // session not found → undefined
    selectOnce([]);                           // no answer record either → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-ghost");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
  });
});

// ── GET /questions/:id — options normalization ────────────────────────────────

describe("GET /questions/:id — options format normalization", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("options already an array → returned unchanged (Array.isArray branch FALSE, no conversion)", async () => {
    // Q_ARRAY_OPTS.options is [{letter,text}] — no conversion needed
    selectOnce([Q_ARRAY_OPTS]);
    // no sessionId so no further selects needed

    const res = await request(app).get("/api/questions/42");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    expect(res.body.options[0]).toMatchObject({ letter: "A", text: "Assess" });
  });

  it("options is a plain object {A:text} → converted to [{letter,text}] array (old DB format)", async () => {
    // Q_DICT_OPTS.options = { A: "Assess", B: "Act" } — must be converted
    selectOnce([Q_DICT_OPTS]);
    selectOnce([{ isSubscribed: true }]); // subscribed so we can also verify correctLetter

    const res = await request(app).get("/api/questions/42?sessionId=sess-sub");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    // Both entries from the dict should appear as {letter, text} objects
    const letters = res.body.options.map((o: any) => o.letter).sort();
    expect(letters).toEqual(["A", "B"]);
    expect(res.body.options[0]).toHaveProperty("letter");
    expect(res.body.options[0]).toHaveProperty("text");
  });
});* Questions — GET /questions (list) and GET /questions/:id (detail + reveal logic)
 *
 * Coverage targets in src/routes/questions.ts:
 *  - GET /questions — no filter, category filter
 *  - GET /questions/:id — invalid id (400), not found (404),
 *    reveal logic (subscribed / answered / neither / no sessionId),
 *    options normalization (array kept, object dict converted to array),
 *    missing session row treated as not-subscribed
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import request from "supertest";

// ── Module mocks (hoisted by Vitest) ─────────────────────────────────────────

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
  and: vi.fn((...args: unknown[]) => ({ and: args })),
  asc: vi.fn((col: unknown) => ({ asc: col })),
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

/**
 * GET /questions (no category): db.select().from().orderBy() — awaited directly.
 * mockResolvedValue on orderBy makes the query thenable.
 */
function selectOrderByOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockResolvedValue(rows),
    }),
  });
}

/**
 * GET /questions?category=X: db.select().from().orderBy().where() — awaited
 * from the .where() at the end of the chain.
 */
function selectOrderByWhereOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      orderBy: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(rows),
      }),
    }),
  });
}

/**
 * GET /questions/:id, session, and answers checks: db.select().from().where().limit(1).
 * Covers questionsTable lookup, sessionsTable subscription check, answersTable check.
 */
function selectOnce(rows: any[]) {
  (db.select as Mock).mockReturnValueOnce({
    from: vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue(rows),
        // answers check uses .where(and(...)).limit() — same chain
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue(rows),
        }),
      }),
    }),
  });
}

// ── Shared fixture ────────────────────────────────────────────────────────────

/** Standard question row returned by the DB — options already in array format. */
const Q_ARRAY_OPTS = {
  id: 42,
  questionNumber: 42,
  category: "Safety and Infection Control",
  text: "A nurse is caring for a client. First action?",
  options: [{ letter: "A", text: "Assess" }, { letter: "B", text: "Act" }],
  correctLetter: "A",
  explanation: "Assess before acting.",
  questionType: "single",
  imageUrl: null,
};

/** Same question but options in old dict format {A: text, B: text}. */
const Q_DICT_OPTS = {
  ...Q_ARRAY_OPTS,
  options: { A: "Assess", B: "Act" } as unknown as any,
};

// ── GET /questions ────────────────────────────────────────────────────────────

describe("GET /questions — list", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no category param → all questions returned (orderBy chain, no .where)", async () => {
    selectOrderByOnce([
      { id: 1, questionNumber: 1, category: "Safety" },
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(2);
    expect(res.body[0]).toMatchObject({ id: 1, category: "Safety" });
  });

  it("category=Pharmacology → filtered list via .where() on orderBy result", async () => {
    selectOrderByWhereOnce([
      { id: 2, questionNumber: 2, category: "Pharmacology" },
    ]);

    const res = await request(app).get("/api/questions?category=Pharmacology");

    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(1);
    expect(res.body[0].category).toBe("Pharmacology");
  });
});

// ── GET /questions/:id — validation ──────────────────────────────────────────

describe("GET /questions/:id — input validation", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("id is not numeric → GetQuestionParams.safeParse fails → 400", async () => {
    const res = await request(app).get("/api/questions/not-a-number");

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Invalid question id");
    // No DB call should have been made
    expect(db.select).not.toHaveBeenCalled();
  });

  it("numeric id but question not in DB → 404", async () => {
    selectOnce([]); // DB returns no rows

    const res = await request(app).get("/api/questions/999");

    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Question not found");
  });
});

// ── GET /questions/:id — answer reveal logic ──────────────────────────────────

describe("GET /questions/:id — reveal logic (correctLetter / explanation gating)", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("no sessionId in request → correctLetter=null, explanation=null (free-tier, no DB subscription check)", async () => {
    selectOnce([Q_ARRAY_OPTS]); // question found

    const res = await request(app).get("/api/questions/42"); // no ?sessionId

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    // Only one DB select (question lookup) — no session or answer checks
    expect(db.select).toHaveBeenCalledTimes(1);
  });

  it("sessionId + isSubscribed=true → correctLetter and explanation revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: true }]);     // session → subscribed → reveal immediately

    const res = await request(app).get("/api/questions/42?sessionId=sess-subscriber");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    // Two selects: question + session; NO third select (answer check skipped)
    expect(db.select).toHaveBeenCalledTimes(2);
  });

  it("sessionId + not subscribed + already answered → answers revealed", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([{ id: 77 }]);                 // answer record found → reveal

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-answered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBe("A");
    expect(res.body.explanation).toBe("Assess before acting.");
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId + not subscribed + not yet answered → answers hidden", async () => {
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([{ isSubscribed: false }]);    // session → not subscribed
    selectOnce([]);                           // no answer record → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-free-unanswered");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
    expect(db.select).toHaveBeenCalledTimes(3);
  });

  it("sessionId present but session row missing → treated as not-subscribed, answers hidden", async () => {
    // session?.isSubscribed = undefined (falsy) → goes to answered check
    selectOnce([Q_ARRAY_OPTS]);               // question
    selectOnce([]);                           // session not found → undefined
    selectOnce([]);                           // no answer record either → hide

    const res = await request(app).get("/api/questions/42?sessionId=sess-ghost");

    expect(res.status).toBe(200);
    expect(res.body.correctLetter).toBeNull();
    expect(res.body.explanation).toBeNull();
  });
});

// ── GET /questions/:id — options normalization ────────────────────────────────

describe("GET /questions/:id — options format normalization", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("options already an array → returned unchanged (Array.isArray branch FALSE, no conversion)", async () => {
    // Q_ARRAY_OPTS.options is [{letter,text}] — no conversion needed
    selectOnce([Q_ARRAY_OPTS]);
    // no sessionId so no further selects needed

    const res = await request(app).get("/api/questions/42");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    expect(res.body.options[0]).toMatchObject({ letter: "A", text: "Assess" });
  });

  it("options is a plain object {A:text} → converted to [{letter,text}] array (old DB format)", async () => {
    // Q_DICT_OPTS.options = { A: "Assess", B: "Act" } — must be converted
    selectOnce([Q_DICT_OPTS]);
    selectOnce([{ isSubscribed: true }]); // subscribed so we can also verify correctLetter

    const res = await request(app).get("/api/questions/42?sessionId=sess-sub");

    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.options)).toBe(true);
    // Both entries from the dict should appear as {letter, text} objects
    const letters = res.body.options.map((o: any) => o.letter).sort();
    expect(letters).toEqual(["A", "B"]);
    expect(res.body.options[0]).toHaveProperty("letter");
    expect(res.body.options[0]).toHaveProperty("text");
  });
});
