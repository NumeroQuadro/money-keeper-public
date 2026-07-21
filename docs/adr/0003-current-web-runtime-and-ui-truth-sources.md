# ADR 0003: Current Web Runtime and UI Truth Sources

- Status: Accepted
- Date: 2026-03-31

## Context

ADR 0002 documented a reset step where older web surfaces were removed so redesign work could restart from a clean baseline.

The current repository now contains a rebuilt React workspace in `web-react`, a `web` service in `docker-compose.yml`, routed primary pages, page tests, and frontend handoff notes. At the same time, multiple generated `design-system/*` files and legacy prompt documents were still describing conflicting product categories and UI directions such as crypto, NFT, marketplace, app-store, or portfolio patterns.

That left multiple competing sources of truth for UI/UX:
- ADR 0002 describing the web surface as removed
- `PRD.md` mixing live-web statements with reset-era statements
- `README.md` claiming there was no web UI in the repo
- generated `design-system/*` docs describing non-banking products
- prompt/reference docs duplicating visual/page rules in multiple places

## Decision

1. The repository is treated as having an active current web workspace in `web-react`.
2. `docs/NORTH_STAR.md` is the canonical source for web visual direction and UI copy.
3. `docs/PAGE_CONTRACTS.md` is the canonical source for primary page purpose and IA contracts.
4. `PRD.md` keeps high-level product requirements and points to those canonical docs instead of duplicating page and visual rules.
5. `docs/ui-slitch-redesign-brief.md` and `RALPH.md` are supporting reference/process docs only and must defer to the canonical docs.
6. Conflicting generated `design-system/*` files are removed rather than retained as alternate UI truth sources.

## Consequences

- Future agents no longer need to infer whether the web workspace exists.
- The repo has one visual source of truth and one page-contract source of truth.
- Historical reset context is preserved without remaining the active architectural description.
- Banking-workspace direction is protected from drift into crypto, NFT, marketplace, landing-page, or investment-dashboard framing.

## Implementation Notes

- `README.md`, `PRD.md`, and `FEATURES.md` were reconciled with the current repo state.
- `docs/adr/0002-web-surface-reset-before-redesign.md` now remains historical only.
- `design-system/` conflicting generated files were removed.

## Alternatives Considered

- Keep ADR 0002 as the active truth and rely on README updates only: rejected because accepted ADRs outrank README and would keep misleading future agents.
- Keep generated `design-system/*` files as archived references inside the same path: rejected because they would continue to appear as candidate truth sources during repo scans.

## Links

- Historical reset: `docs/adr/0002-web-surface-reset-before-redesign.md`
- Canonical visual direction: `docs/NORTH_STAR.md`
- Canonical page contracts: `docs/PAGE_CONTRACTS.md`
