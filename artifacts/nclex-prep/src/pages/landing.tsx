import { useState, Fragment } from "react";
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
  XCircle,
  ChevronLeft,
  ChevronRight,
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
    desc: "Matrix/Grid, Bowtie, Extended Multiple Response — exactly what the Next Generation NCLEX tests.",
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
    title: "CAT Adaptive Testing",
    desc: "Just like the real NCLEX — the engine adapts to your performance and targets your weak areas automatically.",
  },
];

// ── Matrix Question ────────────────────────────────────────────────────────────
const matrixRows = [
  {
    action: "Elevate the head of bed to 45°",
    correct: "Indicated",
    rationale: "Reduces preload and eases breathing in fluid-overloaded patients.",
  },
  {
    action: "Administer furosemide as prescribed",
    correct: "Indicated",
    rationale: "Loop diuretic — primary treatment to remove excess fluid in heart failure.",
  },
  {
    action: "Encourage oral fluid intake of 3 L/day",
    correct: "Contraindicated",
    rationale: "Fluid restriction (1–1.5 L/day) is standard in decompensated heart failure.",
  },
  {
    action: "Weigh the patient daily at the same time",
    correct: "Indicated",
    rationale: "1 kg weight gain = ~1 L fluid retained. Daily weights detect worsening early.",
  },
  {
    action: "Administer prescribed IV 0.9% NS bolus",
    correct: "Contraindicated",
    rationale: "IV saline bolus worsens fluid overload — contraindicated in heart failure.",
  },
];
const matrixColumns = ["Indicated", "Contraindicated", "Non-Essential"];

