# Sharpsim Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Sharpsim workbook upload, sim overlay rendering, per-wallet filter auto-switching, and CSV export to the existing wallet curation flow without creating a second review mode.

**Architecture:** Keep `app.py` as the single wallet-curation workflow, add a server-side Sharpsim session cache for uploaded workbook data, reuse `CurationPrefetchManager` for actual wallet data, and build sim overlay payloads by replaying copied DRL rows through the existing `build_chart_payload()` pricing semantics. Export rows are captured from the same review path so CSV output matches what the reviewer saw.

**Tech Stack:** Python 3.11, Dash, Plotly, dash-bootstrap-components, openpyxl, unittest executed through `pytest`

---

## File Map

- Create: `lib/sharpsim_parser.py`
  Parses the workbook fixture, normalizes Results and DRL rows, and builds interval-specific sim payloads by reusing `lib.clickhouse_charts`.
- Create: `lib/sharpsim_session.py`
  Holds uploaded workbook payloads in process-local memory so large DRL rows do not go through `dcc.Store`.
- Create: `tests/test_sharpsim_parser.py`
  Covers workbook parsing and sim replay behavior.
- Create: `tests/test_sharpsim_session.py`
  Covers create/get/clear semantics for the in-memory session manager.
- Modify: `lib/curation_prefetch.py`
  Change session priming to accept per-wallet filter configs and expose base payload access for sim replay.
- Modify: `app.py`
  Add sim stores and controls, upload/manual toggle callbacks, sim-aware curation view resolution, overlay rendering, and CSV export.
- Modify: `assets/dashboard-ui.css`
  Add styles for sim summary strip, sim comparison values, overlay controls, and validation text.
- Modify: `tests/test_curation_prefetch.py`
  Update prefetch tests for the wallet-config contract and base-payload access.
- Modify: `tests/test_app_curation.py`
  Add callback coverage for upload flow, sim-aware start flow, overlay rendering, degraded-wallet state, and CSV export.

## Task 1: Parse Sharpsim Workbook Uploads

**Files:**
- Create: `lib/sharpsim_parser.py`
- Test: `tests/test_sharpsim_parser.py`
- Reference: `tests/Sharpsim.xlsx`

- [ ] **Step 1: Write the failing parser tests**

```python
from io import BytesIO
from pathlib import Path
import unittest

from openpyxl import load_workbook

from lib.sharpsim_parser import parse_sharpsim


class SharpsimParserTests(unittest.TestCase):
    def test_parse_sharpsim_extracts_wallet_order_and_filter_summary(self):
        payload = parse_sharpsim(Path("tests/Sharpsim.xlsx").read_bytes(), "Sharpsim.xlsx")

        self.assertEqual(payload["wallet_order"][0], "0xdd92232bcdfbbac04132b3cbacbf32c2e5b16b2a")
        self.assertEqual(payload["wallet_order"][1], "0x31864feb9d25dee93728c6225ba891530967e9ca")
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["filter_level"], "detail")
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["filter_value"], "League of Legends")
        self.assertEqual(payload["filter_summary"]["League of Legends"], 43)
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["copied"], 1571)
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["skipped"], 3824)

    def test_parse_sharpsim_marks_missing_drl_wallet_without_failing_upload(self):
        workbook = load_workbook("tests/Sharpsim.xlsx")
        del workbook["01_0xdd9223_LOL_DRL"]
        buffer = BytesIO()
        workbook.save(buffer)

        payload = parse_sharpsim(buffer.getvalue(), "Sharpsim.xlsx")

        wallet = payload["wallet_order"][0]
        self.assertEqual(payload["wallets"][wallet]["sim_status"], "missing_drl")
        self.assertEqual(payload["drl"].get(wallet, []), [])
```

- [ ] **Step 2: Run the parser tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_parser.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.sharpsim_parser'`

- [ ] **Step 3: Write the minimal workbook parser**

```python
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
        raise ValueError("Sharpsim workbook is missing the 📊 Results sheet")

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
```

- [ ] **Step 4: Run the parser tests and verify they pass**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_parser.py -q`

Expected: PASS with `2 passed`

- [ ] **Step 5: Commit the parser skeleton**

```bash
git add lib/sharpsim_parser.py tests/test_sharpsim_parser.py
git commit -m "feat: parse sharpsim workbook uploads"
```

## Task 2: Replay Copied DRL Rows Into Sim Payloads

**Files:**
- Modify: `lib/sharpsim_parser.py`
- Modify: `tests/test_sharpsim_parser.py`

- [ ] **Step 1: Write the failing replay tests**

```python
from datetime import date, datetime
from unittest.mock import patch


def _drl_row(ts, status, side, token_id, condition_id, question, price, shares, notional):
    return {
        "ts": ts,
        "status": status,
        "side": side,
        "token_id": token_id,
        "condition_id": condition_id,
        "question": question,
        "copied_price": price,
        "copied_shares": shares,
        "copied_notional": notional,
    }


