# Implementation Notes

Running log of decisions, deviations, tradeoffs, and surprises during
implementation. Written for human review, tied to build-plan tickets.

---

## 2026-05-26 — P0-T1: Resolve blocking Open Decisions

**Decision (Open Decision #1 — backend stack): Python + FastAPI.** Confirmed by the
user. Rationale per ARCHITECTURE.md: the verification/enrichment/parsing domain
(WHOIS/DNS, address/phone normalization, sanctions, Playwright extraction) — which
*is* the product — has the strongest ecosystem in Python. Frontend stays React+TS.
Alternative (Node/TS) was rejected; it would unify language with the FE but weaken
the enrichment ecosystem.

**Decision (Open Decision #2 — job orchestration): Celery + Redis.** Follows from #1;
mature and simple fit for the async per-run pipeline model.

**Decision (Open Decision #3 — `shared/` contents): OpenAPI-generated TS types.** The
FastAPI backend emits an OpenAPI schema; the frontend consumes generated TS types
rather than hand-maintained duplicates. `shared/` holds the contract, not runtime code.

**Still open / deferred:** #4 HQ map provider, #5 data-source access/licensing,
#6 co-primary tiebreaker, #7 PII retention policy — to be resolved at the tickets
that need them (per BUILD_PLAN.md).

**Effect:** unblocks P0-T2 (monorepo scaffold). All build tickets now have a
confirmed stack.
