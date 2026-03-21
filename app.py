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

PNL_COLUMNS = ['Realized', 'Open P&L', 'Total P&L', 'Combined']
GREEN = '#22c55e'
RED = '#ef4444'


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


app.layout = dbc.Container([
    dcc.Store(id='refresh-trigger', data=0),
    dcc.Store(id='hidden-visible', data=False),

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


def pnl_tab_layout():
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
                    sort_action='native',
                    sort_mode='single',
                    sort_by=[{'column_id': 'Total P&L', 'direction': 'desc'}],
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
@callback(Output('tab-content', 'children'), Input('tabs', 'active_tab'))
def render_tab(tab):
    if tab == 'tab-pnl':
        return pnl_tab_layout()
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


# ─── PnL table ─────────────────────────────────────────────────
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
)
def update_table(snapshot_ids, show_hidden, _):
    if not snapshot_ids:
        return [], [], [], "No snapshots selected", "", "Show Hidden (0)"

    try:
        conn = get_connection()
        from lib.snapshots import get_combined_dataframe, get_all_snapshots

        df = get_combined_dataframe(conn, snapshot_ids, include_hidden=show_hidden)

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
            return [], [], [], summary, "", f"Show Hidden ({hidden_count})"

        # Remove internal columns from display
        display_cols = [c for c in df.columns if c != 'wallet_full']
        columns = [{'name': c, 'id': c} for c in display_cols]

        # Conditional formatting for P&L columns
        pnl_cols = [c for c in display_cols if any(k in c for k in ['Real', 'P&L', 'Total', 'Combined'])]
        style_cond = []
        for col in pnl_cols:
            style_cond.append({
                'if': {'filter_query': f'{{{col}}} > 0', 'column_id': col},
                'color': GREEN,
            })
            style_cond.append({
                'if': {'filter_query': f'{{{col}}} < 0', 'column_id': col},
                'color': RED,
            })

        # Footnotes
        footnotes = []
        if 'Excluded' in df.columns:
            excluded = df[df['Excluded'] > 0]
            for _, row in excluded.iterrows():
                footnotes.append(f"* {row['Wallet']}: {row['Excluded']} positions excluded (missing buy data)")

        records = df[display_cols].to_dict('records')
        fn_text = html.Div([html.Div(f, style={'color': '#888'}) for f in footnotes]) if footnotes else ""

        return records, columns, style_cond, summary, fn_text, f"Show Hidden ({hidden_count})"

    except Exception as e:
        return [], [], [], f"Error: {e}", "", "Show Hidden (0)"


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


# ─── Hide wallet (via table click) ─────────────────────────────
@callback(
    Output('refresh-trigger', 'data', allow_duplicate=True),
    Input('pnl-table', 'active_cell'),
    State('pnl-table', 'data'),
    State('hidden-visible', 'data'),
    prevent_initial_call=True,
)
def handle_cell_click(active_cell, data, show_hidden):
    if not active_cell or not data:
        return no_update

    row = data[active_cell['row']]
    col = active_cell['column_id']

    # Check if the wallet column was clicked — use as hide/unhide toggle
    if col == 'Wallet':
        wallet_short = row['Wallet']
        # Find full wallet address
        conn = get_connection()
        full = conn.execute(
            "SELECT master_wallet FROM wallet_pnl WHERE master_wallet LIKE ? LIMIT 1",
            (wallet_short[:8] + '%',)
        ).fetchone()
        if full:
            addr = full['master_wallet']
            existing = conn.execute(
                "SELECT 1 FROM hidden_wallets WHERE wallet_address = ?", (addr,)
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM hidden_wallets WHERE wallet_address = ?", (addr,))
            else:
                conn.execute("INSERT OR IGNORE INTO hidden_wallets (wallet_address) VALUES (?)", (addr,))
            conn.commit()
        conn.close()
        return 'hide-toggle'

    return no_update


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
