# UI Consistency Port — Match All Pages to Wallet Management Design

## Objective
Port the refined design language from the wallet-management page to the remaining four pages (Portfolio Overview, Per-Wallet Charts, Changes, Settings) so the entire dashboard has a consistent, premium look. Then commit all work to the `ui-redesign` branch.

## Pre-Requisite: Commit Current State

- [ ] **Step 0a.** Ensure you're on the `ui-redesign` branch (`git checkout ui-redesign` or create it if needed)
- [ ] **Step 0b.** Stage and commit all current working changes with message: `feat: wallet-management UI redesign — tier tables, collapsible sections, rich cells`

---

## Implementation Plan

### Page 1: Portfolio Overview (`overview_layout` — `app.py:650-833`)

- [ ] **1.1 Add blue accent to section title.** Apply `pm-section-title--blue` class to the "Overview" H2 at `app.py:668` to match wallet-management's header treatment at `app.py:905`.

- [ ] **1.2 Replace Dash DataTable with custom HTML table.** The daily breakdown table (`app.py:774-824`) uses `dash_table.DataTable` with verbose inline style dicts. Replace it with a custom HTML `<table>` using `.pm-tier-table` classes, matching the pattern from `_tier_table()` at `app.py:480-506`. This means:
  - Create a `_daily_breakdown_table(rows)` helper that returns `html.Table` with `.pm-tier-table`
  - Use `_sortable_th()` for headers (already exists at `app.py:442-446`)
  - Use `.pm-tier-table__cell` and `.pm-num` for cells
  - Add a `html.Colgroup` for fixed column widths
  - Remove all `style_header`, `style_cell`, `style_cell_conditional`, `style_data_conditional` inline dicts
  - Color P&L values using inline style like `_pnl_combined_cell` does (`app.py:413-439`)

- [ ] **1.3 Wrap daily breakdown in a collapsible section.** Use the `<details>/<summary>` pattern with `.pm-tier-collapsible` and `.pm-tier-summary` (same pattern as `app.py:557-566`) so the daily table can be collapsed, matching the tier sections.

- [ ] **1.4 Add summary strip to overview hero.** Add a `.pm-summary-strip` beneath the chart showing key aggregate stats (total wallets, total P&L, date range) — same pattern as the pending changes strip at `app.py:1587`.

- [ ] **1.5 Make wallet addresses copyable in daily table.** Wrap wallet address cells with `.pm-wallet-copyable` and `data-clipboard` attribute (pattern at `app.py:464-465`) so `wallet-copy.js` activates.

### Page 2: Per-Wallet Charts (`wallet_layout` — `app.py:836-891`)

- [ ] **2.1 Add blue accent to section title.** Apply `pm-section-title--blue` to "Per-Wallet Performance" H2 at `app.py:850`.

- [ ] **2.2 Upgrade stat tiles to use summary strip.** The wallet stats block (rendered at `app.py:2090-2115`) uses `.pm-wallet-stat-grid` with `.pm-stat-tile`. Add a `.pm-summary-strip` below the stat grid showing first/last trade dates as pill chips instead of the plain `.pm-wallet-meta-strip` div at `app.py:2107-2112`.

- [ ] **2.3 Add game badge to wallet dropdown options.** When loading wallet options, prefix the dropdown labels with a game badge using the `_game_badge()` helper (`app.py:349-358`) or embed the game emoji in the dropdown label text for visual consistency.

- [ ] **2.4 Make the wallet address in the dropdown copyable.** Add a small copy button or make the selected wallet address clickable-to-copy, reusing the `.pm-wallet-copyable` pattern.

### Page 3: Changes (`changes_layout` — `app.py:1085-1130`)

- [ ] **3.1 Add blue accent to section title.** Apply `pm-section-title--blue` to "CSV Change History" H2 at `app.py:1098`.

- [ ] **3.2 Upgrade push list rows to use richer layout.** The push list rows (`_render_push_list` at `app.py:577-608`) already use `.pm-history-list__row` which is styled well. Add a `.pm-summary-strip` to each row showing the change count and status as pill chips for visual density.

- [ ] **3.3 Add collapsible detail view.** Wrap the push detail section (`_render_push_detail` at `app.py:611-647`) in a `.pm-tier-collapsible` details/summary element so individual change entries can be collapsed.

- [ ] **3.4 Color-code revert button.** The "Revert" button at `app.py:636` uses `pm-button--secondary`. Change to `pm-button--danger` class to match the destructive action pattern used in wallet-management (e.g., remove buttons).

### Page 4: Settings (`settings_layout` — `app.py:1013-1082`)

- [ ] **4.1 Add blue accent to section title.** Apply `pm-section-title--blue` to "Tier Settings" H2 at `app.py:1024`.

