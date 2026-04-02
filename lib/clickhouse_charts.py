"""ClickHouse-backed game-specific wallet P&L chart builder.

Adapted from wallet_game_chart.py reference implementation.
Queries ClickHouse for trades, daily closes, and resolution prices,
then reconstructs a daily marked-to-market P&L series.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "jake")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.environ.get("CLICKHOUSE_DATABASE", "polymarket")


def _validate_id(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Unsafe identifier: {identifier!r}")
    return identifier


def _sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    raise ValueError(f"Could not parse datetime: {value!r}")


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return _parse_dt(value).date()


def _daterange(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _lookback_window_start(end_date: date, lookback_days: int) -> date:
    return end_date - timedelta(days=max(int(lookback_days), 0))


def _build_close_map(closes: list[dict[str, Any]]) -> dict[str, dict[date, float]]:
    close_map: dict[str, dict[date, float]] = {}
    for c in closes:
        close_map.setdefault(c["token_id"], {})[c["trade_date"]] = c["close_price"]
    return close_map


def _resolved_price_as_of(resolution: dict[str, Any] | None, as_of_date: date) -> float | None:
    if not resolution:
        return None
    resolved_ts = resolution.get("resolved_ts")
    if not resolved_ts or resolved_ts.date() > as_of_date:
        return None
    return float(resolution["price"])


def _latest_close_as_of(token_id: str, close_map: dict[str, dict[date, float]], as_of_date: date) -> float:
    latest = None
    for close_day, close_price in sorted(close_map.get(token_id, {}).items()):
        if close_day > as_of_date:
            break
        latest = close_price
    return float(latest) if latest is not None else 0.0


def _build_final_price_lookup(token_ids: list[str], closes: list[dict[str, Any]],
                              resolutions: dict[str, dict[str, Any]],
                              as_of_date: date) -> dict[str, float]:
    close_map = _build_close_map(closes)
    final_prices = {}
    for token_id in token_ids:
        resolved_price = _resolved_price_as_of(resolutions.get(token_id), as_of_date)
        if resolved_price is not None:
            final_prices[token_id] = resolved_price
        else:
            final_prices[token_id] = _latest_close_as_of(token_id, close_map, as_of_date)
    return final_prices


def _weighted_average_trade_price(trades: list[dict[str, Any]]) -> float | None:
    total_shares = sum(float(t.get("shares") or 0.0) for t in trades)
    if total_shares <= 0:
        return None
    total_usdc = sum(float(t.get("usdc") or 0.0) for t in trades)
    return total_usdc / total_shares


def _severity_from_thresholds(value: float | None, amber: float, red: float) -> str:
    if value is None:
        return "green"
    if value >= red:
        return "red"
    if value >= amber:
        return "amber"
    return "green"


def _build_curation_metric_severities(active_days: int, unique_markets: int,
                                      top1_pct: float, top3_pct: float,
                                      both_sides_market_pct: float,
                                      copy_price_gap: float | None,
                                      near_certain_buy_volume_pct: float) -> dict[str, str]:
    sample = "green"
    if unique_markets < 10 or active_days < 5:
        sample = "red"
    elif unique_markets < 20 or active_days < 7:
        sample = "amber"

    concentration = "green"
    if top1_pct >= 45 or top3_pct >= 75:
        concentration = "red"
    elif top1_pct >= 25 or top3_pct >= 60:
        concentration = "amber"

    return {
        "sample": sample,
        "concentration": concentration,
        "top1_pct": _severity_from_thresholds(top1_pct, 25, 45),
        "top3_pct": _severity_from_thresholds(top3_pct, 60, 75),
        "both_sides_market_pct": _severity_from_thresholds(both_sides_market_pct, 70, 85),
        "copy_price_gap": _severity_from_thresholds(copy_price_gap, 0.02, 0.04),
        "near_certain_buy_volume_pct": _severity_from_thresholds(near_certain_buy_volume_pct, 10, 25),
    }


def _build_curation_warning_chips(active_days: int, unique_markets: int,
                                  top1_pct: float, top3_pct: float,
                                  both_sides_market_pct: float,
                                  copy_price_gap: float | None,
                                  near_certain_buy_volume_pct: float) -> tuple[list[dict[str, str]], dict[str, str]]:
    severities = _build_curation_metric_severities(
        active_days,
        unique_markets,
        top1_pct,
        top3_pct,
        both_sides_market_pct,
        copy_price_gap,
        near_certain_buy_volume_pct,
    )
    chips = []

    if severities["sample"] != "green":
        chips.append({
            "key": "low_sample",
            "label": "Low sample",
            "severity": severities["sample"],
            "detail": f"{unique_markets} markets / {active_days} days",
        })
    if severities["concentration"] != "green":
        chips.append({
            "key": "concentrated_edge",
            "label": "Concentrated",
            "severity": severities["concentration"],
            "detail": f"top1 {top1_pct:.1f}%, top3 {top3_pct:.1f}%",
        })
    if severities["both_sides_market_pct"] != "green":
        chips.append({
            "key": "heavy_both_sides",
            "label": "Both-sides",
            "severity": severities["both_sides_market_pct"],
            "detail": f"{both_sides_market_pct:.1f}%",
        })
    if severities["copy_price_gap"] != "green" and copy_price_gap is not None:
        chips.append({
            "key": "taker_price_disadvantage",
            "label": "Buy gap",
            "severity": severities["copy_price_gap"],
            "detail": f"{copy_price_gap:+.4f}",
        })
    if severities["near_certain_buy_volume_pct"] != "green":
        chips.append({
            "key": "near_certain_buying",
            "label": "Near-certain buys",
            "severity": severities["near_certain_buy_volume_pct"],
            "detail": f"{near_certain_buy_volume_pct:.1f}%",
        })

    return chips, severities


def build_curation_signals(token_scope: list[dict[str, Any]], trades: list[dict[str, Any]],
                           breakdown: dict[str, Any]) -> dict[str, Any]:
    token_to_question = {t["token_id"]: t.get("question", "Unknown") for t in token_scope}
    active_days = len({t["trade_date"] for t in trades})
    unique_markets = len({token_to_question.get(t["token_id"], "Unknown") for t in trades}) if trades else 0

    conditions: dict[str, set[str]] = {}
    for trade in trades:
        conditions.setdefault(str(trade["condition_id"]), set()).add(str(trade["token_id"]))
    both_sides_count = sum(1 for token_ids in conditions.values() if len(token_ids) > 1)
    both_sides_market_pct = round((both_sides_count / len(conditions)) * 100.0, 1) if conditions else 0.0

    buy_trades = [trade for trade in trades if trade["side"] == "BUY"]
    maker_buy_trades = [trade for trade in buy_trades if str(trade.get("role") or "").lower() == "maker"]
    taker_buy_trades = [trade for trade in buy_trades if str(trade.get("role") or "").lower() == "taker"]
    maker_buy_avg = _weighted_average_trade_price(maker_buy_trades)
    taker_buy_avg = _weighted_average_trade_price(taker_buy_trades)
    copy_price_gap = round(taker_buy_avg - maker_buy_avg, 4) if maker_buy_avg is not None and taker_buy_avg is not None else None

    buy_volume = sum(float(trade["usdc"]) for trade in buy_trades)
    near_certain_buy_volume = sum(float(trade["usdc"]) for trade in buy_trades if float(trade.get("price") or 0.0) >= 0.95)
    near_certain_buy_volume_pct = round((near_certain_buy_volume / buy_volume) * 100.0, 1) if buy_volume else 0.0

    concentration = breakdown.get("concentration", {})
    top1_pct = float(concentration.get("top1_pct") or 0.0)
    top3_pct = float(concentration.get("top3_pct") or 0.0)
    top5_pct = float(concentration.get("top5_pct") or 0.0)
    warning_chips, metric_severities = _build_curation_warning_chips(
        active_days,
        unique_markets,
        top1_pct,
        top3_pct,
        both_sides_market_pct,
        copy_price_gap,
        near_certain_buy_volume_pct,
    )

    return {
        "active_days": active_days,
        "unique_markets": unique_markets,
        "top1_pct": top1_pct,
        "top3_pct": top3_pct,
        "top5_pct": top5_pct,
        "both_sides_market_pct": both_sides_market_pct,
        "copy_price_gap": copy_price_gap,
        "near_certain_buy_volume_pct": near_certain_buy_volume_pct,
        "metric_severities": metric_severities,
        "warning_chips": warning_chips,
    }


class ClickHouseClient:
    def __init__(self, url=None, user=None, password=None, database=None, timeout=120.0):
        self.url = url or CLICKHOUSE_URL
        self.user = user or CLICKHOUSE_USER
        self.password = password or CLICKHOUSE_PASSWORD
        self.database = database or CLICKHOUSE_DATABASE
        self.timeout = timeout

    def query(self, sql: str) -> list[dict[str, Any]]:
        response = requests.post(
            self.url,
            params={"database": self.database},
            data=f"{sql}\nFORMAT JSON",
            auth=(self.user, self.password) if self.user else None,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ClickHouse error {response.status_code}: {response.text.strip()[:300]}")
        return response.json().get("data", [])

    def is_available(self) -> bool:
        try:
            self.query("SELECT 1")
            return True
        except Exception:
            return False


def get_available_filters(client: ClickHouseClient) -> list[dict]:
    """Query all three filter levels: category, subcategory, and subcategory_detail."""
    db = _validate_id(client.database)

    # Categories (Sports, Crypto, Politics...)
    cat_rows = client.query(f"""
        SELECT category AS label, count() AS token_count
        FROM {db}.token_metadata_latest_v2
        WHERE category != ''
        GROUP BY category ORDER BY token_count DESC
    """)

    # Subcategories (Tennis, NBA, Esports, US Politics...)
    sub_rows = client.query(f"""
        SELECT subcategory AS label, count() AS token_count
        FROM {db}.token_metadata_latest_v2
        WHERE subcategory != ''
        GROUP BY subcategory ORDER BY token_count DESC
    """)

    # Details (Counter-Strike, Valorant, Lakers...)
    det_rows = client.query(f"""
        SELECT subcategory_detail AS label, count() AS token_count
        FROM {db}.token_metadata_latest_v2
        WHERE subcategory_detail != ''
        GROUP BY subcategory_detail ORDER BY token_count DESC
    """)

    filters = []
    for r in cat_rows:
        filters.append({"label": r["label"], "count": int(r["token_count"]), "level": "category"})
    for r in sub_rows:
        filters.append({"label": r["label"], "count": int(r["token_count"]), "level": "subcategory"})
    for r in det_rows:
        filters.append({"label": r["label"], "count": int(r["token_count"]), "level": "detail"})

    return filters


def fetch_token_scope(client: ClickHouseClient, wallet: str, filter_value: str, filter_level: str,
                      window_start_date: date) -> list[dict]:
    db = _validate_id(client.database)
    if filter_level == "category":
        scope_clause = f"category = {_sql_quote(filter_value)}"
    elif filter_level == "subcategory":
        scope_clause = f"subcategory = {_sql_quote(filter_value)}"
    else:
        scope_clause = f"subcategory_detail = {_sql_quote(filter_value)}"
    window_start_dt = f"{window_start_date.isoformat()} 00:00:00"
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
                any(toFloat64(shares)) AS shares
            FROM {db}.trades
            WHERE wallet = {_sql_quote(wallet)}
            GROUP BY trade_id
        )
        SELECT
            t.token_id AS token_id,
            any(st.condition_id) AS condition_id,
            any(st.question) AS question,
            nullIf(minIf(t.ts, t.ts >= toDateTime({_sql_quote(window_start_dt)})), toDateTime(0)) AS first_trade_ts,
            nullIf(maxIf(t.ts, t.ts >= toDateTime({_sql_quote(window_start_dt)})), toDateTime(0)) AS last_trade_ts,
            sumIf(if(t.side = 'BUY', t.shares, -t.shares), t.ts < toDateTime({_sql_quote(window_start_dt)})) AS opening_shares,
            countIf(t.ts >= toDateTime({_sql_quote(window_start_dt)})) AS visible_trade_count
        FROM wallet_trades AS t
        INNER JOIN scoped_tokens AS st ON st.token_id = t.token_id
        GROUP BY t.token_id
        HAVING visible_trade_count > 0 OR abs(opening_shares) > 0.000000001
        ORDER BY coalesce(first_trade_ts, max(t.ts)) ASC, token_id ASC
    """)
    return [
        {
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "question": str(r.get("question") or ""),
            "first_trade_ts": _parse_dt(r["first_trade_ts"]) if r.get("first_trade_ts") else None,
            "last_trade_ts": _parse_dt(r["last_trade_ts"]) if r.get("last_trade_ts") else None,
            "opening_shares": float(r.get("opening_shares") or 0.0),
            "visible_trade_count": int(r.get("visible_trade_count") or 0),
        }
        for r in rows
    ]


