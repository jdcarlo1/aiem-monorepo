import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import {
  CheckCircle,
  Star,
  ArrowRight,
  Brain,
  BookOpen,
  Zap,
  ShieldCheck,
  Clock,
  Trophy,
} from "lucide-react";

const testimonials = [
  {
    name: "Maria S., RN",
    tag: "Passed on 2nd attempt",
    quote:
      "I failed once with traditional prep books. Switched to NCLEX AI and passed. The explanations finally made clinical judgment click.",
    stars: 5,
  },
  {
    name: "James T., BSN",
    tag: "Passed 1st attempt",
    quote:
      "The select-all-that-apply and ordering questions looked identical to what I saw on test day. No other app comes close.",
    stars: 5,
  },
  {
    name: "Ashley R., RN",
    tag: "Passed 1st attempt",
    quote:
      "After every wrong answer, the AI explains the exact clinical reasoning. I walked into the testing center feeling confident.",
    stars: 5,
  },
];

const features = [
  {
    icon: <Brain className="w-6 h-6 text-blue-600" />,
    title: "2,778+ Practice Questions",
    desc: "Covering every NCLEX topic — cardiac, respiratory, pharmacology, maternity, psych, and more.",
  },
  {
    icon: <Zap className="w-6 h-6 text-yellow-500" />,
    title: "NGN-Format Questions",
    desc: "Extended multiple response, drag & drop ordering — exactly what the new Next Generation NCLEX tests.",
  },
  {
    icon: <BookOpen className="w-6 h-6 text-green-600" />,
    title: "Detailed AI Explanations",
    desc: "Every question includes a full clinical reasoning explanation — not just 'A is correct.'",
  },
  {
    icon: <ShieldCheck className="w-6 h-6 text-purple-600" />,
    title: "59 Nursing Categories",
    desc: "Nursing School mode lets you study by category — wound care, pharmacology, physical assessment, and more.",
  },
  {
    icon: <Trophy className="w-6 h-6 text-orange-500" />,
    title: "Interview Prep Included",
    desc: "Practice nursing interview questions so you're ready the day you get your RN license.",
  },
  {
    icon: <Clock className="w-6 h-6 text-rose-500" />,
    title: "Study Anywhere, Anytime",
    desc: "Works on your phone or laptop — no downloads needed. Study between classes or on break.",
  },
];

