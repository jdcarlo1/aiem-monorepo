import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { useGetSessionStatus } from "@workspace/api-client-react";
import { getSessionId } from "@/lib/session";
import { BookOpen, CheckCircle, Clock } from "lucide-react";

export default function Home() {
  const sessionId = getSessionId();
  const { data: sessionStatus, isLoading } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-6 py-4 border-b border-border bg-card">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-xl font-bold tracking-tight text-foreground">NCLEX Prep</span>
          </div>
          {sessionStatus && (
            <div className="text-sm font-medium text-muted-foreground">
              {sessionStatus.isSubscribed ? "Premium" : "Free Tier"}
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center p-6 max-w-3xl mx-auto w-full text-center">
        <div className="space-y-6 mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary text-secondary-foreground text-sm font-medium mx-auto">
            <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            Focused, confidence-building study
          </div>
          
          <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight text-foreground leading-tight">
            Master the NCLEX with <span className="text-primary">Clinical Confidence</span>
          </h1>
          
          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto">
            Like having a knowledgeable study partner. Practice with high-quality questions, get detailed explanations, and build your confidence for test day.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full mb-12">
          <div className="p-6 rounded-2xl bg-card border border-border shadow-sm text-left">
            <CheckCircle className="w-8 h-8 text-primary mb-4" />
            <h3 className="font-semibold text-lg mb-2">Targeted Practice</h3>
            <p className="text-muted-foreground text-sm">Questions designed to match the actual NCLEX difficulty and format.</p>
          </div>
          <div className="p-6 rounded-2xl bg-card border border-border shadow-sm text-left">
            <BookOpen className="w-8 h-8 text-primary mb-4" />
            <h3 className="font-semibold text-lg mb-2">Deep Explanations</h3>
            <p className="text-muted-foreground text-sm">Understand the 'why' behind every correct and incorrect option.</p>
          </div>
          <div className="p-6 rounded-2xl bg-card border border-border shadow-sm text-left">
            <Clock className="w-8 h-8 text-primary mb-4" />
            <h3 className="font-semibold text-lg mb-2">Track Progress</h3>
            <p className="text-muted-foreground text-sm">Watch your skills improve as you work through our question bank.</p>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-center gap-4">
          <Link href="/quiz">
            <Button size="lg" className="w-full sm:w-auto text-lg px-8 py-6 rounded-full shadow-md">
              Start Practicing Now
            </Button>
          </Link>
          {(!sessionStatus || !sessionStatus.isSubscribed) && (
            <p className="text-sm text-muted-foreground">
              Includes 5 free questions. <br className="sm:hidden" />
              Then just $10/month.
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
