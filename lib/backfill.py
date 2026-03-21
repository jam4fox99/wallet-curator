import logging
from collections import defaultdict
from datetime import timedelta

from lib.time_utils import day_bounds, iter_days, now_utc, parse_db_timestamp, to_db_timestamp

logger = logging.getLogger(__name__)


def ensure_resolved_at_fallbacks(conn):
    rows = conn.execute(
        """
        SELECT token_id
        FROM resolutions
        WHERE resolved IN (1, -1, 2) AND resolved_at IS NULL
        ORDER BY token_id
        """
    ).fetchall()
    updated = 0
    for row in rows:
        buy_row = conn.execute(
            "SELECT MAX(timestamp) AS last_buy FROM trades WHERE token_id = ? AND action = 'Buy'",
            (row["token_id"],),
        ).fetchone()
        if buy_row and buy_row["last_buy"]:
            resolved_at = parse_db_timestamp(buy_row["last_buy"]) + timedelta(hours=24)
        else:
            resolved_at = now_utc()
        conn.execute(
            "UPDATE resolutions SET resolved_at = ? WHERE token_id = ?",
            (to_db_timestamp(resolved_at), row["token_id"]),
        )
        updated += 1
    if updated:
        conn.commit()
    return updated


def _positions_as_of(conn, cutoff_iso):
    return conn.execute(
        """
        SELECT
            master_wallet,
            token_id,
            MAX(market) AS market,
            MAX(outcome) AS outcome,
            MAX(game) AS game,
            SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) AS total_shares_bought,
            SUM(CASE WHEN action='Buy' THEN invested ELSE 0 END) AS total_invested,
            SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END) AS total_shares_sold,
            SUM(CASE WHEN action='Sell' THEN received ELSE 0 END) AS total_received,
            SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
              - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END) AS net_shares
        FROM trades
        WHERE timestamp <= ?
        GROUP BY master_wallet, token_id
        HAVING SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) > 0
           AND (SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
              - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END)) >= 0
        ORDER BY master_wallet, token_id
        """,
        (cutoff_iso,),
    ).fetchall()


def _stats_as_of(conn, cutoff_iso):
    rows = conn.execute(
        """
        SELECT master_wallet, COUNT(*) AS total_trades, COUNT(DISTINCT market) AS unique_markets
        FROM trades
        WHERE timestamp <= ?
        GROUP BY master_wallet
        """,
        (cutoff_iso,),
    ).fetchall()
    return {
        row["master_wallet"]: {
            "total_trades": row["total_trades"],
            "unique_markets": row["unique_markets"],
        }
        for row in rows
    }


def _resolution_map(conn):
    rows = conn.execute(
        """
        SELECT token_id, resolved, resolution_price, resolved_at
        FROM resolutions
        WHERE resolved IN (1, -1, 2)
        """
    ).fetchall()
    result = {}
    for row in rows:
        resolved_at = parse_db_timestamp(row["resolved_at"]) if row["resolved_at"] else None
        result[row["token_id"]] = {
            "resolved": row["resolved"],
            "resolution_price": float(row["resolution_price"] or 0),
            "resolved_at": resolved_at,
        }
    return result


def _compute_wallet_rows_as_of(conn, cutoff_dt):
    cutoff_iso = to_db_timestamp(cutoff_dt)
    positions = _positions_as_of(conn, cutoff_iso)
    stats = _stats_as_of(conn, cutoff_iso)
    resolution_map = _resolution_map(conn)

    wallet_rows = defaultdict(
        lambda: {
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "total_invested": 0.0,
            "total_trades": 0,
            "unique_markets": 0,
        }
    )

    for pos in positions:
        wallet = pos["master_wallet"]
        shares_bought = float(pos["total_shares_bought"] or 0)
        invested = float(pos["total_invested"] or 0)
        shares_sold = float(pos["total_shares_sold"] or 0)
        received = float(pos["total_received"] or 0)
        net_shares = float(pos["net_shares"] or 0)
        avg_cost = invested / shares_bought if shares_bought else 0.0

        wallet_rows[wallet]["total_invested"] += invested
        wallet_rows[wallet]["realized_pnl"] += received - (avg_cost * shares_sold)

        resolution = resolution_map.get(pos["token_id"])
        if resolution and resolution["resolved_at"] and resolution["resolved_at"] <= cutoff_dt:
            if resolution["resolved"] == 1:
                wallet_rows[wallet]["realized_pnl"] += (net_shares * 1.0) - (avg_cost * net_shares)
            elif resolution["resolved"] == -1:
                wallet_rows[wallet]["realized_pnl"] -= avg_cost * net_shares
            elif resolution["resolved"] == 2:
                wallet_rows[wallet]["realized_pnl"] += (
                    net_shares * resolution["resolution_price"]
                ) - (avg_cost * net_shares)

    for wallet, data in wallet_rows.items():
        data["total_pnl"] = data["realized_pnl"] + data["unrealized_pnl"]
        data["total_trades"] = stats.get(wallet, {}).get("total_trades", 0)
        data["unique_markets"] = stats.get(wallet, {}).get("unique_markets", 0)

    return wallet_rows


def backfill_pnl_history(conn):
    """Seed daily history rows when trades exist but pnl_history is empty."""
    existing = conn.execute("SELECT COUNT(*) FROM pnl_history").fetchone()[0]
    if existing:
        return 0

    trade_bounds = conn.execute(
        "SELECT MIN(timestamp) AS first_trade, MAX(timestamp) AS last_trade FROM trades"
    ).fetchone()
    if not trade_bounds or not trade_bounds["first_trade"]:
        return 0

    ensure_resolved_at_fallbacks(conn)

    first_trade = parse_db_timestamp(trade_bounds["first_trade"])
    end_day = now_utc().date()
    inserted = 0

    for day in iter_days(first_trade.date(), end_day):
        _, day_end = day_bounds(day)
        wallet_rows = _compute_wallet_rows_as_of(conn, day_end)
        if not wallet_rows:
            continue

        aggregate = {
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "total_invested": 0.0,
            "total_trades": 0,
            "unique_markets": 0,
        }
        for wallet, data in wallet_rows.items():
            conn.execute(
                """
                INSERT INTO pnl_history (
                    recorded_at, master_wallet, realized_pnl, unrealized_pnl,
                    total_pnl, total_invested, total_trades, unique_markets
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_db_timestamp(day_end),
                    wallet,
                    data["realized_pnl"],
                    data["unrealized_pnl"],
                    data["total_pnl"],
                    data["total_invested"],
                    data["total_trades"],
                    data["unique_markets"],
                ),
            )
            inserted += 1
            for key in aggregate:
                aggregate[key] += data[key]

        conn.execute(
            """
            INSERT INTO pnl_history (
                recorded_at, master_wallet, realized_pnl, unrealized_pnl,
                total_pnl, total_invested, total_trades, unique_markets
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_db_timestamp(day_end),
                aggregate["realized_pnl"],
                aggregate["unrealized_pnl"],
                aggregate["total_pnl"],
                aggregate["total_invested"],
                aggregate["total_trades"],
                aggregate["unique_markets"],
            ),
        )
        inserted += 1

    conn.commit()
    logger.info("Backfilled %d pnl_history rows", inserted)
    return inserted
