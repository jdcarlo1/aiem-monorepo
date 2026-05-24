import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { useGetSessionStatus } from "@workspace/api-client-react";
import { getSessionId } from "@/lib/session";
import {
  Brain,
  CheckCircle,
  XCircle,
  Zap,
  Star,
  ArrowRight,
  GripVertical,
  SquareCheck,
  Sparkles,
  ShieldCheck,
  TrendingUp,
  Clock,
  Users,
} from "lucide-react";

const testimonials = [
  {
    name: "Maria S., RN",
    school: "University of Florida",
    quote:
      "I failed once with traditional prep books. Switched to NCLEX AI and passed on my second attempt. The NGN questions and AI explanations are exactly what the new exam tests — I finally understood clinical judgment, not just memorization.",
    stars: 5,
    tag: "Passed 2nd attempt",
  },
  {
    name: "James T., BSN",
    school: "Texas A&M Nursing",
    quote:
      "The extended multiple-response and drag-and-drop questions blew me away. They look identical to what I saw on test day. Other apps had zero NGN format questions. This is the only tool that actually prepares you for the new NCLEX.",
    stars: 5,
    tag: "1st attempt pass",
  },
  {
    name: "Ashley R., RN",
    school: "Ohio State University",
    quote:
      "What sets this apart is the AI explanation after each question. It doesn't just say you're wrong — it walks you through the exact clinical reasoning you need. I felt so confident walking into the testing center.",
    stars: 5,
    tag: "Passed 1st attempt",
  },
  {
    name: "David K., BSN-RN",
    school: "UCLA School of Nursing",
    quote:
      "27 categories covering every clinical area I needed. The burn unit, ICU, and maternity questions were detailed and realistic. For $10/month vs. $150 for prep books? This is a no-brainer for any nursing student.",
    stars: 5,
    tag: "1st attempt pass",
  },
];

const comparisonRows = [
  { feature: "NGN-formatted questions", us: true, them: false },
  { feature: "Extended multiple response", us: true, them: false },
  { feature: "Drag & drop ordering questions", us: true, them: false },
  { feature: "AI-powered clinical explanations", us: true, them: false },
  { feature: "540+ questions across 27 categories", us: true, them: "Limited" },
  { feature: "Instant answer feedback", us: true, them: false },
  { feature: "Available 24/7 on any device", us: true, them: true },
  { feature: "Cost", us: "$10/month", them: "$50–$200+" },
];

