import csv
import io
import logging
import os
from datetime import datetime

from lib.db import get_connection
from lib.normalizers import normalize_game, normalize_wallet
from lib.time_utils import now_utc, to_db_timestamp

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_active_wallet_filters(conn):
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
        pass
    return wallets


def _compute_excluded_counts(conn):
    rows = conn.execute(
        """
        SELECT master_wallet, COUNT(*) AS excluded_count
        FROM (
            SELECT master_wallet, token_id
            FROM trades
            GROUP BY master_wallet, token_id
            HAVING SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) <= 0
               OR (SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
                 - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END)) < 0
        ) excluded
        GROUP BY master_wallet
        """
    ).fetchall()
    return {row["master_wallet"]: row["excluded_count"] for row in rows}


def compute_wallet_pnl(conn, excluded_counts=None, current_prices=None):
    """Compute current wallet P&L from positions, resolutions, and live prices."""
    if excluded_counts is None:
        excluded_counts = _compute_excluded_counts(conn)
    if current_prices is None:
        current_prices = {}

    conn.execute("DELETE FROM wallet_pnl")
    wallets = conn.execute(
        "SELECT DISTINCT master_wallet FROM positions ORDER BY master_wallet"
    ).fetchall()

    for wallet_row in wallets:
        wallet = wallet_row["master_wallet"]
        positions = conn.execute(
            """
            SELECT p.*, r.resolved, r.resolution_price, r.last_price
            FROM positions p
            LEFT JOIN resolutions r ON p.token_id = r.token_id
            WHERE p.master_wallet = ?
            ORDER BY p.token_id
            """,
            (wallet,),
        ).fetchall()

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
            shares_bought = float(pos["total_shares_bought"] or 0)
            invested = float(pos["total_invested"] or 0)
            shares_sold = float(pos["total_shares_sold"] or 0)
            received = float(pos["total_received"] or 0)
            net_shares = float(pos["net_shares"] or 0)
            avg_cost = pos["avg_cost_basis"]
            resolved = pos["resolved"] if pos["resolved"] is not None else 0

            if shares_bought <= 0 or net_shares < 0:
                logger.warning("Skipping invalid position %s / %s", wallet[:10], pos["token_id"][:18])
                continue

            total_invested += invested
            total_received_sells += received
            unique_markets.add(pos["market"])
            unique_tokens.add(pos["token_id"])

            avg_cost = float(avg_cost or 0)
            sell_pnl = received - (avg_cost * shares_sold)
            realized_pnl += sell_pnl

            if resolved == 1:
                resolution_value = net_shares * 1.0
                total_received_resolutions += resolution_value
                realized_pnl += resolution_value - (avg_cost * net_shares)
            elif resolved == -1:
                cost_of_remaining = avg_cost * net_shares
                total_lost_resolutions += cost_of_remaining
                realized_pnl -= cost_of_remaining
            elif resolved == 2:
                resolution_price = float(pos["resolution_price"] or 0.5)
                resolution_value = net_shares * resolution_price
                total_received_resolutions += resolution_value
                realized_pnl += resolution_value - (avg_cost * net_shares)
            else:
                cost_basis = avg_cost * net_shares
                price = current_prices.get(pos["token_id"])
                if price is None and pos["last_price"] is not None:
                    price = float(pos["last_price"])
                unrealized_shares += net_shares
                unrealized_invested += cost_basis
                if price is not None:
                    mark_value = float(price) * net_shares
                    unrealized_value += mark_value
                    unrealized_pnl_total += mark_value - cost_basis
                else:
                    unrealized_value += cost_basis

        stats = conn.execute(
            """
            SELECT COUNT(*) AS trade_count, MIN(timestamp) AS first_trade, MAX(timestamp) AS last_trade
            FROM trades
            WHERE master_wallet = ?
            """,
            (wallet,),
        ).fetchone()

        game_row = conn.execute(
            """
            SELECT game, COUNT(*) AS cnt
            FROM trades
            WHERE master_wallet = ? AND game IS NOT NULL
            GROUP BY game
            ORDER BY cnt DESC, game ASC
            LIMIT 1
            """,
            (wallet,),
        ).fetchone()

        total_pnl = realized_pnl + unrealized_pnl_total
        conn.execute(
            """
            INSERT INTO wallet_pnl (
                master_wallet, game, total_invested, total_received_sells,
                total_received_resolutions, total_lost_resolutions, realized_pnl,
                unrealized_shares, unrealized_invested, unrealized_value, unrealized_pnl,
                total_pnl, unique_markets, unique_tokens, total_trades,
                excluded_positions, first_trade, last_trade, last_computed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wallet,
                game_row["game"] if game_row else None,
                total_invested,
                total_received_sells,
                total_received_resolutions,
                total_lost_resolutions,
                realized_pnl,
                unrealized_shares,
                unrealized_invested,
                unrealized_value,
                unrealized_pnl_total,
                total_pnl,
                len(unique_markets),
                len(unique_tokens),
                stats["trade_count"] if stats else 0,
                excluded_counts.get(wallet, 0),
                stats["first_trade"] if stats else None,
                stats["last_trade"] if stats else None,
                to_db_timestamp(now_utc()),
            ),
        )

    conn.commit()
    logger.info("Computed P&L for %d wallets", len(wallets))
    return len(wallets)


def record_pnl_history(conn, recorded_at=None):
    recorded_at = to_db_timestamp(recorded_at or now_utc())
    rows = conn.execute("SELECT * FROM wallet_pnl ORDER BY master_wallet").fetchall()
    if not rows:
        return 0

    inserted = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO pnl_history (
                recorded_at, master_wallet, realized_pnl, unrealized_pnl,
                total_pnl, total_invested, total_trades, unique_markets
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                row["master_wallet"],
                row["realized_pnl"],
                row["unrealized_pnl"],
                row["total_pnl"],
                row["total_invested"],
                row["total_trades"],
                row["unique_markets"],
            ),
        )
        inserted += 1

    aggregate = conn.execute(
        """
        SELECT
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(SUM(unrealized_pnl), 0) AS unrealized_pnl,
            COALESCE(SUM(total_pnl), 0) AS total_pnl,
            COALESCE(SUM(total_invested), 0) AS total_invested,
            COALESCE(SUM(total_trades), 0) AS total_trades,
            COALESCE(SUM(unique_markets), 0) AS unique_markets
        FROM wallet_pnl
        """
    ).fetchone()
    conn.execute(
        """
        INSERT INTO pnl_history (
            recorded_at, master_wallet, realized_pnl, unrealized_pnl,
            total_pnl, total_invested, total_trades, unique_markets
        )
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            recorded_at,
            aggregate["realized_pnl"],
            aggregate["unrealized_pnl"],
            aggregate["total_pnl"],
            aggregate["total_invested"],
            aggregate["total_trades"],
            aggregate["unique_markets"],
        ),
    )
    conn.commit()
    return inserted + 1


