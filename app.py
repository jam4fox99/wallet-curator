#!/usr/bin/env python3
"""Wallet Curator cloud dashboard."""
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load .env if present (for local dev)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, clientside_callback, dash_table, dcc, html, no_update

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
from lib.push_history import create_push_from_pending_changes, create_revert_push, get_push_detail, list_push_history
from lib.time_utils import now_utc, parse_db_timestamp
from lib.wallet_management import (
    add_wallet_from_csv_line,
    get_tier_configs,
    get_wallet_management_snapshot,
    promote_or_demote_wallet,
    remove_pending_change,
    remove_wallet,
    save_tier_config_changes,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

COLORS = {
    "background": "#090b10",
    "card": "#12161f",
    "card_alt": "#171c27",
    "surface_soft": "#0f131b",
    "text": "#f4f6fb",
    "text_secondary": "#97a3b7",
    "positive": "#2ed47a",
    "negative": "#ff5a67",
    "border": "#232a36",
    "border_soft": "#1a202b",
    "button": "#4f8cff",
}
FONT_FAMILY = '"Inter", "Segoe UI", sans-serif'
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
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap",
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


def _card(children, class_name=""):
    classes = "pm-surface"
    if class_name:
        classes = f"{classes} {class_name}"
    return html.Div(children, className=classes)


def _range_buttons(prefix):
    return html.Div(
        [html.Button(label, id=f"{prefix}-{label}", className="pm-range-pill", n_clicks=0) for label in RANGES],
        className="pm-range-pill-group",
    )


def _money(value):
    if value is None:
        return "-"
    return f"${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _pnl_cell(value, col_class=""):
    """Render a P&L value with color."""
    if value is None:
        value = 0
    color = COLORS["positive"] if value > 0 else COLORS["negative"] if value < 0 else COLORS["text"]
    return html.Td(_money(value), className=f"pm-tier-table__cell pm-num {col_class}".strip(), style={"color": color})


def _daily_breakdown_table(rows):
    """Build an HTML table matching the tier-table design for the daily breakdown."""
    if not rows:
        return html.Div("No wallet data for this range.", className="pm-empty-state__copy")

    header = html.Tr([
        _sortable_th("Hide"),
        _sortable_th("Wallet"),
        _sortable_th("Filter"),
        _sortable_th("Actual"),
        _sortable_th("Sim #"),
        _sortable_th("Invested", "number"),
        _sortable_th("Realized P&L", "number"),
        _sortable_th("Unrealized", "number"),
        _sortable_th("Total P&L", "number"),
        _sortable_th("Markets", "number"),
        _sortable_th("Trades", "number"),
        _sortable_th("In CSV"),
    ])
    colgroup = html.Colgroup([
        html.Col(className="col-hide"),
        html.Col(className="col-wallet"),
        html.Col(className="col-filter"),
        html.Col(className="col-actual"),
        html.Col(className="col-sim"),
        html.Col(className="col-invested"),
        html.Col(className="col-realized"),
        html.Col(className="col-unrealized"),
        html.Col(className="col-total-pnl"),
        html.Col(className="col-markets-daily"),
        html.Col(className="col-trades-daily"),
        html.Col(className="col-in-csv"),
    ])
    body_rows = []
    for row in rows:
        is_hidden = row.get("hidden", False)
        hide_label = "Unhide" if is_hidden else "Hide"
        row_style = {"opacity": "0.5"} if is_hidden else {}
        wallet_addr = row["wallet_address"]
        body_rows.append(html.Tr([
            html.Td(
                html.Button(
                    hide_label,
                    id=_button_id("toggle-hide", wallet=wallet_addr),
                    className="pm-button pm-button--secondary pm-button--inline",
                    n_clicks=0,
                    disabled=READ_ONLY_UI,
                ),
                className="pm-tier-table__cell",
            ),
            html.Td(
                html.Span(wallet_addr, className="pm-wallet-copyable", title="Click to copy",
                           **{"data-clipboard": wallet_addr}),
                className="pm-tier-table__cell",
            ),
            html.Td(row["filter"], className="pm-tier-table__cell"),
            html.Td(row["actual"], className="pm-tier-table__cell"),
            html.Td(row["sim"], className="pm-tier-table__cell pm-num"),
            _pnl_cell(row["invested"]),
            _pnl_cell(row["realized_pnl"]),
            _pnl_cell(row["unrealized_pnl"]),
            _pnl_cell(row["total_pnl"]),
            html.Td(str(row["markets"]), className="pm-tier-table__cell pm-num"),
            html.Td(str(row["trades"]), className="pm-tier-table__cell pm-num"),
            html.Td(row["in_csv"], className="pm-tier-table__cell pm-num"),
        ], className="pm-tier-table__row", style=row_style))

    return html.Table(
        [colgroup, html.Thead(header), html.Tbody(body_rows)],
        className="pm-tier-table",
    )


def _line_color(value):
    return COLORS["positive"] if value >= 0 else COLORS["negative"]


def _status_chip(text, tone="default"):
    class_name = "pm-status-chip"
    if tone != "default":
        class_name = f"{class_name} pm-status-chip--{tone}"
    return html.Span(text, className=class_name)


def _stat_tile(label, value, tone="default"):
    class_name = "pm-stat-tile"
    if tone != "default":
        class_name = f"{class_name} {class_name}--{tone}"
    return html.Div(
        [
            html.Div(label, className="pm-stat-tile__label"),
            html.Div(value, className="pm-stat-tile__value"),
        ],
        className=class_name,
    )


def _brand():
    return html.Div(
        [
            html.Div([html.Span(), html.Span()], className="pm-brand-mark"),
            html.Div(
                [
                    html.Div("Wallet Curator", className="pm-brand-title"),
                    html.Div("Cloud Portfolio", className="pm-brand-subtitle"),
                ],
                className="pm-brand-copy",
            ),
        ],
        className="pm-brand",
    )


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
        return f"All-time performance rebased to zero from {start_at.strftime('%b %d, %Y %H:%M')} UTC"

    return (
        f"{range_key} performance rebased to zero | {start_at.strftime('%b %d, %H:%M')} UTC "
        f"to {end_at.strftime('%b %d, %H:%M')} UTC"
    )


def _build_recent_changes(changes):
    if not changes:
        return html.Div(
            [
                html.Div("No wallet changes yet.", className="pm-empty-state__title"),
                html.Div(
                    "The change feed will populate as synced wallets move in or out of the roster.",
                    className="pm-empty-state__copy",
                ),
            ],
            className="pm-empty-state",
        )
    items = []
    for row in changes:
        label = "ADDED" if row["action"] == "ADDED" else "REMOVED"
        tone = "positive" if row["action"] == "ADDED" else "negative"
        items.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(label, className=f"pm-change-badge pm-change-badge--{tone}"),
                            html.Span(row["game_filter"] or "No filter", className="pm-change-meta"),
                        ],
                        className="pm-change-top",
                    ),
                    html.Div(row["wallet_address"], className="pm-change-wallet"),
                ],
                className="pm-change-row",
            )
        )
    return html.Div(items, className="pm-changes-list")


def _database_error_layout(message):
    return dbc.Alert(message, color="danger", className="mb-0")


def _button_id(button_type, **kwargs):
    payload = {"type": button_type}
    payload.update(kwargs)
    return payload


def _wallet_action_button(label, action, wallet, render_token, disabled=False):
    return html.Button(
        label,
        id=_button_id("wallet-action", action=action, wallet=wallet, render_token=render_token),
        className=f"pm-button pm-button--secondary pm-button--inline pm-button--{action}",
        n_clicks=0,
        disabled=disabled,
    )


def _metric_chip(label, value, tone="default"):
    class_name = "pm-metric-chip"
    if tone != "default":
        class_name = f"{class_name} {class_name}--{tone}"
    return html.Div(
        [
            html.Div(label, className="pm-metric-chip__label"),
            html.Div(value, className="pm-metric-chip__value"),
        ],
        className=class_name,
    )


def _pending_change_card(change, removable=False, render_token=None):
    details = change["details"]
    wallet = change["wallet_address"]
    game = details.get("game_filter") or details.get("snapshot", {}).get("game") or "UNKNOWN"
    snapshot = details.get("snapshot") or {}

    if change["change_type"] == "add":
        title = f"ADD {wallet}"
        subtitle = f"Added at {details['to_tier'].replace('_', ' ').title()} ({details['new_copy_pct']}%)"
    elif change["change_type"] == "remove":
        title = f"REMOVE {wallet}"
        subtitle = f"Was in {details['from_tier'].replace('_', ' ').title()} ({details['old_copy_pct']}%)"
    elif change["change_type"] == "update_tier_config":
        title = f"TIER CONFIG {wallet}"
        subtitle = f"{details['tier_name'].replace('_', ' ').title()} {details['old_copy_pct']}% → {details['new_copy_pct']}%"
    else:
        direction = "PROMOTE" if change["change_type"] == "promote" else "DEMOTE"
        subtitle = (
            f"{details['from_tier'].replace('_', ' ').title()} ({details['old_copy_pct']}%) → "
            f"{details['to_tier'].replace('_', ' ').title()} ({details['new_copy_pct']}%)"
        )
        title = f"{direction} {wallet}"

    metrics = []
    if snapshot:
        metrics.append(f"P&L {_money(snapshot.get('total_pnl_at_action', 0.0))}")
        metrics.append(f"{snapshot.get('unique_markets_at_action', 0)} markets")
        metrics.append(f"{snapshot.get('days_active_at_action', 0)} days")

    header_children = [
        html.Div(
            [
                html.Span(title, className="pm-history-row__title"),
                html.Span(game, className="pm-history-row__meta"),
            ],
            className="pm-history-row__top",
        )
    ]
    if removable:
        header_children.append(
            html.Div(
                html.Button(
                    "Remove from push",
                    id=_button_id("remove-pending-change", change_id=change["id"], render_token=render_token),
                    className="pm-button pm-button--secondary pm-button--inline pm-button--danger",
                    n_clicks=0,
                    disabled=READ_ONLY_UI,
                ),
                className="pm-history-row__actions",
            )
        )

    return html.Div(
        [
            html.Div(
                header_children,
                className="pm-history-row__header",
            ),
            html.Div(subtitle, className="pm-history-row__subtitle"),
            html.Div(" | ".join(metrics), className="pm-history-row__metrics") if metrics else None,
        ],
        className="pm-history-row",
    )


GAME_BADGES = {
    "CS2": {"icon": "🎯", "label": "CS2", "color": "#f59e0b", "bg": "rgba(245,158,11,0.12)"},
    "LOL": {"icon": "⚔️", "label": "LOL", "color": "#eab308", "bg": "rgba(234,179,8,0.12)"},
    "DOTA": {"icon": "🛡️", "label": "DOTA", "color": "#ef4444", "bg": "rgba(239,68,68,0.12)"},
    "VALO": {"icon": "💥", "label": "VALO", "color": "#ec4899", "bg": "rgba(236,72,153,0.12)"},
    "ESPORTS": {"icon": "🎮", "label": "ESPORTS", "color": "#8b5cf6", "bg": "rgba(139,92,246,0.12)"},
    "UNKNOWN": {"icon": "❓", "label": "?", "color": "#6b7280", "bg": "rgba(107,114,128,0.12)"},
}


