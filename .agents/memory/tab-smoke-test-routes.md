---
name: Correct tab route paths for smoke tests
description: Maps dashboard tab names to their actual Flask route paths and HTTP methods
---

## Rule
Several routes use non-obvious paths or require POST — always use these in smoke tests.

## Mapping (as of June 2026)
| Tab name             | Route path                        | Method |
|----------------------|-----------------------------------|--------|
| net-flow             | /stock-api/net-flow               | POST {} |
| net-flow/multiday    | /stock-api/net-flow/multiday      | POST {} |
| net-flow/microcap    | /stock-api/net-flow/microcap      | POST {} |
| behavioral-matches   | /stock-api/behavioral-matches     | GET    |
| nano-morning         | /stock-api/nano-morning/picks     | GET    |
| nano-candidates      | /stock-api/nano-morning/candidates| GET    |
| sc-morning           | /stock-api/sc-morning/picks       | GET    |
| microcap-calls       | /stock-api/unusual-calls/microcap | GET    |

**Why:** Using /stock-api/net-flow with GET returns 405; /stock-api/nano/morning returns 404.
