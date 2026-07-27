import { Router } from "express";
import { db, sessionsTable, questionsTable, affiliatesTable } from "@workspace/db";
import { eq, and, isNull } from "drizzle-orm";
import { getUncachableStripeClient } from "../stripeClient";
import { requireAdmin } from "../lib/adminAuth";
import { z } from "zod";

const router = Router();

// ── Inline Zod schemas (no generated api-zod schemas for these routes) ────────
const CheckoutBody = z.object({
  sessionId: z.string().min(1),
  plan: z.enum(["monthly", "lifetime"]),
  referralCode: z.string().optional(),
});

const VerifyCheckoutBody = z.object({
  sessionId: z.string().min(1),
  checkoutSessionId: z.string().min(1),
});

const RestoreAccessBody = z.object({
  sessionId: z.string().min(1),
  email: z.string().email(),
});

const ActivateSessionBody = z.object({
  sessionId: z.string().min(1),
});

const SeedQuestionsBody = z.object({
  questions: z.array(z.object({
    questionNumber: z.number().int(),
    category: z.string().min(1),
    text: z.string().min(1),
    options: z.union([z.record(z.string()), z.array(z.object({ letter: z.string(), text: z.string() }))]),
    correctLetter: z.string().min(1),
    explanation: z.string().min(1),
    questionType: z.string(),
    imageUrl: z.string().nullable().optional(),
  })).min(1),
});

// ── POST /stripe/checkout ────────────────────────────────────────────────────
router.post("/stripe/checkout", async (req, res) => {
  const parsed = CheckoutBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId and plan are required", details: parsed.error.flatten() });
    return;
  }
  const { sessionId, plan, referralCode } = parsed.data;

  const stripe = await getUncachableStripeClient();

  const [dbSession] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  let customerId = dbSession?.stripeCustomerId ?? undefined;

  if (!customerId) {
    const customer = await stripe.customers.create({ metadata: { sessionId } });
    customerId = customer.id;

    if (dbSession) {
      await db.update(sessionsTable).set({ stripeCustomerId: customerId }).where(eq(sessionsTable.sessionId, sessionId));
    } else {
      await db.insert(sessionsTable).values({ sessionId, questionsAnswered: 0, isSubscribed: false, stripeCustomerId: customerId });
    }
  }

  let validatedCode: string | null = null;
  if (referralCode) {
    const upper = referralCode.trim().toUpperCase();
    const [affiliate] = await db.select().from(affiliatesTable).where(eq(affiliatesTable.code, upper)).limit(1);
    if (affiliate) {
      validatedCode = upper;
      await db.update(sessionsTable).set({ referralCode: validatedCode }).where(eq(sessionsTable.sessionId, sessionId));
    }
  }

  const baseUrl = process.env.SITE_URL ?? "https://nclexai.org";
  const meta = { sessionId, ...(validatedCode ? { referralCode: validatedCode } : {}) };

  if (plan === "monthly") {
    const products = await stripe.products.search({ query: "name:'NCLEX AI Monthly' AND active:'true'" });
    if (products.data.length === 0) { res.status(500).json({ error: "Monthly plan product not found in Stripe." }); return; }
    const prices = await stripe.prices.list({ product: products.data[0].id, active: true, limit: 1 });
    if (prices.data.length === 0) { res.status(500).json({ error: "Monthly price not found." }); return; }

    const checkoutSession = await stripe.checkout.sessions.create({
      customer: customerId,
      payment_method_types: ["card"],
      line_items: [{ price: prices.data[0].id, quantity: 1 }],
      mode: "subscription",
      allow_promotion_codes: true,
      success_url: `${baseUrl}/subscribe-success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${baseUrl}/paywall`,
      metadata: meta,
    });
    res.json({ url: checkoutSession.url });
  } else {
    const products = await stripe.products.search({ query: "name:'NCLEX AI Lifetime' AND active:'true'" });
    if (products.data.length === 0) { res.status(500).json({ error: "Lifetime plan product not found in Stripe." }); return; }
    const prices = await stripe.prices.list({ product: products.data[0].id, active: true, limit: 1 });
    if (prices.data.length === 0) { res.status(500).json({ error: "Lifetime price not found." }); return; }

    const checkoutSession = await stripe.checkout.sessions.create({
      customer: customerId,
      payment_method_types: ["card"],
      line_items: [{ price: prices.data[0].id, quantity: 1 }],
      mode: "payment",
      allow_promotion_codes: true,
      success_url: `${baseUrl}/subscribe-success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${baseUrl}/paywall`,
      metadata: meta,
    });
    res.json({ url: checkoutSession.url });
  }
});

