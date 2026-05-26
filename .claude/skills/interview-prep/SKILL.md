---
name: interview-prep
description: Reconstruct a finished project's context and rehearse its demo/interview, tailored to the candidate. Reads the project's codebase, /docs, and README plus the private career repo (candidate profile + company brief), then writes a full prep doc to ~/workspace/career/companies/<slug>-prep.md plus a self-contained, human-readable ~/workspace/career/companies/<slug>-prep.html rendering of it. Use weeks after a build, before a demo/interview, to get back up to speed fast.
---

Prepare me to demo and defend this project in an interview: $ARGUMENTS

## Context

This skill exists for a specific situation: a project was built AI-assisted, and
the demo/interview happens days or weeks later. By then the build details have
faded. The job of this skill is to **reconstruct full working context from the
repo**, **rehearse the interview**, and **tailor it to me and the company** so I
can walk in cold and present with confidence.

The output is a single durable file in my **private career repo** —
`~/workspace/career/companies/<slug>-prep.md` — NOT in the project repo. The
project repo is read-only here; nothing is written into it. Write the prep FOR
ME, not for tooling — like a smart teammate briefing me on a project I built but
half-forgot, who also knows my background and the company I'm interviewing with.

### Resolving `<slug>` and arguments

`$ARGUMENTS` may contain a **company slug** (e.g. `acme` or `company=acme`), a
focus area (e.g. "focus on the vault"), or be empty.

- **Output filename slug:** if a company slug is given, use it → write
  `~/workspace/career/companies/<slug>-prep.md`. If no company slug is given,
  fall back to the **project's directory name** as the slug → still write into
  `~/workspace/career/companies/<project-name>-prep.md`. Either way the prep
  lands in the career repo's `companies/` folder.
- **Company brief:** if a company slug is given, read
  `~/workspace/career/companies/<slug>.md` as the company brief. Never write to
  that file — it is my hand-filled input. (The generated file is `-prep.md`.)
- **Focus area:** weight that area but still produce the full prep.

## Input

Read these if present, in priority order:

1. **`README.md`** (root) — the public story of the project. Often the source of
   "why" framing, known gaps, and feature claims you'll be asked to defend.
2. **`/docs/`** — read all of it. Pay special attention to docs that contain
   **explicitly pre-identified interview/review questions or a grading rubric**,
   e.g. a `challenge.md` / `PRD.md` / `spec.md` with a "Live Review", "Discussion",
   "Questions", or "Rubric" section. These are authoritative — you MUST answer
   every question found there.
3. **Build/implementation notes** — `docs/implementation-notes.md`,
   `BUILD_NOTES.md`, `BUILD_PLAN.md`, `ARCHITECTURE.md`, `DEPLOY.md`, `RUNBOOK.md`,
   or similar. These capture decisions, tradeoffs, and deviations — gold for
   "why did you…" answers.
