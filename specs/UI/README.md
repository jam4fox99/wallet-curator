# UI Reference Index

This folder is the single home for UI research, audits, and implementation specs.

## Structure

- `reference/audits/`
  - Raw dashboard audits and inventories extracted from the current Wallet Curator UI.
  - Start here when you need to understand what exists today.

- `reference/charts/`
  - Polymarket chart reference material and the earlier chart integration guides.
  - Use these as visual/reference inputs, not as the new design-system spec.

- `improve-styling/`
  - Implementation-oriented specs for the Polymarket-style restyle.
  - Use these when changing `assets/dashboard-ui.css` or UI-related hooks in `app.py`.

## Recommended Reading Order

1. `reference/audits/QUICK_REFERENCE.md`
2. `reference/audits/UI_AUDIT.md`
3. `reference/charts/POLYMARKET_PNL_CHART_SPEC.md`
4. `improve-styling/foundations/DESIGN_TOKENS.md`
5. `improve-styling/foundations/TYPOGRAPHY.md`
6. The relevant component spec under `improve-styling/components/`

## Notes

- Non-UI project specs remain in `specs/`.
- UI-specific docs should not be added back to the repo root.
- If a new UI reference is created, place it under `reference/` unless it is an actionable implementation spec, in which case place it under `improve-styling/`.
