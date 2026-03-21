import csv
import logging
import os
from datetime import datetime

import pandas as pd

from lib.normalizers import normalize_wallet, normalize_game

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_THRESHOLD = 200


def maybe_create_snapshot(conn, new_trade_count=0):
    """After P&L computation, decide whether to create/update a snapshot."""
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    last_snap = conn.execute(
        "SELECT * FROM pnl_snapshots ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchone()

    # Determine trade date range for this period
    if last_snap:
        date_range = conn.execute("""
            SELECT MIN(timestamp) as dt_from, MAX(timestamp) as dt_to
            FROM trades WHERE ingested_at > ?
        """, (last_snap['created_at'],)).fetchone()
    else:
        date_range = conn.execute(
            "SELECT MIN(timestamp) as dt_from, MAX(timestamp) as dt_to FROM trades"
        ).fetchone()

    dt_from = date_range['dt_from'] if date_range else None
    dt_to = date_range['dt_to'] if date_range else None

    # Format date range for description
    def fmt_date(dt_str):
        if not dt_str:
            return "?"
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00')).strftime('%b %d')
        except Exception:
            return dt_str[:10]

    desc = f"{fmt_date(dt_from)} – {fmt_date(dt_to)} — {total_trades:,} trades"

    if last_snap is None:
        # First run — always create
        snap_id = _create_snapshot(conn, total_trades, total_trades, dt_from, dt_to, desc)
        logger.info("Created first snapshot #%d", snap_id)
        return snap_id

    trades_since = total_trades - last_snap['trade_count']
    if trades_since < 0:
        trades_since = 0

    if trades_since >= SNAPSHOT_THRESHOLD or new_trade_count >= SNAPSHOT_THRESHOLD:
        # Create new snapshot
        snap_id = _create_snapshot(conn, total_trades, trades_since, dt_from, dt_to, desc)
        logger.info("Created snapshot #%d (%d new trades)", snap_id, trades_since)
        return snap_id
    else:
        # Update existing snapshot
        _update_snapshot(conn, last_snap['snapshot_id'], total_trades, dt_from, dt_to, desc)
        logger.info("Updated snapshot #%d", last_snap['snapshot_id'])
        return last_snap['snapshot_id']


def _create_snapshot(conn, trade_count, new_trades, dt_from, dt_to, desc):
    cursor = conn.execute("""
        INSERT INTO pnl_snapshots (trade_count, new_trades_since_last, trades_date_from, trades_date_to, description)
        VALUES (?, ?, ?, ?, ?)
    """, (trade_count, new_trades, dt_from, dt_to, desc))
    snap_id = cursor.lastrowid
    _save_snapshot_data(conn, snap_id)
    conn.commit()
    return snap_id


def _update_snapshot(conn, snap_id, trade_count, dt_from, dt_to, desc):
    conn.execute("DELETE FROM pnl_snapshot_data WHERE snapshot_id = ?", (snap_id,))
    conn.execute("""
        UPDATE pnl_snapshots SET trade_count = ?, trades_date_from = ?,
               trades_date_to = ?, description = ?, created_at = datetime('now')
        WHERE snapshot_id = ?
    """, (trade_count, dt_from, dt_to, desc, snap_id))
    _save_snapshot_data(conn, snap_id)
    conn.commit()


def _save_snapshot_data(conn, snap_id):
    """Copy current wallet_pnl + metadata into snapshot data."""
    # Read active_wallets.csv for filter game
    active_wallets = _read_active_wallets()

    wallets = conn.execute("SELECT * FROM wallet_pnl").fetchall()
    for w in wallets:
        wallet = w['master_wallet']
        filter_game = active_wallets.get(wallet, '')
        in_csv = 1 if wallet in active_wallets else 0

        sim_row = conn.execute(
            "SELECT MAX(sim_number) as sim FROM sim_snapshots WHERE wallet_address = ?",
            (wallet,)
        ).fetchone()
        sim_num = sim_row['sim'] if sim_row and sim_row['sim'] else None

        conn.execute("""
            INSERT INTO pnl_snapshot_data (snapshot_id, master_wallet, game, filter_game,
                total_invested, realized_pnl, unrealized_value, unrealized_pnl, total_pnl,
                unique_markets, total_trades, in_csv, incomplete_positions, sim_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snap_id, wallet, w['game'], filter_game,
            w['total_invested'], w['realized_pnl'], w['unrealized_value'], w['unrealized_pnl'],
            w['realized_pnl'] + w['unrealized_pnl'],
            w['unique_markets'], w['total_trades'], in_csv, w['incomplete_positions'], sim_num,
        ))


def _read_active_wallets():
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
        pass
    return wallets


def get_all_snapshots(conn):
    """Return list of all snapshots for the dropdown."""
    return conn.execute(
        "SELECT * FROM pnl_snapshots ORDER BY snapshot_id DESC"
    ).fetchall()


def get_snapshot_data(conn, snapshot_id):
    """Get raw cumulative data for a snapshot as a dict keyed by wallet."""
    rows = conn.execute(
        "SELECT * FROM pnl_snapshot_data WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchall()
    return {r['master_wallet']: dict(r) for r in rows}


def get_snapshot_delta(conn, snapshot_id):
    """Compute delta (period performance) for a snapshot.

    Delta = current cumulative - previous cumulative.
    First snapshot: delta = cumulative.
    """
    current = get_snapshot_data(conn, snapshot_id)

    prev = conn.execute(
        "SELECT snapshot_id FROM pnl_snapshots WHERE snapshot_id < ? ORDER BY snapshot_id DESC LIMIT 1",
        (snapshot_id,)
    ).fetchone()

    if prev is None:
        return current  # first snapshot — delta IS cumulative

    previous = get_snapshot_data(conn, prev['snapshot_id'])

    delta = {}
    numeric_fields = ['total_invested', 'realized_pnl', 'unrealized_value', 'unrealized_pnl', 'total_pnl',
                       'unique_markets', 'total_trades']

    for wallet, cur_data in current.items():
        d = dict(cur_data)
        if wallet in previous:
            prev_data = previous[wallet]
            for field in numeric_fields:
                d[field] = (cur_data.get(field) or 0) - (prev_data.get(field) or 0)
        delta[wallet] = d

    return delta


def get_combined_dataframe(conn, snapshot_ids, include_hidden=False):
    """Build DataFrame for display with per-snapshot delta columns + combined totals."""
    if not snapshot_ids:
        return pd.DataFrame()

    # Get hidden wallets
    hidden = set()
    for r in conn.execute("SELECT wallet_address FROM hidden_wallets"):
        hidden.add(r['wallet_address'])

    # Get snapshot info
    snapshots = []
    for sid in sorted(snapshot_ids):
        snap = conn.execute("SELECT * FROM pnl_snapshots WHERE snapshot_id = ?", (sid,)).fetchone()
        if snap:
            snapshots.append(dict(snap))

    if not snapshots:
        return pd.DataFrame()

    # Single snapshot — return simple view
    if len(snapshots) == 1:
        snap = snapshots[0]
        delta = get_snapshot_delta(conn, snap['snapshot_id'])
        rows = []
        for wallet, data in delta.items():
            is_hidden = wallet in hidden
            if is_hidden and not include_hidden:
                continue
            rows.append({
                'hide': 'Yes' if is_hidden else 'No',
                'wallet': wallet,
                'snaps': f"🟢#{snap['snapshot_id']}",
                'filter': data.get('filter_game') or '—',
                'actual': data.get('game') or '—',
                'sim': f"#{data['sim_number']}" if data.get('sim_number') else '—',
                'invested': round(data.get('total_invested') or 0, 2),
                'realized': round(data.get('realized_pnl') or 0, 2),
                'open_val': round(data.get('unrealized_value') or 0, 2),
                'open_pnl': round(data.get('unrealized_pnl') or 0, 2),
                'total_pnl': round(data.get('total_pnl') or 0, 2),
                'markets': int(data.get('unique_markets') or 0),
                'trades': int(data.get('total_trades') or 0),
                'in_csv': 'Yes' if data.get('in_csv') else 'No',
                'excluded': int(data.get('incomplete_positions') or 0),
            })
        return pd.DataFrame(rows)

    # Multiple snapshots — per-snapshot delta columns + combined
    all_wallets = set()
    deltas = {}
    for snap in snapshots:
        sid = snap['snapshot_id']
        delta = get_snapshot_delta(conn, sid)
        deltas[sid] = delta
        all_wallets.update(delta.keys())

    if not include_hidden:
        all_wallets -= hidden

    rows = []
    for wallet in sorted(all_wallets):
        is_hidden = wallet in hidden
        row = {
            'hide': 'Yes' if is_hidden else 'No',
            'wallet': wallet,
        }

        # Snapshot presence indicators
        snap_indicators = []
        for snap in snapshots:
            sid = snap['snapshot_id']
            if wallet in deltas[sid]:
                snap_indicators.append(f"🟢#{sid}")
            else:
                snap_indicators.append(f"⚫#{sid}")
        row['snaps'] = ' '.join(snap_indicators)

        # Get latest data for static fields
        latest_data = None
        for snap in reversed(snapshots):
            if wallet in deltas[snap['snapshot_id']]:
                latest_data = deltas[snap['snapshot_id']][wallet]
                break

        row['filter'] = (latest_data.get('filter_game') or '—') if latest_data else '—'
        row['actual'] = (latest_data.get('game') or '—') if latest_data else '—'
        row['sim'] = f"#{latest_data['sim_number']}" if latest_data and latest_data.get('sim_number') else '—'
        row['in_csv'] = 'Yes' if latest_data and latest_data.get('in_csv') else 'No'

        # Per-snapshot columns
        combined_total = 0
        for snap in snapshots:
            sid = snap['snapshot_id']
            prefix = f"s{sid}"
            if wallet in deltas[sid]:
                d = deltas[sid][wallet]
                inv = round(d.get('total_invested') or 0, 2)
                real = round(d.get('realized_pnl') or 0, 2)
                total = round(d.get('total_pnl') or 0, 2)
                row[f'{prefix}_inv'] = inv
                row[f'{prefix}_real'] = real
                row[f'{prefix}_total'] = total
                combined_total += total
            else:
                row[f'{prefix}_inv'] = None
                row[f'{prefix}_real'] = None
                row[f'{prefix}_total'] = None

        row['combined'] = round(combined_total, 2)
        row['markets'] = int(latest_data.get('unique_markets', 0)) if latest_data else 0
        row['trades'] = int(latest_data.get('total_trades', 0)) if latest_data else 0
        row['excluded'] = int(latest_data.get('incomplete_positions', 0)) if latest_data else 0

        rows.append(row)

    return pd.DataFrame(rows)


def save_csv_if_changed(conn):
    """Save active_wallets.csv to history if it changed."""
    csv_path = os.path.join(BASE_DIR, 'active_wallets.csv')
    try:
        with open(csv_path) as f:
            content = f.read()
    except FileNotFoundError:
        return

    # Count wallets
    lines = [l for l in content.strip().split('\n') if l.strip() and '__global__' not in l]
    wallet_count = len(lines) - 1  # minus header

    # Check if changed
    last = conn.execute("SELECT csv_content FROM csv_history ORDER BY id DESC LIMIT 1").fetchone()
    if last and last['csv_content'] == content:
        return  # no change

    # Build changes summary
    summary = None
    if last:
        old_wallets = set()
        new_wallets = set()
        for line in last['csv_content'].strip().split('\n')[1:]:
            if '__global__' not in line and line.strip():
                addr = line.split(',')[0].strip().lower()
                old_wallets.add(addr)
        for line in content.strip().split('\n')[1:]:
            if '__global__' not in line and line.strip():
                addr = line.split(',')[0].strip().lower()
                new_wallets.add(addr)
        added = len(new_wallets - old_wallets)
        removed = len(old_wallets - new_wallets)
        parts = []
        if added:
            parts.append(f"added {added}")
        if removed:
            parts.append(f"removed {removed}")
        summary = ', '.join(parts) if parts else 'no wallet changes'
    else:
        summary = 'initial setup'

    conn.execute("""
        INSERT INTO csv_history (wallet_count, csv_content, changes_summary)
        VALUES (?, ?, ?)
    """, (wallet_count, content, summary))
    conn.commit()
