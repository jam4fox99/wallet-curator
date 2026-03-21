import logging
import threading

from lib.backfill import backfill_pnl_history
from lib.changelog import detect_changes
from lib.db import ensure_resolution_entries, get_backend_name, get_connection, init_db, rebuild_positions
from lib.pnl import compute_wallet_pnl, record_pnl_history
from lib.pricing import fetch_prices
from lib.resolver import check_resolutions
from lib.time_utils import now_utc, to_db_timestamp

logger = logging.getLogger(__name__)

PIPELINE_LOCK = threading.Lock()


def _create_pipeline_log(conn, trigger):
    if conn.backend == "postgres":
        row = conn.execute(
            "INSERT INTO pipeline_log (trigger) VALUES (?) RETURNING id",
            (trigger,),
        ).fetchone()
        conn.commit()
        return row[0]

    cursor = conn.execute("INSERT INTO pipeline_log (trigger) VALUES (?)", (trigger,))
    conn.commit()
    return cursor.lastrowid


def _finish_pipeline_log(conn, log_id, stats, error=None):
    conn.execute(
        """
        UPDATE pipeline_log
        SET completed_at = ?, positions_rebuilt = ?, tokens_resolved = ?,
            pnl_computed = ?, history_recorded = ?, error = ?
        WHERE id = ?
        """,
        (
            to_db_timestamp(now_utc()),
            stats.get("positions_rebuilt"),
            stats.get("tokens_resolved"),
            stats.get("pnl_computed"),
            stats.get("history_recorded"),
            error,
            log_id,
        ),
    )
    conn.commit()


def run_hourly_pipeline(trigger="scheduled"):
    if not PIPELINE_LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "backend": get_backend_name(),
            "message": "Pipeline already running",
        }

    init_db()
    conn = get_connection()
    log_id = None
    stats = {
        "positions_rebuilt": 0,
        "tokens_resolved": 0,
        "pnl_computed": 0,
        "history_recorded": 0,
    }
    try:
        log_id = _create_pipeline_log(conn, trigger)
        excluded_counts = rebuild_positions(conn)
        stats["positions_rebuilt"] = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        ensure_resolution_entries(conn)
        stats["tokens_resolved"] = check_resolutions(conn)
        current_prices = fetch_prices(conn)
        stats["pnl_computed"] = compute_wallet_pnl(
            conn,
            excluded_counts=excluded_counts,
            current_prices=current_prices,
        )
        stats["history_recorded"] += backfill_pnl_history(conn)
        stats["history_recorded"] += record_pnl_history(conn)
        detect_changes(conn)
        _finish_pipeline_log(conn, log_id, stats)
        return {"status": "ok", "backend": conn.backend, **stats}
    except Exception as exc:
        logger.exception("Hourly pipeline failed")
        try:
            conn.rollback()
        except Exception:
            pass
        if log_id is not None:
            _finish_pipeline_log(conn, log_id, stats, error=str(exc))
        return {"status": "error", "backend": conn.backend, "error": str(exc), **stats}
    finally:
        conn.close()
        PIPELINE_LOCK.release()
