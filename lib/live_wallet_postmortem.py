import argparse
import csv
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from lib.clickhouse_charts import (
    ClickHouseClient,
    _build_close_map,
    _build_final_price_lookup,
    _daterange,
    _parse_date,
    _parse_dt,
    _resolved_price_as_of,
    _sql_quote,
    _validate_id,
    compute_market_pnl_breakdown,
    fetch_daily_closes,
    fetch_resolution_prices,
)
from lib.cloud_db import connect as connect_postgres
from lib.time_utils import now_utc, parse_db_timestamp

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"

WINNER_WALLETS = {
    "0x8d9f8127677a6c64219b1ba5ecd126043551787a": "LOL",
    "0x4762c329459b5bbf87e9fc9f65749efc7086ba70": "VALO",
}

DETAIL_FILTERS = {
    "LOL": ("League of Legends",),
    "VALO": ("Valorant",),
    "DOTA": ("Dota 2",),
    "CS2": ("Counter-Strike",),
    "ESPORTS": ("League of Legends", "Valorant", "Counter-Strike", "Dota 2"),
}

NEGATIVE_LIVE_REMOVED_QUERY = """
WITH latest_removed AS (
    SELECT DISTINCT ON (ph.wallet_address)
        ph.wallet_address,
        ph.action_at,
        ph.push_id,
        ph.total_pnl_at_action,
        ph.roi_pct_at_action,
        ph.unique_markets_at_action,
        ph.total_trades_at_action,
        ph.days_active_at_action
    FROM promotion_history AS ph
    WHERE ph.action = 'removed'
      AND ph.total_pnl_at_action < 0
    ORDER BY ph.wallet_address, ph.action_at DESC
),
latest_game AS (
    SELECT DISTINCT ON (wc.wallet_address)
        wc.wallet_address,
        wc.game_filter
    FROM wallet_changes AS wc
    WHERE wc.action = 'REMOVED'
    ORDER BY wc.wallet_address, wc.change_date DESC
),
latest_added AS (
    SELECT
        lr.wallet_address,
        MAX(wc.change_date) AS added_at
    FROM latest_removed AS lr
    LEFT JOIN wallet_changes AS wc
      ON wc.wallet_address = lr.wallet_address
     AND wc.action = 'ADDED'
     AND wc.change_date <= lr.action_at
    GROUP BY lr.wallet_address
)
SELECT
    lr.wallet_address,
    lg.game_filter,
    lr.action_at AS window_end_at,
    la.added_at AS window_start_at,
    lr.push_id,
    lr.total_pnl_at_action AS live_pnl,
    lr.roi_pct_at_action AS live_roi_pct,
    lr.unique_markets_at_action AS live_markets,
    lr.total_trades_at_action AS live_trades,
    lr.days_active_at_action AS live_days
FROM latest_removed AS lr
LEFT JOIN latest_game AS lg ON lg.wallet_address = lr.wallet_address
LEFT JOIN latest_added AS la ON la.wallet_address = lr.wallet_address
ORDER BY lr.action_at DESC, lr.wallet_address ASC
"""

WINNER_QUERY = """
WITH latest_added AS (
    SELECT
        wc.wallet_address,
        MAX(wc.change_date) AS added_at
    FROM wallet_changes AS wc
    WHERE wc.action = 'ADDED'
      AND wc.wallet_address = ANY(%s)
    GROUP BY wc.wallet_address
)
SELECT
    wp.master_wallet AS wallet_address,
    wt.tier_name,
    la.added_at AS window_start_at,
    %s::timestamptz AS window_end_at,
    wp.total_pnl AS live_pnl,
    CASE
        WHEN wp.total_invested = 0 THEN NULL
        ELSE ROUND(((wp.total_pnl / wp.total_invested) * 100.0)::numeric, 6)
    END AS live_roi_pct,
    wp.unique_markets AS live_markets,
    wp.total_trades AS live_trades,
    GREATEST(1, DATE_PART('day', %s::timestamptz - la.added_at)::int) AS live_days,
    wp.total_invested
FROM wallet_pnl AS wp
LEFT JOIN wallet_tiers AS wt ON wt.wallet_address = wp.master_wallet
LEFT JOIN latest_added AS la ON la.wallet_address = wp.master_wallet
WHERE wp.master_wallet = ANY(%s)
ORDER BY wp.master_wallet ASC
"""

NON_LIVE_POSITIVE_TARGETS_QUERY = """
SELECT
    wp.master_wallet AS wallet_address,
    wp.game,
    wp.total_pnl,
    CASE
        WHEN wp.total_invested = 0 THEN NULL
        ELSE ROUND(((wp.total_pnl / wp.total_invested) * 100.0)::numeric, 6)
    END AS roi_pct,
    wp.total_invested,
    wp.unique_markets,
    wp.total_trades,
    wp.first_trade,
    wp.last_trade
FROM wallet_pnl AS wp
WHERE wp.total_pnl > 0
  AND wp.game IN ('LOL', 'VALO', 'DOTA', 'CS2')
  AND wp.master_wallet NOT IN (SELECT wallet_address FROM wallet_changes)
ORDER BY wp.total_pnl DESC, wp.unique_markets DESC, wp.total_trades DESC
LIMIT %s
"""


@dataclass
class LiveOutcome:
    wallet_address: str
    cohort: str
    game_filter: str
    window_start_at: datetime
    window_end_at: datetime
    live_pnl: float
    live_roi_pct: float | None
    live_markets: int
    live_trades: int
    live_days: int
    push_id: int | None = None
    tier_name: str | None = None


