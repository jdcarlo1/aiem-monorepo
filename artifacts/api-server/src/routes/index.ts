import { Router, type IRouter } from "express";
import healthRouter from "./health";
import questionsRouter from "./questions";
import sessionRouter from "./session";
import stripeRouter from "./stripe";
import adaptiveRouter from "./adaptive";
import analyzeRouter from "./analyze";
import catalystRouter from "./catalyst";
import morningBriefRouter from "./morning-brief";
import affiliatesRouter from "./affiliates";
import { adminRateLimiter } from "../lib/adminAuth";

const router: IRouter = Router();

// Rate-limit every /admin/* route before handing off to sub-routers
router.use("/admin", adminRateLimiter);

router.use(healthRouter);
router.use(questionsRouter);
router.use(sessionRouter);
router.use(stripeRouter);
router.use(adaptiveRouter);
router.use(analyzeRouter);
router.use(catalystRouter);
router.use(morningBriefRouter);
router.use(affiliatesRouter);

export default router;
