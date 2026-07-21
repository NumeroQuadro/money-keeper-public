# Page Contracts

Status: canonical source of truth for Money Keeper primary web page contracts.

Last updated: 2026-03-31

## Scope

This document defines the primary web workspace contract for `web-react`.

If this document conflicts with `RALPH.md` or `docs/ui-slitch-redesign-brief.md`, this document wins.

For visual and copy direction, use `docs/NORTH_STAR.md`.
For current implemented behavior and evidence, use `FEATURES.md`.

## Global IA Invariants

- Primary navigation must be exactly:
  - `Overview`
  - `Transactions`
  - `Review`
  - `Accounts`
  - `Statements`
- `Rules` must live under `Settings -> Automation`.
- Transfers and exceptions must not exist as separate top-level pages.
- Net worth must not exist as a separate primary page.
- Cross-page drill-downs should move users into the correct workspace instead of duplicating the same content in multiple pages.

## Overview

Question it answers: `what happened this month?`

Must contain:
- concise month summary
- true income, true spending, and net cashflow context
- one primary trend or summary visualization
- only a small number of supporting context modules
- direct handoff into `Transactions` and `Review` where useful

Must not contain:
- deep queue triage as the main purpose
- dashboard clutter
- investment framing
- large card grids competing for attention

## Transactions

Question it answers: `what is this row?`

Must contain:
- dense filterable transaction list
- clear row-level detail
- provenance and auditability context
- fast search and filter interactions
- drill-down support from other pages

Must not contain:
- hero-heavy dashboard composition
- duplicated review inbox content
- marketing or storytelling modules unrelated to row inspection

## Review

Question it answers: `what needs my input now?`

Must contain:
- transfer suggestions
- open exceptions
- transactions that still need human review
- parse and reconciliation anomalies
- list-first inbox layout (prioritized unresolved list + detail/action panel)
- clear actions such as confirm, reject, resolve, and skip
- keyboard-friendly triage (arrow keys, escape)
- visible impact of unresolved items

Must not contain:
- separate top-level transfer or exception pages
- exploratory analytics as primary content
- loud stats-card grids above the fold
- passive dashboards with no next action

## Accounts

Question it answers: `what is my current balance by account, and how did it change?`

Must contain:
- account list with current balances
- balance freshness or statement recency context
- cash-based net worth summary inside this page
- simple account trend context
- handoff into filtered `Transactions`

Must not contain:
- separate primary net-worth route
- investment holdings framing
- portfolio language

## Statements

Question it answers: `what statement data is loaded and what is its status?`

Must contain:
- imported statement history
- file and statement status context
- reconciliation and import outcomes
- straightforward management actions for admin workflows

Must not contain:
- unrelated rules workflows as primary content
- review queue content as the main use of the page

## Change Rule

If a future pass changes any primary page purpose, navigation structure, or page-level must/must-not rules, update this file first and then reconcile `PRD.md` and `FEATURES.md`.
