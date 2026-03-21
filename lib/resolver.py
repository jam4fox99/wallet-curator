import json
import logging
import time
from datetime import timedelta

import requests

from lib.normalizers import token_id_to_decimal
from lib.time_utils import now_utc, parse_db_timestamp, to_db_timestamp

logger = logging.getLogger(__name__)

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
CLOB_API_URL = "https://clob.polymarket.com"
BATCH_SIZE = 50
DELAY_BETWEEN_BATCHES = 0.05


def _extract_market_resolved_at(market):
    for key in ("resolutionDate", "resolveDate", "closedTime", "endDate", "updatedAt"):
        value = market.get(key)
        if not value:
            continue
        try:
            return parse_db_timestamp(value)
        except ValueError:
            continue
    return None


def _fallback_resolved_at(conn, token_id):
    row = conn.execute(
        "SELECT MAX(timestamp) AS last_buy FROM trades WHERE token_id = ? AND action = 'Buy'",
        (token_id,),
    ).fetchone()
    if row and row["last_buy"]:
        try:
            last_buy = parse_db_timestamp(row["last_buy"])
            return last_buy + timedelta(hours=24)
        except ValueError:
            pass
    logger.warning("No buy timestamp found for %s; using current time as resolved_at fallback", token_id[:20])
    return now_utc()


def _update_checked_at(conn, token_id, checked_at):
    conn.execute(
        "UPDATE resolutions SET checked_at = ? WHERE token_id = ?",
        (to_db_timestamp(checked_at), token_id),
    )


def _token_exists_on_clob(token_id):
    try:
        response = requests.get(
            f"{CLOB_API_URL}/book",
            params={"token_id": token_id_to_decimal(token_id)},
            timeout=10,
        )
        return response.status_code == 200
    except Exception as exc:
        logger.warning("CLOB existence fallback failed for %s: %s", token_id[:20], exc)
        return True


def _select_due_unresolved(conn):
    threshold = now_utc() - timedelta(hours=1)
    rows = conn.execute(
        "SELECT token_id, outcome, checked_at FROM resolutions WHERE resolved = 0 ORDER BY token_id"
    ).fetchall()
    due = []
    for row in rows:
        checked_at = parse_db_timestamp(row["checked_at"]) if row["checked_at"] else None
        if checked_at is None or checked_at < threshold:
            due.append(row)
    return due


def check_resolutions(conn):
    """Check unresolved tokens against Gamma, with a CLOB existence fallback."""
    unresolved = _select_due_unresolved(conn)
    if not unresolved:
        return 0

    total = len(unresolved)
    checked = 0
    newly_resolved = 0

    for start in range(0, total, BATCH_SIZE):
        batch = unresolved[start:start + BATCH_SIZE]
        token_map = {}
        params = []
        for row in batch:
            dec_id = token_id_to_decimal(row["token_id"])
            token_map[dec_id] = row["token_id"]
            params.append(("clob_token_ids", dec_id))
        params.append(("limit", "100"))

        checked_at = now_utc()
        try:
            response = requests.get(GAMMA_API_URL, params=params, timeout=30)
            response.raise_for_status()
            markets = response.json()
        except Exception as exc:
            logger.warning("Gamma API request failed: %s", exc)
            for row in batch:
                _update_checked_at(conn, row["token_id"], checked_at)
            conn.commit()
            checked += len(batch)
            continue

        gamma_info = {}
        if isinstance(markets, list):
            for market in markets:
                try:
                    clob_ids = json.loads(market.get("clobTokenIds", "[]"))
                    outcomes = json.loads(market.get("outcomes", "[]"))
                    prices = json.loads(market.get("outcomePrices", "[]"))
                except (TypeError, json.JSONDecodeError) as exc:
                    logger.warning("Failed to parse Gamma response: %s", exc)
                    continue

                resolved_at = _extract_market_resolved_at(market)
                closed = bool(market.get("closed"))
                for index, clob_id in enumerate(clob_ids):
                    if index >= len(prices):
                        continue
                    try:
                        price = float(prices[index])
                    except (TypeError, ValueError):
                        continue
                    gamma_info[str(clob_id)] = {
                        "price": price,
                        "outcome": outcomes[index] if index < len(outcomes) else None,
                        "closed": closed,
                        "resolved_at": resolved_at,
                    }

        missing_from_gamma = []
        for dec_id, token_id in token_map.items():
            info = gamma_info.get(dec_id)
            if not info:
                missing_from_gamma.append(token_id)
                continue

            if not info["closed"]:
                _update_checked_at(conn, token_id, checked_at)
                continue

            price = info["price"]
            if price >= 0.99:
                resolved = 1
                resolution_price = 1.0
            elif price <= 0.01:
                resolved = -1
                resolution_price = 0.0
            else:
                resolved = 2
                resolution_price = price

            resolved_at = info["resolved_at"] or _fallback_resolved_at(conn, token_id)
            conn.execute(
                """
                UPDATE resolutions
                SET resolved = ?, resolution_price = ?, resolved_at = ?, checked_at = ?
                WHERE token_id = ?
                """,
                (
                    resolved,
                    resolution_price,
                    to_db_timestamp(resolved_at),
                    to_db_timestamp(checked_at),
                    token_id,
                ),
            )
            newly_resolved += 1

        for token_id in missing_from_gamma:
            if _token_exists_on_clob(token_id):
                _update_checked_at(conn, token_id, checked_at)
            else:
                conn.execute(
                    "UPDATE resolutions SET resolved = -2, checked_at = ? WHERE token_id = ?",
                    (to_db_timestamp(checked_at), token_id),
                )

        conn.commit()
        checked += len(batch)
        if total > BATCH_SIZE:
            print(f"  Checking resolutions... {checked}/{total} done")
        if start + BATCH_SIZE < total:
            time.sleep(DELAY_BETWEEN_BATCHES)

    return newly_resolved
