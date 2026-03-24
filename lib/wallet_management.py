import json
from datetime import date, datetime, timedelta

from lib.csv_builder import load_current_csv_state, row_to_line, set_row_copy_percentage, validate_wallet_csv_line
from lib.normalizers import normalize_game, normalize_wallet
from lib.time_utils import now_utc, parse_db_timestamp, to_db_timestamp

TIERS = ["test", "promoted", "high_conviction"]
ENTRY_ACTIONS = {"added", "promoted", "demoted"}
CHANGE_TO_HISTORY_ACTION = {
    "promote": "promoted",
    "demote": "demoted",
    "add": "added",
    "remove": "removed",
}


def _json_dump(value):
    return json.dumps(value, sort_keys=True, default=_json_default)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_load(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def get_tier_configs(conn):
    rows = conn.execute(
        "SELECT * FROM tier_config ORDER BY sort_order ASC, tier_name ASC"
    ).fetchall()
    return [
        {
            "tier_name": row["tier_name"],
            "display_name": row["display_name"],
            "copy_percentage": float(row["copy_percentage"] or 0),
            "sort_order": int(row["sort_order"] or 0),
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }
        for row in rows
    ]


def get_tier_map(conn):
    return {row["tier_name"]: row for row in get_tier_configs(conn)}


def _canonical_wallet_rows(conn):
    csv_state = load_current_csv_state(conn)
    rows = []
    for index, row in enumerate(csv_state.get("wallet_rows") or []):
        wallet_address = normalize_wallet(row.get("address", ""))
        if not wallet_address or wallet_address == "__global__":
            continue
        whitelist = row.get("market_whitelist", "") or ""
        rows.append(
            {
                "wallet_address": wallet_address,
                "market_whitelist": whitelist,
                "game_filter": normalize_game(whitelist, source="whitelist"),
                "row_order": index,
                "copy_percentage": parse_float_or_none(row.get("copy_percentage")),
                "copy_percentage_enabled": 1
                if str(row.get("copy_percentage_enabled", "")).strip().lower() == "true"
                else 0,
                "raw_csv_line": row_to_line(csv_state["header"], row) if csv_state.get("header") else "",
            }
        )
    return rows


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


def _get_game_filter_map(conn):
    rows = conn.execute(
        "SELECT wallet_address, game_filter FROM synced_active_wallets ORDER BY wallet_address"
    ).fetchall()
    mapping = {row["wallet_address"]: row["game_filter"] or "UNKNOWN" for row in rows}
    for row in _canonical_wallet_rows(conn):
        mapping.setdefault(row["wallet_address"], row["game_filter"] or "UNKNOWN")
    return mapping


def _wallet_snapshot(conn, wallet_address):
    pnl = conn.execute(
        "SELECT * FROM wallet_pnl WHERE master_wallet = ?",
        (wallet_address,),
    ).fetchone()
    if not pnl:
        return {
            "realized_pnl_at_action": 0.0,
            "unrealized_pnl_at_action": 0.0,
            "total_pnl_at_action": 0.0,
            "total_invested_at_action": 0.0,
            "unique_markets_at_action": 0,
            "total_trades_at_action": 0,
            "days_active_at_action": 0,
            "roi_pct_at_action": 0.0,
            "game": "UNKNOWN",
        }

    first_trade = parse_db_timestamp(pnl["first_trade"]) if pnl["first_trade"] else None
    days_active = max((now_utc() - first_trade).days, 0) if first_trade else 0
    invested = float(pnl["total_invested"] or 0)
    total_pnl = float(pnl["total_pnl"] or 0)
    roi_pct = ((total_pnl / invested) * 100.0) if invested else 0.0
    return {
        "realized_pnl_at_action": float(pnl["realized_pnl"] or 0),
        "unrealized_pnl_at_action": float(pnl["unrealized_pnl"] or 0),
        "total_pnl_at_action": total_pnl,
        "total_invested_at_action": invested,
        "unique_markets_at_action": int(pnl["unique_markets"] or 0),
        "total_trades_at_action": int(pnl["total_trades"] or 0),
        "days_active_at_action": days_active,
        "roi_pct_at_action": roi_pct,
        "game": pnl["game"] or "UNKNOWN",
    }


def _wallet_tier_row_to_dict(row):
    if not row:
        return None
    return {
        "wallet_address": row["wallet_address"],
        "tier_name": row["tier_name"],
        "assigned_at": str(row["assigned_at"]) if row["assigned_at"] is not None else None,
        "assigned_from": row["assigned_from"],
        "notes": row["notes"],
    }


def _current_position_map(conn, wallet_address):
    rows = conn.execute(
        """
        SELECT p.*, r.resolved, r.resolution_price, r.resolved_at, r.last_price
        FROM positions p
        LEFT JOIN resolutions r ON p.token_id = r.token_id
        WHERE p.master_wallet = ?
        """,
        (wallet_address,),
    ).fetchall()
    return {row["token_id"]: row for row in rows}


def compute_since_date_pnl(conn, wallet_address, start_at):
    start_iso = to_db_timestamp(start_at)
    end_iso = to_db_timestamp(now_utc())
    positions = _current_position_map(conn, wallet_address)

    invested_rows = conn.execute(
        """
        SELECT token_id, SUM(invested) AS invested
        FROM trades
        WHERE master_wallet = ? AND action = 'Buy' AND timestamp >= ? AND timestamp <= ?
        GROUP BY token_id
        """,
        (wallet_address, start_iso, end_iso),
    ).fetchall()
    sells_rows = conn.execute(
        """
        SELECT token_id, SUM(shares) AS shares_sold, SUM(received) AS received
        FROM trades
        WHERE master_wallet = ? AND action = 'Sell' AND timestamp >= ? AND timestamp <= ?
        GROUP BY token_id
        """,
        (wallet_address, start_iso, end_iso),
    ).fetchall()
    resolution_rows = conn.execute(
        """
        SELECT token_id, resolved, resolution_price, resolved_at
        FROM resolutions
        WHERE token_id IN (
            SELECT DISTINCT token_id FROM positions WHERE master_wallet = ?
        )
          AND resolved IN (1, -1, 2)
          AND resolved_at >= ?
          AND resolved_at <= ?
        """,
        (wallet_address, start_iso, end_iso),
    ).fetchall()
    stats_row = conn.execute(
        """
        SELECT COUNT(*) AS total_trades, COUNT(DISTINCT market) AS unique_markets
        FROM trades
        WHERE master_wallet = ? AND timestamp >= ? AND timestamp <= ?
        """,
        (wallet_address, start_iso, end_iso),
    ).fetchone()

    invested_map = {row["token_id"]: float(row["invested"] or 0) for row in invested_rows}
    sells_map = {
        row["token_id"]: {
            "shares_sold": float(row["shares_sold"] or 0),
            "received": float(row["received"] or 0),
        }
        for row in sells_rows
    }

    realized = 0.0
    unrealized = 0.0
    resolved_tokens = set()

    for token_id, sell_data in sells_map.items():
        position = positions.get(token_id)
        if not position:
            continue
        avg_cost = float(position["avg_cost_basis"] or 0)
        realized += sell_data["received"] - (avg_cost * sell_data["shares_sold"])

    for resolution in resolution_rows:
        position = positions.get(resolution["token_id"])
        if not position:
            continue
        avg_cost = float(position["avg_cost_basis"] or 0)
        net_shares = float(position["net_shares"] or 0)
        resolution_price = float(resolution["resolution_price"] or 0)
        if resolution["resolved"] == 1:
            realized += net_shares - (avg_cost * net_shares)
        elif resolution["resolved"] == -1:
            realized -= avg_cost * net_shares
        else:
            realized += (net_shares * resolution_price) - (avg_cost * net_shares)
        resolved_tokens.add(resolution["token_id"])

    for token_id in invested_map:
        position = positions.get(token_id)
        if not position or token_id in resolved_tokens:
            continue
        resolved = position["resolved"] if position["resolved"] is not None else 0
        resolved_at = parse_db_timestamp(position["resolved_at"]) if position["resolved_at"] else None
        if resolved in (1, -1, 2) and resolved_at and resolved_at >= parse_db_timestamp(start_iso):
            continue
        if resolved != 0:
            continue
        avg_cost = float(position["avg_cost_basis"] or 0)
        net_shares = float(position["net_shares"] or 0)
        if position["last_price"] is not None:
            unrealized += (float(position["last_price"]) * net_shares) - (avg_cost * net_shares)

    return {
        "invested": round(sum(invested_map.values()), 2),
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "total": round(realized + unrealized, 2),
        "trades": int(stats_row["total_trades"] or 0) if stats_row else 0,
        "markets": int(stats_row["unique_markets"] or 0) if stats_row else 0,
    }


def _tier_transition(current_tier, direction):
    order = {tier: index for index, tier in enumerate(TIERS)}
    idx = order[current_tier]
    if direction == "up":
        if idx >= len(TIERS) - 1:
            raise ValueError("Wallet is already in the highest tier.")
        return TIERS[idx + 1], "promote", "promoted"
    if direction == "down":
        if idx <= 0:
            raise ValueError("Wallet is already in the lowest tier.")
        return TIERS[idx - 1], "demote", "demoted"
    raise ValueError(f"Unsupported direction: {direction}")


def _insert_pending_change(conn, wallet_address, change_type, details, created_at=None):
    created_at = to_db_timestamp(created_at or now_utc())
    cursor = conn.execute(
        """
        INSERT INTO pending_changes (wallet_address, change_type, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (wallet_address, change_type, _json_dump(details), created_at),
    )
    pending_change_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else None
    if not pending_change_id:
        pending_change_id = conn.execute("SELECT MAX(id) FROM pending_changes").fetchone()[0]
    return pending_change_id, created_at


def _insert_promotion_history(
    conn,
    wallet_address,
    action,
    from_tier,
    to_tier,
    snapshot,
    old_copy_pct,
    new_copy_pct,
    action_at,
    pending_change_id=None,
):
    conn.execute(
        """
        INSERT INTO promotion_history (
            wallet_address, action, from_tier, to_tier, action_at,
            realized_pnl_at_action, unrealized_pnl_at_action, total_pnl_at_action,
            total_invested_at_action, unique_markets_at_action, total_trades_at_action,
            days_active_at_action, roi_pct_at_action, old_copy_pct, new_copy_pct,
            pending_change_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            wallet_address,
            action,
            from_tier,
            to_tier,
            action_at,
            snapshot["realized_pnl_at_action"],
            snapshot["unrealized_pnl_at_action"],
            snapshot["total_pnl_at_action"],
            snapshot["total_invested_at_action"],
            snapshot["unique_markets_at_action"],
            snapshot["total_trades_at_action"],
            snapshot["days_active_at_action"],
            snapshot["roi_pct_at_action"],
            old_copy_pct,
            new_copy_pct,
            pending_change_id,
        ),
    )


def bootstrap_existing_wallet_tiers(conn):
    existing = conn.execute("SELECT COUNT(*) FROM wallet_tiers").fetchone()[0]
    if existing:
        return 0

    canonical_rows = _canonical_wallet_rows(conn)
    rows = canonical_rows or [
        {"wallet_address": row["wallet_address"]}
        for row in conn.execute(
            "SELECT wallet_address FROM synced_active_wallets ORDER BY COALESCE(row_order, 999999), wallet_address"
        ).fetchall()
    ]
    if not rows:
        return 0

    tier_map = get_tier_map(conn)
    copy_pct = float(tier_map["test"]["copy_percentage"])
    created = 0
    for row in rows:
        wallet = row["wallet_address"]
        snapshot = _wallet_snapshot(conn, wallet)
        assigned_at = to_db_timestamp(now_utc())
        conn.execute(
            """
            INSERT INTO wallet_tiers (wallet_address, tier_name, assigned_at, assigned_from)
            VALUES (?, 'test', ?, 'new')
            """,
            (wallet, assigned_at),
        )
        _insert_promotion_history(
            conn,
            wallet,
            "added",
            None,
            "test",
            snapshot,
            None,
            copy_pct,
            assigned_at,
        )
        created += 1
    conn.commit()
    return created


def get_pending_changes(conn, only_unpushed=True):
    sql = "SELECT * FROM pending_changes"
    params = []
    if only_unpushed:
        sql += " WHERE push_id IS NULL"
    sql += " ORDER BY created_at ASC, id ASC"
    rows = conn.execute(sql, tuple(params)).fetchall()
    pending = []
    for row in rows:
        details = _json_load(row["details"]) or {}
        pending.append(
            {
                "id": int(row["id"]),
                "wallet_address": row["wallet_address"],
                "change_type": row["change_type"],
                "details": details,
                "created_at": str(row["created_at"]) if row["created_at"] else None,
                "push_id": row["push_id"],
            }
        )
    return pending


def reconstruct_pre_pending_state(current_wallet_tiers, current_tier_config, pending):
    wallet_map = {row["wallet_address"]: dict(row) for row in current_wallet_tiers}
    tier_map = {row["tier_name"]: dict(row) for row in current_tier_config}

    for change in reversed(pending):
        details = change["details"] or {}
        wallet = change["wallet_address"]
        change_type = change["change_type"]

        if change_type == "add":
            wallet_map.pop(wallet, None)
            continue

        if change_type == "remove":
            previous = details.get("previous_wallet_tier")
            if previous:
                wallet_map[wallet] = dict(previous)
            continue

        if change_type in {"promote", "demote"}:
            previous = details.get("previous_wallet_tier")
            if previous:
                wallet_map[wallet] = dict(previous)
            continue

        if change_type == "update_tier_config":
            tier_name = details["tier_name"]
            if tier_name in tier_map:
                tier_map[tier_name]["copy_percentage"] = float(details["old_copy_pct"])

    return (
        sorted(wallet_map.values(), key=lambda row: row["wallet_address"]),
        sorted(tier_map.values(), key=lambda row: (row["sort_order"], row["tier_name"])),
    )


def get_wallet_management_snapshot(conn, bootstrap=True):
    bootstrap_count = bootstrap_existing_wallet_tiers(conn) if bootstrap else 0
    tier_map = get_tier_map(conn)
    game_filters = _get_game_filter_map(conn)
    pending = get_pending_changes(conn, only_unpushed=True)
    pending_push = conn.execute(
        "SELECT * FROM csv_push_history WHERE status = 'pending' ORDER BY pushed_at ASC LIMIT 1"
    ).fetchone()

    wallets = conn.execute(
        """
        SELECT wt.*, wp.game, wp.total_pnl, wp.realized_pnl, wp.unrealized_pnl,
               wp.total_invested, wp.unique_markets, wp.total_trades, wp.first_trade, wp.last_trade
        FROM wallet_tiers wt
        LEFT JOIN wallet_pnl wp ON wp.master_wallet = wt.wallet_address
        ORDER BY wt.tier_name, wt.assigned_at DESC, wt.wallet_address ASC
        """
    ).fetchall()

    snapshot_lookup = {}
    history_rows = conn.execute(
        """
        SELECT wallet_address, to_tier, action_at, total_pnl_at_action, realized_pnl_at_action,
               unrealized_pnl_at_action
        FROM promotion_history
        WHERE action IN ('added', 'promoted', 'demoted')
        ORDER BY action_at DESC, id DESC
        """
    ).fetchall()
    for row in history_rows:
        key = (row["wallet_address"], row["to_tier"], str(row["action_at"]))
        snapshot_lookup.setdefault(key, row)

    tier_sections = []
    for tier in get_tier_configs(conn):
        section_wallets = []
        for row in wallets:
            if row["tier_name"] != tier["tier_name"]:
                continue
            assigned_at = parse_db_timestamp(row["assigned_at"]) if row["assigned_at"] else now_utc()
            days_in_tier = max((now_utc() - assigned_at).days, 0)
            first_trade = parse_db_timestamp(row["first_trade"]) if row["first_trade"] else None
            days_active = max((now_utc() - first_trade).days, 0) if first_trade else 0
            since_promo = {"total": None, "realized": None, "unrealized": None}
            if tier["tier_name"] != "test":
                since_promo = compute_since_date_pnl(conn, row["wallet_address"], assigned_at)
            promo_snapshot = snapshot_lookup.get((row["wallet_address"], tier["tier_name"], str(row["assigned_at"])))
            section_wallets.append(
                {
                    "wallet_address": row["wallet_address"],
                    "game": row["game"] or game_filters.get(row["wallet_address"], "UNKNOWN"),
                    "game_filter": game_filters.get(row["wallet_address"], "UNKNOWN"),
                    "total_pnl": round(float(row["total_pnl"] or 0), 2),
                    "realized_pnl": round(float(row["realized_pnl"] or 0), 2),
                    "unrealized_pnl": round(float(row["unrealized_pnl"] or 0), 2),
                    "total_invested": round(float(row["total_invested"] or 0), 2),
                    "unique_markets": int(row["unique_markets"] or 0),
                    "total_trades": int(row["total_trades"] or 0),
                    "days_active": days_active,
                    "assigned_at": str(row["assigned_at"]) if row["assigned_at"] else None,
                    "days_in_tier": days_in_tier,
                    "since_promo_pnl": since_promo["total"],
                    "since_promo_realized": since_promo["realized"],
                    "since_promo_unrealized": since_promo["unrealized"],
                    "at_promo_pnl": round(float(promo_snapshot["total_pnl_at_action"] or 0), 2) if promo_snapshot else None,
                    "tier_name": tier["tier_name"],
                }
            )
        tier_sections.append(
            {
                "tier_name": tier["tier_name"],
                "display_name": tier["display_name"],
                "copy_percentage": tier["copy_percentage"],
                "wallets": section_wallets,
            }
        )

    # Removed wallets from promotion history
    # Build a fallback game lookup from wallet_pnl for removed wallets
    pnl_games = {}
    for r in conn.execute("SELECT master_wallet, game FROM wallet_pnl WHERE game IS NOT NULL").fetchall():
        pnl_games[r["master_wallet"]] = r["game"]

    removed_rows = conn.execute(
        """
        SELECT wallet_address, from_tier, action_at,
               total_pnl_at_action, realized_pnl_at_action, unrealized_pnl_at_action,
               total_invested_at_action, total_trades_at_action, unique_markets_at_action,
               days_active_at_action
        FROM promotion_history
        WHERE action = 'removed'
        ORDER BY action_at DESC
        """
    ).fetchall()
    removed_wallets = []
    for row in removed_rows:
        removed_at = parse_db_timestamp(row["action_at"]) if row["action_at"] else now_utc()
        wallet = row["wallet_address"]
        game = game_filters.get(wallet) or pnl_games.get(wallet) or "UNKNOWN"
        removed_wallets.append({
            "wallet_address": wallet,
            "from_tier": row["from_tier"],
            "removed_at": removed_at.strftime("%Y-%m-%d %H:%M UTC"),
            "total_pnl": round(float(row["total_pnl_at_action"] or 0), 2),
            "realized_pnl": round(float(row["realized_pnl_at_action"] or 0), 2),
            "total_trades": int(row["total_trades_at_action"] or 0),
            "unique_markets": int(row["unique_markets_at_action"] or 0),
            "days_active": int(row["days_active_at_action"] or 0),
            "game_filter": game,
        })

    # Bulk fetch sparkline data (last 14 days of pnl_history per wallet)
    sparkline_data = {}
    try:
        spark_rows = conn.execute(
            """
            SELECT master_wallet, total_pnl, recorded_at
            FROM pnl_history
            WHERE master_wallet IS NOT NULL
            ORDER BY master_wallet, recorded_at
            """
        ).fetchall()
        for row in spark_rows:
            wallet = row["master_wallet"]
            if wallet not in sparkline_data:
                sparkline_data[wallet] = []
            sparkline_data[wallet].append(float(row["total_pnl"] or 0))
    except Exception as exc:
        logger.warning("Failed to fetch sparkline data: %s", exc)

    return {
        "tiers": tier_sections,
        "pending_changes": pending,
        "pending_count": len(pending),
        "pending_push": pending_push,
        "bootstrap_count": bootstrap_count,
        "render_token": to_db_timestamp(now_utc()),
        "removed_wallets": removed_wallets,
        "sparklines": sparkline_data,
    }


def _replay_pending_change(conn, change):
    details = change["details"] or {}
    wallet_address = normalize_wallet(change["wallet_address"])
    change_type = change["change_type"]
    created_at = to_db_timestamp(change["created_at"] or now_utc())
    pending_change_id, created_at = _insert_pending_change(
        conn,
        wallet_address,
        change_type,
        details,
        created_at=created_at,
    )

    if change_type == "add":
        conn.execute(
            """
            INSERT INTO wallet_tiers (wallet_address, tier_name, assigned_at, assigned_from)
            VALUES (?, ?, ?, 'new')
            """,
            (wallet_address, details["to_tier"], created_at),
        )
        _insert_promotion_history(
            conn,
            wallet_address,
            "added",
            None,
            details["to_tier"],
            details.get("snapshot") or _wallet_snapshot(conn, wallet_address),
            None,
            details["new_copy_pct"],
            created_at,
            pending_change_id=pending_change_id,
        )
        return

    if change_type == "remove":
        conn.execute("DELETE FROM wallet_tiers WHERE wallet_address = ?", (wallet_address,))
        _insert_promotion_history(
            conn,
            wallet_address,
            "removed",
            details.get("from_tier"),
            None,
            details.get("snapshot") or _wallet_snapshot(conn, wallet_address),
            details.get("old_copy_pct"),
            None,
            created_at,
            pending_change_id=pending_change_id,
        )
        return

    if change_type in {"promote", "demote"}:
        conn.execute(
            """
            UPDATE wallet_tiers
            SET tier_name = ?, assigned_at = ?, assigned_from = ?
            WHERE wallet_address = ?
            """,
            (details["to_tier"], created_at, details["from_tier"], wallet_address),
        )
        _insert_promotion_history(
            conn,
            wallet_address,
            CHANGE_TO_HISTORY_ACTION[change_type],
            details.get("from_tier"),
            details.get("to_tier"),
            details.get("snapshot") or _wallet_snapshot(conn, wallet_address),
            details.get("old_copy_pct"),
            details.get("new_copy_pct"),
            created_at,
            pending_change_id=pending_change_id,
        )
        return

    if change_type == "update_tier_config":
        conn.execute(
            "UPDATE tier_config SET copy_percentage = ?, updated_at = ? WHERE tier_name = ?",
            (details["new_copy_pct"], to_db_timestamp(now_utc()), details["tier_name"]),
        )
        return

    raise ValueError(f"Unsupported pending change type: {change_type}")


def remove_pending_change(conn, pending_change_id):
    pending = get_pending_changes(conn, only_unpushed=True)
    target = next((change for change in pending if int(change["id"]) == int(pending_change_id)), None)
    if not target:
        raise ValueError("Queued change not found.")

    current_wallet_tiers = serialize_wallet_tiers_snapshot(conn)
    current_tier_config = serialize_tier_config_snapshot(conn)
    baseline_wallet_tiers, baseline_tier_config = reconstruct_pre_pending_state(
        current_wallet_tiers,
        current_tier_config,
        pending,
    )

    pending_ids = [int(change["id"]) for change in pending]
    if pending_ids:
        placeholders = ",".join(["?"] * len(pending_ids))
        conn.execute(
            f"DELETE FROM promotion_history WHERE pending_change_id IN ({placeholders})",
            tuple(pending_ids),
        )
        conn.execute(
            f"DELETE FROM pending_changes WHERE id IN ({placeholders})",
            tuple(pending_ids),
        )

    restore_wallet_tiers_snapshot(conn, baseline_wallet_tiers)
    restore_tier_config_snapshot(conn, baseline_tier_config)

    remaining = []
    for change in pending:
        if int(change["id"]) == int(pending_change_id):
            continue
        _replay_pending_change(conn, change)
        remaining.append(change)

    conn.commit()
    return {"removed_change": target, "remaining_count": len(remaining)}


def promote_or_demote_wallet(conn, wallet_address, direction):
    tier_map = get_tier_map(conn)
    row = conn.execute(
        "SELECT * FROM wallet_tiers WHERE wallet_address = ?",
        (wallet_address,),
    ).fetchone()
    if not row:
        raise ValueError("Wallet is not currently tier-assigned.")

    new_tier, change_type, history_action = _tier_transition(row["tier_name"], direction)
    snapshot = _wallet_snapshot(conn, wallet_address)
    old_copy_pct = float(tier_map[row["tier_name"]]["copy_percentage"])
    new_copy_pct = float(tier_map[new_tier]["copy_percentage"])
    details = {
        "wallet_address": wallet_address,
        "from_tier": row["tier_name"],
        "to_tier": new_tier,
        "old_copy_pct": old_copy_pct,
        "new_copy_pct": new_copy_pct,
        "snapshot": snapshot,
        "game_filter": _get_game_filter_map(conn).get(wallet_address, snapshot["game"]),
        "previous_wallet_tier": _wallet_tier_row_to_dict(row),
    }
    pending_change_id, created_at = _insert_pending_change(conn, wallet_address, change_type, details)
    conn.execute(
        """
        UPDATE wallet_tiers
        SET tier_name = ?, assigned_at = ?, assigned_from = ?
        WHERE wallet_address = ?
        """,
        (new_tier, created_at, row["tier_name"], wallet_address),
    )
    _insert_promotion_history(
        conn,
        wallet_address,
        history_action,
        row["tier_name"],
        new_tier,
        snapshot,
        old_copy_pct,
        new_copy_pct,
        created_at,
        pending_change_id=pending_change_id,
    )
    conn.commit()
    return details


def remove_wallet(conn, wallet_address):
    tier_map = get_tier_map(conn)
    row = conn.execute(
        "SELECT * FROM wallet_tiers WHERE wallet_address = ?",
        (wallet_address,),
    ).fetchone()
    if not row:
        raise ValueError("Wallet is not currently tier-assigned.")
    snapshot = _wallet_snapshot(conn, wallet_address)
    old_copy_pct = float(tier_map[row["tier_name"]]["copy_percentage"])
    details = {
        "wallet_address": wallet_address,
        "from_tier": row["tier_name"],
        "to_tier": None,
        "old_copy_pct": old_copy_pct,
        "new_copy_pct": None,
        "snapshot": snapshot,
        "game_filter": _get_game_filter_map(conn).get(wallet_address, snapshot["game"]),
        "previous_wallet_tier": _wallet_tier_row_to_dict(row),
    }
    pending_change_id, created_at = _insert_pending_change(conn, wallet_address, "remove", details)
    conn.execute("DELETE FROM wallet_tiers WHERE wallet_address = ?", (wallet_address,))
    _insert_promotion_history(
        conn,
        wallet_address,
        "removed",
        row["tier_name"],
        None,
        snapshot,
        old_copy_pct,
        None,
        created_at,
        pending_change_id=pending_change_id,
    )
    conn.commit()
    return details


def add_wallet_from_csv_line(conn, raw_line, tier_name):
    tier_map = get_tier_map(conn)
    if tier_name not in tier_map:
        raise ValueError("Unknown tier selected.")
    csv_state = load_current_csv_state(conn)
    row = validate_wallet_csv_line(raw_line, csv_state["header"])
    wallet_address = normalize_wallet(row["address"])

    existing = conn.execute(
        "SELECT 1 FROM wallet_tiers WHERE wallet_address = ?",
        (wallet_address,),
    ).fetchone()
    if existing:
        raise ValueError("Wallet already exists in the dashboard state.")

    synced_existing = conn.execute(
        "SELECT 1 FROM synced_active_wallets WHERE wallet_address = ?",
        (wallet_address,),
    ).fetchone()
    if synced_existing:
        raise ValueError("Wallet already exists in the synced CSV.")

    new_copy_pct = float(tier_map[tier_name]["copy_percentage"])
    updated_row = set_row_copy_percentage(row, new_copy_pct)
    raw_csv_line = row_to_line(csv_state["header"], updated_row)
    snapshot = _wallet_snapshot(conn, wallet_address)
    game_filter = normalize_game(updated_row.get("market_whitelist", ""), source="whitelist")
    details = {
        "wallet_address": wallet_address,
        "from_tier": None,
        "to_tier": tier_name,
        "old_copy_pct": None,
        "new_copy_pct": new_copy_pct,
        "raw_csv_line": raw_csv_line,
        "game_filter": game_filter,
        "snapshot": snapshot,
    }
    pending_change_id, created_at = _insert_pending_change(conn, wallet_address, "add", details)
    conn.execute(
        """
        INSERT INTO wallet_tiers (wallet_address, tier_name, assigned_at, assigned_from)
        VALUES (?, ?, ?, 'new')
        """,
        (wallet_address, tier_name, created_at),
    )
    _insert_promotion_history(
        conn,
        wallet_address,
        "added",
        None,
        tier_name,
        snapshot,
        None,
        new_copy_pct,
        created_at,
        pending_change_id=pending_change_id,
    )
    conn.commit()
    return details


def save_tier_config_changes(conn, new_values):
    tier_configs = get_tier_configs(conn)
    tier_map = {row["tier_name"]: row for row in tier_configs}
    total_affected = 0
    changed_tiers = []

    for tier_name, raw_value in new_values.items():
        if tier_name not in tier_map:
            continue
        new_copy_pct = float(raw_value)
        old_copy_pct = float(tier_map[tier_name]["copy_percentage"])
        if abs(new_copy_pct - old_copy_pct) < 1e-9:
            continue

        conn.execute(
            "UPDATE tier_config SET copy_percentage = ?, updated_at = ? WHERE tier_name = ?",
            (new_copy_pct, to_db_timestamp(now_utc()), tier_name),
        )
        wallets = conn.execute(
            "SELECT wallet_address FROM wallet_tiers WHERE tier_name = ? ORDER BY wallet_address",
            (tier_name,),
        ).fetchall()
        changed_tiers.append((tier_name, old_copy_pct, new_copy_pct, len(wallets)))
        for wallet_row in wallets:
            wallet_address = wallet_row["wallet_address"]
            details = {
                "wallet_address": wallet_address,
                "tier_name": tier_name,
                "from_tier": tier_name,
                "to_tier": tier_name,
                "old_copy_pct": old_copy_pct,
                "new_copy_pct": new_copy_pct,
                "snapshot": _wallet_snapshot(conn, wallet_address),
                "game_filter": _get_game_filter_map(conn).get(wallet_address, "UNKNOWN"),
            }
            _insert_pending_change(conn, wallet_address, "update_tier_config", details)
            total_affected += 1

    conn.commit()
    return {"total_affected": total_affected, "changed_tiers": changed_tiers}


def serialize_wallet_tiers_snapshot(conn):
    rows = conn.execute(
        "SELECT wallet_address, tier_name, assigned_at, assigned_from, notes FROM wallet_tiers ORDER BY wallet_address"
    ).fetchall()
    return [
        {
            "wallet_address": row["wallet_address"],
            "tier_name": row["tier_name"],
            "assigned_at": str(row["assigned_at"]) if row["assigned_at"] is not None else None,
            "assigned_from": row["assigned_from"],
            "notes": row["notes"],
        }
        for row in rows
    ]


def serialize_tier_config_snapshot(conn):
    return get_tier_configs(conn)


def restore_wallet_tiers_snapshot(conn, snapshot):
    if conn.backend == "postgres":
        conn.execute("TRUNCATE TABLE wallet_tiers")
    else:
        conn.execute("DELETE FROM wallet_tiers")
    for row in snapshot or []:
        conn.execute(
            """
            INSERT INTO wallet_tiers (wallet_address, tier_name, assigned_at, assigned_from, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["wallet_address"],
                row["tier_name"],
                row["assigned_at"],
                row.get("assigned_from"),
                row.get("notes"),
            ),
        )


def restore_tier_config_snapshot(conn, snapshot):
    for row in snapshot or []:
        conn.execute(
            """
            UPDATE tier_config
            SET display_name = ?, copy_percentage = ?, sort_order = ?, updated_at = ?
            WHERE tier_name = ?
            """,
            (
                row["display_name"],
                row["copy_percentage"],
                row["sort_order"],
                to_db_timestamp(now_utc()),
                row["tier_name"],
            ),
        )
