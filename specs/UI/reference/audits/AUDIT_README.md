# Wallet Curator Dashboard - UI Audit Report

## Audit Completed: April 3, 2026

This comprehensive audit documents **every UI element** in the Wallet Curator Dashboard, a Dash (Python) application with custom CSS styling. The goal is to provide a complete inventory for restyling to match Polymarket design standards.

## Deliverables

### 1. UI_AUDIT.md (32 KB - MAIN REFERENCE)
The complete, detailed audit of all UI components.

**Contents:**
- CSS color tokens and design variables
- 20 layout components documented
- 15 card/surface component variants
- 15 typography components
- 8 button component variants
- 10 input & form components
- 12 badge & status component types
- 8 table components
- 8 message & alert components
- 8 collapsible & details components
- 12 history & change list components
- 12 wallet management components
- 6 settings components
- 20+ component builder functions
- 4-5 page layout functions
- Complete DBC and DCC component inventory
- Responsive design details
- Polymarket mapping recommendations
- Summary of changes needed

**How to use:** Start here for comprehensive details on every component.

### 2. COMPONENT_INVENTORY.txt (24 KB - VISUAL SUMMARY)
A formatted, easy-to-scan summary with statistics, ASCII diagrams, and component hierarchies.

**Contents:**
- Color token definitions
- Component breakdown by category with counts
- Key component functions list
- Modal dialogs documented
- DCC components documented
- Component hierarchy tree (visual)
- Table structure details
- Responsive breakpoint information
- Button variant breakdown
- Form input styling details
- Badge & chip component breakdown
- Empty states & messages structure
- Collapsible & details structure
- History & change list structure
- Sidebar & control components
- Settings components breakdown
- Statistics (file sizes, component counts)
- Implementation approach phases

**How to use:** Read this for a quick overview and visual reference.

### 3. QUICK_REFERENCE.md (8 KB - QUICK LOOKUP)
A condensed reference guide for quick lookups during implementation.

**Contents:**
- File overview and quick stats
- Color variables summary (13 variables)
- Critical components table (High Priority items)
- Component hierarchy (quick view)
- High priority changes organized by type
- Medium priority changes
- Low priority changes (no changes needed)
- Implementation roadmap (5 phases, 4-7 hours total)
- Pages to test (5 main pages)
- CSS file locations
- Python entry points
- CSS class naming convention
- Responsive breakpoints
- Browser compatibility
- Dependencies
- What NOT to change
- Next actions checklist

**How to use:** Use this during implementation as a quick reference guide.

### 4. AUDIT_README.md (this file)
Overview of the audit and how to use the deliverables.

## Key Findings

### Total Components Documented
- **150+ CSS classes** catalogued with full styling details
- **20+ component builder functions** in Python
- **13 CSS root variables** for theming
- **3 modal dialogs** (dbc.Modal components)
- **10+ DCC components** used
- **6 dbc component types** used

### File Statistics
| File | Size | Lines | Components |
|------|------|-------|------------|
| assets/dashboard-ui.css | 1,569 lines | CSS classes: 150+ |
| app.py | 3,217 lines | Functions: 20+, Layouts: 4-5 |

### What Needs Polymarket Styling

**HIGH PRIORITY** (3-4 hours of work):
- 8 button variants
- 40+ dropdown CSS overrides
- 4 form input types
- 7-12 status badge types
- 8 table-related styles
- Top navigation bar styling
- Modal/dbc component overrides

**MEDIUM PRIORITY** (1-2 hours):
- 15 card/surface variants
- 8 message/alert styles
- 8 collapsible styles
- 12 history/list item styles
- 15 typography colors

**LOW PRIORITY** (no changes needed):
- Layout structure (grids, flex, spacing)
- Responsive breakpoints
- Z-index stacking
- Column widths

### Implementation Time Estimate
- **Phase 1 (Setup)**: 5 minutes
- **Phase 2 (Colors)**: 30 minutes
- **Phase 3 (Components)**: 2-3 hours
- **Phase 4 (Polish)**: 1-2 hours
- **Phase 5 (Testing)**: 1-2 hours
- **TOTAL**: 4-7 hours of work

## How to Use These Audit Documents

### For Getting Started
1. Read this file (AUDIT_README.md) - 5 minutes
2. Read QUICK_REFERENCE.md - 10 minutes
3. Skim COMPONENT_INVENTORY.txt - 10 minutes
4. Total: ~25 minutes to understand the scope

### For Planning the Redesign
1. Use QUICK_REFERENCE.md > "Implementation Roadmap" section
2. Cross-reference with COMPONENT_INVENTORY.txt for component counts
3. Check UI_AUDIT.md section 20 for Polymarket mapping recommendations

### For Implementation
1. Have QUICK_REFERENCE.md open as quick lookup
2. Refer to UI_AUDIT.md for detailed styling of each component
3. Use COMPONENT_INVENTORY.txt for component hierarchy and structure

### For Quality Assurance
1. Use "Pages to Test" checklist from QUICK_REFERENCE.md
2. Verify responsive design with breakpoint info from COMPONENT_INVENTORY.txt
3. Check component interactions with details from UI_AUDIT.md

## Component Categories

### Layout (20 components)
- Application shell
- Top navigation
- Tab navigation
- Main columns and containers
- Grid systems (overview, wallet, changes, settings)

### Cards & Surfaces (15 components)
- Base surface card
- Overview hero
- Wallet hero
- Side cards
- Admin cards
- Chart containers

