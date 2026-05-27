import { Router } from "express";
import { db, questionsTable } from "@workspace/db";
import { asc, eq } from "drizzle-orm";

const router = Router();

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
      correctLetter: questionsTable.correctLetter,
      explanation: questionsTable.explanation,
      questionType: questionsTable.questionType,
    })
    .from(questionsTable)
    .where(eq(questionsTable.id, id))
    .limit(1);

  if (!question) {
    res.status(404).json({ error: "Question not found" });
    return;
  }

  // Normalize options: old questions store as {A: "text", B: "text"},
  // new questions store as [{letter: "A", text: "..."}]. Always return array.
  let options = question.options as unknown;
  if (options && !Array.isArray(options) && typeof options === "object") {
    options = Object.entries(options as Record<string, string>).map(
      ([letter, text]) => ({ letter, text })
    );
  }

  res.json({ ...question, options });
});

export default router;
