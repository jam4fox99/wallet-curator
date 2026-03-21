import csv
import logging
import os
from datetime import datetime

from lib.db import init_db, get_connection, ensure_resolution_entries
from lib.normalizers import normalize_wallet, normalize_game

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compute_wallet_pnl(conn, excluded_counts=None):
    """Compute P&L for all wallets from positions + resolutions."""
    if excluded_counts is None:
        # Compute excluded counts dynamically
        cursor = conn.execute("""
            SELECT master_wallet, COUNT(DISTINCT token_id) as excluded
            FROM trades
            WHERE (master_wallet, token_id) NOT IN (
                SELECT master_wallet, token_id FROM positions
            )
            GROUP BY master_wallet
        """)
        excluded_counts = {r['master_wallet']: r['excluded'] for r in cursor.fetchall()}

    conn.execute("DELETE FROM wallet_pnl")

    wallets = conn.execute("SELECT DISTINCT master_wallet FROM positions").fetchall()

    for wallet_row in wallets:
        wallet = wallet_row['master_wallet']

        positions = conn.execute("""
            SELECT p.*, r.resolved, r.resolution_price
            FROM positions p
            LEFT JOIN resolutions r ON p.token_id = r.token_id
            WHERE p.master_wallet = ?
        """, (wallet,)).fetchall()

        total_invested = 0.0
        total_received_sells = 0.0
        total_received_resolutions = 0.0
        total_lost_resolutions = 0.0
        realized_pnl = 0.0
        unrealized_shares = 0.0
        unrealized_invested = 0.0
        unique_markets = set()
        unique_tokens = set()

        for pos in positions:
            shares_bought = pos['total_shares_bought']
            invested = pos['total_invested']
            shares_sold = pos['total_shares_sold']
            received = pos['total_received']
            net_shares = pos['net_shares']
            avg_cost = pos['avg_cost_basis']
            resolved = pos['resolved'] if pos['resolved'] is not None else 0

            total_invested += invested
            total_received_sells += received
            unique_markets.add(pos['market'])
            unique_tokens.add(pos['token_id'])

            if avg_cost is None or avg_cost == 0:
                continue

            # Sell P&L
            sell_pnl = received - (avg_cost * shares_sold)
            realized_pnl += sell_pnl

            if resolved == 1:  # Won
                resolution_value = net_shares * 1.0
                cost_of_remaining = avg_cost * net_shares
                resolved_pnl = resolution_value - cost_of_remaining
                realized_pnl += resolved_pnl
                total_received_resolutions += resolution_value
            elif resolved == -1:  # Lost
                cost_of_remaining = avg_cost * net_shares
                realized_pnl -= cost_of_remaining
                total_lost_resolutions += cost_of_remaining
            else:  # Unresolved or unresolvable
                unrealized_shares += net_shares
                unrealized_invested += avg_cost * net_shares

        # Get trade stats
        stats = conn.execute("""
            SELECT COUNT(*) as trade_count, MIN(timestamp) as first_trade,
                   MAX(timestamp) as last_trade
            FROM trades WHERE master_wallet = ?
        """, (wallet,)).fetchone()

        # Most traded game
        game_row = conn.execute("""
            SELECT game, COUNT(*) as cnt FROM trades
            WHERE master_wallet = ? AND game IS NOT NULL
            GROUP BY game ORDER BY cnt DESC LIMIT 1
        """, (wallet,)).fetchone()
        game = game_row['game'] if game_row else None

        conn.execute("""
            INSERT INTO wallet_pnl (master_wallet, game, total_invested, total_received_sells,
                total_received_resolutions, total_lost_resolutions, realized_pnl,
                unrealized_shares, unrealized_invested, unique_markets, unique_tokens,
                total_trades, incomplete_positions, first_trade, last_trade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            wallet, game, total_invested, total_received_sells,
            total_received_resolutions, total_lost_resolutions, realized_pnl,
            unrealized_shares, unrealized_invested, len(unique_markets), len(unique_tokens),
            stats['trade_count'], excluded_counts.get(wallet, 0),
            stats['first_trade'], stats['last_trade'],
        ))

    conn.commit()
    logger.info("Computed P&L for %d wallets", len(wallets))


def _format_dollar(val):
    if val >= 0:
        return f"+${val:,.0f}"
    return f"-${abs(val):,.0f}"


def run():
    """Display P&L dashboard."""
    init_db()

    if not os.path.exists(os.path.join(BASE_DIR, 'data', 'curator.db')):
        print("No database found. Run `python curator.py ingest` first.")
        return

    conn = get_connection()

    # Check if positions exist
    pos_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    if pos_count == 0:
        print("No positions found. Run `python curator.py ingest` first.")
        conn.close()
        return

    # Run resolution pipeline (steps 2-4, positions NOT rebuilt)
    print("Updating resolutions...")
    ensure_resolution_entries(conn)

    try:
        from lib.resolver import check_resolutions
        newly_resolved = check_resolutions(conn)
        if newly_resolved:
            print(f"  {newly_resolved} tokens newly resolved.")
    except Exception as e:
        print(f"⚠️ Resolution check failed: {e}")

    print("Computing P&L...")
    compute_wallet_pnl(conn)

    # Read active_wallets.csv
    csv_path = os.path.join(BASE_DIR, 'active_wallets.csv')
    active_wallets = {}
    try:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = normalize_wallet(row['address'])
                if addr == '__global__':
                    continue
                game = normalize_game(row.get('market_whitelist', ''), source='whitelist')
                active_wallets[addr] = game
    except Exception:
        pass

    # Get data
    pnl_data = conn.execute("SELECT * FROM wallet_pnl ORDER BY realized_pnl DESC").fetchall()

    # Resolution stats
    res_stats = conn.execute("""
        SELECT resolved, COUNT(*) as cnt FROM resolutions GROUP BY resolved
    """).fetchall()
    res_map = {r['resolved']: r['cnt'] for r in res_stats}
    total_tokens = sum(res_map.values())
    resolved_count = res_map.get(1, 0) + res_map.get(-1, 0)
    unresolvable = res_map.get(-2, 0)

    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    now = datetime.now()
    header = f"Wallet P&L Dashboard — {now.strftime('%Y-%m-%d %H:%M')}"
    data_line = (f"Data: {total_trades} total trades | {len(pnl_data)} wallets tracked | "
                 f"{resolved_count}/{total_tokens} tokens resolved | {unresolvable} unresolvable")

    # Format table
    rows = []
    total_inv = 0
    total_real = 0
    total_unreal = 0
    has_incomplete = False

    for w in pnl_data:
        wallet = w['master_wallet']
        wallet_short = wallet[:8] + "..." + wallet[-4:]
        filter_game = active_wallets.get(wallet, '—')
        actual_game = w['game'] or '—'

        # Latest sim number
        sim_row = conn.execute("""
            SELECT MAX(sim_number) as sim FROM sim_snapshots WHERE wallet_address = ?
        """, (wallet,)).fetchone()
        sim_num = f"#{sim_row['sim']}" if sim_row and sim_row['sim'] else '—'

        in_csv = '✅' if wallet in active_wallets else '❌'

        invested = w['total_invested']
        realized = w['realized_pnl']
        unrealized = w['unrealized_invested']
        incomplete = w['incomplete_positions']

        if incomplete > 0:
            has_incomplete = True

        total_inv += invested
        total_real += realized
        total_unreal += unrealized

        rows.append({
            'wallet': wallet_short,
            'filter': filter_game,
            'actual': actual_game,
            'sim': sim_num,
            'invested': f"${invested:,.0f}",
            'realized': _format_dollar(realized),
            'unrealized': f"${unrealized:,.0f} open" if unrealized > 0 else "$0",
            'total_pnl': _format_dollar(realized),
            'markets': str(w['unique_markets']),
            'trades': str(w['total_trades']),
            'in_csv': in_csv,
            'incomplete': incomplete,
        })

    # Build output
    lines = [header, data_line, ""]

    # Table header
    hdr = (f"{'Wallet':<22} | {'Filter':<8} | {'Actual':<6} | {'Sim #':<5} | "
           f"{'Invested':>10} | {'Realized':>10} | {'Unrealized':>12} | "
           f"{'Total P&L':>10} | {'Markets':>7} | {'Trades':>6} | {'In CSV'}")
    sep = "-" * len(hdr)
    lines.append(hdr)
    lines.append(sep)

    for r in rows:
        line = (f"{r['wallet']:<22} | {r['filter']:<8} | {r['actual']:<6} | {r['sim']:<5} | "
                f"{r['invested']:>10} | {r['realized']:>10} | {r['unrealized']:>12} | "
                f"{r['total_pnl']:>10} | {r['markets']:>7} | {r['trades']:>6} | {r['in_csv']}")
        lines.append(line)

    lines.append(sep)
    lines.append(f"Totals: Invested ${total_inv:,.0f} | Realized {_format_dollar(total_real)} | "
                 f"Unrealized ${total_unreal:,.0f} open")

    if has_incomplete:
        lines.append("")
        incomplete_wallets = [r for r in rows if r['incomplete'] > 0]
        for r in incomplete_wallets:
            lines.append(f"* {r['wallet']}: {r['incomplete']} positions excluded "
                         f"(missing buy data) — P&L may be understated")

    output = '\n'.join(lines)
    print(output)

    # Save to file
    reports_dir = os.path.join(BASE_DIR, 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(
        reports_dir, f"pnl_{now.strftime('%Y-%m-%d_%H%M%S')}.md"
    )
    with open(report_path, 'w') as f:
        f.write(output + '\n')
    print(f"\nSaved to {report_path}")

    conn.close()
