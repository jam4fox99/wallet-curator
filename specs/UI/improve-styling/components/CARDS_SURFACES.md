# Cards & Surfaces

Defines the shared Polymarket surface language for cards, shells, stat tiles, and empty states.

## Polymarket Reference Values

- Main card background: `#181d21`
- Nested/input-like surface: `#1e2428`
- Border: `#242b32`
- Large radius: `15.2px`
- Inner card radius: `9.2px`
- Shadows are restrained; borders do most of the work

## Current Dashboard Mapping

- `.pm-surface`
- `.pm-chart-shell`
- `.pm-stat-tile`
- `.pm-wallet-admin-card`
- `.pm-empty-state`
- `.pm-tier-collapsible`
- `.pm-collapsible-section`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Primary cards | Gradient overlays | Cleaner solid surfaces |
| Card radius | `14-18px` mixed | `15.2px` outer, `9.2px` inner |
| Empty state | Dashed, lightweight | More intentional nested surface |
| Nested shells | Multiple visual languages | One consistent alt-surface treatment |

## Implementation Snippet

```css
.pm-surface,
.pm-chart-shell,
.pm-tier-collapsible,
.pm-collapsible-section,
.pm-admin-card {
  background: var(--pm-surface);
  border: 1px solid var(--pm-border);
  border-radius: var(--pm-radius-lg);
}

.pm-stat-tile,
.pm-wallet-admin-card,
.pm-empty-state,
.pm-metric-chip {
  background: var(--pm-surface-alt);
  border: 1px solid var(--pm-border);
  border-radius: var(--pm-radius-md);
}
```
