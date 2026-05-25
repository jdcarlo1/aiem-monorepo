import Stripe from 'stripe';

async function getStripeCredentials(): Promise<{ secretKey: string }> {
  const hostname = process.env.REPLIT_CONNECTORS_HOSTNAME;
  const xReplitToken = process.env.REPL_IDENTITY
    ? "repl " + process.env.REPL_IDENTITY
    : process.env.WEB_REPL_RENEWAL
      ? "depl " + process.env.WEB_REPL_RENEWAL
      : null;

  if (!hostname || !xReplitToken) {
    throw new Error('Missing Replit env vars. Ensure Stripe integration is connected.');
  }

  const resp = await fetch(
    `https://${hostname}/api/v2/connection?include_secrets=true&connector_names=stripe`,
    {
      headers: { Accept: "application/json", X_REPLIT_TOKEN: xReplitToken },
      signal: AbortSignal.timeout(10_000),
    }
  );

  if (!resp.ok) throw new Error(`Failed to fetch Stripe credentials: ${resp.status}`);

  const data = await resp.json();
  const settings = data.items?.[0]?.settings;

  if (!settings?.secret_key) throw new Error('Stripe integration not connected.');

  return { secretKey: settings.secret_key };
}

async function seedProducts() {
  const { secretKey } = await getStripeCredentials();
  const stripe = new Stripe(secretKey);

  console.log('Checking for existing NCLEX AI products...');

  const existingMonthly = await stripe.products.search({
    query: "name:'NCLEX AI Monthly' AND active:'true'",
  });

  if (existingMonthly.data.length > 0) {
    console.log('NCLEX AI Monthly already exists:', existingMonthly.data[0].id);
  } else {
    const monthly = await stripe.products.create({
      name: 'NCLEX AI Monthly',
      description: 'Full access to NCLEX Prep, Nursing School, and Interview Prep — $15/month',
    });
    const monthlyPrice = await stripe.prices.create({
      product: monthly.id,
      unit_amount: 1500,
      currency: 'usd',
      recurring: { interval: 'month' },
    });
    console.log(`Created NCLEX AI Monthly: ${monthly.id} / price: ${monthlyPrice.id}`);
  }

  const existingLifetime = await stripe.products.search({
    query: "name:'NCLEX AI Lifetime' AND active:'true'",
  });

  if (existingLifetime.data.length > 0) {
    console.log('NCLEX AI Lifetime already exists:', existingLifetime.data[0].id);
  } else {
    const lifetime = await stripe.products.create({
      name: 'NCLEX AI Lifetime',
      description: 'Lifetime access to NCLEX Prep, Nursing School, and Interview Prep — pay once',
    });
    const lifetimePrice = await stripe.prices.create({
      product: lifetime.id,
      unit_amount: 4900,
      currency: 'usd',
    });
    console.log(`Created NCLEX AI Lifetime: ${lifetime.id} / price: ${lifetimePrice.id}`);
  }

  console.log('Done! Products are live in Stripe.');
}

seedProducts().catch((err) => {
  console.error('Error:', err.message);
  process.exit(1);
});