// ── POST /stripe/verify-checkout ─────────────────────────────────────────────
router.post("/stripe/verify-checkout", async (req, res) => {
  const parsed = VerifyCheckoutBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId and checkoutSessionId are required" });
    return;
  }
  const { sessionId, checkoutSessionId } = parsed.data;

  const stripe = await getUncachableStripeClient();
  const checkoutSession = await stripe.checkout.sessions.retrieve(checkoutSessionId);

  if (checkoutSession.payment_status === "paid" || checkoutSession.status === "complete") {
    const subscriptionId = typeof checkoutSession.subscription === "string"
      ? checkoutSession.subscription
      : checkoutSession.subscription?.id ?? null;
    const customerId = typeof checkoutSession.customer === "string" ? checkoutSession.customer : null;
    const customerEmail = checkoutSession.customer_details?.email ?? null;

    if (customerId && customerEmail) {
      try { await stripe.customers.update(customerId, { email: customerEmail }); } catch (_) {}
    }

    await db.update(sessionsTable)
      .set({ isSubscribed: true, stripeCustomerId: customerId, stripeSubscriptionId: subscriptionId })
      .where(eq(sessionsTable.sessionId, sessionId));

    res.json({ success: true, isSubscribed: true, email: customerEmail });
  } else {
    res.json({ success: false, isSubscribed: false, status: checkoutSession.payment_status });
  }
});

// ── POST /stripe/restore-access ───────────────────────────────────────────────
router.post("/stripe/restore-access", async (req, res) => {
  const parsed = RestoreAccessBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "A valid sessionId and email are required" });
    return;
  }
  const { sessionId, email } = parsed.data;

  const stripe = await getUncachableStripeClient();
  const normalizedEmail = email.trim().toLowerCase();

  async function activateSession(customerId: string, subscriptionId: string | null) {
    const [existing] = await db.select().from(sessionsTable).where(eq(sessionsTable.sessionId, sessionId)).limit(1);
    if (existing) {
      await db.update(sessionsTable)
        .set({ isSubscribed: true, stripeCustomerId: customerId, stripeSubscriptionId: subscriptionId })
        .where(eq(sessionsTable.sessionId, sessionId));
    } else {
      await db.insert(sessionsTable).values({ sessionId, questionsAnswered: 0, isSubscribed: true, stripeCustomerId: customerId, stripeSubscriptionId: subscriptionId });
    }
  }

  try {
    const searchResults = await stripe.checkout.sessions.search({
      query: `customer_details.email:"${normalizedEmail}" AND status:"complete"`,
      limit: 5,
    });

    if (searchResults.data.length > 0) {
      const cs = searchResults.data[0];
      const customerId = typeof cs.customer === "string" ? cs.customer : "";
      const subscriptionId = typeof cs.subscription === "string" ? cs.subscription : null;
      if (customerId) {
        try { await stripe.customers.update(customerId, { email: normalizedEmail }); } catch (_) {}
      }
      await activateSession(customerId, subscriptionId);
      res.json({ success: true, message: "Access restored!" });
      return;
    }
  } catch (_) {}

  const customers = await stripe.customers.list({ email: normalizedEmail, limit: 10 });
  for (const customer of customers.data) {
    const checkouts = await stripe.checkout.sessions.list({ customer: customer.id, status: "complete", limit: 5 });
    if (checkouts.data.length > 0) {
      const cs = checkouts.data[0];
      const subscriptionId = typeof cs.subscription === "string" ? cs.subscription : null;
      await activateSession(customer.id, subscriptionId);
      res.json({ success: true, message: "Access restored!" });
      return;
    }
  }

  res.json({ success: false, message: "No completed payment found for that email. Please check the email you used when you paid." });
});