def fetch_trades(client: ClickHouseClient, wallet: str, token_ids: list[str], window_start_date: date) -> list[dict]:
    db = _validate_id(client.database)
    token_list = ", ".join(_sql_quote(t) for t in token_ids)
    window_start_dt = f"{window_start_date.isoformat()} 00:00:00"
    rows = client.query(f"""
        WITH wallet_trades AS (
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
            GROUP BY trade_id
        )
        SELECT
            toDate(ts) AS trade_date, ts, token_id, condition_id, side,
            shares, usdc, fee_usdc, price, role
        FROM wallet_trades
        WHERE token_id IN ({token_list})
          AND ts >= toDateTime({_sql_quote(window_start_dt)})
        ORDER BY ts ASC, token_id ASC
    """)
    return [
        {
            "trade_date": _parse_date(r["trade_date"]),
            "ts": _parse_dt(r["ts"]),
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "side": str(r["side"]),
            "shares": float(r["shares"]),
            "usdc": float(r["usdc"]),
            "fee_usdc": float(r["fee_usdc"]),
            "price": float(r["price"]),
            "role": str(r.get("role") or ""),
        }
        for r in rows
    ]


def fetch_daily_closes(client: ClickHouseClient, token_ids: list[str]) -> list[dict]:
    db = _validate_id(client.database)
    token_list = ", ".join(_sql_quote(t) for t in token_ids)
    rows = client.query(f"""
        SELECT token_id, trade_date, toFloat64(close_price) AS close_price, close_ts
        FROM {db}.token_daily_close
        WHERE token_id IN ({token_list})
        ORDER BY token_id ASC, trade_date ASC
    """)
    return [
        {
            "token_id": str(r["token_id"]),
            "trade_date": _parse_date(r["trade_date"]),
            "close_price": float(r["close_price"]),
        }
        for r in rows
    ]


