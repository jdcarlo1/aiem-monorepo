# Autonomous Desk Doctrine

## Owner intent (authoritative)

The Options Engine / AIEM strategies were designed so the **system knows what to do every day**.

- **No daily human approval** of setups or strategies  
- **No babysitting** complex options structures  
- **Fully autonomous** find → decide → execute → grade  

Human involvement is only for **system control**, not trade picking:

| Human may do | Human does **not** do |
|---|---|
| Arm / disarm live mode | Approve/reject each trade |
| Set max loss / size caps | Pick which options strategy daily |
| Hit kill switch | Manually fill every order |
| Review track record after the fact | Override gates every morning |

## Current state

| Layer | Behavior |
|---|---|
| **Autonomous paper (OE)** | **ON** — find → auto paper-fill → grade (hardened 2026-08-08) |
| **Live broker orders** | **LOCKED** until paper reliability + adapter + risk locks are proven |
| **When live is unlocked later** | Same autonomy as paper — broker `place_order` from the engine, **not** a human queue |

## Explicitly rejected product shape

A prop desk where the operator must Approve/Reject every AI proposal is **not** the product.
That path was considered and discarded because options strategies are complex by design and the AI exists to run them.

## Path to autonomous live (same philosophy)

1. Autonomous paper takes and **completes** trades under `balanced` gates  
2. Honest ask-fill / graded P&L shows whether edge exists  
3. Implement real broker `place_order` (Tradier/IBKR/…) against **broker paper** first  
4. Fail-closed risk: daily loss, kill switch flatten, position reconcile  
5. Arm live locks deliberately — engine still fires orders **without** per-trade human approval  

Owner strategy scope for Tradier go-live (incl. multi-leg catalog; **not** F3-centric):
see `Directive_Tradier_Autonomous_GoLive_OwnerStrategies_2026-08-08.md`.

## Commercial honesty

Until step 3–5 are done, sell/use as **autonomous research & paper**.  
Do not claim live autonomous brokerage until the adapter is real and reviewed.
