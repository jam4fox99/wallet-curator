"""ClickHouse-backed game-specific wallet P&L chart builder.

Adapted from wallet_game_chart.py reference implementation.
Queries ClickHouse for trades, daily closes, and resolution prices,
then reconstructs a daily marked-to-market P&L series.
"""

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "jake")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.environ.get("CLICKHOUSE_DATABASE", "polymarket")
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

CURATION_ALL_RANGE = "ALL"
CURATION_RANGE_DAYS = {
    "1D": 1,
    "7D": 7,
    "14D": 14,
    "30D": 30,
}
CURATION_RANGE_TOKENS = tuple([*CURATION_RANGE_DAYS.keys(), CURATION_ALL_RANGE])
_GAMMA_OUTCOME_CACHE: dict[str, str] = {}


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


def normalize_curation_range_key(range_key: Any) -> str:
    if isinstance(range_key, int):
        range_key = str(range_key)
    text = str(range_key or CURATION_ALL_RANGE).strip().upper()
    aliases = {
        "1": "1D",
        "1D": "1D",
        "7": "7D",
        "7D": "7D",
        "14": "14D",
        "14D": "14D",
        "2W": "14D",
        "30": "30D",
        "30D": "30D",
        "365": CURATION_ALL_RANGE,
        CURATION_ALL_RANGE: CURATION_ALL_RANGE,
    }
    return aliases.get(text, CURATION_ALL_RANGE)


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


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _fetch_gamma_outcomes_for_token_ids(token_ids: list[str]) -> dict[str, str]:
    normalized = [token_id for token_id in token_ids if token_id and token_id.isdigit()]
    missing = [token_id for token_id in normalized if token_id not in _GAMMA_OUTCOME_CACHE]
    if missing:
        batch_size = 50
        for start in range(0, len(missing), batch_size):
            batch = missing[start:start + batch_size]
            params = [("clob_token_ids", token_id) for token_id in batch]
            params.append(("limit", str(max(len(batch) * 2, 50))))
            try:
                response = requests.get(GAMMA_API_URL, params=params, timeout=15)
                response.raise_for_status()
                markets = response.json()
            except Exception as exc:
                logger.warning("Gamma outcome lookup failed for %d token ids: %s", len(batch), exc)
                markets = []

            if isinstance(markets, list):
                for market in markets:
                    clob_ids = _parse_json_list(market.get("clobTokenIds"))
                    outcomes = _parse_json_list(market.get("outcomes"))
                    for index, clob_id in enumerate(clob_ids):
                        token_id = str(clob_id)
                        if token_id in _GAMMA_OUTCOME_CACHE:
                            continue
                        outcome = str(outcomes[index]).strip() if index < len(outcomes) and outcomes[index] is not None else ""
                        _GAMMA_OUTCOME_CACHE[token_id] = outcome

        for token_id in missing:
            _GAMMA_OUTCOME_CACHE.setdefault(token_id, "")

    return {token_id: _GAMMA_OUTCOME_CACHE.get(token_id, "") for token_id in normalized}


