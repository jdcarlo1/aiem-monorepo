---
name: Dashboard tab type union
description: Every new tab ID must appear in the useState type union or the whole Dashboard crashes on render
---

## Rule
When adding a new tab to Dashboard.tsx, you must add its ID in **three places**:
1. The `useState<"..." | "...">` type literal (the long union type on the `setTab` line)
2. The tab list array `{ id: "...", label: "..." }`
3. The render block `{tab === "..." && <MyComponent />}`

Missing from the type union causes TypeScript error TS2367 ("comparison has no overlap") on **every render cycle**, not just when that tab is clicked. This crashes the entire Dashboard on any state change → black screen.

**Why:** React re-runs the whole component render function on every state change. A TS2367 in the JSX becomes a runtime ReferenceError in the compiled output, killing the tree before anything paints.

**How to apply:** Before shipping any new tab, grep for its ID in all three locations. If any one is missing, add it before deploying.

## Also added: TabErrorBoundary
A `TabErrorBoundary` class component now wraps the `<main>` content area with `key={tab}`. If any future component crash occurs, users see "TAB FAILED TO LOAD" with the error message instead of a black screen. The `key={tab}` prop resets the boundary on every tab switch so one bad tab doesn't poison others.
