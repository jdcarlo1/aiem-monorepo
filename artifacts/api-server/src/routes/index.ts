import { Router, type IRouter } from "express";
import healthRouter from "./health";
import questionsRouter from "./questions";
import sessionRouter from "./session";

const router: IRouter = Router();

router.use(healthRouter);
router.use(questionsRouter);
router.use(sessionRouter);

export default router;