def _build_token_meta_lookup(token_scope: list[dict[str, Any]], trades: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    trade_outcomes: dict[str, str] = {}
    for trade in trades or []:
        token_id = str(trade.get("token_id") or "")
        outcome = str(trade.get("outcome") or "").strip()
        if token_id and outcome and token_id not in trade_outcomes:
            trade_outcomes[token_id] = outcome

    gamma_outcomes = _fetch_gamma_outcomes_for_token_ids([
        str(token.get("token_id") or "")
        for token in token_scope
        if str(token.get("token_id") or "")
    ])

    lookup: dict[str, dict[str, Any]] = {}
    for token in token_scope:
        token_id = str(token.get("token_id") or "")
        if not token_id:
            continue
        outcome = trade_outcomes.get(token_id) or str(token.get("outcome") or "").strip() or gamma_outcomes.get(token_id, "")
        lookup[token_id] = {
            "token_id": token_id,
            "condition_id": str(token.get("condition_id") or ""),
            "question": str(token.get("question") or "Unknown").strip() or "Unknown",
            "outcome_label": outcome or f"Token {token_id[:8]}",
            "outcome_is_derived": not bool(outcome),
        }
    return lookup


def _trade_mtm_pnl(trade: dict[str, Any], final_price: float) -> float:
    signed_cash = -float(trade.get("usdc") or 0.0) if trade.get("side") == "BUY" else float(trade.get("usdc") or 0.0)
    signed_shares = float(trade.get("shares") or 0.0) if trade.get("side") == "BUY" else -float(trade.get("shares") or 0.0)
    return signed_cash - float(trade.get("fee_usdc") or 0.0) + (signed_shares * final_price)


def _detect_synthetic_buy_pairs(trades: list[dict[str, Any]]) -> tuple[dict[str, dict[str, dict[str, float]]], dict[str, dict[str, int]]]:
    grouped: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[(str(trade.get("condition_id") or ""), trade.get("ts"))].append(trade)

    paired_buys: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {
        "shares": 0.0,
        "dollars": 0.0,
        "cleanup_shares": 0.0,
        "cleanup_dollars": 0.0,
    }))
    condition_flags: dict[str, dict[str, int]] = defaultdict(lambda: {
        "synthetic_pair_count": 0,
        "cleanup_pair_count": 0,
    })

    for (condition_id, _ts), condition_trades in grouped.items():
        buy_entries = []
        sell_entries = []
        token_buys: dict[str, dict[str, float]] = defaultdict(lambda: {"shares": 0.0, "dollars": 0.0})
        token_sells: dict[str, dict[str, float]] = defaultdict(lambda: {"shares": 0.0, "dollars": 0.0})

        for trade in condition_trades:
            token_id = str(trade.get("token_id") or "")
            shares = float(trade.get("shares") or 0.0)
            dollars = float(trade.get("usdc") or 0.0)
            if shares <= 1e-9:
                continue
            if trade.get("side") == "BUY":
                token_buys[token_id]["shares"] += shares
                token_buys[token_id]["dollars"] += dollars
            elif trade.get("side") == "SELL":
                token_sells[token_id]["shares"] += shares
                token_sells[token_id]["dollars"] += dollars

        for token_id, stats in token_buys.items():
            if stats["shares"] <= 1e-9:
                continue
            buy_entries.append({
                "token_id": token_id,
                "price": stats["dollars"] / stats["shares"],
                "remaining_shares": stats["shares"],
            })
        for token_id, stats in token_sells.items():
            if stats["shares"] <= 1e-9:
                continue
            sell_entries.append({
                "token_id": token_id,
                "price": stats["dollars"] / stats["shares"],
                "remaining_shares": stats["shares"],
            })

        for buy in buy_entries:
            candidates = [
                sell for sell in sell_entries
                if sell["token_id"] != buy["token_id"] and abs((buy["price"] + sell["price"]) - 1.0) <= 0.03
            ]
            candidates.sort(key=lambda sell: abs((buy["price"] + sell["price"]) - 1.0))
            for sell in candidates:
                if buy["remaining_shares"] <= 1e-9:
                    break
                if sell["remaining_shares"] <= 1e-9:
                    continue
                paired_shares = min(buy["remaining_shares"], sell["remaining_shares"])
                if paired_shares <= 1e-9:
                    continue
                paired_dollars = paired_shares * buy["price"]
                paired_buys[condition_id][buy["token_id"]]["shares"] += paired_shares
                paired_buys[condition_id][buy["token_id"]]["dollars"] += paired_dollars
                condition_flags[condition_id]["synthetic_pair_count"] += 1
                if buy["price"] <= 0.02 or sell["price"] >= 0.98:
                    paired_buys[condition_id][buy["token_id"]]["cleanup_shares"] += paired_shares
                    paired_buys[condition_id][buy["token_id"]]["cleanup_dollars"] += paired_dollars
                    condition_flags[condition_id]["cleanup_pair_count"] += 1
                buy["remaining_shares"] -= paired_shares
                sell["remaining_shares"] -= paired_shares

    return paired_buys, condition_flags