### Typography (15 components)
- Section titles (main, side, kickers)
- Labels and field labels
- Metric values
- Secondary text

### Buttons (8 variants)
- Primary button
- Secondary button
- Danger button
- Inline button (compact)
- Range pill selector
- Button variants (remove, promote, demote)

### Form Inputs (10 types)
- Date input
- Textarea
- Number/settings input
- Dropdown (with 40+ Dash-specific overrides)
- Date range container

### Badges & Chips (12 types)
- Status chips (success, info, danger)
- Change badges
- History status badges
- Metric chips
- Summary pills

### Tables (8 components)
- Tier table
- Tier table headers
- Tier table rows
- Tier table cells
- Wallet copyable addresses
- P&L combined cells
- Daily breakdown table container

### Messages & Alerts (8 components)
- Inline messages
- Read-only alerts
- Empty states
- Database error layout

### Collapsibles (8 components)
- Tier collapsible section
- Generic collapsible section
- Summary elements
- Header internals
- Label and meta elements

### History & Changes (12 components)
- Change row
- Change badges
- History row
- History list
- History detail section
- Changes grid

## Pages to Review

The audit covers 5 main page layouts:

1. **Overview Page** (overview_layout)
   - Portfolio overview with chart
   - Recent changes feed
   - Daily breakdown table

2. **Wallet Page** (wallet_layout)
   - Wallet selector dropdown
   - Per-wallet P&L chart
   - Position context summary

3. **Wallet Management** (wallet_management_layout)
   - Wallet admin cards
   - Tier collapsible sections
   - 3 modal dialogs

4. **Subcategory Charts** (subcategory_charts_layout)
   - Category selector
   - Generated charts
   - Concentration badges
   - Top markets table

5. **Wallet Curation** (wallet_curation_layout)
   - Wallet navigation
   - Curation signals
   - Decision interface

## CSS Class Naming Convention

All custom CSS classes follow this convention:

```
.pm-COMPONENT
.pm-COMPONENT__CHILD
.pm-COMPONENT--MODIFIER
.pm-COMPONENT__CHILD--MODIFIER

Examples:
.pm-stat-tile              (base component)
.pm-stat-tile__value       (child element)
.pm-stat-tile--positive    (modifier variant)
.pm-stat-tile__value--positive  (combined)
```

CSS variables follow:
```
--pm-PROPERTY: value;

Examples:
--pm-bg: #15191d;
--pm-blue: #0093fd;
```

## What NOT to Change

Do NOT modify:
- ✗ HTML structure in app.py (class names are used in callbacks)
- ✗ Component class names (they're referenced in Python)
- ✗ JavaScript functionality
- ✗ Responsive layout structure (adjust media queries only for styling)
- ✗ Font families (unless Polymarket specifies different)
- ✗ Z-index stacking order
- ✗ Grid and flex layouts (structure is correct, only colors need updating)

## Dependencies

The application uses:
- **Framework**: Dash (Python web framework)
- **UI Components**: Dash Bootstrap Components (dbc), Dash Core Components (dcc)
- **Charting**: Lightweight Charts (external library)
- **Fonts**: Inter (sans-serif), JetBrains Mono (monospace)
- **CSS**: Custom dashboard-ui.css (1,569 lines)

## Browser Support

Tested/Supported:
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

Dark mode optimized, no IE11 support needed.

## Next Steps

1. **Define Polymarket Design System**
   - Get Polymarket color palette
   - Get Polymarket button specifications
   - Get Polymarket form/input specifications
   - Get Polymarket card/surface specifications

2. **Create CSS Variable Mappings**
   - Map current 13 CSS variables to Polymarket colors
   - Create replacement list

3. **Implement CSS Changes**
   - Back up original dashboard-ui.css
   - Update CSS root variables
   - Update all 150+ component classes
   - Update dbc/dropdown overrides

4. **Test Thoroughly**
   - Visual verification on all 5 pages
   - Responsive design testing (1100px, 720px breakpoints)
   - Component interaction testing
   - Cross-browser testing

5. **Deploy**
   - Version control commit
   - Deploy to staging
   - QA verification
   - Deploy to production

## Document References

- **UI_AUDIT.md**: Full detailed audit (20+ pages)
- **COMPONENT_INVENTORY.txt**: Visual summary with ASCII diagrams
- **QUICK_REFERENCE.md**: Quick lookup guide for implementation
- **AUDIT_README.md**: This document

## Questions or Issues

When reviewing the audit:
- **Specific component details**: See UI_AUDIT.md section on that component type
- **Component counts and statistics**: See COMPONENT_INVENTORY.txt
- **Quick lookup during work**: See QUICK_REFERENCE.md
- **Implementation roadmap**: See QUICK_REFERENCE.md > Implementation Roadmap

## Audit Metadata

- **Generated**: April 3, 2026
- **Project**: Wallet Curator Dashboard
- **Files Audited**: 
  - `/app.py` (3,217 lines)
  - `/assets/dashboard-ui.css` (1,569 lines)
- **Components Documented**: 150+
- **Total Deliverable Size**: 64 KB
- **Audit Type**: Complete UI Inventory with Polymarket Mapping

---

**Start with QUICK_REFERENCE.md for a 25-minute overview, then dive into UI_AUDIT.md for complete details.**

**For visual reference and component hierarchy, see COMPONENT_INVENTORY.txt.**

Happy redesigning!
