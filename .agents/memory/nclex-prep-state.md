---
name: NCLEX Prep project state
description: Current question bank size, all categories, next question number, tech stack, question types, and key constraints for the NCLEX Prep app
---

## Project Overview
Full-stack nursing platform "NCLEX AI" with 3 modes: Nursing School question banks, NCLEX Prep, Interview Prep. Freemium (5 free questions, then $15/mo or $49 lifetime). Stripe live payments working. Clerk auth fully integrated.

## Tech Stack
- Frontend: React + Vite (wouter routing, TanStack Query, Shadcn UI) — artifact: `artifacts/nclex-prep`
- Backend: Express 5 API server — artifact: `artifacts/api-server`
- ORM: Drizzle ORM + PostgreSQL
- Auth: Clerk (provisioned, app_3EEtvOk987ELQSncQZqVjiIuB02). useSessionId() hook returns Clerk userId when signed in, localStorage UUID when not.
- Payments: Stripe live key (STRIPE_SECRET_KEY env var). Products: prod_UaJXZTmKruPrEx (monthly), prod_UaJXMqt2zCm0Q4 (lifetime).

## Clerk Auth — Key Implementation Details
- `useSessionId()` hook at `artifacts/nclex-prep/src/hooks/useSessionId.ts` — returns Clerk userId when signed in, localStorage UUID when not
- Sign-in page: `/sign-in`, Sign-up page: `/sign-up` — both use Clerk's SignIn/SignUp components with routing="path"
- ClerkProvider wraps entire app in App.tsx; proxy middleware mounted in api-server/src/app.ts before body parsers
- `index.css` has `@layer theme, base, clerk, components, utilities;` before `@import "tailwindcss"`
- `vite.config.ts` has `tailwindcss({ optimize: false })` to prevent prod build breakage
- logo.svg at `artifacts/nclex-prep/public/logo.svg`
- clerkPubKey uses `publishableKeyFromHost` from `@clerk/react/internal` — never raw env var
- proxyUrl is `import.meta.env.VITE_CLERK_PROXY_URL` unconditionally — empty in dev, auto-set in prod
- Home page header shows "Sign In" when signed out, username + "Sign Out" when signed in

## Key Files
- `artifacts/api-server/src/routes/session.ts` — free limit enforcement (5 questions), multi-type answer checking
- `artifacts/api-server/src/routes/questions.ts` — question routes; normalizes options format at API layer (see below); returns imageUrl
- `artifacts/api-server/src/routes/stripe.ts` — Stripe checkout, verify-checkout (returns email), restore-access endpoints; admin seed endpoint accepts imageUrl
- `artifacts/api-server/src/stripeClient.ts` — reads Stripe creds from STRIPE_SECRET_KEY env var first (sk_live_), falls back to connector
- `artifacts/api-server/src/webhookHandlers.ts` — Stripe webhook + session update logic
- `artifacts/api-server/src/app.ts` — Clerk proxy + clerkMiddleware wired in
- `lib/db/src/schema/questions.ts` — DB schema (includes questionType + imageUrl columns)
- `lib/db/src/schema/sessions.ts` — sessions schema (has stripeCustomerId, stripeSubscriptionId)
- `artifacts/nclex-prep/src/components/EkgDisplay.tsx` — self-contained SVG ECG strip renderer; takes `rhythm` prop; 12 supported rhythms: normal, bradycardia, tachycardia, afib, flutter, svt, pvcs, vtach, vfib, block1, block3, stemi
- `artifacts/nclex-prep/src/pages/nursing-school.tsx` — 30-category nursing school page (includes NGN formats + EKG Strip Recognition with badge)
- `artifacts/nclex-prep/src/pages/study-quiz.tsx` — supports all 3 question types + EKG/image rendering above question text
- `artifacts/nclex-prep/src/pages/quiz.tsx` — main NCLEX quiz; supports EKG/image rendering above question text
- `artifacts/nclex-prep/src/pages/paywall.tsx` — calls /api/stripe/checkout, has "Restore Access" (stores email to localStorage on success)
- `artifacts/nclex-prep/src/pages/subscribe-success.tsx` — calls verify-checkout, stores payment email to localStorage
- `artifacts/nclex-prep/src/hooks/useAutoRestore.ts` — two hooks: useAutoRestore (quiz page, fires when canAnswerMore=false) and useEagerRestore (fires on mount when isSubscribed is falsy)
- `artifacts/nclex-prep/src/lib/session.ts` — getSessionId(), getPaymentEmail(), setPaymentEmail()
- `scripts/src/seed-products-live.ts` — seeds products to live Stripe account (use this, NOT seed-products.ts)

## CRITICAL: Options Format Bug (fixed in API, do not revert)
Old seeded questions store options as `{"A": "text", "B": "text"}` (plain object).
New questions store options as `[{letter: "A", text: "..."}]` (array).
The fix is in `artifacts/api-server/src/routes/questions.ts` GET /questions/:id — normalizes to array before returning.
**Never assume options are arrays in the DB. Always normalize at the API layer.**
Removing this normalization will cause blank page crashes in the quiz.

## Subscription System — How It Works
- Session ID = Clerk userId (signed in) or localStorage UUID (anonymous)
- On payment: verify-checkout endpoint marks session as subscribed + returns customer email
- Email stored in localStorage as `nclex_payment_email`
- `useEagerRestore` hook on home, nursing-school, study-quiz, interview-prep pages: if isSubscribed=false AND email in localStorage → silently POSTs to /api/stripe/restore-access → invalidates session status query
- "Restore Access" button on paywall: enter payment email → marks current session subscribed + stores email
- Root issue: Stripe webhooks not firing → webhook never activates subscription. Restore Access + auto-restore are the workaround.
- All existing UUID sessions were bulk-activated in DB on 2026-05-27 as emergency fix.

