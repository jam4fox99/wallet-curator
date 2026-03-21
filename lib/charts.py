from datetime import timedelta

from lib.pnl import load_active_wallet_filters
from lib.time_utils import now_utc, parse_db_timestamp, to_db_timestamp

RANGE_DELTAS = {
    "1D": timedelta(days=1),
    "3D": timedelta(days=3),
    "7D": timedelta(days=7),
    "15D": timedelta(days=15),
    "30D": timedelta(days=30),
    "ALL": None,
}


def get_time_series(conn, wallet=None, range_key="ALL"):
    range_key = range_key if range_key in RANGE_DELTAS else "ALL"
    cutoff = None
    if RANGE_DELTAS[range_key] is not None:
        cutoff = now_utc() - RANGE_DELTAS[range_key]

    where_clauses = []
    params = []
    if wallet is None:
        where_clauses.append("master_wallet IS NULL")
    else:
        where_clauses.append("master_wallet = ?")
        params.append(wallet)
    if cutoff is not None:
        where_clauses.append("recorded_at >= ?")
        params.append(to_db_timestamp(cutoff))

    sql = (
        "SELECT recorded_at, total_pnl, realized_pnl, unrealized_pnl "
        f"FROM pnl_history WHERE {' AND '.join(where_clauses)} ORDER BY recorded_at"
    )
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "recorded_at": parse_db_timestamp(row["recorded_at"]),
            "total_pnl": float(row["total_pnl"] or 0),
            "realized_pnl": float(row["realized_pnl"] or 0),
            "unrealized_pnl": float(row["unrealized_pnl"] or 0),
        }
        for row in rows
    ]


def get_wallet_options(conn):
    rows = conn.execute(
        "SELECT master_wallet, game, total_pnl FROM wallet_pnl ORDER BY total_pnl DESC, master_wallet ASC"
    ).fetchall()
    options = []
    for row in rows:
        wallet = row["master_wallet"]
        label = wallet
        if row["game"]:
            label = f"{label} ({row['game']})"
        options.append({"label": label, "value": wallet})
    return options


def get_wallet_stats(conn, wallet):
    filters = load_active_wallet_filters(conn)
    row = conn.execute(
        "SELECT * FROM wallet_pnl WHERE master_wallet = ?",
        (wallet,),
    ).fetchone()
    if not row:
        return None
    return {
        "wallet": wallet,
        "filter": filters.get(wallet, "-"),
        "game": row["game"] or "-",
        "invested": float(row["total_invested"] or 0),
        "realized": float(row["realized_pnl"] or 0),
        "unrealized": float(row["unrealized_pnl"] or 0),
        "total": float(row["total_pnl"] or 0),
        "markets": int(row["unique_markets"] or 0),
        "trades": int(row["total_trades"] or 0),
        "excluded_positions": int(row["excluded_positions"] or 0),
        "first_trade": row["first_trade"],
        "last_trade": row["last_trade"],
    }


def get_sync_status_summary(conn):
    sync = conn.execute("SELECT * FROM sync_status WHERE id = 1").fetchone()
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    latest_pipeline = conn.execute(
        "SELECT * FROM pipeline_log ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {
        "sync": sync,
        "total_trades": total_trades,
        "latest_pipeline": latest_pipeline,
    }
