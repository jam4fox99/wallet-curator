import csv
import logging
import os
from datetime import datetime

from lib.db import init_db, get_connection, ensure_resolution_entries
from lib.normalizers import normalize_wallet, normalize_game

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compute_wallet_pnl(conn, excluded_counts=None, current_prices=None):
    """Compute P&L for all wallets from positions + resolutions.

    Args:
        current_prices: dict of {token_id_hex: current_price} for live pricing.
                        If None, unrealized positions show cost basis only.
    """
    if excluded_counts is None:
        cursor = conn.execute("""
            SELECT master_wallet, COUNT(DISTINCT token_id) as excluded
            FROM trades
            WHERE (master_wallet, token_id) NOT IN (
                SELECT master_wallet, token_id FROM positions
            )
            GROUP BY master_wallet
        """)
        excluded_counts = {r['master_wallet']: r['excluded'] for r in cursor.fetchall()}

    if current_prices is None:
        current_prices = {}

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
        unrealized_value = 0.0
        unrealized_pnl_total = 0.0
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
                resolved_pnl_val = resolution_value - cost_of_remaining
                realized_pnl += resolved_pnl_val
                total_received_resolutions += resolution_value
            elif resolved == -1:  # Lost
                cost_of_remaining = avg_cost * net_shares
                realized_pnl -= cost_of_remaining
                total_lost_resolutions += cost_of_remaining
            elif resolved == 2:  # Voided — resolved at partial price (typically $0.50)
                res_price = pos['resolution_price'] if pos['resolution_price'] is not None else 0.5
                resolution_value = net_shares * res_price
                cost_of_remaining = avg_cost * net_shares
                resolved_pnl_val = resolution_value - cost_of_remaining
                realized_pnl += resolved_pnl_val
                total_received_resolutions += resolution_value
            else:  # Unresolved (0) or unresolvable (-2)
                cost_basis = avg_cost * net_shares
                unrealized_shares += net_shares
                unrealized_invested += cost_basis

                # Live pricing
                token_id = pos['token_id']
                if token_id in current_prices and current_prices[token_id] is not None:
                    cur_value = current_prices[token_id] * net_shares
                    unrealized_value += cur_value
                    unrealized_pnl_total += cur_value - cost_basis
                else:
                    # No live price — use cost basis as value estimate
                    unrealized_value += cost_basis

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
                unrealized_shares, unrealized_invested, unrealized_value, unrealized_pnl,
                unique_markets, unique_tokens,
                total_trades, incomplete_positions, first_trade, last_trade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            wallet, game, total_invested, total_received_sells,
            total_received_resolutions, total_lost_resolutions, realized_pnl,
            unrealized_shares, unrealized_invested, unrealized_value, unrealized_pnl_total,
            len(unique_markets), len(unique_tokens),
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

    # Fetch live prices for open positions
    current_prices = {}
    try:
        from lib.pricing import fetch_prices
        current_prices = fetch_prices(conn)
    except Exception as e:
        print(f"⚠️ Live pricing failed: {e}")

    print("Computing P&L...")
    compute_wallet_pnl(conn, current_prices=current_prices)

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
    resolved_count = res_map.get(1, 0) + res_map.get(-1, 0) + res_map.get(2, 0)
    voided = res_map.get(2, 0)
    unresolvable = res_map.get(-2, 0)

    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    now = datetime.now()
    header = f"Wallet P&L Dashboard — {now.strftime('%Y-%m-%d %H:%M')}"
    data_parts = [f"Data: {total_trades} total trades | {len(pnl_data)} wallets tracked | "
                  f"{resolved_count}/{total_tokens} tokens resolved"]
    if voided:
        data_parts.append(f"{voided} voided")
    if unresolvable:
        data_parts.append(f"{unresolvable} unresolvable")
    data_line = " | ".join(data_parts)

    # Format table
    rows = []
    total_inv = 0
    total_real = 0
    total_open_val = 0
    total_open_pnl = 0
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
        open_value = w['unrealized_value']
        open_pnl = w['unrealized_pnl']
        total_pnl = realized + open_pnl
        incomplete = w['incomplete_positions']

        if incomplete > 0:
            has_incomplete = True

        total_inv += invested
        total_real += realized
        total_open_val += open_value
        total_open_pnl += open_pnl

        rows.append({
            'wallet': wallet_short,
            'filter': filter_game,
            'actual': actual_game,
            'sim': sim_num,
            'invested': f"${invested:,.0f}",
            'realized': _format_dollar(realized),
            'open_value': f"${open_value:,.0f}" if open_value > 0 else "$0",
            'open_pnl': _format_dollar(open_pnl) if open_value > 0 else "—",
            'total_pnl': _format_dollar(total_pnl),
            'markets': str(w['unique_markets']),
            'trades': str(w['total_trades']),
            'in_csv': in_csv,
            'incomplete': incomplete,
        })

    # Build output
    lines = [header, data_line, ""]

    # Table header
    hdr = (f"{'Wallet':<22} | {'Filter':<8} | {'Actual':<6} | {'Sim #':<5} | "
           f"{'Invested':>10} | {'Realized':>10} | {'Open Val':>10} | {'Open P&L':>10} | "
           f"{'Total P&L':>10} | {'Mkts':>5} | {'Trades':>6} | {'CSV'}")
    sep = "-" * len(hdr)
    lines.append(hdr)
    lines.append(sep)

    for r in rows:
        line = (f"{r['wallet']:<22} | {r['filter']:<8} | {r['actual']:<6} | {r['sim']:<5} | "
                f"{r['invested']:>10} | {r['realized']:>10} | {r['open_value']:>10} | {r['open_pnl']:>10} | "
                f"{r['total_pnl']:>10} | {r['markets']:>5} | {r['trades']:>6} | {r['in_csv']}")
        lines.append(line)

    lines.append(sep)
    total_total = total_real + total_open_pnl
    lines.append(f"Totals: Invested ${total_inv:,.0f} | Realized {_format_dollar(total_real)} | "
                 f"Open Value ${total_open_val:,.0f} | Open P&L {_format_dollar(total_open_pnl)} | "
                 f"Total {_format_dollar(total_total)}")

    if has_incomplete:
        lines.append("")
        incomplete_wallets = [r for r in rows if r['incomplete'] > 0]
        for r in incomplete_wallets:
            lines.append(f"* {r['wallet']}: {r['incomplete']} positions excluded "
                         f"(missing buy data) — P&L may be understated")

    output = '\n'.join(lines)
    print(output)

    # Build CSV content
    import csv as csv_mod
    import io as csv_io

    csv_buf = csv_io.StringIO()
    writer = csv_mod.writer(csv_buf)
    writer.writerow(['Wallet', 'Filter', 'Actual', 'Sim', 'Invested',
                     'Realized', 'Open Value', 'Open P&L', 'Total P&L',
                     'Markets', 'Trades', 'In CSV', 'Excluded Positions'])
    for w in pnl_data:
        wallet = w['master_wallet']
        filter_game = active_wallets.get(wallet, '')
        sim_row = conn.execute(
            "SELECT MAX(sim_number) as sim FROM sim_snapshots WHERE wallet_address = ?",
            (wallet,)
        ).fetchone()
        sim_num = sim_row['sim'] if sim_row and sim_row['sim'] else ''
        in_csv_val = 'Yes' if wallet in active_wallets else 'No'
        open_pnl = w['unrealized_pnl']
        total_pnl = w['realized_pnl'] + open_pnl

        writer.writerow([
            wallet,
            filter_game,
            w['game'] or '',
            sim_num,
            round(w['total_invested'], 2),
            round(w['realized_pnl'], 2),
            round(w['unrealized_value'], 2),
            round(open_pnl, 2),
            round(total_pnl, 2),
            w['unique_markets'],
            w['total_trades'],
            in_csv_val,
            w['incomplete_positions'],
        ])

    new_csv = csv_buf.getvalue()

    # Check if data changed vs last saved
    reports_dir = os.path.join(BASE_DIR, 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    latest_path = os.path.join(reports_dir, 'pnl_latest.csv')

    changed = True
    if os.path.exists(latest_path):
        with open(latest_path, 'r') as f:
            old_csv = f.read()
        if old_csv == new_csv:
            changed = False

    if changed:
        # Save timestamped copy (military time)
        ts = now.strftime('%Y-%m-%d_%H%M')
        ts_path = os.path.join(reports_dir, f'pnl_{ts}.csv')
        with open(ts_path, 'w', newline='') as f:
            f.write(new_csv)
        # Also overwrite latest
        with open(latest_path, 'w', newline='') as f:
            f.write(new_csv)
        print(f"\nSaved to {ts_path}")
    else:
        print(f"\nNo changes since last run — skipped save.")

    conn.close()
