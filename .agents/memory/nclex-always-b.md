---
name: NCLEX always-B root cause
description: Why 72% of NCLEX questions had the correct answer at position B, and the definitive fix
---

## The problem
The question-generation script always placed the correct answer at letter "B" in the options array, regardless of content. Result: 1,082 of 1,508 questions had `correct_letter = "B"` — users could always guess B.

Frontend shuffle code was correct but insufficient on its own because:
- React Query sometimes returns cached data synchronously, making the useEffect fire timing uncertain
- The fix must be at the data layer, not just the display layer

## The fix (applied June 25, 2026)
PL/pgSQL DO block: for every question where `question_type IN ('single', 'multiple')`:
1. Normalize old object-format options `{"A": "text"}` → array format `[{letter, text}]`
2. Fisher-Yates shuffle the options array
3. Reassign letter labels sequentially (A, B, C, D…) to the shuffled positions
4. Update `correct_letter` to reflect new position of the correct option(s)
5. Ordered questions (`question_type = 'ordered'`) are NEVER touched

Result: A=316, B=359, C=381, D=354 — roughly equal distribution.

## Explanation text cleanup
~10 questions had "Option B", "Option C" etc. in the explanation text. After the shuffle these references are wrong. Fix: replace with generic language ("The correct response...", remove "(Option D — ..." parentheticals).

## MultipleChoice circle fix
quiz.tsx MultipleChoice component was rendering `opt.letter` (stored) instead of `displayLabel` (shuffled visual position) in the button circle. Fixed to use `displayLabel`.

**Why:** Data fix is the only reliable solution. Frontend shuffle alone can fail due to React Query cache/timing. Always fix data bugs at the source.

**How to apply:** If new questions are added in bulk, run the same PL/pgSQL shuffle migration afterward to avoid re-introducing the bias.