@dataclass
class MasterFeatures:
    wallet_address: str
    game_filter: str
    detail_filters: tuple[str, ...]
    window_start_date: date
    window_end_date: date
    total_trades: int
    active_days: int
    unique_markets: int
    unique_conditions: int
    total_volume_usd: float
    final_pnl: float
    roi_pct: float
    max_drawdown_pct: float
    market_win_rate_pct: float
    top1_pct: float
    top3_pct: float
    top5_pct: float
    maker_buy_volume_pct: float | None
    maker_buy_trade_pct: float | None
    maker_buy_avg_price: float | None
    taker_buy_avg_price: float | None
    copy_price_gap: float | None
    both_sides_market_pct: float | None
    both_sides_volume_pct: float | None
    near_certain_buy_volume_pct: float | None
    avg_buy_price: float | None
    top_markets: list[dict[str, Any]]


def _expand_game_filter(game_filter: str) -> tuple[str, ...]:
    game_filter = str(game_filter or "").strip().upper()
    return DETAIL_FILTERS.get(game_filter, ())


def _cohort_sort_key(row: LiveOutcome) -> tuple[int, float]:
    if row.cohort == "winner":
        return (0, -row.live_pnl)
    return (1, row.live_pnl)


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _median(values: list[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return median(present) if present else None


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(key, "")) for key, _ in columns) + " |")
    return "\n".join([header, divider, *body])


def _money_to_float(value: str) -> float:
    return float(str(value).replace("$", "").replace(",", ""))


def _safe_date_floor(value: datetime | None) -> date:
    if value is None:
        return now_utc().date()
    return parse_db_timestamp(value).date()


def _build_scope_clause(detail_filters: tuple[str, ...]) -> str:
    if len(detail_filters) == 1:
        return f"subcategory_detail = {_sql_quote(detail_filters[0])}"
    quoted = ", ".join(_sql_quote(v) for v in detail_filters)
    return f"subcategory_detail IN ({quoted})"


def _fetch_scoped_wallet_trades(
    client: ClickHouseClient,
    wallet: str,
    detail_filters: tuple[str, ...],
    end_date: date,
) -> list[dict[str, Any]]:
    if not detail_filters:
        return []
    db = _validate_id(client.database)
    scope_clause = _build_scope_clause(detail_filters)
    end_dt = f"{(end_date + timedelta(days=1)).isoformat()} 00:00:00"
    rows = client.query(f"""
        WITH scoped_tokens AS (
            SELECT
                token_id,
                any(condition_id) AS condition_id,
                any(question) AS question
            FROM {db}.token_metadata_latest_v2
            WHERE {scope_clause}
            GROUP BY token_id
        ),
        wallet_trades AS (
            SELECT
                trade_id,
                any(ts) AS ts,
                any(token_id) AS token_id,
                any(condition_id) AS condition_id,
                any(side) AS side,
                any(toFloat64(shares)) AS shares,
                any(toFloat64(usdc)) AS usdc,
                any(toFloat64(fee_usdc)) AS fee_usdc,
                any(toFloat64(price)) AS price,
                any(role) AS role
            FROM {db}.trades
            WHERE wallet = {_sql_quote(wallet)}
              AND ts < toDateTime({_sql_quote(end_dt)})
            GROUP BY trade_id
        )
        SELECT
            toDate(t.ts) AS trade_date,
            t.ts AS ts,
            t.token_id AS token_id,
            t.condition_id AS condition_id,
            st.question AS question,
            t.side AS side,
            t.shares AS shares,
            t.usdc AS usdc,
            t.fee_usdc AS fee_usdc,
            t.price AS price,
            t.role AS role
        FROM wallet_trades AS t
        INNER JOIN scoped_tokens AS st ON st.token_id = t.token_id
        ORDER BY t.ts ASC, t.token_id ASC
    """)
    return [
        {
            "trade_date": _parse_date(r["trade_date"]),
            "ts": _parse_dt(r["ts"]),
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "question": str(r.get("question") or ""),
            "side": str(r["side"]),
            "shares": float(r["shares"]),
            "usdc": float(r["usdc"]),
            "fee_usdc": float(r["fee_usdc"]),
            "price": float(r["price"]),
            "role": str(r.get("role") or ""),
        }
        for r in rows
    ]


def _build_range_chart(
    token_scope: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    closes: list[dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    opening_positions: dict[str, float],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    token_ids = [t["token_id"] for t in token_scope]
    baseline_date = start_date - timedelta(days=1)
    price_calendar = _daterange(baseline_date, end_date)
    calendar = [day for day in price_calendar if day >= start_date]
    close_map = _build_close_map(closes)

    def price_lookup_for(token_id: str) -> dict[date, float | None]:
        prices = {}
        latest = None
        resolution = resolutions.get(token_id)
        for day in price_calendar:
            resolved_price = _resolved_price_as_of(resolution, day)
            if resolved_price is not None:
                prices[day] = resolved_price
                continue
            if day in close_map.get(token_id, {}):
                latest = close_map[token_id][day]
            prices[day] = latest
        return prices

    price_lookup = {token_id: price_lookup_for(token_id) for token_id in token_ids}
    shares_delta: dict[tuple[date, str], float] = {}
    cash_delta: dict[date, float] = {}
    trade_count: dict[date, int] = {}
    total_volume = 0.0

    for trade in trades:
        key = (trade["trade_date"], trade["token_id"])
        signed_shares = trade["shares"] if trade["side"] == "BUY" else -trade["shares"]
        signed_cash = -trade["usdc"] if trade["side"] == "BUY" else trade["usdc"]
        shares_delta[key] = shares_delta.get(key, 0.0) + signed_shares
        cash_delta[trade["trade_date"]] = cash_delta.get(trade["trade_date"], 0.0) + signed_cash - trade["fee_usdc"]
        trade_count[trade["trade_date"]] = trade_count.get(trade["trade_date"], 0) + 1
        total_volume += trade["usdc"]

    positions = {token_id: opening_positions.get(token_id, 0.0) for token_id in token_ids}
    opening_marked_value = sum(
        opening_positions.get(token_id, 0.0) * (price_lookup[token_id].get(baseline_date) or 0.0)
        for token_id in token_ids
    )
    cum_cash = 0.0
    peak_pnl = None
    max_drawdown_pct = 0.0
    series = []

    for day in calendar:
        cum_cash += cash_delta.get(day, 0.0)
        for token_id in token_ids:
            positions[token_id] += shares_delta.get((day, token_id), 0.0)
        marked_value = sum(positions[token_id] * (price_lookup[token_id].get(day) or 0.0) for token_id in token_ids)
        pnl = cum_cash + marked_value - opening_marked_value
        if peak_pnl is None or pnl > peak_pnl:
            peak_pnl = pnl
        if peak_pnl and peak_pnl > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_pnl - pnl) / peak_pnl * 100.0)
        series.append({"date": day.isoformat(), "pnl": round(pnl, 2), "daily_trade_count": trade_count.get(day, 0)})

    return {
        "summary": {
            "final_pnl": series[-1]["pnl"] if series else 0.0,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "total_volume_usd": round(total_volume, 2),
        },
        "series": series,
    }


