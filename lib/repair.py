import csv
import io
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_SIZE = 5

CSV_HEADER = "Date,Master Wallet,Own Wallet,Action,Market,Outcome,Token ID,Price,Shares,Invested,Received,PNL %,% Sold,Reason,Tx Hash"
EXPECTED_COLS = 15


def _get_example_rows(conn, count=5):
    """Get example correctly-formatted trade rows matching the original CSV column order."""
    rows = conn.execute("""
        SELECT timestamp, master_wallet, own_wallet, action, market,
               outcome, token_id, price, shares, invested, received,
               pnl_pct, pct_sold, reason, tx_hash
        FROM trades
        WHERE action IN ('Buy', 'Sell')
        LIMIT ?
    """, (count,)).fetchall()

    lines = []
    for r in rows:
        vals = []
        for v in r:
            if v is None:
                vals.append('')
            else:
                vals.append(str(v))
        lines.append(','.join(vals))
    return lines


def _validate_repaired_row(row_str):
    """Validate a repaired CSV row. Returns True if valid."""
    try:
        reader = csv.reader(io.StringIO(row_str))
        fields = next(reader)
    except Exception:
        return False

    if len(fields) != EXPECTED_COLS:
        logger.debug("Rejected: %d cols (expected %d): %s", len(fields), EXPECTED_COLS, row_str[:80])
        return False

    # Field 3 = Action must be Buy or Sell
    if fields[3].strip() not in ('Buy', 'Sell'):
        logger.debug("Rejected: Action='%s': %s", fields[3], row_str[:80])
        return False

    # Date should look like a timestamp
    if not fields[0].strip() or '20' not in fields[0]:
        logger.debug("Rejected: bad date '%s': %s", fields[0][:20], row_str[:80])
        return False

    # Master Wallet should start with 0x
    if not fields[1].strip().startswith('0x'):
        logger.debug("Rejected: bad wallet '%s': %s", fields[1][:20], row_str[:80])
        return False

    # Token ID should start with 0x
    if not fields[6].strip().startswith('0x'):
        logger.debug("Rejected: bad token '%s': %s", fields[6][:20], row_str[:80])
        return False

    # Tx Hash (last field) should start with 0x
    if not fields[14].strip().startswith('0x'):
        logger.debug("Rejected: bad tx_hash '%s': %s", fields[14][:20], row_str[:80])
        return False

    return True


def run():
    """Repair malformed rows using Claude Sonnet API."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Export it: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    from lib.db import init_db, get_connection
    from lib.file_manager import scan_malformed

    init_db()
    conn = get_connection()

    files = scan_malformed()
    if not files:
        print("No malformed files found in data/malformed/")
        conn.close()
        return

    # Get example rows for context
    example_rows = _get_example_rows(conn)
    if not example_rows:
        print("No trades in database yet — cannot provide examples for repair.")
        conn.close()
        return

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    total_repaired = 0
    total_malformed = 0
    now = datetime.now()

    system_prompt = (
        "You are a CSV data recovery tool. You extract valid trade rows from "
        "corrupted/concatenated CSV data. Return ONLY valid CSV rows, one per line. "
        "No headers, no explanation, no markdown formatting, no blank lines."
    )

    for filepath in files:
        print(f"Repairing {filepath.name}...")

        with open(filepath, 'r') as f:
            lines = f.readlines()

        if len(lines) < 2:
            print(f"  Empty malformed file, skipping")
            continue

        malformed_lines = [l.strip() for l in lines[1:] if l.strip()]
        total_malformed += len(malformed_lines)

        # Output file
        repair_num = conn.execute(
            "SELECT COALESCE(MAX(batch_number), 0) + 1 FROM ingest_registry"
        ).fetchone()[0]
        output_path = os.path.join(
            BASE_DIR, 'data', 'sharp_logs',
            f"repaired_{repair_num:03d}_{now.strftime('%Y-%m-%d')}.csv"
        )

        # Write header to output file
        with open(output_path, 'w') as f:
            f.write(CSV_HEADER + '\n')

        file_repaired = 0

        # Process in batches
        for batch_start in range(0, len(malformed_lines), BATCH_SIZE):
            batch = malformed_lines[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(malformed_lines) + BATCH_SIZE - 1) // BATCH_SIZE

            prompt = f"""These are malformed CSV rows from a trade log. The corruption pattern is: multiple rows got concatenated together on a single line, with some field values merged without proper separators. Each malformed line may contain 2 or more original trades. Some lines are fragments/tails that broke off from the previous concatenated line.

CORRECT CSV FORMAT (15 columns):
{CSV_HEADER}

EXAMPLE CORRECT ROWS:
{chr(10).join(example_rows)}

KEY PATTERNS TO RECOGNIZE:
- Dates look like: 2026-MM-DDTHH:MM:SS.mmmZ
- Wallet addresses: 0x followed by 40 hex chars
- Token IDs: 0x followed by 64 hex chars
- Action is always "Buy" or "Sell"
- Tx Hash: 0x followed by 64 hex chars (always the LAST field)
- Price is between 0.0000 and 1.0000
- Reason field often contains "Copy Sell (X.X%)" or "Buy" or is empty

MALFORMED LINES TO REPAIR:
{chr(10).join(batch)}

Extract ALL valid trades you can identify from these lines. Return one corrected CSV row per line. Each row must have exactly 15 comma-separated fields matching the header above."""

            try:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = response.content[0].text.strip()
            except Exception as e:
                logger.warning("API call failed for batch: %s", e)
                print(f"  Batch {batch_num}/{total_batches} failed: {e}")
                continue

            # Validate and save repaired rows
            batch_repaired = 0
            batch_rejected = 0
            for line in result.split('\n'):
                line = line.strip()
                if not line:
                    continue
                if _validate_repaired_row(line):
                    with open(output_path, 'a') as f:
                        f.write(line + '\n')
                    file_repaired += 1
                    batch_repaired += 1
                else:
                    batch_rejected += 1

            print(f"  Batch {batch_num}/{total_batches}: "
                  f"+{batch_repaired} recovered"
                  + (f", {batch_rejected} rejected" if batch_rejected else "")
                  + f" ({file_repaired} total)")

        total_repaired += file_repaired

        if file_repaired == 0:
            os.remove(output_path)
            print(f"  No rows could be repaired from {filepath.name} — file left for retry")
        else:
            processed_name = f"processed_{filepath.name}"
            filepath.rename(filepath.parent / processed_name)
            print(f"  Repaired {file_repaired} rows from {len(malformed_lines)} malformed lines -> {os.path.basename(output_path)}")

    conn.close()
    print(f"\nRepaired {total_repaired} trades from {total_malformed} malformed lines across {len(files)} file(s).")
    if total_repaired > 0:
        print("Run `python curator.py ingest` to process the repaired rows.")
