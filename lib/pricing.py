import json
import logging
import time

import requests

from lib.normalizers import token_id_to_decimal
from lib.time_utils import now_utc, to_db_timestamp

logger = logging.getLogger(__name__)

CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
DELAY_BETWEEN_CALLS = 0.05
BATCH_SIZE = 50


def _persist_prices(conn, prices, checked_at):
    timestamp = to_db_timestamp(checked_at)
    for token_id, price in prices.items():
        conn.execute(
            "UPDATE resolutions SET last_price = ?, price_checked_at = ? WHERE token_id = ?",
            (price, timestamp, token_id),
        )
    conn.commit()


def fetch_prices(conn):
    """Fetch current sell prices for unresolved tokens.

    Uses executable CLOB sell price first, then Gamma outcomePrices as fallback.
    The result is also cached on the resolutions table for dashboard queries.
    """
    unresolved = [
        row["token_id"]
        for row in conn.execute(
            "SELECT token_id FROM resolutions WHERE resolved = 0 ORDER BY token_id"
        ).fetchall()
    ]
    if not unresolved:
        return {}

    prices = {}
    gamma_fallback = []
    total = len(unresolved)

    print(f"  Fetching live prices for {total} open tokens...")
    for i, token_id in enumerate(unresolved, start=1):
        dec_id = token_id_to_decimal(token_id)
        try:
            response = requests.get(
                f"{CLOB_API_URL}/price",
                params={"token_id": dec_id, "side": "sell"},
                timeout=10,
            )
            if response.status_code == 200:
                payload = response.json()
                price = float(payload.get("price", 0))
                prices[token_id] = price
            else:
                gamma_fallback.append(token_id)
        except Exception as exc:
            logger.warning("CLOB /price failed for %s: %s", token_id[:20], exc)
            gamma_fallback.append(token_id)

        if i % 10 == 0 or i == total:
            print(
                f"    {i}/{total} done ({len(prices)} priced, {len(gamma_fallback)} fallback)"
            )
        if i < total:
            time.sleep(DELAY_BETWEEN_CALLS)

    if gamma_fallback:
        _gamma_fallback(gamma_fallback, prices)

    if prices:
        _persist_prices(conn, prices, now_utc())
    return prices


def _gamma_fallback(token_ids, prices):
    logger.info("Falling back to Gamma API for %d tokens", len(token_ids))
    for start in range(0, len(token_ids), BATCH_SIZE):
        batch = token_ids[start:start + BATCH_SIZE]
        params = [("clob_token_ids", token_id_to_decimal(token_id)) for token_id in batch]
        params.append(("limit", "100"))

        try:
            response = requests.get(GAMMA_API_URL, params=params, timeout=30)
            response.raise_for_status()
            markets = response.json()
        except Exception as exc:
            logger.warning("Gamma price fallback failed: %s", exc)
            continue

        if not isinstance(markets, list):
            continue

        price_by_decimal = {}
        for market in markets:
            try:
                clob_ids = json.loads(market.get("clobTokenIds", "[]"))
                prices_raw = json.loads(market.get("outcomePrices", "[]"))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Failed to parse Gamma fallback payload: %s", exc)
                continue

            for index, clob_id in enumerate(clob_ids):
                if index < len(prices_raw):
                    try:
                        price_by_decimal[str(clob_id)] = float(prices_raw[index])
                    except (TypeError, ValueError):
                        continue

        for token_id in batch:
            dec_id = token_id_to_decimal(token_id)
            if dec_id in price_by_decimal:
                prices[token_id] = price_by_decimal[dec_id]

        if start + BATCH_SIZE < len(token_ids):
            time.sleep(DELAY_BETWEEN_CALLS)

    fetched = sum(1 for token_id in token_ids if token_id in prices)
    if fetched:
        print(f"    Gamma fallback: priced {fetched}/{len(token_ids)} tokens")
