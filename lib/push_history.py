import json

from lib.csv_builder import apply_pending_changes, load_current_csv_state, summarize_changes
from lib.time_utils import now_utc, to_db_timestamp
from lib.wallet_management import (
    get_pending_changes,
    restore_tier_config_snapshot,
    restore_wallet_tiers_snapshot,
    serialize_tier_config_snapshot,
    serialize_wallet_tiers_snapshot,
)


def _json_dump(value):
    return json.dumps(value, sort_keys=True)


def _json_load(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _pending_push_exists(conn):
    row = conn.execute(
        "SELECT id FROM csv_push_history WHERE status = 'pending' ORDER BY pushed_at ASC LIMIT 1"
    ).fetchone()
    return bool(row)


def _reconstruct_old_state(current_wallet_tiers, current_tier_config, pending):
    wallet_map = {row["wallet_address"]: dict(row) for row in current_wallet_tiers}
    tier_map = {row["tier_name"]: dict(row) for row in current_tier_config}

    for change in reversed(pending):
        details = change["details"]
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


def create_push_from_pending_changes(conn):
    pending = get_pending_changes(conn, only_unpushed=True)
    if not pending:
        raise ValueError("There are no queued changes to push.")

    if _pending_push_exists(conn):
        raise ValueError("A CSV push is already pending on the VPS.")

    current_csv = load_current_csv_state(conn)
    if not current_csv["csv_content"]:
        raise ValueError("No canonical synced active_wallets.csv is available yet.")

    new_wallet_tiers = serialize_wallet_tiers_snapshot(conn)
    new_tier_config = serialize_tier_config_snapshot(conn)
    old_wallet_tiers, old_tier_config = _reconstruct_old_state(new_wallet_tiers, new_tier_config, pending)
    new_csv = apply_pending_changes(current_csv["csv_content"], pending)
    summary = summarize_changes(pending)
    pushed_at = to_db_timestamp(now_utc())

    cursor = conn.execute(
        """
        INSERT INTO csv_push_history (
            pushed_at, change_count, summary, old_csv, new_csv, changes, status,
            old_wallet_tiers, new_wallet_tiers, old_tier_config, new_tier_config
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            pushed_at,
            len(pending),
            summary,
            current_csv["csv_content"],
            new_csv,
            _json_dump(pending),
            _json_dump(old_wallet_tiers),
            _json_dump(new_wallet_tiers),
            _json_dump(old_tier_config),
            _json_dump(new_tier_config),
        ),
    )
    push_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else None
    if push_id is None:
        push_id = conn.execute("SELECT MAX(id) FROM csv_push_history").fetchone()[0]

    for change in pending:
        conn.execute("UPDATE pending_changes SET push_id = ? WHERE id = ?", (push_id, change["id"]))
    conn.execute(
        """
        UPDATE promotion_history
        SET push_id = ?
        WHERE pending_change_id IN (
            SELECT id FROM pending_changes WHERE push_id = ?
        )
        """,
        (push_id, push_id),
    )
    conn.commit()
    return {"push_id": push_id, "summary": summary, "change_count": len(pending)}


def list_push_history(conn):
    rows = conn.execute(
        "SELECT * FROM csv_push_history ORDER BY pushed_at DESC, id DESC"
    ).fetchall()
    pushes = []
    for row in rows:
        changes = _json_load(row["changes"]) or []
        pushes.append(
            {
                "id": int(row["id"]),
                "pushed_at": row["pushed_at"],
                "applied_at": row["applied_at"],
                "change_count": int(row["change_count"] or 0),
                "summary": row["summary"],
                "changes": changes,
                "status": row["status"],
                "reverts_push_id": row["reverts_push_id"],
            }
        )
    return pushes


def get_push_detail(conn, push_id):
    row = conn.execute(
        "SELECT * FROM csv_push_history WHERE id = ?",
        (push_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "pushed_at": row["pushed_at"],
        "applied_at": row["applied_at"],
        "change_count": int(row["change_count"] or 0),
        "summary": row["summary"],
        "old_csv": row["old_csv"],
        "new_csv": row["new_csv"],
        "changes": _json_load(row["changes"]) or [],
        "status": row["status"],
        "old_wallet_tiers": _json_load(row["old_wallet_tiers"]) or [],
        "new_wallet_tiers": _json_load(row["new_wallet_tiers"]) or [],
        "old_tier_config": _json_load(row["old_tier_config"]) or [],
        "new_tier_config": _json_load(row["new_tier_config"]) or [],
        "reverts_push_id": row["reverts_push_id"],
    }


def create_revert_push(conn, push_id):
    target = get_push_detail(conn, push_id)
    if not target:
        raise ValueError("Push not found.")
    if target["status"] == "pending":
        raise ValueError("Cannot revert a push that is still pending on the VPS.")
    if _pending_push_exists(conn):
        raise ValueError("A CSV push is already pending. Wait for it to apply before reverting.")
    if get_pending_changes(conn, only_unpushed=True):
        raise ValueError("Clear or push queued local changes before creating a revert.")

    current_csv = load_current_csv_state(conn)
    old_wallet_tiers = serialize_wallet_tiers_snapshot(conn)
    old_tier_config = serialize_tier_config_snapshot(conn)
    new_wallet_tiers = target["old_wallet_tiers"]
    new_tier_config = target["old_tier_config"]
    pushed_at = to_db_timestamp(now_utc())
    summary = f"Revert Push #{push_id}"
    changes = [
        {
            "change_type": "revert",
            "wallet_address": "__system__",
            "details": {
                "target_push_id": push_id,
                "summary": target["summary"],
                "revert_to_pushed_at": target["pushed_at"],
            },
        }
    ]

    restore_wallet_tiers_snapshot(conn, new_wallet_tiers)
    restore_tier_config_snapshot(conn, new_tier_config)
    cursor = conn.execute(
        """
        INSERT INTO csv_push_history (
            pushed_at, change_count, summary, old_csv, new_csv, changes, status,
            old_wallet_tiers, new_wallet_tiers, old_tier_config, new_tier_config, reverts_push_id
        )
        VALUES (?, 1, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
        """,
        (
            pushed_at,
            summary,
            current_csv["csv_content"],
            target["old_csv"],
            _json_dump(changes),
            _json_dump(old_wallet_tiers),
            _json_dump(new_wallet_tiers),
            _json_dump(old_tier_config),
            _json_dump(new_tier_config),
            push_id,
        ),
    )
    revert_push_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else None
    if revert_push_id is None:
        revert_push_id = conn.execute("SELECT MAX(id) FROM csv_push_history").fetchone()[0]
    conn.commit()
    return {"push_id": revert_push_id, "summary": summary}
