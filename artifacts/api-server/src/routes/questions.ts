import { Router } from "express";
import { db, questionsTable } from "@workspace/db";
import { asc } from "drizzle-orm";

const router = Router();

router.get("/questions", async (_req, res) => {
  const questions = await db
    .select({
      id: questionsTable.id,
      questionNumber: questionsTable.questionNumber,
      category: questionsTable.category,
    })
    .from(questionsTable)
    .orderBy(asc(questionsTable.questionNumber));

  res.json(questions);
});

router.get("/questions/:id", async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) {
    res.status(400).json({ error: "Invalid question id" });
    return;
  }

  const [question] = await db
    .select({
      id: questionsTable.id,
      questionNumber: questionsTable.questionNumber,
      category: questionsTable.category,
      text: questionsTable.text,
      options: questionsTable.options,
    })
    .from(questionsTable)
    .where(eq(questionsTable.id, id))
    .limit(1);

  if (!question) {
    res.status(404).json({ error: "Question not found" });
    return;
  }

  res.json(question);
});

import { eq } from "drizzle-orm";

export default router;
