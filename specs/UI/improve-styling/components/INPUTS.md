# Inputs

Defines dropdowns, date inputs, numeric settings fields, and freeform text areas.

## Polymarket Reference Values

| Input | Background | Radius | Font | Notes |
| --- | --- | --- | --- | --- |
| Search/dropdown | `#1e2428` | `9.2px` | `14px` | Blue focus glow |
| Amount / strong input | Transparent + large type | `9.2px` | Large | Not used directly, style principle only |
| Textarea | `#1e2428` | `9.2px` | `14px` | Clean border, no harsh inset |

## Current Dashboard Mapping

- `.pm-wallet-dropdown`
- `.pm-date-range`
- `.pm-date-input`
- `.pm-settings-input`
- `.pm-textarea`
- Wallet curation textarea in `app.py`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Dropdown radius | `14px` | `9.2px` |
| Input backgrounds | Mixed transparent/surface | Uniform `#1e2428` |
| Focus | Often none | Blue glow across controls |
| Curation textarea | Inline style block | Shared `.pm-textarea` class |

## Implementation Snippet

```css
.pm-textarea,
.pm-settings-input,
.pm-date-range,
.pm-wallet-dropdown .Select-control,
.dash-dropdown .Select-control {
  background: var(--pm-surface-alt) !important;
  border: 1px solid var(--pm-border) !important;
  border-radius: var(--pm-radius-md) !important;
}

.pm-textarea:focus,
.pm-settings-input:focus,
.pm-date-input:focus,
.dash-dropdown.is-focused .Select-control {
  box-shadow: var(--pm-focus-glow) !important;
}
```
