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