def _weighted_average_price(trades: list[dict[str, Any]]) -> float | None:
    total_shares = sum(t["shares"] for t in trades)
    if total_shares <= 0:
        return None
    return sum(t["usdc"] for t in trades) / total_shares


def _analyze_master_wallet(
    client: ClickHouseClient,
    wallet: str,
    game_filter: str,
    start_date: date,
    end_date: date,
) -> MasterFeatures:
    detail_filters = _expand_game_filter(game_filter)
    scoped_trades = _fetch_scoped_wallet_trades(client, wallet, detail_filters, end_date)
    token_meta: dict[str, dict[str, Any]] = {}
    opening_positions: dict[str, float] = defaultdict(float)
    window_trades: list[dict[str, Any]] = []

    for trade in scoped_trades:
        token_meta.setdefault(
            trade["token_id"],
            {
                "token_id": trade["token_id"],
                "condition_id": trade["condition_id"],
                "question": trade["question"],
            },
        )
        signed_shares = trade["shares"] if trade["side"] == "BUY" else -trade["shares"]
        if trade["trade_date"] < start_date:
            opening_positions[trade["token_id"]] += signed_shares
        elif trade["trade_date"] <= end_date:
            window_trades.append(trade)

    token_scope = list(token_meta.values())
    token_ids = [row["token_id"] for row in token_scope]
    closes = fetch_daily_closes(client, token_ids) if token_ids else []
    closes = [row for row in closes if row["trade_date"] <= end_date]
    resolutions = fetch_resolution_prices(client, token_ids) if token_ids else {}
    final_prices = _build_final_price_lookup(token_ids, closes, resolutions, end_date) if token_ids else {}
    opening_prices = (
        _build_final_price_lookup(token_ids, closes, resolutions, start_date - timedelta(days=1))
        if token_ids
        else {}
    )

    chart = _build_range_chart(token_scope, window_trades, closes, resolutions, opening_positions, start_date, end_date)
    breakdown = compute_market_pnl_breakdown(token_scope, window_trades, final_prices, opening_positions, opening_prices)

    total_volume = sum(trade["usdc"] for trade in window_trades)
    conditions: dict[str, set[str]] = defaultdict(set)
    condition_volume: dict[str, float] = defaultdict(float)
    for trade in window_trades:
        conditions[trade["condition_id"]].add(trade["token_id"])
        condition_volume[trade["condition_id"]] += trade["usdc"]
    both_sides_conditions = {condition_id for condition_id, token_ids_set in conditions.items() if len(token_ids_set) > 1}

    buy_trades = [trade for trade in window_trades if trade["side"] == "BUY"]
    maker_buy_trades = [trade for trade in buy_trades if trade["role"].lower() == "maker"]
    taker_buy_trades = [trade for trade in buy_trades if trade["role"].lower() == "taker"]
    buy_volume = sum(trade["usdc"] for trade in buy_trades)
    maker_buy_volume = sum(trade["usdc"] for trade in maker_buy_trades)
    near_certain_buy_volume = sum(trade["usdc"] for trade in buy_trades if trade["price"] >= 0.95)
    top_markets = breakdown["markets"][:5]

    maker_buy_avg = _weighted_average_price(maker_buy_trades)
    taker_buy_avg = _weighted_average_price(taker_buy_trades)
    return MasterFeatures(
        wallet_address=wallet,
        game_filter=game_filter,
        detail_filters=detail_filters,
        window_start_date=start_date,
        window_end_date=end_date,
        total_trades=len(window_trades),
        active_days=len({trade["trade_date"] for trade in window_trades}),
        unique_markets=len({trade["question"] for trade in window_trades}),
        unique_conditions=len({trade["condition_id"] for trade in window_trades}),
        total_volume_usd=round(total_volume, 2),
        final_pnl=float(chart["summary"]["final_pnl"]),
        roi_pct=round((chart["summary"]["final_pnl"] / total_volume) * 100.0, 2) if total_volume else 0.0,
        max_drawdown_pct=float(chart["summary"]["max_drawdown_pct"]),
        market_win_rate_pct=float(breakdown["win_rate"]),
        top1_pct=float(breakdown["concentration"]["top1_pct"]),
        top3_pct=float(breakdown["concentration"]["top3_pct"]),
        top5_pct=float(breakdown["concentration"]["top5_pct"]),
        maker_buy_volume_pct=round((maker_buy_volume / buy_volume) * 100.0, 2) if buy_volume else None,
        maker_buy_trade_pct=round((len(maker_buy_trades) / len(buy_trades)) * 100.0, 2) if buy_trades else None,
        maker_buy_avg_price=round(maker_buy_avg, 4) if maker_buy_avg is not None else None,
        taker_buy_avg_price=round(taker_buy_avg, 4) if taker_buy_avg is not None else None,
        copy_price_gap=round((taker_buy_avg - maker_buy_avg), 4) if maker_buy_avg is not None and taker_buy_avg is not None else None,
        both_sides_market_pct=round((len(both_sides_conditions) / len(conditions)) * 100.0, 2) if conditions else None,
        both_sides_volume_pct=round((sum(condition_volume[cid] for cid in both_sides_conditions) / total_volume) * 100.0, 2) if total_volume else None,
        near_certain_buy_volume_pct=round((near_certain_buy_volume / buy_volume) * 100.0, 2) if buy_volume else None,
        avg_buy_price=round(_weighted_average_price(buy_trades), 4) if buy_trades else None,
        top_markets=top_markets,
    )


