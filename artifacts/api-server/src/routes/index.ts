import { Router, type IRouter } from "express";
import healthRouter from "./health";
import questionsRouter from "./questions";
import sessionRouter from "./session";
import stripeRouter from "./stripe";
import adaptiveRouter from "./adaptive";
import analyzeRouter from "./analyze";

const router: IRouter = Router();

router.use(healthRouter);
router.use(questionsRouter);
router.use(sessionRouter);
router.use(stripeRouter);
router.use(adaptiveRouter);
router.use(analyzeRouter);

export default router;
