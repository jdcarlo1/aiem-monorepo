import { Router } from "express";
import { db, sessionsTable, answersTable, questionsTable } from "@workspace/db";
import { eq, sql } from "drizzle-orm";
import { getUncachableStripeClient } from "../stripeClient";
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

function checkAnswer(
  questionType: string,
  correctLetter: string,
  selectedLetter: string
): boolean {
  if (questionType === "multiple") {
    const correct = correctLetter
      .split(",")
      .map((s) => s.trim())
      .sort()
      .join(",");
    const selected = selectedLetter
      .split(",")
      .map((s) => s.trim())
      .sort()
      .join(",");
    return correct === selected;
  }
  // 'single' and 'ordered' — direct comparison
  return correctLetter.trim() === selectedLetter.trim();
}

// ── GET /session/status ──────────────────────────────────────────────────────
router.get("/session/status", async (req, res) => {
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
// Race-condition fix: the session row is locked with SELECT … FOR UPDATE inside
// a DB transaction so two concurrent requests cannot both pass the free-limit
// check at questionsAnswered = 9.
router.post("/session/answer", async (req, res) => {
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

    await tx.insert(answersTable).values({
      sessionId,
      questionId,
      selectedLetter,
      correct,
    });

    const newCount = session.questions_answered + 1;
    await tx
      .update(sessionsTable)
      .set({ questionsAnswered: newCount })
      .where(eq(sessionsTable.sessionId, sessionId));

    return { limited: false, newCount, isSubscribed: session.is_subscribed } as const;
  });

  if (result.limited) {
    res.status(403).json({
      error: "Free question limit reached. Subscription required.",
    });
    return;
  }

  const canAnswerMore = result.isSubscribed || result.newCount < FREE_LIMIT;

  res.json({
    correct,
    correctLetter: question.correctLetter,
    explanation: question.explanation,
    questionsAnswered: result.newCount,
    canAnswerMore,
  });
});

// ── POST /subscription/checkout (legacy stub) ────────────────────────────────
router.post("/subscription/checkout", async (req, res) => {
  const { sessionId, email } = req.body as { sessionId: string; email?: string };

  if (!sessionId) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }

  await getOrCreateSession(sessionId);

  if (email) {
    await db
      .update(sessionsTable)
      .set({ email })
      .where(eq(sessionsTable.sessionId, sessionId));
  }

  // NOTE: This stub is superseded by POST /api/stripe/checkout.
  // The real checkout URL is returned by the Stripe route.
  res.json({
    message:
      "Use POST /api/stripe/checkout with { sessionId, plan: 'monthly' | 'lifetime' } to start the Stripe-hosted checkout.",
    sessionId,
    checkoutUrl: null,
  });
});

// ── POST /subscription/cancel ────────────────────────────────────────────────
// Cancels the Stripe subscription *and* marks the DB row as inactive.
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

  // Cancel in Stripe first so the webhook confirms revocation
  const stripe = await getUncachableStripeClient();
  await stripe.subscriptions.cancel(session.stripeSubscriptionId);

  // Update DB row (webhook will also fire customer.subscription.deleted,
  // which is idempotent against this update)
  await db
    .update(sessionsTable)
    .set({ isSubscribed: false, subscriptionEndDate: null, stripeSubscriptionId: null })
    .where(eq(sessionsTable.sessionId, sessionId));

  res.json({ success: true, message: "Subscription cancelled successfully." });
});

export default router;
