import logging
from typing import Iterable

import psycopg2
from psycopg2.extras import DictCursor

logger = logging.getLogger(__name__)


def _translate_sql(sql: str) -> str:
    """Translate sqlite-style placeholders to psycopg2 placeholders."""
    if "?" in sql and "%s" not in sql:
        return sql.replace("?", "%s")
    return sql


class CloudConnection:
    """Small wrapper that makes psycopg2 feel closer to sqlite3."""

    def __init__(self, raw):
        self.raw = raw
        self.backend = "postgres"

    def cursor(self):
        return self.raw.cursor(cursor_factory=DictCursor)

    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(_translate_sql(sql), tuple(params or ()))
        return cursor

    def executemany(self, sql, param_sets: Iterable[Iterable]):
        cursor = self.cursor()
        cursor.executemany(_translate_sql(sql), param_sets)
        return cursor

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def connect(database_url: str) -> CloudConnection:
    raw = psycopg2.connect(database_url)
    raw.autocommit = False
    return CloudConnection(raw)


POSTGRES_SCHEMA = [
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
        ingest_batch INTEGER,
        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(master_wallet)",
    "CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_trades_game ON trades(game)",
    "CREATE INDEX IF NOT EXISTS idx_trades_tx_hash ON trades(tx_hash)",
    """
    CREATE TABLE IF NOT EXISTS positions (
        id SERIAL PRIMARY KEY,
        master_wallet TEXT NOT NULL,
        token_id TEXT NOT NULL,
        market TEXT NOT NULL,
        outcome TEXT NOT NULL,
        game TEXT,
        total_shares_bought DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_invested DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_shares_sold DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_received DOUBLE PRECISION NOT NULL DEFAULT 0,
        net_shares DOUBLE PRECISION NOT NULL DEFAULT 0,
        avg_cost_basis DOUBLE PRECISION,
        last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(master_wallet, token_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_positions_wallet ON positions(master_wallet)",
    "CREATE INDEX IF NOT EXISTS idx_positions_token ON positions(token_id)",
    """
    CREATE TABLE IF NOT EXISTS resolutions (
        token_id TEXT PRIMARY KEY,
        market TEXT,
        outcome TEXT,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolution_price DOUBLE PRECISION,
        resolved_at TIMESTAMPTZ,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_price DOUBLE PRECISION,
        price_checked_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_pnl (
        master_wallet TEXT PRIMARY KEY,
        game TEXT,
        total_invested DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_received_sells DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_received_resolutions DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_lost_resolutions DOUBLE PRECISION NOT NULL DEFAULT 0,
        realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_shares DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_invested DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_value DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        unique_markets INTEGER NOT NULL DEFAULT 0,
        unique_tokens INTEGER NOT NULL DEFAULT 0,
        total_trades INTEGER NOT NULL DEFAULT 0,
        excluded_positions INTEGER NOT NULL DEFAULT 0,
        first_trade TIMESTAMPTZ,
        last_trade TIMESTAMPTZ,
        last_computed TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pnl_history (
        id SERIAL PRIMARY KEY,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        master_wallet TEXT,
        realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_invested DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_trades INTEGER NOT NULL DEFAULT 0,
        unique_markets INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pnl_history_time ON pnl_history(recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_pnl_history_wallet ON pnl_history(master_wallet)",
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
    """,
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
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS tier_config (
        tier_name TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        copy_percentage DOUBLE PRECISION NOT NULL,
        sort_order INTEGER NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_tiers (
        wallet_address TEXT PRIMARY KEY,
        tier_name TEXT NOT NULL REFERENCES tier_config(tier_name),
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        assigned_from TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS promotion_history (
        id SERIAL PRIMARY KEY,
        wallet_address TEXT NOT NULL,
        action TEXT NOT NULL,
        from_tier TEXT,
        to_tier TEXT,
        action_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        realized_pnl_at_action DOUBLE PRECISION,
        unrealized_pnl_at_action DOUBLE PRECISION,
        total_pnl_at_action DOUBLE PRECISION,
        total_invested_at_action DOUBLE PRECISION,
        unique_markets_at_action INTEGER,
        total_trades_at_action INTEGER,
        days_active_at_action INTEGER,
        roi_pct_at_action DOUBLE PRECISION,
        old_copy_pct DOUBLE PRECISION,
        new_copy_pct DOUBLE PRECISION,
        push_id INTEGER,
        pending_change_id INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_promo_wallet ON promotion_history(wallet_address)",
    "CREATE INDEX IF NOT EXISTS idx_promo_action_at ON promotion_history(action_at)",
    """
    CREATE TABLE IF NOT EXISTS pending_changes (
        id SERIAL PRIMARY KEY,
        wallet_address TEXT NOT NULL,
        change_type TEXT NOT NULL,
        details JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        push_id INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_changes(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_pending_push ON pending_changes(push_id)",
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_push_status ON csv_push_history(status)",
    """
    CREATE TABLE IF NOT EXISTS last_known_wallets (
        wallet_address TEXT PRIMARY KEY,
        game_filter TEXT,
        snapshot_date TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_changes (
        id SERIAL PRIMARY KEY,
        change_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        wallet_address TEXT NOT NULL,
        action TEXT NOT NULL,
        game_filter TEXT,
        trigger TEXT DEFAULT 'sync',
        retirement_summary TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_changes_wallet ON wallet_changes(wallet_address)",
    "CREATE INDEX IF NOT EXISTS idx_changes_date ON wallet_changes(change_date)",
    """
    CREATE TABLE IF NOT EXISTS hidden_wallets (
        wallet_address TEXT PRIMARY KEY,
        hidden_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_log (
        id SERIAL PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        positions_rebuilt INTEGER,
        tokens_resolved INTEGER,
        pnl_computed INTEGER,
        history_recorded INTEGER,
        error TEXT,
        trigger TEXT DEFAULT 'scheduled'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sim_registry (
        sim_number SERIAL PRIMARY KEY,
        original_filename TEXT NOT NULL,
        renamed_filename TEXT NOT NULL,
        sim_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        wallet_count INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sim_snapshots (
        id SERIAL PRIMARY KEY,
        sim_number INTEGER NOT NULL REFERENCES sim_registry(sim_number),
        wallet_address TEXT NOT NULL,
        category TEXT,
        subcategory TEXT,
        detail TEXT,
        trades INTEGER,
        sim_trades INTEGER,
        volume DOUBLE PRECISION,
        sim_pnl DOUBLE PRECISION,
        sim_roi_pct DOUBLE PRECISION,
        max_drawdown_pct DOUBLE PRECISION,
        copied INTEGER,
        skipped INTEGER,
        peak_outflow_30d DOUBLE PRECISION,
        lb_all_time DOUBLE PRECISION,
        lb_name TEXT,
        gamma_cash_pnl DOUBLE PRECISION,
        UNIQUE(sim_number, wallet_address)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sim_wallet ON sim_snapshots(wallet_address)",
    "CREATE INDEX IF NOT EXISTS idx_sim_number ON sim_snapshots(sim_number)",
    """
    CREATE TABLE IF NOT EXISTS sim_profiles (
        id SERIAL PRIMARY KEY,
        sim_number INTEGER NOT NULL REFERENCES sim_registry(sim_number),
        wallet_address TEXT NOT NULL,
        detail TEXT,
        profile_complete INTEGER DEFAULT 1,
        median_entry_price DOUBLE PRECISION,
        mean_entry_price DOUBLE PRECISION,
        pct_entries_above_95 DOUBLE PRECISION,
        pnl_concentration_top1 DOUBLE PRECISION,
        pnl_concentration_top3 DOUBLE PRECISION,
        unique_markets INTEGER,
        total_trades INTEGER,
        market_diversity_ratio DOUBLE PRECISION,
        both_sides_market_pct DOUBLE PRECISION,
        one_hit_wonder_score DOUBLE PRECISION,
        has_arb_pattern INTEGER DEFAULT 0,
        has_scalp_pattern INTEGER DEFAULT 0,
        UNIQUE(sim_number, wallet_address)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_profile_wallet ON sim_profiles(wallet_address)",
    """
    CREATE TABLE IF NOT EXISTS ingest_registry (
        batch_number SERIAL PRIMARY KEY,
        original_filename TEXT NOT NULL,
        archive_filename TEXT NOT NULL,
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        new_trades INTEGER NOT NULL DEFAULT 0,
        duplicate_trades INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pnl_snapshots (
        snapshot_id SERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        trade_count INTEGER NOT NULL,
        new_trades_since_last INTEGER NOT NULL,
        trades_date_from TIMESTAMPTZ,
        trades_date_to TIMESTAMPTZ,
        description TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pnl_snapshot_data (
        id SERIAL PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES pnl_snapshots(snapshot_id),
        master_wallet TEXT NOT NULL,
        game TEXT,
        filter_game TEXT,
        total_invested DOUBLE PRECISION,
        realized_pnl DOUBLE PRECISION,
        unrealized_value DOUBLE PRECISION,
        unrealized_pnl DOUBLE PRECISION,
        total_pnl DOUBLE PRECISION,
        unique_markets INTEGER,
        total_trades INTEGER,
        in_csv INTEGER,
        excluded_positions INTEGER,
        sim_number INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshot_data ON pnl_snapshot_data(snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS csv_history (
        id SERIAL PRIMARY KEY,
        saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        wallet_count INTEGER NOT NULL,
        csv_content TEXT NOT NULL,
        changes_summary TEXT
    )
    """,
]


POSTGRES_MIGRATIONS = [
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS unrealized_value DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS excluded_positions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS unrealized_shares DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE wallet_pnl ADD COLUMN IF NOT EXISTS unrealized_invested DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE resolutions ADD COLUMN IF NOT EXISTS last_price DOUBLE PRECISION",
    "ALTER TABLE resolutions ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS ingest_batch INTEGER",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS raw_csv_line TEXT",
    "ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS row_order INTEGER",
    "ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS copy_percentage DOUBLE PRECISION",
    "ALTER TABLE synced_active_wallets ADD COLUMN IF NOT EXISTS copy_percentage_enabled INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE promotion_history ADD COLUMN IF NOT EXISTS pending_change_id INTEGER",
    "ALTER TABLE csv_push_history ADD COLUMN IF NOT EXISTS old_wallet_tiers JSONB",
    "ALTER TABLE csv_push_history ADD COLUMN IF NOT EXISTS new_wallet_tiers JSONB",
    "ALTER TABLE csv_push_history ADD COLUMN IF NOT EXISTS old_tier_config JSONB",
    "ALTER TABLE csv_push_history ADD COLUMN IF NOT EXISTS new_tier_config JSONB",
    "ALTER TABLE csv_push_history ADD COLUMN IF NOT EXISTS reverts_push_id INTEGER",
]


def init_postgres_schema(conn: CloudConnection):
    cursor = conn.cursor()
    for statement in POSTGRES_SCHEMA:
        cursor.execute(statement)
    for statement in POSTGRES_MIGRATIONS:
        cursor.execute(statement)
    cursor.executemany(
        """
        INSERT INTO tier_config (tier_name, display_name, copy_percentage, sort_order)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tier_name) DO NOTHING
        """,
        [
            ("test", "Test", 4.0, 1),
            ("promoted", "Promoted", 10.0, 2),
            ("high_conviction", "High Conviction", 20.0, 3),
        ],
    )
    cursor.execute(
        """
        INSERT INTO synced_csv_state (id, header_row, global_row, csv_content)
        VALUES (1, '', '', '')
        ON CONFLICT (id) DO NOTHING
        """
    )
    conn.commit()
    logger.info("Postgres schema initialized")