def _game_badge(game_code):
    info = GAME_BADGES.get(game_code, GAME_BADGES["UNKNOWN"])
    return html.Span(
        f"{info['icon']}",
        className="pm-game-badge",
        title=game_code,
        style={"color": info["color"], "backgroundColor": info["bg"],
               "padding": "4px 10px", "borderRadius": "6px", "fontSize": "18px",
               "border": f"1px solid {info['color']}30"},
    )


def _sparkline_svg(pnl_points, width=120, height=28):
    """Generate inline SVG sparkline with smooth curves and gradient fill."""
    if not pnl_points or len(pnl_points) < 2:
        return html.Div("—", style={"width": f"{width}px", "color": "#555", "textAlign": "center"})

    if len(pnl_points) > 40:
        step = len(pnl_points) // 40
        pnl_points = pnl_points[::step]

    min_val = min(pnl_points)
    max_val = max(pnl_points)
    val_range = max_val - min_val if max_val != min_val else 1

    pts = []
    for i, val in enumerate(pnl_points):
        x = (i / (len(pnl_points) - 1)) * width
        y = 1 + (1 - (val - min_val) / val_range) * (height - 2)
        pts.append((x, y))

    # Build smooth cubic bezier path
    path_d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        cx = (x0 + x1) / 2
        path_d += f" C {cx:.1f},{y0:.1f} {cx:.1f},{y1:.1f} {x1:.1f},{y1:.1f}"

    # Fill path: same curve but close at bottom
    fill_d = path_d + f" L {width:.1f},{height} L 0,{height} Z"

    trending_up = pnl_points[-1] >= pnl_points[0]
    color = "#22c55e" if trending_up else "#ef4444"
    grad_id = f"g{abs(hash(tuple(pnl_points[:5]))) % 99999}"

    svg_str = (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block">'
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.03"/>'
        f'</linearGradient></defs>'
        f'<path d="{fill_d}" fill="url(#{grad_id})"/>'
        f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )
    return html.Div(
        dash.dcc.Markdown(svg_str, dangerously_allow_html=True),
        style={"width": f"{width}px", "height": f"{height}px", "lineHeight": "0", "overflow": "hidden"},
    )


def _pnl_combined_cell(total_pnl, since_promo):
    """Rich P&L cell with all-time + since promo subtitle."""
    if total_pnl is None:
        total_pnl = 0
    if total_pnl == 0:
        text = "$0.00"
        color = "#e5e5e5"
    elif total_pnl > 0:
        text = f"${total_pnl:,.2f}"
        color = "#22c55e"
    else:
        text = f"-${abs(total_pnl):,.2f}"
        color = "#ef4444"
    main = html.Div(text, className="pm-pnl-main", style={"color": color})
    if since_promo is not None and since_promo != 0:
        if since_promo > 0:
            sp_text = f"${since_promo:,.2f}"
            sp_color = "#22c55e80"
        else:
            sp_text = f"-${abs(since_promo):,.2f}"
            sp_color = "#ef444480"
        sub = html.Div(f"{sp_text} Since Promo", className="pm-pnl-sub", style={"color": sp_color})
    elif since_promo == 0:
        sub = html.Div("$0.00 Since Promo", className="pm-pnl-sub", style={"color": "#e5e5e580"})
    else:
        sub = html.Div("")
    return html.Td([main, sub], className="pm-tier-table__cell pm-pnl-combined")


def _sortable_th(label, sort_type="text"):
    props = {"data-sortable": "true", "data-sort-dir": "none"}
    if sort_type == "number":
        props["data-sort-type"] = "number"
    return html.Th(label, className="pm-tier-table__th", **props)


def _render_wallet_row(wallet, tier_name, render_token):
    """Render a premium wallet row."""
    addr = wallet["wallet_address"]

    # Action buttons
    actions = []
    if tier_name != "high_conviction":
        actions.append(_wallet_action_button("▲ Promote", "promote", addr, render_token, disabled=READ_ONLY_UI))
    if tier_name != "test":
        actions.append(_wallet_action_button("▼ Demote", "demote", addr, render_token, disabled=READ_ONLY_UI))
    actions.append(html.Span(style={"width": "4px"}))
    actions.append(_wallet_action_button("✕ Remove", "remove", addr, render_token, disabled=READ_ONLY_UI))

    return html.Tr([
        html.Td(
            html.Span(addr, className="pm-wallet-copyable", title="Click to copy",
                       **{"data-clipboard": addr}),
            className="pm-tier-table__cell",
        ),
        html.Td(wallet["game_filter"], className="pm-tier-table__cell"),
        _pnl_combined_cell(wallet["total_pnl"], wallet.get("since_promo_pnl")),
        html.Td(str(wallet["unique_markets"]), className="pm-tier-table__cell pm-num"),
        html.Td(str(wallet["days_in_tier"]), className="pm-tier-table__cell pm-num"),
        html.Td(str(wallet["total_trades"]), className="pm-tier-table__cell pm-num"),
        html.Td(
            html.Div(actions, className="pm-action-row-compact"),
            className="pm-tier-table__cell",
        ),
    ], className="pm-tier-table__row")


def _tier_table(wallets, tier_name, render_token):
    header = html.Tr([
        _sortable_th("Wallet"),
        _sortable_th("Primary Game"),
        _sortable_th("All-Time P&L", "number"),
        _sortable_th("Markets", "number"),
        _sortable_th("Days In Tier", "number"),
        _sortable_th("Trades", "number"),
        html.Th("Actions", className="pm-tier-table__th"),
    ])
    rows = [
        _render_wallet_row(w, tier_name, render_token)
        for w in wallets
    ]
    colgroup = html.Colgroup([
        html.Col(className="col-wallet"),
        html.Col(className="col-game"),
        html.Col(className="col-pnl"),
        html.Col(className="col-markets"),
        html.Col(className="col-days"),
        html.Col(className="col-trades"),
        html.Col(className="col-actions"),
    ])
    return html.Table(
        [colgroup, html.Thead(header), html.Tbody(rows)],
        className="pm-tier-table",
    )


def _render_removed_section(removed_wallets):
    header = html.Tr([
        _sortable_th("Wallet"),
        _sortable_th("Game"),
        _sortable_th("Was In Tier"),
        _sortable_th("P&L At Removal", "number"),
        _sortable_th("Trades", "number"),
        _sortable_th("Removed At"),
    ])
    rows = []
    for w in removed_wallets:
        color = "#22c55e" if w["total_pnl"] > 0 else "#ef4444" if w["total_pnl"] < 0 else "#e5e5e5"
        rows.append(html.Tr([
            html.Td(html.Span(f"0x...{w['wallet_address'][-4:]}", title=w["wallet_address"], className="pm-wallet-short"),
                     className="pm-tier-table__cell"),
            html.Td(w["game_filter"], className="pm-tier-table__cell"),
            html.Td((w["from_tier"] or "").replace("_", " ").title(), className="pm-tier-table__cell"),
            html.Td(f"${w['total_pnl']:,.2f}", style={"color": color}, className="pm-tier-table__cell"),
            html.Td(str(w["total_trades"]), className="pm-tier-table__cell pm-num"),
            html.Td(w["removed_at"], className="pm-tier-table__cell"),
        ], className="pm-tier-table__row"))

    table = html.Table([html.Thead(header), html.Tbody(rows)], className="pm-tier-table") if rows else html.Div("No removed wallets.", className="pm-empty-state__copy")

    return _card([
        html.Div([
            html.Div([
                html.Div("History", className="pm-kicker"),
                html.H3("Removed", className="pm-section-title"),
            ], className="pm-card-title-block"),
            html.Div(f"{len(removed_wallets)} wallets", className="pm-section-side-note"),
        ], className="pm-card-head pm-card-head--tight"),
        table,
    ], class_name="pm-admin-card")


def _render_management_sections(snapshot):
    render_token = snapshot.get("render_token", "stable")
    sections = []
    for tier in snapshot["tiers"]:
        wallets = tier["wallets"]
        if wallets:
            body = _tier_table(wallets, tier["tier_name"], render_token)
        else:
            body = html.Div("No wallets in this tier.", className="pm-empty-state__copy")

        # Tier header — collapsible
        tier_id = tier["tier_name"]
        tier_header = html.Details([
            html.Summary(
                html.Div([
                    html.Span(f"{tier['display_name']} ({tier['copy_percentage']}% copy)", className="pm-tier-label-text"),
                    html.Span(f"{len(wallets)} wallets", className="pm-tier-meta-inline"),
                ], className="pm-tier-header-inner"),
                className="pm-tier-summary",
            ),
            body,
        ], open=True, className="pm-tier-collapsible")

        sections.append(html.Div([tier_header], className="pm-tier-section"))

    removed = snapshot.get("removed_wallets", [])
    if removed:
        sections.append(_render_removed_section(removed))

    return sections


def _render_push_list(pushes):
    if not pushes:
        return html.Div("No CSV push history yet.", className="pm-empty-state__copy")
    rows = []
    for push in pushes:
        status_label = {
            "pending": "Pending VPS apply",
            "applied": "Applied",
            "reverted": "Reverted by later push",
        }.get(push["status"], push["status"])
        change_count = push.get("change_count", "")
        summary_pills = []
        if change_count:
            summary_pills.append(html.Span(f"{change_count} changes"))
        summary_pills.append(html.Span(status_label))
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(f"Push #{push['id']}", className="pm-history-list__title"),
                            html.Div(push["summary"], className="pm-history-list__summary"),
                            html.Div(summary_pills, className="pm-summary-strip"),
                        ],
                        className="pm-history-list__meta",
                    ),
                    html.Button(
                        "View Details",
                        id=_button_id("view-push", push_id=push["id"]),
                        className="pm-button pm-button--secondary pm-button--inline",
                        n_clicks=0,
                    ),
                ],
                className="pm-history-list__row",
            )
        )
    return html.Div(rows, className="pm-history-list")


def _render_push_detail(detail):
    if not detail:
        return html.Div("Select a push to inspect its changes and revert options.", className="pm-empty-state__copy")

    change_rows = [_pending_change_card(change) for change in detail["changes"]]
    pushed_at = parse_db_timestamp(detail["pushed_at"]) if detail["pushed_at"] else None
    applied_at = parse_db_timestamp(detail["applied_at"]) if detail["applied_at"] else None
    pushed_label = pushed_at.strftime("%Y-%m-%d %H:%M UTC") if pushed_at else "Unknown"
    applied_label = applied_at.strftime("%Y-%m-%d %H:%M UTC") if applied_at else "Waiting for VPS"

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(f"Push #{detail['id']}", className="pm-section-title pm-section-title--blue"),
                            html.Div(f"Pushed {pushed_label}", className="pm-range-copy"),
                            html.Div(f"Applied {applied_label}", className="pm-range-copy"),
                        ],
                        className="pm-card-title-block",
                    ),
                    html.Button(
                        "Revert",
                        id="btn-open-revert",
                        className="pm-button pm-button--danger",
                        n_clicks=0,
                        disabled=READ_ONLY_UI or detail["status"] == "pending",
                    ),
                ],
                className="pm-card-head pm-card-head--tight",
            ),
            html.Div(detail["summary"], className="pm-inline-message"),
            html.Details(
                [
                    html.Summary(
                        html.Div([
                            html.Span(f"Changes ({len(detail['changes'])})", className="pm-collapsible-label"),
                        ], className="pm-collapsible-header-inner"),
                        className="pm-tier-summary",
                    ),
                    html.Div(change_rows, className="pm-history-detail-list", style={"padding": "12px"}),
                ],
                open=True,
                className="pm-collapsible-section",
            ),
        ],
        className="pm-history-detail",
    )


