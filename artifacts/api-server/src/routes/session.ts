import { Router } from "express";
import { db, sessionsTable, answersTable, questionsTable, sessionClaimsTable } from "@workspace/db";
import { eq, sql } from "drizzle-orm";
import { getAuth } from "@clerk/express";
import { getUncachableStripeClient } from "../stripeClient";
import { checkAnswer } from "../lib/checkAnswer";
import { verifySessionAccess, ClaimSessionBody, getSessionAccessDecision } from "../lib/sessionAuth";
import {
  GetSessionStatusQueryParams,
  SubmitAnswerBody,
  CancelSubscriptionBody,
} from "@workspace/api-zod";

const router = Router();

const FREE_LIMIT = 10;

async function getOrCreateSession(sessionId: string) {
  const [existing] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  if (existing) return existing;

  const [created] = await db
    .insert(sessionsTable)
    .values({ sessionId, questionsAnswered: 0, isSubscribed: false })
    .returning();

  return created;
}

// ── GET /session/status ──────────────────────────────────────────────────────
router.get("/session/status", verifySessionAccess, async (req, res) => {
  const parsed = GetSessionStatusQueryParams.safeParse(req.query);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }
  const { sessionId } = parsed.data;

  const session = await getOrCreateSession(sessionId);
  const canAnswerMore =
    session.isSubscribed || session.questionsAnswered < FREE_LIMIT;

  res.json({
    sessionId: session.sessionId,
    questionsAnswered: session.questionsAnswered,
    freeLimit: FREE_LIMIT,
    isSubscribed: session.isSubscribed,
    canAnswerMore,
    subscriptionEndDate: session.subscriptionEndDate
      ? session.subscriptionEndDate.toISOString()
      : null,
  });
});

// ── POST /session/answer ─────────────────────────────────────────────────────
// verifySessionAccess gate + SELECT FOR UPDATE inside a transaction
// (two concurrent requests for the same session queue at the lock).
router.post("/session/answer", verifySessionAccess, async (req, res) => {
  const parsed = SubmitAnswerBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({
      error: "sessionId, questionId, and selectedLetter are required",
      details: parsed.error.flatten(),
    });
    return;
  }
  const { sessionId, questionId, selectedLetter } = parsed.data;

  // Fetch the question outside the transaction (read-only, no lock needed)
  const [question] = await db
    .select()
    .from(questionsTable)
    .where(eq(questionsTable.id, questionId))
    .limit(1);

  if (!question) {
    res.status(404).json({ error: "Question not found" });
    return;
  }

  const questionType = question.questionType ?? "single";
  const correct = checkAnswer(questionType, question.correctLetter, selectedLetter);

  // Atomically enforce the free limit and record the answer
  const result = await db.transaction(async (tx) => {
    // Lock the session row so concurrent requests queue here
    const locked = await tx.execute(
      sql`SELECT id, session_id, questions_answered, is_subscribed
          FROM sessions
          WHERE session_id = ${sessionId}
          FOR UPDATE`
    );

    const rows = locked.rows as Array<{
      id: number;
      session_id: string;
      questions_answered: number;
      is_subscribed: boolean;
    }>;

    let session = rows[0];

    // First visit — create the session row inside the transaction
    if (!session) {
      const inserted = await tx
        .insert(sessionsTable)
        .values({ sessionId, questionsAnswered: 0, isSubscribed: false })
        .returning();
      session = {
        id: inserted[0].id,
        session_id: inserted[0].sessionId,
        questions_answered: inserted[0].questionsAnswered,
        is_subscribed: inserted[0].isSubscribed,
      };
    }

    if (!session.is_subscribed && session.questions_answered >= FREE_LIMIT) {
      return { limited: true } as const;
    }

    // Record the answer
    await tx.insert(answersTable).values({
      sessionId,
      questionId,
      selectedLetter,
      correct,
    });

    // Increment the counter
    const newCount = session.questions_answered + 1;
    await tx
      .update(sessionsTable)
      .set({ questionsAnswered: newCount })
      .where(eq(sessionsTable.sessionId, sessionId));

    return { limited: false, newCount, isSubscribed: session.is_subscribed } as const;
  });

  if (result.limited) {
    res.status(403).json({
      error: "Free limit reached",
      freeLimit: FREE_LIMIT,
      checkoutUrl: null,
    });
    return;
  }

  res.json({
    correct,
    correctLetter: question.correctLetter,
    explanation: question.explanation,
    questionsAnswered: result.newCount,
    isSubscribed: result.isSubscribed,
    sessionId,
    checkoutUrl: null,
  });
});

