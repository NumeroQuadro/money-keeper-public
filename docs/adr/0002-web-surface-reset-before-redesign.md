# ADR 0002: Remove Existing Web Surface Before Full Redesign

- Status: Superseded by ADR 0003
- Date: 2026-03-23

## Note

This ADR remains as the historical record of the reset step.

Current runtime/web truth moved to ADR 0003 once the rebuilt `web-react` workspace and canonical UI docs were re-established.

## Context

The repository contained two web implementations (legacy static and React migration output).
The product owner requested a full website redesign from scratch and asked to remove old web
implementation code to avoid carrying forward existing UX/layout constraints.

ADR 0001 described a stage-gated migration path between those two implementations. That path is
no longer relevant once both implementations are removed.

## Decision

The existing web surface is removed from the repository:

1. Delete legacy static web code (`web/`).
2. Delete React web code (`web-react/`).
3. Remove web services/build targets from runtime tooling (`docker-compose.yml`, `Makefile`).
4. Remove Telegram bot web-link integration tied to the deleted web client.
5. Reconcile product docs/contracts (`PRD.md`, `FEATURES.md`, `README.md`) with the removed surface.

## Consequences

- The active product runtime is API + Telegram bot only until a new web implementation is introduced.
- Frontend rollout/rollback logic from ADR 0001 is retired.
- A future web rewrite can start from a clean baseline without legacy code coupling.

## Implementation Notes

- Removed directories: `web/`, `web-react/`.
- Tooling updated to stop building/running web containers.
- Bot no longer renders “Open web UI” actions.

## Alternatives Considered

- Keep old web implementations during redesign: rejected due to high maintenance noise and accidental reuse risk.
- Keep only one of the old web implementations: rejected because the request is for a clean redesign baseline.

## Links

- Supersedes: `docs/adr/0001-stage-gated-react-migration.md`
- Superseded by: `docs/adr/0003-current-web-runtime-and-ui-truth-sources.md`
