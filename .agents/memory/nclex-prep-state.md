---
name: NCLEX Prep project state
description: Current question bank size, all categories, next question number, tech stack, question types, and key constraints for the NCLEX Prep app
---

## Project Overview
Full-stack nursing platform "NCLEX AI" with 3 modes: Nursing School question banks, NCLEX Prep, Interview Prep. Freemium ($15/mo or $49 lifetime). FREE_LIMIT = 10 questions (do NOT change without user approval). Stripe live payments working. Clerk auth fully integrated.

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
- `artifacts/api-server/src/routes/session.ts` — free limit enforcement (FREE_LIMIT = 10), multi-type answer checking
- `artifacts/api-server/src/routes/questions.ts` — question routes; normalizes options format at API layer (see below); returns imageUrl
- `artifacts/api-server/src/routes/stripe.ts` — Stripe checkout, verify-checkout (returns email), restore-access endpoints; admin seed endpoint accepts imageUrl
- `artifacts/api-server/src/stripeClient.ts` — reads Stripe creds from STRIPE_SECRET_KEY env var first (sk_live_), falls back to connector
- `artifacts/api-server/src/webhookHandlers.ts` — Stripe webhook + session update logic
- `artifacts/api-server/src/app.ts` — Clerk proxy + clerkMiddleware wired in
- `lib/db/src/schema/questions.ts` — DB schema (includes questionType + imageUrl columns)
- `lib/db/src/schema/sessions.ts` — sessions schema (has stripeCustomerId, stripeSubscriptionId)
- `artifacts/nclex-prep/src/components/EkgDisplay.tsx` — self-contained SVG ECG strip renderer; takes `rhythm` prop; 12 supported rhythms: normal, bradycardia, tachycardia, afib, flutter, svt, pvcs, vtach, vfib, block1, block3, stemi
- `artifacts/nclex-prep/src/pages/nursing-school.tsx` — 53-category nursing school page
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
- FREE_LIMIT = 10 questions (in artifacts/api-server/src/routes/session.ts)
- Do NOT re-run `seed-products.ts` (test keys) — use `seed-products-live.ts`
- User is very sensitive to breaking changes — always test thoroughly before publishing
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

## Answer Type Encoding
- 'single': correctLetter = "A" (one letter)
- 'multiple': correctLetter = sorted comma-separated letters "A,C,D"; server and client both sort before comparing
- 'ordered': correctLetter = correct sequence of item letters e.g. "C,B,A,D,E"; direct string compare after sorting both sides

## Question Bank State (updated 2026-07-15)
- 70+ nursing school categories (38 new tabs added July 2026)
- Production question count: **~2,300+** after July 2026 re-seed (exact count via /api/questions)
- Next question number to use: **Q2310** (safe starting point after re-seed)
- Assessment categories seeded (410 questions):
  - Assessment: Cardiac — Q1894–Q1943 (50q)
  - Assessment: Respiratory — Q1944–Q1993 (50q)
  - Assessment: Neurological — Q1994–Q2043 (50q)
  - Assessment: Gastrointestinal — Q2044–Q2093 (50q)
  - Assessment: Genitourinary — Q2094–Q2143 (50q)
  - GI High-Yield NCLEX — Q2144–Q2173 (30q)
  - GU High-Yield NCLEX — Q2174–Q2203 (30q)
  - Assessment: Musculoskeletal — Q2204–Q2253 (50q)
  - Assessment: Integumentary — Q2254–Q2303 (50q)
- Laboratory & Diagnostics — Q1844–Q1893 (50q)
- 20 Nursing Interview Prep questions (category: "Nursing Interview Prep")
- 5 hard "hook" questions at questionNumbers -5 through -1 — always appear first in main NCLEX quiz
- totalCategories computed dynamically from array lengths (includes assessments array now)
- executeSql tool checks DEV database (shows ~1508); production count verified via /api/questions fetch

