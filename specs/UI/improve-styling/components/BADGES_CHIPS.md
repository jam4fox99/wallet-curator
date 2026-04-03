# Badges & Chips

Defines status chips, summary pills, history state labels, and compact metric chips.

## Polymarket Reference Values

- Low-height pills with `600` weight
- Neutral chips use dark surface + border
- Positive chips use green text on subtle green wash
- Negative chips use red text on subtle red wash
- Warning chips use amber, but sparingly

## Current Dashboard Mapping

- `.pm-status-chip*`
- `.pm-metric-chip*`
- `.pm-change-badge*`
- `.pm-history-status*`
- `.pm-summary-strip span`
- `.pm-settings-row__count-pill`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Badge styles | Several unrelated treatments | One shared chip language |
| Success/danger hues | Mixed values | Use system tokens |
| Radius | Mostly pill | Keep pill, normalize spacing/weight |

## Implementation Snippet

```css
.pm-status-chip,
.pm-history-status,
.pm-summary-strip span,
.pm-settings-row__count-pill {
  border-radius: 999px;
  border: 1px solid var(--pm-border);
  background: var(--pm-surface-alt);
  color: var(--pm-text-secondary);
  font-weight: 600;
}

.pm-status-chip--success,
.pm-history-status--applied {
  color: var(--pm-green);
  background: rgba(61, 180, 104, 0.12);
}

.pm-status-chip--danger,
.pm-change-badge--negative {
  color: var(--pm-red);
  background: rgba(255, 77, 77, 0.12);
}
```
