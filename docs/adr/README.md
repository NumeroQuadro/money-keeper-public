# Architecture Decision Records (ADRs)

This folder contains **Architecture Decision Records** for Money Keeper.

## How to use

- Create a new ADR when making an architectural decision that should be durable.
- Use the template in `docs/adr/0000-template.md`.
- Keep ADRs small and focused: one decision per ADR.

## Naming

- `NNNN-short-title.md` (e.g., `0001-import-processing-background-tasks.md`)
- Use a 4-digit, zero-padded sequence number.

## Status values

- Proposed
- Accepted
- Superseded (link the superseding ADR)
- Deprecated

## Notes for agents

Agents must read all **Accepted** ADRs before implementing changes that could affect architecture.
