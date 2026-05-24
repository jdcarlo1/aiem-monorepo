---
name: NCLEX Prep project state
description: Current question bank size, all 28 categories, next question number, tech stack, question types, and key constraints for the NCLEX Prep app
---

## Project Overview
Full-stack NCLEX Prep web app with freemium model (5 free questions, then $10/month). Stripe not yet connected. Branded as "NCLEX AI" with AI-powered theme.

## Tech Stack
- Frontend: React + Vite (wouter routing, TanStack Query, Shadcn UI) — artifact: `artifacts/nclex-prep`
- Backend: Express 5 API server — artifact: `artifacts/api-server`
- ORM: Drizzle ORM + PostgreSQL
- Session tracking: UUID in localStorage as "nclex_session_id" — no auth required

## Key Files
- `artifacts/api-server/src/routes/session.ts` — free limit enforcement (5 questions), multi-type answer checking
- `artifacts/api-server/src/routes/questions.ts` — question routes (returns questionType field)
- `lib/db/src/schema/questions.ts` — DB schema (includes questionType column)
- `lib/db/src/schema/sessions.ts` — sessions schema
- `lib/api-client-react/src/generated/api.schemas.ts` — manually maintained (no codegen script)
- `artifacts/nclex-prep/src/pages/home.tsx` — AI-themed landing page with testimonials + comparison
- `artifacts/nclex-prep/src/pages/quiz.tsx` — quiz engine with 3 question type renderers

## Database Schema (questions table)
- id (serial PK)
- question_number (int) — NOT a unique constraint
- category (text)
- text (text)
- options (jsonb array of {letter, text})
- correct_letter (text) — for 'multiple': sorted comma-sep "A,C,D"; for 'ordered': "1,2,3,4"
- explanation (text)
- question_type (text, default 'single') — values: 'single' | 'multiple' | 'ordered'

## Answer Type Encoding
- 'single': correctLetter = "A" (one letter)
- 'multiple': correctLetter = sorted comma-separated letters "A,C,D"; server sorts both sides before comparing
- 'ordered': correctLetter = correct position order "1,2,3,4,5"; items use numeric letters; direct string compare

## Question Bank State
- **Total questions: 593**
- **Total categories: 28**
- **Last question number used: 593**
- **Next questions should start at: 594**

## All 28 Categories
1. Burn Unit Nursing (20 questions)
2. Cardiac Surgery Nursing (20)
3. Chest Tube Nursing (20)
4. Dermatology (20)
5. Diabetes (20)
6. Fundamentals (20)
7. Gastroenterology (20)
8. Geriatric Nursing (20)
9. Hematology-Oncology (20)
10. High-Frequency NCLEX (20)
11. ICU Nursing (20)
12. Integumentary System (20)
13. Leadership (20)
14. Lymphatic System (20)
15. Maternal-Newborn (20)
16. Maternity Nursing (20)
17. Medical-Surgical (20)
18. Mental Health (20)
19. Nervous System (20)
20. NGN - Clinical Judgment (53 — mixed: 20 single, 20 multiple, 13 ordered)
21. Ophthalmology (20)
22. Orthopedic Nursing (20)
23. Pediatric Nursing (20)
24. Pediatrics (20)
25. Pharmacology (20)
26. Psychiatric Nursing (20)
27. Reproductive System (20)
28. Urology (20)

## Landing Page (home.tsx)
- Branding: "NCLEX AI" with Brain icon
- Hero: "The Smarter Way to Pass Your NCLEX"
- 6-feature grid, AI vs Traditional comparison table, 4 testimonials, CTA

## Business Logic
- Free limit = 5 questions, enforced server-side in `session.ts`
- `/api/subscription/checkout` returns a placeholder message — Stripe NOT connected
- Artifact ID for presenting: `artifacts/nclex-prep`

**Why:** User explicitly asked this to be saved so context isn't lost across sessions.