// ── POST /session/claim ──────────────────────────────────────────────────────
// Links one anonymous sessionId to one Clerk account (one-time, permanent).
// Option B migration gate — the explicit handshake before the gate enforces
// ownership on subsequent requests.
router.post("/session/claim", async (req, res) => {
  const auth = getAuth(req);
  if (!auth?.userId) {
    res.status(401).json({
      error: "Authentication required to claim a session. Sign in first.",
      code: "UNAUTHENTICATED",
    });
    return;
  }
  const clerkUserId = auth.userId;

  const parsed = ClaimSessionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId is required", details: parsed.error.flatten() });
    return;
  }
  const { sessionId } = parsed.data;

  // Verify the session exists (don't let users claim phantom IDs)
  const [session] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  if (!session) {
    res.status(404).json({
      error: "Session not found. Answer at least one question before claiming.",
      code: "SESSION_NOT_FOUND",
    });
    return;
  }

  // Check if sessionId is already claimed
  const [existingBySession] = await db
    .select()
    .from(sessionClaimsTable)
    .where(eq(sessionClaimsTable.sessionId, sessionId))
    .limit(1);

  if (existingBySession) {
    if (existingBySession.clerkUserId === clerkUserId) {
      // Idempotent — same user re-claiming their own session
      res.json({
        success: true,
        clerkUserId,
        sessionId,
        message: "Session already linked to your account.",
        idempotent: true,
      });
      return;
    }
    // Different user already owns this session
    res.status(409).json({
      error: "This session has already been claimed by a different account.",
      code: "SESSION_ALREADY_CLAIMED",
    });
    return;
  }

  // Check if this Clerk user already has a different session claimed
  const [existingByUser] = await db
    .select()
    .from(sessionClaimsTable)
    .where(eq(sessionClaimsTable.clerkUserId, clerkUserId))
    .limit(1);

  if (existingByUser) {
    // One claim per Clerk user — reject to avoid silent progress loss
    res.status(409).json({
      error:
        "Your account is already linked to a different session. " +
        "Contact support if you need to transfer progress.",
      code: "USER_ALREADY_HAS_CLAIM",
      claimedSessionId: existingByUser.sessionId,
    });
    return;
  }

  // All checks pass — insert the claim
  await db.insert(sessionClaimsTable).values({ clerkUserId, sessionId });

  res.status(201).json({
    success: true,
    clerkUserId,
    sessionId,
    message: "Session linked to your account. Your progress is now tied to your sign-in.",
  });
});

// ── POST /subscription/cancel ────────────────────────────────────────────────
router.post("/subscription/cancel", async (req, res) => {
  const parsed = CancelSubscriptionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }
  const { sessionId } = parsed.data;

  const [session] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  if (!session) {
    res.status(404).json({ error: "Session not found" });
    return;
  }

  if (!session.stripeSubscriptionId) {
    res.status(400).json({
      error:
        "No Stripe subscription on record for this session. " +
        "Lifetime access cannot be cancelled here — contact support.",
    });
    return;
  }

  const stripe = await getUncachableStripeClient();
  await stripe.subscriptions.cancel(session.stripeSubscriptionId);

  await db
    .update(sessionsTable)
    .set({ isSubscribed: false, subscriptionEndDate: null, stripeSubscriptionId: null })
    .where(eq(sessionsTable.sessionId, sessionId));

  res.json({ success: true, message: "Subscription cancelled successfully." });
});

export default router;
