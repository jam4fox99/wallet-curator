# Tabs

Defines the tab rail and active/inactive tab treatment.

## Polymarket Reference Values

| Element | Value |
| --- | --- |
| Active text | `#e5e5e5` |
| Inactive text | `#7b8996` |
| Active emphasis | Minimal fill, not a loud pill |
| Font | `16px/600` |
| Rail feel | Clean, low-chrome, surface-adjacent |

## Current Dashboard Mapping

- `.pm-tab-rail`
- `.pm-tabs-shell`
- `.pm-tab`
- `.pm-tab--selected`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Active state | Filled blue-ish pill | Lower-chrome active tab |
| Font size | `14px` | `16px` |
| Hover | White haze | Slight surface-alt emphasis |

## Implementation Snippet

```css
.pm-tab {
  font-size: 16px;
  font-weight: 600;
  border-radius: var(--pm-radius-md);
  color: var(--pm-text-secondary);
}

.pm-tab--selected {
  color: #e5e5e5 !important;
  background: rgba(255, 255, 255, 0.04) !important;
  box-shadow: inset 0 -1px 0 rgba(255, 255, 255, 0.08);
}
```
