import crypto from "crypto";
import { type Request, type Response } from "express";
import rateLimit from "express-rate-limit";

/**
 * Timing-safe admin token check.
 * Returns true if the request is authorised; sends 401 and returns false otherwise.
 */
export function requireAdmin(req: Request, res: Response): boolean {
  const secret = req.headers["x-admin-secret"];
  const expected = process.env.ADMIN_TOKEN;

  if (!expected) {
    res.status(503).json({ error: "Admin token not configured on server" });
    return false;
  }

  if (typeof secret !== "string") {
    res.status(401).json({ error: "Unauthorized" });
    return false;
  }

  // Constant-time comparison prevents timing-based token enumeration
  const secretBuf = Buffer.from(secret);
  const expectedBuf = Buffer.from(expected);

  if (
    secretBuf.length !== expectedBuf.length ||
    !crypto.timingSafeEqual(secretBuf, expectedBuf)
  ) {
    res.status(401).json({ error: "Unauthorized" });
    return false;
  }

  return true;
}

/**
 * Rate limiter for /api/admin/* routes.
 * 20 requests per 15 minutes per IP — enough for legitimate admin use
 * but slow enough to make brute-force impractical.
 */
export const adminRateLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many admin requests — try again in 15 minutes" },
});