// ── POST /admin/seed-questions ────────────────────────────────────────────────
router.post("/admin/seed-questions", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const parsed = SeedQuestionsBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "questions array required", details: parsed.error.flatten() });
    return;
  }
  const { questions } = parsed.data;

  const batchSize = 50;
  let inserted = 0;
  for (let i = 0; i < questions.length; i += batchSize) {
    const batch = questions.slice(i, i + batchSize);
    await db.insert(questionsTable).values(batch as any).onConflictDoNothing();
    inserted += batch.length;
  }

  res.json({ success: true, message: `Inserted ${inserted} questions` });
});

// ── POST /admin/fix-sessions ──────────────────────────────────────────────────
router.post("/admin/fix-sessions", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const result = await db
    .update(sessionsTable)
    .set({ isSubscribed: false })
    .where(and(eq(sessionsTable.isSubscribed, true), isNull(sessionsTable.stripeCustomerId)))
    .returning({ id: sessionsTable.id });

  res.json({ success: true, fixed: result.length });
});

// ── StockScanner AI checkout ──────────────────────────────────────────────────

router.post("/stock-scanner/checkout", async (req, res) => {
  const { email, referralCode } = req.body as { email?: string; referralCode?: string };
  if (!email || !email.includes("@")) {
    res.status(400).json({ error: "Valid email is required" });
    return;
  }

  const stripe = await getUncachableStripeClient();
  const allProducts = await stripe.products.list({ active: true, limit: 100 });
  const products = { data: allProducts.data.filter(p => p.name === "StockScanner AI Pro") };

  if (products.data.length === 0) {
    res.status(500).json({ error: "StockScanner AI Pro product not found." });
    return;
  }

  const prices = await stripe.prices.list({ product: products.data[0].id, active: true, limit: 1 });
  if (prices.data.length === 0) { res.status(500).json({ error: "Subscription price not found." }); return; }

  const existingCustomers = await stripe.customers.list({ email: email.trim().toLowerCase(), limit: 1 });
  let customerId: string;
  if (existingCustomers.data.length > 0) {
    customerId = existingCustomers.data[0].id;
  } else {
    const customer = await stripe.customers.create({ email: email.trim().toLowerCase() });
    customerId = customer.id;
  }

  const domains = process.env.REPLIT_DOMAINS?.split(",") ?? [];
  const host = domains[0] ?? "localhost";
  const baseUrl = `https://${host}/stock-scanner`;

  let validatedCode: string | null = null;
  if (referralCode) {
    const upper = referralCode.trim().toUpperCase();
    const [affiliate] = await db.select().from(affiliatesTable).where(eq(affiliatesTable.code, upper)).limit(1);
    if (affiliate) validatedCode = upper;
  }

  const session = await stripe.checkout.sessions.create({
    customer: customerId,
    payment_method_types: ["card"],
    line_items: [{ price: prices.data[0].id, quantity: 1 }],
    mode: "subscription",
    success_url: `${baseUrl}?subscribed=true`,
    cancel_url: `${baseUrl}`,
    metadata: { product: "stock-scanner", ...(validatedCode ? { referralCode: validatedCode } : {}) },
  });

  res.json({ url: session.url, referralCode: validatedCode });
});

router.post("/stock-scanner/manage", async (req, res) => {
  const { email } = req.body as { email?: string };
  if (!email) { res.status(400).json({ error: "Email required" }); return; }

  const stripe = await getUncachableStripeClient();
  const customers = await stripe.customers.list({ email: email.trim().toLowerCase(), limit: 1 });

  if (customers.data.length === 0) {
    res.status(404).json({ error: "No subscription found for that email." });
    return;
  }

  const domains = process.env.REPLIT_DOMAINS?.split(",") ?? [];
  const host = domains[0] ?? "localhost";

  const portal = await stripe.billingPortal.sessions.create({
    customer: customers.data[0].id,
    return_url: `https://${host}/stock-scanner`,
  });

  res.json({ url: portal.url });
});

// ── POST /admin/activate-sessions ─────────────────────────────────────────────
router.post("/admin/activate-sessions", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const parsed = ActivateSessionBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: "sessionId is required" });
    return;
  }
  const { sessionId } = parsed.data;

  await db.update(sessionsTable).set({ isSubscribed: true }).where(eq(sessionsTable.sessionId, sessionId));
  res.json({ success: true, message: `Activated session ${sessionId}` });
});

export default router;
