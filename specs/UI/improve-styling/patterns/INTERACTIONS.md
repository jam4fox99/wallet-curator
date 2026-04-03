# Interactions

Defines hover, focus, copy feedback, and subtle motion patterns so the restyle feels cohesive instead of a set of isolated color swaps.

## Polymarket Reference Values

- Hover lift: `transform 0.12s cubic-bezier(0.4, 0, 0.2, 1)`
- Focus glow: soft blue ring, not browser default
- Copyable text uses accent on hover and positive confirmation on active state
- Table rows and chips should react with background/value shifts, not large motion

## Current Dashboard Mapping

- `.pm-button:hover`
- `.pm-range-pill:hover`
- `.pm-tab:hover`
- `.pm-wallet-copyable`
- `.pm-tier-table__row:hover`
- Dropdown focus states

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Motion timing | `140ms ease` scattered | One shared cubic-bezier curve |
| Focus behavior | Missing on many inputs | Shared blue focus ring |
| Hover language | Heavy blue haze in places | Subtle, consistent emphasis |

## Implementation Snippet

```css
.pm-button,
.pm-range-pill,
.pm-tab,
.pm-tier-table__row,
.pm-wallet-copyable {
  transition: var(--pm-transition), color 0.12s ease, background-color 0.12s ease, border-color 0.12s ease;
}

.pm-wallet-copyable:hover {
  color: var(--pm-blue);
}

.pm-wallet-copyable:active {
  color: var(--pm-green);
}

:focus-visible {
  outline: none;
  box-shadow: var(--pm-focus-glow);
}
```
