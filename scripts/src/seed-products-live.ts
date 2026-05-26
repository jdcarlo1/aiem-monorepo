import Stripe from 'stripe';

async function seedProducts() {
  const secretKey = process.env.STRIPE_SECRET_KEY;
  if (!secretKey) throw new Error('STRIPE_SECRET_KEY not set');

  const stripe = new Stripe(secretKey, { apiVersion: '2025-08-27.basil' as any });

  console.log('Seeding products in LIVE Stripe account...');

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
