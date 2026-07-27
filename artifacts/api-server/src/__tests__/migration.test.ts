/**
 * Item 4 — Option B migration gate tests.
 *
 * Strategy:
 *  - getSessionAccessDecision() is a pure function → tested exhaustively with no mocks.
 *  - verifySessionAccess() middleware uses getAuth() + DB → tested by injecting
 *    fake req/res/next objects and mocking both dependencies.
 *  - POST /session/claim scenarios are covered by testing the decision branches
 *    that the route logic exercises (covered via pure-function and middleware tests
 *    rather than full HTTP, which would require DB + Clerk environment setup).
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { getSessionAccessDecision } from "../lib/sessionAuth";

// ── Pure decision function ────────────────────────────────────────────────────

describe("getSessionAccessDecision — pure decision logic", () => {
  const USER_A = "user_clerk_aaaa";
  const USER_B = "user_clerk_bbbb";
  const SESSION_X = "anon-uuid-xxxx";
  const SESSION_Y = "anon-uuid-yyyy";

  it("Rule 1: no Clerk user → allowed (anonymous)", () => {
    const d = getSessionAccessDecision(null, SESSION_X, null);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("anonymous");
  });

  it("Rule 2: clerkUserId === sessionId → allowed (clerk-native session)", () => {
    const d = getSessionAccessDecision(USER_A, USER_A, null);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("clerk-native");
  });

  it("Rule 3: claim exists and matches requesting user → allowed", () => {
    const claim = { clerkUserId: USER_A, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_A, SESSION_X, claim);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("claimed-by-owner");
  });

  it("Rule 4: claim exists but belongs to different user → forbidden", () => {
    const claim = { clerkUserId: USER_B, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_A, SESSION_X, claim);
    expect(d.allowed).toBe(false);
    if (!d.allowed) {
      expect(d.code).toBe("SESSION_OWNED_BY_OTHER_USER");
    }
  });

  it("Rule 5: Clerk user present but session unclaimed → allowed", () => {
    const d = getSessionAccessDecision(USER_A, SESSION_X, null);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("unclaimed-session");
  });

  it("cross-user access: userB cannot access session claimed by userA", () => {
    const claim = { clerkUserId: USER_A, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_B, SESSION_X, claim);
    expect(d.allowed).toBe(false);
  });

  it("owner can access their own claimed session", () => {
    const claim = { clerkUserId: USER_A, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_A, SESSION_X, claim);
    expect(d.allowed).toBe(true);
  });

  it("owner can access a DIFFERENT unclaimed session (Rule 5)", () => {
    // User A has claimed SESSION_X, but is accessing SESSION_Y (unclaimed) → allowed
    const d = getSessionAccessDecision(USER_A, SESSION_Y, null);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("unclaimed-session");
  });
});

// ── verifySessionAccess middleware ────────────────────────────────────────────

// We mock at the module level so we can control getAuth and db per test
vi.mock("@clerk/express", () => ({
  getAuth: vi.fn(),
}));

vi.mock("@workspace/db", () => {
  const selectMock = vi.fn();
  const fromMock = vi.fn(() => ({ where: vi.fn(() => ({ limit: selectMock })) }));
  const dbMock = { select: vi.fn(() => ({ from: fromMock })) };
  return {
    db: dbMock,
    sessionClaimsTable: {},
    sessionsTable: {},
    answersTable: {},
    questionsTable: {},
  };
});

vi.mock("drizzle-orm", () => ({
  eq: vi.fn((col: unknown, val: unknown) => ({ col, val })),
}));

import { getAuth } from "@clerk/express";
import { db } from "@workspace/db";
import { verifySessionAccess } from "../lib/sessionAuth";

function makeReqResNext(sessionId: string, location: "query" | "body" = "query") {
  const req: any = location === "query"
    ? { query: { sessionId }, body: {} }
    : { query: {}, body: { sessionId } };
  const res: any = {
    status: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
  };
  const next = vi.fn();
  return { req, res, next };
}

function mockDbSelect(rows: any[]) {
  const limitMock = vi.fn().mockResolvedValue(rows);
  const whereMock = vi.fn(() => ({ limit: limitMock }));
  const fromMock = vi.fn(() => ({ where: whereMock }));
  (db.select as Mock).mockReturnValue({ from: fromMock });
  return { limitMock, whereMock, fromMock };
}

describe("verifySessionAccess — middleware", () => {
  const USER_A = "user_clerk_aaaa";
  const USER_B = "user_clerk_bbbb";
  const SESSION_X = "anon-uuid-xxxx";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("no sessionId in request → next() called without DB lookup", async () => {
    const { req, res, next } = makeReqResNext("" as any);
    req.query = {};
    req.body = {};
    (getAuth as Mock).mockReturnValue({ userId: USER_A });

    await verifySessionAccess(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(db.select).not.toHaveBeenCalled();
  });

  it("no Clerk JWT → next() called (anonymous allowed)", async () => {
    const { req, res, next } = makeReqResNext(SESSION_X);
    (getAuth as Mock).mockReturnValue({ userId: null });

    await verifySessionAccess(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(db.select).not.toHaveBeenCalled();
  });

  it("JWT present + sessionId === userId → next() (clerk-native, no DB lookup)", async () => {
    const { req, res, next } = makeReqResNext(USER_A);
    (getAuth as Mock).mockReturnValue({ userId: USER_A });

    await verifySessionAccess(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(db.select).not.toHaveBeenCalled();
  });

  it("JWT present + session claimed by requesting user → next()", async () => {
    const { req, res, next } = makeReqResNext(SESSION_X);
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    mockDbSelect([{ clerkUserId: USER_A, sessionId: SESSION_X }]);

    await verifySessionAccess(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(res.status).not.toHaveBeenCalled();
  });

  it("JWT present + session claimed by DIFFERENT user → 403", async () => {
    const { req, res, next } = makeReqResNext(SESSION_X);
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    mockDbSelect([{ clerkUserId: USER_B, sessionId: SESSION_X }]);

    await verifySessionAccess(req, res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(403);
    expect(res.json).toHaveBeenCalledWith(
      expect.objectContaining({ code: "SESSION_OWNED_BY_OTHER_USER" })
    );
  });

  it("JWT present + session unclaimed → next() (rule 5 permissive)", async () => {
    const { req, res, next } = makeReqResNext(SESSION_X);
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    mockDbSelect([]);   // no claim record found

    await verifySessionAccess(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(res.status).not.toHaveBeenCalled();
  });

  it("sessionId in body (POST route) is also checked", async () => {
    const { req, res, next } = makeReqResNext(SESSION_X, "body");
    (getAuth as Mock).mockReturnValue({ userId: USER_A });
    mockDbSelect([{ clerkUserId: USER_B, sessionId: SESSION_X }]);

    await verifySessionAccess(req, res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(403);
  });
});

// ── /session/claim route logic scenarios ─────────────────────────────────────
// These test the decision branches as pure logic using getSessionAccessDecision.

describe("session/claim — decision branches via pure function", () => {
  const USER_A = "user_clerk_aaaa";
  const USER_B = "user_clerk_bbbb";
  const SESSION_X = "anon-uuid-xxxx";

  it("valid claim: no prior claim, session exists → access decision is unclaimed-session before claim, owner after", () => {
    // Before claim: unclaimed session, userA present → allowed (rule 5)
    const before = getSessionAccessDecision(USER_A, SESSION_X, null);
    expect(before.allowed).toBe(true);
    expect(before.reason).toBe("unclaimed-session");

    // After claim: claim record exists for userA → allowed (rule 3)
    const after = getSessionAccessDecision(USER_A, SESSION_X, { clerkUserId: USER_A, sessionId: SESSION_X });
    expect(after.allowed).toBe(true);
    expect(after.reason).toBe("claimed-by-owner");
  });

  it("duplicate claim (same user re-claims): decision is still claimed-by-owner", () => {
    const claim = { clerkUserId: USER_A, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_A, SESSION_X, claim);
    expect(d.allowed).toBe(true);
    expect(d.reason).toBe("claimed-by-owner");
  });

  it("cross-user claim attempt: userB tries to access session already claimed by userA → 403", () => {
    const claim = { clerkUserId: USER_A, sessionId: SESSION_X };
    const d = getSessionAccessDecision(USER_B, SESSION_X, claim);
    expect(d.allowed).toBe(false);
    if (!d.allowed) expect(d.code).toBe("SESSION_OWNED_BY_OTHER_USER");
  });

  it("anonymous user accessing any session → always allowed (no Clerk JWT)", () => {
    const d1 = getSessionAccessDecision(null, SESSION_X, null);
    const d2 = getSessionAccessDecision(null, SESSION_X, { clerkUserId: USER_A, sessionId: SESSION_X });
    expect(d1.allowed).toBe(true);
    expect(d2.allowed).toBe(true);
  });
});
