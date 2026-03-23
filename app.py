#!/usr/bin/env python3
"""Wallet Curator cloud dashboard."""
import logging
import os
from datetime import timedelta

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, clientside_callback, dash_table, dcc, html, no_update

try:
    import dash_auth
except ModuleNotFoundError:
    dash_auth = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ModuleNotFoundError:
    BackgroundScheduler = None

from lib.changelog import get_recent_changes
from lib.charts import get_chart_payload, get_sync_status_summary, get_wallet_options, get_wallet_stats
from lib.daily_pnl import get_daily_breakdown
from lib.db import get_connection, init_db
from lib.pipeline import run_hourly_pipeline
from lib.time_utils import now_utc, parse_db_timestamp

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

COLORS = {
    "background": "#0f0f0f",
    "card": "#1a1a1a",
    "text": "#e5e5e5",
    "text_secondary": "#9ca3af",
    "positive": "#22c55e",
    "negative": "#ef4444",
    "border": "#2a2a2a",
    "button": "#2563eb",
}
FONT_FAMILY = '"Manrope", "Avenir Next", "Segoe UI", sans-serif'
READ_ONLY_UI = os.environ.get("READ_ONLY_UI") == "1"

RANGES = ["1D", "3D", "7D", "15D", "30D", "ALL"]
TABLE_COLUMNS = [
    {"name": "Hide", "id": "hide"},
    {"name": "Wallet", "id": "wallet"},
    {"name": "Filter", "id": "filter"},
    {"name": "Actual", "id": "actual"},
    {"name": "Sim #", "id": "sim"},
    {"name": "Invested", "id": "invested", "type": "numeric"},
    {"name": "Realized P&L", "id": "realized_pnl", "type": "numeric"},
    {"name": "Unrealized", "id": "unrealized_pnl", "type": "numeric"},
    {"name": "Total P&L", "id": "total_pnl", "type": "numeric"},
    {"name": "Markets In Range", "id": "markets", "type": "numeric"},
    {"name": "Trades In Range", "id": "trades", "type": "numeric"},
    {"name": "In CSV", "id": "in_csv"},
]

app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        "https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap",
    ],
    suppress_callback_exceptions=True,
)
server = app.server
server.secret_key = os.environ.get("FLASK_SECRET_KEY", "wallet-curator-dashboard")
app.title = "Wallet Curator Dashboard"

if dash_auth and os.environ.get("DASH_USERNAME") and os.environ.get("DASH_PASSWORD"):
    dash_auth.BasicAuth(
        app,
        {os.environ["DASH_USERNAME"]: os.environ["DASH_PASSWORD"]},
        public_routes=["/healthz"],
    )
elif os.environ.get("DASH_USERNAME") and os.environ.get("DASH_PASSWORD"):
    logger.warning("dash-auth is not installed; dashboard auth disabled")
else:
    logger.warning("DASH_USERNAME/DASH_PASSWORD not set; dashboard auth disabled")

_scheduler_started = False


@server.route("/healthz")
def healthz():
    return "ok", 200


