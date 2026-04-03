# Wallet Curator Dashboard - Complete UI Component Audit

## Overview
This is a comprehensive inventory of every UI element in the Wallet Curator Dashboard (Dash/Python application with custom CSS). The goal is to identify all components that need restyling to match Polymarket design standards.

---

## 1. CSS COLOR & DESIGN TOKENS

### Root CSS Variables (`:root` selector)
```css
--pm-bg: #15191d;              /* Page background */
--pm-surface: #181d21;         /* Card/surface background */
--pm-surface-alt: #1e2428;     /* Alternative surface (slightly lighter) */
--pm-surface-soft: #15191d;    /* Soft/muted surface */
--pm-border: #242b32;          /* Primary border color */
--pm-border-soft: #1a202b;     /* Soft border color */
--pm-text: #dee3e7;            /* Primary text color */
--pm-text-secondary: #7b8996;  /* Secondary text color */
--pm-blue: #0093fd;            /* Primary blue accent */
--pm-green: #3db468;           /* Success/positive color */
--pm-red: #ff4d4d;             /* Danger/negative color */
--pm-purple: #9B51E0;          /* Purple accent */
--pm-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);  /* Drop shadow */
```

### Theme
- **Background Gradient**: Radial gradient (blue at top) + linear gradient (dark top to darker bottom)
- **Font Family**: "Inter", "Segoe UI", sans-serif
- **Monospace Font**: JetBrains Mono (used in tables)

---

## 2. LAYOUT COMPONENTS

### 2.1 Application Shell
**Class**: `.pm-app-shell`
- **Current**: Dark wrapper, min-height: 100vh
- **Structure**: Houses the entire application
- **Status**: Needs theming to match Polymarket

### 2.2 Top Navigation Bar (Topbar)
**Classes**: `.pm-topbar`, `.pm-topbar-inner`
- **Current**: 
  - Sticky positioning (top: 0, z-index: 40)
  - Backdrop blur (18px)
  - Semi-transparent dark background (rgba(9, 11, 16, 0.88))
  - 1px bottom border (rgba(255, 255, 255, 0.05))
  - Min-height: 84px
  - Responsive grid layout: auto | minmax(260px, 1fr) | minmax(280px, 520px)
- **Contains**: Brand, header title, status chips
- **Status**: Core layout element requiring Polymarket styling

### 2.3 Brand/Logo Section
**Classes**: `.pm-brand`, `.pm-brand-mark`, `.pm-brand-copy`, `.pm-brand-title`, `.pm-brand-subtitle`
- **Current**:
  - `.pm-brand`: Inline flex, gap 14px
  - `.pm-brand-mark`: 28x28px with two gradient spans (white + blue-purple)
  - `.pm-brand-title`: "Wallet Curator" - 17px, 700 weight, letter-spacing -0.02em
  - `.pm-brand-subtitle`: "Cloud Portfolio" - 12px, uppercase, letter-spacing 0.03em
- **Status**: Needs Polymarket brand replacement

### 2.4 Main Column Container
**Class**: `.pm-main-column`
- **Current**: Max-width 1540px, centered, responsive padding (clamp 16-28px)
- **Purpose**: All main page content wrapper
- **Status**: Layout only, no restyling needed

### 2.5 Main Content Area
**Classes**: `.pm-main-shell`, `.pm-page-stack`
- **Current**:
  - `.pm-main-shell`: Padding 22px 0 40px
  - `.pm-page-stack`: Flexbox column, gap 18px
- **Purpose**: Content stacking container
- **Status**: Layout only

### 2.6 Primary Grid Layouts
**Classes**: `.pm-overview-grid`, `.pm-wallet-grid`
- **Current**: 2-column grid (2fr | 360px), gap 18px, 460px auto-rows
- **Responsive**: Collapses to 1 column at 1100px breakpoint
- **Status**: Layout only

### 2.7 Tabs/Navigation Rail
**Classes**: `.pm-tab-rail`, `.pm-tabs-parent`, `.pm-tabs-shell`, `.pm-tab`, `.pm-tab--selected`
- **Current**:
  - `.pm-tab`: 42px height, 16px padding, transparent background
  - Color transition on hover: --pm-text-secondary → --pm-text
  - Selected state: rgba(0, 147, 253, 0.16) background with inset glow
  - Font: 14px, 600 weight, border-radius 12px
- **Status**: Needs Polymarket tab styling

---

## 3. CARD & SURFACE COMPONENTS

