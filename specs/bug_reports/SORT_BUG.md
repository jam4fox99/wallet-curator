# DataTable Sorting Bug — Needs Fix

## The Problem

The Dash DataTable in `app.py` doesn't sort correctly when clicking column headers. It loads sorted by Total P&L (descending) on startup, but clicking any other column header to re-sort either doesn't work or briefly sorts then snaps back.

## Stack

- **Dash 4.0.0** (`pip install dash==4.0.0`)
- **dash-bootstrap-components 2.0.4** (DARKLY theme)
- Python 3.11
- DataTable populated via callback (data + columns set dynamically)

## What We've Tried

### Attempt 1: `sort_action='native'` with `type='numeric'`
**File:** `app.py` line ~148, callback at line ~219

Set `sort_action='native'` on the DataTable and `type='numeric'` on numeric column definitions. Result: sorting was lexicographic (string sort), not numeric. "-353" sorted before "-5" because "3" < "5" as strings.

### Attempt 2: `pd.to_numeric()` on DataFrame columns
**File:** `app.py` line ~242

Forced all numeric columns through `pd.to_numeric(df[col], errors='coerce')` before converting to records. Verified the data reaching the DataTable is proper Python `float`/`int` (confirmed via JSON serialization test). Result: still sorting as strings.

### Attempt 3: `sort_action='custom'` (server-side sort)
**File:** `app.py` line ~148, sort logic at line ~316

Switched to `sort_action='custom'` — the DataTable sends `sort_by` to the callback, Python sorts the DataFrame with `df.sort_values()`, returns sorted records. The initial sort (Total P&L desc on load) works. But clicking other columns to re-sort doesn't stick.

### Attempt 4: `no_update` for columns on sort trigger
**File:** `app.py` line ~333

Discovered that the callback returns `Output('pnl-table', 'columns')` on every trigger. When DataTable receives new columns, it resets `sort_by` back to the initial value, causing a circular reset. Fix: used `dash.ctx.triggered_id` to detect sort triggers and return `no_update` for columns/styles when sort_by changed. Result: still not fully working.

## Architecture of the Table

### How data flows:

1. `lib/snapshots.py` → `get_combined_dataframe(conn, snapshot_ids)` returns a pandas DataFrame with clean column IDs (no spaces):
   - `wallet`, `snaps`, `filter`, `actual`, `sim`, `invested`, `realized`, `open_val`, `open_pnl`, `total_pnl`, `markets`, `trades`, `in_csv`, `excluded`, `hide`

2. `app.py` callback `update_table()` receives the DataFrame, forces numeric types, sorts via `df.sort_values()`, converts to `df.to_dict('records')`, returns to DataTable.

3. Column definitions are built in the callback with explicit `type='numeric'`:
   ```python
   SINGLE_COLUMNS = [
       {'name': 'Invested', 'id': 'invested', 'type': 'numeric'},
       {'name': 'Realized', 'id': 'realized', 'type': 'numeric'},
       {'name': 'Total P&L', 'id': 'total_pnl', 'type': 'numeric'},
       ...
   ]
   ```

4. The DataTable is created inside `pnl_tab_layout()` which is called by a tab-switching callback. This means the DataTable component is RECREATED every time the user switches tabs.

### The callback:
```python
@callback(
    [Output('pnl-table', 'data'),
     Output('pnl-table', 'columns'),
     Output('pnl-table', 'style_data_conditional'),
     Output('summary-bar', 'children'),
     Output('footnotes', 'children'),
     Output('btn-hidden', 'children')],
    [Input('snapshot-select', 'value'),
     Input('hidden-visible', 'data'),
     Input('refresh-trigger', 'data'),
     Input('pnl-table', 'sort_by')],
)
def update_table(snapshot_ids, show_hidden, _, sort_by):
```

Multiple Inputs trigger this callback: snapshot dropdown, hidden toggle, refresh trigger, AND sort_by. Every trigger re-runs the full callback.

## Suspected Root Causes

1. **DataTable recreation on tab switch** — the component is inside `pnl_tab_layout()` which returns a new component each time. This might not preserve sort state.

2. **Columns output resetting sort_by** — even with the `no_update` fix, there may be a Dash 4.0 issue where updating `data` also resets sort state.

