import Stripe from 'stripe';

async function getStripeCredentials(): Promise<{ secretKey: string; publishableKey: string }> {
  const envSecret = process.env.STRIPE_SECRET_KEY;

  if (envSecret && envSecret.startsWith('sk_live_')) {
    return {
      secretKey: envSecret,
      publishableKey: process.env.STRIPE_PUBLISHABLE_KEY || '',
    };
  }

  const hostname = process.env.REPLIT_CONNECTORS_HOSTNAME;
  const xReplitToken = process.env.REPL_IDENTITY
    ? 'repl ' + process.env.REPL_IDENTITY
    : process.env.WEB_REPL_RENEWAL
      ? 'depl ' + process.env.WEB_REPL_RENEWAL
      : null;

  if (hostname && xReplitToken) {
    const isProduction = process.env.REPLIT_DEPLOYMENT === '1';
    const targetEnvironment = isProduction ? 'production' : 'development';

    const url = new URL(`https://${hostname}/api/v2/connection`);
    url.searchParams.set('include_secrets', 'true');
    url.searchParams.set('connector_names', 'stripe');
    url.searchParams.set('environment', targetEnvironment);

    const resp = await fetch(url.toString(), {
      headers: {
        'Accept': 'application/json',
        'X-Replit-Token': xReplitToken,
      },
      signal: AbortSignal.timeout(10_000),
    });

    if (resp.ok) {
      const data = await resp.json() as any;
      const settings = data.items?.[0]?.settings;
      if (settings?.secret && settings?.publishable) {
        const secret = (envSecret && envSecret.startsWith('sk_live_')) ? envSecret : settings.secret;
        return {
          secretKey: secret,
          publishableKey: settings.publishable,
        };
      }
    }
  }

  if (envSecret) {
    return {
      secretKey: envSecret,
      publishableKey: process.env.STRIPE_PUBLISHABLE_KEY || '',
    };
  }

  throw new Error(
    'Stripe not configured. Add STRIPE_SECRET_KEY to Secrets or connect via the Integrations tab.'
  );
}

export async function getUncachableStripeClient(): Promise<Stripe> {
  const { secretKey } = await getStripeCredentials();
  return new Stripe(secretKey, { apiVersion: '2025-08-27.basil' as any });
}

export async function getStripePublishableKey(): Promise<string> {
  const { publishableKey } = await getStripeCredentials();
  return publishableKey;
}

export async function getStripeSync() {
  const { StripeSync } = await import('stripe-replit-sync');
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error('DATABASE_URL environment variable is required');
  const { secretKey } = await getStripeCredentials();
  return new StripeSync({
    poolConfig: { connectionString: databaseUrl, max: 2 },
    stripeSecretKey: secretKey,
  });
}
