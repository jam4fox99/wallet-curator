#!/usr/bin/env python3
"""Wallet Curator CLI entry point."""
import argparse
import logging
import os
import sys


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def cmd_ingest(_args):
    from lib.ingest_sharp import run as ingest_run
    from lib.pipeline import run_hourly_pipeline

    ingest_run()
    result = run_hourly_pipeline(trigger="cli-ingest")
    if result["status"] == "error":
        print(f"Pipeline failed after ingest: {result['error']}")


def cmd_pnl(_args):
    from lib.pnl import run as pnl_run

    pnl_run()


def cmd_status(_args):
    from lib.db import DB_PATH, get_backend_name, get_connection, init_db
    from lib.time_utils import parse_db_timestamp

    init_db()
    conn = get_connection()
    try:
        backend = get_backend_name()
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        positions = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        wallets = conn.execute("SELECT COUNT(*) FROM wallet_pnl").fetchone()[0]
        hidden = conn.execute("SELECT COUNT(*) FROM hidden_wallets").fetchone()[0]
        resolutions = conn.execute(
            "SELECT resolved, COUNT(*) AS count FROM resolutions GROUP BY resolved"
        ).fetchall()
        resolution_map = {row["resolved"]: row["count"] for row in resolutions}
        latest_pipeline = conn.execute(
            "SELECT * FROM pipeline_log ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        sync_status = conn.execute("SELECT * FROM sync_status WHERE id = 1").fetchone()

        print("Wallet Curator Status")
        print("---------------------")
        if backend == "postgres":
            print("Backend: Postgres (DATABASE_URL)")
        else:
            print(f"Backend: SQLite ({DB_PATH})")

        print(f"Trades: {trades:,}")
        print(f"Positions: {positions:,}")
        print(f"Wallets with P&L: {wallets:,}")
        print(
            "Tokens tracked: "
            f"{sum(resolution_map.values()):,} "
            f"({resolution_map.get(1, 0) + resolution_map.get(-1, 0) + resolution_map.get(2, 0)} resolved, "
            f"{resolution_map.get(0, 0)} pending, {resolution_map.get(-2, 0)} unresolvable)"
        )
        print(f"Hidden wallets: {hidden}")

        if sync_status:
            last_sync = parse_db_timestamp(sync_status["last_sync_at"]).strftime("%Y-%m-%d %H:%M UTC")
            print(
                f"Sync status: folder={sync_status['current_version_folder'] or 'unknown'} | "
                f"last sync={last_sync} | last cycle={sync_status['trades_synced_this_cycle']} trades"
            )
            if sync_status["last_error"]:
                print(f"Last sync error: {sync_status['last_error']}")

        if latest_pipeline:
            started_at = parse_db_timestamp(latest_pipeline["started_at"]).strftime("%Y-%m-%d %H:%M UTC")
            print(
                f"Last pipeline: {started_at} | positions={latest_pipeline['positions_rebuilt'] or 0} | "
                f"resolved={latest_pipeline['tokens_resolved'] or 0} | history rows={latest_pipeline['history_recorded'] or 0}"
            )
            if latest_pipeline["error"]:
                print(f"Pipeline error: {latest_pipeline['error']}")
    finally:
        conn.close()


def cmd_run(args):
    from lib.file_manager import scan_sharp_logs
    from lib.pipeline import run_hourly_pipeline

    logs = scan_sharp_logs()
    if logs:
        cmd_ingest(args)
    else:
        result = run_hourly_pipeline(trigger="cli-run")
        if result["status"] == "error":
            print(f"Pipeline failed: {result['error']}")
    cmd_status(args)


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Wallet Curator CLI - cloud-ready Polymarket P&L tooling"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("run", help="Run local ingest if files exist, then refresh pipeline")
    subparsers.add_parser("ingest", help="Ingest local Sharp CSVs, then refresh pipeline")
    subparsers.add_parser("pnl", help="Refresh pipeline and print wallet P&L")
    subparsers.add_parser("status", help="Show database, sync, and pipeline status")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "run": cmd_run,
        "ingest": cmd_ingest,
        "pnl": cmd_pnl,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
