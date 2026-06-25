import { useState, useCallback, useEffect, useRef } from "react";
import { Link, useLocation } from "wouter";
import {
  useGetSessionStatus,
  useGetQuestion,
  useSubmitAnswer,
  getGetSessionStatusQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useSessionId } from "@/hooks/useSessionId";
import { useAutoRestore } from "@/hooks/useAutoRestore";
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
import EkgDisplay from "@/components/EkgDisplay";

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
      {options.map((opt, index) => {
        const displayLabel = "ABCDE"[index] ?? opt.letter;
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
                displayLabel
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
      {options.map((opt, index) => {
        const displayLabel = "ABCDE"[index] ?? opt.letter;
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
  const [touchDrag, setTouchDrag] = useState<{ dragIdx: number; overIdx: number } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);
  const touchStateRef = useRef<{ dragIdx: number; overIdx: number } | null>(null);
  const correctOrder = correctLetter.split(",").map(s => s.trim());

  const itemsRef = useRef(items);
  useEffect(() => { itemsRef.current = items; }, [items]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const onTouchMove = (e: TouchEvent) => {
      if (!touchStateRef.current) return;
      e.preventDefault();
      const touch = e.touches[0];
      let hit = touchStateRef.current.overIdx;
      for (let i = 0; i < itemRefs.current.length; i++) {
        const el = itemRefs.current[i];
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (touch.clientY >= rect.top && touch.clientY <= rect.bottom) { hit = i; break; }
      }
      if (hit !== touchStateRef.current.overIdx) {
        touchStateRef.current = { ...touchStateRef.current, overIdx: hit };
        setTouchDrag({ dragIdx: touchStateRef.current.dragIdx, overIdx: hit });
      }
    };

    const onTouchEnd = () => {
      if (touchStateRef.current) {
        const { dragIdx, overIdx } = touchStateRef.current;
        if (dragIdx !== overIdx) {
          const n = [...itemsRef.current];
          const [m] = n.splice(dragIdx, 1);
          n.splice(overIdx, 0, m);
          setItems(n);
        }
      }
      touchStateRef.current = null;
      setTouchDrag(null);
    };

    container.addEventListener("touchmove", onTouchMove, { passive: false });
    container.addEventListener("touchend", onTouchEnd);
    container.addEventListener("touchcancel", onTouchEnd);
    return () => {
      container.removeEventListener("touchmove", onTouchMove);
      container.removeEventListener("touchend", onTouchEnd);
      container.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [setItems]);

  const handleTouchStart = (index: number) => {
    if (answerResult) return;
    touchStateRef.current = { dragIdx: index, overIdx: index };
    setTouchDrag({ dragIdx: index, overIdx: index });
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDragIndex(index); e.dataTransfer.effectAllowed = "move";
  };
  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault(); e.dataTransfer.dropEffect = "move"; setDragOverIndex(index);
  };
  const handleDrop = (e: React.DragEvent, dropIndex: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === dropIndex) { setDragIndex(null); setDragOverIndex(null); return; }
    const n = [...items]; const [m] = n.splice(dragIndex, 1); n.splice(dropIndex, 0, m);
    setItems(n); setDragIndex(null); setDragOverIndex(null);
  };
  const handleDragEnd = () => { setDragIndex(null); setDragOverIndex(null); };

  const moveUp = (i: number) => {
    if (i === 0 || answerResult) return;
    const n = [...items]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setItems(n);
  };
  const moveDown = (i: number) => {
    if (i === items.length - 1 || answerResult) return;
    const n = [...items]; [n[i], n[i + 1]] = [n[i + 1], n[i]]; setItems(n);
  };

  return (
    <div ref={containerRef} className="space-y-2 mb-8">
      <p className="text-sm font-semibold text-primary bg-primary/10 border border-primary/20 rounded-lg px-3 py-2 inline-block mb-3">
        Hold &amp; drag to reorder · or tap ▲▼
      </p>
      {items.map((item, i) => {
        const isCorrectPos = answerResult && correctOrder[i] === item.letter;
        const isDragging = dragIndex === i || touchDrag?.dragIdx === i;
        const isDragOver =
          (dragOverIndex === i && dragIndex !== null && dragIndex !== i) ||
          (touchDrag !== null && touchDrag.overIdx === i && touchDrag.dragIdx !== i);

        let cls = "w-full rounded-xl border-2 flex items-stretch transition-all duration-150 overflow-hidden select-none ";
        if (!answerResult) {
          cls += isDragging
            ? "opacity-40 border-primary bg-primary/5 scale-[0.98]"
            : isDragOver
            ? "border-primary bg-primary/10 scale-[1.01] shadow-md"
            : "border-border bg-card";
        } else {
          cls += isCorrectPos
            ? "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100 cursor-default"
            : "border-destructive/40 bg-destructive/5 text-destructive cursor-default";
        }

        return (
          <div
            key={item.letter}
            ref={(el) => { itemRefs.current[i] = el; }}
            draggable={!answerResult}
            onDragStart={(e) => handleDragStart(e, i)}
            onDragOver={(e) => handleDragOver(e, i)}
            onDrop={(e) => handleDrop(e, i)}
            onDragEnd={handleDragEnd}
            className={cls}
          >
            {!answerResult && (
              <div
                className="flex items-center justify-center w-12 shrink-0 border-r border-border bg-muted/30 cursor-grab active:cursor-grabbing touch-none"
                onTouchStart={() => handleTouchStart(i)}
              >
                <GripVertical className="w-5 h-5 text-muted-foreground" />
              </div>
            )}
            {answerResult && (
              <div className="flex items-center justify-center w-12 shrink-0 border-r border-border">
                {isCorrectPos
                  ? <CheckCircle2 className="w-5 h-5 text-green-500" />
                  : <XCircle className="w-5 h-5 text-destructive/70" />}
              </div>
            )}
            <div className="flex items-center gap-3 px-3 py-3 flex-1 min-w-0">
              <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground shrink-0">
                {i + 1}
              </div>
              <p className="text-base font-medium flex-1 leading-snug">{item.text}</p>
            </div>
            {!answerResult && (
              <div className="flex flex-col shrink-0 border-l border-border">
                <button
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.stopPropagation(); moveUp(i); }}
                  disabled={i === 0}
                  className="flex-1 flex items-center justify-center w-11 text-muted-foreground hover:text-primary hover:bg-primary/5 active:bg-primary/10 disabled:opacity-20 transition-colors"
                  aria-label="Move up"
                >
                  <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M10 17a.75.75 0 01-.75-.75V5.612L5.29 9.77a.75.75 0 01-1.08-1.04l5.25-5.5a.75.75 0 011.08 0l5.25 5.5a.75.75 0 11-1.08 1.04l-3.96-4.158V16.25A.75.75 0 0110 17z" clipRule="evenodd" /></svg>
                </button>
                <div className="h-px bg-border" />
                <button
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.stopPropagation(); moveDown(i); }}
                  disabled={i === items.length - 1}
                  className="flex-1 flex items-center justify-center w-11 text-muted-foreground hover:text-primary hover:bg-primary/5 active:bg-primary/10 disabled:opacity-20 transition-colors"
                  aria-label="Move down"
                >
                  <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z" clipRule="evenodd" /></svg>
                </button>
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

interface CategoryStat {
  category: string;
  total: number;
  correct: number;
  accuracy: number;
}

// ─── Main Quiz ────────────────────────────────────────────────────────────────
export default function Quiz() {
  const [, setLocation] = useLocation();
  const sessionId = useSessionId();
  const queryClient = useQueryClient();

  const [selectedLetter, setSelectedLetter] = useState<string | null>(null);
  const [selectedLetters, setSelectedLetters] = useState<string[]>([]);
  const [orderedItems, setOrderedItems] = useState<{ letter: string; text: string }[] | null>(null);
  const [shuffledOptions, setShuffledOptions] = useState<{ letter: string; text: string }[] | null>(null);
  const [answerResult, setAnswerResult] = useState<AnswerResultState | null>(null);

  // Adaptive engine state
  const [currentQuestionId, setCurrentQuestionId] = useState<number | null>(null);
  const [isFinished, setIsFinished] = useState(false);
  const [isLoadingNext, setIsLoadingNext] = useState(false);
  const [categoryPerformance, setCategoryPerformance] = useState<CategoryStat[]>([]);
  const [totalAnswered, setTotalAnswered] = useState(0);
  const [showPerformance, setShowPerformance] = useState(false);
  const fetchingRef = useRef(false);

  const { data: sessionStatus, isLoading: isSessionLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  useAutoRestore(sessionId, sessionStatus?.canAnswerMore);

  useEffect(() => {
    if (sessionStatus && !sessionStatus.canAnswerMore && !sessionStatus.isSubscribed) {
      setLocation("/paywall");
    }
  }, [sessionStatus, setLocation]);

  // Fetch next adaptive question from the engine
  const fetchNextQuestion = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    setIsLoadingNext(true);
    try {
      const resp = await fetch(`/api/adaptive/next?sessionId=${encodeURIComponent(sessionId)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.questionId === null) {
        setIsFinished(true);
      } else {
        setCurrentQuestionId(data.questionId);
        setCategoryPerformance(data.categoryPerformance ?? []);
        setTotalAnswered(data.totalAnswered ?? 0);
      }
    } finally {
      setIsLoadingNext(false);
      fetchingRef.current = false;
    }
  }, [sessionId]);

  // Load first question once session is ready
  useEffect(() => {
    if (sessionId && sessionStatus?.canAnswerMore && currentQuestionId === null && !isFinished && !fetchingRef.current) {
      fetchNextQuestion();
    }
  }, [sessionId, sessionStatus, currentQuestionId, isFinished, fetchNextQuestion]);

  const { data: currentQuestion, isLoading: isQuestionLoading } = useGetQuestion(
    currentQuestionId ?? 0,
    { query: { enabled: !!currentQuestionId } }
  );

  const questionType = currentQuestion?.questionType ?? "single";

  useEffect(() => {
    if (!currentQuestion) return;
    if (currentQuestion.questionType === "ordered" && !answerResult) {
      const shuffled = [...(currentQuestion.options ?? [])].sort(() => Math.random() - 0.5);
      setOrderedItems(shuffled);
    } else if (currentQuestion.questionType !== "ordered") {
      const shuffled = [...(currentQuestion.options ?? [])].sort(() => Math.random() - 0.5);
      setShuffledOptions(shuffled);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentQuestion?.id]);

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
      { data: { sessionId, questionId: currentQuestion.id, selectedLetter: answer } },
      {
        onSuccess: (result) => {
          setAnswerResult(result);
        },
        onError: () => {
          setLocation("/paywall");
        },
      }
    );
  };

  const handleNext = () => {
    if (answerResult) {
      queryClient.setQueryData(
        getGetSessionStatusQueryKey({ sessionId }),
        (old: any) => {
          if (!old) return old;
          return { ...old, questionsAnswered: answerResult.questionsAnswered, canAnswerMore: answerResult.canAnswerMore };
        }
      );
      if (!answerResult.canAnswerMore && !sessionStatus?.isSubscribed) {
        setLocation("/paywall");
        return;
      }
    }
    setSelectedLetter(null);
    setSelectedLetters([]);
    setOrderedItems(null);
    setShuffledOptions(null);
    setAnswerResult(null);
    setCurrentQuestionId(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // ─── Finished state ───────────────────────────────────────────────────────
  if (isFinished) {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center bg-background p-6">
        <Card className="max-w-md w-full text-center p-8">
          <CheckCircle2 className="w-16 h-16 text-primary mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">You've completed all questions!</h2>
          <p className="text-muted-foreground mb-6">You've answered every question in the bank. More coming soon.</p>
          <Link href="/">
            <Button className="w-full">Return Home</Button>
          </Link>
        </Card>
      </div>
    );
  }

  // ─── Loading state ────────────────────────────────────────────────────────
  if (isSessionLoading || (isLoadingNext && !currentQuestion)) {
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

  const isLoading = isQuestionLoading || !currentQuestion;
  const questionsAnsweredCount = answerResult?.questionsAnswered ?? sessionStatus?.questionsAnswered ?? totalAnswered;

  const questionTypeLabel =
    questionType === "multiple"
      ? "Extended Multiple Response"
      : questionType === "ordered"
      ? "Drag & Drop Ordering"
      : null;

  // Top 3 weakest categories (with at least 1 answer)
  const weakCategories = categoryPerformance.filter(c => c.total > 0).slice(0, 3);

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
          <div className="flex items-center gap-2">
            {categoryPerformance.length > 0 && (
              <button
                onClick={() => setShowPerformance(p => !p)}
                className="text-xs font-semibold px-2.5 py-1 rounded-full bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition-colors"
              >
                📊 Stats
              </button>
            )}
            {sessionStatus && !sessionStatus.isSubscribed && (
              <div className="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground">
                {Math.min(questionsAnsweredCount, sessionStatus.freeLimit)} / {sessionStatus.freeLimit} free
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Adaptive performance panel */}
      {showPerformance && categoryPerformance.length > 0 && (
        <div className="border-b border-border bg-card/50 px-4 py-4">
          <div className="max-w-3xl mx-auto">
            <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-3">
              Your Performance — AI is focusing on your weak areas
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {categoryPerformance.slice(0, 8).map((c) => (
                <div key={c.category} className="flex items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between text-xs mb-0.5">
                      <span className="truncate text-foreground font-medium">{c.category}</span>
                      <span className="text-muted-foreground ml-2 shrink-0">{c.correct}/{c.total}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          c.accuracy >= 0.8 ? "bg-green-500" :
                          c.accuracy >= 0.6 ? "bg-yellow-500" : "bg-red-500"
                        }`}
                        style={{ width: `${Math.round(c.accuracy * 100)}%` }}
                      />
                    </div>
                  </div>
                  <span className={`text-xs font-bold w-8 text-right shrink-0 ${
                    c.accuracy >= 0.8 ? "text-green-600" :
                    c.accuracy >= 0.6 ? "text-yellow-600" : "text-red-600"
                  }`}>
                    {Math.round(c.accuracy * 100)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 w-full max-w-3xl mx-auto p-4 sm:p-6 pb-24">
        <div className="mb-6 space-y-2">
          <div className="flex justify-between text-sm font-medium text-muted-foreground">
            <span>{questionsAnsweredCount} answered · adapting to your performance</span>
            <span>{currentQuestion?.category || "Loading..."}</span>
          </div>
          {questionTypeLabel && (
            <div className="flex justify-start">
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
            {currentQuestion.imageUrl?.startsWith("ekg:") ? (
              <EkgDisplay rhythm={currentQuestion.imageUrl.slice(4)} />
            ) : currentQuestion.imageUrl ? (
              <img
                src={currentQuestion.imageUrl}
                alt="Clinical image"
                className="w-full rounded-xl border border-slate-200 shadow-sm mb-6 object-contain max-h-64"
              />
            ) : null}

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
                options={shuffledOptions ?? currentQuestion.options}
                selected={selectedLetters}
                answerResult={answerResult}
                onToggle={handleToggleLetter}
              />
            ) : (
              <SingleChoice
                options={shuffledOptions ?? currentQuestion.options}
                selected={selectedLetter}
                answerResult={answerResult}
                onSelect={handleOptionSelect}
              />
            )}

            {answerResult ? (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500 space-y-4">
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
                        answerResult.correct ? "text-green-700 dark:text-green-400" : "text-destructive"
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
                    {weakCategories.length > 0 && (
                      <div className="mt-4 pt-4 border-t border-border">
                        <p className="text-xs text-muted-foreground font-semibold mb-2">
                          🎯 AI is drilling your weakest areas:
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {weakCategories.map(c => (
                            <span
                              key={c.category}
                              className="text-xs px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200 font-medium"
                            >
                              {c.category} · {Math.round(c.accuracy * 100)}%
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="mt-6">
                      <Button size="lg" className="w-full sm:w-auto" onClick={handleNext}>
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