def overview_layout():
    today = now_utc().date()
    week_ago = today - timedelta(days=6)
    return html.Div(
        className="pm-page-stack",
        children=[
            html.Div(
                className="pm-overview-grid",
                children=[
                    _card(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div("Portfolio", className="pm-kicker"),
                                            html.H2("Overview", className="pm-section-title pm-section-title--blue"),
                                        ],
                                        className="pm-card-title-block",
                                    ),
                                    _range_buttons("overview-range"),
                                ],
                                className="pm-card-head",
                            ),
                            html.Div("Selected Range P&L", className="pm-metric-label"),
                            html.Div(id="overview-current-pnl", className="pm-metric-value"),
                            html.Div(id="overview-range-label", className="pm-range-copy"),
                            html.Div(_chart_mount("overview-chart-container"), className="pm-chart-shell"),
                        ],
                        class_name="pm-overview-hero",
                    ),
                    html.Div(
                        className="pm-side-rail",
                        children=[
                            _card(
                                [
                                    html.Div(
                                        [
                                            html.Div("Workspace", className="pm-kicker"),
                                            html.H3("Controls & Feed", className="pm-side-title"),
                                        ],
                                        className="pm-card-title-block",
                                    ),
                                    html.Div(
                                        [
                                            html.Button(
                                                "Refresh P&L",
                                                id="btn-refresh",
                                                className="pm-button pm-button--primary",
                                                n_clicks=0,
                                            ),
                                            html.Button(
                                                "Show Hidden Wallets",
                                                id="btn-hidden",
                                                className="pm-button pm-button--secondary",
                                                n_clicks=0,
                                            ),
                                        ],
                                        className="pm-action-row",
                                    ),
                                    dbc.Alert(
                                        "Read-only local UI mode is enabled. Refresh P&L and hide/unhide writes are disabled.",
                                        color="secondary",
                                        className="pm-readonly-alert",
                                        style={"display": "block" if READ_ONLY_UI else "none"},
                                    ),
                                    html.Div(id="refresh-message", className="pm-inline-message"),
                                    html.Div("Recent changes", className="pm-side-section-title"),
                                    html.Div(id="recent-changes"),
                                ],
                                class_name="pm-side-card",
                            )
                        ],
                    ),
                ],
            ),
            _card(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Holdings", className="pm-kicker"),
                                    html.H3("Daily Breakdown", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "Include Wallets Outside Date Range",
                                        id="btn-outside-range",
                                        className="pm-button pm-button--secondary",
                                        n_clicks=0,
                                    ),
                                    html.Div(
                                        [
                                            dcc.Input(
                                                id="daily-range-start",
                                                type="date",
                                                value=week_ago.isoformat(),
                                                className="pm-date-input",
                                            ),
                                            html.Span("\u2192", className="pm-date-range-arrow"),
                                            dcc.Input(
                                                id="daily-range-end",
                                                type="date",
                                                value=today.isoformat(),
                                                className="pm-date-input",
                                            ),
                                        ],
                                        className="pm-date-range",
                                    ),
                                ],
                                className="pm-breakdown-controls",
                            ),
                        ],
                        className="pm-card-head pm-card-head--tight",
                    ),
                    html.Div(id="daily-totals", className="pm-breakdown-summary"),
                    html.Details(
                        [
                            html.Summary(
                                html.Div([
                                    html.Span("Wallet Breakdown Table", className="pm-collapsible-label"),
                                    html.Span(id="daily-table-count", className="pm-collapsible-meta"),
                                ], className="pm-collapsible-header-inner"),
                                className="pm-tier-summary",
                            ),
                            html.Div(
                                id="daily-table",
                                className="pm-daily-table-shell",
                            ),
                        ],
                        open=True,
                        className="pm-collapsible-section",
                    ),
                ],
                class_name="pm-breakdown-card",
            ),
        ],
    )


def wallet_layout():
    return html.Div(
        className="pm-page-stack",
        children=[
            html.Div(
                className="pm-wallet-grid",
                children=[
                    _card(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div("Wallets", className="pm-kicker"),
                                            html.H2("Per-Wallet Performance", className="pm-section-title pm-section-title--blue"),
                                        ],
                                        className="pm-card-title-block",
                                    ),
                                    _range_buttons("wallet-range"),
                                ],
                                className="pm-card-head",
                            ),
                            html.Div(
                                [
                                    html.Div("Wallet", className="pm-field-label"),
                                    html.Div(
                                        dcc.Dropdown(id="wallet-dropdown", placeholder="Select wallet..."),
                                        className="pm-wallet-dropdown",
                                    ),
                                ],
                                className="pm-wallet-picker-block",
                            ),
                            html.Div("Selected Range P&L", className="pm-metric-label"),
                            html.Div(id="wallet-current-pnl", className="pm-metric-value pm-metric-value--wallet"),
                            html.Div(id="wallet-range-label", className="pm-range-copy"),
                            html.Div(_chart_mount("wallet-chart-container"), className="pm-chart-shell"),
                        ],
                        class_name="pm-wallet-hero",
                    ),
                    _card(
                        [
                            html.Div(
                                [
                                    html.Div("Wallet Summary", className="pm-kicker"),
                                    html.H3("Position Context", className="pm-side-title"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(id="wallet-stats"),
                        ],
                        class_name="pm-wallet-side",
                    ),
                ],
            )
        ],
    )


def wallet_management_layout():
    return html.Div(
        className="pm-page-stack",
        children=[
            _card(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Control Plane", className="pm-kicker"),
                                    html.H2("Wallet Management", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "Add Wallet ➕",
                                        id="btn-open-add-wallet",
                                        className="pm-button pm-button--secondary",
                                        n_clicks=0,
                                        disabled=READ_ONLY_UI,
                                    ),
                                    html.Button(
                                        "Export XLSX 📊",
                                        id="btn-export-xlsx",
                                        className="pm-button pm-button--secondary",
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "Export Wallets 📋",
                                        id="btn-export-txt",
                                        className="pm-button pm-button--secondary",
                                        n_clicks=0,
                                    ),
                                    html.Button(
                                        "Push to VPS 🚀",
                                        id="btn-open-push",
                                        className="pm-button pm-button--primary",
                                        n_clicks=0,
                                        disabled=READ_ONLY_UI,
                                    ),
                                ],
                                className="pm-action-row",
                            ),
                        ],
                        className="pm-card-head",
                    ),
                    dbc.Alert(
                        "Local read-only UI mode is enabled. Wallet management writes are disabled.",
                        color="secondary",
                        className="pm-readonly-alert",
                        style={"display": "block" if READ_ONLY_UI else "none"},
                    ),
                    html.Div(id="wallet-management-flash", className="pm-inline-message"),
                    html.Div(id="wallet-management-banner"),
                    html.Div(id="wallet-management-pending"),
                ],
                class_name="pm-admin-card",
            ),
            html.Div(id="wallet-management-sections", className="pm-page-stack"),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Add Wallets")),
                    dbc.ModalBody(
                        [
                            html.Div("Paste Sharp CSV lines (one wallet per line)", className="pm-field-label"),
                            dcc.Textarea(
                                id="add-wallet-line",
                                className="pm-textarea",
                                placeholder="0xabc123...,true,true,true,200,...\n0xdef456...,true,true,true,200,...",
                                style={"width": "100%", "minHeight": "160px"},
                            ),
                            html.Div("Assign tier", className="pm-field-label"),
                            dcc.Dropdown(
                                id="add-wallet-tier",
                                options=[
                                    {"label": "Test", "value": "test"},
                                    {"label": "Promoted", "value": "promoted"},
                                    {"label": "High Conviction", "value": "high_conviction"},
                                ],
                                value="test",
                                clearable=False,
                                className="pm-wallet-dropdown",
                            ),
                            html.Div(id="add-wallet-message", className="pm-inline-message"),
                        ]
                    ),
                    dbc.ModalFooter(
                        [
                            html.Button("Cancel", id="btn-cancel-add-wallet", className="pm-button pm-button--secondary", n_clicks=0),
                            html.Button("Queue Wallets", id="btn-submit-add-wallet", className="pm-button pm-button--primary", n_clicks=0),
                        ]
                    ),
                ],
                id="add-wallet-modal",
                is_open=False,
                size="lg",
            ),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Push Changes to VPS")),
                    dbc.ModalBody(html.Div(id="push-preview-body")),
                    dbc.ModalFooter(
                        [
                            html.Button("Cancel", id="btn-cancel-push", className="pm-button pm-button--secondary", n_clicks=0),
                            html.Button("Confirm & Push", id="btn-confirm-push", className="pm-button pm-button--primary", n_clicks=0),
                        ]
                    ),
                ],
                id="push-preview-modal",
                is_open=False,
                size="lg",
            ),
        ],
    )


def subcategory_charts_layout():
    RANGE_OPTIONS = [
        {"label": "1D", "value": 1},
        {"label": "7D", "value": 7},
        {"label": "2W", "value": 14},
        {"label": "30D", "value": 30},
        {"label": "ALL", "value": 365},
    ]
    return html.Div(
        className="pm-page-stack",
        children=[
            _card(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Subcategory Charts", className="pm-kicker"),
                                    html.H2("Wallet Performance by Category", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                        ],
                        className="pm-card-head",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Wallet Address", className="pm-field-label"),
                                    dcc.Input(
                                        id="sc-wallet-input",
                                        type="text",
                                        placeholder="0x...",
                                        className="pm-text-input",
                                        style={"width": "100%", "padding": "8px 12px", "fontSize": "13px",
                                               "background": "var(--pm-surface-alt)", "border": "1px solid var(--pm-border)",
                                               "borderRadius": "8px", "color": "var(--pm-text)", "fontFamily": "monospace"},
                                    ),
                                ],
                                style={"flex": "2"},
                            ),
                            html.Div(
                                [
                                    html.Div("Category", className="pm-field-label"),
                                    dcc.Dropdown(
                                        id="sc-game-dropdown",
                                        placeholder="Select category...",
                                        searchable=True,
                                        className="pm-wallet-dropdown",
                                    ),
                                ],
                                style={"flex": "1"},
                            ),
                        ],
                        style={"display": "flex", "gap": "16px", "marginBottom": "12px"},
                    ),
                    dcc.Store(id="sc-active-range", data=365),
                    html.Div(
                        [
                            html.Button(
                                opt["label"],
                                id=f"sc-range-{opt['value']}",
                                className="pm-range-pill" + (" pm-range-pill--active" if opt["value"] == 365 else ""),
                                n_clicks=0,
                            )
                            for opt in RANGE_OPTIONS
                        ],
                        id="sc-range-pills",
                        style={"display": "flex", "gap": "6px", "marginBottom": "16px"},
                    ),
                    html.Button("Generate Chart", id="sc-generate", className="pm-button pm-button--primary", n_clicks=0),
                    html.Div(id="sc-message", className="pm-inline-message", style={"marginTop": "12px"}),
                ],
                class_name="pm-admin-card",
            ),
            html.Div(id="sc-summary", style={"marginTop": "16px"}),
            html.Div(
                dcc.Graph(id="sc-chart", style={"height": "500px"}, config={"displayModeBar": False}),
                id="sc-chart-container",
                style={"display": "none", "marginTop": "16px"},
            ),
        ],
    )