def fetch_resolution_prices(client: ClickHouseClient, token_ids: list[str]) -> dict[str, float]:
    db = _validate_id(client.database)
    token_list = ", ".join(_sql_quote(t) for t in token_ids)
    rows = client.query(f"""
        SELECT
            tm.token_id AS token_id, tm.condition_id AS condition_id,
            mr.closed AS closed, mr.resolved_ts AS resolved_ts,
            mr.updated_ts AS updated_ts, mr.token_prices AS token_prices
        FROM {db}.token_metadata_latest_v2 AS tm
        INNER JOIN {db}.market_resolution AS mr ON mr.condition_id = tm.condition_id
        WHERE tm.token_id IN ({token_list})
        ORDER BY tm.token_id ASC, updated_ts DESC
    """)
    resolutions = {}
    for r in rows:
        token_id = str(r["token_id"])
        if token_id in resolutions:
            continue
        if not bool(r.get("closed")):
            continue
        token_prices = r.get("token_prices") or {}
        if token_id not in token_prices:
            continue
        resolutions[token_id] = {
            "token_id": token_id,
            "resolved_ts": _parse_dt(r["resolved_ts"]),
            "price": float(token_prices[token_id]),
        }
    return resolutions


def build_chart_payload(wallet: str, game: str, lookback_days: int,
                        token_scope: list[dict], trades: list[dict],
                        closes: list[dict], resolutions: dict,
                        opening_positions: dict[str, float] | None = None) -> dict[str, Any]:
    """Reconstruct daily marked-to-market P&L series for the selected window."""
    if not token_scope:
        return None

    token_ids = [t["token_id"] for t in token_scope]
    opening_positions = opening_positions or {}
    latest_close_date = max((c["trade_date"] for c in closes), default=date.today())
    first_visible_trade_date = min((t["trade_date"] for t in trades), default=None)
    last_trade_date = max((t["trade_date"] for t in trades), default=None)
    chart_end_date = max(date.today(), last_trade_date or date.today(), latest_close_date)
    window_start_date = _lookback_window_start(date.today(), lookback_days)
    has_opening_positions = any(abs(opening_positions.get(tid, 0.0)) > 1e-9 for tid in token_ids)
    chart_start_date = window_start_date if has_opening_positions else (first_visible_trade_date or window_start_date)
    baseline_date = chart_start_date - timedelta(days=1)
    price_calendar = _daterange(baseline_date, chart_end_date)
    calendar = [day for day in price_calendar if day >= chart_start_date]

    # Build close price lookup: {token_id: {date: price}}
    close_map = _build_close_map(closes)

    # Build daily price lookup with forward-fill + resolution
    def get_prices(token_id):
        prices = {}
        latest = None
        res = resolutions.get(token_id)
        for day in price_calendar:
            resolved_price = _resolved_price_as_of(res, day)
            if resolved_price is not None:
                prices[day] = resolved_price
                continue
            if day in close_map.get(token_id, {}):
                latest = close_map[token_id][day]
                prices[day] = latest
            else:
                prices[day] = latest
        return prices

    price_lookup = {tid: get_prices(tid) for tid in token_ids}

    # Aggregate trade deltas
    shares_delta = {}  # (date, token_id) -> float
    cash_delta = {}  # date -> float
    trade_count = {}  # date -> int
    total_volume = 0.0

    for t in trades:
        key = (t["trade_date"], t["token_id"])
        signed_shares = t["shares"] if t["side"] == "BUY" else -t["shares"]
        signed_cash = -t["usdc"] if t["side"] == "BUY" else t["usdc"]
        shares_delta[key] = shares_delta.get(key, 0.0) + signed_shares
        cash_delta[t["trade_date"]] = cash_delta.get(t["trade_date"], 0.0) + signed_cash - t["fee_usdc"]
        trade_count[t["trade_date"]] = trade_count.get(t["trade_date"], 0) + 1
        total_volume += t["usdc"]

    # Build daily series
    positions = {tid: opening_positions.get(tid, 0.0) for tid in token_ids}
    cum_cash = 0.0
    opening_marked_value = sum(
        opening_positions.get(tid, 0.0) * (price_lookup[tid].get(baseline_date) or 0.0)
        for tid in token_ids
    )
    peak_pnl = None
    max_dd = 0.0
    series = []

    for day in calendar:
        cum_cash += cash_delta.get(day, 0.0)
        for tid in token_ids:
            positions[tid] += shares_delta.get((day, tid), 0.0)

        marked_value = sum(
            positions[tid] * (price_lookup[tid].get(day) or 0)
            for tid in token_ids
        )
        pnl = cum_cash + marked_value - opening_marked_value
        marked_delta = marked_value - opening_marked_value

        if peak_pnl is None or pnl > peak_pnl:
            peak_pnl = pnl
        if peak_pnl and peak_pnl > 0:
            max_dd = max(max_dd, (peak_pnl - pnl) / peak_pnl * 100)

        series.append({
            "date": day.isoformat(),
            "pnl": round(pnl, 2),
            "cumulative_cash": round(cum_cash, 2),
            "marked_value": round(marked_delta, 2),
            "daily_trade_count": trade_count.get(day, 0),
        })

    return {
        "meta": {"wallet": wallet, "game": game, "lookback_days": lookback_days},
        "summary": {
            "first_trade_date": chart_start_date.isoformat(),
            "last_trade_date": (last_trade_date or chart_start_date).isoformat(),
            "chart_end_date": chart_end_date.isoformat(),
            "total_trades": len(trades),
            "scoped_tokens": len(token_ids),
            "scoped_conditions": len({t["condition_id"] for t in token_scope}),
            "final_pnl": series[-1]["pnl"] if series else 0,
            "max_drawdown_pct": round(max_dd, 2),
            "total_volume_usd": round(total_volume, 2),
        },
        "series": series,
    }


