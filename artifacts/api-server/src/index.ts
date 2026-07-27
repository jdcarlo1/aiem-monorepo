import app from "./app";
import { logger } from "./lib/logger";
import { runMigrations } from 'stripe-replit-sync';
import { getStripeSync } from "./stripeClient";

process.on('uncaughtException', (err) => {
  logger.error({ err }, 'Uncaught exception — continuing');
});
process.on('unhandledRejection', (err) => {
  logger.error({ err }, 'Unhandled rejection — continuing');
});

const port = Number(process.env["PORT"] || 8080);

async function initStripe() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    logger.warn('DATABASE_URL not set — skipping Stripe init');
    return;
  }

  try {
    logger.info('Initializing Stripe schema...');
    await (runMigrations as any)({ databaseUrl, schema: 'stripe' });
    logger.info('Stripe schema ready');

    const stripeSync = await getStripeSync();

    const webhookBaseUrl = process.env.SITE_URL ?? "https://nclexai.org";
    await stripeSync.findOrCreateManagedWebhook(`${webhookBaseUrl}/api/stripe/webhook`);
    logger.info('Stripe webhook configured');

    stripeSync.syncBackfill()
      .then(() => logger.info('Stripe backfill complete'))
      .catch((err) => logger.error({ err }, 'Stripe backfill error'));
  } catch (error) {
    logger.error({ err: error }, 'Failed to initialize Stripe — continuing without payments');
  }
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");

  initStripe().catch((err) => logger.error({ err }, 'Stripe init failed'));
});
