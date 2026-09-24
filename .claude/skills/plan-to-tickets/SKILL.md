---
name: plan-to-tickets
description: "Decompose a ce-plan into tracked backlog tickets — one per Implementation Unit, with refs back to the plan and depends_on from its sequencing. Handles legacy plans with no artifact_readiness field. Idempotent. Use on /plan-to-tickets, or after ce-plan finishes and the work needs a lifecycle."
---

# /plan-to-tickets — Turn a plan into tracked work

The bridge between the two halves of the workflow. `ce-brainstorm` and `ce-plan`
decide **what** and **how**, writing a unified plan to `docs/plans/`. Nothing in
the TerMinal loop reads that directory: `/session-start` builds its checklist
from **ticket acceptance criteria**, `/factory` routes on ticket `depends_on`,
and `/merge-sync` reconciles tickets. A plan with no tickets therefore produces
no tracked work — it ships (or stalls) invisibly.

This skill closes that gap. It is the only supported way plan work enters the
backlog.

## Fast path: TerMinal MCP tools

When the `terminal-harness` MCP server is registered:

- **`file_ticket({repo, title, body, type, priority, agentId, agentScope, agentKind})`** —
  allocates the id and writes frontmatter; returns `{slug, id, path}`.
- **`update_ticket({slug, ...})`** — to set `depends_on` once sibling ids exist.
- **`list_tickets({repo})`** — for the idempotency scan in step 2.

Fall back to `.claude/skills/ticket/bin/next-ticket-id` + a direct write when
MCP is unavailable. The judgment — unit-to-ticket sizing, acceptance criteria,
dependency translation — stays here either way.

## Input

```
/plan-to-tickets docs/plans/2026-06-15-001-feat-income-setups-endpoint-plan.md
```

With no argument, list plans in `docs/plans/` (`<root>` is `docs` unless
`docs_root` is set in `.compound-engineering/config.local.yaml`) and ask which.

## Process

### 1. Gate on decomposability

The test is **whether the plan has Implementation Units**, not which format it
was written in. Plans predating the unified-plan contract carry no
`artifact_readiness` field at all and are still perfectly decomposable.

- `artifact_readiness: requirements-only` → **stop.** That is a `ce-brainstorm`
  output that never went through `ce-plan`; filing tickets off product
  requirements produces work items with no technical shape.
- No `## Implementation Units` section, or zero `### U<N>.` headings under it →
  **stop and report.** Do not invent units from prose headings.
- Everything else decomposes, with or without `artifact_readiness`.

Also read the plan's `status:`. A plan already marked `completed` shipped before
this bridge existed — decompose only what is genuinely outstanding, and say
explicitly what you skipped and why.

### 2. Scan for existing tickets (idempotency)

This skill **must be safe to re-run** — plans get deepened and units get added.
Before filing anything, find every ticket whose `refs:` contains this plan's
path, and read which unit id each one carries (`U1`, `U2`, …).

Units already represented are **skipped**, not re-filed. Report them as existing
so the user sees the full mapping, not just the new rows.

### 3. Size the tickets

Default is **one ticket per Implementation Unit** — units are already the plan's
own decomposition, and `ce-plan` sizes them to be independently reviewable.

Deviate only when the plan's `## Validation & Sequencing` section explicitly
groups units into one PR. Then file one ticket for the group and list every unit
id in `refs:`. Never split a unit across tickets: a unit is the smallest thing
the plan claims is coherent.

### 4. File in dependency order

Units reference each other ("U5 wires U3's evaluator into the pipeline"). Ticket
`depends_on` takes **ticket ids**, not unit ids, so the mapping only exists once
the earlier ticket is filed. File in the plan's sequencing order, keeping a
`unit id -> ticket id` map as you go, and translate each unit's dependencies
through it.

Per ticket:

| Field | Source |
|---|---|
| `title` | The unit heading, rewritten action-first (`U4. TickerSnapshot income fields` → "Add income fields to TickerSnapshot") |
| `type` | The plan's frontmatter `type:` (`feat` → `feature`) |
| `priority` | `high` for units on the plan's critical path, else `medium` |
| `horizon` | `now` for units in the current phase; `future` for anything under `## Deferred to Follow-Up Work` |
| `refs` | `["<plan path>", "U<N>"]` — **both**, so the ticket resolves to the design and the specific unit |
| `depends_on` | Translated ticket ids, per above |
| `acceptance` | The unit's own checkable criteria, as a YAML block list |
| `source` | `plan-to-tickets` |
| `agent_id` / `agent_scope` / `agent_kind` | Per CLAUDE.md [4] — exactly one owner. Inherit the plan's if stated, else pick per `list_agents` |

Body: a one-paragraph Description quoting the unit's intent, then
`## Acceptance criteria`, then `## Design notes` carrying the unit's constraints
and anything the plan's Risks section pins to it. **Link, don't copy** — the plan
stays the design record. A ticket that restates 60 lines of plan will drift from
it.

### 5. Do not write the mapping back into the plan

Tempting, and wrong. The ticket's `refs:` is the system of record for the
plan-ticket link; a `tickets:` list in the plan frontmatter is a second copy that
can disagree with it. Derive the reverse direction by scanning `refs:` when you
need it — which is exactly what `/merge-sync` does to close the plan.

(Standing decision prior, 2026-07-16: store only what cannot be derived from the
system of record.)

### 6. Report

A table of unit → ticket id → title, marking which were newly filed and which
already existed. Name the first ticket with no unclosed `depends_on` as the
suggested starting point, then hand off:

```
/session-start "<plan title> — units U1–U6"
```

## Quality bar

- **Every ticket carries the plan path in `refs:`.** Without it the lineage is
  unrecoverable and `/merge-sync` can never close the plan.
- **Acceptance criteria are checkable**, per the ticket schema — not "implement
  U3" but the condition that proves U3 works.
- **`depends_on` reflects the plan's real sequencing.** `/factory` treats
  blocked tickets as out of scope; a wrong edge stalls the queue or, worse,
  lets work start before its foundation exists.
- **Re-running changes nothing** when no units were added.

## What NOT to do

- Don't decompose a `requirements-only` plan — run `ce-plan` first.
- Don't file tickets for units that already shipped. If the plan predates the
  backlog (as `2026-06-15-001` does), only decompose what is genuinely
  outstanding and say what you skipped.
- Don't copy the plan into ticket bodies. Reference it.
- Don't hand-edit `.next-id` — use `bin/next-ticket-id` or `file_ticket`.

## Activity

```bash
.claude/bin/activity ticket-filed "Plan decomposed · <plan basename>" "<N> tickets from <M> units"
```
