#!/usr/bin/env python3
"""Wallet Curator cloud dashboard."""
import logging
import os
from datetime import timedelta

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


def _wallet_action_button(label, action, wallet, disabled=False):
    return html.Button(
        label,
        id=_button_id("wallet-action", action=action, wallet=wallet),
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


def _pending_change_card(change):
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

    return html.Div(
        [
            html.Div([html.Span(title, className="pm-history-row__title"), html.Span(game, className="pm-history-row__meta")], className="pm-history-row__top"),
            html.Div(subtitle, className="pm-history-row__subtitle"),
            html.Div(" | ".join(metrics), className="pm-history-row__metrics") if metrics else None,
        ],
        className="pm-history-row",
    )


def _render_management_wallet(wallet, tier_name):
    total_tone = "positive" if wallet["total_pnl"] > 0 else "negative" if wallet["total_pnl"] < 0 else "default"
    since_tone = "positive" if (wallet["since_promo_pnl"] or 0) > 0 else "negative" if (wallet["since_promo_pnl"] or 0) < 0 else "default"
    actions = []
    if tier_name != "high_conviction":
        actions.append(_wallet_action_button("Promote ▲", "promote", wallet["wallet_address"], disabled=READ_ONLY_UI))
    if tier_name != "test":
        actions.append(_wallet_action_button("Demote ▼", "demote", wallet["wallet_address"], disabled=READ_ONLY_UI))
    actions.append(_wallet_action_button("Remove ✕", "remove", wallet["wallet_address"], disabled=READ_ONLY_UI))

    chips = [
        _metric_chip("Game", wallet["game"]),
        _metric_chip("All-Time P&L", _money(wallet["total_pnl"]), tone=total_tone),
        _metric_chip("Markets", str(wallet["unique_markets"])),
        _metric_chip("Trades", str(wallet["total_trades"])),
        _metric_chip("Days Active", str(wallet["days_active"])),
    ]
    if tier_name != "test":
        chips.extend(
            [
                _metric_chip("Since Tier", _money(wallet["since_promo_pnl"] or 0), tone=since_tone),
                _metric_chip("At Tier Entry", _money(wallet["at_promo_pnl"] or 0)),
                _metric_chip("Days In Tier", str(wallet["days_in_tier"])),
            ]
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(wallet["wallet_address"], className="pm-wallet-admin-card__wallet"),
                    html.Div(wallet["game_filter"], className="pm-wallet-admin-card__filter"),
                ],
                className="pm-wallet-admin-card__header",
            ),
            html.Div(chips, className="pm-wallet-admin-card__metrics"),
            html.Div(actions, className="pm-wallet-admin-card__actions"),
        ],
        className="pm-wallet-admin-card",
    )


