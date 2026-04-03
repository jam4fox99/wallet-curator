from __future__ import annotations

from collections import Counter
from io import BytesIO
from typing import Any

from openpyxl import load_workbook


_RESULTS_REQUIRED = {
    "Wallet Address",
    "Category",
    "Subcategory",
    "Detail",
    "Trades",
    "Volume",
    "💰 Sim 1d",
    "💰 Sim 7d",
    "💰 Sim 30d",
    "📈 Sim ROI %",
    "📉 Max DD %",
    "✅ Copied",
    "⏭️ Skipped",
}

_DRL_REQUIRED = {
    "Timestamp (UTC)",
    "Status",
    "Side",
    "Market ID",
    "Question",
    "Token ID",
    "Copied Price",
    "Copied Shares",
    "Copied Notional $",
}


def _header_map(header_row: tuple[Any, ...]) -> dict[str, int]:
    return {
        str(value).strip(): idx
        for idx, value in enumerate(header_row)
        if value is not None and str(value).strip()
    }


def _require_columns(header_map: dict[str, int], required: set[str], sheet_name: str) -> None:
    missing = sorted(column for column in required if column not in header_map)
    if missing:
        raise ValueError(f"{sheet_name} is missing required columns: {', '.join(missing)}")


def _wallet_value(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") else None


def parse_sharpsim(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    if "📊 Results" not in workbook.sheetnames:
        raise ValueError("Sharpsim workbook is missing the 📊 Results")

    results_ws = workbook["📊 Results"]
    results_rows = results_ws.iter_rows(values_only=True)
    results_header = _header_map(next(results_rows))
    _require_columns(results_header, _RESULTS_REQUIRED, "📊 Results")

    session_meta = {"filename": filename}
    if "📦 Portfolio" in workbook.sheetnames:
        portfolio_ws = workbook["📦 Portfolio"]
        for label, value, *_ in portfolio_ws.iter_rows(min_row=1, values_only=True):
            if str(label or "").strip() == "Capital (CLI)":
                session_meta["capital"] = float(value or 0.0)
                break

    wallets: dict[str, dict[str, Any]] = {}
    wallet_order: list[str] = []
    filter_summary: Counter[str] = Counter()

    for row in results_rows:
        wallet = _wallet_value(row[results_header["Wallet Address"]])
        if not wallet:
            continue
        filter_value = str(row[results_header["Detail"]] or "").strip()
        wallets[wallet] = {
            "address": wallet,
            "filter_level": "detail",
            "filter_value": filter_value,
            "category": str(row[results_header["Category"]] or "").strip(),
            "subcategory": str(row[results_header["Subcategory"]] or "").strip(),
            "total_trades": int(row[results_header["Trades"]] or 0),
            "volume": float(row[results_header["Volume"]] or 0.0),
            "sim_1d": float(row[results_header["💰 Sim 1d"]] or 0.0),
            "sim_7d": float(row[results_header["💰 Sim 7d"]] or 0.0),
            "sim_30d": float(row[results_header["💰 Sim 30d"]] or 0.0),
            "sim_roi_pct": float(row[results_header["📈 Sim ROI %"]] or 0.0),
            "max_dd_pct": float(row[results_header["📉 Max DD %"]] or 0.0),
            "copied": int(row[results_header["✅ Copied"]] or 0),
            "skipped": int(row[results_header["⏭️ Skipped"]] or 0),
            "sim_status": "missing_drl",
            "sim_error": "",
        }
        wallet_order.append(wallet)
        filter_summary[filter_value] += 1

    if not wallet_order:
        raise ValueError("Sharpsim workbook did not contain any valid wallet rows")

    drl: dict[str, list[dict[str, Any]]] = {}
    for sheet_name in workbook.sheetnames:
        if not sheet_name.endswith("_DRL"):
            continue
        drl_ws = workbook[sheet_name]
        rows = drl_ws.iter_rows(min_row=5, values_only=True)
        header_map = _header_map(next(rows))
        _require_columns(header_map, _DRL_REQUIRED, sheet_name)
        wallet = _wallet_value(sheet_name.split("_")[1])
        if not wallet:
            continue
        wallet_rows = []
        for row in rows:
            if not row or row[header_map["Timestamp (UTC)"]] is None:
                continue
            wallet_rows.append(
                {
                    "ts": row[header_map["Timestamp (UTC)"]],
                    "status": str(row[header_map["Status"]] or "").strip().upper(),
                    "side": str(row[header_map["Side"]] or "").strip().upper(),
                    "condition_id": str(row[header_map["Market ID"]] or "").strip(),
                    "question": str(row[header_map["Question"]] or "").strip(),
                    "token_id": str(row[header_map["Token ID"]] or "").strip(),
                    "copied_price": float(row[header_map["Copied Price"]] or 0.0),
                    "copied_shares": float(row[header_map["Copied Shares"]] or 0.0),
                    "copied_notional": float(row[header_map["Copied Notional $"]] or 0.0),
                }
            )
        drl[wallet] = wallet_rows
        if wallet in wallets:
            wallets[wallet]["sim_status"] = "ready"

    return {
        "session_meta": session_meta,
        "wallet_order": wallet_order,
        "wallets": wallets,
        "drl": drl,
        "filter_summary": dict(filter_summary),
        "parse_errors": [],
    }
