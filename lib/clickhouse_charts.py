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


def fetch_token_scope(client: ClickHouseClient, wallet: str, filter_value: str, filter_level: str, lookback_days: int) -> list[dict]:
    db = _validate_id(client.database)
    if filter_level == "category":
        scope_clause = f"tm.category = {_sql_quote(filter_value)}"
    elif filter_level == "subcategory":
        scope_clause = f"tm.subcategory = {_sql_quote(filter_value)}"
    else:
        scope_clause = f"tm.subcategory_detail = {_sql_quote(filter_value)}"
    rows = client.query(f"""
        SELECT
            t.token_id AS token_id,
            any(t.condition_id) AS condition_id,
            any(tm.question) AS question,
            min(t.ts) AS first_trade_ts,
            max(t.ts) AS last_trade_ts
        FROM {db}.trades AS t
        INNER JOIN {db}.token_metadata_latest_v2 AS tm ON tm.token_id = t.token_id
        WHERE t.wallet = {_sql_quote(wallet)}
          AND {scope_clause}
          AND t.ts >= now() - INTERVAL {int(lookback_days)} DAY
        GROUP BY t.token_id
        ORDER BY first_trade_ts ASC, token_id ASC
    """)
    return [
        {
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "question": str(r.get("question") or ""),
            "first_trade_ts": _parse_dt(r["first_trade_ts"]),
            "last_trade_ts": _parse_dt(r["last_trade_ts"]),
        }
        for r in rows
    ]


