import { pgTable, text, serial, integer, timestamp } from "drizzle-orm/pg-core";

export const affiliatesTable = pgTable("affiliates", {
  id: serial("id").primaryKey(),
  code: text("code").notNull().unique(),
  name: text("name").notNull(),
  stripeConnectId: text("stripe_connect_id"),
  commissionPct: integer("commission_pct").notNull().default(50),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export type Affiliate = typeof affiliatesTable.$inferSelect;