def compute_market_pnl_breakdown(token_scope: list[dict], trades: list[dict],
                                 final_prices: dict[str, float],
                                 opening_positions: dict[str, float] | None = None,
                                 opening_prices: dict[str, float] | None = None) -> dict[str, Any]:
    """Compute per-market P&L using the chart's snapshot pricing basis."""
    if not token_scope:
        return {"markets": [], "total_markets": 0, "concentration": {"top1_pct": 0, "top3_pct": 0, "top5_pct": 0}, "win_rate": 0}

    opening_positions = opening_positions or {}
    opening_prices = opening_prices or {}

    # Map token_id -> question (individual market name)
    token_to_question = {}
    for t in token_scope:
        token_to_question[t["token_id"]] = t.get("question", "Unknown")

    # Aggregate per market (by question name): opening positions, cash flow, ending positions, trade count
    mkt_cash = {}       # question -> total cash flow
    mkt_positions = {}   # question -> {token_id: shares}
    mkt_open_positions = {}   # question -> {token_id: shares}
    mkt_trades = {}     # question -> trade count
    mkt_volume = {}     # question -> total volume

    for token_id, opening_shares in opening_positions.items():
        if abs(opening_shares) <= 1e-9:
            continue
        question = token_to_question.get(token_id, "Unknown")
        mkt_cash.setdefault(question, 0.0)
        mkt_positions.setdefault(question, {})
        mkt_open_positions.setdefault(question, {})
        mkt_trades.setdefault(question, 0)
        mkt_volume.setdefault(question, 0.0)
        mkt_positions[question][token_id] = opening_shares
        mkt_open_positions[question][token_id] = opening_shares

    for t in trades:
        question = token_to_question.get(t["token_id"], "Unknown")

        if question not in mkt_cash:
            mkt_cash[question] = 0.0
            mkt_positions[question] = {}
            mkt_open_positions[question] = {}
            mkt_trades[question] = 0
            mkt_volume[question] = 0.0

        signed_shares = t["shares"] if t["side"] == "BUY" else -t["shares"]
        signed_cash = -t["usdc"] if t["side"] == "BUY" else t["usdc"]

        mkt_cash[question] += signed_cash - t["fee_usdc"]
        mkt_positions[question].setdefault(t["token_id"], opening_positions.get(t["token_id"], 0.0))
        mkt_positions[question][t["token_id"]] += signed_shares
        mkt_trades[question] += 1
        mkt_volume[question] += t["usdc"]

    # Compute final P&L per market: cash + marked position value
    markets = []
    for question in mkt_cash:
        cash = mkt_cash[question]
        opening_value = sum(
            shares * opening_prices.get(tid, 0.0)
            for tid, shares in mkt_open_positions[question].items()
        )
        ending_value = sum(
            shares * final_prices.get(tid, 0.0)
            for tid, shares in mkt_positions[question].items()
        )
        pnl = cash + ending_value - opening_value

        markets.append({
            "market_name": question,
            "total_trades": mkt_trades[question],
            "net_cash": round(pnl, 2),
            "volume": round(mkt_volume[question], 2),
        })

    # Sort by absolute P&L
    markets.sort(key=lambda m: abs(m["net_cash"]), reverse=True)

    # Concentration
    pnls = [m["net_cash"] for m in markets]
    total_abs = sum(abs(p) for p in pnls) if pnls else 0
    sorted_pnls = sorted(pnls, key=lambda x: abs(x), reverse=True)

    def pct(top_n):
        if not total_abs:
            return 0
        return round(sum(abs(p) for p in sorted_pnls[:top_n]) / total_abs * 100, 1)

    profitable = sum(1 for p in pnls if p > 0)
    win_rate = round(profitable / len(pnls) * 100, 1) if pnls else 0

    return {
        "markets": markets[:10],
        "total_markets": len(markets),
        "concentration": {"top1_pct": pct(1), "top3_pct": pct(3), "top5_pct": pct(5)},
        "win_rate": win_rate,
    }