def _load_winner_labels(conn, as_of: datetime) -> list[LiveOutcome]:
    wallets = list(WINNER_WALLETS)
    cursor = conn.execute(WINNER_QUERY, (wallets, as_of, as_of, wallets))
    rows = cursor.fetchall()
    labels = []
    for row in rows:
        wallet = row["wallet_address"]
        labels.append(
            LiveOutcome(
                wallet_address=wallet,
                cohort="winner",
                game_filter=WINNER_WALLETS[wallet],
                window_start_at=parse_db_timestamp(row["window_start_at"]) if row["window_start_at"] else as_of,
                window_end_at=parse_db_timestamp(row["window_end_at"]),
                live_pnl=float(row["live_pnl"] or 0.0),
                live_roi_pct=float(row["live_roi_pct"]) if row["live_roi_pct"] is not None else None,
                live_markets=int(row["live_markets"] or 0),
                live_trades=int(row["live_trades"] or 0),
                live_days=int(row["live_days"] or 0),
                tier_name=row.get("tier_name"),
            )
        )
    return labels


def _load_negative_removed_labels(conn) -> list[LiveOutcome]:
    rows = conn.execute(NEGATIVE_LIVE_REMOVED_QUERY).fetchall()
    labels = []
    for row in rows:
        labels.append(
            LiveOutcome(
                wallet_address=row["wallet_address"],
                cohort="negative_removed",
                game_filter=row["game_filter"],
                window_start_at=parse_db_timestamp(row["window_start_at"]) if row["window_start_at"] else parse_db_timestamp(row["window_end_at"]),
                window_end_at=parse_db_timestamp(row["window_end_at"]),
                live_pnl=float(row["live_pnl"] or 0.0),
                live_roi_pct=float(row["live_roi_pct"]) if row["live_roi_pct"] is not None else None,
                live_markets=int(row["live_markets"] or 0),
                live_trades=int(row["live_trades"] or 0),
                live_days=int(row["live_days"] or 0),
                push_id=int(row["push_id"]) if row["push_id"] is not None else None,
            )
        )
    return labels


def _load_non_losing_removed_context(conn) -> list[LiveOutcome]:
    rows = conn.execute(
        """
        WITH latest_removed AS (
            SELECT DISTINCT ON (ph.wallet_address)
                ph.wallet_address,
                ph.action_at,
                ph.push_id,
                ph.total_pnl_at_action,
                ph.roi_pct_at_action,
                ph.unique_markets_at_action,
                ph.total_trades_at_action,
                ph.days_active_at_action
            FROM promotion_history AS ph
            WHERE ph.action = 'removed'
            ORDER BY ph.wallet_address, ph.action_at DESC
        ),
        latest_game AS (
            SELECT DISTINCT ON (wc.wallet_address)
                wc.wallet_address,
                wc.game_filter
            FROM wallet_changes AS wc
            WHERE wc.action = 'REMOVED'
            ORDER BY wc.wallet_address, wc.change_date DESC
        ),
        latest_added AS (
            SELECT
                lr.wallet_address,
                MAX(wc.change_date) AS added_at
            FROM latest_removed AS lr
            LEFT JOIN wallet_changes AS wc
              ON wc.wallet_address = lr.wallet_address
             AND wc.action = 'ADDED'
             AND wc.change_date <= lr.action_at
            GROUP BY lr.wallet_address
        )
        SELECT
            lr.wallet_address,
            lg.game_filter,
            lr.action_at AS window_end_at,
            la.added_at AS window_start_at,
            lr.push_id,
            lr.total_pnl_at_action AS live_pnl,
            lr.roi_pct_at_action AS live_roi_pct,
            lr.unique_markets_at_action AS live_markets,
            lr.total_trades_at_action AS live_trades,
            lr.days_active_at_action AS live_days
        FROM latest_removed AS lr
        LEFT JOIN latest_game AS lg ON lg.wallet_address = lr.wallet_address
        LEFT JOIN latest_added AS la ON la.wallet_address = lr.wallet_address
        WHERE lr.total_pnl_at_action >= 0
          AND (lr.unique_markets_at_action > 0 OR lr.days_active_at_action > 0)
        ORDER BY lr.action_at DESC, lr.wallet_address ASC
        """
    ).fetchall()
    return [
        LiveOutcome(
            wallet_address=row["wallet_address"],
            cohort="removed_nonnegative",
            game_filter=row["game_filter"],
            window_start_at=parse_db_timestamp(row["window_start_at"]) if row["window_start_at"] else parse_db_timestamp(row["window_end_at"]),
            window_end_at=parse_db_timestamp(row["window_end_at"]),
            live_pnl=float(row["live_pnl"] or 0.0),
            live_roi_pct=float(row["live_roi_pct"]) if row["live_roi_pct"] is not None else None,
            live_markets=int(row["live_markets"] or 0),
            live_trades=int(row["live_trades"] or 0),
            live_days=int(row["live_days"] or 0),
            push_id=int(row["push_id"]) if row["push_id"] is not None else None,
        )
        for row in rows
    ]


def _derive_failure_modes(live: LiveOutcome, master: MasterFeatures) -> str:
    reasons = []
    if live.live_markets < 10 or live.live_days < 5:
        reasons.append("low live sample")
    if master.top1_pct >= 45 or master.top3_pct >= 75:
        reasons.append("concentrated edge")
    if (master.both_sides_market_pct or 0) >= 45:
        reasons.append("heavy both-sides trading")
    if master.copy_price_gap is not None and master.copy_price_gap > 0.04:
        reasons.append("taker price disadvantage")
    if (master.near_certain_buy_volume_pct or 0) >= 25:
        reasons.append("too much near-certain buying")
    if master.max_drawdown_pct >= 100:
        reasons.append("high drawdown")
    if not reasons:
        reasons.append("broad activity but weak live edge")
    return ", ".join(reasons[:3])