### 3.1 Surface/Card Container
**Class**: `.pm-surface`
- **Current**:
  - Background: Gradient overlay + surface color
  - Border: 1px solid var(--pm-border)
  - Border-radius: 18px
  - Box-shadow: var(--pm-shadow)
- **Status**: Core card styling, needs Polymarket update

### 3.2 Card Helper (_card function)
**Generated Classes**: Dynamic - combines "pm-surface" + custom class
- **Purpose**: Wrapper function for consistent card styling
- **Usage**: All major content cards use this
- **Status**: Needs refactoring to output Polymarket classes

### 3.3 Specific Card Types
**Classes**:
- `.pm-overview-hero` / `.pm-wallet-hero`: 24px padding, flex column, height 100%
- `.pm-side-card`: 22px padding, overflow hidden, flex column
- `.pm-wallet-side`: 22px padding, min-height 100%, flex column
- `.pm-breakdown-card`: 22px padding
- `.pm-admin-card`: 24px padding

**Status**: All need color and style updates

### 3.4 Card Headers
**Classes**: `.pm-card-head`, `.pm-card-head--tight`, `.pm-card-title-block`
- **Current**:
  - `.pm-card-head`: Flex, space-between, gap 16px
  - `.pm-card-head--tight`: Aligns center instead of flex-start
  - `.pm-card-title-block`: Flex column, gap 4px
- **Status**: Layout only, no color changes

---

## 4. TYPOGRAPHY COMPONENTS

### 4.1 Section Titles
**Classes**: `.pm-kicker`, `.pm-section-title`, `.pm-side-title`
- **Current**:
  - `.pm-kicker`: 11px, 700 weight, uppercase, letter-spacing 0.06em, color: --pm-text-secondary
  - `.pm-section-title`: 28px, 700 weight, letter-spacing -0.035em, line-height 1.05
  - `.pm-side-title`: 22px, 700 weight (same as main title)
  - `.pm-section-title--blue`: Override color to --pm-blue
- **Status**: Needs Polymarket typography scaling

### 4.2 Labels & Field Labels
**Classes**: `.pm-metric-label`, `.pm-field-label`
- **Current**: 12px, 600 weight, uppercase, letter-spacing 0.05em, color: --pm-text-secondary
- **Status**: Needs Polymarket label styling

### 4.3 Metric Values
**Classes**: `.pm-metric-value`, `.pm-metric-value--wallet`
- **Current**:
  - `.pm-metric-value`: 56px, 800 weight, letter-spacing -0.045em, line-height 1
  - `.pm-metric-value--wallet`: 48px (smaller variant)
- **Status**: Needs Polymarket sizing

### 4.4 Secondary Text
**Class**: `.pm-range-copy`
- **Current**: 13px, line-height 1.45, color: --pm-text-secondary
- **Status**: Needs Polymarket color update

---

## 5. BUTTON COMPONENTS

### 5.1 Primary Button
**Class**: `.pm-button.pm-button--primary`
- **Current**:
  - Min-height: 46px, padding: 0 14px
  - Background: Linear gradient (--pm-blue to --pm-purple)
  - Color: white
  - Border: 1px solid rgba(0, 147, 253, 0.75)
  - Border-radius: 12px
  - Font: 13px, 700 weight, letter-spacing -0.01em
  - Hover: translateY(-1px)
- **Status**: Needs Polymarket primary button styling

### 5.2 Secondary Button
**Class**: `.pm-button.pm-button--secondary`
- **Current**:
  - Background: rgba(255, 255, 255, 0.03)
  - Border: 1px solid rgba(255, 255, 255, 0.08)
  - Color: --pm-text
  - Hover: rgba(255, 255, 255, 0.06) background
- **Status**: Needs Polymarket secondary button styling

### 5.3 Danger Button
**Class**: `.pm-button.pm-button--danger`
- **Current**:
  - Color: #ffb4bc
  - Background: rgba(255, 90, 103, 0.08)
  - Border: 1px solid rgba(255, 90, 103, 0.22)
  - Hover: Brighter color and background
- **Status**: Needs Polymarket danger button styling

### 5.4 Inline Button (Compact)
**Class**: `.pm-button.pm-button--inline`
- **Current**: Min-height 38px, padding 0 14px
- **Variants**: `.pm-button--remove` (red), `.pm-button--promote` (green), `.pm-button--demote` (orange)
- **Status**: Needs Polymarket inline button styling

