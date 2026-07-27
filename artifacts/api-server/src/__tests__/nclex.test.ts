import { describe, it, expect } from "vitest";
import { checkAnswer, adaptiveWeight, commissionCents } from "../lib/checkAnswer";

// ── checkAnswer ───────────────────────────────────────────────────────────────

describe("checkAnswer — single choice", () => {
  it("correct answer returns true", () => {
    expect(checkAnswer("single", "B", "B")).toBe(true);
  });

  it("wrong answer returns false", () => {
    expect(checkAnswer("single", "B", "C")).toBe(false);
  });

  it("trims whitespace before comparing", () => {
    expect(checkAnswer("single", " A ", " A")).toBe(true);
  });
});

describe("checkAnswer — ordered (drag-and-drop)", () => {
  it("exact sequence returns true", () => {
    expect(checkAnswer("ordered", "A,B,C,D", "A,B,C,D")).toBe(true);
  });

  it("wrong sequence returns false", () => {
    expect(checkAnswer("ordered", "A,B,C,D", "A,C,B,D")).toBe(false);
  });
});

describe("checkAnswer — multiple choice (SATA)", () => {
  it("exact match regardless of submission order returns true", () => {
    expect(checkAnswer("multiple", "A,C,E", "E,C,A")).toBe(true);
  });

  it("missing option returns false", () => {
    expect(checkAnswer("multiple", "A,C,E", "A,C")).toBe(false);
  });

  it("extra option returns false", () => {
    expect(checkAnswer("multiple", "A,C", "A,C,E")).toBe(false);
  });

  it("handles spaces around letters", () => {
    expect(checkAnswer("multiple", "A, C, E", "E, A, C")).toBe(true);
  });
});

// ── adaptiveWeight ────────────────────────────────────────────────────────────

describe("adaptiveWeight — category weighting", () => {
  it("returns 0.55 with no history", () => {
    expect(adaptiveWeight(0, 0)).toBe(0.55);
  });

  it("returns 1.05 for 0% accuracy (all wrong)", () => {
    // (1 - 0)^2 + 0.05 = 1.05
    expect(adaptiveWeight(0, 10)).toBeCloseTo(1.05);
  });

  it("returns 0.05 floor for 100% accuracy (all correct)", () => {
    // (1 - 1)^2 + 0.05 = 0.05
    expect(adaptiveWeight(10, 10)).toBeCloseTo(0.05);
  });

  it("returns higher weight for weaker categories", () => {
    const weak = adaptiveWeight(2, 10);   // 20% accuracy
    const strong = adaptiveWeight(8, 10); // 80% accuracy
    expect(weak).toBeGreaterThan(strong);
  });

  it("weight is always > 0 (mastered categories still appear)", () => {
    expect(adaptiveWeight(10, 10)).toBeGreaterThan(0);
  });
});

// ── commissionCents ───────────────────────────────────────────────────────────

describe("commissionCents — affiliate commission calculation", () => {
  it("50% commission on $110 lifetime = $55", () => {
    expect(commissionCents(11000, 50)).toBe(5500);
  });

  it("50% commission on $22 monthly = $11", () => {
    expect(commissionCents(2200, 50)).toBe(1100);
  });

  it("30% commission rounds down (no fractional cents)", () => {
    // 30% of $10.01 = $3.003 → floor → $3
    expect(commissionCents(1001, 30)).toBe(300);
  });

  it("0 amount produces 0 commission", () => {
    expect(commissionCents(0, 50)).toBe(0);
  });

  it("100% commission returns full amount", () => {
    expect(commissionCents(5000, 100)).toBe(5000);
  });
});

// ── Free-limit gate logic ─────────────────────────────────────────────────────

describe("free-limit enforcement — canAnswer logic", () => {
  const FREE_LIMIT = 10;

  function canAnswer(isSubscribed: boolean, questionsAnswered: number): boolean {
    return isSubscribed || questionsAnswered < FREE_LIMIT;
  }

  it("allows question 1 for anonymous user", () => {
    expect(canAnswer(false, 0)).toBe(true);
  });

  it("allows question 10 (last free) for anonymous user", () => {
    expect(canAnswer(false, 9)).toBe(true);
  });

  it("blocks question 11 for anonymous user", () => {
    expect(canAnswer(false, 10)).toBe(false);
  });

  it("allows question 11+ for subscribed user", () => {
    expect(canAnswer(true, 10)).toBe(true);
    expect(canAnswer(true, 1000)).toBe(true);
  });

  it("boundary: exactly at limit is blocked", () => {
    expect(canAnswer(false, FREE_LIMIT)).toBe(false);
  });

  it("boundary: one below limit is allowed", () => {
    expect(canAnswer(false, FREE_LIMIT - 1)).toBe(true);
  });
});