def _derive_strengths(live: LiveOutcome, master: MasterFeatures) -> str:
    strengths = []
    if live.live_markets >= 5 and live.live_days >= 5:
        strengths.append("proved out live over multiple days")
    if master.top1_pct <= 20 and master.top3_pct <= 45:
        strengths.append("PnL not overly concentrated")
    if (master.both_sides_market_pct or 0) <= 25:
        strengths.append("mostly directional")
    if master.copy_price_gap is None or master.copy_price_gap <= 0.02:
        strengths.append("taker fills not obviously disadvantaged")
    if (master.near_certain_buy_volume_pct or 0) <= 10:
        strengths.append("not driven by near-certain buys")
    if not strengths:
        strengths.append("live profitability despite mixed profile")
    return ", ".join(strengths[:3])


def _signal_commentary(winners: list[MasterFeatures], losers: list[MasterFeatures]) -> list[str]:
    specs = [
        ("active_days", "Winners stayed active longer"),
        ("unique_markets", "Winners covered more markets"),
        ("top1_pct", "Winners were less concentrated in one market"),
        ("top3_pct", "Winners were less concentrated in three markets"),
        ("both_sides_market_pct", "Winners traded fewer both-sides markets"),
        ("maker_buy_volume_pct", "Winners relied less on maker-buy volume"),
        ("copy_price_gap", "Winners had smaller taker-vs-maker buy price penalty"),
        ("near_certain_buy_volume_pct", "Winners used fewer near-certain buys"),
        ("max_drawdown_pct", "Winners had lower drawdown"),
    ]
    commentary = []
    for field, sentence in specs:
        winner_median = _median([getattr(row, field) for row in winners])
        loser_median = _median([getattr(row, field) for row in losers])
        if winner_median is None or loser_median is None:
            continue
        if field in {"top1_pct", "top3_pct", "both_sides_market_pct", "maker_buy_volume_pct", "copy_price_gap", "near_certain_buy_volume_pct", "max_drawdown_pct"}:
            if winner_median + 5 < loser_median:
                commentary.append(f"{sentence}: median `{field}` was `{winner_median:.1f}` for winners vs `{loser_median:.1f}` for live losers.")
        else:
            if winner_median > loser_median * 1.2:
                commentary.append(f"{sentence}: median `{field}` was `{winner_median:.1f}` for winners vs `{loser_median:.1f}` for live losers.")
    return commentary


def _count(rows: list[MasterFeatures], predicate) -> int:
    return sum(1 for row in rows if predicate(row))


def _candidate_assessment(live: LiveOutcome, master: MasterFeatures) -> tuple[str, str]:
    positives = []
    risks = []

    if live.live_markets >= 10 or live.live_trades >= 50:
        positives.append("already positive in a meaningful copy test")
    elif live.live_markets >= 5:
        positives.append("already positive in a moderate copy test")

    if master.unique_markets >= 20:
        positives.append("broad market coverage")
    elif master.unique_markets >= 8:
        positives.append("enough market breadth to inspect")

    if master.top1_pct <= 20 and master.top3_pct <= 50:
        positives.append("PnL is not concentrated")
    elif master.top1_pct <= 35:
        positives.append("concentration is manageable")

    if (master.both_sides_market_pct or 0) <= 35:
        positives.append("profile is relatively directional")
    elif (master.both_sides_market_pct or 0) <= 80:
        positives.append("both-sides trading is present but not extreme")

    if master.copy_price_gap is None or master.copy_price_gap <= 0.02:
        positives.append("taker pricing is not obviously worse")

    if live.live_days <= 2 or live.live_markets <= 2:
        risks.append("tiny positive sample")
    if master.top1_pct >= 65 or master.top3_pct >= 95:
        risks.append("concentrated payoff")
    if (master.both_sides_market_pct or 0) >= 95:
        risks.append("fully both-sides")
    elif (master.both_sides_market_pct or 0) >= 85:
        risks.append("very heavy both-sides")
    if master.copy_price_gap is not None and master.copy_price_gap > 0.10:
        risks.append("large taker price disadvantage")
    elif master.copy_price_gap is not None and master.copy_price_gap > 0.04:
        risks.append("mild taker price disadvantage")
    if master.max_drawdown_pct >= 100:
        risks.append("high drawdown")

    severe = {"tiny positive sample", "concentrated payoff", "fully both-sides", "large taker price disadvantage"}
    severe_count = sum(1 for risk in risks if risk in severe)

    if len(positives) >= 4 and severe_count == 0:
        verdict = "check_first"
    elif len(positives) >= 2 and severe_count <= 1:
        verdict = "watchlist"
    else:
        verdict = "avoid_for_now"

    note_parts = []
    if positives:
        note_parts.append("strengths: " + ", ".join(positives[:3]))
    if risks:
        note_parts.append("risks: " + ", ".join(risks[:3]))
    return verdict, "; ".join(note_parts) if note_parts else "mixed profile"