def get_wallet_curation_data(wallet: str, filter_value: str, lookback_days: int = 365, filter_level: str = "detail") -> dict[str, Any]:
    """Fetch chart payload + market breakdown for curation page."""
    from concurrent.futures import ThreadPoolExecutor

    client = ClickHouseClient()
    window_start_date = _lookback_window_start(date.today(), lookback_days)
    token_scope = fetch_token_scope(client, wallet, filter_value, filter_level, window_start_date)
    if not token_scope:
        return None
    token_ids = [t["token_id"] for t in token_scope]
    opening_positions = {t["token_id"]: float(t.get("opening_shares") or 0.0) for t in token_scope}

    with ThreadPoolExecutor(max_workers=3) as pool:
        trades_future = pool.submit(fetch_trades, client, wallet, token_ids, window_start_date)
        closes_future = pool.submit(fetch_daily_closes, client, token_ids)
        resolutions_future = pool.submit(fetch_resolution_prices, client, token_ids)
        trades = trades_future.result()
        closes = closes_future.result()
        resolutions = resolutions_future.result()

    if not trades and not any(abs(shares) > 1e-9 for shares in opening_positions.values()):
        return None

    chart = build_chart_payload(wallet, filter_value, lookback_days, token_scope, trades, closes, resolutions, opening_positions)
    if not chart:
        return None

    chart_end_date = _parse_date(chart["summary"]["chart_end_date"])
    opening_date = _parse_date(chart["summary"]["first_trade_date"]) - timedelta(days=1)
    final_prices = _build_final_price_lookup(token_ids, closes, resolutions, chart_end_date)
    opening_prices = _build_final_price_lookup(token_ids, closes, resolutions, opening_date)
    breakdown = compute_market_pnl_breakdown(token_scope, trades, final_prices, opening_positions, opening_prices)
    chart["breakdown"] = breakdown
    chart["signals"] = build_curation_signals(token_scope, trades, breakdown)

    vol = chart["summary"]["total_volume_usd"]
    pnl = chart["summary"]["final_pnl"]
    chart["summary"]["roi_pct"] = round((pnl / vol * 100), 2) if vol else 0
    chart["summary"]["win_rate"] = breakdown["win_rate"]
    chart["summary"]["active_days"] = chart["signals"]["active_days"]
    chart["summary"]["unique_markets"] = chart["signals"]["unique_markets"]
    return chart


