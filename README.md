# EntityIQ

**Enterprise Business Verification & Risk Intelligence Platform.**

EntityIQ ingests self-service enterprise registration data, runs automated
multi-source verification and enrichment, and produces an explainable risk
assessment — helping compliance operators decide, quickly and defensibly, whether
to approve an enterprise account. It is a risk-reduction and confidence-assessment
platform that augments human review; it does not auto-approve accounts.

## Documentation

- [docs/STRATEGY.md](docs/STRATEGY.md) — product strategy: problem, approach, metrics, tracks
- [docs/PRD.md](docs/PRD.md) — product requirements
- [docs/USERS.md](docs/USERS.md) — users and how they interface with the system
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technical design and open decisions
- [docs/problem-statement.md](docs/problem-statement.md) — originating problem statement

## Status

Working end to end: submission API → async 10-stage verification pipeline →
four-layer explainable risk score with triage → operator web app (queue,
detail, correct and re-run, review, export) with an append-only audit log.
Backend: 385 tests; frontend: 18 tests.

Known gaps and the plan to close them are in
[docs/ASSESSMENT-2026-09-24.md](docs/ASSESSMENT-2026-09-24.md) and
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md). To run it locally, see
[docs/RUNBOOK.md](docs/RUNBOOK.md).
