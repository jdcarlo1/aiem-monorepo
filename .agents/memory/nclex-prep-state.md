---
name: NCLEX Prep project state
description: Current question bank size, all 27 categories, next question number, tech stack, and key constraints for the NCLEX Prep app
---

## Project Overview
Full-stack NCLEX Prep web app with freemium model (5 free questions, then $10/month). Stripe not yet connected.

## Tech Stack
- Frontend: React + Vite (wouter routing, TanStack Query, Shadcn UI) — artifact: `artifacts/nclex-prep`
- Backend: Express 5 API server — artifact: `artifacts/api-server`
- ORM: Drizzle ORM + PostgreSQL
- Session tracking: UUID in localStorage as "nclex_session_id" — no auth required

## Key Files
- `artifacts/api-server/src/routes/session.ts` — free limit enforcement (5 questions)
- `artifacts/api-server/src/routes/questions.ts` — question routes
- `lib/db/src/schema/questions.ts` — DB schema
- `lib/db/src/schema/sessions.ts` — sessions schema
- `artifacts/nclex-prep/src/App.tsx`
- `artifacts/nclex-prep/src/pages/quiz.tsx`
- `lib/api-spec/openapi.yaml`

## Database Schema (questions table)
- id (serial PK)
- question_number (int) — NOT a unique constraint
- category (text)
- text (text)
- options (jsonb array of {letter, text})
- correct_letter (text)
- explanation (text)

## Question Bank State
- **Total questions: 540**
- **Total categories: 27**
- **Last question number used: 540**
- **Next questions should start at: 541**

## All 27 Categories (20 questions each)
1. Burn Unit Nursing (Q521–Q540)
2. Cardiac Surgery Nursing
3. Chest Tube Nursing
4. Dermatology
5. Diabetes
6. Fundamentals
7. Gastroenterology
8. Geriatric Nursing (Q461–Q480)
9. Hematology-Oncology
10. High-Frequency NCLEX
11. ICU Nursing (Q501–Q520)
12. Integumentary System
13. Leadership
14. Lymphatic System
15. Maternal-Newborn
16. Maternity Nursing (Q481–Q500)
17. Medical-Surgical
18. Mental Health
19. Nervous System
20. Ophthalmology
21. Orthopedic Nursing
22. Pediatric Nursing
23. Pediatrics
24. Pharmacology
25. Psychiatric Nursing
26. Reproductive System
27. Urology

## Business Logic
- Free limit = 5 questions, enforced server-side in `session.ts`
- `/api/subscription/checkout` returns a placeholder message — Stripe NOT connected
- Artifact ID for presenting: `artifacts/nclex-prep`

**Why:** User explicitly asked this to be saved so context isn't lost across turns/sessions.
