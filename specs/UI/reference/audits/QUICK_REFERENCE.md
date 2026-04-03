# Wallet Curator Dashboard - UI Audit Quick Reference

## Files Generated
1. **UI_AUDIT.md** (29 KB, 807 lines) - Complete detailed audit
2. **COMPONENT_INVENTORY.txt** (12 KB, 452 lines) - Summary with statistics
3. **QUICK_REFERENCE.md** (this file) - Quick lookup guide

## Quick Stats
- **CSS Classes**: 150+ custom components documented
- **CSS File**: 1,569 lines in `assets/dashboard-ui.css`
- **Python File**: 3,217 lines in `app.py`
- **Component Functions**: 20+ builder functions identified
- **Page Layouts**: 4-5 main page layouts
- **Modals**: 3 dialog components

## Color Variables to Update (13 total)
```css
--pm-bg                    /* Background - needs Polymarket color */
--pm-surface               /* Card surface - needs Polymarket color */
--pm-surface-alt           /* Alternative surface - needs update */
--pm-surface-soft          /* Soft surface - needs update */
--pm-border                /* Border color - needs update */
--pm-border-soft           /* Soft border - needs update */
--pm-text                  /* Main text - needs update */
--pm-text-secondary        /* Secondary text - needs update */
--pm-blue                  /* Primary accent - needs Polymarket blue */
--pm-green                 /* Success color - needs Polymarket green */
--pm-red                   /* Danger color - needs Polymarket red */
--pm-purple                /* Secondary accent - can remove or update */
--pm-shadow                /* Drop shadow - may need Polymarket adjustment */
```

## Critical Components (High Priority)
| Component | Type | Classes | File | Notes |
|-----------|------|---------|------|-------|
| Buttons | Controls | 8 variants | CSS | Complete restyle needed |
| Dropdowns | Input | 40+ selectors | CSS | Extensive Dash overrides |
| Form Inputs | Input | 4 main types | CSS | Date, textarea, settings input |
| Status Badges | Display | 7 types | CSS | Color updates needed |
| Tables | Display | 8 classes | CSS | Header, cell, row styling |
| Modals | Containers | dbc overrides | CSS | Need Polymarket theme |
| Top Nav | Layout | 8 classes | CSS | Brand, tabs, status chips |

## Component Hierarchy (Quick View)
```
.pm-app-shell
├── .pm-topbar (header with brand, title, status)
├── .pm-tab-rail (navigation tabs)
└── .pm-main-shell
    └── .pm-page-stack (content area)
        ├── .pm-overview-grid
        ├── .pm-wallet-grid
        ├── .pm-changes-grid
        └── .pm-surface / cards
            ├── .pm-stat-tile
            ├── .pm-metric-chip
            ├── .pm-tier-table
            └── [content]
```

## High Priority Changes
**BUTTONS** (8 variants)
- `.pm-button` (base)
- `.pm-button--primary` (gradient blue→purple)
- `.pm-button--secondary` (transparent)
- `.pm-button--danger` (red)
- `.pm-button--inline` (compact)
- `.pm-button--remove` (red)
- `.pm-button--promote` (green)
- `.pm-button--demote` (orange)

**FORM INPUTS** (4+ types)
- `.pm-date-input` (130px width)
- `.pm-textarea` (full width)
- `.pm-settings-input` (100px number input)
- `.pm-wallet-dropdown` (select with 40+ overrides)

**BADGES/CHIPS** (12 types)
- `.pm-status-chip` and variants (success, info, danger)
- `.pm-change-badge--positive/negative`
- `.pm-history-status--pending/applied/reverted`
- `.pm-metric-chip` and variants (positive, negative)
- `.pm-summary-strip` span pills
- `.pm-settings-row__count-pill`

**TABLES** (8 classes)
- `.pm-tier-table` (main table)
- `.pm-tier-table__th` (headers)
- `.pm-tier-table__row` (rows)
- `.pm-tier-table__cell` (cells)
- `.pm-wallet-copyable` (clickable addresses)
- `.pm-pnl-combined` (P&L with subtext)
- `.pm-daily-table-shell` (container)
- `.pm-num` (numeric alignment)

## Medium Priority Changes
**CARDS/SURFACES** (15 classes)
- `.pm-surface` (base card)
- `.pm-overview-hero`
- `.pm-wallet-hero`
- `.pm-side-card`
- `.pm-wallet-side`
- `.pm-breakdown-card`
- `.pm-admin-card`
- `.pm-chart-shell` (chart container)
- etc.

