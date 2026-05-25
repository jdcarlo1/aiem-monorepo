---
name: NCLEX Prep project state
description: Current question bank size, all categories, next question number, tech stack, question types, and key constraints for the NCLEX Prep app
---

## Project Overview
Full-stack nursing platform "NCLEX AI" with 3 modes: Nursing School question banks, NCLEX Prep, Interview Prep. Freemium (5 free questions, then $15/mo or $49 lifetime). Stripe backend is wired but not yet connected via Integrations tab.

## Tech Stack
- Frontend: React + Vite (wouter routing, TanStack Query, Shadcn UI) — artifact: `artifacts/nclex-prep`
- Backend: Express 5 API server — artifact: `artifacts/api-server`
- ORM: Drizzle ORM + PostgreSQL
- Session tracking: UUID in localStorage as "nclex_session_id" — no auth required

## Key Files
- `artifacts/api-server/src/routes/session.ts` — free limit enforcement (5 questions), multi-type answer checking
- `artifacts/api-server/src/routes/questions.ts` — question routes (returns questionType field)
- `artifacts/api-server/src/routes/stripe.ts` — Stripe checkout endpoint
- `artifacts/api-server/src/stripeClient.ts` — reads Stripe creds from Replit connector
- `artifacts/api-server/src/webhookHandlers.ts` — Stripe webhook + session update logic
- `lib/db/src/schema/questions.ts` — DB schema (includes questionType column)
- `lib/db/src/schema/sessions.ts` — sessions schema (has stripeCustomerId, stripeSubscriptionId)
- `artifacts/nclex-prep/src/pages/nursing-school.tsx` — 24-category nursing school page (7 sections)
- `artifacts/nclex-prep/src/pages/paywall.tsx` — calls /api/stripe/checkout, redirects to Stripe URL
- `scripts/src/seed-products.ts` — run after connecting Stripe to create products

## Database Schema (questions table)
- id (serial PK)
- question_number (int) — NOT a unique constraint
- category (text)
- text (text)
- options (jsonb — {"A":"...","B":"...","C":"...","D":"..."})
- correct_letter (text) — for 'multiple': sorted comma-sep "A,C,D"; for 'ordered': "1,2,3,4"
- explanation (text)
- question_type (text, default 'single') — values: 'single' | 'multiple' | 'ordered'

## Answer Type Encoding
- 'single': correctLetter = "A" (one letter)
- 'multiple': correctLetter = sorted comma-separated letters "A,C,D"; server sorts both sides before comparing
- 'ordered': correctLetter = correct position order "1,2,3,4,5"; items use numeric letters; direct string compare

## Question Bank State
- **Total questions in DB: ~1333 (mix of NCLEX and nursing school)**
- **Next question number to use: 1334**
- Nursing school questions use question_type='single' and CLIENT-SIDE answer checking (no submitAnswer call — avoids corrupting NCLEX session counter)
- Interview prep also uses client-side checking

## Nursing School Page — 24 Categories (7 sections)

**Semester 1 — Fundamentals (1 category)**
1. Fundamentals of Nursing (30 questions)

**Medical-Surgical — By Body System (9 categories)**
2. MedSurg: Cardiac (30)
3. MedSurg: Respiratory (30)
4. MedSurg: Neurological (30)
5. MedSurg: Endocrine (30)
6. MedSurg: Renal & Urology (30)
7. MedSurg: Gastrointestinal (30)
8. MedSurg: Burns & Integumentary (30)
9. MedSurg: Orthopedic (30)
10. MedSurg: Chest Tubes (30)

**Infectious Disease (2 categories)**
11. Infectious Disease: Tuberculosis (30)
12. Infectious Disease: HIV/AIDS (30)

**Specialty Nursing (4 categories)**
13. Pediatric Nursing (30)
14. Maternity & OB Nursing (30)
15. Psychiatric/Mental Health (30)
16. Oncology Nursing (30)

**Advanced Practice (2 categories)**
17. Critical Care/ICU (30)
18. Fluid & Electrolytes (30)

**Clinical Reasoning (2 categories)**
19. ABG Interpretation (30) — Q1274–Q1303
20. EKG Interpretation (30) — Q1304–Q1333

**Pharmacology (4 categories)**
21. Pharmacology: Cardiac Meds (30)
22. Pharmacology: Respiratory Meds (30)
23. Pharmacology: Diabetes & Insulin (30)
24. Pharmacology: Anticoagulation (30)

## NCLEX Prep Categories (legacy — 613+ questions, various types)
Burn Unit Nursing, Cardiac Surgery, Chest Tube Nursing, Dermatology, Diabetes, Fundamentals, Gastroenterology, Geriatric, Hematology-Oncology, High-Frequency NCLEX, ICU Nursing, Integumentary, Leadership, Lymphatic, Maternal-Newborn, Maternity Nursing, Medical-Surgical, Mental Health, Nervous System, NGN-Clinical Judgment (53, mixed types), Ophthalmology, Orthopedic Nursing, Pediatrics, Pharmacology, Psychiatric Nursing, Reproductive System, Urology

## Business Logic
- Free limit = 5 questions, enforced server-side in session.ts
- Nursing school + interview prep: client-side answer checking only (do NOT call /api/session/submit)
- Stripe: stripeClient.ts reads from REPLIT_CONNECTORS_HOSTNAME; user must connect via Integrations tab
- seed-products.ts must be run after Stripe is connected

**Why:** User explicitly asked this to be saved so context isn't lost across sessions.