## Admin Seeding
- Endpoint: POST https://nclexai.org/api/admin/seed-questions
- Header: x-admin-secret: value of `ADMIN_TOKEN` env var (NOT hardcoded "nclexai-admin-2026" — that gives 401)
- Use `os.environ["ADMIN_TOKEN"]` in Python; never hardcode the secret
- Always use Python urllib (not bash curl) to avoid apostrophe/shell escaping issues
- Seed endpoint uses `onConflictDoNothing()` on PK only — question_number has NO unique constraint, so
  duplicate q-numbers are safe (multiple rows can share the same question_number)
- Model that works for seeding: gpt-5.4, max_completion_tokens=1500, 5q per call max
  (use 3q per call for NGN: Trend/Graphic — those questions are longer and truncate at 5)
- Parallel seeding: up to 5 concurrent workers before hitting 429 rate limits; use 2-3 for safety

## Data-Loss Prevention (added 2026-07-15)
- lib/db/drizzle.config.ts tablesFilter = ["sessions", "affiliates"] — questions NOT in list
  This prevents Drizzle migrations from ever DROP/ALTER/recreating the questions table again
- Verification script: scripts/verify-nclex-db.sh — checks all 70+ categories, exits 1 if any empty
  Run after every publish: bash scripts/verify-nclex-db.sh

## nursing-school.tsx Array Structure
fundamentals (1), medsurg (9), infectiousDisease (2), specialtyNursing (6), advancedPractice (2),
assessments (8 — Cardiac, Respiratory, Neurological, Gastrointestinal, Genitourinary, Musculoskeletal, GI High-Yield NCLEX, GU High-Yield NCLEX — "Physical Assessment" section),
clinicalReasoning (3 — ABG, EKG, Laboratory & Diagnostics),
pharmacology (5), nursingSkillsLab (1), woundCare (1), dosageCalculations (1), ngnFormats (3),
ivTherapy (2), hygieneADLs (1), safetyMobility (1), woundDressing (1), eliminationSkills (1),
respiratorySkills (1), giNutritionSkills (1),
hematologic (1), immuneRheum (1), sensory (1), perioperative (1), painManagement (1),
infectionInflammation (1), shockSepsis (1), endOfLife (1), emergencyCritical (1)
Total: 53 categories

## JSX Section Order (nursing-school.tsx)
1. Fundamentals
2. Medical-Surgical Nursing
3. Infectious Disease
4. Specialty Nursing
5. Advanced Practice
6. Advanced Clinical Topics (New badge) — hematologic, immuneRheum, sensory, perioperative, painManagement, infectionInflammation, shockSepsis, endOfLife, emergencyCritical
7. Physical Assessment (New badge) — assessments array (Cardiac, Respiratory, Neurological)
8. Clinical Reasoning — ABG, EKG, Laboratory & Diagnostics
9. Pharmacology
10. Nursing Skills Lab (Procedures badge) — nursingSkillsLab + 7 subcategory arrays

## Icons imported from lucide-react (nursing-school.tsx)
Brain, ChevronLeft, ArrowRight, Heart, Wind, Zap, Activity, Droplets, Pill, FlaskConical,
BookOpen, Flame, Lock, Syringe, Bone, Stethoscope, Bug, Baby, HeartPulse, ShieldAlert,
Radiation, Monitor, Waves, TestTube, ListChecks, GripVertical, Calculator, Bandage, ClipboardList,
ShieldCheck, Scissors, Droplet, Utensils, Sparkles, Eye, AlertTriangle, Thermometer
(ReactNode also imported from react)

## Nursing School + Interview Prep
- Both use CLIENT-SIDE answer checking (no submitAnswer API call)
- study-quiz.tsx supports all 3 question types AND shows polished results screen (pass = 75%)

## Results Screen (study-quiz.tsx)
- Shows after completing all questions in a category
- Displays score as large XX% with pass/fail badge (75% threshold = passing, mirrors NCLEX)
- Score bar with 75% passing marker, 3-stat grid (correct/missed/total), contextual message
- Green = passing (≥75%), amber = close (60-74%), red = below 60%
- Retry and Choose Another Section buttons