def wallet_curation_layout():
    RANGE_OPTIONS = [
        {"label": "1D", "value": 1},
        {"label": "7D", "value": 7},
        {"label": "2W", "value": 14},
        {"label": "30D", "value": 30},
        {"label": "ALL", "value": 365},
    ]
    return html.Div(
        className="pm-page-stack",
        children=[
            # Stores
            dcc.Store(id="cur-wallets", data=[]),
            dcc.Store(id="cur-index", data=0),
            dcc.Store(id="cur-approved", data=[]),
            dcc.Store(id="cur-decisions", data={}),
            dcc.Store(id="cur-filter", data=""),
            dcc.Store(id="cur-range", data=365),
            dcc.Store(id="cur-cache", data={}),
            dcc.Store(id="cur-phase", data="setup"),

            # Setup screen
            html.Div(
                id="cur-setup",
                children=[
                    _card([
                        html.Div([
                            html.Div("Wallet Curation", className="pm-kicker"),
                            html.H2("Swipe Review", className="pm-section-title pm-section-title--blue"),
                        ], className="pm-card-title-block"),
                        html.Div("Paste wallet addresses (one per line)", className="pm-field-label"),
                        dcc.Textarea(
                            id="cur-wallet-input",
                            placeholder="0xabc123...\n0xdef456...\n0x789...",
                            style={"width": "100%", "minHeight": "120px", "padding": "8px 12px",
                                   "fontSize": "13px", "background": "var(--pm-surface-alt)",
                                   "border": "1px solid var(--pm-border)", "borderRadius": "8px",
                                   "color": "var(--pm-text)", "fontFamily": "monospace"},
                        ),
                        html.Div([
                            html.Div([
                                html.Div("Category", className="pm-field-label"),
                                dcc.Dropdown(id="cur-category", placeholder="Select category...",
                                             searchable=True, className="pm-wallet-dropdown"),
                            ], style={"flex": "1"}),
                        ], style={"display": "flex", "gap": "16px", "margin": "12px 0"}),
                        html.Div([
                            html.Button(opt["label"], id=f"cur-setup-range-{opt['value']}",
                                        className="pm-range-pill" + (" pm-range-pill--active" if opt["value"] == 365 else ""),
                                        n_clicks=0)
                            for opt in RANGE_OPTIONS
                        ], style={"display": "flex", "gap": "6px", "marginBottom": "16px"}),
                        html.Button("Start Review", id="cur-start", className="pm-button pm-button--primary", n_clicks=0),
                        html.Div(id="cur-setup-msg", style={"marginTop": "8px"}),
                    ], class_name="pm-admin-card"),
                ],
            ),

            # Swipe screen
            html.Div(
                id="cur-swipe",
                style={"display": "none"},
                children=[
                    html.Div(id="cur-progress", style={"marginBottom": "12px", "color": "var(--pm-text-secondary)", "fontSize": "13px"}),
                    html.Div(id="cur-wallet-header", style={"marginBottom": "8px"}),
                    dcc.Loading(
                        html.Div([
                            dcc.Graph(id="cur-chart", style={"height": "400px"}, config={"displayModeBar": False}),
                            html.Div(id="cur-stats"),
                            html.Div(id="cur-concentration", style={"marginTop": "12px"}),
                            html.Div(id="cur-top-markets", style={"marginTop": "12px"}),
                        ]),
                        type="default",
                    ),
                    html.Div([
                        html.Button("← Back", id="cur-back", className="pm-button pm-button--secondary", n_clicks=0),
                        html.Button("✗ Skip", id="cur-skip", className="pm-button pm-button--secondary",
                                    style={"color": "#ef4444", "borderColor": "#ef444440"}, n_clicks=0),
                        html.Button("✓ Approve", id="cur-approve", className="pm-button pm-button--primary",
                                    style={"background": "#22c55e", "borderColor": "#22c55e"}, n_clicks=0),
                    ], style={"display": "flex", "gap": "12px", "justifyContent": "center", "marginTop": "20px"}),
                ],
            ),

            # Results screen
            html.Div(
                id="cur-results",
                style={"display": "none"},
                children=[
                    _card([
                        html.Div([
                            html.Div("Review Complete", className="pm-kicker"),
                            html.H2(id="cur-results-title", className="pm-section-title pm-section-title--blue"),
                        ], className="pm-card-title-block"),
                        html.Div(id="cur-results-list", style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "13px"}),
                        html.Div([
                            dcc.Download(id="cur-download"),
                            html.Button("Download Approved List", id="cur-download-btn",
                                        className="pm-button pm-button--primary", n_clicks=0),
                            html.Button("Start New Batch", id="cur-new-batch",
                                        className="pm-button pm-button--secondary", n_clicks=0),
                        ], style={"display": "flex", "gap": "12px", "marginTop": "16px"}),
                    ], class_name="pm-admin-card"),
                ],
            ),
        ],
    )


def settings_layout():
    return html.Div(
        className="pm-page-stack",
        children=[
            _card(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Config", className="pm-kicker"),
                                    html.H2("Tier Settings", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Button(
                                "Save Changes",
                                id="btn-save-tier-settings",
                                className="pm-button pm-button--primary",
                                n_clicks=0,
                                disabled=READ_ONLY_UI,
                            ),
                        ],
                        className="pm-card-head",
                    ),
                    dbc.Alert(
                        "Local read-only UI mode is enabled. Tier config writes are disabled.",
                        color="secondary",
                        className="pm-readonly-alert",
                        style={"display": "block" if READ_ONLY_UI else "none"},
                    ),
                    html.Div(id="settings-message", className="pm-inline-message"),
                    html.Details(
                        [
                            html.Summary(
                                html.Div([
                                    html.Span("Tier Copy Percentages", className="pm-collapsible-label"),
                                    html.Span("3 tiers configured", className="pm-collapsible-meta"),
                                ], className="pm-collapsible-header-inner"),
                                className="pm-tier-summary",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div("Test", className="pm-settings-row__name"),
                                            dcc.Input(id="settings-copy-test", type="number", min=0, step=0.1, className="pm-settings-input"),
                                            html.Div(id="settings-count-test", className="pm-settings-row__count"),
                                        ],
                                        className="pm-settings-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Div("Promoted", className="pm-settings-row__name"),
                                            dcc.Input(id="settings-copy-promoted", type="number", min=0, step=0.1, className="pm-settings-input"),
                                            html.Div(id="settings-count-promoted", className="pm-settings-row__count"),
                                        ],
                                        className="pm-settings-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Div("High Conviction", className="pm-settings-row__name"),
                                            dcc.Input(id="settings-copy-high", type="number", min=0, step=0.1, className="pm-settings-input"),
                                            html.Div(id="settings-count-high", className="pm-settings-row__count"),
                                        ],
                                        className="pm-settings-row",
                                    ),
                                ],
                                className="pm-settings-grid",
                                style={"padding": "12px"},
                            ),
                        ],
                        open=True,
                        className="pm-collapsible-section",
                    ),
                    html.Div(
                        "Changing a tier's copy percentage queues per-wallet CSV updates. Use Push to VPS to apply them live.",
                        className="pm-range-copy",
                    ),
                ],
                class_name="pm-admin-card",
            )
        ],
    )