def start_scheduler():
    global _scheduler_started
    if _scheduler_started or os.environ.get("DISABLE_SCHEDULER") == "1":
        return
    if BackgroundScheduler is None:
        logger.warning("apscheduler is not installed; hourly scheduler disabled")
        return
    scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    scheduler.add_job(
        run_hourly_pipeline,
        "interval",
        hours=1,
        kwargs={"trigger": "scheduled"},
        id="wallet-curator-hourly-pipeline",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler_started = True
    logger.info("Started hourly scheduler")


def _card(children):
    return dbc.Card(
        dbc.CardBody(children),
        style={
            "backgroundColor": COLORS["card"],
            "border": f"1px solid {COLORS['border']}",
            "borderRadius": "16px",
        },
    )


def _range_buttons(prefix):
    return dbc.ButtonGroup(
        [dbc.Button(label, id=f"{prefix}-{label}", size="sm", outline=True, color="secondary") for label in RANGES]
    )


def _money(value):
    if value is None:
        return "-"
    return f"${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _line_color(value):
    return COLORS["positive"] if value >= 0 else COLORS["negative"]


def _chart_mount(container_id):
    return html.Div(id=container_id, className="lightweight-chart")


def _format_chart_range_label(payload, range_key):
    if not payload or not payload.get("series"):
        return "Waiting for chart history"

    start_at = parse_db_timestamp(payload.get("start_at"))
    end_at = parse_db_timestamp(payload.get("end_at"))
    if not start_at or not end_at:
        return "Waiting for chart history"

    if range_key == "ALL":
        return (
            f"Rebased from zero | {start_at.strftime('%b %d, %Y %H:%M')} UTC "
            f"to {end_at.strftime('%b %d, %Y %H:%M')} UTC"
        )

    return (
        f"{range_key} change from zero | {start_at.strftime('%b %d, %H:%M')} UTC "
        f"to {end_at.strftime('%b %d, %H:%M')} UTC"
    )


def _build_recent_changes(changes):
    if not changes:
        return html.Div("No wallet changes yet.", style={"color": COLORS["text_secondary"]})
    items = []
    for row in changes:
        label = "ADDED" if row["action"] == "ADDED" else "REMOVED"
        color = COLORS["positive"] if row["action"] == "ADDED" else COLORS["negative"]
        text = f"{label} {row['wallet_address']}"
        if row["game_filter"]:
            text += f" ({row['game_filter']})"
        items.append(html.Div(text, style={"color": color, "marginBottom": "6px"}))
    return html.Div(items)


def _database_error_layout(message):
    return dbc.Alert(message, color="danger", className="mb-0")


def overview_layout():
    today = now_utc().date()
    week_ago = today - timedelta(days=6)
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        _card(
                            [
                                dbc.Row(
                                    [
                                        dbc.Col(html.H4("Portfolio P&L", className="mb-1"), md=7),
                                        dbc.Col(_range_buttons("overview-range"), md=5, style={"textAlign": "right"}),
                                    ],
                                    align="center",
                                ),
                                html.Div(id="overview-current-pnl", style={"fontSize": "40px", "fontWeight": "600"}),
                                html.Div(id="overview-range-label", style={"color": COLORS["text_secondary"], "marginBottom": "12px"}),
                                _chart_mount("overview-chart-container"),
                            ]
                        ),
                        lg=8,
                    ),
                    dbc.Col(
                        _card(
                            [
                                html.Div(
                                    [
                                        dbc.Button("Refresh P&L", id="btn-refresh", color="primary", className="me-2"),
                                        dbc.Button("Show Hidden Wallets", id="btn-hidden", color="secondary", outline=True),
                                    ],
                                    className="mb-3",
                                ),
                                dbc.Alert(
                                    "Read-only local UI mode is enabled. Refresh P&L and hide/unhide writes are disabled.",
                                    color="secondary",
                                    className="mb-3",
                                    style={"display": "block" if READ_ONLY_UI else "none"},
                                ),
                                html.Div(id="refresh-message", className="mb-3"),
                                html.H5("Recent Changes"),
                                html.Div(id="recent-changes"),
                            ]
                        ),
                        lg=4,
                    ),
                ],
                className="g-4 mb-4",
            ),
            _card(
                [
                    dbc.Row(
                        [
                            dbc.Col(html.H4("Daily Breakdown"), md=4),
                            dbc.Col(
                                html.Div(
                                    [
                                        dbc.Button(
                                            "Include Wallets Outside Date Range",
                                            id="btn-outside-range",
                                            color="secondary",
                                            outline=True,
                                            className="me-2",
                                        ),
                                        dcc.DatePickerRange(
                                            id="daily-range",
                                            start_date=week_ago.isoformat(),
                                            end_date=today.isoformat(),
                                            display_format="YYYY-MM-DD",
                                            className="daily-date-picker",
                                        ),
                                    ],
                                    style={"display": "flex", "justifyContent": "flex-end", "gap": "12px"},
                                ),
                                md=8,
                                style={"textAlign": "right"},
                            ),
                        ],
                        align="center",
                        className="mb-3",
                    ),
                    html.Div(id="daily-totals", style={"color": COLORS["text_secondary"], "marginBottom": "10px"}),
                    dcc.Loading(
                        dash_table.DataTable(
                            id="daily-table",
                            columns=TABLE_COLUMNS,
                            data=[],
                            sort_action="native",
                            page_action="none",
                            fixed_rows={"headers": True},
                            style_header={
                                "backgroundColor": COLORS["card"],
                                "color": COLORS["text"],
                                "border": f"1px solid {COLORS['border']}",
                            },
                            style_cell={
                                "backgroundColor": COLORS["background"],
                                "color": COLORS["text"],
                                "border": f"1px solid {COLORS['border']}",
                                "padding": "8px",
                                "fontFamily": FONT_FAMILY,
                                "fontSize": "14px",
                                "lineHeight": "1.4",
                                "whiteSpace": "normal",
                                "height": "auto",
                            },
                            style_cell_conditional=[
                                {
                                    "if": {"column_id": "wallet"},
                                    "minWidth": "340px",
                                    "width": "340px",
                                    "maxWidth": "420px",
                                    "whiteSpace": "nowrap",
                                },
                                {"if": {"column_id": "filter"}, "minWidth": "110px", "width": "110px", "maxWidth": "140px"},
                                {"if": {"column_id": "actual"}, "minWidth": "110px", "width": "110px", "maxWidth": "140px"},
                            ],
                            style_table={"overflowX": "auto", "overflowY": "auto", "height": "560px", "maxHeight": "560px"},
                            style_data_conditional=[],
                        )
                    ),
                ]
            ),
        ]
    )


