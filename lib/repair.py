import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tx hashes are 0x + 64 hex chars
TX_HASH_PATTERN = re.compile(r'0x[a-fA-F0-9]{64}')


def run():
    """Extract tx_hashes from malformed rows as a missing-trades checklist.

    These tx_hashes represent trades we know exist but couldn't parse.
    On the next CSV download from the VPS, these trades will be in the fresh
    export and get ingested normally through dedup.
    """
    from lib.db import init_db, get_connection
    from lib.file_manager import scan_malformed

    init_db()
    conn = get_connection()

    files = scan_malformed()
    if not files:
        print("No malformed files found in data/malformed/")
        conn.close()
        return

    # Get existing tx_hashes so we know which are truly missing
    existing = set()
    for row in conn.execute("SELECT tx_hash FROM trades"):
        existing.add(row[0])

    total_found = 0
    total_missing = 0
    all_missing = []

    for filepath in files:
        print(f"Scanning {filepath.name} for tx_hashes...")

        with open(filepath, 'r') as f:
            lines = f.readlines()

        # Skip header
        malformed_lines = [l.strip() for l in lines[1:] if l.strip()]

        found_hashes = set()
        for line in malformed_lines:
            matches = TX_HASH_PATTERN.findall(line)
            for h in matches:
                h = h.lower()
                found_hashes.add(h)

        # Filter to hashes NOT already in our database
        missing = found_hashes - existing
        already_have = found_hashes & existing

        total_found += len(found_hashes)
        total_missing += len(missing)
        all_missing.extend(sorted(missing))

        print(f"  Found {len(found_hashes)} tx_hashes ({len(already_have)} already ingested, {len(missing)} missing)")

        # Rename to processed
        processed_name = f"processed_{filepath.name}"
        filepath.rename(filepath.parent / processed_name)

    # Save missing tx_hashes checklist
    if all_missing:
        reports_dir = os.path.join(BASE_DIR, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        checklist_path = os.path.join(reports_dir, 'missing_trades.txt')
        with open(checklist_path, 'w') as f:
            f.write(f"# Missing Trade Tx Hashes — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"# {len(all_missing)} trades found in malformed rows but not in database\n")
            f.write(f"# These will be picked up automatically on next CSV download from VPS\n\n")
            for h in all_missing:
                f.write(h + '\n')
        print(f"\n{len(all_missing)} missing tx_hashes saved to {checklist_path}")
        print("These trades will be ingested automatically on your next CSV download from the VPS.")
    else:
        print(f"\nAll {total_found} tx_hashes from malformed rows are already in the database.")
        print("No missing trades.")

    conn.close()