def build_trade_audit_rows(token_scope: list[dict[str, Any]], trades: list[dict[str, Any]],
                           final_prices: dict[str, float]) -> list[dict[str, Any]]:
    token_meta = _build_token_meta_lookup(token_scope, trades)
    rows = []
    for trade in trades:
        token_id = str(trade.get("token_id") or "")
        meta = token_meta.get(token_id, {
            "question": "Unknown",
            "outcome_label": f"Token {token_id[:8]}",
            "outcome_is_derived": True,
        })
        trade_outcome = str(trade.get("outcome") or "").strip()
        mtm_pnl = _trade_mtm_pnl(trade, float(final_prices.get(token_id, 0.0)))
        rows.append({
            "trade_id": str(trade.get("trade_id") or ""),
            "ts": trade["ts"].isoformat() if isinstance(trade.get("ts"), datetime) else str(trade.get("ts") or ""),
            "market": meta["question"],
            "outcome_label": trade_outcome or meta["outcome_label"],
            "outcome_is_derived": not bool(trade_outcome) and bool(meta.get("outcome_is_derived")),
            "side": str(trade.get("side") or ""),
            "shares": round(float(trade.get("shares") or 0.0), 2),
            "price": round(float(trade.get("price") or 0.0), 4),
            "dollars": round(float(trade.get("usdc") or 0.0), 2),
            "fee": round(float(trade.get("fee_usdc") or 0.0), 2),
            "mtm_pnl": round(mtm_pnl, 2),
        })
    rows.sort(key=lambda row: (-row["mtm_pnl"], row["ts"], row["market"], row["outcome_label"]))
    return rows


def _compact_trade_row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    trade_id = str(row.get("trade_id") or "").strip()
    if trade_id:
        return ("trade_id", trade_id)
    return (
        str(row.get("ts") or ""),
        str(row.get("market") or ""),
        str(row.get("outcome_label") or ""),
        str(row.get("side") or ""),
        float(row.get("shares") or 0.0),
        float(row.get("price") or 0.0),
        float(row.get("dollars") or 0.0),
    )


def build_compact_trade_audit_rows(rows: list[dict[str, Any]], limit_per_side: int = 50) -> list[dict[str, Any]]:
    rows = list(rows or [])
    if limit_per_side <= 0 or len(rows) <= (limit_per_side * 2):
        return rows

    compact_rows = rows[:limit_per_side] + rows[-limit_per_side:]
    deduped_rows = []
    seen = set()
    for row in compact_rows:
        row_key = _compact_trade_row_key(row)
        if row_key in seen:
            continue
        seen.add(row_key)
        deduped_rows.append(row)
    deduped_rows.sort(key=lambda row: (-float(row.get("mtm_pnl") or 0.0), str(row.get("ts") or ""), str(row.get("market") or ""), str(row.get("outcome_label") or "")))
    return deduped_rows


