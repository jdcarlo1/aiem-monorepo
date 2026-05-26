import { useState } from "react";
import { Link } from "wouter";
import { useSessionId } from "@/hooks/useSessionId";
import { Button } from "@/components/ui/button";
import { Brain, Check, Lock, ShieldCheck, Zap } from "lucide-react";

const features = [
  "Unlimited NCLEX Prep — 613+ questions with NGN formats",
  "26 Nursing School question banks — 780+ targeted practice questions",
  "Interview Prep — 20 nursing job interview questions",
  "AI-powered clinical explanations after every answer",
  "AI Adaptive Engine — focuses on your weak spots",
  "Updated regularly with new questions",
];

export default function Paywall() {
  const [selectedPlan, setSelectedPlan] = useState<"monthly" | "lifetime">("lifetime");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionId = useSessionId();

  const handleSubscribe = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch("/api/stripe/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, plan: selectedPlan }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(data.error ?? "Something went wrong. Please try again.");
        return;
      }
      if (data.url) {
        window.location.href = data.url;
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background justify-center p-4 sm:p-8">
      <div className="max-w-2xl w-full mx-auto">
        <Link href="/" className="inline-flex items-center gap-2 text-primary hover:opacity-80 transition-opacity mb-8 font-semibold">
          <Brain className="w-5 h-5" />
          <span>NCLEX AI</span>
        </Link>

        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/10 mb-4">
            <Lock className="w-7 h-7 text-primary" />
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight mb-2">Unlock Full Access</h1>
          <p className="text-muted-foreground text-lg">You've used your 5 free questions. Choose a plan to keep going.</p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <button
            onClick={() => setSelectedPlan("monthly")}
            className={`relative rounded-2xl border-2 p-6 text-left transition-all duration-200 ${
              selectedPlan === "monthly"
                ? "border-primary bg-primary/5 shadow-md"
                : "border-border bg-card hover:border-primary/40"
            }`}
          >
            <p className="text-sm font-semibold text-muted-foreground mb-1">Monthly</p>
            <div className="flex items-baseline gap-1 mb-1">
              <span className="text-4xl font-extrabold text-foreground">$15</span>
              <span className="text-muted-foreground font-medium">/month</span>
            </div>
            <p className="text-xs text-muted-foreground">Cancel anytime</p>
            {selectedPlan === "monthly" && (
              <div className="absolute top-3 right-3 w-5 h-5 rounded-full bg-primary flex items-center justify-center">
                <Check className="w-3 h-3 text-primary-foreground" />
              </div>
            )}
          </button>

          <button
            onClick={() => setSelectedPlan("lifetime")}
            className={`relative rounded-2xl border-2 p-6 text-left transition-all duration-200 ${
              selectedPlan === "lifetime"
                ? "border-primary bg-primary/5 shadow-md"
                : "border-border bg-card hover:border-primary/40"
            }`}
          >
            <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary text-primary-foreground text-xs font-bold mb-2">
              <Zap className="w-3 h-3" />
              Most Popular
            </div>
            <p className="text-sm font-semibold text-muted-foreground mb-1">Lifetime</p>
            <div className="flex items-baseline gap-1 mb-1">
              <span className="text-4xl font-extrabold text-foreground">$49</span>
              <span className="text-muted-foreground font-medium">one-time</span>
            </div>
            <p className="text-xs text-muted-foreground">Pay once, access forever</p>
            {selectedPlan === "lifetime" && (
              <div className="absolute top-3 right-3 w-5 h-5 rounded-full bg-primary flex items-center justify-center">
                <Check className="w-3 h-3 text-primary-foreground" />
              </div>
            )}
          </button>
        </div>

        <div className="bg-card border border-border rounded-2xl p-6 mb-6">
          <p className="text-sm font-semibold text-foreground mb-4">Everything included:</p>
          <div className="space-y-3">
            {features.map((feature, i) => (
              <div key={i} className="flex items-start gap-3">
                <Check className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                <span className="text-sm text-foreground">{feature}</span>
              </div>
            ))}
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-sm text-destructive text-center">
            {error}
          </div>
        )}

        <Button
          size="lg"
          className="w-full text-lg py-6 rounded-xl shadow-md mb-3"
          onClick={handleSubscribe}
          disabled={loading}
        >
          {loading
            ? "Redirecting to checkout..."
            : selectedPlan === "lifetime"
            ? "Get Lifetime Access — $49"
            : "Start Monthly Plan — $15/mo"}
        </Button>

        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground font-medium">
          <ShieldCheck className="w-4 h-4" />
          Secure payment via Stripe · 30-day money-back guarantee
        </div>
      </div>
    </div>
  );
}
