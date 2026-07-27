/**
 * Option B — Anonymous-session → Clerk-account migration gate.
 *
 * Rules (evaluated in order, first match wins):
 *  1. No Clerk JWT in request  →  allow  (anonymous user, unchanged behaviour)
 *  2. JWT present + sessionId === clerkUserId  →  allow  (Clerk-native session)
 *  3. JWT present + sessionId claimed by THIS user  →  allow  (migrated session)
 *  4. JWT present + sessionId claimed by DIFFERENT user  →  403
 *  5. JWT present + sessionId unclaimed  →  allow  (user hasn't called /session/claim yet)
 *
 * Rule 5 is deliberately permissive: blocking here would break the common
 * "start quiz anonymously, then subscribe" flow.  The /session/claim endpoint
 * is the explicit opt-in; only after claiming does the gate enforce ownership.
 */

import { getAuth } from "@clerk/express";
import { db, sessionClaimsTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import type { Request, Response, NextFunction } from "express";
import { z } from "zod";

// ── Zod schema (shared with session route) ─────────────────────────────────
export const ClaimSessionBody = z.object({
  sessionId: z.string().min(1, "sessionId is required"),
});

// ── Pure decision function — testable without Express or DB ────────────────

export type AccessDecision =
  | { allowed: true; reason: string }
  | { allowed: false; reason: string; code: string };

/**
 * Given the Clerk user, the requested sessionId, and the claim record (if any),
 * return an access decision.  All inputs are plain values — no I/O here.
 */
export function getSessionAccessDecision(
  clerkUserId: string | null,
  sessionId: string,
  claim: { clerkUserId: string; sessionId: string } | null
): AccessDecision {
  // Rule 1 — No Clerk user
  if (!clerkUserId) {
    return { allowed: true, reason: "anonymous" };
  }

  // Rule 2 — Clerk-native session (user uses their own ID as sessionId)
  if (sessionId === clerkUserId) {
    return { allowed: true, reason: "clerk-native" };
  }

  // Rule 3 / 4 — Check claim table
  if (claim) {
    if (claim.clerkUserId === clerkUserId) {
      return { allowed: true, reason: "claimed-by-owner" };
    }
    return {
      allowed: false,
      reason: "session owned by different Clerk user",
      code: "SESSION_OWNED_BY_OTHER_USER",
    };
  }

  // Rule 5 — Unclaimed session, Clerk user present → allow
  return { allowed: true, reason: "unclaimed-session" };
}

// ── Express middleware ─────────────────────────────────────────────────────

export async function verifySessionAccess(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  // Extract sessionId from query or body (routes use both)
  const sessionId =
    (req.query["sessionId"] as string | undefined) ??
    (req.body as Record<string, unknown>)?.["sessionId"] as string | undefined;

  // No sessionId → let the route's Zod validator handle it
  if (!sessionId) {
    next();
    return;
  }

  const auth = getAuth(req);
  const clerkUserId = auth?.userId ?? null;

  // Fast path: no auth token, skip DB lookup
  if (!clerkUserId) {
    next();
    return;
  }

  // Fast path: Clerk-native session
  if (sessionId === clerkUserId) {
    next();
    return;
  }

  // DB lookup — who owns this session?
  const [claim] = await db
    .select()
    .from(sessionClaimsTable)
    .where(eq(sessionClaimsTable.sessionId, sessionId))
    .limit(1);

  const decision = getSessionAccessDecision(
    clerkUserId,
    sessionId,
    claim ?? null
  );

  if (decision.allowed) {
    next();
  } else {
    res.status(403).json({
      error: "This session belongs to a different account.",
      code: decision.code,
    });
  }
}