def build_both_sides_rows(token_scope: list[dict[str, Any]], trades: list[dict[str, Any]],
                          final_prices: dict[str, float]) -> list[dict[str, Any]]:
    token_meta = _build_token_meta_lookup(token_scope, trades)
    paired_buys, condition_flags = _detect_synthetic_buy_pairs(trades)
    buy_trades_by_condition: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    token_cash: dict[str, float] = defaultdict(float)
    token_net_shares: dict[str, float] = defaultdict(float)
    token_sell_shares: dict[str, float] = defaultdict(float)
    condition_cash: dict[str, float] = defaultdict(float)
    condition_net_shares: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    condition_market: dict[str, str] = {}

    for trade in trades:
        token_id = str(trade.get("token_id") or "")
        meta = token_meta.get(token_id, {})
        condition_id = str(trade.get("condition_id") or meta.get("condition_id") or token_id)
        condition_market.setdefault(condition_id, str(meta.get("question") or "Unknown"))

        signed_cash = -float(trade.get("usdc") or 0.0) if trade.get("side") == "BUY" else float(trade.get("usdc") or 0.0)
        signed_shares = float(trade.get("shares") or 0.0) if trade.get("side") == "BUY" else -float(trade.get("shares") or 0.0)
        fee = float(trade.get("fee_usdc") or 0.0)

        token_cash[token_id] += signed_cash - fee
        token_net_shares[token_id] += signed_shares
        condition_cash[condition_id] += signed_cash - fee
        condition_net_shares[condition_id][token_id] += signed_shares

        if trade.get("side") == "BUY":
            buy_trades_by_condition[condition_id][token_id].append(trade)
        elif trade.get("side") == "SELL":
            token_sell_shares[token_id] += float(trade.get("shares") or 0.0)

    rows = []
    for condition_id, token_buys in buy_trades_by_condition.items():
        side_entries = []
        for token_id, buy_trades in token_buys.items():
            meta = token_meta.get(token_id, {
                "outcome_label": f"Token {token_id[:8]}",
                "outcome_is_derived": True,
            })
            total_buy_shares = sum(float(trade.get("shares") or 0.0) for trade in buy_trades)
            total_buy_dollars = sum(float(trade.get("usdc") or 0.0) for trade in buy_trades)
            paired = paired_buys.get(condition_id, {}).get(token_id, {})
            effective_buy_shares = max(total_buy_shares - float(paired.get("shares") or 0.0), 0.0)
            effective_buy_dollars = max(total_buy_dollars - float(paired.get("dollars") or 0.0), 0.0)
            if effective_buy_shares <= 1e-9:
                continue
            avg_buy = (effective_buy_dollars / effective_buy_shares) if effective_buy_shares > 0 else 0.0
            side_pnl = token_cash[token_id] + (token_net_shares[token_id] * float(final_prices.get(token_id, 0.0)))
            side_entries.append({
                "token_id": token_id,
                "outcome_label": meta["outcome_label"],
                "outcome_is_derived": bool(meta.get("outcome_is_derived")),
                "avg_buy": round(avg_buy, 4),
                "bought_shares": round(effective_buy_shares, 2),
                "bought_dollars": round(effective_buy_dollars, 2),
                "sold_shares": round(token_sell_shares[token_id], 2),
                "side_pnl": round(side_pnl, 2),
            })

        if len(side_entries) < 2:
            continue

        side_entries.sort(key=lambda entry: (-entry["bought_dollars"], entry["outcome_label"]))
        top_a, top_b = side_entries[:2]
        total_pnl = condition_cash[condition_id] + sum(
            shares * float(final_prices.get(token_id, 0.0))
            for token_id, shares in condition_net_shares[condition_id].items()
        )
        rows.append({
            "condition_id": condition_id,
            "market": condition_market.get(condition_id, "Unknown"),
            "outcome_a_label": top_a["outcome_label"],
            "outcome_a_is_derived": top_a["outcome_is_derived"],
            "avg_buy_a": round(top_a["avg_buy"], 4),
            "bought_shares_a": round(top_a["bought_shares"], 2),
            "bought_dollars_a": round(top_a["bought_dollars"], 2),
            "sold_shares_a": round(top_a["sold_shares"], 2),
            "side_pnl_a": round(top_a["side_pnl"], 2),
            "outcome_b_label": top_b["outcome_label"],
            "outcome_b_is_derived": top_b["outcome_is_derived"],
            "avg_buy_b": round(top_b["avg_buy"], 4),
            "bought_shares_b": round(top_b["bought_shares"], 2),
            "bought_dollars_b": round(top_b["bought_dollars"], 2),
            "sold_shares_b": round(top_b["sold_shares"], 2),
            "side_pnl_b": round(top_b["side_pnl"], 2),
            "combined_avg_buy": round(float(top_a["avg_buy"]) + float(top_b["avg_buy"]), 3),
            "total_pnl": round(total_pnl, 2),
            "extra_outcome_count": max(len(side_entries) - 2, 0),
            "has_paired_complements": condition_flags.get(condition_id, {}).get("synthetic_pair_count", 0) > 0,
            "cleanup_pair_count": condition_flags.get(condition_id, {}).get("cleanup_pair_count", 0),
        })

    rows.sort(key=lambda row: (-row["combined_avg_buy"], -row["total_pnl"], row["market"]))
    return rows


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

    paired_buys, _condition_flags = _detect_synthetic_buy_pairs(trades)
    buy_trades = [trade for trade in trades if trade["side"] == "BUY"]
    conditions: dict[str, set[str]] = {}
    buy_shares_by_token: dict[tuple[str, str], float] = defaultdict(float)
    for trade in buy_trades:
        condition_id = str(trade["condition_id"])
        token_id = str(trade["token_id"])
        buy_shares_by_token[(condition_id, token_id)] += float(trade.get("shares") or 0.0)
    for (condition_id, token_id), total_buy_shares in buy_shares_by_token.items():
        effective_buy_shares = total_buy_shares - float(paired_buys.get(condition_id, {}).get(token_id, {}).get("shares") or 0.0)
        if effective_buy_shares > 1e-9:
            conditions.setdefault(condition_id, set()).add(token_id)
    both_sides_count = sum(1 for token_ids in conditions.values() if len(token_ids) > 1)
    both_sides_market_pct = round((both_sides_count / len(conditions)) * 100.0, 1) if conditions else 0.0

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