def _render_management_sections(snapshot):
    sections = []
    for tier in snapshot["tiers"]:
        rows = tier["wallets"]
        if rows:
            body = html.Div(
                [_render_management_wallet(wallet, tier["tier_name"]) for wallet in rows],
                className="pm-wallet-admin-grid",
            )
        else:
            body = html.Div("No wallets in this tier.", className="pm-empty-state__copy")
        sections.append(
            _card(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Tier", className="pm-kicker"),
                                    html.H3(
                                        f"{tier['display_name']} ({tier['copy_percentage']}% copy)",
                                        className="pm-section-title",
                                    ),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(f"{len(rows)} wallets", className="pm-section-side-note"),
                        ],
                        className="pm-card-head pm-card-head--tight",
                    ),
                    body,
                ],
                class_name="pm-admin-card",
            )
        )
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
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(f"Push #{push['id']}", className="pm-history-list__title"),
                            html.Div(push["summary"], className="pm-history-list__summary"),
                            html.Div(status_label, className=f"pm-history-status pm-history-status--{push['status']}"),
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
                            html.Div(f"Push #{detail['id']}", className="pm-section-title"),
                            html.Div(f"Pushed {pushed_label}", className="pm-range-copy"),
                            html.Div(f"Applied {applied_label}", className="pm-range-copy"),
                        ],
                        className="pm-card-title-block",
                    ),
                    html.Button(
                        "Revert",
                        id="btn-open-revert",
                        className="pm-button pm-button--secondary",
                        n_clicks=0,
                        disabled=READ_ONLY_UI or detail["status"] == "pending",
                    ),
                ],
                className="pm-card-head pm-card-head--tight",
            ),
            html.Div(detail["summary"], className="pm-inline-message"),
            html.Div(change_rows, className="pm-history-detail-list"),
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
                                            html.H2("Overview", className="pm-section-title"),
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
                                    html.H3("Daily Breakdown", className="pm-section-title"),
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
                    html.Div(
                        className="pm-table-shell",
                        children=[
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
                                        "color": COLORS["text_secondary"],
                                        "border": f"1px solid {COLORS['border']}",
                                        "fontWeight": "600",
                                        "textTransform": "uppercase",
                                        "fontSize": "12px",
                                        "letterSpacing": "0.04em",
                                        "whiteSpace": "normal",
                                        "height": "auto",
                                    },
                                    style_cell={
                                        "backgroundColor": COLORS["surface_soft"],
                                        "color": COLORS["text"],
                                        "border": f"1px solid {COLORS['border_soft']}",
                                        "padding": "14px 12px",
                                        "fontFamily": FONT_FAMILY,
                                        "fontSize": "13px",
                                        "lineHeight": "1.45",
                                        "whiteSpace": "normal",
                                        "height": "auto",
                                    },
                                    style_cell_conditional=[
                                        {
                                            "if": {"column_id": "wallet"},
                                            "minWidth": "460px",
                                            "width": "460px",
                                            "maxWidth": "560px",
                                            "whiteSpace": "nowrap",
                                        },
                                        {"if": {"column_id": "sim"}, "minWidth": "82px", "width": "82px", "maxWidth": "82px"},
                                        {"if": {"column_id": "filter"}, "minWidth": "132px", "width": "132px", "maxWidth": "150px"},
                                        {"if": {"column_id": "actual"}, "minWidth": "132px", "width": "132px", "maxWidth": "150px"},
                                        {"if": {"column_id": "hide"}, "minWidth": "88px", "width": "88px", "maxWidth": "88px"},
                                        {"if": {"column_id": "invested"}, "minWidth": "150px", "width": "150px", "maxWidth": "170px"},
                                        {"if": {"column_id": "realized_pnl"}, "minWidth": "160px", "width": "160px", "maxWidth": "176px"},
                                        {"if": {"column_id": "unrealized_pnl"}, "minWidth": "150px", "width": "150px", "maxWidth": "166px"},
                                        {"if": {"column_id": "total_pnl"}, "minWidth": "142px", "width": "142px", "maxWidth": "156px"},
                                        {"if": {"column_id": "markets"}, "minWidth": "140px", "width": "140px", "maxWidth": "150px"},
                                        {"if": {"column_id": "trades"}, "minWidth": "158px", "width": "158px", "maxWidth": "168px"},
                                        {"if": {"column_id": "in_csv"}, "minWidth": "96px", "width": "96px", "maxWidth": "96px"},
                                    ],
                                    style_table={"overflowX": "auto", "overflowY": "auto", "height": "760px", "maxHeight": "760px"},
                                    style_data_conditional=[],
                                )
                            )
                        ],
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
                                            html.H2("Per-Wallet Performance", className="pm-section-title"),
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
                                    html.H2("Wallet Management", className="pm-section-title"),
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
                    dbc.ModalHeader(dbc.ModalTitle("Add New Wallet")),
                    dbc.ModalBody(
                        [
                            html.Div("Paste full Sharp CSV line", className="pm-field-label"),
                            dcc.Textarea(
                                id="add-wallet-line",
                                className="pm-textarea",
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
                            html.Button("Queue Wallet", id="btn-submit-add-wallet", className="pm-button pm-button--primary", n_clicks=0),
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
                                    html.H2("Tier Settings", className="pm-section-title"),
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
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Test", className="pm-settings-row__name"),
                                    dcc.Input(id="settings-copy-test", type="number", min=0, step=0.1, className="pm-date-input"),
                                    html.Div(id="settings-count-test", className="pm-settings-row__count"),
                                ],
                                className="pm-settings-row",
                            ),
                            html.Div(
                                [
                                    html.Div("Promoted", className="pm-settings-row__name"),
                                    dcc.Input(id="settings-copy-promoted", type="number", min=0, step=0.1, className="pm-date-input"),
                                    html.Div(id="settings-count-promoted", className="pm-settings-row__count"),
                                ],
                                className="pm-settings-row",
                            ),
                            html.Div(
                                [
                                    html.Div("High Conviction", className="pm-settings-row__name"),
                                    dcc.Input(id="settings-copy-high", type="number", min=0, step=0.1, className="pm-date-input"),
                                    html.Div(id="settings-count-high", className="pm-settings-row__count"),
                                ],
                                className="pm-settings-row",
                            ),
                        ],
                        className="pm-settings-grid",
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
                                    html.H2("CSV Change History", className="pm-section-title"),
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
            dcc.Store(id="refresh-token", data=0),
            dcc.Store(id="wallet-admin-token", data=0),
            dcc.Store(id="overview-range", data="ALL"),
            dcc.Store(id="wallet-range", data="ALL"),
            dcc.Store(id="show-hidden", data=False),
            dcc.Store(id="include-outside-range", data=False),
            dcc.Store(id="selected-push-id"),
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
                                label="Wallet Management",
                                value="wallet-management",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Settings",
                                value="settings",
                                className="pm-tab",
                                selected_className="pm-tab pm-tab--selected",
                            ),
                            dcc.Tab(
                                label="Changes",
                                value="changes",
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


@callback(
    [
        Output("overview-container", "style"),
        Output("wallet-container", "style"),
        Output("wallet-management-container", "style"),
        Output("settings-container", "style"),
        Output("changes-container", "style"),
    ],
    Input("tabs", "value"),
)
def render_tab(active_tab):
    if active_tab == "wallets":
        return {"display": "none"}, {"display": "block"}, {"display": "none"}, {"display": "none"}, {"display": "none"}
    if active_tab == "wallet-management":
        return {"display": "none"}, {"display": "none"}, {"display": "block"}, {"display": "none"}, {"display": "none"}
    if active_tab == "settings":
        return {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "block"}, {"display": "none"}
    if active_tab == "changes":
        return {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "block"}
    return {"display": "block"}, {"display": "none"}, {"display": "none"}, {"display": "none"}, {"display": "none"}


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
        Output("daily-table", "data"),
        Output("daily-table", "style_data_conditional"),
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
        return breakdown["rows"], style, totals_text, button_label, range_button_label
    except Exception as exc:
        logger.exception("Failed to load daily table")
        return [], [], html.Div(f"Daily table unavailable: {exc}"), "Show Hidden Wallets", "Include Wallets Outside Date Range"


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
    Input(_button_id("wallet-action", action=ALL, wallet=ALL), "n_clicks"),
    State("wallet-admin-token", "data"),
    prevent_initial_call=True,
)
def handle_wallet_management_actions(_, wallet_admin_token):
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
def handle_add_wallet_modal(_, __, submit_clicks, is_open, raw_line, tier_name, wallet_admin_token):
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
        try:
            conn = get_connection()
            result = add_wallet_from_csv_line(conn, raw_line or "", tier_name)
            conn.close()
            return (
                False,
                dbc.Alert(f"Queued new wallet {result['wallet_address']} at {result['to_tier'].replace('_', ' ').title()}.", color="success"),
                "",
                wallet_admin_token + 1,
            )
        except Exception as exc:
            logger.exception("Failed to add wallet")
            return True, dbc.Alert(str(exc), color="danger"), no_update, no_update
    return is_open, no_update, no_update, no_update


@callback(
    Output("push-preview-modal", "is_open"),
    [Input("btn-open-push", "n_clicks"), Input("btn-cancel-push", "n_clicks"), Input("btn-confirm-push", "n_clicks")],
    State("push-preview-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_push_preview_modal(_, __, ___, is_open):
    triggered = dash.callback_context.triggered_id
    if triggered == "btn-open-push":
        return True
    if triggered in {"btn-cancel-push", "btn-confirm-push"}:
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
        return html.Div([_pending_change_card(change) for change in snapshot["pending_changes"]]), False
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
            f"{counts.get('test', 0)} wallets",
            f"{counts.get('promoted', 0)} wallets",
            f"{counts.get('high_conviction', 0)} wallets",
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
def toggle_revert_modal(_, __, ___, is_open):
    triggered = dash.callback_context.triggered_id
    if triggered == "btn-open-revert":
        return True
    if triggered in {"btn-cancel-revert", "btn-confirm-revert"}:
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
                        _stat_tile("Wallet", stats["wallet"]),
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
                    className="pm-wallet-meta-strip",
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


start_scheduler()
try:
    init_db()
except Exception as exc:
    logger.warning("Initial database bootstrap failed: %s", exc)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    app.run(host="0.0.0.0", port=port, debug=False)
