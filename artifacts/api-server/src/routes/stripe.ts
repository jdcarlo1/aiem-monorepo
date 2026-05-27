import { Router } from "express";
import { db, sessionsTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import { getUncachableStripeClient } from "../stripeClient";

const router = Router();

router.post("/stripe/checkout", async (req, res) => {
  const { sessionId, plan } = req.body as { sessionId: string; plan: "monthly" | "lifetime" };

  if (!sessionId || !plan) {
    res.status(400).json({ error: "sessionId and plan are required" });
    return;
  }

  const stripe = await getUncachableStripeClient();

  const [dbSession] = await db
    .select()
    .from(sessionsTable)
    .where(eq(sessionsTable.sessionId, sessionId))
    .limit(1);

  let customerId = dbSession?.stripeCustomerId ?? undefined;

  if (!customerId) {
    const customer = await stripe.customers.create({
      metadata: { sessionId },
    });
    customerId = customer.id;

    if (dbSession) {
      await db
        .update(sessionsTable)
        .set({ stripeCustomerId: customerId })
        .where(eq(sessionsTable.sessionId, sessionId));
    } else {
      await db.insert(sessionsTable).values({
        sessionId,
        questionsAnswered: 0,
        isSubscribed: false,
        stripeCustomerId: customerId,
      });
    }
  }

  const baseUrl = `https://${process.env.REPLIT_DOMAINS?.split(',')[0]}`;

  if (plan === "monthly") {
    const products = await stripe.products.search({
      query: "name:'NCLEX AI Monthly' AND active:'true'",
    });

    if (products.data.length === 0) {
      res.status(500).json({ error: "Monthly plan product not found in Stripe. Please seed products first." });
      return;
    }

    const prices = await stripe.prices.list({ product: products.data[0].id, active: true, limit: 1 });
    if (prices.data.length === 0) {
      res.status(500).json({ error: "Monthly price not found." });
      return;
    }

    const checkoutSession = await stripe.checkout.sessions.create({
      customer: customerId,
      payment_method_types: ["card"],
      line_items: [{ price: prices.data[0].id, quantity: 1 }],
      mode: "subscription",
      success_url: `${baseUrl}/subscribe-success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${baseUrl}/upgrade`,
      metadata: { sessionId },
    });

    res.json({ url: checkoutSession.url });
  } else {
    const products = await stripe.products.search({
      query: "name:'NCLEX AI Lifetime' AND active:'true'",
    });

    if (products.data.length === 0) {
      res.status(500).json({ error: "Lifetime plan product not found in Stripe. Please seed products first." });
      return;
    }

    const prices = await stripe.prices.list({ product: products.data[0].id, active: true, limit: 1 });
    if (prices.data.length === 0) {
      res.status(500).json({ error: "Lifetime price not found." });
      return;
    }

    const checkoutSession = await stripe.checkout.sessions.create({
      customer: customerId,
      payment_method_types: ["card"],
      line_items: [{ price: prices.data[0].id, quantity: 1 }],
      mode: "payment",
      success_url: `${baseUrl}/subscribe-success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${baseUrl}/upgrade`,
      metadata: { sessionId },
    });

    res.json({ url: checkoutSession.url });
  }
});

router.post("/stripe/verify-checkout", async (req, res) => {
  const { sessionId, checkoutSessionId } = req.body as { sessionId: string; checkoutSessionId: string };

  if (!sessionId || !checkoutSessionId) {
    res.status(400).json({ error: "sessionId and checkoutSessionId are required" });
    return;
  }

  const stripe = await getUncachableStripeClient();

  const checkoutSession = await stripe.checkout.sessions.retrieve(checkoutSessionId);

  if (checkoutSession.payment_status === "paid" || checkoutSession.status === "complete") {
    const subscriptionId = typeof checkoutSession.subscription === "string"
      ? checkoutSession.subscription
      : checkoutSession.subscription?.id ?? null;

    const customerEmail = checkoutSession.customer_details?.email ?? null;

    await db
      .update(sessionsTable)
      .set({
        isSubscribed: true,
        stripeCustomerId: typeof checkoutSession.customer === "string" ? checkoutSession.customer : null,
        stripeSubscriptionId: subscriptionId,
      })
      .where(eq(sessionsTable.sessionId, sessionId));

    res.json({ success: true, isSubscribed: true, email: customerEmail });
  } else {
    res.json({ success: false, isSubscribed: false, status: checkoutSession.payment_status });
  }
});

router.post("/stripe/restore-access", async (req, res) => {
  const { sessionId, email } = req.body as { sessionId: string; email: string };

  if (!sessionId || !email) {
    res.status(400).json({ error: "sessionId and email are required" });
    return;
  }

  const stripe = await getUncachableStripeClient();

  const customers = await stripe.customers.list({ email: email.trim().toLowerCase(), limit: 10 });

  if (customers.data.length === 0) {
    res.json({ success: false, message: "No account found with that email." });
    return;
  }

  for (const customer of customers.data) {
    // Check for completed checkout sessions
    const checkouts = await stripe.checkout.sessions.list({ customer: customer.id, status: "complete", limit: 10 });
    if (checkouts.data.length > 0) {
      const cs = checkouts.data[0];
      const subscriptionId = typeof cs.subscription === "string" ? cs.subscription : null;

      await db
        .update(sessionsTable)
        .set({ isSubscribed: true, stripeCustomerId: customer.id, stripeSubscriptionId: subscriptionId })
        .where(eq(sessionsTable.sessionId, sessionId));

      // Also insert if not existing
      const [existing] = await db.select().from(sessionsTable).where(eq(sessionsTable.sessionId, sessionId)).limit(1);
      if (!existing) {
        await db.insert(sessionsTable).values({ sessionId, questionsAnswered: 0, isSubscribed: true, stripeCustomerId: customer.id, stripeSubscriptionId: subscriptionId });
      }

      res.json({ success: true, message: "Access restored!" });
      return;
    }
  }

  res.json({ success: false, message: "No completed payment found for that email." });
});

export default router;