def _scope_clause_for_filter(filter_value: str, filter_level: str) -> str:
    if filter_level == "category":
        return f"category = {_sql_quote(filter_value)}"
    if filter_level == "subcategory":
        return f"subcategory = {_sql_quote(filter_value)}"
    return f"subcategory_detail = {_sql_quote(filter_value)}"


def fetch_token_scope(client: ClickHouseClient, wallet: str, filter_value: str, filter_level: str,
                      window_start_date: date) -> list[dict]:
    db = _validate_id(client.database)
    scope_clause = _scope_clause_for_filter(filter_value, filter_level)
    window_start_dt = f"{window_start_date.isoformat()} 00:00:00"
    rows = client.query(f"""
        WITH scoped_tokens AS (
            SELECT
                token_id,
                any(condition_id) AS condition_id,
                any(question) AS question,
                any(outcome) AS outcome
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
            any(st.outcome) AS outcome,
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
            "outcome": str(r.get("outcome") or ""),
            "first_trade_ts": _parse_dt(r["first_trade_ts"]) if r.get("first_trade_ts") else None,
            "last_trade_ts": _parse_dt(r["last_trade_ts"]) if r.get("last_trade_ts") else None,
            "opening_shares": float(r.get("opening_shares") or 0.0),
            "visible_trade_count": int(r.get("visible_trade_count") or 0),
        }
        for r in rows
    ]


def fetch_token_scope_all_history(client: ClickHouseClient, wallet: str, filter_value: str, filter_level: str) -> list[dict]:
    db = _validate_id(client.database)
    scope_clause = _scope_clause_for_filter(filter_value, filter_level)
    rows = client.query(f"""
        WITH scoped_tokens AS (
            SELECT
                token_id,
                any(condition_id) AS condition_id,
                any(question) AS question,
                any(outcome) AS outcome
            FROM {db}.token_metadata_latest_v2
            WHERE {scope_clause}
            GROUP BY token_id
        ),
        wallet_trades AS (
            SELECT
                trade_id,
                any(ts) AS ts,
                any(token_id) AS token_id
            FROM {db}.trades
            WHERE wallet = {_sql_quote(wallet)}
            GROUP BY trade_id
        )
        SELECT
            t.token_id AS token_id,
            any(st.condition_id) AS condition_id,
            any(st.question) AS question,
            any(st.outcome) AS outcome,
            min(t.ts) AS first_trade_ts,
            max(t.ts) AS last_trade_ts,
            count() AS visible_trade_count
        FROM wallet_trades AS t
        INNER JOIN scoped_tokens AS st ON st.token_id = t.token_id
        GROUP BY t.token_id
        ORDER BY first_trade_ts ASC, token_id ASC
    """)
    return [
        {
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "question": str(r.get("question") or ""),
            "outcome": str(r.get("outcome") or ""),
            "first_trade_ts": _parse_dt(r["first_trade_ts"]) if r.get("first_trade_ts") else None,
            "last_trade_ts": _parse_dt(r["last_trade_ts"]) if r.get("last_trade_ts") else None,
            "opening_shares": 0.0,
            "visible_trade_count": int(r.get("visible_trade_count") or 0),
        }
        for r in rows
    ]


def fetch_trades(client: ClickHouseClient, wallet: str, token_ids: list[str], window_start_date: date | None = None) -> list[dict]:
    db = _validate_id(client.database)
    token_list = ", ".join(_sql_quote(t) for t in token_ids)
    window_filter = ""
    if window_start_date is not None:
        window_start_dt = f"{window_start_date.isoformat()} 00:00:00"
        window_filter = f"\n          AND ts >= toDateTime({_sql_quote(window_start_dt)})"
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
                any(role) AS role,
                any(outcome) AS outcome
            FROM {db}.trades
            WHERE wallet = {_sql_quote(wallet)}
            GROUP BY trade_id
        )
        SELECT
            toDate(ts) AS trade_date, ts, trade_id, token_id, condition_id, side,
            shares, usdc, fee_usdc, price, role, outcome
        FROM wallet_trades
        WHERE token_id IN ({token_list})
          {window_filter}
        ORDER BY ts ASC, token_id ASC
    """)
    return [
        {
            "trade_date": _parse_date(r["trade_date"]),
            "ts": _parse_dt(r["ts"]),
            "trade_id": str(r.get("trade_id") or ""),
            "token_id": str(r["token_id"]),
            "condition_id": str(r["condition_id"]),
            "side": str(r["side"]),
            "shares": float(r["shares"]),
            "usdc": float(r["usdc"]),
            "fee_usdc": float(r["fee_usdc"]),
            "price": float(r["price"]),
            "role": str(r.get("role") or ""),
            "outcome": str(r.get("outcome") or ""),
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
                        opening_positions: dict[str, float] | None = None,
                        window_start_date: date | None = None) -> dict[str, Any]:
    """Reconstruct daily marked-to-market P&L series for the selected window."""
    if not token_scope:
        return None

    token_ids = [t["token_id"] for t in token_scope]
    opening_positions = opening_positions or {}
    latest_close_date = max((c["trade_date"] for c in closes), default=date.today())
    first_visible_trade_date = min((t["trade_date"] for t in trades), default=None)
    last_trade_date = max((t["trade_date"] for t in trades), default=None)
    chart_end_date = max(date.today(), last_trade_date or date.today(), latest_close_date)
    if window_start_date is None:
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


def get_wallet_curation_base_data(wallet: str, filter_value: str, filter_level: str = "detail") -> dict[str, Any] | None:
    """Fetch lifetime scoped wallet data for deriving curation windows without refetching."""
    from concurrent.futures import ThreadPoolExecutor

    client = ClickHouseClient()
    token_scope = fetch_token_scope_all_history(client, wallet, filter_value, filter_level)
    if not token_scope:
        return None
    token_ids = [t["token_id"] for t in token_scope]

    with ThreadPoolExecutor(max_workers=3) as pool:
        trades_future = pool.submit(fetch_trades, client, wallet, token_ids, None)
        closes_future = pool.submit(fetch_daily_closes, client, token_ids)
        resolutions_future = pool.submit(fetch_resolution_prices, client, token_ids)
        trades = trades_future.result()
        closes = closes_future.result()
        resolutions = resolutions_future.result()

    if not trades:
        return None

    return {
        "wallet": wallet,
        "filter_value": filter_value,
        "filter_level": filter_level,
        "token_scope": token_scope,
        "trades": trades,
        "closes": closes,
        "resolutions": resolutions,
    }


def _slice_curation_base_data(base_data: dict[str, Any], range_key: Any = CURATION_ALL_RANGE) -> dict[str, Any] | None:
    if not base_data:
        return None

    range_key = normalize_curation_range_key(range_key)
    full_token_scope = base_data["token_scope"]
    all_trades = base_data["trades"]
    closes = base_data["closes"]
    resolutions = base_data["resolutions"]

    if not full_token_scope or not all_trades:
        return None

    if range_key == CURATION_ALL_RANGE:
        window_start_date = min((t["trade_date"] for t in all_trades), default=date.today())
    else:
        window_start_date = _lookback_window_start(date.today(), CURATION_RANGE_DAYS[range_key])

    opening_positions: dict[str, float] = {}
    visible_trade_counts: dict[str, int] = {}
    trades: list[dict[str, Any]] = []

    for trade in all_trades:
        token_id = trade["token_id"]
        if trade["trade_date"] < window_start_date:
            signed_shares = trade["shares"] if trade["side"] == "BUY" else -trade["shares"]
            opening_positions[token_id] = opening_positions.get(token_id, 0.0) + signed_shares
            continue
        trades.append(trade)
        visible_trade_counts[token_id] = visible_trade_counts.get(token_id, 0) + 1

    token_scope = []
    scoped_opening_positions = {}
    for token in full_token_scope:
        token_id = token["token_id"]
        opening_shares = opening_positions.get(token_id, 0.0)
        visible_trade_count = visible_trade_counts.get(token_id, 0)
        if visible_trade_count <= 0 and abs(opening_shares) <= 1e-9:
            continue
        token_scope.append({
            **token,
            "opening_shares": opening_shares,
            "visible_trade_count": visible_trade_count,
        })
        scoped_opening_positions[token_id] = opening_shares

    if not token_scope:
        return None

    return {
        "wallet": base_data["wallet"],
        "filter_value": base_data["filter_value"],
        "filter_level": base_data["filter_level"],
        "range_key": range_key,
        "lookback_days": CURATION_RANGE_DAYS.get(range_key),
        "window_start_date": window_start_date,
        "token_scope": token_scope,
        "opening_positions": scoped_opening_positions,
        "trades": trades,
        "closes": closes,
        "resolutions": resolutions,
    }


def build_wallet_curation_payload_from_base(base_data: dict[str, Any], range_key: Any = CURATION_ALL_RANGE) -> dict[str, Any] | None:
    """Derive an interval-specific curation payload from a cached lifetime base dataset."""
    scoped_data = _slice_curation_base_data(base_data, range_key)
    if not scoped_data:
        return None

    wallet = scoped_data["wallet"]
    filter_value = scoped_data["filter_value"]
    token_scope = scoped_data["token_scope"]
    trades = scoped_data["trades"]
    closes = scoped_data["closes"]
    resolutions = scoped_data["resolutions"]
    scoped_opening_positions = scoped_data["opening_positions"]
    lookback_days = scoped_data["lookback_days"]
    chart = build_chart_payload(
        wallet,
        filter_value,
        lookback_days or 0,
        token_scope,
        trades,
        closes,
        resolutions,
        opening_positions=scoped_opening_positions,
        window_start_date=scoped_data["window_start_date"],
    )
    if not chart:
        return None

    token_ids = [t["token_id"] for t in token_scope]
    chart["meta"]["range_key"] = range_key
    chart["meta"]["lookback_days"] = lookback_days

    chart_end_date = _parse_date(chart["summary"]["chart_end_date"])
    opening_date = _parse_date(chart["summary"]["first_trade_date"]) - timedelta(days=1)
    final_prices = _build_final_price_lookup(token_ids, closes, resolutions, chart_end_date)
    opening_prices = _build_final_price_lookup(token_ids, closes, resolutions, opening_date)
    breakdown = compute_market_pnl_breakdown(token_scope, trades, final_prices, scoped_opening_positions, opening_prices)
    chart["breakdown"] = breakdown
    chart["signals"] = build_curation_signals(token_scope, trades, breakdown)
    chart["both_sides_rows"] = build_both_sides_rows(token_scope, trades, final_prices)

    vol = chart["summary"]["total_volume_usd"]
    pnl = chart["summary"]["final_pnl"]
    chart["summary"]["roi_pct"] = round((pnl / vol * 100), 2) if vol else 0
    chart["summary"]["win_rate"] = breakdown["win_rate"]
    chart["summary"]["active_days"] = chart["signals"]["active_days"]
    chart["summary"]["unique_markets"] = chart["signals"]["unique_markets"]
    return chart


def build_wallet_trade_audit_payload_from_base(base_data: dict[str, Any], range_key: Any = CURATION_ALL_RANGE,
                                               limit_per_side: int | None = 50) -> dict[str, Any]:
    scoped_data = _slice_curation_base_data(base_data, range_key)
    if not scoped_data or not scoped_data["trades"]:
        return {"total_rows": 0, "display_rows": [], "rows": []}

    token_scope = scoped_data["token_scope"]
    trades = scoped_data["trades"]
    closes = scoped_data["closes"]
    resolutions = scoped_data["resolutions"]
    token_ids = [token["token_id"] for token in token_scope]
    latest_close_date = max((c["trade_date"] for c in closes), default=date.today())
    last_trade_date = max((trade["trade_date"] for trade in trades), default=date.today())
    chart_end_date = max(date.today(), last_trade_date, latest_close_date)
    final_prices = _build_final_price_lookup(token_ids, closes, resolutions, chart_end_date)
    rows = build_trade_audit_rows(token_scope, trades, final_prices)
    display_rows = rows if limit_per_side is None else build_compact_trade_audit_rows(rows, limit_per_side=limit_per_side)
    return {
        "total_rows": len(rows),
        "display_rows": display_rows,
        "rows": rows,
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