def wallet_layout():
    return html.Div(
        [
            _card(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                dcc.Dropdown(id="wallet-dropdown", placeholder="Select wallet..."),
                                lg=7,
                            ),
                            dbc.Col(_range_buttons("wallet-range"), lg=5, style={"textAlign": "right"}),
                        ],
                        className="mb-3",
                        align="center",
                    ),
                    html.Div(id="wallet-current-pnl", style={"fontSize": "34px", "fontWeight": "600"}),
                    html.Div(id="wallet-range-label", style={"color": COLORS["text_secondary"], "marginBottom": "12px"}),
                    _chart_mount("wallet-chart-container"),
                    html.Div(id="wallet-stats"),
                ]
            )
        ]
    )


def serve_layout():
    return dbc.Container(
        [
            dcc.Store(id="refresh-token", data=0),
            dcc.Store(id="overview-range", data="ALL"),
            dcc.Store(id="wallet-range", data="ALL"),
            dcc.Store(id="show-hidden", data=False),
            dcc.Store(id="include-outside-range", data=False),
            dcc.Store(id="overview-chart-data"),
            dcc.Store(id="wallet-chart-data"),
            dcc.Interval(id="status-poll", interval=60_000, n_intervals=0),
            html.Div(id="overview-chart-signal", style={"display": "none"}),
            html.Div(id="wallet-chart-signal", style={"display": "none"}),
            dbc.Row(
                [
                    dbc.Col(html.H2("Wallet Curator Dashboard", style={"margin": 0}), md=6),
                    dbc.Col(
                        html.Div(id="status-bar", style={"textAlign": "right", "color": COLORS["text_secondary"]}),
                        md=6,
                    ),
                ],
                className="mb-4",
                align="center",
            ),
            dbc.Tabs(
                [
                    dbc.Tab(label="Portfolio Overview", tab_id="overview"),
                    dbc.Tab(label="Per-Wallet Charts", tab_id="wallets"),
                ],
                id="tabs",
                active_tab="overview",
                className="mb-4",
            ),
            html.Div(id="overview-container", children=overview_layout()),
            html.Div(id="wallet-container", children=wallet_layout(), style={"display": "none"}),
        ],
        fluid=True,
        style={
            "minHeight": "100vh",
            "padding": "24px",
            "backgroundColor": COLORS["background"],
            "color": COLORS["text"],
            "fontFamily": FONT_FAMILY,
        },
    )