3. **Multiple callback triggers** — the callback has 4 Inputs. Changing sort_by triggers it, but the other Inputs haven't changed. The interplay between these might cause unexpected behavior.

4. **Possible Dash 4.0 regression** — `sort_action='custom'` behavior may have changed from Dash 2.x to 4.0.

## How to Reproduce

```bash
cd wallet-curator
pip install -r requirements.txt
python app.py
# Open http://localhost:5050
# Table loads sorted by Total P&L (this works)
# Click "Markets" column header — should sort by markets but doesn't stick
# Click "Realized" column header — should sort by realized but doesn't stick
```

## What a Fix Looks Like

- Click any column header → table sorts by that column (ascending)
- Click again → sorts descending
- Sort persists until user clicks a different column
- Numeric columns sort numerically (2, 6, 17 — not 17, 2, 6)
- Text columns sort alphabetically
- Works with the existing callback architecture (data populated via callback)

## Repo Setup

```
git clone https://github.com/jam4fox99/wallet-curator.git
cd wallet-curator
pip install -r requirements.txt
python app.py
```

Create a branch for the fix:
```
git checkout -b fix/table-sorting
# make changes
git add .
git commit -m "Fix DataTable sorting"
git push -u origin fix/table-sorting
```

Then open a Pull Request on GitHub to merge into main.

## Key Files

- `app.py` — the Dash app, DataTable definition (~line 148), callback (~line 219)
- `lib/snapshots.py` — `get_combined_dataframe()` builds the DataFrame
- `requirements.txt` — dependencies


Dev Thoughts

## Root Cause Analysis

There are two distinct bugs causing the sort to not stick, and they compound each other:

### Bug 1: DataTable is Recreated on Every Tab Switch (the primary killer)

The tab-switching callback at app.py:192-198 calls pnl_tab_layout() which returns a brand-new dash_table.DataTable component every time:

@callback(Output('tab-content', 'children'), Input('tabs', 'active_tab'))
def render_tab(tab):
    if tab == 'tab-pnl':
        return pnl_tab_layout()  # <-- creates a NEW DataTable from scratch

This is relevant because pnl_tab_layout() at app.py:117-165 hardcodes the initial sort_by:

sort_by=[{'column_id': 'total_pnl', 'direction': 'desc'}],

Every time the tab is rendered, the DataTable is born with that initial sort_by. But more critically, the DataTable has no persistent data or columns -- it starts empty and waits for the update_table callback to populate it. This creates a race where the component initializes, fires sort_by as an Input, and the callback must populate everything from scratch.

This isn't the main sorting bug though (users aren't switching tabs to break it). The real issue is Bug 2.

### Bug 2: Circular Callback Loop via sort_by (the actual sort-snap-back)

The update_table callback at app.py:219-340 has sort_by as an Input alongside three other Inputs. Here's the critical flow when a user clicks a column header:

1. User clicks "Markets" column header
2. DataTable updates sort_by to [{'column_id': 'markets', 'direction': 'asc'}]
3. This triggers update_table because sort_by is an Input
4. The callback correctly detects triggered_id == 'pnl-table' and returns no_update for columns (line 333-335)
5. BUT it still returns new data (the sorted records)
6. When Dash's DataTable receives new data, it resets sort_by back to its initial value (total_pnl desc) -- this is a known Dash DataTable behavior where setting data programmatically
   clears the sort state
7. The reset sort_by fires the callback again (since sort_by is an Input)
8. Now sort_by is back to total_pnl desc, the callback sorts by that, returns data again
9. The table shows total_pnl desc -- the user's sort "didn't stick"

The no_update fix at lines 333-335 was the right instinct but doesn't solve it because returning data alone is enough to reset sort_by in Dash's DataTable component, especially with sort_action='custom'.

### Bug 3 (Minor): The no_update Still Returns Records

Even when the sort trigger is detected (app.py:334), the callback returns the full records as data output. In Dash 4.0, pushing new data to a DataTable with sort_action='custom' can reset the component's internal sort indicator, creating the visual "snap back."

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


## The Fix

There are two approaches, ordered by reliability:

### Approach A: Separate the Sort Callback (Recommended)

Split update_table into two callbacks:

1. Data callback -- triggered by snapshot-select, hidden-visible, refresh-trigger. Returns data, columns, style_data_conditional, summaries. Applies a default sort (total_pnl desc) when
   building data. Does NOT take sort_by as an input.

