#!/usr/bin/env python3
"""Standalone VPS trade sync for Wallet Curator.

This script only needs outbound access to Railway Postgres. No inbound ports are required.
Dependencies: stdlib + psycopg2-binary
"""

import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

UTC = timezone.utc
SCRIPT_DIR = Path(__file__).resolve().parent
LAST_SYNC_PATH = SCRIPT_DIR / "last_sync.txt"
TIMESTAMP_LOGGED = False


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def getenv(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_wallet(addr):
    return str(addr).strip().lower()


def normalize_token_id(token_id):
    token_id = str(token_id).strip()
    if token_id.startswith("0x") or token_id.startswith("0X"):
        return token_id.lower()
    try:
        return hex(int(token_id)).lower()
    except ValueError:
        pass
    try:
        value = int(float(token_id))
        print(f"WARNING: token ID in scientific notation: {token_id} -- precision loss likely")
        return hex(value).lower()
    except (ValueError, OverflowError):
        pass
    print(f"ERROR: unrecognizable token ID format: {token_id}")
    return token_id.lower()


def normalize_game(market_name):
    market_name = str(market_name or "")
    if market_name.startswith("Counter-Strike:"):
        return "CS2"
    if market_name.startswith("LoL:"):
        return "LOL"
    if market_name.startswith("Dota 2:"):
        return "DOTA"
    if market_name.startswith("Valorant:"):
        return "VALO"
    return "UNKNOWN"


def parse_game_from_whitelist(whitelist):
    if not whitelist or not str(whitelist).strip():
        return "UNKNOWN"
    whitelist = str(whitelist).lower()
    games = set()
    if any(pattern in whitelist for pattern in ["cs2", "csgo", "counter-strike"]):
        games.add("CS2")
    if "lol" in whitelist:
        games.add("LOL")
    if any(pattern in whitelist for pattern in ["dota2", "dota-2", "dota"]):
        games.add("DOTA")
    if any(pattern in whitelist for pattern in ["val", "valorant"]):
        games.add("VALO")
    if not games:
        return "UNKNOWN"
    if len(games) == 1:
        return games.pop()
    return "ESPORTS"


def parse_float_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_timestamp(value):
    global TIMESTAMP_LOGGED

    text = str(value).strip()
    if not text:
        raise ValueError("Empty timestamp")

    if not TIMESTAMP_LOGGED:
        if any(marker in text for marker in ["Z", "+", "-"]) and " " not in text[:10]:
            print(f"Timestamp format detected with timezone info: {text}")
        else:
            print(f"Timestamp format detected without timezone info, assuming UTC: {text}")
        TIMESTAMP_LOGGED = True

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp format: {value}")


def version_key(name):
    parts = name.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def find_current_sharp_folder(downloads_dir, csv_subpath, csv_filename):
    candidates = []
    for child in Path(downloads_dir).iterdir():
        if child.is_dir():
            key = version_key(child.name)
            if key is not None:
                candidates.append((key, child))
    if not candidates:
        raise RuntimeError(f"No Sharp version folders found in {downloads_dir}")

    candidates.sort(reverse=True)
    preferred = candidates[0][1]
    for _, folder in candidates:
        csv_path = folder / csv_subpath / csv_filename
        if csv_path.exists():
            if folder != preferred:
                print(
                    f"WARNING: highest version folder {preferred.name} is missing the trade CSV; "
                    f"falling back to {folder.name}"
                )
            return folder
    raise RuntimeError("No Sharp version folder contains the trade CSV")


def read_csv_safely(filepath):
    raw_text = filepath.read_text(encoding="utf-8", errors="replace")
    lines = raw_text.splitlines()
    if not lines:
        return []

    header_fields = next(csv.reader([lines[0]]))
    expected_columns = len(header_fields)
    valid_lines = [lines[0]]

    for index, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            fields = next(csv.reader([line]))
        except Exception:
            fields = []
        if len(fields) != expected_columns:
            if index == len(lines):
                print(f"WARNING: dropping partial last CSV row from {filepath.name}")
            else:
                print(f"WARNING: skipping malformed CSV row {index} in {filepath.name}")
            continue
        valid_lines.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(valid_lines)))
    return list(reader)


def read_valid_csv_text(filepath):
    raw_text = filepath.read_text(encoding="utf-8", errors="replace")
    lines = raw_text.splitlines()
    if not lines:
        return "", []

    header_fields = next(csv.reader([lines[0]]))
    expected_columns = len(header_fields)
    valid_lines = [lines[0]]

    for index, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            fields = next(csv.reader([line]))
        except Exception:
            fields = []
        if len(fields) != expected_columns:
            if index == len(lines):
                print(f"WARNING: dropping partial last CSV row from {filepath.name}")
            else:
                print(f"WARNING: skipping malformed CSV row {index} in {filepath.name}")
            continue
        valid_lines.append(line)

    csv_text = "\n".join(valid_lines)
    if csv_text and not csv_text.endswith("\n"):
        csv_text += "\n"
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    return csv_text, rows