def fetch_trades(client: ClickHouseClient, wallet: str, token_ids: list[str], lookback_days: int) -> list[dict]:
    db = _validate_id(client.database)
    token_list = ", ".join(_sql_quote(t) for t in token_ids)
    rows = client.query(f"""
        SELECT
            toDate(ts) AS trade_date, ts, token_id, condition_id, side,
            toFloat64(shares) AS shares, toFloat64(usdc) AS usdc,
            toFloat64(fee_usdc) AS fee_usdc, toFloat64(price) AS price, role
        FROM {db}.trades
        WHERE wallet = {_sql_quote(wallet)}
          AND token_id IN ({token_list})
          AND ts >= now() - INTERVAL {int(lookback_days)} DAY
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
                        closes: list[dict], resolutions: dict) -> dict[str, Any]:
    """Reconstruct daily marked-to-market P&L series."""
    if not token_scope or not trades:
        return None

    token_ids = [t["token_id"] for t in token_scope]
    first_trade_date = min(t["first_trade_ts"].date() for t in token_scope)
    last_trade_date = max(t["last_trade_ts"].date() for t in token_scope)
    latest_close_date = max((c["trade_date"] for c in closes), default=last_trade_date)
    chart_end_date = max(date.today(), last_trade_date, latest_close_date)
    calendar = _daterange(first_trade_date, chart_end_date)

    # Build close price lookup: {token_id: {date: price}}
    close_map = {}
    for c in closes:
        close_map.setdefault(c["token_id"], {})[c["trade_date"]] = c["close_price"]

    # Build daily price lookup with forward-fill + resolution
    def get_prices(token_id):
        prices = {}
        latest = None
        res = resolutions.get(token_id)
        res_day = res["resolved_ts"].date() if res else None
        res_price = res["price"] if res else None
        for day in calendar:
            if res_day and day >= res_day and res_price is not None:
                prices[day] = res_price
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
    positions = {tid: 0.0 for tid in token_ids}
    cum_cash = 0.0
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
        pnl = cum_cash + marked_value

        if peak_pnl is None or pnl > peak_pnl:
            peak_pnl = pnl
        if peak_pnl and peak_pnl > 0:
            max_dd = max(max_dd, (peak_pnl - pnl) / peak_pnl * 100)

        series.append({
            "date": day.isoformat(),
            "pnl": round(pnl, 2),
            "cumulative_cash": round(cum_cash, 2),
            "marked_value": round(marked_value, 2),
            "daily_trade_count": trade_count.get(day, 0),
        })

    return {
        "meta": {"wallet": wallet, "game": game, "lookback_days": lookback_days},
        "summary": {
            "first_trade_date": first_trade_date.isoformat(),
            "last_trade_date": last_trade_date.isoformat(),
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
                                  closes: list[dict], resolutions: dict) -> dict[str, Any]:
    """Compute per-market P&L using position tracking (same logic as the chart).

    For each condition_id: track positions per token, apply resolution/close prices,
    then P&L = cash_flow + final_position_value.
    """
    if not trades:
        return {"markets": [], "total_markets": 0, "concentration": {"top1_pct": 0, "top3_pct": 0, "top5_pct": 0}, "win_rate": 0}

    # Map token_id -> question (individual market name)
    token_to_question = {}
    for t in token_scope:
        token_to_question[t["token_id"]] = t.get("question", "Unknown")

    # Build last known price per token (resolution price if resolved, else latest close)
    last_close = {}
    for c in closes:
        last_close[c["token_id"]] = c["close_price"]  # keeps overwriting to latest

    def final_price(token_id):
        res = resolutions.get(token_id)
        if res:
            return res["price"]
        return last_close.get(token_id, 0)

    # Aggregate per market (by question name): cash flow, positions, trade count
    mkt_cash = {}       # question -> total cash flow
    mkt_positions = {}   # question -> {token_id: shares}
    mkt_trades = {}     # question -> trade count
    mkt_volume = {}     # question -> total volume

    for t in trades:
        question = token_to_question.get(t["token_id"], "Unknown")

        if question not in mkt_cash:
            mkt_cash[question] = 0.0
            mkt_positions[question] = {}
            mkt_trades[question] = 0
            mkt_volume[question] = 0.0

        signed_shares = t["shares"] if t["side"] == "BUY" else -t["shares"]
        signed_cash = -t["usdc"] if t["side"] == "BUY" else t["usdc"]

        mkt_cash[question] += signed_cash - t["fee_usdc"]
        mkt_positions[question].setdefault(t["token_id"], 0.0)
        mkt_positions[question][t["token_id"]] += signed_shares
        mkt_trades[question] += 1
        mkt_volume[question] += t["usdc"]

    # Compute final P&L per market: cash + marked position value
    markets = []
    for question in mkt_cash:
        cash = mkt_cash[question]
        position_value = sum(
            max(shares, 0) * final_price(tid)
            for tid, shares in mkt_positions[question].items()
        )
        pnl = cash + position_value

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
    token_scope = fetch_token_scope(client, wallet, filter_value, filter_level, lookback_days)
    if not token_scope:
        return None
    token_ids = [t["token_id"] for t in token_scope]

    with ThreadPoolExecutor(max_workers=3) as pool:
        trades_future = pool.submit(fetch_trades, client, wallet, token_ids, lookback_days)
        closes_future = pool.submit(fetch_daily_closes, client, token_ids)
        resolutions_future = pool.submit(fetch_resolution_prices, client, token_ids)
        trades = trades_future.result()
        closes = closes_future.result()
        resolutions = resolutions_future.result()

    if not trades:
        return None

    chart = build_chart_payload(wallet, filter_value, lookback_days, token_scope, trades, closes, resolutions)
    if not chart:
        return None

    # Compute market breakdown using position tracking (same data, no extra queries)
    breakdown = compute_market_pnl_breakdown(token_scope, trades, closes, resolutions)
    chart["breakdown"] = breakdown

    vol = chart["summary"]["total_volume_usd"]
    pnl = chart["summary"]["final_pnl"]
    chart["summary"]["roi_pct"] = round((pnl / vol * 100), 2) if vol else 0
    chart["summary"]["win_rate"] = breakdown["win_rate"]
    return chart


def get_wallet_game_chart(wallet: str, filter_value: str, lookback_days: int = 365, filter_level: str = "detail") -> dict[str, Any]:
    """High-level function: fetch all data and build the chart payload.
    Queries 2-4 run in parallel after token scope is fetched."""
    from concurrent.futures import ThreadPoolExecutor

    client = ClickHouseClient()
    token_scope = fetch_token_scope(client, wallet, filter_value, filter_level, lookback_days)
    if not token_scope:
        return None
    token_ids = [t["token_id"] for t in token_scope]

    # Run trades, closes, resolutions in parallel
    with ThreadPoolExecutor(max_workers=3) as pool:
        trades_future = pool.submit(fetch_trades, client, wallet, token_ids, lookback_days)
        closes_future = pool.submit(fetch_daily_closes, client, token_ids)
        resolutions_future = pool.submit(fetch_resolution_prices, client, token_ids)

        trades = trades_future.result()
        closes = closes_future.result()
        resolutions = resolutions_future.result()

    if not trades:
        return None
    return build_chart_payload(wallet, filter_value, lookback_days, token_scope, trades, closes, resolutions)