### 5.5 Range Pills (Date Range Selector)
**Class**: `.pm-range-pill`, `.pm-range-pill--active`
- **Current**:
  - 34px height, 12px padding, border-radius 10px
  - Color: --pm-text-secondary by default
  - Active state: rgba(0, 147, 253, 0.16) with inset glow
  - Hover: rgba(255, 255, 255, 0.05)
  - Font: 12px, 700 weight
- **Location**: Used for date range selection (1D, 7D, 2W, 30D, ALL)
- **Status**: Needs Polymarket pill styling

---

## 6. INPUT & FORM COMPONENTS

### 6.1 Text Input (Date Input)
**Class**: `.pm-date-input`
- **Current**:
  - Width: 130px, height: 42px
  - Transparent background
  - Color: --pm-text
  - Border: none
  - Font: 14px, 600 weight, monospace
  - Custom webkit picker styling (inverted filter)
- **Status**: Needs Polymarket input styling

### 6.2 Textarea
**Class**: `.pm-textarea`
- **Current**:
  - Full width
  - Padding: 14px 16px
  - Border-radius: 14px
  - Border: 1px solid rgba(255, 255, 255, 0.08)
  - Background: rgba(255, 255, 255, 0.03)
  - Color: --pm-text
  - Font: Inter, 13px, line-height 1.5
  - Resize: vertical
- **Used for**: CSV input, comments
- **Status**: Needs Polymarket textarea styling

### 6.3 Settings Input (Number)
**Class**: `.pm-settings-input`
- **Current**:
  - Width: 100px, height: 42px
  - Border: 1px solid rgba(255, 255, 255, 0.08)
  - Border-radius: 10px
  - Background: rgba(255, 255, 255, 0.03)
  - Color: --pm-text
  - Font: 15px, 600 weight, tabular-nums
  - Text-align: right
- **Used for**: Percentage inputs
- **Status**: Needs Polymarket input styling

### 6.4 Dropdown/Select (dcc.Dropdown)
**Classes**: `.pm-wallet-dropdown`, `.dash-dropdown`
- **Current**:
  - Background: var(--pm-surface)
  - Border: 1px solid var(--pm-border)
  - Color: --pm-text
  - Min-height: 52px
  - Border-radius: 14px
  - Menu: 8px margin-top, same styling
  - Options: 13px font, blue focus state (rgba(79, 140, 255, 0.12))
- **Extensive overrides**: Multiple specificity levels for Dash dropdown components
- **Status**: Completely custom, needs Polymarket redesign

### 6.5 Date Range Picker
**Class**: `.pm-date-range`
- **Current**:
  - Inline flex, gap 10px
  - Min-height: 46px, padding: 0 12px
  - Border-radius: 14px
  - Border: 1px solid rgba(255, 255, 255, 0.08)
  - Background: rgba(255, 255, 255, 0.03)
  - Contains: start input, arrow (→), end input
- **Status**: Needs Polymarket styling

---

## 7. BADGE & STATUS COMPONENTS

