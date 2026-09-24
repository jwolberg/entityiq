# Problem Statement

The original problem EntityIQ was built to solve, stated in plain terms. The
[PRD](./PRD.md) turns this into requirements.

## Context

Many B2B platforms let enterprise customers self-register for enterprise
features. Those accounts must pass a business compliance check on the company
behind them. Self-service registration opens several risks:

- Registering a company that does not exist
- Impersonating a real company, or hijacking its account
- Registering a front company to evade sanctions screening

The usual fix is a manual review of what the registrant submitted. That review is
slow, error-prone, and only as good as the reviewer's expertise. It is also hard
to do well: there is no single global company register, and many countries have
several.

The goal is a tool that searches public and authoritative sources, gathers and
verifies as much as it can about the company from the registration data, and
gives a numeric risk score.

## Impact metrics

- Reviewer time saved
- Accuracy of the collected data
- Detection of staged web presence (for example, recently published articles or
  suspicious domain names)

## Functional requirements

### Operator app

- Operators sign in with individual accounts, and their actions are audited
- A list of analyzed companies with analysis date and filtering
- Mark a company as reviewed
- Re-run the analysis
- Correct submitted data and re-run
- A score for the company's DNS records
- Registration details, found versus submitted, with a visual match/difference
  marker
- Contact people: names, phones, emails, addresses
- A visual for the HQ address

### Backend

- An API that serves the operator app
- An endpoint other systems use to submit registrations (company name, domain
  from the email address, billing email, optional phone, billing address, and a
  tax ID whose format varies by country)
- An API for pulling the report programmatically
- An agent-driven flow that collects information, runs verifications, and
  produces a report that the API and the operator app both read

## Performance

Accuracy matters more than speed, but screening one company should take no
more than 2 hours.

## Code quality

A monorepo holding backend, frontend, documentation, and tests for both backend
and frontend.
