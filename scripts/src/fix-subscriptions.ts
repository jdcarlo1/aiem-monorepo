import Stripe from "stripe";
import pg from "pg";

const { Pool } = pg;
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

const sessions = await stripe.checkout.sessions.list({ limit: 100, status: "complete" });
console.log(`Found ${sessions.data.length} completed checkout sessions in Stripe`);

for (const cs of sessions.data) {
  const sessionId = cs.metadata?.sessionId;
  const customerId = typeof cs.customer === "string" ? cs.customer : null;
  const subscriptionId = typeof cs.subscription === "string" ? cs.subscription : null;

  if (!sessionId) {
    console.log(`  Skipped ${cs.id} — no sessionId in metadata`);
    continue;
  }

  await pool.query(
    `INSERT INTO sessions (session_id, questions_answered, is_subscribed, stripe_customer_id, stripe_subscription_id)
     VALUES ($1, 0, true, $2, $3)
     ON CONFLICT (session_id) DO UPDATE SET is_subscribed = true, stripe_customer_id = $2, stripe_subscription_id = $3`,
    [sessionId, customerId, subscriptionId]
  );
  console.log(`  ✅ Activated: ${sessionId} (customer: ${customerId})`);
}

await pool.end();
console.log("Done!");
process.exit(0);
