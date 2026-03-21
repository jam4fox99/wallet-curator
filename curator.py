#!/usr/bin/env python3
"""Wallet Curator Agent — CLI entry point."""
import argparse
import logging
import sys


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )


def cmd_ingest(args):
    from lib.ingest_sharp import run as ingest_run
    from lib.db import get_connection

    excluded_counts = ingest_run()

    # Run resolver + P&L after ingest
    conn = get_connection()
    try:
        from lib.resolver import check_resolutions
        print("Checking resolutions...")
        newly_resolved = check_resolutions(conn)
        if newly_resolved:
            print(f"  {newly_resolved} tokens newly resolved.")
    except Exception as e:
        print(f"⚠️ Resolution check failed: {e}")
        newly_resolved = 0

    # Fetch live prices for open positions
    current_prices = {}
    try:
        from lib.pricing import fetch_prices
        current_prices = fetch_prices(conn)
    except Exception as e:
        print(f"⚠️ Live pricing failed: {e}")

    try:
        from lib.pnl import compute_wallet_pnl
        print("Computing P&L...")
        compute_wallet_pnl(conn, excluded_counts or {}, current_prices=current_prices)
    except Exception as e:
        print(f"⚠️ P&L computation failed: {e}")

    # Run changelog
    try:
        from lib.changelog import detect_changes
        detect_changes(conn)
    except Exception as e:
        print(f"⚠️ Changelog detection failed: {e}")

    conn.close()


def cmd_ingest_sim(args):
    from lib.ingest_sim import run as sim_run
    sim_run()


def cmd_pnl(args):
    from lib.pnl import run as pnl_run
    pnl_run()


def cmd_status(args):
    import os
    from lib.db import DB_PATH, init_db, get_connection
    from lib.file_manager import scan_sharp_logs, scan_sims, scan_malformed

    init_db()

    print("Wallet Curator Status")
    print("─────────────────────")

    # Database
    db_exists = os.path.exists(DB_PATH)
    print(f"Database: {DB_PATH} ({'exists' if db_exists else 'not found'})")

    if not db_exists:
        print("Run `python curator.py ingest` to get started.")
        return

    conn = get_connection()

    # Active wallets
    try:
        import csv
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'active_wallets.csv')
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            wallets = [r for r in reader if r['address'] != '__global__']
        print(f"Active wallets: {len(wallets)} in active_wallets.csv")
    except Exception:
        print("Active wallets: active_wallets.csv not found")

    # Sharp logs
    ingests = conn.execute("SELECT COUNT(*), COALESCE(SUM(new_trades), 0) FROM ingest_registry").fetchone()
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    print(f"Sharp logs ingested: {ingests[0]} files, {total_trades} total trades")

    # Sims
    sims = conn.execute("SELECT sim_number, wallet_count FROM sim_registry ORDER BY sim_number").fetchall()
    if sims:
        sim_parts = [f"Sim #{s['sim_number']}: {s['wallet_count']} wallets" for s in sims]
        print(f"Sharp sims ingested: {len(sims)} files ({', '.join(sim_parts)})")
    else:
        print("Sharp sims ingested: 0 files")

    # Tokens
    res = conn.execute("""
        SELECT resolved, COUNT(*) FROM resolutions GROUP BY resolved
    """).fetchall()
    res_map = {r['resolved']: r[1] for r in res}
    total_tokens = sum(res_map.values())
    resolved = res_map.get(1, 0) + res_map.get(-1, 0)
    pending = res_map.get(0, 0)
    unresolvable = res_map.get(-2, 0)
    print(f"Tokens tracked: {total_tokens} ({resolved} resolved, {pending} pending, {unresolvable} unresolvable)")

    # Positions
    valid_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    all_pos_groups = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT master_wallet, token_id FROM trades)"
    ).fetchone()[0]
    excluded = all_pos_groups - valid_pos
    print(f"Positions: {valid_pos} valid, {excluded} excluded (missing buy data)")

    # Timestamps
    last_ingest = conn.execute(
        "SELECT MAX(ingested_at) FROM ingest_registry"
    ).fetchone()[0]
    if last_ingest:
        print(f"Last ingest: {last_ingest}")

    last_eval = conn.execute(
        "SELECT MAX(eval_date) FROM evaluation_log"
    ).fetchone()[0]
    if last_eval:
        print(f"Last evaluation: {last_eval}")
        # Since last eval
        new_ingests = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(new_trades), 0) FROM ingest_registry WHERE ingested_at > ?",
            (last_eval,)
        ).fetchone()
        new_sims = conn.execute(
            "SELECT COUNT(*) FROM sim_registry WHERE ingested_at > ?", (last_eval,)
        ).fetchone()[0]
        new_changes = conn.execute(
            "SELECT COUNT(*) FROM wallet_changes WHERE change_date > ?", (last_eval,)
        ).fetchone()[0]
        print(f"Since last eval: {new_ingests[0]} new ingest(s) ({new_ingests[1]} trades), "
              f"{new_sims} new sim(s), {new_changes} wallet change(s)")
    else:
        print("Last evaluation: No evaluations yet")

    # Pending files
    pending_logs = scan_sharp_logs()
    pending_sims = scan_sims()
    pending_malformed = scan_malformed()
    print(f"Pending in data/sharp_logs/: {len(pending_logs)} unprocessed file(s)")
    print(f"Pending in data/sims/: {len(pending_sims)} unprocessed file(s)")
    if pending_malformed:
        total_malformed_rows = 0
        for f in pending_malformed:
            with open(f) as mf:
                total_malformed_rows += max(0, sum(1 for _ in mf) - 1)  # subtract header
        print(f"Malformed rows awaiting repair: {total_malformed_rows} (in data/malformed/)")

    # Mem0
    try:
        import mem0  # noqa: F401
        print("Mem0: configured")
    except ImportError:
        print("Mem0: not configured")

    conn.close()


