import logging
import time

import requests

from lib.normalizers import token_id_to_decimal

logger = logging.getLogger(__name__)

CLOB_API_URL = "https://clob.polymarket.com"
DELAY_BETWEEN_CALLS = 0.05  # 50ms


def fetch_prices(conn):
    """Fetch current sell prices for all unresolved tokens.

    Uses CLOB API /price?side=sell (executable exit price).
    Falls back to Gamma API outcomePrices if CLOB returns 404.

    Returns: {token_id_hex: current_price_float}
    """
    cursor = conn.execute("""
        SELECT token_id FROM resolutions WHERE resolved = 0
    """)
    unresolved = [row['token_id'] for row in cursor.fetchall()]

    if not unresolved:
        return {}

    prices = {}
    failed_tokens = []  # tokens where CLOB returned 404
    total = len(unresolved)

    print(f"  Fetching live prices for {total} open tokens...")

    for i, hex_id in enumerate(unresolved):
        dec_id = token_id_to_decimal(hex_id)

        try:
            r = requests.get(
                f"{CLOB_API_URL}/price",
                params={"token_id": dec_id, "side": "sell"},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                price = float(data.get("price", 0))
                prices[hex_id] = price
            elif r.status_code == 404:
                failed_tokens.append(hex_id)
            else:
                logger.warning("CLOB /price returned %d for %s", r.status_code, hex_id[:20])
                failed_tokens.append(hex_id)
        except Exception as e:
            logger.warning("CLOB /price failed for %s: %s", hex_id[:20], e)
            failed_tokens.append(hex_id)

        if (i + 1) % 10 == 0 or i + 1 == total:
            print(f"    {i + 1}/{total} done ({len(prices)} priced, {len(failed_tokens)} no order book)")

        if i < total - 1:
            time.sleep(DELAY_BETWEEN_CALLS)

    # Fallback: use Gamma API outcomePrices for tokens with no CLOB order book
    if failed_tokens:
        _gamma_fallback(failed_tokens, prices)

    return prices


def _gamma_fallback(token_ids, prices):
    """Fetch prices from Gamma API outcomePrices for tokens missing from CLOB."""
    import json
    from lib.normalizers import token_id_to_decimal

    GAMMA_URL = "https://gamma-api.polymarket.com/markets"
    BATCH_SIZE = 50

    logger.info("Falling back to Gamma API for %d tokens", len(token_ids))

    for batch_start in range(0, len(token_ids), BATCH_SIZE):
        batch = token_ids[batch_start:batch_start + BATCH_SIZE]

        params = [("clob_token_ids", token_id_to_decimal(t)) for t in batch]
        params.append(("limit", "100"))

        try:
            r = requests.get(GAMMA_URL, params=params, timeout=30)
            r.raise_for_status()
            markets = r.json()
        except Exception as e:
            logger.warning("Gamma API fallback failed: %s", e)
            continue

        if not isinstance(markets, list):
            continue

        # Build token -> price lookup from response
        for market in markets:
            try:
                clob_ids = json.loads(market.get("clobTokenIds", "[]"))
                outcome_prices = json.loads(market.get("outcomePrices", "[]"))

                for i, clob_id in enumerate(clob_ids):
                    if i < len(outcome_prices):
                        # Match against our hex tokens
                        for hex_id in batch:
                            if token_id_to_decimal(hex_id) == str(clob_id):
                                prices[hex_id] = float(outcome_prices[i])
                                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse Gamma fallback: %s", e)

        if batch_start + BATCH_SIZE < len(token_ids):
            time.sleep(DELAY_BETWEEN_CALLS)

    fetched = sum(1 for t in token_ids if t in prices)
    if fetched:
        print(f"    Gamma fallback: priced {fetched}/{len(token_ids)} tokens")
