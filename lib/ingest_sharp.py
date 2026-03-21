import csv
import io
import logging
from datetime import datetime

import pandas as pd

from lib.db import init_db, get_connection, rebuild_positions, ensure_resolution_entries
from lib.file_manager import (
    scan_sharp_logs, rename_processed, create_archive, save_malformed
)
from lib.normalizers import normalize_wallet, normalize_token_id, normalize_game

logger = logging.getLogger(__name__)


def _capture_malformed_rows(filepath):
    """Read file and identify structurally malformed rows.

    Returns (header_line, list_of_malformed_raw_lines).
    """
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        raw_lines = f.readlines()

    if not raw_lines:
        return '', []

    header_line = raw_lines[0]
    # Count expected columns from header
    reader = csv.reader(io.StringIO(header_line))
    header_fields = next(reader)
    expected_cols = len(header_fields)

    malformed = []
    for i, line in enumerate(raw_lines[1:], start=2):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        try:
            reader = csv.reader(io.StringIO(line_stripped))
            fields = next(reader)
            if len(fields) != expected_cols:
                malformed.append(line_stripped)
        except Exception:
            malformed.append(line_stripped)

    return header_line, malformed


def run():
    """Ingest all unprocessed Sharp log CSVs."""
    init_db()
    conn = get_connection()

    files = scan_sharp_logs()
    if not files:
        print("No unprocessed Sharp log files found in data/sharp_logs/")
        return

    total_new = 0
    total_dupes = 0
    total_malformed = 0
    now = datetime.now()

    for filepath in files:
        print(f"Processing {filepath.name}...")

        # Step 1: Capture structurally malformed rows
        header_line, struct_malformed = _capture_malformed_rows(filepath)

        # Step 2: Read with pandas
        try:
            df = pd.read_csv(
                filepath,
                dtype={'Token ID': str, 'Tx Hash': str, 'Master Wallet': str},
                on_bad_lines='skip'
            )
        except Exception as e:
            print(f"  ERROR reading {filepath.name}: {e}")
            continue

        # Step 3: Identify semantically malformed rows (parsed OK but bad Action)
        valid_actions = df['Action'].isin(['Buy', 'Sell'])
        sem_malformed_df = df[~valid_actions]
        sem_malformed_lines = []
        if len(sem_malformed_df) > 0:
            # Convert back to CSV lines for repair
            buf = io.StringIO()
            sem_malformed_df.to_csv(buf, index=False, header=False)
            sem_malformed_lines = [l for l in buf.getvalue().strip().split('\n') if l.strip()]

        # Combine all malformed
        all_malformed = struct_malformed + sem_malformed_lines
        if all_malformed:
            batch_num = conn.execute(
                "SELECT COALESCE(MAX(batch_number), 0) + 1 FROM ingest_registry"
            ).fetchone()[0]
            save_malformed(header_line, all_malformed, batch_num, now)
            total_malformed += len(all_malformed)
            print(f"  ⚠️ {len(all_malformed)} malformed rows saved to data/malformed/ for repair.")

        # Step 4: Filter to valid rows only
        df = df[valid_actions].copy()
        skipped = len(sem_malformed_df) + len(struct_malformed)
        if skipped > 0:
            logger.info("Skipped %d malformed/invalid rows from %s", skipped, filepath.name)

        if df.empty:
            print(f"  No valid trades in {filepath.name}")
            rename_processed(filepath, now)
            continue

        # Step 5: Normalize
        df['Master Wallet'] = df['Master Wallet'].apply(normalize_wallet)
        df['Token ID'] = df['Token ID'].apply(normalize_token_id)
        df['game'] = df['Market'].apply(lambda m: normalize_game(m, source='market'))

        # Check for scientific notation (e.g., 6.84e+76) — NOT hex 'e' in 0x... strings
        sci_tokens = df['Token ID'].str.match(r'^\d+\.?\d*e[+-]?\d+$', case=False, na=False)
        if sci_tokens.any():
            logger.warning(
                "⚠️ Found %d token IDs in scientific notation — dtype=str enforcement may have failed",
                sci_tokens.sum()
            )

        # Step 6: Dedup against database
        existing_hashes = set()
        cursor = conn.execute("SELECT tx_hash FROM trades")
        for row in cursor:
            existing_hashes.add(row[0])

        new_mask = ~df['Tx Hash'].isin(existing_hashes)
        new_df = df[new_mask]
        dupe_count = (~new_mask).sum()

        # Step 7: Get batch number for archive
        batch_num = conn.execute(
            "SELECT COALESCE(MAX(batch_number), 0) + 1 FROM ingest_registry"
        ).fetchone()[0]

        # Step 8: Create archive if new trades exist
        if len(new_df) > 0:
            create_archive(new_df, batch_num, now)
            archive_name = f"ingested_{batch_num:03d}_{now.strftime('%Y-%m-%d')}.csv"
        else:
            archive_name = 'SKIPPED'
            print(f"  No new trades found in {filepath.name} (all {len(df)} trades already in database).")

        # Step 9: Rename original
        rename_processed(filepath, now)

        # Step 10: Insert new trades
        if len(new_df) > 0:
            for _, row in new_df.iterrows():
                try:
                    conn.execute("""
                        INSERT INTO trades (tx_hash, timestamp, master_wallet, own_wallet,
                            action, market, outcome, token_id, price, shares, invested,
                            received, pnl_pct, pct_sold, reason, game, ingest_batch)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row['Tx Hash'],
                        row['Date'],
                        row['Master Wallet'],
                        row.get('Own Wallet', ''),
                        row['Action'],
                        row['Market'],
                        row['Outcome'],
                        row['Token ID'],
                        float(row['Price']),
                        float(row['Shares']),
                        float(row['Invested']),
                        float(row['Received']),
                        float(row['PNL %']) if pd.notna(row.get('PNL %')) else None,
                        float(row['% Sold']) if pd.notna(row.get('% Sold')) else None,
                        row.get('Reason', ''),
                        row['game'],
                        batch_num,
                    ))
                except Exception as e:
                    logger.warning("Failed to insert trade %s: %s", row.get('Tx Hash', '?'), e)

        # Step 11: Register ingest
        conn.execute("""
            INSERT INTO ingest_registry (original_filename, archive_filename, new_trades, duplicate_trades)
            VALUES (?, ?, ?, ?)
        """, (filepath.name, archive_name, len(new_df), dupe_count))
        conn.commit()

        total_new += len(new_df)
        total_dupes += dupe_count
        print(f"  Ingested {len(new_df)} new trades ({dupe_count} duplicates skipped).")

    # Step 12: Run 4-step pipeline
    if total_new > 0 or not conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]:
        print("\nRebuilding positions...")
        excluded_counts = rebuild_positions(conn)

        print("Ensuring resolution entries...")
        new_entries = ensure_resolution_entries(conn)
        if new_entries:
            print(f"  Created {new_entries} new resolution entries.")

        # Resolver and P&L are called from curator.py after this returns
        # to avoid circular imports — we return the excluded_counts
        conn.close()

        print(f"\nProcessed {len(files)} file(s). Ingested {total_new} new trades "
              f"({total_dupes} already existed).", end="")
        if total_malformed:
            print(f" {total_malformed} malformed rows saved for repair.", end="")
        print()
        return excluded_counts

    conn.close()
    print(f"\nProcessed {len(files)} file(s). Ingested {total_new} new trades "
          f"({total_dupes} already existed).", end="")
    if total_malformed:
        print(f" {total_malformed} malformed rows saved for repair.", end="")
    print()
    return {}
