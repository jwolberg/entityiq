# Project Operating Rules

## Mission
We build production-ready software with fast iteration, but reliability matters more than cleverness.

## Access Rules
- Only modify files in the current project
- Do not access sibling directories unless explicitly instructed
- Never make changes outside the current repo

## Team-wide rules
- Prefer the smallest change that delivers real user value.
- Reuse existing patterns before introducing new abstractions.
- Avoid unrelated refactors unless they are required to complete the task safely.
- Keep scope narrow and explicit.

## Workflow
Always follow this sequence unless explicitly told otherwise:
1. Understand the task
2. Inspect relevant files
3. Propose a brief plan
4. Implement in small steps
5. Run validation
6. Summarize what changed and what remains

## Engineering standards
- Prefer simple solutions over clever abstractions
- Reuse existing patterns in the codebase
- Keep functions focused and readable
- Do not introduce new dependencies unless justified
- Preserve backward compatibility unless told otherwise

## Validation
Before considering work complete:
- Run lint
- Run tests relevant to changed files
- If no tests exist, suggest a minimal test or validation path
- Report failures clearly

## Product behavior
- Clarify the user-facing goal of every feature
- Prefer shipping a narrower working version over a broader fragile one
- Call out tradeoffs when making implementation choices

## Output style
- Be concise
- State assumptions
- List changed files
- Note risks / follow-ups

## Implementation rules (project-specific)

### 1. Commit per ticket
- After completing **each ticket** in the build plan, make a **git commit** scoped
  to that ticket — do not batch multiple tickets into one commit.
- The commit message must reference the ticket (ID and/or title) and summarize
  what changed.
- Commit only (local); **do not push** unless explicitly asked. (Note: `main`
  is a protected branch; pushes/force-pushes require the user.)
- Run the relevant validation (lint/tests) **before** the commit per the
  Validation section.

### 2. Running implementation notes
- While implementing, maintain a running **`docs/implementation-notes.md`**.
- Append an entry whenever you:
  - make a **decision not specified** in the spec/PRD/build plan,
  - **change or deviate** from the spec/plan,
  - accept a **tradeoff** (and why),
  - hit anything else the user should know (surprises, blockers, assumptions,
    follow-ups, things to revisit).
- Keep entries short and dated, ideally tied to the ticket being worked. This
  file is for human review — write it for the user, not for tooling.