4. **Existing prep docs** — if a prior `*-prep.md` (this skill's output) or an
   in-repo `*review-prep*.md` / `*live-review*.md` exists, treat it as prior art:
   reuse its locked positions, don't contradict it, and supersede it cleanly.
5. **Code** — entry points, core modules, tests, config. Enough to ground every
   answer in what the code ACTUALLY does (not what the docs claim). Where docs and
   code disagree, trust the code and flag the gap.

### Personal / company context (from the private career repo)

These make the prep about ME, not just the code. Read if present:

6. **Candidate profile** — `~/workspace/career/candidate.md`. My stable
   background: narrative, strengths, signature stories (with competency tags),
   skills, values, logistics. Use it to (a) draft my "tell me about yourself,"
   and (b) pick which strengths/stories this project best demonstrates. This is
   the primary personalization input — always read it.
7. **Company brief** — `~/workspace/career/companies/<slug>.md` (only when a
   company slug is given). The company, role, and what they value, plus my angle
   and any reverse-questions I've seeded. Use it to tailor the strategic angle and
   the questions I ask them. If no slug is given or the file is absent, proceed
   without company tailoring and say so.

## Write output to

- `~/workspace/career/companies/<slug>-prep.md` (slug resolved as above) — the
  canonical source.
- `~/workspace/career/companies/<slug>-prep.html` — a self-contained,
  browser-ready rendering of that same prep, laid out for fast human
  comprehension (see **HTML rendering** below). The `.md` is the source of
  truth; the `.html` derives from it and must not add or drop content.

These files live in my private career repo, so they MAY reference my background
freely. Do NOT write anything into the project repo (no files, no `.gitignore`
edits). Do NOT commit or push — I handle git in the career repo myself.

## Goal

A self-contained doc that lets me, weeks later:
- **Re-load the project into my head in 5 minutes** (what, why, how, stack).
- **Answer the questions an interviewer will actually ask**, each with a crisp
  lead-with line, honest gaps named proactively, and a concrete next step.
- **Position myself** — know which of my strengths/stories to lead with for this
  project and (when known) this company.
- **Run the demo** without fumbling, and **ask sharp questions back**.

Reliability over cleverness: every project answer must be grounded in the repo,
and gaps must be stated honestly rather than papered over. An interviewer rewards
"here's the honest limitation and what I'd do next" far more than overclaiming.

## When to use

- Returning to a finished (or near-finished) project to prep for a demo/interview.
- Any time the lag between building and presenting has eroded recall.

Do NOT use this skill to:
- Implement features, fix bugs, or refactor.
- Review code against a spec (that's `/review`).
- Assess an unfamiliar codebase to plan new work (that's `/assess`).

## Search strategy

1. Read `README.md` and skim every file in `/docs` to map what documentation
   exists and which docs carry decisions, questions, or a rubric.
2. Extract any **pre-identified questions / rubric dimensions** verbatim into a
   working list — these are required outputs.
3. Identify entry points and core modules; read enough code to verify the claims
   in the README/docs and to ground each "how" answer. **As you go, capture a
   one-line purpose for each key functional file** — entry points, core modules,
   and the files carrying the load-bearing logic — for the Codebase Map.
4. Scan tests to learn what's actually validated (useful for "how do you know it
   works" questions) and where coverage is thin (an honest gap).
5. Note deviations between docs and code — these are likely interview probes.
6. Read `~/workspace/career/candidate.md` and, if a slug is given, the company
   brief, to drive the strategic-angle and questions-to-ask sections.

Prefer targeted reading over exhaustive scans. If the repo is large, focus on the
area named in `$ARGUMENTS` and mark the rest lightly covered.

## Task

1. Reconstruct the **project snapshot**: one-line pitch, what it does, who it's
   for, the stack, and the single most important idea to lead with.
2. Collect every **pre-identified question / rubric dimension** from the docs and
   answer each one, grounded in the code.
3. Generate **anticipated interview questions** the docs did NOT spell out,
   grouped by the categories in the output format. Favor the questions a sharp
   interviewer asks: why this over the alternative, how does the hard part work,
   what breaks at scale, where are the security/failure edges, what would you
   change.
4. For each answer, write a **lead-with one-liner**, then supporting detail
   grounded in specific files, then an **honest gap / next step** where one
   exists. Cite files as `path:line` where it helps me find it again.
5. Mark each answer's confidence with the **legend** so I know what's solid vs.
   needs a second look before the interview.
6. Write a **demo walkthrough** — the happy-path script to show the project live,
   including setup/run commands pulled from the repo, and the 2–3 moments worth
   highlighting.
7. If a **candidate profile** is present, write a **Your Strategic Angle (fit)**
   section: which 2–3 of my strengths/signature stories this project best
   demonstrates, mapped (when a company brief exists) to what that company values,
   plus a tailored "tell me about yourself" tied to this project.
8. Write **Questions to Ask Them** in three buckets: project/tech (grounded in the
   repo — sharp, specific questions a builder would ask), role/team, and
   company/strategy. Seed the role/company buckets from the company brief when
   present; otherwise provide strong generic-but-tailorable defaults. Favor
   questions that subtly surface my strengths.
9. Build the **Codebase Map**: a one-line description of what each key functional
   file does (entry points + core modules + the load-bearing logic), grounded in
   what the code actually does. Keep it skimmable — skip tests, config, generated
   files, and boilerplate unless they carry real logic.
10. Write the assembled prep to `~/workspace/career/companies/<slug>-prep.md`,
   creating `~/workspace/career/companies/` if needed. Never overwrite the
   hand-filled `<slug>.md` brief.
11. Render that same prep into `~/workspace/career/companies/<slug>-prep.html`
   per the **HTML rendering** section — a self-contained file laid out for fast
   human reading. Generate it from the finished `.md` content so the two never
   drift; do not introduce new claims or drop sections.

## Required output format (~/workspace/career/companies/<slug>-prep.md)

```markdown
# Interview Prep — <Project Name><, for <Company> (<role>) if a brief exists>

**Generated:** <date>  ·  **Source:** README + /docs + code (+ candidate profile <+ company brief>)
**Legend:** ✅ solid, ready to say · ✍️ drafted, sanity-check before interview · ❓ verify / needs data

## 30-Second Pitch
- One sentence on what it is and the problem it solves.
- Who it's for / the scenario it targets.

## Project Snapshot (re-load context fast)
- **What it does:** …
- **Stack & why:** … (language, framework, key libs — and the reason each was chosen)
- **Architecture in 3 sentences:** …
- **Entry points / how to run:** `…`
- **The one idea to lead with:** …

## Codebase Map (key files)
> One line per key functional file, so I can jump straight to where any answer
> lives. Entry points first, then core modules and the load-bearing logic. Skip
> tests/config/boilerplate unless they carry real logic. Cite `path` (or
> `path:line` for a specific symbol).
- `path/to/entrypoint` — what it does, in one line.
- `path/to/core-module` — …

## Your Strategic Angle (fit)
> Include only if `candidate.md` exists. Tie my background to THIS project (and to
> the company when a brief exists). Omit if no profile.
- **Tell me about yourself (tailored to this project):** … (2–3 sentences)
- **Strengths to showcase here:** … (2–3 from candidate.md §3/§4, each with the
  moment in this project that proves it)
- **Best-fit signature story:** … (which story to reach for, and why it lands)
- **Maps to what they value:** … (only if a company brief exists)
- **Risk to preempt:** … (the concern they might have about me + my framing)

## Pre-Identified Questions (from the docs)
> Source: <file + section>. Answer every one.

### Q — <question, verbatim or paraphrased>   <legend marker>
- **Lead with:** <crisp one-liner>
- **Detail:** <grounded explanation, cite files>
- **Honest gap / next step:** <if any>

(repeat per question; omit this whole section if the docs contain none)

## Anticipated Questions

### Why (decisions & alternatives)
For each: why this choice, what you considered, the tradeoff you accepted.
### Q — …  <marker>
- **Lead with:** …
- **Detail:** …
- **Honest gap / next step:** …

### How (mechanics & implementation)
How the hard / interesting parts actually work, grounded in code.

### Scaling & production-readiness
What breaks at 10×/100×, what's a prototype shortcut vs. production design.

### Security, failure modes & edge cases
Threats, what happens when things go wrong, what's deliberately out of scope.

### Testing & validation
How you know it works; coverage strengths and honest gaps.

### What you'd do differently / known gaps / next steps
The questions you want to answer before they're asked.

## Demo Walkthrough
1. Setup / run: `…`
2. Happy path to show, step by step.
3. Moments to highlight (the 2–3 things that land).
4. What to avoid clicking / known rough edges in the demo.

## Questions to Ask Them
> Smart questions signal seniority and genuine interest. Lead with the ones that
> also surface my strengths. Pull role/company items from the company brief when present.

**About the project / tech** (grounded in this repo)
- …

**About the role / team**
- …

**About the company / strategy**
- …

## Rubric Cheat-Sheet
> Include only if a rubric / grading criteria exists in the docs.

| Dimension (weight) | Lead with | Honest gap to name |
|---|---|---|
| … | … | … |

## Cross-Cutting Reminders
- The 3–5 framings to keep returning to, no matter the question.
- The single honest limitation to name proactively rather than get caught on.
```

## HTML rendering (~/workspace/career/companies/<slug>-prep.html)

Produce a second file that presents the exact same prep content in a layout built
for a human skimming it minutes before an interview. It derives from the finished
`.md` — same sections, same wording, nothing added or removed — just structured
and styled for the eye instead of the parser.

**Hard requirements**
- **Self-contained, single file.** All CSS in one `<style>` block; no external
  stylesheets, fonts, scripts, or CDN links. It must open correctly by
  double-clicking the file offline. A tiny bit of inline vanilla JS is fine for
  the TOC/collapse behavior below, but the doc must be fully readable with JS off.
- **Content parity.** Every section, question, answer, table, and citation in the
  `.md` appears in the `.html`. Don't summarize or reorder. If the `.md` omits a
  section (no rubric, no Strategic Angle), the `.html` omits it too.
- **Escape correctly.** Treat the content as text: escape `<`, `>`, `&` so code
  snippets, generics like `Map<K,V>`, and `path:line` citations render literally.

**Layout for comprehension**
- **Header band:** project name (and company/role if a brief exists), the
  generated date, and the source line.
- **Legend as a key:** render the ✅ / ✍️ / ❓ legend once near the top as colored
  pill badges, and reuse those same badges wherever a question carries a marker —
  green ✅, amber ✍️, red/grey ❓ — so confidence is scannable at a glance.
- **Sticky table of contents:** a side (or top, on narrow screens) nav listing the
  major sections with in-page anchor links, so I can jump to "Demo Walkthrough" or
  "Questions to Ask Them" instantly.
- **Q&A as cards:** render each question as a self-contained card with the question
  as its heading and its confidence badge inline. Visually emphasize the **Lead
  with** line (it's what I say first), set **Detail** as normal body text, and
  style **Honest gap / next step** as a distinct callout (e.g. left-border note) so
  gaps stand out rather than hide.
- **Codebase Map & Rubric as tables/lists:** render the Codebase Map with
  monospaced file paths and the Rubric Cheat-Sheet as a real `<table>` with clear
  headers.
- **Cross-Cutting Reminders as a highlighted callout** at the end — these are the
  framings to keep returning to, so make them visually prominent.
- **Readable defaults:** system font stack, comfortable line length
  (~70–80ch max-width for body text), generous line-height, clear heading
  hierarchy, and a light/neutral theme with enough contrast. Include
  `@media print` rules so it prints/exports to PDF cleanly (expand any collapsed
  sections, drop the sticky nav). Keep it responsive down to a phone width.

Keep the styling clean and professional, not flashy — this is a working document I
read under pressure, not a landing page.

## Rules

- Ground every project answer in the actual repo. Cite files. No invented capabilities.
- When docs and code disagree, trust the code and flag it as a likely probe.
- Name honest gaps explicitly — an unstated limitation is a trap; a stated one is
  a strength. Never overclaim.
- Answer EVERY pre-identified question found in the docs.
- Write for me reading this cold weeks later: plain, specific, skimmable.
- Be concise. A tight lead-with line beats a paragraph.
- **Do NOT modify the project repo in any way** — no code, docs, tests, or
  `.gitignore`. The only files you create are the prep doc and its HTML rendering
  in the career repo.
- The `.html` is a faithful rendering of the `.md` — keep them in lockstep. Never
  let the HTML add claims, drop sections, or say something the markdown doesn't.
- Never overwrite `candidate.md` or the hand-filled `companies/<slug>.md` brief.
- Do NOT commit or push in either repo.

## Behavior

- If `~/workspace/career/companies/<slug>-prep.md` already exists, ask whether to
  refresh it; if refreshing, preserve any edits/locked positions you can identify
  and update the rest. Always regenerate the `.html` from the final `.md` so they
  stay in sync, even when only the markdown was edited.
- If the `~/workspace/career/companies/` directory doesn't exist, create it before
  writing (the career repo should already exist; if it doesn't, tell me and stop).
- If `/docs` is sparse or absent, work from README + code and mark the snapshot
  and rubric sections as inferred / unavailable.
- If no rubric or pre-identified questions exist, omit those sections and lean
  harder on the Anticipated Questions.
- If `$ARGUMENTS` names a focus area, weight that area but still produce the full
  snapshot so context recovery stays complete.
- If the repo is large, cap the Codebase Map at the ~10–15 most load-bearing files
  (weighted toward any focus area) and say it's a partial map rather than listing
  everything.
- If a major claim can't be verified from the repo, mark it ❓ and tell me what to
  confirm before the interview.
- If `~/workspace/career/candidate.md` is missing or still has unfilled _prompts_,
  skip the Strategic Angle section, note it, and point me to fill it.
- If no company slug is given or the brief is absent, derive the filename slug from
  the project directory name, write the Questions to Ask Them from the repo +
  sensible defaults, and note that role/company tailoring was skipped for lack of
  a brief.

After writing:
- Confirm files created: `~/workspace/career/companies/<slug>-prep.md` and
  `~/workspace/career/companies/<slug>-prep.html`
- State which slug was used and whether it came from `$ARGUMENTS` or the project name
- Note how many pre-identified questions were answered and the count of ❓ items
  I should verify before the interview
- State which personal/company context was used (candidate profile? company brief?)
  or that it was skipped and why
- STOP
