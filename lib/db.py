import logging
import os
import sqlite3
from contextlib import closing

from lib.cloud_db import CloudConnection, connect as connect_postgres, init_postgres_schema

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "curator.db")


class SQLiteConnection:
    """Small wrapper so sqlite and postgres expose the same surface area."""

    def __init__(self, raw):
        self.raw = raw
        self.backend = "sqlite"

    def cursor(self):
        return self.raw.cursor()

    def execute(self, sql, params=None):
        return self.raw.execute(sql, tuple(params or ()))

    def executemany(self, sql, param_sets):
        return self.raw.executemany(sql, param_sets)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def using_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def get_backend_name() -> str:
    return "postgres" if using_postgres() else "sqlite"


def _get_sqlite_connection() -> SQLiteConnection:
    raw = sqlite3.connect(DB_PATH)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    raw.row_factory = sqlite3.Row
    return SQLiteConnection(raw)


def get_connection():
    if using_postgres():
        return connect_postgres(os.environ["DATABASE_URL"])
    return _get_sqlite_connection()


def init_db():
    """Create all tables and directories. Idempotent."""
    data_dir = os.path.dirname(DB_PATH)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, "sharp_logs"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "sims"), exist_ok=True)
    os.makedirs(os.path.join(data_dir, "malformed"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "reports"), exist_ok=True)

    if using_postgres():
        conn = get_connection()
        try:
            init_postgres_schema(conn)
        finally:
            conn.close()
        return

    conn = _get_sqlite_connection()
    with closing(conn.raw.cursor()) as cursor:
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_hash TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                master_wallet TEXT NOT NULL,
                own_wallet TEXT NOT NULL,
                action TEXT NOT NULL,
                market TEXT NOT NULL,
                outcome TEXT NOT NULL,
                token_id TEXT NOT NULL,
                price REAL NOT NULL,
                shares REAL NOT NULL,
                invested REAL NOT NULL,
                received REAL NOT NULL,
                pnl_pct REAL,
                pct_sold REAL,
                reason TEXT,
                game TEXT,
                ingest_batch INTEGER,
                synced_at TEXT NOT NULL DEFAULT (datetime('now')),
                ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(master_wallet);
            CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_game ON trades(game);
            CREATE INDEX IF NOT EXISTS idx_trades_tx_hash ON trades(tx_hash);

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_wallet TEXT NOT NULL,
                token_id TEXT NOT NULL,
                market TEXT NOT NULL,
                outcome TEXT NOT NULL,
                game TEXT,
                total_shares_bought REAL NOT NULL DEFAULT 0,
                total_invested REAL NOT NULL DEFAULT 0,
                total_shares_sold REAL NOT NULL DEFAULT 0,
                total_received REAL NOT NULL DEFAULT 0,
                net_shares REAL NOT NULL DEFAULT 0,
                avg_cost_basis REAL,
                last_updated TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(master_wallet, token_id)
            );

            CREATE INDEX IF NOT EXISTS idx_positions_wallet ON positions(master_wallet);
            CREATE INDEX IF NOT EXISTS idx_positions_token ON positions(token_id);

            CREATE TABLE IF NOT EXISTS resolutions (
                token_id TEXT PRIMARY KEY,
                market TEXT,
                outcome TEXT,
                resolved INTEGER NOT NULL DEFAULT 0,
                resolution_price REAL,
                resolved_at TEXT,
                checked_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_price REAL,
                price_checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS wallet_pnl (
                master_wallet TEXT PRIMARY KEY,
                game TEXT,
                total_invested REAL NOT NULL DEFAULT 0,
                total_received_sells REAL NOT NULL DEFAULT 0,
                total_received_resolutions REAL NOT NULL DEFAULT 0,
                total_lost_resolutions REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_shares REAL NOT NULL DEFAULT 0,
                unrealized_invested REAL NOT NULL DEFAULT 0,
                unrealized_value REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                total_pnl REAL NOT NULL DEFAULT 0,
                unique_markets INTEGER NOT NULL DEFAULT 0,
                unique_tokens INTEGER NOT NULL DEFAULT 0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                excluded_positions INTEGER NOT NULL DEFAULT 0,
                first_trade TEXT,
                last_trade TEXT,
                last_computed TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pnl_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                master_wallet TEXT,
                realized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                total_pnl REAL NOT NULL DEFAULT 0,
                total_invested REAL NOT NULL DEFAULT 0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                unique_markets INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_pnl_history_time ON pnl_history(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_pnl_history_wallet ON pnl_history(master_wallet);

            CREATE TABLE IF NOT EXISTS sync_status (
                id INTEGER PRIMARY KEY DEFAULT 1,
                last_sync_at TEXT NOT NULL DEFAULT (datetime('now')),
                current_version_folder TEXT NOT NULL DEFAULT '',
                trades_synced_this_cycle INTEGER NOT NULL DEFAULT 0,
                total_trades_synced INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                CHECK (id = 1)
            );

            CREATE TABLE IF NOT EXISTS synced_active_wallets (
                wallet_address TEXT PRIMARY KEY,
                market_whitelist TEXT,
                game_filter TEXT,
                synced_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS last_known_wallets (
                wallet_address TEXT PRIMARY KEY,
                game_filter TEXT,
                snapshot_date TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wallet_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_date TEXT NOT NULL DEFAULT (datetime('now')),
                wallet_address TEXT NOT NULL,
                action TEXT NOT NULL,
                game_filter TEXT,
                trigger TEXT DEFAULT 'sync',
                retirement_summary TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_changes_wallet ON wallet_changes(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_changes_date ON wallet_changes(change_date);

            CREATE TABLE IF NOT EXISTS hidden_wallets (
                wallet_address TEXT PRIMARY KEY,
                hidden_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pipeline_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                positions_rebuilt INTEGER,
                tokens_resolved INTEGER,
                pnl_computed INTEGER,
                history_recorded INTEGER,
                error TEXT,
                trigger TEXT DEFAULT 'scheduled'
            );

            CREATE TABLE IF NOT EXISTS sim_registry (
                sim_number INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                renamed_filename TEXT NOT NULL,
                sim_date TEXT NOT NULL DEFAULT (datetime('now')),
                ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
                wallet_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS sim_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sim_number INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                category TEXT,
                subcategory TEXT,
                detail TEXT,
                trades INTEGER,
                sim_trades INTEGER,
                volume REAL,
                sim_pnl REAL,
                sim_roi_pct REAL,
                max_drawdown_pct REAL,
                copied INTEGER,
                skipped INTEGER,
                peak_outflow_30d REAL,
                lb_all_time REAL,
                lb_name TEXT,
                gamma_cash_pnl REAL,
                UNIQUE(sim_number, wallet_address),
                FOREIGN KEY (sim_number) REFERENCES sim_registry(sim_number)
            );
            CREATE INDEX IF NOT EXISTS idx_sim_wallet ON sim_snapshots(wallet_address);
            CREATE INDEX IF NOT EXISTS idx_sim_number ON sim_snapshots(sim_number);

            CREATE TABLE IF NOT EXISTS sim_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sim_number INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                detail TEXT,
                profile_complete INTEGER DEFAULT 1,
                median_entry_price REAL,
                mean_entry_price REAL,
                pct_entries_above_95 REAL,
                pnl_concentration_top1 REAL,
                pnl_concentration_top3 REAL,
                unique_markets INTEGER,
                total_trades INTEGER,
                market_diversity_ratio REAL,
                both_sides_market_pct REAL,
                one_hit_wonder_score REAL,
                has_arb_pattern INTEGER DEFAULT 0,
                has_scalp_pattern INTEGER DEFAULT 0,
                UNIQUE(sim_number, wallet_address),
                FOREIGN KEY (sim_number) REFERENCES sim_registry(sim_number)
            );
            CREATE INDEX IF NOT EXISTS idx_profile_wallet ON sim_profiles(wallet_address);

            CREATE TABLE IF NOT EXISTS ingest_registry (
                batch_number INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                archive_filename TEXT NOT NULL,
                ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
                new_trades INTEGER NOT NULL DEFAULT 0,
                duplicate_trades INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pnl_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                trade_count INTEGER NOT NULL,
                new_trades_since_last INTEGER NOT NULL,
                trades_date_from TEXT,
                trades_date_to TEXT,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS pnl_snapshot_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                master_wallet TEXT NOT NULL,
                game TEXT,
                filter_game TEXT,
                total_invested REAL,
                realized_pnl REAL,
                unrealized_value REAL,
                unrealized_pnl REAL,
                total_pnl REAL,
                unique_markets INTEGER,
                total_trades INTEGER,
                in_csv INTEGER,
                excluded_positions INTEGER,
                sim_number INTEGER,
                FOREIGN KEY (snapshot_id) REFERENCES pnl_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshot_data ON pnl_snapshot_data(snapshot_id);

            CREATE TABLE IF NOT EXISTS csv_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_at TEXT NOT NULL DEFAULT (datetime('now')),
                wallet_count INTEGER NOT NULL,
                csv_content TEXT NOT NULL,
                changes_summary TEXT
            );
            """
        )
    conn.commit()

    migrations = [
        "ALTER TABLE wallet_pnl ADD COLUMN unrealized_value REAL NOT NULL DEFAULT 0",
        "ALTER TABLE wallet_pnl ADD COLUMN unrealized_pnl REAL NOT NULL DEFAULT 0",
        "ALTER TABLE wallet_pnl ADD COLUMN total_pnl REAL NOT NULL DEFAULT 0",
        "ALTER TABLE wallet_pnl ADD COLUMN excluded_positions INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE wallet_pnl ADD COLUMN unrealized_shares REAL NOT NULL DEFAULT 0",
        "ALTER TABLE wallet_pnl ADD COLUMN unrealized_invested REAL NOT NULL DEFAULT 0",
        "ALTER TABLE resolutions ADD COLUMN last_price REAL",
        "ALTER TABLE resolutions ADD COLUMN price_checked_at TEXT",
        "ALTER TABLE trades ADD COLUMN ingest_batch INTEGER",
        "ALTER TABLE trades ADD COLUMN ingested_at TEXT NOT NULL DEFAULT (datetime('now'))",
        "ALTER TABLE trades ADD COLUMN synced_at TEXT NOT NULL DEFAULT (datetime('now'))",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass
    conn.commit()
    conn.close()
    logger.info("SQLite database initialized at %s", DB_PATH)


def rebuild_positions(conn):
    """Full rebuild of positions table from trades. Only stores valid positions."""
    cursor = conn.execute(
        """
        SELECT master_wallet, COUNT(*) as excluded_count
        FROM (
            SELECT master_wallet, token_id
            FROM trades
            GROUP BY master_wallet, token_id
            HAVING SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) <= 0
               OR (SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
                 - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END)) < 0
        ) excluded
        GROUP BY master_wallet
        """
    )
    excluded_counts = {row["master_wallet"]: row["excluded_count"] for row in cursor.fetchall()}

    if excluded_counts:
        total_excluded = sum(excluded_counts.values())
        logger.warning(
            "Excluding %d positions across %d wallets (missing buy data or net_shares < 0)",
            total_excluded,
            len(excluded_counts),
        )

    if conn.backend == "postgres":
        conn.execute("TRUNCATE TABLE positions RESTART IDENTITY")
    else:
        conn.execute("DELETE FROM positions")

    conn.execute(
        """
        INSERT INTO positions (
            master_wallet, token_id, market, outcome, game,
            total_shares_bought, total_invested, total_shares_sold, total_received,
            net_shares, avg_cost_basis
        )
        SELECT
            master_wallet,
            token_id,
            MAX(market),
            MAX(outcome),
            MAX(game),
            SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END),
            SUM(CASE WHEN action='Buy' THEN invested ELSE 0 END),
            SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END),
            SUM(CASE WHEN action='Sell' THEN received ELSE 0 END),
            SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
              - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END),
            CASE WHEN SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) > 0
                 THEN SUM(CASE WHEN action='Buy' THEN invested ELSE 0 END)
                    / SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
                 ELSE NULL END
        FROM trades
        GROUP BY master_wallet, token_id
        HAVING SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END) > 0
           AND (SUM(CASE WHEN action='Buy' THEN shares ELSE 0 END)
              - SUM(CASE WHEN action='Sell' THEN shares ELSE 0 END)) >= 0
        """
    )

    valid_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    conn.commit()
    logger.info("Rebuilt positions: %d valid positions", valid_count)
    return excluded_counts


def ensure_resolution_entries(conn):
    """Ensure every unique token_id in positions has a row in resolutions."""
    if conn.backend == "postgres":
        cursor = conn.execute(
            """
            INSERT INTO resolutions (token_id, market, outcome, checked_at)
            SELECT DISTINCT token_id, market, outcome, TIMESTAMPTZ '2000-01-01 00:00:00+00'
            FROM positions
            ON CONFLICT (token_id) DO NOTHING
            """
        )
        new_entries = cursor.rowcount
    else:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO resolutions (token_id, market, outcome, checked_at)
            SELECT DISTINCT token_id, market, outcome, '2000-01-01 00:00:00'
            FROM positions
            """
        )
        new_entries = cursor.rowcount

    conn.commit()
    if new_entries > 0:
        logger.info("Created %d new resolution entries", new_entries)
    return new_entries
