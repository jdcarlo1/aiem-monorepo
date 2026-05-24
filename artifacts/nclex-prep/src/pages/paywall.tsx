import { Link, useLocation } from "wouter";
import { getSessionId } from "@/lib/session";
import { useCreateCheckout } from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { BookOpen, Check, Lock, ShieldCheck } from "lucide-react";

export default function Paywall() {
  const [, setLocation] = useLocation();
  const sessionId = getSessionId();
  const createCheckout = useCreateCheckout();

  const handleSubscribe = () => {
    createCheckout.mutate({
      data: { sessionId }
    }, {
      onSuccess: () => {
        // Since Stripe is placeholder, it returns a message and we manually route to success
        setLocation("/subscribe-success");
      }
    });
  };

  return (
    <div className="min-h-[100dvh] flex flex-col bg-slate-50 dark:bg-background justify-center p-4 sm:p-8">
      <div className="max-w-md w-full mx-auto">
        <Link href="/" className="inline-flex items-center gap-2 text-primary hover:opacity-80 transition-opacity mb-8 font-semibold">
          <BookOpen className="w-5 h-5" />
          <span>NCLEX Prep</span>
        </Link>
        
        <Card className="border-border shadow-xl rounded-2xl overflow-hidden">
          <div className="bg-primary px-6 py-8 text-primary-foreground text-center">
            <Lock className="w-12 h-12 mx-auto mb-4 opacity-90" />
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight mb-2">
              Unlock Full Access
            </h1>
            <p className="text-primary-foreground/80 font-medium">
              You've used your 5 free questions.
            </p>
          </div>
          
          <CardContent className="p-6 sm:p-8 space-y-6">
            <div className="text-center">
              <div className="flex items-baseline justify-center gap-1">
                <span className="text-5xl font-extrabold text-foreground">$10</span>
                <span className="text-muted-foreground font-medium">/month</span>
              </div>
              <p className="text-sm text-muted-foreground mt-2">Cancel anytime. No hidden fees.</p>
            </div>

            <div className="space-y-3">
              {[
                "Unlimited access to all NCLEX questions",
                "Detailed explanations for every answer",
                "Progress tracking and performance insights",
                "Updated regularly with new questions"
              ].map((feature, i) => (
                <div key={i} className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-primary shrink-0 mt-0.5" />
                  <span className="text-foreground text-sm font-medium">{feature}</span>
                </div>
              ))}
            </div>
          </CardContent>

          <CardFooter className="p-6 sm:p-8 pt-0 flex flex-col gap-4">
            <Button 
              size="lg" 
              className="w-full text-lg py-6 rounded-xl shadow-md"
              onClick={handleSubscribe}
              disabled={createCheckout.isPending}
            >
              {createCheckout.isPending ? "Processing..." : "Subscribe Now"}
            </Button>
            <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground font-medium">
              <ShieldCheck className="w-4 h-4" />
              Secure payment processing
            </div>
          </CardFooter>
        </Card>
      </div>
    </div>
  );
}