app.layout = serve_layout


clientside_callback(
    """
    function(payload, activeTab) {
        if (activeTab !== "overview" || !window.walletCuratorCharts) {
            return window.dash_clientside.no_update;
        }
        window.walletCuratorCharts.renderChart("overview-chart-container", payload);
        return String(Date.now());
    }
    """,
    Output("overview-chart-signal", "children"),
    Input("overview-chart-data", "data"),
    Input("tabs", "active_tab"),
)


clientside_callback(
    """
    function(payload, activeTab) {
        if (activeTab !== "wallets" || !window.walletCuratorCharts) {
            return window.dash_clientside.no_update;
        }
        window.walletCuratorCharts.renderChart("wallet-chart-container", payload);
        return String(Date.now());
    }
    """,
    Output("wallet-chart-signal", "children"),
    Input("wallet-chart-data", "data"),
    Input("tabs", "active_tab"),
)


@callback(
    [Output("overview-container", "style"), Output("wallet-container", "style")],
    Input("tabs", "active_tab"),
)
def render_tab(active_tab):
    if active_tab == "wallets":
        return {"display": "none"}, {"display": "block"}
    return {"display": "block"}, {"display": "none"}


@callback(
    Output("overview-range", "data"),
    [Input(f"overview-range-{label}", "n_clicks") for label in RANGES],
    State("overview-range", "data"),
    prevent_initial_call=True,
)
def set_overview_range(*args):
    current = args[-1]
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return current
    return triggered.split("-")[-1]


@callback(
    Output("wallet-range", "data"),
    [Input(f"wallet-range-{label}", "n_clicks") for label in RANGES],
    State("wallet-range", "data"),
    prevent_initial_call=True,
)
def set_wallet_range(*args):
    current = args[-1]
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return current
    return triggered.split("-")[-1]


@callback(
    Output("show-hidden", "data"),
    Input("btn-hidden", "n_clicks"),
    State("show-hidden", "data"),
    prevent_initial_call=True,
)
def toggle_hidden(_, current):
    return not current


@callback(
    Output("include-outside-range", "data"),
    Input("btn-outside-range", "n_clicks"),
    State("include-outside-range", "data"),
    prevent_initial_call=True,
)
def toggle_outside_range(_, current):
    return not current


@callback(
    [
        Output("status-bar", "children"),
        Output("overview-chart-data", "data"),
        Output("overview-current-pnl", "children"),
        Output("overview-current-pnl", "style"),
        Output("overview-range-label", "children"),
        Output("recent-changes", "children"),
    ],
    [Input("refresh-token", "data"), Input("overview-range", "data"), Input("status-poll", "n_intervals")],
)
def update_overview(_, range_key, __):
    try:
        conn = get_connection()
        summary = get_sync_status_summary(conn)
        payload = get_chart_payload(conn, wallet=None, range_key=range_key)
        sync = summary["sync"]
        latest = payload["current_delta_pnl"]
        if sync:
            last_sync = parse_db_timestamp(sync["last_sync_at"]).strftime("%Y-%m-%d %H:%M UTC")
            status = (
                f"Syncing from: {sync['current_version_folder'] or 'unknown'} | "
                f"Last sync: {last_sync} | Trades: {summary['total_trades']:,}"
            )
        else:
            status = f"Trades: {summary['total_trades']:,} | Waiting for first VPS sync"
        if READ_ONLY_UI:
            status += " | Local read-only mode"
        if summary["latest_pipeline"] and summary["latest_pipeline"]["error"]:
            status += f" | Pipeline warning: {summary['latest_pipeline']['error']}"
        changes = _build_recent_changes(get_recent_changes(conn, limit=8))
        conn.close()
        return (
            status,
            payload,
            _money(latest),
            {"fontSize": "40px", "fontWeight": "600", "color": _line_color(latest)},
            _format_chart_range_label(payload, range_key),
            changes,
        )
    except Exception as exc:
        logger.exception("Failed to load overview")
        return (
            f"Database unavailable: {exc}",
            None,
            "Database unavailable",
            {"fontSize": "32px", "fontWeight": "600", "color": COLORS["negative"]},
            "",
            _database_error_layout(str(exc)),
        )


