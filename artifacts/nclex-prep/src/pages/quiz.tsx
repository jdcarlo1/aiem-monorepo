import { useState, useMemo, useCallback } from "react";
import { Link, useLocation } from "wouter";
import {
  useGetSessionStatus,
  useListQuestions,
  useGetQuestion,
  useSubmitAnswer,
  getGetSessionStatusQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { getSessionId } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChevronLeft,
  ArrowRight,
  CheckCircle2,
  XCircle,
  GripVertical,
  Brain,
} from "lucide-react";

// ─── Single Choice ────────────────────────────────────────────────────────────
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
        const correctLetters = answerResult?.correctLetter.split(",").map(s => s.trim()) ?? [];
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
            cls +=
              "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100";
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

// ─── Multiple Choice (Select All That Apply) ─────────────────────────────────
function MultipleChoice({
  options,
  selected,
  answerResult,
  onToggle,
}: {
  options: { letter: string; text: string }[];
  selected: string[];
  answerResult: AnswerResultState | null;
  onToggle: (letter: string) => void;
}) {
  const correctLetters = answerResult?.correctLetter.split(",").map(s => s.trim()) ?? [];

  return (
    <div className="space-y-3 mb-8">
      <p className="text-sm font-semibold text-primary bg-primary/10 border border-primary/20 rounded-lg px-3 py-2 inline-block mb-2">
        Select all that apply
      </p>
      {options.map((opt) => {
        const isSelected = selected.includes(opt.letter);
        const showResult = !!answerResult;
        const isCorrect = correctLetters.includes(opt.letter);
        const isWrongSelection = showResult && isSelected && !isCorrect;
        const isMissed = showResult && !isSelected && isCorrect;

        let cls =
          "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
        if (!showResult) {
          cls += isSelected
            ? "border-primary bg-primary/5 shadow-sm"
            : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
        } else {
          if (isCorrect && isSelected)
            cls += "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100";
          else if (isMissed)
            cls += "border-amber-400 bg-amber-50 text-amber-900 dark:bg-amber-950/30 dark:text-amber-100";
          else if (isWrongSelection)
            cls += "border-destructive/60 bg-destructive/5 text-destructive";
          else cls += "border-border/50 bg-card/50 opacity-50";
        }

        return (
          <button
            key={opt.letter}
            onClick={() => onToggle(opt.letter)}
            disabled={showResult}
            className={cls}
          >
            <div
              className={`w-8 h-8 rounded-lg border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
                !showResult && isSelected
                  ? "border-primary bg-primary text-primary-foreground"
                  : showResult && isCorrect && isSelected
                  ? "border-green-500 bg-green-500 text-white"
                  : showResult && isMissed
                  ? "border-amber-400 bg-amber-400 text-white"
                  : showResult && isWrongSelection
                  ? "border-destructive bg-destructive text-white"
                  : "border-muted-foreground/30 text-muted-foreground"
              }`}
            >
              {showResult && isCorrect && isSelected ? (
                <CheckCircle2 className="w-5 h-5" />
              ) : showResult && isWrongSelection ? (
                <XCircle className="w-5 h-5" />
              ) : showResult && isMissed ? (
                <CheckCircle2 className="w-5 h-5" />
              ) : isSelected ? (
                "✓"
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

// ─── Ordered / Drag-and-Drop ─────────────────────────────────────────────────
function OrderedQuestion({
  items,
  setItems,
  answerResult,
  correctLetter,
}: {
  items: { letter: string; text: string }[];
  setItems: (items: { letter: string; text: string }[]) => void;
  answerResult: AnswerResultState | null;
  correctLetter: string;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const correctOrder = correctLetter.split(",").map(s => s.trim());

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDragIndex(index);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverIndex(index);
  };

  const handleDrop = (e: React.DragEvent, dropIndex: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === dropIndex) {
      setDragIndex(null);
      setDragOverIndex(null);
      return;
    }
    const newItems = [...items];
    const [moved] = newItems.splice(dragIndex, 1);
    newItems.splice(dropIndex, 0, moved);
    setItems(newItems);
    setDragIndex(null);
    setDragOverIndex(null);
  };

  const handleDragEnd = () => {
    setDragIndex(null);
    setDragOverIndex(null);
  };

  // Move up/down for mobile
  const moveUp = (i: number) => {
    if (i === 0 || answerResult) return;
    const newItems = [...items];
    [newItems[i - 1], newItems[i]] = [newItems[i], newItems[i - 1]];
    setItems(newItems);
  };
  const moveDown = (i: number) => {
    if (i === items.length - 1 || answerResult) return;
    const newItems = [...items];
    [newItems[i], newItems[i + 1]] = [newItems[i + 1], newItems[i]];
    setItems(newItems);
  };

  return (
    <div className="space-y-2 mb-8">
      <p className="text-sm font-semibold text-primary bg-primary/10 border border-primary/20 rounded-lg px-3 py-2 inline-block mb-3">
        Drag to arrange in the correct order
      </p>
      {items.map((item, i) => {
        const isCorrectPos = answerResult && correctOrder[i] === item.letter;
        const isWrongPos = answerResult && correctOrder[i] !== item.letter;
        const isDragging = dragIndex === i;
        const isDragOver = dragOverIndex === i && dragIndex !== i;

        let cls = "w-full p-4 rounded-xl border-2 flex items-center gap-3 transition-all duration-200 ";
        if (!answerResult) {
          cls += isDragging
            ? "opacity-50 border-primary bg-primary/5 scale-95"
            : isDragOver
            ? "border-primary/60 bg-primary/10 scale-[1.02]"
            : "border-border bg-card hover:border-primary/40 cursor-grab active:cursor-grabbing";
        } else {
          cls += isCorrectPos
            ? "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100 cursor-default"
            : "border-destructive/40 bg-destructive/5 text-destructive cursor-default";
        }

        return (
          <div
            key={item.letter}
            draggable={!answerResult}
            onDragStart={(e) => handleDragStart(e, i)}
            onDragOver={(e) => handleDragOver(e, i)}
            onDrop={(e) => handleDrop(e, i)}
            onDragEnd={handleDragEnd}
            className={cls}
          >
            {!answerResult && (
              <GripVertical className="w-5 h-5 text-muted-foreground shrink-0" />
            )}
            {answerResult && (
              isCorrectPos
                ? <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
                : <XCircle className="w-5 h-5 text-destructive/70 shrink-0" />
            )}
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground shrink-0">
              {i + 1}
            </div>
            <p className="text-base font-medium flex-1 leading-snug">{item.text}</p>
            {!answerResult && (
              <div className="flex flex-col gap-1 shrink-0">
                <button
                  onClick={() => moveUp(i)}
                  disabled={i === 0}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30 text-xs leading-none px-1"
                >▲</button>
                <button
                  onClick={() => moveDown(i)}
                  disabled={i === items.length - 1}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-30 text-xs leading-none px-1"
                >▼</button>
              </div>
            )}
          </div>
        );
      })}
      {answerResult && (
        <div className="mt-3 p-3 rounded-lg bg-muted/50 border border-border text-sm">
          <p className="font-semibold text-muted-foreground mb-1">Correct order:</p>
          <ol className="list-decimal list-inside space-y-1">
            {correctOrder.map((letter) => {
              const item = items.find(it => it.letter === letter);
              return <li key={letter} className="text-foreground">{item?.text}</li>;
            })}
          </ol>
        </div>
      )}
    </div>
  );
}

// ─── Types ────────────────────────────────────────────────────────────────────
interface AnswerResultState {
  correct: boolean;
  correctLetter: string;
  explanation: string;
  canAnswerMore: boolean;
  questionsAnswered: number;
}

// ─── Main Quiz ────────────────────────────────────────────────────────────────
export default function Quiz() {
  const [, setLocation] = useLocation();
  const sessionId = getSessionId();
  const queryClient = useQueryClient();

  const [selectedLetter, setSelectedLetter] = useState<string | null>(null);
  const [selectedLetters, setSelectedLetters] = useState<string[]>([]);
  const [orderedItems, setOrderedItems] = useState<{ letter: string; text: string }[] | null>(null);
  const [answerResult, setAnswerResult] = useState<AnswerResultState | null>(null);

  const { data: sessionStatus, isLoading: isSessionLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  const { data: questionsList, isLoading: isListLoading } = useListQuestions();

  const sortedQuestions = useMemo(() => {
    if (!questionsList) return [];
    return [...questionsList].sort((a, b) => a.questionNumber - b.questionNumber);
  }, [questionsList]);

  const currentIndex = sessionStatus ? sessionStatus.questionsAnswered : 0;
  const currentQuestionSummary = sortedQuestions[currentIndex];
  const isFinished = sortedQuestions.length > 0 && currentIndex >= sortedQuestions.length;

  const { data: currentQuestion, isLoading: isQuestionLoading } = useGetQuestion(
    currentQuestionSummary?.id ?? 0,
    {
      query: {
        enabled: !!currentQuestionSummary?.id,
        // When question changes, reset ordered items
      },
    }
  );

  // Shuffle ordered items when question loads (only for 'ordered' type)
  const questionType = currentQuestion?.questionType ?? "single";

  // Initialize orderedItems when a new ordered question loads
  const orderedKey = currentQuestion?.id;
  useMemo(() => {
    if (currentQuestion?.questionType === "ordered" && !answerResult) {
      const shuffled = [...(currentQuestion.options ?? [])].sort(() => Math.random() - 0.5);
      setOrderedItems(shuffled);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderedKey]);

  const submitAnswer = useSubmitAnswer();

  const handleOptionSelect = (letter: string) => {
    if (answerResult || submitAnswer.isPending) return;
    setSelectedLetter(letter);
  };

  const handleToggleLetter = (letter: string) => {
    if (answerResult || submitAnswer.isPending) return;
    setSelectedLetters((prev) =>
      prev.includes(letter) ? prev.filter((l) => l !== letter) : [...prev, letter]
    );
  };

  const buildSelectedLetter = useCallback((): string | null => {
    if (questionType === "multiple") {
      if (selectedLetters.length === 0) return null;
      return [...selectedLetters].sort().join(",");
    }
    if (questionType === "ordered") {
      if (!orderedItems) return null;
      return orderedItems.map((it) => it.letter).join(",");
    }
    return selectedLetter;
  }, [questionType, selectedLetters, orderedItems, selectedLetter]);

  const canSubmit = (() => {
    if (questionType === "multiple") return selectedLetters.length > 0;
    if (questionType === "ordered") return !!orderedItems;
    return !!selectedLetter;
  })();

  const handleSubmit = () => {
    if (!currentQuestion || !canSubmit) return;
    const answer = buildSelectedLetter();
    if (!answer) return;

    submitAnswer.mutate(
      {
        data: {
          sessionId,
          questionId: currentQuestion.id,
          selectedLetter: answer,
        },
      },
      {
        onSuccess: (result) => {
          setAnswerResult(result);
        },
      }
    );
  };

  const handleNext = () => {
    if (answerResult && !answerResult.canAnswerMore) {
      setLocation("/paywall");
      return;
    }
    if (answerResult) {
      queryClient.setQueryData(
        getGetSessionStatusQueryKey({ sessionId }),
        (old: any) => {
          if (!old) return old;
          return {
            ...old,
            questionsAnswered: answerResult.questionsAnswered,
            canAnswerMore: answerResult.canAnswerMore,
          };
        }
      );
    }
    setSelectedLetter(null);
    setSelectedLetters([]);
    setOrderedItems(null);
    setAnswerResult(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (isSessionLoading || isListLoading) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background p-6">
        <Skeleton className="h-10 w-full max-w-3xl mx-auto mb-8" />
        <Skeleton className="h-64 w-full max-w-3xl mx-auto mb-6" />
        <div className="space-y-4 max-w-3xl mx-auto w-full">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isFinished) {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center bg-background p-6">
        <Card className="max-w-md w-full text-center p-8">
          <CheckCircle2 className="w-16 h-16 text-primary mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">You've completed all questions!</h2>
          <p className="text-muted-foreground mb-6">More questions will be added soon.</p>
          <Link href="/">
            <Button className="w-full">Return Home</Button>
          </Link>
        </Card>
      </div>
    );
  }

  const isLoading = isQuestionLoading || !currentQuestion;
  const progressPercent =
    sortedQuestions.length > 0 ? (currentIndex / sortedQuestions.length) * 100 : 0;

  const questionTypeLabel =
    questionType === "multiple"
      ? "Extended Multiple Response"
      : questionType === "ordered"
      ? "Drag & Drop Ordering"
      : null;

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
          {sessionStatus && !sessionStatus.isSubscribed && (
            <div className="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground">
              {Math.min(currentIndex, sessionStatus.freeLimit)} / {sessionStatus.freeLimit} free
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 w-full max-w-3xl mx-auto p-4 sm:p-6 pb-24">
        <div className="mb-6 space-y-2">
          <div className="flex justify-between text-sm font-medium text-muted-foreground">
            <span>Question {currentIndex + 1} of {sortedQuestions.length}</span>
            <span>{currentQuestion?.category || "Loading..."}</span>
          </div>
          <Progress value={progressPercent} className="h-2" />
          {questionTypeLabel && (
            <div className="flex justify-end">
              <span className="text-xs font-semibold px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                {questionTypeLabel}
              </span>
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="space-y-6">
            <Skeleton className="h-32 w-full" />
            <div className="space-y-3">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          </div>
        ) : (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <h2 className="text-xl sm:text-2xl font-semibold leading-relaxed text-foreground mb-8">
              {currentQuestion.text}
            </h2>

            {questionType === "ordered" ? (
              <OrderedQuestion
                items={orderedItems ?? currentQuestion.options}
                setItems={setOrderedItems}
                answerResult={answerResult}
                correctLetter={answerResult?.correctLetter ?? ""}
              />
            ) : questionType === "multiple" ? (
              <MultipleChoice
                options={currentQuestion.options}
                selected={selectedLetters}
                answerResult={answerResult}
                onToggle={handleToggleLetter}
              />
            ) : (
              <SingleChoice
                options={currentQuestion.options}
                selected={selectedLetter}
                answerResult={answerResult}
                onSelect={handleOptionSelect}
              />
            )}

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
                        <>
                          <CheckCircle2 className="w-6 h-6" /> Correct!
                        </>
                      ) : (
                        <>
                          <XCircle className="w-6 h-6" /> Incorrect
                        </>
                      )}
                    </h3>
                    <p className="text-foreground leading-relaxed text-sm sm:text-base">
                      {answerResult.explanation}
                    </p>
                    <div className="mt-6">
                      <Button
                        size="lg"
                        className="w-full sm:w-auto"
                        onClick={handleNext}
                      >
                        {answerResult.canAnswerMore ? "Next Question" : "Continue"}
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
                disabled={!canSubmit || submitAnswer.isPending}
                onClick={handleSubmit}
              >
                {submitAnswer.isPending ? "Submitting..." : "Submit Answer"}
              </Button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
