/**
 * Pure answer-checking function, exported for unit testing.
 * session.ts delegates to this so it can be tested without HTTP overhead.
 */
export function checkAnswer(
  questionType: string,
  correctLetter: string,
  selectedLetter: string
): boolean {
  if (questionType === "multiple") {
    // SATA — order-insensitive CSV comparison
    const correct = correctLetter
      .split(",")
      .map((s) => s.trim())
      .sort()
      .join(",");
    const selected = selectedLetter
      .split(",")
      .map((s) => s.trim())
      .sort()
      .join(",");
    return correct === selected;
  }
  // "single" and "ordered" — exact string match after trimming
  return correctLetter.trim() === selectedLetter.trim();
}

/**
 * Weight formula used by the adaptive engine.
 * Exported for unit testing.
 * weight = (1 - accuracy)^2 + 0.05 floor
 * Guarantees weight ∈ [0.05, 1.05]; fully-mastered categories still appear ~5%.
 */
export function adaptiveWeight(correctCount: number, totalCount: number): number {
  if (totalCount === 0) return 0.55; // No history → slightly above neutral
  const acc = correctCount / totalCount;
  return Math.pow(1 - acc, 2) + 0.05;
}

/**
 * Commission calculation used in sendAffiliateTransfer.
 * Exported for unit testing.
 */
export function commissionCents(amountCents: number, commissionPct: number): number {
  return Math.floor(amountCents * (commissionPct / 100));
}