@callback(
    [
        Output("daily-table", "data"),
        Output("daily-table", "style_data_conditional"),
        Output("daily-totals", "children"),
        Output("btn-hidden", "children"),
        Output("btn-outside-range", "children"),
    ],
    [
        Input("daily-range", "start_date"),
        Input("daily-range", "end_date"),
        Input("show-hidden", "data"),
        Input("include-outside-range", "data"),
        Input("refresh-token", "data"),
    ],
)
def update_daily_table(start_date, end_date, show_hidden, include_outside_range, _):
    try:
        conn = get_connection()
        breakdown = get_daily_breakdown(
            conn,
            start_date,
            end_date,
            include_hidden=show_hidden,
            include_outside_range=include_outside_range,
        )
        conn.close()
        style = [
            {
                "if": {"filter_query": "{realized_pnl} > 0", "column_id": "realized_pnl"},
                "color": COLORS["positive"],
            },
            {
                "if": {"filter_query": "{realized_pnl} < 0", "column_id": "realized_pnl"},
                "color": COLORS["negative"],
            },
            {
                "if": {"filter_query": "{unrealized_pnl} > 0", "column_id": "unrealized_pnl"},
                "color": COLORS["positive"],
            },
            {
                "if": {"filter_query": "{unrealized_pnl} < 0", "column_id": "unrealized_pnl"},
                "color": COLORS["negative"],
            },
            {
                "if": {"filter_query": "{total_pnl} > 0", "column_id": "total_pnl"},
                "color": COLORS["positive"],
            },
            {
                "if": {"filter_query": "{total_pnl} < 0", "column_id": "total_pnl"},
                "color": COLORS["negative"],
            },
            {
                "if": {"filter_query": "{hidden} = true"},
                "backgroundColor": "#141414",
                "color": COLORS["text_secondary"],
            },
        ]
        roster_label = "Outside-range wallets included" if include_outside_range else "In-range wallets only"
        totals_text = (
            f"Date range: {start_date} to {end_date} UTC | "
            f"Showing {len(breakdown['rows'])} wallets | {roster_label}. "
            f"Totals (excluding hidden wallets in table view): Invested {_money(breakdown['totals']['invested'])} | "
            f"Realized {_money(breakdown['totals']['realized'])} | "
            f"Unrealized {_money(breakdown['totals']['unrealized'])} | "
            f"Total {_money(breakdown['totals']['total'])}. "
            f"True total incl. hidden: {_money(breakdown['true_totals']['total'])}. "
            f"Trades and markets columns are range-scoped; the header trade count is all-time."
        )
        button_label = "Hide Hidden Wallets" if show_hidden else "Show Hidden Wallets"
        range_button_label = (
            "Hide Wallets Outside Date Range"
            if include_outside_range
            else "Include Wallets Outside Date Range"
        )
        return breakdown["rows"], style, totals_text, button_label, range_button_label
    except Exception as exc:
        logger.exception("Failed to load daily table")
        return [], [], f"Daily table unavailable: {exc}", "Show Hidden Wallets", "Include Wallets Outside Date Range"


