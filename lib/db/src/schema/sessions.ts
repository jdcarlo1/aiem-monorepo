import { pgTable, text, serial, integer, boolean, timestamp } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const sessionsTable = pgTable("sessions", {
  id: serial("id").primaryKey(),
  sessionId: text("session_id").notNull().unique(),
  questionsAnswered: integer("questions_answered").notNull().default(0),
  isSubscribed: boolean("is_subscribed").notNull().default(false),
  subscriptionEndDate: timestamp("subscription_end_date", { withTimezone: true }),
  email: text("email"),
  stripeCustomerId: text("stripe_customer_id"),
  stripeSubscriptionId: text("stripe_subscription_id"),
  referralCode: text("referral_code"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow().$onUpdate(() => new Date()),
});

export const insertSessionSchema = createInsertSchema(sessionsTable).omit({ id: true, createdAt: true, updatedAt: true });
export type InsertSession = z.infer<typeof insertSessionSchema>;
export type Session = typeof sessionsTable.$inferSelect;

export const answersTable = pgTable("answers", {
  id: serial("id").primaryKey(),
  sessionId: text("session_id").notNull(),
  questionId: integer("question_id").notNull(),
  selectedLetter: text("selected_letter").notNull(),
  correct: boolean("correct").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const insertAnswerSchema = createInsertSchema(answersTable).omit({ id: true, createdAt: true });
export type InsertAnswer = z.infer<typeof insertAnswerSchema>;
export type Answer = typeof answersTable.$inferSelect;
