# Typography

Caps the dashboard at the Polymarket weight/size scale and maps that scale onto existing headings, labels, and data text.

## Polymarket Reference Values

| Role | Size | Weight | Notes |
| --- | --- | --- | --- |
| Eyebrow / label | `12px` | `600` | Uppercase, secondary color |
| Small body | `13px` | `400`/`500` | Helper copy |
| Standard UI text | `14px` | `400`/`500`/`600` | Inputs/buttons |
| Section support | `16px` | `500`/`600` | Tabs / stronger inline text |
| Side title | `24px` | `600` | Dense section header |
| Hero title | `28px` | `600` | Main section title |

## Current Dashboard Mapping

- `.pm-kicker`
- `.pm-section-title`
- `.pm-side-title`
- `.pm-field-label`
- `.pm-metric-value`
- `.pm-stat-tile__label`
- `.pm-button`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Font weights | Frequent `700/800` | Cap visible UI at `600` |
| Section title | `28px/700` | `24px` or `28px/600` depending on context |
| Big metrics | `48px/56px` heavy | Keep size hierarchy but soften weight |
| Buttons | `13px/700` | `14px/400-600` |

## Implementation Snippet

```css
.pm-kicker,
.pm-field-label,
.pm-stat-tile__label,
.pm-metric-chip__label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.pm-section-title {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.36px;
}

.pm-side-title,
.pm-header-title,
.pm-history-list__title {
  font-weight: 600;
}

.pm-button,
.pm-tab,
.pm-range-pill {
  font-weight: 600;
}
```