@callback(
    Output("refresh-token", "data", allow_duplicate=True),
    Input("daily-table", "active_cell"),
    State("daily-table", "data"),
    State("refresh-token", "data"),
    prevent_initial_call=True,
)
def toggle_hidden_wallet(active_cell, rows, refresh_token):
    if READ_ONLY_UI:
        return no_update
    if not active_cell or not rows:
        return no_update
    if active_cell["column_id"] != "hide":
        return no_update
    wallet = rows[active_cell["row"]].get("wallet_address")
    if not wallet:
        return no_update

    conn = get_connection()
    existing = conn.execute(
        "SELECT 1 FROM hidden_wallets WHERE wallet_address = ?",
        (wallet,),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM hidden_wallets WHERE wallet_address = ?", (wallet,))
    else:
        conn.execute("INSERT INTO hidden_wallets (wallet_address) VALUES (?)", (wallet,))
    conn.commit()
    conn.close()
    return refresh_token + 1


@callback(
    [Output("refresh-message", "children"), Output("refresh-token", "data", allow_duplicate=True)],
    Input("btn-refresh", "n_clicks"),
    State("refresh-token", "data"),
    prevent_initial_call=True,
)
def refresh_pipeline(n_clicks, refresh_token):
    if not n_clicks:
        return no_update, no_update
    if READ_ONLY_UI:
        message = dbc.Alert("Refresh is disabled in local read-only mode.", color="secondary")
        return message, no_update
    result = run_hourly_pipeline(trigger="manual-refresh")
    if result["status"] == "ok":
        message = dbc.Alert(
            f"Pipeline complete. {result['positions_rebuilt']} positions, "
            f"{result['tokens_resolved']} newly resolved tokens.",
            color="success",
        )
    elif result["status"] == "busy":
        message = dbc.Alert(result["message"], color="warning")
    else:
        message = dbc.Alert(result.get("error", "Pipeline failed"), color="danger")
    return message, refresh_token + 1


@callback(
    [Output("wallet-dropdown", "options"), Output("wallet-dropdown", "value")],
    Input("refresh-token", "data"),
    State("wallet-dropdown", "value"),
)
def load_wallet_options(_, current_wallet):
    try:
        conn = get_connection()
        options = get_wallet_options(conn)
        conn.close()
        values = {option["value"] for option in options}
        value = current_wallet if current_wallet in values else (options[0]["value"] if options else None)
        return options, value
    except Exception:
        return [], None


@callback(
    [
        Output("wallet-chart-data", "data"),
        Output("wallet-current-pnl", "children"),
        Output("wallet-current-pnl", "style"),
        Output("wallet-range-label", "children"),
        Output("wallet-stats", "children"),
    ],
    [Input("wallet-dropdown", "value"), Input("wallet-range", "data"), Input("refresh-token", "data")],
)
def update_wallet_view(wallet, range_key, _):
    if not wallet:
        return None, "Select a wallet", {"fontSize": "28px"}, "", ""
    try:
        conn = get_connection()
        payload = get_chart_payload(conn, wallet=wallet, range_key=range_key)
        stats = get_wallet_stats(conn, wallet)
        conn.close()
        if not stats:
            return None, "No data", {"fontSize": "28px"}, "", ""

        current = payload["current_delta_pnl"]
        stat_block = html.Div(
            [
                html.Div(
                    f"Invested: {_money(stats['invested'])} | Realized: {_money(stats['realized'])} | "
                    f"Unrealized: {_money(stats['unrealized'])}",
                    className="mb-1",
                ),
                html.Div(
                    f"Filter: {stats['filter']} | Actual: {stats['game']} | Markets: {stats['markets']} | "
                    f"Trades: {stats['trades']} | Excluded positions: {stats['excluded_positions']}",
                    style={"color": COLORS["text_secondary"]},
                ),
            ],
            style={"marginTop": "12px"},
        )
        return (
            payload,
            _money(current),
            {"fontSize": "34px", "fontWeight": "600", "color": _line_color(current)},
            _format_chart_range_label(payload, range_key),
            stat_block,
        )
    except Exception as exc:
        logger.exception("Failed to load wallet view")
        return None, str(exc), {"fontSize": "28px"}, "", ""


start_scheduler()
try:
    init_db()
except Exception as exc:
    logger.warning("Initial database bootstrap failed: %s", exc)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    app.run(host="0.0.0.0", port=port, debug=False)
