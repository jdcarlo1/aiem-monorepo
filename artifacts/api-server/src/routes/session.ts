import { Router } from "express";
import { db, sessionsTable, answersTable, questionsTable } from "@workspace/db";
import { eq } from "drizzle-orm";

const router = Router();

const FREE_LIMIT = 5;

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

router.get("/session/status", async (req, res) => {
  const sessionId = req.query.sessionId as string;
  if (!sessionId) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }

  const session = await getOrCreateSession(sessionId);
  const canAnswerMore = session.isSubscribed || session.questionsAnswered < FREE_LIMIT;

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

router.post("/session/answer", async (req, res) => {
  const { sessionId, questionId, selectedLetter } = req.body as {
    sessionId: string;
    questionId: number;
    selectedLetter: string;
  };

  if (!sessionId || !questionId || !selectedLetter) {
    res.status(400).json({ error: "sessionId, questionId, and selectedLetter are required" });
    return;
  }

  const session = await getOrCreateSession(sessionId);

  if (!session.isSubscribed && session.questionsAnswered >= FREE_LIMIT) {
    res.status(403).json({ error: "Free question limit reached. Subscription required." });
    return;
  }

  const [question] = await db
    .select()
    .from(questionsTable)
    .where(eq(questionsTable.id, questionId))
    .limit(1);

  if (!question) {
    res.status(404).json({ error: "Question not found" });
    return;
  }

  const correct = question.correctLetter === selectedLetter;

  await db.insert(answersTable).values({
    sessionId,
    questionId,
    selectedLetter,
    correct,
  });

  const newCount = session.questionsAnswered + 1;
  await db
    .update(sessionsTable)
    .set({ questionsAnswered: newCount })
    .where(eq(sessionsTable.sessionId, sessionId));

  const canAnswerMore = session.isSubscribed || newCount < FREE_LIMIT;

  res.json({
    correct,
    correctLetter: question.correctLetter,
    explanation: question.explanation,
    questionsAnswered: newCount,
    canAnswerMore,
  });
});

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

  res.json({
    message: "Stripe integration coming soon. Your subscription will be activated once payment is set up.",
    sessionId,
    checkoutUrl: null,
  });
});

router.post("/subscription/cancel", async (req, res) => {
  const { sessionId } = req.body as { sessionId: string };

  if (!sessionId) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }

  const [session] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  if (!session) {
    res.status(404).json({ error: "Session not found" });
    return;
  }

  await db
    .update(sessionsTable)
    .set({ isSubscribed: false, subscriptionEndDate: null })
    .where(eq(sessionsTable.sessionId, sessionId));

  res.json({ success: true, message: "Subscription cancelled successfully." });
});

export default router;