## Key Constraints — Do NOT Change Without User Approval
- FREE_LIMIT stays at 5 questions — user explicitly rejected changing to 10
- Do NOT re-run `seed-products.ts` (test keys) — use `seed-products-live.ts`
- User is very sensitive to breaking changes — always test thoroughly before publishing
- Always run a full category check script across all 27 categories before publishing any question/API changes
- Admin endpoints secured with header `x-admin-secret: nclexai-admin-2026`

## Database Schema (questions table)
- id (serial PK)
- question_number (int) — NOT a unique constraint
- category (text)
- text (text)
- options (jsonb) — TWO formats exist: old `{"A":"..."}`, new `[{letter,text}]` — API normalizes
- correct_letter (text) — for 'multiple': sorted comma-sep "A,C,D"; for 'ordered': correct sequence "B,A,D,C"
- explanation (text)
- question_type (text, default 'single') — values: 'single' | 'multiple' | 'ordered'
- image_url (text, nullable) — 'ekg:rhythm_name' for EKG strips, or external URL for photos

## imageUrl / EKG System
- DB column: `image_url` (text, nullable)
- Values: `ekg:vfib`, `ekg:afib`, `ekg:vtach`, `ekg:normal`, `ekg:bradycardia`, `ekg:tachycardia`, `ekg:svt`, `ekg:flutter`, `ekg:pvcs`, `ekg:block1`, `ekg:block3`, `ekg:stemi`
- Frontend: quiz.tsx and study-quiz.tsx both check `imageUrl?.startsWith('ekg:')` and render `<EkgDisplay rhythm={imageUrl.slice(4)} />`, else `<img src={imageUrl} />` for external images
- EkgDisplay.tsx also accepts aliases: normal-sinus, sinus-bradycardia, etc.
- API returns imageUrl in GET /questions/:id response
- Seed endpoint (POST /api/admin/seed-questions) accepts `imageUrl` field
- **Production note**: image_url column added to dev DB. Will be applied to production DB on next Publish. After publishing, must re-seed EKG questions to production (20 questions, Q#1494–1513, category "EKG Strip Recognition").

## Answer Type Encoding
- 'single': correctLetter = "A" (one letter)
- 'multiple': correctLetter = sorted comma-separated letters "A,C,D"; server and client both sort before comparing
- 'ordered': correctLetter = correct sequence of item letters e.g. "C,B,A,D,E"; direct string compare after sorting both sides

## Question Bank State (verified 2026-05-27)
- 33 nursing school categories (includes Nursing Skills Lab, Wound Care Management, Dosage Calculations, EKG, SATA, Drag & Drop)
- EKG Strip Recognition: 20 questions (Q#1494–1513)
- Dosage Calculations: 30 questions (Q#1514–1543)
- Nursing Skills Lab: 50 questions (Q#1544–1623, includes ostomy care Q#1614–1623) — seeded to production
- Wound Care Management: 30 questions (Q#1584–1613) — seeded to production
- 20 Nursing Interview Prep questions (category: "Nursing Interview Prep")
- 5 hard "hook" questions at questionNumbers -5 through -1 — always appear first in main NCLEX quiz
- Next question number to use: 1624
- totalQuestions formula: totalCategories * 30 (EKG -10 and SkillsLab +10 cancel out)
- Nursing school + interview prep use CLIENT-SIDE answer checking (no submitAnswer API call)
- study-quiz.tsx supports all 3 question types AND shows polished results screen (pass = 75%)

## Results Screen (study-quiz.tsx)
- Shows after completing all questions in a category
- Displays score as large XX% with pass/fail badge (75% threshold = passing, mirrors NCLEX)
- Score bar with 75% passing marker, 3-stat grid (correct/missed/total), contextual message
- Green = passing (≥75%), amber = close (60-74%), red = below 60%
- Retry and Choose Another Section buttons

## All 30 Nursing School Categories (exact strings — must match DB)
Fundamentals of Nursing, MedSurg: Cardiac, MedSurg: Respiratory, MedSurg: Neurological,
MedSurg: Endocrine, MedSurg: Renal & Urology, MedSurg: Gastrointestinal,
MedSurg: Burns & Integumentary, MedSurg: Orthopedic, MedSurg: Chest Tubes,
Infectious Disease: Tuberculosis, Infectious Disease: HIV/AIDS, Pediatric Nursing,
Maternity & OB Nursing, Psychiatric/Mental Health, Oncology Nursing,
Seizure & Epilepsy Nursing, Critical Care/ICU, Fluid & Electrolytes,
ABG Interpretation, EKG Interpretation, Pharmacology: Antidepressants,
Pharmacology: Cardiac Meds, Pharmacology: Respiratory Meds,
Pharmacology: Diabetes & Insulin, Pharmacology: Anticoagulation, Nursing Interview Prep,
Select All That Apply, Drag & Drop Ordering, EKG Strip Recognition

## Lucide Icons in nursing-school.tsx (already imported — do not duplicate)
Brain, ChevronLeft, ArrowRight, Heart, Wind, Zap, Activity, Droplets, Pill, FlaskConical,
BookOpen, Flame, Lock, Syringe, Bone, Stethoscope, Bug, Baby, HeartPulse, ShieldAlert,
Radiation, Monitor, Waves, TestTube, ListChecks, GripVertical
(ReactNode also imported from react)