function MatrixDemo() {
  const [selections, setSelections] = useState<Record<number, string>>({});
  const [submitted, setSubmitted] = useState(false);

  const allSelected = matrixRows.every((_, i) => selections[i]);
  const correctCount = submitted
    ? matrixRows.filter((r, i) => selections[i] === r.correct).length
    : 0;

  function select(rowIdx: number, col: string) {
    if (submitted) return;
    setSelections((prev) => ({ ...prev, [rowIdx]: col }));
  }

  return (
    <div className="bg-white border border-gray-200 rounded-2xl shadow-sm">
      {/* Header */}
      <div className="bg-blue-600 px-6 py-3 flex items-center gap-2 rounded-t-2xl">
        <span className="bg-white text-blue-600 text-xs font-bold px-2 py-0.5 rounded">MATRIX / GRID</span>
        <span className="text-white text-xs font-medium">NGN Question Type</span>
      </div>

      {/* Scenario */}
      <div className="px-6 pt-5 pb-4">
        <p className="text-xs font-bold text-blue-700 uppercase tracking-wider mb-2">Clinical Scenario</p>
        <p className="text-sm text-gray-800 leading-relaxed">
          A nurse is caring for a <strong>72-year-old client</strong> admitted with <strong>acute decompensated heart failure</strong>.
          The client has +2 pitting edema, bilateral crackles, weight gain of 4 lbs in 2 days, and BP 158/92 mmHg.
        </p>
        <p className="text-sm font-semibold text-gray-900 mt-3">
          For each nursing action below, tap <em>Indicated</em>, <em>Contraindicated</em>, or <em>Non-Essential</em>:
        </p>
      </div>

      {/* Card-based rows — large tap targets, no table */}
      <div className="px-6 pb-4 space-y-3">
        {matrixRows.map((row, i) => {
          const sel = selections[i];
          const isCorrect = submitted && sel === row.correct;
          const isWrong = submitted && sel && sel !== row.correct;
          return (
            <div
              key={i}
              className={`rounded-xl border p-4 transition-colors ${
                isCorrect ? "border-green-300 bg-green-50"
                : isWrong ? "border-red-300 bg-red-50"
                : "border-gray-200 bg-gray-50"
              }`}
            >
              {/* Action label */}
              <div className="flex items-start gap-2 mb-3">
                {submitted && isCorrect && <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0 mt-0.5" />}
                {submitted && isWrong && <XCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />}
                <p className="text-sm font-medium text-gray-800">{row.action}</p>
              </div>
              {/* Big pill buttons */}
              <div className="flex flex-wrap gap-2">
                {matrixColumns.map((col) => {
                  const isSelected = sel === col;
                  const isCorrectCol = submitted && col === row.correct;
                  return (
                    <button
                      key={col}
                      type="button"
                      onClick={() => select(i, col)}
                      disabled={submitted}
                      className={`px-4 py-2 rounded-lg text-sm font-semibold border-2 transition-all cursor-pointer
                        ${isCorrectCol
                          ? "border-green-500 bg-green-500 text-white"
                          : isSelected && isWrong
                          ? "border-red-400 bg-red-400 text-white"
                          : isSelected
                          ? "border-blue-600 bg-blue-600 text-white"
                          : submitted
                          ? "border-gray-200 bg-white text-gray-400"
                          : "border-gray-300 bg-white text-gray-600 hover:border-blue-400 hover:text-blue-600"
                        }`}
                    >
                      {col}
                    </button>
                  );
                })}
              </div>
              {/* Rationale */}
              {submitted && (
                <p className={`text-xs mt-2 italic ${isCorrect ? "text-green-700" : "text-red-700"}`}>
                  <strong>Correct: {row.correct}.</strong> {row.rationale}
                </p>
              )}
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between flex-wrap gap-3">
        {!submitted ? (
          <Button
            type="button"
            onClick={() => setSubmitted(true)}
            disabled={!allSelected}
            className="bg-blue-600 hover:bg-blue-700 text-white px-6 disabled:opacity-50"
          >
            Check My Answers
          </Button>
        ) : (
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`font-bold text-sm ${correctCount === matrixRows.length ? "text-green-600" : "text-orange-600"}`}>
              {correctCount}/{matrixRows.length} correct
            </span>
            <Link href="/quiz">
              <Button className="bg-blue-600 hover:bg-blue-700 text-white px-5">
                Try More Questions <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            </Link>
          </div>
        )}
        {!allSelected && !submitted && (
          <span className="text-xs text-gray-400">Select an answer for each row above</span>
        )}
      </div>
    </div>
  );
}

// ── Bowtie Question ────────────────────────────────────────────────────────────
const bowtieLeft = [
  { id: "A", text: "Administer 15g fast-acting carbohydrates orally", correct: true },
  { id: "B", text: "Administer insulin per sliding scale", correct: false },
  { id: "C", text: "Restrict oral intake and call the provider", correct: false },
  { id: "D", text: "Place the client in Trendelenburg position", correct: false },
];
const bowtieCenter = [
  { id: "A", text: "Diabetic ketoacidosis (DKA)", correct: false },
  { id: "B", text: "Hypoglycemia", correct: true },
  { id: "C", text: "Hyperglycemic hyperosmolar state", correct: false },
  { id: "D", text: "Addisonian crisis", correct: false },
];
const bowtieRight = [
  { id: "A", text: "Serum potassium level", correct: false },
  { id: "B", text: "Blood glucose level", correct: true },
  { id: "C", text: "Urine output", correct: false },
  { id: "D", text: "Peripheral oxygen saturation", correct: false },
];

const bowtieExplanation =
  "The scenario describes hypoglycemia (confusion, diaphoresis, trembling after insulin + missed meal). First action: give 15g fast-acting carbs (Rule of 15). Monitor blood glucose every 15 minutes until >70 mg/dL.";

function BowtieColumn({
  title,
  color,
  options,
  selected,
  onSelect,
  submitted,
}: {
  title: string;
  color: string;
  options: typeof bowtieLeft;
  selected: string | null;
  onSelect: (id: string) => void;
  submitted: boolean;
}) {
  return (
    <div className="flex-1 min-w-0">
      <div className={`text-center text-xs font-bold uppercase tracking-wider mb-3 ${color}`}>{title}</div>
      <div className="space-y-2">
        {options.map((opt) => {
          const isSelected = selected === opt.id;
          const isCorrectOpt = submitted && opt.correct;
          const isWrongOpt = submitted && isSelected && !opt.correct;
          return (
            <button
              key={opt.id}
              onClick={() => !submitted && onSelect(opt.id)}
              disabled={submitted}
              className={`w-full text-left text-xs px-3 py-2.5 rounded-lg border-2 transition-all leading-snug
                ${isCorrectOpt
                  ? "border-green-500 bg-green-50 text-green-800"
                  : isWrongOpt
                  ? "border-red-400 bg-red-50 text-red-800"
                  : isSelected
                  ? "border-blue-500 bg-blue-50 text-blue-800"
                  : "border-gray-200 bg-white text-gray-700 hover:border-blue-300"
                }`}
            >
              <span className="font-bold mr-1">{opt.id}.</span> {opt.text}
              {isCorrectOpt && <span className="ml-1">✓</span>}
              {isWrongOpt && <span className="ml-1">✗</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function BowtieDemo() {
  const [leftSel, setLeftSel] = useState<string | null>(null);
  const [centerSel, setCenterSel] = useState<string | null>(null);
  const [rightSel, setRightSel] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const allSelected = leftSel && centerSel && rightSel;
  const leftCorrect = submitted && bowtieLeft.find((o) => o.id === leftSel)?.correct;
  const centerCorrect = submitted && bowtieCenter.find((o) => o.id === centerSel)?.correct;
  const rightCorrect = submitted && bowtieRight.find((o) => o.id === rightSel)?.correct;
  const score = submitted ? [leftCorrect, centerCorrect, rightCorrect].filter(Boolean).length : 0;

  function renderColumn(
    title: string,
    accent: string,
    options: typeof bowtieLeft,
    selected: string | null,
    onSelect: (id: string) => void,
  ) {
    return (
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-bold uppercase tracking-wider mb-2 ${accent}`}>{title}</p>
        <div className="space-y-2">
          {options.map((opt) => {
            const isSelected = selected === opt.id;
            const isCorrectOpt = submitted && opt.correct;
            const isWrongOpt = submitted && isSelected && !opt.correct;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => !submitted && onSelect(opt.id)}
                disabled={submitted}
                className={`w-full text-left text-sm px-4 py-3 rounded-xl border-2 transition-all font-medium cursor-pointer
                  ${isCorrectOpt
                    ? "border-green-500 bg-green-50 text-green-800"
                    : isWrongOpt
                    ? "border-red-400 bg-red-50 text-red-800"
                    : isSelected
                    ? "border-blue-500 bg-blue-50 text-blue-800"
                    : submitted
                    ? "border-gray-200 bg-white text-gray-400"
                    : "border-gray-200 bg-white text-gray-700 hover:border-blue-300 hover:bg-blue-50"
                  }`}
              >
                <span className="font-bold mr-1">{opt.id}.</span> {opt.text}
                {isCorrectOpt && <span className="ml-1 text-green-600">✓</span>}
                {isWrongOpt && <span className="ml-1 text-red-500">✗</span>}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-2xl shadow-sm">
      {/* Header */}
      <div className="bg-purple-600 px-6 py-3 flex items-center gap-2 rounded-t-2xl">
        <span className="bg-white text-purple-600 text-xs font-bold px-2 py-0.5 rounded">BOW-TIE</span>
        <span className="text-white text-xs font-medium">NGN Question Type</span>
      </div>

      {/* Scenario */}
      <div className="px-6 pt-5 pb-4">
        <p className="text-xs font-bold text-purple-700 uppercase tracking-wider mb-2">Clinical Scenario</p>
        <p className="text-sm text-gray-800 leading-relaxed">
          A <strong>28-year-old client</strong> with Type 1 diabetes is brought to the ED confused, diaphoretic,
          and trembling. Family reports the client took their insulin this morning but skipped breakfast.
          Vital signs: BP 110/70, HR 102, RR 18, Temp 98.4°F, SpO₂ 98%.
        </p>
        <p className="text-sm font-semibold text-gray-900 mt-3">
          Select one answer in each column — Action → Condition → Parameter to Monitor:
        </p>
      </div>

      {/* Three columns — stacks on mobile, side by side on desktop */}
      <div className="px-6 pb-4">
        <div className="flex flex-col sm:flex-row gap-4">
          {renderColumn("Action to Take", "text-blue-600", bowtieLeft, leftSel, setLeftSel)}
          <div className="hidden sm:flex flex-col items-center justify-center flex-shrink-0 gap-1 pt-6">
            <div className="w-3 h-3 bg-gray-300 rotate-45" />
            <div className="w-3 h-3 bg-gray-300 rotate-45" />
          </div>
          {renderColumn("Condition", "text-purple-600", bowtieCenter, centerSel, setCenterSel)}
          <div className="hidden sm:flex flex-col items-center justify-center flex-shrink-0 gap-1 pt-6">
            <div className="w-3 h-3 bg-gray-300 rotate-45" />
            <div className="w-3 h-3 bg-gray-300 rotate-45" />
          </div>
          {renderColumn("Parameter to Monitor", "text-green-600", bowtieRight, rightSel, setRightSel)}
        </div>
      </div>

      {/* AI Explanation */}
      {submitted && (
        <div className="mx-6 mb-4 bg-blue-50 border border-blue-200 rounded-xl p-4">
          <p className="text-xs font-bold text-blue-700 mb-1">🤖 AI Explanation</p>
          <p className="text-sm text-blue-900 leading-relaxed">{bowtieExplanation}</p>
        </div>
      )}

      {/* Footer */}
      <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between flex-wrap gap-3">
        {!submitted ? (
          <Button
            type="button"
            onClick={() => setSubmitted(true)}
            disabled={!allSelected}
            className="bg-purple-600 hover:bg-purple-700 text-white px-6 disabled:opacity-50"
          >
            Check My Answers
          </Button>
        ) : (
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`font-bold text-sm ${score === 3 ? "text-green-600" : "text-orange-600"}`}>
              {score}/3 correct
            </span>
            <Link href="/quiz">
              <Button className="bg-blue-600 hover:bg-blue-700 text-white px-5">
                Try More Questions <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            </Link>
          </div>
        )}
        {!allSelected && !submitted && (
          <span className="text-xs text-gray-400">Select one from each column above</span>
        )}
      </div>
    </div>
  );
}

// ── Question Preview Carousel ──────────────────────────────────────────────────
const slides = [
  {
    badge: "MATRIX / GRID",
    badgeColor: "bg-blue-600",
    label: "Case Study — Heart Failure",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Clinical Scenario</p>
        <p className="mb-3">A <strong>72-year-old client</strong> admitted with acute decompensated heart failure has +2 pitting edema, bilateral crackles, and BP 158/92 mmHg. Select whether each action is <em>Indicated</em>, <em>Contraindicated</em>, or <em>Non-Essential</em>.</p>
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left py-1 pr-2 text-gray-500 font-medium">Nursing Action</th>
              <th className="text-center px-1 text-gray-500 font-medium w-16">Indicated</th>
              <th className="text-center px-1 text-gray-500 font-medium w-20">Contraind.</th>
              <th className="text-center px-1 text-gray-500 font-medium w-16">Non-Ess.</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["Elevate HOB to 45°", true, false, false],
              ["Give furosemide as ordered", true, false, false],
              ["Encourage 3 L fluid/day", false, true, false],
              ["Weigh patient daily", true, false, false],
              ["IV 0.9% NS bolus", false, true, false],
            ].map(([action, ind, contra], i) => (
              <tr key={i} className="border-b border-gray-100">
                <td className="py-1.5 pr-2 text-gray-700">{action as string}</td>
                {[ind, contra, false].map((val, j) => (
                  <td key={j} className="text-center py-1.5 px-1">
                    <div className={`w-3.5 h-3.5 rounded-full border-2 mx-auto flex items-center justify-center
                      ${val ? "border-blue-600 bg-blue-600" : "border-gray-300"}`}>
                      {val && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ 4/5 Correct</p>
        <p><strong>Elevate HOB 45°</strong> — Reduces preload and eases dyspnea in fluid-overloaded patients. <span className="text-green-600 font-medium">Indicated ✓</span></p>
        <p><strong>Furosemide</strong> — Loop diuretic; removes excess fluid. Primary HF treatment. <span className="text-green-600 font-medium">Indicated ✓</span></p>
        <p><strong>3 L fluids/day</strong> — Contraindicated. Fluid restriction (1–1.5 L/day) is standard. <span className="text-red-500 font-medium">Contraindicated ✓</span></p>
        <p><strong>Daily weight</strong> — 1 kg gain ≈ 1 L retained. Detects worsening early. <span className="text-green-600 font-medium">Indicated ✓</span></p>
      </div>
    ),
  },
  {
    badge: "BOW-TIE",
    badgeColor: "bg-purple-600",
    label: "Standalone — Diabetes Emergency",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Clinical Scenario</p>
        <p className="mb-3">A <strong>28-year-old</strong> Type 1 diabetic arrives confused, diaphoretic, trembling. Took insulin this morning but skipped breakfast. HR 102, BP 110/70.</p>
        <div className="grid grid-cols-3 gap-2 mt-2">
          {[
            { title: "Action to Take", color: "text-blue-600 border-blue-200 bg-blue-50", options: ["✓ Give 15g fast carbs", "Administer insulin", "Restrict oral intake"], correct: 0 },
            { title: "Condition", color: "text-purple-600 border-purple-200 bg-purple-50", options: ["Diabetic ketoacidosis", "✓ Hypoglycemia", "Hyperosm. state"], correct: 1 },
            { title: "Monitor", color: "text-green-600 border-green-200 bg-green-50", options: ["Serum potassium", "✓ Blood glucose", "Urine output"], correct: 1 },
          ].map((col, ci) => (
            <div key={ci}>
              <p className={`text-[10px] font-bold mb-1 ${col.color.split(" ")[0]}`}>{col.title}</p>
              <div className="space-y-1">
                {col.options.map((opt, oi) => (
                  <div key={oi} className={`text-[10px] px-2 py-1.5 rounded border ${oi === col.correct ? col.color : "border-gray-200 bg-white text-gray-600"}`}>
                    {opt}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ 3/3 Correct — Perfect!</p>
        <p><strong>Condition:</strong> Hypoglycemia — confusion + diaphoresis + trembling after insulin + missed meal is the classic presentation.</p>
        <p><strong>Action:</strong> Rule of 15 — give 15g fast-acting carbs (juice, glucose tabs), recheck glucose in 15 min. <em>Never</em> give more insulin.</p>
        <p><strong>Monitor:</strong> Blood glucose every 15 min until &gt;70 mg/dL. Level of consciousness is also critical.</p>
      </div>
    ),
  },
  {
    badge: "MULTIPLE SELECT",
    badgeColor: "bg-green-600",
    label: "Select All That Apply — Pharmacology",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Question</p>
        <p className="mb-3">A nurse is administering <strong>metformin</strong> to a client with Type 2 diabetes. Which findings require the nurse to <strong>hold the medication and notify the provider</strong>? Select all that apply.</p>
        <div className="space-y-1.5">
          {[
            { text: "Serum creatinine 2.8 mg/dL", correct: true },
            { text: "Blood glucose 142 mg/dL", correct: false },
            { text: "Scheduled CT scan with contrast today", correct: true },
            { text: "Client reports mild nausea", correct: false },
            { text: "eGFR of 28 mL/min/1.73m²", correct: true },
            { text: "Client ate a full breakfast", correct: false },
          ].map((opt, i) => (
            <div key={i} className={`flex items-center gap-2 px-2 py-1.5 rounded border text-[11px] ${opt.correct ? "border-green-400 bg-green-50 text-green-800" : "border-gray-200 bg-white text-gray-600"}`}>
              <div className={`w-3 h-3 rounded flex-shrink-0 flex items-center justify-center border ${opt.correct ? "border-green-500 bg-green-500" : "border-gray-300"}`}>
                {opt.correct && <CheckCircle className="w-2.5 h-2.5 text-white" />}
              </div>
              {opt.text}
            </div>
          ))}
        </div>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ 3 correct answers</p>
        <p><strong>Creatinine 2.8</strong> — Metformin is contraindicated with renal impairment (risk of lactic acidosis).</p>
        <p><strong>CT with contrast</strong> — Iodinated contrast can cause acute kidney injury; hold metformin 48h before and after.</p>
        <p><strong>eGFR 28</strong> — Metformin is contraindicated when eGFR &lt;30. Kidney function cannot clear the drug safely.</p>
        <p className="text-gray-500 italic">Nausea is a common side effect — not a reason to hold. Glucose of 142 and eating are not contraindications.</p>
      </div>
    ),
  },
  {
    badge: "ORDERING / DRAG & DROP",
    badgeColor: "bg-orange-500",
    label: "Priority Ordering — Code Response",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Question</p>
        <p className="mb-3">A client is found unresponsive and pulseless. Place the following nursing actions in the <strong>correct priority order</strong> (1 = first).</p>
        <div className="space-y-1.5">
          {[
            { n: "1", text: "Call for help / activate emergency response", color: "bg-blue-600" },
            { n: "2", text: "Begin high-quality chest compressions (100–120/min)", color: "bg-blue-500" },
            { n: "3", text: "Apply AED / defibrillator as soon as available", color: "bg-blue-400" },
            { n: "4", text: "Establish IV access for medication administration", color: "bg-blue-300" },
            { n: "5", text: "Administer epinephrine 1 mg IV every 3–5 min", color: "bg-blue-200" },
          ].map((step, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded border border-blue-200 bg-blue-50">
              <div className={`w-5 h-5 rounded-full ${step.color} text-white flex items-center justify-center text-[10px] font-bold flex-shrink-0`}>{step.n}</div>
              <span className="text-[11px] text-gray-700">{step.text}</span>
            </div>
          ))}
        </div>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ Correct Order — AHA Guidelines</p>
        <p><strong>1. Call for help</strong> — Activate the emergency response system immediately so advanced care is coming.</p>
        <p><strong>2. CPR first</strong> — Begin compressions within 10 seconds. Every minute without CPR decreases survival by 7–10%.</p>
        <p><strong>3. Defibrillate</strong> — Shockable rhythms (VF/pVT) respond to early defibrillation. Time is muscle.</p>
        <p><strong>4–5. IV/Meds</strong> — Vascular access and epinephrine come after the ABCs are established.</p>
      </div>
    ),
  },
  {
    badge: "CLOZE / DROP-DOWN",
    badgeColor: "bg-teal-600",
    label: "Fill-in-the-Blank — Respiratory Assessment",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Clinical Scenario</p>
        <p className="mb-4">A nurse is assessing a client with pneumonia. Complete the following documentation by selecting the correct term from each drop-down.</p>
        <div className="space-y-3">
          {[
            {
              prefix: "The nurse auscultates",
              selected: "crackles (rales)",
              options: ["crackles (rales)", "wheezes", "stridor"],
              correct: 0,
              suffix: "in the lower lung fields.",
            },
            {
              prefix: "This finding is consistent with",
              selected: "fluid in the alveoli",
              options: ["bronchospasm", "fluid in the alveoli", "upper airway obstruction"],
              correct: 1,
              suffix: ".",
            },
            {
              prefix: "The nurse should prioritize",
              selected: "oxygen therapy",
              options: ["ambulation", "oral hygiene", "oxygen therapy"],
              correct: 2,
              suffix: "as the next intervention.",
            },
          ].map((item, i) => (
            <div key={i} className="flex flex-wrap items-center gap-1 text-[11px] text-gray-700 leading-relaxed">
              <span>{item.prefix}</span>
              <span className="inline-flex items-center gap-1 bg-teal-100 border border-teal-400 text-teal-800 font-bold px-2 py-0.5 rounded">
                {item.selected} ▾
              </span>
              <span>{item.suffix}</span>
            </div>
          ))}
        </div>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ 3/3 Correct</p>
        <p><strong>Crackles (rales)</strong> — Discontinuous popping sounds caused by fluid-filled alveoli snapping open. Classic for pneumonia and pulmonary edema.</p>
        <p><strong>Fluid in the alveoli</strong> — Pneumonia causes exudate to fill air sacs, impairing gas exchange and producing crackles on auscultation.</p>
        <p><strong>Oxygen therapy</strong> — Hypoxia is the priority concern. Administer O₂ to maintain SpO₂ ≥ 95% before any other intervention.</p>
      </div>
    ),
  },
  {
    badge: "HOT SPOT / HIGHLIGHT",
    badgeColor: "bg-rose-500",
    label: "Enhanced Hot Spot — EHR Note",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-2">Question</p>
        <p className="mb-3">Read the nurse's note below. <strong>Click to highlight</strong> the findings that indicate a deteriorating neurological status.</p>
        <div className="bg-white border border-gray-200 rounded-lg p-3 text-[11px] leading-relaxed text-gray-700 space-y-1.5">
          <p className="font-bold text-gray-500 text-[10px] uppercase mb-2">Nurse's Note — 0800</p>
          <p>Client is a <span className="bg-yellow-200 px-0.5 rounded">65-year-old male</span> admitted with hypertension. VS: T 98.6°F, HR 88, BP <span className="bg-rose-200 border border-rose-400 px-0.5 rounded font-semibold text-rose-800">192/104 mmHg</span>, SpO₂ 97%. Client <span className="bg-rose-200 border border-rose-400 px-0.5 rounded font-semibold text-rose-800">reports sudden severe headache</span> rated 10/10, onset 20 minutes ago. Pupils <span className="bg-rose-200 border border-rose-400 px-0.5 rounded font-semibold text-rose-800">unequal — right 4mm, left 6mm</span>. Speech <span className="bg-rose-200 border border-rose-400 px-0.5 rounded font-semibold text-rose-800">slurred and difficult to understand</span>. Bowel sounds present ×4. Skin warm and dry. IV access patent in left AC.</p>
        </div>
        <p className="text-[10px] text-rose-600 font-semibold mt-2">🔴 Highlighted = correct findings to select</p>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-green-700 text-sm">✓ 4 correct findings identified</p>
        <p><strong>BP 192/104</strong> — Hypertensive crisis. Combined with neuro symptoms, raises concern for hemorrhagic stroke.</p>
        <p><strong>Sudden severe headache 10/10</strong> — "Thunderclap headache" is a hallmark warning sign of subarachnoid hemorrhage.</p>
        <p><strong>Unequal pupils</strong> — Anisocoria (right 4mm / left 6mm) indicates increased intracranial pressure or herniation risk.</p>
        <p><strong>Slurred speech</strong> — A stroke alert sign (FAST: Face, Arm, Speech, Time). Requires immediate provider notification.</p>
      </div>
    ),
  },
  {
    badge: "TREND",
    badgeColor: "bg-indigo-600",
    label: "Trend Analysis — Sepsis Progression",
    question: (
      <div className="text-xs text-gray-800 leading-relaxed">
        <p className="font-semibold text-gray-500 uppercase tracking-wider text-[10px] mb-1">Clinical Scenario</p>
        <p className="mb-3">Review the following vital sign trend for a <strong>58-year-old post-op client</strong>. Select what the data indicates and the priority nursing action.</p>
        <table className="w-full text-[11px] border-collapse mb-3">
          <thead>
            <tr className="bg-indigo-50 border-b border-indigo-200">
              <th className="text-left py-1.5 px-2 text-indigo-700 font-bold">Vital</th>
              <th className="text-center py-1.5 px-2 text-indigo-700 font-bold">0600</th>
              <th className="text-center py-1.5 px-2 text-indigo-700 font-bold">0800</th>
              <th className="text-center py-1.5 px-2 text-indigo-700 font-bold">1000</th>
              <th className="text-center py-1.5 px-2 text-indigo-700 font-bold">Trend</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["Temp (°F)", "98.8", "100.4", "102.6", "↑ 📈"],
              ["HR (bpm)", "78", "96", "118", "↑ 📈"],
              ["BP (mmHg)", "128/76", "112/70", "88/54", "↓ ⚠️"],
              ["RR (/min)", "14", "18", "24", "↑ 📈"],
              ["SpO₂ (%)", "98", "96", "91", "↓ ⚠️"],
            ].map(([vital, t1, t2, t3, trend], i) => (
              <tr key={i} className={`border-b border-gray-100 ${i % 2 === 0 ? "bg-white" : "bg-gray-50"}`}>
                <td className="py-1.5 px-2 font-medium text-gray-700">{vital}</td>
                <td className="text-center py-1.5 px-2 text-gray-500">{t1}</td>
                <td className="text-center py-1.5 px-2 text-gray-600">{t2}</td>
                <td className="text-center py-1.5 px-2 font-bold text-red-600">{t3}</td>
                <td className="text-center py-1.5 px-2">{trend}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[10px] text-gray-500 italic">What does this trend indicate and what is your first action?</p>
      </div>
    ),
    explanation: (
      <div className="text-xs text-gray-700 leading-relaxed space-y-2">
        <p className="font-bold text-red-600 text-sm">⚠️ Septic Shock — Notify provider STAT</p>
        <p><strong>Pattern = Sepsis → Septic Shock:</strong> Rising fever + tachycardia + falling BP + rising RR + dropping SpO₂ over 4 hours is the classic trajectory.</p>
        <p><strong>BP 88/54</strong> — Hypotension despite likely fluid resuscitation = septic shock. Mean arterial pressure (MAP) &lt; 65 is a critical threshold.</p>
        <p><strong>Priority action:</strong> Notify the rapid response team / provider immediately. Anticipate IV fluid bolus, blood cultures ×2, broad-spectrum antibiotics, and possible vasopressors.</p>
        <p className="text-indigo-700 font-semibold">Trend questions test your ability to recognize deterioration over time — a key NGN clinical judgment skill.</p>
      </div>
    ),
  },
];

function QuestionCarousel() {
  const [current, setCurrent] = useState(0);
  const prev = () => setCurrent((c) => (c === 0 ? slides.length - 1 : c - 1));
  const next = () => setCurrent((c) => (c === slides.length - 1 ? 0 : c + 1));
  const slide = slides[current];

  return (
    <section className="py-14 px-6 bg-white">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-8">
          <h2 className="text-3xl font-bold text-gray-900 mb-2">
            NCLEX Prep Questions That{" "}
            <span className="text-blue-600">Mirror the Real Exam</span>
          </h2>
          <p className="text-gray-600 text-xl font-medium max-w-2xl mx-auto">
            Every question type on the Next Generation NCLEX — with detailed AI explanations for every answer choice.
          </p>
        </div>

        {/* Carousel */}
        <div className="relative">
          {/* Card */}
          <div className="bg-white border border-gray-200 rounded-2xl shadow-lg overflow-hidden">
            {/* Badge row */}
            <div className={`${slide.badgeColor} px-6 py-3 flex items-center justify-between`}>
              <div className="flex items-center gap-3">
                <span className="bg-white bg-opacity-20 text-white text-xs font-bold px-3 py-1 rounded-full border border-white border-opacity-30">
                  {slide.badge}
                </span>
                <span className="text-white text-sm font-medium opacity-90">{slide.label}</span>
              </div>
              <span className="text-white text-xs opacity-70">{current + 1} / {slides.length}</span>
            </div>

            {/* Two-panel layout */}
            <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-gray-100">
              {/* Question panel */}
              <div className="p-6">
                <p className="text-[11px] font-bold text-gray-400 uppercase tracking-widest mb-3">Question</p>
                {slide.question}
              </div>

              {/* Explanation panel */}
              <div className="p-6 bg-blue-50 border-l-4 border-blue-400">
                <p className="text-[11px] font-bold text-blue-600 uppercase tracking-widest mb-3">🤖 AI Explanation</p>
                {slide.explanation}
              </div>
            </div>
          </div>

          {/* Arrow buttons */}
          <button
            onClick={prev}
            className="absolute left-0 top-1/2 -translate-y-1/2 -translate-x-5 w-10 h-10 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors z-10"
          >
            <ChevronLeft className="w-5 h-5 text-gray-600" />
          </button>
          <button
            onClick={next}
            className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-5 w-10 h-10 bg-white border border-gray-200 rounded-full shadow-md flex items-center justify-center hover:bg-gray-50 transition-colors z-10"
          >
            <ChevronRight className="w-5 h-5 text-gray-600" />
          </button>
        </div>

        {/* Try each type prompt */}
        <div className="mt-8 text-center">
          <div className="inline-flex items-center gap-2 bg-yellow-400 border-2 border-yellow-500 rounded-full px-6 py-3 mb-5 animate-bounce shadow-md">
            <span className="text-xl">👆</span>
            <span className="text-base font-extrabold text-yellow-900">Try each question type — click below!</span>
          </div>

          <div className="flex flex-wrap justify-center gap-3">
            {slides.map((s, i) => (
              <button
                key={i}
                onClick={() => setCurrent(i)}
                className={`flex items-center gap-2 px-6 py-3 rounded-xl border-2 font-extrabold text-sm transition-all shadow
                  ${i === current
                    ? `${s.badgeColor} text-white border-transparent shadow-lg scale-105`
                    : "border-gray-400 bg-white text-gray-700 hover:border-blue-500 hover:text-blue-700 hover:shadow-lg hover:scale-105"
                  }`}
              >
                {i === current ? "✓ " : ""}{s.badge}
              </button>
            ))}
          </div>
          <p className="text-sm font-bold text-gray-700 mt-4">Each one appears on the real Next Generation NCLEX</p>
        </div>
      </div>
    </section>
  );
}

// ── Landing Page ───────────────────────────────────────────────────────────────
export default function Landing() {
  return (
    <div className="min-h-screen bg-white">
      {/* Top bar */}
      <div className="bg-blue-600 text-white text-center text-sm py-2 px-4 font-medium">
        🎉 Try 10 questions free — no sign-up needed
      </div>

      {/* Nav */}
      <nav className="flex items-center justify-between px-6 py-4 border-b border-gray-100 max-w-5xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <Brain className="w-5 h-5 text-white" />
          </div>
          <span className="font-bold text-gray-900 text-xl">NCLEX AI</span>
          <span className="text-2xl font-extrabold text-blue-600 ml-1">nclexai.org</span>
        </div>
        <Link href="/quiz">
          <Button size="sm" className="bg-blue-600 hover:bg-blue-700 text-white">
            Start Free
          </Button>
        </Link>
      </nav>

      {/* Member login bar */}
      <div className="bg-gray-900 text-white text-center py-2 px-4">
        <Link href="/home">
          <span className="font-bold text-sm cursor-pointer hover:underline">Already a member? Click here to log in →</span>
        </Link>
      </div>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-6 pt-8 pb-10 text-center">
        <div className="flex flex-wrap justify-center gap-3 mb-4">
          <div className="inline-flex items-center gap-2 bg-green-50 text-green-700 text-sm font-medium px-4 py-1.5 rounded-full">
            <ShieldCheck className="w-4 h-4 text-green-600" />
            Created by a Registered Nurse
          </div>
          <div className="inline-flex items-center gap-2 bg-blue-50 text-blue-700 text-sm font-medium px-4 py-1.5 rounded-full">
            <Star className="w-4 h-4 fill-blue-600 text-blue-600" />
            2,778+ questions · NGN &amp; CAT format
          </div>
        </div>

        <h1 className="text-4xl sm:text-5xl font-extrabold text-gray-900 leading-tight mb-4">
          Pass the NCLEX on{" "}
          <span className="text-blue-600">your first attempt</span>
        </h1>

        <p className="text-lg text-gray-600 mb-6 max-w-xl mx-auto">
          AI explains why each wrong answer is wrong — just like the real NCLEX tests your reasoning.
        </p>

        <Link href="/quiz">
          <Button
            size="lg"
            className="bg-blue-600 hover:bg-blue-700 text-white text-xl px-12 py-7 rounded-xl shadow-lg shadow-blue-200 w-full sm:w-auto"
          >
            Start 10 Questions Free
            <ArrowRight className="w-5 h-5 ml-2" />
          </Button>
        </Link>

        <p className="text-sm text-blue-700 font-semibold mt-3 mb-4">
          or unlock everything — $15/mo or $49 one-time lifetime access
        </p>

        <div className="inline-flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-xl px-5 py-3 mt-1">
          <div className="flex gap-0.5">
            {[1,2,3,4,5].map(s => <Star key={s} className="w-3.5 h-3.5 fill-yellow-400 text-yellow-400" />)}
          </div>
          <p className="text-sm text-gray-700 font-medium">"The questions looked identical to what I saw on test day." — James T., BSN</p>
        </div>
      </section>

      {/* Question Preview Carousel */}
      <QuestionCarousel />

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
            <div className="text-3xl font-extrabold text-gray-900">CAT</div>
            <div className="text-sm text-gray-500 mt-1">Adaptive Testing</div>
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

      {/* ── NGN Demo Section ─────────────────────────────────────────────────── */}
      <section className="bg-gray-50 border-y border-gray-100 py-16 px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-10">
            <span className="inline-block bg-blue-100 text-blue-700 text-xs font-bold uppercase tracking-widest px-4 py-1.5 rounded-full mb-3">
              Try It Live — No Sign-Up
            </span>
            <h2 className="text-3xl font-bold text-gray-900 mb-3">
              The new NCLEX asks questions like these
            </h2>
            <p className="text-gray-500 text-lg max-w-2xl mx-auto mb-5">
              Matrix/Grid and Bow-Tie questions are on the Next Generation NCLEX.
              Most prep books don't cover them. We do — with AI explanations for every answer.
            </p>
            <div className="flex flex-wrap justify-center gap-3 max-w-2xl mx-auto">
              <div className="bg-blue-50 border border-blue-200 rounded-xl px-5 py-3 text-sm text-left">
                <p className="font-bold text-blue-800 mb-0.5">📊 Matrix / Grid</p>
                <p className="text-blue-700">The real NCLEX includes <strong>3 scored case studies</strong> — each with 6 matrix questions (18 total). You must evaluate nursing interventions using a grid.</p>
              </div>
              <div className="bg-purple-50 border border-purple-200 rounded-xl px-5 py-3 text-sm text-left">
                <p className="font-bold text-purple-800 mb-0.5">🎀 Bow-Tie</p>
                <p className="text-purple-700">Bow-Tie questions ask you to connect a <strong>condition → action → monitoring parameter</strong> in one question. Up to 2 standalone bow-ties appear on your exam.</p>
              </div>
            </div>
          </div>

          <div className="space-y-8">
            <MatrixDemo />
            <BowtieDemo />
          </div>

          <div className="text-center mt-10">
            <Link href="/quiz">
              <Button
                size="lg"
                className="bg-blue-600 hover:bg-blue-700 text-white text-lg px-10 py-6 rounded-xl shadow-lg shadow-blue-200"
              >
                Start 10 Free Questions
                <ArrowRight className="w-5 h-5 ml-2" />
              </Button>
            </Link>
            <p className="text-gray-400 text-sm mt-3">No sign-up · Free forever up to 10 questions</p>
          </div>
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
          Start with 10 free questions right now. No sign-up needed.
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
        <p className="text-white text-2xl font-extrabold mt-6 tracking-wide">
          nclexai.org
        </p>
        <p className="text-gray-400 text-sm mt-1">
          $15/mo or $49 lifetime after free trial
        </p>
      </section>
    </div>
  );
}
