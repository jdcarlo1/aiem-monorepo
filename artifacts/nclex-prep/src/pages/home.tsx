import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { useGetSessionStatus } from "@workspace/api-client-react";
import { useSessionId } from "@/hooks/useSessionId";
import { useEagerRestore } from "@/hooks/useAutoRestore";
import { Show, useClerk, useUser } from "@clerk/react";
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
  Briefcase,
  BookOpen,
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
      "27 categories covering every clinical area I needed. The burn unit, ICU, and maternity questions were detailed and realistic. For $15/month vs. $150 for prep books? This is a no-brainer for any nursing student.",
    stars: 5,
    tag: "1st attempt pass",
  },
];

const comparisonRows = [
  { feature: "NGN (Next Generation NCLEX) formatted questions", us: true, them: false },
  { feature: "Extended multiple response", us: true, them: false },
  { feature: "Drag & drop ordering questions", us: true, them: false },
  { feature: "AI-powered clinical explanations", us: true, them: false },
  { feature: "2,000+ questions across 48 categories", us: true, them: "Limited" },
  { feature: "Instant answer feedback", us: true, them: false },
  { feature: "Available 24/7 on any device", us: true, them: true },
  { feature: "Cost", us: "$15/mo or $49 lifetime", them: "$50–$200+" },
];

