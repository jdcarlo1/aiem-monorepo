import { db, sessionsTable } from "@workspace/db";
import { eq, sql } from "drizzle-orm";
import { getStripeSync } from './stripeClient';
import { logger } from './lib/logger';
import crypto from 'crypto';

export class WebhookHandlers {
  static async processWebhook(payload: Buffer, signature: string): Promise<void> {
    if (!Buffer.isBuffer(payload)) {
      throw new Error(
        'STRIPE WEBHOOK ERROR: Payload must be a Buffer. ' +
        'FIX: Ensure webhook route is registered BEFORE app.use(express.json()).'
      );
    }

    // Parse the raw event first so we can handle it even if sync fails
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

    if (event.type === 'checkout.session.completed') {
      const session = event.data?.object ?? {};
      const sessionId = session.metadata?.sessionId;
      const product = session.metadata?.product;
      const customerId = session.customer ?? null;
      const subscriptionId = session.subscription ?? null;

      // NCLEX session unlock
      if (sessionId) {
        await db
          .update(sessionsTable)
          .set({
            isSubscribed: true,
            stripeCustomerId: customerId,
            stripeSubscriptionId: subscriptionId,
          })
          .where(eq(sessionsTable.sessionId, sessionId));
        logger.info({ sessionId, customerId }, 'Session unlocked after successful checkout');
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

    if (event.type === 'customer.subscription.deleted') {
      const subscription = event.data?.object ?? {};
      const customerId = subscription.customer;
      if (customerId) {
        // Deactivate NCLEX session
        await db
          .update(sessionsTable)
          .set({ isSubscribed: false, subscriptionEndDate: null, stripeSubscriptionId: null })
          .where(eq(sessionsTable.stripeCustomerId, customerId));

        // Deactivate StockScanner subscriber
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