def get_wallet_game_chart(wallet: str, filter_value: str, lookback_days: int = 365, filter_level: str = "detail") -> dict[str, Any]:
    """High-level function: fetch all data and build the chart payload.
    Queries 2-4 run in parallel after token scope is fetched."""
    from concurrent.futures import ThreadPoolExecutor

    client = ClickHouseClient()
    window_start_date = _lookback_window_start(date.today(), lookback_days)
    token_scope = fetch_token_scope(client, wallet, filter_value, filter_level, window_start_date)
    if not token_scope:
        return None
    token_ids = [t["token_id"] for t in token_scope]
    opening_positions = {t["token_id"]: float(t.get("opening_shares") or 0.0) for t in token_scope}

    # Run trades, closes, resolutions in parallel
    with ThreadPoolExecutor(max_workers=3) as pool:
        trades_future = pool.submit(fetch_trades, client, wallet, token_ids, window_start_date)
        closes_future = pool.submit(fetch_daily_closes, client, token_ids)
        resolutions_future = pool.submit(fetch_resolution_prices, client, token_ids)

        trades = trades_future.result()
        closes = closes_future.result()
        resolutions = resolutions_future.result()

    if not trades and not any(abs(shares) > 1e-9 for shares in opening_positions.values()):
        return None
    return build_chart_payload(wallet, filter_value, lookback_days, token_scope, trades, closes, resolutions, opening_positions)
