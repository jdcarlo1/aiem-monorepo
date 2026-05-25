import { useState, useMemo, useCallback } from "react";
import { Link, useLocation } from "wouter";
import {
  useGetSessionStatus,
  useListQuestions,
  useGetQuestion,
  useSubmitAnswer,
} from "@workspace/api-client-react";
import { getSessionId } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowRight,
  Briefcase,
  CheckCircle2,
  XCircle,
  Lock,
  ChevronLeft,
  RotateCcw,
} from "lucide-react";

interface AnswerResultState {
  correct: boolean;
  correctLetter: string;
  explanation: string;
  canAnswerMore: boolean;
  questionsAnswered: number;
}

export default function InterviewPrep() {
  const [, setLocation] = useLocation();
  const sessionId = getSessionId();

  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedLetter, setSelectedLetter] = useState<string | null>(null);
  const [answerResult, setAnswerResult] = useState<AnswerResultState | null>(null);
  const [completed, setCompleted] = useState(false);

  const { data: sessionStatus, isLoading: isSessionLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  const { data: questionsList, isLoading: isListLoading } = useListQuestions();

  const interviewQuestions = useMemo(() => {
    if (!questionsList) return [];
    return [...questionsList]
      .filter((q) => q.category === "Nursing Interview Prep")
      .sort((a, b) => a.questionNumber - b.questionNumber);
  }, [questionsList]);

  const currentQuestionSummary = interviewQuestions[currentIndex];

  const { data: currentQuestion, isLoading: isQuestionLoading } = useGetQuestion(
    currentQuestionSummary?.id ?? 0,
    { query: { enabled: !!currentQuestionSummary?.id } }
  );

  const submitAnswer = useSubmitAnswer();

  const handleSelect = (letter: string) => {
    if (answerResult || submitAnswer.isPending) return;
    setSelectedLetter(letter);
  };

  const handleSubmit = () => {
    if (!currentQuestion || !selectedLetter) return;
    submitAnswer.mutate(
      { data: { sessionId, questionId: currentQuestion.id, selectedLetter } },
      { onSuccess: (result) => setAnswerResult(result) }
    );
  };

  const handleNext = () => {
    if (currentIndex + 1 >= interviewQuestions.length) {
      setCompleted(true);
    } else {
      setCurrentIndex((i) => i + 1);
    }
    setSelectedLetter(null);
    setAnswerResult(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleRestart = () => {
    setCurrentIndex(0);
    setSelectedLetter(null);
    setAnswerResult(null);
    setCompleted(false);
  };

  if (isSessionLoading || isListLoading) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background p-6">
        <Skeleton className="h-10 w-full max-w-3xl mx-auto mb-8" />
        <Skeleton className="h-64 w-full max-w-3xl mx-auto mb-6" />
        <div className="space-y-4 max-w-3xl mx-auto w-full">
          {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      </div>
    );
  }

  if (!sessionStatus?.isSubscribed) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background justify-center p-6">
        <div className="max-w-md w-full mx-auto text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-6">
            <Lock className="w-8 h-8 text-primary" />
          </div>
          <h1 className="text-2xl font-extrabold tracking-tight mb-3">
            Interview Prep is a Premium Feature
          </h1>
          <p className="text-muted-foreground mb-8 leading-relaxed">
            Unlock all 20 nursing interview questions with detailed rationales — plus 593 NCLEX practice questions — with a one-time $49 lifetime plan.
          </p>
          <div className="flex flex-col gap-3">
            <Button size="lg" className="rounded-xl" onClick={() => setLocation("/paywall")}>
              Unlock Lifetime Access — $49
              <ArrowRight className="w-4 h-4 ml-2" />
            </Button>
            <Link href="/">
              <Button variant="ghost" className="w-full">Back to Home</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (completed) {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center bg-background p-6">
        <Card className="max-w-md w-full text-center p-8">
          <CheckCircle2 className="w-16 h-16 text-primary mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">Interview Prep Complete!</h2>
          <p className="text-muted-foreground mb-6">
            You've reviewed all {interviewQuestions.length} nursing interview questions. Feel confident walking into your next interview.
          </p>
          <div className="flex flex-col gap-3">
            <Button className="w-full rounded-xl" onClick={handleRestart}>
              <RotateCcw className="w-4 h-4 mr-2" />
              Start Over
            </Button>
            <Link href="/quiz">
              <Button variant="outline" className="w-full rounded-xl">Back to NCLEX Practice</Button>
            </Link>
            <Link href="/">
              <Button variant="ghost" className="w-full">Return Home</Button>
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  const options = currentQuestion?.options ?? [];
  const progress = Math.round(((currentIndex) / interviewQuestions.length) * 100);

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-6 py-4 border-b border-border bg-card/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2 text-primary hover:opacity-80 transition-opacity font-semibold">
            <ChevronLeft className="w-4 h-4" />
            Home
          </Link>
          <div className="flex items-center gap-2">
            <Briefcase className="w-4 h-4 text-primary" />
            <span className="font-semibold text-sm">Nursing Interview Prep</span>
          </div>
          <span className="text-sm text-muted-foreground font-medium">
            {currentIndex + 1} / {interviewQuestions.length}
          </span>
        </div>
      </header>

      <main className="flex-1 px-4 py-8 max-w-3xl mx-auto w-full">
        <div className="mb-6">
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-2">
            <span>Progress</span>
            <span>{progress}% complete</span>
          </div>
          <div className="w-full bg-secondary rounded-full h-2">
            <div
              className="bg-primary h-2 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        <div className="mb-2">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full bg-primary/10 text-primary border border-primary/20">
            <Briefcase className="w-3 h-3" />
            Interview Question {currentIndex + 1}
          </span>
        </div>

        {isQuestionLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-24 w-full" />
            {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        ) : currentQuestion ? (
          <>
            <p className="text-xl font-semibold leading-relaxed mb-8 text-foreground">
              {currentQuestion.text}
            </p>

            <div className="space-y-3 mb-8">
              {options.map((opt) => {
                const isSelected = selectedLetter === opt.letter;
                const showResult = !!answerResult;
                const isCorrect = answerResult?.correctLetter === opt.letter;
                const isWrong = showResult && isSelected && !isCorrect;

                let cls = "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
                if (!showResult) {
                  cls += isSelected
                    ? "border-primary bg-primary/5 shadow-sm"
                    : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
                } else {
                  if (isCorrect) cls += "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100";
                  else if (isWrong) cls += "border-destructive/60 bg-destructive/5 text-destructive";
                  else cls += "border-border/50 bg-card/50 opacity-50";
                }

                return (
                  <button
                    key={opt.letter}
                    onClick={() => handleSelect(opt.letter)}
                    disabled={showResult}
                    className={cls}
                  >
                    <div className={`w-8 h-8 rounded-full border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
                      !showResult && isSelected
                        ? "border-primary bg-primary text-primary-foreground"
                        : showResult && isCorrect
                        ? "border-green-500 bg-green-500 text-white"
                        : showResult && isWrong
                        ? "border-destructive bg-destructive text-white"
                        : "border-muted-foreground/30 text-muted-foreground"
                    }`}>
                      {showResult && isCorrect ? <CheckCircle2 className="w-5 h-5" /> :
                       showResult && isWrong ? <XCircle className="w-5 h-5" /> : opt.letter}
                    </div>
                    <div className="pt-1 text-base font-medium leading-snug">{opt.text}</div>
                  </button>
                );
              })}
            </div>

            {answerResult && (
              <div className={`p-5 rounded-2xl border mb-6 ${
                answerResult.correct
                  ? "bg-green-50 border-green-200 dark:bg-green-950/20 dark:border-green-800"
                  : "bg-orange-50 border-orange-200 dark:bg-orange-950/20 dark:border-orange-800"
              }`}>
                <div className="flex items-center gap-2 mb-2">
                  {answerResult.correct
                    ? <CheckCircle2 className="w-5 h-5 text-green-600" />
                    : <XCircle className="w-5 h-5 text-orange-500" />}
                  <span className={`font-bold text-base ${answerResult.correct ? "text-green-700 dark:text-green-400" : "text-orange-600 dark:text-orange-400"}`}>
                    {answerResult.correct ? "Great answer!" : "Not quite — here's why:"}
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-foreground/80">{answerResult.explanation}</p>
              </div>
            )}

            <div className="flex justify-end gap-3">
              {!answerResult ? (
                <Button
                  size="lg"
                  className="rounded-xl px-8"
                  onClick={handleSubmit}
                  disabled={!selectedLetter || submitAnswer.isPending}
                >
                  {submitAnswer.isPending ? "Checking..." : "Submit Answer"}
                </Button>
              ) : (
                <Button
                  size="lg"
                  className="rounded-xl px-8"
                  onClick={handleNext}
                >
                  {currentIndex + 1 >= interviewQuestions.length ? "Finish" : "Next Question"}
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              )}
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
