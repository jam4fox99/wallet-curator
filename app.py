#!/usr/bin/env python3
"""Wallet Curator — Dash Web UI Dashboard."""
import logging
import threading

import dash
from dash import html, dcc, dash_table, Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc
import pandas as pd

from lib.db import init_db, get_connection

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)
app.title = "Wallet Curator"

# ─── Custom CSS ────────────────────────────────────────────────
CUSTOM_CSS = {
    'fontFamily': 'monospace',
    'backgroundColor': '#0f0f0f',
    'minHeight': '100vh',
    'padding': '20px',
    'color': '#e5e5e5',
}

TABLE_STYLE = {
    'backgroundColor': '#1a1a1a',
    'color': '#e5e5e5',
    'fontFamily': 'monospace',
    'fontSize': '13px',
}

HEADER_STYLE = {
    'backgroundColor': '#1a1a1a',
    'color': '#e5e5e5',
    'fontWeight': 'bold',
    'border': '1px solid #2a2a2a',
    'fontFamily': 'monospace',
    'fontSize': '13px',
}

CELL_STYLE = {
    'backgroundColor': '#0f0f0f',
    'color': '#e5e5e5',
    'border': '1px solid #2a2a2a',
    'fontFamily': 'monospace',
    'fontSize': '13px',
}

SINGLE_COLUMNS = [
    {'name': '👁', 'id': 'hide', 'type': 'text'},
    {'name': 'Wallet', 'id': 'wallet', 'type': 'text'},
    {'name': 'Snaps', 'id': 'snaps', 'type': 'text'},
    {'name': 'Filter', 'id': 'filter', 'type': 'text'},
    {'name': 'Actual', 'id': 'actual', 'type': 'text'},
    {'name': 'Sim', 'id': 'sim', 'type': 'text'},
    {'name': 'Invested', 'id': 'invested', 'type': 'numeric'},
    {'name': 'Realized', 'id': 'realized', 'type': 'numeric'},
    {'name': 'Open Val', 'id': 'open_val', 'type': 'numeric'},
    {'name': 'Open P&L', 'id': 'open_pnl', 'type': 'numeric'},
    {'name': 'Total P&L', 'id': 'total_pnl', 'type': 'numeric'},
    {'name': 'Markets', 'id': 'markets', 'type': 'numeric'},
    {'name': 'Trades', 'id': 'trades', 'type': 'numeric'},
    {'name': 'In CSV', 'id': 'in_csv', 'type': 'text'},
    {'name': 'Excluded', 'id': 'excluded', 'type': 'numeric'},
]
PNL_COL_IDS = ['realized', 'open_pnl', 'total_pnl', 'combined']
GREEN = '#22c55e'
RED = '#ef4444'
DEFAULT_PNL_SORT = [{'column_id': 'total_pnl', 'direction': 'desc'}]


# ─── Layout ────────────────────────────────────────────────────
def get_snapshot_options():
    init_db()
    conn = get_connection()
    from lib.snapshots import get_all_snapshots
    snaps = get_all_snapshots(conn)
    conn.close()
    options = []
    for s in snaps:
        label = f"Snap #{s['snapshot_id']} — {s['description'] or 'no description'}"
        options.append({'label': label, 'value': s['snapshot_id']})
    return options


def get_default_snapshot():
    opts = get_snapshot_options()
    return [opts[0]['value']] if opts else []


def get_default_pnl_sort():
    return [dict(item) for item in DEFAULT_PNL_SORT]


def build_pnl_columns(snapshot_ids):
    if len(snapshot_ids) == 1:
        return list(SINGLE_COLUMNS)

    columns = [
        {'name': '👁', 'id': 'hide', 'type': 'text'},
        {'name': 'Wallet', 'id': 'wallet', 'type': 'text'},
        {'name': 'Snaps', 'id': 'snaps', 'type': 'text'},
        {'name': 'Filter', 'id': 'filter', 'type': 'text'},
        {'name': 'Actual', 'id': 'actual', 'type': 'text'},
        {'name': 'Sim', 'id': 'sim', 'type': 'text'},
    ]
    for sid in sorted(snapshot_ids):
        columns.append({'name': f'#{sid} Inv', 'id': f's{sid}_inv', 'type': 'numeric'})
        columns.append({'name': f'#{sid} Real', 'id': f's{sid}_real', 'type': 'numeric'})
        columns.append({'name': f'#{sid} Total', 'id': f's{sid}_total', 'type': 'numeric'})
    columns.extend([
        {'name': 'Combined', 'id': 'combined', 'type': 'numeric'},
        {'name': 'Markets', 'id': 'markets', 'type': 'numeric'},
        {'name': 'Trades', 'id': 'trades', 'type': 'numeric'},
        {'name': 'In CSV', 'id': 'in_csv', 'type': 'text'},
        {'name': 'Excluded', 'id': 'excluded', 'type': 'numeric'},
    ])
    return columns