export default function Home() {
  const sessionId = useSessionId();
  const { signOut } = useClerk();
  const { user, isLoaded } = useUser();
  const { data: sessionStatus } = useGetSessionStatus(
    { sessionId },
    { query: { enabled: !!sessionId } }
  );

  useEagerRestore(sessionId, sessionStatus?.isSubscribed);

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
          <div className="flex items-center gap-2">
            <Link href="/nursing-school">
              <Button size="sm" variant="ghost" className="rounded-full hidden sm:flex items-center gap-1.5">
                <BookOpen className="w-3.5 h-3.5" />
                Nursing School
              </Button>
            </Link>
            {sessionStatus?.isSubscribed && (
              <Link href="/interview-prep">
                <Button size="sm" variant="ghost" className="rounded-full hidden sm:flex items-center gap-1.5">
                  <Briefcase className="w-3.5 h-3.5" />
                  Interview Prep
                </Button>
              </Link>
            )}
            <Link href="/quiz">
              <Button size="sm" variant="outline" className="rounded-full">
                Start Practicing
              </Button>
            </Link>
            <Show when="signed-out">
              <Link href="/sign-in">
                <Button size="sm" variant="ghost" className="rounded-full">
                  Sign In
                </Button>
              </Link>
            </Show>
            <Show when="signed-in">
              <Button
                size="sm"
                variant="ghost"
                className="rounded-full text-muted-foreground text-xs"
                onClick={() => signOut({ redirectUrl: "/" })}
              >
                {isLoaded && user ? user.primaryEmailAddress?.emailAddress?.split("@")[0] : ""} · Sign Out
              </Button>
            </Show>
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
            AI Adaptive Engine — Trains Like the Real NCLEX CAT (Computerized Adaptive Test)
          </div>

          <h1 className="text-4xl md:text-6xl lg:text-7xl font-extrabold tracking-tight text-foreground leading-tight max-w-4xl mx-auto mb-6">
            The NCLEX Is Adaptive.
            <br />
            <span className="text-primary">Your Training Should Be Too.</span>
          </h1>

          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 leading-relaxed">
            The real NCLEX CAT (Computerized Adaptive Test) shuts off when you've proven yourself — or haven't. Our AI Adaptive Engine works the same way: finding your weak spots, raising the difficulty, and drilling you until you're above the passing standard.
          </p>

          <div className="flex flex-col sm:flex-row items-center gap-4 mb-12">
            <Link href="/quiz">
              <Button size="lg" className="text-lg px-10 py-6 rounded-full shadow-lg hover:shadow-primary/25 hover:scale-105 transition-all duration-200">
                {sessionStatus?.isSubscribed ? "Continue Practicing" : "Start Free Practice"}
                <ArrowRight className="w-5 h-5 ml-2" />
              </Button>
            </Link>
            {(!sessionStatus || !sessionStatus.isSubscribed) && (
              <p className="text-sm text-muted-foreground">
                10 free questions · Then $15/mo or $49 lifetime
              </p>
            )}
          </div>

          <p className="text-sm font-semibold text-primary mb-6 tracking-wide">🌐 nclexai.org</p>

          <div className="flex flex-wrap justify-center gap-6 text-sm font-medium text-muted-foreground">
            {[
              { icon: <Zap className="w-4 h-4 text-primary" />, label: "2,000+ Questions" },
              { icon: <ShieldCheck className="w-4 h-4 text-primary" />, label: "48+ Categories" },
              { icon: <Brain className="w-4 h-4 text-primary" />, label: "AI Explanations" },
              { icon: <TrendingUp className="w-4 h-4 text-primary" />, label: "NGN (Next Generation NCLEX) Formats" },
            ].map((stat) => (
              <div key={stat.label} className="flex items-center gap-1.5">
                {stat.icon}
                <span>{stat.label}</span>
              </div>
            ))}
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
                <p className="text-4xl font-extrabold text-primary">98%</p>
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

        {/* 3 Pillars */}
        <section className="px-6 py-12 max-w-6xl mx-auto w-full">
          <div className="text-center mb-8">
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight mb-2">
              One Platform. <span className="text-primary">Three Pillars.</span>
            </h2>
            <p className="text-muted-foreground">Everything a nursing student needs — from orientation week to orientation day at your first hospital.</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            <Link href="/nursing-school">
              <div className="group h-full p-7 rounded-2xl border-2 border-border bg-card hover:border-primary/50 hover:shadow-lg transition-all duration-200 cursor-pointer flex flex-col">
                <div className="w-12 h-12 rounded-xl bg-blue-100 flex items-center justify-center mb-5">
                  <BookOpen className="w-6 h-6 text-blue-700" />
                </div>
                <div className="text-xs font-bold text-primary uppercase tracking-widest mb-2">Pillar 1</div>
                <h3 className="text-xl font-extrabold tracking-tight mb-2 group-hover:text-primary transition-colors">Nursing School</h3>
                <p className="text-muted-foreground text-sm leading-relaxed flex-1">Study by system — Fundamentals, Med-Surg, Pharmacology. Pick Cardiac one week, Renal the next. 30 focused questions per topic.</p>
                <div className="mt-5 flex items-center gap-1 text-primary text-sm font-semibold">
                  Explore question banks <ArrowRight className="w-3.5 h-3.5 ml-1 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </Link>
            <Link href="/quiz">
              <div className="group h-full p-7 rounded-2xl border-2 border-primary bg-card shadow-lg hover:shadow-xl transition-all duration-200 cursor-pointer flex flex-col relative">
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 px-3 py-1 rounded-full bg-primary text-primary-foreground text-xs font-bold">
                  <Zap className="w-3 h-3" /> Most Popular
                </div>
                <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center mb-5">
                  <Brain className="w-6 h-6 text-primary" />
                </div>
                <div className="text-xs font-bold text-primary uppercase tracking-widest mb-2">Pillar 2</div>
                <h3 className="text-xl font-extrabold tracking-tight mb-2 group-hover:text-primary transition-colors">NCLEX Prep</h3>
                <p className="text-muted-foreground text-sm leading-relaxed flex-1">Train exactly like the real CAT (Computerized Adaptive Test). Our AI Adaptive Engine adjusts every question to your performance — NGN (Next Generation NCLEX) formats, drag-and-drop, extended multiple response. When the computer shuts off at 85, you'll know why.</p>
                <div className="mt-5 flex items-center gap-1 text-primary text-sm font-semibold">
                  Start practicing <ArrowRight className="w-3.5 h-3.5 ml-1 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </Link>
            <Link href="/interview-prep">
              <div className="group h-full p-7 rounded-2xl border-2 border-border bg-card hover:border-primary/50 hover:shadow-lg transition-all duration-200 cursor-pointer flex flex-col">
                <div className="w-12 h-12 rounded-xl bg-green-100 flex items-center justify-center mb-5">
                  <Briefcase className="w-6 h-6 text-green-700" />
                </div>
                <div className="text-xs font-bold text-primary uppercase tracking-widest mb-2">Pillar 3</div>
                <h3 className="text-xl font-extrabold tracking-tight mb-2 group-hover:text-primary transition-colors">Interview Prep</h3>
                <p className="text-muted-foreground text-sm leading-relaxed flex-1">20 real nursing job interview questions with expert rationales. Walk into your first hospital interview confident and prepared.</p>
                <div className="mt-5 flex items-center gap-1 text-primary text-sm font-semibold">
                  Ace your interview <ArrowRight className="w-3.5 h-3.5 ml-1 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </Link>
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
                desc: "Select all that apply — the NGN (Next Generation NCLEX) format that trips most students. Practice until it feels natural.",
              },
              {
                icon: <GripVertical className="w-7 h-7 text-primary" />,
                title: "Drag & Drop Ordering",
                desc: "Put steps in the right clinical sequence. Drag-and-drop questions mirror exactly what you'll see on test day.",
              },
              {
                icon: <Zap className="w-7 h-7 text-primary" />,
                title: "NGN (Next Generation NCLEX) Clinical Judgment",
                desc: "50+ Next Generation NCLEX questions built around the clinical judgment measurement model (CJMM).",
              },
              {
                icon: <ShieldCheck className="w-7 h-7 text-primary" />,
                title: "28 Clinical Categories",
                desc: "ICU, Maternity, Geriatrics, Burn Unit, Pharmacology, Mental Health, and 22 more — full NCLEX coverage.",
              },
              {
                icon: <Briefcase className="w-7 h-7 text-primary" />,
                title: "Nursing Interview Prep",
                desc: "20 real interview questions with detailed rationales — ace your first RN job interview with confidence.",
              },
              {
                icon: <TrendingUp className="w-7 h-7 text-primary" />,
                title: "AI Adaptive Engine",
                desc: "Our adaptive engine analyzes your performance and surfaces the questions you need most — focusing your study time where it counts.",
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

        {/* Interview Prep Banner */}
        <section className="px-6 py-16 bg-gradient-to-br from-primary/8 via-primary/5 to-background">
          <div className="max-w-4xl mx-auto">
            <div className="rounded-2xl border border-primary/20 bg-card shadow-md overflow-hidden">
              <div className="grid md:grid-cols-2 gap-0">
                <div className="p-8 md:p-10">
                  <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-xs font-bold mb-4 border border-primary/20">
                    <Briefcase className="w-3.5 h-3.5" />
                    BONUS — Included with Every Plan
                  </div>
                  <h2 className="text-2xl md:text-3xl font-extrabold tracking-tight mb-3">
                    Ace Your Nursing Interview <span className="text-primary">with Confidence.</span>
                  </h2>
                  <p className="text-muted-foreground leading-relaxed mb-6">
                    Most nurses pass the exam and blank when the hiring manager asks, "Tell me about a time you made a mistake." We include 20 real hospital interview questions with expert rationales — so you walk in prepared, polished, and ready to get the job.
                  </p>
                  <Link href="/quiz">
                    <Button className="rounded-full px-6">
                      Practice Interview Questions
                      <ArrowRight className="w-4 h-4 ml-2" />
                    </Button>
                  </Link>
                </div>
                <div className="bg-secondary/40 p-8 md:p-10 flex flex-col justify-center gap-4">
                  {[
                    "How to answer behavioral questions using STAR",
                    "Prioritization & conflict with physicians",
                    "Medication safety & scope of practice",
                    "Patient advocacy & end-of-life care",
                    "What to ask your interviewer",
                    "How to handle your greatest weakness question",
                  ].map((item) => (
                    <div key={item} className="flex items-start gap-3">
                      <CheckCircle className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                      <span className="text-sm text-foreground font-medium">{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
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

        {/* Returning Member Section */}
        {sessionStatus?.isSubscribed && (
          <section className="px-6 py-10">
            <div className="max-w-4xl mx-auto">
              <div className="rounded-2xl border border-primary/30 bg-primary/5 p-6 md:p-8 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
                <div>
                  <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-xs font-bold mb-3 border border-primary/20">
                    <Briefcase className="w-3.5 h-3.5" />
                    Welcome Back, Premium Member
                  </div>
                  <h3 className="text-xl font-extrabold tracking-tight mb-1">Ready for Your Nursing Interview?</h3>
                  <p className="text-muted-foreground text-sm">You've got full access to all 20 nursing interview prep questions with detailed rationales — practice anytime.</p>
                </div>
                <Link href="/interview-prep" className="shrink-0">
                  <Button className="rounded-full px-6 shadow-md">
                    Go to Interview Prep
                    <ArrowRight className="w-4 h-4 ml-2" />
                  </Button>
                </Link>
              </div>
            </div>
          </section>
        )}

        {/* Pricing */}
        <section className="px-6 py-16 bg-secondary/30">
          <div className="max-w-4xl mx-auto">
            <div className="text-center mb-12">
              <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
                Simple, <span className="text-primary">Transparent Pricing</span>
              </h2>
              <p className="text-muted-foreground text-lg">
                {sessionStatus?.isSubscribed ? "You're an active member. Thank you for supporting NCLEX AI." : "Start free. Upgrade when you're ready."}
              </p>
            </div>

            {sessionStatus?.isSubscribed ? (
              <div className="max-w-2xl mx-auto rounded-2xl border-2 border-primary bg-primary/5 p-8 text-center shadow-md">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-xs font-bold mb-4 border border-primary/20">
                  <ShieldCheck className="w-3.5 h-3.5" />
                  Active Member
                </div>
                <h3 className="text-2xl font-extrabold tracking-tight mb-2">You have full access.</h3>
                <p className="text-muted-foreground mb-6">All 2,000+ questions, every category, and AI explanations — unlocked.</p>
                <Link href="/quiz">
                  <Button className="rounded-full px-8 shadow-md">
                    Continue Practicing
                    <ArrowRight className="w-4 h-4 ml-2" />
                  </Button>
                </Link>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 max-w-2xl mx-auto">
                  {/* Monthly */}
                  <div className="rounded-2xl border border-border bg-card p-8 flex flex-col shadow-sm hover:shadow-md hover:border-primary/30 transition-all duration-200">
                    <p className="text-sm font-semibold text-muted-foreground mb-2">Monthly</p>
                    <div className="flex items-baseline gap-1 mb-1">
                      <span className="text-5xl font-extrabold text-foreground">$15</span>
                      <span className="text-muted-foreground font-medium">/month</span>
                    </div>
                    <p className="text-xs text-muted-foreground mb-6">Cancel anytime</p>
                    <ul className="space-y-3 mb-8 flex-1">
                      {["All 2,000+ questions", "48 categories incl. Interview Prep", "NGN (Next Generation NCLEX) question formats", "AI explanations", "AI Adaptive Engine"].map((f) => (
                        <li key={f} className="flex items-center gap-2 text-sm text-foreground">
                          <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                          {f}
                        </li>
                      ))}
                    </ul>
                    <Link href="/quiz">
                      <Button variant="outline" className="w-full rounded-xl">Get Started</Button>
                    </Link>
                  </div>

                  {/* Lifetime */}
                  <div className="relative rounded-2xl border-2 border-primary bg-card p-8 flex flex-col shadow-lg">
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 px-3 py-1 rounded-full bg-primary text-primary-foreground text-xs font-bold">
                      <Zap className="w-3 h-3" />
                      Most Popular
                    </div>
                    <p className="text-sm font-semibold text-muted-foreground mb-2">Lifetime</p>
                    <div className="flex items-baseline gap-1 mb-1">
                      <span className="text-5xl font-extrabold text-foreground">$49</span>
                      <span className="text-muted-foreground font-medium">one-time</span>
                    </div>
                    <p className="text-xs text-muted-foreground mb-6">Pay once · Access forever</p>
                    <ul className="space-y-3 mb-8 flex-1">
                      {["All 2,000+ questions", "48 categories incl. Interview Prep", "NGN (Next Generation NCLEX) question formats", "AI explanations", "AI Adaptive Engine", "All future updates included"].map((f) => (
                        <li key={f} className="flex items-center gap-2 text-sm text-foreground">
                          <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                          {f}
                        </li>
                      ))}
                    </ul>
                    <Link href="/quiz">
                      <Button className="w-full rounded-xl shadow-md">Get Lifetime Access</Button>
                    </Link>
                  </div>
                </div>

                <p className="text-center text-sm text-muted-foreground mt-8">
                  🔒 Secure payment · 30-day money-back guarantee · No hidden fees
                </p>
              </>
            )}
          </div>
        </section>

        {/* Final CTA */}
        <section className="px-6 py-20 text-center bg-gradient-to-br from-primary/5 via-background to-background">
          <div className="max-w-2xl mx-auto">
            {sessionStatus?.isSubscribed ? (
              <>
                <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-4">
                  Welcome Back.
                </h2>
                <p className="text-muted-foreground text-lg mb-8">
                  Pick up right where you left off. Your progress is saved.
                </p>
                <Link href="/quiz">
                  <Button size="lg" className="text-lg px-10 py-6 rounded-full shadow-lg hover:shadow-primary/25 hover:scale-105 transition-all duration-200">
                    Continue Practicing
                    <ArrowRight className="w-5 h-5 ml-2" />
                  </Button>
                </Link>
              </>
            ) : (
              <>
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
                <p className="text-sm text-muted-foreground mt-4">10 free questions · $15/month or $49 lifetime · Cancel anytime</p>
              </>
            )}
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