export default function Landing() {
  return (
    <div className="min-h-screen bg-white">
      {/* Top bar */}
      <div className="bg-blue-600 text-white text-center text-sm py-2 px-4 font-medium">
        🎉 Try 10 questions free — no credit card required
      </div>

      {/* Nav */}
      <nav className="flex items-center justify-between px-6 py-4 border-b border-gray-100 max-w-5xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <Brain className="w-5 h-5 text-white" />
          </div>
          <div>
            <span className="font-bold text-gray-900 text-lg">NCLEX AI</span>
            <span className="text-base font-bold text-black ml-2">nclexai.org</span>
          </div>
        </div>
        <Link href="/quiz">
          <Button size="sm" className="bg-blue-600 hover:bg-blue-700 text-white">
            Start Free
          </Button>
        </Link>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-6 pt-16 pb-12 text-center">
        <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 text-sm font-medium px-4 py-1.5 rounded-full mb-6">
          <Star className="w-4 h-4 fill-blue-600 text-blue-600" />
          Trusted by nursing students across the US
        </div>

        <h1 className="text-4xl sm:text-5xl font-extrabold text-gray-900 leading-tight mb-6">
          Pass the NCLEX on{" "}
          <span className="text-blue-600">your first attempt</span>
        </h1>

        <p className="text-xl text-gray-600 mb-10 max-w-2xl mx-auto leading-relaxed">
          2,778+ practice questions with AI-powered explanations. NGN-format
          questions, 59 categories, and a free mode — no credit card needed.
        </p>

        <div className="flex flex-col sm:flex-row gap-4 justify-center items-center mb-6">
          <Link href="/quiz">
            <Button
              size="lg"
              className="bg-blue-600 hover:bg-blue-700 text-white text-lg px-10 py-6 rounded-xl shadow-lg shadow-blue-200 w-full sm:w-auto"
            >
              Start 10 Free Questions
              <ArrowRight className="w-5 h-5 ml-2" />
            </Button>
          </Link>
          <Link href="/nursing-school">
            <Button
              size="lg"
              variant="outline"
              className="text-lg px-8 py-6 rounded-xl w-full sm:w-auto border-gray-300 text-gray-700"
            >
              Browse All 59 Categories
            </Button>
          </Link>
        </div>

        <p className="text-sm text-gray-400">
          No signup required · Start instantly · $15/mo or $49 lifetime after free trial
        </p>
      </section>

      {/* Social proof strip */}
      <section className="bg-gray-50 border-y border-gray-100 py-6 px-6">
        <div className="max-w-4xl mx-auto flex flex-col sm:flex-row items-center justify-center gap-6 sm:gap-12 text-center">
          <div>
            <div className="text-3xl font-extrabold text-gray-900">2,778+</div>
            <div className="text-sm text-gray-500 mt-1">Practice Questions</div>
          </div>
          <div className="hidden sm:block w-px h-10 bg-gray-200" />
          <div>
            <div className="text-3xl font-extrabold text-gray-900">59</div>
            <div className="text-sm text-gray-500 mt-1">Nursing Categories</div>
          </div>
          <div className="hidden sm:block w-px h-10 bg-gray-200" />
          <div>
            <div className="text-3xl font-extrabold text-gray-900">NGN</div>
            <div className="text-sm text-gray-500 mt-1">Next Gen Format</div>
          </div>
          <div className="hidden sm:block w-px h-10 bg-gray-200" />
          <div>
            <div className="text-3xl font-extrabold text-gray-900">FREE</div>
            <div className="text-sm text-gray-500 mt-1">To Start — No Card</div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="max-w-5xl mx-auto px-6 py-16">
        <h2 className="text-3xl font-bold text-gray-900 text-center mb-3">
          Everything you need to pass
        </h2>
        <p className="text-gray-500 text-center mb-12 text-lg">
          Built specifically for the Next Generation NCLEX
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f, i) => (
            <div
              key={i}
              className="bg-white border border-gray-100 rounded-2xl p-6 shadow-sm hover:shadow-md transition-shadow"
            >
              <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center mb-4">
                {f.icon}
              </div>
              <h3 className="font-bold text-gray-900 mb-2">{f.title}</h3>
              <p className="text-gray-500 text-sm leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Testimonials */}
      <section className="bg-blue-600 py-16 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-3">
            Real nurses. Real results.
          </h2>
          <p className="text-blue-200 text-center mb-12 text-lg">
            Join students who passed the NCLEX with NCLEX AI
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            {testimonials.map((t, i) => (
              <div key={i} className="bg-white rounded-2xl p-6 shadow-lg">
                <div className="flex gap-1 mb-3">
                  {Array.from({ length: t.stars }).map((_, s) => (
                    <Star
                      key={s}
                      className="w-4 h-4 fill-yellow-400 text-yellow-400"
                    />
                  ))}
                </div>
                <p className="text-gray-700 text-sm leading-relaxed mb-4">
                  "{t.quote}"
                </p>
                <div>
                  <div className="font-bold text-gray-900 text-sm">{t.name}</div>
                  <div className="text-xs text-blue-600 font-medium mt-0.5">
                    ✓ {t.tag}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section className="max-w-4xl mx-auto px-6 py-16 text-center">
        <h2 className="text-3xl font-bold text-gray-900 mb-3">
          Simple, affordable pricing
        </h2>
        <p className="text-gray-500 text-lg mb-12">
          Fraction of the cost of prep books or tutoring
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 max-w-2xl mx-auto">
          {/* Monthly */}
          <div className="border border-gray-200 rounded-2xl p-8 text-left">
            <div className="text-gray-500 text-sm font-medium mb-2">Monthly</div>
            <div className="text-4xl font-extrabold text-gray-900 mb-1">$15</div>
            <div className="text-gray-400 text-sm mb-6">per month, cancel anytime</div>
            <ul className="space-y-3 mb-8">
              {["All 2,778+ questions", "All 59 categories", "NGN-format questions", "AI explanations", "Interview prep"].map((item) => (
                <li key={item} className="flex items-center gap-2 text-sm text-gray-700">
                  <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
                  {item}
                </li>
              ))}
            </ul>
            <Link href="/quiz">
              <Button className="w-full bg-gray-900 hover:bg-gray-800 text-white rounded-xl py-5">
                Start Free Trial
              </Button>
            </Link>
          </div>

          {/* Lifetime */}
          <div className="border-2 border-blue-600 rounded-2xl p-8 text-left relative bg-blue-50">
            <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-blue-600 text-white text-xs font-bold px-4 py-1 rounded-full">
              BEST VALUE
            </div>
            <div className="text-blue-600 text-sm font-medium mb-2">Lifetime</div>
            <div className="text-4xl font-extrabold text-gray-900 mb-1">$49</div>
            <div className="text-gray-400 text-sm mb-6">one-time payment, forever</div>
            <ul className="space-y-3 mb-8">
              {["All 2,778+ questions", "All 59 categories", "NGN-format questions", "AI explanations", "Interview prep", "Future questions included"].map((item) => (
                <li key={item} className="flex items-center gap-2 text-sm text-gray-700">
                  <CheckCircle className="w-4 h-4 text-blue-600 flex-shrink-0" />
                  {item}
                </li>
              ))}
            </ul>
            <Link href="/quiz">
              <Button className="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-xl py-5">
                Get Lifetime Access
              </Button>
            </Link>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="bg-gray-900 py-16 px-6 text-center">
        <h2 className="text-3xl font-bold text-white mb-4">
          Ready to pass the NCLEX?
        </h2>
        <p className="text-gray-400 text-lg mb-8 max-w-xl mx-auto">
          Start with 10 free questions right now. No account needed, no credit card.
        </p>
        <Link href="/quiz">
          <Button
            size="lg"
            className="bg-blue-600 hover:bg-blue-700 text-white text-lg px-12 py-6 rounded-xl shadow-lg"
          >
            Start Free Now
            <ArrowRight className="w-5 h-5 ml-2" />
          </Button>
        </Link>
        <p className="text-gray-500 text-sm mt-4">
          nclexai.org · $15/mo or $49 lifetime after free trial
        </p>
      </section>
    </div>
  );
}