- [ ] **4.2 Wrap settings grid in collapsible section.** Wrap the `.pm-settings-grid` at `app.py:1046-1071` in a `<details>` with `.pm-tier-collapsible` and a summary showing "3 tiers configured".

- [ ] **4.3 Add wallet count pills to settings rows.** Replace the plain text "X wallets" spans (`.pm-settings-row__count` at `app.py:1051, 1059, 1067`) with `.pm-summary-strip` pill-style badges for visual consistency.

- [ ] **4.4 Upgrade copy percentage inputs.** The `dcc.Input` fields use `.pm-date-input` class which was designed for date pickers. Add a new CSS class `.pm-settings-input` that has the same dark surface styling but with proper numeric input width and alignment. Add it in `dashboard-ui.css`.

### CSS Additions (`assets/dashboard-ui.css`)

- [ ] **5.1 Add `.pm-settings-input` class.** Similar to `.pm-date-input` but with `width: 100px`, `text-align: right`, and `font-variant-numeric: tabular-nums`.

- [ ] **5.2 Add daily-table HTML table overrides.** When the DataTable is replaced with a custom HTML table, add column width definitions using `colgroup` classes (like `.col-wallet`, `.col-pnl`, etc. already defined at `dashboard-ui.css:1188-1194`). Add new column classes for the daily breakdown columns: `.col-hide`, `.col-filter`, `.col-actual`, `.col-sim`, `.col-invested`, `.col-realized`, `.col-unrealized`, `.col-total-pnl`, `.col-markets-daily`, `.col-trades-daily`, `.col-in-csv`.

- [ ] **5.3 Ensure collapsible pattern works for non-tier contexts.** The existing `.pm-tier-collapsible` name implies tier-specific use. Add a generic alias `.pm-collapsible-section` that applies the same styles, so usage on Settings/Changes/Overview feels semantically correct.

### JS: No Changes Needed

The existing `tier-table-sort.js` and `wallet-copy.js` are event-delegated (they use `document.addEventListener("click", ...)`) so they will automatically work on any new HTML tables added to other pages. No JS changes required.

### Final Commit

- [ ] **6.1 Stage all modified files** (`app.py`, `assets/dashboard-ui.css`)
- [ ] **6.2 Commit with message:** `feat: port wallet-management UI patterns to all pages — consistent tables, collapsible sections, blue accents`
- [ ] **6.3 Push the branch** (`git push origin ui-redesign`)

---

## Verification Criteria

- All five tab pages use the `.pm-section-title--blue` accent on their primary heading
- The daily breakdown table renders as custom HTML (`<table class="pm-tier-table">`) not Dash DataTable
- Column sorting works on the new daily breakdown table (via `tier-table-sort.js`)
- Click-to-copy works on wallet addresses across all pages
- Collapsible sections open/close smoothly on Overview, Settings, and Changes pages
- Settings tier rows show pill-style wallet counts
- No regressions in wallet-management page functionality
- All callbacks still function (replacing DataTable means updating the `update_daily_table` callback at `app.py:1486` to return HTML children instead of DataTable `data`/`style_data_conditional`)

## Potential Risks and Mitigations

1. **DataTable → HTML table breaks the `update_daily_table` callback**
   The callback at `app.py:1486` currently returns `data` and `style_data_conditional` to a `dash_table.DataTable`. Replacing with HTML means the callback must now return `html.Div(children=...)` to a plain container. This is the highest-risk change.
   *Mitigation:* Replace `dash_table.DataTable` with an `html.Div(id="daily-table-container")` and have the callback return the full HTML table as children. Remove the DataTable-specific outputs (`style_data_conditional`). Update all callback `Output` signatures accordingly.

2. **Collapsible sections may conflict with Dash re-renders**
   Dash re-renders wipe DOM state. If a `<details>` element is inside a callback-controlled container, its open/closed state resets on every data refresh.
   *Mitigation:* Use `dcc.Store` to persist open/closed state, or accept that sections reset to open on refresh (which is the wallet-management page's current behavior — all tiers default to `open=True` at `app.py:566`).

3. **Daily table sort may need callback adjustment**
   Native `sort_action="native"` on DataTable goes away. The replacement `tier-table-sort.js` handles this client-side but requires `data-sortable` and `data-sort-type` attributes on `<th>` elements.
   *Mitigation:* Use `_sortable_th()` helper (already exists) for all daily table headers.

## Alternative Approaches

1. **Minimal approach — blue accents + summary strips only (no DataTable replacement):** Skip the DataTable→HTML migration (Steps 1.2, 1.3, 1.5) and just add the cosmetic consistency (blue titles, summary strips, collapsible settings). Lower risk, 70% of the visual consistency gain, keeps DataTable callbacks intact.

2. **Full parity — also add sparklines and game badges to daily table:** Go beyond consistency by adding `_sparkline_svg()` columns and `_game_badge()` cells to the daily breakdown, making it as rich as the tier tables. Higher effort but maximum polish.