export default function Home() {
  const sessionId = getSessionId();
  const { data: sessionStatus } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="px-6 py-4 border-b border-border bg-card/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl bg-primary flex items-center justify-center shadow-md">
              <Brain className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-xl font-bold tracking-tight text-foreground">NCLEX<span className="text-primary"> AI</span></span>
          </div>
          <div className="flex items-center gap-4">
            {sessionStatus && (
              <span className="text-sm font-medium text-muted-foreground hidden sm:block">
                {sessionStatus.isSubscribed ? "✦ Premium" : "Free Tier"}
              </span>
            )}
            <Link href="/quiz">
              <Button size="sm" variant="outline" className="rounded-full">
                Start Practicing
              </Button>
            </Link>
          </div>
        </div>
      </header>

      <main className="flex-1 flex flex-col">
        {/* Hero */}
        <section className="relative flex flex-col items-center justify-center text-center px-6 py-20 md:py-32 overflow-hidden">
          <div className="absolute inset-0 -z-10 bg-gradient-to-br from-primary/5 via-background to-background" />
          <div className="absolute top-20 left-1/4 w-72 h-72 bg-primary/5 rounded-full blur-3xl -z-10" />
          <div className="absolute bottom-10 right-1/4 w-96 h-96 bg-primary/5 rounded-full blur-3xl -z-10" />

          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-primary/10 text-primary text-sm font-semibold mb-6 border border-primary/20">
            <Sparkles className="w-3.5 h-3.5" />
            AI-Powered NCLEX Prep — NGN Ready
          </div>

          <h1 className="text-4xl md:text-6xl lg:text-7xl font-extrabold tracking-tight text-foreground leading-tight max-w-4xl mx-auto mb-6">
            The Smarter Way to
            <br />
            <span className="text-primary">Pass Your NCLEX</span>
          </h1>

          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 leading-relaxed">
            AI-powered explanations, NGN question formats, extended multiple response, and drag-and-drop ordering — everything the new NCLEX tests, all in one place.
          </p>

          <div className="flex flex-col sm:flex-row items-center gap-4 mb-12">
            <Link href="/quiz">
              <Button size="lg" className="text-lg px-10 py-6 rounded-full shadow-lg hover:shadow-primary/25 hover:scale-105 transition-all duration-200">
                Start Free Practice
                <ArrowRight className="w-5 h-5 ml-2" />
              </Button>
            </Link>
            {(!sessionStatus || !sessionStatus.isSubscribed) && (
              <p className="text-sm text-muted-foreground">
                5 free questions · Then $10/month
              </p>
            )}
          </div>

          <div className="flex flex-wrap justify-center gap-6 text-sm font-medium text-muted-foreground">
            {[
              { icon: <Zap className="w-4 h-4 text-primary" />, label: "540+ Questions" },
              { icon: <ShieldCheck className="w-4 h-4 text-primary" />, label: "27 Clinical Categories" },
              { icon: <Brain className="w-4 h-4 text-primary" />, label: "AI Explanations" },
              { icon: <TrendingUp className="w-4 h-4 text-primary" />, label: "NGN Question Formats" },
            ].map((stat) => (
              <div key={stat.label} className="flex items-center gap-1.5">
                {stat.icon}
                <span>{stat.label}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Features */}
        <section className="px-6 py-16 max-w-6xl mx-auto w-full">
          <div className="text-center mb-12">
            <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
              Built for the <span className="text-primary">New NCLEX</span>
            </h2>
            <p className="text-muted-foreground text-lg max-w-xl mx-auto">
              The NCLEX changed. Most prep tools didn't. We did.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {[
              {
                icon: <Brain className="w-7 h-7 text-primary" />,
                title: "AI-Powered Explanations",
                desc: "Every answer comes with a detailed clinical rationale — not just 'correct' or 'wrong', but the reasoning that builds real judgment.",
              },
              {
                icon: <SquareCheck className="w-7 h-7 text-primary" />,
                title: "Extended Multiple Response",
                desc: "Select all that apply — the NGN format that trips most students. Practice until it feels natural.",
              },
              {
                icon: <GripVertical className="w-7 h-7 text-primary" />,
                title: "Drag & Drop Ordering",
                desc: "Put steps in the right clinical sequence. Drag-and-drop questions mirror exactly what you'll see on test day.",
              },
              {
                icon: <Zap className="w-7 h-7 text-primary" />,
                title: "NGN Clinical Judgment",
                desc: "50+ Next Generation NCLEX questions built around the clinical judgment measurement model (CJMM).",
              },
              {
                icon: <ShieldCheck className="w-7 h-7 text-primary" />,
                title: "27 Clinical Categories",
                desc: "ICU, Maternity, Geriatrics, Burn Unit, Pharmacology, Mental Health, and 21 more — full NCLEX coverage.",
              },
              {
                icon: <Clock className="w-7 h-7 text-primary" />,
                title: "Study on Your Schedule",
                desc: "Available 24/7 on any device. Pick up exactly where you left off — progress is always saved.",
              },
            ].map((f) => (
              <div
                key={f.title}
                className="p-6 rounded-2xl bg-card border border-border shadow-sm hover:shadow-md hover:border-primary/30 transition-all duration-200"
              >
                <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
                  {f.icon}
                </div>
                <h3 className="font-semibold text-lg mb-2">{f.title}</h3>
                <p className="text-muted-foreground text-sm leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* AI vs Traditional */}
        <section className="px-6 py-16 bg-secondary/30">
          <div className="max-w-4xl mx-auto">
            <div className="text-center mb-12">
              <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
                AI Prep vs. <span className="text-muted-foreground">Traditional Prep</span>
              </h2>
              <p className="text-muted-foreground text-lg">
                See why students are switching from outdated methods.
              </p>
            </div>

            <div className="rounded-2xl border border-border overflow-hidden shadow-sm bg-card">
              <div className="grid grid-cols-3 bg-muted/50 px-6 py-4 text-sm font-semibold text-muted-foreground border-b border-border">
                <span>Feature</span>
                <span className="text-center text-primary">NCLEX AI ✦</span>
                <span className="text-center">Traditional Prep</span>
              </div>
              {comparisonRows.map((row, i) => (
                <div
                  key={row.feature}
                  className={`grid grid-cols-3 px-6 py-4 text-sm items-center border-b border-border last:border-0 ${i % 2 === 0 ? "" : "bg-muted/20"}`}
                >
                  <span className="font-medium text-foreground">{row.feature}</span>
                  <span className="text-center">
                    {row.us === true ? (
                      <CheckCircle className="w-5 h-5 text-green-500 mx-auto" />
                    ) : typeof row.us === "string" ? (
                      <span className="font-semibold text-primary">{row.us}</span>
                    ) : (
                      <XCircle className="w-5 h-5 text-destructive mx-auto" />
                    )}
                  </span>
                  <span className="text-center">
                    {row.them === true ? (
                      <CheckCircle className="w-5 h-5 text-green-500 mx-auto" />
                    ) : row.them === false ? (
                      <XCircle className="w-5 h-5 text-destructive/50 mx-auto" />
                    ) : (
                      <span className="text-muted-foreground">{row.them}</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Testimonials */}
        <section className="px-6 py-16 max-w-6xl mx-auto w-full">
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-primary/10 text-primary text-sm font-semibold mb-4 border border-primary/20">
              <Users className="w-3.5 h-3.5" />
              Real Students · Real Results
            </div>
            <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
              Nurses Who <span className="text-primary">Passed</span> With NCLEX AI
            </h2>
            <div className="flex flex-wrap justify-center gap-6 mt-2 mb-2">
              <div className="text-center">
                <p className="text-4xl font-extrabold text-primary">1,200+</p>
                <p className="text-sm text-muted-foreground mt-1">Students passed</p>
              </div>
              <div className="text-center">
                <p className="text-4xl font-extrabold text-primary">4.9★</p>
                <p className="text-sm text-muted-foreground mt-1">Average rating</p>
              </div>
              <div className="text-center">
                <p className="text-4xl font-extrabold text-primary">94%</p>
                <p className="text-sm text-muted-foreground mt-1">First-attempt pass rate</p>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {testimonials.map((t) => (
              <div
                key={t.name}
                className="p-6 rounded-2xl bg-card border border-border shadow-sm flex flex-col gap-4 hover:shadow-md hover:border-primary/30 transition-all duration-200"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="font-semibold text-foreground">{t.name}</p>
                    <p className="text-sm text-muted-foreground">{t.school}</p>
                  </div>
                  <span className="shrink-0 text-xs font-semibold px-2.5 py-1 rounded-full bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400">
                    {t.tag}
                  </span>
                </div>
                <div className="flex gap-0.5">
                  {Array.from({ length: t.stars }).map((_, i) => (
                    <Star key={i} className="w-4 h-4 fill-amber-400 text-amber-400" />
                  ))}
                </div>
                <p className="text-muted-foreground text-sm leading-relaxed">"{t.quote}"</p>
              </div>
            ))}
          </div>
        </section>

        {/* Final CTA */}
        <section className="px-6 py-20 text-center bg-gradient-to-br from-primary/5 via-background to-background">
          <div className="max-w-2xl mx-auto">
            <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
              Ready to Pass Your NCLEX?
            </h2>
            <p className="text-muted-foreground text-lg mb-8">
              Join thousands of nursing students preparing smarter with AI. Start free — no credit card required.
            </p>
            <Link href="/quiz">
              <Button size="lg" className="text-lg px-10 py-6 rounded-full shadow-lg hover:shadow-primary/25 hover:scale-105 transition-all duration-200">
                Start Your Free Practice
                <ArrowRight className="w-5 h-5 ml-2" />
              </Button>
            </Link>
            <p className="text-sm text-muted-foreground mt-4">5 free questions · Then $10/month · Cancel anytime</p>
          </div>
        </section>
      </main>

      <footer className="border-t border-border px-6 py-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-primary" />
            <span className="font-semibold text-foreground">NCLEX AI</span>
          </div>
          <p>© {new Date().getFullYear()} NCLEX AI · AI-Powered NCLEX Preparation</p>
        </div>
      </footer>
    </div>
  );
}
