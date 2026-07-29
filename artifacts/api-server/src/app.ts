import express, { type Express } from "express";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import pinoHttp from "pino-http";
import { clerkMiddleware } from "@clerk/express";
import { publishableKeyFromHost } from "@clerk/shared/keys";
import {
  CLERK_PROXY_PATH,
  clerkProxyMiddleware,
  getClerkProxyHost,
} from "./middlewares/clerkProxyMiddleware";
import router from "./routes";
import { logger } from "./lib/logger";
import { WebhookHandlers } from "./webhookHandlers";

const app: Express = express();

// Health check registered first — before all middleware and other routes —
// so the Replit promote-phase prober gets 200 from the first second of startup.
app.get('/api/healthz', (_req, res) => {
  res.status(200).json({ status: 'ok' });
});

app.post(
  '/api/stripe/webhook',
  express.raw({ type: 'application/json' }),
  async (req, res) => {
    const signature = req.headers['stripe-signature'];
    if (!signature) {
      res.status(400).json({ error: 'Missing stripe-signature' });
      return;
    }
    const sig = Array.isArray(signature) ? signature[0] : signature;
    try {
      await WebhookHandlers.processWebhook(req.body as Buffer, sig);
      res.status(200).json({ received: true });
    } catch (error: any) {
      logger.error({ err: error }, 'Webhook error');
      res.status(400).json({ error: 'Webhook processing error' });
    }
  }
);

app.use(CLERK_PROXY_PATH, clerkProxyMiddleware());

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);

// Explicit CORS allowlist — do NOT use `origin: true` (reflects any origin
// with credentials, which is a CORS misconfiguration for credentialed requests).
const _CORS_ALLOWED_ORIGINS: (string | RegExp)[] = [
  "https://nclexai.org",
  /^https:\/\/[\w-]+\.replit\.app$/,
  /^https:\/\/[\w-]+\.repl\.co$/,
  /^https:\/\/[\w-]+\.janeway\.replit\.dev$/,
  /^https:\/\/[\w-]+\.replit\.dev$/,
];
if (process.env.NODE_ENV !== "production") {
  _CORS_ALLOWED_ORIGINS.push(/^http:\/\/localhost(:\d+)?$/);
}
app.use(cors({ credentials: true, origin: _CORS_ALLOWED_ORIGINS }));

// Security headers via helmet (after CORS so preflight headers aren't overridden)
app.use(
  helmet({
    // CSP relaxed for Clerk hosted scripts + Stripe iframe
    contentSecurityPolicy: false,
    // Allow Stripe/Clerk iframes in the frontend
    crossOriginEmbedderPolicy: false,
  })
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use(
  clerkMiddleware((req) => ({
    publishableKey: publishableKeyFromHost(
      getClerkProxyHost(req) ?? "",
      process.env.CLERK_PUBLISHABLE_KEY,
    ),
  })),
);

// ── Per-route rate limiters ────────────────────────────────────────────────
// session/answer: 60 req/min per IP — a real student answers ~1q/30s (≈2/min);
//   60/min gives 30× legitimate headroom while blocking automated harvesting.
app.use(
  "/api/session/answer",
  rateLimit({
    windowMs: 60 * 1000,
    max: 60,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many answer submissions — please slow down" },
  })
);

// stripe/restore-access: 5 req/15min per IP — email-guessing protection.
//   Legitimate use is once per user session; 5 gives enough room for retries.
app.use(
  "/api/stripe/restore-access",
  rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 5,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many restore attempts — try again in 15 minutes" },
  })
);

// stripe/checkout: 20 req/15min per IP — prevents checkout session flooding.
//   Legitimate users create one checkout session per purchase attempt.
app.use(
  "/api/stripe/checkout",
  rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 20,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many checkout requests — try again in 15 minutes" },
  })
);

app.use("/api", router);

export default app;
