import csv
import io
import os
from typing import Dict, List, Optional

from lib.normalizers import normalize_wallet

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _row_to_line(header: List[str], row: Dict[str, str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([row.get(column, "") for column in header])
    return buffer.getvalue()


def parse_csv_text(csv_text: str):
    text = (csv_text or "").strip("\ufeff").strip()
    if not text:
        return {"header": [], "header_row": "", "global_row": None, "wallet_rows": []}

    lines = text.splitlines()
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    wallet_rows = []
    global_row = None

    for row in reader:
        address = normalize_wallet(row.get("address", ""))
        if not address:
            continue
        if address == "__global__":
            global_row = {key: row.get(key, "") for key in header}
            continue
        normalized = {key: row.get(key, "") for key in header}
        normalized["address"] = address
        wallet_rows.append(normalized)

    return {
        "header": header,
        "header_row": lines[0] if lines else "",
        "global_row": global_row,
        "wallet_rows": wallet_rows,
    }


def serialize_csv(header: List[str], global_row: Optional[Dict[str, str]], wallet_rows: List[Dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    if global_row:
        writer.writerow({column: global_row.get(column, "") for column in header})
    for row in wallet_rows:
        writer.writerow({column: row.get(column, "") for column in header})
    return buffer.getvalue()


def load_current_csv_state(conn):
    row = conn.execute("SELECT * FROM synced_csv_state WHERE id = 1").fetchone()
    if row and row["csv_content"]:
        parsed = parse_csv_text(row["csv_content"])
        parsed["csv_content"] = row["csv_content"]
        parsed["synced_at"] = row["synced_at"]
        return parsed

    local_path = os.path.join(BASE_DIR, "active_wallets.csv")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as handle:
            csv_text = handle.read()
        parsed = parse_csv_text(csv_text)
        parsed["csv_content"] = csv_text
        parsed["synced_at"] = None
        return parsed

    return {"header": [], "header_row": "", "global_row": None, "wallet_rows": [], "csv_content": "", "synced_at": None}


def validate_wallet_csv_line(raw_line: str, header: List[str]):
    if not header:
        raise ValueError("No canonical active_wallets.csv header is available yet. Wait for the first VPS wallet sync.")

    values = next(csv.reader([raw_line]))
    if len(values) != len(header):
        raise ValueError(f"Expected {len(header)} CSV columns, got {len(values)}.")

    row = {header[index]: values[index].strip() for index in range(len(header))}
    address = normalize_wallet(row.get("address", ""))
    if not address or address == "__global__":
        raise ValueError("Wallet CSV line must contain a real wallet address.")
    row["address"] = address
    return row


def set_row_copy_percentage(row: Dict[str, str], copy_percentage: float) -> Dict[str, str]:
    updated = dict(row)
    updated["copy_percentage_enabled"] = "true"
    updated["copy_percentage"] = str(float(copy_percentage)).rstrip("0").rstrip(".") if "." in str(float(copy_percentage)) else str(float(copy_percentage))
    return updated


def row_to_line(header: List[str], row: Dict[str, str]) -> str:
    return _row_to_line(header, row)


def apply_pending_changes(csv_text: str, pending_changes: List[Dict[str, object]]) -> str:
    parsed = parse_csv_text(csv_text)
    header = parsed["header"]
    global_row = parsed["global_row"]
    wallet_rows = [dict(row) for row in parsed["wallet_rows"]]

    def index_wallets():
        return {normalize_wallet(row.get("address", "")): idx for idx, row in enumerate(wallet_rows)}

    wallet_index = index_wallets()

    for change in pending_changes:
        details = change["details"]
        wallet = normalize_wallet(change["wallet_address"])
        change_type = change["change_type"]

        if change_type == "remove":
            idx = wallet_index.get(wallet)
            if idx is not None:
                wallet_rows.pop(idx)
                wallet_index = index_wallets()
            continue

        if change_type == "add":
            row = validate_wallet_csv_line(details["raw_csv_line"], header)
            row = set_row_copy_percentage(row, details["new_copy_pct"])
            idx = wallet_index.get(wallet)
            if idx is not None:
                wallet_rows[idx] = row
            else:
                wallet_rows.append(row)
            wallet_index = index_wallets()
            continue

        if change_type in {"promote", "demote", "update_tier_config"}:
            idx = wallet_index.get(wallet)
            if idx is None:
                continue
            wallet_rows[idx] = set_row_copy_percentage(wallet_rows[idx], details["new_copy_pct"])
            wallet_index = index_wallets()

    return serialize_csv(header, global_row, wallet_rows)


def summarize_changes(changes: List[Dict[str, object]]) -> str:
    counts = {"promote": 0, "demote": 0, "add": 0, "remove": 0, "update_tier_config": 0}
    for change in changes:
        counts[change["change_type"]] = counts.get(change["change_type"], 0) + 1

    parts = []
    if counts["promote"]:
        parts.append(f"{counts['promote']} promoted")
    if counts["demote"]:
        parts.append(f"{counts['demote']} demoted")
    if counts["add"]:
        parts.append(f"{counts['add']} added")
    if counts["remove"]:
        parts.append(f"{counts['remove']} removed")
    if counts["update_tier_config"]:
        parts.append(f"{counts['update_tier_config']} tier config updated")
    return ", ".join(parts) if parts else "No changes"
