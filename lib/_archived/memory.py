import logging

logger = logging.getLogger(__name__)

_client = None
_available = None


def is_available():
    """Check if Mem0 is available."""
    global _client, _available
    if _available is not None:
        return _available

    try:
        from mem0 import Memory
        _client = Memory()
        _available = True
        return True
    except Exception as e:
        logger.info("Mem0 not available: %s", e)
        _available = False
        return False


def _get_client():
    if not is_available():
        return None
    return _client


def search(query, user_id="wallet_curator", metadata_filter=None):
    """Search Mem0 for relevant memories."""
    client = _get_client()
    if not client:
        return []
    try:
        results = client.search(query, user_id=user_id)
        if metadata_filter and results:
            results = [r for r in results
                       if all(r.get('metadata', {}).get(k) == v
                              for k, v in metadata_filter.items())]
        return results
    except Exception as e:
        logger.warning("Mem0 search failed: %s", e)
        return []


def add(text, user_id="wallet_curator", metadata=None):
    """Add a memory to Mem0."""
    client = _get_client()
    if not client:
        logger.info("Mem0 not available — memory not stored: %s", text[:80])
        return None
    try:
        return client.add(text, user_id=user_id, metadata=metadata or {})
    except Exception as e:
        logger.warning("Mem0 add failed: %s", e)
        return None


def get_all(user_id="wallet_curator"):
    """Get all memories."""
    client = _get_client()
    if not client:
        return []
    try:
        return client.get_all(user_id=user_id)
    except Exception as e:
        logger.warning("Mem0 get_all failed: %s", e)
        return []


def store_retirement(conn, wallet, summary):
    """Store a retirement summary in Mem0, or log for later catch-up."""
    if is_available():
        add(summary, metadata={"type": "retired_wallet", "wallet": wallet})
        logger.info("Stored retirement memory for %s", wallet[:12])
    else:
        logger.info("Mem0 not available — retirement memory pending for %s", wallet[:12])


def catch_up_retirements(conn):
    """Store any pending retirement summaries in Mem0."""
    if not is_available():
        return 0

    pending = conn.execute("""
        SELECT wallet_address, retirement_summary FROM wallet_changes
        WHERE action = 'REMOVED' AND retirement_summary IS NOT NULL
    """).fetchall()

    stored = 0
    for row in pending:
        # Check if already in Mem0
        existing = search(
            f"retired {row['wallet_address'][:12]}",
            metadata_filter={"type": "retired_wallet", "wallet": row['wallet_address']}
        )
        if not existing:
            add(row['retirement_summary'],
                metadata={"type": "retired_wallet", "wallet": row['wallet_address']})
            stored += 1

    if stored > 0:
        logger.info("Caught up %d retirement memories in Mem0", stored)
    return stored