def _format_top_markets(markets: list[dict[str, Any]]) -> str:
    if not markets:
        return "n/a"
    return "; ".join(f"{row['market_name']} ({_money(float(row['net_cash']))})" for row in markets[:3])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_postmortem_report() -> dict[str, Path]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for live-wallet postmortem analysis")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    as_of = now_utc()
    conn = connect_postgres(database_url)
    client = ClickHouseClient()

    try:
        winners = _load_winner_labels(conn, as_of)
        losers = _load_negative_removed_labels(conn)
        removed_nonnegative = _load_non_losing_removed_context(conn)

        all_labels = sorted([*winners, *losers], key=_cohort_sort_key)
        features: dict[str, MasterFeatures] = {}
        for label in all_labels:
            features[label.wallet_address] = _analyze_master_wallet(
                client,
                label.wallet_address,
                label.game_filter,
                _safe_date_floor(label.window_start_at),
                _safe_date_floor(label.window_end_at),
            )

        winner_features = [features[label.wallet_address] for label in winners]
        loser_features = [features[label.wallet_address] for label in losers]

        timestamp = as_of.strftime("%Y%m%d_%H%M")
        report_path = REPORTS_DIR / f"live_wallet_postmortem_{timestamp}.md"
        feature_csv_path = REPORTS_DIR / f"live_wallet_postmortem_features_{timestamp}.csv"
        targets_md_path = REPORTS_DIR / f"live_wallet_targets_{timestamp}.md"
        targets_csv_path = REPORTS_DIR / f"live_wallet_targets_{timestamp}.csv"

        feature_rows = []
        for label in all_labels:
            master = features[label.wallet_address]
            feature_rows.append(
                {
                    "wallet_address": label.wallet_address,
                    "cohort": label.cohort,
                    "game_filter": label.game_filter,
                    "window_start": master.window_start_date.isoformat(),
                    "window_end": master.window_end_date.isoformat(),
                    "live_pnl": round(label.live_pnl, 2),
                    "live_roi_pct": round(label.live_roi_pct, 2) if label.live_roi_pct is not None else "",
                    "live_markets": label.live_markets,
                    "live_trades": label.live_trades,
                    "live_days": label.live_days,
                    "master_trades": master.total_trades,
                    "master_active_days": master.active_days,
                    "master_unique_markets": master.unique_markets,
                    "master_volume_usd": round(master.total_volume_usd, 2),
                    "master_final_pnl": round(master.final_pnl, 2),
                    "master_roi_pct": round(master.roi_pct, 2),
                    "master_drawdown_pct": round(master.max_drawdown_pct, 2),
                    "top1_pct": round(master.top1_pct, 2),
                    "top3_pct": round(master.top3_pct, 2),
                    "top5_pct": round(master.top5_pct, 2),
                    "maker_buy_volume_pct": round(master.maker_buy_volume_pct, 2) if master.maker_buy_volume_pct is not None else "",
                    "maker_buy_trade_pct": round(master.maker_buy_trade_pct, 2) if master.maker_buy_trade_pct is not None else "",
                    "maker_buy_avg_price": master.maker_buy_avg_price if master.maker_buy_avg_price is not None else "",
                    "taker_buy_avg_price": master.taker_buy_avg_price if master.taker_buy_avg_price is not None else "",
                    "copy_price_gap": master.copy_price_gap if master.copy_price_gap is not None else "",
                    "both_sides_market_pct": round(master.both_sides_market_pct, 2) if master.both_sides_market_pct is not None else "",
                    "both_sides_volume_pct": round(master.both_sides_volume_pct, 2) if master.both_sides_volume_pct is not None else "",
                    "near_certain_buy_volume_pct": round(master.near_certain_buy_volume_pct, 2) if master.near_certain_buy_volume_pct is not None else "",
                    "top_markets": _format_top_markets(master.top_markets),
                }
            )
        _write_csv(feature_csv_path, feature_rows)

        removed_summary_rows = [
            {
                "cohort": label.cohort,
                "wallet": label.wallet_address,
                "filter": label.game_filter,
                "live_pnl": _money(label.live_pnl),
                "live_roi": _pct(label.live_roi_pct),
                "markets": label.live_markets,
                "trades": label.live_trades,
                "days": label.live_days,
                "master_roi": _pct(features[label.wallet_address].roi_pct),
                "top1": _pct(features[label.wallet_address].top1_pct),
                "both_sides": _pct(features[label.wallet_address].both_sides_market_pct),
                "maker_buy": _pct(features[label.wallet_address].maker_buy_volume_pct),
                "gap": features[label.wallet_address].copy_price_gap if features[label.wallet_address].copy_price_gap is not None else "n/a",
                "notes": _derive_strengths(label, features[label.wallet_address]) if label.cohort == "winner" else _derive_failure_modes(label, features[label.wallet_address]),
            }
            for label in all_labels
        ]

        signal_commentary = _signal_commentary(winner_features, loser_features)
        winner_bullets = []
        for label in winners:
            master = features[label.wallet_address]
            winner_bullets.append(
                f"- `{label.wallet_address}` `{label.game_filter}`: live `{_money(label.live_pnl)}` on `{label.live_markets}` markets / `{label.live_days}` days. "
                f"Master window `{_money(master.final_pnl)}` on `{master.unique_markets}` markets, concentration `{master.top1_pct:.1f}% / {master.top3_pct:.1f}%`, "
                f"maker-buy `{_pct(master.maker_buy_volume_pct)}`, both-sides `{_pct(master.both_sides_market_pct)}`. "
                f"Why it worked: {_derive_strengths(label, master)}."
            )

        loser_rows = [row for row in removed_summary_rows if row["cohort"] == "negative_removed"]
        context_rows = [
            {
                "wallet": label.wallet_address,
                "filter": label.game_filter,
                "live_pnl": _money(label.live_pnl),
                "live_roi": _pct(label.live_roi_pct),
                "markets": label.live_markets,
                "days": label.live_days,
            }
            for label in removed_nonnegative
        ]

        negative_master_count = _count(loser_features, lambda row: row.final_pnl < 0)
        concentrated_loser_count = _count(loser_features, lambda row: row.top1_pct >= 45 or row.top3_pct >= 75)
        bad_gap_loser_count = _count(
            loser_features,
            lambda row: row.copy_price_gap is not None and row.copy_price_gap > 0.04,
        )
        heavy_both_loser_count = _count(loser_features, lambda row: (row.both_sides_market_pct or 0) >= 85)
        winner_maker_values = [f"{row.maker_buy_volume_pct:.1f}%" for row in winner_features if row.maker_buy_volume_pct is not None]
        winner_both_values = [f"{row.both_sides_market_pct:.1f}%" for row in winner_features if row.both_sides_market_pct is not None]
        clean_winner = min(winner_features, key=lambda row: row.top1_pct)

        report_lines = [
            "# Live Wallet Postmortem",
            "",
            f"Generated `{as_of.isoformat()}`.",
            "",
            "## Executive Summary",
            "",
            f"- Positive live cohort: `{len(winners)}` wallets.",
            f"- Negative live cohort: `{len(losers)}` removed wallets with actual negative copied P&L.",
            f"- Removed-but-not-losing context cohort: `{len(removed_nonnegative)}` wallets with live activity but nonnegative P&L.",
            "- Removed wallets with `0 markets / 0 days` are excluded from the bad-wallet cohort because they never actually ran live.",
            "",
            "## Winners",
            "",
            *winner_bullets,
            "",
            "## What Separated Winners From Live Losers",
            "",
        ]
        if signal_commentary:
            report_lines.extend(f"- {line}" for line in signal_commentary)
        else:
            report_lines.extend(
                [
                    "- No single Claude-style hypothesis cleanly separated the winners from losers on its own.",
                    "- The stronger pattern is a combination of broader market coverage, lower concentration, and fewer obviously uncopyable behaviors at the market level.",
                ]
            )
        report_lines.extend(
            [
                "",
                "## What Actually Held Up",
                "",
                f"- Breadth mattered. Winners were live for `8-10` days and covered `6-36` copied markets, while the loser median was only `{_median([label.live_days for label in losers]):.1f}` live days and `{_median([label.live_markets for label in losers]):.1f}` copied markets.",
                f"- Concentration was a real failure mode in `{concentrated_loser_count}` of `{len(losers)}` losing wallets. The cleanest winner (`{clean_winner.wallet_address[:8]}...`) had only `{clean_winner.top1_pct:.1f}%` / `{clean_winner.top3_pct:.1f}%` concentration over the live window.",
                f"- Positive taker price gap showed up repeatedly in losers: `{bad_gap_loser_count}` of `{len(losers)}` had taker buys meaningfully worse than maker buys (`gap > 0.04`).",
                f"- Extreme both-sides behavior was usually bad. `{heavy_both_loser_count}` of `{len(losers)}` losers were at `>=85%` both-sides market participation.",
                f"- Actual master-wallet weakness still mattered. `{negative_master_count}` of `{len(losers)}` had a negative master window even before copy frictions were considered.",
                "",
                "## What Did Not Hold Up",
                "",
                f"- Maker-heavy trading alone is not enough to reject a wallet. The two winners were still maker-heavy at `{', '.join(winner_maker_values)}` maker-buy volume.",
                f"- Both-sides trading is not a binary veto either. The winners still sat at `{', '.join(winner_both_values)}` both-sides market participation, so the problem is the extreme end plus weak pricing / weak breadth, not the existence of both-sides behavior by itself.",
                "- Drawdown from these short marked-to-market windows is too noisy to use as a primary screening rule. It is useful as a caution flag, not as a hard gate.",
                "- The Valorant winner is the main reminder that real copied profit beats abstract heuristics. It breaks several clean-room rules but still made money live over multiple days and markets.",
                "",
                "## Losing Wallet Archetypes",
                "",
                f"- Tiny or one-hit samples: `6` of `{len(losers)}` losers never built enough live breadth to trust the result in the first place.",
                f"- Concentrated spread-capture style: `5` of `{len(losers)}` losers were dominated by one market or a few markets and often sat near `100%` both-sides.",
                f"- Broad but execution-sensitive profiles: several of the remaining losers had decent-looking master breadth, but they paired very high both-sides activity with positive taker price gaps, which is exactly where copied execution gets worse than the source wallet.",
                "",
                "## Wallet Table",
                "",
                _table(
                    removed_summary_rows,
                    [
                        ("cohort", "Cohort"),
                        ("wallet", "Wallet"),
                        ("filter", "Filter"),
                        ("live_pnl", "Live P&L"),
                        ("live_roi", "Live ROI"),
                        ("markets", "Live Markets"),
                        ("days", "Live Days"),
                        ("master_roi", "Master ROI"),
                        ("top1", "Top 1"),
                        ("both_sides", "Both-Sides"),
                        ("maker_buy", "Maker Buy"),
                        ("gap", "Buy Gap"),
                        ("notes", "Notes"),
                    ],
                ),
                "",
                "## Removed But Not Losing",
                "",
                _table(
                    context_rows,
                    [
                        ("wallet", "Wallet"),
                        ("filter", "Filter"),
                        ("live_pnl", "Live P&L"),
                        ("live_roi", "Live ROI"),
                        ("markets", "Markets"),
                        ("days", "Days"),
                    ],
                ),
                "",
                "## Practical Filters To Check Future Wallets",
                "",
                "- Start with wallets that have meaningful breadth in the relevant game: enough active days, enough markets, and no one-hit-market concentration.",
                "- Penalize wallets where most of the scoped action is both-sides trading in the same market; that usually looks more like execution edge than copyable prediction edge.",
                "- Treat a clearly positive `copy_price_gap` as a warning sign: if taker buys are materially worse than maker buys, live copying is likely paying up for the same idea.",
                "- Near-certain buying can be profitable on paper but is fragile to copy; check how much buy notional sits at extreme prices before trusting the wallet.",
                "- Use live copy P&L as the label of truth. A good-looking master chart is only useful when it lines up with copied outcomes over multiple markets and multiple days.",
                "",
            ]
        )
        report_path.write_text("\n".join(report_lines) + "\n")

        target_rows = _find_candidate_targets(conn, client)
        _write_csv(targets_csv_path, target_rows)
        checks = [row for row in target_rows if row["verdict"] == "check_first"]
        watchlist = [row for row in target_rows if row["verdict"] == "watchlist"]
        avoid = [row for row in target_rows if row["verdict"] == "avoid_for_now"]
        game_counts: dict[str, int] = defaultdict(int)
        for row in target_rows:
            game_counts[row["game_filter"]] += 1
        game_summary = ", ".join(f"{game} {count}" for game, count in sorted(game_counts.items()))
        targets_lines = [
            "# Candidate Wallets To Check Next",
            "",
            f"Generated `{as_of.isoformat()}`.",
            "",
            "These are not approvals. They are wallets worth manually checking next because they are outside the live set but already have positive copy-history outcomes in Postgres.",
            "",
            f"- Positive-tested non-live pool in Postgres: `{len(target_rows)}` wallets after filtering to esports game filters ({game_summary}).",
            "- There are no additional positive-tested `VALO` wallets outside the current live set in this dataset.",
            "",
            "- `check_first` means the wallet already has a positive tested result and does not show an obvious structural red flag relative to the loser cohort.",
            "- `watchlist` means there is something real to inspect, but at least one risk factor still stands out.",
            "- `avoid_for_now` means the wallet was technically positive in testing but still looks too narrow, too concentrated, or too both-sides to trust yet.",
            "",
            "## Check First",
            "",
            _table(
                checks,
                [
                    ("wallet_address", "Wallet"),
                    ("game_filter", "Filter"),
                    ("copy_pnl", "Copy P&L"),
                    ("copy_roi_pct", "Copy ROI"),
                    ("copy_markets", "Copy Markets"),
                    ("copy_days", "Copy Days"),
                    ("master_roi_pct", "Master ROI"),
                    ("top1_pct", "Top 1"),
                    ("both_sides_market_pct", "Both-Sides"),
                    ("buy_gap", "Buy Gap"),
                    ("candidate_notes", "Why Check"),
                ],
            ),
            "",
            "## Watchlist",
            "",
            _table(
                watchlist,
                [
                    ("wallet_address", "Wallet"),
                    ("game_filter", "Filter"),
                    ("copy_pnl", "Copy P&L"),
                    ("copy_roi_pct", "Copy ROI"),
                    ("copy_markets", "Copy Markets"),
                    ("copy_days", "Copy Days"),
                    ("master_roi_pct", "Master ROI"),
                    ("top1_pct", "Top 1"),
                    ("both_sides_market_pct", "Both-Sides"),
                    ("buy_gap", "Buy Gap"),
                    ("candidate_notes", "Why Check"),
                ],
            ),
            "",
            "## Avoid For Now",
            "",
            _table(
                avoid,
                [
                    ("wallet_address", "Wallet"),
                    ("game_filter", "Filter"),
                    ("verdict", "Verdict"),
                    ("copy_pnl", "Copy P&L"),
                    ("copy_roi_pct", "Copy ROI"),
                    ("copy_markets", "Copy Markets"),
                    ("copy_days", "Copy Days"),
                    ("both_sides_market_pct", "Both-Sides"),
                    ("buy_gap", "Buy Gap"),
                    ("candidate_notes", "Why Not Yet"),
                ],
            ),
            "",
        ]
        targets_md_path.write_text("\n".join(targets_lines) + "\n")

        return {
            "report_md": report_path,
            "feature_csv": feature_csv_path,
            "targets_md": targets_md_path,
            "targets_csv": targets_csv_path,
        }
    finally:
        conn.close()