class SharpsimReplayTests(unittest.TestCase):
    def test_build_sim_payload_filters_to_copied_rows_and_rebases_selected_window(self):
        wallet_meta = {
            "address": "0xabc",
            "filter_value": "League of Legends",
            "copied": 2,
            "skipped": 1,
            "sim_7d": 4.0,
            "sim_30d": 6.0,
        }
        drl_rows = [
            _drl_row(datetime(2026, 3, 28, 10, 0), "COPIED", "BUY", "yes-token", "cid-1", "Match 1", 0.40, 10.0, 4.0),
            _drl_row(datetime(2026, 3, 29, 10, 0), "SKIPPED", "BUY", "yes-token", "cid-1", "Match 1", 0.60, 10.0, 6.0),
            _drl_row(datetime(2026, 4, 2, 10, 0), "COPIED", "SELL", "yes-token", "cid-1", "Match 1", 0.80, 5.0, 4.0),
        ]
        closes = [
            {"token_id": "yes-token", "trade_date": date(2026, 3, 27), "close_price": 0.40},
            {"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.70},
        ]
        resolutions = {}

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        with patch("lib.sharpsim_parser.date", FixedDate):
            payload = build_sim_payload(wallet_meta, drl_rows, closes, resolutions, "7D")

        self.assertEqual(payload["summary"]["total_trades"], 2)
        self.assertEqual(payload["summary"]["copied_trades"], 2)
        self.assertEqual(payload["summary"]["sim_status"], "ready")
        self.assertEqual(payload["validation"]["workbook_value"], 4.0)
        self.assertEqual(payload["series"][0]["pnl"], 0.0)

    def test_build_sim_payload_carries_opening_positions_from_pre_window_copied_rows(self):
        wallet_meta = {"address": "0xabc", "filter_value": "League of Legends", "copied": 1, "skipped": 0}
        drl_rows = [
            _drl_row(datetime(2026, 3, 1, 9, 0), "COPIED", "BUY", "yes-token", "cid-1", "Match 1", 0.40, 10.0, 4.0),
        ]
        closes = [
            {"token_id": "yes-token", "trade_date": date(2026, 3, 31), "close_price": 0.40},
            {"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.60},
        ]

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        with patch("lib.sharpsim_parser.date", FixedDate):
            payload = build_sim_payload(wallet_meta, drl_rows, closes, {}, "1D")

        self.assertEqual(payload["summary"]["first_trade_date"], "2026-04-02")
        self.assertEqual(payload["series"][-1]["marked_value"], 2.0)
```

- [ ] **Step 2: Run the replay tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_parser.py -q`

Expected: FAIL with `NameError: name 'build_sim_payload' is not defined`

- [ ] **Step 3: Implement sim replay using existing chart pricing semantics**

```python
from datetime import date, timedelta

from lib.clickhouse_charts import (
    CURATION_ALL_RANGE,
    CURATION_RANGE_DAYS,
    build_chart_payload,
    normalize_curation_range_key,
)


def _normalize_copied_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": row["ts"].date(),
        "ts": row["ts"],
        "token_id": row["token_id"],
        "condition_id": row["condition_id"],
        "side": row["side"],
        "shares": float(row["copied_shares"] or 0.0),
        "usdc": float(row["copied_notional"] or 0.0),
        "fee_usdc": 0.0,
        "price": float(row["copied_price"] or 0.0),
        "role": "sim",
    }


def _token_scope_from_trades(trades: list[dict[str, Any]], opening_positions: dict[str, float], visible_counts: dict[str, int]) -> list[dict[str, Any]]:
    ordered: dict[str, dict[str, Any]] = {}
    for trade in trades:
        ordered.setdefault(
            trade["token_id"],
            {
                "token_id": trade["token_id"],
                "condition_id": trade["condition_id"],
                "question": trade["question"],
                "first_trade_ts": trade["ts"],
                "last_trade_ts": trade["ts"],
                "opening_shares": opening_positions.get(trade["token_id"], 0.0),
                "visible_trade_count": visible_counts.get(trade["token_id"], 0),
            },
        )
        ordered[trade["token_id"]]["last_trade_ts"] = trade["ts"]
    return list(ordered.values())


def build_sim_payload(wallet_meta: dict[str, Any], drl_rows: list[dict[str, Any]], closes: list[dict[str, Any]], resolutions: dict[str, Any], range_key: Any = CURATION_ALL_RANGE) -> dict[str, Any]:
    copied_rows = [row for row in drl_rows if row.get("status") == "COPIED"]
    if not copied_rows:
        return {
            "series": [],
            "summary": {"sim_status": wallet_meta.get("sim_status", "missing_drl"), "copied_trades": 0, "total_trades": 0, "final_pnl": 0.0, "roi_pct": 0.0},
            "validation": {"workbook_value": None, "recomputed_value": None, "delta": None},
        }

    normalized_range = normalize_curation_range_key(range_key)
    today_value = date.today()
    if normalized_range == CURATION_ALL_RANGE:
        window_start_date = min(row["ts"].date() for row in copied_rows)
    else:
        window_start_date = today_value - timedelta(days=CURATION_RANGE_DAYS[normalized_range])

    opening_positions: dict[str, float] = {}
    visible_trades: list[dict[str, Any]] = []
    visible_counts: dict[str, int] = {}
    normalized_trades = []

    for row in copied_rows:
        trade = _normalize_copied_trade(row) | {"question": row["question"]}
        normalized_trades.append(trade)
        token_id = trade["token_id"]
        if trade["trade_date"] < window_start_date:
            signed = trade["shares"] if trade["side"] == "BUY" else -trade["shares"]
            opening_positions[token_id] = opening_positions.get(token_id, 0.0) + signed
            continue
        visible_trades.append(trade)
        visible_counts[token_id] = visible_counts.get(token_id, 0) + 1

    token_scope = _token_scope_from_trades(normalized_trades, opening_positions, visible_counts)
    chart = build_chart_payload(
        wallet_meta["address"],
        wallet_meta.get("filter_value", ""),
        CURATION_RANGE_DAYS.get(normalized_range, 0),
        token_scope,
        visible_trades,
        closes,
        resolutions,
        opening_positions=opening_positions,
        window_start_date=window_start_date,
    )

    workbook_map = {
        "1D": wallet_meta.get("sim_1d"),
        "7D": wallet_meta.get("sim_7d"),
        "30D": wallet_meta.get("sim_30d"),
    }
    workbook_value = workbook_map.get(normalized_range)
    recomputed_value = chart["summary"]["final_pnl"] if chart else 0.0
    if chart:
        volume = sum(trade["usdc"] for trade in visible_trades)
        chart["summary"]["copied_trades"] = len(copied_rows)
        chart["summary"]["copied_volume_usd"] = round(volume, 2)
        chart["summary"]["sim_status"] = wallet_meta.get("sim_status", "ready")
        chart["summary"]["roi_pct"] = round((recomputed_value / volume) * 100, 2) if volume else 0.0
        chart["validation"] = {
            "workbook_value": workbook_value,
            "recomputed_value": recomputed_value,
            "delta": None if workbook_value is None else round(recomputed_value - float(workbook_value), 2),
        }
    return chart
```

- [ ] **Step 4: Run the replay tests and verify they pass**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_parser.py -q`

Expected: PASS with `4 passed`

- [ ] **Step 5: Commit the replay helper**

```bash
git add lib/sharpsim_parser.py tests/test_sharpsim_parser.py
git commit -m "feat: replay sharpsim copied trades"
```

## Task 3: Add A Server-Side Sharpsim Session Cache

**Files:**
- Create: `lib/sharpsim_session.py`
- Test: `tests/test_sharpsim_session.py`

- [ ] **Step 1: Write the failing session-manager tests**

```python
import time
import unittest

from lib.sharpsim_session import SharpsimSessionManager


class SharpsimSessionManagerTests(unittest.TestCase):
    def test_create_get_and_clear_session(self):
        manager = SharpsimSessionManager(ttl_seconds=60, max_sessions=5)
        payload = {"wallet_order": ["0xabc"], "wallets": {"0xabc": {"address": "0xabc"}}}

        session_id = manager.create_session(payload)

        self.assertEqual(manager.get_session(session_id)["wallet_order"], ["0xabc"])
        self.assertEqual(manager.get_wallet_meta(session_id, "0xabc")["address"], "0xabc")

        manager.clear_session(session_id)
        self.assertIsNone(manager.get_session(session_id))

    def test_stale_sessions_are_evicted_on_access(self):
        manager = SharpsimSessionManager(ttl_seconds=1, max_sessions=5)
        session_id = manager.create_session({"wallet_order": []})
        manager._sessions[session_id].last_accessed = time.time() - 5

        self.assertIsNone(manager.get_session(session_id))
```

- [ ] **Step 2: Run the session-manager tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_session.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'lib.sharpsim_session'`

- [ ] **Step 3: Implement the in-memory session manager**

```python
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharpsimSession:
    session_id: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


class SharpsimSessionManager:
    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 12):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, SharpsimSession] = {}

    def create_session(self, payload: dict[str, Any]) -> str:
        with self._lock:
            self._evict_locked()
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = SharpsimSession(session_id=session_id, payload=payload)
            return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if not session:
                return None
            session.last_accessed = time.time()
            return session.payload

    def get_wallet_meta(self, session_id: str, wallet: str) -> dict[str, Any] | None:
        payload = self.get_session(session_id)
        if not payload:
            return None
        return payload.get("wallets", {}).get(wallet.strip().lower())

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _evict_locked(self) -> None:
        now = time.time()
        stale = [session_id for session_id, session in self._sessions.items() if now - session.last_accessed > self.ttl_seconds]
        for session_id in stale:
            self._sessions.pop(session_id, None)
        if len(self._sessions) <= self.max_sessions:
            return
        oldest = sorted(self._sessions.values(), key=lambda session: session.last_accessed)
        for session in oldest[: len(self._sessions) - self.max_sessions]:
            self._sessions.pop(session.session_id, None)


_MANAGER: SharpsimSessionManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_sharpsim_session_manager() -> SharpsimSessionManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = SharpsimSessionManager()
    return _MANAGER
```

- [ ] **Step 4: Run the session-manager tests and verify they pass**

Run: `PYTHONPATH=. pytest tests/test_sharpsim_session.py -q`

Expected: PASS with `2 passed`

- [ ] **Step 5: Commit the session cache**

```bash
git add lib/sharpsim_session.py tests/test_sharpsim_session.py
git commit -m "feat: cache sharpsim uploads in memory"
```

## Task 4: Prime Curation Prefetch With Per-Wallet Filters

**Files:**
- Modify: `lib/curation_prefetch.py:18-257`
- Modify: `tests/test_curation_prefetch.py`

- [ ] **Step 1: Write the failing prefetch tests for wallet configs**

```python
    def test_overlapping_sessions_do_not_block_each_other(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=60, max_ready_entries=20)
        started = []

        def fake_fetch(wallet, filter_value, filter_level):
            started.append((wallet, filter_value, filter_level))
            time.sleep(0.05)
            return {"wallet": wallet, "filter_value": filter_value}

        with patch("lib.curation_prefetch.get_wallet_curation_base_data", side_effect=fake_fetch):
            manager.prime_session(
                "session-a",
                [
                    {"address": "0xa0", "filter_level": "detail", "filter_value": "League of Legends"},
                    {"address": "0xa1", "filter_level": "detail", "filter_value": "Valorant"},
                ],
                warm_count=2,
            )
            manager.prime_session(
                "session-b",
                [{"address": "0xb0", "filter_level": "subcategory", "filter_value": "Esports"}],
                warm_count=1,
            )
            time.sleep(0.25)

        self.assertEqual(
            started,
            [
                ("0xa0", "League of Legends", "detail"),
                ("0xb0", "Esports", "subcategory"),
                ("0xa1", "Valorant", "detail"),
            ],
        )

    def test_get_base_payload_returns_cached_lifetime_payload(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=60, max_ready_entries=20)
        base_key = manager.make_base_key("0xabc", "detail", "League of Legends")
        manager._cache[base_key] = CacheEntry(key=base_key, status="ready", payload={"wallet": "0xabc", "closes": [1]})

        self.assertEqual(manager.get_base_payload(base_key), {"wallet": "0xabc", "closes": [1]})
```

- [ ] **Step 2: Run the prefetch tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_curation_prefetch.py -q`

Expected: FAIL because `prime_session()` still expects `wallets`, `filter_level`, and `filter_value`, and `get_base_payload()` does not exist yet

- [ ] **Step 3: Switch the manager contract to wallet configs**

```python
WalletConfig = dict[str, str]


class CurationPrefetchManager:
    def prime_session(self, session_id: str, wallet_configs: list[WalletConfig], warm_count: int = 6) -> None:
        keys = [
            self.make_base_key(
                config.get("address", ""),
                config.get("filter_level", "detail"),
                config.get("filter_value", ""),
            )
            for config in wallet_configs
            if config.get("address")
        ]
        with self._lock:
            self._sessions[session_id] = keys
            self._session_last_access[session_id] = time.time()
            if not keys:
                return
            for idx, key in enumerate(keys):
                priority = 0 if idx == 0 else (1 if idx < warm_count else 2)
                self._enqueue_locked(key, session_id=session_id, priority=priority)
            self._dispatch_locked()

    def get_base_payload(self, base_key: WalletBaseKey) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(base_key)
            if not entry or entry.status != "ready":
                return None
            entry.last_accessed = time.time()
            return entry.payload
```

- [ ] **Step 4: Run the prefetch tests and verify they pass**

Run: `PYTHONPATH=. pytest tests/test_curation_prefetch.py -q`

Expected: PASS with `4 passed`

- [ ] **Step 5: Commit the prefetch contract update**

```bash
git add lib/curation_prefetch.py tests/test_curation_prefetch.py
git commit -m "refactor: prime curation cache with wallet configs"
```

## Task 5: Add Sim Upload State And Sim-Aware Review Startup

**Files:**
- Modify: `app.py:1208-1325`
- Modify: `app.py:2983-3150`
- Modify: `tests/test_app_curation.py`

- [ ] **Step 1: Write the failing app-state tests**

```python
    def test_handle_sim_upload_activates_sim_mode(self):
        fake_payload = {
            "wallet_order": ["0xabc"],
            "wallets": {"0xabc": {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"}},
            "filter_summary": {"League of Legends": 1},
        }
        session_manager = Mock()
        session_manager.create_session.return_value = "sim-session-1"

        with patch("lib.sharpsim_parser.parse_sharpsim", return_value=fake_payload), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager
        ):
            result = app.handle_sim_upload(
                "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,ZmFrZQ==",
                "Sharpsim.xlsx",
            )

        self.assertEqual(result[0], "sim-session-1")
        self.assertTrue(result[1])
        self.assertTrue(result[2])
        self.assertEqual(result[4], {"display": "none"})
        self.assertEqual(result[5], {"display": "inline-flex"})

    def test_start_curation_uses_sim_wallet_configs_when_session_is_active(self):
        payload = {
            "wallet_order": ["0xabc", "0xdef"],
            "wallets": {
                "0xabc": {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"},
                "0xdef": {"address": "0xdef", "filter_level": "detail", "filter_value": "Valorant"},
            },
        }
        session_manager = Mock()
        session_manager.get_session.return_value = payload
        prefetch_manager = Mock()

        with patch("lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager), patch(
            "lib.curation_prefetch.get_curation_prefetch_manager", return_value=prefetch_manager
        ):
            result = app.start_curation(1, "", None, "30D", True, "sim-session-1")

        self.assertEqual(result[0], ["0xabc", "0xdef"])
        self.assertEqual(result[1], "detail::League of Legends")
        self.assertEqual(
            prefetch_manager.prime_session.call_args.kwargs["wallet_configs"],
            [
                {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"},
                {"address": "0xdef", "filter_level": "detail", "filter_value": "Valorant"},
            ],
        )
```

- [ ] **Step 2: Run the app-state tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_app_curation.py -q`

Expected: FAIL because `handle_sim_upload()` does not exist and `start_curation()` does not accept sim-session state

- [ ] **Step 3: Implement sim stores, upload callback, and sim-aware startup**

```python
def wallet_curation_layout():
    return html.Div(
        className="pm-page-stack",
        children=[
            dcc.Store(id="cur-wallets", data=[]),
            dcc.Store(id="cur-index", data=0),
            dcc.Store(id="cur-approved", data=[]),
            dcc.Store(id="cur-decisions", data={}),
            dcc.Store(id="cur-filter", data=""),
            dcc.Store(id="cur-range", data=CURATION_ALL_RANGE),
            dcc.Store(id="cur-session-id", data=""),
            dcc.Store(id="cur-view", data={"status": "idle"}),
            dcc.Store(id="cur-sim-session-id", data=""),
            dcc.Store(id="cur-sim-active", data=False),
            dcc.Store(id="cur-sim-overlay-visible", data=False),
            dcc.Interval(id="cur-prefetch-poll", interval=1500, n_intervals=0, disabled=True),
            html.Div(
                id="cur-setup",
                children=[
                    _card(
                        [
                            html.Div(
                                [
                                    html.Div("Wallet Curation", className="pm-kicker"),
                                    html.H2("Swipe Review", className="pm-section-title pm-section-title--blue"),
                                ],
                                className="pm-card-title-block",
                            ),
                            html.Div(
                                [
                                    dcc.Upload(
                                        id="cur-sim-upload",
                                        children=html.Button("Upload Sharpsim", className="pm-button pm-button--secondary"),
                                        accept=".xlsx",
                                    ),
                                    html.Button(
                                        "Switch to manual",
                                        id="cur-sim-clear",
                                        className="pm-button pm-button--secondary",
                                        n_clicks=0,
                                        style={"display": "none"},
                                    ),
                                ],
                                style={"display": "flex", "gap": "12px", "marginBottom": "12px"},
                            ),
                            html.Div(id="cur-sim-summary", className="pm-inline-message"),
                            html.Div(
                                id="cur-manual-fields",
                                children=[
                                    html.Div("Paste wallet addresses (one per line)", className="pm-field-label"),
                                    dcc.Textarea(
                                        id="cur-wallet-input",
                                        className="pm-textarea pm-textarea--mono",
                                        placeholder="0xabc123...\n0xdef456...\n0x789...",
                                        style={"minHeight": "120px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Div("Category", className="pm-field-label"),
                                                    dcc.Dropdown(
                                                        id="cur-category",
                                                        placeholder="Select category...",
                                                        searchable=True,
                                                        className="pm-wallet-dropdown",
                                                    ),
                                                ],
                                                style={"flex": "1"},
                                            ),
                                        ],
                                        style={"display": "flex", "gap": "16px", "margin": "12px 0"},
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        opt["label"],
                                        id=f"cur-setup-range-{opt['token']}",
                                        className="pm-range-pill" + (" pm-range-pill--active" if opt["token"] == CURATION_ALL_RANGE else ""),
                                        n_clicks=0,
                                    )
                                    for opt in _CUR_RANGE_OPTIONS
                                ],
                                className="pm-range-pill-group",
                                style={"marginBottom": "16px"},
                            ),
                            html.Button("Start Review", id="cur-start", className="pm-button pm-button--primary", n_clicks=0),
                            html.Div(id="cur-setup-msg", style={"marginTop": "8px"}),
                        ],
                        class_name="pm-admin-card",
                    ),
                ],
            ),
            html.Div(
                id="cur-swipe",
                style={"display": "none"},
                children=[
                    html.Div(id="cur-progress", style={"marginBottom": "12px", "color": "var(--pm-text-secondary)", "fontSize": "13px"}),
                    html.Div(id="cur-wallet-header", style={"marginBottom": "8px"}),
                    html.Div(id="cur-sim-summary-panel", style={"marginBottom": "12px"}),
                    dcc.Graph(id="cur-chart", style={"height": "400px"}, config={"displayModeBar": False}),
                    html.Div(id="cur-stats"),
                ],
            ),
        ],
    )


@callback(
    [
        Output("cur-sim-session-id", "data"),
        Output("cur-sim-active", "data"),
        Output("cur-sim-overlay-visible", "data"),
        Output("cur-sim-summary", "children"),
        Output("cur-manual-fields", "style"),
        Output("cur-sim-clear", "style"),
        Output("cur-setup-msg", "children"),
    ],
    Input("cur-sim-upload", "contents"),
    State("cur-sim-upload", "filename"),
    prevent_initial_call=True,
)
def handle_sim_upload(contents, filename):
    if not contents:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update
    try:
        from base64 import b64decode
        from lib.sharpsim_parser import parse_sharpsim
        from lib.sharpsim_session import get_sharpsim_session_manager

        _, encoded = contents.split(",", 1)
        payload = parse_sharpsim(b64decode(encoded), filename or "")
        session_id = get_sharpsim_session_manager().create_session(payload)
        summary = " | ".join(
            [f"{len(payload['wallet_order'])} wallets loaded from Sharpsim"]
            + [f"{label} ({count})" for label, count in payload["filter_summary"].items()]
        )
        return session_id, True, True, dbc.Alert(summary, color="secondary"), {"display": "none"}, {"display": "inline-flex"}, ""
    except Exception as exc:
        logger.exception("Sharpsim upload failed")
        return "", False, False, "", {"display": "block"}, {"display": "none"}, dbc.Alert(f"Invalid Sharpsim workbook: {exc}", color="danger")


@callback(
    [
        Output("cur-sim-session-id", "data", allow_duplicate=True),
        Output("cur-sim-active", "data", allow_duplicate=True),
        Output("cur-sim-overlay-visible", "data", allow_duplicate=True),
        Output("cur-sim-summary", "children", allow_duplicate=True),
        Output("cur-manual-fields", "style", allow_duplicate=True),
        Output("cur-sim-clear", "style", allow_duplicate=True),
    ],
    Input("cur-sim-clear", "n_clicks"),
    State("cur-sim-session-id", "data"),
    prevent_initial_call=True,
)
def clear_sim_session(n_clicks, sim_session_id):
    if not n_clicks:
        return [no_update] * 6
    from lib.sharpsim_session import get_sharpsim_session_manager

    if sim_session_id:
        get_sharpsim_session_manager().clear_session(sim_session_id)
    return "", False, False, "", {"display": "block"}, {"display": "none"}


def _wallet_configs_from_manual_inputs(wallet_text, category):
    wallets = [wallet.strip().lower() for wallet in (wallet_text or "").splitlines() if wallet.strip().startswith("0x")]
    if "::" in (category or ""):
        filter_level, filter_value = category.split("::", 1)
    else:
        filter_level, filter_value = "detail", category or ""
    wallet_configs = [
        {"address": wallet, "filter_level": filter_level, "filter_value": filter_value}
        for wallet in wallets
    ]
    return wallets, wallet_configs, f"{filter_level}::{filter_value}"


def _wallet_configs_from_sim_session(sim_session_id):
    from lib.sharpsim_session import get_sharpsim_session_manager

    payload = get_sharpsim_session_manager().get_session(sim_session_id) or {}
    wallet_configs = [
        {
            "address": wallet,
            "filter_level": payload["wallets"][wallet]["filter_level"],
            "filter_value": payload["wallets"][wallet]["filter_value"],
        }
        for wallet in payload.get("wallet_order", [])
    ]
    filter_raw = ""
    if wallet_configs:
        filter_raw = f"{wallet_configs[0]['filter_level']}::{wallet_configs[0]['filter_value']}"
    return payload.get("wallet_order", []), wallet_configs, filter_raw


def start_curation(n_clicks, wallet_text, category, selected_range, sim_active, sim_session_id):
    if not n_clicks:
        return [no_update] * 10
    if sim_active and sim_session_id:
        wallets, wallet_configs, filter_raw = _wallet_configs_from_sim_session(sim_session_id)
        if not wallets:
            return [no_update] * 9 + [dbc.Alert("Uploaded Sharpsim session has no wallets.", color="warning")]
    else:
        wallets, wallet_configs, filter_raw = _wallet_configs_from_manual_inputs(wallet_text, category)
        if not wallets:
            return [no_update] * 9 + [dbc.Alert("Paste at least one wallet address.", color="warning")]
        if not category:
            return [no_update] * 9 + [dbc.Alert("Select a category.", color="warning")]
    session_id = uuid.uuid4().hex
    get_curation_prefetch_manager().prime_session(session_id=session_id, wallet_configs=wallet_configs, warm_count=6)
    return wallets, filter_raw, 0, [], {}, session_id, {"display": "none"}, {"display": "block"}, {"display": "none"}, ""
```

- [ ] **Step 4: Run the app-state tests and verify they pass**

Run: `PYTHONPATH=. pytest tests/test_app_curation.py -q`

Expected: PASS with the new upload/start tests plus the existing curation tests

- [ ] **Step 5: Commit the upload and startup plumbing**

```bash
git add app.py tests/test_app_curation.py
git commit -m "feat: add sharpsim upload flow to wallet curation"
```

## Task 6: Render The Overlay, Degrade Gracefully, And Export CSV Results

**Files:**
- Modify: `app.py:1270-1320`
- Modify: `app.py:2983-3340`
- Modify: `assets/dashboard-ui.css`
- Modify: `tests/test_app_curation.py`

- [ ] **Step 1: Write the failing render and export tests**

```python
    def test_render_curation_wallet_adds_sim_trace_when_overlay_visible(self):
        actual_manager = Mock()
        session_manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "detail", "League of Legends")
        base_payload = {
            "wallet": wallet,
            "filter_value": "League of Legends",
            "closes": [{"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.7}],
            "resolutions": {},
        }
        actual_payload = {
            "series": [{"date": "2026-04-03", "pnl": 1.0, "cumulative_cash": 0.0, "marked_value": 1.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 1.0, "roi_pct": 1.0, "total_trades": 1, "total_volume_usd": 10.0, "unique_markets": 1, "active_days": 1},
            "signals": {},
            "breakdown": {"markets": []},
        }
        sim_session = {
            "wallets": {wallet: {"address": wallet, "filter_value": "League of Legends", "sim_status": "ready", "copied": 2, "skipped": 1}},
            "drl": {wallet: []},
        }
        sim_payload = {
            "series": [{"date": "2026-04-03", "pnl": 2.0, "cumulative_cash": 0.0, "marked_value": 2.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 2.0, "roi_pct": 20.0, "copied_trades": 2, "sim_status": "ready"},
            "validation": {"workbook_value": 2.0, "recomputed_value": 2.0, "delta": 0.0},
        }
        actual_manager.make_base_key.return_value = base_key
        actual_manager.get_payload.return_value = actual_payload
        actual_manager.get_base_payload.return_value = base_payload
        session_manager.get_session.return_value = sim_session

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=actual_manager), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager
        ), patch("lib.sharpsim_parser.build_sim_payload", return_value=sim_payload):
            result = app.render_curation_wallet(
                {"status": "ready", "wallet": wallet, "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                True,
                "sim-session-1",
            )

        figure = result[3]
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[1].name, "Sim P&L")

    def test_render_curation_wallet_shows_warning_when_sim_payload_is_unavailable(self):
        actual_manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "detail", "League of Legends")
        actual_manager.make_base_key.return_value = base_key
        actual_manager.get_payload.return_value = {
            "series": [{"date": "2026-04-03", "pnl": 1.0, "cumulative_cash": 0.0, "marked_value": 1.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 1.0, "roi_pct": 1.0, "total_trades": 1, "total_volume_usd": 10.0, "unique_markets": 1, "active_days": 1},
            "signals": {},
            "breakdown": {"markets": []},
        }
        actual_manager.get_base_payload.return_value = {"wallet": wallet, "filter_value": "League of Legends", "closes": [], "resolutions": {}}

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=actual_manager), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager"
        ) as session_manager, patch("lib.sharpsim_parser.build_sim_payload", return_value=None):
            session_manager.return_value.get_session.return_value = {
                "wallets": {wallet: {"address": wallet, "filter_value": "League of Legends", "sim_status": "missing_drl", "sim_error": ""}},
                "drl": {wallet: []},
            }
            result = app.render_curation_wallet(
                {"status": "ready", "wallet": wallet, "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                True,
                "sim-session-1",
            )

        self.assertTrue(hasattr(result[3], "to_plotly_json"))
        self.assertIn("Sim data unavailable", str(result[1]))

    def test_download_curation_results_returns_csv_for_reviewed_wallets(self):
        decisions = {
            "0xabc": {
                "wallet": "0xabc",
                "decision": "approved",
                "filter_level": "detail",
                "filter_value": "League of Legends",
                "actual_final_pnl": 12.5,
                "actual_roi_pct": 10.0,
                "sim_final_pnl": 4.0,
                "sim_roi_pct": 40.0,
                "sim_copied": 2,
                "sim_skipped": 1,
                "sim_status": "ready",
            }
        }

        download = app.download_curation_results(1, decisions)

        self.assertTrue(download["filename"].startswith("curation_results_"))
        self.assertIn("wallet,filter_level,filter_value,decision", download["content"])
        self.assertIn("0xabc,detail,League of Legends,approved,12.5,10.0,4.0,40.0,2,1,ready", download["content"])

    def test_handle_curation_action_records_structured_decision_rows(self):
        with patch("app._build_curation_decision_row", return_value={"wallet": "0xabc", "decision": "approved", "sim_status": "ready"}):
            result = app.handle_curation_action(
                1,
                0,
                0,
                0,
                ["0xabc"],
                [],
                {},
                {"status": "ready", "wallet": "0xabc", "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                "sim-session-1",
            )

        self.assertEqual(result[2]["0xabc"]["sim_status"], "ready")
```

- [ ] **Step 2: Run the render/export tests and verify they fail**

Run: `PYTHONPATH=. pytest tests/test_app_curation.py -q`

Expected: FAIL because `render_curation_wallet()` does not accept sim inputs yet and `download_curation_results()` does not exist

- [ ] **Step 3: Implement overlay rendering, degraded sim state, and CSV export**

```python
def _build_sim_summary_strip(wallet_meta):
    values = []
    if wallet_meta.get("capital"):
        values.append(html.Span(f"${wallet_meta['capital']:,.0f} capital"))
    if wallet_meta.get("copy_ratio") is not None:
        values.append(html.Span(f"{wallet_meta['copy_ratio']:.0%} copy ratio"))
    if wallet_meta.get("execution_mode"):
        values.append(html.Span(wallet_meta["execution_mode"]))
    values.append(html.Span(f"{wallet_meta.get('copied', 0):,} copied / {wallet_meta.get('skipped', 0):,} skipped"))
    return html.Div(values, className="pm-sim-summary-strip")


def _build_sim_validation(validation):
    if not validation or validation.get("workbook_value") is None:
        return ""
    delta = float(validation.get("delta") or 0.0)
    tone = "pm-sim-validation--ok" if abs(delta) <= 10 else "pm-sim-validation--warn" if abs(delta) <= 100 else "pm-sim-validation--bad"
    return html.Div(
        f"Workbook: ${validation['workbook_value']:,.2f} · Recomputed: ${validation['recomputed_value']:,.2f} · Δ ${delta:,.2f}",
        className=f"pm-sim-validation {tone}",
    )


def _build_curation_stats(actual_summary, sim_payload, wallet_meta):
    sim_summary = (sim_payload or {}).get("summary", {})
    sim_pnl = sim_summary.get("final_pnl")
    sim_roi = sim_summary.get("roi_pct")
    return html.Div(
        [
            _stat_tile("Final P&L", f"${actual_summary['final_pnl']:,.2f}", tone="positive" if actual_summary["final_pnl"] >= 0 else "negative"),
            html.Div([_stat_tile("Sim P&L", "N/A" if sim_pnl is None else f"${sim_pnl:,.2f}", tone="positive" if (sim_pnl or 0) >= 0 else "negative")], className="pm-sim-stat"),
            _stat_tile("ROI", f"{actual_summary.get('roi_pct', 0):.1f}%"),
            html.Div([_stat_tile("Sim ROI", "N/A" if sim_roi is None else f"{sim_roi:.1f}%")], className="pm-sim-stat"),
            _stat_tile("Trades", f"{actual_summary['total_trades']:,}"),
            html.Div([_stat_tile("Copied", f"{wallet_meta.get('copied', 0):,}")], className="pm-sim-stat"),
        ],
        className="pm-wallet-stat-grid pm-wallet-stat-grid--sim",
    )


def _load_sim_payload(sim_session_id, wallet, range_key, base_payload):
    if not sim_session_id or not base_payload:
        return None, None
    from lib.sharpsim_parser import build_sim_payload
    from lib.sharpsim_session import get_sharpsim_session_manager

    session = get_sharpsim_session_manager().get_session(sim_session_id) or {}
    wallet_meta = (session.get("wallets") or {}).get(wallet)
    if not wallet_meta:
        return None, None
    wallet_meta = {
        **wallet_meta,
        "capital": (session.get("session_meta") or {}).get("capital"),
        "copy_ratio": (session.get("session_meta") or {}).get("copy_ratio"),
        "execution_mode": (session.get("session_meta") or {}).get("execution_mode"),
    }
    sim_payload = None
    if wallet_meta.get("sim_status") == "ready":
        sim_payload = build_sim_payload(wallet_meta, (session.get("drl") or {}).get(wallet, []), base_payload.get("closes", []), base_payload.get("resolutions", {}), range_key)
    return wallet_meta, sim_payload


def _build_curation_decision_row(view, decision, sim_session_id):
    manager = get_curation_prefetch_manager()
    wallet = view["wallet"]
    filter_level = view.get("filter_level", "detail")
    filter_value = view.get("filter_value", "")
    range_key = _normalize_curation_range(view.get("range"))
    key = manager.make_base_key(wallet, filter_level, filter_value)
    actual_payload = manager.get_payload(key, range_key) or {}
    base_payload = manager.get_base_payload(key) or {}
    wallet_meta, sim_payload = _load_sim_payload(sim_session_id, wallet, range_key, base_payload)
    actual_summary = actual_payload.get("summary", {})
    sim_summary = (sim_payload or {}).get("summary", {})
    return {
        "wallet": wallet,
        "filter_level": filter_level,
        "filter_value": filter_value,
        "decision": decision,
        "actual_final_pnl": actual_summary.get("final_pnl", 0.0),
        "actual_roi_pct": actual_summary.get("roi_pct", 0.0),
        "sim_final_pnl": sim_summary.get("final_pnl"),
        "sim_roi_pct": sim_summary.get("roi_pct"),
        "sim_copied": (wallet_meta or {}).get("copied", 0),
        "sim_skipped": (wallet_meta or {}).get("skipped", 0),
        "sim_status": sim_summary.get("sim_status") or (wallet_meta or {}).get("sim_status", "missing_drl"),
    }


@callback(
    [
        Output("cur-warnings", "children"),
        Output("cur-read", "children"),
        Output("cur-sim-summary-panel", "children"),
        Output("cur-chart", "figure"),
        Output("cur-stats", "children"),
        Output("cur-concentration", "children"),
        Output("cur-top-markets", "children"),
    ],
    Input("cur-view", "data"),
    Input("cur-sim-overlay-visible", "data"),
    State("cur-sim-session-id", "data"),
)
def render_curation_wallet(view, sim_overlay_visible, sim_session_id):
    if not view or view.get("status") == "idle":
        return "", "", "", _build_curation_loading_figure("No wallet selected"), "", "", ""
    wallet = view.get("wallet", "")
    filter_level = view.get("filter_level", "detail")
    filter_value = view.get("filter_value", "")
    selected_range = _normalize_curation_range(view.get("range"))
    manager = get_curation_prefetch_manager()
    key = manager.make_base_key(wallet, filter_level, filter_value)
    data = manager.get_payload(key, selected_range)
    base_payload = manager.get_base_payload(key)
    if not data:
        return "", _build_curation_loading_read(), "", _build_curation_loading_figure(f"Loading {wallet[:10]}..."), "", "", ""
    wallet_meta, sim_payload = _load_sim_payload(sim_session_id, wallet, selected_range, base_payload)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[point["date"] for point in data["series"]],
            y=[point["pnl"] for point in data["series"]],
            mode="lines",
            line=dict(color=COLORS["chart_line"], width=2),
            fill="tozeroy",
            fillcolor=COLORS["chart_fill"],
            customdata=[[point["cumulative_cash"], point["marked_value"], point["daily_trade_count"]] for point in data["series"]],
            hovertemplate="<b>%{x}</b><br>P&L: $%{y:,.2f}<br>Cash: $%{customdata[0]:,.2f}<br>Marked: $%{customdata[1]:,.2f}<br>Trades: %{customdata[2]}<extra></extra>",
            name="Actual P&L",
        )
    )
    if sim_overlay_visible and sim_payload and sim_payload.get("series"):
        fig.add_trace(
            go.Scatter(
                x=[point["date"] for point in sim_payload["series"]],
                y=[point["pnl"] for point in sim_payload["series"]],
                mode="lines",
                line=dict(color="#9B51E0", width=2),
                fill="tozeroy",
                fillcolor="rgba(155, 81, 224, 0.12)",
                name="Sim P&L",
                hovertemplate="<b>%{x}</b><br>Sim P&L: $%{y:,.2f}<extra></extra>",
            )
        )

    sim_panel = ""
    if wallet_meta:
        sim_panel = html.Div(
            [
                dbc.Checkbox(id="cur-sim-overlay-toggle", value=bool(sim_overlay_visible), className="pm-sim-toggle"),
                _build_sim_summary_strip(wallet_meta),
                _build_sim_validation((sim_payload or {}).get("validation")),
                dbc.Alert("Sim data unavailable for this wallet.", color="secondary", className="pm-inline-message") if wallet_meta.get("sim_status") != "ready" or not sim_payload else "",
            ],
            className="pm-sim-panel",
        )

    stats = _build_curation_stats(data["summary"], sim_payload, wallet_meta or {})
    return warnings, curation_read, sim_panel, fig, stats, concentration, top_markets


@callback(
    Output("cur-sim-overlay-visible", "data"),
    Input("cur-sim-overlay-toggle", "value"),
    prevent_initial_call=True,
)
def set_cur_sim_overlay_visible(value):
    return bool(value)


@callback(
    [Output("cur-index", "data", allow_duplicate=True), Output("cur-approved", "data", allow_duplicate=True), Output("cur-decisions", "data", allow_duplicate=True), Output("cur-setup", "style", allow_duplicate=True), Output("cur-swipe", "style", allow_duplicate=True), Output("cur-results", "style", allow_duplicate=True), Output("cur-results-title", "children"), Output("cur-results-list", "children")],
    [Input("cur-approve", "n_clicks"), Input("cur-skip", "n_clicks"), Input("cur-back", "n_clicks")],
    [State("cur-index", "data"), State("cur-wallets", "data"), State("cur-approved", "data"), State("cur-decisions", "data"), State("cur-view", "data"), State("cur-sim-session-id", "data")],
    prevent_initial_call=True,
)
def handle_curation_action(approve_clicks, skip_clicks, back_clicks, index, wallets, approved, decisions, current_view, sim_session_id):
    triggered = dash.ctx.triggered_id
    if not triggered or not wallets:
        return [no_update] * 8
    if triggered == "cur-back":
        if index > 0:
            return [index - 1] + [no_update] * 7
        return [no_update] * 8
    wallet = wallets[index] if index < len(wallets) else None
    if not wallet or not current_view:
        return [no_update] * 8
    if triggered == "cur-approve" and approve_clicks:
        if wallet not in approved:
            approved = approved + [wallet]
        decisions = {**decisions, wallet: _build_curation_decision_row(current_view, "approved", sim_session_id)}
    elif triggered == "cur-skip" and skip_clicks:
        decisions = {**decisions, wallet: _build_curation_decision_row(current_view, "skipped", sim_session_id)}
    else:
        return [no_update] * 8
    next_index = index + 1
    if next_index >= len(wallets):
        title = f"Approved {len(approved)} of {len(wallets)} wallets"
        wallet_list = html.Div(
            [html.Div(wallet, style={"fontFamily": "monospace", "padding": "2px 0"}) for wallet in approved]
        ) if approved else html.Div("No wallets approved.", style={"color": "var(--pm-text-secondary)"})
        return [next_index, approved, decisions, {"display": "none"}, {"display": "none"}, {"display": "block"}, title, wallet_list]
    return [next_index, approved, decisions, no_update, no_update, no_update, no_update, no_update]


@callback(
    Output("cur-download", "data"),
    Input("cur-download-btn", "n_clicks"),
    State("cur-decisions", "data"),
    prevent_initial_call=True,
)
def download_curation_results(n_clicks, decisions):
    import csv
    import io

    if not n_clicks or not decisions:
        return no_update
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "wallet",
            "filter_level",
            "filter_value",
            "decision",
            "actual_final_pnl",
            "actual_roi_pct",
            "sim_final_pnl",
            "sim_roi_pct",
            "sim_copied",
            "sim_skipped",
            "sim_status",
        ],
    )
    writer.writeheader()
    for row in decisions.values():
        writer.writerow(row)
    return {"content": buffer.getvalue(), "filename": f"curation_results_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"}
```

```css
.pm-sim-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}

.pm-sim-summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--pm-border);
  border-radius: var(--pm-radius-md);
  background: var(--pm-surface-alt);
}

.pm-sim-summary-strip span {
  color: var(--pm-text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.pm-wallet-stat-grid--sim .pm-sim-stat .pm-metric-value {
  color: #9B51E0;
  font-size: 18px;
}

.pm-sim-validation {
  color: var(--pm-text-secondary);
  font-size: 11px;
}

.pm-sim-validation--ok { color: var(--pm-green); }
.pm-sim-validation--warn { color: var(--pm-warning); }
.pm-sim-validation--bad { color: var(--pm-red); }
```

- [ ] **Step 4: Run the targeted app tests and the full suite**

Run: `PYTHONPATH=. pytest tests/test_app_curation.py -q`

Expected: PASS with all curation tests green

Run: `PYTHONPATH=. pytest`

Expected: PASS with the full suite green

- [ ] **Step 5: Commit the overlay and export work**

```bash
git add app.py assets/dashboard-ui.css tests/test_app_curation.py
git commit -m "feat: render sharpsim overlay and export results"
```

- [ ] **Step 6: Manual smoke-test the browser flow before asking for review**

Run: `PYTHONPATH=. python app.py`

Expected manual checks:
- Upload `tests/Sharpsim.xlsx` on `/wallet-curation`
- Confirm setup switches to sim mode and manual inputs hide
- Start review and confirm first wallet loads with the purple overlay visible
- Change range from `ALL` to `7D` and confirm both traces rebase
- Skip one wallet and approve one wallet
- Finish the batch and confirm `Download Results` returns CSV
