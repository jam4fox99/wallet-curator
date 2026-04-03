# Buttons

Documents the actionable surfaces: CTAs, secondary buttons, compact actions, and range pills.

## Polymarket Reference Values

| Variant | Background | Text | Radius | Height | Font |
| --- | --- | --- | --- | --- | --- |
| Primary CTA | `#0093fd` | `#ffffff` | `9.2px` | `43px` | `16px/400` |
| Secondary / quick action | `#242b32` | `#dee3e7` | `9.2px` | `30-43px` | `12-14px/600` |
| Buy | `#359a5e` | `#ffffff` | `7.2px` | `48px` | `14px/600` |
| Sell | `rgba(203,49,49,0.15)` | `#cb3131` | `7.2px` | `48px` | `14px/600` |
| Range pill active | `rgba(0,147,253,0.16)` | `#dee3e7` | `9.2px` | `30-34px` | `12px/600` |

## Current Dashboard Mapping

- `.pm-button`
- `.pm-button--primary`
- `.pm-button--secondary`
- `.pm-button--danger`
- `.pm-button--inline`
- `.pm-range-pill`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Primary CTA | Blue-purple gradient | Flat `#0093fd` |
| Buy/sell states | Inline color hacks in `app.py` | Dedicated CSS variants |
| Inline actions | Tiny sharp corners | Keep compact but move to token radius |
| Motion | Generic `140ms ease` | shared Polymarket cubic-bezier |

## Implementation Snippet

```css
.pm-button {
  border-radius: var(--pm-radius-md);
  transition: var(--pm-transition), background-color 0.12s ease, border-color 0.12s ease, color 0.12s ease;
}

.pm-button--primary {
  background: var(--pm-blue);
  color: #fff;
}

.pm-button--quick,
.pm-button--secondary {
  background: #242b32;
  color: var(--pm-text);
}

.pm-button--buy {
  background: var(--pm-buy);
  color: #fff;
  border-radius: var(--pm-radius-sm);
}

.pm-button--sell {
  background: var(--pm-sell-bg);
  color: var(--pm-sell-text);
  border-radius: var(--pm-radius-sm);
}
```
