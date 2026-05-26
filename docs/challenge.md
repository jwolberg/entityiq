# Challenge

SkyFi provides Enterprise features to Enterprises. Enterprise accounts (self register) are subject to business Compliance checks that is performed on the Business entity. Self service registration allows for non-existing companies creation or fraudulent user trying to impersonate a company.

## Problem & Context

### Business Context

We have created a self service registration process for our Enterprise customers. This however exposes several risks - highjacking of company accounts, mis-representation of a company, non-existent company registration to evade Sanctions checks.

We have implemented a manual (human) review of the information provided during the self serve registration. This process however is prone to errors and slow and also depends on the expertise of the operator. Also there is no single register of all companies in the world, oftentimes there are multiple registers per country.

To improve this process - we want to have a tool that will go out and scrape the internet and try to find (and verify) as much information as possible given the data entered in the registration process.

It would also be ideal if there is a numeric score of the perceived risk assessment for this company.

### Impact Metrics

- time saved
- accuracy of data
- detect staged internet resources (ie recently added articles or fishy domain names)

## Requirements & Success Criteria

### Functional Requirements

#### FE

- operators sign in with an account and their actions are audited
- exposes a list of "companies" that the intelligence tool has analyzed, date of analysis, filtering
- allow marking as reviewed by an operator
- allow operator to re-trigger analysis
- allow operator to correct data (should be accurate) and re-trigger
- provide score about DNS associated records with the "company"
- provide company registration information - found vs provided by the registering person. Visual mark of match/difference
- provide contact people information - names, phones, emails, address
- provide visual for HQ address

#### BE

- serve the FE needs via API
- provide an endpoint so that other systems can send data (Org name, domain taken from email, Billing email, phone (optional), billing address - Country, state, zip, address, TAX ID (different by country :) ))
- provide API for automated extraction of the "report"
- Agentic flow - based on the information provided - implement a flow that goes, collects information, performs verifications and produces the data needed and produces a "report" that can be queried by APIs and can serve the FE needs too

### Performance Benchmarks

accuracy is more important than speed, but the general expectation is that a single company screen should not take more than 2 hours

### Code Quality Expectations

monorepo for BE, FE, documentation, tests for both BE, FE
