import { useState, useMemo } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import {
  useGetSessionStatus,
  useGetQuestion,
} from "@workspace/api-client-react";
import { useSessionId } from "@/hooks/useSessionId";
import { useEagerRestore } from "@/hooks/useAutoRestore";
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
  Trophy,
  Target,
  TrendingUp,
  GripVertical,
  BookOpen,
} from "lucide-react";
import EkgDisplay from "@/components/EkgDisplay";

interface LocalAnswerResult {
  correct: boolean;
  correctLetter: string;
  explanation: string;
}

// ─── Single Choice ───────────────────────────────────────────────────────────
function SingleChoice({
  options,
  selected,
  answerResult,
  onSelect,
}: {
  options: { letter: string; text: string }[];
  selected: string | null;
  answerResult: LocalAnswerResult | null;
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

        let cls = "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
        if (!showResult) {
          cls += isSelected
            ? "border-primary bg-primary/5 shadow-sm"
            : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
        } else {
          if (isCorrectAnswer)
            cls += "border-green-500 bg-green-50 text-green-900";
          else if (isWrongSelection)
            cls += "border-destructive/60 bg-destructive/5 text-destructive";
          else cls += "border-border/50 bg-card/50 opacity-50";
        }

        return (
          <button key={opt.letter} onClick={() => onSelect(opt.letter)} disabled={showResult} className={cls}>
            <div className={`w-8 h-8 rounded-full border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
              !showResult && isSelected
                ? "border-primary bg-primary text-primary-foreground"
                : showResult && isCorrectAnswer
                ? "border-green-500 bg-green-500 text-white"
                : showResult && isWrongSelection
                ? "border-destructive bg-destructive text-white"
                : "border-muted-foreground/30 text-muted-foreground"
            }`}>
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

// ─── Multiple Choice (SATA) ───────────────────────────────────────────────────
function MultipleChoice({
  options,
  selected,
  answerResult,
  onToggle,
}: {
  options: { letter: string; text: string }[];
  selected: string[];
  answerResult: LocalAnswerResult | null;
  onToggle: (letter: string) => void;
}) {
  const correctLetters = answerResult?.correctLetter.split(",").map((s) => s.trim()) ?? [];

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

        let cls = "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
        if (!showResult) {
          cls += isSelected
            ? "border-primary bg-primary/5 shadow-sm"
            : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
        } else {
          if (isCorrect && isSelected)
            cls += "border-green-500 bg-green-50 text-green-900";
          else if (isMissed)
            cls += "border-amber-400 bg-amber-50 text-amber-900";
          else if (isWrongSelection)
            cls += "border-destructive/60 bg-destructive/5 text-destructive";
          else cls += "border-border/50 bg-card/50 opacity-50";
        }

        return (
          <button key={opt.letter} onClick={() => onToggle(opt.letter)} disabled={showResult} className={cls}>
            <div className={`w-8 h-8 rounded-lg border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
              !showResult && isSelected
                ? "border-primary bg-primary text-primary-foreground"
                : showResult && isCorrect && isSelected
                ? "border-green-500 bg-green-500 text-white"
                : showResult && isMissed
                ? "border-amber-400 bg-amber-400 text-white"
                : showResult && isWrongSelection
                ? "border-destructive bg-destructive text-white"
                : "border-muted-foreground/30 text-muted-foreground"
            }`}>
              {showResult && isCorrect && isSelected ? <CheckCircle2 className="w-5 h-5" />
                : showResult && isWrongSelection ? <XCircle className="w-5 h-5" />
                : showResult && isMissed ? <CheckCircle2 className="w-5 h-5" />
                : isSelected ? "✓"
                : opt.letter}
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
  answerResult: LocalAnswerResult | null;
  correctLetter: string;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const correctOrder = correctLetter.split(",").map((s) => s.trim());

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
    <div className="space-y-2 mb-8">
      <p className="text-sm font-semibold text-primary bg-primary/10 border border-primary/20 rounded-lg px-3 py-2 inline-block mb-3">
        Drag to arrange in the correct order
      </p>
      {items.map((item, i) => {
        const isCorrectPos = !!answerResult && correctOrder[i] === item.letter;
        const isWrongPos = !!answerResult && correctOrder[i] !== item.letter;
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
            ? "border-green-500 bg-green-50 text-green-900 cursor-default"
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
            {!answerResult && <GripVertical className="w-5 h-5 text-muted-foreground shrink-0" />}
            {answerResult && (isCorrectPos
              ? <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
              : <XCircle className="w-5 h-5 text-destructive/70 shrink-0" />
            )}
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground shrink-0">
              {i + 1}
            </div>
            <p className="text-base font-medium flex-1 leading-snug">{item.text}</p>
            {!answerResult && (
              <div className="flex flex-col gap-1 shrink-0">
                <button onClick={() => moveUp(i)} disabled={i === 0} className="text-muted-foreground hover:text-foreground disabled:opacity-30 text-xs leading-none px-1">▲</button>
                <button onClick={() => moveDown(i)} disabled={i === items.length - 1} className="text-muted-foreground hover:text-foreground disabled:opacity-30 text-xs leading-none px-1">▼</button>
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
              const it = items.find((x) => x.letter === letter);
              return <li key={letter} className="text-foreground">{it?.text}</li>;
            })}
          </ol>
        </div>
      )}
    </div>
  );
}

// ─── Results Screen ──────────────────────────────────────────────────────────
function ResultsScreen({
  category,
  score,
  total,
  onRetry,
  backLink,
  backLabel,
}: {
  category: string;
  score: number;
  total: number;
  onRetry: () => void;
  backLink: string;
  backLabel: string;
}) {
  const pct = total > 0 ? Math.round((score / total) * 100) : 0;
  const passed = pct >= 75;

  const getMessage = () => {
    if (pct === 100) return { text: "Perfect score! Outstanding clinical knowledge.", icon: <Trophy className="w-8 h-8" /> };
    if (pct >= 90) return { text: "Excellent! You have a strong command of this material.", icon: <Trophy className="w-8 h-8" /> };
    if (pct >= 75) return { text: "Passing score! You're on track to pass the NCLEX.", icon: <TrendingUp className="w-8 h-8" /> };
    if (pct >= 60) return { text: "Almost there — a quick review of the missed questions will push you over the passing mark.", icon: <Target className="w-8 h-8" /> };
    return { text: "Keep practicing — each attempt builds the clinical reasoning the NCLEX tests for.", icon: <BookOpen className="w-8 h-8" /> };
  };

  const { text: message, icon } = getMessage();
  const scoreColor = passed ? "text-green-600" : pct >= 60 ? "text-amber-500" : "text-destructive";
  const barColor = passed ? "bg-green-500" : pct >= 60 ? "bg-amber-400" : "bg-destructive";

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href={backLink} className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
            <ChevronLeft className="w-4 h-4 mr-1" />{backLabel}
          </Link>
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm font-bold text-primary">NCLEX AI</span>
          </div>
          <div className="w-24" />
        </div>
      </header>

      <div className="flex-1 flex items-center justify-center p-6">
        <div className="max-w-md w-full mx-auto">
          {/* Score card */}
          <div className="rounded-3xl border-2 border-border bg-card shadow-lg overflow-hidden mb-5">
            {/* Header band */}
            <div className={`px-6 py-4 ${passed ? "bg-green-50 border-b border-green-100" : pct >= 60 ? "bg-amber-50 border-b border-amber-100" : "bg-red-50 border-b border-red-100"}`}>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-bold uppercase tracking-widest text-muted-foreground mb-0.5">Section Complete</p>
                  <p className="font-semibold text-foreground text-sm leading-snug">{category}</p>
                </div>
                <div className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-bold border ${
                  passed
                    ? "bg-green-100 text-green-700 border-green-200"
                    : pct >= 60
                    ? "bg-amber-100 text-amber-700 border-amber-200"
                    : "bg-red-100 text-red-700 border-red-200"
                }`}>
                  {passed ? "✓ Passing" : "✗ Not Passing"}
                </div>
              </div>
            </div>

            {/* Score */}
            <div className="px-6 pt-8 pb-6 text-center">
              <div className={`text-7xl font-black tracking-tighter mb-1 ${scoreColor}`}>
                {pct}<span className="text-3xl font-bold text-muted-foreground">%</span>
              </div>
              <p className="text-muted-foreground text-sm mb-6">
                {score} correct out of {total} questions
              </p>

              {/* Score bar */}
              <div className="w-full bg-muted rounded-full h-3 mb-2 overflow-hidden">
                <div
                  className={`h-3 rounded-full transition-all duration-1000 ${barColor}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground mb-6">
                <span>0%</span>
                <span className={`font-semibold ${passed ? "text-green-600" : "text-muted-foreground"}`}>75% passing</span>
                <span>100%</span>
              </div>

              {/* Stats row */}
              <div className="grid grid-cols-3 gap-3 mb-6">
                <div className="rounded-xl bg-green-50 border border-green-100 p-3">
                  <div className="text-xl font-bold text-green-600">{score}</div>
                  <div className="text-xs text-green-700 font-medium">Correct</div>
                </div>
                <div className="rounded-xl bg-red-50 border border-red-100 p-3">
                  <div className="text-xl font-bold text-red-500">{total - score}</div>
                  <div className="text-xs text-red-600 font-medium">Missed</div>
                </div>
                <div className="rounded-xl bg-muted border border-border p-3">
                  <div className="text-xl font-bold text-foreground">{total}</div>
                  <div className="text-xs text-muted-foreground font-medium">Total</div>
                </div>
              </div>

              {/* Message */}
              <div className={`flex items-start gap-3 p-4 rounded-2xl text-left ${
                passed ? "bg-green-50 border border-green-100" : pct >= 60 ? "bg-amber-50 border border-amber-100" : "bg-muted border border-border"
              }`}>
                <div className={`shrink-0 mt-0.5 ${passed ? "text-green-600" : pct >= 60 ? "text-amber-600" : "text-muted-foreground"}`}>
                  {icon}
                </div>
                <p className={`text-sm font-medium leading-relaxed ${passed ? "text-green-800" : pct >= 60 ? "text-amber-800" : "text-foreground"}`}>
                  {message}
                </p>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-3">
            <Button size="lg" className="w-full rounded-xl" onClick={onRetry}>
              <RotateCcw className="w-4 h-4 mr-2" />
              Retry This Section
            </Button>
            <Link href={backLink}>
              <Button variant="outline" size="lg" className="w-full rounded-xl">
                Choose Another Section
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main ────────────────────────────────────────────────────────────────────
export default function StudyQuiz() {
  const [, setLocation] = useLocation();
  const sessionId = useSessionId();

  const category = new URLSearchParams(window.location.search).get("category") ?? "";

  const [currentIndex, setCurrentIndex] = useState(0);

  // single choice
  const [selectedLetter, setSelectedLetter] = useState<string | null>(null);
  // multiple choice (SATA)
  const [selectedLetters, setSelectedLetters] = useState<string[]>([]);
  // ordered / drag-and-drop
  const [orderedItems, setOrderedItems] = useState<{ letter: string; text: string }[] | null>(null);

  const [answerResult, setAnswerResult] = useState<LocalAnswerResult | null>(null);
  const [completed, setCompleted] = useState(false);
  const [score, setScore] = useState({ correct: 0, total: 0 });

  const { data: sessionStatus, isLoading: isSessionLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  useEagerRestore(sessionId, sessionStatus?.isSubscribed);

  const { data: filteredQuestions = [], isLoading: isListLoading } = useQuery({
    queryKey: ["questions", "category", category],
    queryFn: async () => {
      const resp = await fetch(`/api/questions?category=${encodeURIComponent(category)}`);
      return resp.json() as Promise<{ id: number; questionNumber: number; category: string }[]>;
    },
    enabled: !!category,
  });

  const currentQuestionSummary = filteredQuestions[currentIndex];

  const { data: currentQuestion, isLoading: isQuestionLoading } = useGetQuestion(
    currentQuestionSummary?.id ?? 0,
    { query: { enabled: !!currentQuestionSummary?.id } }
  );

  // Initialize orderedItems when a new ordered question loads
  const questionKey = currentQuestion?.id;
  useMemo(() => {
    if (currentQuestion?.questionType === "ordered" && !answerResult) {
      const shuffled = [...(currentQuestion.options ?? [])].sort(() => Math.random() - 0.5);
      setOrderedItems(shuffled);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionKey]);

  const questionType = currentQuestion?.questionType ?? "single";

  const getSubmittedAnswer = () => {
    if (questionType === "multiple") return [...selectedLetters].sort().join(",");
    if (questionType === "ordered") {
      if (!orderedItems) return null;
      return orderedItems.map((it) => it.letter).join(",");
    }
    return selectedLetter;
  };

  const canSubmit = () => {
    if (questionType === "multiple") return selectedLetters.length > 0;
    if (questionType === "ordered") return !!orderedItems;
    return !!selectedLetter;
  };

  const handleToggleMultiple = (letter: string) => {
    if (answerResult) return;
    setSelectedLetters((prev) =>
      prev.includes(letter) ? prev.filter((l) => l !== letter) : [...prev, letter]
    );
  };

  const handleSubmit = () => {
    if (!currentQuestion) return;
    const submitted = getSubmittedAnswer();
    if (!submitted) return;

    const correct =
      [...submitted.split(",").map((s) => s.trim()).sort()].join(",") ===
      [...currentQuestion.correctLetter.split(",").map((s) => s.trim()).sort()].join(",");

    setAnswerResult({
      correct,
      correctLetter: currentQuestion.correctLetter,
      explanation: currentQuestion.explanation,
    });
    setScore((s) => ({ correct: s.correct + (correct ? 1 : 0), total: s.total + 1 }));
  };

  const handleNext = () => {
    if (currentIndex + 1 >= filteredQuestions.length) {
      setCompleted(true);
    } else {
      setCurrentIndex((i) => i + 1);
    }
    setSelectedLetter(null);
    setSelectedLetters([]);
    setOrderedItems(null);
    setAnswerResult(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleRestart = () => {
    setCurrentIndex(0);
    setSelectedLetter(null);
    setSelectedLetters([]);
    setOrderedItems(null);
    setAnswerResult(null);
    setCompleted(false);
    setScore({ correct: 0, total: 0 });
  };

  const backLink = "/nursing-school";
  const backLabel = "Nursing School";

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
      <div className="min-h-[100dvh] flex flex-col bg-background">
        <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
          <div className="max-w-3xl mx-auto flex items-center justify-between">
            <Link href={backLink} className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
              <ChevronLeft className="w-4 h-4 mr-1" />{backLabel}
            </Link>
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-primary" />
              <span className="text-sm font-bold text-primary">NCLEX AI</span>
            </div>
            <div className="w-24" />
          </div>
        </header>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="max-w-md w-full mx-auto text-center">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-6">
              <Lock className="w-8 h-8 text-primary" />
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight mb-3">
              {category || "This Question Bank"} is a Premium Feature
            </h1>
            <p className="text-muted-foreground mb-8 leading-relaxed">
              Unlock all nursing school question banks, NCLEX prep, and interview prep with a one-time $49 lifetime plan.
            </p>
            <div className="flex flex-col gap-3">
              <Button size="lg" className="rounded-xl w-full" onClick={() => setLocation("/paywall")}>
                Unlock Lifetime Access — $49
                <ArrowRight className="w-4 h-4 ml-2" />
              </Button>
              <Link href={backLink}>
                <Button variant="ghost" className="w-full">Back to Nursing School</Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!isListLoading && filteredQuestions.length === 0) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background">
        <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
          <div className="max-w-3xl mx-auto flex items-center justify-between">
            <Link href={backLink} className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
              <ChevronLeft className="w-4 h-4 mr-1" />{backLabel}
            </Link>
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-primary" />
              <span className="text-sm font-bold text-primary">NCLEX AI</span>
            </div>
            <div className="w-24" />
          </div>
        </header>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="text-center">
            <p className="text-muted-foreground">No questions found for "{category}".</p>
            <Link href={backLink}><Button className="mt-4">Back to Nursing School</Button></Link>
          </div>
        </div>
      </div>
    );
  }

  if (completed) {
    return (
      <ResultsScreen
        category={category}
        score={score.correct}
        total={score.total}
        onRetry={handleRestart}
        backLink={backLink}
        backLabel={backLabel}
      />
    );
  }

  const isLoading = isQuestionLoading || !currentQuestion;
  const progressPercent = filteredQuestions.length > 0 ? (currentIndex / filteredQuestions.length) * 100 : 0;

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href={backLink} className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
            <ChevronLeft className="w-4 h-4 mr-1" />{backLabel}
          </Link>
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm font-bold text-primary">NCLEX AI</span>
          </div>
          <div className="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground">
            {currentIndex + 1} / {filteredQuestions.length}
          </div>
        </div>
      </header>

      <main className="flex-1 w-full max-w-3xl mx-auto p-4 sm:p-6 pb-24">
        <div className="mb-6 space-y-2">
          <div className="flex justify-between text-sm font-medium text-muted-foreground">
            <span>Question {currentIndex + 1} of {filteredQuestions.length}</span>
            <span>{category}</span>
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
                correctLetter={currentQuestion.correctLetter}
              />
            ) : questionType === "multiple" ? (
              <MultipleChoice
                options={currentQuestion.options}
                selected={selectedLetters}
                answerResult={answerResult}
                onToggle={handleToggleMultiple}
              />
            ) : (
              <SingleChoice
                options={currentQuestion.options}
                selected={selectedLetter}
                answerResult={answerResult}
                onSelect={(l) => { if (!answerResult) setSelectedLetter(l); }}
              />
            )}

            {answerResult ? (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500">
                <Card className={`border-2 ${answerResult.correct
                  ? "border-green-200 bg-green-50/50"
                  : "border-red-200 bg-red-50/50"}`}>
                  <CardContent className="p-6">
                    <h3 className={`text-lg font-bold flex items-center gap-2 mb-3 ${answerResult.correct ? "text-green-700" : "text-destructive"}`}>
                      {answerResult.correct
                        ? <><CheckCircle2 className="w-6 h-6" /> Correct!</>
                        : <><XCircle className="w-6 h-6" /> Incorrect</>}
                    </h3>
                    <p className="text-foreground leading-relaxed text-sm sm:text-base">
                      {answerResult.explanation}
                    </p>
                    <div className="mt-6">
                      <Button size="lg" className="w-full sm:w-auto" onClick={handleNext}>
                        {currentIndex + 1 >= filteredQuestions.length ? "See Results" : "Next Question"}
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
                disabled={!canSubmit()}
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