def sanitize_sort_by(sort_by, available_columns):
    col_ids = {c['id'] for c in available_columns}
    active_sort = sort_by or get_default_pnl_sort()
    col = active_sort[0].get('column_id')

    if col in col_ids:
        return active_sort
    if 'total_pnl' in col_ids:
        return get_default_pnl_sort()
    if 'combined' in col_ids:
        return [{'column_id': 'combined', 'direction': 'desc'}]
    return []


def sort_dataframe(df, sort_by):
    if df.empty:
        return df

    active_sort = sort_by or get_default_pnl_sort()
    if not active_sort:
        return df

    col = active_sort[0].get('column_id')
    direction = active_sort[0].get('direction', 'asc')
    if col not in df.columns:
        fallback_sort = sanitize_sort_by(active_sort, [{'id': c} for c in df.columns])
        if not fallback_sort:
            return df
        col = fallback_sort[0]['column_id']
        direction = fallback_sort[0].get('direction', 'asc')

    sorted_df = df.copy()
    sorted_df[col] = pd.to_numeric(sorted_df[col], errors='coerce').fillna(sorted_df[col])
    return sorted_df.sort_values(col, ascending=(direction == 'asc'), na_position='last')


app.layout = dbc.Container([
    dcc.Store(id='refresh-trigger', data=0),
    dcc.Store(id='hidden-visible', data=False),
    dcc.Store(id='pnl-sort-state', data=get_default_pnl_sort(), storage_type='session'),

    # Header
    dbc.Row([
        dbc.Col(html.H2("Wallet Curator", style={'color': '#e5e5e5', 'margin': '0'}), width=6),
        dbc.Col(html.Div(id='status-bar', style={'textAlign': 'right', 'color': '#888'}), width=6),
    ], className='mb-3', align='center'),

    # Navigation
    dbc.Tabs([
        dbc.Tab(label="PnL Dashboard", tab_id="tab-pnl"),
        dbc.Tab(label="Wallet Changes", tab_id="tab-changes"),
    ], id='tabs', active_tab='tab-pnl', className='mb-3'),

    html.Div(id='tab-content'),

], fluid=True, style=CUSTOM_CSS)


def pnl_tab_layout(sort_by=None):
    return html.Div([
        # Action bar
        dbc.Row([
            dbc.Col([
                dbc.Button("Ingest ▶", id='btn-ingest', color='primary', className='me-2', size='sm'),
                dbc.Button("Refresh 🔄", id='btn-refresh', color='secondary', className='me-2', size='sm'),
                dbc.Button("Show Hidden (0)", id='btn-hidden', color='dark', outline=True, size='sm'),
            ], width=5),
            dbc.Col([
                dcc.Dropdown(
                    id='snapshot-select',
                    options=get_snapshot_options(),
                    value=get_default_snapshot(),
                    multi=True,
                    placeholder="Select snapshot(s)...",
                    style={'backgroundColor': '#1a1a1a', 'color': '#000'},
                ),
            ], width=7),
        ], className='mb-3'),

        # Summary bar
        html.Div(id='summary-bar', style={'color': '#888', 'marginBottom': '10px', 'fontSize': '13px'}),

        # Notification
        html.Div(id='notification', style={'marginBottom': '10px'}),

        # Loading wrapper
        dcc.Loading(
            id='loading-table',
            type='default',
            children=[
                dash_table.DataTable(
                    id='pnl-table',
                    sort_action='custom',
                    sort_mode='single',
                    sort_by=sort_by or get_default_pnl_sort(),
                    style_table={'overflowX': 'auto'},
                    style_header=HEADER_STYLE,
                    style_cell=CELL_STYLE,
                    style_data_conditional=[],
                    page_size=100,
                ),
            ],
        ),

        # Footnotes
        html.Div(id='footnotes', style={'color': '#888', 'marginTop': '10px', 'fontSize': '12px'}),
    ])


def changes_tab_layout():
    return html.Div([
        dbc.Row([
            dbc.Col([
                html.H5("CSV Version History", style={'color': '#e5e5e5'}),
                dcc.Dropdown(
                    id='csv-version-select',
                    placeholder="Select a past CSV version...",
                    style={'backgroundColor': '#1a1a1a', 'color': '#000'},
                ),
            ], width=6),
        ], className='mb-3'),

        html.Div(id='csv-viewer', className='mb-4'),

        html.H5("Full Changelog", style={'color': '#e5e5e5'}),
        html.Div(id='changelog-content', style={
            'backgroundColor': '#1a1a1a', 'padding': '15px', 'borderRadius': '8px',
            'maxHeight': '500px', 'overflowY': 'auto', 'fontSize': '13px',
        }),
    ])


