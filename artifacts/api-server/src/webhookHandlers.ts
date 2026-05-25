import { db, sessionsTable } from "@workspace/db";
import { eq } from "drizzle-orm";
import { getStripeSync } from './stripeClient';

export class WebhookHandlers {
  static async processWebhook(payload: Buffer, signature: string): Promise<void> {
    if (!Buffer.isBuffer(payload)) {
      throw new Error(
        'STRIPE WEBHOOK ERROR: Payload must be a Buffer. ' +
        'FIX: Ensure webhook route is registered BEFORE app.use(express.json()).'
      );
    }

    const sync = await getStripeSync();

    // stripe-replit-sync validates the signature and syncs to stripe schema
    await sync.processWebhook(payload, signature);

    // Parse the raw event for custom session-level handling
    let event: any;
    try {
      event = JSON.parse(payload.toString());
    } catch {
      return;
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
      }
    }
  }
}
