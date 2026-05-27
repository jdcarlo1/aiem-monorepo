import { pgTable, text, serial, integer, timestamp, jsonb } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const questionsTable = pgTable("questions", {
  id: serial("id").primaryKey(),
  questionNumber: integer("question_number").notNull(),
  category: text("category").notNull(),
  text: text("text").notNull(),
  options: jsonb("options").notNull().$type<{ letter: string; text: string }[]>(),
  correctLetter: text("correct_letter").notNull(),
  explanation: text("explanation").notNull(),
  questionType: text("question_type").notNull().default("single"),
  imageUrl: text("image_url"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const insertQuestionSchema = createInsertSchema(questionsTable).omit({ id: true, createdAt: true });
export type InsertQuestion = z.infer<typeof insertQuestionSchema>;
export type Question = typeof questionsTable.$inferSelect;