# ─── Tab switching ─────────────────────────────────────────────
@callback(Output('tab-content', 'children'), Input('tabs', 'active_tab'), State('pnl-sort-state', 'data'))
def render_tab(tab, sort_by):
    if tab == 'tab-pnl':
        return pnl_tab_layout(sort_by=sort_by)
    elif tab == 'tab-changes':
        return changes_tab_layout()
    return html.Div()


# ─── Status bar ────────────────────────────────────────────────
@callback(Output('status-bar', 'children'), Input('refresh-trigger', 'data'))
def update_status(_):
    try:
        conn = get_connection()
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        res = conn.execute("SELECT resolved, COUNT(*) FROM resolutions GROUP BY resolved").fetchall()
        res_map = {r[0]: r[1] for r in res}
        resolved = res_map.get(1, 0) + res_map.get(-1, 0) + res_map.get(2, 0)
        total = sum(res_map.values())
        last = conn.execute("SELECT MAX(ingested_at) FROM ingest_registry").fetchone()[0]
        conn.close()
        return f"{trades:,} trades | {resolved}/{total} resolved | Last ingest: {last or 'never'}"
    except Exception:
        return "No data yet"


# ─── PnL table: DATA callback (no sort_by input — breaks the circular loop) ───
@callback(
    [Output('pnl-table', 'data'),
     Output('pnl-table', 'columns'),
     Output('pnl-table', 'sort_by'),
     Output('pnl-table', 'style_data_conditional'),
     Output('summary-bar', 'children'),
     Output('footnotes', 'children'),
     Output('btn-hidden', 'children')],
    [Input('snapshot-select', 'value'),
     Input('hidden-visible', 'data'),
     Input('refresh-trigger', 'data')],
    State('pnl-sort-state', 'data'),
)
def update_table(snapshot_ids, show_hidden, _, stored_sort_by):
    if not snapshot_ids:
        return [], [], get_default_pnl_sort(), [], "No snapshots selected", "", "Show Hidden (0)"

    try:
        conn = get_connection()
        from lib.snapshots import get_combined_dataframe, get_all_snapshots

        df = get_combined_dataframe(conn, snapshot_ids, include_hidden=show_hidden)

        # Force numeric columns to proper numeric types
        numeric_cols = [c for c in df.columns if c in
                        ['invested', 'realized', 'open_val', 'open_pnl', 'total_pnl',
                         'markets', 'trades', 'excluded', 'combined']
                        or c.endswith('_inv') or c.endswith('_real') or c.endswith('_total')]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Hidden count
        hidden_count = conn.execute("SELECT COUNT(*) FROM hidden_wallets").fetchone()[0]

        # Summary bar
        snaps = get_all_snapshots(conn)
        snap_map = {s['snapshot_id']: s for s in snaps}
        parts = []
        for sid in sorted(snapshot_ids):
            if sid in snap_map:
                s = snap_map[sid]
                parts.append(f"Snap #{sid} ({s['new_trades_since_last']:,} trades, {s['description'] or ''})")
        summary = " + ".join(parts)

        conn.close()

        if df.empty:
            columns = build_pnl_columns(snapshot_ids)
            sort_by = sanitize_sort_by(stored_sort_by, columns)
            return [], columns, sort_by, [], summary, "", f"Show Hidden ({hidden_count})"

        columns = build_pnl_columns(snapshot_ids)
        sort_by = sanitize_sort_by(stored_sort_by, columns)

        # Conditional formatting + cell selection fix
        col_ids = [c['id'] for c in columns]
        pnl_col_ids = [c for c in col_ids if any(c.endswith(k) for k in ['realized', 'open_pnl', 'total_pnl', 'combined', '_real', '_total'])]
        style_cond = [
            {'if': {'state': 'active'}, 'backgroundColor': '#2a2a2a', 'color': '#e5e5e5', 'border': '1px solid #444'},
            {'if': {'state': 'selected'}, 'backgroundColor': '#2a2a2a', 'color': '#e5e5e5', 'border': '1px solid #444'},
        ]
        for col_id in pnl_col_ids:
            style_cond.append({
                'if': {'filter_query': f'{{{col_id}}} > 0', 'column_id': col_id},
                'color': GREEN,
            })
            style_cond.append({
                'if': {'filter_query': f'{{{col_id}}} < 0', 'column_id': col_id},
                'color': RED,
            })

        # Footnotes
        footnotes = []
        if 'excluded' in df.columns:
            excluded_df = df[df['excluded'] > 0]
            for _, row in excluded_df.iterrows():
                footnotes.append(f"* {row['wallet']}: {row['excluded']} positions excluded (missing buy data)")

        df = sort_dataframe(df, sort_by)

        records = df.to_dict('records')
        fn_text = html.Div([html.Div(f, style={'color': '#888'}) for f in footnotes]) if footnotes else ""

        return records, columns, sort_by, style_cond, summary, fn_text, f"Show Hidden ({hidden_count})"

    except Exception as e:
        return [], [], get_default_pnl_sort(), [], f"Error: {e}", "", "Show Hidden (0)"