def changes_layout():
    return html.Div(
        className="pm-page-stack",
        children=[
            html.Div(
                className="pm-changes-grid",
                children=[
                    _card(
                        [
                            html.Div(
                                [
                                    html.Div("History", className="pm-kicker"),
                                    html.H2("CSV Change History", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(id="changes-list"),
                        ],
                        class_name="pm-admin-card",
                    ),
                    _card(
                        [
                            html.Div(id="changes-detail"),
                            html.Div(id="changes-message", className="pm-inline-message"),
                        ],
                        class_name="pm-admin-card",
                    ),
                ],
            ),
            dbc.Modal(
                [
                    dbc.ModalHeader(dbc.ModalTitle("Revert CSV Push")),
                    dbc.ModalBody(html.Div(id="revert-preview-body")),
                    dbc.ModalFooter(
                        [
                            html.Button("Cancel", id="btn-cancel-revert", className="pm-button pm-button--secondary", n_clicks=0),
                            html.Button("Confirm Revert", id="btn-confirm-revert", className="pm-button pm-button--primary", n_clicks=0),
                        ]
                    ),
                ],
                id="revert-modal",
                is_open=False,
                size="lg",
            ),
        ],
    )


def serve_layout():
    return html.Div(
        className="pm-app-shell",
        children=[
            dcc.Location(id="url", refresh=False),
            dcc.Store(id="refresh-token", data=0),
            dcc.Store(id="wallet-admin-token", data=0),
            dcc.Store(id="overview-range", data="ALL"),
            dcc.Store(id="wallet-range", data="ALL"),
            dcc.Store(id="show-hidden", data=False),
            dcc.Store(id="include-outside-range", data=False),
            dcc.Store(id="selected-push-id"),
            dcc.Download(id="download-xlsx"),
            dcc.Download(id="download-txt"),
            dcc.Store(id="overview-chart-data"),
            dcc.Store(id="wallet-chart-data"),
            dcc.Interval(id="status-poll", interval=60_000, n_intervals=0),
            html.Div(id="overview-chart-signal", style={"display": "none"}),
            html.Div(id="wallet-chart-signal", style={"display": "none"}),
            html.Header(
                className="pm-topbar",
                children=[
                    html.Div(
                        [
                            _brand(),
                            html.Div(
                                [
                                    html.Div("Live Cloud Portfolio", className="pm-header-title"),
                                    html.Div(
                                        "Railway-hosted dashboard with VPS trade sync and live wallet performance.",
                                        className="pm-header-copy",
                                    ),
                                ],
                                className="pm-header-center",
                            ),
                            html.Div(id="status-bar", className="pm-status-bar"),
                        ],
                        className="pm-topbar-inner",
                    )
                ],
            ),
            html.Div(
                className="pm-tab-rail",
                children=[
                    dcc.Tabs(
                        id="tabs",
                        value="overview",
                        parent_className="pm-tabs-parent",
                        className="pm-tabs-shell",
                        children=[
                            dcc.Tab(
                                label="Wallet Management",
                                value="wallet-management",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Portfolio Overview",
                                value="overview",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Per-Wallet Charts",
                                value="wallets",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Changes",
                                value="changes",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Wallet Curation",
                                value="wallet-curation",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Subcategory Charts",
                                value="subcategory-charts",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Settings",
                                value="settings",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                        ],
                    )
                ],
            ),
            html.Main(
                className="pm-main-shell",
                children=[
                    html.Div(
                        className="pm-main-column",
                        children=[
                            html.Div(id="overview-container", children=overview_layout()),
                            html.Div(id="wallet-container", children=wallet_layout(), style={"display": "none"}),
                            html.Div(id="wallet-management-container", children=wallet_management_layout(), style={"display": "none"}),
                            html.Div(id="wallet-curation-container", children=wallet_curation_layout(), style={"display": "none"}),
                            html.Div(id="subcategory-charts-container", children=subcategory_charts_layout(), style={"display": "none"}),
                            html.Div(id="settings-container", children=settings_layout(), style={"display": "none"}),
                            html.Div(id="changes-container", children=changes_layout(), style={"display": "none"}),
                        ],
                    )
                ],
            ),
        ],
        style={
            "minHeight": "100vh",
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
    Input("tabs", "value"),
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
    Input("tabs", "value"),
)


TAB_PATHS = {
    "overview": "/",
    "wallets": "/wallets",
    "wallet-management": "/wallet-management",
    "wallet-curation": "/wallet-curation",
    "subcategory-charts": "/subcategory-charts",
    "settings": "/settings",
    "changes": "/changes",
}
PATH_TO_TAB = {v: k for k, v in TAB_PATHS.items()}

_HIDE = {"display": "none"}
_SHOW = {"display": "block"}
_TAB_ORDER = ["overview", "wallets", "wallet-management", "wallet-curation", "subcategory-charts", "settings", "changes"]
_CONTAINER_IDS = [f"{t.replace('subcategory-charts', 'subcategory-charts')}-container" for t in _TAB_ORDER]


@callback(
    [
        Output("overview-container", "style"),
        Output("wallet-container", "style"),
        Output("wallet-management-container", "style"),
        Output("wallet-curation-container", "style"),
        Output("subcategory-charts-container", "style"),
        Output("settings-container", "style"),
        Output("changes-container", "style"),
    ],
    Input("tabs", "value"),
)
def render_tab(active_tab):
    return [_SHOW if tab == active_tab else _HIDE for tab in _TAB_ORDER]


# Sync URL → tab on page load
@callback(
    Output("tabs", "value"),
    Input("url", "pathname"),
    prevent_initial_call=False,
)
def url_to_tab(pathname):
    normalized = pathname or "/"
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return PATH_TO_TAB.get(normalized, "overview")


# Sync tab → URL when user clicks a tab
@callback(
    Output("url", "pathname"),
    Input("tabs", "value"),
    prevent_initial_call=True,
)
def tab_to_url(tab_value):
    return TAB_PATHS.get(tab_value, "/")


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
    [Output(f"overview-range-{label}", "className") for label in RANGES],
    Input("overview-range", "data"),
)
def style_overview_range_buttons(active_range):
    return [
        "pm-range-pill pm-range-pill--active" if label == active_range else "pm-range-pill"
        for label in RANGES
    ]


@callback(
    [Output(f"wallet-range-{label}", "className") for label in RANGES],
    Input("wallet-range", "data"),
)
def style_wallet_range_buttons(active_range):
    return [
        "pm-range-pill pm-range-pill--active" if label == active_range else "pm-range-pill"
        for label in RANGES
    ]


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
        status_chips = []
        if sync:
            last_sync_at = parse_db_timestamp(sync["last_sync_at"])
            last_sync = last_sync_at.strftime("%Y-%m-%d %H:%M UTC") if last_sync_at else "Unknown"
            status_chips.extend(
                [
                    _status_chip(f"Sync source {sync['current_version_folder'] or 'unknown'}"),
                    _status_chip(f"Last sync {last_sync}"),
                    _status_chip(f"{summary['total_trades']:,} trades"),
                ]
            )
        else:
            status_chips.extend(
                [
                    _status_chip("Waiting for first VPS sync", tone="warning"),
                    _status_chip(f"{summary['total_trades']:,} trades"),
                ]
            )
        if READ_ONLY_UI:
            status_chips.append(_status_chip("Local read-only", tone="info"))
        latest_pipeline = summary["latest_pipeline"]
        if latest_pipeline and latest_pipeline["error"]:
            status_chips.append(_status_chip(f"Pipeline warning {latest_pipeline['error']}", tone="danger"))
        elif latest_pipeline:
            status_chips.append(_status_chip("Pipeline healthy", tone="success"))
        changes = _build_recent_changes(get_recent_changes(conn, limit=8))
        conn.close()
        return (
            html.Div(status_chips, className="pm-status-chip-row"),
            payload,
            _money(latest),
            {"color": _line_color(latest)},
            _format_chart_range_label(payload, range_key),
            changes,
        )
    except Exception as exc:
        logger.exception("Failed to load overview")
        return (
            html.Div(
                [_status_chip("Database unavailable", tone="danger"), html.Span(str(exc), className="pm-status-error")],
                className="pm-status-chip-row",
            ),
            None,
            "Database unavailable",
            {"color": COLORS["negative"]},
            "",
            _database_error_layout(str(exc)),
        )


@callback(
    [
        Output("daily-table", "children"),
        Output("daily-table-count", "children"),
        Output("daily-totals", "children"),
        Output("btn-hidden", "children"),
        Output("btn-outside-range", "children"),
    ],
    [
        Input("daily-range-start", "value"),
        Input("daily-range-end", "value"),
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
        table = _daily_breakdown_table(breakdown["rows"])
        count_label = f"{len(breakdown['rows'])} wallets"
        roster_label = "Outside-range wallets included" if include_outside_range else "In-range wallets only"
        totals_text = html.Div(
            [
                html.Span(f"Date range {start_date} to {end_date} UTC"),
                html.Span(f"Showing {len(breakdown['rows'])} wallets"),
                html.Span(roster_label),
                html.Span(f"Invested {_money(breakdown['totals']['invested'])}"),
                html.Span(f"Realized {_money(breakdown['totals']['realized'])}"),
                html.Span(f"Unrealized {_money(breakdown['totals']['unrealized'])}"),
                html.Span(f"Table total {_money(breakdown['totals']['total'])}"),
                html.Span(f"True total incl. hidden {_money(breakdown['true_totals']['total'])}"),
                html.Span("Trades and markets columns are range-scoped"),
            ],
            className="pm-summary-strip",
        )
        button_label = "Hide Hidden Wallets" if show_hidden else "Show Hidden Wallets"
        range_button_label = (
            "Hide Wallets Outside Date Range"
            if include_outside_range
            else "Include Wallets Outside Date Range"
        )
        return table, count_label, totals_text, button_label, range_button_label
    except Exception as exc:
        logger.exception("Failed to load daily table")
        return (
            html.Div(f"Daily table unavailable: {exc}", className="pm-empty-state__copy"),
            "",
            html.Div(f"Daily table unavailable: {exc}"),
            "Show Hidden Wallets",
            "Include Wallets Outside Date Range",
        )


@callback(
    [
        Output("wallet-management-banner", "children"),
        Output("wallet-management-pending", "children"),
        Output("wallet-management-sections", "children"),
        Output("btn-open-push", "children"),
    ],
    [Input("wallet-admin-token", "data"), Input("refresh-token", "data"), Input("status-poll", "n_intervals")],
)
def update_wallet_management_view(_, __, ___):
    try:
        conn = get_connection()
        snapshot = get_wallet_management_snapshot(conn, bootstrap=not READ_ONLY_UI)
        conn.close()
        logger.info(
            "Wallet management snapshot rendered: test=%d promoted=%d high_conviction=%d pending=%d",
            next((len(tier["wallets"]) for tier in snapshot["tiers"] if tier["tier_name"] == "test"), 0),
            next((len(tier["wallets"]) for tier in snapshot["tiers"] if tier["tier_name"] == "promoted"), 0),
            next((len(tier["wallets"]) for tier in snapshot["tiers"] if tier["tier_name"] == "high_conviction"), 0),
            snapshot["pending_count"],
        )
        banner = None
        if snapshot["bootstrap_count"]:
            banner = dbc.Alert(
                "All existing wallets have been assigned to Test tier. Review and promote as needed.",
                color="info",
            )
        pending_bits = [html.Span(f"{snapshot['pending_count']} queued local changes")]
        if snapshot["pending_push"]:
            pushed_at = parse_db_timestamp(snapshot["pending_push"]["pushed_at"])
            pushed_label = pushed_at.strftime("%Y-%m-%d %H:%M UTC") if pushed_at else "Unknown"
            pending_bits.append(html.Span(f"Pending push #{snapshot['pending_push']['id']} since {pushed_label}"))
        pending = html.Div(pending_bits, className="pm-summary-strip")
        push_label = f"Push to VPS 🚀 ({snapshot['pending_count']})" if snapshot["pending_count"] else "Push to VPS 🚀"
        return banner, pending, _render_management_sections(snapshot), push_label
    except Exception as exc:
        logger.exception("Failed to load wallet management")
        return (
            _database_error_layout(str(exc)),
            html.Div("Wallet management data unavailable.", className="pm-empty-state__copy"),
            [],
            "Push to VPS 🚀",
        )


@callback(
    [Output("wallet-management-flash", "children"), Output("wallet-admin-token", "data")],
    Input(_button_id("wallet-action", action=ALL, wallet=ALL, render_token=ALL), "n_clicks"),
    State("wallet-admin-token", "data"),
    prevent_initial_call=True,
)
def handle_wallet_management_actions(clicks, wallet_admin_token):
    # clicks is a list of n_clicks for ALL matched buttons.
    # On rerender, all values are 0 (no real click). any() catches this.
    if not any(clicks):
        return no_update, no_update
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return no_update, no_update
    if READ_ONLY_UI:
        return dbc.Alert("Wallet management writes are disabled in local read-only mode.", color="secondary"), no_update

    wallet = triggered["wallet"]
    action = triggered["action"]
    try:
        conn = get_connection()
        if action == "promote":
            result = promote_or_demote_wallet(conn, wallet, "up")
            message = dbc.Alert(
                f"Queued promotion for {wallet} to {result['to_tier'].replace('_', ' ').title()} ({result['new_copy_pct']}%).",
                color="success",
            )
        elif action == "demote":
            result = promote_or_demote_wallet(conn, wallet, "down")
            message = dbc.Alert(
                f"Queued demotion for {wallet} to {result['to_tier'].replace('_', ' ').title()} ({result['new_copy_pct']}%).",
                color="warning",
            )
        elif action == "remove":
            remove_wallet(conn, wallet)
            message = dbc.Alert(f"Queued removal for {wallet}.", color="danger")
        else:
            message = dbc.Alert(f"Unsupported wallet action: {action}", color="danger")
        conn.close()
        return message, wallet_admin_token + 1
    except Exception as exc:
        logger.exception("Wallet management action failed")
        return dbc.Alert(str(exc), color="danger"), no_update


@callback(
    [Output("wallet-management-flash", "children", allow_duplicate=True), Output("wallet-admin-token", "data", allow_duplicate=True)],
    Input(_button_id("remove-pending-change", change_id=ALL, render_token=ALL), "n_clicks"),
    State("wallet-admin-token", "data"),
    prevent_initial_call=True,
)
def handle_remove_pending_change(clicks, wallet_admin_token):
    if not any(clicks):
        return no_update, no_update
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return no_update, no_update
    if READ_ONLY_UI:
        return dbc.Alert("Queued change removal is disabled in local read-only mode.", color="secondary"), no_update

    try:
        conn = get_connection()
        result = remove_pending_change(conn, triggered["change_id"])
        conn.close()
        removed = result["removed_change"]
        action_labels = {
            "add": "add",
            "remove": "removal",
            "promote": "promotion",
            "demote": "demotion",
            "update_tier_config": "tier config update",
        }
        label = action_labels.get(removed["change_type"], removed["change_type"])
        wallet = removed["wallet_address"]
        return (
            dbc.Alert(
                f"Removed queued {label} for {wallet}. {result['remaining_count']} changes remain in the next push.",
                color="info",
            ),
            wallet_admin_token + 1,
        )
    except Exception as exc:
        logger.exception("Failed to remove queued change")
        return dbc.Alert(str(exc), color="danger"), no_update


@callback(
    [
        Output("add-wallet-modal", "is_open"),
        Output("add-wallet-message", "children"),
        Output("add-wallet-line", "value"),
        Output("wallet-admin-token", "data", allow_duplicate=True),
    ],
    [
        Input("btn-open-add-wallet", "n_clicks"),
        Input("btn-cancel-add-wallet", "n_clicks"),
        Input("btn-submit-add-wallet", "n_clicks"),
    ],
    [
        State("add-wallet-modal", "is_open"),
        State("add-wallet-line", "value"),
        State("add-wallet-tier", "value"),
        State("wallet-admin-token", "data"),
    ],
    prevent_initial_call=True,
)
def handle_add_wallet_modal(_, __, submit_clicks, is_open, raw_lines, tier_name, wallet_admin_token):
    triggered = dash.callback_context.triggered_id
    if triggered == "btn-open-add-wallet":
        return True, "", no_update, no_update
    if triggered == "btn-cancel-add-wallet":
        return False, "", "", no_update
    if triggered == "btn-submit-add-wallet":
        if READ_ONLY_UI:
            return True, dbc.Alert("Wallet writes are disabled in local read-only mode.", color="secondary"), no_update, no_update
        if not submit_clicks:
            return is_open, no_update, no_update, no_update

        lines = [l.strip() for l in (raw_lines or "").split("\n") if l.strip()]
        if not lines:
            return True, dbc.Alert("Paste at least one CSV line.", color="warning"), no_update, no_update

        added = []
        errors = []
        conn = get_connection()
        for line in lines:
            try:
                result = add_wallet_from_csv_line(conn, line, tier_name)
                added.append(result["wallet_address"][:12] + "...")
            except Exception as exc:
                errors.append(f"{line[:20]}...: {exc}")
        conn.close()

        messages = []
        if added:
            messages.append(dbc.Alert(
                f"Queued {len(added)} wallet(s) at {tier_name.replace('_', ' ').title()}: {', '.join(added)}",
                color="success",
            ))
        if errors:
            messages.append(dbc.Alert(
                html.Div([html.Div(e) for e in errors]),
                color="danger",
            ))

        return (
            False if not errors else True,
            html.Div(messages),
            "" if not errors else no_update,
            wallet_admin_token + 1 if added else no_update,
        )
    return is_open, no_update, no_update, no_update


@callback(
    Output("push-preview-modal", "is_open"),
    [Input("btn-open-push", "n_clicks"), Input("btn-cancel-push", "n_clicks"), Input("btn-confirm-push", "n_clicks")],
    State("push-preview-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_push_preview_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
    triggered = dash.callback_context.triggered_id
    if triggered == "btn-open-push" and open_clicks:
        return True
    if triggered == "btn-cancel-push" and cancel_clicks:
        return False
    if triggered == "btn-confirm-push" and confirm_clicks:
        return False
    return is_open


@callback(
    [Output("push-preview-body", "children"), Output("btn-confirm-push", "disabled")],
    [Input("push-preview-modal", "is_open"), Input("wallet-admin-token", "data")],
)
def render_push_preview(is_open, _):
    if not is_open:
        return no_update, no_update
    try:
        conn = get_connection()
        snapshot = get_wallet_management_snapshot(conn, bootstrap=False)
        conn.close()
        if READ_ONLY_UI:
            return dbc.Alert("Pushes are disabled in local read-only mode.", color="secondary"), True
        if snapshot["pending_push"]:
            return dbc.Alert("A push is already pending on the VPS. Wait for it to apply before pushing again.", color="warning"), True
        if not snapshot["pending_changes"]:
            return dbc.Alert("There are no queued changes to push.", color="secondary"), True
        return html.Div(
            [_pending_change_card(change, removable=True, render_token=snapshot.get("render_token")) for change in snapshot["pending_changes"]],
        ), False
    except Exception as exc:
        logger.exception("Failed to render push preview")
        return dbc.Alert(str(exc), color="danger"), True


@callback(
    [Output("wallet-management-flash", "children", allow_duplicate=True), Output("wallet-admin-token", "data", allow_duplicate=True)],
    Input("btn-confirm-push", "n_clicks"),
    State("wallet-admin-token", "data"),
    prevent_initial_call=True,
)
def confirm_push_to_vps(n_clicks, wallet_admin_token):
    if not n_clicks:
        return no_update, no_update
    if READ_ONLY_UI:
        return dbc.Alert("Pushes are disabled in local read-only mode.", color="secondary"), no_update
    try:
        conn = get_connection()
        result = create_push_from_pending_changes(conn)
        conn.close()
        return dbc.Alert(f"Push #{result['push_id']} queued for the VPS: {result['summary']}.", color="success"), wallet_admin_token + 1
    except Exception as exc:
        logger.exception("Failed to create push")
        return dbc.Alert(str(exc), color="danger"), no_update


@callback(
    [
        Output("settings-copy-test", "value"),
        Output("settings-copy-promoted", "value"),
        Output("settings-copy-high", "value"),
        Output("settings-count-test", "children"),
        Output("settings-count-promoted", "children"),
        Output("settings-count-high", "children"),
    ],
    [Input("wallet-admin-token", "data"), Input("status-poll", "n_intervals")],
)
def load_tier_settings(_, __):
    try:
        conn = get_connection()
        configs = {row["tier_name"]: row for row in get_tier_configs(conn)}
        rows = conn.execute(
            "SELECT tier_name, COUNT(*) AS wallet_count FROM wallet_tiers GROUP BY tier_name"
        ).fetchall()
        conn.close()
        counts = {row["tier_name"]: int(row["wallet_count"] or 0) for row in rows}
        return (
            configs["test"]["copy_percentage"],
            configs["promoted"]["copy_percentage"],
            configs["high_conviction"]["copy_percentage"],
            html.Span(f"{counts.get('test', 0)} wallets", className="pm-settings-row__count-pill"),
            html.Span(f"{counts.get('promoted', 0)} wallets", className="pm-settings-row__count-pill"),
            html.Span(f"{counts.get('high_conviction', 0)} wallets", className="pm-settings-row__count-pill"),
        )
    except Exception:
        return no_update, no_update, no_update, "—", "—", "—"


@callback(
    [Output("settings-message", "children"), Output("wallet-admin-token", "data", allow_duplicate=True)],
    Input("btn-save-tier-settings", "n_clicks"),
    [
        State("settings-copy-test", "value"),
        State("settings-copy-promoted", "value"),
        State("settings-copy-high", "value"),
        State("wallet-admin-token", "data"),
    ],
    prevent_initial_call=True,
)
def save_tier_settings(n_clicks, test_copy, promoted_copy, high_copy, wallet_admin_token):
    if not n_clicks:
        return no_update, no_update
    if READ_ONLY_UI:
        return dbc.Alert("Tier config writes are disabled in local read-only mode.", color="secondary"), no_update
    try:
        conn = get_connection()
        result = save_tier_config_changes(
            conn,
            {
                "test": test_copy,
                "promoted": promoted_copy,
                "high_conviction": high_copy,
            },
        )
        conn.close()
        if not result["changed_tiers"]:
            return dbc.Alert("No tier percentages changed.", color="secondary"), no_update
        return (
            dbc.Alert(f"Saved tier settings. Next push will update {result['total_affected']} wallet rows.", color="success"),
            wallet_admin_token + 1,
        )
    except Exception as exc:
        logger.exception("Failed to save tier settings")
        return dbc.Alert(str(exc), color="danger"), no_update


@callback(
    Output("selected-push-id", "data"),
    [Input("wallet-admin-token", "data"), Input("status-poll", "n_intervals"), Input(_button_id("view-push", push_id=ALL), "n_clicks")],
    State("selected-push-id", "data"),
)
def sync_selected_push(_, __, ___, current_selected):
    triggered = dash.callback_context.triggered_id
    if isinstance(triggered, dict) and triggered.get("type") == "view-push":
        return triggered["push_id"]
    try:
        conn = get_connection()
        pushes = list_push_history(conn)
        conn.close()
    except Exception:
        return current_selected
    valid_ids = {push["id"] for push in pushes}
    if current_selected in valid_ids:
        return current_selected
    return pushes[0]["id"] if pushes else None


@callback(
    [Output("changes-list", "children"), Output("changes-detail", "children")],
    [Input("selected-push-id", "data"), Input("wallet-admin-token", "data"), Input("status-poll", "n_intervals")],
)
def render_changes_view(selected_push_id, _, __):
    try:
        conn = get_connection()
        pushes = list_push_history(conn)
        detail = get_push_detail(conn, selected_push_id) if selected_push_id else None
        conn.close()
        return _render_push_list(pushes), _render_push_detail(detail)
    except Exception as exc:
        logger.exception("Failed to load change history")
        error = _database_error_layout(str(exc))
        return error, error


@callback(
    Output("revert-modal", "is_open"),
    [Input("btn-open-revert", "n_clicks"), Input("btn-cancel-revert", "n_clicks"), Input("btn-confirm-revert", "n_clicks")],
    State("revert-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_revert_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
    triggered = dash.callback_context.triggered_id
    if triggered == "btn-open-revert" and open_clicks:
        return True
    if triggered == "btn-cancel-revert" and cancel_clicks:
        return False
    if triggered == "btn-confirm-revert" and confirm_clicks:
        return False
    return is_open


@callback(
    Output("revert-preview-body", "children"),
    [Input("revert-modal", "is_open"), Input("selected-push-id", "data")],
)
def render_revert_preview(is_open, selected_push_id):
    if not is_open or not selected_push_id:
        return no_update
    try:
        conn = get_connection()
        detail = get_push_detail(conn, selected_push_id)
        conn.close()
        if not detail:
            return dbc.Alert("Push not found.", color="danger")
        pushed_at = parse_db_timestamp(detail["pushed_at"]) if detail["pushed_at"] else None
        pushed_label = pushed_at.strftime("%Y-%m-%d %H:%M UTC") if pushed_at else "Unknown time"
        return html.Div(
            [
                html.Div(
                    f"Revert to the CSV state from {pushed_label} (before Push #{detail['id']} was applied).",
                    className="pm-range-copy",
                ),
                html.Div([_pending_change_card(change) for change in detail["changes"]], className="pm-history-detail-list"),
            ]
        )
    except Exception as exc:
        return dbc.Alert(str(exc), color="danger")


@callback(
    [Output("changes-message", "children"), Output("wallet-admin-token", "data", allow_duplicate=True)],
    Input("btn-confirm-revert", "n_clicks"),
    [State("selected-push-id", "data"), State("wallet-admin-token", "data")],
    prevent_initial_call=True,
)
def confirm_revert_push(n_clicks, selected_push_id, wallet_admin_token):
    if not n_clicks or not selected_push_id:
        return no_update, no_update
    if READ_ONLY_UI:
        return dbc.Alert("Reverts are disabled in local read-only mode.", color="secondary"), no_update
    try:
        conn = get_connection()
        result = create_revert_push(conn, selected_push_id)
        conn.close()
        return dbc.Alert(f"Queued revert push #{result['push_id']}.", color="warning"), wallet_admin_token + 1
    except Exception as exc:
        logger.exception("Failed to create revert push")
        return dbc.Alert(str(exc), color="danger"), no_update


@callback(
    Output("refresh-token", "data", allow_duplicate=True),
    Input(_button_id("toggle-hide", wallet=ALL), "n_clicks"),
    State("refresh-token", "data"),
    prevent_initial_call=True,
)
def toggle_hidden_wallet(clicks, refresh_token):
    if READ_ONLY_UI:
        return no_update
    if not any(clicks):
        return no_update
    triggered = dash.callback_context.triggered_id
    if not triggered:
        return no_update
    wallet = triggered["wallet"]

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
        return None, "Select a wallet", {"color": COLORS["text_secondary"]}, "", ""
    try:
        conn = get_connection()
        payload = get_chart_payload(conn, wallet=wallet, range_key=range_key)
        stats = get_wallet_stats(conn, wallet)
        conn.close()
        if not stats:
            return None, "No data", {"color": COLORS["text_secondary"]}, "", ""

        current = payload["current_delta_pnl"]
        realized_tone = "positive" if stats["realized"] > 0 else "negative" if stats["realized"] < 0 else "default"
        unrealized_tone = "positive" if stats["unrealized"] > 0 else "negative" if stats["unrealized"] < 0 else "default"
        stat_block = html.Div(
            [
                html.Div(
                    [
                        _stat_tile("Wallet", html.Span(
                            stats["wallet"], className="pm-wallet-copyable",
                            title="Click to copy", **{"data-clipboard": stats["wallet"]})),
                        _stat_tile("Filter", stats["filter"]),
                        _stat_tile("Actual", stats["game"]),
                        _stat_tile("Invested", _money(stats["invested"])),
                        _stat_tile("Realized", _money(stats["realized"]), tone=realized_tone),
                        _stat_tile("Unrealized", _money(stats["unrealized"]), tone=unrealized_tone),
                        _stat_tile("Markets", f"{stats['markets']}"),
                        _stat_tile("Trades", f"{stats['trades']}"),
                        _stat_tile("Excluded", f"{stats['excluded_positions']}"),
                    ],
                    className="pm-wallet-stat-grid",
                ),
                html.Div(
                    [
                        html.Span(f"First trade {stats['first_trade'] or '-'}"),
                        html.Span(f"Last trade {stats['last_trade'] or '-'}"),
                    ],
                    className="pm-summary-strip",
                ),
            ],
            className="pm-wallet-summary",
        )
        return (
            payload,
            _money(current),
            {"color": _line_color(current)},
            _format_chart_range_label(payload, range_key),
            stat_block,
        )
    except Exception as exc:
        logger.exception("Failed to load wallet view")
        return None, str(exc), {"color": COLORS["negative"]}, "", ""


# ─── Export callbacks ──────────────────────────────────────────
@callback(
    Output("download-xlsx", "data"),
    Input("btn-export-xlsx", "n_clicks"),
    prevent_initial_call=True,
)
def handle_export_xlsx(n_clicks):
    if not n_clicks:
        return no_update
    try:
        from lib.exporter import export_xlsx
        conn = get_connection()
        xlsx_bytes = export_xlsx(conn)
        conn.close()
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        return dcc.send_bytes(xlsx_bytes, f"wallet_export_{ts}.xlsx")
    except Exception as exc:
        logger.exception("XLSX export failed")
        return no_update


@callback(
    Output("download-txt", "data"),
    Input("btn-export-txt", "n_clicks"),
    prevent_initial_call=True,
)
def handle_export_txt(n_clicks):
    if not n_clicks:
        return no_update
    try:
        from lib.exporter import export_wallet_list_txt
        conn = get_connection()
        txt = export_wallet_list_txt(conn)
        conn.close()
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        return dict(content=txt, filename=f"wallet_list_{ts}.txt")
    except Exception as exc:
        logger.exception("TXT export failed")
        return no_update


# ─── Subcategory Charts callbacks ──────────────────────────────
@callback(
    Output("sc-game-dropdown", "options"),
    Input("tabs", "value"),
)
def load_game_options(tab):
    if tab != "subcategory-charts":
        return no_update
    try:
        from lib.clickhouse_charts import ClickHouseClient, get_available_filters
        client = ClickHouseClient()
        filters = get_available_filters(client)

        # Build grouped options: categories first, then subcategories, then details
        # Value encodes the level: "category::Sports", "subcategory::Tennis", "detail::Counter-Strike"
        options = []
        seen = set()
        for level_label, level_key in [("Category", "category"), ("Subcategory", "subcategory"), ("Detail", "detail")]:
            level_items = [f for f in filters if f["level"] == level_key]
            for item in level_items:
                val = f"{level_key}::{item['label']}"
                if val not in seen:
                    seen.add(val)
                    options.append({
                        "label": f"[{level_label}] {item['label']} ({item['count']:,})",
                        "value": val,
                    })
        return options
    except Exception as exc:
        logger.warning("Failed to load filters from ClickHouse: %s", exc)
        return [
            {"label": "[Subcategory] Esports", "value": "subcategory::Esports"},
            {"label": "[Detail] Counter-Strike", "value": "detail::Counter-Strike"},
            {"label": "[Subcategory] NBA", "value": "subcategory::NBA"},
            {"label": "[Subcategory] US Politics", "value": "subcategory::US Politics"},
        ]


_SC_RANGES = [1, 7, 14, 30, 365]


# Range pill click → update active range store + highlight
@callback(
    [Output("sc-active-range", "data")] + [Output(f"sc-range-{d}", "className") for d in _SC_RANGES],
    [Input(f"sc-range-{d}", "n_clicks") for d in _SC_RANGES],
    State("sc-active-range", "data"),
    prevent_initial_call=True,
)
def update_active_range(c1, c7, c14, c30, c365, current):
    triggered = dash.ctx.triggered_id
    selected = current or 365
    click_map = {"sc-range-1": 1, "sc-range-7": 7, "sc-range-14": 14, "sc-range-30": 30, "sc-range-365": 365}
    clicks_map = {"sc-range-1": c1, "sc-range-7": c7, "sc-range-14": c14, "sc-range-30": c30, "sc-range-365": c365}
    if triggered in click_map and clicks_map.get(triggered):
        selected = click_map[triggered]
    return [selected] + [
        "pm-range-pill pm-range-pill--active" if d == selected else "pm-range-pill"
        for d in _SC_RANGES
    ]


# Generate chart — triggered by Generate button OR range pill change
@callback(
    [
        Output("sc-chart", "figure"),
        Output("sc-chart-container", "style"),
        Output("sc-summary", "children"),
        Output("sc-message", "children"),
    ],
    [Input("sc-generate", "n_clicks"), Input("sc-active-range", "data")],
    [State("sc-wallet-input", "value"), State("sc-game-dropdown", "value")],
    prevent_initial_call=True,
)
def generate_subcategory_chart(n_clicks, active_range, wallet, filter_raw):
    import plotly.graph_objects as go

    # Only run if Generate was clicked at least once (don't fire on initial range store)
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    if not wallet or not wallet.startswith("0x"):
        return no_update, {"display": "none"}, "", dbc.Alert("Enter a valid wallet address (0x...)", color="warning")

    if not filter_raw:
        return no_update, {"display": "none"}, "", dbc.Alert("Select a category from the dropdown.", color="warning")

    # Parse level and value from "level::value" format
    if "::" in filter_raw:
        filter_level, filter_value = filter_raw.split("::", 1)
    else:
        filter_level, filter_value = "detail", filter_raw

    lookback = active_range or 365

    try:
        from lib.clickhouse_charts import get_wallet_game_chart
        payload = get_wallet_game_chart(wallet.strip().lower(), filter_value, lookback, filter_level=filter_level)
    except Exception as exc:
        logger.exception("ClickHouse chart query failed")
        return no_update, {"display": "none"}, "", dbc.Alert(f"ClickHouse error: {exc}", color="danger")

    if not payload:
        return no_update, {"display": "none"}, "", dbc.Alert(f"No trades found for {wallet[:12]}... in {filter_value}.", color="info")

    series = payload["series"]
    summary = payload["summary"]
    dates = [s["date"] for s in series]
    pnl = [s["pnl"] for s in series]

    final_pnl = summary["final_pnl"]
    color = "#22c55e" if final_pnl >= 0 else "#ef4444"
    fill_color = "rgba(34,197,94,0.12)" if final_pnl >= 0 else "rgba(239,68,68,0.12)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=pnl,
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=fill_color,
        customdata=[[s["cumulative_cash"], s["marked_value"], s["daily_trade_count"]] for s in series],
        hovertemplate=(
            "<b>%{x}</b><br>"
            "P&L: $%{y:,.2f}<br>"
            "Cash: $%{customdata[0]:,.2f}<br>"
            "Marked Value: $%{customdata[1]:,.2f}<br>"
            "Trades: %{customdata[2]}<extra></extra>"
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=50, r=20, t=20, b=40),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(122,145,173,0.10)",
            zeroline=False, tickprefix="$",
        ),
        hovermode="x unified",
    )

    # Summary stats
    sign = "" if final_pnl == 0 else ("-" if final_pnl < 0 else "")
    pnl_str = f"{sign}${abs(final_pnl):,.2f}"
    vol_str = f"${summary['total_volume_usd']:,.0f}"

    stats = html.Div([
        _card([
            html.Div([
                _stat_tile("Final P&L", pnl_str, tone="positive" if final_pnl >= 0 else "negative"),
                _stat_tile("Trades", f"{summary['total_trades']:,}"),
                _stat_tile("Volume", vol_str),
                _stat_tile("Max Drawdown", f"{summary['max_drawdown_pct']:.1f}%"),
                _stat_tile("Tokens", f"{summary['scoped_tokens']}"),
                _stat_tile("Period", f"{summary['first_trade_date']} — {summary['chart_end_date']}"),
            ], className="pm-wallet-stat-grid"),
        ], class_name="pm-admin-card"),
    ])

    return fig, {"display": "block", "marginTop": "16px"}, stats, ""


# ─── Wallet Curation callbacks ─────────────────────────────────
@callback(
    Output("cur-category", "options"),
    Input("tabs", "value"),
)
def load_curation_categories(tab):
    if tab != "wallet-curation":
        return no_update
    try:
        from lib.clickhouse_charts import ClickHouseClient, get_available_filters
        client = ClickHouseClient()
        filters = get_available_filters(client)
        options = []
        seen = set()
        for level_label, level_key in [("Category", "category"), ("Subcategory", "subcategory"), ("Detail", "detail")]:
            for item in [f for f in filters if f["level"] == level_key]:
                val = f"{level_key}::{item['label']}"
                if val not in seen:
                    seen.add(val)
                    options.append({"label": f"[{level_label}] {item['label']} ({item['count']:,})", "value": val})
        return options
    except Exception:
        return [{"label": "[Subcategory] Esports", "value": "subcategory::Esports"}]


@callback(
    [Output("cur-wallets", "data"), Output("cur-filter", "data"), Output("cur-range", "data"),
     Output("cur-index", "data"), Output("cur-approved", "data"), Output("cur-decisions", "data"),
     Output("cur-setup", "style"), Output("cur-swipe", "style"), Output("cur-results", "style"),
     Output("cur-setup-msg", "children")],
    Input("cur-start", "n_clicks"),
    [State("cur-wallet-input", "value"), State("cur-category", "value")] +
    [State(f"cur-setup-range-{d}", "n_clicks") for d in [1, 7, 14, 30, 365]],
    prevent_initial_call=True,
)
def start_curation(n_clicks, wallet_text, category, *range_clicks):
    if not n_clicks:
        return [no_update] * 10

    if not wallet_text or not wallet_text.strip():
        return [no_update] * 9 + [dbc.Alert("Paste at least one wallet address.", color="warning")]
    if not category:
        return [no_update] * 9 + [dbc.Alert("Select a category.", color="warning")]

    wallets = [w.strip().lower() for w in wallet_text.strip().split("\n") if w.strip().startswith("0x")]
    if not wallets:
        return [no_update] * 9 + [dbc.Alert("No valid wallet addresses found.", color="warning")]

    # Determine range
    range_map = {0: 1, 1: 7, 2: 14, 3: 30, 4: 365}
    lookback = 365
    max_c = 0
    for i, c in enumerate(range_clicks):
        if c and c > max_c:
            max_c = c
            lookback = range_map[i]

    return (
        wallets, category, lookback, 0, [], {},
        {"display": "none"}, {"display": "block"}, {"display": "none"}, "",
    )


@callback(
    [Output("cur-progress", "children"), Output("cur-wallet-header", "children"),
     Output("cur-chart", "figure"), Output("cur-stats", "children"),
     Output("cur-concentration", "children"), Output("cur-top-markets", "children")],
    Input("cur-index", "data"),
    [State("cur-wallets", "data"), State("cur-filter", "data"), State("cur-range", "data")],
)
def render_curation_wallet(index, wallets, filter_raw, lookback):
    import plotly.graph_objects as go

    if not wallets or index is None or index >= len(wallets):
        return ["", "", go.Figure(), "", "", ""]

    wallet = wallets[index]
    if "::" in (filter_raw or ""):
        filter_level, filter_value = filter_raw.split("::", 1)
    else:
        filter_level, filter_value = "detail", filter_raw or ""

    progress = f"Wallet {index + 1} of {len(wallets)}"
    header = html.Div([
        html.Span(wallet, className="pm-wallet-copyable", **{"data-clipboard": wallet}),
        html.Span(f" — {filter_value}", style={"color": "var(--pm-text-secondary)", "marginLeft": "8px"}),
    ])

    try:
        from lib.clickhouse_charts import get_wallet_curation_data
        data = get_wallet_curation_data(wallet, filter_value, lookback, filter_level)
    except Exception as exc:
        logger.exception("Curation chart failed")
        return [progress, header, go.Figure(), dbc.Alert(f"Error: {exc}", color="danger"), "", ""]

    if not data:
        return [progress, header, go.Figure(),
                dbc.Alert(f"No trades found for {wallet[:12]}... in {filter_value}.", color="info"), "", ""]

    # Chart
    series = data["series"]
    summary = data["summary"]
    final_pnl = summary["final_pnl"]
    color = "#22c55e" if final_pnl >= 0 else "#ef4444"
    fill_color = "rgba(34,197,94,0.12)" if final_pnl >= 0 else "rgba(239,68,68,0.12)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[s["date"] for s in series], y=[s["pnl"] for s in series],
        mode="lines", line=dict(color=color, width=2), fill="tozeroy", fillcolor=fill_color,
        customdata=[[s["cumulative_cash"], s["marked_value"], s["daily_trade_count"]] for s in series],
        hovertemplate="<b>%{x}</b><br>P&L: $%{y:,.2f}<br>Cash: $%{customdata[0]:,.2f}<br>Marked: $%{customdata[1]:,.2f}<br>Trades: %{customdata[2]}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=50, r=20, t=10, b=40),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(122,145,173,0.10)", zeroline=False, tickprefix="$"),
        hovermode="x unified",
    )

    # Stats
    pnl_color = "positive" if final_pnl >= 0 else "negative"
    stats = html.Div([
        _stat_tile("Final P&L", f"${final_pnl:,.2f}", tone=pnl_color),
        _stat_tile("ROI", f"{summary.get('roi_pct', 0):.1f}%"),
        _stat_tile("Trades", f"{summary['total_trades']:,}"),
        _stat_tile("Volume", f"${summary['total_volume_usd']:,.0f}"),
        _stat_tile("Drawdown", f"{summary['max_drawdown_pct']:.1f}%"),
        _stat_tile("Win Rate", f"{summary.get('win_rate', 0):.0f}%"),
    ], className="pm-wallet-stat-grid")

    # Concentration
    breakdown = data.get("breakdown", {})
    conc = breakdown.get("concentration", {})

    def conc_badge(label, pct):
        if pct > 50:
            color, bg = "#ef4444", "rgba(239,68,68,0.12)"
        elif pct > 30:
            color, bg = "#f59e0b", "rgba(245,158,11,0.12)"
        else:
            color, bg = "#22c55e", "rgba(34,197,94,0.12)"
        return html.Span(f"{label}: {pct}%",
                          style={"color": color, "background": bg, "padding": "4px 10px",
                                 "borderRadius": "6px", "fontSize": "12px", "border": f"1px solid {color}30"})

    concentration = html.Div([
        html.Div("P&L Concentration", style={"fontSize": "11px", "color": "var(--pm-text-secondary)",
                                              "textTransform": "uppercase", "letterSpacing": "0.5px", "marginBottom": "6px"}),
        html.Div([
            conc_badge("Top 1 Market", conc.get("top1_pct", 0)),
            conc_badge("Top 3 Markets", conc.get("top3_pct", 0)),
            conc_badge("Top 5 Markets", conc.get("top5_pct", 0)),
        ], style={"display": "flex", "gap": "8px"}),
    ])

    # Top markets table
    top_markets_data = breakdown.get("markets", [])
    total_abs = sum(abs(m["net_cash"]) for m in top_markets_data) if top_markets_data else 0
    if top_markets_data:
        market_rows = []
        for m in top_markets_data:
            pct = round(abs(m["net_cash"]) / total_abs * 100, 1) if total_abs else 0
            mc = "#22c55e" if m["net_cash"] >= 0 else "#ef4444"
            market_rows.append(html.Tr([
                html.Td(m["market_name"][:60], style={"maxWidth": "300px", "overflow": "hidden", "textOverflow": "ellipsis"}),
                html.Td(f"${m['net_cash']:,.2f}", style={"color": mc}),
                html.Td(str(m["total_trades"])),
                html.Td(f"{pct}%"),
            ], className="pm-tier-table__row"))

        top_markets = html.Div([
            html.Div("Top Markets by P&L", style={"fontSize": "11px", "color": "var(--pm-text-secondary)",
                                                    "textTransform": "uppercase", "letterSpacing": "0.5px", "marginBottom": "6px"}),
            html.Table([
                html.Thead(html.Tr([html.Th("Market"), html.Th("P&L"), html.Th("Trades"), html.Th("% of Total")])),
                html.Tbody(market_rows),
            ], className="pm-tier-table", style={"fontSize": "12px"}),
        ])
    else:
        top_markets = ""

    return [progress, header, fig, stats, concentration, top_markets]


@callback(
    [Output("cur-index", "data", allow_duplicate=True),
     Output("cur-approved", "data", allow_duplicate=True),
     Output("cur-decisions", "data", allow_duplicate=True),
     Output("cur-setup", "style", allow_duplicate=True),
     Output("cur-swipe", "style", allow_duplicate=True),
     Output("cur-results", "style", allow_duplicate=True),
     Output("cur-results-title", "children"),
     Output("cur-results-list", "children")],
    [Input("cur-approve", "n_clicks"), Input("cur-skip", "n_clicks"), Input("cur-back", "n_clicks")],
    [State("cur-index", "data"), State("cur-wallets", "data"),
     State("cur-approved", "data"), State("cur-decisions", "data")],
    prevent_initial_call=True,
)
def handle_curation_action(approve_clicks, skip_clicks, back_clicks, index, wallets, approved, decisions):
    triggered = dash.ctx.triggered_id
    if not triggered or not wallets:
        return [no_update] * 8

    if triggered == "cur-back":
        if index > 0:
            return [index - 1] + [no_update] * 7
        return [no_update] * 8

    wallet = wallets[index] if index < len(wallets) else None
    if not wallet:
        return [no_update] * 8

    if triggered == "cur-approve" and approve_clicks:
        if wallet not in approved:
            approved = approved + [wallet]
        decisions = {**decisions, wallet: "approved"}
    elif triggered == "cur-skip" and skip_clicks:
        decisions = {**decisions, wallet: "skipped"}
    else:
        return [no_update] * 8

    next_index = index + 1
    if next_index >= len(wallets):
        # Done — show results
        title = f"Approved {len(approved)} of {len(wallets)} wallets"
        wallet_list = html.Div([html.Div(w, style={"fontFamily": "monospace", "padding": "2px 0"}) for w in approved]) if approved else html.Div("No wallets approved.", style={"color": "var(--pm-text-secondary)"})
        return [next_index, approved, decisions,
                {"display": "none"}, {"display": "none"}, {"display": "block"},
                title, wallet_list]

    return [next_index, approved, decisions, no_update, no_update, no_update, no_update, no_update]


@callback(
    Output("cur-download", "data"),
    Input("cur-download-btn", "n_clicks"),
    State("cur-approved", "data"),
    prevent_initial_call=True,
)
def download_approved(n_clicks, approved):
    if not n_clicks or not approved:
        return no_update
    txt = "\n".join(approved) + "\n"
    return dict(content=txt, filename=f"approved_wallets_{datetime.now().strftime('%Y%m%d_%H%M')}.txt")


@callback(
    [Output("cur-setup", "style", allow_duplicate=True),
     Output("cur-swipe", "style", allow_duplicate=True),
     Output("cur-results", "style", allow_duplicate=True)],
    Input("cur-new-batch", "n_clicks"),
    prevent_initial_call=True,
)
def new_batch(n_clicks):
    if not n_clicks:
        return [no_update] * 3
    return [{"display": "block"}, {"display": "none"}, {"display": "none"}]


def _ensure_clickhouse_tunnel():
    """Auto-start SSH tunnel for local ClickHouse access if not already running."""
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("DISABLE_TUNNEL") == "1":
        return  # skip on Railway or if explicitly disabled
    ch_url = os.environ.get("CLICKHOUSE_URL", "")
    if "127.0.0.1" not in ch_url and "localhost" not in ch_url:
        return  # not using local tunnel
    key_path = Path.home() / ".ssh" / "jake_hetzner_ed25519"
    if not key_path.exists():
        logger.info("SSH key not found at %s — skipping tunnel", key_path)
        return
    # Check if tunnel is already running
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ssh.*8123.*142.132.139.47"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("ClickHouse SSH tunnel already running")
            return
    except Exception:
        pass
    # Start tunnel in background
    try:
        subprocess.Popen(
            [
                "ssh", "-N", "-f",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ExitOnForwardFailure=yes",
                "-L", "8123:127.0.0.1:8123",
                "-L", "9000:127.0.0.1:9000",
                "-i", str(key_path),
                "jake@142.132.139.47",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Started ClickHouse SSH tunnel")
    except Exception as exc:
        logger.warning("Failed to start SSH tunnel: %s", exc)


start_scheduler()
try:
    init_db()
except Exception as exc:
    logger.warning("Initial database bootstrap failed: %s", exc)

if __name__ == "__main__":
    _ensure_clickhouse_tunnel()
    port = int(os.environ.get("PORT", "8050"))
    app.run(host="0.0.0.0", port=port, debug=False)
