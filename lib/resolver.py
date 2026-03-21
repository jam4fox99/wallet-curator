import json
import logging
import time

import requests

from lib.normalizers import token_id_to_decimal

logger = logging.getLogger(__name__)

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
BATCH_SIZE = 50
DELAY_BETWEEN_BATCHES = 0.05  # 50ms


def check_resolutions(conn):
    """Check all unresolved tokens against the Gamma API. Returns count of newly resolved."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT token_id, outcome FROM resolutions
        WHERE resolved = 0
        AND (checked_at IS NULL OR checked_at < datetime('now', '-1 hour'))
    """)
    unresolved = cursor.fetchall()

    if not unresolved:
        return 0

    total = len(unresolved)
    newly_resolved = 0
    checked = 0

    # Process in batches
    for batch_start in range(0, total, BATCH_SIZE):
        batch = unresolved[batch_start:batch_start + BATCH_SIZE]

        # Build lookup of hex -> (token_id, stored_outcome)
        token_map = {}
        params = []
        for row in batch:
            hex_id = row['token_id']
            dec_id = token_id_to_decimal(hex_id)
            token_map[dec_id] = (hex_id, row['outcome'])
            params.append(('clob_token_ids', dec_id))

        # limit=100 needed because API defaults to 20 markets per response
        params.append(('limit', '100'))

        try:
            response = requests.get(GAMMA_API_URL, params=params, timeout=30)
            response.raise_for_status()
            markets = response.json()
        except Exception as e:
            logger.warning("Gamma API request failed: %s", e)
            # Update checked_at so we don't hammer on errors
            for dec_id, (hex_id, _) in token_map.items():
                cursor.execute(
                    "UPDATE resolutions SET checked_at = datetime('now') WHERE token_id = ?",
                    (hex_id,)
                )
            conn.commit()
            checked += len(batch)
            print(f"  ⚠️ API error on batch — {len(batch)} tokens skipped")
            continue

        if not isinstance(markets, list):
            logger.warning("Unexpected API response: %s", str(markets)[:200])
            checked += len(batch)
            continue

        # Build resolution lookup from API response
        resolved_tokens = {}  # decimal_token_id -> (won: bool, market_closed: bool)
        for market in markets:
            try:
                clob_ids = json.loads(market.get('clobTokenIds', '[]'))
                outcomes = json.loads(market.get('outcomes', '[]'))
                prices = json.loads(market.get('outcomePrices', '[]'))
                is_closed = market.get('closed', False)

                for i, clob_id in enumerate(clob_ids):
                    if i < len(prices):
                        resolved_tokens[str(clob_id)] = {
                            'price': prices[i],
                            'outcome': outcomes[i] if i < len(outcomes) else None,
                            'closed': is_closed,
                        }
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.warning("Failed to parse market response: %s", e)
                continue

        # Match our tokens against results
        for dec_id, (hex_id, stored_outcome) in token_map.items():
            if dec_id in resolved_tokens:
                info = resolved_tokens[dec_id]
                if info['closed']:
                    if info['price'] == '1':
                        cursor.execute("""
                            UPDATE resolutions
                            SET resolved = 1, resolution_price = 1.0,
                                resolved_at = datetime('now'), checked_at = datetime('now')
                            WHERE token_id = ?
                        """, (hex_id,))
                        newly_resolved += 1
                    elif info['price'] == '0':
                        cursor.execute("""
                            UPDATE resolutions
                            SET resolved = -1, resolution_price = 0.0,
                                resolved_at = datetime('now'), checked_at = datetime('now')
                            WHERE token_id = ?
                        """, (hex_id,))
                        newly_resolved += 1
                    else:
                        # Unexpected price value
                        cursor.execute(
                            "UPDATE resolutions SET checked_at = datetime('now') WHERE token_id = ?",
                            (hex_id,)
                        )
                else:
                    # Market not closed yet
                    cursor.execute(
                        "UPDATE resolutions SET checked_at = datetime('now') WHERE token_id = ?",
                        (hex_id,)
                    )
            else:
                # Token not found in any returned market — mark unresolvable
                cursor.execute("""
                    UPDATE resolutions
                    SET resolved = -2, checked_at = datetime('now')
                    WHERE token_id = ?
                """, (hex_id,))
                logger.info("Token %s not found in API — marked unresolvable", hex_id[:20])

        conn.commit()
        checked += len(batch)

        if total > BATCH_SIZE:
            print(f"  Checking resolutions... {min(checked, total)}/{total} done")

        if batch_start + BATCH_SIZE < total:
            time.sleep(DELAY_BETWEEN_BATCHES)

    return newly_resolved
