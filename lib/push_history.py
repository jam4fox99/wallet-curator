import json
from datetime import date, datetime

from lib.csv_builder import apply_pending_changes, load_current_csv_state, summarize_changes
from lib.time_utils import now_utc, to_db_timestamp
from lib.wallet_management import (
    get_pending_changes,
    reconstruct_pre_pending_state,
    restore_tier_config_snapshot,
    restore_wallet_tiers_snapshot,
    serialize_tier_config_snapshot,
    serialize_wallet_tiers_snapshot,
)


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


def _pending_push_exists(conn):
    row = conn.execute(
        "SELECT id FROM csv_push_history WHERE status = 'pending' ORDER BY pushed_at ASC LIMIT 1"
    ).fetchone()
    return bool(row)


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
    old_wallet_tiers, old_tier_config = reconstruct_pre_pending_state(new_wallet_tiers, new_tier_config, pending)
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
    if not push_id:
        push_id = conn.execute("SELECT MAX(id) FROM csv_push_history").fetchone()[0]

    for change in pending:
        conn.execute("UPDATE pending_changes SET push_id = ? WHERE id = ?", (push_id, change["id"]))
        history_action = {
            "promote": "promoted",
            "demote": "demoted",
            "add": "added",
            "remove": "removed",
            "update_tier_config": None,
        }.get(change["change_type"])
        if history_action is None:
            continue
        conn.execute(
            """
            UPDATE promotion_history
            SET push_id = ?, pending_change_id = ?
            WHERE (
                pending_change_id = ?
                OR (
                    COALESCE(pending_change_id, 0) = 0
                    AND wallet_address = ?
                    AND action = ?
                    AND action_at = ?
                )
            )
            """,
            (
                push_id,
                change["id"],
                change["id"],
                change["wallet_address"],
                history_action,
                change["created_at"],
            ),
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
                "pushed_at": str(row["pushed_at"]) if row["pushed_at"] else None,
                "applied_at": str(row["applied_at"]) if row["applied_at"] else None,
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
        "pushed_at": str(row["pushed_at"]) if row["pushed_at"] else None,
        "applied_at": str(row["applied_at"]) if row["applied_at"] else None,
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
    if not revert_push_id:
        revert_push_id = conn.execute("SELECT MAX(id) FROM csv_push_history").fetchone()[0]
    conn.commit()
    return {"push_id": revert_push_id, "summary": summary}