def _format_dollar(value):
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.0f}"


def _save_csv_report(conn):
    active_wallets = load_active_wallet_filters(conn)
    pnl_data = conn.execute("SELECT * FROM wallet_pnl ORDER BY total_pnl DESC").fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Wallet",
            "Filter",
            "Actual",
            "Sim",
            "Invested",
            "Realized",
            "Open Value",
            "Open P&L",
            "Total P&L",
            "Markets",
            "Trades",
            "In CSV",
            "Excluded Positions",
        ]
    )

    for wallet in pnl_data:
        sim_row = conn.execute(
            "SELECT MAX(sim_number) AS sim FROM sim_snapshots WHERE wallet_address = ?",
            (wallet["master_wallet"],),
        ).fetchone()
        writer.writerow(
            [
                wallet["master_wallet"],
                active_wallets.get(wallet["master_wallet"], ""),
                wallet["game"] or "",
                sim_row["sim"] if sim_row and sim_row["sim"] else "",
                round(wallet["total_invested"], 2),
                round(wallet["realized_pnl"], 2),
                round(wallet["unrealized_value"], 2),
                round(wallet["unrealized_pnl"], 2),
                round(wallet["total_pnl"], 2),
                wallet["unique_markets"],
                wallet["total_trades"],
                "Yes" if wallet["master_wallet"] in active_wallets else "No",
                wallet["excluded_positions"],
            ]
        )

    new_csv = buffer.getvalue()
    reports_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    latest_path = os.path.join(reports_dir, "pnl_latest.csv")
    changed = True
    if os.path.exists(latest_path):
        with open(latest_path, "r") as handle:
            changed = handle.read() != new_csv

    if changed:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        report_path = os.path.join(reports_dir, f"pnl_{timestamp}.csv")
        with open(report_path, "w", newline="") as handle:
            handle.write(new_csv)
        with open(latest_path, "w", newline="") as handle:
            handle.write(new_csv)
        print(f"\nSaved to {report_path}")
    else:
        print("\nNo changes since last run - skipped save.")


def run():
    """CLI P&L entrypoint: refresh data via pipeline and print a simple table."""
    from lib.pipeline import run_hourly_pipeline

    result = run_hourly_pipeline(trigger="cli-pnl")
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM wallet_pnl ORDER BY total_pnl DESC").fetchall()
        if not rows:
            print("No wallet P&L data found. Run `python curator.py ingest` first.")
            return

        active_wallets = load_active_wallet_filters(conn)
        total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        print(
            f"Wallet P&L Dashboard - backend={result['backend']} - {total_trades} trades - "
            f"{len(rows)} wallets"
        )
        print(
            f"{'Wallet':<18} {'Filter':<8} {'Actual':<8} {'Invested':>10} "
            f"{'Realized':>10} {'Open':>10} {'Total':>10} {'Excl':>5}"
        )
        print("-" * 86)
        for row in rows:
            wallet = row["master_wallet"]
            short_wallet = f"{wallet[:8]}...{wallet[-4:]}"
            print(
                f"{short_wallet:<18} {active_wallets.get(wallet, '-'):<8} {(row['game'] or '-'): <8} "
                f"${row['total_invested']:>9,.0f} {_format_dollar(row['realized_pnl']):>10} "
                f"{_format_dollar(row['unrealized_pnl']):>10} {_format_dollar(row['total_pnl']):>10} "
                f"{row['excluded_positions']:>5}"
            )
        _save_csv_report(conn)
    finally:
        conn.close()