def load_last_synced_hash():
    if LAST_SYNC_PATH.exists():
        return LAST_SYNC_PATH.read_text(encoding="utf-8").strip() or None
    return None


def save_last_synced_hash(tx_hash):
    LAST_SYNC_PATH.write_text(str(tx_hash).strip(), encoding="utf-8")


def find_trades_after_hash(valid_trades, last_synced_tx_hash):
    if not last_synced_tx_hash:
        return valid_trades
    for index in range(len(valid_trades) - 1, -1, -1):
        if valid_trades[index]["tx_hash"] == last_synced_tx_hash:
            return valid_trades[index + 1:]
    print("WARNING: last synced tx_hash not found in CSV; rescanning full file and relying on Postgres dedup")
    return valid_trades


def connect_db(database_url):
    return psycopg2.connect(database_url)


def ensure_tables(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                tx_hash TEXT UNIQUE NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                master_wallet TEXT NOT NULL,
                own_wallet TEXT NOT NULL,
                action TEXT NOT NULL,
                market TEXT NOT NULL,
                outcome TEXT NOT NULL,
                token_id TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                shares DOUBLE PRECISION NOT NULL,
                invested DOUBLE PRECISION NOT NULL,
                received DOUBLE PRECISION NOT NULL,
                pnl_pct DOUBLE PRECISION,
                pct_sold DOUBLE PRECISION,
                reason TEXT,
                game TEXT,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_active_wallets (
                wallet_address TEXT PRIMARY KEY,
                market_whitelist TEXT,
                game_filter TEXT,
                raw_csv_line TEXT,
                row_order INTEGER,
                copy_percentage DOUBLE PRECISION,
                copy_percentage_enabled INTEGER NOT NULL DEFAULT 0,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_csv_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                header_row TEXT NOT NULL DEFAULT '',
                global_row TEXT NOT NULL DEFAULT '',
                csv_content TEXT NOT NULL DEFAULT '',
                synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source_path TEXT,
                CHECK (id = 1)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS csv_push_history (
                id SERIAL PRIMARY KEY,
                pushed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                applied_at TIMESTAMPTZ,
                change_count INTEGER NOT NULL,
                summary TEXT NOT NULL,
                old_csv TEXT NOT NULL,
                new_csv TEXT NOT NULL,
                changes JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                old_wallet_tiers JSONB,
                new_wallet_tiers JSONB,
                old_tier_config JSONB,
                new_tier_config JSONB,
                reverts_push_id INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_status (
                id INTEGER PRIMARY KEY DEFAULT 1,
                last_sync_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                current_version_folder TEXT NOT NULL DEFAULT '',
                trades_synced_this_cycle INTEGER NOT NULL DEFAULT 0,
                total_trades_synced INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                CHECK (id = 1)
            )
            """
        )
        cursor.execute("ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS raw_csv_line TEXT")
        cursor.execute("ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS row_order INTEGER")
        cursor.execute("ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS copy_percentage DOUBLE PRECISION")
        cursor.execute(
            "ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS copy_percentage_enabled INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()


def push_trades(conn, trades):
    if not trades:
        return 0

    values = [
        (
            trade["tx_hash"],
            trade["timestamp"],
            trade["master_wallet"],
            trade["own_wallet"],
            trade["action"],
            trade["market"],
            trade["outcome"],
            trade["token_id"],
            trade["price"],
            trade["shares"],
            trade["invested"],
            trade["received"],
            trade["pnl_pct"],
            trade["pct_sold"],
            trade["reason"],
            trade["game"],
        )
        for trade in trades
    ]

    sql = """
        INSERT INTO trades (
            tx_hash, timestamp, master_wallet, own_wallet, action, market, outcome,
            token_id, price, shares, invested, received, pnl_pct, pct_sold, reason, game
        ) VALUES %s
        ON CONFLICT (tx_hash) DO NOTHING
        RETURNING tx_hash
    """
    with conn.cursor() as cursor:
        inserted = execute_values(cursor, sql, values, page_size=500, fetch=True)
    conn.commit()
    return len(inserted or [])


def sync_active_wallets(conn, wallets_path):
    csv_text, rows = read_valid_csv_text(wallets_path)
    if not rows:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO synced_csv_state (id, header_row, global_row, csv_content, synced_at, source_path)
                VALUES (1, '', '', '', NOW(), %s)
                ON CONFLICT (id) DO UPDATE
                SET header_row = EXCLUDED.header_row,
                    global_row = EXCLUDED.global_row,
                    csv_content = EXCLUDED.csv_content,
                    synced_at = EXCLUDED.synced_at,
                    source_path = EXCLUDED.source_path
                """,
                (str(wallets_path),),
            )
        conn.commit()
        return 0

    raw_lines = csv_text.splitlines()
    header_row = raw_lines[0] if raw_lines else ""
    global_row = raw_lines[1] if len(raw_lines) > 1 else ""
    wallets = []
    row_order = 0
    for index, row in enumerate(rows, start=1):
        address = normalize_wallet(row.get("address", ""))
        if not address or address == "__global__":
            continue
        whitelist = (row.get("market_whitelist") or "").strip()
        raw_line = raw_lines[index] if index < len(raw_lines) else ""
        copy_percentage = parse_float_or_none(row.get("copy_percentage"))
        copy_percentage_enabled = 1 if str(row.get("copy_percentage_enabled", "")).strip().lower() == "true" else 0
        wallets.append(
            (
                address,
                whitelist,
                parse_game_from_whitelist(whitelist),
                raw_line,
                row_order,
                copy_percentage,
                copy_percentage_enabled,
            )
        )
        row_order += 1

    with conn.cursor() as cursor:
        cursor.execute("TRUNCATE TABLE synced_active_wallets")
        if wallets:
            execute_values(
                cursor,
                """
                INSERT INTO synced_active_wallets (
                    wallet_address, market_whitelist, game_filter, raw_csv_line,
                    row_order, copy_percentage, copy_percentage_enabled
                )
                VALUES %s
                """,
                wallets,
                page_size=250,
            )
        cursor.execute(
            """
            INSERT INTO synced_csv_state (id, header_row, global_row, csv_content, synced_at, source_path)
            VALUES (1, %s, %s, %s, NOW(), %s)
            ON CONFLICT (id) DO UPDATE
            SET header_row = EXCLUDED.header_row,
                global_row = EXCLUDED.global_row,
                csv_content = EXCLUDED.csv_content,
                synced_at = EXCLUDED.synced_at,
                source_path = EXCLUDED.source_path
            """,
            (header_row, global_row, csv_text, str(wallets_path)),
        )
    conn.commit()
    return len(wallets)


def check_pending_push(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, new_csv, reverts_push_id
            FROM csv_push_history
            WHERE status = 'pending'
            ORDER BY pushed_at ASC, id ASC
            LIMIT 1
            """
        )
        return cursor.fetchone()


def apply_csv_changes(conn, push, wallets_path):
    push_id = push["id"]
    temp_path = wallets_path.with_suffix(wallets_path.suffix + ".tmp")
    print(f"Applying pending CSV push #{push_id} to {wallets_path}")
    temp_path.write_text(push["new_csv"], encoding="utf-8")
    temp_path.replace(wallets_path)

    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE csv_push_history
            SET status = 'applied', applied_at = NOW()
            WHERE id = %s
            """,
            (push_id,),
        )
        if push["reverts_push_id"]:
            cursor.execute(
                """
                UPDATE csv_push_history
                SET status = 'reverted'
                WHERE id = %s AND status = 'applied'
                """,
                (push["reverts_push_id"],),
            )
    conn.commit()
    wallet_count = sync_active_wallets(conn, wallets_path)
    print(f"Applied push #{push_id}; synced {wallet_count} wallet rows from rewritten CSV")
    return wallet_count


def update_sync_status(conn, version_folder, synced_count, error_message=None):
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sync_status (
                id, last_sync_at, current_version_folder, trades_synced_this_cycle,
                total_trades_synced, last_error
            )
            VALUES (1, NOW(), %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET last_sync_at = EXCLUDED.last_sync_at,
                current_version_folder = EXCLUDED.current_version_folder,
                trades_synced_this_cycle = EXCLUDED.trades_synced_this_cycle,
                total_trades_synced = sync_status.total_trades_synced + EXCLUDED.trades_synced_this_cycle,
                last_error = EXCLUDED.last_error
            """,
            (version_folder, synced_count, synced_count, error_message),
        )
    conn.commit()


def build_trade_rows(raw_rows):
    trades = []
    for row in raw_rows:
        if row.get("Action") not in ("Buy", "Sell"):
            continue
        try:
            trades.append(
                {
                    "tx_hash": row["Tx Hash"].strip(),
                    "timestamp": parse_timestamp(row["Date"].strip()),
                    "master_wallet": normalize_wallet(row["Master Wallet"]),
                    "own_wallet": (row.get("Own Wallet") or "").strip(),
                    "action": row["Action"],
                    "market": (row.get("Market") or "").strip(),
                    "outcome": (row.get("Outcome") or "").strip(),
                    "token_id": normalize_token_id(row.get("Token ID", "")),
                    "price": float(row["Price"]),
                    "shares": float(row["Shares"]),
                    "invested": float(row["Invested"]),
                    "received": float(row["Received"]),
                    "pnl_pct": parse_float_or_none(row.get("PNL %")),
                    "pct_sold": parse_float_or_none(row.get("% Sold")),
                    "reason": row.get("Reason", ""),
                    "game": normalize_game(row.get("Market", "")),
                }
            )
        except Exception as exc:
            print(f"WARNING: skipping malformed trade row: {exc}")
    trades.sort(key=lambda trade: trade["timestamp"])
    return trades


def main():
    load_env_file(SCRIPT_DIR / ".env")

    database_url = getenv("DATABASE_URL")
    downloads_dir = getenv("SHARP_DOWNLOADS_DIR", r"C:\Users\Administrator\Downloads")
    sharp_csv_filename = getenv("SHARP_CSV_FILENAME", "polymarket_copytrade.csv")
    sharp_csv_subpath = Path(getenv("SHARP_CSV_SUBPATH", r"config\trade_result_csv"))
    sharp_wallets_filename = getenv("SHARP_WALLETS_FILENAME", "active_wallets.csv")
    sharp_wallets_subpath = Path(getenv("SHARP_WALLETS_SUBPATH", r"config\polymarket_csv"))
    sync_interval_seconds = int(getenv("SYNC_INTERVAL_SECONDS", "300"))
    wallet_sync_interval_seconds = int(getenv("WALLET_SYNC_INTERVAL_SECONDS", "3600"))

    print("Wallet Curator VPS sync starting. This script only opens outbound connections to Railway Postgres.")
    print(f"Trade CSV path template: <version>/{sharp_csv_subpath}/{sharp_csv_filename}")
    print(f"Wallet CSV path template: <version>/{sharp_wallets_subpath}/{sharp_wallets_filename}")

    last_synced_tx_hash = load_last_synced_hash()
    last_wallet_sync_at = 0.0

    while True:
        version_name = ""
        try:
            sharp_folder = find_current_sharp_folder(downloads_dir, sharp_csv_subpath, sharp_csv_filename)
            version_name = sharp_folder.name
            print(f"Reading Sharp data from {sharp_folder}")

            csv_path = sharp_folder / sharp_csv_subpath / sharp_csv_filename
            raw_rows = read_csv_safely(csv_path)
            valid_trades = build_trade_rows(raw_rows)
            candidate_trades = find_trades_after_hash(valid_trades, last_synced_tx_hash)

            conn = connect_db(database_url)
            ensure_tables(conn)
            inserted_count = push_trades(conn, candidate_trades)
            if candidate_trades:
                last_synced_tx_hash = candidate_trades[-1]["tx_hash"]
                save_last_synced_hash(last_synced_tx_hash)

            update_sync_status(conn, version_name, inserted_count, error_message=None)
            if inserted_count:
                print(f"Synced {inserted_count} new trades from {version_name}")
            else:
                print("No new trades this cycle")

            if time.time() - last_wallet_sync_at >= wallet_sync_interval_seconds:
                wallets_path = sharp_folder / sharp_wallets_subpath / sharp_wallets_filename
                if wallets_path.exists():
                    wallet_count = sync_active_wallets(conn, wallets_path)
                    last_wallet_sync_at = time.time()
                    print(f"Synced {wallet_count} active wallets")
                else:
                    print(
                        "WARNING: active_wallets.csv not found. Verify the exact path before relying on wallet sync: "
                        f"{wallets_path}"
                    )
            else:
                wallets_path = sharp_folder / sharp_wallets_subpath / sharp_wallets_filename

            pending_push = check_pending_push(conn)
            if pending_push:
                if wallets_path.exists():
                    apply_csv_changes(conn, pending_push, wallets_path)
                    last_wallet_sync_at = time.time()
                else:
                    raise RuntimeError(
                        "Cannot apply pending CSV push because active_wallets.csv was not found at "
                        f"{wallets_path}"
                    )
            conn.close()

        except Exception as exc:
            print(f"ERROR: sync cycle failed: {exc}")
            try:
                conn = connect_db(database_url)
                ensure_tables(conn)
                update_sync_status(conn, version_name, 0, error_message=str(exc))
                conn.close()
            except Exception as status_exc:
                print(f"ERROR: failed to update sync_status after error: {status_exc}")

        time.sleep(sync_interval_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