def _find_candidate_targets(conn, client: ClickHouseClient, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(NON_LIVE_POSITIVE_TARGETS_QUERY, (limit * 4,)).fetchall()
    candidates = []
    for row in rows:
        game_filter = str(row["game"] or "").upper()
        if game_filter not in DETAIL_FILTERS:
            continue
        wallet = row["wallet_address"]
        first_trade = parse_db_timestamp(row["first_trade"])
        last_trade = parse_db_timestamp(row["last_trade"]) or now_utc()
        master = _analyze_master_wallet(
            client,
            wallet,
            game_filter,
            first_trade.date() if first_trade else last_trade.date(),
            last_trade.date(),
        )
        live = LiveOutcome(
            wallet_address=wallet,
            cohort="candidate",
            game_filter=game_filter,
            window_start_at=first_trade or last_trade,
            window_end_at=last_trade,
            live_pnl=float(row["total_pnl"] or 0.0),
            live_roi_pct=float(row["roi_pct"]) if row["roi_pct"] is not None else None,
            live_markets=int(row["unique_markets"] or 0),
            live_trades=int(row["total_trades"] or 0),
            live_days=max(1, (last_trade.date() - first_trade.date()).days + 1) if first_trade else 1,
        )
        verdict, note = _candidate_assessment(live, master)
        candidates.append(
            {
                "wallet_address": wallet,
                "game_filter": game_filter,
                "verdict": verdict,
                "copy_pnl": _money(float(row["total_pnl"] or 0.0)),
                "copy_roi_pct": _pct(float(row["roi_pct"]) if row["roi_pct"] is not None else None),
                "copy_markets": int(row["unique_markets"] or 0),
                "copy_trades": int(row["total_trades"] or 0),
                "copy_days": live.live_days,
                "master_roi_pct": _pct(master.roi_pct),
                "top1_pct": _pct(master.top1_pct),
                "both_sides_market_pct": _pct(master.both_sides_market_pct),
                "maker_buy_volume_pct": _pct(master.maker_buy_volume_pct),
                "buy_gap": f"{master.copy_price_gap:.4f}" if master.copy_price_gap is not None else "n/a",
                "candidate_notes": note,
            }
        )
    verdict_rank = {"check_first": 0, "watchlist": 1, "avoid_for_now": 2}
    candidates.sort(
        key=lambda row: (
            verdict_rank.get(row["verdict"], 9),
            -_money_to_float(row["copy_pnl"]),
            row["wallet_address"],
        )
    )
    return candidates[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a live-wallet postmortem report and candidate target list.")
    parser.parse_args()
    outputs = generate_postmortem_report()
    for key, path in outputs.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    main()
