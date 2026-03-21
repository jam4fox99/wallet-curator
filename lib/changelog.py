import csv
import logging
import os
from datetime import datetime

from lib.normalizers import normalize_game, normalize_wallet
from lib.time_utils import parse_db_timestamp, to_db_timestamp

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_current_wallets(conn):
    rows = conn.execute(
        "SELECT wallet_address, game_filter FROM synced_active_wallets ORDER BY wallet_address"
    ).fetchall()
    if rows:
        return {row["wallet_address"]: row["game_filter"] for row in rows}

    csv_path = os.path.join(BASE_DIR, "active_wallets.csv")
    wallets = {}
    try:
        with open(csv_path) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                wallet = normalize_wallet(row["address"])
                if wallet == "__global__":
                    continue
                wallets[wallet] = normalize_game(row.get("market_whitelist", ""), source="whitelist")
    except FileNotFoundError:
        logger.warning("No synced_active_wallets rows and no local active_wallets.csv found")
    return wallets


def _generate_retirement_summary(conn, wallet):
    pnl = conn.execute(
        "SELECT * FROM wallet_pnl WHERE master_wallet = ?",
        (wallet,),
    ).fetchone()
    if not pnl:
        return f"RETIRED {wallet[:10]}... - no P&L data available"

    duration = "unknown duration"
    if pnl["first_trade"] and pnl["last_trade"]:
        try:
            first_trade = parse_db_timestamp(pnl["first_trade"])
            last_trade = parse_db_timestamp(pnl["last_trade"])
            duration = f"{(last_trade - first_trade).days} days"
        except ValueError:
            pass

    sim_row = conn.execute(
        "SELECT MAX(sim_number) AS sim FROM sim_snapshots WHERE wallet_address = ?",
        (wallet,),
    ).fetchone()
    sim_label = f"Sharp Sim #{sim_row['sim']}" if sim_row and sim_row["sim"] else "no sim data"

    return (
        f"RETIRED {wallet[:10]}... ({pnl['game'] or 'UNKNOWN'}) - active for {duration}. "
        f"Performance: {'+' if pnl['total_pnl'] >= 0 else ''}${pnl['total_pnl']:,.0f} total P&L, "
        f"{pnl['unique_markets']} unique markets, {pnl['total_trades']} trades. {sim_label}."
    )


def detect_changes(conn):
    current = _read_current_wallets(conn)
    last_known_rows = conn.execute(
        "SELECT wallet_address, game_filter FROM last_known_wallets"
    ).fetchall()
    last_known = {row["wallet_address"]: row["game_filter"] for row in last_known_rows}

    added = {wallet: game for wallet, game in current.items() if wallet not in last_known}
    removed = {wallet: game for wallet, game in last_known.items() if wallet not in current}

    if not added and not removed and current == last_known:
        return {"added": {}, "removed": {}}

    now = to_db_timestamp(datetime.utcnow())
    for wallet, game in added.items():
        conn.execute(
            """
            INSERT INTO wallet_changes (change_date, wallet_address, action, game_filter, trigger)
            VALUES (?, ?, 'ADDED', ?, 'sync')
            """,
            (now, wallet, game),
        )

    for wallet, game in removed.items():
        summary = _generate_retirement_summary(conn, wallet)
        conn.execute(
            """
            INSERT INTO wallet_changes (change_date, wallet_address, action, game_filter, trigger, retirement_summary)
            VALUES (?, ?, 'REMOVED', ?, 'sync', ?)
            """,
            (now, wallet, game, summary),
        )
        logger.info(summary)

    conn.execute("DELETE FROM last_known_wallets")
    for wallet, game in current.items():
        conn.execute(
            "INSERT INTO last_known_wallets (wallet_address, game_filter, snapshot_date) VALUES (?, ?, ?)",
            (wallet, game, now),
        )
    conn.commit()
    return {"added": added, "removed": removed}


def get_recent_changes(conn, limit=10):
    return conn.execute(
        "SELECT * FROM wallet_changes ORDER BY change_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
