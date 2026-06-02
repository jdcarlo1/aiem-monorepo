import { useEffect, useState } from "react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { CheckCircle, Loader2 } from "lucide-react";
import { useSessionId } from "@/hooks/useSessionId";
import { setPaymentEmail } from "@/lib/session";

export default function SubscribeSuccess() {
  const [verified, setVerified] = useState(false);
  const [verifying, setVerifying] = useState(true);
  const sessionId = useSessionId();

  useEffect(() => {
    if (!sessionId) return;

    const params = new URLSearchParams(window.location.search);
    const checkoutSessionId = params.get("session_id");

    if (!checkoutSessionId) {
      setVerifying(false);
      return;
    }

    fetch("/api/stripe/verify-checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, checkoutSessionId }),
    })
      .then((r) => r.json())
      .then((data) => {
        setVerified(data.isSubscribed === true);
        if (data.email) {
          setPaymentEmail(data.email);
        }
      })
      .catch(() => {})
      .finally(() => setVerifying(false));
  }, [sessionId]);

  return (
    <div className="min-h-[100dvh] flex items-center justify-center bg-background p-6">
      <div className="max-w-md w-full text-center space-y-6">
        {verifying ? (
          <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto" />
        ) : (
          <>
            <div className="w-20 h-20 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-6">
              <CheckCircle className="w-10 h-10 text-green-600 dark:text-green-400" />
            </div>

            <h1 className="text-3xl font-bold tracking-tight text-foreground">
              Subscription Activated!
            </h1>

            <p className="text-lg text-muted-foreground">
              You now have unlimited access to all NCLEX practice questions and detailed explanations.
            </p>

            <div className="pt-6 space-y-3">
              <Link href="/quiz">
                <Button size="lg" className="w-full py-6 text-lg rounded-xl">
                  Start Practicing
                </Button>
              </Link>
              <Link href="/home">
                <Button size="lg" variant="outline" className="w-full py-6 text-lg rounded-xl">
                  Explore All Categories
                </Button>
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
