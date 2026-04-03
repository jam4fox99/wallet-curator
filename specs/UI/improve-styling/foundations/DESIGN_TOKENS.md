# Design Tokens

Defines the shared color, radius, spacing, and motion tokens for the Polymarket-style restyle.

## Polymarket Reference Values

| Token | Value | Usage |
| --- | --- | --- |
| Page background | `#15191d` | App shell background |
| Surface | `#181d21` | Cards, shells, modals |
| Surface alt | `#1e2428` | Inputs, hover layers |
| Border | `#242b32` | Standard outlines |
| Primary blue | `#0093fd` | CTAs, active state, links |
| Buy green | `#359a5e` | Buy/approve actions |
| Sell red text | `#cb3131` | Sell/skip actions |
| Sell red bg | `rgba(203, 49, 49, 0.15)` | Sell button surface |
| Positive P&L | `#3db468` | Positive values |
| Negative P&L | `#ff4d4d` | Negative values |
| White heading | `#ffffff` | Strong headlines |
| Primary text | `#dee3e7` | Body/value text |
| Secondary text | `#7b8996` | Labels/help text |
| Radius xs | `4px` | Tiny chips |
| Radius sm | `7.2px` | Buy/sell buttons |
| Radius md | `9.2px` | Inputs/CTAs |
| Radius lg | `15.2px` | Cards/chart shells |
| Motion | `transform 0.12s cubic-bezier(0.4, 0, 0.2, 1)` | Hover lift |

## Current Dashboard Mapping

- CSS source: `assets/dashboard-ui.css:root`
- App mirror: `app.py` `COLORS`
- Affected classes: `.pm-surface`, `.pm-button*`, `.pm-wallet-dropdown`, `.pm-stat-tile`, `.pm-status-chip`

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Radius system | Mixed `10px/12px/14px/16px/18px` | Normalize to `4/7.2/9.2/15.2` |
| Button color system | Blue/purple gradient + generic red | Flat Polymarket blue + explicit buy/sell tokens |
| Focus treatment | Mostly absent | Shared blue focus glow |
| Surface layering | Strong gradients in cards | Flatter, more disciplined surfaces |

## Implementation Snippet

```css
:root {
  --pm-bg: #15191d;
  --pm-surface: #181d21;
  --pm-surface-alt: #1e2428;
  --pm-border: #242b32;
  --pm-blue: #0093fd;
  --pm-buy: #359a5e;
  --pm-sell-text: #cb3131;
  --pm-sell-bg: rgba(203, 49, 49, 0.15);
  --pm-text: #dee3e7;
  --pm-text-secondary: #7b8996;
  --pm-radius-xs: 4px;
  --pm-radius-sm: 7.2px;
  --pm-radius-md: 9.2px;
  --pm-radius-lg: 15.2px;
  --pm-transition: transform 0.12s cubic-bezier(0.4, 0, 0.2, 1);
  --pm-focus-glow: 0 0 0 3px rgba(0, 147, 253, 0.18);
}
```
