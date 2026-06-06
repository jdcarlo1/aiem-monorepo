import { Router, type IRouter } from "express";
import healthRouter from "./health";
import questionsRouter from "./questions";
import sessionRouter from "./session";
import stripeRouter from "./stripe";
import adaptiveRouter from "./adaptive";
import analyzeRouter from "./analyze";
import catalystRouter from "./catalyst";
import morningBriefRouter from "./morning-brief";

const router: IRouter = Router();

router.use(healthRouter);
router.use(questionsRouter);
router.use(sessionRouter);
router.use(stripeRouter);
router.use(adaptiveRouter);
router.use(analyzeRouter);
router.use(catalystRouter);
router.use(morningBriefRouter);

export default router;
