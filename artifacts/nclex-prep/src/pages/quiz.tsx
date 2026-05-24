import { useState, useMemo } from "react";
import { Link, useLocation } from "wouter";
import { 
  useGetSessionStatus, 
  useListQuestions, 
  useGetQuestion, 
  useSubmitAnswer,
  getGetSessionStatusQueryKey
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { getSessionId } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ChevronLeft, ArrowRight, CheckCircle2, XCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

export default function Quiz() {
  const [, setLocation] = useLocation();
  const sessionId = getSessionId();
  const queryClient = useQueryClient();
  
  const [selectedLetter, setSelectedLetter] = useState<string | null>(null);
  const [answerResult, setAnswerResult] = useState<{
    correct: boolean;
    correctLetter: string;
    explanation: string;
    canAnswerMore: boolean;
  } | null>(null);

  const { data: sessionStatus, isLoading: isSessionLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  const { data: questionsList, isLoading: isListLoading } = useListQuestions();

  const sortedQuestions = useMemo(() => {
    if (!questionsList) return [];
    return [...questionsList].sort((a, b) => a.questionNumber - b.questionNumber);
  }, [questionsList]);

  // Determine current question index
  const currentIndex = sessionStatus ? sessionStatus.questionsAnswered : 0;
  const currentQuestionSummary = sortedQuestions[currentIndex];
  const isFinished = sortedQuestions.length > 0 && currentIndex >= sortedQuestions.length;

  const { data: currentQuestion, isLoading: isQuestionLoading } = useGetQuestion(
    currentQuestionSummary?.id ?? 0,
    { query: { enabled: !!currentQuestionSummary?.id } }
  );

  const submitAnswer = useSubmitAnswer();

  const handleOptionSelect = (letter: string) => {
    if (answerResult || submitAnswer.isPending) return;
    setSelectedLetter(letter);
  };

  const handleSubmit = () => {
    if (!selectedLetter || !currentQuestion) return;

    submitAnswer.mutate({
      data: {
        sessionId,
        questionId: currentQuestion.id,
        selectedLetter
      }
    }, {
      onSuccess: (result) => {
        setAnswerResult(result);
        // Do NOT update the session cache here — updating questionsAnswered now
        // would cause currentIndex to advance, swapping in the next question's
        // text while this question's explanation is still displayed.
        // The cache is updated in handleNext, after the user moves on.
      }
    });
  };

  const handleNext = () => {
    if (answerResult && !answerResult.canAnswerMore) {
      setLocation("/paywall");
      return;
    }
    // Now that the user is moving on, update the session cache so currentIndex advances.
    if (answerResult) {
      queryClient.setQueryData(getGetSessionStatusQueryKey({ sessionId }), (old: any) => {
        if (!old) return old;
        return {
          ...old,
          questionsAnswered: answerResult.questionsAnswered,
          canAnswerMore: answerResult.canAnswerMore
        };
      });
    }
    setSelectedLetter(null);
    setAnswerResult(null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (isSessionLoading || isListLoading) {
    return (
      <div className="min-h-[100dvh] flex flex-col bg-background p-6">
        <Skeleton className="h-10 w-full max-w-3xl mx-auto mb-8" />
        <Skeleton className="h-64 w-full max-w-3xl mx-auto mb-6" />
        <div className="space-y-4 max-w-3xl mx-auto w-full">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
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
  const progressPercent = sortedQuestions.length > 0 ? (currentIndex / sortedQuestions.length) * 100 : 0;

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-4 py-3 border-b border-border bg-card sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <Link href="/" className="inline-flex items-center text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">
            <ChevronLeft className="w-4 h-4 mr-1" />
            Home
          </Link>
          {sessionStatus && !sessionStatus.isSubscribed && (
            <div className="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground">
              {Math.min(currentIndex, sessionStatus.freeLimit)} of {sessionStatus.freeLimit} free questions used
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
        </div>

        {isLoading ? (
          <div className="space-y-6">
            <Skeleton className="h-32 w-full" />
            <div className="space-y-3">
              {[1,2,3,4].map(i => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          </div>
        ) : (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <h2 className="text-xl sm:text-2xl font-semibold leading-relaxed text-foreground mb-8">
              {currentQuestion.text}
            </h2>

            <div className="space-y-3 mb-8">
              {currentQuestion.options.map((opt) => {
                const isSelected = selectedLetter === opt.letter;
                const showResult = !!answerResult;
                const isCorrectAnswer = answerResult?.correctLetter === opt.letter;
                const isWrongSelection = showResult && isSelected && !answerResult.correct;
                
                let optionClasses = "w-full p-4 rounded-xl border-2 text-left transition-all duration-200 flex items-start gap-4 ";
                
                if (!showResult) {
                  optionClasses += isSelected 
                    ? "border-primary bg-primary/5 shadow-sm" 
                    : "border-border hover:border-primary/40 hover:bg-secondary/50 bg-card";
                } else {
                  if (isCorrectAnswer) {
                    optionClasses += "border-green-500 bg-green-50 text-green-900 dark:bg-green-950/30 dark:text-green-100";
                  } else if (isWrongSelection) {
                    optionClasses += "border-destructive/60 bg-destructive/5 text-destructive";
                  } else {
                    optionClasses += "border-border/50 bg-card/50 opacity-50";
                  }
                }

                return (
                  <button
                    key={opt.letter}
                    onClick={() => handleOptionSelect(opt.letter)}
                    disabled={showResult || submitAnswer.isPending}
                    className={optionClasses}
                  >
                    <div className={`w-8 h-8 rounded-full border-2 flex items-center justify-center shrink-0 font-semibold text-sm ${
                      !showResult && isSelected ? "border-primary bg-primary text-primary-foreground" : 
                      showResult && isCorrectAnswer ? "border-green-500 bg-green-500 text-white" :
                      showResult && isWrongSelection ? "border-destructive bg-destructive text-white" :
                      "border-muted-foreground/30 text-muted-foreground"
                    }`}>
                      {showResult && isCorrectAnswer ? <CheckCircle2 className="w-5 h-5" /> : 
                       showResult && isWrongSelection ? <XCircle className="w-5 h-5" /> :
                       opt.letter}
                    </div>
                    <div className="pt-1 text-base font-medium leading-snug">
                      {opt.text}
                    </div>
                  </button>
                );
              })}
            </div>

            {answerResult ? (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500">
                <Card className={`border-2 ${answerResult.correct ? "border-green-200 bg-green-50/50 dark:border-green-900/50 dark:bg-green-900/10" : "border-red-200 bg-red-50/50 dark:border-red-900/50 dark:bg-red-900/10"}`}>
                  <CardContent className="p-6">
                    <h3 className={`text-lg font-bold flex items-center gap-2 mb-3 ${answerResult.correct ? "text-green-700 dark:text-green-400" : "text-destructive"}`}>
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
                disabled={!selectedLetter || submitAnswer.isPending}
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