2. Sort callback -- triggered ONLY by pnl-table.sort_by. Takes sort_by as Input and current pnl-table.data as State. Re-sorts the existing data client-side in Python and returns only 
   Output('pnl-table', 'data'). Does NOT touch columns or styles.

@callback(
    [Output('pnl-table', 'data'),
     Output('pnl-table', 'columns'),
     Output('pnl-table', 'style_data_conditional'),
     Output('summary-bar', 'children'),
     Output('footnotes', 'children'),
     Output('btn-hidden', 'children')],
    [Input('snapshot-select', 'value'),
     Input('hidden-visible', 'data'),
     Input('refresh-trigger', 'data')],
    # NOTE: sort_by is NOT an input here
)
def update_table(snapshot_ids, show_hidden, _):
    # ... build df, default sort by total_pnl desc, return everything ...


@callback(
    Output('pnl-table', 'data', allow_duplicate=True),
    Input('pnl-table', 'sort_by'),
    State('pnl-table', 'data'),
    prevent_initial_call=True,
)
def sort_table(sort_by, current_data):
    if not sort_by or not current_data:
        return no_update
    col = sort_by[0]['column_id']
    direction = sort_by[0].get('direction', 'asc')
    
    # Sort the existing records in-place
    df = pd.DataFrame(current_data)
    if col in df.columns:
        # Ensure numeric columns sort numerically
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(df[col])
        df = df.sort_values(col, ascending=(direction == 'asc'), na_position='last')
    return df.to_dict('records')

This breaks the circular loop because:
• The sort callback only outputs data, never columns
• The data callback never reads sort_by, so it can't create a feedback loop
• prevent_initial_call=True stops it from firing on page load

### Approach B: Switch to sort_action='native' with Proper Numeric Coercion (Simpler)

The original attempt at native sorting failed because the data was arriving as strings. The fix would be to ensure df.to_dict('records') produces actual Python int/float values (not string representations), which you've already done with pd.to_numeric() at app.py:242-247. 

However, there's a subtle issue: some values might still be strings after to_dict('records') if the DataFrame has mixed types in a column. The fix would be:

1. Set sort_action='native' at app.py:151
2. Remove sort_by entirely from the callback Inputs (line 229)
3. Remove the server-side sort logic (lines 317-325)
4. Remove the triggered_id check (lines 332-337)
5. After pd.to_numeric, explicitly cast: df[col] = df[col].astype(float) for numeric columns

But I'd recommend Approach A because it gives you full control over sort behavior and avoids any Dash DataTable quirks with native sort and mixed-type columns.

### Bonus: Fix the Tab Recreation Issue

Regardless of which approach you pick, the DataTable being recreated on tab switch is wasteful and loses state. Consider moving the DataTable out of the tab-switching callback and using CSS display toggling instead, or use dcc.Store to persist the current sort state across tab switches.

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


## Summary

┌─────────────────────────────────────────────────┬─────────────────┬──────────────────────────────────────────┐
│ Issue                                           │ Location        │ Impact                                   │
├─────────────────────────────────────────────────┼─────────────────┼──────────────────────────────────────────┤
│ sort_by as Input creates circular callback loop │ app.py:229      │ Sort clicks snap back to default         │
├─────────────────────────────────────────────────┼─────────────────┼──────────────────────────────────────────┤
│ Returning data resets DataTable sort state      │ app.py:327, 335 │ Even the no_update fix doesn't help      │
├─────────────────────────────────────────────────┼─────────────────┼──────────────────────────────────────────┤
│ DataTable recreated on tab switch               │ app.py:194      │ Loses all sort state when switching tabs │
└─────────────────────────────────────────────────┴─────────────────┴──────────────────────────────────────────┘

The fix is to separate the sort callback from the data-loading callback so they can't create a feedback loop.
⏺ [15:10:35] Update Todos 1 item(s)

  󰄵 Research the DataTable sorting bug in app.py
  󰄵 Examine the callback structure and sort_by handling
  󰄵 Identify root causes and propose fixes

That's the full analysis. The core issue is the circular callback loop -- sort_by is an Input to the same callback that outputs data, and pushing new data resets sort_by, which triggers the callback again, snapping the sort back to default. The fix is to split it into two independent callbacks. Let me know if you want me to implement it.