**MESSAGES/ALERTS** (8 classes)
- `.pm-inline-message` (message container)
- `.pm-readonly-alert` (read-only warning)
- `.pm-empty-state` (no data state)
- `.pm-empty-state__title`
- `.pm-empty-state__copy`

**COLLAPSIBLES** (8 classes)
- `.pm-tier-collapsible` (details element)
- `.pm-tier-summary` (summary element)
- `.pm-collapsible-section` (generic)
- `.pm-collapsible-label`
- `.pm-collapsible-header-inner`
- `.pm-collapsible-meta`
- `.pm-tier-label-text`
- `.pm-tier-meta-inline`

## Low Priority (Layout Only - No Changes Needed)
- Grid layouts (.pm-overview-grid, .pm-wallet-grid, etc.)
- Flex containers
- Spacing/padding
- Responsive breakpoints
- Column widths
- Z-index stacking

## Implementation Roadmap

### Phase 1: Setup (5 minutes)
1. Create Polymarket color variable mappings
2. Create CSS variable replacement list
3. Back up original dashboard-ui.css

### Phase 2: Colors (30 minutes)
1. Update all 13 root CSS variables
2. Do global find-replace for color values
3. Test on all pages for color consistency

### Phase 3: Components (2-3 hours)
1. Update button styles (all 8 variants)
2. Update form inputs (4 types + dropdown overrides)
3. Update badges/chips (7-12 types)
4. Update table styling
5. Update modal/dbc components
6. Update cards/surfaces

### Phase 4: Polish (1-2 hours)
1. Update typography colors
2. Update messages/alerts
3. Update collapsibles
4. Test all pages

### Phase 5: Testing (1-2 hours)
1. Visual verification on each page
2. Responsive design testing (1100px, 720px)
3. Component interaction testing
4. Cross-browser compatibility check

## Pages to Test
1. **Overview Page** (overview_layout)
   - Portfolio overview chart
   - Recent changes feed
   - Daily breakdown table
   - Range selector buttons

2. **Wallet Page** (wallet_layout)
   - Wallet dropdown
   - Per-wallet chart
   - Position context summary
   - Stat tiles

3. **Wallet Management** (wallet_management_layout)
   - Wallet tiles
   - Tier collapsible sections
   - Modals (Add, Push, Revert)
   - Action buttons

4. **Subcategory Charts** (subcategory_charts_layout)
   - Category selector
   - Chart display
   - Concentration badges
   - Top markets table

5. **Wallet Curation** (wallet_curation_layout)
   - Wallet navigation
   - Curation signals
   - Decision interface
   - Status display

## CSS File Locations
- **Main CSS**: `/assets/dashboard-ui.css` (1,569 lines)
- **Icon/Assets**: `/assets/` directory
- **Images**: `/assets/` directory

## Python Entry Points
- **Main App**: `/app.py` line 1
- **Layout Functions**: Lines 762-2700 (approx)
- **Callback Handlers**: Lines 1500-2600 (approx)
- **Utility Functions**: Lines 73-760 (approx)

## CSS Class Naming Convention
- `.pm-` prefix for all custom classes
- `--pm-` prefix for CSS variables
- BEM methodology: `.pm-component__child--modifier`
- Example: `.pm-stat-tile__value--positive`

## Key Responsive Breakpoints
- **1100px**: Large → Medium (grids collapse)
- **720px**: Medium → Small (stacking layout)

## Browser Compatibility
- Modern browsers (Chrome, Firefox, Safari, Edge)
- Dark mode optimized
- No IE11 support needed (Dash requirement)

## Dependencies
- Dash (Python framework)
- Dash Bootstrap Components (dbc)
- Dash Core Components (dcc)
- Lightweight Charts (charting library)
- Inter font
- JetBrains Mono font (monospace)

## What NOT to Change
- HTML structure in app.py
- Component class names (they're used in Python callbacks)
- JavaScript functionality
- Responsive layout breakpoints
- Font families (unless Polymarket requires different)
- Z-index stacking order

## Next Actions
1. Define Polymarket color palette
2. Create CSS variable mapping spreadsheet
3. Update dashboard-ui.css
4. Test all pages
5. Deploy

---

**For detailed information**, see `UI_AUDIT.md` (full 20+ page audit with all 150+ classes documented)

**For statistics and overview**, see `COMPONENT_INVENTORY.txt` (summary with component counts, hierarchy, and breakdown)
