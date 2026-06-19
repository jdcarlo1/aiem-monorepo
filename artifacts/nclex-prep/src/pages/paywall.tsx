import { useState, useEffect } from "react";
import { Link, useLocation, useSearch } from "wouter";
import { useSessionId } from "@/hooks/useSessionId";
import { getPaymentEmail, setPaymentEmail } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { Brain, Check, Lock, ShieldCheck, Zap, Loader2, Tag } from "lucide-react";

const features = [
  "Unlimited NCLEX Prep — 2,000+ questions with NGN (Next Generation NCLEX Test) formats",
  "48 Nursing School question banks — 1,380+ targeted practice questions",
  "Interview Prep — 20 nursing job interview questions",
  "AI-powered clinical explanations after every answer",
  "AI Adaptive Engine — focuses on your weak spots",
  "Updated regularly with new questions",
];

export default function Paywall() {
  const [selectedPlan, setSelectedPlan] = useState<"monthly" | "lifetime">("lifetime");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRestore, setShowRestore] = useState(false);
  const [restoreEmail, setRestoreEmail] = useState("");
  const [restoreLoading, setRestoreLoading] = useState(false);
  const [restoreMsg, setRestoreMsg] = useState<string | null>(null);
  const [autoRestoring, setAutoRestoring] = useState(false);
  const [referralCode, setReferralCode] = useState("");
  const [referralValid, setReferralValid] = useState<boolean | null>(null);
  const sessionId = useSessionId();
  const [, setLocation] = useLocation();
  const search = useSearch();

  // Auto-fill referral code from URL ?ref=CODE
  useEffect(() => {
    const params = new URLSearchParams(search);
    const ref = params.get("ref");
    if (ref) {
      setReferralCode(ref.toUpperCase());
    }
  }, [search]);

  // Auto-restore on mount if we have a stored payment email
  useEffect(() => {
    if (!sessionId) return;
    const email = getPaymentEmail();
    if (!email) return;

    setAutoRestoring(true);
    fetch("/api/stripe/restore-access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, email }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          setPaymentEmail(email);
          setLocation("/quiz");
        } else {
          setAutoRestoring(false);
        }
      })
      .catch(() => {
        setAutoRestoring(false);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handleRestore = async () => {
    if (!restoreEmail || !sessionId) return;
    setRestoreLoading(true);
    setRestoreMsg(null);
    try {
      const resp = await fetch("/api/stripe/restore-access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, email: restoreEmail }),
      });
      const data = await resp.json();
      if (data.success) {
        setPaymentEmail(restoreEmail.trim().toLowerCase());
        setRestoreMsg("✅ Access restored! Redirecting...");
        setTimeout(() => setLocation("/quiz"), 1500);
      } else {
        setRestoreMsg(data.message ?? "No payment found for that email.");
      }
    } catch {
      setRestoreMsg("Network error. Please try again.");
    } finally {
      setRestoreLoading(false);
    }
  };

  const handleSubscribe = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch("/api/stripe/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId,
          plan: selectedPlan,
          ...(referralCode.trim() ? { referralCode: referralCode.trim() } : {}),
        }),
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

  if (autoRestoring) {
    return (
      <div className="min-h-[100dvh] flex flex-col items-center justify-center bg-background gap-4">
        <Loader2 className="w-10 h-10 text-primary animate-spin" />
        <p className="text-lg font-semibold text-foreground">Checking your membership...</p>
        <p className="text-sm text-muted-foreground">Just a moment</p>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background justify-center p-4 sm:p-8">
      <div className="max-w-2xl w-full mx-auto">
        <Link href="/" className="inline-flex items-center gap-2 text-primary hover:opacity-80 transition-opacity mb-8 font-semibold">
          <Brain className="w-5 h-5" />
          <span>NCLEX AI</span>
        </Link>

        {/* Urgency banner */}
        <div className="bg-amber-50 border border-amber-300 rounded-2xl px-5 py-4 mb-6 text-center">
          <p className="text-amber-900 font-bold text-sm">⏰ You've seen what the real NGN looks like.</p>
          <p className="text-amber-800 text-sm mt-1">2,768 more questions are waiting — including every NGN format the real exam uses.</p>
        </div>

        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-primary/10 mb-4">
            <Lock className="w-7 h-7 text-primary" />
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight mb-2">Don't Stop Now</h1>
          <p className="text-muted-foreground text-lg">You've used your 10 free questions. Most students who pass practice <strong>daily</strong> — keep your momentum going.</p>
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
              <span className="text-4xl font-extrabold text-foreground">$20</span>
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
              <span className="text-4xl font-extrabold text-foreground">$100</span>
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

        {/* Referral code — always visible */}
        <div className="mb-4">
          <label className="text-xs text-muted-foreground font-medium flex items-center gap-1 mb-1.5">
            <Tag className="w-3 h-3" /> Referral code <span className="text-muted-foreground/60">(optional)</span>
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="e.g. JOHN50"
              value={referralCode}
              onChange={e => {
                setReferralCode(e.target.value.toUpperCase());
                setReferralValid(null);
              }}
              className="flex-1 px-4 py-2.5 rounded-xl border border-border bg-background text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary"
            />
            {referralCode && (
              <span className="text-xs px-2 py-1 rounded-lg bg-primary/10 text-primary font-semibold">
                Applied ✓
              </span>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-muted-foreground mb-3">⭐⭐⭐⭐⭐ &nbsp;"The questions looked identical to what I saw on test day." — Sarah M., BSN · Florida</p>

        <Button
          size="lg"
          className="w-full text-lg py-6 rounded-xl shadow-md mb-3"
          onClick={handleSubscribe}
          disabled={loading}
        >
          {loading
            ? "Redirecting to checkout..."
            : selectedPlan === "lifetime"
            ? "Get Lifetime Access — $100"
            : "Start Monthly Plan — $20/mo"}
        </Button>

        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground font-medium">
          <ShieldCheck className="w-4 h-4" />
          Secure payment via Stripe · 30-day money-back guarantee
        </div>

        <div className="mt-6 pt-6 border-t border-border text-center">
          {!showRestore ? (
            <button
              onClick={() => setShowRestore(true)}
              className="text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
            >
              Already subscribed? Restore access
            </button>
          ) : (
            <div className="space-y-3">
              <p className="text-sm font-medium text-foreground">Enter the email you used to pay:</p>
              <input
                type="email"
                value={restoreEmail}
                onChange={(e) => setRestoreEmail(e.target.value)}
                placeholder="your@email.com"
                className="w-full px-4 py-3 rounded-xl border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
              <Button
                onClick={handleRestore}
                disabled={restoreLoading || !restoreEmail}
                className="w-full rounded-xl"
                variant="outline"
              >
                {restoreLoading ? "Checking..." : "Restore Access"}
              </Button>
              {restoreMsg && (
                <p className={`text-sm text-center ${restoreMsg.startsWith("✅") ? "text-green-600" : "text-destructive"}`}>
                  {restoreMsg}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