def cmd_evaluate(args):
    from lib.evaluator import run as eval_run
    eval_run()


def cmd_repair(args):
    from lib.repair import run as repair_run
    repair_run()


def cmd_run(args):
    """Run the full pipeline: ingest → ingest-sim → pnl."""
    from lib.file_manager import scan_sharp_logs, scan_sims

    # Only run ingest steps if there are unprocessed files
    logs = scan_sharp_logs()
    if logs:
        print(f"=== Ingest ({len(logs)} file(s)) ===")
        cmd_ingest(args)
        print()
    else:
        # Still need to resolve + price even if no new files
        from lib.db import init_db, get_connection, ensure_resolution_entries
        init_db()
        conn = get_connection()
        pos_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        if pos_count == 0:
            print("No data yet. Drop CSV files into data/sharp_logs/ first.")
            conn.close()
            return
        conn.close()

    sims = scan_sims()
    if sims:
        print(f"=== Ingest Sim ({len(sims)} file(s)) ===")
        cmd_ingest_sim(args)
        print()

    print("=== P&L Dashboard ===")
    cmd_pnl(args)


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Wallet Curator Agent — Polymarket esports copy-trading tool'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    subparsers.add_parser('run', help='Run full pipeline: ingest + ingest-sim + pnl')
    subparsers.add_parser('ingest', help='Ingest Sharp log CSVs from data/sharp_logs/')
    subparsers.add_parser('ingest-sim', help='Ingest Sharp sim xlsx from data/sims/')
    subparsers.add_parser('pnl', help='Display P&L dashboard')
    subparsers.add_parser('status', help='Show system status')
    subparsers.add_parser('evaluate', help='Run wallet evaluation (requires API keys)')
    subparsers.add_parser('repair', help='Repair malformed rows (requires API key)')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        'run': cmd_run,
        'ingest': cmd_ingest,
        'ingest-sim': cmd_ingest_sim,
        'pnl': cmd_pnl,
        'status': cmd_status,
        'evaluate': cmd_evaluate,
        'repair': cmd_repair,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
