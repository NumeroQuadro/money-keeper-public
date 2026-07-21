# North Star

Status: canonical source of truth for Money Keeper web visual direction and UI copy.

Last updated: 2026-03-31

## Scope

This document applies to the web workspace in `web-react`.

If this document conflicts with `RALPH.md` or `docs/ui-slitch-redesign-brief.md`, this document wins.

For page-level behavior and content boundaries, use `docs/PAGE_CONTRACTS.md`.
For current implemented behavior, use `FEATURES.md`.

## Product Frame

Money Keeper is a PDF-first personal finance banking workspace for one owner managing many bank accounts and cards.

The web product must feel like:
- a trustworthy banking operations workspace
- a calm admin surface for investigation and review
- a production product, not a concept piece

The web product must not feel like:
- an investment dashboard
- a crypto or NFT product
- a marketplace or directory
- an app-store landing page
- a migration shell
- a glossy design exercise

## UI Truth Hierarchy

For web UI and UX decisions, use this order:
1. `docs/NORTH_STAR.md` for visual direction and copy
2. `docs/PAGE_CONTRACTS.md` for page purpose and IA contracts
3. `PRD.md` for product scope and high-level requirements
4. `FEATURES.md` for current implemented behavior and evidence
5. `docs/ui-slitch-redesign-brief.md` and `RALPH.md` as supporting reference/process docs only

## Visual Direction

- light neutral background
- white surfaces
- teal accent
- coral for warnings, anomalies, and destructive emphasis
- soft radii
- minimal shadows
- subtle borders preferred over elevation
- calm left sidebar
- one dark hero max per page
- simple charts with rounded bars
- restrained spacing and stable layout

## Layout and Composition Rules

- Every page gets one primary job and one dominant information area.
- Remove old UI before adding new UI.
- Prefer fewer cards, fewer pills, fewer badges, and fewer helper sentences.
- Use supporting cards only when they help a decision on the same page.
- Avoid dashboard clutter on operational screens.
- Avoid tutorial framing on core daily-use pages.

## Chart Rules

- Charts must summarize money behavior in a few seconds.
- Prefer rounded bars and simple lines over analytics-heavy chrome.
- Keep axes, grid, and labels minimal.
- Use muted context colors for most data.
- Use teal for focus/current selection.
- Use coral only for warnings or negative emphasis.

## Copy and Terminology Rules

Allowed:
- plain operational banking language
- short labels focused on money movement, review status, balances, and statements
- boring trustworthy product copy

Forbidden:
- investment language
- portfolio framing
- wealth-advisor vocabulary
- speculative or promotional language
- conceptual filler copy

Avoid terms such as:
- market data
- yield
- intelligence
- vault
- curator
- premium insights
- assets

## Non-Negotiable Product Framing

- PDF statements remain the source of truth.
- Review and auditability stay central.
- Net worth is cash-based and belongs inside `Accounts`.
- Rules live under `Settings -> Automation`, not in primary navigation.
- The primary navigation remains exactly five pages:
  - `Overview`
  - `Transactions`
  - `Review`
  - `Accounts`
  - `Statements`

## Supporting References

- `docs/ui-slitch-redesign-brief.md` records the original redesign brief and reference inputs.
- `RALPH.md` defines repeated-pass behavior and simplification heuristics.

Those files may guide process and historical context, but they must not redefine the visual direction.
