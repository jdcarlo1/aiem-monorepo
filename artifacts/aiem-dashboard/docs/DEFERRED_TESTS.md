# Deferred Playwright Test Tracking

| # | Test ID | Description | Reason Deferred | Target Release | Owner |
|---|---------|-------------|-----------------|----------------|-------|
| 1 | `smoke.spec.ts::ux::dark-mode` | Dark mode toggle persists across navigation | Theme system not yet wired to localStorage | v1.1.0 | Frontend |
| 2 | `smoke.spec.ts::ux::search` | Global search filters visible rows | Search endpoint and debounce hook not implemented | v1.1.0 | Frontend |
| 3 | `smoke.spec.ts::ux::filter` | Column filter controls reduce dataset | Filter state management not implemented | v1.1.0 | Frontend |
| 4 | `smoke.spec.ts::ux::sort` | Column header click sorts table | Sort state management not implemented | v1.1.0 | Frontend |
| 5 | `smoke.spec.ts::ux::pagination` | Pagination controls advance page | Pagination component wired but not end-to-end tested | v1.1.0 | Frontend |
| 6 | `smoke.spec.ts::export::csv` | CSV export downloads a file | Export endpoint `/options/export?format=csv` not implemented | v1.2.0 | Backend + Frontend |
| 7 | `smoke.spec.ts::export::pdf` | PDF export downloads a file | PDF generation library not selected/integrated | v1.2.0 | Backend + Frontend |

## Acceptance Criteria Before Production Sign-off

All 7 tests must move from `test.skip` to active and pass in CI before the v1.1.0 / v1.2.0 release gates.
Each deferred item maps to a concrete missing implementation (not a flaky test or environment issue).

## How to Activate a Test

1. Implement the feature.
2. In `e2e/smoke.spec.ts`, change `test.skip(...)` to `test(...)` for the relevant block.
3. Confirm it passes in GitHub Actions CI.
4. Remove its entry from this document.
