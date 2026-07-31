# SEO Strategy

## In scope
- NCLEX Prep app (`artifacts/nclex-prep`) — public-facing, has real domain nclexai.org, real users, clear SEO intent. Root path `/`.
- Stock Scanner landing page (`artifacts/stock-scanner/src/pages/Landing.tsx`) — public marketing surface at `/stock-scanner/`.
- AIEM Institutional Terminal (`artifacts/aiem-dashboard`) — primarily an authenticated trading dashboard; public landing/login surface only.

## Out of scope
- Authenticated app routes (quiz, study, home, dashboard views behind auth)
- Admin pages (`/admin/**`)
- Python backend scripts, data files, and archive artifacts

## Target audience
- NCLEX Prep: Nursing students and RNs preparing for the NCLEX exam (US market)
- Stock Scanner / AIEM: Institutional or sophisticated individual investors / traders

## Primary keywords
- NCLEX Prep: "NCLEX practice questions", "NGN bowtie questions", "next generation NCLEX prep", "NCLEX AI", "NCLEX CAT adaptive"
- Stock Scanner: inferred from branding — stock screening, smart money signals

## Rendering strategy
- All three artifacts are Vite + React SPAs. No SSR or SSG is present.
- All public marketing content is client-rendered only; crawlers receive an empty `<div id="root"></div>`.

## Dismissed categories
- (None yet)
