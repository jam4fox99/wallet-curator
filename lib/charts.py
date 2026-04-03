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


def _load_series_rows(conn, wallet=None):
    params = []
    if wallet is None:
        where = "master_wallet IS NULL"
    else:
        where = "master_wallet = ?"
        params.append(wallet)

    rows = conn.execute(
        f"""
        SELECT recorded_at, total_pnl, realized_pnl, unrealized_pnl
        FROM pnl_history
        WHERE {where}
        ORDER BY recorded_at
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "recorded_at": parse_db_timestamp(row["recorded_at"]),
            "total_pnl": float(row["total_pnl"] or 0),
            "realized_pnl": float(row["realized_pnl"] or 0),
            "unrealized_pnl": float(row["unrealized_pnl"] or 0),
        }
        for row in rows
    ]


def _select_visible_window(points, range_key):
    if not points:
        return None, [], None, None

    range_key = range_key if range_key in RANGE_DELTAS else "ALL"
    end_at = points[-1]["recorded_at"]

    if range_key == "ALL":
        return points[0], list(points), points[0]["recorded_at"], end_at

    cutoff = now_utc() - RANGE_DELTAS[range_key]
    baseline = None
    visible = []

    for point in points:
        if point["recorded_at"] <= cutoff:
            baseline = point
        if point["recorded_at"] >= cutoff:
            visible.append(point)

    if not visible:
        visible = [points[-1]]

    if baseline is None:
        baseline = visible[0]

    if visible[0]["recorded_at"] > cutoff:
        visible = [
            {
                "recorded_at": cutoff,
                "total_pnl": baseline["total_pnl"],
                "realized_pnl": baseline["realized_pnl"],
                "unrealized_pnl": baseline["unrealized_pnl"],
            }
        ] + visible

    return baseline, visible, cutoff, end_at


def get_chart_payload(conn, wallet=None, range_key="ALL"):
    points = _load_series_rows(conn, wallet=wallet)
    baseline, visible, start_at, end_at = _select_visible_window(points, range_key)

    if not visible or baseline is None:
        return {
            "range_key": range_key,
            "series": [],
            "start_at": None,
            "end_at": None,
            "baseline_total_pnl": 0.0,
            "current_delta_pnl": 0.0,
            "current_total_pnl": 0.0,
        }

    baseline_total = baseline["total_pnl"]
    current_total = points[-1]["total_pnl"]

    series = []
    for point in visible:
        series.append(
            {
                "time": int(point["recorded_at"].timestamp()),
                "value": round(point["total_pnl"] - baseline_total, 2),
                "absolute_value": round(point["total_pnl"], 2),
            }
        )

    return {
        "range_key": range_key,
        "series": series,
        "start_at": to_db_timestamp(start_at),
        "end_at": to_db_timestamp(end_at),
        "baseline_total_pnl": round(baseline_total, 2),
        "current_delta_pnl": round(current_total - baseline_total, 2),
        "current_total_pnl": round(current_total, 2),
    }


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
