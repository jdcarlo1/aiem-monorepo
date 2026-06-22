---
name: Node.js pg pool crash on Replit
description: Replit periodically kills idle DB connections; pg pool emits unhandled 'error' event which crashes Node.js — fix and prevention pattern.
---

# Node.js pg Pool Crash on Replit

## The Rule
Any Node.js server using `pg` (or libraries that wrap it, like `stripe-replit-sync`) must add top-level uncaught error handlers in the entry point (`index.ts`) or it will crash when Replit recycles a DB connection.

## Why
Replit's managed Postgres periodically issues `terminating connection due to administrator command` to idle connections. The `pg` pool emits an `error` event on the pool instance. Node.js crashes the process on any unhandled `error` event (hard Node.js rule). The server then crash-loops until Replit's deployment system marks the deployment as "could not be reached" (full outage).

## How to Apply
Add this at the very top of `artifacts/api-server/src/index.ts` (before any async calls):

```ts
process.on('uncaughtException', (err) => {
  logger.error({ err }, 'Uncaught exception — continuing');
});
process.on('unhandledRejection', (err) => {
  logger.error({ err }, 'Unhandled rejection — continuing');
});
```

This is already applied. Do not remove these handlers. If you see them missing, add them back — their absence WILL cause outages.

## Symptoms
- Production shows "The deployment could not be reached"
- Deployment logs show: `error: terminating connection due to administrator command` followed by `throw er; // Unhandled 'error' event` then `crash loop detected`
- The NCLEX API server (Node.js) goes down; the stock-scanner Python API is unaffected
