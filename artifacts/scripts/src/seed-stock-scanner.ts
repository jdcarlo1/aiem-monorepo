import { getUncachableStripeClient } from './stripeClient';

async function createStockScannerProduct() {
  try {
    const stripe = await getUncachableStripeClient();
    console.log('Checking for existing StockScanner AI Pro product...');

    const existing = await stripe.products.search({
      query: "name:'StockScanner AI Pro' AND active:'true'",
    });

    if (existing.data.length > 0) {
      const prod = existing.data[0];
      const prices = await stripe.prices.list({ product: prod.id, active: true, limit: 5 });
      console.log('StockScanner AI Pro already exists.');
      console.log(`  Product ID: ${prod.id}`);
      for (const p of prices.data) {
        const interval = (p.recurring as any)?.interval ?? 'one-time';
        const amount = ((p.unit_amount ?? 0) / 100).toFixed(2);
        console.log(`  Price ID: ${p.id}  $${amount}/${interval}`);
      }
      return;
    }

    console.log('Creating StockScanner AI Pro product...');
    const product = await stripe.products.create({
      name: 'StockScanner AI Pro',
      description: 'Daily Smart Money email alerts — 4 emails per trading day with top buy signals, real options flow, and historical win-rate data.',
      metadata: { product: 'stock-scanner' },
    });
    console.log(`Created product: ${product.name} (${product.id})`);

    const monthlyPrice = await stripe.prices.create({
      product: product.id,
      unit_amount: 2900,
      currency: 'usd',
      recurring: { interval: 'month' },
    });
    console.log(`Created monthly price: $29.00/month (${monthlyPrice.id})`);

    console.log('\n✅ Done! Copy these IDs:');
    console.log(`  PRODUCT_ID=${product.id}`);
    console.log(`  PRICE_ID=${monthlyPrice.id}`);
  } catch (err: any) {
    console.error('Error:', err.message);
    process.exit(1);
  }
}

createStockScannerProduct();