### 7.1 Status Chips (Top Bar)
**Classes**: `.pm-status-chip`, `.pm-status-chip--success`, `.pm-status-chip--info`, `.pm-status-chip--danger`
- **Current**:
  - Display: inline-flex, min-height 34px
  - Padding: 0 12px
  - Border-radius: 999px
  - Base: rgba(255, 255, 255, 0.03) background, 1px border
  - Success: Green (#3db468)
  - Info/Warning: Blue (#c5d6ff)
  - Danger: Red (#ff9aa2)
  - Font: 12px, 600 weight
- **Status**: Needs Polymarket status styling

### 7.2 Change Badges
**Classes**: `.pm-change-badge`, `.pm-change-badge--positive`, `.pm-change-badge--negative`
- **Current**:
  - Display: inline-flex, min-height 24px
  - Padding: 0 9px
  - Border-radius: 999px
  - Font: 10px, 800 weight, letter-spacing 0.06em
  - Positive: Green background
  - Negative: Red background
- **Used for**: Wallet change indicators
- **Status**: Needs Polymarket styling

### 7.3 History Status Badges
**Classes**: `.pm-history-status`, `.pm-history-status--pending`, `.pm-history-status--applied`, `.pm-history-status--reverted`
- **Current**:
  - Display: inline-flex, width fit-content
  - Min-height: 30px, padding: 0 10px
  - Border-radius: 999px
  - Pending: Blue (#c5d6ff)
  - Applied: Green (#3db468)
  - Reverted: Yellow (#f0c15a)
  - Font: 12px, 700 weight
- **Status**: Needs Polymarket status styling

### 7.4 Metric Chips
**Classes**: `.pm-metric-chip`, `.pm-metric-chip--positive`, `.pm-metric-chip--negative`
- **Current**:
  - Min-width: 110px, padding: 10px 12px
  - Border-radius: 12px
  - Background: rgba(255, 255, 255, 0.03)
  - Border: 1px solid rgba(255, 255, 255, 0.05)
  - Label: 10px, uppercase
  - Value: 14px, 700 weight
- **Used for**: Displaying metrics in cards
- **Status**: Needs Polymarket styling

### 7.5 Stat Tiles
**Classes**: `.pm-stat-tile`, `.pm-stat-tile__label`, `.pm-stat-tile__value`
- **Current**:
  - Padding: 14px
  - Border-radius: 14px
  - Background: rgba(255, 255, 255, 0.025)
  - Border: 1px solid rgba(255, 255, 255, 0.05)
  - Label: 11px, uppercase, secondary color
  - Value: 14px, 700 weight
- **Variants**: --positive (green), --negative (red)
- **Used for**: Summary statistics
- **Status**: Needs Polymarket styling

### 7.6 Summary Pills
**Class**: `.pm-summary-strip` > `span`
- **Current**:
  - Display: inline-flex, min-height 30px
  - Padding: 0 11px
  - Border-radius: 999px
  - Background: rgba(255, 255, 255, 0.03)
  - Border: 1px solid rgba(255, 255, 255, 0.05)
  - Color: --pm-text-secondary
  - Font: 12px, 600 weight
- **Used for**: Push summary labels
- **Status**: Needs Polymarket styling

### 7.7 Settings Count Pill
**Class**: `.pm-settings-row__count-pill`
- **Current**: Same as summary pills (30px height, inline-flex, rounded)
- **Status**: Needs Polymarket styling

### 7.8 Game Badge/Emoji
**Class**: `.pm-game-badge`
- **Current**:
  - Display: inline-flex, min-width 40px, height 40px
  - Border-radius: 8px
  - Contains emoji (font-size: 18px)
- **Status**: Layout only, no color changes

---

## 8. TABLE COMPONENTS

### 8.1 Tier Table (Main Table)
**Classes**: `.pm-tier-table`, `.pm-tier-table__th`, `.pm-tier-table__row`, `.pm-tier-table__cell`
- **Current**:
  - Font-family: JetBrains Mono (monospace)
  - Font-size: 13px
  - Background: transparent
  - Border-collapse: collapse
  - Table-layout: fixed
  - Header padding: 4px 6px
  - Cell padding: 1px 6px
  - Row hover: rgba(79, 140, 255, 0.04) background
  - Cell vertical-align: middle
  - Numeric alignment: text-align center (`.pm-num`)
- **Sort Indicators**: ⇅ (default), ▲ (asc), ▼ (desc)
- **Status**: Needs Polymarket table styling

### 8.2 Daily Breakdown Table
**Class**: `.pm-daily-table-shell`
- **Current**:
  - Border-radius: 16px
  - Overflow: auto
  - Border: 1px solid rgba(255, 255, 255, 0.05)
  - Max-height: 760px
  - Wraps a Dash DataTable
- **Status**: Needs Polymarket styling

### 8.3 Table Column Width Classes
- `.col-wallet`, `.col-game`, `.col-pnl`, `.col-markets`, `.col-days`, `.col-trades`, `.col-actions`
- `.col-filter`, `.col-actual`, `.col-sim`, `.col-invested`, `.col-realized`, `.col-unrealized`, `.col-total-pnl`
- These set fixed percentages in Colgroup elements
- **Status**: Layout only

### 8.4 Wallet Copyable
**Class**: `.pm-wallet-copyable`
- **Current**:
  - Font-size: 11px, 500 weight
  - Color: --pm-text
  - Cursor: pointer
  - User-select: all
  - Hover: --pm-blue color
  - Active: --pm-green color
  - Letter-spacing: 0.2px
- **Purpose**: Clickable wallet addresses
- **Status**: Needs Polymarket styling

### 8.5 P&L Combined Cell
**Classes**: `.pm-pnl-combined`, `.pm-pnl-main`, `.pm-pnl-sub`
- **Current**:
  - `.pm-pnl-combined`: Min-width 140px
  - `.pm-pnl-main`: 13px font
  - `.pm-pnl-sub`: 11px font, margin-top 2px
  - Color applied via inline styles
- **Status**: Needs Polymarket styling

---

## 9. MESSAGES & ALERTS

### 9.1 Inline Messages
**Class**: `.pm-inline-message`
- **Current**: Margin-top 14px, contains `.alert` element
- **Alert styling**:
  - Margin-bottom: 0
  - Border-radius: 14px
  - Border: 1px solid rgba(255, 255, 255, 0.08)
  - Background: rgba(255, 255, 255, 0.04)
  - Color: --pm-text
- **Status**: Needs Polymarket alert styling

### 9.2 Read-Only Alert
**Class**: `.pm-readonly-alert`
- **Current**: Same as inline message, color: --pm-text-secondary
- **Status**: Needs Polymarket styling

### 9.3 Database Error Layout
**Component**: `_database_error_layout()`
- **Returns**: `dbc.Alert()` with color="danger" and className="mb-0"
- **Status**: Uses dbc styling, needs Polymarket override

### 9.4 Empty States
**Classes**: `.pm-empty-state`, `.pm-empty-state__title`, `.pm-empty-state__copy`
- **Current**:
  - `.pm-empty-state`: Padding 18px 16px, border-radius 14px, dashed border (1px rgba(255, 255, 255, 0.1))
  - Background: rgba(255, 255, 255, 0.02)
  - Title: 14px, 700 weight
  - Copy: 13px, line-height 1.5, secondary color
- **Status**: Needs Polymarket styling

---

## 10. COLLAPSIBLE & DETAILS COMPONENTS

### 10.1 Tier Collapsible Section
**Classes**: `.pm-tier-collapsible`, `.pm-tier-summary`, `.pm-tier-header-inner`, `.pm-tier-label-text`, `.pm-tier-meta-inline`
- **Current**:
  - `.pm-tier-collapsible`: `<details>` element, border 1px solid var(--pm-border), border-radius 14px
  - `.pm-tier-summary`: Background gradient (surface to surface-alt), border-bottom, padding 4px 12px
  - `.pm-tier-label-text`: 14px, 600 weight, color: --pm-blue
  - `.pm-tier-meta-inline`: 12px, secondary color, JetBrains Mono font
  - Custom expand/collapse arrows (▾/▸)
- **Status**: Needs Polymarket styling

### 10.2 Generic Collapsible Section
**Classes**: `.pm-collapsible-section`, `.pm-collapsible-label`, `.pm-collapsible-header-inner`, `.pm-collapsible-meta`
- **Current**: Same structure as tier collapsible
- **Status**: Needs Polymarket styling

### 10.3 Chart Container
**Class**: `.pm-chart-shell`
- **Current**:
  - Margin-top: 18px
  - Padding: 10px 6px 0
  - Border-radius: 15.2px
  - Background: var(--pm-surface)
  - Border: 1px solid var(--pm-border)
  - Flex: 1 1 auto, min-height 0
- **Contains**: Lightweight Charts (external charting library)
- **Status**: Needs Polymarket styling

---

## 11. HISTORY & CHANGE LIST COMPONENTS

### 11.1 Recent Changes List
**Id**: `#recent-changes`
- **Container Class**: `.pm-changes-list`
- **Current**:
  - Display: flex, flex-direction column, gap 10px
  - Flex: 1 1 auto, min-height 0
  - Overflow-y: auto, padding-right 4px
- **Status**: Layout only

### 11.2 Change Row Item
**Classes**: `.pm-change-row`, `.pm-change-top`, `.pm-change-badge`, `.pm-change-meta`, `.pm-change-wallet`
- **Current**:
  - `.pm-change-row`: Padding 12px 14px, border-radius 14px, background rgba(255, 255, 255, 0.025), border 1px solid rgba(255, 255, 255, 0.05)
  - `.pm-change-top`: Flex, gap 8px, margin-bottom 8px
  - `.pm-change-wallet`: 13px, line-height 1.45, overflow-wrap anywhere
- **Status**: Needs Polymarket styling

### 11.3 History Row (Push History)
**Classes**: `.pm-history-row`, `.pm-history-row__top`, `.pm-history-row__header`, `.pm-history-row__title`, `.pm-history-row__subtitle`, `.pm-history-row__metrics`, `.pm-history-row__actions`, `.pm-history-row__meta`
- **Current**:
  - `.pm-history-row`: Flex column, gap 10px, padding 14px 16px, border-radius 14px, background rgba(255, 255, 255, 0.025), border 1px solid rgba(255, 255, 255, 0.06)
  - Title: 15px, 700 weight
  - Subtitle/Metrics: 13px, secondary color, line-height 1.45
  - Meta: 12px, 700 weight, uppercase, letter-spacing 0.04em
- **Status**: Needs Polymarket styling

### 11.4 History List Row
**Classes**: `.pm-history-list__row`, `.pm-history-list__meta`, `.pm-history-list__title`, `.pm-history-list__summary`
- **Current**: Similar to history row, gap 14px
- **Status**: Needs Polymarket styling

### 11.5 History Detail Section
**Class**: `.pm-history-detail`
- **Current**: Flex column, gap 16px
- **Contains**: Collapsible changes list
- **Status**: Layout only

### 11.6 Changes Grid
**Class**: `.pm-changes-grid`
- **Current**: 2-column grid (1.1fr | 1fr), gap 18px, align-items start
- **Responsive**: Collapses to 1 column at 1100px
- **Status**: Layout only

---

## 12. SIDEBAR & CONTROL COMPONENTS

### 12.1 Side Rail
**Class**: `.pm-side-rail`
- **Current**: Flex column, gap 18px, height 100%
- **Status**: Layout only

### 12.2 Action Row
**Classes**: `.pm-action-row`, `.pm-action-row-compact`
- **Current**:
  - `.pm-action-row`: Grid 2-column, gap 10px
  - `.pm-action-row-compact`: Flex, gap 6px, align-items center
- **Status**: Layout only

### 12.3 Breakdown Controls
**Class**: `.pm-breakdown-controls`
- **Current**: Flex, align-items center, justify-content flex-end, gap 12px, flex-wrap wrap
- **Status**: Layout only

### 12.4 Breakdown Summary
**Class**: `.pm-breakdown-summary`
- **Current**: Margin-bottom 16px
- **Status**: Layout only

### 12.5 Side Section Title
**Class**: `.pm-side-section-title`
- **Current**: Margin-top 18px, margin-bottom 12px, font-size 13px, 700 weight, letter-spacing 0.02em, flex 0 0 auto
- **Status**: Needs Polymarket styling

### 12.6 Section Side Note
**Class**: `.pm-section-side-note`
- **Current**: Color secondary, font-size 13px, 600 weight
- **Status**: Needs Polymarket styling

---

## 13. WALLET MANAGEMENT COMPONENTS

### 13.1 Wallet Admin Grid
**Class**: `.pm-wallet-admin-grid`
- **Current**: Grid, auto-fit, minmax(300px, 1fr), gap 14px
- **Status**: Layout only

### 13.2 Wallet Admin Card
**Classes**: `.pm-wallet-admin-card`, `.pm-wallet-admin-card__header`, `.pm-wallet-admin-card__wallet`, `.pm-wallet-admin-card__filter`, `.pm-wallet-admin-card__metrics`, `.pm-wallet-admin-card__actions`
- **Current**:
  - `.pm-wallet-admin-card`: Flex column, gap 14px, padding 16px, border-radius 16px, background rgba(255, 255, 255, 0.025), border 1px solid rgba(255, 255, 255, 0.06)
  - Wallet: 15px, 700 weight
  - Filter: 12px, uppercase, secondary color
  - Metrics/Actions: Flex wrap, gap 10px
- **Status**: Needs Polymarket styling

### 13.3 Wallet Summary
**Classes**: `.pm-wallet-summary`, `.pm-wallet-stat-grid`
- **Current**:
  - `.pm-wallet-summary`: Flex column, gap 14px, margin-top 18px
  - `.pm-wallet-stat-grid`: Grid 2 columns, gap 10px
- **Status**: Layout only

---

## 14. SETTINGS COMPONENTS

### 14.1 Settings Grid
**Class**: `.pm-settings-grid`
- **Current**: Grid, gap 12px
- **Status**: Layout only

### 14.2 Settings Row
**Classes**: `.pm-settings-row`, `.pm-settings-row__name`, `.pm-settings-row__count`
- **Current**:
  - Grid 3-column (minmax(180px, 1fr) | 180px | minmax(120px, 180px))
  - Gap 14px, align-items center
  - Padding 14px 0
  - Border-bottom: 1px solid rgba(255, 255, 255, 0.05)
  - Name: 15px, 700 weight
  - Count: 13px, 600 weight, secondary color
- **Status**: Needs Polymarket styling

---

## 15. COMPONENT BUILDER FUNCTIONS

### Key UI Component Functions in app.py

| Function | Purpose | Returns | CSS Classes Used |
|----------|---------|---------|------------------|
| `_card()` | Wraps content in surface card | html.Div | `.pm-surface` + custom |
| `_range_buttons()` | Range selector buttons | html.Div | `.pm-range-pill-group`, `.pm-range-pill` |
| `_money()` | Formats currency | formatted string | (inline styles) |
| `_pnl_cell()` | P&L table cell | html.Td | `.pm-tier-table__cell`, `.pm-num` |
| `_daily_breakdown_table()` | Daily wallet table | html.Table | `.pm-tier-table`, `.pm-empty-state__copy` |
| `_status_chip()` | Status indicator | html.Span | `.pm-status-chip`, `.pm-status-chip--{tone}` |
| `_stat_tile()` | Metric display | html.Div | `.pm-stat-tile`, `.pm-stat-tile__label`, `.pm-stat-tile__value` |
| `_brand()` | Header logo | html.Div | `.pm-brand`, `.pm-brand-mark`, `.pm-brand-copy`, etc. |
| `_build_recent_changes()` | Change list | html.Div | `.pm-changes-list`, `.pm-change-row`, `.pm-change-badge` |
| `_metric_chip()` | Metric badge | html.Div | `.pm-metric-chip`, `.pm-metric-chip__label`, `.pm-metric-chip__value` |
| `_pending_change_card()` | Change display | html.Div | `.pm-history-row`, `.pm-history-status--{status}` |
| `_game_badge()` | Game emoji display | html.Span | `.pm-game-badge` |
| `_sparkline_svg()` | Mini chart | SVG | (inline) |
| `_pnl_combined_cell()` | P&L with subtext | html.Td | `.pm-pnl-combined`, `.pm-pnl-main`, `.pm-pnl-sub` |
| `_sortable_th()` | Table header | html.Th | `.pm-tier-table__th` |
| `_render_wallet_row()` | Wallet table row | html.Tr | `.pm-tier-table__row`, `.pm-tier-table__cell` |
| `_tier_table()` | Tier wallet table | html.Table | `.pm-tier-table` |
| `_render_removed_section()` | Removed wallets card | html.Div | `.pm-admin-card`, `.pm-section-title` |
| `_render_management_sections()` | Tier collapsible sections | html.Div[] | `.pm-tier-collapsible`, `.pm-tier-summary` |
| `_render_push_list()` | Push history list | html.Div | `.pm-history-list`, `.pm-history-list__row` |
| `_render_push_detail()` | Push detail view | html.Div | `.pm-history-detail`, `.pm-collapsible-section` |

---

## 16. PAGE LAYOUTS

### 16.1 Overview Page Layout
**Function**: `overview_layout()`
- **Main Classes**: `.pm-page-stack`, `.pm-overview-grid`, `.pm-overview-hero`, `.pm-side-rail`, `.pm-side-card`
- **Key Elements**:
  - Portfolio overview card with chart
  - Range selector buttons
  - Controls sidebar with recent changes feed
  - Daily breakdown table
  - Date range picker
- **Status**: Entire page needs Polymarket styling

### 16.2 Wallet Page Layout
**Function**: `wallet_layout()`
- **Main Classes**: `.pm-page-stack`, `.pm-wallet-grid`, `.pm-wallet-hero`, `.pm-wallet-side`
- **Key Elements**:
  - Wallet selector dropdown
  - Per-wallet P&L chart
  - Position context summary
  - Stat tiles for metrics
- **Status**: Entire page needs Polymarket styling

### 16.3 Wallet Management Page Layout
**Function**: `wallet_management_layout()`
- **Main Classes**: `.pm-page-stack`, `.pm-admin-card`
- **Key Elements**:
  - Action buttons (Add, Export, Push)
  - Wallet management sections with tier collapsibles
  - Modal dialogs (Add Wallet, Push Preview, Revert)
  - dbc.Modal components
- **Modals**:
  - `add-wallet-modal`: dbc.Modal with textarea + dropdown
  - `push-preview-modal`: dbc.Modal with change preview
  - `revert-preview-modal`: dbc.Modal for revert confirmation
- **Status**: Entire page + modals need Polymarket styling

---

## 17. DBC (DASH BOOTSTRAP) COMPONENTS

### Components Used
- `dbc.Alert()`: Error/success messages, read-only alerts
- `dbc.Modal()`: Dialog boxes
- `dbc.ModalHeader()`: Modal title
- `dbc.ModalTitle()`: Modal title text
- `dbc.ModalBody()`: Modal content area
- `dbc.ModalFooter()`: Modal action buttons
- `dbc.themes.DARKLY`: Base Bootstrap theme (dark)

### Status
**All dbc components need Polymarket theme overrides** via CSS. The current DARKLY theme is used as a base, but requires custom styling to match Polymarket design.

---

## 18. DCC (DASH CORE COMPONENTS)

### Components Used
- `dcc.Input()`: Text/date inputs
- `dcc.Textarea()`: Multi-line text input
- `dcc.Dropdown()`: Select dropdown
- `dcc.Graph()`: Chart visualization
- `dcc.Loading()`: Loading spinner
- `dcc.Store()`: Client-side data storage
- `dcc.Interval()`: Periodic polling
- `dcc.Download()`: File download trigger
- `dcc.Location()`: URL routing
- `dcc.Tabs()`: Tab navigation
- `dcc.Tab()`: Individual tab

### Status
**All dcc components need custom CSS overrides** to match Polymarket styling. Currently using extensive custom classes (`.pm-date-input`, `.pm-textarea`, `.pm-wallet-dropdown`, etc.)

---

## 19. RESPONSIVE DESIGN

### Breakpoints
- **1100px**: Large to medium - grid layouts collapse to 1 column
- **720px**: Medium to small - mobile layout, card headers stack

### Responsive Adjustments
- Topbar changes from 3-column to 1-column layout at 1100px
- Status bar moves from right to left alignment
- Overview/wallet grids collapse to single column
- Metric values reduce from 56px to 42px at 720px
- Action rows change from 2-column to 1-column grid at 720px
- Settings rows change to single-column layout at 720px

### Status
Responsive breakpoints are layout-only and don't require Polymarket changes.

---

## 20. POLYMARKET MAPPING RECOMMENDATIONS

### Colors to Update
| Current | Variable | Polymarket Equivalent | Action |
|---------|----------|----------------------|--------|
| #15191d | --pm-bg | Polymarket background | Update to Polymarket dark |
| #181d21 | --pm-surface | Polymarket card background | Update to Polymarket surface |
| #0093fd | --pm-blue | Polymarket primary | Update to Polymarket blue |
| #3db468 | --pm-green | Polymarket success | Update to Polymarket green |
| #ff4d4d | --pm-red | Polymarket danger | Update to Polymarket red |
| #dee3e7 | --pm-text | Polymarket text | Update to Polymarket text |
| #7b8996 | --pm-text-secondary | Polymarket text secondary | Update to Polymarket secondary |

### Component Styling Priorities
1. **HIGH**: Top navigation bar, buttons, badges, alerts
2. **HIGH**: Tables and data cells
3. **HIGH**: Form inputs and dropdowns
4. **MEDIUM**: Cards and surfaces
5. **MEDIUM**: Typography scales
6. **MEDIUM**: Modals and dialogs
7. **LOW**: Responsive layout (structure OK, just needs color updates)

---

## 21. SUMMARY OF CHANGES NEEDED

### Total CSS Classes: 150+ custom Polymarket classes

### Categories Requiring Updates
- **Layout**: 20 classes (mostly structure OK, some gradient updates)
- **Typography**: 15 classes (sizing OK, color updates needed)
- **Buttons**: 8 classes (major styling needed)
- **Inputs/Forms**: 10 classes (major styling needed)
- **Badges/Status**: 12 classes (color updates needed)
- **Tables**: 8 classes (styling updates needed)
- **Cards/Surfaces**: 15 classes (styling updates needed)
- **Messages/Alerts**: 8 classes (styling updates needed)
- **Collapsibles**: 8 classes (styling updates needed)
- **History/Lists**: 12 classes (styling updates needed)
- **Modals (dbc overrides)**: 30+ classes (major styling needed)
- **Dropdowns/Selects**: 40+ classes (major styling needed)

### Component Builder Functions Requiring Updates
- All 20+ `_*()` functions that return HTML
- Page layout functions (4 main layouts)
- All return statements that generate HTML with hardcoded pm- classes

---

## END OF AUDIT

This audit provides a complete inventory of every UI element in the Wallet Curator Dashboard. Each component is documented with:
- Current CSS classes and styling
- Current appearance and behavior
- Purpose and usage
- Status relative to Polymarket redesign

Next steps:
1. Create Polymarket color variable mappings
2. Create Polymarket CSS class definitions
3. Update CSS file with Polymarket styling
4. Test all pages and interactions
5. Update component builder functions as needed (may not need changes if only CSS is updated)
