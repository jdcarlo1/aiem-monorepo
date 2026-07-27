import { Router } from "express";
import { db, questionsTable, sessionsTable, answersTable } from "@workspace/db";
import { asc, eq, and } from "drizzle-orm";
import { GetQuestionParams } from "@workspace/api-zod";

const router = Router();

// ── GET /questions ───────────────────────────────────────────────────────────
router.get("/questions", async (req, res) => {
  const category = req.query.category as string | undefined;

  const query = db
    .select({
      id: questionsTable.id,
      questionNumber: questionsTable.questionNumber,
      category: questionsTable.category,
    })
    .from(questionsTable)
    .orderBy(asc(questionsTable.questionNumber));

  const questions = category
    ? await query.where(eq(questionsTable.category, category))
    : await query;

  res.json(questions);
});

// ── GET /questions/:id ───────────────────────────────────────────────────────
// correctLetter and explanation are gated:
//   • Returned if the session is subscribed
//   • Returned if the question was already answered by this session
//   • Omitted (null) otherwise — free-tier callers cannot harvest answers
//     just by fetching question details
//
// IMPORTANT frontend notes:
//   quiz.tsx      — uses /session/answer response for correctLetter+explanation ✅ safe
//   study-quiz.tsx — reads correctLetter from question fetch directly; relies on
//                    session being subscribed (study mode is subscription-only) ✅ safe
//   interview-prep.tsx — reads correctLetter from question fetch; no subscription
//                        gate enforced on that page; non-subscribed users will see
//                        null for correctLetter/explanation until they subscribe
//                        (acceptable: interview prep is a lead-gen feature, not
//                         the core exam flow)
router.get("/questions/:id", async (req, res) => {
  const parsed = GetQuestionParams.safeParse(req.params);
  if (!parsed.success) {
    res.status(400).json({ error: "Invalid question id" });
    return;
  }
  const { id } = parsed.data;

  const sessionId = req.query.sessionId as string | undefined;

  const [question] = await db
    .select({
      id: questionsTable.id,
      questionNumber: questionsTable.questionNumber,
      category: questionsTable.category,
      text: questionsTable.text,
      options: questionsTable.options,
      correctLetter: questionsTable.correctLetter,
      explanation: questionsTable.explanation,
      questionType: questionsTable.questionType,
      imageUrl: questionsTable.imageUrl,
    })
    .from(questionsTable)
    .where(eq(questionsTable.id, id))
    .limit(1);

  if (!question) {
    res.status(404).json({ error: "Question not found" });
    return;
  }

  // Normalize options: old questions store as {A: "text"}, new as [{letter, text}]
  let options = question.options as unknown;
  if (options && !Array.isArray(options) && typeof options === "object") {
    options = Object.entries(options as Record<string, string>).map(
      ([letter, text]) => ({ letter, text })
    );
  }

  // Determine whether to expose the correct answer and explanation
  let revealAnswer = false;

  if (sessionId) {
    // Check subscription status
    const [session] = await db
      .select({ isSubscribed: sessionsTable.isSubscribed })
      .from(sessionsTable)
      .where(eq(sessionsTable.sessionId, sessionId))
      .limit(1);

    if (session?.isSubscribed) {
      revealAnswer = true;
    } else {
      // Check if already answered by this session
      const [answered] = await db
        .select({ id: answersTable.id })
        .from(answersTable)
        .where(
          and(
            eq(answersTable.sessionId, sessionId),
            eq(answersTable.questionId, id)
          )
        )
        .limit(1);

      if (answered) revealAnswer = true;
    }
  }

  res.json({
    ...question,
    options,
    correctLetter: revealAnswer ? question.correctLetter : null,
    explanation: revealAnswer ? question.explanation : null,
  });
});

export default router;
