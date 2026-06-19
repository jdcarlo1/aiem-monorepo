import { db, sessionsTable, affiliatesTable } from "@workspace/db";
import { eq, sql } from "drizzle-orm";
import { getStripeSync, getUncachableStripeClient } from './stripeClient';
import { logger } from './lib/logger';
import crypto from 'crypto';

async function sendAffiliateTransfer(
  affiliateCode: string,
  amountCents: number,
  description: string
): Promise<void> {
  if (!affiliateCode || amountCents <= 0) return;

  const [affiliate] = await db
    .select()
    .from(affiliatesTable)
    .where(eq(affiliatesTable.code, affiliateCode))
    .limit(1);

  if (!affiliate?.stripeConnectId) {
    logger.warn({ affiliateCode }, 'Affiliate has no Stripe Connect account — skipping transfer');
    return;
  }

  const commissionCents = Math.floor(amountCents * (affiliate.commissionPct / 100));
  if (commissionCents <= 0) return;

  const stripe = await getUncachableStripeClient();

  try {
    const account = await stripe.accounts.retrieve(affiliate.stripeConnectId);
    if (!account.payouts_enabled) {
      logger.warn({ affiliateCode, stripeConnectId: affiliate.stripeConnectId },
        'Affiliate Stripe account not yet active — skipping transfer (they need to complete onboarding)');
      return;
    }
  } catch (err) {
    logger.error({ err, affiliateCode }, 'Could not retrieve affiliate Stripe account');
    return;
  }

  await stripe.transfers.create({
    amount: commissionCents,
    currency: 'usd',
    destination: affiliate.stripeConnectId,
    description,
  });

  logger.info({ affiliateCode, commissionCents, description }, 'Affiliate transfer sent');
}

export class WebhookHandlers {
  static async processWebhook(payload: Buffer, signature: string): Promise<void> {
    if (!Buffer.isBuffer(payload)) {
      throw new Error(
        'STRIPE WEBHOOK ERROR: Payload must be a Buffer. ' +
        'FIX: Ensure webhook route is registered BEFORE app.use(express.json()).'
      );
    }

    let event: any;
    try {
      event = JSON.parse(payload.toString());
    } catch {
      return;
    }

    // Attempt stripe-replit-sync — non-critical, never block session unlock
    try {
      const sync = await getStripeSync();
      await sync.processWebhook(payload, signature);
    } catch (err) {
      logger.warn({ err }, 'stripe-replit-sync processWebhook failed (non-critical) — continuing with session handling');
    }

    // ── checkout.session.completed ─────────────────────────────────────────
    if (event.type === 'checkout.session.completed') {
      const session = event.data?.object ?? {};
      const sessionId = session.metadata?.sessionId;
      const product = session.metadata?.product;
      const referralCode = session.metadata?.referralCode ?? null;
      const customerId = session.customer ?? null;
      const subscriptionId = session.subscription ?? null;
      const mode = session.mode; // "payment" = lifetime, "subscription" = monthly

      // NCLEX session unlock
      if (sessionId) {
        await db
          .update(sessionsTable)
          .set({
            isSubscribed: true,
            stripeCustomerId: customerId,
            stripeSubscriptionId: subscriptionId,
            ...(referralCode ? { referralCode } : {}),
          })
          .where(eq(sessionsTable.sessionId, sessionId));
        logger.info({ sessionId, customerId, referralCode }, 'Session unlocked after successful checkout');
      }

      // Affiliate payout — ONLY for lifetime (one-time payment).
      // Monthly subscription payouts are handled in invoice.payment_succeeded.
      if (referralCode && mode === 'payment') {
        const amountCents = session.amount_total ?? 0;
        await sendAffiliateTransfer(
          referralCode,
          amountCents,
          `NCLEX AI lifetime referral — code ${referralCode}`
        );
      }

      // StockScanner AI subscription activation
      if (product === 'stock-scanner') {
        const email = session.customer_details?.email ?? session.customer_email ?? null;
        if (email) {
          const token = crypto.randomUUID().replace(/-/g, '');
          await db.execute(sql`
            INSERT INTO sm_subscribers (email, token, active, stripe_customer_id, stripe_subscription_id, paid)
            VALUES (${email}, ${token}, true, ${customerId}, ${subscriptionId}, true)
            ON CONFLICT (email) DO UPDATE SET
              active = true,
              stripe_customer_id = COALESCE(${customerId}, sm_subscribers.stripe_customer_id),
              stripe_subscription_id = COALESCE(${subscriptionId}, sm_subscribers.stripe_subscription_id),
              paid = true
          `);
          logger.info({ email, customerId }, 'StockScanner subscriber activated after checkout');
        }
      }
    }

    // ── invoice.payment_succeeded ──────────────────────────────────────────
    // Handles BOTH first month and all renewal months for monthly subscriptions.
    if (event.type === 'invoice.payment_succeeded') {
      const invoice = event.data?.object ?? {};
      const customerId = invoice.customer;
      const amountPaid = invoice.amount_paid ?? 0;

      if (customerId && amountPaid > 0) {
        // Look up the session to find the referral code
        const [session] = await db
          .select()
          .from(sessionsTable)
          .where(eq(sessionsTable.stripeCustomerId, customerId))
          .limit(1);

        if (session?.referralCode) {
          const billingReason = invoice.billing_reason ?? '';
          await sendAffiliateTransfer(
            session.referralCode,
            amountPaid,
            `NCLEX AI monthly referral — code ${session.referralCode} (${billingReason})`
          );
        }
      }
    }

    // ── customer.subscription.deleted ─────────────────────────────────────
    if (event.type === 'customer.subscription.deleted') {
      const subscription = event.data?.object ?? {};
      const customerId = subscription.customer;
      if (customerId) {
        await db
          .update(sessionsTable)
          .set({ isSubscribed: false, subscriptionEndDate: null, stripeSubscriptionId: null })
          .where(eq(sessionsTable.stripeCustomerId, customerId));

        await db.execute(sql`
          UPDATE sm_subscribers
          SET active = false, paid = false, stripe_subscription_id = null
          WHERE stripe_customer_id = ${customerId}
        `);

        logger.info({ customerId }, 'Subscriptions deactivated after cancellation');
      }
    }
  }
}
