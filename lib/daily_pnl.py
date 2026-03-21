from collections import defaultdict
from datetime import date, datetime

from lib.pnl import load_active_wallet_filters
from lib.time_utils import day_bounds, parse_db_timestamp


def _coerce_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.fromisoformat(str(value)).date()


def _hidden_wallets(conn):
    return {
        row["wallet_address"]
        for row in conn.execute("SELECT wallet_address FROM hidden_wallets").fetchall()
    }


def _current_positions(conn):
    rows = conn.execute(
        """
        SELECT p.*, r.resolved, r.resolution_price, r.resolved_at, r.last_price
        FROM positions p
        LEFT JOIN resolutions r ON p.token_id = r.token_id
        """
    ).fetchall()
    return {
        (row["master_wallet"], row["token_id"]): row
        for row in rows
    }


def _wallet_meta(conn):
    wallet_pnl = {
        row["master_wallet"]: row
        for row in conn.execute("SELECT * FROM wallet_pnl").fetchall()
    }
    sim_rows = conn.execute(
        "SELECT wallet_address, MAX(sim_number) AS sim_number FROM sim_snapshots GROUP BY wallet_address"
    ).fetchall()
    sim_map = {row["wallet_address"]: row["sim_number"] for row in sim_rows}
    return wallet_pnl, sim_map


def _wallet_ids_in_range(invested_map, sells_map, resolutions_in_window):
    return set(invested_map) | set(sells_map) | set(resolutions_in_window)


def _wallet_ids_with_outside_range(filters, wallet_meta, stats_map, wallet_ids):
    wallet_ids = set(wallet_ids)
    wallet_ids.update(filters)
    wallet_ids.update(wallet_meta)
    wallet_ids.update(stats_map)
    return wallet_ids


