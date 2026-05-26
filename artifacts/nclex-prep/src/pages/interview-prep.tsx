import { useState, useMemo } from "react";
import { Link, useLocation } from "wouter";
import {
  useGetSessionStatus,
  useListQuestions,
  useGetQuestion,
} from "@workspace/api-client-react";
import { useSessionId } from "@/hooks/useSessionId";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowRight,
  Brain,
  CheckCircle2,
  XCircle,
  ChevronLeft,
  RotateCcw,
  Lock,
} from "lucide-react";

interface AnswerResultState {
  correct: boolean;
  correctLetter: string;
  explanation: string;
}

// ─── Single Choice (same as quiz.tsx) ────────────────────────────────────────
function SingleChoice({
  options,
  selected,
  answerResult,
  onSelect,
}: {
  options: { letter: string; text: string }[];
  selected: string | null;
  answerResult: AnswerResultState | null;
  onSelect: (letter: string) => void;
}) {
  return (
    <div className="space-y-3 mb-8">
      {options.map((opt) => {
        const isSelected = selected === opt.letter;
        const showResult = !!answerResult;
        const correctLetters = answerResult?.correctLetter.split(",").map((s) => s.trim()) ?? [];
        const isCorrectAnswer = correctLetters.includes(opt.letter);
        const isWrongSelection = showResult && isSelected && !isCorrectAnswer;

        let cls =
          "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
        if (!showResult) {
          cls += isSelected
            ? "border-primary bg-primary/5 shadow-sm"
            : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
        } else {
          if (isCorrectAnswer)
            cls += "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100";
          else if (isWrongSelection)
            cls += "border-destructive/60 bg-destructive/5 text-destructive";
          else cls += "border-border/50 bg-card/50 opacity-50";
        }

        return (
          <button
            key={opt.letter}
            onClick={() => onSelect(opt.letter)}
            disabled={showResult}
            className={cls}
          >
            <div
              className={`w-8 h-8 rounded-full border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
                !showResult && isSelected
                  ? "border-primary bg-primary text-primary-foreground"
                  : showResult && isCorrectAnswer
                  ? "border-green-500 bg-green-500 text-white"
                  : showResult && isWrongSelection
                  ? "border-destructive bg-destructive text-white"
                  : "border-muted-foreground/30 text-muted-foreground"
              }`}
            >
              {showResult && isCorrectAnswer ? (
                <CheckCircle2 className="w-5 h-5" />
              ) : showResult && isWrongSelection ? (
                <XCircle className="w-5 h-5" />
              ) : (
                opt.letter
              )}
            </div>
            <div className="pt-1 text-base font-medium leading-snug">{opt.text}</div>
          </button>
        );
      })}
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export default function InterviewPrep() {
  const [, setLocation] = useLocation();
  const sessionId = useSessionId();

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

  const handleOptionSelect = (letter: string) => {
    if (answerResult) return;
    setSelectedLetter(letter);
  };

  const handleSubmit = () => {
    if (!currentQuestion || !selectedLetter) return;
    const correct = selectedLetter.trim() === currentQuestion.correctLetter.trim();
    setAnswerResult({
      correct,
      correctLetter: currentQuestion.correctLetter,
      explanation: currentQuestion.explanation,
    });
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

  // ─── Not subscribed ──────────────────────────────────────────────────────
  if (!sessionStatus?.isSubscribed) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background">
        <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
          <div className="max-w-3xl mx-auto flex items-center justify-between">
            <Link
              href="/"
              className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronLeft className="w-4 h-4 mr-1" />
              Home
            </Link>
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-primary" />
              <span className="text-sm font-bold text-primary">NCLEX AI</span>
            </div>
            <div className="w-16" />
          </div>
        </header>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="max-w-md w-full mx-auto text-center">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-6">
              <Lock className="w-8 h-8 text-primary" />
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight mb-3">
              Interview Prep is a Premium Feature
            </h1>
            <p className="text-muted-foreground mb-8 leading-relaxed">
              Unlock all 20 nursing interview questions with detailed rationales — plus 613 NCLEX practice questions — with a one-time $49 lifetime plan.
            </p>
            <div className="flex flex-col gap-3">
              <Button size="lg" className="rounded-xl w-full" onClick={() => setLocation("/paywall")}>
                Unlock Lifetime Access — $49
                <ArrowRight className="w-4 h-4 ml-2" />
              </Button>
              <Link href="/">
                <Button variant="ghost" className="w-full">Back to Home</Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ─── Completed ───────────────────────────────────────────────────────────
  if (completed) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background">
        <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
          <div className="max-w-3xl mx-auto flex items-center justify-between">
            <Link
              href="/"
              className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronLeft className="w-4 h-4 mr-1" />
              Home
            </Link>
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-primary" />
              <span className="text-sm font-bold text-primary">NCLEX AI</span>
            </div>
            <div className="w-16" />
          </div>
        </header>
        <div className="flex-1 flex items-center justify-center p-6">
          <Card className="max-w-md w-full text-center p-8">
            <CheckCircle2 className="w-16 h-16 text-primary mx-auto mb-4" />
            <h2 className="text-2xl font-bold mb-2">Interview Prep Complete!</h2>
            <p className="text-muted-foreground mb-6">
              You've reviewed all {interviewQuestions.length} nursing interview questions. Go get that job!
            </p>
            <div className="flex flex-col gap-3">
              <Button className="w-full" onClick={handleRestart}>
                <RotateCcw className="w-4 h-4 mr-2" />
                Start Over
              </Button>
              <Link href="/quiz">
                <Button variant="outline" className="w-full">Back to NCLEX Practice</Button>
              </Link>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  // ─── Quiz ────────────────────────────────────────────────────────────────
  const isLoading = isQuestionLoading || !currentQuestion;
  const progressPercent =
    interviewQuestions.length > 0 ? (currentIndex / interviewQuestions.length) * 100 : 0;

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link
            href="/"
            className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            Home
          </Link>
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm font-bold text-primary">NCLEX AI</span>
          </div>
          <div className="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground">
            {currentIndex + 1} / {interviewQuestions.length}
          </div>
        </div>
      </header>

      <main className="flex-1 w-full max-w-3xl mx-auto p-4 sm:p-6 pb-24">
        <div className="mb-6 space-y-2">
          <div className="flex justify-between text-sm font-medium text-muted-foreground">
            <span>Question {currentIndex + 1} of {interviewQuestions.length}</span>
            <span>Nursing Interview Prep</span>
          </div>
          <Progress value={progressPercent} className="h-2" />
        </div>

        {isLoading ? (
          <div className="space-y-6">
            <Skeleton className="h-32 w-full" />
            <div className="space-y-3">
              {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          </div>
        ) : (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <h2 className="text-xl sm:text-2xl font-semibold leading-relaxed text-foreground mb-8">
              {currentQuestion.text}
            </h2>

            <SingleChoice
              options={currentQuestion.options}
              selected={selectedLetter}
              answerResult={answerResult}
              onSelect={handleOptionSelect}
            />

            {answerResult ? (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500">
                <Card
                  className={`border-2 ${
                    answerResult.correct
                      ? "border-green-200 bg-green-50/50 dark:border-green-900/50 dark:bg-green-900/10"
                      : "border-red-200 bg-red-50/50 dark:border-red-900/50 dark:bg-red-900/10"
                  }`}
                >
                  <CardContent className="p-6">
                    <h3
                      className={`text-lg font-bold flex items-center gap-2 mb-3 ${
                        answerResult.correct
                          ? "text-green-700 dark:text-green-400"
                          : "text-destructive"
                      }`}
                    >
                      {answerResult.correct ? (
                        <><CheckCircle2 className="w-6 h-6" /> Correct!</>
                      ) : (
                        <><XCircle className="w-6 h-6" /> Incorrect</>
                      )}
                    </h3>
                    <p className="text-foreground leading-relaxed text-sm sm:text-base">
                      {answerResult.explanation}
                    </p>
                    <div className="mt-6">
                      <Button size="lg" className="w-full sm:w-auto" onClick={handleNext}>
                        {currentIndex + 1 >= interviewQuestions.length ? "Finish" : "Next Question"}
                        <ArrowRight className="w-4 h-4 ml-2" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            ) : (
              <Button
                size="lg"
                className="w-full sm:w-auto px-8"
                disabled={!selectedLetter}
                onClick={handleSubmit}
              >
                Submit Answer
              </Button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