@callback(
    Output('pnl-sort-state', 'data'),
    Input('pnl-table', 'sort_by'),
    prevent_initial_call=True,
)
def persist_table_sort(sort_by):
    return sort_by or get_default_pnl_sort()


# ─── PnL table: SORT callback (separate — no circular loop) ───────────────────
@callback(
    Output('pnl-table', 'data', allow_duplicate=True),
    Input('pnl-table', 'sort_by'),
    State('pnl-table', 'data'),
    prevent_initial_call=True,
)
def sort_table(sort_by, current_data):
    if not sort_by or not current_data:
        return no_update
    df = pd.DataFrame(current_data)
    return sort_dataframe(df, sort_by).to_dict('records')


# ─── Ingest button ─────────────────────────────────────────────
@callback(
    [Output('notification', 'children'),
     Output('refresh-trigger', 'data', allow_duplicate=True),
     Output('snapshot-select', 'options', allow_duplicate=True),
     Output('snapshot-select', 'value', allow_duplicate=True)],
    Input('btn-ingest', 'n_clicks'),
    prevent_initial_call=True,
)
def run_ingest(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    try:
        conn = get_connection()

        # Run ingest pipeline
        from lib.ingest_sharp import run as ingest_run
        from lib.db import rebuild_positions, ensure_resolution_entries
        from lib.resolver import check_resolutions
        from lib.pricing import fetch_prices
        from lib.pnl import compute_wallet_pnl
        from lib.snapshots import maybe_create_snapshot, save_csv_if_changed
        from lib.changelog import detect_changes

        excluded = ingest_run()
        if excluded is None:
            excluded = {}

        conn = get_connection()
        ensure_resolution_entries(conn)
        resolved = check_resolutions(conn)
        prices = fetch_prices(conn)
        compute_wallet_pnl(conn, excluded, current_prices=prices)

        new_trades = conn.execute("SELECT SUM(new_trades) FROM ingest_registry").fetchone()[0] or 0
        snap_id = maybe_create_snapshot(conn, new_trade_count=new_trades)
        save_csv_if_changed(conn)
        detect_changes(conn)
        conn.close()

        msg = dbc.Alert(f"Ingest complete. {resolved} tokens resolved. Snapshot #{snap_id} updated.",
                        color='success', duration=5000)
        opts = get_snapshot_options()
        default = [opts[0]['value']] if opts else []
        return msg, dash.callback_context.triggered[0]['prop_id'], opts, default

    except Exception as e:
        return dbc.Alert(f"Ingest failed: {e}", color='danger', duration=8000), no_update, no_update, no_update


# ─── Refresh button ────────────────────────────────────────────
@callback(
    [Output('notification', 'children', allow_duplicate=True),
     Output('refresh-trigger', 'data', allow_duplicate=True)],
    Input('btn-refresh', 'n_clicks'),
    prevent_initial_call=True,
)
def run_refresh(n_clicks):
    if not n_clicks:
        return no_update, no_update

    try:
        conn = get_connection()
        from lib.db import ensure_resolution_entries
        from lib.resolver import check_resolutions
        from lib.pricing import fetch_prices
        from lib.pnl import compute_wallet_pnl
        from lib.snapshots import maybe_create_snapshot

        ensure_resolution_entries(conn)
        resolved = check_resolutions(conn)
        prices = fetch_prices(conn)
        compute_wallet_pnl(conn, current_prices=prices)
        maybe_create_snapshot(conn)
        conn.close()

        msg = dbc.Alert(f"Refresh complete. {resolved} tokens resolved. Prices updated.",
                        color='info', duration=5000)
        return msg, dash.callback_context.triggered[0]['prop_id']

    except Exception as e:
        return dbc.Alert(f"Refresh failed: {e}", color='danger', duration=8000), no_update


# ─── Hide/Show toggle ──────────────────────────────────────────
@callback(
    Output('hidden-visible', 'data'),
    Input('btn-hidden', 'n_clicks'),
    State('hidden-visible', 'data'),
    prevent_initial_call=True,
)
def toggle_hidden(n_clicks, current):
    return not current


# ─── Hide wallet (via 👁 column click) ─────────────────────────
@callback(
    Output('refresh-trigger', 'data', allow_duplicate=True),
    Input('pnl-table', 'active_cell'),
    State('pnl-table', 'data'),
    prevent_initial_call=True,
)
def handle_cell_click(active_cell, data):
    if not active_cell or not data:
        return no_update

    col = active_cell['column_id']
    if col != 'hide':
        return no_update

    row = data[active_cell['row']]
    wallet = row.get('wallet', '')
    if not wallet.startswith('0x'):
        return no_update

    conn = get_connection()
    existing = conn.execute(
        "SELECT 1 FROM hidden_wallets WHERE wallet_address = ?", (wallet,)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM hidden_wallets WHERE wallet_address = ?", (wallet,))
    else:
        conn.execute("INSERT OR IGNORE INTO hidden_wallets (wallet_address) VALUES (?)", (wallet,))
    conn.commit()
    conn.close()
    return 'hide-toggle'


# ─── Wallet Changes tab ───────────────────────────────────────
@callback(
    Output('csv-version-select', 'options'),
    Input('tabs', 'active_tab'),
)
def load_csv_versions(tab):
    if tab != 'tab-changes':
        return no_update
    try:
        conn = get_connection()
        versions = conn.execute(
            "SELECT id, saved_at, wallet_count, changes_summary FROM csv_history ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return [
            {'label': f"{v['saved_at']} — {v['wallet_count']} wallets ({v['changes_summary'] or ''})",
             'value': v['id']}
            for v in versions
        ]
    except Exception:
        return []


@callback(
    Output('csv-viewer', 'children'),
    Input('csv-version-select', 'value'),
)
def show_csv_version(version_id):
    if not version_id:
        return html.Div("Select a CSV version to view", style={'color': '#888'})
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM csv_history WHERE id = ?", (version_id,)).fetchone()
        conn.close()
        if not row:
            return html.Div("Version not found", style={'color': '#888'})

        import csv as csv_mod
        import io
        reader = csv_mod.DictReader(io.StringIO(row['csv_content']))
        rows_data = list(reader)
        if not rows_data:
            return html.Div("Empty CSV", style={'color': '#888'})

        df = pd.DataFrame(rows_data)
        # Only show address and market_whitelist
        show_cols = ['address', 'market_whitelist']
        show_cols = [c for c in show_cols if c in df.columns]
        if show_cols:
            df = df[show_cols]
        df = df[df['address'] != '__global__'] if 'address' in df.columns else df

        return dash_table.DataTable(
            data=df.to_dict('records'),
            columns=[{'name': c, 'id': c} for c in df.columns],
            style_header=HEADER_STYLE,
            style_cell=CELL_STYLE,
            page_size=50,
        )
    except Exception as e:
        return html.Div(f"Error: {e}", style={'color': RED})


@callback(
    Output('changelog-content', 'children'),
    Input('tabs', 'active_tab'),
)
def load_changelog(tab):
    if tab != 'tab-changes':
        return no_update
    try:
        conn = get_connection()
        changes = conn.execute(
            "SELECT * FROM wallet_changes ORDER BY change_date DESC LIMIT 50"
        ).fetchall()
        conn.close()

        if not changes:
            return html.Div("No wallet changes recorded yet.", style={'color': '#888'})

        items = []
        for c in changes:
            icon = "✅" if c['action'] == 'ADDED' else "🔴"
            addr = c['wallet_address']
            short = addr[:10] + '...' + addr[-4:]
            game = c['game_filter'] or '?'
            date = c['change_date']
            items.append(html.Div(
                f"{icon} {c['action']} {short} ({game}) — {date}",
                style={'marginBottom': '4px'}
            ))
        return html.Div(items)
    except Exception:
        return html.Div("Error loading changelog", style={'color': RED})


# ─── Run ───────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()

    # Create initial snapshot if none exist
    conn = get_connection()
    snap_count = conn.execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0]
    trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if snap_count == 0 and trade_count > 0:
        from lib.snapshots import maybe_create_snapshot, save_csv_if_changed
        maybe_create_snapshot(conn)
        save_csv_if_changed(conn)
    conn.close()

    print("\n  Wallet Curator Dashboard")
    print("  http://localhost:5050\n")
    app.run(debug=False, port=5050)
