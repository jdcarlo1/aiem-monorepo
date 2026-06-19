import { Router } from "express";
import { db, affiliatesTable, sessionsTable } from "@workspace/db";
import { eq, sql } from "drizzle-orm";
import { getUncachableStripeClient } from "../stripeClient";

const router = Router();

function requireAdmin(req: any, res: any): boolean {
  const secret = req.headers["x-admin-secret"];
  const expected = process.env.ADMIN_TOKEN;
  if (!expected || secret !== expected) {
    res.status(401).json({ error: "Unauthorized" });
    return false;
  }
  return true;
}

router.post("/admin/affiliates", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const { code, name, commissionPct } = req.body as {
    code: string;
    name: string;
    commissionPct?: number;
  };

  if (!code || !name) {
    res.status(400).json({ error: "code and name are required" });
    return;
  }

  const upperCode = code.trim().toUpperCase();

  const existing = await db
    .select()
    .from(affiliatesTable)
    .where(eq(affiliatesTable.code, upperCode))
    .limit(1);

  if (existing.length > 0) {
    res.status(409).json({ error: `Code ${upperCode} already exists` });
    return;
  }

  const stripe = await getUncachableStripeClient();

  const account = await stripe.accounts.create({
    type: "express",
    metadata: { code: upperCode, name },
  });

  const pct = commissionPct ?? 50;

  await db.insert(affiliatesTable).values({
    code: upperCode,
    name: name.trim(),
    stripeConnectId: account.id,
    commissionPct: pct,
  });

  const baseUrl = process.env.SITE_URL ?? "https://nclexai.org";

  const accountLink = await stripe.accountLinks.create({
    account: account.id,
    refresh_url: `${baseUrl}/admin/affiliates`,
    return_url: `${baseUrl}/admin/affiliates`,
    type: "account_onboarding",
  });

  res.json({
    success: true,
    code: upperCode,
    name: name.trim(),
    commissionPct: pct,
    stripeConnectId: account.id,
    onboardingUrl: accountLink.url,
    message: `Send this link to ${name.trim()} — they complete it once on Stripe's site and are ready to receive payments.`,
  });
});

router.post("/admin/affiliates/:code/refresh-link", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const code = req.params.code.toUpperCase();

  const [affiliate] = await db
    .select()
    .from(affiliatesTable)
    .where(eq(affiliatesTable.code, code))
    .limit(1);

  if (!affiliate) {
    res.status(404).json({ error: `Affiliate ${code} not found` });
    return;
  }

  if (!affiliate.stripeConnectId) {
    res.status(400).json({ error: "Affiliate has no Stripe Connect account" });
    return;
  }

  const stripe = await getUncachableStripeClient();
  const baseUrl = process.env.SITE_URL ?? "https://nclexai.org";

  const accountLink = await stripe.accountLinks.create({
    account: affiliate.stripeConnectId,
    refresh_url: `${baseUrl}/admin/affiliates`,
    return_url: `${baseUrl}/admin/affiliates`,
    type: "account_onboarding",
  });

  res.json({ success: true, code, onboardingUrl: accountLink.url });
});

router.get("/admin/affiliates", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const affiliates = await db.select().from(affiliatesTable);

  const stripe = await getUncachableStripeClient();

  const results = await Promise.all(
    affiliates.map(async (aff) => {
      const referralCount = await db
        .select({ count: sql<number>`count(*)` })
        .from(sessionsTable)
        .where(eq(sessionsTable.referralCode, aff.code));

      let stripeStatus = "not_started";
      if (aff.stripeConnectId) {
        try {
          const account = await stripe.accounts.retrieve(aff.stripeConnectId);
          stripeStatus = account.details_submitted
            ? account.payouts_enabled
              ? "active"
              : "pending"
            : "onboarding_incomplete";
        } catch {
          stripeStatus = "error";
        }
      }

      return {
        ...aff,
        referralCount: Number(referralCount[0]?.count ?? 0),
        stripeStatus,
      };
    })
  );

  res.json({ affiliates: results });
});

router.delete("/admin/affiliates/:code", async (req, res) => {
  if (!requireAdmin(req, res)) return;

  const code = req.params.code.toUpperCase();
  await db.delete(affiliatesTable).where(eq(affiliatesTable.code, code));
  res.json({ success: true });
});

export default router;
