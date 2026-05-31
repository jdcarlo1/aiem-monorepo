import { db, sessionsTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import { getStripeSync } from './stripeClient';
import { logger } from './lib/logger';

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
      const customerId = session.customer;
      const subscriptionId = session.subscription ?? null;

      if (sessionId) {
        await db
          .update(sessionsTable)
          .set({
            isSubscribed: true,
            stripeCustomerId: customerId ?? null,
            stripeSubscriptionId: subscriptionId,
          })
          .where(eq(sessionsTable.sessionId, sessionId));
        logger.info({ sessionId, customerId }, 'Session unlocked after successful checkout');
      }
    }

    if (event.type === 'customer.subscription.deleted') {
      const subscription = event.data?.object ?? {};
      const customerId = subscription.customer;
      if (customerId) {
        await db
          .update(sessionsTable)
          .set({ isSubscribed: false, subscriptionEndDate: null, stripeSubscriptionId: null })
          .where(eq(sessionsTable.stripeCustomerId, customerId));
        logger.info({ customerId }, 'Session deactivated after subscription cancelled');
      }
    }
  }
}
