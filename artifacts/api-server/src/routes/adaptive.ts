import { Router } from "express";
import { db, answersTable, questionsTable } from "@workspace/db";
import { eq, inArray, notInArray } from "drizzle-orm";

const router = Router();

export interface CategoryStat {
  category: string;
  total: number;
  correct: number;
  accuracy: number;
}

async function computeAdaptiveNext(sessionId: string): Promise<{
  questionId: number | null;
  categoryPerformance: CategoryStat[];
  totalAnswered: number;
}> {
  // 1. Get all answered rows for this session
  const answeredRows = await db
    .select({ questionId: answersTable.questionId, correct: answersTable.correct })
    .from(answersTable)
    .where(eq(answersTable.sessionId, sessionId));

  const answeredIds = answeredRows.map((r) => r.questionId);
  const totalAnswered = answeredIds.length;

  // 2. Build per-category accuracy from answered questions
  const categoryMap: Record<string, { correct: number; total: number }> = {};

  if (answeredIds.length > 0) {
    const answeredQuestions = await db
      .select({ id: questionsTable.id, category: questionsTable.category })
      .from(questionsTable)
      .where(inArray(questionsTable.id, answeredIds));

    const qCategoryMap = new Map(answeredQuestions.map((q) => [q.id, q.category]));

    for (const row of answeredRows) {
      const cat = qCategoryMap.get(row.questionId) ?? "Unknown";
      if (!categoryMap[cat]) categoryMap[cat] = { correct: 0, total: 0 };
      categoryMap[cat].total++;
      if (row.correct) categoryMap[cat].correct++;
    }
  }

  // 3. Get all unanswered questions (include questionType for difficulty bias)
  const allUnanswered =
    answeredIds.length > 0
      ? await db
          .select({ id: questionsTable.id, category: questionsTable.category, questionType: questionsTable.questionType })
          .from(questionsTable)
          .where(notInArray(questionsTable.id, answeredIds))
      : await db
          .select({ id: questionsTable.id, category: questionsTable.category, questionType: questionsTable.questionType })
          .from(questionsTable);

  // Onboarding ramp: start simple, introduce harder formats gradually
  const EASY_TYPES = ["single"];
  const HARD_TYPES = ["multiple", "ordered"];
  const easyUnanswered = allUnanswered.filter(q => EASY_TYPES.includes(q.questionType ?? "single"));
  const hardUnanswered = allUnanswered.filter(q => HARD_TYPES.includes(q.questionType ?? ""));

  let unanswered: typeof allUnanswered;
  if (totalAnswered === 0 && easyUnanswered.length >= 1) {
    // First question: always a simple single-choice to ease them in
    unanswered = easyUnanswered;
  } else if (totalAnswered < 3 && easyUnanswered.length >= 1) {
    // Questions 2-3: still mostly easy
    unanswered = easyUnanswered;
  } else {
    // Question 4+: full mix of all types
    unanswered = allUnanswered;
  }

  const categoryPerformance: CategoryStat[] = Object.entries(categoryMap)
    .map(([category, s]) => ({
      category,
      total: s.total,
      correct: s.correct,
      accuracy: s.total > 0 ? s.correct / s.total : 0.5,
    }))
    .sort((a, b) => a.accuracy - b.accuracy);

  if (unanswered.length === 0) {
    return { questionId: null, categoryPerformance, totalAnswered };
  }

  // 4. Group unanswered by category
  const unansweredByCategory: Record<string, number[]> = {};
  for (const q of unanswered) {
    if (!unansweredByCategory[q.category]) unansweredByCategory[q.category] = [];
    unansweredByCategory[q.category].push(q.id);
  }

  // 5. Weighted random selection — weaker categories get higher probability
  //    weight = (1 - accuracy)^2 + 0.05 floor so mastered topics still appear occasionally
  const categories = Object.keys(unansweredByCategory);
  const weights = categories.map((cat) => {
    const stats = categoryMap[cat];
    if (!stats) return 0.55; // No history: slightly above neutral
    const acc = stats.correct / stats.total;
    return Math.pow(1 - acc, 2) + 0.05;
  });

  const totalWeight = weights.reduce((a, b) => a + b, 0);
  let rand = Math.random() * totalWeight;
  let selectedCategory = categories[0];
  for (let i = 0; i < categories.length; i++) {
    rand -= weights[i];
    if (rand <= 0) {
      selectedCategory = categories[i];
      break;
    }
  }

  // 6. Pick a random unanswered question from the selected category
  const pool = unansweredByCategory[selectedCategory];
  const questionId = pool[Math.floor(Math.random() * pool.length)];

  return { questionId, categoryPerformance, totalAnswered };
}

router.get("/adaptive/next", async (req, res) => {
  const sessionId = req.query.sessionId as string;
  if (!sessionId) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }
  try {
    const result = await computeAdaptiveNext(sessionId);
    res.json(result);
  } catch (err) {
    console.error("adaptive/next error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

router.get("/adaptive/performance", async (req, res) => {
  const sessionId = req.query.sessionId as string;
  if (!sessionId) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }
  try {
    const { categoryPerformance, totalAnswered } = await computeAdaptiveNext(sessionId);
    res.json({ categoryPerformance, totalAnswered });
  } catch (err) {
    console.error("adaptive/performance error:", err);
    res.status(500).json({ error: "Internal server error" });
  }
});

export default router;
