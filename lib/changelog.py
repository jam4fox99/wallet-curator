import csv
import logging
import os
from datetime import datetime

from lib.normalizers import normalize_wallet, normalize_game

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_active_wallets():
    """Read active_wallets.csv, return dict of {wallet: game_filter}."""
    csv_path = os.path.join(BASE_DIR, 'active_wallets.csv')
    wallets = {}
    try:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = normalize_wallet(row['address'])
                if addr == '__global__':
                    continue
                game = normalize_game(row.get('market_whitelist', ''), source='whitelist')
                wallets[addr] = game
    except FileNotFoundError:
        logger.warning("active_wallets.csv not found")
    return wallets


def _generate_retirement_summary(conn, wallet):
    """Generate a factual retirement summary from SQLite data."""
    pnl = conn.execute(
        "SELECT * FROM wallet_pnl WHERE master_wallet = ?", (wallet,)
    ).fetchone()

    if not pnl:
        return f"RETIRED {wallet[:10]}... — no P&L data available"

    # Duration
    first = pnl['first_trade']
    last = pnl['last_trade']
    if first and last:
        try:
            f_dt = datetime.fromisoformat(first.replace('Z', '+00:00'))
            l_dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
            days = (l_dt - f_dt).days
            duration = f"{days} days"
        except Exception:
            duration = "unknown duration"
    else:
        duration = "unknown duration"

    # Latest sim
    sim_row = conn.execute(
        "SELECT MAX(sim_number) as sim FROM sim_snapshots WHERE wallet_address = ?",
        (wallet,)
    ).fetchone()
    sim_str = f"Sharp Sim #{sim_row['sim']}" if sim_row and sim_row['sim'] else "no sim data"

    return (
        f"RETIRED {wallet[:10]}... ({pnl['game'] or 'UNKNOWN'}) — "
        f"active for {duration}. "
        f"Performance: {'+' if pnl['realized_pnl'] >= 0 else ''}"
        f"${pnl['realized_pnl']:,.0f} realized P&L, "
        f"{pnl['unique_markets']} unique markets, "
        f"{pnl['total_trades']} trades. {sim_str}."
    )


def detect_changes(conn):
    """Diff active_wallets.csv against last_known_wallets snapshot."""
    current = _read_active_wallets()
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

    # Get last snapshot
    last_known = {}
    rows = conn.execute("SELECT wallet_address, game_filter FROM last_known_wallets").fetchall()
    for r in rows:
        last_known[r['wallet_address']] = r['game_filter']

    is_first_run = len(last_known) == 0

    # Compute diff
    added = {w: g for w, g in current.items() if w not in last_known}
    removed = {w: g for w, g in last_known.items() if w not in current}

    if not added and not removed:
        # Update snapshot anyway (game filter might have changed)
        conn.execute("DELETE FROM last_known_wallets")
        for wallet, game in current.items():
            conn.execute(
                "INSERT INTO last_known_wallets (wallet_address, game_filter) VALUES (?, ?)",
                (wallet, game)
            )
        conn.commit()
        return {'added': {}, 'removed': {}}

    changes = []

    # Log changes
    for wallet, game in added.items():
        conn.execute("""
            INSERT INTO wallet_changes (wallet_address, action, game_filter)
            VALUES (?, 'ADDED', ?)
        """, (wallet, game))
        changes.append(f"- ✅ ADDED {wallet[:10]}...{wallet[-4:]} ({game})")

    for wallet, game in removed.items():
        summary = _generate_retirement_summary(conn, wallet)
        conn.execute("""
            INSERT INTO wallet_changes (wallet_address, action, game_filter, retirement_summary)
            VALUES (?, 'REMOVED', ?, ?)
        """, (wallet, game, summary))
        changes.append(f"- 🔴 REMOVED {wallet[:10]}...{wallet[-4:]} ({game})")
        logger.info(summary)

    # Update snapshot
    conn.execute("DELETE FROM last_known_wallets")
    for wallet, game in current.items():
        conn.execute(
            "INSERT INTO last_known_wallets (wallet_address, game_filter) VALUES (?, ?)",
            (wallet, game)
        )
    conn.commit()

    # Append to changelog markdown
    if changes:
        note = "[initial setup — {} wallets]".format(len(current)) if is_first_run else ""
        changelog_path = os.path.join(BASE_DIR, 'reports', 'wallet_changelog.md')
        os.makedirs(os.path.dirname(changelog_path), exist_ok=True)

        # Create file with header if it doesn't exist
        if not os.path.exists(changelog_path):
            with open(changelog_path, 'w') as f:
                f.write("# Wallet Change Log\n\n")

        with open(changelog_path, 'a') as f:
            f.write(f"## {now_str}\n")
            for change in changes:
                f.write(change + "\n")
            if note:
                f.write(note + "\n")
            f.write("\n")

        print(f"  Changelog: {len(added)} added, {len(removed)} removed"
              + (" [initial setup]" if is_first_run else ""))

    return {'added': added, 'removed': removed}
