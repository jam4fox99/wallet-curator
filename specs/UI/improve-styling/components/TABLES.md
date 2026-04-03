# Tables

Covers sortable headers, row hover behavior, dense wallet tables, and the Dash daily breakdown table shell.

## Polymarket Reference Values

- Dense rows with crisp separators
- Hover background closer to `#1e2428` than blue glow
- Header text uses secondary text and `600` weight
- Numeric data stays readable but not oversized

## Current Dashboard Mapping

- `.pm-tier-table`
- `.pm-tier-table__th`
- `.pm-tier-table__row`
- `.pm-tier-table__cell`
- `.pm-table-shell`
- `.pm-daily-table-shell`
- `#daily-table`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Row hover | Blue-tinted hover | Surface-alt hover |
| Header chrome | Soft but inconsistent | Uniform dense table headers |
| Outer shell | `16px` / translucent | Same UI token system as other cards |

## Implementation Snippet

```css
.pm-tier-table__th {
  background: transparent;
  color: var(--pm-text-secondary);
  border-bottom: 1px solid var(--pm-border);
}

.pm-tier-table__row:hover,
#daily-table tr:hover td {
  background: rgba(30, 36, 40, 0.72);
}

.pm-table-shell,
.pm-daily-table-shell {
  border-radius: var(--pm-radius-lg);
  border: 1px solid var(--pm-border);
  background: var(--pm-surface);
}
```