def get_daily_breakdown(conn, start_date, end_date, include_hidden=False, include_outside_range=True):
    start_day = _coerce_date(start_date)
    end_day = _coerce_date(end_date)
    start_dt, _ = day_bounds(start_day)
    _, end_dt = day_bounds(end_day)
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    hidden = _hidden_wallets(conn)
    filters = load_active_wallet_filters(conn)
    positions = _current_positions(conn)
    wallet_meta, sim_map = _wallet_meta(conn)

    invested_rows = conn.execute(
        """
        SELECT master_wallet, token_id, SUM(invested) AS invested
        FROM trades
        WHERE action = 'Buy' AND timestamp >= ? AND timestamp <= ?
        GROUP BY master_wallet, token_id
        """,
        (start_iso, end_iso),
    ).fetchall()
    sells_rows = conn.execute(
        """
        SELECT master_wallet, token_id, SUM(shares) AS shares_sold, SUM(received) AS received
        FROM trades
        WHERE action = 'Sell' AND timestamp >= ? AND timestamp <= ?
        GROUP BY master_wallet, token_id
        """,
        (start_iso, end_iso),
    ).fetchall()
    resolution_rows = conn.execute(
        """
        SELECT token_id, resolved, resolution_price, resolved_at
        FROM resolutions
        WHERE resolved IN (1, -1, 2) AND resolved_at >= ? AND resolved_at <= ?
        """,
        (start_iso, end_iso),
    ).fetchall()
    stats_rows = conn.execute(
        """
        SELECT master_wallet, COUNT(*) AS total_trades, COUNT(DISTINCT market) AS unique_markets
        FROM trades
        WHERE timestamp >= ? AND timestamp <= ?
        GROUP BY master_wallet
        """,
        (start_iso, end_iso),
    ).fetchall()

    invested_map = defaultdict(dict)
    for row in invested_rows:
        invested_map[row["master_wallet"]][row["token_id"]] = float(row["invested"] or 0)

    sells_map = defaultdict(dict)
    for row in sells_rows:
        sells_map[row["master_wallet"]][row["token_id"]] = {
            "shares_sold": float(row["shares_sold"] or 0),
            "received": float(row["received"] or 0),
        }

    resolutions_in_window = defaultdict(list)
    for row in resolution_rows:
        for (wallet, token_id), pos in positions.items():
            if token_id == row["token_id"]:
                resolutions_in_window[wallet].append((pos, row))

    stats_map = {
        row["master_wallet"]: {
            "trades": row["total_trades"],
            "markets": row["unique_markets"],
        }
        for row in stats_rows
    }

    wallet_ids = _wallet_ids_in_range(invested_map, sells_map, resolutions_in_window)
    if include_outside_range:
        wallet_ids = _wallet_ids_with_outside_range(filters, wallet_meta, stats_map, wallet_ids)
    rows = []
    true_totals = {"invested": 0.0, "realized": 0.0, "unrealized": 0.0, "total": 0.0}

    for wallet in sorted(wallet_ids):
        invested = sum(invested_map[wallet].values())
        realized = 0.0
        unrealized = 0.0

        for token_id, sell_data in sells_map[wallet].items():
            pos = positions.get((wallet, token_id))
            if not pos:
                continue
            avg_cost = float(pos["avg_cost_basis"] or 0)
            realized += sell_data["received"] - (avg_cost * sell_data["shares_sold"])

        unresolved_tokens_seen = set()
        for pos, resolution in resolutions_in_window[wallet]:
            avg_cost = float(pos["avg_cost_basis"] or 0)
            net_shares = float(pos["net_shares"] or 0)
            resolution_price = float(resolution["resolution_price"] or 0)
            if resolution["resolved"] == 1:
                realized += (net_shares * 1.0) - (avg_cost * net_shares)
            elif resolution["resolved"] == -1:
                realized -= avg_cost * net_shares
            else:
                realized += (net_shares * resolution_price) - (avg_cost * net_shares)
            unresolved_tokens_seen.add(pos["token_id"])

        for token_id in invested_map[wallet]:
            pos = positions.get((wallet, token_id))
            if not pos or token_id in unresolved_tokens_seen:
                continue
            resolved = pos["resolved"] if pos["resolved"] is not None else 0
            resolved_at = parse_db_timestamp(pos["resolved_at"]) if pos["resolved_at"] else None
            if resolved in (1, -1, 2) and resolved_at and start_dt <= resolved_at <= end_dt:
                continue
            if resolved != 0:
                continue

            avg_cost = float(pos["avg_cost_basis"] or 0)
            net_shares = float(pos["net_shares"] or 0)
            if pos["last_price"] is not None:
                unrealized += (float(pos["last_price"]) * net_shares) - (avg_cost * net_shares)

        total = realized + unrealized
        true_totals["invested"] += invested
        true_totals["realized"] += realized
        true_totals["unrealized"] += unrealized
        true_totals["total"] += total

        is_hidden = wallet in hidden
        if is_hidden and not include_hidden:
            continue

        stats = stats_map.get(wallet, {"trades": 0, "markets": 0})
        meta = wallet_meta.get(wallet, {})
        rows.append(
            {
                "hide": "Unhide" if is_hidden else "Hide",
                "wallet": wallet,
                "wallet_address": wallet,
                "filter": filters.get(wallet, "-"),
                "actual": meta["game"] if meta and meta["game"] else "-",
                "sim": f"#{sim_map[wallet]}" if wallet in sim_map and sim_map[wallet] else "-",
                "invested": round(invested, 2),
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(unrealized, 2),
                "total_pnl": round(total, 2),
                "markets": int(stats["markets"]),
                "trades": int(stats["trades"]),
                "in_csv": "Yes" if wallet in filters else "No",
                "hidden": is_hidden,
            }
        )

    rows.sort(key=lambda row: row["total_pnl"], reverse=True)
    visible_totals = {
        "invested": round(sum(row["invested"] for row in rows), 2),
        "realized": round(sum(row["realized_pnl"] for row in rows), 2),
        "unrealized": round(sum(row["unrealized_pnl"] for row in rows), 2),
        "total": round(sum(row["total_pnl"] for row in rows), 2),
    }
    return {
        "rows": rows,
        "totals": visible_totals,
        "true_totals": {key: round(value, 2) for key, value in true_totals.items()},
        "hidden_count": len(hidden),
    }
